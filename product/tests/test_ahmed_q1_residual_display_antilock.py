from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
if str(AHMAD_TOOLS) not in sys.path:
    sys.path.insert(0, str(AHMAD_TOOLS))

from ahmad_ref_engine import (  # noqa: E402
    AHMED_Q1_RESIDUAL_DISPLAY_ANTILOCK_DISABLE,
    AHMED_Q1_RESIDUAL_DISPLAY_ANTILOCK_NOTE,
    run_reference_engine,
)
from ahmad_live_panel_server import (  # noqa: E402
    _low_quality_count_probability_hover,
    _quality_uncertainty_summary,
)


def _snapshot(*, hero: str = "ahmed") -> dict:
    """Reduced from HeroRefDiag 2549:1741282916626220 R3 bidding."""
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 2549,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 47,
            "fixed_counts": {"q3": 9, "q5": 4},
            "quality_cells": {"q5": 10.0},
            "avg_cells": {
                "q1": 1.4285714626312256,
                "q4": 3.153846263885498,
                "q5": 2.5,
            },
        },
    }


def test_2549_q1_avg_residual_does_not_render_zero_red_as_impossible() -> None:
    result = run_reference_engine(_snapshot(), max_combos=50_000).as_dict()

    assert result["combo_count"] == 4
    assert result["quality_count_candidates"]["q1"] == [
        {"value": 14, "prob": 0.9657},
        {"value": 21, "prob": 0.0338},
    ]
    assert result["quality_count_candidates"]["q6"] == [
        {"value": 7, "prob": 0.9657},
        {"value": 0, "prob": 0.0338},
    ]
    assert result["quality_count_ranges"]["q1"] == [14, 14, 21]
    assert result["quality_cells_ranges"]["q1"] == [20, 20, 30]
    assert result["quality_count_ranges"]["q6"] == [0, 7, 7]
    assert result["red_count_range"] == [0, 7, 7]
    assert result["red_cells_range"][0] == 0
    assert result["red_value_range"][0] == 0
    assert result["quality_lock"]["q1"]["strength"] == "probabilistic"
    assert result["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert AHMED_Q1_RESIDUAL_DISPLAY_ANTILOCK_NOTE in result["notes"]


def test_2549_low_quality_hover_keeps_the_same_rare_q1_arm_visible() -> None:
    result = run_reference_engine(_snapshot(), max_combos=50_000).as_dict()

    assert _quality_uncertainty_summary(result) == "未锁 白绿14/14/21"
    assert _low_quality_count_probability_hover(result) == (
        "白绿件候选：14件 96.6% · 21件 3.4%；蓝件候选：9件 100%"
    )


def test_2549_q1_residual_display_antilock_has_exact_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired = run_reference_engine(_snapshot(), max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_Q1_RESIDUAL_DISPLAY_ANTILOCK_DISABLE, "1")
    legacy = run_reference_engine(_snapshot(), max_combos=50_000).as_dict()

    assert legacy["quality_count_ranges"]["q1"] == [14, 14, 14]
    assert legacy["quality_count_ranges"]["q6"] == [7, 7, 7]
    assert legacy["red_count_range"] == [7, 7, 7]
    assert legacy["red_cells_range"] == [14, 22, 54]
    assert AHMED_Q1_RESIDUAL_DISPLAY_ANTILOCK_NOTE not in legacy["notes"]
    assert [
        repaired["conservative"],
        repaired["balanced"],
        repaired["aggressive"],
    ] == [
        legacy["conservative"],
        legacy["balanced"],
        legacy["aggressive"],
    ]


@pytest.mark.parametrize("hero", ["aisha", "gabriela"])
def test_2549_q1_residual_display_antilock_is_ahmed_only(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unchanged = run_reference_engine(_snapshot(hero=hero), max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_Q1_RESIDUAL_DISPLAY_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(_snapshot(hero=hero), max_combos=50_000).as_dict()

    assert unchanged == rollback
