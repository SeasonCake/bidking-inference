"""Cross-validate v3 formal/value sampler candidates by session holdout."""

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
    _default_calibration_path,
    _default_paths,
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)

DEFAULT_GROUP_FIELD = "hero_map_id"
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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _pairs(
    rows: Iterable[dict[str, Any]],
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


def _is_value_floor_candidate(row: dict[str, Any]) -> bool:
    stress = str(row.get("v3_fv_stress_class") or "")
    return (
        _bool(row.get("v3_fv_candidate"))
        and "value_floor_stress" in stress
        and "capacity_cells_drift" not in stress
        and "q6_cells_floor_stress" not in stress
    )


def _is_mixed_value_floor_watch(row: dict[str, Any]) -> bool:
    stress = str(row.get("v3_fv_stress_class") or "")
    return (
        "value_floor_stress" in stress
        and (
            "capacity_cells_drift" in stress
            or "q6_cells_floor_stress" in stress
            or str(row.get("v3_fv_status") or "") == "watch_mixed_value_floor_guarded"
        )
    )


def _is_capacity_watch(row: dict[str, Any]) -> bool:
    stress = str(row.get("v3_fv_stress_class") or "")
    return (
        not _bool(row.get("v3_fv_candidate"))
        and (
            "capacity_cells_drift" in stress
            or "q6_cells_floor_stress" in stress
        )
    )


def _paired_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and row.get("v3_fv_ready")
        and not _bool(row.get("v3_fv_active"))
        and not _bool(row.get("v3_fv_affects_bid"))
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_fv_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
        and _float_or_none(row.get("v3_post_q6_formal_decision_value_p50"))
        is not None
        and _float_or_none(row.get("v3_fv_q6_formal_decision_value_p50"))
        is not None
        and _float_or_none(row.get("v3_truth_q6_formal_decision_value"))
        is not None
    )


def _candidate_applies(row: dict[str, Any], *, group_selected: bool) -> bool:
    if "candidate_applied" in row:
        return _bool(row.get("candidate_applied"))
    return group_selected and _is_value_floor_candidate(row)


def _row_candidate_value(
    row: dict[str, Any],
    *,
    group_selected: bool,
    baseline_key: str,
    candidate_key: str,
) -> float | None:
    baseline = _float_or_none(row.get(baseline_key))
    if not _candidate_applies(row, group_selected=group_selected):
        return baseline
    candidate = _float_or_none(row.get(candidate_key))
    return candidate if candidate is not None else baseline


def _candidate_flags(
    row: dict[str, Any],
    *,
    min_windows: int,
    min_sessions: int,
) -> tuple[str, ...]:
    flags: list[str] = []
    if int(row["candidate_rows"]) <= 0:
        flags.append("no_value_floor_candidate")
    if int(row["candidate_rows"]) < int(min_windows):
        flags.append("few_candidate_windows")
    if int(row["candidate_sessions"]) < int(min_sessions):
        flags.append("few_candidate_sessions")
    if (
        row.get("delta_formal_p50_mae") is not None
        and float(row["delta_formal_p50_mae"]) > _MAX_FORMAL_MAE_HURT
    ):
        flags.append("formal_sampler_hurts")
    if (
        row.get("delta_q6_formal_p50_mae") is not None
        and float(row["delta_q6_formal_p50_mae"]) > _MAX_Q6_FORMAL_MAE_HURT
    ):
        flags.append("q6_formal_sampler_hurts")
    if (
        row.get("delta_formal_p90_coverage") is not None
        and float(row["delta_formal_p90_coverage"]) < -_MAX_P90_COVERAGE_DROP
    ):
        flags.append("p90_coverage_drop")
    if (
        row.get("candidate_formal_p50_over_rate") is not None
        and float(row["candidate_formal_p50_over_rate"]) >= _MAX_CANDIDATE_OVER_RATE
    ):
        flags.append("candidate_high_over")
    below_delta = _delta(
        row.get("candidate_formal_p50_below_rate"),
        row.get("baseline_formal_p50_below_rate"),
    )
    if below_delta is not None and float(below_delta) > _MAX_BELOW_RATE_INCREASE:
        flags.append("below_rate_increase")
    over_delta = _delta(
        row.get("candidate_formal_p50_over_rate"),
        row.get("baseline_formal_p50_over_rate"),
    )
    if over_delta is not None and float(over_delta) > _MAX_OVER_RATE_INCREASE:
        flags.append("over_rate_increase")
    return tuple(flags)


