"""Session holdout for v3 settlement count-prior count->cells/value bridge."""

from __future__ import annotations

import argparse
import hashlib
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
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)
from summarize_v3_scp_count_value_bridge import (  # noqa: E402
    _bool,
    _counter_dict,
    _group_value,
    _is_metric_row,
    _numeric_summary,
    _row_bridge_fields,
    _session_id,
)

DEFAULT_GROUP_FIELD = "v3_scp_group"
_MAX_FORMAL_MAE_HURT = 10_000.0
_MAX_P90_COVERAGE_DROP = 0.02
_MAX_OVER_RATE_INCREASE = 0.08
_MAX_CANDIDATE_OVER_RATE = 0.60


def _stable_fold(value: Any, folds: int) -> int:
    if folds <= 1:
        return 0
    digest = hashlib.sha1(str(value or "unknown").encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % int(folds)


def _percentile(values: Iterable[Any], quantile: float) -> float | None:
    seq = sorted(
        value
        for value in (_float_or_none(item) for item in values)
        if value is not None
    )
    if not seq:
        return None
    index = min(
        len(seq) - 1,
        max(0, int(round((len(seq) - 1) * float(quantile)))),
    )
    return seq[index]


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _pairs(
    rows: Iterable[Mapping[str, Any]],
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


def _over_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred > truth else 0.0 for pred, truth in pairs)


def _coverage_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _safe_ratio(num: Any, den: Any) -> float | None:
    numerator = _float_or_none(num)
    denominator = _float_or_none(den)
    if numerator is None or denominator is None or denominator <= 0.0:
        return None
    return numerator / denominator


def _metric_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {**dict(row), **_row_bridge_fields(row)}
        for row in rows
        if _is_metric_row(row)
    )


def _is_bridge_candidate(row: Mapping[str, Any]) -> bool:
    return (
        _bool(row.get("v3_scp_candidate"))
        and (scp_gap := _float_or_none(row.get("scp_p95_minus_target"))) is not None
        and scp_gap > 0.0
        and _float_or_none(row.get("scp_p95")) is not None
    )


def _ratio_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    ratio_source: str,
) -> tuple[Mapping[str, Any], ...]:
    if ratio_source == "bridge":
        return tuple(row for row in rows if _is_bridge_candidate(row))
    return tuple(rows)


def _train_stats(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_train_sessions: int,
    ratio_source: str,
) -> dict[str, Any]:
    ratio_rows = _ratio_rows(rows, ratio_source=ratio_source)
    sessions = {_session_id(row) for row in ratio_rows}
    ready = len(sessions) >= int(min_train_sessions)
    cells_per_item = tuple(
        value
        for value in (
            _safe_ratio(row.get("v3_truth_total_cells"), row.get("v3_truth_item_count"))
            for row in ratio_rows
        )
        if value is not None
    )
    formal_per_item = tuple(
        value
        for value in (
            _safe_ratio(
                row.get("v3_truth_formal_decision_value"),
                row.get("v3_truth_item_count"),
            )
            for row in ratio_rows
        )
        if value is not None
    )
    return {
        "ready": ready and bool(cells_per_item) and bool(formal_per_item),
        "train_rows": len(tuple(rows)),
        "train_ratio_rows": len(ratio_rows),
        "train_sessions": len(sessions),
        "cells_per_item_p50": _percentile(cells_per_item, 0.50),
        "cells_per_item_p90": _percentile(cells_per_item, 0.90),
        "formal_per_item_p50": _percentile(formal_per_item, 0.50),
        "formal_per_item_p90": _percentile(formal_per_item, 0.90),
    }


def _bridge_floor(count: Any, per_item: Any) -> float | None:
    count_value = _float_or_none(count)
    per_item_value = _float_or_none(per_item)
    if count_value is None or per_item_value is None:
        return None
    return count_value * per_item_value


def _extra_bridge_floor(
    baseline: Any,
    *,
    count_gap: Any,
    per_item: Any,
) -> float | None:
    baseline_value = _float_or_none(baseline)
    gap_value = _float_or_none(count_gap)
    per_item_value = _float_or_none(per_item)
    if baseline_value is None:
        return None
    if gap_value is None or per_item_value is None or gap_value <= 0.0:
        return baseline_value
    return baseline_value + gap_value * per_item_value


