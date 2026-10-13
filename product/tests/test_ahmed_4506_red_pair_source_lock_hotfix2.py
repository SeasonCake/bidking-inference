"""Ahmed 4506 red count/cells display-source regression.

Sanitized from HeroRefDiag-20260727-092038-4506_9000000000000029.  The old
display path treated a collapsed q4 percentile triplet as an exact source lock,
despite q4 retaining 8/12/16 candidates.  It then rewrote raw q6=0/4/8 to
4/4/4 while leaving q6 cells at 0/13/31.
"""

from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import (  # noqa: E402
    _candidate_count_support_state,
    _red_display_ranges,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE,
)


SOURCE_LOCK_ROLLBACK = "BIDKING_DISABLE_RED_PAIRED_SOURCE_LOCK_GUARD"
CANDIDATE_GUARD_ROLLBACK = "BIDKING_DISABLE_RED_PAIRED_CANDIDATE_GUARD"


def _ref_result_4506() -> dict:
    return {
        "quality_count_ranges": {
            "q1": [13, 13, 13],
            "q3": [20, 20, 20],
            "q4": [12, 12, 12],
            "q5": [7, 7, 7],
            "q6": [0, 4, 8],
        },
        "red_count_range": [0, 4, 8],
        "red_cells_range": [0, 13, 31],
        "quality_count_candidates": {
            "q4": [
                {"value": 12, "prob": 0.8827},
                {"value": 16, "prob": 0.0670},
                {"value": 8, "prob": 0.0498},
            ],
            "q6": [
                {"value": 4, "prob": 0.8827},
                {"value": 0, "prob": 0.0670},
                {"value": 8, "prob": 0.0452},
            ],
        },
        "quality_lock": {
            "q1": {"strength": "exact", "reason": "derived_single_solution"},
            "q3": {"strength": "exact", "reason": "exact_lock"},
            "q4": {"strength": "probabilistic", "reason": "combo_multi_solution"},
            "q5": {"strength": "exact", "reason": "derived_single_solution"},
            "q6": {"strength": "probabilistic", "reason": "combo_multi_solution"},
        },
        "evidence": {"total_count": 56, "count_sums": {}},
    }


def _snapshot_4506() -> dict:
    source = {
        "qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    }
    return {
        "created_at": time.time(),
        "hero": "ahmed",
        "map_id": 4506,
        "round": 3,
        "phase": "bidding",
        "session_id": "4506:9000000000000029",
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": False,
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4506,
                "round": 3,
                "phase": "bidding",
                "session_id": "4506:9000000000000029",
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": source["qualification"],
                "official_distribution_eligible": False,
            },
            "constraints": {},
            "baseline": {"decision": {}, "posterior": {}},
            "source": {},
        },
        "structured_ref_inputs": {
            "total_count": 56,
            "fixed_counts": {"q3": 20},
            "avg_cells": {
                "q1": 16 / 13,
                "q4": 51 / 12,
                "q5": 30 / 7,
            },
        },
    }


def test_4506_probabilistic_q4_cannot_become_paired_total_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = _ref_result_4506()
    assert _red_display_ranges(ref) == ([0, 4, 8], [0, 13, 31])

    monkeypatch.setenv(SOURCE_LOCK_ROLLBACK, "1")
    assert _red_display_ranges(ref) == ([0, 4, 8], [0, 13, 31])


def test_exact_source_locks_keep_existing_inverse_pairing() -> None:
    ref = _ref_result_4506()
    ref["quality_count_candidates"].pop("q4")
    ref["quality_lock"]["q4"] = {
        "strength": "exact",
        "reason": "exact_lock",
    }
    ref["red_count_range"] = [4, 4, 4]
    ref["red_cells_range"] = [12, 13, 14]

    assert _red_display_ranges(ref) == ([4, 4, 4], [12, 13, 14])


def test_legacy_payload_without_source_metadata_keeps_policy_order() -> None:
    ref = _ref_result_4506()
    ref.pop("quality_lock")
    ref.pop("quality_count_candidates")

    assert _red_display_ranges(ref) == ([0, 4, 8], [0, 13, 31])


@pytest.mark.parametrize(
    "case",
    [
        "missing_q1",
        "missing_q3",
        "missing_q4",
        "wrong_lock_field",
        "wrong_q1",
        "wrong_q3",
        "wrong_q4",
        "missing_strength_q1",
        "missing_strength_q3",
        "missing_strength_q4",
    ],
)
def test_partial_or_malformed_lock_metadata_fails_closed(case: str) -> None:
    ref = _ref_result_4506()
    for quality in ("q1", "q3", "q4"):
        ref["quality_lock"][quality] = {
            "strength": "exact",
            "reason": "test_exact",
        }
    ref["quality_count_candidates"]["q4"] = [{"value": 12, "prob": 1.0}]

    if case == "wrong_lock_field":
        ref["quality_lock"] = []
    elif case.startswith("missing_strength_"):
        quality = case.removeprefix("missing_strength_")
        ref["quality_lock"][quality] = {"reason": "missing_strength"}
    else:
        operation, quality = case.split("_", 1)
        if operation == "missing":
            ref["quality_lock"].pop(quality)
        else:
            ref["quality_lock"][quality] = "exact"

    assert _red_display_ranges(ref) == ([0, 4, 8], [0, 13, 31])


