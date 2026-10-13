from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import permutations
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Literal, Mapping
from urllib.parse import urlparse


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = Path("data/logs/live/latest_snapshot.json")
STALE_SNAPSHOT_SECONDS = 60.0
SETTLED_STALE_SECONDS = 60.0
CAPTURE_SESSION_RECENT_SECONDS = 20.0
# Gabriela 2401 live: same-session reading/bidding can exceed 60s without a new snapshot write
# while the last count_prior is still the best available quote — keep it visible with a stale flag
# instead of wiping to standby. session_ahead / monitor_restarted still force full standby.
HERO_REF_DISABLE_STALE_KEEP_LAST_QUOTE = "HERO_REF_DISABLE_STALE_KEEP_LAST_QUOTE"
MARIA_R1_LOW_TIER_COUNT_DISPLAY_DISABLE = (
    "BIDKING_DISABLE_MARIA_R1_LOW_TIER_COUNT_DISPLAY"
)
CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV = (
    "BIDKING_DISABLE_CURRENT_JOINT_EXACT_CONTINUITY"
)
STALE_KEEP_LAST_QUOTE_PHASES = frozenset({"reading", "bidding"})
LAB_ROOT = Path(__file__).resolve().parents[1]
LAB_SRC = LAB_ROOT / "src"
LAB_TOOLS = Path(__file__).resolve().parent
if str(LAB_SRC) not in sys.path:
    sys.path.insert(0, str(LAB_SRC))
if str(LAB_TOOLS) not in sys.path:
    sys.path.insert(0, str(LAB_TOOLS))
from detail_composition_view import (  # noqa: E402
    QUALITY_COMPOSITION_SCHEMA_VERSION,
    detail_composition_view,
    format_compact_quality_count,
    quality_is_strict_exact,
)
from bidking_lab.live.live_refresh_signatures import (  # noqa: E402
    ref_input_signature_from_snapshot,
)
from bidking_lab.live.quote_preferences import quote_basis, monitor_generation  # noqa: E402
from bidking_lab.runtime.snapshot import (  # noqa: E402
    project_raven_template_alias_receipt,
)
QUALITY_LABELS = {
    "q1": "白绿",
    "q3": "蓝",
    "q4": "紫",
    "q5": "金",
    "q6": "红",
}
QUALITY_DISPLAY_ORDER = ("q1", "q3", "q4", "q5", "q6")
MAX_COMPACT_COUNT_ARM_STATES = 4096
LOW_QUALITY_DISPLAY_ORDER = ("q1", "q3")
SPLIT_QUALITY_LABELS = {
    "white": "白",
    "green": "绿",
}
SPLIT_QUALITY_DISPLAY_ORDER = ("white", "green")

try:
    from ahmad_ref_engine import (
        AISHA_EARLY_ROUND_MAX_COMBOS,
        HERO_ALIASES,
        HERO_BY_ID,
        STRUCTURED_REF_HERO_KEYS,
        _activity_2511_provisional_scope_state,
        is_supported_ref_hero,
        normalize_hero_key,
        prepare_reference_engine_snapshot,
        run_reference_engine,
        stabilize_reference_engine_external_snapshot,
    )
except Exception:  # noqa: BLE001 - keep debug server usable without ref core
    HERO_ALIASES = {
        "aisha": "aisha",
        "艾莎": "aisha",
        "ahmad": "ahmed",
        "ahmed": "ahmed",
        "ahamed": "ahmed",
        "艾哈": "ahmed",
        "艾哈迈德": "ahmed",
        "victor": "victor",
        "维克": "victor",
        "维克托": "victor",
    }
    HERO_BY_ID = {
        103: "aisha",
        204: "ahmed",
        209: "victor",
    }
    STRUCTURED_REF_HERO_KEYS = frozenset({"aisha", "ahmed", "victor"})

    def normalize_hero_key(hero: Any) -> str:
        text = _text(hero, "").strip()
        if not text or text.lower() in {"?", "unknown", "none", "null"}:
            return ""
        return HERO_ALIASES.get(text.lower(), HERO_ALIASES.get(text, text.lower()))

    def is_supported_ref_hero(hero: Any) -> bool:
        return normalize_hero_key(hero) in set(HERO_BY_ID.values())

    run_reference_engine = None  # type: ignore[assignment,misc]
    def _activity_2511_provisional_scope_state(snapshot: Any) -> str:
        return "engine_unavailable"
    AISHA_EARLY_ROUND_MAX_COMBOS = 2500  # type: ignore[misc]

    def prepare_reference_engine_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return dict(snapshot)

    def stabilize_reference_engine_external_snapshot(
        snapshot: Any,
    ) -> dict[str, Any]:
        return snapshot if type(snapshot) is dict else {}  # noqa: E721

try:
    from draw_hero_policy import (
        DRAW_HERO_COUNT_KEYS,
        DRAW_HERO_DECISION_ROUND_MIN,
        draw_hero_progressive_prop_hint,
        draw_hero_prop_for_gap,
        draw_hero_uses_prop_hint,
    )
except Exception:  # noqa: BLE001 - draw-hero prop hints are optional polish
    DRAW_HERO_COUNT_KEYS = frozenset({"sophie", "gabriela", "raven", "wuqilin"})
    DRAW_HERO_DECISION_ROUND_MIN = 4

    def draw_hero_uses_prop_hint(normalized_hero: Any) -> bool:  # type: ignore[misc]
        return False

    def draw_hero_prop_for_gap(gap: str, *, limit: int = 2) -> str:  # type: ignore[misc]
        return ""

    def draw_hero_progressive_prop_hint(  # type: ignore[misc]
        normalized_hero: Any,
        *,
        round_no: int | None,
        session_id: str = "",
        used_action_ids: Any = (),
    ) -> str:
        return ""


# Maria's R1 skill can arrive after the reading/bidding snapshot.  Her map/count
# prior is already sufficient for a clearly-labelled provisional quote, so do
# not leave that short opening interval blank.  Keep the draw-hero calibration
# set itself unchanged: Maria does not use its count scaling or prop policy.
PROVISIONAL_COUNT_PRIOR_HERO_KEYS = frozenset((*DRAW_HERO_COUNT_KEYS, "maria"))

try:
    from hero_ref_live_schedule import (
        EARLY_ROUND_ENGINE_FLAG,
        hero_max_combos_for_round,
        hero_quote_ready,
        hero_ref_wait_hint,
        hero_ref_waiting_result,
        hero_should_run_scheduled_inference,
        hero_uses_dual_pass,
    )
except Exception:  # noqa: BLE001 - schedule optional when panel runs standalone
    EARLY_ROUND_ENGINE_FLAG = "audit_aisha_early_round"

    def hero_should_run_scheduled_inference(hero_key: str, round_no: int | None, phase: str) -> bool:
        return False

    def hero_uses_dual_pass(hero_key: str, round_no: int | None, phase: str) -> bool:
        return hero_key == "aisha" and phase not in ("settled", "manual") and round_no is not None and 1 <= int(round_no) <= 5

    def hero_max_combos_for_round(hero_key: str, round_no: int | None) -> int:
        if hero_key == "aisha" and round_no is not None and int(round_no) < 3:
            return AISHA_EARLY_ROUND_MAX_COMBOS
        return 20_000

    def hero_quote_ready(
        snapshot: dict[str, Any],
        hero_key: str,
        round_no: int,
        public_info: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        del snapshot, hero_key, round_no, public_info
        return True, ""

    def hero_ref_waiting_result(*, hero_key: str, wait_hint: str, category: Any = None) -> dict[str, Any]:
        del hero_key, category
        return {"status": "missing_total_count", "source": "ref_v0", "notes": [f"hero_ref_wait:{wait_hint}"]}

    def hero_ref_wait_hint(ref_notes: list[str]) -> str:
        for note in ref_notes:
            text = str(note)
            if text.startswith("hero_ref_wait:"):
                return text.split(":", 1)[1]
        return ""


def _dig(value: Any, *path: str, default: Any = None) -> Any:
    current = value
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _mask_player_display_name(name: str) -> str:
    """UI privacy: keep first/last character, mask the rest."""
    text = str(name or "").strip()
    if len(text) <= 1:
        return text
    if len(text) == 2:
        return f"{text[0]}*{text[1]}"
    return f"{text[0]}{'*' * (len(text) - 2)}{text[-1]}"


def _mask_bidder_display_text(value: Any) -> str:
    """Mask player name in 'name bid' readouts; leave settlement totals untouched."""
    text = _text(value, "-").strip()
    if text in ("", "-"):
        return text
    if text.startswith("总值"):
        return text
    name_part, separator, tail = text.rpartition(" ")
    if separator and any(ch.isdigit() for ch in tail):
        return f"{_mask_player_display_name(name_part)} {tail}"
    return _mask_player_display_name(text)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是"}
    return bool(value)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _settlement_truth_snapshot(raw_truth: Any) -> dict[str, Any]:
    """Copy bounded display truth without trusting container protocols.

    Engine preparation rejects an oversized ``ui_contract`` as one atomic
    transaction.  A complete settlement can exceed that budget through its
    unrelated minimap/action detail while its small truth block remains valid.
    Preserve only that block for settlement display; malformed, subclassed,
    cyclic or resource-heavy values fail closed.
    """
    node_budget = [0]
    active: set[int] = set()
    invalid = object()

    def project(value: Any, depth: int) -> Any:
        node_budget[0] += 1
        if node_budget[0] > 256 or depth > 8:
            return invalid
        if value is None or type(value) is bool:  # noqa: E721 - exact JSON only
            return value
        if type(value) is int:  # noqa: E721 - reject subclasses
            return value if int.bit_length(value) <= 63 else invalid
        if type(value) is float:  # noqa: E721 - reject subclasses/nonfinite
            return value if math.isfinite(value) else invalid
        if type(value) is str:  # noqa: E721 - reject subclasses
            return value if len(value) <= 1_024 else invalid
        if type(value) is dict:  # noqa: E721 - builtin methods only
            if len(value) > 64:
                return invalid
            identity = id(value)
            if identity in active:
                return invalid
            active.add(identity)
            try:
                result: dict[str, Any] = {}
                for key, child in dict.items(value):
                    if type(key) is not str or len(key) > 128:  # noqa: E721
                        return invalid
                    projected = project(child, depth + 1)
                    if projected is invalid:
                        return invalid
                    result[key] = projected
                return result
            finally:
                active.remove(identity)
        if type(value) is list:  # noqa: E721 - builtin methods only
            if len(value) > 64:
                return invalid
            identity = id(value)
            if identity in active:
                return invalid
            active.add(identity)
            try:
                result_list: list[Any] = []
                for child in value:
                    projected = project(child, depth + 1)
                    if projected is invalid:
                        return invalid
                    result_list.append(projected)
                return result_list
            finally:
                active.remove(identity)
        return invalid

    projected = project(raw_truth, 0)
    return projected if type(projected) is dict else {}  # noqa: E721


def _clone_as_pre_settlement_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(snapshot, ensure_ascii=False))
    cloned["phase"] = "bidding"
    # The settled-view "估价" card must reproduce the last live (pre-settlement)
    # estimate, not re-estimate with settlement truth. Flipping phase alone is not
    # enough: settlement-grade evidence stays in the snapshot body and ui_contract
    # (notably ui_contract.constraints, which carries exact settlement counts), so
    # the engine would read it back and inflate the estimate. Strip those so the
    # engine falls back to the pre-settlement live evidence stream
    # (structured_ref_inputs / monitor field updates).
    for key in list(cloned.keys()):
        if key.startswith("final_"):
            cloned.pop(key, None)
    for key in (
        "inventory_count",
        "inventory_cells",
        "known_value_sum",
        "minimap_grid_items",
        "model_eval",
    ):
        cloned.pop(key, None)
    uc = cloned.get("ui_contract") if isinstance(cloned.get("ui_contract"), dict) else {}
    cloned["ui_contract"] = uc
    for key in ("constraints", "minimap"):
        uc.pop(key, None)
    context = uc.get("context") if isinstance(uc.get("context"), dict) else {}
    uc["context"] = context
    context["phase"] = "bidding"
    truth = uc.get("truth") if isinstance(uc.get("truth"), dict) else {}
    uc["truth"] = truth
    truth["available"] = False
    return cloned


def _quote_receipt_sha256(value: Any) -> str:
    """Hash a JSON-safe diagnostic projection without retaining its payload."""

    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(payload).hexdigest()


def _quote_receipt(
    snapshot: dict[str, Any],
    result: dict[str, Any],
    *,
    source: str,
    decision_range: str,
    value_range: str,
) -> dict[str, Any]:
    """Build a support-safe identity for the exact input/result behind a quote.

    The engine input signature is already the overlay memoization contract.  Its
    first field is the private session id, so the receipt deliberately removes
    that field before hashing.  No path, player identity, packet body, or raw
    evidence table is serialized into the receipt.
    """

    if not isinstance(result, dict) or result.get("status") not in {
        "ok",
        "count_prior",
        "provisional_estimated",
    }:
        return {}
    try:
        signature = ref_input_signature_from_snapshot(snapshot)
    except Exception:  # noqa: BLE001 - diagnostics must not break the quote path
        return {}
    if not isinstance(signature, tuple) or len(signature) < 2:
        return {}
    input_fingerprint = _quote_receipt_sha256(
        ("hero_ref_input_without_session_v1", *signature[1:])
    )
    result_identity = _quote_receipt_sha256(result)
    if not input_fingerprint or not result_identity:
        return {}

    ui_contract = (
        snapshot.get("ui_contract")
        if type(snapshot.get("ui_contract")) is dict  # noqa: E721 - JSON boundary
        else {}
    )
    context = (
        ui_contract.get("context")
        if type(ui_contract.get("context")) is dict  # noqa: E721 - JSON boundary
        else {}
    )
    phase = _text(context.get("phase") or snapshot.get("phase"), "?")
    formal_mode = _text(
        snapshot.get("formal_mode")
        or context.get("formal_mode")
        or _dig(snapshot, "parameters", "formal_mode"),
        "unknown",
    )
    round_no = _parse_int(context.get("round") or snapshot.get("round"))
    action_round = _parse_int(
        context.get("action_round") or snapshot.get("action_round")
    )
    observed_round = _parse_int(
        context.get("observed_round") or snapshot.get("observed_round")
    )
    result_status = _text(result.get("status"), "")
    result_combo_count = _parse_int(result.get("combo_count"))
    balanced = _parse_money(result.get("balanced"))
    if (
        source not in {"live_ref_result", "settlement_clone"}
        or phase not in {"reading", "bidding"}
        or round_no is None
        or action_round is None
        or observed_round is None
        or min(round_no, action_round, observed_round) < 0
        or formal_mode in {"", "?", "unknown"}
        or result_status not in {"ok", "count_prior", "provisional_estimated"}
        or result_combo_count is None
        or result_combo_count < 1
        or balanced is None
        or not isinstance(decision_range, str)
        or decision_range in {"", "-"}
        or not isinstance(value_range, str)
        or value_range in {"", "-"}
    ):
        return {}
    return {
        "schema": "hero_ref_quote_receipt_v1",
        "source": source,
        "round": round_no,
        "action_round": action_round,
        "observed_round": observed_round,
        "phase": phase,
        "formal_mode": formal_mode,
        "input_fingerprint_scope": "ref_inputs_without_session_v1",
        "input_fingerprint": input_fingerprint,
        "result_identity": result_identity,
        "result_status": result_status,
        "result_combo_count": result_combo_count,
        "balanced": balanced,
        "decision_range": decision_range,
        "value_range": value_range,
    }


def _pre_settlement_ref_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    if run_reference_engine is None:
        return {}
    # Display-only "what the bid WOULD have been just before the truth landed" estimate, computed on a
    # truth-UNlocked clone — so deep draw vaults re-enumerate the full combo space (~11s uncapped). The
    # overlay computes settlement INLINE on the Tk UI thread (settlement-preempt), so that ~11s FROZE the
    # UI ("结算等很久才出"). The settled truth is shown alongside this estimate, so a small cap is fine:
    # 2000 combos -> ~0.4s on the deepest observed vault (~4% off the uncapped估价). Tune/disable via
    # env AHMAD_PRE_SETTLEMENT_MAX_COMBOS.
    try:
        cap = int(os.environ.get("AHMAD_PRE_SETTLEMENT_MAX_COMBOS", "2000"))
    except (TypeError, ValueError):
        cap = 2000
    try:
        result = run_reference_engine(
            prepare_reference_engine_snapshot(_clone_as_pre_settlement_snapshot(snapshot)),
            max_combos=cap,
        ).as_dict()
    except Exception:
        return {}
    if result.get("status") not in {
        "ok",
        "count_prior",
        "provisional_estimated",
    }:
        return {}
    return result


def _flag(label: str, level: str = "watch", detail: str = "") -> dict[str, str]:
    return {"label": label, "level": level, "detail": detail}


def _money(value: Any, fallback: str = "-") -> str:
    if value in (None, ""):
        return fallback
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return str(value)


def _compact_float(value: Any, *, max_decimals: int = 4) -> str:
    if value in (None, ""):
        return "-"
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return str(value)
    if parsed.is_integer():
        return str(int(parsed))
    return f"{parsed:.{max_decimals}f}".rstrip("0").rstrip(".")


def _compact_grid_cells(value: Any) -> str:
    """Warehouse grid counts are whole cells; layout hints may carry float noise."""
    if value in (None, ""):
        return "-"
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(round(parsed)))


def _range_text(value: Any, *, money: bool = False, suffix: str = "") -> str:
    if not isinstance(value, (list, tuple)) or not value:
        return "-"
    parts: list[str] = []
    for item in value[:3]:
        if item in (None, ""):
            parts.append("-")
        elif money:
            parts.append(_money(item))
        else:
            parts.append(_compact_grid_cells(item))
    if not parts or all(part == "-" for part in parts):
        return "-"
    text = " / ".join(parts)
    return f"{text}{suffix}" if suffix else text


def _locked_range_value(value: Any) -> int | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    parsed = [_parse_int(item) for item in value[:3]]
    if any(item is None for item in parsed):
        return None
    first = parsed[0]
    if all(item == first for item in parsed):
        return first
    return None


def _hard_grid_target_locked(evidence: dict[str, Any]) -> bool:
    target = evidence.get("total_grid_target")
    if target in (None, ""):
        return False
    notes = {str(item) for item in (evidence.get("source_notes") or ())}
    if "total_grid_target_soft_forced_red_tail" in notes:
        return False
    hard_notes = {
        "structured_ref_bridge_total_cells",
        "field_update_total_cells",
        "public_total_cells",
        "public_info_total_cells",
        "action_100103_total_cells",
        "settlement_review_total_grid",
        "total_grid_target_from_known_high_tier_cells",
        "total_grid_from_exhausted_quality_cells",
    }
    return any(note in hard_notes for note in notes)