def _max_or_baseline(baseline: Any, floor: Any) -> float | None:
    baseline_value = _float_or_none(baseline)
    floor_value = _float_or_none(floor)
    if baseline_value is None:
        return floor_value
    if floor_value is None:
        return baseline_value
    return max(baseline_value, floor_value)


def _capped_max_or_baseline(
    baseline: Any,
    floor: Any,
    *,
    lift_cap: float | None,
) -> float | None:
    value = _max_or_baseline(baseline, floor)
    baseline_value = _float_or_none(baseline)
    cap_value = _float_or_none(lift_cap)
    if (
        value is None
        or baseline_value is None
        or cap_value is None
        or cap_value <= 0.0
    ):
        return value
    return min(value, baseline_value + cap_value)


def _eval_row(
    row: Mapping[str, Any],
    *,
    group: str,
    fold: int,
    stats: Mapping[str, Any],
    floor_mode: str,
    formal_lift_cap: float | None,
) -> dict[str, Any]:
    candidate = _is_bridge_candidate(row)
    sample_limited = candidate and not _bool(stats.get("ready"))
    scp_p95 = _float_or_none(row.get("scp_p95"))
    count_gap = _float_or_none(row.get("scp_p95_minus_target"))
    baseline_cells_p50 = _float_or_none(row.get("v3_post_total_cells_p50"))
    baseline_cells_p90 = _float_or_none(row.get("v3_post_total_cells_p90"))
    baseline_formal_p50 = _float_or_none(row.get("v3_post_formal_decision_value_p50"))
    baseline_formal_p90 = _float_or_none(row.get("v3_post_formal_decision_value_p90"))
    if candidate and not sample_limited and floor_mode == "extra":
        cells_p50_floor = _extra_bridge_floor(
            baseline_cells_p50,
            count_gap=count_gap,
            per_item=stats.get("cells_per_item_p50"),
        )
        cells_p90_floor = _extra_bridge_floor(
            baseline_cells_p90,
            count_gap=count_gap,
            per_item=stats.get("cells_per_item_p90"),
        )
        formal_p50_floor = _extra_bridge_floor(
            baseline_formal_p50,
            count_gap=count_gap,
            per_item=stats.get("formal_per_item_p50"),
        )
        formal_p90_floor = _extra_bridge_floor(
            baseline_formal_p90,
            count_gap=count_gap,
            per_item=stats.get("formal_per_item_p90"),
        )
    else:
        cells_p50_floor = (
            _bridge_floor(scp_p95, stats.get("cells_per_item_p50"))
            if candidate and not sample_limited
            else None
        )
        cells_p90_floor = (
            _bridge_floor(scp_p95, stats.get("cells_per_item_p90"))
            if candidate and not sample_limited
            else None
        )
        formal_p50_floor = (
            _bridge_floor(scp_p95, stats.get("formal_per_item_p50"))
            if candidate and not sample_limited
            else None
        )
        formal_p90_floor = (
            _bridge_floor(scp_p95, stats.get("formal_per_item_p90"))
            if candidate and not sample_limited
            else None
        )
    bridge_cells_p50 = _max_or_baseline(baseline_cells_p50, cells_p50_floor)
    bridge_cells_p90 = _max_or_baseline(baseline_cells_p90, cells_p90_floor)
    bridge_formal_p50 = _capped_max_or_baseline(
        baseline_formal_p50,
        formal_p50_floor,
        lift_cap=formal_lift_cap,
    )
    bridge_formal_p90 = _capped_max_or_baseline(
        baseline_formal_p90,
        formal_p90_floor,
        lift_cap=formal_lift_cap,
    )
    applied = (
        candidate
        and not sample_limited
        and (
            (
                bridge_formal_p50 is not None
                and baseline_formal_p50 is not None
                and bridge_formal_p50 > baseline_formal_p50
            )
            or (
                bridge_formal_p90 is not None
                and baseline_formal_p90 is not None
                and bridge_formal_p90 > baseline_formal_p90
            )
        )
    )
    return {
        **dict(row),
        "group": group,
        "fold": fold,
        "candidate_eligible": candidate,
        "candidate_applied": applied,
        "sample_limited": sample_limited,
        "train_sessions": stats.get("train_sessions"),
        "train_ratio_rows": stats.get("train_ratio_rows"),
        "train_cells_per_item_p50": stats.get("cells_per_item_p50"),
        "train_cells_per_item_p90": stats.get("cells_per_item_p90"),
        "train_formal_per_item_p50": stats.get("formal_per_item_p50"),
        "train_formal_per_item_p90": stats.get("formal_per_item_p90"),
        "bridge_cells_p50": bridge_cells_p50,
        "bridge_cells_p90": bridge_cells_p90,
        "bridge_formal_p50": bridge_formal_p50,
        "bridge_formal_p90": bridge_formal_p90,
        "baseline_cells_p50": baseline_cells_p50,
        "baseline_cells_p90": baseline_cells_p90,
        "baseline_formal_p50": baseline_formal_p50,
        "baseline_formal_p90": baseline_formal_p90,
    }


