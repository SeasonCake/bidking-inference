"""Curated Aisha fatbeans gap audit: ref ranges vs settlement truth.

Filters out low-quality captures (1-round, tail-heavy, insufficient evidence)
before measuring where Hero Ref v0 misses on counts/cells/value/bid.
Local audit only — not shipped in Hero Ref packages.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts" / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "calibration"))
import aisha_calib as _ac  # noqa: E402  (q6 long-tail RED normalization — single source)


def _norm_item_value(item_id: int, quality: int, raw: int, disclosed: frozenset = frozenset()) -> int:
    """q6 long-tail reds -> normalized expected value; everything else (incl. gold) -> raw.
    Fixes the raw-settlement-truth leak in every consumer of _settlement_breakdown (leak audit L3-L9).
    EXCEPTION (author rule, 2026-07-09): a long-tail red FULLY DISCLOSED during the match (catalog id +
    price + footprint revealed in-game — the engine prices it RAW via _is_fully_disclosed_catalog_item)
    must stay RAW in the eval truth too; only never-known long-tail reds are normalized."""
    if int(quality) == 6 and int(item_id) not in disclosed and _ac.is_longtail(int(item_id)):
        return int(_ac.normalized_item_value(int(item_id)))
    return int(raw)


def _match_disclosed_red_ids(events) -> frozenset:
    """Catalog item_ids fully disclosed BEFORE settlement (item_id + value>0 + shape/footprint known):
    public reveals / action results / skill reveals. Mirrors the engine's
    _is_fully_disclosed_catalog_item criterion (200048/200021/100163 class rows carry all three)."""
    out: set[int] = set()

    def _scan(observed) -> None:
        for oi in observed or ():
            iid = getattr(oi, "item_id", None)
            val = getattr(oi, "value", None)
            shape = getattr(oi, "shape_code", None)
            cells = getattr(oi, "cells", None)
            if iid and val and int(val) > 0 and (shape is not None or (cells or 0) > 0):
                out.add(int(iid))

    for state in events.states:
        if state.inventory_items:
            break  # settlement reached — only PRE-settlement disclosure counts as match-known
        for pi in getattr(state, "public_infos", ()) or ():
            _scan(getattr(pi, "observed_items", ()))
        for ar in getattr(state, "action_results", ()) or ():
            _scan(getattr(ar, "observed_items", ()))
        for sr in getattr(state, "skill_reveals", ()) or ():
            _scan(getattr(sr, "observed_items", ()))
    return frozenset(out)

from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    _hero_mode_from_state,
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import (  # noqa: E402
    _action_round,
    _ahmad_ref_inputs_from_batches,
    _latest_round,
    _minimap_grid_items,
    _public_info_rows,
    _skill_reveal_rows,
    load_monitor_tables,
)
from ahmad_ref_engine import (  # noqa: E402
    _avg_value_count_matches,
    normalize_hero_key,
    run_reference_engine,
)
from bidking_lab.runtime.snapshot import (  # noqa: E402
    _ui_public_numeric_contract,  # faithful public_info (总均格/q均格 semantics) — see fidelity fix below
)
from build_clean_eval_set import (  # noqa: E402
    activity_eval_files,
    clean_eval_files,
    sample_selection_metadata,
)

# Compatibility roots for older explicitly historical audit helpers. The current main path below
# uses a capability selector instead of globbing these roots.
SAMPLE_ROOTS = (
    ROOT / "data/samples/fatbeans",
)
DEFAULT_REPORT = ROOT / "data/reports/audit_aisha_gap.txt"
DEFAULT_REPRESENTATIVE_DOC = (
    ROOT / "docs/hero_ref_aisha_representative_samples_2026-06-13.zh-CN.md"
)
ITEMS_PATH = ROOT / "data/processed/items.json"
_ITEM_VALUES: dict[int, int] | None = None

COUNT_KEYS = ("q1", "q3", "q4", "q5", "q6")
CELL_KEYS = ("q1", "q3", "q4", "q5", "q6")


@dataclass
class FilterStats:
    scanned: int = 0
    reasons: Counter = field(default_factory=Counter)


@dataclass(frozen=True)
class GapRow:
    file: str
    map_id: int | None
    cohort: str
    rounds: int
    audit_round: int
    status: str
    total_count_exact: bool
    has_gold_avg_value: bool
    has_gold_avg_cells: bool
    has_gold_count_lock: bool
    gold_price_only: bool
    count_miss: dict[str, bool]
    cells_miss: dict[str, bool | None]
    q6_value_miss: bool
    bid_balanced_miss: bool
    gold_price_unique_derived: bool | None
    gold_price_derived_truth: bool | None
    gaps: dict[str, int | None]
    settlement_total_value: int
    balanced: int | None
    combo_count: int
    notes: tuple[str, ...]


def _events_through_sort(events: FatbeansCaptureEvents, sort_id: int) -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=tuple(row for row in events.packets if int(row.sort_id) <= sort_id),
        frames=tuple(row for row in events.frames if int(row.sort_id) <= sort_id),
        sends=tuple(row for row in events.sends if int(row.sort_id) <= sort_id),
        states=tuple(row for row in events.states if int(row.sort_id) <= sort_id),
        statuses=tuple(row for row in events.statuses if int(row.sort_id) <= sort_id),
    )


def _hero_from_events(events) -> str:
    for state in reversed(events.states):
        hero = _hero_mode_from_state(state)
        if hero:
            return normalize_hero_key(hero)
    return ""


def _item_values() -> dict[int, int]:
    global _ITEM_VALUES
    if _ITEM_VALUES is not None:
        return _ITEM_VALUES
    if not ITEMS_PATH.is_file():
        _ITEM_VALUES = {}
        return _ITEM_VALUES
    import json

    payload = json.loads(ITEMS_PATH.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        _ITEM_VALUES = {
            int(entry["item_id"]): int(entry.get("value") or 0)
            for entry in payload
            if isinstance(entry, dict) and entry.get("item_id") is not None
        }
    elif isinstance(payload, dict):
        _ITEM_VALUES = {
            int(item_id): int(entry.get("value") or 0)
            for item_id, entry in payload.items()
            if isinstance(entry, dict)
        }
    else:
        _ITEM_VALUES = {}
    return _ITEM_VALUES


def _item_qualities() -> dict[int, int]:
    """item_id -> catalog quality (BUG-C recovery: settlement wire can drop quality to 0/None while
    the catalog id survives — 5603 磁悬浮滑板 q6 counted as q0, red 2件3格 instead of 3件6格)."""
    tables = load_monitor_tables()
    return {int(iid): int(getattr(it, "quality", 0) or 0) for iid, it in tables.items.items()}


def _settlement_breakdown(events) -> dict[str, Any]:
    values_by_id = _item_values()
    qualities_by_id = _item_qualities()
    disclosed = _match_disclosed_red_ids(events)
    for state in reversed(events.states):
        if not state.inventory_items:
            continue
        counts: Counter[int] = Counter()
        cells: Counter[int] = Counter()
        values: Counter[int] = Counter()
        item_vals: list[int] = []
        for item in state.inventory_items:
            quality = int(item.quality or 0)
            if quality <= 0:
                # BUG-C: recover the catalog quality so the item is counted under its real tier
                quality = int(qualities_by_id.get(int(item.item_id), 0) or 0)
            iv = _norm_item_value(int(item.item_id), quality, int(values_by_id.get(int(item.item_id), 0)),
                                  disclosed=disclosed)
            item_vals.append(iv)
            if quality <= 0:
                continue
            counts[quality] += 1
            cells[quality] += int(item.cells or 0)
            values[quality] += iv  # q6 long-tail-normalized (leak audit L3-L9)
        return {
            "total_items": len(state.inventory_items),
            "total_cells": sum(int(item.cells or 0) for item in state.inventory_items),
            "total_value": sum(item_vals),
            "counts": dict(counts),
            "cells": dict(cells),
            "values": dict(values),
        }
    return {}


def _rounds_from_filename(path: Path) -> int | None:
    name = path.name.lower()
    marker = "_rounds_"
    if marker not in name:
        return None
    token = name.split(marker, 1)[1].split("_", 1)[0]
    try:
        return int(token)
    except ValueError:
        return None


def _cohort_from_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    if "fatbeans_activity" in text:
        return "activity"
    if "mixed_aisha" in path.name.lower():
        return "mixed"
    return "valid"


def _triplet(payload: dict[str, Any], key: str) -> tuple[int | None, int | None, int | None]:
    raw = payload.get(key)
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return None, None, None
    out: list[int | None] = []
    for part in raw:
        if part in (None, ""):
            out.append(None)
            continue
        try:
            out.append(int(part))
        except (TypeError, ValueError):
            out.append(None)
    return out[0], out[1], out[2]


def _triplet_ranges(
    ranges: dict[str, Any],
    key: str,
) -> tuple[int | None, int | None, int | None]:
    raw = ranges.get(key)
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return None, None, None
    return _triplet({"value": raw}, "value")


def _truth_in_range(truth: int, low: int | None, mid: int | None, high: int | None) -> bool:
    if low is None and mid is None and high is None:
        return False
    lo = low if low is not None else mid
    hi = high if high is not None else mid
    if lo is None or hi is None:
        return False
    return lo <= truth <= hi


def _mid_gap(truth: int, low: int | None, mid: int | None, high: int | None) -> int | None:
    pivot = mid
    if pivot is None and low is not None and high is not None:
        pivot = (low + high) // 2
    if pivot is None:
        pivot = low if low is not None else high
    if pivot is None:
        return None
    return truth - pivot


def _sum_range_low_high(
    q_ranges: dict[str, Any],
) -> tuple[int | None, int | None]:
    lows: list[int] = []
    highs: list[int] = []
    for key in COUNT_KEYS:
        low, _mid, high = _triplet_ranges(q_ranges, key)
        if low is not None:
            lows.append(low)
        if high is not None:
            highs.append(high)
    if not lows or not highs:
        return None, None
    return sum(lows), sum(highs)


def _total_items_range(
    result: dict[str, Any],
    q_ranges: dict[str, Any],
    *,
    total_count_exact: bool,
    evidence: dict[str, Any],
) -> tuple[int | None, int | None, int | None]:
    if total_count_exact:
        exact = int(evidence["total_count"])
        return exact, exact, exact
    low, high = _sum_range_low_high(q_ranges)
    if low is None or high is None:
        return None, None, None
    mid = (low + high) // 2
    return low, mid, high


def _action_result_rows_from_events(events) -> list[dict[str, Any]]:
    """Mirror the live monitor's action_result_rows (minimal fields extract_evidence reads)."""
    rows: list[dict[str, Any]] = []
    for state in events.states:
        for ar in getattr(state, "action_results", ()) or ():
            aid = getattr(ar, "action_id", None)
            if not aid:
                continue
            rows.append(
                {
                    "action_id": int(aid),
                    "result": getattr(ar, "result", None),
                    "result_field": getattr(ar, "result_field", None),
                    "revealed_items": len(getattr(ar, "observed_items", ()) or ()),
                }
            )
    return rows