def _grid_summary_parts(
    ref_result: dict[str, Any], evidence: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Return (locked_total_grid, estimated_range). Prefer float range unless grid is hard-known."""
    grid_range = ref_result.get("total_grid_range")
    range_text = _compact_range_text(grid_range, suffix="格")
    range_unlocked = range_text != "-" and _range_is_unlocked(grid_range)
    target = evidence.get("total_grid_target")
    if _hard_grid_target_locked(evidence):
        return (f"总格 {_compact_grid_cells(target)}", None)
    if range_unlocked:
        return (None, f"估总格 {range_text}")
    if target not in (None, ""):
        return (f"总格 {_compact_grid_cells(target)}", None)
    if range_text != "-":
        return (None, f"估总格 {range_text}")
    return (None, None)


def _floored_range_values(value: Any, floor: int | None) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    if floor is None or floor <= 0:
        return list(value[:3])
    out: list[Any] = []
    for item in value[:3]:
        parsed = _parse_int(item)
        out.append(max(parsed, floor) if parsed is not None else item)
    return out


def _maria_r1_low_tier_display_counts(
    ref_result: Any,
    quality_composition: Any,
) -> tuple[int, int] | None:
    """Return trusted Maria q1/q3 counts without invoking coercion protocols."""

    def _plain_dict(value: Any) -> dict[str, Any] | None:
        if type(value) is not dict:  # noqa: E721 - exact JSON object required
            return None
        if any(type(key) is not str for key in value):
            return None
        return value

    result = _plain_dict(ref_result)
    composition = _plain_dict(quality_composition)
    if result is None or composition is None:
        return None
    result_status = dict.get(result, "status")
    composition_status = dict.get(composition, "status")
    if (
        type(result_status) is not str  # noqa: E721 - exact scalar required
        or result_status not in {"ok", "count_prior"}
        or type(composition_status) is not str  # noqa: E721
        or composition_status not in {"ready", "exact"}
    ):
        return None

    evidence = _plain_dict(dict.get(result, "evidence"))
    if evidence is None:
        return None
    hero = dict.get(evidence, "hero")
    if type(hero) is not str or hero.strip().lower() != "maria":  # noqa: E721
        return None

    source_notes = dict.get(evidence, "source_notes")
    if type(source_notes) not in (list, tuple):
        return None
    if any(type(note) is not str for note in source_notes):
        return None
    required_notes = (
        "maria_skill_coarse_quality_min_counts",
        "maria_skill_coarse_quality_split_counts",
        "maria_skill_coarse_quality_source:maria_skill_10010801",
    )
    if any(note not in source_notes for note in required_notes):
        return None

    min_counts = _plain_dict(dict.get(evidence, "min_counts"))
    split_counts = _plain_dict(dict.get(evidence, "split_counts"))
    if min_counts is None or split_counts is None:
        return None

    def _count(container: dict[str, Any], key: str) -> int | None:
        value = dict.get(container, key)
        if type(value) is not int:  # noqa: E721 - bool/subclasses are invalid
            return None
        return value if value >= 0 else None

    white = _count(split_counts, "white")
    green = _count(split_counts, "green")
    q1 = _count(min_counts, "q1")
    q3 = _count(min_counts, "q3")
    if white is None or green is None or q1 is None or q3 is None:
        return None
    if white + green != q1:
        return None
    return q1, q3


def _maria_r1_low_tier_composition_views(
    quality_composition: Any,
    *,
    joint_only: bool,
) -> dict[str, Any] | None:
    """Parse Maria's q1/q3 branches independently through the canonical view."""

    if type(quality_composition) is not dict:  # noqa: E721 - exact JSON object
        return None
    if any(type(key) is not str for key in quality_composition):
        return None
    status = dict.get(quality_composition, "status")
    qualities = dict.get(quality_composition, "qualities")
    if (
        type(status) is not str  # noqa: E721 - exact scalar required
        or status not in {"ready", "exact"}
        or type(qualities) is not dict  # noqa: E721 - exact JSON object
        or any(type(key) is not str for key in qualities)
    ):
        return None

    parsed: dict[str, Any] = {}
    for key in LOW_QUALITY_DISPLAY_ORDER:
        if key not in qualities:
            continue
        raw_quality = dict.get(qualities, key)
        if type(raw_quality) is dict:  # noqa: E721
            if any(type(raw_key) is not str for raw_key in raw_quality):
                return None
            raw_quality = dict.copy(raw_quality)
            # A single-tier Maria compatibility view cannot validate the
            # root-owned five-quality arm matrix.  Preserve the current schema
            # generation while explicitly marking the detached tier as
            # correlation-unknown; never downgrade it to an unmarked legacy row.
            raw_quality["compact_count_candidates"] = []
            raw_quality["compact_count_triplet"] = []
            raw_quality["count_correlation_state"] = "independent"
            raw_quality["count_correlation_note"] = ""
        if joint_only and type(raw_quality) is dict:  # noqa: E721
            # These marginals are optional in the canonical contract and are
            # independently rounded by the server projector.  They must not
            # erase otherwise valid joint candidates for this count-known row.
            dict.pop(raw_quality, "count_candidates", None)
            dict.pop(raw_quality, "cells_candidates", None)
        tier_view = detail_composition_view(
            {
                "quality_composition": {
                    "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
                    "status": status,
                    "qualities": {key: raw_quality},
                }
            }
        )
        parsed[key] = tier_view.quality(key)
    return parsed


def _quality_uncertainty_summary(
    ref_result: Any,
    *,
    quality_composition: Any = None,
    count_floors: dict[str, int] | None = None,
    display_order: tuple[str, ...] = LOW_QUALITY_DISPLAY_ORDER,
) -> str:
    def _plain_child(owner: dict[str, Any], key: str) -> dict[str, Any]:
        value = dict.get(owner, key)
        if type(value) is not dict:  # noqa: E721 - exact JSON object required
            return {}
        if any(type(raw_key) is not str for raw_key in value):
            return {}
        return value

    # Reject an untrusted mapping subclass before any get/iter/truth/string
    # protocol can execute.  This formatter is a UI boundary, so malformed
    # top-level input has one deterministic empty-state result.
    if type(ref_result) is not dict:  # noqa: E721 - exact JSON object required
        return "-"
    if any(type(raw_key) is not str for raw_key in ref_result):
        return "-"

    ranges = _plain_child(ref_result, "quality_count_ranges")
    cells_ranges = _plain_child(ref_result, "quality_cells_ranges")
    raw_evidence = dict.get(ref_result, "evidence")
    if raw_evidence is None:
        evidence: dict[str, Any] = {}
    elif type(raw_evidence) is not dict:  # noqa: E721
        return "-"
    elif any(type(raw_key) is not str for raw_key in raw_evidence):
        return "-"
    else:
        evidence = raw_evidence
    min_counts = _plain_child(evidence, "min_counts")
    composition_view = detail_composition_view(
        {"quality_composition": quality_composition}
    )

    def _strict_quality(key: str):
        quality = composition_view.quality(key)
        return quality if quality_is_strict_exact(quality) else None

    def _strict_tier_text(key: str) -> str | None:
        quality = _strict_quality(key)
        if quality is None:
            return None
        joint = quality.candidates[0]
        return f"{QUALITY_LABELS.get(key, key)}{joint.count}件/{joint.cells}格"

    def _composition_tier_ready(key: str, maria_views: Any) -> bool:
        if type(maria_views) is not dict:  # noqa: E721 - internal plain map
            return False
        quality = dict.get(maria_views, key)
        return (
            quality is not None
            and quality.status == "ready"
            and bool(quality.candidates)
        )

    # 艾莎早轮按轮次揭示低品(q1=白绿 / q3=蓝),把观察、已锁与未锁并到一行:
    #   R1: 白已揭示(split_counts['white']),但 q1 合并档仍含未知绿，故白只写「已见」；
    #       蓝只有 canonical q3 joint strict exact 才可写「已锁」。
    #   R2: 白绿(q1)已锁,蓝(q3)仍未锁
    #       → 「已锁 白绿N件/X格 未锁蓝品 <件数range> [格子range格]」(未锁蓝带引擎当回合件数+格子估计)
    #   R3+/全锁/早于R1(白未揭示)/非艾莎 → 落到下面原逻辑。
    # 注:本函数只处理 q1/q3(LOW_QUALITY_DISPLAY_ORDER);紫(q4)/金(q5)/红(q6)不进入低品件行,
    # 故 R1 即便提前揭示紫色也不会影响这里的 R1/R2 判定与文案(紫走各自中/高品行)。
    fixed_counts = _plain_child(evidence, "fixed_counts")
    split_counts = _plain_child(evidence, "split_counts")
    split_cells = _plain_child(evidence, "split_quality_cells")
    raw_hero = dict.get(evidence, "hero")
    if raw_hero is not None and type(raw_hero) is not str:
        evidence = dict.copy(evidence)
        dict.pop(evidence, "hero", None)
    is_aisha = type(raw_hero) is str and raw_hero.strip().lower() == "aisha"
    is_maria = type(raw_hero) is str and raw_hero.strip().lower() == "maria"
    maria_counts = _maria_r1_low_tier_display_counts(
        ref_result,
        quality_composition,
    )
    maria_full_views = (
        _maria_r1_low_tier_composition_views(
            quality_composition,
            joint_only=False,
        )
        if maria_counts is not None
        else None
    )
    maria_joint_views = (
        _maria_r1_low_tier_composition_views(
            quality_composition,
            joint_only=True,
        )
        if maria_counts is not None
        else None
    )
    if is_maria and maria_counts is None:
        # Invalid Maria branch-only evidence must not fall into the legacy
        # coercive floor path and invoke scalar/container protocols.
        min_counts = {}
        split_counts = {}

    # Maria's R1 inventory reveals every low-tier item's quality, so q1 (white+green)
    # and q3 (blue) counts are known before any outline/cell evidence exists.  Keep that
    # observed count evidence visible without pretending that the engine or grid totals
    # are already locked.
    if (
        is_maria
        and os.environ.get(MARIA_R1_LOW_TIER_COUNT_DISPLAY_DISABLE) != "1"
        and maria_counts is not None
        and _composition_tier_ready("q1", maria_joint_views)
        and _composition_tier_ready("q3", maria_joint_views)
        and type(maria_full_views) is dict  # noqa: E721 - internal plain map
        and not any(
            quality_is_strict_exact(dict.get(maria_full_views, key))
            for key in LOW_QUALITY_DISPLAY_ORDER
        )
    ):
        maria_q1, maria_q3 = maria_counts
        return (
            f"清点已知 白绿{maria_q1}件 蓝{maria_q3}件"
            " · 格数待全库透视"
        )

    def _floor_for(key: str) -> int | None:
        floor = _parse_int(min_counts.get(key)) if isinstance(min_counts, dict) else None
        if count_floors and key in count_floors:
            explicit = _parse_int(count_floors.get(key))
            if explicit is not None:
                floor = max(floor or 0, explicit)
        return floor

    white_n = _parse_int(split_counts.get("white"))
    if (
        is_aisha
        and "q1" in display_order
        and _parse_int(fixed_counts.get("q1")) is None  # 白绿合并档未锁(绿未知)
        and white_n is not None
        and white_n > 0
    ):
        # R1: 已见白品 + canonical strict 蓝(如有) + 未锁绿[、蓝]。
        white_cells = _parse_int(split_cells.get("white"))
        white_text = f"白品{white_n}件/{white_cells}格" if white_cells is not None else f"白品{white_n}件"
        locked_blue = (
            _strict_tier_text("q3") if "q3" in display_order else None
        )
        unlocked_names = ["绿"]  # 绿 = q1(白绿)未揭示半
        if "q3" in display_order and locked_blue is None:
            unlocked_names.append("蓝")
        parts = [f"已见 {white_text}"]
        if locked_blue is not None:
            parts.append(f"已锁 {locked_blue}")
        parts.append(f"未锁 {'、'.join(unlocked_names)}")
        return " · ".join(parts)
    q1_strict_text = _strict_tier_text("q1")
    q3_strict = _strict_quality("q3")
    if (
        is_aisha
        and "q1" in display_order
        and "q3" in display_order
        and q1_strict_text is not None
        and q3_strict is None
    ):
        # R2: 已锁白绿(件/格) + 未锁蓝品(带引擎当回合蓝件数+格子估计)
        locked_text = q1_strict_text
        blue_text = _range_text(_floored_range_values(ranges.get("q3"), _floor_for("q3"))).replace(" / ", "/")
        blue_cells_text = _ethan_cells_text(cells_ranges.get("q3"))
        if blue_text == "-" and blue_cells_text == "-":
            return f"已锁 {locked_text} 未锁蓝品"
        if blue_text == "-":
            return f"已锁 {locked_text} 未锁蓝品 {blue_cells_text}格"
        if blue_cells_text == "-":
            return f"已锁 {locked_text} 未锁蓝品 {blue_text}"
        return f"已锁 {locked_text} 未锁蓝品 {blue_text} {blue_cells_text}格"
    unlocked: list[str] = []
    locked: list[str] = []
    for key in display_order:
        strict_text = _strict_tier_text(key)
        if strict_text is not None:
            locked.append(strict_text)
            continue
        floor = _parse_int(min_counts.get(key)) if isinstance(min_counts, dict) else None
        if count_floors and key in count_floors:
            explicit_floor = _parse_int(count_floors.get(key))
            if explicit_floor is not None:
                floor = max(floor or 0, explicit_floor)
        range_values = _floored_range_values(ranges.get(key), floor)
        text = _range_text(range_values).replace(" / ", "/")
        if text == "-":
            continue
        collapsed_value = _locked_range_value(range_values)
        if collapsed_value is not None:
            # A collapsed display range remains only a compact value hint.  The
            # leading 未锁 comes from the canonical joint state above.
            text = str(collapsed_value)
        label = QUALITY_LABELS.get(key, key)
        # 伊森专属:低品(白绿/蓝)range 模式也带格子范围。range只提供值，
        # 是否锁定仍完全由上面的 canonical joint strict predicate 决定。
        if _is_ethan_hero(evidence):
            cells_text = _ethan_cells_text(cells_ranges.get(key))
            if cells_text != "-":
                unlocked.append(f"{label}{text} {cells_text}格")
                continue
        unlocked.append(f"{label}{text}")
    locked_text = ""
    unlocked_text = ""
    if locked:
        suffix = f" 等{len(locked)}项" if len(locked) > 4 else ""
        locked_text = "已锁 " + " ".join(locked[:4]) + suffix
    if unlocked:
        suffix = f" 等{len(unlocked)}项" if len(unlocked) > 3 else ""
        unlocked_text = "未锁 " + " ".join(unlocked[:3]) + suffix
    if locked_text and unlocked_text:
        return f"{locked_text} · {unlocked_text}"
    if unlocked_text:
        return unlocked_text
    if locked_text:
        return locked_text
    return "-"


def _compact_range_text(values: Any, *, suffix: str = "") -> str:
    text = _range_text(values, suffix=suffix)
    return text.replace(" / ", "/") if text != "-" else text


def _display_range_text(values: Any) -> str:
    locked = _locked_range_value(values)
    if locked is not None:
        return _money(locked)
    return _range_text(values)


def _red_range_text(values: Any) -> str:
    """Keep red item/grid columns visible; do not collapse locked triplets."""
    return _range_text(values)


def _range_triplet_ints(values: Any) -> list[int] | None:
    if not isinstance(values, (list, tuple)) or len(values) < 3:
        return None
    parsed = [_parse_int(value) for value in values[:3]]
    if any(value is None for value in parsed):
        return None
    return [int(value) for value in parsed]


def _paired_complement_range(
    total: int | None,
    base_range: Any,
    existing_range: Any,
) -> list[int] | None:
    if total is None or total < 0:
        return None
    base_values = _range_triplet_ints(base_range)
    if base_values is None:
        return None
    # Red is the complement of gold over the q5+q6 total: red = total - q5. The base (q5) range is
    # ascending so `total - value` comes out DESCENDING -- this is INTENTIONAL: when only gold+red
    # remain they trade off (more gold <-> fewer red), and displaying red inverse to gold lets the
    # user read "this much gold pairs with this much red" directly. Do not sort it ascending.
    paired = [total - value for value in base_values]
    if any(value < 0 for value in paired):
        return None
    existing_values = _range_triplet_ints(existing_range)
    if existing_values is not None and sorted(existing_values) != sorted(paired):
        # BUG-D (2026-07-10, 5610/5607/5602): the engine red range can transiently carry a value the
        # count identity forbids (5610 live: red [3,6,6] while gold [2,2,4] over total 8 -> red must
        # be {6,4}; the illegal 3 lived ~1.5min mid-round, unreproducible from exported evidence on
        # HEAD or the 0.2.6 package engine). This guard used to bail out -> the inverse gold<->red
        # pairing display silently vanished (作者: "没有逆序"). The pairing total only exists when
        # EVERYTHING except gold+red is locked (_infer_q5q6_count_total), so red = total - gold is an
        # ARITHMETIC IDENTITY — clamping the display to the paired complement is unconditionally
        # correct and repairs the transient. Kill: BIDKING_DISABLE_RED_PAIRED_CLAMP=1.
        if os.environ.get("BIDKING_DISABLE_RED_PAIRED_CLAMP") == "1":
            return None
    return paired


def _permutation_to_match_values(source: list[int], target: list[int]) -> list[int] | None:
    if len(source) != len(target):
        return None
    used: set[int] = set()
    out: list[int] = []
    for value in target:
        match = None
        for index, candidate in enumerate(source):
            if index in used:
                continue
            if candidate == value:
                match = index
                break
        if match is None:
            return None
        used.add(match)
        out.append(match)
    return out


def _valid_count_cells_pairs(counts: list[int], cells: list[int]) -> bool:
    if len(counts) != len(cells):
        return False
    for count, cell in zip(counts, cells):
        if count < 0 or cell < 0:
            return False
        if count == 0 and cell != 0:
            return False
        if count > 0 and cell < count:
            return False
    return True


CandidateCountSupportState = Literal["safe", "escape", "invalid"]


def _strict_candidate_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _candidate_count_support_state(
    ref_result: dict[str, Any],
    quality: str,
) -> CandidateCountSupportState:
    """Classify candidate metadata without treating malformed payloads as safe."""
    ranges = ref_result.get("quality_count_ranges")
    if not isinstance(ranges, Mapping):
        return "invalid"
    display = _range_triplet_ints(ranges.get(quality))
    if display is None:
        return "invalid"
    if "quality_count_candidates" not in ref_result:
        return "safe"
    candidates_by_quality = ref_result.get("quality_count_candidates")
    if not isinstance(candidates_by_quality, Mapping):
        return "invalid"
    if quality not in candidates_by_quality:
        return "safe"
    candidates = candidates_by_quality.get(quality)
    if not isinstance(candidates, list) or not candidates:
        return "invalid"
    lo, hi = min(display), max(display)
    for row in candidates:
        if not isinstance(row, Mapping) or "value" not in row:
            return "invalid"
        value = _strict_candidate_count(row.get("value"))
        if value is None or value < 0:
            return "invalid"
        if value < lo or value > hi:
            return "escape"
    return "safe"


def _paired_total_locked_count(
    ref_result: dict[str, Any],
    quality: str,
) -> int | None:
    ranges = ref_result.get("quality_count_ranges")
    if not isinstance(ranges, dict):
        return None
    value = _locked_range_value(ranges.get(quality))
    if value is None:
        return None
    # A percentile triplet can collapse even though the source support remains
    # probabilistic.  Do not promote it into a hard arithmetic complement: that
    # can pair an exact-looking red count with the raw zero-red cells arm.
    # Old/synthetic payloads without either metadata field keep the established
    # fallback. Once either field exists, partial or malformed metadata is not
    # evidence of an exact arithmetic lock.
    # Narrow rollback: BIDKING_DISABLE_RED_PAIRED_SOURCE_LOCK_GUARD=1.
    if os.environ.get("BIDKING_DISABLE_RED_PAIRED_SOURCE_LOCK_GUARD") != "1":
        metadata_present = (
            "quality_lock" in ref_result
            or "quality_count_candidates" in ref_result
        )
        if metadata_present:
            locks = ref_result.get("quality_lock")
            if not isinstance(locks, Mapping):
                return None
            lock = locks.get(quality)
            if not isinstance(lock, Mapping) or lock.get("strength") != "exact":
                return None
            if _candidate_count_support_state(ref_result, quality) != "safe":
                return None
    return value


def _align_cells_to_count_display(
    display_counts: Any,
    original_counts: Any,
    original_cells: Any,
) -> list[int] | None:
    counts = _range_triplet_ints(display_counts)
    cells = _range_triplet_ints(original_cells)
    source_counts = _range_triplet_ints(original_counts)
    if counts is None or cells is None:
        return None
    if source_counts is not None:
        permutation = _permutation_to_match_values(source_counts, counts)
        if permutation is not None:
            reordered = [cells[index] for index in permutation]
            if _valid_count_cells_pairs(counts, reordered):
                return reordered
    if _valid_count_cells_pairs(counts, cells):
        return cells
    for candidate in dict.fromkeys(permutations(cells, 3)):
        reordered = list(candidate)
        if _valid_count_cells_pairs(counts, reordered):
            return reordered
    return None


def _infer_q5q6_count_total(ref_result: dict[str, Any]) -> int | None:
    ranges = ref_result.get("quality_count_ranges")
    if not isinstance(ranges, dict):
        return None
    evidence = ref_result.get("evidence") if isinstance(ref_result.get("evidence"), dict) else {}
    count_sums = evidence.get("count_sums") if isinstance(evidence.get("count_sums"), dict) else {}
    q456_total = _parse_int(count_sums.get("q4q5q6"))
    q4_count = _paired_total_locked_count(ref_result, "q4")
    if q456_total is not None and q4_count is not None:
        total = q456_total - q4_count
        return total if total >= 0 else None

    total_count = _parse_int(evidence.get("total_count"))
    locked_known = [
        _paired_total_locked_count(ref_result, key)
        for key in ("q1", "q3", "q4")
    ]
    if total_count is not None and all(value is not None for value in locked_known):
        total = total_count - sum(int(value) for value in locked_known if value is not None)
        return total if total >= 0 else None
    return None


def _red_display_ranges(ref_result: dict[str, Any]) -> tuple[Any, Any]:
    # The main card is a quote-policy view: all three red triplets permanently keep
    # conservative / balanced / aggressive order.  Gold/red correlation remains in
    # the structured candidates and hover detail; it must never reorder the policy
    # columns or make a conservative quote appear paired with an aggressive red arm.
    return ref_result.get("red_count_range"), ref_result.get("red_cells_range")


def _red_display_value_range(
    ref_result: dict[str, Any],
    display_counts: Any = None,
) -> Any:
    """Return red value in the same fixed quote-policy order as the price cards."""
    _ = display_counts
    return ref_result.get("red_value_range")


def _summed_quality_count_candidates(ref_result: dict[str, Any]) -> list[int]:
    ranges = ref_result.get("quality_count_ranges")
    if not isinstance(ranges, dict):
        return []
    candidates: list[int] = []
    for index in range(3):
        total = 0
        for key in QUALITY_DISPLAY_ORDER:
            values = ranges.get(key)
            if not isinstance(values, (list, tuple)) or len(values) <= index:
                return []
            parsed = _parse_int(values[index])
            if parsed is None:
                return []
            total += parsed
        candidates.append(total)
    return candidates


def _candidate_summary(
    ref_result: dict[str, Any],
    *,
    settled_total_cells: Any = None,
) -> str:
    evidence = ref_result.get("evidence")
    if not isinstance(evidence, dict):
        return "-"
    parts: list[str] = []
    total_count = evidence.get("total_count")
    if total_count not in (None, ""):
        parts.append(f"总件 {total_count}")
    else:
        count_candidates = _summed_quality_count_candidates(ref_result)
        if count_candidates:
            parts.append(f"估总件 {_compact_range_text(count_candidates)}")
    # At settlement the true cells are known: show them (consistent with the truth item count
    # already displayed) instead of the retained pre-settlement grid ESTIMATE. The estimate-vs-actual
    # comparison lives in the 估价/结算/差值 value card; the candidate row should read as truth here.
    settled_cells = _parse_int(settled_total_cells)
    if settled_cells is not None:
        parts.append(f"总格 {settled_cells}")
    else:
        locked_grid, estimated_grid = _grid_summary_parts(ref_result, evidence)
        if locked_grid:
            parts.append(locked_grid)
        elif estimated_grid:
            parts.append(estimated_grid)
    return " · ".join(parts) if parts else "-"


def _range_is_unlocked(values: Any, floor: int | None = None) -> bool:
    range_values = _floored_range_values(values, floor)
    parsed = [_parse_int(value) for value in range_values]
    known = [value for value in parsed if value is not None]
    return len(known) >= 2 and len(set(known)) > 1


def _q6_grid_needs_direct_info(ref_result: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if evidence.get("total_grid_target") not in (None, ""):
        return False
    quality_cells = evidence.get("quality_cells")
    if isinstance(quality_cells, dict) and quality_cells.get("q6") not in (None, ""):
        return False
    avg_cells = evidence.get("avg_cells")
    if isinstance(avg_cells, dict) and avg_cells.get("q6") not in (None, ""):
        return False
    ranges = ref_result.get("quality_count_ranges")
    if not isinstance(ranges, dict):
        return False
    return _locked_range_value(ranges.get("q6")) is not None


def _unlocked_quality_labels(
    ref_result: dict[str, Any],
    evidence: dict[str, Any],
    quality_order: tuple[str, ...],
) -> list[str]:
    ranges = ref_result.get("quality_count_ranges")
    if not isinstance(ranges, dict):
        return []
    min_counts = evidence.get("min_counts") if isinstance(evidence.get("min_counts"), dict) else {}
    labels: list[str] = []
    for key in quality_order:
        floor = _parse_int(min_counts.get(key))
        if _range_is_unlocked(ranges.get(key), floor):
            labels.append(QUALITY_LABELS.get(key, key))
    return labels


def _evidence_exact_quality_field(
    evidence: dict[str, Any],
    bucket: str,
    quality_key: str,
) -> bool:
    payload = evidence.get(bucket)
    if not isinstance(payload, dict):
        return False
    return payload.get(quality_key) not in (None, "")


def _aisha_tier_needs_tool_info(
    ref_result: dict[str, Any],
    evidence: dict[str, Any],
    quality_key: str,
) -> bool:
    has_count = _evidence_exact_quality_field(evidence, "fixed_counts", quality_key)
    has_cells = _evidence_exact_quality_field(evidence, "quality_cells", quality_key)
    has_avg = _evidence_exact_quality_field(evidence, "avg_cells", quality_key)
    if quality_key == "q5" and has_count and (has_cells or has_avg):
        return False
    if quality_key in {"q3", "q4"} and has_count and (has_cells or has_avg):
        return False
    ranges = ref_result.get("quality_count_ranges")
    min_counts = evidence.get("min_counts") if isinstance(evidence.get("min_counts"), dict) else {}
    floor = _parse_int(min_counts.get(quality_key))
    if isinstance(ranges, dict) and _range_is_unlocked(ranges.get(quality_key), floor):
        return True
    if has_count or (isinstance(ranges, dict) and not _range_is_unlocked(ranges.get(quality_key), floor)):
        if quality_key == "q5":
            return not (has_cells or has_avg)
        return not (has_cells or has_avg)
    return True


def _aisha_green_needs_info(ref_result: dict[str, Any], evidence: dict[str, Any]) -> bool:
    """Green (q1/白绿) is low value, so treat it as known once its count is locked.

    Unlike blue/purple/gold, we do not also require cells/avg for green: waiting on
    green cells would otherwise keep it pinned in the hint for the whole match.
    """
    if _evidence_exact_quality_field(evidence, "fixed_counts", "q1"):
        return False
    ranges = ref_result.get("quality_count_ranges")
    if not isinstance(ranges, dict) or ranges.get("q1") in (None, ""):
        return False
    min_counts = evidence.get("min_counts") if isinstance(evidence.get("min_counts"), dict) else {}
    floor = _parse_int(min_counts.get("q1"))
    return _range_is_unlocked(ranges.get("q1"), floor)


def _aisha_missing_total_count(ref_result: dict[str, Any], evidence: dict[str, Any]) -> bool:
    status = _text(ref_result.get("status"), "")
    if status == "missing_total_count":
        return True
    return evidence.get("total_count") in (None, "")


AISHA_LIVE_ENGINE_MIN_ROUND = 3  # R1–R2 use lighter combo caps via audit_aisha_early_round
AISHA_DUAL_PASS_MAX_ROUND = 5
AISHA_HERO_ID = 103
AISHA_ENGINE_PASS_SKILL = "skill"
AISHA_ENGINE_PASS_ITEM = "item"
AISHA_QUOTE_PASS_PUBLIC = "public"
# One-arm rollback: restore the historical R1 skill-frame wait without changing later rounds.
AISHA_DISABLE_R1_PUBLIC_PROVISIONAL = "AISHA_DISABLE_R1_PUBLIC_PROVISIONAL"
_AISHA_PUBLIC_NUMERIC_INFO_IDS = frozenset(
    {
        200009,
        200010,
        200011,
        200012,
        200013,
        200014,
        200015,
        200016,
        200017,
        200018,
        200019,
        200020,
        200031,
        200032,
        200033,
        200034,
        200035,
        200036,
        200037,
        200038,
        200044,
    }
)
_AISHA_PUBLIC_PRESENCE_INFO_IDS = frozenset(
    {200001, 200002, 200003, 200004, 200028, 200030, 200048}
)
# Later skill-only passes still own the quote, but an item whose identity/value (or q6 footprint
# minimum) was already disclosed is permanent hard evidence. Roll back only this carry-forward with
# BIDKING_DISABLE_AISHA_PERSISTENT_ITEM_FLOORS=1.
AISHA_PERSISTENT_ITEM_FLOORS_DISABLE = (
    "BIDKING_DISABLE_AISHA_PERSISTENT_ITEM_FLOORS"
)
# When skill pass already consumed most of the live refresh budget, skip the heavier item pass.
AISHA_ITEM_PASS_SKILL_BUDGET_MS = 3500.0
AISHA_ITEM_PASS_COMBO_CAP_SKILL_BUDGET_MS = 2000.0
# Early-round (R1/R2) large-vault latency guard. On a big vault the public total grid is pinned, so
# the estimate is cap-INSENSITIVE (measured: balanced ±3.4% across combo 100→2500, grid/gold already
# pinned), yet FILLING 2500 combos over R1's wide count-prior takes up to ~14s and stalls the live
# overlay (the skill pass has no time budget). Lower the cap ONLY when the public total cells is large;
# mid/small vaults are cap-SENSITIVE (+20% at cap 300) and KEEP the full cap. Deterministic (vault-size
# gated, not wall-clock). Live-only (this caller path); batch/eval/calibration go through the engine
# directly and are unaffected. Rollback: AISHA_DISABLE_EARLY_LARGE_VAULT_CAP=1.
# See memory aisha-early-round-enumeration-slow-large-vault.
AISHA_EARLY_LARGE_VAULT_CELLS = 140        # public total grid >= this => large flat regime
AISHA_EARLY_LARGE_VAULT_MAX_COMBOS = 300   # measured ~2s on a 156-cell vault, balanced delta <2%


def _aisha_formal_calculator_ready(
    hero_key: str,
    round_no: int | None,
    phase: str,
) -> bool:
    """Aisha uses the same quote gate as other heroes; early-round uncertainty is via count_prior + defense hint."""
    del hero_key, round_no, phase
    return True


def _snapshot_live_evidence_stub(
    snapshot: dict[str, Any],
    *,
    constraints: dict[str, Any],
) -> dict[str, Any]:
    ref_inputs: dict[str, Any] = {}
    for candidate in (
        snapshot.get("structured_ref_inputs"),
        constraints.get("structured_ref_inputs"),
    ):
        if isinstance(candidate, dict):
            ref_inputs.update(candidate)
    fixed_counts = {
        key: int(value)
        for key, value in (ref_inputs.get("fixed_counts") or {}).items()
        if _parse_int(value) is not None
    }
    quality_cells = {}
    for key, value in (ref_inputs.get("quality_cells") or {}).items():
        try:
            if value not in (None, ""):
                quality_cells[key] = float(value)
        except (TypeError, ValueError):
            continue
    avg_cells = {}
    for key, value in (ref_inputs.get("avg_cells") or {}).items():
        try:
            if value not in (None, ""):
                avg_cells[key] = float(value)
        except (TypeError, ValueError):
            continue
    for key, value in _quality_counts_from_text(snapshot.get("final_quality_counts")).items():
        fixed_counts.setdefault(key, int(value))
    for key, value in _quality_counts_from_text(snapshot.get("final_quality_cells")).items():
        quality_cells.setdefault(key, float(value))
    total_count = _parse_int(
        ref_inputs.get("total_count")
        or constraints.get("total_count")
        or snapshot.get("inventory_count")
    )
    total_grid = _parse_int(
        ref_inputs.get("total_cells")
        or constraints.get("total_cells")
        or snapshot.get("inventory_cells")
    )
    return {
        "hero": "aisha",
        "total_count": total_count,
        "total_grid_target": total_grid,
        "fixed_counts": fixed_counts,
        "quality_cells": quality_cells,
        "avg_cells": avg_cells,
        "min_counts": {},
    }


# Live monitor: R1–R4 outline skills; R5 has no dedicated skill frame.
AISHA_ROUND_SKILL_IDS: dict[int, int] = {
    1: 1001034,
    2: 1001033,
    3: 1001032,
    4: 1001031,
}
AISHA_ROUND_SKILL_LABELS: dict[int, str] = {
    1: "白品技能帧",
    2: "绿品技能帧",
    3: "蓝品技能帧",
    4: "紫品技能帧",
}
# Player prop frames for item-pass detection (engine mapping optional).
# Keep in sync with monitor zero_implied_action_ids + ahmad_ref_engine ACTION_* tables.
AISHA_PROP_ACTION_IDS: frozenset[str] = frozenset(
    {
        str(action_id)
        for action_id in (
            100103,
            100104,
            100105,
            100106,
            100107,
            100108,
            100109,
            100110,
            100111,
            100112,
            100113,
            100114,
            100115,
            100116,
            100117,
            100118,
            100119,
            100120,
            100121,
            100122,
            100123,
            100124,
            100125,
            100126,
            100127,
            100134,
            100135,
            100136,
            100137,
            100138,
            100139,
            100140,
            100163,
            100204,
            1002041,
            1002042,
            1002043,
            1002044,
        )
    }
)
AISHA_SKILL_ACTION_IDS: frozenset[str] = frozenset(
    str(skill_id) for skill_id in AISHA_ROUND_SKILL_IDS.values()
)

AISHA_DEFENSE_MULTIPLIERS: dict[int, str] = {
    1: "2.0",
    2: "1.6",
    3: "1.3",
    4: "1.1",
    5: "1.1",
}


def _aisha_defense_multiplier_hint(round_no: int | None) -> str:
    """Product-only round defense reference; not applied inside ref_v0."""
    if round_no is None or round_no < 1:
        return ""
    clamped = min(int(round_no), 5)
    multiplier = AISHA_DEFENSE_MULTIPLIERS.get(clamped, "1.1")
    return f"R{clamped}防守×{multiplier}"


def _infer_hero_from_skill_reveals(snapshot: dict[str, Any]) -> str:
    """Backfill hero when monitor session binding lags but skill frames already landed."""
    for row in _iter_snapshot_skill_reveal_rows(snapshot):
        hero_id = _parse_int(row.get("hero_id"))
        if hero_id in HERO_BY_ID:
            return HERO_BY_ID[hero_id]
    return ""


def _snapshot_hero_key(snapshot: dict[str, Any], context: dict[str, Any]) -> str:
    hero = _hero_from_context(
        context.get("hero") or snapshot.get("hero"),
        context.get("hero_id"),
        context.get("player_hero_id"),
        context.get("current_player_hero_id"),
        snapshot.get("hero_id"),
        snapshot.get("player_hero_id"),
        snapshot.get("current_player_hero_id"),
    )
    if _is_unknown_hero(hero):
        structured = snapshot.get("structured_ref_inputs")
        if isinstance(structured, dict):
            structured_hero = _hero_from_context(structured.get("hero"))
            if not _is_unknown_hero(structured_hero):
                hero = structured_hero
    if _is_unknown_hero(hero):
        inferred = _infer_hero_from_skill_reveals(snapshot)
        if inferred:
            hero = inferred
    return normalize_hero_key(hero)


def _aisha_should_dual_pass(
    hero_key: str,
    round_no: int | None,
    phase: str,
) -> bool:
    """Aisha R1–R5: skill-frame pass then optional item-frame pass."""
    return hero_uses_dual_pass(hero_key, round_no, phase)


def _aisha_pass_max_combos(round_no: int, prepared: dict[str, Any] | None = None) -> int:
    base = hero_max_combos_for_round("aisha", round_no)
    # Early-round large-vault latency guard (see constants above). Only fires for R1/R2 on a large
    # pinned-grid vault; otherwise returns the normal cap. `prepared` carries the public total cells.
    if prepared is None or int(round_no) >= AISHA_LIVE_ENGINE_MIN_ROUND:
        return base
    if os.environ.get("AISHA_DISABLE_EARLY_LARGE_VAULT_CAP") == "1":
        return base
    constraints = prepared.get("inference_input_constraints")
    cell_obj = constraints.get("warehouse_total_cells") if isinstance(constraints, dict) else None
    value = cell_obj.get("value") if isinstance(cell_obj, dict) else None
    try:
        total_cells = float(value)
    except (TypeError, ValueError):
        return base
    if total_cells >= AISHA_EARLY_LARGE_VAULT_CELLS:
        return min(base, AISHA_EARLY_LARGE_VAULT_MAX_COMBOS)
    return base


def _iter_snapshot_skill_reveal_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for key in ("skill_reveal_rows", "skill_reveals"):
        payload = snapshot.get(key)
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict):
                continue
            row_id = id(row)
            if row_id in seen:
                continue
            seen.add(row_id)
            rows.append(row)
    return rows


def _skill_reveal_row_has_signal(row: dict[str, Any]) -> bool:
    revealed = _parse_int(row.get("revealed_items"))
    if revealed is not None and revealed > 0:
        return True
    result_val = row.get("result")
    if result_val not in (None, ""):
        try:
            if float(result_val) != 0.0:
                return True
        except (TypeError, ValueError):
            pass
    items = row.get("observed_items") or row.get("revealed_items_detail") or ()
    if isinstance(items, list) and items:
        return True
    return False


def _aisha_round_skill_frame_ready(snapshot: dict[str, Any], round_no: int) -> bool:
    expected_skill = AISHA_ROUND_SKILL_IDS.get(int(round_no))
    if expected_skill is None:
        return True
    for row in _iter_snapshot_skill_reveal_rows(snapshot):
        if _parse_int(row.get("hero_id")) != AISHA_HERO_ID:
            continue
        if _parse_int(row.get("skill_id")) != expected_skill:
            continue
        if _skill_reveal_row_has_signal(row):
            return True
    return False


def _public_info_row_has_signal(row: dict[str, Any]) -> bool:
    if _parse_int(row.get("info_id")) not in (None, 0):
        return True
    if _skill_reveal_row_has_signal(row):
        return True
    summary = _text(row.get("revealed_summary") or row.get("summary"), "").strip()
    return bool(summary)


def _aisha_round_public_info_ready(
    snapshot: dict[str, Any],
    round_no: int,
) -> bool:
    """Public info landing at or after the current round skill anchor."""
    floor = _aisha_round_skill_sort(snapshot, round_no)
    rows = snapshot.get("public_info_rows")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict) or not _public_info_row_has_signal(row):
            continue
        row_sort = _action_row_sort(row)
        if floor is not None:
            if row_sort is None or row_sort < floor:
                continue
        return True
    return False


def _aisha_public_info_ready(
    snapshot: dict[str, Any],
    public_info: dict[str, Any],
) -> bool:
    """Any public info in snapshot (cumulative); used for diagnostics only."""
    rows = snapshot.get("public_info_rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and _public_info_row_has_signal(row):
                return True
    if isinstance(public_info.get("public_numeric_facts"), list) and public_info["public_numeric_facts"]:
        return True
    if _text(public_info.get("public_numeric_summary"), "").strip():
        return True
    input_constraints = public_info.get("input_constraints")
    if isinstance(input_constraints, dict) and input_constraints:
        return True
    return False


def _aisha_r1_builtin_number(value: Any, *, integer: bool) -> float | None:
    """Validate a monitor-parsed scalar without accepting coercible lookalikes."""

    if type(value) is int:  # noqa: E721 - bool/subclasses are not parser scalars
        return float(value) if value >= 0 else None
    if not integer and type(value) is float:  # noqa: E721
        return value if math.isfinite(value) and value >= 0.0 else None
    return None


def _aisha_r1_mapping_is_partial(value: dict[str, Any]) -> bool:
    if any(
        value.get(key) is True
        for key in ("estimated", "is_estimated", "placeholder", "is_placeholder", "partial")
    ):
        return True
    for key in ("confidence", "provenance", "source", "status"):
        raw = value.get(key)
        if raw is None:
            continue
        if type(raw) is not str:  # noqa: E721 - exact JSON scalar boundary
            return True
        lowered = raw.lower()
        if any(marker in lowered for marker in ("estimate", "approx", "placeholder", "partial")):
            return True
    return False


def _aisha_r1_scope_scalar(
    snapshot: dict[str, Any],
    context: dict[str, Any],
    key: str,
) -> tuple[bool, str | int | None]:
    """Select one exact scope scalar without invoking user protocols.

    A missing context key may fall back to the snapshot.  An explicitly
    present value must itself be canonical, and two present aliases must agree;
    malformed values never become falsey fallbacks.
    """

    values: list[str | int] = []
    for container in (context, snapshot):
        if not dict.__contains__(container, key):
            continue
        raw = dict.get(container, key)
        if key == "session_id":
            if type(raw) is not str:  # noqa: E721 - exact JSON scalar boundary
                return False, None
            canonical: str | int = raw.strip()
            if not canonical:
                return False, None
        else:
            if type(raw) is not int:  # noqa: E721 - bool/subclasses excluded
                return False, None
            canonical = raw
            if (key == "map_id" and canonical < 0) or (
                key == "round" and canonical <= 0
            ):
                return False, None
        if values and canonical != values[0]:
            return False, None
        values.append(canonical)
    if not values:
        return False, None
    return True, values[0]


def _aisha_r1_current_public_row_ready(snapshot: dict[str, Any]) -> bool:
    """Require one valid targeted row while ignoring safe unrelated siblings.

    The producer row is a protocol boundary.  Target recognition therefore
    uses exact builtin scalars only; numeric-equal wrong types are malformed,
    not unrelated.  Scope aliases and duplicate target values form one atomic
    ledger so an unrelated public row cannot erase a verified count, while a
    conflicting targeted row cannot become a last-write winner.
    """

    if type(snapshot) is not dict:  # noqa: E721 - protocol boundary
        return False
    if not dict.__contains__(snapshot, "ui_contract"):
        ui_contract = {}
    else:
        ui_contract = dict.get(snapshot, "ui_contract")
        if type(ui_contract) is not dict:  # noqa: E721
            return False
    if not dict.__contains__(ui_contract, "context"):
        context = {}
    else:
        context = dict.get(ui_contract, "context")
        if type(context) is not dict:  # noqa: E721
            return False
    session_ok, current_session = _aisha_r1_scope_scalar(
        snapshot,
        context,
        "session_id",
    )
    map_ok, current_map = _aisha_r1_scope_scalar(
        snapshot,
        context,
        "map_id",
    )
    round_ok, current_round = _aisha_r1_scope_scalar(
        snapshot,
        context,
        "round",
    )
    if (
        not session_ok
        or not map_ok
        or not round_ok
        or type(current_session) is not str  # noqa: E721
        or type(current_map) is not int  # noqa: E721
        or type(current_round) is not int  # noqa: E721
    ):
        return False

    rows = snapshot.get("public_info_rows")
    if type(rows) is not list or not rows:  # noqa: E721
        return False
    target_ids = _AISHA_PUBLIC_NUMERIC_INFO_IDS | _AISHA_PUBLIC_PRESENCE_INFO_IDS
    target_text_ids = frozenset(str(info_id) for info_id in target_ids)
    integer_ids = frozenset(
        {
            200009,
            200010,
            200011,
            200012,
            200017,
            200018,
            200019,
            200020,
            200044,
        }
    )
    seen: dict[int, tuple[float | None, int | None]] = {}
    for row in rows:
        if type(row) is not dict:  # noqa: E721 - never inspect mapping subclasses
            return False
        info_id = dict.get(row, "info_id")
        if type(info_id) is int:  # noqa: E721 - bool excluded
            if info_id not in target_ids:
                continue
        elif type(info_id) is str:  # noqa: E721
            if info_id.strip() in target_text_ids:
                return False
            continue
        elif type(info_id) is float:  # noqa: E721
            if (
                math.isfinite(info_id)
                and info_id.is_integer()
                and str(int(info_id)) in target_text_ids
            ):
                return False
            continue
        else:
            # Unknown protocol-bearing identifiers cannot be proven unrelated
            # without executing user code, so fail the public lane closed.
            return False
        if _aisha_r1_mapping_is_partial(row):
            return False
        row_session = row.get("session_id")
        if row_session is not None and (
            type(row_session) is not str  # noqa: E721
            or row_session.strip() != current_session
        ):
            return False
        row_map = row.get("map_id")
        if row_map is not None and (
            type(row_map) is not int or row_map != current_map  # noqa: E721
        ):
            return False
        row_sorts = [
            dict.get(row, key)
            for key in ("sort", "sort_id")
            if dict.__contains__(row, key)
        ]
        if (
            not row_sorts
            or any(type(value) is not int or value < 0 for value in row_sorts)  # noqa: E721
            or len(set(row_sorts)) != 1
        ):
            return False
        row_rounds = [
            dict.get(row, key)
            for key in ("round", "round_no", "action_round")
            if dict.__contains__(row, key)
        ]
        if row_rounds and (
            any(type(value) is not int for value in row_rounds)  # noqa: E721
            or len(set(row_rounds)) != 1
            or row_rounds[0] != current_round
        ):
            return False

        value = None
        if "value" in row:
            value = _aisha_r1_builtin_number(
                row.get("value"),
                integer=info_id in integer_ids,
            )
            if value is None:
                return False
        revealed_items = None
        if "revealed_items" in row:
            parsed_revealed = _aisha_r1_builtin_number(
                row.get("revealed_items"),
                integer=True,
            )
            if parsed_revealed is None:
                return False
            revealed_items = int(parsed_revealed)

        if info_id in _AISHA_PUBLIC_NUMERIC_INFO_IDS:
            if value is None:
                return False
        elif info_id in _AISHA_PUBLIC_PRESENCE_INFO_IDS:
            details = row.get("revealed_items_detail")
            if details is not None and (
                type(details) is not list  # noqa: E721
                or any(type(item) is not dict for item in details)  # noqa: E721
            ):
                return False
            summary = row.get("revealed_summary") or row.get("summary")
            if summary is not None and type(summary) is not str:  # noqa: E721
                return False
            if not (
                value is not None
                or (revealed_items is not None and revealed_items > 0)
                or (type(details) is list and bool(details))  # noqa: E721
                or (type(summary) is str and bool(summary.strip()))  # noqa: E721
            ):
                return False
        else:
            return False

        fact = (value, revealed_items)
        previous = seen.get(info_id)
        if previous is not None and previous != fact:
            return False
        seen[info_id] = fact
    return bool(seen)


def _aisha_r1_quality_key(value: Any) -> str | None:
    if type(value) is not str:  # noqa: E721
        return None
    normalized = value.strip().lower()
    aliases = {"1": "q1", "2": "q2", "3": "q3", "4": "q4", "5": "q5", "6": "q6"}
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"q1", "q2", "q3", "q4", "q5", "q6"} else None


