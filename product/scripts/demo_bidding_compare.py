"""Compare open (明拍) vs sealed (暗拍) across 别墅/沉船/集装箱.

Shows how budget differences affect net profit under the same drop pool.
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
from bidking_lab.simulation.bidding import BidPolicy, simulate_session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=20_000)
    parser.add_argument("--bid-factor", type=float, default=0.35)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    t = repo / "data" / "raw" / "tables"

    maps = load_bid_map_table(t / "BidMap.txt")
    drops = load_drop_table(t / "Drop.txt")
    items = load_item_table(t / "Item.txt")

    policy = BidPolicy(bid_factor=args.bid_factor, reserve_lo=0.15, reserve_hi=0.50)

    target_ids = [
        # 别墅 — 未知别墅: open / training / sealed
        2401, 3401, 4401,
        # 别墅 — 私人金库
        2407, 3407, 4407,
        # 沉船 — 殖民商船宝库
        2505, 3505, 4505,
        # 集装箱 — 杂货: open / training (no sealed tier)
        2301, 3301,
        # 快递 — 古董街快递
        2107, 3107,
    ]

    print(f"Bid policy: factor={policy.bid_factor}  "
          f"NPC reserve=[{policy.reserve_lo}, {policy.reserve_hi}]  "
          f"trials={args.trials}\n")

    header = (
        f"{'id':>5}  {'name':<18}  {'mode':<8}  {'budget':>12}  {'fee':>7}  "
        f"{'gross_mean':>12}  {'net_mean':>12}  {'net_q50':>10}  {'net_q95':>10}  "
        f"{'win%':>5}  {'budg%':>5}  {'ROI':>6}"
    )
    print(header)
    print("-" * len(header))

    rng = np.random.default_rng(20260515)
    for mid in target_ids:
        if mid not in maps:
            continue
        r = simulate_session(
            mid, maps=maps, drops=drops, items=items,
            policy=policy, n_trials=args.trials, rng=rng,
        )
        print(
            f"{r.map_id:>5}  {r.map_name[:18]:<18}  {r.auction_mode:<8}  "
            f"{r.starting_budget:>12,}  {r.entry_fee:>7,}  "
            f"{r.gross_mean:>12,.0f}  {r.net_mean:>12,.0f}  "
            f"{r.net_q50:>10,}  {r.net_q95:>10,}  "
            f"{r.win_rate_mean:>5.0%}  {r.budget_util_mean:>5.0%}  "
            f"{r.roi_mean:>6.1f}x"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
