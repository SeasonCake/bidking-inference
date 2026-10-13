"""伊森分块误差分析: 跑引擎 over 合成 ethan 池(预生成 snapshot), 按【品质×维度】分解, 定位主误差点。
块 = 逐品质件数(q1/q3/q4/q5/q6) + 逐品质价值(红/金) + 总格 + 总价值 + 总件数, 按族(56xx/其它)×era。
伊森=grid-primary, 总格应准; 主误差预期在金红残差(件数/价值)。

用法: python scripts/calibration/eval_ethan_blocks.py [era=NEW|OLD|ALL] [limit] [max_combos]
"""
from __future__ import annotations

import glob
import json
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("scripts", "hero_ref/src", "src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

QMAP = {"q1": (1, 2), "q3": (3,), "q4": (4,), "q5": (5,), "q6": (6,)}


def _fam(m):
    m = int(m or 0)
    return "56xx" if m // 100 == 56 else f"{(m//100)%10}xx其它"


def _cmid(res, q):
    return int(((res.get("quality_count_ranges") or {}).get(q) or [0, 0, 0])[1] or 0)


def main(argv):
    era = argv[1] if len(argv) > 1 else "NEW"
    limit = int(argv[2]) if len(argv) > 2 else 100000
    max_combos = int(argv[3]) if len(argv) > 3 else 4000
    files = sorted(glob.glob(str(ROOT / "data/samples/synthetic_draw/ethan/synth_ethan_*.json")))
    rows = []
    t0 = time.time()
    for f in files:
        if len(rows) >= limit:
            break
        rec = json.loads(Path(f).read_text(encoding="utf-8"))
        if era != "ALL" and rec.get("era") != era:
            continue
        res = run_reference_engine(prepare_reference_engine_snapshot(rec["snapshot"]), max_combos=max_combos).as_dict()
        tc = {int(k): int(v) for k, v in (rec.get("truth_counts") or {}).items()}
        row = {"fam": _fam(rec.get("map_id")), "round": rec.get("round"),
               "truth_count": rec["truth_count"], "truth_cells": rec.get("truth_cells"),
               "truth_value": rec.get("truth_total_value"),
               "est_count": sum(_cmid(res, q) for q in QMAP),
               "est_grid": (res.get("total_grid_range") or [None, None, None])[1],
               "est_value": res.get("balanced"),
               "est_red_value": (res.get("red_value_range") or [None, None, None])[1]}
        for q, qs in QMAP.items():
            row[f"t_{q}"] = sum(tc.get(x, 0) for x in qs)
            row[f"e_{q}"] = _cmid(res, q)
        rows.append(row)
        if len(rows) % 100 == 0:
            print(f"  {len(rows)} ({time.time()-t0:.0f}s)", flush=True)

    def bias(rs, ek, tk, nz=False):
        b = [(r[ek] - r[tk]) / r[tk] * 100 for r in rs
             if r.get(ek) is not None and r.get(tk) and (not nz or r[tk] > 0)]
        if not b:
            return None
        return {"n": len(b), "med": round(st.median(b), 1), "mean": round(st.mean(b), 1),
                "MAE": round(st.mean([abs(x) for x in b]), 1)}

    def report(rs, tag):
        print(f"\n===== {tag} (n={len(rs)}) =====")
        blocks = [("总件数", "est_count", "truth_count", False), ("总格", "est_grid", "truth_cells", False),
                  ("总价值", "est_value", "truth_value", False), ("红价值", "est_red_value", None, False)]
        for lbl, ek, tk, nz in blocks:
            if tk is None:
                continue
            b = bias(rs, ek, tk, nz)
            if b:
                print(f"  {lbl:6} med={b['med']:+6.1f}% mean={b['mean']:+6.1f}% MAE={b['MAE']:5.1f}% (n{b['n']})")
        print("  -- 逐品质件数 --")
        for q in ("q1", "q3", "q4", "q5", "q6"):
            b = bias(rs, f"e_{q}", f"t_{q}", True)
            if b:
                qn = {"q1": "白绿", "q3": "蓝", "q4": "紫", "q5": "金", "q6": "红"}[q]
                print(f"     {qn}({q}) med={b['med']:+6.1f}% mean={b['mean']:+6.1f}% MAE={b['MAE']:5.1f}% (n{b['n']})")

    report(rows, f"ethan {era} 全部")
    byfam = defaultdict(list)
    for r in rows:
        byfam[r["fam"]].append(r)
    for fam in sorted(byfam, key=lambda x: -len(byfam[x])):
        report(byfam[fam], fam)
    print(f"\n[{len(rows)} rows, {time.time()-t0:.0f}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