def _aisha_r1_count_sum_key(value: Any) -> str | None:
    if type(value) is not str:  # noqa: E721 - exact JSON object key only
        return None
    normalized = value.strip().lower().replace("+", "")
    return {
        "q4q5": "q4q5",
        "45": "q4q5",
        "q4q5q6": "q4q5q6",
        "456": "q4q5q6",
    }.get(normalized)


def _aisha_r1_record_structured_fact(
    facts: dict[str, float],
    key: str,
    value: float,
) -> bool:
    previous = facts.get(key)
    if previous is not None and abs(previous - value) > 0.000001:
        return False
    facts[key] = value
    return True


def _aisha_r1_field_update_fact(row: dict[str, Any]) -> tuple[str, float] | None:
    path = row.get("path") or row.get("field")
    if type(path) not in (list, tuple) or not path:  # noqa: E721
        return None
    if any(type(part) is not str for part in path):  # noqa: E721
        return None
    parts = tuple(part.strip().lower() for part in path)
    key = ""
    integer = False
    if len(parts) >= 2 and parts[0] == "session":
        if parts[1] in {"total_count", "total_item_count"}:
            key, integer = "total_count", True
        elif parts[1] in {"total_cells", "warehouse_total_cells"}:
            key = "total_cells"
    elif len(parts) >= 3 and parts[0] == "bucket":
        quality = _aisha_r1_quality_key(parts[1])
        field = parts[2]
        if quality is None:
            return None
        if field == "count":
            key, integer = f"count.{quality}", True
        elif field in {"cells", "total_cells"}:
            key = f"cells.{quality}"
        elif field == "avg_cells":
            key = f"avg_cells.{quality}"
        elif field in {"avg_value", "average_value"}:
            key = f"avg_value.{quality}"
        elif field in {"value", "value_sum", "total_value"}:
            key = f"value.{quality}"
    elif len(parts) >= 3 and parts[0] == "bucket_group":
        group = _aisha_r1_count_sum_key(parts[1])
        if group is not None and parts[2] == "count":
            key, integer = f"count_sum.{group}", True
    elif len(parts) >= 3 and parts[0] == "bucket_split":
        split = parts[1]
        if split not in {"white", "green"}:
            return None
        if parts[2] == "count":
            key, integer = f"split_count.{split}", True
        elif parts[2] in {"cells", "total_cells"}:
            key = f"split_cells.{split}"
        elif parts[2] == "avg_cells":
            key = f"split_avg_cells.{split}"
    if not key:
        return None
    parsed = _aisha_r1_builtin_number(row.get("value"), integer=integer)
    return (key, parsed) if parsed is not None else None


def _aisha_r1_structured_candidate_facts(
    candidate: dict[str, Any],
    *,
    current_session: str,
    current_round: int,
    facts: dict[str, float],
) -> tuple[bool, bool]:
    """Return (valid, has_scalar), rejecting partial/conflicting candidate evidence."""

    if _aisha_r1_mapping_is_partial(candidate):
        return False, False
    hero = candidate.get("hero")
    if hero is not None and (
        type(hero) is not str  # noqa: E721
        or normalize_hero_key(hero) != "aisha"
    ):
        return False, False
    source = candidate.get("source")
    if source is not None and type(source) is not str:  # noqa: E721
        return False, False
    candidate_session = candidate.get("session_id")
    if candidate_session is not None and (
        type(candidate_session) is not str  # noqa: E721
        or candidate_session.strip() != current_session
    ):
        return False, False
    candidate_round = candidate.get("round")
    if candidate_round is not None and (
        type(candidate_round) is not int  # noqa: E721
        or candidate_round != current_round
    ):
        return False, False

    has_scalar = False
    scalar_specs = {
        "total_count": ("total_count", True),
        "total_item_count": ("total_count", True),
        "session_total_count": ("total_count", True),
        "session_total_item_count": ("total_count", True),
        "total_cells": ("total_cells", False),
        "total_grid": ("total_cells", False),
        "warehouse_total_cells": ("total_cells", False),
        "session_total_cells": ("total_cells", False),
    }
    for field, (key, integer) in scalar_specs.items():
        if field not in candidate or candidate[field] in (None, ""):
            continue
        parsed = _aisha_r1_builtin_number(candidate[field], integer=integer)
        if parsed is None or not _aisha_r1_record_structured_fact(facts, key, parsed):
            return False, False
        has_scalar = True

    mapping_specs = {
        "counts": ("count", "quality", True),
        "fixed_counts": ("count", "quality", True),
        "min_counts": ("min_count", "quality", True),
        "quality_cells": ("cells", "quality", False),
        "cells": ("cells", "quality", False),
        "avg_cells": ("avg_cells", "quality", False),
        "average_cells": ("avg_cells", "quality", False),
        "avg_values": ("avg_value", "quality", False),
        "average_values": ("avg_value", "quality", False),
        "quality_values": ("value", "quality", False),
        "value_sums": ("value", "quality", False),
        "values": ("value", "quality", False),
        "quality_value_floors": ("value_floor", "quality", False),
        "value_floors": ("value_floor", "quality", False),
        "count_sums": ("count_sum", "count_sum", True),
        "countSums": ("count_sum", "count_sum", True),
        "split_counts": ("split_count", "split", True),
        "low_quality_counts": ("split_count", "split", True),
        "split_quality_cells": ("split_cells", "split", False),
        "split_cells": ("split_cells", "split", False),
        "low_quality_cells": ("split_cells", "split", False),
        "split_avg_cells": ("split_avg_cells", "split", False),
        "low_quality_avg_cells": ("split_avg_cells", "split", False),
    }
    for field, (namespace, key_kind, integer) in mapping_specs.items():
        if field not in candidate:
            continue
        values = candidate[field]
        if type(values) is not dict:  # noqa: E721
            return False, False
        for raw_key, raw_value in values.items():
            if key_kind == "quality":
                normalized_key = _aisha_r1_quality_key(raw_key)
            elif key_kind == "split":
                normalized_key = raw_key if raw_key in {"white", "green"} else None
            else:
                normalized_key = _aisha_r1_count_sum_key(raw_key)
            parsed = _aisha_r1_builtin_number(raw_value, integer=integer)
            if (
                normalized_key is None
                or parsed is None
                or not _aisha_r1_record_structured_fact(
                    facts,
                    f"{namespace}.{normalized_key}",
                    parsed,
                )
            ):
                return False, False
            has_scalar = True

    if "field_updates" in candidate:
        updates = candidate["field_updates"]
        if type(updates) is not list:  # noqa: E721
            return False, False
        for row in updates:
            if type(row) is not dict or _aisha_r1_mapping_is_partial(row):  # noqa: E721
                return False, False
            row_session = row.get("session_id")
            if row_session is not None and (
                type(row_session) is not str  # noqa: E721
                or row_session.strip() != current_session
            ):
                return False, False
            row_round = row.get("round")
            if row_round is not None and (
                type(row_round) is not int or row_round != current_round  # noqa: E721
            ):
                return False, False
            sequence = row.get("sequence")
            if sequence is not None and (
                type(sequence) is not int or sequence < 0  # noqa: E721
            ):
                return False, False
            confidence = row.get("confidence")
            if confidence is not None and (
                type(confidence) is not str  # noqa: E721
                or confidence.lower() not in {"exact", "high"}
            ):
                return False, False
            update_fact = _aisha_r1_field_update_fact(row)
            if update_fact is None or not _aisha_r1_record_structured_fact(
                facts,
                *update_fact,
            ):
                return False, False
            has_scalar = True
    return True, has_scalar


def _aisha_structured_quote_input_ready(snapshot: dict[str, Any]) -> bool:
    """Require one parsed top-level scalar and reject all conflicting mirrors."""

    ui_contract = snapshot.get("ui_contract")
    context = ui_contract.get("context") if type(ui_contract) is dict else None  # noqa: E721
    constraints = ui_contract.get("constraints") if type(ui_contract) is dict else None  # noqa: E721
    if type(ui_contract) is not dict:  # noqa: E721
        ui_contract = {}
    if type(context) is not dict:  # noqa: E721
        context = {}
    current_session = context.get("session_id") or snapshot.get("session_id")
    current_round = context.get("round") or snapshot.get("round")
    if (
        type(current_session) is not str  # noqa: E721
        or not current_session.strip()
        or type(current_round) is not int  # noqa: E721
    ):
        return False

    top_level = snapshot.get("structured_ref_inputs")
    if type(top_level) is not dict:  # noqa: E721
        return False
    candidates: list[dict[str, Any]] = [top_level]
    seen = {id(top_level)}
    candidate_keys = (
        "structured_ref_inputs",
        "hero_ref_inputs",
        "aisha_ref_inputs",
    )
    for key in candidate_keys[1:]:
        if key not in snapshot:
            continue
        candidate = snapshot[key]
        if type(candidate) is not dict:  # noqa: E721
            return False
        if id(candidate) not in seen:
            candidates.append(candidate)
            seen.add(id(candidate))
    for container in (ui_contract, constraints):
        if container is None:
            continue
        if type(container) is not dict:  # noqa: E721
            return False
        for key in candidate_keys:
            if key not in container:
                continue
            candidate = container[key]
            if type(candidate) is not dict:  # noqa: E721
                return False
            if id(candidate) not in seen:
                candidates.append(candidate)
                seen.add(id(candidate))

    facts: dict[str, float] = {}
    top_level_has_scalar = False
    for index, candidate in enumerate(candidates):
        valid, has_scalar = _aisha_r1_structured_candidate_facts(
            candidate,
            current_session=current_session.strip(),
            current_round=current_round,
            facts=facts,
        )
        if not valid:
            return False
        if index == 0:
            top_level_has_scalar = has_scalar
    return top_level_has_scalar and bool(facts)


def _aisha_r1_public_provisional_ready(
    snapshot: dict[str, Any],
    round_no: int,
    public_info: dict[str, Any],
) -> bool:
    """Allow a marked quote only from current-row, conflict-free parsed evidence."""

    del public_info
    if int(round_no) != 1:
        return False
    if (
        os.environ.get(AISHA_DISABLE_R1_PUBLIC_PROVISIONAL) == "1"
        or os.environ.get("DRAW_HERO_DISABLE_PROVISIONAL_QUOTE") == "1"
    ):
        return False
    return _aisha_r1_current_public_row_ready(
        snapshot
    ) and _aisha_structured_quote_input_ready(snapshot)


def _aisha_early_round_quote_ready(
    snapshot: dict[str, Any],
    round_no: int,
    public_info: dict[str, Any],
) -> tuple[bool, str]:
    """Prefer the skill frame; strict current public evidence may open R1 provisionally."""
    skill_ready = _aisha_round_skill_frame_ready(snapshot, round_no)
    skill_label = AISHA_ROUND_SKILL_LABELS.get(int(round_no), "技能帧")
    if skill_ready or _aisha_r1_public_provisional_ready(snapshot, round_no, public_info):
        return True, ""
    return False, f"等待{skill_label}"


def _snapshot_action_result_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    top = snapshot.get("action_result_rows")
    if isinstance(top, list):
        rows.extend(row for row in top if isinstance(row, dict))
    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    actions = uc.get("actions") if isinstance(uc.get("actions"), dict) else {}
    for row in actions.get("results") or ():
        if isinstance(row, dict):
            rows.append(row)
    return rows


_ACTION_ROW_SORT_MAX = (1 << 63) - 1
_ACTION_ROW_SORT_TEXT_MAX = 32
_ACTION_ROW_SORT_JSON_CONFLICT = "__bidking_invalid_action_sort__"
_ACTION_ROW_SORT_MISSING = object()


def _stable_action_row_sort(value: Any) -> int | None:
    """Parse one scheduler sort scalar without invoking user protocols."""
    parsed: int
    if type(value) is int:
        parsed = value
    elif type(value) is float:
        if not math.isfinite(value):
            return None
        parsed = int(value)
    elif type(value) is str:
        if (
            len(value) > _ACTION_ROW_SORT_TEXT_MAX
            or not value.isascii()
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        ):
            return None
        text = value.strip(" ")
        if not text:
            return None
        digits = text.lstrip("+-")
        if digits.isdigit():
            parsed = int(text)
        else:
            try:
                numeric = float(text)
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(numeric):
                return None
            parsed = int(numeric)
    else:
        return None
    if parsed < 0 or parsed > _ACTION_ROW_SORT_MAX:
        return None
    return parsed


def _aisha_action_rows_for_json_clone(value: Any) -> list[dict[str, Any]]:
    """Project action rows without invoking untrusted sort/container protocols."""

    if type(value) is not list:  # noqa: E721 - wire container subclasses are unsafe
        return []
    projected: list[dict[str, Any]] = []
    for row in list.__iter__(value):
        if type(row) is not dict:  # noqa: E721 - direct path ignores non-dict rows
            continue
        stable_row = dict.copy(row)
        raw_sort = dict.get(row, "sort", _ACTION_ROW_SORT_MISSING)
        if (
            raw_sort is not _ACTION_ROW_SORT_MISSING
            and _stable_action_row_sort(raw_sort) is None
        ):
            # Missing sort keeps the historical scheduler fallback. A present invalid
            # sort must remain distinguishable, otherwise the JSON clone would turn a
            # direct conflict into a valid fallback row.
            stable_row["sort"] = _ACTION_ROW_SORT_JSON_CONFLICT
        projected.append(stable_row)
    return projected


