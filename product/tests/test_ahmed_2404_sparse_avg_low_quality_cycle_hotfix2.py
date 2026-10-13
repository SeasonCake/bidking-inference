"""Ahmed 2404 sparse-average low-quality feedback-loop regression.

Sanitized from HeroRefDiag-20260728-000648-2404_9000000000000031.
The bidding timeline was exact total=53, exact q3=26, hard q5 mean=2.6,
hard q4 mean=2.25, then merged q1 cells=14.  The old tight prior published
q6=0, then q1=0/q6=5, then no reachable combo.  Settlement truth was
q1/q3/q4/q5/q6 = 9/26/12/5/1.
"""

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
    AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_DISABLE,
    AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_NOTE,
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


def _snapshot_2404(
    stage: int = 3,
    *,
    hero: str = "ahmed",
    phase: str = "bidding",
    source: dict | None = UNKNOWN_SOURCE,
) -> dict:
    """Build the three evidence transitions that reproduced the live incident."""
    actions = [{"action_id": 100117, "result": 26}]
    avg_cells = {"q5": 2.5999999046325684}
    quality_cells: dict[str, int] = {}
    skill_reveals = [
        {
            "hero_id": 204,
            "skill_id": 1002041,
            "result": 2.5999999046325684,
        }
    ]
    if stage >= 2:
        avg_cells["q4"] = 2.25
        skill_reveals.append(
            {
                "hero_id": 204,
                "skill_id": 1002042,
                "result": 2.25,
            }
        )
    if stage >= 3:
        actions.append({"action_id": 100104, "result": 14})
        quality_cells["q1"] = 14

    context = {
        "hero": hero,
        "map_id": 2404,
        "round": stage + 1,
        "phase": phase,
        "session_id": "2404:9000000000000031",
    }
    snapshot = {
        **context,
        "ui_contract": {
            "context": copy.deepcopy(context),
            "actions": {"results": actions},
        },
        "structured_ref_inputs": {
            "total_count": 53,
            "fixed_counts": {"q3": 26},
            "avg_cells": avg_cells,
            "quality_cells": quality_cells,
        },
        "skill_reveals": skill_reveals,
    }
    if source is not None:
        for owner in (snapshot, snapshot["ui_contract"]["context"]):
            owner["warehouse_source"] = copy.deepcopy(source)
            owner["warehouse_source_qualification"] = source["qualification"]
            owner["official_distribution_eligible"] = source[
                "official_distribution_eligible"
            ]
    return snapshot


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return run_reference_engine(snapshot, max_combos=max_combos).as_dict()


def _quotes(result: dict) -> list[int | None]:
    return [
        result["conservative"],
        result["balanced"],
        result["aggressive"],
    ]


