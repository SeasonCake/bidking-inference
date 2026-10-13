from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_ref_engine import (
    AHMED_PUBLIC_Q4_AVG_ANTILOCK_DISABLE,
    AHMED_PUBLIC_Q4_AVG_ANTILOCK_NOTE,
    AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_DISABLE,
    AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_NOTE,
    _display_grid_options_for_quality,
    extract_evidence,
    run_reference_engine,
)
import ahmad_live_panel_server as live_panel


PRODUCT_R2_MAX_COMBOS = 8_000


def _snapshot(*, skill_avg: float | None) -> dict:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4542,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "q5_avg_cells", "value": 4.125},
                    ],
                },
            },
            "actions": {
                "results": [
                    {"action_id": 100110, "result": 2.2916667461395264},
                    {"action_id": 100117, "result": 14},
                    {"action_id": 100118, "result": 7},
                ],
            },
        },
        "structured_ref_inputs": {
            "total_count": 56,
            "fixed_counts": {"q3": 14, "q4": 7},
            "avg_cells": {
                "q1": 2.2916667461395264,
                "q5": 4.125,
            },
        },
    }
    if skill_avg is not None:
        snapshot["skill_reveals"] = [
            {
                "hero_id": 104,
                "skill_id": 1002041,
                "result": skill_avg,
            },
        ]
    return snapshot


def _public_q4_only_4541_r2_snapshot(*, with_low_avg: bool = False) -> dict:
    """Reduced from HeroRefDiag 4541:1741282928131482 R2."""
    actions = [
        {"action_id": 100117, "result": 13},
    ]
    avg_cells = {"q5": 5.400000095367432}
    if with_low_avg:
        actions.append({"action_id": 100110, "result": 1.1111111640930176})
        avg_cells["q1"] = 1.1111111640930176
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
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


def _soft_q4_low_red_gap_2601_r2_snapshot() -> dict:
    """Reduced from HeroRefDiag 2601:1741283023365943 R2.

    Settlement truth is 48 items with q6=2 items / 3 cells.  Before the
    low-red gap retry, the sparse prior produced only q6=0.
    """
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2601,
                "phase": "bidding",
                "round": 2,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "semantic": "q4_avg_cells",
                            "value": 3.473684310913086,
                        },
                    ],
                },
            },
            "actions": {
                "results": [
                    {"action_id": 100117, "result": 13},
                ],
            },
        },
        "structured_ref_inputs": {
            "total_count": 48,
            "fixed_counts": {"q3": 13},
            "avg_cells": {"q5": 4.769230842590332},
        },
        "skill_reveals": [
            {
                "hero_id": 204,
                "skill_id": 1002041,
                "result": 4.769230842590332,
            },
        ],
    }


def _soft_q4_low_red_gap_2601_r2_with_random_avg(
    *,
    sample_count: int,
    avg_value: float,
) -> dict:
    snapshot = _soft_q4_low_red_gap_2601_r2_snapshot()
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    public_info["public_numeric_facts"].append(
        {
            "semantic": f"random_{sample_count}_avg_value",
            "kind": "random_avg_value",
            "sample_count": sample_count,
            "value": avg_value,
        }
    )
    return snapshot


def _true_zero_red_2505_r2_snapshot() -> dict:
    """Reduced negative from HeroRefDiag 2505:1630025996132767 R2.

    Settlement truth q6=0.  Its hard q1 average is intentionally outside the
    2601 evidence shape, so the exact zero-red result must remain unchanged.
    """
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2505,
                "phase": "bidding",
                "round": 2,
            },
            "actions": {
                "results": [
                    {"action_id": 100117, "result": 12},
                    {
                        "action_id": 100110,
                        "result": 2.0322580337524414,
                    },
                ],
            },
        },
        "structured_ref_inputs": {
            "total_count": 66,
            "fixed_counts": {"q3": 12},
            "avg_cells": {
                "q1": 2.0322580337524414,
                "q5": 3.222222328186035,
            },
        },
        "skill_reveals": [
            {
                "hero_id": 204,
                "skill_id": 1002041,
                "result": 3.222222328186035,
            },
        ],
    }


