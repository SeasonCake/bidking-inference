"""Tests for the budget-aware bidding simulator."""

from __future__ import annotations

import numpy as np
import pytest

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.simulation.bidding import BidPolicy, simulate_session


def _item(item_id: int, value: int) -> Item:
    return Item(
        item_id=item_id, name=f"i{item_id}", description="",
        name_key="", desc_key="", quality=3, quality_color="blue",
        value=value, shape_w=1, shape_h=1,
        tags=[], allowed_shelves=[], icon_name="",
        model_name="", raw_row=["0"] * 38,
    )


def _pool(pool_id: int, entries: list[tuple[int, int, int]]) -> DropPool:
    return DropPool(
        pool_id=pool_id, name="p", description="", pool_type=2,
        entries=[
            DropEntry(category=101, item_id=iid, n_min=1, n_max=1, weight=w)
            for iid, _, w in entries
        ],
    )


def _bmap(
    map_id: int, pool_id: int, min_k: int, max_k: int,
    budget: int, fee: int = 0, mode: str = "open",
) -> BidMap:
    return BidMap(
        map_id=map_id, name=f"m{map_id}", description="",
        category=101, auction_mode=mode,
        sub_pool_weights=[], rounds_total=10,
        entry_fee_silver=fee, starting_budget_silver=budget,
        drop_pool_id=pool_id,
        items_per_session_min=min_k, items_per_session_max=max_k,
        value_tier_ui="ui_value_low", mode_flag=4,
        bid_price_ladder=[], raw_row=["0"] * 21,
    )


def test_net_profit_less_than_gross() -> None:
    """With a bid cost, net should be strictly less than gross."""
    items = {1: _item(1, 1000), 2: _item(2, 2000)}
    pool = _pool(9, [(1, 1000, 1), (2, 2000, 1)])
    bm = _bmap(9, 9, 2, 2, budget=100_000, fee=500)
    policy = BidPolicy(bid_factor=0.4, reserve_lo=0.1, reserve_hi=0.3)

    res = simulate_session(
        9, maps={9: bm}, drops={9: pool}, items=items,
        policy=policy, n_trials=3000, rng=np.random.default_rng(42),
    )
    assert res.net_mean < res.gross_mean
    assert res.net_mean > 0
    assert res.entry_fee == 500
    assert res.starting_budget == 100_000


def test_budget_exhaustion_limits_wins() -> None:
    """Tiny budget → can only win a few items."""
    items = {1: _item(1, 10_000)}
    pool = _pool(8, [(1, 10_000, 1)])
    # budget=5000, bid_factor=0.5 → bid=5000 per item, can win at most 1
    bm = _bmap(8, 8, 5, 5, budget=5000)
    policy = BidPolicy(bid_factor=0.5, reserve_lo=0.0, reserve_hi=0.1)

    res = simulate_session(
        8, maps={8: bm}, drops={8: pool}, items=items,
        policy=policy, n_trials=2000, rng=np.random.default_rng(7),
    )
    assert res.items_won_mean <= 1.1  # should be ~1
    assert res.items_total_mean == 5.0
    assert res.budget_util_mean > 0.8  # spends most of budget on that 1 item


def test_zero_budget_yields_loss_from_entry_fee() -> None:
    """If budget=0 and entry_fee>0, net profit ≈ -entry_fee."""
    items = {1: _item(1, 1000)}
    pool = _pool(6, [(1, 1000, 1)])
    bm = _bmap(6, 6, 3, 3, budget=0, fee=1000)
    res = simulate_session(
        6, maps={6: bm}, drops={6: pool}, items=items,
        n_trials=500, rng=np.random.default_rng(0),
    )
    assert res.net_mean == pytest.approx(-1000, abs=1)
    assert res.items_won_mean == 0


def test_higher_budget_yields_higher_net() -> None:
    """Doubling the budget should yield higher mean net profit."""
    items = {i: _item(i, 500 * i) for i in range(1, 11)}
    pool = _pool(5, [(i, 500 * i, 1) for i in range(1, 11)])
    bm_lo = _bmap(5, 5, 8, 8, budget=5_000, fee=100)
    bm_hi = _bmap(5, 5, 8, 8, budget=50_000, fee=100)

    rng = np.random.default_rng(99)
    policy = BidPolicy(bid_factor=0.35, reserve_lo=0.1, reserve_hi=0.3)
    res_lo = simulate_session(
        5, maps={5: bm_lo}, drops={5: pool}, items=items,
        policy=policy, n_trials=5000, rng=rng,
    )
    res_hi = simulate_session(
        5, maps={5: bm_hi}, drops={5: pool}, items=items,
        policy=policy, n_trials=5000, rng=rng,
    )
    assert res_hi.net_mean > res_lo.net_mean
    assert res_hi.items_won_mean > res_lo.items_won_mean


def test_roi_is_positive_for_good_deals() -> None:
    """Low bid_factor + low reserve → high ROI."""
    items = {1: _item(1, 100_000)}
    pool = _pool(3, [(1, 100_000, 1)])
    bm = _bmap(3, 3, 1, 1, budget=1_000_000)
    policy = BidPolicy(bid_factor=0.2, reserve_lo=0.01, reserve_hi=0.1)
    res = simulate_session(
        3, maps={3: bm}, drops={3: pool}, items=items,
        policy=policy, n_trials=3000, rng=np.random.default_rng(5),
    )
    assert res.roi_mean > 2.0  # paying 20% of value → ~400% ROI
