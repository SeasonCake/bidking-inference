"""Summarize v3 residual sampler candidates by hero/evidence profile."""

from __future__ import annotations

import argparse
import json
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
    "watch_only_over_correction_candidate": 0,
    "watch_only_neutral": 1,
    "blocked_under_value_downshift": 2,
    "blocked_systemic_under": 3,
    "blocked_residual_hurts": 4,
    "blocked_low_sample": 5,
}


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
        and row.get("v3_resid_ready")
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
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


def _rate(rows: Iterable[dict[str, Any]], predicate: Any) -> float | None:
    seq = tuple(rows)
    if not seq:
        return None
    return sum(1 for row in seq if predicate(row)) / len(seq)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


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
    if float(row.get("public_total_rate") or 0.0) < 0.10:
        flags.append("little_public_total")
    if float(row.get("q6_floor_rate") or 0.0) < 0.20:
        flags.append("weak_q6_evidence")
    formal_bias = row.get("formal_p50_bias")
    formal_mae = row.get("formal_p50_mae")
    below_rate = float(row.get("formal_p50_below_rate") or 0.0)
    over_rate = float(row.get("formal_p50_over_rate") or 0.0)
    if (
        formal_bias is not None
        and formal_mae is not None
        and float(formal_bias) <= -0.35 * float(formal_mae)
    ) or below_rate >= 0.60:
        flags.append("systemic_under")
    if over_rate >= 0.60:
        flags.append("high_over_rate")
    if (row.get("v3_resid_delta_q6_count_p50_mae") or 0.0) > 0.05:
        flags.append("resid_count_hurts")
    if (row.get("v3_resid_delta_q6_cells_p50_mae") or 0.0) > 0.25:
        flags.append("resid_cells_hurts")
    if (row.get("v3_resid_delta_q6_value_p50_mae") or 0.0) > 10_000:
        flags.append("resid_value_hurts")
    if (
        "systemic_under" in flags
        and (row.get("v3_resid_q6_value_prediction_delta_mean") or 0.0) < -10_000
    ):
        flags.append("resid_lowers_under_value")
    return tuple(flags)


def _candidate_status(flags: tuple[str, ...]) -> str:
    if "few_windows" in flags or "few_sessions" in flags:
        return "blocked_low_sample"
    if "systemic_under" in flags and "resid_lowers_under_value" in flags:
        return "blocked_under_value_downshift"
    if "systemic_under" in flags:
        return "blocked_systemic_under"
    if any(
        flag in flags
        for flag in ("resid_count_hurts", "resid_cells_hurts", "resid_value_hurts")
    ):
        return "blocked_residual_hurts"
    if "high_over_rate" in flags:
        return "watch_only_over_correction_candidate"
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
        scope_counts = Counter(
            str(row.get("v3_resid_match_scope") or "none") for row in group_rows
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
        resid_count = _pairs(group_rows, "v3_resid_q6_count_p50", "v3_truth_q6_count")
        resid_cells = _pairs(group_rows, "v3_resid_q6_cells_p50", "v3_truth_q6_cells")
        resid_value = _pairs(
            group_rows,
            "v3_resid_q6_value_p50",
            "v3_truth_q6_raw_value",
        )
        q6_count_mae = _mae(q6_count)
        q6_cells_mae = _mae(q6_cells)
        q6_value_mae = _mae(q6_value)
        resid_count_mae = _mae(resid_count)
        resid_cells_mae = _mae(resid_cells)
        resid_value_mae = _mae(resid_value)
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
            "resid_likelihood_rate": _round_metric(
                scope_counts.get("residual_likelihood", 0) / len(group_rows),
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
            "q6_value_p50_bias": _round_metric(_bias(q6_value), 1),
            "v3_resid_q6_count_p50_mae": _round_metric(resid_count_mae, 2),
            "v3_resid_delta_q6_count_p50_mae": _round_metric(
                _delta(resid_count_mae, q6_count_mae),
                2,
            ),
            "v3_resid_q6_cells_p50_mae": _round_metric(resid_cells_mae, 2),
            "v3_resid_delta_q6_cells_p50_mae": _round_metric(
                _delta(resid_cells_mae, q6_cells_mae),
                2,
            ),
            "v3_resid_q6_value_p50_mae": _round_metric(resid_value_mae, 1),
            "v3_resid_q6_value_p50_bias": _round_metric(_bias(resid_value), 1),
            "v3_resid_delta_q6_value_p50_mae": _round_metric(
                _delta(resid_value_mae, q6_value_mae),
                1,
            ),
            "v3_resid_q6_value_prediction_delta_mean": _round_metric(
                _mean(
                    _prediction_delta_pairs(
                        group_rows,
                        "v3_resid_q6_value_p50",
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
        )
        row["candidate_status"] = _candidate_status(flags)
        row["flags"] = list(flags)
        out.append(row)
    return sorted(
        out,
        key=lambda item: (
            STATUS_SORT_ORDER.get(str(item["candidate_status"]), 99),
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
                    f"over={row['formal_p50_over_rate']}",
                    f"p90_cover={row['formal_p90_coverage']}",
                    f"resid_rate={row['resid_likelihood_rate']}",
                    f"q6_count_delta={row['v3_resid_delta_q6_count_p50_mae']}",
                    f"q6_cells_delta={row['v3_resid_delta_q6_cells_p50_mae']}",
                    f"q6_value_delta={row['v3_resid_delta_q6_value_p50_mae']}",
                    f"q6_value_pred_delta={row['v3_resid_q6_value_prediction_delta_mean']}",
                    f"public_total={row['public_total_rate']}",
                    f"q6_floor={row['q6_floor_rate']}",
                    f"flags={flags}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize profile-level v3 residual sampler candidates.",
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
