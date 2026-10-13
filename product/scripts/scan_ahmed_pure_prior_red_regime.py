"""Characterize the 'pure-prior red' regime across ALL rounds for real ahmed ships.

The conservative red strip (AHMED_CONS_PURE_PRIOR_RED_STRIP) only fires when
_is_pure_prior_red(evidence) is True AND tier105 AND hero==ahmed. measure_ahmed_ship_strip.py
only probes R4 (decision round). This scans R1..R_last per ship and records, per (ship, round):
  - pure_prior (engine's own _is_pure_prior_red on the real evidence)
  - strip fired (ahmed_cons_pure_prior_red_strip note present)
  - conservative on/off delta
  - revealed q6 (fixed/min) so we can SEE when red stops being pure-prior

Goal: in which rounds is red pure-prior? what fraction of all (ship, round) cells? Read-only
(toggles env only). Run: PYTHONIOENCODING=utf-8 python scripts/scan_ahmed_pure_prior_red_regime.py
"""
from __future__ import annotations

import glob
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("scripts", "scripts/calibration", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, str(ROOT / p))

import aisha_calib as C  # noqa: E402
from audit_aisha_gap import _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    prepare_reference_engine_snapshot,
    run_reference_engine,
    _is_pure_prior_red,
    _map_tier,
)
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

DATA3 = r"C:/Users/example/Desktop/data (3)/data/logs/live/raw/archive/reset"
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")
DIS = "AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"
ENV = "AHMED_CONS_PURE_PRIOR_RED_KEEP"


def _m(x):
    return f"{x/1e6:.2f}" if x else "0.00"


def _ship_captures():
    out = []
    for mp in SHIP_MAPS:
        out += glob.glob(f"{DATA3}/*_{mp}_*reset.json")
    return sorted(out)


def _run(snap, strip_on):
    if strip_on:
        os.environ.pop(DIS, None)
        os.environ[ENV] = "0.50"
    else:
        os.environ[DIS] = "1"
        os.environ.pop(ENV, None)
    r = run_reference_engine(snap, max_combos=hero_max_combos_for_round("ahmed", 4))
    os.environ.pop(DIS, None)
    os.environ.pop(ENV, None)
    return r


