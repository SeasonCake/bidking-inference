"""Aisha 4501 q5 exact-value/public-average support regression for 0.3.1.

Sanitized from HeroRefDiag-20260728-194853-4501_9000000000000036.
At bidding R4 the public q5 average is soft, while the exact q5 total value
and catalog keep several physical count/cell arms reachable.  The old scorer
compared that average with a provisional pre-reconciliation grid and drove the
real 7/14 arm to roughly 7.7e-13 posterior mass.
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

from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_DISABLE,
    AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE,
    _exact_q5_count_cells_candidates,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


SESSION_ID = "4501:9000000000000036"
Q4_AVG_CELLS = 3.4285714626312256
Q5_TOTAL_VALUE = 154_624
Q5_PUBLIC_AVG_CELLS = 2.0


def _warehouse_source(
    qualification: str = "postupdate_unknown_warehouse_source",
) -> dict:
    official = qualification == "official_distribution"
    return {
        "qualification": qualification,
        "official_distribution_eligible": official,
        "quote_policy": "default" if official else "advisory_only_no_clamp",
    }


def _snapshot(
    *,
    hero: str = "aisha",
    map_id: int = 4501,
    phase: str = "bidding",
    qualification: str = "postupdate_unknown_warehouse_source",
) -> dict:
    source = _warehouse_source(qualification)
    actions = [
        {"action_id": 100103, "result": 122},
        {"action_id": 100125, "result": Q5_TOTAL_VALUE},
        {"action_id": 100112, "result": Q4_AVG_CELLS},
        {"action_id": 100115, "result": 45},
    ]
    public_facts = [
        {
            "info_id": 200015,
            "semantic": "q5_avg_cells",
            "kind": "avg_cells",
            "quality": 5,
            "value": Q5_PUBLIC_AVG_CELLS,
            "display_value": "2.00",
        }
    ]
    skill_rows = [
        {
            "sort": 25,
            "skill_id": skill_id,
            "hero_id": 103,
            "revealed_items": count,
            "revealed_items_detail": [],
        }
        for skill_id, count in (
            (1001034, 7),
            (1001033, 11),
            (1001032, 9),
            (1001031, 7),
        )
    ]
    return {
        "created_at": time.time(),
        "hero": hero,
        "map_id": map_id,
        "round": 4,
        "phase": phase,
        "session_id": SESSION_ID,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
        "action_result_rows": copy.deepcopy(actions),
        "skill_reveal_rows": skill_rows if hero == "aisha" else [],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": hero,
            "total_count": 45,
            "total_cells": 122,
            "avg_cells": {"q4": Q4_AVG_CELLS},
            "counts": {"q3": 9, "q4": 7},
            "quality_cells": {"q3": 16, "q4": 24},
            "quality_values": {"q5": Q5_TOTAL_VALUE},
            "split_counts": {"white": 7, "green": 11},
            "split_quality_cells": {"white": 12, "green": 18},
        },
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": map_id,
                "round": 4,
                "phase": phase,
                "session_id": SESSION_ID,
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": qualification,
                "official_distribution_eligible": source[
                    "official_distribution_eligible"
                ],
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": copy.deepcopy(public_facts),
                }
            },
            "actions": {"results": copy.deepcopy(actions)},
            "source": {"created_at": time.time()},
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=max_combos,
    ).as_dict()


def _snapshot_with_q5_total(total_value: int) -> dict:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["quality_values"]["q5"] = total_value
    for rows in (
        snapshot["action_result_rows"],
        snapshot["ui_contract"]["actions"]["results"],
    ):
        for row in rows:
            if row["action_id"] == 100125:
                row["result"] = total_value
    return snapshot


def _joint(result: dict, quality: str) -> list[tuple[int, int, float]]:
    return [
        (int(row["count"]), int(row["cells"]), float(row["prob"]))
        for row in result["quality_count_cells_candidates"][quality]
    ]


def _disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_DISABLE, "1")


def test_exact_catalog_keeps_low_unit_value_seven_gold_arm() -> None:
    status, states = _exact_q5_count_cells_candidates(
        Q5_TOTAL_VALUE,
        Q5_PUBLIC_AVG_CELLS,
        45,
    )

    assert status == "ok"
    assert states == (
        (5, 10),
        (6, 12),
        (7, 14),
        (8, 16),
        (9, 18),
        (10, 20),
    )


@pytest.mark.parametrize(
    ("total_value", "expected_states"),
    [
        (99_999, ((4, 8), (5, 10))),
        (100_000, ((4, 8), (5, 10), (6, 12))),
        (100_002, ((4, 8), (5, 10), (6, 12))),
    ],
)
def test_exact_value_only_boundary_catalog_states(
    total_value: int,
    expected_states: tuple[tuple[int, int], ...],
) -> None:
    status, states = _exact_q5_count_cells_candidates(
        total_value,
        Q5_PUBLIC_AVG_CELLS,
        45,
    )

    assert status == "ok"
    assert states == expected_states


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
@pytest.mark.parametrize(
    ("total_value", "expected_states"),
    [
        (99_999, ((4, 8), (5, 10))),
        (100_000, ((4, 8), (5, 10), (6, 12))),
        (100_002, ((4, 8), (5, 10), (6, 12))),
    ],
)
def test_exact_value_only_boundary_arms_are_disclosed_without_hard_lock(
    total_value: int,
    expected_states: tuple[tuple[int, int], ...],
    max_combos: int,
) -> None:
    result = _run(
        _snapshot_with_q5_total(total_value),
        max_combos=max_combos,
    )
    joint = {
        (count, cells): probability
        for count, cells, probability in _joint(result, "q5")
    }

    assert set(expected_states) <= joint.keys()
    assert all(joint[state] > 0.0 for state in expected_states)
    assert sum(joint.values()) <= 1.0
    assert result["evidence"]["soft_avg_cell_keys"] == ["q5"]
    assert result["evidence"]["fixed_counts"].get("q5") is None
    assert result["quality_lock"]["q5"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE in result["notes"]


def test_value_only_boundary_rollback_keeps_historical_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_with_q5_total(100_000)
    default = _run(copy.deepcopy(snapshot))
    _disable(monkeypatch)
    rollback = _run(copy.deepcopy(snapshot))

    assert (5, 10) in {
        (count, cells) for count, cells, _probability in _joint(default, "q5")
    }
    assert (5, 10) not in {
        (count, cells) for count, cells, _probability in _joint(rollback, "q5")
    }
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE in default["notes"]
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE not in rollback["notes"]
    assert [
        default[key] for key in ("conservative", "balanced", "aggressive")
    ] != [
        rollback[key] for key in ("conservative", "balanced", "aggressive")
    ]


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_4501_catalog_supported_q5_arms_remain_probabilistic(
    max_combos: int,
) -> None:
    result = _run(_snapshot(), max_combos=max_combos)

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 8
    assert result["evidence"]["soft_avg_cell_keys"] == ["q5"]
    assert result["evidence"]["fixed_counts"].get("q5") is None
    assert result["quality_lock"]["q5"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert (7, 14, 0.0218) in _joint(result, "q5")
    assert (4, 38, 0.0218) in _joint(result, "q6")
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE in result["notes"]
    assert [
        result[key] for key in ("conservative", "balanced", "aggressive")
    ] == [1_196_703, 1_675_362, 1_675_362]


def test_4501_rollback_restores_provisional_grid_penalty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable(monkeypatch)
    rollback = _run(_snapshot())

    assert rollback["combo_count"] == 8
    assert rollback["quality_count_cells_candidates"]["q5"][0] == {
        "count": 4,
        "cells": 8,
        "prob": 0.99,
    }
    assert rollback["quality_count_cells_candidates"]["q6"][0] == {
        "count": 7,
        "cells": 44,
        "prob": 0.99,
    }
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE not in rollback["notes"]


@pytest.mark.parametrize(
    "qualification",
    [
        "official_distribution",
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
    ],
)
def test_source_pools_keep_their_qualification(
    qualification: str,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(qualification=qualification)
    result = _run(copy.deepcopy(snapshot))
    summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / f"{qualification}.json",
    )

    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE in result["notes"]
    assert summary["context"]["warehouse_source_qualification"] == qualification
    assert summary["context"]["warehouse_source"]["qualification"] == qualification
    assert summary["context"]["warehouse_source"][
        "official_distribution_eligible"
    ] == (
        qualification == "official_distribution"
    )
    assert (7, 14, 0.0218) in _joint(summary["ahmed_ref"], "q5")


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_value",
        "missing_public_avg",
        "malformed_public_avg",
        "missing_total_count",
        "missing_total_grid",
        "direct_q5_count",
        "direct_q5_cells",
        "direct_q6_count",
        "direct_q6_cells",
        "direct_q6_value",
        "non_public_q5_avg",
    ],
)
def test_incomplete_conflicting_or_strong_evidence_is_noop(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    structured = snapshot["structured_ref_inputs"]
    facts = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]
    if mutation == "missing_value":
        structured["quality_values"].pop("q5")
        snapshot["action_result_rows"] = [
            row
            for row in snapshot["action_result_rows"]
            if row["action_id"] != 100125
        ]
        snapshot["ui_contract"]["actions"]["results"] = [
            row
            for row in snapshot["ui_contract"]["actions"]["results"]
            if row["action_id"] != 100125
        ]
    elif mutation == "missing_public_avg":
        facts.clear()
    elif mutation == "malformed_public_avg":
        facts[0]["value"] = "bad"
    elif mutation == "missing_total_count":
        structured.pop("total_count")
        snapshot["action_result_rows"] = [
            row
            for row in snapshot["action_result_rows"]
            if row["action_id"] != 100115
        ]
        snapshot["ui_contract"]["actions"]["results"] = [
            row
            for row in snapshot["ui_contract"]["actions"]["results"]
            if row["action_id"] != 100115
        ]
    elif mutation == "missing_total_grid":
        structured.pop("total_cells")
        snapshot["action_result_rows"] = [
            row
            for row in snapshot["action_result_rows"]
            if row["action_id"] != 100103
        ]
        snapshot["ui_contract"]["actions"]["results"] = [
            row
            for row in snapshot["ui_contract"]["actions"]["results"]
            if row["action_id"] != 100103
        ]
    elif mutation == "direct_q5_count":
        structured["counts"]["q5"] = 7
    elif mutation == "direct_q5_cells":
        structured["quality_cells"]["q5"] = 14
    elif mutation == "direct_q6_count":
        structured["counts"]["q6"] = 4
    elif mutation == "direct_q6_cells":
        structured["quality_cells"]["q6"] = 38
    elif mutation == "direct_q6_value":
        structured["quality_values"]["q6"] = 900_000
    else:
        facts.clear()
        structured["avg_cells"]["q5"] = Q5_PUBLIC_AVG_CELLS

    default = _run(copy.deepcopy(snapshot))
    _disable(monkeypatch)
    rollback = _run(copy.deepcopy(snapshot))

    assert default == rollback
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE not in default["notes"]


@pytest.mark.parametrize(
    ("hero", "map_id"),
    [
        ("ahmed", 4501),
        ("gabriela", 4501),
        ("ethan", 4501),
        ("maria", 4501),
        ("raven", 4501),
        ("ivan", 4501),
        ("carlos", 4501),
        ("aisha", 2501),
    ],
)
def test_other_heroes_and_maps_are_noop(
    hero: str,
    map_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(hero=hero, map_id=map_id)
    default = _run(copy.deepcopy(snapshot))
    _disable(monkeypatch)
    rollback = _run(copy.deepcopy(snapshot))

    assert default == rollback
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE not in default["notes"]


def test_settlement_truth_is_not_reweighted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(phase="settled")
    snapshot["final_quality_counts"] = "q1=18;q3=9;q4=7;q5=7;q6=4"
    snapshot["final_quality_cells"] = "q1=30;q3=16;q4=24;q5=14;q6=38"
    snapshot["final_quality_values"] = (
        "q1=0;q3=0;q4=0;q5=154624;q6=580684"
    )
    snapshot["ui_contract"]["truth"] = {
        "available": True,
        "complete": True,
        "total_items": 45,
        "total_cells": 122,
        "total_value": 735_308,
    }

    default = _run(copy.deepcopy(snapshot))
    _disable(monkeypatch)
    rollback = _run(copy.deepcopy(snapshot))

    assert default == rollback
    assert default["quality_count_ranges"]["q5"] == [7, 7, 7]
    assert default["quality_cells_ranges"]["q5"] == [14, 14, 14]
    assert default["quality_count_ranges"]["q6"] == [4, 4, 4]
    assert default["quality_cells_ranges"]["q6"] == [38, 38, 38]
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE not in default["notes"]


def test_product_summary_consumes_probabilistic_joint_without_relabeling(
    tmp_path: Path,
) -> None:
    summary = summarize_snapshot(
        _snapshot(),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]

    assert (7, 14, 0.0218) in _joint(ref, "q5")
    assert (4, 38, 0.0218) in _joint(ref, "q6")
    assert ref["quality_lock"]["q5"]["strength"] == "probabilistic"
    assert ref["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert AISHA_Q5_EXACT_VALUE_PUBLIC_AVG_SUPPORT_NOTE in ref["notes"]
    assert summary["context"]["warehouse_source_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert (
        summary["context"]["warehouse_source"][
            "official_distribution_eligible"
        ]
        is False
    )


@pytest.mark.parametrize("total_value", [99_999, 100_000, 100_002])
def test_boundary_product_payload_preserves_visible_and_other_mass(
    total_value: int,
    tmp_path: Path,
) -> None:
    summary = summarize_snapshot(
        _snapshot_with_q5_total(total_value),
        snapshot_path=tmp_path / f"q5-{total_value}.json",
    )
    ref = summary["ahmed_ref"]
    q5 = summary["quality_composition"]["qualities"]["q5"]
    visible = sum(
        float(row["prob"]) for row in ref["quality_count_cells_candidates"]["q5"]
    )

    assert q5["status"] == "ready"
    assert q5["lock_strength"] == "probabilistic"
    assert q5["pricing_posterior"] is True
    assert q5["shown_probability"] == pytest.approx(visible)
    assert q5["other_probability"] == pytest.approx(1.0 - visible)
    assert q5["truncated"] is (visible < 1.0)
