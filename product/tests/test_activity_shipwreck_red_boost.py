"""Regression gate for the shipwreck activity red boost (release-critical special tune).

⭐ RE-CLASSIFIED 2026-07-06: activity is the map NUMBER X511-X520, not the match date (census: those
ids are activity-exclusive, red 12.11% vs normal X501-X510 7.33%). Factor recalibrated 1.5 -> 1.74
(the boost renormalizes, so 1.74 hits the 12.16% q6 share; the old 1.5 came from a time-window pool
diluted with normal games). See test_activity_shipwreck_map_number_gate + memory activity-shipwreck-red-boost.
"""
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import (  # noqa: E402
    ACTIVITY_SHIPWRECK_RED_BOOST_FACTOR,
    ACTIVITY_SHIPWRECK_RED_BOOST_START,
    _apply_activity_shipwreck_red_boost,
)

PROBS = {"q1": 0.30, "q2": 0.15, "q3": 0.20, "q4": 0.15, "q5": 0.126, "q6": 0.074}


def _ts(s: str) -> float:
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


IN_WINDOW = {"created_at": _ts("2026-07-03 20:00:00")}
BEFORE_WINDOW = {"created_at": _ts("2026-07-01 20:00:00")}


def test_factor_recalibrated_1_74():
    # 1.65x rate ratio (12.11%/7.33%) -> factor 1.74 after renormalize; re-run pooled truth on change.
    assert ACTIVITY_SHIPWRECK_RED_BOOST_FACTOR == 1.74
    assert ACTIVITY_SHIPWRECK_RED_BOOST_START == "2026-07-02 12:00:00"


def test_in_window_shipwreck_boosts_and_renormalizes(monkeypatch):
    monkeypatch.delenv("AHMAD_DISABLE_ACTIVITY_SHIPWRECK_RED_BOOST", raising=False)
    notes: list[str] = []
    out = _apply_activity_shipwreck_red_boost(dict(PROBS), 2511, IN_WINDOW, notes)
    assert abs(sum(out.values()) - 1.0) < 1e-9
    # boosted then renormalized: q6 ratio = f / (1 + (f-1)*q6)
    f = ACTIVITY_SHIPWRECK_RED_BOOST_FACTOR
    expect = PROBS["q6"] * f / (1.0 + (f - 1.0) * PROBS["q6"])
    assert abs(out["q6"] - expect) < 1e-9
    assert out["q6"] > PROBS["q6"]
    assert notes and "activity_shipwreck_red_boost:1.74" in notes[0]


def test_outside_window_auto_expires(monkeypatch):
    # A date OUTSIDE the (loose) window auto-expires the boost even for an activity map id.
    monkeypatch.delenv("AHMAD_FORCE_ACTIVITY_SHIPWRECK_RED_BOOST", raising=False)
    notes: list[str] = []
    assert _apply_activity_shipwreck_red_boost(dict(PROBS), 2511, BEFORE_WINDOW, notes) == PROBS
    assert notes == []


def test_normal_wreckage_is_noop_even_in_window():
    # ⭐ the mis-classification fix: normal X501-X510 played DURING the window must NOT boost.
    notes: list[str] = []
    assert _apply_activity_shipwreck_red_boost(dict(PROBS), 2501, IN_WINDOW, notes) == PROBS
    assert _apply_activity_shipwreck_red_boost(dict(PROBS), 2510, IN_WINDOW, notes) == PROBS
    assert notes == []


def test_non_shipwreck_map_is_noop_even_in_window():
    notes: list[str] = []
    assert _apply_activity_shipwreck_red_boost(dict(PROBS), 2407, IN_WINDOW, notes) == PROBS
    assert _apply_activity_shipwreck_red_boost(dict(PROBS), 5604, IN_WINDOW, notes) == PROBS
    assert notes == []


def test_no_timestamp_boosts_by_map_number(monkeypatch):
    # ⭐ NEW contract: an activity map id (X511-X520) WITHOUT a date DOES boost (id is sufficient).
    monkeypatch.delenv("AHMAD_FORCE_ACTIVITY_SHIPWRECK_RED_BOOST", raising=False)
    notes: list[str] = []
    out = _apply_activity_shipwreck_red_boost(dict(PROBS), 2511, {}, notes)
    assert out["q6"] > PROBS["q6"]
    assert notes and "activity_shipwreck_red_boost" in notes[0]


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("AHMAD_DISABLE_ACTIVITY_SHIPWRECK_RED_BOOST", "1")
    notes: list[str] = []
    assert _apply_activity_shipwreck_red_boost(dict(PROBS), 2511, IN_WINDOW, notes) == PROBS
