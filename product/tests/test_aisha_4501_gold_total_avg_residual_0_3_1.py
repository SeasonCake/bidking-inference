"""Aisha 4501 gold total+average residual regression for 0.3.1.

Sanitized from HeroRefDiag-20260728-182338-4501_9000000000000033.
At bidding R3 the game exposed q5 total value 134,950 and public q5 average
33,737.5, which jointly prove four gold items.  A hard q4 average required a
multiple-of-six q4 count, but the statistical q4 window stopped at 12.  The
empty joint support then triggered the generic public-average fallback and
reopened q5 at nine items.
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
    AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_DISABLE,
    AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


Q5_TOTAL = 134_950
Q5_AVG = 33_737.5
Q4_AVG_CELLS = 2.1666667461395264


def _source(qualification: str = "postupdate_unknown_warehouse_source") -> dict:
    return {
        "qualification": qualification,
        "official_distribution_eligible": qualification == "official_distribution",
        "quote_policy": (
            "default"
            if qualification == "official_distribution"
            else "advisory_only_no_clamp"
        ),
    }


def _snapshot(*, hero: str = "aisha") -> dict:
    source = _source()
    actions = [
        {"action_id": 100125, "result": Q5_TOTAL},
        {"action_id": 100112, "result": Q4_AVG_CELLS},
        {"action_id": 100115, "result": 45},
    ]
    skill_rows = [
        {
            "sort": 17,
            "skill_id": skill_id,
            "hero_id": 103,
            "revealed_items": count,
            "revealed_items_detail": [],
        }
        for skill_id, count in ((1001034, 3), (1001033, 4), (1001032, 10))
    ]
    return {
        "created_at": time.time(),
        "hero": hero,
        "map_id": 4501,
        "round": 3,
        "phase": "bidding",
        "session_id": "4501:9000000000000033",
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": False,
        "action_result_rows": copy.deepcopy(actions),
        "skill_reveal_rows": skill_rows if hero == "aisha" else [],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": hero,
            "total_count": 45,
            "avg_cells": {"q4": Q4_AVG_CELLS},
            "counts": {"q3": 10},
            "quality_cells": {"q3": 33},
            "quality_values": {"q5": Q5_TOTAL},
            "split_counts": {"white": 3, "green": 4},
            "split_quality_cells": {"white": 5, "green": 6},
        },
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 4501,
                "round": 3,
                "phase": "bidding",
                "session_id": "4501:9000000000000033",
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": source["qualification"],
                "official_distribution_eligible": False,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "info_id": 200037,
                            "semantic": "q5_avg_value",
                            "kind": "avg_value",
                            "quality": 5,
                            "value": Q5_AVG,
                        }
                    ]
                }
            },
            "actions": {"results": copy.deepcopy(actions)},
            "baseline": {"decision": {}, "posterior": {}},
            "source": {"created_at": time.time()},
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=max_combos,
    ).as_dict()


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_4501_gold_total_average_keeps_exact_four_count(
    max_combos: int,
) -> None:
    result = _run(_snapshot(), max_combos=max_combos)

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 2
    assert result["evidence"]["fixed_counts"]["q5"] == 4
    assert result["quality_count_ranges"]["q5"] == [4, 4, 4]
    assert result["quality_count_ranges"]["q4"] == [18, 18, 18]
    assert result["quality_count_ranges"]["q6"] == [6, 6, 6]
    assert result["quality_lock"]["q5"] == {
        "strength": "exact",
        "reason": "exact_lock",
    }
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["quality_count_candidates"]["q6"][-1] == {
        "value": 0,
        "prob": 0.0258,
    }
    assert [
        result[key] for key in ("conservative", "balanced", "aggressive")
    ] == [1_132_773, 1_440_180, 1_539_819]
    assert AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_NOTE in result["notes"]
    assert "q5_catalog_projection:count:4" in result["notes"]
    assert "quality_value_q5_count_derived" in result["notes"]
    assert "public_quality_avg_value_conflict_fallback" not in result["notes"]
    assert "public_q5_avg_value_downgraded" not in result["notes"]


def test_4501_rollback_reproduces_nine_gold_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_DISABLE,
        "1",
    )
    legacy = _run(_snapshot())

    assert legacy["status"] == "count_prior"
    assert legacy["quality_count_ranges"]["q5"] == [9, 9, 9]
    assert [
        legacy[key] for key in ("conservative", "balanced", "aggressive")
    ] == [1_250_999, 1_592_107, 1_699_707]
    assert AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_NOTE not in legacy["notes"]
    assert "public_quality_avg_value_conflict_fallback" in legacy["notes"]
    assert "public_q5_avg_value_downgraded" in legacy["notes"]


def test_4501_dual_pass_product_summary_consumes_four_gold(
    tmp_path: Path,
) -> None:
    summary = summarize_snapshot(
        _snapshot(),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["reference"]["decision_range"] == (
        "1,132,773 / 1,440,180 / 1,539,819"
    )
    assert summary["ahmed_ref"]["quality_count_ranges"]["q5"] == [4, 4, 4]
    assert AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_NOTE in (
        summary["ahmed_ref"]["notes"]
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing_total_value", "conflicting_average", "direct_red"],
)
def test_4501_incomplete_or_conflicting_evidence_is_noop(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    if mutation == "missing_total_value":
        snapshot["structured_ref_inputs"]["quality_values"].pop("q5")
        snapshot["action_result_rows"] = [
            row for row in snapshot["action_result_rows"] if row["action_id"] != 100125
        ]
        snapshot["ui_contract"]["actions"]["results"] = [
            row
            for row in snapshot["ui_contract"]["actions"]["results"]
            if row["action_id"] != 100125
        ]
    elif mutation == "conflicting_average":
        fact = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ][0]
        fact["value"] = 30_000.0
    else:
        snapshot["structured_ref_inputs"]["counts"]["q6"] = 6

    default = _run(copy.deepcopy(snapshot))
    monkeypatch.setenv(
        AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_DISABLE,
        "1",
    )
    rollback = _run(copy.deepcopy(snapshot))

    assert default == rollback
    assert AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_NOTE not in default["notes"]


@pytest.mark.parametrize(
    "hero",
    ["ahmed", "gabriela", "ethan", "maria", "raven", "ivan", "carlos"],
)
def test_4501_same_inputs_are_noop_for_other_heroes(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = _run(_snapshot(hero=hero))
    monkeypatch.setenv(
        AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_DISABLE,
        "1",
    )
    rollback = _run(_snapshot(hero=hero))

    assert default == rollback
    assert AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_NOTE not in default["notes"]


def test_4501_settlement_truth_remains_exact() -> None:
    snapshot = _snapshot()
    snapshot["phase"] = "settled"
    snapshot["ui_contract"]["context"]["phase"] = "settled"
    snapshot["ui_contract"]["truth"] = {
        "available": True,
        "complete": True,
        "total_items": 45,
        "total_cells": 113,
        "total_value": 1_095_108,
    }
    snapshot["final_quality_counts"] = "q1=3;q2=4;q3=10;q4=18;q5=4;q6=6"
    snapshot["final_quality_cells"] = "q1=5;q2=6;q3=33;q4=39;q5=15;q6=15"
    snapshot["final_quality_values"] = (
        "q1=6815;q2=11383;q3=57207;q4=71710;q5=134950;q6=813180"
    )

    result = _run(snapshot)

    assert result["status"] == "ok"
    assert result["evidence"]["total_count"] == 45
    assert result["evidence"]["total_grid_target"] == 113
    assert result["quality_count_ranges"]["q5"] == [4, 4, 4]
    assert result["quality_cells_ranges"]["q5"] == [15, 15, 15]
    assert result["quality_count_ranges"]["q6"] == [6, 6, 6]
    assert result["quality_cells_ranges"]["q6"] == [15, 15, 15]
    assert AISHA_Q5_VALUE_Q4_AVG_RESIDUAL_ANTILOCK_NOTE not in result["notes"]
