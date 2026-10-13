"""Cross-validate v3 count/cell/value sampler candidates by session holdout."""

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
from summarize_v3_ccv_profile_candidates import (  # noqa: E402
    DEFAULT_GROUP_FIELD,
    _paired_rows,
    summarize_candidates,
)


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


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _pinball_loss(truth: float, prediction: float, quantile: float) -> float:
    delta = truth - prediction
    if delta >= 0:
        return quantile * delta
    return (1.0 - quantile) * (-delta)


def _candidate_value(
    row: dict[str, Any],
    *,
    candidate: dict[str, Any] | None,
    baseline_key: str,
    candidate_key: str,
) -> float | None:
    baseline = _float_or_none(row.get(baseline_key))
    if candidate is None:
        return baseline
    ccv = _float_or_none(row.get(candidate_key))
    return ccv if ccv is not None else baseline


def _row_evals(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str,
    candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        group = _group_value(row, group_field)
        candidate = candidates.get(group)
        eval_row: dict[str, Any] = {
            "group": group,
            "session_id": _session_id(row),
            "candidate_applied": candidate is not None,
            "candidate_status": (
                candidate.get("candidate_status") if candidate is not None else None
            ),
            "baseline_q6_count_p50": _float_or_none(row.get("v3_post_q6_count_p50")),
            "candidate_q6_count_p50": _candidate_value(
                row,
                candidate=candidate,
                baseline_key="v3_post_q6_count_p50",
                candidate_key="v3_ccv_q6_count_p50",
            ),
            "baseline_q6_count_p90": _float_or_none(row.get("v3_post_q6_count_p90")),
            "candidate_q6_count_p90": _candidate_value(
                row,
                candidate=candidate,
                baseline_key="v3_post_q6_count_p90",
                candidate_key="v3_ccv_q6_count_p90",
            ),
            "truth_q6_count": _float_or_none(row.get("v3_truth_q6_count")),
            "baseline_q6_cells_p50": _float_or_none(row.get("v3_post_q6_cells_p50")),
            "candidate_q6_cells_p50": _candidate_value(
                row,
                candidate=candidate,
                baseline_key="v3_post_q6_cells_p50",
                candidate_key="v3_ccv_q6_cells_p50",
            ),
            "baseline_q6_cells_p90": _float_or_none(row.get("v3_post_q6_cells_p90")),
            "candidate_q6_cells_p90": _candidate_value(
                row,
                candidate=candidate,
                baseline_key="v3_post_q6_cells_p90",
                candidate_key="v3_ccv_q6_cells_p90",
            ),
            "truth_q6_cells": _float_or_none(row.get("v3_truth_q6_cells")),
            "baseline_q6_value_p50": _float_or_none(row.get("v3_post_q6_value_p50")),
            "candidate_q6_value_p50": _candidate_value(
                row,
                candidate=candidate,
                baseline_key="v3_post_q6_value_p50",
                candidate_key="v3_ccv_q6_value_p50",
            ),
            "baseline_q6_value_p90": _float_or_none(row.get("v3_post_q6_value_p90")),
            "candidate_q6_value_p90": _candidate_value(
                row,
                candidate=candidate,
                baseline_key="v3_post_q6_value_p90",
                candidate_key="v3_ccv_q6_value_p90",
            ),
            "truth_q6_value": _float_or_none(row.get("v3_truth_q6_raw_value")),
            "baseline_q6_formal_p50": _float_or_none(
                row.get("v3_post_q6_formal_decision_value_p50")
            ),
            "candidate_q6_formal_p50": _candidate_value(
                row,
                candidate=candidate,
                baseline_key="v3_post_q6_formal_decision_value_p50",
                candidate_key="v3_ccv_q6_formal_decision_value_p50",
            ),
            "baseline_q6_formal_p90": _float_or_none(
                row.get("v3_post_q6_formal_decision_value_p90")
            ),
            "candidate_q6_formal_p90": _candidate_value(
                row,
                candidate=candidate,
                baseline_key="v3_post_q6_formal_decision_value_p90",
                candidate_key="v3_ccv_q6_formal_decision_value_p90",
            ),
            "truth_q6_formal": _float_or_none(
                row.get("v3_truth_q6_formal_decision_value")
            ),
        }
        out.append(eval_row)
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


def _bias(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(pred - truth for pred, truth in pairs)


def _below_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred < truth else 0.0 for pred, truth in pairs)


def _over_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred > truth else 0.0 for pred, truth in pairs)


def _coverage_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)


