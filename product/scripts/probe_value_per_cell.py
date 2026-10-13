"""Estimate per-cell value (value / shape_area) by quality.

User heuristic (2026-05-15 playtest):
  - 紫品 (purple, quality=4): ~2500 silver / cell
  - 金品 (gold, quality=5):  ~9400 silver / cell, high variance
  - 红品 (red, quality=6):   high variance (few items)

This probe verifies those numbers against the actual droppable-item
distribution. For each quality tier, we compute the per-cell value of
every droppable physical item (shape > 0), weighted by drop probability
in a target map's flattened pool. The result is the prior we'll feed
into the inference engine when the player uses a value-sum tool
(``X品估价``) — it lets us convert ``Σ value`` back to "approximately
how many cells worth of purple stuff is in the session".
"""

from __future__ import annotations

import io
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

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

QUALITY_LABELS = {0: "-", 1: "白", 2: "绿", 3: "蓝", 4: "紫", 5: "金", 6: "红"}


def percentiles(values: list[float], weights: list[float]) -> dict[str, float]:
    """Compute weighted mean / median / p10 / p90."""
    if not values:
        return {}
    total_w = sum(weights)
    mean = sum(v * w for v, w in zip(values, weights)) / total_w if total_w else float("nan")
    paired = sorted(zip(values, weights))
    cum = 0.0
    p10 = p50 = p90 = float("nan")
    for v, w in paired:
        cum += w / total_w
        if p10 != p10 and cum >= 0.10:  # NaN check is the only one that matters
            p10 = v
        if p50 != p50 and cum >= 0.50:
            p50 = v
        if p90 != p90 and cum >= 0.90:
            p90 = v
    return {"mean": mean, "p10": p10, "p50": p50, "p90": p90}


def main() -> None:
    maps = load_bid_map_table(TABLES / "BidMap.txt")
    drops = load_drop_table(TABLES / "Drop.txt")
    items = load_item_table(TABLES / "Item.txt")

    # Global prior: every droppable item with shape > 0
    droppable: set[int] = set()
    for m in maps.values():
        if m.sub_pool_weights or m.auction_mode == "training":
            continue
        fp = flatten_pool(m.drop_pool_id, drops, items)
        droppable.update(fp.item_ids)

    by_q_unweighted: dict[int, list[float]] = defaultdict(list)
    for iid in droppable:
        it = items[iid]
        area = it.shape_w * it.shape_h
        if area == 0:
            continue
        by_q_unweighted[it.quality].append(it.value / area)

    print("=== global (unweighted by drop prob, just by item population) ===")
    print(f"{'Q':2s}  {'name':<4s}  {'n':>4s}  {'mean':>10s}  {'p10':>10s}  {'p50':>10s}  {'p90':>10s}")
    for q in sorted(by_q_unweighted):
        vals = sorted(by_q_unweighted[q])
        n = len(vals)
        mean = sum(vals) / n
        p10 = vals[max(0, int(n * 0.1) - 1)]
        p50 = median(vals)
        p90 = vals[min(n - 1, int(n * 0.9))]
        print(
            f"{q:2d}  {QUALITY_LABELS.get(q, '?'):<4s}  {n:>4d}  "
            f"{mean:>10,.0f}  {p10:>10,.0f}  {p50:>10,.0f}  {p90:>10,.0f}"
        )

    # Red items split by shape area (small vs medium vs huge)
    # User insight: red 巨物 (huge items) have LOWER per-cell value than
    # small red items (because small reds are concentrated high-value gems).
    print()
    print("=== red items split by shape area ===")
    red_by_band: dict[str, list[float]] = {"1-2格": [], "3-6格": [], "7-15格": [], "≥16格": []}
    for iid in droppable:
        it = items[iid]
        if it.quality != 6:
            continue
        area = it.shape_w * it.shape_h
        if area == 0:
            continue
        per_cell = it.value / area
        if area <= 2:
            band = "1-2格"
        elif area <= 6:
            band = "3-6格"
        elif area <= 15:
            band = "7-15格"
        else:
            band = "≥16格"
        red_by_band[band].append(per_cell)
    print(f"{'band':<8s}  {'n':>4s}  {'mean':>10s}  {'p10':>10s}  {'p50':>10s}  {'p90':>10s}")
    for band, vals in red_by_band.items():
        if not vals:
            continue
        n = len(vals)
        vals_sorted = sorted(vals)
        mean = sum(vals) / n
        p10 = vals_sorted[max(0, int(n * 0.1) - 1)]
        p50 = median(vals)
        p90 = vals_sorted[min(n - 1, int(n * 0.9))]
        print(
            f"{band:<8s}  {n:>4d}  "
            f"{mean:>10,.0f}  {p10:>10,.0f}  {p50:>10,.0f}  {p90:>10,.0f}"
        )

    # Per representative map, weighted by drop probability
    print()
    print("=== per representative map (weighted by drop probability) ===")
    for mid in [2407, 2507]:
        bm = maps[mid]
        fp = flatten_pool(bm.drop_pool_id, drops, items)
        print(f"\n--- map {mid} {bm.name} ---")
        per_q_vals: dict[int, list[float]] = defaultdict(list)
        per_q_weights: dict[int, list[float]] = defaultdict(list)
        for iid, p in zip(fp.item_ids, fp.probabilities):
            it = items[iid]
            area = it.shape_w * it.shape_h
            if area == 0:
                continue
            per_q_vals[it.quality].append(it.value / area)
            per_q_weights[it.quality].append(p)

        print(f"{'Q':2s}  {'name':<4s}  {'mean':>10s}  {'p10':>10s}  {'p50':>10s}  {'p90':>10s}")
        for q in sorted(per_q_vals):
            stats = percentiles(per_q_vals[q], per_q_weights[q])
            print(
                f"{q:2d}  {QUALITY_LABELS.get(q, '?'):<4s}  "
                f"{stats['mean']:>10,.0f}  {stats['p10']:>10,.0f}  "
                f"{stats['p50']:>10,.0f}  {stats['p90']:>10,.0f}"
            )


if __name__ == "__main__":
    main()
