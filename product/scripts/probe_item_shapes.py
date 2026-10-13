"""Probe Item.txt col[7] for shape data (width × height encoding)."""
from __future__ import annotations

import io
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from bidking_lab.extract.tables import load_table_rows

rows = load_table_rows(Path(__file__).resolve().parent.parent / "data" / "raw" / "tables" / "Item.txt")

shape_items: dict[int, list[str]] = defaultdict(list)
for r in rows:
    wh = int(r[7])
    shape_items[wh].append(r[1][:20])

print("Shape WH -> width x height (cabinet grid is 6x7):")
print(f"{'WH':>4s}  {'WxH':>5s}  {'area':>4s}  {'count':>5s}  sample_names")
print("-" * 75)
for wh in sorted(shape_items):
    w = wh // 10
    h = wh % 10
    area = w * h
    names = shape_items[wh][:3]
    print(f"{wh:>4d}  {w}x{h}    {area:>4d}  {len(shape_items[wh]):>5d}  {names}")

print("\nItems with largest shapes:")
for r in rows:
    wh = int(r[7])
    if wh >= 50:
        print(f"  id={r[0]:>5s}  shape={wh}  quality={r[8]}  value={r[9]:>8s}  name={r[1]}")

print("\nShape=0 items by type (col[25]):")
zero_types: Counter[str] = Counter()
for r in rows:
    if r[7] == "0":
        zero_types[r[25]] += 1
for t, cnt in zero_types.most_common():
    print(f"  {t}: {cnt}")
