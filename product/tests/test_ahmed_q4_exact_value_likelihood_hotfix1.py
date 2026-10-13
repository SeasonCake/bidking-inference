from __future__ import annotations

import sys
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
    AHMED_Q1_RESIDUAL_DISPLAY_ANTILOCK_DISABLE,
    Q4_EXACT_VALUE_JOINT_ANTILOCK_NOTE,
    Q4_EXACT_VALUE_LIKELIHOOD_REFINEMENT_DISABLE,
    Q4_EXACT_VALUE_LIKELIHOOD_REFINEMENT_NOTE,
    Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_DISABLE,
    Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_NOTE,
    _cached_exact_q4_count_cells_candidates,
    _quality_total_from_public_avg,
    _validated_q4_catalog,
    run_reference_engine,
)


def _snapshot(
    *,
    total_count: int,
    fixed_counts: dict[str, int],
    avg_cells: dict[str, float],
    quality_values: dict[str, int] | None = None,
    hero: str = "ahmed",
    map_id: int = 2501,
) -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": map_id,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": total_count,
            "fixed_counts": fixed_counts,
            "avg_cells": avg_cells,
            "quality_values": quality_values or {},
        },
    }


def _diag_2501_q4_value_snapshot(*, hero: str = "ahmed") -> dict:
    """Reduced pre-settlement facts from 2501:1758737621237155 R3."""
    return _snapshot(
        hero=hero,
        total_count=39,
        fixed_counts={"q3": 11},
        avg_cells={
            "q1": 2.1818182468414307,
            "q4": 3.0,
            "q5": 3.8333332538604736,
        },
        quality_values={"q4": 54_662},
    )


def _diag_4501_q4_value_snapshot() -> dict:
    """Reduced pre-settlement facts from 4501:1758737621594031 R3."""
    return _snapshot(
        map_id=4501,
        total_count=48,
        fixed_counts={"q3": 19, "q5": 8},
        avg_cells={"q1": 2.0, "q4": 4.333333492279053, "q5": 3.875},
        quality_values={"q4": 102_279},
    )


def _diag_2403_sparse_q4_value_snapshot(*, hero: str = "ahmed") -> dict:
    """Reduced pre-settlement facts from 2403:1770351216784255 R5."""
    return _snapshot(
        hero=hero,
        map_id=2403,
        total_count=42,
        fixed_counts={"q1": 11, "q3": 19},
        avg_cells={
            "q1": 1.3636363744735718,
            "q3": 4.3684210777282715,
            "q4": 3.5,
            "q5": 6.666666507720947,
        },
        quality_values={"q4": 40_716, "q5": 156_360},
    )


def _diag_4509_correlated_q5_q6_snapshot() -> dict:
    """Reduced pre-settlement facts from 4509:1770351167898215 R5."""
    return _snapshot(
        total_count=56,
        fixed_counts={"q1": 23, "q3": 11},
        avg_cells={
            "q1": 2.04347825050354,
            "q3": 2.2727272510528564,
            "q4": 4.833333492279053,
            "q5": 8.199999809265137,
        },
        quality_values={"q4": 97_082},
        map_id=4509,
    )


def test_2501_exact_q4_value_and_private_avg_improve_center_without_false_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _diag_2501_q4_value_snapshot()
    refined = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(Q4_EXACT_VALUE_LIKELIHOOD_REFINEMENT_DISABLE, "1")
    legacy = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert refined["quality_count_ranges"]["q4"] == [7, 8, 10]
    assert refined["quality_cells_ranges"]["q4"] == [21, 24, 30]
    assert legacy["quality_count_ranges"]["q4"] == [7, 9, 10]
    assert legacy["quality_cells_ranges"]["q4"] == [21, 27, 30]
    assert Q4_EXACT_VALUE_LIKELIHOOD_REFINEMENT_NOTE in refined["notes"]
    assert Q4_EXACT_VALUE_LIKELIHOOD_REFINEMENT_NOTE not in legacy["notes"]
    assert refined["quality_lock"]["q4"]["strength"] == "probabilistic"
    assert {
        (row["count"], row["cells"])
        for row in refined["quality_count_cells_candidates"]["q4"]
    } >= {(7, 21), (8, 24), (9, 27), (10, 30)}
    assert _purple_gold_quality_summary(refined).startswith("紫件 7/8/10 约21/24/30格")


def test_sparse_exact_q4_support_tempers_approximate_likelihood(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _diag_2403_sparse_q4_value_snapshot()
    guarded = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_DISABLE, "1")
    prior_refinement = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert guarded["quality_count_ranges"]["q4"] == [6, 8, 8]
    assert guarded["quality_cells_ranges"]["q4"] == [21, 28, 28]
    assert prior_refinement["quality_count_ranges"]["q4"] == [6, 6, 8]
    assert prior_refinement["quality_cells_ranges"]["q4"] == [21, 21, 28]
    assert {
        (row["count"], row["cells"])
        for row in guarded["evidence"]["quality_count_cells_options"]["q4"]
    } == {(4, 14), (6, 21), (8, 28)}
    assert Q4_EXACT_VALUE_LIKELIHOOD_REFINEMENT_NOTE in guarded["notes"]
    assert Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_NOTE in guarded["notes"]
    assert Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_NOTE not in prior_refinement["notes"]
    assert guarded["quality_lock"]["q4"]["strength"] == "probabilistic"


def test_sparse_guard_keeps_broad_4501_support_and_quotes_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _diag_4501_q4_value_snapshot()
    default = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert default == rollback
    assert default["quality_count_ranges"]["q4"] == [9, 12, 15]
    assert default["quality_cells_ranges"]["q4"] == [39, 52, 65]
    assert Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_NOTE not in default["notes"]


