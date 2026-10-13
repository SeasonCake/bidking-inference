from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for _path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_live_panel_server import _purple_gold_quality_summary  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AHMED_PUBLIC_Q4_AVG_ANTILOCK_NOTE,
    AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_DISABLE,
    AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_NOTE,
    _apply_ahmed_public_q4_reachable_tail_display,
    run_reference_engine,
)


def _public_q4_only_4541_r2_snapshot(
    *,
    hero: str = "ahmed",
    with_low_avg: bool = False,
) -> dict:
    """Reduced from HeroRefDiag 4541:1741282928131482 R2."""
    actions = [{"action_id": 100117, "result": 13}]
    avg_cells = {"q5": 5.400000095367432}
    if with_low_avg:
        actions.append({"action_id": 100110, "result": 1.1111111640930176})
        avg_cells["q1"] = 1.1111111640930176
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 4541,
                "phase": "bidding",
                "round": 2,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "q4_avg_cells", "value": 2.125},
                    ],
                },
            },
            "actions": {"results": actions},
        },
        "structured_ref_inputs": {
            "total_count": 38,
            "fixed_counts": {"q3": 13},
            "avg_cells": avg_cells,
        },
        "skill_reveals": [
            {"hero_id": 204, "skill_id": 100204, "result": 38},
            {
                "hero_id": 204,
                "skill_id": 1002041,
                "result": 5.400000095367432,
            },
        ],
    }
    if hero != "ahmed":
        snapshot.pop("skill_reveals")
    return snapshot


def _exact_q4_value_4501_r3_snapshot() -> dict:
    """Reduced pre-settlement facts from 4501:1758737621594031 R3."""
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4501,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 48,
            "fixed_counts": {"q3": 19, "q5": 8},
            "avg_cells": {
                "q1": 2.0,
                "q4": 4.333333492279053,
                "q5": 3.875,
            },
            "quality_values": {"q4": 102_279},
        },
    }


def _settled_4541_snapshot() -> dict:
    """Sanitized complete settlement used only as a display-switch no-op control."""
    return {
        "phase": "settled",
        "hero": "aisha",
        "map_id": 4541,
        "inventory_count": 56,
        "inventory_cells": 154,
        "settlement_incomplete": False,
        "settlement_parse_residual": 0,
        "final_quality_counts": "q1=3;q2=10;q3=15;q4=12;q5=8;q6=8",
        "final_quality_cells": "q1=3;q2=25;q3=43;q4=34;q5=27;q6=22",
        "layout_stage_rows": [{"布局估计": "71/164/170"}],
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 4541, "phase": "settled"},
            "truth": {
                "available": True,
                "complete": True,
                "partial": False,
                "captured_items": 56,
                "captured_cells": 154,
                "declared_items": None,
                "declared_cells": None,
                "total_items": 56,
                "total_cells": 154,
                "total_value": 2_163_815,
            },
        },
    }


def _assert_non_display_contract_equal(default: dict, rollback: dict) -> None:
    for key in (
        "status",
        "conservative",
        "balanced",
        "aggressive",
        "value_p25",
        "value_p50",
        "value_p75",
        "combo_count",
        "red_count_range",
        "red_cells_range",
        "red_value_range",
        "total_grid_range",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "evidence",
    ):
        assert default[key] == rollback[key], key


def test_4541_reachable_subthreshold_q4_tail_stays_in_display_support() -> None:
    result_object = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(),
        max_combos=50_000,
    )
    result = result_object.as_dict()
    q4_candidates = dict(result_object.quality_count_candidates["q4"])

    assert 0.0 < q4_candidates[16] < 0.005
    assert result["quality_count_ranges"]["q4"] == [8, 8, 16]
    assert result["quality_cells_ranges"]["q4"] == [17, 17, 34]
    assert result["quality_lock"]["q4"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["quality_count_candidates"]["q4"] == [
        {"value": 8, "prob": 1.0},
        {"value": 16, "prob": 0.0},
    ]
    assert AHMED_PUBLIC_Q4_AVG_ANTILOCK_NOTE in result["notes"]
    assert AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_NOTE in result["notes"]
    assert _purple_gold_quality_summary(result) == (
        "紫件 8/8/16 17/17/34格 · 金5件/27格"
    )


def test_4541_reachable_tail_display_has_independent_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(),
        max_combos=50_000,
    ).as_dict()
    monkeypatch.setenv(AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_DISABLE, "1")
    rollback = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(),
        max_combos=50_000,
    ).as_dict()

    assert default["quality_count_ranges"]["q4"] == [8, 8, 16]
    assert default["quality_cells_ranges"]["q4"] == [17, 17, 34]
    assert rollback["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert rollback["quality_cells_ranges"]["q4"] == [17, 17, 17]
    assert AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_NOTE not in rollback["notes"]
    _assert_non_display_contract_equal(default, rollback)


def test_unreachable_q4_tail_is_not_synthesized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _public_q4_only_4541_r2_snapshot(with_low_avg=True)
    default = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert default == rollback
    assert default["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert default["quality_cells_ranges"]["q4"] == [17, 17, 17]
    assert "q4" not in default["quality_count_candidates"]


def test_true_zero_probability_q4_candidate_is_not_display_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_DISABLE, "1")
    baseline = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(),
        max_combos=50_000,
    )
    monkeypatch.delenv(AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_DISABLE)
    zero_probability = replace(
        baseline,
        quality_count_candidates={
            **baseline.quality_count_candidates,
            "q4": ((8, 1.0), (16, 0.0)),
        },
        quality_count_cells_candidates={
            **baseline.quality_count_cells_candidates,
            "q4": ((8, 17, 1.0), (16, 34, 0.0)),
        },
    )

    assert (
        _apply_ahmed_public_q4_reachable_tail_display(zero_probability)
        == zero_probability
    )


@pytest.mark.parametrize("hero", ("aisha", "gabriela"))
def test_reachable_tail_display_switch_is_ahmed_only(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _public_q4_only_4541_r2_snapshot(hero=hero, with_low_avg=True)
    default = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert default == rollback


def test_exact_q4_value_lane_on_4501_keeps_probability_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _exact_q4_value_4501_r3_snapshot()
    default = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert default == rollback
    assert default["quality_count_ranges"]["q4"] == [9, 12, 15]
    assert default["quality_cells_ranges"]["q4"] == [39, 52, 65]


def test_settlement_path_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _settled_4541_snapshot()
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(AHMED_PUBLIC_Q4_REACHABLE_TAIL_DISPLAY_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
    assert default["status"] == "ok"
