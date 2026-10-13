"""INDEPENDENT adversarial check of the claim:
  'strip fires 0/21 at decision-round R4 in real settled ahmed ships;
   its scope is early/no-reveal pure-prior red, rare in the settled library.'

For every valid tier105 ahmed ship (mirror inbox + fatbeans dir), at the decision
round, we report under BOTH hero settings:
  (a) original buggy path: snapshot built WITHOUT hero (defaults aisha)  -> fire?
  (b) corrected path: snapshot built WITH hero='ahmed'                   -> fire?
plus the engine's own _is_pure_prior_red(result.evidence) gate value.

We also vary the 'decision round' definition: R4=min(len(pre),4) vs R_last=last
pre-settlement batch, since sessions have 2-8 pre rounds and R4 may be mid-game.

Read-only. Run: PYTHONIOENCODING=utf-8 python scripts/verify_strip_r4_firerate.py
"""
from __future__ import annotations

import glob
import os
import sys
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

MIRROR = ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures"
FATBEANS = ROOT / "data/samples/fatbeans_activity_20260605_shipwreck"
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")


def _mirror_real_file(entry: Path) -> Path:
    """Mirror layout: each '*_reset.json' is a DIR holding a same-named real file."""
    if entry.is_dir():
        inner = entry / entry.name
        if inner.is_file():
            return inner
    return entry


def _ship_captures():
    out = []
    # mirror inbox (dir-wrapped) -- ahmed ships are activity ships 25xx/45xx
    if MIRROR.is_dir():
        for entry in sorted(MIRROR.iterdir()):
            nm = entry.name
            if any(f"_{mp}_" in nm for mp in SHIP_MAPS) and nm.endswith("reset.json"):
                out.append(("mirror", _mirror_real_file(entry)))
    # fatbeans ship dir (flat json) -- multi-hero ships
    for f in sorted(glob.glob(str(FATBEANS / "*.json"))):
        out.append(("fatbeans", Path(f)))
    return out


def _run(snap):
    # return the RefResult OBJECT (not as_dict) so .evidence is a RefEvidence and
    # _is_pure_prior_red can be called directly; .notes carries the fire signal.
    return run_reference_engine(snap, max_combos=hero_max_combos_for_round("ahmed", 4))


def _fired(res) -> bool:
    return any(str(n).startswith("ahmed_cons_pure_prior_red_strip")
               for n in res.notes)


def main():
    os.environ.pop("AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP", None)
    os.environ.pop("AHMED_CONS_PURE_PRIOR_RED_KEEP", None)  # default 0.50

    caps = _ship_captures()
    print(f"candidate captures (mirror ships + fatbeans): {len(caps)}\n")

    rows = []
    seen = set()
    for src, f in caps:
        try:
            ev = C._events(f)
            if not C.settlement_is_valid(ev):
                continue
            mp = _map_id_from_events(ev)
            if _map_tier(mp) != "105":
                continue
            bd = C.settlement_breakdown(ev)
            t_red = int((bd.get("counts") or {}).get(6, 0))
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            if not pre:
                continue
            n_pre = len(pre)
            dr_r4 = min(n_pre, 4)      # the original script's "R4"
            dr_last = n_pre            # the genuine decision round (last pre-settlement)

            key = (mp, f.stem)
            if key in seen:
                continue
            seen.add(key)

            out = {"src": src, "name": f.stem[:46], "map": mp, "t_red": t_red, "n_pre": n_pre}

            for label, dr in (("R4", dr_r4), ("Rlast", dr_last)):
                # (a) ORIGINAL BUGGY path: no hero -> defaults aisha
                snap_a = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=dr, map_id=mp)
                res_a = _run(prepare_reference_engine_snapshot(snap_a))
                # (b) CORRECTED path: hero='ahmed'
                snap_b = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=dr, map_id=mp, hero="ahmed")
                snap_b_prepped = prepare_reference_engine_snapshot(snap_b)
                res_b = _run(snap_b_prepped)
                pp = _is_pure_prior_red(res_b.evidence)
                out[f"{label}_hero_a"] = res_a.evidence.hero
                out[f"{label}_fire_aisha_path"] = _fired(res_a)
                out[f"{label}_hero_b"] = res_b.evidence.hero
                out[f"{label}_fire_ahmed_path"] = _fired(res_b)
                out[f"{label}_pure_prior"] = pp
            rows.append(out)
        except Exception as e:
            print(f"  ERR {f.name[:42]}: {type(e).__name__}: {str(e)[:70]}")

    rows.sort(key=lambda r: (r["map"], r["name"]))
    print(f"\nvalid tier105 ships: {len(rows)}\n")
    print(f"{'map':>5} {'tRed':>4} {'nPre':>4} | "
          f"{'R4_heroA':>9} {'R4fireA':>8} {'R4fireAHM':>10} {'R4pp':>5} | "
          f"{'Rl_fireAHM':>11} {'Rl_pp':>6}")
    for r in rows:
        print(f"{r['map']:>5} {r['t_red']:>4} {r['n_pre']:>4} | "
              f"{str(r['R4_hero_a']):>9} {str(r['R4_fire_aisha_path']):>8} "
              f"{str(r['R4_fire_ahmed_path']):>10} {str(r['R4_pure_prior']):>5} | "
              f"{str(r['Rlast_fire_ahmed_path']):>11} {str(r['Rlast_pure_prior']):>6}")

    n = len(rows)
    fa_r4 = sum(1 for r in rows if r["R4_fire_aisha_path"])
    fb_r4 = sum(1 for r in rows if r["R4_fire_ahmed_path"])
    fb_rl = sum(1 for r in rows if r["Rlast_fire_ahmed_path"])
    pp_r4 = sum(1 for r in rows if r["R4_pure_prior"])
    pp_rl = sum(1 for r in rows if r["Rlast_pure_prior"])
    print(f"\n=== TOTALS over {n} valid tier105 ships ===")
    print(f"  R4 fire, AISHA path (original buggy script): {fa_r4}/{n}")
    print(f"  R4 fire, AHMED path (corrected):             {fb_r4}/{n}")
    print(f"  R4 pure_prior gate True (ahmed evidence):    {pp_r4}/{n}")
    print(f"  Rlast fire, AHMED path (genuine decision rd):{fb_rl}/{n}")
    print(f"  Rlast pure_prior gate True:                  {pp_rl}/{n}")


if __name__ == "__main__":
    main()
