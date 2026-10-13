"""Adversarial per-ship check of the claim:
  "Most red>=3 ahmed ships are UNDER-valued (参考 < truth) at R4, not over."

For every valid ahmed tier105 ship, run the engine at BOTH:
  - decision = last pre-settlement batch
  - r4       = fixed round 4 (min(n_pre,4))
and print, per ship, 参考(balanced) vs truth, the signed gap, and over/under flag.
Then tally over/under counts for red>=3, red>=4, red>=5, and the full set.

Truth = normalized_truth.norm_value (long-tail red normalized). Also report raw for sensitivity.
Engine run with strip OFF (balanced is strip-independent anyway). Read-only.
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
    _map_tier,
)
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

DATA3 = r"C:/Users/example/Desktop/data (3)/data/logs/live/raw/archive/reset"
MIRROR = str(ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures")
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")
HERO = "ahmed"


def _pre(ev):
    return [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]


def _ships():
    out, seen = [], set()
    src = DATA3 if os.path.isdir(DATA3) else None
    for mp in SHIP_MAPS:
        pat = f"{src}/*_{mp}_*reset.json" if src else f"{MIRROR}/**/*_{mp}_*reset.json"
        for f in glob.glob(pat, recursive=not src):
            if os.path.isfile(f) and os.path.getsize(f) > 0 and os.path.basename(f) not in seen:
                seen.add(os.path.basename(f))
                out.append(f)
    return sorted(out)


def _bal(ev, mp, batch_no):
    snap_raw = _snapshot_at_batch(events=ev, pre_batches=_pre(ev), batch_no=batch_no, map_id=mp, hero=HERO)
    snap = prepare_reference_engine_snapshot(snap_raw)
    os.environ["AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"] = "1"
    r = run_reference_engine(snap, max_combos=hero_max_combos_for_round(HERO, batch_no)).as_dict()
    os.environ.pop("AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP", None)
    return r


def main():
    ships = _ships()
    print(f"ahmed tier105 ships: {len(ships)}  (source={'DATA3' if os.path.isdir(DATA3) else 'MIRROR'})")
    rows = []
    for f in ships:
        try:
            ev = C._events(Path(f))
            if not C.settlement_is_valid(ev):
                continue
            bd = C.settlement_breakdown(ev)
            nt = C.normalized_truth(ev)
            t_red = int((bd.get("counts") or {}).get(6, 0))
            t_norm = nt.get("norm_value") or bd.get("total_value") or 0
            t_raw = bd.get("total_value") or 0
            mp = _map_id_from_events(ev)
            if _map_tier(mp) != "105":
                continue
            pre = _pre(ev)
            if not pre:
                continue
            n_pre = len(pre)
            r_dec = _bal(ev, mp, n_pre)
            r_r4 = _bal(ev, mp, min(n_pre, 4))
            rows.append({
                "f": Path(f).name[-30:], "map": mp, "n_pre": n_pre, "t_red": t_red,
                "t_norm": t_norm, "t_raw": t_raw,
                "bal_dec": r_dec["balanced"], "bal_r4": r_r4["balanced"],
                "red_dec": r_dec.get("red_count_range"), "red_r4": r_r4.get("red_count_range"),
            })
        except Exception as e:
            print(f"  ERR {Path(f).name[-30:]}: {str(e)[:60]}")

    def m(x):
        return f"{x/1e6:6.2f}"

    print(f"\n{'map':>5} {'nPre':>4} {'tRed':>4} {'truthN':>7} {'truthR':>7} | "
          f"{'参R4':>7} {'gapR4':>7} {'o/u':>3} | {'参Dec':>7} {'gapDec':>7} {'o/u':>3} | {'redR4':>9}")
    for r in sorted(rows, key=lambda x: (x["t_red"], x["map"])):
        gap_r4 = r["bal_r4"] - r["t_norm"]
        gap_dec = r["bal_dec"] - r["t_norm"]
        ou_r4 = "OVR" if gap_r4 > 0 else "und"
        ou_dec = "OVR" if gap_dec > 0 else "und"
        print(f"{r['map']:>5} {r['n_pre']:>4} {r['t_red']:>4} {m(r['t_norm'])} {m(r['t_raw'])} | "
              f"{m(r['bal_r4'])} {gap_r4/1e6:+7.2f} {ou_r4:>3} | "
              f"{m(r['bal_dec'])} {gap_dec/1e6:+7.2f} {ou_dec:>3} | {str(r['red_r4']):>9}")

    print("\n--- over/under tallies (参考 balanced vs norm truth) ---")
    for label, thr in (("ALL", 0), ("red>=3", 3), ("red>=4", 4), ("red>=5", 5)):
        sub = [r for r in rows if r["t_red"] >= thr]
        n = len(sub)
        for rnd, key in (("R4", "bal_r4"), ("Decision", "bal_dec")):
            ov = sum(1 for r in sub if r[key] - r["t_norm"] > 0)
            un = n - ov
            mb = sum(r[key] - r["t_norm"] for r in sub) / n / 1e6 if n else 0
            print(f"  {label:>8} @ {rnd:>8}: n={n:2d}  OVER={ov:2d}  UNDER={un:2d}  mean_gap={mb:+.3f}M")

    # raw-truth sensitivity for red>=3 at decision round
    print("\n--- raw-truth sensitivity (red>=3) ---")
    for rnd, key in (("R4", "bal_r4"), ("Decision", "bal_dec")):
        sub = [r for r in rows if r["t_red"] >= 3]
        ov = sum(1 for r in sub if r[key] - r["t_raw"] > 0)
        un = len(sub) - ov
        mb = sum(r[key] - r["t_raw"] for r in sub) / len(sub) / 1e6
        print(f"  red>=3 @ {rnd:>8} vs RAW: OVER={ov} UNDER={un} mean_gap={mb:+.3f}M")


if __name__ == "__main__":
    main()
