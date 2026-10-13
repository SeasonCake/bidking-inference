"""P1 threshold survey: random-N-item avg-value public signal vs settlement truth.

Public ids 200031/032/033/034 = avg value of a random 3/6/9/12-item sample of the warehouse
(field 9 carries the avg). The engine currently turns N*avg into a SOFT total-value floor but never
hardens it or uses it to anchor a red (q6) item. This survey measures, per map, the relationship
between the observed random-avg level and settlement truth (does a red item exist? total value?
is N*avg a safe hard lower bound?) so we can pick a defensible high-signal threshold.

Read-only. Run: python scripts/survey_random_avg_value_anchor.py
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

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402

SAMPLE_DIR = ROOT / "data/samples/fatbeans"
RANDOM_N_BY_ID = {200031: 3, 200032: 6, 200033: 9, 200034: 12}


def _random_avg_signals(events) -> dict[int, float]:
    """Best (max) random-avg value seen per sample-size N across all pre-settlement frames."""
    best: dict[int, float] = {}
    for ev in getattr(events, "states", []) or []:
        # do not read settlement frames (truth leak); pre-settlement only
        if getattr(ev, "settlement_loss_units", None) is not None:
            continue
        for pi in getattr(ev, "public_infos", ()) or ():
            n = RANDOM_N_BY_ID.get(int(pi.info_id))
            if n is None:
                continue
            val = float(pi.value or 0)
            if val > 0:
                best[n] = max(best.get(n, 0.0), val)
    return best


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
        s = _settlement_breakdown(events)
        truth_total = int(s.get("total_value") or 0)
        counts = {int(k): int(v) for k, v in (s.get("counts") or {}).items()}
        if truth_total <= 0 or not counts:
            continue
        values = {int(k): int(v) for k, v in (s.get("values") or {}).items()}
        max_avg = max(sig.values())  # highest per-item random avg observed
        floor = max(n * v for n, v in sig.items())  # tightest implied total-value floor
        red_count = counts.get(6, 0)  # q6 = red
        gold_count = counts.get(5, 0)  # q5 = gold
        red_value = values.get(6, 0)  # total value held by red(q6) items
        rows.append(
            {
                "map": _map_id_from_events(events),
                "max_avg": max_avg,
                "floor": floor,
                "truth_total": truth_total,
                "floor_ok": floor <= truth_total,  # hard-floor safety (must always hold)
                "has_red": red_count > 0,
                "red_count": red_count,
                "red_value": red_value,
                "red_value_share": red_value / truth_total if truth_total else 0.0,
                "avg_red_value": red_value / red_count if red_count else 0.0,
                "has_gold_or_red": (red_count + gold_count) > 0,
            }
        )

    print(f"samples with random-avg signal: {len(rows)}")
    if not rows:
        return 0

    # 1) Hard-floor safety: is N*avg ever ABOVE truth total value?
    viol = [r for r in rows if not r["floor_ok"]]
    print(f"\n[hard-floor safety] floor>truth_total violations: {len(viol)}/{len(rows)}")
    for r in viol[:8]:
        print(f"   map {r['map']} floor={r['floor']:.0f} > truth={r['truth_total']:.0f}")

    # 2) Red-presence likelihood by max-avg bucket
    print("\n[red(q6) presence by max random per-item avg]")
    buckets = [(0, 5e4), (5e4, 1e5), (1e5, 3e5), (3e5, 1e6), (1e6, 1e12)]
    labels = ["<5w", "5-10w", "10-30w", "30-100w", ">=100w"]
    print(f"{'bucket':10s} {'n':>3s} {'P(red)':>7s} {'P(gold|red)':>11s} {'med_total':>10s}")
    for (lo, hi), lab in zip(buckets, labels):
        b = [r for r in rows if lo <= r["max_avg"] < hi]
        if not b:
            print(f"{lab:10s} {0:>3d}")
            continue
        pr = sum(r["has_red"] for r in b) / len(b)
        pgr = sum(r["has_gold_or_red"] for r in b) / len(b)
        med = statistics.median(r["truth_total"] for r in b)
        print(f"{lab:10s} {len(b):>3d} {pr:>7.2f} {pgr:>11.2f} {med:>10.0f}")

    # 2b) Under HIGH avg: how many reds, and how concentrated is value in reds?
    # Informs how many reds to anchor and at what value (vs hugging a flat total floor).
    print("\n[red COUNT + value concentration under high random avg]")
    for thr in (50_000, 100_000):
        hi = [r for r in rows if r["max_avg"] >= thr]
        if not hi:
            print(f"avg>={thr//10000}w: n=0")
            continue
        from collections import Counter

        hist = Counter(r["red_count"] for r in hi)
        hist_str = " ".join(f"{k}reds:{v}" for k, v in sorted(hist.items()))
        med_rc = statistics.median(r["red_count"] for r in hi)
        med_share = statistics.median(r["red_value_share"] for r in hi)
        med_avgred = statistics.median(r["avg_red_value"] for r in hi if r["red_count"])
        med_floor = statistics.median(r["floor"] for r in hi)
        print(
            f"avg>={thr//10000}w: n={len(hi)} med_redcount={med_rc:.0f} "
            f"red_value_share(med)={med_share:.2f} med_avg_red_value={med_avgred:.0f} "
            f"med_floor(N*avg)={med_floor:.0f}"
        )
        print(f"          red-count histogram: {hist_str}")

    # 3) Per-map view (where signal present)
    print("\n[per-map: signal samples, avg range, P(red)]")
    by_map = defaultdict(list)
    for r in rows:
        by_map[r["map"]].append(r)
    print(f"{'map':>5s} {'n':>3s} {'avg_min':>8s} {'avg_max':>9s} {'P(red)':>7s} {'med_total':>10s}")
    for m in sorted(by_map):
        b = by_map[m]
        amin = min(r["max_avg"] for r in b)
        amax = max(r["max_avg"] for r in b)
        pr = sum(r["has_red"] for r in b) / len(b)
        med = statistics.median(r["truth_total"] for r in b)
        print(f"{m:>5d} {len(b):>3d} {amin:>8.0f} {amax:>9.0f} {pr:>7.2f} {med:>10.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
