"""Tests for historical 21-column and current 23-column BidMap parsing."""

from __future__ import annotations

import pytest

from bidking_lab.extract.bid_map_table import (
    BID_MAP_TABLE_COLUMN_COUNT,
    BID_MAP_TABLE_COLUMN_COUNT_V2,
    parse_bid_map_row,
    parse_bid_map_table,
)


def _make_row(**overrides: str) -> list[str]:
    row = ["0"] * BID_MAP_TABLE_COLUMN_COUNT
    row[0] = "2101"
    row[1] = "未知快递"
    row[2] = "desc"
    row[7] = "101"
    row[8] = "[[2101,100],[2102,20]]"
    row[9] = "ui_value_low"
    row[10] = "10"
    row[11] = "[1,1,2000]"
    row[14] = "[[1,1,10000]]"
    row[16] = "[9999,2101,16,32]"
    row[17] = "4"
    row[18] = "[2000,1600,1300,1100,0]"
    row[19] = "[102,103,103,104,105]"
    for k, v in overrides.items():
        idx = int(k[3:])
        row[idx] = v
    return row


def _make_v2_row(**overrides: str) -> list[str]:
    row = ["0"] * BID_MAP_TABLE_COLUMN_COUNT_V2
    row[0] = "2501"
    row[1] = "沉船"
    row[2] = "desc"
    row[7] = "103"
    row[9] = "[[]]"
    row[10] = "ui_value_high"
    row[11] = "30"
    row[12] = "[1,1,2500]"
    row[14] = "[50,50,50]"
    row[15] = "[[1,1,50000]]"
    row[16] = "[[]]"
    row[17] = "[9999,2501,22,44]"
    row[18] = "4"
    row[19] = "[3000,2500,2000,1500,0]"
    row[20] = "[103,0,103,0,0]"
    for k, v in overrides.items():
        idx = int(k[3:])
        row[idx] = v
    return row


def test_parses_anthology_map() -> None:
    bm = parse_bid_map_row(_make_row())
    assert bm.map_id == 2101
    assert bm.name == "未知快递"
    assert bm.auction_mode == "open"
    assert bm.sub_pool_weights == [(2101, 100), (2102, 20)]
    assert bm.drop_pool_id == 2101
    assert bm.items_per_session_min == 16
    assert bm.items_per_session_max == 32
    assert bm.entry_fee_silver == 2000
    assert bm.starting_budget_silver == 10000
    assert bm.value_tier_ui == "ui_value_low"
    assert bm.rounds_total == 10
    assert bm.mode_flag == 4
    assert bm.bid_price_ladder == [2000, 1600, 1300, 1100, 0]
    assert bm.round_category_hints == [102, 103, 103, 104, 105]
    assert bm.round_skill_group_ids == [102, 103, 103, 104, 105]


def test_parses_current_23_column_drop_ref_from_col17() -> None:
    bm = parse_bid_map_row(_make_v2_row())

    assert bm.map_id == 2501
    assert bm.category == 103
    assert bm.rounds_total == 30
    assert bm.entry_fee_silver == 2500
    assert bm.starting_budget_silver == 50000
    assert bm.drop_pool_id == 2501
    assert bm.items_per_session_min == 22
    assert bm.items_per_session_max == 44
    assert bm.value_tier_ui == "ui_value_high"
    assert bm.mode_flag == 4
    assert bm.raw_row[14] == "[50,50,50]"
    assert bm.raw_row[16] == "[[]]"
    assert bm.raw_row[17] == "[9999,2501,22,44]"


def test_v303_shipwreck_blank_category_is_inferred() -> None:
    bm = parse_bid_map_row(_make_v2_row(**{"col7": ""}))

    assert bm.map_id == 2501
    assert bm.category == 105


def test_v303_sealed_shipwreck_blank_category_is_inferred() -> None:
    bm = parse_bid_map_row(
        _make_v2_row(
            **{
                "col0": "4501",
                "col7": "",
                "col17": "[9999,2501,22,44]",
            }
        )
    )

    assert bm.map_id == 4501
    assert bm.category == 305
    assert bm.auction_mode == "sealed"


def test_v308_activity_shipwreck_blank_category_is_inferred() -> None:
    bm = parse_bid_map_row(
        _make_v2_row(
            **{
                "col0": "2521",
                "col7": "",
                "col17": "[9999,2521,22,44]",
            }
        )
    )

    assert bm.map_id == 2521
    assert bm.category == 105


def test_v308_sealed_activity_shipwreck_blank_category_is_inferred() -> None:
    bm = parse_bid_map_row(
        _make_v2_row(
            **{
                "col0": "4521",
                "col7": "",
                "col17": "[9999,2521,22,44]",
            }
        )
    )

    assert bm.map_id == 4521
    assert bm.category == 305
    assert bm.auction_mode == "sealed"


def test_unknown_blank_category_still_rejected() -> None:
    with pytest.raises(ValueError, match="missing category"):
        parse_bid_map_row(_make_v2_row(**{"col0": "2401", "col7": ""}))


def test_round_category_hints_with_zeros() -> None:
    """Mansion-style maps only hint R1 and R3 (zeros for other rounds)."""
    bm = parse_bid_map_row(_make_row(**{"col19": "[103,0,103,0,0]"}))
    assert bm.round_category_hints == [103, 0, 103, 0, 0]
    assert bm.round_skill_group_ids == [103, 0, 103, 0, 0]


def test_round_category_hints_missing() -> None:
    bm = parse_bid_map_row(_make_row(**{"col19": ""}))
    assert bm.round_category_hints == []
    assert bm.round_skill_group_ids == []


def test_parses_leaf_map() -> None:
    bm = parse_bid_map_row(_make_row(**{"col8": "[[]]", "col11": "[1,1,0]", "col14": "[[]]"}))
    assert bm.sub_pool_weights == []
    assert bm.entry_fee_silver == 0
    assert bm.starting_budget_silver == 0


def test_sealed_mode_for_tier4() -> None:
    bm = parse_bid_map_row(_make_row(**{"col0": "4401", "col17": "4"}))
    assert bm.auction_mode == "sealed"


def test_training_mode_for_tier3() -> None:
    bm = parse_bid_map_row(_make_row(**{"col0": "3401", "col17": "1", "col18": "[0,0,0,0,0]"}))
    assert bm.auction_mode == "training"
    assert bm.bid_price_ladder == [0, 0, 0, 0, 0]


def test_rejects_bad_drop_ref() -> None:
    with pytest.raises(Exception):
        parse_bid_map_row(_make_row(**{"col16": "[9999,2101]"}))


def test_dedups() -> None:
    rows = [_make_row(), _make_row()]
    with pytest.raises(ValueError, match="duplicate map_id"):
        parse_bid_map_table(rows)
