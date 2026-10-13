"""Aisha R3 hard-arithmetic support must not become a soft-prior false lock."""

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

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_tk_overlay import AhmadTkOverlay  # noqa: E402
from detail_composition_view import detail_composition_view  # noqa: E402


PRODUCT_COMBO_CAPS = (8_000, 50_000)
EXPECTED_PHYSICAL_ARMS = {
    (4, 6, 12),
    (4, 12, 6),
    (4, 18, 0),
    (8, 6, 8),
    (8, 12, 2),
    (12, 6, 4),
    (16, 6, 0),
}
EXPECTED_COUNT_SUPPORT = {
    "q4": {4, 8, 12, 16},
    "q5": {6, 12, 18},
    "q6": {0, 2, 4, 6, 8, 12},
}


def _snapshot(*, source_pool: str = "structured") -> dict:
    snapshot = {
        "created_at": time.time(),
        "hero": "aisha",
        "map_id": 2409,
        "round": 3,
        "phase": "bidding",
        "session_id": "2409:sanitized-soft-prior-support",
        "structured_ref_inputs": {
            "source": "sanitized-hard-facts",
            "hero": "aisha",
            "counts": {"q1": 17, "q3": 12},
            "quality_cells": {"q1": 31, "q3": 34},
            "avg_cells": {"q1": 31 / 17, "q3": 34 / 12},
        },
        "skill_reveal_rows": [
            {
                "sort": 10,
                "hero_id": 103,
                "skill_id": 1001032,
                "revealed_items": 12,
                "revealed_items_detail": [],
            }
        ],
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2409,
                "round": 3,
                "phase": "bidding",
                "session_id": "2409:sanitized-soft-prior-support",
            },
            "constraints": {"public_info": {}},
            "actions": {"results": []},
        },
    }
    structured = snapshot["structured_ref_inputs"]
    if source_pool == "structured":
        structured["total_count"] = 51
        structured["avg_cells"].update({"q4": 39 / 12, "q5": 23 / 6})
    elif source_pool == "action_skill":
        actions = [
            {"sort": 11, "action_id": 100115, "result": 51},
            {"sort": 12, "action_id": 100112, "result": 39 / 12},
        ]
        snapshot["action_result_rows"] = copy.deepcopy(actions)
        snapshot["ui_contract"]["actions"]["results"] = actions
        snapshot["skill_reveal_rows"].append(
            {"sort": 13, "hero_id": 204, "skill_id": 1002041, "result": 23 / 6}
        )
    elif source_pool == "public_soft":
        structured["total_count"] = 51
        snapshot["ui_contract"]["constraints"]["public_info"] = {
            "public_numeric_facts": [
                {"semantic": "q4_avg_cells", "value": 39 / 12},
                {"semantic": "q5_avg_cells", "value": 23 / 6},
            ]
        }
    else:
        raise AssertionError(f"unknown source pool: {source_pool}")
    return snapshot


def _run(snapshot: dict, *, max_combos: int) -> engine.RefResult:
    return engine.run_reference_engine(snapshot, max_combos=max_combos)


def _wire_values(rows: list[dict], field: str) -> set[int]:
    return {int(row[field]) for row in rows}


