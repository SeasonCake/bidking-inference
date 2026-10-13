"""Summarize v3 tail/value review candidates by evidence slice."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

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
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)


DEFAULT_GROUP_FIELD = "hero_map_id"
STATUS_SORT_ORDER: dict[str, int] = {
    "watch_only_q6_tail_value_candidate": 0,
    "watch_only_tail_value_candidate": 1,
    "watch_only_needs_evidence": 2,
    "watch_only_neutral": 3,
    "blocked_tail_estimate_hurts": 4,
    "blocked_no_tail_signal": 5,
    "blocked_low_sample": 6,
}
_TAIL_SIGNAL_RATE = 0.15
_Q6_TAIL_SIGNAL_RATE = 0.10
_TAIL_VALUE_SIGNAL = 50_000.0
_MAE_IMPROVE_THRESHOLD = 10_000.0
_P90_MISS_RATE = 0.20
_SYSTEMIC_UNDER_RATE = 0.50
_STRONG_P90_MISS_RATE = 0.35


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _median(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return float(statistics.median(seq))


def _paired_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
        and _float_or_none(row.get("v3_truth_tail_replacement_decision_value"))
        is not None
        and _float_or_none(row.get("v3_post_formal_decision_value_p50"))
        is not None
        and _float_or_none(row.get("v3_post_tail_replacement_decision_value_p50"))
        is not None
    )


def _pairs(
    rows: Iterable[dict[str, Any]],
    pred_key: str,
    truth_key: str,
) -> tuple[tuple[float, float], ...]:
    out: list[tuple[float, float]] = []
    for row in rows:
        pred = _float_or_none(row.get(pred_key))
        truth = _float_or_none(row.get(truth_key))
        if pred is not None and truth is not None:
            out.append((pred, truth))
    return tuple(out)


def _values(rows: Iterable[dict[str, Any]], key: str) -> tuple[float, ...]:
    out: list[float] = []
    for row in rows:
        value = _float_or_none(row.get(key))
        if value is not None:
            out.append(value)
    return tuple(out)


def _mae(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(abs(pred - truth) for pred, truth in pairs)


def _bias(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(pred - truth for pred, truth in pairs)


def _below_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred < truth else 0.0 for pred, truth in pairs)


def _coverage_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)


def _rate(rows: Iterable[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> float | None:
    seq = tuple(rows)
    if not seq:
        return None
    return sum(1 for row in seq if predicate(row)) / len(seq)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _tail_value(row: dict[str, Any], key: str) -> float:
    value = _float_or_none(row.get(key))
    return float(value or 0.0)


def _map_id_from_group(value: Any) -> int | None:
    try:
        return int(str(value or "").split("|", 1)[-1])
    except (TypeError, ValueError):
        return None


def _candidate_flags(
    row: dict[str, Any],
    *,
    min_windows: int,
    min_sessions: int,
) -> tuple[str, ...]:
    flags: list[str] = []
    if int(row["n"]) < min_windows:
        flags.append("few_windows")
    if int(row["sessions"]) < min_sessions:
        flags.append("few_sessions")
    map_id = _map_id_from_group(row.get("group"))
    if map_id is not None and 2600 <= map_id < 2700:
        flags.append("hidden_requires_separate_validation")
    tail_rate = float(row.get("tail_replacement_value_rate") or 0.0)
    q6_tail_rate = float(row.get("q6_tail_replacement_value_rate") or 0.0)
    tail_median = float(row.get("tail_replacement_value_median") or 0.0)
    q6_tail_median = float(row.get("q6_tail_replacement_value_median") or 0.0)
    if (
        tail_rate < _TAIL_SIGNAL_RATE
        and q6_tail_rate < _Q6_TAIL_SIGNAL_RATE
        and max(tail_median, q6_tail_median) < _TAIL_VALUE_SIGNAL
    ):
        flags.append("no_tail_signal")
    if float(row.get("public_total_rate") or 0.0) < 0.10:
        flags.append("little_public_total")
    if float(row.get("q6_floor_rate") or 0.0) < 0.20:
        flags.append("weak_q6_evidence")
    formal_below = float(row.get("formal_p50_below_rate") or 0.0)
    strongest_p90_miss = max(
        float(row.get("tail_replacement_p90_under_rate") or 0.0),
        float(row.get("q6_tail_replacement_p90_under_rate") or 0.0),
    )
    if (
        formal_below < _SYSTEMIC_UNDER_RATE
        and strongest_p90_miss < _STRONG_P90_MISS_RATE
    ):
        flags.append("weak_tail_under_context")
    tail_delta = row.get("tail_replacement_delta_mae_vs_formal_to_tail")
    q6_tail_delta = row.get("q6_tail_replacement_delta_mae_vs_formal_to_tail")
    if tail_delta is not None and float(tail_delta) > _MAE_IMPROVE_THRESHOLD:
        flags.append("tail_estimate_hurts_total")
    if q6_tail_delta is not None and float(q6_tail_delta) > _MAE_IMPROVE_THRESHOLD:
        flags.append("tail_estimate_hurts_q6")
    if tail_delta is not None and float(tail_delta) <= -_MAE_IMPROVE_THRESHOLD:
        flags.append("tail_estimate_improves_total")
    if q6_tail_delta is not None and float(q6_tail_delta) <= -_MAE_IMPROVE_THRESHOLD:
        flags.append("tail_estimate_improves_q6")
    if float(row.get("tail_replacement_p90_under_rate") or 0.0) >= _P90_MISS_RATE:
        flags.append("tail_p90_miss")
    if float(row.get("q6_tail_replacement_p90_under_rate") or 0.0) >= _P90_MISS_RATE:
        flags.append("q6_tail_p90_miss")
    return tuple(flags)


def _candidate_status(flags: tuple[str, ...]) -> str:
    if "few_windows" in flags or "few_sessions" in flags:
        return "blocked_low_sample"
    if "tail_estimate_hurts_total" in flags or "tail_estimate_hurts_q6" in flags:
        return "blocked_tail_estimate_hurts"
    if "no_tail_signal" in flags:
        return "blocked_no_tail_signal"
    if (
        "hidden_requires_separate_validation" in flags
        or "little_public_total" in flags
        or "weak_q6_evidence" in flags
        or "weak_tail_under_context" in flags
    ):
        return "watch_only_needs_evidence"
    if "tail_estimate_improves_q6" in flags or "q6_tail_p90_miss" in flags:
        return "watch_only_q6_tail_value_candidate"
    if "tail_estimate_improves_total" in flags or "tail_p90_miss" in flags:
        return "watch_only_tail_value_candidate"
    return "watch_only_neutral"


def summarize_candidates(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    min_windows: int = 20,
    min_sessions: int = 8,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _paired_rows(rows):
        value = row.get(group_field)
        groups[str(value) if value not in (None, "") else "unknown"].append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in groups.items():
        sessions = {
            str(row.get("session_id"))
            for row in group_rows
            if row.get("session_id") not in (None, "")
        }
        formal = _pairs(
            group_rows,
            "v3_post_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
        )
        formal_p90 = _pairs(
            group_rows,
            "v3_post_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
        )
        formal_to_tail = _pairs(
            group_rows,
            "v3_post_formal_decision_value_p50",
            "v3_truth_tail_replacement_decision_value",
        )
        tail = _pairs(
            group_rows,
            "v3_post_tail_replacement_decision_value_p50",
            "v3_truth_tail_replacement_decision_value",
        )
        tail_p90 = _pairs(
            group_rows,
            "v3_post_tail_replacement_decision_value_p90",
            "v3_truth_tail_replacement_decision_value",
        )
        q6_formal_to_tail = _pairs(
            group_rows,
            "v3_post_q6_formal_decision_value_p50",
            "v3_truth_q6_tail_replacement_decision_value",
        )
        q6_tail = _pairs(
            group_rows,
            "v3_post_q6_tail_replacement_decision_value_p50",
            "v3_truth_q6_tail_replacement_decision_value",
        )
        q6_tail_p90 = _pairs(
            group_rows,
            "v3_post_q6_tail_replacement_decision_value_p90",
            "v3_truth_q6_tail_replacement_decision_value",
        )
        formal_mae = _mae(formal)
        formal_to_tail_mae = _mae(formal_to_tail)
        tail_mae = _mae(tail)
        q6_formal_to_tail_mae = _mae(q6_formal_to_tail)
        q6_tail_mae = _mae(q6_tail)
        tail_values = _values(group_rows, "v3_truth_tail_replacement_value")
        q6_tail_values = _values(group_rows, "v3_truth_q6_tail_replacement_value")
        row = {
            "group_field": group_field,
            "group": group,
            "n": len(group_rows),
            "sessions": len(sessions),
            "formal_p50_mae": _round_metric(formal_mae, 1),
            "formal_p50_bias": _round_metric(_bias(formal), 1),
            "formal_p50_below_rate": _round_metric(_below_rate(formal), 6),
            "formal_p90_coverage": _round_metric(_coverage_rate(formal_p90), 6),
            "formal_to_tail_replacement_p50_mae": _round_metric(
                formal_to_tail_mae,
                1,
            ),
            "tail_replacement_p50_mae": _round_metric(tail_mae, 1),
            "tail_replacement_delta_mae_vs_formal_to_tail": _round_metric(
                _delta(tail_mae, formal_to_tail_mae),
                1,
            ),
            "tail_replacement_p50_bias": _round_metric(_bias(tail), 1),
            "tail_replacement_p90_coverage": _round_metric(
                _coverage_rate(tail_p90),
                6,
            ),
            "tail_replacement_p90_under_rate": _round_metric(
                1.0 - (_coverage_rate(tail_p90) or 0.0),
                6,
            ),
            "q6_formal_to_tail_replacement_p50_mae": _round_metric(
                q6_formal_to_tail_mae,
                1,
            ),
            "q6_tail_replacement_p50_mae": _round_metric(q6_tail_mae, 1),
            "q6_tail_replacement_delta_mae_vs_formal_to_tail": _round_metric(
                _delta(q6_tail_mae, q6_formal_to_tail_mae),
                1,
            ),
            "q6_tail_replacement_p50_bias": _round_metric(_bias(q6_tail), 1),
            "q6_tail_replacement_p90_coverage": _round_metric(
                _coverage_rate(q6_tail_p90),
                6,
            ),
            "q6_tail_replacement_p90_under_rate": _round_metric(
                1.0 - (_coverage_rate(q6_tail_p90) or 0.0),
                6,
            ),
            "tail_replacement_value_rate": _round_metric(
                _rate(
                    group_rows,
                    lambda item: _tail_value(item, "v3_truth_tail_replacement_value")
                    > 0,
                ),
                6,
            ),
            "tail_replacement_value_median": _round_metric(_median(tail_values), 1),
            "tail_replacement_value_mean": _round_metric(_mean(tail_values), 1),
            "q6_tail_replacement_value_rate": _round_metric(
                _rate(
                    group_rows,
                    lambda item: _tail_value(
                        item,
                        "v3_truth_q6_tail_replacement_value",
                    )
                    > 0,
                ),
                6,
            ),
            "q6_tail_replacement_value_median": _round_metric(
                _median(q6_tail_values),
                1,
            ),
            "q6_tail_replacement_value_mean": _round_metric(
                _mean(q6_tail_values),
                1,
            ),
            "public_total_rate": _round_metric(
                _rate(
                    group_rows,
                    lambda item: item.get("v3_summary_session_total_cells_exact")
                    is not None
                    or item.get("v3_summary_session_total_count_exact") is not None,
                ),
                6,
            ),
            "q6_floor_rate": _round_metric(
                _rate(
                    group_rows,
                    lambda item: bool(item.get("v3_summary_q6_count_floor"))
                    or bool(item.get("v3_summary_q6_cells_floor"))
                    or bool(item.get("v3_summary_q6_value_floor")),
                ),
                6,
            ),
        }
        flags = _candidate_flags(
            row,
            min_windows=min_windows,
            min_sessions=min_sessions,
        )
        row["candidate_status"] = _candidate_status(flags)
        row["flags"] = list(flags)
        out.append(row)
    return sorted(
        out,
        key=lambda item: (
            STATUS_SORT_ORDER.get(str(item["candidate_status"]), 99),
            item["q6_tail_replacement_delta_mae_vs_formal_to_tail"]
            if item["q6_tail_replacement_delta_mae_vs_formal_to_tail"] is not None
            else 0.0,
            item["tail_replacement_delta_mae_vs_formal_to_tail"]
            if item["tail_replacement_delta_mae_vs_formal_to_tail"] is not None
            else 0.0,
            -(item["tail_replacement_value_mean"] or 0.0),
            str(item["group"]),
        ),
    )


def _print_table(rows: list[dict[str, Any]], *, top: int) -> None:
    for row in rows[:top]:
        flags = "+".join(row["flags"]) if row["flags"] else "none"
        print(
            " ".join(
                (
                    f"{row['group_field']}={row['group']}",
                    f"status={row['candidate_status']}",
                    f"n={row['n']}",
                    f"sessions={row['sessions']}",
                    f"formal_mae={row['formal_p50_mae']}",
                    f"formal_below={row['formal_p50_below_rate']}",
                    f"tail_rate={row['tail_replacement_value_rate']}",
                    f"tail_value_med={row['tail_replacement_value_median']}",
                    f"tail_delta={row['tail_replacement_delta_mae_vs_formal_to_tail']}",
                    f"tail_p90_under={row['tail_replacement_p90_under_rate']}",
                    f"q6_tail_rate={row['q6_tail_replacement_value_rate']}",
                    f"q6_tail_value_med={row['q6_tail_replacement_value_median']}",
                    f"q6_tail_delta={row['q6_tail_replacement_delta_mae_vs_formal_to_tail']}",
                    f"q6_tail_p90_under={row['q6_tail_replacement_p90_under_rate']}",
                    f"public_total={row['public_total_rate']}",
                    f"q6_floor={row['q6_floor_rate']}",
                    f"flags={flags}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize v3 tail/value review candidates.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--by", default=DEFAULT_GROUP_FIELD)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
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
    )
    candidates = summarize_candidates(
        rows,
        group_field=args.by,
        min_windows=args.min_windows,
        min_sessions=args.min_sessions,
    )
    status_counts = Counter(str(row["candidate_status"]) for row in candidates)
    result = {
        "errors": errors,
        "status_counts": dict(sorted(status_counts.items())),
        "candidates": candidates,
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        print(
            "status_counts="
            + ",".join(
                f"{status}:{count}"
                for status, count in sorted(status_counts.items())
            )
        )
        _print_table(candidates, top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
