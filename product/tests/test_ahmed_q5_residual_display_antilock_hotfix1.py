from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for _path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AHMED_Q5_RESIDUAL_DISPLAY_ANTILOCK_DISABLE,
    AHMED_Q5_RESIDUAL_DISPLAY_ANTILOCK_NOTE,
    run_reference_engine,
)


def _snapshot(*, hero: str = "ahmed", phase: str = "bidding") -> dict:
    """Reduced from HeroRefDiag 4509:1770351167898215 R5 pre-settlement fields."""
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 4509,
                "phase": phase,
                "round": 5,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 56,
            "counts": {"q1": 23, "q3": 11},
            "avg_cells": {
                "q1": 2.04347825050354,
                "q3": 2.2727272510528564,
                "q4": 4.833333492279053,
                "q5": 8.199999809265137,
            },
            "quality_values": {"q4": 97_082},
        },
    }


def test_4509_q5_residual_zero_red_arm_is_visible_without_repricing() -> None:
    result = run_reference_engine(_snapshot(), max_combos=8_000).as_dict()

    assert result["combo_count"] == 5
    assert result["quality_count_candidates"]["q5"] == [
        {"value": 5, "prob": 0.9445},
        {"value": 10, "prob": 0.0555},
    ]
    assert result["quality_count_candidates"]["q6"] == [
        {"value": 5, "prob": 0.9445},
        {"value": 0, "prob": 0.0555},
    ]
    assert result["quality_count_ranges"]["q5"] == [5, 5, 5]
    assert result["quality_cells_ranges"]["q5"] == [41, 41, 41]
    assert result["quality_count_ranges"]["q6"] == [0, 5, 5]
    assert result["quality_cells_ranges"]["q6"] == [0, 16, 23]
    assert result["red_count_range"] == [0, 5, 5]
    assert result["red_cells_range"] == [0, 16, 39]
    assert result["red_value_range"][0] == 0
    assert result["quality_lock"]["q5"]["strength"] == "probabilistic"
    assert result["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert result["total_grid_range"] == [181, 187, 210]
    assert [
        result["conservative"],
        result["balanced"],
        result["aggressive"],
    ] == [1_380_342, 1_483_690, 1_587_039]
    assert AHMED_Q5_RESIDUAL_DISPLAY_ANTILOCK_NOTE in result["notes"]


def test_4509_q5_residual_display_antilock_has_exact_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired = run_reference_engine(_snapshot(), max_combos=8_000).as_dict()
    monkeypatch.setenv(AHMED_Q5_RESIDUAL_DISPLAY_ANTILOCK_DISABLE, "1")
    legacy = run_reference_engine(_snapshot(), max_combos=8_000).as_dict()

    assert legacy["quality_count_ranges"]["q5"] == [5, 5, 5]
    assert legacy["quality_cells_ranges"]["q5"] == [41, 41, 41]
    assert legacy["quality_count_ranges"]["q6"] == [5, 5, 5]
    assert legacy["quality_cells_ranges"]["q6"] == [10, 16, 23]
    assert legacy["red_count_range"] == [5, 5, 5]
    assert legacy["red_cells_range"] == [10, 16, 39]
    assert AHMED_Q5_RESIDUAL_DISPLAY_ANTILOCK_NOTE not in legacy["notes"]
    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "total_grid_range",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
    ):
        assert repaired[key] == legacy[key]


def test_4509_q5_residual_arm_reaches_api_summary_with_joint_order() -> None:
    summary = summarize_snapshot(
        _snapshot(),
        snapshot_path=ROOT / "scratch" / "4509_q5_residual_display_test.json",
    )

    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["red_count_range"] == [0, 5, 5]
    assert summary["ahmed_ref"]["quality_count_ranges"]["q5"] == [5, 5, 5]
    # q5's alternate arm remains in the probability hover; the existing candidate guard prevents
    # the compact layer from turning its collapsed weighted triplet back into a false red lock.
    assert summary["red"]["count_range"] == "0 / 5 / 5"
    assert summary["red"]["cells_range"] == "0 / 16 / 39"
    assert summary["red"]["value_range"] == "0 / 1,079,236 / 1,275,686"
    assert summary["red"]["quality_count_summary"] == "紫12件/58格 · 金5件/41格"
    assert summary["red"]["count_probability_hover"] == (
        "红件候选：5件 94.5% · 0件 5.5%"
    )


@pytest.mark.parametrize("hero", ["aisha", "gabriela", "ethan"])
def test_4509_q5_residual_display_antilock_is_ahmed_only(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = run_reference_engine(
        _snapshot(hero=hero), max_combos=8_000
    ).as_dict()
    monkeypatch.setenv(AHMED_Q5_RESIDUAL_DISPLAY_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(
        _snapshot(hero=hero), max_combos=8_000
    ).as_dict()

    assert default == rollback


def test_4509_q5_residual_display_antilock_does_not_touch_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = run_reference_engine(
        _snapshot(phase="settled"), max_combos=8_000
    ).as_dict()
    monkeypatch.setenv(AHMED_Q5_RESIDUAL_DISPLAY_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(
        _snapshot(phase="settled"), max_combos=8_000
    ).as_dict()

    assert default == rollback


def test_4509_q5_residual_display_antilock_respects_direct_q6_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = deepcopy(_snapshot())
    snapshot["structured_ref_inputs"]["counts"]["q6"] = 0

    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(AHMED_Q5_RESIDUAL_DISPLAY_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
