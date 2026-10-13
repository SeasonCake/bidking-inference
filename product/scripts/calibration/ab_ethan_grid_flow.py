"""Paired, cache-isolated A/B sweep for Ethan's cell-centric tool chain.

The parent process launches every arm in a clean Python subprocess.  This is
intentional: several engine helpers use environment-controlled behavior and
``lru_cache``, so switching env vars inside one process is not valid A/B
instrumentation.

The sample population is the tracked ``clean_eval_ethan.json`` manifest.  Each
candidate is rechecked with the current strict truth gate in memory; this tool
does not rebuild E07 or write sample registries.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "sample_manifests" / "clean_eval_ethan.json"

ACTION_IDS = {
    "q1_scan": 100104,
    "q3_scan": 100105,
    "q4_scan": 100106,
    "q5_scan": 100107,
    "q4_avg": 100112,
    "q5_avg": 100113,
    "q4_value": 100124,
    "q5_value": 100125,
}

ARMS: dict[str, dict[str, str]] = {
    "base": {},
    "lift_off": {"ETHAN_DISABLE_RED_RESID_LIFT": "1"},
    "occupancy_off": {"ETHAN_DISABLE_OCCUPANCY_GRID": "1"},
    "value_floor_off": {"ETHAN_DISABLE_VALUE_CELL_FLOOR": "1"},
    "revalue_on": {"ETHAN_ENABLE_RED_CELL_RESIDUAL_REVALUE": "1"},
    "q4mid_on": {"ETHAN_ENABLE_Q4_MID_RESID": "1"},
    "q4mid_probe": {
        "ETHAN_ENABLE_Q4_MID_RESID": "1",
        "ETHAN_Q4_MID_NO_REDGATE": "1",
    },
    "q5_scan_cap_legacy": {
        "ETHAN_DISABLE_RED_RESID_LIFT_Q5_SCAN_CAP": "1"
    },
    "q5_scan_cap_105": {"ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP": "1.05"},
    "q5_scan_cap_115": {"ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP": "1.15"},
}
CONTROLLED_ENVS = frozenset(key for values in ARMS.values() for key in values)


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _triplet(value: Any) -> tuple[float | None, float | None, float | None]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None, None, None
    return _number(value[0]), _number(value[1]), _number(value[2])


def _error_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [
        float(row["error_pct"])
        for row in rows
        if _number(row.get("error_pct")) is not None
    ]
    if not errors:
        return {"n": 0}
    return {
        "n": len(errors),
        "mean_pct": round(statistics.mean(errors), 3),
        "median_pct": round(statistics.median(errors), 3),
        "mae_pct": round(statistics.mean(abs(value) for value in errors), 3),
        "over_20": sum(value > 20.0 for value in errors),
        "under_minus_20": sum(value < -20.0 for value in errors),
    }


def _action_ids(events: Any) -> frozenset[int]:
    return frozenset(
        int(action.action_id)
        for state in events.states
        for action in (getattr(state, "action_results", ()) or ())
        if getattr(action, "action_id", None)
    )


def _complete_grid_flow(action_ids: frozenset[int]) -> bool:
    has_q4 = bool(
        action_ids
        & {
            ACTION_IDS["q4_scan"],
            ACTION_IDS["q4_avg"],
            ACTION_IDS["q4_value"],
        }
    )
    has_q5 = bool(
        action_ids
        & {
            ACTION_IDS["q5_scan"],
            ACTION_IDS["q5_avg"],
            ACTION_IDS["q5_value"],
        }
    )
    return (
        ACTION_IDS["q1_scan"] in action_ids
        and ACTION_IDS["q3_scan"] in action_ids
        and has_q4
        and has_q5
    )


def _quality_action(action_ids: frozenset[int], quality: str) -> str:
    for suffix in ("scan", "avg", "value"):
        key = f"{quality}_{suffix}"
        if ACTION_IDS[key] in action_ids:
            return suffix
    return "none"


def _worker(
    arm: str,
    scope: str,
    limit: int,
    file_names: tuple[str, ...],
) -> dict[str, Any]:
    for relative in (
        "scripts",
        "scripts/calibration",
        "hero_ref/src",
        "hero_ref/tools",
        "src",
        ".",
    ):
        value = str(ROOT / relative)
        if value not in sys.path:
            sys.path.insert(0, value)

    from ahmad_ref_engine import run_reference_engine
    from audit_aisha_gap import (
        _build_snapshot,
        _events_through_sort,
        _map_id_from_events,
        _prefix_sort_id,
        _settlement_breakdown,
    )
    from bidking_lab.live.fatbeans import (
        live_batches_from_fatbeans_events,
        parse_fatbeans_capture,
    )
    from bidking_lab.live.monitor import _action_round, load_monitor_tables
    from hero_ref_live_schedule import hero_max_combos_for_round
    from sample_truth_gate import validate_events

    files = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if file_names:
        requested = set(file_names)
        files = [path for path in files if Path(path).name in requested]
        found = {Path(path).name for path in files}
        missing = sorted(requested - found)
        if missing:
            raise ValueError(f"requested files not in manifest: {missing}")
    if limit > 0:
        files = files[:limit]
    tables = load_monitor_tables()
    rows: list[dict[str, Any]] = []
    invalid: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    started = time.perf_counter()

    for index, relative_path in enumerate(files, 1):
        path = ROOT / relative_path
        events = parse_fatbeans_capture(path)
        gate = validate_events(events, tables=tables)
        if gate.get("verdict") != "valid":
            reasons = gate.get("reasons") or ("unknown_invalid",)
            invalid.update(str(reason) for reason in reasons)
            continue

        action_round = int(_action_round(events) or 0)
        if action_round < 3:
            skipped[f"round_{action_round}"] += 1
            continue
        action_ids = _action_ids(events)
        complete = _complete_grid_flow(action_ids)
        if scope == "complete" and not complete:
            skipped["incomplete_grid_flow"] += 1
            continue

        settlement = _settlement_breakdown(events)
        if not settlement:
            invalid["no_normalized_settlement"] += 1
            continue
        pre_batches = [
            batch
            for batch in live_batches_from_fatbeans_events(events)
            if batch.phase != "settled"
        ]
        sort_id = _prefix_sort_id(pre_batches)
        prefix_events = _events_through_sort(events, sort_id) if sort_id else events
        map_id = _map_id_from_events(events)
        snapshot = _build_snapshot(
            hero="ethan",
            events=prefix_events,
            prefix_batches=pre_batches,
            map_id=map_id,
        )
        snapshot_round = int(
            snapshot["ui_contract"]["context"].get("round") or action_round
        )
        max_combos = hero_max_combos_for_round("ethan", min(snapshot_round, 5))
        result = run_reference_engine(snapshot, max_combos=max_combos).as_dict()

        truth_counts = settlement.get("counts") or {}
        truth_cells = settlement.get("cells") or {}
        truth_value = int(settlement.get("total_value") or 0)
        balanced = _number(result.get("balanced"))
        notes = tuple(str(note) for note in (result.get("notes") or ()))
        evidence = result.get("evidence") or {}
        source_notes = tuple(
            str(note) for note in (evidence.get("source_notes") or ())
        )
        grid_range = _triplet(result.get("total_grid_range"))
        cell_ranges = result.get("quality_cells_ranges") or {}
        q4_range = _triplet(cell_ranges.get("q4"))
        q5_range = _triplet(cell_ranges.get("q5"))
        q6_range = _triplet(cell_ranges.get("q6"))
        truth_total_cells = int(settlement.get("total_cells") or 0)

        rows.append(
            {
                "file": path.name,
                "map_id": map_id,
                "round": snapshot_round,
                "action_ids": sorted(action_ids),
                "complete_grid_flow": complete,
                "q4_action": _quality_action(action_ids, "q4"),
                "q5_action": _quality_action(action_ids, "q5"),
                "truth_red_count": int(truth_counts.get(6, 0) or 0),
                "truth_red_cells": int(truth_cells.get(6, 0) or 0),
                "truth_total_cells": truth_total_cells,
                "truth_value": truth_value,
                "status": result.get("status"),
                "combo_count": result.get("combo_count"),
                "balanced": int(round(balanced)) if balanced is not None else None,
                "error_pct": (
                    100.0 * (balanced - truth_value) / truth_value
                    if balanced is not None and truth_value > 0
                    else None
                ),
                "grid_mid_error": (
                    grid_range[1] - truth_total_cells
                    if grid_range[1] is not None
                    else None
                ),
                "q4_mid_error": (
                    q4_range[1] - int(truth_cells.get(4, 0) or 0)
                    if q4_range[1] is not None
                    else None
                ),
                "q5_mid_error": (
                    q5_range[1] - int(truth_cells.get(5, 0) or 0)
                    if q5_range[1] is not None
                    else None
                ),
                "q6_mid_error": (
                    q6_range[1] - int(truth_cells.get(6, 0) or 0)
                    if q6_range[1] is not None
                    else None
                ),
                "notes": list(notes),
                "path_flags": {
                    "occupancy": "ethan_occupancy_grid_estimate" in source_notes,
                    "full_outline": "ethan_skill_full_outline_cells" in source_notes,
                    "value_cell_floor": any(
                        "ethan_q4_value_cell_floor:" in note
                        or "ethan_q5_value_cell_floor:" in note
                        for note in notes
                    ),
                    "red_resid_lift": any(
                        "ethan_red_resid_lift:" in note for note in notes
                    ),
                    "red_residual_revalue": any(
                        "ethan_red_residual_revalue:" in note for note in notes
                    ),
                    "q4_mid_resid": any(
                        "ethan_q4_mid_resid:" in note for note in notes
                    ),
                    "q5_scan_lift_cap": any(
                        "ethan_red_resid_lift_q5_scan_cap:" in note
                        for note in notes
                    ),
                },
            }
        )
        if index % 10 == 0:
            print(
                f"[{arm}] scanned={index}/{len(files)} selected={len(rows)} "
                f"elapsed={time.perf_counter() - started:.1f}s",
                file=sys.stderr,
                flush=True,
            )

    return {
        "arm": arm,
        "env": ARMS[arm],
        "scope": scope,
        "manifest_count": len(files),
        "invalid": dict(invalid),
        "skipped": dict(skipped),
        "runtime_s": round(time.perf_counter() - started, 3),
        "rows": rows,
    }


def _row_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("status"),
        row.get("combo_count"),
        row.get("balanced"),
        row.get("grid_mid_error"),
        row.get("q4_mid_error"),
        row.get("q5_mid_error"),
        row.get("q6_mid_error"),
    )


def _path_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "occupancy",
        "full_outline",
        "value_cell_floor",
        "red_resid_lift",
        "red_residual_revalue",
        "q4_mid_resid",
        "q5_scan_lift_cap",
    )
    return {
        key: sum(bool(row["path_flags"].get(key)) for row in rows) for key in keys
    }


def _paired_summary(
    base_rows: list[dict[str, Any]], arm_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    base_by_file = {row["file"]: row for row in base_rows}
    arm_by_file = {row["file"]: row for row in arm_rows}
    if set(base_by_file) != set(arm_by_file):
        missing = sorted(set(base_by_file) - set(arm_by_file))
        extra = sorted(set(arm_by_file) - set(base_by_file))
        raise RuntimeError(
            f"A/B population mismatch: missing={missing[:3]} extra={extra[:3]}"
        )

    paired = [(base_by_file[name], arm_by_file[name]) for name in sorted(base_by_file)]
    changed = [
        (base, arm)
        for base, arm in paired
        if _row_signature(base) != _row_signature(arm)
    ]
    quote_changed = [
        (base, arm)
        for base, arm in paired
        if base.get("balanced") != arm.get("balanced")
    ]
    improved = 0
    worsened = 0
    tied = 0
    for base, arm in quote_changed:
        base_error = _number(base.get("error_pct"))
        arm_error = _number(arm.get("error_pct"))
        if base_error is None or arm_error is None:
            continue
        delta_abs = abs(arm_error) - abs(base_error)
        if delta_abs < -1e-9:
            improved += 1
        elif delta_abs > 1e-9:
            worsened += 1
        else:
            tied += 1

    def _band(rows: list[dict[str, Any]], red_min: int, red_max: int) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if red_min <= int(row["truth_red_count"]) <= red_max
        ]

    return {
        "changed_any": len(changed),
        "changed_quote": len(quote_changed),
        "paired_improved": improved,
        "paired_worsened": worsened,
        "paired_tied": tied,
        "base": {
            "all": _error_stats(base_rows),
            "red_1_2": _error_stats(_band(base_rows, 1, 2)),
            "red_3_plus": _error_stats(_band(base_rows, 3, 999)),
            "paths": _path_counts(base_rows),
            "statuses": dict(Counter(str(row.get("status")) for row in base_rows)),
        },
        "arm": {
            "all": _error_stats(arm_rows),
            "red_1_2": _error_stats(_band(arm_rows, 1, 2)),
            "red_3_plus": _error_stats(_band(arm_rows, 3, 999)),
            "paths": _path_counts(arm_rows),
            "statuses": dict(Counter(str(row.get("status")) for row in arm_rows)),
        },
        "largest_quote_changes": [
            {
                "file": base["file"],
                "map_id": base["map_id"],
                "truth_red_count": base["truth_red_count"],
                "base_error_pct": round(float(base["error_pct"]), 3),
                "arm_error_pct": round(float(arm["error_pct"]), 3),
                "delta_pct": round(
                    float(arm["error_pct"]) - float(base["error_pct"]), 3
                ),
            }
            for base, arm in sorted(
                quote_changed,
                key=lambda pair: abs(
                    float(pair[1]["error_pct"]) - float(pair[0]["error_pct"])
                ),
                reverse=True,
            )[:10]
            if _number(base.get("error_pct")) is not None
            and _number(arm.get("error_pct")) is not None
        ],
    }


def _instrumentation_check(
    arm: str, paired: dict[str, Any]
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    base_paths = paired["base"]["paths"]
    arm_paths = paired["arm"]["paths"]
    if arm == "lift_off":
        if base_paths["red_resid_lift"] <= 0:
            reasons.append("positive_control_missing:base_red_resid_lift")
        if arm_paths["red_resid_lift"] != 0:
            reasons.append("kill_switch_failed:red_resid_lift_still_fired")
    elif arm == "occupancy_off":
        if base_paths["occupancy"] <= 0:
            reasons.append("positive_control_missing:base_occupancy")
        if arm_paths["occupancy"] != 0:
            reasons.append("kill_switch_failed:occupancy_still_fired")
    elif arm == "value_floor_off":
        if base_paths["value_cell_floor"] <= 0:
            reasons.append("positive_control_missing:base_value_cell_floor")
        if arm_paths["value_cell_floor"] != 0:
            reasons.append("kill_switch_failed:value_cell_floor_still_fired")
    elif arm == "revalue_on":
        if arm_paths["red_residual_revalue"] <= 0:
            reasons.append("positive_control_missing:red_residual_revalue")
    elif arm == "q4mid_probe":
        if arm_paths["q4_mid_resid"] <= 0:
            reasons.append("positive_control_missing:q4_mid_resid")
    elif arm == "q5_scan_cap_legacy":
        if base_paths["q5_scan_lift_cap"] <= 0:
            reasons.append("positive_control_missing:base_q5_scan_lift_cap")
        if arm_paths["q5_scan_lift_cap"] != 0:
            reasons.append("kill_switch_failed:q5_scan_lift_cap_still_applied")
    elif arm.startswith("q5_scan_cap_"):
        if arm_paths["q5_scan_lift_cap"] <= 0:
            reasons.append("positive_control_missing:q5_scan_lift_cap")
    if arm not in {"q4mid_on", "q4mid_probe"} and paired["changed_any"] <= 0:
        reasons.append("instrumentation_alarm:all_outputs_identical")
    return not reasons, reasons


def _arm_environment(arm: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in CONTROLLED_ENVS:
        env.pop(key, None)
    env.update(ARMS[arm])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_arm_subprocess(arm: str, args: argparse.Namespace) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-arm",
        arm,
        "--scope",
        args.scope,
        "--limit",
        str(args.limit),
    ]
    for file_name in args.file:
        command.extend(("--file", file_name))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_arm_environment(arm),
        check=False,
        capture_output=True,
        text=True,
        timeout=args.arm_timeout,
    )
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        raise RuntimeError(
            f"arm {arm} failed with {completed.returncode}: {completed.stderr[-1000:]}"
        )
    return json.loads(completed.stdout)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arms",
        default=",".join(ARMS),
        help=f"Comma-separated arms. Known: {','.join(ARMS)}",
    )
    parser.add_argument(
        "--scope",
        choices=("complete", "r3plus"),
        default="complete",
        help="Complete tool-chain subset or every decision round >=3.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit manifest candidates before qualification; 0 means all.",
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="Restrict to an exact sample basename; repeat for multiple samples.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="Number of clean arm subprocesses to run concurrently.",
    )
    parser.add_argument(
        "--arm-timeout",
        type=int,
        default=600,
        help="Hard wall-clock timeout per arm subprocess in seconds.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print paired summaries only; --output still receives the full report.",
    )
    parser.add_argument("--worker-arm", choices=tuple(ARMS), help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.worker_arm:
        print(
            json.dumps(
                _worker(
                    args.worker_arm,
                    args.scope,
                    args.limit,
                    tuple(args.file),
                )
            )
        )
        return 0

    arms = [part.strip() for part in args.arms.split(",") if part.strip()]
    unknown = sorted(set(arms) - set(ARMS))
    if unknown:
        raise SystemExit(f"unknown arms: {', '.join(unknown)}")
    if "base" not in arms:
        arms.insert(0, "base")

    started = time.perf_counter()
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        pending = {
            executor.submit(_run_arm_subprocess, arm, args): arm for arm in arms
        }
        for future in as_completed(pending):
            arm = pending[future]
            results[arm] = future.result()
            print(
                f"arm={arm} rows={len(results[arm]['rows'])} "
                f"runtime={results[arm]['runtime_s']}s",
                file=sys.stderr,
                flush=True,
            )

    base_rows = results["base"]["rows"]
    paired: dict[str, Any] = {}
    instrumentation_ok = True
    instrumentation_reasons: dict[str, list[str]] = {}
    for arm in arms:
        if arm == "base":
            continue
        comparison = _paired_summary(base_rows, results[arm]["rows"])
        ok, reasons = _instrumentation_check(arm, comparison)
        comparison["instrumentation_ok"] = ok
        comparison["instrumentation_reasons"] = reasons
        paired[arm] = comparison
        if not ok:
            instrumentation_ok = False
            instrumentation_reasons[arm] = reasons

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    report = {
        "schema": "ethan_grid_flow_ab_v1",
        "head": head,
        "manifest": str(MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "scope": args.scope,
        "arms": {arm: ARMS[arm] for arm in arms},
        "clean_process_per_arm": True,
        "instrumentation_ok": instrumentation_ok,
        "instrumentation_reasons": instrumentation_reasons,
        "wall_clock_s": round(time.perf_counter() - started, 3),
        "arm_results": results,
        "paired": paired,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output
        if not output.is_absolute():
            output = ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
        print(f"report={output}", file=sys.stderr)
    if args.summary_only:
        display_report = {
            key: value for key, value in report.items() if key != "arm_results"
        }
        print(json.dumps(display_report, ensure_ascii=False, indent=2))
    else:
        print(encoded)
    return 0 if instrumentation_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
