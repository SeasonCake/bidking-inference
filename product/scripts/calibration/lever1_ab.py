"""LEVER1 (aisha no-red count damp) A/B over synth NEW pool, by map family.
Runs the engine over pre-built synth snapshots under several env configs in ONE process
(each config = a dict of env overrides applied around the run), prints a clean ASCII
per-family table for 红件/件数/总格/价值 bias. Reusable for validating the map-aware fix.

用法: python scripts/calibration/lever1_ab.py [era=NEW] [limit] [max_combos]
配置在 CONFIGS 里改。CPU-bound,串行跑(别 fan-out)。
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
for _sub in ("scripts", "hero_ref/src", "src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import ahmad_ref_engine as eng  # noqa: E402

# Each config: (label, {env_key: value, ...}). Applied as os.environ overrides for that pass.
CONFIGS = [
    ("legacy", {"AISHA_NO_RED_DAMP_MAP_AWARE": "0"}),  # old flat full-press
    ("ramp", {"AISHA_NO_RED_DAMP_MAP_AWARE": "1"}),  # new default (ratescale ramp)
    ("OFF", {"AISHA_ENABLE_NO_RED_DAMP": "0"}),
]


def _fam(map_id: int) -> str:
    m = int(map_id or 0)
    if m // 100 == 56:
        return "56xx"
    t = (m // 100) % 10
    return {5: "25xx", 4: "24xx"}.get(t, f"{m // 100}xx")


def _cnt_mid(res, q):
    r = (res.get("quality_count_ranges") or {}).get(q) or [0, 0, 0]
    return int(r[1] or 0)


def _run_one(rec, max_combos):
    res = eng.run_reference_engine(
        eng.prepare_reference_engine_snapshot(rec["snapshot"]), max_combos=max_combos
    ).as_dict()
    tc = {int(k): int(v) for k, v in (rec.get("truth_counts") or {}).items()}
    est_total = sum(_cnt_mid(res, q) for q in ("q1", "q3", "q4", "q5", "q6"))
    return {
        "fam": _fam(rec.get("map_id") or 0),
        "truth_count": rec["truth_count"], "true_red": tc.get(6, 0),
        "truth_cells": rec.get("truth_cells"), "truth_value": rec.get("truth_total_value"),
        "est_count": est_total, "est_red": _cnt_mid(res, "q6"),
        "est_grid": (res.get("total_grid_range") or [None, None, None])[1],
        "est_value": res.get("balanced"),
    }


def _bias(rs, ek, tk, nz=False):
    b = [(r[ek] - r[tk]) / r[tk] * 100 for r in rs
         if r.get(ek) is not None and r.get(tk) and (not nz or r[tk] > 0)]
    if not b:
        return None
    return {"n": len(b), "med": round(st.median(b), 1), "mean": round(st.mean(b), 1),
            "MAE": round(st.mean([abs(x) for x in b]), 1)}


def main(argv):
    era = argv[1] if len(argv) > 1 else "NEW"
    limit = int(argv[2]) if len(argv) > 2 else 100000
    max_combos = int(argv[3]) if len(argv) > 3 else 6000
    files = sorted(glob.glob(str(ROOT / "data/samples/synthetic_draw/aisha/synth_aisha_*.json")))
    recs = []
    for f in files:
        if len(recs) >= limit:
            break
        rec = json.loads(Path(f).read_text(encoding="utf-8"))
        if era != "ALL" and rec.get("era") != era:
            continue
        recs.append(rec)
    print(f"[pool] {len(recs)} {era} samples, max_combos={max_combos}", flush=True)

    results = {}
    for label, envs in CONFIGS:
        saved = {k: os.environ.get(k) for k in envs}
        for k, v in envs.items():
            os.environ[k] = v
        t0 = time.time()
        rows = [_run_one(r, max_combos) for r in recs]
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        results[label] = rows
        print(f"[done] {label} ({time.time()-t0:.0f}s)", flush=True)

    fams = ["56xx", "25xx", "24xx", "ALL"]
    metrics = [("red", "est_red", "true_red", True), ("cnt", "est_count", "truth_count", False),
               ("grid", "est_grid", "truth_cells", False), ("val", "est_value", "truth_value", False)]
    for fam in fams:
        print(f"\n===== family={fam} =====")
        for mlbl, ek, tk, nz in metrics:
            line = f"  {mlbl:5}"
            for label, _ in CONFIGS:
                rs = results[label] if fam == "ALL" else [r for r in results[label] if r["fam"] == fam]
                b = _bias(rs, ek, tk, nz)
                if b:
                    line += f" | {label}: med{b['med']:+6.1f} mean{b['mean']:+6.1f} MAE{b['MAE']:5.1f}(n{b['n']})"
                else:
                    line += f" | {label}: --"
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