def _build_snapshot(
    *,
    hero: str,
    events,
    prefix_batches,
    map_id: int | None = None,
) -> dict:
    bridge = _ahmad_ref_inputs_from_batches(prefix_batches, hero=hero) or {}
    # ⚠️ ROUND FIDELITY (2026-07-09): the engine's round-gated logic reads ui_contract.context.round,
    # which LIVE sets to action_round (the bidding round the user acts on = game round; monitor._action_round).
    # This builder previously used len(prefix_batches) — a STATE-FRAME count, NOT a round:
    # live_batches_from_fatbeans_events emits one batch per game state frame, and each game round produces
    # several frames (public-info reveal / skill / tool / grid update are each their own state). So len(prefix)
    # ran ~2-3x the true round (measured 07-09: ethan R5 full-outline games labelled round 9-10; 77% of ethan /
    # 33% of aisha decision rounds spuriously >=6 vs <=3% truly >=6), and every offline round-gated path fired
    # at the WRONG round — corrupting aisha offline output on ~67% of samples (aisha layout gates on
    # AISHA_LAYOUT_MIN_ROUND etc.). Fix: derive action_round from the game state at the prefix (slice events to
    # the prefix's last sort_id so it is correct whether the caller passed full or already-sliced events),
    # matching live exactly. Fall back to len only if the capture carries no round_no.
    _prefix_sid = _prefix_sort_id(prefix_batches)
    _prefix_events = _events_through_sort(events, _prefix_sid) if _prefix_sid else events
    _action = _action_round(_prefix_events)
    context: dict[str, Any] = {
        "hero": hero,
        "phase": "bidding",
        "round": int(_action) if _action is not None else len(prefix_batches),
        "observed_round": _latest_round(_prefix_events),
    }
    if map_id is not None:
        context["map_id"] = int(map_id)
    # Fidelity fix (2026-07-01): the live snapshot (runtime/snapshot.py) publishes public
    # avg-cells/avg-value facts (200013-16 total/紫/金/红均格 etc.) into constraints.public_info
    # via _ui_public_numeric_contract, which extract_evidence consumes. This offline builder
    # previously left public_info={}, so every offline audit/regress ran BLIND to public 总均格
    # (总均格 constraint + count<->grid cross-check never fired). Route the same rows through the
    # live contract builder so offline replay matches live. Same source rows, no Monte-Carlo.
    public_info_rows = _public_info_rows(events, {})
    action_rows = _action_result_rows_from_events(events)
    snapshot: dict[str, Any] = {
        "ui_contract": {
            "context": context,
            "constraints": {"public_info": _ui_public_numeric_contract(public_info_rows)},
            # extract_evidence reads item-chain results from ui_contract.actions.results (the live
            # contract path), NOT the top-level rows — publish both, mirroring the live snapshot.
            "actions": {"results": action_rows},
        },
        "structured_ref_inputs": bridge,
        "public_info_rows": public_info_rows,
        "skill_reveals": _skill_reveal_rows(events, {}),
        "skill_reveal_rows": _skill_reveal_rows(events, {}),
        # Fidelity fix (2026-07-05): the live snapshot carries 道具 action results (普品/良品扫描
        # -> q1/q3 cells, 优品/极品估价 -> q4/q5 value sums, 极品扫描 -> q5 cells, ...) which
        # extract_evidence consumes via action_result_rows. This builder hardcoded [] so every
        # offline sweep ran BLIND to the player's item chain (real58 ethan: 48/58 games use
        # 普品扫描, 33/58 优品估价 — all silently dropped; the ethan value-chain gate could never
        # fire offline). Bridge the same minimal fields the live monitor publishes.
        "action_result_rows": action_rows,
    }
    if map_id is not None:
        snapshot["map_id"] = int(map_id)
    try:
        tables = load_monitor_tables()
        snapshot["minimap_grid_items"] = _minimap_grid_items(prefix_batches, tables.items)
    except OSError:
        pass
    return snapshot