def test_2601_r2_soft_purple_split_does_not_false_lock_zero_red() -> None:
    result = run_reference_engine(
        _soft_q4_low_red_gap_2601_r2_snapshot(),
        max_combos=PRODUCT_R2_MAX_COMBOS,
    ).as_dict()

    assert result["combo_count"] == 4
    assert result["evidence"]["soft_avg_cell_keys"] == ["q4"]
    assert result["quality_count_ranges"] == {
        "q1": [0, 1, 2],
        "q3": [13, 13, 13],
        "q4": [19, 19, 19],
        "q5": [13, 13, 13],
        "q6": [1, 2, 3],
    }
    assert result["quality_cells_ranges"] == {
        "q1": [0, 2, 5],
        "q3": [28, 29, 30],
        "q4": [66, 66, 66],
        "q5": [62, 62, 62],
        "q6": [2, 6, 10],
    }
    assert result["red_count_range"] == [1, 2, 3]
    assert result["red_cells_range"] == [2, 6, 15]
    assert result["red_value_range"] == [126121, 325474, 551631]
    red_count_candidates = result["quality_count_candidates"]["q6"]
    assert {row["value"] for row in red_count_candidates} == {0, 1, 2, 3}
    assert next(row["prob"] for row in red_count_candidates if row["value"] == 0) > 0
    assert [
        result["conservative"],
        result["balanced"],
        result["aggressive"],
    ] == [888678, 998291, 1085818]
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_NOTE in result["notes"]


