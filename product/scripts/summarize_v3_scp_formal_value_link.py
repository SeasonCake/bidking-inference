"""Audit the link between v3 settlement count-prior evidence and formal value stress."""

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
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)

DEFAULT_GROUP_FIELD = "v3_scp_status"


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _session_id(row: Mapping[str, Any]) -> str:
    value = row.get("session_id")
    if value not in (None, ""):
        return str(value)
    return str(row.get("file") or "unknown")


def _group_value(row: Mapping[str, Any], field: str) -> str:
    parts = tuple(part.strip() for part in str(field).split(",") if part.strip())
    if len(parts) > 1:
        return "|".join(f"{part}={_group_value(row, part)}" for part in parts)
    value = row.get(field)
    return str(value) if value not in (None, "") else "unknown"


def _is_scp_row(row: Mapping[str, Any]) -> bool:
    return (
        row.get("status") == "ready"
        and _bool(row.get("v3_scp_ready"))
        and not _bool(row.get("v3_scp_active"))
        and not _bool(row.get("v3_scp_affects_bid"))
    )


def _is_formal_metric_row(row: Mapping[str, Any]) -> bool:
    return (
        _is_scp_row(row)
        and _bool(row.get("v3_truth_decision_available"))
        and _bool(row.get("v3_post_ready"))
        and _bool(row.get("v3_fv_ready"))
        and not _bool(row.get("v3_fv_active"))
        and not _bool(row.get("v3_fv_affects_bid"))
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_post_formal_decision_value_p90")) is not None
        and _float_or_none(row.get("v3_fv_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_fv_formal_decision_value_p90")) is not None
    )


def _has_stress(row: Mapping[str, Any], stress: str) -> bool:
    return stress in str(row.get("v3_fv_stress_class") or "")


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


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


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


def _counter_dict(values: Iterable[Any], *, top: int) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _positive_numeric_rows(rows: Iterable[Mapping[str, Any]], field: str) -> int:
    out = 0
    for row in rows:
        value = _float_or_none(row.get(field))
        if value is not None and value > 0.0:
            out += 1
    return out


def _formal_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    seq = tuple(row for row in rows if _is_formal_metric_row(row))
    baseline_p50 = _pairs(
        seq,
        "v3_post_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
    )
    baseline_p90 = _pairs(
        seq,
        "v3_post_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
    )
    fv_p50 = _pairs(
        seq,
        "v3_fv_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
    )
    fv_p90 = _pairs(
        seq,
        "v3_fv_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
    )
    baseline_mae = _mae(baseline_p50)
    fv_mae = _mae(fv_p50)
    baseline_p90_coverage = _coverage_rate(baseline_p90)
    fv_p90_coverage = _coverage_rate(fv_p90)
    return {
        "formal_rows": len(seq),
        "formal_sessions": len({_session_id(row) for row in seq}),
        "formal_baseline_p50_mae": _round_metric(baseline_mae, 3),
        "formal_fv_p50_mae": _round_metric(fv_mae, 3),
        "formal_fv_delta_p50_mae": _round_metric(_delta(fv_mae, baseline_mae), 3),
        "formal_baseline_p50_bias": _round_metric(_bias(baseline_p50), 3),
        "formal_fv_p50_bias": _round_metric(_bias(fv_p50), 3),
        "formal_baseline_p50_below_rate": _round_metric(
            _below_rate(baseline_p50),
            6,
        ),
        "formal_fv_p50_below_rate": _round_metric(_below_rate(fv_p50), 6),
        "formal_baseline_p50_over_rate": _round_metric(_over_rate(baseline_p50), 6),
        "formal_fv_p50_over_rate": _round_metric(_over_rate(fv_p50), 6),
        "formal_baseline_p90_coverage": _round_metric(baseline_p90_coverage, 6),
        "formal_fv_p90_coverage": _round_metric(fv_p90_coverage, 6),
        "formal_fv_delta_p90_coverage": _round_metric(
            _delta(fv_p90_coverage, baseline_p90_coverage),
            6,
        ),
    }


