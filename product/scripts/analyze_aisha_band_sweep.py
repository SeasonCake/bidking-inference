"""Analyze .tmp/aisha_band_sweep.jsonl -> per-round combo/band/budget tables. Read-only."""
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".tmp/aisha_band_sweep.jsonl")
rows = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
# Exclude 26xx secret maps per user spec.
rows = [r for r in rows if not r.get("is26")]
print(f"rows (non-26xx) = {len(rows)}  files = {len({r['file'] for r in rows})}")

def pct(n, d):
    return f"{100*n/d:.1f}%" if d else "n/a"

# Per-round aggregation
by_round = defaultdict(list)
for r in rows:
    by_round[r["round"]].append(r)

print("\n=== PER-ROUND TABLE ===")
hdr = f"{'rnd':>3} {'N':>5} {'combo<=5':>9} {'combo<=5%':>9} {'budget_hit':>10} {'bud_hit%':>8} {'collapsed':>9} {'collapse%':>9} {'med_combo':>9} {'med_band':>9}"
print(hdr)
for r in sorted(by_round):
    rr = by_round[r]
    N = len(rr)
    c5 = sum(1 for x in rr if isinstance(x.get("combo"), int) and x["combo"] <= 5)
    bh = sum(1 for x in rr if x.get("budget_touched"))
    col = sum(1 for x in rr if x.get("collapsed") is True)
    coln = sum(1 for x in rr if x.get("collapsed") is not None)
    combos = sorted(x["combo"] for x in rr if isinstance(x.get("combo"), int))
    bands = sorted(x["band_w"] for x in rr if isinstance(x.get("band_w"), (int, float)))
    medc = combos[len(combos)//2] if combos else None
    medb = bands[len(bands)//2] if bands else None
    print(f"{r:>3} {N:>5} {c5:>9} {pct(c5,N):>9} {bh:>10} {pct(bh,N):>8} {col:>9} {pct(col,coln):>9} {str(medc):>9} {(f'{medb:.3f}' if medb is not None else 'n/a'):>9}")

# Collapse + missing-hi-cells co-occurrence
print("\n=== COLLAPSE vs MISSING-HI-CELLS ===")
col_rows = [r for r in rows if r.get("collapsed") is True]
print(f"collapsed rows total: {len(col_rows)}")
mh = sum(1 for r in col_rows if r.get("missing_hi_cells"))
print(f"  of which missing_hi_cells (q5/q6 item present, cells unknown): {mh} ({pct(mh,len(col_rows))})")
# combo<=5 vs missing
c5_rows = [r for r in rows if isinstance(r.get("combo"), int) and r["combo"] <= 5]
print(f"\ncombo<=5 rows total: {len(c5_rows)}")
mh5 = sum(1 for r in c5_rows if r.get("missing_hi_cells"))
print(f"  of which missing_hi_cells: {mh5} ({pct(mh5,len(c5_rows))})")
# by round for combo<=5
c5_by_round = defaultdict(int)
for r in c5_rows:
    c5_by_round[r["round"]] += 1
print("  combo<=5 by round:", dict(sorted(c5_by_round.items())))

# Early (1-3) vs decision (4+) summary
print("\n=== EARLY (R1-3) vs LATE (R4+) ===")
for label, rounds in [("early R1-3", {1,2,3}), ("late R4+", None)]:
    sub = [r for r in rows if (r["round"] in rounds if rounds else r["round"] >= 4)]
    if not sub:
        continue
    N = len(sub)
    c5 = sum(1 for x in sub if isinstance(x.get("combo"), int) and x["combo"] <= 5)
    bh = sum(1 for x in sub if x.get("budget_touched"))
    col = sum(1 for x in sub if x.get("collapsed") is True)
    coln = sum(1 for x in sub if x.get("collapsed") is not None)
    print(f"{label:>10}: N={N:>4} combo<=5={pct(c5,N)} budget_hit={pct(bh,N)} collapsed={pct(col,coln)}")

# Budget-touched detail (the user's new question): where does budget actually bind?
print("\n=== BUDGET-TOUCHED detail (combo == max_combos) ===")
bt = [r for r in rows if r.get("budget_touched")]
print(f"total budget-touched rows: {len(bt)}")
bt_round = defaultdict(int)
for r in bt:
    bt_round[r["round"]] += 1
print("  by round:", dict(sorted(bt_round.items())))
# Show some examples
for r in bt[:8]:
    print(f"    {r['file'][:40]:40} R{r['round']} combo={r['combo']} budget={r['max_combos']} band={r['band_w']}")
