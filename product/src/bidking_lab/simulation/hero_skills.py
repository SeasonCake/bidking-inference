"""Hero skill information model for MC.

Each hero reveals partial information about session items. We model
the **information coverage** as a per-item score in [0, 1]:

  1.0 = player knows the item's exact value (or full info)
  0.7 = player knows the item's quality tier
  0.3 = player knows the item's outline/shape
  0.1 = player has a statistical aggregate (count, avg cells)
  0.0 = player knows nothing about this item

A "rational player" bids on items in descending order of
*expected value given revealed info*. Items with quality revealed
can be ranked by quality tier (higher quality ≈ higher value);
items with no info are valued at the session's mean.

For the MC contrast, we compare:
  - **baseline**: no hero, all items equally unknown → bid randomly
  - **with hero**: hero reveals info → player bids on best-known items first

The difference in expected net value is the hero's **marginal value**.

Implementation note: rather than hard-coding 20 hero-specific functions,
we define a small DSL of "skill effects" and compose them. Each hero
maps to a list of effects. Effects are evaluated per-item to produce
the info score.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from bidking_lab.extract.item_table import Item


class InfoType(Enum):
    NONE = 0.0
    COUNT_HINT = 0.1
    OUTLINE = 0.3
    QUALITY = 0.7
    OUTLINE_QUALITY = 0.85  # shape + quality combo (Aisha's 遗珍慧眼 style)
    VALUE = 1.0
    FULL = 1.0


@dataclass(frozen=True)
class SkillEffect:
    """One component of a hero's skill.

    ``filter_categories``: only items whose category (from Item.txt
    col[5] or our mapped tags) matches. Empty = all items.

    ``filter_qualities``: only items whose quality matches. Empty = all.

    ``info_type``: what's revealed for matching items.

    ``max_items``: if >0, only applies to this many items (randomly
    chosen from the matching set). 0 = unlimited.

    ``per_round``: if >0, this effect fires this many items per round
    rather than all at once. Combined with ``rounds`` to get total.

    ``rounds``: how many rounds this effect fires over.

    ``available_at_round``: the round (1-indexed) when this info first
    becomes available. 1 = start of auction (most valuable).
    bid_price_ladder decays per round, so late info is worth less.
    """

    info_type: InfoType
    filter_categories: frozenset[int] = field(default_factory=frozenset)
    filter_qualities: frozenset[int] = field(default_factory=frozenset)
    max_items: int = 0
    per_round: int = 0
    rounds: int = 1
    available_at_round: int = 1
    random_categories: int = 0  # if > 0, restrict to a random subset of N categories per trial


# Timing discount: fraction of "decision value" remaining at each phase.
# Derived from bid_price_ladder [2000, 1600, 1300, 1100, 0].
# Phase 1 info is usable for all 5 phases; phase 5 info only for the
# last (cheapest) phase. Phase 5 gets 0.05 (not 0) because even the
# last round's info can prevent one bad bid.
TIMING_WEIGHTS = {1: 1.0, 2: 0.75, 3: 0.50, 4: 0.30, 5: 0.05}


# Category IDs from Item.txt col[5] (tags field). These are approximate
# groupings reverse-engineered from item names + hero skill text.
# Not all are needed; we define those referenced by hero skills.
CAT_ANTIQUE = 101       # 文玩古董
CAT_JEWELRY = 102       # 珠宝矿藏
CAT_FASHION = 103       # 时尚潮流
CAT_DIGITAL = 104       # 数码电子
CAT_MEDICAL = 105       # 医疗药品
CAT_WEAPON = 106        # 武器装备
CAT_HOUSEHOLD = 107     # 家居日用
CAT_FOOD = 108          # 食品烹饪
CAT_BOOK = 109          # 书籍绘画
CAT_ENERGY = 110        # 能源交通


@dataclass(frozen=True)
class HeroSkillProfile:
    hero_id: int
    name: str
    tier: int
    effects: tuple[SkillEffect, ...]
    description: str = ""


def _e(info: InfoType, cats: frozenset[int] = frozenset(),
       quals: frozenset[int] = frozenset(), max_items: int = 0,
       per_round: int = 0, rounds: int = 1,
       at_round: int = 1, random_categories: int = 0) -> SkillEffect:
    return SkillEffect(
        info, cats, quals, max_items, per_round, rounds, at_round,
        random_categories=random_categories,
    )


# All 20 heroes. Skill breakdown from docs/hero_skill_schema.md.
# ``at_round`` reflects when info first arrives (1=start, 5=last phase).
HERO_SKILLS: dict[int, HeroSkillProfile] = {
    101: HeroSkillProfile(101, "法蒂玛", 1, (
        _e(InfoType.QUALITY, frozenset({CAT_ANTIQUE}), max_items=1, at_round=1),
        _e(InfoType.OUTLINE, frozenset({CAT_ANTIQUE}), max_items=12, per_round=3, rounds=4, at_round=2),
        _e(InfoType.QUALITY, frozenset({CAT_ANTIQUE}), max_items=6, per_round=3, rounds=2, at_round=3),
    )),
    102: HeroSkillProfile(102, "陈美", 1, (
        _e(InfoType.OUTLINE, frozenset({CAT_JEWELRY, CAT_FASHION}), at_round=1),
    )),
    103: HeroSkillProfile(103, "艾莎", 4, (
        # 2026-05-15 screenshot confirms: at level 4, Aisha (遗珍慧眼)
        # reveals outline+quality for one bucket per round, white-up:
        # R1=白, R2=绿, R3=蓝, R4=紫. Game text in Hero.txt col[2] only
        # describes the level-1 (3-round, blue-down) version; the
        # 4-effect col[10] = [1001031,1001032,1001033,1001034] is the
        # 4 level upgrades. Each reveal is OUTLINE + QUALITY (the panel
        # says "显示所有X色品质道具的轮廓和品质") so we use the combined
        # InfoType.OUTLINE_QUALITY.
        _e(InfoType.OUTLINE_QUALITY, quals=frozenset({1}), at_round=1),
        _e(InfoType.OUTLINE_QUALITY, quals=frozenset({2}), at_round=2),
        _e(InfoType.OUTLINE_QUALITY, quals=frozenset({3}), at_round=3),
        _e(InfoType.OUTLINE_QUALITY, quals=frozenset({4}), at_round=4),
    )),
    104: HeroSkillProfile(104, "加布里埃拉", 1, (
        _e(InfoType.QUALITY, max_items=2, per_round=2, rounds=10, at_round=1),
    )),
    105: HeroSkillProfile(105, "塔蒂安娜", 1, (
        _e(InfoType.QUALITY, frozenset({CAT_FASHION}), at_round=1),
        _e(InfoType.OUTLINE, frozenset({CAT_FASHION}), at_round=1),
    )),
    106: HeroSkillProfile(106, "娜奥米", 1, (
        _e(InfoType.OUTLINE, frozenset({CAT_FASHION, CAT_DIGITAL}), at_round=1),
        _e(InfoType.COUNT_HINT, quals=frozenset({5, 6}), at_round=1),
    )),
    107: HeroSkillProfile(107, "索菲", 1, (
        _e(InfoType.QUALITY, max_items=5, at_round=1),
        _e(InfoType.QUALITY, max_items=2, per_round=2, rounds=9, at_round=2),
    )),
    108: HeroSkillProfile(108, "玛丽亚", 1, (
        _e(InfoType.VALUE, quals=frozenset({1}), at_round=1),
        _e(InfoType.VALUE, quals=frozenset({2}), at_round=1),
        _e(InfoType.VALUE, quals=frozenset({3}), at_round=1),
        _e(InfoType.QUALITY, quals=frozenset({1, 2, 3}), at_round=1),
    )),
    109: HeroSkillProfile(109, "海琳娜", 1, (
        _e(InfoType.QUALITY, frozenset({CAT_MEDICAL}), at_round=1),
        _e(InfoType.OUTLINE, frozenset({CAT_MEDICAL}), max_items=2, per_round=2, rounds=10, at_round=1),
    )),
    110: HeroSkillProfile(110, "伊莎贝拉", 1, (
        _e(InfoType.OUTLINE, max_items=1, at_round=1),
        _e(InfoType.OUTLINE, frozenset({CAT_JEWELRY}), max_items=4, at_round=1),
    )),
    201: HeroSkillProfile(201, "乔治", 2, (
        _e(InfoType.QUALITY, frozenset({CAT_WEAPON}), at_round=1),
        _e(InfoType.OUTLINE, frozenset({CAT_WEAPON}), at_round=1),
    )),
    202: HeroSkillProfile(202, "卡洛斯", 2, (
        _e(InfoType.OUTLINE, frozenset({CAT_HOUSEHOLD, CAT_DIGITAL}), at_round=1),
        _e(InfoType.QUALITY, frozenset({CAT_HOUSEHOLD, CAT_DIGITAL}), max_items=2, per_round=2, rounds=10, at_round=2),
    )),
    203: HeroSkillProfile(203, "莱昂纳德", 2, (
        _e(InfoType.QUALITY, frozenset({CAT_FOOD}), at_round=1),
        _e(InfoType.QUALITY, frozenset({CAT_ANTIQUE}), max_items=2, at_round=1),
    )),
    204: HeroSkillProfile(204, "艾哈迈德", 2, (
        _e(InfoType.COUNT_HINT, at_round=1),
        _e(InfoType.COUNT_HINT, quals=frozenset({5}), at_round=2),
        _e(InfoType.COUNT_HINT, quals=frozenset({4}), at_round=3),
        _e(InfoType.COUNT_HINT, quals=frozenset({3}), at_round=4),
        _e(InfoType.COUNT_HINT, quals=frozenset({1, 2}), at_round=5),
    )),
    205: HeroSkillProfile(205, "伊万", 2, (
        _e(InfoType.OUTLINE, frozenset({CAT_WEAPON, CAT_ENERGY}), at_round=1),
    )),
    206: HeroSkillProfile(206, "武田宏志", 2, (
        _e(InfoType.OUTLINE, frozenset({CAT_BOOK}), at_round=1),
        _e(InfoType.QUALITY, frozenset({CAT_BOOK}), max_items=2, per_round=2, rounds=10, at_round=2),
    )),
    207: HeroSkillProfile(207, "吴起灵", 2, (
        _e(InfoType.COUNT_HINT, frozenset({CAT_ANTIQUE}), at_round=1),
        _e(InfoType.OUTLINE, frozenset({CAT_ANTIQUE}), at_round=2),
        _e(InfoType.QUALITY, frozenset({CAT_ANTIQUE}), at_round=3),
        # R4 reveals full info for ~1/3 of antique items (not the whole warehouse).
        _e(InfoType.FULL, frozenset({CAT_ANTIQUE}), max_items=0, at_round=4),
    )),
    208: HeroSkillProfile(208, "伊森", 2, (
        # 2026-05-15 screenshot confirms: Ethan (空间觉知) R1 reveals
        # outlines for ALL items in 5 random categories (out of 10), not
        # 5 random items. R2-R4 fire only on items whose quality is
        # already known (via other tools); we don't model that
        # conditional and skip those rounds in the baseline contrast. R5
        # reveals all outlines. The panel never shows category names on
        # hover, so quality_hint stays None for Ethan outlines.
        _e(InfoType.OUTLINE, at_round=1, random_categories=5),
        _e(InfoType.OUTLINE, at_round=5),
    )),
    209: HeroSkillProfile(209, "维克托", 2, (
        _e(InfoType.COUNT_HINT, quals=frozenset({4, 5, 6}), at_round=1),
    )),
    301: HeroSkillProfile(301, "拉文", 0, (
        _e(InfoType.QUALITY, at_round=5),  # R5 only: all quality (very late!)
    )),
}


def compute_info_score(
    hero_id: int,
    session_items: Sequence[Item],
    *,
    use_timing: bool = True,
    rng: random.Random | None = None,
) -> list[float]:
    """Compute per-item info score [0, 1] for a hero + session.

    Returns a list parallel to ``session_items``. The score represents
    how well the player can estimate each item's value given the hero's
    skill revelations.

    When ``use_timing=True`` (default), scores are discounted by when
    the information arrives. R1 info keeps full value; R5 info is nearly
    worthless because bidding costs are lowest and most good items are
    already gone. See ``TIMING_WEIGHTS``.

    When ``use_timing=False``, behaves like the v1 model (all info
    treated equally regardless of round).
    """
    profile = HERO_SKILLS.get(hero_id)
    if profile is None:
        return [0.0] * len(session_items)

    scores = [0.0] * len(session_items)
    for effect in profile.effects:
        # When random_categories > 0, restrict the filter to a random
        # subset of N categories present in this session. Ethan's R1
        # (5 of 10 categories) uses this; we sample per-trial via the
        # provided RNG. Without an RNG we fall back to a deterministic
        # first-N pick (still useful for tests).
        allowed_categories = effect.filter_categories
        if effect.random_categories > 0:
            present = sorted({_item_category(it) for it in session_items})
            if effect.random_categories < len(present):
                if rng is not None:
                    picked = rng.sample(present, effect.random_categories)
                else:
                    picked = present[: effect.random_categories]
                allowed_categories = frozenset(picked)
            else:
                allowed_categories = frozenset(present)

        matching_indices = []
        for i, item in enumerate(session_items):
            if allowed_categories and _item_category(item) not in allowed_categories:
                continue
            if effect.filter_qualities and item.quality not in effect.filter_qualities:
                continue
            matching_indices.append(i)

        if effect.max_items > 0 and len(matching_indices) > effect.max_items:
            effective_count = min(
                effect.max_items * max(1, effect.rounds),
                len(matching_indices),
            )
            matching_indices = matching_indices[:effective_count]

        raw_score = effect.info_type.value
        if use_timing:
            tw = TIMING_WEIGHTS.get(effect.available_at_round, 0.05)
            effective_score = raw_score * tw
        else:
            effective_score = raw_score

        for i in matching_indices:
            scores[i] = max(scores[i], effective_score)

    return scores


def _item_category(item: Item) -> int:
    """Extract category from Item. Uses the first tag or falls back to raw_row col[5]."""
    if item.tags:
        return item.tags[0]
    try:
        return int(item.raw_row[5])
    except (IndexError, ValueError):
        return 0


__all__ = (
    "CAT_ANTIQUE", "CAT_BOOK", "CAT_DIGITAL", "CAT_ENERGY",
    "CAT_FASHION", "CAT_FOOD", "CAT_HOUSEHOLD", "CAT_JEWELRY",
    "CAT_MEDICAL", "CAT_WEAPON",
    "HERO_SKILLS",
    "HeroSkillProfile",
    "InfoType",
    "SkillEffect",
    "TIMING_WEIGHTS",
    "compute_info_score",
)
