"""Phase 2 tool-ROI table.

Runs the leave-one-out ROI evaluation on the two priority maps
(\u522b\u5885 2407 + \u6c89\u8239 2510, configurable via --map-ids) for several
hero+kit combinations. Outputs a Markdown table to stdout suitable for
pasting into PROGRESS.md or the resume notebook.

Modeled tools (have a TOOL_SPECS entry):

* \u666e\u54c1\u626b\u63cf, \u826f\u54c1\u626b\u63cf, \u4f18\u54c1\u626b\u63cf,
  \u4f18\u54c1\u4f30\u4ef7, \u4f18\u54c1\u5747\u683c,
  \u6781\u54c1\u626b\u63cf, \u6781\u54c1\u4f30\u4ef7,
  \u603b\u4ed3\u50a8\u7a7a\u95f4

Not yet modeled (excluded from the kits below):

* \u968f\u673a\u62bd\u68c0 / \u5b9d\u5149\u56db\u9274 (random-item reveals)
  — Phase 2.1 follow-up.

ROI metric is *value-error per silver*: positive means the tool's
revealed reading shrinks the gap between top-1 inferred warehouse value
and ground truth.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from bidking_lab.extract.bid_map_table import load_bid_map_table
from bidking_lab.extract.drop_table import load_drop_table
from bidking_lab.extract.item_table import load_item_table
from bidking_lab.inference.observation import HeroMode
from bidking_lab.inference.roi import compute_tool_roi


# Kits with only modeled tools. The narrative "minimal vs full" lets the
# table show that adding scans on top of value tools is what unlocks
# joint inference rather than the value-tool readings themselves.
KITS: dict[str, tuple[HeroMode, tuple[str, ...], bool]] = {
    "Ethan default":  ("ethan", (
        "\u666e\u54c1\u626b\u63cf", "\u826f\u54c1\u626b\u63cf",
        "\u4f18\u54c1\u4f30\u4ef7", "\u4f18\u54c1\u5747\u683c",
        "\u6781\u54c1\u4f30\u4ef7",
    ), False),
    "Ethan +warehouse": ("ethan", (
        "\u666e\u54c1\u626b\u63cf", "\u826f\u54c1\u626b\u63cf",
        "\u4f18\u54c1\u4f30\u4ef7", "\u4f18\u54c1\u5747\u683c",
        "\u6781\u54c1\u4f30\u4ef7", "\u603b\u4ed3\u50a8\u7a7a\u95f4",
    ), False),
    "Aisha minimal":  ("aisha", (
        "\u4f18\u54c1\u4f30\u4ef7", "\u6781\u54c1\u4f30\u4ef7",
        "\u603b\u4ed3\u50a8\u7a7a\u95f4",
    ), True),  # outline pin ON
}


def _fmt(x: float) -> str:
    if abs(x) >= 100_000:
        return f"{x:,.0f}"
    if abs(x) >= 1:
        return f"{x:,.1f}"
    return f"{x:+.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=80)
    parser.add_argument("--per-bucket-top", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260515)
    parser.add_argument(
        "--map-ids", type=int, nargs="*",
        default=[2407, 2510],
        help="map ids to run (default: \u522b\u5885 2407, \u6c89\u8239 2510)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    tables_in = repo_root / "data" / "raw" / "tables"

    sys.stderr.write(f"loading tables from {tables_in}\n")
    maps = load_bid_map_table(tables_in / "BidMap.txt")
    drops = load_drop_table(tables_in / "Drop.txt")
    items = load_item_table(tables_in / "Item.txt")
    sys.stderr.write(
        f"  loaded: maps={len(maps)}, drops={len(drops)}, items={len(items)}\n"
    )

    rng = np.random.default_rng(args.seed)

    # Header
    print(f"# Tool ROI table — Phase 2 ({args.trials} trials/cell, "
          f"per_bucket_top={args.per_bucket_top})\n")
    print("Metric: **mean value-error reduction per LOO trial, in silver.**")
    print("ROI = info_gain_value_mean / silver_cost. "
          "Positive = the tool sharpens inference; negative = the engine "
          "was relying on a fortuitous prior in that bucket.\n")

    for map_id in args.map_ids:
        if map_id not in maps:
            sys.stderr.write(f"skip: map {map_id} not in BidMap.txt\n")
            continue
        map_name = maps[map_id].name
        print(f"## Map {map_id}  ({map_name})\n")
        for kit_name, (hero, tools, outline) in KITS.items():
            sys.stderr.write(
                f"  running {kit_name} (hero={hero}, "
                f"{len(tools)} tools, outline={outline}) ...\n"
            )
            rois = compute_tool_roi(
                map_id, tool_kit=tools,
                maps=maps, drops=drops, items=items,
                hero=hero, n_trials=args.trials, rng=rng,
                include_aisha_outline=outline,
                per_bucket_top=args.per_bucket_top,
            )
            print(f"### {kit_name}\n")
            print("| Tool | Silver | Info gain (\u00B1std) | "
                  "Cells gain | ROI per silver |")
            print("|------|-------:|--------------------:|-----------:|---------------:|")
            for r in sorted(rois, key=lambda x: -x.roi_value):
                print(
                    f"| {r.tool_name} | {r.silver_cost:,} | "
                    f"{_fmt(r.info_gain_value_mean)} \u00B1 "
                    f"{_fmt(r.info_gain_value_std)} | "
                    f"{_fmt(r.info_gain_cells_mean)} | "
                    f"{r.roi_value:+.4f} |"
                )
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
