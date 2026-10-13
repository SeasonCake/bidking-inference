from __future__ import annotations

import pytest

from bidking_lab.inference.tier_value_subset import (
    TierPoolItem,
    enumerate_avg_value_combos,
    enumerate_exact_value_combos,
)


def test_exact_subset_finds_simple_combo() -> None:
    pool = [
        TierPoolItem(item_id=1, value=10_000, cells=4),
        TierPoolItem(item_id=2, value=20_000, cells=4),
        TierPoolItem(item_id=3, value=30_000, cells=4),
    ]
    stats = enumerate_exact_value_combos(pool, 30_000, max_count=2)
    assert (1, 4) in stats.unique_count_cells
    assert stats.elapsed_ms >= 0


def test_exact_subset_is_complete_not_just_nonempty() -> None:
    """The old pool trim silently dropped real combos, and the test above still passed.

    Same pool as above: 10k+20k is a second, equally valid way to reach 30k. The retired
    `value >= target // max_count` floor deleted the 10k item, so only the single-item
    combo survived and the caller had no way to know the answer was incomplete.
    """
    pool = [
        TierPoolItem(item_id=1, value=10_000, cells=4),
        TierPoolItem(item_id=2, value=20_000, cells=4),
        TierPoolItem(item_id=3, value=30_000, cells=4),
    ]
    stats = enumerate_exact_value_combos(pool, 30_000, max_count=2)
    assert stats.unique_count_cells == ((1, 4), (2, 8))
    assert stats.unique_counts == (1, 2)


def test_dear_item_is_not_ranked_out_of_the_pool() -> None:
    """The retired trim sorted by |value * max_count - target| and kept the head.

    That ranks the CHEAPEST items first, so a target that is itself a catalog price got
    its own item truncated away and the caller received an empty result.
    """
    pool = [TierPoolItem(item_id=i, value=v, cells=1) for i, v in enumerate(range(1_000, 40_000, 1_000), start=1)]
    pool.append(TierPoolItem(item_id=99, value=51_801, cells=6))
    stats = enumerate_exact_value_combos(pool, 51_801, max_count=16)
    assert 1 in stats.unique_counts
    assert (1, 6) in stats.unique_count_cells


def test_repeats_are_found_only_when_allowed() -> None:
    """Repeats happen in the real game: one item is listed in several drop entries, so a
    vault can roll it twice. 428 cloud games: 5 settled truths need a repeat, 0 need an
    item outside the catalog."""
    pool = [TierPoolItem(item_id=1, value=25_000, cells=3)]
    without = enumerate_exact_value_combos(pool, 50_000, max_count=4)
    assert without.unique_counts == ()

    with_repeats = enumerate_exact_value_combos(pool, 50_000, max_count=4, allow_repeats=True)
    assert with_repeats.unique_counts == (2,)
    assert with_repeats.unique_count_cells == ((2, 6),)


def test_max_pool_raises_instead_of_silently_truncating() -> None:
    pool = [TierPoolItem(item_id=i, value=1_000 * i, cells=1) for i in range(1, 41)]
    with pytest.raises(ValueError, match="max_pool"):
        enumerate_exact_value_combos(pool, 40_000, max_count=4, max_pool=32)

    # No guard requested -> full pool, complete answer.
    stats = enumerate_exact_value_combos(pool, 40_000, max_count=4)
    assert stats.pool_size == 40
    assert 1 in stats.unique_counts


def test_repeats_over_mitm_threshold_raise_rather_than_return_0_1_results() -> None:
    pool = [TierPoolItem(item_id=i, value=1_000 * i, cells=1) for i in range(1, 41)]
    with pytest.raises(NotImplementedError, match="meet-in-the-middle"):
        enumerate_exact_value_combos(pool, 40_000, max_count=4, allow_repeats=True)


def test_avg_value_expands_count_candidates() -> None:
    pool = [
        TierPoolItem(item_id=1, value=8_000, cells=1),
        TierPoolItem(item_id=2, value=12_000, cells=2),
        TierPoolItem(item_id=3, value=20_000, cells=4),
        TierPoolItem(item_id=4, value=32_000, cells=4),
    ]
    stats = enumerate_avg_value_combos(pool, 20_000.0, max_count=4, product_tolerance=0.51)
    assert (1, 4) in stats.unique_count_cells