def main():
    caps = _ship_captures()
    print(f"ahmed ship captures: {len(caps)}\n")

    # per (ship, round) records
    cells = []  # dicts
    per_round = defaultdict(lambda: {"n": 0, "pure": 0, "fired": 0})
    ship_rows = []

    for f in caps:
        try:
            ev = C._events(Path(f))
            if not C.settlement_is_valid(ev):
                print(f"  SKIP (invalid settle) {Path(f).name[:46]}")
                continue
            bd = C.settlement_breakdown(ev)
            t_red = int((bd.get("counts") or {}).get(6, 0))
            mp = _map_id_from_events(ev)
            tier = _map_tier(mp)
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            if not pre:
                print(f"  SKIP (no pre batches) {Path(f).name[:46]}")
                continue
            n_rounds = len(pre)
            row_marks = []
            for rno in range(1, n_rounds + 1):
                snap_raw = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=rno, map_id=mp, hero="ahmed")
                snap = prepare_reference_engine_snapshot(snap_raw)
                r_on = _run(snap, True)
                # pure-prior is evidence-derived (strip on/off cannot change evidence); use the on run's evidence
                evid = r_on.evidence
                pure = bool(_is_pure_prior_red(evid)) if evid is not None else False
                fired = any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in (r_on.notes or ()))
                rev_q6 = 0
                if evid is not None:
                    rev_q6 = int((evid.fixed_counts or {}).get("q6", 0) or 0) or int((evid.min_counts or {}).get("q6", 0) or 0)
                cons_on = r_on.conservative
                cons_off = None
                if fired:
                    r_off = _run(snap, False)
                    cons_off = r_off.conservative
                rec = {
                    "map": mp, "tier": tier, "round": rno, "n_rounds": n_rounds,
                    "t_red": t_red, "pure": pure, "fired": fired, "rev_q6": rev_q6,
                    "cons_on": cons_on, "cons_off": cons_off,
                    "is_R4": rno == min(n_rounds, 4),
                    "is_Rlast": rno == n_rounds,
                    "file": Path(f).name,
                }
                cells.append(rec)
                # round bucket: use absolute round number, capped at 6+
                rb = rno if rno <= 6 else 7
                per_round[rb]["n"] += 1
                per_round[rb]["pure"] += int(pure)
                per_round[rb]["fired"] += int(fired)
                row_marks.append(("P" if pure else ".") + ("!" if fired else ""))
            ship_rows.append({"map": mp, "tier": tier, "t_red": t_red, "n_rounds": n_rounds,
                              "marks": row_marks, "file": Path(f).name})
        except Exception as e:
            print(f"  ERR {Path(f).name[:40]}: {type(e).__name__}: {str(e)[:70]}")

    total = len(cells)
    n_pure = sum(1 for c in cells if c["pure"])
    n_fired = sum(1 for c in cells if c["fired"])
    print(f"\n=== TOTAL (ship,round) cells = {total} over {len(ship_rows)} ships ===")
    print(f"pure-prior red cells: {n_pure}/{total} = {100*n_pure/max(1,total):.1f}%")
    print(f"strip FIRED cells:    {n_fired}/{total} = {100*n_fired/max(1,total):.1f}%")
    print(f"  (fired requires pure AND tier105 AND a non-zero red value to strip; pure>fired when tier!=105"
          f" or rv50<=0)")

    # by absolute round
    print(f"\n=== pure-prior & fire rate by ABSOLUTE round ===")
    print(f"{'round':>6} {'n':>4} {'pure':>6} {'pure%':>6} {'fired':>6} {'fired%':>7}")
    for rb in sorted(per_round):
        d = per_round[rb]
        lbl = str(rb) if rb <= 6 else "7+"
        print(f"{lbl:>6} {d['n']:>4} {d['pure']:>6} {100*d['pure']/max(1,d['n']):>5.0f}% "
              f"{d['fired']:>6} {100*d['fired']/max(1,d['n']):>6.0f}%")

    # by tier (105 vs other) — strip is tier105-gated
    by_tier = defaultdict(lambda: {"n": 0, "pure": 0, "fired": 0})
    for c in cells:
        t = by_tier[c["tier"]]
        t["n"] += 1; t["pure"] += int(c["pure"]); t["fired"] += int(c["fired"])
    print(f"\n=== by tier (strip gates on tier==105) ===")
    for t in sorted(by_tier, key=lambda x: str(x)):
        d = by_tier[t]
        print(f"  tier {str(t):>5}: n={d['n']:>3}  pure={d['pure']:>3} ({100*d['pure']/max(1,d['n']):.0f}%)  "
              f"fired={d['fired']:>3} ({100*d['fired']/max(1,d['n']):.0f}%)")

    # R4-only (matches measure scaffold) and Rlast
    r4 = [c for c in cells if c["is_R4"]]
    rl = [c for c in cells if c["is_Rlast"]]
    print(f"\n=== decision-round (R4 = min(n,4)) cross-check ===")
    print(f"  R4 cells: {len(r4)}  pure={sum(c['pure'] for c in r4)}  fired={sum(c['fired'] for c in r4)}")
    print(f"  Rlast cells: {len(rl)}  pure={sum(c['pure'] for c in rl)}  fired={sum(c['fired'] for c in rl)}")

    # per-ship round-by-round map (P=pure, !=fired)
    print(f"\n=== per-ship round map (col = round; P=pure-prior red, !=strip fired, .=evidenced) ===")
    print(f"{'map':>5} {'tier':>4} {'tRed':>4} {'rounds':>6} | R1 R2 R3 ... (R4 starred if =decision)")
    for s in sorted(ship_rows, key=lambda x: (x["map"], x["file"])):
        marks = " ".join(f"{m:>2}" for m in s["marks"])
        print(f"{s['map']:>5} {str(s['tier']):>4} {s['t_red']:>4} {s['n_rounds']:>6} | {marks}")

    # when does pure->evidenced transition happen? first evidenced round per ship
    print(f"\n=== first round where red becomes EVIDENCED (pure-prior ends) ===")
    trans = []
    for s in ship_rows:
        first_ev = None
        for i, m in enumerate(s["marks"], start=1):
            if not m.startswith("P"):
                first_ev = i
                break
        trans.append(first_ev)
        # already printed in map; collect for summary
    never = sum(1 for t in trans if t is None)
    evd = [t for t in trans if t is not None]
    print(f"  ships where red is evidenced by some round: {len(evd)}/{len(trans)}")
    if evd:
        from collections import Counter
        print(f"  distribution of first-evidenced round: {dict(sorted(Counter(evd).items()))}")
    print(f"  ships pure-prior in EVERY round: {never}/{len(trans)}")


if __name__ == "__main__":
    main()
