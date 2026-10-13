"""Audit whether v3 CCV p50 movements go in the right direction."""

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
    V3CcvOptions,
    _default_calibration_path,
    _default_paths,
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)


def component_fields(candidate_prefix: str = "v3_ccv_") -> dict[str, tuple[str, str, str]]:
    prefix = str(candidate_prefix)
    return {
        "q6_count": (
            "v3_post_q6_count_p50",
            f"{prefix}q6_count_p50",
            "v3_truth_q6_count",
        ),
        "q6_cells": (
            "v3_post_q6_cells_p50",
            f"{prefix}q6_cells_p50",
            "v3_truth_q6_cells",
        ),
        "q6_value": (
            "v3_post_q6_value_p50",
            f"{prefix}q6_value_p50",
            "v3_truth_q6_raw_value",
        ),
        "q6_formal": (
            "v3_post_q6_formal_decision_value_p50",
            f"{prefix}q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
        ),
    }


COMPONENT_FIELDS: dict[str, tuple[str, str, str]] = component_fields()
DEFAULT_COMPONENTS = ("q6_count", "q6_cells", "q6_formal")
MOVEMENT_POLICIES = ("all", "up_only", "down_only")


def apply_movement_policy(
    baseline: float,
    candidate: float,
    *,
    movement_policy: str = "all",
) -> float:
    policy = str(movement_policy)
    if policy == "all":
        return candidate
    if policy == "up_only":
        return candidate if candidate > baseline else baseline
    if policy == "down_only":
        return candidate if candidate < baseline else baseline
    raise ValueError(f"unknown movement_policy: {movement_policy}")


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _paired_rows(
    rows: Iterable[dict[str, Any]],
    *,
    component: str,
    fields: dict[str, tuple[str, str, str]],
    candidate_prefix: str,
) -> tuple[dict[str, Any], ...]:
    baseline_key, ccv_key, truth_key = fields[component]
    ready_key = f"{candidate_prefix}ready"
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_available")
        and row.get("v3_post_ready")
        and row.get(ready_key)
        and _float_or_none(row.get(baseline_key)) is not None
        and _float_or_none(row.get(ccv_key)) is not None
        and _float_or_none(row.get(truth_key)) is not None
    )


def _group_value(row: dict[str, Any], group_field: str) -> str:
    parts = tuple(part.strip() for part in str(group_field).split(",") if part.strip())
    if len(parts) > 1:
        return "|".join(f"{part}={_group_value(row, part)}" for part in parts)
    value = row.get(group_field)
    return str(value) if value not in (None, "") else "unknown"


