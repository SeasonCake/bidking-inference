"""Enhanced random-avg -> red mapping survey (2026-06-19).

Extends scripts/survey_random_avg_value_anchor.py. The original keys the red anchor on the
PER-ITEM avg (>=100k) which almost never fires (top bucket had n=1). The real, abundant signal is
the IMPLIED DRAW TOTAL = floor = max(N * avg) -- the value of the random N-item draw, which is an
absolute lower bound on warehouse value (those N items are in the warehouse) and whose magnitude is
driven by whether a red was drawn.

This builds the empirical map: floor bucket -> P(red), red value distribution (p25/50/75),
red cells, and the ratio (red_value / floor) so we can calibrate the anchor value. It also measures
the FALSE-ANCHOR risk: buckets where floor is moderate but the warehouse has no red / a tiny red.

Read-only. Excludes 26xx (隐秘/hidden super-rich maps) per project caliber rule.
Run: python scripts/survey_random_avg_red_mapping.py
"""

from __future__ import annotations

import glob
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402

SAMPLE_DIR = ROOT / "data/samples/fatbeans"
RANDOM_N_BY_ID = {200031: 3, 200032: 6, 200033: 9, 200034: 12}


def _hero_from_path(f: str) -> str:
    name = Path(f).name
    for h in ("aisha", "ahmed", "maria", "ethan", "gabriela", "sophie", "wuqilin"):
        if f"_{h}_" in name:
            return h
    return "?"


def _random_avg_signals(events) -> dict[int, float]:
    best: dict[int, float] = {}
    for ev in getattr(events, "states", []) or []:
        if getattr(ev, "settlement_loss_units", None) is not None:
            continue  # never read settlement frames (truth leak)
        for pi in getattr(ev, "public_infos", ()) or ():
            n = RANDOM_N_BY_ID.get(int(pi.info_id))
            if n is None:
                continue
            val = float(pi.value or 0)
            if val > 0:
                best[n] = max(best.get(n, 0.0), val)
    return best


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    import math

    i = q * (len(xs) - 1)
    lo = math.floor(i)
    hi = math.ceil(i)
    if lo == hi:
        return float(xs[lo])
    return float(xs[lo] + (xs[hi] - xs[lo]) * (i - lo))


