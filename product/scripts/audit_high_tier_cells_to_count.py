"""When a high-tier quality's CELLS are known, what count center best predicts the true count?

Lever 1 (C): live 2406 R3 had gold cells=28 known but the engine gave count [4,5,7] (balanced 5) vs
the 6 truth -- the count center divides cells by too high a per-item gold size (~5.6) instead of the
real ~4.67. This audits, across settled aisha library vaults, the per-item cell size (avg = cells /
count) of gold (q5) and red (q6), and whether it grows with the known cell total (i.e. should the
divisor be constant or cell-dependent), so the cells->count center can be re-centered without the
naive `cells / low_prior` overshoot.

Read-only. Run: python scripts/audit_high_tier_cells_to_count.py [cap]
"""

from __future__ import annotations

import glob
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from synthesize_draw_hero_samples import load_warehouse  # noqa: E402

SAMPLE_DIR = ROOT / "data/samples/fatbeans"


def _per_quality_totals(wh) -> dict[int, tuple[int, int]]:
    """quality -> (count, cells) for this warehouse."""
    out: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for it in wh.items:
        q = int(it["quality"])
        out[q][0] += 1
        out[q][1] += int(it["cells"] or 1)
    return {q: (c, cells) for q, (c, cells) in out.items()}


def main(argv: list[str]) -> int:
    cap = int(argv[1]) if len(argv) > 1 else 400
    # per quality: list of (cells, count, avg)
    rows: dict[int, list[tuple[int, int, float]]] = {5: [], 6: []}
    for f in sorted(glob.glob(str(SAMPLE_DIR / "*_aisha_*.json")))[:cap]:
        wh = load_warehouse(Path(f))
        if not wh or not wh.items:
            continue
        totals = _per_quality_totals(wh)
        for q in (5, 6):
            count, cells = totals.get(q, (0, 0))
            if count > 0 and cells > 0:
                rows[q].append((cells, count, cells / count))

    for q, label in ((5, "GOLD q5"), (6, "RED q6")):
        data = rows[q]
        if not data:
            print(f"=== {label}: no samples ===\n")
            continue
        avgs = [a for _c, _n, a in data]
        print(f"=== {label} cells->count (n={len(data)} vaults with q{q}>0) ===")
        print(
            f"  per-item cells (avg=cells/count): mean={statistics.mean(avgs):.2f} "
            f"median={statistics.median(avgs):.2f} p25={_pct(avgs,25):.2f} p75={_pct(avgs,75):.2f} "
            f"min={min(avgs):.1f} max={max(avgs):.1f}"
        )
        # does per-item size grow with total cells? bucket by cells
        buckets = [(1, 6), (7, 12), (13, 20), (21, 99)]
        print("  by known cell total -> mean per-item size & count:")
        for lo, hi in buckets:
            sub = [(c, n, a) for c, n, a in data if lo <= c <= hi]
            if not sub:
                print(f"    cells {lo:>2}-{hi:<2}: n=0")
                continue
            mean_avg = statistics.mean(a for _c, _n, a in sub)
            mean_cnt = statistics.mean(n for _c, n, _a in sub)
            mean_cells = statistics.mean(c for c, _n, _a in sub)
            # what a fixed divisor would predict vs truth count
            print(
                f"    cells {lo:>2}-{hi:<2}: n={len(sub):>3} mean_cells={mean_cells:4.1f} "
                f"mean_count={mean_cnt:4.1f} mean_per_item={mean_avg:.2f}"
            )
        # best constant divisor: minimize mean |round(cells/d) - count|
        print("  constant-divisor count-center error (mean |pred-truth|):")
        best = None
        for d10 in range(25, 56):
            d = d10 / 10.0
            err = statistics.mean(abs(round(c / d) - n) for c, n, _a in data)
            if best is None or err < best[1]:
                best = (d, err)
            if d10 % 5 == 0:
                print(f"    divisor {d:.1f}: mean_count_err={err:.2f}")
        print(f"  >> best constant divisor = {best[0]:.1f} (mean_count_err={best[1]:.2f})")
        print()
    return 0


def _pct(values: list[float], pct: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
