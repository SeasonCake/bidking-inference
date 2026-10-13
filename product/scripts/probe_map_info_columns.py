"""Find the unparsed BidMap columns that carry the 4 map-info hint types
spotted on 2026-05-15 screenshots:

* gold count        — 末日庇护所 (R1, 3 件), 未知别墅 (R1, 4 件)
* gold total cells  — 极客改造屋 (R1, 14 格)
* purple avg cells  — 未知别墅 (R3, 约 2.54 格)
* random reveal n   — 未知残骸 (R2, 6 件)

We load Tables/BidMap.txt directly (un-parsed raw_row preserved) and
print each of the 4 known maps' full column dump, then highlight which
columns hold the expected numbers.
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
from bidking_lab.extract.bid_map_table import load_bid_map_table


def main() -> None:
    bid_maps = load_bid_map_table(get_game_root() / "Tables/BidMap.txt")

    # Find by name fragment
    targets = {
        "末日庇护所": ("gold_count", 3),
        "极客改造屋": ("gold_total_cells", 14),
        "未知别墅": ("purple_avg_cells_R3 + gold_count_R1", 2.54),
        "未知残骸": ("random_reveal_R2", 6),
    }

    matches = []
    for bm in bid_maps.values():
        if bm.name in targets:
            matches.append(bm)

    if not matches:
        print("No matching maps found.")
        return

    for bm in matches:
        kind, expected = targets[bm.name]
        print(f"=== {bm.name} (id={bm.map_id}) — expect '{kind}' contains {expected} ===")
        print(f"  drop_pool_id: {bm.drop_pool_id}")
        print(f"  rounds_total: {bm.rounds_total}")
        print(f"  items_per_session: {bm.items_per_session_min}-{bm.items_per_session_max}")
        print(f"  raw columns (21):")
        for i, v in enumerate(bm.raw_row):
            note = ""
            # Heuristic: highlight if the expected number appears in this column
            if isinstance(expected, (int, float)):
                if str(int(expected)) in v or (isinstance(expected, float) and str(expected) in v):
                    note = "  <-- contains expected!"
                # 254 might appear for 2.54
                if isinstance(expected, float) and str(int(expected * 100)) in v:
                    note = "  <-- contains expected*100 (scaled int)"
            print(f"    [{i:2d}] {v!r}{note}")
        print()


if __name__ == "__main__":
    main()
