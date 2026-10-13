"""Tests for the first-pass Monte Carlo simulator."""

from __future__ import annotations

import numpy as np
import pytest

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import prepare_session_sampler
from bidking_lab.live.monitor import _effective_item_weights_for_map
from bidking_lab.simulation.basic_mc import (
    DropPoolResolutionError,
    flatten_pool,
    simulate_map,
)


def _make_item(
    item_id: int,
    value: int,
    quality: int = 3,
    *,
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


def _make_map(
    map_id: int,
    drop_pool_id: int,
    min_items: int,
    max_items: int,
    sub_weights: list[tuple[int, int]] | None = None,
    *,
    budget: int = 100_000,
    entry_fee: int = 0,
) -> BidMap:
    return BidMap(
        map_id=map_id,
        name=f"map_{map_id}",
        description="",
        category=101,
        auction_mode="open",
        sub_pool_weights=sub_weights or [],
        rounds_total=10,
        entry_fee_silver=entry_fee,
        starting_budget_silver=budget,
        drop_pool_id=drop_pool_id,
        items_per_session_min=min_items,
        items_per_session_max=max_items,
        value_tier_ui="ui_value_low",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=["0"] * 21,
    )


def test_flatten_leaf_pool_basic() -> None:
    items = {1: _make_item(1, value=100), 2: _make_item(2, value=200)}
    pool = _make_pool(900, [(101, 1, 1, 1, 1), (101, 2, 1, 1, 3)])
    fp = flatten_pool(900, {900: pool}, items)
    assert fp.item_ids == [1, 2]
    assert fp.probabilities[0] == pytest.approx(0.25)
    assert fp.probabilities[1] == pytest.approx(0.75)
    assert fp.values == [100, 200]


def test_flatten_skips_non_physical_loot_items() -> None:
    items = {
        1: _make_item(1, value=100, quality=3),
        2: _make_item(2, value=0, quality=0),
        3: _make_item(3, value=200, quality=3, shape_w=0),
    }
    pool = _make_pool(
        900,
        [
            (101, 1, 1, 1, 1),
            (12, 2, 1, 1, 10),
            (101, 3, 1, 1, 10),
        ],
    )
    fp = flatten_pool(900, {900: pool}, items)
    assert fp.item_ids == [1]
    assert fp.probabilities == [pytest.approx(1.0)]
    assert fp.values == [100]


def test_flatten_resolves_pool_references() -> None:
    """Two-level: outer pool has cat=9999 → inner pool with cat=101 leaves."""
    items = {10: _make_item(10, value=500), 20: _make_item(20, value=1000)}
    inner = _make_pool(50, [(101, 10, 1, 1, 1), (101, 20, 1, 1, 1)])
    outer = _make_pool(900, [(9999, 50, 1, 1, 1)])
    fp = flatten_pool(900, {50: inner, 900: outer}, items)
    assert set(fp.item_ids) == {10, 20}
    assert fp.probabilities[0] == pytest.approx(0.5)
    assert fp.probabilities[1] == pytest.approx(0.5)


def test_flatten_handles_same_item_via_multiple_paths() -> None:
    items = {7: _make_item(7, value=100)}
    p_left = _make_pool(10, [(101, 7, 1, 2, 1)])
    p_right = _make_pool(20, [(101, 7, 3, 5, 1)])
    outer = _make_pool(900, [(9999, 10, 1, 1, 1), (9999, 20, 1, 1, 3)])
    fp = flatten_pool(900, {10: p_left, 20: p_right, 900: outer}, items)
    assert fp.item_ids == [7]
    assert fp.probabilities == [pytest.approx(1.0)]
    # union of count ranges
    assert fp.n_min == [1]
    assert fp.n_max == [5]


@pytest.mark.parametrize(
    ("pool_id", "drops", "reason"),
    [
        (404, {}, "missing_pool"),
        (900, {900: _make_pool(900, [])}, "empty_pool"),
    ],
)
def test_flatten_rejects_missing_or_empty_top_pool(
    pool_id: int,
    drops: dict[int, DropPool],
    reason: str,
) -> None:
    with pytest.raises(DropPoolResolutionError, match=reason):
        flatten_pool(pool_id, drops, {1: _make_item(1, value=100)})


@pytest.mark.parametrize(
    ("nested", "reason"),
    [
        ({}, "missing_pool"),
        ({51: _make_pool(51, [])}, "empty_pool"),
    ],
)
def test_flatten_rejects_incomplete_nested_pool_without_partial_renormalization(
    nested: dict[int, DropPool],
    reason: str,
) -> None:
    items = {1: _make_item(1, value=100)}
    valid = _make_pool(50, [(101, 1, 1, 1, 1)])
    outer = _make_pool(
        900,
        [(9999, 50, 1, 1, 1), (9999, 51, 1, 1, 3)],
    )

    with pytest.raises(DropPoolResolutionError, match=rf"{reason}.*pool_id=51"):
        flatten_pool(900, {50: valid, 900: outer, **nested}, items)


def test_flatten_rejects_unknown_leaf_without_partial_renormalization() -> None:
    items = {1: _make_item(1, value=100)}
    pool = _make_pool(
        900,
        [(101, 1, 1, 1, 1), (101, 1_106_013, 1, 1, 3)],
    )

    with pytest.raises(
        DropPoolResolutionError,
        match=r"unknown_item.*item_id=1106013",
    ):
        flatten_pool(900, {900: pool}, items)


def test_direct_simulation_and_ground_truth_reject_incomplete_pool() -> None:
    items = {1: _make_item(1, value=100)}
    pool = _make_pool(900, [(101, 1_106_013, 1, 1, 1)])
    bid_map = _make_map(900, 900, min_items=1, max_items=1)

    with pytest.raises(DropPoolResolutionError, match="unknown_item"):
        simulate_map(
            900,
            maps={900: bid_map},
            drops={900: pool},
            items=items,
            n_trials=1,
        )
    with pytest.raises(DropPoolResolutionError, match="unknown_item"):
        prepare_session_sampler(
            900,
            maps={900: bid_map},
            drops={900: pool},
            items=items,
        )


def test_monitor_suppresses_incomplete_pool_without_leaking_partial_weights() -> None:
    items = {1: _make_item(1, value=100)}
    pool = _make_pool(
        900,
        [(101, 1, 1, 1, 1), (101, 1_106_013, 1, 1, 3)],
    )
    bid_map = _make_map(900, 900, min_items=1, max_items=1)

    assert _effective_item_weights_for_map(
        900,
        maps={900: bid_map},
        drops={900: pool},
        items=items,
    ) == {}


def test_simulate_leaf_map_expected_value() -> None:
    items = {1: _make_item(1, value=100), 2: _make_item(2, value=200)}
    pool = _make_pool(900, [(101, 1, 1, 1, 1), (101, 2, 1, 1, 1)])
    bm = _make_map(900, 900, min_items=2, max_items=2)
    res = simulate_map(
        900,
        maps={900: bm},
        drops={900: pool},
        items=items,
        n_trials=5000,
        rng=np.random.default_rng(seed=42),
    )
    # E[item value] = (100 + 200) / 2 = 150; k=2 with replacement → E[total] = 300.
    assert abs(res.mean - 300) < 20
    assert res.pool_size_after_flatten == 2


def test_simulate_handles_anthology_routing() -> None:
    items = {1: _make_item(1, value=10), 2: _make_item(2, value=10_000)}
    pool_cheap = _make_pool(100, [(101, 1, 1, 1, 1)])
    pool_rich = _make_pool(200, [(101, 2, 1, 1, 1)])
    leaf_cheap = _make_map(100, 100, 1, 1)
    leaf_rich = _make_map(200, 200, 1, 1)
    anthology = _make_map(999, 0, 1, 1, sub_weights=[(100, 1), (200, 1)])
    res = simulate_map(
        999,
        maps={100: leaf_cheap, 200: leaf_rich, 999: anthology},
        drops={100: pool_cheap, 200: pool_rich},
        items=items,
        n_trials=4000,
        rng=np.random.default_rng(seed=0),
    )
    assert 3000 < res.mean < 7000
    assert res.min == 10
    assert res.max == 10_000


def test_simulate_empty_pool_raises_resolution_error() -> None:
    items = {1: _make_item(1, value=100)}
    pool = _make_pool(7, [])
    bm = _make_map(7, 7, 1, 1)
    with pytest.raises(DropPoolResolutionError, match="empty_pool"):
        simulate_map(
            7,
            maps={7: bm},
            drops={7: pool},
            items=items,
            n_trials=100,
            rng=np.random.default_rng(seed=1),
        )


def test_simulate_unknown_map_raises() -> None:
    with pytest.raises(KeyError):
        simulate_map(404, maps={}, drops={}, items={}, n_trials=1)
