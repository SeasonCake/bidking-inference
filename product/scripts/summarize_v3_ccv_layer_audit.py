"""Audit v3 CCV holdout stability across grouping layers."""

from __future__ import annotations

import argparse
import json
import sys
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
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)
from summarize_v3_ccv_holdout import summarize_holdout  # noqa: E402


DEFAULT_GROUP_FIELDS = (
    "hero_map_id",
    "map_id",
    "map_family",
    "hero_map_evidence_profile",
)


def _applied_hurt_groups(result: dict[str, Any]) -> list[str]:
    groups: list[str] = []
    for row in result.get("group_results", ()):
        if int(row.get("candidate_rows") or 0) <= 0:
            continue
        if (
            (
                row.get("delta_q6_count_p50_mae") is not None
                and float(row["delta_q6_count_p50_mae"]) > 0.05
            )
            or (
                row.get("delta_q6_cells_p50_mae") is not None
                and float(row["delta_q6_cells_p50_mae"]) > 0.25
            )
            or (
                row.get("delta_q6_value_p50_mae") is not None
                and float(row["delta_q6_value_p50_mae"]) > 10_000.0
            )
            or (
                row.get("delta_q6_formal_p50_mae") is not None
                and float(row["delta_q6_formal_p50_mae"]) > 10_000.0
            )
        ):
            groups.append(str(row.get("group")))
    return groups


def _layer_status(
    candidate: dict[str, Any],
    applied_hurts: list[str],
) -> str:
    candidate_rows = int(candidate.get("n") or 0)
    if candidate_rows <= 0:
        return "sample_limited"
    if applied_hurts:
        return "blocked_applied_hurt"
    count_delta = candidate.get("delta_q6_count_p50_mae")
    cells_delta = candidate.get("delta_q6_cells_p50_mae")
    formal_delta = candidate.get("delta_q6_formal_p50_mae")
    if (
        count_delta is not None
        and cells_delta is not None
        and formal_delta is not None
        and float(count_delta) <= 0.0
        and float(cells_delta) < 0.0
        and float(formal_delta) <= 0.0
    ):
        return "watch"
    return "blocked_holdout_delta"


def summarize_layers(
    rows: Iterable[dict[str, Any]],
    *,
    group_fields: Iterable[str] = DEFAULT_GROUP_FIELDS,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_ccv_likelihood_rate: float = 0.20,
) -> dict[str, Any]:
    layers: list[dict[str, Any]] = []
    for field in group_fields:
        result = summarize_holdout(
            rows,
            group_field=str(field),
            folds=folds,
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_ccv_likelihood_rate=min_ccv_likelihood_rate,
        )
        overall = result["overall"]
        candidate = result["candidate_only"]
        applied_hurts = _applied_hurt_groups(result)
        layers.append(
            {
                "group_field": str(field),
                "status": _layer_status(candidate, applied_hurts),
                "rows": overall.get("n"),
                "sessions": overall.get("sessions"),
                "candidate_rows": candidate.get("n"),
                "candidate_sessions": candidate.get("sessions"),
                "candidate_groups": candidate.get("candidate_groups"),
                "candidate_delta_q6_count_p50_mae": candidate.get(
                    "delta_q6_count_p50_mae"
                ),
                "candidate_delta_q6_cells_p50_mae": candidate.get(
                    "delta_q6_cells_p50_mae"
                ),
                "candidate_delta_q6_value_p50_mae": candidate.get(
                    "delta_q6_value_p50_mae"
                ),
                "candidate_delta_q6_formal_p50_mae": candidate.get(
                    "delta_q6_formal_p50_mae"
                ),
                "overall_delta_q6_count_p50_mae": overall.get(
                    "delta_q6_count_p50_mae"
                ),
                "overall_delta_q6_cells_p50_mae": overall.get(
                    "delta_q6_cells_p50_mae"
                ),
                "applied_ccv_hurts_groups": applied_hurts,
                "status_counts": result.get("candidate_status_counts_across_folds"),
            }
        )
    status_rank = {
        "blocked_applied_hurt": 0,
        "blocked_holdout_delta": 1,
        "sample_limited": 2,
        "watch": 3,
    }
    overall_status = min(
        (str(layer["status"]) for layer in layers),
        key=lambda item: status_rank.get(item, 99),
        default="sample_limited",
    )
    return {
        "folds": int(folds),
        "min_windows": int(min_windows),
        "min_sessions": int(min_sessions),
        "min_ccv_likelihood_rate": _round_metric(min_ccv_likelihood_rate, 6),
        "overall_status": overall_status,
        "layers": layers,
    }


def _print_summary(result: dict[str, Any]) -> None:
    print(
        " ".join(
            (
                f"overall_status={result['overall_status']}",
                f"folds={result['folds']}",
                f"min_windows={result['min_windows']}",
                f"min_sessions={result['min_sessions']}",
                f"min_ccv_likelihood_rate={result['min_ccv_likelihood_rate']}",
            )
        )
    )
    for row in result["layers"]:
        print(
            " ".join(
                (
                    f"group_field={row['group_field']}",
                    f"status={row['status']}",
                    f"rows={row['rows']}",
                    f"sessions={row['sessions']}",
                    f"candidate_rows={row['candidate_rows']}",
                    "groups=" + ",".join(row["candidate_groups"] or ()),
                    f"count_delta={row['candidate_delta_q6_count_p50_mae']}",
                    f"cells_delta={row['candidate_delta_q6_cells_p50_mae']}",
                    f"value_delta={row['candidate_delta_q6_value_p50_mae']}",
                    f"formal_delta={row['candidate_delta_q6_formal_p50_mae']}",
                    "applied_hurts="
                    + ",".join(row["applied_ccv_hurts_groups"] or ()),
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit v3 CCV holdout stability across grouping layers.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--group-field",
        action="append",
        dest="group_fields",
        help="Grouping field to audit. Can be passed more than once.",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-ccv-likelihood-rate", type=float, default=0.20)
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
        **summarize_layers(
            rows,
            group_fields=args.group_fields or DEFAULT_GROUP_FIELDS,
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
        _print_summary(result)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