def _prefix_sort_id(prefix_batches) -> int:
    sort_ids = [int(batch.sequence or 0) for batch in prefix_batches if batch.sequence is not None]
    return max(sort_ids) if sort_ids else 0


def _map_id_from_events(events) -> int | None:
    for state in events.states:
        if state.map_id:
            return int(state.map_id)
    return None


def _evidence_strength(result: dict[str, Any]) -> int:
    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        return 0
    score = 0
    if evidence.get("total_count") not in (None, ""):
        score += 2
    if evidence.get("total_grid_target") not in (None, ""):
        score += 1
    for bucket in ("fixed_counts", "quality_cells", "avg_cells", "split_counts", "avg_values"):
        payload = evidence.get(bucket)
        if isinstance(payload, dict):
            score += sum(1 for value in payload.values() if value not in (None, ""))
    return score


def _gold_price_only_evidence(evidence: dict[str, Any]) -> bool:
    avg_values = evidence.get("avg_values") if isinstance(evidence.get("avg_values"), dict) else {}
    fixed = evidence.get("fixed_counts") if isinstance(evidence.get("fixed_counts"), dict) else {}
    avg_cells = evidence.get("avg_cells") if isinstance(evidence.get("avg_cells"), dict) else {}
    quality_cells = evidence.get("quality_cells") if isinstance(evidence.get("quality_cells"), dict) else {}
    has_gold_price = ("q5" in avg_values and avg_values["q5"] not in (None, "")) or (
        "q5" in (evidence.get("quality_values") or {})
    )
    if not has_gold_price:
        return False
    if fixed.get("q5") not in (None, ""):
        return False
    if avg_cells.get("q5") not in (None, ""):
        return False
    if quality_cells.get("q5") not in (None, ""):
        return False
    return True


