"""Export compact Fatbeans sample views for manual review.

The raw Fatbeans captures are packet dumps and are too large/noisy for routine
semantic review. This script writes one compact JSONL timeline per capture plus
a summary CSV, preserving public-info/action/skill/inventory facts that matter
for inference debugging.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansObservedItem,
    latest_player_bids,
    parse_fatbeans_capture,
)

DEFAULT_OUT_DIR = ROOT / "data" / "review" / "fatbeans_compact"


def _expand_paths(raw_paths: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in raw_paths:
        matches = sorted(glob.glob(raw)) or [raw]
        for match in matches:
            path = Path(match)
            if path.is_dir():
                paths.extend(sorted(path.glob("*.json")))
            else:
                paths.append(path)
    return paths


def _default_paths() -> list[Path]:
    root = ROOT / "data" / "samples" / "fatbeans"
    return sorted(root.glob("*.json")) if root.exists() else []


def _format_counter(counter: Counter[Any]) -> str:
    return ";".join(
        f"{key}:{count}"
        for key, count in sorted(counter.items(), key=lambda item: str(item[0]))
        if count
    )


def _shape_cells(shape_code: int | None) -> int | None:
    if shape_code is None:
        return None
    width = int(shape_code) // 10
    height = int(shape_code) % 10
    if width <= 0 or height <= 0:
        return None
    return width * height


def _item_payload(item: FatbeansObservedItem) -> dict[str, Any]:
    cells = item.cells
    if cells is None:
        cells = _shape_cells(item.shape_code)
    return {
        "local": item.local_index,
        "runtime": item.runtime_id,
        "item_id": item.item_id,
        "quality": item.quality,
        "value": item.value,
        "shape": item.shape_code,
        "cells": cells,
    }


def _quality_counters(items: Iterable[Any]) -> tuple[Counter[int], Counter[int]]:
    counts: Counter[int] = Counter()
    cells: Counter[int] = Counter()
    for item in items:
        quality = getattr(item, "quality", None)
        if quality is None:
            continue
        quality = int(quality)
        counts[quality] += 1
        item_cells = getattr(item, "cells", None)
        if item_cells is not None:
            cells[quality] += int(item_cells)
    return counts, cells


def _state_payload(state: Any) -> dict[str, Any]:
    public_infos = [
        {
            "id": info.info_id,
            "map_id": info.map_id,
            "value": info.value,
            "value_field": info.value_field,
            "items": [_item_payload(item) for item in info.observed_items],
        }
        for info in state.public_infos
    ]
    actions = [
        {
            "id": result.action_id,
            "result": result.result,
            "result_field": result.result_field,
            "items": [_item_payload(item) for item in result.observed_items],
        }
        for result in state.action_results
    ]
    skills = [
        {
            "id": reveal.skill_id,
            "hero_id": reveal.hero_id,
            "round": reveal.round_index,
            "items": [_item_payload(item) for item in reveal.observed_items],
        }
        for reveal in state.skill_reveals
    ]
    quality_counts, quality_cells = _quality_counters(state.inventory_items)
    payload = {
        "type": "state",
        "sort_id": state.sort_id,
        "capture_time": state.capture_time,
        "message_id": f"0x{state.message_id:04x}",
        "phase": "settled" if state.message_id == 0x002D else "bidding",
        "map_id": state.map_id,
        "round": state.round_index,
        "session_id": state.session_id,
        "public_infos": public_infos,
        "actions": actions,
        "skills": skills,
        "bids": [
            {
                "player_id": bid.player_id,
                "name": bid.name,
                "hero_id": bid.hero_id,
                "values": list(bid.values),
                "current": bid.current_value,
            }
            for bid in state.bids
        ],
    }
    if state.inventory_items:
        payload["inventory"] = {
            "count": len(state.inventory_items),
            "cells": sum(item.cells for item in state.inventory_items),
            "quality_counts": dict(sorted(quality_counts.items())),
            "quality_cells": dict(sorted(quality_cells.items())),
            "items": [
                {
                    "runtime": item.runtime_id,
                    "item_id": item.item_id,
                    "quality": item.quality,
                    "cells": item.cells,
                }
                for item in state.inventory_items
            ],
        }
    if state.settlement_loss_units is not None:
        payload["settlement_loss_units"] = state.settlement_loss_units
    return payload


def _summary_row(path: Path, events: Any, compact_path: Path) -> dict[str, Any]:
    public_ids: Counter[int] = Counter()
    public_item_counts: Counter[int] = Counter()
    public_values: dict[int, list[str]] = {}
    action_ids: Counter[int] = Counter()
    skill_ids: Counter[int] = Counter()
    map_ids: Counter[int] = Counter()
    rounds: list[int] = []
    final_inventory: tuple[Any, ...] = ()
    for state in events.states:
        if state.map_id is not None:
            map_ids[int(state.map_id)] += 1
        if state.round_index is not None:
            rounds.append(int(state.round_index))
        for info in state.public_infos:
            public_ids[int(info.info_id)] += 1
            if info.observed_items:
                public_item_counts[int(info.info_id)] += len(info.observed_items)
            public_values.setdefault(int(info.info_id), []).append(str(info.value))
        for result in state.action_results:
            action_ids[int(result.action_id)] += 1
        for reveal in state.skill_reveals:
            skill_ids[int(reveal.skill_id)] += 1
        if state.inventory_items:
            final_inventory = state.inventory_items

    final_counts, final_cells = _quality_counters(final_inventory)
    latest_bids = latest_player_bids(events.states)
    return {
        "file": path.name,
        "compact_file": str(compact_path.relative_to(compact_path.parents[1])),
        "packets": len(events.packets),
        "frames": len(events.frames),
        "states": len(events.states),
        "public_info_ids": _format_counter(public_ids),
        "public_info_item_counts": _format_counter(public_item_counts),
        "public_info_values": ";".join(
            f"{info_id}={','.join(values[:5])}"
            for info_id, values in sorted(public_values.items())
        ),
        "action_ids": _format_counter(action_ids),
        "skill_ids": _format_counter(skill_ids),
        "map_ids": _format_counter(map_ids),
        "max_round": max(rounds) if rounds else "",
        "latest_bid_max": max(latest_bids.values()) if latest_bids else "",
        "inventory_items": len(final_inventory),
        "inventory_cells": sum(item.cells for item in final_inventory),
        "final_quality_counts": _format_counter(final_counts),
        "final_quality_cells": _format_counter(final_cells),
    }


def export_capture(path: Path, out_dir: Path) -> dict[str, Any]:
    events = parse_fatbeans_capture(path)
    events_dir = out_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    compact_path = events_dir / f"{path.stem}.jsonl"
    with compact_path.open("w", encoding="utf-8", newline="\n") as fh:
        for state in events.states:
            if not (
                state.public_infos
                or state.action_results
                or state.skill_reveals
                or state.bids
                or state.inventory_items
            ):
                continue
            fh.write(json.dumps(_state_payload(state), ensure_ascii=False) + "\n")
    return _summary_row(path, events, compact_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write compact Fatbeans review JSONL files and summary CSV.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Fatbeans JSON files or directories. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()

    paths = _expand_paths(args.paths) if args.paths else _default_paths()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in paths:
        try:
            rows.append(export_capture(path, out_dir))
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)})

    if rows:
        summary_path = out_dir / "summary.csv"
        fieldnames = sorted({key for row in rows for key in row})
        with summary_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    if errors:
        error_path = out_dir / "errors.jsonl"
        with error_path.open("w", encoding="utf-8", newline="\n") as fh:
            for error in errors:
                fh.write(json.dumps(error, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "exported": len(rows),
                "errors": len(errors),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
