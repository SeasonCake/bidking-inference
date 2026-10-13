"""Tests for processed-data build helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item


def _load_build_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_processed_data.py"
    spec = importlib.util.spec_from_file_location("build_processed_data", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_item(
    item_id: int,
    *,
    value: int = 100,
    quality: int = 3,
    shape_w: int = 1,
    shape_h: int = 1,
) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"k_{item_id}",
        desc_key=f"d_{item_id}",
        quality=quality,
        quality_color="blue",
        value=value,
        shape_w=shape_w,
        shape_h=shape_h,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=["0"] * 38,
    )


def _make_pool(pool_id: int, entries: list[tuple[int, int, int, int, int]]) -> DropPool:
    return DropPool(
        pool_id=pool_id,
        name="test",
        description="",
        pool_type=2,
        entries=[
            DropEntry(category=c, item_id=i, n_min=mn, n_max=mx, weight=w)
            for c, i, mn, mx, w in entries
        ],
    )


def _make_map(map_id: int, drop_pool_id: int) -> BidMap:
    return BidMap(
        map_id=map_id,
        name=f"map_{map_id}",
        description="",
        category=101,
        auction_mode="open",
        sub_pool_weights=[],
        rounds_total=10,
        entry_fee_silver=0,
        starting_budget_silver=100_000,
        drop_pool_id=drop_pool_id,
        items_per_session_min=1,
        items_per_session_max=1,
        value_tier_ui="ui_value_low",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=["0"] * 21,
    )


def test_map_reachable_valid_droppable_ids_filters_invalid_and_unreachable() -> None:
    module = _load_build_module()
    items = {
        1: _make_item(1),
        2: _make_item(2),
        3: _make_item(3, value=0, quality=0),
        4: _make_item(4, shape_w=0),
    }
    maps = {
        1001: _make_map(1001, 10),
        1002: _make_map(1002, 999),
    }
    drops = {
        10: _make_pool(10, [(9999, 11, 1, 1, 1), (101, 99, 1, 1, 1)]),
        11: _make_pool(
            11,
            [
                (101, 1, 1, 1, 1),
                (101, 3, 1, 1, 1),
                (101, 4, 1, 1, 1),
            ],
        ),
        20: _make_pool(20, [(101, 2, 1, 1, 1)]),
    }

    droppable_ids, missing_item_ids, missing_pool_ids = (
        module._map_reachable_valid_droppable_ids(
            maps=maps,
            drops=drops,
            items=items,
        )
    )

    assert droppable_ids == {1}
    assert missing_item_ids == {99}
    assert missing_pool_ids == {999}


def test_map_reachable_strict_mode_fails_on_missing_legacy_inputs() -> None:
    module = _load_build_module()
    with pytest.raises(ValueError, match="legacy distribution input is incomplete"):
        module._map_reachable_valid_droppable_ids(
            maps={1001: _make_map(1001, 999)},
            drops={},
            items={1: _make_item(1)},
            strict_missing=True,
        )
