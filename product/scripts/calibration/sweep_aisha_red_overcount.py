"""Quantify aisha red over-count across the library: engine red count/cells vs settled
truth, and whether a revealed red sat on the minimap that the engine ignored. Read-only.
Usage: python .tmp/sweep_aisha_red_overcount.py [limit]"""
from __future__ import annotations
import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bidking_lab.live.fatbeans import parse_fatbeans_capture, live_batches_from_fatbeans_events
from ahmad_ref_engine import run_reference_engine, _minimap_items_from_snapshot, _safe_int
from audit_aisha_gap import _build_snapshot, _hero_from_events, _map_id_from_events, _settlement_breakdown, SAMPLE_ROOTS

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
files = []
for root in SAMPLE_ROOTS:
    if root.is_dir():
        files.extend(sorted(root.glob("fatbeans*aisha*.json")))
if limit:
    files = files[:limit]
print(f"aisha samples={len(files)}", flush=True)

rows = []
t0 = time.time()
for i, path in enumerate(files):
    try:
        events = parse_fatbeans_capture(path)
        hero = _hero_from_events(events)
        if hero != "aisha":
            continue
        pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        if not pre:
            continue
        snap = _build_snapshot(hero="aisha", events=events, prefix_batches=pre, map_id=_map_id_from_events(events))
        res = run_reference_engine(snap, max_combos=50_000)
        sb = _settlement_breakdown(events)
        if not sb:
            continue
        true_n = sb["counts"].get(6, 0)
        true_c = sb["cells"].get(6, 0)
        mm = _minimap_items_from_snapshot(snap)
        reds = [m for m in mm if _safe_int(m.get("quality")) == 6]
        rev_red_n = len(reds)
        eng_n = res.red_count_range[1]   # median
        eng_c = res.red_cells_range[1]
        rows.append((path.name, true_n, true_c, eng_n, eng_c, rev_red_n))
    except Exception:
        pass
    if i and i % 50 == 0:
        print(f"  {i}/{len(files)} ({time.time()-t0:.0f}s)", flush=True)

print(f"\ncollected {len(rows)} aisha samples in {time.time()-t0:.0f}s\n")

# Bucket by true red count
from collections import defaultdict
buck = defaultdict(list)
for name, tn, tc, en, ec, rn in rows:
    key = "0" if tn == 0 else ("1" if tn == 1 else ("2-3" if tn <= 3 else "4+"))
    buck[key].append((tn, tc, en, ec, rn))

print("truth_red | n | eng_count(avg) vs truth | eng_cells(avg) vs truth | %over_count | revealed_red_present%")
for key in ("0", "1", "2-3", "4+"):
    g = buck.get(key, [])
    if not g:
        continue
    n = len(g)
    avg_tn = sum(x[0] for x in g) / n
    avg_en = sum((x[2] or 0) for x in g) / n
    avg_tc = sum(x[1] for x in g) / n
    avg_ec = sum((x[3] or 0) for x in g) / n
    over = sum(1 for x in g if (x[2] or 0) > x[0]) / n * 100
    rev = sum(1 for x in g if x[4] > 0) / n * 100
    print(f"  {key:5} | {n:3} | eng {avg_en:.1f} vs true {avg_tn:.1f} | eng {avg_ec:.1f} vs true {avg_tc:.1f} | {over:.0f}% over | revealed {rev:.0f}%")

# REVEALED-RED SUBSET (the D-target): when a red is on the minimap, eng vs truth vs revealed
print("\n=== REVEALED-RED subset (rev_red_n>=1): does eng over/under vs truth AND vs revealed? ===")
rev = [x for x in rows if x[5] >= 1]
print(f"  n={len(rev)} samples have a revealed red")
rb = defaultdict(list)
for name, tn, tc, en, ec, rn in rev:
    key = "1" if tn == 1 else ("2-3" if tn <= 3 else "4+")
    rb[key].append((tn, en, rn))
print("  truth_red | n | eng_count(avg) | truth(avg) | revealed(avg) | eng-truth | eng>truth%")
for key in ("1", "2-3", "4+"):
    g = rb.get(key, [])
    if not g: continue
    n = len(g)
    aen = sum((x[1] or 0) for x in g)/n; atn = sum(x[0] for x in g)/n; arn = sum(x[2] for x in g)/n
    over = sum(1 for x in g if (x[1] or 0) > x[0])/n*100
    print(f"    {key:4} | {n:3} | eng {aen:.1f} | true {atn:.1f} | rev {arn:.1f} | {aen-atn:+.1f} | {over:.0f}% over")

# Worst over-counters
print("\nworst red over-counters (eng_count - true_count):")
worst = sorted(rows, key=lambda x: -((x[3] or 0) - x[1]))[:12]
for name, tn, tc, en, ec, rn in worst:
    print(f"  {name[:46]:46} true {tn}red/{tc}c  eng {en}red/{ec}c  revealed_reds={rn}")
