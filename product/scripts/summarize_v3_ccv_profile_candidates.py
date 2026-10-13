"""Summarize v3 count/cell/value sampler candidates by evidence slice."""

from __future__ import annotations

import argparse
import json
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


DEFAULT_GROUP_FIELD = "hero_map_evidence_profile"
STATUS_SORT_ORDER: dict[str, int] = {
    "watch_only_count_cell_candidate": 0,
    "watch_only_needs_evidence": 1,
    "watch_only_neutral": 2,
    "blocked_under_count_cell_downshift": 3,
    "blocked_ccv_hurts": 4,
    "blocked_low_ccv_activity": 5,
    "blocked_low_sample": 6,
}
_SYSTEMIC_UNDER_BIAS_RATIO = 0.35
_SYSTEMIC_UNDER_RATE = 0.60
_MIN_CCV_LIKELIHOOD_RATE = 0.20
_COUNT_IMPROVE_THRESHOLD = 0.05
_CELLS_IMPROVE_THRESHOLD = 0.25
_COUNT_HURT_THRESHOLD = 0.05
_CELLS_HURT_THRESHOLD = 0.25
_VALUE_HURT_THRESHOLD = 10_000.0
_VALUE_IMPROVE_THRESHOLD = 10_000.0
_UNDER_COUNT_DOWNSHIFT_THRESHOLD = -0.05
_UNDER_CELLS_DOWNSHIFT_THRESHOLD = -0.50


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _paired_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and row.get("v3_ccv_ready")
        and _float_or_none(row.get("v3_post_q6_count_p50")) is not None
        and _float_or_none(row.get("v3_ccv_q6_count_p50")) is not None
        and _float_or_none(row.get("v3_truth_q6_count")) is not None
        and _float_or_none(row.get("v3_post_q6_cells_p50")) is not None
        and _float_or_none(row.get("v3_ccv_q6_cells_p50")) is not None
        and _float_or_none(row.get("v3_truth_q6_cells")) is not None
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


def _prediction_delta_pairs(
    rows: Iterable[dict[str, Any]],
    candidate_key: str,
    baseline_key: str,
) -> tuple[float, ...]:
    out: list[float] = []
    for row in rows:
        candidate = _float_or_none(row.get(candidate_key))
        baseline = _float_or_none(row.get(baseline_key))
        if candidate is not None and baseline is not None:
            out.append(candidate - baseline)
    return tuple(out)


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


