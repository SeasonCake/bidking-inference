"""Run the first-pass Monte Carlo on a handful of maps and print a digest.

Picks one map from each major category (快递 / 仓库 / 集装箱 / 别墅 /
沉船) at each available tier, runs ``n_trials`` sessions, and prints a
ranked summary.
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
from bidking_lab.simulation.basic_mc import simulate_map


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=10_000)
    parser.add_argument(
        "--map-ids",
        type=int,
        nargs="*",
        default=[
            # 快递
            2101, 2107,
            # 仓库
            2201, 2203,
            # 集装箱 — low / mid tier
            2301, 2307, 3301,
            # 别墅 — low / mid / high tier (same theme: 私人金库)
            2407, 3407, 4407,
            # 沉船 — low / mid / high tier (同主题：殖民商船宝库)
            2505, 3505, 4505,
        ],
        help="map ids to simulate",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    tables_in = repo_root / "data" / "raw" / "tables"

    print(f"loading raw tables from: {tables_in}")
    maps = load_bid_map_table(tables_in / "BidMap.txt")
    drops = load_drop_table(tables_in / "Drop.txt")
    items = load_item_table(tables_in / "Item.txt")
    print(f"  loaded: maps={len(maps)}, drops={len(drops)}, items={len(items)}\n")

    rng = np.random.default_rng(seed=20260514)
    results = []
    for map_id in args.map_ids:
        if map_id not in maps:
            print(f"  skip: map_id={map_id} not in BidMap.txt")
            continue
        res = simulate_map(
            map_id,
            maps=maps,
            drops=drops,
            items=items,
            n_trials=args.trials,
            rng=rng,
        )
        results.append(res)

    results.sort(key=lambda r: r.mean, reverse=True)

    print(f"=== Monte Carlo summary ({args.trials} trials per map, "
          f"by mean session value, descending) ===\n")
    print(
        f"{'map_id':>7}  {'name':<28}  {'mean':>14}  {'q05':>12}  "
        f"{'q50':>12}  {'q95':>12}  {'max':>14}  {'CV':>6}"
    )
    print("-" * 124)
    for r in results:
        cv = (r.std / r.mean) if r.mean > 0 else float("nan")
        print(
            f"{r.map_id:>7}  {r.map_name[:28]:<28}  "
            f"{r.mean:>14,.0f}  {r.q05:>12,}  {r.q50:>12,}  "
            f"{r.q95:>12,}  {r.max:>14,}  {cv:>6.2f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
