"""One-off sweep: per-round aisha combo_count / band-collapse / budget-touch across the library.

Read-only. Replays every aisha sample at every bidding round with the round-budgeted
max_combos (hero_max_combos_for_round) and records combo_count, band width, whether the
combo budget ceiling was touched, and the "missing high-quality cells -> total inflated via
grid/avg" bug signature. Writes JSONL to .tmp/aisha_band_sweep.jsonl.

Retirement: delete once the early-round band-collapse question is closed (see handoff).
"""
from __future__ import annotations
import sys, json, time, traceback
from pathlib import Path

for p in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, p)

import audit_aisha_gap as a  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from ahmad_ref_engine import run_reference_engine  # noqa: E402

OUT = Path(".tmp/aisha_band_sweep.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

# Optional budget-multiplier experiment: when set, early rounds (<=3) use max_combos*MULT.
import os  # noqa: E402
BUDGET_MULT = float(os.environ.get("SWEEP_BUDGET_MULT", "1.0"))
EARLY_ROUNDS = {1, 2, 3}


def _has_item(rng_dict, key):
    """High value present at this round (range max > 0)."""
    if not isinstance(rng_dict, dict):
        return False
    v = rng_dict.get(key)
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return False
    try:
        return float(v[2]) > 0
    except (TypeError, ValueError):
        return False


def _is26(map_id):
    s = str(map_id or "")
    return s.startswith("26")


def main():
    roots = a.SAMPLE_ROOTS
    files = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(root.glob("fatbeans*aisha*.json")))
    n = len(files)
    print(f"[sweep] {n} aisha files, budget_mult={BUDGET_MULT}", flush=True)
    t_start = time.time()
    written = 0
    with OUT.open("w", encoding="utf-8") as fh:
        for i, path in enumerate(files):
            try:
                events = a.parse_fatbeans_capture(path)
            except Exception:
                continue
            map_id = a._map_id_from_events(events)
            settlement = a._settlement_breakdown(events)
            pre = [b for b in a.live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            # Collapse reading+bidding duplicate batches: use unique sort prefixes.
            n_rounds = len(pre)
            for r in range(1, n_rounds + 1):
                pb = pre[:r]
                sid = a._prefix_sort_id(pb)
                pe = a._events_through_sort(events, sid) if sid else events
                try:
                    snap = a._build_snapshot(hero="aisha", events=pe, prefix_batches=pb, map_id=map_id)
                    base_budget = hero_max_combos_for_round("aisha", r)
                    budget = int(base_budget * BUDGET_MULT) if r in EARLY_ROUNDS else base_budget
                    res = run_reference_engine(snap, max_combos=budget).as_dict()
                except Exception:
                    continue
                combo = res.get("combo_count")
                bal = res.get("balanced")
                aggr = res.get("aggressive")
                cons = res.get("conservative")
                notes = res.get("notes", []) or []
                ev = res.get("evidence", {}) or {}
                qcells_ev = ev.get("quality_cells") or {}
                qc_ranges = res.get("quality_count_ranges") or {}
                red_cnt = res.get("red_count_range")
                # bug signature: high-quality item present but its cells unknown in evidence
                grid_avg_note = next((nn for nn in notes if isinstance(nn, str) and nn.startswith("total_count_from_grid_avg_cells:")), None)
                total_from_grid_avg = None
                if grid_avg_note:
                    try:
                        total_from_grid_avg = int(grid_avg_note.split(":")[1])
                    except Exception:
                        pass
                q5_item = _has_item(qc_ranges, "q5")
                q6_item = _has_item(qc_ranges, "q6") or _has_item({"q6": red_cnt} if isinstance(red_cnt, (list, tuple)) else {}, "q6")
                q5_cells_known = "q5" in qcells_ev
                q6_cells_known = "q6" in qcells_ev
                missing_hi_cells = (q5_item and not q5_cells_known) or (q6_item and not q6_cells_known)

                band_w = None
                collapsed = None
                try:
                    if bal not in (None, "") and aggr not in (None, "") and float(bal) > 0:
                        band_w = (float(aggr) - float(bal)) / float(bal)
                        collapsed = band_w < 0.05
                except Exception:
                    pass

                budget_touched = (combo is not None and budget is not None and combo >= budget)

                rec = {
                    "file": path.name,
                    "map": str(map_id),
                    "is26": _is26(map_id),
                    "round": r,
                    "n_rounds": n_rounds,
                    "max_combos": budget,
                    "base_budget": base_budget,
                    "combo": combo,
                    "budget_touched": bool(budget_touched),
                    "balanced": bal,
                    "aggressive": aggr,
                    "conservative": cons,
                    "band_w": band_w,
                    "collapsed": collapsed,
                    "q5_item": q5_item,
                    "q6_item": q6_item,
                    "q5_cells_known": q5_cells_known,
                    "q6_cells_known": q6_cells_known,
                    "missing_hi_cells": bool(missing_hi_cells),
                    "total_from_grid_avg": total_from_grid_avg,
                    "total_count_estimate": res.get("total_count_estimate"),
                }
                fh.write(json.dumps(rec) + "\n")
                written += 1
            if (i + 1) % 25 == 0:
                el = time.time() - t_start
                eta = el / (i + 1) * (n - i - 1)
                print(f"[sweep] {i+1}/{n} files, {written} rows, {el:.0f}s elapsed, ETA {eta:.0f}s", flush=True)
    print(f"[sweep] DONE {written} rows in {time.time()-t_start:.0f}s -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
