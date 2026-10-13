"""Tests for the ground-truth session sampler."""

from __future__ import annotations

import numpy as np
import pytest

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import (
    BucketTruth,
    SessionTruth,
    is_huge_item,
    prepare_session_sampler,
    sample_session_truth,
)
from bidking_lab.simulation.basic_mc import DropPoolResolutionError


def _make_item(
    item_id: int,
    value: int,
    quality: int,
    shape: tuple[int, int] = (1, 1),
) -> Item:
    w, h = shape
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"k_{item_id}",
        desc_key=f"d_{item_id}",
        quality=quality,
        quality_color="x",
        value=value,
        shape_w=w,
        shape_h=h,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=["0"] * 38,
    )


def _make_pool(pool_id: int, entries: list[tuple[int, int, int, int, int]]) -> DropPool:
    return DropPool(
        pool_id=pool_id,
        name=f"pool_{pool_id}",
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
    *,
    min_items: int = 5,
    max_items: int = 5,
    sub_weights: list[tuple[int, int]] | None = None,
) -> BidMap:
    return BidMap(
        map_id=map_id,
        name=f"map_{map_id}",
        description="",
        category=101,
        auction_mode="open",
        sub_pool_weights=sub_weights or [],
        rounds_total=10,
        entry_fee_silver=0,
        starting_budget_silver=100_000,
        drop_pool_id=drop_pool_id,
        items_per_session_min=min_items,
        items_per_session_max=max_items,
        value_tier_ui="ui_value_low",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=["0"] * 21,
    )


# --- is_huge_item ---

def test_is_huge_item_purple_4x4_yes() -> None:
    item = _make_item(1, value=40_000, quality=4, shape=(4, 4))   # area 16
    assert is_huge_item(item) is True


def test_is_huge_item_purple_3x3_no() -> None:
    item = _make_item(1, value=20_000, quality=4, shape=(3, 3))   # area 9
    assert is_huge_item(item) is False


def test_is_huge_item_gold_6x3_yes() -> None:
    item = _make_item(1, value=108_000, quality=5, shape=(6, 3))  # area 18
    assert is_huge_item(item) is True


def test_is_huge_item_gold_4x4_yes() -> None:
    """Gold items >= 12 cells are huge (threshold=12). 4x4=16 qualifies."""
    item = _make_item(1, value=50_000, quality=5, shape=(4, 4))
    assert is_huge_item(item) is True


def test_is_huge_item_white_never() -> None:
    """White/green/blue have no huge-band concept; always False."""
    item = _make_item(1, value=500, quality=1, shape=(4, 4))
    assert is_huge_item(item) is False


# --- sample_session_truth ---

def test_sample_session_single_item_deterministic() -> None:
    """One-item pool, exactly 1 of it per session → deterministic ground truth."""
    items = {1: _make_item(1, value=2500, quality=4, shape=(2, 2))}   # area 4
    pool = _make_pool(900, [(101, 1, 1, 1, 1)])
    bmap = _make_map(2000, 900, min_items=1, max_items=1)

    truth = sample_session_truth(
        2000,
        maps={2000: bmap},
        drops={900: pool},
        items=items,
        rng=np.random.default_rng(0),
    )
    assert truth.map_id == 2000
    assert truth.warehouse_total_cells == 4
    assert set(truth.buckets.keys()) == {4}
    b = truth.buckets[4]
    assert b.count == 1
    assert b.total_cells == 4
    assert b.value_sum == 2500
    assert b.huge_count == 0       # 2x2 < 16-cell huge threshold for purple
    assert b.items[0].item_id == 1


