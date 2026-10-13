"""Narrow to the claim's exact '21 ship' frame = mirror SHIP_MAPS only, R4 fire rate
under corrected hero='ahmed', and prove the non-firing ships are genuinely NOT
pure-prior (have q6 evidence) so the strip correctly skips them (not a 0/21 artifact)."""
from __future__ import annotations
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("scripts", "scripts/calibration", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, str(ROOT / p))
import aisha_calib as C
from audit_aisha_gap import _map_id_from_events
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events
from ahmad_ref_engine import (prepare_reference_engine_snapshot, run_reference_engine,
                              _is_pure_prior_red, _map_tier)
from hero_ref_live_schedule import hero_max_combos_for_round
from regenerate_checkpoint_matrices import _snapshot_at_batch

MIRROR = ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures"
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")

os.environ.pop("AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP", None)
os.environ.pop("AHMED_CONS_PURE_PRIOR_RED_KEEP", None)

def real(entry):
    if entry.is_dir():
        inner = entry / entry.name
        if inner.is_file(): return inner
    return entry

rows = []
for entry in sorted(MIRROR.iterdir()):
    nm = entry.name
    if not (nm.endswith("reset.json") and any(f"_{mp}_" in nm for mp in SHIP_MAPS)):
        continue
    f = real(entry)
    try:
        ev = C._events(f)
        if not C.settlement_is_valid(ev): continue
        mp = _map_id_from_events(ev)
        if _map_tier(mp) != "105": continue
        bd = C.settlement_breakdown(ev)
        t_red = int((bd.get("counts") or {}).get(6, 0))
        pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
        if not pre: continue
        dr = min(len(pre), 4)
        snap = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=dr, map_id=mp, hero="ahmed")
        res = run_reference_engine(prepare_reference_engine_snapshot(snap),
                                   max_combos=hero_max_combos_for_round("ahmed", 4))
        fired = any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in res.notes)
        pp = _is_pure_prior_red(res.evidence)
        evd = res.evidence
        # why-not-pure-prior diagnostics for the non-firing ships
        q6_fix = int(evd.fixed_counts.get("q6", 0) or 0)
        q6_min = int(evd.min_counts.get("q6", 0) or 0)
        q6_qcf = evd.quality_cell_floors.get("q6")
        q6_qc = evd.quality_cells.get("q6")
        rows.append((mp, t_red, len(pre), dr, fired, pp, q6_fix, q6_min, q6_qcf, q6_qc, res.red_count_range))
    except Exception as e:
        print(f"ERR {f.name[:40]}: {type(e).__name__} {str(e)[:50]}")

rows.sort(key=lambda r: (r[0], r[1]))
print(f"{'map':>5} {'tRed':>4} {'nPre':>4} {'dr':>3} {'fire':>5} {'pp':>5} | "
      f"{'q6fix':>5} {'q6min':>5} {'q6cf':>5} {'q6c':>4} {'redRng':>10}")
for r in rows:
    print(f"{r[0]:>5} {r[1]:>4} {r[2]:>4} {r[3]:>3} {str(r[4]):>5} {str(r[5]):>5} | "
          f"{r[6]:>5} {r[7]:>5} {str(r[8]):>5} {str(r[9]):>4} {str(r[10]):>10}")

n = len(rows)
fire = sum(1 for r in rows if r[4])
pp = sum(1 for r in rows if r[5])
print(f"\n=== mirror SHIP_MAPS subset (claim's '21' frame) ===")
print(f"  valid tier105 ships: {n}")
print(f"  R4 fire (hero=ahmed):       {fire}/{n}")
print(f"  R4 pure_prior gate True:    {pp}/{n}")
print(f"  non-firing ships (should have q6 evidence => correctly skipped):")
for r in rows:
    if not r[4]:
        print(f"    map {r[0]} tRed{r[1]}: q6fix={r[6]} q6min={r[7]} q6cellfloor={r[8]} q6cells={r[9]} -> pure_prior={r[5]}")
