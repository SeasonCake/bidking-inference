"""Fresh-process A/B for the hotfix1 Ivan/Carlos skill-footprint adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for sub in ("src", "scripts", "hero_ref/src", "hero_ref/tools"):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from bidking_lab.extract.item_table import load_item_table  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import (  # noqa: E402
    build_monitor_artifact_from_events,
    load_monitor_tables,
)

DEFAULT_IVAN = (
    ROOT
    / "data/logs/live/raw/archive/reset"
    / "wss_live_2026-07-23_052231_2406_1758737576637583_reset.json"
)
DEFAULT_CARLOS = (
    "wss_live_2026-07-23_052853_2407_1758737576848169_reset.json",
    "wss_live_2026-07-23_053057_2406_1758737576917614_reset.json",
    "wss_live_2026-07-23_053346_2408_1758737577012571_reset.json",
    "wss_live_2026-07-23_053448_2401_1758737577047382_reset.json",
)
SWITCHES = (
    "BIDKING_DISABLE_IVAN_SAME_RUNTIME_FOOTPRINT",
)
QUALITY_KEYS = ("q1", "q3", "q4", "q5", "q6")


def _truncate(events: FatbeansCaptureEvents, cut: int) -> FatbeansCaptureEvents:
    def keep(rows):
        return tuple(row for row in rows if int(row.sort_id) < cut)

    return FatbeansCaptureEvents(
        packets=keep(events.packets),
        frames=keep(events.frames),
        sends=keep(events.sends),
        states=keep(events.states),
        statuses=keep(events.statuses),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _truth(events: FatbeansCaptureEvents) -> dict[str, Any]:
    settlement = next(
        state for state in reversed(events.states) if state.inventory_items
    )
    catalog = load_item_table(ROOT / "data/raw/tables/Item.txt")
    counts: dict[int, int] = defaultdict(int)
    cells: dict[int, int] = defaultdict(int)
    values: dict[int, int] = defaultdict(int)
    for item in settlement.inventory_items:
        quality = int(item.quality or 0)
        counts[quality] += 1
        cells[quality] += int(item.cells or 0)
        catalog_item = catalog.get(int(item.item_id or 0))
        if catalog_item is not None:
            values[quality] += int(catalog_item.value)
    merged_counts = {
        "q1": counts[1] + counts[2],
        "q3": counts[3],
        "q4": counts[4],
        "q5": counts[5],
        "q6": counts[6],
    }
    merged_cells = {
        "q1": cells[1] + cells[2],
        "q3": cells[3],
        "q4": cells[4],
        "q5": cells[5],
        "q6": cells[6],
    }
    return {
        "total_items": sum(counts.values()),
        "total_cells": sum(cells.values()),
        "total_value": sum(values.values()),
        "counts": merged_counts,
        "cells": merged_cells,
        "values": {
            "q1": values[1] + values[2],
            "q3": values[3],
            "q4": values[4],
            "q5": values[5],
            "q6": values[6],
        },
    }


def _run_sample(
    hero: str,
    path: Path,
    *,
    max_combos: int,
) -> dict[str, Any]:
    events = parse_fatbeans_capture(path)
    truth = _truth(events)
    bids = sorted(
        (row for row in events.sends if getattr(row, "kind", "") == "bid"),
        key=lambda row: int(row.sort_id),
    )
    tables = load_monitor_tables()
    rounds: list[dict[str, Any]] = []
    for round_no, bid in enumerate(bids, 1):
        artifact = build_monitor_artifact_from_events(
            _truncate(events, int(bid.sort_id)),
            file=path.name,
            tables=tables,
            n_trials=1,
            roi_trials=0,
            run_debug_shadows=False,
        )
        result = run_reference_engine(
            prepare_reference_engine_snapshot(artifact),
            max_combos=max_combos,
        ).as_dict()
        count_mids = {
            key: int((result.get("quality_count_ranges", {}).get(key) or [0, 0, 0])[1])
            for key in QUALITY_KEYS
        }
        rounds.append(
            {
                "round": round_no,
                "cut_sort": int(bid.sort_id),
                "status": result.get("status"),
                "quotes": {
                    key: result.get(key)
                    for key in ("conservative", "balanced", "aggressive")
                },
                "balanced_abs_error": abs(
                    int(result.get("balanced") or 0) - int(truth["total_value"])
                ),
                "count_mids": count_mids,
                "count_l1_error": sum(
                    abs(count_mids[key] - int(truth["counts"][key]))
                    for key in QUALITY_KEYS
                ),
                "quality_count_ranges": result.get("quality_count_ranges"),
                "quality_cells_ranges": result.get("quality_cells_ranges"),
                "quality_cell_floors": (
                    result.get("evidence", {}).get("quality_cell_floors")
                ),
                "quality_cell_floor_item_counts": (
                    result.get("evidence", {}).get(
                        "quality_cell_floor_item_counts"
                    )
                ),
                "footprint_notes": [
                    note
                    for note in result.get("notes") or ()
                    if str(note).startswith("hero_skill_footprint:")
                ],
            }
        )
    return {
        "hero": hero,
        "path": str(path),
        "sha256": _sha256(path),
        "truth": truth,
        "rounds": rounds,
    }


def _run_arm(args: argparse.Namespace) -> dict[str, Any]:
    for switch in SWITCHES:
        if args.arm == "rollback":
            os.environ[switch] = "1"
        else:
            os.environ.pop(switch, None)
    return {
        "arm": args.arm,
        "max_combos": args.max_combos,
        "samples": [
            _run_sample("ivan", args.ivan, max_combos=args.max_combos),
            *[
                _run_sample("carlos", path, max_combos=args.max_combos)
                for path in args.carlos
            ],
        ],
    }


def _run_fresh_process(args: argparse.Namespace, arm: str) -> dict[str, Any]:
    command = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "--arm",
        arm,
        "--max-combos",
        str(args.max_combos),
        "--ivan",
        str(args.ivan),
    ]
    for path in args.carlos:
        command.extend(("--carlos", str(path)))
    env = os.environ.copy()
    for switch in SWITCHES:
        env.pop(switch, None)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("default", "rollback"))
    parser.add_argument("--max-combos", type=int, default=8_000)
    parser.add_argument("--ivan", type=Path, default=DEFAULT_IVAN)
    parser.add_argument("--carlos", type=Path, action="append")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.carlos:
        root = ROOT / "data/logs/live/raw/archive/reset"
        args.carlos = [root / name for name in DEFAULT_CARLOS]
    return args


def main() -> int:
    args = _parse_args()
    missing = [
        str(path)
        for path in (args.ivan, *args.carlos)
        if not path.is_file()
    ]
    if missing:
        raise SystemExit(f"missing sample(s): {', '.join(missing)}")
    payload = (
        _run_arm(args)
        if args.arm
        else {
            "schema_version": 1,
            "fresh_process": True,
            "default": _run_fresh_process(args, "default"),
            "rollback": _run_fresh_process(args, "rollback"),
        }
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
