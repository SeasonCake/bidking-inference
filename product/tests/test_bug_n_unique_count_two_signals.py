"""BUG-N gate (#10): 两个信息就该枚举唯一解 —— 但只在硬约束真的把候选压到一个时才锁.

作者 (2026-07-11): "如果价格、均格或者总格、件数这四个有两个满足的情况下,触发枚举 …… 能不能做一个排列,
直接锁定唯一解呢(目前很多群友在说这个)".

The (均格 + 总价) pair on one tier admits these HARD constraints:
  1. integer-product: avg × count must compose an integer cell total
  2. physical ceiling: the tier cannot hold more items than the vault has left
  3. catalog price band: total_value / count must be a price a collectible of that tier can carry
     (q5 spans 7,800..199,900 — a count implying 3,000/件 or 400,000/件 is impossible, not unlikely)
The legacy gate stops there.  The q5 path now additionally solves the exact repeat-allowed catalog
multiset and locks only a unique (count, cells) pair. 5606's coarse 5/10/15 becomes exactly 10/72;
bounded or unavailable catalog work fails open. Kill the exact layer with
BIDKING_DISABLE_Q5_EXACT_VALUE_CELL_SOLVER=1; the broad two-signal kill remains unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "hero_ref" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "hero_ref" / "src"))

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    Q5_EXACT_VALUE_CELLS_NOTE,
    UNIQUE_COUNT_TWO_SIGNAL_NOTE,
    _correlated_q5_known_item_facts,
    _exact_count_cells_candidates_from_catalog,
    _exact_q5_count_cells_candidates,
    _catalog_value_bounds_by_quality,
    _unique_count_from_avg_and_total_value,
    extract_evidence,
    prepare_reference_engine_snapshot,
)

FIXTURE_5606 = ROOT / "data" / "samples" / "recordings_inbox_20260711_desktop_diag5606_aisha"


def test_catalog_price_band_prunes_impossible_counts() -> None:
    bounds = _catalog_value_bounds_by_quality()
    if not bounds:
        pytest.skip("Item.txt catalog absent")
    lo, hi = bounds[5]
    assert lo == 7800 and hi == 199900  # gold collectibles

    # (a) the PRICE BAND alone does the pruning: avg 4.0 admits every count (4×n is always integer),
    # but with 500,000 of gold, 1 item -> 500k/件 and 2 items -> 250k/件 are both dearer than ANY gold
    # collectible; only 3 items (166,667/件) can exist -> unique.
    assert _unique_count_from_avg_and_total_value("q5", 4.0, 500_000, 3) == 3

    # (b) the MULTIPLE rule + the ceiling do it: avg 3.5 admits only even counts, and a 3-item ceiling
    # leaves just 2 (195,000/件, a legal gold price).
    assert _unique_count_from_avg_and_total_value("q5", 3.5, 390_000, 3) == 2

    # (c) several legal counts survive -> no lock (the engine must not guess)
    assert _unique_count_from_avg_and_total_value("q5", 4.0, 40_000, 5) is None

    # (d) nothing survives (a price no gold can carry at any count) -> no lock either
    assert _unique_count_from_avg_and_total_value("q5", 4.0, 1_000, 10) is None


def test_ambiguity_never_locks() -> None:
    if not _catalog_value_bounds_by_quality():
        pytest.skip("Item.txt catalog absent")
    # 5606: 金总价 552,983 + 均格 7.2 -> counts 5/10/15 (multiples of 5); 110k / 55k / 37k per item are
    # ALL inside the gold band -> ambiguous -> must NOT lock (the truth, 10, is picked softly instead).
    assert _unique_count_from_avg_and_total_value("q5", 7.2, 552_983, 12) is None
    assert _unique_count_from_avg_and_total_value("q5", 7.2, 552_983, 15) is None


def test_exact_q5_multiset_resolves_5606_to_truth_pair() -> None:
    status, candidates = _exact_q5_count_cells_candidates(552_983, 7.2, 15)
    assert status == "ok"
    assert candidates == ((10, 72),)


def test_exact_q5_multiset_keeps_multiple_reachable_pairs_ambiguous() -> None:
    status, candidates = _exact_q5_count_cells_candidates(107_225, 3.0, 34)
    assert status == "ok"
    assert candidates == ((3, 9), (4, 12))


def test_tool_q5_total_and_avg_lock_unique_exact_pair_in_evidence() -> None:
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 5606, "phase": "bidding", "round": 3},
            "actions": {
                "results": [
                    {"action_id": 100113, "result": 6.25},
                    {"action_id": 100125, "result": 416_163},
                ]
            },
        },
        "structured_ref_inputs": {"total_count": 55},
    }
    ev = extract_evidence(snapshot)
    assert ev.fixed_counts.get("q5") == 8
    assert ev.quality_cells.get("q5") == 50
    assert f"{Q5_EXACT_VALUE_CELLS_NOTE}:ok:1" in ev.source_notes


def test_exact_q5_kill_switch_restores_legacy_coarse_gate(monkeypatch) -> None:
    monkeypatch.setenv("BIDKING_DISABLE_Q5_EXACT_VALUE_CELL_SOLVER", "1")
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 5606, "phase": "bidding", "round": 3},
            "actions": {
                "results": [
                    {"action_id": 100113, "result": 6.25},
                    {"action_id": 100125, "result": 416_163},
                ]
            },
        },
        "structured_ref_inputs": {"total_count": 55},
    }
    ev = extract_evidence(snapshot)
    assert ev.fixed_counts.get("q5") is None
    assert not any(str(n).startswith(Q5_EXACT_VALUE_CELLS_NOTE) for n in ev.source_notes)


def test_tool_q5_exact_lock_uses_correlated_known_item_residual() -> None:
    action_rows = [
        {"action_id": 100113, "result": 1.5},
        {"action_id": 100125, "result": 26_400},
        {
            "action_id": 100161,
            "revealed_items_detail": [
                {"runtime_id": 7, "local_index": 34, "quality": 5, "cells": 2}
            ],
        },
        {
            "action_id": 100163,
            "revealed_items_detail": [
                {"runtime_id": 7, "local_index": 34, "value": 18_600}
            ],
        },
    ]
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 4},
            "actions": {"results": action_rows},
        },
        "action_result_rows": action_rows,
        "structured_ref_inputs": {"total_count": 8},
    }
    ev = extract_evidence(snapshot)
    assert ev.q5_known_item_facts == (("local:34", 2, 18_600),)
    assert ev.fixed_counts.get("q5") == 2
    assert ev.quality_cells.get("q5") == 3


def test_exact_solver_allows_repeated_catalog_items() -> None:
    status, candidates = _exact_count_cells_candidates_from_catalog(
        ((7_800, 1),),
        15_600,
        1.0,
        2,
    )
    assert status == "ok"
    assert candidates == ((2, 2),)


@pytest.mark.parametrize(
    ("total_value", "expected"),
    [
        (7_800, ((1, 1),)),
        (15_600, ((2, 2),)),
        (29_025, ((1, 1), (1, 4), (2, 2))),
    ],
)
def test_exact_solver_total_value_only_enumerates_full_gold_multiset(
    total_value: int,
    expected: tuple[tuple[int, int], ...],
) -> None:
    pairs = engine._validated_q5_catalog()[0]
    if not pairs:
        pytest.skip("validated q5 catalog absent")
    assert _exact_count_cells_candidates_from_catalog(
        pairs,
        total_value,
        None,
        64,
    ) == ("ok", expected)


def test_q5_total_value_only_projects_unique_count_and_cells() -> None:
    ev = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 3},
                "actions": {
                    "results": [
                        {"action_id": 100125, "tool": "金品总价", "result": 15_600}
                    ]
                },
            },
        }
    )
    assert ev.fixed_counts.get("q5") == 2
    assert ev.quality_cells.get("q5") == 2
    assert "action_100125_q5_value_sum" in ev.source_notes
    assert f"{Q5_EXACT_VALUE_CELLS_NOTE}:ok:1" in ev.source_notes


def test_q5_ambiguous_total_value_projects_nothing() -> None:
    ev = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 3},
                "actions": {"results": []},
            },
            "structured_ref_inputs": {"quality_values": {"q5": 29_025}},
        }
    )
    assert ev.fixed_counts.get("q5") is None
    assert ev.quality_cells.get("q5") is None
    assert f"{Q5_EXACT_VALUE_CELLS_NOTE}:ok:3" in ev.source_notes


def test_q5_known_count_and_total_value_projects_unique_cells() -> None:
    ev = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 3},
                "actions": {"results": []},
            },
            "structured_ref_inputs": {
                "fixed_counts": {"q5": 2},
                "quality_values": {"q5": 17_045},
            },
        }
    )
    assert ev.fixed_counts.get("q5") == 2
    assert ev.quality_cells.get("q5") == 2


def test_public_q5_count_and_action_total_value_project_unique_cells() -> None:
    ev = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 3},
                "constraints": {
                    "public_info": {
                        "public_numeric_facts": [
                            {"semantic": "q5_item_count", "value": 2}
                        ]
                    }
                },
                "actions": {
                    "results": [
                        {"action_id": 100125, "tool": "金品总价", "result": 17_045}
                    ]
                },
            },
        }
    )
    assert ev.fixed_counts.get("q5") == 2
    assert ev.quality_cells.get("q5") == 2
    assert "public_q5_count" in ev.source_notes


def test_q5_known_count_and_total_value_keeps_ambiguous_cells_unlocked() -> None:
    ev = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 3},
                "actions": {"results": []},
            },
            "structured_ref_inputs": {
                "fixed_counts": {"q5": 1},
                "quality_values": {"q5": 25_875},
            },
        }
    )
    assert ev.fixed_counts.get("q5") == 1
    assert ev.quality_cells.get("q5") is None
    assert f"{Q5_EXACT_VALUE_CELLS_NOTE}:ok:2" in ev.source_notes


def test_public_fractional_q5_avg_projects_count_cells_and_total_value() -> None:
    ev = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 3},
                "constraints": {
                    "public_info": {
                        "public_avg_values": [
                            {
                                "semantic": "q5_avg_value",
                                "kind": "avg_value",
                                "quality": 5,
                                "value": 8_522.5,
                            }
                        ]
                    }
                },
                "actions": {"results": []},
            },
            "structured_ref_inputs": {"total_count": 3},
        }
    )
    assert ev.fixed_counts.get("q5") == 2
    assert ev.quality_cells.get("q5") == 2
    assert ev.quality_values.get("q5") == 17_045


def test_public_integer_q5_avg_keeps_repeated_item_counts_ambiguous() -> None:
    ev = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 3},
                "constraints": {
                    "public_info": {
                        "public_avg_values": [
                            {
                                "semantic": "q5_avg_value",
                                "kind": "avg_value",
                                "quality": 5,
                                "value": 7_800.0,
                            }
                        ]
                    }
                },
                "actions": {"results": []},
            },
            "structured_ref_inputs": {"total_count": 3},
        }
    )
    assert ev.fixed_counts.get("q5") is None
    assert ev.quality_cells.get("q5") is None
    assert ev.quality_values.get("q5") is None


def test_q5_total_value_only_kill_switch_fails_open(monkeypatch) -> None:
    monkeypatch.setenv("BIDKING_DISABLE_Q5_EXACT_VALUE_CELL_SOLVER", "1")
    ev = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 3},
                "actions": {"results": []},
            },
            "structured_ref_inputs": {"quality_values": {"q5": 15_600}},
        }
    )
    assert ev.fixed_counts.get("q5") is None
    assert ev.quality_cells.get("q5") is None


def test_exact_solver_subtracts_correlated_known_item_residual() -> None:
    pairs = engine._validated_q5_catalog()[0]
    if not pairs:
        pytest.skip("validated q5 catalog absent")
    status, candidates = _exact_count_cells_candidates_from_catalog(
        pairs,
        26_400,
        1.5,
        8,
        known_items=((2, 18_600),),
    )
    assert status == "ok"
    assert candidates == ((2, 3),)


def test_exact_solver_no_solution_and_full_range_guard_fail_open() -> None:
    pairs = engine._validated_q5_catalog()[0]
    if not pairs:
        pytest.skip("validated q5 catalog absent")
    assert _exact_count_cells_candidates_from_catalog(
        pairs, 1_000, 4.0, 10
    ) == ("ok", ())
    # A 65-count theoretical range must not be silently truncated to 64 and called unique.
    assert _exact_count_cells_candidates_from_catalog(
        ((1, 1),), 65, 1.0, 65
    ) == ("complexity_guard", ())


def test_q5_catalog_unavailable_never_hard_locks(monkeypatch) -> None:
    monkeypatch.setattr(
        engine,
        "_validated_q5_catalog",
        lambda: ((), (), "catalog_mismatch"),
    )
    assert _exact_q5_count_cells_candidates(552_983, 7.2, 15) == (
        "catalog_mismatch",
        (),
    )


def test_correlated_known_item_facts_merge_and_deduplicate_physical_ids() -> None:
    snapshot = {
        "action_result_rows": [
            {
                "action_id": 100161,
                "revealed_items_detail": [
                    {"runtime_id": 7, "local_index": 34, "quality": 5, "cells": 2},
                    {"runtime_id": 8, "local_index": 35, "quality": 5, "cells": 2},
                ],
            },
            {
                "action_id": 100163,
                "revealed_items_detail": [
                    {"runtime_id": 7, "local_index": 34, "value": 18_600},
                    {"runtime_id": 8, "local_index": 35, "value": 18_600},
                    {"runtime_id": 7, "local_index": 34, "value": 18_600},
                ],
            },
        ]
    }
    facts = _correlated_q5_known_item_facts(snapshot)
    assert len(facts) == 2
    assert {(cells, value) for _, cells, value in facts} == {(2, 18_600)}


def test_independent_cell_and_value_rows_are_not_correlated() -> None:
    snapshot = {
        "action_result_rows": [
            {
                "action_id": 999001,
                "revealed_items_detail": [
                    {"runtime_id": 1, "quality": 5, "cells": 2},
                    {"runtime_id": 2, "quality": 5, "value": 18_600},
                ],
            }
        ]
    }
    assert _correlated_q5_known_item_facts(snapshot) == ()


def test_value_only_same_price_different_cells_is_not_an_exact_fact() -> None:
    # q5 value 29,025 exists as both 1-cell and 4-cell items.
    snapshot = {
        "action_result_rows": [
            {
                "action_id": 100163,
                "revealed_items_detail": [{"local_index": 9, "value": 29_025}],
            }
        ]
    }
    assert _correlated_q5_known_item_facts(snapshot) == ()


def test_kill_env_disables_the_lock(monkeypatch) -> None:
    if not _catalog_value_bounds_by_quality():
        pytest.skip("Item.txt catalog absent")
    monkeypatch.setenv("BIDKING_DISABLE_UNIQUE_COUNT_TWO_SIGNALS", "1")
    assert _unique_count_from_avg_and_total_value("q5", 4.0, 40_000, 2) is None


@pytest.mark.skipif(
    not (FIXTURE_5606 / "r04_bidding.json").exists(),
    reason="5606 snapshot absent (gitignored) — local-only gate",
)
def test_5606_replay_exactly_locks_gold_truth_pair() -> None:
    snap = prepare_reference_engine_snapshot(
        json.loads((FIXTURE_5606 / "r04_bidding.json").read_text(encoding="utf-8"))
    )
    ev = extract_evidence(snap)
    assert ev.fixed_counts.get("q5") == 10
    assert ev.quality_cells.get("q5") == 72
    assert f"{Q5_EXACT_VALUE_CELLS_NOTE}:ok:1" in ev.source_notes
    assert any(
        str(n).startswith(f"{UNIQUE_COUNT_TWO_SIGNAL_NOTE}:q5:10:72")
        for n in ev.source_notes
    )


@pytest.mark.skipif(
    not (FIXTURE_5606 / "r04_bidding.json").exists(),
    reason="5606 snapshot absent (gitignored) — local-only gate",
)
def test_exact_q5_kill_switch_restores_legacy_5606_behavior(monkeypatch) -> None:
    monkeypatch.setenv("BIDKING_DISABLE_Q5_EXACT_VALUE_CELL_SOLVER", "1")
    snap = prepare_reference_engine_snapshot(
        json.loads((FIXTURE_5606 / "r04_bidding.json").read_text(encoding="utf-8"))
    )
    ev = extract_evidence(snap)
    assert ev.fixed_counts.get("q5") is None
    assert not any(str(n).startswith(Q5_EXACT_VALUE_CELLS_NOTE) for n in ev.source_notes)


def test_public_soft_q5_avg_cells_never_triggers_exact_lock() -> None:
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 5606, "phase": "bidding", "round": 4},
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "q5_avg_cells", "value": 7.2}
                    ]
                }
            },
            "actions": {"results": [{"action_id": 100125, "result": 552_983}]},
        },
        "structured_ref_inputs": {"total_count": 41},
    }
    ev = extract_evidence(snapshot)
    assert ev.fixed_counts.get("q5") is None
    assert ev.soft_avg_cell_keys == frozenset({"q5"})
    # The public avg-cells itself remains soft. The separate high total-value-only path is inspected
    # but fails open at its bounded live guard.
    assert f"{Q5_EXACT_VALUE_CELLS_NOTE}:value_only_guard:0" in ev.source_notes
