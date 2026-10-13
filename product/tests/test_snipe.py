"""Tests for the R2 snipe-bid recommendation."""

from __future__ import annotations

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.observation import (
    QualityBucketObs,
    SessionObs,
)
from bidking_lab.inference.snipe import (
    PassRecommendation,
    SnipeRecommendation,
    compute_pass_recommendation,
    compute_snipe_recommendation,
)


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


def _make_map(map_id, drop_pool_id, *, min_items=15, max_items=20) -> BidMap:
    return BidMap(
        map_id=map_id, name=f"map_{map_id}", description="",
        category=101, auction_mode="open", sub_pool_weights=[],
        rounds_total=10, entry_fee_silver=0, starting_budget_silver=500_000,
        drop_pool_id=drop_pool_id,
        items_per_session_min=min_items, items_per_session_max=max_items,
        value_tier_ui="ui_value_low", mode_flag=4,
        bid_price_ladder=[], raw_row=["0"] * 21,
    )


def _build_small_junky_world():
    """A pool that produces small (\u224860-cell) cabinets dominated by low-tier
    junk. Designed for testing the pass-recommendation gate.
    """
    items = {
        # White/green/blue dominate the count
        101: _make_item(101, value=130, quality=1, shape=(2, 2)),
        102: _make_item(102, value=150, quality=1, shape=(3, 2)),
        201: _make_item(201, value=320, quality=2, shape=(2, 2)),
        301: _make_item(301, value=1_100, quality=3, shape=(2, 2)),
        # Rare purple anchor
        401: _make_item(401, value=8_000, quality=4, shape=(2, 2)),
    }
    pool = _make_pool(900, [
        (101, 101, 2, 4, 8),
        (101, 102, 1, 3, 6),
        (101, 201, 2, 4, 5),
        (101, 301, 1, 2, 3),
        (101, 401, 0, 1, 1),
    ])
    bid_map = _make_map(2407, 900, min_items=8, max_items=12)
    return {2407: bid_map}, {900: pool}, items


def _build_big_warehouse_world():
    """A pool that consistently produces ~130-cell big-warehouse sessions."""
    items = {
        # Whites/greens — cheap fillers
        101: _make_item(101, value=130, quality=1, shape=(2, 2)),         # 4 cells, 130 silver
        201: _make_item(201, value=320, quality=2, shape=(2, 2)),         # 4 cells, 320 silver
        # Blues
        301: _make_item(301, value=1_200, quality=3, shape=(2, 2)),       # 4 cells
        # Purples
        401: _make_item(401, value=8_000, quality=4, shape=(2, 2)),       # 4 cells, 2k/cell
        402: _make_item(402, value=42_000, quality=4, shape=(4, 4)),      # huge, 16 cells
        # Golds — high variance
        501: _make_item(501, value=180_000, quality=5, shape=(3, 3)),     # 9 cells, 20k/cell
        # Reds
        601: _make_item(601, value=500_000, quality=6, shape=(3, 3)),     # 9 cells, big
    }
    pool = _make_pool(900, [
        (101, 101, 2, 4, 6),
        (101, 201, 2, 4, 5),
        (101, 301, 2, 4, 5),
        (101, 401, 1, 3, 3),
        (101, 402, 0, 1, 1),
        (101, 501, 0, 1, 1),
        (101, 601, 0, 1, 1),
    ])
    bid_map = _make_map(2407, 900, min_items=10, max_items=14)
    return {2407: bid_map}, {900: pool}, items


# --- Gating ---

def test_snipe_returns_none_when_aisha_missing_green_outline() -> None:
    """Aisha requires q=1, q=2, q=3 all observed (her R1-R3 outline accumulation)."""
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="aisha", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=10),
            # missing q=2 (green outline cells)
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, rng=np.random.default_rng(0),
    )
    assert rec is None


def test_snipe_returns_none_for_unknown_hero() -> None:
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="sophie", warehouse_total_cells=130,   # type: ignore[arg-type]
        buckets={
            1: QualityBucketObs(quality=1, total_cells=10),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, rng=np.random.default_rng(0),
    )
    assert rec is None


def test_snipe_returns_none_when_warehouse_below_120() -> None:
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=110,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=10),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, rng=np.random.default_rng(0),
    )
    assert rec is None


def test_snipe_returns_none_when_white_green_cells_missing() -> None:
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            3: QualityBucketObs(quality=3, total_cells=8),
            # missing q=1
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, rng=np.random.default_rng(0),
    )
    assert rec is None


def test_snipe_returns_none_when_blue_cells_missing() -> None:
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=10),
            # missing q=3
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, rng=np.random.default_rng(0),
    )
    assert rec is None


