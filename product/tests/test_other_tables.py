"""Tests for BattleItem, Hero, and BidMap parsers."""

from __future__ import annotations

import pytest

from bidking_lab.extract.battle_item_table import (
    BATTLE_ITEM_TABLE_COLUMN_COUNT,
    parse_battle_item_row,
    parse_battle_item_table,
)
from bidking_lab.extract.bid_map_table import (
    BID_MAP_TABLE_COLUMN_COUNT,
    parse_bid_map_row,
)
from bidking_lab.extract.hero_table import (
    HERO_TABLE_COLUMN_COUNT,
    parse_hero_row,
)


def test_battle_item_basic() -> None:
    row = ["100104", "普品扫描", "显示所有绿色品质藏品的总格数", "[11]", "1", "3"]
    assert len(row) == BATTLE_ITEM_TABLE_COLUMN_COUNT
    bi = parse_battle_item_row(row)
    assert bi.battle_item_id == 100104
    assert bi.name == "普品扫描"
    assert bi.quality == 1
    assert bi.quality_color == "white"
    assert bi.effect_type == 3
    assert bi.effect_type_label == "show_total_cells"


def test_battle_item_unknown_effect_type_label() -> None:
    row = ["100200", "x", "y", "[11]", "2", "99"]
    bi = parse_battle_item_row(row)
    assert bi.effect_type == 99
    assert bi.effect_type_label == "unknown_99"


def test_battle_item_dedups() -> None:
    r = ["100104", "x", "y", "[11]", "1", "3"]
    with pytest.raises(ValueError, match="duplicate battle_item_id"):
        parse_battle_item_table([r, r])


def test_hero_basic() -> None:
    row = ["0"] * HERO_TABLE_COLUMN_COUNT
    row[0] = "204"
    row[1] = "艾哈迈德"
    row[2] = "技能说明"
    h = parse_hero_row(row)
    assert h.hero_id == 204
    assert h.name == "艾哈迈德"
    assert h.skill_description == "技能说明"
    assert len(h.raw_row) == HERO_TABLE_COLUMN_COUNT


def test_hero_wrong_column_count() -> None:
    with pytest.raises(ValueError, match="must have 21 columns"):
        parse_hero_row(["204", "艾哈迈德"])


def test_bid_map_basic() -> None:
    row = ["0"] * BID_MAP_TABLE_COLUMN_COUNT
    row[0] = "2101"
    row[1] = "未知快递"
    row[2] = "包裹形状规整不一"
    row[7] = "101"
    row[8] = "[[]]"
    row[9] = "ui_value_low"
    row[10] = "10"
    row[11] = "[1,1,0]"
    row[14] = "[[]]"
    row[16] = "[9999,2101,16,32]"
    row[17] = "4"
    row[18] = "[2000,1600,1300,1100,0]"
    m = parse_bid_map_row(row)
    assert m.map_id == 2101
    assert m.name == "未知快递"
    assert m.description == "包裹形状规整不一"
    assert m.drop_pool_id == 2101
    assert m.items_per_session_min == 16
    assert m.items_per_session_max == 32
