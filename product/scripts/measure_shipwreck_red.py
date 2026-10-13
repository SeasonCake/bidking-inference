"""Measure the TRUE red (q6) distribution across settled tier105 shipwreck vaults vs what the
count prior implies — decides whether/how hard to strip Ahmed's conservative tier on activity ships.

Read-only. Parses data/samples/fatbeans_activity_20260605_shipwreck/*.json (raw captures) via the
calibration pipeline, prints per-vault settled red count/cells + raw/normalized total value + the
prior-implied red (official tier105 q6 rate x settled total count), then summary stats. Long-tail
red is normalized (memory: long-tail contaminates shipwreck truth) so the value picture isn't skewed.

Run: python scripts/measure_shipwreck_red.py
"""

from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "scripts/calibration", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import aisha_calib as C  # noqa: E402
import ahmad_ref_engine as E  # noqa: E402

SHIP_DIR = ROOT / "data/samples/fatbeans_activity_20260605_shipwreck"
SD = E.load_reference_static_data()


def _m(x):
    return f"{x/1e6:.2f}M" if x else "0"


def main():
    rows = []
    for f in sorted(glob.glob(str(SHIP_DIR / "*.json"))):
        name = Path(f).name
        hero = (re.search(r"valid_(\w+?)_", name) or [None, "?"])[1]
        mp = int((re.search(r"_(\d{4})_\d{6,}", name) or [0, 0])[1])
        try:
            ev = C._events(Path(f))
            bd = C.settlement_breakdown(ev)
            nt = C.normalized_truth(ev)
        except Exception as e:
            print(f"  SKIP {name}: {e}")
            continue
        counts = bd.get("counts") or {}
        cells = bd.get("cells") or {}
        red = int(counts.get(6, 0))
        red_cells = int(cells.get(6, 0))
        total_cnt = sum(int(v) for v in counts.values())
        probs, _ = E._quality_probabilities(mp, SD)
        prior_red = probs.get("q6", 0) * total_cnt if total_cnt else 0
        rows.append((hero, mp, red, red_cells, total_cnt, prior_red,
                     bd.get("total_value") or 0, nt.get("norm_value") or 0, nt.get("category")))

    print(f"{'hero':9} {'map':>5} {'redCnt':>6} {'redCells':>8} {'tot':>4} {'prior_red':>9} "
          f"{'rawVal':>8} {'normVal':>8} {'cat':>10}")
    for h, mp, r, rc, tot, pr, rv, nv, cat in rows:
        flag = "  <-- prior OVER" if pr - r >= 2 else ("  <-- prior UNDER" if r - pr >= 2 else "")
        print(f"{h:9} {mp:>5} {r:>6} {rc:>8} {tot:>4} {pr:>9.1f} {_m(rv):>8} {_m(nv):>8} {str(cat):>10}{flag}")

    reds = [r[2] for r in rows]
    priors = [r[5] for r in rows]
    if reds:
        import statistics as st
        print(f"\nN={len(reds)}  TRUE red: min={min(reds)} median={st.median(reds)} "
              f"mean={st.mean(reds):.1f} max={max(reds)}  dist={sorted(reds)}")
        print(f"          PRIOR red (per vault): mean={st.mean(priors):.1f}  "
              f"=> prior - true mean gap = {st.mean(priors) - st.mean(reds):+.1f}")
        zero_low = sum(1 for r in reds if r <= 2)
        print(f"          vaults with <=2 true red: {zero_low}/{len(reds)} "
              f"({100*zero_low//len(reds)}%) — these are where the ~5-6 prior over-bids")


if __name__ == "__main__":
    main()
