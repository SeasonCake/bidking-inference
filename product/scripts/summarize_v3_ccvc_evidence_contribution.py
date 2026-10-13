"""Audit evidence-feature contributions for v3 CCVC p50 movements."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluate_fatbeans_v3_samples import (  # noqa: E402
    V3CcvOptions,
    _default_calibration_path,
    _default_paths,
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)
from summarize_v3_ccv_direction_audit import (  # noqa: E402
    DEFAULT_COMPONENTS,
    component_fields,
)

DEFAULT_FEATURES = (
    "public_total",
    "public_random_avg",
    "public_max_item_cells",
    "tool_category",
    "item_anchor",
    "shape_anchor",
    "layout",
    "q6_floor",
    "explicit_q6_anchor",
    "unassigned_anchor",
    "public_total+q6_floor",
    "public_random_avg+shape_anchor",
    "public_total+layout",
    "item_anchor+shape_anchor",
)


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _session_id(row: dict[str, Any]) -> str:
    value = row.get("session_id")
    if value not in (None, ""):
        return str(value)
    return str(row.get("file") or "unknown")


def _diagnostic_int(row: dict[str, Any], key: str) -> int:
    text = str(row.get("v3_ccvc_diagnostics") or "")
    match = re.search(rf"(?:^|;){re.escape(key)}=([0-9]+)", text)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _has_q6_floor(row: dict[str, Any]) -> bool:
    return any((
        bool(row.get("v3_summary_q6_count_floor") or 0),
        bool(row.get("v3_summary_q6_cells_floor") or 0),
        bool(row.get("v3_summary_q6_value_floor") or 0),
        row.get("v3_summary_q6_count_exact") is not None,
        row.get("v3_summary_q6_cells_exact") is not None,
        row.get("v3_summary_q6_value_exact") is not None,
    ))


def _feature_flags(row: dict[str, Any]) -> dict[str, bool]:
    profile = str(row.get("evidence_profile_key") or "")
    public_total = (
        "public:total" in profile
        or row.get("v3_summary_session_total_count_exact") is not None
        or row.get("v3_summary_session_total_cells_exact") is not None
    )
    public_random_avg = "public:random_avg" in profile
    public_max_item_cells = "public:max_item_cells" in profile
    tool_category = "tool:category" in profile
    item_anchor = "item" in profile or int(row.get("item_anchors") or 0) > 0
    shape_anchor = "shape" in profile or int(row.get("shape_anchors") or 0) > 0
    layout = "layout" in profile
    q6_floor = _has_q6_floor(row)
    explicit_q6_anchor = _diagnostic_int(row, "ccvc_explicit_q6_anchor_count") > 0
    unassigned_anchor = _diagnostic_int(row, "ccvc_unassigned_anchor_count") > 0
    flags = {
        "public_total": public_total,
        "public_random_avg": public_random_avg,
        "public_max_item_cells": public_max_item_cells,
        "tool_category": tool_category,
        "item_anchor": item_anchor,
        "shape_anchor": shape_anchor,
        "layout": layout,
        "q6_floor": q6_floor,
        "explicit_q6_anchor": explicit_q6_anchor,
        "unassigned_anchor": unassigned_anchor,
    }
    flags["public_total+q6_floor"] = public_total and q6_floor
    flags["public_random_avg+shape_anchor"] = public_random_avg and shape_anchor
    flags["public_total+layout"] = public_total and layout
    flags["item_anchor+shape_anchor"] = item_anchor and shape_anchor
    return flags


def _paired_rows(
    rows: Iterable[dict[str, Any]],
    *,
    component: str,
    candidate_prefix: str,
) -> tuple[dict[str, Any], ...]:
    fields = component_fields(candidate_prefix)
    baseline_key, candidate_key, truth_key = fields[component]
    ready_key = f"{candidate_prefix}ready"
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_available")
        and row.get("v3_post_ready")
        and row.get(ready_key)
        and _float_or_none(row.get(baseline_key)) is not None
        and _float_or_none(row.get(candidate_key)) is not None
        and _float_or_none(row.get(truth_key)) is not None
    )


def _row_eval(
    row: dict[str, Any],
    *,
    component: str,
    candidate_prefix: str,
) -> dict[str, Any] | None:
    baseline_key, candidate_key, truth_key = component_fields(candidate_prefix)[component]
    baseline = _float_or_none(row.get(baseline_key))
    candidate = _float_or_none(row.get(candidate_key))
    truth = _float_or_none(row.get(truth_key))
    if baseline is None or candidate is None or truth is None:
        return None
    prediction_delta = candidate - baseline
    baseline_error = baseline - truth
    candidate_error = candidate - truth
    baseline_abs = abs(baseline_error)
    candidate_abs = abs(candidate_error)
    if abs(prediction_delta) <= 1e-9:
        effect = "unchanged"
    elif candidate_abs < baseline_abs - 1e-9:
        effect = "helped"
    elif candidate_abs > baseline_abs + 1e-9:
        effect = "hurt"
    else:
        effect = "neutral"
    if baseline_error < 0:
        baseline_side = "under"
    elif baseline_error > 0:
        baseline_side = "over"
    else:
        baseline_side = "exact"
    if prediction_delta > 0:
        move = "up"
    elif prediction_delta < 0:
        move = "down"
    else:
        move = "flat"
    return {
        "session_id": _session_id(row),
        "component": component,
        "baseline": baseline,
        "candidate": candidate,
        "truth": truth,
        "prediction_delta": prediction_delta,
        "baseline_abs_error": baseline_abs,
        "candidate_abs_error": candidate_abs,
        "effect": effect,
        "baseline_side": baseline_side,
        "move": move,
        "directional_error": (
            (baseline_side == "under" and move == "down")
            or (baseline_side == "over" and move == "up")
        ),
        "flags": _feature_flags(row),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _status(
    row: dict[str, Any],
    *,
    min_changed: int,
    max_hurt_rate: float,
    max_directional_error_rate: float,
) -> str:
    if int(row["changed_rows"]) < min_changed:
        return "low_movement"
    hurt_rate = row.get("hurt_rate_changed")
    directional = row.get("directional_error_rate_changed")
    if (
        hurt_rate is not None
        and float(hurt_rate) > float(max_hurt_rate)
    ) or (
        directional is not None
        and float(directional) > float(max_directional_error_rate)
    ):
        return "blocked_directional_hurt"
    delta = row.get("mae_delta")
    if delta is not None and float(delta) < 0.0:
        return "positive_contribution"
    return "neutral_or_hurt"


def _metrics(
    evals: Iterable[dict[str, Any]],
    *,
    component: str,
    feature: str,
    feature_present: bool | None,
    min_changed: int,
    max_hurt_rate: float,
    max_directional_error_rate: float,
) -> dict[str, Any]:
    rows = tuple(evals)
    changed = tuple(row for row in rows if row["move"] != "flat")
    baseline_mae = _mean(row["baseline_abs_error"] for row in rows)
    candidate_mae = _mean(row["candidate_abs_error"] for row in rows)
    effect_counts = Counter(str(row["effect"]) for row in rows)
    changed_effect_counts = Counter(str(row["effect"]) for row in changed)
    move_counts = Counter(str(row["move"]) for row in changed)
    side_counts = Counter(str(row["baseline_side"]) for row in rows)
    directional_errors = sum(1 for row in changed if row["directional_error"])
    out: dict[str, Any] = {
        "component": component,
        "feature": feature,
        "feature_present": feature_present,
        "n": len(rows),
        "sessions": len({str(row["session_id"]) for row in rows}),
        "changed_rows": len(changed),
        "changed_rate": _round_metric(_rate(len(changed), len(rows)), 6),
        "baseline_mae": _round_metric(baseline_mae, 3),
        "candidate_mae": _round_metric(candidate_mae, 3),
        "mae_delta": _round_metric(
            candidate_mae - baseline_mae
            if candidate_mae is not None and baseline_mae is not None
            else None,
            3,
        ),
        "prediction_delta_mean": _round_metric(
            _mean(row["prediction_delta"] for row in rows),
            3,
        ),
        "prediction_delta_changed_mean": _round_metric(
            _mean(row["prediction_delta"] for row in changed),
            3,
        ),
        "helped_rows": effect_counts.get("helped", 0),
        "hurt_rows": effect_counts.get("hurt", 0),
        "helped_changed": changed_effect_counts.get("helped", 0),
        "hurt_changed": changed_effect_counts.get("hurt", 0),
        "hurt_rate_changed": _round_metric(
            _rate(changed_effect_counts.get("hurt", 0), len(changed)),
            6,
        ),
        "directional_error_rows": directional_errors,
        "directional_error_rate_changed": _round_metric(
            _rate(directional_errors, len(changed)),
            6,
        ),
        "baseline_under_rate": _round_metric(
            _rate(side_counts.get("under", 0), len(rows)),
            6,
        ),
        "baseline_over_rate": _round_metric(
            _rate(side_counts.get("over", 0), len(rows)),
            6,
        ),
        "move_up_changed": move_counts.get("up", 0),
        "move_down_changed": move_counts.get("down", 0),
    }
    out["status"] = _status(
        out,
        min_changed=min_changed,
        max_hurt_rate=max_hurt_rate,
        max_directional_error_rate=max_directional_error_rate,
    )
    return out


def summarize_contributions(
    rows: Iterable[dict[str, Any]],
    *,
    components: Iterable[str] = ("q6_count", "q6_cells"),
    features: Iterable[str] = DEFAULT_FEATURES,
    candidate_prefix: str = "v3_ccvc_",
    min_changed: int = 5,
    max_hurt_rate: float = 0.45,
    max_directional_error_rate: float = 0.35,
) -> dict[str, Any]:
    selected_components = tuple(components)
    selected_features = tuple(features)
    component_results: list[dict[str, Any]] = []
    feature_results: list[dict[str, Any]] = []
    for component in selected_components:
        paired = _paired_rows(
            rows,
            component=component,
            candidate_prefix=candidate_prefix,
        )
        evals = tuple(
            item
            for row in paired
            if (item := _row_eval(
                row,
                component=component,
                candidate_prefix=candidate_prefix,
            )) is not None
        )
        component_results.append(
            _metrics(
                evals,
                component=component,
                feature="all",
                feature_present=None,
                min_changed=min_changed,
                max_hurt_rate=max_hurt_rate,
                max_directional_error_rate=max_directional_error_rate,
            )
        )
        for feature in selected_features:
            present = tuple(row for row in evals if row["flags"].get(feature))
            absent = tuple(row for row in evals if not row["flags"].get(feature))
            present_metrics = _metrics(
                present,
                component=component,
                feature=feature,
                feature_present=True,
                min_changed=min_changed,
                max_hurt_rate=max_hurt_rate,
                max_directional_error_rate=max_directional_error_rate,
            )
            absent_metrics = _metrics(
                absent,
                component=component,
                feature=feature,
                feature_present=False,
                min_changed=min_changed,
                max_hurt_rate=max_hurt_rate,
                max_directional_error_rate=max_directional_error_rate,
            )
            present_metrics["absent_mae_delta"] = absent_metrics["mae_delta"]
            present_metrics["present_minus_absent_mae_delta"] = _round_metric(
                (
                    float(present_metrics["mae_delta"])
                    - float(absent_metrics["mae_delta"])
                )
                if present_metrics["mae_delta"] is not None
                and absent_metrics["mae_delta"] is not None
                else None,
                3,
            )
            present_metrics["feature_rate"] = _round_metric(
                _rate(len(present), len(evals)),
                6,
            )
            feature_results.append(present_metrics)
    status_counts = Counter(str(row["status"]) for row in feature_results)
    feature_results.sort(
        key=lambda row: (
            row["status"] != "blocked_directional_hurt",
            -float(row.get("hurt_rate_changed") or 0.0),
            float(row.get("mae_delta") or 0.0),
            str(row["component"]),
            str(row["feature"]),
        )
    )
    return {
        "candidate_prefix": candidate_prefix,
        "components": selected_components,
        "features": selected_features,
        "min_changed": int(min_changed),
        "max_hurt_rate": _round_metric(max_hurt_rate, 6),
        "max_directional_error_rate": _round_metric(max_directional_error_rate, 6),
        "status_counts": dict(sorted(status_counts.items())),
        "component_results": component_results,
        "feature_results": feature_results,
    }


def _print_summary(result: dict[str, Any], *, top: int) -> None:
    print(
        " ".join((
            f"candidate_prefix={result['candidate_prefix']}",
            "components=" + ",".join(result["components"]),
            "status_counts="
            + ",".join(
                f"{status}:{count}"
                for status, count in result["status_counts"].items()
            ),
        ))
    )
    for row in result["component_results"]:
        print(
            " ".join((
                f"component={row['component']}",
                f"rows={row['n']}",
                f"changed={row['changed_rows']}",
                f"delta={row['mae_delta']}",
                f"pred_delta={row['prediction_delta_changed_mean']}",
                f"hurt_rate={row['hurt_rate_changed']}",
                f"directional_error={row['directional_error_rate_changed']}",
            ))
        )
    for row in result["feature_results"][:top]:
        print(
            " ".join((
                f"feature={row['component']}:{row['feature']}",
                f"status={row['status']}",
                f"rate={row['feature_rate']}",
                f"rows={row['n']}",
                f"changed={row['changed_rows']}",
                f"delta={row['mae_delta']}",
                f"absent_delta={row['absent_mae_delta']}",
                f"present_minus_absent={row['present_minus_absent_mae_delta']}",
                f"pred_delta={row['prediction_delta_changed_mean']}",
                f"hurt_rate={row['hurt_rate_changed']}",
                f"directional_error={row['directional_error_rate_changed']}",
                f"baseline_under={row['baseline_under_rate']}",
            ))
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit evidence-feature contributions for v3 CCVC movements.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--component",
        action="append",
        choices=tuple(component_fields("v3_ccvc_")),
        help="Component to audit. Can be passed more than once.",
    )
    parser.add_argument(
        "--feature",
        action="append",
        choices=DEFAULT_FEATURES,
        help="Evidence feature to audit. Can be passed more than once.",
    )
    parser.add_argument("--candidate-prefix", default="v3_ccvc_")
    parser.add_argument(
        "--ccv-component-freeze-cells",
        action="store_true",
        help="When using v3_ccvc_, keep q6 cells at the baseline posterior.",
    )
    parser.add_argument("--min-changed", type=int, default=5)
    parser.add_argument("--max-hurt-rate", type=float, default=0.45)
    parser.add_argument("--max-directional-error-rate", type=float, default=0.35)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    parser.add_argument("--posterior-trials", type=int, default=512)
    parser.add_argument("--posterior-seed", type=int, default=0)
    args = parser.parse_args(argv)

    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=load_monitor_tables(),
        calibration_entries=load_prior_calibration_entries(
            _default_calibration_path()
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
        ccv_options=V3CcvOptions(
            component_likelihood=args.candidate_prefix == "v3_ccvc_",
            component_move_cells=not args.ccv_component_freeze_cells,
        ),
    )
    result = {
        "errors": errors,
        **summarize_contributions(
            rows,
            components=args.component or ("q6_count", "q6_cells"),
            features=args.feature or DEFAULT_FEATURES,
            candidate_prefix=args.candidate_prefix,
            min_changed=args.min_changed,
            max_hurt_rate=args.max_hurt_rate,
            max_directional_error_rate=args.max_directional_error_rate,
        ),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_summary(result, top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
