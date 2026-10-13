from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_ref_engine import (  # noqa: E402
    AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_DISABLE,
    AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_NOTE,
    AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_NOTE,
    AISHA_Q5_Q6_RESIDUAL_ANTILOCK_DISABLE,
    AISHA_Q5_Q6_RESIDUAL_ANTILOCK_NOTE,
    COMPLETE_GT5_JOINT_SUPPORT_DISABLE,
    run_reference_engine,
)


PRODUCT_MAX_COMBOS = 8_000


@pytest.fixture(autouse=True)
def _historical_sparse_residual_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep these hotfix2 assertions on their named pre-joint-support wire."""
    monkeypatch.setenv(COMPLETE_GT5_JOINT_SUPPORT_DISABLE, "1")


def _aisha_4541_r3_sparse_snapshot() -> dict:
    """Sanitized evidence core from 4541:1741283071537164 R3."""
    return {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 4541,
                "phase": "bidding",
                "round": 3,
            },
        },
        "structured_ref_inputs": {
            "total_count": 56,
            "fixed_counts": {"q1": 13, "q3": 15},
            "avg_cells": {
                "q1": 28 / 13,
                "q3": 43 / 15,
                "q4": 34 / 12,
                "q5": 27 / 8,
            },
            "quality_cells": {"q1": 28, "q3": 43},
        },
    }


def _aisha_4541_r3_empty_sparse_snapshot() -> dict:
    """Sanitized evidence core from 4541:1741283121560580 R3.

    The hard q4/q5 means imply q4=24 and q5=5.  Together with the exact low-tier
    counts, total=75 leaves q6=3, which sits in the independent tight prior's 1..3 gap.
    """
    return {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 4541,
                "phase": "bidding",
                "round": 3,
            },
        },
        "structured_ref_inputs": {
            "total_count": 75,
            "fixed_counts": {"q1": 22, "q3": 21},
            "avg_cells": {
                "q1": 39 / 22,
                "q3": 60 / 21,
                "q4": 89 / 24,
                "q5": 8 / 5,
            },
            "quality_cells": {"q1": 39, "q3": 60},
        },
    }


def _ahmed_2541_r2_screenshot_snapshot() -> dict:
    """Only facts visible in the 2541 R2 screenshot; final q4/q6 truth is unknown."""
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2541,
                "phase": "bidding",
                "round": 2,
            },
            "actions": {
                "results": [
                    {"action_id": 100110, "result": 31 / 16},
                    {"action_id": 100117, "result": 22},
                ],
            },
        },
        "structured_ref_inputs": {
            "total_count": 57,
            "fixed_counts": {"q3": 22},
            "avg_cells": {"q1": 31 / 16, "q5": 38 / 6},
        },
        "skill_reveals": [
            {"hero_id": 204, "skill_id": 1002041, "result": 38 / 6},
        ],
    }


def _ahmed_2505_r2_true_zero_red_snapshot() -> dict:
    """Sanitized real negative; settlement q6 truth is zero."""
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2505,
                "phase": "bidding",
                "round": 2,
            },
            "actions": {
                "results": [
                    {"action_id": 100117, "result": 12},
                    {"action_id": 100110, "result": 2.0322580337524414},
                ],
            },
        },
        "structured_ref_inputs": {
            "total_count": 66,
            "fixed_counts": {"q3": 12},
            "avg_cells": {
                "q1": 2.0322580337524414,
                "q5": 3.222222328186035,
            },
        },
        "skill_reveals": [
            {
                "hero_id": 204,
                "skill_id": 1002041,
                "result": 3.222222328186035,
            },
        ],
    }


def _ahmed_2601_r2_soft_q4_snapshot() -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2601,
                "phase": "bidding",
                "round": 2,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "q4_avg_cells", "value": 3.473684310913086},
                    ],
                },
            },
            "actions": {"results": [{"action_id": 100117, "result": 13}]},
        },
        "structured_ref_inputs": {
            "total_count": 48,
            "fixed_counts": {"q3": 13},
            "avg_cells": {"q5": 4.769230842590332},
        },
        "skill_reveals": [
            {
                "hero_id": 204,
                "skill_id": 1002041,
                "result": 4.769230842590332,
            },
        ],
    }


def _run(snapshot: dict) -> dict:
    return run_reference_engine(snapshot, max_combos=PRODUCT_MAX_COMBOS).as_dict()


def test_aisha_4541_q5_widen_restores_exact_total_q6_residual() -> None:
    result = _run(_aisha_4541_r3_sparse_snapshot())

    assert result["combo_count"] == 2
    assert result["quality_count_ranges"]["q1"] == [13, 13, 13]
    assert result["quality_count_ranges"]["q3"] == [15, 15, 15]
    assert result["quality_count_ranges"]["q4"] == [12, 12, 12]
    assert result["quality_count_ranges"]["q5"] == [8, 8, 8]
    assert result["quality_count_ranges"]["q6"] == [8, 8, 8]
    assert result["quality_cells_ranges"]["q5"] == [27, 27, 27]
    assert AISHA_Q5_Q6_RESIDUAL_ANTILOCK_NOTE in result["notes"]
    assert "avg_multiple_false_lock_widen:q5" in result["notes"]
    assert not any(
        row["value"] == 18
        for row in result["quality_count_candidates"].get("q4", [])
    )


def test_aisha_4541_q5_q6_residual_has_named_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AISHA_Q5_Q6_RESIDUAL_ANTILOCK_DISABLE, "1")
    legacy = _run(_aisha_4541_r3_sparse_snapshot())

    assert legacy["combo_count"] == 1
    assert legacy["quality_count_ranges"]["q5"] == [16, 16, 16]
    assert legacy["quality_cells_ranges"]["q5"] == [54, 54, 54]
    assert legacy["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert legacy["quality_cells_ranges"]["q6"] == [0, 0, 0]
    assert AISHA_Q5_Q6_RESIDUAL_ANTILOCK_NOTE not in legacy["notes"]


def test_aisha_4541_empty_prior_restores_hard_mean_residual() -> None:
    result = _run(_aisha_4541_r3_empty_sparse_snapshot())

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 1
    assert result["quality_count_ranges"] == {
        "q1": [22, 22, 22],
        "q3": [21, 21, 21],
        "q4": [24, 24, 24],
        "q5": [5, 5, 5],
        "q6": [3, 3, 3],
    }
    assert result["quality_cells_ranges"]["q4"] == [89, 89, 89]
    assert result["quality_cells_ranges"]["q5"] == [8, 8, 8]
    assert all(result[key] is not None for key in ("conservative", "balanced", "aggressive"))
    assert AISHA_Q5_Q6_RESIDUAL_ANTILOCK_NOTE in result["notes"]


def test_aisha_4541_empty_prior_has_named_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AISHA_Q5_Q6_RESIDUAL_ANTILOCK_DISABLE, "1")
    legacy = _run(_aisha_4541_r3_empty_sparse_snapshot())

    assert legacy["status"] == "no_reachable_combo"
    assert legacy["combo_count"] == 0
    assert all(legacy[key] is None for key in ("conservative", "balanced", "aggressive"))
    assert AISHA_Q5_Q6_RESIDUAL_ANTILOCK_NOTE not in legacy["notes"]


def test_ahmed_2541_q1_q5_shape_does_not_false_lock_purple_and_zero_red() -> None:
    result = _run(_ahmed_2541_r2_screenshot_snapshot())

    assert result["combo_count"] == 2
    assert result["quality_count_ranges"]["q1"] == [16, 16, 16]
    assert result["quality_count_ranges"]["q3"] == [22, 22, 22]
    assert result["quality_count_ranges"]["q4"] == [12, 12, 13]
    assert result["quality_count_ranges"]["q5"] == [6, 6, 6]
    assert result["quality_count_ranges"]["q6"] == [0, 1, 1]
    assert result["quality_lock"]["q4"]["strength"] == "probabilistic"
    assert result["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_NOTE in result["notes"]


def test_ahmed_2541_q1_q5_low_red_gap_has_named_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_DISABLE, "1")
    legacy = _run(_ahmed_2541_r2_screenshot_snapshot())

    assert legacy["combo_count"] == 1
    assert legacy["quality_count_ranges"]["q4"] == [13, 13, 13]
    assert legacy["quality_cells_ranges"]["q4"] == [30, 31, 32]
    assert legacy["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert legacy["quality_cells_ranges"]["q6"] == [0, 0, 0]
    assert [
        legacy["conservative"],
        legacy["balanced"],
        legacy["aggressive"],
    ] == [457_371, 480_116, 502_862]
    assert AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_NOTE not in legacy["notes"]


def test_ahmed_2505_true_zero_red_is_unchanged_by_new_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = _run(_ahmed_2505_r2_true_zero_red_snapshot())
    monkeypatch.setenv(AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_DISABLE, "1")
    rollback = _run(_ahmed_2505_r2_true_zero_red_snapshot())

    assert default == rollback
    assert default["combo_count"] == 2
    assert default["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_NOTE not in default["notes"]


def test_ahmed_2601_soft_q4_repair_is_not_reprocessed_by_new_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = _run(_ahmed_2601_r2_soft_q4_snapshot())
    monkeypatch.setenv(AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_DISABLE, "1")
    rollback = _run(_ahmed_2601_r2_soft_q4_snapshot())

    assert default == rollback
    assert AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_NOTE in default["notes"]
    assert AHMED_Q1_Q5_LOW_RED_GAP_ANTILOCK_NOTE not in default["notes"]
