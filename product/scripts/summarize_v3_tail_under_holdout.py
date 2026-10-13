"""Cross-validate guarded v3 underestimate + tail review candidates.

This is a promotion-audit script only. It tests whether bounded formal upshift
and tail/value review can coexist under session holdout without changing live
or formal bid decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
from summarize_v3_tail_value_candidates import (  # noqa: E402
    summarize_candidates as summarize_tail_candidates,
)
from summarize_v3_underestimate_repair_candidates import (  # noqa: E402
    DEFAULT_GROUP_FIELD,
    summarize_candidates as summarize_under_candidates,
)


_UNDER_APPLIED_STATUSES = frozenset(("watch_only_upshift_candidate",))
_TAIL_APPLIED_STATUSES = frozenset(
    (
        "watch_only_q6_tail_value_candidate",
        "watch_only_tail_value_candidate",
    )
)
_TAIL_HURT_STATUSES = frozenset(("blocked_tail_estimate_hurts",))


def _stable_fold(value: Any, folds: int) -> int:
    if folds <= 1:
        return 0
    text = str(value or "unknown")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % int(folds)


def _session_id(row: dict[str, Any]) -> str:
    value = row.get("session_id")
    if value not in (None, ""):
        return str(value)
    return str(row.get("file") or "unknown")


def _group_value(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    return str(value) if value not in (None, "") else "unknown"


def _normal_error_denominator(truth: float) -> float:
    return max(100_000.0, abs(truth))


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


def _pinball_loss(truth: float, prediction: float, quantile: float) -> float:
    delta = truth - prediction
    if delta >= 0:
        return quantile * delta
    return (1.0 - quantile) * (-delta)


def _paired_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_post_formal_decision_value_p90")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    )


def _candidate_scale(candidate: dict[str, Any] | None) -> float:
    if candidate is None:
        return 1.0
    scale = _float_or_none(candidate.get("proposed_scale"))
    return max(1.0, float(scale or 1.0))


def _scaled_value(value: Any, scale: float) -> float | None:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return parsed * scale


def _candidate_value(
    row: dict[str, Any],
    *,
    candidate_applied: bool,
    baseline_key: str,
    candidate_key: str,
) -> float | None:
    baseline = _float_or_none(row.get(baseline_key))
    if not candidate_applied:
        return baseline
    candidate = _float_or_none(row.get(candidate_key))
    return candidate if candidate is not None else baseline


def _row_evals(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str,
    under_candidates: dict[str, dict[str, Any]],
    tail_candidates: dict[str, dict[str, Any]],
    tail_hurt_guards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        group = _group_value(row, group_field)
        under_candidate = under_candidates.get(group)
        tail_candidate = tail_candidates.get(group)
        tail_hurt = tail_hurt_guards.get(group)
        under_applied = under_candidate is not None
        tail_applied = tail_candidate is not None and tail_hurt is None
        scale = _candidate_scale(under_candidate)
        out.append(
            {
                "group": group,
                "session_id": _session_id(row),
                "under_candidate_applied": under_applied,
                "tail_candidate_applied": tail_applied,
                "tail_hurt_guard": tail_hurt is not None,
                "under_status": (
                    under_candidate.get("candidate_status")
                    if under_candidate is not None
                    else None
                ),
                "tail_status": (
                    tail_candidate.get("candidate_status")
                    if tail_candidate is not None
                    else (
                        tail_hurt.get("candidate_status")
                        if tail_hurt is not None
                        else None
                    )
                ),
                "under_scale": _round_metric(scale, 6),
                "baseline_formal_p50": _float_or_none(
                    row.get("v3_post_formal_decision_value_p50")
                ),
                "candidate_formal_p50": _scaled_value(
                    row.get("v3_post_formal_decision_value_p50"),
                    scale,
                ),
                "baseline_formal_p90": _float_or_none(
                    row.get("v3_post_formal_decision_value_p90")
                ),
                "candidate_formal_p90": _scaled_value(
                    row.get("v3_post_formal_decision_value_p90"),
                    scale,
                ),
                "truth_formal": _float_or_none(
                    row.get("v3_truth_formal_decision_value")
                ),
                "baseline_q6_formal_p50": _float_or_none(
                    row.get("v3_post_q6_formal_decision_value_p50")
                ),
                "candidate_q6_formal_p50": _scaled_value(
                    row.get("v3_post_q6_formal_decision_value_p50"),
                    scale,
                ),
                "baseline_q6_formal_p90": _float_or_none(
                    row.get("v3_post_q6_formal_decision_value_p90")
                ),
                "candidate_q6_formal_p90": _scaled_value(
                    row.get("v3_post_q6_formal_decision_value_p90"),
                    scale,
                ),
                "truth_q6_formal": _float_or_none(
                    row.get("v3_truth_q6_formal_decision_value")
                ),
                "baseline_tail_p50": _float_or_none(
                    row.get("v3_post_formal_decision_value_p50")
                ),
                "candidate_tail_p50": _candidate_value(
                    row,
                    candidate_applied=tail_applied,
                    baseline_key="v3_post_formal_decision_value_p50",
                    candidate_key="v3_post_tail_replacement_decision_value_p50",
                ),
                "baseline_tail_p90": _float_or_none(
                    row.get("v3_post_formal_decision_value_p90")
                ),
                "candidate_tail_p90": _candidate_value(
                    row,
                    candidate_applied=tail_applied,
                    baseline_key="v3_post_formal_decision_value_p90",
                    candidate_key="v3_post_tail_replacement_decision_value_p90",
                ),
                "truth_tail": _float_or_none(
                    row.get("v3_truth_tail_replacement_decision_value")
                ),
                "baseline_q6_tail_p50": _float_or_none(
                    row.get("v3_post_q6_formal_decision_value_p50")
                ),
                "candidate_q6_tail_p50": _candidate_value(
                    row,
                    candidate_applied=tail_applied,
                    baseline_key="v3_post_q6_formal_decision_value_p50",
                    candidate_key="v3_post_q6_tail_replacement_decision_value_p50",
                ),
                "baseline_q6_tail_p90": _float_or_none(
                    row.get("v3_post_q6_formal_decision_value_p90")
                ),
                "candidate_q6_tail_p90": _candidate_value(
                    row,
                    candidate_applied=tail_applied,
                    baseline_key="v3_post_q6_formal_decision_value_p90",
                    candidate_key="v3_post_q6_tail_replacement_decision_value_p90",
                ),
                "truth_q6_tail": _float_or_none(
                    row.get("v3_truth_q6_tail_replacement_decision_value")
                ),
            }
        )
    return out


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


def _median_abs_error(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _median(abs(pred - truth) for pred, truth in pairs)


def _median_normalized_abs_error(
    pairs: Iterable[tuple[float, float]],
) -> float | None:
    return _median(
        abs(pred - truth) / _normal_error_denominator(truth)
        for pred, truth in pairs
    )


def _bias(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(pred - truth for pred, truth in pairs)


def _below_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred < truth else 0.0 for pred, truth in pairs)


def _over_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred > truth else 0.0 for pred, truth in pairs)


def _coverage_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)


def _extreme_over_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(
        1.0 if pred - truth > _normal_error_denominator(truth) else 0.0
        for pred, truth in pairs
    )


def _pinball(
    pairs: Iterable[tuple[float, float]],
    quantile: float,
) -> float | None:
    return _mean(_pinball_loss(truth, pred, quantile) for pred, truth in pairs)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _add_component_metrics(
    out: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    *,
    component: str,
    truth_key: str,
    digits: int,
) -> None:
    base_p50 = _pairs(rows, f"baseline_{component}_p50", truth_key)
    cand_p50 = _pairs(rows, f"candidate_{component}_p50", truth_key)
    base_p90 = _pairs(rows, f"baseline_{component}_p90", truth_key)
    cand_p90 = _pairs(rows, f"candidate_{component}_p90", truth_key)

    base_mae = _mae(base_p50)
    cand_mae = _mae(cand_p50)
    base_below = _below_rate(base_p50)
    cand_below = _below_rate(cand_p50)
    base_cover = _coverage_rate(base_p90)
    cand_cover = _coverage_rate(cand_p90)
    base_extreme = _extreme_over_rate(base_p90)
    cand_extreme = _extreme_over_rate(cand_p90)
    base_p50_pinball = _pinball(base_p50, 0.5)
    cand_p50_pinball = _pinball(cand_p50, 0.5)
    base_p90_pinball = _pinball(base_p90, 0.9)
    cand_p90_pinball = _pinball(cand_p90, 0.9)

    out[f"baseline_{component}_p50_mae"] = _round_metric(base_mae, digits)
    out[f"candidate_{component}_p50_mae"] = _round_metric(cand_mae, digits)
    out[f"delta_{component}_p50_mae"] = _round_metric(
        _delta(cand_mae, base_mae),
        digits,
    )
    out[f"baseline_{component}_p50_median_abs_error"] = _round_metric(
        _median_abs_error(base_p50),
        digits,
    )
    out[f"candidate_{component}_p50_median_abs_error"] = _round_metric(
        _median_abs_error(cand_p50),
        digits,
    )
    out[f"baseline_{component}_p50_median_norm_abs_error"] = _round_metric(
        _median_normalized_abs_error(base_p50),
        6,
    )
    out[f"candidate_{component}_p50_median_norm_abs_error"] = _round_metric(
        _median_normalized_abs_error(cand_p50),
        6,
    )
    out[f"baseline_{component}_p50_bias"] = _round_metric(_bias(base_p50), digits)
    out[f"candidate_{component}_p50_bias"] = _round_metric(_bias(cand_p50), digits)
    out[f"baseline_{component}_p50_below_rate"] = _round_metric(base_below, 6)
    out[f"candidate_{component}_p50_below_rate"] = _round_metric(cand_below, 6)
    out[f"delta_{component}_p50_below_rate"] = _round_metric(
        _delta(cand_below, base_below),
        6,
    )
    out[f"baseline_{component}_p50_over_rate"] = _round_metric(_over_rate(base_p50), 6)
    out[f"candidate_{component}_p50_over_rate"] = _round_metric(_over_rate(cand_p50), 6)
    out[f"baseline_{component}_p50_pinball"] = _round_metric(
        base_p50_pinball,
        digits,
    )
    out[f"candidate_{component}_p50_pinball"] = _round_metric(
        cand_p50_pinball,
        digits,
    )
    out[f"delta_{component}_p50_pinball"] = _round_metric(
        _delta(cand_p50_pinball, base_p50_pinball),
        digits,
    )
    out[f"baseline_{component}_p90_coverage"] = _round_metric(base_cover, 6)
    out[f"candidate_{component}_p90_coverage"] = _round_metric(cand_cover, 6)
    out[f"delta_{component}_p90_coverage"] = _round_metric(
        _delta(cand_cover, base_cover),
        6,
    )
    out[f"baseline_{component}_p90_miss_rate"] = _round_metric(
        1.0 - base_cover if base_cover is not None else None,
        6,
    )
    out[f"candidate_{component}_p90_miss_rate"] = _round_metric(
        1.0 - cand_cover if cand_cover is not None else None,
        6,
    )
    out[f"baseline_{component}_p90_extreme_over_rate"] = _round_metric(
        base_extreme,
        6,
    )
    out[f"candidate_{component}_p90_extreme_over_rate"] = _round_metric(
        cand_extreme,
        6,
    )
    out[f"delta_{component}_p90_extreme_over_rate"] = _round_metric(
        _delta(cand_extreme, base_extreme),
        6,
    )
    out[f"baseline_{component}_p90_pinball"] = _round_metric(
        base_p90_pinball,
        digits,
    )
    out[f"candidate_{component}_p90_pinball"] = _round_metric(
        cand_p90_pinball,
        digits,
    )
    out[f"delta_{component}_p90_pinball"] = _round_metric(
        _delta(cand_p90_pinball, base_p90_pinball),
        digits,
    )


def _metrics(evals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = tuple(evals)
    sessions = {str(row["session_id"]) for row in rows}
    under_sessions = {
        str(row["session_id"]) for row in rows if row.get("under_candidate_applied")
    }
    tail_sessions = {
        str(row["session_id"]) for row in rows if row.get("tail_candidate_applied")
    }
    hurt_sessions = {
        str(row["session_id"]) for row in rows if row.get("tail_hurt_guard")
    }
    out: dict[str, Any] = {
        "n": len(rows),
        "sessions": len(sessions),
        "under_candidate_rows": sum(
            1 for row in rows if row.get("under_candidate_applied")
        ),
        "under_candidate_sessions": len(under_sessions),
        "under_candidate_groups": sorted(
            {
                str(row["group"])
                for row in rows
                if row.get("under_candidate_applied")
            }
        ),
        "tail_candidate_rows": sum(
            1 for row in rows if row.get("tail_candidate_applied")
        ),
        "tail_candidate_sessions": len(tail_sessions),
        "tail_candidate_groups": sorted(
            {
                str(row["group"])
                for row in rows
                if row.get("tail_candidate_applied")
            }
        ),
        "tail_hurt_guard_rows": sum(1 for row in rows if row.get("tail_hurt_guard")),
        "tail_hurt_guard_sessions": len(hurt_sessions),
        "tail_hurt_guard_groups": sorted(
            {str(row["group"]) for row in rows if row.get("tail_hurt_guard")}
        ),
        "any_candidate_rows": sum(
            1
            for row in rows
            if row.get("under_candidate_applied") or row.get("tail_candidate_applied")
        ),
    }
    _add_component_metrics(
        out,
        rows,
        component="formal",
        truth_key="truth_formal",
        digits=3,
    )
    _add_component_metrics(
        out,
        rows,
        component="q6_formal",
        truth_key="truth_q6_formal",
        digits=3,
    )
    _add_component_metrics(
        out,
        rows,
        component="tail",
        truth_key="truth_tail",
        digits=1,
    )
    _add_component_metrics(
        out,
        rows,
        component="q6_tail",
        truth_key="truth_q6_tail",
        digits=1,
    )
    return out


def summarize_holdout(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    max_upshift: float = 1.25,
) -> dict[str, Any]:
    fold_count = max(1, int(folds))
    paired = tuple(_paired_rows(rows))
    all_evals: list[dict[str, Any]] = []
    folds_out: list[dict[str, Any]] = []
    for fold in range(fold_count):
        train_rows = [
            row
            for row in paired
            if _stable_fold(_session_id(row), fold_count) != fold
        ]
        holdout_rows = [
            row
            for row in paired
            if _stable_fold(_session_id(row), fold_count) == fold
        ]
        under = summarize_under_candidates(
            train_rows,
            group_field=group_field,
            min_windows=min_windows,
            min_sessions=min_sessions,
            max_upshift=max_upshift,
        )
        tail = summarize_tail_candidates(
            train_rows,
            group_field=group_field,
            min_windows=min_windows,
            min_sessions=min_sessions,
        )
        under_by_group = {
            str(row["group"]): row
            for row in under
            if row.get("candidate_status") in _UNDER_APPLIED_STATUSES
        }
        tail_by_group = {
            str(row["group"]): row
            for row in tail
            if row.get("candidate_status") in _TAIL_APPLIED_STATUSES
        }
        hurt_by_group = {
            str(row["group"]): row
            for row in tail
            if row.get("candidate_status") in _TAIL_HURT_STATUSES
        }
        evals = _row_evals(
            holdout_rows,
            group_field=group_field,
            under_candidates=under_by_group,
            tail_candidates=tail_by_group,
            tail_hurt_guards=hurt_by_group,
        )
        all_evals.extend(evals)
        fold_metrics = _metrics(evals)
        fold_metrics.update(
            {
                "fold": fold,
                "train_rows": len(train_rows),
                "holdout_rows": len(holdout_rows),
                "train_under_status_counts": dict(
                    sorted(Counter(str(row["candidate_status"]) for row in under).items())
                ),
                "train_tail_status_counts": dict(
                    sorted(Counter(str(row["candidate_status"]) for row in tail).items())
                ),
                "train_under_candidate_groups": sorted(under_by_group),
                "train_tail_candidate_groups": sorted(tail_by_group),
                "train_tail_hurt_guard_groups": sorted(hurt_by_group),
            }
        )
        folds_out.append(fold_metrics)

    group_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_evals:
        group_rows[str(row["group"])].append(row)
    groups = [
        {"group": group, **_metrics(group_evals)}
        for group, group_evals in sorted(group_rows.items())
        if any(
            row.get("under_candidate_applied")
            or row.get("tail_candidate_applied")
            or row.get("tail_hurt_guard")
            for row in group_evals
        )
    ]
    groups = sorted(
        groups,
        key=lambda row: (
            row["delta_formal_p50_mae"]
            if row["delta_formal_p50_mae"] is not None
            else 0.0,
            row["delta_q6_tail_p50_mae"]
            if row["delta_q6_tail_p50_mae"] is not None
            else 0.0,
            str(row["group"]),
        ),
    )

    under_status_by_fold = Counter()
    tail_status_by_fold = Counter()
    for row in folds_out:
        under_status_by_fold.update(row["train_under_status_counts"])
        tail_status_by_fold.update(row["train_tail_status_counts"])
    return {
        "group_field": group_field,
        "folds": fold_count,
        "min_windows": int(min_windows),
        "min_sessions": int(min_sessions),
        "max_upshift": float(max_upshift),
        "under_status_counts_across_folds": dict(sorted(under_status_by_fold.items())),
        "tail_status_counts_across_folds": dict(sorted(tail_status_by_fold.items())),
        "overall": _metrics(all_evals),
        "candidate_only": _metrics(
            row
            for row in all_evals
            if row.get("under_candidate_applied") or row.get("tail_candidate_applied")
        ),
        "tail_hurt_guard_only": _metrics(
            row for row in all_evals if row.get("tail_hurt_guard")
        ),
        "fold_results": folds_out,
        "group_results": groups,
    }


def _print_summary(result: dict[str, Any], *, top: int) -> None:
    overall = result["overall"]
    candidate_only = result["candidate_only"]
    print(
        " ".join(
            (
                f"group_field={result['group_field']}",
                f"folds={result['folds']}",
                f"min_windows={result['min_windows']}",
                f"min_sessions={result['min_sessions']}",
                f"rows={overall['n']}",
                f"sessions={overall['sessions']}",
                f"under_rows={overall['under_candidate_rows']}",
                f"tail_rows={overall['tail_candidate_rows']}",
                f"hurt_rows={overall['tail_hurt_guard_rows']}",
                f"formal_mae={overall['baseline_formal_p50_mae']}",
                f"guarded_formal_mae={overall['candidate_formal_p50_mae']}",
                f"formal_delta={overall['delta_formal_p50_mae']}",
                f"below={overall['baseline_formal_p50_below_rate']}",
                f"guarded_below={overall['candidate_formal_p50_below_rate']}",
                f"p90_cover={overall['baseline_formal_p90_coverage']}",
                f"guarded_p90_cover={overall['candidate_formal_p90_coverage']}",
                f"p90_extreme={overall['baseline_formal_p90_extreme_over_rate']}",
                f"guarded_p90_extreme={overall['candidate_formal_p90_extreme_over_rate']}",
                f"q6_miss={overall['baseline_q6_formal_p90_miss_rate']}",
                f"guarded_q6_miss={overall['candidate_q6_formal_p90_miss_rate']}",
            )
        )
    )
    print(
        " ".join(
            (
                "candidate_only",
                f"rows={candidate_only['n']}",
                f"under_groups={','.join(candidate_only['under_candidate_groups']) or '-'}",
                f"tail_groups={','.join(candidate_only['tail_candidate_groups']) or '-'}",
                f"formal_delta={candidate_only['delta_formal_p50_mae']}",
                f"below={candidate_only['baseline_formal_p50_below_rate']}",
                f"guarded_below={candidate_only['candidate_formal_p50_below_rate']}",
                f"p90_cover={candidate_only['baseline_formal_p90_coverage']}",
                f"guarded_p90_cover={candidate_only['candidate_formal_p90_coverage']}",
                f"p90_extreme={candidate_only['baseline_formal_p90_extreme_over_rate']}",
                f"guarded_p90_extreme={candidate_only['candidate_formal_p90_extreme_over_rate']}",
                f"q6_tail_delta={candidate_only['delta_q6_tail_p50_mae']}",
            )
        )
    )
    print(
        "under_status_counts="
        + ",".join(
            f"{status}:{count}"
            for status, count in result["under_status_counts_across_folds"].items()
        )
    )
    print(
        "tail_status_counts="
        + ",".join(
            f"{status}:{count}"
            for status, count in result["tail_status_counts_across_folds"].items()
        )
    )
    for row in result["group_results"][:top]:
        print(
            " ".join(
                (
                    f"group={row['group']}",
                    f"rows={row['n']}",
                    f"sessions={row['sessions']}",
                    f"under_rows={row['under_candidate_rows']}",
                    f"tail_rows={row['tail_candidate_rows']}",
                    f"hurt_rows={row['tail_hurt_guard_rows']}",
                    f"formal_delta={row['delta_formal_p50_mae']}",
                    f"below={row['baseline_formal_p50_below_rate']}",
                    f"guarded_below={row['candidate_formal_p50_below_rate']}",
                    f"p90_cover={row['baseline_formal_p90_coverage']}",
                    f"guarded_p90_cover={row['candidate_formal_p90_coverage']}",
                    f"p90_extreme={row['candidate_formal_p90_extreme_over_rate']}",
                    f"q6_delta={row['delta_q6_formal_p50_mae']}",
                    f"tail_delta={row['delta_tail_p50_mae']}",
                    f"q6_tail_delta={row['delta_q6_tail_p50_mae']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-validate guarded v3 underestimate + tail review candidates.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--by", default=DEFAULT_GROUP_FIELD)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--max-upshift", type=float, default=1.25)
    parser.add_argument("--top", type=int, default=12)
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
    result = {
        "errors": errors,
        **summarize_holdout(
            rows,
            group_field=args.by,
            folds=args.folds,
            min_windows=args.min_windows,
            min_sessions=args.min_sessions,
            max_upshift=args.max_upshift,
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