def _pinball(
    pairs: Iterable[tuple[float, float]],
    quantile: float,
) -> float | None:
    return _mean(_pinball_loss(truth, pred, quantile) for pred, truth in pairs)


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
    out[f"baseline_{component}_p50_mae"] = _round_metric(base_mae, digits)
    out[f"candidate_{component}_p50_mae"] = _round_metric(cand_mae, digits)
    out[f"delta_{component}_p50_mae"] = _round_metric(
        cand_mae - base_mae if cand_mae is not None and base_mae is not None else None,
        digits,
    )
    out[f"baseline_{component}_p50_bias"] = _round_metric(_bias(base_p50), digits)
    out[f"candidate_{component}_p50_bias"] = _round_metric(_bias(cand_p50), digits)
    out[f"baseline_{component}_p50_below_rate"] = _round_metric(
        _below_rate(base_p50),
        6,
    )
    out[f"candidate_{component}_p50_below_rate"] = _round_metric(
        _below_rate(cand_p50),
        6,
    )
    out[f"baseline_{component}_p50_over_rate"] = _round_metric(
        _over_rate(base_p50),
        6,
    )
    out[f"candidate_{component}_p50_over_rate"] = _round_metric(
        _over_rate(cand_p50),
        6,
    )
    out[f"baseline_{component}_p50_pinball"] = _round_metric(
        _pinball(base_p50, 0.5),
        digits,
    )
    out[f"candidate_{component}_p50_pinball"] = _round_metric(
        _pinball(cand_p50, 0.5),
        digits,
    )
    out[f"baseline_{component}_p90_coverage"] = _round_metric(
        _coverage_rate(base_p90),
        6,
    )
    out[f"candidate_{component}_p90_coverage"] = _round_metric(
        _coverage_rate(cand_p90),
        6,
    )
    out[f"baseline_{component}_p90_pinball"] = _round_metric(
        _pinball(base_p90, 0.9),
        digits,
    )
    out[f"candidate_{component}_p90_pinball"] = _round_metric(
        _pinball(cand_p90, 0.9),
        digits,
    )


