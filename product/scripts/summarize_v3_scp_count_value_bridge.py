"""Audit count->cells/value bridge candidates for v3 settlement count-prior evidence."""

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

DEFAULT_GROUP_FIELD = "v3_scp_group"


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


def _safe_ratio(num: Any, den: Any) -> float | None:
    numerator = _float_or_none(num)
    denominator = _float_or_none(den)
    if numerator is None or denominator is None or denominator <= 0.0:
        return None
    return numerator / denominator


def _delta(left: Any, right: Any) -> float | None:
    left_value = _float_or_none(left)
    right_value = _float_or_none(right)
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _positive(value: Any) -> bool:
    parsed = _float_or_none(value)
    return parsed is not None and parsed > 0.0


def _negative(value: Any) -> bool:
    parsed = _float_or_none(value)
    return parsed is not None and parsed < 0.0


def _is_scp_row(row: Mapping[str, Any]) -> bool:
    return (
        row.get("status") == "ready"
        and _bool(row.get("v3_scp_ready"))
        and not _bool(row.get("v3_scp_active"))
        and not _bool(row.get("v3_scp_affects_bid"))
    )


def _is_metric_row(row: Mapping[str, Any]) -> bool:
    return (
        _is_scp_row(row)
        and _bool(row.get("v3_truth_decision_available"))
        and _bool(row.get("v3_post_ready"))
        and _float_or_none(row.get("v3_truth_item_count")) is not None
        and _float_or_none(row.get("v3_truth_total_cells")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
        and _float_or_none(row.get("v3_post_total_cells_p50")) is not None
        and _float_or_none(row.get("v3_post_total_cells_p90")) is not None
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_post_formal_decision_value_p90")) is not None
    )


def _numeric_values(values: Iterable[Any]) -> tuple[float, ...]:
    out: list[float] = []
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            out.append(parsed)
    return tuple(out)


def _percentile(sorted_values: tuple[float, ...], pct: float) -> float:
    if not sorted_values:
        raise ValueError("sorted_values must not be empty")
    index = min(
        len(sorted_values) - 1,
        max(0, int(round((len(sorted_values) - 1) * pct))),
    )
    return sorted_values[index]


def _numeric_summary(values: Iterable[Any], *, digits: int = 3) -> dict[str, Any]:
    seq = _numeric_values(values)
    if not seq:
        return {"n": 0, "avg": None, "p50": None, "p90": None, "p95": None, "max": None}
    ordered = tuple(sorted(seq))
    return {
        "n": len(seq),
        "avg": round(sum(seq) / len(seq), digits),
        "p50": round(_percentile(ordered, 0.5), digits),
        "p90": round(_percentile(ordered, 0.9), digits),
        "p95": round(_percentile(ordered, 0.95), digits),
        "max": round(max(seq), digits),
    }


def _counter_dict(values: Iterable[Any], *, top: int) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _row_bridge_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    truth_count = _float_or_none(row.get("v3_truth_item_count"))
    target_count = _float_or_none(row.get("v3_scp_target_count"))
    prior_max = _float_or_none(row.get("v3_scp_prior_items_per_session_max"))
    scp_p95 = _float_or_none(row.get("v3_scp_non_temp_inventory_count_p95"))
    truth_cells = _float_or_none(row.get("v3_truth_total_cells"))
    truth_formal = _float_or_none(row.get("v3_truth_formal_decision_value"))
    post_cells_p50 = _float_or_none(row.get("v3_post_total_cells_p50"))
    post_cells_p90 = _float_or_none(row.get("v3_post_total_cells_p90"))
    post_formal_p50 = _float_or_none(row.get("v3_post_formal_decision_value_p50"))
    post_formal_p90 = _float_or_none(row.get("v3_post_formal_decision_value_p90"))
    cells_p50_under = (
        max(0.0, truth_cells - post_cells_p50)
        if truth_cells is not None and post_cells_p50 is not None
        else None
    )
    cells_p90_under = (
        max(0.0, truth_cells - post_cells_p90)
        if truth_cells is not None and post_cells_p90 is not None
        else None
    )
    formal_p50_under = (
        max(0.0, truth_formal - post_formal_p50)
        if truth_formal is not None and post_formal_p50 is not None
        else None
    )
    formal_p90_under = (
        max(0.0, truth_formal - post_formal_p90)
        if truth_formal is not None and post_formal_p90 is not None
        else None
    )
    return {
        "truth_count": truth_count,
        "target_count": target_count,
        "prior_max": prior_max,
        "scp_p95": scp_p95,
        "scp_p95_minus_prior_max": _delta(scp_p95, prior_max),
        "scp_p95_minus_target": _delta(scp_p95, target_count),
        "truth_minus_prior_max": _delta(truth_count, prior_max),
        "truth_minus_target": _delta(truth_count, target_count),
        "target_minus_truth": _delta(target_count, truth_count),
        "truth_cells": truth_cells,
        "truth_cells_per_item": _safe_ratio(truth_cells, truth_count),
        "cells_p50_under_by": cells_p50_under,
        "cells_p90_under_by": cells_p90_under,
        "truth_formal": truth_formal,
        "truth_formal_per_item": _safe_ratio(truth_formal, truth_count),
        "formal_p50_under_by": formal_p50_under,
        "formal_p90_under_by": formal_p90_under,
    }


def _bridge_status(row: Mapping[str, Any]) -> str:
    if int(row.get("scp_active_rows") or 0) > 0:
        return "regression_active_shadow"
    if int(row.get("scp_missing_table_rows") or 0) > 0:
        return "missing_table_shadow_only"
    if int(row.get("metric_rows") or 0) <= 0:
        return "blocked_no_metric_rows"
    if int(row.get("scp_candidate_metric_rows") or 0) <= 0:
        return "no_scp_candidate_metric_rows"
    if int(row.get("count_cells_value_bridge_rows") or 0) > 0:
        return "watch_count_cells_value_bridge"
    if int(row.get("count_cells_bridge_rows") or 0) > 0:
        return "watch_count_cells_only_bridge"
    if int(row.get("count_value_bridge_rows") or 0) > 0:
        return "watch_count_value_only_bridge"
    return "blocked_no_count_cells_value_bridge"


def _summarize_bucket(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_field: str,
    group: str,
    top: int,
) -> dict[str, Any]:
    scp_rows = tuple(row for row in rows if _is_scp_row(row))
    metric_rows = tuple(row for row in scp_rows if _is_metric_row(row))
    enriched = tuple(
        {**dict(row), **_row_bridge_fields(row)}
        for row in metric_rows
    )
    scp_candidate_metric = tuple(
        row for row in enriched if _bool(row.get("v3_scp_candidate"))
    )
    count_bridge = tuple(
        row for row in scp_candidate_metric if _positive(row.get("scp_p95_minus_target"))
    )
    count_cells_bridge = tuple(
        row for row in count_bridge if _positive(row.get("cells_p90_under_by"))
    )
    count_value_bridge = tuple(
        row for row in count_bridge if _positive(row.get("formal_p90_under_by"))
    )
    count_cells_value_bridge = tuple(
        row
        for row in count_bridge
        if _positive(row.get("cells_p90_under_by"))
        and _positive(row.get("formal_p90_under_by"))
    )
    row = {
        "group_field": group_field,
        "group": group,
        "scp_rows": len(scp_rows),
        "scp_sessions": len({_session_id(item) for item in scp_rows}),
        "metric_rows": len(metric_rows),
        "metric_sessions": len({_session_id(item) for item in metric_rows}),
        "scp_candidate_rows": sum(
            1 for item in scp_rows if _bool(item.get("v3_scp_candidate"))
        ),
        "scp_candidate_metric_rows": len(scp_candidate_metric),
        "scp_missing_table_rows": sum(
            1 for item in scp_rows if _bool(item.get("v3_scp_missing_table"))
        ),
        "scp_active_rows": sum(
            1 for item in scp_rows if _bool(item.get("v3_scp_active"))
        ),
        "scp_p95_above_target_rows": len(count_bridge),
        "truth_above_prior_rows": sum(
            1 for item in enriched if _positive(item.get("truth_minus_prior_max"))
        ),
        "target_below_truth_rows": sum(
            1 for item in enriched if _negative(item.get("target_minus_truth"))
        ),
        "cells_p90_under_rows": sum(
            1 for item in enriched if _positive(item.get("cells_p90_under_by"))
        ),
        "formal_p90_under_rows": sum(
            1 for item in enriched if _positive(item.get("formal_p90_under_by"))
        ),
        "count_cells_bridge_rows": len(count_cells_bridge),
        "count_value_bridge_rows": len(count_value_bridge),
        "count_cells_value_bridge_rows": len(count_cells_value_bridge),
        "truth_count": _numeric_summary(item.get("truth_count") for item in enriched),
        "target_count": _numeric_summary(item.get("target_count") for item in enriched),
        "scp_p95": _numeric_summary(item.get("scp_p95") for item in enriched),
        "scp_p95_minus_target": _numeric_summary(
            item.get("scp_p95_minus_target") for item in enriched
        ),
        "truth_minus_target": _numeric_summary(
            item.get("truth_minus_target") for item in enriched
        ),
        "truth_cells_per_item": _numeric_summary(
            item.get("truth_cells_per_item") for item in enriched
        ),
        "truth_formal_per_item": _numeric_summary(
            item.get("truth_formal_per_item") for item in enriched
        ),
        "cells_p90_under_by": _numeric_summary(
            item.get("cells_p90_under_by") for item in enriched
        ),
        "formal_p90_under_by": _numeric_summary(
            item.get("formal_p90_under_by") for item in enriched
        ),
        "scp_statuses": _counter_dict(
            (item.get("v3_scp_status") for item in scp_rows),
            top=top,
        ),
        "capacity_cases": _counter_dict(
            (item.get("v3_capacity_cases") for item in metric_rows),
            top=top,
        ),
        "fv_stress_classes": _counter_dict(
            (item.get("v3_fv_stress_class") for item in metric_rows),
            top=top,
        ),
    }
    row["bridge_status"] = _bridge_status(row)
    return row


def summarize_bridge(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    top: int = 12,
) -> dict[str, Any]:
    scp_rows = tuple(row for row in rows if _is_scp_row(row))
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in scp_rows:
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
            0 if str(row["bridge_status"]).startswith("watch") else 1,
            -int(row.get("count_cells_value_bridge_rows") or 0),
            -int(row.get("count_cells_bridge_rows") or 0),
            -int(row.get("count_value_bridge_rows") or 0),
            str(row["group"]),
        ),
    )
    overall = _summarize_bucket(
        scp_rows,
        group_field="overall",
        group="all",
        top=top,
    )
    return {
        "group_field": group_field,
        "groups": len(group_rows),
        "status_counts": dict(
            sorted(Counter(row["bridge_status"] for row in group_rows).items())
        ),
        "overall": overall,
        "rows": group_rows,
    }


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p50={summary['p50']}"
        f"/p90={summary['p90']}"
        f"/p95={summary['p95']}"
        f"/max={summary['max']}"
    )


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    overall = result["overall"]
    print(
        " ".join(
            (
                f"group_field={result['group_field']}",
                f"groups={result['groups']}",
                f"scp_rows={overall['scp_rows']}",
                f"metric_rows={overall['metric_rows']}",
                f"scp_candidate_rows={overall['scp_candidate_rows']}",
                f"scp_candidate_metric_rows={overall['scp_candidate_metric_rows']}",
                f"missing_table_rows={overall['scp_missing_table_rows']}",
                f"scp_p95_above_target_rows={overall['scp_p95_above_target_rows']}",
                f"truth_above_prior_rows={overall['truth_above_prior_rows']}",
                f"target_below_truth_rows={overall['target_below_truth_rows']}",
                f"cells_p90_under_rows={overall['cells_p90_under_rows']}",
                f"formal_p90_under_rows={overall['formal_p90_under_rows']}",
                f"count_cells_bridge_rows={overall['count_cells_bridge_rows']}",
                f"count_value_bridge_rows={overall['count_value_bridge_rows']}",
                f"count_cells_value_bridge_rows={overall['count_cells_value_bridge_rows']}",
                f"cells_per_item={_format_summary(overall['truth_cells_per_item'])}",
                f"formal_per_item={_format_summary(overall['truth_formal_per_item'])}",
                f"status_counts={_format_counts(result['status_counts'])}",
            )
        )
    )
    for row in result["rows"][:top]:
        print(
            " ".join(
                (
                    f"group={row['group']}",
                    f"status={row['bridge_status']}",
                    f"scp_rows={row['scp_rows']}",
                    f"metric_rows={row['metric_rows']}",
                    f"scp_candidate={row['scp_candidate_rows']}",
                    f"p95_above_target={row['scp_p95_above_target_rows']}",
                    f"cells_under={row['cells_p90_under_rows']}",
                    f"formal_under={row['formal_p90_under_rows']}",
                    f"count_cells={row['count_cells_bridge_rows']}",
                    f"count_value={row['count_value_bridge_rows']}",
                    f"count_cells_value={row['count_cells_value_bridge_rows']}",
                    f"truth_count={_format_summary(row['truth_count'])}",
                    f"scp_p95_minus_target={_format_summary(row['scp_p95_minus_target'])}",
                    f"cells_per_item={_format_summary(row['truth_cells_per_item'])}",
                    f"formal_per_item={_format_summary(row['truth_formal_per_item'])}",
                    f"capacity_cases={_format_counts(row['capacity_cases'])}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit v3 settlement count-prior count->cells/value bridge candidates.",
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
    result = {"errors": errors, **summarize_bridge(rows, group_field=args.by, top=args.top)}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_summary(result, top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
