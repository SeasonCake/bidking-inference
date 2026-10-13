"""Export compact UI-contract review rows for live readiness checks."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.live.monitor import (  # noqa: E402
    build_monitor_artifact_from_file,
    load_monitor_tables,
)
from bidking_lab.runtime import ui_contract_from_artifact  # noqa: E402


DEFAULT_OUT_DIR = ROOT / "data" / "review" / "ui_contract"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是"}
    return bool(value)


def _compact_mapping(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    parts = [
        f"{key}:{item}"
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        if item not in (None, "", 0)
    ]
    return ";".join(parts)


def _compact_list(values: Iterable[Any]) -> str:
    return ";".join(str(value) for value in values if value not in (None, ""))


def _first_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, list | tuple) and value:
        first = value[0]
        if isinstance(first, Mapping):
            return first
    return {}


def _match_counts(value: Any) -> tuple[int | None, int | None]:
    text = _text(value).strip()
    if "/" not in text:
        return None, None
    left, right = text.split("/", 1)
    matched = _int(left.strip())
    total = _int(right.strip())
    return matched, total


def _range_ints(value: Any) -> list[int]:
    text = _text(value)
    if not text:
        return []
    values: list[int] = []
    for part in text.split("/"):
        parsed = _int(part.strip().replace(",", ""))
        if parsed is not None:
            values.append(parsed)
    return values


def _range_p90(value: Any) -> int | None:
    values = _range_ints(value)
    if len(values) >= 3:
        return values[2]
    return None


def _q6_below_prior_review(
    posterior: Mapping[str, Any],
    truth_q6: Mapping[str, Any],
    *,
    below_drop_prior: bool,
) -> dict[str, Any]:
    if not below_drop_prior:
        return {
            "class": "",
            "under_by": None,
            "actionable": False,
        }
    raw_truth_value = _int(truth_q6.get("value"))
    decision_truth_value = _int(truth_q6.get("decision_value"))
    truth_value = (
        decision_truth_value
        if decision_truth_value is not None
        else raw_truth_value
    )
    q6_p90 = _range_p90(posterior.get("q6_decision_value_range"))
    if truth_value is None:
        return {
            "class": "truth_missing",
            "under_by": None,
            "actionable": False,
        }
    if q6_p90 is None:
        return {
            "class": "posterior_missing",
            "under_by": None,
            "actionable": False,
        }
    if truth_value <= 0:
        if raw_truth_value and raw_truth_value > 0:
            return {
                "class": "no_plannable_tail_review",
                "under_by": 0,
                "actionable": False,
            }
        return {
            "class": "truth_zero_noise",
            "under_by": 0,
            "actionable": False,
        }
    if truth_value > q6_p90:
        return {
            "class": "truth_p90_miss",
            "under_by": truth_value - q6_p90,
            "actionable": True,
        }
    return {
        "class": "truth_p90_covered",
        "under_by": 0,
        "actionable": False,
    }


def _q6_actionable_shadow_status(
    shadows: Iterable[Mapping[str, Any]],
    *,
    actionable: bool,
) -> str:
    if not actionable:
        return ""
    risk_candidates = [
        shadow
        for shadow in shadows
        if shadow.get("display_mode") == "risk_reference_candidate"
    ]
    pending_candidates = [
        shadow
        for shadow in shadows
        if shadow.get("display_mode")
        in {
            "shadow_only_pending_no_q6_controls",
            "shadow_only_hidden_tail_review",
        }
    ]
    if any(_bool(shadow.get("active")) for shadow in risk_candidates):
        return "active_shadow_candidate"
    if any(_bool(shadow.get("active")) for shadow in pending_candidates):
        return "active_pending_shadow_candidate"
    if risk_candidates:
        return "not_covered_by_shadow_gate"
    if pending_candidates:
        return "pending_shadow_candidate_inactive"
    return "no_shadow_candidate"


def _q6_actionable_followup(
    row: Mapping[str, Any],
    *,
    actionable: bool,
    shadow_status: str,
) -> dict[str, str]:
    if not actionable:
        return {"bucket": "", "reason": ""}
    if shadow_status in {"active_shadow_candidate", "active_pending_shadow_candidate"}:
        return {"bucket": "shadow_observation", "reason": "covered_by_active_shadow"}
    hero = str(row.get("hero") or "").lower()
    family = _map_family(row.get("map_id"))
    profile = str(row.get("evidence_profile_key") or "")
    bottom_row = _int(row.get("layout_bottom_row"))
    if hero == "aisha" and family == "shipwreck":
        if bottom_row is not None and bottom_row < 13:
            return {
                "bucket": "aisha_shipwreck_low_bottom_floor_risky",
                "reason": "below_current_deep_floor_gate_and_no_q6_controls_raise",
            }
        return {
            "bucket": "aisha_shipwreck_profile_gap",
            "reason": "outside_active_shipwreck_shadow_or_sampler_boundary",
        }
    if hero == "aisha" and family == "villa":
        if profile.startswith("public:"):
            return {
                "bucket": "aisha_villa_public_profile_outside_pending_gate",
                "reason": "pending_villa_floor_only_covers_plain_shape_layout",
            }
        return {
            "bucket": "aisha_villa_uncovered_profile",
            "reason": "outside_active_pending_villa_gate",
        }
    if hero == "ethan" and profile == "layout":
        return {
            "bucket": "ethan_layout_floor_risky",
            "reason": "prior_floor_experiments_raise_no_q6_or_no_plannable_controls",
        }
    if hero == "ethan":
        return {
            "bucket": "ethan_non_layout_floor_risky",
            "reason": "ethan_floor_not_clean_enough_for_shadow_or_no_plannable_controls",
        }
    return {
        "bucket": "unsupported_q6_miss",
        "reason": "needs_manual_feature_split",
    }


def _map_family(map_id: Any) -> str:
    try:
        mid = int(map_id)
    except (TypeError, ValueError):
        return "unknown"
    prefix = mid // 100
    if mid == 2601:
        return "hidden"
    if prefix in {24, 34, 44}:
        return "villa"
    if prefix in {25, 35, 45}:
        return "shipwreck"
    return f"map_{prefix}xx"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _looks_like_monitor_artifact(payload: Any) -> bool:
    return isinstance(payload, dict) and any(
        key in payload
        for key in (
            "ui_contract",
            "model_eval",
            "v2_posterior_rows",
            "bid_rows",
            "minimap_grid_items",
        )
    )


def _expand_paths(raw_paths: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in raw_paths:
        path = Path(raw)
        if path.is_dir():
            latest = path / "latest_snapshot.json"
            if latest.exists():
                paths.append(latest)
            else:
                paths.extend(sorted(path.glob("*.json")))
        else:
            paths.append(path)
    return paths


def _default_paths() -> list[Path]:
    latest = ROOT / "data" / "logs" / "live" / "latest_snapshot.json"
    return [latest] if latest.exists() else []


def _artifact_from_path(
    path: Path,
    *,
    tables: Any | None,
    tables_dir: str | None,
    n_trials: int,
    roi_trials: int,
    shadow_trials: int | None,
    run_debug_shadows: bool,
    seed: int,
) -> tuple[dict[str, Any], Any | None]:
    payload = _read_json(path)
    if _looks_like_monitor_artifact(payload):
        return dict(payload), tables
    if tables is None:
        tables = load_monitor_tables(tables_dir=tables_dir)
    return (
        build_monitor_artifact_from_file(
            path,
            tables=tables,
            n_trials=n_trials,
            roi_trials=roi_trials,
            shadow_trials=shadow_trials,
            run_debug_shadows=run_debug_shadows,
            seed=seed,
        ),
        tables,
    )


def _contract_from_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    contract = artifact.get("ui_contract")
    if isinstance(contract, dict):
        return contract
    return ui_contract_from_artifact(artifact)


def _shadow_labels(shadows: Iterable[Any], *, display_mode: str | None = None) -> str:
    labels: list[str] = []
    for shadow in shadows:
        if not isinstance(shadow, Mapping):
            continue
        if display_mode is not None and shadow.get("display_mode") != display_mode:
            continue
        label = _text(shadow.get("label"))
        if shadow.get("active"):
            label += "*"
        if label:
            labels.append(label)
    return _compact_list(labels)


def _review_flags(
    artifact: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> list[str]:
    flags: list[str] = []
    baseline = contract.get("baseline") if isinstance(contract.get("baseline"), Mapping) else {}
    posterior = (
        baseline.get("posterior")
        if isinstance(baseline.get("posterior"), Mapping)
        else {}
    )
    q6_risk = (
        contract.get("q6_risk_reference")
        if isinstance(contract.get("q6_risk_reference"), Mapping)
        else {}
    )
    minimap = contract.get("minimap") if isinstance(contract.get("minimap"), Mapping) else {}
    constraints = (
        contract.get("constraints")
        if isinstance(contract.get("constraints"), Mapping)
        else {}
    )
    diagnostics = (
        contract.get("diagnostics")
        if isinstance(contract.get("diagnostics"), Mapping)
        else {}
    )
    diag_layout = (
        diagnostics.get("layout")
        if isinstance(diagnostics.get("layout"), Mapping)
        else {}
    )
    diag_q6 = diagnostics.get("q6") if isinstance(diagnostics.get("q6"), Mapping) else {}
    sampling = (
        diagnostics.get("sampling")
        if isinstance(diagnostics.get("sampling"), Mapping)
        else {}
    )
    public_info = (
        constraints.get("public_info")
        if isinstance(constraints.get("public_info"), Mapping)
        else {}
    )
    truth = contract.get("truth") if isinstance(contract.get("truth"), Mapping) else {}
    shadows = [
        shadow
        for shadow in contract.get("shadows", ()) or ()
        if isinstance(shadow, Mapping)
    ]
    fallback = (
        contract.get("fallback")
        if isinstance(contract.get("fallback"), Mapping)
        else {}
    )
    items = [
        item
        for item in minimap.get("items", ()) or ()
        if isinstance(item, Mapping)
    ]

    if not _bool(baseline.get("official")):
        flags.append("baseline_not_official")
    if not _bool(baseline.get("affects_bid")):
        flags.append("baseline_not_affecting_bid")
    if _bool(q6_risk.get("affects_bid")):
        flags.append("q6_risk_affects_bid")
    if any(_bool(shadow.get("affects_bid")) for shadow in shadows):
        flags.append("shadow_affects_bid")
    v2_row = _first_mapping(artifact.get("v2_posterior_rows"))
    v2_matched, v2_total = _match_counts(v2_row.get("匹配"))
    if v2_matched == 0 and (v2_total or 0) > 0:
        flags.append("zero_posterior_match")
        if not _bool(fallback.get("active")):
            flags.append("zero_match_without_fallback")
    if _bool(fallback.get("affects_bid")):
        flags.append("fallback_affects_bid")
    if any(_text(item.get("display_label")).strip() for item in items):
        flags.append("compact_minimap_label_present")
    if artifact.get("known_value_sum") not in (None, "", 0) and not _bool(
        truth.get("available")
    ):
        flags.append("truth_missing_with_settlement_value")
    input_mode = _text(public_info.get("input_constraints_mode"))
    if "settlement" in input_mode and not input_mode.startswith("pre_settlement"):
        flags.append("settlement_totals_used_as_input")
    truth_q6 = truth.get("q6") if isinstance(truth.get("q6"), Mapping) else {}
    if _int(truth_q6.get("count")) == 0:
        active_tail = [
            shadow
            for shadow in shadows
            if shadow.get("display_mode") == "risk_reference_candidate"
            and _bool(shadow.get("active"))
            and (_int(shadow.get("q6_decision_value_p90")) or 0) > 0
        ]
        if active_tail:
            flags.append("zero_q6_truth_with_active_tail_shadow")
    if _bool(diag_layout.get("conflict")):
        flags.append("layout_conflict")
    if _bool(diag_q6.get("below_drop_prior")):
        flags.append("q6_below_drop_prior")
        q6_review = _q6_below_prior_review(
            posterior,
            truth_q6,
            below_drop_prior=True,
        )
        if q6_review["class"] == "truth_p90_miss":
            flags.append("q6_below_drop_prior_truth_miss")
    if _bool(diag_q6.get("quality_only_deep_local_risk")):
        flags.append("q6_quality_only_deep_local_risk")
    if _bool(diag_q6.get("tail_replacement_p90_misses_truth")):
        flags.append("q6_tail_replacement_truth_miss")
    processing_seconds = _float(sampling.get("processing_seconds"))
    if processing_seconds is not None and processing_seconds >= 10:
        flags.append("slow_monitor_processing")
    decision = baseline.get("decision") if isinstance(baseline.get("decision"), Mapping) else {}
    if not decision.get("action"):
        flags.append("missing_baseline_action")
    return flags


def _manual_focus(flags: list[str]) -> str:
    if flags:
        return _compact_list(flags)
    return "check_hero_map_round;check_public_constraints;check_minimap_colors"


def _minimap_item_sample(minimap: Mapping[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    sample: list[dict[str, Any]] = []
    for item in minimap.get("items", ()) or ():
        if not isinstance(item, Mapping):
            continue
        sample.append(
            {
                "row": item.get("row"),
                "col": item.get("col"),
                "width": item.get("width"),
                "height": item.get("height"),
                "quality": item.get("quality"),
                "item_id": item.get("item_id"),
                "item_name": item.get("item_name"),
                "shape_key": item.get("shape_key"),
            }
        )
        if len(sample) >= limit:
            break
    return sample


def review_row_from_artifact(
    artifact: Mapping[str, Any],
    *,
    source_path: str = "",
) -> dict[str, Any]:
    contract = _contract_from_artifact(artifact)
    context = contract.get("context") if isinstance(contract.get("context"), Mapping) else {}
    source = contract.get("source") if isinstance(contract.get("source"), Mapping) else {}
    baseline = contract.get("baseline") if isinstance(contract.get("baseline"), Mapping) else {}
    decision = baseline.get("decision") if isinstance(baseline.get("decision"), Mapping) else {}
    posterior = (
        baseline.get("posterior")
        if isinstance(baseline.get("posterior"), Mapping)
        else {}
    )
    truth = contract.get("truth") if isinstance(contract.get("truth"), Mapping) else {}
    truth_q6 = truth.get("q6") if isinstance(truth.get("q6"), Mapping) else {}
    constraints = (
        contract.get("constraints")
        if isinstance(contract.get("constraints"), Mapping)
        else {}
    )
    summary = (
        constraints.get("summary")
        if isinstance(constraints.get("summary"), Mapping)
        else {}
    )
    public_info = (
        constraints.get("public_info")
        if isinstance(constraints.get("public_info"), Mapping)
        else {}
    )
    diagnostics = (
        contract.get("diagnostics")
        if isinstance(contract.get("diagnostics"), Mapping)
        else {}
    )
    diag_q6 = diagnostics.get("q6") if isinstance(diagnostics.get("q6"), Mapping) else {}
    diag_layout = (
        diagnostics.get("layout")
        if isinstance(diagnostics.get("layout"), Mapping)
        else {}
    )
    sampling = (
        diagnostics.get("sampling")
        if isinstance(diagnostics.get("sampling"), Mapping)
        else {}
    )
    minimap = contract.get("minimap") if isinstance(contract.get("minimap"), Mapping) else {}
    items = [
        item
        for item in minimap.get("items", ()) or ()
        if isinstance(item, Mapping)
    ]
    shadows = [
        shadow
        for shadow in contract.get("shadows", ()) or ()
        if isinstance(shadow, Mapping)
    ]
    q6_risk = (
        contract.get("q6_risk_reference")
        if isinstance(contract.get("q6_risk_reference"), Mapping)
        else {}
    )
    fallback = (
        contract.get("fallback")
        if isinstance(contract.get("fallback"), Mapping)
        else {}
    )
    fallback_decision = (
        fallback.get("decision")
        if isinstance(fallback.get("decision"), Mapping)
        else {}
    )
    fallback_posterior = (
        fallback.get("posterior")
        if isinstance(fallback.get("posterior"), Mapping)
        else {}
    )
    v2_row = _first_mapping(artifact.get("v2_posterior_rows"))
    v2_matched, v2_total = _match_counts(v2_row.get("匹配"))
    model_eval = (
        artifact.get("model_eval")
        if isinstance(artifact.get("model_eval"), Mapping)
        else {}
    )
    flags = _review_flags(artifact, contract)
    q6_below_prior_review = _q6_below_prior_review(
        posterior,
        truth_q6,
        below_drop_prior=_bool(diag_q6.get("below_drop_prior")),
    )
    q6_actionable_shadow_status = _q6_actionable_shadow_status(
        shadows,
        actionable=bool(q6_below_prior_review["actionable"]),
    )
    row = {
        "source_path": source_path,
        "file": source.get("file") or artifact.get("file") or Path(source_path).name,
        "hero": context.get("hero") or artifact.get("hero"),
        "map_id": context.get("map_id") or artifact.get("map_id"),
        "round": context.get("round") or artifact.get("round"),
        "known_value_sum": context.get("known_value_sum") or artifact.get("known_value_sum"),
        "baseline_action": decision.get("action"),
        "baseline_risk_band": decision.get("risk_band"),
        "baseline_current_highest": decision.get("current_highest"),
        "baseline_stop_price": decision.get("stop_price"),
        "decision_value_range": posterior.get("decision_value_range"),
        "raw_value_range": posterior.get("raw_value_range"),
        "v2_match_text": v2_row.get("匹配"),
        "v2_matched": v2_matched,
        "v2_total": v2_total,
        "fallback_active": fallback.get("active"),
        "fallback_mode": fallback.get("mode"),
        "fallback_action": fallback_decision.get("action"),
        "fallback_raw_value_range": fallback_posterior.get("raw_value_range"),
        "fallback_match_text": fallback_posterior.get("match_text"),
        "fallback_affects_bid": fallback.get("affects_bid"),
        "q6_sample_rate": posterior.get("q6_sample_rate"),
        "q6_prior_rate": posterior.get("q6_prior_rate"),
        "q6_prior_expected_count": posterior.get("q6_prior_expected_count"),
        "q6_prior_expected_cells": posterior.get("q6_prior_expected_cells"),
        "q6_prior_expected_value": posterior.get("q6_prior_expected_value"),
        "q6_decision_value_range": posterior.get("q6_decision_value_range"),
        "q6_count_range": posterior.get("q6_count_range"),
        "q6_cells_range": posterior.get("q6_cells_range"),
        "q6_count_p90_under_prior_by": model_eval.get(
            "v2_q6_count_p90_under_prior_by"
        ),
        "q6_cells_p90_under_prior_by": model_eval.get(
            "v2_q6_cells_p90_under_prior_by"
        ),
        "q6_count_cell_prior_risk": model_eval.get("q6_count_cell_prior_risk"),
        "q6_count_cell_prior_gap": model_eval.get("q6_count_cell_prior_gap"),
        "q6_count_cell_prior_floor_value": model_eval.get(
            "q6_count_cell_prior_floor_value"
        ),
        "total_cells_range": posterior.get("total_cells_range"),
        "remaining_cells_after_layout_range": posterior.get(
            "remaining_cells_after_layout_range"
        ),
        "q6_space_pressure_range": posterior.get("q6_space_pressure_range"),
        "truth_available": truth.get("available"),
        "truth_source": truth.get("source"),
        "truth_total_items": truth.get("total_items"),
        "truth_total_cells": truth.get("total_cells"),
        "truth_q6_count": truth_q6.get("count"),
        "truth_q6_cells": truth_q6.get("cells"),
        "truth_q6_value": truth_q6.get("value"),
        "truth_q6_decision_value": truth_q6.get("decision_value"),
        "truth_q6_trimmed_tail_value": truth_q6.get("trimmed_tail_value"),
        "truth_q6_tail_replacement_value": truth_q6.get(
            "tail_replacement_value"
        ),
        "truth_q6_decision_value_with_tail_replacement": truth_q6.get(
            "decision_value_with_tail_replacement"
        ),
        "input_constraints_mode": public_info.get("input_constraints_mode"),
        "input_total_item_count": summary.get("input_total_item_count"),
        "input_warehouse_total_cells": summary.get("input_warehouse_total_cells"),
        "known_grid_items": summary.get("known_grid_items"),
        "known_purple_item_count": summary.get("known_purple_item_count"),
        "known_gold_item_count": summary.get("known_gold_item_count"),
        "known_red_item_count": summary.get("known_red_item_count"),
        "shape_target_count": summary.get("shape_target_count"),
        "category_target_count": summary.get("category_target_count"),
        "category_exclusion_count": summary.get("category_exclusion_count"),
        "public_constraint_key": summary.get("public_constraint_key"),
        "evidence_profile_key": (
            summary.get("evidence_profile_key")
            or public_info.get("evidence_profile_key")
        ),
        "information_density_band": (
            summary.get("information_density_band")
            or public_info.get("information_density_band")
        ),
        "random_sample_avg_values": public_info.get("random_sample_avg_values"),
        "random_sample_avg_signal_values": public_info.get(
            "random_sample_avg_signal_values"
        ),
        "minimap_known_items": minimap.get("known_items"),
        "minimap_quality_counts": _compact_mapping(minimap.get("quality_counts")),
        "minimap_category_counts": _compact_mapping(minimap.get("category_counts")),
        "minimap_rows_hint": minimap.get("rows_hint"),
        "minimap_scrollable": minimap.get("scrollable"),
        "minimap_nonempty_display_labels": sum(
            1 for item in items if _text(item.get("display_label")).strip()
        ),
        "minimap_unknown_quality_items": sum(
            1 for item in items if item.get("quality") is None
        ),
        "minimap_items_with_names": sum(
            1 for item in items if _text(item.get("item_name")).strip()
        ),
        "q6_risk": q6_risk.get("risk"),
        "q6_risk_reference_p90": (
            q6_risk.get("practical_reference_p90")
            or q6_risk.get("prior_reference_p90")
        ),
        "q6_risk_affects_bid": q6_risk.get("affects_bid"),
        "q6_risk_bid_floor_applied": q6_risk.get("bid_floor_applied"),
        "q6_risk_minimum_bid_floor": q6_risk.get("minimum_bid_floor"),
        "shadow_labels": _shadow_labels(shadows),
        "shadow_risk_candidates": _shadow_labels(
            shadows,
            display_mode="risk_reference_candidate",
        ),
        "shadow_affects_bid_count": sum(
            1 for shadow in shadows if _bool(shadow.get("affects_bid"))
        ),
        "layout_conflict": diag_layout.get("conflict"),
        "layout_conflict_root": diag_layout.get("conflict_root"),
        "layout_bottom_row": diag_layout.get("bottom_row"),
        "layout_bottom_row_risk": diag_layout.get("bottom_row_risk"),
        "layout_bottom_row_risk_threshold": diag_layout.get(
            "bottom_row_risk_threshold"
        ),
        "q6_no_plannable_control": diag_q6.get("no_plannable_control"),
        "q6_zero_q6_proven_control": diag_q6.get("zero_q6_proven_control"),
        "q6_below_drop_prior": diag_q6.get("below_drop_prior"),
        "q6_below_drop_prior_class": q6_below_prior_review["class"],
        "q6_below_drop_prior_under_by": q6_below_prior_review["under_by"],
        "q6_below_drop_prior_actionable": q6_below_prior_review["actionable"],
        "q6_actionable_shadow_status": q6_actionable_shadow_status,
        "q6_top_size_band": diag_q6.get("top_size_band"),
        "q6_quality_only_local_count": diag_q6.get("quality_only_local_count"),
        "q6_quality_only_deepest_local_index": diag_q6.get(
            "quality_only_deepest_local_index"
        ),
        "q6_quality_only_deepest_start_row": diag_q6.get(
            "quality_only_deepest_start_row"
        ),
        "q6_quality_only_deep_local_risk": diag_q6.get(
            "quality_only_deep_local_risk"
        ),
        "q6_quality_only_deep_row_threshold": diag_q6.get(
            "quality_only_deep_row_threshold"
        ),
        "q6_tail_replacement_p90_misses_truth": diag_q6.get(
            "tail_replacement_p90_misses_truth"
        ),
        "q6_tail_replacement_p90_under_by": diag_q6.get(
            "tail_replacement_p90_under_by"
        ),
        "q6_tail_replacement_estimate_p90": diag_q6.get(
            "tail_replacement_estimate_p90"
        ),
        "q6_tail_replacement_estimate_p90_misses_truth": diag_q6.get(
            "tail_replacement_estimate_p90_misses_truth"
        ),
        "q6_tail_replacement_estimate_p90_under_by": diag_q6.get(
            "tail_replacement_estimate_p90_under_by"
        ),
        "q6_tail_replacement_count": diag_q6.get("tail_replacement_count"),
        "q6_tail_replacement_items": diag_q6.get("tail_replacement_items"),
        "q6_tail_replacement_source": diag_q6.get("tail_replacement_source"),
        "processing_seconds": sampling.get("processing_seconds"),
        "n_trials": sampling.get("n_trials") or source.get("n_trials"),
        "shadow_trials": sampling.get("shadow_trials") or source.get("shadow_trials"),
        "review_flags": _compact_list(flags),
        "manual_review_focus": _manual_focus(flags),
        "minimap_item_sample": _minimap_item_sample(minimap),
    }
    q6_followup = _q6_actionable_followup(
        row,
        actionable=bool(q6_below_prior_review["actionable"]),
        shadow_status=q6_actionable_shadow_status,
    )
    row["q6_actionable_followup_bucket"] = q6_followup["bucket"]
    row["q6_actionable_followup_reason"] = q6_followup["reason"]
    return row


def _csv_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: (
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if isinstance(value, (list, dict, tuple))
            else value
        )
        for key, value in row.items()
    }


def _split_flags(value: Any) -> list[str]:
    return [
        part
        for part in str(value or "").split(";")
        if part
    ]


def summarize_review_rows(
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    flag_counts: Counter[str] = Counter()
    focus_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    q6_below_class_counts: Counter[str] = Counter()
    q6_actionable_group_counts: Counter[str] = Counter()
    q6_actionable_profile_counts: Counter[str] = Counter()
    q6_actionable_group_profile_counts: Counter[str] = Counter()
    q6_actionable_shadow_status_counts: Counter[str] = Counter()
    q6_actionable_group_shadow_counts: Counter[str] = Counter()
    q6_actionable_followup_counts: Counter[str] = Counter()
    q6_actionable_group_followup_counts: Counter[str] = Counter()
    q6_actionable_under_by: dict[str, list[int]] = {}
    rows_with_flags = 0
    for row in rows:
        flags = _split_flags(row.get("review_flags"))
        if flags:
            rows_with_flags += 1
        flag_counts.update(flags)
        focus_counts.update(_split_flags(row.get("manual_review_focus")))
        group_key = f"{row.get('hero') or '?'}:{_map_family(row.get('map_id'))}"
        profile_key = str(row.get("evidence_profile_key") or "unknown")
        group_counts[group_key] += 1
        q6_below_class = str(row.get("q6_below_drop_prior_class") or "")
        if q6_below_class:
            q6_below_class_counts[q6_below_class] += 1
        if _bool(row.get("q6_below_drop_prior_actionable")):
            q6_actionable_group_counts[group_key] += 1
            q6_actionable_profile_counts[profile_key] += 1
            q6_actionable_group_profile_counts[
                f"{group_key}:{profile_key}"
            ] += 1
            shadow_status = str(row.get("q6_actionable_shadow_status") or "unknown")
            q6_actionable_shadow_status_counts[shadow_status] += 1
            q6_actionable_group_shadow_counts[f"{group_key}:{shadow_status}"] += 1
            followup_bucket = str(
                row.get("q6_actionable_followup_bucket") or "unknown"
            )
            q6_actionable_followup_counts[followup_bucket] += 1
            q6_actionable_group_followup_counts[
                f"{group_key}:{followup_bucket}"
            ] += 1
            under_by = _int(row.get("q6_below_drop_prior_under_by"))
            if under_by is not None:
                q6_actionable_under_by.setdefault(group_key, []).append(under_by)
    q6_actionable_under_summary = {
        key: {
            "count": len(values),
            "max": max(values),
            "median": sorted(values)[len(values) // 2],
        }
        for key, values in sorted(q6_actionable_under_by.items())
        if values
    }
    return {
        "total_rows": len(rows),
        "error_rows": len(errors or ()),
        "rows_with_review_flags": rows_with_flags,
        "flag_counts": dict(sorted(flag_counts.items())),
        "manual_focus_counts": dict(sorted(focus_counts.items())),
        "hero_map_counts": dict(sorted(group_counts.items())),
        "layout_conflict_rows": sum(
            1 for row in rows if row.get("layout_conflict") is True
        ),
        "q6_below_drop_prior_rows": sum(
            1 for row in rows if row.get("q6_below_drop_prior") is True
        ),
        "q6_below_drop_prior_class_counts": dict(
            sorted(q6_below_class_counts.items())
        ),
        "q6_below_drop_prior_actionable_rows": sum(
            1 for row in rows if _bool(row.get("q6_below_drop_prior_actionable"))
        ),
        "q6_below_drop_prior_no_plannable_rows": sum(
            1
            for row in rows
            if row.get("q6_below_drop_prior") is True
            and _bool(row.get("q6_no_plannable_control"))
        ),
        "q6_below_drop_prior_zero_q6_proven_rows": sum(
            1
            for row in rows
            if row.get("q6_below_drop_prior") is True
            and _bool(row.get("q6_zero_q6_proven_control"))
        ),
        "q6_quality_only_deep_local_risk_rows": sum(
            1 for row in rows if _bool(row.get("q6_quality_only_deep_local_risk"))
        ),
        "q6_tail_replacement_value_rows": sum(
            1 for row in rows
            if (_int(row.get("truth_q6_tail_replacement_value")) or 0) > 0
        ),
        "q6_tail_replacement_truth_miss_rows": sum(
            1 for row in rows
            if _bool(row.get("q6_tail_replacement_p90_misses_truth"))
        ),
        "q6_actionable_miss_by_hero_map": dict(
            sorted(q6_actionable_group_counts.items())
        ),
        "q6_actionable_miss_by_evidence_profile": dict(
            sorted(q6_actionable_profile_counts.items())
        ),
        "q6_actionable_miss_by_hero_map_profile": dict(
            sorted(q6_actionable_group_profile_counts.items())
        ),
        "q6_actionable_shadow_status_counts": dict(
            sorted(q6_actionable_shadow_status_counts.items())
        ),
        "q6_actionable_miss_by_hero_map_shadow_status": dict(
            sorted(q6_actionable_group_shadow_counts.items())
        ),
        "q6_actionable_followup_bucket_counts": dict(
            sorted(q6_actionable_followup_counts.items())
        ),
        "q6_actionable_followup_by_hero_map": dict(
            sorted(q6_actionable_group_followup_counts.items())
        ),
        "q6_actionable_under_by_by_hero_map": q6_actionable_under_summary,
        "zero_q6_truth_rows": sum(
            1 for row in rows if _int(row.get("truth_q6_count")) == 0
        ),
        "q6_no_plannable_control_rows": sum(
            1 for row in rows if _bool(row.get("q6_no_plannable_control"))
        ),
        "q6_zero_q6_proven_control_rows": sum(
            1 for row in rows if _bool(row.get("q6_zero_q6_proven_control"))
        ),
        "q6_risk_affects_bid_rows": sum(
            1 for row in rows if _bool(row.get("q6_risk_affects_bid"))
        ),
        "q6_risk_bid_floor_applied_rows": sum(
            1 for row in rows if _bool(row.get("q6_risk_bid_floor_applied"))
        ),
        "zero_posterior_match_rows": sum(
            1 for row in rows if _int(row.get("v2_matched")) == 0
        ),
        "zero_match_with_fallback_rows": sum(
            1
            for row in rows
            if _int(row.get("v2_matched")) == 0 and _bool(row.get("fallback_active"))
        ),
        "fallback_active_rows": sum(
            1 for row in rows if _bool(row.get("fallback_active"))
        ),
        "tail_shadow_candidate_rows": sum(
            1 for row in rows if row.get("shadow_risk_candidates")
        ),
        "active_tail_shadow_candidate_rows": sum(
            1 for row in rows if "*" in str(row.get("shadow_risk_candidates") or "")
        ),
        "minimap_text_regression_rows": sum(
            1 for row in rows if (_int(row.get("minimap_nonempty_display_labels")) or 0) > 0
        ),
        "minimap_unknown_quality_rows": sum(
            1 for row in rows if (_int(row.get("minimap_unknown_quality_items")) or 0) > 0
        ),
        "shadow_affects_bid_rows": sum(
            1 for row in rows if (_int(row.get("shadow_affects_bid_count")) or 0) > 0
        ),
    }


def export_review_rows(
    paths: Iterable[Path],
    *,
    out_dir: Path,
    tables_dir: str | None = None,
    n_trials: int = 80,
    roi_trials: int = 0,
    shadow_trials: int | None = None,
    run_debug_shadows: bool = False,
    seed: int = 20260530,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    tables: Any | None = None
    for path in paths:
        try:
            artifact, tables = _artifact_from_path(
                path,
                tables=tables,
                tables_dir=tables_dir,
                n_trials=n_trials,
                roi_trials=roi_trials,
                shadow_trials=shadow_trials,
                run_debug_shadows=run_debug_shadows,
                seed=seed,
            )
            rows.append(review_row_from_artifact(artifact, source_path=str(path)))
        except Exception as exc:
            errors.append(
                {
                    "path": str(path),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    jsonl_path = out_dir / "ui_contract_review.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")

    csv_path = out_dir / "ui_contract_review.csv"
    if rows:
        fieldnames = list(rows[0].keys())
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_csv_row(row) for row in rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    error_path = out_dir / "ui_contract_review_errors.jsonl"
    with error_path.open("w", encoding="utf-8", newline="\n") as fh:
        for error in errors:
            fh.write(json.dumps(error, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")

    summary = summarize_review_rows(rows, errors)
    summary_path = out_dir / "ui_contract_review_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "out_dir": str(out_dir),
        "review_jsonl": str(jsonl_path),
        "review_csv": str(csv_path),
        "summary_json": str(summary_path),
        "errors_jsonl": str(error_path),
        "exported": len(rows),
        "errors": len(errors),
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export UI-contract rows for manual live-readiness review.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "latest_snapshot.json, monitor artifacts, Fatbeans JSON files, or "
            "directories. Defaults to data/logs/live/latest_snapshot.json."
        ),
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--tables-dir", default=None)
    parser.add_argument("--n-trials", type=int, default=80)
    parser.add_argument("--roi-trials", type=int, default=0)
    parser.add_argument("--shadow-trials", type=int, default=None)
    parser.add_argument(
        "--include-debug-shadows",
        action="store_true",
        help=(
            "Run debug-only q6 shadows such as profile_b5. By default review "
            "exports skip them because they are not shown in the UI contract "
            "risk path and are expensive on full batches."
        ),
    )
    parser.add_argument("--seed", type=int, default=20260530)
    args = parser.parse_args()

    paths = _expand_paths(args.paths) if args.paths else _default_paths()
    if not paths:
        print(
            json.dumps(
                {
                    "out_dir": args.out_dir,
                    "exported": 0,
                    "errors": 0,
                    "note": "no paths; pass latest_snapshot.json or Fatbeans captures",
                },
                ensure_ascii=False,
            )
        )
        return 0
    result = export_review_rows(
        paths,
        out_dir=Path(args.out_dir),
        tables_dir=args.tables_dir,
        n_trials=args.n_trials,
        roi_trials=args.roi_trials,
        shadow_trials=args.shadow_trials,
        run_debug_shadows=args.include_debug_shadows,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
