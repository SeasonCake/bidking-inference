"""Tests for map-level likelihood from partial observations."""

from __future__ import annotations

import pytest

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import BucketTruth, SessionTruth
from bidking_lab.inference.map_likelihood import (
    category_observation_soft_score,
    estimate_map_likelihood,
    summarize_map_truths,
    truth_matches_obs,
)
from bidking_lab.inference.observation import (
    CategoryItemObservation,
    QualityBucketObs,
    SessionObs,
)


def _item(
    item_id: int,
    *,
    quality: int,
    value: int,
    shape: tuple[int, int],
    tags: list[int] | None = None,
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
        tags=tags or [],
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


def _truth(
    *,
    warehouse: int,
    q4_cells: int,
    q4_count: int = 1,
    q4_value: int = 20_000,
) -> SessionTruth:
    bucket = BucketTruth(
        quality=4,
        count=q4_count,
        total_cells=q4_cells,
        value_sum=q4_value,
        huge_count=0,
    )
    return SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=warehouse,
        buckets={4: bucket},
    )


def test_truth_matches_obs_does_not_apply_fallback_warehouse_when_missing() -> None:
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        buckets={4: QualityBucketObs(quality=4, total_cells=8)},
    )

    assert truth_matches_obs(_truth(warehouse=42, q4_cells=8), obs)


def test_truth_matches_obs_applies_warehouse_when_provided() -> None:
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        warehouse_total_cells=80,
        buckets={4: QualityBucketObs(quality=4, total_cells=8)},
    )

    assert not truth_matches_obs(_truth(warehouse=42, q4_cells=8), obs)


def test_truth_matches_obs_applies_visible_outline_lower_bounds() -> None:
    obs = SessionObs(
        map_id=0,
        hero="ethan",
        visible_outline_item_count_min=2,
        visible_outline_total_cells_min=12,
    )

    assert truth_matches_obs(
        SessionTruth(
            map_id=2401,
            map_name="test",
            warehouse_total_cells=12,
            buckets={
                3: BucketTruth(quality=3, count=1, total_cells=6, value_sum=10),
                4: BucketTruth(quality=4, count=1, total_cells=6, value_sum=20),
            },
        ),
        obs,
    )
    assert not truth_matches_obs(
        SessionTruth(
            map_id=2401,
            map_name="test",
            warehouse_total_cells=11,
            buckets={
                3: BucketTruth(quality=3, count=1, total_cells=11, value_sum=10),
            },
        ),
        obs,
    )


def test_truth_matches_obs_applies_quality_bucket_lower_bounds() -> None:
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        buckets={
            4: QualityBucketObs(
                quality=4,
                total_cells_min=8,
                count_min=2,
            ),
        },
    )

    assert truth_matches_obs(_truth(warehouse=20, q4_cells=9, q4_count=2), obs)
    assert not truth_matches_obs(_truth(warehouse=20, q4_cells=7, q4_count=2), obs)
    assert not truth_matches_obs(_truth(warehouse=20, q4_cells=9, q4_count=1), obs)


def test_quality_sample_placeholder_does_not_filter_truths_yet() -> None:
    obs = SessionObs(
        map_id=0,
        hero="ethan",
        quality_sample_histogram={6: 5},
        quality_sample_mode="without_replacement",
    )

    assert truth_matches_obs(_truth(warehouse=42, q4_cells=8), obs)


def test_category_item_observation_soft_scores_without_hard_rejecting() -> None:
    matching = _item(1, quality=4, value=20_000, shape=(3, 1), tags=[107])
    mismatch = _item(2, quality=4, value=20_000, shape=(3, 1), tags=[102])
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        category_items=(
            CategoryItemObservation(category=107, cells=3),
        ),
    )
    matching_truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=3,
        buckets={
            4: BucketTruth(
                quality=4,
                count=1,
                total_cells=3,
                value_sum=20_000,
                items=[matching],
            ),
        },
    )
    mismatch_truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=3,
        buckets={
            4: BucketTruth(
                quality=4,
                count=1,
                total_cells=3,
                value_sum=20_000,
                items=[mismatch],
            ),
        },
    )

    assert truth_matches_obs(mismatch_truth, obs)
    assert category_observation_soft_score(matching_truth, obs) == 1
    assert 0 < category_observation_soft_score(mismatch_truth, obs) < 1

    result = summarize_map_truths(
        2401,
        "test",
        [matching_truth, mismatch_truth],
        obs,
    )

    assert result.n_matched == 2
    assert result.likelihood == (1 + 0.35) / 2


