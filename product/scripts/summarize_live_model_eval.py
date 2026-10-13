"""Summarize live monitor ``model_eval.jsonl`` calibration logs."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.inference.diagnostics import layout_conflict_root  # noqa: E402


_Q6_SHADOW_SAMPLING_TARGETS: tuple[tuple[str, str, int], ...] = (
    ("aisha", "shipwreck", 20),
    ("ethan", "shipwreck", 20),
    ("aisha", "hidden", 10),
    ("ethan", "hidden", 5),
)
_Q6_DEEP_FLOOR_SHADOW_SAMPLING_TARGETS: tuple[tuple[str, str, int], ...] = (
    ("aisha", "shipwreck", 20),
)
_Q6_HIDDEN_FLOOR_SHADOW_SAMPLING_TARGETS: tuple[tuple[str, str, int], ...] = (
    ("aisha", "hidden", 10),
)
_Q6_VILLA_FLOOR_SHADOW_SAMPLING_TARGETS: tuple[tuple[str, str, int], ...] = (
    ("aisha", "villa", 20),
)
_Q6_ETHAN_SHIPWRECK_LAYOUT_CONDITIONAL_SHADOW_SAMPLING_TARGETS: tuple[
    tuple[str, str, int], ...
] = (
    ("ethan", "shipwreck", 20),
)
_Q6_SHADOW_REVIEW_SPECS: dict[str, tuple[str, str]] = {
    "profile_b5": (
        "q6_residual_boost_shadow_label",
        "q6_residual_boost_shadow",
    ),
    "aisha_deep_floor1": (
        "q6_residual_deep_floor_shadow_label",
        "q6_residual_deep_floor_shadow",
    ),
    "aisha_hidden_floor15": (
        "q6_residual_hidden_floor_shadow_label",
        "q6_residual_hidden_floor_shadow",
    ),
    "aisha_villa_floor05": (
        "q6_residual_villa_floor_shadow_label",
        "q6_residual_villa_floor_shadow",
    ),
    "ethan_shipwreck_layout_conditional_c4_cells15": (
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_label",
        "q6_residual_ethan_shipwreck_layout_conditional_shadow",
    ),
}
_Q6_SHADOW_REVIEW_FIELDS: tuple[str, ...] = (
    "candidate",
    "review_class",
    "file",
    "hero",
    "map_id",
    "map_family",
    "round",
    "evidence_stage",
    "information_density_band",
    "evidence_profile_key",
    "public_constraint_key",
    "layout_bottom_row",
    "q6_quality_only_local_count",
    "q6_quality_only_deepest_local_index",
    "q6_quality_only_deepest_start_row",
    "q6_quality_only_deep_local_risk",
    "q6_quality_only_deep_row_threshold",
    "layout_conflict",
    "layout_conflict_root",
    "relaxed_exact_used",
    "final_value",
    "final_cells",
    "final_q6_count",
    "final_q6_cells",
    "final_q6_value",
    "final_q6_decision_value",
    "final_q6_trimmed_tail_value",
    "final_q6_trimmed_tail_items",
    "final_q6_tail_replacement_value",
    "final_q6_tail_replacement_count",
    "final_q6_tail_replacement_items",
    "final_q6_tail_replacement_source",
    "final_q6_decision_value_with_tail_replacement",
    "q6_tail_replacement_p90_misses_truth",
    "v2_q6_tail_replacement_decision_value_p90_under_by",
    "v2_q6_tail_replacement_estimate_p90",
    "q6_tail_replacement_estimate_p90_misses_truth",
    "v2_q6_tail_replacement_estimate_p90_under_by",
    "q6_top_size_band",
    "q6_no_plannable_control",
    "q6_zero_q6_proven_control",
    "baseline_decision_value_p50",
    "baseline_decision_value_p90",
    "baseline_q6_decision_value_p90",
    "baseline_q6_plannable_under_by",
    "baseline_q6_tail_replacement_under_by",
    "shadow_decision_value_p50",
    "shadow_decision_value_p90",
    "shadow_q6_decision_value_p90",
    "shadow_q6_plannable_under_by",
    "shadow_q6_plannable_gap_band",
    "shadow_q6_tail_replacement_under_by",
    "shadow_q6_tail_replacement_gap_band",
    "tail_trimmed_q6",
    "tail_replacement_review_needed",
    "shadow_q6_count_p90",
    "shadow_q6_cells_p90",
    "shadow_q6_p90_delta",
    "shadow_under_before",
    "shadow_covered_after",
    "shadow_helped",
    "shadow_no_plannable_control",
    "shadow_no_plannable_positive_proxy",
    "shadow_zero_q6_proven_control",
    "shadow_zero_q6_proven_false_positive",
    "shadow_false_positive_proxy",
    "monitor_processing_seconds",
)


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _round(value: float | int | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value)))


def _mae(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [
        abs(int(row[key]))
        for row in rows
        if row.get(key) is not None
    ]
    return _round(statistics.mean(values)) if values else None


def _median_abs(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [
        abs(int(row[key]))
        for row in rows
        if row.get(key) is not None
    ]
    return _round(statistics.median(values)) if values else None


def _numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _numeric(row, key)
        if value is not None:
            values.append(value)
    return values


def _median_value(rows: list[dict[str, Any]], key: str) -> int | None:
    values = _numeric_values(rows, key)
    return _round(statistics.median(values)) if values else None


def _median_float_value(
    rows: list[dict[str, Any]],
    key: str,
    *,
    digits: int = 3,
) -> float | None:
    values = _numeric_values(rows, key)
    return round(statistics.median(values), digits) if values else None


def _p75_value(rows: list[dict[str, Any]], key: str) -> int | None:
    values = _numeric_values(rows, key)
    if len(values) < 4:
        return None
    return _round(statistics.quantiles(values, n=4)[2])


def _p75_float_value(
    rows: list[dict[str, Any]],
    key: str,
    *,
    digits: int = 3,
) -> float | None:
    values = _numeric_values(rows, key)
    if len(values) < 4:
        return None
    return round(statistics.quantiles(values, n=4)[2], digits)


def _numeric_distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[int] = Counter()
    for row in rows:
        value = _numeric(row, key)
        if value is not None:
            counts[int(round(value))] += 1
    return {
        str(value): count
        for value, count in sorted(counts.items())
    }


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row.get(key) for row in rows if row.get(key) is not None]
    if not values:
        return None
    return round(statistics.mean(1.0 if value else 0.0 for value in values), 4)


def _truthy_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _truthy_count(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if _truthy_value(row.get(key)))


def _first_numeric(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _numeric(row, key)
        if value is not None:
            return value
    return None


def _split_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        tokens: list[str] = []
        for item in value:
            tokens.extend(_split_tokens(item))
        return tokens
    text = str(value)
    for sep in ("+", ",", ";", "|"):
        text = text.replace(sep, " ")
    return [part.strip() for part in text.split() if part.strip()]


def _text_counts(
    rows: list[dict[str, Any]],
    key: str,
    *,
    split_tokens: bool = False,
    limit: int = 8,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = row.get(key)
        if split_tokens:
            values = _split_tokens(value)
        elif value is None or str(value).strip() == "":
            values = []
        else:
            values = [str(value).strip()]
        counts.update(values)
    return {
        label: count
        for label, count in counts.most_common(limit)
    }


def _numeric(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _filter_rows_since(
    rows: list[dict[str, Any]],
    *,
    since_hours: float | None,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], float | None]:
    if since_hours is None:
        return list(rows), None
    cutoff = (time.time() if now is None else now) - max(0.0, since_hours) * 3600.0
    return [
        row
        for row in rows
        if (ts := _numeric(row, "ts")) is not None and ts >= cutoff
    ], cutoff


def _with_derived_decision_errors(row: dict[str, Any]) -> dict[str, Any]:
    decision_p50 = _numeric(row, "decision_value_p50")
    decision_p90 = _numeric(row, "decision_value_p90")
    final_decision_value = _numeric(row, "final_decision_value")
    final_replacement_decision_value = _numeric(
        row,
        "final_decision_value_with_tail_replacement",
    )
    final_value = _numeric(row, "final_value")
    decision_value_truth = final_replacement_decision_value
    if decision_value_truth is None:
        decision_value_truth = final_decision_value
    if decision_value_truth is None:
        decision_value_truth = final_value
    updates: dict[str, Any] = {}
    if decision_value_truth is not None:
        updates["decision_value_truth"] = decision_value_truth
        if final_replacement_decision_value is not None and (
            final_decision_value is None
            or final_replacement_decision_value != final_decision_value
        ):
            updates["decision_value_truth_source"] = "tail_replacement"
        elif final_decision_value is not None:
            updates["decision_value_truth_source"] = "formal"
        else:
            updates["decision_value_truth_source"] = "raw"
    if decision_p50 is not None and decision_value_truth is not None:
        updates["decision_value_p50_error"] = decision_p50 - decision_value_truth
    if decision_p90 is not None and decision_value_truth is not None:
        updates["decision_value_p90_error"] = decision_p90 - decision_value_truth
    if decision_p50 is not None and final_decision_value is not None:
        updates["decision_value_p50_error_vs_formal"] = (
            decision_p50 - final_decision_value
        )
    if decision_p90 is not None and final_decision_value is not None:
        updates["decision_value_p90_error_vs_formal"] = (
            decision_p90 - final_decision_value
        )
    if decision_p50 is not None and final_value is not None:
        updates["decision_value_p50_error_vs_raw"] = decision_p50 - final_value
    if decision_p90 is not None and final_value is not None:
        updates["decision_value_p90_error_vs_raw"] = decision_p90 - final_value
    return {**row, **updates} if updates else row


def _final_q6_decision_value(row: dict[str, Any]) -> int:
    """Read plannable q6 truth while remaining compatible with old live logs."""

    value = row.get("final_q6_decision_value")
    if value is None:
        value = row.get("final_q6_value")
    return int(value or 0)


def _final_q6_tail_replacement_decision_value(row: dict[str, Any]) -> int:
    value = row.get("final_q6_decision_value_with_tail_replacement")
    if value is None:
        return _final_q6_decision_value(row)
    return int(value or 0)


def _q6_no_plannable_control(row: dict[str, Any]) -> bool:
    value = row.get("q6_no_plannable_control")
    if value is not None:
        return bool(value)
    return _final_q6_decision_value(row) <= 0


def _q6_zero_q6_proven_control(row: dict[str, Any]) -> bool:
    return bool(row.get("q6_zero_q6_proven_control"))


def _q6_plannable_under_by(
    row: dict[str, Any],
    predicted_key: str,
) -> int | None:
    predicted = row.get(predicted_key)
    if predicted is None:
        return None
    return max(0, _final_q6_decision_value(row) - int(predicted or 0))


def _q6_tail_replacement_under_by(
    row: dict[str, Any],
    predicted_key: str,
) -> int | None:
    predicted = row.get(predicted_key)
    if predicted is None:
        return None
    return max(
        0,
        _final_q6_tail_replacement_decision_value(row) - int(predicted or 0),
    )


def _q6_under_gap_band(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value <= 0:
        return "covered"
    if value <= 100_000:
        return "small_<=100k"
    if value <= 300_000:
        return "medium_<=300k"
    return "large_>300k"


def _v3_practical_truth_value(row: dict[str, Any]) -> float | None:
    return _first_numeric(
        row,
        "final_formal_decision_value",
        "final_decision_value",
        "decision_value_truth",
        "final_value",
    )


def _v3_practical_q6_truth_value(row: dict[str, Any]) -> float | None:
    if (
        row.get("final_q6_decision_value") is None
        and row.get("final_q6_value") is None
    ):
        return None
    return float(_final_q6_decision_value(row))


def _under_by(truth: float | None, prediction: float | None) -> float | None:
    if truth is None or prediction is None:
        return None
    return max(0.0, truth - prediction)


def _normalized_error_denominator(truth: float) -> float:
    return max(100_000.0, abs(truth))


def _coverage_rate(under_by_values: list[float]) -> float | None:
    if not under_by_values:
        return None
    return round(
        statistics.mean(1.0 if value <= 0 else 0.0 for value in under_by_values),
        4,
    )


def _median_number(values: list[float]) -> int | None:
    return _round(statistics.median(values)) if values else None


def _v3_practical_p90_summary(
    rows: list[dict[str, Any]],
    *,
    truth_func: Any,
    baseline_key: str,
    practical_key: str,
) -> dict[str, Any]:
    baseline_under: list[float] = []
    practical_under: list[float] = []
    paired_reduction: list[float] = []
    baseline_missed = 0
    practical_missed = 0
    helped = 0
    still_missed = 0
    worsened = 0
    extreme_over_values: list[float] = []

    for row in rows:
        truth = truth_func(row)
        baseline = _numeric(row, baseline_key)
        practical = _numeric(row, practical_key)
        baseline_gap = _under_by(truth, baseline)
        practical_gap = _under_by(truth, practical)
        if baseline_gap is not None:
            baseline_under.append(baseline_gap)
            if baseline_gap > 0:
                baseline_missed += 1
        if practical_gap is not None:
            practical_under.append(practical_gap)
            if practical_gap > 0:
                practical_missed += 1
        if baseline_gap is not None and practical_gap is not None:
            paired_reduction.append(baseline_gap - practical_gap)
            if baseline_gap > 0 and practical_gap <= 0:
                helped += 1
            if baseline_gap > 0 and practical_gap > 0:
                still_missed += 1
            if practical_gap > baseline_gap:
                worsened += 1
            if truth is not None and practical is not None:
                extreme_over_values.append(
                    1.0
                    if practical - truth > _normalized_error_denominator(truth)
                    else 0.0
                )

    return {
        "baseline_p90_median": _median_value(rows, baseline_key),
        "practical_p90_median": _median_value(rows, practical_key),
        "delta_p90_median": _median_number(
            [
                practical - baseline
                for row in rows
                if (baseline := _numeric(row, baseline_key)) is not None
                and (practical := _numeric(row, practical_key)) is not None
            ]
        ),
        "truth_rows": len(
            [
                row
                for row in rows
                if truth_func(row) is not None
                and (
                    _numeric(row, baseline_key) is not None
                    or _numeric(row, practical_key) is not None
                )
            ]
        ),
        "baseline_under_rows": baseline_missed,
        "baseline_coverage_rate": _coverage_rate(baseline_under),
        "baseline_under_by_median": _median_number(
            [value for value in baseline_under if value > 0]
        ),
        "practical_under_rows": practical_missed,
        "practical_coverage_rate": _coverage_rate(practical_under),
        "practical_under_by_median": _median_number(
            [value for value in practical_under if value > 0]
        ),
        "helped_rows": helped,
        "still_missed_rows": still_missed,
        "worsened_rows": worsened,
        "under_by_reduction_median": _median_number(paired_reduction),
        "practical_extreme_over_rate": (
            round(statistics.mean(extreme_over_values), 4)
            if extreme_over_values
            else None
        ),
    }


def _v3_practical_raise_watch_review(rows: list[dict[str, Any]]) -> dict[str, Any]:
    watch_rows = [
        row
        for row in rows
        if row.get("v3_practical_recommendation") == "raise_watch"
    ]
    hit = 0
    miss = 0
    false_alarm = 0
    extreme_over = 0
    misleading = 0
    evaluated = 0
    for row in watch_rows:
        truth = _v3_practical_truth_value(row)
        baseline = _numeric(row, "v3_practical_baseline_formal_decision_value_p90")
        practical = _numeric(row, "v3_practical_formal_decision_value_p90")
        if truth is None or baseline is None or practical is None:
            continue
        evaluated += 1
        baseline_missed = baseline < truth
        practical_covered = practical >= truth
        is_false_alarm = not baseline_missed
        is_extreme_over = practical - truth > _normalized_error_denominator(truth)
        if baseline_missed and practical_covered:
            hit += 1
        if baseline_missed and not practical_covered:
            miss += 1
        if is_false_alarm:
            false_alarm += 1
        if is_extreme_over:
            extreme_over += 1
        if is_false_alarm and is_extreme_over:
            misleading += 1
    return {
        "rows": len(watch_rows),
        "evaluated_rows": evaluated,
        "hit_rows": hit,
        "miss_rows": miss,
        "false_alarm_rows": false_alarm,
        "extreme_over_rows": extreme_over,
        "misleading_rows": misleading,
        "hit_rate": round(hit / evaluated, 4) if evaluated else None,
        "miss_rate": round(miss / evaluated, 4) if evaluated else None,
        "false_alarm_rate": (
            round(false_alarm / evaluated, 4) if evaluated else None
        ),
        "extreme_over_rate": (
            round(extreme_over / evaluated, 4) if evaluated else None
        ),
        "misleading_rate": round(misleading / evaluated, 4) if evaluated else None,
    }


def _v3_practical_review_row(row: dict[str, Any]) -> dict[str, Any]:
    truth = _v3_practical_truth_value(row)
    baseline_p90 = _numeric(row, "v3_practical_baseline_formal_decision_value_p90")
    practical_p90 = _numeric(row, "v3_practical_formal_decision_value_p90")
    q6_truth = _v3_practical_q6_truth_value(row)
    baseline_q6_p90 = _numeric(
        row,
        "v3_practical_baseline_q6_formal_decision_value_p90",
    )
    practical_q6_p90 = _numeric(row, "v3_practical_q6_formal_decision_value_p90")
    return {
        "ts": row.get("ts"),
        "file": row.get("file"),
        "hero": row.get("hero"),
        "map_id": row.get("map_id"),
        "round": row.get("observed_round", row.get("round")),
        "action_round": row.get("action_round"),
        "recommendation": row.get("v3_practical_recommendation"),
        "confidence": row.get("v3_practical_confidence"),
        "source_lanes": row.get("v3_practical_source_lanes")
        or row.get("v3_practical_source"),
        "risk_flags": row.get("v3_practical_risk_flags"),
        "active": bool(row.get("v3_practical_active")),
        "affects_bid": bool(row.get("v3_practical_affects_bid")),
        "truth": _round(truth),
        "baseline_p90": _round(baseline_p90),
        "practical_p90": _round(practical_p90),
        "delta_p90": _round(
            practical_p90 - baseline_p90
            if practical_p90 is not None and baseline_p90 is not None
            else None
        ),
        "baseline_under_by": _round(_under_by(truth, baseline_p90)),
        "practical_under_by": _round(_under_by(truth, practical_p90)),
        "q6_truth": _round(q6_truth),
        "baseline_q6_p90": _round(baseline_q6_p90),
        "practical_q6_p90": _round(practical_q6_p90),
        "delta_q6_p90": _round(
            practical_q6_p90 - baseline_q6_p90
            if practical_q6_p90 is not None and baseline_q6_p90 is not None
            else None
        ),
        "q6_under_by": _round(_under_by(q6_truth, practical_q6_p90)),
    }


def _v3_practical_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    practical_rows = [
        row
        for row in rows
        if row.get("v3_practical_available") is not None
        or row.get("v3_practical_formal_decision_value_p90") is not None
        or row.get("v3_practical_recommendation") is not None
    ]
    ready_rows = [
        row for row in practical_rows if _truthy_value(row.get("v3_practical_ready"))
    ]
    candidate_rows = [
        row
        for row in practical_rows
        if _truthy_value(row.get("v3_practical_candidate"))
    ]
    scoped_rows = ready_rows or practical_rows
    latest_rows = [
        _v3_practical_review_row(row)
        for row in sorted(
            practical_rows,
            key=lambda row: float(row.get("ts") or 0),
            reverse=True,
        )[:8]
    ]
    top_under_rows = [
        _v3_practical_review_row(row)
        for row in sorted(
            [
                row
                for row in practical_rows
                if (
                    _under_by(
                        _v3_practical_truth_value(row),
                        _numeric(row, "v3_practical_formal_decision_value_p90"),
                    )
                    or 0
                )
                > 0
            ],
            key=lambda row: _under_by(
                _v3_practical_truth_value(row),
                _numeric(row, "v3_practical_formal_decision_value_p90"),
            )
            or 0,
            reverse=True,
        )[:8]
    ]
    if practical_rows:
        status = "fields_present"
        note = None
    elif rows:
        status = "no_v3_practical_fields"
        note = (
            "当前选中的 model_eval 行没有 v3_practical_* 字段；"
            "需要用最新 live monitor 采集的新日志生成该汇总。"
        )
    else:
        status = "no_evaluable_rows"
        note = "当前时间窗口没有可评估 model_eval 行。"
    return {
        "status": status,
        "note": note,
        "rows": len(practical_rows),
        "available_rows": _truthy_count(practical_rows, "v3_practical_available"),
        "ready_rows": len(ready_rows),
        "candidate_rows": len(candidate_rows),
        "active_rows": _truthy_count(practical_rows, "v3_practical_active"),
        "affects_bid_rows": _truthy_count(
            practical_rows,
            "v3_practical_affects_bid",
        ),
        "recommendation_counts": _text_counts(
            practical_rows,
            "v3_practical_recommendation",
        ),
        "confidence_counts": _text_counts(
            practical_rows,
            "v3_practical_confidence",
        ),
        "source_counts": _text_counts(practical_rows, "v3_practical_source"),
        "source_lane_counts": _text_counts(
            practical_rows,
            "v3_practical_source_lanes",
            split_tokens=True,
        ),
        "risk_flag_counts": _text_counts(
            practical_rows,
            "v3_practical_risk_flags",
            split_tokens=True,
            limit=12,
        ),
        "formal_p90": _v3_practical_p90_summary(
            scoped_rows,
            truth_func=_v3_practical_truth_value,
            baseline_key="v3_practical_baseline_formal_decision_value_p90",
            practical_key="v3_practical_formal_decision_value_p90",
        ),
        "q6_formal_p90": _v3_practical_p90_summary(
            scoped_rows,
            truth_func=_v3_practical_q6_truth_value,
            baseline_key="v3_practical_baseline_q6_formal_decision_value_p90",
            practical_key="v3_practical_q6_formal_decision_value_p90",
        ),
        "formal_p50": {
            "baseline_p50_median": _median_value(
                scoped_rows,
                "v3_practical_baseline_formal_decision_value_p50",
            ),
            "practical_p50_median": _median_value(
                scoped_rows,
                "v3_practical_formal_decision_value_p50",
            ),
            "delta_p50_median": _median_value(
                scoped_rows,
                "v3_practical_delta_formal_decision_value_p50",
            ),
        },
        "q6_formal_p50": {
            "baseline_p50_median": _median_value(
                scoped_rows,
                "v3_practical_baseline_q6_formal_decision_value_p50",
            ),
            "practical_p50_median": _median_value(
                scoped_rows,
                "v3_practical_q6_formal_decision_value_p50",
            ),
            "delta_p50_median": _median_value(
                scoped_rows,
                "v3_practical_delta_q6_formal_decision_value_p50",
            ),
        },
        "raise_watch_review": _v3_practical_raise_watch_review(scoped_rows),
        "latest_rows": latest_rows,
        "top_under_rows": top_under_rows,
    }


def _limit_rows(rows: Any, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [
        row
        for row in rows[:limit]
        if isinstance(row, dict)
    ]


def _readiness_brief(readiness: Any) -> dict[str, Any]:
    if not isinstance(readiness, dict):
        return {}
    source_groups = readiness.get("groups") or readiness.get("targets")
    groups = [
        {
            "hero": row.get("hero"),
            "map_family": row.get("map_family"),
            "n": row.get("n"),
            "target": row.get("target"),
            "needed": row.get("needed"),
        }
        for row in _limit_rows(source_groups, limit=12)
    ]
    return {
        "ready": readiness.get("ready"),
        "total_needed": readiness.get("total_needed"),
        "priority_needs": _limit_rows(readiness.get("priority_needs"), limit=8),
        "groups": groups,
    }


def _candidate_readiness_brief(candidates: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(candidates, dict):
        return {}
    brief: dict[str, dict[str, Any]] = {}
    for label, candidate in candidates.items():
        if not isinstance(candidate, dict):
            continue
        brief[str(label)] = {
            "status": candidate.get("status"),
            "target_ready": candidate.get("target_ready"),
            "target_total_needed": candidate.get("target_total_needed"),
            "tracked_rows": candidate.get("tracked_rows"),
            "active_rows": candidate.get("active_rows"),
            "active_no_q6_rows": candidate.get("active_no_q6_rows"),
            "active_zero_q6_proven_rows": candidate.get(
                "active_zero_q6_proven_rows"
            ),
            "under_before_rows": candidate.get("under_before_rows"),
            "helped_rows": candidate.get("helped_rows"),
            "still_missed_rows": candidate.get("still_missed_rows"),
            "helped_rate": candidate.get("helped_rate"),
            "false_positive_proxy_rows": candidate.get("false_positive_proxy_rows"),
            "zero_q6_proven_false_positive_rows": candidate.get(
                "zero_q6_proven_false_positive_rows"
            ),
            "false_positive_proxy_rate_active": candidate.get(
                "false_positive_proxy_rate_active"
            ),
            "q6_p90_delta_median": candidate.get("q6_p90_delta_median"),
            "priority_needs": _limit_rows(candidate.get("priority_needs"), limit=5),
        }
    return brief


def _v3_practical_brief(practical: Any) -> dict[str, Any]:
    if not isinstance(practical, dict):
        return {}
    rows = int(practical.get("rows") or 0)
    brief: dict[str, Any] = {
        "status": practical.get("status"),
        "note": practical.get("note"),
        "rows": rows,
        "available_rows": practical.get("available_rows"),
        "ready_rows": practical.get("ready_rows"),
        "candidate_rows": practical.get("candidate_rows"),
        "active_rows": practical.get("active_rows"),
        "affects_bid_rows": practical.get("affects_bid_rows"),
    }
    if rows <= 0:
        return brief
    brief.update(
        {
            "recommendation_counts": practical.get("recommendation_counts"),
            "confidence_counts": practical.get("confidence_counts"),
            "source_lane_counts": practical.get("source_lane_counts"),
            "risk_flag_counts": practical.get("risk_flag_counts"),
            "formal_p90": practical.get("formal_p90"),
            "q6_formal_p90": practical.get("q6_formal_p90"),
            "formal_p50": practical.get("formal_p50"),
            "q6_formal_p50": practical.get("q6_formal_p50"),
            "raise_watch_review": practical.get("raise_watch_review"),
            "latest_rows": _limit_rows(practical.get("latest_rows"), limit=5),
            "top_under_rows": _limit_rows(
                practical.get("top_under_rows"),
                limit=5,
            ),
        }
    )
    return brief


def brief_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Return the high-signal subset used for live calibration check-ins."""

    return {
        "window": summary.get("window"),
        "rows": summary.get("rows"),
        "raw_rows": summary.get("raw_rows"),
        "deduped_rows": summary.get("deduped_rows"),
        "valid": summary.get("valid"),
        "decision_value_mae": summary.get("decision_value_mae"),
        "decision_value_median_abs_error": summary.get(
            "decision_value_median_abs_error"
        ),
        "raw_value_mae": summary.get("raw_value_mae"),
        "warehouse_mae": summary.get("warehouse_mae"),
        "layout_fit_mae": summary.get("layout_fit_mae"),
        "performance": {
            "processing_seconds_median": summary.get(
                "monitor_processing_seconds_median"
            ),
            "processing_seconds_p75": summary.get("monitor_processing_seconds_p75"),
            "n_trials": summary.get("monitor_n_trials_values"),
            "shadow_trials": summary.get("monitor_shadow_trials_values"),
            "roi_trials": summary.get("monitor_roi_trials_values"),
        },
        "log_quality": summary.get("log_quality"),
        "monitor_errors": summary.get("monitor_errors"),
        "collection_readiness": _readiness_brief(
            summary.get("collection_readiness")
        ),
        "q6_shadow_sampling_progress": _readiness_brief(
            summary.get("q6_shadow_sampling_progress")
        ),
        "q6_shadow_candidate_readiness": _candidate_readiness_brief(
            summary.get("q6_shadow_candidate_readiness")
        ),
        "v3_practical": _v3_practical_brief(summary.get("v3_practical")),
        "next_sampling_targets": _limit_rows(
            summary.get("next_sampling_targets"),
            limit=8,
        ),
        "q6": {
            "q6_p90_miss_count": summary.get("q6_p90_miss_count"),
            "q6_p90_under_by_median": summary.get("q6_p90_under_by_median"),
            "q6_plannable_p90_miss_count": summary.get(
                "q6_plannable_p90_miss_count"
            ),
            "q6_plannable_p90_under_by_median": summary.get(
                "q6_plannable_p90_under_by_median"
            ),
            "q6_tail_replacement_value_count": summary.get(
                "q6_tail_replacement_value_count"
            ),
            "q6_tail_replacement_value_median": summary.get(
                "q6_tail_replacement_value_median"
            ),
            "q6_tail_replacement_p90_miss_count": summary.get(
                "q6_tail_replacement_p90_miss_count"
            ),
            "q6_tail_replacement_p90_under_by_median": summary.get(
                "q6_tail_replacement_p90_under_by_median"
            ),
            "q6_tail_replacement_estimate_p90_miss_count": summary.get(
                "q6_tail_replacement_estimate_p90_miss_count"
            ),
            "q6_tail_replacement_estimate_p90_under_by_median": summary.get(
                "q6_tail_replacement_estimate_p90_under_by_median"
            ),
            "q6_false_low_count": summary.get("q6_false_low_count"),
            "q6_below_drop_prior_count": summary.get("q6_below_drop_prior_count"),
            "q6_practical_gate_count": summary.get("q6_practical_gate_count"),
            "q6_practical_gate_helped_count": summary.get(
                "q6_practical_gate_helped_count"
            ),
            "q6_practical_gate_false_positive_proxy_count": summary.get(
                "q6_practical_gate_false_positive_proxy_count"
            ),
            "q6_quality_only_local_count": summary.get(
                "q6_quality_only_local_count"
            ),
            "q6_quality_only_deep_local_risk_count": summary.get(
                "q6_quality_only_deep_local_risk_count"
            ),
            "top_miss_root_causes": _limit_rows(
                summary.get("q6_miss_root_causes"),
                limit=5,
            ),
        },
        "layout": {
            "layout_conflict_count": summary.get("layout_conflict_count"),
            "relaxed_exact_count": summary.get("relaxed_exact_count"),
            "top_conflict_root_causes": _limit_rows(
                summary.get("layout_conflict_root_causes"),
                limit=5,
            ),
        },
    }


