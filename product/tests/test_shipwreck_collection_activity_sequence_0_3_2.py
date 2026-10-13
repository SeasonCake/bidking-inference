"""2026-08-13 shipwreck collection activity: reused ActivityMap cohort contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
import build_match_records as records  # noqa: E402
import public_stats as public_stats  # noqa: E402
import sample_dashboard as private_dashboard  # noqa: E402


PROBS = {"q1": 0.30, "q2": 0.15, "q3": 0.20, "q4": 0.15, "q5": 0.126, "q6": 0.074}


def _snapshot(map_id: int, *, timestamp: int, phase: str = "bidding") -> dict:
    context = {"map_id": map_id, "phase": phase}
    return {"map_id": map_id, "created_at": timestamp, "phase": phase, "ui_contract": {"context": context}}


def _apply(map_id: int, *, timestamp: int, phase: str = "bidding") -> tuple[dict[str, float], list[str]]:
    notes: list[str] = []
    result = engine._apply_activity_shipwreck_red_boost(
        dict(PROBS), map_id, _snapshot(map_id, timestamp=timestamp, phase=phase), notes, phase=phase
    )
    return result, notes


@pytest.mark.parametrize(
    "map_id",
    (2531, 2533, 2537, 2540, 4531, 4533, 4537, 4540),
)
def test_reused_shipwreck_activity_block_is_bounded_to_the_two_confirmed_arms(map_id: int) -> None:
    assert engine._is_collection_activity_shipwreck_map(map_id) is True


@pytest.mark.parametrize("map_id", (2501, 2511, 2521, 2541, 2550, 2551, 2599, 3501, 4501, 4511, 4521, 4550, 4551, 4599, 2401, 5601))
def test_regular_known_history_hero_and_other_unknown_maps_are_not_collection_activity(map_id: int) -> None:
    assert engine._is_collection_activity_shipwreck_map(map_id) is False


def test_collection_activity_boost_is_sample_calibrated_only_in_announced_live_window() -> None:
    start = engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_START_EPOCH
    assert engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_CONTRACT_VERSION == (
        "2026-08-13-v3-sample-calibrated"
    )
    assert engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_FACTOR == 1.11
    assert engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_START == "2026-08-13 10:00:00+08:00"
    assert engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_END == "2026-08-20 10:00:00+08:00"
    assert start == 1_786_586_400
    assert engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_END_EPOCH == 1_787_191_200
    result, notes = _apply(4533, timestamp=start)
    expected_q6 = PROBS["q6"] * 1.11 / (1.0 + 0.11 * PROBS["q6"])
    assert result["q6"] == pytest.approx(expected_q6)
    assert result["q6"] == pytest.approx(0.08148, abs=0.00001)
    assert sum(result.values()) == pytest.approx(1.0)
    assert notes == ["shipwreck_collection_activity_red_boost:1.11"]

    assert _apply(
        4537,
        timestamp=engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_END_EPOCH - 1,
    )[0]["q6"] == pytest.approx(expected_q6)
    for timestamp in (
        start - 1,
        engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_END_EPOCH,
        1_799_999_999,
    ):
        assert _apply(4533, timestamp=timestamp) == (PROBS, [])
    assert _apply(4533, timestamp=start, phase="settled") == (PROBS, [])


def test_conflicting_raw_map_owners_fail_closed_for_collection_boost() -> None:
    start = engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_START_EPOCH
    snapshot = _snapshot(4533, timestamp=start)
    snapshot["ui_contract"]["context"]["map_id"] = 4534
    notes: list[str] = []
    result = engine._apply_activity_shipwreck_red_boost(
        dict(PROBS),
        4533,
        snapshot,
        notes,
        phase="bidding",
        current_activity_raw_map_decision=engine._current_activity_raw_map_decision(snapshot),
    )
    assert result == PROBS
    assert notes == []


def test_collection_activity_has_an_independent_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_DISABLE, "1")
    assert _apply(
        4537,
        timestamp=engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_START_EPOCH,
    ) == (PROBS, [])


@pytest.mark.parametrize(
    ("map_id", "expected_name", "auction_mode"),
    (
        (2533, "军用舰艇保险库(活动沉船)", "open"),
        (4533, "军用舰艇保险库(活动沉船)", "sealed"),
        (4537, "皇家御用货舱(活动沉船)", "sealed"),
    ),
)
def test_public_and_private_server_metadata_share_human_activity_shipwreck_name(
    map_id: int, expected_name: str, auction_mode: str
) -> None:
    meta = records._load_map_meta()
    row = meta[map_id]
    assert row["name"] == expected_name
    assert row["map_kind"] == "activity"
    assert row["activity_status"] == "historical"
    assert row["auction_mode"] == auction_mode
    assert str(map_id) not in row["name"]

    sample = {
        "map_id": map_id,
        "final_quality_counts": "q1=2;q6=1",
        "final_quality_values": "q1=100;q6=90000",
        "inventory_count": 3,
        "inventory_cells": 6,
        "known_value_sum": 90100,
        "client_ts": float(engine.SHIPWRECK_COLLECTION_ACTIVITY_RED_BOOST_START_EPOCH),
    }
    public_row = public_stats.aggregate_public([sample])["maps"][0]
    private_row = private_dashboard._row_for_client(sample, map_meta=meta, include_details=False)
    assert public_row["name"] == expected_name
    assert private_row is not None and private_row["name"] == expected_name
