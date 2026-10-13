"""VERIFY H2 (adversarial): does the LOW-RED ahmed-ship over-estimate come from NON-RED
base/grid value over-estimation (q1-q5 cells / total grid estimated high), independent of red count?

Re-runs the decomposition with the Phase1-confirmed fix: _snapshot_at_batch is called WITH
hero="ahmed" (its default is "aisha" -> the engine ran these ahmed ships under the aisha model in
decompose_low_red_ahmed.py / measure_ahmed_ship_strip.py, contaminating every red/base number).

Data source: inbox mirror (desktop data3 is gone). Each capture is a DIR wrapping a same-named
inner *_reset.json; we resolve the inner file. Plus the in-repo fatbeans ahmed ship (2527).

Engine non-red base = balanced(p50) - red_value(rv50)   (engine's own internal split, L1438).
Truth non-red = total_value - q6_value  (raw) and norm_value - per_quality_norm[6] (normalized).
Grid: engine total_grid_range mid vs truth total_cells; q1-q5 cells engine vs truth per quality.

Strip OFF throughout (the conservative red strip only touches the conservative tier; balanced/base/
grid are strip-independent, but we force OFF to be unambiguous).

Read-only. Run: PYTHONIOENCODING=utf-8 python scripts/verify_h2_nonred_base_grid.py
"""
from __future__ import annotations

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

INBOX = ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures"
FATBEANS = ROOT / "data/samples/fatbeans_activity_20260605_shipwreck"
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")
LOW_RED_MAX = 3
DIS = "AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"


def _m(x):
    return f"{(x or 0)/1e6:+.2f}" if x else " 0.00"


def _mu(x):
    return f"{(x or 0)/1e6:.2f}" if x else "0.00"


def _mid(rng):
    return (rng or [None, None, None])[1] or 0


def _inner(cap_dir: Path) -> Path | None:
    """Inbox capture is a dir wrapping a same-named inner reset.json; resolve it.
    If cap_dir is itself a plain file (fatbeans), return it."""
    if cap_dir.is_file():
        return cap_dir
    inner = list(cap_dir.glob("*reset.json"))
    return inner[0] if inner else None


def _ship_files():
    out: list[Path] = []
    for mp in SHIP_MAPS:
        for d in INBOX.glob(f"*_{mp}_*reset.json"):
            f = _inner(d)
            if f:
                out.append(f)
    if FATBEANS.exists():
        out += sorted(FATBEANS.glob("*.json"))
    # de-dup by resolved path
    seen, uniq = set(), []
    for f in out:
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def _hero_of(ev):
    for s in ev.states:
        h = hero_mode_from_state(s)
        if h:
            return str(h).strip().lower()
    return "?"