def _gold_price_derived_count(
    *,
    evidence: dict[str, Any],
    truth_q5: int,
) -> tuple[bool | None, bool | None]:
    """If only gold avg price + total_count, can _avg_value_count_matches yield unique q5?"""
    if not _gold_price_only_evidence(evidence):
        return None, None
    avg_values = evidence.get("avg_values") or {}
    avg = avg_values.get("q5")
    if avg in (None, ""):
        return None, None
    try:
        total_count = int(evidence.get("total_count"))
        avg_f = float(avg)
    except (TypeError, ValueError):
        return None, None
    if total_count <= 0:
        return None, None
    min_counts = evidence.get("min_counts") if isinstance(evidence.get("min_counts"), dict) else {}
    minimum = max(1, int(min_counts.get("q5", 0) or 0))
    candidates = [
        count
        for count in range(minimum, total_count + 1)
        if _avg_value_count_matches(count, avg_f)
    ]
    if len(candidates) != 1:
        return False, None
    return True, candidates[0] == truth_q5


def _evaluate_at_round(
    *,
    path: Path,
    events,
    audit_round: int,
    pre_batches: list,
) -> GapRow | None:
    settlement = _settlement_breakdown(events)
    if not settlement:
        return None
    prefix_batches = pre_batches[:audit_round]
    sort_id = _prefix_sort_id(prefix_batches)
    prefix_events = _events_through_sort(events, sort_id) if sort_id else events
    snapshot = _build_snapshot(
        hero="aisha",
        events=prefix_events,
        prefix_batches=prefix_batches,
        map_id=_map_id_from_events(events),
    )
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    q_ranges = result.get("quality_count_ranges")
    if not isinstance(q_ranges, dict):
        q_ranges = {}
    cell_ranges = result.get("quality_cells_ranges")
    if not isinstance(cell_ranges, dict):
        cell_ranges = {}
    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    fixed = evidence.get("fixed_counts") if isinstance(evidence.get("fixed_counts"), dict) else {}
    avg_values = evidence.get("avg_values") if isinstance(evidence.get("avg_values"), dict) else {}
    avg_cells = evidence.get("avg_cells") if isinstance(evidence.get("avg_cells"), dict) else {}

    total_count_exact = evidence.get("total_count") not in (None, "")
    total_low, total_mid, total_high = _total_items_range(
        result,
        q_ranges,
        total_count_exact=total_count_exact,
        evidence=evidence,
    )
    grid_low, grid_mid, grid_high = _triplet(result, "total_grid_range")
    rv_low, rv_mid, rv_high = _triplet(result, "red_value_range")
    balanced = result.get("balanced")
    try:
        balanced_int = int(balanced) if balanced not in (None, "") else None
    except (TypeError, ValueError):
        balanced_int = None

    truth_items = int(settlement["total_items"])
    truth_cells = int(settlement["total_cells"])
    truth_total_value = int(settlement["total_value"])
    truth_by_q = {
        "q1": int(settlement["counts"].get(1, 0)),
        "q3": int(settlement["counts"].get(3, 0)),
        "q4": int(settlement["counts"].get(4, 0)),
        "q5": int(settlement["counts"].get(5, 0)),
        "q6": int(settlement["counts"].get(6, 0)),
    }
    truth_cells_by_q = {
        key: int(settlement["cells"].get(int(key[1]), 0)) for key in CELL_KEYS
    }

    count_miss: dict[str, bool] = {}
    gaps: dict[str, int | None] = {}
    count_miss["total_items"] = not _truth_in_range(truth_items, total_low, total_mid, total_high)
    gaps["total_items"] = _mid_gap(truth_items, total_low, total_mid, total_high)
    gaps["total_cells"] = _mid_gap(truth_cells, grid_low, grid_mid, grid_high)
    count_miss["total_cells"] = not _truth_in_range(truth_cells, grid_low, grid_mid, grid_high)

    for key in COUNT_KEYS:
        low, mid, high = _triplet_ranges(q_ranges, key)
        truth = truth_by_q[key]
        count_miss[key] = not _truth_in_range(truth, low, mid, high)
        gaps[key] = _mid_gap(truth, low, mid, high)

    cells_miss: dict[str, bool | None] = {}
    for key in CELL_KEYS:
        if truth_cells_by_q[key] == 0 and key in ("q5", "q6"):
            cells_miss[key] = None
            continue
        low, mid, high = _triplet_ranges(cell_ranges, key)
        if low is None and mid is None and high is None:
            cells_miss[key] = None
        else:
            cells_miss[key] = not _truth_in_range(truth_cells_by_q[key], low, mid, high)
            gaps[f"{key}_cells"] = _mid_gap(truth_cells_by_q[key], low, mid, high)

    truth_q6_value = int(settlement["values"].get(6, 0))
    q6_value_miss = (
        not _truth_in_range(truth_q6_value, rv_low, rv_mid, rv_high) if truth_q6_value > 0 else False
    )
    gaps["q6_value"] = _mid_gap(truth_q6_value, rv_low, rv_mid, rv_high)

    bid_balanced_miss = False
    if balanced_int is not None and truth_total_value > 0:
        # Allow 15% band — ref uses safety_factor on nest prior, not exact inventory sum.
        bid_balanced_miss = not (
            int(balanced_int * 0.85) <= truth_total_value <= int(balanced_int * 1.15)
        )
        gaps["balanced"] = truth_total_value - balanced_int

    gold_unique, gold_truth = _gold_price_derived_count(
        evidence=evidence,
        truth_q5=truth_by_q["q5"],
    )

    map_id = _map_id_from_events(events)

    notes = result.get("notes")
    if isinstance(notes, str):
        note_tuple = tuple(part.strip() for part in notes.split(";") if part.strip())
    elif isinstance(notes, (list, tuple)):
        note_tuple = tuple(str(part) for part in notes)
    else:
        note_tuple = ()

    return GapRow(
        file=path.name,
        map_id=map_id,
        cohort=_cohort_from_path(path),
        rounds=len(pre_batches),
        audit_round=audit_round,
        status=str(result.get("status") or ""),
        total_count_exact=total_count_exact,
        has_gold_avg_value="q5" in avg_values and avg_values["q5"] not in (None, ""),
        has_gold_avg_cells="q5" in avg_cells and avg_cells["q5"] not in (None, ""),
        has_gold_count_lock=fixed.get("q5") not in (None, ""),
        gold_price_only=_gold_price_only_evidence(evidence),
        count_miss=count_miss,
        cells_miss=cells_miss,
        q6_value_miss=q6_value_miss,
        bid_balanced_miss=bid_balanced_miss,
        gold_price_unique_derived=gold_unique,
        gold_price_derived_truth=gold_truth,
        gaps=gaps,
        settlement_total_value=truth_total_value,
        balanced=balanced_int,
        combo_count=int(result.get("combo_count") or 0),
        notes=note_tuple,
    )


