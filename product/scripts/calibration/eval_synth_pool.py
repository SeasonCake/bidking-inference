"""合成池评测: 直接跑引擎 over 预生成的合成 snapshot(无需重建 monitor, 快), 比估计 vs 结算真值,
按地图族(56xx排位/25xx沉船/24xx别墅/其它)+ era 分解 件数/红件/总格/价值 bias。
交叉确认真实样本基线的两个偏差(56xx价值过估 / 25xx-24xx红欠估)。

用法: python scripts/calibration/eval_synth_pool.py [hero=aisha] [era=NEW|OLD|ALL] [limit] [max_combos]
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


def _fam(map_id: int) -> str:
    m = int(map_id or 0)
    if m // 100 == 56:
        return "56xx排位"
    t = (m // 100) % 10
    return {5: "25xx沉船类", 4: "24xx别墅类"}.get(t, f"{m // 100}xx")


def _cnt_mid(res, q):
    r = (res.get("quality_count_ranges") or {}).get(q) or [0, 0, 0]
    return int(r[1] or 0)


def main(argv):
    hero = argv[1] if len(argv) > 1 else "aisha"
    era = argv[2] if len(argv) > 2 else "NEW"
    limit = int(argv[3]) if len(argv) > 3 else 100000
    max_combos = int(argv[4]) if len(argv) > 4 else 6000
    files = sorted(glob.glob(str(ROOT / f"data/samples/synthetic_draw/{hero}/synth_{hero}_*.json")))
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
        true_red = tc.get(6, 0)
        est_total = sum(_cnt_mid(res, q) for q in ("q1", "q3", "q4", "q5", "q6"))
        rows.append({
            "fam": _fam(rec.get("map_id") or 0), "round": rec.get("round"),
            "truth_count": rec["truth_count"], "true_red": true_red,
            "truth_cells": rec.get("truth_cells"), "truth_value": rec.get("truth_total_value"),
            "est_count": est_total, "est_red": _cnt_mid(res, "q6"),
            "est_grid": (res.get("total_grid_range") or [None, None, None])[1],
            "est_value": res.get("balanced"),
        })
        if len(rows) % 100 == 0:
            print(f"  {len(rows)} ({time.time()-t0:.0f}s)", flush=True)

    def bias(rs, ek, tk, nonzero=False):
        b = [(r[ek] - r[tk]) / r[tk] * 100 for r in rs if r.get(ek) is not None and r.get(tk) and (not nonzero or r[tk] > 0)]
        if not b:
            return None
        return {"n": len(b), "median": round(st.median(b), 1), "mean": round(st.mean(b), 1),
                "MAE": round(st.mean([abs(x) for x in b]), 1)}

    def report(rs, tag):
        print(f"\n=== {tag} (n={len(rs)}) ===")
        for lbl, ek, tk, nz in [("件数", "est_count", "truth_count", False), ("红件", "est_red", "true_red", True),
                                ("总格", "est_grid", "truth_cells", False), ("价值", "est_value", "truth_value", False)]:
            b = bias(rs, ek, tk, nz)
            if b:
                print(f"  {lbl:5} median={b['median']:+6.1f}% mean={b['mean']:+6.1f}% MAE={b['MAE']:5.1f}% (n={b['n']})")

    report(rows, f"{hero} {era} 全部")
    byfam = defaultdict(list)
    for r in rows:
        byfam[r["fam"]].append(r)
    for fam in sorted(byfam, key=lambda x: -len(byfam[x])):
        report(byfam[fam], fam)
    out = ROOT / f"data/reports/eval_synth_pool_{hero}_{era}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"\n[out] {out} ({len(rows)} rows, {time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