def _aisha_snapshot_for_json_clone(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Copy both action-row bridges before the generic JSON clone."""

    if type(snapshot) is not dict:  # noqa: E721 - engine preparation returns exact dict
        return {}
    projected = dict.copy(snapshot)
    top_rows = dict.get(projected, "action_result_rows", _ACTION_ROW_SORT_MISSING)
    if top_rows is not _ACTION_ROW_SORT_MISSING:
        projected["action_result_rows"] = _aisha_action_rows_for_json_clone(top_rows)

    ui_contract = dict.get(projected, "ui_contract", _ACTION_ROW_SORT_MISSING)
    if type(ui_contract) is not dict:  # noqa: E721 - other UI projection owns this boundary
        return projected
    stable_ui_contract = dict.copy(ui_contract)
    projected["ui_contract"] = stable_ui_contract
    actions = dict.get(stable_ui_contract, "actions", _ACTION_ROW_SORT_MISSING)
    if type(actions) is not dict:  # noqa: E721 - malformed containers expose no rows
        return projected
    stable_actions = dict.copy(actions)
    stable_ui_contract["actions"] = stable_actions
    action_rows = dict.get(stable_actions, "results", _ACTION_ROW_SORT_MISSING)
    if action_rows is not _ACTION_ROW_SORT_MISSING:
        stable_actions["results"] = _aisha_action_rows_for_json_clone(action_rows)
    return projected


def _action_row_sort(row: dict[str, Any]) -> int | None:
    return _stable_action_row_sort(row.get("sort"))


def _aisha_round_skill_sort(snapshot: dict[str, Any], round_no: int) -> int | None:
    """Sort anchor for the current round; props at or after this sort belong to this round."""
    expected_skill = AISHA_ROUND_SKILL_IDS.get(int(round_no))
    if expected_skill is None and int(round_no) >= 5:
        expected_skill = AISHA_ROUND_SKILL_IDS.get(4)
    if expected_skill is None:
        return None
    anchor: int | None = None
    for row in _iter_snapshot_skill_reveal_rows(snapshot):
        if _parse_int(row.get("hero_id")) != AISHA_HERO_ID:
            continue
        if _parse_int(row.get("skill_id")) != expected_skill:
            continue
        row_sort = _action_row_sort(row)
        if row_sort is None:
            continue
        if anchor is None or row_sort > anchor:
            anchor = row_sort
    return anchor


def _aisha_is_prop_action_row(row: dict[str, Any]) -> bool:
    action_id = _text(row.get("action_id"), "").strip()
    if not action_id or action_id in AISHA_SKILL_ACTION_IDS:
        return False
    if action_id in AISHA_PROP_ACTION_IDS:
        return True
    if row.get("inferred_zero"):
        return True
    detail = row.get("revealed_items_detail") or row.get("observed_items") or ()
    return isinstance(detail, list) and bool(detail)


def _prop_action_row_has_signal(row: dict[str, Any]) -> bool:
    action_id = _text(row.get("action_id"), "").strip()
    if not action_id:
        return False
    if row.get("inferred_zero"):
        return True
    if action_id in AISHA_PROP_ACTION_IDS and (
        row.get("result") is not None or row.get("result_field") is not None
    ):
        return True
    if _skill_reveal_row_has_signal(row):
        return True
    items = row.get("revealed_items_detail") or row.get("observed_items") or ()
    return isinstance(items, list) and bool(items)


def _aisha_item_frame_ready(snapshot: dict[str, Any], round_no: int) -> bool:
    """Prop/item frame for the current round only; public info is handled by the skill pass."""
    floor = _aisha_round_skill_sort(snapshot, round_no)
    if floor is None:
        return False
    for row in _snapshot_action_result_rows(snapshot):
        if not _aisha_is_prop_action_row(row):
            continue
        row_rounds = [
            dict.get(row, key)
            for key in ("round", "round_no", "action_round")
            if dict.__contains__(row, key)
        ]
        if row_rounds and (
            any(type(value) is not int for value in row_rounds)  # noqa: E721
            or len(set(row_rounds)) != 1
            or row_rounds[0] != int(round_no)
        ):
            continue
        row_sort = _action_row_sort(row)
        if row_sort is not None and row_sort < floor:
            continue
        if _prop_action_row_has_signal(row):
            return True
    return False


def _aisha_persistent_action_floor_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return disclosed item facts that remain hard evidence in later skill passes."""
    if os.environ.get(AISHA_PERSISTENT_ITEM_FLOORS_DISABLE) == "1":
        return []
    raw_items = row.get("revealed_items_detail") or row.get("observed_items") or ()
    if not isinstance(raw_items, list):
        return []
    persistent: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        quality = _parse_int(item.get("quality"))
        priced_catalog_item = (
            item.get("item_id") not in (None, "")
            and (_parse_money(item.get("value")) or 0) > 0
        )
        has_footprint = (
            (_parse_int(item.get("cells")) or 0) > 0
            or item.get("shape_code") not in (None, "", 0, "0")
            or bool(str(item.get("shape_key") or "").strip())
        )
        # A complete red outline proves the cheapest catalog identity even when the exact item/value
        # is hidden. Other qualities need an actually disclosed catalog price to become a hard floor.
        if priced_catalog_item or (quality == 6 and has_footprint):
            persistent.append(json.loads(json.dumps(item, ensure_ascii=False)))
    return persistent


def _aisha_skill_pass_action_rows(rows: Any) -> list[dict[str, Any]]:
    """Keep persistent hard floors while omitting ordinary old prop estimates."""
    if not isinstance(rows, list):
        return []
    # A zero-result tool has no server result packet. The monitor can only mark it
    # inferred_zero after a later state arrives, which is commonly the next round's
    # skill frame. Dropping every action here would then lose a confirmed persistent
    # constraint (for example, q5 avg_cells=0 means exactly zero gold items). Ordinary
    # aggregate prop rows remain item-pass-only so old-round estimates do not contaminate the new
    # skill pass. Fully disclosed item prices and red catalog-footprint minima are persistent facts.
    kept: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _bool(row.get("inferred_zero")):
            kept.append(row)
            continue
        persistent_items = _aisha_persistent_action_floor_items(row)
        if not persistent_items:
            continue
        # Do not carry the old tool's aggregate result into a later skill pass. Only the disclosed
        # physical items persist, so a reversible aggregate/count hypothesis can still unlock.
        floor_row = {
            key: row[key]
            for key in ("sort", "time", "action_id", "tool", "source")
            if key in row
        }
        floor_row["revealed_items"] = len(persistent_items)
        floor_row["revealed_items_detail"] = persistent_items
        floor_row["persistent_item_floor_only"] = True
        kept.append(floor_row)
    return kept


def _aisha_clone_for_engine_pass(snapshot: dict[str, Any], pass_kind: str) -> dict[str, Any]:
    """Skill pass omits prop action rows only; full snapshot still feeds minimap UI."""
    if pass_kind != AISHA_ENGINE_PASS_SKILL:
        return snapshot
    stable_snapshot = prepare_reference_engine_snapshot(snapshot)
    clone_input = _aisha_snapshot_for_json_clone(stable_snapshot)
    cloned = json.loads(json.dumps(clone_input, ensure_ascii=False))
    cloned["action_result_rows"] = _aisha_skill_pass_action_rows(
        cloned.get("action_result_rows")
    )
    uc = cloned.get("ui_contract") if isinstance(cloned.get("ui_contract"), dict) else {}
    cloned["ui_contract"] = uc
    actions = uc.get("actions") if isinstance(uc.get("actions"), dict) else {}
    uc["actions"] = actions
    actions["results"] = _aisha_skill_pass_action_rows(actions.get("results"))
    return cloned


def _aisha_prepare_pass_snapshot(snapshot: dict[str, Any], *, pass_kind: str, round_no: int) -> dict[str, Any]:
    base = _aisha_clone_for_engine_pass(snapshot, pass_kind)
    prepared = prepare_reference_engine_snapshot(base)
    prepared["audit_aisha_engine_pass"] = pass_kind
    if int(round_no) < AISHA_LIVE_ENGINE_MIN_ROUND:
        prepared["audit_aisha_early_round"] = True
    return prepared


def _aisha_run_engine_pass(snapshot: dict[str, Any], *, pass_kind: str, round_no: int) -> dict[str, Any]:
    if run_reference_engine is None:
        return {"status": "unavailable", "source": "ref_v0", "notes": ["ref_engine_unavailable"]}
    prepared = _aisha_prepare_pass_snapshot(snapshot, pass_kind=pass_kind, round_no=round_no)
    try:
        return run_reference_engine(
            prepared,
            max_combos=_aisha_pass_max_combos(round_no, prepared),
        ).as_dict()
    except Exception as exc:  # noqa: BLE001 - prototype diagnostics
        return {
            "status": "error",
            "source": "ref_v0",
            "notes": [f"aisha_engine_pass:{pass_kind}", str(exc)],
        }


def _aisha_append_quote_pass_note(result: dict[str, Any], pass_kind: str) -> dict[str, Any]:
    notes = list(result.get("notes") or [])
    marker = f"aisha_quote_pass:{pass_kind}"
    if marker not in notes:
        notes.append(marker)
    result["notes"] = notes
    return result


def _aisha_append_skill_only_pass_notes(
    result: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    round_no: int,
) -> dict[str, Any]:
    result = _aisha_append_quote_pass_note(result, AISHA_ENGINE_PASS_SKILL)
    notes = list(result.get("notes") or [])
    if not _aisha_round_public_info_ready(snapshot, round_no):
        marker = "aisha_quote_pass:skill_no_public_this_round"
        if marker not in notes:
            notes.append(marker)
    if not _aisha_item_frame_ready(snapshot, round_no):
        marker = "aisha_quote_pass:skill_no_items_this_round"
        if marker not in notes:
            notes.append(marker)
    result["notes"] = notes
    return result


def _aisha_append_public_provisional_notes(
    result: dict[str, Any],
    *,
    wait_hint: str,
) -> dict[str, Any]:
    result = _aisha_append_quote_pass_note(result, AISHA_QUOTE_PASS_PUBLIC)
    notes = list(result.get("notes") or [])
    marker = "aisha_public_before_skill_provisional"
    if marker not in notes:
        notes.append(marker)
    if result.get("status") in {"ok", "count_prior"} and result.get("balanced") is not None:
        for note in ("hero_ref_provisional", f"hero_ref_wait:{wait_hint}"):
            if note not in notes:
                notes.append(note)
    result["notes"] = notes
    return result


def _aisha_skill_only_flag_detail(ref_notes: list[str]) -> str:
    no_items = "aisha_quote_pass:skill_no_items_this_round" in ref_notes
    no_public = "aisha_quote_pass:skill_no_public_this_round" in ref_notes
    if no_items and no_public:
        return "本轮无新公开信息、未用道具；仍按技能帧估价"
    if no_public:
        return "本轮无新公开信息；仍按技能帧估价"
    if no_items:
        return "本轮未用道具；下轮仍按技能帧估价"
    return "道具帧未到；小地图仍显示全部道具"


def _aisha_should_skip_item_pass_after_skill(
    skill_result: dict[str, Any],
    *,
    skill_ms: float | None,
) -> bool:
    if skill_ms is None:
        return False
    elapsed = float(skill_ms)
    if elapsed >= AISHA_ITEM_PASS_SKILL_BUDGET_MS:
        return True
    notes = tuple(str(item) for item in skill_result.get("notes") or ())
    return elapsed >= AISHA_ITEM_PASS_COMBO_CAP_SKILL_BUDGET_MS and "combo_cap_hit" in notes


def _aisha_current_round_item_must_apply(snapshot: dict[str, Any], round_no: int) -> bool:
    """A ready item frame is current-round hard evidence, not a deferred hint."""
    return _aisha_item_frame_ready(snapshot, round_no)


def _aisha_dual_pass_ref_result(
    snapshot: dict[str, Any],
    *,
    round_no: int,
    public_info: dict[str, Any],
    raw_readiness: tuple[bool, bool] | None = None,
) -> tuple[dict[str, Any], dict[str, float | None]]:
    """Run skill+public pass, then full item pass when prop frame lands."""
    if raw_readiness is None:
        raw_skill_ready = _aisha_round_skill_frame_ready(snapshot, round_no)
        raw_public_provisional = (
            not raw_skill_ready
            and _aisha_r1_public_provisional_ready(snapshot, round_no, public_info)
        )
    else:
        raw_skill_ready, raw_public_provisional = raw_readiness
    snapshot = prepare_reference_engine_snapshot(snapshot)
    timing: dict[str, float | None] = {"skill": None, "item": None}
    skill_ready = raw_skill_ready or _aisha_round_skill_frame_ready(snapshot, round_no)
    public_provisional = (
        raw_public_provisional
        and not skill_ready
        and _aisha_r1_public_provisional_ready(snapshot, round_no, public_info)
    )
    if not skill_ready and not public_provisional:
        skill_label = AISHA_ROUND_SKILL_LABELS.get(int(round_no), "技能帧")
        wait_hint = f"等待{skill_label}"
        return _aisha_early_round_waiting_result(wait_hint=wait_hint), timing

    skill_started = time.perf_counter()
    skill_result = _aisha_run_engine_pass(
        snapshot,
        pass_kind=AISHA_ENGINE_PASS_SKILL,
        round_no=round_no,
    )
    timing["skill"] = round((time.perf_counter() - skill_started) * 1000.0, 2)

    if public_provisional:
        skill_label = AISHA_ROUND_SKILL_LABELS.get(int(round_no), "技能帧")
        return _aisha_append_public_provisional_notes(
            skill_result,
            wait_hint=f"等待{skill_label}",
        ), timing

    if not _aisha_item_frame_ready(snapshot, round_no):
        return _aisha_append_skill_only_pass_notes(
            skill_result,
            snapshot=snapshot,
            round_no=round_no,
        ), timing

    # A current-round item frame is hard evidence.  Its pass must not be
    # deferred merely because the preceding skill pass consumed the budget.
    if (
        not _aisha_current_round_item_must_apply(snapshot, round_no)
        and _aisha_should_skip_item_pass_after_skill(skill_result, skill_ms=timing["skill"])
    ):
        reason = (
            "skill_budget"
            if float(timing["skill"] or 0.0) >= AISHA_ITEM_PASS_SKILL_BUDGET_MS
            else "combo_cap_budget"
        )
        skipped = _aisha_append_quote_pass_note(dict(skill_result), AISHA_ENGINE_PASS_SKILL)
        notes = list(skipped.get("notes") or [])
        marker = f"aisha_item_pass_skipped:{reason}"
        if marker not in notes:
            notes.append(marker)
        if timing["skill"] is not None:
            budget_note = f"aisha_item_pass_skipped_skill_ms:{float(timing['skill']):g}"
            if budget_note not in notes:
                notes.append(budget_note)
        skipped["notes"] = notes
        return skipped, timing

    item_started = time.perf_counter()
    item_result = _aisha_run_engine_pass(
        snapshot,
        pass_kind=AISHA_ENGINE_PASS_ITEM,
        round_no=round_no,
    )
    timing["item"] = round((time.perf_counter() - item_started) * 1000.0, 2)
    return _aisha_append_quote_pass_note(item_result, AISHA_ENGINE_PASS_ITEM), timing


def _hero_single_pass_ref_result(
    snapshot: dict[str, Any],
    *,
    hero_key: str,
    round_no: int,
    public_info: dict[str, Any],
) -> dict[str, Any]:
    """Scheduled single-pass ref for Ahmed/Victor/sparse heroes."""
    if run_reference_engine is None:
        return {"status": "unavailable", "source": "ref_v0", "notes": ["ref_engine_unavailable"]}
    ready, wait_hint = hero_quote_ready(snapshot, hero_key, round_no, public_info)
    map_id = snapshot.get('map_id',snapshot.get('ui_contract',{}).get('context',{}).get('map_id'))
    if type(map_id) in (int,str) and map_id in (2511,4511,'2511','4511'):
        # Let the engine publish either its calibrated quote or its precise
        # source/input rejection; a scheduling wait must not hide that result.
        ready = True
    prepared = prepare_reference_engine_snapshot(snapshot)
    spec_boundary = 3
    try:
        from hero_ref_live_schedule import get_hero_schedule

        spec = get_hero_schedule(hero_key)
        if spec is not None:
            spec_boundary = spec.early_round_boundary
    except Exception:  # noqa: BLE001
        pass
    if int(round_no) < spec_boundary:
        prepared[EARLY_ROUND_ENGINE_FLAG] = True
    if not ready:
        # Method A (2026-06-25): instead of a blank no-quote while the round's scheduled reveal is
        # pending (e.g. sophie R2 waits for the cross-round quality skill 1001072 — gated in ~30/34
        # sophie games, gabriela never), surface the engine's PRIOR-BASED estimate as an explicitly
        # PROVISIONAL quote. The library sweep showed this decision-moment estimate is shaky (truth
        # bracketed only ~43%, 参考 bias median -12% but range [-75%,+86%], count rarely locked) — so
        # it is tagged `hero_ref_provisional` for the UI to badge "暂估·待品质", NOT shown as firm.
        # Better than blank (a -12%-median ballpark beats nothing); only when the engine actually
        # yields a usable count_prior/ok estimate, else stay blank. SCOPED TO DRAW HEROES only
        # (sophie/gabriela/…): they have a count-from-map prior worth showing; signalled heroes
        # (ahmed/victor/…) intentionally wait blank without skill/public (no meaningful prior) — see
        # test_ahmad_server_ahmed_waits_without_skill_or_public. Revert: DRAW_HERO_DISABLE_PROVISIONAL_QUOTE=1.
        normalized_hero = normalize_hero_key(hero_key)
        if (
            os.environ.get("DRAW_HERO_DISABLE_PROVISIONAL_QUOTE") == "1"
            or normalized_hero not in PROVISIONAL_COUNT_PRIOR_HERO_KEYS
            or (normalized_hero == "maria" and int(round_no) != 1)
        ):
            return hero_ref_waiting_result(hero_key=hero_key, wait_hint=wait_hint)
        try:
            prov = run_reference_engine(
                prepared, max_combos=hero_max_combos_for_round(hero_key, round_no)
            ).as_dict()
        except Exception:  # noqa: BLE001 - fall back to blank on any engine failure
            return hero_ref_waiting_result(hero_key=hero_key, wait_hint=wait_hint)
        if prov.get("status") in ("ok", "count_prior") and prov.get("balanced") is not None:
            prov["notes"] = list(prov.get("notes") or ()) + [
                "hero_ref_provisional",
                f"hero_ref_wait:{wait_hint}",
            ]
            return prov
        return hero_ref_waiting_result(hero_key=hero_key, wait_hint=wait_hint)
    try:
        return run_reference_engine(
            prepared,
            max_combos=hero_max_combos_for_round(hero_key, round_no),
        ).as_dict()
    except Exception as exc:  # noqa: BLE001 - prototype diagnostics
        return {
            "status": "error",
            "source": "ref_v0",
            "notes": [f"hero_ref_pass:{hero_key}", str(exc)],
        }


def _aisha_quote_pass_kind(ref_notes: list[str]) -> str:
    for note in ref_notes:
        text = str(note)
        if text.startswith("aisha_quote_pass:"):
            return text.split(":", 1)[1]
    return ""


def _aisha_early_round_waiting_result(*, wait_hint: str) -> dict[str, Any]:
    return {
        "status": "missing_total_count",
        "source": "ref_v0",
        "conservative": None,
        "balanced": None,
        "aggressive": None,
        "value_p25": None,
        "value_p50": None,
        "value_p75": None,
        "combo_count": 0,
        "red_count_range": [None, None, None],
        "red_cells_range": [None, None, None],
        "red_value_range": [None, None, None],
        "quality_count_ranges": {},
        "quality_cells_ranges": {},
        "total_grid_range": [None, None, None],
        "notes": ["aisha_early_round_waiting", f"aisha_early_wait:{wait_hint}"],
        "evidence": {"hero": "aisha"},
    }


def _aisha_early_wait_hint(ref_notes: list[str]) -> str:
    return hero_ref_wait_hint(ref_notes)


AISHA_D1_FLAG_DISCOUNT_THRESHOLD = 0.7


def _parse_aisha_d1_discount(note: str) -> float | None:
    """Parse the suggested discount from notes like aisha_d1_shadow_q6_discount=0.62@r3."""
    if "=" not in note:
        return None
    tail = note.split("=", 1)[1]
    value = tail.split("@", 1)[0].strip()
    try:
        return float(value)
    except ValueError:
        return None


def _aisha_d1_flag_detail(ref_notes: list[str]) -> str:
    """Surface red-weight reference only when meaningful, to avoid per-round flag spam.

    apply notes always surface (they change the bid); shadow notes only when the
    suggested discount is below the threshold (most rows emit a near-1.0 shadow note).
    """
    relevant: list[str] = []
    for note in ref_notes:
        text = str(note)
        is_apply = "aisha_d1_apply" in text
        is_shadow = "aisha_d1_shadow" in text
        if not (is_apply or is_shadow):
            continue
        if is_apply:
            relevant.append(text)
            continue
        discount = _parse_aisha_d1_discount(text)
        if discount is not None and discount < AISHA_D1_FLAG_DISCOUNT_THRESHOLD:
            relevant.append(text)
    return "; ".join(relevant[:2])


def _aisha_next_info_hint(
    ref_result: dict[str, Any],
    evidence: dict[str, Any],
    *,
    round_no: int | None = None,
) -> str:
    # Frame Aisha's next step as the info still worth waiting for, instead of a
    # generic "open this tool" prompt. Show the two lowest quality tiers that are
    # still unknown (green < blue < purple < gold); once <=1 quality tier remains
    # open, fold in the outstanding total-cells / total-count needs. Red (q6) is
    # not a waited tier here because it arrives via public / villa reveals.
    quality_tiers = (("q3", "蓝"), ("q4", "紫"), ("q5", "金"))
    unknown: list[str] = []
    if _aisha_green_needs_info(ref_result, evidence):
        unknown.append("绿")
    unknown.extend(
        label
        for key, label in quality_tiers
        if _aisha_tier_needs_tool_info(ref_result, evidence, key)
    )
    total_grid_range = ref_result.get("total_grid_range")
    # A prior-derived steering center is not a measurement.  Wait for total cells
    # whenever the public range remains open and provenance does not classify the
    # target as hard; a collapsed model range still counts as effectively known.
    grid_missing = not _hard_grid_target_locked(evidence) and _range_is_unlocked(total_grid_range)
    total_bits: list[str] = []
    if grid_missing:
        total_bits.append("总格")
    if _aisha_missing_total_count(ref_result, evidence):
        total_bits.append("总件")
    total_text = "、".join(total_bits)

    if len(unknown) >= 2:
        return f"等待{unknown[0]}品和{unknown[1]}品信息"
    if len(unknown) == 1:
        if total_text:
            # Gold (q5) is the top scannable tier and cannot be back-solved from
            # totals, so it is needed together with them ("和"). A lower tier can
            # be derived once the totals + higher tiers are known, so either path
            # helps ("或").
            connector = "和" if unknown[0] == "金" else "信息或"
            return f"等待{unknown[0]}品{connector}{total_text}信息"
        return f"等待{unknown[0]}品信息"
    if total_text:
        return f"等待{total_text}信息"
    return "信息已足够，观察出价"


# Ethan only ever obtains a tier's CELLS (格子数), never its 件数 — unlike Aisha who can lock count.
# So coverage is cell-info-based, NOT count-based.
_ETHAN_CELL_BUCKETS = ("quality_cells", "avg_cells")  # 总格(扫描) / 均格 — 白绿/蓝/金 hard-lock via these
# 紫 is the ONE soft exception (author): it cannot be hard-locked — Ethan usually only 估价s it, so ANY
# of 总价/均价/总格/均格/件数 is enough to switch away (the 紫金件 display marks it 未完全锁定). Gold is
# NOT soft: it reconciles with 总格/总件 so it hard-locks, and is treated like 白绿/蓝 (cell info only).
_ETHAN_PURPLE_BUCKETS = ("quality_cells", "avg_cells", "quality_values", "avg_values", "fixed_counts")


def _ethan_tier_covered(evidence: dict[str, Any], quality_key: str) -> bool:
    """A tier is 'covered' (stop waiting / switch to the next gap) once it has the relevant signal.

    白绿(q1)/蓝(q3)/金(q5): hard-lock tiers — covered only by CELL info (扫描/均格). Never by count
    (Ethan cannot get per-tier 件数). 紫(q4): the soft exception — covered by ANY of 总价/均价/总格/均格/
    件数, since it is usually only 估价d (approximate); the display flags it 未完全锁定."""
    buckets = _ETHAN_PURPLE_BUCKETS if quality_key == "q4" else _ETHAN_CELL_BUCKETS
    return any(_evidence_exact_quality_field(evidence, bucket, quality_key) for bucket in buckets)


def _ethan_next_info_hint(
    ref_result: dict[str, Any],
    evidence: dict[str, Any],
    *,
    round_no: int | None = None,
) -> str:
    """Ethan 下一步: Aisha-style "等待X品信息" framing (no prop names, no per-tier 件数).

    Ethan reads OUTLINES only; per-quality CELLS come from the 鉴格流 (白绿/蓝 scan-lockable
    cheaply; 紫/金 usually via 估价 = approximate, which still counts as covered). Walk the tiers
    in 鉴格流 order 白绿→蓝→紫→金 (red excluded — arrives via outline), surface the first two still
    uncovered, then fold in 总格 (总仓储). Total COUNT is never actively waited on: Ethan cannot
    lock it; it resolves automatically at R5 full-outline, after which "信息已足够，观察出价".
    """
    tiers = (("q1", "白绿"), ("q3", "蓝"), ("q4", "紫"), ("q5", "金"))
    unknown = [label for key, label in tiers if not _ethan_tier_covered(evidence, key)]
    grid_missing = evidence.get("total_grid_target") in (None, "") and _range_is_unlocked(
        ref_result.get("total_grid_range")
    )
    total_text = "总格" if grid_missing else ""
    if len(unknown) >= 2:
        return f"等待{unknown[0]}品和{unknown[1]}品信息"
    if len(unknown) == 1:
        if total_text:
            # 金 (top scannable tier) cannot be back-solved from totals, so needed together ("和");
            # a lower tier can be derived once totals + higher tiers are known ("信息或").
            connector = "和" if unknown[0] == "金" else "信息或"
            return f"等待{unknown[0]}品{connector}{total_text}信息"
        return f"等待{unknown[0]}品信息"
    if total_text:
        return f"等待{total_text}信息"
    return "信息已足够，观察出价"


def _ref_notes_list(ref_result: dict[str, Any]) -> list[str]:
    notes = ref_result.get("notes")
    if isinstance(notes, str):
        return [part.strip() for part in notes.split(";") if part.strip()]
    if isinstance(notes, (list, tuple)):
        return [str(part) for part in notes if str(part).strip()]
    return []


def _activity_2511_provisional_notice(ref_result: dict[str, Any]) -> str:
    metadata = ref_result.get("provisional_quote")
    if (
        type(metadata) is dict
        and metadata.get("schema") == "activity_2511_provisional_b049_v1"
        and metadata.get("map_id") == 2511
        and metadata.get("empirically_calibrated") is False
        and ref_result.get("balanced") is not None
    ):
        return "暂估价 · 本周掉率未校准"
    return ""


def _wuqilin_5215_provisional_notice(ref_result: dict[str, Any]) -> str:
    metadata = ref_result.get("provisional_quote")
    if (type(metadata) is dict and metadata.get("schema") == "wuqilin_5215_peer_conditioned_v1"
        and metadata.get("map_id") == 5215 and ref_result.get("balanced") is not None):
        return "算法暂估"
    return ""


def _wuqilin_5215_joint_projection(ref_result: Any) -> dict[str, Any]:
    """Only the dedicated model's same-posterior joint receipt enables this lane."""
    if type(ref_result) is not dict or dict.get(ref_result,'source') != 'wuqilin_5215_peer_conditioned_v1':
        return {}
    metadata=dict.get(ref_result,'provisional_quote')
    if (type(metadata) is not dict or dict.get(metadata,'schema')!='wuqilin_5215_peer_conditioned_v1'
        or dict.get(metadata,'map_id')!=5215 or dict.get(metadata,'model_map_id')!=5215):
        return {}
    display=dict.get(metadata,'quality_display')
    if (type(display) is not dict or dict.get(display,'schema')!='wuqilin_5215_joint_posterior_v1'
        or dict.get(display,'posterior')!='same_surviving_particles_and_weights'):
        return {}
    return display


def activity_2511_provisional_quote_revoked(snapshot: dict[str, Any], summary: dict[str, Any]) -> bool:
    ref = summary.get("ahmed_ref")
    if type(ref) is dict and ref.get('source') in {
            'auction_hall_6001_shadow', 'auction_hall_6001_slotted_algorithm_036_v1'}:
        from auction_hall_6001_quote_model import load_model, MODEL_VERSION, GENERATION, INPUTS, ModelInputError
        from ahmad_ref_engine import (ROOT, _aisha_q5_total_cells_plaintext_bytes,
            _current_rankmap_distribution_generation_status, _auction_hall_6001_current_scope_state)
        metadata = ref.get('provisional_quote')
        if (ref.get('source') != MODEL_VERSION or type(metadata) is not dict
                or metadata.get('schema') != MODEL_VERSION or metadata.get('generation') != GENERATION
                or metadata.get('map_id') != 6001 or metadata.get('model_map_id') != 6001
                or metadata.get('input_sha256') != {path: pin[1] for path, pin in INPUTS.items()}
                or _auction_hall_6001_current_scope_state(snapshot) != 'ok'
                or _current_rankmap_distribution_generation_status()[0] != 'ok'):
            return True
        try:
            load_model(lambda path: _aisha_q5_total_cells_plaintext_bytes(ROOT / path))
        except ModelInputError:
            return True
        return False
    calibrated=ref.get('empirical_calibration') if type(ref) is dict else None
    if type(calibrated) is dict and calibrated.get('schema') in {'shipwreck_calibrated_b049_v1', 'shipwreck_calibrated_transfer_036_v1'}:
        from ahmad_ref_engine import CURRENT_RANKMAP_PACKAGE_GENERATION, _shipwreck_calibrated_scope_state
        map_id=calibrated.get('map_id')
        return (calibrated.get('generation') != CURRENT_RANKMAP_PACKAGE_GENERATION
                or type(map_id) is not int or map_id not in (2511,4511)
                or _shipwreck_calibrated_scope_state(snapshot,map_id)!='ok')
    if type(ref) is dict and (ref.get("source") == "wuqilin_5215_peer_conditioned_v1"
                             or _wuqilin_5215_provisional_notice(ref)):
        from wuqilin_5215_quote_model import scope_state, load_model, input_profile, GENERATION, MODEL_VERSION, ModelInputError
        from ahmad_ref_engine import ROOT, CURRENT_RANKMAP_PACKAGE_GENERATION, _aisha_q5_total_cells_plaintext_bytes
        metadata = ref.get("provisional_quote")
        if (ref.get("source") != MODEL_VERSION or type(metadata) is not dict
                or metadata.get("schema") != MODEL_VERSION
                or metadata.get("map_id") != 5215 or metadata.get("model_map_id") != 5215
                or metadata.get("generation") != CURRENT_RANKMAP_PACKAGE_GENERATION
                or metadata.get("parameter_generation") != GENERATION
                or metadata.get("input_sha256") != {
                    path: pin[1] for path, pin in input_profile(CURRENT_RANKMAP_PACKAGE_GENERATION).items()
                }):
            return True
        if scope_state(snapshot, expected_generation=CURRENT_RANKMAP_PACKAGE_GENERATION) != "ok":
            return True
        try:
            load_model(lambda relative: _aisha_q5_total_cells_plaintext_bytes(ROOT / relative),
                       generation=CURRENT_RANKMAP_PACKAGE_GENERATION)
        except ModelInputError:
            return True
        return False
    # A cached old uncalibrated result is retired even if the new current
    # session is eligible for the replacement empirical model.
    return type(ref) is dict and bool(_activity_2511_provisional_notice(ref))


def _ref_adaptation_pending_text(
    ref_result: dict[str, Any],
    *,
    formal_mode_reason: Any = None,
) -> str:
    """Project existing no-quote authority reasons; never recover gated evidence."""
    if any(ref_result.get(key) is not None for key in ("conservative", "balanced", "aggressive")):
        return ""
    hall_notes = [note for note in _ref_notes_list(ref_result)
                  if note.startswith('auction_hall_6001_model_unavailable:')]
    if hall_notes:
        if ref_result.get('status') == 'computational_insufficient':
            return '本局约束较强，暂未收敛'
        if any('proved_public_conflict:' in note for note in hall_notes):
            return '公开信息存在冲突'
        return '本局信息待确认'
    if type(formal_mode_reason) is str and formal_mode_reason == "game_generation_unverified":
        return "游戏更新待适配"
    if any(note.startswith('shipwreck_calibrated_unavailable:') for note in _ref_notes_list(ref_result)):
        return "当前仓库待适配"
    if (type(formal_mode_reason) is str and formal_mode_reason == "wuqilin_5215_adaptation_pending") or any(
        note.startswith("wuqilin_5215_adaptation_pending:")
        for note in _ref_notes_list(ref_result)
    ):
        return "当前仓库待适配"
    if ref_result.get("status") == "no_reachable_combo" and any(
        note.startswith("update606_changed_route_distribution_authority:")
        and note.endswith(":no_approved_distribution")
        for note in _ref_notes_list(ref_result)
    ):
        return "当前仓库待适配"
    return ""


def _ref_not_ready_flag_detail(ref_result: dict[str, Any]) -> str:
    adaptation_pending = _ref_adaptation_pending_text(ref_result)
    if adaptation_pending:
        return adaptation_pending
    status = _text(ref_result.get("status"), "")
    notes = _ref_notes_list(ref_result)
    if status == "no_reachable_combo":
        if "public_quality_avg_cells_conflict_fallback" in notes:
            return "公开均格与锁定冲突，已降级后重算"
        if any("public_q4_avg_cells_soft_pending_count" in note for note in notes):
            return "紫均格作软约束，待紫件数锁定后收紧"
        if "constraints_conflict_or_too_strict" in notes:
            if any("public_q" in note and "avg_cells" in note for note in notes):
                return "公开均格与当前锁定冲突，暂无可行组合"
            return "约束冲突，暂无可行组合"
    return status


def _ref_waiting_grid_only(ref_result: dict[str, Any]) -> bool:
    return any(note == "waiting_total_count:grid_only" for note in _ref_notes_list(ref_result))


def _ref_waiting_display_text(
    hero_key: str,
    ref_result: dict[str, Any],
    *,
    round_no: int | None = None,
) -> str:
    adaptation_pending = _ref_adaptation_pending_text(ref_result)
    if adaptation_pending:
        return adaptation_pending
    ref_status = _text(ref_result.get("status"), "")
    schedule_wait = hero_ref_wait_hint(_ref_notes_list(ref_result))
    if schedule_wait:
        return schedule_wait
    if ref_status != "missing_total_count":
        return "等待总件/品质输入"
    if hero_key == "ahmed" and _ref_waiting_grid_only(ref_result):
        return "等待总件数"
    if hero_key == "victor":
        return "等待总件/紫金红"
    if hero_key == "aisha":
        early_wait = _aisha_early_wait_hint(_ref_notes_list(ref_result))
        if early_wait:
            return early_wait
        ref_evidence = ref_result.get("evidence")
        if not isinstance(ref_evidence, dict):
            ref_evidence = {}
        hint = _aisha_next_info_hint(ref_result, ref_evidence, round_no=round_no)
        if hint and hint not in {"-", "信息已足够，观察出价"}:
            return hint
        return "等待总件"
    if hero_key == "ethan":
        return "等待公开输入"
    hint = _next_info_hint(ref_result, hero_key=hero_key)
    if hint and hint != "-":
        return hint
    return "等待外援输入"


def _ref_waiting_flag_label(
    hero_key: str,
    ref_result: dict[str, Any],
    *,
    round_no: int | None = None,
) -> str:
    ref_status = _text(ref_result.get("status"), "")
    if ref_status != "missing_total_count":
        return ""
    return _ref_waiting_display_text(hero_key, ref_result, round_no=round_no)


def _next_info_hint(
    ref_result: dict[str, Any],
    *,
    hero_key: str = "",
    round_no: int | None = None,
    actions: Mapping[str, Any] | None = None,
    session_id: str = "",
    phase: str = "",
) -> str:
    if phase == "settled" and draw_hero_uses_prop_hint(hero_key):
        return ""
    if _wuqilin_5215_provisional_notice(ref_result):
        return "随信息更新"
    if _ref_adaptation_pending_text(ref_result):
        return "本局暂不提供估值"
    evidence = ref_result.get("evidence")
    if not isinstance(evidence, dict):
        return "-"
    status = _text(ref_result.get("status"), "")
    if hero_key == "aisha":
        return _aisha_next_info_hint(ref_result, evidence, round_no=round_no)
    # Ethan reads outlines + 鉴格流 per-tier cells; frame like Aisha (等待X品信息), never a prop name
    # and never a per-tier 件数 (he cannot lock count). Handles every state incl. early/missing-total.
    if hero_key == "ethan":
        return _ethan_next_info_hint(ref_result, evidence, round_no=round_no)
    # Draw heroes (sophie/gabriela/...) fill the picture by SPENDING props, so name the
    # cheap reveal they actually use each round instead of the generic 补总件/总格 copy.
    if draw_hero_uses_prop_hint(hero_key):
        return _draw_hero_next_prop_hint(
            ref_result,
            evidence,
            status,
            hero_key=hero_key,
            round_no=round_no,
            actions=actions,
            session_id=session_id,
        )
    if status == "missing_total_count":
        if hero_key == "ahmed" and _ref_waiting_grid_only(ref_result):
            return "先补总件"
        if hero_key == "victor":
            return "先补总件/紫金红"
    if status == "missing_total_count" or evidence.get("total_count") in (None, ""):
        return "先补总件"
    total_grid_missing = evidence.get("total_grid_target") in (None, "")
    unlocked_low = _unlocked_quality_labels(ref_result, evidence, LOW_QUALITY_DISPLAY_ORDER)
    if unlocked_low:
        return f"优先补{'/'.join(unlocked_low[:2])}件数或均格"
    unlocked_high = _unlocked_quality_labels(ref_result, evidence, ("q4", "q5"))
    if unlocked_high:
        return f"补{'/'.join(unlocked_high[:2])}件数或均格"
    if total_grid_missing and (
        _range_is_unlocked(ref_result.get("total_grid_range"))
        or _q6_grid_needs_direct_info(ref_result, evidence)
    ):
        return "优先补总格/全均格"
    if total_grid_missing and _compact_range_text(ref_result.get("total_grid_range"), suffix="格") != "-":
        return "补总格/全均格"
    return "信息已足够，观察出价"


def _draw_hero_next_prop_hint(
    ref_result: dict[str, Any],
    evidence: dict[str, Any],
    status: str,
    *,
    hero_key: str,
    round_no: int | None = None,
    actions: Mapping[str, Any] | None = None,
    session_id: str = "",
) -> str:
    """Prop-name 下一步 for a draw hero, filtered by the live action history.

    Gabriela/Sophie/Raven follow the author-confirmed progressive queue; Wuqilin retains
    the older information-gap fallback until its own workflow has matching evidence.
    """
    used_action_ids = {
        str(row.get("action_id")).strip()
        for bucket in ("sent", "results")
        for row in ((actions or {}).get(bucket) or ())
        if isinstance(row, Mapping) and str(row.get("action_id") or "").strip()
    }
    progressive_hint = draw_hero_progressive_prop_hint(
        hero_key,
        round_no=round_no,
        session_id=session_id,
        used_action_ids=used_action_ids,
    )
    if progressive_hint:
        return progressive_hint

    # Wuqilin keeps the legacy information-gap policy until its per-round player workflow
    # has been independently confirmed.
    total_uncertain = (
        status == "missing_total_count"
        or evidence.get("total_count") in (None, "")
        or (
            evidence.get("total_grid_target") in (None, "")
            and _range_is_unlocked(ref_result.get("total_grid_range"))
        )
    )
    in_decision_window = round_no is not None and int(round_no) >= DRAW_HERO_DECISION_ROUND_MIN
    if in_decision_window and total_uncertain:
        return draw_hero_prop_for_gap("endgame_total")
    # Cheap per-round reveal: keep revealing quality (their most-used prop) while any
    # tier is open; once quality is broadly known, the residual is item value -> 随机抽检.
    quality_open = bool(
        _unlocked_quality_labels(ref_result, evidence, ("q1", "q3", "q4", "q5"))
    )
    ranges = ref_result.get("quality_count_ranges")
    quality_known = bool(isinstance(ranges, dict) and ranges) and not quality_open
    if quality_known:
        return draw_hero_prop_for_gap("value")
    return draw_hero_prop_for_gap("quality")


def _parse_money(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(str(value).replace(",", "").strip())))
    except (TypeError, ValueError, OverflowError):
        return None


def _signed_money(value: Any, fallback: str = "-") -> str:
    parsed = _parse_money(value)
    if parsed is None:
        return fallback
    sign = "+" if parsed > 0 else ""
    return f"{sign}{parsed:,}"


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        return None


def _hero_from_context(hero: Any, *hero_id_candidates: Any) -> str:
    text = _text(hero, "").strip()
    if text and text.lower() not in {"?", "unknown", "none", "null"}:
        normalized = normalize_hero_key(text)
        return normalized or text
    for candidate in hero_id_candidates:
        hero_id = _parse_int(candidate)
        if hero_id in HERO_BY_ID:
            return HERO_BY_ID[hero_id]
    return text or "?"


def _is_unknown_hero(value: Any) -> bool:
    return _text(value, "").strip().lower() in {"", "?", "unknown", "none", "null"}


def _clean_range_text(value: Any) -> str:
    text = _text(value, "").strip()
    return text if text and text != "-" else ""


def _format_range(values: tuple[Any, Any, Any]) -> str:
    return " / ".join(_money(value, "?") for value in values)


def _parse_range_numbers(value: Any) -> list[int | None]:
    text = _text(value, "").strip()
    if not text or text == "-":
        return []
    parts = [part.strip() for part in text.split("/") if part.strip()]
    out: list[int | None] = []
    for part in parts[:3]:
        out.append(_parse_money(part))
    while len(out) < 3:
        out.append(None)
    return out


def _range_mid(value: Any) -> int | None:
    numbers = _parse_range_numbers(value)
    if len(numbers) >= 2:
        return numbers[1]
    if numbers:
        return numbers[0]
    return None


def _floor_range_text(value: str, floor: int | None) -> str:
    if floor is None or floor <= 0:
        return value
    numbers = _parse_range_numbers(value)
    if len(numbers) < 3:
        return value
    floored = [
        max(number, floor) if number is not None else None
        for number in numbers[:3]
    ]
    return " / ".join(_money(number, "?") for number in floored)


def _join_notes(*parts: str) -> str:
    cleaned: list[str] = []
    for part in parts:
        text = _text(part, "").strip()
        if text and text != "-" and text not in cleaned:
            cleaned.append(text)
    return "；".join(cleaned)


def _shadow_range_text(
    shadow: dict[str, Any],
    keys: tuple[str, str, str],
    *,
    money: bool = False,
) -> str:
    values = tuple(shadow.get(key) for key in keys)
    if all(value in (None, "") for value in values):
        return ""
    if money:
        return _format_range(values)
    out: list[str] = []
    for value in values:
        if value in (None, ""):
            out.append("?")
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            out.append(str(value))
            continue
        out.append(str(int(round(number))))
    return " / ".join(out)


def _posterior_or_shadow_range(
    posterior: dict[str, Any],
    shadow: dict[str, Any],
    posterior_key: str,
    shadow_keys: tuple[str, str, str],
    *,
    money: bool = False,
) -> str:
    return (
        _clean_range_text(posterior.get(posterior_key))
        or _shadow_range_text(shadow, shadow_keys, money=money)
        or "-"
    )


def _red_risk_reference(
    decision: dict[str, Any],
    q6_ref: dict[str, Any],
    posterior: dict[str, Any],
) -> str:
    parts: list[str] = []
    decision_text = _text(decision.get("q6_risk_reference"), "").strip()
    if decision_text:
        parts.append(decision_text)
    else:
        prior_gap = _text(q6_ref.get("prior_gap"), "").strip()
        if prior_gap:
            parts.append(prior_gap)
        reference_p90 = _text(
            q6_ref.get("practical_reference_p90") or q6_ref.get("prior_reference_p90"),
            "",
        ).strip()
        if reference_p90:
            parts.append(f"参考P90 {reference_p90}")
    prior_rate = _text(posterior.get("q6_prior_rate"), "").strip()
    sample_rate = _text(posterior.get("q6_sample_rate"), "").strip()
    if prior_rate or sample_rate:
        parts.append(f"先验/样本 {prior_rate or '-'} / {sample_rate or '-'}")
    return "；".join(parts)


def _quality_key(value: Any) -> str:
    if value in (None, ""):
        return "unknown"
    text = str(value).strip().lower()
    if text.startswith("q") and text[1:].isdigit():
        return text
    parsed = _parse_int(value)
    if parsed is not None:
        return f"q{parsed}"
    return text or "unknown"


def _ref_input_summary(ref_result: dict[str, Any]) -> str:
    evidence = ref_result.get("evidence")
    if not isinstance(evidence, dict):
        return "-"
    parts: list[str] = []
    total_parts: list[str] = []
    avg_parts: list[str] = []
    total_count = evidence.get("total_count")
    if total_count not in (None, ""):
        total_parts.append(f"总件 {total_count}")
    total_grid = evidence.get("total_grid_target")
    locked_grid, estimated_grid = _grid_summary_parts(ref_result, evidence)
    if locked_grid:
        total_parts.append(locked_grid)
        parsed_count = _parse_int(total_count)
        try:
            parsed_grid = float(total_grid)
        except (TypeError, ValueError):
            parsed_grid = None
        if parsed_count and parsed_grid is not None:
            avg_parts.append(f"全均格 {_compact_float(parsed_grid / parsed_count)}")
    elif estimated_grid:
        total_parts.append(estimated_grid)
    avg_cells = evidence.get("avg_cells")
    split_avg_cells = evidence.get("split_avg_cells")
    if isinstance(split_avg_cells, dict):
        for key in SPLIT_QUALITY_DISPLAY_ORDER:
            value = split_avg_cells.get(key)
            if value in (None, ""):
                continue
            avg_parts.append(f"{SPLIT_QUALITY_LABELS.get(key, key)}均格 {_compact_float(value)}")
    if isinstance(avg_cells, dict):
        for key in QUALITY_DISPLAY_ORDER:
            value = avg_cells.get(key)
            if value in (None, ""):
                continue
            avg_parts.append(f"{QUALITY_LABELS.get(key, key)}均格 {_compact_float(value)}")
    fixed_counts = evidence.get("fixed_counts")
    split_counts = evidence.get("split_counts")
    if isinstance(split_counts, dict):
        split_count_parts = [
            f"{SPLIT_QUALITY_LABELS.get(key, key)}件 {split_counts[key]}"
            for key in SPLIT_QUALITY_DISPLAY_ORDER
            if split_counts.get(key) not in (None, "")
        ]
        if split_count_parts:
            parts.append("分件 " + "，".join(split_count_parts))
    if isinstance(fixed_counts, dict):
        count_parts = [
            f"{QUALITY_LABELS.get(key, key)}件 {fixed_counts[key]}"
            for key in QUALITY_DISPLAY_ORDER
            if fixed_counts.get(key) not in (None, "")
        ]
        if count_parts:
            parts.append("件数 " + "，".join(count_parts))
    quality_cells = evidence.get("quality_cells")
    split_quality_cells = evidence.get("split_quality_cells")
    if isinstance(split_quality_cells, dict):
        split_cell_parts = [
            f"{SPLIT_QUALITY_LABELS.get(key, key)}格 {split_quality_cells[key]}"
            for key in SPLIT_QUALITY_DISPLAY_ORDER
            if split_quality_cells.get(key) not in (None, "")
        ]
        if split_cell_parts:
            parts.append("分格 " + "，".join(split_cell_parts))
    if isinstance(quality_cells, dict):
        cell_parts = [
            f"{QUALITY_LABELS.get(key, key)}格 {quality_cells[key]}"
            for key in QUALITY_DISPLAY_ORDER
            if quality_cells.get(key) not in (None, "")
        ]
        if cell_parts:
            parts.append("格数 " + "，".join(cell_parts))
    min_counts = evidence.get("min_counts")
    if isinstance(min_counts, dict):
        floor_parts = []
        for key in QUALITY_DISPLAY_ORDER:
            value = _parse_int(min_counts.get(key))
            fixed_value = _parse_int(fixed_counts.get(key)) if isinstance(fixed_counts, dict) else None
            if value is None or value <= 0 or fixed_value is not None and fixed_value >= value:
                continue
            floor_parts.append(f"{QUALITY_LABELS.get(key, key)}≥{value}")
        if floor_parts:
            parts.append("下界 " + "，".join(floor_parts))
    count_sums = evidence.get("count_sums")
    if isinstance(count_sums, dict):
        if count_sums.get("q4q5q6") not in (None, ""):
            parts.append(f"紫金红件 {count_sums['q4q5q6']}")
        elif count_sums.get("q5q6_min") not in (None, ""):
            parts.append(
                f"时尚+数码金红件 {count_sums['q5q6_min']}（类内精确，全仓下界）"
            )
        elif count_sums.get("q4q5") not in (None, ""):
            parts.append(f"紫金件 {count_sums['q4q5']}")
    ordered_parts = total_parts + parts + avg_parts
    return " · ".join(ordered_parts) if ordered_parts else "-"


def _quality_lower_bound_text(quality_key: str) -> str:
    label = QUALITY_LABELS.get(quality_key)
    if label:
        return f"{label}品≥1"
    if quality_key and quality_key != "unknown":
        return f"{quality_key.upper()}≥1"
    return "品质≥1"


def _minimap_source_label(source_text: str, layout_source: str) -> str:
    source = (source_text or layout_source or "").strip().lower()
    if source == "public_info":
        return "公共抽检"
    if source in {"packet", "quality_reveal", "quality_only", "quality_marker"}:
        return "抽检"
    if source == "skill_reveal":
        return "英雄技能"
    if source in {"settlement", "settlement_inventory"}:
        return "结算"
    return source_text or layout_source or ""


def _shape_text(shape_code: Any, width: int, height: int, cells: Any) -> str:
    parsed_cells = _parse_int(cells)
    if _parse_int(shape_code) is not None and width > 0 and height > 0:
        total = parsed_cells or width * height
        return f"轮廓 {width}x{height}/{total}格"
    if parsed_cells is not None and parsed_cells > 0:
        return f"已知 {parsed_cells}格"
    return ""


def _minimap_item_display_text(
    raw: dict[str, Any],
    *,
    quality_key: str,
    shape_code: Any,
    width: int,
    height: int,
    source_text: str,
) -> tuple[str, str]:
    item_name = _text(raw.get("item_name") or raw.get("name"), "").strip()
    item_id = raw.get("item_id")
    category = _text(raw.get("category_label"), "").strip()
    fallback_label = _quality_lower_bound_text(quality_key)
    label = _text(raw.get("display_label"), "").strip() or item_name or category or fallback_label
    quality_label = QUALITY_LABELS.get(quality_key, quality_key.upper() if quality_key else "品质")
    if item_name:
        primary = item_name
    elif item_id not in (None, "", 0, "0"):
        primary = f"ID {item_id}"
    else:
        primary = fallback_label
    quality_part = (
        ""
        if primary == fallback_label
        else f"{quality_label}品"
        if quality_label and "品" not in quality_label
        else quality_label
    )
    excluded_category_labels = raw.get("excluded_category_labels")
    if not isinstance(excluded_category_labels, list):
        excluded_category_labels = []
    excluded_category_text = " + ".join(
        _text(value, "").strip()
        for value in excluded_category_labels
        if _text(value, "").strip()
    )
    excluded_quality_labels = {
        1: "白",
        2: "绿",
        3: "蓝",
        4: "紫",
        5: "金",
    }
    raw_excluded_qualities = raw.get("excluded_qualities")
    if not isinstance(raw_excluded_qualities, list):
        raw_excluded_qualities = []
    excluded_quality_text = "/".join(
        excluded_quality_labels[quality]
        for quality in raw_excluded_qualities
        if type(quality) is int and quality in excluded_quality_labels
    )
    quality_source = _text(raw.get("quality_source"), "").strip()
    tooltip = _join_notes(
        primary,
        quality_part,
        "鉴影域推断红" if quality_source == "catalog_red_domain" else "",
        _shape_text(shape_code, width, height, raw.get("cells")),
        f"类别 {category}" if category else "",
        (
            f"排除类别 {excluded_category_text}"
            if excluded_category_text
            else ""
        ),
        (
            f"排除品质 {excluded_quality_text}"
            if excluded_quality_text
            else ""
        ),
        (
            f"价值 {_parse_int(raw.get('value')):,}"
            if _parse_int(raw.get("value")) not in (None, 0)
            else ""
        ),
        _minimap_source_label(source_text, _text(raw.get("layout_source"), "")),
        f"local {raw.get('local_index')}" if raw.get("local_index") not in (None, "") else "",
    )
    explicit_tooltip = _text(raw.get("tooltip"), "").strip()
    return label, explicit_tooltip or tooltip or _text(raw.get("tooltip"), "").strip()


