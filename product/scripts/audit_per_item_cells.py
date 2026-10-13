"""Per-family per-item cell audit: is the engine's residual per_item (~3.5-4.0) too high?

The engine's `_residual_per_item_cell_estimate` averages the per-quality avg_cells of the UNFIXED
qualities (fallback constant 4.0). Hypothesis: an UNWEIGHTED mean across qualities over-counts cells
per item, because high tiers (q5/q6) are physically large and drag the mean up, while the residual
(unknown) items in a real warehouse are dominated by small low-tier pieces. The right per_item for
"convert residual grid -> residual item count" should be the ITEM-WEIGHTED mean cells/item of the
items that are actually still unknown at decision time.

This script measures, per map family and overall, on SETTLED truth:
  - item-weighted overall mean cells/item  = total_cells / total_count  (the natural residual target)
  - per-quality mean cells/item            (q1/q3/q4/q5/q6)
  - unweighted mean across present qualities (mimics engine averaging) vs item-weighted mean
  - residual-mix mean: weighting per-quality cells by each quality's share of total ITEM count
    (what a "typical unknown item" costs in cells) -- this is the calibration target.

Read-only. Run: python scripts/audit_per_item_cells.py [hero] [cap]
  hero default "aisha"; cap default 600 (warehouses scanned).
"""

from __future__ import annotations

import glob
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from synthesize_draw_hero_samples import load_warehouse  # noqa: E402

SAMPLE_DIR = ROOT / "data/samples/fatbeans"
QKEYS = ("q1", "q3", "q4", "q5", "q6")
# quality int -> bucket key (q1 absorbs raw quality 1 and 2, matching engine QMAP)
QBUCKET = {1: "q1", 2: "q1", 3: "q3", 4: "q4", 5: "q5", 6: "q6"}


def main(argv: list[str]) -> int:
    hero = argv[1] if len(argv) > 1 else "aisha"
    cap = int(argv[2]) if len(argv) > 2 else 600

    # Per family: accumulate cells and counts by quality bucket.
    fam_cells: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fam_count: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fam_n: dict[int, int] = defaultdict(int)
    # global shape distribution for cells/item histogram
    cell_hist: dict[int, int] = defaultdict(int)

    pattern = str(SAMPLE_DIR / (f"*_{hero}_*.json" if hero != "all" else "*.json"))
    used = 0
    for f in sorted(glob.glob(pattern))[:cap]:
        wh = load_warehouse(Path(f))
        if not wh or not wh.items:
            continue
        fam = (wh.map_id or 0) // 100
        fam_n[fam] += 1
        for it in wh.items:
            q = int(it["quality"])
            key = QBUCKET.get(q)
            if key is None:
                continue
            c = int(it["cells"] or 1)
            fam_cells[fam][key] += c
            fam_count[fam][key] += 1
            cell_hist[c] += 1
        used += 1

    if not used:
        print(f"no samples matched for hero={hero}")
        return 1

    def per_quality_means(fam: int) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for k in QKEYS:
            n = fam_count[fam][k]
            out[k] = (fam_cells[fam][k] / n) if n else None
        return out

    print(f"=== per-item cells audit (hero={hero}, warehouses={used}) ===\n")
    hdr = (
        f"{'family':>7s} {'whs':>4s} {'items':>6s} "
        f"{'itemWtd':>8s} {'unwMean':>8s} {'mixWtd':>7s} | "
        + " ".join(f"{k:>6s}" for k in QKEYS)
    )
    print(hdr)
    print("-" * len(hdr))

    agg_cells: dict[str, int] = defaultdict(int)
    agg_count: dict[str, int] = defaultdict(int)
    for fam in sorted(fam_n):
        pq = per_quality_means(fam)
        tot_cells = sum(fam_cells[fam].values())
        tot_count = sum(fam_count[fam].values())
        if tot_count == 0:
            continue
        item_wtd = tot_cells / tot_count  # the natural residual target
        present = [pq[k] for k in QKEYS if pq[k] is not None]
        unw_mean = statistics.mean(present) if present else 0.0  # mimics engine averaging
        # mix-weighted: per-quality mean weighted by quality's share of total item count
        mix = sum((pq[k] or 0.0) * (fam_count[fam][k] / tot_count) for k in QKEYS)
        for k in QKEYS:
            agg_cells[k] += fam_cells[fam][k]
            agg_count[k] += fam_count[fam][k]
        pq_str = " ".join(
            (f"{pq[k]:6.2f}" if pq[k] is not None else "   -  ") for k in QKEYS
        )
        print(
            f"{fam:>7d} {fam_n[fam]:>4d} {tot_count:>6d} "
            f"{item_wtd:>8.2f} {unw_mean:>8.2f} {mix:>7.2f} | {pq_str}"
        )

    # overall
    tot_cells = sum(agg_cells.values())
    tot_count = sum(agg_count.values())
    item_wtd = tot_cells / tot_count
    pq_all = {k: (agg_cells[k] / agg_count[k] if agg_count[k] else None) for k in QKEYS}
    present = [pq_all[k] for k in QKEYS if pq_all[k] is not None]
    unw_mean = statistics.mean(present)
    mix = sum((pq_all[k] or 0.0) * (agg_count[k] / tot_count) for k in QKEYS)
    print("-" * len(hdr))
    pq_str = " ".join(
        (f"{pq_all[k]:6.2f}" if pq_all[k] is not None else "   -  ") for k in QKEYS
    )
    print(
        f"{'ALL':>7s} {used:>4d} {tot_count:>6d} "
        f"{item_wtd:>8.2f} {unw_mean:>8.2f} {mix:>7.2f} | {pq_str}"
    )

    print("\n[quality share of total item count]")
    for k in QKEYS:
        print(f"  {k}: {agg_count[k] / tot_count:.3f}  (mean cells {pq_all[k]:.2f})" if agg_count[k] else f"  {k}: -")

    print("\n[cells/item histogram, all items]")
    tot_items = sum(cell_hist.values())
    for c in sorted(cell_hist):
        print(f"  {c:>2d} cells: {cell_hist[c]:>6d}  ({cell_hist[c] / tot_items:.3f})")

    print(
        "\nCRUX READ: engine residual per_item fallback=4.0, effective ~3.5 (unweighted per-quality mean)."
        "\n  itemWtd = total_cells/total_count is the natural residual target."
        "\n  If unwMean >> itemWtd, the engine over-counts cells/item -> under-counts residual items."
        "\n  The residual (unknown) items are mostly LOW tier, so mixWtd ~ itemWtd is the calibration goal."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
