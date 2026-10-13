"""Gate the NEW procedural drop-table prior wiring (Phase 3, 2026-06-27).

The post-2026-06-25-noon drop table (drop_weights_all_maps_0627) is consumed by the live engine
through a per-map prior sidecar (drop_table_priors_0627.json -> static_data["drop_table_priors"]):

  - _quality_probabilities: table quality mix is the source by DEFAULT (env AHMAD_DISABLE_DROP_TABLE
    _QUALITY=1 reverts). It is ~identical to the legacy StaticData/56xx-patch source (same measured
    weights) for the 145 covered maps, so this is near-no-op — the gate proves it MATCHES the sidecar
    and that the disable flag falls back to the legacy path.
  - _quality_item_values: the fitted per-quality value overlay is GATED OFF by default (env
    AHMAD_USE_DROP_TABLE_VALUE=1 to enable), because item_values scales every hero's price and is
    rolled out per-hero during the Phase 5 re-tune. The gate proves DEFAULT == legacy (zero change)
    and that the flag overlays the sidecar's fitted q6 value.

See docs/HANDOFF_drop_rate_closeout_2026-06-27 §8 + memory drop-rate-system-closeout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as E  # noqa: E402

PRIORS = E._load_drop_table_priors()
_HAS_SIDECAR = bool(PRIORS)
needs_sidecar = pytest.mark.skipif(not _HAS_SIDECAR, reason="drop_table_priors sidecar not present")


def _static():
    return E.load_reference_static_data()


@needs_sidecar
@pytest.mark.parametrize("map_id", [5601, 2501, 2201, 2601, 2401, 2301])
def test_quality_probs_match_sidecar_by_default(map_id: int) -> None:
    """Default ON: _quality_probabilities returns the sidecar's (normalized) quality mix."""
    probs, note = E._quality_probabilities(map_id, _static())
    assert note == f"drop_table_prob:{map_id}"
    sidecar = PRIORS[map_id]["quality_probs"]
    tot = sum(sidecar.values()) or 1.0
    for k in E.QUALITY_KEYS:
        assert probs[k] == pytest.approx(sidecar[k] / tot, abs=1e-6)


@needs_sidecar
@pytest.mark.parametrize("map_id", [4501, 4510, 4530])
def test_family45_excluded_falls_to_legacy_105(map_id: int) -> None:
    """45xx (深海暗拍) is omitted from the sidecar: settled truth q6=7.64% matches the legacy
    105 sealed-cabin alias, NOT the memory dropmap's classic-like 5.1%. Engine must use tier105."""
    assert map_id not in PRIORS
    _, note = E._quality_probabilities(map_id, _static())
    assert note == "tier_prob:105"


@needs_sidecar
def test_quality_probs_disable_flag_reverts_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """AHMAD_DISABLE_DROP_TABLE_QUALITY=1 falls back to the StaticData/56xx-patch path."""
    monkeypatch.setenv("AHMAD_DISABLE_DROP_TABLE_QUALITY", "1")
    _, note = E._quality_probabilities(5601, _static())
    assert not note.startswith("drop_table_prob")
    assert note.startswith("tier_prob")


@needs_sidecar
@pytest.mark.parametrize("map_id", [5601, 2501, 2401, 2201])
def test_item_values_default_is_legacy_unchanged(map_id: int) -> None:
    """Value side is GATED OFF by default -> byte-identical to the legacy nest-price path."""
    static = _static()
    new_vals, new_note = E._quality_item_values(map_id, static)
    # legacy = same call with the table-value flag explicitly off (it is off by default)
    assert "drop_table_value" not in new_note
    # reconstruct legacy by temporarily stripping the sidecar from static_data
    legacy_static = dict(static)
    legacy_static["drop_table_priors"] = {}
    legacy_vals, _ = E._quality_item_values(map_id, legacy_static)
    assert new_vals == legacy_vals


@needs_sidecar
def test_item_values_flag_overlays_fitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """AHMAD_USE_DROP_TABLE_VALUE=1 overlays the sidecar's fitted per-quality values (where >0)."""
    monkeypatch.setenv("AHMAD_USE_DROP_TABLE_VALUE", "1")
    map_id = 2401
    vals, note = E._quality_item_values(map_id, _static())
    assert f"drop_table_value:{map_id}" in note
    fitted = PRIORS[map_id]["quality_values"]
    assert vals["q6"] == pytest.approx(float(fitted["q6"]), abs=1.0)