def test_snipe_returns_none_when_warehouse_total_cells_unknown() -> None:
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=None,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=10),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, rng=np.random.default_rng(0),
    )
    assert rec is None


def test_snipe_returns_none_when_too_few_matching_samples() -> None:
    """If MC produces zero matches in the warehouse_tolerance window,
    bail rather than emit a noisy recommendation."""
    maps, drops, items = _build_big_warehouse_world()
    # Ask for a warehouse_total_cells far from what the pool ever produces.
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=400,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=10),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, warehouse_tolerance=2,
        min_matching_samples=10, rng=np.random.default_rng(0),
    )
    assert rec is None


# --- Happy path ---

def test_snipe_produces_recommendation_for_big_ethan_session() -> None:
    maps, drops, items = _build_big_warehouse_world()
    # The pool produces ~10-14 items × 4-16 cells ≈ 40-130 cells.
    # Use warehouse_total_cells=80 with tolerance=20 → most samples match.
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            3: QualityBucketObs(quality=3, total_cells=10),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=500, warehouse_tolerance=40,        # generous for synthetic pool
        min_matching_samples=30,
        rng=np.random.default_rng(123),
    )
    assert isinstance(rec, SnipeRecommendation)
    assert rec.map_id == 2407
    assert rec.warehouse_total_cells == 130
    assert rec.low_tier_cells_observed == 24
    # Monotonic quantiles
    assert rec.p25_value <= rec.expected_value <= rec.p75_value <= rec.p90_value
    # safe_floor < expected < snipe_max
    assert rec.safe_floor_bid <= rec.expected_value
    assert rec.snipe_max_bid >= rec.p75_value
    # Rationale references the observed warehouse + low-tier numbers
    assert "130" in rec.rationale
    assert "24" in rec.rationale
    # UI tooltip is non-empty
    tip = rec.as_ui_tooltip()
    assert "\u79d2\u4ed3" in tip and str(rec.snipe_max_bid) in tip.replace(",", "")


def test_snipe_produces_aisha_r3_recommendation() -> None:
    """Aisha at R3 with full q=1+q=2+q=3 outlines: rationale mentions R3 + 0 silver scan."""
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="aisha", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=8),
            2: QualityBucketObs(quality=2, total_cells=6),
            3: QualityBucketObs(quality=3, total_cells=10),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=500, warehouse_tolerance=40, min_matching_samples=30,
        rng=np.random.default_rng(2026),
    )
    assert isinstance(rec, SnipeRecommendation)
    assert rec.hero == "aisha"
    assert rec.round_window == "R3"
    assert rec.low_tier_cells_observed == 24       # 8 + 6 + 10
    # Aisha rationale should mention R3 and the "0 silver" framing.
    assert "R3" in rec.rationale
    assert "\u8f6e\u5ed3" in rec.rationale          # 轮廓
    assert "0 \u94f6\u5e01" in rec.rationale       # 0 银币
    # Tooltip prefix carries the round window.
    tip = rec.as_ui_tooltip()
    assert "(R3)" in tip


def test_snipe_ethan_rationale_mentions_R2_and_scan_tools() -> None:
    """Sanity: Ethan branch produces R2-labelled rationale referencing scan tools."""
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            3: QualityBucketObs(quality=3, total_cells=10),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=400, warehouse_tolerance=40, min_matching_samples=20,
        rng=np.random.default_rng(11),
    )
    assert rec is not None
    assert rec.hero == "ethan"
    assert rec.round_window == "R2"
    assert "\u666e\u54c1\u626b\u63cf" in rec.rationale   # 普品扫描
    assert "\u826f\u54c1\u626b\u63cf" in rec.rationale   # 良品扫描
    assert "(R2)" in rec.as_ui_tooltip()


# --- compute_pass_recommendation ---

def test_pass_returns_none_when_warehouse_too_big() -> None:
    """Pass gate is the inverse of snipe: warehouse must be <= max_warehouse_cells."""
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=120,    # too big
        buckets={
            1: QualityBucketObs(quality=1, total_cells=20),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, rng=np.random.default_rng(0),
    )
    assert rec is None


def test_pass_returns_none_when_low_tier_fraction_too_low() -> None:
    """Low-tier占比 must be >= min_low_tier_fraction (default 0.40)."""
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=8),
            3: QualityBucketObs(quality=3, total_cells=4),
        },
    )
    # 12 / 60 = 0.20 — below the 0.40 threshold.
    rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, min_low_tier_fraction=0.40,
        rng=np.random.default_rng(0),
    )
    assert rec is None


def test_pass_returns_none_when_low_tier_missing() -> None:
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=20),
            # missing q=3
        },
    )
    rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=200, rng=np.random.default_rng(0),
    )
    assert rec is None