def _minimap_summary(snapshot: dict[str, Any], uc: dict[str, Any]) -> dict[str, Any]:
    minimap = uc.get("minimap") if isinstance(uc.get("minimap"), dict) else {}
    context = uc.get("context") if isinstance(uc.get("context"), dict) else {}
    phase = _text(context.get("phase") or snapshot.get("phase"), "")
    root_items = snapshot.get("minimap_grid_items")
    if not isinstance(root_items, list):
        root_items = []

    status = _text(minimap.get("status"), "")
    contract_items = minimap.get("items") if isinstance(minimap.get("items"), list) else []
    raw_items = contract_items
    layout_source = _text(minimap.get("layout_source"), "")
    if phase == "settled" and root_items:
        raw_items = root_items
        status = "available"
        layout_source = layout_source or "settlement_inventory"
    elif contract_items:
        status = "available"
    elif root_items:
        raw_items = root_items
        status = "available"
        layout_source = layout_source or "minimap_grid_items"
    else:
        raw_items = []

    columns = _parse_int(minimap.get("columns")) or 10
    rows_hint = (
        _parse_int(minimap.get("viewport_rows"))
        or _parse_int(minimap.get("rows_hint"))
        or _parse_int(minimap.get("max_rows"))
        or 13
    )
    known_items = _parse_int(minimap.get("known_items"))
    drawable_items = _parse_int(minimap.get("drawable_items"))
    final_total_items = _parse_int(minimap.get("final_total_items"))
    quality_counts = minimap.get("quality_counts") if isinstance(minimap.get("quality_counts"), dict) else {}
    quality_reveal_counts = (
        minimap.get("quality_reveal_counts")
        if isinstance(minimap.get("quality_reveal_counts"), dict)
        else {}
    )

    items: list[dict[str, Any]] = []
    max_row = 0
    max_col = 0
    observed_quality_counts: dict[str, int] = {}
    public_shapes = _public_shape_by_local(snapshot)
    if status == "available":
        for raw in raw_items[:120]:
            if not isinstance(raw, dict):
                continue
            row = _parse_int(raw.get("row"))
            col = _parse_int(raw.get("col"))
            local_index = _parse_int(raw.get("local_index"))
            if (row is None or col is None) and local_index is not None:
                row = local_index // columns + 1
                col = local_index % columns + 1
            if row is None or col is None:
                continue
            shape_code = _parse_int(raw.get("shape_key") or raw.get("shape_code"))
            if shape_code is None and local_index is not None:
                shape_code = public_shapes.get(local_index)
            dims = _shape_dims(shape_code)
            if dims is not None:
                width, height = dims
            else:
                width = max(1, _parse_int(raw.get("width")) or 1)
                height = max(1, _parse_int(raw.get("height")) or 1)
            source_text = _text(raw.get("source") or raw.get("layout_source"), "")
            render_mode = _text(raw.get("render_mode") or "")
            item_value = _parse_int(raw.get("value"))
            if (
                not render_mode
                and item_value not in (None, 0)
                and shape_code is None
                and _parse_int(raw.get("item_id")) is None
            ):
                shape_code = 11
                width = max(1, width or 1)
                height = max(1, height or 1)
                render_mode = "footprint"
            quality_key = _quality_key(raw.get("quality"))
            label, tooltip = _minimap_item_display_text(
                raw,
                quality_key=quality_key,
                shape_code=shape_code,
                width=width,
                height=height,
                source_text=source_text,
            )
            observed_quality_counts[quality_key] = observed_quality_counts.get(quality_key, 0) + 1
            max_row = max(max_row, row + height - 1)
            max_col = max(max_col, col + width - 1)
            items.append(
                {
                    "row": row,
                    "col": col,
                    "width": width,
                    "height": height,
                    "quality": quality_key,
                    "runtime_id": raw.get("runtime_id"),
                    "local_index": local_index,
                    "item_id": raw.get("item_id"),
                    "shape_key": _text(shape_code or ""),
                    "label": label,
                    "display_label": _text(raw.get("display_label"), "").strip() or label,
                    "tooltip": tooltip,
                    "value": item_value,
                    "cells": width * height if dims is not None else _parse_int(raw.get("cells")),
                    "source": source_text,
                    "layout_source": _text(raw.get("layout_source") or ""),
                    "render_mode": render_mode,
                }
            )
    if not isinstance(quality_counts, dict) or not quality_counts:
        quality_counts = quality_reveal_counts
    if not isinstance(quality_counts, dict) or not quality_counts:
        quality_counts = observed_quality_counts
    settlement_quality_counts = _quality_counts_from_text(snapshot.get("final_quality_counts"))
    if phase == "settled" and settlement_quality_counts:
        quality_counts = settlement_quality_counts
    columns = max(columns, max_col, 1)
    rows_hint = max(rows_hint, max_row, 1)
    if status != "available" or not items:
        return {
            "status": "unavailable",
            "summary_text": "等待公开轮廓/小地图",
            "layout_source": layout_source or "-",
            "columns": 10,
            "viewport_rows": 13,
            "known_items": 0,
            "drawable_items": 0,
            "final_total_items": final_total_items,
            "quality_counts": {},
            "items": [],
        }

    known = known_items if known_items is not None else len(items)
    drawable = drawable_items if drawable_items is not None else len(items)
    total = final_total_items if final_total_items is not None else known
    if phase == "settled" and total and drawable:
        known = drawable
    source_text = layout_source or "live_grid"
    marker_only = bool(items) and all(
        _text(item.get("render_mode"), "") == "marker" for item in items
    )
    if (
        phase != "settled"
        and source_text == "live_known"
        and drawable == 0
        and marker_only
        and known
    ):
        summary_text = f"{known} 个标记 · {source_text}"
    elif total and known != total:
        summary_text = f"{known}/{total} 件 · {source_text}"
    else:
        summary_text = f"{drawable} 件 · {source_text}"
    return {
        "status": "available",
        "summary_text": summary_text,
        "layout_source": source_text,
        "columns": columns,
        "viewport_rows": rows_hint,
        "known_items": known,
        "drawable_items": drawable,
        "final_total_items": final_total_items,
        "quality_counts": quality_counts,
        "items": items,
    }


