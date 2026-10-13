"""Audit pre-bid guard precision for v3 capacity/source expansion.

This script is diagnostic-only. It joins archive pre-bid evaluator rows with
settlement source-semantics truth by capture file, then scores guard tiers that
are available before settlement. It does not change any sampler or bid path.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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
    _default_capacity_source_expansion_path,
    _default_paths,
    _default_settlement_count_prior_path,
    _default_tail_value_review_path,
    _default_underestimate_repair_path,
    evaluate_paths,
    load_capacity_source_expansion_entries,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)
from summarize_v3_capacity_source_expansion_holdout import (  # noqa: E402
    _format_counts,
    _rows_for_paths,
    _source_semantics_truth,
)


DEFAULT_GUARDS = (
    "cse_candidate",
    "pressure_candidate",
    "target_above_prior_max",
    "target_ge_prior_max",
    "target_near_source_p95_5",
    "target_near_source_p95_10",
    "round_ge4_candidate",
)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _base_file(row: Mapping[str, Any]) -> str:
    return str(row.get("file") or "").split("#", 1)[0]


def _truth_by_file(rows: Iterable[Mapping[str, Any]]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        out[str(row.get("file") or "")] = _source_semantics_truth(row)
    return out


def _target_prior_delta(row: Mapping[str, Any]) -> float | None:
    compact = _float_or_none(row.get("v3_cse_target_prior_max_delta"))
    if compact is not None:
        return compact
    target = _float_or_none(row.get("v3_cse_target_count"))
    prior_max = _float_or_none(row.get("v3_cse_prior_items_per_session_max"))
    if target is None or prior_max is None:
        return None
    return target - prior_max


def _guard_applies(row: Mapping[str, Any], guard: str) -> bool:
    candidate = bool(row.get("v3_cse_candidate"))
    if guard == "cse_candidate":
        return candidate
    if guard == "pressure_candidate":
        return bool(row.get("v3_cse_pressure_candidate")) or (
            candidate
            and (_target_prior_delta(row) is not None)
            and (_target_prior_delta(row) or 0.0) > 0.0
        )
    if guard == "target_above_prior_max":
        delta = _target_prior_delta(row)
        return candidate and delta is not None and delta > 0.0
    if guard == "target_ge_prior_max":
        delta = _target_prior_delta(row)
        return candidate and delta is not None and delta >= 0.0
    if guard == "target_near_source_p95_5":
        delta = _float_or_none(row.get("v3_cse_target_to_unique_non_temp_p95_delta"))
        return candidate and delta is not None and delta >= -5.0
    if guard == "target_near_source_p95_10":
        delta = _float_or_none(row.get("v3_cse_target_to_unique_non_temp_p95_delta"))
        return candidate and delta is not None and delta >= -10.0
    if guard == "round_ge4_candidate":
        return candidate and int(row.get("round") or 0) >= 4
    raise ValueError(f"unsupported guard: {guard}")


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _guard_summary(
    rows: Iterable[Mapping[str, Any]],
    *,
    truth_by_file: Mapping[str, bool],
    guard: str,
) -> dict[str, Any]:
    seq = tuple(
        row
        for row in rows
        if row.get("status") == "ready" and row.get("v3_cse_ready")
    )
    truth_rows = [row for row in seq if truth_by_file.get(_base_file(row))]
    selected = [row for row in seq if _guard_applies(row, guard)]
    covered = [row for row in selected if truth_by_file.get(_base_file(row))]
    truth_sessions = { _base_file(row) for row in truth_rows }
    selected_sessions = { _base_file(row) for row in selected }
    covered_sessions = truth_sessions & selected_sessions
    return {
        "guard": guard,
        "ready_rows": len(seq),
        "truth_rows": len(truth_rows),
        "selected_rows": len(selected),
        "covered_truth_rows": len(covered),
        "missed_truth_rows": max(0, len(truth_rows) - len(covered)),
        "false_positive_rows": max(0, len(selected) - len(covered)),
        "row_recall": _rate(len(covered), len(truth_rows)),
        "row_precision": _rate(len(covered), len(selected)),
        "truth_sessions": len(truth_sessions),
        "selected_sessions": len(selected_sessions),
        "covered_truth_sessions": len(covered_sessions),
        "missed_truth_sessions": max(0, len(truth_sessions) - len(covered_sessions)),
        "false_positive_sessions": max(
            0, len(selected_sessions) - len(covered_sessions)
        ),
        "session_recall": _rate(len(covered_sessions), len(truth_sessions)),
        "session_precision": _rate(len(covered_sessions), len(selected_sessions)),
        "rounds": dict(
            sorted(
                Counter(
                    str(row.get("round") or "none")
                    for row in selected
                ).items()
            )
        ),
        "target_sources": dict(
            sorted(
                Counter(
                    str(row.get("v3_cse_target_count_source") or "none")
                    for row in selected
                ).items()
            )
        ),
    }


def summarize_prebid_guard(
    paths: Iterable[Path] = (),
    *,
    rows: Iterable[Mapping[str, Any]] | None = None,
    truth_by_file: Mapping[str, bool] | None = None,
    guards: Iterable[str] = DEFAULT_GUARDS,
    posterior_trials: int = 64,
) -> dict[str, Any]:
    selected_paths = tuple(paths) or _default_paths()
    errors: list[dict[str, str]] = []
    if rows is None:
        tables = load_monitor_tables()
        rows, errors = evaluate_paths(
            selected_paths,
            tables=tables,
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
            capacity_source_expansion_entries=load_capacity_source_expansion_entries(
                _default_capacity_source_expansion_path()
            ),
            posterior_trials=posterior_trials,
        )
    seq = tuple(rows)
    if truth_by_file is None:
        truth_by_file = _truth_by_file(_rows_for_paths(selected_paths))
    ready_rows = [
        row for row in seq if row.get("status") == "ready" and row.get("v3_cse_ready")
    ]
    truth_rows = [row for row in ready_rows if truth_by_file.get(_base_file(row))]
    guard_rows = [
        _guard_summary(ready_rows, truth_by_file=truth_by_file, guard=guard)
        for guard in guards
    ]
    return {
        "files": len({ _base_file(row) for row in ready_rows }),
        "ready_rows": len(ready_rows),
        "truth_rows": len(truth_rows),
        "truth_sessions": len({ _base_file(row) for row in truth_rows }),
        "parse_errors": len(errors),
        "guards": guard_rows,
    }


def _print_summary(result: Mapping[str, Any]) -> None:
    print(
        " ".join(
            (
                f"files={result['files']}",
                f"ready_rows={result['ready_rows']}",
                f"truth_rows={result['truth_rows']}",
                f"truth_sessions={result['truth_sessions']}",
                f"parse_errors={result['parse_errors']}",
            )
        )
    )
    for row in result["guards"]:
        print(
            " ".join(
                (
                    f"guard={row['guard']}",
                    f"selected_rows={row['selected_rows']}",
                    f"covered_rows={row['covered_truth_rows']}",
                    f"missed_rows={row['missed_truth_rows']}",
                    f"fp_rows={row['false_positive_rows']}",
                    f"row_recall={row['row_recall']}",
                    f"row_precision={row['row_precision']}",
                    f"selected_sessions={row['selected_sessions']}",
                    f"covered_sessions={row['covered_truth_sessions']}",
                    f"missed_sessions={row['missed_truth_sessions']}",
                    f"fp_sessions={row['false_positive_sessions']}",
                    f"session_recall={row['session_recall']}",
                    f"session_precision={row['session_precision']}",
                    f"rounds={_format_counts(row['rounds'])}",
                    f"target_sources={_format_counts(row['target_sources'])}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize v3 CSE pre-bid pressure guard precision.",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--posterior-trials",
        type=int,
        default=64,
        help="Archive evaluator posterior trials used to emit v3 shadow fields.",
    )
    parser.add_argument(
        "--format",
        choices=("summary", "json"),
        default="summary",
    )
    args = parser.parse_args(argv)
    result = summarize_prebid_guard(
        args.paths,
        posterior_trials=args.posterior_trials,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