def main() -> int:
    rows = []
    for f in sorted(glob.glob(str(SAMPLE_DIR / "*.json"))):
        try:
            events = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        sig = _random_avg_signals(events)
        if not sig:
            continue
        mp = _map_id_from_events(events)
        if mp is not None and 2600 <= int(mp) <= 2699:
            continue  # exclude 26xx hidden super-rich
        s = _settlement_breakdown(events)
        truth_total = int(s.get("total_value") or 0)
        counts = {int(k): int(v) for k, v in (s.get("counts") or {}).items()}
        if truth_total <= 0 or not counts:
            continue
        values = {int(k): int(v) for k, v in (s.get("values") or {}).items()}
        cells = {int(k): int(v) for k, v in (s.get("cells") or {}).items()}
        max_avg = max(sig.values())
        floor = max(n * v for n, v in sig.items())
        red_count = counts.get(6, 0)
        gold_count = counts.get(5, 0)
        red_value = values.get(6, 0)
        red_cells = cells.get(6, 0)
        rows.append(
            {
                "map": mp,
                "hero": _hero_from_path(f),
                "max_avg": max_avg,
                "floor": floor,
                "truth_total": truth_total,
                "floor_ok": floor <= truth_total,
                "has_red": red_count > 0,
                "red_count": red_count,
                "gold_count": gold_count,
                "red_value": red_value,
                "red_cells": red_cells,
                "red_value_share": red_value / truth_total if truth_total else 0.0,
                "red_over_floor": red_value / floor if floor else 0.0,
                "avg_red_value": red_value / red_count if red_count else 0.0,
            }
        )

    print(f"samples (non-26xx) with random-avg signal: {len(rows)}")
    if not rows:
        return 0

    # de-dup note: many rows are different rounds of the SAME match -> floor/avg vary by round
    # (later rounds reveal more). We keep all rows: each is an independent (signal, truth) pair
    # for that frame, which is exactly what the engine sees per round.

    # ---- A) Hard-floor safety on floor (N*avg) ----
    viol = [r for r in rows if not r["floor_ok"]]
    print(f"\n[A hard-floor safety] floor>truth violations: {len(viol)}/{len(rows)}")
    for r in sorted(viol, key=lambda r: r["floor"] - r["truth_total"], reverse=True)[:10]:
        print(
            f"   map {r['map']} {r['hero']:9s} floor={r['floor']:>9.0f} "
            f"truth={r['truth_total']:>9.0f} red_cnt={r['red_count']}"
        )

    # ---- B) Map keyed on FLOOR (implied draw total). Finer buckets in the live range. ----
    fl_buckets = [
        (0, 50_000),
        (50_000, 100_000),
        (100_000, 150_000),
        (150_000, 200_000),
        (200_000, 300_000),
        (300_000, 500_000),
        (500_000, 1e12),
    ]
    fl_labels = ["<5w", "5-10w", "10-15w", "15-20w", "20-30w", "30-50w", ">=50w"]
    print("\n[B red(q6) mapping keyed on FLOOR = max(N*avg) = implied draw total]")
    print(
        f"{'floor':9s} {'n':>3s} {'P(red)':>6s} {'redval_p25':>10s} {'p50':>9s} {'p75':>9s} "
        f"{'redcell_p50':>11s} {'redval/floor_p50':>16s} {'no/tiny_red':>11s}"
    )
    for (lo, hi), lab in zip(fl_buckets, fl_labels):
        b = [r for r in rows if lo <= r["floor"] < hi]
        if not b:
            print(f"{lab:9s} {0:>3d}")
            continue
        pr = sum(r["has_red"] for r in b) / len(b)
        rv = [r["red_value"] for r in b if r["has_red"]]
        rvp25 = _pct(rv, 0.25) if rv else 0
        rvp50 = _pct(rv, 0.50) if rv else 0
        rvp75 = _pct(rv, 0.75) if rv else 0
        rcells = [r["red_cells"] for r in b if r["has_red"]]
        rcp50 = _pct(rcells, 0.50) if rcells else 0
        rof = [r["red_over_floor"] for r in b if r["has_red"]]
        rof50 = _pct(rof, 0.50) if rof else 0
        # false-anchor risk = share of bucket with no red OR red value < 0.5*floor (anchor too high)
        bad = sum(1 for r in b if (not r["has_red"]) or r["red_value"] < 0.5 * r["floor"])
        print(
            f"{lab:9s} {len(b):>3d} {pr:>6.2f} {rvp25:>10.0f} {rvp50:>9.0f} {rvp75:>9.0f} "
            f"{rcp50:>11.0f} {rof50:>16.2f} {bad/len(b):>11.2f}"
        )

    # ---- C) Per-item avg keyed (original axis), finer low end + P(red) and false-anchor ----
    av_buckets = [
        (0, 10_000),
        (10_000, 20_000),
        (20_000, 30_000),
        (30_000, 50_000),
        (50_000, 100_000),
        (100_000, 1e12),
    ]
    av_labels = ["<1w", "1-2w", "2-3w", "3-5w", "5-10w", ">=10w"]
    print("\n[C red(q6) mapping keyed on PER-ITEM avg = max(avg)]")
    print(
        f"{'avg':8s} {'n':>3s} {'P(red)':>6s} {'redval_p50':>10s} "
        f"{'med_floor':>10s} {'med_truth':>10s} {'P(noRed)':>8s} {'med_redcnt':>10s}"
    )
    for (lo, hi), lab in zip(av_buckets, av_labels):
        b = [r for r in rows if lo <= r["max_avg"] < hi]
        if not b:
            print(f"{lab:8s} {0:>3d}")
            continue
        pr = sum(r["has_red"] for r in b) / len(b)
        rv = [r["red_value"] for r in b if r["has_red"]]
        rvp50 = _pct(rv, 0.50) if rv else 0
        mfloor = statistics.median(r["floor"] for r in b)
        mtruth = statistics.median(r["truth_total"] for r in b)
        pnored = sum(1 for r in b if not r["has_red"]) / len(b)
        medrc = statistics.median(r["red_count"] for r in b)
        print(
            f"{lab:8s} {len(b):>3d} {pr:>6.2f} {rvp50:>10.0f} "
            f"{mfloor:>10.0f} {mtruth:>10.0f} {pnored:>8.2f} {medrc:>10.0f}"
        )

    # ---- D) The 2406 case + no-red cases above each candidate floor threshold ----
    print("\n[D false-anchor probe: samples with NO red, by floor threshold]")
    for thr in (50_000, 100_000, 150_000, 200_000, 281_000, 300_000):
        above = [r for r in rows if r["floor"] >= thr]
        nored = [r for r in above if not r["has_red"]]
        tinyred = [r for r in above if r["has_red"] and r["red_value"] < 0.3 * r["floor"]]
        print(
            f"floor>={thr//1000}k: n={len(above):>3d} P(red)={sum(r['has_red'] for r in above)/len(above) if above else 0:.2f} "
            f"noRed={len(nored)} tinyRed(<0.3*floor)={len(tinyred)}"
        )
        for r in nored[:4]:
            print(f"      NO-RED: map {r['map']} {r['hero']:9s} floor={r['floor']:.0f} truth={r['truth_total']:.0f} gold={r['gold_count']}")

    # ---- E) red_value vs floor calibration (when red present) by floor band ----
    print("\n[E calibration: red_value / floor when red present (anchor multiplier)]")
    for (lo, hi), lab in zip(fl_buckets, fl_labels):
        b = [r for r in rows if lo <= r["floor"] < hi and r["has_red"]]
        if not b:
            continue
        rof = [r["red_over_floor"] for r in b]
        # also red_value as fraction of truth_total
        rvshare = [r["red_value_share"] for r in b]
        print(
            f"{lab:9s} n={len(b):>3d} red/floor p25={_pct(rof,0.25):.2f} p50={_pct(rof,0.50):.2f} "
            f"p75={_pct(rof,0.75):.2f} | red_share_of_truth p50={_pct(rvshare,0.50):.2f}"
        )

    # ---- F) 2406 specific ----
    print("\n[F 2406 rows (the motivating case)]")
    for r in [r for r in rows if r["map"] == 2406]:
        print(
            f"   {r['hero']:9s} avg={r['max_avg']:>8.0f} floor={r['floor']:>8.0f} "
            f"truth={r['truth_total']:>8.0f} red_cnt={r['red_count']} red_val={r['red_value']:>8.0f} "
            f"red_cells={r['red_cells']} red/floor={r['red_over_floor']:.2f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