@pytest.mark.parametrize(
    ("case", "expected_state"),
    [
        ("field_wrong_type", "invalid"),
        ("target_wrong_type", "invalid"),
        ("empty_target", "invalid"),
        ("row_wrong_type", "invalid"),
        ("row_missing_value", "invalid"),
        ("value_unparseable", "invalid"),
        ("value_fractional", "invalid"),
        ("value_negative", "invalid"),
        ("value_escape", "escape"),
        ("value_safe", "safe"),
    ],
)
def test_candidate_metadata_uses_safe_escape_invalid_states(
    case: str,
    expected_state: str,
) -> None:
    ref = _ref_result_4506()
    for quality in ("q1", "q3", "q4"):
        ref["quality_lock"][quality] = {
            "strength": "exact",
            "reason": "test_exact",
        }
    ref["quality_count_candidates"]["q4"] = [{"value": 12, "prob": 1.0}]

    if case == "field_wrong_type":
        ref["quality_count_candidates"] = []
    elif case == "target_wrong_type":
        ref["quality_count_candidates"]["q4"] = {}
    elif case == "empty_target":
        ref["quality_count_candidates"]["q4"] = []
    elif case == "row_wrong_type":
        ref["quality_count_candidates"]["q4"] = [None]
    elif case == "row_missing_value":
        ref["quality_count_candidates"]["q4"] = [{"prob": 1.0}]
    elif case == "value_unparseable":
        ref["quality_count_candidates"]["q4"] = [{"value": "not-a-count"}]
    elif case == "value_fractional":
        ref["quality_count_candidates"]["q4"] = [{"value": 12.5}]
    elif case == "value_negative":
        ref["quality_count_candidates"]["q4"] = [{"value": -1}]
    elif case == "value_escape":
        ref["quality_count_candidates"]["q4"] = [{"value": 16}]

    assert _candidate_count_support_state(ref, "q4") == expected_state
    assert _red_display_ranges(ref) == ([0, 4, 8], [0, 13, 31])


def test_malformed_q5_candidate_metadata_fails_closed() -> None:
    ref = _ref_result_4506()
    for quality in ("q1", "q3", "q4"):
        ref["quality_lock"][quality] = {
            "strength": "exact",
            "reason": "test_exact",
        }
    ref["quality_count_candidates"]["q4"] = [{"value": 12, "prob": 1.0}]
    ref["quality_count_candidates"]["q5"] = {"value": 7}

    assert _red_display_ranges(ref) == ([0, 4, 8], [0, 13, 31])


def test_partial_lock_and_invalid_candidate_have_narrow_rollbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial_lock = _ref_result_4506()
    partial_lock["quality_lock"].pop("q1")
    assert _red_display_ranges(partial_lock) == ([0, 4, 8], [0, 13, 31])
    monkeypatch.setenv(SOURCE_LOCK_ROLLBACK, "1")
    assert _red_display_ranges(partial_lock) == ([0, 4, 8], [0, 13, 31])
    monkeypatch.delenv(SOURCE_LOCK_ROLLBACK)

    invalid_q5 = _ref_result_4506()
    for quality in ("q1", "q3", "q4"):
        invalid_q5["quality_lock"][quality] = {
            "strength": "exact",
            "reason": "test_exact",
        }
    invalid_q5["quality_count_candidates"]["q4"] = [{"value": 12}]
    invalid_q5["quality_count_candidates"]["q5"] = [{"value": "bad"}]
    assert _red_display_ranges(invalid_q5) == ([0, 4, 8], [0, 13, 31])
    monkeypatch.setenv(CANDIDATE_GUARD_ROLLBACK, "1")
    assert _red_display_ranges(invalid_q5) == ([0, 4, 8], [0, 13, 31])


def test_4506_historical_display_env_is_now_a_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_4506()
    monkeypatch.setenv(AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE, "1")
    default = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    monkeypatch.setenv(SOURCE_LOCK_ROLLBACK, "1")
    rollback = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert default["red"] == rollback["red"]
    assert default["red"]["count_range"] == "4 / 8 / 12"
    assert default["red"]["count_display"] == "保 4 / 均 8 / 激 12件"
    assert default["reference"] == rollback["reference"]
    assert default["ahmed_ref"] == rollback["ahmed_ref"]


def test_4506_current_q4_arm_safety_is_noop_for_source_lock_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_4506()
    default = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    monkeypatch.setenv(SOURCE_LOCK_ROLLBACK, "1")
    rollback = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert default["red"] == rollback["red"]
    assert default["reference"] == rollback["reference"]
