"""Independent visibility simulator using only explicitly fictional inputs.

Mechanism references: docs/research/PROVENANCE.json, aisha-synthetic-visibility.
No product parser, catalogue, capture, valuation model or calibrated prior is used.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path


SKILL_QUALITY = {1: 1, 2: 2, 3: 3, 4: 4}
DEFAULT_TOOLS = {1: "baoguang4", 2: "inspect2", 3: "inspect1", 4: "goldscan", 5: "none"}
TOOL_DRAWS = {"baoguang4": (4, "quality"), "inspect2": (2, "full"), "inspect1": (1, "full")}
_RANK = {"quality": 1, "outline": 2, "full": 3}


def toy_warehouse() -> dict:
    """Load a fresh copy so caller edits cannot contaminate later examples."""
    path = Path(__file__).with_name("fixtures") / "toy_warehouse.json"
    return json.loads(path.read_text(encoding="utf-8"))


def public_visible_round(map_family: str, wire_round: int) -> int:
    """Teaching form of the July correction; uncalibrated families are rejected.

    Villa: first wave R1, later wave R3. Shipwreck/ranked/hidden: single R1 wave.
    This is the documented historical mapping, not a claim about today's game.
    """
    if type(wire_round) is not int or wire_round < 1:
        raise ValueError("wire_round must be a positive integer")
    if map_family in ("shipwreck", "ranked", "hidden"):
        return 1
    if map_family == "villa":
        return 1 if wire_round <= 1 else 3
    raise ValueError("uncalibrated teaching map family")


def _validate_fixture(fixture: dict) -> None:
    if not isinstance(fixture, dict) or fixture.get("synthetic") is not True:
        raise ValueError("an explicitly synthetic fixture is required")
    if fixture.get("coordinate_system") != "one_based_row_col_rectangles":
        raise ValueError("the teaching fixture uses one-based rectangular footprints")
    items = fixture.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    ids: set[str] = set()
    occupied: set[tuple[int, int]] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("entity"), str) or not item["entity"]:
            raise ValueError("every toy item needs a nonempty entity key")
        if item["entity"] in ids:
            raise ValueError("duplicate entity key")
        ids.add(item["entity"])
        if any(type(item.get(k)) is not int or item[k] < 1 for k in ("quality", "cells", "row", "col", "width", "height")):
            raise ValueError("quality, cells and geometry must be positive integers")
        if item["quality"] > 6 or item["cells"] != item["width"] * item["height"]:
            raise ValueError("invalid quality or rectangular cell count")
        if type(item.get("value")) is not int or item["value"] < 0:
            raise ValueError("value must be a non-negative fictional integer")
        footprint = {
            (r, c)
            for r in range(item["row"], item["row"] + item["height"])
            for c in range(item["col"], item["col"] + item["width"])
        }
        if occupied & footprint:
            raise ValueError("overlapping toy items")
        occupied |= footprint
    public_visible_round(fixture.get("map_family"), 1)
    events = fixture.get("public_events", [])
    if not isinstance(events, list):
        raise ValueError("public_events must be a list")
    supported = {"total_avg_cells": "cells_per_item", "q4_count": "items"}
    for event in events:
        if not isinstance(event, dict) or event.get("semantic") not in supported:
            raise ValueError("unsupported teaching public event")
        public_visible_round(fixture["map_family"], event.get("wire_round"))
        if event.get("unit") != supported[event["semantic"]]:
            raise ValueError("wrong public-event unit")
        value = event.get("value")
        if type(value) not in (int, float):
            raise ValueError("public-event value must be numeric")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or value < 0 or (event["semantic"] == "total_avg_cells" and value == 0):
            raise ValueError("invalid public-event value")
        if event["semantic"] == "q4_count" and type(value) is not int:
            raise ValueError("q4_count is an integer count")


def simulate_visibility(
    fixture: dict,
    round_no: int,
    *,
    seed: int = 0,
    tool_schedule: dict[int, str] | None = None,
) -> dict:
    """Return observable fields, never the warehouse truth object.

    Every call replays from R1 with its own integer-seeded RNG. Within a tool
    use draws are without replacement; each new use can hit prior reveals.
    A custom schedule replaces the whole default schedule (missing rounds=no tool).
    """
    _validate_fixture(fixture)
    if type(round_no) is not int or not 1 <= round_no <= 5:
        raise ValueError("round_no must be an integer in 1..5")
    if type(seed) is not int:
        raise ValueError("seed must be an explicit integer")
    schedule = DEFAULT_TOOLS if tool_schedule is None else tool_schedule
    if not isinstance(schedule, dict) or any(
        type(r) is not int or not 1 <= r <= 5 or tool not in {*TOOL_DRAWS, "goldscan", "none"}
        for r, tool in schedule.items()
    ):
        raise ValueError("invalid teaching tool schedule")
    items = fixture["items"]
    rng = random.Random(seed)
    visible: dict[str, dict] = {}
    tool_events: list[dict] = []
    q5_scan: dict | None = None

    def reveal(item: dict, granularity: str, source: str) -> None:
        key = item["entity"]
        old = visible.get(key)
        sources = old["sources"] + [source] if old else [source]
        if old and _RANK[old["granularity"]] >= _RANK[granularity]:
            old["sources"] = sources
            return
        # These are new objects with a field whitelist, never item.copy().
        view = {"entity": key, "quality": item["quality"], "granularity": granularity, "sources": sources}
        if granularity == "quality":
            # Deliberately coarse synthetic location, not a hidden footprint.
            view["coarse_zone"] = "upper" if item["row"] <= 4 else "lower"
        else:
            view.update({k: item[k] for k in ("row", "col", "width", "height", "cells")})
        if granularity == "full":
            view["value"] = item["value"]
        visible[key] = view

    for current_round in range(1, round_no + 1):
        quality = SKILL_QUALITY.get(current_round)
        if quality is not None:
            for item in items:
                if item["quality"] == quality:
                    reveal(item, "outline", f"R{current_round}:skill")
        tool = schedule.get(current_round, "none")
        if tool == "goldscan":
            # No hidden gold item IDs/counts/positions are attached to this aggregate.
            q5_scan = {"status": "known", "cells": sum(it["cells"] for it in items if it["quality"] == 5)}
            tool_events.append({"round": current_round, "tool": tool, "cells": q5_scan["cells"], "unit": "cells"})
        elif tool in TOOL_DRAWS:
            count, granularity = TOOL_DRAWS[tool]
            drawn = rng.sample(items, min(count, len(items)))
            repeated = [item["entity"] for item in drawn if item["entity"] in visible]
            for item in drawn:
                reveal(item, granularity, f"R{current_round}:{tool}")
            tool_events.append({
                "round": current_round, "tool": tool,
                "entities": [item["entity"] for item in drawn],
                "already_visible": repeated,
            })

    fixed_counts = {"split_white": sum(it["quality"] == 1 for it in items)}
    fixed_cells = {"split_white": sum(it["cells"] for it in items if it["quality"] == 1)}
    if round_no >= 2:
        fixed_counts["split_green"] = sum(it["quality"] == 2 for it in items)
        fixed_counts["bucket_q1_white_plus_green"] = fixed_counts["split_white"] + fixed_counts["split_green"]
        fixed_cells["bucket_q1_white_plus_green"] = sum(it["cells"] for it in items if it["quality"] in (1, 2))
    for quality in (3, 4):
        if round_no >= quality:
            fixed_counts[f"q{quality}"] = sum(it["quality"] == quality for it in items)
            fixed_cells[f"q{quality}"] = sum(it["cells"] for it in items if it["quality"] == quality)
    if q5_scan is not None:
        fixed_cells["q5"] = q5_scan["cells"]
    public = []
    for event in fixture.get("public_events", []):
        shown = public_visible_round(fixture["map_family"], event["wire_round"])
        if shown <= round_no:
            public.append({"visible_round": shown, **{k: event[k] for k in ("semantic", "value", "unit")}})
    return {
        "synthetic": True,
        "kind": "visibility_snapshot",
        "round": round_no,
        "seed": seed,
        "coordinate_system": fixture["coordinate_system"],
        "items_visible": [visible[key] for key in sorted(visible)],
        "tool_events": tool_events,
        "fixed_counts": fixed_counts,
        "fixed_cells": fixed_cells,
        "merged_q1_count": {
            "certainty": "exact" if round_no >= 2 else "lower_bound",
            "value": sum(item["quality"] in (1, 2) for item in visible.values()),
            "unit": "items",
        },
        "q5_scan": q5_scan if q5_scan is not None else {"status": "missing"},
        "public_facts": public,
    }