def test_sample_session_groups_items_by_quality() -> None:
    """Two qualities sampled → two buckets, correct sums."""
    items = {
        1: _make_item(1, value=1000, quality=4, shape=(2, 2)),   # purple, area 4
        2: _make_item(2, value=50_000, quality=6, shape=(3, 3)), # red, area 9
    }
    pool = _make_pool(900, [(101, 1, 1, 1, 1), (101, 2, 1, 1, 1)])
    bmap = _make_map(2000, 900, min_items=20, max_items=20)

    truth = sample_session_truth(
        2000,
        maps={2000: bmap},
        drops={900: pool},
        items=items,
        rng=np.random.default_rng(42),
    )
    # 20 draws across 2 equally-weighted items → both qualities present.
    assert set(truth.buckets.keys()) == {4, 6}
    total_items = sum(b.count for b in truth.buckets.values())
    assert total_items == 20
    total_cells = sum(b.total_cells for b in truth.buckets.values())
    assert truth.warehouse_total_cells == total_cells
    # Sanity: total value is sum of all bucket value_sums.
    total_value = sum(b.value_sum for b in truth.buckets.values())
    assert truth.total_value() == total_value


def test_sample_session_huge_count_counts_qualifying_items() -> None:
    """Pool of one 4x4 purple item; sampling 3 of them → huge_count == 3."""
    items = {1: _make_item(1, value=40_000, quality=4, shape=(4, 4))}   # area 16
    pool = _make_pool(900, [(101, 1, 3, 3, 1)])   # exactly 3 per draw
    bmap = _make_map(2000, 900, min_items=1, max_items=1)

    truth = sample_session_truth(
        2000,
        maps={2000: bmap},
        drops={900: pool},
        items=items,
        rng=np.random.default_rng(7),
    )
    b = truth.buckets[4]
    assert b.count == 3
    assert b.huge_count == 3
    assert b.total_cells == 48        # 3 * 16


def test_sample_session_empty_pool_raises_resolution_error() -> None:
    """An empty pool is incomplete input, not a valid zero-value truth."""
    bmap = _make_map(2000, 900, min_items=5, max_items=5)
    with pytest.raises(DropPoolResolutionError, match="empty_pool"):
        sample_session_truth(
            2000,
            maps={2000: bmap},
            drops={900: DropPool(
                pool_id=900, name="empty", description="",
                pool_type=2, entries=[],
            )},
            items={},
            rng=np.random.default_rng(0),
        )


def test_sample_session_reproducible_with_same_seed() -> None:
    """Same seed → identical SessionTruth (modulo dict ordering)."""
    items = {
        1: _make_item(1, value=1000, quality=2, shape=(2, 1)),
        2: _make_item(2, value=20_000, quality=4, shape=(3, 3)),
        3: _make_item(3, value=300_000, quality=6, shape=(4, 4)),
    }
    pool = _make_pool(900, [(101, 1, 1, 2, 5), (101, 2, 1, 1, 3), (101, 3, 1, 1, 1)])
    bmap = _make_map(2000, 900, min_items=10, max_items=12)

    t1 = sample_session_truth(
        2000, maps={2000: bmap}, drops={900: pool}, items=items,
        rng=np.random.default_rng(123),
    )
    t2 = sample_session_truth(
        2000, maps={2000: bmap}, drops={900: pool}, items=items,
        rng=np.random.default_rng(123),
    )
    for q in t1.buckets:
        assert t2.buckets[q].count == t1.buckets[q].count
        assert t2.buckets[q].total_cells == t1.buckets[q].total_cells
        assert t2.buckets[q].value_sum == t1.buckets[q].value_sum


def test_sample_session_anthology_sub_pool_routing() -> None:
    """A BidMap with sub_pool_weights samples from the chosen sub-map's pool."""
    items = {1: _make_item(1, value=500, quality=2, shape=(1, 1))}
    pool_a = _make_pool(910, [(101, 1, 1, 1, 1)])
    sub_map = _make_map(2001, 910, min_items=1, max_items=1)
    outer = _make_map(2000, 999, min_items=1, max_items=1, sub_weights=[(2001, 1)])

    truth = sample_session_truth(
        2000,
        maps={2000: outer, 2001: sub_map},
        drops={910: pool_a},
        items=items,
        rng=np.random.default_rng(5),
    )
    assert truth.buckets[2].items[0].item_id == 1


