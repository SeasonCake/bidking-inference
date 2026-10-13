"""Cache-isolated A/B for Ahmed's non-official reachable-zero-red quote cap.

The parent launches default and rollback in separate Python processes.  Every pool uses the
same last pre-settlement Ahmed snapshot builder and the current normalized settlement truth.
The tracked clean manifest is forced to an official-eligible source contract; quarantined
unknown and confirmed player-custom captures stay in distinct pools.

This tool is read-only with respect to sample registries and E07.  Its only write is the
explicit JSON report path supplied by the caller.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
OFFICIAL_MANIFEST = ROOT / "data" / "sample_manifests" / "clean_eval_ahmed.json"
UNKNOWN_DIR = ROOT / "data" / "samples" / "postupdate_unknown_warehouse_source"
PLAYER_DIR = ROOT / "data" / "samples" / "player_custom_warehouse"
ROLLBACK_ENV = "BIDKING_DISABLE_AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP"
CAP_NOTE_PREFIX = "ahmed_nonofficial_zero_red_quote_cap:"

SOURCE_CONTRACTS = {
    "official": {
        "qualification": "official_normal_warehouse",
        "official_distribution_eligible": True,
        "clean_eval_eligible": True,
        "quote_policy": "normal",
    },
    "unknown": {
        "qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
        "clean_eval_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    },
    "player": {
        "qualification": "player_custom_warehouse",
        "official_distribution_eligible": False,
        "clean_eval_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    },
}


def _pool_paths(pool: str) -> list[Path]:
    if pool == "official":
        relative = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
        return [ROOT / str(path) for path in relative]
    directory = UNKNOWN_DIR if pool == "unknown" else PLAYER_DIR
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def _inject_source_contract(snapshot: dict[str, Any], pool: str) -> dict[str, Any]:
    payload = deepcopy(snapshot)
    contract = deepcopy(SOURCE_CONTRACTS[pool])
    payload["warehouse_source"] = deepcopy(contract)
    payload["warehouse_source_qualification"] = contract["qualification"]
    ui_contract = payload.setdefault("ui_contract", {})
    context = ui_contract.setdefault("context", {})
    source = ui_contract.setdefault("source", {})
    context["warehouse_source"] = deepcopy(contract)
    context["warehouse_source_qualification"] = contract["qualification"]
    source["warehouse_source"] = deepcopy(contract)
    source["warehouse_source_qualification"] = contract["qualification"]
    return payload


def _triplet(value: Any) -> tuple[int | None, int | None, int | None]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None, None, None
    out: list[int | None] = []
    for part in value:
        try:
            out.append(int(part) if part is not None else None)
        except (TypeError, ValueError, OverflowError):
            out.append(None)
    return out[0], out[1], out[2]


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [
        float(row["error_pct"])
        for row in rows
        if isinstance(row.get("error_pct"), (int, float))
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


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "all": _metric(rows),
        "truth_red_0_1": _metric(
            [row for row in rows if int(row.get("truth_red_count") or 0) <= 1]
        ),
        "truth_red_2_plus": _metric(
            [row for row in rows if int(row.get("truth_red_count") or 0) >= 2]
        ),
        "correct_red_positive": _metric(
            [
                row
                for row in rows
                if int(row.get("truth_red_count") or 0) >= 2
                and row.get("red_mid") == row.get("truth_red_count")
            ]
        ),
        "correct_q5_lock": _metric(
            [row for row in rows if bool(row.get("correct_q5_lock"))]
        ),
    }


def _reduced_4509(pool: str = "unknown") -> dict[str, Any]:
    snapshot = {
        "hero": "ahmed",
        "map_id": 4509,
        "phase": "bidding",
        "round": 5,
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4509,
                "phase": "bidding",
                "round": 5,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 56,
            "counts": {"q1": 23, "q3": 11},
            "avg_cells": {
                "q1": 2.04347825050354,
                "q3": 2.2727272510528564,
                "q4": 4.833333492279053,
                "q5": 8.199999809265137,
            },
            "quality_values": {"q4": 97_082},
        },
    }
    return _inject_source_contract(snapshot, pool)


def _control_4509(run_reference_engine: Any) -> dict[str, Any]:
    controls: dict[str, Any] = {}
    for pool in ("unknown", "player", "official"):
        by_budget: dict[str, Any] = {}
        for budget in (8_000, 50_000):
            result = run_reference_engine(
                _reduced_4509(pool),
                max_combos=budget,
            ).as_dict()
            by_budget[str(budget)] = {
                "status": result.get("status"),
                "combo_count": result.get("combo_count"),
                "quotes": [
                    result.get("conservative"),
                    result.get("balanced"),
                    result.get("aggressive"),
                ],
                "raw_value_range": [
                    result.get("value_p25"),
                    result.get("value_p50"),
                    result.get("value_p75"),
                ],
                "q5_candidates": result.get("quality_count_candidates", {}).get("q5"),
                "q6_candidates": result.get("quality_count_candidates", {}).get("q6"),
                "q5_range": result.get("quality_count_ranges", {}).get("q5"),
                "q6_range": result.get("quality_count_ranges", {}).get("q6"),
                "red_count_range": result.get("red_count_range"),
                "red_cells_range": result.get("red_cells_range"),
                "total_grid_range": result.get("total_grid_range"),
                "cap_notes": [
                    str(note)
                    for note in result.get("notes") or ()
                    if str(note).startswith(CAP_NOTE_PREFIX)
                ],
            }
        controls[pool] = by_budget
    return controls


def _worker(arm: str, pools: tuple[str, ...], limit: int) -> dict[str, Any]:
    for relative in (
        "scripts",
        "scripts/calibration",
        "hero_ref/src",
        "hero_ref/tools",
        "src",
        ".",
    ):
        path = str(ROOT / relative)
        if path not in sys.path:
            sys.path.insert(0, path)

    os.environ.pop(ROLLBACK_ENV, None)
    if arm == "rollback":
        os.environ[ROLLBACK_ENV] = "1"

    from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine
    from audit_aisha_gap import _map_id_from_events, _settlement_breakdown
    from bidking_lab.live.fatbeans import (
        live_batches_from_fatbeans_events,
        parse_fatbeans_capture,
    )
    from bidking_lab.live.monitor import load_monitor_tables
    from regenerate_checkpoint_matrices import _snapshot_at_batch
    from sample_truth_gate import validate_events

    tables = load_monitor_tables()
    started = time.perf_counter()
    pool_results: dict[str, Any] = {}
    for pool in pools:
        paths = _pool_paths(pool)
        if limit > 0:
            paths = paths[:limit]
        rows: list[dict[str, Any]] = []
        rejected: Counter[str] = Counter()
        for index, path in enumerate(paths, 1):
            try:
                events = parse_fatbeans_capture(path)
                gate = validate_events(events, tables=tables)
                if gate.get("verdict") != "valid":
                    rejected.update(
                        str(reason)
                        for reason in (gate.get("reasons") or ("strict_truth_invalid",))
                    )
                    continue
                settlement = _settlement_breakdown(events)
                truth_value = int(settlement.get("total_value") or 0)
                if truth_value <= 0:
                    rejected["no_normalized_settlement"] += 1
                    continue
                pre_batches = [
                    batch
                    for batch in live_batches_from_fatbeans_events(events)
                    if batch.phase != "settled"
                ]
                if not pre_batches:
                    rejected["no_pre_settlement_batch"] += 1
                    continue
                map_id = _map_id_from_events(events)
                snapshot = _snapshot_at_batch(
                    events=events,
                    pre_batches=pre_batches,
                    batch_no=len(pre_batches),
                    map_id=map_id,
                    hero="ahmed",
                )
                if snapshot is None:
                    rejected["snapshot_build_none"] += 1
                    continue
                snapshot = _inject_source_contract(
                    prepare_reference_engine_snapshot(snapshot),
                    pool,
                )
                result = run_reference_engine(snapshot, max_combos=8_000).as_dict()
                balanced = result.get("balanced")
                if not isinstance(balanced, (int, float)):
                    rejected[f"engine_{result.get('status') or 'unknown'}"] += 1
                    continue
                truth_counts = settlement.get("counts") or {}
                truth_red = int(truth_counts.get(6, 0) or 0)
                truth_q5 = int(truth_counts.get(5, 0) or 0)
                q5_range = _triplet(
                    (result.get("quality_count_ranges") or {}).get("q5")
                )
                red_range = _triplet(result.get("red_count_range"))
                error_pct = 100.0 * (float(balanced) - truth_value) / truth_value
                notes = tuple(str(note) for note in result.get("notes") or ())
                rows.append(
                    {
                        "file": path.name,
                        "map_id": int(map_id) if map_id is not None else None,
                        "truth_value": truth_value,
                        "truth_red_count": truth_red,
                        "truth_q5_count": truth_q5,
                        "balanced": int(round(float(balanced))),
                        "error_pct": round(error_pct, 6),
                        "status": result.get("status"),
                        "combo_count": result.get("combo_count"),
                        "red_mid": red_range[1],
                        "q5_range": list(q5_range),
                        "correct_q5_lock": (
                            q5_range[0] == q5_range[1] == q5_range[2] == truth_q5
                        ),
                        "cap_fired": any(
                            note.startswith(CAP_NOTE_PREFIX) for note in notes
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - census records failure class
                rejected[f"exception:{type(exc).__name__}"] += 1
            if index % 100 == 0:
                print(
                    f"[{arm}:{pool}] {index}/{len(paths)} selected={len(rows)} "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
        pool_results[pool] = {
            "input_count": len(paths),
            "selected_count": len(rows),
            "rejected": dict(rejected),
            "cap_fired": sum(bool(row["cap_fired"]) for row in rows),
            "metrics": _metrics(rows),
            "guard_maps": {
                str(map_id): [
                    row
                    for row in rows
                    if row.get("map_id") == map_id
                ]
                for map_id in (2549, 2502, 2509, 4501)
            },
            "rows": rows,
        }
    return {
        "arm": arm,
        "rollback_env": os.environ.get(ROLLBACK_ENV),
        "max_combos": 8_000,
        "runtime_s": round(time.perf_counter() - started, 3),
        "pools": pool_results,
        "control_4509": _control_4509(run_reference_engine),
    }


def _compare(default: dict[str, Any], rollback: dict[str, Any]) -> dict[str, Any]:
    pools: dict[str, Any] = {}
    for pool in default["pools"]:
        left = {
            row["file"]: row for row in default["pools"][pool].get("rows") or ()
        }
        right = {
            row["file"]: row for row in rollback["pools"][pool].get("rows") or ()
        }
        shared = sorted(left.keys() & right.keys())
        changed = [
            name
            for name in shared
            if left[name] != right[name]
        ]
        pools[pool] = {
            "default_count": len(left),
            "rollback_count": len(right),
            "shared_count": len(shared),
            "default_only": sorted(left.keys() - right.keys()),
            "rollback_only": sorted(right.keys() - left.keys()),
            "changed_count": len(changed),
            "changed_files": changed,
        }
    return {
        "pools": pools,
        "control_4509_unknown_changed": (
            default["control_4509"]["unknown"]["8000"]
            != rollback["control_4509"]["unknown"]["8000"]
        ),
        "control_4509_official_identical": (
            default["control_4509"]["official"]["8000"]
            == rollback["control_4509"]["official"]["8000"]
        ),
        "control_4509_product_audit_identical": {
            arm: result["control_4509"]["unknown"]["8000"]
            == result["control_4509"]["unknown"]["50000"]
            for arm, result in (("default", default), ("rollback", rollback))
        },
    }


def _run_subprocess(
    arm: str,
    pools: tuple[str, ...],
    limit: int,
    output: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-B",
        str(SCRIPT),
        "--worker",
        arm,
        "--pools",
        ",".join(pools),
        "--limit",
        str(limit),
        "--output",
        str(output),
    ]
    env = os.environ.copy()
    env.pop(ROLLBACK_ENV, None)
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{arm} worker exited {completed.returncode}")
    return json.loads(output.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("default", "rollback"))
    parser.add_argument("--pools", default="official,unknown,player")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "scratch" / "ahmed_nonofficial_zero_red_quote_cap_ab.json",
    )
    args = parser.parse_args()
    pools = tuple(
        pool.strip()
        for pool in args.pools.split(",")
        if pool.strip() in SOURCE_CONTRACTS
    )
    if args.worker:
        result = _worker(args.worker, pools, args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    arm_paths = {
        arm: args.output.with_name(f"{args.output.stem}.{arm}{args.output.suffix}")
        for arm in ("default", "rollback")
    }
    arms: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                _run_subprocess,
                arm,
                pools,
                args.limit,
                arm_paths[arm],
            ): arm
            for arm in ("default", "rollback")
        }
        for future in as_completed(futures):
            arm = futures[future]
            arms[arm] = future.result()
    report = {
        "schema": "ahmed_nonofficial_zero_red_quote_cap_ab_v1",
        "generated_at": time.time(),
        "rollback_env": ROLLBACK_ENV,
        "pools": list(pools),
        "limit": args.limit,
        "arms": arms,
        "comparison": _compare(arms["default"], arms["rollback"]),
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "output": str(args.output),
            "runtime_s": {
                arm: arms[arm]["runtime_s"] for arm in ("default", "rollback")
            },
            "comparison": report["comparison"],
            "metrics": {
                arm: {
                    pool: arms[arm]["pools"][pool]["metrics"]
                    for pool in pools
                }
                for arm in ("default", "rollback")
            },
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
