from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE,
    AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_NOTE_PREFIX,
    AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_NOTE_PREFIX,
    run_reference_engine,
)


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
OFFICIAL_SOURCE = {
    "qualification": "official_distribution",
    "official_distribution_eligible": True,
    "quote_policy": "normal",
}


def _snapshot_2405(
    *,
    hero: str = "ahmed",
    source: dict | None = UNKNOWN_SOURCE,
) -> dict:
    """Reduced hard-average state from data(20) 2405:1770351257931037 R4."""
    context = {
        "hero": hero,
        "map_id": 2405,
        "phase": "bidding",
        "round": 4,
    }
    if source is not None:
        context["warehouse_source"] = copy.deepcopy(source)
        context["warehouse_source_qualification"] = source["qualification"]
    return {
        "ui_contract": {"context": context},
        "structured_ref_inputs": {
            "total_count": 41,
            "fixed_counts": {"q3": 13},
            "min_counts": {"q1": 1, "q3": 13, "q6": 1},
            "avg_cells": {
                "q1": 19 / 13,
                "q3": 37 / 13,
                "q4": 21 / 4,
                "q5": 33 / 5,
            },
            "quality_value_floors": {
                "q1": 360,
                "q3": 2_565,
                "q6": 295_500,
            },
        },
    }


