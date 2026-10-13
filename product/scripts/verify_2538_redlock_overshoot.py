"""ADVERSARIAL CHECK of the claim:
  "2538 (true red=2) ~2x overshoot PERSISTS when red is correctly estimated as 2
   => main cause is NOT phantom red, it's base/grid/non-red value."

Method: run the engine on every 2538 capture ROUND BY ROUND with hero='ahmed' (the CORRECT
hero — the decompose script defaulted to aisha which inflates red to ~6 and is the bug noted
in earlier phases). At each round print balanced/conservative/aggressive, red_count_range,
red_value_range(rv50), total_grid_range, non-red base = balanced - rv50, plus truth.

The crux: find the round(s) where engine red_mid == 2 (red correctly estimated) and check
whether the overshoot multiple is still ~2x there, or collapses to ~1x.

Read-only. PYTHONIOENCODING=utf-8 python scripts/verify_2538_redlock_overshoot.py
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
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
)
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

MIRROR = ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures"
DIS = "AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"


def _mid(rng):
    return (rng or [None, None, None])[1] or 0


def _captures_for(mapid: str):
    out = []
    # mirror layout: each *_reset.json is a DIR containing the real same-named file
    for d in sorted(MIRROR.glob(f"*_{mapid}_*reset.json")):
        if d.is_dir():
            inner = d / d.name
            if inner.exists():
                out.append(inner)
        elif d.is_file():
            out.append(d)
    # also try desktop source if it still exists
    src = r"C:/Users/example/Desktop/data (3)/data/logs/live/raw/archive/reset"
    if os.path.isdir(src):
        out += [Path(p) for p in glob.glob(f"{src}/*_{mapid}_*reset.json")]
    return out


def trace(path: Path, strip_off=True):
    if strip_off:
        os.environ[DIS] = "1"
    else:
        os.environ.pop(DIS, None)
    ev = C._events(path)
    valid = C.settlement_is_valid(ev)
    bd = C.settlement_breakdown(ev)
    counts = bd.get("counts") or {}
    values = bd.get("values") or {}
    cells = bd.get("cells") or {}
    t_red = int(counts.get(6, 0))
    t_red_val = int(values.get(6, 0))
    t_red_cells = int(cells.get(6, 0))
    t_total_cells = int(bd.get("total_cells") or 0)
    t_val_raw = bd.get("total_value") or 0
    nt = C.normalized_truth(ev)
    t_val_norm = nt.get("norm_value") or 0
    t_nonred_raw = t_val_raw - t_red_val
    per_q_norm = nt.get("per_quality_norm") or {}
    t_red_val_norm = per_q_norm.get(6, t_red_val)
    t_nonred_norm = t_val_norm - t_red_val_norm

    mp = _map_id_from_events(ev)
    pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
    n_pre = len(pre)

    print(f"\n{'='*100}")
    print(f"FILE {path.name}")
    print(f"  valid_settlement={valid}  map={mp}  n_pre_rounds={n_pre}")
    print(f"  TRUTH: total raw={t_val_raw/1e6:.3f}M norm={t_val_norm/1e6:.3f}M | red count={t_red} "
          f"cells={t_red_cells} value={t_red_val/1e6:.3f}M (norm {t_red_val_norm/1e6:.3f}M) "
          f"$/red={t_red_val/t_red/1e6:.3f}M" if t_red else
          f"  TRUTH: total raw={t_val_raw/1e6:.3f}M norm={t_val_norm/1e6:.3f}M | red count=0")
    print(f"  TRUTH non-red base: raw={t_nonred_raw/1e6:.3f}M norm={t_nonred_norm/1e6:.3f}M | total_cells={t_total_cells}")
    print(f"  {'-'*96}")
    hdr = (f"  {'R':>2} {'redMid':>6} {'redRng':>10} | {'bal':>6} {'cons':>6} {'aggr':>6} | "
           f"{'rv50':>6} {'base':>6} | {'grid':>10} | {'over/norm':>9} {'xNorm':>5} | {'over/raw':>8} {'xRaw':>5} {'fired':>5}")
    print(hdr)
    results = []
    for b in range(1, n_pre + 1):
        snap_raw = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=b, map_id=mp, hero="ahmed")
        snap = prepare_reference_engine_snapshot(snap_raw)
        ctx_hero = getattr(getattr(snap, "evidence", None), "hero", None)
        r = run_reference_engine(snap, max_combos=hero_max_combos_for_round("ahmed", min(b, 4))).as_dict()
        bal = r.get("balanced") or 0
        cons = r.get("conservative") or 0
        aggr = r.get("aggressive") or 0
        rv50 = _mid(r.get("red_value_range"))
        red_mid = _mid(r.get("red_count_range"))
        grid = r.get("total_grid_range")
        base = bal - rv50
        over_n = bal - t_val_norm
        over_r = bal - t_val_raw
        xN = bal / t_val_norm if t_val_norm else 0
        xR = bal / t_val_raw if t_val_raw else 0
        notes = r.get("notes") or []
        fired = any("ahmed_cons_pure_prior_red_strip" in str(n) for n in notes)
        results.append(dict(R=b, red_mid=red_mid, bal=bal, cons=cons, aggr=aggr, rv50=rv50,
                            base=base, grid=grid, over_n=over_n, xN=xN, hero=ctx_hero,
                            red_rng=r.get("red_count_range"), fired=fired,
                            red_cells=_mid(r.get("red_cells_range")), notes=notes))
        gridtxt = f"[{grid[0]},{grid[1]},{grid[2]}]" if grid and grid[1] is not None else str(grid)
        print(f"  {b:>2} {red_mid:>6} {str(r.get('red_count_range')):>10} | "
              f"{bal/1e6:>6.3f} {cons/1e6:>6.3f} {aggr/1e6:>6.3f} | "
              f"{rv50/1e6:>6.3f} {base/1e6:>6.3f} | {gridtxt:>10} | "
              f"{over_n/1e6:>+9.3f} {xN:>5.2f} | {over_r/1e6:>+8.3f} {xR:>5.2f} {str(fired):>5}")
    os.environ.pop(DIS, None)
    # crux summary
    print(f"  {'-'*96}")
    print(f"  ctx_hero seen = {set(x['hero'] for x in results)}  (must be 'ahmed' for valid test)")
    lock_rounds = [x for x in results if x["red_mid"] == t_red]
    print(f"  ROUNDS where engine red_mid == truth red({t_red}): {[x['R'] for x in lock_rounds]}")
    for x in lock_rounds:
        print(f"    R{x['R']}: bal={x['bal']/1e6:.3f}M  xNorm={x['xN']:.2f}x  over={x['over_n']/1e6:+.3f}M  "
              f"rv50={x['rv50']/1e6:.3f}M base={x['base']/1e6:.3f}M  -> "
              f"red-value-residual(rv50-trueRedNorm)={(x['rv50']-t_red_val_norm)/1e6:+.3f}M  "
              f"base-residual(base-trueNonredNorm)={(x['base']-t_nonred_norm)/1e6:+.3f}M")
    peak = max(results, key=lambda x: x["xN"])
    print(f"  PEAK overshoot round: R{peak['R']} redMid={peak['red_mid']} xNorm={peak['xN']:.2f}x over={peak['over_n']/1e6:+.3f}M")
    return results, dict(t_red=t_red, t_red_val=t_red_val, t_red_val_norm=t_red_val_norm,
                         t_nonred_norm=t_nonred_norm, t_val_norm=t_val_norm, t_total_cells=t_total_cells,
                         t_red_cells=t_red_cells)


def main():
    caps = _captures_for("2538")
    print(f"2538 captures found: {len(caps)}")
    for c in caps:
        print(f"  - {c}")
    for c in caps:
        try:
            trace(c, strip_off=True)
        except Exception as e:
            import traceback
            print(f"ERR {c.name}: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