def _row_direction(
    row: dict[str, Any],
    *,
    component: str,
    fields: dict[str, tuple[str, str, str]],
    movement_policy: str = "all",
) -> dict[str, Any] | None:
    baseline_key, ccv_key, truth_key = fields[component]
    baseline = _float_or_none(row.get(baseline_key))
    raw_ccv = _float_or_none(row.get(ccv_key))
    truth = _float_or_none(row.get(truth_key))
    if baseline is None or raw_ccv is None or truth is None:
        return None
    ccv = apply_movement_policy(
        baseline,
        raw_ccv,
        movement_policy=movement_policy,
    )
    prediction_delta = ccv - baseline
    baseline_error = baseline - truth
    ccv_error = ccv - truth
    baseline_abs = abs(baseline_error)
    ccv_abs = abs(ccv_error)
    if abs(prediction_delta) <= 1e-9:
        effect = "unchanged"
    elif ccv_abs < baseline_abs - 1e-9:
        effect = "helped"
    elif ccv_abs > baseline_abs + 1e-9:
        effect = "hurt"
    else:
        effect = "neutral"
    if baseline_error < 0:
        baseline_side = "under"
    elif baseline_error > 0:
        baseline_side = "over"
    else:
        baseline_side = "exact"
    if prediction_delta > 0:
        move = "up"
    elif prediction_delta < 0:
        move = "down"
    else:
        move = "flat"
    directional_error = (
        (baseline_side == "under" and move == "down")
        or (baseline_side == "over" and move == "up")
    )
    return {
        "baseline": baseline,
        "ccv": ccv,
        "raw_ccv": raw_ccv,
        "truth": truth,
        "prediction_delta": prediction_delta,
        "baseline_error": baseline_error,
        "ccv_error": ccv_error,
        "baseline_abs_error": baseline_abs,
        "ccv_abs_error": ccv_abs,
        "effect": effect,
        "baseline_side": baseline_side,
        "move": move,
        "directional_error": directional_error,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _status(
    row: dict[str, Any],
    *,
    min_windows: int,
    min_sessions: int,
    min_changed: int,
    max_hurt_rate: float,
    max_directional_error_rate: float,
) -> str:
    if int(row["n"]) < min_windows or int(row["sessions"]) < min_sessions:
        return "blocked_low_sample"
    if int(row["changed_rows"]) < min_changed:
        return "blocked_low_movement"
    hurt_rate = row.get("hurt_rate_changed")
    directional_error_rate = row.get("directional_error_rate_changed")
    mae_delta = row.get("mae_delta")
    if (
        hurt_rate is not None
        and float(hurt_rate) > float(max_hurt_rate)
    ) or (
        directional_error_rate is not None
        and float(directional_error_rate) > float(max_directional_error_rate)
    ):
        return "blocked_directional_hurt"
    if mae_delta is not None and float(mae_delta) < 0.0:
        return "watch_directional_candidate"
    return "watch_neutral"


def _summarize_group(
    group: str,
    rows: Iterable[dict[str, Any]],
    *,
    component: str,
    group_field: str,
    fields: dict[str, tuple[str, str, str]],
    candidate_prefix: str,
    movement_policy: str,
    min_windows: int,
    min_sessions: int,
    min_changed: int,
    max_hurt_rate: float,
    max_directional_error_rate: float,
) -> dict[str, Any]:
    source_rows = tuple(rows)
    directions = tuple(
        item
        for row in source_rows
        if (item := _row_direction(
            row,
            component=component,
            fields=fields,
            movement_policy=movement_policy,
        )) is not None
    )
    changed = tuple(row for row in directions if row["move"] != "flat")
    sessions = {
        str(row.get("session_id"))
        for row in source_rows
        if row.get("session_id") not in (None, "")
    }
    effect_counts = Counter(str(row["effect"]) for row in directions)
    move_counts = Counter(str(row["move"]) for row in directions)
    side_counts = Counter(str(row["baseline_side"]) for row in directions)
    changed_effect_counts = Counter(str(row["effect"]) for row in changed)
    changed_move_counts = Counter(str(row["move"]) for row in changed)
    changed_directional_errors = sum(1 for row in changed if row["directional_error"])
    candidate_likelihood_rows = sum(
        1
        for row in source_rows
        if str(row.get(f"{candidate_prefix}match_scope") or "").endswith(
            "likelihood"
        )
    )
    public_total_rows = sum(
        1
        for row in source_rows
        if row.get("v3_summary_session_total_cells_exact") is not None
        or row.get("v3_summary_session_total_count_exact") is not None
    )
    q6_floor_rows = sum(
        1
        for row in source_rows
        if (row.get("v3_summary_q6_count_floor") or 0)
        or (row.get("v3_summary_q6_cells_floor") or 0)
        or (row.get("v3_summary_q6_value_floor") or 0)
        or row.get("v3_summary_q6_count_exact") is not None
        or row.get("v3_summary_q6_cells_exact") is not None
        or row.get("v3_summary_q6_value_exact") is not None
    )
    baseline_mae = _mean(row["baseline_abs_error"] for row in directions)
    ccv_mae = _mean(row["ccv_abs_error"] for row in directions)
    out: dict[str, Any] = {
        "group_field": group_field,
        "group": group,
        "component": component,
        "movement_policy": movement_policy,
        "n": len(directions),
        "sessions": len(sessions),
        "ccv_likelihood_rate": _round_metric(
            _rate(candidate_likelihood_rows, len(source_rows)),
            6,
        ),
        "public_total_rate": _round_metric(_rate(public_total_rows, len(source_rows)), 6),
        "q6_floor_rate": _round_metric(_rate(q6_floor_rows, len(source_rows)), 6),
        "changed_rows": len(changed),
        "changed_rate": _round_metric(_rate(len(changed), len(directions)), 6),
        "helped_rows": effect_counts.get("helped", 0),
        "hurt_rows": effect_counts.get("hurt", 0),
        "neutral_rows": effect_counts.get("neutral", 0),
        "unchanged_rows": effect_counts.get("unchanged", 0),
        "helped_changed": changed_effect_counts.get("helped", 0),
        "hurt_changed": changed_effect_counts.get("hurt", 0),
        "hurt_rate_changed": _round_metric(
            _rate(changed_effect_counts.get("hurt", 0), len(changed)),
            6,
        ),
        "directional_error_rows": changed_directional_errors,
        "directional_error_rate_changed": _round_metric(
            _rate(changed_directional_errors, len(changed)),
            6,
        ),
        "baseline_under_rate": _round_metric(
            _rate(side_counts.get("under", 0), len(directions)),
            6,
        ),
        "baseline_over_rate": _round_metric(
            _rate(side_counts.get("over", 0), len(directions)),
            6,
        ),
        "move_up_rows": move_counts.get("up", 0),
        "move_down_rows": move_counts.get("down", 0),
        "changed_move_up_rows": changed_move_counts.get("up", 0),
        "changed_move_down_rows": changed_move_counts.get("down", 0),
        "prediction_delta_mean": _round_metric(
            _mean(row["prediction_delta"] for row in directions),
            3,
        ),
        "prediction_delta_changed_mean": _round_metric(
            _mean(row["prediction_delta"] for row in changed),
            3,
        ),
        "baseline_mae": _round_metric(baseline_mae, 3),
        "ccv_mae": _round_metric(ccv_mae, 3),
        "mae_delta": _round_metric(
            ccv_mae - baseline_mae
            if ccv_mae is not None and baseline_mae is not None
            else None,
            3,
        ),
    }
    out["status"] = _status(
        out,
        min_windows=min_windows,
        min_sessions=min_sessions,
        min_changed=min_changed,
        max_hurt_rate=max_hurt_rate,
        max_directional_error_rate=max_directional_error_rate,
    )
    return out


def summarize_direction(
    rows: Iterable[dict[str, Any]],
    *,
    group_field: str = "map_id",
    components: Iterable[str] = DEFAULT_COMPONENTS,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_changed: int = 5,
    max_hurt_rate: float = 0.45,
    max_directional_error_rate: float = 0.35,
    candidate_prefix: str = "v3_ccv_",
    movement_policy: str = "all",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if movement_policy not in MOVEMENT_POLICIES:
        raise ValueError(f"unknown movement_policy: {movement_policy}")
    fields = component_fields(candidate_prefix)
    for component in components:
        if component not in fields:
            raise ValueError(f"unknown component: {component}")
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _paired_rows(
            rows,
            component=component,
            fields=fields,
            candidate_prefix=candidate_prefix,
        ):
            groups[_group_value(row, group_field)].append(row)
        for group, group_rows in groups.items():
            out.append(
                _summarize_group(
                    group,
                    group_rows,
                    component=component,
                    group_field=group_field,
                    fields=fields,
                    candidate_prefix=candidate_prefix,
                    movement_policy=movement_policy,
                    min_windows=min_windows,
                    min_sessions=min_sessions,
                    min_changed=min_changed,
                    max_hurt_rate=max_hurt_rate,
                    max_directional_error_rate=max_directional_error_rate,
                )
            )
    status_rank = {
        "blocked_directional_hurt": 0,
        "watch_directional_candidate": 1,
        "watch_neutral": 2,
        "blocked_low_movement": 3,
        "blocked_low_sample": 4,
    }
    return sorted(
        out,
        key=lambda row: (
            status_rank.get(str(row["status"]), 99),
            -float(row.get("hurt_rate_changed") or 0.0),
            float(row.get("mae_delta") or 0.0),
            str(row["component"]),
            str(row["group"]),
        ),
    )


def _print_summary(rows: list[dict[str, Any]], *, top: int) -> None:
    status_counts = Counter(str(row["status"]) for row in rows)
    print(
        "status_counts="
        + ",".join(f"{status}:{count}" for status, count in sorted(status_counts.items()))
    )
    for row in rows[:top]:
        print(
            " ".join(
                (
                    f"{row['group_field']}={row['group']}",
                    f"component={row['component']}",
                    f"policy={row['movement_policy']}",
                    f"status={row['status']}",
                    f"n={row['n']}",
                    f"sessions={row['sessions']}",
                    f"changed={row['changed_rows']}",
                    f"helped={row['helped_changed']}",
                    f"hurt={row['hurt_changed']}",
                    f"hurt_rate={row['hurt_rate_changed']}",
                    f"directional_error={row['directional_error_rate_changed']}",
                    f"move_up={row['changed_move_up_rows']}",
                    f"move_down={row['changed_move_down_rows']}",
                    f"baseline_under={row['baseline_under_rate']}",
                    f"pred_delta={row['prediction_delta_changed_mean']}",
                    f"mae_delta={row['mae_delta']}",
                    f"ccv_rate={row['ccv_likelihood_rate']}",
                    f"public_total={row['public_total_rate']}",
                    f"q6_floor={row['q6_floor_rate']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit directional correctness of v3 CCV p50 movements.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--group-field", default="map_id")
    parser.add_argument(
        "--candidate-prefix",
        default="v3_ccv_",
        help="Candidate flat-field prefix, for example v3_ccv_ or v3_ccvc_.",
    )
    parser.add_argument(
        "--ccv-component-freeze-cells",
        action="store_true",
        help="When using v3_ccvc_, keep q6 cells at the baseline posterior.",
    )
    parser.add_argument(
        "--component",
        action="append",
        choices=tuple(COMPONENT_FIELDS),
        help="Component to audit. Can be passed more than once.",
    )
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-changed", type=int, default=5)
    parser.add_argument("--max-hurt-rate", type=float, default=0.45)
    parser.add_argument("--max-directional-error-rate", type=float, default=0.35)
    parser.add_argument(
        "--movement-policy",
        choices=MOVEMENT_POLICIES,
        default="all",
        help="How to apply candidate p50 movement before profile auditing.",
    )
    parser.add_argument("--top", type=int, default=30)
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
        ccv_options=V3CcvOptions(
            component_likelihood=args.candidate_prefix == "v3_ccvc_",
            component_move_cells=not args.ccv_component_freeze_cells,
        ),
    )
    result = {
        "errors": errors,
        "group_field": args.group_field,
        "components": tuple(args.component or DEFAULT_COMPONENTS),
        "direction": summarize_direction(
            rows,
            group_field=args.group_field,
            components=args.component or DEFAULT_COMPONENTS,
            min_windows=args.min_windows,
            min_sessions=args.min_sessions,
            min_changed=args.min_changed,
            max_hurt_rate=args.max_hurt_rate,
            max_directional_error_rate=args.max_directional_error_rate,
            candidate_prefix=args.candidate_prefix,
            movement_policy=args.movement_policy,
        ),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_summary(result["direction"], top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