def test_pass_produces_recommendation_for_junky_small_cabinet() -> None:
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=22),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    # 30 / 60 = 50%, > 40% — gate passes.
    rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=600, warehouse_tolerance=25,
        min_matching_samples=30,
        rng=np.random.default_rng(123),
    )
    assert isinstance(rec, PassRecommendation)
    assert rec.warehouse_total_cells == 60
    assert rec.low_tier_cells_observed == 30
    assert abs(rec.low_tier_fraction - 0.50) < 1e-9
    # Conditional P25 <= P50 <= P75
    assert rec.p25_value <= rec.expected_value <= rec.p75_value
    # pass_max_bid is the conditional P50; safe_entry is P25
    assert rec.pass_max_bid == rec.expected_value
    assert rec.safe_entry_bid == rec.p25_value
    # The tooltip mentions percentage and pass threshold
    tip = rec.as_ui_tooltip()
    assert "\u4f4e\u4ef7\u4ed3" in tip
    assert str(rec.pass_max_bid).split(",")[0] in tip.replace(",", "")


def test_pass_aisha_branch_uses_R3_window() -> None:
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="aisha", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            2: QualityBucketObs(quality=2, total_cells=8),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    # 30 / 60 = 50% — gate passes.
    rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=600, warehouse_tolerance=25,
        min_matching_samples=30,
        rng=np.random.default_rng(2026),
    )
    assert rec is not None
    assert rec.hero == "aisha"
    assert rec.round_window == "R3"
    assert "\u8f6e\u5ed3" in rec.rationale       # outlines mentioned


def test_snipe_purple_conditioned_when_purple_cells_given() -> None:
    """When q=4.total_cells is provided AND enough samples match, MC tightens."""
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            3: QualityBucketObs(quality=3, total_cells=10),
            4: QualityBucketObs(quality=4, total_cells=12),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=800, warehouse_tolerance=50, purple_tolerance=20,
        min_matching_samples=20, rng=np.random.default_rng(11),
    )
    assert rec is not None
    assert rec.purple_conditioned is True
    assert "\u7d2b\u54c1\u683c\u6570" in rec.rationale   # 紫品格数 mentioned


def test_snipe_falls_back_when_purple_tolerance_too_tight() -> None:
    """Tight purple tolerance pointing at an unreachable cell count → fallback."""
    maps, drops, items = _build_big_warehouse_world()
    # Pool produces purple cells in multiples of 4 (2x2=4 or 4x4=16);
    # claim 13 cells with zero tolerance → impossible match → fallback.
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            3: QualityBucketObs(quality=3, total_cells=10),
            4: QualityBucketObs(quality=4, total_cells=13),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=400, warehouse_tolerance=50, purple_tolerance=0,
        min_matching_samples=20, rng=np.random.default_rng(2027),
    )
    assert rec is not None
    assert rec.purple_conditioned is False
    assert "fallback" in rec.rationale


def test_snipe_purple_conditioned_field_defaults_false_when_no_purple_obs() -> None:
    """When q=4 isn't in obs.buckets, no purple conditioning attempted."""
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            3: QualityBucketObs(quality=3, total_cells=10),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=400, warehouse_tolerance=40, min_matching_samples=20,
        rng=np.random.default_rng(11),
    )
    assert rec is not None
    assert rec.purple_conditioned is False


def test_pass_purple_conditioned_when_purple_cells_given() -> None:
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=22),
            3: QualityBucketObs(quality=3, total_cells=8),
            4: QualityBucketObs(quality=4, total_cells=4),
        },
    )
    rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=600, warehouse_tolerance=25, purple_tolerance=20,
        min_matching_samples=30, rng=np.random.default_rng(2030),
    )
    assert rec is not None
    assert rec.purple_conditioned is True
    assert "\u7d2b\u54c1\u683c\u6570" in rec.rationale


def test_pass_reproducible_with_seed() -> None:
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=22),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    kwargs = dict(
        maps=maps, drops=drops, items=items,
        n_trials=400, warehouse_tolerance=25, min_matching_samples=20,
    )
    r1 = compute_pass_recommendation(session, rng=np.random.default_rng(7), **kwargs)
    r2 = compute_pass_recommendation(session, rng=np.random.default_rng(7), **kwargs)
    assert r1 is not None and r2 is not None
    assert r1.pass_max_bid == r2.pass_max_bid
    assert r1.safe_entry_bid == r2.safe_entry_bid
    assert r1.unconditional_p50 == r2.unconditional_p50


