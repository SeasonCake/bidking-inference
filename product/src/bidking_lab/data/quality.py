"""Quality (rarity) enum shared across item / battle-item / drop schemas.

Confirmed by cross-referencing Item.txt col[8] with in-game observations:

- 0 → no quality (system / UI items like trial cards)
- 1 → 白 white  (junk)
- 2 → 绿 green  (common)
- 3 → 蓝 blue   (uncommon)
- 4 → 紫 purple (rare)
- 5 → 金 gold   (epic) — game also calls this "橙" colloquially
- 6 → 红 red    (legendary)

Median item value scales ~5-10x per tier; see docs/item_table_schema.md.
"""

from __future__ import annotations

from enum import IntEnum


class Quality(IntEnum):
    NONE = 0
    WHITE = 1
    GREEN = 2
    BLUE = 3
    PURPLE = 4
    GOLD = 5
    RED = 6


COLOR_NAME_BY_QUALITY: dict[int, str] = {
    Quality.NONE: "none",
    Quality.WHITE: "white",
    Quality.GREEN: "green",
    Quality.BLUE: "blue",
    Quality.PURPLE: "purple",
    Quality.GOLD: "gold",
    Quality.RED: "red",
}

CHINESE_NAME_BY_QUALITY: dict[int, str] = {
    Quality.NONE: "无",
    Quality.WHITE: "白",
    Quality.GREEN: "绿",
    Quality.BLUE: "蓝",
    Quality.PURPLE: "紫",
    Quality.GOLD: "金",
    Quality.RED: "红",
}


def color_name(q: int) -> str:
    """English color label; returns ``"unknown"`` for out-of-range values."""
    return COLOR_NAME_BY_QUALITY.get(int(q), "unknown")


def chinese_name(q: int) -> str:
    """Chinese color label; returns ``"未知"`` for out-of-range values."""
    return CHINESE_NAME_BY_QUALITY.get(int(q), "未知")


__all__ = (
    "CHINESE_NAME_BY_QUALITY",
    "COLOR_NAME_BY_QUALITY",
    "Quality",
    "chinese_name",
    "color_name",
)
