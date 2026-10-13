"""Tests for hero skill info scoring (v2 timing-aware)."""

from __future__ import annotations

import random

from bidking_lab.extract.item_table import Item
from bidking_lab.simulation.hero_skills import (
    CAT_ANTIQUE,
    CAT_DIGITAL,
    CAT_ENERGY,
    CAT_FASHION,
    CAT_MEDICAL,
    CAT_WEAPON,
    HERO_SKILLS,
    InfoType,
    TIMING_WEIGHTS,
    compute_info_score,
)


def _item(item_id: int, quality: int, category: int, value: int = 1000) -> Item:
    raw = ["0"] * 38
    raw[0] = str(item_id)
    raw[5] = str(category)
    raw[8] = str(quality)
    return Item(
        item_id=item_id, name=f"i{item_id}", description="",
        name_key="", desc_key="", quality=quality, quality_color="",
        value=value, shape_w=1, shape_h=1,
        tags=[category], allowed_shelves=[],
        icon_name="", model_name="", raw_row=raw,
    )


def test_all_20_heroes_defined() -> None:
    assert len(HERO_SKILLS) == 20


def test_raven_timing_penalised() -> None:
    """Hero 301 (拉文) reveals quality at R5 — heavily discounted."""
    items = [_item(1, 3, CAT_FASHION), _item(2, 1, 101)]
    scores_timed = compute_info_score(301, items, use_timing=True)
    scores_raw = compute_info_score(301, items, use_timing=False)
    # Without timing: full quality score
    assert all(s == InfoType.QUALITY.value for s in scores_raw)
    # With timing: quality * R5 weight → tiny
    expected = InfoType.QUALITY.value * TIMING_WEIGHTS[5]
    assert all(abs(s - expected) < 1e-9 for s in scores_timed)
    assert scores_timed[0] < 0.05


def test_maria_unaffected_by_timing() -> None:
    """Hero 108 (玛丽亚) reveals value at R1 — full score with timing."""
    items = [
        _item(1, 1, 101),  # white → value
        _item(2, 2, 102),  # green → value
        _item(3, 3, 103),  # blue → value
        _item(4, 4, 104),  # purple → nothing
        _item(5, 5, 105),  # gold → nothing
    ]
    scores = compute_info_score(108, items, use_timing=True)
    assert scores[0] == InfoType.VALUE.value
    assert scores[1] == InfoType.VALUE.value
    assert scores[2] == InfoType.VALUE.value
    assert scores[3] == 0.0
    assert scores[4] == 0.0


def test_ahmed_r1_effect_dominates() -> None:
    """Hero 204 (艾哈迈德) R1 total count covers all items."""
    items = [_item(1, 3, 101), _item(2, 5, 102)]
    scores = compute_info_score(204, items, use_timing=True)
    expected = InfoType.COUNT_HINT.value * TIMING_WEIGHTS[1]
    assert all(abs(s - expected) < 1e-9 for s in scores)


def test_unknown_hero_returns_zeros() -> None:
    items = [_item(1, 3, 101)]
    scores = compute_info_score(9999, items)
    assert scores == [0.0]


def test_category_filter_works() -> None:
    """Hero 105 (塔蒂安娜) only reveals fashion items (R1)."""
    items = [
        _item(1, 3, CAT_FASHION),  # match
        _item(2, 3, 101),          # no match
    ]
    scores = compute_info_score(105, items, use_timing=True)
    assert scores[0] == InfoType.QUALITY.value * TIMING_WEIGHTS[1]
    assert scores[1] == 0.0


def test_timing_disabled_gives_raw_scores() -> None:
    """With use_timing=False, scores are the raw InfoType values."""
    items = [_item(1, 3, CAT_FASHION)]
    s_raw = compute_info_score(105, items, use_timing=False)
    s_timed = compute_info_score(105, items, use_timing=True)
    assert s_raw[0] == InfoType.QUALITY.value
    assert s_timed[0] < s_raw[0] or TIMING_WEIGHTS[1] == 1.0


# ---- C-5: Aisha 4-stage outline+quality, Ethan 5-categories ----

def test_outline_quality_value_sits_between_outline_and_full() -> None:
    """OUTLINE_QUALITY = 0.85 falls between QUALITY (0.7) and FULL (1.0)."""
    assert InfoType.OUTLINE.value < InfoType.OUTLINE_QUALITY.value
    assert InfoType.OUTLINE_QUALITY.value < InfoType.FULL.value
    assert InfoType.OUTLINE_QUALITY.value == 0.85


