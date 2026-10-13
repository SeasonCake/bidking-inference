"""实战复盘: 把一局 HeroRefDiag 导出的逐轮估计 vs 最终结算真值生成一张人类可读对照表。

导出 settled json 只有聚合真值(final_quality_*)，没有逐件 item_id，所以无法逐件规范化
长尾红；改用启发式长尾标注: 红/金每件均价异常高 -> 提示「疑似长尾，引擎按期望估、欠估正常」
(见 memory long-tail-red-contaminates-truth)。

用法(独立): python hero_ref/tools/replay_vs_truth.py <export.zip 或解压dir>
被 overlay 导出按钮在导出后自动调用，产出 <export>.replay.md。读取，零副作用，失败不影响导出。
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
for _p in (_HERE.parents[2] / "hero_ref" / "src", _HERE.parents[2] / "src", _HERE.parents[1] / "src"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

QKEYS = ("q1", "q3", "q4", "q5", "q6")
QLABEL = {"q1": "白绿", "q3": "蓝", "q4": "紫", "q5": "金", "q6": "红"}


def _longtail_threshold(quality_key: str) -> int:
    """该品质「普通(非长尾)」单件价值上限。export 只有聚合真值(无 item_id)，用它做启发式:
    真值里某品质 value/count 超过此值 -> 疑似含长尾(金q5/红q6)。口径统一走 scripts/calibration
    /aisha_calib.max_normal_per_item; overlay/打包环境取不到时回退到内置常量(同量级)。"""
    qn = {"q5": 5, "q6": 6}.get(quality_key)
    if qn is not None:
        try:
            import importlib.util as _ilu
            _core = _HERE.parents[2] / "scripts" / "calibration" / "aisha_calib.py"
            if _core.is_file():
                _spec = _ilu.spec_from_file_location("aisha_calib", _core)
                _m = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_m)
                return int(_m.max_normal_per_item(qn))
        except Exception:
            pass
    return {"q6": 400_000, "q5": 350_000}.get(quality_key, 10 ** 12)


def _parse_kv(text: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in (text or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = int(float(v))
            except ValueError:
                pass
    return out


def _truth(settled: dict[str, Any]) -> dict[str, Any]:
    qc = _parse_kv(settled.get("final_quality_counts"))
    qg = _parse_kv(settled.get("final_quality_cells"))
    qv = _parse_kv(settled.get("final_quality_values"))
    # 引擎 QMAP 把 q2 合并进 q1
    def merge(d):
        return {"q1": d.get("q1", 0) + d.get("q2", 0), "q3": d.get("q3", 0),
                "q4": d.get("q4", 0), "q5": d.get("q5", 0), "q6": d.get("q6", 0)}
    return {
        "counts": merge(qc), "cells": merge(qg), "values": merge(qv),
        "total_count": int(settled.get("inventory_count") or 0),
        "total_cells": int(settled.get("inventory_cells") or 0),
        "total_value": sum(qv.values()),
        "map_id": settled.get("map_id"), "hero": settled.get("hero"),
        "session": settled.get("session_id"),
    }


def _mid(t) -> int:
    return int(t[1]) if isinstance(t, (list, tuple)) and len(t) > 1 and t[1] is not None else 0


def _estimate(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine
    try:
        r = run_reference_engine(prepare_reference_engine_snapshot(snapshot), max_combos=8000).as_dict()
    except Exception:
        return None
    qr = r.get("quality_count_ranges") or {}
    return {
        "round": snapshot.get("round"),
        "grid": _mid(r.get("total_grid_range") or [None] * 3),
        "count": sum(_mid(qr.get(q) or [None] * 3) for q in QKEYS),
        "per_q": {q: _mid(qr.get(q) or [None] * 3) for q in QKEYS},
        "cons": int(r.get("conservative") or 0),
        "bal": int(r.get("balanced") or 0),
        "aggr": int(r.get("aggressive") or 0),
    }


def _load(export: Path) -> tuple[list[dict], dict | None]:
    """返回 (逐轮 bidding 快照列表, settled dict)。支持 zip 或解压 dir。"""
    biddings: list[dict] = []
    settled: dict | None = None
    if export.is_file() and export.suffix == ".zip":
        with zipfile.ZipFile(export) as zf:
            names = sorted(zf.namelist())
            for n in names:
                base = Path(n).name
                if base.endswith("_bidding.json") and "round_snapshots" in n:
                    biddings.append(json.loads(zf.read(n)))
                elif base.endswith("_settled.json"):
                    settled = json.loads(zf.read(n))
    else:
        rd = export / "round_snapshots"
        src = rd if rd.is_dir() else export
        for f in sorted(src.glob("*_bidding.json")):
            biddings.append(json.loads(f.read_text(encoding="utf-8")))
        sf = sorted(src.glob("*_settled.json"))
        if sf:
            settled = json.loads(sf[-1].read_text(encoding="utf-8"))
    return biddings, settled


def build_report(export: Path) -> str:
    biddings, settled = _load(export)
    if settled is None:
        return f"# 实战复盘: {export.name}\n\n(无 settled 真值，可能未结算就导出)\n"
    t = _truth(settled)
    L: list[str] = []
    L.append(f"# 实战复盘 vs 真值: 地图 {t['map_id']}  英雄 {t['hero']}")
    L.append(f"session {t['session']}")
    L.append("")
    L.append(f"**最终真值: {t['total_count']} 件 / {t['total_cells']} 格 · 价值合计 {t['total_value']//1000}k**")
    L.append("")
    # 各品质真值 + 长尾标注
    L.append("| 品质 | 件数 | 格子 | 价值k | 每件均价k | 长尾? |")
    L.append("|---|---|---|---|---|---|")
    flags = []
    for q in QKEYS:
        c = t["counts"][q]; g = t["cells"][q]; v = t["values"][q]
        per = (v / c) if c else 0
        tail = ""
        thr = _longtail_threshold(q)
        if q in ("q5", "q6") and c and per >= thr:
            tail = "⚠疑似长尾"
            flags.append(f"{QLABEL[q]}每件均价 {per/10000:.0f}w(>普通上限{thr//10000}w)")
        L.append(f"| {QLABEL[q]}({q}) | {c} | {g} | {v//1000} | {per//1000:.0f} | {tail} |")
    L.append("")
    if flags:
        L.append(f"> ⚠ **长尾红/金提示**: {'; '.join(flags)}。引擎按形状期望估值、无法预知长尾，**本局欠估属正常**，"
                 f"评估时应把长尾物换成同形状普通价后再比(见 long-tail-red-contaminates-truth)。")
        L.append("")
    # 逐轮估计 vs 真值
    L.append("## 逐轮估计 vs 最终真值")
    L.append(f"| 轮 | 格子估(真{t['total_cells']}) | 件数估(真{t['total_count']}) | "
             f"金估(真{t['counts']['q5']}) | 红估(真{t['counts']['q6']}) | 报价 保/参/激 |")
    L.append("|---|---|---|---|---|---|")
    ests = [e for e in (_estimate(b) for b in biddings) if e]
    for e in ests:
        L.append(f"| R{e['round']} | {e['grid']} | {e['count']} | {e['per_q']['q5']} | {e['per_q']['q6']} | "
                 f"{e['cons']//1000}/{e['bal']//1000}/{e['aggr']//1000} |")
    L.append("")
    # 决策轮(最后一轮)报价 vs 真值
    if ests:
        last = ests[-1]
        dev = last["bal"] - t["total_value"]
        L.append(f"## 决策轮 R{last['round']} 参考档报价 vs 真值")
        L.append(f"- 参考档 **{last['bal']//1000}k** vs 真值 **{t['total_value']//1000}k** → 偏差 **{dev//1000:+d}k**")
        if t["counts"]["q6"] == 0 and last["per_q"]["q6"] > 0:
            L.append(f"- ⚠ 引擎估红 {last['per_q']['q6']} 件但真值红=0 → **phantom 红过估**(red=0 villa 的典型问题)")
        if flags:
            L.append(f"- ⚠ 真值含长尾(见上)，此偏差不代表引擎错；规范化后再判")
    return "\n".join(L)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法: python hero_ref/tools/replay_vs_truth.py <export.zip 或解压dir>")
        return 1
    export = Path(argv[1])
    md = build_report(export)
    out = export.with_suffix(".replay.md") if export.is_file() else (export / "replay_vs_truth.md")
    out.write_text(md, encoding="utf-8")
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