def _quality_counts_from_text(text: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for part in _text(text, "").split(";"):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        quality = _quality_key(key.strip())
        value = _parse_int(raw_value)
        if value is not None:
            counts[quality] = value
    return counts


def _shape_dims(shape_code: Any) -> tuple[int, int] | None:
    code = _parse_int(shape_code)
    if code is None:
        return None
    width = code // 10
    height = code % 10
    if width <= 0 or height <= 0:
        return None
    return width, height


def _public_shape_by_local(snapshot: dict[str, Any]) -> dict[int, int]:
    out: dict[int, int] = {}
    rows = snapshot.get("public_info_rows")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        for item in row.get("revealed_items_detail") or ():
            if not isinstance(item, dict):
                continue
            local = _parse_int(item.get("local_index"))
            shape = _parse_int(item.get("shape_code") or item.get("shape_key"))
            if local is not None and shape is not None and _shape_dims(shape) is not None:
                out[local] = shape
    return out


def _known_quality_footprint(
    minimap_summary: dict[str, Any],
    quality_key: str,
) -> tuple[int, int]:
    count = 0
    cells = 0
    items = minimap_summary.get("items")
    if not isinstance(items, list):
        return (0, 0)
    for item in items:
        if not isinstance(item, dict):
            continue
        if _quality_key(item.get("quality")) != quality_key:
            continue
        render_mode = _text(item.get("render_mode"), "")
        if render_mode != "footprint":
            continue
        item_cells = _parse_int(item.get("cells"))
        if item_cells is None or item_cells <= 0:
            width = _parse_int(item.get("width"))
            height = _parse_int(item.get("height"))
            if width is not None and height is not None:
                item_cells = width * height
        if item_cells is None or item_cells <= 0:
            continue
        count += 1
        cells += item_cells
    return (count, cells)


def _is_ethan_hero(evidence: dict[str, Any]) -> bool:
    """伊森(208)看轮廓+逐品质格子,不锁逐档件数 → range 模式也带上格子范围。"""
    return str(evidence.get("hero") or "").strip().lower() == "ethan"


def _ethan_cells_text(cells_range: Any) -> str:
    """伊森格子读数:已锁(min==mid==max,如良品扫描蓝=24)折成单值「24」省空间;否则显 min/mid/max 范围。"""
    locked = _locked_range_value(cells_range)
    if locked is not None:
        return str(locked)
    return _range_text(cells_range).replace(" / ", "/")


def _purple_estimate_only(key: str, evidence: dict[str, Any]) -> bool:
    """紫(q4)是软例外:格子来自估算(quality_values/avg_values)而非扫描锁定
    (无 quality_cells[q4])时,前缀「约」表示未完全锁定。金(q5)是硬锁,永不「约」。"""
    if key != "q4":
        return False
    quality_cells = evidence.get("quality_cells")
    if isinstance(quality_cells, dict) and _parse_int(quality_cells.get(key)) is not None:
        return False  # 已被扫描锁定 → 不标「约」
    for field in ("quality_values", "avg_values"):
        src = evidence.get(field)
        if isinstance(src, dict) and src.get(key) not in (None, ""):
            return True
    return False


def _probability_text(probability: Any) -> str:
    try:
        percent = max(0.0, min(100.0, float(probability) * 100.0))
    except (TypeError, ValueError):
        return "?%"
    if 0.0 < percent < 0.1:
        return "<0.1%"
    return f"{percent:.1f}".rstrip("0").rstrip(".") + "%"


def _joint_quality_candidates(
    ref_result: dict[str, Any], key: str
) -> list[tuple[int, int, float]]:
    candidates = ref_result.get("quality_count_cells_candidates")
    raw_rows = candidates.get(key) if isinstance(candidates, dict) else None
    rows: list[tuple[int, int, float]] = []
    if isinstance(raw_rows, (list, tuple)):
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            count = _parse_int(raw.get("count"))
            cells = _parse_int(raw.get("cells"))
            prob = _quality_candidate_probability(raw.get("prob"))
            if (
                count is None
                or cells is None
                or prob is None
                or (count == 0) != (cells == 0)
            ):
                return []
            rows.append((count, cells, prob))
    if rows:
        # A late geometry/room reconciliation may honestly widen the displayed range
        # without rewriting the pricing candidate posterior.  In that state a lone
        # modeled row is no longer a public 100% fact, so suppress it instead of
        # pairing the exact count with a falsely exact cell footprint (5604 R4).
        count_ranges = ref_result.get("quality_count_ranges")
        cells_ranges = ref_result.get("quality_cells_ranges")
        count_range = count_ranges.get(key) if isinstance(count_ranges, dict) else None
        cells_range = cells_ranges.get(key) if isinstance(cells_ranges, dict) else None
        if len(rows) == 1 and rows[0][2] >= 0.999 and (
            _range_is_unlocked(count_range) or _range_is_unlocked(cells_range)
        ):
            return []
        return sorted(rows, key=lambda row: (-row[2], row[0], row[1]))

    # Backward-compatible fallback for an old/cached exact result.  Non-unique marginal ranges are
    # deliberately not paired here; only a true one-state lock can honestly be called 100%.
    count_ranges = ref_result.get("quality_count_ranges")
    cells_ranges = ref_result.get("quality_cells_ranges")
    count = _locked_range_value(count_ranges.get(key)) if isinstance(count_ranges, dict) else None
    cells = _locked_range_value(cells_ranges.get(key)) if isinstance(cells_ranges, dict) else None
    return [(count, cells, 1.0)] if count is not None and cells is not None else []


def _quality_candidate_probability(value: Any) -> float | None:
    """Validate a JSON probability before any potentially overflowing cast."""
    if type(value) is int:
        return 1.0 if value == 1 else None
    if type(value) is float:
        if not math.isfinite(value) or value <= 0.0 or value > 1.0:
            return None
        return value
    return None


def _quality_marginal_candidates_payload(
    ref_result: dict[str, Any],
    key: str,
    field: str,
) -> tuple[list[dict[str, Any]], str]:
    """Project a display marginal without pairing it into joint candidates."""
    raw_by_quality = ref_result.get(field)
    if raw_by_quality is None:
        return [], ""
    if type(raw_by_quality) is not dict:  # noqa: E721 - no mapping protocols
        return [], "品质边际候选字段类型冲突"
    if any(type(raw_key) is not str for raw_key in raw_by_quality):
        return [], "品质边际候选字段类型冲突"
    if key not in raw_by_quality:
        return [], ""
    raw_rows = raw_by_quality.get(key)
    if type(raw_rows) not in (list, tuple):
        return [], "品质边际候选行类型冲突"
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    total = 0.0
    for raw_row in raw_rows:
        if type(raw_row) is not dict:  # noqa: E721 - no mapping protocols
            return [], "品质边际候选行类型冲突"
        if any(type(raw_key) is not str for raw_key in raw_row):
            return [], "品质边际候选行类型冲突"
        value = raw_row.get("value")
        probability = _quality_candidate_probability(raw_row.get("prob"))
        if (
            type(value) is not int
            or value < 0
            or probability is None
        ):
            return [], "品质边际候选数值冲突"
        if value in seen:
            return [], "品质边际候选概率冲突"
        seen.add(value)
        total += probability
        rows.append({"value": value, "probability": probability})
    if total > 1.000001:
        return [], "品质边际候选概率冲突"
    rows.sort(key=lambda row: (-row["probability"], row["value"]))
    return rows, ""


def _visible_joint_marginal_candidates(
    candidates: list[dict[str, Any]],
    dimension: str,
) -> list[dict[str, Any]]:
    """Aggregate one display marginal from the exact visible joint rows."""
    totals: dict[int, float] = {}
    for candidate in candidates:
        value = candidate[dimension]
        totals[value] = totals.get(value, 0.0) + candidate["probability"]
    rows = [
        {"value": value, "probability": round(probability, 6)}
        for value, probability in totals.items()
    ]
    rows.sort(key=lambda row: (-row["probability"], row["value"]))
    return rows


def _reconcile_visible_joint_marginal(
    marginal: list[dict[str, Any]],
    visible: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Make a truncated display marginal cover every visible joint value.

    The engine deliberately publishes a wider top-k for joint rows than for
    independent marginals, and rounds both projections separately.  The joint
    rows are authoritative for the compact composition view, so missing visible
    values and rounding-only deficits are filled from those same rows.  Existing
    marginal-only values remain useful for the displayed range.  If their union
    cannot be a probability distribution, fall back to the visible joint
    projection instead of manufacturing extra mass.
    """
    reconciled = {
        row["value"]: {
            "value": row["value"],
            "probability": row["probability"],
        }
        for row in marginal
    }
    for visible_row in visible:
        value = visible_row["value"]
        current = reconciled.get(value)
        if (
            current is None
            or current["probability"] + 0.000001 < visible_row["probability"]
        ):
            reconciled[value] = dict(visible_row)
    rows = sorted(
        reconciled.values(),
        key=lambda row: (-row["probability"], row["value"]),
    )
    if sum(row["probability"] for row in rows) > 1.000001:
        rows = [dict(row) for row in visible]
    return rows, rows != marginal


def _project_total_constrained_count_candidates(
    ref_result: dict[str, Any],
    qualities: dict[str, dict[str, Any]],
) -> tuple[
    int | None,
    list[dict[str, int]],
    list[dict[str, int]],
]:
    """Project three aligned compact arms that each satisfy total_count.

    Per-quality marginals do not encode cross-quality correlation.  Select only
    from complete arms carried by the engine's additive display authority; never
    reconstruct a cartesian product from independent visible values.
    """

    evidence = dict.get(ref_result, "evidence")
    evidence_missing = evidence is None
    evidence_invalid = not evidence_missing and (
        type(evidence) is not dict  # noqa: E721 - JSON trust boundary
        or any(
            type(raw_key) is not str for raw_key in evidence  # noqa: E721
        )
    )
    if evidence_missing or evidence_invalid:
        evidence = {}
    raw_total_count = dict.get(evidence, "total_count")
    total_count = _strict_non_negative_whole(raw_total_count)
    raw_locks = dict.get(ref_result, "quality_lock")
    raw_q6_lock = (
        dict.get(raw_locks, "q6")
        if type(raw_locks) is dict  # noqa: E721
        else None
    )
    q6_is_residual_projection = (
        type(raw_q6_lock) is dict  # noqa: E721
        and dict.get(raw_q6_lock, "reason") == "derived_single_solution"
    )

    def arms_for_wire(
        arms: tuple[tuple[int, ...], ...],
    ) -> list[dict[str, int]]:
        return [
            {
                key: arm[index]
                for index, key in enumerate(QUALITY_DISPLAY_ORDER)
            }
            for arm in arms
        ]

    def mark_unknown(
        note: str,
        authoritative_arms: tuple[tuple[int, ...], ...] = (),
    ) -> tuple[int | None, list[dict[str, int]], list[dict[str, int]]]:
        for key, quality in qualities.items():
            if type(quality) is dict and dict.get(quality, "status") in {  # noqa: E721
                "ready",
                "exact",
            }:
                quality["compact_count_candidates"] = []
                quality["compact_count_triplet"] = []
                if q6_is_residual_projection and key == "q6":
                    # The red count is only a residual authority.  Once the
                    # total/complete-arm chain is unavailable or malformed,
                    # do not let its per-quality singleton revive an
                    # independent 1/1/1 display.
                    quality["count_lock_value"] = None
                    quality["dimension_state"] = _quality_dimension_state_text(
                        None,
                        dict.get(quality, "cells_lock_value"),
                    )
                    quality["count_correlation_state"] = "unknown"
                    quality["count_correlation_note"] = note
                elif q6_is_residual_projection:
                    # A broken residual chain does not erase an unrelated
                    # per-quality exact fact.
                    quality["count_correlation_state"] = "independent"
                    quality["count_correlation_note"] = ""
                else:
                    quality["count_correlation_state"] = "unknown"
                    quality["count_correlation_note"] = note
        # Omit the root arm envelope: it would be invalid to expose a total
        # constrained matrix while one of its authority inputs is unknown.
        return (
            (None, [], [])
            if q6_is_residual_projection
            else (total_count, arms_for_wire(authoritative_arms), [])
        )

    if evidence_invalid:
        return mark_unknown("evidence 字段无效；品质件数联动未知")

    if raw_total_count is None:
        if q6_is_residual_projection:
            return mark_unknown("缺少总件 authority；红件联动待定")
        for quality in qualities.values():
            if type(quality) is dict and dict.get(quality, "status") in {  # noqa: E721
                "ready",
                "exact",
            }:
                quality["compact_count_candidates"] = []
                quality["compact_count_triplet"] = []
                quality["count_correlation_state"] = "independent"
                quality["count_correlation_note"] = ""
        return None, [], []

    if total_count is None:
        return mark_unknown("总件字段无效；品质件数联动未知")

    raw_authoritative_arms = dict.get(ref_result, "quality_count_arms")
    if (
        type(raw_authoritative_arms) is not list  # noqa: E721
        or not raw_authoritative_arms
        or len(raw_authoritative_arms) > MAX_COMPACT_COUNT_ARM_STATES
    ):
        return mark_unknown("缺少完整品质件数联合臂 authority；联动未知")
    seen_authoritative_arms: set[tuple[int, ...]] = set()
    for raw_arm in raw_authoritative_arms:
        if (
            type(raw_arm) is not dict  # noqa: E721
            or any(type(raw_key) is not str for raw_key in raw_arm)  # noqa: E721
            or set(raw_arm) != set(QUALITY_DISPLAY_ORDER)
        ):
            return mark_unknown("品质件数联合臂 authority 类型冲突；联动未知")
        arm = tuple(dict.get(raw_arm, key) for key in QUALITY_DISPLAY_ORDER)
        if (
            any(type(value) is not int or value < 0 for value in arm)  # noqa: E721
            or sum(arm) != total_count
            or arm in seen_authoritative_arms
        ):
            return mark_unknown("品质件数联合臂 authority 冲突；联动未知")
        seen_authoritative_arms.add(arm)
    authoritative_arms = tuple(sorted(seen_authoritative_arms))

    # Count certainty and footprint certainty are separate dimensions.  A red
    # row may have several reachable cell footprints while its item count is
    # still the unique arithmetic residual of one exact total and four exact
    # non-red counts.  Authorize that count-only lock only from this atomic
    # RefResult: every complete engine arm must equal the same residual closure,
    # and every q6 joint row must agree on the residual count.  This deliberately
    # does not change cells_lock_value or feed anything back into pricing.
    residual_count_lock: int | None = None
    # Derived q6 is a residual-only display here: without all four exact
    # non-red authorities it must remain pending, even if its own candidate
    # marginal happens to collapse to one visible count.  Other heroes retain
    # their established independent/exact q6 contracts.
    residual_count_conflict = q6_is_residual_projection
    non_red_counts: dict[str, int] = {}
    for key in QUALITY_DISPLAY_ORDER:
        if key == "q6":
            continue
        quality = dict.get(qualities, key)
        count_lock = (
            dict.get(quality, "count_lock_value")
            if type(quality) is dict  # noqa: E721 - JSON trust boundary
            else None
        )
        if type(count_lock) is not int or count_lock < 0:  # noqa: E721
            break
        non_red_counts[key] = count_lock
    if len(non_red_counts) == len(QUALITY_DISPLAY_ORDER) - 1:
        residual_count_conflict = True
        residual = total_count - sum(non_red_counts.values())
        q6_quality = dict.get(qualities, "q6")
        q6_candidates = (
            dict.get(q6_quality, "candidates")
            if type(q6_quality) is dict  # noqa: E721
            else None
        )
        expected_arm = tuple(
            residual if key == "q6" else non_red_counts[key]
            for key in QUALITY_DISPLAY_ORDER
        )
        if (
            0 <= residual <= total_count
            and all(arm == expected_arm for arm in authoritative_arms)
            and type(q6_candidates) is list  # noqa: E721
            and q6_candidates
            and all(
                type(row) is dict  # noqa: E721
                and type(dict.get(row, "count")) is int  # noqa: E721
                and dict.get(row, "count") == residual
                for row in q6_candidates
            )
        ):
            residual_count_lock = residual
            residual_count_conflict = False
            q6_quality["count_lock_value"] = residual
            q6_quality["dimension_state"] = _quality_dimension_state_text(
                residual,
                dict.get(q6_quality, "cells_lock_value"),
            )

    visible_values: dict[str, tuple[int, ...]] = {}
    for key in QUALITY_DISPLAY_ORDER:
        quality = dict.get(qualities, key)
        raw_candidates = (
            dict.get(quality, "candidates")
            if type(quality) is dict  # noqa: E721
            else None
        )
        values = tuple(
            sorted(
                {
                    dict.get(row, "count")
                    for row in raw_candidates
                    if type(row) is dict  # noqa: E721
                    and type(dict.get(row, "count")) is int  # noqa: E721
                    and dict.get(row, "count") >= 0
                }
            )
        ) if type(raw_candidates) is list else ()  # noqa: E721
        visible_values[key] = values
        if not values:
            return mark_unknown(
                f"缺少完整品质件数联合证据；总件 {total_count} 下联动未知"
            )

    # The three count-range entries remain an independent sibling authority.
    # Complete arms must not resurrect a reference when an explicit range is
    # missing, malformed, unordered, or disagrees with the same RefResult's
    # visible joint candidates.  A legal non-unique range (for example q5
    # varying with q6) is intentionally accepted; a count lock is not needed.
    raw_count_ranges = dict.get(ref_result, "quality_count_ranges")

    def valid_visible_count_range(key: str) -> bool:
        if (
            type(raw_count_ranges) is not dict  # noqa: E721
            or any(type(raw_key) is not str for raw_key in raw_count_ranges)  # noqa: E721
        ):
            return False
        raw_range = dict.get(raw_count_ranges, key)
        if type(raw_range) not in (list, tuple) or len(raw_range) != 3:  # noqa: E721
            return False
        if any(type(value) is not int or value < 0 for value in raw_range):  # noqa: E721
            return False
        low, middle, high = raw_range
        return (
            low <= middle <= high
            and all(value in visible_values[key] for value in raw_range)
        )

    valid_count_ranges = all(
        valid_visible_count_range(key) for key in QUALITY_DISPLAY_ORDER
    )
    if q6_is_residual_projection and not valid_count_ranges:
        return mark_unknown("品质件数范围 authority 无效；红件联动待定")

    feasible_arms = tuple(
        arm
        for arm in authoritative_arms
        if all(
            arm[index] in visible_values[key]
            for index, key in enumerate(QUALITY_DISPLAY_ORDER)
        )
    )
    if not feasible_arms:
        return mark_unknown(
            f"完整联合臂与可见品质候选不相交；总件 {total_count} 下联动未知",
            authoritative_arms,
        )

    probability_by_quality: dict[str, dict[int, float]] = {}
    for key in QUALITY_DISPLAY_ORDER:
        quality = qualities[key]
        raw_rows = dict.get(quality, "count_candidates")
        probability_by_quality[key] = {
            dict.get(row, "value"): dict.get(row, "probability")
            for row in raw_rows
            if type(row) is dict  # noqa: E721
            and type(dict.get(row, "value")) is int  # noqa: E721
            and type(dict.get(row, "probability")) is float  # noqa: E721
            and dict.get(row, "probability") > 0.0
        } if type(raw_rows) is list else {}  # noqa: E721

    def arm_score(arm: tuple[int, ...]) -> float:
        score = 0.0
        for index, key in enumerate(QUALITY_DISPLAY_ORDER):
            probability = probability_by_quality[key].get(arm[index])
            if probability is None:
                return float("-inf")
            score += math.log(probability)
        return score

    conservative_arm = feasible_arms[0]
    balanced_arm = max(
        feasible_arms,
        key=lambda arm: (arm_score(arm), tuple(-value for value in arm)),
    )
    aggressive_arm = feasible_arms[-1]
    display_arms = (conservative_arm, balanced_arm, aggressive_arm)
    supported: dict[str, set[int]] = {
        key: {arm[index] for arm in feasible_arms}
        for index, key in enumerate(QUALITY_DISPLAY_ORDER)
    }

    global_partial = any(
        type(dict.get(qualities[key], "other_probability")) is float  # noqa: E721
        and dict.get(qualities[key], "other_probability") > 0.0
        for key in QUALITY_DISPLAY_ORDER
    ) or any(
        len(supported[key]) != len(visible_values[key])
        for key in QUALITY_DISPLAY_ORDER
    ) or len(feasible_arms) != len(authoritative_arms)

    # A red reference needs every complete arm, not merely a compatible subset,
    # and no unrepresented probability mass.  This separates the valid
    # non-unique q5/q6 linkage from malformed or cross-authority evidence.
    q6_pending_reference_complete = (
        q6_is_residual_projection
        and residual_count_conflict
        and not global_partial
    )

    for index, key in enumerate(QUALITY_DISPLAY_ORDER):
        quality = qualities[key]
        raw_rows = dict.get(quality, "count_candidates")
        compact_rows = [
            dict(row)
            for row in raw_rows
            if type(row) is dict  # noqa: E721
            and dict.get(row, "value") in supported[key]
        ] if type(raw_rows) is list else []  # noqa: E721
        own_other = dict.get(quality, "other_probability")
        independently_exact = dict.get(quality, "count_lock_value") is not None
        excluded_visible_value = len(supported[key]) != len(visible_values[key])
        if key == "q6" and residual_count_conflict:
            if not q6_pending_reference_complete:
                quality["compact_count_candidates"] = []
                quality["compact_count_triplet"] = []
                quality["count_correlation_state"] = "unknown"
                quality["count_correlation_note"] = (
                    f"总件 {total_count} 的完整品质件数联合臂不完整；红件联动待定"
                )
                continue
            # The residual count did not collapse to one value, but the valid
            # complete arms above still provide a strictly correlated compact
            # reference.  Keep it separate from a count lock: the q6 count
            # remains pending and the UI must say so explicitly.
            quality["compact_count_candidates"] = compact_rows
            quality["compact_count_triplet"] = [
                arm[index] for arm in display_arms
            ]
            quality["count_correlation_state"] = "complete"
            quality["count_correlation_note"] = (
                f"总件 {total_count} 与其余四品质件锁未形成唯一完整联合臂；"
                "红件待定；主卡按小/中/大显示可达红件值，不是件锁；"
                "完整联动臂见详情"
            )
            continue
        if (
            type(own_other) is float  # noqa: E721
            and own_other > 0.0
            and not independently_exact
            and not excluded_visible_value
        ):
            quality["compact_count_candidates"] = []
            quality["compact_count_triplet"] = []
            quality["count_correlation_state"] = "unknown"
            quality["count_correlation_note"] = (
                f"该品质仍有未展示件数概率；总件 {total_count} 下联动未知"
            )
            continue
        quality["compact_count_candidates"] = compact_rows
        quality["compact_count_triplet"] = [
            arm[index] for arm in display_arms
        ]
        quality["count_correlation_state"] = (
            "partial" if global_partial else "complete"
        )
        if key == "q6" and residual_count_lock is not None:
            quality["count_correlation_note"] = (
                f"总件 {total_count} 与其余四品质件锁闭合，红件残差 "
                f"{residual_count_lock}；红格仍按候选范围显示"
            )
        elif global_partial:
            quality["count_correlation_note"] = (
                f"保/均/激各取一条满足总件 {total_count} 的完整可见联合臂；"
                "低概率件数与其他品质相关，完整候选详见详情"
            )
        else:
            quality["count_correlation_note"] = (
                f"保/均/激完整品质件数臂均满足总件 {total_count}"
            )
    return (
        total_count,
        arms_for_wire(authoritative_arms),
        arms_for_wire(display_arms),
    )


def _quality_exact_count_facts(
    ref_result: Any,
    quality_composition: Any,
) -> dict[str, dict[str, Any]]:
    """Project independently trustworthy 100% count marginals for compact UI.

    A malformed joint block remains a root conflict and cannot publish posterior
    rows.  One valid count marginal can still be shown as a count-only fact (件锁)
    without claiming that its cell footprint or joint composition is complete.
    """
    if type(ref_result) is not dict:  # noqa: E721 - exact JSON object required
        return {}
    if any(type(raw_key) is not str for raw_key in ref_result):
        return {}
    status = dict.get(ref_result, "status")
    if type(status) is not str or status not in {"ok", "count_prior"}:  # noqa: E721
        return {}
    facts: dict[str, dict[str, Any]] = {}
    maria_counts = _maria_r1_low_tier_display_counts(
        ref_result,
        quality_composition,
    )
    if maria_counts is not None:
        facts.update(
            {
                "q1": {
                    "count": maria_counts[0],
                    "source": "maria_skill_count_exact",
                },
                "q3": {
                    "count": maria_counts[1],
                    "source": "maria_skill_count_exact",
                },
            }
        )
    for key in QUALITY_DISPLAY_ORDER:
        rows, error = _quality_marginal_candidates_payload(
            ref_result,
            key,
            "quality_count_candidates",
        )
        if error or len(rows) != 1:
            continue
        row = rows[0]
        value = row.get("value")
        probability = row.get("probability")
        if type(value) is int and value >= 0 and probability == 1.0:  # noqa: E721
            facts[key] = {
                "count": value,
                "source": "count_marginal_exact",
            }
    return facts


def _quality_joint_exact_facts(
    ref_result: Any,
) -> dict[str, dict[str, Any]]:
    """Project independently complete per-quality count/cells facts.

    The root composition is deliberately all-or-nothing: one malformed tier
    makes it a conflict.  A different tier's complete singleton joint remains
    useful as a current hard fact during same-session UI continuity, but only
    after its joint row, both marginals, singleton ranges, lock metadata, and
    engine evidence agree.  No cross-tier or legacy-range reconstruction is
    allowed here.
    """

    if type(ref_result) is not dict:  # noqa: E721 - exact JSON object required
        return {}
    if any(type(raw_key) is not str for raw_key in ref_result):
        return {}
    status = dict.get(ref_result, "status")
    if type(status) is not str or status not in {"ok", "count_prior"}:  # noqa: E721
        return {}
    raw_candidates = dict.get(ref_result, "quality_count_cells_candidates")
    raw_locks = dict.get(ref_result, "quality_lock")
    if type(raw_candidates) is not dict or type(raw_locks) is not dict:  # noqa: E721
        return {}
    if any(type(raw_key) is not str for raw_key in raw_candidates) or any(
        type(raw_key) is not str for raw_key in raw_locks
    ):
        return {}
    evidence = dict.get(ref_result, "evidence")
    if type(evidence) is not dict or any(  # noqa: E721
        type(raw_key) is not str for raw_key in evidence
    ):
        return {}
    fixed_counts = dict.get(evidence, "fixed_counts")
    quality_cells = dict.get(evidence, "quality_cells")
    if (
        fixed_counts is not None
        and (
            type(fixed_counts) is not dict
            or any(type(raw_key) is not str for raw_key in fixed_counts)
        )
    ) or (
        quality_cells is not None
        and (
            type(quality_cells) is not dict
            or any(type(raw_key) is not str for raw_key in quality_cells)
        )
    ):
        return {}

    facts: dict[str, dict[str, Any]] = {}
    for key in QUALITY_DISPLAY_ORDER:
        raw_rows = dict.get(raw_candidates, key)
        raw_lock = dict.get(raw_locks, key)
        if (
            type(raw_rows) not in (list, tuple)
            or len(raw_rows) != 1
            or type(raw_lock) is not dict
            or any(type(raw_key) is not str for raw_key in raw_lock)
        ):
            continue
        raw_row = raw_rows[0]
        if type(raw_row) is not dict or any(  # noqa: E721
            type(raw_key) is not str for raw_key in raw_row
        ):
            continue
        count = dict.get(raw_row, "count")
        cells = dict.get(raw_row, "cells")
        probability = _quality_candidate_probability(dict.get(raw_row, "prob"))
        if (
            type(count) is not int
            or count < 0
            or type(cells) is not int
            or cells < 0
            or probability != 1.0
        ):
            continue
        lock_strength = dict.get(raw_lock, "strength")
        lock_reason = dict.get(raw_lock, "reason")
        if (
            type(lock_strength) is not str
            or lock_strength.lower() != "exact"
            or type(lock_reason) is not str
            or lock_reason not in {"exact_lock", "derived_single_solution"}
        ):
            continue
        if type(fixed_counts) is dict and key in fixed_counts:  # noqa: E721
            if _strict_non_negative_whole(dict.get(fixed_counts, key)) != count:
                continue
        if type(quality_cells) is dict and key in quality_cells:  # noqa: E721
            if _strict_non_negative_whole(dict.get(quality_cells, key)) != cells:
                continue

        count_rows, count_error = _quality_marginal_candidates_payload(
            ref_result,
            key,
            "quality_count_candidates",
        )
        cells_rows, cells_error = _quality_marginal_candidates_payload(
            ref_result,
            key,
            "quality_cells_candidates",
        )
        if (
            count_error
            or cells_error
            or (
                count_rows
                and count_rows != [{"value": count, "probability": 1.0}]
            )
            or (
                cells_rows
                and cells_rows != [{"value": cells, "probability": 1.0}]
            )
        ):
            continue
        locked_count, locked_cells = _quality_dimension_lock_values(
            ref_result,
            key,
            lock_strength=lock_strength.lower(),
            lock_reason=lock_reason,
            joint_exact=True,
            joint_count=count,
            joint_cells=cells,
        )
        if locked_count != count or locked_cells != cells:
            continue
        facts[key] = {
            "count": count,
            "cells": cells,
            "source": "joint_exact",
        }
    return facts


def _quality_composition_conflict() -> dict[str, Any]:
    return {
        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": "conflict",
        "reason": "结构化组合字段冲突",
        "qualities": {},
    }


def _strict_non_negative_whole(value: Any) -> int | None:
    """Read a JSON number without accepting bool/string/subclass coercion."""
    if type(value) is int:  # noqa: E721 - exact JSON scalar is the trust boundary
        return value if value >= 0 else None
    if type(value) is float:  # noqa: E721
        return (
            int(value)
            if math.isfinite(value) and value >= 0 and value.is_integer()
            else None
        )
    return None


def _strict_locked_range_value(value: Any) -> int | None:
    """Accept only a plain, three-value, non-negative integral singleton range."""
    if type(value) not in (list, tuple) or len(value) != 3:  # noqa: E721
        return None
    parsed = tuple(_strict_non_negative_whole(item) for item in value)
    if any(item is None for item in parsed):
        return None
    return parsed[0] if parsed[0] == parsed[1] == parsed[2] else None


def _quality_dimension_lock_values(
    ref_result: dict[str, Any],
    key: str,
    *,
    lock_strength: str,
    lock_reason: str,
    joint_exact: bool,
    joint_count: int | None,
    joint_cells: int | None,
) -> tuple[int | None, int | None]:
    """Project engine-owned count/cell certainty as two independent facts.

    Percentile triples and truncated joint rows are display projections, not lock
    authority.  A dimension becomes exact only when its singleton range agrees
    with direct engine evidence, explicit count-lock metadata, or a complete
    singleton joint.  This lets a count-only public clue render ``件锁`` without
    pretending that its footprint is also known.
    """
    evidence = dict.get(ref_result, "evidence")
    if type(evidence) is not dict or any(  # noqa: E721
        type(raw_key) is not str for raw_key in evidence  # noqa: E721
    ):
        evidence = {}

    count_ranges = dict.get(ref_result, "quality_count_ranges")
    cells_ranges = dict.get(ref_result, "quality_cells_ranges")
    count_range_value = (
        _strict_locked_range_value(dict.get(count_ranges, key))
        if type(count_ranges) is dict  # noqa: E721
        and all(type(raw_key) is str for raw_key in count_ranges)  # noqa: E721
        else None
    )
    cells_range_value = (
        _strict_locked_range_value(dict.get(cells_ranges, key))
        if type(cells_ranges) is dict  # noqa: E721
        and all(type(raw_key) is str for raw_key in cells_ranges)  # noqa: E721
        else None
    )

    fixed_counts = dict.get(evidence, "fixed_counts")
    quality_cells = dict.get(evidence, "quality_cells")
    fixed_counts_valid = type(fixed_counts) is dict and all(  # noqa: E721
        type(raw_key) is str for raw_key in fixed_counts  # noqa: E721
    )
    quality_cells_valid = type(quality_cells) is dict and all(  # noqa: E721
        type(raw_key) is str for raw_key in quality_cells  # noqa: E721
    )
    fixed_counts_invalid = fixed_counts is not None and not fixed_counts_valid
    quality_cells_invalid = quality_cells is not None and not quality_cells_valid
    fixed_count_present = fixed_counts_valid and any(
        raw_key == key for raw_key in fixed_counts
    )
    fixed_cells_present = quality_cells_valid and any(
        raw_key == key for raw_key in quality_cells
    )
    direct_count = (
        _strict_non_negative_whole(dict.get(fixed_counts, key))
        if fixed_counts_valid
        else None
    )
    direct_cells = (
        _strict_non_negative_whole(dict.get(quality_cells, key))
        if quality_cells_valid
        else None
    )

    raw_total_count = dict.get(evidence, "total_count")
    raw_total_grid = dict.get(evidence, "total_grid_target")
    # Engine evidence keeps unavailable optional totals as JSON null.  Null is
    # absence, not a malformed authority value: it must not erase an otherwise
    # complete child-dimension lock.  Any non-null value is still an asserted
    # bound and therefore remains strict/fail-closed below.
    total_count_present = raw_total_count is not None
    total_grid_present = raw_total_grid is not None
    total_count = _strict_non_negative_whole(raw_total_count)
    total_grid = _strict_non_negative_whole(raw_total_grid)

    count_value: int | None = None
    if not fixed_counts_invalid and not (
        fixed_count_present and direct_count is None
    ):
        if direct_count is not None and direct_count == count_range_value:
            count_value = direct_count
        elif joint_exact and joint_count == count_range_value:
            count_value = joint_count

    cells_value: int | None = None
    if not quality_cells_invalid and not (
        fixed_cells_present and direct_cells is None
    ):
        if direct_cells is not None and direct_cells == cells_range_value:
            cells_value = direct_cells
        elif joint_exact and joint_cells == cells_range_value:
            cells_value = joint_cells

    # A malformed or exceeded total cannot authorize a child-dimension lock.
    if total_count_present and (
        total_count is None
        or (count_value is not None and count_value > total_count)
    ):
        count_value = None
    if total_grid_present and (
        total_grid is None
        or (cells_value is not None and cells_value > total_grid)
    ):
        cells_value = None
    return count_value, cells_value


def _quality_dimension_state_text(
    count_value: int | None,
    cells_value: int | None,
) -> str:
    if count_value is not None and cells_value is not None:
        return "锁定"
    if count_value is not None:
        return "件锁"
    if cells_value is not None:
        return "格锁"
    return "未锁"


def _quality_composition_payload(ref_result: Mapping[str, Any]) -> dict[str, Any]:
    """Project authoritative joint candidates into the detail-view contract.

    This is intentionally stricter than the compact legacy hover helper above:
    ranges and marginal candidates are never paired, and the legacy exact
    fallback is not accepted.  Only the engine's joint candidate field plus
    its matching lock metadata can produce a ready quality row.
    """
    # This is a security/display trust boundary: the native engine returns a
    # JSON-shaped plain dict.  Refuse subclasses before any get/str/float or
    # Mapping protocol can execute user-controlled behavior.
    if type(ref_result) is not dict:  # noqa: E721 - exact JSON object required
        return _quality_composition_conflict()
    if any(type(raw_key) is not str for raw_key in ref_result):
        return _quality_composition_conflict()
    raw_status = ref_result.get("status")
    if raw_status is None:
        source_status = "unavailable"
    elif type(raw_status) is not str:
        return _quality_composition_conflict()
    else:
        source_status = raw_status.lower()
    if source_status in {"no_reachable", "stale", "error"}:
        return {
            "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
            "status": source_status,
            "reason": {
                "no_reachable": "没有可达品质组合",
                "stale": "结构化组合已过期",
                "error": "品质组合计算异常",
            }[source_status],
            "qualities": {},
        }
    model_joint = _wuqilin_5215_joint_projection(ref_result)
    if source_status == "provisional_estimated" and not model_joint:
        return {
            "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
            "status": "unavailable",
            "reason": ("算法暂估提供品质区间" if _wuqilin_5215_provisional_notice(ref_result)
                       else "6001估算提供品质边际与件数区间；无官方联合组合"),
            "qualities": {},
        }
    if source_status not in {"ok", "count_prior"} and not (source_status == "provisional_estimated" and model_joint):
        return {
            "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
            "status": "unavailable",
            "reason": "等待引擎提供结构化组合",
            "qualities": {},
        }

    raw_candidates = ref_result.get("quality_count_cells_candidates")
    raw_locks = ref_result.get("quality_lock")
    if raw_candidates is None or raw_locks is None:
        return {
            "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
            "status": "unavailable",
            "reason": "等待引擎提供结构化组合",
            "qualities": {},
        }
    if type(raw_candidates) is not dict or type(raw_locks) is not dict:
        return _quality_composition_conflict()
    if any(type(raw_key) is not str for raw_key in raw_candidates) or any(
        type(raw_key) is not str for raw_key in raw_locks
    ):
        return _quality_composition_conflict()

    qualities: dict[str, dict[str, Any]] = {}
    has_ready = False

    def fractional_average_discrete_count_display(key: str) -> bool:
        """Project only an authoritative fractional-average presence bit to UI."""
        evidence = dict.get(ref_result, "evidence")
        if type(evidence) is not dict or any(  # noqa: E721 - exact JSON object
            type(raw_key) is not str for raw_key in evidence
        ):
            return False
        for field in ("avg_values", "avg_cells"):
            by_quality = dict.get(evidence, field)
            if type(by_quality) is not dict or any(  # noqa: E721
                type(raw_key) is not str for raw_key in by_quality
            ):
                continue
            value = dict.get(by_quality, key)
            if type(value) is float and math.isfinite(value) and not value.is_integer():
                return True
        return False

    def unavailable_quality(reason: str) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "reason": reason,
            "source": "",
            "lock_strength": "unavailable",
            "lock_reason": "",
            "candidates": [],
            "count_candidates": [],
            "cells_candidates": [],
            "truncated": False,
            "cap_hit": False,
            "pricing_posterior": False,
            "display_reconciled": False,
            "count_lock_value": None,
            "cells_lock_value": None,
            "dimension_state": "未锁",
            "discrete_count_display": False,
        }

    for key in QUALITY_DISPLAY_ORDER:
        raw_rows = raw_candidates.get(key)
        raw_lock = raw_locks.get(key)
        if raw_rows is None and raw_lock is None:
            qualities[key] = {
                "status": "unavailable",
                "reason": "等待结构化组合数据",
                "source": "",
                "lock_strength": "unavailable",
                "lock_reason": "",
                "candidates": [],
                "count_candidates": [],
                "cells_candidates": [],
                "truncated": False,
                "cap_hit": False,
                "pricing_posterior": False,
                "display_reconciled": False,
                "count_lock_value": None,
                "cells_lock_value": None,
                "dimension_state": "未锁",
                "discrete_count_display": False,
            }
            continue

        if type(raw_rows) not in (list, tuple) or type(raw_lock) is not dict:
            return _quality_composition_conflict()
        if any(type(raw_key) is not str for raw_key in raw_lock):
            return _quality_composition_conflict()
        raw_lock_strength = raw_lock.get("strength")
        raw_lock_reason = raw_lock.get("reason")
        if type(raw_lock_strength) is not str or (
            raw_lock_reason is not None and type(raw_lock_reason) is not str
        ):
            return _quality_composition_conflict()
        lock_strength = raw_lock_strength.lower()
        lock_reason = raw_lock_reason or ""
        if lock_strength not in {"exact", "probabilistic"}:
            return _quality_composition_conflict()

        candidates: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        shown_probability_terms: list[float] = []
        for raw_row in raw_rows:
            if type(raw_row) is not dict:
                return _quality_composition_conflict()
            if any(type(raw_key) is not str for raw_key in raw_row):
                return _quality_composition_conflict()
            count = raw_row.get("count")
            cells = raw_row.get("cells")
            probability = _quality_candidate_probability(raw_row.get("prob"))
            if (
                type(count) is not int
                or count < 0
                or type(cells) is not int
                or cells < 0
                or probability is None
            ):
                q6_lock = dict.get(raw_locks, "q6")
                q6_is_residual_projection = (
                    type(q6_lock) is dict  # noqa: E721
                    and dict.get(q6_lock, "reason") == "derived_single_solution"
                )
                if key != "q6" and q6_is_residual_projection:
                    # A non-red malformed row invalidates the q6 residual
                    # authority, but must not turn the valid red row into a
                    # root "组合冲突" display.
                    qualities[key] = unavailable_quality(
                        "品质件数 authority 类型无效"
                    )
                    break
                return _quality_composition_conflict()
            if (count == 0) != (cells == 0):
                qualities[key] = unavailable_quality(
                    "品质件格物理组合待定"
                )
                break
            identity = (count, cells)
            if identity in seen:
                return _quality_composition_conflict()
            seen.add(identity)
            shown_probability_terms.append(probability)
            candidates.append(
                {
                    "count": count,
                    "cells": cells,
                    "probability": probability,
                }
            )

        if key in qualities:
            continue

        # Candidate probabilities are already projected to four decimal places.
        # Ordinary left-to-right addition can still turn an exact visible mass
        # of 1.0 into 1.0000000000000002.  The producer accepts a 1e-6 numeric
        # tolerance below, while the strict consumer correctly rejects any wire
        # probability above 1.0.  Use stable summation so both trust boundaries
        # receive the same canonical value instead of blanking the whole view.
        shown_probability = math.fsum(shown_probability_terms)

        count_candidates, count_candidates_conflict = (
            _quality_marginal_candidates_payload(
                ref_result,
                key,
                "quality_count_candidates",
            )
        )
        cells_candidates, cells_candidates_conflict = (
            _quality_marginal_candidates_payload(
                ref_result,
                key,
                "quality_cells_candidates",
            )
        )
        if (
            count_candidates_conflict
            or cells_candidates_conflict
            or not candidates
            or shown_probability > 1.000001
        ):
            return _quality_composition_conflict()

        has_ready = True
        other_probability = max(0.0, 1.0 - shown_probability)
        joint_exact = (
            lock_strength == "exact"
            and len(candidates) == 1
            and candidates[0]["probability"] == 1.0
            and other_probability == 0.0
        )
        visible_count_candidates = _visible_joint_marginal_candidates(
            candidates,
            "count",
        )
        visible_cells_candidates = _visible_joint_marginal_candidates(
            candidates,
            "cells",
        )
        display_reconciled = False
        if joint_exact:
            if (
                count_candidates
                and count_candidates != visible_count_candidates
            ) or (
                cells_candidates
                and cells_candidates != visible_cells_candidates
            ):
                return _quality_composition_conflict()
            if not count_candidates:
                count_candidates = visible_count_candidates
            if not cells_candidates:
                cells_candidates = visible_cells_candidates
        else:
            count_candidates, count_reconciled = _reconcile_visible_joint_marginal(
                count_candidates,
                visible_count_candidates,
            )
            cells_candidates, cells_reconciled = _reconcile_visible_joint_marginal(
                cells_candidates,
                visible_cells_candidates,
            )
            display_reconciled = count_reconciled or cells_reconciled
        effective_lock_strength = "exact" if joint_exact else "probabilistic"
        effective_lock_reason = (
            lock_reason
            if joint_exact or lock_strength == "probabilistic"
            else (
                "exact_lock_has_other_mass"
                if len(candidates) == 1 and other_probability > 0.0
                else "count_exact_joint_multi_solution"
            )
        )
        count_lock_value, cells_lock_value = _quality_dimension_lock_values(
            ref_result,
            key,
            lock_strength=lock_strength,
            lock_reason=lock_reason,
            joint_exact=joint_exact,
            joint_count=(candidates[0]["count"] if joint_exact else None),
            joint_cells=(candidates[0]["cells"] if joint_exact else None),
        )
        # An independent exact dimension may survive a probabilistic joint only
        # when every visible joint arm agrees with it.  Raw exact lock metadata
        # alone is not independent authority once other posterior mass exists.
        if count_lock_value is not None and any(
            candidate["count"] != count_lock_value for candidate in candidates
        ):
            count_lock_value = None
        if cells_lock_value is not None and any(
            candidate["cells"] != cells_lock_value for candidate in candidates
        ):
            cells_lock_value = None
        qualities[key] = {
            "status": "exact" if joint_exact else "ready",
            "source": "pricing_posterior",
            "lock_strength": effective_lock_strength,
            "lock_reason": effective_lock_reason,
            "candidates": candidates,
            "count_candidates": count_candidates,
            "cells_candidates": cells_candidates,
            "shown_probability": shown_probability,
            "other_probability": other_probability,
            "truncated": other_probability > 0.0,
            # The current engine has no structured per-quality cap flag.
            # Do not infer one from notes or combo_count.
            "cap_hit": False,
            "pricing_posterior": True,
            "display_reconciled": display_reconciled,
            "count_lock_value": count_lock_value,
            "cells_lock_value": cells_lock_value,
            "dimension_state": _quality_dimension_state_text(
                count_lock_value,
                cells_lock_value,
            ),
            "discrete_count_display": fractional_average_discrete_count_display(key),
            "reason": "",
        }

    # Independently exact count sources remain useful in a probabilistic joint,
    # but only when every visible joint arm agrees.  Maria's split/coarse skill
    # contract and a complete 100% count marginal are such sources; raw joint
    # lock metadata by itself is not.
    provisional_composition = {
        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": "ready" if has_ready else "unavailable",
        "qualities": qualities,
    }
    maria_counts = _maria_r1_low_tier_display_counts(
        ref_result,
        provisional_composition,
    )
    independent_counts: dict[str, int] = {}
    if maria_counts is not None:
        independent_counts.update({"q1": maria_counts[0], "q3": maria_counts[1]})
    for key in QUALITY_DISPLAY_ORDER:
        rows, error = _quality_marginal_candidates_payload(
            ref_result,
            key,
            "quality_count_candidates",
        )
        if (
            not error
            and len(rows) == 1
            and rows[0]["probability"] == 1.0
            and (not model_joint or key in model_joint.get('exact_counts',{}))
        ):
            independent_counts[key] = rows[0]["value"]
    for key, independent_count in independent_counts.items():
        quality = qualities.get(key)
        if type(quality) is not dict:  # noqa: E721
            continue
        candidates = dict.get(quality, "candidates")
        if (
            type(candidates) is list
            and candidates
            and all(
                type(candidate) is dict  # noqa: E721
                and dict.get(candidate, "count") == independent_count
                for candidate in candidates
            )
        ):
            quality["count_lock_value"] = independent_count
            quality["dimension_state"] = _quality_dimension_state_text(
                independent_count,
                dict.get(quality, "cells_lock_value"),
            )

    (
        count_correlation_total,
        authoritative_count_arms,
        compact_count_arms,
    ) = (
        _project_total_constrained_count_candidates(ref_result, qualities)
    )

    if has_ready:
        root_status = "ready"
        root_reason = "结构化联合候选"
    else:
        root_status = "unavailable"
        root_reason = "等待引擎提供结构化组合"
    payload = {
        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": root_status,
        "reason": root_reason,
        "qualities": qualities,
    }
    if count_correlation_total is not None:
        payload["count_correlation_total"] = count_correlation_total
        payload["authoritative_count_arms"] = authoritative_count_arms
        payload["compact_count_arms"] = compact_count_arms
    return payload


def _candidate_tail_text(probabilities: list[float]) -> str:
    remainder = max(0.0, 1.0 - sum(probabilities))
    return f" · 其他 {_probability_text(remainder)}" if remainder >= 0.005 else ""


def _provisional_quality_mass(ref_result: Any) -> dict[str, float]:
    """Return the generation-bound 6001 quality marginal, never a joint claim."""

    if type(ref_result) is not dict:  # noqa: E721 - exact engine JSON only
        return {}
    receipt = dict.get(ref_result, "provisional_quote")
    if (
        dict.get(ref_result, "status") != "provisional_estimated"
        or type(receipt) is not dict  # noqa: E721
        or dict.get(receipt, "official_distribution_authority") is not False
        or dict.get(receipt, "official_quote_authority") is not False
        or dict.get(receipt, "provisional_distribution_available") is not True
        or dict.get(receipt, "provisional_quote_available") is not True
    ):
        return {}
    raw_mass = dict.get(receipt, "primary_quality_mass")
    if type(raw_mass) is not dict or set(raw_mass) != set(QUALITY_DISPLAY_ORDER):  # noqa: E721
        return {}
    if any(
        type(dict.get(raw_mass, key)) is not float  # noqa: E721
        or not math.isfinite(dict.get(raw_mass, key))
        or not 0.0 <= dict.get(raw_mass, key) <= 1.0
        for key in QUALITY_DISPLAY_ORDER
    ):
        return {}
    if not math.isclose(
        sum(dict.get(raw_mass, key) for key in QUALITY_DISPLAY_ORDER),
        1.0,
        rel_tol=0.0,
        abs_tol=2e-12,
    ):
        return {}
    return {key: dict.get(raw_mass, key) for key in QUALITY_DISPLAY_ORDER}


def _quality_probability_summary(
    ref_result: dict[str, Any], quality_order: tuple[str, ...] = ("q5", "q6")
) -> str:
    """Compact top joint state for the always-visible 金/红率 row."""
    provisional_mass = _provisional_quality_mass(ref_result)
    if provisional_mass:
        return "·".join(
            f"{QUALITY_LABELS.get(key, key)}{_probability_text(provisional_mass[key])}"
            for key in quality_order
            if key in provisional_mass
        )
    parts: list[str] = []
    for key in quality_order:
        rows = _joint_quality_candidates(ref_result, key)
        if not rows:
            continue
        count, cells, prob = rows[0]
        parts.append(
            f"{QUALITY_LABELS.get(key, key)}{count}件{cells}格{_probability_text(prob)}"
        )
    return "·".join(parts) if parts else "-"


def _quality_probability_hover(
    ref_result: dict[str, Any], quality_order: tuple[str, ...] = ("q5", "q6")
) -> str:
    """Detailed joint candidates; count and cells stay paired within each feasible state."""
    provisional_mass = _provisional_quality_mass(ref_result)
    if provisional_mass:
        body = " · ".join(
            f"{QUALITY_LABELS.get(key, key)}品 {_probability_text(provisional_mass[key])}"
            for key in quality_order
            if key in provisional_mass
        )
        return f"6001 估算品质边际：{body}；非官方联合组合" if body else ""
    quality_parts: list[str] = []
    for key in quality_order:
        rows = _joint_quality_candidates(ref_result, key)
        if not rows:
            continue
        candidate_text = " · ".join(
            f"{count}件/{cells}格 {_probability_text(prob)}"
            for count, cells, prob in rows
        )
        candidate_text += _candidate_tail_text([prob for _count, _cells, prob in rows])
        quality_parts.append(f"{QUALITY_LABELS.get(key, key)}品：{candidate_text}")
    detail = "；".join(quality_parts)
    notes = ref_result.get("notes")
    if detail and isinstance(notes, (list, tuple)) and any(
        str(note).startswith("grid_identity_reconciled:") for note in notes
    ):
        detail += "；注：联合概率按报价候选，格数主行已做空间对账"
    return detail


def _quality_dimension_probability_hover(
    ref_result: dict[str, Any], key: str, dimension: str
) -> str:
    field = "quality_count_candidates" if dimension == "count" else "quality_cells_candidates"
    unit = "件" if dimension == "count" else "格"
    candidates = ref_result.get(field)
    raw_rows = candidates.get(key) if isinstance(candidates, dict) else None
    rows: list[tuple[int, float]] = []
    if isinstance(raw_rows, (list, tuple)):
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            value = _parse_int(raw.get("value"))
            prob = _quality_candidate_probability(raw.get("prob"))
            if value is not None and prob is not None:
                rows.append((value, prob))
    if not rows:
        range_field = "quality_count_ranges" if dimension == "count" else "quality_cells_ranges"
        ranges = ref_result.get(range_field)
        locked = _locked_range_value(ranges.get(key)) if isinstance(ranges, dict) else None
        if locked is not None:
            rows = [(locked, 1.0)]
    if not rows:
        return ""
    rows.sort(key=lambda row: (-row[1], row[0]))
    body = " · ".join(f"{value}{unit} {_probability_text(prob)}" for value, prob in rows)
    body += _candidate_tail_text([prob for _value, prob in rows])
    return f"{QUALITY_LABELS.get(key, key)}{unit}候选：{body}"


def _low_quality_count_probability_hover(ref_result: dict[str, Any]) -> str:
    """Low-tier count marginals for the compact 白绿/蓝 summary hover.

    Counts deliberately use the engine's marginal candidates instead of pairing them with an
    independent cells marginal.  A missing candidate list falls back to 100% only when the
    displayed count range is a true one-state lock.
    """
    parts = [
        _quality_dimension_probability_hover(ref_result, key, "count")
        for key in LOW_QUALITY_DISPLAY_ORDER
    ]
    return "；".join(part for part in parts if part)


def _purple_gold_tier_display_text(
    key: str,
    *,
    count_range: Any,
    cells_ranges: dict[str, Any] | None,
    evidence: dict[str, Any],
) -> str | None:
    """Purple/gold readout: every hero gets both count and cell state when available."""
    label = QUALITY_LABELS.get(key, key)
    locked_count = _locked_range_value(count_range)
    cells_text = "-"
    if isinstance(cells_ranges, dict):
        cells_text = _ethan_cells_text(cells_ranges.get(key))
    prefix = "约" if _purple_estimate_only(key, evidence) else ""
    if locked_count is not None:
        cells_locked = None
        if isinstance(cells_ranges, dict):
            cells_locked = _locked_range_value(cells_ranges.get(key))
        if cells_locked is None:
            quality_cells = evidence.get("quality_cells")
            if isinstance(quality_cells, dict):
                cells_locked = _parse_int(quality_cells.get(key))
        if cells_locked is not None:
            return f"{label}{locked_count}件/{cells_locked}格"
        if cells_text != "-":
            return f"{label}{locked_count}件 {prefix}{cells_text}格"
        return f"{label}{locked_count}件"
    text = _range_text(count_range).replace(" / ", "/")
    if text == "-":
        return None
    if cells_text != "-":
        return f"{label}件 {text} {prefix}{cells_text}格"
    return f"{label}件 {text}"


def _purple_gold_quality_summary(ref_result: dict[str, Any]) -> str:
    ranges = ref_result.get("quality_count_ranges")
    if not isinstance(ranges, dict):
        return "-"
    evidence = ref_result.get("evidence") if isinstance(ref_result.get("evidence"), dict) else {}
    cells_ranges = ref_result.get("quality_cells_ranges")
    if not isinstance(cells_ranges, dict):
        cells_ranges = {}
    parts: list[str] = []
    for key in ("q4", "q5"):
        tier_text = _purple_gold_tier_display_text(
            key,
            count_range=ranges.get(key),
            cells_ranges=cells_ranges,
            evidence=evidence,
        )
        if tier_text:
            parts.append(tier_text)
    return " · ".join(parts) if parts else "-"


def _purple_gold_count_summary(
    counts: dict[str, Any],
    cells: dict[str, Any] | None = None,
) -> str:
    if not isinstance(counts, dict) or not counts:
        return "-"
    cell_map = cells if isinstance(cells, dict) else {}
    parts: list[str] = []
    for key, label in (("q4", "紫"), ("q5", "金")):
        count = _parse_int(counts.get(key))
        if count is None:
            continue
        cell_count = _parse_int(cell_map.get(key))
        if cell_count is not None:
            parts.append(f"{label}{count}件/{cell_count}格")
        else:
            parts.append(f"{label}{count}件")
    return " · ".join(parts) if parts else "-"


def _known_purple_gold_summary(constraints: dict[str, Any]) -> str:
    if not isinstance(constraints, dict) or not constraints:
        return "-"
    summary = constraints.get("summary") if isinstance(constraints.get("summary"), dict) else {}
    counts = constraints.get("counts") if isinstance(constraints.get("counts"), dict) else {}
    known_counts = (
        counts.get("known_quality_counts")
        if isinstance(counts.get("known_quality_counts"), dict)
        else {}
    )
    q4 = _parse_int(summary.get("known_purple_item_count"))
    if q4 is None:
        q4 = _parse_int(known_counts.get("q4"))
    q5 = _parse_int(summary.get("known_gold_item_count"))
    if q5 is None:
        q5 = _parse_int(known_counts.get("q5"))
    return _purple_gold_count_summary({"q4": q4, "q5": q5})


def _snapshot_age_seconds(
    snapshot: dict[str, Any],
    snapshot_path: Path,
    source: dict[str, Any],
) -> float | None:
    candidates: list[float] = []
    for value in (snapshot.get("created_at"), source.get("created_at")):
        if value in (None, ""):
            continue
        try:
            candidates.append(time.time() - float(value))
        except (TypeError, ValueError):
            pass
    try:
        candidates.append(time.time() - snapshot_path.stat().st_mtime)
    except OSError:
        pass
    if not candidates:
        return None
    return max(0.0, min(candidates))


def _capture_status(snapshot_path: Path) -> dict[str, Any]:
    status_path = snapshot_path.parent / "capture_source_status.json"
    try:
        payload = _read_json(status_path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _capture_status_age_seconds(capture_status: dict[str, Any]) -> float | None:
    try:
        return time.time() - float(capture_status.get("ts"))
    except (TypeError, ValueError):
        return None


def _capture_session_id(capture_status: dict[str, Any]) -> str:
    return _text(capture_status.get("active_session_id"), "").strip()


def should_keep_last_quote_on_age_stale(
    *,
    phase: str,
    is_age_stale: bool,
    capture_session_ahead: bool,
    monitor_restarted: bool,
) -> bool:
    """Same-session age stale during reading/bidding: re-run on frozen snapshot, keep quotes."""
    if os.environ.get(HERO_REF_DISABLE_STALE_KEEP_LAST_QUOTE) == "1":
        return False
    if not is_age_stale or capture_session_ahead or monitor_restarted:
        return False
    return _text(phase, "").strip().lower() in STALE_KEEP_LAST_QUOTE_PHASES


def capture_session_ahead_of_snapshot(
    capture_status: Mapping[str, Any],
    *,
    snapshot_session_id: str,
    recent_seconds: float = CAPTURE_SESSION_RECENT_SECONDS,
) -> bool:
    """True when live capture has switched to a newer game session.

    Used by summarize_snapshot and the overlay refresh loop so a settled
    ``latest_snapshot.json`` from the prior game does not keep rendering after
    ``reset_rows`` until a new inference artifact is written.
    """
    capture_session_id = _capture_session_id(dict(capture_status))
    if not capture_session_id:
        return False
    capture_age = _capture_status_age_seconds(dict(capture_status))
    if capture_age is None or capture_age > recent_seconds:
        return False
    prior_session_id = _text(snapshot_session_id, "").strip()
    if not prior_session_id:
        return False
    return capture_session_id != prior_session_id


def _monitor_lock(snapshot_path: Path) -> dict[str, Any]:
    lock_path = snapshot_path.parent / "monitor.lock"
    try:
        payload = _read_json(lock_path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _monitor_started_after_snapshot(
    lock_payload: dict[str, Any],
    snapshot_path: Path,
    snapshot: dict[str, Any],
    source: dict[str, Any],
) -> bool:
    try:
        started_at = float(lock_payload.get("started_at"))
    except (TypeError, ValueError):
        return False
    snapshot_markers: list[float] = []
    for value in (snapshot.get("created_at"), source.get("created_at")):
        try:
            snapshot_markers.append(float(value))
        except (TypeError, ValueError):
            pass
    try:
        snapshot_markers.append(snapshot_path.stat().st_mtime)
    except OSError:
        pass
    return bool(snapshot_markers) and started_at > max(snapshot_markers) + 1.0


def _stale_snapshot_payload(
    *,
    snapshot_path: Path,
    age_seconds: float | None,
    phase: str,
    context: dict[str, Any],
    snapshot: dict[str, Any],
    uc: dict[str, Any],
    reason: str = "stale_snapshot",
) -> dict[str, Any]:
    age_text = f"{int(age_seconds)}s" if age_seconds is not None else "unknown"
    return {
        "status": "stale_snapshot",
        "snapshot_path": str(snapshot_path),
        "updated_at_text": time.strftime("%H:%M:%S"),
        "context": {
            "hero": context.get("hero") or snapshot.get("hero") or "?",
            "is_ahmed": False,
            "is_supported_ref_hero": False,
            "map_id": context.get("map_id") or snapshot.get("map_id"),
            "model_map_id": context.get("model_map_id"),
            "round": context.get("round") or snapshot.get("round"),
            "action_round": context.get("action_round") or snapshot.get("action_round"),
            "phase": phase or "?",
            "session_id": context.get("session_id") or snapshot.get("session_id"),
            "file": None,
        },
        "reference": {
            "label": "Hero Ref",
            "source": "standby",
            "readiness": "standby",
            "note": (
                "new live session detected; waiting for first inference snapshot."
                if reason == "session_ahead"
                else "monitor restarted; waiting for first fresh inference snapshot."
                if reason == "monitor_restarted"
                else f"latest_snapshot is stale ({age_text}); waiting for the next live packet."
            ),
            "conservative": "-",
            "balanced": "-",
            "aggressive": "-",
            "raw_value_range": "-",
            "v3_conservative": "-",
            "v3_balanced": "-",
            "v3_aggressive": "-",
            "ref_minus_v3_balanced": "-",
            "ref_minus_v3_balanced_raw": None,
            "action": "等待新局",
            "risk_band": "-",
            "current_highest": "-",
            "decision_range": "-",
            "total_value_range": "-",
        },
        "red": {
            "count_range": "-",
            "cells_range": "-",
            "value_range": "-",
            "quality_count_summary": "-",
            "quality_probability_summary": "-",
            "count_probability_hover": "",
            "cells_probability_hover": "",
            "purple_gold_probability_hover": "",
            "quality_probability_hover": "",
            "low_quality_count_probability_hover": "",
            "uncertainty_summary": "-",
            "prior_rate": "-",
            "sample_rate": "-",
            "risk_reference": "",
        },
        "evidence": {
            "match_text": "-",
            "information_density": "-",
            "diagnostics": "",
            "latest_sent": {},
            "latest_result": {},
            "public_constraint_key": "",
            "evidence_profile_key": "",
            "source_mode": "standby",
            "ref_status": "standby",
            "ref_readiness": "standby",
            "ref_combo_count": "",
            "ref_input_summary": "-",
            "ref_notes": "",
        },
        "truth": {
            "available": False,
            "total_value": None,
            "total_items": None,
            "total_cells": None,
            "q6": {},
            "top_item": {},
        },
        "ahmed_ref": {"status": "standby", "source": "standby", "notes": []},
        "minimap": _minimap_summary({}, {}),
        "flags": [
            _flag("待机", "neutral", "waiting for next live packet"),
            _flag(
                "新局等待"
                if reason == "session_ahead"
                else "等待首包"
                if reason == "monitor_restarted"
                else "旧快照",
                "watch",
                f"phase={phase or '?'} age={age_text}",
            ),
        ],
        "stale": {
            "age_seconds": age_seconds,
            "phase": phase,
            "reason": reason,
        },
    }


def summarize_snapshot(
    snapshot: dict[str, Any],
    *,
    snapshot_path: Path,
    defer_settlement_estimate: bool = False,
) -> dict[str, Any]:
    if type(defer_settlement_estimate) is not bool:  # noqa: E721
        raise TypeError("defer_settlement_estimate must be a builtin bool")
    summary_started = time.perf_counter()
    snapshot = stabilize_reference_engine_external_snapshot(snapshot)
    raw_ui_contract = (
        snapshot.get("ui_contract")
        if isinstance(snapshot.get("ui_contract"), dict)
        else {}
    )
    raw_context = (
        raw_ui_contract.get("context")
        if isinstance(raw_ui_contract.get("context"), dict)
        else {}
    )
    raw_public_info = (
        raw_ui_contract.get("constraints", {}).get("public_info", {})
        if isinstance(raw_ui_contract.get("constraints"), dict)
        else {}
    )
    raw_phase = _text(raw_context.get("phase") or snapshot.get("phase"), "?")
    raw_settlement_truth = _settlement_truth_snapshot(
        dict.get(raw_ui_contract, "truth")
        if type(raw_ui_contract) is dict  # noqa: E721 - no subclass hooks
        else None
    )
    raw_hero_key = _snapshot_hero_key(snapshot, raw_context)
    raw_round_no = _parse_int(raw_context.get("round") or snapshot.get("round"))
    raw_aisha_readiness = (False, False)
    if raw_round_no is not None and _aisha_should_dual_pass(
        raw_hero_key,
        raw_round_no,
        raw_phase,
    ):
        raw_skill_ready = _aisha_round_skill_frame_ready(snapshot, int(raw_round_no))
        raw_aisha_readiness = (
            raw_skill_ready,
            not raw_skill_ready
            and _aisha_r1_public_provisional_ready(
                snapshot,
                int(raw_round_no),
                raw_public_info,
            ),
        )
    # Stabilize scoped public avg-cell values before any dual-pass JSON clone or
    # UI/mini consumer can normalize a scalar subclass into an allowed builtin.
    snapshot = prepare_reference_engine_snapshot(snapshot)
    ref_engine_ms: float | None = None
    ref_engine_skill_pass_ms: float | None = None
    ref_engine_item_pass_ms: float | None = None
    settlement_ref_engine_ms: float | None = None
    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    context = uc.get("context") if isinstance(uc.get("context"), dict) else {}
    baseline = uc.get("baseline") if isinstance(uc.get("baseline"), dict) else {}
    decision = baseline.get("decision") if isinstance(baseline.get("decision"), dict) else {}
    posterior = baseline.get("posterior") if isinstance(baseline.get("posterior"), dict) else {}
    diagnostics = uc.get("diagnostics") if isinstance(uc.get("diagnostics"), dict) else {}
    constraints = uc.get("constraints") if isinstance(uc.get("constraints"), dict) else {}
    public_info = constraints.get("public_info") if isinstance(constraints.get("public_info"), dict) else {}
    truth = uc.get("truth") if isinstance(uc.get("truth"), dict) else {}
    if not truth and raw_phase.strip().lower() == "settled":
        truth = raw_settlement_truth
    actions = uc.get("actions") if isinstance(uc.get("actions"), dict) else {}
    source = uc.get("source") if isinstance(uc.get("source"), dict) else {}
    warehouse_source = (
        context.get("warehouse_source")
        if isinstance(context.get("warehouse_source"), dict)
        else source.get("warehouse_source")
        if isinstance(source.get("warehouse_source"), dict)
        else snapshot.get("warehouse_source")
        if isinstance(snapshot.get("warehouse_source"), dict)
        else {}
    )
    warehouse_qualification = _text(
        context.get("warehouse_source_qualification")
        or source.get("warehouse_source_qualification")
        or snapshot.get("warehouse_source_qualification")
        or warehouse_source.get("qualification"),
        "",
    )
    phase = _text(context.get("phase") or snapshot.get("phase"), "?")
    snapshot_session_id = _text(context.get("session_id") or snapshot.get("session_id"), "").strip()
    capture_status = _capture_status(snapshot_path)
    lock_payload = _monitor_lock(snapshot_path)
    monitor_restarted = _monitor_started_after_snapshot(
        lock_payload,
        snapshot_path,
        snapshot,
        source,
    )
    capture_session_ahead = capture_session_ahead_of_snapshot(
        capture_status,
        snapshot_session_id=snapshot_session_id,
    )
    age_seconds = _snapshot_age_seconds(snapshot, snapshot_path, source)
    is_age_stale = (
        age_seconds is not None
        and age_seconds >= STALE_SNAPSHOT_SECONDS
        and phase != "settled"
    )
    keep_last_quote_on_age_stale = should_keep_last_quote_on_age_stale(
        phase=phase,
        is_age_stale=is_age_stale,
        capture_session_ahead=capture_session_ahead,
        monitor_restarted=monitor_restarted,
    )
    if capture_session_ahead or monitor_restarted or (
        is_age_stale and not keep_last_quote_on_age_stale
    ):
        return _stale_snapshot_payload(
            snapshot_path=snapshot_path,
            age_seconds=age_seconds,
            phase=phase,
            context=context,
            snapshot=snapshot,
            uc=uc,
            reason=(
                "session_ahead"
                if capture_session_ahead
                else "monitor_restarted"
                if monitor_restarted
                else "stale_snapshot"
            ),
        )
    hero_key_early = _snapshot_hero_key(snapshot, context)
    round_no_early = _parse_int(context.get("round") or snapshot.get("round"))
    aisha_dual_pass = _aisha_should_dual_pass(
        hero_key_early,
        round_no_early,
        phase,
    )
    hero_scheduled_pass = (
        not aisha_dual_pass
        and hero_should_run_scheduled_inference(hero_key_early, round_no_early, phase)
    )
    if aisha_dual_pass:
        ref_started = time.perf_counter()
        ref_result, pass_timing = _aisha_dual_pass_ref_result(
            snapshot,
            round_no=int(round_no_early),
            public_info=public_info,
            raw_readiness=raw_aisha_readiness,
        )
        ref_engine_skill_pass_ms = pass_timing.get("skill")
        ref_engine_item_pass_ms = pass_timing.get("item")
        ref_engine_ms = round((time.perf_counter() - ref_started) * 1000.0, 2)
    elif hero_scheduled_pass and round_no_early is not None:
        ref_started = time.perf_counter()
        ref_result = _hero_single_pass_ref_result(
            snapshot,
            hero_key=hero_key_early,
            round_no=int(round_no_early),
            public_info=public_info,
        )
        ref_engine_ms = round((time.perf_counter() - ref_started) * 1000.0, 2)
    elif run_reference_engine is not None:
        ref_started = time.perf_counter()
        try:
            ref_result = run_reference_engine(
                prepare_reference_engine_snapshot(snapshot),
                max_combos=hero_max_combos_for_round(hero_key_early, round_no_early),
            ).as_dict()
        except Exception as exc:  # noqa: BLE001 - prototype diagnostics
            ref_result = {
                "status": "error",
                "source": "ref_v0",
                "notes": [str(exc)],
            }
        finally:
            ref_engine_ms = round((time.perf_counter() - ref_started) * 1000.0, 2)
    else:
        ref_result = {"status": "unavailable", "source": "ref_v0", "notes": []}
    quality_composition = _quality_composition_payload(ref_result)
    current_joint_exact_continuity_enabled = (
        os.environ.get(CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV) != "1"
    )
    current_joint_exact_continuity_publishable = (
        current_joint_exact_continuity_enabled and not is_age_stale
    )
    quality_joint_exact_facts = (
        _quality_joint_exact_facts(ref_result)
        if phase in {"reading", "bidding"}
        and current_joint_exact_continuity_publishable
        else {}
    )
    quality_composition_conflict = quality_composition.get("status") == "conflict"
    if quality_composition_conflict:
        # The canonical composition producer and strict per-quality exact-fact
        # projector are the only readers of an untrusted engine mapping.  Once
        # the root producer rejects the structure, replace the whole raw result;
        # only already-validated independent facts may survive into summary/API/Tk.
        ref_result = {
            "status": "conflict",
            "source": "ref_v0",
            "notes": ["quality_composition_conflict"],
        }
    ref_evidence = ref_result.get("evidence") if isinstance(ref_result.get("evidence"), dict) else {}

    hero_key = _snapshot_hero_key(snapshot, context)
    hero = hero_key or _hero_from_context(context.get("hero") or snapshot.get("hero"))
    if _is_unknown_hero(hero):
        evidence_hero = _hero_from_context(ref_evidence.get("hero"))
        if not _is_unknown_hero(evidence_hero):
            hero = evidence_hero
            hero_key = normalize_hero_key(hero)
    is_supported = is_supported_ref_hero(hero)
    round_no = _parse_int(context.get("round") or snapshot.get("round"))

    info_band = _text(
        public_info.get("information_density_band")
        or _dig(snapshot, "model_eval", "information_density_band"),
        "",
    )
    flags: list[dict[str, str]] = []
    drop_pool_support = (
        snapshot.get("drop_pool_support")
        if isinstance(snapshot.get("drop_pool_support"), dict)
        else {}
    )
    rankmap_distribution_support = (
        snapshot.get("rankmap_distribution_support")
        if isinstance(snapshot.get("rankmap_distribution_support"), dict)
        else {}
    )
    drop_pool_support_degraded = (
        drop_pool_support.get("status")
        == "legacy_incomplete_conditional_known_support"
        and drop_pool_support.get("quote_quality") == "degraded"
    )
    if drop_pool_support_degraded:
        flags.append(
            _flag(
                "掉落资料降级",
                "watch",
                "同代历史池有已知缺叶；报价按已知支持计算，残余概率已记录",
            )
        )
    if rankmap_distribution_support:
        if (
            rankmap_distribution_support.get("status")
            == "current_rankmap_algorithm_prior"
        ):
            flags.append(
                _flag(
                    "本局静态排位模型",
                    "info",
                    "本局地图与静态算法校准权威已匹配；仅说明本局模型身份，不代表排位当前开放",
                )
            )
        else:
            flags.append(
                _flag(
                    "排位资料不可用",
                    "watch",
                    "当前 RankMap 权威缺失或不一致，本局保持无报价",
                )
            )
    replay = snapshot.get("replay_carousel") if isinstance(snapshot.get("replay_carousel"), dict) else {}
    if replay.get("active"):
        replay_detail_parts = [
            _text(replay.get("sample"), "-"),
            _text(replay.get("label"), "-"),
            f"state_sort={_text(replay.get('sort_id'), '-')}",
        ]
        if replay.get("bid_sort_id") not in (None, ""):
            replay_detail_parts.append(f"bid_sort={_text(replay.get('bid_sort_id'), '-')}")
        if replay.get("observed_round") not in (None, ""):
            replay_detail_parts.append(f"observed=R{_text(replay.get('observed_round'), '-')}")
        flags.append(
            _flag(
                f"轮播 {replay.get('step_index', '?')}/{replay.get('step_total', '?')}",
                "neutral",
                "; ".join(replay_detail_parts),
            )
        )
    if warehouse_qualification == "player_custom_warehouse":
        flags.append(
            _flag(
                "玩家自定义仓",
                "watch",
                "7月23日活动仓；不纳入官方普通仓爆率/调参，当前报价仅作参考",
            )
        )
    elif warehouse_qualification == "postupdate_unknown_warehouse_source":
        flags.append(
            _flag(
                "仓来源待确认",
                "watch",
                "来源或表版本尚未确认；样本已隔离，当前报价仅作参考",
            )
        )
    if not is_supported:
        flags.append(_flag("未识别英雄", "neutral", f"当前英雄 {hero}"))
    elif hero_key not in STRUCTURED_REF_HERO_KEYS:
        flags.append(_flag("通用 Ref", "neutral", f"当前英雄 {hero_key or hero}"))
    if phase == "settled":
        flags.append(_flag("已结算", "neutral"))
        if snapshot.get("settlement_incomplete"):
            _sid = snapshot.get("settlement_incomplete_detail") or {}
            flags.append(
                _flag(
                    "结算不完整",
                    "watch",
                    f"仅捕获 {_sid.get('captured_items', '?')}/{_sid.get('declared_items', '?')} 件"
                    "（抓包未收全结算数据）— 结算总值与差值偏低、不可信",
                )
            )
    raw_map_alias_mode = context.get("map_alias_mode") or source.get("map_alias_mode")
    map_alias = (
        context.get("map_alias")
        if isinstance(context.get("map_alias"), dict)
        else source.get("map_alias")
        if isinstance(source.get("map_alias"), dict)
        else {}
    )
    raven_alias_receipt = project_raven_template_alias_receipt(
        map_alias,
        claimed_mode=raw_map_alias_mode,
    )
    if raven_alias_receipt:
        flags.append(
            _flag(
                "模板估算",
                "watch",
                "活动开放前按对应旧图模板估算；非精确掉落池或概率，待实战校准",
            )
        )
    elif raven_alias_receipt is None:
        map_alias_mode = _text(raw_map_alias_mode)
        if map_alias_mode:
            flags.append(_flag("地图 fallback", "watch", map_alias_mode))
    if info_band in {"low", "低"}:
        flags.append(_flag("证据低", "watch"))
    ref_ok = ref_result.get("status") in {
        "ok",
        "count_prior",
        "provisional_estimated",
    }
    # Keep the concrete container aligned with the typed helper contract. Cython
    # enforces ``list[str]`` here even though pure Python accepted the former tuple.
    ref_notes = [str(item) for item in ref_result.get("notes") or ()]
    nonofficial_zero_red_quote_cap = next(
        (
            note
            for note in ref_notes
            if note.startswith("ahmed_nonofficial_zero_red_quote_cap:")
        ),
        "",
    )
    nonofficial_q4_avg_arm_safety = next(
        (
            note
            for note in ref_notes
            if note.startswith("ahmed_nonofficial_q4_avg_arm_safety:")
            and not note.endswith(":display_support")
        ),
        "",
    )
    if nonofficial_zero_red_quote_cap:
        flags.append(
            _flag(
                "来源风险限价",
                "watch",
                (
                    "玩家自定义仓；0红物理臂可达，报价已按该风险臂收紧"
                    if warehouse_qualification == "player_custom_warehouse"
                    else "仓来源待确认；0红物理臂可达，报价已按该风险臂收紧；未判定为玩家仓"
                ),
            )
        )
    if nonofficial_q4_avg_arm_safety:
        flags.append(
            _flag(
                "来源多臂保护",
                "watch",
                (
                    "玩家自定义仓；紫均格存在多个可达件数臂，三档已按低值/高值臂分层保护"
                    if warehouse_qualification == "player_custom_warehouse"
                    else "仓来源待确认；紫均格存在多个可达件数臂，三档已按低值/高值臂"
                    "分层保护；未判定为玩家仓"
                ),
            )
        )
    aisha_early_round_waiting = "aisha_early_round_waiting" in ref_notes
    hero_ref_waiting = "hero_ref_waiting" in ref_notes
    ref_trigger_waiting = aisha_early_round_waiting or hero_ref_waiting
    aisha_quote_pass = _aisha_quote_pass_kind(ref_notes)
    aisha_early_round_lightweight = (
        hero_key == "aisha"
        and not ref_trigger_waiting
        and aisha_quote_pass
        in {AISHA_ENGINE_PASS_SKILL, AISHA_ENGINE_PASS_ITEM, AISHA_QUOTE_PASS_PUBLIC}
        and round_no is not None
        and int(round_no) < AISHA_LIVE_ENGINE_MIN_ROUND
    )
    rare_signals = (
        diagnostics.get("rare_signals")
        if isinstance(diagnostics.get("rare_signals"), dict)
        else {}
    )
    ref_sparse_exact_prior = "sparse_exact_total_prior_enumeration" in ref_notes
    ref_combo_cap_hit = "combo_cap_hit" in ref_notes
    ref_count_sums = ref_evidence.get("count_sums") if isinstance(ref_evidence.get("count_sums"), dict) else {}
    victor_missing_q456 = (
        hero_key == "victor"
        and phase != "settled"
        and "q4q5q6" not in ref_count_sums
        and "q4q5" not in ref_count_sums
    )
    ref_review_only = any(note.startswith("settlement_review") for note in ref_notes)
    provisional_5215_notice = _wuqilin_5215_provisional_notice(ref_result)
    current_hall_estimate = ref_result.get('source') == 'auction_hall_6001_slotted_algorithm_036_v1'
    if ref_result.get("status") == "provisional_estimated":
        ref_readiness = "provisional_estimated"
    elif ref_ok and ref_review_only:
        ref_readiness = "review_only"
    elif ref_ok and victor_missing_q456:
        ref_readiness = "victor_q456_prior"
    elif ref_result.get("status") == "count_prior" and ref_sparse_exact_prior:
        ref_readiness = "sparse_exact_prior"
    elif ref_result.get("status") == "count_prior":
        ref_readiness = "count_prior"
    elif ref_ok:
        ref_readiness = "live_ready"
    else:
        ref_readiness = _text(ref_result.get("status"), "unavailable")
    ref_display_ready = (
        ref_ok
        and not ref_review_only
        and phase != "settled"
    )
    aisha_formal_quote = _aisha_formal_calculator_ready(hero_key, round_no, phase)
    ref_quote_display_ready = (
        ref_display_ready and aisha_formal_quote
        if hero_key == "aisha"
        else ref_display_ready
    )
    ref_balanced = _parse_money(ref_result.get("balanced")) if ref_ok else None
    v3_balanced = _parse_money(decision.get("attack_bid"))
    display_source = (
        "wuqilin_5215_algorithm_estimate"
        if provisional_5215_notice
        else
        "auction_hall_6001_provisional"
        if ref_readiness == "provisional_estimated"
        else "ref_prior"
        if ref_readiness in {"count_prior", "sparse_exact_prior", "victor_q456_prior"}
        else "ref_v0"
        if ref_quote_display_ready
        else "ref_waiting"
    )
    ref_minus_v3 = (
        ref_balanced - v3_balanced
        if ref_balanced is not None and v3_balanced is not None
        else None
    )
    if hero_key == "aisha" and phase != "settled":
        ref_minus_v3 = None
    ref_status = _text(ref_result.get("status"), "")
    adaptation_pending = (
        _ref_adaptation_pending_text(
            ref_result,
            formal_mode_reason=snapshot.get("formal_mode_reason"),
        )
        if not ref_quote_display_ready
        else ""
    )
    waiting_flag = _ref_waiting_flag_label(hero_key, ref_result, round_no=round_no)
    if is_supported and waiting_flag and not adaptation_pending:
        flags.append(_flag(waiting_flag, "watch"))
    # Method A: the scheduled reveal is still pending but we surface the engine's prior-based estimate
    # rather than a blank no-quote. Badge it clearly so the numbers read as 暂估 (provisional, may be
    # off), not a firm quote. See _hero_single_pass_ref_result / DRAW_HERO_DISABLE_PROVISIONAL_QUOTE.
    if "hero_ref_provisional" in ref_notes:
        _prov_hint = hero_ref_wait_hint(ref_notes) or "待品质/件数"
        flags.append(_flag(f"暂估·{_prov_hint}", "watch", "排程揭示未到，先用先验估计；偏差可能较大，待揭示自动收窄"))
    if victor_missing_q456:
        flags.append(_flag("缺紫金红件数", "watch", "Victor 100209 not captured; using prior"))
    if ref_readiness == "sparse_exact_prior":
        flags.append(_flag("宽约束快速", "watch", "exact total count with probability-prior quality split"))
    if ref_readiness == "count_prior":
        flags.append(_flag("总件估计", "watch", "ref count prior; no exact total count"))
    if ref_combo_cap_hit:
        flags.append(_flag("组合截断", "watch", f"ref combos={_text(ref_result.get('combo_count'), '?')}"))
    if ref_review_only:
        flags.append(_flag("回放口径", "watch", "ref_v0 used settlement review fields"))
    if ref_readiness == "provisional_estimated":
        flags.append(
            _flag(
                provisional_5215_notice or "6001 估算",
                "watch",
                ("依据本局信息更新估计" if provisional_5215_notice
                 else "结合当前藏品、两类保底与本局件格信息估计；详情可查看模型来源" if current_hall_estimate
                 else "同代673项候选模型；非官方精确率，详情可查看来源与置信"),
            )
        )
    elif ref_ok:
        flags.append(_flag("外援 ref_v0", "neutral"))
    elif is_supported:
        flags.append(_flag(
            adaptation_pending or "外援未就绪", "watch",
            "本局暂不提供估值" if adaptation_pending else _ref_not_ready_flag_detail(ref_result),
        ))
    public_numeric_summary = _text(public_info.get("public_numeric_summary"), "").strip()
    if public_numeric_summary:
        flags.append(_flag("公开信息", "neutral", public_numeric_summary))
    layout_notes = [note for note in ref_notes if "aisha_layout" in note]
    if hero_key == "aisha" and layout_notes:
        flags.append(_flag("布局余量", "neutral", "; ".join(layout_notes[:4])))
    defense_hint = _aisha_defense_multiplier_hint(round_no)
    if hero_key == "aisha" and defense_hint:
        flags.append(_flag(defense_hint, "neutral", "产品参考倍数，不进引擎"))
    if hero_key == "aisha" and ref_trigger_waiting and not adaptation_pending:
        wait_hint = _aisha_early_wait_hint(ref_notes)
        flags.append(
            _flag(
                wait_hint or "等待技能帧",
                "watch",
                "技能帧就绪后再估价",
            )
        )
    elif hero_key == "aisha" and aisha_quote_pass == AISHA_ENGINE_PASS_ITEM:
        detail = "技能帧+道具帧双 pass"
        if ref_engine_skill_pass_ms is not None and ref_engine_item_pass_ms is not None:
            detail = (
                f"技能 {ref_engine_skill_pass_ms:g}ms + 道具 {ref_engine_item_pass_ms:g}ms"
            )
        flags.append(_flag("道具帧估计", "watch", detail))
    elif hero_key == "aisha" and aisha_quote_pass == AISHA_ENGINE_PASS_SKILL:
        flags.append(
            _flag(
                "技能帧估计",
                "watch",
                _aisha_skill_only_flag_detail(ref_notes),
            )
        )
    if hero_key == "aisha" and aisha_early_round_lightweight:
        flags.append(
            _flag(
                "R1–R2轻量",
                "watch",
                "早轮 capped combos；R3+ 全量",
            )
        )
    d1_detail = _aisha_d1_flag_detail(ref_notes) if hero_key == "aisha" else ""
    if d1_detail:
        flags.append(_flag("红品权重参考", "watch", d1_detail))

    latest_result = actions.get("latest_result") if isinstance(actions.get("latest_result"), dict) else {}
    latest_sent = actions.get("latest_sent") if isinstance(actions.get("latest_sent"), dict) else {}
    red_count_display_range, red_cells_display_range = _red_display_ranges(ref_result)
    ref_red_count_range = (
        _red_range_text(red_count_display_range)
        if ref_quote_display_ready
        else ""
    )
    ref_red_cells_range = (
        _red_range_text(red_cells_display_range)
        if ref_quote_display_ready
        else ""
    )
    red_value_display_range = _red_display_value_range(
        ref_result,
        red_count_display_range,
    )
    ref_red_value_range = (
        " / ".join(_money(v, "?") for v in (red_value_display_range or ()))
        if ref_quote_display_ready
        else ""
    )
    quality_count_ranges = (
        ref_result.get("quality_count_ranges")
        if isinstance(ref_result.get("quality_count_ranges"), dict)
        else {}
    )
    minimap_summary = _minimap_summary(snapshot, uc)
    minimap_quality_summary = ""
    quality_counts = minimap_summary.get("quality_counts")
    if isinstance(quality_counts, dict) and quality_counts:
        minimap_quality_summary = " ".join(
            f"{key.upper()}:{quality_counts[key]}"
            for key in ("q6", "q5", "q4", "q3", "q2", "q1")
            if key in quality_counts
        )
        if minimap_quality_summary:
            flags.append(_flag("品质标记", "neutral", minimap_quality_summary))

    display_red_count_range = ref_red_count_range or "-"
    display_red_cells_range = ref_red_cells_range or "-"
    display_red_value_range = ref_red_value_range or "-"
    display_quality_count_summary = (
        _purple_gold_quality_summary(ref_result)
        if ref_quote_display_ready
        else "-"
    )
    display_quality_probability_summary = (
        _quality_probability_summary(ref_result)
        if ref_quote_display_ready and not quality_composition_conflict
        else "-"
    )
    display_red_count_probability_hover = (
        _quality_dimension_probability_hover(ref_result, "q6", "count")
        if ref_quote_display_ready and not quality_composition_conflict
        else ""
    )
    q6_composition = (
        quality_composition.get("qualities", {}).get("q6", {})
        if isinstance(quality_composition.get("qualities"), dict)
        else {}
    )
    q6_count_correlation_note = (
        q6_composition.get("count_correlation_note", "")
        if isinstance(q6_composition, dict)
        and isinstance(q6_composition.get("count_correlation_note", ""), str)
        else ""
    )
    if q6_count_correlation_note:
        display_red_count_probability_hover = "；".join(
            part
            for part in (
                display_red_count_probability_hover,
                q6_count_correlation_note,
            )
            if part
        )
    display_red_cells_probability_hover = (
        _quality_dimension_probability_hover(ref_result, "q6", "cells")
        if ref_quote_display_ready and not quality_composition_conflict
        else ""
    )
    display_purple_gold_probability_hover = (
        _quality_probability_hover(ref_result, ("q4", "q5"))
        if ref_quote_display_ready and not quality_composition_conflict
        else ""
    )
    display_quality_probability_hover = (
        _quality_probability_hover(ref_result, ("q5", "q6"))
        if ref_quote_display_ready and not quality_composition_conflict
        else ""
    )
    display_low_quality_count_probability_hover = (
        _low_quality_count_probability_hover(ref_result)
        if (
            ref_quote_display_ready
            and phase != "settled"
            and not quality_composition_conflict
        )
        else ""
    )
    waiting_display = adaptation_pending or _ref_waiting_display_text(hero_key, ref_result, round_no=round_no)
    display_uncertainty_summary = (
        _quality_uncertainty_summary(
            ref_result,
            quality_composition=quality_composition,
        )
        if ref_quote_display_ready
        else waiting_display
    )
    if ref_display_ready and ref_readiness == "count_prior":
        display_red_risk_reference = "总件先验"
    elif ref_display_ready and ref_readiness == "sparse_exact_prior":
        display_red_risk_reference = "总件已知，品质先验"
    elif ref_display_ready and ref_readiness == "victor_q456_prior":
        display_red_risk_reference = "紫金红先验"
    elif ref_readiness == "provisional_estimated":
        display_red_risk_reference = provisional_5215_notice or "6001 同池弱先验估算"
    elif ref_display_ready:
        display_red_risk_reference = "ref_v0 估计"
    else:
        display_red_risk_reference = waiting_display
    v3_assist_notes: list[str] = []
    known_q6_count, known_q6_cells = _known_quality_footprint(
        minimap_summary,
        "q6",
    )
    if ref_display_ready and phase != "settled" and known_q6_count > 0:
        display_red_count_range = _floor_range_text(
            display_red_count_range,
            known_q6_count,
        )
        display_red_cells_range = _floor_range_text(
            display_red_cells_range,
            known_q6_cells,
        )
        known_note = f"已见红{known_q6_count}件/{known_q6_cells}格"
        v3_assist_notes.append(known_note)
        display_uncertainty_summary = _quality_uncertainty_summary(
            ref_result,
            quality_composition=quality_composition,
            count_floors={"q6": known_q6_count},
        )
        flags.append(
            _flag(
                "已见红",
                "neutral",
                f"known q6 footprint lower bound: {known_q6_count} item(s), {known_q6_cells} cells",
            )
        )
    ref_q6_mid = _range_mid(ref_red_count_range)
    v3_q6_mid = _range_mid(posterior.get("q6_count_range"))
    if (
        ref_display_ready
        and phase != "settled"
        and ref_q6_mid is not None
        and v3_q6_mid is not None
        and abs(ref_q6_mid - v3_q6_mid) >= 1
    ):
        flags.append(
            _flag(
                "v3红件对照",
                "watch",
                f"ref median q6={ref_q6_mid}; v3 median q6={v3_q6_mid}; display keeps ref",
            )
        )
        v3_assist_notes.append(f"v3红中位{v3_q6_mid}仅对照")
    if ref_quote_display_ready and ref_minus_v3 is not None and abs(ref_minus_v3) >= 120_000:
        flags.append(
            _flag(
                "v3价差",
                "watch",
                f"ref-v3 balanced={ref_minus_v3:+,}; main quote keeps ref",
            )
        )
    display_red_risk_reference = _join_notes(
        display_red_risk_reference,
        *v3_assist_notes,
    )
    if nonofficial_zero_red_quote_cap:
        display_red_risk_reference = _join_notes(
            display_red_risk_reference,
            "non-official 来源：0红余数臂已纳入报价保护",
        )
    if nonofficial_q4_avg_arm_safety:
        display_red_risk_reference = _join_notes(
            display_red_risk_reference,
            "non-official 来源：紫均格多臂已纳入报价与红尾保护",
        )
    main_conservative = (
        _money(ref_result.get("conservative"))
        if ref_quote_display_ready
        else "-"
    )
    main_balanced = (
        _money(ref_result.get("balanced"))
        if ref_quote_display_ready
        else "-"
    )
    main_aggressive = (
        _money(ref_result.get("aggressive"))
        if ref_quote_display_ready
        else "-"
    )
    main_action = (
        provisional_5215_notice or "6001 估算"
        if ref_readiness == "provisional_estimated" and ref_quote_display_ready
        else "估计参考"
        if ref_readiness in {"count_prior", "sparse_exact_prior", "victor_q456_prior"} and ref_quote_display_ready
        else "参考可用"
        if ref_quote_display_ready
        else adaptation_pending or "等待外援输入"
    )
    provisional_2511_notice = _activity_2511_provisional_notice(ref_result) if ref_quote_display_ready else ""
    if provisional_2511_notice:
        main_action = provisional_2511_notice
        flags.append(_flag(provisional_2511_notice, "watch",
            "品质比例沿用旧通用先验，价值采用本图当前曲线；未按本周实际掉率校准，仓来源资格不变。"))
    main_current_highest = _mask_bidder_display_text(decision.get("current_highest"))
    price_titles = {
        "conservative": "保守",
        "balanced": "参考",
        "aggressive": "激进",
    }
    reference_note = (
        "算法暂估，随本局信息更新。"
        if provisional_5215_notice
        else "6001 算法估算：当前藏品与两类保底，随本局信息更新；区间为模型范围。"
        if current_hall_estimate
        else
        "6001 估算：同代 673 项候选；品质边际与三档报价均为非官方候选。"
        if ref_readiness == "provisional_estimated"
        else "External ref_v0 count/cells/value engine; total count estimated from ref prior."
        if ref_readiness == "count_prior"
        else "External ref_v0 count/cells/value engine; exact total count with probability-prior quality split."
        if ref_readiness == "sparse_exact_prior"
        else "External ref_v0 count/cells/value engine; Victor q4+q5+q6 count missing, using prior."
        if ref_readiness == "victor_q456_prior"
        else "External ref_v0 count/cells/value engine; not promoted."
        if ref_quote_display_ready
        else (
            "ref_v0 needs exact total count; main quote is held."
            if ref_status == "missing_total_count"
            else "External ref_v0 is not live-ready; main quote is held."
        )
    )
    if provisional_2511_notice:
        reference_note = "2511暂估：旧StaticData 105档品质先验 + 本图当前col6价值曲线与702项候选；本周掉率未校准，未使用活动UP倍率，不构成官方分布或训练资格。"
    live_total_value_bound = (
        ref_result.get("total_value_bound")
        if phase != "settled"
        and isinstance(ref_result.get("total_value_bound"), dict)
        else {}
    )
    lower_bound_value = (
        _parse_money(live_total_value_bound.get("lower_bound"))
        if live_total_value_bound.get("mode") == "lower_bound_only"
        else None
    )
    lower_bound_only = lower_bound_value is not None and lower_bound_value > 0
    if lower_bound_only:
        lower_bound_text = f"≥{_money(lower_bound_value)}"
        price_titles = {
            "conservative": "已知下限",
            "balanced": "估价状态",
            "aggressive": "类别残余",
        }
        main_conservative = lower_bound_text
        main_balanced = "下限态"
        main_aggressive = "残余未定"
        main_action = "仅下限可用"
        reference_note = (
            "公开竞拍类别总价只证明整仓下限；缺少类别身份/成员，"
            "当前不生成残余联合估价。"
        )
        display_red_risk_reference = _join_notes(
            display_red_risk_reference,
            "公开类别小计仅作整仓下限",
        )
        flags.append(
            _flag(
                "总价仅下限",
                "watch",
                "public category subtotal dominates modeled support; residual identity unavailable",
            )
        )
    truth_available = bool(truth.get("available"))
    settlement_partial = bool(
        truth.get("partial")
        or snapshot.get("settlement_incomplete")
        or (
            phase == "settled"
            and truth_available
            and truth.get("complete") is not True
        )
    )
    settlement_complete = bool(
        truth_available
        and truth.get("complete") is True
        and not settlement_partial
    )
    settlement_partial_detail = (
        snapshot.get("settlement_incomplete_detail")
        if isinstance(snapshot.get("settlement_incomplete_detail"), dict)
        else {}
    )
    settlement_captured_items = _parse_int(
        truth.get("captured_items")
        or settlement_partial_detail.get("captured_items")
        or truth.get("total_items")
    )
    settlement_declared_items = _parse_int(
        truth.get("declared_items")
        or settlement_partial_detail.get("declared_items")
    )
    settlement_captured_cells = _parse_int(
        truth.get("captured_cells")
        or snapshot.get("inventory_cells")
    )
    settlement_declared_cells = _parse_int(
        truth.get("declared_cells")
        or settlement_partial_detail.get("declared_cells")
    )
    if settlement_partial:
        minimap_summary = dict(minimap_summary)
        minimap_summary["partial"] = True
        minimap_summary["captured_items"] = settlement_captured_items
        minimap_summary["declared_items"] = settlement_declared_items
        minimap_summary["captured_cells"] = settlement_captured_cells
        minimap_summary["declared_cells"] = settlement_declared_cells
        if minimap_summary.get("status") == "available":
            source_text = _text(
                minimap_summary.get("layout_source"),
                "settlement_inventory",
            )
            minimap_summary["summary_text"] = (
                f"已捕获 {settlement_captured_items}/{settlement_declared_items} 件"
                f" · {source_text}"
                if settlement_captured_items is not None
                and settlement_declared_items is not None
                else f"已捕获部分结算 · {source_text}"
            )
    settlement_total = _parse_money(truth.get("total_value"))
    settlement_ref_result: dict[str, Any] = {}
    settlement_estimate_pending = bool(
        defer_settlement_estimate and phase == "settled" and truth_available
    )
    if phase == "settled" and truth_available and not settlement_estimate_pending:
        settlement_ref_started = time.perf_counter()
        settlement_ref_result = _pre_settlement_ref_result(snapshot)
        settlement_ref_engine_ms = round(
            (time.perf_counter() - settlement_ref_started) * 1000.0,
            2,
        )
    settlement_ref_estimate = _parse_money(settlement_ref_result.get("balanced"))
    posterior_decision_values = _parse_range_numbers(posterior.get("decision_value_range"))
    posterior_decision_p90 = (
        posterior_decision_values[2]
        if len(posterior_decision_values) >= 3
        else None
    )
    settlement_estimate = (
        None
        if settlement_estimate_pending
        else settlement_ref_estimate
        or _parse_money(decision.get("attack_bid"))
        or posterior_decision_p90
        or ref_balanced
    )
    if phase == "settled" and settlement_partial:
        # The ref-vs-live comparison is a pre-settlement diagnostic, but it is easy
        # to mistake for a settlement delta on this screen. Hide it together with
        # the real final-minus-estimate delta until complete truth is available.
        ref_minus_v3 = None
        settlement_quality_counts = _quality_counts_from_text(
            snapshot.get("final_quality_counts")
        )
        settlement_quality_cells = _quality_counts_from_text(
            snapshot.get("final_quality_cells")
        )
        partial_quality_summary = _purple_gold_count_summary(
            settlement_quality_counts,
            settlement_quality_cells,
        )
        display_red_count_range = "未知"
        display_red_cells_range = "未知"
        display_red_value_range = "未知"
        display_quality_count_summary = (
            f"已见 {partial_quality_summary}"
            if partial_quality_summary != "-"
            else "结算未收全"
        )
        display_quality_probability_summary = "-"
        display_red_count_probability_hover = ""
        display_red_cells_probability_hover = ""
        display_purple_gold_probability_hover = ""
        display_quality_probability_hover = ""
        display_low_quality_count_probability_hover = ""
        display_uncertainty_summary = "结算未收全"
        display_red_risk_reference = "红品未知；已捕获数据仅为下界"
        price_titles = {
            "conservative": "估价",
            "balanced": "已知下界",
            "aggressive": "完整性",
        }
        display_source = "settlement_partial"
        main_conservative = (
            "更新中" if settlement_estimate_pending else _money(settlement_estimate)
        )
        main_balanced = (
            f"≥{_money(settlement_total)}"
            if settlement_total is not None
            else "未知"
        )
        main_aggressive = (
            f"{settlement_captured_items}/{settlement_declared_items}件"
            if settlement_captured_items is not None
            and settlement_declared_items is not None
            else "未收全"
        )
        main_action = "结算不完整"
        main_current_highest = (
            f"已捕获 {settlement_captured_items}/{settlement_declared_items} 件"
            if settlement_captured_items is not None
            and settlement_declared_items is not None
            else "结算数据未收全"
        )
        reference_note = (
            "Partial settlement capture: displayed counts and value are lower bounds; "
            "red totals and estimate delta are intentionally withheld."
        )
    elif phase == "settled" and settlement_complete:
        truth_q6 = truth.get("q6") if isinstance(truth.get("q6"), dict) else {}
        display_red_count_range = f"{_text(truth_q6.get('count'), '?')} 件"
        display_red_cells_range = f"{_text(truth_q6.get('cells'), '?')} 格"
        display_red_value_range = _money(truth_q6.get("value"))
        settlement_quality_counts = _quality_counts_from_text(snapshot.get("final_quality_counts"))
        settlement_quality_cells = _quality_counts_from_text(snapshot.get("final_quality_cells"))
        if settlement_quality_counts:
            settlement_quality_counts.setdefault("q4", 0)
            settlement_quality_counts.setdefault("q5", 0)
        settlement_quality_summary = _purple_gold_count_summary(
            settlement_quality_counts,
            settlement_quality_cells,
        )
        minimap_settlement_summary = _purple_gold_count_summary(quality_counts)
        known_purple_gold_summary = _known_purple_gold_summary(constraints)
        # Settlement truth carries count+cells; constraints summary is count-only.
        if settlement_quality_summary != "-" and settlement_quality_cells:
            display_quality_count_summary = settlement_quality_summary
        elif known_purple_gold_summary != "-":
            display_quality_count_summary = known_purple_gold_summary
        elif settlement_quality_summary != "-":
            display_quality_count_summary = settlement_quality_summary
        elif minimap_settlement_summary != "-":
            display_quality_count_summary = minimap_settlement_summary
        red_review_parts = ["真实结算"]
        decision_value = _parse_money(truth_q6.get("decision_value"))
        replacement_value = _parse_money(truth_q6.get("tail_replacement_value"))
        trimmed_tail = _parse_money(truth_q6.get("trimmed_tail_value"))
        if trimmed_tail:
            red_review_parts.append(f"裁尾{_money(decision_value)}")
            red_review_parts.append(f"替换{_money(replacement_value)}")
        display_uncertainty_summary = "已结算"
        display_red_risk_reference = "；".join(red_review_parts)
        price_titles = {
            "conservative": "估价",
            "balanced": "结算",
            "aggressive": "差值",
        }
        display_source = "settlement"
        main_conservative = (
            "更新中" if settlement_estimate_pending else _money(settlement_estimate)
        )
        main_balanced = _money(settlement_total)
        main_aggressive = (
            _signed_money(settlement_total - settlement_estimate)
            if not settlement_estimate_pending
            and settlement_total is not None
            and settlement_estimate is not None
            else "-"
        )
        main_action = "结算完成"
        main_current_highest = f"总值 {_money(settlement_total)}"
        reference_note = (
            "Settlement review: top cards show a pre-settlement replay estimate, final total, and final-minus-estimate delta."
            if settlement_ref_estimate is not None
            else "Settlement review: top cards show last formal estimate, final total, and final-minus-estimate delta."
        )

    if settlement_estimate_pending:
        reference_note = (
            f"{reference_note}; settlement truth is visible while the "
            "pre-settlement estimate updates asynchronously"
        )

    range_ref_result = settlement_ref_result or ref_result
    range_ref_ok = range_ref_result.get("status") in {
        "ok",
        "count_prior",
        "provisional_estimated",
    }
    ref_decision_range = (
        f"{_money(range_ref_result.get('conservative'))} / "
        f"{_money(range_ref_result.get('balanced'))} / "
        f"{_money(range_ref_result.get('aggressive'))}"
        if range_ref_ok
        else "-"
    )
    ref_total_value_range = (
        f"{_money(range_ref_result.get('value_p25'))} / "
        f"{_money(range_ref_result.get('value_p50'))} / "
        f"{_money(range_ref_result.get('value_p75'))}"
        if range_ref_ok
        else "-"
    )
    if lower_bound_only:
        lower_bound_range = f"≥{_money(lower_bound_value)}（下限态；类别残余未定）"
        ref_decision_range = lower_bound_range
        ref_total_value_range = lower_bound_range

    if drop_pool_support_degraded:
        reference_note = (
            f"{reference_note}; "
            "legacy drop support is incomplete; quote is conditional on known support"
        )

    performance = {
        "summary_total_ms": round((time.perf_counter() - summary_started) * 1000.0, 2),
        "ref_engine_ms": ref_engine_ms,
        "ref_engine_skill_pass_ms": ref_engine_skill_pass_ms,
        "ref_engine_item_pass_ms": ref_engine_item_pass_ms,
        "settlement_ref_engine_ms": settlement_ref_engine_ms,
        "settlement_estimate_state": (
            "pending"
            if settlement_estimate_pending
            else "computed"
            if settlement_ref_estimate is not None
            else "unavailable"
            if phase == "settled" and truth_available
            else "not_applicable"
        ),
    }

    stale_keep_last_quote: dict[str, Any] | None = None
    if keep_last_quote_on_age_stale:
        age_text = (
            f"{int(age_seconds)}s"
            if age_seconds is not None
            else "unknown"
        )
        reference_note = (
            f"{reference_note}; snapshot stale ({age_text}), last quote kept"
        )
        stale_keep_last_quote = {
            "age_seconds": age_seconds,
            "phase": phase,
            "reason": "stale_snapshot",
            "keep_last_quote": True,
        }
        flags.append(
            _flag(
                "快照过期",
                "watch",
                f"age={age_text}; 显示末次估价",
            )
        )
    truth_display_value: Any = truth.get("total_value")
    truth_display_items: Any = truth.get("total_items")
    truth_display_cells: Any = truth.get("total_cells")
    truth_display_q6 = (
        truth.get("q6") if isinstance(truth.get("q6"), dict) else {}
    )
    truth_display_top_item = (
        truth.get("top_item") if isinstance(truth.get("top_item"), dict) else {}
    )
    if settlement_partial:
        truth_display_value = (
            f"≥{_money(settlement_total)}"
            if settlement_total is not None
            else "未知"
        )
        truth_display_items = (
            f"{settlement_captured_items}/{settlement_declared_items}"
            if settlement_captured_items is not None
            and settlement_declared_items is not None
            else "未收全"
        )
        partial_cells = _parse_int(truth.get("total_cells"))
        truth_display_cells = (
            f"≥{partial_cells}" if partial_cells is not None else "未知"
        )
        truth_display_q6 = {"count": "未知", "cells": "未知", "value": "未知"}
        truth_display_top_item = {}

    if phase == "settled":
        display_red_count_compact = display_red_count_range
    else:
        composition_view = detail_composition_view(
            {"quality_composition": quality_composition}
        )
        display_red_count_compact = format_compact_quality_count(
            composition_view.quality("q6"),
            fallback=display_red_count_range,
        )
    api_ref_result = (
        {
            "status": "conflict",
            "source": "ref_v0",
            "notes": ["quality_composition_conflict"],
        }
        if quality_composition_conflict
        else ref_result
    )
    quote_input_snapshot = (
        _clone_as_pre_settlement_snapshot(snapshot)
        if settlement_ref_result
        else snapshot
    )
    quote_receipt = _quote_receipt(
        quote_input_snapshot,
        range_ref_result,
        source=("settlement_clone" if settlement_ref_result else "live_ref_result"),
        decision_range=ref_decision_range,
        value_range=ref_total_value_range,
    )
    quote_identity = (
        {
            "schema": "hero_ref_quote_identity_v1",
            "source": quote_receipt["source"],
            "input_fingerprint_scope": quote_receipt["input_fingerprint_scope"],
            "input_fingerprint": quote_receipt["input_fingerprint"],
            "result_identity": quote_receipt["result_identity"],
        }
        if quote_receipt
        else {}
    )

    return {
        "status": "ok",
        "snapshot_path": str(snapshot_path),
        "updated_at": snapshot.get("created_at") or source.get("created_at"),
        "updated_at_text": time.strftime(
            "%H:%M:%S",
            time.localtime(float(snapshot.get("created_at") or source.get("created_at") or time.time())),
        ),
        "context": {
            "hero": hero,
            "is_ahmed": hero_key == "ahmed",
            "is_supported_ref_hero": is_supported,
            "map_id": context.get("map_id") or snapshot.get("map_id"),
            "model_map_id": context.get("model_map_id") or source.get("model_map_id"),
            "round": context.get("round") or snapshot.get("round"),
            "action_round": context.get("action_round") or snapshot.get("action_round"),
            "observed_round": context.get("observed_round")
            or snapshot.get("observed_round"),
            "phase": phase,
            "formal_mode": _text(
                snapshot.get("formal_mode")
                or context.get("formal_mode")
                or _dig(snapshot, "parameters", "formal_mode"),
                "unknown",
            ),
            "session_id": context.get("session_id") or snapshot.get("session_id"),
            "file": source.get("file") or snapshot.get("file"),
            "warehouse_source": warehouse_source,
            "warehouse_source_qualification": warehouse_qualification,
        },
        "reference": {
            "label": (
                "Hero Ref · 算法暂估"
                if provisional_5215_notice
                else
                "Hero Ref · 6001估算"
                if ref_readiness == "provisional_estimated"
                else "Hero Ref"
            ),
            "source": display_source,
            "readiness": ref_readiness,
            "note": reference_note,
            "price_titles": price_titles,
            "conservative": main_conservative,
            "balanced": main_balanced,
            "aggressive": main_aggressive,
            "raw_value_range": ref_total_value_range,
            "v3_conservative": _text(decision.get("defend_bid"), "-"),
            "v3_balanced": _text(decision.get("attack_bid"), "-"),
            "v3_aggressive": _text(decision.get("stop_price"), "-"),
            "ref_minus_v3_balanced": (
                _money(ref_minus_v3)
                if ref_minus_v3 is not None
                else "-"
            ),
            "ref_minus_v3_balanced_raw": ref_minus_v3,
            "action": main_action,
            "risk_band": _text(decision.get("risk_band"), "-"),
            "current_highest": main_current_highest,
            "decision_range": ref_decision_range,
            "total_value_range": ref_total_value_range,
            **(
                {"total_value_bound": dict(live_total_value_bound)}
                if lower_bound_only
                else {}
            ),
            "total_grid_range": _range_text(ref_result.get("total_grid_range"), suffix="格"),
            **(
                {"provisional_quote": dict(ref_result["provisional_quote"])}
                if isinstance(ref_result.get("provisional_quote"), dict)
                and ref_result.get("provisional_quote")
                else {}
            ),
        },
        "red": {
            "count_range": display_red_count_range,
            "count_display": display_red_count_compact,
            "cells_range": display_red_cells_range,
            "value_range": display_red_value_range,
            "quality_count_summary": display_quality_count_summary,
            "quality_probability_summary": display_quality_probability_summary,
            "count_probability_hover": display_red_count_probability_hover,
            "cells_probability_hover": display_red_cells_probability_hover,
            "purple_gold_probability_hover": display_purple_gold_probability_hover,
            "quality_probability_hover": display_quality_probability_hover,
            "low_quality_count_probability_hover": display_low_quality_count_probability_hover,
            "uncertainty_summary": display_uncertainty_summary,
            "quality_count_facts": _quality_exact_count_facts(
                ref_result,
                quality_composition,
            ),
            **(
                {"quality_joint_exact_facts": quality_joint_exact_facts}
                if current_joint_exact_continuity_publishable
                else {}
            ),
            "prior_rate": _text(posterior.get("q6_prior_rate"), "-"),
            "sample_rate": _text(posterior.get("q6_sample_rate"), "-"),
            "risk_reference": display_red_risk_reference,
            "count_order_note": (
                "主卡保 / 均 / 激按同一红件/红格联合候选从小到大显示；"
                "跨品质总件联动与低概率候选以详情为准"
            ),
        },
        "evidence": {
            "match_text": ref_readiness,
            "information_density": _text(decision.get("information_density") or info_band, "-"),
            "diagnostics": ";".join(ref_notes),
            "latest_sent": latest_sent,
            "latest_result": latest_result,
            "public_constraint_key": _text(public_info.get("public_constraint_key"), ""),
            "evidence_profile_key": _text(public_info.get("evidence_profile_key"), ""),
            "public_numeric_summary": public_numeric_summary,
            "minimap_quality_summary": minimap_quality_summary,
            "source_mode": display_source,
            "ref_status": _text(ref_result.get("status"), ""),
            "ref_readiness": ref_readiness,
            "ref_combo_count": _text(ref_result.get("combo_count"), ""),
            "ref_input_summary": _ref_input_summary(ref_result),
            "candidate_summary": _candidate_summary(
                ref_result,
                settled_total_cells=(
                    truth.get("total_cells")
                    if phase == "settled" and settlement_complete
                    else None
                ),
            ),
            "next_info_hint": "本局暂不提供估值" if adaptation_pending and not (
                phase == "settled" and draw_hero_uses_prop_hint(hero_key)
            ) else _next_info_hint(
                ref_result,
                hero_key=hero_key,
                round_no=round_no,
                actions=actions,
                session_id=snapshot_session_id,
                phase=phase,
            ),
            "ref_notes": ";".join(ref_notes),
        },
        "diagnostics": {
            "rare_signals": rare_signals,
            "performance": performance,
            **(
                {"drop_pool_support": dict(drop_pool_support)}
                if drop_pool_support_degraded
                else {}
            ),
            **(
                {"rankmap_distribution_support": dict(rankmap_distribution_support)}
                if rankmap_distribution_support
                else {}
            ),
        },
        "quote_basis": quote_basis(
            ref_result,
            eligible=(ref_quote_display_ready and not lower_bound_only
                      and not quality_composition_conflict and phase in {"reading", "bidding"}
                      and ref_result.get("status") in {"ok", "count_prior", "provisional_estimated"}),
            source_mode="manual_overlay" if source.get("manual_overlay") else "live",
            generation_hash=monitor_generation(lock_payload),
        ),
        "quote_receipt": quote_receipt,
        "quote_identity": quote_identity,
        "truth": {
            "available": bool(truth.get("available")),
            "complete": settlement_complete,
            "partial": settlement_partial,
            "captured_items": settlement_captured_items,
            "captured_cells": settlement_captured_cells,
            "declared_items": settlement_declared_items,
            "declared_cells": settlement_declared_cells,
            "total_value": truth_display_value,
            "total_items": truth_display_items,
            "total_cells": truth_display_cells,
            "q6": truth_display_q6,
            "top_item": truth_display_top_item,
        },
        "red_outline_value_summary": (
            dict(ref_result.get("red_outline_value_summary"))
            if isinstance(ref_result.get("red_outline_value_summary"), dict)
            else {}
        ),
        "q5_total_cells_value_summary": (
            dict(ref_result.get("q5_total_cells_value_summary"))
            if isinstance(ref_result.get("q5_total_cells_value_summary"), dict)
            else {}
        ),
        "quality_composition": quality_composition,
        "ahmed_ref": api_ref_result,
        "minimap": minimap_summary,
        "flags": flags,
        **(
            {"drop_pool_support": dict(drop_pool_support)}
            if drop_pool_support_degraded
            else {}
        ),
        **(
            {"rankmap_distribution_support": dict(rankmap_distribution_support)}
            if rankmap_distribution_support
            else {}
        ),
        **({"stale": stale_keep_last_quote} if stale_keep_last_quote else {}),
    }


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ahmed Live Reference</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101316;
      --panel: #171b1f;
      --line: #2b3238;
      --text: #edf1f2;
      --muted: #9aa6ad;
      --green: #54c58a;
      --amber: #e7b75f;
      --red: #ef7771;
      --blue: #7fb7ff;
      --chip: #232a30;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }
    main {
      width: min(760px, calc(100vw - 20px));
      margin: 10px auto;
    }
    .shell {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
      box-shadow: 0 12px 34px rgba(0,0,0,.32);
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      align-items: center;
    }
    .title {
      font-size: 18px;
      font-weight: 700;
      white-space: nowrap;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .chip {
      background: var(--chip);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      line-height: 1.25;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      padding: 10px 12px;
    }
    .tile {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      min-height: 70px;
      background: #12171b;
    }
    .tile label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }
    .price {
      font-size: 24px;
      font-weight: 800;
      line-height: 1.1;
      white-space: nowrap;
    }
    .safe .price { color: var(--green); }
    .mid .price { color: var(--amber); }
    .hot .price { color: var(--red); }
    .section {
      display: grid;
      grid-template-columns: 1.15fr .85fr;
      gap: 8px;
      padding: 0 12px 10px;
    }
    .box {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      background: #12171b;
      min-height: 96px;
    }
    h2 {
      margin: 0 0 8px;
      font-size: 13px;
      color: var(--muted);
      font-weight: 700;
    }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 3px 0;
      font-size: 13px;
      border-bottom: 1px solid rgba(255,255,255,.04);
    }
    .row:last-child { border-bottom: 0; }
    .row span:first-child { color: var(--muted); }
    .row span:last-child { text-align: right; overflow-wrap: anywhere; }
    .flags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 6px;
    }
    .flag {
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      background: var(--chip);
      border: 1px solid var(--line);
    }
    .flag.risk { color: #ffd4d1; border-color: rgba(239,119,113,.55); }
    .flag.watch { color: #ffe2a8; border-color: rgba(231,183,95,.55); }
    .flag.neutral { color: #d4e6ff; border-color: rgba(127,183,255,.5); }
    footer {
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 11px;
      padding: 8px 12px;
      display: flex;
      justify-content: space-between;
      gap: 12px;
    }
    footer a { color: var(--accent); }
    code { color: #d8e6ef; }
    @media (max-width: 640px) {
      .grid, .section { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
      .meta { justify-content: flex-start; }
      .price { font-size: 22px; }
    }
  </style>
</head>
<body>
<main>
  <div class="shell">
    <header>
      <div>
        <div class="title" id="title">Ahmed Live Reference</div>
        <div class="flags" id="flags"></div>
      </div>
      <div class="meta">
        <span class="chip" id="phase">phase</span>
        <span class="chip" id="session">session</span>
        <span class="chip" id="updated">--:--:--</span>
      </div>
    </header>
    <div class="grid">
      <div class="tile safe"><label>保守</label><div class="price" id="safe">-</div></div>
      <div class="tile mid"><label>参考</label><div class="price" id="mid">-</div></div>
      <div class="tile hot"><label>激进</label><div class="price" id="hot">-</div></div>
    </div>
    <div class="section">
      <div class="box">
        <h2>品质与概率</h2>
        <div class="row"><span>红品件数</span><span id="redCount">-</span></div>
        <div class="row"><span>红品格数</span><span id="redCells">-</span></div>
        <div class="row"><span>金/红率</span><span id="qualityProbability">-</span></div>
        <div class="row"><span>紫/金品</span><span id="purpleGold">-</span></div>
        <div class="row"><span>决策区间</span><span id="decisionRange">-</span></div>
        <div class="row"><span>总值区间</span><span id="totalRange">-</span></div>
      </div>
      <div class="box">
        <h2>证据</h2>
        <div class="row"><span>匹配</span><span id="match">-</span></div>
        <div class="row"><span>密度</span><span id="density">-</span></div>
        <div class="row"><span>来源</span><span id="sourceMode">-</span></div>
        <div class="row"><span>最近结果</span><span id="latestResult">-</span></div>
        <div class="row"><span>诊断</span><span id="diag">-</span></div>
      </div>
    </div>
    <div class="section">
      <div class="box">
        <h2>当前建议</h2>
        <div class="row"><span>动作</span><span id="action">-</span></div>
        <div class="row"><span>风险</span><span id="riskBand">-</span></div>
        <div class="row"><span>当前最高</span><span id="highest">-</span></div>
        <div class="row"><span>低品件</span><span id="q6Note">-</span></div>
      </div>
      <div class="box">
        <h2>结算对照</h2>
        <div class="row"><span>状态</span><span id="truthState">-</span></div>
        <div class="row"><span>总值/件/格</span><span id="truthTotal">-</span></div>
        <div class="row"><span>红品 truth</span><span id="truthQ6">-</span></div>
        <div class="row"><span>最高物</span><span id="truthTop">-</span></div>
      </div>
    </div>
    <footer>
      <span>作者：加菲_Barista · 协作：lemyes · <a href="https://space.bilibili.com/88048665?spm_id_from=333.1007.0.0" target="_blank" rel="noreferrer">Bilibili</a></span>
      <span><code id="path"></code></span>
    </footer>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
function text(v, fallback='-') {
  if (v === null || v === undefined || v === '') return fallback;
  return String(v);
}
function latestAction(v) {
  if (!v || typeof v !== 'object') return '-';
  const tool = v.tool || v.action_id || '';
  const result = v.result ? ` = ${v.result}` : '';
  return `${tool}${result}` || '-';
}
function exactLowQualityFacts(red) {
  const facts = red && red.quality_joint_exact_facts;
  if (!facts || typeof facts !== 'object' || Array.isArray(facts)) return [];
  const labels = {q1: '白绿', q3: '蓝'};
  const rows = [];
  for (const key of ['q1', 'q3']) {
    const fact = facts[key];
    if (!fact || typeof fact !== 'object' || Array.isArray(fact)) continue;
    if (
      fact.source === 'joint_exact'
      && Number.isInteger(fact.count) && fact.count >= 0
      && Number.isInteger(fact.cells) && fact.cells >= 0
    ) rows.push(`${labels[key]}${fact.count}件/${fact.cells}格`);
  }
  return rows;
}
function renderFlags(flags) {
  const root = $('flags');
  root.innerHTML = '';
  if (!flags || !flags.length) {
    const el = document.createElement('span');
    el.className = 'flag neutral';
    el.textContent = '正常监测';
    root.appendChild(el);
    return;
  }
  for (const flag of flags) {
    const el = document.createElement('span');
    el.className = `flag ${flag.level || 'watch'}`;
    el.title = flag.detail || '';
    el.textContent = flag.label || 'watch';
    root.appendChild(el);
  }
}
function render(data) {
  const c = data.context || {};
  const r = data.reference || {};
  const red = data.red || {};
  const ev = data.evidence || {};
  const truth = data.truth || {};
  $('title').textContent = `${text(c.hero, '?')} · ${text(c.map_id, '?')} · R${text(c.round, '?')}`;
  $('phase').textContent = text(c.phase);
  $('session').textContent = text(c.session_id);
  $('updated').textContent = text(data.updated_at_text);
  $('safe').textContent = text(r.conservative);
  $('mid').textContent = text(r.balanced);
  $('hot').textContent = text(r.aggressive);
  $('redCount').textContent = text(red.count_display || red.count_range);
  $('redCells').textContent = text(red.cells_range);
  $('redCount').title = text(red.count_probability_hover, '');
  $('redCells').title = text(red.cells_probability_hover, '');
  $('qualityProbability').textContent = text(red.quality_probability_summary);
  $('qualityProbability').title = text(red.quality_probability_hover, '');
  $('purpleGold').textContent = text(red.quality_count_summary);
  $('purpleGold').title = text(red.purple_gold_probability_hover, '');
  $('decisionRange').textContent = text(r.decision_range);
  $('totalRange').textContent = text(r.total_value_range);
  $('match').textContent = text(ev.match_text);
  $('density').textContent = text(ev.information_density);
  $('sourceMode').textContent = text(ev.source_mode);
  const latestResult = latestAction(ev.latest_result);
  $('latestResult').textContent = latestResult !== '-' ? latestResult : latestAction(ev.latest_sent);
  $('diag').textContent = text(ev.diagnostics);
  $('action').textContent = text(r.action);
  $('riskBand').textContent = text(r.risk_band);
  $('highest').textContent = text(r.current_highest);
  const lowSummary = text(red.uncertainty_summary || red.risk_reference, '');
  const currentFacts = exactLowQualityFacts(red).filter((row) => !lowSummary.includes(row));
  $('q6Note').textContent = [
    lowSummary,
    currentFacts.length ? `当前硬事实 ${currentFacts.join(' · ')}` : '',
  ].filter(Boolean).join(' · ') || '-';
  $('q6Note').title = text(red.low_quality_count_probability_hover, '');
  $('truthState').textContent = truth.partial ? 'partial' : truth.available ? 'available' : 'not settled';
  $('truthTotal').textContent = truth.available ? `${text(truth.total_value)} / ${text(truth.total_items)}件 / ${text(truth.total_cells)}格` : '-';
  const q6 = truth.q6 || {};
  $('truthQ6').textContent = truth.available ? `${text(q6.count)}件 / ${text(q6.cells)}格 / ${text(q6.value)}` : '-';
  const top = truth.top_item || {};
  $('truthTop').textContent = truth.available ? `${text(top.name)} ${text(top.value)}` : '-';
  $('path').textContent = text(data.snapshot_path, '');
  renderFlags(data.flags || []);
}
async function refresh() {
  try {
    const response = await fetch('/api/latest', {cache: 'no-store'});
    const data = await response.json();
    if (data.status === 'ok') render(data);
  } catch (err) {
    renderFlags([{label: '读取失败', level: 'risk', detail: String(err)}]);
  }
}
refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""


@dataclass(frozen=True)
class ServerConfig:
    project_root: Path
    snapshot: Path

    @property
    def snapshot_path(self) -> Path:
        if self.snapshot.is_absolute():
            return self.snapshot
        return self.project_root / self.snapshot


def make_handler(config: ServerConfig) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AhmadLiveReference/0.1"

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self) -> None:
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                self._send_html()
                return
            if parsed.path == "/api/health":
                self._send_json(
                    {
                        "status": "ok",
                        "snapshot_path": str(config.snapshot_path),
                        "exists": config.snapshot_path.exists(),
                    }
                )
                return
            if parsed.path == "/api/latest":
                try:
                    snapshot = _read_json(config.snapshot_path)
                    self._send_json(
                        summarize_snapshot(snapshot, snapshot_path=config.snapshot_path)
                    )
                except FileNotFoundError:
                    self._send_json(
                        {
                            "status": "missing_snapshot",
                            "snapshot_path": str(config.snapshot_path),
                        },
                        status=404,
                    )
                except Exception as exc:  # noqa: BLE001 - expose prototype diagnostics
                    self._send_json(
                        {
                            "status": "error",
                            "error": str(exc),
                            "snapshot_path": str(config.snapshot_path),
                        },
                        status=500,
                    )
                return
            self._send_json({"status": "not_found", "path": parsed.path}, status=404)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the isolated Ahmed live reference panel.")
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args(argv)

    config = ServerConfig(
        project_root=Path(args.project_root).resolve(),
        snapshot=Path(args.snapshot),
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(config))
    print(f"Ahmed live reference panel: http://{args.host}:{args.port}", flush=True)
    print(f"Snapshot: {config.snapshot_path}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
