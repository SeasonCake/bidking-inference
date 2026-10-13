"""3-D (件数 / 价格 / 格子) A/B eval of draw heroes on the SYNTHETIC dataset (RANDOM SUBSET).

╔══════════════════════════════════════════════════════════════════════════════════════════╗
║  ⚠️  TIME COST — READ BEFORE RUNNING  ⚠️                                                   ║
║  Each sample runs the engine TWICE (OFF then ON) ≈ 12-18s/sample. The full dataset is      ║
║  500+500 = 1000 samples => a FULL OFF/ON sweep is ~3-5 HOURS. DO NOT blind-scan the full   ║
║  set. This script RANDOM-SAMPLES a small subset (--n per hero, default 50) — the intended  ║
║  usage. It prints an ETA after the first few samples; if over budget, Ctrl-C and lower --n.║
║  One engine run yields ALL three metrics, so 3-D costs the SAME as the count-only eval.    ║
║                                                                                            ║
║  Caveats: (1) synthetic count carries ~-2 offset vs real (trend proxy, NOT ground truth —  ║
║  real-sample audit is authoritative). (2) 价格真值=目录求和; 已核实(2026-06-23)抽奖有效样本池  ║
║  缺价 0%(红528/金882件全有价)→ value bias 是 REAL 过估/欠估, 不是真值缺口, 可据此调价.          ║
║  (3) 偏差按仓位大小双峰 — 永远读 per-bracket 行, 不只均值. (4) 默认占格夹 ON; OFF=rollback.    ║
╚══════════════════════════════════════════════════════════════════════════════════════════╝

Run: python scripts/eval_draw_hero_3d.py --n 50
     python scripts/eval_draw_hero_3d.py gabriela --n 40 --seed 11
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import synthesize_draw_hero_samples as syn  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

QMAP = syn.QMAP
SPLIT = 42  # small/mid vs large vault boundary (bias is bimodal — always read brackets)
DATASET = ROOT / "data" / "samples" / "synthetic_draw"


def _metrics(record: dict, flag_on: bool) -> tuple[int, float, int]:
    """One engine run -> (count_mid, value_p50, grid_mid)."""
    if flag_on:  # 占格夹 ON = 已发布默认
        os.environ.pop("DRAW_HERO_DISABLE_OCC_CAP", None)
    else:  # OFF = rollback(无夹)
        os.environ["DRAW_HERO_DISABLE_OCC_CAP"] = "1"
    res = run_reference_engine(prepare_reference_engine_snapshot(record["snapshot"]), max_combos=8000).as_dict()
    ranges = res.get("quality_count_ranges") or {}
    count = sum(int((ranges.get(q) or [0, 0, 0])[1] or 0) for q in QMAP)
    value = float(res.get("value_p50") or 0.0)
    grid = res.get("total_grid_range") or [0, 0, 0]
    gmid = int(grid[1] or 0) if isinstance(grid, list) and len(grid) > 1 else 0
    return count, value, gmid


def _abs_stat(xs):
    return (f"bias={statistics.mean(xs):+.1f} MAE={statistics.mean(abs(x) for x in xs):.1f} "
            f"over%={100 * sum(1 for x in xs if x > 0) / len(xs):.0f}")


def _pct_stat(pairs):  # pairs of (est, truth) -> relative bias/MAE %
    rel = [(e - t) / t for e, t in pairs if t > 0]
    if not rel:
        return "n/a"
    return (f"bias={100 * statistics.mean(rel):+.0f}% MAE={100 * statistics.mean(abs(x) for x in rel):.0f}% "
            f"over%={100 * sum(1 for x in rel if x > 0) / len(rel):.0f}")


def run(hero: str, n: int, seed: int) -> None:
    files = sorted(glob.glob(str(DATASET / hero / "synth_*.json")))
    if not files:
        print(f"{hero}: no dataset (run: synthesize_draw_hero_samples.py emit {hero} 500)")
        return
    picked = random.Random(seed).sample(files, min(n, len(files)))  # RANDOM SUBSET, not a full scan
    rows = []  # (truth_count, truth_value, truth_cells, off=(c,v,g), on=(c,v,g))
    t0 = time.time()
    for i, f in enumerate(picked, 1):
        rec = json.loads(Path(f).read_text(encoding="utf-8"))
        rows.append((int(rec["truth_count"]), float(rec["truth_total_value"]), int(rec.get("truth_cells") or 0),
                     _metrics(rec, False), _metrics(rec, True)))
        if i == 5:
            per = (time.time() - t0) / 5
            print(f"  [{hero}] ETA ~{per * len(picked):.0f}s for {len(picked)} ({per:.1f}s/sample). "
                  f"Ctrl-C + lower --n if over budget.", flush=True)
        if i % 20 == 0:
            print(f"  [{hero}] {i}/{len(picked)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"\n=== {hero} 3-D OFF/ON (random n={len(rows)}; count~-2 offset, value 真值缺价偏正) ===")
    for lo, hi, lbl in [(0, 999, "ALL"), (0, SPLIT, f"small<= {SPLIT}"), (SPLIT + 1, 999, f"large> {SPLIT}")]:
        sub = [r for r in rows if lo <= r[0] <= hi]
        if not sub:
            continue
        print(f"  [{lbl}] n={len(sub)}")
        print(f"    件数 count OFF: {_abs_stat([r[3][0] - r[0] for r in sub])}")
        print(f"    件数 count ON : {_abs_stat([r[4][0] - r[0] for r in sub])}")
        print(f"    价格 value OFF: {_pct_stat([(r[3][1], r[1]) for r in sub])}")
        print(f"    价格 value ON : {_pct_stat([(r[4][1], r[1]) for r in sub])}")
        print(f"    格子 grid  OFF: {_abs_stat([r[3][2] - r[2] for r in sub])}")
        print(f"    格子 grid  ON : {_abs_stat([r[4][2] - r[2] for r in sub])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Random-subset 3-D (count/value/grid) OFF/ON eval of draw heroes.")
    ap.add_argument("heroes", nargs="*", default=["gabriela", "sophie"])
    ap.add_argument("--n", type=int, default=50, help="RANDOM samples per hero (default 50 ≈ 12-15 min/hero). "
                    "Scale up only with budget + timeout — full 500 is ~2h/hero.")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    for h in (args.heroes or ["gabriela", "sophie"]):
        run(h, args.n, args.seed)