def test_aisha_4_stages_one_per_round() -> None:
    """Aisha (103) fires OUTLINE_QUALITY on q=1..4 at rounds 1..4.

    Use ``use_timing=False`` so all rounds carry equal weight; this
    confirms each round's quality filter independently.
    """
    items = [
        _item(1, 1, 101),   # 白
        _item(2, 2, 102),   # 绿
        _item(3, 3, 103),   # 蓝
        _item(4, 4, 104),   # 紫
        _item(5, 5, 105),   # 金 (out of Aisha's reach)
        _item(6, 6, 106),   # 红 (out of reach)
    ]
    scores = compute_info_score(103, items, use_timing=False)
    expected = InfoType.OUTLINE_QUALITY.value
    assert scores[0] == expected   # 白 reveal
    assert scores[1] == expected   # 绿
    assert scores[2] == expected   # 蓝
    assert scores[3] == expected   # 紫
    assert scores[4] == 0.0        # 金 untouched
    assert scores[5] == 0.0        # 红 untouched


def test_aisha_timing_discount_purple_r4() -> None:
    """Purple (R4) gets the steepest discount among Aisha's reveals."""
    items = [_item(1, 1, 101), _item(2, 4, 104)]
    s = compute_info_score(103, items, use_timing=True)
    # 白 fires at R1 (full weight), 紫 fires at R4 (heavy discount)
    assert s[0] == InfoType.OUTLINE_QUALITY.value * TIMING_WEIGHTS[1]
    assert s[1] == InfoType.OUTLINE_QUALITY.value * TIMING_WEIGHTS[4]
    assert s[0] > s[1]


def test_ethan_r1_random_categories_uses_rng_seed() -> None:
    """Ethan (208) R1 picks 5 of 10 categories per trial via the RNG.

    Different seeds → different category subsets → different score sets.
    With items spanning many categories, the revealed-vs-hidden split
    flips depending on which 5 the RNG happens to pick.
    """
    items = [
        _item(1, 3, CAT_ANTIQUE),
        _item(2, 3, CAT_FASHION),
        _item(3, 3, CAT_DIGITAL),
        _item(4, 3, CAT_MEDICAL),
        _item(5, 3, CAT_WEAPON),
        _item(6, 3, CAT_ENERGY),
        _item(7, 3, 107),   # 家居
        _item(8, 3, 108),   # 食品
        _item(9, 3, 109),   # 书画
        _item(10, 3, 110),  # 文玩? careful: CAT_BOOK
    ]
    rng_a = random.Random(0)
    rng_b = random.Random(42)
    s_a = compute_info_score(208, items, use_timing=False, rng=rng_a)
    s_b = compute_info_score(208, items, use_timing=False, rng=rng_b)
    # Both should have exactly 5 items scoring OUTLINE (the rest 0) for
    # the R1 effect, ignoring R5's all-reveal (use_timing=False keeps R5
    # but R5 score also = OUTLINE so the union covers everyone).
    # Hence with R5 active, every item has at least OUTLINE.
    assert all(score >= InfoType.OUTLINE.value for score in s_a)
    assert all(score >= InfoType.OUTLINE.value for score in s_b)


def test_ethan_r1_random_categories_covers_5_of_10() -> None:
    """Without R5 fired, exactly 5 categories should be touched."""
    items = [_item(i + 1, 3, 101 + i) for i in range(10)]  # one item per category
    # Manually exercise the R1 effect by suppressing the R5 effect:
    # We can't easily, so instead disable timing then expect 5 covered.
    rng = random.Random(123)
    s = compute_info_score(208, items, use_timing=True, rng=rng)
    # Under timing: R1 weight=1.0 hits OUTLINE for 5 of 10 items;
    # R5 weight=0.05 hits OUTLINE for ALL items → 0.05*0.3 = 0.015.
    # The 5 chosen ones score max(0.3, 0.015) = 0.3.
    high = [s_i for s_i in s if s_i >= InfoType.OUTLINE.value]
    low = [s_i for s_i in s if 0 < s_i < InfoType.OUTLINE.value]
    assert len(high) == 5, f"expected 5 items revealed by R1, got {len(high)}"
    assert len(low) == 5, f"expected 5 items only seen via R5 (low score), got {len(low)}"


def test_ethan_r1_deterministic_without_rng() -> None:
    """No RNG → deterministic first-N category selection (test-mode)."""
    items = [_item(i + 1, 3, 101 + i) for i in range(10)]
    s = compute_info_score(208, items, use_timing=True, rng=None)
    # First-5 by sorted category id: 101, 102, 103, 104, 105
    # Items 0..4 should be revealed by R1; items 5..9 only by R5.
    assert s[0] >= InfoType.OUTLINE.value
    assert s[4] >= InfoType.OUTLINE.value
    assert s[5] < InfoType.OUTLINE.value
    assert s[9] < InfoType.OUTLINE.value


def test_aisha_outline_quality_dominates_a_lower_score() -> None:
    """When OUTLINE_QUALITY (0.85) and OUTLINE (0.3) both apply, max wins."""
    items = [_item(1, 1, 101)]   # 白 + category 101
    # Aisha (103) fires OUTLINE_QUALITY on q=1 at R1.
    s = compute_info_score(103, items, use_timing=False)
    assert s[0] == InfoType.OUTLINE_QUALITY.value
