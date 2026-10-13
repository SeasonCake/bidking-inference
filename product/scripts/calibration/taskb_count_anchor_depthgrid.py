"""⚠ 既有实验脚本，早于统一核心 aisha_calib.py。后续窗口注意旧口径:
  - glob `*aisha*4rounds*` 会含 mixed 坏样本 → 应改用 aisha_calib.valid_aisha_samples()。
  - 用裸 _settlement_breakdown(无长尾规范化)；这里量 count/grid(非价值)故不受影响，
    但若改评估 VALUE 必须用 aisha_calib.normalized_truth。新校准一律 import aisha_calib。

TASK B: evaluate count-anchor depth_grid alternatives.

The count anchor (ahmad_ref_engine ~L6892-6917) computes:
    rect_depth_grid = raw_deepest * 10   (+ lone-deep discount, gated)
    depth_grid      = max(known_minimap_cells, rect_depth_grid)
    count_hint      = round(depth_grid / total_avg_cells)
and passes count_hint as the count center. raw_deepest*10 OVER-states the grid
when the deepest revealed rows are NOT full (R3 over). When count is under, the
late-reveal cap tightens and grid goes under (R4 under).

This script replays every aisha R3/R4 export+fatbeans snapshot and, for the
snapshots that ACTUALLY HIT the count-anchor branch (raw_deepest present,
total_avg_cells present, total_count None, strength>=2), recomputes the
count_hint under candidate depth_grid anchors WITHOUT touching the engine, then
compares count_hint vs truth item count, split R3 / R4.

Anchors compared (all max'd with known_minimap_cells, like the engine):
  CUR : current engine depth_grid (rect with shipped lone-deep discount)
  RECT: raw rect raw_deepest*10 (no discount) -- baseline reference
  (b) : _occupancy_adjusted_depth_grid(items, raw_deepest)  -- supported depth + lone tail
  (c) : deepest-VISIBLE-row density: full-row floor if any full row, else
        per-row-fill density extrapolation (NOT raw_deepest*10)

Also reports the engine's ACTUAL produced count (sanity) and grid.
Excludes hidden 26xx. Dedup by session. timeout-safe with progress.
Usage: python scripts/calibration/taskb_count_anchor_depthgrid.py [--limit N] [--src all|archive|fatbeans]
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
    AISHA_GRID_COLUMNS,
    COUNT_ANCHOR_RESIDUAL_ITEM_CELL_ESTIMATE,
    QUALITY_KEYS,
    _deepest_minimap_bottom_row,
    _known_minimap_cells,
    _minimap_items_from_snapshot,
    _occupancy_adjusted_depth_grid,
    _deepest_full_row_grid_floor,
    _exact_integer_quality_cell,
    _high_tier_floor_extra,
    _quality_probabilities,
    _residual_per_item_cell_estimate,
    _snapshot_round_no,
    _safe_int,
    extract_evidence,
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
COLS = AISHA_GRID_COLUMNS


def _mid(t):
    if not isinstance(t, (list, tuple)) or len(t) < 2 or t[1] is None:
        return None
    return float(t[1])


def _is_excluded_map(map_id) -> bool:
    try:
        m = int(map_id)
    except Exception:  # noqa: BLE001
        return True
    return (m // 100) == 26


# ---- anchor (c): deepest-VISIBLE-row density (per-row fill, NOT raw_deepest*10) ----
def _occupied_cells_by_row(items):
    """Return {row: set(cols)} of REVEALED occupancy (1-based row/col)."""
    occ = defaultdict(set)
    for item in items:
        row = _safe_int(item.get("row"))
        col = _safe_int(item.get("col"))
        if row is None or col is None:
            li = _safe_int(item.get("local_index"))
            if li is not None and COLS > 0:
                row = li // COLS + 1
                col = li % COLS + 1
        if row is None or col is None:
            continue
        w = max(1, _safe_int(item.get("width")) or 1)
        h = max(1, _safe_int(item.get("height")) or 1)
        for dr in range(h):
            for dc in range(w):
                cc = col + dc
                if 1 <= cc <= COLS:
                    occ[row + dr].add(cc)
    return occ


def anchor_c_density(items, raw_deepest, known_cells):
    """Deepest-VISIBLE-row density anchor.

    Build occupied-cells-per-row from revealed items. The warehouse packs as a
    contiguous rectangle from the top, so:
      - If a row is FULL (10 cols), the rect is proven full down to it -> use the
        full-row floor (deepest_full_row*10 + cells strictly below).
      - Else NO row proves the rect width at the deepest level. raw_deepest*10
        over-states. Instead extrapolate using the OBSERVED row-fill density:
        density = (sum of observed cells in occupied rows) / (#occupied rows).
        grid = round(raw_deepest * density), clamped to [known_cells, raw_deepest*10].
        This shrinks the deep rows toward how full the revealed rows actually are,
        so a deep-but-sparse tail (2409 R3 row11 = 4 cells) is not counted as a
        full 10-wide row.
    """
    rect = int(raw_deepest) * COLS
    occ = _occupied_cells_by_row(items)
    if not occ:
        return rect
    full_floor = _deepest_full_row_grid_floor(items)
    if full_floor > 0:
        # a row is full -> rect proven down to it; below cells already counted.
        # But rows BELOW the deepest full row may still be sparse: full_floor already
        # = deepest_full*10 + (observed cells below). That's a LOWER bound. Take the
        # larger of full_floor and known_cells.
        return max(int(known_cells or 0), full_floor)
    # No full row: density extrapolation over the full depth.
    occ_rows = sorted(occ.keys())
    observed_cells = sum(len(cs) for cs in occ.values())
    n_rows = len(occ_rows)
    density = observed_cells / n_rows if n_rows else 0.0
    est = int(round(int(raw_deepest) * density))
    est = max(int(known_cells or 0), min(rect, est))
    return est


def _non_total_strength(evidence):
    try:
        return E._non_total_count_evidence_strength(evidence)
    except Exception:  # noqa: BLE001
        return 0


_STATIC = E.load_reference_static_data()


def _hint_from_depth_grid(depth_grid, evidence, snapshot, total_avg, probs):
    """Convert a depth_grid to count_hint EXACTLY as engine L6915-6941 does,
    picking the same branch the engine would (total_avg path vs residual path)."""
    if depth_grid <= 0:
        return None
    if total_avg and total_avg > 0:
        return int(round(depth_grid / float(total_avg)))
    # else branch: residual per-item conversion
    per_item = _residual_per_item_cell_estimate(
        fixed_counts=evidence.fixed_counts,
        avg_cells=evidence.avg_cells,
        source_notes=[],
        quality_probs=probs,
        fallback=COUNT_ANCHOR_RESIDUAL_ITEM_CELL_ESTIMATE,
    )
    if per_item <= 0:
        return None
    known_q_cells = sum(
        _exact_integer_quality_cell(evidence.quality_cells.get(k)) or 0
        for k in QUALITY_KEYS
    )
    fixed_sum = sum(max(0, int(v)) for v in evidence.fixed_counts.values())
    q5fix_cells, q5fix_items = _high_tier_floor_extra(snapshot, evidence, [])
    residual_items = int(round(max(0.0, depth_grid - known_q_cells - q5fix_cells) / per_item))
    return fixed_sum + residual_items + q5fix_items


def _depth_grids(snap, round_no):
    """Return per-anchor count_hints + engine actuals, or None if branch not hit."""
    prepared = prepare_reference_engine_snapshot(snap)
    res = run_reference_engine(
        prepared, max_combos=hero_max_combos_for_round("aisha", round_no)
    ).as_dict()
    raw_count = res.get("total_count_estimate")
    eng_count = float(raw_count) if isinstance(raw_count, (int, float)) else _mid(raw_count)
    eng_grid = _mid(res.get("total_grid_range") or [None, None, None])

    # rebuild branch inputs (must mirror engine L6873-6917)
    evidence = extract_evidence(prepared)
    items = _minimap_items_from_snapshot(prepared)
    raw_deepest = _deepest_minimap_bottom_row(items)
    known_cells = _known_minimap_cells(items)
    total_avg = evidence.avg_cells.get("total")
    strength = _non_total_strength(evidence)
    snap_round = _snapshot_round_no(prepared)
    map_id = evidence.map_id
    probs, _ = _quality_probabilities(map_id, _STATIC)

    # The count-anchor depth_grid branch fires when total_count unknown, minimap depth
    # present, and non-total evidence strength >= 2. It has TWO conversion sub-branches
    # (total_avg division vs residual per-item); BOTH consume depth_grid, so the anchor
    # lever applies to both. We replicate whichever the engine uses.
    hit = (
        evidence.total_count is None
        and raw_deepest
        and raw_deepest > 0
        and strength >= 2
    )
    if not hit:
        return {"hit": False, "eng_count": eng_count, "eng_grid": eng_grid}

    rect = int(raw_deepest) * COLS
    # CUR: replicate shipped lone-deep discount gate (round>=2, completeness>=0.45)
    cur_rect = rect
    if (
        snap_round is not None
        and int(snap_round) >= 2
        and rect > 0
        and (known_cells or 0) / rect >= 0.45
    ):
        adj = _occupancy_adjusted_depth_grid(items, int(raw_deepest))
        if adj < cur_rect:
            cur_rect = adj
    dg_cur = max(int(known_cells or 0), cur_rect)
    dg_rect = max(int(known_cells or 0), rect)
    dg_b = max(int(known_cells or 0), _occupancy_adjusted_depth_grid(items, int(raw_deepest)))
    dg_c = anchor_c_density(items, int(raw_deepest), known_cells)

    def hint(dg):
        return _hint_from_depth_grid(dg, evidence, prepared, total_avg, probs)

    branch = "avg" if (total_avg and total_avg > 0) else "residual"
    return {
        "hit": True,
        "branch": branch,
        "eng_count": eng_count,
        "eng_grid": eng_grid,
        "raw_deepest": int(raw_deepest),
        "known_cells": int(known_cells or 0),
        "total_avg": float(total_avg) if total_avg else None,
        "dg_cur": dg_cur, "dg_rect": dg_rect, "dg_b": dg_b, "dg_c": dg_c,
        "h_cur": hint(dg_cur), "h_rect": hint(dg_rect),
        "h_b": hint(dg_b), "h_c": hint(dg_c),
    }


def _session_id(name: str) -> str:
    base = Path(name).stem
    return base.split("_")[-1] if "_" in base else base


def collect_archive(limit):
    zips = sorted(glob.glob(ARCHIVE_GLOB))
    rows = []
    seen = set()
    start = time.time()
    total = len(zips)
    for k, zpath in enumerate(zips, 1):
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
        for round_no in (3, 4):
            bn = f"round_snapshots/r0{round_no}_bidding.json"
            if bn not in names:
                continue
            try:
                snap = json.loads(zf.read(bn))
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
            if key in seen:
                continue
            seen.add(key)
            try:
                d = _depth_grids(snap, round_no)
            except Exception as e:  # noqa: BLE001
                continue
            rows.append({
                "src": "archive", "name": Path(zpath).name, "sess": sess,
                "map": int(map_id), "round": round_no,
                "tru_count": int(t_count), "tru_cells": int(t_cells),
                **d,
            })
        if k % 40 == 0 or k == total:
            el = time.time() - start
            print(f"  [archive] {k}/{total} rows={len(rows)} el={el:.0f}s "
                  f"eta~{el/k*(total-k):.0f}s", flush=True)
        if limit and len([r for r in rows if r.get("hit")]) >= limit:
            break
    return rows


def collect_fatbeans(limit):
    samples = sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    rows = []
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
        for skill_idx in (2, 3):
            skill_round, skill_id, _ = AISHA_SKILL_ROUNDS[skill_idx]
            snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
            if snap is None:
                continue
            snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
            try:
                d = _depth_grids(snap, skill_round)
            except Exception:  # noqa: BLE001
                continue
            rows.append({
                "src": "fatbeans", "name": sample.name, "sess": sample.name,
                "map": int(map_id), "round": skill_round,
                "tru_count": t_count, "tru_cells": t_cells,
                **d,
            })
        if k % 25 == 0 or k == total:
            el = time.time() - start
            print(f"  [fatbeans] {k}/{total} rows={len(rows)} el={el:.0f}s "
                  f"eta~{el/k*(total-k):.0f}s", flush=True)
    return rows


def _bias_block(label, rows, key):
    """count_hint(key) - truth, mean/MAE/over-rate, split nothing here (caller splits)."""
    diffs = [r[key] - r["tru_count"] for r in rows if r.get(key) is not None]
    n = len(diffs)
    if n == 0:
        return f"  {label}: n=0"
    mean = sum(diffs) / n
    mae = sum(abs(d) for d in diffs) / n
    over = sum(1 for d in diffs if d > 0) / n
    under = sum(1 for d in diffs if d < 0) / n
    return (f"  {label:5s}: bias={mean:+.2f} MAE={mae:.2f} "
            f"over={over*100:.0f}% under={under*100:.0f}%")


def _report(rows):
    hits = [r for r in rows if r.get("hit")]
    print(f"\n=== TOTAL rows={len(rows)} HIT(count-anchor branch)={len(hits)} "
          f"(archive={sum(1 for r in hits if r['src']=='archive')}, "
          f"fatbeans={sum(1 for r in hits if r['src']=='fatbeans')}) ===")
    if not hits:
        print("NO HIT ROWS -- count-anchor lever does not act on this library subset.")
        return
    nb = sum(1 for r in hits if r.get("branch") == "avg")
    print(f"    conversion branch: total_avg={nb}  residual={len(hits)-nb}")

    for rnd_label, subset in (
        ("ALL", hits),
        ("R3", [r for r in hits if r["round"] == 3]),
        ("R4", [r for r in hits if r["round"] == 4]),
    ):
        print(f"\n--- {rnd_label} (n={len(subset)}) count_hint vs truth item-count ---")
        for label, key in (("eng", None), ("RECT", "h_rect"), ("CUR", "h_cur"),
                           ("(b)", "h_b"), ("(c)", "h_c")):
            if key is None:
                # engine actual produced count (may differ from hint due to downstream blending)
                diffs = [r["eng_count"] - r["tru_count"] for r in subset if r.get("eng_count")]
                if diffs:
                    n = len(diffs)
                    mean = sum(diffs) / n
                    mae = sum(abs(d) for d in diffs) / n
                    over = sum(1 for d in diffs if d > 0) / n
                    print(f"  eng* : bias={mean:+.2f} MAE={mae:.2f} over={over*100:.0f}%  "
                          f"(actual produced count, n={n})")
                continue
            print(_bias_block(label, subset, key))

    # flagged live cases
    print("\n--- FLAGGED live cases (2409 ...261416, 2407 ...041349) ---")
    for r in hits:
        if any(tag in r["sess"] for tag in ("261416", "041349")) or any(
            tag in r["name"] for tag in ("261416", "041349")
        ):
            ta = r.get('total_avg')
            print(f"  {r['name']} R{r['round']} map{r['map']} truth_count={r['tru_count']} "
                  f"raw_deepest={r.get('raw_deepest')} known={r.get('known_cells')} "
                  f"avg={ta if ta is None else round(ta,2)} branch={r.get('branch')}")
            eg = r.get('eng_grid')
            print(f"      dg: cur={r['dg_cur']} rect={r['dg_rect']} b={r['dg_b']} c={r['dg_c']}  "
                  f"| hint: cur={r['h_cur']} rect={r['h_rect']} b={r['h_b']} c={r['h_c']} "
                  f"| eng_count={r['eng_count']:.0f} eng_grid={eg if eg is None else round(eg)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--src", choices=["all", "archive", "fatbeans"], default="all")
    args = ap.parse_args()
    rows = []
    if args.src in ("all", "archive"):
        print("Collecting archive exports...", flush=True)
        rows += collect_archive(args.limit)
    if args.src in ("all", "fatbeans"):
        print("Collecting fatbeans...", flush=True)
        rows += collect_fatbeans(args.limit)
    _report(rows)


if __name__ == "__main__":
    main()
