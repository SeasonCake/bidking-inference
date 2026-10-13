"""Cross-validate v3 underestimate-repair candidates by session holdout."""

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
from summarize_v3_underestimate_repair_candidates import (  # noqa: E402
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


def _pinball_loss(truth: float, prediction: float, quantile: float) -> float:
    delta = truth - prediction
    if delta >= 0:
        return quantile * delta
    return (1.0 - quantile) * (-delta)


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


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
        scale = float(candidate.get("proposed_scale") or 1.0) if candidate else 1.0
        formal_p50 = _float_or_none(row.get("v3_post_formal_decision_value_p50"))
        formal_p90 = _float_or_none(row.get("v3_post_formal_decision_value_p90"))
        truth = _float_or_none(row.get("v3_truth_formal_decision_value"))
        q6_p50 = _float_or_none(row.get("v3_post_q6_formal_decision_value_p50"))
        q6_truth = _float_or_none(row.get("v3_truth_q6_formal_decision_value"))
        if formal_p50 is None or formal_p90 is None or truth is None:
            continue
        eval_row = {
            "group": group,
            "session_id": _session_id(row),
            "candidate_applied": candidate is not None,
            "candidate_status": (
                candidate.get("candidate_status") if candidate is not None else None
            ),
            "scale": _round_metric(scale, 6),
            "baseline_formal_p50": formal_p50,
            "scaled_formal_p50": formal_p50 * scale,
            "baseline_formal_p90": formal_p90,
            "scaled_formal_p90": formal_p90 * scale,
            "truth_formal": truth,
            "baseline_q6_formal_p50": q6_p50,
            "scaled_q6_formal_p50": q6_p50 * scale if q6_p50 is not None else None,
            "truth_q6_formal": q6_truth,
        }
        out.append(eval_row)
    return out


def _metrics(evals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = tuple(evals)
    formal_pairs = tuple(
        (float(row["baseline_formal_p50"]), float(row["truth_formal"]))
        for row in rows
    )
    scaled_pairs = tuple(
        (float(row["scaled_formal_p50"]), float(row["truth_formal"]))
        for row in rows
    )
    formal_p90_pairs = tuple(
        (float(row["baseline_formal_p90"]), float(row["truth_formal"]))
        for row in rows
    )
    scaled_p90_pairs = tuple(
        (float(row["scaled_formal_p90"]), float(row["truth_formal"]))
        for row in rows
    )
    q6_pairs = tuple(
        (
            float(row["baseline_q6_formal_p50"]),
            float(row["truth_q6_formal"]),
        )
        for row in rows
        if row.get("baseline_q6_formal_p50") is not None
        and row.get("truth_q6_formal") is not None
    )
    scaled_q6_pairs = tuple(
        (
            float(row["scaled_q6_formal_p50"]),
            float(row["truth_q6_formal"]),
        )
        for row in rows
        if row.get("scaled_q6_formal_p50") is not None
        and row.get("truth_q6_formal") is not None
    )

    def mae(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(abs(pred - truth) for pred, truth in pairs)

    def bias(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(pred - truth for pred, truth in pairs)

    def below(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(1.0 if pred < truth else 0.0 for pred, truth in pairs)

    def over(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(1.0 if pred > truth else 0.0 for pred, truth in pairs)

    def cover(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)

    def pinball(
        pairs: tuple[tuple[float, float], ...],
        quantile: float,
    ) -> float | None:
        return _mean(_pinball_loss(truth, pred, quantile) for pred, truth in pairs)

    baseline_mae = mae(formal_pairs)
    scaled_mae = mae(scaled_pairs)
    baseline_q6_mae = mae(q6_pairs)
    scaled_q6_mae = mae(scaled_q6_pairs)
    sessions = {str(row["session_id"]) for row in rows}
    candidate_sessions = {
        str(row["session_id"]) for row in rows if row.get("candidate_applied")
    }
    candidate_groups = {
        str(row["group"]) for row in rows if row.get("candidate_applied")
    }
    return {
        "n": len(rows),
        "sessions": len(sessions),
        "candidate_rows": sum(1 for row in rows if row.get("candidate_applied")),
        "candidate_sessions": len(candidate_sessions),
        "candidate_groups": sorted(candidate_groups),
        "baseline_formal_p50_mae": _round_metric(baseline_mae, 3),
        "scaled_formal_p50_mae": _round_metric(scaled_mae, 3),
        "delta_formal_p50_mae": _round_metric(
            scaled_mae - baseline_mae
            if scaled_mae is not None and baseline_mae is not None
            else None,
            3,
        ),
        "baseline_formal_p50_bias": _round_metric(bias(formal_pairs), 3),
        "scaled_formal_p50_bias": _round_metric(bias(scaled_pairs), 3),
        "baseline_formal_p50_below_rate": _round_metric(below(formal_pairs), 6),
        "scaled_formal_p50_below_rate": _round_metric(below(scaled_pairs), 6),
        "baseline_formal_p50_over_rate": _round_metric(over(formal_pairs), 6),
        "scaled_formal_p50_over_rate": _round_metric(over(scaled_pairs), 6),
        "baseline_formal_p50_pinball": _round_metric(pinball(formal_pairs, 0.5), 3),
        "scaled_formal_p50_pinball": _round_metric(pinball(scaled_pairs, 0.5), 3),
        "baseline_formal_p90_coverage": _round_metric(cover(formal_p90_pairs), 6),
        "scaled_formal_p90_coverage": _round_metric(cover(scaled_p90_pairs), 6),
        "baseline_formal_p90_pinball": _round_metric(
            pinball(formal_p90_pairs, 0.9),
            3,
        ),
        "scaled_formal_p90_pinball": _round_metric(
            pinball(scaled_p90_pairs, 0.9),
            3,
        ),
        "baseline_q6_formal_p50_mae": _round_metric(baseline_q6_mae, 3),
        "scaled_q6_formal_p50_mae": _round_metric(scaled_q6_mae, 3),
        "delta_q6_formal_p50_mae": _round_metric(
            scaled_q6_mae - baseline_q6_mae
            if scaled_q6_mae is not None and baseline_q6_mae is not None
            else None,
            3,
        ),
    }


def summarize_holdout(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = DEFAULT_GROUP_FIELD,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    max_upshift: float = 1.25,
) -> dict[str, Any]:
    paired = tuple(_paired_rows(rows))
    all_evals: list[dict[str, Any]] = []
    folds_out: list[dict[str, Any]] = []
    for fold in range(max(1, int(folds))):
        train_rows = [
            row
            for row in paired
            if _stable_fold(_session_id(row), int(folds)) != fold
        ]
        holdout_rows = [
            row
            for row in paired
            if _stable_fold(_session_id(row), int(folds)) == fold
        ]
        candidates = summarize_candidates(
            train_rows,
            group_field=group_field,
            min_windows=min_windows,
            min_sessions=min_sessions,
            max_upshift=max_upshift,
        )
        candidate_by_group = {
            str(row["group"]): row
            for row in candidates
            if row.get("candidate_status") == "watch_only_upshift_candidate"
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
            row["delta_formal_p50_mae"]
            if row["delta_formal_p50_mae"] is not None
            else 0.0,
            str(row["group"]),
        ),
    )

    candidate_status_by_fold = Counter()
    for row in folds_out:
        candidate_status_by_fold.update(row["train_candidate_status_counts"])
    return {
        "group_field": group_field,
        "folds": int(folds),
        "min_windows": int(min_windows),
        "min_sessions": int(min_sessions),
        "max_upshift": float(max_upshift),
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
                f"rows={overall['n']}",
                f"sessions={overall['sessions']}",
                f"candidate_rows={overall['candidate_rows']}",
                f"candidate_sessions={overall['candidate_sessions']}",
                f"mae={overall['baseline_formal_p50_mae']}",
                f"scaled_mae={overall['scaled_formal_p50_mae']}",
                f"delta={overall['delta_formal_p50_mae']}",
                f"below={overall['baseline_formal_p50_below_rate']}",
                f"scaled_below={overall['scaled_formal_p50_below_rate']}",
                f"p90_cover={overall['baseline_formal_p90_coverage']}",
                f"scaled_p90_cover={overall['scaled_formal_p90_coverage']}",
                f"pinball={overall['baseline_formal_p50_pinball']}",
                f"scaled_pinball={overall['scaled_formal_p50_pinball']}",
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
                f"mae={candidate_only['baseline_formal_p50_mae']}",
                f"scaled_mae={candidate_only['scaled_formal_p50_mae']}",
                f"delta={candidate_only['delta_formal_p50_mae']}",
                f"below={candidate_only['baseline_formal_p50_below_rate']}",
                f"scaled_below={candidate_only['scaled_formal_p50_below_rate']}",
                f"p90_cover={candidate_only['baseline_formal_p90_coverage']}",
                f"scaled_p90_cover={candidate_only['scaled_formal_p90_coverage']}",
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
                    f"delta={row['delta_formal_p50_mae']}",
                    f"mae={row['baseline_formal_p50_mae']}",
                    f"scaled_mae={row['scaled_formal_p50_mae']}",
                    f"below={row['baseline_formal_p50_below_rate']}",
                    f"scaled_below={row['scaled_formal_p50_below_rate']}",
                    f"p90_cover={row['baseline_formal_p90_coverage']}",
                    f"scaled_p90_cover={row['scaled_formal_p90_coverage']}",
                    f"q6_delta={row['delta_q6_formal_p50_mae']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-validate v3 underestimate repair candidates by session holdout.",
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
        underestimate_repair_entries={},
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