def _summarize_bucket(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_field: str,
    group: str,
    top: int,
) -> dict[str, Any]:
    seq = tuple(row for row in rows if _is_scp_row(row))
    formal_seq = tuple(row for row in seq if _is_formal_metric_row(row))
    scp_candidate = tuple(row for row in seq if _bool(row.get("v3_scp_candidate")))
    scp_missing = tuple(row for row in seq if _bool(row.get("v3_scp_missing_table")))
    value_floor = tuple(
        row for row in formal_seq if _has_stress(row, "value_floor_stress")
    )
    capacity_watch = tuple(
        row for row in formal_seq if _has_stress(row, "capacity_cells_drift")
    )
    q6_cells_watch = tuple(
        row for row in formal_seq if _has_stress(row, "q6_cells_floor_stress")
    )
    scp_candidate_formal = tuple(
        row for row in formal_seq if _bool(row.get("v3_scp_candidate"))
    )
    value_and_scp = tuple(
        row
        for row in formal_seq
        if _bool(row.get("v3_scp_candidate"))
        and _has_stress(row, "value_floor_stress")
    )
    capacity_and_scp = tuple(
        row
        for row in formal_seq
        if _bool(row.get("v3_scp_candidate"))
        and _has_stress(row, "capacity_cells_drift")
    )
    row = {
        "group_field": group_field,
        "group": group,
        "scp_rows": len(seq),
        "scp_sessions": len({_session_id(item) for item in seq}),
        "scp_candidate_rows": len(scp_candidate),
        "scp_missing_table_rows": len(scp_missing),
        "scp_active_rows": sum(1 for item in seq if _bool(item.get("v3_scp_active"))),
        "fv_active_rows": sum(1 for item in formal_seq if _bool(item.get("v3_fv_active"))),
        "fv_value_floor_rows": len(value_floor),
        "fv_capacity_watch_rows": len(capacity_watch),
        "fv_q6_cells_watch_rows": len(q6_cells_watch),
        "scp_candidate_formal_rows": len(scp_candidate_formal),
        "scp_candidate_value_floor_rows": len(value_and_scp),
        "scp_candidate_capacity_watch_rows": len(capacity_and_scp),
        "capacity_prior_max_conflict_rows": sum(
            1
            for item in formal_seq
            if "direct_prior_max_conflict"
            in str(item.get("v3_capacity_cases") or "")
        ),
        "truth_above_prior_max_rows": _positive_numeric_rows(
            formal_seq,
            "v3_capacity_truth_prior_max_delta",
        ),
        "target_above_prior_max_rows": _positive_numeric_rows(
            formal_seq,
            "v3_capacity_target_prior_max_delta",
        ),
        "scp_statuses": _counter_dict(
            (item.get("v3_scp_status") for item in seq),
            top=top,
        ),
        "fv_stress_classes": _counter_dict(
            (item.get("v3_fv_stress_class") for item in formal_seq),
            top=top,
        ),
        "capacity_cases": _counter_dict(
            (item.get("v3_capacity_cases") for item in formal_seq),
            top=top,
        ),
        **_formal_metrics(seq),
    }
    row["link_status"] = _link_status(row)
    return row


def _link_status(row: Mapping[str, Any]) -> str:
    if int(row.get("scp_active_rows") or 0) > 0 or int(row.get("fv_active_rows") or 0) > 0:
        return "regression_active_shadow"
    if int(row.get("scp_missing_table_rows") or 0) > 0:
        return "missing_table_shadow_only"
    if int(row.get("formal_rows") or 0) <= 0:
        return "blocked_no_formal_metric_rows"
    if int(row.get("scp_candidate_formal_rows") or 0) <= 0:
        return "no_scp_candidate_formal_rows"
    if int(row.get("scp_candidate_value_floor_rows") or 0) > 0:
        return "watch_scp_value_floor_overlap"
    if int(row.get("scp_candidate_capacity_watch_rows") or 0) > 0:
        return "watch_scp_capacity_only_overlap"
    return "watch_scp_formal_metric_overlap"


