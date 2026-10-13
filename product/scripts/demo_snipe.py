"""Demo: bidding-hint recommendations (snipe + pass) for an Ethan session.

Loads real game tables and runs both ``compute_snipe_recommendation``
(big warehouse upside) and ``compute_pass_recommendation`` (small
junk-heavy downside) on hand-crafted scenarios. Prints rationales and
tooltips for each.
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
from bidking_lab.inference.observation import (
    QualityBucketObs,
    SessionObs,
)
from bidking_lab.inference.snipe import (
    compute_pass_recommendation,
    compute_snipe_recommendation,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-id", type=int, default=2407)
    parser.add_argument("--warehouse-cells", type=int, default=132,
                        help="player-observed warehouse total cells")
    parser.add_argument("--wg-cells", type=int, default=22,
                        help="white+green total cells from \u666e\u54c1\u626b\u63cf")
    parser.add_argument("--blue-cells", type=int, default=16,
                        help="blue total cells from \u826f\u54c1\u626b\u63cf")
    parser.add_argument("--trials", type=int, default=3000)
    parser.add_argument("--tol", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260515)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    tables_in = repo_root / "data" / "raw" / "tables"
    print(f"loading tables from {tables_in}")
    maps = load_bid_map_table(tables_in / "BidMap.txt")
    drops = load_drop_table(tables_in / "Drop.txt")
    items = load_item_table(tables_in / "Item.txt")
    print(f"  loaded: maps={len(maps)}, drops={len(drops)}, items={len(items)}\n")

    if args.map_id not in maps:
        print(f"error: map_id={args.map_id} not in BidMap.txt")
        return 1

    session = SessionObs(
        map_id=args.map_id,
        hero="ethan",
        warehouse_total_cells=args.warehouse_cells,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=args.wg_cells),
            3: QualityBucketObs(quality=3, total_cells=args.blue_cells),
        },
    )

    print(f"=== Snipe scenario: map {args.map_id} ({maps[args.map_id].name}) ===")
    print(f"  warehouse_total_cells = {args.warehouse_cells}")
    print(f"  observed: wg={args.wg_cells}, blue={args.blue_cells}, "
          f"low_total={args.wg_cells + args.blue_cells}")
    print(f"  MC: n_trials={args.trials}, tolerance=\u00b1{args.tol} cells\n")

    rng = np.random.default_rng(args.seed)
    snipe = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=args.trials, warehouse_tolerance=args.tol, rng=rng,
    )
    pass_rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=args.trials, warehouse_tolerance=args.tol,
        rng=np.random.default_rng(args.seed + 1),
    )

    if snipe is None and pass_rec is None:
        wh = args.warehouse_cells
        print("Both snipe and pass gates returned None. Reasons:\n")
        if wh < 80:
            print(f"  [snipe] warehouse {wh} < 120 \u2014 structurally fails")
            print(f"  [pass ] warehouse {wh} \u2264 80 \u2014 gate passes, but MC")
            print(f"          probably didn't get enough matching samples; try")
            print(f"          larger --trials or larger --tol.")
        elif wh > 120:
            print(f"  [snipe] warehouse {wh} \u2265 120 \u2014 gate passes, but MC")
            print(f"          probably didn't get enough matching samples; this is")
            print(f"          common on small mansion maps where {wh} cells is rare.")
            print(f"          Try --map-id 2510 (shipwreck, distribution goes to 164).")
            print(f"  [pass ] warehouse {wh} > 80 \u2014 structurally fails")
        else:
            print(f"  [snipe] warehouse {wh} < 120 \u2014 structurally fails")
            print(f"  [pass ] warehouse {wh} > 80 \u2014 structurally fails")
            print(f"          (the 80-120 range is intentionally a no-hint zone)")
        return 0

    if snipe is not None:
        print(f"--- SNIPE (big warehouse upside) ---")
        print(f"  matching samples : {snipe.n_matching_samples}")
        print(f"  P25 / P50 / P75 / P90 : "
              f"{snipe.p25_value:,} / {snipe.expected_value:,} / "
              f"{snipe.p75_value:,} / {snipe.p90_value:,} silver")
        print(f"  safe_floor_bid   : {snipe.safe_floor_bid:>12,} silver  (P50 \u00d7 0.70)")
        print(f"  snipe_max_bid    : {snipe.snipe_max_bid:>12,} silver  (P75 \u00d7 1.15)")
        print(f"  tooltip          : {snipe.as_ui_tooltip()}\n")

    if pass_rec is not None:
        print(f"--- PASS (small junk-heavy downside) ---")
        print(f"  matching samples : {pass_rec.n_matching_samples}")
        print(f"  conditional P25/P50/P75 : "
              f"{pass_rec.p25_value:,} / {pass_rec.expected_value:,} / "
              f"{pass_rec.p75_value:,} silver")
        print(f"  unconditional P50: {pass_rec.unconditional_p50:>12,} silver  "
              f"(this cabinet = {pass_rec.value_ratio:.0%} of overall)")
        print(f"  safe_entry_bid   : {pass_rec.safe_entry_bid:>12,} silver  (= P25)")
        print(f"  pass_max_bid     : {pass_rec.pass_max_bid:>12,} silver  (= P50)")
        print(f"  tooltip          : {pass_rec.as_ui_tooltip()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
