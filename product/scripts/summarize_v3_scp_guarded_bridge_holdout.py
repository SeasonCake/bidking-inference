"""Nested holdout for a train-selected v3 settlement count/value bridge."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

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
    _default_settlement_count_prior_path,
    _default_tail_value_review_path,
    _default_underestimate_repair_path,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)
from summarize_v3_scp_count_value_bridge import (  # noqa: E402
    _counter_dict,
    _group_value,
    _session_id,
)
from summarize_v3_scp_count_value_bridge_holdout import (  # noqa: E402
    DEFAULT_GROUP_FIELD,
    _candidate_status,
    _eval_row,
    _metric_rows,
    _metrics,
    _overall_status,
    _stable_fold,
    _train_stats,
)


def _inner_fold(row: Mapping[str, Any], folds: int) -> int:
    return _stable_fold(f"inner:{_session_id(row)}", folds)


def _applied_sessions(rows: Iterable[Mapping[str, Any]]) -> int:
    return len(
        {
            _session_id(row)
            for row in rows
            if row.get("candidate_applied")
        }
    )


def _guard_status(
    metrics: Mapping[str, Any],
    *,
    applied_sessions: int,
    min_guard_sessions: int,
) -> str:
    if applied_sessions < int(min_guard_sessions):
        return "blocked_guard_low_sample"
    status = _candidate_status(metrics)
    if status == "watch_count_value_bridge_holdout":
        return "watch_train_guard"
    return status


def _crossfit_guard(
    rows: Iterable[Mapping[str, Any]],
    *,
    group: str,
    inner_folds: int,
    min_train_sessions: int,
    min_guard_sessions: int,
    min_guard_fold_sessions: int,
    guard_stability: str,
    max_guard_over_increase: float,
    ratio_source: str,
    floor_mode: str,
    formal_lift_cap: float | None,
) -> dict[str, Any]:
    seq = tuple(rows)
    fold_count = max(2, int(inner_folds))
    evals: list[dict[str, Any]] = []
    evals_by_fold: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in seq:
        fold = _inner_fold(row, fold_count)
        train = tuple(item for item in seq if _inner_fold(item, fold_count) != fold)
        stats = _train_stats(
            train,
            min_train_sessions=min_train_sessions,
            ratio_source=ratio_source,
        )
        evaluated = _eval_row(
            row,
            group=group,
            fold=fold,
            stats=stats,
            floor_mode=floor_mode,
            formal_lift_cap=formal_lift_cap,
        )
        evals.append(evaluated)
        evals_by_fold[fold].append(evaluated)
    metrics = _metrics(evals)
    applied_sessions = _applied_sessions(evals)
    inner_fold_results = []
    for fold in range(fold_count):
        fold_evals = evals_by_fold.get(fold, [])
        fold_metrics = _metrics(fold_evals)
        fold_applied_sessions = _applied_sessions(fold_evals)
        inner_fold_results.append(
            {
                "fold": fold,
                "applied_sessions": fold_applied_sessions,
                "status": _candidate_status(fold_metrics),
                **fold_metrics,
            }
        )
    guard_status = _guard_status(
        metrics,
        applied_sessions=applied_sessions,
        min_guard_sessions=min_guard_sessions,
    )
    if (
        guard_status == "watch_train_guard"
        and metrics.get("delta_formal_p50_over_rate") is not None
        and float(metrics["delta_formal_p50_over_rate"])
        > float(max_guard_over_increase)
    ):
        guard_status = "blocked_guard_over_increase"
    if guard_status == "watch_train_guard" and guard_stability == "all_folds":
        if any(
            int(row["applied_sessions"]) < int(min_guard_fold_sessions)
            for row in inner_fold_results
        ):
            guard_status = "blocked_guard_fold_low_sample"
        elif any(
            row["status"] != "watch_count_value_bridge_holdout"
            for row in inner_fold_results
        ):
            guard_status = "blocked_guard_fold_unstable"
        elif any(
            row.get("delta_formal_p50_over_rate") is not None
            and float(row["delta_formal_p50_over_rate"])
            > float(max_guard_over_increase)
            for row in inner_fold_results
        ):
            guard_status = "blocked_guard_fold_over_increase"
    return {
        "group": group,
        "guard_status": guard_status,
        "applied_sessions": applied_sessions,
        "inner_fold_results": inner_fold_results,
        **metrics,
    }


def _disable_candidate(
    row: Mapping[str, Any],
    *,
    guard_status: str,
) -> dict[str, Any]:
    return {
        **dict(row),
        "candidate_applied": False,
        "guard_selected": False,
        "guard_status": guard_status,
        "bridge_cells_p50": row.get("baseline_cells_p50"),
        "bridge_cells_p90": row.get("baseline_cells_p90"),
        "bridge_formal_p50": row.get("baseline_formal_p50"),
        "bridge_formal_p90": row.get("baseline_formal_p90"),
    }


def _support_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    seq = tuple(rows)
    return {
        "metric_rows": len(seq),
        "sessions": len({_session_id(row) for row in seq}),
        "candidate_rows": sum(1 for row in seq if row.get("candidate_eligible")),
        "applied_rows": sum(1 for row in seq if row.get("candidate_applied")),
        "sample_limited_rows": sum(1 for row in seq if row.get("sample_limited")),
    }


def summarize_guarded_holdout(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    folds: int = 5,
    inner_folds: int = 4,
    min_train_sessions: int = 8,
    min_guard_sessions: int = 8,
    min_guard_fold_sessions: int = 2,
    guard_stability: str = "all_folds",
    max_guard_over_increase: float = 0.0,
    ratio_source: str = "all",
    floor_mode: str = "total",
    formal_lift_cap: float | None = 5_000.0,
    top: int = 12,
) -> dict[str, Any]:
    metric_rows = _metric_rows(rows)
    fold_count = max(2, int(folds))
    all_evals: list[dict[str, Any]] = []
    fold_results: list[dict[str, Any]] = []
    selected_group_guard_summary: list[dict[str, Any]] = []
    selected_group_fold_support: list[dict[str, Any]] = []
    guard_status_counts: Counter[str] = Counter()
    selected_group_fold_counts: Counter[str] = Counter()

    for fold in range(fold_count):
        train_rows = tuple(
            row
            for row in metric_rows
            if _stable_fold(_session_id(row), fold_count) != fold
        )
        holdout_rows = tuple(
            row
            for row in metric_rows
            if _stable_fold(_session_id(row), fold_count) == fold
        )
        train_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in train_rows:
            train_groups[_group_value(row, group_field)].append(row)

        guards = {
            group: _crossfit_guard(
                group_rows,
                group=group,
                inner_folds=inner_folds,
                min_train_sessions=min_train_sessions,
                min_guard_sessions=min_guard_sessions,
                min_guard_fold_sessions=min_guard_fold_sessions,
                guard_stability=guard_stability,
                max_guard_over_increase=max_guard_over_increase,
                ratio_source=ratio_source,
                floor_mode=floor_mode,
                formal_lift_cap=formal_lift_cap,
            )
            for group, group_rows in train_groups.items()
        }
        guard_status_counts.update(
            str(guard["guard_status"]) for guard in guards.values()
        )
        selected_groups = {
            group
            for group, guard in guards.items()
            if guard.get("guard_status") == "watch_train_guard"
        }
        selected_group_fold_counts.update(selected_groups)

        outer_stats = {
            group: _train_stats(
                group_rows,
                min_train_sessions=min_train_sessions,
                ratio_source=ratio_source,
            )
            for group, group_rows in train_groups.items()
        }
        fold_evals: list[dict[str, Any]] = []
        for row in holdout_rows:
            group = _group_value(row, group_field)
            guard = guards.get(group, {})
            guard_status = str(guard.get("guard_status") or "blocked_guard_missing_group")
            evaluated = _eval_row(
                row,
                group=group,
                fold=fold,
                stats=outer_stats.get(group, {}),
                floor_mode=floor_mode,
                formal_lift_cap=formal_lift_cap,
            )
            if group not in selected_groups:
                evaluated = _disable_candidate(
                    evaluated,
                    guard_status=guard_status,
                )
            else:
                evaluated["guard_selected"] = True
                evaluated["guard_status"] = guard_status
            fold_evals.append(evaluated)

        all_evals.extend(fold_evals)
        for group in sorted(selected_groups):
            guard = guards.get(group, {})
            group_fold_evals = [
                row for row in fold_evals if str(row.get("group")) == group
            ]
            inner_status_counts = Counter(
                str(row.get("status") or "unknown")
                for row in guard.get("inner_fold_results", ())
            )
            selected_group_guard_summary.append(
                {
                    "fold": fold,
                    "group": group,
                    "guard_status": guard.get("guard_status"),
                    "guard_applied_sessions": guard.get("applied_sessions"),
                    "guard_delta_formal_p50_mae": guard.get(
                        "delta_formal_p50_mae"
                    ),
                    "guard_delta_formal_p90_coverage": guard.get(
                        "delta_formal_p90_coverage"
                    ),
                    "guard_bridge_formal_p50_over_rate": guard.get(
                        "bridge_formal_p50_over_rate"
                    ),
                    "guard_inner_status_counts": dict(
                        sorted(inner_status_counts.items())
                    ),
                    **_support_counts(group_fold_evals),
                }
            )
            selected_group_fold_support.append(
                {
                    "fold": fold,
                    "group": group,
                    **_support_counts(group_fold_evals),
                }
            )
        fold_candidate = _metrics(
            row for row in fold_evals if row.get("candidate_applied")
        )
        fold_results.append(
            {
                "fold": fold,
                "train_rows": len(train_rows),
                "holdout_rows": len(holdout_rows),
                "selected_groups": sorted(selected_groups),
                "selected_group_count": len(selected_groups),
                "candidate_only": fold_candidate,
            }
        )

    group_evals: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in all_evals:
        if row.get("candidate_applied"):
            group_evals[str(row.get("group") or "unknown")].append(row)
    selected_support_evals: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in all_evals:
        group = str(row.get("group") or "unknown")
        if group in selected_group_fold_counts:
            selected_support_evals[group].append(row)
    selected_group_support = [
        {
            "group": group,
            "selected_folds": selected_group_fold_counts[group],
            **_support_counts(group_rows),
        }
        for group, group_rows in sorted(selected_support_evals.items())
    ]
    group_results: list[dict[str, Any]] = []
    for group, group_rows in sorted(group_evals.items()):
        metrics = _metrics(group_rows)
        group_results.append(
            {
                "group": group,
                "selected_folds": selected_group_fold_counts[group],
                "holdout_status": _candidate_status(metrics),
                **metrics,
                "capacity_cases": _counter_dict(
                    (row.get("v3_capacity_cases") for row in group_rows),
                    top=top,
                ),
            }
        )
    group_results.sort(
        key=lambda row: (
            0 if row["holdout_status"] == "watch_count_value_bridge_holdout" else 1,
            -int(row.get("applied_rows") or 0),
            str(row["group"]),
        )
    )

    candidate_only = _metrics(
        row for row in all_evals if row.get("candidate_applied")
    )
    overall = _metrics(all_evals)
    applied_hurts = [
        str(row["group"])
        for row in group_results
        if row["holdout_status"] in {
            "blocked_holdout_hurt",
            "blocked_holdout_over_risk",
        }
    ]
    result = {
        "group_field": group_field,
        "folds": fold_count,
        "inner_folds": max(2, int(inner_folds)),
        "min_train_sessions": int(min_train_sessions),
        "min_guard_sessions": int(min_guard_sessions),
        "min_guard_fold_sessions": int(min_guard_fold_sessions),
        "guard_stability": guard_stability,
        "max_guard_over_increase": _round_metric(
            max_guard_over_increase,
            6,
        ),
        "ratio_source": ratio_source,
        "floor_mode": floor_mode,
        "formal_lift_cap": _round_metric(formal_lift_cap, 3),
        "overall": overall,
        "candidate_only": candidate_only,
        "guard_status_counts": dict(sorted(guard_status_counts.items())),
        "selected_group_fold_counts": dict(
            sorted(selected_group_fold_counts.items())
        ),
        "selected_group_guard_summary": selected_group_guard_summary,
        "selected_group_fold_support": selected_group_fold_support,
        "selected_group_support": selected_group_support,
        "fold_results": fold_results,
        "group_results": group_results,
        "applied_hurts": applied_hurts,
    }
    result["overall_status"] = _overall_status(candidate_only, applied_hurts)
    return result


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p50={summary['p50']}"
        f"/p90={summary['p90']}"
        f"/p95={summary['p95']}"
        f"/max={summary['max']}"
    )


def _format_counts(counts: Mapping[str, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    candidate = result["candidate_only"]
    overall = result["overall"]
    print(
        " ".join(
            (
                f"overall_status={result['overall_status']}",
                f"group_field={result['group_field']}",
                f"folds={result['folds']}",
                f"inner_folds={result['inner_folds']}",
                f"min_train_sessions={result['min_train_sessions']}",
                f"min_guard_sessions={result['min_guard_sessions']}",
                f"min_guard_fold_sessions={result['min_guard_fold_sessions']}",
                f"guard_stability={result['guard_stability']}",
                f"max_guard_over_increase={result['max_guard_over_increase']}",
                f"ratio_source={result['ratio_source']}",
                f"floor_mode={result['floor_mode']}",
                f"formal_lift_cap={result['formal_lift_cap']}",
                f"rows={overall['rows']}",
                f"candidate_rows={overall['candidate_rows']}",
                f"applied_rows={candidate['applied_rows']}",
                f"candidate_delta_mae={candidate['delta_formal_p50_mae']}",
                f"candidate_delta_p90={candidate['delta_formal_p90_coverage']}",
                f"candidate_over={candidate['bridge_formal_p50_over_rate']}",
                f"guard_statuses={_format_counts(result['guard_status_counts'])}",
                f"selected_groups={_format_counts(result['selected_group_fold_counts'])}",
                "applied_hurts=" + ",".join(result["applied_hurts"]),
            )
        )
    )
    for row in result["group_results"][:top]:
        print(
            " ".join(
                (
                    f"group={row['group']}",
                    f"selected_folds={row['selected_folds']}",
                    f"status={row['holdout_status']}",
                    f"applied_rows={row['applied_rows']}",
                    f"delta_mae={row['delta_formal_p50_mae']}",
                    f"delta_p90={row['delta_formal_p90_coverage']}",
                    f"bridge_over={row['bridge_formal_p50_over_rate']}",
                    f"cells_p90_cover={row['baseline_cells_p90_coverage']}->{row['bridge_cells_p90_coverage']}",
                    f"train_sessions={_format_summary(row['train_sessions'])}",
                    f"capacity_cases={_format_counts(row['capacity_cases'])}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nested train-only guard holdout for v3 settlement count/value bridge.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--by", default=DEFAULT_GROUP_FIELD)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--inner-folds", type=int, default=4)
    parser.add_argument("--min-train-sessions", type=int, default=8)
    parser.add_argument("--min-guard-sessions", type=int, default=8)
    parser.add_argument("--min-guard-fold-sessions", type=int, default=2)
    parser.add_argument(
        "--guard-stability",
        choices=("aggregate", "all_folds"),
        default="all_folds",
    )
    parser.add_argument("--max-guard-over-increase", type=float, default=0.0)
    parser.add_argument("--ratio-source", choices=("all", "bridge"), default="all")
    parser.add_argument("--floor-mode", choices=("total", "extra"), default="total")
    parser.add_argument("--formal-lift-cap", type=float, default=5_000.0)
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
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
        settlement_count_prior_entries=load_settlement_count_prior_entries(
            _default_settlement_count_prior_path()
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    result = {
        "errors": errors,
        **summarize_guarded_holdout(
            rows,
            group_field=args.by,
            folds=args.folds,
            inner_folds=args.inner_folds,
            min_train_sessions=args.min_train_sessions,
            min_guard_sessions=args.min_guard_sessions,
            min_guard_fold_sessions=args.min_guard_fold_sessions,
            guard_stability=args.guard_stability,
            max_guard_over_increase=args.max_guard_over_increase,
            ratio_source=args.ratio_source,
            floor_mode=args.floor_mode,
            formal_lift_cap=args.formal_lift_cap,
            top=args.top,
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