def _snapshot_2401(*, source: dict | None = UNKNOWN_SOURCE) -> dict:
    """Reduced hard-average state from data(20) 2401:1770351257653463 R3."""
    context = {
        "hero": "ahmed",
        "map_id": 2401,
        "phase": "bidding",
        "round": 3,
    }
    if source is not None:
        context["warehouse_source"] = copy.deepcopy(source)
        context["warehouse_source_qualification"] = source["qualification"]
    return {
        "ui_contract": {"context": context},
        "structured_ref_inputs": {
            "total_count": 44,
            "fixed_counts": {"q1": 8, "q3": 16},
            "min_counts": {"q1": 8, "q3": 16, "q4": 3},
            "avg_cells": {
                "q1": 17 / 8,
                "q4": 50 / 18,
                "q5": 4.0,
            },
            "quality_cells": {"q1": 17},
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return run_reference_engine(snapshot, max_combos=max_combos).as_dict()


def _safety_notes(result: dict) -> list[str]:
    return [
        str(note)
        for note in result["notes"]
        if str(note).startswith(AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_NOTE_PREFIX)
    ]


def _risk_ceiling_notes(result: dict) -> list[str]:
    return [
        str(note)
        for note in result["notes"]
        if str(note).startswith(
            AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_NOTE_PREFIX
        )
    ]


def test_2405_restores_six_red_arm_and_aggressive_quote_with_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2405()
    default = _run(snapshot)

    monkeypatch.setenv(AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE, "1")
    rollback = _run(snapshot)

    assert rollback["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert rollback["quality_count_ranges"]["q6"] == [2, 2, 2]
    assert rollback["red_count_range"] == [2, 2, 2]
    assert rollback["quality_lock"]["q6"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert (
        rollback["conservative"],
        rollback["balanced"],
        rollback["aggressive"],
    ) == (693_457, 753_712, 813_967)

    assert default["quality_count_ranges"]["q4"] == [4, 8, 8]
    assert default["quality_count_ranges"]["q6"] == [2, 2, 6]
    assert default["red_count_range"] == [2, 2, 6]
    assert default["quality_cells_ranges"]["q6"] == default["red_cells_range"]
    assert default["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "partial_reveal_min",
    }
    assert default["quality_count_candidates"]["q6"] == [
        {"value": 2, "prob": 0.9859},
        {"value": 6, "prob": 0.0083},
        {"value": 1, "prob": 0.0058},
    ]
    assert (
        default["conservative"],
        default["balanced"],
        default["aggressive"],
    ) == (693_457, 753_712, 813_967)
    assert default["risk_ceiling"]["value"] == 1_426_755
    assert default["risk_ceiling"]["baseline_aggressive"] == 813_967
    selected_branch = default["risk_ceiling"]["selected_branch"]
    assert selected_branch["quality"] == "q4"
    assert selected_branch["count"] == 4
    assert selected_branch["cells"] == 21
    assert selected_branch["probability"] == pytest.approx(0.014077)
    assert selected_branch["eligible"] is True
    assert selected_branch["aggressive"] == 1_426_755
    assert _safety_notes(default) == [
        f"{AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_NOTE_PREFIX}:display_support"
    ]
    assert _risk_ceiling_notes(default)[-1].endswith(
        "813967->1426755:q4=4@0.0141,q4=8@0.9859"
    )
    assert _safety_notes(rollback) == []
    assert rollback["risk_ceiling"] == {}

    # Source-risk projection does not rewrite the raw posterior value quantiles or evidence.
    for key in ("value_p25", "value_p50", "value_p75", "combo_count", "evidence"):
        assert default[key] == rollback[key]


def test_2401_uses_low_value_q4_arm_for_reference_without_erasing_upside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2401()
    default = _run(snapshot)

    monkeypatch.setenv(AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE, "1")
    rollback = _run(snapshot)

    assert (
        rollback["conservative"],
        rollback["balanced"],
        rollback["aggressive"],
    ) == (785_649, 1_004_982, 1_178_225)
    assert (
        default["conservative"],
        default["balanced"],
        default["aggressive"],
    ) == (213_500, 281_985, 1_178_225)
    assert default["risk_ceiling"]["value"] == 1_246_372
    assert default["risk_ceiling"]["baseline_aggressive"] == 1_178_225
    assert default["quality_count_ranges"]["q4"] == [9, 9, 18]
    assert rollback["quality_count_ranges"]["q4"] == [9, 9, 9]
    # The q4-arm repair does not inflate an already-wide red triplet.
    assert default["red_count_range"] == rollback["red_count_range"] == [1, 3, 6]
    assert _safety_notes(default)[-1].endswith(
        "785649/1004982/1178225->213500/281985/1178225"
    )
    assert _risk_ceiling_notes(default)[-1].endswith(
        "1178225->1246372:q4=9@0.9219,q4=18@0.0781"
    )


@pytest.mark.parametrize(
    "source",
    [
        None,
        OFFICIAL_SOURCE,
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
def test_missing_official_or_malformed_source_contract_is_noop(
    source: dict | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2405(source=source)
    default = _run(snapshot)
    monkeypatch.setenv(AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE, "1")
    rollback = _run(snapshot)
    assert default == rollback


@pytest.mark.parametrize("hero", ["aisha", "gabriela", "ethan", "maria"])
def test_other_heroes_are_noop(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2405(hero=hero)
    default = _run(snapshot)
    monkeypatch.setenv(AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE, "1")
    rollback = _run(snapshot)
    assert default == rollback


def test_exact_q4_count_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = _snapshot_2405()
    snapshot["structured_ref_inputs"]["fixed_counts"]["q4"] = 4
    default = _run(snapshot)
    monkeypatch.setenv(AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE, "1")
    rollback = _run(snapshot)
    assert default == rollback


def test_settlement_truth_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = _snapshot_2405()
    snapshot["ui_contract"]["context"]["phase"] = "settled"
    snapshot["structured_ref_inputs"].update(
        fixed_counts={"q1": 13, "q3": 13, "q4": 4, "q5": 5, "q6": 6},
        quality_cells={"q1": 19, "q3": 37, "q4": 21, "q5": 33, "q6": 18},
    )
    default = _run(snapshot)
    monkeypatch.setenv(AHMED_NONOFFICIAL_Q4_AVG_ARM_SAFETY_DISABLE, "1")
    rollback = _run(snapshot)

    assert default == rollback
    assert default["quality_count_ranges"]["q6"] == [6, 6, 6]
    assert default["total_grid_range"] == [128, 128, 128]
    assert _safety_notes(default) == []


def test_product_summary_distinguishes_unknown_and_player_sources(
    tmp_path: Path,
) -> None:
    unknown = summarize_snapshot(
        _snapshot_2405(),
        snapshot_path=tmp_path / "unknown.json",
    )
    player = summarize_snapshot(
        _snapshot_2405(source=PLAYER_SOURCE),
        snapshot_path=tmp_path / "player.json",
    )

    assert unknown["reference"]["decision_range"] == ("693,457 / 753,712 / 813,967")
    assert unknown["red"]["risk_reference"] == "ref_v0 估计"
    assert [flag["label"] for flag in unknown["flags"][:2]] == [
        "仓来源待确认",
        "外援 ref_v0",
    ]
    assert [flag["label"] for flag in player["flags"][:2]] == [
        "玩家自定义仓",
        "外援 ref_v0",
    ]
    assert AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_NOTE_PREFIX in (
        unknown["evidence"]["diagnostics"]
    )
    assert AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_NOTE_PREFIX in (
        player["evidence"]["diagnostics"]
    )


def test_product_and_audit_combo_caps_agree() -> None:
    product = _run(_snapshot_2405(), max_combos=8_000)
    audit = _run(_snapshot_2405(), max_combos=50_000)
    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "value_p25",
        "value_p50",
        "value_p75",
        "combo_count",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_count_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "red_count_range",
        "red_cells_range",
        "red_value_range",
        "total_grid_range",
    ):
        assert product[key] == audit[key]