def test_2404_default_breaks_the_full_feedback_loop_with_named_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = [_run(_snapshot_2404(stage)) for stage in (1, 2, 3)]

    monkeypatch.setenv(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_DISABLE,
        "1",
    )
    rollback = [_run(_snapshot_2404(stage)) for stage in (1, 2, 3)]

    assert _quotes(rollback[0]) == [223_089, 231_692, 240_294]
    assert rollback[0]["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert _quotes(rollback[1]) == [1_146_107, 1_235_715, 1_325_323]
    assert rollback[1]["quality_count_ranges"]["q1"] == [0, 0, 0]
    assert rollback[1]["quality_count_ranges"]["q6"] == [5, 5, 5]
    assert rollback[2]["status"] == "no_reachable_combo"
    assert _quotes(rollback[2]) == [None, None, None]

    assert _quotes(default[0]) == [372_729, 500_245, 679_544]
    assert default[0]["quality_count_ranges"]["q6"] == [0, 2, 3]
    assert _quotes(default[1]) == [377_437, 477_321, 678_970]
    assert default[1]["quality_count_ranges"]["q1"] == [9, 11, 13]
    assert default[1]["quality_count_ranges"]["q6"] == [0, 1, 4]
    assert _quotes(default[2]) == [377_366, 477_142, 678_845]
    assert default[2]["status"] == "count_prior"
    assert default[2]["quality_count_ranges"]["q1"] == [9, 11, 13]
    assert default[2]["quality_cells_ranges"]["q1"] == [14, 14, 14]
    assert default[2]["quality_count_ranges"]["q6"] == [0, 1, 4]
    assert default[2]["combo_count"] == 9

    truth = {"q1": 9, "q3": 26, "q4": 12, "q5": 5, "q6": 1}
    for key, value in truth.items():
        low, _mid, high = default[2]["quality_count_ranges"][key]
        assert low <= value <= high
    assert all(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_NOTE in result["notes"]
        for result in default
    )
    assert all(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_NOTE
        not in result["notes"]
        for result in rollback
    )


@pytest.mark.parametrize("source", [UNKNOWN_SOURCE, PLAYER_SOURCE])
def test_explicit_nonofficial_source_pools_share_the_safety_contract(
    source: dict,
) -> None:
    result = _run(_snapshot_2404(source=source))

    assert result["status"] == "count_prior"
    assert result["quality_count_ranges"]["q6"] == [0, 1, 4]
    assert AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_NOTE in result["notes"]


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
def test_missing_official_or_malformed_source_is_noop(
    source: dict | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404(source=source)
    default = _run(snapshot)
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_DISABLE,
        "1",
    )
    rollback = _run(snapshot)

    assert default == rollback


@pytest.mark.parametrize("hero", ["aisha", "gabriela", "ethan", "maria"])
def test_other_heroes_are_noop(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404(hero=hero)
    default = _run(snapshot)
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_DISABLE,
        "1",
    )
    rollback = _run(snapshot)

    assert default == rollback


def test_exact_zero_low_quality_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404(stage=2)
    snapshot["structured_ref_inputs"]["fixed_counts"]["q1"] = 0
    default = _run(snapshot)
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_DISABLE,
        "1",
    )
    rollback = _run(snapshot)

    assert default == rollback


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("q4", 12),
        ("q6", 1),
    ],
)
def test_direct_high_tier_count_evidence_is_noop(
    key: str,
    value: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404(stage=2)
    snapshot["structured_ref_inputs"]["fixed_counts"][key] = value
    default = _run(snapshot)
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_DISABLE,
        "1",
    )
    rollback = _run(snapshot)

    assert default == rollback


def test_settlement_truth_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404(phase="settled")
    snapshot["structured_ref_inputs"].update(
        fixed_counts={"q1": 9, "q3": 26, "q4": 12, "q5": 5, "q6": 1},
        quality_cells={"q1": 14, "q3": 89, "q4": 27, "q5": 13, "q6": 1},
        total_cells=144,
    )
    default = _run(snapshot)
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_DISABLE,
        "1",
    )
    rollback = _run(snapshot)

    assert default == rollback
    assert default["quality_count_ranges"]["q6"] == [1, 1, 1]
    assert default["quality_cells_ranges"]["q6"] == [1, 1, 1]
    assert default["total_grid_range"] == [144, 144, 144]


def test_product_summary_no_longer_enters_waiting_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404()
    default = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_SPARSE_AVG_LOW_QUALITY_CYCLE_DISABLE,
        "1",
    )
    rollback = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert default["reference"]["decision_range"] == (
        "377,366 / 477,142 / 678,845"
    )
    assert default["red"]["count_range"] == "0 / 1 / 4"
    assert default["red"]["cells_range"] == "0 / 4 / 11"
    assert default["red"]["uncertainty_summary"] == "未锁 白绿9/11/13"
    assert [flag["label"] for flag in default["flags"][:2]] == [
        "仓来源待确认",
        "宽约束快速",
    ]
    assert rollback["reference"]["readiness"] == "no_reachable_combo"
    assert rollback["reference"]["decision_range"] == "-"
    assert rollback["red"]["count_range"] == "-"


def test_product_and_audit_combo_caps_agree() -> None:
    product = _run(_snapshot_2404(), max_combos=8_000)
    audit = _run(_snapshot_2404(), max_combos=50_000)

    for key in (
        "status",
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
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "red_count_range",
        "red_cells_range",
        "red_value_range",
        "total_grid_range",
    ):
        assert product[key] == audit[key]