def main():
    os.environ[DIS] = "1"  # strip OFF (base/grid strip-independent; force OFF for clarity)
    files = _ship_files()
    print(f"candidate ship files: {len(files)} (inbox + fatbeans)\n")
    rows = []
    skip = {"not_ahmed": 0, "invalid": 0, "no_pre": 0, "err": 0}
    for f in files:
        try:
            ev = C._events(f)
            if _hero_of(ev) != "ahmed":
                skip["not_ahmed"] += 1
                continue
            if not C.settlement_is_valid(ev):
                skip["invalid"] += 1
                continue
            bd = C.settlement_breakdown(ev)
            counts = bd.get("counts") or {}
            cells = bd.get("cells") or {}
            values = bd.get("values") or {}
            t_red = int(counts.get(6, 0))
            t_val_raw = bd.get("total_value") or 0
            t_red_val = int(values.get(6, 0))
            t_total_cells = int(bd.get("total_cells") or 0)
            t_red_cells = int(cells.get(6, 0))
            t_nonred_cells = t_total_cells - t_red_cells
            t_nonred_raw = t_val_raw - t_red_val
            nt = C.normalized_truth(ev)
            t_val_norm = nt.get("norm_value") or 0
            t_red_val_norm = (nt.get("per_quality_norm") or {}).get(6, t_red_val)
            t_nonred_norm = t_val_norm - t_red_val_norm

            mp = _map_id_from_events(ev)
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            if not pre:
                skip["no_pre"] += 1
                continue
            dr = min(len(pre), 4)  # R4 decision round (capped at last pre)
            # *** THE FIX: pass hero="ahmed" (default is aisha) ***
            snap_raw = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=dr, map_id=mp, hero="ahmed")
            snap = prepare_reference_engine_snapshot(snap_raw)
            r = run_reference_engine(snap, max_combos=hero_max_combos_for_round("ahmed", 4)).as_dict()
            # sanity: confirm the engine actually ran as ahmed
            ctx_hero = (snap.get("ui_contract", {}).get("context", {}) or {}).get("hero") or snap.get("hero")

            bal = r.get("balanced") or 0
            rv50 = _mid(r.get("red_value_range"))
            red_mid = _mid(r.get("red_count_range"))
            grid_mid = _mid(r.get("total_grid_range"))
            qcells = r.get("quality_cells_ranges") or {}
            base_eng = bal - rv50
            q15_cells_eng = sum(_mid(qcells.get(f"q{i}")) for i in range(1, 6))
            q15_cells_truth = sum(int(cells.get(i, 0)) for i in range(1, 6))

            rows.append({
                "map": mp, "file": f.name, "t_red": t_red, "ctx_hero": ctx_hero,
                "t_val_norm": t_val_norm, "t_val_raw": t_val_raw,
                "t_red_val": t_red_val, "t_total_cells": t_total_cells, "t_red_cells": t_red_cells,
                "t_nonred_cells": t_nonred_cells,
                "t_nonred_raw": t_nonred_raw, "t_nonred_norm": t_nonred_norm,
                "bal": bal, "rv50": rv50, "red_mid": red_mid, "red_rng": r.get("red_count_range"),
                "grid_mid": grid_mid, "grid_rng": r.get("total_grid_range"),
                "base_eng": base_eng, "q15_cells_eng": q15_cells_eng, "q15_cells_truth": q15_cells_truth,
                "all_truth_cells": dict(cells), "all_eng_cells": {k: _mid(v) for k, v in qcells.items()},
            })
        except Exception as e:
            skip["err"] += 1
            print(f"  ERR {f.name[:46]}: {str(e)[:80]}")
    os.environ.pop(DIS, None)

    print(f"valid ahmed ships: {len(rows)}   skipped={skip}")
    hero_check = set(r["ctx_hero"] for r in rows)
    print(f"engine context hero (MUST be ahmed): {hero_check}\n")

    def block(label, subset):
        if not subset:
            print(f"--- {label}: (none)\n")
            return
        print("=" * 118)
        print(f"### {label}  (n={len(subset)})")
        print(f"{'map':>5} {'tRed':>4} | {'参考':>7} {'truthN':>7} {'over':>7} || "
              f"{'baseEng':>7} {'baseTr':>7} {'baseOvr':>7} || {'gridE':>5} {'gridT':>5} {'g%':>5} || "
              f"{'rv50':>6} {'redE':>4} {'q15cE':>6} {'q15cT':>6}")
        for r in sorted(subset, key=lambda x: (x["t_red"], x["map"])):
            over = r["bal"] - r["t_val_norm"]
            base_ovr = r["base_eng"] - r["t_nonred_norm"]
            g_ovr = int(r["grid_mid"]) - int(r["t_total_cells"])
            g_pct = 100 * g_ovr / (r["t_total_cells"] or 1)
            print(f"{r['map']:>5} {r['t_red']:>4} | {_mu(r['bal']):>7} {_mu(r['t_val_norm']):>7} {_m(over):>7} || "
                  f"{_mu(r['base_eng']):>7} {_mu(r['t_nonred_norm']):>7} {_m(base_ovr):>7} || "
                  f"{int(r['grid_mid']):>5} {r['t_total_cells']:>5} {g_pct:>+4.0f}% || "
                  f"{_mu(r['rv50']):>6} {r['red_mid']:>4} {r['q15_cells_eng']:>6} {r['q15_cells_truth']:>6}")
        n = len(subset)

        def avg(g):
            return sum(g(r) for r in subset) / n
        over_n = avg(lambda r: r["bal"] - r["t_val_norm"])
        over_r = avg(lambda r: r["bal"] - r["t_val_raw"])
        red_ovr = avg(lambda r: r["rv50"] - r["t_red_val"])
        base_ovr_n = avg(lambda r: r["base_eng"] - r["t_nonred_norm"])
        base_ovr_r = avg(lambda r: r["base_eng"] - r["t_nonred_raw"])
        grid_ovr = avg(lambda r: r["grid_mid"] - r["t_total_cells"])
        grid_pct = avg(lambda r: 100 * (r["grid_mid"] - r["t_total_cells"]) / (r["t_total_cells"] or 1))
        q15_ovr = avg(lambda r: r["q15_cells_eng"] - r["q15_cells_truth"])
        # MAE of each channel
        base_mae_n = avg(lambda r: abs(r["base_eng"] - r["t_nonred_norm"]))
        red_mae = avg(lambda r: abs(r["rv50"] - r["t_red_val"]))
        print(f"  AVG over(vsNorm)={_m(over_n)}M over(vsRaw)={_m(over_r)}M | "
              f"RED-val-ovr={_m(red_ovr)}M(MAE {_mu(red_mae)}) | "
              f"BASE-ovr norm={_m(base_ovr_n)}M raw={_m(base_ovr_r)}M (MAE {_mu(base_mae_n)}) | "
              f"GRID-ovr={grid_ovr:+.0f}cells({grid_pct:+.0f}%) | q1-5 cells ovr={q15_ovr:+.0f}")
        # share of total over (norm basis)
        if abs(over_n) > 1:
            print(f"  -> of 参考 over(vsNorm): RED channel={100*red_ovr/over_n:.0f}%  BASE channel={100*base_ovr_n/over_n:.0f}%")
        # count how many ships have base over vs base under
        nb_over = sum(1 for r in subset if r["base_eng"] - r["t_nonred_norm"] > 50000)
        nb_under = sum(1 for r in subset if r["base_eng"] - r["t_nonred_norm"] < -50000)
        ng_over = sum(1 for r in subset if r["grid_mid"] - r["t_total_cells"] > 0)
        ng_under = sum(1 for r in subset if r["grid_mid"] - r["t_total_cells"] < 0)
        print(f"  ships: BASE over(> +0.05M)={nb_over}  BASE under(< -0.05M)={nb_under}  | "
              f"GRID over={ng_over}  GRID under={ng_under}  (of {n})\n")

    low = [r for r in rows if r["t_red"] <= LOW_RED_MAX]
    high = [r for r in rows if r["t_red"] > LOW_RED_MAX]
    block("LOW-RED ships (true q6 <= 3) -- H2 TARGET", low)
    block("HIGH-RED ships (true q6 >= 4) -- contrast", high)
    block("ALL ahmed ships", rows)

    # H2 verdict heuristic
    if low:
        n = len(low)
        base_ovr_n = sum(r["base_eng"] - r["t_nonred_norm"] for r in low) / n
        grid_ovr = sum(r["grid_mid"] - r["t_total_cells"] for r in low) / n
        nb_over = sum(1 for r in low if r["base_eng"] - r["t_nonred_norm"] > 50000)
        print("=" * 118)
        print("H2 VERDICT (low-red base/grid over-estimate?):")
        print(f"  mean non-red BASE over (norm) = {_m(base_ovr_n)}M ; ships with base OVER = {nb_over}/{n}")
        print(f"  mean GRID over = {grid_ovr:+.1f} cells")
        if base_ovr_n > 50000 and nb_over > n / 2:
            print("  => H2 SUPPORTED: non-red base is over-estimated on most low-red ships.")
        elif base_ovr_n < -50000:
            print("  => H2 FALSIFIED: non-red base is UNDER-estimated on average (over-estimate is NOT base/grid).")
        else:
            print("  => H2 MIXED/WEAK: base over-estimate is small or not majority.")


if __name__ == "__main__":
    main()
