"""Audit v3 prior robustness, activity, and prior-stress slices."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_calibration_path,
    _default_paths,
    _default_tail_value_review_path,
    _default_underestimate_repair_path,
    _float_or_none,
    _mean,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)


DEFAULT_GROUP_FIELDS: tuple[str, ...] = (
    "v3_robust_status",
    "v3_robust_reason",
    "v3_robust_fallback_mode",
    "map_id",
    "hero_map_evidence_profile",
)
SUMMARY_COMPONENTS: tuple[str, ...] = (
    "total_cells",
    "q6_cells",
    "total_value",
    "q6_value",
)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _reason_tokens(row: dict[str, Any]) -> tuple[str, ...]:
    text = str(row.get("v3_robust_reasons") or "").strip()
    if not text:
        return ("none",)
    return tuple(token.strip() for token in text.split(";") if token.strip()) or ("none",)


def _group_values(row: dict[str, Any], field: str) -> tuple[str, ...]:
    if field == "v3_robust_reason":
        return _reason_tokens(row)
    value = row.get(field)
    return (str(value) if value not in (None, "") else "none",)


def _ready_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(row for row in rows if row.get("status") == "ready")


def _metric_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    )


def _pairs(
    rows: Iterable[dict[str, Any]],
    pred_key: str,
    truth_key: str,
) -> tuple[tuple[float, float], ...]:
    pairs: list[tuple[float, float]] = []
    for row in rows:
        pred = _float_or_none(row.get(pred_key))
        truth = _float_or_none(row.get(truth_key))
        if pred is not None and truth is not None:
            pairs.append((pred, truth))
    return tuple(pairs)


def _mae(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(abs(pred - truth) for pred, truth in pairs)


def _bias(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(pred - truth for pred, truth in pairs)


def _below_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred < truth else 0.0 for pred, truth in pairs)


def _coverage_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)


def _top_counts(
    rows: Iterable[dict[str, Any]],
    field: str,
    *,
    top: int = 5,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = row.get(field)
        counts[str(value) if value not in (None, "") else "none"] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _reason_counts(rows: Iterable[dict[str, Any]], *, top: int = 8) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(_reason_tokens(row))
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _constraint_target(row: dict[str, Any], exact_key: str, floor_key: str) -> float | None:
    values = [
        value
        for value in (
            _float_or_none(row.get(exact_key)),
            _float_or_none(row.get(floor_key)),
        )
        if value is not None and value > 0.0
    ]
    return max(values) if values else None


def _constraint_source_and_target(
    row: dict[str, Any],
    exact_key: str,
    floor_key: str,
) -> tuple[str, float | None]:
    exact = _float_or_none(row.get(exact_key))
    floor = _float_or_none(row.get(floor_key))
    if exact is not None and exact > 0.0:
        if floor is not None and floor > exact:
            return ("floor_over_exact", floor)
        return ("exact", exact)
    if floor is not None and floor > 0.0:
        return ("floor", floor)
    return ("none", None)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0.0:
        return None
    return numerator / denominator


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _ratio_values(
    rows: Iterable[dict[str, Any]],
    *,
    exact_key: str,
    floor_key: str,
    expected_key: str,
) -> tuple[float, ...]:
    values: list[float] = []
    for row in rows:
        target = _constraint_target(row, exact_key, floor_key)
        expected = _float_or_none(row.get(expected_key))
        if target is None or expected is None or expected <= 0.0:
            continue
        values.append(target / expected)
    return tuple(values)


def _target_detail(
    row: dict[str, Any],
    *,
    exact_key: str,
    floor_key: str,
    prior_key: str,
    truth_key: str,
    post_p50_key: str | None = None,
    post_p90_key: str | None = None,
) -> dict[str, Any]:
    source, target = _constraint_source_and_target(row, exact_key, floor_key)
    prior = _float_or_none(row.get(prior_key))
    truth = _float_or_none(row.get(truth_key))
    post_p50 = _float_or_none(row.get(post_p50_key)) if post_p50_key else None
    post_p90 = _float_or_none(row.get(post_p90_key)) if post_p90_key else None
    return {
        "source": source,
        "target": _round_metric(target, 3),
        "prior_expected": _round_metric(prior, 3),
        "truth": _round_metric(truth, 3),
        "post_p50": _round_metric(post_p50, 3),
        "post_p90": _round_metric(post_p90, 3),
        "target_prior_ratio": _round_metric(_ratio(target, prior), 3),
        "truth_prior_ratio": _round_metric(_ratio(truth, prior), 3),
        "target_truth_delta": _round_metric(_delta(target, truth), 3),
        "post_p50_target_delta": _round_metric(_delta(post_p50, target), 3),
        "post_p90_target_delta": _round_metric(_delta(post_p90, target), 3),
        "post_p50_truth_delta": _round_metric(_delta(post_p50, truth), 3),
        "post_p90_truth_delta": _round_metric(_delta(post_p90, truth), 3),
    }


def _prior_capacity_detail(row: dict[str, Any]) -> dict[str, Any]:
    total_count_source, total_count_target = _constraint_source_and_target(
        row,
        "v3_summary_session_total_count_exact",
        "v3_summary_known_count_floor",
    )
    prior_min = _float_or_none(row.get("v3_prior_items_per_session_min"))
    prior_max = _float_or_none(row.get("v3_prior_items_per_session_max"))
    truth_count = _float_or_none(row.get("v3_truth_item_count"))
    flags: list[str] = []
    if prior_max is not None:
        if total_count_target is not None and total_count_target > prior_max:
            flags.append("target_count_above_prior_max")
        if truth_count is not None and truth_count > prior_max:
            flags.append("truth_count_above_prior_max")
    if prior_min is not None and truth_count is not None and truth_count < prior_min:
        flags.append("truth_count_below_prior_min")
    target_prior_max_delta = _delta(total_count_target, prior_max)
    truth_prior_max_delta = _delta(truth_count, prior_max)
    target_truth_delta = _delta(total_count_target, truth_count)
    target_prior_max_ratio = _ratio(total_count_target, prior_max)
    truth_prior_max_ratio = _ratio(truth_count, prior_max)
    cases = _capacity_cases(
        total_count_source=total_count_source,
        target_prior_max_delta=target_prior_max_delta,
        truth_prior_max_delta=truth_prior_max_delta,
        target_truth_delta=target_truth_delta,
    )
    return {
        "total_count_source": total_count_source,
        "total_count_target": _round_metric(total_count_target, 3),
        "truth_item_count": _round_metric(truth_count, 3),
        "prior_items_per_session_min": _round_metric(prior_min, 3),
        "prior_items_per_session_max": _round_metric(prior_max, 3),
        "target_prior_max_delta": _round_metric(target_prior_max_delta, 3),
        "truth_prior_max_delta": _round_metric(truth_prior_max_delta, 3),
        "target_truth_delta": _round_metric(target_truth_delta, 3),
        "target_prior_max_ratio": _round_metric(target_prior_max_ratio, 3),
        "truth_prior_max_ratio": _round_metric(truth_prior_max_ratio, 3),
        "flags": flags,
        "cases": cases,
    }


def _capacity_cases(
    *,
    total_count_source: str,
    target_prior_max_delta: float | None,
    truth_prior_max_delta: float | None,
    target_truth_delta: float | None,
) -> list[str]:
    target_above_prior = (
        target_prior_max_delta is not None and target_prior_max_delta > 0.0
    )
    truth_above_prior = (
        truth_prior_max_delta is not None and truth_prior_max_delta > 0.0
    )
    target_below_truth = target_truth_delta is not None and target_truth_delta < 0.0
    target_matches_truth = target_truth_delta is not None and target_truth_delta == 0.0
    target_above_truth = target_truth_delta is not None and target_truth_delta > 0.0
    cases: list[str] = []
    if target_above_prior and truth_above_prior and target_matches_truth:
        cases.append("direct_prior_max_conflict")
    if truth_above_prior and target_below_truth:
        cases.append("target_lower_bound_truth_above_prior")
    if target_above_prior and target_below_truth:
        cases.append("target_above_prior_but_below_truth")
    if target_above_prior and target_above_truth:
        cases.append("target_over_truth_capacity_risk")
    if truth_above_prior and total_count_source == "none":
        cases.append("truth_above_prior_without_count_target")
    if target_above_prior and not truth_above_prior:
        cases.append("target_above_prior_without_truth_support")
    if truth_above_prior and not target_above_prior and not target_below_truth:
        cases.append("truth_above_prior_without_target_prior_hit")
    if not cases:
        cases.append("no_capacity_prior_max_case")
    return cases


def _stress_detail_flags(row: dict[str, Any]) -> tuple[str, ...]:
    flags: list[str] = []
    total_cells = row["total_cells"]
    q6_cells = row["q6_cells"]
    capacity = row["item_count_capacity"]
    if total_cells["source"] == "exact" and total_cells["target_truth_delta"] == 0:
        flags.append("total_cells_exact_matches_truth")
    if total_cells["source"].startswith("floor"):
        delta_to_truth = total_cells["target_truth_delta"]
        if delta_to_truth is not None and float(delta_to_truth) > 0.0:
            flags.append("total_cells_floor_above_truth")
    if q6_cells["source"].startswith("floor"):
        delta_to_truth = q6_cells["target_truth_delta"]
        if delta_to_truth is not None and float(delta_to_truth) > 0.0:
            flags.append("q6_cells_floor_above_truth")
    total_cells_post_delta = total_cells["post_p50_truth_delta"]
    if total_cells_post_delta is not None and float(total_cells_post_delta) < 0.0:
        flags.append("posterior_total_cells_under_truth")
    q6_cells_post_delta = q6_cells["post_p50_truth_delta"]
    if q6_cells_post_delta is not None and float(q6_cells_post_delta) < 0.0:
        flags.append("posterior_q6_cells_under_truth")
    total_cells_post_target_delta = total_cells["post_p50_target_delta"]
    if (
        total_cells_post_target_delta is not None
        and float(total_cells_post_target_delta) < 0.0
    ):
        flags.append("posterior_total_cells_below_target")
    q6_cells_post_target_delta = q6_cells["post_p50_target_delta"]
    if (
        q6_cells_post_target_delta is not None
        and float(q6_cells_post_target_delta) < 0.0
    ):
        flags.append("posterior_q6_cells_below_target")
    flags.extend(capacity["flags"])
    return tuple(flags)


def _negative(value: Any) -> bool:
    parsed = _float_or_none(value)
    return parsed is not None and parsed < 0.0


def _positive(value: Any) -> bool:
    parsed = _float_or_none(value)
    return parsed is not None and parsed > 0.0


def _zero(value: Any) -> bool:
    parsed = _float_or_none(value)
    return parsed is not None and parsed == 0.0


def _target_truth_class(
    detail: Mapping[str, Any],
    *,
    component: str,
) -> str:
    source = str(detail.get("source") or "none")
    delta = detail.get("target_truth_delta")
    if source == "none":
        return f"{component}_target_missing"
    source_kind = "floor" if source.startswith("floor") else source
    if _zero(delta):
        return f"{component}_{source_kind}_matches_truth"
    if _negative(delta):
        return f"{component}_{source_kind}_below_truth"
    if _positive(delta):
        return f"{component}_{source_kind}_above_truth"
    return f"{component}_{source_kind}_truth_unknown"


def _capacity_consistency_classes(capacity: Mapping[str, Any]) -> tuple[str, ...]:
    cases = {str(case) for case in capacity.get("cases", ())}
    classes: list[str] = []
    if "direct_prior_max_conflict" in cases:
        classes.append("capacity_direct_prior_max_conflict")
    if any(
        case in cases
        for case in (
            "target_lower_bound_truth_above_prior",
            "truth_above_prior_without_count_target",
            "truth_above_prior_without_target_prior_hit",
        )
    ):
        classes.append("capacity_truth_above_prior_not_targeted")
    if "target_above_prior_but_below_truth" in cases:
        classes.append("capacity_target_above_prior_below_truth")
    if any(
        case in cases
        for case in (
            "target_above_prior_without_truth_support",
            "target_over_truth_capacity_risk",
        )
    ):
        classes.append("capacity_target_not_truth_confirmed")
    if not classes:
        classes.append("capacity_no_prior_max_conflict")
    return tuple(classes)


def _consistency_classes(row: Mapping[str, Any]) -> tuple[str, ...]:
    classes: list[str] = []
    classes.extend(
        _capacity_consistency_classes(row.get("item_count_capacity", {}))
    )
    classes.append(
        _target_truth_class(row.get("total_cells", {}), component="total_cells")
    )
    classes.append(
        _target_truth_class(row.get("q6_cells", {}), component="q6_cells")
    )
    classes.append(
        _target_truth_class(row.get("total_value", {}), component="total_value")
    )
    classes.append(
        _target_truth_class(row.get("q6_value", {}), component="q6_value")
    )
    return tuple(classes)


def _consistency_bucket(row: Mapping[str, Any]) -> str:
    classes = set(_consistency_classes(row))
    if "capacity_direct_prior_max_conflict" in classes:
        return "hard_capacity_conflict"
    if any(
        item.endswith("_above_truth")
        for item in classes
    ) or "capacity_target_not_truth_confirmed" in classes:
        return "target_over_truth_risk"
    if any(
        item in classes
        for item in (
            "capacity_truth_above_prior_not_targeted",
            "capacity_target_above_prior_below_truth",
        )
    ):
        return "lower_bound_under_truth"
    if any(
        item.endswith("_below_truth") or item.endswith("_target_missing")
        for item in classes
    ):
        return "evidence_floor_only"
    if "capacity_no_prior_max_conflict" in classes:
        return "no_capacity_prior_conflict"
    return "mixed_prior_stress"


def _counter_dict(values: Iterable[Any], *, top: int = 8) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _numeric_values(values: Iterable[Any]) -> tuple[float, ...]:
    out: list[float] = []
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            out.append(parsed)
    return tuple(out)


def _numeric_summary(values: Iterable[Any], *, digits: int = 3) -> dict[str, Any]:
    seq = _numeric_values(values)
    if not seq:
        return {"n": 0, "avg": None, "p90": None, "max": None}
    ordered = sorted(seq)
    p90_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.9)))
    return {
        "n": len(seq),
        "avg": _round_metric(_mean(seq), digits),
        "p90": _round_metric(ordered[p90_index], digits),
        "max": _round_metric(max(seq), digits),
    }


def _detail_value(rows: Iterable[dict[str, Any]], name: str, key: str) -> tuple[Any, ...]:
    return tuple(row.get(name, {}).get(key) for row in rows)


def _capacity_value(rows: Iterable[dict[str, Any]], key: str) -> tuple[Any, ...]:
    return tuple(row.get("item_count_capacity", {}).get(key) for row in rows)


def _delta_counts(
    values: Iterable[Any],
    *,
    negative_label: str = "below_truth",
    zero_label: str = "matches_truth",
    positive_label: str = "above_truth",
) -> dict[str, int]:
    counts = {negative_label: 0, zero_label: 0, positive_label: 0}
    for value in _numeric_values(values):
        if value < 0.0:
            counts[negative_label] += 1
        elif value > 0.0:
            counts[positive_label] += 1
        else:
            counts[zero_label] += 1
    return counts


def _component_delta_counts(
    rows: Iterable[dict[str, Any]],
    *,
    key: str,
    negative_label: str = "below_truth",
    zero_label: str = "matches_truth",
    positive_label: str = "above_truth",
) -> dict[str, dict[str, int]]:
    seq = tuple(rows)
    return {
        "total_cells": _delta_counts(
            _detail_value(seq, "total_cells", key),
            negative_label=negative_label,
            zero_label=zero_label,
            positive_label=positive_label,
        ),
        "q6_cells": _delta_counts(
            _detail_value(seq, "q6_cells", key),
            negative_label=negative_label,
            zero_label=zero_label,
            positive_label=positive_label,
        ),
        "total_value": _delta_counts(
            _detail_value(seq, "total_value", key),
            negative_label=negative_label,
            zero_label=zero_label,
            positive_label=positive_label,
        ),
        "q6_value": _delta_counts(
            _detail_value(seq, "q6_value", key),
            negative_label=negative_label,
            zero_label=zero_label,
            positive_label=positive_label,
        ),
    }


def _component_numeric_summary(
    rows: Iterable[dict[str, Any]],
    *,
    key: str,
) -> dict[str, dict[str, Any]]:
    seq = tuple(rows)
    return {
        "total_cells": _numeric_summary(_detail_value(seq, "total_cells", key)),
        "q6_cells": _numeric_summary(_detail_value(seq, "q6_cells", key)),
        "total_value": _numeric_summary(_detail_value(seq, "total_value", key)),
        "q6_value": _numeric_summary(_detail_value(seq, "q6_value", key)),
    }


def _component_issue_label(detail: Mapping[str, Any]) -> str:
    source = str(detail.get("source") or "none")
    delta = detail.get("target_truth_delta")
    if source == "none":
        return "target_missing"
    source_kind = "floor" if source.startswith("floor") else source
    if _zero(delta):
        return f"{source_kind}_matches_truth"
    if _negative(delta):
        return f"{source_kind}_below_truth"
    if _positive(delta):
        return f"{source_kind}_above_truth"
    return f"{source_kind}_truth_unknown"


def _component_issue_counts(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, dict[str, int]]:
    seq = tuple(rows)
    return {
        component: _counter_dict(
            (_component_issue_label(row.get(component, {})) for row in seq),
            top=top,
        )
        for component in SUMMARY_COMPONENTS
    }


def _component_issue_pattern_counts(
    rows: Iterable[dict[str, Any]],
    *,
    predicate: Callable[[str, str], bool],
    top: int = 8,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        components = tuple(
            component
            for component in SUMMARY_COMPONENTS
            if predicate(component, _component_issue_label(row.get(component, {})))
        )
        counts["+".join(components) if components else "none"] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _target_missing_pattern_counts(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, int]:
    return _component_issue_pattern_counts(
        rows,
        predicate=lambda _component, issue: issue == "target_missing",
        top=top,
    )


def _floor_below_truth_pattern_counts(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, int]:
    return _component_issue_pattern_counts(
        rows,
        predicate=lambda _component, issue: issue == "floor_below_truth",
        top=top,
    )


def _exact_with_target_missing_pattern_counts(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, int]:
    def predicate(component: str, issue: str) -> bool:
        return issue == "target_missing" or (
            component == "total_cells" and issue == "exact_matches_truth"
        )

    return _component_issue_pattern_counts(rows, predicate=predicate, top=top)


def _target_missing_components(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        component
        for component in SUMMARY_COMPONENTS
        if _component_issue_label(row.get(component, {})) == "target_missing"
    )


def _evidence_count(row: Mapping[str, Any], field: str) -> float:
    return _float_or_none(row.get("evidence_counts", {}).get(field)) or 0.0


def _target_missing_attribution_tokens(row: Mapping[str, Any]) -> tuple[str, ...]:
    missing = set(_target_missing_components(row))
    tokens: list[str] = []
    if not missing:
        return ()
    for component in SUMMARY_COMPONENTS:
        if component in missing:
            tokens.append(f"{component}_target_missing")
    total_cells_issue = _component_issue_label(row.get("total_cells", {}))
    if total_cells_issue == "exact_matches_truth":
        tokens.append("total_cells_exact_matches_truth")
    elif total_cells_issue == "floor_below_truth":
        tokens.append("total_cells_floor_below_truth")
    if _evidence_count(row, "numeric_constraints") > 0:
        tokens.append("numeric_constraints_present")
    if _evidence_count(row, "item_anchors") > 0:
        tokens.append("item_anchors_present")
    if _evidence_count(row, "shape_anchors") > 0:
        tokens.append("shape_anchors_present")
    if _evidence_count(row, "quality_floor_anchors") > 0:
        tokens.append("quality_floor_anchors_present")
    if {"q6_cells", "total_value", "q6_value"} <= missing:
        tokens.append("q6_and_value_targets_missing")
    if (
        total_cells_issue == "exact_matches_truth"
        and {"q6_cells", "total_value", "q6_value"} <= missing
    ):
        tokens.append("total_cells_exact_q6_value_targets_missing")
    if _evidence_count(row, "shape_anchors") > 0 and "q6_cells" in missing:
        tokens.append("shape_anchors_present_q6_cells_target_missing")
    if _evidence_count(row, "item_anchors") > 0 and (
        "total_value" in missing or "q6_value" in missing
    ):
        tokens.append("item_anchors_present_value_targets_missing")
    if _evidence_count(row, "quality_floor_anchors") > 0 and (
        "total_value" in missing or "q6_value" in missing
    ):
        tokens.append("quality_floor_present_value_targets_missing")
    return tuple(tokens)


def _multi_label_counts(
    values: Iterable[Iterable[str]],
    *,
    top: int = 8,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for tokens in values:
        counts.update(tokens)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _target_missing_attribution_summary(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, Any]:
    seq = tuple(row for row in rows if _target_missing_components(row))
    return {
        "rows": len(seq),
        "map_counts": _counter_dict((row.get("map_id") for row in seq), top=top),
        "hero_map_evidence_profile_counts": _counter_dict(
            (row.get("hero_map_evidence_profile") for row in seq),
            top=top,
        ),
        "missing_component_pattern_counts": _target_missing_pattern_counts(
            seq,
            top=top,
        ),
        "attribution_counts": _multi_label_counts(
            (_target_missing_attribution_tokens(row) for row in seq),
            top=top,
        ),
        "evidence_count_summary": _evidence_count_summary(seq),
        "source_counts": _source_counts(seq, top=top),
    }


def _evidence_count_summary(
    rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    evidence = tuple(row.get("evidence_counts", {}) for row in rows)
    return {
        "numeric_constraints": _numeric_summary(
            row.get("numeric_constraints") for row in evidence
        ),
        "item_anchors": _numeric_summary(row.get("item_anchors") for row in evidence),
        "shape_anchors": _numeric_summary(row.get("shape_anchors") for row in evidence),
        "quality_floor_anchors": _numeric_summary(
            row.get("quality_floor_anchors") for row in evidence
        ),
    }


def _source_counts(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, dict[str, int]]:
    seq = tuple(rows)
    return {
        "total_cells": _counter_dict(_detail_value(seq, "total_cells", "source"), top=top),
        "q6_cells": _counter_dict(_detail_value(seq, "q6_cells", "source"), top=top),
        "total_value": _counter_dict(_detail_value(seq, "total_value", "source"), top=top),
        "q6_value": _counter_dict(_detail_value(seq, "q6_value", "source"), top=top),
    }


def _evidence_floor_only_summary(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, Any]:
    seq = tuple(
        row
        for row in rows
        if row.get("consistency_bucket") == "evidence_floor_only"
    )
    return {
        "rows": len(seq),
        "reason_counts": _counter_dict(
            (reason for row in seq for reason in row.get("reasons", ())),
            top=top,
        ),
        "map_counts": _counter_dict((row.get("map_id") for row in seq), top=top),
        "hero_map_evidence_profile_counts": _counter_dict(
            (row.get("hero_map_evidence_profile") for row in seq),
            top=top,
        ),
        "source_counts": _source_counts(seq, top=top),
        "component_issue_counts": _component_issue_counts(seq, top=top),
        "target_missing_pattern_counts": _target_missing_pattern_counts(
            seq,
            top=top,
        ),
        "floor_below_truth_pattern_counts": _floor_below_truth_pattern_counts(
            seq,
            top=top,
        ),
        "exact_with_target_missing_pattern_counts": (
            _exact_with_target_missing_pattern_counts(seq, top=top)
        ),
        "target_missing_attribution_summary": (
            _target_missing_attribution_summary(seq, top=top)
        ),
        "target_truth_delta_counts": _component_delta_counts(
            seq,
            key="target_truth_delta",
        ),
        "target_truth_delta_summary": _component_numeric_summary(
            seq,
            key="target_truth_delta",
        ),
        "evidence_count_summary": _evidence_count_summary(seq),
    }


def _lower_bound_target_completeness_label(row: Mapping[str, Any]) -> str:
    capacity = row.get("item_count_capacity", {})
    count_source = str(capacity.get("total_count_source") or "none")
    target_prior_delta = _float_or_none(capacity.get("target_prior_max_delta"))
    target_truth_delta = _float_or_none(capacity.get("target_truth_delta"))
    truth_prior_delta = _float_or_none(capacity.get("truth_prior_max_delta"))

    if truth_prior_delta is None or truth_prior_delta <= 0.0:
        return "truth_not_above_prior"
    if count_source == "none":
        return "missing_count_target_truth_above_prior"
    if target_truth_delta is not None and target_truth_delta < 0.0:
        if target_prior_delta is not None and target_prior_delta > 0.0:
            return "count_target_above_prior_but_below_truth"
        if target_prior_delta is not None and target_prior_delta == 0.0:
            return "count_target_matches_prior_but_below_truth"
        return f"{count_source}_count_target_below_prior_and_truth"
    if target_truth_delta is not None and target_truth_delta == 0.0:
        return "count_target_matches_truth"
    if target_truth_delta is not None and target_truth_delta > 0.0:
        return "count_target_above_truth"
    return "count_target_truth_delta_missing"


def _lower_bound_target_completeness_summary(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, Any]:
    seq = tuple(
        row
        for row in rows
        if row.get("consistency_bucket") == "lower_bound_under_truth"
    )
    return {
        "rows": len(seq),
        "reason_counts": _counter_dict(
            (reason for row in seq for reason in row.get("reasons", ())),
            top=top,
        ),
        "map_counts": _counter_dict((row.get("map_id") for row in seq), top=top),
        "hero_map_evidence_profile_counts": _counter_dict(
            (row.get("hero_map_evidence_profile") for row in seq),
            top=top,
        ),
        "target_completeness_class_counts": _counter_dict(
            (_lower_bound_target_completeness_label(row) for row in seq),
            top=top,
        ),
        "source_counts": _source_counts(seq, top=top),
        "component_issue_counts": _component_issue_counts(seq, top=top),
        "target_missing_pattern_counts": _target_missing_pattern_counts(
            seq,
            top=top,
        ),
        "floor_below_truth_pattern_counts": _floor_below_truth_pattern_counts(
            seq,
            top=top,
        ),
        "capacity_count_summary": _capacity_count_summary(seq, top=top),
        "target_truth_delta_counts": _component_delta_counts(
            seq,
            key="target_truth_delta",
        ),
        "target_truth_delta_summary": _component_numeric_summary(
            seq,
            key="target_truth_delta",
        ),
        "evidence_count_summary": _evidence_count_summary(seq),
    }


def _capacity_count_summary(rows: Iterable[dict[str, Any]], *, top: int = 8) -> dict[str, Any]:
    seq = tuple(rows)
    capacity_cases = [
        case
        for row in seq
        for case in row.get("item_count_capacity", {}).get("cases", ())
    ]
    return {
        "case_counts": _counter_dict(capacity_cases, top=top),
        "total_count_source_counts": _counter_dict(
            _capacity_value(seq, "total_count_source"),
            top=top,
        ),
        "target_count": _numeric_summary(_capacity_value(seq, "total_count_target")),
        "truth_item_count": _numeric_summary(_capacity_value(seq, "truth_item_count")),
        "prior_items_per_session_min": _numeric_summary(
            _capacity_value(seq, "prior_items_per_session_min")
        ),
        "prior_items_per_session_max": _numeric_summary(
            _capacity_value(seq, "prior_items_per_session_max")
        ),
        "target_prior_max_delta": _numeric_summary(
            _capacity_value(seq, "target_prior_max_delta")
        ),
        "truth_prior_max_delta": _numeric_summary(
            _capacity_value(seq, "truth_prior_max_delta")
        ),
        "target_truth_delta": _numeric_summary(_capacity_value(seq, "target_truth_delta")),
        "target_prior_max_ratio": _numeric_summary(
            _capacity_value(seq, "target_prior_max_ratio")
        ),
        "truth_prior_max_ratio": _numeric_summary(
            _capacity_value(seq, "truth_prior_max_ratio")
        ),
        "target_prior_max_delta_counts": _delta_counts(
            _capacity_value(seq, "target_prior_max_delta"),
            negative_label="below_prior_max",
            zero_label="matches_prior_max",
            positive_label="above_prior_max",
        ),
        "truth_prior_max_delta_counts": _delta_counts(
            _capacity_value(seq, "truth_prior_max_delta"),
            negative_label="below_prior_max",
            zero_label="matches_prior_max",
            positive_label="above_prior_max",
        ),
        "target_truth_delta_counts": _delta_counts(
            _capacity_value(seq, "target_truth_delta"),
            negative_label="below_truth",
            zero_label="matches_truth",
            positive_label="above_truth",
        ),
    }


def _stress_detail_summary_block(
    rows: Iterable[dict[str, Any]],
    *,
    top: int = 8,
) -> dict[str, Any]:
    seq = tuple(rows)
    capacity_flags = [
        flag
        for row in seq
        for flag in row["item_count_capacity"].get("flags", ())
    ]
    detail_flags = [flag for row in seq for flag in row.get("flags", ())]
    consistency_classes = [
        item for row in seq for item in row.get("consistency_classes", ())
    ]
    consistency_buckets = [
        row.get("consistency_bucket") for row in seq
    ]
    reason_tokens = [reason for row in seq for reason in row.get("reasons", ())]
    return {
        "rows": len(seq),
        "reason_counts": _counter_dict(reason_tokens, top=top),
        "posterior_scope_counts": _counter_dict(
            (row.get("posterior_scope") for row in seq),
            top=top,
        ),
        "fallback_counts": _counter_dict(
            (row.get("fallback_mode") for row in seq),
            top=top,
        ),
        "map_counts": _counter_dict((row.get("map_id") for row in seq), top=top),
        "hero_counts": _counter_dict((row.get("hero") for row in seq), top=top),
        "evidence_profile_counts": _counter_dict(
            (row.get("evidence_profile_key") for row in seq),
            top=top,
        ),
        "hero_map_evidence_profile_counts": _counter_dict(
            (row.get("hero_map_evidence_profile") for row in seq),
            top=top,
        ),
        "source_counts": _source_counts(seq, top=top),
        "capacity_flag_counts": _counter_dict(capacity_flags, top=top),
        "capacity_count_summary": _capacity_count_summary(seq, top=top),
        "detail_flag_counts": _counter_dict(detail_flags, top=top),
        "consistency_class_counts": _counter_dict(
            consistency_classes,
            top=top,
        ),
        "consistency_bucket_counts": _counter_dict(
            consistency_buckets,
            top=top,
        ),
        "evidence_floor_only_summary": _evidence_floor_only_summary(
            seq,
            top=top,
        ),
        "lower_bound_target_completeness_summary": (
            _lower_bound_target_completeness_summary(
                seq,
                top=top,
            )
        ),
        "target_truth_match_counts": {
            "total_cells": sum(
                1
                for row in seq
                if row["total_cells"].get("target_truth_delta") == 0
            ),
            "q6_cells": sum(
                1 for row in seq if row["q6_cells"].get("target_truth_delta") == 0
            ),
            "total_value": sum(
                1
                for row in seq
                if row["total_value"].get("target_truth_delta") == 0
            ),
            "q6_value": sum(
                1 for row in seq if row["q6_value"].get("target_truth_delta") == 0
            ),
        },
        "target_truth_delta_counts": _component_delta_counts(
            seq,
            key="target_truth_delta",
        ),
        "target_truth_delta_summary": _component_numeric_summary(
            seq,
            key="target_truth_delta",
        ),
        "post_p50_truth_delta_summary": _component_numeric_summary(
            seq,
            key="post_p50_truth_delta",
        ),
        "post_p50_target_delta_counts": _component_delta_counts(
            seq,
            key="post_p50_target_delta",
            negative_label="below_target",
            zero_label="matches_target",
            positive_label="above_target",
        ),
        "post_p50_target_delta_summary": _component_numeric_summary(
            seq,
            key="post_p50_target_delta",
        ),
        "post_p90_target_delta_summary": _component_numeric_summary(
            seq,
            key="post_p90_target_delta",
        ),
        "ratio_summary": {
            "total_cells": _numeric_summary(
                _detail_value(seq, "total_cells", "target_prior_ratio")
            ),
            "q6_cells": _numeric_summary(
                _detail_value(seq, "q6_cells", "target_prior_ratio")
            ),
            "total_value": _numeric_summary(
                _detail_value(seq, "total_value", "target_prior_ratio")
            ),
            "q6_value": _numeric_summary(
                _detail_value(seq, "q6_value", "target_prior_ratio")
            ),
        },
        "evidence_count_summary": _evidence_count_summary(seq),
    }


def _summary_group_value(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    return str(value) if value not in (None, "") else "none"


def summarize_prior_stress_detail_groups(
    details: Iterable[dict[str, Any]],
    group_field: str,
    *,
    top: int = 8,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        groups[_summary_group_value(row, group_field)].append(row)
    out: list[dict[str, Any]] = []
    for value, group_rows in groups.items():
        block = _stress_detail_summary_block(group_rows, top=top)
        capacity_hits = sum(block["capacity_flag_counts"].values())
        q6_ratio = block["ratio_summary"]["q6_cells"]["max"] or 0.0
        total_ratio = block["ratio_summary"]["total_cells"]["max"] or 0.0
        value_ratio = max(
            block["ratio_summary"]["total_value"]["max"] or 0.0,
            block["ratio_summary"]["q6_value"]["max"] or 0.0,
        )
        out.append(
            {
                "field": group_field,
                "value": value,
                "capacity_flag_hits": capacity_hits,
                "max_cells_ratio": _round_metric(max(q6_ratio, total_ratio), 3),
                "max_value_ratio": _round_metric(value_ratio, 3),
                **block,
            }
        )
    return sorted(
        out,
        key=lambda row: (
            -int(row["rows"]),
            -int(row["capacity_flag_hits"]),
            -float(row["max_cells_ratio"] or 0.0),
            -float(row["max_value_ratio"] or 0.0),
            str(row["field"]),
            str(row["value"]),
        ),
    )[:top]


def summarize_prior_stress_detail_summary(
    details: Iterable[dict[str, Any]],
    *,
    top: int = 8,
    group_fields: Iterable[str] = (),
) -> dict[str, Any]:
    rows = tuple(details)
    by_reason: list[dict[str, Any]] = []
    for reason, count in _counter_dict(
        (reason for row in rows for reason in row.get("reasons", ())),
        top=top,
    ).items():
        reason_rows = [
            row
            for row in rows
            if reason in tuple(str(item) for item in row.get("reasons", ()))
        ]
        by_reason.append(
            {
                "reason": reason,
                "ready": count,
                **_stress_detail_summary_block(reason_rows, top=top),
            }
        )
    return {
        "overall": _stress_detail_summary_block(rows, top=top),
        "by_reason": by_reason,
        "by_group": [
            row
            for group_field in group_fields
            for row in summarize_prior_stress_detail_groups(
                rows,
                group_field,
                top=top,
            )
        ],
    }


def summarize_prior_stress_details(
    rows: Iterable[dict[str, Any]],
    *,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for source_row in rows:
        if source_row.get("status") != "ready":
            continue
        stress_score = int(source_row.get("v3_robust_prior_stress_score") or 0)
        if stress_score <= 0:
            continue
        reasons = _reason_tokens(source_row)
        if reason and reason not in reasons:
            continue
        row = {
            "file": source_row.get("file"),
            "session_id": source_row.get("session_id"),
            "round": source_row.get("round"),
            "hero": source_row.get("hero"),
            "map_id": source_row.get("map_id"),
            "map_family": source_row.get("map_family"),
            "evidence_profile_key": source_row.get("evidence_profile_key"),
            "hero_map_evidence_profile": source_row.get("hero_map_evidence_profile"),
            "fallback_mode": source_row.get("v3_robust_fallback_mode"),
            "posterior_scope": source_row.get("v3_post_match_scope"),
            "stress_score": stress_score,
            "reasons": list(reasons),
            "evidence_counts": {
                "numeric_constraints": source_row.get("numeric_constraints"),
                "item_anchors": source_row.get("item_anchors"),
                "shape_anchors": source_row.get("shape_anchors"),
                "quality_floor_anchors": source_row.get("quality_floor_anchors"),
            },
            "total_cells": _target_detail(
                source_row,
                exact_key="v3_summary_session_total_cells_exact",
                floor_key="v3_summary_known_cells_floor",
                prior_key="v3_prior_expected_cells",
                truth_key="v3_truth_total_cells",
                post_p50_key="v3_post_total_cells_p50",
                post_p90_key="v3_post_total_cells_p90",
            ),
            "q6_cells": _target_detail(
                source_row,
                exact_key="v3_summary_q6_cells_exact",
                floor_key="v3_summary_q6_cells_floor",
                prior_key="v3_prior_q6_expected_cells",
                truth_key="v3_truth_q6_cells",
                post_p50_key="v3_post_q6_cells_p50",
                post_p90_key="v3_post_q6_cells_p90",
            ),
            "total_value": _target_detail(
                source_row,
                exact_key="__never_exact_total_value__",
                floor_key="v3_summary_known_value_floor",
                prior_key="v3_prior_expected_value",
                truth_key="v3_truth_formal_decision_value",
                post_p50_key="v3_post_formal_decision_value_p50",
                post_p90_key="v3_post_formal_decision_value_p90",
            ),
            "q6_value": _target_detail(
                source_row,
                exact_key="v3_summary_q6_value_exact",
                floor_key="v3_summary_q6_value_floor",
                prior_key="v3_prior_q6_expected_value",
                truth_key="v3_truth_q6_raw_value",
                post_p50_key="v3_post_q6_value_p50",
                post_p90_key="v3_post_q6_value_p90",
            ),
            "item_count_capacity": _prior_capacity_detail(source_row),
        }
        row["flags"] = list(_stress_detail_flags(row))
        row["consistency_classes"] = list(_consistency_classes(row))
        row["consistency_bucket"] = _consistency_bucket(row)
        details.append(row)

    def sort_key(row: dict[str, Any]) -> tuple[float, float, float, str]:
        ratios = [
            float(row[name]["target_prior_ratio"] or 0.0)
            for name in ("total_cells", "q6_cells", "total_value", "q6_value")
        ]
        formal_abs = abs(float(row["total_value"]["post_p50_truth_delta"] or 0.0))
        return (
            -float(row["stress_score"]),
            -max(ratios),
            -formal_abs,
            str(row.get("file") or ""),
        )

    return sorted(details, key=sort_key)


def _audit_flags(row: dict[str, Any]) -> tuple[str, ...]:
    flags: list[str] = []
    if row["activity_candidate"]:
        flags.append("activity_or_new_table")
    if row["prior_stressed"]:
        flags.append("prior_stressed")
    if row["prior_trusted_rate"] is not None and float(row["prior_trusted_rate"]) < 0.5:
        flags.append("mostly_untrusted")
    if row["metric_rows"] == 0:
        flags.append("no_metric_rows")
    if row["formal_p50_below_rate"] is not None and float(row["formal_p50_below_rate"]) >= 0.6:
        flags.append("high_below")
    if row["formal_p90_coverage"] is not None and float(row["formal_p90_coverage"]) < 0.7:
        flags.append("poor_p90")
    return tuple(flags)


def summarize_prior_robustness(
    rows: Iterable[dict[str, Any]],
    group_field: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for value in _group_values(row, group_field):
            groups[value].append(row)

    out: list[dict[str, Any]] = []
    for value, group in groups.items():
        ready = _ready_rows(group)
        metric = _metric_rows(group)
        if not ready:
            continue
        formal_p50 = _pairs(
            metric,
            "v3_post_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
        )
        formal_p90 = _pairs(
            metric,
            "v3_post_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
        )
        q6_count = _pairs(metric, "v3_post_q6_count_p50", "v3_truth_q6_count")
        q6_cells = _pairs(metric, "v3_post_q6_cells_p50", "v3_truth_q6_cells")
        q6_value = _pairs(metric, "v3_post_q6_value_p50", "v3_truth_q6_raw_value")
        stress_scores = tuple(
            int(row.get("v3_robust_prior_stress_score") or 0)
            for row in ready
        )
        q6_count_ratios = _ratio_values(
            ready,
            exact_key="v3_summary_q6_count_exact",
            floor_key="v3_summary_q6_count_floor",
            expected_key="v3_prior_q6_expected_count",
        )
        q6_cells_ratios = _ratio_values(
            ready,
            exact_key="v3_summary_q6_cells_exact",
            floor_key="v3_summary_q6_cells_floor",
            expected_key="v3_prior_q6_expected_cells",
        )
        q6_value_ratios = _ratio_values(
            ready,
            exact_key="v3_summary_q6_value_exact",
            floor_key="v3_summary_q6_value_floor",
            expected_key="v3_prior_q6_expected_value",
        )
        row = {
            "field": group_field,
            "value": value,
            "windows": len(group),
            "ready": len(ready),
            "posterior_ready": sum(1 for item in ready if item.get("v3_post_ready")),
            "metric_rows": len(metric),
            "prior_usable": sum(1 for item in ready if _bool(item.get("v3_robust_prior_usable"))),
            "prior_trusted": sum(1 for item in ready if _bool(item.get("v3_robust_prior_trusted"))),
            "activity_candidate": sum(
                1 for item in ready if _bool(item.get("v3_robust_activity_candidate"))
            ),
            "prior_stressed": sum(
                1
                for item in ready
                if int(item.get("v3_robust_prior_stress_score") or 0) > 0
            ),
            "avg_stress_score": _round_metric(_mean(float(value) for value in stress_scores), 3),
            "max_stress_score": max(stress_scores) if stress_scores else 0,
            "status_counts": _top_counts(ready, "v3_robust_status"),
            "fallback_counts": _top_counts(ready, "v3_robust_fallback_mode"),
            "reason_counts": _reason_counts(ready),
            "posterior_scope_counts": _top_counts(
                (item for item in ready if item.get("v3_post_ready")),
                "v3_post_match_scope",
            ),
            "maps": _top_counts(ready, "map_id"),
            "heroes": _top_counts(ready, "hero"),
            "rounds": _top_counts(ready, "round"),
            "evidence_profiles": _top_counts(ready, "evidence_profile_key"),
            "prior_trusted_rate": _round_metric(
                _mean(1.0 if _bool(item.get("v3_robust_prior_trusted")) else 0.0 for item in ready),
                6,
            ),
            "activity_rate": _round_metric(
                _mean(1.0 if _bool(item.get("v3_robust_activity_candidate")) else 0.0 for item in ready),
                6,
            ),
            "prior_stress_rate": _round_metric(
                _mean(
                    1.0
                    if int(item.get("v3_robust_prior_stress_score") or 0) > 0
                    else 0.0
                    for item in ready
                ),
                6,
            ),
            "formal_p50_mae": _round_metric(_mae(formal_p50), 1),
            "formal_p50_bias": _round_metric(_bias(formal_p50), 1),
            "formal_p50_below_rate": _round_metric(_below_rate(formal_p50), 6),
            "formal_p90_coverage": _round_metric(_coverage_rate(formal_p90), 6),
            "q6_count_p50_mae": _round_metric(_mae(q6_count), 2),
            "q6_cells_p50_mae": _round_metric(_mae(q6_cells), 2),
            "q6_value_p50_mae": _round_metric(_mae(q6_value), 1),
            "q6_count_target_prior_ratio_avg": _round_metric(_mean(q6_count_ratios), 3),
            "q6_count_target_prior_ratio_max": _round_metric(
                max(q6_count_ratios) if q6_count_ratios else None,
                3,
            ),
            "q6_cells_target_prior_ratio_avg": _round_metric(_mean(q6_cells_ratios), 3),
            "q6_cells_target_prior_ratio_max": _round_metric(
                max(q6_cells_ratios) if q6_cells_ratios else None,
                3,
            ),
            "q6_value_target_prior_ratio_avg": _round_metric(_mean(q6_value_ratios), 3),
            "q6_value_target_prior_ratio_max": _round_metric(
                max(q6_value_ratios) if q6_value_ratios else None,
                3,
            ),
        }
        row["flags"] = list(_audit_flags(row))
        out.append(row)
    return sorted(
        out,
        key=lambda item: (
            -int(item["prior_stressed"]),
            -int(item["activity_candidate"]),
            -(item["formal_p50_mae"] or 0.0),
            str(item["field"]),
            str(item["value"]),
        ),
    )


def _print_table(rows: list[dict[str, Any]], *, top: int) -> None:
    for row in rows[:top]:
        flags = "+".join(row["flags"]) if row["flags"] else "normalish"
        reasons = ",".join(f"{key}:{value}" for key, value in row["reason_counts"].items())
        scopes = ",".join(
            f"{key}:{value}" for key, value in row["posterior_scope_counts"].items()
        )
        maps = ",".join(f"{key}:{value}" for key, value in row["maps"].items())
        profiles = ",".join(
            f"{key}:{value}" for key, value in row["evidence_profiles"].items()
        )
        print(
            " ".join(
                (
                    f"{row['field']}={row['value']}",
                    f"ready={row['ready']}",
                    f"post_ready={row['posterior_ready']}",
                    f"metric={row['metric_rows']}",
                    f"trusted={row['prior_trusted']}/{row['ready']}",
                    f"activity={row['activity_candidate']}",
                    f"stressed={row['prior_stressed']}",
                    f"avg_stress={row['avg_stress_score']}",
                    f"mae={row['formal_p50_mae']}",
                    f"bias={row['formal_p50_bias']}",
                    f"below={row['formal_p50_below_rate']}",
                    f"p90_cover={row['formal_p90_coverage']}",
                    f"q6_count_mae={row['q6_count_p50_mae']}",
                    f"q6_cells_mae={row['q6_cells_p50_mae']}",
                    f"q6_value_mae={row['q6_value_p50_mae']}",
                    f"q6_count_ratio={row['q6_count_target_prior_ratio_avg']}",
                    f"q6_cells_ratio={row['q6_cells_target_prior_ratio_avg']}",
                    f"q6_value_ratio={row['q6_value_target_prior_ratio_avg']}",
                    f"scopes={scopes or '-'}",
                    f"maps={maps or '-'}",
                    f"profiles={profiles or '-'}",
                    f"reasons={reasons or '-'}",
                    f"flags={flags}",
                )
            )
        )


def _format_target_detail(label: str, detail: dict[str, Any]) -> str:
    return (
        f"{label}={detail['source']}:{detail['target']}"
        f"/prior={detail['prior_expected']}"
        f"/ratio={detail['target_prior_ratio']}"
        f"/truth={detail['truth']}"
        f"/post50={detail['post_p50']}"
        f"/post90={detail['post_p90']}"
        f"/target_truth_delta={detail['target_truth_delta']}"
        f"/post50_target_delta={detail['post_p50_target_delta']}"
        f"/post50_truth_delta={detail['post_p50_truth_delta']}"
    )


def _print_details(rows: list[dict[str, Any]], *, top: int) -> None:
    for row in rows[:top]:
        evidence = ",".join(
            f"{key}:{value}" for key, value in row["evidence_counts"].items()
        )
        capacity = row["item_count_capacity"]
        capacity_flags = "+".join(capacity["flags"]) if capacity["flags"] else "-"
        flags = "+".join(row["flags"]) if row["flags"] else "-"
        print(
            " ".join(
                (
                    f"file={row['file']}",
                    f"round={row['round']}",
                    f"hero={row['hero']}",
                    f"map={row['map_id']}",
                    f"profile={row['evidence_profile_key']}",
                    f"stress={row['stress_score']}",
                    f"scope={row['posterior_scope']}",
                    f"fallback={row['fallback_mode']}",
                    f"reasons={';'.join(row['reasons'])}",
                    _format_target_detail("total_cells", row["total_cells"]),
                    _format_target_detail("q6_cells", row["q6_cells"]),
                    _format_target_detail("total_value", row["total_value"]),
                    _format_target_detail("q6_value", row["q6_value"]),
                    (
                        "item_count="
                        f"{capacity['total_count_source']}:{capacity['total_count_target']}"
                        f"/truth={capacity['truth_item_count']}"
                        f"/prior_min={capacity['prior_items_per_session_min']}"
                        f"/prior_max={capacity['prior_items_per_session_max']}"
                        f"/target_prior_max_delta={capacity['target_prior_max_delta']}"
                        f"/truth_prior_max_delta={capacity['truth_prior_max_delta']}"
                        f"/target_truth_delta={capacity['target_truth_delta']}"
                        f"/cases={'+'.join(capacity['cases'])}"
                        f"/flags={capacity_flags}"
                    ),
                    f"evidence={evidence}",
                    f"flags={flags}",
                )
            )
        )


def _format_counts(counts: dict[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _format_ratio_summary(summary: dict[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p90={summary['p90']}"
        f"/max={summary['max']}"
    )


def _format_delta_counts(counts: dict[str, int]) -> str:
    return (
        f"below={counts.get('below_truth', 0)}"
        f"/match={counts.get('matches_truth', 0)}"
        f"/above={counts.get('above_truth', 0)}"
    )


def _format_target_absorption_counts(counts: dict[str, int]) -> str:
    return (
        f"below={counts.get('below_target', 0)}"
        f"/match={counts.get('matches_target', 0)}"
        f"/above={counts.get('above_target', 0)}"
    )


def _format_capacity_prior_counts(counts: dict[str, int]) -> str:
    return (
        f"below={counts.get('below_prior_max', 0)}"
        f"/match={counts.get('matches_prior_max', 0)}"
        f"/above={counts.get('above_prior_max', 0)}"
    )


def _format_capacity_count_summary(summary: dict[str, Any]) -> tuple[str, ...]:
    return (
        "capacity_cases=" + _format_counts(summary["case_counts"]),
        "capacity_count_sources="
        + _format_counts(summary["total_count_source_counts"]),
        "capacity_prior_max="
        + _format_ratio_summary(summary["prior_items_per_session_max"]),
        "capacity_target_prior_max_delta="
        + _format_ratio_summary(summary["target_prior_max_delta"]),
        "capacity_truth_prior_max_delta="
        + _format_ratio_summary(summary["truth_prior_max_delta"]),
        "capacity_target_truth_delta="
        + _format_ratio_summary(summary["target_truth_delta"]),
        "capacity_target_prior_counts="
        + _format_capacity_prior_counts(summary["target_prior_max_delta_counts"]),
        "capacity_truth_prior_counts="
        + _format_capacity_prior_counts(summary["truth_prior_max_delta_counts"]),
        "capacity_target_truth_counts="
        + _format_delta_counts(summary["target_truth_delta_counts"]),
    )


def _format_evidence_floor_only_summary(summary: dict[str, Any]) -> tuple[str, ...]:
    missing = summary["target_missing_attribution_summary"]
    return (
        f"evidence_floor_only_rows={summary['rows']}",
        "evidence_floor_only_missing_patterns="
        + _format_counts(summary["target_missing_pattern_counts"]),
        "evidence_floor_only_floor_below_patterns="
        + _format_counts(summary["floor_below_truth_pattern_counts"]),
        "evidence_floor_only_exact_missing_patterns="
        + _format_counts(summary["exact_with_target_missing_pattern_counts"]),
        f"evidence_target_missing_rows={missing['rows']}",
        "evidence_target_missing_maps=" + _format_counts(missing["map_counts"]),
        "evidence_target_missing_attribution="
        + _format_counts(missing["attribution_counts"]),
    )


def _format_lower_bound_target_completeness_summary(
    summary: dict[str, Any],
) -> tuple[str, ...]:
    capacity = summary["capacity_count_summary"]
    return (
        f"lower_bound_rows={summary['rows']}",
        "lower_bound_target_completeness="
        + _format_counts(summary["target_completeness_class_counts"]),
        "lower_bound_capacity_cases="
        + _format_counts(capacity["case_counts"]),
        "lower_bound_count_sources="
        + _format_counts(capacity["total_count_source_counts"]),
        "lower_bound_target_truth_delta="
        + _format_ratio_summary(capacity["target_truth_delta"]),
        "lower_bound_target_truth_counts="
        + _format_delta_counts(capacity["target_truth_delta_counts"]),
        "lower_bound_missing_patterns="
        + _format_counts(summary["target_missing_pattern_counts"]),
        "lower_bound_floor_below_patterns="
        + _format_counts(summary["floor_below_truth_pattern_counts"]),
    )


def _print_detail_summary(summary: dict[str, Any], *, top: int) -> None:
    overall = summary["overall"]
    print(
        " ".join(
            (
                f"rows={overall['rows']}",
                "reasons=" + _format_counts(overall["reason_counts"]),
                "scopes=" + _format_counts(overall["posterior_scope_counts"]),
                "maps=" + _format_counts(overall["map_counts"]),
                "profiles=" + _format_counts(overall["evidence_profile_counts"]),
                "capacity_flags=" + _format_counts(overall["capacity_flag_counts"]),
                "detail_flags=" + _format_counts(overall["detail_flag_counts"]),
                "consistency_buckets="
                + _format_counts(overall["consistency_bucket_counts"]),
                "consistency_classes="
                + _format_counts(overall["consistency_class_counts"]),
                "sources_total_cells="
                + _format_counts(overall["source_counts"]["total_cells"]),
                "sources_q6_cells="
                + _format_counts(overall["source_counts"]["q6_cells"]),
                "target_delta_total_cells="
                + _format_delta_counts(
                    overall["target_truth_delta_counts"]["total_cells"]
                ),
                "target_delta_q6_cells="
                + _format_delta_counts(
                    overall["target_truth_delta_counts"]["q6_cells"]
                ),
                "post50_target_delta_total_cells="
                + _format_target_absorption_counts(
                    overall["post_p50_target_delta_counts"]["total_cells"]
                ),
                "post50_target_delta_q6_cells="
                + _format_target_absorption_counts(
                    overall["post_p50_target_delta_counts"]["q6_cells"]
                ),
                "ratio_total_cells="
                + _format_ratio_summary(overall["ratio_summary"]["total_cells"]),
                "ratio_q6_cells="
                + _format_ratio_summary(overall["ratio_summary"]["q6_cells"]),
                "ratio_total_value="
                + _format_ratio_summary(overall["ratio_summary"]["total_value"]),
                "ratio_q6_value="
                + _format_ratio_summary(overall["ratio_summary"]["q6_value"]),
                *_format_capacity_count_summary(
                    overall["capacity_count_summary"],
                ),
                *_format_evidence_floor_only_summary(
                    overall["evidence_floor_only_summary"],
                ),
                *_format_lower_bound_target_completeness_summary(
                    overall["lower_bound_target_completeness_summary"],
                ),
            )
        )
    )
    for row in summary["by_reason"][:top]:
        print(
            " ".join(
                (
                    f"reason={row['reason']}",
                    f"rows={row['rows']}",
                    "capacity_flags=" + _format_counts(row["capacity_flag_counts"]),
                    "detail_flags=" + _format_counts(row["detail_flag_counts"]),
                    "consistency_buckets="
                    + _format_counts(row["consistency_bucket_counts"]),
                    "consistency_classes="
                    + _format_counts(row["consistency_class_counts"]),
                    "sources_total_cells="
                    + _format_counts(row["source_counts"]["total_cells"]),
                    "sources_q6_cells="
                    + _format_counts(row["source_counts"]["q6_cells"]),
                    "target_delta_total_cells="
                    + _format_delta_counts(
                        row["target_truth_delta_counts"]["total_cells"]
                    ),
                    "target_delta_q6_cells="
                    + _format_delta_counts(
                        row["target_truth_delta_counts"]["q6_cells"]
                    ),
                    "post50_target_delta_total_cells="
                    + _format_target_absorption_counts(
                        row["post_p50_target_delta_counts"]["total_cells"]
                    ),
                    "post50_target_delta_q6_cells="
                    + _format_target_absorption_counts(
                        row["post_p50_target_delta_counts"]["q6_cells"]
                    ),
                    "ratio_total_cells="
                    + _format_ratio_summary(row["ratio_summary"]["total_cells"]),
                    "ratio_q6_cells="
                    + _format_ratio_summary(row["ratio_summary"]["q6_cells"]),
                    "ratio_total_value="
                    + _format_ratio_summary(row["ratio_summary"]["total_value"]),
                    "ratio_q6_value="
                    + _format_ratio_summary(row["ratio_summary"]["q6_value"]),
                    *_format_capacity_count_summary(
                        row["capacity_count_summary"],
                    ),
                )
            )
        )
    for row in summary.get("by_group", ())[:top]:
        print(
            " ".join(
                (
                    f"group={row['field']}:{row['value']}",
                    f"rows={row['rows']}",
                    f"capacity_hits={row['capacity_flag_hits']}",
                    f"max_cells_ratio={row['max_cells_ratio']}",
                    f"max_value_ratio={row['max_value_ratio']}",
                    "reasons=" + _format_counts(row["reason_counts"]),
                    "capacity_flags=" + _format_counts(row["capacity_flag_counts"]),
                    "consistency_buckets="
                    + _format_counts(row["consistency_bucket_counts"]),
                    "consistency_classes="
                    + _format_counts(row["consistency_class_counts"]),
                    "sources_total_cells="
                    + _format_counts(row["source_counts"]["total_cells"]),
                    "sources_q6_cells="
                    + _format_counts(row["source_counts"]["q6_cells"]),
                    "target_delta_total_cells="
                    + _format_delta_counts(
                        row["target_truth_delta_counts"]["total_cells"]
                    ),
                    "target_delta_q6_cells="
                    + _format_delta_counts(
                        row["target_truth_delta_counts"]["q6_cells"]
                    ),
                    "post50_target_delta_total_cells="
                    + _format_target_absorption_counts(
                        row["post_p50_target_delta_counts"]["total_cells"]
                    ),
                    "post50_target_delta_q6_cells="
                    + _format_target_absorption_counts(
                        row["post_p50_target_delta_counts"]["q6_cells"]
                    ),
                    "ratio_total_cells="
                    + _format_ratio_summary(row["ratio_summary"]["total_cells"]),
                    "ratio_q6_cells="
                    + _format_ratio_summary(row["ratio_summary"]["q6_cells"]),
                    "profiles=" + _format_counts(row["evidence_profile_counts"]),
                    *_format_capacity_count_summary(
                        row["capacity_count_summary"],
                    ),
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit v3 prior robustness/activity/prior-stress slices.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--by",
        action="append",
        default=None,
        help="Row field to group by. Use v3_robust_reason to split reason tokens.",
    )
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument(
        "--details",
        type=int,
        default=0,
        help="Print top-N prior-stressed evidence/capacity detail rows.",
    )
    parser.add_argument(
        "--details-reason",
        default=None,
        help="Filter detail rows to a single v3_robust reason token.",
    )
    parser.add_argument(
        "--detail-summary",
        action="store_true",
        help="Print aggregate prior-stress detail consistency summary.",
    )
    parser.add_argument(
        "--detail-summary-top",
        type=int,
        default=8,
        help="Top-N counts/reasons to include in detail summary.",
    )
    parser.add_argument(
        "--detail-summary-by",
        action="append",
        default=None,
        help=(
            "Detail row field to group detail summary by, e.g. map_id, "
            "evidence_profile_key, or hero_map_evidence_profile."
        ),
    )
    args = parser.parse_args(argv)

    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=load_monitor_tables(),
        calibration_entries=load_prior_calibration_entries(
            _default_calibration_path()
        ),
        underestimate_repair_entries=load_underestimate_repair_entries(
            _default_underestimate_repair_path()
        ),
        tail_value_review_entries=load_tail_value_review_entries(
            _default_tail_value_review_path()
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    fields = tuple(args.by or DEFAULT_GROUP_FIELDS)
    audits = [
        item
        for field in fields
        for item in summarize_prior_robustness(rows, field)
    ]
    details = summarize_prior_stress_details(rows, reason=args.details_reason)
    detail_summary = summarize_prior_stress_detail_summary(
        details,
        top=args.detail_summary_top,
        group_fields=tuple(args.detail_summary_by or ()),
    )
    result = {
        "errors": errors,
        "audits": audits,
        "detail_summary": detail_summary,
        "details": details[: args.details] if args.details else [],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        for field in fields:
            print(f"== {field} ==")
            _print_table([row for row in audits if row["field"] == field], top=args.top)
        if args.details:
            suffix = f" reason={args.details_reason}" if args.details_reason else ""
            print(f"== prior_stress_details{suffix} ==")
            _print_details(details, top=args.details)
        if args.detail_summary:
            suffix = f" reason={args.details_reason}" if args.details_reason else ""
            print(f"== prior_stress_detail_summary{suffix} ==")
            _print_detail_summary(detail_summary, top=args.detail_summary_top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