@pytest.mark.parametrize("max_combos", PRODUCT_COMBO_CAPS)
def test_hard_arithmetic_support_is_probabilistic_at_product_caps(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    monkeypatch.delenv(
        engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_DISABLE,
        raising=False,
    )
    result = _run(_snapshot(), max_combos=max_combos).as_dict()

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 7
    assert "combo_cap_hit" not in result["notes"]
    assert engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_NOTE in result["notes"]
    assert result["quality_count_ranges"]["q4"][::2] == [4, 16]
    assert result["quality_count_ranges"]["q5"][::2] == [6, 18]
    assert result["quality_count_ranges"]["q6"][::2] == [0, 12]

    for key, expected_support in EXPECTED_COUNT_SUPPORT.items():
        assert result["quality_lock"][key] == {
            "strength": "probabilistic",
            "reason": "combo_multi_solution",
        }
        count_rows = result["quality_count_candidates"][key]
        joint_rows = result["quality_count_cells_candidates"][key]
        assert _wire_values(count_rows, "value") == expected_support
        assert _wire_values(joint_rows, "count") == expected_support
        assert all(float(row["prob"]) > 0.0 for row in count_rows)
        assert all(float(row["prob"]) > 0.0 for row in joint_rows)
        assert sum(float(row["prob"]) for row in count_rows) <= 1.000001
        assert sum(float(row["prob"]) for row in joint_rows) <= 1.000001


def test_physical_enumerator_keeps_all_catalog_reachable_correlation_arms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[set[tuple[int, int, int]]] = []
    original = engine._enumerate_prior_count_combos

    def _capture(*args: object, **kwargs: object) -> list[engine.RefCombo]:
        combos = original(*args, **kwargs)
        overrides = kwargs.get("count_value_overrides")
        if isinstance(overrides, dict) and set(overrides) == {
            "q1",
            "q3",
            "q4",
            "q5",
            "q6",
        }:
            captured.append(
                {
                    (combo.counts["q4"], combo.counts["q5"], combo.counts["q6"])
                    for combo in combos
                }
            )
        return combos

    monkeypatch.setattr(engine, "_enumerate_prior_count_combos", _capture)
    result = _run(_snapshot(), max_combos=8_000)

    assert result.combo_count == 7
    assert captured == [EXPECTED_PHYSICAL_ARMS]
    assert {(8, 12, 2), (12, 6, 4), (16, 6, 0)} <= captured[0]


@pytest.mark.parametrize("max_combos", PRODUCT_COMBO_CAPS)
def test_named_rollback_restores_current_0_3_2_legacy_singleton(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    monkeypatch.setenv(
        engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_DISABLE,
        "1",
    )
    result = _run(_snapshot(), max_combos=max_combos).as_dict()

    assert result["combo_count"] == 1
    assert engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_NOTE not in result["notes"]
    for key in ("q4", "q5", "q6"):
        assert len(set(result["quality_count_ranges"][key])) == 1
        assert result["quality_lock"][key] == {
            "strength": "exact",
            "reason": "derived_single_solution",
        }


def test_structured_and_action_skill_hard_sources_share_the_same_support() -> None:
    results = {
        source_pool: _run(_snapshot(source_pool=source_pool), max_combos=8_000).as_dict()
        for source_pool in ("structured", "action_skill")
    }

    for result in results.values():
        assert result["combo_count"] == 7
        assert engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_NOTE in result["notes"]
    for field in (
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_count_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
    ):
        assert results["structured"][field] == results["action_skill"][field]


def test_public_only_averages_remain_soft_and_byte_stable_under_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(source_pool="public_soft")
    default = _run(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(
        engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_DISABLE,
        "1",
    )
    rollback = _run(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
    assert default["combo_count"] == 1
    assert engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_NOTE not in default["notes"]
    assert {"q4", "q5"} <= set(default["evidence"]["soft_avg_cell_keys"])


def test_established_aisha_repair_lanes_are_byte_stable_under_new_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_aisha_4501_gold_total_avg_residual_0_3_1 import (
        _snapshot as gold_value_snapshot,
    )
    from test_sparse_prior_residual_antilock_hotfix2 import (
        _aisha_4541_r3_sparse_snapshot,
    )

    for snapshot in (_aisha_4541_r3_sparse_snapshot(), gold_value_snapshot()):
        default = _run(snapshot, max_combos=8_000).as_dict()
        monkeypatch.setenv(
            engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_DISABLE,
            "1",
        )
        rollback = _run(snapshot, max_combos=8_000).as_dict()
        monkeypatch.delenv(
            engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_DISABLE,
            raising=False,
        )

        assert default == rollback
        assert engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_NOTE not in default[
            "notes"
        ]

    legacy_snapshot = _aisha_4541_r3_sparse_snapshot()
    monkeypatch.setenv(engine.AISHA_Q5_Q6_RESIDUAL_ANTILOCK_DISABLE, "1")
    old_lane_rollback = _run(legacy_snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(
        engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_DISABLE,
        "1",
    )
    both_disabled = _run(legacy_snapshot, max_combos=8_000).as_dict()

    assert old_lane_rollback == both_disabled
    assert old_lane_rollback["combo_count"] == 1


@pytest.mark.parametrize(
    "mutation",
    (
        "other_hero",
        "other_phase",
        "missing_total",
        "hard_total_grid",
        "direct_q6",
        "missing_low_tier",
        "stronger_q4",
        "fixed_sum_conflict",
    ),
)
def test_narrow_gate_negative_controls(mutation: str) -> None:
    snapshot = _snapshot()
    structured = snapshot["structured_ref_inputs"]
    if mutation == "other_hero":
        snapshot["hero"] = "ahmed"
        snapshot["ui_contract"]["context"]["hero"] = "ahmed"
        structured["hero"] = "ahmed"
        snapshot["skill_reveal_rows"] = []
    elif mutation == "other_phase":
        snapshot["phase"] = "settled"
        snapshot["ui_contract"]["context"]["phase"] = "settled"
    elif mutation == "missing_total":
        structured.pop("total_count")
    elif mutation == "hard_total_grid":
        structured["total_cells"] = 120
    elif mutation == "direct_q6":
        structured["counts"]["q6"] = 0
    elif mutation == "missing_low_tier":
        structured["counts"].pop("q1")
        structured["quality_cells"].pop("q1")
        structured["avg_cells"].pop("q1")
    elif mutation == "stronger_q4":
        structured["quality_cells"]["q4"] = 39
    elif mutation == "fixed_sum_conflict":
        structured["counts"] = {"q1": 40, "q3": 20}
    else:
        raise AssertionError(mutation)

    result = _run(snapshot, max_combos=8_000)

    assert engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_NOTE not in result.notes


def test_panel_detail_and_tk_keep_default_probabilistic_and_rollback_exact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    default_summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "default" / "latest_snapshot.json",
    )
    assert default_summary["quality_composition"]["status"] == "ready"
    default_view = detail_composition_view(default_summary)
    assert default_view.status == "ready"
    for key in ("q4", "q5", "q6"):
        quality = default_view.quality(key)
        assert quality.status == "ready"
        assert quality.lock_strength == "probabilistic"
        assert AhmadTkOverlay._joint_quality_is_exact(quality) is False
        assert AhmadTkOverlay._detail_quality_state_text(
            quality,
            candidate_index=0,
        ) == "最可能"
        hover = AhmadTkOverlay._lock_triple(
            "8/8/8",
            "件数",
            quality=quality,
            dimension="count",
        )
        assert "概率估计" in hover
        assert "唯一锁定" not in hover

    monkeypatch.setenv(
        engine.AISHA_HARD_AVG_PHYSICAL_SUPPORT_ANTILOCK_DISABLE,
        "1",
    )
    rollback_summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "rollback" / "latest_snapshot.json",
    )
    assert rollback_summary["quality_composition"]["status"] == "ready"
    rollback_view = detail_composition_view(rollback_summary)
    for key in ("q4", "q5"):
        quality = rollback_view.quality(key)
        assert quality.status == "exact"
        assert AhmadTkOverlay._joint_quality_is_exact(quality) is True
        assert AhmadTkOverlay._detail_quality_state_text(quality) == "锁定"