def _shadow_review_class(row: dict[str, Any], *, prefix: str) -> str:
    if row.get(f"{prefix}_zero_q6_proven_false_positive"):
        return "active_zero_q6_proven_false_positive"
    if row.get(f"{prefix}_false_positive_proxy"):
        return "active_false_positive"
    if row.get(f"{prefix}_helped"):
        return "active_helped"
    if row.get(f"{prefix}_under_before"):
        return "active_still_missed"
    if _q6_no_plannable_control(row):
        return "active_no_q6_control"
    return "active_observation"


def _shadow_candidate_review_row(
    row: dict[str, Any],
    *,
    label: str,
    prefix: str,
) -> dict[str, Any]:
    shadow_under_by = _q6_plannable_under_by(
        row,
        f"{prefix}_q6_decision_value_p90",
    )
    shadow_tail_replacement_under_by = _q6_tail_replacement_under_by(
        row,
        f"{prefix}_q6_decision_value_p90",
    )
    tail_trimmed_q6 = int(row.get("final_q6_trimmed_tail_value") or 0) > 0
    tail_replacement_value = int(row.get("final_q6_tail_replacement_value") or 0)
    return {
        "candidate": label,
        "review_class": _shadow_review_class(row, prefix=prefix),
        "file": row.get("file"),
        "hero": row.get("hero"),
        "map_id": row.get("map_id"),
        "map_family": _map_family(row.get("map_id")),
        "round": row.get("round"),
        "evidence_stage": row.get("evidence_stage"),
        "information_density_band": row.get("information_density_band"),
        "evidence_profile_key": row.get("evidence_profile_key"),
        "public_constraint_key": row.get("public_constraint_key"),
        "layout_bottom_row": row.get("layout_bottom_row"),
        "q6_quality_only_local_count": row.get("q6_quality_only_local_count"),
        "q6_quality_only_deepest_local_index": row.get(
            "q6_quality_only_deepest_local_index"
        ),
        "q6_quality_only_deepest_start_row": row.get(
            "q6_quality_only_deepest_start_row"
        ),
        "q6_quality_only_deep_local_risk": row.get(
            "q6_quality_only_deep_local_risk"
        ),
        "q6_quality_only_deep_row_threshold": row.get(
            "q6_quality_only_deep_row_threshold"
        ),
        "layout_conflict": row.get("layout_conflict"),
        "layout_conflict_root": row.get("layout_conflict_root"),
        "relaxed_exact_used": row.get("relaxed_exact_used"),
        "final_value": row.get("final_value"),
        "final_cells": row.get("final_cells"),
        "final_q6_count": row.get("final_q6_count"),
        "final_q6_cells": row.get("final_q6_cells"),
        "final_q6_value": row.get("final_q6_value"),
        "final_q6_decision_value": row.get("final_q6_decision_value"),
        "final_q6_trimmed_tail_value": row.get("final_q6_trimmed_tail_value"),
        "final_q6_trimmed_tail_items": row.get("final_q6_trimmed_tail_items"),
        "final_q6_tail_replacement_value": row.get(
            "final_q6_tail_replacement_value"
        ),
        "final_q6_tail_replacement_count": row.get(
            "final_q6_tail_replacement_count"
        ),
        "final_q6_tail_replacement_items": row.get(
            "final_q6_tail_replacement_items"
        ),
        "final_q6_tail_replacement_source": row.get(
            "final_q6_tail_replacement_source"
        ),
        "final_q6_decision_value_with_tail_replacement": row.get(
            "final_q6_decision_value_with_tail_replacement"
        ),
        "q6_tail_replacement_p90_misses_truth": row.get(
            "q6_tail_replacement_p90_misses_truth"
        ),
        "v2_q6_tail_replacement_decision_value_p90_under_by": row.get(
            "v2_q6_tail_replacement_decision_value_p90_under_by"
        ),
        "v2_q6_tail_replacement_estimate_p90": row.get(
            "v2_q6_tail_replacement_estimate_p90"
        ),
        "q6_tail_replacement_estimate_p90_misses_truth": row.get(
            "q6_tail_replacement_estimate_p90_misses_truth"
        ),
        "v2_q6_tail_replacement_estimate_p90_under_by": row.get(
            "v2_q6_tail_replacement_estimate_p90_under_by"
        ),
        "q6_top_size_band": row.get("q6_top_size_band"),
        "q6_no_plannable_control": _q6_no_plannable_control(row),
        "q6_zero_q6_proven_control": _q6_zero_q6_proven_control(row),
        "baseline_decision_value_p50": row.get("decision_value_p50"),
        "baseline_decision_value_p90": row.get("decision_value_p90"),
        "baseline_q6_decision_value_p90": row.get("v2_q6_decision_value_p90"),
        "baseline_q6_plannable_under_by": row.get(
            "v2_q6_decision_value_p90_under_by"
        ),
        "baseline_q6_tail_replacement_under_by": _q6_tail_replacement_under_by(
            row,
            "v2_q6_decision_value_p90",
        ),
        "shadow_decision_value_p50": row.get(f"{prefix}_decision_value_p50"),
        "shadow_decision_value_p90": row.get(f"{prefix}_decision_value_p90"),
        "shadow_q6_decision_value_p90": row.get(
            f"{prefix}_q6_decision_value_p90"
        ),
        "shadow_q6_plannable_under_by": shadow_under_by,
        "shadow_q6_plannable_gap_band": _q6_under_gap_band(shadow_under_by),
        "shadow_q6_tail_replacement_under_by": shadow_tail_replacement_under_by,
        "shadow_q6_tail_replacement_gap_band": _q6_under_gap_band(
            shadow_tail_replacement_under_by
        ),
        "tail_trimmed_q6": tail_trimmed_q6,
        "tail_replacement_review_needed": (
            tail_trimmed_q6 and tail_replacement_value > 0
        ),
        "shadow_q6_count_p90": row.get(f"{prefix}_q6_count_p90"),
        "shadow_q6_cells_p90": row.get(f"{prefix}_q6_cells_p90"),
        "shadow_q6_p90_delta": row.get(f"{prefix}_q6_p90_delta"),
        "shadow_under_before": row.get(f"{prefix}_under_before"),
        "shadow_covered_after": row.get(f"{prefix}_covered_after"),
        "shadow_helped": row.get(f"{prefix}_helped"),
        "shadow_no_plannable_control": row.get(
            f"{prefix}_no_plannable_control"
        ),
        "shadow_no_plannable_positive_proxy": row.get(
            f"{prefix}_no_plannable_positive_proxy"
        ),
        "shadow_zero_q6_proven_control": row.get(
            f"{prefix}_zero_q6_proven_control"
        ),
        "shadow_zero_q6_proven_false_positive": row.get(
            f"{prefix}_zero_q6_proven_false_positive"
        ),
        "shadow_false_positive_proxy": row.get(
            f"{prefix}_false_positive_proxy"
        ),
        "monitor_processing_seconds": row.get("monitor_processing_seconds"),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_Q6_SHADOW_REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def export_shadow_candidate_reviews(
    rows: list[dict[str, Any]],
    *,
    out_dir: Path,
    candidate_labels: list[str] | tuple[str, ...] | None = None,
    dedupe: bool = True,
) -> dict[str, Any]:
    """Export active live-shadow rows for candidate promotion review."""

    selected_rows = _dedupe_latest_by_file(rows) if dedupe else list(rows)
    labels = list(candidate_labels or _Q6_SHADOW_REVIEW_SPECS)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "source_rows": len(rows),
        "deduped_rows": len(selected_rows),
        "out_dir": str(out_dir),
        "candidates": {},
    }
    for label in labels:
        if label not in _Q6_SHADOW_REVIEW_SPECS:
            raise ValueError(f"unknown q6 shadow review candidate: {label}")
        label_key, prefix = _Q6_SHADOW_REVIEW_SPECS[label]
        tracked = [
            row for row in selected_rows
            if row.get(label_key) == label
        ]
        active = [
            row for row in tracked
            if row.get(f"{prefix}_active")
        ]
        review_rows = [
            _shadow_candidate_review_row(row, label=label, prefix=prefix)
            for row in active
        ]
        review_rows.sort(
            key=lambda row: (
                str(row.get("review_class") or ""),
                str(row.get("file") or ""),
            )
        )
        csv_path = out_dir / f"{label}.csv"
        jsonl_path = out_dir / f"{label}.jsonl"
        _write_csv(csv_path, review_rows)
        _write_jsonl(jsonl_path, review_rows)
        class_counts = Counter(
            str(row.get("review_class") or "unknown")
            for row in review_rows
        )
        summary["candidates"][label] = {
            "tracked_rows": len(tracked),
            "active_rows": len(active),
            "inactive_rows": len(tracked) - len(active),
            "review_class_counts": dict(sorted(class_counts.items())),
            "csv": str(csv_path),
            "jsonl": str(jsonl_path),
        }
    summary_path = out_dir / "q6_shadow_candidate_review_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **summary,
        "summary": str(summary_path),
    }


