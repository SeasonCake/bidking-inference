"""Item.txt single-price lookup for enumeration boost (q=4 紫, q=5 金)."""

from __future__ import annotations

import pytest

from bidking_lab.inference.observation import (
    QualityBucketObs,
    _max_value_per_single_item,
    candidates_for_bucket,
    lookup_single_item_value,
)


def _require_item_db() -> None:
    from pathlib import Path

    p = Path("data/raw/tables/Item.txt")
    if not p.is_file():
        pytest.skip("Item.txt not available")


@pytest.fixture(autouse=True)
def _reset_item_cache() -> None:
    import bidking_lab.inference.observation as obs_mod

    obs_mod._ITEM_DB_BY_QUALITY = None
    obs_mod._MAX_CELLS_PER_ITEM_BY_QUALITY = None
    obs_mod._MAX_VALUE_PER_ITEM_BY_QUALITY = None
    yield
    obs_mod._ITEM_DB_BY_QUALITY = None
    obs_mod._MAX_CELLS_PER_ITEM_BY_QUALITY = None
    obs_mod._MAX_VALUE_PER_ITEM_BY_QUALITY = None


# (quality, value, expected_boost_cells, ambiguous, over_max, tier)
_LOOKUP_CASES: list[tuple[int, int, frozenset[int], bool, bool, str]] = [
    # 紫 q=4 — unique prices from Item.txt
    (4, 20_082, frozenset({12}), False, False, "exact"),
    (4, 31_688, frozenset({10}), False, False, "exact"),
    (4, 25_000, frozenset({6}), False, False, "exact"),
    (4, 23_760, frozenset({4}), False, False, "exact"),
    (4, 22_064, frozenset({9}), False, False, "exact"),
    (4, 21_000, frozenset({4}), False, False, "exact"),
    (4, 18_872, frozenset({9}), False, False, "exact"),
    # 紫 — same price, different footprints → no boost
    (4, 10_000, frozenset(), True, False, "exact"),
    # 紫 — above any single-item price (combo total)
    (4, 1_234_567, frozenset(), False, True, "none"),
    # 金 q=5 — unique / same-footprint multi-name
    (5, 24_435, frozenset({2}), False, False, "exact"),
    (5, 88_473, frozenset({8}), False, False, "exact"),
    (5, 85_800, frozenset({12}), False, False, "exact"),
    (5, 74_745, frozenset({12}), False, False, "exact"),
    (5, 106_500, frozenset({18}), False, False, "exact"),
    (5, 199_900, frozenset({16}), False, False, "exact"),
    (5, 29_025, frozenset(), True, False, "exact"),
    # 金 — no item near price (under max but no hit)
    (5, 12_345_678, frozenset(), False, True, "none"),
]


@pytest.mark.parametrize(
    "quality, value, boost_cells, ambiguous, over_max, tier",
    _LOOKUP_CASES,
    ids=[f"q{q}-v{v}" for q, v, *_ in _LOOKUP_CASES],
)
def test_lookup_single_item_value_cases(
    quality: int,
    value: int,
    boost_cells: frozenset[int],
    ambiguous: bool,
    over_max: bool,
    tier: str,
) -> None:
    _require_item_db()
    lu = lookup_single_item_value(quality, value)
    assert lu.boost_cells == boost_cells
    assert lu.ambiguous is ambiguous
    assert lu.over_max is over_max
    assert lu.tier == tier


def test_max_value_per_single_item_purple_gold() -> None:
    _require_item_db()
    assert _max_value_per_single_item(4) == 31_688
    assert _max_value_per_single_item(5) == 199_900


@pytest.mark.parametrize(
    "quality, value, cells",
    [
        (4, 20_082, 12),
        (4, 31_688, 10),
        (5, 24_435, 2),
        (5, 88_473, 8),
        (5, 85_800, 12),
        (5, 199_900, 16),
    ],
)
def test_candidates_top1_db_hit(quality: int, value: int, cells: int) -> None:
    _require_item_db()
    bucket = QualityBucketObs(quality=quality, value_sum=value)
    cands = candidates_for_bucket(bucket, warehouse_capacity=80)
    assert cands
    assert (cands[0].total_cells, cands[0].count) == (cells, 1)
    assert cands[0].is_db_matched


@pytest.mark.parametrize(
    "quality, value",
    [(4, 10_000), (5, 29_025)],
)
def test_candidates_no_db_boost_when_ambiguous_price(
    quality: int, value: int,
) -> None:
    _require_item_db()
    bucket = QualityBucketObs(quality=quality, value_sum=value)
    cands = candidates_for_bucket(bucket, warehouse_capacity=80)
    assert cands
    assert not any(c.is_db_matched for c in cands)


def test_candidates_no_db_boost_over_max_purple() -> None:
    _require_item_db()
    bucket = QualityBucketObs(quality=4, value_sum=1_234_567)
    cands = candidates_for_bucket(bucket, warehouse_capacity=80)
    assert cands
    assert not any(c.is_db_matched for c in cands)


def test_candidates_no_db_boost_over_max_gold() -> None:
    _require_item_db()
    bucket = QualityBucketObs(quality=5, value_sum=12_345_678)
    cands = candidates_for_bucket(bucket, warehouse_capacity=80)
    assert cands
    assert not any(c.is_db_matched for c in cands)


def test_candidates_count2_unaffected_by_db_boost() -> None:
    _require_item_db()
    bucket = QualityBucketObs(quality=5, count=3, value_sum=24_435)
    cands = candidates_for_bucket(bucket, warehouse_capacity=80)
    assert cands
    assert all(c.count == 3 for c in cands)
    assert not any(c.is_db_matched for c in cands)
