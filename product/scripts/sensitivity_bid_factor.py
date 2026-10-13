"""Bid-factor sensitivity analysis: open vs sealed 别墅."""

from __future__ import annotations

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
from bidking_lab.simulation.bidding import BidPolicy, simulate_session


def main() -> int:
    t = Path(__file__).resolve().parent.parent / "data" / "raw" / "tables"
    maps = load_bid_map_table(t / "BidMap.txt")
    drops = load_drop_table(t / "Drop.txt")
    items = load_item_table(t / "Item.txt")

    h = f"{'factor':>6}  {'mode':>8}  {'budget':>10}  {'net_mean':>10}  {'win%':>5}  {'budg%':>5}  {'ROI':>6}"
    print("=== bid_factor sensitivity: 未知别墅 open(2401) vs sealed(4401) ===\n")
    print(h)
    print("-" * len(h))

    for bf in [0.20, 0.30, 0.35, 0.40, 0.50, 0.60]:
        for mid in [2401, 4401]:
            p = BidPolicy(bid_factor=bf, reserve_lo=0.15, reserve_hi=0.50)
            r = simulate_session(
                mid, maps=maps, drops=drops, items=items,
                policy=p, n_trials=15_000,
                rng=np.random.default_rng(42),
            )
            print(
                f"{bf:>6.2f}  {r.auction_mode:>8}  {r.starting_budget:>10,}  "
                f"{r.net_mean:>10,.0f}  {r.win_rate_mean:>5.0%}  "
                f"{r.budget_util_mean:>5.0%}  {r.roi_mean:>6.1f}x"
            )
        print()

    print("\n=== Same for 殖民商船宝库 open(2505) vs sealed(4505) ===\n")
    print(h)
    print("-" * len(h))

    for bf in [0.20, 0.30, 0.35, 0.40, 0.50, 0.60]:
        for mid in [2505, 4505]:
            p = BidPolicy(bid_factor=bf, reserve_lo=0.15, reserve_hi=0.50)
            r = simulate_session(
                mid, maps=maps, drops=drops, items=items,
                policy=p, n_trials=15_000,
                rng=np.random.default_rng(42),
            )
            print(
                f"{bf:>6.2f}  {r.auction_mode:>8}  {r.starting_budget:>10,}  "
                f"{r.net_mean:>10,.0f}  {r.win_rate_mean:>5.0%}  "
                f"{r.budget_util_mean:>5.0%}  {r.roi_mean:>6.1f}x"
            )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
