"""1-based minimap audit matrix (footprint + baoguang local_index hints).

Game/UI convention: row and col are **1-based** (col 1 = leftmost, col 10 = rightmost).
Do not render with 0-based column indices — col 10 items will be clipped.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from layout_depth_policy import (  # noqa: E402
    GRID_COLUMNS,
    WAREHOUSE_ROWS,
    _item_top_row,
    _known_minimap_cells,
)

# v=绿 (lv) — avoid lowercase g which reads like gold G in dense matrices.
QUALITY_SYMBOLS = {1: "w", 2: "v", 3: "b", 4: "p", 5: "G", 6: "R"}
MATRIX_LEGEND = "w白 v绿 b蓝 p紫 G金 R红 .空 · * = 宝光/无 footprint"


def decode_item_col(item: dict[str, Any], *, columns: int = GRID_COLUMNS) -> int | None:
    col = item.get("col")
    if col is not None:
        return int(col)
    local_index = item.get("local_index")
    if local_index is not None and columns > 0:
        return int(local_index) % int(columns) + 1
    return None


def item_matrix_symbol(item: dict[str, Any]) -> tuple[str, str]:
    quality = QUALITY_SYMBOLS.get(int(item.get("quality") or 0), "?")
    row = _item_top_row(item)
    if row is None:
        return f"{quality}?", "no_position"
    has_footprint = bool(
        item.get("shape_key")
        or item.get("width")
        or item.get("height")
        or str(item.get("render_mode") or "") == "footprint"
    )
    if item.get("row") is None and item.get("col") is None:
        return f"{quality}*", "baoguang_hint"
    if not has_footprint:
        return f"{quality}*", "quality_only"
    return quality, "footprint"


def render_minimap_matrix(
    items: list[dict[str, Any]],
    *,
    columns: int = GRID_COLUMNS,
    max_rows: int = WAREHOUSE_ROWS,
) -> tuple[str, list[str], int]:
    grid: dict[tuple[int, int], str] = {}
    notes: list[str] = []
    for item in items:
        symbol, kind = item_matrix_symbol(item)
        row = _item_top_row(item, columns=columns)
        col = decode_item_col(item, columns=columns)
        width = max(1, int(item.get("width") or 1))
        height = max(1, int(item.get("height") or 1))
        if row is None or col is None:
            notes.append(
                f"  {symbol} [{kind}] local_index={item.get('local_index')} "
                f"cells={item.get('cells')}"
            )
            continue
        notes.append(
            f"  {symbol} [{kind}] row={row} col={col} {width}x{height} "
            f"local_index={item.get('local_index')} cells={item.get('cells')}"
        )
        for dr in range(height):
            for dc in range(width):
                rr, cc = row + dr, col + dc
                if 1 <= rr <= max_rows and 1 <= cc <= columns:
                    grid[(rr, cc)] = symbol
    rows = sorted({key[0] for key in grid})
    lines = ["     " + " ".join(f"{col:2d}" for col in range(1, columns + 1))]
    for row in rows:
        cells = " ".join(f"{grid.get((row, col), '.'):>2}" for col in range(1, columns + 1))
        lines.append(f"{row:2d}: {cells}")
    return "\n".join(lines), notes, _known_minimap_cells(items)