def test_snipe_recommendation_is_reproducible_with_seed() -> None:
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            3: QualityBucketObs(quality=3, total_cells=10),
        },
    )
    kwargs = dict(
        maps=maps, drops=drops, items=items,
        n_trials=300, warehouse_tolerance=40, min_matching_samples=20,
    )
    rec1 = compute_snipe_recommendation(
        session, rng=np.random.default_rng(7), **kwargs,
    )
    rec2 = compute_snipe_recommendation(
        session, rng=np.random.default_rng(7), **kwargs,
    )
    assert rec1 is not None and rec2 is not None
    assert rec1.expected_value == rec2.expected_value
    assert rec1.snipe_max_bid == rec2.snipe_max_bid
    assert rec1.n_matching_samples == rec2.n_matching_samples


def test_snipe_low_confidence_fallback_returns_with_warning() -> None:
    """When the strict min_matching_samples threshold misses but the relaxed
    one (default 10) passes, we should still get a recommendation back —
    flagged with low_confidence=True and a warning baked into rationale.
    """
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            3: QualityBucketObs(quality=3, total_cells=10),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=80, warehouse_tolerance=2,
        min_matching_samples=200,     # impossible at this trial count
        min_matching_samples_relaxed=5,
        rng=np.random.default_rng(31),
    )
    assert rec is not None
    assert rec.low_confidence is True
    assert "\u6837\u672c\u4ec5" in rec.rationale       # "样本仅 N 个"
    assert rec.n_matching_samples < 200
    assert rec.n_matching_samples >= 5


def test_snipe_returns_none_when_even_relaxed_threshold_fails() -> None:
    """Below the relaxed threshold → still None (truly insufficient data)."""
    maps, drops, items = _build_big_warehouse_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=130,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=14),
            3: QualityBucketObs(quality=3, total_cells=10),
        },
    )
    rec = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=20, warehouse_tolerance=0,   # tight + few samples
        min_matching_samples=200,
        min_matching_samples_relaxed=200,
        rng=np.random.default_rng(31),
    )
    assert rec is None


def test_pass_low_confidence_fallback_returns_with_warning() -> None:
    """Symmetric to snipe: pass-rec also surfaces a low-confidence fallback."""
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=22),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=80, warehouse_tolerance=50,   # wide enough to catch the 32-48 cell pool
        min_matching_samples=200,              # impossible at n_trials=80
        min_matching_samples_relaxed=5,
        rng=np.random.default_rng(41),
    )
    assert rec is not None
    assert rec.low_confidence is True
    assert "\u6837\u672c\u4ec5" in rec.rationale
    assert rec.n_matching_samples < 200
    assert rec.n_matching_samples >= 5


def test_pass_suppressed_when_conditional_value_above_unconditional_median() -> None:
    """Regression for false-fire: when the player's input narrows down to an
    above-average warehouse (e.g. red huge=1 → conditional median way above
    the map's overall median), the "drop above X" hint is misleading and
    must be suppressed.
    """
    maps, drops, items = _build_small_junky_world()
    session = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=22),
            3: QualityBucketObs(quality=3, total_cells=8),
        },
    )
    # Default suppress_above_ratio=1.0 — tiny synthetic world has roughly
    # constant cabinet value, so make ratio guard explicit by pushing the
    # threshold below typical ratio (any ratio > 0.0 trips it).
    rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items,
        n_trials=600, warehouse_tolerance=25,
        min_matching_samples=30,
        suppress_above_ratio=0.0,             # forces suppression
        rng=np.random.default_rng(123),
    )
    assert rec is None


def test_pass_red_huge_filters_truths_when_active() -> None:
    """Regression for the user-reported case: when red huge_band is given,
    the API must accept the constraint without raising, and downstream code
    paths that consume cond_red must be exercised.
    """
    maps, drops, items = _build_small_junky_world()
    sess_red = SessionObs(
        map_id=2407, hero="ethan", warehouse_total_cells=60,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=22),
            3: QualityBucketObs(quality=3, total_cells=8),
            6: QualityBucketObs(quality=6, huge_band="1"),
        },
    )
    # Synthetic world has no Q6 huge items; cond_red is empty and the engine
    # must gracefully fall back through tiers without raising.
    rec_red = compute_pass_recommendation(
        sess_red, maps=maps, drops=drops, items=items,
        n_trials=600, warehouse_tolerance=25,
        min_matching_samples=30,
        suppress_above_ratio=2.0,             # disable suppression for the test
        rng=np.random.default_rng(123),
    )
    # In the synthetic world cond_red is empty → fall back to cond_purple/
    # cond_warehouse. Recommendation may or may not return depending on tier
    # availability, but the call must not raise.
    assert rec_red is None or isinstance(rec_red, PassRecommendation)
