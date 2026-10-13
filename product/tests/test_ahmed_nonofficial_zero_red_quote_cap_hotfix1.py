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
    AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_DISABLE,
    AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_NOTE_PREFIX,
    AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_NOTE_PREFIX,
    AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402


UNKNOWN_SOURCE = {
    "qualification": "postupdate_unknown_warehouse_source",
    "official_distribution_eligible": False,
    "quote_policy": "advisory_only_no_clamp",
}
PLAYER_SOURCE = {
    "qualification": "player_custom_warehouse",
    "official_distribution_eligible": False,
    "quote_policy": "advisory_only_no_clamp",
}


def _snapshot(
    *,
    hero: str = "ahmed",
    phase: str = "bidding",
    warehouse_source: dict | None = UNKNOWN_SOURCE,
) -> dict:
    """Reduced bidding evidence from HeroRefDiag 4509:1770351167898215 R5."""
    context = {
        "hero": hero,
        "map_id": 4509,
        "phase": phase,
        "round": 5,
    }
    snapshot = {
        "hero": hero,
        "map_id": 4509,
        "phase": phase,
        "round": 5,
        "ui_contract": {
            "context": context,
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
    if warehouse_source is not None:
        source = deepcopy(warehouse_source)
        snapshot["warehouse_source"] = source
        context["warehouse_source"] = deepcopy(source)
    return snapshot


def _quotes(result: dict) -> list[int | None]:
    return [
        result.get("conservative"),
        result.get("balanced"),
        result.get("aggressive"),
    ]


def _cap_notes(result: dict) -> list[str]:
    return [
        str(note)
        for note in result.get("notes") or ()
        if str(note).startswith(AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_NOTE_PREFIX)
    ]


def _risk_floor_notes(result: dict) -> list[str]:
    return [
        str(note)
        for note in result.get("notes") or ()
        if str(note).startswith(
            AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_NOTE_PREFIX
        )
    ]


def test_4509_unknown_source_keeps_conditional_zero_red_tiers_as_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = run_reference_engine(_snapshot(), max_combos=8_000).as_dict()
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE,
        "1",
    )
    rollback = run_reference_engine(_snapshot(), max_combos=8_000).as_dict()

    assert _quotes(default) == [1_380_342, 1_483_690, 1_587_039]
    assert _quotes(rollback) == [822_836, 860_226, 897_615]
    assert default["quality_count_candidates"]["q5"] == rollback[
        "quality_count_candidates"
    ]["q5"] == [
        {"value": 5, "prob": 0.9445},
        {"value": 10, "prob": 0.0555},
    ]
    assert default["quality_count_candidates"]["q6"] == rollback[
        "quality_count_candidates"
    ]["q6"] == [
        {"value": 5, "prob": 0.9445},
        {"value": 0, "prob": 0.0555},
    ]
    for key in (
        "value_p25",
        "value_p50",
        "value_p75",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "red_count_range",
        "red_cells_range",
        "red_value_range",
        "total_grid_range",
    ):
        assert default[key] == rollback[key]
    assert default["risk_floor"]["probability"] == pytest.approx(
        0.055491,
        abs=0.000001,
    )
    assert default["risk_floor"]["selected_branch"] == {
        "q5_count": 10,
        "q6_count": 0,
        "probability": pytest.approx(0.055491, abs=0.000001),
        "eligible": True,
    }
    assert default["risk_floor"]["tail_quotes"] == {
        "conservative": 822_836,
        "balanced": 860_226,
        "aggressive": 897_615,
    }
    assert rollback["risk_floor"] == {}
    assert _risk_floor_notes(default) == [
        "ahmed_nonofficial_zero_red_tail_risk_floor:"
        "postupdate_unknown_warehouse_source:p0=0.0555:"
        "1380342/1483690/1587039->822836/860226/897615"
    ]
    assert _cap_notes(default) == []
    assert _cap_notes(rollback) == [
        "ahmed_nonofficial_zero_red_quote_cap:"
        "postupdate_unknown_warehouse_source:p0=0.0555:"
        "1380342/1483690/1587039->822836/860226/897615"
    ]


def test_4509_product_and_tk_keep_ordinary_quotes_without_relabelling_player() -> None:
    snapshot_path = ROOT / "scratch" / "4509_nonofficial_quote_cap_test.json"
    summary = summarize_snapshot(_snapshot(), snapshot_path=snapshot_path)
    tk_row = _summary_diagnostic_row(
        summary,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )

    assert (
        summary["reference"]["decision_range"]
        == "1,380,342 / 1,483,690 / 1,587,039"
    )
    assert summary["ahmed_ref"]["risk_floor"]["tail_quotes"] == {
        "conservative": 822_836,
        "balanced": 860_226,
        "aggressive": 897_615,
    }
    assert summary["red"]["risk_reference"] == "ref_v0 估计"
    assert [flag["label"] for flag in summary["flags"][:2]] == [
        "仓来源待确认",
        "外援 ref_v0",
    ]
    assert all(flag["label"] != "玩家自定义仓" for flag in summary["flags"])
    assert (
        tk_row["reference"]["decision_range"]
        == "1,380,342 / 1,483,690 / 1,587,039"
    )
    assert tk_row["red"]["risk_reference"] == summary["red"]["risk_reference"]
    assert tk_row["flags"][:2] == summary["flags"][:2]


def test_4509_confirmed_player_source_keeps_distinct_ui_classification() -> None:
    summary = summarize_snapshot(
        _snapshot(warehouse_source=PLAYER_SOURCE),
        snapshot_path=ROOT / "scratch" / "4509_player_quote_cap_test.json",
    )

    assert (
        summary["reference"]["decision_range"]
        == "1,380,342 / 1,483,690 / 1,587,039"
    )
    assert (
        summary["ahmed_ref"]["risk_floor"]["warehouse_qualification"]
        == "player_custom_warehouse"
    )
    assert [flag["label"] for flag in summary["flags"][:2]] == [
        "玩家自定义仓",
        "外援 ref_v0",
    ]


@pytest.mark.parametrize(
    "warehouse_source",
    [
        None,
        {
            "qualification": "official_distribution",
            "official_distribution_eligible": True,
            "quote_policy": "normal",
        },
        {
            "qualification": "postupdate_unknown_warehouse_source",
            "official_distribution_eligible": True,
            "quote_policy": "advisory_only_no_clamp",
        },
        {
            "qualification": "postupdate_unknown_warehouse_source",
            "official_distribution_eligible": False,
            "quote_policy": "normal",
        },
    ],
)
def test_4509_official_or_incomplete_source_contract_is_noop(
    warehouse_source: dict | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(warehouse_source=warehouse_source)
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
    assert _quotes(default) == [1_380_342, 1_483_690, 1_587_039]


@pytest.mark.parametrize("evidence_kind", ["count", "cells", "value", "physical"])
def test_4509_direct_q6_evidence_is_noop(
    evidence_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    structured = snapshot["structured_ref_inputs"]
    if evidence_kind == "count":
        structured["counts"]["q6"] = 0
    elif evidence_kind == "cells":
        structured["quality_cells"] = {"q6": 0}
    elif evidence_kind == "value":
        structured["quality_values"]["q6"] = 0
    else:
        snapshot["public_info_rows"] = [
            {
                "info_id": 200028,
                "revealed_items_detail": [
                    {
                        "quality": 6,
                        "item_id": 1046003,
                        "runtime_id": 45090001,
                        "cells": 1,
                        "shape_key": "11",
                        "row": 1,
                        "col": 4,
                        "width": 1,
                        "height": 1,
                        "value": 54_300,
                    }
                ],
            }
        ]

    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
    assert _cap_notes(default) == []


@pytest.mark.parametrize("hero", ["aisha", "gabriela", "ethan", "maria"])
def test_non_ahmed_heroes_are_noop(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(hero=hero)
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback


def test_aisha_product_dual_pass_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(hero="aisha")
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
    snapshot["ui_contract"]["actions"] = {"results": [action_row]}
    snapshot_path = ROOT / "scratch" / "aisha_dual_pass_quote_cap_test.json"
    default = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_DISABLE, "1")
    rollback = summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    performance = default["diagnostics"]["performance"]
    assert performance["ref_engine_skill_pass_ms"] is not None
    assert performance["ref_engine_item_pass_ms"] is not None
    for key in ("ahmed_ref", "reference", "red"):
        assert default[key] == rollback[key]
    assert [flag["label"] for flag in default["flags"]] == [
        flag["label"] for flag in rollback["flags"]
    ]
    assert all(flag["label"] != "来源风险限价" for flag in default["flags"])


def test_4509_product_and_audit_combo_caps_agree() -> None:
    product = run_reference_engine(_snapshot(), max_combos=8_000).as_dict()
    audit = run_reference_engine(_snapshot(), max_combos=50_000).as_dict()

    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "value_p25",
        "value_p50",
        "value_p75",
        "combo_count",
        "quality_count_candidates",
        "quality_count_cells_candidates",
        "red_count_range",
        "red_cells_range",
        "red_value_range",
        "total_grid_range",
    ):
        assert product[key] == audit[key]
