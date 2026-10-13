"""H4 check: is the ahmed-ship over/under driven by RED PER-CELL PRICE calibration + red KNOWABILITY
(engine undercounts cells/count for giant/sunken red; catalog red unit price too high)?

For every valid ahmed activity-ship capture (mirror inbox, dir-wrapped layout), at the decision round
(last pre-settlement batch, hero='ahmed' per the corrected Phase1 methodology), reverse-derive and
compare to SETTLED truth:
  RED CELLS    : engine q6 cells mid  vs truth q6 cells          (H4: engine undercounts sunken-red cells?)
  RED COUNT    : engine red_count mid vs truth q6 count          (phantom-count lever, for contrast)
  RED $/CELL   : truth q6 value / truth q6 cells  vs engine item_values[q6]/avg-cells-per-item
  RED $/ITEM   : truth q6 value / truth q6 count  vs engine item_values[q6]  (catalog q6 price used)
  CATALOG q6   : item_values[q6] the engine actually used (nest price, aliased base cabin for 45xx)

H4 predicts (per aisha F3 / memory): engine red CELLS << truth cells on big sunk red (undercount), AND
catalog q6 $/item >> truth $/item on low-value small red (~2.4x over) while flat ~62k/cell over-prices
big sunk red 1.24-1.35x. We test both the cell-undercount and the unit-price-too-high halves on AHMED,
and check whether ahmed shares aisha's item_values / has any ahmed-specific red price path.

Strip OFF (raw engine). Read-only. Run: PYTHONIOENCODING=utf-8 python scripts/h4_ahmed_red_price_cells.py
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
from bidking_lab.live.fatbeans import hero_mode_from_state, live_batches_from_fatbeans_events  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

MIRROR = ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures"
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")
FB = ROOT / "data/samples/fatbeans_activity_20260605_shipwreck"
DIS = "AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"


def _mid(rng):
    return (rng or [None, None, None])[1] or 0


def _resolve(p: Path) -> Path:
    """Mirror layout: each *_reset.json is a DIR containing a same-named real file. Unwrap it."""
    if p.is_dir():
        inner = p / p.name
        if inner.is_file():
            return inner
        kids = [k for k in p.iterdir() if k.is_file()]
        if len(kids) == 1:
            return kids[0]
    return p


def _hero(ev):
    for s in ev.states:
        h = hero_mode_from_state(s)
        if h:
            return str(h).strip().lower()
    return "?"


def _captures():
    out = []
    for mp in SHIP_MAPS:
        out += glob.glob(str(MIRROR / f"*_{mp}_*reset.json"))
    if FB.exists():
        out += [str(p) for p in FB.glob("*.json")]
    return sorted(set(out))


def main():
    os.environ[DIS] = "1"  # strip OFF — raw engine
    rows = []
    skipped = {"not_ahmed": 0, "invalid": 0, "no_pre": 0, "err": 0, "unreadable": 0}
    for f in _captures():
        try:
            real = _resolve(Path(f))
            ev = C._events(real)
            if _hero(ev) != "ahmed":
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
            t_red_cells = int(cells.get(6, 0))
            t_red_val = int(values.get(6, 0))
            mp = _map_id_from_events(ev)
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            if not pre:
                skipped["no_pre"] += 1
                continue
            dr = len(pre)  # decision round = LAST pre-settlement batch (Phase1 fix; not min(.,4))
            snap_raw = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=dr, map_id=mp)
            snap = prepare_reference_engine_snapshot(snap_raw)
            r = run_reference_engine(snap, max_combos=hero_max_combos_for_round("ahmed", 4)).as_dict()

            qcells = r.get("quality_cells_ranges") or {}
            q6_cells_eng = _mid(qcells.get("q6"))
            red_mid = _mid(r.get("red_count_range"))
            rv50 = _mid(r.get("red_value_range"))
            # catalog q6 price the engine used: parse from notes nest_price, else reverse from rv50/cells
            notes = r.get("notes") or []
            price_note = next((n for n in notes if "nest_price" in str(n) or "fallback_default_price" in str(n)), "")
            rows.append({
                "map": mp, "file": real.name, "t_red": t_red, "t_red_cells": t_red_cells,
                "t_red_val": t_red_val, "q6_cells_eng": q6_cells_eng, "red_mid": red_mid,
                "rv50": rv50, "price_note": str(price_note), "snap": snap,
            })
        except PermissionError:
            skipped["unreadable"] += 1
        except Exception as e:
            skipped["err"] += 1
            print(f"  ERR {Path(f).name[:46]}: {str(e)[:70]}")
    os.environ.pop(DIS, None)

    # pull the actual item_values[q6] the engine computed for one representative ship per family
    from ahmad_ref_engine import _quality_item_values, load_reference_static_data  # noqa
    sd = load_reference_static_data()
    print(f"\nValid ahmed ships at decision round: {len(rows)}   skipped={skipped}\n")
    print("ITEM_VALUES[q6] (catalog price engine uses; aliased base cabin for 45xx):")
    for mp in sorted({r["map"] for r in rows}):
        iv, note = _quality_item_values(int(mp), sd)
        print(f"  map {mp}: item_values[q6]={iv.get('q6',0)/1e3:.1f}k   ({note})")

    print("\n" + "=" * 118)
    hdr = f"{'map':>5} {'tRed':>4} {'tRedCells':>9} {'engQ6cells':>10} | {'t$/cell':>8} {'t$/item':>8} | {'engRedMid':>9} {'rv50(M)':>8} {'engImpl$/item':>13}"
    print(hdr)
    print("-" * 118)
    agg = {"cell_eng": [], "cell_truth": [], "ppc_truth": [], "ppi_truth": [], "ppi_eng": [],
           "count_eng": [], "count_truth": [], "cell_ratio": [], "ppi_ratio": []}
    for r in sorted(rows, key=lambda x: (x["t_red"], x["map"])):
        tppc = r["t_red_val"] / r["t_red_cells"] if r["t_red_cells"] else 0   # truth $/cell
        tppi = r["t_red_val"] / r["t_red"] if r["t_red"] else 0               # truth $/item
        eppi = r["rv50"] / r["red_mid"] if r["red_mid"] else 0                # engine implied $/item (incl grid adj)
        print(f"{r['map']:>5} {r['t_red']:>4} {r['t_red_cells']:>9} {r['q6_cells_eng']:>10} | "
              f"{tppc/1e3:>7.0f}k {tppi/1e3:>7.0f}k | {r['red_mid']:>9} {r['rv50']/1e6:>8.2f} {eppi/1e3:>12.0f}k")
        if r["t_red"] > 0:
            agg["cell_eng"].append(r["q6_cells_eng"]); agg["cell_truth"].append(r["t_red_cells"])
            agg["ppc_truth"].append(tppc); agg["ppi_truth"].append(tppi); agg["ppi_eng"].append(eppi)
            agg["count_eng"].append(r["red_mid"]); agg["count_truth"].append(r["t_red"])
            if r["t_red_cells"]:
                agg["cell_ratio"].append(r["q6_cells_eng"] / r["t_red_cells"])
            if tppi:
                agg["ppi_ratio"].append(eppi / tppi)

    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else 0

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0

    print("\n" + "=" * 118)
    n = len(agg["cell_truth"])
    print(f"AGGREGATE over ships with true red>0 (n={n}):")
    print(f"  RED CELLS   engine mid: mean={mean(agg['cell_eng']):.1f} med={med(agg['cell_eng']):.0f}  |  "
          f"truth: mean={mean(agg['cell_truth']):.1f} med={med(agg['cell_truth']):.0f}  "
          f"-> eng/truth cell ratio mean={mean(agg['cell_ratio']):.2f} med={med(agg['cell_ratio']):.2f}")
    print(f"  RED COUNT   engine mid: mean={mean(agg['count_eng']):.1f} med={med(agg['count_eng']):.0f}  |  "
          f"truth: mean={mean(agg['count_truth']):.1f} med={med(agg['count_truth']):.0f}  "
          f"-> phantom (eng-truth) mean={mean(agg['count_eng'])-mean(agg['count_truth']):+.1f}")
    print(f"  RED $/CELL  truth: mean={mean(agg['ppc_truth'])/1e3:.0f}k med={med(agg['ppc_truth'])/1e3:.0f}k   "
          f"(engine flat catalog per-CELL claim in F3 ~62k; aisha truth per-cell ~60k)")
    print(f"  RED $/ITEM  truth: mean={mean(agg['ppi_truth'])/1e3:.0f}k med={med(agg['ppi_truth'])/1e3:.0f}k  |  "
          f"engine implied: mean={mean(agg['ppi_eng'])/1e3:.0f}k med={med(agg['ppi_eng'])/1e3:.0f}k  "
          f"-> eng/truth $/item ratio mean={mean(agg['ppi_ratio']):.2f} med={med(agg['ppi_ratio']):.2f}")

    # low-red vs high-red split (H4 says small low-value red over-priced ~2.4x; big sunk red cells undercounted)
    lo = [i for i in range(n) if agg["count_truth"][i] <= 3]
    hi = [i for i in range(n) if agg["count_truth"][i] >= 5]
    for label, idx in (("LOW red (truth<=3)", lo), ("HIGH red (truth>=5)", hi)):
        if not idx:
            continue
        cr = [agg["cell_ratio"][i] for i in idx if i < len(agg["cell_ratio"])]
        pr = [agg["ppi_ratio"][i] for i in idx if i < len(agg["ppi_ratio"])]
        tppc = [agg["ppc_truth"][i] for i in idx]
        print(f"  [{label}] n={len(idx)}  eng/truth CELL ratio med={med(cr):.2f}  "
              f"eng/truth $/item ratio med={med(pr):.2f}  truth $/cell med={med(tppc)/1e3:.0f}k")


if __name__ == "__main__":
    main()