def test_sparse_guard_ignores_q4_domain_rows_unreachable_in_joint_posterior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _diag_4509_correlated_q5_q6_snapshot()
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
    assert default["quality_count_candidates"]["q5"] == [
        {"value": 5, "prob": 0.9445},
        {"value": 10, "prob": 0.0555},
    ]
    assert default["quality_count_candidates"]["q6"] == [
        {"value": 5, "prob": 0.9445},
        {"value": 0, "prob": 0.0555},
    ]
    assert [
        default["conservative"],
        default["balanced"],
        default["aggressive"],
    ] == [1_380_342, 1_483_690, 1_587_039]
    assert {
        (row["count"], row["cells"])
        for row in default["evidence"]["quality_count_cells_options"]["q4"]
    } == {(6, 29), (12, 58), (18, 87)}
    assert Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_NOTE not in default["notes"]


@pytest.mark.parametrize("hero", ["aisha", "gabriela", "ethan", "maria"])
def test_sparse_guard_is_ahmed_only(
    monkeypatch: pytest.MonkeyPatch,
    hero: str,
) -> None:
    snapshot = _diag_2403_sparse_q4_value_snapshot(hero=hero)
    default = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(Q4_SPARSE_EXACT_VALUE_LIKELIHOOD_GUARD_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert default == rollback


def test_4501_strong_q4_posterior_keeps_neighboring_joint_states_visible() -> None:
    result = run_reference_engine(
        _diag_4501_q4_value_snapshot(), max_combos=50_000
    ).as_dict()

    assert result["quality_count_ranges"]["q4"] == [9, 12, 15]
    assert result["quality_cells_ranges"]["q4"] == [39, 52, 65]
    assert result["quality_lock"]["q4"]["strength"] == "probabilistic"
    assert Q4_EXACT_VALUE_JOINT_ANTILOCK_NOTE in result["notes"]
    assert {
        (row["count"], row["cells"])
        for row in result["quality_count_cells_candidates"]["q4"]
    } >= {(9, 39), (12, 52), (15, 65)}


def test_q4_likelihood_refinement_is_ahmed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _diag_2501_q4_value_snapshot(hero="aisha")
    default = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(Q4_EXACT_VALUE_LIKELIHOOD_REFINEMENT_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert default == rollback


def test_2501_zero_red_arm_also_widens_correlated_gold_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(
        total_count=50,
        fixed_counts={"q3": 17, "q4": 9},
        avg_cells={
            "q1": 2.0714285373687744,
            "q4": 4.333333492279053,
            "q5": 5.400000095367432,
        },
    )
    repaired = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_Q1_RESIDUAL_DISPLAY_ANTILOCK_DISABLE, "1")
    legacy = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert repaired["quality_count_ranges"]["q5"] == [5, 5, 10]
    assert repaired["quality_cells_ranges"]["q5"] == [27, 27, 54]
    assert repaired["quality_count_ranges"]["q6"] == [0, 5, 5]
    assert legacy["quality_count_ranges"]["q5"] == [5, 5, 5]
    assert legacy["quality_count_ranges"]["q6"] == [5, 5, 5]
    assert [
        repaired["conservative"],
        repaired["balanced"],
        repaired["aggressive"],
    ] == [legacy["conservative"], legacy["balanced"], legacy["aggressive"]]


def _diag_2408_public_q4_avg_snapshot(*, with_private_avg_cells: bool) -> dict:
    """Reduced pre-settlement facts from 2408:1758737639481992."""
    avg_cells = {}
    quality_cells = {}
    if with_private_avg_cells:
        avg_cells = {
            "q1": 1.8333333730697632,
            "q4": 2.5,
            "q5": 3.3333332538604736,
        }
        quality_cells = {"q5": 10}
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2408,
                "phase": "bidding",
                "round": 3 if with_private_avg_cells else 1,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "info_id": 200036,
                            "semantic": "q4_avg_value",
                            "kind": "avg_value",
                            "quality": 4,
                            "value": 5373.875,
                        }
                    ]
                }
            },
        },
        "structured_ref_inputs": {
            "total_count": 32,
            "fixed_counts": {"q3": 13},
            "avg_cells": avg_cells,
            "quality_cells": quality_cells,
        },
        "public_info_rows": [
            {
                "info_id": 200036,
                "map_id": 2408,
                "value": 5373.875,
                "value_field": 9,
            }
        ],
    }


def test_2408_public_q4_avg_locks_count_and_total_but_not_cells() -> None:
    result = run_reference_engine(
        _diag_2408_public_q4_avg_snapshot(with_private_avg_cells=False),
        max_combos=50_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert result["quality_cells_ranges"]["q4"] == [18, 19, 20]
    assert _quality_total_from_public_avg(5373.875, 8) == 42_991

    pairs, _id_rows, status = _validated_q4_catalog()
    assert status == "ok"
    solve_status, reachable = _cached_exact_q4_count_cells_candidates(
        pairs,
        42_991,
        None,
        8,
        (),
        8,
        None,
    )
    assert solve_status == "ok"
    assert len(reachable) == 27
    assert {cells for count, cells in reachable if count == 8} == set(range(9, 36))


def test_2408_private_q4_avg_cells_then_locks_correlated_grid() -> None:
    result = run_reference_engine(
        _diag_2408_public_q4_avg_snapshot(with_private_avg_cells=True),
        max_combos=50_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert result["quality_cells_ranges"]["q4"] == [20, 20, 20]
    assert {
        (row["count"], row["cells"])
        for row in result["evidence"]["quality_count_cells_options"]["q4"]
    } == {(8, 20), (16, 40)}
