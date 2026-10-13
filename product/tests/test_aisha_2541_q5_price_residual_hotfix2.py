from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for _path in (
    ROOT / "scripts",
    ROOT / "src",
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_ref_engine as engine  # noqa: E402


def _snapshot_2541_r4(*, include_q5_value: bool) -> dict:
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "total_count": 40,
        "total_cells": None,
        "avg_cells": {"q4": 3.875, "q5": 6.5},
        "counts": {"q3": 9, "q4": 8},
        "quality_cells": {"q3": 19, "q4": 31},
        "avg_values": {},
        "quality_values": {"q5": 100_320} if include_q5_value else {},
        "quality_value_floors": {},
        "count_sums": {},
        "split_avg_cells": {},
        "split_counts": {"white": 5, "green": 8},
        "split_quality_cells": {"white": 7, "green": 13},
        "field_updates": [],
        "minimap_items": [],
    }
    return {
        "session_id": "2541:9000000000000025",
        "hero": "aisha",
        "map_id": 2541,
        "model_map_id": 2501,
        "round": 4,
        "phase": "bidding",
        "structured_ref_inputs": structured,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2541,
                "model_map_id": 2501,
                "round": 4,
                "phase": "bidding",
            },
            "constraints": {"public_info": {}},
            "actions": {"results": []},
        },
    }


@pytest.fixture
def q5_catalog_100320(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deterministic form of the real catalog result: 100,320 at 6.5 cells has one joint state,
    # two gold items occupying thirteen cells.  Tests do not depend on a gitignored Item.txt.
    pairs = ((50_000, 6), (50_320, 7))
    rows = ((1, 50_000, 6), (2, 50_320, 7))
    monkeypatch.setattr(engine, "_validated_q5_catalog", lambda: (pairs, rows, "ok"))
    original = engine._validated_map_quality_catalog

    def map_catalog(map_id: int, quality: int):
        if map_id == 2501 and quality == 5:
            return pairs, rows, "ok", "physical-category-v1:q5-100320-test"
        return original(map_id, quality)

    monkeypatch.setattr(engine, "_validated_map_quality_catalog", map_catalog)
    monkeypatch.setattr(
        engine,
        "_exact_item_values_by_table_quality",
        lambda: {5: frozenset({50_000, 50_320})},
    )


def _result(snapshot: dict) -> dict:
    return engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()


def _scheduler_snapshot() -> dict:
    snapshot = _snapshot_2541_r4(include_q5_value=True)
    skill_row = {
        "sort": 25,
        "skill_id": 1_001_031,
        "hero_id": 103,
        "revealed_items": 8,
        "revealed_items_detail": [{"quality": 4, "shape_code": 22}],
    }
    action_row = {
        "sort": 27,
        "action_id": 100125,
        "tool": "极品估价",
        "result": 100_320,
        "result_field": 12,
        "revealed_items": 0,
    }
    snapshot["skill_reveal_rows"] = [skill_row]
    snapshot["skill_reveals"] = [skill_row]
    snapshot["action_result_rows"] = [action_row]
    snapshot["ui_contract"]["actions"]["results"] = [action_row]
    return snapshot


def test_2541_r4_gold_price_restores_exact_red_residual(
    monkeypatch: pytest.MonkeyPatch,
    q5_catalog_100320: None,
) -> None:
    monkeypatch.delenv(engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_DISABLE, raising=False)

    result = _result(_snapshot_2541_r4(include_q5_value=True))

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 1
    assert result["quality_count_ranges"]["q5"] == [2, 2, 2]
    assert result["quality_cells_ranges"]["q5"] == [13, 13, 13]
    assert result["quality_count_ranges"]["q6"] == [8, 8, 8]
    assert result["quality_cells_ranges"]["q6"][0] <= 26 <= result["quality_cells_ranges"]["q6"][2]
    assert result["evidence"]["total_grid_target"] == 115
    assert result["total_grid_range"] == [115, 115, 115]
    assert all(result[key] is not None for key in ("conservative", "balanced", "aggressive"))
    assert engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_NOTE in result["notes"]


def test_2541_r4_named_rollback_reproduces_post_gold_blank(
    monkeypatch: pytest.MonkeyPatch,
    q5_catalog_100320: None,
) -> None:
    monkeypatch.setenv(engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_DISABLE, "1")

    result = _result(_snapshot_2541_r4(include_q5_value=True))

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert all(result[key] is None for key in ("conservative", "balanced", "aggressive"))
    assert engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_NOTE not in result["notes"]


def test_2541_r4_actual_scheduler_and_ui_keep_data_after_gold_price(
    monkeypatch: pytest.MonkeyPatch,
    q5_catalog_100320: None,
) -> None:
    from ahmad_live_panel_server import (  # noqa: E402
        _aisha_dual_pass_ref_result,
        _purple_gold_quality_summary,
        _quality_probability_hover,
        _quality_probability_summary,
    )

    monkeypatch.delenv(engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_DISABLE, raising=False)
    snapshot = _scheduler_snapshot()
    result, timing = _aisha_dual_pass_ref_result(
        snapshot,
        round_no=4,
        public_info=snapshot["ui_contract"]["constraints"]["public_info"],
    )

    assert result["status"] == "count_prior"
    assert timing["skill"] is not None
    assert timing["item"] is not None
    assert "aisha_quote_pass:item" in result["notes"]
    assert _purple_gold_quality_summary(result) == "紫8件/31格 · 金2件/13格"
    assert result["quality_count_ranges"]["q6"] == [8, 8, 8]
    assert result["quality_cells_ranges"]["q6"] == [19, 32, 32]
    # The red count remains exact, but the red footprint is still a soft range.  Do not
    # resurrect the modeled 8件/32格 row as a public 100% physical fact (5604 contract).
    assert _quality_probability_summary(result) == "金2件13格100%"
    hover = _quality_probability_hover(result)
    assert "金品：2件/13格 100%" in hover
    assert "红品" not in hover


def test_2541_r4_before_gold_price_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    q5_catalog_100320: None,
) -> None:
    snapshot = _snapshot_2541_r4(include_q5_value=False)
    monkeypatch.delenv(engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_DISABLE, raising=False)
    default = _result(snapshot)
    monkeypatch.setenv(engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_DISABLE, "1")
    rollback = _result(snapshot)

    for field in (
        "status",
        "combo_count",
        "conservative",
        "balanced",
        "aggressive",
        "quality_count_ranges",
        "quality_cells_ranges",
        "total_grid_range",
    ):
        assert default[field] == rollback[field]
    assert default["quality_count_ranges"]["q5"] == [4, 6, 8]
    assert default["quality_count_ranges"]["q6"] == [2, 4, 6]


def test_residual_retry_does_not_override_explicit_red_conflict(
    monkeypatch: pytest.MonkeyPatch,
    q5_catalog_100320: None,
) -> None:
    snapshot = _snapshot_2541_r4(include_q5_value=True)
    snapshot["structured_ref_inputs"]["counts"]["q6"] = 7
    monkeypatch.delenv(engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_DISABLE, raising=False)

    result = _result(snapshot)

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert engine.AISHA_Q6_EXACT_RESIDUAL_ANTILOCK_NOTE not in result["notes"]
