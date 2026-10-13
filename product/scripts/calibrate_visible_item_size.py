"""⚠ 归档实验脚本(早期阶段)。校准请优先用 scripts/calibration/ 工具包(核心 aisha_calib.py)。
后续窗口注意:
  - 量 count/cells avg(非价值)，长尾规范化不涉及；样本=实战导出 zip。
  - 同名升级版在 scripts/calibration/calibrate_max_visible_item_avg.py，优先用后者。
  - 若改成评估 VALUE，必须改用 aisha_calib.normalized_truth。

TASK B: calibrate large-item-fraction & mean-visible-item-size -> true avg_cells.

For each aisha export round-snapshot (round_snapshots/rNN_bidding.json) with a matching
*_settled.json truth, compute from the VISIBLE minimap items:
  (i)  large_frac  = fraction of visible items with footprint >= 6 cells
  (ii) mean_size   = mean visible item footprint (cells)
  (iii) max_size   = max visible item footprint
Correlate each feature with:
  - truth_avg      = inventory_cells / inventory_count  (settlement truth avg cells/item)
  - count_overbias = engine total_count_estimate - inventory_count

Report binned feature -> mean(truth_avg), mean(engine count), n; and which feature best
flags the over-count vaults (user's "no large items visible -> count balloons").

Run from bidking-lab:  python scripts/calibrate_visible_item_size.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
import zipfile
from statistics import mean, pstdev

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    p = os.path.join(ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from ahmad_ref_engine import (  # noqa: E402
    _minimap_items_from_snapshot,
    _safe_int,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)

LARGE_CELLS = 6  # user's "biggest ~6 cells" threshold for a large ordinary item


def _item_footprint(item: dict) -> int | None:
    cells = _safe_int(item.get("cells"))
    if cells is not None and cells > 0:
        return int(cells)
    w = _safe_int(item.get("width")) or 1
    h = _safe_int(item.get("height")) or 1
    fp = max(1, int(w) * int(h))
    return fp


def visible_features(snap: dict) -> dict | None:
    items = _minimap_items_from_snapshot(snap)
    fps = [fp for it in items if (fp := _item_footprint(it)) is not None]
    if not fps:
        return None
    return {
        "n_vis": len(fps),
        "large_frac": sum(1 for f in fps if f >= LARGE_CELLS) / len(fps),
        "mean_size": mean(fps),
        "max_size": max(fps),
    }


def session_id_from_zip(path: str) -> str:
    base = os.path.basename(path)
    # ...-MAP_SESSIONDIGITS.zip ; trailing digit group is session id
    stem = base.rsplit(".", 1)[0]
    tail = stem.rsplit("_", 1)[-1]
    return tail


def collect() -> list[dict]:
    rows: list[dict] = []
    zips = sorted(glob.glob(os.path.join(ROOT, "data/samples/hero_ref/archive/exports/2026-06-*/*.zip")))
    seen_sessions: set[tuple] = set()
    for zpath in zips:
        try:
            zf = zipfile.ZipFile(zpath)
        except Exception:
            continue
        names = zf.namelist()
        settled = [n for n in names if n.endswith("_settled.json")]
        if not settled:
            continue
        # load truth from the deepest settled (final warehouse)
        truths = []
        for sn in settled:
            try:
                s = json.loads(zf.read(sn))
            except Exception:
                continue
            if s.get("hero") != "aisha":
                continue
            ic = _safe_int(s.get("inventory_count"))
            icl = _safe_int(s.get("inventory_cells"))
            if ic and icl and ic > 0:
                truths.append((s.get("round") or 0, ic, icl, s.get("map_id"), s.get("session_id")))
        if not truths:
            continue
        truths.sort()
        _, t_count, t_cells, map_id, sess = truths[-1]
        sess_key = (str(map_id), str(sess) or session_id_from_zip(zpath))
        if sess_key in seen_sessions:
            continue
        seen_sessions.add(sess_key)
        truth_avg = t_cells / t_count
        bidding = sorted(n for n in names if n.startswith("round_snapshots/") and n.endswith("_bidding.json"))
        for bn in bidding:
            try:
                snap = json.loads(zf.read(bn))
            except Exception:
                continue
            feats = visible_features(snap)
            if feats is None:
                continue
            try:
                res = run_reference_engine(prepare_reference_engine_snapshot(snap)).as_dict()
            except Exception:
                continue
            eng_count = res.get("total_count_estimate")
            if eng_count is None:
                continue
            rnd = _safe_int(snap.get("round")) or _safe_int(snap.get("action_round")) or 0
            rows.append({
                "zip": os.path.basename(zpath),
                "round": rnd,
                "map_id": map_id,
                "t_count": t_count,
                "t_cells": t_cells,
                "truth_avg": truth_avg,
                "eng_count": float(eng_count),
                "overbias": float(eng_count) - t_count,
                **feats,
            })
    return rows


def corr(xs, ys):
    if len(xs) < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    dy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def bin_report(rows, key, edges, label):
    print(f"\n=== {label} binned -> truth_avg / engine count / overbias ===")
    print(f"{'bin':<14}{'n':>4}{'mean_truth_avg':>16}{'mean_eng_cnt':>14}{'mean_t_cnt':>12}{'mean_overbias':>15}")
    labels = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        sel = [r for r in rows if lo <= r[key] < hi]
        if i == len(edges) - 2:
            sel = [r for r in rows if lo <= r[key] <= hi]
        if not sel:
            labels.append(f"[{lo:g},{hi:g}) n=0")
            continue
        ta = mean(r["truth_avg"] for r in sel)
        ec = mean(r["eng_count"] for r in sel)
        tc = mean(r["t_count"] for r in sel)
        ob = mean(r["overbias"] for r in sel)
        print(f"[{lo:g},{hi:g})".ljust(14) + f"{len(sel):>4}{ta:>16.2f}{ec:>14.1f}{tc:>12.1f}{ob:>+15.2f}")
        labels.append(f"[{lo:g},{hi:g}) n={len(sel)} truth_avg={ta:.2f} eng_cnt={ec:.1f} overbias={ob:+.1f}")
    return labels


def main():
    rows = collect()
    print(f"Collected {len(rows)} aisha round-snapshots with settlement truth (deduped by session).")
    if not rows:
        return
    r3 = [r for r in rows if r["round"] == 3]
    r4 = [r for r in rows if r["round"] == 4]
    print(f"  by round: R3={len(r3)} R4={len(r4)} other={len(rows)-len(r3)-len(r4)}")

    # correlations
    print("\n=== Pearson correlation (feature vs target) ===")
    for feat in ("large_frac", "mean_size", "max_size"):
        c_avg = corr([r[feat] for r in rows], [r["truth_avg"] for r in rows])
        c_ob = corr([r[feat] for r in rows], [r["overbias"] for r in rows])
        print(f"  {feat:<12} vs truth_avg r={c_avg:+.3f}   vs count_overbias r={c_ob:+.3f}")

    # focus R3+R4 (decision rounds) where the bias was characterised
    dr = [r for r in rows if r["round"] in (3, 4)]
    if dr:
        print(f"\n=== R3+R4 only (n={len(dr)}) correlation ===")
        for feat in ("large_frac", "mean_size", "max_size"):
            c_avg = corr([r[feat] for r in dr], [r["truth_avg"] for r in dr])
            c_ob = corr([r[feat] for r in dr], [r["overbias"] for r in dr])
            print(f"  {feat:<12} vs truth_avg r={c_avg:+.3f}   vs count_overbias r={c_ob:+.3f}")

    bins = {}
    bins["large_frac"] = bin_report(rows, "large_frac", [0.0, 0.001, 0.15, 0.30, 0.50, 1.01], "large_frac (>=6 cells)")
    bins["mean_size"] = bin_report(rows, "mean_size", [1.0, 2.0, 2.5, 3.0, 3.5, 10.0], "mean_visible_item_size")
    bins["max_size"] = bin_report(rows, "max_size", [1.0, 5.0, 6.0, 8.0, 12.0, 100.0], "max_visible_item_size")

    # over-count flagging: how well does each feature separate over-count (overbias>=+2.5) vaults?
    print("\n=== over-count separation (overbias >= +2.5 = 'ballooned') ===")
    over = [r for r in rows if r["overbias"] >= 2.5]
    ok = [r for r in rows if r["overbias"] < 2.5]
    print(f"  over-count n={len(over)}  ok n={len(ok)}")
    for feat in ("large_frac", "mean_size", "max_size"):
        mo = mean(r[feat] for r in over) if over else float("nan")
        mk = mean(r[feat] for r in ok) if ok else float("nan")
        so = pstdev([r[feat] for r in over]) if len(over) > 1 else 0.0
        sk = pstdev([r[feat] for r in ok]) if len(ok) > 1 else 0.0
        # separation (Cohen's d-ish)
        pooled = ((so ** 2 + sk ** 2) / 2) ** 0.5 or 1e-9
        d = (mk - mo) / pooled
        print(f"  {feat:<12} over_mean={mo:.3f}  ok_mean={mk:.3f}  sep_d={d:+.2f}")

    # The user's claim concretely: low max_size / low large_frac -> high overbias
    print("\n=== user's hypothesis check: no-large-items group vs has-large group ===")
    no_large = [r for r in rows if r["max_size"] < LARGE_CELLS]
    has_large = [r for r in rows if r["max_size"] >= LARGE_CELLS]
    for name, grp in (("no_large(max<6)", no_large), ("has_large(max>=6)", has_large)):
        if not grp:
            print(f"  {name}: n=0")
            continue
        print(f"  {name:<18} n={len(grp):<3} mean_truth_avg={mean(r['truth_avg'] for r in grp):.2f} "
              f"mean_overbias={mean(r['overbias'] for r in grp):+.2f} "
              f"mean_eng_cnt={mean(r['eng_count'] for r in grp):.1f} mean_t_cnt={mean(r['t_count'] for r in grp):.1f}")

    return rows, bins


if __name__ == "__main__":
    main()
