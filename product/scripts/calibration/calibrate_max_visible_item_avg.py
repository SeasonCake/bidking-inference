"""⚠ 既有实验脚本，早于统一核心 aisha_calib.py。后续窗口注意旧口径:
  - glob `*aisha*4rounds*` 会含 mixed 坏样本 → 应改用 aisha_calib.valid_aisha_samples()。
  - 用裸 _settlement_breakdown(无长尾规范化)；这里量 count/avg(非价值)故不受影响，
    但若改评估 VALUE 必须用 aisha_calib.normalized_truth。新校准一律 import aisha_calib。

TASK A: calibrate MAX-VISIBLE-ITEM-SIZE (cells) -> true avg_cells, to test the
USER lever: the avg-cells expectation used in count = grid / avg must be VISIBLE-ITEM-
SIZE-AWARE. If vaults with a LARGE max-visible item have a HIGHER true avg-cells
(truth_total_cells / truth_item_count), then the engine's flat/low avg over-counts
exactly where no large item is visible (biggest ~6 cells) -> count balloons.

For every aisha R3 + R4 decision snapshot (fatbeans 4round samples + archived live
exports), record:
  - max_vis   : max visible minimap item footprint (cells)
  - mean_vis  : mean visible item footprint
  - large_frac: fraction of visible items with cells >= 6
  - n_vis     : visible item count
  - eng_count : engine total_count_estimate (mid)
  - eng_grid  : engine total_grid_range (mid)
  - eng_avg   : eng_grid / eng_count  (engine's EFFECTIVE avg-cells)
  - tru_count : settlement truth item count
  - tru_cells : settlement truth total cells
  - tru_avg   : tru_cells / tru_count  (TRUTH avg-cells)
Bin by max_vis; per bin report n, mean tru_avg, mean eng_avg, mean count over-bias.

Excludes hidden 26xx maps (knowability口径). Decisive numbers only.
Usage: python scripts/_wf_/calibrate_max_visible_item_avg.py [--limit N]
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
import ahmad_ref_engine as E  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    _minimap_items_from_snapshot,
    _safe_int,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import (  # noqa: E402
    AISHA_SKILL_ROUNDS,
    _skill_ids,
    _snapshot_at_batch,
)

FATBEANS_DIR = ROOT / "data/samples/fatbeans"
ARCHIVE_GLOB = str(ROOT / "data/samples/hero_ref/archive/exports/2026-06-*/*.zip")


def _mid(triplet) -> float | None:
    if not isinstance(triplet, (list, tuple)) or len(triplet) < 2 or triplet[1] is None:
        return None
    return float(triplet[1])


def _item_cells(item: dict) -> int:
    cells = _safe_int(item.get("cells"))
    if cells is not None and cells > 0:
        return int(cells)
    w = _safe_int(item.get("width")) or 1
    h = _safe_int(item.get("height")) or 1
    return max(1, int(w) * int(h))


def _visible_item_size_features(snapshot: dict) -> dict:
    """Visible minimap items = revealed footprints the user can SEE the size of.
    We measure footprint size; quality is irrelevant to the avg-cells lever."""
    items = _minimap_items_from_snapshot(snapshot)
    sizes = [_item_cells(it) for it in items]
    sizes = [s for s in sizes if s > 0]
    if not sizes:
        return {"n_vis": 0, "max_vis": 0, "mean_vis": 0.0, "large_frac": 0.0}
    return {
        "n_vis": len(sizes),
        "max_vis": max(sizes),
        "mean_vis": sum(sizes) / len(sizes),
        "large_frac": sum(1 for s in sizes if s >= 6) / len(sizes),
    }


def _is_excluded_map(map_id) -> bool:
    """Exclude hidden 26xx (and non-2xxx) maps per knowability口径."""
    try:
        m = int(map_id)
    except Exception:  # noqa: BLE001
        return True
    fam = m // 100
    return fam == 26


def _max_vis_bin(mx: int) -> str:
    if mx <= 0:
        return "?none"
    if mx <= 2:
        return "1-2"
    if mx <= 4:
        return "3-4"
    if mx <= 6:
        return "5-6"
    if mx <= 9:
        return "7-9"
    return "10+"


_BIN_ORDER = ["1-2", "3-4", "5-6", "7-9", "10+", "?none"]


def _run_snapshot(snap: dict, round_no: int):
    res = run_reference_engine(
        prepare_reference_engine_snapshot(snap),
        max_combos=hero_max_combos_for_round("aisha", round_no),
    ).as_dict()
    # total_count_estimate is a scalar (mid count); total_grid_range is a (lo,mid,hi) triplet.
    raw_count = res.get("total_count_estimate")
    eng_count = float(raw_count) if isinstance(raw_count, (int, float)) else _mid(raw_count)
    eng_grid = _mid(res.get("total_grid_range") or [None, None, None])
    return eng_count, eng_grid


# ---------------------------------------------------------------------------
# Source 1: fatbeans 4round samples (R3 + R4)
# ---------------------------------------------------------------------------
def collect_fatbeans(limit: int) -> list[dict]:
    samples = sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    if limit:
        samples = samples[:limit]
    rows: list[dict] = []
    start = time.time()
    total = len(samples)
    for k, sample in enumerate(samples, 1):
        try:
            events = parse_fatbeans_capture(sample)
            truth = _settlement_breakdown(events) or {}
            t_count = int(sum((truth.get("counts") or {}).values()))
            t_cells = int(sum((truth.get("cells") or {}).values()))
            map_id = _map_id_from_events(events)
            if _is_excluded_map(map_id) or t_count <= 0 or t_cells <= 0:
                continue
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            snaps = [
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
                for i in range(1, len(pre) + 1)
            ]
        except Exception:  # noqa: BLE001
            continue
        for skill_idx in (2, 3):  # R3, R4
            skill_round, skill_id, _ = AISHA_SKILL_ROUNDS[skill_idx]
            snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
            if snap is None:
                continue
            snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
            feats = _visible_item_size_features(snap)
            try:
                eng_count, eng_grid = _run_snapshot(snap, skill_round)
            except Exception:  # noqa: BLE001
                continue
            if not eng_count or eng_count <= 0:
                continue
            rows.append({
                "src": "fatbeans", "name": sample.name, "map": int(map_id), "round": skill_round,
                **feats,
                "eng_count": eng_count, "eng_grid": eng_grid or 0,
                "eng_avg": (eng_grid / eng_count) if eng_grid else 0.0,
                "tru_count": t_count, "tru_cells": t_cells,
                "tru_avg": t_cells / t_count,
            })
        if k % 20 == 0 or k == total:
            el = time.time() - start
            print(f"  [fatbeans] {k}/{total} rows={len(rows)} el={el:.0f}s "
                  f"eta~{el / k * (total - k):.0f}s", flush=True)
    return rows


# ---------------------------------------------------------------------------
# Source 2: archived live exports (round_snapshots/r0X_bidding.json + r0X_settled.json)
# ---------------------------------------------------------------------------
def _session_id(name: str) -> str:
    base = Path(name).stem
    return base.split("_")[-1] if "_" in base else base


def collect_archive(limit: int) -> list[dict]:
    zips = sorted(glob.glob(ARCHIVE_GLOB))
    rows: list[dict] = []
    seen_session_round: set[tuple] = set()
    start = time.time()
    total = len(zips)
    used_zip = 0
    for k, zpath in enumerate(zips, 1):
        if limit and used_zip >= limit:
            break
        try:
            zf = zipfile.ZipFile(zpath)
            names = set(zf.namelist())
        except Exception:  # noqa: BLE001
            continue
        settled_name = next((n for n in names if n.endswith("_settled.json")), None)
        if settled_name is None:
            continue
        try:
            settled = json.loads(zf.read(settled_name))
        except Exception:  # noqa: BLE001
            continue
        t_count = _safe_int(settled.get("inventory_count"))
        t_cells = _safe_int(settled.get("inventory_cells"))
        if not t_count or not t_cells or t_count <= 0 or t_cells <= 0:
            continue
        sess = _session_id(zpath)
        used_this_zip = False
        for round_no in (3, 4):
            bidding_name = f"round_snapshots/r0{round_no}_bidding.json"
            if bidding_name not in names:
                continue
            try:
                snap = json.loads(zf.read(bidding_name))
            except Exception:  # noqa: BLE001
                continue
            hero = (snap.get("hero") or snap.get("current_hero")
                    or ((snap.get("ui_contract") or {}).get("hero")))
            if str(hero).lower() != "aisha":
                continue
            map_id = snap.get("map_id") or snap.get("model_map_id")
            if _is_excluded_map(map_id):
                continue
            key = (sess, round_no)
            if key in seen_session_round:
                continue
            seen_session_round.add(key)
            feats = _visible_item_size_features(snap)
            try:
                eng_count, eng_grid = _run_snapshot(snap, round_no)
            except Exception:  # noqa: BLE001
                continue
            if not eng_count or eng_count <= 0:
                continue
            used_this_zip = True
            rows.append({
                "src": "archive", "name": Path(zpath).name, "map": int(map_id), "round": round_no,
                **feats,
                "eng_count": eng_count, "eng_grid": eng_grid or 0,
                "eng_avg": (eng_grid / eng_count) if eng_grid else 0.0,
                "tru_count": int(t_count), "tru_cells": int(t_cells),
                "tru_avg": t_cells / t_count,
            })
        if used_this_zip:
            used_zip += 1
        if k % 30 == 0 or k == total:
            el = time.time() - start
            print(f"  [archive] {k}/{total} rows={len(rows)} el={el:.0f}s", flush=True)
    return rows


def _report(rows: list[dict]) -> None:
    if not rows:
        print("NO ROWS")
        return
    print(f"\n=== TOTAL rows={len(rows)} "
          f"(fatbeans={sum(1 for r in rows if r['src']=='fatbeans')}, "
          f"archive={sum(1 for r in rows if r['src']=='archive')}) ===")

    # overall avg comparison + count bias
    eng_avg = sum(r["eng_avg"] for r in rows if r["eng_avg"] > 0)
    n_ea = sum(1 for r in rows if r["eng_avg"] > 0)
    tru_avg = sum(r["tru_avg"] for r in rows) / len(rows)
    cbias = sum(r["eng_count"] - r["tru_count"] for r in rows) / len(rows)
    print(f"overall: mean tru_avg={tru_avg:.3f}  mean eng_avg={eng_avg/max(1,n_ea):.3f}  "
          f"mean count over-bias(eng-tru)={cbias:+.2f}")

    # ---- bin by MAX visible item size ----
    print("\n=== bin by MAX-VISIBLE-ITEM-SIZE (cells) ===")
    hdr = (f"{'max_vis':8s} {'n':>4s} | {'tru_avg':>8s} {'eng_avg':>8s} {'avg_gap':>8s} | "
           f"{'cntBias':>8s} {'cntMAE':>7s} | {'tru_cnt':>7s} {'eng_cnt':>7s} | {'meanVis':>7s} {'lgFrac':>6s}")
    print(hdr)
    print("-" * len(hdr))
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[_max_vis_bin(int(r["max_vis"]))].append(r)
    for b in _BIN_ORDER:
        rs = groups.get(b)
        if not rs:
            continue
        n = len(rs)
        ta = sum(r["tru_avg"] for r in rs) / n
        ea_rs = [r["eng_avg"] for r in rs if r["eng_avg"] > 0]
        ea = sum(ea_rs) / len(ea_rs) if ea_rs else 0.0
        cb = sum(r["eng_count"] - r["tru_count"] for r in rs) / n
        cm = sum(abs(r["eng_count"] - r["tru_count"]) for r in rs) / n
        tc = sum(r["tru_count"] for r in rs) / n
        ec = sum(r["eng_count"] for r in rs) / n
        mv = sum(r["mean_vis"] for r in rs) / n
        lf = sum(r["large_frac"] for r in rs) / n
        print(f"{b:8s} {n:>4d} | {ta:>8.3f} {ea:>8.3f} {ta-ea:>+8.3f} | "
              f"{cb:>+8.2f} {cm:>7.2f} | {tc:>7.1f} {ec:>7.1f} | {mv:>7.2f} {lf:>6.2f}")

    # ---- correlation: does larger max_vis predict higher tru_avg? ----
    import statistics
    xs = [float(r["max_vis"]) for r in rows]
    ys = [r["tru_avg"] for r in rows]
    try:
        corr_max = statistics.correlation(xs, ys)
    except Exception:  # noqa: BLE001
        corr_max = float("nan")
    xs2 = [r["mean_vis"] for r in rows]
    try:
        corr_mean = statistics.correlation(xs2, ys)
    except Exception:  # noqa: BLE001
        corr_mean = float("nan")
    xs3 = [r["large_frac"] for r in rows]
    try:
        corr_lf = statistics.correlation(xs3, ys)
    except Exception:  # noqa: BLE001
        corr_lf = float("nan")
    print(f"\ncorr(max_vis, tru_avg)={corr_max:+.3f}  "
          f"corr(mean_vis, tru_avg)={corr_mean:+.3f}  "
          f"corr(large_frac, tru_avg)={corr_lf:+.3f}")

    # ---- over-count concentration: where is engine over-counting? ----
    print("\n=== over-count (eng_count - tru_count >= +3) concentration by max_vis bin ===")
    for b in _BIN_ORDER:
        rs = groups.get(b)
        if not rs:
            continue
        over = [r for r in rs if (r["eng_count"] - r["tru_count"]) >= 3]
        print(f"  {b:8s} n={len(rs):>3d}  over3={len(over):>3d}  "
              f"share={len(over)/len(rs):.2f}  meanOverBias={sum(r['eng_count']-r['tru_count'] for r in rs)/len(rs):+.2f}")

    # ---- grid vs truth-cells: is the residual over-count a GRID problem or an AVG problem? ----
    # count = grid / avg.  If eng_avg ~= tru_avg per bin, then any count over-bias must come from
    # eng_grid > tru_cells (depth_grid over-states), NOT from a too-low avg.
    print("\n=== eng_grid vs truth_cells (grid over-bias) by max_vis bin ===")
    print(f"  {'bin':8s} {'n':>4s} {'eng_grid':>8s} {'tru_cells':>9s} {'gridBias':>8s}  {'gridBias/avg(~cnt)':>18s}")
    for b in _BIN_ORDER:
        rs = groups.get(b)
        if not rs:
            continue
        n = len(rs)
        eg = sum(r["eng_grid"] for r in rs) / n
        tcl = sum(r["tru_cells"] for r in rs) / n
        ta = sum(r["tru_avg"] for r in rs) / n
        print(f"  {b:8s} {n:>4d} {eg:>8.1f} {tcl:>9.1f} {eg-tcl:>+8.1f}  {(eg-tcl)/max(ta,0.1):>+18.2f}")

    # ---- by round ----
    print("\n=== by round ===")
    for rnd in (3, 4):
        rs = [r for r in rows if r["round"] == rnd]
        if not rs:
            continue
        cb = sum(r["eng_count"] - r["tru_count"] for r in rs) / len(rs)
        ta = sum(r["tru_avg"] for r in rs) / len(rs)
        ea_rs = [r["eng_avg"] for r in rs if r["eng_avg"] > 0]
        ea = sum(ea_rs) / len(ea_rs) if ea_rs else 0.0
        print(f"  R{rnd}: n={len(rs)} cntBias={cb:+.2f} tru_avg={ta:.3f} eng_avg={ea:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap samples per source (0=all)")
    ap.add_argument("--source", choices=["all", "fatbeans", "archive"], default="all")
    args = ap.parse_args()
    rows: list[dict] = []
    if args.source in ("all", "fatbeans"):
        print("collecting fatbeans...", flush=True)
        rows += collect_fatbeans(args.limit)
    if args.source in ("all", "archive"):
        print("collecting archive exports...", flush=True)
        rows += collect_archive(args.limit)
    _report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
