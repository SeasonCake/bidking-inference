"""伊森合成保真度对比(作者要求: 合成时原样本也合成, 同仓库核对合成vs非合成)。
对每个真实 ethan NEW 样本, 同一仓库分别喂引擎:
  · 真实: 最后 bid 决策快照 -> real_est(件数/总格/价值)
  · 合成: synthesize_snapshot(wh,'ethan',round) full-outline -> synth_est
  · 真值: 结算(件数/总格/normalized价值)
报告 real/synth 各自 vs 真值 bias + synth-vs-real 一致度。验证伊森合成机制是否还原真实。

用法: python scripts/calibration/compare_synth_vs_real_ethan.py [limit] [max_combos]
输出: data/reports/synth_vs_real_ethan_compare.json
"""
from __future__ import annotations

import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("src", "scripts", "scripts/calibration", "hero_ref/src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import random  # noqa: E402

from bidking_lab.live import fatbeans as FB  # noqa: E402
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import build_monitor_artifact_from_events, load_monitor_tables  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from synthesize_draw_hero_samples import load_warehouse, synthesize_snapshot  # noqa: E402
import aisha_calib as C  # noqa: E402

MANIFEST = ROOT / "data" / "sample_manifests" / "era_partition_noon_2026-06-27.json"
_TABLES = None


def _tables():
    global _TABLES
    if _TABLES is None:
        _TABLES = load_monitor_tables()
    return _TABLES


def _hero(ev):
    return next((FB._hero_mode_from_state(s) for s in ev.states if FB._hero_mode_from_state(s)), None)


def _truncate(ev, cut):
    k = lambda seq: tuple(r for r in seq if int(r.sort_id) < cut)  # noqa: E731
    return FB.FatbeansCaptureEvents(packets=k(ev.packets), frames=k(ev.frames), sends=k(ev.sends),
                                    states=k(ev.states), statuses=k(ev.statuses))


def _count_mid(res):
    r = res.get("quality_count_ranges") or {}
    return sum(int((r.get(q) or [0, 0, 0])[1] or 0) for q in ("q1", "q3", "q4", "q5", "q6"))


def _grid_mid(res):
    return (res.get("total_grid_range") or [None, None, None])[1]


def main(argv):
    limit = int(argv[1]) if len(argv) > 1 else 999
    max_combos = int(argv[2]) if len(argv) > 2 else 4000
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    new_paths = [r["path"] for r in man["rows"] if r.get("era") == "NEW"]
    rows = []
    t0 = time.time()
    for fp in new_paths:
        if len(rows) >= limit:
            break
        p = ROOT / fp if not Path(fp).is_absolute() else Path(fp)
        if not p.is_file():
            continue
        try:
            ev = parse_fatbeans_capture(p)
        except Exception:
            continue
        if _hero(ev) != "ethan":
            continue
        wh = load_warehouse(p, with_geometry=True)
        if wh is None or wh.total_count <= 0:
            continue
        st_state = next((s for s in reversed(ev.states) if s.inventory_items), None)
        if not st_state:
            continue
        items = st_state.inventory_items
        truth_count = len(items)
        truth_cells = sum(int(it.cells or 0) for it in items)
        try:
            truth_value = int((C.normalized_truth(ev) or {}).get("norm_value") or 0)
        except Exception:
            truth_value = 0
        if truth_value <= 0:
            continue
        bids = sorted((s for s in ev.sends if getattr(s, "kind", "") == "bid"), key=lambda s: int(s.sort_id))
        if not bids:
            continue
        cut = int(bids[-1].sort_id)
        try:
            art = build_monitor_artifact_from_events(_truncate(ev, cut), file="replay.json", tables=_tables(),
                                                     n_trials=1, roi_trials=1, run_debug_shadows=False)
            real_snap = prepare_reference_engine_snapshot(art)
        except Exception:
            continue
        rnd = (((real_snap or {}).get("ui_contract") or {}).get("context") or {}).get("round") or 5
        real_res = run_reference_engine(real_snap, max_combos=max_combos).as_dict()
        synth_snap = synthesize_snapshot(wh, "ethan", int(rnd), random.Random((p.name, int(rnd)).__hash__() & 0xFFFFFFFF))
        synth_res = run_reference_engine(prepare_reference_engine_snapshot(synth_snap), max_combos=max_combos).as_dict()
        rows.append({"sample": p.name[:46], "map": wh.map_id, "round": int(rnd),
                     "truth_count": truth_count, "truth_cells": truth_cells, "truth_value": truth_value,
                     "real_cnt": _count_mid(real_res), "synth_cnt": _count_mid(synth_res),
                     "real_grid": _grid_mid(real_res), "synth_grid": _grid_mid(synth_res),
                     "real_val": real_res.get("balanced"), "synth_val": synth_res.get("balanced")})
        if len(rows) % 5 == 0:
            print(f"  {len(rows)} ({time.time()-t0:.0f}s)", flush=True)

    def bias(ek, tk):
        b = [(r[ek] - r[tk]) / r[tk] * 100 for r in rows if r.get(ek) is not None and r.get(tk)]
        if not b:
            return None
        return {"n": len(b), "med": round(st.median(b), 1), "mean": round(st.mean(b), 1),
                "MAE": round(st.mean([abs(x) for x in b]), 1)}

    print(f"\n=== ethan synth-vs-real fidelity (n={len(rows)}) ===")
    for lbl, rk, sk, tk in [("件数", "real_cnt", "synth_cnt", "truth_count"),
                            ("总格", "real_grid", "synth_grid", "truth_cells"),
                            ("价值", "real_val", "synth_val", "truth_value")]:
        rb, sb = bias(rk, tk), bias(sk, tk)
        agree = [abs(r[sk] - r[rk]) / r[rk] * 100 for r in rows if r.get(sk) and r.get(rk)]
        amed = round(st.median(agree), 1) if agree else None
        print(f"  {lbl}: real {rb} | synth {sb} | synth-vs-real |Δ|中位={amed}%")
    out = ROOT / "data/reports/synth_vs_real_ethan_compare.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] {out} ({len(rows)} rows, {time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
