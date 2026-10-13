"""Gate the per-hero truth-value regime (Phase 5d, 2026-06-27), starting with ahmed.

Migrated heroes (``_VALUE_REGIME_HERO_SAFETY``) price off the truth-anchored drop-table value column
(empirical settled per-quality means) AND a re-calibrated quote safety factor. For ahmed the raw p50
is well-centered (+4.7% over settled truth on 40 NEW samples; count/cells exact) so the legacy 0.85
safety over-discounts balanced to -11%; safety 0.92 re-centers balanced to the blessed ~-2~-5% band.

This pins:
  - the ahmed regime safety constant (so it isn't silently retuned),
  - ahmed engine runs apply BOTH the value overlay and the regime safety note by default,
  - a non-migrated hero stays on legacy values + 0.85 by default,
  - env AHMAD_USE_DROP_TABLE_VALUE=0 forces ahmed back to legacy (A/B escape hatch).

See docs/HANDOFF_drop_rate_closeout_2026-06-27 §8.3-next / memory drop-rate-system-closeout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as E  # noqa: E402

PRIORS = E._load_drop_table_priors()
needs_sidecar = pytest.mark.skipif(not PRIORS, reason="drop_table_priors sidecar not present")


def test_ahmed_regime_safety_pinned() -> None:
    assert E._VALUE_REGIME_HERO_SAFETY.get("ahmed") == 0.92


@needs_sidecar
def test_ahmed_value_item_values_use_truth_table_by_default() -> None:
    """ahmed (in the regime) gets the truth-anchored value overlay with no env set."""
    static = E.load_reference_static_data()
    vals, note = E._quality_item_values(2401, static, value_on=True)
    assert "drop_table_value:2401" in note
    assert vals["q4"] == pytest.approx(float(PRIORS[2401]["quality_values"]["q4"]), abs=1.0)


@needs_sidecar
def test_value_off_by_default_without_value_on() -> None:
    """The overlay stays OFF when value_on is not passed and no env forces it (legacy heroes)."""
    static = E.load_reference_static_data()
    _, note = E._quality_item_values(2401, static)  # value_on defaults False
    assert "drop_table_value" not in note


def test_env_zero_forces_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """AHMAD_USE_DROP_TABLE_VALUE=0 force-disables the overlay even for value_on=True (A/B off arm)."""
    monkeypatch.setenv("AHMAD_USE_DROP_TABLE_VALUE", "0")
    static = E.load_reference_static_data()
    _, note = E._quality_item_values(2401, static, value_on=True)
    assert "drop_table_value" not in note


def test_env_one_forces_value_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """AHMAD_USE_DROP_TABLE_VALUE=1 force-enables for any hero (A/B on arm / value-only mode)."""
    monkeypatch.setenv("AHMAD_USE_DROP_TABLE_VALUE", "1")
    static = E.load_reference_static_data()
    _, note = E._quality_item_values(2401, static, value_on=False)
    assert "drop_table_value:2401" in note
