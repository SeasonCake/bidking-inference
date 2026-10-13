"""Check whether the live game's tables have grown new columns since
our pydantic schemas were authored. This indicates a game patch and
the new columns may carry the map-info hints we observed in screenshots.
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
from bidking_lab.extract.tables import load_table_rows, assert_uniform_columns

TABLES = (
    "BidMap.txt",
    "Hero.txt",
    "Item.txt",
    "Drop.txt",
    "BattleItem.txt",
    "Cabinet.txt",
)

EXPECTED_COLUMNS = {
    "BidMap.txt": 21,
    "Hero.txt": 21,
    "Item.txt": 38,
    "Drop.txt": 5,
    "BattleItem.txt": 6,
    "Cabinet.txt": 14,
}


def main() -> None:
    root = get_game_root() / "Tables"
    for name in TABLES:
        path = root / name
        if not path.exists():
            print(f"{name}: missing")
            continue
        rows = list(load_table_rows(path))
        col_counts = {len(r) for r in rows}
        live = next(iter(col_counts)) if len(col_counts) == 1 else f"mixed {sorted(col_counts)}"
        expected = EXPECTED_COLUMNS.get(name, "?")
        drift = "" if live == expected else f"  <-- DRIFT (was {expected})"
        print(f"{name}: rows={len(rows)}, cols={live}{drift}")


if __name__ == "__main__":
    main()
