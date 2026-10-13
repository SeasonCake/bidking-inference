"""Monte-Carlo: what drives a HIGH random-N-item avg-value draw? (red-cause analysis)

The public signal 200031/032/033/034 = avg value of a random 3/6/9/12-item draw from the
warehouse. Observed high-signal samples are sparse (~15), so instead we SIMULATE the signal:
take every settled warehouse (ground truth, all ~600 samples), draw random N-item subsets
ourselves, and measure — conditioned on the draw avg being high — how many RED(q6) items were in
the draw (i.e. what causes the high average) and the warehouse red composition. This is reusable
and decoupled from the sparse observed signal; it is the same Monte-Carlo idea as the planned
simulated-sample work.

Read-only. Run: python scripts/simulate_random_avg_red_cause.py [draws_per_cell]
"""

from __future__ import annotations

import glob
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_aisha_gap import _item_values  # noqa: E402
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402

SAMPLE_DIR = ROOT / "data/samples/fatbeans"
DRAW_SIZES = (3, 6, 9)
RED_Q = 6
SEED = 1234  # deterministic; reproducible across runs


def _settled_items(events, values_by_id) -> list[tuple[int, int]]:
    """(quality, value) for each item in the settled warehouse, or [] if none."""
    for state in reversed(events.states):
        if not state.inventory_items:
            continue
        out = []
        for item in state.inventory_items:
            q = int(item.quality or 0)
            if q <= 0:
                continue
            out.append((q, int(values_by_id.get(int(item.item_id), 0))))
        return out
    return []


def main(argv: list[str]) -> int:
    draws_per_cell = int(argv[1]) if len(argv) > 1 else 300
    values_by_id = _item_values()
    rng = random.Random(SEED)

    # one record per simulated draw
    draws: list[dict] = []
    n_warehouses = 0
    for f in sorted(glob.glob(str(SAMPLE_DIR / "*.json"))):
        try:
            events = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        items = _settled_items(events, values_by_id)
        if len(items) < min(DRAW_SIZES):
            continue
        n_warehouses += 1
        wh_red = sum(1 for q, _ in items if q == RED_Q)
        wh_total = sum(v for _, v in items)
        for n in DRAW_SIZES:
            if len(items) < n:
                continue
            for _ in range(draws_per_cell):
                sample = rng.sample(items, n)
                draw_sum = sum(v for _, v in sample)
                avg = draw_sum / n
                reds = sum(1 for q, _ in sample if q == RED_Q)
                red_value_in_draw = sum(v for q, v in sample if q == RED_Q)
                top_value = max(v for _, v in sample)
                draws.append(
                    {
                        "n": n,
                        "avg": avg,
                        "draw_sum": draw_sum,
                        "reds_in_draw": reds,
                        "red_value_in_draw": red_value_in_draw,
                        "top_value": top_value,
                        "wh_red": wh_red,
                        "wh_total": wh_total,
                    }
                )

    print(f"warehouses={n_warehouses}  simulated draws={len(draws)}  (seed={SEED}, {draws_per_cell}/cell/N)")

    # Core question: conditioned on a HIGH draw avg, was it caused by a red in the draw?
    buckets = [(0, 5e4), (5e4, 1e5), (1e5, 3e5), (3e5, 1e6), (1e6, 1e15)]
    labels = ["<5w", "5-10w", "10-30w", "30-100w", ">=100w"]
    print("\n[per draw-avg bucket: P(>=1 red IN the draw), reds-in-draw, warehouse reds]")
    print(f"{'bucket':9s} {'n':>7s} {'P(red_in)':>9s} {'med_reds_in':>11s} {'med_wh_red':>10s} {'P(wh_red>=2)':>12s}")
    for (lo, hi), lab in zip(buckets, labels):
        b = [d for d in draws if lo <= d["avg"] < hi]
        if not b:
            print(f"{lab:9s} {0:>7d}")
            continue
        p_red_in = sum(d["reds_in_draw"] > 0 for d in b) / len(b)
        med_in = statistics.median(d["reds_in_draw"] for d in b)
        med_wh = statistics.median(d["wh_red"] for d in b)
        p_wh2 = sum(d["wh_red"] >= 2 for d in b) / len(b)
        print(f"{lab:9s} {len(b):>7d} {p_red_in:>9.2f} {med_in:>11.0f} {med_wh:>10.0f} {p_wh2:>12.2f}")

    # How much of the draw value does the red carry? (sets the per-red value anchor vs N*avg floor)
    print("\n[among HIGH-avg (>=10w) draws: red value share of the draw, and top item = red?]")
    print(f"{'N':>3s} {'n':>6s} {'med red_val/draw_sum':>20s} {'med top/draw_sum':>17s} {'med red_val(=anchor)':>21s}")
    for n in DRAW_SIZES:
        hi = [d for d in draws if d["n"] == n and d["avg"] >= 1e5 and d["reds_in_draw"] > 0]
        if not hi:
            continue
        med_share = statistics.median(d["red_value_in_draw"] / d["draw_sum"] for d in hi if d["draw_sum"])
        med_topshare = statistics.median(d["top_value"] / d["draw_sum"] for d in hi if d["draw_sum"])
        med_redval = statistics.median(d["red_value_in_draw"] for d in hi)
        print(f"{n:>3d} {len(hi):>6d} {med_share:>20.2f} {med_topshare:>17.2f} {med_redval:>21.0f}")

    # By N separately (does the relationship hold across draw sizes the engine reports?)
    print("\n[by draw size N: among HIGH-avg (>=10w) draws, reds-in-draw histogram]")
    for n in DRAW_SIZES:
        hi = [d for d in draws if d["n"] == n and d["avg"] >= 1e5]
        if not hi:
            print(f"N={n}: (no >=10w draws)")
            continue
        hist = Counter(d["reds_in_draw"] for d in hi)
        hist_str = " ".join(f"{k}:{v}" for k, v in sorted(hist.items()))
        p_red_in = sum(d["reds_in_draw"] > 0 for d in hi) / len(hi)
        print(f"N={n}: n={len(hi)} P(red_in)={p_red_in:.2f} reds-in-draw histogram: {hist_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
