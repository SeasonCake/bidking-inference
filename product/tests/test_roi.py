"""Tests for leave-one-out tool ROI."""

from __future__ import annotations

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.roi import ToolROI, compute_tool_roi


def _make_item(item_id, value, quality, shape=(1, 1)) -> Item:
    w, h = shape
    return Item(
        item_id=item_id, name=f"i{item_id}", description="",
        name_key="k", desc_key="d", quality=quality, quality_color="x",
        value=value, shape_w=w, shape_h=h,
        tags=[], allowed_shelves=[], icon_name="", model_name="",
        raw_row=["0"] * 38,
    )


def _make_pool(pool_id, entries) -> DropPool:
    return DropPool(
        pool_id=pool_id, name=f"pool_{pool_id}", description="",
        pool_type=2,
        entries=[
            DropEntry(category=c, item_id=i, n_min=mn, n_max=mx, weight=w)
            for c, i, mn, mx, w in entries
        ],
    )


def _make_map(map_id, drop_pool_id, *, min_items=8, max_items=8) -> BidMap:
    return BidMap(
        map_id=map_id, name=f"map_{map_id}", description="",
        category=101, auction_mode="open", sub_pool_weights=[],
        rounds_total=10, entry_fee_silver=0, starting_budget_silver=100_000,
        drop_pool_id=drop_pool_id,
        items_per_session_min=min_items, items_per_session_max=max_items,
        value_tier_ui="ui_value_low", mode_flag=4,
        bid_price_ladder=[], raw_row=["0"] * 21,
    )


def _build_mini_world():
    """Small but realistic 4-quality cabinet: 1 blue, 2 purple sizes, 1 gold."""
    items = {
        301: _make_item(301, value=1_100, quality=3, shape=(2, 1)),       # blue
        401: _make_item(401, value=2_500, quality=4, shape=(2, 2)),       # purple small
        402: _make_item(402, value=40_000, quality=4, shape=(4, 4)),      # purple huge
        501: _make_item(501, value=180_000, quality=5, shape=(3, 3)),     # gold
    }
    pool = _make_pool(900, [
        (101, 301, 1, 2, 4),
        (101, 401, 1, 3, 3),
        (101, 402, 0, 1, 1),
        (101, 501, 0, 1, 1),
    ])
    bid_map = _make_map(2407, 900, min_items=4, max_items=6)
    return {2407: bid_map}, {900: pool}, items


def test_compute_tool_roi_returns_one_entry_per_tool() -> None:
    maps, drops, items = _build_mini_world()
    rois = compute_tool_roi(
        2407,
        tool_kit=("\u666e\u54c1\u626b\u63cf", "\u4f18\u54c1\u4f30\u4ef7"),
        maps=maps, drops=drops, items=items,
        hero="ethan", n_trials=10, rng=np.random.default_rng(0),
        per_bucket_top=4,
    )
    assert len(rois) == 2
    names = {r.tool_name for r in rois}
    assert names == {"\u666e\u54c1\u626b\u63cf", "\u4f18\u54c1\u4f30\u4ef7"}
    for r in rois:
        assert isinstance(r, ToolROI)
        assert r.n_trials == 10
        assert r.silver_cost > 0


def test_compute_tool_roi_returns_finite_numbers() -> None:
    """ROI math runs end-to-end and produces finite, structured output.

    Note: with a small kit (no warehouse total, no scans), the absolute
    sign of info_gain can be either positive or negative — the engine
    has too little info to keep the cells side stable, and the priors
    can either over- or under-shoot truth. Phase 2 ROI tables will use
    full ETHAN_DEFAULT_LOADOUT plus a hero outline pin to surface
    realistic numbers; here we only smoke-test the pipeline.
    """
    maps, drops, items = _build_mini_world()
    rois = compute_tool_roi(
        2407,
        tool_kit=("\u4f18\u54c1\u4f30\u4ef7", "\u6781\u54c1\u4f30\u4ef7"),
        maps=maps, drops=drops, items=items,
        hero="ethan", n_trials=10, rng=np.random.default_rng(42),
        per_bucket_top=4,
    )
    purple = next(r for r in rois if r.tool_name == "\u4f18\u54c1\u4f30\u4ef7")
    assert purple.silver_cost == 20_000
    assert np.isfinite(purple.info_gain_value_mean)
    assert purple.info_gain_value_std >= 0
    assert np.isfinite(purple.roi_value)


def test_compute_tool_roi_warehouse_tool_helps_cells_side() -> None:
    """总仓储空间 pins warehouse_total_cells exactly. When the player's
    eyeball estimate is noisy (default σ=10), the LOO run uses
    ``warehouse_total_cells_approx`` and the joint engine can pick a
    different top-1 hypothesis → measurable, non-negative cells-side
    info gain. With σ=0 (player can read cells perfectly) the tool
    should contribute ~0 (it tells you nothing you didn't already know).
    """
    maps, drops, items = _build_mini_world()
    kit = ("\u4f18\u54c1\u4f30\u4ef7", "\u6781\u54c1\u4f30\u4ef7",
           "\u603b\u4ed3\u50a8\u7a7a\u95f4")
    # With realistic noise: tool should at least not hurt on average.
    rois_noisy = compute_tool_roi(
        2407, tool_kit=kit, maps=maps, drops=drops, items=items,
        hero="ethan", n_trials=30, rng=np.random.default_rng(2026),
        per_bucket_top=5, player_warehouse_noise_std=15.0,
    )
    wh_noisy = next(
        r for r in rois_noisy
        if r.tool_name == "\u603b\u4ed3\u50a8\u7a7a\u95f4"
    )
    assert wh_noisy.info_gain_cells_mean >= 0

    # With zero noise: the LOO run also "knows" the truth → tool ROI
    # collapses to zero. This is the documented bug fix's behavior.
    rois_clean = compute_tool_roi(
        2407, tool_kit=kit, maps=maps, drops=drops, items=items,
        hero="ethan", n_trials=15, rng=np.random.default_rng(2026),
        per_bucket_top=5, player_warehouse_noise_std=0.0,
    )
    wh_clean = next(
        r for r in rois_clean
        if r.tool_name == "\u603b\u4ed3\u50a8\u7a7a\u95f4"
    )
    assert wh_clean.info_gain_cells_mean == 0.0


def test_compute_tool_roi_handles_empty_kit() -> None:
    """An empty kit returns an empty list (no division by zero)."""
    maps, drops, items = _build_mini_world()
    rois = compute_tool_roi(
        2407, tool_kit=(),
        maps=maps, drops=drops, items=items,
        hero="ethan", n_trials=5, rng=np.random.default_rng(0),
        per_bucket_top=4,
    )
    assert rois == []


def test_compute_tool_roi_deterministic_with_seed() -> None:
    """Same rng seed → same ROI numbers."""
    maps, drops, items = _build_mini_world()
    kit = ("\u4f18\u54c1\u4f30\u4ef7", "\u603b\u4ed3\u50a8\u7a7a\u95f4")
    r1 = compute_tool_roi(
        2407, tool_kit=kit, maps=maps, drops=drops, items=items,
        hero="aisha", n_trials=10, rng=np.random.default_rng(7),
        per_bucket_top=4,
    )
    r2 = compute_tool_roi(
        2407, tool_kit=kit, maps=maps, drops=drops, items=items,
        hero="aisha", n_trials=10, rng=np.random.default_rng(7),
        per_bucket_top=4,
    )
    for a, b in zip(r1, r2):
        assert a.tool_name == b.tool_name
        assert a.info_gain_value_mean == b.info_gain_value_mean
        assert a.roi_value == b.roi_value
