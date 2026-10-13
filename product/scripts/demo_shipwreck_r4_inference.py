"""End-to-end demo: shipwreck R4 inference from the 2026-05-15 screenshot.

Inputs (read directly off the screenshot):

* Map: shipwreck-tier (e.g. 2510 / 2507)
* Hero: 伊森 (he scans blue + white-green totals automatically)
* Round info:
    - 良品扫描:  蓝品总占位 = 18 格
    - 普品扫描:  白绿总占位 = 15 格
    - 优品均格:  紫品 "约2.5格"
    - 优品估价:  紫品总价 = 86,490 银币
    - (player has not used 巨物 tools; reasonable assumption: no
      purple huge items in this round)
* Cabinet capacity (player has chosen a large warehouse): assume 159
  cells (the same shipwreck map used in another screenshot).

Goal: recover the (total_cells, count) for the purple bucket and rank
the top-3 candidates.
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

from bidking_lab.inference.display import parse_reading
from bidking_lab.inference.observation import (
    QualityBucketObs,
    SessionObs,
    top_k_for_session,
)


def main() -> None:
    # Reconstruct the screenshot scene.
    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells=159,   # the player saw this from a previous tool
        buckets={
            # 普品扫描 reveals 白+绿 share 15 cells; we treat them as one
            # bucket (quality=1) for the demo since the screenshot doesn't
            # split them. A real UI would model 白 and 绿 separately or
            # let the player tick "combined white+green scan".
            1: QualityBucketObs(quality=1, total_cells=15),
            3: QualityBucketObs(quality=3, total_cells=18),
            4: QualityBucketObs(
                quality=4,
                avg_cells=parse_reading("2.5"),
                value_sum=86_490,
                huge_band="none",   # no purple 巨物 reported
            ),
            # The screenshot doesn't show gold/red info; leave empty.
        },
    )

    print("=== Shipwreck R4 inference demo ===")
    print(f"map_id           : {session.map_id}")
    print(f"hero             : {session.hero}")
    print(f"warehouse cells  : {session.warehouse_total_cells}")
    print(f"purple avg cells : {session.buckets[4].avg_cells.raw}")
    print(f"purple value sum : {session.buckets[4].value_sum:,}")
    print()
    print("Top-K candidates per quality bucket:")
    print()

    results = top_k_for_session(session, k=3)
    for q, cands in results.items():
        if not cands:
            continue
        print(f"-- quality {q} ({'白绿蓝紫金红'[q-1]}) --")
        print(
            f"  {'rank':>4s}  {'total':>6s}  {'count':>5s}  {'avg√':>5s}  "
            f"{'val_score':>9s}  {'cells_score':>11s}  {'composite':>9s}"
        )
        for i, c in enumerate(cands, start=1):
            mark = "✓" if c.avg_match else "x"
            print(
                f"  {i:>4d}  {c.total_cells:>6d}  {c.count:>5d}  {mark:>5s}  "
                f"{c.value_score:>9.4f}  {c.cells_score:>11.4f}  {c.composite:>9.4f}"
            )
        print()

    purple_top = results.get(4, [])
    if purple_top:
        top = purple_top[0]
        print(
            f"Inference: purple bucket has likely {top.count} items occupying "
            f"{top.total_cells} cells."
        )
        print(
            f"(Avg cells reading 2.5 + value sum 86,490 / 2,500 per cell → "
            f"~35 cells; matches {top.total_cells}/{top.count} = "
            f"{top.total_cells/top.count:.2f} cells per item.)"
        )


if __name__ == "__main__":
    main()
