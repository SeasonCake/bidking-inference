"""Inspect the live BidMap.txt rows to find which column was inserted
in the 2026-05-15 patch. Compare row 0 (first map) with the old 21-col
schema position by position.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.config import get_game_root
from bidking_lab.extract.tables import load_table_rows


OLD_NAMES = [
    "map_id",            # 0
    "name",              # 1
    "description",       # 2
    "name_key",          # 3
    "0_or_1",            # 4
    "icon_key",          # 5
    "bg_key",            # 6
    "category",          # 7
    "sub_pool_weights",  # 8
    "value_tier_ui",     # 9
    "rounds_total",      # 10
    "entry_fee",         # 11
    "12_?",              # 12
    "13_?",              # 13
    "starting_budget",   # 14
    "15_?",              # 15
    "drop_ref",          # 16
    "mode_flag",         # 17
    "bid_price_ladder",  # 18
    "round_category_hints",  # 19
    "20_?",              # 20
]


def main() -> None:
    rows = list(load_table_rows(get_game_root() / "Tables/BidMap.txt"))
    print(f"Total rows: {len(rows)}; columns per row: {len(rows[0])}")
    print()

    # Found IDs (probed by 4-byte Chinese name fragments):
    target_ids = {
        "2409": "末日庇护所 (container open, gold count = 3)",
        "2410": "极客改造屋 (cyber cafe open, gold total cells = 14)",
        "2401": "未知别墅 (mansion open, purple avg cells = 2.54, gold count = 4)",
        "2501": "未知残骸 (shipwreck open, random reveal = 6)",
    }
    found = [row for row in rows if row[0] in target_ids]
    if not found:
        print("Targets not found by ID; falling back to first 3 rows...")
        found = rows[:3]

    for row in found:
        note = target_ids.get(row[0], "")
        print(f"=== id={row[0]} ({note}) ===")
        for i, v in enumerate(row):
            name = OLD_NAMES[i] if i < len(OLD_NAMES) else f"NEW_{i}"
            display_v = v if len(v) < 100 else v[:97] + "..."
            print(f"  [{i:2d}] {name:25s} = {display_v}")
        print()


if __name__ == "__main__":
    main()
