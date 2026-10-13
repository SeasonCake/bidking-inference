"""Probe BidMap.col[19] to confirm it is the per-round category sequence.

Hypothesis
----------
``col[19]`` is a length-5 list of Item category codes (101–110) that
gives, for each of the 5 auction rounds, *which* category the round
will draw from. If true:

1. The 5 entries should all be valid Item category codes
   (101=家具, 102=医疗, 103=时尚, 104=武器, 105=珠宝, 106=文物,
   107=数码, 108=能源, 109=食饮, 110=书画).
2. The set of categories in ``col[19]`` should be a **subset** of the
   set of categories actually present in the map's flattened drop pool.
3. If we group the drop pool by category and compute "category mass"
   (sum of probabilities), the categories that appear more often in
   ``col[19]`` should dominate the pool — at least there should be a
   positive correlation.

We dump col[19] for every leaf map, plus an alignment report between
col[19] and pool category mass for a small set of representative maps.

Usage
-----
    python scripts/probe_round_categories.py             # full report
    python scripts/probe_round_categories.py --map 2407  # one map only
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
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

CATEGORY_NAMES = {
    101: "家具",
    102: "医疗",
    103: "时尚",
    104: "武器",
    105: "珠宝",
    106: "文物",
    107: "数码",
    108: "能源",
    109: "食饮",
    110: "书画",
}

TABLES = ROOT / "data" / "raw" / "tables"


def parse_col19(raw: str) -> list[int]:
    if raw in ("", "[]"):
        return []
    return [int(x) for x in json.loads(raw)]


def pool_category_mass(fp, items) -> dict[int, float]:
    mass: dict[int, float] = Counter()
    for iid, p in zip(fp.item_ids, fp.probabilities):
        item = items[iid]
        cat = item.tags[0] if item.tags else 0
        mass[cat] += p
    return dict(mass)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", type=int, default=None, help="single map id to inspect")
    parser.add_argument("--limit", type=int, default=20, help="row limit when scanning all maps")
    args = parser.parse_args()

    maps = load_bid_map_table(TABLES / "BidMap.txt")
    drops = load_drop_table(TABLES / "Drop.txt")
    items = load_item_table(TABLES / "Item.txt")

    if args.map is not None:
        bm = maps[args.map]
        col19 = parse_col19(bm.raw_row[19])
        print(f"=== map {bm.map_id} {bm.name} ===")
        print(f"raw col[19]    : {bm.raw_row[19]!r}")
        print(f"parsed col[19] : {col19}")
        if col19:
            print("  per-round  :", [(c, CATEGORY_NAMES.get(c, "?")) for c in col19])
        if bm.sub_pool_weights:
            print(f"anthology map → sub_pool_weights = {bm.sub_pool_weights[:3]}...")
            return
        fp = flatten_pool(bm.drop_pool_id, drops, items)
        mass = pool_category_mass(fp, items)
        print(f"\ndrop pool {bm.drop_pool_id} category mass:")
        for cat, m in sorted(mass.items(), key=lambda x: -x[1]):
            mark = "[hint]" if cat in col19 else "      "
            print(f"  {mark} {cat} {CATEGORY_NAMES.get(cat, '?'):4s}  {m:6.2%}")
        col19_set = set(col19)
        pool_set = set(mass.keys())
        missing = col19_set - pool_set
        extra = pool_set - col19_set
        print(f"\ncol[19] cats not in pool : {sorted(missing)}")
        print(f"pool cats not in col[19] : {sorted(extra)}")
        return

    leaf = [m for m in maps.values() if not m.sub_pool_weights and m.auction_mode != "training"]
    print(f"leaf+nontraining maps: {len(leaf)}")
    print()

    col19_lengths = Counter()
    col19_value_set: set[int] = set()
    rows = []
    nonzero_position_counter = Counter()
    nonzero_count_counter = Counter()
    for bm in sorted(leaf, key=lambda m: m.map_id):
        col19 = parse_col19(bm.raw_row[19])
        col19_lengths[len(col19)] += 1
        col19_value_set.update(col19)
        rows.append((bm.map_id, bm.name, col19))
        nonzero_count = sum(1 for x in col19 if x != 0)
        nonzero_count_counter[nonzero_count] += 1
        for i, x in enumerate(col19):
            if x != 0:
                nonzero_position_counter[i] += 1

    print("col[19] length distribution:")
    for k, v in sorted(col19_lengths.items()):
        print(f"  length={k}: {v} maps")
    print(f"\nunique values across all col[19]: {sorted(col19_value_set)}")
    print(f"  (CATEGORY_NAMES known: {sorted(CATEGORY_NAMES)})")

    print("\nhint positions (which round indices carry non-zero category):")
    for i in range(5):
        c = nonzero_position_counter.get(i, 0)
        print(f"  round_idx={i} (R{i+1}): {c}/{len(rows)} maps carry hint")
    print("\nhow many hints per map:")
    for k, v in sorted(nonzero_count_counter.items()):
        print(f"  {k} hint(s): {v} maps")

    print("\nthemed sample (one per theme/tier):")
    seen = set()
    for mid, name, col in rows:
        key = mid // 100
        if key in seen:
            continue
        seen.add(key)
        print(f"  {mid}  {name[:14]:14s}  {col}")


if __name__ == "__main__":
    main()
