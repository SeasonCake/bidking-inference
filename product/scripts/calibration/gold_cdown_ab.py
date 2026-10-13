"""Aisha gold (q5) count-down A/B on CLEAN REAL NEW (the untested experiment from gold_calib doc §6:
cdown + remaining-grid gate on NEW data). Reuses the hero_new_health aisha NEW cache (decision
snapshots). Reports gold-count + value bias bucketed by vault size (total grid) so the known
compensation trap (small/mid over-fill ENEMY vs big under-fill ACCEPTABLE) is visible, NOT just the
median. cdown only fires R4+ (decision-round gate) — samples deciding earlier are unaffected.

用法: python scripts/calibration/gold_cdown_ab.py [max_combos]
判读(gold_calib doc 纪律): 净赢 = 小/中仓金件过估→~0 且 大仓金件欠估不恶化 且 价值mean收敛 且
56xx不破。补偿性 = 小仓改善靠把误差搬进大仓欠填(MAE假降)。设计gate另跑。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("hero_ref/src", "scripts/calibration", "src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

CACHE = ROOT / "data/reports/_health_aisha_new_snaps.json"

CONFIGS = [
    ("OFF", {}),
    ("cd0.5", {"AISHA_GOLD_COUNT_DOWN": "0.5"}),
    ("cd1.0", {"AISHA_GOLD_COUNT_DOWN": "1.0"}),
    ("cd1.0+rem40", {"AISHA_GOLD_COUNT_DOWN": "1.0", "AISHA_GOLD_COUNT_DOWN_MAX_REMAINING": "40"}),
    ("cd1.0+rem25", {"AISHA_GOLD_COUNT_DOWN": "1.0", "AISHA_GOLD_COUNT_DOWN_MAX_REMAINING": "25"}),
    ("cd1.5+rem25", {"AISHA_GOLD_COUNT_DOWN": "1.5", "AISHA_GOLD_COUNT_DOWN_MAX_REMAINING": "25"}),
]


def _cnt_mid(res, q):
    r = (res.get("quality_count_ranges") or {}).get(q) or [0, 0, 0]
    return int(r[1] or 0)


def _bucket(truth_cells):
    tc = int(truth_cells or 0)
    return "小中仓(<60格)" if tc < 60 else "大仓(>=60格)"


def main(argv):
    import ahmad_ref_engine as eng
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("max_combos", nargs="?", type=int, default=6000)
    parser.add_argument("--cache", type=Path, default=CACHE)
    args = parser.parse_args(argv[1:])
    max_combos = args.max_combos
    recs = json.loads(args.cache.read_text(encoding="utf-8"))
    print(f"[pool] {len(recs)} aisha NEW, max_combos={max_combos}", flush=True)
    results = {}
    for label, envs in CONFIGS:
        saved = {k: os.environ.get(k) for k in envs}
        for k, v in envs.items():
            os.environ[k] = v
        rows = []
        for rec in recs:
            res = eng.run_reference_engine(eng.prepare_reference_engine_snapshot(rec["snapshot"]),
                                           max_combos=max_combos).as_dict()
            rows.append({"fam": rec["fam"], "round": rec.get("round"), "bucket": _bucket(rec.get("truth_cells")),
                         "true_gold": rec.get("true_gold"), "truth_value": rec.get("truth_value"),
                         "est_gold": _cnt_mid(res, "q5"), "est_value": res.get("balanced"),
                         "est_grid": (res.get("total_grid_range") or [None, None, None])[1],
                         "truth_cells": rec.get("truth_cells")})
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        results[label] = rows
        print(f"[done] {label}", flush=True)

    def bias(rs, ek, tk, nz=False):
        b = [(r[ek] - r[tk]) / r[tk] * 100 for r in rs
             if r.get(ek) is not None and r.get(tk) and (not nz or r[tk] > 0)]
        if not b:
            return None
        return {"n": len(b), "med": round(st.median(b), 1), "mean": round(st.mean(b), 1),
                "MAE": round(st.mean([abs(x) for x in b]), 1)}

    groups = [("ALL", lambda r: True), ("小中仓<60", lambda r: r["bucket"].startswith("小")),
              ("大仓>=60", lambda r: r["bucket"].startswith("大")),
              ("56xx", lambda r: r["fam"] == "56xx"), ("25xx", lambda r: r["fam"] == "25xx")]
    for gtag, gf in groups:
        print(f"\n===== {gtag} =====")
        for metric, ek, tk, nz in [("金件", "est_gold", "true_gold", True),
                                   ("总格", "est_grid", "truth_cells", False),
                                   ("价值", "est_value", "truth_value", False)]:
            line = f"  {metric}"
            for label, _ in CONFIGS:
                rs = [r for r in results[label] if gf(r)]
                b = bias(rs, ek, tk, nz)
                line += f" | {label}:{b['med']:+5.0f}/m{b['mean']:+5.0f}/MAE{b['MAE']:4.0f}(n{b['n']})" if b else f" | {label}:--"
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
