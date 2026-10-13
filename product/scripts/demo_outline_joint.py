"""Demo: joint posterior with vs without Aisha outlines (Mansion 2407).

Scenario: Aisha is playing Mansion 2407 (109-cell warehouse). By the end
of R3 she has seen *all* white, green, and blue outlines. On R4 she
runs three readings: 极品估价 (gold value sum), 优品估价 (purple value
sum), and the warehouse-total-cells tool. She also reports the huge
band she expects for purple (1) and red (1).

Two joint inferences are run:

* **Without outlines**: the engine sees only the three rare readings +
  huge bands + warehouse cap. White/green/blue are silent: the engine
  has no idea what fraction of the 109-cell warehouse they consume, so
  the joint total of the observed buckets ends up well *below* the
  warehouse capacity — the "uncovered" cells are exactly the WGB
  items the player hasn't bound to the model.
* **With outlines**: white/green/blue are exactly pinned via Aisha's
  R1-R3 reveals. The joint total now lands at ~98-105 cells, only
  4-11 cells shy of the observed warehouse (a healthy slack for
  red value-range elasticity).

The headline metric is the "warehouse coverage gap": how many cells
remain unaccounted for after the joint top-1 hypothesis. Aisha's free
R1-R3 outlines (zero silver cost) shrink this gap by ~75-80%, which is
the part of the player's *prior* knowledge that wasn't being used in
the baseline inference.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.inference.joint import (
    JointHypothesis,
    joint_top_k_for_session,
)
from bidking_lab.inference.observation import (
    QualityBucketObs,
    SessionObs,
)

WAREHOUSE_CELLS = 109
PURPLE_VALUE_SUM = 80_000        # → 32 cells with 1 purple huge (16×2500 subtracted)
GOLD_VALUE_SUM = 180_000         # → 19 cells (no gold huge)
RED_VALUE_RANGE = (800_000, 2_500_000)   # → 16..50 cells span at 50k/cell (huge=1)

# Pinned by Aisha outlines after R3 (synthetic but plausible counts):
WHITE_PIN = dict(count=5, total_cells=9)
GREEN_PIN = dict(count=4, total_cells=8)
BLUE_PIN = dict(count=6, total_cells=14)

TOP_K = 8
WAREHOUSE_SLACK = 4
# Big per-bucket top so red's 16..50-cell band is fully explored; only
# the warehouse-total constraint trims it down once outlines are added.
PER_BUCKET_TOP = 35


def build_baseline_session() -> SessionObs:
    """Aisha session WITHOUT using R1-R3 outlines (only R4 readings)."""
    return SessionObs(
        map_id=2407,
        hero="aisha",
        warehouse_total_cells=WAREHOUSE_CELLS,
        buckets={
            4: QualityBucketObs(quality=4, value_sum=PURPLE_VALUE_SUM, huge_band="1"),
            5: QualityBucketObs(quality=5, value_sum=GOLD_VALUE_SUM, huge_band="none"),
            6: QualityBucketObs(quality=6, value_range=RED_VALUE_RANGE, huge_band="1"),
        },
    )

def build_outlines_session() -> SessionObs:
    """Same player + readings, plus the white/green/blue outline pins."""
    return SessionObs(
        map_id=2407,
        hero="aisha",
        warehouse_total_cells=WAREHOUSE_CELLS,
        buckets={
            1: QualityBucketObs(quality=1, **WHITE_PIN),
            2: QualityBucketObs(quality=2, **GREEN_PIN),
            3: QualityBucketObs(quality=3, **BLUE_PIN),
            4: QualityBucketObs(quality=4, value_sum=PURPLE_VALUE_SUM, huge_band="1"),
            5: QualityBucketObs(quality=5, value_sum=GOLD_VALUE_SUM, huge_band="none"),
            6: QualityBucketObs(quality=6, value_range=RED_VALUE_RANGE, huge_band="1"),
        },
    )


def cells_spread_for_quality(
    hyps: list[JointHypothesis], quality: int
) -> tuple[int, int, int]:
    """Return ``(min, max, max-min)`` cells in top-K hypotheses for one bucket."""
    cells = [h.per_bucket[quality].total_cells for h in hyps if quality in h.per_bucket]
    if not cells:
        return (0, 0, 0)
    return (min(cells), max(cells), max(cells) - min(cells))


def print_top_hypotheses(label: str, hyps: list[JointHypothesis]) -> None:
    print(f"  -- {label}: top-{len(hyps)} joint hypotheses --")
    header = "    {:>4s}  {:>9s}  {:>9s}".format("rank", "tot_cells", "composite")
    quals = sorted({q for h in hyps for q in h.per_bucket.keys()}, reverse=True)
    header += "".join("  {:>9s}".format(f"q{q}_c_n") for q in quals)
    print(header)
    for i, h in enumerate(hyps, start=1):
        row = "    {:>4d}  {:>9d}  {:>9.4f}".format(i, h.total_cells, h.composite)
        for q in quals:
            c = h.per_bucket.get(q)
            if c is None:
                row += "  {:>9s}".format("-")
            else:
                row += "  {:>9s}".format(f"{c.total_cells}/{c.count}")
        print(row)
    print()


def main() -> None:
    print("=== Mansion 2407 · joint posterior · with vs without outlines ===")
    print(f"warehouse_total_cells : {WAREHOUSE_CELLS}")
    print(f"purple value sum      : {PURPLE_VALUE_SUM:,}  (huge_band=1)")
    print(f"gold value sum        : {GOLD_VALUE_SUM:,}  (huge_band=none)")
    print(f"red value range       : {RED_VALUE_RANGE[0]:,}-{RED_VALUE_RANGE[1]:,}  (huge_band=1)")
    print()
    print(f"Aisha R1-R3 outline pins (white / green / blue):")
    print(f"  white  : count={WHITE_PIN['count']}, total_cells={WHITE_PIN['total_cells']}")
    print(f"  green  : count={GREEN_PIN['count']}, total_cells={GREEN_PIN['total_cells']}")
    print(f"  blue   : count={BLUE_PIN['count']}, total_cells={BLUE_PIN['total_cells']}")
    print(f"  → free budget for purple+gold+red : "
          f"{WAREHOUSE_CELLS - sum(p['total_cells'] for p in (WHITE_PIN, GREEN_PIN, BLUE_PIN))} cells")
    print()

    baseline = joint_top_k_for_session(
        build_baseline_session(),
        k=TOP_K,
        per_bucket_top=PER_BUCKET_TOP,
        warehouse_slack=WAREHOUSE_SLACK,
    )
    outlines = joint_top_k_for_session(
        build_outlines_session(),
        k=TOP_K,
        per_bucket_top=PER_BUCKET_TOP,
        warehouse_slack=WAREHOUSE_SLACK,
    )

    print_top_hypotheses("WITHOUT outlines", baseline)
    print_top_hypotheses("WITH outlines", outlines)

    print("  -- warehouse coverage gap (cells unaccounted for in the joint top-1) --")
    print("    {:>20s}  {:>10s}  {:>10s}  {:>10s}".format(
        "scenario", "top-1 sum", "warehouse", "gap (cells)"
    ))
    base_top1 = baseline[0].total_cells if baseline else 0
    out_top1 = outlines[0].total_cells if outlines else 0
    gap_base = WAREHOUSE_CELLS - base_top1
    gap_out = WAREHOUSE_CELLS - out_top1
    shrink_pct = 0.0 if gap_base == 0 else (1.0 - gap_out / gap_base) * 100
    print("    {:>20s}  {:>10d}  {:>10d}  {:>10d}".format(
        "WITHOUT outlines", base_top1, WAREHOUSE_CELLS, gap_base,
    ))
    print("    {:>20s}  {:>10d}  {:>10d}  {:>10d}".format(
        "WITH outlines", out_top1, WAREHOUSE_CELLS, gap_out,
    ))
    print("    {:>20s}  {:>10s}  {:>10s}  {:>9.1f}%".format(
        "gap shrink", "—", "—", shrink_pct,
    ))
    print()
    print("  -- spread of top-{} total_cells per rare bucket --".format(TOP_K))
    print("    {:>9s}  {:>15s}  {:>15s}".format(
        "bucket", "without outlines", "with outlines"
    ))
    for q in (4, 5, 6):
        lo_b, hi_b, sp_b = cells_spread_for_quality(baseline, q)
        lo_o, hi_o, sp_o = cells_spread_for_quality(outlines, q)
        print("    {:>9s}  {:>5s}  ({:>2d}..{:<2d})  {:>5s}  ({:>2d}..{:<2d})".format(
            f"q{q}",
            f"spread={sp_b}", lo_b, hi_b,
            f"spread={sp_o}", lo_o, hi_o,
        ))
    print()
    print("Insight: Aisha's free R1-R3 outline reveals shrink the warehouse")
    print("coverage gap by {:.0f}% — the engine no longer leaves 30+ cells".format(shrink_pct))
    print("unexplained at zero silver cost. The per-bucket cells spread for")
    print("the rare qualities is unchanged in this scenario because the")
    print("value-side readings already pin purple/gold tightly; what outlines")
    print("buy you here is honest accounting of the *low-tier* footprint,")
    print("which sharpens any downstream value-density estimate that needs to")
    print("normalise per cell.")


if __name__ == "__main__":
    main()
