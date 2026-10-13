"""Decompose WHERE the 参考 (balanced) overestimation comes from on LOW-RED ahmed ships
(true q6 <= LOW_RED_MAX), at the decision round R4 — the lever-choosing diagnostic for the
tier105 conservative red strip.

For every valid ahmed activity-ship capture (data3 inbox + fatbeans ship dir) whose SETTLED
truth has q6 <= 3, run the engine at R4 (strip OFF so we see the raw over-estimate) and split:
  参考(balanced) = red_value_contribution(rv50) + non_red_base
and attribute the over/under vs normalized truth to four channels:
  (a) PHANTOM RED COUNT : engine red_count_mid vs truth q6 count  (extra red items invented)
  (b) RED UNIT PRICE    : engine per-red (rv50/red_mid) vs truth per-red (q6_value/q6_count)
  (c) NON-RED BASE      : (balanced - rv50) vs truth non-red value (total_value - q6_value)
  (d) GRID OVERSHOOT    : engine total_grid_mid vs truth total_cells (+ per-quality cells)

Truth uses normalized_truth.norm_value for TOTAL (long-tail red/gold normalized per memory),
but per-quality value/cells come from the raw settlement breakdown (counts/cells/values) since
that is what's physically settled; the non-red base channel is reported both raw and normalized.

Read-only. Run: PYTHONIOENCODING=utf-8 python scripts/decompose_low_red_ahmed.py
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
    hero_mode_from_state,
    live_batches_from_fatbeans_events,
)
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

DATA3 = r"C:/Users/example/Desktop/data (3)/data/logs/live/raw/archive/reset"
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")
LOW_RED_MAX = 3
DIS = "AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"


def _m(x):
    return f"{(x or 0)/1e6:+.2f}" if x else " 0.00"


def _mu(x):  # unsigned
    return f"{(x or 0)/1e6:.2f}" if x else "0.00"


def _mid(rng):
    return (rng or [None, None, None])[1] or 0


def _ship_captures():
    out = []
    for mp in SHIP_MAPS:
        out += glob.glob(f"{DATA3}/*_{mp}_*reset.json")
    # fatbeans ship dir (multi-hero) — filter to ahmed at parse time below
    fb = ROOT / "data/samples/fatbeans_activity_20260605_shipwreck"
    if fb.exists():
        out += [str(p) for p in fb.glob("*.json")]
    return sorted(set(out))


def _hero_of(ev):
    for s in ev.states:
        h = hero_mode_from_state(s)
        if h:
            return str(h).strip().lower()
    return "?"


def main():
    os.environ[DIS] = "1"  # strip OFF for the whole run — measure the raw over-estimate
    caps = _ship_captures()
    rows = []
    skipped = {"not_ahmed": 0, "invalid": 0, "no_red_le3": 0, "no_pre": 0, "err": 0}
    for f in caps:
        try:
            ev = C._events(Path(f))
            hero = _hero_of(ev)
            if hero != "ahmed":
                skipped["not_ahmed"] += 1
                continue
            if not C.settlement_is_valid(ev):
                skipped["invalid"] += 1
                continue
            bd = C.settlement_breakdown(ev)
            counts = bd.get("counts") or {}
            cells = bd.get("cells") or {}
            values = bd.get("values") or {}
            t_red = int(counts.get(6, 0))
            if t_red > LOW_RED_MAX:
                skipped["no_red_le3"] += 1
                continue
            nt = C.normalized_truth(ev)
            t_val_norm = nt.get("norm_value") or 0
            t_val_raw = bd.get("total_value") or 0
            t_red_val = int(values.get(6, 0))       # raw red value (settled)
            t_red_cells = int(cells.get(6, 0))
            t_total_cells = int(bd.get("total_cells") or 0)
            t_nonred_raw = t_val_raw - t_red_val
            # normalized non-red = norm_value - normalized red value (per-quality norm has q6)
            t_red_val_norm = (nt.get("per_quality_norm") or {}).get(6, t_red_val)
            t_nonred_norm = t_val_norm - t_red_val_norm

            mp = _map_id_from_events(ev)
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            if not pre:
                skipped["no_pre"] += 1
                continue
            dr = min(len(pre), 4)
            snap_raw = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=dr, map_id=mp)
            snap = prepare_reference_engine_snapshot(snap_raw)
            r = run_reference_engine(snap, max_combos=hero_max_combos_for_round("ahmed", 4)).as_dict()

            bal = r.get("balanced") or 0
            rv50 = _mid(r.get("red_value_range"))
            red_mid = _mid(r.get("red_count_range"))
            red_cells_mid = _mid(r.get("red_cells_range"))
            grid_mid = _mid(r.get("total_grid_range"))
            qcells = r.get("quality_cells_ranges") or {}
            q6_cells_eng = _mid(qcells.get("q6"))
            base_eng = bal - rv50                       # engine non-red base

            per_red_eng = rv50 / red_mid if red_mid else 0
            per_red_truth = t_red_val / t_red if t_red else 0

            rows.append({
                "map": mp, "file": Path(f).name, "t_red": t_red,
                "t_val_norm": t_val_norm, "t_val_raw": t_val_raw,
                "t_red_val": t_red_val, "t_red_cells": t_red_cells, "t_total_cells": t_total_cells,
                "t_nonred_raw": t_nonred_raw, "t_nonred_norm": t_nonred_norm,
                "bal": bal, "rv50": rv50, "red_mid": red_mid, "red_rng": r.get("red_count_range"),
                "red_cells_mid": red_cells_mid, "q6_cells_eng": q6_cells_eng,
                "grid_mid": grid_mid, "grid_rng": r.get("total_grid_range"),
                "base_eng": base_eng, "per_red_eng": per_red_eng, "per_red_truth": per_red_truth,
                "all_truth_cells": dict(cells), "all_eng_cells": {k: _mid(v) for k, v in qcells.items()},
                "all_truth_counts": dict(counts), "notes": r.get("notes"),
            })
        except Exception as e:
            skipped["err"] += 1
            print(f"  ERR {Path(f).name[:46]}: {str(e)[:70]}")
    os.environ.pop(DIS, None)

    print(f"\nLOW-RED ahmed ships (true q6 <= {LOW_RED_MAX}): {len(rows)}   skipped={skipped}\n")
    print("=" * 110)
    for r in sorted(rows, key=lambda x: (x["t_red"], x["map"])):
        over_norm = r["bal"] - r["t_val_norm"]
        over_raw = r["bal"] - r["t_val_raw"]
        print(f"\n--- map {r['map']}  trueRed={r['t_red']}  {r['file'][:50]}")
        print(f"  PARAM 参考(bal)={_mu(r['bal'])}M  truth norm={_mu(r['t_val_norm'])}M raw={_mu(r['t_val_raw'])}M"
              f"  -> OVER(vs norm)={_m(over_norm)}M  OVER(vs raw)={_m(over_raw)}M")
        # (a) phantom red
        phantom = max(0, r["red_mid"] - r["t_red"])
        print(f"  (a) RED COUNT : engine mid={r['red_mid']} (rng={r['red_rng']})  truth={r['t_red']}"
              f"   -> phantom red = +{phantom} items")
        # (b) red unit price
        print(f"  (b) RED $/item: engine={_mu(r['per_red_eng'])}M  truth={_mu(r['per_red_truth'])}M"
              f"   (engine red value rv50={_mu(r['rv50'])}M vs truth red value={_mu(r['t_red_val'])}M)")
        # red value over-contribution
        red_over = r["rv50"] - r["t_red_val"]
        print(f"      -> RED VALUE over-contribution = {_m(red_over)}M"
              f"   [={_m(phantom * r['per_red_truth'])}M phantom-count  +{_m(r['t_red'] * (r['per_red_eng']-r['per_red_truth']))}M unit-price, approx]")
        # (c) non-red base
        base_over_raw = r["base_eng"] - r["t_nonred_raw"]
        base_over_norm = r["base_eng"] - r["t_nonred_norm"]
        print(f"  (c) NON-RED BASE: engine={_mu(r['base_eng'])}M  truth raw={_mu(r['t_nonred_raw'])}M"
              f" norm={_mu(r['t_nonred_norm'])}M  -> base over(raw)={_m(base_over_raw)}M over(norm)={_m(base_over_norm)}M")
        # (d) grid
        grid_over = int(r["grid_mid"]) - int(r["t_total_cells"])
        grid_pct = 100 * grid_over / (r["t_total_cells"] or 1)
        print(f"  (d) GRID total: engine mid={r['grid_mid']} (rng={r['grid_rng']})  truth={r['t_total_cells']}"
              f"   -> grid over = {grid_over:+d} cells ({grid_pct:+.0f}%)")
        print(f"      red cells: engine mid={r['q6_cells_eng']}  truth={r['t_red_cells']}"
              f"   ({r['red_cells_mid']} from red_cells_range)")
        # per-quality cells side by side
        qmap = {"q1": 1, "q2": 2, "q3": 3, "q4": 4, "q5": 5, "q6": 6}
        line = "      cells/q: "
        for qk, qn in qmap.items():
            te = r["all_eng_cells"].get(qk, 0)
            tt = r["all_truth_cells"].get(qn, 0)
            if te or tt:
                line += f"{qk}={te}/{tt}  "
        print(line + "  (engine/truth)")
        vnotes = [n for n in (r["notes"] or ()) if any(t in str(n).lower() for t in
                  ("red", "floor", "anchor", "cap", "grid", "tier105", "ship", "strip", "phantom", "residual"))]
        if vnotes:
            print(f"      notes: {vnotes[:6]}")

    # ---- aggregate attribution across the low-red ships ----
    print("\n" + "=" * 110)
    print("AGGREGATE attribution on low-red ships (engine - truth, mean across n ships):")
    n = len(rows)
    if n:
        def avg(g):
            return sum(g(r) for r in rows) / n
        print(f"  n={n}")
        print(f"  参考 over vs norm truth : {_m(avg(lambda r: r['bal']-r['t_val_norm']))}M   "
              f"(vs raw: {_m(avg(lambda r: r['bal']-r['t_val_raw']))}M)")
        print(f"  (a)+(b) RED VALUE over  : {_m(avg(lambda r: r['rv50']-r['t_red_val']))}M   "
              f"[phantom red items mean = +{avg(lambda r: max(0,r['red_mid']-r['t_red'])):.1f}]")
        print(f"  (c) NON-RED BASE over   : {_m(avg(lambda r: r['base_eng']-r['t_nonred_raw']))}M raw  "
              f"/ {_m(avg(lambda r: r['base_eng']-r['t_nonred_norm']))}M norm")
        print(f"  (d) GRID total over     : {avg(lambda r: r['grid_mid']-r['t_total_cells']):+.0f} cells mean "
              f"({avg(lambda r: 100*(r['grid_mid']-r['t_total_cells'])/(r['t_total_cells'] or 1)):+.0f}%)")
        # how much of total over is red vs base (decomposition identity check, raw basis)
        red_share = avg(lambda r: r['rv50']-r['t_red_val'])
        base_share = avg(lambda r: r['base_eng']-r['t_nonred_raw'])
        tot = avg(lambda r: r['bal']-r['t_val_raw'])
        print(f"\n  IDENTITY (raw): 参考over({_m(tot)}M) = RED over({_m(red_share)}M) + BASE over({_m(base_share)}M)")
        if abs(tot) > 1:
            print(f"  -> RED channel = {100*red_share/tot:.0f}% of over;  BASE channel = {100*base_share/tot:.0f}% of over")


if __name__ == "__main__":
    main()
