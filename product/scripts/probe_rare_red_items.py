"""List rare, high-value items that distort raw expected-value estimates.

The user identified that mansion / shipwreck loot histograms show big
positive bumps caused by a handful of >2M-silver red items (非洲之心,
金陵折扇, 黑王子红宝石, 豪宅管理用黑盒, 羊脂白玉籽和田玉, ...). For the
default no-information estimator these items dominate the variance but
contribute very little decision value (they almost never drop).

This probe:

1. Lists every Item with ``value > VALUE_FLOOR`` (default 1,000,000),
   sorted by value.
2. Joins each item with its drop probability in a representative high-
   tier map (default 2407 私人金库 and 2507 皇家御用货舱).
3. Shows shape (``shape_w x shape_h``) so we can later decide which
   rare items have a "distinctive footprint" (大物品形状 → 看到就知道
   是红的 → 不该被降权).

Usage::

    python scripts/probe_rare_red_items.py
    python scripts/probe_rare_red_items.py --value-floor 500000 --map 2507
"""

from __future__ import annotations

import argparse
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
        return f"{v/10_000:>7.1f}万"
    return f"{v:>7d}"


def fmt_prob(p: float) -> str:
    if p == 0:
        return "  --   "
    if p >= 1e-3:
        return f"{p*100:>5.2f}% "
    if p >= 1e-5:
        return f"{p*100:>5.3f}%"
    return f"{p*1e6:>5.1f}ppm"


def expected_contribution(p: float, value: int, items_per_session: float = 20.0) -> float:
    """Approx expected silver contribution per session from one item.

    With ``items_per_session`` items drawn with replacement, the expected
    count of any single item is ``items_per_session × p``. So expected
    value per session ≈ items_per_session × p × value.
    """
    return items_per_session * p * value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--value-floor", type=int, default=1_000_000)
    parser.add_argument("--map", type=int, action="append", default=None,
                        help="map_id(s) to evaluate; defaults to 2407 + 2507")
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args()

    map_ids = args.map if args.map else [2407, 2507]

    maps = load_bid_map_table(TABLES / "BidMap.txt")
    drops = load_drop_table(TABLES / "Drop.txt")
    items = load_item_table(TABLES / "Item.txt")

    prob_per_map: dict[int, dict[int, float]] = {}
    items_per_session: dict[int, float] = {}
    for mid in map_ids:
        bm = maps[mid]
        fp = flatten_pool(bm.drop_pool_id, drops, items)
        prob_per_map[mid] = dict(zip(fp.item_ids, fp.probabilities))
        items_per_session[mid] = (bm.items_per_session_min + bm.items_per_session_max) / 2

    rare = [it for it in items.values() if it.value >= args.value_floor]
    rare.sort(key=lambda it: -it.value)

    header_maps = "  ".join(f"{mid}({maps[mid].name[:8]})" for mid in map_ids)
    print(f"items with value ≥ {args.value_floor:,} ({len(rare)} total)")
    print(f"maps: {header_maps}")
    print()
    cols = [
        f"{'id':>7s}",
        f"{'name':<14s}",
        f"{'Q':2s}",
        f"{'value':>9s}",
        f"{'shape':>5s}",
    ]
    for mid in map_ids:
        cols.append(f"{'p('+str(mid)+')':>10s}")
        cols.append(f"{'E[v|'+str(mid)+']':>12s}")
    print("  ".join(cols))
    print("-" * (sum(len(c) for c in cols) + 2 * (len(cols) - 1)))

    total_contrib_per_map = defaultdict(float)
    full_pool_e = {}
    for mid in map_ids:
        full_pool_e[mid] = sum(
            items_per_session[mid] * p * items[iid].value
            for iid, p in prob_per_map[mid].items()
        )

    for it in rare[: args.top]:
        cols = [
            f"{it.item_id:>7d}",
            f"{it.name[:14]:<14s}",
            f"{QUALITY_LABEL.get(it.quality, '?'):2s}",
            fmt_value(it.value),
            f"{it.shape_w}x{it.shape_h}".rjust(5),
        ]
        for mid in map_ids:
            p = prob_per_map[mid].get(it.item_id, 0.0)
            cols.append(fmt_prob(p).rjust(10))
            contrib = expected_contribution(p, it.value, items_per_session[mid])
            cols.append(f"{contrib:>10,.0f}银".rjust(12))
            total_contrib_per_map[mid] += contrib
        print("  ".join(cols))

    print()
    print("=== summary ===")
    for mid in map_ids:
        contrib = total_contrib_per_map[mid]
        total = full_pool_e[mid]
        print(
            f"map {mid} {maps[mid].name[:10]:10s}"
            f"   pool E[session] = {total:>12,.0f}"
            f"   from these {min(args.top, len(rare))} rare items = {contrib:>10,.0f}"
            f"   ({contrib/total*100:.1f}% of total)"
        )


if __name__ == "__main__":
    main()
