"""Tests for current-state tool information ROI."""

from __future__ import annotations

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.observation import QualityBucketObs, SessionObs
from bidking_lab.inference.tool_info_roi import estimate_tool_info_roi


def _item(item_id: int, *, quality: int, value: int, shape: tuple[int, int]) -> Item:
    w, h = shape
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=quality,
        quality_color="",
        value=value,
        shape_w=w,
        shape_h=h,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _pool(pool_id: int, item_id: int) -> DropPool:
    return DropPool(
        pool_id=pool_id,
        name=f"pool_{pool_id}",
        description="",
        pool_type=2,
        entries=[DropEntry(category=100, item_id=item_id, n_min=1, n_max=1, weight=1)],
    )


def _map(map_id: int, pool_id: int) -> BidMap:
    return BidMap(
        map_id=map_id,
        name=f"map_{map_id}",
        description="",
        category=101,
        auction_mode="open",
        sub_pool_weights=[],
        rounds_total=3,
        entry_fee_silver=0,
        starting_budget_silver=100_000,
        drop_pool_id=pool_id,
        items_per_session_min=1,
        items_per_session_max=1,
        value_tier_ui="",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=[],
    )


def test_total_warehouse_tool_collapses_cell_width() -> None:
    items = {
        1: _item(1, quality=4, value=20_000, shape=(2, 2)),
        2: _item(2, quality=4, value=90_000, shape=(3, 3)),
    }
    maps = {2401: _map(2401, 9001), 2402: _map(2402, 9002)}
    drops = {9001: _pool(9001, 1), 9002: _pool(9002, 2)}
    obs = SessionObs(map_id=2401, hero="aisha")

    rows = estimate_tool_info_roi(
        [2401, 2402],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        tools=("总仓储空间",),
        n_trials=10,
        seed=1,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name == "总仓储空间"
    assert row.base_cells_width > 0
    assert row.expected_cells_width == 0
    assert row.cells_width_gain == row.base_cells_width


def test_random_inspection_tool_has_expected_cost_and_finite_roi() -> None:
    items = {
        1: _item(1, quality=3, value=2_000, shape=(1, 1)),
        2: _item(2, quality=5, value=90_000, shape=(3, 3)),
    }
    maps = {2401: _map(2401, 9001), 2402: _map(2402, 9002)}
    drops = {9001: _pool(9001, 1), 9002: _pool(9002, 2)}
    obs = SessionObs(map_id=2401, hero="ethan")

    rows = estimate_tool_info_roi(
        [2401, 2402],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        tools=("随机抽检（2）", "宝光四鉴"),
        n_trials=10,
        seed=2,
    )

    by_name = {row.tool_name: row for row in rows}
    assert by_name["随机抽检（2）"].silver_cost == 2_500
    assert by_name["随机抽检（2）"].value_width_gain >= 0
    assert by_name["宝光四鉴"].silver_cost == 2_500


def test_mirror_eye_tool_is_supported_as_full_quality_signal() -> None:
    items = {
        1: _item(1, quality=3, value=2_000, shape=(1, 1)),
        2: _item(2, quality=5, value=90_000, shape=(3, 3)),
    }
    maps = {2401: _map(2401, 9001), 2402: _map(2402, 9002)}
    drops = {9001: _pool(9001, 1), 9002: _pool(9002, 2)}
    obs = SessionObs(map_id=2401, hero="ethan")

    rows = estimate_tool_info_roi(
        [2401, 2402],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        tools=("明镜之眼",),
        n_trials=10,
        seed=2,
    )

    assert len(rows) == 1
    assert rows[0].tool_name == "明镜之眼"
    assert rows[0].silver_cost == 50_000
    assert "空间觉知" in rows[0].note


def test_no_matching_truths_returns_empty() -> None:
    items = {1: _item(1, quality=4, value=20_000, shape=(2, 2))}
    maps = {2401: _map(2401, 9001)}
    drops = {9001: _pool(9001, 1)}
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        buckets={4: QualityBucketObs(quality=4, total_cells=999)},
    )

    rows = estimate_tool_info_roi(
        [2401],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        tools=("总仓储空间",),
        n_trials=10,
        seed=1,
    )

    assert rows == []
