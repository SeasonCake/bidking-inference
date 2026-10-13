"""H3 adversarial check: is the "red-heavy ahmed ships are UNDERESTIMATED" finding an artifact of
long-tail red inflating the settled truth (memory long-tail-red-contaminates-truth)?

Method (falsify-first):
  For every valid ahmed tier105 ship, at the DECISION round (last pre-settlement batch), compute the
  engine's reference (balanced) estimate vs BOTH truths:
    - raw_value  (settlement inventory at catalog price, long-tail included)
    - norm_value (long-tail red/gold replaced by same-shape non-tail mean)
  For ships judged UNDERESTIMATED against norm at red>=5, report:
    bias_vs_raw = ref - raw,  bias_vs_norm = ref - norm,  and how much the underestimate SHRINKS
    when we (incorrectly) use raw instead of norm. List ships that contain long-tail red and how
    much each tail inflates raw over norm.

If the red>=5 underestimate largely VANISHES under raw -> the underestimate is a long-tail artifact
(H3 supported). If it persists under BOTH raw and norm -> the engine genuinely underestimates
red-heavy ships and H3 is falsified.

Truth source = aisha_calib (the single calibration source of truth). Engine = ahmad_ref_engine,
run with hero='ahmed' (the prior phase proved the snapshot defaults to aisha if hero is not passed).
Read-only on engine. Run: PYTHONIOENCODING=utf-8 python scripts/verify_h3_longtail_underest.py
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
    extract_evidence,
    _is_pure_prior_red,
    _map_tier,
)
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

DATA3 = r"C:/Users/example/Desktop/data (3)/data/logs/live/raw/archive/reset"
MIRROR = str(ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures")
FATBEANS_SHIP = str(ROOT / "data/samples/fatbeans_activity_20260605_shipwreck")
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")
HERO = "ahmed"


def _m(x):
    return f"{x/1e6:+.2f}" if x else "0.00"


def _ship_captures():
    out = []
    seen = set()
    src = DATA3 if os.path.isdir(DATA3) else None
    if src:
        for mp in SHIP_MAPS:
            for f in glob.glob(f"{src}/*_{mp}_*reset.json"):
                if os.path.basename(f) not in seen:
                    seen.add(os.path.basename(f)); out.append(f)
    else:
        print(f"  [desktop source gone -> mirror {MIRROR}]")
        for mp in SHIP_MAPS:
            for f in glob.glob(f"{MIRROR}/**/*_{mp}_*reset.json", recursive=True):
                if os.path.isfile(f) and os.path.getsize(f) > 0 and os.path.basename(f) not in seen:
                    seen.add(os.path.basename(f)); out.append(f)
    # fatbeans ship dir (multi-hero; we filter to tier105 ship maps too)
    for f in glob.glob(f"{FATBEANS_SHIP}/*.json"):
        if os.path.basename(f) not in seen:
            seen.add(os.path.basename(f)); out.append(f)
    return sorted(out)


def _pre(ev):
    return [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]


def _run_ref(snap, rnd):
    # strip OFF so we read the raw engine reference (balanced is strip-independent anyway)
    os.environ["AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"] = "1"
    r = run_reference_engine(snap, max_combos=hero_max_combos_for_round(HERO, rnd)).as_dict()
    os.environ.pop("AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP", None)
    return r


def main():
    caps = _ship_captures()
    print(f"candidate ship captures: {len(caps)}\n")
    rows = []
    for f in caps:
        try:
            ev = C._events(Path(f))
            if not C.settlement_is_valid(ev):
                continue
            mp = _map_id_from_events(ev)
            if _map_tier(mp) != "105":
                continue
            bd = C.settlement_breakdown(ev)
            nt = C.normalized_truth(ev)
            t_red = int((bd.get("counts") or {}).get(6, 0))
            raw = nt.get("raw_value") or 0
            norm = nt.get("norm_value") or 0
            pre = _pre(ev)
            if not pre:
                continue
            dr = len(pre)  # decision round = last pre-settlement batch
            snap_raw = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=dr, map_id=mp, hero=HERO)
            snap = prepare_reference_engine_snapshot(snap_raw)
            evd = extract_evidence(snap)
            res = _run_ref(snap, dr)
            ref = res.get("balanced") or 0
            agg = res.get("aggressive") or 0
            cons = res.get("conservative") or 0
            rows.append({
                "f": Path(f).name, "map": mp, "n_pre": len(pre), "dr": dr,
                "t_red": t_red, "raw": raw, "norm": norm,
                "ref": ref, "agg": agg, "cons": cons,
                "has_lt": bool(nt.get("has_longtail")), "lt": nt.get("longtail") or [],
                "category": nt.get("category"),
                "pure": _is_pure_prior_red(evd),
            })
        except Exception as e:
            print(f"  ERR {Path(f).name[-40:]}: {str(e)[:70]}")

    rows.sort(key=lambda x: (x["t_red"], x["map"]))

    # ---- full per-ship table: ref vs raw vs norm ----
    print(f"{'map':>5} {'nPre':>4} {'tRed':>4} | {'raw':>6} {'norm':>6} {'LT?':>3} | "
          f"{'ref(参)':>7} | {'ref-raw':>8} {'ref-norm':>9} | shrink(norm->raw)")
    print("-" * 100)
    for r in rows:
        b_raw = r["ref"] - r["raw"]
        b_norm = r["ref"] - r["norm"]
        # how much the (negative) underestimate magnitude shrinks if we used raw instead of norm
        shrink = abs(b_norm) - abs(b_raw)
        print(f"{r['map']:>5} {r['n_pre']:>4} {r['t_red']:>4} | "
              f"{r['raw']/1e6:>6.2f} {r['norm']/1e6:>6.2f} {('Y' if r['has_lt'] else '-'):>3} | "
              f"{r['ref']/1e6:>7.2f} | {_m(b_raw):>8} {_m(b_norm):>9} | "
              f"{shrink/1e6:+.2f}M {'<-- LT' if r['has_lt'] else ''}")

    # ---- focus: red>=5 ships, are they underestimated, and does it survive raw? ----
    hi = [r for r in rows if r["t_red"] >= 5]
    print(f"\n{'='*100}")
    print(f"=== H3 FOCUS: red>=5 ships (n={len(hi)}) — underestimate under norm vs raw ===")
    print(f"{'map':>5} {'tRed':>4} | {'raw':>6} {'norm':>6} | {'ref':>6} | "
          f"{'b_raw':>7} {'b_norm':>8} | under(norm)? under(raw)? | LT")
    under_norm = under_raw = 0
    sum_bnorm = sum_braw = 0.0
    for r in hi:
        b_raw = r["ref"] - r["raw"]
        b_norm = r["ref"] - r["norm"]
        sum_bnorm += b_norm; sum_braw += b_raw
        un = b_norm < -0.05e6
        ur = b_raw < -0.05e6
        under_norm += int(un); under_raw += int(ur)
        lt_desc = ",".join(f"{t['name'][:10]}({t['raw']//1000}->{t['replacement']//1000}k)" for t in r["lt"]) or "-"
        print(f"{r['map']:>5} {r['t_red']:>4} | {r['raw']/1e6:>6.2f} {r['norm']/1e6:>6.2f} | "
              f"{r['ref']/1e6:>6.2f} | {_m(b_raw):>7} {_m(b_norm):>8} | "
              f"{('UNDER' if un else 'ok  '):>9}   {('UNDER' if ur else 'ok  '):>9}  | {lt_desc}")
    n = len(hi) or 1
    print(f"\n  red>=5 mean bias vs NORM: {sum_bnorm/n/1e6:+.3f}M   (underestimated ships: {under_norm}/{len(hi)})")
    print(f"  red>=5 mean bias vs RAW : {sum_braw/n/1e6:+.3f}M   (underestimated ships: {under_raw}/{len(hi)})")
    print(f"  => if underestimate (count & magnitude) largely vanishes under RAW, H3 is supported.")
    print(f"     if it persists under BOTH, the engine genuinely underestimates red-heavy ships (H3 falsified).")

    # ---- long-tail inventory among red>=5 ----
    lt_ships = [r for r in hi if r["has_lt"]]
    print(f"\n=== red>=5 ships CONTAINING long-tail (n={len(lt_ships)} of {len(hi)}) ===")
    if not lt_ships:
        print("  NONE. No red>=5 ahmed ship in this library carries a long-tail item ->")
        print("  the red>=5 underestimate CANNOT be a long-tail-truth artifact (raw==norm for these ships).")
    for r in lt_ships:
        infl = r["raw"] - r["norm"]
        print(f"  {r['map']} red={r['t_red']}: raw-norm inflation +{infl/1e6:.2f}M; tails: "
              + "; ".join(f"{t['name']} q{t['quality']} {t['raw']//1000}k->{t['replacement']//1000}k" for t in r["lt"]))


if __name__ == "__main__":
    main()