def _eval_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_field: str,
    folds: int,
    min_train_sessions: int,
    ratio_source: str,
    floor_mode: str,
    formal_lift_cap: float | None,
) -> tuple[dict[str, Any], ...]:
    metric_rows = _metric_rows(rows)
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metric_rows:
        by_group[_group_value(row, group_field)].append(row)

    out: list[dict[str, Any]] = []
    fold_count = max(1, int(folds))
    for group, group_rows in by_group.items():
        group_seq = tuple(group_rows)
        for row in group_seq:
            fold = _stable_fold(_session_id(row), fold_count)
            train = tuple(
                item
                for item in group_seq
                if _stable_fold(_session_id(item), fold_count) != fold
            )
            stats = _train_stats(
                train,
                min_train_sessions=min_train_sessions,
                ratio_source=ratio_source,
            )
            out.append(
                _eval_row(
                    row,
                    group=group,
                    fold=fold,
                    stats=stats,
                    floor_mode=floor_mode,
                    formal_lift_cap=formal_lift_cap,
                )
            )
    return tuple(out)


def _metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    seq = tuple(rows)
    baseline_formal = _pairs(seq, "baseline_formal_p50", "v3_truth_formal_decision_value")
    bridge_formal = _pairs(seq, "bridge_formal_p50", "v3_truth_formal_decision_value")
    baseline_formal_p90 = _pairs(seq, "baseline_formal_p90", "v3_truth_formal_decision_value")
    bridge_formal_p90 = _pairs(seq, "bridge_formal_p90", "v3_truth_formal_decision_value")
    baseline_cells_p90 = _pairs(seq, "baseline_cells_p90", "v3_truth_total_cells")
    bridge_cells_p90 = _pairs(seq, "bridge_cells_p90", "v3_truth_total_cells")
    baseline_mae = _mae(baseline_formal)
    bridge_mae = _mae(bridge_formal)
    baseline_p90_coverage = _coverage_rate(baseline_formal_p90)
    bridge_p90_coverage = _coverage_rate(bridge_formal_p90)
    baseline_over = _over_rate(baseline_formal)
    bridge_over = _over_rate(bridge_formal)
    return {
        "rows": len(seq),
        "sessions": len({_session_id(row) for row in seq}),
        "candidate_rows": sum(1 for row in seq if row.get("candidate_eligible")),
        "applied_rows": sum(1 for row in seq if row.get("candidate_applied")),
        "sample_limited_rows": sum(1 for row in seq if row.get("sample_limited")),
        "baseline_formal_p50_mae": _round_metric(baseline_mae, 3),
        "bridge_formal_p50_mae": _round_metric(bridge_mae, 3),
        "delta_formal_p50_mae": _round_metric(_delta(bridge_mae, baseline_mae), 3),
        "baseline_formal_p50_bias": _round_metric(_bias(baseline_formal), 3),
        "bridge_formal_p50_bias": _round_metric(_bias(bridge_formal), 3),
        "baseline_formal_p50_below_rate": _round_metric(_below_rate(baseline_formal), 6),
        "bridge_formal_p50_below_rate": _round_metric(_below_rate(bridge_formal), 6),
        "baseline_formal_p50_over_rate": _round_metric(baseline_over, 6),
        "bridge_formal_p50_over_rate": _round_metric(bridge_over, 6),
        "delta_formal_p50_over_rate": _round_metric(_delta(bridge_over, baseline_over), 6),
        "baseline_formal_p90_coverage": _round_metric(baseline_p90_coverage, 6),
        "bridge_formal_p90_coverage": _round_metric(bridge_p90_coverage, 6),
        "delta_formal_p90_coverage": _round_metric(
            _delta(bridge_p90_coverage, baseline_p90_coverage),
            6,
        ),
        "baseline_cells_p90_coverage": _round_metric(_coverage_rate(baseline_cells_p90), 6),
        "bridge_cells_p90_coverage": _round_metric(_coverage_rate(bridge_cells_p90), 6),
        "train_sessions": _numeric_summary(row.get("train_sessions") for row in seq),
        "train_cells_per_item_p90": _numeric_summary(
            row.get("train_cells_per_item_p90") for row in seq
        ),
        "train_formal_per_item_p90": _numeric_summary(
            row.get("train_formal_per_item_p90") for row in seq
        ),
    }


