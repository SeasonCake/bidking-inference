"""Regression gate for the solid-component discount anchor (2026-07-04, raven 4511 R5 群友实战).

Bug: the descending safety multipliers (×0.90/0.85/0.80) discount the WHOLE total, including the
solid fully-evidenced component. With 6/6 reds revealed + count locked, balanced = 0.85×p50(1.66M)
= 1.41M fell BELOW the engine's own red component (1.47M) and the game's minimum bid (1.50M) —
an internal invariant violation (total < own solid part) that lost a winnable game (settled 1.90M).
Fix: tier = solid + (base − solid)×mult — discount rides only on the uncertain remainder.
Rollback: AHMAD_DISABLE_SOLID_VALUE_FLOOR=1.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import _solid_anchor_tier  # noqa: E402


def test_anchor_lifts_tier_above_solid():
    # raven 4511 numbers: base p50 1.66M, mult 0.85, legacy tier 1.41M, solid 1.47M
    out = _solid_anchor_tier(1_662_798, 0.85, 1_413_378, 1_466_297)
    assert out > 1_466_297  # never below the solid component
    assert abs(out - (1_466_297 + (1_662_798 - 1_466_297) * 0.85)) < 1.0


def test_anchor_never_lowers_a_tier():
    # when the legacy tier already exceeds the anchored value, keep the legacy tier
    assert _solid_anchor_tier(2_000_000, 0.85, 1_900_000, 500_000) == 1_900_000


def test_base_below_solid_floors_at_solid():
    # a p25 base below the solid value floors the tier AT solid (uncertain remainder = 0)
    assert _solid_anchor_tier(1_300_000, 0.90, 1_170_000, 1_466_297) == 1_466_297


def test_none_base_floors_at_solid():
    assert _solid_anchor_tier(None, 0.85, 0.0, 100_000) == 100_000
