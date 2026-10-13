"""Approximate replay of cloud collector `ref_inputs` records — RED-PRESENCE / DIRECTION ANALYSIS ONLY.

⚠️⚠️ DO NOT USE THIS FOR VALUE / BID TUNING (B5, red unit price, etc.). Verified 2026-07-08/09 (engine +
audit windows, §5): the cloud upload stores only `ref_inputs` (== live structured_ref_inputs) and its
`counts` DROPS q6 (the revealed red count). Live, the engine reads reveal-driven red from
`ui_contract.constraints.counts.known_quality_counts` (has q6) / `public_info_rows[200041]` — NONE of which
the cloud record persists. So raw ref_inputs replay collapses reveal-locked red to a count-prior phantom
(aisha 2410: red [0,0,1]/275k vs faithful [4,4,5]/791k). 94% (10231/10859) of ref_inputs lack counts.q6.

Reconstruction path (2026-07-09 fix): the by-quality tally of ref_inputs.minimap_items (the BIDDING
snapshot) is fed through the engine's REVEAL path — ui_contract.constraints.counts.known_quality_counts,
reconciled with the grid — exactly as live does. It is NOT injected into ri["counts"] (the exact-lock
domain: the game never sends an exact red count, so an injected q6 over-constrains the bridge's red=0
count_sums/total and collapses to no_reachable_combo — the reason the release window's "merge into counts"
approach was withdrawn). This makes the snapshot REACHABLE and the red count DIRECTIONALLY right (floor
tracks the revealed/prior red).

⚠️ It is NOT tuning-grade even so. Same-map faithful-diag check (aisha 5605): cloud red=[3,5,7] vs faithful
locked [3,3,3]; cloud balanced 934k vs faithful 738-806k (+16-27%). The tight live red lock needs the
per-item footprint detail (item_id/shape) that cloud minimap_items often lack, and the full combo/constraint
context is not reproducible from ref_inputs. Tune B5 / red price on the DIAG round_snapshots packages
(which carry constraints + public_info_rows), never on this. This helper answers "is there red / roughly how
much / does the estimate move the right way", not "what is the exact red value/bid".

`build_snapshot(rec)` -> engine snapshot (reachable, red-count directional, value/bid NOT faithful).
`replayable_reason(rec)` -> None when the bidding-phase minimap quality evidence is complete enough
for directional replay, else why-not. Final settlement red count is deliberately NOT an eligibility
gate: bidding evidence is partial by design and draw heroes are not expected to reveal the whole vault.
NOT a live path; reconstruction only touches replay (no engine/monitor/live code changed).
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

QUALITY_REVEAL_INFO_ID = 200041  # aisha per-item quality reveal (value_field 8)


def recorded_quote_evidence(rec: dict) -> dict:
    """Client-reported last-recorded arithmetic only; NOT a faithful model replay."""
    from bidking_lab.live.quote_preferences import validate_settlement_record, unknown
    if not rec.get("session_hash"):
        return unknown("unavailable")
    return validate_settlement_record(rec.get("quote_application", unknown("missing")),
                                      session_hash=rec.get("session_hash"),
                                      settlement_cutoff=rec.get("client_ts"))


REF_CORE_FIELDS = (
    "hero",
    "counts",
    "avg_cells",
    "avg_values",
    "quality_cells",
    "quality_values",
    "count_sums",
    "total_count",
    "total_cells",
    "minimap_items",
)


def _quality_of(item: dict[str, Any]) -> int | None:
    q = item.get("quality")
    try:
        return int(q) if q is not None else None
    except (TypeError, ValueError):
        return None


def _minimap_items(rec: dict[str, Any]) -> list[dict[str, Any]]:
    ri = rec.get("ref_inputs") or {}
    mm = ri.get("minimap_items")
    return [it for it in mm if isinstance(it, dict)] if isinstance(mm, list) else []


def parse_quality_map(s: Any) -> dict[str, float]:
    d: dict[str, float] = {}
    for part in str(s or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                d[k] = float(v)
            except ValueError:
                pass
    return d


def build_snapshot(rec: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct an engine snapshot from a cloud ref_inputs record.

    Synthesizes the quality-reveal (public_info_rows + ui_contract.minimap.quality_reveal_counts) from
    ref_inputs.minimap_items so the engine locks reveal-driven red counts as it did live.
    """
    ri = dict(rec.get("ref_inputs") or {})
    mm = _minimap_items(rec)
    qc: Counter[str] = Counter()
    for it in mm:
        q = _quality_of(it)
        if q:
            qc[f"q{q}"] += 1
    detail = [
        {
            "local_index": it.get("local_index"),
            "runtime_id": it.get("runtime_id"),
            "item_id": it.get("item_id"),
            "quality": it.get("quality"),
            "value": it.get("value"),
            "shape_code": it.get("shape_code"),
            "cells": it.get("cells"),
        }
        for it in mm
    ]
    public_info_rows = [
        {
            "sort": 11,
            "info_id": QUALITY_REVEAL_INFO_ID,
            "map_id": rec.get("map_id"),
            "value": len(detail),
            "value_field": 8,
            "revealed_items": len(detail),
            "revealed_items_detail": detail,
        }
    ]
    # Feed the bidding-phase revealed counts through the engine's REVEAL path, exactly as live does:
    # extract_evidence (ahmad_ref_engine.py:5106-5122) reads
    # ui_contract.constraints.counts.known_quality_counts and RECONCILES it with the grid/cells — it is
    # NOT a fixed_counts exact-lock. Live simultaneously carries structured_ref_inputs.counts (the game's
    # exact per-quality件数 for q3/q4, an exact lock) AND this minimap-tally known_quality_counts (all
    # revealed qualities incl. q6 red); reconstructing BOTH reproduces the live contract → reachable and
    # red-faithful. `qc` is tallied from ref_inputs.minimap_items = the BIDDING snapshot, so q6 here is the
    # bidding-revealed red (phase-correct), never the settled final_quality_counts. Do NOT inject qc into
    # ri["counts"]: counts is the exact-lock domain (→ _merge_quality_counts → fixed_counts → _count_values
    # returns [N] single-value), and the game never sends an exact red count, so an injected q6 over-
    # constrains against the bridge's red=0 count_sums/total and collapses to no_reachable_combo
    # (verified 2026-07-09: engine + release windows, memory cloud-ref-inputs-replay-fidelity §07-09).
    ui_contract = {
        "minimap": {"items": mm, "quality_counts": dict(qc),
                    "quality_reveal_counts": dict(qc), "columns": 10},
        "constraints": {"counts": {"known_quality_counts": dict(qc)}},
    }
    ri["counts"] = dict(ri.get("counts") or {})  # copy: never mutate the caller's exact-lock counts
    return {
        "hero": ri.get("hero") or rec.get("hero"),
        "map_id": rec.get("map_id"),
        "round": rec.get("round"),
        "action_round": rec.get("action_round"),
        "structured_ref_inputs": ri,
        "minimap_grid_items": mm,
        "public_info_rows": public_info_rows,
        "ui_contract": ui_contract,
        "source": "cloud_ref_replay",
    }


def replayable_reason(rec: dict[str, Any]) -> str | None:
    """None if stored bidding evidence supports red-directional replay.

    This is a capability check, not a settlement-agreement gate. A pre-settlement reveal may contain
    fewer red items than final truth without any capture loss, so equality is only a descriptive metric.
    """
    ref_inputs = rec.get("ref_inputs")
    if not isinstance(ref_inputs, dict) or not ref_inputs:
        return "no_ref_inputs"
    missing = [field for field in REF_CORE_FIELDS if field not in ref_inputs]
    if missing:
        return "missing_ref_core:" + ",".join(missing)
    if rec.get("hero") in (None, "") or rec.get("map_id") in (None, ""):
        return "missing_hero_or_map"
    if rec.get("round") in (None, "") and rec.get("action_round") in (None, ""):
        return "missing_round_context"
    raw_items = ref_inputs.get("minimap_items")
    if not isinstance(raw_items, list) or not raw_items:
        return "minimap_items_empty"
    if any(not isinstance(item, dict) for item in raw_items):
        return "minimap_item_not_mapping"
    if any(_quality_of(item) is None for item in raw_items):
        return "minimap_quality_incomplete"
    return None
