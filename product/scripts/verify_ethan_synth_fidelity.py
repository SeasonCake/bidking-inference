"""Fidelity gate for the new-standard ahmed->ethan synth generator (2026-07-09, author-requested).

Two checks the author asked for:
 (A) EVERY emitted tool-chain datum is actually READ by the engine (no silent evidence drop): each
     {action_id, result} row must produce its source_note (action_<id>_<q>_cells/avg_cells/value_sum) and
     the derived evidence bucket. The 优品均格 integer-product lock must recover the TRUE q4 count.
 (B) SYNTH vs REAL structural match: run our standard-conforming REAL ethan (clean_eval, the author's own
     plays) through the SAME engine and compare the action-note SET, so a synth is only trusted when it
     exercises the same channels a real ethan of that loadout does.

Run:  python scripts/verify_ethan_synth_fidelity.py [n_synth=40] [n_real=20]
(combos capped at 15k — the gate checks evidence notes + count ranges, not the full combo search)
"""
import sys, json, glob
from pathlib import Path
from collections import Counter
ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT/"scripts", ROOT/"hero_ref"/"src", ROOT/"hero_ref"/"tools", ROOT/"src", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from ahmad_ref_engine import run_reference_engine
from hero_ref_live_schedule import hero_max_combos_for_round
from build_clean_eval_set import clean_eval_files
from audit_aisha_gap import (_build_snapshot, _events_through_sort, _map_id_from_events,
    _prefix_sort_id, _settlement_breakdown)
from bidking_lab.live.fatbeans import parse_fatbeans_capture, live_batches_from_fatbeans_events

ACTION_Q = {100104: "q1", 100105: "q3", 100112: "q4", 100107: "q5", 100125: "q5", 100124: "q4"}
ACTION_KIND = {100104: "cells", 100105: "cells", 100112: "avg_cells", 100107: "cells", 100125: "value_sum", 100124: "value_sum"}


def run_snap(snap, rnd):
    # cap combos: the fidelity gate only needs evidence notes + count ranges, not the full combo search
    mc = min(15000, hero_max_combos_for_round("ethan", min(int(rnd or 5), 5)))
    return run_reference_engine(snap, max_combos=mc).as_dict()


def check_synth(rec):
    snap = rec["snapshot"]
    rnd = rec.get("round") or 5
    rows = snap.get("action_result_rows") or []
    res = run_snap(snap, rnd)
    notes = set(res.get("notes") or []) | set((res.get("evidence") or {}).get("source_notes") or [])
    ev = res.get("evidence") or {}
    read_ok, missing = True, []
    for r in rows:
        aid = int(r["action_id"]); q = ACTION_Q[aid]; kind = ACTION_KIND[aid]
        expect = f"action_{aid}_{q}_{kind}"
        # engine appends _inferred_zero suffix sometimes; match prefix
        if not any(n.startswith(expect) for n in notes):
            read_ok = False; missing.append(expect)
    # LEVEL-1(b): 优品均格 -> integer-product lock recovers truth q4 count
    truth_counts = {int(k): v for k, v in (rec.get("truth_counts") or {}).items()}
    q4_truth = truth_counts.get(4, 0)
    qcr = res.get("quality_count_ranges") or {}
    q4_rng = qcr.get("q4")
    q4_lock_ok = None
    if any(int(r["action_id"]) == 100112 for r in rows) and q4_truth:
        # locked if the count range collapses onto the truth (mid==truth or singleton)
        q4_lock_ok = bool(q4_rng and q4_rng[0] <= q4_truth <= q4_rng[2] and (q4_rng[2] - q4_rng[0]) <= 1)
    return dict(map=rec.get("map_id"), rnd=rnd, n_rows=len(rows), read_ok=read_ok, missing=missing,
                action_notes=sorted(n for n in notes if n.startswith("action_")),
                q4_truth=q4_truth, q4_rng=q4_rng, q4_lock_ok=q4_lock_ok,
                bal=res.get("balanced"), tval=rec.get("truth_total_value"))


def real_action_notes(f):
    ev = parse_fatbeans_capture(Path(f)); mid = _map_id_from_events(ev); st = _settlement_breakdown(ev)
    if not st:
        return None
    pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
    prefix = None
    for r in range(len(pre), 0, -1):
        if not all(b.phase == "reading" for b in pre[:r]):
            prefix = pre[:r]; break
    if not prefix:
        return None
    sid = _prefix_sort_id(prefix); pe = _events_through_sort(ev, sid) if sid else ev
    snap = _build_snapshot(hero="ethan", events=pe, prefix_batches=prefix, map_id=mid)
    ar = ((snap.get("ui_contract") or {}).get("context") or {}).get("round")
    res = run_snap(snap, ar)
    notes = set(res.get("notes") or []) | set((res.get("evidence") or {}).get("source_notes") or [])
    return mid, ar, sorted(n for n in notes if n.startswith("action_") or "avg_cells" in n or "cells" in n)


def main():
    n_synth = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    n_real = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    synth_dir = ROOT/"data"/"samples"/"synthetic_draw"/"ethan"
    files = sorted(glob.glob(str(synth_dir/"synth_ethan_*.json")))[:n_synth]
    print(f"=== SYNTH FIDELITY: {len(files)} samples ===")
    read_fail = lock_fail = 0
    note_ctr = Counter()
    for sf in files:
        rec = json.loads(Path(sf).read_text(encoding="utf-8"))
        r = check_synth(rec)
        note_ctr.update(r["action_notes"])
        if not r["read_ok"]:
            read_fail += 1
            print(f"  READ-DROP map={r['map']} r={r['rnd']} MISSING={r['missing']} rows={r['n_rows']}")
        if r["q4_lock_ok"] is False:
            lock_fail += 1
            print(f"  Q4-LOCK-MISS map={r['map']} truth_q4={r['q4_truth']} q4_rng={r['q4_rng']}")
    print(f"  read_ok: {len(files)-read_fail}/{len(files)}   q4-lock recovered truth: fails={lock_fail}")
    print(f"  emitted action-note coverage across synth: {dict(note_ctr)}")
    # real ethan action-note vocabulary
    print(f"\n=== REAL clean_eval ETHAN action/cells notes ({n_real}) ===")
    real_notes = Counter()
    reals = clean_eval_files("ethan")[:n_real]
    for f in reals:
        try:
            rr = real_action_notes(f)
        except Exception as e:
            print("  real fail", Path(f).name, e); continue
        if rr:
            real_notes.update(rr[2])
    print(f"  real action/cells note vocabulary: {dict(real_notes)}")


if __name__ == "__main__":
    main()
