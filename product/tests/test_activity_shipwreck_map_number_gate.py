"""Gate for the 2026-07-06 shipwreck activity RE-CLASSIFICATION: activity is the map NUMBER
X511-X520 (2511-2520 / 4511-4520), not the match date.

Census (full pool, scripts shipwreck_by_mapnum): X511-X520 have ZERO pre-2026-07-02 samples
(activity-exclusive ids) and settled red rate 12.11% vs the normal X501-X510 wreckage 7.33%
(1.65x rate; factor 1.74 after renormalize hits the 12.16% q6 share). The old time-window gate
wrongly boosted normal X501-X510 games played during the window — fixed here by gating on map id.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import (  # noqa: E402
    ACTIVITY_SHIPWRECK_RED_BOOST_FACTOR,
    RED_CRATE_SHIPWRECK_RED_BOOST_FACTOR,
    _apply_activity_shipwreck_red_boost,
    _is_activity_shipwreck_map,
    _is_red_crate_activity_shipwreck_map,
    _is_shipwreck_map,
    _quality_probabilities,
    load_reference_static_data,
)


def test_activity_map_number_classification():
    # X511-X520 = activity; X501-X510 (normal wreckage) and X531+ (old) = NOT activity
    for mid in (2511, 2515, 2520, 4511, 4517, 4520):
        assert _is_activity_shipwreck_map(mid) is True, mid
    for mid in (2501, 2504, 2510, 2521, 2531, 2545, 4501, 4510, 4521, 4531, 4545):
        assert _is_activity_shipwreck_map(mid) is False, mid
    # non-shipwreck never activity
    assert _is_activity_shipwreck_map(2401) is False
    assert _is_shipwreck_map(2511) is True and _is_shipwreck_map(2401) is False


def test_factor_recalibrated_to_1_74():
    assert abs(ACTIVITY_SHIPWRECK_RED_BOOST_FACTOR - 1.74) < 1e-9


def test_boost_gates_by_map_number_not_date():
    sd = load_reference_static_data()
    # Activity map WITHOUT a date still boosts (map number is sufficient evidence).
    # The current package deliberately removes 2511 from quote authority because
    # its route changed.  Keep this helper-level calibration check independent of
    # that fail-closed package scope by freezing the historical pre-route prior.
    probs = {
        "q1": 0.2947376,
        "q3": 0.2526319,
        "q4": 0.2526315,
        "q5": 0.1263156,
        "q6": 0.0736834,
    }
    boosted = _apply_activity_shipwreck_red_boost(dict(probs), 2511, {}, [])
    assert boosted["q6"] > probs["q6"]
    # renormalized q6 share reaches ~12.1% (empirical activity red rate)
    assert 0.118 <= boosted["q6"] <= 0.125

    # Normal wreckage NEVER boosts, even played during the window (created_at in-window).
    import datetime
    in_window = datetime.datetime.strptime("2026-07-04 10:00:00", "%Y-%m-%d %H:%M:%S").timestamp()
    probs2, _ = _quality_probabilities(2501, sd)
    boosted2 = _apply_activity_shipwreck_red_boost(dict(probs2), 2501, {"created_at": in_window}, [])
    assert boosted2["q6"] == probs2["q6"]  # unchanged — 2501 is normal wreckage


def test_red_crate_activity_uses_its_independent_milder_q6_regime():
    """The 07-16 production census supports a q6 lift, but not the older 1.74 regime."""
    sd = load_reference_static_data()
    for source_map_id in (2541, 2545, 2550, 4541, 4545, 4550):
        probs, _ = _quality_probabilities(source_map_id, sd)
        notes: list[str] = []
        boosted = _apply_activity_shipwreck_red_boost(
            dict(probs), source_map_id, {}, notes
        )
        expected_q6 = probs["q6"] * RED_CRATE_SHIPWRECK_RED_BOOST_FACTOR / (
            1.0 + (RED_CRATE_SHIPWRECK_RED_BOOST_FACTOR - 1.0) * probs["q6"]
        )
        assert abs(boosted["q6"] - expected_q6) < 1e-9, source_map_id
        assert abs(sum(boosted.values()) - 1.0) < 1e-9, source_map_id
        assert notes == ["red_crate_shipwreck_red_boost:1.22"], source_map_id

    assert RED_CRATE_SHIPWRECK_RED_BOOST_FACTOR == 1.22
    assert _is_red_crate_activity_shipwreck_map(2541) is True
    assert _is_red_crate_activity_shipwreck_map(4545) is True
    assert _is_red_crate_activity_shipwreck_map(2551) is False


def test_red_crate_activity_has_an_independent_kill_switch(monkeypatch):
    sd = load_reference_static_data()
    monkeypatch.setenv("AHMAD_DISABLE_RED_CRATE_SHIPWRECK_RED_BOOST", "1")
    probs, _ = _quality_probabilities(2545, sd)
    notes: list[str] = []
    assert _apply_activity_shipwreck_red_boost(dict(probs), 2545, {}, notes) == probs
    assert notes == []

    # The new switch must not disable the older X511-X520 activity regime.
    legacy, _ = _quality_probabilities(2515, sd)
    assert _apply_activity_shipwreck_red_boost(dict(legacy), 2515, {}, [])["q6"] > legacy["q6"]


def test_red_crate_activity_factor_override_is_independent(monkeypatch):
    sd = load_reference_static_data()
    probs, _ = _quality_probabilities(2545, sd)
    monkeypatch.setenv("AHMAD_ACTIVITY_SHIPWRECK_RED_BOOST_FACTOR", "2.50")
    monkeypatch.setenv("AHMAD_RED_CRATE_SHIPWRECK_RED_BOOST_FACTOR", "1.10")

    notes: list[str] = []
    boosted = _apply_activity_shipwreck_red_boost(dict(probs), 2545, {}, notes)
    expected_q6 = probs["q6"] * 1.10 / (1.0 + 0.10 * probs["q6"])
    assert abs(boosted["q6"] - expected_q6) < 1e-9
    assert notes == ["red_crate_shipwreck_red_boost:1.1"]


def test_kill_switch(monkeypatch):
    sd = load_reference_static_data()
    monkeypatch.setenv("AHMAD_DISABLE_ACTIVITY_SHIPWRECK_RED_BOOST", "1")
    probs, _ = _quality_probabilities(2511, sd)
    boosted = _apply_activity_shipwreck_red_boost(dict(probs), 2511, {}, [])
    assert boosted["q6"] == probs["q6"]