def _passes_filters(
    *,
    path: Path,
    events,
    hero: str,
    pre_batches: list,
    settlement: dict[str, Any],
    min_rounds: int,
    min_evidence_score: int,
    max_q6_value: int,
    max_q6_count: int,
    audit_round_override: int,
    stats: FilterStats,
) -> tuple[bool, int]:
    stats.scanned += 1
    if hero != "aisha":
        stats.reasons["not_aisha"] += 1
        return False, 0
    if not settlement:
        stats.reasons["no_settlement"] += 1
        return False, 0
    rounds_file = _rounds_from_filename(path)
    rounds_live = len(pre_batches)
    rounds = rounds_live
    if rounds_file is not None:
        rounds = min(rounds_file, rounds_live) if rounds_live else rounds_file
    if rounds < min_rounds:
        stats.reasons[f"rounds<{min_rounds}"] += 1
        return False, 0
    q6_value = int(settlement.get("values", {}).get(6, 0) or 0)
    q6_count = int(settlement.get("counts", {}).get(6, 0) or 0)
    if q6_value > max_q6_value:
        stats.reasons["tail_q6_value"] += 1
        return False, 0
    if q6_count > max_q6_count:
        stats.reasons["tail_q6_count"] += 1
        return False, 0
    audit_round = audit_round_override or min(max(min_rounds, rounds - 1), len(pre_batches))
    prefix_batches = pre_batches[:audit_round]
    sort_id = _prefix_sort_id(prefix_batches)
    prefix_events = _events_through_sort(events, sort_id) if sort_id else events
    snapshot = _build_snapshot(
        hero="aisha",
        events=prefix_events,
        prefix_batches=prefix_batches,
        map_id=_map_id_from_events(events),
    )
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    status = str(result.get("status") or "")
    if status in {"missing_total_count", "no_reachable_combo"}:
        stats.reasons[f"status:{status}"] += 1
        return False, audit_round
    if _evidence_strength(result) < min_evidence_score:
        stats.reasons["insufficient_evidence"] += 1
        return False, audit_round
    return True, audit_round