def _candidate_status(flags: tuple[str, ...]) -> str:
    if "few_candidate_windows" in flags or "few_candidate_sessions" in flags:
        return "blocked_low_sample"
    if any(
        flag in flags
        for flag in (
            "formal_sampler_hurts",
            "q6_formal_sampler_hurts",
            "p90_coverage_drop",
            "candidate_high_over",
            "below_rate_increase",
            "over_rate_increase",
        )
    ):
        return "blocked_holdout_hurt"
    if "no_value_floor_candidate" in flags:
        return "sample_limited"
    return "watch_formal_value_sampler_candidate"


def _metrics(
    rows: Iterable[dict[str, Any]],
    *,
    group_selected: bool = True,
) -> dict[str, Any]:
    seq = tuple(rows)
    candidate_rows = tuple(
        row for row in seq if _candidate_applies(row, group_selected=group_selected)
    )
    sessions = {_session_id(row) for row in seq}
    candidate_sessions = {_session_id(row) for row in candidate_rows}
    baseline_formal = _pairs(
        seq,
        "v3_post_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
    )
    baseline_formal_p90 = _pairs(
        seq,
        "v3_post_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
    )
    baseline_q6_formal = _pairs(
        seq,
        "v3_post_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
    )
    candidate_formal = tuple(
        (pred, truth)
        for row in seq
        if (
            pred := _row_candidate_value(
                row,
                group_selected=group_selected,
                baseline_key="v3_post_formal_decision_value_p50",
                candidate_key="v3_fv_formal_decision_value_p50",
            )
        )
        is not None
        and (truth := _float_or_none(row.get("v3_truth_formal_decision_value")))
        is not None
    )
    candidate_formal_p90 = tuple(
        (pred, truth)
        for row in seq
        if (
            pred := _row_candidate_value(
                row,
                group_selected=group_selected,
                baseline_key="v3_post_formal_decision_value_p90",
                candidate_key="v3_fv_formal_decision_value_p90",
            )
        )
        is not None
        and (truth := _float_or_none(row.get("v3_truth_formal_decision_value")))
        is not None
    )
    candidate_q6_formal = tuple(
        (pred, truth)
        for row in seq
        if (
            pred := _row_candidate_value(
                row,
                group_selected=group_selected,
                baseline_key="v3_post_q6_formal_decision_value_p50",
                candidate_key="v3_fv_q6_formal_decision_value_p50",
            )
        )
        is not None
        and (truth := _float_or_none(row.get("v3_truth_q6_formal_decision_value")))
        is not None
    )
    base_mae = _mae(baseline_formal)
    cand_mae = _mae(candidate_formal)
    base_q6_mae = _mae(baseline_q6_formal)
    cand_q6_mae = _mae(candidate_q6_formal)
    base_p90_cover = _coverage_rate(baseline_formal_p90)
    cand_p90_cover = _coverage_rate(candidate_formal_p90)
    return {
        "n": len(seq),
        "sessions": len(sessions),
        "candidate_rows": len(candidate_rows),
        "candidate_sessions": len(candidate_sessions),
        "capacity_watch_rows": sum(1 for row in seq if _is_capacity_watch(row)),
        "mixed_value_floor_watch_rows": sum(
            1 for row in seq if _is_mixed_value_floor_watch(row)
        ),
        "baseline_formal_p50_mae": _round_metric(base_mae, 1),
        "candidate_formal_p50_mae": _round_metric(cand_mae, 1),
        "delta_formal_p50_mae": _round_metric(_delta(cand_mae, base_mae), 1),
        "baseline_formal_p50_bias": _round_metric(_bias(baseline_formal), 1),
        "candidate_formal_p50_bias": _round_metric(_bias(candidate_formal), 1),
        "baseline_formal_p50_below_rate": _round_metric(
            _below_rate(baseline_formal),
            6,
        ),
        "candidate_formal_p50_below_rate": _round_metric(
            _below_rate(candidate_formal),
            6,
        ),
        "baseline_formal_p50_over_rate": _round_metric(
            _over_rate(baseline_formal),
            6,
        ),
        "candidate_formal_p50_over_rate": _round_metric(
            _over_rate(candidate_formal),
            6,
        ),
        "baseline_formal_p90_coverage": _round_metric(base_p90_cover, 6),
        "candidate_formal_p90_coverage": _round_metric(cand_p90_cover, 6),
        "delta_formal_p90_coverage": _round_metric(
            _delta(cand_p90_cover, base_p90_cover),
            6,
        ),
        "baseline_formal_p90_pinball": _round_metric(
            _pinball(baseline_formal_p90, 0.9),
            1,
        ),
        "candidate_formal_p90_pinball": _round_metric(
            _pinball(candidate_formal_p90, 0.9),
            1,
        ),
        "baseline_q6_formal_p50_mae": _round_metric(base_q6_mae, 1),
        "candidate_q6_formal_p50_mae": _round_metric(cand_q6_mae, 1),
        "delta_q6_formal_p50_mae": _round_metric(
            _delta(cand_q6_mae, base_q6_mae),
            1,
        ),
    }


