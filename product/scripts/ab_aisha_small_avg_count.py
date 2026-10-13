"""⚠ 归档实验脚本(早期阶段)。校准请优先用 scripts/calibration/ 工具包(核心 aisha_calib.py:
有效样本 / 长尾规范化 / clean-normalized 分块)。本脚本沿用旧口径，后续窗口注意:
  - AISHA_SMALL_AVG 杠杆已证伪(见 memory value-accuracy-q6-count-undercount / aisha-red-residual)。
  - 量的是 count/grid(非价值)，长尾规范化不涉及；样本=用户实战导出 zip(非 fatbeans 库)。
  - 若改成评估 VALUE，必须改用 aisha_calib.normalized_truth(裸真值含长尾→假欠估)。

Adversarial A/B verify for the size-aware avg-cells COUNT lift (AISHA_SMALL_AVG knobs).

Loads aisha R3/R4 snapshots from the export zips (round_snapshots/r0X_bidding.json,
truth = matching *_settled.json inventory_count / inventory_cells), dedup by session id.
Runs each twice: baseline (knobs unset) vs fix (env from --fix-env). Reports count+grid
bias vs truth, split by no_large/has_large (max visible item cells < BIG_THR), R3/R4, and
flags dense-vault under-counts and deep-vault grid-unders.

Usage:
  python scripts/ab_aisha_small_avg_count.py --fix-env "AISHA_SMALL_AVG_TARGET=2.55,AISHA_SMALL_AVG_STRENGTH=0.6"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as E  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402

BIG_THR = 8  # max visible cells >= this => has_large => lever gated OFF


def _mid(t):
    if not isinstance(t, (list, tuple)) or len(t) < 2 or t[1] is None:
        return None
    return int(t[1])


def _set_env(spec):
    keys = []
    if not spec:
        return keys
    for part in spec.split(","):
        if "=" in part:
            k, _, v = part.partition("=")
            os.environ[k.strip()] = v.strip()
            keys.append(k.strip())
    return keys


def _clear_env(keys):
    for k in keys:
        os.environ.pop(k, None)


def _max_vis(snap):
    items = E._minimap_items_from_snapshot(snap)
    vc = []
    for it in (items or []):
        c = E._safe_int(it.get("cells"))
        if not c:
            w = E._safe_int(it.get("width")) or 0
            h = E._safe_int(it.get("height")) or 0
            c = w * h
        if c:
            vc.append(int(c))
    return (max(vc) if vc else 0), len(vc)


def _run(snap, rnd):
    res = run_reference_engine(
        prepare_reference_engine_snapshot(snap),
        max_combos=hero_max_combos_for_round("aisha", rnd),
    ).as_dict()
    return res.get("total_count_estimate"), _mid(res.get("total_grid_range") or [None, None, None])


def _session_of(name):
    # trailing session id (the user re-dumps the same session many times); NOT the date prefix
    m = re.search(r"_(\d+)\.zip$", name)
    return m.group(1) if m else name


def _map_of(name):
    m = re.search(r"-(\d{4})_", name)
    return m.group(1) if m else "?"


def collect():
    zips = list((ROOT / "data/samples/hero_ref/archive/exports").glob("2026-06-*/*.zip"))
    zips += list((ROOT / "data/logs/live/exports").glob("*.zip"))
    rows = []
    seen = {}
    for z in sorted(zips):
        try:
            with zipfile.ZipFile(z) as zf:
                names = set(zf.namelist())
                bids = sorted(n for n in names if re.match(r"round_snapshots/r0\d_bidding\.json$", n))
                if not bids:
                    continue
                # truth from the deepest settled file in this zip
                settled = sorted(n for n in names if re.match(r"round_snapshots/r0\d_settled\.json$", n))
                if not settled:
                    continue
                st = json.loads(zf.read(settled[-1]))
                if st.get("hero") != "aisha":
                    continue
                t_cnt = st.get("inventory_count")
                t_cells = st.get("inventory_cells")
                if not t_cnt or not t_cells:
                    continue
                for bn in bids:
                    rnd = int(re.search(r"r0(\d)_", bn).group(1))
                    if rnd < 3:
                        continue
                    snap = json.loads(zf.read(bn))
                    if snap.get("hero") != "aisha":
                        continue
                    mv, nvis = _max_vis(snap)
                    key = (_session_of(z.name), rnd)
                    if key in seen:
                        continue
                    seen[key] = True
                    rows.append({
                        "z": z.name, "map": _map_of(z.name), "rnd": rnd,
                        "t_cnt": int(t_cnt), "t_cells": int(t_cells),
                        "max_vis": mv, "n_vis": nvis, "snap": snap,
                    })
        except Exception:  # noqa: BLE001
            continue
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix-env", default="AISHA_SMALL_AVG_TARGET=2.55,AISHA_SMALL_AVG_STRENGTH=0.6")
    ap.add_argument("--deep-top", type=int, default=12)
    args = ap.parse_args()

    rows = collect()
    print(f"collected {len(rows)} aisha R3/R4 export snaps (dedup by session+round)", flush=True)

    # baseline (ensure clean env)
    for k in ("AISHA_SMALL_AVG_TARGET", "AISHA_SMALL_AVG_STRENGTH", "AISHA_DISABLE_SMALL_AVG_LIFT"):
        os.environ.pop(k, None)
    for r in rows:
        r["b_cnt"], r["b_grid"] = _run(r["snap"], r["rnd"])

    keys = _set_env(args.fix_env)
    for r in rows:
        r["f_cnt"], r["f_grid"] = _run(r["snap"], r["rnd"])
    _clear_env(keys)

    dropped = [r for r in rows if r["b_cnt"] is None or r["f_cnt"] is None or r["b_grid"] is None or r["f_grid"] is None]
    if dropped:
        print(f"  (dropped {len(dropped)} rows with None count/grid: maps {sorted(set(r['map'] for r in dropped))})")
    rows = [r for r in rows if r["b_cnt"] is not None and r["f_cnt"] is not None and r["b_grid"] is not None and r["f_grid"] is not None]

    def summ(sub, label):
        if not sub:
            return
        n = len(sub)
        bcb = sum(r["b_cnt"] - r["t_cnt"] for r in sub) / n
        fcb = sum(r["f_cnt"] - r["t_cnt"] for r in sub) / n
        bgb = sum(r["b_grid"] - r["t_cells"] for r in sub) / n
        fgb = sum(r["f_grid"] - r["t_cells"] for r in sub) / n
        print(f"  {label:14s} n={n:>3d} | countBias {bcb:+6.2f} -> {fcb:+6.2f} | gridBias {bgb:+6.2f} -> {fgb:+6.2f}")

    print("\n=== count + grid bias (baseline -> fix) ===")
    summ(rows, "ALL")
    summ([r for r in rows if r["max_vis"] < BIG_THR], "no_large")
    summ([r for r in rows if r["max_vis"] >= BIG_THR], "has_large")
    summ([r for r in rows if r["rnd"] == 3], "R3")
    summ([r for r in rows if r["rnd"] == 4], "R4")

    # dense under-count: fix count newly < 92% truth on a vault that was NOT under at baseline
    print("\n=== regressions introduced by fix ===")
    dense_under = [r for r in rows if r["f_cnt"] < 0.92 * r["t_cnt"] and r["b_cnt"] >= 0.92 * r["t_cnt"]]
    grid_under = [r for r in rows if r["f_grid"] < 0.92 * r["t_cells"] and r["b_grid"] >= 0.92 * r["t_cells"]]
    print(f"  newly count-under(<92% truth): {len(dense_under)}")
    for r in dense_under:
        print(f"    {r['map']} R{r['rnd']} t_cnt={r['t_cnt']} {r['b_cnt']}->{r['f_cnt']}  max_vis={r['max_vis']}")
    print(f"  newly grid-under(<92% truth):  {len(grid_under)}")
    for r in grid_under:
        print(f"    {r['map']} R{r['rnd']} t_cells={r['t_cells']} {r['b_grid']}->{r['f_grid']}  max_vis={r['max_vis']}")

    # deepest-truth vaults: are they pushed under?
    print(f"\n=== deepest-truth vaults (top {args.deep_top} by t_cells) ===")
    for r in sorted(rows, key=lambda r: -r["t_cells"])[: args.deep_top]:
        cf = "U" if r["f_cnt"] < 0.92 * r["t_cnt"] else " "
        gf = "U" if r["f_grid"] < 0.92 * r["t_cells"] else " "
        print(f"  {r['map']} R{r['rnd']} t={r['t_cnt']}/{r['t_cells']} cnt {r['b_cnt']}->{r['f_cnt']}{cf} grid {r['b_grid']}->{r['f_grid']}{gf} mv={r['max_vis']}")

    # flagged maps
    print("\n=== flagged maps (2407 / 2406 / 2401) ===")
    for mp in ("2407", "2406", "2401"):
        sub = [r for r in rows if r["map"] == mp]
        for r in sub:
            chg = "" if (r["f_cnt"] == r["b_cnt"] and r["f_grid"] == r["b_grid"]) else "  <-CHANGED"
            print(f"  {mp} R{r['rnd']} t={r['t_cnt']}/{r['t_cells']} cnt {r['b_cnt']}->{r['f_cnt']} grid {r['b_grid']}->{r['f_grid']} mv={r['max_vis']}{chg}")


if __name__ == "__main__":
    raise SystemExit(main())