def _metrics(evals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = tuple(evals)
    sessions = {str(row["session_id"]) for row in rows}
    candidate_sessions = {
        str(row["session_id"]) for row in rows if row.get("candidate_applied")
    }
    candidate_groups = {
        str(row["group"]) for row in rows if row.get("candidate_applied")
    }
    out: dict[str, Any] = {
        "n": len(rows),
        "sessions": len(sessions),
        "candidate_rows": sum(1 for row in rows if row.get("candidate_applied")),
        "candidate_sessions": len(candidate_sessions),
        "candidate_groups": sorted(candidate_groups),
    }
    _add_component_metrics(
        out,
        rows,
        component="q6_count",
        truth_key="truth_q6_count",
        digits=3,
    )
    _add_component_metrics(
        out,
        rows,
        component="q6_cells",
        truth_key="truth_q6_cells",
        digits=3,
    )
    _add_component_metrics(
        out,
        rows,
        component="q6_value",
        truth_key="truth_q6_value",
        digits=1,
    )
    _add_component_metrics(
        out,
        rows,
        component="q6_formal",
        truth_key="truth_q6_formal",
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
    min_ccv_likelihood_rate: float = 0.20,
) -> dict[str, Any]:
    fold_count = max(1, int(folds))
    paired = tuple(_paired_rows(rows))
    all_evals: list[dict[str, Any]] = []
    folds_out: list[dict[str, Any]] = []
    for fold in range(fold_count):
        train_rows = [
            row for row in paired if _stable_fold(_session_id(row), fold_count) != fold
        ]
        holdout_rows = [
            row for row in paired if _stable_fold(_session_id(row), fold_count) == fold
        ]
        candidates = summarize_candidates(
            train_rows,
            group_field=group_field,
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_ccv_likelihood_rate=min_ccv_likelihood_rate,
        )
        candidate_by_group = {
            str(row["group"]): row
            for row in candidates
            if row.get("candidate_status") == "watch_only_count_cell_candidate"
        }
        evals = _row_evals(
            holdout_rows,
            group_field=group_field,
            candidates=candidate_by_group,
        )
        all_evals.extend(evals)
        fold_status_counts = Counter(str(row["candidate_status"]) for row in candidates)
        fold_metrics = _metrics(evals)
        fold_metrics.update(
            {
                "fold": fold,
                "train_rows": len(train_rows),
                "holdout_rows": len(holdout_rows),
                "train_candidate_status_counts": dict(
                    sorted(fold_status_counts.items())
                ),
                "train_candidate_groups": sorted(candidate_by_group),
            }
        )
        folds_out.append(fold_metrics)

    group_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_evals:
        group_rows[str(row["group"])].append(row)
    groups = [
        {"group": group, **_metrics(group_evals)}
        for group, group_evals in sorted(group_rows.items())
        if any(row.get("candidate_applied") for row in group_evals)
    ]
    groups = sorted(
        groups,
        key=lambda row: (
            row["delta_q6_cells_p50_mae"]
            if row["delta_q6_cells_p50_mae"] is not None
            else 0.0,
            row["delta_q6_count_p50_mae"]
            if row["delta_q6_count_p50_mae"] is not None
            else 0.0,
            str(row["group"]),
        ),
    )

    candidate_status_by_fold = Counter()
    for row in folds_out:
        candidate_status_by_fold.update(row["train_candidate_status_counts"])
    return {
        "group_field": group_field,
        "folds": fold_count,
        "min_windows": int(min_windows),
        "min_sessions": int(min_sessions),
        "min_ccv_likelihood_rate": float(min_ccv_likelihood_rate),
        "candidate_status_counts_across_folds": dict(
            sorted(candidate_status_by_fold.items())
        ),
        "overall": _metrics(all_evals),
        "candidate_only": _metrics(
            row for row in all_evals if row.get("candidate_applied")
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
                f"min_ccv_likelihood_rate={result['min_ccv_likelihood_rate']}",
                f"rows={overall['n']}",
                f"sessions={overall['sessions']}",
                f"candidate_rows={overall['candidate_rows']}",
                f"candidate_sessions={overall['candidate_sessions']}",
                f"count_mae={overall['baseline_q6_count_p50_mae']}",
                f"ccv_count_mae={overall['candidate_q6_count_p50_mae']}",
                f"count_delta={overall['delta_q6_count_p50_mae']}",
                f"cells_mae={overall['baseline_q6_cells_p50_mae']}",
                f"ccv_cells_mae={overall['candidate_q6_cells_p50_mae']}",
                f"cells_delta={overall['delta_q6_cells_p50_mae']}",
                f"q6_formal_mae={overall['baseline_q6_formal_p50_mae']}",
                f"ccv_q6_formal_mae={overall['candidate_q6_formal_p50_mae']}",
                f"q6_formal_delta={overall['delta_q6_formal_p50_mae']}",
                f"count_p90_cover={overall['baseline_q6_count_p90_coverage']}",
                f"ccv_count_p90_cover={overall['candidate_q6_count_p90_coverage']}",
                f"cells_p90_cover={overall['baseline_q6_cells_p90_coverage']}",
                f"ccv_cells_p90_cover={overall['candidate_q6_cells_p90_coverage']}",
            )
        )
    )
    print(
        " ".join(
            (
                "candidate_only",
                f"rows={candidate_only['n']}",
                f"sessions={candidate_only['sessions']}",
                f"groups={','.join(candidate_only['candidate_groups']) or '-'}",
                f"count_mae={candidate_only['baseline_q6_count_p50_mae']}",
                f"ccv_count_mae={candidate_only['candidate_q6_count_p50_mae']}",
                f"count_delta={candidate_only['delta_q6_count_p50_mae']}",
                f"cells_mae={candidate_only['baseline_q6_cells_p50_mae']}",
                f"ccv_cells_mae={candidate_only['candidate_q6_cells_p50_mae']}",
                f"cells_delta={candidate_only['delta_q6_cells_p50_mae']}",
                f"q6_formal_mae={candidate_only['baseline_q6_formal_p50_mae']}",
                f"ccv_q6_formal_mae={candidate_only['candidate_q6_formal_p50_mae']}",
                f"q6_formal_delta={candidate_only['delta_q6_formal_p50_mae']}",
            )
        )
    )
    print(
        "status_counts="
        + ",".join(
            f"{status}:{count}"
            for status, count in result[
                "candidate_status_counts_across_folds"
            ].items()
        )
    )
    for row in result["group_results"][:top]:
        print(
            " ".join(
                (
                    f"group={row['group']}",
                    f"rows={row['n']}",
                    f"sessions={row['sessions']}",
                    f"count_delta={row['delta_q6_count_p50_mae']}",
                    f"cells_delta={row['delta_q6_cells_p50_mae']}",
                    f"q6_value_delta={row['delta_q6_value_p50_mae']}",
                    f"q6_formal_delta={row['delta_q6_formal_p50_mae']}",
                    f"count_below={row['baseline_q6_count_p50_below_rate']}",
                    f"ccv_count_below={row['candidate_q6_count_p50_below_rate']}",
                    f"cells_below={row['baseline_q6_cells_p50_below_rate']}",
                    f"ccv_cells_below={row['candidate_q6_cells_p50_below_rate']}",
                    f"count_p90_cover={row['baseline_q6_count_p90_coverage']}",
                    f"ccv_count_p90_cover={row['candidate_q6_count_p90_coverage']}",
                    f"cells_p90_cover={row['baseline_q6_cells_p90_coverage']}",
                    f"ccv_cells_p90_cover={row['candidate_q6_cells_p90_coverage']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-validate v3 count/cell/value sampler candidates by session holdout.",
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
    parser.add_argument("--min-ccv-likelihood-rate", type=float, default=0.20)
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
            min_ccv_likelihood_rate=args.min_ccv_likelihood_rate,
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