def test_2601_r2_low_red_gap_antilock_has_named_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_DISABLE, "1")

    legacy = run_reference_engine(
        _soft_q4_low_red_gap_2601_r2_snapshot(),
        max_combos=PRODUCT_R2_MAX_COMBOS,
    ).as_dict()

    assert legacy["combo_count"] == 1
    assert legacy["quality_count_ranges"]["q1"] == [3, 3, 3]
    assert legacy["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert legacy["quality_cells_ranges"]["q6"] == [0, 0, 0]
    assert legacy["red_value_range"] == [0, 0, 0]
    assert [
        legacy["conservative"],
        legacy["balanced"],
        legacy["aggressive"],
    ] == [670645, 695018, 719391]
    assert AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_NOTE not in legacy["notes"]


def test_true_zero_red_2505_is_unchanged_by_2601_antilock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired = run_reference_engine(
        _true_zero_red_2505_r2_snapshot(),
        max_combos=PRODUCT_R2_MAX_COMBOS,
    ).as_dict()
    monkeypatch.setenv(AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(
        _true_zero_red_2505_r2_snapshot(),
        max_combos=PRODUCT_R2_MAX_COMBOS,
    ).as_dict()

    assert repaired == rollback
    assert repaired["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert repaired["quality_cells_ranges"]["q6"] == [0, 0, 0]
    assert repaired["red_value_range"] == [0, 0, 0]
    assert AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_NOTE not in repaired["notes"]


@pytest.mark.parametrize("sample_count", [5, 6, 7])
def test_2601_low_signal_random_avg_sample_count_does_not_gate_antilock(
    sample_count: int,
) -> None:
    result = run_reference_engine(
        _soft_q4_low_red_gap_2601_r2_with_random_avg(
            sample_count=sample_count,
            avg_value=1_000.0,
        ),
        max_combos=PRODUCT_R2_MAX_COMBOS,
    ).as_dict()

    assert result["evidence"]["random_value_floors"] == [
        [sample_count, sample_count * 1_000.0]
    ]
    assert result["evidence"]["min_counts"].get("q6") is None
    assert result["evidence"]["quality_value_floors"].get("q6") is None
    assert result["combo_count"] == 4
    assert result["quality_count_ranges"]["q6"] == [1, 2, 3]
    assert AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_NOTE in result["notes"]


@pytest.mark.parametrize("sample_count", [5, 6, 7])
def test_2601_high_signal_random_avg_routes_through_canonical_red_anchor(
    sample_count: int,
) -> None:
    result = run_reference_engine(
        _soft_q4_low_red_gap_2601_r2_with_random_avg(
            sample_count=sample_count,
            avg_value=100_000.0,
        ),
        max_combos=PRODUCT_R2_MAX_COMBOS,
    ).as_dict()

    assert result["evidence"]["min_counts"]["q6"] == 1
    assert result["evidence"]["quality_value_floors"]["q6"] == pytest.approx(
        sample_count * 95_000.0
    )
    assert AHMED_SOFT_Q4_LOW_RED_GAP_ANTILOCK_NOTE not in result["notes"]


def test_2601_product_scheduler_and_summary_use_r2_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _soft_q4_low_red_gap_2601_r2_snapshot()
    snapshot["created_at"] = time.time()
    snapshot["ui_contract"].setdefault("source", {})["created_at"] = time.time()
    observed_budgets: list[int] = []

    def recording_engine(prepared: dict, *, max_combos: int):
        observed_budgets.append(max_combos)
        return run_reference_engine(prepared, max_combos=max_combos)

    monkeypatch.setattr(live_panel, "run_reference_engine", recording_engine)
    summary = live_panel.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert observed_budgets == [PRODUCT_R2_MAX_COMBOS]
    result = summary["ahmed_ref"]
    assert result["combo_count"] == 4
    assert result["quality_count_ranges"]["q6"] == [1, 2, 3]
    assert result["quality_cells_ranges"]["q6"] == [2, 6, 10]
    assert result["red_value_range"] == [126121, 325474, 551631]
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        888678,
        998291,
        1085818,
    ]
    assert summary["red"]["count_range"] == "1 / 2 / 3"
    assert summary["red"]["cells_range"] == "2 / 6 / 15"
    assert summary["red"]["value_range"] == "126,121 / 325,474 / 551,631"


def test_4541_public_only_purple_avg_does_not_false_lock_count() -> None:
    result = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(),
        max_combos=50_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["evidence"]["soft_avg_cell_keys"] == ["q4"]
    assert result["quality_count_ranges"]["q4"] == [8, 8, 16]
    assert result["quality_cells_ranges"]["q4"] == [17, 17, 34]
    assert result["quality_lock"]["q4"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert {row["value"] for row in result["quality_count_candidates"]["q4"]} == {
        8,
        16,
    }
    assert AHMED_PUBLIC_Q4_AVG_ANTILOCK_NOTE in result["notes"]
    assert result["quality_count_ranges"]["q5"] == [5, 5, 5]
    assert result["quality_cells_ranges"]["q5"] == [27, 27, 27]


def test_4541_public_purple_avg_antilock_has_narrow_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(),
        max_combos=50_000,
    ).as_dict()
    monkeypatch.setenv(AHMED_PUBLIC_Q4_AVG_ANTILOCK_DISABLE, "1")

    result = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(),
        max_combos=50_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert result["quality_cells_ranges"]["q4"] == [17, 17, 17]
    assert result["quality_lock"]["q4"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert AHMED_PUBLIC_Q4_AVG_ANTILOCK_NOTE not in result["notes"]
    assert [
        repaired["conservative"],
        repaired["balanced"],
        repaired["aggressive"],
    ] == [
        result["conservative"],
        result["balanced"],
        result["aggressive"],
    ]


def test_4541_final_r2_filters_counts_incompatible_with_public_purple_avg() -> None:
    result = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(with_low_avg=True),
        max_combos=50_000,
    ).as_dict()

    assert result["evidence"]["soft_avg_cell_keys"] == ["q4"]
    assert result["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert result["quality_cells_ranges"]["q4"] == [17, 17, 17]
    assert result["quality_count_ranges"]["q6"] == [3, 3, 3]
    assert result["quality_lock"]["q4"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert AHMED_PUBLIC_Q4_AVG_ANTILOCK_NOTE not in result["notes"]


def test_4541_final_r2_public_purple_feasibility_has_narrow_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(with_low_avg=True),
        max_combos=50_000,
    ).as_dict()
    monkeypatch.setenv(AHMED_PUBLIC_Q4_AVG_ANTILOCK_DISABLE, "1")

    legacy = run_reference_engine(
        _public_q4_only_4541_r2_snapshot(with_low_avg=True),
        max_combos=50_000,
    ).as_dict()

    assert repaired["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert repaired["quality_cells_ranges"]["q4"] == [17, 17, 17]
    assert legacy["quality_count_ranges"]["q4"] == [6, 8, 10]
    assert legacy["quality_cells_ranges"]["q4"] == [14, 17, 24]


@pytest.mark.parametrize("hero", ["aisha", "gabriela"])
def test_4541_public_purple_fix_is_scoped_to_ahmed(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _public_q4_only_4541_r2_snapshot(with_low_avg=True)
    snapshot["ui_contract"]["context"]["hero"] = hero
    snapshot.pop("skill_reveals", None)

    unchanged = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_PUBLIC_Q4_AVG_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert unchanged == rollback
    assert unchanged["quality_count_ranges"]["q4"] == [6, 8, 10]


def test_matching_ahmed_skill_avg_is_not_downgraded_by_public_avg() -> None:
    result = run_reference_engine(_snapshot(skill_avg=4.125), max_combos=50_000).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["soft_avg_cell_keys"] == []
    assert "skill_1002041_q5_avg_cells" in result["notes"]
    assert result["quality_count_ranges"]["q5"] == [8, 8, 8]
    assert result["quality_cells_ranges"]["q5"] == [33, 33, 33]
    assert result["quality_count_cells_candidates"]["q5"] == [
        {"count": 8, "cells": 33, "prob": 1.0},
    ]


def test_r2_joint_prior_does_not_turn_gold_avg_into_false_16_item_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4542 R2: a tight sibling prior must not delete the feasible 8-gold arm."""
    snapshot = _snapshot(skill_avg=4.125)
    snapshot["ui_contract"]["context"]["round"] = 2
    snapshot["ui_contract"]["actions"]["results"] = [
        row
        for row in snapshot["ui_contract"]["actions"]["results"]
        if row["action_id"] != 100118
    ]
    snapshot["structured_ref_inputs"]["fixed_counts"] = {"q3": 14}

    repaired = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert repaired["status"] == "count_prior"
    assert repaired["combo_count"] == 4
    assert repaired["quality_count_ranges"]["q4"] == [8, 8, 9]
    assert repaired["quality_count_ranges"]["q5"] == [8, 8, 8]
    assert repaired["quality_cells_ranges"]["q5"] == [33, 33, 33]
    assert "sparse_exact_joint_avg_domain_untightened:q5" in repaired["notes"]

    monkeypatch.setenv("BIDKING_DISABLE_SPARSE_EXACT_JOINT_AVG_ANTILOCK", "1")
    legacy = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    assert legacy["combo_count"] == 1
    assert legacy["quality_count_ranges"]["q4"] == [0, 0, 0]
    assert legacy["quality_count_ranges"]["q5"] == [16, 16, 16]
    assert legacy["quality_cells_ranges"]["q5"] == [66, 66, 66]


def test_hard_ahmed_skill_avg_cannot_fall_back_to_default_gold_cells() -> None:
    evidence = extract_evidence(_snapshot(skill_avg=4.125))
    impossible_counts = {"q1": 24, "q3": 14, "q4": 7, "q5": 7, "q6": 4}
    valid_counts = {"q1": 24, "q3": 14, "q4": 7, "q5": 8, "q6": 3}

    assert _display_grid_options_for_quality(
        impossible_counts,
        evidence,
        "q5",
    ) == ()
    assert _display_grid_options_for_quality(
        valid_counts,
        evidence,
        "q5",
    ) == (33,)


def test_public_only_gold_avg_keeps_existing_soft_semantics() -> None:
    evidence = extract_evidence(_snapshot(skill_avg=None))

    assert evidence.soft_avg_cell_keys == frozenset({"q5"})
    assert "public_q5_avg_cells_soft_pending_count" in evidence.source_notes
    assert not any(
        str(note).startswith("skill_1002041_q5_avg_cells")
        for note in evidence.source_notes
    )


def test_conflicting_skill_row_does_not_harden_public_gold_avg() -> None:
    evidence = extract_evidence(_snapshot(skill_avg=3.25))

    assert evidence.avg_cells["q5"] == 4.125
    assert evidence.soft_avg_cell_keys == frozenset({"q5"})
    assert "skill_1002041_q5_avg_cells_conflict" in evidence.source_notes
    assert "skill_1002041_q5_avg_cells" not in evidence.source_notes


def test_new_public_avg_can_unlock_stale_skill_avg() -> None:
    locked = run_reference_engine(_snapshot(skill_avg=4.125), max_combos=50_000).as_dict()

    updated_snapshot = _snapshot(skill_avg=4.125)
    updated_snapshot["ui_contract"]["constraints"]["public_info"]["public_numeric_facts"][0][
        "value"
    ] = 3.25
    updated_snapshot["structured_ref_inputs"]["avg_cells"]["q5"] = 3.25
    updated = run_reference_engine(updated_snapshot, max_combos=50_000).as_dict()

    assert locked["quality_count_ranges"]["q5"] == [8, 8, 8]
    assert locked["quality_cells_ranges"]["q5"] == [33, 33, 33]
    assert locked["quality_lock"]["q5"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }

    assert updated["evidence"]["soft_avg_cell_keys"] == ["q5"]
    assert updated["quality_count_ranges"]["q5"] == [4, 6, 9]
    assert updated["quality_cells_ranges"]["q5"] == [13, 17, 26]
    assert updated["quality_lock"]["q5"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert "skill_1002041_q5_avg_cells_conflict" in updated["notes"]
    assert "public_q5_avg_cells_soft_pending_count" in updated["notes"]


@pytest.mark.parametrize(
    ("quality", "total_count", "avg_value", "skill_id", "expected_count", "expected_cells"),
    [
        ("q4", 10, 5615.625, 1002042, 8, 26),
        ("q5", 7, 34288.75, 1002041, 4, 13),
    ],
)
def test_public_avg_value_and_skill_avg_cells_jointly_lock(
    quality: str,
    total_count: int,
    avg_value: float,
    skill_id: int,
    expected_count: int,
    expected_cells: int,
) -> None:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4542,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "semantic": f"{quality}_avg_value",
                            "kind": "avg_value",
                            "quality": int(quality[1]),
                            "value": avg_value,
                        },
                    ],
                },
            },
        },
        "structured_ref_inputs": {
            "total_count": total_count,
            "avg_cells": {quality: 3.25},
        },
        "skill_reveals": [
            {
                "hero_id": 104,
                "skill_id": skill_id,
                "result": 3.25,
            },
        ],
    }

    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["evidence"]["avg_cells"][quality] == 3.25
    assert result["evidence"]["avg_values"][quality] == avg_value
    assert result["evidence"]["soft_avg_cell_keys"] == []
    assert result["evidence"]["soft_avg_value_keys"] == []
    assert result["evidence"]["fixed_counts"][quality] == expected_count
    assert result["quality_count_ranges"][quality] == [
        expected_count,
        expected_count,
        expected_count,
    ]
    assert result["quality_cells_ranges"][quality] == [
        expected_cells,
        expected_cells,
        expected_cells,
    ]
    assert result["quality_lock"][quality] == {
        "strength": "exact",
        "reason": "exact_lock",
    }
    assert f"skill_{skill_id}_{quality}_avg_cells" in result["notes"]
    assert f"public_{quality}_avg_value" in result["notes"]
    assert f"avg_value_cells_{quality}_count_derived" in result["notes"]
    assert "public_quality_avg_value_conflict_fallback" not in result["notes"]
