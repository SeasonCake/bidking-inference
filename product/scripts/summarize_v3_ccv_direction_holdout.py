"""Cross-validate directionally safe v3 CCV p50 movements by session."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
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
    COMPONENT_FIELDS,
    DEFAULT_COMPONENTS,
    MOVEMENT_POLICIES,
    apply_movement_policy,
    component_fields,
    summarize_direction,
)

_COMPONENT_MAE_HURT_THRESHOLDS = {
    "q6_count": 0.05,
    "q6_cells": 0.25,
    "q6_value": 10_000.0,
    "q6_formal": 10_000.0,
}


def _stable_fold(value: Any, folds: int) -> int:
    if folds <= 1:
        return 0
    digest = hashlib.sha1(str(value or "unknown").encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % int(folds)


def _session_id(row: dict[str, Any]) -> str:
    value = row.get("session_id")
    if value not in (None, ""):
        return str(value)
    return str(row.get("file") or "unknown")


def _group_value(row: dict[str, Any], field: str) -> str:
    parts = tuple(part.strip() for part in str(field).split(",") if part.strip())
    if len(parts) > 1:
        return "|".join(f"{part}={_group_value(row, part)}" for part in parts)
    value = row.get(field)
    return str(value) if value not in (None, "") else "unknown"


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _row_eval(
    row: dict[str, Any],
    *,
    group_field: str,
    component: str,
    candidates: dict[tuple[str, str], dict[str, Any]],
    fields: dict[str, tuple[str, str, str]],
    movement_policy: str,
) -> dict[str, Any] | None:
    baseline_key, ccv_key, truth_key = fields[component]
    baseline = _float_or_none(row.get(baseline_key))
    ccv = _float_or_none(row.get(ccv_key))
    truth = _float_or_none(row.get(truth_key))
    if baseline is None or ccv is None or truth is None:
        return None
    group = _group_value(row, group_field)
    applied = (component, group) in candidates
    candidate = (
        apply_movement_policy(
            baseline,
            ccv,
            movement_policy=movement_policy,
        )
        if applied
        else baseline
    )
    prediction_delta = candidate - baseline
    baseline_error = baseline - truth
    candidate_error = candidate - truth
    baseline_abs = abs(baseline_error)
    candidate_abs = abs(candidate_error)
    if not applied or abs(prediction_delta) <= 1e-9:
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
    directional_error = applied and (
        (baseline_side == "under" and move == "down")
        or (baseline_side == "over" and move == "up")
    )
    return {
        "session_id": _session_id(row),
        "group": group,
        "component": component,
        "candidate_applied": applied,
        "truth": truth,
        "baseline": baseline,
        "candidate": candidate,
        "ccv": ccv,
        "baseline_abs_error": baseline_abs,
        "candidate_abs_error": candidate_abs,
        "baseline_below": baseline < truth,
        "candidate_below": candidate < truth,
        "prediction_delta": prediction_delta,
        "effect": effect,
        "directional_error": directional_error,
    }


def _row_evals(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str,
    components: Iterable[str],
    candidates: dict[tuple[str, str], dict[str, Any]],
    candidate_prefix: str,
    fields: dict[str, tuple[str, str, str]],
    movement_policy: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ready_key = f"{candidate_prefix}ready"
    for row in rows:
        if (
            row.get("status") != "ready"
            or not row.get("v3_truth_available")
            or not row.get("v3_post_ready")
            or not row.get(ready_key)
        ):
            continue
        for component in components:
            item = _row_eval(
                row,
                group_field=group_field,
                component=component,
                candidates=candidates,
                fields=fields,
                movement_policy=movement_policy,
            )
            if item is not None:
                out.append(item)
    return out


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _metrics(evals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = tuple(evals)
    candidate_rows = tuple(row for row in rows if row.get("candidate_applied"))
    sessions = {str(row["session_id"]) for row in rows}
    candidate_sessions = {str(row["session_id"]) for row in candidate_rows}
    candidate_groups = {
        f"{row['component']}:{row['group']}" for row in candidate_rows
    }
    baseline_mae = _mean(row["baseline_abs_error"] for row in rows)
    candidate_mae = _mean(row["candidate_abs_error"] for row in rows)
    candidate_only_baseline_mae = _mean(
        row["baseline_abs_error"] for row in candidate_rows
    )
    candidate_only_mae = _mean(row["candidate_abs_error"] for row in candidate_rows)
    hurt_rows = sum(1 for row in candidate_rows if row["effect"] == "hurt")
    helped_rows = sum(1 for row in candidate_rows if row["effect"] == "helped")
    directional_error_rows = sum(1 for row in candidate_rows if row["directional_error"])
    return {
        "n": len(rows),
        "sessions": len(sessions),
        "candidate_rows": len(candidate_rows),
        "candidate_sessions": len(candidate_sessions),
        "candidate_groups": sorted(candidate_groups),
        "baseline_p50_mae": _round_metric(baseline_mae, 3),
        "candidate_p50_mae": _round_metric(candidate_mae, 3),
        "delta_p50_mae": _round_metric(
            candidate_mae - baseline_mae
            if candidate_mae is not None and baseline_mae is not None
            else None,
            3,
        ),
        "baseline_p50_below_rate": _round_metric(
            _mean(1.0 if row["baseline_below"] else 0.0 for row in rows),
            6,
        ),
        "candidate_p50_below_rate": _round_metric(
            _mean(1.0 if row["candidate_below"] else 0.0 for row in rows),
            6,
        ),
        "candidate_only_baseline_p50_mae": _round_metric(
            candidate_only_baseline_mae,
            3,
        ),
        "candidate_only_p50_mae": _round_metric(candidate_only_mae, 3),
        "candidate_only_delta_p50_mae": _round_metric(
            candidate_only_mae - candidate_only_baseline_mae
            if candidate_only_mae is not None
            and candidate_only_baseline_mae is not None
            else None,
            3,
        ),
        "candidate_only_prediction_delta_mean": _round_metric(
            _mean(row["prediction_delta"] for row in candidate_rows),
            3,
        ),
        "candidate_only_helped_rows": helped_rows,
        "candidate_only_hurt_rows": hurt_rows,
        "candidate_only_hurt_rate": _round_metric(
            _rate(hurt_rows, len(candidate_rows)),
            6,
        ),
        "candidate_only_directional_error_rows": directional_error_rows,
        "candidate_only_directional_error_rate": _round_metric(
            _rate(directional_error_rows, len(candidate_rows)),
            6,
        ),
    }


def _status(
    candidate: dict[str, Any],
    *,
    applied_hurts: Iterable[str],
    max_hurt_rate: float,
    max_directional_error_rate: float,
) -> str:
    if int(candidate.get("candidate_rows") or 0) <= 0:
        return "sample_limited"
    if tuple(applied_hurts):
        return "blocked_holdout_directional_hurt"
    delta = candidate.get("candidate_only_delta_p50_mae")
    hurt_rate = candidate.get("candidate_only_hurt_rate")
    directional_error_rate = candidate.get("candidate_only_directional_error_rate")
    if (
        delta is not None
        and float(delta) <= 0.0
        and (hurt_rate is None or float(hurt_rate) <= float(max_hurt_rate))
        and (
            directional_error_rate is None
            or float(directional_error_rate) <= float(max_directional_error_rate)
        )
    ):
        return "watch"
    return "blocked_holdout_directional_hurt"


def _applied_hurt_groups(
    group_results: Iterable[dict[str, Any]],
    *,
    max_hurt_rate: float,
    max_directional_error_rate: float,
) -> list[str]:
    groups: list[str] = []
    for row in group_results:
        component = str(row.get("component") or "")
        threshold = _COMPONENT_MAE_HURT_THRESHOLDS.get(component, 0.0)
        delta = row.get("candidate_only_delta_p50_mae")
        hurt_rate = row.get("candidate_only_hurt_rate")
        directional_error_rate = row.get("candidate_only_directional_error_rate")
        if (
            delta is not None
            and float(delta) > float(threshold)
        ) or (
            hurt_rate is not None
            and float(hurt_rate) > float(max_hurt_rate)
        ) or (
            directional_error_rate is not None
            and float(directional_error_rate) > float(max_directional_error_rate)
        ):
            groups.append(f"{component}:{row.get('group')}")
    return groups


def _candidate_allowed(
    *,
    component: str,
    group: str,
    include_pattern: str | None,
    exclude_pattern: str | None,
) -> bool:
    label = f"{component}:{group}"
    if include_pattern and re.search(include_pattern, label) is None:
        return False
    if exclude_pattern and re.search(exclude_pattern, label) is not None:
        return False
    return True


def summarize_holdout(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = "map_id",
    components: Iterable[str] = DEFAULT_COMPONENTS,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_changed: int = 5,
    max_hurt_rate: float = 0.45,
    max_directional_error_rate: float = 0.35,
    candidate_prefix: str = "v3_ccv_",
    movement_policy: str = "all",
    candidate_include_pattern: str | None = None,
    candidate_exclude_pattern: str | None = None,
) -> dict[str, Any]:
    source_rows = tuple(rows)
    selected_components = tuple(components)
    if movement_policy not in MOVEMENT_POLICIES:
        raise ValueError(f"unknown movement_policy: {movement_policy}")
    fields = component_fields(candidate_prefix)
    fold_count = max(1, int(folds))
    all_evals: list[dict[str, Any]] = []
    folds_out: list[dict[str, Any]] = []
    candidate_status_counts = Counter()
    for fold in range(fold_count):
        train_rows = [
            row
            for row in source_rows
            if _stable_fold(_session_id(row), fold_count) != fold
        ]
        holdout_rows = [
            row
            for row in source_rows
            if _stable_fold(_session_id(row), fold_count) == fold
        ]
        train_candidates = summarize_direction(
            train_rows,
            group_field=group_field,
            components=selected_components,
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_changed=min_changed,
            max_hurt_rate=max_hurt_rate,
            max_directional_error_rate=max_directional_error_rate,
            candidate_prefix=candidate_prefix,
            movement_policy=movement_policy,
        )
        candidate_status_counts.update(str(row["status"]) for row in train_candidates)
        candidates = {
            (str(row["component"]), str(row["group"])): row
            for row in train_candidates
            if row.get("status") == "watch_directional_candidate"
            and _candidate_allowed(
                component=str(row["component"]),
                group=str(row["group"]),
                include_pattern=candidate_include_pattern,
                exclude_pattern=candidate_exclude_pattern,
            )
        }
        evals = _row_evals(
            holdout_rows,
            group_field=group_field,
            components=selected_components,
            candidates=candidates,
            candidate_prefix=candidate_prefix,
            fields=fields,
            movement_policy=movement_policy,
        )
        all_evals.extend(evals)
        fold_metrics = _metrics(evals)
        fold_metrics.update(
            {
                "fold": fold,
                "train_rows": len(train_rows),
                "holdout_rows": len(holdout_rows),
                "train_candidate_groups": [
                    f"{component}:{group}" for component, group in sorted(candidates)
                ],
            }
        )
        folds_out.append(fold_metrics)

    group_evals: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    component_evals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_evals:
        component_evals[str(row["component"])].append(row)
        if row.get("candidate_applied"):
            group_evals[(str(row["component"]), str(row["group"]))].append(row)
    group_results = [
        {
            "component": component,
            "group": group,
            **_metrics(evals),
        }
        for (component, group), evals in sorted(group_evals.items())
    ]
    group_results.sort(
        key=lambda item: (
            -float(item.get("candidate_only_hurt_rate") or 0.0),
            float(item.get("candidate_only_delta_p50_mae") or 0.0),
            str(item["component"]),
            str(item["group"]),
        )
    )
    component_results = [
        {"component": component, **_metrics(evals)}
        for component, evals in sorted(component_evals.items())
    ]
    candidate = _metrics(row for row in all_evals if row.get("candidate_applied"))
    applied_hurts = _applied_hurt_groups(
        group_results,
        max_hurt_rate=max_hurt_rate,
        max_directional_error_rate=max_directional_error_rate,
    )
    result = {
        "group_field": group_field,
        "candidate_prefix": candidate_prefix,
        "movement_policy": movement_policy,
        "candidate_include_pattern": candidate_include_pattern,
        "candidate_exclude_pattern": candidate_exclude_pattern,
        "components": selected_components,
        "folds": fold_count,
        "min_windows": int(min_windows),
        "min_sessions": int(min_sessions),
        "min_changed": int(min_changed),
        "max_hurt_rate": _round_metric(max_hurt_rate, 6),
        "max_directional_error_rate": _round_metric(max_directional_error_rate, 6),
        "overall": _metrics(all_evals),
        "candidate_only": candidate,
        "component_results": component_results,
        "group_results": group_results,
        "applied_direction_hurts_groups": applied_hurts,
        "fold_results": folds_out,
        "train_candidate_status_counts": dict(sorted(candidate_status_counts.items())),
    }
    result["overall_status"] = _status(
        candidate,
        applied_hurts=applied_hurts,
        max_hurt_rate=max_hurt_rate,
        max_directional_error_rate=max_directional_error_rate,
    )
    return result


def _print_summary(result: dict[str, Any], *, top: int) -> None:
    candidate = result["candidate_only"]
    print(
        " ".join(
            (
                f"overall_status={result['overall_status']}",
                f"group_field={result['group_field']}",
                f"candidate_prefix={result['candidate_prefix']}",
                f"movement_policy={result['movement_policy']}",
                f"include={result['candidate_include_pattern']}",
                f"exclude={result['candidate_exclude_pattern']}",
                "components=" + ",".join(result["components"]),
                f"folds={result['folds']}",
                f"rows={result['overall']['n']}",
                f"candidate_rows={candidate['candidate_rows']}",
                "candidate_groups=" + ",".join(candidate["candidate_groups"]),
                f"candidate_delta={candidate['candidate_only_delta_p50_mae']}",
                f"candidate_hurt_rate={candidate['candidate_only_hurt_rate']}",
                "candidate_directional_error="
                f"{candidate['candidate_only_directional_error_rate']}",
                f"candidate_baseline_below={candidate['baseline_p50_below_rate']}",
                f"candidate_below={candidate['candidate_p50_below_rate']}",
                "applied_hurts="
                + ",".join(result["applied_direction_hurts_groups"]),
            )
        )
    )
    print(
        "train_status_counts="
        + ",".join(
            f"{status}:{count}"
            for status, count in result["train_candidate_status_counts"].items()
        )
    )
    for row in result["component_results"]:
        print(
            " ".join(
                (
                    f"component={row['component']}",
                    f"rows={row['n']}",
                    f"candidate_rows={row['candidate_rows']}",
                    f"delta={row['candidate_only_delta_p50_mae']}",
                    f"hurt_rate={row['candidate_only_hurt_rate']}",
                    f"directional_error={row['candidate_only_directional_error_rate']}",
                )
            )
        )
    for row in result["group_results"][:top]:
        print(
            " ".join(
                (
                    f"group={row['component']}:{row['group']}",
                    f"rows={row['candidate_rows']}",
                    f"sessions={row['candidate_sessions']}",
                    f"delta={row['candidate_only_delta_p50_mae']}",
                    f"pred_delta={row['candidate_only_prediction_delta_mean']}",
                    f"helped={row['candidate_only_helped_rows']}",
                    f"hurt={row['candidate_only_hurt_rows']}",
                    f"hurt_rate={row['candidate_only_hurt_rate']}",
                    "directional_error="
                    f"{row['candidate_only_directional_error_rate']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-validate directionally safe v3 CCV movements.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--group-field", default="map_id")
    parser.add_argument(
        "--candidate-prefix",
        default="v3_ccv_",
        help="Candidate flat-field prefix, for example v3_ccv_ or v3_ccvc_.",
    )
    parser.add_argument(
        "--ccv-component-freeze-cells",
        action="store_true",
        help="When using v3_ccvc_, keep q6 cells at the baseline posterior.",
    )
    parser.add_argument(
        "--component",
        action="append",
        choices=tuple(COMPONENT_FIELDS),
        help="Component to audit. Can be passed more than once.",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-changed", type=int, default=5)
    parser.add_argument("--max-hurt-rate", type=float, default=0.45)
    parser.add_argument("--max-directional-error-rate", type=float, default=0.35)
    parser.add_argument(
        "--movement-policy",
        choices=MOVEMENT_POLICIES,
        default="all",
        help="How to apply candidate p50 movement before holdout auditing.",
    )
    parser.add_argument(
        "--candidate-include-pattern",
        help="Regex over 'component:group' labels allowed into holdout candidates.",
    )
    parser.add_argument(
        "--candidate-exclude-pattern",
        help="Regex over 'component:group' labels removed from holdout candidates.",
    )
    parser.add_argument("--top", type=int, default=20)
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
    result = {"errors": errors, **summarize_holdout(
        rows,
        group_field=args.group_field,
        components=args.component or DEFAULT_COMPONENTS,
        folds=args.folds,
        min_windows=args.min_windows,
        min_sessions=args.min_sessions,
        min_changed=args.min_changed,
        max_hurt_rate=args.max_hurt_rate,
        max_directional_error_rate=args.max_directional_error_rate,
        candidate_prefix=args.candidate_prefix,
        movement_policy=args.movement_policy,
        candidate_include_pattern=args.candidate_include_pattern,
        candidate_exclude_pattern=args.candidate_exclude_pattern,
    )}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_summary(result, top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