def _dedupe_latest_by_file(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        key = str(row.get("file") or "")
        if not key:
            key = f"row:{len(order)}"
        if key not in selected:
            order.append(key)
        current = selected.get(key)
        if current is None or float(row.get("ts") or 0) >= float(current.get("ts") or 0):
            selected[key] = row
    return [selected[key] for key in order]


def _with_derived_layout_root(row: dict[str, Any]) -> dict[str, Any]:
    if not row.get("layout_conflict") or row.get("layout_conflict_root"):
        return row
    root = layout_conflict_root(
        row.get("posterior_diagnostics") or row.get("layout_diagnostics")
    )
    if not root:
        return row
    return {
        **row,
        "layout_conflict_root": root,
    }


def _q6_top_size_band(row: dict[str, Any]) -> str:
    if row.get("q6_top_size_band"):
        return str(row["q6_top_size_band"])
    if (
        int(row.get("final_q6_count") or 0) <= 0
        and int(row.get("final_q6_value") or 0) <= 0
    ):
        return "no_q6"
    if row.get("final_top_item_quality") is None:
        return "q6_top_unknown_cells"
    if int(row.get("final_top_item_quality") or 0) != 6:
        return "q6_not_top_item"
    cells = row.get("final_top_item_cells")
    if cells is None:
        return "q6_top_unknown_cells"
    cells = int(cells)
    if cells <= 2:
        return "q6_top_small"
    if cells <= 4:
        return "q6_top_compact"
    if cells <= 9:
        return "q6_top_medium"
    if cells <= 12:
        return "q6_top_large"
    return "q6_top_huge"


def _q6_miss_root(row: dict[str, Any]) -> str:
    if row.get("q6_miss_root"):
        return str(row["q6_miss_root"])
    if row.get("q6_p90_misses_truth") is not True:
        return ""
    markers: list[str] = []
    if row.get("q6_false_low_risk"):
        markers.append("low_q6_sample_rate")
    if row.get("q6_below_drop_prior"):
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
    return ";".join(dict.fromkeys(markers))


def _with_derived_q6_fields(row: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if row.get("q6_top_size_band") is None:
        updates["q6_top_size_band"] = _q6_top_size_band(row)
    if row.get("q6_miss_root") is None:
        root = _q6_miss_root({**row, **updates})
        if root:
            updates["q6_miss_root"] = root
    if (
        row.get("v2_q6_value_p90_under_by") is None
        and row.get("v2_q6_value_p90") is not None
        and int(row.get("final_q6_value") or 0) > 0
    ):
        updates["v2_q6_value_p90_under_by"] = max(
            0,
            int(row.get("final_q6_value") or 0) - int(row["v2_q6_value_p90"]),
        )
    return {**row, **updates} if updates else row


def _evidence_stage(round_no: Any) -> str:
    try:
        value = int(round_no)
    except (TypeError, ValueError):
        return "unknown"
    if value <= 2:
        return "early_1_2"
    if value <= 4:
        return "mid_3_4"
    return "full_5"


def _information_density_score(row: dict[str, Any]) -> int:
    round_no = _numeric(row, "round") or 0
    evidence_count = sum(
        int(_numeric(row, key) or 0)
        for key in (
            "anchor_count",
            "shape_target_count",
            "category_target_count",
            "category_exclusion_count",
        )
    )
    return int(round_no) * 2 + min(evidence_count, 6) * 2


def _information_density_band(score: int | None) -> str:
    if score is None:
        return "unknown"
    if score < 18:
        return "low"
    if score < 34:
        return "medium"
    return "high"


def _with_derived_information_density(row: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if row.get("evidence_stage") is None:
        updates["evidence_stage"] = _evidence_stage(row.get("round"))
    if row.get("information_density_score") is None:
        updates["information_density_score"] = _information_density_score(row)
    if row.get("information_density_band") is None:
        score = int(
            updates.get("information_density_score")
            or row.get("information_density_score")
            or 0
        )
        updates["information_density_band"] = _information_density_band(
            score
        )
    density = str(
        updates.get("information_density_band")
        or row.get("information_density_band")
        or "unknown"
    )
    if row.get("hero_information_density") is None:
        updates["hero_information_density"] = f"{row.get('hero') or 'unknown'}|{density}"
    if row.get("evidence_profile_key") is None:
        updates["evidence_profile_key"] = _evidence_profile_key(row)
    return {**row, **updates} if updates else row


def _evidence_profile_key(row: dict[str, Any]) -> str:
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
    if (
        int(_numeric(row, "category_target_count") or 0)
        + int(_numeric(row, "category_exclusion_count") or 0)
        > 0
    ):
        parts.append("tool:category")
    if int(_numeric(row, "shape_target_count") or 0) > 0:
        parts.append("shape")
    return "+".join(parts) if parts else "basic"


def _group_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)
    out: list[dict[str, Any]] = []
    for value, group_rows in sorted(groups.items()):
        out.append(
            {
                key: value,
                "n": len(group_rows),
                "decision_value_mae": _mae(group_rows, "decision_value_p50_error"),
                "decision_value_median_abs_error": _median_abs(
                    group_rows,
                    "decision_value_p50_error",
                ),
                "warehouse_mae": _mae(group_rows, "warehouse_p50_error"),
                "layout_fit_mae": _mae(group_rows, "layout_fit_p50_error"),
                "q6_false_low_rate": _rate(group_rows, "q6_false_low_risk"),
                "q6_prior_risk_rate": _rate(
                    group_rows,
                    "q6_count_cell_prior_risk",
                ),
                "q6_practical_gate_rate": _rate(
                    group_rows,
                    "q6_practical_gate_hit",
                ),
                "q6_practical_false_positive_rate": _rate(
                    [
                        row for row in group_rows
                        if row.get("q6_practical_gate_hit")
                    ],
                    "q6_practical_gate_false_positive_proxy",
                ),
                "q6_practical_helped_rate": _rate(
                    [
                        row for row in group_rows
                        if row.get("q6_practical_gate_under_before")
                    ],
                    "q6_practical_gate_helped",
                ),
                "q6_residual_boost_shadow_active_rate": _rate(
                    group_rows,
                    "q6_residual_boost_shadow_active",
                ),
                "q6_residual_boost_shadow_false_positive_rate": _rate(
                    [
                        row for row in group_rows
                        if row.get("q6_residual_boost_shadow_active")
                    ],
                    "q6_residual_boost_shadow_false_positive_proxy",
                ),
                "q6_residual_boost_shadow_helped_rate": _rate(
                    [
                        row for row in group_rows
                        if row.get("q6_residual_boost_shadow_under_before")
                    ],
                    "q6_residual_boost_shadow_helped",
                ),
                "q6_residual_deep_floor_shadow_active_rate": _rate(
                    group_rows,
                    "q6_residual_deep_floor_shadow_active",
                ),
                "q6_residual_deep_floor_shadow_false_positive_rate": _rate(
                    [
                        row for row in group_rows
                        if row.get("q6_residual_deep_floor_shadow_active")
                    ],
                    "q6_residual_deep_floor_shadow_false_positive_proxy",
                ),
                "q6_residual_deep_floor_shadow_helped_rate": _rate(
                    [
                        row for row in group_rows
                        if row.get("q6_residual_deep_floor_shadow_under_before")
                    ],
                    "q6_residual_deep_floor_shadow_helped",
                ),
                "q6_p90_miss_rate": _rate(group_rows, "q6_p90_misses_truth"),
                "raw_ceiling_gap_median": _median_value(
                    group_rows,
                    "raw_minus_decision_p90",
                ),
                "layout_conflict_rate": _rate(group_rows, "layout_conflict"),
                "layout_overlap_rate": _marker_rate(
                    group_rows,
                    "layout_conflict_root",
                    "footprint_overlap",
                ),
                "layout_overflow_rate": _marker_rate(
                    group_rows,
                    "layout_conflict_root",
                    "footprint_overflow",
                ),
                "relaxed_exact_rate": _rate(group_rows, "relaxed_exact_used"),
            }
        )
    return out


def _marker_rate(rows: list[dict[str, Any]], key: str, marker: str) -> float | None:
    values = [str(row.get(key) or "") for row in rows if row.get(key) is not None]
    if not values:
        return None
    return round(
        statistics.mean(1.0 if marker in value.split(";") else 0.0 for value in values),
        4,
    )


def _root_cause_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for row in rows:
        raw = str(row.get(key) or "unclassified")
        markers = [part for part in raw.split(";") if part] or ["unclassified"]
        for marker in markers:
            counts[marker] = counts.get(marker, 0) + 1
            examples.setdefault(marker, [])
            if len(examples[marker]) < 5:
                examples[marker].append(str(row.get("file") or ""))
    return [
        {
            "cause": cause,
            "n": n,
            "examples": examples.get(cause, []),
        }
        for cause, n in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


def _bid_gap_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)

    out: list[dict[str, Any]] = []
    for value, group_rows in sorted(groups.items()):
        bid_ratios: list[float] = []
        stop_minus_values: list[float] = []
        over_final = 0
        usable_bid_rows = 0
        for row in group_rows:
            highest = _numeric(row, "highest_bid")
            final_value = _numeric(row, "final_value")
            if highest is not None and final_value is not None and final_value > 0:
                usable_bid_rows += 1
                bid_ratios.append(highest / final_value)
                if highest > final_value:
                    over_final += 1
            stop_minus = _numeric(row, "stop_minus_final_value")
            if stop_minus is not None:
                stop_minus_values.append(stop_minus)
        out.append(
            {
                key: value,
                "n": len(group_rows),
                "bid_rows": usable_bid_rows,
                "highest_bid_over_final_median": (
                    round(statistics.median(bid_ratios), 3)
                    if bid_ratios
                    else None
                ),
                "highest_bid_over_final_p75": (
                    round(statistics.quantiles(bid_ratios, n=4)[2], 3)
                    if len(bid_ratios) >= 4
                    else None
                ),
                "highest_bid_over_final_rate": (
                    round(over_final / usable_bid_rows, 4)
                    if usable_bid_rows
                    else None
                ),
                "stop_minus_final_median": (
                    _round(statistics.median(stop_minus_values))
                    if stop_minus_values
                    else None
                ),
            }
        )
    return out


def _q6_practical_gate_summary(
    rows: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)

    out: list[dict[str, Any]] = []
    for value, group_rows in sorted(groups.items()):
        gated = [row for row in group_rows if row.get("q6_practical_gate_hit")]
        under_before = [
            row for row in gated
            if row.get("q6_practical_gate_under_before")
        ]
        out.append(
            {
                key: value,
                "n": len(group_rows),
                "gated_rows": len(gated),
                "under_before_rows": len(under_before),
                "helped_rows": sum(
                    1 for row in under_before
                    if row.get("q6_practical_gate_helped")
                ),
                "false_positive_proxy_rows": sum(
                    1 for row in gated
                    if row.get("q6_practical_gate_false_positive_proxy")
                ),
                "zero_q6_proven_false_positive_rows": sum(
                    1 for row in gated
                    if row.get("q6_practical_gate_zero_q6_proven_false_positive")
                ),
                "practical_p90_under_by_median": _median_value(
                    gated,
                    "q6_practical_p90_under_by",
                ),
            }
        )
    return out


def _q6_shadow_summary(
    rows: list[dict[str, Any]],
    key: str,
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)

    out: list[dict[str, Any]] = []
    for value, group_rows in sorted(groups.items()):
        active = [
            row for row in group_rows
            if row.get(f"{prefix}_active")
        ]
        under_before = [
            row for row in active
            if row.get(f"{prefix}_under_before")
        ]
        out.append(
            {
                key: value,
                "n": len(group_rows),
                "active_rows": len(active),
                "under_before_rows": len(under_before),
                "helped_rows": sum(
                    1 for row in under_before
                    if row.get(f"{prefix}_helped")
                ),
                "false_positive_proxy_rows": sum(
                    1 for row in active
                    if row.get(f"{prefix}_false_positive_proxy")
                ),
                "zero_q6_proven_false_positive_rows": sum(
                    1 for row in active
                    if row.get(f"{prefix}_zero_q6_proven_false_positive")
                ),
                "q6_p90_delta_median": _median_value(
                    active,
                    f"{prefix}_q6_p90_delta",
                ),
            }
        )
    return out


def _q6_residual_boost_shadow_summary(
    rows: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    return _q6_shadow_summary(
        rows,
        key,
        prefix="q6_residual_boost_shadow",
    )


def _q6_residual_deep_floor_shadow_summary(
    rows: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    return _q6_shadow_summary(
        rows,
        key,
        prefix="q6_residual_deep_floor_shadow",
    )


def _q6_residual_hidden_floor_shadow_summary(
    rows: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    return _q6_shadow_summary(
        rows,
        key,
        prefix="q6_residual_hidden_floor_shadow",
    )


def _q6_residual_villa_floor_shadow_summary(
    rows: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    return _q6_shadow_summary(
        rows,
        key,
        prefix="q6_residual_villa_floor_shadow",
    )


def _q6_residual_ethan_shipwreck_layout_conditional_shadow_summary(
    rows: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    return _q6_shadow_summary(
        rows,
        key,
        prefix="q6_residual_ethan_shipwreck_layout_conditional_shadow",
    )


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
        family = _map_family(row.get("map_id"))
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


def _next_sampling_targets(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(readiness.get("priority_needs") or [])
    rows.sort(
        key=lambda row: (
            0 if row.get("map_family") == "hidden" else 1,
            -int(row.get("needed") or 0),
            str(row.get("hero") or ""),
        )
    )
    out: list[dict[str, Any]] = []
    for row in rows[:6]:
        family = str(row.get("map_family") or "unknown")
        out.append(
            {
                "hero": row.get("hero"),
                "map_family": family,
                "needed": row.get("needed"),
                "reason": (
                    "hidden_cold_start"
                    if family == "hidden"
                    else "coverage_gap"
                ),
            }
        )
    return out


def _q6_shadow_sampling_progress_for_label(
    rows: list[dict[str, Any]],
    *,
    label_key: str,
    label: str,
    sample_scope: str,
    targets_config: tuple[tuple[str, str, int], ...],
) -> dict[str, Any]:
    shadow_rows = [
        row
        for row in rows
        if row.get(label_key) == label
    ]
    counts = Counter(
        (
            str(row.get("hero") or "unknown"),
            _map_family(row.get("map_id")),
        )
        for row in shadow_rows
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
        for hero, family, target in targets_config
    ]
    return {
        "sample_scope": sample_scope,
        "tracked_rows": len(shadow_rows),
        "ready": all(row["ready"] for row in targets),
        "total_needed": sum(row["needed"] for row in targets),
        "targets": targets,
        "priority_needs": [row for row in targets if row["needed"] > 0],
    }


def _q6_shadow_sampling_progress(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profile_b5 = _q6_shadow_sampling_progress_for_label(
        rows,
        label_key="q6_residual_boost_shadow_label",
        label="profile_b5",
        sample_scope="live_profile_b5_logs",
        targets_config=_Q6_SHADOW_SAMPLING_TARGETS,
    )
    aisha_deep_floor1 = _q6_shadow_sampling_progress_for_label(
        rows,
        label_key="q6_residual_deep_floor_shadow_label",
        label="aisha_deep_floor1",
        sample_scope="live_aisha_deep_floor1_logs",
        targets_config=_Q6_DEEP_FLOOR_SHADOW_SAMPLING_TARGETS,
    )
    aisha_hidden_floor15 = _q6_shadow_sampling_progress_for_label(
        rows,
        label_key="q6_residual_hidden_floor_shadow_label",
        label="aisha_hidden_floor15",
        sample_scope="live_aisha_hidden_floor15_logs",
        targets_config=_Q6_HIDDEN_FLOOR_SHADOW_SAMPLING_TARGETS,
    )
    aisha_villa_floor05 = _q6_shadow_sampling_progress_for_label(
        rows,
        label_key="q6_residual_villa_floor_shadow_label",
        label="aisha_villa_floor05",
        sample_scope="live_aisha_villa_floor05_logs",
        targets_config=_Q6_VILLA_FLOOR_SHADOW_SAMPLING_TARGETS,
    )
    ethan_shipwreck_layout_conditional = _q6_shadow_sampling_progress_for_label(
        rows,
        label_key=(
            "q6_residual_ethan_shipwreck_layout_conditional_shadow_label"
        ),
        label="ethan_shipwreck_layout_conditional_c4_cells15",
        sample_scope="live_ethan_shipwreck_layout_conditional_logs",
        targets_config=(
            _Q6_ETHAN_SHIPWRECK_LAYOUT_CONDITIONAL_SHADOW_SAMPLING_TARGETS
        ),
    )
    return {
        **profile_b5,
        "candidates": {
            "profile_b5": profile_b5,
            "aisha_deep_floor1": aisha_deep_floor1,
            "aisha_hidden_floor15": aisha_hidden_floor15,
            "aisha_villa_floor05": aisha_villa_floor05,
            "ethan_shipwreck_layout_conditional_c4_cells15": (
                ethan_shipwreck_layout_conditional
            ),
        },
    }


def _q6_shadow_candidate_readiness(
    rows: list[dict[str, Any]],
    *,
    prefix: str,
    label_key: str,
    label: str,
    progress: dict[str, Any],
) -> dict[str, Any]:
    tracked = [
        row for row in rows
        if row.get(label_key) == label
    ]
    active = [
        row for row in tracked
        if row.get(f"{prefix}_active")
    ]
    active_no_q6 = [
        row for row in active
        if _q6_no_plannable_control(row)
    ]
    active_zero_q6_proven = [
        row for row in active
        if _q6_zero_q6_proven_control(row)
    ]
    under_before = [
        row for row in active
        if row.get(f"{prefix}_under_before")
    ]
    helped = [
        row for row in under_before
        if row.get(f"{prefix}_helped")
    ]
    still_missed = [
        row for row in under_before
        if not row.get(f"{prefix}_helped")
    ]
    false_positive = [
        row for row in active
        if row.get(f"{prefix}_false_positive_proxy")
    ]
    zero_q6_false_positive = [
        row for row in active
        if row.get(f"{prefix}_zero_q6_proven_false_positive")
    ]
    target_ready = bool(progress.get("ready"))
    if not target_ready:
        status = "needs_live_samples"
    elif false_positive:
        status = "blocked_false_positive"
    elif under_before and not helped:
        status = "no_observed_help"
    elif helped:
        status = "candidate_for_review"
    else:
        status = "monitoring"
    return {
        "label": label,
        "sample_scope": progress.get("sample_scope"),
        "status": status,
        "target_ready": target_ready,
        "target_total_needed": progress.get("total_needed"),
        "tracked_rows": len(tracked),
        "active_rows": len(active),
        "active_no_q6_rows": len(active_no_q6),
        "active_zero_q6_proven_rows": len(active_zero_q6_proven),
        "under_before_rows": len(under_before),
        "helped_rows": len(helped),
        "still_missed_rows": len(still_missed),
        "helped_rate": (
            round(len(helped) / len(under_before), 4)
            if under_before
            else None
        ),
        "still_missed_rate": (
            round(len(still_missed) / len(under_before), 4)
            if under_before
            else None
        ),
        "false_positive_proxy_rows": len(false_positive),
        "zero_q6_proven_false_positive_rows": len(zero_q6_false_positive),
        "false_positive_proxy_rate_active": (
            round(len(false_positive) / len(active), 4)
            if active
            else None
        ),
        "false_positive_proxy_rate_active_no_q6": (
            round(len(false_positive) / len(active_no_q6), 4)
            if active_no_q6
            else None
        ),
        "zero_q6_proven_false_positive_rate": (
            round(len(zero_q6_false_positive) / len(active_zero_q6_proven), 4)
            if active_zero_q6_proven
            else None
        ),
        "q6_p90_delta_median": _median_value(active, f"{prefix}_q6_p90_delta"),
        "priority_needs": progress.get("priority_needs") or [],
    }


def _q6_shadow_candidate_readiness_summary(
    rows: list[dict[str, Any]],
    progress: dict[str, Any],
) -> dict[str, Any]:
    candidates = progress.get("candidates") or {}
    return {
        "profile_b5": _q6_shadow_candidate_readiness(
            rows,
            prefix="q6_residual_boost_shadow",
            label_key="q6_residual_boost_shadow_label",
            label="profile_b5",
            progress=candidates.get("profile_b5") or {},
        ),
        "aisha_deep_floor1": _q6_shadow_candidate_readiness(
            rows,
            prefix="q6_residual_deep_floor_shadow",
            label_key="q6_residual_deep_floor_shadow_label",
            label="aisha_deep_floor1",
            progress=candidates.get("aisha_deep_floor1") or {},
        ),
        "aisha_hidden_floor15": _q6_shadow_candidate_readiness(
            rows,
            prefix="q6_residual_hidden_floor_shadow",
            label_key="q6_residual_hidden_floor_shadow_label",
            label="aisha_hidden_floor15",
            progress=candidates.get("aisha_hidden_floor15") or {},
        ),
        "aisha_villa_floor05": _q6_shadow_candidate_readiness(
            rows,
            prefix="q6_residual_villa_floor_shadow",
            label_key="q6_residual_villa_floor_shadow_label",
            label="aisha_villa_floor05",
            progress=candidates.get("aisha_villa_floor05") or {},
        ),
        "ethan_shipwreck_layout_conditional_c4_cells15": (
            _q6_shadow_candidate_readiness(
                rows,
                prefix="q6_residual_ethan_shipwreck_layout_conditional_shadow",
                label_key=(
                    "q6_residual_ethan_shipwreck_layout_conditional_shadow_label"
                ),
                label="ethan_shipwreck_layout_conditional_c4_cells15",
                progress=(
                    candidates.get(
                        "ethan_shipwreck_layout_conditional_c4_cells15"
                    )
                    or {}
                ),
            )
        ),
    }


def _log_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "missing_hero": sum(1 for row in rows if not row.get("hero")),
        "missing_final_value": sum(1 for row in rows if row.get("final_value") is None),
        "missing_final_cells": sum(1 for row in rows if row.get("final_cells") is None),
        "missing_decision_value": sum(
            1 for row in rows if row.get("decision_value_p50") is None
        ),
        "missing_q6_truth_fields": sum(
            1 for row in rows if row.get("final_q6_value") is None
        ),
        "missing_q6_decision_truth_fields": sum(
            1 for row in rows if row.get("final_q6_decision_value") is None
        ),
    }


def _monitor_error_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts: Counter[str] = Counter()
    fingerprint_keys: set[str] = set()
    latest: list[dict[str, Any]] = []
    for row in rows:
        error_type = str(row.get("error_type") or "unknown")
        type_counts[error_type] += 1
        fingerprint = row.get("fingerprint") or {}
        if isinstance(fingerprint, dict):
            key = "|".join(
                str(part)
                for part in (
                    row.get("path") or row.get("name") or "",
                    fingerprint.get("size"),
                    fingerprint.get("mtime_ns"),
                )
            )
        else:
            key = str(row.get("path") or row.get("name") or row.get("ts") or "")
        if key:
            fingerprint_keys.add(key)
        latest.append(
            {
                "ts": row.get("ts"),
                "name": row.get("name"),
                "error_type": error_type,
                "error": str(row.get("error") or "")[:240],
            }
        )
    latest.sort(key=lambda row: float(row.get("ts") or 0), reverse=True)
    return {
        "rows": len(rows),
        "unique_file_fingerprints": len(fingerprint_keys),
        "error_type_counts": dict(sorted(type_counts.items())),
        "latest": latest[:5],
    }


def summarize(
    rows: list[dict[str, Any]],
    *,
    dedupe: bool = True,
    target_per_hero_family: int = 30,
    hidden_target_per_hero: int = 10,
    hidden_target_by_hero: dict[str, int] | None = None,
    monitor_error_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    original_count = len(rows)
    if dedupe:
        rows = _dedupe_latest_by_file(rows)
    valid = [
        _with_derived_information_density(
            _with_derived_q6_fields(
                _with_derived_layout_root(_with_derived_decision_errors(row))
            )
        )
        for row in rows
        if row.get("final_value") is not None or row.get("final_cells") is not None
    ]
    q6_truth = [row for row in valid if int(row.get("final_q6_value") or 0) > 0]
    collection_readiness = _collection_readiness(
        valid,
        target_per_hero_family=target_per_hero_family,
        hidden_target_per_hero=hidden_target_per_hero,
        hidden_target_by_hero=hidden_target_by_hero,
    )
    layout_conflict = [
        row for row in valid
        if row.get("layout_conflict") is True
    ]
    q6_p90_miss = [
        row for row in valid
        if row.get("q6_p90_misses_truth") is True
    ]
    q6_plannable_p90_miss = [
        row for row in valid
        if row.get("q6_plannable_p90_misses_truth") is True
    ]
    q6_tail_replacement_p90_miss = [
        row for row in valid
        if row.get("q6_tail_replacement_p90_misses_truth") is True
    ]
    q6_tail_replacement_estimate_p90_miss = [
        row for row in valid
        if row.get("q6_tail_replacement_estimate_p90_misses_truth") is True
    ]
    has_q6_plannable_metrics = any(
        row.get("q6_plannable_p90_misses_truth") is not None
        for row in valid
    )
    has_q6_tail_replacement_metrics = any(
        row.get("q6_tail_replacement_p90_misses_truth") is not None
        for row in valid
    )
    has_q6_tail_replacement_estimate_metrics = any(
        row.get("q6_tail_replacement_estimate_p90_misses_truth") is not None
        for row in valid
    )
    q6_shadow_sampling_progress = _q6_shadow_sampling_progress(valid)
    return {
        "rows": len(rows),
        "raw_rows": original_count,
        "deduped_rows": original_count - len(rows),
        "valid": len(valid),
        "q6_truth_rows": len(q6_truth),
        "decision_value_mae": _mae(valid, "decision_value_p50_error"),
        "decision_value_median_abs_error": _median_abs(
            valid,
            "decision_value_p50_error",
        ),
        "raw_value_mae": _mae(valid, "value_p50_error"),
        "raw_ceiling_gap_median": _median_value(valid, "raw_minus_decision_p90"),
        "raw_ceiling_gap_p75": _p75_value(valid, "raw_minus_decision_p90"),
        "raw_ceiling_gap_250k_count": sum(
            1 for row in valid
            if (_numeric(row, "raw_minus_decision_p90") or 0) >= 250_000
        ),
        "raw_ceiling_gap_700k_count": sum(
            1 for row in valid
            if (_numeric(row, "raw_minus_decision_p90") or 0) >= 700_000
        ),
        "warehouse_mae": _mae(valid, "warehouse_p50_error"),
        "layout_fit_mae": _mae(valid, "layout_fit_p50_error"),
        "monitor_processing_seconds_median": _median_float_value(
            valid,
            "monitor_processing_seconds",
        ),
        "monitor_processing_seconds_p75": _p75_float_value(
            valid,
            "monitor_processing_seconds",
        ),
        "monitor_n_trials_values": _numeric_distribution(valid, "monitor_n_trials"),
        "monitor_shadow_trials_values": _numeric_distribution(
            valid,
            "monitor_shadow_trials",
        ),
        "monitor_roi_trials_values": _numeric_distribution(
            valid,
            "monitor_roi_trials",
        ),
        "category_target_rows": sum(
            1 for row in valid
            if (_numeric(row, "category_target_count") or 0) > 0
        ),
        "category_exclusion_rows": sum(
            1 for row in valid
            if (_numeric(row, "category_exclusion_count") or 0) > 0
        ),
        "category_target_total": sum(
            int(_numeric(row, "category_target_count") or 0)
            for row in valid
        ),
        "category_exclusion_total": sum(
            int(_numeric(row, "category_exclusion_count") or 0)
            for row in valid
        ),
        "log_quality": _log_quality(valid),
        "monitor_errors": _monitor_error_summary(monitor_error_rows or []),
        "collection_readiness": collection_readiness,
        "q6_shadow_sampling_progress": q6_shadow_sampling_progress,
        "q6_shadow_candidate_readiness": _q6_shadow_candidate_readiness_summary(
            valid,
            q6_shadow_sampling_progress,
        ),
        "v3_practical": _v3_practical_summary(valid),
        "next_sampling_targets": _next_sampling_targets(collection_readiness),
        "q6_false_low_count": sum(
            1 for row in valid if row.get("q6_false_low_risk") is True
        ),
        "q6_below_drop_prior_count": sum(
            1 for row in valid if row.get("q6_below_drop_prior") is True
        ),
        "q6_count_cell_prior_risk_count": sum(
            1 for row in valid if row.get("q6_count_cell_prior_risk") is True
        ),
        "q6_count_cell_prior_floor_median": _median_value(
            [
                row for row in valid
                if row.get("q6_count_cell_prior_risk") is True
            ],
            "q6_count_cell_prior_floor_value",
        ),
        "q6_practical_gate_count": sum(
            1 for row in valid if row.get("q6_practical_gate")
        ),
        "q6_practical_p90_median": _median_value(
            [
                row for row in valid
                if row.get("q6_practical_gate")
            ],
            "q6_practical_p90",
        ),
        "q6_practical_gate_under_before_count": sum(
            1 for row in valid if row.get("q6_practical_gate_under_before")
        ),
        "q6_practical_gate_helped_count": sum(
            1 for row in valid if row.get("q6_practical_gate_helped")
        ),
        "q6_practical_gate_false_positive_proxy_count": sum(
            1 for row in valid
            if row.get("q6_practical_gate_false_positive_proxy")
        ),
        "q6_practical_gate_zero_q6_proven_false_positive_count": sum(
            1 for row in valid
            if row.get("q6_practical_gate_zero_q6_proven_false_positive")
        ),
        "q6_practical_p90_under_by_median": _median_value(
            [
                row for row in valid
                if row.get("q6_practical_gate_hit")
            ],
            "q6_practical_p90_under_by",
        ),
        "q6_residual_boost_shadow_active_count": sum(
            1 for row in valid
            if row.get("q6_residual_boost_shadow_active")
        ),
        "q6_residual_boost_shadow_under_before_count": sum(
            1 for row in valid
            if row.get("q6_residual_boost_shadow_under_before")
        ),
        "q6_residual_boost_shadow_helped_count": sum(
            1 for row in valid
            if row.get("q6_residual_boost_shadow_helped")
        ),
        "q6_residual_boost_shadow_false_positive_proxy_count": sum(
            1 for row in valid
            if row.get("q6_residual_boost_shadow_false_positive_proxy")
        ),
        "q6_residual_boost_shadow_zero_q6_proven_false_positive_count": sum(
            1 for row in valid
            if row.get("q6_residual_boost_shadow_zero_q6_proven_false_positive")
        ),
        "q6_residual_boost_shadow_q6_p90_delta_median": _median_value(
            [
                row for row in valid
                if row.get("q6_residual_boost_shadow_active")
            ],
            "q6_residual_boost_shadow_q6_p90_delta",
        ),
        "q6_residual_deep_floor_shadow_active_count": sum(
            1 for row in valid
            if row.get("q6_residual_deep_floor_shadow_active")
        ),
        "q6_residual_deep_floor_shadow_under_before_count": sum(
            1 for row in valid
            if row.get("q6_residual_deep_floor_shadow_under_before")
        ),
        "q6_residual_deep_floor_shadow_helped_count": sum(
            1 for row in valid
            if row.get("q6_residual_deep_floor_shadow_helped")
        ),
        "q6_residual_deep_floor_shadow_false_positive_proxy_count": sum(
            1 for row in valid
            if row.get("q6_residual_deep_floor_shadow_false_positive_proxy")
        ),
        "q6_residual_deep_floor_shadow_zero_q6_proven_false_positive_count": sum(
            1 for row in valid
            if row.get("q6_residual_deep_floor_shadow_zero_q6_proven_false_positive")
        ),
        "q6_residual_deep_floor_shadow_q6_p90_delta_median": _median_value(
            [
                row for row in valid
                if row.get("q6_residual_deep_floor_shadow_active")
            ],
            "q6_residual_deep_floor_shadow_q6_p90_delta",
        ),
        "q6_residual_hidden_floor_shadow_active_count": sum(
            1 for row in valid
            if row.get("q6_residual_hidden_floor_shadow_active")
        ),
        "q6_residual_hidden_floor_shadow_under_before_count": sum(
            1 for row in valid
            if row.get("q6_residual_hidden_floor_shadow_under_before")
        ),
        "q6_residual_hidden_floor_shadow_helped_count": sum(
            1 for row in valid
            if row.get("q6_residual_hidden_floor_shadow_helped")
        ),
        "q6_residual_hidden_floor_shadow_false_positive_proxy_count": sum(
            1 for row in valid
            if row.get("q6_residual_hidden_floor_shadow_false_positive_proxy")
        ),
        "q6_residual_hidden_floor_shadow_zero_q6_proven_false_positive_count": sum(
            1 for row in valid
            if row.get("q6_residual_hidden_floor_shadow_zero_q6_proven_false_positive")
        ),
        "q6_residual_hidden_floor_shadow_q6_p90_delta_median": _median_value(
            [
                row for row in valid
                if row.get("q6_residual_hidden_floor_shadow_active")
            ],
            "q6_residual_hidden_floor_shadow_q6_p90_delta",
        ),
        "q6_residual_villa_floor_shadow_active_count": sum(
            1 for row in valid
            if row.get("q6_residual_villa_floor_shadow_active")
        ),
        "q6_residual_villa_floor_shadow_under_before_count": sum(
            1 for row in valid
            if row.get("q6_residual_villa_floor_shadow_under_before")
        ),
        "q6_residual_villa_floor_shadow_helped_count": sum(
            1 for row in valid
            if row.get("q6_residual_villa_floor_shadow_helped")
        ),
        "q6_residual_villa_floor_shadow_false_positive_proxy_count": sum(
            1 for row in valid
            if row.get("q6_residual_villa_floor_shadow_false_positive_proxy")
        ),
        "q6_residual_villa_floor_shadow_zero_q6_proven_false_positive_count": sum(
            1 for row in valid
            if row.get("q6_residual_villa_floor_shadow_zero_q6_proven_false_positive")
        ),
        "q6_residual_villa_floor_shadow_q6_p90_delta_median": _median_value(
            [
                row for row in valid
                if row.get("q6_residual_villa_floor_shadow_active")
            ],
            "q6_residual_villa_floor_shadow_q6_p90_delta",
        ),
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_active_count": sum(
            1 for row in valid
            if row.get(
                "q6_residual_ethan_shipwreck_layout_conditional_shadow_active"
            )
        ),
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_under_before_count": sum(
            1 for row in valid
            if row.get(
                "q6_residual_ethan_shipwreck_layout_conditional_shadow_under_before"
            )
        ),
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_helped_count": sum(
            1 for row in valid
            if row.get(
                "q6_residual_ethan_shipwreck_layout_conditional_shadow_helped"
            )
        ),
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_false_positive_proxy_count": sum(
            1 for row in valid
            if row.get(
                "q6_residual_ethan_shipwreck_layout_conditional_shadow_false_positive_proxy"
            )
        ),
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_zero_q6_proven_false_positive_count": sum(
            1 for row in valid
            if row.get(
                "q6_residual_ethan_shipwreck_layout_conditional_shadow_zero_q6_proven_false_positive"
            )
        ),
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_q6_p90_delta_median": _median_value(
            [
                row for row in valid
                if row.get(
                    "q6_residual_ethan_shipwreck_layout_conditional_shadow_active"
                )
            ],
            "q6_residual_ethan_shipwreck_layout_conditional_shadow_q6_p90_delta",
        ),
        "q6_aisha_bottom_row_risk_count": sum(
            1 for row in valid if row.get("q6_aisha_bottom_row_risk")
        ),
        "q6_quality_only_local_count": sum(
            int(_numeric(row, "q6_quality_only_local_count") or 0)
            for row in valid
        ),
        "q6_quality_only_deep_local_risk_count": sum(
            1 for row in valid if row.get("q6_quality_only_deep_local_risk")
        ),
        "q6_tail_replacement_value_count": sum(
            1 for row in valid
            if int(_numeric(row, "final_q6_tail_replacement_value") or 0) > 0
        ),
        "q6_tail_replacement_value_median": _median_value(
            [
                row for row in valid
                if int(_numeric(row, "final_q6_tail_replacement_value") or 0) > 0
            ],
            "final_q6_tail_replacement_value",
        ),
        "q6_p90_miss_count": sum(
            1 for row in valid if row.get("q6_p90_misses_truth") is True
        ),
        "q6_p90_under_by_median": (
            _round(
                statistics.median(
                    int(row["v2_q6_value_p90_under_by"])
                    for row in q6_p90_miss
                    if row.get("v2_q6_value_p90_under_by") is not None
                )
            )
            if any(
                row.get("v2_q6_value_p90_under_by") is not None
                for row in q6_p90_miss
            )
            else None
        ),
        "q6_plannable_p90_miss_count": (
            len(q6_plannable_p90_miss)
            if has_q6_plannable_metrics
            else None
        ),
        "q6_plannable_p90_under_by_median": (
            _median_value(
                q6_plannable_p90_miss,
                "v2_q6_decision_value_p90_under_by",
            )
            if has_q6_plannable_metrics
            else None
        ),
        "q6_tail_replacement_p90_miss_count": (
            len(q6_tail_replacement_p90_miss)
            if has_q6_tail_replacement_metrics
            else None
        ),
        "q6_tail_replacement_p90_under_by_median": (
            _median_value(
                q6_tail_replacement_p90_miss,
                "v2_q6_tail_replacement_decision_value_p90_under_by",
            )
            if has_q6_tail_replacement_metrics
            else None
        ),
        "q6_tail_replacement_estimate_p90_miss_count": (
            len(q6_tail_replacement_estimate_p90_miss)
            if has_q6_tail_replacement_estimate_metrics
            else None
        ),
        "q6_tail_replacement_estimate_p90_under_by_median": (
            _median_value(
                q6_tail_replacement_estimate_p90_miss,
                "v2_q6_tail_replacement_estimate_p90_under_by",
            )
            if has_q6_tail_replacement_estimate_metrics
            else None
        ),
        "q6_miss_root_causes": _root_cause_summary(
            q6_p90_miss,
            "q6_miss_root",
        ),
        "layout_conflict_count": sum(
            1 for row in valid if row.get("layout_conflict") is True
        ),
        "layout_conflict_root_causes": _root_cause_summary(
            layout_conflict,
            "layout_conflict_root",
        ),
        "relaxed_exact_count": sum(
            1 for row in valid if row.get("relaxed_exact_used") is True
        ),
        "groups": {
            "hero": _group_summary(valid, "hero"),
            "map_family": _group_summary(
                [
                    {
                        **row,
                        "map_family": _map_family(row.get("map_id")),
                    }
                    for row in valid
                ],
                "map_family",
            ),
            "map_id": _group_summary(valid, "map_id"),
            "evidence_stage": _group_summary(valid, "evidence_stage"),
            "information_density": _group_summary(valid, "information_density_band"),
            "hero_information_density": _group_summary(
                valid,
                "hero_information_density",
            ),
            "evidence_profile": _group_summary(valid, "evidence_profile_key"),
        },
        "bid_gap": {
            "hero": _bid_gap_summary(valid, "hero"),
            "map_family": _bid_gap_summary(
                [
                    {
                        **row,
                        "map_family": _map_family(row.get("map_id")),
                    }
                    for row in valid
                ],
                "map_family",
            ),
        },
        "q6_practical_gate": {
            "hero": _q6_practical_gate_summary(valid, "hero"),
            "hero_map_family": _q6_practical_gate_summary(
                [
                    {
                        **row,
                        "hero_map_family": (
                            f"hero={row.get('hero') or 'unknown'}|"
                            f"map_family={_map_family(row.get('map_id'))}"
                        ),
                    }
                    for row in valid
                ],
                "hero_map_family",
            ),
            "map_family": _q6_practical_gate_summary(
                [
                    {
                        **row,
                        "map_family": _map_family(row.get("map_id")),
                    }
                    for row in valid
                ],
                "map_family",
            ),
            "evidence_stage": _q6_practical_gate_summary(valid, "evidence_stage"),
            "information_density": _q6_practical_gate_summary(
                valid,
                "information_density_band",
            ),
        },
        "q6_residual_boost_shadow": {
            "hero": _q6_residual_boost_shadow_summary(valid, "hero"),
            "hero_map_family": _q6_residual_boost_shadow_summary(
                [
                    {
                        **row,
                        "hero_map_family": (
                            f"hero={row.get('hero') or 'unknown'}|"
                            f"map_family={_map_family(row.get('map_id'))}"
                        ),
                    }
                    for row in valid
                ],
                "hero_map_family",
            ),
            "map_family": _q6_residual_boost_shadow_summary(
                [
                    {
                        **row,
                        "map_family": _map_family(row.get("map_id")),
                    }
                    for row in valid
                ],
                "map_family",
            ),
            "evidence_stage": _q6_residual_boost_shadow_summary(
                valid,
                "evidence_stage",
            ),
            "information_density": _q6_residual_boost_shadow_summary(
                valid,
                "information_density_band",
            ),
        },
        "q6_residual_deep_floor_shadow": {
            "hero": _q6_residual_deep_floor_shadow_summary(valid, "hero"),
            "hero_map_family": _q6_residual_deep_floor_shadow_summary(
                [
                    {
                        **row,
                        "hero_map_family": (
                            f"hero={row.get('hero') or 'unknown'}|"
                            f"map_family={_map_family(row.get('map_id'))}"
                        ),
                    }
                    for row in valid
                ],
                "hero_map_family",
            ),
            "map_family": _q6_residual_deep_floor_shadow_summary(
                [
                    {
                        **row,
                        "map_family": _map_family(row.get("map_id")),
                    }
                    for row in valid
                ],
                "map_family",
            ),
            "evidence_stage": _q6_residual_deep_floor_shadow_summary(
                valid,
                "evidence_stage",
            ),
            "information_density": _q6_residual_deep_floor_shadow_summary(
                valid,
                "information_density_band",
            ),
        },
        "q6_residual_hidden_floor_shadow": {
            "hero": _q6_residual_hidden_floor_shadow_summary(valid, "hero"),
            "hero_map_family": _q6_residual_hidden_floor_shadow_summary(
                [
                    {
                        **row,
                        "hero_map_family": (
                            f"hero={row.get('hero') or 'unknown'}|"
                            f"map_family={_map_family(row.get('map_id'))}"
                        ),
                    }
                    for row in valid
                ],
                "hero_map_family",
            ),
            "map_family": _q6_residual_hidden_floor_shadow_summary(
                [
                    {
                        **row,
                        "map_family": _map_family(row.get("map_id")),
                    }
                    for row in valid
                ],
                "map_family",
            ),
            "evidence_stage": _q6_residual_hidden_floor_shadow_summary(
                valid,
                "evidence_stage",
            ),
            "information_density": _q6_residual_hidden_floor_shadow_summary(
                valid,
                "information_density_band",
            ),
        },
        "q6_residual_villa_floor_shadow": {
            "hero": _q6_residual_villa_floor_shadow_summary(valid, "hero"),
            "hero_map_family": _q6_residual_villa_floor_shadow_summary(
                [
                    {
                        **row,
                        "hero_map_family": (
                            f"hero={row.get('hero') or 'unknown'}|"
                            f"map_family={_map_family(row.get('map_id'))}"
                        ),
                    }
                    for row in valid
                ],
                "hero_map_family",
            ),
            "map_family": _q6_residual_villa_floor_shadow_summary(
                [
                    {
                        **row,
                        "map_family": _map_family(row.get("map_id")),
                    }
                    for row in valid
                ],
                "map_family",
            ),
            "evidence_stage": _q6_residual_villa_floor_shadow_summary(
                valid,
                "evidence_stage",
            ),
            "information_density": _q6_residual_villa_floor_shadow_summary(
                valid,
                "information_density_band",
            ),
        },
        "q6_residual_ethan_shipwreck_layout_conditional_shadow": {
            "hero": (
                _q6_residual_ethan_shipwreck_layout_conditional_shadow_summary(
                    valid,
                    "hero",
                )
            ),
            "hero_map_family": (
                _q6_residual_ethan_shipwreck_layout_conditional_shadow_summary(
                    [
                        {
                            **row,
                            "hero_map_family": (
                                f"hero={row.get('hero') or 'unknown'}|"
                                f"map_family={_map_family(row.get('map_id'))}"
                            ),
                        }
                        for row in valid
                    ],
                    "hero_map_family",
                )
            ),
            "map_family": (
                _q6_residual_ethan_shipwreck_layout_conditional_shadow_summary(
                    [
                        {
                            **row,
                            "map_family": _map_family(row.get("map_id")),
                        }
                        for row in valid
                    ],
                    "map_family",
                )
            ),
            "evidence_stage": (
                _q6_residual_ethan_shipwreck_layout_conditional_shadow_summary(
                    valid,
                    "evidence_stage",
                )
            ),
            "information_density": (
                _q6_residual_ethan_shipwreck_layout_conditional_shadow_summary(
                    valid,
                    "information_density_band",
                )
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize live monitor model_eval.jsonl logs.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(ROOT / "data" / "logs" / "live" / "model_eval.jsonl"),
        help="Path to model_eval.jsonl",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Do not collapse duplicate rows with the same file name",
    )
    parser.add_argument(
        "--target-per-hero-family",
        type=int,
        default=30,
        help="Readiness target for each hero x map-family bucket",
    )
    parser.add_argument(
        "--hidden-target-per-hero",
        type=int,
        default=10,
        help="Fallback readiness target for each hero x hidden-auction bucket",
    )
    parser.add_argument(
        "--aisha-hidden-target",
        type=int,
        default=10,
        help="Readiness target for Aisha x hidden-auction",
    )
    parser.add_argument(
        "--ethan-hidden-target",
        type=int,
        default=5,
        help="Readiness target for Ethan x hidden-auction",
    )
    parser.add_argument(
        "--error-log",
        default=None,
        help=(
            "Path to monitor_errors.jsonl. Defaults to monitor_errors.jsonl next "
            "to the selected model_eval.jsonl."
        ),
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="Only include model_eval and monitor error rows newer than this many hours.",
    )
    parser.add_argument(
        "--format",
        choices=("full", "brief"),
        default="full",
        help="Output full JSON summary or a compact live-calibration summary",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="Shortcut for --format brief",
    )
    parser.add_argument(
        "--export-shadow-review-dir",
        default=None,
        help="Optional directory for active q6-shadow candidate CSV/JSONL review rows",
    )
    parser.add_argument(
        "--shadow-review-candidate",
        action="append",
        choices=tuple(_Q6_SHADOW_REVIEW_SPECS),
        default=None,
        help="Limit shadow-review export to one or more candidate labels",
    )
    args = parser.parse_args()
    path = Path(args.path)
    raw_rows = _read_jsonl(path)
    error_log = Path(args.error_log) if args.error_log else path.with_name(
        "monitor_errors.jsonl"
    )
    raw_monitor_errors = _read_jsonl(error_log)
    filter_now = time.time() if args.since_hours is not None else None
    rows, since_ts = _filter_rows_since(
        raw_rows,
        since_hours=args.since_hours,
        now=filter_now,
    )
    monitor_errors, _ = _filter_rows_since(
        raw_monitor_errors,
        since_hours=args.since_hours,
        now=filter_now,
    )
    summary = summarize(
        rows,
        dedupe=not args.no_dedupe,
        target_per_hero_family=args.target_per_hero_family,
        hidden_target_per_hero=args.hidden_target_per_hero,
        hidden_target_by_hero={
            "aisha": args.aisha_hidden_target,
            "ethan": args.ethan_hidden_target,
        },
        monitor_error_rows=monitor_errors,
    )
    if args.since_hours is not None:
        summary["window"] = {
            "since_hours": args.since_hours,
            "since_ts": since_ts,
            "input_rows": len(raw_rows),
            "selected_rows": len(rows),
            "input_monitor_error_rows": len(raw_monitor_errors),
            "selected_monitor_error_rows": len(monitor_errors),
        }
    shadow_review_export = None
    if args.export_shadow_review_dir:
        shadow_review_export = export_shadow_candidate_reviews(
            rows,
            out_dir=Path(args.export_shadow_review_dir),
            candidate_labels=args.shadow_review_candidate,
            dedupe=not args.no_dedupe,
        )
    if args.brief or args.format == "brief":
        summary = brief_summary(summary)
    if shadow_review_export is not None:
        summary["shadow_review_export"] = shadow_review_export
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
