"""Compare v3 CCVC q6-count movement policies across holdout groupings."""

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
    V3CcvOptions,
    _default_calibration_path,
    _default_paths,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)
from summarize_v3_ccv_direction_audit import MOVEMENT_POLICIES  # noqa: E402
from summarize_v3_ccv_direction_holdout import summarize_holdout  # noqa: E402

DEFAULT_GROUP_FIELDS = (
    "evidence_profile_key",
    "map_id",
    "map_id,evidence_profile_key",
)
DEFAULT_POLICIES = ("all", "up_only", "down_only")


def _group_metric_row(row: dict[str, Any]) -> dict[str, Any]:
    label = f"{row.get('component')}:{row.get('group')}"
    return {
        "label": label,
        "component": row.get("component"),
        "group": row.get("group"),
        "rows": row.get("n"),
        "sessions": row.get("sessions"),
        "candidate_rows": row.get("candidate_rows"),
        "candidate_sessions": row.get("candidate_sessions"),
        "candidate_delta_p50_mae": row.get(
            "candidate_only_delta_p50_mae"
        ),
        "candidate_hurt_rate": row.get("candidate_only_hurt_rate"),
        "candidate_hurt_rows": row.get("candidate_only_hurt_rows"),
        "candidate_helped_rows": row.get("candidate_only_helped_rows"),
        "candidate_directional_error_rate": row.get(
            "candidate_only_directional_error_rate"
        ),
        "candidate_directional_error_rows": row.get(
            "candidate_only_directional_error_rows"
        ),
        "candidate_baseline_below_rate": row.get(
            "baseline_p50_below_rate"
        ),
        "candidate_below_rate": row.get("candidate_p50_below_rate"),
    }


def _candidate_group_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_groups = set(result["candidate_only"].get("candidate_groups") or ())
    out: list[dict[str, Any]] = []
    for row in result.get("group_results") or ():
        label = f"{row.get('component')}:{row.get('group')}"
        if label not in candidate_groups:
            continue
        out.append(_group_metric_row(row))
    return out


def _applied_hurt_group_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    hurt_groups = set(result.get("applied_direction_hurts_groups") or ())
    out: list[dict[str, Any]] = []
    for row in result.get("group_results") or ():
        label = f"{row.get('component')}:{row.get('group')}"
        if label in hurt_groups:
            out.append(_group_metric_row(row))
    return out


def summarize_matrix(
    rows: Iterable[dict[str, Any]],
    *,
    group_fields: Iterable[str] = DEFAULT_GROUP_FIELDS,
    movement_policies: Iterable[str] = DEFAULT_POLICIES,
    candidate_prefix: str = "v3_ccvc_",
    component: str = "q6_count",
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_changed: int = 5,
    max_hurt_rate: float = 0.45,
    max_directional_error_rate: float = 0.35,
    candidate_include_pattern: str | None = None,
    candidate_exclude_pattern: str | None = None,
) -> list[dict[str, Any]]:
    source_rows = tuple(rows)
    out: list[dict[str, Any]] = []
    for group_field in group_fields:
        for movement_policy in movement_policies:
            result = summarize_holdout(
                source_rows,
                group_field=group_field,
                components=(component,),
                folds=folds,
                min_windows=min_windows,
                min_sessions=min_sessions,
                min_changed=min_changed,
                max_hurt_rate=max_hurt_rate,
                max_directional_error_rate=max_directional_error_rate,
                candidate_prefix=candidate_prefix,
                movement_policy=movement_policy,
                candidate_include_pattern=candidate_include_pattern,
                candidate_exclude_pattern=candidate_exclude_pattern,
            )
            candidate = result["candidate_only"]
            out.append(
                {
                    "group_field": result["group_field"],
                    "movement_policy": result["movement_policy"],
                    "candidate_prefix": result["candidate_prefix"],
                    "component": component,
                    "overall_status": result["overall_status"],
                    "rows": result["overall"]["n"],
                    "candidate_rows": candidate["candidate_rows"],
                    "candidate_groups": candidate["candidate_groups"],
                    "candidate_group_results": _candidate_group_results(result),
                    "applied_hurt_group_results": _applied_hurt_group_results(
                        result
                    ),
                    "candidate_delta_p50_mae": candidate[
                        "candidate_only_delta_p50_mae"
                    ],
                    "candidate_hurt_rate": candidate[
                        "candidate_only_hurt_rate"
                    ],
                    "candidate_directional_error_rate": candidate[
                        "candidate_only_directional_error_rate"
                    ],
                    "candidate_baseline_below_rate": candidate[
                        "baseline_p50_below_rate"
                    ],
                    "candidate_below_rate": candidate[
                        "candidate_p50_below_rate"
                    ],
                    "candidate_include_pattern": result[
                        "candidate_include_pattern"
                    ],
                    "candidate_exclude_pattern": result[
                        "candidate_exclude_pattern"
                    ],
                    "applied_hurts": result["applied_direction_hurts_groups"],
                    "train_candidate_status_counts": result[
                        "train_candidate_status_counts"
                    ],
                }
            )
    return out


