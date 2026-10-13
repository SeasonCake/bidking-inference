"""Summarize v3 low-estimate repair candidates by hero/map or evidence profile."""

from __future__ import annotations

import argparse
import json
import math
import statistics
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


DEFAULT_GROUP_FIELD = "hero_map_id"
STATUS_SORT_ORDER: dict[str, int] = {
    "watch_only_upshift_candidate": 0,
    "watch_only_needs_evidence": 1,
    "blocked_repair_hurts": 2,
    "blocked_high_over": 3,
    "blocked_not_systemic_under": 4,
    "blocked_low_sample": 5,
}
_SYSTEMIC_UNDER_BIAS_RATIO = 0.35
_SYSTEMIC_UNDER_RATE = 0.60
_HIGH_OVER_RATE_GUARD = 0.60
_MAX_UPSHIFT = 1.25
_SHRINK_SESSION_HALF_LIFE = 45.0


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
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    )


def _pairs(
    rows: Iterable[dict[str, Any]],
    pred_key: str,
    truth_key: str,
    *,
    scale: float = 1.0,
) -> tuple[tuple[float, float], ...]:
    out: list[tuple[float, float]] = []
    for row in rows:
        pred = _float_or_none(row.get(pred_key))
        truth = _float_or_none(row.get(truth_key))
        if pred is not None and truth is not None:
            out.append((pred * scale, truth))
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


def _median_truth_pred_ratio(
    rows: Iterable[dict[str, Any]],
    pred_key: str,
    truth_key: str,
) -> float | None:
    ratios: list[float] = []
    for row in rows:
        pred = _float_or_none(row.get(pred_key))
        truth = _float_or_none(row.get(truth_key))
        if pred is None or truth is None or pred <= 0.0:
            continue
        ratio = truth / pred
        if math.isfinite(ratio) and ratio > 0.0:
            ratios.append(ratio)
    if not ratios:
        return None
    return float(statistics.median(ratios))


def _proposed_scale(
    rows: Iterable[dict[str, Any]],
    *,
    sessions: int,
    max_upshift: float,
) -> float:
    ratio = _median_truth_pred_ratio(
        rows,
        "v3_post_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
    )
    if ratio is None or ratio <= 1.0:
        return 1.0
    shrink = min(
        0.45,
        max(0.0, float(sessions)) / (max(0.0, float(sessions)) + _SHRINK_SESSION_HALF_LIFE),
    )
    return min(float(max_upshift), 1.0 + shrink * (ratio - 1.0))


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
    formal_bias = row.get("formal_p50_bias")
    formal_mae = row.get("formal_p50_mae")
    below_rate = float(row.get("formal_p50_below_rate") or 0.0)
    over_rate = float(row.get("formal_p50_over_rate") or 0.0)
    systemic_under = (
        formal_bias is not None
        and formal_mae is not None
        and float(formal_mae) > 0.0
        and float(formal_bias) <= -_SYSTEMIC_UNDER_BIAS_RATIO * float(formal_mae)
    ) or below_rate >= _SYSTEMIC_UNDER_RATE
    if not systemic_under:
        flags.append("not_systemic_under")
    if over_rate >= _HIGH_OVER_RATE_GUARD:
        flags.append("high_over_rate")
    if float(row.get("public_total_rate") or 0.0) < 0.10:
        flags.append("little_public_total")
    if float(row.get("q6_floor_rate") or 0.0) < 0.20:
        flags.append("weak_q6_evidence")
    scale = float(row.get("proposed_scale") or 1.0)
    if scale <= 1.000001:
        flags.append("no_upshift_scale")
    delta = row.get("scaled_delta_formal_p50_mae")
    if delta is not None and float(delta) > 0.0:
        flags.append("repair_hurts_mae")
    if float(row.get("scaled_formal_p50_over_rate") or 0.0) >= _HIGH_OVER_RATE_GUARD:
        flags.append("repair_high_over")
    return tuple(flags)


def _candidate_status(flags: tuple[str, ...]) -> str:
    if "few_windows" in flags or "few_sessions" in flags:
        return "blocked_low_sample"
    if "not_systemic_under" in flags or "no_upshift_scale" in flags:
        return "blocked_not_systemic_under"
    if "high_over_rate" in flags or "repair_high_over" in flags:
        return "blocked_high_over"
    if "repair_hurts_mae" in flags:
        return "blocked_repair_hurts"
    if (
        "hidden_requires_separate_validation" in flags
        or "little_public_total" in flags
        or "weak_q6_evidence" in flags
    ):
        return "watch_only_needs_evidence"
    return "watch_only_upshift_candidate"