def _candidate_status(row: Mapping[str, Any]) -> str:
    if int(row.get("applied_rows") or 0) <= 0:
        if int(row.get("sample_limited_rows") or 0) > 0:
            return "blocked_low_sample"
        return "sample_limited"
    delta_mae = _float_or_none(row.get("delta_formal_p50_mae"))
    delta_p90 = _float_or_none(row.get("delta_formal_p90_coverage"))
    delta_over = _float_or_none(row.get("delta_formal_p50_over_rate"))
    bridge_over = _float_or_none(row.get("bridge_formal_p50_over_rate"))
    if delta_mae is not None and delta_mae > _MAX_FORMAL_MAE_HURT:
        return "blocked_holdout_hurt"
    if delta_p90 is not None and delta_p90 < -_MAX_P90_COVERAGE_DROP:
        return "blocked_holdout_hurt"
    if delta_over is not None and delta_over > _MAX_OVER_RATE_INCREASE:
        return "blocked_holdout_hurt"
    if bridge_over is not None and bridge_over >= _MAX_CANDIDATE_OVER_RATE:
        return "blocked_holdout_over_risk"
    if delta_mae is not None and delta_mae <= 0.0:
        return "watch_count_value_bridge_holdout"
    return "blocked_holdout_no_improvement"


def summarize_holdout(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    folds: int = 5,
    min_train_sessions: int = 8,
    ratio_source: str = "all",
    floor_mode: str = "total",
    formal_lift_cap: float | None = None,
    top: int = 12,
) -> dict[str, Any]:
    eval_rows = _eval_rows(
        rows,
        group_field=group_field,
        folds=folds,
        min_train_sessions=min_train_sessions,
        ratio_source=ratio_source,
        floor_mode=floor_mode,
        formal_lift_cap=formal_lift_cap,
    )
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in eval_rows:
        by_group[str(row.get("group") or "unknown")].append(row)
    group_results: list[dict[str, Any]] = []
    for group, group_rows in sorted(by_group.items()):
        metrics = _metrics(group_rows)
        row = {
            "group_field": group_field,
            "group": group,
            **metrics,
            "capacity_cases": _counter_dict(
                (item.get("v3_capacity_cases") for item in group_rows),
                top=top,
            ),
        }
        row["candidate_status"] = _candidate_status(row)
        group_results.append(row)
    group_results = sorted(
        group_results,
        key=lambda row: (
            0 if row["candidate_status"] == "watch_count_value_bridge_holdout" else 1,
            -int(row.get("applied_rows") or 0),
            -int(row.get("candidate_rows") or 0),
            str(row["group"]),
        ),
    )
    overall = _metrics(eval_rows)
    candidate_only = _metrics(row for row in eval_rows if row.get("candidate_applied"))
    applied_hurts = [
        str(row.get("group"))
        for row in group_results
        if row.get("candidate_status") in {
            "blocked_holdout_hurt",
            "blocked_holdout_over_risk",
        }
    ][:8]
    status_counts = dict(
        sorted(Counter(str(row["candidate_status"]) for row in group_results).items())
    )
    result = {
        "group_field": group_field,
        "folds": int(folds),
        "min_train_sessions": int(min_train_sessions),
        "ratio_source": ratio_source,
        "floor_mode": floor_mode,
        "formal_lift_cap": _round_metric(_float_or_none(formal_lift_cap), 3),
        "overall": overall,
        "candidate_only": candidate_only,
        "group_results": group_results,
        "status_counts": status_counts,
        "applied_hurts": applied_hurts,
    }
    result["overall_status"] = _overall_status(candidate_only, applied_hurts)
    return result


