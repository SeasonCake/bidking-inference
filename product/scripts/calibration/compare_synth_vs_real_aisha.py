"""机制保真度对比: 同一仓库, 把「真实艾莎决策快照」与「合成艾莎快照」分别喂引擎, 比较两者估计
是否吻合(以及各自 vs 结算真值的偏差)。验证合成生成机制是否还原真实艾莎行为。

对 0625中午后(NEW)真实艾莎有效样本逐个:
  · 真实: 取最后 bid_send 的决策快照 -> 引擎 -> real_est(件数/价值)
  · 合成: 同一仓库、同决策轮 -> synthesize_snapshot -> 引擎 -> synth_est
  · 真值: 结算 normalized 真值
报告 real/synth 各自 vs 真值的 bias%、以及 synth-vs-real 的差异(机制吻合度)。

用法: python scripts/calibration/compare_synth_vs_real_aisha.py [limit] [max_combos]
输出: data/reports/synth_vs_real_aisha_compare.json (+ 控制台摘要)
"""
from __future__ import annotations

import glob
import json
import random
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

from bidking_lab.live import fatbeans as FB  # noqa: E402
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import build_monitor_artifact_from_events, load_monitor_tables  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from synthesize_draw_hero_samples import load_warehouse, synthesize_snapshot, _keep_map  # noqa: E402
import aisha_calib as C  # noqa: E402

_TABLES = None
DIRS = ["data/samples/group_0625_56xx", "data/samples/group_post0625_newrate_classic"]


def _tables():
    global _TABLES
    if _TABLES is None:
        _TABLES = load_monitor_tables()
    return _TABLES


def _is_aisha(name: str, ev) -> bool:
    if "aisha" in name:
        return True
    for s in ev.states:
        h = FB._hero_mode_from_state(s)
        if h:
            return h == "aisha"
    return False


def _truncate(ev, cut: int):
    k = lambda seq: tuple(r for r in seq if int(r.sort_id) < cut)  # noqa: E731
    return FB.FatbeansCaptureEvents(packets=k(ev.packets), frames=k(ev.frames), sends=k(ev.sends),
                                    states=k(ev.states), statuses=k(ev.statuses))


def _real_decision(ev, max_combos):
    """真实艾莎决策快照(最后 bid)-> 引擎估计 + 决策轮。"""
    bids = sorted((s for s in ev.sends if getattr(s, "kind", "") == "bid"), key=lambda s: int(s.sort_id))
    if not bids:
        return None, None
    cut = int(bids[-1].sort_id)
    art = build_monitor_artifact_from_events(_truncate(ev, cut), file="replay.json", tables=_tables(),
                                             n_trials=1, roi_trials=1, run_debug_shadows=False)
    snap = prepare_reference_engine_snapshot(art)
    rnd = (((snap or {}).get("ui_contract") or {}).get("context") or {}).get("round")
    res = run_reference_engine(snap, max_combos=max_combos).as_dict()
    return res, (int(rnd) if rnd is not None else None)


def _count_mid(res):
    r = res.get("quality_count_ranges") or {}
    return sum(int((r.get(q) or [0, 0, 0])[1] or 0) for q in ("q1", "q3", "q4", "q5", "q6"))


def main(argv):
    limit = int(argv[1]) if len(argv) > 1 else 999
    max_combos = int(argv[2]) if len(argv) > 2 else 6000
    rows = []
    files = []
    for d in DIRS:
        files += glob.glob(str(ROOT / d / "*.json"))
    random.Random(5).shuffle(files)
    t0 = time.time()
    for f in files:
        if len(rows) >= limit:
            break
        nm = Path(f).name
        try:
            ev = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        if not _is_aisha(nm, ev):
            continue
        wh = load_warehouse(Path(f), with_geometry=True)
        if wh is None or wh.total_count <= 0 or not _keep_map(wh.map_id):
            continue
        try:
            truth = int((C.normalized_truth(ev) or {}).get("norm_value") or 0)
        except Exception:
            truth = 0
        if truth <= 0:
            continue
        real_res, rnd = _real_decision(ev, max_combos)
        if real_res is None:
            continue
        rnd = rnd or 4
        synth_snap = synthesize_snapshot(wh, "aisha", min(max(rnd, 3), 5), random.Random((nm, rnd).__hash__() & 0xFFFFFFFF))
        synth_res = run_reference_engine(prepare_reference_engine_snapshot(synth_snap), max_combos=max_combos).as_dict()
        rows.append({
            "sample": nm[:46], "map": wh.map_id, "round": rnd, "truth": truth,
            "true_items": wh.total_count,
            "real_bal": real_res.get("balanced"), "synth_bal": synth_res.get("balanced"),
            "real_cnt": _count_mid(real_res), "synth_cnt": _count_mid(synth_res),
        })
        if len(rows) % 10 == 0:
            print(f"  {len(rows)} ({time.time()-t0:.0f}s)", flush=True)

    def bias(key):
        b = [(r[key] - r["truth"]) / r["truth"] * 100 for r in rows if r.get(key) and r["truth"]]
        return {"n": len(b), "median": round(st.median(b), 1) if b else None,
                "mean": round(st.mean(b), 1) if b else None,
                "MAE": round(st.mean([abs(x) for x in b]), 1) if b else None} if b else None
    # synth-vs-real agreement (value + count)
    val_delta = [abs(r["synth_bal"] - r["real_bal"]) / r["real_bal"] * 100
                 for r in rows if r.get("synth_bal") and r.get("real_bal")]
    cnt_delta = [abs(r["synth_cnt"] - r["real_cnt"]) for r in rows if r.get("synth_cnt") and r.get("real_cnt")]
    summary = {
        "n": len(rows),
        "real_value_bias_vs_truth": bias("real_bal"),
        "synth_value_bias_vs_truth": bias("synth_bal"),
        "synth_vs_real_value_abs_delta_pct": {"median": round(st.median(val_delta), 1) if val_delta else None,
                                              "mean": round(st.mean(val_delta), 1) if val_delta else None},
        "synth_vs_real_count_abs_delta": {"median": round(st.median(cnt_delta), 1) if cnt_delta else None,
                                          "mean": round(st.mean(cnt_delta), 1) if cnt_delta else None},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = ROOT / "data/reports/synth_vs_real_aisha_compare.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