def test_category_item_observation_matches_secondary_item_tags() -> None:
    multi_tag = _item(1, quality=3, value=3_875, shape=(2, 3), tags=[103, 101])
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        category_items=(CategoryItemObservation(category=101, cells=6),),
    )
    truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=6,
        buckets={
            3: BucketTruth(
                quality=3,
                count=1,
                total_cells=6,
                value_sum=3_875,
                items=[multi_tag],
            ),
        },
    )

    assert category_observation_soft_score(truth, obs) == 1


def test_category_item_observation_uses_shape_key_when_present() -> None:
    matching = _item(1, quality=6, value=300_000, shape=(3, 4), tags=[108])
    same_area = _item(2, quality=6, value=300_000, shape=(4, 3), tags=[108])
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        category_items=(CategoryItemObservation(category=108, cells=12, shape_key="34"),),
    )
    matching_truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=12,
        buckets={
            6: BucketTruth(
                quality=6,
                count=1,
                total_cells=12,
                value_sum=300_000,
                items=[matching],
            ),
        },
    )
    same_area_truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=12,
        buckets={
            6: BucketTruth(
                quality=6,
                count=1,
                total_cells=12,
                value_sum=300_000,
                items=[same_area],
            ),
        },
    )

    assert category_observation_soft_score(matching_truth, obs) == 1
    assert 0 < category_observation_soft_score(same_area_truth, obs) < 1


def test_summarize_map_truths_reports_likelihood_and_quantiles() -> None:
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        buckets={4: QualityBucketObs(quality=4, total_cells=8)},
    )
    truths = [
        _truth(warehouse=42, q4_cells=8, q4_value=10_000),
        _truth(warehouse=50, q4_cells=9, q4_value=20_000),
        _truth(warehouse=70, q4_cells=20, q4_value=80_000),
    ]

    result = summarize_map_truths(2401, "test", truths, obs, cells_tol=1)

    assert result.n_total == 3
    assert result.n_matched == 2
    assert result.likelihood == 2 / 3
    assert result.total_cells is not None
    assert result.total_cells.p50 == 46
    assert result.total_value is not None
    assert result.total_value.p50 == 15_000


def test_estimate_map_likelihood_ranks_matching_map_without_warehouse() -> None:
    items = {
        1: _item(1, quality=4, value=20_000, shape=(2, 2)),
        2: _item(2, quality=4, value=90_000, shape=(3, 3)),
    }
    maps = {2401: _map(2401, 9001), 2402: _map(2402, 9002)}
    drops = {9001: _pool(9001, 1), 9002: _pool(9002, 2)}
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        buckets={4: QualityBucketObs(quality=4, total_cells=4)},
    )

    results = estimate_map_likelihood(
        [2401, 2402],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=20,
        seed=1,
    )

    assert [result.map_id for result in results] == [2401, 2402]
    assert results[0].likelihood == 1
    assert results[0].posterior_probability == 1
    assert results[1].likelihood == 0


def test_estimate_map_likelihood_uses_category_items_as_soft_weight() -> None:
    items = {
        1: _item(1, quality=4, value=20_000, shape=(2, 2), tags=[107]),
        2: _item(2, quality=4, value=20_000, shape=(2, 2), tags=[102]),
    }
    maps = {2401: _map(2401, 9001), 2402: _map(2402, 9002)}
    drops = {9001: _pool(9001, 1), 9002: _pool(9002, 2)}
    obs = SessionObs(
        map_id=0,
        hero="aisha",
        buckets={4: QualityBucketObs(quality=4, total_cells=4)},
        category_items=(CategoryItemObservation(category=107, cells=4),),
    )

    results = estimate_map_likelihood(
        [2401, 2402],
        obs,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=20,
        seed=1,
    )

    assert [result.map_id for result in results] == [2401, 2402]
    assert results[0].likelihood == 1
    assert results[1].likelihood == pytest.approx(0.35)
    assert results[0].posterior_probability > results[1].posterior_probability