def summarize_candidates(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    min_windows: int = 20,
    min_sessions: int = 8,
    max_upshift: float = _MAX_UPSHIFT,
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
        scale = _proposed_scale(
            group_rows,
            sessions=len(sessions),
            max_upshift=max_upshift,
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
        scaled_formal_p50 = _pairs(
            group_rows,
            "v3_post_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
            scale=scale,
        )
        scaled_formal_p90 = _pairs(
            group_rows,
            "v3_post_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
            scale=scale,
        )
        q6_formal_p50 = _pairs(
            group_rows,
            "v3_post_q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
        )
        scaled_q6_formal_p50 = _pairs(
            group_rows,
            "v3_post_q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
            scale=scale,
        )
        formal_mae = _mae(formal_p50)
        scaled_formal_mae = _mae(scaled_formal_p50)
        q6_formal_mae = _mae(q6_formal_p50)
        scaled_q6_formal_mae = _mae(scaled_q6_formal_p50)
        row = {
            "group_field": group_field,
            "group": group,
            "n": len(group_rows),
            "sessions": len(sessions),
            "proposed_scale": _round_metric(scale, 6),
            "formal_p50_mae": _round_metric(formal_mae, 1),
            "formal_p50_bias": _round_metric(_bias(formal_p50), 1),
            "formal_p50_below_rate": _round_metric(_below_rate(formal_p50), 6),
            "formal_p50_over_rate": _round_metric(_over_rate(formal_p50), 6),
            "formal_p90_coverage": _round_metric(_coverage_rate(formal_p90), 6),
            "scaled_formal_p50_mae": _round_metric(scaled_formal_mae, 1),
            "scaled_delta_formal_p50_mae": _round_metric(
                scaled_formal_mae - formal_mae
                if scaled_formal_mae is not None and formal_mae is not None
                else None,
                1,
            ),
            "scaled_formal_p50_bias": _round_metric(_bias(scaled_formal_p50), 1),
            "scaled_formal_p50_below_rate": _round_metric(
                _below_rate(scaled_formal_p50),
                6,
            ),
            "scaled_formal_p50_over_rate": _round_metric(
                _over_rate(scaled_formal_p50),
                6,
            ),
            "scaled_formal_p90_coverage": _round_metric(
                _coverage_rate(scaled_formal_p90),
                6,
            ),
            "q6_formal_p50_mae": _round_metric(q6_formal_mae, 1),
            "scaled_q6_formal_p50_mae": _round_metric(scaled_q6_formal_mae, 1),
            "scaled_delta_q6_formal_p50_mae": _round_metric(
                scaled_q6_formal_mae - q6_formal_mae
                if scaled_q6_formal_mae is not None and q6_formal_mae is not None
                else None,
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
            item["scaled_delta_formal_p50_mae"]
            if item["scaled_delta_formal_p50_mae"] is not None
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
                    f"scale={row['proposed_scale']}",
                    f"mae={row['formal_p50_mae']}",
                    f"scaled_mae={row['scaled_formal_p50_mae']}",
                    f"delta={row['scaled_delta_formal_p50_mae']}",
                    f"bias={row['formal_p50_bias']}",
                    f"scaled_bias={row['scaled_formal_p50_bias']}",
                    f"below={row['formal_p50_below_rate']}",
                    f"scaled_below={row['scaled_formal_p50_below_rate']}",
                    f"over={row['formal_p50_over_rate']}",
                    f"scaled_over={row['scaled_formal_p50_over_rate']}",
                    f"p90_cover={row['formal_p90_coverage']}",
                    f"scaled_p90_cover={row['scaled_formal_p90_coverage']}",
                    f"q6_delta={row['scaled_delta_q6_formal_p50_mae']}",
                    f"public_total={row['public_total_rate']}",
                    f"q6_floor={row['q6_floor_rate']}",
                    f"flags={flags}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize v3 low-estimate repair candidates.",
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
    parser.add_argument("--max-upshift", type=float, default=_MAX_UPSHIFT)
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
        max_upshift=args.max_upshift,
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