def _rate(rows: Iterable[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> float | None:
    seq = tuple(rows)
    if not seq:
        return None
    return sum(1 for row in seq if predicate(row)) / len(seq)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _positive_improvement(row: dict[str, Any]) -> bool:
    count_delta = row.get("v3_ccv_delta_q6_count_p50_mae")
    cells_delta = row.get("v3_ccv_delta_q6_cells_p50_mae")
    formal_delta = row.get("v3_ccv_delta_q6_formal_p50_mae")
    return (
        (count_delta is not None and float(count_delta) <= -_COUNT_IMPROVE_THRESHOLD)
        or (
            cells_delta is not None
            and float(cells_delta) <= -_CELLS_IMPROVE_THRESHOLD
        )
        or (
            formal_delta is not None
            and float(formal_delta) <= -_VALUE_IMPROVE_THRESHOLD
        )
    )


def _candidate_flags(
    row: dict[str, Any],
    *,
    min_windows: int,
    min_sessions: int,
    min_ccv_likelihood_rate: float,
) -> tuple[str, ...]:
    flags: list[str] = []
    if int(row["n"]) < min_windows:
        flags.append("few_windows")
    if int(row["sessions"]) < min_sessions:
        flags.append("few_sessions")
    if float(row.get("ccv_likelihood_rate") or 0.0) < float(min_ccv_likelihood_rate):
        flags.append("low_ccv_likelihood_rate")
    formal_bias = row.get("formal_p50_bias")
    formal_mae = row.get("formal_p50_mae")
    below_rate = float(row.get("formal_p50_below_rate") or 0.0)
    if (
        formal_bias is not None
        and formal_mae is not None
        and float(formal_mae) > 0.0
        and float(formal_bias) <= -_SYSTEMIC_UNDER_BIAS_RATIO * float(formal_mae)
    ) or below_rate >= _SYSTEMIC_UNDER_RATE:
        flags.append("systemic_under")
    if float(row.get("public_total_rate") or 0.0) < 0.10:
        flags.append("little_public_total")
    if float(row.get("q6_floor_rate") or 0.0) < 0.20:
        flags.append("weak_q6_evidence")

    count_delta = row.get("v3_ccv_delta_q6_count_p50_mae")
    cells_delta = row.get("v3_ccv_delta_q6_cells_p50_mae")
    value_delta = row.get("v3_ccv_delta_q6_value_p50_mae")
    formal_delta = row.get("v3_ccv_delta_q6_formal_p50_mae")
    if count_delta is not None and float(count_delta) > _COUNT_HURT_THRESHOLD:
        flags.append("ccv_count_hurts")
    if cells_delta is not None and float(cells_delta) > _CELLS_HURT_THRESHOLD:
        flags.append("ccv_cells_hurts")
    if value_delta is not None and float(value_delta) > _VALUE_HURT_THRESHOLD:
        flags.append("ccv_value_hurts")
    if formal_delta is not None and float(formal_delta) > _VALUE_HURT_THRESHOLD:
        flags.append("ccv_formal_hurts")
    if (
        "systemic_under" in flags
        and float(row.get("v3_ccv_q6_count_prediction_delta_mean") or 0.0)
        <= _UNDER_COUNT_DOWNSHIFT_THRESHOLD
    ):
        flags.append("ccv_lowers_under_count")
    if (
        "systemic_under" in flags
        and float(row.get("v3_ccv_q6_cells_prediction_delta_mean") or 0.0)
        <= _UNDER_CELLS_DOWNSHIFT_THRESHOLD
    ):
        flags.append("ccv_lowers_under_cells")
    if not _positive_improvement(row):
        flags.append("no_material_improvement")
    return tuple(flags)


def _candidate_status(flags: tuple[str, ...]) -> str:
    if "few_windows" in flags or "few_sessions" in flags:
        return "blocked_low_sample"
    if "low_ccv_likelihood_rate" in flags:
        return "blocked_low_ccv_activity"
    if "systemic_under" in flags and (
        "ccv_lowers_under_count" in flags or "ccv_lowers_under_cells" in flags
    ):
        return "blocked_under_count_cell_downshift"
    if any(
        flag in flags
        for flag in (
            "ccv_count_hurts",
            "ccv_cells_hurts",
            "ccv_value_hurts",
            "ccv_formal_hurts",
        )
    ):
        return "blocked_ccv_hurts"
    if "no_material_improvement" in flags:
        return "watch_only_neutral"
    if "little_public_total" in flags or "weak_q6_evidence" in flags:
        return "watch_only_needs_evidence"
    return "watch_only_count_cell_candidate"


def summarize_candidates(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_ccv_likelihood_rate: float = _MIN_CCV_LIKELIHOOD_RATE,
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
        scope_counts = Counter(
            str(row.get("v3_ccv_match_scope") or "none") for row in group_rows
        )
        formal_p50 = _pairs(
            group_rows,
            "v3_post_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
        )
        formal_p90 = _pairs(
            group_rows,
            "v3_post_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
        )
        q6_count = _pairs(group_rows, "v3_post_q6_count_p50", "v3_truth_q6_count")
        q6_cells = _pairs(group_rows, "v3_post_q6_cells_p50", "v3_truth_q6_cells")
        q6_value = _pairs(group_rows, "v3_post_q6_value_p50", "v3_truth_q6_raw_value")
        q6_formal = _pairs(
            group_rows,
            "v3_post_q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
        )
        ccv_count = _pairs(group_rows, "v3_ccv_q6_count_p50", "v3_truth_q6_count")
        ccv_cells = _pairs(group_rows, "v3_ccv_q6_cells_p50", "v3_truth_q6_cells")
        ccv_value = _pairs(group_rows, "v3_ccv_q6_value_p50", "v3_truth_q6_raw_value")
        ccv_formal = _pairs(
            group_rows,
            "v3_ccv_q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
        )
        q6_count_mae = _mae(q6_count)
        q6_cells_mae = _mae(q6_cells)
        q6_value_mae = _mae(q6_value)
        q6_formal_mae = _mae(q6_formal)
        ccv_count_mae = _mae(ccv_count)
        ccv_cells_mae = _mae(ccv_cells)
        ccv_value_mae = _mae(ccv_value)
        ccv_formal_mae = _mae(ccv_formal)
        row = {
            "group_field": group_field,
            "group": group,
            "n": len(group_rows),
            "sessions": len(sessions),
            "strict_rate": _round_metric(
                _rate(
                    group_rows,
                    lambda item: item.get("v3_post_match_scope") == "strict",
                ),
                6,
            ),
            "summary_likelihood_rate": _round_metric(
                _rate(
                    group_rows,
                    lambda item: item.get("v3_post_match_scope")
                    == "summary_likelihood",
                ),
                6,
            ),
            "ccv_likelihood_rate": _round_metric(
                scope_counts.get("ccv_likelihood", 0) / len(group_rows),
                6,
            ),
            "formal_p50_mae": _round_metric(_mae(formal_p50), 1),
            "formal_p50_bias": _round_metric(_bias(formal_p50), 1),
            "formal_p50_below_rate": _round_metric(_below_rate(formal_p50), 6),
            "formal_p50_over_rate": _round_metric(_over_rate(formal_p50), 6),
            "formal_p90_coverage": _round_metric(_coverage_rate(formal_p90), 6),
            "q6_count_p50_mae": _round_metric(q6_count_mae, 2),
            "q6_cells_p50_mae": _round_metric(q6_cells_mae, 2),
            "q6_value_p50_mae": _round_metric(q6_value_mae, 1),
            "q6_formal_p50_mae": _round_metric(q6_formal_mae, 1),
            "v3_ccv_q6_count_p50_mae": _round_metric(ccv_count_mae, 2),
            "v3_ccv_delta_q6_count_p50_mae": _round_metric(
                _delta(ccv_count_mae, q6_count_mae),
                2,
            ),
            "v3_ccv_q6_cells_p50_mae": _round_metric(ccv_cells_mae, 2),
            "v3_ccv_delta_q6_cells_p50_mae": _round_metric(
                _delta(ccv_cells_mae, q6_cells_mae),
                2,
            ),
            "v3_ccv_q6_value_p50_mae": _round_metric(ccv_value_mae, 1),
            "v3_ccv_delta_q6_value_p50_mae": _round_metric(
                _delta(ccv_value_mae, q6_value_mae),
                1,
            ),
            "v3_ccv_q6_formal_p50_mae": _round_metric(ccv_formal_mae, 1),
            "v3_ccv_delta_q6_formal_p50_mae": _round_metric(
                _delta(ccv_formal_mae, q6_formal_mae),
                1,
            ),
            "v3_ccv_q6_count_prediction_delta_mean": _round_metric(
                _mean(
                    _prediction_delta_pairs(
                        group_rows,
                        "v3_ccv_q6_count_p50",
                        "v3_post_q6_count_p50",
                    )
                ),
                2,
            ),
            "v3_ccv_q6_cells_prediction_delta_mean": _round_metric(
                _mean(
                    _prediction_delta_pairs(
                        group_rows,
                        "v3_ccv_q6_cells_p50",
                        "v3_post_q6_cells_p50",
                    )
                ),
                2,
            ),
            "v3_ccv_q6_value_prediction_delta_mean": _round_metric(
                _mean(
                    _prediction_delta_pairs(
                        group_rows,
                        "v3_ccv_q6_value_p50",
                        "v3_post_q6_value_p50",
                    )
                ),
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
            min_ccv_likelihood_rate=min_ccv_likelihood_rate,
        )
        row["candidate_status"] = _candidate_status(flags)
        row["flags"] = list(flags)
        out.append(row)
    return sorted(
        out,
        key=lambda item: (
            STATUS_SORT_ORDER.get(str(item["candidate_status"]), 99),
            item["v3_ccv_delta_q6_cells_p50_mae"]
            if item["v3_ccv_delta_q6_cells_p50_mae"] is not None
            else 0.0,
            item["v3_ccv_delta_q6_count_p50_mae"]
            if item["v3_ccv_delta_q6_count_p50_mae"] is not None
            else 0.0,
            -(item["formal_p50_mae"] or 0.0),
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
                    f"bias={row['formal_p50_bias']}",
                    f"below={row['formal_p50_below_rate']}",
                    f"p90_cover={row['formal_p90_coverage']}",
                    f"ccv_rate={row['ccv_likelihood_rate']}",
                    f"q6_count_delta={row['v3_ccv_delta_q6_count_p50_mae']}",
                    f"q6_cells_delta={row['v3_ccv_delta_q6_cells_p50_mae']}",
                    f"q6_value_delta={row['v3_ccv_delta_q6_value_p50_mae']}",
                    f"q6_formal_delta={row['v3_ccv_delta_q6_formal_p50_mae']}",
                    f"q6_count_pred_delta={row['v3_ccv_q6_count_prediction_delta_mean']}",
                    f"q6_cells_pred_delta={row['v3_ccv_q6_cells_prediction_delta_mean']}",
                    f"public_total={row['public_total_rate']}",
                    f"q6_floor={row['q6_floor_rate']}",
                    f"flags={flags}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize profile-level v3 count/cell/value sampler candidates.",
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
    parser.add_argument(
        "--min-ccv-likelihood-rate",
        type=float,
        default=_MIN_CCV_LIKELIHOOD_RATE,
    )
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
        min_ccv_likelihood_rate=args.min_ccv_likelihood_rate,
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