def test_prepared_sampler_matches_single_pool_sample_for_same_seed() -> None:
    """Prepared sampler keeps sampling semantics while avoiding repeated flatten."""
    items = {
        1: _make_item(1, value=1000, quality=2, shape=(2, 1)),
        2: _make_item(2, value=20_000, quality=4, shape=(3, 3)),
        3: _make_item(3, value=300_000, quality=6, shape=(4, 4)),
    }
    pool = _make_pool(900, [(101, 1, 1, 2, 5), (101, 2, 1, 1, 3), (101, 3, 1, 1, 1)])
    bmap = _make_map(2000, 900, min_items=10, max_items=12)
    maps = {2000: bmap}
    drops = {900: pool}

    direct = sample_session_truth(
        2000, maps=maps, drops=drops, items=items,
        rng=np.random.default_rng(123),
    )
    sampler = prepare_session_sampler(2000, maps=maps, drops=drops, items=items)
    prepared = sampler.sample(rng=np.random.default_rng(123))

    assert prepared.warehouse_total_cells == direct.warehouse_total_cells
    assert prepared.total_value() == direct.total_value()
    assert {
        q: (b.count, b.total_cells, b.value_sum, b.huge_count)
        for q, b in prepared.buckets.items()
    } == {
        q: (b.count, b.total_cells, b.value_sum, b.huge_count)
        for q, b in direct.buckets.items()
    }


def test_prepared_sampler_temporarily_includes_blue_zodiac_candidates() -> None:
    items = {
        1: _make_item(1, value=1000, quality=2, shape=(1, 1)),
        1306006: _make_item(1306006, value=8_888, quality=3, shape=(2, 2)),
        1306001: _make_item(1306001, value=188_888, quality=6, shape=(3, 3)),
    }
    pool = _make_pool(900, [(101, 1, 1, 1, 1)])
    bmap = _make_map(2000, 900, min_items=1, max_items=1)

    sampler = prepare_session_sampler(
        2000,
        maps={2000: bmap},
        drops={900: pool},
        items=items,
    )
    pool_item_ids = {item.item_id for pool in sampler.pools for item in pool.items}

    assert 1306006 in pool_item_ids
    assert 1306001 not in pool_item_ids


def test_prepared_sampler_rejects_missing_pool_before_zodiac_overlay() -> None:
    items = {
        1306006: _make_item(1306006, value=8_888, quality=3, shape=(2, 2)),
    }
    bmap = _make_map(2527, 2527, min_items=1, max_items=1)

    with pytest.raises(
        DropPoolResolutionError,
        match=r"missing_pool.*pool_id=2527",
    ):
        prepare_session_sampler(
            2527,
            maps={2527: bmap},
            drops={},
            items=items,
        )


def test_prepared_sampler_matches_anthology_sample_for_same_seed() -> None:
    """Prepared sampler preserves anthology sub-pool routing RNG order."""
    items = {
        1: _make_item(1, value=500, quality=2, shape=(1, 1)),
        2: _make_item(2, value=30_000, quality=5, shape=(2, 3)),
    }
    pool_a = _make_pool(910, [(101, 1, 1, 1, 1)])
    pool_b = _make_pool(911, [(101, 2, 1, 1, 1)])
    sub_a = _make_map(2001, 910, min_items=1, max_items=1)
    sub_b = _make_map(2002, 911, min_items=1, max_items=1)
    outer = _make_map(
        2000, 999, min_items=5, max_items=5,
        sub_weights=[(2001, 1), (2002, 3)],
    )
    maps = {2000: outer, 2001: sub_a, 2002: sub_b}
    drops = {910: pool_a, 911: pool_b}

    direct = sample_session_truth(
        2000, maps=maps, drops=drops, items=items,
        rng=np.random.default_rng(42),
    )
    sampler = prepare_session_sampler(2000, maps=maps, drops=drops, items=items)
    prepared = sampler.sample(rng=np.random.default_rng(42))

    assert prepared.warehouse_total_cells == direct.warehouse_total_cells
    assert prepared.total_value() == direct.total_value()
    assert sorted(prepared.buckets) == sorted(direct.buckets)