def audit_aisha_gaps(
    *,
    sample_roots: tuple[Path, ...],
    sample_files: tuple[Path, ...] | None = None,
    min_rounds: int,
    min_evidence_score: int,
    max_q6_value: int,
    max_q6_count: int,
    audit_round_override: int,
    limit: int,
) -> tuple[list[GapRow], FilterStats]:
    stats = FilterStats()
    rows: list[GapRow] = []
    if sample_files is None:
        files = [
            path
            for root in sample_roots
            if root.is_dir()
            for path in sorted(root.glob("fatbeans*aisha*.json"))
        ]
    else:
        files = sorted(set(sample_files))
    for path in files:
        try:
            events = parse_fatbeans_capture(path)
        except OSError:
            stats.reasons["parse_error"] += 1
            continue
        hero = _hero_from_events(events)
        pre_batches = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        settlement = _settlement_breakdown(events)
        ok, audit_round = _passes_filters(
            path=path,
            events=events,
            hero=hero,
            pre_batches=pre_batches,
            settlement=settlement,
            min_rounds=min_rounds,
            min_evidence_score=min_evidence_score,
            max_q6_value=max_q6_value,
            max_q6_count=max_q6_count,
            audit_round_override=audit_round_override,
            stats=stats,
        )
        if not ok:
            continue
        row = _evaluate_at_round(
            path=path,
            events=events,
            audit_round=audit_round,
            pre_batches=pre_batches,
        )
        if row is None:
            continue
        rows.append(row)
        if limit and len(rows) >= limit:
            return rows, stats
    return rows, stats


def _miss_rate(rows: list[GapRow], predicate) -> float | None:
    applicable = [row for row in rows if predicate(row) is not None]
    if not applicable:
        return None
    return sum(1 for row in applicable if predicate(row)) / len(applicable)


def _avg_abs_gap(rows: list[GapRow], gap_key: str) -> float | None:
    gaps = [abs(row.gaps[gap_key]) for row in rows if row.gaps.get(gap_key) is not None]
    if not gaps:
        return None
    return sum(gaps) / len(gaps)


def _severity(row: GapRow) -> float:
    score = 0.0
    for key, gap in row.gaps.items():
        if gap is None:
            continue
        if key == "balanced":
            score += abs(gap) / max(row.settlement_total_value, 1)
        elif key == "q6_value":
            score += abs(gap) / 500_000.0
        elif "cells" in key or key == "total_cells":
            score += abs(gap) / 10.0
        else:
            score += abs(gap)
    score += sum(1 for miss in row.count_miss.values() if miss) * 2
    score += 3 if row.q6_value_miss else 0
    return score


def _bucket(row: GapRow) -> str:
    if row.total_count_exact and row.count_miss.get("total_cells"):
        return "grid_exact_total"
    if row.count_miss.get("total_cells"):
        return "grid_count_prior"
    if row.count_miss.get("total_items"):
        return "total_items"
    if row.gold_price_only and row.count_miss.get("q5"):
        return "gold_price_only"
    if row.q6_value_miss:
        return "red_value"
    if any(row.count_miss.get(key) for key in ("q3", "q4", "q5", "q6")):
        return "tier_counts"
    return "good_regression"


def select_representative_rows(rows: list[GapRow], *, target: int = 20) -> list[GapRow]:
    chosen: list[GapRow] = []
    seen_files: set[str] = set()
    buckets = (
        "grid_exact_total",
        "grid_count_prior",
        "total_items",
        "gold_price_only",
        "red_value",
        "tier_counts",
        "good_regression",
    )
    for bucket in buckets:
        candidates = sorted(
            [row for row in rows if _bucket(row) == bucket],
            key=_severity,
            reverse=True,
        )
        per_bucket = 3 if bucket != "good_regression" else 2
        for row in candidates:
            if row.file in seen_files:
                continue
            chosen.append(row)
            seen_files.add(row.file)
            if sum(1 for item in chosen if _bucket(item) == bucket) >= per_bucket:
                break
        if len(chosen) >= target:
            break
    if len(chosen) < target:
        for row in sorted(rows, key=_severity, reverse=True):
            if row.file in seen_files:
                continue
            chosen.append(row)
            seen_files.add(row.file)
            if len(chosen) >= target:
                break
    return chosen[:target]


