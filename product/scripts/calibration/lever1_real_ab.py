"""LEVER1 map-aware 真实样本验证: 对真实艾莎 NEW 决策快照做 legacy/mapaware/OFF 三配置 A/B。
策略: 真实决策快照(monitor 忠实重建)只构建一次, 缓存到磁盘; 之后每个 env 配置在缓存快照上跑
引擎(快)。按地图族报告 红件/件数/总格/价值 bias vs 结算真值。

用法: python scripts/calibration/lever1_real_ab.py [build|run] [max_combos]
  build: (重)扫描真实艾莎NEW, monitor 重建决策快照 + 结算真值, 缓存到
         data/reports/_lever1_real_snaps.json
  run:   (快)读缓存, 跑三配置引擎, 按族对比
默认无参 = build(若无缓存) 然后 run。CPU-bound, 串行。
"""
from __future__ import annotations

import glob
import json
import os
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("scripts", "scripts/calibration", "hero_ref/src", "src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

CACHE = ROOT / "data/reports/_lever1_real_snaps.json"
DIRS = ["data/samples/group_0625_56xx", "data/samples/group_post0625_newrate_classic"]


def _fam(map_id: int) -> str:
    m = int(map_id or 0)
    if m // 100 == 56:
        return "56xx"
    t = (m // 100) % 10
    return {5: "25xx", 4: "24xx"}.get(t, f"{m // 100}xx")


def build():
    from bidking_lab.live import fatbeans as FB
    from bidking_lab.live.fatbeans import parse_fatbeans_capture
    from bidking_lab.live.monitor import build_monitor_artifact_from_events, load_monitor_tables
    from ahmad_ref_engine import prepare_reference_engine_snapshot
    from synthesize_draw_hero_samples import load_warehouse, _keep_map
    import aisha_calib as C

    tables = load_monitor_tables()

    def is_aisha(name, ev):
        if "aisha" in name:
            return True
        for s in ev.states:
            h = FB._hero_mode_from_state(s)
            if h:
                return h == "aisha"
        return False

    def truncate(ev, cut):
        k = lambda seq: tuple(r for r in seq if int(r.sort_id) < cut)  # noqa: E731
        return FB.FatbeansCaptureEvents(packets=k(ev.packets), frames=k(ev.frames), sends=k(ev.sends),
                                        states=k(ev.states), statuses=k(ev.statuses))

    _QN = {"q1": (1, 2), "q3": (3,), "q4": (4,), "q5": (5,), "q6": (6,)}
    files = []
    for d in DIRS:
        files += glob.glob(str(ROOT / d / "*.json"))
    out = []
    t0 = time.time()
    for f in files:
        nm = Path(f).name
        try:
            ev = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        if not is_aisha(nm, ev):
            continue
        wh = load_warehouse(Path(f), with_geometry=True)
        if wh is None or wh.total_count <= 0 or not _keep_map(wh.map_id):
            continue
        try:
            truth_val = int((C.normalized_truth(ev) or {}).get("norm_value") or 0)
        except Exception:
            truth_val = 0
        if truth_val <= 0:
            continue
        # settled truth quantities
        st_state = next((s for s in reversed(ev.states) if s.inventory_items), None)
        if not st_state:
            continue
        items = st_state.inventory_items

        def _nv(it):
            nv = C.normalized_item_value(int(getattr(it, "item_id", 0) or 0))
            return float(nv if nv is not None else (getattr(it, "value", 0) or 0))
        red_items = [it for it in items if int(it.quality or 0) == 6]
        gold_items = [it for it in items if int(it.quality or 0) == 5]
        tq = {"truth_count": len(items),
              "true_red": len(red_items),
              "true_red_value": int(sum(_nv(it) for it in red_items)),
              "true_gold": len(gold_items),
              "true_gold_value": int(sum(_nv(it) for it in gold_items)),
              "truth_cells": sum(int(it.cells or 0) for it in items)}
        bids = sorted((s for s in ev.sends if getattr(s, "kind", "") == "bid"), key=lambda s: int(s.sort_id))
        if not bids:
            continue
        cut = int(bids[-1].sort_id)
        art = build_monitor_artifact_from_events(truncate(ev, cut), file="replay.json", tables=tables,
                                                 n_trials=1, roi_trials=1, run_debug_shadows=False)
        snap = prepare_reference_engine_snapshot(art)
        rnd = (((snap or {}).get("ui_contract") or {}).get("context") or {}).get("round")
        out.append({"sample": nm[:46], "map_id": wh.map_id, "fam": _fam(wh.map_id),
                    "round": int(rnd) if rnd is not None else None, "truth_value": truth_val,
                    "snapshot": snap, **tq})
        if len(out) % 10 == 0:
            print(f"  built {len(out)} ({time.time()-t0:.0f}s)", flush=True)
    CACHE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    byf = defaultdict(int)
    for r in out:
        byf[r["fam"]] += 1
    print(f"[cache] {CACHE} ({len(out)} real aisha NEW, {time.time()-t0:.0f}s) {dict(byf)}")


def _rs(lo, hi):
    return {"AISHA_NO_RED_DAMP_MAP_AWARE": "1", "AISHA_NO_RED_DAMP_RATE_LO": str(lo),
            "AISHA_NO_RED_DAMP_RATE_HI": str(hi)}


CONFIGS = [
    ("legacy", {"AISHA_NO_RED_DAMP_MAP_AWARE": "0"}),
    ("ramp.078-.095", _rs(0.078, 0.095)),
    ("ramp.080-.100", _rs(0.080, 0.100)),
    ("ramp.075-.100", _rs(0.075, 0.100)),
    ("ramp.085-.099", _rs(0.085, 0.099)),
    ("OFF", {"AISHA_ENABLE_NO_RED_DAMP": "0"}),
]


def _cnt_mid(res, q):
    r = (res.get("quality_count_ranges") or {}).get(q) or [0, 0, 0]
    return int(r[1] or 0)


def run(max_combos):
    import ahmad_ref_engine as eng
    recs = json.loads(CACHE.read_text(encoding="utf-8"))
    print(f"[pool] {len(recs)} real aisha NEW snapshots, max_combos={max_combos}", flush=True)
    results = {}
    for label, envs in CONFIGS:
        saved = {k: os.environ.get(k) for k in envs}
        for k, v in envs.items():
            os.environ[k] = v
        t0 = time.time()
        rows = []
        for rec in recs:
            res = eng.run_reference_engine(eng.prepare_reference_engine_snapshot(rec["snapshot"]),
                                           max_combos=max_combos).as_dict()
            est_total = sum(_cnt_mid(res, q) for q in ("q1", "q3", "q4", "q5", "q6"))
            rows.append({"fam": rec["fam"], "truth_count": rec["truth_count"], "true_red": rec["true_red"],
                         "true_red_value": rec.get("true_red_value"), "true_gold": rec.get("true_gold"),
                         "true_gold_value": rec.get("true_gold_value"),
                         "truth_cells": rec["truth_cells"], "truth_value": rec["truth_value"],
                         "est_count": est_total, "est_red": _cnt_mid(res, "q6"), "est_gold": _cnt_mid(res, "q5"),
                         "est_red_value": (res.get("red_value_range") or [None, None, None])[1],
                         "est_grid": (res.get("total_grid_range") or [None, None, None])[1],
                         "est_value": res.get("balanced")})
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        results[label] = rows
        print(f"[done] {label} ({time.time()-t0:.0f}s)", flush=True)

    def bias(rs, ek, tk, nz=False):
        b = [(r[ek] - r[tk]) / r[tk] * 100 for r in rs
             if r.get(ek) is not None and r.get(tk) and (not nz or r[tk] > 0)]
        if not b:
            return None
        return {"n": len(b), "med": round(st.median(b), 1), "mean": round(st.mean(b), 1),
                "MAE": round(st.mean([abs(x) for x in b]), 1)}

    metrics = [("red", "est_red", "true_red", True), ("redval", "est_red_value", "true_red_value", True),
               ("cnt", "est_count", "truth_count", False),
               ("grid", "est_grid", "truth_cells", False), ("val", "est_value", "truth_value", False)]
    # red per-item value decomposition (ramp config): est red unit value vs truth red unit value
    print("\n--- red per-item value (config=last ramp) ---")
    for fam in ["56xx", "25xx", "24xx"]:
        for label in [c[0] for c in CONFIGS]:
            rs = [r for r in results[label] if r["fam"] == fam and r.get("true_red") and r.get("est_red")
                  and r.get("true_red_value") and r.get("est_red_value")]
            if not rs:
                continue
            est_unit = st.median([r["est_red_value"] / r["est_red"] for r in rs])
            true_unit = st.median([r["true_red_value"] / r["true_red"] for r in rs])
            print(f"  {fam} {label:14} est_red_unit={est_unit:8.0f} true_red_unit={true_unit:8.0f} "
                  f"ratio={est_unit/true_unit:.2f} (n{len(rs)})")
    for fam in ["56xx", "25xx", "24xx", "ALL"]:
        print(f"\n===== family={fam} =====")
        for mlbl, ek, tk, nz in metrics:
            line = f"  {mlbl:5}"
            for label, _ in CONFIGS:
                rs = results[label] if fam == "ALL" else [r for r in results[label] if r["fam"] == fam]
                b = bias(rs, ek, tk, nz)
                line += f" | {label}: med{b['med']:+6.1f} mean{b['mean']:+6.1f} MAE{b['MAE']:5.1f}(n{b['n']})" if b else f" | {label}: --"
            print(line)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "auto"
    max_combos = int(argv[2]) if len(argv) > 2 else 6000
    if cmd == "build" or (cmd == "auto" and not CACHE.is_file()):
        build()
    if cmd in ("run", "auto"):
        run(max_combos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
