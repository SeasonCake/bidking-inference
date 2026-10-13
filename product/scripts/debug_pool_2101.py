"""Look inside drop pool 2101 to see what's actually there."""

from __future__ import annotations

import io
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from bidking_lab.extract.drop_table import load_drop_table
from bidking_lab.extract.item_table import load_item_table


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    tables_in = repo_root / "data" / "raw" / "tables"

    drops = load_drop_table(tables_in / "Drop.txt")
    items = load_item_table(tables_in / "Item.txt")

    for pid in [101101, 101201, 101301, 101401, 101501, 102301, 104307, 1011, 1012]:
        pool = drops.get(pid)
        if pool is None:
            print(f"pool {pid}: MISSING")
            continue
        print(f"== pool {pid}  type={pool.pool_type}  name={pool.name!r} desc={pool.description!r}  entries={len(pool.entries)} ==")
        in_items = 0
        nonzero_value = 0
        for e in pool.entries[:8]:
            in_it = e.item_id in items
            v = items[e.item_id].value if in_it else None
            print(
                f"  cat={e.category}  item={e.item_id}  count=[{e.n_min},{e.n_max}]  "
                f"weight={e.weight}  in_items={in_it}  value={v}"
            )
        for e in pool.entries:
            if e.item_id in items:
                in_items += 1
                if items[e.item_id].value > 0:
                    nonzero_value += 1
        print(f"  -> {in_items}/{len(pool.entries)} entries have an Item.txt row; "
              f"{nonzero_value} of those have value > 0")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
