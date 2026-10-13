"""Cross-validate formal decision candidates from q6 formal deltas."""

from __future__ import annotations

import argparse
import hashlib
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
    V3CcvOptions,
    _default_calibration_path,
    _default_paths,
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)

DEFAULT_GROUP_FIELD = "evidence_profile_key"
_SYSTEMIC_UNDER_BIAS_RATIO = 0.35
_SYSTEMIC_UNDER_RATE = 0.60
_MIN_Q6_FORMAL_UPSHIFT = 10_000.0
_MAX_FORMAL_MAE_HURT = 10_000.0
_MAX_Q6_FORMAL_MAE_HURT = 10_000.0
_MAX_BELOW_RATE_INCREASE = 0.02
_MAX_OVER_RATE_INCREASE = 0.08
_MAX_CANDIDATE_OVER_RATE = 0.60
_MAX_P90_COVERAGE_DROP = 0.05


def _stable_fold(value: Any, folds: int) -> int:
    if folds <= 1:
        return 0
    digest = hashlib.sha1(str(value or "unknown").encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % int(folds)


def _session_id(row: dict[str, Any]) -> str:
    value = row.get("session_id")
    if value not in (None, ""):
        return str(value)
    return str(row.get("file") or "unknown")


def _group_value(row: dict[str, Any], field: str) -> str:
    parts = tuple(part.strip() for part in str(field).split(",") if part.strip())
    if len(parts) > 1:
        return "|".join(f"{part}={_group_value(row, part)}" for part in parts)
    value = row.get(field)
    return str(value) if value not in (None, "") else "unknown"


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


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


def _pinball_loss(truth: float, prediction: float, quantile: float) -> float:
    delta = truth - prediction
    if delta >= 0:
        return quantile * delta
    return (1.0 - quantile) * (-delta)


def _pinball(
    pairs: Iterable[tuple[float, float]],
    quantile: float,
) -> float | None:
    return _mean(_pinball_loss(truth, pred, quantile) for pred, truth in pairs)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _rate(rows: Iterable[dict[str, Any]], predicate: Any) -> float | None:
    seq = tuple(rows)
    if not seq:
        return None
    return sum(1 for row in seq if predicate(row)) / len(seq)


def _ready_key(prefix: str) -> str:
    return f"{prefix}ready"


def _paired_rows(
    rows: Iterable[dict[str, Any]],
    *,
    candidate_prefix: str,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and row.get(_ready_key(candidate_prefix))
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_post_q6_formal_decision_value_p50")) is not None
        and _float_or_none(row.get(f"{candidate_prefix}q6_formal_decision_value_p50"))
        is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
        and _float_or_none(row.get("v3_truth_q6_formal_decision_value")) is not None
    )


def _candidate_formal_value(
    row: dict[str, Any],
    *,
    candidate_prefix: str,
    probability: str,
    apply_delta: bool,
) -> float | None:
    baseline_formal = _float_or_none(
        row.get(f"v3_post_formal_decision_value_{probability}")
    )
    if not apply_delta:
        return baseline_formal
    baseline_q6 = _float_or_none(
        row.get(f"v3_post_q6_formal_decision_value_{probability}")
    )
    candidate_q6 = _float_or_none(
        row.get(f"{candidate_prefix}q6_formal_decision_value_{probability}")
    )
    if baseline_formal is None or baseline_q6 is None or candidate_q6 is None:
        return baseline_formal
    return max(0.0, baseline_formal + (candidate_q6 - baseline_q6))


def _candidate_flags(
    row: dict[str, Any],
    *,
    min_windows: int,
    min_sessions: int,
    min_public_total_rate: float,
    min_q6_floor_rate: float,
) -> tuple[str, ...]:
    flags: list[str] = []
    if int(row["n"]) < int(min_windows):
        flags.append("few_windows")
    if int(row["sessions"]) < int(min_sessions):
        flags.append("few_sessions")
    formal_bias = row.get("formal_p50_bias")
    formal_mae = row.get("formal_p50_mae")
    below_rate = float(row.get("formal_p50_below_rate") or 0.0)
    systemic_under = (
        formal_bias is not None
        and formal_mae is not None
        and float(formal_mae) > 0.0
        and float(formal_bias) <= -_SYSTEMIC_UNDER_BIAS_RATIO * float(formal_mae)
    ) or below_rate >= _SYSTEMIC_UNDER_RATE
    if not systemic_under:
        flags.append("not_systemic_under")
    if float(row.get("public_total_rate") or 0.0) < float(min_public_total_rate):
        flags.append("little_public_total")
    if float(row.get("q6_floor_rate") or 0.0) < float(min_q6_floor_rate):
        flags.append("weak_q6_evidence")
    if float(row.get("candidate_q6_formal_prediction_delta_mean") or 0.0) < _MIN_Q6_FORMAL_UPSHIFT:
        flags.append("no_q6_formal_upshift")
    if float(row.get("formal_p50_over_rate") or 0.0) >= _MAX_CANDIDATE_OVER_RATE:
        flags.append("high_over_context")
    if float(row.get("candidate_formal_p50_over_rate") or 0.0) >= _MAX_CANDIDATE_OVER_RATE:
        flags.append("candidate_high_over")
    if row.get("delta_formal_p50_mae") is not None and float(row["delta_formal_p50_mae"]) > _MAX_FORMAL_MAE_HURT:
        flags.append("formal_delta_hurts")
    if row.get("delta_q6_formal_p50_mae") is not None and float(row["delta_q6_formal_p50_mae"]) > _MAX_Q6_FORMAL_MAE_HURT:
        flags.append("q6_formal_delta_hurts")
    if row.get("delta_formal_p90_coverage") is not None and float(row["delta_formal_p90_coverage"]) < -_MAX_P90_COVERAGE_DROP:
        flags.append("p90_coverage_drop")
    return tuple(flags)


def _candidate_status(flags: tuple[str, ...]) -> str:
    if "few_windows" in flags or "few_sessions" in flags:
        return "blocked_low_sample"
    if "not_systemic_under" in flags:
        return "blocked_not_systemic_under"
    if "little_public_total" in flags or "weak_q6_evidence" in flags:
        return "watch_only_needs_evidence"
    if "no_q6_formal_upshift" in flags:
        return "blocked_no_q6_formal_upshift"
    if any(
        flag in flags
        for flag in (
            "formal_delta_hurts",
            "q6_formal_delta_hurts",
            "p90_coverage_drop",
            "high_over_context",
            "candidate_high_over",
        )
    ):
        return "blocked_delta_hurts"
    return "watch_formal_value_candidate"


def _group_metrics(
    rows: Iterable[dict[str, Any]],
    *,
    group: str,
    group_field: str,
    candidate_prefix: str,
    min_windows: int,
    min_sessions: int,
    min_public_total_rate: float,
    min_q6_floor_rate: float,
) -> dict[str, Any]:
    group_rows = tuple(rows)
    sessions = {_session_id(row) for row in group_rows}
    formal = _pairs(
        group_rows,
        "v3_post_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
    )
    q6_formal = _pairs(
        group_rows,
        "v3_post_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
    )
    formal_p90 = _pairs(
        group_rows,
        "v3_post_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
    )
    candidate_formal = tuple(
        (pred, truth)
        for row in group_rows
        if (pred := _candidate_formal_value(
            row,
            candidate_prefix=candidate_prefix,
            probability="p50",
            apply_delta=True,
        ))
        is not None
        and (truth := _float_or_none(row.get("v3_truth_formal_decision_value")))
        is not None
    )
    candidate_formal_p90 = tuple(
        (pred, truth)
        for row in group_rows
        if (pred := _candidate_formal_value(
            row,
            candidate_prefix=candidate_prefix,
            probability="p90",
            apply_delta=True,
        ))
        is not None
        and (truth := _float_or_none(row.get("v3_truth_formal_decision_value")))
        is not None
    )
    candidate_q6_formal = _pairs(
        group_rows,
        f"{candidate_prefix}q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
    )
    formal_mae = _mae(formal)
    candidate_formal_mae = _mae(candidate_formal)
    q6_formal_mae = _mae(q6_formal)
    candidate_q6_formal_mae = _mae(candidate_q6_formal)
    formal_p90_coverage = _coverage_rate(formal_p90)
    candidate_p90_coverage = _coverage_rate(candidate_formal_p90)
    row = {
        "group_field": group_field,
        "group": group,
        "candidate_prefix": candidate_prefix,
        "n": len(group_rows),
        "sessions": len(sessions),
        "formal_p50_mae": _round_metric(formal_mae, 1),
        "candidate_formal_p50_mae": _round_metric(candidate_formal_mae, 1),
        "delta_formal_p50_mae": _round_metric(
            _delta(candidate_formal_mae, formal_mae),
            1,
        ),
        "formal_p50_bias": _round_metric(_bias(formal), 1),
        "candidate_formal_p50_bias": _round_metric(_bias(candidate_formal), 1),
        "formal_p50_below_rate": _round_metric(_below_rate(formal), 6),
        "candidate_formal_p50_below_rate": _round_metric(
            _below_rate(candidate_formal),
            6,
        ),
        "formal_p50_over_rate": _round_metric(_over_rate(formal), 6),
        "candidate_formal_p50_over_rate": _round_metric(
            _over_rate(candidate_formal),
            6,
        ),
        "formal_p90_coverage": _round_metric(formal_p90_coverage, 6),
        "candidate_formal_p90_coverage": _round_metric(candidate_p90_coverage, 6),
        "delta_formal_p90_coverage": _round_metric(
            _delta(candidate_p90_coverage, formal_p90_coverage),
            6,
        ),
        "formal_p90_pinball": _round_metric(_pinball(formal_p90, 0.9), 1),
        "candidate_formal_p90_pinball": _round_metric(
            _pinball(candidate_formal_p90, 0.9),
            1,
        ),
        "q6_formal_p50_mae": _round_metric(q6_formal_mae, 1),
        "candidate_q6_formal_p50_mae": _round_metric(candidate_q6_formal_mae, 1),
        "delta_q6_formal_p50_mae": _round_metric(
            _delta(candidate_q6_formal_mae, q6_formal_mae),
            1,
        ),
        "candidate_q6_formal_prediction_delta_mean": _round_metric(
            _mean(
                (candidate - baseline)
                for row in group_rows
                if (candidate := _float_or_none(
                    row.get(f"{candidate_prefix}q6_formal_decision_value_p50")
                ))
                is not None
                and (baseline := _float_or_none(
                    row.get("v3_post_q6_formal_decision_value_p50")
                ))
                is not None
            ),
            1,
        ),
        "public_total_rate": _round_metric(
            _rate(
                group_rows,
                lambda item: item.get("v3_summary_session_total_cells_exact") is not None
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
        min_public_total_rate=min_public_total_rate,
        min_q6_floor_rate=min_q6_floor_rate,
    )
    row["candidate_status"] = _candidate_status(flags)
    row["flags"] = list(flags)
    return row


def summarize_candidates(
    rows: Iterable[dict[str, Any]],
    *,
    candidate_prefix: str,
    group_field: str = DEFAULT_GROUP_FIELD,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_public_total_rate: float = 0.10,
    min_q6_floor_rate: float = 0.20,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _paired_rows(rows, candidate_prefix=candidate_prefix):
        groups[_group_value(row, group_field)].append(row)
    out = [
        _group_metrics(
            group_rows,
            group=group,
            group_field=group_field,
            candidate_prefix=candidate_prefix,
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_public_total_rate=min_public_total_rate,
            min_q6_floor_rate=min_q6_floor_rate,
        )
        for group, group_rows in sorted(groups.items())
    ]
    rank = {
        "watch_formal_value_candidate": 0,
        "watch_only_needs_evidence": 1,
        "blocked_delta_hurts": 2,
        "blocked_no_q6_formal_upshift": 3,
        "blocked_not_systemic_under": 4,
        "blocked_low_sample": 5,
    }
    return sorted(
        out,
        key=lambda row: (
            rank.get(str(row["candidate_status"]), 99),
            -(float(row.get("formal_p50_mae") or 0.0)),
            str(row["group"]),
        ),
    )


def _row_evals(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str,
    candidate_prefix: str,
    candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _paired_rows(rows, candidate_prefix=candidate_prefix):
        group = _group_value(row, group_field)
        applied = group in candidates
        out.append(
            {
                "group": group,
                "session_id": _session_id(row),
                "candidate_applied": applied,
                "baseline_formal_p50": _float_or_none(
                    row.get("v3_post_formal_decision_value_p50")
                ),
                "candidate_formal_p50": _candidate_formal_value(
                    row,
                    candidate_prefix=candidate_prefix,
                    probability="p50",
                    apply_delta=applied,
                ),
                "baseline_formal_p90": _float_or_none(
                    row.get("v3_post_formal_decision_value_p90")
                ),
                "candidate_formal_p90": _candidate_formal_value(
                    row,
                    candidate_prefix=candidate_prefix,
                    probability="p90",
                    apply_delta=applied,
                ),
                "truth_formal": _float_or_none(row.get("v3_truth_formal_decision_value")),
                "baseline_q6_formal_p50": _float_or_none(
                    row.get("v3_post_q6_formal_decision_value_p50")
                ),
                "candidate_q6_formal_p50": _float_or_none(
                    row.get(
                        f"{candidate_prefix}q6_formal_decision_value_p50"
                        if applied
                        else "v3_post_q6_formal_decision_value_p50"
                    )
                ),
                "truth_q6_formal": _float_or_none(
                    row.get("v3_truth_q6_formal_decision_value")
                ),
            }
        )
    return out


def _metrics(evals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = tuple(evals)
    candidate_rows = tuple(row for row in rows if row.get("candidate_applied"))
    sessions = {str(row["session_id"]) for row in rows}
    candidate_sessions = {str(row["session_id"]) for row in candidate_rows}
    candidate_groups = {str(row["group"]) for row in candidate_rows}
    base_formal = tuple(
        (float(row["baseline_formal_p50"]), float(row["truth_formal"]))
        for row in rows
        if row.get("baseline_formal_p50") is not None
        and row.get("truth_formal") is not None
    )
    cand_formal = tuple(
        (float(row["candidate_formal_p50"]), float(row["truth_formal"]))
        for row in rows
        if row.get("candidate_formal_p50") is not None
        and row.get("truth_formal") is not None
    )
    base_formal_p90 = tuple(
        (float(row["baseline_formal_p90"]), float(row["truth_formal"]))
        for row in rows
        if row.get("baseline_formal_p90") is not None
        and row.get("truth_formal") is not None
    )
    cand_formal_p90 = tuple(
        (float(row["candidate_formal_p90"]), float(row["truth_formal"]))
        for row in rows
        if row.get("candidate_formal_p90") is not None
        and row.get("truth_formal") is not None
    )
    base_q6_formal = tuple(
        (float(row["baseline_q6_formal_p50"]), float(row["truth_q6_formal"]))
        for row in rows
        if row.get("baseline_q6_formal_p50") is not None
        and row.get("truth_q6_formal") is not None
    )
    cand_q6_formal = tuple(
        (float(row["candidate_q6_formal_p50"]), float(row["truth_q6_formal"]))
        for row in rows
        if row.get("candidate_q6_formal_p50") is not None
        and row.get("truth_q6_formal") is not None
    )
    base_formal_mae = _mae(base_formal)
    cand_formal_mae = _mae(cand_formal)
    base_q6_mae = _mae(base_q6_formal)
    cand_q6_mae = _mae(cand_q6_formal)
    base_p90_cover = _coverage_rate(base_formal_p90)
    cand_p90_cover = _coverage_rate(cand_formal_p90)
    return {
        "n": len(rows),
        "sessions": len(sessions),
        "candidate_rows": len(candidate_rows),
        "candidate_sessions": len(candidate_sessions),
        "candidate_groups": sorted(candidate_groups),
        "baseline_formal_p50_mae": _round_metric(base_formal_mae, 1),
        "candidate_formal_p50_mae": _round_metric(cand_formal_mae, 1),
        "delta_formal_p50_mae": _round_metric(_delta(cand_formal_mae, base_formal_mae), 1),
        "baseline_formal_p50_bias": _round_metric(_bias(base_formal), 1),
        "candidate_formal_p50_bias": _round_metric(_bias(cand_formal), 1),
        "baseline_formal_p50_below_rate": _round_metric(_below_rate(base_formal), 6),
        "candidate_formal_p50_below_rate": _round_metric(_below_rate(cand_formal), 6),
        "baseline_formal_p50_over_rate": _round_metric(_over_rate(base_formal), 6),
        "candidate_formal_p50_over_rate": _round_metric(_over_rate(cand_formal), 6),
        "baseline_formal_p90_coverage": _round_metric(base_p90_cover, 6),
        "candidate_formal_p90_coverage": _round_metric(cand_p90_cover, 6),
        "delta_formal_p90_coverage": _round_metric(_delta(cand_p90_cover, base_p90_cover), 6),
        "baseline_formal_p90_pinball": _round_metric(_pinball(base_formal_p90, 0.9), 1),
        "candidate_formal_p90_pinball": _round_metric(_pinball(cand_formal_p90, 0.9), 1),
        "baseline_q6_formal_p50_mae": _round_metric(base_q6_mae, 1),
        "candidate_q6_formal_p50_mae": _round_metric(cand_q6_mae, 1),
        "delta_q6_formal_p50_mae": _round_metric(_delta(cand_q6_mae, base_q6_mae), 1),
    }


def _applied_hurts(
    group_results: Iterable[dict[str, Any]],
    *,
    max_below_rate_increase: float,
    max_over_rate_increase: float,
) -> list[str]:
    hurts: list[str] = []
    for row in group_results:
        below_delta = 0.0
        if (
            row.get("candidate_formal_p50_below_rate") is not None
            and row.get("baseline_formal_p50_below_rate") is not None
        ):
            below_delta = float(row["candidate_formal_p50_below_rate"]) - float(row["baseline_formal_p50_below_rate"])
        over_delta = 0.0
        if (
            row.get("candidate_formal_p50_over_rate") is not None
            and row.get("baseline_formal_p50_over_rate") is not None
        ):
            over_delta = float(row["candidate_formal_p50_over_rate"]) - float(row["baseline_formal_p50_over_rate"])
        if (
            (row.get("delta_formal_p50_mae") is not None and float(row["delta_formal_p50_mae"]) > _MAX_FORMAL_MAE_HURT)
            or (row.get("delta_q6_formal_p50_mae") is not None and float(row["delta_q6_formal_p50_mae"]) > _MAX_Q6_FORMAL_MAE_HURT)
            or (row.get("delta_formal_p90_coverage") is not None and float(row["delta_formal_p90_coverage"]) < -_MAX_P90_COVERAGE_DROP)
            or (row.get("candidate_formal_p50_over_rate") is not None and float(row["candidate_formal_p50_over_rate"]) >= _MAX_CANDIDATE_OVER_RATE)
            or below_delta > float(max_below_rate_increase)
            or over_delta > float(max_over_rate_increase)
        ):
            hurts.append(str(row["group"]))
    return hurts


def _overall_status(candidate: dict[str, Any], applied_hurts: list[str]) -> str:
    if int(candidate.get("candidate_rows") or 0) <= 0:
        return "sample_limited"
    if applied_hurts:
        return "blocked_holdout_hurt"
    if (
        candidate.get("delta_formal_p50_mae") is not None
        and float(candidate["delta_formal_p50_mae"]) <= 0.0
        and candidate.get("candidate_formal_p50_below_rate") is not None
        and candidate.get("baseline_formal_p50_below_rate") is not None
        and candidate.get("candidate_formal_p50_over_rate") is not None
        and float(candidate["candidate_formal_p50_over_rate"]) < _MAX_CANDIDATE_OVER_RATE
        and float(candidate["candidate_formal_p50_below_rate"])
        <= float(candidate["baseline_formal_p50_below_rate"]) + _MAX_BELOW_RATE_INCREASE
    ):
        return "watch"
    return "blocked_holdout_hurt"


def summarize_holdout(
    rows: Iterable[dict[str, Any]],
    *,
    candidate_prefix: str,
    group_field: str = DEFAULT_GROUP_FIELD,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_public_total_rate: float = 0.10,
    min_q6_floor_rate: float = 0.20,
    max_below_rate_increase: float = _MAX_BELOW_RATE_INCREASE,
    max_over_rate_increase: float = _MAX_OVER_RATE_INCREASE,
) -> dict[str, Any]:
    paired = tuple(_paired_rows(rows, candidate_prefix=candidate_prefix))
    fold_count = max(1, int(folds))
    all_evals: list[dict[str, Any]] = []
    folds_out: list[dict[str, Any]] = []
    status_counts = Counter()
    for fold in range(fold_count):
        train_rows = [
            row for row in paired if _stable_fold(_session_id(row), fold_count) != fold
        ]
        holdout_rows = [
            row for row in paired if _stable_fold(_session_id(row), fold_count) == fold
        ]
        train_candidates = summarize_candidates(
            train_rows,
            candidate_prefix=candidate_prefix,
            group_field=group_field,
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_public_total_rate=min_public_total_rate,
            min_q6_floor_rate=min_q6_floor_rate,
        )
        status_counts.update(str(row["candidate_status"]) for row in train_candidates)
        candidates = {
            str(row["group"]): row
            for row in train_candidates
            if row.get("candidate_status") == "watch_formal_value_candidate"
        }
        evals = _row_evals(
            holdout_rows,
            group_field=group_field,
            candidate_prefix=candidate_prefix,
            candidates=candidates,
        )
        all_evals.extend(evals)
        fold_metrics = _metrics(evals)
        fold_metrics.update(
            {
                "fold": fold,
                "train_rows": len(train_rows),
                "holdout_rows": len(holdout_rows),
                "train_candidate_groups": sorted(candidates),
            }
        )
        folds_out.append(fold_metrics)
    group_evals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_evals:
        if row.get("candidate_applied"):
            group_evals[str(row["group"])].append(row)
    group_results = [
        {"group": group, **_metrics(evals)}
        for group, evals in sorted(group_evals.items())
    ]
    group_results.sort(
        key=lambda row: (
            float(row.get("delta_formal_p50_mae") or 0.0),
            str(row["group"]),
        )
    )
    candidate = _metrics(row for row in all_evals if row.get("candidate_applied"))
    applied_hurts = _applied_hurts(
        group_results,
        max_below_rate_increase=max_below_rate_increase,
        max_over_rate_increase=max_over_rate_increase,
    )
    result = {
        "group_field": group_field,
        "candidate_prefix": candidate_prefix,
        "candidate_formula": (
            "formal + (candidate_q6_formal - baseline_q6_formal)"
        ),
        "folds": fold_count,
        "min_windows": int(min_windows),
        "min_sessions": int(min_sessions),
        "min_public_total_rate": _round_metric(min_public_total_rate, 6),
        "min_q6_floor_rate": _round_metric(min_q6_floor_rate, 6),
        "max_below_rate_increase": _round_metric(max_below_rate_increase, 6),
        "max_over_rate_increase": _round_metric(max_over_rate_increase, 6),
        "train_candidate_status_counts": dict(sorted(status_counts.items())),
        "overall": _metrics(all_evals),
        "candidate_only": candidate,
        "applied_hurts": applied_hurts,
        "fold_results": folds_out,
        "group_results": group_results,
    }
    result["overall_status"] = _overall_status(candidate, applied_hurts)
    return result


def _print_summary(result: dict[str, Any], *, top: int) -> None:
    candidate = result["candidate_only"]
    print(
        " ".join(
            (
                f"overall_status={result['overall_status']}",
                f"group_field={result['group_field']}",
                f"candidate_prefix={result['candidate_prefix']}",
                f"rows={result['overall']['n']}",
                f"candidate_rows={candidate['candidate_rows']}",
                "candidate_groups=" + ",".join(candidate["candidate_groups"]),
                f"formal_delta={candidate['delta_formal_p50_mae']}",
                f"q6_formal_delta={candidate['delta_q6_formal_p50_mae']}",
                f"below={candidate['baseline_formal_p50_below_rate']}",
                f"candidate_below={candidate['candidate_formal_p50_below_rate']}",
                f"over={candidate['baseline_formal_p50_over_rate']}",
                f"candidate_over={candidate['candidate_formal_p50_over_rate']}",
                f"p90_cover={candidate['baseline_formal_p90_coverage']}",
                f"candidate_p90_cover={candidate['candidate_formal_p90_coverage']}",
                "applied_hurts=" + ",".join(result["applied_hurts"]),
            )
        )
    )
    print(
        "train_status_counts="
        + ",".join(
            f"{status}:{count}"
            for status, count in result["train_candidate_status_counts"].items()
        )
    )
    for row in result["group_results"][:top]:
        print(
            " ".join(
                (
                    f"group={row['group']}",
                    f"rows={row['candidate_rows']}",
                    f"sessions={row['candidate_sessions']}",
                    f"formal_delta={row['delta_formal_p50_mae']}",
                    f"q6_formal_delta={row['delta_q6_formal_p50_mae']}",
                    f"below={row['baseline_formal_p50_below_rate']}",
                    f"candidate_below={row['candidate_formal_p50_below_rate']}",
                    f"over={row['baseline_formal_p50_over_rate']}",
                    f"candidate_over={row['candidate_formal_p50_over_rate']}",
                    f"p90_cover={row['baseline_formal_p90_coverage']}",
                    f"candidate_p90_cover={row['candidate_formal_p90_coverage']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-validate formal candidates from q6 formal deltas.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--candidate-prefix", default="v3_ccv_")
    parser.add_argument("--ccv-component-likelihood", action="store_true")
    parser.add_argument("--ccv-component-freeze-cells", action="store_true")
    parser.add_argument("--by", default=DEFAULT_GROUP_FIELD)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-public-total-rate", type=float, default=0.10)
    parser.add_argument("--min-q6-floor-rate", type=float, default=0.20)
    parser.add_argument("--max-below-rate-increase", type=float, default=_MAX_BELOW_RATE_INCREASE)
    parser.add_argument("--max-over-rate-increase", type=float, default=_MAX_OVER_RATE_INCREASE)
    parser.add_argument("--posterior-trials", type=int, default=512)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=load_monitor_tables(),
        calibration_entries=load_prior_calibration_entries(
            _default_calibration_path()
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
        ccv_options=V3CcvOptions(
            component_likelihood=(
                args.ccv_component_likelihood
                or args.candidate_prefix == "v3_ccvc_"
                or args.ccv_component_freeze_cells
            ),
            component_move_cells=not args.ccv_component_freeze_cells,
        ),
    )
    result = {
        "errors": errors,
        **summarize_holdout(
            rows,
            candidate_prefix=args.candidate_prefix,
            group_field=args.by,
            folds=args.folds,
            min_windows=args.min_windows,
            min_sessions=args.min_sessions,
            min_public_total_rate=args.min_public_total_rate,
            min_q6_floor_rate=args.min_q6_floor_rate,
            max_below_rate_increase=args.max_below_rate_increase,
            max_over_rate_increase=args.max_over_rate_increase,
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