def _overall_status(candidate: Mapping[str, Any], applied_hurts: list[str]) -> str:
    if int(candidate.get("applied_rows") or 0) <= 0:
        return "sample_limited"
    if applied_hurts:
        return "blocked_holdout_hurt"
    delta_mae = _float_or_none(candidate.get("delta_formal_p50_mae"))
    delta_p90 = _float_or_none(candidate.get("delta_formal_p90_coverage"))
    bridge_over = _float_or_none(candidate.get("bridge_formal_p50_over_rate"))
    if (
        delta_mae is not None
        and delta_mae <= 0.0
        and (delta_p90 is None or delta_p90 >= -_MAX_P90_COVERAGE_DROP)
        and (bridge_over is None or bridge_over < _MAX_CANDIDATE_OVER_RATE)
    ):
        return "watch"
    return "blocked_holdout_hurt"


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p50={summary['p50']}"
        f"/p90={summary['p90']}"
        f"/p95={summary['p95']}"
        f"/max={summary['max']}"
    )


def _format_counts(counts: Mapping[str, int]) -> str:
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
                f"min_train_sessions={result['min_train_sessions']}",
                f"ratio_source={result['ratio_source']}",
                f"floor_mode={result['floor_mode']}",
                f"formal_lift_cap={result['formal_lift_cap']}",
                f"rows={overall['rows']}",
                f"candidate_rows={overall['candidate_rows']}",
                f"applied_rows={candidate['applied_rows']}",
                f"sample_limited_rows={overall['sample_limited_rows']}",
                f"candidate_delta_mae={candidate['delta_formal_p50_mae']}",
                f"candidate_delta_p90={candidate['delta_formal_p90_coverage']}",
                f"candidate_over={candidate['bridge_formal_p50_over_rate']}",
                f"overall_delta_mae={overall['delta_formal_p50_mae']}",
                f"overall_delta_p90={overall['delta_formal_p90_coverage']}",
                f"status_counts={_format_counts(result['status_counts'])}",
                "applied_hurts=" + ",".join(result["applied_hurts"]),
            )
        )
    )
    for row in result["group_results"][:top]:
        print(
            " ".join(
                (
                    f"group={row['group']}",
                    f"status={row['candidate_status']}",
                    f"rows={row['rows']}",
                    f"candidate_rows={row['candidate_rows']}",
                    f"applied_rows={row['applied_rows']}",
                    f"sample_limited={row['sample_limited_rows']}",
                    f"delta_mae={row['delta_formal_p50_mae']}",
                    f"delta_p90={row['delta_formal_p90_coverage']}",
                    f"bridge_over={row['bridge_formal_p50_over_rate']}",
                    f"cells_p90_cover={row['baseline_cells_p90_coverage']}->{row['bridge_cells_p90_coverage']}",
                    f"train_sessions={_format_summary(row['train_sessions'])}",
                    f"train_value_p90={_format_summary(row['train_formal_per_item_p90'])}",
                    f"capacity_cases={_format_counts(row['capacity_cases'])}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Session holdout for v3 settlement count-prior count->cells/value bridge.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--by", default=DEFAULT_GROUP_FIELD)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-train-sessions", type=int, default=8)
    parser.add_argument("--ratio-source", choices=("all", "bridge"), default="all")
    parser.add_argument("--floor-mode", choices=("total", "extra"), default="total")
    parser.add_argument("--formal-lift-cap", type=float, default=None)
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=load_monitor_tables(),
        calibration_entries=load_prior_calibration_entries(_default_calibration_path()),
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
        **summarize_holdout(
            rows,
            group_field=args.by,
            folds=args.folds,
            min_train_sessions=args.min_train_sessions,
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
