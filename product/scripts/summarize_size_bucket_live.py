"""Summarize live ``model_eval.jsonl`` by N-cell average tool usage (100169-100173).

Compares decision-value P50 error between sessions that used size-avg tools (especially
``100172`` 四格均价) versus sessions without, and reports whether posterior diagnostics
contain ``size_bucket:*`` tokens.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _numeric(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_value(row: dict[str, Any], key: str) -> float | None:
    if key == "decision_value_p50_error":
        decision_p50 = _numeric(row, "decision_value_p50")
        final_replacement_decision_value = _numeric(
            row,
            "final_decision_value_with_tail_replacement",
        )
        if final_replacement_decision_value is None:
            final_replacement_decision_value = _numeric(row, "final_decision_value")
        if final_replacement_decision_value is None:
            final_replacement_decision_value = _numeric(row, "final_value")
        if decision_p50 is not None and final_replacement_decision_value is not None:
            return decision_p50 - final_replacement_decision_value
    return _numeric(row, key)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _dedupe_latest_by_file(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        file_name = str(row.get("file") or "")
        if not file_name:
            continue
        prev = latest.get(file_name)
        if prev is None or float(row.get("ts") or 0) >= float(prev.get("ts") or 0):
            latest[file_name] = row
    return list(latest.values())


def _mae(rows: list[dict[str, Any]], key: str = "decision_value_p50_error") -> float | None:
    values = [_metric_value(row, key) for row in rows]
    abs_values = [abs(v) for v in values if v is not None]
    if not abs_values:
        return None
    return round(statistics.mean(abs_values), 1)


def _median_abs_error(
    rows: list[dict[str, Any]],
    key: str = "decision_value_p50_error",
) -> float | None:
    values = [_metric_value(row, key) for row in rows]
    abs_values = [abs(v) for v in values if v is not None]
    if not abs_values:
        return None
    return round(statistics.median(abs_values), 1)


def _bias(rows: list[dict[str, Any]], key: str = "decision_value_p50_error") -> float | None:
    values = [_metric_value(row, key) for row in rows]
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return round(statistics.mean(clean), 1)


def _summarize_group(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    valued = [
        row
        for row in rows
        if _metric_value(row, "decision_value_p50_error") is not None
        and _numeric(row, "final_value") is not None
    ]
    return {
        "label": label,
        "rows": len(rows),
        "valued_rows": len(valued),
        "decision_p50_mae": _mae(valued),
        "decision_p50_median_abs_error": _median_abs_error(valued),
        "decision_p50_bias": _bias(valued),
        "size_bucket_active_rate": round(
            statistics.mean(1.0 if row.get("size_bucket_active") else 0.0 for row in rows),
            4,
        )
        if rows
        else None,
        "action_100172_rate": round(
            statistics.mean(1.0 if row.get("action_100172_used") else 0.0 for row in rows),
            4,
        )
        if rows
        else None,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [
        row
        for row in rows
        if _numeric(row, "final_value") is not None
        and _numeric(row, "decision_value_p50") is not None
    ]
    with_100172 = [row for row in settled if row.get("action_100172_used")]
    without_100172 = [row for row in settled if not row.get("action_100172_used")]
    with_any_size_avg = [
        row for row in settled if int(row.get("action_size_avg_tool_count") or 0) > 0
    ]
    without_any_size_avg = [
        row
        for row in settled
        if int(row.get("action_size_avg_tool_count") or 0) <= 0
    ]
    with_active_inference = [row for row in settled if row.get("size_bucket_active")]
    with_reading_no_inference = [
        row
        for row in settled
        if int(row.get("action_size_avg_tool_count") or 0) > 0
        and not row.get("size_bucket_active")
    ]
    return {
        "total_rows": len(rows),
        "settled_rows": len(settled),
        "groups": [
            _summarize_group("all_settled", settled),
            _summarize_group("action_100172_used", with_100172),
            _summarize_group("action_100172_not_used", without_100172),
            _summarize_group("any_size_avg_tool", with_any_size_avg),
            _summarize_group("no_size_avg_tool", without_any_size_avg),
            _summarize_group("size_bucket_active", with_active_inference),
            _summarize_group("size_avg_tool_but_no_size_bucket_diag", with_reading_no_inference),
        ],
    }


def _print_report(summary: dict[str, Any]) -> None:
    print(f"total_rows={summary['total_rows']} settled_rows={summary['settled_rows']}")
    print()
    print("label,rows,valued,MAE,bias,median_abs,size_bucket_rate,100172_rate")
    for group in summary["groups"]:
        print(
            f"{group['label']},"
            f"{group['rows']},"
            f"{group['valued_rows']},"
            f"{group.get('decision_p50_mae')},"
            f"{group.get('decision_p50_bias')},"
            f"{group.get('decision_p50_median_abs_error')},"
            f"{group.get('size_bucket_active_rate')},"
            f"{group.get('action_100172_rate')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=ROOT / "data" / "logs" / "live" / "model_eval.jsonl",
        help="Path to model_eval.jsonl",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep all rows instead of latest row per file",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional JSON output path",
    )
    args = parser.parse_args()
    if not args.log.exists():
        print(f"Missing log: {args.log}", file=sys.stderr)
        return 1
    rows = _load_rows(args.log)
    if not args.no_dedupe:
        rows = _dedupe_latest_by_file(rows)
    summary = summarize(rows)
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_report(summary)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