def summarize_link(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    top: int = 12,
) -> dict[str, Any]:
    seq = tuple(row for row in rows if _is_scp_row(row))
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in seq:
        groups[_group_value(row, group_field)].append(row)
    group_rows = [
        _summarize_bucket(
            group_rows,
            group_field=group_field,
            group=group,
            top=top,
        )
        for group, group_rows in sorted(groups.items())
    ]
    group_rows = sorted(
        group_rows,
        key=lambda row: (
            0 if str(row["link_status"]).startswith("watch") else 1,
            -int(row.get("scp_candidate_value_floor_rows") or 0),
            -int(row.get("scp_candidate_capacity_watch_rows") or 0),
            -int(row.get("scp_candidate_formal_rows") or 0),
            str(row["group"]),
        ),
    )
    overall = _summarize_bucket(
        seq,
        group_field="overall",
        group="all",
        top=top,
    )
    status_counts = Counter(str(row["link_status"]) for row in group_rows)
    return {
        "group_field": group_field,
        "groups": len(group_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "overall": overall,
        "rows": group_rows,
    }


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    overall = result["overall"]
    print(
        " ".join(
            (
                f"group_field={result['group_field']}",
                f"groups={result['groups']}",
                f"scp_rows={overall['scp_rows']}",
                f"formal_rows={overall['formal_rows']}",
                f"scp_candidate_rows={overall['scp_candidate_rows']}",
                f"scp_missing_table_rows={overall['scp_missing_table_rows']}",
                f"scp_candidate_formal_rows={overall['scp_candidate_formal_rows']}",
                f"scp_candidate_value_floor_rows={overall['scp_candidate_value_floor_rows']}",
                f"scp_candidate_capacity_watch_rows={overall['scp_candidate_capacity_watch_rows']}",
                f"fv_value_floor_rows={overall['fv_value_floor_rows']}",
                f"fv_capacity_watch_rows={overall['fv_capacity_watch_rows']}",
                f"formal_mae={overall['formal_baseline_p50_mae']}",
                f"fv_delta_mae={overall['formal_fv_delta_p50_mae']}",
                f"formal_below={overall['formal_baseline_p50_below_rate']}",
                f"formal_p90_cover={overall['formal_baseline_p90_coverage']}",
                f"status_counts={_format_counts(result['status_counts'])}",
            )
        )
    )
    for row in result["rows"][:top]:
        print(
            " ".join(
                (
                    f"group={row['group']}",
                    f"status={row['link_status']}",
                    f"scp_rows={row['scp_rows']}",
                    f"formal_rows={row['formal_rows']}",
                    f"scp_candidate={row['scp_candidate_rows']}",
                    f"scp_value_floor={row['scp_candidate_value_floor_rows']}",
                    f"scp_capacity={row['scp_candidate_capacity_watch_rows']}",
                    f"missing_table={row['scp_missing_table_rows']}",
                    f"formal_mae={row['formal_baseline_p50_mae']}",
                    f"fv_delta_mae={row['formal_fv_delta_p50_mae']}",
                    f"below={row['formal_baseline_p50_below_rate']}",
                    f"p90_cover={row['formal_baseline_p90_coverage']}",
                    f"fv_stress={_format_counts(row['fv_stress_classes'])}",
                    f"capacity_cases={_format_counts(row['capacity_cases'])}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit v3 settlement count-prior evidence against formal/value stress.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--by", default=DEFAULT_GROUP_FIELD)
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
    result = {"errors": errors, **summarize_link(rows, group_field=args.by, top=args.top)}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_summary(result, top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
