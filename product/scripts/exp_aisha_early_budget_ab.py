"""A/B: does multiplying EARLY-round (R1-3) max_combos by N change aisha estimate/band?

For a focused set of samples whose early rounds touch the combo budget ceiling at baseline,
re-run R1-3 at baseline budget and at budget*MULT, and report combo_count, balanced (ref),
band width, and total_count_estimate side by side. Also reports settlement truth total_value as
a coarse anchor (early rounds aren't expected to match settlement, but a big swing toward/away
from it is informative). Read-only.

Usage: python scripts/exp_aisha_early_budget_ab.py [MULT] [N_FILES]
"""
from __future__ import annotations
import sys
from pathlib import Path
for p in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, p)
import audit_aisha_gap as a  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from ahmad_ref_engine import run_reference_engine  # noqa: E402

MULT = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
N_FILES = int(sys.argv[2]) if len(sys.argv) > 2 else 12
EARLY = (1, 2, 3)


def band(res):
    bal = res.get("balanced")
    aggr = res.get("aggressive")
    try:
        if bal and float(bal) > 0:
            return (float(aggr) - float(bal)) / float(bal)
    except Exception:
        pass
    return None


def run_at(snap, budget):
    return run_reference_engine(snap, max_combos=budget).as_dict()


def main():
    files = []
    for root in a.SAMPLE_ROOTS:
        if root.is_dir():
            files.extend(sorted(root.glob("fatbeans*aisha*.json")))
    picked = 0
    print(f"MULT={MULT}  scanning for early budget-touched samples (target {N_FILES})\n")
    print(f"{'file':36} {'rnd':>3} {'truthVal':>9} | {'combo_b':>7} {'bal_b':>9} {'band_b':>7} {'tc_b':>4} | {'combo_x':>7} {'bal_x':>9} {'band_x':>7} {'tc_x':>4} | {'dCombo':>7} {'dBal%':>7}")
    for path in files:
        try:
            events = a.parse_fatbeans_capture(path)
        except Exception:
            continue
        map_id = a._map_id_from_events(events)
        if str(map_id).startswith("26"):
            continue
        settlement = a._settlement_breakdown(events)
        truth_val = int(settlement["total_value"]) if settlement else None
        pre = [b for b in a.live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        # quick baseline check at R2 to decide budget-touch
        touched = False
        rows = []
        for r in EARLY:
            if r > len(pre):
                continue
            pb = pre[:r]
            sid = a._prefix_sort_id(pb)
            pe = a._events_through_sort(events, sid) if sid else events
            try:
                snap = a._build_snapshot(hero="aisha", events=pe, prefix_batches=pb, map_id=map_id)
            except Exception:
                continue
            base_budget = hero_max_combos_for_round("aisha", r)
            res_b = run_at(snap, base_budget)
            if res_b.get("combo_count") and res_b["combo_count"] >= base_budget:
                touched = True
            res_x = run_at(snap, int(base_budget * MULT))
            rows.append((r, truth_val, res_b, res_x))
        if not touched:
            continue
        picked += 1
        for (r, tv, rb, rx) in rows:
            cb, cx = rb.get("combo_count"), rx.get("combo_count")
            bb, bx = rb.get("balanced"), rx.get("balanced")
            wb, wx = band(rb), band(rx)
            tcb, tcx = rb.get("total_count_estimate"), rx.get("total_count_estimate")
            dcombo = (cx - cb) if (isinstance(cb, int) and isinstance(cx, int)) else None
            dbal = (100 * (bx - bb) / bb) if (bb and bx) else None
            print(f"{path.name[:36]:36} {r:>3} {str(tv):>9} | {str(cb):>7} {str(bb):>9} {(f'{wb:.3f}' if wb is not None else 'n/a'):>7} {str(tcb):>4} | {str(cx):>7} {str(bx):>9} {(f'{wx:.3f}' if wx is not None else 'n/a'):>7} {str(tcx):>4} | {str(dcombo):>7} {(f'{dbal:+.2f}' if dbal is not None else 'n/a'):>7}")
        if picked >= N_FILES:
            break
    print(f"\npicked {picked} early-budget-touched files")


if __name__ == "__main__":
    main()
