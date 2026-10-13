"""样本级保真度诊断: 对【同一个源文件】(同仓库, 绝不错配), 把真实艾莎决策快照与合成艾莎快照
(对齐到同一轮)分别喂引擎, 逐项打印两边的引擎证据 + 估计 + 结算真值, 定位合成过估的幻影从哪条
证据炸开(fixed_counts 锁 vs min_counts floor / 质格 / grid 目标 / 红 count range)。

用法: python scripts/calibration/diagnose_synth_vs_real_one.py <源文件名子串> [round]
  不给文件名 -> 默认诊断几个高分歧样本。
"""
from __future__ import annotations

import glob
import random
import sys
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
from synthesize_draw_hero_samples import load_warehouse, synthesize_snapshot, reveal_trace  # noqa: E402
import aisha_calib as C  # noqa: E402

_TABLES = None
DIRS = ["data/samples/group_0625_56xx", "data/samples/group_post0625_newrate_classic", "data/samples/fatbeans"]


def _tables():
    global _TABLES
    if _TABLES is None:
        _TABLES = load_monitor_tables()
    return _TABLES


def _truncate(ev, cut):
    k = lambda seq: tuple(r for r in seq if int(r.sort_id) < cut)  # noqa: E731
    return FB.FatbeansCaptureEvents(packets=k(ev.packets), frames=k(ev.frames), sends=k(ev.sends),
                                    states=k(ev.states), statuses=k(ev.statuses))


def _ev_fields(res):
    e = res.get("evidence", {}) or {}
    qcr = res.get("quality_count_ranges") or {}
    return {
        "est_total": sum(int((qcr.get(q) or [0, 0, 0])[1] or 0) for q in ("q1", "q3", "q4", "q5", "q6")),
        "balanced": res.get("balanced"), "value_p50": res.get("value_p50"),
        "red_cnt": (res.get("red_count_range") or [None, None, None])[1],
        "grid": (res.get("total_grid_range") or [None, None, None]),
        "q_counts": {q: (qcr.get(q) or [None, None, None])[1] for q in ("q1", "q3", "q4", "q5", "q6")},
        "total_count": e.get("total_count"),
        "fixed_counts": e.get("fixed_counts"), "min_counts": e.get("min_counts"),
        "quality_cells": e.get("quality_cells"), "quality_cell_floors": e.get("quality_cell_floors"),
        "total_grid_target": e.get("total_grid_target"),
        "split_counts": e.get("split_counts"),
        "damp_notes": [n for n in (e.get("source_notes") or []) if "no_red" in n or "damp" in n or "mask" in n],
    }


def _find(sub):
    for d in DIRS:
        for f in glob.glob(str(ROOT / d / "*.json")):
            if sub in Path(f).name:
                return Path(f)
    return None


def diagnose(sub, force_round=None, max_combos=6000):
    f = _find(sub)
    if f is None:
        print(f"未找到 {sub}")
        return
    ev = parse_fatbeans_capture(f)
    wh = load_warehouse(f, with_geometry=True)
    truth = int((C.normalized_truth(ev) or {}).get("norm_value") or 0)
    # real decision
    bids = sorted((s for s in ev.sends if getattr(s, "kind", "") == "bid"), key=lambda s: int(s.sort_id))
    real = realr = None
    if bids:
        art = build_monitor_artifact_from_events(_truncate(ev, int(bids[-1].sort_id)), file="r.json",
                                                 tables=_tables(), n_trials=1, roi_trials=1, run_debug_shadows=False)
        rsnap = prepare_reference_engine_snapshot(art)
        realr = (((rsnap or {}).get("ui_contract") or {}).get("context") or {}).get("round")
        real = _ev_fields(run_reference_engine(rsnap, max_combos=max_combos).as_dict())
    rnd = int(force_round) if force_round else (int(realr) if realr else 4)
    rnd = min(max(rnd, 1), 5)
    ssnap = synthesize_snapshot(wh, "aisha", rnd, random.Random(0))
    synth = _ev_fields(run_reference_engine(prepare_reference_engine_snapshot(ssnap), max_combos=max_combos).as_dict())
    # synth reveal summary
    revealed, _log = reveal_trace(wh, "aisha", rnd, random.Random(0))

    print("=" * 78)
    print(f"源文件(同一仓库): {f.name}")
    print(f"map={wh.map_id} 结算真值: 总{wh.total_count}件 truth_counts={dict(wh.truth_counts)} norm_value={truth:,}")
    print(f"真实决策轮(context.round)={realr} | 合成对齐轮=R{rnd} | 合成累计揭示={len(revealed)}件")
    print(f"\n{'字段':22}{'真实':>26}{'合成':>26}")
    def row(lbl, rk, sk=None):
        sk = sk or rk
        rv = real.get(rk) if real else "-"
        print(f"{lbl:22}{str(rv):>26}{str(synth.get(sk)):>26}")
    row("est_total(件)", "est_total")
    row("balanced(价值)", "balanced")
    row(f"  vs真值{truth:,}", "balanced")  # ratio printed below
    if real and real.get("balanced") and synth.get("balanced") and truth:
        print(f"{'  价值bias%':22}{(real['balanced']-truth)/truth*100:>25.0f}%{(synth['balanced']-truth)/truth*100:>25.0f}%")
    row("red_cnt(红件估)", "red_cnt")
    row("q_counts", "q_counts")
    row("grid_range", "grid")
    row("total_count(证据)", "total_count")
    row("fixed_counts", "fixed_counts")
    row("min_counts", "min_counts")
    row("split_counts", "split_counts")
    row("quality_cells", "quality_cells")
    row("quality_cell_floors", "quality_cell_floors")
    row("total_grid_target", "total_grid_target")
    print(f"{'damp/mask notes':22}")
    print(f"  真实: {real.get('damp_notes') if real else '-'}")
    print(f"  合成: {synth.get('damp_notes')}")


def main(argv):
    if len(argv) > 1:
        diagnose(argv[1], force_round=int(argv[2]) if len(argv) > 2 else None)
    else:
        for sub in ["5607_1575875050441379", "2509", "5603"]:
            diagnose(sub)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