def _print_summary(rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        print(
            " ".join(
                (
                    f"group_field={row['group_field']}",
                    f"policy={row['movement_policy']}",
                    f"include={row['candidate_include_pattern']}",
                    f"exclude={row['candidate_exclude_pattern']}",
                    f"status={row['overall_status']}",
                    f"rows={row['rows']}",
                    f"candidate_rows={row['candidate_rows']}",
                    "candidate_groups=" + ",".join(row["candidate_groups"]),
                    f"delta={row['candidate_delta_p50_mae']}",
                    f"hurt_rate={row['candidate_hurt_rate']}",
                    "directional_error="
                    f"{row['candidate_directional_error_rate']}",
                    f"baseline_below={row['candidate_baseline_below_rate']}",
                    f"candidate_below={row['candidate_below_rate']}",
                    "applied_hurts=" + ",".join(row["applied_hurts"]),
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare v3 CCVC q6-count policy holdout results.",
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
        help="Group field to audit. Can be passed more than once.",
    )
    parser.add_argument(
        "--movement-policy",
        action="append",
        choices=MOVEMENT_POLICIES,
        help="Movement policy to audit. Can be passed more than once.",
    )
    parser.add_argument("--candidate-prefix", default="v3_ccvc_")
    parser.add_argument("--component", default="q6_count")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-changed", type=int, default=5)
    parser.add_argument("--max-hurt-rate", type=float, default=0.45)
    parser.add_argument("--max-directional-error-rate", type=float, default=0.35)
    parser.add_argument(
        "--candidate-include-pattern",
        help="Regex over 'component:group' labels allowed into holdout candidates.",
    )
    parser.add_argument(
        "--candidate-exclude-pattern",
        help="Regex over 'component:group' labels removed from holdout candidates.",
    )
    parser.add_argument("--posterior-trials", type=int, default=512)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument(
        "--ccv-component-freeze-cells",
        action="store_true",
        help="Keep q6 cells at baseline when emitting v3_ccvc_ fields.",
    )
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
            component_likelihood=args.candidate_prefix == "v3_ccvc_",
            component_move_cells=not args.ccv_component_freeze_cells,
        ),
    )
    result = {
        "errors": errors,
        "matrix": summarize_matrix(
            rows,
            group_fields=args.group_field or DEFAULT_GROUP_FIELDS,
            movement_policies=args.movement_policy or DEFAULT_POLICIES,
            candidate_prefix=args.candidate_prefix,
            component=args.component,
            folds=args.folds,
            min_windows=args.min_windows,
            min_sessions=args.min_sessions,
            min_changed=args.min_changed,
            max_hurt_rate=args.max_hurt_rate,
            max_directional_error_rate=args.max_directional_error_rate,
            candidate_include_pattern=args.candidate_include_pattern,
            candidate_exclude_pattern=args.candidate_exclude_pattern,
        ),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_summary(result["matrix"])
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
