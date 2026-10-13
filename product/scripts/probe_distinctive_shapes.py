"""Enumerate items by shape, focusing on shapes that pin down value/quality.

User notes:

- 4x4 shapes: usually 屏风 (high-value); only confounder is 石狮子.
- 5x4 / 4x5: only one low-value blue item — i.e., seeing 5x4 = essentially
  a known cheap item, so subtract its cells from "remaining mystery" total.
- More generally, rare shapes carry high information about both quality
  and value.

This script enumerates the population of every distinct shape that appears
in the *droppable* item set (those reachable from at least one map's pool).
"""

from __future__ import annotations

import io
import sys
from collections import defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.bid_map_table import load_bid_map_table
from bidking_lab.extract.drop_table import load_drop_table
from bidking_lab.extract.item_table import load_item_table
from bidking_lab.simulation.basic_mc import flatten_pool

TABLES = ROOT / "data" / "raw" / "tables"
QUALITY_LABEL = {0: "-", 1: "白", 2: "绿", 3: "蓝", 4: "紫", 5: "金", 6: "红"}


def fmt_value(v: int) -> str:
    if v >= 10_000:
        return f"{v/10_000:>5.1f}万"
    return f"{v:>5d}"


def main() -> None:
    maps = load_bid_map_table(TABLES / "BidMap.txt")
    drops = load_drop_table(TABLES / "Drop.txt")
    items = load_item_table(TABLES / "Item.txt")

    droppable: set[int] = set()
    for m in maps.values():
        if m.sub_pool_weights or m.auction_mode == "training":
            continue
        fp = flatten_pool(m.drop_pool_id, drops, items)
        droppable.update(fp.item_ids)

    by_shape: dict[tuple[int, int], list] = defaultdict(list)
    for iid in droppable:
        it = items[iid]
        if it.shape_w == 0 or it.shape_h == 0:
            continue
        by_shape[(it.shape_w, it.shape_h)].append(it)

    print(f"droppable items with real shape: {sum(len(v) for v in by_shape.values())}")
    print(f"distinct shapes: {len(by_shape)}")
    print()
    print(f"{'shape':>6s}  {'area':>4s}  {'count':>5s}  contents (id Q value name)")
    print("-" * 80)

    for shape in sorted(by_shape.keys(), key=lambda s: (s[0] * s[1], s)):
        cells = shape[0] * shape[1]
        bucket = by_shape[shape]
        if cells >= 12 or len(bucket) <= 3:
            sample = bucket
        else:
            sample = []

        head = f"{shape[0]}x{shape[1]:<3d}".rjust(6)
        print(f"{head}  {cells:>4d}  {len(bucket):>5d}", end="")
        if sample:
            print()
            for it in sample:
                print(
                    f"           {it.item_id:>7d} {QUALITY_LABEL.get(it.quality,'?'):2s} "
                    f"{fmt_value(it.value):>7s}  {it.name}"
                )
        else:
            by_q: dict[int, int] = defaultdict(int)
            for it in bucket:
                by_q[it.quality] += 1
            qstr = " ".join(
                f"{QUALITY_LABEL.get(q,'?')}={n}"
                for q, n in sorted(by_q.items())
            )
            print(f"  ({qstr})")

    print()
    print("=== shapes spotlighted by the user ===")
    for shape in [(4, 4), (5, 4), (4, 5), (5, 5), (6, 3), (6, 4), (3, 6)]:
        bucket = by_shape.get(shape, [])
        if not bucket:
            continue
        print(f"\n{shape}: {len(bucket)} item(s)")
        for it in bucket:
            print(
                f"  {it.item_id:>7d} {QUALITY_LABEL.get(it.quality,'?'):2s} "
                f"{fmt_value(it.value):>7s}  {it.name}"
            )


if __name__ == "__main__":
    main()
