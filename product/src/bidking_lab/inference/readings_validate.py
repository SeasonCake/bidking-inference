"""Validate user readings against warehouse capacity (cells budget)."""

from __future__ import annotations

from typing import Any


def _cell_val(raw: Any) -> int:
    if raw is None:
        return 0
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, v)


def sum_filled_bucket_cells(state: dict[str, Any]) -> int:
    """Sum explicit bucket cell counts the user filled (low + purple + gold + red)."""
    hero = state.get("hero", "ethan")
    total = 0
    if hero == "ethan":
        total += _cell_val(state.get("wg_cells"))
        total += _cell_val(state.get("blue_cells"))
    elif state.get("aisha_split"):
        total += _cell_val(state.get("white_cells"))
        total += _cell_val(state.get("green_cells"))
        total += _cell_val(state.get("blue_cells"))
    else:
        total += _cell_val(state.get("white_cells"))
        total += _cell_val(state.get("blue_cells"))
    total += _cell_val(state.get("purple_cells"))
    total += _cell_val(state.get("gold_cells"))
    total += _cell_val(state.get("red_cells_total"))
    return total


def check_warehouse_cell_budget(state: dict[str, Any]) -> str | None:
    """Return an error message if filled cells exceed warehouse capacity."""
    wh = _cell_val(state.get("warehouse_cells"))
    if wh <= 0:
        return None
    tolerance = (
        _cell_val(state.get("warehouse_cells_tolerance"))
        if state.get("warehouse_cells_mode") == "estimate"
        else 0
    )
    capacity = wh + tolerance
    filled = sum_filled_bucket_cells(state)
    if filled > capacity:
        limit_text = f"{wh}+{tolerance}" if tolerance > 0 else str(wh)
        return (
            f"已填各品级格数合计 **{filled}** 格，超过左侧栏仓库总格数 **{limit_text}** 格。"
            f"请核对 OCR/手填（白绿、蓝、紫、金、红），修正后再推断。"
        )
    return None
