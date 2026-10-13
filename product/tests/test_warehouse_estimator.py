"""Tests for warehouse-cell estimation from partial observations."""

from __future__ import annotations

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.observation import QualityBucketObs, SessionObs
from bidking_lab.inference.warehouse_estimator import estimate_warehouse_cells


def _item(
    item_id: int,
    *,
    quality: int,
    value: int,
    shape: tuple[int, int],
) -> Item:
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


def _map(map_id: int, pool_id: int, k: int = 1) -> BidMap:
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
        items_per_session_min=k,
        items_per_session_max=k,
        value_tier_ui="",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=[],
    )


def test_estimate_warehouse_ignores_existing_warehouse_reading() -> None:
    items = {1: _item(1, quality=4, value=20_000, shape=(2, 2))}
    maps = {2401: _map(2401, 9001)}
    drops = {9001: _pool(9001, 1)}
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        warehouse_total_cells=999,
        buckets={4: QualityBucketObs(quality=4, total_cells=4)},
    )

    estimate = estimate_warehouse_cells(
        [2401],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=20,
        seed=1,
    )

    assert estimate.n_matched == 20
    assert estimate.total_cells is not None
    assert estimate.total_cells.p50 == 4


def test_estimate_warehouse_ranks_candidate_maps_by_evidence() -> None:
    items = {
        1: _item(1, quality=4, value=20_000, shape=(2, 2)),
        2: _item(2, quality=4, value=90_000, shape=(3, 3)),
    }
    maps = {2401: _map(2401, 9001), 2402: _map(2402, 9002)}
    drops = {9001: _pool(9001, 1), 9002: _pool(9002, 2)}
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        buckets={4: QualityBucketObs(quality=4, total_cells=4)},
    )

    estimate = estimate_warehouse_cells(
        [2401, 2402],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=20,
        seed=1,
    )

    assert estimate.n_total == 40
    assert estimate.n_matched == 20
    assert estimate.total_cells is not None
    assert estimate.total_cells.p50 == 4
    assert [row.map_id for row in estimate.map_contributions] == [2401, 2402]
    assert estimate.map_contributions[0].posterior_probability == 1
    assert estimate.map_contributions[1].posterior_probability == 0


def test_estimate_warehouse_reports_no_match() -> None:
    items = {1: _item(1, quality=4, value=20_000, shape=(2, 2))}
    maps = {2401: _map(2401, 9001)}
    drops = {9001: _pool(9001, 1)}
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        buckets={4: QualityBucketObs(quality=4, total_cells=99)},
    )

    estimate = estimate_warehouse_cells(
        [2401],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=20,
        seed=1,
    )

    assert estimate.n_matched == 0
    assert estimate.total_cells is None
    assert estimate.confidence == "无匹配"