def write_representative_doc(rows: list[GapRow], path: Path) -> None:
    rep = select_representative_rows(rows)
    lines = [
        "# Hero Ref 艾莎代表样本表（2026-06-13）",
        "",
        "从 curated gap audit 中按误差类型分层抽取，供 §61 批 B 回归与人工复核。",
        "不全库使用；高 tail / ≤2 轮 / 证据不足样本已排除。",
        "",
        f"- 源脚本：`scripts/audit_aisha_gap.py`",
        f"- curated 池：见 `data/reports/audit_aisha_gap.txt`",
        f"- 本表条数：**{len(rep)}**",
        "",
        "| # | 桶 | 文件 | map | r | 总格gap | 总件gap | q5 | q6值 | 金价only | balanced Δ |",
        "|---|---|---|---:|---:|---:|---:|---|---|---|---:|",
    ]
    for idx, row in enumerate(rep, start=1):
        lines.append(
            f"| {idx} | {_bucket(row)} | `{row.file[:48]}` | {row.map_id or '?'} | "
            f"{row.audit_round}/{row.rounds} | {row.gaps.get('total_cells')} | "
            f"{row.gaps.get('total_items')} | {row.gaps.get('q5')} | {row.gaps.get('q6_value')} | "
            f"{'Y' if row.gold_price_only else '-'} | {row.gaps.get('balanced')} |"
        )
    lines.extend(
        [
            "",
            "## 批 B 回归门禁（15 条 `total_count_exact`）",
            "",
            "优先用桶 `grid_exact_total` + `gold_price_only` 中带精确总件的样本；",
            "每条改动需：`total_cells` band 覆盖或 mid-gap 缩小，且 `balanced` 不劣化 >15%。",
            "",
        ]
    )
    exact = sorted([row for row in rows if row.total_count_exact], key=_severity, reverse=True)[:15]
    for idx, row in enumerate(exact, start=1):
        lines.append(
            f"{idx}. `{row.file}` — map {row.map_id}, cells_gap={row.gaps.get('total_cells')}, "
            f"q5_gap={row.gaps.get('q5')}, status={row.status}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_report(
    rows: list[GapRow],
    stats: FilterStats,
    *,
    min_rounds: int,
    min_evidence_score: int,
    audit_round_override: int,
    selection_metadata: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("audit_aisha_gap (2026-06-13)")
    if selection_metadata:
        lines.append(f"selector={selection_metadata['selector']}")
        lines.append(f"sample_sources={selection_metadata['sources']}")
        lines.append(
            "sample_scope="
            f"count={selection_metadata['sample_count']} "
            f"time=[{selection_metadata['capture_time_min']},{selection_metadata['capture_time_max']}] "
            f"unknown_time={selection_metadata['unknown_time_count']} "
            f"activity={selection_metadata['activity_count']} "
            f"commit={selection_metadata['commit']}"
        )
    lines.append(f"curated_rows={len(rows)} scanned={stats.scanned}")
    round_note = "penultimate" if audit_round_override in (0, None) else f"fixed_r{audit_round_override}"
    lines.append(f"audit_round_mode={round_note}")
    lines.append(
        f"filters: min_rounds={min_rounds} min_evidence_score={min_evidence_score} "
        "exclude_tail_q6 exclude_status_missing/no_combo"
    )
    lines.append("")
    lines.append("filter_funnel:")
    for reason, count in stats.reasons.most_common():
        lines.append(f"  {reason}: {count}")

    if not rows:
        lines.append("")
        lines.append("No curated rows.")
        return "\n".join(lines)

    count_dims = [
        ("total_items", "total_items"),
        ("total_cells", "total_cells"),
        ("q1", "q1"),
        ("q3", "q3"),
        ("q4", "q4"),
        ("q5", "q5"),
        ("q6", "q6"),
    ]
    cell_dims = [("q3_cells", "q3"), ("q4_cells", "q4"), ("q5_cells", "q5"), ("q6_cells", "q6")]

    lines.append("")
    lines.append("miss_rate (settlement outside ref band):")
    ranked: list[tuple[str, float]] = []
    for label, key in count_dims:
        rate = _miss_rate(rows, lambda row, k=key: row.count_miss.get(k))
        if rate is None:
            continue
        gap = _avg_abs_gap(rows, key)
        gap_text = f" avg_abs_mid_gap={gap:.1f}" if gap is not None else ""
        lines.append(f"  {label:12} {rate*100:5.1f}%{gap_text}")
        ranked.append((label, rate))
    for label, key in cell_dims:
        rate = _miss_rate(rows, lambda row, k=key: row.cells_miss.get(k))
        if rate is None:
            lines.append(f"  {label:12}   n/a")
            continue
        gap = _avg_abs_gap(rows, f"{key}_cells")
        gap_text = f" avg_abs_mid_gap={gap:.1f}" if gap is not None else ""
        lines.append(f"  {label:12} {rate*100:5.1f}%{gap_text}")
        ranked.append((label, rate))
    rv_rate = _miss_rate(rows, lambda row: row.q6_value_miss)
    if rv_rate is not None:
        gap = _avg_abs_gap(rows, "q6_value")
        gap_text = f" avg_abs_mid_gap={gap:.0f}" if gap is not None else ""
        lines.append(f"  {'q6_value':12} {rv_rate*100:5.1f}%{gap_text}")
        ranked.append(("q6_value", rv_rate))
    bid_rate = _miss_rate(rows, lambda row: row.bid_balanced_miss)
    if bid_rate is not None:
        lines.append(f"  {'balanced_bid':12} {bid_rate*100:5.1f}% (±15% vs settlement total value)")
        ranked.append(("balanced_bid", bid_rate))

    ranked.sort(key=lambda item: item[1], reverse=True)
    lines.append("ranked: " + ", ".join(f"{name}={rate*100:.0f}%" for name, rate in ranked[:6]))

    exact = [row for row in rows if row.total_count_exact]
    lines.append("")
    lines.append(f"subset_exact_total_count: n={len(exact)}")
    if exact:
        for label, key in count_dims:
            if label == "total_items":
                continue
            rate = _miss_rate(exact, lambda row, k=key: row.count_miss.get(k))
            if rate is None:
                continue
            lines.append(f"  exact.{label:10} {rate*100:5.1f}%")

    gold_only = [row for row in rows if row.gold_price_only]
    lines.append("")
    lines.append(f"subset_gold_price_only: n={len(gold_only)} (public/structured 金均价，无金扫描/均格/件数)")
    if gold_only:
        q5_miss = _miss_rate(gold_only, lambda row: row.count_miss.get("q5"))
        if q5_miss is not None:
            lines.append(f"  q5_count_miss {q5_miss*100:.1f}%")
        unique_rows = [row for row in gold_only if row.gold_price_unique_derived is not None]
        if unique_rows:
            unique_rate = sum(1 for row in unique_rows if row.gold_price_unique_derived) / len(unique_rows)
            truth_rate = sum(
                1 for row in unique_rows if row.gold_price_derived_truth is True
            ) / max(1, sum(1 for row in unique_rows if row.gold_price_unique_derived))
            lines.append(
                f"  price_only_unique_q5_from_avg_value: {unique_rate*100:.1f}% of subset "
                f"(engine today requires avg_cells too for avg_value_cells_* derivation)"
            )
            lines.append(f"  when_unique_matches_truth: {truth_rate*100:.1f}%")
        lines.append(
            "  note: v0 batch-B may trial q5 avg_value-only count pin when unique + total_count exact"
        )

    lines.append("")
    lines.append("interpretation:")
    lines.append("  - total_items miss reflects count_prior / per-tier sum band, not only 200017")
    lines.append("  - total_cells + tier counts are primary v0 gaps; balanced_bid is secondary")
    lines.append("  - gold avg price alone rarely unique without avg_cells — see subset above")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-rounds", type=int, default=3)
    parser.add_argument("--min-evidence-score", type=int, default=4)
    parser.add_argument("--max-q6-value", type=int, default=1_200_000)
    parser.add_argument("--max-q6-count", type=int, default=4)
    parser.add_argument(
        "--audit-round",
        type=int,
        default=0,
        help="Force audit round (0=penultimate bidding round before settle)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max curated rows (0=all)")
    parser.add_argument(
        "--activity",
        action="store_true",
        help="Use the explicit 20260702 shipwreck activity pool instead of clean-eval",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--write-representative-doc", type=Path, default=None)
    args = parser.parse_args()

    selector = "activity_20260702:aisha" if args.activity else "clean_eval:aisha"
    selected = activity_eval_files("aisha") if args.activity else clean_eval_files("aisha")
    metadata = sample_selection_metadata(selected, selector=selector)
    rows, stats = audit_aisha_gaps(
        sample_roots=SAMPLE_ROOTS,
        sample_files=tuple(Path(path) for path in selected),
        min_rounds=args.min_rounds,
        min_evidence_score=args.min_evidence_score,
        max_q6_value=args.max_q6_value,
        max_q6_count=args.max_q6_count,
        audit_round_override=args.audit_round,
        limit=args.limit,
    )
    report = format_report(
        rows,
        stats,
        min_rounds=args.min_rounds,
        min_evidence_score=args.min_evidence_score,
        audit_round_override=args.audit_round,
        selection_metadata=metadata,
    )
    print(report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report + "\n", encoding="utf-8")
    print(f"\nwrote {args.report}")

    rep_path = args.write_representative_doc or DEFAULT_REPRESENTATIVE_DOC
    if rows:
        write_representative_doc(rows, rep_path)
        print(f"wrote {rep_path}")


if __name__ == "__main__":
    main()
