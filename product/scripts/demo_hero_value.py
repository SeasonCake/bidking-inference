"""Demo: hero marginal value across maps.

Runs contrast MC for all 20 heroes on a selection of maps, prints
a ranked table of which hero adds the most value on each map.
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
from bidking_lab.simulation.hero_skills import HERO_SKILLS
from bidking_lab.simulation.hero_value import simulate_hero_value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=8_000)
    parser.add_argument(
        "--map-ids", type=int, nargs="*",
        default=[2107, 2301, 2407, 2505],
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    t = repo / "data" / "raw" / "tables"
    maps = load_bid_map_table(t / "BidMap.txt")
    drops = load_drop_table(t / "Drop.txt")
    items = load_item_table(t / "Item.txt")

    rng = np.random.default_rng(20260515)
    hero_ids = sorted(HERO_SKILLS.keys())

    for mid in args.map_ids:
        if mid not in maps:
            print(f"skip map {mid}")
            continue
        print(f"\n{'='*80}")
        print(
            f"Map {mid}: {maps[mid].name}  "
            f"(items={maps[mid].items_per_session_min}-"
            f"{maps[mid].items_per_session_max})"
        )
        print(f"{'='*80}")

        results = []
        for hid in hero_ids:
            r = simulate_hero_value(
                hid, mid, maps=maps, drops=drops, items=items,
                n_trials=args.trials, rng=rng,
            )
            results.append(r)

        results.sort(key=lambda r: r.marginal_value, reverse=True)
        print(
            f"{'rank':>4}  {'hero':>3}  {'name':<10}  "
            f"{'baseline':>12}  {'with_hero':>12}  {'marginal':>10}  {'%':>6}"
        )
        print("-" * 68)
        for rank, r in enumerate(results, 1):
            print(
                f"{rank:>4}  {r.hero_id:>3}  {r.hero_name:<10}  "
                f"{r.baseline_mean:>12,.0f}  {r.hero_mean:>12,.0f}  "
                f"{r.marginal_value:>+10,.0f}  {r.marginal_pct:>+5.1f}%"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
