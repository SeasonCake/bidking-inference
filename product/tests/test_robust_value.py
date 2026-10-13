"""Tests for the long-tail-trimming robust estimators."""

from __future__ import annotations

import numpy as np
import pytest

from bidking_lab.extract.item_table import Item
from bidking_lab.simulation.basic_mc import FlattenedPool
from bidking_lab.simulation.robust_value import (
    is_confusable_long_tail,
    robust_session_value,
    winsorize,
)


def _make_item(*, item_id: int, value: int, w: int, h: int) -> Item:
    return Item(
        item_id=item_id,
        name=f"item-{item_id}",
        description="",
        name_key=f"key-{item_id}",
        desc_key=f"desc-{item_id}",
        quality=0,
        quality_color="none",
        value=value,
        shape_w=w,
        shape_h=h,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def test_small_rare_red_is_confusable() -> None:
    # 金陵折扇: 1×2, 1937万
    assert is_confusable_long_tail(_make_item(item_id=1, value=19_371_213, w=1, h=2))


def test_large_rare_red_is_not_confusable() -> None:
    # 蓝鳍金枪鱼: 3×5, 155万
    assert not is_confusable_long_tail(_make_item(item_id=2, value=1_552_000, w=3, h=5))


def test_small_cheap_is_not_confusable() -> None:
    # 1×1 white item: not rare
    assert not is_confusable_long_tail(_make_item(item_id=3, value=500, w=1, h=1))


def test_2x2_is_not_confusable_even_if_expensive() -> None:
    # Area 4 falls outside the default confusable_max_area=3
    #豪宅黑盒 (2×2, 740万) is rescued by area threshold
    assert not is_confusable_long_tail(_make_item(item_id=4, value=7_402_320, w=2, h=2))


def test_non_physical_item_is_not_confusable() -> None:
    # Achievements / skins have w=h=0 and never drop in auctions
    assert not is_confusable_long_tail(_make_item(item_id=5, value=99_999_999, w=0, h=0))


def test_robust_value_drops_small_rare_contribution() -> None:
    cheap = _make_item(item_id=1, value=1000, w=1, h=1)
    fan = _make_item(item_id=2, value=19_000_000, w=1, h=2)  # small-rare → trimmed
    fish = _make_item(item_id=3, value=1_500_000, w=3, h=5)  # large-rare → kept
    items = {1: cheap, 2: fan, 3: fish}
    fp = FlattenedPool(
        item_ids=[1, 2, 3],
        probabilities=[0.99, 0.005, 0.005],
        n_min=[1, 1, 1],
        n_max=[1, 1, 1],
        values=[cheap.value, fan.value, fish.value],
    )

    # Raw closed form: 20 * (0.99*1000 + 0.005*19e6 + 0.005*1.5e6) = 20 * 103480 = 2,069,600
    # Robust: 20 * (0.99*1000 + 0.005*1.5e6) = 20 * 8490 = 169,800
    robust = robust_session_value(fp, items, items_per_session=20.0)
    assert robust == pytest.approx(20.0 * (0.99 * 1000 + 0.005 * 1_500_000))


def test_robust_value_matches_raw_when_nothing_trimmed() -> None:
    # All items below value_floor → nothing trimmed → matches raw
    a = _make_item(item_id=1, value=500_000, w=1, h=1)
    b = _make_item(item_id=2, value=200_000, w=2, h=2)
    items = {1: a, 2: b}
    fp = FlattenedPool(
        item_ids=[1, 2],
        probabilities=[0.7, 0.3],
        n_min=[1, 1],
        n_max=[1, 1],
        values=[a.value, b.value],
    )
    raw = 15.0 * (0.7 * 500_000 + 0.3 * 200_000)
    assert robust_session_value(fp, items, items_per_session=15.0) == pytest.approx(raw)


def test_winsorize_caps_upper_tail() -> None:
    rng = np.random.default_rng(0)
    samples = np.concatenate([rng.normal(loc=10, scale=1, size=990), np.full(10, 1000.0)])
    capped = winsorize(samples, upper_quantile=0.99)
    assert capped.max() < 100
    assert (capped <= capped.max()).all()


def test_winsorize_handles_empty() -> None:
    out = winsorize(np.array([], dtype=np.float64))
    assert out.size == 0
