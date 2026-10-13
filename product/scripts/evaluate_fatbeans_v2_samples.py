"""Batch-evaluate Fatbeans captures with the evidence-first v2 posterior."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bidking_lab.inference.diagnostics import layout_conflict_root  # noqa: E402
from bidking_lab.inference.ground_truth import prepare_session_sampler  # noqa: E402
from bidking_lab.inference.quality_combo_presolve import (  # noqa: E402
    is_quality_combo_reachable,
    load_quality_combo_presolve,
)
from bidking_lab.inference.q6_residual import (  # noqa: E402
    AISHA_BOTTOM_ROW_RISK_THRESHOLD,
    AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES,
    AISHA_Q6_QUALITY_ONLY_DEEP_ROW_THRESHOLD,
    AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD,
    RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR,
    actionable_random_sample_avg_values,
    aisha_bottom_row_risk,
    evidence_profile_key_from_problem,
    q6_conditional_target_active_for_profile,
    q6_residual_boost_for_profile,
    q6_residual_prior_floor_ratio_for_profile,
    q6_residual_value_power_for_profile,
)
from bidking_lab.inference.v2 import (  # noqa: E402
    RANDOM_SAMPLE_HARD_FLOOR_EXTRA_TRIALS,
    RANDOM_SAMPLE_HARD_FLOOR_MAX_MIN_MATCHED,
    RANDOM_SAMPLE_HARD_FLOOR_MIN_MATCHED,
    RANDOM_SAMPLE_HARD_FLOOR_MIN_RATE,
    RANDOM_SAMPLE_VALUE_FLOOR_FACTOR,
    RANDOM_SAMPLE_VALUE_FLOOR_SOFT_PENALTY,
    build_residual_problem,
    estimate_posterior_v2,
    evidence_store_from_fatbeans_events,
)
from bidking_lab.inference.v3.evidence_registry import (  # noqa: E402
    public_info_semantic as _v3_public_info_semantic,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    _CATEGORY_OUTLINE_ACTIONS,
    latest_player_bids,
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import (  # noqa: E402
    _inventory_quality_breakdown,
    _inventory_totals,
    _inventory_value,
    _latest_round,
    _states_to_session,
    load_monitor_tables,
)
from build_clean_eval_set import (  # noqa: E402
    activity_eval_files,
    clean_eval_files_many,
    sample_selection_metadata,
    selection_row_fields,
)

_CATEGORY_ACTION_LABELS = {
    100151: "家具",
    100152: "医疗",
    100153: "时尚",
    100154: "武器",
    100155: "珠宝",
    100156: "古董",
    100157: "数码",
    100158: "能源",
    100159: "食饮",
    100160: "书画",
}

_Q6_COUNT_CELL_NARROW_P90_SWEEP = (
    ("aisha_shipwreck_deep_v1", (1.0, 1.25, 1.5, 2.0)),
    ("ethan_villa_random_avg_v1", (1.0, 1.5, 2.0)),
)
_AISHA_SHIPWRECK_DEEP_THRESHOLD_P90_SWEEP = (9, 10, 11, 12, 13)
_DEFAULT_COMBO_PRESOLVE_PATH = (
    ROOT / "data" / "processed" / "quality_combo_presolve_q456.json"
)
_Q6_SHADOW_SAMPLING_TARGETS: tuple[tuple[str, str, int], ...] = (
    ("aisha", "shipwreck", 20),
    ("ethan", "shipwreck", 20),
    ("aisha", "hidden", 10),
    ("ethan", "hidden", 5),
)
_PUBLIC_AVG_CELLS_INFO: dict[int, tuple[str, int | None]] = {
    200013: ("q4_avg_cells", 4),
    200014: ("total_avg_cells", None),
    200015: ("q5_avg_cells", 5),
    200016: ("q6_avg_cells", 6),
}
_AVG_CELLS_REVIEW_MAX_TOTAL_COUNT = 120
_AVG_CELLS_REVIEW_MAX_QUALITY_COUNT = 80
_AVG_CELLS_REVIEW_MAX_CELLS = 240
_HIGH_INFO_DECISION_MISS_THRESHOLD = 300_000
_AISHA_SHIPWRECK_TAIL_VALUE_PROFILES = frozenset(
    {
        "shape+layout",
        "public:random_avg+shape+layout",
        "tool:category+shape+layout",
        "public:random_avg+tool:category+shape+layout",
    }
)
_AISHA_SHIPWRECK_TAIL_VALUE_BOTTOM_THRESHOLDS = (9, 10, 11, 12, 13)
_PUBLIC_INFO_SEMANTICS = {
    200001: {
        "semantic": "q4_all_outlines",
        "model_use": "modeled_bucket_outline",
        "constraint": "hard",
        "reference": "known",
    },
    200002: {
        "semantic": "q5_all_outlines",
        "model_use": "modeled_bucket_outline",
        "constraint": "hard",
        "reference": "known",
    },
    200003: {
        "semantic": "q6_all_outlines",
        "model_use": "modeled_bucket_outline",
        "constraint": "hard",
        "reference": "known",
    },
    200004: {
        "semantic": "all_item_quality",
        "model_use": "generic_item_evidence",
        "constraint": "partial",
        "reference": "known",
    },
    200009: {
        "semantic": "total_cells",
        "model_use": "modeled_numeric_exact",
        "constraint": "hard",
        "reference": "known",
    },
    200010: {
        "semantic": "q4_total_cells",
        "model_use": "modeled_numeric_exact",
        "constraint": "hard",
        "reference": "known",
    },
    200011: {
        "semantic": "q5_total_cells",
        "model_use": "modeled_numeric_exact",
        "constraint": "hard",
        "reference": "known",
    },
    200012: {
        "semantic": "q6_total_cells",
        "model_use": "modeled_numeric_exact",
        "constraint": "hard",
        "reference": "known",
    },
    200013: {
        "semantic": "q4_avg_cells",
        "model_use": "modeled_soft_avg_cells",
        "constraint": "soft",
        "reference": "known",
    },
    200014: {
        "semantic": "total_avg_cells",
        "model_use": "modeled_soft_total_avg_cells",
        "constraint": "soft",
        "reference": "known",
    },
    200015: {
        "semantic": "q5_avg_cells",
        "model_use": "modeled_soft_avg_cells",
        "constraint": "soft",
        "reference": "known",
    },
    200016: {
        "semantic": "q6_avg_cells",
        "model_use": "modeled_soft_avg_cells",
        "constraint": "soft",
        "reference": "known",
    },
    200017: {
        "semantic": "total_item_count",
        "model_use": "modeled_numeric_exact",
        "constraint": "hard",
        "reference": "known",
    },
    200018: {
        "semantic": "q4_item_count",
        "model_use": "modeled_numeric_exact",
        "constraint": "hard",
        "reference": "known",
    },
    200019: {
        "semantic": "q5_item_count",
        "model_use": "modeled_numeric_exact",
        "constraint": "hard",
        "reference": "known",
    },
    200020: {
        "semantic": "q6_item_count",
        "model_use": "modeled_numeric_exact",
        "constraint": "hard",
        "reference": "known",
    },
    200021: {
        "semantic": "random_2_item_reveal",
        "model_use": "modeled_item_anchor_shape_layout",
        "constraint": "hard_item",
        "reference": "known",
    },
    200022: {
        "semantic": "random_4_item_reveal",
        "model_use": "modeled_item_anchor_shape_layout",
        "constraint": "hard_item",
        "reference": "known",
    },
    200023: {
        "semantic": "random_6_item_reveal",
        "model_use": "modeled_item_anchor_shape_layout",
        "constraint": "hard_item",
        "reference": "known",
    },
    200024: {
        "semantic": "random_8_item_reveal",
        "model_use": "modeled_item_anchor_shape_layout",
        "constraint": "hard_item",
        "reference": "known",
    },
    200025: {
        "semantic": "random_12_item_reveal",
        "model_use": "modeled_item_anchor_shape_layout",
        "constraint": "hard_item",
        "reference": "known",
    },
    200026: {
        "semantic": "random_3_quality_reveal",
        "model_use": "modeled_quality_floor_if_keyed",
        "constraint": "partial",
        "reference": "known",
    },
    200027: {
        "semantic": "random_6_quality_reveal",
        "model_use": "modeled_quality_floor_if_keyed",
        "constraint": "partial",
        "reference": "known",
    },
    200028: {
        "semantic": "random_9_quality_reveal",
        "model_use": "modeled_quality_floor_if_keyed",
        "constraint": "partial",
        "reference": "known",
    },
    200029: {
        "semantic": "random_12_quality_reveal",
        "model_use": "modeled_quality_floor_if_keyed",
        "constraint": "partial",
        "reference": "known",
    },
    200030: {
        "semantic": "all_item_quality",
        "model_use": "generic_item_evidence",
        "constraint": "partial",
        "reference": "known",
    },
    200031: {
        "semantic": "random_3_avg_value",
        "model_use": "diagnostic_random_avg_signal",
        "constraint": "diagnostic",
        "reference": "known",
    },
    200032: {
        "semantic": "random_6_avg_value",
        "model_use": "diagnostic_random_avg_signal",
        "constraint": "diagnostic",
        "reference": "known",
    },
    200033: {
        "semantic": "random_9_avg_value",
        "model_use": "diagnostic_random_avg_signal",
        "constraint": "diagnostic",
        "reference": "known",
    },
    200034: {
        "semantic": "random_12_avg_value",
        "model_use": "diagnostic_random_avg_signal",
        "constraint": "diagnostic",
        "reference": "known",
    },
    200035: {
        "semantic": "total_avg_value",
        "model_use": "pending_global_avg_value",
        "constraint": "pending",
        "reference": "known",
    },
    200036: {
        "semantic": "q4_avg_value",
        "model_use": "modeled_soft_avg_value",
        "constraint": "soft",
        "reference": "known",
    },
    200037: {
        "semantic": "q5_avg_value",
        "model_use": "modeled_soft_avg_value",
        "constraint": "soft",
        "reference": "known",
    },
    200038: {
        "semantic": "q6_avg_value",
        "model_use": "modeled_soft_avg_value",
        "constraint": "soft",
        "reference": "known",
    },
    200039: {
        "semantic": "all_outlines",
        "model_use": "modeled_layout_if_shapes_present",
        "constraint": "partial",
        "reference": "known",
    },
    200048: {
        "semantic": "highest_quality_item",
        "model_use": "modeled_global_max_quality",
        "constraint": "hard_global",
        "reference": "known",
    },
    200049: {
        "semantic": "highest_value_item",
        "model_use": "modeled_item_anchor_shape_layout",
        "constraint": "hard_item",
        "reference": "known",
    },
    200050: {
        "semantic": "largest_cell_item",
        "model_use": "modeled_global_max_item_cells",
        "constraint": "hard_global",
        "reference": "known",
    },
    200052: {
        "semantic": "highest_quality_value",
        "model_use": "pending_numeric_exact",
        "constraint": "pending",
        "reference": "known",
    },
}


def _default_paths() -> list[Path]:
    return [Path(path) for path in clean_eval_files_many()]


def _iter_unique(paths: Iterable[Path]) -> Iterable[Path]:
    seen: set[str] = set()
    for path in paths:
        if path.name in seen:
            continue
        seen.add(path.name)
        yield path


def _round(value: float | int | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value)))


def _round_float(value: float | int | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _normalized_error_denominator(truth: float) -> float:
    return max(100_000.0, abs(float(truth)))


def _evidence_stage(round_no: int | None) -> str:
    if round_no is None:
        return "unknown"
    if round_no <= 2:
        return "early_1_2"
    if round_no <= 4:
        return "mid_3_4"
    return "full_5"


def _information_density_score(row: Mapping[str, Any]) -> int:
    round_no = _int_or_none(row.get("capture_round"))
    anchors = _int_or_none(row.get("anchor_count")) or 0
    shape_targets = _int_or_none(row.get("shape_target_count")) or 0
    category_targets = _int_or_none(row.get("category_target_count")) or 0
    category_exclusions = _int_or_none(row.get("category_exclusion_count")) or 0
    trusted_footprints = _int_or_none(row.get("trusted_footprint_count")) or 0
    public_bonus = 2 if row.get("public_constraint_key") not in (None, "", "none") else 0
    return (
        (round_no or 0) * 2
        + min(anchors, 6) * 2
        + min(shape_targets + category_targets + category_exclusions, 6) * 2
        + min(trusted_footprints, 8)
        + public_bonus
    )


def _information_density_band(score: int | None) -> str:
    if score is None:
        return "unknown"
    if score < 18:
        return "low"
    if score < 34:
        return "medium"
    return "high"


def _is_tail_event(row: Mapping[str, Any]) -> bool:
    return (
        int(row.get("final_trimmed_tail_value") or 0) > 0
        or int(row.get("final_q6_trimmed_tail_value") or 0) > 0
    )


def _is_early_diagnostic(row: Mapping[str, Any]) -> bool:
    return str(row.get("evidence_stage") or "unknown") == "early_1_2"


def _is_hidden_case(row: Mapping[str, Any]) -> bool:
    return str(row.get("map_family") or "unknown") == "hidden"


def _is_no_q6_control(row: Mapping[str, Any]) -> bool:
    return (
        int(row.get("final_q6_decision_value") or 0) <= 0
        and row.get("v2_q6_decision_value_p90") is not None
    )


def _diagnostic_int(row: Mapping[str, Any], name: str) -> int | None:
    match = re.search(
        rf"(?:^|;){re.escape(name)}:(-?\d+)",
        str(row.get("diagnostics") or ""),
    )
    if not match:
        return None
    return int(match.group(1))


def _has_q6_exact_zero(row: Mapping[str, Any]) -> bool:
    return bool(
        re.search(
            r"(?:^|;)q6:[^;]*\bcount=0\b",
            str(row.get("bucket_targets") or ""),
        )
    )


def _is_zero_q6_proven(row: Mapping[str, Any]) -> bool:
    max_quality = _diagnostic_int(row, "public_max_quality")
    return (
        (max_quality is not None and max_quality < 6)
        or _has_q6_exact_zero(row)
    )


def _is_high_info_value_miss(row: Mapping[str, Any]) -> bool:
    if row.get("information_density_band") != "high":
        return False
    if row.get("v2_decision_value_p50_error") is None:
        return False
    return (
        abs(int(row["v2_decision_value_p50_error"]))
        >= _HIGH_INFO_DECISION_MISS_THRESHOLD
        or row.get("v2_value_p90_covers_final") is False
    )


def _is_high_info_q6_miss(row: Mapping[str, Any]) -> bool:
    return (
        row.get("information_density_band") == "high"
        and row.get("q6_plannable_p90_misses_truth") is True
    )


def _is_normal_case(row: Mapping[str, Any]) -> bool:
    return (
        row.get("calibration_eligible") is True
        and bool(row.get("v2_matched"))
        and row.get("v2_decision_value_p50_error") is not None
        and not _is_early_diagnostic(row)
        and not _is_tail_event(row)
        and not _is_hidden_case(row)
    )


def _case_tags(row: Mapping[str, Any]) -> list[str]:
    tags: list[str] = []
    if _is_normal_case(row):
        tags.append("normal_case")
    if _is_early_diagnostic(row):
        tags.append("early_diagnostic")
    if row.get("capture_round") == 1:
        tags.append("single_round")
    if _is_tail_event(row):
        tags.append("tail_event")
    if _is_hidden_case(row):
        tags.append("hidden_case")
    if _is_no_q6_control(row):
        tags.append("no_q6_control")
    if _is_zero_q6_proven(row):
        tags.append("zero_q6_proven")
    if _is_high_info_value_miss(row):
        tags.append("high_info_value_miss")
    if _is_high_info_q6_miss(row):
        tags.append("high_info_q6_miss")

    avg_cells_band = str(row.get("public_avg_cells_solution_band") or "none")
    if avg_cells_band in {"all_unique", "mixed_unique"}:
        tags.append("avg_cells_unique")
    elif avg_cells_band == "ambiguous":
        tags.append("avg_cells_ambiguous")

    if row.get("public_max_quality_used"):
        tags.append("public_max_quality")
    if row.get("public_max_item_cells_used"):
        tags.append("public_max_item_cells")
    return tags


def _capture_round(events: Any) -> int | None:
    """Return the visible evidence coverage round for settlement captures."""

    latest_round = _latest_round(events)
    action_round = max(
        (len(state.action_results) for state in events.states),
        default=0,
    )
    visible_round = max(latest_round or 0, action_round)
    return visible_round or None


def _format_random_sample_avg_values(
    values: Iterable[tuple[int, float]],
) -> str:
    return ";".join(
        f"n={sample_count}:avg={value:.2f}"
        for sample_count, value in values
    )


def _category_action_combo(events: Any) -> str:
    action_ids: list[int] = []
    seen: set[int] = set()
    for state in events.states:
        for result in state.action_results:
            action_id = int(result.action_id)
            if action_id not in _CATEGORY_OUTLINE_ACTIONS or action_id in seen:
                continue
            seen.add(action_id)
            action_ids.append(action_id)
    return ";".join(
        f"{action_id}:{_CATEGORY_ACTION_LABELS.get(action_id, str(action_id))}"
        for action_id in action_ids
    )


def _map_family(file: str, map_id: int | None) -> str:
    if map_id is not None:
        prefix = map_id // 100
        if map_id == 2601:
            return "hidden"
        if prefix in {24, 34, 44}:
            return "villa"
        if prefix in {25, 35, 45}:
            return "shipwreck"
        return f"map_{prefix}xx"
    lower = file.lower()
    if "hidden" in lower or "secret" in lower:
        return "hidden"
    if "villa" in lower:
        return "villa"
    if "shipwreck" in lower:
        return "shipwreck"
    if map_id is None:
        return "unknown"
    return f"map_{map_id // 100}xx"


def _value_tier(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value < 300_000:
        return "<300k"
    if value < 800_000:
        return "300k-800k"
    if value < 1_200_000:
        return "800k-1.2m"
    return ">=1.2m"


def _counter_sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, int):
        return (0, value)
    return (1, str(value))


def _format_counter(counter: Counter[Any]) -> str:
    return ";".join(
        f"{key}:{count}"
        for key, count in sorted(
            counter.items(),
            key=lambda item: _counter_sort_key(item[0]),
        )
        if count
    )


def _parse_counter_field(value: Any) -> Counter[str]:
    counts: Counter[str] = Counter()
    for part in str(value or "").split(";"):
        if not part or ":" not in part:
            continue
        key, raw_count = part.rsplit(":", 1)
        try:
            count = int(raw_count)
        except ValueError:
            continue
        if key:
            counts[key] += count
    return counts


def _public_info_semantic(info_id: int) -> dict[str, str]:
    return _v3_public_info_semantic(info_id)


def _public_info_row_summary(events: Any) -> dict[str, Any]:
    ids: Counter[int] = Counter()
    semantics: Counter[str] = Counter()
    model_uses: Counter[str] = Counter()
    constraints: Counter[str] = Counter()
    value_ids: Counter[int] = Counter()
    item_event_ids: Counter[int] = Counter()
    observed_item_counts: Counter[int] = Counter()
    with_item_id: Counter[int] = Counter()
    with_shape: Counter[int] = Counter()
    with_local: Counter[int] = Counter()
    with_quality: Counter[int] = Counter()

    for state in getattr(events, "states", ()) or ():
        for info in getattr(state, "public_infos", ()) or ():
            info_id = _int_or_none(getattr(info, "info_id", None))
            if info_id is None:
                continue
            semantic = _public_info_semantic(info_id)
            observed_items = tuple(getattr(info, "observed_items", ()) or ())
            ids[info_id] += 1
            semantics[semantic["semantic"]] += 1
            model_uses[semantic["model_use"]] += 1
            constraints[semantic["constraint"]] += 1
            if getattr(info, "value", None) is not None:
                value_ids[info_id] += 1
            if observed_items:
                item_event_ids[info_id] += 1
                observed_item_counts[info_id] += len(observed_items)
            for item in observed_items:
                if getattr(item, "item_id", None) is not None:
                    with_item_id[info_id] += 1
                if getattr(item, "shape_code", None) is not None:
                    with_shape[info_id] += 1
                if getattr(item, "local_index", None) is not None:
                    with_local[info_id] += 1
                if getattr(item, "quality", None) is not None:
                    with_quality[info_id] += 1

    pending_ids = [
        str(info_id)
        for info_id in sorted(ids)
        if _public_info_semantic(info_id)["constraint"] in {"pending", "unknown"}
    ]
    unknown_ids = [
        str(info_id)
        for info_id in sorted(ids)
        if _public_info_semantic(info_id)["reference"] == "missing"
    ]
    return {
        "public_info_ids": _format_counter(ids),
        "public_info_semantics": _format_counter(semantics),
        "public_info_model_uses": _format_counter(model_uses),
        "public_info_constraint_levels": _format_counter(constraints),
        "public_info_value_ids": _format_counter(value_ids),
        "public_info_item_event_ids": _format_counter(item_event_ids),
        "public_info_observed_item_counts": _format_counter(observed_item_counts),
        "public_info_items_with_item_id": _format_counter(with_item_id),
        "public_info_items_with_shape": _format_counter(with_shape),
        "public_info_items_with_local": _format_counter(with_local),
        "public_info_items_with_quality": _format_counter(with_quality),
        "public_info_pending_model_ids": ";".join(pending_ids),
        "public_info_unknown_ids": ";".join(unknown_ids),
        "public_info_needs_screenshot_ids": ";".join(unknown_ids),
    }


def _avg_cells_candidate_bounds(
    *,
    map_id: int,
    tables: Any,
    quality: int | None,
) -> tuple[int, int, int, int]:
    """Return broad review bounds for avg-cells integer inversion."""

    try:
        sampler = prepare_session_sampler(
            map_id,
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
        )
    except Exception:
        max_count = (
            _AVG_CELLS_REVIEW_MAX_TOTAL_COUNT
            if quality is None
            else _AVG_CELLS_REVIEW_MAX_QUALITY_COUNT
        )
        return 1, max_count, 1, _AVG_CELLS_REVIEW_MAX_CELLS

    if quality is not None and not any(
        int(item.quality) == quality
        for pool in sampler.pools
        for item in pool.items
    ):
        return 1, 0, 1, 0

    max_count = (
        _AVG_CELLS_REVIEW_MAX_TOTAL_COUNT
        if quality is None
        else _AVG_CELLS_REVIEW_MAX_QUALITY_COUNT
    )
    return 1, max_count, 1, _AVG_CELLS_REVIEW_MAX_CELLS


def _avg_cells_integer_candidates(
    avg_cells: float,
    *,
    min_count: int,
    max_count: int,
    min_cells: int,
    max_cells: int,
    tolerance: float = 1e-4,
) -> list[tuple[int, int]]:
    if avg_cells <= 0 or max_count < min_count or max_cells < min_cells:
        return []
    candidates: list[tuple[int, int]] = []
    for count in range(max(1, min_count), max_count + 1):
        raw_cells = avg_cells * count
        cells = int(round(raw_cells))
        if abs(raw_cells - cells) > tolerance:
            continue
        if cells < min_cells or cells > max_cells:
            continue
        candidates.append((count, cells))
    return candidates


def _public_avg_cells_solution_summary(
    events: Any,
    *,
    map_id: int,
    tables: Any,
) -> dict[str, str]:
    parts: list[str] = []
    statuses: list[str] = []
    seen: set[tuple[int, float]] = set()
    for state in getattr(events, "states", ()) or ():
        for info in getattr(state, "public_infos", ()) or ():
            info_id = _int_or_none(getattr(info, "info_id", None))
            if info_id not in _PUBLIC_AVG_CELLS_INFO:
                continue
            try:
                avg_cells = float(getattr(info, "value", None))
            except (TypeError, ValueError):
                continue
            if avg_cells <= 0:
                continue
            key = (int(info_id), avg_cells)
            if key in seen:
                continue
            seen.add(key)
            label, quality = _PUBLIC_AVG_CELLS_INFO[int(info_id)]
            bounds = _avg_cells_candidate_bounds(
                map_id=map_id,
                tables=tables,
                quality=quality,
            )
            candidates = _avg_cells_integer_candidates(
                avg_cells,
                min_count=bounds[0],
                max_count=bounds[1],
                min_cells=bounds[2],
                max_cells=bounds[3],
            )
            if len(candidates) == 1:
                status = "unique"
            elif candidates:
                status = "ambiguous"
            else:
                status = "no_candidate"
            statuses.append(f"{label}:{status}")
            preview = ",".join(
                f"count={count}/cells={cells}" for count, cells in candidates[:5]
            )
            if len(candidates) > 5:
                preview += f",+{len(candidates) - 5}"
            parts.append(
                f"{label}:avg={avg_cells:.6f}:status={status}:"
                f"candidates={len(candidates)}"
                + (f":{preview}" if preview else "")
            )
    if not statuses:
        band = "none"
    elif all(status.endswith(":unique") for status in statuses):
        band = "all_unique"
    elif any(status.endswith(":unique") for status in statuses):
        band = "mixed_unique"
    elif any(status.endswith(":ambiguous") for status in statuses):
        band = "ambiguous"
    else:
        band = "no_candidate"
    return {
        "public_avg_cells_solution_band": band,
        "public_avg_cells_solution_statuses": ";".join(statuses),
        "public_avg_cells_solution_details": ";".join(parts),
    }


def _format_bucket_targets(problem: Any) -> str:
    parts: list[str] = []
    for quality, target in sorted(problem.bucket_targets.items()):
        fields: list[str] = []
        if target.count_exact is not None:
            fields.append(f"count={target.count_exact}")
        if target.total_cells_exact is not None:
            fields.append(f"cells={target.total_cells_exact}")
        if target.count_floor is not None and target.count_exact is None:
            fields.append(f"count>={target.count_floor}")
        if target.total_cells_floor is not None and target.total_cells_exact is None:
            fields.append(f"cells>={target.total_cells_floor}")
        if target.value_floor is not None:
            if getattr(target, "value_exact", None) is not None:
                fields.append(f"value={target.value_exact}")
            else:
                fields.append(f"value>={target.value_floor}")
        if target.avg_value is not None:
            fields.append(f"avg={target.avg_value:.2f}")
        avg_cells = getattr(target, "avg_cells", None)
        if avg_cells is not None:
            fields.append(f"avg_cells={float(avg_cells):.4f}")
        if fields:
            parts.append(f"q{quality}:" + ",".join(fields))
    return ";".join(parts)


def _format_presolve_unreachable_exact_buckets(
    problem: Any,
    payload: Mapping[str, object] | None,
) -> str:
    if payload is None:
        return ""
    parts: list[str] = []
    for quality, target in sorted(problem.bucket_targets.items()):
        reachable = is_quality_combo_reachable(
            payload,
            map_id=int(problem.map_id),
            quality=int(quality),
            count=target.count_exact,
            cells=target.total_cells_exact,
        )
        if reachable is False:
            parts.append(
                f"q{quality}:count={target.count_exact},cells={target.total_cells_exact}"
            )
    return ";".join(parts)


def _inventory_truth_breakdown(
    events: Any,
    items: Any,
    *,
    problem: Any | None = None,
    maps: Any | None = None,
    drops: Any | None = None,
    map_id: int | None = None,
) -> dict[str, Any]:
    return _inventory_quality_breakdown(
        events,
        items,
        problem=problem,
        maps=maps,
        drops=drops,
        map_id=map_id,
    )


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _anchor_band(anchor_count: Any) -> str:
    count = _int_or_none(anchor_count)
    if count is None:
        return "unknown"
    if count == 0:
        return "0"
    if count <= 2:
        return "1-2"
    if count <= 5:
        return "3-5"
    return "6+"


def _q6_top_size_band(row: dict[str, Any]) -> str:
    if int(row.get("final_q6_count") or 0) <= 0:
        return "no_q6"
    if _int_or_none(row.get("final_top_item_quality")) != 6:
        return "q6_not_top_item"
    cells = _int_or_none(row.get("final_top_item_cells"))
    if cells is None:
        return "q6_top_unknown_cells"
    if cells <= 2:
        return "q6_top_small"
    if cells <= 4:
        return "q6_top_compact"
    if cells <= 8:
        return "q6_top_medium"
    if cells <= 12:
        return "q6_top_large"
    return "q6_top_huge"


def _public_constraint_key(row: dict[str, Any]) -> str:
    parts: list[str] = []
    if row.get("public_max_quality_used"):
        parts.append("max_quality")
    if row.get("public_max_item_cells_used"):
        parts.append("max_item_cells")
    return "+".join(parts) if parts else "none"


def _evidence_profile_key(row: Mapping[str, Any]) -> str:
    parts: list[str] = []
    public_key = str(row.get("public_constraint_key") or "none")
    if public_key != "none":
        parts.append(f"public:{public_key}")
    random_sample_avg_values = (
        row.get("random_sample_avg_signal_values")
        if "random_sample_avg_signal_values" in row
        else row.get("random_sample_avg_values")
    )
    if str(random_sample_avg_values or ""):
        parts.append("public:random_avg")
    if int(row.get("category_action_count") or 0) > 0:
        parts.append("tool:category")
    if int(row.get("shape_target_count") or 0) > 0:
        parts.append("shape")
    if int(row.get("trusted_footprint_count") or 0) > 0:
        parts.append("layout")
    return "+".join(parts) if parts else "basic"


def _random_sample_avg_signal_band(row: Mapping[str, Any]) -> str:
    if not str(row.get("random_sample_avg_values") or ""):
        return "none"
    if str(row.get("random_sample_avg_signal_values") or ""):
        return "signal"
    return "low_filtered"


def _evidence_profile_key_from_problem(
    problem: Any,
    *,
    random_sample_avg_profile_floor: float = RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR,
) -> str:
    return evidence_profile_key_from_problem(
        problem,
        random_sample_avg_signal_floor=random_sample_avg_profile_floor,
    )


def _q6_residual_boost_for_profile(
    *,
    hero: str | None,
    map_family: str,
    evidence_profile_key: str,
    requested_boost: float,
    gate: str,
    bottom_row: int | None = None,
) -> float:
    return q6_residual_boost_for_profile(
        hero=hero,
        map_family=map_family,
        evidence_profile_key=evidence_profile_key,
        requested_boost=requested_boost,
        gate=gate,
        bottom_row=bottom_row,
    )


def _exact_bucket_markers(bucket_targets: Any) -> list[str]:
    text = str(bucket_targets or "")
    markers: list[str] = []
    for match in re.finditer(r"q(?P<q>\d+):(?P<fields>[^;]+)", text):
        quality = match.group("q")
        fields = match.group("fields")
        has_count = "count=" in fields
        has_cells = "cells=" in fields
        has_value = "value=" in fields
        if has_count and has_cells:
            markers.append(f"q{quality}_exact_count_cells")
        elif has_count:
            markers.append(f"q{quality}_exact_count")
        elif has_cells:
            markers.append(f"q{quality}_exact_cells")
        if has_value:
            markers.append(f"q{quality}_exact_value")
        if "avg=" in fields:
            markers.append(f"q{quality}_avg_value")
        if "avg_cells=" in fields:
            markers.append(f"q{quality}_avg_cells")
    return markers


def _zero_match_root(row: dict[str, Any]) -> str:
    if row.get("v2_matched"):
        return ""
    markers: list[str] = []
    if row.get("layout_conflict"):
        markers.append("layout_conflict")
        markers.extend(
            part
            for part in str(row.get("layout_conflict_root") or "").split(";")
            if part
        )
    if row.get("relaxed_exact_used"):
        markers.append("relaxed_exact_fallback")
    if row.get("public_max_quality_used"):
        markers.append("public_max_quality")
    if row.get("public_max_item_cells_used"):
        markers.append("public_max_item_cells")
    if row.get("presolve_unreachable_exact_buckets"):
        markers.append("presolve_unreachable_exact_bucket")
    markers.extend(_exact_bucket_markers(row.get("bucket_targets")))
    if not markers:
        markers.append("unclassified")
    return ";".join(dict.fromkeys(markers))


def _q6_miss_root(row: dict[str, Any]) -> str:
    if not row.get("q6_p90_misses_truth"):
        return ""
    markers: list[str] = []
    if row.get("q6_false_low_risk"):
        markers.append("low_q6_sample_rate")
    if "q6_below_drop_prior:" in str(row.get("diagnostics") or ""):
        markers.append("below_drop_prior")
    markers.append(_q6_top_size_band(row))
    if row.get("layout_conflict"):
        markers.append("layout_conflict")
        markers.extend(
            part
            for part in str(row.get("layout_conflict_root") or "").split(";")
            if part
        )
    if row.get("relaxed_exact_used"):
        markers.append("relaxed_exact_fallback")
    if row.get("public_max_quality_used"):
        markers.append("public_max_quality")
    if row.get("public_max_item_cells_used"):
        markers.append("public_max_item_cells")
    if row.get("presolve_unreachable_exact_buckets"):
        markers.append("presolve_unreachable_exact_bucket")
    markers.extend(
        marker
        for marker in _exact_bucket_markers(row.get("bucket_targets"))
        if marker.startswith("q6_")
    )
    return ";".join(dict.fromkeys(markers))


def _q6_plannable_miss_root(row: dict[str, Any]) -> str:
    if not row.get("q6_plannable_p90_misses_truth"):
        return ""
    markers: list[str] = []
    q6_rate = row.get("v2_q6_match_rate")
    if q6_rate is None:
        markers.append("unknown_q6_sample_rate")
    elif float(q6_rate) < 0.10:
        markers.append("low_q6_sample_rate")
    elif float(q6_rate) >= 0.80:
        markers.append("low_q6_value_distribution")
    else:
        markers.append("mixed_q6_sample_value")
    if row.get("q6_below_drop_prior"):
        markers.append("below_drop_prior")
    if int(row.get("v2_q6_count_p90_under_by") or 0) > 0:
        markers.append("q6_count_under")
    if int(row.get("v2_q6_cells_p90_under_by") or 0) > 0:
        markers.append("q6_cells_under")
    if float(row.get("v2_q6_count_p90_under_prior_by") or 0) > 0:
        markers.append("q6_count_below_prior")
    if float(row.get("v2_q6_cells_p90_under_prior_by") or 0) > 0:
        markers.append("q6_cells_below_prior")
    if float(row.get("v2_q6_space_pressure_p90") or 0) >= 1.0:
        markers.append("q6_space_pressure_high")
    if float(row.get("v2_q6_space_overflow_rate") or 0) > 0:
        markers.append("q6_space_overflow")
    markers.append(_q6_top_size_band(row))
    if row.get("layout_conflict"):
        markers.append("layout_conflict")
        markers.extend(
            part
            for part in str(row.get("layout_conflict_root") or "").split(";")
            if part
        )
    if row.get("relaxed_exact_used"):
        markers.append("relaxed_exact_fallback")
    if row.get("public_max_quality_used"):
        markers.append("public_max_quality")
    if row.get("public_max_item_cells_used"):
        markers.append("public_max_item_cells")
    if row.get("presolve_unreachable_exact_buckets"):
        markers.append("presolve_unreachable_exact_bucket")
    markers.extend(
        marker
        for marker in _exact_bucket_markers(row.get("bucket_targets"))
        if marker.startswith("q6_")
    )
    return ";".join(dict.fromkeys(markers))


def evaluate_path(
    path: Path,
    *,
    tables: Any,
    n_trials: int,
    seed: int,
    cells_tol: int,
    count_tol: int,
    combo_presolve: Mapping[str, object] | None = None,
    q6_residual_boost: float = 1.0,
    q6_residual_boost_gate: str = "all",
    q6_residual_prior_floor_ratio: float = 0.0,
    q6_residual_prior_cell_floor_ratio: float = 0.0,
    q6_residual_prior_floor_gate: str = "all",
    q6_residual_value_power: float = 0.0,
    q6_residual_value_gate: str = "all",
    q6_conditional_target_count: float = 0.0,
    q6_conditional_target_cells: float = 0.0,
    q6_conditional_target_gate: str = "none",
    q6_conditional_value_power: float = 0.0,
    random_sample_avg_profile_floor: float = RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR,
) -> dict[str, Any]:
    try:
        events = parse_fatbeans_capture(path)
        batches = live_batches_from_fatbeans_events(events)
        base_session, *_ = _states_to_session(batches)
        final_value = _inventory_value(events, tables.items)
        inventory_count, final_cells = _inventory_totals(events)
        capture_round = _capture_round(events)
        category_action_combo = _category_action_combo(events)
        latest_bids = latest_player_bids(events.states)
        highest_bid = max(latest_bids.values()) if latest_bids else None
        if base_session is None:
            return {"file": path.name, "status": "skip", "reason": "no_session_obs"}
        if final_value is None:
            return {"file": path.name, "status": "skip", "reason": "no_inventory_truth"}

        store = evidence_store_from_fatbeans_events(events)
        problem = build_residual_problem(
            base_session.map_id,
            store,
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
            obs=base_session,
        )
        map_family = _map_family(path.name, base_session.map_id)
        pre_profile_key = _evidence_profile_key_from_problem(
            problem,
            random_sample_avg_profile_floor=random_sample_avg_profile_floor,
        )
        active_q6_residual_boost = _q6_residual_boost_for_profile(
            hero=base_session.hero,
            map_family=map_family,
            evidence_profile_key=pre_profile_key,
            requested_boost=q6_residual_boost,
            gate=q6_residual_boost_gate,
            bottom_row=problem.layout.bottom_row,
        )
        active_q6_residual_prior_floor_ratio = (
            q6_residual_prior_floor_ratio_for_profile(
                hero=base_session.hero,
                map_family=map_family,
                evidence_profile_key=pre_profile_key,
                requested_ratio=q6_residual_prior_floor_ratio,
                gate=q6_residual_prior_floor_gate,
                bottom_row=problem.layout.bottom_row,
            )
        )
        active_q6_residual_prior_cell_floor_ratio = (
            q6_residual_prior_cell_floor_ratio
            if active_q6_residual_prior_floor_ratio > 0
            and q6_residual_prior_cell_floor_ratio > 0
            else 0.0
        )
        active_q6_residual_value_power = q6_residual_value_power_for_profile(
            hero=base_session.hero,
            map_family=map_family,
            evidence_profile_key=pre_profile_key,
            requested_power=q6_residual_value_power,
            gate=q6_residual_value_gate,
            bottom_row=problem.layout.bottom_row,
        )
        active_q6_conditional_target = q6_conditional_target_active_for_profile(
            hero=base_session.hero,
            map_family=map_family,
            evidence_profile_key=pre_profile_key,
            gate=q6_conditional_target_gate,
        )
        active_q6_conditional_target_count = (
            q6_conditional_target_count if active_q6_conditional_target else 0.0
        )
        active_q6_conditional_target_cells = (
            q6_conditional_target_cells if active_q6_conditional_target else 0.0
        )
        active_q6_conditional_value_power = (
            q6_conditional_value_power if active_q6_conditional_target else 0.0
        )
        truth_breakdown = _inventory_truth_breakdown(
            events,
            tables.items,
            problem=problem,
            maps=tables.maps,
            drops=tables.drops,
            map_id=base_session.map_id,
        )
        report = estimate_posterior_v2(
            base_session.map_id,
            base_session,
            store,
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
            n_trials=n_trials,
            seed=seed,
            cells_tol=cells_tol,
            count_tol=count_tol,
            q6_residual_boost=active_q6_residual_boost,
            q6_residual_prior_floor_ratio=active_q6_residual_prior_floor_ratio,
            q6_residual_prior_cell_floor_ratio=(
                active_q6_residual_prior_cell_floor_ratio
            ),
            q6_residual_value_power=active_q6_residual_value_power,
            q6_conditional_target_count=active_q6_conditional_target_count,
            q6_conditional_target_cells=active_q6_conditional_target_cells,
            q6_conditional_value_power=active_q6_conditional_value_power,
        )
        value_p10 = _round(report.total_value.p10 if report.total_value else None)
        value_p50 = _round(report.total_value.p50 if report.total_value else None)
        value_p90 = _round(report.total_value.p90 if report.total_value else None)
        decision_p10 = _round(report.decision_value.p10 if report.decision_value else None)
        decision_p50 = _round(report.decision_value.p50 if report.decision_value else None)
        decision_p90 = _round(report.decision_value.p90 if report.decision_value else None)
        tail_replacement_decision_p90 = _round(
            report.tail_replacement_decision_value.p90
            if report.tail_replacement_decision_value
            else None
        )
        q6_value_p50 = _round(report.q6_value.p50 if report.q6_value else None)
        q6_value_p90 = _round(report.q6_value.p90 if report.q6_value else None)
        q6_decision_value_p50 = _round(
            report.q6_decision_value.p50
            if report.q6_decision_value
            else None
        )
        q6_decision_value_p90 = _round(
            report.q6_decision_value.p90
            if report.q6_decision_value
            else None
        )
        q6_tail_replacement_decision_value_p90 = _round(
            report.q6_tail_replacement_decision_value.p90
            if report.q6_tail_replacement_decision_value
            else None
        )
        q6_count_p90 = _round(report.q6_count.p90 if report.q6_count else None)
        q6_cells_p90 = _round(report.q6_cells.p90 if report.q6_cells else None)
        remaining_cells_after_layout_p10 = _round(
            report.remaining_cells_after_layout.p10
            if report.remaining_cells_after_layout
            else None
        )
        remaining_cells_after_layout_p50 = _round(
            report.remaining_cells_after_layout.p50
            if report.remaining_cells_after_layout
            else None
        )
        remaining_cells_after_layout_p90 = _round(
            report.remaining_cells_after_layout.p90
            if report.remaining_cells_after_layout
            else None
        )
        q6_space_pressure_p50 = _round_float(
            report.q6_space_pressure.p50
            if report.q6_space_pressure
            else None,
            3,
        )
        q6_space_pressure_p90 = _round_float(
            report.q6_space_pressure.p90
            if report.q6_space_pressure
            else None,
            3,
        )
        q6_space_overflow_rate = _round_float(report.q6_space_overflow_rate, 4)
        cells_p10 = _round(report.total_cells.p10 if report.total_cells else None)
        cells_p50 = _round(report.total_cells.p50 if report.total_cells else None)
        cells_p90 = _round(report.total_cells.p90 if report.total_cells else None)
        diagnostics = ";".join(report.diagnostics)
        layout_diagnostics = ";".join(report.layout_diagnostics)
        layout_root = layout_conflict_root(
            layout_diagnostics,
            footprint_count=problem.layout.footprint_count,
            trusted_footprint_count=problem.layout.trusted_footprint_count,
        )
        final_q6_value = int(truth_breakdown.get("final_q6_value") or 0)
        final_q6_decision_value = int(
            truth_breakdown.get("final_q6_decision_value") or 0
        )
        final_q6_tail_replacement_value = int(
            truth_breakdown.get("final_q6_tail_replacement_value") or 0
        )
        final_q6_decision_value_with_tail_replacement = int(
            truth_breakdown.get(
                "final_q6_decision_value_with_tail_replacement"
            )
            or final_q6_decision_value
        )
        has_formal_decision_truth = truth_breakdown.get("final_decision_value") is not None
        has_replacement_decision_truth = (
            truth_breakdown.get("final_decision_value_with_tail_replacement")
            is not None
        )
        final_formal_decision_value = (
            int(truth_breakdown["final_decision_value"])
            if has_formal_decision_truth
            else None
        )
        final_replacement_decision_value = (
            int(truth_breakdown["final_decision_value_with_tail_replacement"])
            if has_replacement_decision_truth
            else final_formal_decision_value
            if has_formal_decision_truth
            else final_value
        )
        if has_replacement_decision_truth and (
            not has_formal_decision_truth
            or final_replacement_decision_value != final_formal_decision_value
        ):
            decision_value_truth_source = "tail_replacement"
        elif has_formal_decision_truth:
            decision_value_truth_source = "formal"
        else:
            decision_value_truth_source = "raw"
        row = {
            "file": path.name,
            "status": "ok",
            "hero": base_session.hero,
            "map_id": base_session.map_id,
            "map_family": map_family,
            "value_tier": _value_tier(final_value),
            "inventory_count": inventory_count,
            "capture_round": capture_round,
            "evidence_stage": _evidence_stage(capture_round),
            "calibration_eligible": (
                capture_round is not None and capture_round >= 3
            ),
            "category_action_combo": category_action_combo,
            "category_action_count": (
                len(category_action_combo.split(";"))
                if category_action_combo
                else 0
            ),
            **_public_info_row_summary(events),
            **_public_avg_cells_solution_summary(
                events,
                map_id=base_session.map_id,
                tables=tables,
            ),
            "final_value": final_value,
            "final_cells": final_cells,
            "highest_bid": highest_bid,
            "highest_bid_over_final": (
                highest_bid / final_value
                if highest_bid is not None and final_value > 0
                else None
            ),
            "highest_bid_minus_final": (
                highest_bid - final_value if highest_bid is not None else None
            ),
            **truth_breakdown,
            "v2_matched": report.n_matched,
            "v2_match_rate": report.n_matched / max(1, report.n_total),
            "v2_value_p10": value_p10,
            "v2_value_p50": value_p50,
            "v2_value_p90": value_p90,
            "v2_decision_value_p10": decision_p10,
            "v2_decision_value_p50": decision_p50,
            "v2_decision_value_p90": decision_p90,
            "v2_tail_replacement_decision_value_p90": (
                tail_replacement_decision_p90
            ),
            "v2_decision_value_truth": final_replacement_decision_value,
            "v2_decision_value_truth_source": decision_value_truth_source,
            "v2_decision_value_p50_error": (
                decision_p50 - final_replacement_decision_value
                if decision_p50 is not None
                else None
            ),
            "v2_decision_value_p90_error": (
                decision_p90 - final_replacement_decision_value
                if decision_p90 is not None
                else None
            ),
            "v2_decision_value_p50_error_vs_formal": (
                decision_p50 - final_formal_decision_value
                if decision_p50 is not None
                and final_formal_decision_value is not None
                else None
            ),
            "v2_decision_value_p90_error_vs_formal": (
                decision_p90 - final_formal_decision_value
                if decision_p90 is not None
                and final_formal_decision_value is not None
                else None
            ),
            "v2_decision_value_p50_error_vs_raw": (
                decision_p50 - final_value
                if decision_p50 is not None
                else None
            ),
            "v2_decision_value_p90_error_vs_raw": (
                decision_p90 - final_value
                if decision_p90 is not None
                else None
            ),
            "v2_q6_match_rate": report.q6_match_rate,
            "v2_q6_prior_match_rate": report.q6_prior_match_rate,
            "v2_q6_prior_expected_count": _round_float(
                report.q6_prior_expected_count,
                2,
            ),
            "v2_q6_prior_expected_cells": _round_float(
                report.q6_prior_expected_cells,
                1,
            ),
            "v2_q6_prior_expected_value": _round(report.q6_prior_expected_value),
            "v2_q6_value_p50": q6_value_p50,
            "v2_q6_value_p90": q6_value_p90,
            "v2_q6_decision_value_p50": q6_decision_value_p50,
            "v2_q6_decision_value_p90": q6_decision_value_p90,
            "v2_q6_tail_replacement_decision_value_p90": (
                q6_tail_replacement_decision_value_p90
            ),
            "v2_q6_count_p90": q6_count_p90,
            "v2_q6_cells_p90": q6_cells_p90,
            "v2_remaining_cells_after_layout_p10": remaining_cells_after_layout_p10,
            "v2_remaining_cells_after_layout_p50": remaining_cells_after_layout_p50,
            "v2_remaining_cells_after_layout_p90": remaining_cells_after_layout_p90,
            "v2_q6_space_pressure_p50": q6_space_pressure_p50,
            "v2_q6_space_pressure_p90": q6_space_pressure_p90,
            "v2_q6_space_overflow_rate": q6_space_overflow_rate,
            "v2_q6_count_p90_under_by": (
                max(0, int(truth_breakdown.get("final_q6_count") or 0) - q6_count_p90)
                if q6_count_p90 is not None
                else None
            ),
            "v2_q6_cells_p90_under_by": (
                max(0, int(truth_breakdown.get("final_q6_cells") or 0) - q6_cells_p90)
                if q6_cells_p90 is not None
                else None
            ),
            "v2_q6_count_p90_under_prior_by": (
                _round_float(report.q6_prior_expected_count - q6_count_p90, 2)
                if report.q6_prior_expected_count is not None
                and q6_count_p90 is not None
                and q6_count_p90 < report.q6_prior_expected_count
                else 0.0
            ),
            "v2_q6_cells_p90_under_prior_by": (
                _round_float(report.q6_prior_expected_cells - q6_cells_p90, 1)
                if report.q6_prior_expected_cells is not None
                and q6_cells_p90 is not None
                and q6_cells_p90 < report.q6_prior_expected_cells
                else 0.0
            ),
            "v2_q6_value_p90_error": (
                q6_value_p90 - final_q6_value if q6_value_p90 is not None else None
            ),
            "v2_q6_value_p90_under_by": (
                max(0, final_q6_value - q6_value_p90)
                if q6_value_p90 is not None
                else None
            ),
            "v2_q6_decision_value_p90_under_by": (
                max(0, final_q6_decision_value - q6_decision_value_p90)
                if q6_decision_value_p90 is not None
                else None
            ),
            "v2_q6_tail_replacement_decision_value_p90_under_by": (
                max(
                    0,
                    final_q6_decision_value_with_tail_replacement
                    - q6_decision_value_p90,
                )
                if q6_decision_value_p90 is not None
                and final_q6_tail_replacement_value > 0
                else None
            ),
            "v2_q6_tail_replacement_estimate_p90_under_by": (
                max(
                    0,
                    final_q6_decision_value_with_tail_replacement
                    - q6_tail_replacement_decision_value_p90,
                )
                if q6_tail_replacement_decision_value_p90 is not None
                and final_q6_tail_replacement_value > 0
                else None
            ),
            "v2_value_p50_error": (
                value_p50 - final_value if value_p50 is not None else None
            ),
            "v2_value_p90_error": (
                value_p90 - final_value if value_p90 is not None else None
            ),
            "v2_value_p90_covers_final": (
                value_p90 >= final_value if value_p90 is not None else None
            ),
            "v2_cells_p10": cells_p10,
            "v2_cells_p50": cells_p50,
            "v2_cells_p90": cells_p90,
            "v2_cells_p50_error": (
                cells_p50 - final_cells
                if cells_p50 is not None and final_cells is not None
                else None
            ),
            "v2_cells_p90_error": (
                cells_p90 - final_cells
                if cells_p90 is not None and final_cells is not None
                else None
            ),
            "anchor_count": report.anchor_count,
            "known_value": report.known_value,
            "layout_score": report.layout_score,
            "layout_diagnostics": layout_diagnostics,
            "diagnostics": diagnostics,
            "q6_residual_boost": active_q6_residual_boost,
            "q6_residual_boost_gate": q6_residual_boost_gate,
            "q6_residual_prior_floor_ratio": active_q6_residual_prior_floor_ratio,
            "q6_residual_prior_cell_floor_ratio": (
                active_q6_residual_prior_cell_floor_ratio
            ),
            "q6_residual_prior_floor_gate": q6_residual_prior_floor_gate,
            "q6_residual_value_power": active_q6_residual_value_power,
            "q6_residual_value_gate": q6_residual_value_gate,
            "q6_conditional_target_count": active_q6_conditional_target_count,
            "q6_conditional_target_cells": active_q6_conditional_target_cells,
            "q6_conditional_target_gate": q6_conditional_target_gate,
            "q6_conditional_value_power": active_q6_conditional_value_power,
            "relaxed_exact_used": "relaxed_exact_bucket_targets:" in diagnostics,
            "public_max_quality_used": "public_max_quality:" in diagnostics,
            "public_max_item_cells_used": "public_max_item_cells:" in diagnostics,
            "q6_below_drop_prior": "q6_below_drop_prior:" in diagnostics,
            "layout_conflict": bool(layout_root),
            "layout_conflict_root": layout_root,
            "q6_false_low_risk": (
                final_q6_value > 0
                and report.q6_match_rate is not None
                and report.q6_match_rate < 0.10
            ),
            "q6_p90_misses_truth": (
                final_q6_value > 0
                and q6_value_p90 is not None
                and q6_value_p90 < final_q6_value
            ),
            "q6_plannable_p90_misses_truth": (
                final_q6_decision_value > 0
                and q6_decision_value_p90 is not None
                and q6_decision_value_p90 < final_q6_decision_value
            ),
            "q6_tail_replacement_p90_misses_truth": (
                final_q6_tail_replacement_value > 0
                and q6_decision_value_p90 is not None
                and q6_decision_value_p90
                < final_q6_decision_value_with_tail_replacement
            ),
            "q6_tail_replacement_estimate_p90_misses_truth": (
                final_q6_tail_replacement_value > 0
                and q6_tail_replacement_decision_value_p90 is not None
                and q6_tail_replacement_decision_value_p90
                < final_q6_decision_value_with_tail_replacement
            ),
            "bucket_targets": _format_bucket_targets(problem),
            "presolve_unreachable_exact_buckets": (
                _format_presolve_unreachable_exact_buckets(problem, combo_presolve)
            ),
            "shape_target_count": len(problem.shape_targets),
            "category_target_count": len(problem.category_targets),
            "category_exclusion_count": sum(
                len(target.excluded_categories)
                for target in problem.category_targets
            ),
            "random_sample_avg_values": _format_random_sample_avg_values(
                problem.random_sample_avg_values
            ),
            "random_sample_avg_signal_values": _format_random_sample_avg_values(
                actionable_random_sample_avg_values(
                    problem.random_sample_avg_values,
                    signal_floor=random_sample_avg_profile_floor,
                )
            ),
            "random_sample_avg_profile_floor": random_sample_avg_profile_floor,
            "footprint_count": problem.layout.footprint_count,
            "trusted_footprint_count": problem.layout.trusted_footprint_count,
            "footprint_occupied_cells": problem.layout.occupied_cells,
            "footprint_bottom_row": problem.layout.bottom_row,
            "aisha_bottom_row_risk": aisha_bottom_row_risk(
                hero=base_session.hero,
                map_family=map_family,
                bottom_row=problem.layout.bottom_row,
            ),
        }
        row["public_constraint_key"] = _public_constraint_key(row)
        row["random_sample_avg_signal_band"] = _random_sample_avg_signal_band(row)
        row["evidence_profile_key"] = _evidence_profile_key(row)
        density_score = _information_density_score(row)
        row["information_density_score"] = density_score
        row["information_density_band"] = _information_density_band(density_score)
        row["density_value_tier"] = (
            f"{row['information_density_band']}|{row['value_tier']}"
        )
        row["hero_information_density"] = (
            f"{row['hero']}|{row['information_density_band']}"
        )
        row["hero_evidence_stage"] = f"{row['hero']}|{row['evidence_stage']}"
        row["anchor_band"] = _anchor_band(row.get("anchor_count"))
        row["q6_top_size_band"] = _q6_top_size_band(row)
        row["zero_match_root"] = _zero_match_root(row)
        row["q6_miss_root"] = _q6_miss_root(row)
        row["q6_plannable_miss_root"] = _q6_plannable_miss_root(row)
        row["case_tags"] = ";".join(_case_tags(row))
        return row
    except Exception as exc:
        return {
            "file": path.name,
            "status": "error",
            "reason": f"{type(exc).__name__}: {str(exc)[:160]}",
        }


def _q6_residual_floor_value(
    row: dict[str, Any],
    *,
    floor_ratio: float,
) -> int | None:
    if floor_ratio <= 0:
        return None
    if not row.get("q6_below_drop_prior"):
        return None
    if row.get("public_max_quality_used"):
        return None
    prior_value = row.get("v2_q6_prior_expected_value")
    if prior_value is None:
        return None
    return max(0, _round(float(prior_value) * floor_ratio) or 0)


def _q6_count_cell_prior_floor_value(
    row: dict[str, Any],
    *,
    floor_ratio: float,
) -> int | None:
    if floor_ratio <= 0:
        return None
    if row.get("public_max_quality_used"):
        return None
    if (
        float(row.get("v2_q6_count_p90_under_prior_by") or 0) <= 0
        and float(row.get("v2_q6_cells_p90_under_prior_by") or 0) <= 0
    ):
        return None
    prior_value = row.get("v2_q6_prior_expected_value")
    if prior_value is None:
        return None
    return max(0, _round(float(prior_value) * floor_ratio) or 0)


def _q6_low_space_residual_floor_value(
    row: dict[str, Any],
    *,
    floor_ratio: float,
) -> int | None:
    if row.get("v2_q6_space_pressure_p90") is None:
        return None
    if float(row.get("v2_q6_space_pressure_p90") or 0) >= 0.50:
        return None
    if float(row.get("v2_q6_space_overflow_rate") or 0) > 0:
        return None
    return _q6_count_cell_prior_floor_value(row, floor_ratio=floor_ratio)


def _q6_residual_floor_experiment(
    rows: list[dict[str, Any]],
    *,
    floor_ratio: float,
) -> dict[str, Any] | None:
    if floor_ratio <= 0:
        return None
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_value") or 0) > 0
        and row.get("v2_q6_value_p90") is not None
    ]
    if not q6_truth:
        return {
            "enabled": True,
            "floor_ratio": floor_ratio,
            "q6_truth_files": 0,
            "eligible_rows": 0,
            "q6_value_p90_coverage": None,
            "q6_p90_misses_truth": 0,
        }
    eligible_rows = 0
    adjusted_misses = 0
    floors: list[int] = []
    for row in q6_truth:
        q6_p90 = int(row.get("v2_q6_value_p90") or 0)
        floor_value = _q6_residual_floor_value(row, floor_ratio=floor_ratio)
        if floor_value is not None:
            eligible_rows += 1
            floors.append(floor_value)
            q6_p90 = max(q6_p90, floor_value)
        if q6_p90 < int(row.get("final_q6_value") or 0):
            adjusted_misses += 1
    return {
        "enabled": True,
        "floor_ratio": floor_ratio,
        "q6_truth_files": len(q6_truth),
        "eligible_rows": eligible_rows,
        "eligible_no_q6_rows": sum(
            1 for row in rows
            if int(row.get("final_q6_value") or 0) <= 0
            and _q6_residual_floor_value(row, floor_ratio=floor_ratio) is not None
        ),
        "floor_median": _round(statistics.median(floors)) if floors else None,
        "q6_value_p90_coverage": round(
            1.0 - adjusted_misses / len(q6_truth),
            4,
        ),
        "q6_p90_misses_truth": adjusted_misses,
        "groups": {
            "hero_map_family": _q6_residual_floor_group_summary(
                rows,
                ("hero", "map_family"),
                floor_ratio=floor_ratio,
            ),
            "map_family_value_tier": _q6_residual_floor_group_summary(
                rows,
                ("map_family", "value_tier"),
                floor_ratio=floor_ratio,
            ),
            "evidence_stage": _q6_residual_floor_group_summary(
                rows,
                ("evidence_stage",),
                floor_ratio=floor_ratio,
            ),
            "top_item_size": _q6_residual_floor_group_summary(
                rows,
                ("q6_top_size_band",),
                floor_ratio=floor_ratio,
            ),
        },
    }


def _q6_low_space_residual_floor_experiment(
    rows: list[dict[str, Any]],
    *,
    floor_ratio: float,
) -> dict[str, Any] | None:
    if floor_ratio <= 0:
        return None
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    if not q6_truth:
        return {
            "enabled": True,
            "floor_ratio": floor_ratio,
            "q6_plannable_truth_files": 0,
            "eligible_rows": 0,
            "q6_plannable_value_p90_coverage": None,
            "q6_plannable_p90_misses_truth": 0,
        }
    eligible_rows = 0
    adjusted_misses = 0
    floors: list[int] = []
    for row in q6_truth:
        q6_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        floor_value = _q6_low_space_residual_floor_value(
            row,
            floor_ratio=floor_ratio,
        )
        if floor_value is not None:
            eligible_rows += 1
            floors.append(floor_value)
            q6_p90 = max(q6_p90, floor_value)
        if q6_p90 < int(row.get("final_q6_decision_value") or 0):
            adjusted_misses += 1
    return {
        "enabled": True,
        "floor_ratio": floor_ratio,
        "gate": "low_space_pressure_count_cell_prior",
        "q6_plannable_truth_files": len(q6_truth),
        "eligible_rows": eligible_rows,
        "eligible_no_q6_rows": sum(
            1 for row in rows
            if int(row.get("final_q6_decision_value") or 0) <= 0
            and _q6_low_space_residual_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            is not None
        ),
        "floor_median": _round(statistics.median(floors)) if floors else None,
        "q6_plannable_value_p90_coverage": round(
            1.0 - adjusted_misses / len(q6_truth),
            4,
        ),
        "q6_plannable_p90_misses_truth": adjusted_misses,
        "groups": {
            "hero_map_profile": _q6_low_space_residual_floor_group_summary(
                rows,
                ("hero", "map_family", "evidence_profile_key"),
                floor_ratio=floor_ratio,
            ),
            "evidence_profile": _q6_low_space_residual_floor_group_summary(
                rows,
                ("evidence_profile_key",),
                floor_ratio=floor_ratio,
            ),
        },
    }


def _q6_low_space_residual_gated_floor_experiment(
    rows: list[dict[str, Any]],
    *,
    floor_ratio: float,
    gate_keys: tuple[str, ...] = ("hero", "map_family", "evidence_profile_key"),
    gate_name: str = "low_space_profile_positive_net",
    min_q6_truth: int = 10,
) -> dict[str, Any] | None:
    if floor_ratio <= 0:
        return None
    gate_rows = [
        row for row in _q6_low_space_residual_floor_group_summary(
            rows,
            gate_keys,
            floor_ratio=floor_ratio,
        )
        if int(row["net_improvement"]) > 0
        and int(row["q6_plannable_truth"]) >= min_q6_truth
    ]
    gate_groups = {str(row["group"]) for row in gate_rows}
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    if not q6_truth:
        return {
            "enabled": True,
            "floor_ratio": floor_ratio,
            "gate": gate_name,
            "gates": gate_rows,
            "q6_plannable_truth_files": 0,
            "eligible_rows": 0,
            "eligible_no_q6_rows": 0,
            "q6_plannable_value_p90_coverage": None,
            "q6_plannable_p90_misses_truth": 0,
        }

    eligible_rows = 0
    adjusted_misses = 0
    floors: list[int] = []
    for row in q6_truth:
        q6_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        if _group_key(row, gate_keys) in gate_groups:
            floor_value = _q6_low_space_residual_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            if floor_value is not None:
                eligible_rows += 1
                floors.append(floor_value)
                q6_p90 = max(q6_p90, floor_value)
        if q6_p90 < int(row.get("final_q6_decision_value") or 0):
            adjusted_misses += 1

    return {
        "enabled": True,
        "floor_ratio": floor_ratio,
        "gate": gate_name,
        "gates": gate_rows,
        "q6_plannable_truth_files": len(q6_truth),
        "eligible_rows": eligible_rows,
        "eligible_no_q6_rows": sum(
            1 for row in rows
            if int(row.get("final_q6_decision_value") or 0) <= 0
            and _group_key(row, gate_keys) in gate_groups
            and _q6_low_space_residual_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            is not None
        ),
        "floor_median": _round(statistics.median(floors)) if floors else None,
        "q6_plannable_value_p90_coverage": round(
            1.0 - adjusted_misses / len(q6_truth),
            4,
        ),
        "q6_plannable_p90_misses_truth": adjusted_misses,
    }


def _q6_count_cell_prior_floor_experiment(
    rows: list[dict[str, Any]],
    *,
    floor_ratio: float,
) -> dict[str, Any] | None:
    if floor_ratio <= 0:
        return None
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    if not q6_truth:
        return {
            "enabled": True,
            "floor_ratio": floor_ratio,
            "q6_plannable_truth_files": 0,
            "eligible_rows": 0,
            "q6_plannable_value_p90_coverage": None,
            "q6_plannable_p90_misses_truth": 0,
        }
    eligible_rows = 0
    adjusted_misses = 0
    floors: list[int] = []
    for row in q6_truth:
        q6_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        floor_value = _q6_count_cell_prior_floor_value(
            row,
            floor_ratio=floor_ratio,
        )
        if floor_value is not None:
            eligible_rows += 1
            floors.append(floor_value)
            q6_p90 = max(q6_p90, floor_value)
        if q6_p90 < int(row.get("final_q6_decision_value") or 0):
            adjusted_misses += 1
    return {
        "enabled": True,
        "floor_ratio": floor_ratio,
        "q6_plannable_truth_files": len(q6_truth),
        "eligible_rows": eligible_rows,
        "eligible_no_q6_rows": sum(
            1 for row in rows
            if int(row.get("final_q6_decision_value") or 0) <= 0
            and _q6_count_cell_prior_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            is not None
        ),
        "floor_median": _round(statistics.median(floors)) if floors else None,
        "q6_plannable_value_p90_coverage": round(
            1.0 - adjusted_misses / len(q6_truth),
            4,
        ),
        "q6_plannable_p90_misses_truth": adjusted_misses,
        "groups": {
            "hero_map_family": _q6_count_cell_prior_floor_group_summary(
                rows,
                ("hero", "map_family"),
                floor_ratio=floor_ratio,
            ),
            "top_item_size": _q6_count_cell_prior_floor_group_summary(
                rows,
                ("q6_top_size_band",),
                floor_ratio=floor_ratio,
            ),
            "evidence_stage": _q6_count_cell_prior_floor_group_summary(
                rows,
                ("evidence_stage",),
                floor_ratio=floor_ratio,
            ),
            "information_density": _q6_count_cell_prior_floor_group_summary(
                rows,
                ("information_density_band",),
                floor_ratio=floor_ratio,
            ),
        },
    }


def _q6_count_cell_prior_gated_floor_experiment(
    rows: list[dict[str, Any]],
    *,
    floor_ratio: float,
    gate_keys: tuple[str, ...] = ("hero", "map_family"),
    gate_name: str = "hero_map_family_positive_net",
    min_q6_truth: int = 1,
) -> dict[str, Any] | None:
    if floor_ratio <= 0:
        return None
    gate_rows = _q6_count_cell_prior_floor_gate_candidates(
        rows,
        floor_ratio=floor_ratio,
        keys=gate_keys,
        min_q6_truth=min_q6_truth,
    )
    gate_groups = {str(row["group"]) for row in gate_rows}
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    if not q6_truth:
        return {
            "enabled": True,
            "floor_ratio": floor_ratio,
            "gate": gate_name,
            "gates": gate_rows,
            "q6_plannable_truth_files": 0,
            "eligible_rows": 0,
            "eligible_no_q6_rows": 0,
            "q6_plannable_value_p90_coverage": None,
            "q6_plannable_p90_misses_truth": 0,
        }

    eligible_rows = 0
    adjusted_misses = 0
    floors: list[int] = []
    for row in q6_truth:
        q6_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        if _group_key(row, gate_keys) in gate_groups:
            floor_value = _q6_count_cell_prior_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            if floor_value is not None:
                eligible_rows += 1
                floors.append(floor_value)
                q6_p90 = max(q6_p90, floor_value)
        if q6_p90 < int(row.get("final_q6_decision_value") or 0):
            adjusted_misses += 1

    return {
        "enabled": True,
        "floor_ratio": floor_ratio,
        "gate": gate_name,
        "gates": gate_rows,
        "q6_plannable_truth_files": len(q6_truth),
        "eligible_rows": eligible_rows,
        "eligible_no_q6_rows": sum(
            1 for row in rows
            if int(row.get("final_q6_decision_value") or 0) <= 0
            and _group_key(row, gate_keys) in gate_groups
            and _q6_count_cell_prior_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            is not None
        ),
        "floor_median": _round(statistics.median(floors)) if floors else None,
        "q6_plannable_value_p90_coverage": round(
            1.0 - adjusted_misses / len(q6_truth),
            4,
        ),
        "q6_plannable_p90_misses_truth": adjusted_misses,
    }


def _q6_count_cell_prior_named_gate_active(
    row: Mapping[str, Any],
    *,
    gate: str,
) -> bool:
    return (
        q6_residual_prior_floor_ratio_for_profile(
            hero=str(row.get("hero") or ""),
            map_family=str(row.get("map_family") or ""),
            evidence_profile_key=str(row.get("evidence_profile_key") or ""),
            requested_ratio=1.0,
            gate=gate,
            bottom_row=_int_or_none(row.get("footprint_bottom_row")),
        )
        > 0
    )


def _q6_count_cell_prior_named_gate_p90_experiment(
    rows: list[dict[str, Any]],
    *,
    gate: str,
    floor_ratio: float,
) -> dict[str, Any] | None:
    if floor_ratio <= 0:
        return None
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    active_rows = [
        row for row in rows
        if _q6_count_cell_prior_named_gate_active(row, gate=gate)
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    active_q6_truth = [
        row for row in active_rows
        if int(row.get("final_q6_decision_value") or 0) > 0
    ]
    active_no_q6 = [
        row for row in active_rows
        if int(row.get("final_q6_decision_value") or 0) <= 0
    ]

    eligible_rows = 0
    eligible_q6_truth_rows = 0
    eligible_no_q6_rows = 0
    adjusted_misses = 0
    active_misses_before = 0
    active_misses_after = 0
    helped_examples: list[str] = []
    no_q6_increased_examples: list[str] = []
    floors: list[int] = []
    increases: list[int] = []
    no_q6_increased_rows = 0
    no_q6_new_positive_rows = 0

    for row in active_rows:
        baseline_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        floor_value = _q6_count_cell_prior_floor_value(
            row,
            floor_ratio=floor_ratio,
        )
        if floor_value is None:
            continue
        eligible_rows += 1
        floors.append(floor_value)
        candidate_p90 = max(baseline_p90, floor_value)
        increase = candidate_p90 - baseline_p90
        if increase > 0:
            increases.append(increase)
        if int(row.get("final_q6_decision_value") or 0) > 0:
            eligible_q6_truth_rows += 1
        else:
            eligible_no_q6_rows += 1
            if increase > 0:
                no_q6_increased_rows += 1
                if len(no_q6_increased_examples) < 5:
                    no_q6_increased_examples.append(str(row.get("file") or ""))
            if baseline_p90 <= 0 < candidate_p90:
                no_q6_new_positive_rows += 1

    active_q6_truth_ids = {id(row) for row in active_q6_truth}
    for row in q6_truth:
        truth = int(row.get("final_q6_decision_value") or 0)
        baseline_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        candidate_p90 = baseline_p90
        if id(row) in active_q6_truth_ids:
            floor_value = _q6_count_cell_prior_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            if floor_value is not None:
                candidate_p90 = max(candidate_p90, floor_value)
            if baseline_p90 < truth:
                active_misses_before += 1
            if candidate_p90 < truth:
                active_misses_after += 1
            elif baseline_p90 < truth and len(helped_examples) < 5:
                helped_examples.append(str(row.get("file") or ""))
        if candidate_p90 < truth:
            adjusted_misses += 1

    misses_before = sum(
        1 for row in q6_truth
        if int(row.get("v2_q6_decision_value_p90") or 0)
        < int(row.get("final_q6_decision_value") or 0)
    )
    return {
        "enabled": True,
        "gate": gate,
        "floor_ratio": floor_ratio,
        "formal_decision_value_unchanged": True,
        "p50_unchanged": True,
        "q6_plannable_truth_files": len(q6_truth),
        "active_rows": len(active_rows),
        "active_q6_truth_rows": len(active_q6_truth),
        "active_no_q6_rows": len(active_no_q6),
        "eligible_rows": eligible_rows,
        "eligible_q6_truth_rows": eligible_q6_truth_rows,
        "eligible_no_q6_rows": eligible_no_q6_rows,
        "no_q6_p90_increased_rows": no_q6_increased_rows,
        "no_q6_new_positive_rows": no_q6_new_positive_rows,
        "q6_plannable_misses_before": misses_before,
        "q6_plannable_misses_after": adjusted_misses,
        "q6_helped_rows": misses_before - adjusted_misses,
        "active_q6_misses_before": active_misses_before,
        "active_q6_misses_after": active_misses_after,
        "active_q6_helped_rows": active_misses_before - active_misses_after,
        "q6_plannable_coverage_before": (
            round(1.0 - misses_before / len(q6_truth), 4)
            if q6_truth
            else None
        ),
        "q6_plannable_coverage_after": (
            round(1.0 - adjusted_misses / len(q6_truth), 4)
            if q6_truth
            else None
        ),
        "floor_median": _round(statistics.median(floors)) if floors else None,
        "p90_increase_median": (
            _round(statistics.median(increases)) if increases else None
        ),
        "p90_increase_max": max(increases, default=None),
        "helped_examples": helped_examples,
        "no_q6_increased_examples": no_q6_increased_examples,
    }


def _q6_count_cell_prior_narrow_gate_p90_sweep(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = [
        experiment
        for gate, ratios in _Q6_COUNT_CELL_NARROW_P90_SWEEP
        for floor_ratio in ratios
        if (
            experiment := _q6_count_cell_prior_named_gate_p90_experiment(
                rows,
                gate=gate,
                floor_ratio=floor_ratio,
            )
        )
        is not None
    ]
    return {
        "offline_shadow_only": True,
        "formal_decision_value_unchanged": True,
        "p50_unchanged": True,
        "candidates": candidates,
    }


def _aisha_shipwreck_threshold_gate_active(
    row: Mapping[str, Any],
    *,
    bottom_row_threshold: int,
) -> bool:
    key = (
        str(row.get("hero") or "").lower(),
        str(row.get("map_family") or ""),
        str(row.get("evidence_profile_key") or ""),
    )
    bottom_row = _int_or_none(row.get("footprint_bottom_row"))
    return (
        key in AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES
        and bottom_row is not None
        and bottom_row >= bottom_row_threshold
    )


def _q6_count_cell_prior_aisha_threshold_p90_summary(
    rows: list[dict[str, Any]],
    *,
    bottom_row_threshold: int,
    floor_ratio: float,
) -> dict[str, Any]:
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    active_rows = [
        row for row in rows
        if row.get("v2_q6_decision_value_p90") is not None
        and _aisha_shipwreck_threshold_gate_active(
            row,
            bottom_row_threshold=bottom_row_threshold,
        )
    ]
    active_q6_truth = [
        row for row in active_rows
        if int(row.get("final_q6_decision_value") or 0) > 0
    ]
    active_no_q6 = [
        row for row in active_rows
        if int(row.get("final_q6_decision_value") or 0) <= 0
    ]

    eligible_rows = 0
    eligible_q6_truth_rows = 0
    eligible_no_q6_rows = 0
    no_q6_increased_rows = 0
    no_q6_new_positive_rows = 0
    no_q6_increased_examples: list[str] = []
    floors: list[int] = []
    increases: list[int] = []

    for row in active_rows:
        baseline_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        floor_value = _q6_count_cell_prior_floor_value(
            row,
            floor_ratio=floor_ratio,
        )
        if floor_value is None:
            continue
        eligible_rows += 1
        floors.append(floor_value)
        candidate_p90 = max(baseline_p90, floor_value)
        increase = candidate_p90 - baseline_p90
        if increase > 0:
            increases.append(increase)
        if int(row.get("final_q6_decision_value") or 0) > 0:
            eligible_q6_truth_rows += 1
        else:
            eligible_no_q6_rows += 1
            if increase > 0:
                no_q6_increased_rows += 1
                if len(no_q6_increased_examples) < 5:
                    no_q6_increased_examples.append(str(row.get("file") or ""))
            if baseline_p90 <= 0 < candidate_p90:
                no_q6_new_positive_rows += 1

    active_q6_truth_ids = {id(row) for row in active_q6_truth}
    adjusted_misses = 0
    active_misses_before = 0
    active_misses_after = 0
    helped_examples: list[str] = []
    for row in q6_truth:
        truth = int(row.get("final_q6_decision_value") or 0)
        baseline_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        candidate_p90 = baseline_p90
        if id(row) in active_q6_truth_ids:
            floor_value = _q6_count_cell_prior_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            if floor_value is not None:
                candidate_p90 = max(candidate_p90, floor_value)
            if baseline_p90 < truth:
                active_misses_before += 1
            if candidate_p90 < truth:
                active_misses_after += 1
            elif baseline_p90 < truth and len(helped_examples) < 5:
                helped_examples.append(str(row.get("file") or ""))
        if candidate_p90 < truth:
            adjusted_misses += 1

    misses_before = sum(
        1 for row in q6_truth
        if int(row.get("v2_q6_decision_value_p90") or 0)
        < int(row.get("final_q6_decision_value") or 0)
    )
    return {
        "bottom_row_threshold": bottom_row_threshold,
        "floor_ratio": floor_ratio,
        "formal_decision_value_unchanged": True,
        "p50_unchanged": True,
        "q6_plannable_truth_files": len(q6_truth),
        "active_rows": len(active_rows),
        "active_q6_truth_rows": len(active_q6_truth),
        "active_no_q6_rows": len(active_no_q6),
        "eligible_rows": eligible_rows,
        "eligible_q6_truth_rows": eligible_q6_truth_rows,
        "eligible_no_q6_rows": eligible_no_q6_rows,
        "no_q6_p90_increased_rows": no_q6_increased_rows,
        "no_q6_new_positive_rows": no_q6_new_positive_rows,
        "q6_plannable_misses_before": misses_before,
        "q6_plannable_misses_after": adjusted_misses,
        "q6_helped_rows": misses_before - adjusted_misses,
        "active_q6_misses_before": active_misses_before,
        "active_q6_misses_after": active_misses_after,
        "active_q6_helped_rows": active_misses_before - active_misses_after,
        "q6_plannable_coverage_before": (
            round(1.0 - misses_before / len(q6_truth), 4)
            if q6_truth
            else None
        ),
        "q6_plannable_coverage_after": (
            round(1.0 - adjusted_misses / len(q6_truth), 4)
            if q6_truth
            else None
        ),
        "floor_median": _round(statistics.median(floors)) if floors else None,
        "p90_increase_median": (
            _round(statistics.median(increases)) if increases else None
        ),
        "p90_increase_max": max(increases, default=None),
        "net_helped_minus_no_q6_new_positive": (
            misses_before - adjusted_misses - no_q6_new_positive_rows
        ),
        "net_helped_minus_no_q6_p90_increase": (
            misses_before - adjusted_misses - no_q6_increased_rows
        ),
        "helped_examples": helped_examples,
        "no_q6_increased_examples": no_q6_increased_examples,
    }


def _aisha_shipwreck_deep_threshold_p90_sweep(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    thresholds = [
        _q6_count_cell_prior_aisha_threshold_p90_summary(
            rows,
            bottom_row_threshold=threshold,
            floor_ratio=1.0,
        )
        for threshold in _AISHA_SHIPWRECK_DEEP_THRESHOLD_P90_SWEEP
    ]
    return {
        "audit_only": True,
        "formal_decision_value_unchanged": True,
        "p50_unchanged": True,
        "profile_scope": "AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES",
        "current_formal_threshold": AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD,
        "floor_ratio": 1.0,
        "thresholds": thresholds,
        "best_by_net": sorted(
            thresholds,
            key=lambda row: (
                int(row["net_helped_minus_no_q6_p90_increase"]),
                int(row["net_helped_minus_no_q6_new_positive"]),
                int(row["q6_helped_rows"]),
                -int(row["active_no_q6_rows"]),
            ),
            reverse=True,
        )[:3],
    }


def _q6_condition_tags(row: dict[str, Any]) -> tuple[str, ...]:
    tags: list[str] = []
    if float(row.get("v2_q6_count_p90_under_by") or 0) > 0:
        tags.append("q6_count_under_truth")
    if float(row.get("v2_q6_cells_p90_under_by") or 0) > 0:
        tags.append("q6_cells_under_truth")
    if float(row.get("v2_q6_count_p90_under_prior_by") or 0) > 0:
        tags.append("q6_count_below_prior")
    if float(row.get("v2_q6_cells_p90_under_prior_by") or 0) > 0:
        tags.append("q6_cells_below_prior")
    if float(row.get("v2_q6_space_pressure_p90") or 0) < 0.50:
        tags.append("low_space_pressure")
    if (
        row.get("v2_q6_space_pressure_p90") is not None
        and float(row.get("v2_q6_space_pressure_p90") or 0) >= 1.0
    ) or float(row.get("v2_q6_space_overflow_rate") or 0) > 0:
        tags.append("space_overflow_or_constrained")
    random_band = str(row.get("random_sample_avg_signal_band") or "none")
    if random_band == "signal":
        tags.append("random_avg_signal")
    elif random_band == "low_filtered":
        tags.append("random_avg_low_filtered")
    if _q6_count_cell_prior_named_gate_active(
        row,
        gate="aisha_shipwreck_deep_v1",
    ):
        tags.append("aisha_deep_gate_active")
    elif (
        str(row.get("hero") or "").lower() == "aisha"
        and str(row.get("map_family") or "") == "shipwreck"
    ):
        tags.append("aisha_shipwreck_deep_gate_inactive")
    if _q6_count_cell_prior_named_gate_active(
        row,
        gate="ethan_villa_random_avg_v1",
    ):
        tags.append("ethan_villa_random_avg_gate_active")
    avg_cells_band = str(row.get("public_avg_cells_solution_band") or "none")
    if avg_cells_band == "all_unique":
        tags.append("public_avg_cells_unique")
    elif avg_cells_band == "ambiguous":
        tags.append("public_avg_cells_ambiguous")
    if row.get("public_max_quality_used"):
        tags.append("public_max_quality")
    if row.get("public_max_item_cells_used"):
        tags.append("public_max_item_cells")
    if int(row.get("final_q6_trimmed_tail_value") or 0) > 0:
        tags.append("q6_tail_event")
    else:
        tags.append("q6_no_tail_event")
    return tuple(dict.fromkeys(tags))


def _q6_condition_audit_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_misses = [
        row for row in q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in q6_truth:
        for tag in _q6_condition_tags(row):
            by_tag[tag].append(row)

    tags: list[dict[str, Any]] = []
    for tag, tagged_rows in by_tag.items():
        tagged_misses = [
            row for row in tagged_rows
            if row.get("q6_plannable_p90_misses_truth")
        ]
        under_by = [
            int(row["v2_q6_decision_value_p90_under_by"])
            for row in tagged_misses
            if row.get("v2_q6_decision_value_p90_under_by") is not None
        ]
        tags.append(
            {
                "tag": tag,
                "q6_plannable_truth_rows": len(tagged_rows),
                "q6_plannable_miss_rows": len(tagged_misses),
                "q6_plannable_coverage": (
                    round(1.0 - len(tagged_misses) / len(tagged_rows), 4)
                    if tagged_rows
                    else None
                ),
                "median_q6_under_by": (
                    _round(statistics.median(under_by)) if under_by else None
                ),
                "examples": [
                    str(row.get("file") or "")
                    for row in tagged_misses[:5]
                ],
            }
        )

    return {
        "audit_only": True,
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_plannable_miss_rows": len(q6_misses),
        "q6_plannable_coverage": (
            round(1.0 - len(q6_misses) / len(q6_truth), 4)
            if q6_truth
            else None
        ),
        "tags": sorted(
            tags,
            key=lambda row: (
                int(row["q6_plannable_miss_rows"]),
                int(row["median_q6_under_by"] or 0),
                int(row["q6_plannable_truth_rows"]),
            ),
            reverse=True,
        ),
    }


def _q6_bottom_row_band(row: Mapping[str, Any]) -> str:
    bottom_row = _int_or_none(row.get("footprint_bottom_row"))
    if bottom_row is None:
        return "unknown"
    if bottom_row < 9:
        return "bottom_lt9"
    if bottom_row <= 10:
        return "bottom_9_10"
    if bottom_row <= 12:
        return "bottom_11_12"
    if bottom_row <= 14:
        return "bottom_13_14"
    return "bottom_15_plus"


def _aisha_shipwreck_deep_band(row: Mapping[str, Any]) -> str:
    if (
        str(row.get("hero") or "").lower() != "aisha"
        or str(row.get("map_family") or "") != "shipwreck"
    ):
        return "not_aisha_shipwreck"
    bottom_row = _int_or_none(row.get("footprint_bottom_row"))
    if bottom_row is None:
        return "aisha_shipwreck_bottom_unknown"
    if bottom_row >= AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD:
        return "aisha_shipwreck_deep_ge13_formal"
    if bottom_row >= 11:
        return "aisha_shipwreck_deep11_12_shadow"
    if bottom_row >= 9:
        return "aisha_shipwreck_bottom9_10_review"
    return "aisha_shipwreck_shallow_lt9"


def _q6_prior_gap_kind(row: Mapping[str, Any]) -> str:
    count_gap = float(row.get("v2_q6_count_p90_under_prior_by") or 0.0)
    cells_gap = float(row.get("v2_q6_cells_p90_under_prior_by") or 0.0)
    if count_gap > 0 and cells_gap > 0:
        return "count_and_cells_below_prior"
    if count_gap > 0:
        return "count_below_prior"
    if cells_gap > 0:
        return "cells_below_prior"
    return "not_below_prior"


def _q6_truth_gap_kind(row: Mapping[str, Any]) -> str:
    count_gap = float(row.get("v2_q6_count_p90_under_by") or 0.0)
    cells_gap = float(row.get("v2_q6_cells_p90_under_by") or 0.0)
    if count_gap > 0 and cells_gap > 0:
        return "count_and_cells_under_truth"
    if count_gap > 0:
        return "count_under_truth"
    if cells_gap > 0:
        return "cells_under_truth"
    return "not_under_truth_count_cells"


def _q6_cell_gap_feature_value(row: Mapping[str, Any], key: str) -> str:
    if key == "q6_bottom_row_band":
        return _q6_bottom_row_band(row)
    if key == "aisha_shipwreck_deep_band":
        return _aisha_shipwreck_deep_band(row)
    if key == "q6_prior_gap_kind":
        return _q6_prior_gap_kind(row)
    if key == "q6_truth_gap_kind":
        return _q6_truth_gap_kind(row)
    return str(row.get(key) or "unknown")


def _q6_cell_gap_group_key(row: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    return "|".join(
        f"{key}={_q6_cell_gap_feature_value(row, key)}"
        for key in keys
    )


def _numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        values.append(float(value))
    return values


def _median_numeric(
    rows: list[dict[str, Any]],
    key: str,
    *,
    digits: int | None = None,
) -> int | float | None:
    values = _numeric_values(rows, key)
    if not values:
        return None
    value = statistics.median(values)
    return _round_float(value, digits) if digits is not None else _round(value)


def _quantile_numeric(
    rows: list[dict[str, Any]],
    key: str,
    quantile: float,
    *,
    digits: int | None = None,
) -> int | float | None:
    values = sorted(_numeric_values(rows, key))
    if not values:
        return None
    if len(values) == 1:
        value = values[0]
    else:
        position = (len(values) - 1) * quantile
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        fraction = position - lower
        value = values[lower] * (1.0 - fraction) + values[upper] * fraction
    return _round_float(value, digits) if digits is not None else _round(value)


def _q6_cell_gap_examples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            int(row.get("v2_q6_decision_value_p90_under_by") or 0),
            int(row.get("v2_q6_cells_p90_under_by") or 0),
            int(row.get("v2_q6_count_p90_under_by") or 0),
        ),
        reverse=True,
    )
    return [
        {
            "file": row.get("file"),
            "hero": row.get("hero"),
            "map_family": row.get("map_family"),
            "capture_round": row.get("capture_round"),
            "evidence_profile_key": row.get("evidence_profile_key"),
            "bottom_row": row.get("footprint_bottom_row"),
            "q6_truth_count": row.get("final_q6_count"),
            "q6_truth_cells": row.get("final_q6_cells"),
            "q6_p90_count": row.get("v2_q6_count_p90"),
            "q6_p90_cells": row.get("v2_q6_cells_p90"),
            "q6_count_under_by": row.get("v2_q6_count_p90_under_by"),
            "q6_cells_under_by": row.get("v2_q6_cells_p90_under_by"),
            "q6_value_under_by": row.get("v2_q6_decision_value_p90_under_by"),
        }
        for row in ordered[:5]
    ]


def _q6_cell_gap_group_summary(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status", "ok") != "ok":
            continue
        groups[_q6_cell_gap_group_key(row, keys)].append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        q6_truth = [
            row for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) > 0
            and row.get("v2_q6_decision_value_p90") is not None
        ]
        if not q6_truth:
            continue
        q6_misses = [
            row for row in q6_truth
            if row.get("q6_plannable_p90_misses_truth")
        ]
        no_q6_controls = [
            row for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) <= 0
            and row.get("v2_q6_decision_value_p90") is not None
        ]
        no_q6_positive = [
            row for row in no_q6_controls
            if int(row.get("v2_q6_decision_value_p90") or 0) > 0
        ]
        q6_count_under = [
            row for row in q6_misses
            if float(row.get("v2_q6_count_p90_under_by") or 0.0) > 0
        ]
        q6_cells_under = [
            row for row in q6_misses
            if float(row.get("v2_q6_cells_p90_under_by") or 0.0) > 0
        ]
        q6_count_prior_gap = [
            row for row in q6_misses
            if float(row.get("v2_q6_count_p90_under_prior_by") or 0.0) > 0
        ]
        q6_cells_prior_gap = [
            row for row in q6_misses
            if float(row.get("v2_q6_cells_p90_under_prior_by") or 0.0) > 0
        ]
        out.append(
            {
                "group": group,
                "n": len(group_rows),
                "q6_plannable_truth_rows": len(q6_truth),
                "q6_plannable_miss_rows": len(q6_misses),
                "q6_plannable_coverage": (
                    round(1.0 - len(q6_misses) / len(q6_truth), 4)
                    if q6_truth
                    else None
                ),
                "q6_count_under_rows": len(q6_count_under),
                "q6_cells_under_rows": len(q6_cells_under),
                "q6_count_and_cells_under_rows": sum(
                    1 for row in q6_misses
                    if float(row.get("v2_q6_count_p90_under_by") or 0.0) > 0
                    and float(row.get("v2_q6_cells_p90_under_by") or 0.0) > 0
                ),
                "q6_count_below_prior_rows": len(q6_count_prior_gap),
                "q6_cells_below_prior_rows": len(q6_cells_prior_gap),
                "median_q6_under_by": _median_numeric(
                    q6_misses,
                    "v2_q6_decision_value_p90_under_by",
                ),
                "median_count_under_by": _median_numeric(
                    q6_count_under,
                    "v2_q6_count_p90_under_by",
                    digits=2,
                ),
                "median_cells_under_by": _median_numeric(
                    q6_cells_under,
                    "v2_q6_cells_p90_under_by",
                    digits=1,
                ),
                "median_prior_count_gap": _median_numeric(
                    q6_count_prior_gap,
                    "v2_q6_count_p90_under_prior_by",
                    digits=2,
                ),
                "median_prior_cells_gap": _median_numeric(
                    q6_cells_prior_gap,
                    "v2_q6_cells_p90_under_prior_by",
                    digits=1,
                ),
                "median_truth_count": _median_numeric(q6_truth, "final_q6_count"),
                "median_truth_cells": _median_numeric(q6_truth, "final_q6_cells"),
                "median_p90_count": _median_numeric(
                    q6_truth,
                    "v2_q6_count_p90",
                    digits=2,
                ),
                "median_p90_cells": _median_numeric(
                    q6_truth,
                    "v2_q6_cells_p90",
                    digits=1,
                ),
                "median_prior_expected_count": _median_numeric(
                    q6_truth,
                    "v2_q6_prior_expected_count",
                    digits=2,
                ),
                "median_prior_expected_cells": _median_numeric(
                    q6_truth,
                    "v2_q6_prior_expected_cells",
                    digits=1,
                ),
                "no_q6_control_rows": len(no_q6_controls),
                "no_q6_p90_positive_rows": len(no_q6_positive),
                "no_q6_p90_positive_rate": (
                    round(len(no_q6_positive) / len(no_q6_controls), 4)
                    if no_q6_controls
                    else None
                ),
                "no_q6_p90_positive_median": _median_numeric(
                    no_q6_positive,
                    "v2_q6_decision_value_p90",
                ),
                "active_prior_floor_rows": sum(
                    1 for row in group_rows
                    if float(row.get("q6_residual_prior_floor_ratio") or 0.0) > 0
                ),
                "active_prior_cell_floor_rows": sum(
                    1 for row in group_rows
                    if float(row.get("q6_residual_prior_cell_floor_ratio") or 0.0)
                    > 0
                ),
                "net_miss_minus_no_q6_positive": (
                    len(q6_misses) - len(no_q6_positive)
                ),
                "examples": _q6_cell_gap_examples(q6_misses),
            }
        )
    return sorted(
        out,
        key=lambda row: (
            int(row["q6_plannable_miss_rows"]),
            int(row["q6_count_and_cells_under_rows"]),
            int(row["net_miss_minus_no_q6_positive"]),
            int(row["median_q6_under_by"] or 0),
            -int(row["no_q6_p90_positive_rows"]),
        ),
        reverse=True,
    )[:limit]


def _q6_cell_gap_by_feature_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ok = [row for row in rows if row.get("status", "ok") == "ok"]
    q6_truth = [
        row for row in ok
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_misses = [
        row for row in q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    no_q6_controls = [
        row for row in ok
        if int(row.get("final_q6_decision_value") or 0) <= 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    no_q6_positive = [
        row for row in no_q6_controls
        if int(row.get("v2_q6_decision_value_p90") or 0) > 0
    ]
    return {
        "audit_only": True,
        "formal_decision_value_unchanged": True,
        "sampler_design_note": (
            "Use these groups to choose conditional q6 count/cell/value "
            "features before adding any live or formal sampler change."
        ),
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_plannable_miss_rows": len(q6_misses),
        "q6_count_under_miss_rows": sum(
            1 for row in q6_misses
            if float(row.get("v2_q6_count_p90_under_by") or 0.0) > 0
        ),
        "q6_cells_under_miss_rows": sum(
            1 for row in q6_misses
            if float(row.get("v2_q6_cells_p90_under_by") or 0.0) > 0
        ),
        "q6_count_and_cells_under_miss_rows": sum(
            1 for row in q6_misses
            if float(row.get("v2_q6_count_p90_under_by") or 0.0) > 0
            and float(row.get("v2_q6_cells_p90_under_by") or 0.0) > 0
        ),
        "q6_count_below_prior_miss_rows": sum(
            1 for row in q6_misses
            if float(row.get("v2_q6_count_p90_under_prior_by") or 0.0) > 0
        ),
        "q6_cells_below_prior_miss_rows": sum(
            1 for row in q6_misses
            if float(row.get("v2_q6_cells_p90_under_prior_by") or 0.0) > 0
        ),
        "no_q6_control_rows": len(no_q6_controls),
        "no_q6_p90_positive_rows": len(no_q6_positive),
        "no_q6_p90_positive_rate": (
            round(len(no_q6_positive) / len(no_q6_controls), 4)
            if no_q6_controls
            else None
        ),
        "feature_groups": {
            "hero_map_profile": _q6_cell_gap_group_summary(
                ok,
                ("hero", "map_family", "evidence_profile_key"),
            ),
            "hero_map_bottom_row": _q6_cell_gap_group_summary(
                ok,
                ("hero", "map_family", "q6_bottom_row_band"),
            ),
            "aisha_shipwreck_deep_band": _q6_cell_gap_group_summary(
                [
                    row for row in ok
                    if str(row.get("hero") or "").lower() == "aisha"
                    and str(row.get("map_family") or "") == "shipwreck"
                ],
                ("aisha_shipwreck_deep_band", "evidence_profile_key"),
            ),
            "hero_map_random_avg": _q6_cell_gap_group_summary(
                ok,
                ("hero", "map_family", "random_sample_avg_signal_band"),
            ),
            "hero_map_stage_density": _q6_cell_gap_group_summary(
                ok,
                (
                    "hero",
                    "map_family",
                    "evidence_stage",
                    "information_density_band",
                ),
            ),
            "hero_map_top_size": _q6_cell_gap_group_summary(
                ok,
                ("hero", "map_family", "q6_top_size_band"),
            ),
            "hero_map_prior_gap": _q6_cell_gap_group_summary(
                ok,
                ("hero", "map_family", "q6_prior_gap_kind"),
            ),
            "hero_map_truth_gap": _q6_cell_gap_group_summary(
                ok,
                ("hero", "map_family", "q6_truth_gap_kind"),
            ),
            "hero_map_public_avg_cells": _q6_cell_gap_group_summary(
                ok,
                ("hero", "map_family", "public_avg_cells_solution_band"),
            ),
        },
    }


def _q6_conditional_target_row_scope(
    rows: list[dict[str, Any]],
    scope: str,
) -> list[dict[str, Any]]:
    if scope == "all":
        return rows
    if scope == "aisha_shipwreck":
        return [
            row for row in rows
            if str(row.get("hero") or "").lower() == "aisha"
            and str(row.get("map_family") or "") == "shipwreck"
        ]
    if scope == "ethan":
        return [
            row for row in rows
            if str(row.get("hero") or "").lower() == "ethan"
        ]
    return rows


def _q6_conditional_target_group_rows(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status", "ok") != "ok":
            continue
        groups[_q6_cell_gap_group_key(row, keys)].append(row)
    return groups


def _q6_conditional_target_value(
    *,
    median_value: int | float | None,
    quantile_value: int | float | None,
    n: int,
    shrinkage_k: float,
    digits: int | None = None,
) -> int | float | None:
    if median_value is None or quantile_value is None:
        return None
    weight = n / (n + shrinkage_k) if shrinkage_k > 0 else 1.0
    value = float(median_value) + weight * (
        float(quantile_value) - float(median_value)
    )
    return _round_float(value, digits) if digits is not None else _round(value)


def _q6_conditional_target_for_group(
    group: str,
    group_rows: list[dict[str, Any]],
    *,
    target_quantile: float,
    shrinkage_k: float,
    min_q6_truth: int,
    min_miss_rows: int,
) -> dict[str, Any] | None:
    q6_truth = [
        row for row in group_rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    if len(q6_truth) < min_q6_truth:
        return None
    q6_misses = [
        row for row in q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    if len(q6_misses) < min_miss_rows:
        return None
    count_cells_under = [
        row for row in q6_misses
        if float(row.get("v2_q6_count_p90_under_by") or 0.0) > 0
        and float(row.get("v2_q6_cells_p90_under_by") or 0.0) > 0
    ]
    if not count_cells_under:
        return None
    no_q6_controls = [
        row for row in group_rows
        if int(row.get("final_q6_decision_value") or 0) <= 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    no_q6_positive = [
        row for row in no_q6_controls
        if int(row.get("v2_q6_decision_value_p90") or 0) > 0
    ]
    if len(q6_misses) <= len(no_q6_positive):
        return None

    target_count = _q6_conditional_target_value(
        median_value=_median_numeric(q6_truth, "final_q6_count", digits=2),
        quantile_value=_quantile_numeric(
            q6_truth,
            "final_q6_count",
            target_quantile,
            digits=2,
        ),
        n=len(q6_truth),
        shrinkage_k=shrinkage_k,
        digits=2,
    )
    target_cells = _q6_conditional_target_value(
        median_value=_median_numeric(q6_truth, "final_q6_cells", digits=1),
        quantile_value=_quantile_numeric(
            q6_truth,
            "final_q6_cells",
            target_quantile,
            digits=1,
        ),
        n=len(q6_truth),
        shrinkage_k=shrinkage_k,
        digits=1,
    )
    target_value = _q6_conditional_target_value(
        median_value=_median_numeric(q6_truth, "final_q6_decision_value"),
        quantile_value=_quantile_numeric(
            q6_truth,
            "final_q6_decision_value",
            target_quantile,
        ),
        n=len(q6_truth),
        shrinkage_k=shrinkage_k,
    )
    if target_count is None or target_cells is None or target_value is None:
        return None
    return {
        "group": group,
        "n": len(group_rows),
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_plannable_miss_rows": len(q6_misses),
        "q6_count_and_cells_under_rows": len(count_cells_under),
        "no_q6_control_rows": len(no_q6_controls),
        "no_q6_p90_positive_rows": len(no_q6_positive),
        "target_count": target_count,
        "target_cells": target_cells,
        "target_value": target_value,
        "truth_count_median": _median_numeric(q6_truth, "final_q6_count", digits=2),
        "truth_cells_median": _median_numeric(q6_truth, "final_q6_cells", digits=1),
        "truth_value_median": _median_numeric(q6_truth, "final_q6_decision_value"),
        "truth_count_q": _quantile_numeric(
            q6_truth,
            "final_q6_count",
            target_quantile,
            digits=2,
        ),
        "truth_cells_q": _quantile_numeric(
            q6_truth,
            "final_q6_cells",
            target_quantile,
            digits=1,
        ),
        "truth_value_q": _quantile_numeric(
            q6_truth,
            "final_q6_decision_value",
            target_quantile,
        ),
        "median_q6_under_by": _median_numeric(
            q6_misses,
            "v2_q6_decision_value_p90_under_by",
        ),
        "median_count_under_by": _median_numeric(
            count_cells_under,
            "v2_q6_count_p90_under_by",
            digits=2,
        ),
        "median_cells_under_by": _median_numeric(
            count_cells_under,
            "v2_q6_cells_p90_under_by",
            digits=1,
        ),
    }


def _q6_conditional_target_candidate_p90(
    row: Mapping[str, Any],
    target: Mapping[str, Any],
) -> int | None:
    base_p90 = _int_or_none(row.get("v2_q6_decision_value_p90"))
    if base_p90 is None:
        return None
    if row.get("public_max_quality_used"):
        return None
    target_count = float(target.get("target_count") or 0.0)
    target_cells = float(target.get("target_cells") or 0.0)
    target_value = _int_or_none(target.get("target_value"))
    if target_count <= 0 or target_cells <= 0 or target_value is None:
        return None
    q6_count_p90 = float(row.get("v2_q6_count_p90") or 0.0)
    q6_cells_p90 = float(row.get("v2_q6_cells_p90") or 0.0)
    if q6_count_p90 >= target_count and q6_cells_p90 >= target_cells:
        return None
    return max(base_p90, target_value)


def _q6_conditional_target_examples(
    rows: list[dict[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    keys: tuple[str, ...],
    *,
    helped: bool,
) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for row in rows:
        group = _q6_cell_gap_group_key(row, keys)
        target = targets.get(group)
        if target is None:
            continue
        candidate_p90 = _q6_conditional_target_candidate_p90(row, target)
        if candidate_p90 is None:
            continue
        base_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        final_q6 = int(row.get("final_q6_decision_value") or 0)
        is_helped = final_q6 > 0 and base_p90 < final_q6 <= candidate_p90
        is_no_q6_increased = final_q6 <= 0 and candidate_p90 > base_p90
        if helped and not is_helped:
            continue
        if not helped and not is_no_q6_increased:
            continue
        examples.append(
            {
                "file": row.get("file"),
                "group": group,
                "hero": row.get("hero"),
                "map_family": row.get("map_family"),
                "capture_round": row.get("capture_round"),
                "evidence_profile_key": row.get("evidence_profile_key"),
                "bottom_row": row.get("footprint_bottom_row"),
                "base_q6_p90": base_p90,
                "candidate_q6_p90": candidate_p90,
                "target_count": target.get("target_count"),
                "target_cells": target.get("target_cells"),
                "target_value": target.get("target_value"),
                "truth_q6_value": row.get("final_q6_decision_value"),
                "truth_q6_count": row.get("final_q6_count"),
                "truth_q6_cells": row.get("final_q6_cells"),
            }
        )
    return sorted(
        examples,
        key=lambda row: int(row["candidate_q6_p90"] or 0)
        - int(row["base_q6_p90"] or 0),
        reverse=True,
    )[:5]


def _q6_conditional_target_group_effect(
    group_rows: list[dict[str, Any]],
    target: Mapping[str, Any],
) -> dict[str, Any]:
    q6_truth = [
        row for row in group_rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_misses_before = 0
    q6_misses_after = 0
    helped = 0
    active_truth = 0
    deltas: list[int] = []
    for row in q6_truth:
        base_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        final_q6 = int(row.get("final_q6_decision_value") or 0)
        candidate_p90 = _q6_conditional_target_candidate_p90(row, target)
        if base_p90 < final_q6:
            q6_misses_before += 1
        if candidate_p90 is None:
            candidate_p90 = base_p90
        else:
            active_truth += 1
            if candidate_p90 > base_p90:
                deltas.append(candidate_p90 - base_p90)
        if candidate_p90 < final_q6:
            q6_misses_after += 1
        elif base_p90 < final_q6:
            helped += 1

    no_q6_controls = [
        row for row in group_rows
        if int(row.get("final_q6_decision_value") or 0) <= 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    active_no_q6 = 0
    no_q6_increased = 0
    no_q6_new_positive = 0
    no_q6_deltas: list[int] = []
    for row in no_q6_controls:
        base_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        candidate_p90 = _q6_conditional_target_candidate_p90(row, target)
        if candidate_p90 is None:
            continue
        active_no_q6 += 1
        if candidate_p90 > base_p90:
            no_q6_increased += 1
            no_q6_deltas.append(candidate_p90 - base_p90)
            if base_p90 <= 0:
                no_q6_new_positive += 1

    return {
        "active_truth_rows": active_truth,
        "q6_misses_before": q6_misses_before,
        "q6_misses_after": q6_misses_after,
        "q6_helped_rows": helped,
        "active_no_q6_rows": active_no_q6,
        "no_q6_increased_rows": no_q6_increased,
        "no_q6_new_positive_rows": no_q6_new_positive,
        "median_q6_delta": _round(statistics.median(deltas)) if deltas else None,
        "median_no_q6_delta": (
            _round(statistics.median(no_q6_deltas)) if no_q6_deltas else None
        ),
        "net_helped_minus_no_q6_increase": helped - no_q6_increased,
    }


def _q6_conditional_target_experiment(
    rows: list[dict[str, Any]],
    *,
    name: str,
    keys: tuple[str, ...],
    scope: str = "all",
    target_quantile: float = 0.75,
    shrinkage_k: float = 8.0,
    min_q6_truth: int = 8,
    min_miss_rows: int = 2,
) -> dict[str, Any]:
    scoped_rows = _q6_conditional_target_row_scope(
        [row for row in rows if row.get("status", "ok") == "ok"],
        scope,
    )
    groups = _q6_conditional_target_group_rows(scoped_rows, keys)
    targets: dict[str, dict[str, Any]] = {}
    group_rows_by_key: dict[str, list[dict[str, Any]]] = {}
    for group, group_rows in groups.items():
        target = _q6_conditional_target_for_group(
            group,
            group_rows,
            target_quantile=target_quantile,
            shrinkage_k=shrinkage_k,
            min_q6_truth=min_q6_truth,
            min_miss_rows=min_miss_rows,
        )
        if target is None:
            continue
        targets[group] = target
        group_rows_by_key[group] = group_rows

    q6_truth = [
        row for row in scoped_rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_misses_before = 0
    q6_misses_after = 0
    active_truth = 0
    helped = 0
    q6_deltas: list[int] = []
    for row in q6_truth:
        base_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        final_q6 = int(row.get("final_q6_decision_value") or 0)
        if base_p90 < final_q6:
            q6_misses_before += 1
        target = targets.get(_q6_cell_gap_group_key(row, keys))
        candidate_p90 = (
            _q6_conditional_target_candidate_p90(row, target)
            if target is not None
            else None
        )
        if candidate_p90 is None:
            candidate_p90 = base_p90
        else:
            active_truth += 1
            if candidate_p90 > base_p90:
                q6_deltas.append(candidate_p90 - base_p90)
        if candidate_p90 < final_q6:
            q6_misses_after += 1
        elif base_p90 < final_q6:
            helped += 1

    no_q6_controls = [
        row for row in scoped_rows
        if int(row.get("final_q6_decision_value") or 0) <= 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    active_no_q6 = 0
    no_q6_increased = 0
    no_q6_new_positive = 0
    no_q6_deltas: list[int] = []
    for row in no_q6_controls:
        target = targets.get(_q6_cell_gap_group_key(row, keys))
        if target is None:
            continue
        base_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        candidate_p90 = _q6_conditional_target_candidate_p90(row, target)
        if candidate_p90 is None:
            continue
        active_no_q6 += 1
        if candidate_p90 > base_p90:
            no_q6_increased += 1
            no_q6_deltas.append(candidate_p90 - base_p90)
            if base_p90 <= 0:
                no_q6_new_positive += 1

    target_rows = []
    for group, target in targets.items():
        effect = _q6_conditional_target_group_effect(
            group_rows_by_key[group],
            target,
        )
        target_rows.append({**target, **effect})

    target_rows = sorted(
        target_rows,
        key=lambda row: (
            int(row["net_helped_minus_no_q6_increase"]),
            int(row["q6_helped_rows"]),
            -int(row["no_q6_increased_rows"]),
            int(row["q6_plannable_truth_rows"]),
        ),
        reverse=True,
    )
    return {
        "name": name,
        "scope": scope,
        "keys": list(keys),
        "in_sample_upper_bound": True,
        "target_quantile": target_quantile,
        "shrinkage_k": shrinkage_k,
        "min_q6_truth": min_q6_truth,
        "min_miss_rows": min_miss_rows,
        "candidate_groups": len(targets),
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_misses_before": q6_misses_before,
        "q6_misses_after": q6_misses_after,
        "q6_helped_rows": helped,
        "active_truth_rows": active_truth,
        "no_q6_control_rows": len(no_q6_controls),
        "active_no_q6_rows": active_no_q6,
        "no_q6_increased_rows": no_q6_increased,
        "no_q6_new_positive_rows": no_q6_new_positive,
        "median_q6_delta": (
            _round(statistics.median(q6_deltas)) if q6_deltas else None
        ),
        "median_no_q6_delta": (
            _round(statistics.median(no_q6_deltas)) if no_q6_deltas else None
        ),
        "net_helped_minus_no_q6_increase": helped - no_q6_increased,
        "groups": target_rows[:12],
        "helped_examples": _q6_conditional_target_examples(
            q6_truth,
            targets,
            keys,
            helped=True,
        ),
        "no_q6_increased_examples": _q6_conditional_target_examples(
            no_q6_controls,
            targets,
            keys,
            helped=False,
        ),
    }


def _q6_conditional_target_experiment_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    configs = [
        _q6_conditional_target_experiment(
            rows,
            name="hero_map_profile",
            keys=("hero", "map_family", "evidence_profile_key"),
            min_q6_truth=10,
        ),
        _q6_conditional_target_experiment(
            rows,
            name="hero_map_profile_prior_gap",
            keys=(
                "hero",
                "map_family",
                "evidence_profile_key",
                "q6_prior_gap_kind",
            ),
            min_q6_truth=6,
        ),
        _q6_conditional_target_experiment(
            rows,
            name="hero_map_bottom_profile",
            keys=("hero", "map_family", "q6_bottom_row_band", "evidence_profile_key"),
            min_q6_truth=6,
        ),
        _q6_conditional_target_experiment(
            rows,
            name="aisha_shipwreck_deep_profile",
            keys=("aisha_shipwreck_deep_band", "evidence_profile_key"),
            scope="aisha_shipwreck",
            min_q6_truth=6,
        ),
        _q6_conditional_target_experiment(
            rows,
            name="ethan_random_profile",
            keys=(
                "hero",
                "map_family",
                "random_sample_avg_signal_band",
                "evidence_profile_key",
            ),
            scope="ethan",
            min_q6_truth=4,
        ),
    ]
    return {
        "audit_only": True,
        "formal_decision_value_unchanged": True,
        "in_sample_upper_bound": True,
        "sampler_design_note": (
            "This estimates conditional q6 count/cells/value targets from "
            "current sample groups. Treat it as an upper-bound selector for "
            "future sampler features, not as deployable accuracy."
        ),
        "configs": configs,
        "best_by_net": sorted(
            configs,
            key=lambda row: (
                int(row["net_helped_minus_no_q6_increase"]),
                int(row["q6_helped_rows"]),
                -int(row["no_q6_increased_rows"]),
            ),
            reverse=True,
        )[:3],
    }


def _gate_activity_summary(
    rows: list[dict[str, Any]],
    *,
    gate: str,
) -> dict[str, Any]:
    active = [
        row for row in rows
        if _q6_count_cell_prior_named_gate_active(row, gate=gate)
    ]
    active_q6_truth = [
        row for row in active
        if int(row.get("final_q6_decision_value") or 0) > 0
    ]
    active_no_q6 = [
        row for row in active
        if int(row.get("final_q6_decision_value") or 0) <= 0
    ]
    active_misses = [
        row for row in active_q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    return {
        "active_rows": len(active),
        "active_q6_truth_rows": len(active_q6_truth),
        "active_no_q6_rows": len(active_no_q6),
        "active_q6_miss_rows": len(active_misses),
        "active_q6_coverage": (
            round(1.0 - len(active_misses) / len(active_q6_truth), 4)
            if active_q6_truth
            else None
        ),
    }


def _evidence_parameter_audit_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    random_bands = Counter(
        str(row.get("random_sample_avg_signal_band") or "none")
        for row in rows
    )
    random_floor_values = sorted(
        {
            float(row.get("random_sample_avg_profile_floor"))
            for row in rows
            if row.get("random_sample_avg_profile_floor") is not None
        }
    )
    public_info = _public_info_semantics_summary(rows)
    avg_cells = _public_avg_cells_uniqueness_summary(rows)
    aisha_bottom = _aisha_bottom_row_risk_summary(rows)
    return {
        "audit_only": True,
        "random_sample_avg": {
            "profile_signal_floor": RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR,
            "observed_profile_floor_values": random_floor_values,
            "compiled_value_floor_signal_floor": (
                RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR
            ),
            "floor_scope_note": (
                "random_sample_avg_profile_floor controls evidence-profile "
                "routing in this evaluator; posterior random-sample value "
                "flooring still uses the compiled signal floor unless v2 "
                "_random_sample_value_floor is changed."
            ),
            "value_floor_factor": RANDOM_SAMPLE_VALUE_FLOOR_FACTOR,
            "hard_floor_min_matched": RANDOM_SAMPLE_HARD_FLOOR_MIN_MATCHED,
            "hard_floor_min_rate": RANDOM_SAMPLE_HARD_FLOOR_MIN_RATE,
            "hard_floor_max_min_matched": RANDOM_SAMPLE_HARD_FLOOR_MAX_MIN_MATCHED,
            "hard_floor_extra_trials": RANDOM_SAMPLE_HARD_FLOOR_EXTRA_TRIALS,
            "soft_penalty": RANDOM_SAMPLE_VALUE_FLOOR_SOFT_PENALTY,
            "signal_band_counts": dict(sorted(random_bands.items())),
            "signal_case": _case_metric_summary(
                [
                    row for row in rows
                    if str(row.get("random_sample_avg_signal_band") or "none")
                    == "signal"
                ]
            ),
            "low_filtered_case": _case_metric_summary(
                [
                    row for row in rows
                    if str(row.get("random_sample_avg_signal_band") or "none")
                    == "low_filtered"
                ]
            ),
        },
        "public_info": {
            "modeled_hard_ids": public_info.get("modeled_hard_ids"),
            "modeled_soft_ids": public_info.get("modeled_soft_ids"),
            "diagnostic_ids": public_info.get("diagnostic_ids"),
            "partial_ids": public_info.get("partial_ids"),
            "pending_model_ids": public_info.get("pending_model_ids"),
            "unknown_ids": public_info.get("unknown_ids"),
            "constraint_events": public_info.get("constraint_events"),
        },
        "public_avg_cells": {
            "unique_rows": avg_cells.get("unique_rows"),
            "ambiguous_rows": avg_cells.get("ambiguous_rows"),
            "no_candidate_rows": avg_cells.get("no_candidate_rows"),
            "band_counts": avg_cells.get("band_counts"),
            "status_counts": avg_cells.get("status_counts"),
        },
        "aisha_q6_position": {
            "bottom_row_risk_threshold": AISHA_BOTTOM_ROW_RISK_THRESHOLD,
            "shipwreck_deep_row_threshold": AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD,
            "quality_only_deep_row_threshold": (
                AISHA_Q6_QUALITY_ONLY_DEEP_ROW_THRESHOLD
            ),
            "bottom_row_risk": aisha_bottom,
        },
        "q6_gate_activity": {
            "aisha_shipwreck_deep_v1": _gate_activity_summary(
                rows,
                gate="aisha_shipwreck_deep_v1",
            ),
            "ethan_villa_random_avg_v1": _gate_activity_summary(
                rows,
                gate="ethan_villa_random_avg_v1",
            ),
        },
        "change_requires_confirmation": [
            {
                "parameter": "RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR",
                "reason": (
                    "Changes evidence_profile routing and can move rows into "
                    "random_avg q6 gates."
                ),
            },
            {
                "parameter": "RANDOM_SAMPLE_VALUE_FLOOR_*",
                "reason": (
                    "Changes public random sample avg hard/soft filtering and "
                    "can affect both P50 center and P90 tail."
                ),
            },
            {
                "parameter": "PUBLIC_AVG_VALUE / PUBLIC_AVG_CELLS handling",
                "reason": (
                    "Avg evidence is usually not a unique inventory solver; "
                    "hardening it needs separate uniqueness/control proof."
                ),
            },
            {
                "parameter": "AISHA_*_ROW_THRESHOLD",
                "reason": (
                    "Moves Aisha q6 gates and quality-only review risk; "
                    "must be checked against no-q6 controls."
                ),
            },
            {
                "parameter": (
                    "q6_residual_prior_floor_ratio / "
                    "q6_residual_prior_cell_floor_ratio / value_power / "
                    "q6_conditional_target_*"
                ),
                "reason": (
                    "Can improve P90 coverage while degrading P50 MAE; "
                    "must pass paired accuracy and no-q6 checks."
                ),
            },
        ],
    }


def _aisha_shipwreck_tail_value_active(
    row: Mapping[str, Any],
    *,
    bottom_row_threshold: int,
) -> bool:
    bottom_row = _int_or_none(row.get("footprint_bottom_row"))
    return (
        str(row.get("hero") or "").lower() == "aisha"
        and str(row.get("map_family") or "") == "shipwreck"
        and str(row.get("evidence_profile_key") or "")
        in _AISHA_SHIPWRECK_TAIL_VALUE_PROFILES
        and bottom_row is not None
        and bottom_row >= int(bottom_row_threshold)
        and row.get("v2_q6_decision_value_p90") is not None
        and row.get("v2_q6_tail_replacement_decision_value_p90") is not None
    )


def _aisha_shipwreck_tail_value_candidate_p90(row: Mapping[str, Any]) -> int | None:
    base = _int_or_none(row.get("v2_q6_decision_value_p90"))
    replacement = _int_or_none(row.get("v2_q6_tail_replacement_decision_value_p90"))
    if base is None or replacement is None:
        return None
    return max(base, replacement)


def _q6_tail_value_sampler_threshold_summary(
    rows: list[dict[str, Any]],
    *,
    bottom_row_threshold: int,
) -> dict[str, Any]:
    q6_truth = [
        row
        for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    no_q6_controls = [
        row
        for row in rows
        if int(row.get("final_q6_decision_value") or 0) <= 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    active_truth = [
        row
        for row in q6_truth
        if _aisha_shipwreck_tail_value_active(
            row,
            bottom_row_threshold=bottom_row_threshold,
        )
    ]
    active_no_q6 = [
        row
        for row in no_q6_controls
        if _aisha_shipwreck_tail_value_active(
            row,
            bottom_row_threshold=bottom_row_threshold,
        )
    ]

    helped = 0
    still_missed = 0
    newly_missed = 0
    before_misses = 0
    after_misses = 0
    q6_deltas: list[int] = []
    tail_truth_rows = 0
    tail_truth_helped = 0
    tail_truth_after_misses = 0
    examples: list[dict[str, Any]] = []
    for row in q6_truth:
        final_q6 = int(row.get("final_q6_decision_value") or 0)
        base_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        active = row in active_truth
        candidate_p90 = (
            _aisha_shipwreck_tail_value_candidate_p90(row) if active else base_p90
        )
        if candidate_p90 is None:
            candidate_p90 = base_p90
        base_missed = base_p90 < final_q6
        candidate_missed = candidate_p90 < final_q6
        if base_missed:
            before_misses += 1
        if candidate_missed:
            after_misses += 1
        if active:
            q6_deltas.append(candidate_p90 - base_p90)
            if base_missed and not candidate_missed:
                helped += 1
            if base_missed and candidate_missed:
                still_missed += 1
            if not base_missed and candidate_missed:
                newly_missed += 1
            if len(examples) < 8:
                examples.append(
                    {
                        "file": row.get("file"),
                        "final_q6_decision_value": final_q6,
                        "base_q6_p90": base_p90,
                        "candidate_q6_p90": candidate_p90,
                        "bottom_row": row.get("footprint_bottom_row"),
                        "evidence_profile_key": row.get("evidence_profile_key"),
                    }
                )

        tail_truth = int(
            row.get("final_q6_decision_value_with_tail_replacement") or final_q6
        )
        if active and tail_truth > final_q6:
            tail_truth_rows += 1
            if base_p90 < tail_truth <= candidate_p90:
                tail_truth_helped += 1
            if candidate_p90 < tail_truth:
                tail_truth_after_misses += 1

    no_q6_deltas: list[int] = []
    no_q6_new_positive = 0
    no_q6_positive = 0
    for row in active_no_q6:
        base_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
        candidate_p90 = _aisha_shipwreck_tail_value_candidate_p90(row)
        if candidate_p90 is None:
            continue
        delta = candidate_p90 - base_p90
        no_q6_deltas.append(delta)
        if base_p90 <= 0 < candidate_p90:
            no_q6_new_positive += 1
        if candidate_p90 > 0:
            no_q6_positive += 1

    return {
        "bottom_row_threshold": bottom_row_threshold,
        "gate": "aisha_shipwreck_shape_layout_tail_replacement",
        "q6_plannable_truth_rows": len(q6_truth),
        "active_q6_truth_rows": len(active_truth),
        "q6_misses_before": before_misses,
        "q6_misses_after": after_misses,
        "q6_helped_rows": helped,
        "q6_still_missed_rows": still_missed,
        "q6_newly_missed_rows": newly_missed,
        "active_q6_p90_delta_median": _round(statistics.median(q6_deltas))
        if q6_deltas
        else None,
        "tail_replacement_truth_rows": tail_truth_rows,
        "tail_replacement_helped_rows": tail_truth_helped,
        "tail_replacement_after_misses": tail_truth_after_misses,
        "active_no_q6_rows": len(active_no_q6),
        "active_no_q6_positive_rows": no_q6_positive,
        "active_no_q6_new_positive_rows": no_q6_new_positive,
        "active_no_q6_p90_delta_median": _round(statistics.median(no_q6_deltas))
        if no_q6_deltas
        else None,
        "net_improvement": helped - no_q6_new_positive,
        "examples": examples,
    }


def _q6_tail_value_sampler_experiment(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    thresholds = [
        _q6_tail_value_sampler_threshold_summary(
            rows,
            bottom_row_threshold=threshold,
        )
        for threshold in _AISHA_SHIPWRECK_TAIL_VALUE_BOTTOM_THRESHOLDS
    ]
    return {
        "enabled": True,
        "candidate": "aisha_shipwreck_shape_layout_tail_replacement",
        "formal_decision_value_unchanged": True,
        "thresholds": thresholds,
        "best_by_net": sorted(
            thresholds,
            key=lambda row: (
                int(row["net_improvement"]),
                int(row["q6_helped_rows"]),
                -int(row["active_no_q6_rows"]),
            ),
            reverse=True,
        )[:3],
        "notes": (
            "Candidate uses max(v2_q6_decision_value_p90, "
            "v2_q6_tail_replacement_decision_value_p90) for diagnostics only."
        ),
    }


def _aisha_bottom_row_risk_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        row
        for row in rows
        if row.get("hero") == "aisha"
        and row.get("map_family") == "shipwreck"
        and row.get("footprint_bottom_row") is not None
    ]

    def segment(group_rows: list[dict[str, Any]]) -> dict[str, Any]:
        q6_truth = [
            row
            for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) > 0
            and row.get("v2_q6_decision_value_p90") is not None
        ]
        q6_misses = [
            row for row in q6_truth if row.get("q6_plannable_p90_misses_truth")
        ]
        cell_errors = [
            abs(int(row["v2_cells_p50_error"]))
            for row in group_rows
            if row.get("v2_cells_p50_error") is not None
        ]
        return {
            "rows": len(group_rows),
            "q6_plannable_truth_rows": len(q6_truth),
            "q6_plannable_miss_rows": len(q6_misses),
            "q6_plannable_miss_rate": (
                round(len(q6_misses) / len(q6_truth), 4) if q6_truth else None
            ),
            "cells_p50_abs_error_mean": (
                _round(statistics.mean(cell_errors)) if cell_errors else None
            ),
        }

    return {
        "bottom_row_threshold": AISHA_BOTTOM_ROW_RISK_THRESHOLD,
        "risk": segment(
            [row for row in eligible if row.get("aisha_bottom_row_risk")]
        ),
        "below_threshold": segment(
            [row for row in eligible if not row.get("aisha_bottom_row_risk")]
        ),
    }


def _q6_shadow_reference_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(
        (
            str(row.get("hero") or "unknown"),
            str(row.get("map_family") or "unknown"),
        )
        for row in rows
    )
    targets = [
        {
            "hero": hero,
            "map_family": family,
            "n": counts[(hero, family)],
            "target": target,
            "needed": max(0, target - counts[(hero, family)]),
            "ready": counts[(hero, family)] >= target,
        }
        for hero, family, target in _Q6_SHADOW_SAMPLING_TARGETS
    ]
    return {
        "sample_scope": "all_available_batch_samples",
        "ready": all(row["ready"] for row in targets),
        "total_needed": sum(row["needed"] for row in targets),
        "targets": targets,
        "priority_needs": [row for row in targets if row["needed"] > 0],
    }


def _decision_truth_value(row: Mapping[str, Any]) -> float | None:
    for key in (
        "v2_decision_value_truth",
        "final_decision_value_with_tail_replacement",
        "final_decision_value",
        "final_value",
    ):
        value = row.get(key)
        if value is not None:
            return float(value)
    p50 = row.get("v2_decision_value_p50")
    error = row.get("v2_decision_value_p50_error")
    if p50 is not None and error is not None:
        return float(p50) - float(error)
    return None


def _quantile_pinball_loss(*, signed_error: float, quantile: float) -> float:
    if signed_error >= 0:
        return (1.0 - quantile) * signed_error
    return quantile * -signed_error


def _decision_value_accuracy_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    p50_rows = [
        row for row in rows
        if row.get("v2_decision_value_p50_error") is not None
    ]
    p50_errors = [
        float(row["v2_decision_value_p50_error"]) for row in p50_rows
    ]
    p50_abs_errors = [abs(error) for error in p50_errors]
    normalized_abs_errors: list[float] = []
    normalized_p50_pinball_losses: list[float] = []
    for row, error in zip(p50_rows, p50_errors):
        truth = _decision_truth_value(row)
        if truth is None:
            continue
        denominator = _normalized_error_denominator(truth)
        normalized_abs_errors.append(abs(error) / denominator)
        normalized_p50_pinball_losses.append(
            _quantile_pinball_loss(signed_error=error, quantile=0.5)
            / denominator
        )

    p90_rows: list[tuple[dict[str, Any], float, float]] = []
    for row in rows:
        truth = _decision_truth_value(row)
        p90 = row.get("v2_decision_value_p90")
        if truth is not None and p90 is not None:
            signed_error = float(p90) - truth
        elif row.get("v2_decision_value_p90_error") is not None:
            signed_error = float(row["v2_decision_value_p90_error"])
            truth = _decision_truth_value(row)
            if truth is None:
                truth = 0.0
        else:
            continue
        p90_rows.append((row, signed_error, truth))

    p90_signed_errors = [signed_error for _, signed_error, _ in p90_rows]
    p90_pinball_losses = [
        _quantile_pinball_loss(signed_error=signed_error, quantile=0.9)
        for _, signed_error, _ in p90_rows
    ]
    p90_covered_excess_ratios: list[float] = []
    p90_under_ratios: list[float] = []
    normalized_p90_pinball_losses: list[float] = []
    extreme_over_values: list[float] = []
    for _, signed_error, truth in p90_rows:
        denominator = _normalized_error_denominator(truth)
        normalized_p90_pinball_losses.append(
            _quantile_pinball_loss(signed_error=signed_error, quantile=0.9)
            / denominator
        )
        if signed_error >= 0:
            p90_covered_excess_ratios.append(signed_error / denominator)
        else:
            p90_under_ratios.append(-signed_error / denominator)
        extreme_over_values.append(1.0 if signed_error > denominator else 0.0)

    return {
        "point_metric_note": (
            "MAE is a P50 center-error metric; compare it with normalized "
            "error and separate P90 risk metrics."
        ),
        "decision_rows": len(p50_rows),
        "decision_value_mae": (
            _round(statistics.mean(p50_abs_errors)) if p50_abs_errors else None
        ),
        "decision_value_median_abs_error": (
            _round(statistics.median(p50_abs_errors))
            if p50_abs_errors
            else None
        ),
        "median_normalized_abs_p50_error": (
            _round_float(statistics.median(normalized_abs_errors), 3)
            if normalized_abs_errors
            else None
        ),
        "p50_under_rate": (
            _round_float(
                statistics.mean(1.0 if error < 0 else 0.0 for error in p50_errors),
                4,
            )
            if p50_errors
            else None
        ),
        "p50_pinball_loss_mean": (
            _round(statistics.mean(abs(error) * 0.5 for error in p50_errors))
            if p50_errors
            else None
        ),
        "median_normalized_p50_pinball_loss": (
            _round_float(statistics.median(normalized_p50_pinball_losses), 3)
            if normalized_p50_pinball_losses
            else None
        ),
        "p90_rows": len(p90_rows),
        "p90_coverage": (
            _round_float(
                statistics.mean(
                    1.0 if signed_error >= 0 else 0.0
                    for signed_error in p90_signed_errors
                ),
                4,
            )
            if p90_signed_errors
            else None
        ),
        "median_p90_signed_error": (
            _round(statistics.median(p90_signed_errors))
            if p90_signed_errors
            else None
        ),
        "median_p90_under_ratio": (
            _round_float(statistics.median(p90_under_ratios), 3)
            if p90_under_ratios
            else None
        ),
        "median_p90_covered_excess_ratio": (
            _round_float(statistics.median(p90_covered_excess_ratios), 3)
            if p90_covered_excess_ratios
            else None
        ),
        "p90_extreme_over_rate": (
            _round_float(statistics.mean(extreme_over_values), 4)
            if extreme_over_values
            else None
        ),
        "p90_pinball_loss_mean": (
            _round(statistics.mean(p90_pinball_losses))
            if p90_pinball_losses
            else None
        ),
        "median_normalized_p90_pinball_loss": (
            _round_float(statistics.median(normalized_p90_pinball_losses), 3)
            if normalized_p90_pinball_losses
            else None
        ),
    }


def _case_metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decision_rows = [
        row
        for row in rows
        if row.get("v2_decision_value_p50_error") is not None
    ]
    decision_abs_errors = [
        abs(int(row["v2_decision_value_p50_error"])) for row in decision_rows
    ]
    p90_rows = [
        row
        for row in rows
        if row.get("v2_value_p90_covers_final") is not None
    ]
    q6_plannable_rows = [
        row
        for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_plannable_miss_rows = [
        row for row in q6_plannable_rows
        if row.get("q6_plannable_p90_misses_truth")
    ]
    no_q6_rows = [row for row in rows if _is_no_q6_control(row)]
    no_q6_positive_rows = [
        row for row in no_q6_rows
        if int(row.get("v2_q6_decision_value_p90") or 0) > 0
    ]
    cells_rows = [
        row for row in rows
        if row.get("v2_cells_p50_error") is not None
    ]
    tail_rows = [row for row in rows if _is_tail_event(row)]
    return {
        "rows": len(rows),
        "matched_rows": sum(1 for row in rows if row.get("v2_matched")),
        "zero_match_rows": sum(1 for row in rows if not row.get("v2_matched")),
        "decision_rows": len(decision_rows),
        "decision_value_mae": (
            _round(statistics.mean(decision_abs_errors))
            if decision_abs_errors
            else None
        ),
        "decision_value_median_abs_error": (
            _round(statistics.median(decision_abs_errors))
            if decision_abs_errors
            else None
        ),
        "decision_value_accuracy": _decision_value_accuracy_summary(rows),
        "value_p90_coverage": (
            round(
                statistics.mean(
                    1.0 if row.get("v2_value_p90_covers_final") else 0.0
                    for row in p90_rows
                ),
                4,
            )
            if p90_rows
            else None
        ),
        "q6_plannable_truth_rows": len(q6_plannable_rows),
        "q6_plannable_miss_rows": len(q6_plannable_miss_rows),
        "q6_plannable_coverage": (
            round(
                statistics.mean(
                    0.0 if row.get("q6_plannable_p90_misses_truth") else 1.0
                    for row in q6_plannable_rows
                ),
                4,
            )
            if q6_plannable_rows
            else None
        ),
        "no_q6_control_rows": len(no_q6_rows),
        "no_q6_positive_rows": len(no_q6_positive_rows),
        "no_q6_positive_rate": (
            round(len(no_q6_positive_rows) / len(no_q6_rows), 4)
            if no_q6_rows
            else None
        ),
        "no_q6_positive_median": (
            _round(
                statistics.median(
                    int(row.get("v2_q6_decision_value_p90") or 0)
                    for row in no_q6_positive_rows
                )
            )
            if no_q6_positive_rows
            else None
        ),
        "cells_p50_mae": (
            _round(
                statistics.mean(
                    abs(int(row["v2_cells_p50_error"])) for row in cells_rows
                )
            )
            if cells_rows
            else None
        ),
        "tail_event_rows": len(tail_rows),
        "tail_trimmed_value_median": (
            _round(
                statistics.median(
                    int(row.get("final_trimmed_tail_value") or 0)
                    for row in tail_rows
                )
            )
            if tail_rows
            else None
        ),
    }


def _case_breakdown_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    normal_rows = [row for row in rows if _is_normal_case(row)]
    early_rows = [row for row in rows if _is_early_diagnostic(row)]
    single_round_rows = [row for row in rows if row.get("capture_round") == 1]
    tail_rows = [row for row in rows if _is_tail_event(row)]
    hidden_rows = [row for row in rows if _is_hidden_case(row)]
    no_q6_rows = [row for row in rows if _is_no_q6_control(row)]
    zero_q6_proven_rows = [row for row in rows if _is_zero_q6_proven(row)]
    high_info_value_miss_rows = [
        row for row in rows if _is_high_info_value_miss(row)
    ]
    high_info_q6_miss_rows = [
        row for row in rows if _is_high_info_q6_miss(row)
    ]
    avg_cells_unique_rows = [
        row
        for row in rows
        if str(row.get("public_avg_cells_solution_band") or "none")
        in {"all_unique", "mixed_unique"}
    ]
    avg_cells_ambiguous_rows = [
        row
        for row in rows
        if row.get("public_avg_cells_solution_band") == "ambiguous"
    ]
    tag_counts: Counter[str] = Counter()
    for row in rows:
        tag_counts.update(_case_tags(row))
    return {
        "normal_case": _case_metric_summary(normal_rows),
        "early_diagnostic": _case_metric_summary(early_rows),
        "single_round": _case_metric_summary(single_round_rows),
        "tail_event": _case_metric_summary(tail_rows),
        "hidden_case": _case_metric_summary(hidden_rows),
        "no_q6_control": _case_metric_summary(no_q6_rows),
        "zero_q6_proven": _case_metric_summary(zero_q6_proven_rows),
        "high_info_value_miss": _case_metric_summary(high_info_value_miss_rows),
        "high_info_q6_miss": _case_metric_summary(high_info_q6_miss_rows),
        "avg_cells_unique": _case_metric_summary(avg_cells_unique_rows),
        "avg_cells_ambiguous": _case_metric_summary(avg_cells_ambiguous_rows),
        "case_tag_counts": dict(sorted(tag_counts.items())),
        "criteria": {
            "normal_case": (
                "calibration_eligible && matched && !early_1_2 && "
                "!unsupported_tail_event"
            ),
            "early_diagnostic": "evidence_stage == early_1_2",
            "single_round": "capture_round == 1",
            "tail_event": "final_trimmed_tail_value > 0 || final_q6_trimmed_tail_value > 0",
            "hidden_case": "map_family == hidden",
            "no_q6_control": "final_q6_decision_value <= 0",
            "zero_q6_proven": "public_max_quality < 6 || q6 exact count == 0",
            "high_info_value_miss": (
                "information_density_band == high && "
                "(abs(decision_value_p50_error) >= 300000 || P90 undercovers final)"
            ),
            "high_info_q6_miss": (
                "information_density_band == high && q6_plannable_p90_misses_truth"
            ),
            "avg_cells_unique": "public avg-cells integer inversion has one candidate",
            "avg_cells_ambiguous": "public avg-cells integer inversion has multiple candidates",
        },
    }


def _public_info_semantics_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    events_by_id: Counter[int] = Counter()
    rows_by_id: Counter[int] = Counter()
    semantic_events: Counter[str] = Counter()
    model_use_events: Counter[str] = Counter()
    constraint_events: Counter[str] = Counter()
    examples: dict[int, list[str]] = defaultdict(list)

    for row in rows:
        row_ids: set[int] = set()
        for key, count in _parse_counter_field(row.get("public_info_ids")).items():
            try:
                info_id = int(key)
            except ValueError:
                continue
            events_by_id[info_id] += count
            row_ids.add(info_id)
            semantic = _public_info_semantic(info_id)
            semantic_events[semantic["semantic"]] += count
            model_use_events[semantic["model_use"]] += count
            constraint_events[semantic["constraint"]] += count
        for info_id in row_ids:
            rows_by_id[info_id] += 1
            if len(examples[info_id]) < 5:
                examples[info_id].append(str(row.get("file") or ""))

    by_id: list[dict[str, Any]] = []
    pending_model_ids: list[int] = []
    unknown_ids: list[int] = []
    screenshot_ids: list[int] = []
    hard_ids: list[int] = []
    soft_ids: list[int] = []
    diagnostic_ids: list[int] = []
    partial_ids: list[int] = []
    for info_id, events in sorted(events_by_id.items()):
        semantic = _public_info_semantic(info_id)
        constraint = semantic["constraint"]
        reference = semantic["reference"]
        if constraint in {"pending", "unknown"}:
            pending_model_ids.append(info_id)
        if constraint.startswith("hard"):
            hard_ids.append(info_id)
        elif constraint == "soft":
            soft_ids.append(info_id)
        elif constraint == "diagnostic":
            diagnostic_ids.append(info_id)
        elif constraint == "partial":
            partial_ids.append(info_id)
        if reference == "missing":
            unknown_ids.append(info_id)
            screenshot_ids.append(info_id)
        by_id.append(
            {
                "info_id": info_id,
                "events": events,
                "rows": rows_by_id[info_id],
                "semantic": semantic["semantic"],
                "model_use": semantic["model_use"],
                "constraint": constraint,
                "reference": reference,
                "examples": examples[info_id],
            }
        )

    return {
        "events": sum(events_by_id.values()),
        "rows_with_public_info": sum(
            1 for row in rows if str(row.get("public_info_ids") or "")
        ),
        "unique_ids": len(events_by_id),
        "by_id": by_id,
        "semantic_events": _format_counter(semantic_events),
        "model_use_events": _format_counter(model_use_events),
        "constraint_events": _format_counter(constraint_events),
        "modeled_hard_ids": hard_ids,
        "modeled_soft_ids": soft_ids,
        "diagnostic_ids": diagnostic_ids,
        "partial_ids": partial_ids,
        "pending_model_ids": pending_model_ids,
        "unknown_ids": unknown_ids,
        "needs_screenshot_ids": screenshot_ids,
        "notes": {
            "known_reference": (
                "Known IDs are mapped from Skill_export semantics; screenshots are "
                "only needed for unknown IDs or live UI/reference mismatches."
            ),
            "avg_values": (
                "Full packet floats remove OCR rounding, but averages are not "
                "unique inventory solvers unless count/total or item-table "
                "constraints make the integer solution unique."
            ),
            "zero_q6": (
                "public:200048 as highest-quality item can prove no q6 when its "
                "observed quality is below 6; item-level gold evidence alone "
                "does not prove q6 absence."
            ),
        },
    }


def _public_avg_cells_uniqueness_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    band_counts = Counter(
        str(row.get("public_avg_cells_solution_band") or "none")
        for row in rows
    )
    status_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        statuses = [
            status
            for status in str(
                row.get("public_avg_cells_solution_statuses") or ""
            ).split(";")
            if status
        ]
        for status in statuses:
            status_counts[status] += 1
            if len(examples[status]) < 5:
                examples[status].append(str(row.get("file") or ""))
    return {
        "band_counts": dict(sorted(band_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "unique_rows": band_counts.get("all_unique", 0) + band_counts.get(
            "mixed_unique",
            0,
        ),
        "ambiguous_rows": band_counts.get("ambiguous", 0),
        "no_candidate_rows": band_counts.get("no_candidate", 0),
        "examples": {
            status: files
            for status, files in sorted(examples.items())
        },
        "note": (
            "Unique means the packet float maps to exactly one integer "
            "(count,total_cells) candidate within broad map/drop bounds. "
            "Quality-specific averages are evaluated separately from total "
            "all-item averages."
        ),
    }


def _summary(
    rows: list[dict[str, Any]],
    *,
    q6_residual_floor_ratio: float = 0.0,
) -> dict[str, Any]:
    ok = [row for row in rows if row.get("status") == "ok"]
    valued = [
        row for row in ok
        if row.get("v2_value_p50_error") is not None
    ]
    zero = [
        row for row in ok
        if not row.get("v2_matched")
    ]
    relaxed = [
        row for row in ok
        if row.get("relaxed_exact_used")
    ]
    layout_conflict = [row for row in ok if row.get("layout_conflict")]
    q6_false_low = [row for row in ok if row.get("q6_false_low_risk")]
    q6_below_prior = [row for row in ok if row.get("q6_below_drop_prior")]
    q6_p90_miss = [row for row in ok if row.get("q6_p90_misses_truth")]
    q6_plannable_p90_miss = [
        row for row in ok
        if row.get("q6_plannable_p90_misses_truth")
    ]
    category_target_rows = [
        row for row in ok
        if int(row.get("category_target_count") or 0) > 0
    ]
    category_exclusion_rows = [
        row for row in ok
        if int(row.get("category_exclusion_count") or 0) > 0
    ]
    category_no_pool_rows = [
        row for row in ok
        if "category_target_no_pool_match:" in str(row.get("diagnostics") or "")
    ]
    presolve_unreachable_rows = [
        row for row in ok
        if row.get("presolve_unreachable_exact_buckets")
    ]
    calibration_rows = [
        row for row in ok
        if row.get("calibration_eligible") is True
    ]
    calibration_q6_plannable_rows = [
        row
        for row in calibration_rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    early_rows = [
        row for row in ok
        if row.get("evidence_stage") == "early_1_2"
    ]
    single_round_rows = [
        row for row in ok if row.get("capture_round") == 1
    ]
    early_large_cells_gap_rows = [
        row
        for row in early_rows
        if row.get("v2_cells_p50_error") is not None
        and abs(int(row["v2_cells_p50_error"])) >= 30
    ]
    high_value_undercovered = [
        row for row in ok
        if row.get("value_tier") == ">=1.2m"
        and row.get("v2_value_p90_covers_final") is False
    ]
    abs_errors = [abs(int(row["v2_value_p50_error"])) for row in valued]
    decision_valued = [
        row for row in ok
        if row.get("v2_decision_value_p50_error") is not None
    ]
    regular_decision_valued = [
        row for row in decision_valued
        if int(row.get("final_trimmed_tail_value") or 0) == 0
    ]
    tail_event_decision_valued = [
        row for row in decision_valued
        if int(row.get("final_trimmed_tail_value") or 0) > 0
    ]
    decision_abs_errors = [
        abs(int(row["v2_decision_value_p50_error"])) for row in decision_valued
    ]
    regular_decision_abs_errors = [
        abs(int(row["v2_decision_value_p50_error"]))
        for row in regular_decision_valued
    ]
    tail_event_decision_abs_errors = [
        abs(int(row["v2_decision_value_p50_error"]))
        for row in tail_event_decision_valued
    ]
    p90_valued = [
        row for row in ok
        if row.get("v2_value_p90_error") is not None
    ]
    p90_abs_errors = [abs(int(row["v2_value_p90_error"])) for row in p90_valued]
    q6_rows = [row for row in valued if int(row.get("final_q6_count") or 0) > 0]
    q6_plannable_rows = [
        row for row in valued
        if int(row.get("final_q6_decision_value") or 0) > 0
    ]
    q6_no_plannable_rows = [
        row for row in valued
        if int(row.get("final_q6_decision_value") or 0) <= 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_no_plannable_p90_positive = [
        row for row in q6_no_plannable_rows
        if int(row.get("v2_q6_decision_value_p90") or 0) > 0
    ]
    q6_tail_rows = [
        row for row in valued
        if int(row.get("final_q6_trimmed_tail_value") or 0) > 0
    ]
    summary = {
        "files": len(rows),
        "ok": len(ok),
        "valued": len(valued),
        "zero_match": len(zero),
        "relaxed_exact": len(relaxed),
        "zero_match_after_relax": sum(1 for row in zero if row.get("relaxed_exact_used")),
        "layout_conflict": len(layout_conflict),
        "zero_match_with_layout_conflict": sum(
            1 for row in zero if row.get("layout_conflict")
        ),
        "q6_false_low_risk": len(q6_false_low),
        "q6_below_drop_prior": len(q6_below_prior),
        "q6_p90_misses_truth": len(q6_p90_miss),
        "q6_plannable_p90_misses_truth": len(q6_plannable_p90_miss),
        "high_value_p90_undercovered": len(high_value_undercovered),
        "presolve_unreachable_exact_rows": len(presolve_unreachable_rows),
        "skip_or_error": len(rows) - len(ok),
        "value_mae": _round(statistics.mean(abs_errors)) if abs_errors else None,
        "value_median_abs_error": (
            _round(statistics.median(abs_errors)) if abs_errors else None
        ),
        "decision_value_mae": (
            _round(statistics.mean(decision_abs_errors)) if decision_abs_errors else None
        ),
        "regular_decision_value_mae": (
            _round(statistics.mean(regular_decision_abs_errors))
            if regular_decision_abs_errors
            else None
        ),
        "tail_event_decision_value_mae": (
            _round(statistics.mean(tail_event_decision_abs_errors))
            if tail_event_decision_abs_errors
            else None
        ),
        "tail_event_count": len(tail_event_decision_valued),
        "decision_value_median_abs_error": (
            _round(statistics.median(decision_abs_errors))
            if decision_abs_errors
            else None
        ),
        "tail_event_trimmed_value_median": (
            _round(
                statistics.median(
                    int(row.get("final_trimmed_tail_value") or 0)
                    for row in tail_event_decision_valued
                )
            )
            if tail_event_decision_valued
            else None
        ),
        "decision_value_accuracy": _decision_value_accuracy_summary(ok),
        "value_p90_mae": (
            _round(statistics.mean(p90_abs_errors)) if p90_abs_errors else None
        ),
        "value_p90_median_abs_error": (
            _round(statistics.median(p90_abs_errors)) if p90_abs_errors else None
        ),
        "value_p90_coverage": (
            round(
                statistics.mean(
                    1.0 if row["v2_value_p90_covers_final"] else 0.0
                    for row in p90_valued
                ),
                4,
            )
            if p90_valued
            else None
        ),
        "mean_match_rate": (
            round(statistics.mean(row["v2_match_rate"] for row in valued), 4)
            if valued
            else None
        ),
        "q6_truth_files": len(q6_rows),
        "q6_plannable_truth_files": len(q6_plannable_rows),
        "q6_tail_event_files": len(q6_tail_rows),
        "q6_no_plannable_truth_files": len(q6_no_plannable_rows),
        "q6_no_plannable_p90_positive": len(q6_no_plannable_p90_positive),
        "q6_no_plannable_p90_positive_rate": (
            round(len(q6_no_plannable_p90_positive) / len(q6_no_plannable_rows), 4)
            if q6_no_plannable_rows
            else None
        ),
        "q6_no_plannable_p90_positive_median": (
            _round(
                statistics.median(
                    int(row.get("v2_q6_decision_value_p90") or 0)
                    for row in q6_no_plannable_p90_positive
                )
            )
            if q6_no_plannable_p90_positive
            else None
        ),
        "q6_value_p90_coverage": (
            round(
                statistics.mean(
                    0.0 if row.get("q6_p90_misses_truth") else 1.0
                    for row in q6_rows
                    if row.get("v2_q6_value_p90") is not None
                ),
                4,
            )
            if any(row.get("v2_q6_value_p90") is not None for row in q6_rows)
            else None
        ),
        "q6_plannable_value_p90_coverage": (
            round(
                statistics.mean(
                    0.0 if row.get("q6_plannable_p90_misses_truth") else 1.0
                    for row in q6_plannable_rows
                    if row.get("v2_q6_decision_value_p90") is not None
                ),
                4,
            )
            if any(
                row.get("v2_q6_decision_value_p90") is not None
                for row in q6_plannable_rows
            )
            else None
        ),
        "q6_tail_trimmed_value_median": (
            _round(
                statistics.median(
                    int(row.get("final_q6_trimmed_tail_value") or 0)
                    for row in q6_tail_rows
                )
            )
            if q6_tail_rows
            else None
        ),
        "q6_truth_p90_coverage": (
            round(
                statistics.mean(
                    1.0 if row["v2_value_p90_covers_final"] else 0.0
                    for row in q6_rows
                ),
                4,
            )
            if q6_rows
            else None
        ),
        "worst_value_errors": sorted(
            (
                {
                    "file": row["file"],
                    "hero": row.get("hero"),
                    "map_id": row.get("map_id"),
                    "map_family": row.get("map_family"),
                    "value_tier": row.get("value_tier"),
                    "final_value": row.get("final_value"),
                    "v2_value_p50": row.get("v2_value_p50"),
                    "v2_value_p90": row.get("v2_value_p90"),
                    "final_decision_value": row.get("final_decision_value"),
                    "v2_decision_value_p50": row.get("v2_decision_value_p50"),
                    "v2_decision_value_p90": row.get("v2_decision_value_p90"),
                    "v2_value_p50_error": row.get("v2_value_p50_error"),
                    "v2_value_p90_error": row.get("v2_value_p90_error"),
                    "v2_decision_value_p50_error": row.get(
                        "v2_decision_value_p50_error"
                    ),
                    "v2_matched": row.get("v2_matched"),
                    "anchor_count": row.get("anchor_count"),
                    "v2_q6_match_rate": row.get("v2_q6_match_rate"),
                    "v2_q6_value_p90": row.get("v2_q6_value_p90"),
                    "v2_q6_decision_value_p90": row.get(
                        "v2_q6_decision_value_p90"
                    ),
                    "v2_q6_count_p90": row.get("v2_q6_count_p90"),
                    "v2_q6_cells_p90": row.get("v2_q6_cells_p90"),
                    "final_q6_count": row.get("final_q6_count"),
                    "final_q6_cells": row.get("final_q6_cells"),
                    "final_q6_value": row.get("final_q6_value"),
                    "final_q6_decision_value": row.get("final_q6_decision_value"),
                    "final_q6_trimmed_tail_value": row.get(
                        "final_q6_trimmed_tail_value"
                    ),
                    "final_top_item_name": row.get("final_top_item_name"),
                    "final_top_item_value": row.get("final_top_item_value"),
                    "layout_score": row.get("layout_score"),
                    "diagnostics": row.get("diagnostics"),
                }
                for row in valued
            ),
            key=lambda row: abs(int(row["v2_value_p50_error"])),
            reverse=True,
        )[:12],
        "zero_match_details": [
            {
                "file": row["file"],
                "hero": row.get("hero"),
                "map_id": row.get("map_id"),
                "map_family": row.get("map_family"),
                "final_value": row.get("final_value"),
                "bucket_targets": row.get("bucket_targets"),
                "zero_match_root": row.get("zero_match_root"),
                "diagnostics": row.get("diagnostics"),
            }
            for row in zero[:12]
        ],
        "q6_false_low_details": [
            {
                "file": row["file"],
                "hero": row.get("hero"),
                "map_id": row.get("map_id"),
                "final_q6_count": row.get("final_q6_count"),
                "final_q6_value": row.get("final_q6_value"),
                "final_q6_decision_value": row.get("final_q6_decision_value"),
                "final_q6_trimmed_tail_value": row.get("final_q6_trimmed_tail_value"),
                "v2_q6_match_rate": row.get("v2_q6_match_rate"),
                "v2_q6_prior_expected_count": row.get("v2_q6_prior_expected_count"),
                "v2_q6_prior_expected_cells": row.get("v2_q6_prior_expected_cells"),
                "v2_q6_value_p90": row.get("v2_q6_value_p90"),
                "v2_q6_decision_value_p90": row.get("v2_q6_decision_value_p90"),
                "v2_q6_count_p90": row.get("v2_q6_count_p90"),
                "v2_q6_cells_p90": row.get("v2_q6_cells_p90"),
                "v2_q6_count_p90_under_by": row.get("v2_q6_count_p90_under_by"),
                "v2_q6_cells_p90_under_by": row.get("v2_q6_cells_p90_under_by"),
                "v2_q6_count_p90_under_prior_by": row.get(
                    "v2_q6_count_p90_under_prior_by"
                ),
                "v2_q6_cells_p90_under_prior_by": row.get(
                    "v2_q6_cells_p90_under_prior_by"
                ),
                "v2_q6_value_p90_under_by": row.get("v2_q6_value_p90_under_by"),
                "v2_q6_decision_value_p90_under_by": row.get(
                    "v2_q6_decision_value_p90_under_by"
                ),
                "q6_top_size_band": row.get("q6_top_size_band"),
                "q6_miss_root": row.get("q6_miss_root"),
                "q6_plannable_miss_root": row.get("q6_plannable_miss_root"),
                "diagnostics": row.get("diagnostics"),
            }
            for row in q6_false_low[:12]
        ],
        "zero_match_root_causes": _root_cause_summary(zero, "zero_match_root"),
        "layout_conflict_root_causes": _root_cause_summary(
            layout_conflict,
            "layout_conflict_root",
        ),
        "q6_miss_root_causes": _root_cause_summary(q6_p90_miss, "q6_miss_root"),
        "q6_plannable_miss_root_causes": _root_cause_summary(
            q6_plannable_p90_miss,
            "q6_plannable_miss_root",
        ),
        "q6_calibration_priority": _q6_calibration_priority(ok),
        "q6_plannable_calibration_priority": _q6_plannable_calibration_priority(ok),
        "q6_actionable_targets": _q6_actionable_targets(ok),
        "q6_space_diagnostics": _q6_space_diagnostics(ok),
        "aisha_bottom_row_risk": _aisha_bottom_row_risk_summary(ok),
        "case_breakdown": _case_breakdown_summary(ok),
        "public_info_semantics": _public_info_semantics_summary(ok),
        "public_avg_cells_uniqueness": _public_avg_cells_uniqueness_summary(ok),
        "evidence_parameter_audit": _evidence_parameter_audit_summary(ok),
        "q6_shadow_reference_coverage": _q6_shadow_reference_coverage(ok),
        "q6_residual_boost_experiment": _q6_residual_boost_summary(ok),
        "q6_residual_prior_floor_sampler_experiment": (
            _q6_residual_prior_floor_sampler_summary(ok)
        ),
        "q6_residual_value_sampler_experiment": (
            _q6_residual_value_sampler_summary(ok)
        ),
        "q6_conditional_target_sampler_experiment": (
            _q6_conditional_target_sampler_summary(ok)
        ),
        "q6_tail_value_sampler_experiment": (
            _q6_tail_value_sampler_experiment(ok)
        ),
        "q6_condition_audit": _q6_condition_audit_summary(ok),
        "q6_cell_gap_by_feature": _q6_cell_gap_by_feature_summary(ok),
        "q6_conditional_target_experiment": (
            _q6_conditional_target_experiment_summary(ok)
        ),
        "aisha_shipwreck_deep_threshold_p90_sweep": (
            _aisha_shipwreck_deep_threshold_p90_sweep(ok)
        ),
        "q6_risk_groups": {
            "hero_map_family": _q6_group_summary(ok, ("hero", "map_family")),
            "map_family_value_tier": _q6_group_summary(
                ok,
                ("map_family", "value_tier"),
            ),
            "anchor_band": _q6_group_summary(ok, ("anchor_band",)),
            "public_constraint": _q6_group_summary(ok, ("public_constraint_key",)),
            "random_sample_avg_signal": _q6_group_summary(
                ok,
                ("random_sample_avg_signal_band",),
            ),
            "evidence_profile": _q6_group_summary(ok, ("evidence_profile_key",)),
            "top_item_size": _q6_group_summary(ok, ("q6_top_size_band",)),
            "evidence_stage": _q6_group_summary(ok, ("evidence_stage",)),
            "information_density": _q6_group_summary(ok, ("information_density_band",)),
        },
        "q6_plannable_risk_groups": {
            "hero_map_family": _q6_plannable_group_summary(
                ok,
                ("hero", "map_family"),
            ),
            "top_item_size": _q6_plannable_group_summary(
                ok,
                ("q6_top_size_band",),
            ),
            "evidence_stage": _q6_plannable_group_summary(
                ok,
                ("evidence_stage",),
            ),
            "information_density": _q6_plannable_group_summary(
                ok,
                ("information_density_band",),
            ),
            "random_sample_avg_signal": _q6_plannable_group_summary(
                ok,
                ("random_sample_avg_signal_band",),
            ),
            "evidence_profile": _q6_plannable_group_summary(
                ok,
                ("evidence_profile_key",),
            ),
        },
        "category_evidence": {
            "target_rows": len(category_target_rows),
            "exclusion_rows": len(category_exclusion_rows),
            "target_total": sum(
                int(row.get("category_target_count") or 0)
                for row in ok
            ),
            "exclusion_total": sum(
                int(row.get("category_exclusion_count") or 0)
                for row in ok
            ),
            "no_pool_match_rows": len(category_no_pool_rows),
            "action_combo_top": [
                {
                    "combo": combo,
                    "n": n,
                }
                for combo, n in Counter(
                    str(row.get("category_action_combo") or "none")
                    for row in ok
                ).most_common(12)
            ],
            "examples": [
                {
                    "file": row["file"],
                    "hero": row.get("hero"),
                    "map_family": row.get("map_family"),
                    "category_action_combo": row.get("category_action_combo"),
                    "category_target_count": row.get("category_target_count"),
                    "category_exclusion_count": row.get("category_exclusion_count"),
                    "diagnostics": row.get("diagnostics"),
                }
                for row in sorted(
                    category_target_rows,
                    key=lambda item: (
                        int(item.get("category_exclusion_count") or 0),
                        int(item.get("category_target_count") or 0),
                    ),
                    reverse=True,
                )[:10]
            ],
            "no_pool_match_examples": [
                {
                    "file": row["file"],
                    "hero": row.get("hero"),
                    "map_family": row.get("map_family"),
                    "diagnostics": row.get("diagnostics"),
                }
                for row in category_no_pool_rows[:10]
            ],
        },
        "presolve_exact_bucket": {
            "unreachable_rows": len(presolve_unreachable_rows),
            "zero_match_rows": sum(
                1 for row in presolve_unreachable_rows
                if not row.get("v2_matched")
            ),
            "relaxed_exact_rows": sum(
                1 for row in presolve_unreachable_rows
                if row.get("relaxed_exact_used")
            ),
            "examples": [
                {
                    "file": row["file"],
                    "hero": row.get("hero"),
                    "map_family": row.get("map_family"),
                    "bucket_targets": row.get("bucket_targets"),
                    "presolve_unreachable_exact_buckets": row.get(
                        "presolve_unreachable_exact_buckets"
                    ),
                    "zero_match_root": row.get("zero_match_root"),
                    "diagnostics": row.get("diagnostics"),
                }
                for row in presolve_unreachable_rows[:12]
            ],
        },
        "sample_feasibility": {
            "calibration_eligible_rows": len(calibration_rows),
            "early_rows": len(early_rows),
            "single_round_rows": len(single_round_rows),
            "calibration_decision_value_mae": (
                _round(
                    statistics.mean(
                        abs(int(row["v2_decision_value_p50_error"]))
                        for row in calibration_rows
                        if row.get("v2_decision_value_p50_error") is not None
                    )
                )
                if any(
                    row.get("v2_decision_value_p50_error") is not None
                    for row in calibration_rows
                )
                else None
            ),
            "calibration_q6_p90_misses_truth": sum(
                1 for row in calibration_rows
                if row.get("q6_p90_misses_truth")
            ),
            "calibration_q6_plannable_truth_rows": sum(
                1 for _row in calibration_q6_plannable_rows
            ),
            "calibration_q6_plannable_p90_misses_truth": sum(
                1
                for row in calibration_q6_plannable_rows
                if row.get("q6_plannable_p90_misses_truth")
            ),
            "calibration_q6_plannable_value_p90_coverage": (
                round(
                    statistics.mean(
                        0.0 if row.get("q6_plannable_p90_misses_truth") else 1.0
                        for row in calibration_q6_plannable_rows
                    ),
                    4,
                )
                if calibration_q6_plannable_rows
                else None
            ),
            "calibration_cells_p50_mae": (
                _round(
                    statistics.mean(
                        abs(int(row["v2_cells_p50_error"]))
                        for row in calibration_rows
                        if row.get("v2_cells_p50_error") is not None
                    )
                )
                if any(
                    row.get("v2_cells_p50_error") is not None
                    for row in calibration_rows
                )
                else None
            ),
            "early_cells_p50_mae": (
                _round(
                    statistics.mean(
                        abs(int(row["v2_cells_p50_error"]))
                        for row in early_rows
                        if row.get("v2_cells_p50_error") is not None
                    )
                )
                if any(
                    row.get("v2_cells_p50_error") is not None
                    for row in early_rows
                )
                else None
            ),
            "early_large_cells_gap_rows": len(early_large_cells_gap_rows),
            "by_evidence_stage": dict(
                Counter(str(row.get("evidence_stage") or "unknown") for row in ok)
            ),
            "by_information_density": dict(
                Counter(
                    str(row.get("information_density_band") or "unknown")
                    for row in ok
                )
            ),
            "early_examples": [
                {
                    "file": row["file"],
                    "hero": row.get("hero"),
                    "map_family": row.get("map_family"),
                    "capture_round": row.get("capture_round"),
                    "category_target_count": row.get("category_target_count"),
                    "category_exclusion_count": row.get("category_exclusion_count"),
                }
                for row in early_rows[:10]
            ],
            "single_round_examples": [
                {
                    "file": row["file"],
                    "hero": row.get("hero"),
                    "map_family": row.get("map_family"),
                    "final_cells": row.get("final_cells"),
                    "v2_cells_p50": row.get("v2_cells_p50"),
                    "v2_cells_p90": row.get("v2_cells_p90"),
                }
                for row in single_round_rows[:10]
            ],
            "early_large_cells_gap_examples": [
                {
                    "file": row["file"],
                    "hero": row.get("hero"),
                    "map_family": row.get("map_family"),
                    "capture_round": row.get("capture_round"),
                    "final_cells": row.get("final_cells"),
                    "v2_cells_p50": row.get("v2_cells_p50"),
                    "v2_cells_p90": row.get("v2_cells_p90"),
                    "v2_cells_p50_error": row.get("v2_cells_p50_error"),
                }
                for row in sorted(
                    early_large_cells_gap_rows,
                    key=lambda item: abs(int(item.get("v2_cells_p50_error") or 0)),
                    reverse=True,
                )[:10]
            ],
        },
        "groups": {
            "hero": _group_summary(ok, "hero"),
            "map_family": _group_summary(ok, "map_family"),
            "value_tier": _group_summary(ok, "value_tier"),
            "evidence_stage": _group_summary(ok, "evidence_stage"),
            "information_density": _group_summary(ok, "information_density_band"),
            "hero_information_density": _group_summary(ok, "hero_information_density"),
            "hero_evidence_stage": _group_summary(ok, "hero_evidence_stage"),
            "density_value_tier": _group_summary(ok, "density_value_tier"),
            "random_sample_avg_signal": _group_summary(
                ok,
                "random_sample_avg_signal_band",
            ),
            "evidence_profile": _group_summary(ok, "evidence_profile_key"),
        },
        "collection_readiness": _collection_readiness(
            ok,
            target_per_hero_family=30,
            hidden_target_per_hero=10,
            hidden_target_by_hero={"aisha": 10, "ethan": 5},
        ),
        "bid_gap": {
            "hero": _bid_gap_summary(ok, "hero"),
            "map_family": _bid_gap_summary(ok, "map_family"),
        },
    }
    experiment = _q6_residual_floor_experiment(
        ok,
        floor_ratio=q6_residual_floor_ratio,
    )
    if experiment is not None:
        summary["q6_residual_floor_experiment"] = experiment
    count_cell_experiment = _q6_count_cell_prior_floor_experiment(
        ok,
        floor_ratio=q6_residual_floor_ratio,
    )
    if count_cell_experiment is not None:
        summary["q6_count_cell_prior_floor_experiment"] = count_cell_experiment
    low_space_experiment = _q6_low_space_residual_floor_experiment(
        ok,
        floor_ratio=q6_residual_floor_ratio,
    )
    if low_space_experiment is not None:
        summary["q6_low_space_residual_floor_experiment"] = low_space_experiment
    low_space_gated_experiment = _q6_low_space_residual_gated_floor_experiment(
        ok,
        floor_ratio=q6_residual_floor_ratio,
    )
    if low_space_gated_experiment is not None:
        summary["q6_low_space_residual_gated_floor_experiment"] = (
            low_space_gated_experiment
        )
    gated_count_cell_experiment = _q6_count_cell_prior_gated_floor_experiment(
        ok,
        floor_ratio=q6_residual_floor_ratio,
    )
    if gated_count_cell_experiment is not None:
        summary["q6_count_cell_prior_gated_floor_experiment"] = (
            gated_count_cell_experiment
        )
    profile_gated_count_cell_experiment = _q6_count_cell_prior_gated_floor_experiment(
        ok,
        floor_ratio=q6_residual_floor_ratio,
        gate_keys=("hero", "map_family", "evidence_profile_key"),
        gate_name="hero_map_family_profile_positive_net",
        min_q6_truth=10,
    )
    if profile_gated_count_cell_experiment is not None:
        summary["q6_count_cell_prior_profile_gated_floor_experiment"] = (
            profile_gated_count_cell_experiment
        )
    summary["q6_count_cell_prior_narrow_gate_p90_sweep"] = (
        _q6_count_cell_prior_narrow_gate_p90_sweep(ok)
    )
    return summary


def _root_cause_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        raw = str(row.get(key) or "unclassified")
        markers = [part for part in raw.split(";") if part] or ["unclassified"]
        for marker in markers:
            counts[marker] += 1
            if len(examples[marker]) < 5:
                examples[marker].append(str(row.get("file") or ""))
    return [
        {
            "cause": cause,
            "n": n,
            "examples": examples[cause],
        }
        for cause, n in counts.most_common()
    ]


def _group_key(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    return "|".join(f"{key}={row.get(key) or 'unknown'}" for key in keys)


def _q6_residual_floor_group_summary(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    floor_ratio: float,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        groups.setdefault(_group_key(row, keys), []).append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        q6_truth = [
            row for row in group_rows
            if int(row.get("final_q6_value") or 0) > 0
            and row.get("v2_q6_value_p90") is not None
        ]
        if not q6_truth:
            continue
        before_misses = sum(1 for row in q6_truth if row.get("q6_p90_misses_truth"))
        after_misses = 0
        floors: list[int] = []
        eligible_truth = 0
        for row in q6_truth:
            q6_p90 = int(row.get("v2_q6_value_p90") or 0)
            floor_value = _q6_residual_floor_value(row, floor_ratio=floor_ratio)
            if floor_value is not None:
                eligible_truth += 1
                floors.append(floor_value)
                q6_p90 = max(q6_p90, floor_value)
            if q6_p90 < int(row.get("final_q6_value") or 0):
                after_misses += 1
        eligible_no_q6 = sum(
            1 for row in group_rows
            if int(row.get("final_q6_value") or 0) <= 0
            and _q6_residual_floor_value(row, floor_ratio=floor_ratio) is not None
        )
        out.append(
            {
                "group": group,
                "n": len(group_rows),
                "q6_truth": len(q6_truth),
                "eligible_rows": eligible_truth,
                "eligible_no_q6_rows": eligible_no_q6,
                "q6_p90_misses_before": before_misses,
                "q6_p90_misses_after": after_misses,
                "q6_p90_miss_improvement": before_misses - after_misses,
                "q6_value_p90_coverage_after": round(
                    1.0 - after_misses / len(q6_truth),
                    4,
                ),
                "floor_median": _round(statistics.median(floors))
                if floors
                else None,
            }
        )
    return sorted(
        out,
        key=lambda row: (
            int(row["q6_p90_miss_improvement"]),
            -int(row["eligible_no_q6_rows"]),
            int(row["q6_truth"]),
        ),
        reverse=True,
    )[:12]


def _q6_count_cell_prior_floor_group_summary(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    floor_ratio: float,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_group_key(row, keys), []).append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        q6_truth = [
            row for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) > 0
            and row.get("v2_q6_decision_value_p90") is not None
        ]
        if not q6_truth:
            continue
        before_misses = sum(
            1 for row in q6_truth
            if row.get("q6_plannable_p90_misses_truth")
        )
        after_misses = 0
        eligible_truth = 0
        floors: list[int] = []
        for row in q6_truth:
            q6_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
            floor_value = _q6_count_cell_prior_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            if floor_value is not None:
                eligible_truth += 1
                floors.append(floor_value)
                q6_p90 = max(q6_p90, floor_value)
            if q6_p90 < int(row.get("final_q6_decision_value") or 0):
                after_misses += 1
        eligible_no_q6 = sum(
            1 for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) <= 0
            and _q6_count_cell_prior_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            is not None
        )
        out.append(
            {
                "group": group,
                "n": len(group_rows),
                "q6_plannable_truth": len(q6_truth),
                "eligible_rows": eligible_truth,
                "eligible_no_q6_rows": eligible_no_q6,
                "q6_plannable_misses_before": before_misses,
                "q6_plannable_misses_after": after_misses,
                "q6_plannable_miss_improvement": before_misses - after_misses,
                "q6_plannable_coverage_after": round(
                    1.0 - after_misses / len(q6_truth),
                    4,
                ),
                "floor_median": _round(statistics.median(floors))
                if floors
                else None,
            }
        )
    return sorted(
        out,
        key=lambda row: (
            int(row["q6_plannable_miss_improvement"]),
            -int(row["eligible_no_q6_rows"]),
            int(row["q6_plannable_truth"]),
        ),
        reverse=True,
    )[:12]


def _q6_low_space_residual_floor_group_summary(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    floor_ratio: float,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_group_key(row, keys), []).append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        q6_truth = [
            row for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) > 0
            and row.get("v2_q6_decision_value_p90") is not None
        ]
        if not q6_truth:
            continue
        before_misses = sum(
            1 for row in q6_truth
            if row.get("q6_plannable_p90_misses_truth")
        )
        after_misses = 0
        eligible_truth = 0
        floors: list[int] = []
        for row in q6_truth:
            q6_p90 = int(row.get("v2_q6_decision_value_p90") or 0)
            floor_value = _q6_low_space_residual_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            if floor_value is not None:
                eligible_truth += 1
                floors.append(floor_value)
                q6_p90 = max(q6_p90, floor_value)
            if q6_p90 < int(row.get("final_q6_decision_value") or 0):
                after_misses += 1
        eligible_no_q6 = sum(
            1 for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) <= 0
            and _q6_low_space_residual_floor_value(
                row,
                floor_ratio=floor_ratio,
            )
            is not None
        )
        out.append(
            {
                "group": group,
                "n": len(group_rows),
                "q6_plannable_truth": len(q6_truth),
                "eligible_rows": eligible_truth,
                "eligible_no_q6_rows": eligible_no_q6,
                "q6_plannable_misses_before": before_misses,
                "q6_plannable_misses_after": after_misses,
                "q6_plannable_miss_improvement": before_misses - after_misses,
                "net_improvement": before_misses - after_misses - eligible_no_q6,
                "q6_plannable_coverage_after": round(
                    1.0 - after_misses / len(q6_truth),
                    4,
                ),
                "floor_median": _round(statistics.median(floors))
                if floors
                else None,
            }
        )
    return sorted(
        out,
        key=lambda row: (
            int(row["net_improvement"]),
            int(row["q6_plannable_miss_improvement"]),
            -int(row["eligible_no_q6_rows"]),
            int(row["q6_plannable_truth"]),
        ),
        reverse=True,
    )[:12]


def _q6_count_cell_prior_floor_gate_candidates(
    rows: list[dict[str, Any]],
    *,
    floor_ratio: float,
    keys: tuple[str, ...] = ("hero", "map_family"),
    min_q6_truth: int = 1,
) -> list[dict[str, Any]]:
    candidates = []
    for row in _q6_count_cell_prior_floor_group_summary(
        rows,
        keys,
        floor_ratio=floor_ratio,
    ):
        improvement = int(row["q6_plannable_miss_improvement"])
        false_positive_proxy = int(row["eligible_no_q6_rows"])
        if (
            improvement <= 0
            or improvement <= false_positive_proxy
            or int(row["q6_plannable_truth"]) < min_q6_truth
        ):
            continue
        candidates.append(
            {
                **row,
                "net_improvement": improvement - false_positive_proxy,
            }
        )
    return sorted(
        candidates,
        key=lambda row: (
            int(row["net_improvement"]),
            int(row["q6_plannable_miss_improvement"]),
            int(row["q6_plannable_truth"]),
        ),
        reverse=True,
    )


def _q6_group_summary(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_group_key(row, keys), []).append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        q6_truth = [row for row in group_rows if int(row.get("final_q6_value") or 0) > 0]
        if not q6_truth:
            continue
        q6_misses = [row for row in q6_truth if row.get("q6_p90_misses_truth")]
        under_by = [
            int(row["v2_q6_value_p90_under_by"])
            for row in q6_misses
            if row.get("v2_q6_value_p90_under_by") is not None
        ]
        q6_p90_errors = [
            int(row["v2_q6_value_p90_error"])
            for row in q6_truth
            if row.get("v2_q6_value_p90_error") is not None
        ]
        out.append(
            {
                "group": group,
                "n": len(group_rows),
                "q6_truth": len(q6_truth),
                "q6_p90_misses_truth": len(q6_misses),
                "q6_false_low_risk": sum(
                    1 for row in q6_truth if row.get("q6_false_low_risk")
                ),
                "q6_below_drop_prior": sum(
                    1 for row in q6_truth if row.get("q6_below_drop_prior")
                ),
                "q6_miss_rate": round(len(q6_misses) / len(q6_truth), 4),
                "median_q6_under_by": (
                    _round(statistics.median(under_by)) if under_by else None
                ),
                "median_q6_p90_error": (
                    _round(statistics.median(q6_p90_errors))
                    if q6_p90_errors
                    else None
                ),
                "zero_match": sum(1 for row in group_rows if not row.get("v2_matched")),
                "layout_conflict": sum(
                    1 for row in group_rows if row.get("layout_conflict")
                ),
            }
        )
    return sorted(
        out,
        key=lambda row: (
            int(row["q6_p90_misses_truth"]),
            int(row["median_q6_under_by"] or 0),
            int(row["q6_truth"]),
        ),
        reverse=True,
    )


def _q6_plannable_group_summary(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_group_key(row, keys), []).append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        q6_truth = [
            row for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) > 0
        ]
        if not q6_truth:
            continue
        q6_misses = [
            row for row in q6_truth
            if row.get("q6_plannable_p90_misses_truth")
        ]
        under_by = [
            int(row["v2_q6_decision_value_p90_under_by"])
            for row in q6_misses
            if row.get("v2_q6_decision_value_p90_under_by") is not None
        ]
        trusted_footprints = [
            int(row["trusted_footprint_count"])
            for row in group_rows
            if row.get("trusted_footprint_count") is not None
        ]
        occupied_cells = [
            int(row["footprint_occupied_cells"])
            for row in group_rows
            if row.get("footprint_occupied_cells") is not None
        ]
        remaining_cells_p50 = [
            int(row["v2_remaining_cells_after_layout_p50"])
            for row in group_rows
            if row.get("v2_remaining_cells_after_layout_p50") is not None
        ]
        q6_space_pressure_p90 = [
            float(row["v2_q6_space_pressure_p90"])
            for row in group_rows
            if row.get("v2_q6_space_pressure_p90") is not None
        ]
        q6_space_overflow_rates = [
            float(row["v2_q6_space_overflow_rate"])
            for row in group_rows
            if row.get("v2_q6_space_overflow_rate") is not None
        ]
        out.append(
            {
                "group": group,
                "n": len(group_rows),
                "q6_plannable_truth": len(q6_truth),
                "q6_plannable_p90_misses_truth": len(q6_misses),
                "q6_plannable_miss_rate": round(len(q6_misses) / len(q6_truth), 4),
                "median_q6_plannable_under_by": (
                    _round(statistics.median(under_by)) if under_by else None
                ),
                "q6_tail_event_files": sum(
                    1 for row in group_rows
                    if int(row.get("final_q6_trimmed_tail_value") or 0) > 0
                ),
                "zero_match": sum(1 for row in group_rows if not row.get("v2_matched")),
                "layout_conflict": sum(
                    1 for row in group_rows if row.get("layout_conflict")
                ),
                "q6_count_under": sum(
                    1 for row in q6_misses
                    if int(row.get("v2_q6_count_p90_under_by") or 0) > 0
                ),
                "q6_cells_under": sum(
                    1 for row in q6_misses
                    if int(row.get("v2_q6_cells_p90_under_by") or 0) > 0
                ),
                "q6_count_below_prior": sum(
                    1 for row in q6_misses
                    if float(row.get("v2_q6_count_p90_under_prior_by") or 0) > 0
                ),
                "q6_cells_below_prior": sum(
                    1 for row in q6_misses
                    if float(row.get("v2_q6_cells_p90_under_prior_by") or 0) > 0
                ),
                "trusted_footprint_median": (
                    _round(statistics.median(trusted_footprints))
                    if trusted_footprints
                    else None
                ),
                "footprint_occupied_cells_median": (
                    _round(statistics.median(occupied_cells))
                    if occupied_cells
                    else None
                ),
                "remaining_cells_after_layout_p50_median": (
                    _round(statistics.median(remaining_cells_p50))
                    if remaining_cells_p50
                    else None
                ),
                "q6_space_pressure_p90_median": (
                    _round_float(statistics.median(q6_space_pressure_p90), 3)
                    if q6_space_pressure_p90
                    else None
                ),
                "q6_space_overflow_rate_mean": (
                    _round_float(statistics.mean(q6_space_overflow_rates), 4)
                    if q6_space_overflow_rates
                    else None
                ),
            }
        )
    return sorted(
        out,
        key=lambda row: (
            int(row["q6_plannable_p90_misses_truth"]),
            int(row["median_q6_plannable_under_by"] or 0),
            int(row["q6_plannable_truth"]),
        ),
        reverse=True,
    )


def _q6_calibration_priority(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    primary = _q6_group_summary(rows, ("hero", "map_family"))
    return [
        {
            **row,
            "priority_reason": (
                "q6_p90_undercoverage"
                if row["q6_p90_misses_truth"]
                else "low_q6_truth_coverage"
            ),
        }
        for row in primary
        if row["q6_p90_misses_truth"] or row["q6_truth"] < 10
    ][:10]


def _q6_plannable_calibration_priority(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    primary = _q6_plannable_group_summary(rows, ("hero", "map_family"))
    return [
        {
            **row,
            "priority_reason": "plannable_q6_p90_undercoverage",
        }
        for row in primary
        if row["q6_plannable_p90_misses_truth"]
    ][:10]


def _float_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        out.append(float(value))
    return out


def _median_float_value(
    rows: list[dict[str, Any]],
    key: str,
    *,
    digits: int = 3,
) -> float | None:
    values = _float_values(rows, key)
    return _round_float(statistics.median(values), digits) if values else None


def _mean_float_value(
    rows: list[dict[str, Any]],
    key: str,
    *,
    digits: int = 4,
) -> float | None:
    values = _float_values(rows, key)
    return _round_float(statistics.mean(values), digits) if values else None


def _q6_space_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_space_pressure_p90") is not None
    ]
    q6_misses = [
        row for row in q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    q6_covered = [
        row for row in q6_truth
        if not row.get("q6_plannable_p90_misses_truth")
    ]
    low_pressure_misses = [
        row for row in q6_misses
        if float(row.get("v2_q6_space_pressure_p90") or 0) < 0.50
        and float(row.get("v2_q6_space_overflow_rate") or 0) <= 0
    ]
    high_pressure_misses = [
        row for row in q6_misses
        if float(row.get("v2_q6_space_pressure_p90") or 0) >= 1.00
        or float(row.get("v2_q6_space_overflow_rate") or 0) > 0
    ]
    miss_count = len(q6_misses)
    low_pressure_rate = (
        round(len(low_pressure_misses) / miss_count, 4) if miss_count else None
    )
    high_pressure_rate = (
        round(len(high_pressure_misses) / miss_count, 4) if miss_count else None
    )
    if miss_count == 0:
        recommendation = "q6_space_not_current_bottleneck"
    elif low_pressure_rate is not None and low_pressure_rate >= 0.60:
        recommendation = "residual_q6_count_cell_sampler"
    elif high_pressure_rate is not None and high_pressure_rate >= 0.30:
        recommendation = "space_feasibility_weight"
    else:
        recommendation = "mixed_residual_and_space_audit"
    return {
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_plannable_miss_rows": miss_count,
        "q6_plannable_covered_rows": len(q6_covered),
        "miss_q6_space_pressure_p90_median": _median_float_value(
            q6_misses,
            "v2_q6_space_pressure_p90",
        ),
        "covered_q6_space_pressure_p90_median": _median_float_value(
            q6_covered,
            "v2_q6_space_pressure_p90",
        ),
        "miss_q6_space_overflow_rate_mean": _mean_float_value(
            q6_misses,
            "v2_q6_space_overflow_rate",
        ),
        "covered_q6_space_overflow_rate_mean": _mean_float_value(
            q6_covered,
            "v2_q6_space_overflow_rate",
        ),
        "low_space_pressure_miss_rows": len(low_pressure_misses),
        "low_space_pressure_miss_rate": low_pressure_rate,
        "high_space_pressure_miss_rows": len(high_pressure_misses),
        "high_space_pressure_miss_rate": high_pressure_rate,
        "recommended_next": recommendation,
        "groups": {
            "hero_map_profile": _q6_space_group_summary(
                rows,
                ("hero", "map_family", "evidence_profile_key"),
            ),
            "evidence_profile": _q6_space_group_summary(
                rows,
                ("evidence_profile_key",),
            ),
        },
    }


def _q6_space_group_summary(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_group_key(row, keys), []).append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        q6_misses = [
            row for row in group_rows
            if row.get("q6_plannable_p90_misses_truth")
            and row.get("v2_q6_space_pressure_p90") is not None
        ]
        if not q6_misses:
            continue
        low_pressure_misses = [
            row for row in q6_misses
            if float(row.get("v2_q6_space_pressure_p90") or 0) < 0.50
            and float(row.get("v2_q6_space_overflow_rate") or 0) <= 0
        ]
        high_pressure_misses = [
            row for row in q6_misses
            if float(row.get("v2_q6_space_pressure_p90") or 0) >= 1.00
            or float(row.get("v2_q6_space_overflow_rate") or 0) > 0
        ]
        under_by = [
            int(row["v2_q6_decision_value_p90_under_by"])
            for row in q6_misses
            if row.get("v2_q6_decision_value_p90_under_by") is not None
        ]
        recommendation = (
            "residual_q6_count_cell_sampler"
            if len(low_pressure_misses) >= len(high_pressure_misses)
            else "space_feasibility_weight"
        )
        out.append(
            {
                "group": group,
                "q6_plannable_miss_rows": len(q6_misses),
                "low_space_pressure_miss_rows": len(low_pressure_misses),
                "high_space_pressure_miss_rows": len(high_pressure_misses),
                "miss_q6_space_pressure_p90_median": _median_float_value(
                    q6_misses,
                    "v2_q6_space_pressure_p90",
                ),
                "miss_q6_space_overflow_rate_mean": _mean_float_value(
                    q6_misses,
                    "v2_q6_space_overflow_rate",
                ),
                "median_q6_plannable_under_by": (
                    _round(statistics.median(under_by)) if under_by else None
                ),
                "recommended_next": recommendation,
            }
        )
    return sorted(
        out,
        key=lambda row: (
            int(row["q6_plannable_miss_rows"]),
            int(row["median_q6_plannable_under_by"] or 0),
        ),
        reverse=True,
    )[:12]


def _q6_residual_boost_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valued = [
        row for row in rows
        if row.get("v2_decision_value_p50_error") is not None
    ]
    active = [
        row for row in rows
        if float(row.get("q6_residual_boost") or 1.0) > 1.0
    ]
    active_no_q6 = [
        row for row in active
        if int(row.get("final_q6_decision_value") or 0) <= 0
    ]
    active_no_q6_positive = [
        row for row in active_no_q6
        if int(row.get("v2_q6_decision_value_p90") or 0) > 0
    ]
    q6_truth = [
        row for row in valued
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_misses = [
        row for row in q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    return {
        "boost_values": sorted(
            {
                float(row.get("q6_residual_boost") or 1.0)
                for row in rows
            }
        ),
        "gate_values": sorted(
            {
                str(row.get("q6_residual_boost_gate") or "none")
                for row in rows
            }
        ),
        "active_rows": len(active),
        "active_rate": round(len(active) / len(rows), 4) if rows else None,
        "active_no_q6_rows": len(active_no_q6),
        "active_no_q6_p90_positive": len(active_no_q6_positive),
        "active_no_q6_p90_positive_rate": (
            round(len(active_no_q6_positive) / len(active_no_q6), 4)
            if active_no_q6
            else None
        ),
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_plannable_miss_rows": len(q6_misses),
        "q6_plannable_value_p90_coverage": (
            round(1.0 - len(q6_misses) / len(q6_truth), 4)
            if q6_truth
            else None
        ),
        "groups": {
            "hero_map_profile": _q6_residual_boost_group_summary(
                rows,
                ("hero", "map_family", "evidence_profile_key"),
            ),
            "hero_map_family": _q6_residual_boost_group_summary(
                rows,
                ("hero", "map_family"),
            ),
        },
    }


def _q6_residual_prior_floor_sampler_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    active = [
        row for row in rows
        if float(row.get("q6_residual_prior_floor_ratio") or 0.0) > 0.0
    ]
    active_no_q6 = [
        row for row in active
        if int(row.get("final_q6_decision_value") or 0) <= 0
    ]
    active_no_q6_positive = [
        row for row in active_no_q6
        if int(row.get("v2_q6_decision_value_p90") or 0) > 0
    ]
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_misses = [
        row for row in q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    return {
        "floor_ratios": sorted(
            {
                float(row.get("q6_residual_prior_floor_ratio") or 0.0)
                for row in rows
            }
        ),
        "cell_floor_ratios": sorted(
            {
                float(row.get("q6_residual_prior_cell_floor_ratio") or 0.0)
                for row in rows
            }
        ),
        "gate_values": sorted(
            {
                str(row.get("q6_residual_prior_floor_gate") or "none")
                for row in rows
            }
        ),
        "active_rows": len(active),
        "active_rate": round(len(active) / len(rows), 4) if rows else None,
        "active_no_q6_rows": len(active_no_q6),
        "active_no_q6_p90_positive": len(active_no_q6_positive),
        "active_no_q6_p90_positive_rate": (
            round(len(active_no_q6_positive) / len(active_no_q6), 4)
            if active_no_q6
            else None
        ),
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_plannable_miss_rows": len(q6_misses),
        "q6_plannable_value_p90_coverage": (
            round(1.0 - len(q6_misses) / len(q6_truth), 4)
            if q6_truth
            else None
        ),
    }


def _q6_residual_value_sampler_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    active = [
        row for row in rows
        if float(row.get("q6_residual_value_power") or 0.0) > 0.0
    ]
    active_no_q6 = [
        row for row in active
        if int(row.get("final_q6_decision_value") or 0) <= 0
    ]
    active_no_q6_positive = [
        row for row in active_no_q6
        if int(row.get("v2_q6_decision_value_p90") or 0) > 0
    ]
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_misses = [
        row for row in q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    return {
        "value_powers": sorted(
            {
                float(row.get("q6_residual_value_power") or 0.0)
                for row in rows
            }
        ),
        "gate_values": sorted(
            {
                str(row.get("q6_residual_value_gate") or "none")
                for row in rows
            }
        ),
        "active_rows": len(active),
        "active_rate": round(len(active) / len(rows), 4) if rows else None,
        "active_no_q6_rows": len(active_no_q6),
        "active_no_q6_p90_positive": len(active_no_q6_positive),
        "active_no_q6_p90_positive_rate": (
            round(len(active_no_q6_positive) / len(active_no_q6), 4)
            if active_no_q6
            else None
        ),
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_plannable_miss_rows": len(q6_misses),
        "q6_plannable_value_p90_coverage": (
            round(1.0 - len(q6_misses) / len(q6_truth), 4)
            if q6_truth
            else None
        ),
    }


def _q6_conditional_target_sampler_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    active = [
        row for row in rows
        if float(row.get("q6_conditional_target_count") or 0.0) > 0.0
        or float(row.get("q6_conditional_target_cells") or 0.0) > 0.0
    ]
    active_no_q6 = [
        row for row in active
        if int(row.get("final_q6_decision_value") or 0) <= 0
    ]
    active_no_q6_positive = [
        row for row in active_no_q6
        if int(row.get("v2_q6_decision_value_p90") or 0) > 0
    ]
    q6_truth = [
        row for row in rows
        if int(row.get("final_q6_decision_value") or 0) > 0
        and row.get("v2_q6_decision_value_p90") is not None
    ]
    q6_misses = [
        row for row in q6_truth
        if row.get("q6_plannable_p90_misses_truth")
    ]
    return {
        "target_counts": sorted(
            {
                float(row.get("q6_conditional_target_count") or 0.0)
                for row in rows
            }
        ),
        "target_cells": sorted(
            {
                float(row.get("q6_conditional_target_cells") or 0.0)
                for row in rows
            }
        ),
        "value_powers": sorted(
            {
                float(row.get("q6_conditional_value_power") or 0.0)
                for row in rows
            }
        ),
        "gate_values": sorted(
            {
                str(row.get("q6_conditional_target_gate") or "none")
                for row in rows
            }
        ),
        "active_rows": len(active),
        "active_rate": round(len(active) / len(rows), 4) if rows else None,
        "active_no_q6_rows": len(active_no_q6),
        "active_no_q6_p90_positive": len(active_no_q6_positive),
        "active_no_q6_p90_positive_rate": (
            round(len(active_no_q6_positive) / len(active_no_q6), 4)
            if active_no_q6
            else None
        ),
        "q6_plannable_truth_rows": len(q6_truth),
        "q6_plannable_miss_rows": len(q6_misses),
        "q6_plannable_value_p90_coverage": (
            round(1.0 - len(q6_misses) / len(q6_truth), 4)
            if q6_truth
            else None
        ),
        "groups": {
            "hero_map_profile": _q6_plannable_group_summary(
                active,
                ("hero", "map_family", "evidence_profile_key"),
            ),
        },
    }


def _q6_residual_boost_group_summary(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_group_key(row, keys), []).append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        active = [
            row for row in group_rows
            if float(row.get("q6_residual_boost") or 1.0) > 1.0
        ]
        if not active:
            continue
        active_no_q6 = [
            row for row in active
            if int(row.get("final_q6_decision_value") or 0) <= 0
        ]
        q6_truth = [
            row for row in group_rows
            if int(row.get("final_q6_decision_value") or 0) > 0
            and row.get("v2_q6_decision_value_p90") is not None
        ]
        q6_misses = [
            row for row in q6_truth
            if row.get("q6_plannable_p90_misses_truth")
        ]
        out.append(
            {
                "group": group,
                "n": len(group_rows),
                "active_rows": len(active),
                "active_no_q6_rows": len(active_no_q6),
                "q6_plannable_truth": len(q6_truth),
                "q6_plannable_misses": len(q6_misses),
                "q6_plannable_coverage": (
                    round(1.0 - len(q6_misses) / len(q6_truth), 4)
                    if q6_truth
                    else None
                ),
                "boost_values": sorted(
                    {
                        float(row.get("q6_residual_boost") or 1.0)
                        for row in active
                    }
                ),
            }
        )
    return sorted(
        out,
        key=lambda row: (
            int(row["active_rows"]),
            int(row["q6_plannable_truth"]),
            -int(row["active_no_q6_rows"]),
        ),
        reverse=True,
    )[:12]


def _q6_actionable_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    scopes = (
        ("hero_map_family", ("hero", "map_family"), 10),
        ("hero_map_profile", ("hero", "map_family", "evidence_profile_key"), 10),
        ("information_density", ("information_density_band",), 10),
        ("evidence_profile", ("evidence_profile_key",), 10),
    )
    for scope, keys, min_truth in scopes:
        for row in _q6_plannable_group_summary(rows, keys):
            truth = int(row["q6_plannable_truth"])
            misses = int(row["q6_plannable_p90_misses_truth"])
            if truth < min_truth or misses <= 0:
                continue
            under_by = int(row["median_q6_plannable_under_by"] or 0)
            priority_score = round(misses * max(1.0, under_by / 100_000), 2)
            candidates.append(
                {
                    "scope": scope,
                    "group": row["group"],
                    "q6_plannable_truth": truth,
                    "q6_plannable_misses": misses,
                    "q6_plannable_miss_rate": row["q6_plannable_miss_rate"],
                    "median_q6_plannable_under_by": (
                        row["median_q6_plannable_under_by"]
                    ),
                    "layout_conflict": row["layout_conflict"],
                    "zero_match": row["zero_match"],
                    "priority_score": priority_score,
                    "recommended_next": _q6_action_recommendation(
                        str(row["group"]),
                        layout_conflict=int(row["layout_conflict"]),
                        q6_space_pressure_p90_median=row.get(
                            "q6_space_pressure_p90_median"
                        ),
                        q6_space_overflow_rate_mean=row.get(
                            "q6_space_overflow_rate_mean"
                        ),
                    ),
                }
            )
    return sorted(
        candidates,
        key=lambda row: (
            float(row["priority_score"]),
            int(row["q6_plannable_misses"]),
            int(row["q6_plannable_truth"]),
        ),
        reverse=True,
    )[:12]


def _q6_action_recommendation(
    group: str,
    *,
    layout_conflict: int,
    q6_space_pressure_p90_median: Any = None,
    q6_space_overflow_rate_mean: Any = None,
) -> str:
    low_space_pressure = (
        q6_space_pressure_p90_median is not None
        and float(q6_space_pressure_p90_median) < 0.50
        and float(q6_space_overflow_rate_mean or 0) < 0.10
    )
    if low_space_pressure and "shipwreck" in group and "shape+layout" in group:
        return "shipwreck_shape_residual_sampler"
    if low_space_pressure and "shape+layout" in group:
        return "residual_q6_count_cell_sampler"
    if "shipwreck" in group and "shape+layout" in group:
        return "shipwreck_shape_space_residual"
    if "shipwreck" in group and "tool:category" in group:
        return "shipwreck_category_shape_residual"
    if "shipwreck" in group:
        return "shipwreck_q6_count_cell_gate"
    if layout_conflict > 0 and "information_density_band=high" in group:
        return "high_density_layout_conflict_audit"
    if "public:random_avg" in group:
        return "random_avg_likelihood_calibration"
    if "shape+layout" in group:
        return "remaining_space_feasibility"
    return "q6_residual_diagnostics"


def _collection_readiness(
    rows: list[dict[str, Any]],
    *,
    target_per_hero_family: int,
    hidden_target_per_hero: int,
    hidden_target_by_hero: dict[str, int] | None = None,
) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        hero = str(row.get("hero") or "unknown")
        family = str(row.get("map_family") or "unknown")
        groups.setdefault((hero, family), []).append(row)

    rows_out: list[dict[str, Any]] = []
    for hero in ("aisha", "ethan"):
        for family in ("villa", "shipwreck", "hidden"):
            target = (
                int((hidden_target_by_hero or {}).get(hero, hidden_target_per_hero))
                if family == "hidden"
                else target_per_hero_family
            )
            count = len(groups.get((hero, family), ()))
            rows_out.append(
                {
                    "hero": hero,
                    "map_family": family,
                    "n": count,
                    "target": target,
                    "needed": max(0, target - count),
                    "ready": count >= target,
                }
            )
    missing = sum(row["needed"] for row in rows_out)
    return {
        "target_per_hero_family": target_per_hero_family,
        "hidden_target_per_hero": hidden_target_per_hero,
        "hidden_target_by_hero": dict(hidden_target_by_hero or {}),
        "ready": missing == 0,
        "total_needed": missing,
        "groups": rows_out,
        "priority_needs": [
            row for row in rows_out
            if row["needed"] > 0
        ],
    }


def _bid_gap_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)
    summary: list[dict[str, Any]] = []
    for value, group_rows in sorted(groups.items()):
        ratios = [
            float(row["highest_bid_over_final"])
            for row in group_rows
            if row.get("highest_bid_over_final") is not None
        ]
        gaps = [
            int(row["highest_bid_minus_final"])
            for row in group_rows
            if row.get("highest_bid_minus_final") is not None
        ]
        summary.append(
            {
                key: value,
                "n": len(group_rows),
                "bid_rows": len(ratios),
                "highest_bid_over_final_median": (
                    round(statistics.median(ratios), 3) if ratios else None
                ),
                "highest_bid_over_final_p75": (
                    round(statistics.quantiles(ratios, n=4)[2], 3)
                    if len(ratios) >= 4
                    else None
                ),
                "highest_bid_over_final_rate": (
                    round(
                        statistics.mean(1.0 if ratio > 1.0 else 0.0 for ratio in ratios),
                        4,
                    )
                    if ratios
                    else None
                ),
                "highest_bid_minus_final_median": (
                    _round(statistics.median(gaps)) if gaps else None
                ),
            }
        )
    return summary


def _group_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)
    summary: list[dict[str, Any]] = []
    for value, group_rows in sorted(groups.items()):
        p50_abs = [
            abs(int(row["v2_value_p50_error"]))
            for row in group_rows
            if row.get("v2_value_p50_error") is not None
        ]
        decision_p50_abs = [
            abs(int(row["v2_decision_value_p50_error"]))
            for row in group_rows
            if row.get("v2_decision_value_p50_error") is not None
        ]
        p90_abs = [
            abs(int(row["v2_value_p90_error"]))
            for row in group_rows
            if row.get("v2_value_p90_error") is not None
        ]
        p90_rows = [
            row for row in group_rows
            if row.get("v2_value_p90_covers_final") is not None
        ]
        summary.append(
            {
                key: value,
                "n": len(group_rows),
                "zero_match": sum(1 for row in group_rows if not row.get("v2_matched")),
                "value_mae": _round(statistics.mean(p50_abs)) if p50_abs else None,
                "value_median_abs_error": (
                    _round(statistics.median(p50_abs)) if p50_abs else None
                ),
                "decision_value_mae": (
                    _round(statistics.mean(decision_p50_abs))
                    if decision_p50_abs
                    else None
                ),
                "value_p90_mae": (
                    _round(statistics.mean(p90_abs)) if p90_abs else None
                ),
                "value_p90_coverage": (
                    round(
                        statistics.mean(
                            1.0 if row["v2_value_p90_covers_final"] else 0.0
                            for row in p90_rows
                        ),
                        4,
                    )
                    if p90_rows
                    else None
                ),
                "mean_match_rate": round(
                    statistics.mean(row["v2_match_rate"] for row in group_rows),
                    4,
                ),
            }
        )
    return summary


def _write_csv(rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)


def _expand_cli_paths(raw_paths: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in raw_paths:
        matches = sorted(glob.glob(raw))
        if not matches:
            matches = [raw]
        for match in matches:
            path = Path(match)
            if path.is_dir():
                paths.extend(sorted(path.glob("*.json")))
            else:
                paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Fatbeans JSON captures with v2 posterior.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "JSON files or directories. Defaults to repo-local "
            "data/samples/fatbeans; pass external capture dirs explicitly."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("summary", "jsonl", "csv"),
        default="summary",
    )
    parser.add_argument(
        "--activity",
        action="store_true",
        help="Use the explicit 20260702 shipwreck activity pool instead of clean-eval",
    )
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cells-tol", type=int, default=8)
    parser.add_argument("--count-tol", type=int, default=3)
    parser.add_argument(
        "--q6-residual-floor-ratio",
        type=float,
        default=0.0,
        help=(
            "Offline what-if only: floor q6 P90 for q6_below_drop_prior rows "
            "to this fraction of q6 prior expected value in the summary."
        ),
    )
    parser.add_argument(
        "--q6-residual-boost",
        type=float,
        default=1.0,
        help=(
            "Offline sampler experiment only: multiply q6 candidate weights "
            "during residual sampling. Default 1.0 keeps the production sampler."
        ),
    )
    parser.add_argument(
        "--q6-residual-boost-gate",
        choices=(
            "all",
            "shipwreck_profile_v1",
            "aisha_shipwreck_bottom_v1",
            "aisha_shipwreck_deep_v1",
        ),
        default="all",
        help=(
            "Offline sampler experiment gate for --q6-residual-boost. "
            "shipwreck_profile_v1 limits the boost to positive-net "
            "hero+shipwreck+evidence-profile groups; "
            "aisha_shipwreck_bottom_v1 additionally requires bottom-row risk; "
            "aisha_shipwreck_deep_v1 uses a wider deep-layout threshold."
        ),
    )
    parser.add_argument(
        "--q6-residual-prior-floor-ratio",
        type=float,
        default=0.0,
        help=(
            "Offline sampler experiment only: pre-sample q6 residual items "
            "until q6 count/cells reach this fraction of the q6 Drop prior. "
            "Default 0.0 keeps the production sampler."
        ),
    )
    parser.add_argument(
        "--q6-residual-prior-cell-floor-ratio",
        type=float,
        default=0.0,
        help=(
            "Offline sampler experiment only: override the q6 prior cells "
            "floor ratio while keeping the count floor ratio unchanged. "
            "Default 0.0 reuses --q6-residual-prior-floor-ratio."
        ),
    )
    parser.add_argument(
        "--q6-residual-prior-floor-gate",
        choices=(
            "all",
            "shipwreck_profile_v1",
            "aisha_shipwreck_profile_v1",
            "aisha_shipwreck_bottom_v1",
            "aisha_shipwreck_deep_v1",
            "aisha_shipwreck_deep12_v1",
            "aisha_shipwreck_deep11_v1",
            "aisha_hidden_v1",
            "aisha_villa_shape_layout_v1",
            "ethan_villa_random_avg_v1",
            "aisha_deep_or_hidden_v1",
        ),
        default="all",
        help=(
            "Offline sampler experiment gate for --q6-residual-prior-floor-ratio."
        ),
    )
    parser.add_argument(
        "--q6-residual-value-power",
        type=float,
        default=0.0,
        help=(
            "Offline sampler experiment only: tilt q6 residual candidate "
            "weights by value**power inside the q6 candidate set. Default 0.0."
        ),
    )
    parser.add_argument(
        "--q6-residual-value-gate",
        choices=(
            "all",
            "aisha_shipwreck_profile_v1",
            "aisha_shipwreck_deep_v1",
            "ethan_villa_random_avg_v1",
        ),
        default="all",
        help="Offline sampler experiment gate for --q6-residual-value-power.",
    )
    parser.add_argument(
        "--q6-conditional-target-count",
        type=float,
        default=0.0,
        help=(
            "Offline sampler experiment only: q6 count target for conditional "
            "count/cells sampling. Default 0.0 disables it."
        ),
    )
    parser.add_argument(
        "--q6-conditional-target-cells",
        type=float,
        default=0.0,
        help=(
            "Offline sampler experiment only: q6 cells target for conditional "
            "count/cells sampling. Default 0.0 disables it."
        ),
    )
    parser.add_argument(
        "--q6-conditional-target-gate",
        choices=(
            "none",
            "all",
            "ethan_shipwreck_layout_v1",
        ),
        default="none",
        help="Offline sampler experiment gate for q6 conditional targets.",
    )
    parser.add_argument(
        "--q6-conditional-value-power",
        type=float,
        default=0.0,
        help=(
            "Offline sampler experiment only: value tilt inside q6 candidates "
            "for conditional target sampling. Default 0.0 keeps no extra tilt."
        ),
    )
    parser.add_argument(
        "--random-sample-avg-profile-floor",
        type=float,
        default=RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR,
        help=(
            "Offline routing experiment: random N-item average values below "
            "this floor are kept in logs but ignored by evidence-profile gates."
        ),
    )
    parser.add_argument(
        "--combo-presolve",
        default=str(_DEFAULT_COMBO_PRESOLVE_PATH),
        help=(
            "Optional q4/q5/q6 count/cells presolve JSON. If absent, "
            "presolve diagnostics are skipped."
        ),
    )
    args = parser.parse_args()

    paths: list[Path] = []
    if args.paths and args.activity:
        parser.error("--activity cannot be combined with explicit paths")
    if args.paths:
        paths = _expand_cli_paths(args.paths)
        selector = "explicit_paths"
    elif args.activity:
        paths = [Path(path) for path in activity_eval_files()]
        selector = "activity_20260702:all"
    else:
        paths = _default_paths()
        selector = "clean_eval:seven_heroes"
    selected_paths = list(_iter_unique(paths))
    metadata = sample_selection_metadata(selected_paths, selector=selector)
    metadata_fields = selection_row_fields(metadata)
    print("run_metadata=" + json.dumps(metadata, ensure_ascii=False, sort_keys=True), file=sys.stderr)

    tables = load_monitor_tables()
    combo_presolve = None
    combo_presolve_path = Path(args.combo_presolve) if args.combo_presolve else None
    if combo_presolve_path is not None and combo_presolve_path.exists():
        combo_presolve = load_quality_combo_presolve(combo_presolve_path)
    rows = [
        evaluate_path(
            path,
            tables=tables,
            n_trials=args.trials,
            seed=args.seed,
            cells_tol=args.cells_tol,
            count_tol=args.count_tol,
            combo_presolve=combo_presolve,
            q6_residual_boost=args.q6_residual_boost,
            q6_residual_boost_gate=args.q6_residual_boost_gate,
            q6_residual_prior_floor_ratio=args.q6_residual_prior_floor_ratio,
            q6_residual_prior_cell_floor_ratio=(
                args.q6_residual_prior_cell_floor_ratio
            ),
            q6_residual_prior_floor_gate=args.q6_residual_prior_floor_gate,
            q6_residual_value_power=args.q6_residual_value_power,
            q6_residual_value_gate=args.q6_residual_value_gate,
            q6_conditional_target_count=args.q6_conditional_target_count,
            q6_conditional_target_cells=args.q6_conditional_target_cells,
            q6_conditional_target_gate=args.q6_conditional_target_gate,
            q6_conditional_value_power=args.q6_conditional_value_power,
            random_sample_avg_profile_floor=args.random_sample_avg_profile_floor,
        )
        for path in selected_paths
    ]
    for row in rows:
        row.update(metadata_fields)

    if args.format == "jsonl":
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    elif args.format == "csv":
        _write_csv(rows)
    else:
        summary = _summary(
            rows,
            q6_residual_floor_ratio=args.q6_residual_floor_ratio,
        )
        summary["run_metadata"] = metadata
        print(
            json.dumps(
                summary,
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
