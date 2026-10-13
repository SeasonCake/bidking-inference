"""A/B-evaluate the draw-hero occupancy-cap fix on the SYNTHETIC dataset (RANDOM SUBSET).

╔══════════════════════════════════════════════════════════════════════════════════════════╗
║  ⚠️  TIME COST — READ BEFORE RUNNING  ⚠️                                                   ║
║  Each sample runs the engine TWICE (OFF then ON) at max_combos=8000 ≈ 6-9s/run            ║
║  => ~12-18s PER SAMPLE. The full dataset is 500 gabriela + 500 sophie = 1000 samples      ║
║  => a FULL OFF/ON sweep is ~3-5 HOURS. DO NOT blind-scan the full set for a quick check.  ║
║                                                                                            ║
║  This script RANDOM-SAMPLES a small subset by default (--n per hero). That is the         ║
║  intended usage. Scale up ONLY with an explicit --n + a generous wall-clock timeout, and   ║
║  ONLY when the smaller subset is ambiguous. It prints an ETA after the first few samples — ║
║  if the ETA is larger than your budget, Ctrl-C and lower --n.                              ║
║                                                                                            ║
║  Synthetic bias carries a ~-2 count offset vs real (trend-confirmation proxy, NOT ground   ║
║  truth — the real-sample audit _tmp_boost_contribution.py / audit_hero_total_count_bias.py ║
║  is authoritative). Always read the per-SIZE-BRACKET rows, not just the mean.              ║
╚══════════════════════════════════════════════════════════════════════════════════════════╝

Run (recommended): python scripts/eval_draw_occ_cap.py --n 60
                   python scripts/eval_draw_occ_cap.py gabriela --n 40 --seed 11
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import os  # noqa: E402

import synthesize_draw_hero_samples as syn  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

QMAP = syn.QMAP
SPLIT = 42  # small/mid vs large vault boundary (bias is bimodal — always read brackets)
DATASET = ROOT / "data" / "samples" / "synthetic_draw"


def _est(record: dict, flag_on: bool) -> int:
    if flag_on:  # 占格夹 ON = 已发布默认
        os.environ.pop("DRAW_HERO_DISABLE_OCC_CAP", None)
    else:  # OFF = rollback(无夹)
        os.environ["DRAW_HERO_DISABLE_OCC_CAP"] = "1"
    res = run_reference_engine(prepare_reference_engine_snapshot(record["snapshot"]), max_combos=8000).as_dict()
    ranges = res.get("quality_count_ranges") or {}
    return sum(int((ranges.get(q) or [0, 0, 0])[1] or 0) for q in QMAP)


def _stat(xs):
    return (f"bias={statistics.mean(xs):+.1f} MAE={statistics.mean(abs(x) for x in xs):.1f} "
            f"over%={100 * sum(1 for x in xs if x > 0) / len(xs):.0f}")


def run(hero: str, n: int, seed: int) -> None:
    files = sorted(glob.glob(str(DATASET / hero / "synth_*.json")))
    if not files:
        print(f"{hero}: no dataset at {DATASET / hero} (run: synthesize_draw_hero_samples.py emit {hero} 500)")
        return
    rng = random.Random(seed)
    picked = rng.sample(files, min(n, len(files)))  # RANDOM SUBSET, not a full scan
    rows: list[tuple] = []  # (truth, off, on)
    t0 = time.time()
    for i, f in enumerate(picked, 1):
        rec = json.loads(Path(f).read_text(encoding="utf-8"))
        truth = int(rec["truth_count"])
        rows.append((truth, _est(rec, False), _est(rec, True)))
        if i == 5:  # ETA guard: extrapolate after a representative few, warn if over budget
            per = (time.time() - t0) / 5
            print(f"  [{hero}] ETA ~{per * len(picked):.0f}s for {len(picked)} samples "
                  f"({per:.1f}s/sample). Ctrl-C + lower --n if over budget.", flush=True)
        if i % 20 == 0:
            print(f"  [{hero}] {i}/{len(picked)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"\n=== {hero} synthetic OFF/ON (random n={len(rows)}, ~-2 offset vs real) ===")
    for lo, hi, lbl in [(0, 999, "ALL"), (0, SPLIT, f"small<= {SPLIT}"), (SPLIT + 1, 999, f"large> {SPLIT}")]:
        sub = [r for r in rows if lo <= r[0] <= hi]
        if not sub:
            continue
        off = [r[1] - r[0] for r in sub]
        on = [r[2] - r[0] for r in sub]
        print(f"  [{lbl}] n={len(sub)}")
        print(f"      OFF (no cap): {_stat(off)}")
        print(f"      ON  (cap)   : {_stat(on)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Random-subset OFF/ON eval of the draw-hero occ-cap fix.")
    ap.add_argument("heroes", nargs="*", default=["gabriela", "sophie"])
    ap.add_argument("--n", type=int, default=60, help="RANDOM samples per hero (default 60 ≈ 15-20 min/hero). "
                    "Scale up only with budget + timeout — full 500 is ~2h/hero.")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    for h in (args.heroes or ["gabriela", "sophie"]):
        run(h, args.n, args.seed)
