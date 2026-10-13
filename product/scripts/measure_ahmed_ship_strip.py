"""Measure the Ahmed tier105-ship conservative red strip against settled truth, per ship, at the
decision round — the empirical foundation for reviewing/tuning AHMED_CONS_PURE_PRIOR_RED_KEEP.

Runs the engine on every valid ahmed activity-ship capture (data3 inbox source + fatbeans ship dir)
at the last pre-settlement round, with the strip OFF and at a sweep of keep values, and compares the
three tiers (保守/参考/激进) to the normalized settled truth (long-tail red normalized per memory).
Reports per-ship rows + bias/MAE by true-red bucket, so the strip's real impact and the right keep
are read from data, not guessed.

Read-only (no engine edits; toggles env only). Run: python scripts/measure_ahmed_ship_strip.py
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
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

# Original desktop source is typically deleted after ingest; fall back to the in-repo mirror.
DATA3 = r"C:/Users/example/Desktop/data (3)/data/logs/live/raw/archive/reset"
MIRROR = str(ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures")
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")
KEEPS = ("off", "0.35", "0.50", "0.65")
KEEPENV = "AHMED_CONS_PURE_PRIOR_RED_KEEP"
ENABLE = "AHMED_ENABLE_CONS_PURE_PRIOR_RED_STRIP"  # strip is DEFAULT OFF — enable to fire


def _m(x):
    return f"{x/1e6:.2f}" if x else "0.00"


def _rbkt(r):
    return "0-2" if r <= 2 else ("3-4" if r <= 4 else ("5-6" if r <= 6 else "7+"))


def _ship_captures():
    base = DATA3 if os.path.isdir(DATA3) else MIRROR
    out = []
    for mp in SHIP_MAPS:
        out += glob.glob(f"{base}/**/*_{mp}_*reset.json", recursive=True)
    return sorted(out)


def _run(snap, keep):
    # strip is DEFAULT OFF: "off" = no enable env; a keep value = enable + set keep
    if keep == "off":
        os.environ.pop(ENABLE, None); os.environ.pop(KEEPENV, None)
    else:
        os.environ[ENABLE] = "1"; os.environ[KEEPENV] = keep
    r = run_reference_engine(snap, max_combos=hero_max_combos_for_round("ahmed", 4)).as_dict()
    os.environ.pop(ENABLE, None); os.environ.pop(KEEPENV, None)
    return r


def main():
    caps = _ship_captures()
    print(f"ahmed ship captures: {len(caps)}\n")
    rows = []
    for f in caps:
        try:
            ev = C._events(Path(f))
            if not C.settlement_is_valid(ev):
                continue
            bd = C.settlement_breakdown(ev)
            nt = C.normalized_truth(ev)
            t_red = int((bd.get("counts") or {}).get(6, 0))
            t_val = nt.get("norm_value") or bd.get("total_value") or 0
            mp = _map_id_from_events(ev)
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            if not pre:
                continue
            # Decision round = LAST pre-settlement batch (these ships run 2..8 rounds; capping at 4
            # mislabels longer games). snapshot + round label MUST share batch_no. CRITICAL: pass
            # hero="ahmed" — _snapshot_at_batch defaults to aisha, which routes the engine down the
            # aisha branch and makes the ahmed strip unreachable (the original "0/21" harness bug).
            dr = len(pre)
            snap_raw = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=dr, map_id=mp, hero="ahmed")
            snap = prepare_reference_engine_snapshot(snap_raw)
            res = {k: _run(snap, k) for k in KEEPS}
            fired = any(str(n).startswith("ahmed_cons_pure_prior_red_strip")
                        for n in res["0.50"].get("notes", []))
            rows.append({"map": mp, "t_red": t_red, "t_val": t_val, "res": res, "fired": fired})
        except Exception as e:
            print(f"  ERR {Path(f).name[:40]}: {str(e)[:60]}")

    # per-ship table
    print(f"{'map':>5} {'tRed':>4} {'tVal':>5} {'red_est':>9} {'fire':>4} | "
          + "  ".join(f"{'保'+k:>10}" for k in KEEPS) + "   (参考, unchanged)")
    for r in sorted(rows, key=lambda x: x["t_red"]):
        cons = "  ".join(f"{_m(r['res'][k]['conservative']):>10}" for k in KEEPS)
        bal = r["res"]["0.50"]["balanced"]
        re_ = r["res"]["0.50"].get("red_count_range")
        print(f"{r['map']:>5} {r['t_red']:>4} {_m(r['t_val']):>5} {str(re_):>9} {('Y' if r['fired'] else 'n'):>4} | "
              f"{cons}   参考={_m(bal)}")

    # bias/MAE of CONSERVATIVE vs truth, by red bucket, per keep (does the strip help low-red without hurting?)
    print("\n=== 保守 tier vs truth — bias(保守-真) & MAE by true-red bucket ===")
    print(f"{'bucket':>7} {'n':>3} | " + "  ".join(f"{k:>16}" for k in KEEPS))
    buckets = defaultdict(list)
    for r in rows:
        buckets[_rbkt(r["t_red"])].append(r)
    for bk in ("0-2", "3-4", "5-6", "7+"):
        rs = buckets.get(bk, [])
        if not rs:
            continue
        cells = []
        for k in KEEPS:
            biases = [r["res"][k]["conservative"] - r["t_val"] for r in rs]
            maes = [abs(b) for b in biases]
            cells.append(f"{sum(biases)/len(biases)/1e6:+.2f}/{sum(maes)/len(maes)/1e6:.2f}")
        print(f"{bk:>7} {len(rs):>3} | " + "  ".join(f"{c:>16}" for c in cells))
    print("  (cell = mean_bias / MAE in M; bias<0 = 保守 below truth = safe floor)")

    # 参考(balanced) vs truth by bucket — is the engine over- or under-estimating ahmed ships?
    print("\n=== 参考 tier vs truth — bias(参考-真) & MAE by true-red bucket (strip-independent) ===")
    for bk in ("0-2", "3-4", "5-6", "7+"):
        rs = buckets.get(bk, [])
        if not rs:
            continue
        biases = [r["res"]["0.50"]["balanced"] - r["t_val"] for r in rs]
        maes = [abs(b) for b in biases]
        print(f"{bk:>7} {len(rs):>3} | bias {sum(biases)/len(biases)/1e6:+.2f}M  MAE {sum(maes)/len(maes)/1e6:.2f}M")

    fired_n = sum(1 for r in rows if r["fired"])
    print(f"\n*** STRIP FIRE RATE at decision round: {fired_n}/{len(rows)} ahmed ships ***")
    print("    Pure-prior red is the NORM on ahmed ships (red is off-wire to ahmed), so the strip fires"
          " ~90% here, NOT rarely. The buggy precursor reported 0/21 by omitting hero='ahmed' (ran the"
          " aisha branch). 保守-OFF is already a safe floor; strip ON over-suppresses high-red ships"
          " (7+ bucket) → DEFAULT OFF (2026-06-21 workflow). 参考/激进 unchanged (conservative-only).")


if __name__ == "__main__":
    main()