def summarize_candidates(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    min_windows: int = 20,
    min_sessions: int = 8,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _paired_rows(rows):
        groups[_group_value(row, group_field)].append(row)
    out: list[dict[str, Any]] = []
    for group, group_rows in sorted(groups.items()):
        row = {
            "group_field": group_field,
            "group": group,
            **_metrics(group_rows, group_selected=True),
        }
        flags = _candidate_flags(
            row,
            min_windows=min_windows,
            min_sessions=min_sessions,
        )
        row["candidate_status"] = _candidate_status(flags)
        row["flags"] = list(flags)
        out.append(row)
    rank = {
        "watch_formal_value_sampler_candidate": 0,
        "blocked_holdout_hurt": 1,
        "blocked_low_sample": 2,
        "sample_limited": 3,
    }
    return sorted(
        out,
        key=lambda row: (
            rank.get(str(row["candidate_status"]), 99),
            -(int(row.get("candidate_rows") or 0)),
            str(row["group"]),
        ),
    )


def _row_evals(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str,
    selected_groups: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _paired_rows(rows):
        group = _group_value(row, group_field)
        selected = group in selected_groups
        applied = selected and _is_value_floor_candidate(row)
        out.append(
            {
                **row,
                "group": group,
                "session_id": _session_id(row),
                "candidate_applied": applied,
            }
        )
    return out


def _applied_hurts(
    group_results: Iterable[dict[str, Any]],
) -> list[str]:
    hurts: list[str] = []
    for row in group_results:
        flags = _candidate_flags(row, min_windows=1, min_sessions=1)
        if any(
            flag in flags
            for flag in (
                "formal_sampler_hurts",
                "q6_formal_sampler_hurts",
                "p90_coverage_drop",
                "candidate_high_over",
                "below_rate_increase",
                "over_rate_increase",
            )
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
        and candidate.get("delta_q6_formal_p50_mae") is not None
        and float(candidate["delta_q6_formal_p50_mae"]) <= 0.0
        and candidate.get("candidate_formal_p50_over_rate") is not None
        and float(candidate["candidate_formal_p50_over_rate"]) < _MAX_CANDIDATE_OVER_RATE
    ):
        return "watch"
    return "blocked_holdout_hurt"


def summarize_holdout(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
) -> dict[str, Any]:
    paired = _paired_rows(rows)
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
            group_field=group_field,
            min_windows=min_windows,
            min_sessions=min_sessions,
        )
        status_counts.update(str(row["candidate_status"]) for row in train_candidates)
        selected_groups = {
            str(row["group"])
            for row in train_candidates
            if row.get("candidate_status") == "watch_formal_value_sampler_candidate"
        }
        evals = _row_evals(
            holdout_rows,
            group_field=group_field,
            selected_groups=selected_groups,
        )
        all_evals.extend(evals)
        fold_metrics = _metrics(evals, group_selected=True)
        fold_metrics.update(
            {
                "fold": fold,
                "train_rows": len(train_rows),
                "holdout_rows": len(holdout_rows),
                "train_candidate_groups": sorted(selected_groups),
            }
        )
        folds_out.append(fold_metrics)

    group_evals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_evals:
        if row.get("candidate_applied"):
            group_evals[str(row["group"])].append(row)
    group_results = [
        {"group": group, **_metrics(evals, group_selected=True)}
        for group, evals in sorted(group_evals.items())
    ]
    candidate_only = _metrics(
        (row for row in all_evals if row.get("candidate_applied")),
        group_selected=True,
    )
    candidate_only["candidate_groups"] = sorted(group_evals)
    overall = _metrics(all_evals, group_selected=True)
    applied_hurts = _applied_hurts(group_results)
    result = {
        "group_field": group_field,
        "folds": fold_count,
        "min_windows": int(min_windows),
        "min_sessions": int(min_sessions),
        "train_candidate_status_counts": dict(sorted(status_counts.items())),
        "overall": overall,
        "candidate_only": candidate_only,
        "applied_hurts": applied_hurts,
        "fold_results": folds_out,
        "group_results": group_results,
    }
    result["overall_status"] = _overall_status(candidate_only, applied_hurts)
    return result


def _print_summary(result: dict[str, Any], *, top: int) -> None:
    candidate = result["candidate_only"]
    print(
        " ".join(
            (
                f"overall_status={result['overall_status']}",
                f"group_field={result['group_field']}",
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
        description="Cross-validate v3 formal/value sampler candidates.",
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
    )
    result = {
        "errors": errors,
        **summarize_holdout(
            rows,
            group_field=args.by,
            folds=args.folds,
            min_windows=args.min_windows,
            min_sessions=args.min_sessions,
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
