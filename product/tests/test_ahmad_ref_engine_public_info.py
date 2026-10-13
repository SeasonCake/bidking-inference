from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
FATBEANS_SAMPLE_DIR = ROOT / "data" / "samples" / "fatbeans"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    AISHA_LAYOUT_FOOTROOM_CAP_NOTE,
    AISHA_LAYOUT_FOOTROOM_MULT_NOTE,
    AISHA_LAYOUT_FOOTROOM_NOTE,
    AISHA_LAYOUT_FOOTROOM_SKIP_NOTE,
    AISHA_LAYOUT_FOOTROOM_SPARSE_NOTE,
    AISHA_LAYOUT_GRID_HINT_NOTE,
    EXACT_TOTAL_Q1_AVG_DOMAIN_NOTE,
    HARD_TOTAL_JOINT_CELLS_DISABLE,
    HARD_TOTAL_JOINT_CELLS_NOTE,
    PINNED_QUALITY_CELLS_SPARSE_PRIOR_NOTE,
    Q5_CATALOG_CELL_COMPOSE_DISABLE,
    Q5_CATALOG_CELL_COMPOSE_NOTE,
    Q5_KNOWN_ITEM_COMPOSE_DISABLE,
    Q5_KNOWN_ITEM_COMPOSE_NOTE,
    Q6_CATALOG_CELL_COMPOSE_DISABLE,
    RESIDUAL_AVG_CELLS_NOTE,
    TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE,
    _apply_aisha_pinned_nonred_room_hi,
    _aisha_low_tier_count_upper,
    _aisha_red_keep_evidence_ramp,
    _avg_count_from_cells,
    _avg_value_count_matches_for_public_evidence,
    _avg_value_total_from_count,
    _can_compose_grid_total_for_quality,
    _can_compose_grid_total_with_q6_catalog,
    _catalog_cell_sizes_by_table_quality,
    _catalog_min_total_value_for_quality_count_cells,
    _catalog_quality_grid_ceiling,
    _known_quality_value_total_floor,
    _prior_count_values,
    _revealed_giant_red_count_bias_log,
    _avg_grid_options,
    _fit_grids_to_total_target,
    can_compose_grid_total,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from layout_depth_policy import apply_aisha_geometry_grid_range  # noqa: E402

CAPTURE_AVG_CELL_FIXTURES = [
    pytest.param(
        2.909090995788574,
        11,
        32,
        "2.90",
        "fatbeans_valid_aisha_2401_1rounds_2401_1367517774693221_0001.json public_info 200013",
        id="capture_q4_2p90_ratio_11x32",
    ),
    pytest.param(
        2.4285714626312256,
        7,
        17,
        "2.42",
        "recordings/data6/data/logs/live/latest_snapshot.json bucket.q3.avg_cells",
        id="capture_q3_2p42_ratio_7x17",
    ),
    pytest.param(
        2.09,
        11,
        23,
        "2.09",
        "display reading when packet stores rounded 2.09 (2.09×11→23)",
        id="display_q5_2p09_literal_11x23",
    ),
    pytest.param(
        23 / 11,
        11,
        23,
        "2.09",
        "exact ratio float 23/11 from detailed packet capture",
        id="capture_ratio_23_over_11",
    ),
    pytest.param(
        3.43,
        16,
        55,
        "3.43",
        "display reading 3.43 (3.43×16→55, shipwreck screenshot scenario)",
        id="display_q4_3p43_literal_16x55",
    ),
    pytest.param(
        55 / 16,
        16,
        55,
        "3.43",
        "exact ratio float 55/16 from detailed packet capture",
        id="capture_ratio_55_over_16",
    ),
]

FATBEANS_Q4_290_SAMPLE = (
    FATBEANS_SAMPLE_DIR
    / "fatbeans_valid_aisha_2401_1rounds_2401_1367517774693221_0001.json"
)


def _snapshot(
    *,
    hero: str = "ahmed",
    map_id: int = 2402,
    phase: str = "bidding",
    structured_ref_inputs: dict | None = None,
    public_info: dict | None = None,
    public_rows: list[dict] | None = None,
) -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": map_id,
                "phase": phase,
            },
            "constraints": {
                "public_info": public_info or {},
            },
        },
        "structured_ref_inputs": (
            {"total_count": 34} if structured_ref_inputs is None else structured_ref_inputs
        ),
        "public_info_rows": public_rows or [],
    }


def test_ref_engine_uses_public_quality_reveal_as_min_counts() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_rows=[
                {
                    "info_id": 200028,
                    "revealed_items_detail": [
                        {"runtime_id": 1, "quality": 6},
                        {"runtime_id": 2, "quality": 6},
                        {"runtime_id": 3, "quality": 4},
                        {"runtime_id": 4, "quality": 1},
                        {"runtime_id": 4, "quality": 1},
                    ],
                }
            ],
        )
    )

    assert evidence.min_counts["q6"] == 2
    assert evidence.min_counts["q4"] == 1
    assert evidence.min_counts["q1"] == 1
    assert "public_quality_reveal_min_counts" in evidence.source_notes


def test_ref_engine_public_red_reveal_min_count_constrains_enumeration() -> None:
    result = run_reference_engine(
        _snapshot(
            public_rows=[
                {
                    "info_id": 200028,
                    "revealed_items_detail": [
                        {"runtime_id": 1, "quality": 6},
                        {"runtime_id": 2, "quality": 6},
                        {"runtime_id": 2, "quality": 6},
                    ],
                }
            ],
        ),
        max_combos=60000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["evidence"]["min_counts"]["q6"] == 2
    assert result["quality_count_ranges"]["q6"][0] >= 2
    assert "public_quality_reveal_min_counts" in result["notes"]


def test_ref_engine_public_red_reveal_value_and_cells_are_floors() -> None:
    result = run_reference_engine(
        _snapshot(
            structured_ref_inputs={
                "total_count": 48,
                "fixed_counts": {"q1": 9, "q3": 15, "q4": 17, "q5": 5},
                "quality_cells": {"q1": 15, "q3": 49, "q4": 48, "q5": 24},
                "avg_cells": {
                    "q3": 49 / 15,
                    "q4": 48 / 17,
                    "q5": 24 / 5,
                },
            },
            public_rows=[
                {
                    "info_id": 200023,
                    "revealed_items_detail": [
                        {
                            "runtime_id": 1425860479021732,
                            "quality": 6,
                            "value": 452800,
                            "shape_code": 53,
                            "cells": 15,
                        }
                    ],
                }
            ],
        ),
        max_combos=60000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["fixed_counts"]["q6"] == 2
    assert result["evidence"]["quality_cell_floors"]["q6"] == 15.0
    assert result["evidence"]["quality_value_floors"]["q6"] == 452800.0
    assert min(result["red_cells_range"]) >= 15
    assert min(result["red_value_range"]) >= 452800
    assert "public_quality_reveal_q6_cell_floor" in result["notes"]
    assert "public_quality_reveal_q6_value_floor" in result["notes"]


def test_ref_engine_partial_known_red_value_includes_unknown_estimate() -> None:
    result = run_reference_engine(
        _snapshot(
            structured_ref_inputs={
                "total_count": 50,
                "fixed_counts": {"q1": 10, "q3": 24, "q4": 10, "q5": 4, "q6": 2},
                "quality_cells": {"q1": 15, "q3": 49, "q4": 48, "q5": 24},
                "avg_cells": {
                    "q3": 49 / 24,
                    "q4": 48 / 10,
                    "q5": 24 / 4,
                },
            },
            public_rows=[
                {
                    "info_id": 200023,
                    "revealed_items_detail": [
                        {
                            "runtime_id": 1425860479021733,
                            "quality": 6,
                            "value": 390000,
                            "shape_code": 11,
                            "cells": 1,
                        }
                    ],
                }
            ],
        ),
        max_combos=60000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["quality_value_floors"]["q6"] == 390000.0
    assert result["evidence"]["quality_value_floor_item_counts"]["q6"] == 1
    rv10, rv50, rv90 = result["red_value_range"]
    assert rv10 is not None and rv50 is not None and rv90 is not None
    assert rv10 > 390000
    assert rv50 > 390000
    assert rv10 >= 550000  # 390k known + at least one default red item floor
    assert rv10 <= rv50 <= rv90
    assert min(result["red_value_range"]) >= 390000


def test_ref_engine_partial_known_red_data6_style_above_known_not_flat() -> None:
    """EXECUTION_NOTES §43 data6: one 452800/15-cell red, q6=2; settlement q6≈520900."""
    result = run_reference_engine(
        _snapshot(
            map_id=2309,
            structured_ref_inputs={
                "total_count": 48,
                "fixed_counts": {"q1": 9, "q3": 15, "q4": 17, "q5": 5},
                "quality_cells": {"q1": 15, "q3": 49, "q4": 48, "q5": 24},
                "avg_cells": {
                    "q3": 49 / 15,
                    "q4": 48 / 17,
                    "q5": 24 / 5,
                },
            },
            public_rows=[
                {
                    "info_id": 200023,
                    "revealed_items_detail": [
                        {
                            "runtime_id": 1425860479021732,
                            "quality": 6,
                            "value": 452800,
                            "shape_code": 53,
                            "cells": 15,
                        }
                    ],
                }
            ],
        ),
        max_combos=60000,
    ).as_dict()

    rv10, rv50, rv90 = result["red_value_range"]
    assert rv10 is not None and rv50 is not None and rv90 is not None
    assert rv10 > 452800
    assert rv50 > 452800
    assert rv10 >= 612800  # 452800 + one default red (~160k)
    assert rv10 <= rv50 <= rv90
    assert min(result["red_value_range"]) >= 452800


def test_ref_engine_uses_public_bucket_outline_as_exact_count_and_cells() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_rows=[
                {
                    "info_id": 200001,
                    "revealed_items_detail": [
                        {"runtime_id": 1, "quality": 4, "shape_code": 23},
                        {"runtime_id": 2, "shape_key": 11},
                    ],
                }
            ],
        )
    )

    assert evidence.fixed_counts["q4"] == 2
    assert evidence.min_counts["q4"] == 2
    assert evidence.quality_cells["q4"] == 7.0
    assert evidence.avg_cells["q4"] == 3.5
    assert "public_bucket_outline_q4_count" in evidence.source_notes
    assert "public_bucket_outline_q4_cells" in evidence.source_notes
    assert "public_quality_reveal_min_counts" not in evidence.source_notes


def test_ref_engine_uses_public_red_bucket_outline_as_exact_count_and_cells() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_rows=[
                {
                    "info_id": 200003,
                    "revealed_items_detail": [
                        {"runtime_id": 1, "shape_code": 23},
                        {"runtime_id": 2, "shape_key": 11},
                    ],
                }
            ],
        )
    )

    assert evidence.fixed_counts["q6"] == 2
    assert evidence.min_counts["q6"] == 2
    assert evidence.quality_cells["q6"] == 7.0
    assert evidence.avg_cells["q6"] == 3.5
    assert "public_bucket_outline_q6_count" in evidence.source_notes
    assert "public_bucket_outline_q6_cells" in evidence.source_notes
    assert "public_quality_reveal_min_counts" not in evidence.source_notes


def test_ref_engine_public_bucket_outline_does_not_override_structured_exact_inputs() -> None:
    evidence = extract_evidence(
        _snapshot(
            structured_ref_inputs={
                "total_count": 21,
                "field_updates": [
                    {"path": ["bucket", "4", "count"], "value": 1},
                    {"path": ["bucket", "4", "total_cells"], "value": 4},
                ],
            },
            public_rows=[
                {
                    "info_id": 200001,
                    "revealed_items_detail": [
                        {"runtime_id": 1, "quality": 4, "shape_code": 23},
                        {"runtime_id": 2, "quality": 4, "shape_key": 11},
                    ],
                }
            ],
        )
    )

    assert evidence.fixed_counts["q4"] == 1
    assert evidence.min_counts["q4"] == 1
    assert evidence.quality_cells["q4"] == 4.0
    assert "field_update_q4_count" in evidence.source_notes
    assert "field_update_q4_cells" in evidence.source_notes
    assert "public_bucket_outline_q4_count_conflict" in evidence.source_notes
    assert "public_bucket_outline_q4_count" not in evidence.source_notes


def test_ref_engine_does_not_treat_random_quality_reveal_shape_as_bucket_total() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_rows=[
                {
                    "info_id": 200028,
                    "revealed_items_detail": [
                        {"runtime_id": 1, "quality": 4, "shape_code": 23},
                    ],
                }
            ],
        )
    )

    assert evidence.min_counts.get("q4", 0) == 0
    assert "q4" not in evidence.fixed_counts
    assert "q4" not in evidence.quality_cells
    assert "coarse_quality_reveal_min_counts" not in evidence.source_notes
    assert "public_bucket_outline_q4_count" not in evidence.source_notes


def test_ref_engine_uses_structured_hero_when_context_is_unknown() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="?",
            map_id=2404,
            structured_ref_inputs={
                "hero": "victor",
                "total_count": 21,
            },
        )
    )

    assert evidence.hero == "victor"
    assert "structured_hero" in evidence.source_notes


def test_ref_engine_duplicate_total_count_sources_do_not_add_counts() -> None:
    evidence = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "ahmed", "map_id": 2401, "phase": "bidding"},
                "actions": {"results": [{"action_id": 100204, "result": 38}]},
                "constraints": {
                    "public_info": {
                        "public_numeric_facts": [
                            {"semantic": "total_item_count", "kind": "count", "value": 38}
                        ]
                    }
                },
            },
            "structured_ref_inputs": {
                "field_updates": [
                    {"path": ["session", "total_item_count"], "value": 38}
                ]
            },
        }
    )

    assert evidence.total_count == 38
    assert "field_update_total_count" in evidence.source_notes
    assert "action_100204_total_count" in evidence.source_notes
    assert "public_total_item_count" in evidence.source_notes
    assert not any("conflicts_total_count" in note for note in evidence.source_notes)


def test_ref_engine_duplicate_total_count_sources_log_conflicts() -> None:
    evidence = extract_evidence(
        {
            "ui_contract": {
                "context": {"hero": "ahmed", "map_id": 2401, "phase": "bidding"},
                "actions": {"results": [{"action_id": 100204, "result": 38}]},
                "constraints": {
                    "public_info": {
                        "public_numeric_facts": [
                            {"semantic": "total_item_count", "kind": "count", "value": 39}
                        ]
                    }
                },
            },
            "structured_ref_inputs": {
                "field_updates": [
                    {"path": ["session", "total_item_count"], "value": 38}
                ]
            },
        }
    )

    assert evidence.total_count == 39
    assert "public_total_item_count_conflicts_total_count:38->39" in evidence.source_notes


def test_ref_engine_random_avg_value_soft_floor_raises_low_prior() -> None:
    base = {
        "ui_contract": {
            "context": {"hero": "ahmed", "map_id": 2402, "phase": "bidding"},
        },
        "structured_ref_inputs": {"total_count": 5},
    }
    with_random_avg = {
        "ui_contract": {
            "context": {"hero": "ahmed", "map_id": 2402, "phase": "bidding"},
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "semantic": "random_3_avg_value",
                            "kind": "random_avg_value",
                            "sample_count": 3,
                            "value": 120_000,
                        }
                    ],
                },
            },
        },
        "structured_ref_inputs": {"total_count": 5},
    }

    base_result = run_reference_engine(base, max_combos=60_000).as_dict()
    floor_result = run_reference_engine(with_random_avg, max_combos=60_000).as_dict()

    assert floor_result["balanced"] > base_result["balanced"]
    assert floor_result["red_count_range"][1] >= base_result["red_count_range"][1]
    assert "public_random_avg_value_floor_3:360000" in floor_result["notes"]
    assert "random_value_floor_soft_weight:360000" in floor_result["notes"]


def test_ref_engine_public_quality_avg_value_decimal_filters_count() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=4406,
            structured_ref_inputs={
                "total_count": 10,
                "fixed_counts": {"q1": 0, "q3": 3, "q4": 0, "q5": 4},
                "count_sums": {"q4q5q6": 7},
            },
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 34288.75,
                    }
                ]
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["avg_values"] == {"q5": 34288.75}
    assert result["quality_count_ranges"]["q5"] == [4, 4, 4]
    assert result["red_count_range"] == [3, 3, 3]
    assert "public_q5_avg_value" in result["notes"]


def test_ref_engine_public_numeric_fact_quality_avg_value_uses_same_route() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_info={
                "public_numeric_facts": [
                    {
                        "info_id": 200037,
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 34288.75,
                    }
                ]
            },
        )
    )

    assert evidence.avg_values == {"q5": 34288.75}
    assert "public_q5_avg_value" in evidence.source_notes


def test_ref_engine_2407_gold_avg_value_keeps_only_exact_item_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """37494.125 is 299953/8; float tolerance must not admit the adjacent count 9."""
    monkeypatch.delenv("AHMAD_AVG_VALUE_ULP_K", raising=False)
    monkeypatch.delenv(
        "BIDKING_DISABLE_PRECISE_PUBLIC_AVG_VALUE_ULP_COUNT_LOCK",
        raising=False,
    )
    evidence = extract_evidence(
        _snapshot(
            hero="chenmei",
            map_id=2407,
            structured_ref_inputs={},
            public_info={
                "public_avg_values": [
                    {
                        "info_id": 200037,
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 37494.125,
                    }
                ]
            },
        )
    )
    probabilities = {"q1": 0.30, "q3": 0.25, "q4": 0.20, "q5": 0.16, "q6": 0.09}

    assert evidence.soft_avg_value_keys == frozenset({"q5"})
    assert _avg_value_count_matches_for_public_evidence(
        "q5", 8, 37494.125, evidence.source_notes
    )
    assert not _avg_value_count_matches_for_public_evidence(
        "q5", 9, 37494.125, evidence.source_notes
    )
    assert _avg_value_total_from_count(37494.125, 8) == 299953
    assert _prior_count_values(50, "q5", evidence, probabilities) == [8]


def test_ref_engine_2407_gold_avg_value_locks_product_count_not_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AHMAD_AVG_VALUE_ULP_K", raising=False)
    monkeypatch.delenv(
        "BIDKING_DISABLE_PRECISE_PUBLIC_AVG_VALUE_ULP_COUNT_LOCK",
        raising=False,
    )
    result = run_reference_engine(
        _snapshot(
            hero="chenmei",
            map_id=2407,
            structured_ref_inputs={
                "total_count": 52,
                "min_counts": {"q1": 3, "q3": 7, "q4": 5, "q5": 5, "q6": 2},
                "quality_cells": {"q4": 33},
            },
            public_info={
                "public_avg_values": [
                    {
                        "info_id": 200037,
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 37494.125,
                    }
                ]
            },
        ),
        max_combos=2500,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["quality_count_ranges"]["q5"] == [8, 8, 8]
    q5_cells = result["quality_cells_ranges"]["q5"]
    assert q5_cells[0] <= q5_cells[1] <= q5_cells[2]
    assert len(set(q5_cells)) > 1
    assert result["balanced"] is not None


def test_ref_engine_2407_gold_avg_value_legacy_ulp_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BIDKING_DISABLE_PRECISE_PUBLIC_AVG_VALUE_ULP_COUNT_LOCK", "1"
    )
    evidence = extract_evidence(
        _snapshot(
            hero="chenmei",
            map_id=2407,
            structured_ref_inputs={},
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 37494.125,
                    }
                ]
            },
        )
    )
    probabilities = {"q1": 0.30, "q3": 0.25, "q4": 0.20, "q5": 0.16, "q6": 0.09}

    assert _avg_value_count_matches_for_public_evidence(
        "q5", 9, 37494.125, evidence.source_notes
    )
    assert _prior_count_values(50, "q5", evidence, probabilities) == [8, 9]


def test_ref_engine_half_ulp_keeps_high_magnitude_true_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AHMAD_AVG_VALUE_ULP_K", raising=False)
    assert _avg_value_count_matches_for_public_evidence(
        "q4",
        3,
        65536.3359375,
        ("public_q4_avg_value",),
    )


def test_ref_engine_quality_avg_value_uses_three_decimal_fraction() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=4402,
            structured_ref_inputs={
                "total_count": 10,
                "fixed_counts": {"q1": 0, "q3": 2, "q5": 0, "q6": 0},
            },
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q4_avg_value",
                        "kind": "avg_value",
                        "quality": 4,
                        "value": 5615.625,
                    }
                ]
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert result["evidence"]["fixed_counts"]["q4"] == 8
    assert "public_q4_avg_value" in result["notes"]
    assert "quality_count_q4_from_total_count_residual" in result["notes"]


def test_ref_engine_public_quality_avg_value_wire_float_normalizes() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q4_avg_value",
                        "kind": "avg_value",
                        "quality": 4,
                        "value": 6659.21435546875,
                    }
                ]
            },
        )
    )

    assert evidence.avg_values == {"q4": 6659.214}
    assert "public_q4_avg_value_wire_normalized" in evidence.source_notes


def test_ref_engine_public_quality_avg_value_wire_float_stays_reachable_when_split_locked() -> None:
    """Real 2402/0052 wire float must not collapse to the bogus 93229/14 count lock."""
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2402,
            structured_ref_inputs={
                "total_count": 40,
                "counts": {"q3": 14},
                "quality_cells": {"q3": 31},
                "split_counts": {"white": 4, "green": 7},
                "split_quality_cells": {"white": 8, "green": 10},
            },
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q4_avg_value",
                        "kind": "avg_value",
                        "quality": 4,
                        "value": 6659.21435546875,
                    }
                ]
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert result["evidence"]["avg_values"] == {"q4": 6659.214}
    assert "public_q4_avg_value_wire_normalized" in result["notes"]
    assert "public_quality_avg_value_conflict_fallback" not in result["notes"]
    assert result["quality_count_ranges"]["q4"] == [14, 14, 14]


def test_ref_engine_public_quality_avg_value_falls_back_on_hard_residual_conflict() -> None:
    """Clean public avg with split lock now stays soft-reachable instead of zero-combo fallback."""
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2402,
            structured_ref_inputs={
                "total_count": 34,
                "counts": {"q3": 14},
                "quality_cells": {"q3": 31},
                "split_counts": {"white": 4, "green": 7},
                "split_quality_cells": {"white": 8, "green": 10},
            },
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q4_avg_value",
                        "kind": "avg_value",
                        "quality": 4,
                        "value": 5615.625,
                    }
                ]
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert result["evidence"]["avg_values"] == {"q4": 5615.625}
    assert result["evidence"]["soft_avg_value_keys"] == ["q4"]
    assert "public_q4_avg_value_soft_pending_count" in result["notes"]
    assert "public_quality_avg_value_conflict_fallback" not in result["notes"]
    assert result["quality_count_ranges"]["q4"] == [8, 8, 8]


def test_ref_engine_zero_quality_avg_value_fixes_count_zero() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 0,
                    }
                ]
            },
        )
    )

    assert evidence.avg_values["q5"] == 0
    assert evidence.fixed_counts["q5"] == 0
    assert evidence.min_counts["q5"] == 0
    assert "zero_avg_value_q5_count_zero" in evidence.source_notes


def test_ref_engine_marks_rare_actions_diagnostic_only_without_constraints() -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"]["actions"] = {
        "results": [
            {
                "action_id": 100121,
                "tool": "终极审计",
                "result": 728211,
                "revealed_items": 0,
            },
            {
                "action_id": 100127,
                "tool": "全知全能",
                "result": None,
                "revealed_items": 42,
                "revealed_items_detail": [{"local_index": 1, "quality": 6}],
            },
            {
                "action_id": 100134,
                "tool": "明镜之眼",
                "result": None,
                "revealed_items": 42,
                "revealed_items_detail": [{"local_index": 1, "quality": 6}],
            },
        ],
    }

    evidence = extract_evidence(snapshot)

    assert "action_100121_total_value_diagnostic_only" in evidence.source_notes
    assert "action_100127_all_items_diagnostic_only" in evidence.source_notes
    assert "action_100134_all_item_quality_diagnostic_only" in evidence.source_notes
    assert "action_100127_revealed_items:42" in evidence.source_notes
    assert "action_100134_revealed_items:42" in evidence.source_notes
    assert evidence.min_counts.get("q6") == 1
    assert "coarse_quality_reveal_min_counts" in evidence.source_notes
    assert evidence.quality_values == {}
    assert evidence.fixed_counts == {}


def test_ref_engine_marks_total_avg_value_public_info_diagnostic_only() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_info={
                "public_numeric_facts": [
                    {
                        "info_id": 200035,
                        "semantic": "total_avg_value",
                        "kind": "avg_value",
                        "quality": None,
                        "value": 18502.75,
                    }
                ]
            },
        )
    )

    assert "public_total_avg_value_diagnostic_only" in evidence.source_notes
    assert evidence.avg_values == {}
    assert evidence.quality_values == {}


def test_ref_engine_quality_value_sum_and_avg_value_derive_count() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=4406,
            structured_ref_inputs={
                "total_count": 10,
                "avg_values": {"q5": 34288.75},
                "quality_values": {"q5": 137155},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["evidence"]["fixed_counts"]["q5"] == 4
    assert result["evidence"]["quality_cells"].get("q5") is None
    assert result["quality_count_ranges"]["q5"] == [4, 4, 4]
    assert "quality_value_q5_count_derived" in result["notes"]


def test_ref_engine_action_quality_value_sum_joins_public_avg_value() -> None:
    result = run_reference_engine(
        {
            "ui_contract": {
                "context": {"hero": "ahmed", "map_id": 4406, "phase": "bidding"},
                "constraints": {
                    "public_info": {
                        "public_numeric_facts": [
                            {
                                "info_id": 200037,
                                "semantic": "q5_avg_value",
                                "kind": "avg_value",
                                "quality": 5,
                                "value": 34288.75,
                            }
                        ]
                    }
                },
                "actions": {
                    "results": [
                        {
                            "action_id": 100125,
                            "tool": "金品总价",
                            "result": 137155,
                        }
                    ]
                },
            },
            "structured_ref_inputs": {"total_count": 10},
        },
        max_combos=60_000,
    ).as_dict()

    assert result["evidence"]["avg_values"] == {"q5": 34288.75}
    assert result["evidence"]["quality_values"] == {"q5": 137155.0}
    assert result["evidence"]["fixed_counts"]["q5"] == 4
    assert "action_100125_q5_value_sum" in result["notes"]
    assert "public_q5_avg_value" in result["notes"]


def test_ref_engine_quality_value_sum_derives_q5_count_with_product_tolerance() -> None:
    """Wire-normalized public avg can break strict rational derive; tolerate product residual."""
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "avg_values": {"q5": 36235.223},
                "quality_values": {"q5": 326117},
            },
        ),
        max_combos=8_000,
    ).as_dict()

    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    fixed_counts = evidence.get("fixed_counts") if isinstance(evidence.get("fixed_counts"), dict) else {}
    assert fixed_counts.get("q5") == 9
    assert "quality_value_q5_count_derived_product_tolerance" in result["notes"]


def test_ref_engine_quality_value_sum_soft_weights_count_without_avg_value() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={
                "total_count": 50,
                "counts": {"q3": 16},
                "avg_cells": {
                    "q1": 1.2666666507720947,
                    "q3": 2.5,
                    "q4": 3.923076868057251,
                    "q5": 3.6666667461395264,
                },
                "quality_values": {"q5": 208230},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["quality_count_ranges"]["q5"] == [6, 6, 6]
    assert result["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert result["red_count_range"] == [0, 0, 0]
    assert "quality_value_soft_weight_v0" in result["notes"]
    assert "quality_value_q5_count_derived" not in result["notes"]


def test_ref_engine_avg_value_and_avg_cells_unique_intersection_derives_count() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={
                "total_count": 7,
                "avg_values": {"q5": 34288.75},
                "avg_cells": {"q5": 3.25},
            },
        )
    )

    assert evidence.fixed_counts["q5"] == 4
    assert evidence.min_counts["q5"] == 4
    assert "avg_value_cells_q5_count_derived" in evidence.source_notes


def test_ref_engine_avg_value_and_avg_cells_multiple_intersections_do_not_lock() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={
                "total_count": 12,
                "avg_values": {"q5": 34288.75},
                "avg_cells": {"q5": 3.25},
            },
        )
    )

    assert "q5" not in evidence.fixed_counts
    assert "avg_value_cells_q5_count_derived" not in evidence.source_notes


def test_ref_engine_keeps_value_band_when_quality_counts_and_cells_are_fixed() -> None:
    result = run_reference_engine(
        _snapshot(
            map_id=2404,
            structured_ref_inputs={
                "total_count": 25,
                "fixed_counts": {"q1": 5, "q3": 8, "q4": 6, "q5": 4, "q6": 2},
                "quality_cells": {"q1": 8, "q3": 11, "q4": 12, "q5": 9, "q6": 10},
            },
        )
    ).as_dict()

    assert result["combo_count"] == 1
    assert result["quality_count_ranges"]["q6"] == [2, 2, 2]
    assert result["value_p25"] < result["value_p50"] < result["value_p75"]
    assert result["conservative"] < result["balanced"] < result["aggressive"]
    assert "intra_quality_value_band_v0" in result["notes"]


def test_ref_engine_keeps_value_point_when_quality_values_are_exact() -> None:
    result = run_reference_engine(
        _snapshot(
            map_id=2404,
            structured_ref_inputs={
                "total_count": 25,
                "fixed_counts": {"q1": 5, "q3": 8, "q4": 6, "q5": 4, "q6": 2},
                "quality_cells": {"q1": 8, "q3": 11, "q4": 12, "q5": 9, "q6": 10},
                "quality_values": {
                    "q1": 2027,
                    "q3": 11784,
                    "q4": 32128,
                    "q5": 101220,
                    "q6": 336915,
                },
            },
        )
    ).as_dict()

    assert result["combo_count"] == 1
    assert result["value_p25"] == result["value_p50"] == result["value_p75"]
    assert "intra_quality_value_band_v0" not in result["notes"]


def test_ref_engine_quote_never_drops_below_known_quality_value(monkeypatch) -> None:
    """Reduced from local HeroRefDiag 2502:1700600975192202 R3.

    Aisha's pure-prior-red strip may remove unseen red value, but it must not
    erase the exact q5 total already disclosed by 极品估价.
    """
    snapshot = _snapshot(
        hero="aisha",
        map_id=2502,
        structured_ref_inputs={
            "total_count": 34,
            "avg_cells": {"q4": 4.25},
            "counts": {"q3": 12},
            "quality_cells": {"q3": 48},
            "quality_values": {"q5": 113_409},
            "split_counts": {"green": 9, "white": 2},
            "split_quality_cells": {"green": 18, "white": 2},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 3

    monkeypatch.setenv("BIDKING_DISABLE_KNOWN_QUALITY_VALUE_QUOTE_FLOOR", "1")
    disabled = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert disabled["conservative"] == 0

    monkeypatch.delenv("BIDKING_DISABLE_KNOWN_QUALITY_VALUE_QUOTE_FLOOR")
    monkeypatch.setenv("BIDKING_DISABLE_KNOWN_QUALITY_RESIDUAL_QUOTE_TRANSFORM", "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv("BIDKING_DISABLE_KNOWN_QUALITY_RESIDUAL_QUOTE_TRANSFORM")
    enabled = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert enabled["conservative"] == 113_409
    assert [
        rollback[key] for key in ("conservative", "balanced", "aggressive")
    ] == [113_409, 720_477, 866_202]
    assert [
        enabled[key] for key in ("conservative", "balanced", "aggressive")
    ] == [113_409, 731_818, 877_543]
    assert [
        enabled[key] for key in ("value_p25", "value_p50", "value_p75")
    ] == [
        rollback[key] for key in ("value_p25", "value_p50", "value_p75")
    ]
    assert enabled["quality_count_cells_candidates"] == rollback[
        "quality_count_cells_candidates"
    ]
    assert enabled["quality_lock"] == rollback["quality_lock"]
    assert enabled["conservative"] <= enabled["balanced"] <= enabled["aggressive"]
    assert any(
        str(note).startswith(
            "known_quality_value_residual_quote_transform:113409:0/"
        )
        for note in enabled["notes"]
    )


def test_known_quality_value_floor_uses_max_within_quality_and_sum_across_qualities() -> None:
    evidence = extract_evidence(
        _snapshot(
            structured_ref_inputs={
                "total_count": 20,
                "quality_values": {"q4": 20_000, "q5": 100_000},
            },
        )
    )
    evidence = replace(evidence, quality_value_floors={"q5": 40_000, "q6": 30_000})

    assert _known_quality_value_total_floor(evidence) == 150_000


def test_ref_engine_settlement_truth_overrides_stale_bridge_totals() -> None:
    result = run_reference_engine(
        {
            "ui_contract": {
                "context": {"hero": "ahmed", "map_id": 4406, "phase": "settled"},
                "truth": {
                    "available": True,
                    "total_items": 24,
                    "total_cells": 62,
                    "total_value": 477562,
                },
            },
            "structured_ref_inputs": {
                "total_count": 39,
                "total_cells": 99,
                "counts": {"q3": 12},
                "avg_cells": {"q3": 2.0833332538604736, "q5": 2.799999952316284},
            },
            "final_quality_counts": "q1=1;q2=4;q3=9;q4=6;q5=2;q6=2",
            "final_quality_cells": "q1=1;q2=9;q3=17;q4=18;q5=5;q6=12",
        },
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["total_count"] == 24
    assert result["evidence"]["total_grid_target"] == 62
    assert result["evidence"]["fixed_counts"] == {
        "q1": 5,
        "q3": 9,
        "q4": 6,
        "q5": 2,
        "q6": 2,
    }
    assert result["quality_count_ranges"]["q5"] == [2, 2, 2]
    assert result["red_count_range"] == [2, 2, 2]
    assert "settlement_review_total_count_overrode_bridge" in result["notes"]
    assert "settlement_review_total_grid_overrode_bridge" in result["notes"]


def test_ref_engine_sparse_exact_total_uses_probability_prior() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2401,
            structured_ref_inputs={"total_count": 33},
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["balanced"] is not None
    assert result["combo_count"] < 10_000
    assert "sparse_exact_total_prior_enumeration" in result["notes"]
    assert "combo_cap_hit" not in result["notes"]


def test_ref_engine_nonzero_quality_count_still_routes_fast_when_sparse() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2401,
            structured_ref_inputs={
                "total_count": 38,
                "avg_cells": {"q5": 9.0},
                "fixed_counts": {"q3": 13},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert "sparse_exact_total_prior_enumeration" in result["notes"]
    assert result["evidence"]["fixed_counts"]["q3"] == 13


def test_ref_engine_public_gold_total_cells_keeps_sparse_prior_path() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2401,
            public_rows=[
                {"info_id": 200017, "value": 33},
                {"info_id": 200011, "value": 23},
            ],
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["balanced"] is not None
    assert result["combo_count"] < 10_000
    assert "public_info_200011_q5_cells" in result["notes"]
    assert "sparse_exact_total_prior_enumeration" in result["notes"]
    assert "combo_cap_hit" not in result["notes"]


def test_u05_gold_avg_five_uses_gold_catalog_footprints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 5-cell item exists in lower tiers, but not in the current droppable GOLD catalog."""
    monkeypatch.delenv(Q5_CATALOG_CELL_COMPOSE_DISABLE, raising=False)

    assert 5 not in _catalog_cell_sizes_by_table_quality()[5]
    assert _avg_grid_options(1, 5.0, "q5") == []
    assert _avg_grid_options(2, 5.0, "q5") == [10]

    # Negative controls: this is quality-specific, and must not ban legal rectangular footprints.
    assert _avg_grid_options(1, 6.0, "q5") == [6]
    assert _avg_grid_options(1, 5.0, "q4") == [5]

    monkeypatch.setenv(Q5_CATALOG_CELL_COMPOSE_DISABLE, "1")
    assert _avg_grid_options(1, 5.0, "q5") == [5]


def test_hotfix1_red_composition_uses_real_q6_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real red catalog has a 20-cell item and no 18-cell item."""
    monkeypatch.delenv(Q6_CATALOG_CELL_COMPOSE_DISABLE, raising=False)

    assert 20 in _catalog_cell_sizes_by_table_quality()[6]
    assert 18 not in _catalog_cell_sizes_by_table_quality()[6]
    assert _can_compose_grid_total_for_quality("q6", 1, 20)
    assert not _can_compose_grid_total_for_quality("q6", 1, 18)
    assert _avg_grid_options(1, 20.0, "q6") == [20]
    assert _avg_grid_options(1, 18.0, "q6") == []
    assert _catalog_quality_grid_ceiling("q6", 2) == 40

    # Scope gate: this hotfix must not silently move q1-q5 onto a different catalog domain.
    for quality_key in (None, "q1", "q3", "q4", "q5"):
        for grid in range(1, 21):
            assert _can_compose_grid_total_with_q6_catalog(quality_key, 1, grid) == (
                can_compose_grid_total(1, grid)
            )

    # Known-fail control: the rollback arm reproduces the legacy cross-quality union.
    monkeypatch.setenv(Q6_CATALOG_CELL_COMPOSE_DISABLE, "1")
    assert not _can_compose_grid_total_for_quality("q6", 1, 20)
    assert _can_compose_grid_total_for_quality("q6", 1, 18)


def test_hotfix1_hard_total_accepts_20_cell_red_and_rejects_fake_18_cell_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hard_residual_snapshot(total_cells: int) -> dict:
        return _snapshot(
            hero="aisha",
            map_id=4550,
            structured_ref_inputs={
                "total_count": 2,
                "total_cells": total_cells,
                "fixed_counts": {"q1": 1, "q3": 0, "q4": 0, "q5": 0, "q6": 1},
                "quality_cells": {"q1": 1, "q3": 0, "q4": 0, "q5": 0},
            },
        )

    monkeypatch.delenv(Q6_CATALOG_CELL_COMPOSE_DISABLE, raising=False)
    real_red = run_reference_engine(hard_residual_snapshot(21), max_combos=10_000).as_dict()
    fake_red = run_reference_engine(hard_residual_snapshot(19), max_combos=10_000).as_dict()

    assert real_red["status"] == "ok"
    assert real_red["red_count_range"] == [1, 1, 1]
    assert real_red["red_cells_range"] == [20, 20, 20]
    assert fake_red["status"] == "no_reachable_combo"

    # The rollback arm must distinguish the same two fixtures in the opposite direction.
    monkeypatch.setenv(Q6_CATALOG_CELL_COMPOSE_DISABLE, "1")
    legacy_real = run_reference_engine(hard_residual_snapshot(21), max_combos=10_000).as_dict()
    legacy_fake = run_reference_engine(hard_residual_snapshot(19), max_combos=10_000).as_dict()
    assert legacy_real["status"] == "no_reachable_combo"
    assert legacy_fake["status"] == "ok"


def test_u05_ahmed_2401_avg_five_prunes_only_impossible_single_gold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reduced replay of 1700600973408643 R3 (the export has no settlement frame)."""
    snapshot = _snapshot(
        hero="ahmed",
        map_id=2401,
        structured_ref_inputs={
            "total_count": 27,
            "counts": {"q3": 9},
            "quality_cells": {"q4": 18},
            "avg_cells": {
                "q1": 2.1818182468414307,
                "q4": 4.5,
                "q5": 5.0,
            },
        },
    )

    monkeypatch.delenv(Q5_CATALOG_CELL_COMPOSE_DISABLE, raising=False)
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=50_000
    ).as_dict()
    assert fixed["status"] == "ok"
    assert fixed["quality_count_ranges"]["q5"] == [2, 2, 3]
    assert fixed["balanced"] > 0
    assert Q5_CATALOG_CELL_COMPOSE_NOTE in fixed["notes"]

    monkeypatch.setenv(Q5_CATALOG_CELL_COMPOSE_DISABLE, "1")
    legacy = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=50_000
    ).as_dict()
    assert legacy["quality_count_ranges"]["q5"] == [1, 2, 3]
    assert Q5_CATALOG_CELL_COMPOSE_NOTE not in legacy["notes"]


def test_u05_aisha_2510_avg_five_keeps_settlement_truth_six() -> None:
    """Reduced replay of 1700600974399774 R3; settlement truth is q5=6 items / 30 cells."""
    snapshot = _snapshot(
        hero="aisha",
        map_id=2510,
        structured_ref_inputs={
            "counts": {"q3": 8},
            "quality_cells": {"q3": 14},
            "avg_cells": {"q4": 2.125, "q5": 5.0},
            "split_counts": {"white": 5, "green": 6},
            "split_quality_cells": {"white": 6, "green": 9},
            "minimap_items": [
                {
                    "quality": 6,
                    "item_id": 1046003,
                    "cells": 1,
                    "shape_key": "11",
                    "row": 1,
                    "col": 4,
                    "width": 1,
                    "height": 1,
                    "value": 54300,
                }
            ],
        },
    )

    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=50_000
    ).as_dict()
    q5_range = result["quality_count_ranges"]["q5"]

    assert result["status"] == "count_prior"
    assert q5_range[0] >= 2
    assert q5_range[0] <= 6 <= q5_range[2]
    assert result["balanced"] > 0
    assert Q5_CATALOG_CELL_COMPOSE_NOTE in result["notes"]


def test_u08_known_six_cell_gold_is_subtracted_before_avg_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known 6-cell gold + q5 avg 5.5: count=2 would require an impossible 5-cell gold."""
    known_gold = {
        "local_index": 46,
        "runtime_id": 1,
        "quality": 5,
        "item_id": 1075001,
        "item_name": "曲面带鱼屏显示器",
        "cells": 6,
        "shape_code": 32,
        "value": 54495,
    }
    snapshot = _snapshot(
        hero="aisha",
        map_id=2510,
        structured_ref_inputs={
            "total_count": 12,
            "counts": {"q1": 4, "q3": 2, "q4": 2},
            "quality_cells": {"q1": 8, "q3": 4, "q4": 6},
            "avg_cells": {"q5": 5.5},
        },
    )
    snapshot["action_result_rows"] = [
        {"action_id": 100129, "revealed_items_detail": [known_gold]}
    ]

    monkeypatch.delenv(Q5_KNOWN_ITEM_COMPOSE_DISABLE, raising=False)
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=50_000
    ).as_dict()
    assert fixed["quality_count_ranges"]["q5"] == [4, 4, 4]
    assert fixed["quality_cells_ranges"]["q5"] == [22, 22, 22]
    assert Q5_KNOWN_ITEM_COMPOSE_NOTE in fixed["notes"]

    monkeypatch.setenv(Q5_KNOWN_ITEM_COMPOSE_DISABLE, "1")
    legacy = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=50_000
    ).as_dict()
    assert legacy["quality_count_ranges"]["q5"] == [2, 2, 4]
    assert legacy["quality_cells_ranges"]["q5"] == [11, 11, 22]
    assert Q5_KNOWN_ITEM_COMPOSE_NOTE not in legacy["notes"]

    # Negative control: with no revealed gold base, the new lever is a semantic no-op.
    without_known = dict(snapshot)
    without_known.pop("action_result_rows")
    monkeypatch.delenv(Q5_KNOWN_ITEM_COMPOSE_DISABLE, raising=False)
    default_without_known = run_reference_engine(
        prepare_reference_engine_snapshot(without_known), max_combos=50_000
    ).as_dict()
    monkeypatch.setenv(Q5_KNOWN_ITEM_COMPOSE_DISABLE, "1")
    disabled_without_known = run_reference_engine(
        prepare_reference_engine_snapshot(without_known), max_combos=50_000
    ).as_dict()
    assert default_without_known["quality_count_ranges"] == disabled_without_known[
        "quality_count_ranges"
    ]
    assert default_without_known["quality_cells_ranges"] == disabled_without_known[
        "quality_cells_ranges"
    ]


def test_u08_aisha_2510_known_gold_and_avg_keep_truth_nine() -> None:
    """Reduced replay of 1700600974313372 R3; settlement q5 truth is 9 items / 16 cells."""
    snapshot = _snapshot(
        hero="aisha",
        map_id=2510,
        structured_ref_inputs={
            "total_count": 44,
            "counts": {"q1": 16, "q3": 7},
            "quality_cells": {"q1": 40, "q3": 24},
            "avg_cells": {
                "q4": 3.0833332538604736,
                "q5": 1.7777777910232544,
            },
        },
    )
    snapshot["action_result_rows"] = [
        {
            "action_id": 100129,
            "revealed_items_detail": [
                {
                    "local_index": 46,
                    "runtime_id": 1700600974313889,
                    "quality": 5,
                    "item_id": 1075001,
                    "item_name": "曲面带鱼屏显示器",
                    "cells": 6,
                    "shape_code": 32,
                    "value": 54495,
                }
            ],
        }
    ]

    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=50_000
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["quality_count_ranges"]["q5"] == [9, 9, 9]
    assert result["quality_cells_ranges"]["q5"] == [16, 16, 16]
    assert Q5_KNOWN_ITEM_COMPOSE_NOTE in result["notes"]


def test_ref_engine_derives_fixed_counts_from_avg_and_quality_cells() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "total_cells": 34,
                "avg_cells": {
                    "q1": 1.1428571428571428,
                    "q3": 2.0,
                    "q5": 6.0,
                },
                "quality_cells": {
                    "q1": 8,
                    "q3": 16,
                    "q5": 24,
                },
            },
        ),
        max_combos=60_000,
    ).as_dict()

    evidence = result["evidence"]

    assert evidence["fixed_counts"]["q1"] == 7
    assert evidence["fixed_counts"]["q3"] == 8
    assert evidence["fixed_counts"]["q5"] == 4
    assert "quality_cells_q1_count_derived" in result["notes"]
    assert "quality_cells_q3_count_derived" in result["notes"]
    assert "quality_cells_q5_count_derived" in result["notes"]


def test_ref_engine_rejects_conflicting_count_cells_and_avg_cells() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "fixed_counts": {"q4": 7},
                "quality_cells": {"q4": 15},
                "avg_cells": {"q4": 2.0},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert "quality_cells_q4_avg_count_conflict" in result["notes"]
    assert "constraints_conflict_or_too_strict" in result["notes"]


def test_ref_engine_rejects_avg_and_quality_cells_without_integer_count() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "quality_cells": {"q4": 15},
                "avg_cells": {"q4": 2.0},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert "quality_cells_q4_avg_cells_conflict" in result["notes"]
    assert "constraints_conflict_or_too_strict" in result["notes"]


def _aisha_high_tier_grid_no_reachable_snapshot() -> dict:
    # Repro of live 2537 R4: every low/mid tier is pinned exactly (q1/q3/q4 counts+cells),
    # gold q5 avg_cells = 12/7 (forces 7 gold items / 12 cells), and a single q6 red is
    # publicly revealed at 6 cells. The derived high-tier-cell grid target lands at 83 while
    # the physical minimum is 87 (known 69 + gold 12 + red floor 6), so every combo is rejected.
    return _snapshot(
        hero="aisha",
        map_id=2537,
        structured_ref_inputs={
            "total_count": 42,
            "counts": {"q1": 12, "q3": 12, "q4": 10},
            "quality_cells": {"q1": 25, "q3": 24, "q4": 20},
            "avg_cells": {"q5": 12 / 7},
        },
        public_rows=[
            {
                "info_id": 200023,
                "revealed_items_detail": [
                    {"runtime_id": 999, "quality": 6, "value": 199080, "shape_code": 53, "cells": 6}
                ],
            }
        ],
    )


def test_ref_engine_high_tier_grid_target_reachable_after_residual_prior_fill() -> None:
    # Re-baselined 2026-06-29 (root-cause fix to _residual_per_item_cell_estimate, live 5603).
    # This 2537 synthetic pins q1/q3/q4 counts+cells, gives gold q5 a lone avg_cells signal
    # (12/7 ~ 1.714), and reveals one q6 red at 6 cells. Physical minimum grid = 69 + 12 + 6 = 87.
    # Legacy: the lone q5 signal stood in for the WHOLE residual, collapsing per_item to 1.714,
    # driving the derived high-tier-cell target to 83 (< physical min 87) -> no_reachable_combo
    # with fallback OFF. The fix fills the no-signal unfixed tier (q6) with its DEFAULT_GRID_MEANS
    # prior so per_item covers the full residual; the derived target rises above the physical
    # minimum and the combo becomes reachable. The guard no longer fires because the old
    # "unreachable" target was a self-inflicted artifact of the partial-signal per_item, not a
    # genuine physical impossibility -- so the primary path quotes even with the fallback OFF.
    # The no_reachable_combo guard itself is still covered by the rollback assertions in
    # test_sparse_early_soft_target_not_hard_unblocks_deep_draw_vault_and_rollback and others.
    snapshot = _aisha_high_tier_grid_no_reachable_snapshot()
    evidence = extract_evidence(snapshot)
    assert "total_grid_target_from_known_high_tier_cells" in evidence.source_notes
    assert "residual_avg_cells_partial_signal_prior_filled" in evidence.source_notes

    blocked = run_reference_engine(
        snapshot, max_combos=20_000, _allow_high_tier_grid_fallback=False
    ).as_dict()
    assert blocked["status"] in {"ok", "count_prior"}
    assert blocked["balanced"] is not None
    # 42-item vault (34 pinned low/mid + 7 gold + 1 red@199080): sane quote ~500k.
    assert 400_000 < blocked["balanced"] < 600_000


def test_ref_engine_high_tier_grid_target_no_relaxation_needed_after_residual_prior_fill() -> None:
    # Re-baselined 2026-06-29 (companion to the test above). Because the derived target is now
    # reachable after the residual per_item prior-fill, the fallback relaxation is NO LONGER
    # triggered for this sample -- the primary path produces a quote directly, so the
    # `high_tier_grid_target_relaxed_no_reachable` note is absent. The fallback mechanism itself
    # is unchanged; it simply has nothing to relax here.
    snapshot = _aisha_high_tier_grid_no_reachable_snapshot()
    rescued = run_reference_engine(snapshot, max_combos=20_000).as_dict()

    assert rescued["status"] in {"ok", "count_prior"}
    assert rescued["balanced"] is not None
    assert "high_tier_grid_target_relaxed_no_reachable" not in rescued["notes"]


def test_aisha_red_keep_evidence_ramp_scales_with_red_count() -> None:
    base = 0.30
    # Low/mid red-count estimate (phantom-likely or still-modest red) -> keep stays at the flat base
    # (LO=4: red-bucket 2-3 with q6 est ~4 is intentionally NOT lifted).
    assert _aisha_red_keep_evidence_ramp(1.0, base) == pytest.approx(base)
    assert _aisha_red_keep_evidence_ramp(4.0, base) == pytest.approx(base)  # at LO, still base
    # Above LO ramps up; high estimate reaches the ceiling and clamps.
    assert base < _aisha_red_keep_evidence_ramp(4.5, base) < 0.90
    assert _aisha_red_keep_evidence_ramp(5.0, base) == pytest.approx(0.90)
    assert _aisha_red_keep_evidence_ramp(9.0, base) == pytest.approx(0.90)  # clamp above HI
    # Conservative-strip base also ramps to the same ceiling.
    assert _aisha_red_keep_evidence_ramp(5.0, 0.25) == pytest.approx(0.90)
    # Guards: already-full keep, missing signal -> unchanged.
    assert _aisha_red_keep_evidence_ramp(5.0, 1.0) == 1.0
    assert _aisha_red_keep_evidence_ramp(None, base) == base


def test_aisha_red_keep_evidence_ramp_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("AISHA_DISABLE_RED_KEEP_RAMP", "1")
    assert _aisha_red_keep_evidence_ramp(5.0, 0.30) == 0.30


def _giant_red_evidence(**over):
    from types import SimpleNamespace
    base = dict(hero="aisha", fixed_counts={}, min_counts={"q6": 1}, quality_cell_floors={"q6": 15.0})
    base.update(over)
    return SimpleNamespace(**base)


def test_revealed_giant_red_count_bias_nudges_toward_revealed() -> None:
    # A revealed giant red (cell_floor 15 from 1 item) nudges the q6 combo count toward the revealed
    # count (1): zero penalty at the revealed count, growing penalty for phantom-stacked higher counts.
    ev = _giant_red_evidence()
    assert _revealed_giant_red_count_bias_log({"q6": 1}, ev) == 0.0
    assert _revealed_giant_red_count_bias_log({"q6": 2}, ev) < 0.0
    assert _revealed_giant_red_count_bias_log({"q6": 6}, ev) < _revealed_giant_red_count_bias_log({"q6": 2}, ev)


def test_revealed_giant_red_count_bias_strict_gate_is_noop() -> None:
    # The nudge must be a structural no-op outside a genuine giant reveal, so the library-wide
    # under-count path (red invisible) is never touched.
    assert _revealed_giant_red_count_bias_log({"q6": 6}, _giant_red_evidence(quality_cell_floors={"q6": 4.0})) == 0.0  # small revealed red
    assert _revealed_giant_red_count_bias_log({"q6": 6}, _giant_red_evidence(min_counts={})) == 0.0  # red invisible
    assert _revealed_giant_red_count_bias_log({"q6": 6}, _giant_red_evidence(fixed_counts={"q6": 2})) == 0.0  # count locked
    assert _revealed_giant_red_count_bias_log({"q6": 6}, _giant_red_evidence(hero="ahmed")) == 0.0  # aisha-only


def test_revealed_giant_red_count_bias_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("BIDKING_DISABLE_AISHA_GIANT_RED_COUNT", "1")
    assert _revealed_giant_red_count_bias_log({"q6": 6}, _giant_red_evidence()) == 0.0


def test_ref_engine_high_tier_fallback_does_not_rescue_true_conflict() -> None:
    # The fallback must NOT mask a genuine constraint conflict (different note); such a
    # snapshot has no high-tier-target note, so the fallback never fires.
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "fixed_counts": {"q4": 7},
                "quality_cells": {"q4": 15},
                "avg_cells": {"q4": 2.0},
            },
        ),
        max_combos=60_000,
    ).as_dict()
    assert result["status"] == "no_reachable_combo"
    assert "high_tier_grid_target_relaxed_no_reachable" not in result["notes"]


def test_ref_engine_aisha_split_white_green_fold_to_merged_q1() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "split_counts": {"white": 3, "green": 4},
                "split_quality_cells": {"white": 5, "green": 8},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    evidence = result["evidence"]

    assert result["status"] in {"ok", "count_prior"}
    assert evidence["hero"] == "aisha"
    assert evidence["split_counts"] == {"white": 3, "green": 4}
    assert evidence["split_quality_cells"] == {"white": 5.0, "green": 8.0}
    assert evidence["fixed_counts"]["q1"] == 7
    assert evidence["quality_cells"]["q1"] == 13.0
    assert evidence["avg_cells"]["q1"] == 13.0 / 7.0
    assert "split_low_quality_q1_count_merged" in result["notes"]
    assert "split_low_quality_q1_cells_merged" in result["notes"]


def test_ref_engine_aisha_split_complements_missing_green_from_merged_q1_exact() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "fixed_counts": {"q1": 7},
                "quality_cells": {"q1": 13},
                "split_counts": {"white": 3},
                "split_quality_cells": {"white": 5},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    evidence = result["evidence"]

    assert result["status"] in {"ok", "count_prior"}
    assert evidence["split_counts"] == {"white": 3, "green": 4}
    assert evidence["split_quality_cells"] == {"white": 5.0, "green": 8.0}
    assert evidence["fixed_counts"]["q1"] == 7
    assert evidence["quality_cells"]["q1"] == 13.0
    assert evidence["split_avg_cells"]["green"] == 2.0
    assert "split_low_quality_green_count_from_q1_exact" in result["notes"]
    assert "split_low_quality_green_cells_from_q1_exact" in result["notes"]


def test_ref_engine_aisha_split_derives_q1_from_total_residual_before_complement() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "fixed_counts": {"q3": 5, "q4": 4, "q5": 3, "q6": 2},
                "split_counts": {"white": 3},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    evidence = result["evidence"]

    assert result["status"] == "ok"
    assert evidence["fixed_counts"]["q1"] == 7
    assert evidence["split_counts"] == {"white": 3, "green": 4}
    assert result["quality_count_ranges"]["q1"] == [7, 7, 7]
    assert "quality_count_q1_from_total_count_residual" in result["notes"]
    assert "split_low_quality_green_count_from_q1_exact" in result["notes"]


def test_ref_engine_aisha_split_complement_rejects_count_overflow() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "fixed_counts": {"q1": 2},
                "split_counts": {"white": 3},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert "hard_conflict:split_low_quality_q1_count_complement" in result["notes"]
    assert "constraints_conflict_or_too_strict" in result["notes"]


def test_ref_engine_settlement_truth_overrides_stale_live_action_counts() -> None:
    snapshot = _snapshot(
        hero="ahmed",
        map_id=4521,
        phase="settled",
        structured_ref_inputs={
            "total_count": 21,
            "fixed_counts": {"q1": 3, "q3": 9, "q5": 2},
            "quality_cells": {"q1": 25},
            "avg_cells": {"q1": 1.6666666269302368},
        },
    )
    snapshot["final_quality_counts"] = "q2=3;q3=2;q4=4;q5=2;q6=3"
    snapshot["final_quality_cells"] = "q2=6;q3=10;q4=12;q5=8;q6=7"
    snapshot["ui_contract"]["truth"] = {
        "total_items": 14,
        "total_cells": 43,
    }
    snapshot["ui_contract"]["actions"] = {
        "results": [
            {"action_id": 100117, "result": 9},
            {"action_id": 100104, "result": 25},
            {"action_id": 100110, "result": 1.6666666269302368},
        ]
    }

    result = run_reference_engine(snapshot, max_combos=60_000).as_dict()
    evidence = result["evidence"]

    assert result["status"] == "ok"
    assert evidence["total_count"] == 14
    assert evidence["total_grid_target"] == 43.0
    assert evidence["fixed_counts"] == {"q1": 3, "q3": 2, "q4": 4, "q5": 2, "q6": 3}
    assert evidence["quality_cells"] == {"q1": 6.0, "q3": 10.0, "q4": 12.0, "q5": 8.0, "q6": 7.0}
    assert evidence["avg_cells"]["q1"] == 2.0
    assert "settlement_review_quality_counts_overrode_live" in result["notes"]
    assert "settlement_review_quality_cells_overrode_live" in result["notes"]
    assert "quality_cells_q1_avg_count_conflict" not in result["notes"]


def test_ref_engine_aisha_white_only_is_q1_lower_bound_not_exact() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="艾莎",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "split_counts": {"white": 3},
                "split_quality_cells": {"white": 5},
            },
        )
    )

    assert evidence.hero == "aisha"
    assert evidence.split_counts == {"white": 3}
    assert evidence.split_quality_cells == {"white": 5.0}
    assert evidence.min_counts["q1"] == 3
    assert "q1" not in evidence.fixed_counts
    assert "q1" not in evidence.quality_cells
    assert "split_low_quality_q1_min_count" in evidence.source_notes
    assert "split_low_quality_q1_grid_floor" in evidence.source_notes


def test_ref_engine_aisha_white_only_q1_avg_respects_split_cell_floor() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "avg_cells": {"q1": 1.0},
                "split_counts": {"white": 3},
                "split_quality_cells": {"white": 5},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert "split_low_quality_q1_grid_floor" in result["notes"]
    assert "constraints_conflict_or_too_strict" in result["notes"]


def test_ref_engine_aisha_white_only_q1_avg_can_remain_reachable_above_split_floor() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "avg_cells": {"q1": 2.0},
                "split_counts": {"white": 3},
                "split_quality_cells": {"white": 5},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["quality_count_ranges"]["q1"][0] >= 3
    assert result["quality_cells_ranges"]["q1"][0] >= result["quality_count_ranges"]["q1"][0] + 2
    assert "q1" not in result["evidence"]["fixed_counts"]
    assert "q1" not in result["evidence"]["quality_cells"]


def test_ref_engine_aisha_split_rejects_impossible_count_cells_pair() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "split_counts": {"white": 3},
                "split_quality_cells": {"white": 2},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert "hard_conflict:split_low_quality_white_count_cells" in result["notes"]
    assert "constraints_conflict_or_too_strict" in result["notes"]


def test_ref_engine_aisha_split_input_without_total_count_uses_simple_prior() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2401,
            structured_ref_inputs={
                "split_counts": {"white": 3},
                "split_quality_cells": {"white": 5},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["combo_count"] > 0
    assert "total_count_from_ref_count_prior" in result["notes"]
    assert result["quality_count_ranges"]["q1"][0] >= 3


def test_ref_engine_aisha_split_conflict_with_merged_q1_is_hard_conflict() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "fixed_counts": {"q1": 8},
                "quality_cells": {"q1": 13},
                "split_counts": {"white": 3, "green": 4},
                "split_quality_cells": {"white": 5, "green": 8},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert "split_low_quality_q1_count_conflict" in result["notes"]
    assert "hard_conflict:split_low_quality_q1_count" in result["notes"]
    assert "constraints_conflict_or_too_strict" in result["notes"]


def test_ref_engine_ahmed_r5_exact_q1_overrides_coarse_public_split() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2107,
            structured_ref_inputs={
                "total_count": 37,
                "counts": {"q1": 18},
                "split_counts": {"white": 4, "green": 4},
            },
            public_rows=[
                {
                    "info_id": 200028,
                    "revealed_items_detail": [
                        {"quality": 1},
                        {"quality": 1},
                        {"quality": 1},
                        {"quality": 2},
                    ],
                },
            ],
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert "split_low_quality_q1_exact_overrides_coarse_split" in result["notes"]
    assert "hard_conflict:split_low_quality_q1_count" not in result["notes"]
    assert result["evidence"]["fixed_counts"]["q1"] == 18


def test_ref_engine_coarse_public_split_without_exact_q1_stays_a_floor() -> None:
    """Without an exact q1, a random public reveal of white/green stays a floor.

    Mirrors the live R1 case: "未知别墅" randomly shows 9 items (4 white/green here)
    while total=35 and blue=7 are exact. q1 must remain ranged with a min floor of 4,
    not get locked to 4 (which over-allocates the remaining items to higher tiers).
    """
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2401,
            structured_ref_inputs={
                "total_count": 35,
                "counts": {"q3": 7},
            },
            public_rows=[
                {
                    "info_id": 200028,
                    "value": 9,
                    "revealed_items": 9,
                    "revealed_items_detail": [
                        {"quality": 1},
                        {"quality": 1},
                        {"quality": 2},
                        {"quality": 2},
                        {"quality": 3},
                        {"quality": 4},
                        {"quality": 4},
                        {"quality": 5},
                        {"quality": 6},
                    ],
                },
            ],
        ),
        max_combos=60_000,
    ).as_dict()

    assert "split_low_quality_q1_coarse_floor_only" in result["notes"]
    assert "split_low_quality_q1_count_merged" not in result["notes"]
    assert result["evidence"]["fixed_counts"].get("q1") is None
    assert int(result["evidence"]["min_counts"].get("q1") or 0) >= 4
    q1_range = result["quality_count_ranges"].get("q1")
    assert q1_range is not None and q1_range[0] != q1_range[2]


def test_ref_engine_total_grid_prior_does_not_collapse_to_known_count_floor() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2410,
            structured_ref_inputs={
                "total_cells": 70,
                "fixed_counts": {"q3": 13, "q4": 9, "q5": 3},
                "quality_cells": {"q3": 19, "q4": 29, "q5": 3},
                "split_counts": {"white": 2, "green": 6},
                "split_quality_cells": {"white": 3, "green": 8},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["red_count_range"][1] >= 1
    assert result["red_cells_range"][1] >= 1
    assert result["total_grid_range"] == [70, 70, 70]
    assert "total_count_from_ref_count_prior" in result["notes"]


def test_ref_engine_total_grid_hard_when_gold_count_unknown() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2410,
            structured_ref_inputs={
                "total_cells": 70,
                "counts": {"q3": 13, "q4": 9},
                "quality_cells": {"q3": 19, "q4": 29, "q5": 3},
                "split_counts": {"white": 2, "green": 6},
                "split_quality_cells": {"white": 3, "green": 8},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["red_count_range"][1] >= 1
    assert result["red_cells_range"][1] >= 1
    assert result["total_grid_range"] == [70, 70, 70]
    assert "structured_ref_bridge_total_cells" in result["notes"]


def test_ref_engine_total_count_residual_derives_single_missing_quality_count() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 25,
                "fixed_counts": {"q1": 5, "q3": 8, "q4": 6, "q5": 4},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["fixed_counts"]["q6"] == 2
    assert result["quality_count_ranges"]["q6"] == [2, 2, 2]
    assert "quality_count_q6_from_total_count_residual" in result["notes"]


def test_ref_engine_count_sum_residual_derives_single_missing_group_count() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "fixed_counts": {"q4": 3, "q5": 2},
                "count_sums": {"q4q5q6": 8},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["fixed_counts"]["q6"] == 3
    assert result["quality_count_ranges"]["q6"] == [3, 3, 3]
    assert "count_sum_q4q5q6_q6_count_from_residual" in result["notes"]


def test_ref_engine_total_grid_residual_derives_single_missing_quality_cells() -> None:
    fixed_counts = {"q1": 5, "q3": 8, "q4": 6, "q5": 4, "q6": 2}
    all_quality_cells = {"q1": 8, "q3": 11, "q4": 12, "q5": 9, "q6": 10}

    for missing_key in ("q1", "q4", "q6"):
        quality_cells = {
            key: value for key, value in all_quality_cells.items() if key != missing_key
        }
        result = run_reference_engine(
            _snapshot(
                hero="ahmed",
                map_id=2404,
                structured_ref_inputs={
                    "total_count": 25,
                    "total_cells": 50,
                    "fixed_counts": fixed_counts,
                    "quality_cells": quality_cells,
                },
            ),
            max_combos=60_000,
        ).as_dict()

        assert result["status"] == "ok"
        assert result["total_grid_range"] == [50, 50, 50]
        assert result["quality_cells_ranges"][missing_key] == [
            all_quality_cells[missing_key],
            all_quality_cells[missing_key],
            all_quality_cells[missing_key],
        ]
        assert result["evidence"]["quality_cells"][missing_key] == float(
            all_quality_cells[missing_key]
        )
        assert f"quality_cells_{missing_key}_from_total_grid_residual" in result["notes"]


def test_ref_engine_aisha_split_uses_total_grid_residual_for_missing_green_cells() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 25,
                "total_cells": 50,
                "fixed_counts": {"q1": 5, "q3": 8, "q4": 6, "q5": 4, "q6": 2},
                "quality_cells": {"q3": 11, "q4": 12, "q5": 9, "q6": 10},
                "split_counts": {"white": 2},
                "split_quality_cells": {"white": 3},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    evidence = result["evidence"]

    assert result["status"] == "ok"
    assert evidence["quality_cells"]["q1"] == 8.0
    assert evidence["split_counts"] == {"white": 2, "green": 3}
    assert evidence["split_quality_cells"] == {"white": 3.0, "green": 5.0}
    assert "quality_cells_q1_from_total_grid_residual" in result["notes"]
    assert "split_low_quality_green_cells_from_q1_exact" in result["notes"]


def test_ref_engine_total_grid_residual_rejects_negative_missing_quality_cells() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 25,
                "total_cells": 20,
                "fixed_counts": {"q1": 5, "q3": 8, "q4": 6, "q5": 4, "q6": 2},
                "quality_cells": {"q3": 11, "q4": 12, "q5": 9, "q6": 10},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert "hard_conflict:quality_cells_q1_total_grid_residual" in result["notes"]


def test_aisha_hard_total_and_hard_nonred_cells_lock_red_residual() -> None:
    """4550 R5: a hard avg-derived low tier is as exact as a direct cells field.

    Gold count may remain unknown, but total cells and every non-red tier's cells make
    q6 cells the exact residual.  The room-band must not revive an all-1x1 red state.
    """
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=4550,
            structured_ref_inputs={
                "total_count": 43,
                "total_cells": 134,
                "fixed_counts": {"q1": 15, "q3": 11, "q4": 8},
                "min_counts": {"q6": 1},
                "avg_cells": {"q1": 2.0},
                "quality_cells": {"q3": 30, "q4": 29, "q5": 18},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["red_count_range"][0] < result["red_count_range"][2]
    assert result["red_cells_range"] == [27, 27, 27]
    assert result["quality_cells_ranges"]["q6"] == [27, 27, 27]
    assert {
        candidate["cells"]
        for candidate in result["quality_count_cells_candidates"]["q6"]
    } == {27}
    assert "red_cells_locked_to_hard_total_residual:27cells" in result["notes"]
    assert not any(note.startswith("red_cells_room_band:") for note in result["notes"])


def test_aisha_hard_total_does_not_lock_red_when_one_nonred_tier_is_unresolved() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=4550,
            structured_ref_inputs={
                "total_count": 43,
                "total_cells": 134,
                "fixed_counts": {"q1": 15, "q3": 11, "q4": 8},
                "min_counts": {"q6": 1},
                "quality_cells": {"q3": 30, "q4": 29, "q5": 18},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["red_cells_range"][0] < result["red_cells_range"][2]
    assert "red_cells_locked_to_hard_total_residual:27cells" not in result["notes"]
    assert any(note.startswith("red_cells_room_band:") for note in result["notes"])


def _aisha_4547_hard_total_joint_tail_snapshot() -> dict:
    """Reduced from 4547:1741282946336012 R5 bidding."""
    snapshot = _snapshot(
        hero="aisha",
        map_id=4547,
        structured_ref_inputs={
            "total_count": 54,
            "total_cells": 142,
            "fixed_counts": {"q1": 20, "q3": 9, "q4": 14},
            "min_counts": {"q6": 1},
            "avg_cells": {"q5": 3.3333332538604736},
            "quality_cells": {"q1": 47, "q3": 17, "q4": 41},
            "quality_values": {"q5": 207840},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 5
    return snapshot


def test_aisha_hard_total_keeps_correlated_gold_red_tail_without_false_lock() -> None:
    result = run_reference_engine(
        _aisha_4547_hard_total_joint_tail_snapshot(),
        max_combos=50_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["total_grid_range"] == [142, 142, 142]
    assert result["quality_cells_ranges"] == {
        "q1": [47, 47, 47],
        "q3": [17, 17, 17],
        "q4": [41, 41, 41],
        "q5": [20, 20, 20],
        "q6": [17, 17, 17],
    }
    assert all(
        sum(
            result["quality_cells_ranges"][key][index]
            for key in ("q1", "q3", "q4", "q5", "q6")
        )
        == 142
        for index in range(3)
    )
    assert result["quality_count_candidates"]["q5"] == [
        {"value": 6, "prob": 0.9882},
        {"value": 9, "prob": 0.0118},
    ]
    assert result["quality_count_candidates"]["q6"] == [
        {"value": 5, "prob": 0.9882},
        {"value": 2, "prob": 0.0118},
    ]
    assert result["quality_count_cells_candidates"]["q5"] == [
        {"count": 6, "cells": 20, "prob": 0.9882},
        {"count": 9, "cells": 30, "prob": 0.0118},
    ]
    assert result["quality_count_cells_candidates"]["q6"] == [
        {"count": 5, "cells": 17, "prob": 0.9882},
        {"count": 2, "cells": 7, "prob": 0.0118},
    ]
    assert result["quality_lock"]["q5"]["strength"] == "probabilistic"
    assert result["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert HARD_TOTAL_JOINT_CELLS_NOTE in result["notes"]
    assert not any(note.startswith("red_cells_room_band:") for note in result["notes"])


def test_aisha_hard_total_joint_tail_has_narrow_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _aisha_4547_hard_total_joint_tail_snapshot()
    repaired = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(HARD_TOTAL_JOINT_CELLS_DISABLE, "1")
    legacy = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert legacy["red_cells_range"] == [5, 17, 17]
    assert legacy["quality_lock"]["q5"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert legacy["quality_lock"]["q6"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert HARD_TOTAL_JOINT_CELLS_NOTE not in legacy["notes"]
    assert [repaired[key] for key in ("conservative", "balanced", "aggressive")] == [
        legacy[key] for key in ("conservative", "balanced", "aggressive")
    ]


def test_aisha_q6_20_cell_catalog_total_does_not_float_generic_sibling() -> None:
    """Ten fixed 20-cell reds exhaust ten total items, so the total is exactly 200."""
    snapshot = _snapshot(
        hero="aisha",
        map_id=5601,
        structured_ref_inputs={
            "total_count": 10,
            "fixed_counts": {"q6": 10},
            "quality_cells": {"q6": 200},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 3

    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["status"] == "count_prior"
    assert result["quality_count_ranges"]["q6"] == [10, 10, 10]
    assert result["quality_cells_ranges"]["q6"] == [200, 200, 200]
    assert result["total_grid_range"] == [200, 200, 200]
    assert "total_grid_from_exhausted_quality_cells" in result["notes"]
    assert "aisha_floating_grid_min_spread_applied" not in result["notes"]


def test_aisha_pinned_nonred_soft_geometry_reconciles_full_red_residual_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2549 R4: each red arm follows total-grid minus 120 exact non-red cells."""
    snapshot = _snapshot(
        hero="aisha",
        map_id=2549,
        structured_ref_inputs={
            "fixed_counts": {"q1": 19, "q3": 14, "q4": 11},
            "quality_cells": {"q1": 34, "q3": 38, "q4": 45, "q5": 3},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 4
    evidence = extract_evidence(snapshot)
    base = run_reference_engine(snapshot, max_combos=20_000)
    seeded = replace(
        base,
        total_grid_range=(153, 160, 170),
        red_cells_range=(3, 6, 40),
        quality_cells_ranges={
            **base.quality_cells_ranges,
            "q6": (3, 6, 40),
        },
    )

    repaired = _apply_aisha_pinned_nonred_room_hi(seeded, evidence, round_no=4)

    assert repaired.red_cells_range == (0, 40, 50)
    assert repaired.quality_cells_ranges["q6"] == (0, 40, 50)
    assert (repaired.conservative, repaired.balanced, repaired.aggressive) == (
        seeded.conservative,
        seeded.balanced,
        seeded.aggressive,
    )
    assert (
        "aisha_pinned_nonred_red_residual_band:3/6/40->0/40/50"
        in repaired.notes
    )

    monkeypatch.setenv("BIDKING_DISABLE_AISHA_PINNED_NONRED_ROOM_HI", "1")
    assert _apply_aisha_pinned_nonred_room_hi(seeded, evidence, round_no=4) == seeded


def test_aisha_geometry_capacity_cannot_fall_below_visible_row_20() -> None:
    notes: list[str] = []
    items = [
        {"row": row, "col": 1, "width": 10 if row <= 17 else 5, "height": 1, "quality": 3}
        for row in range(1, 21)
    ]

    repaired = apply_aisha_geometry_grid_range(
        (181, 196, 220),
        notes,
        hero_key="aisha",
        round_no=3,
        raw_deepest=20,
        known_cells=73,
        items=items,
    )

    assert repaired == (181, 190, 194)
    assert repaired[0] <= repaired[1] <= repaired[2] <= 200


def test_aisha_exact_low_tiers_cap_only_the_large_item_count_explosion() -> None:
    probs = {"q1": 0.290035, "q3": 0.248602}
    large_item = extract_evidence(
        _snapshot(
            hero="aisha",
            map_id=2549,
            structured_ref_inputs={
                "fixed_counts": {"q1": 13, "q3": 11},
                "quality_cells": {"q1": 41, "q3": 25},
            },
        )
    )
    ordinary = extract_evidence(
        _snapshot(
            hero="aisha",
            map_id=2549,
            structured_ref_inputs={
                "fixed_counts": {"q1": 19, "q3": 14},
                "quality_cells": {"q1": 34, "q3": 38},
            },
        )
    )

    assert _aisha_low_tier_count_upper(large_item, probs, round_no=3) == 59
    assert _aisha_low_tier_count_upper(ordinary, probs, round_no=4) == 78


def test_aisha_large_item_count_cap_preserves_the_independent_spatial_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [
        {"row": row, "col": 1, "width": 4, "height": 1, "quality": 3, "cells": 4}
        for row in range(1, 17)
    ] + [
        {"row": 17, "col": 1, "width": 3, "height": 1, "quality": 3, "cells": 3},
        {"row": 18, "col": 1, "width": 2, "height": 3, "quality": 1, "cells": 6},
    ]
    snapshot = _snapshot(
        hero="aisha",
        map_id=2549,
        structured_ref_inputs={
            "fixed_counts": {"q1": 13, "q3": 11},
            "quality_cells": {"q1": 41, "q3": 25},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["ui_contract"]["minimap"] = {"items": items}
    snapshot["minimap_grid_items"] = items

    repaired = run_reference_engine(snapshot, max_combos=20_000).as_dict()
    monkeypatch.setenv("BIDKING_DISABLE_AISHA_LOW_TIER_COUNT_UPPER", "1")
    legacy = run_reference_engine(snapshot, max_combos=20_000).as_dict()

    assert repaired["balanced"] == 1_635_156
    assert legacy["balanced"] == 2_456_577
    assert repaired["total_grid_range"] == [158, 176, 197]
    assert repaired["total_grid_range"][0] <= repaired["total_grid_range"][1] <= repaired["total_grid_range"][2]
    assert "aisha_low_tier_count_upper:76->59" in repaired["notes"]
    assert "aisha_low_tier_spatial_grid_anchor:162->176" in repaired["notes"]


def test_aisha_red_residual_catalog_floor_uses_joint_count_and_cells() -> None:
    assert _catalog_min_total_value_for_quality_count_cells(6, 2, 40) == 1_039_600
    assert _catalog_min_total_value_for_quality_count_cells(6, 2, 33) is None


def test_aisha_fully_pinned_nonred_promotes_a_coherent_red_residual_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items: list[dict[str, int]] = []
    runtime_id = 0
    for quality, widths in (
        (1, [2] * 15 + [1] * 4),
        (3, [3] * 10 + [2] * 4),
        (4, [5] + [4] * 10),
    ):
        for width in widths:
            row = runtime_id % 17 + 1
            col = 1 if (runtime_id // 17) % 2 == 0 else max(1, 11 - width)
            runtime_id += 1
            items.append(
                {
                    "runtime_id": runtime_id,
                    "row": row,
                    "col": col,
                    "width": width,
                    "height": 1,
                    "quality": quality,
                    "cells": width,
                }
            )
    snapshot = _snapshot(
        hero="aisha",
        map_id=2549,
        structured_ref_inputs={
            "fixed_counts": {"q1": 19, "q3": 14, "q4": 11},
            "quality_cells": {"q1": 34, "q3": 38, "q4": 45, "q5": 3},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 4
    snapshot["ui_contract"]["minimap"] = {"items": items}
    snapshot["minimap_grid_items"] = items

    repaired = run_reference_engine(snapshot, max_combos=20_000).as_dict()
    for flag in (
        "BIDKING_DISABLE_AISHA_PINNED_RED_RESIDUAL_FLOOR",
        "BIDKING_DISABLE_AISHA_PINNED_RED_RESIDUAL_VALUE",
        "BIDKING_DISABLE_AISHA_PINNED_RED_CATALOG_FLOOR",
        "BIDKING_DISABLE_AISHA_PINNED_NONRED_RESIDUAL_BAND",
    ):
        monkeypatch.setenv(flag, "1")
    legacy = run_reference_engine(snapshot, max_combos=20_000).as_dict()

    assert repaired["red_count_range"] == [3, 6, 8]
    assert repaired["red_cells_range"] == [33, 36, 40]
    assert repaired["balanced"] > legacy["balanced"]
    assert "aisha_pinned_red_residual_value:36" in repaired["notes"]
    assert "q6" not in repaired["quality_count_cells_candidates"]


def test_aisha_red_room_high_cannot_exceed_displayed_red_count_catalog_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A red-or-empty room of 100 cells is not a two-red footprint; two q6 items cap at 40."""
    snapshot = _snapshot(
        hero="aisha",
        map_id=2549,
        structured_ref_inputs={
            "fixed_counts": {"q1": 19, "q3": 14, "q4": 11},
            "quality_cells": {"q1": 34, "q3": 38, "q4": 45, "q5": 3},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 4
    evidence = extract_evidence(snapshot)
    base = run_reference_engine(snapshot, max_combos=20_000)
    seeded = replace(
        base,
        red_count_range=(1, 1, 2),
        total_grid_range=(180, 200, 220),
        red_cells_range=(3, 6, 30),
        quality_cells_ranges={**base.quality_cells_ranges, "q6": (3, 6, 30)},
    )

    monkeypatch.delenv(Q6_CATALOG_CELL_COMPOSE_DISABLE, raising=False)
    repaired = _apply_aisha_pinned_nonred_room_hi(seeded, evidence, round_no=4)
    assert repaired.red_cells_range == (20, 20, 40)
    assert repaired.quality_cells_ranges["q6"] == (20, 20, 40)
    assert (repaired.conservative, repaired.balanced, repaired.aggressive) == (
        seeded.conservative,
        seeded.balanced,
        seeded.aggressive,
    )

    monkeypatch.setenv(Q6_CATALOG_CELL_COMPOSE_DISABLE, "1")
    legacy = _apply_aisha_pinned_nonred_room_hi(seeded, evidence, round_no=4)
    assert legacy.red_cells_range == (3, 6, 100)


def test_aisha_pinned_nonred_room_high_requires_every_nonred_cell_total() -> None:
    snapshot = _snapshot(
        hero="aisha",
        map_id=2549,
        structured_ref_inputs={
            "fixed_counts": {"q1": 19, "q3": 14, "q4": 11},
            # q5 total cells are intentionally absent: red/empty room is not isolated.
            "quality_cells": {"q1": 34, "q3": 38, "q4": 45},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 4
    evidence = extract_evidence(snapshot)
    base = run_reference_engine(snapshot, max_combos=20_000)
    seeded = replace(base, total_grid_range=(153, 160, 170), red_cells_range=(3, 6, 40))

    assert _apply_aisha_pinned_nonred_room_hi(seeded, evidence, round_no=4) == seeded


def test_ref_engine_public_gold_total_cells_zero_locks_q5_like_screenshot() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={
                "total_count": 25,
                "counts": {"q3": 8},
            },
            public_rows=[
                {"info_id": 200011, "value": 0},
                {"info_id": 200017, "value": 25},
            ],
        ),
        max_combos=80_000,
    ).as_dict()

    evidence = result["evidence"]

    assert evidence["quality_cells"]["q5"] == 0.0
    assert evidence["fixed_counts"]["q5"] == 0
    assert "public_info_200011_q5_cells" in result["notes"]
    assert "zero_quality_cells_q5_count_zero" in result["notes"]
    assert result["quality_count_ranges"]["q5"] == [0, 0, 0]
    assert result["quality_cells_ranges"]["q5"] == [0, 0, 0]


def test_ref_engine_structured_bridge_gold_total_cells_zero_locks_q5() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={
                "total_count": 25,
                "quality_cells": {"q5": 0},
            },
        ),
        max_combos=80_000,
    ).as_dict()

    assert result["evidence"]["fixed_counts"]["q5"] == 0
    assert result["quality_count_ranges"]["q5"] == [0, 0, 0]


PUBLIC_EXACT_QUALITY_CELLS_ZERO_CASES = [
    pytest.param(200010, "q4", id="public_q4_total_cells_zero"),
    pytest.param(200011, "q5", id="public_q5_total_cells_zero"),
    pytest.param(200012, "q6", id="public_q6_total_cells_zero"),
]

PUBLIC_EXACT_QUALITY_CELLS_NONZERO_CASES = [
    pytest.param(200010, "q4", 17, id="public_q4_total_cells_17"),
    pytest.param(200011, "q5", 24, id="public_q5_total_cells_24"),
    pytest.param(200012, "q6", 9, id="public_q6_total_cells_9"),
]

PUBLIC_EXACT_QUALITY_COUNT_LOCK_CASES = [
    pytest.param(200018, "q4", 6, id="public_q4_count_6"),
    pytest.param(200019, "q5", 3, id="public_q5_count_3"),
    pytest.param(200020, "q6", 2, id="public_q6_count_2"),
]

PUBLIC_EXACT_QUALITY_COUNT_ZERO_CASES = [
    pytest.param(200018, "q4", id="public_q4_count_zero"),
    pytest.param(200019, "q5", id="public_q5_count_zero"),
    pytest.param(200020, "q6", id="public_q6_count_zero"),
]


@pytest.mark.parametrize("info_id,quality_key", PUBLIC_EXACT_QUALITY_CELLS_ZERO_CASES)
def test_ref_engine_public_exact_quality_cells_zero_locks_count(
    info_id: int,
    quality_key: str,
) -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={"total_count": 30},
            public_rows=[{"info_id": info_id, "value": 0}],
        )
    )

    assert evidence.quality_cells[quality_key] == 0.0
    assert evidence.fixed_counts[quality_key] == 0
    assert f"public_info_{info_id}_{quality_key}_cells" in evidence.source_notes
    assert f"zero_quality_cells_{quality_key}_count_zero" in evidence.source_notes


@pytest.mark.parametrize("info_id,quality_key,value", PUBLIC_EXACT_QUALITY_CELLS_NONZERO_CASES)
def test_ref_engine_public_exact_quality_cells_nonzero_does_not_force_zero(
    info_id: int,
    quality_key: str,
    value: int,
) -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={"total_count": 30},
            public_rows=[{"info_id": info_id, "value": value}],
        )
    )

    assert evidence.quality_cells[quality_key] == float(value)
    assert evidence.fixed_counts.get(quality_key) != 0
    assert f"public_info_{info_id}_{quality_key}_cells" in evidence.source_notes
    assert f"zero_quality_cells_{quality_key}_count_zero" not in evidence.source_notes


@pytest.mark.parametrize("info_id,quality_key,value", PUBLIC_EXACT_QUALITY_COUNT_LOCK_CASES)
def test_ref_engine_public_exact_quality_count_locks_ranges(
    info_id: int,
    quality_key: str,
    value: int,
) -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={"total_count": 30},
            public_rows=[{"info_id": info_id, "value": value}],
        ),
        max_combos=80_000,
    ).as_dict()

    assert result["evidence"]["fixed_counts"][quality_key] == value
    assert result["quality_count_ranges"][quality_key] == [value, value, value]
    assert f"public_info_{info_id}_{quality_key}_count" in result["notes"]


@pytest.mark.parametrize("info_id,quality_key", PUBLIC_EXACT_QUALITY_COUNT_ZERO_CASES)
def test_ref_engine_public_exact_quality_count_zero_locks_ranges(
    info_id: int,
    quality_key: str,
) -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={"total_count": 30},
            public_rows=[{"info_id": info_id, "value": 0}],
        ),
        max_combos=80_000,
    ).as_dict()

    assert result["evidence"]["fixed_counts"][quality_key] == 0
    assert result["quality_count_ranges"][quality_key] == [0, 0, 0]
    assert f"public_info_{info_id}_{quality_key}_count" in result["notes"]


PUBLIC_EXACT_SESSION_INFO_CASES = [
    pytest.param(200017, "total_count", 25, "public_info_total_item_count", id="public_total_count_25"),
    pytest.param(200009, "total_grid_target", 100, "public_info_total_cells", id="public_total_cells_100"),
]


@pytest.mark.parametrize(
    "info_id,evidence_attr,expected_value,note_token",
    PUBLIC_EXACT_SESSION_INFO_CASES,
)
def test_ref_engine_public_exact_session_info_rows(
    info_id: int,
    evidence_attr: str,
    expected_value: int,
    note_token: str,
) -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            public_rows=[{"info_id": info_id, "value": expected_value}],
        )
    )

    assert getattr(evidence, evidence_attr) == expected_value
    assert note_token in evidence.source_notes


def test_ref_engine_public_exact_quality_cells_conflict_preserves_bridge() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="ahmed",
            map_id=2410,
            structured_ref_inputs={
                "total_count": 25,
                "quality_cells": {"q5": 24},
            },
            public_rows=[{"info_id": 200011, "value": 0}],
        )
    )

    assert evidence.quality_cells["q5"] == 24.0
    assert evidence.fixed_counts.get("q5") != 0
    assert "public_info_200011_q5_cells_conflict" in evidence.source_notes
    assert "zero_quality_cells_q5_count_zero" not in evidence.source_notes


def test_ref_engine_public_q5_avg_value_and_count_zero_from_public_numeric_facts() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="maria",
            map_id=2102,
            structured_ref_inputs={"total_count": 20},
            public_info={
                "public_numeric_facts": [
                    {
                        "info_id": 200037,
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "label": "金均价",
                        "value": 0.0,
                        "display_value": "0.00",
                        "text": "金均价 0.00",
                    },
                    {
                        "info_id": 200019,
                        "semantic": "q5_count",
                        "kind": "count",
                        "quality": 5,
                        "label": "金件",
                        "value": 0,
                        "display_value": "0",
                        "text": "金件 0",
                    },
                ],
                "public_avg_values": [
                    {
                        "info_id": 200037,
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "label": "金均价",
                        "value": 0.0,
                        "display_value": "0.00",
                        "text": "金均价 0.00",
                    }
                ],
            },
            public_rows=[
                {"info_id": 200037, "value": 0.0},
                {"info_id": 200019, "value": 0},
            ],
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["evidence"]["avg_values"]["q5"] == 0.0
    assert result["evidence"]["fixed_counts"]["q5"] == 0
    assert result["quality_count_ranges"]["q5"] == [0, 0, 0]
    assert "public_q5_avg_value" in result["notes"]
    # The explicit q5 count fact (200019=0) locks the count before the zero-avg
    # fallback runs, so q5=0 is attributed to the count fact rather than emitting
    # a redundant zero_avg_value_q5_count_zero note.
    assert "public_info_200019_q5_count" in result["notes"]


def test_ref_engine_public_q5_avg_cells_zero_locks_count() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="maria",
            map_id=2101,
            structured_ref_inputs={"total_count": 20},
            public_info={
                "public_numeric_facts": [
                    {
                        "info_id": 200015,
                        "semantic": "q5_avg_cells",
                        "kind": "avg_cells",
                        "quality": 5,
                        "label": "金均格",
                        "value": 0.0,
                        "display_value": "0.00",
                        "text": "金均格 0.00",
                    }
                ],
                "public_avg_cells": [
                    {
                        "info_id": 200015,
                        "semantic": "q5_avg_cells",
                        "kind": "avg_cells",
                        "quality": 5,
                        "label": "金均格",
                        "value": 0.0,
                        "display_value": "0.00",
                        "text": "金均格 0.00",
                    }
                ],
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["evidence"]["avg_cells"]["q5"] == 0.0
    assert result["evidence"]["fixed_counts"]["q5"] == 0
    assert result["quality_count_ranges"]["q5"] == [0, 0, 0]
    assert result["quality_cells_ranges"]["q5"] == [0, 0, 0]
    assert "public_q5_avg_cells" in result["notes"]
    assert "zero_avg_cells_q5_count_zero" in result["notes"]


def test_ref_engine_ahmed_express_zero_purple_cells_and_gold_avg_lock_counts() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2105,
            structured_ref_inputs={
                "total_count": 17,
                "quality_cells": {"q4": 0},
                "avg_cells": {"q5": 0.0},
            },
            public_rows=[
                {"info_id": 200010, "value": 0, "value_field": 14},
            ],
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q4"] == [0, 0, 0]
    assert result["quality_count_ranges"]["q5"] == [0, 0, 0]
    assert result["evidence"]["fixed_counts"]["q4"] == 0
    assert result["evidence"]["fixed_counts"]["q5"] == 0
    assert "public_info_200010_q4_cells" in result["notes"]
    assert "zero_quality_cells_q4_count_zero" in result["notes"]
    assert "zero_avg_cells_q5_count_zero" in result["notes"]


def test_ref_engine_victor_q4_q5_q6_count_sum_and_zero_gold_avg() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "total_cells": 34,
                "avg_cells": {"q4": 1.8, "q5": 0},
                "count_sums": {"q4q5q6": 6},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    evidence = result["evidence"]
    ranges = result["quality_count_ranges"]

    assert result["status"] == "ok"
    assert evidence["count_sums"] == {"q4q5q6": 6}
    assert evidence["fixed_counts"]["q5"] == 0
    assert "zero_avg_cells_q5_count_zero" in result["notes"]
    assert ranges["q5"] == [0, 0, 0]
    assert ranges["q4"] == [5, 5, 5]
    assert result["red_count_range"] == [1, 1, 1]
    assert result["quality_cells_ranges"]["q4"] == [9, 9, 9]
    assert result["quality_cells_ranges"]["q5"] == [0, 0, 0]
    # H7 (2026-07-10): with the red COUNT pinned and its footprint unseen, the display hi carries the
    # giant-red tail hedge (count_hi × family per-item p90) — a single unseen red CAN be an 8-cell
    # giant (5602: 5 reds × 8.2). lo/mid stay the combo bounds.
    assert result["red_cells_range"][:2] == [2, 3]
    assert result["red_cells_range"][2] >= 4


def test_ref_engine_victor_inferred_zero_action_constrains_gold_avg() -> None:
    snapshot = _snapshot(
        hero="victor",
        map_id=2404,
        structured_ref_inputs={
            "total_count": 21,
            "total_cells": 34,
            "avg_cells": {"q4": 1.8},
            "count_sums": {"q4q5q6": 6},
        },
    )
    snapshot["ui_contract"]["actions"] = {
        "results": [
            {
                "action_id": "100113",
                "result": "0",
                "inferred_zero": "True",
            }
        ],
    }

    result = run_reference_engine(snapshot, max_combos=60_000).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["avg_cells"]["q5"] == 0.0
    assert result["evidence"]["fixed_counts"]["q5"] == 0
    assert result["quality_count_ranges"]["q5"] == [0, 0, 0]
    assert result["quality_cells_ranges"]["q5"] == [0, 0, 0]
    assert "action_100113_q5_avg_cells_inferred_zero" in result["notes"]


def test_ref_engine_victor_decimal_q4_avg_keeps_reachable_counts() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2401,
            structured_ref_inputs={
                "avg_cells": {"q4": 1.8666666746139526},
                "count_sums": {"q4q5q6": 18},
            },
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["combo_count"] > 0
    assert result["quality_count_ranges"]["q4"][1] == 15
    assert result["quality_cells_ranges"]["q4"][1] == 28
    assert result["red_count_range"][1] is not None


def test_ref_engine_sparse_map_family_prior_centers_cover_non_villa_maps() -> None:
    cases = [
        (2101, "nest_price:2001", "tier_prob:101", "total_count_prior_center:24"),
        (2201, "nest_price:2011", "tier_prob:102", "total_count_prior_center:24"),
        (2301, "nest_price:2021", "tier_prob:103", "total_count_prior_center:27"),
        (2401, "nest_price:2031", "tier_prob:104", "total_count_prior_center:28"),
        (2501, "nest_price:2041", "tier_prob:105", "total_count_prior_center:33"),
        (2601, "fallback_default_price", "tier_prob:106", "total_count_prior_center:33"),
    ]

    for map_id, price_note, tier_note, count_center_note in cases:
        result = run_reference_engine(
            _snapshot(
                hero="victor",
                map_id=map_id,
                structured_ref_inputs={"min_counts": {"q4": 1}},
            ),
            max_combos=500,
        ).as_dict()

        assert result["status"] == "count_prior"
        assert result["combo_count"] > 0
        assert price_note in result["notes"]
        # Post-2026-06-25 drop-table wiring: covered maps now carry the per-map drop-table quality
        # prior (drop_table_prob:<map_id>) instead of the legacy StaticData tier prior (tier_prob:NNN).
        # Either source satisfies the intent (a per-map quality prior exists for this family).
        assert tier_note in result["notes"] or f"drop_table_prob:{map_id}" in result["notes"]
        assert "total_count_from_ref_count_prior" in result["notes"]
        assert count_center_note in result["notes"]


def test_ref_engine_known_non_villa_maps_are_reachable_with_explicit_total() -> None:
    for map_id, tier_note in ((2101, "tier_prob:101"), (2301, "tier_prob:103"), (2601, "tier_prob:106")):
        result = run_reference_engine(
            _snapshot(
                hero="victor",
                map_id=map_id,
                structured_ref_inputs={
                    "total_count": 24 if map_id == 2101 else 33,
                    "count_sums": {"q4q5q6": 4},
                },
            ),
            max_combos=500,
        ).as_dict()

        assert result["status"] == "ok"
        assert result["combo_count"] > 0
        # Either the legacy StaticData tier prior or the new per-map drop-table prior (see note above).
        assert tier_note in result["notes"] or f"drop_table_prob:{map_id}" in result["notes"]


def test_ref_engine_avg_cells_map_to_integer_grid_options() -> None:
    assert _avg_grid_options(4, 1.8) == []
    assert _avg_grid_options(5, 1.8) == [9]
    assert _avg_grid_options(6, 1.8) == []
    assert _avg_grid_options(0, 0) == [0]
    assert _avg_grid_options(1, 0) == []


def test_ref_engine_avg_grid_options_use_game_display_for_truncated_ratios() -> None:
    assert _avg_grid_options(11, 2.09) == [23]
    assert _avg_grid_options(11, 23 / 11) == [23]
    assert _avg_grid_options(11, 2.9) == [32]
    assert _avg_grid_options(10, 2.9) == [29]
    assert _avg_grid_options(7, 2.4285714285714284) == [17]
    assert _avg_grid_options(16, 3.4375) == [55]


@pytest.mark.parametrize(
    ("avg", "count", "cells", "display", "source_note"),
    CAPTURE_AVG_CELL_FIXTURES,
)
def test_ref_engine_avg_grid_options_match_capture_and_display_samples(
    avg: float,
    count: int,
    cells: int,
    display: str,
    source_note: str,
) -> None:
    del source_note
    assert _avg_grid_options(count, avg) == [cells]
    assert _avg_count_from_cells(avg, cells) == count


def test_ref_engine_avg_grid_options_reject_nearby_capture_mismatch() -> None:
    # 24/7 packet float displays as 3.42, not 3.43 — must not lock 55 cells at count 16.
    assert _avg_grid_options(16, 3.4285714626312256) == []
    assert _avg_grid_options(7, 3.4285714626312256) == [24]


@pytest.mark.skipif(
    not FATBEANS_Q4_290_SAMPLE.exists(),
    reason="fatbeans q4 2.90 capture sample is not available",
)
def test_ref_engine_avg_grid_options_load_q4_290_from_fatbeans_capture() -> None:
    from bidking_lab.inference.display import format_value
    from bidking_lab.live.fatbeans import parse_fatbeans_capture

    events = parse_fatbeans_capture(FATBEANS_Q4_290_SAMPLE)
    observed = [
        float(info.value)
        for state in events.states
        for info in state.public_infos
        if info.info_id == 200013 and info.value is not None
    ]

    assert observed
    avg = observed[0]
    assert avg == pytest.approx(2.909090995788574, abs=1e-6)
    assert _avg_grid_options(11, avg) == [32]
    assert format_value(32, 11) == "2.90"
    assert _avg_grid_options(22, avg) == [64]


def test_ref_engine_q5_avg_209_locks_count_from_group_report() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2404,
            structured_ref_inputs={
                "total_count": 21,
                "avg_cells": {"q5": 2.09},
            },
        ),
        max_combos=80_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q5"] == [11, 11, 11]


def test_ref_engine_avg_count_from_cells_accepts_display_truncated_ratios() -> None:
    assert _avg_count_from_cells(2.09, 23) == 11
    assert _avg_count_from_cells(23 / 11, 23) == 11
    assert _avg_count_from_cells(2.909090995788574, 32) == 11
    assert _avg_count_from_cells(2.4285714626312256, 17) == 7
    assert _avg_count_from_cells(3.43, 55) == 16
    assert _avg_count_from_cells(55 / 16, 55) == 16
    assert _avg_count_from_cells(2.9, 32) == 11
    assert _avg_count_from_cells(1.8, 9) == 5


def test_ref_engine_total_grid_fit_keeps_scalable_cells_integer_and_composable() -> None:
    counts = {"q1": 4, "q3": 9, "q4": 5, "q5": 0, "q6": 2}
    grids = {"q1": 5.2, "q3": 18.4, "q4": 9.0, "q5": 0.0, "q6": 4.6}

    fitted = _fit_grids_to_total_target(
        grids,
        counts,
        {"q4": 1.8, "q5": 0.0},
        34.0,
    )

    assert fitted["q4"] == 9.0
    assert fitted["q5"] == 0.0
    assert sum(fitted.values()) == 34.0
    for key, value in fitted.items():
        assert value == int(value)
        assert can_compose_grid_total(counts[key], int(value))


def test_ref_engine_exact_grid_fit_respects_revealed_cell_floor(monkeypatch) -> None:
    # Gabriela 2405 R2: the skill revealed one 10-cell red item. The old exact-DP fit chose q6=9
    # to hit the soft 61-cell target, after which the caller rejected the otherwise valid q6=1 combo.
    counts = {"q1": 11, "q3": 10, "q4": 5, "q5": 2, "q6": 1}
    grids = {"q1": 24.0, "q3": 22.0, "q4": 12.0, "q5": 6.0, "q6": 10.0}
    cell_floors = {"q1": 3.0, "q3": 6.0, "q4": 4.0, "q5": 1.0, "q6": 10.0}

    monkeypatch.delenv("DRAW_HERO_DISABLE_FIT_CELLFLOOR_FLOOR", raising=False)
    fitted = _fit_grids_to_total_target(
        grids,
        counts,
        {},
        61.0,
        cell_floors=cell_floors,
    )

    assert sum(fitted.values()) == 61.0
    assert fitted["q6"] >= 10.0
    for key, value in fitted.items():
        assert can_compose_grid_total(counts[key], int(value))

    # Validate the regression instrument: disabling the floor contract must reproduce the old q6=9.
    monkeypatch.setenv("DRAW_HERO_DISABLE_FIT_CELLFLOOR_FLOOR", "1")
    legacy = _fit_grids_to_total_target(
        grids,
        counts,
        {},
        61.0,
        cell_floors=cell_floors,
    )
    assert sum(legacy.values()) == 61.0
    assert legacy["q6"] == 9.0


def test_ref_engine_legacy_victor_q4_q5_count_sum_still_supported() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="victor",
            structured_ref_inputs={
                "total_count": 21,
                "count_sums": {"q4q5": 6},
            },
        )
    )

    assert evidence.count_sums == {"q4q5": 6}


def test_ref_engine_recognizes_full_hero_id_map() -> None:
    evidence = extract_evidence(
        {
            "ui_contract": {
                "context": {
                    "hero": "?",
                    "hero_id": 110,
                    "map_id": 2401,
                    "phase": "bidding",
                },
                "constraints": {"public_info": {}},
            },
            "structured_ref_inputs": {"total_count": 30},
            "public_info_rows": [],
        }
    )

    assert evidence.hero == "isabella"


def test_ref_engine_generic_hero_runs_reference_engine() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ethan",
            structured_ref_inputs={"total_count": 28},
        )
    )

    assert result.status != "not_structured_hero"
    assert "generic_ref_hero" in result.notes


def test_ref_engine_ethan_full_outline_skill_sets_total_grid_target() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="ethan",
            structured_ref_inputs={"total_count": 34},
        )
        | {
            "skill_reveals": [
                {
                    "skill_id": 1002085,
                    "hero_id": 208,
                    "observed_items": [
                        {
                            "runtime_id": 1,
                            "shape_code": 11,
                            "cells": 1,
                        },
                        {
                            "runtime_id": 2,
                            "shape_code": 22,
                            "cells": 4,
                        },
                    ],
                }
            ],
        }
    )

    assert evidence.total_count == 2
    assert evidence.total_grid_target == 5.0
    assert "ethan_skill_full_outline_count" in evidence.source_notes
    assert "ethan_skill_full_outline_cells" in evidence.source_notes
    assert any(
        note.startswith("ethan_skill_full_outline_count_conflicts_total_count:")
        for note in evidence.source_notes
    )


def test_ref_engine_ethan_r1_outline_is_diagnostic_only() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="ethan",
            structured_ref_inputs={"total_count": 28},
        )
        | {
            "skill_reveals": [
                {
                    "skill_id": 1002081,
                    "hero_id": 208,
                    "observed_items": [
                        {"runtime_id": 1, "shape_code": 23, "cells": 4},
                        {"runtime_id": 2, "shape_code": 11, "cells": 1},
                    ],
                }
            ],
        }
    )

    assert evidence.total_grid_target is None
    assert "ethan_skill_r1_outline:2:5" in evidence.source_notes
    assert "ethan_skill_full_outline_cells" not in evidence.source_notes


def test_ref_engine_public_max_quality_gold_locks_q6_to_zero() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="isabella",
            structured_ref_inputs={"total_count": 24},
            public_rows=[
                {
                    "info_id": 200048,
                    "revealed_items_detail": [
                        {"runtime_id": 501, "quality": 5},
                    ],
                }
            ],
        )
    )

    assert evidence.fixed_counts.get("q6") == 0
    assert evidence.min_counts.get("q6") == 0
    assert "public_max_quality_ceiling:5" in evidence.source_notes
    assert "public_max_quality_zero_q6" in evidence.source_notes


def test_ref_engine_public_max_quality_conflicts_with_existing_red_count() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="isabella",
            structured_ref_inputs={"total_count": 24},
            public_rows=[
                {
                    "info_id": 200048,
                    "revealed_items_detail": [
                        {"runtime_id": 501, "quality": 5},
                    ],
                },
                {
                    "info_id": 200028,
                    "revealed_items_detail": [
                        {"runtime_id": 601, "quality": 6},
                    ],
                },
            ],
        )
    )

    assert "hard_conflict:public_max_quality_zero_q6" in evidence.source_notes
    assert evidence.min_counts.get("q6", 0) >= 1


def test_ref_engine_public_max_quality_full_item_uses_catalog_value_floor() -> None:
    """200048 with item_id+shape must floor at catalog price, not long-tail normalized EV."""
    evidence = extract_evidence(
        _snapshot(
            hero="ahmed",
            map_id=2408,
            structured_ref_inputs={"total_count": 54},
            public_rows=[
                {
                    "info_id": 200048,
                    "revealed_items_detail": [
                        {
                            "local_index": 40,
                            "runtime_id": 1630026029583307,
                            "item_id": 1086004,
                            "quality": 6,
                            "value": 1_688_400,
                            "shape_code": 22,
                            "cells": 4,
                        }
                    ],
                }
            ],
        )
    )

    assert evidence.quality_value_floors.get("q6") == 1_688_400.0
    assert evidence.quality_value_floor_item_counts.get("q6") == 1
    assert evidence.quality_cell_floors.get("q6") == 4.0
    assert "public_quality_reveal_q6_value_floor" in evidence.source_notes


@pytest.mark.parametrize("quality_only_first", [True, False])
def test_ref_engine_duplicate_reveal_keeps_identified_red_value_floor(
    quality_only_first: bool,
) -> None:
    """Two skills revealing the SAME red (same runtime_id) must not drop its catalog value floor.

    gabriela 2401 R4: 二抽 gave the full 氦-3燃料罐 (item_id+313.2k+shape) while 宝光四鉴 gave the
    same item quality-only. The value-less quality-only reveal claimed the runtime_id first and the
    floor dedup skipped the value-bearing twin -> q6 value floor lost -> red_value floor fell to the
    quality prior (~190k) and the headline "直接降下来". The floor must come from the richest reveal
    of the physical item regardless of reveal order.
    """
    runtime_id = 1654627627538009
    full = {
        "local_index": 25,
        "runtime_id": runtime_id,
        "item_id": 1916030,
        "quality": 6,
        "value": 313200,
        "shape_code": 23,
        "cells": 6,
    }
    quality_only = {
        "local_index": 35,
        "runtime_id": runtime_id,
        "item_id": None,
        "quality": 6,
        "value": None,
    }
    rows = (
        [{"action_id": 100136, "revealed_items_detail": [quality_only]},
         {"action_id": 100135, "revealed_items_detail": [full]}]
        if quality_only_first
        else [{"action_id": 100135, "revealed_items_detail": [full]},
              {"action_id": 100136, "revealed_items_detail": [quality_only]}]
    )
    evidence = extract_evidence(
        _snapshot(hero="gabriela", structured_ref_inputs={"total_count": 28}, public_rows=[])
        | {"action_result_rows": rows}
    )

    # Identified red keeps its exact catalog value floor; counted once (not double-counted as 2 reds).
    assert evidence.quality_value_floors.get("q6") == 313200.0
    assert evidence.quality_value_floor_item_counts.get("q6") == 1
    assert evidence.min_counts.get("q6") == 1
    assert "public_quality_reveal_q6_value_floor" in evidence.source_notes


def _gabriela_two_known_one_cell_reds_snapshot() -> dict:
    """Minimal 5601 R3 contract: two public red items, both with exact 1-cell footprints."""
    known_reds = [
        {
            "local_index": 11,
            "runtime_id": 101,
            "item_id": 560_101,
            "quality": 6,
            "value": 158_550,
            "shape_code": 11,
            "cells": 1,
        },
        {
            "local_index": 12,
            "runtime_id": 102,
            "item_id": 560_102,
            "quality": 6,
            "value": 71_500,
            "shape_code": 11,
            "cells": 1,
        },
    ]
    quality_only = [
        {
            "local_index": item["local_index"],
            "runtime_id": item["runtime_id"],
            "item_id": None,
            "quality": 6,
            "value": None,
        }
        for item in known_reds
    ]
    snapshot = _snapshot(
        hero="gabriela",
        map_id=5601,
        phase="bidding",
        structured_ref_inputs={"total_count": 30, "total_cells": 57},
        public_rows=[
            {"info_id": 200028, "revealed_items_detail": quality_only},
            {"info_id": 200023, "revealed_items_detail": known_reds},
        ],
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["minimap_grid_items"] = known_reds
    return snapshot


def test_draw_hero_matching_known_red_count_uses_exact_cells_and_has_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """U02 / 5601 R3: two known 1-cell reds make the two-red candidate exactly 2 cells.

    A three-red candidate still has one hidden red, so only the known two-cell base is fixed. The
    dedicated red output and generic q6 output must stay identical; otherwise the panel and API/audit
    contracts disagree. The disable env validates the instrument by reproducing the old 5/7/11 prior.
    """
    snapshot = _gabriela_two_known_one_cell_reds_snapshot()

    monkeypatch.delenv("DRAW_HERO_DISABLE_EXACT_REVEALED_RED_CELLS", raising=False)
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()

    assert fixed["red_count_range"] == [2, 2, 3]
    assert fixed["red_cells_range"] == [2, 2, 6]
    assert fixed["quality_cells_ranges"]["q6"] == fixed["red_cells_range"]
    assert fixed["red_cells_range"][2] > 2  # one hidden red remains a floor/prior, not exact 2
    assert fixed["evidence"]["quality_cell_floors"]["q6"] == 2.0
    assert fixed["evidence"]["quality_cell_floor_item_counts"]["q6"] == 2
    assert "draw_revealed_red_exact_small_fp:2items:2cells" in fixed["notes"]

    monkeypatch.setenv("DRAW_HERO_DISABLE_EXACT_REVEALED_RED_CELLS", "1")
    legacy = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()

    assert legacy["red_count_range"] == fixed["red_count_range"]
    assert legacy["red_cells_range"] == [5, 7, 11]
    assert legacy["quality_cells_ranges"]["q6"] == legacy["red_cells_range"]
    assert "draw_revealed_red_exact_small_fp:2items:2cells" not in legacy["notes"]


@pytest.mark.parametrize(
    ("shape_code", "cells", "catalog_floor"),
    [
        (11, 1, 52_500),
        (54, 20, 519_800),
    ],
)
def test_gabriela_known_q6_footprint_sets_catalog_minimum_quote_floor(
    monkeypatch: pytest.MonkeyPatch,
    shape_code: int,
    cells: int,
    catalog_floor: int,
) -> None:
    """A known q6 outline cannot be quoted below its cheapest catalog identity."""
    runtime_id = 1_700_600_930_276_192 + cells
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2541,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["ui_contract"]["context"]["round"] = 2
    snapshot["skill_reveals"] = [{
        "skill_id": 1001041,
        "hero_id": 104,
        "observed_items": [{
            "local_index": 74,
            "runtime_id": runtime_id,
            "item_id": None,
            "quality": 6,
            "value": None,
            "shape_code": shape_code,
            "cells": cells,
        }],
    }]

    monkeypatch.delenv(
        "BIDKING_DISABLE_KNOWN_Q6_FOOTPRINT_VALUE_FLOOR",
        raising=False,
    )
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()

    assert (
        fixed["evidence"]["quality_catalog_min_value_floors"]["q6"]
        == catalog_floor
    )
    assert fixed["evidence"]["quality_value_floors"].get("q6") is None
    assert fixed["evidence"]["quality_value_floor_item_counts"].get("q6") is None
    assert min(fixed["red_value_range"]) >= catalog_floor
    assert min(
        fixed["conservative"],
        fixed["balanced"],
        fixed["aggressive"],
    ) >= catalog_floor
    assert (
        f"known_q6_footprint_value_floor:{cells}:{catalog_floor}"
        in fixed["notes"]
    )

    monkeypatch.setenv(
        "BIDKING_DISABLE_KNOWN_Q6_FOOTPRINT_VALUE_FLOOR",
        "1",
    )
    legacy = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert (
        legacy["evidence"]["quality_catalog_min_value_floors"].get("q6")
        is None
    )
    assert "known_q6_footprint_value_floor" not in " ".join(legacy["notes"])
    assert fixed["conservative"] >= legacy["conservative"]
    assert fixed["balanced"] >= legacy["balanced"]
    assert fixed["aggressive"] >= legacy["aggressive"]
    if cells == 20:
        assert legacy["conservative"] < catalog_floor


def test_q6_footprint_value_floor_fails_open_for_non_catalog_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The droppable q6 catalog has no 18-cell item; do not invent a price floor."""
    snapshot = _snapshot(
        hero="gabriela",
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["skill_reveals"] = [{
        "skill_id": 1001041,
        "hero_id": 104,
        "observed_items": [{
            "runtime_id": 180_018,
            "quality": 6,
            "shape_code": 63,
            "cells": 18,
        }],
    }]
    monkeypatch.delenv(
        "BIDKING_DISABLE_KNOWN_Q6_FOOTPRINT_VALUE_FLOOR",
        raising=False,
    )

    evidence = extract_evidence(snapshot)

    assert (evidence.quality_catalog_min_value_floors or {}).get("q6") is None
    assert "known_q6_footprint_value_floor" not in " ".join(evidence.source_notes)


def test_q6_priced_and_footprint_only_item_floors_add_without_double_counting() -> None:
    """Distinct known q6 items contribute exact value plus catalog minimum."""
    snapshot = _snapshot(
        hero="gabriela",
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["action_result_rows"] = [
        {
            "action_id": 100135,
            "revealed_items_detail": [{
                "runtime_id": 6001,
                "item_id": 1916030,
                "quality": 6,
                "value": 313_200,
                "shape_code": 23,
                "cells": 6,
            }],
        },
        {
            "action_id": 100136,
            "revealed_items_detail": [{
                "runtime_id": 6002,
                "item_id": None,
                "quality": 6,
                "value": None,
                "shape_code": 54,
                "cells": 20,
            }],
        },
    ]

    evidence = extract_evidence(snapshot)

    assert evidence.quality_value_floors["q6"] == 313_200
    assert evidence.quality_catalog_min_value_floors == {"q6": 519_800}
    assert _known_quality_value_total_floor(evidence) == 833_000


def test_gabriela_one_known_one_cell_red_keeps_one_cell_minimum_and_scoping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5602:1700600930275700 R2: one width/height-known red is exactly one cell.

    The skill reveal carries ``item_id=None`` and ``cells=None``; its matching minimap item supplies
    ``shape_key=11`` and ``width=height=1``. The matching one-red candidate must therefore stay at one
    cell; candidates with hidden extra reds remain soft. The rollback arm reproduces the old two-cell
    minimum, and Ahmed proves the draw-hero exact-small-footprint path does not leak into other heroes.
    """
    runtime_id = 1_700_600_930_276_192
    snapshot = _snapshot(
        hero="gabriela",
        map_id=5602,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["ui_contract"]["context"]["round"] = 2
    snapshot["minimap_grid_items"] = [{
        "quality": 6,
        "runtime_id": runtime_id,
        "item_id": None,
        "local_index": 74,
        "cells": 1,
        "shape_key": "11",
        "row": 8,
        "col": 5,
        "width": 1,
        "height": 1,
        "value": None,
    }]
    snapshot["skill_reveals"] = [{
        "skill_id": 1001041,
        "hero_id": 104,
        "observed_items": [{
            "local_index": 74,
            "runtime_id": runtime_id,
            "item_id": None,
            "quality": 6,
            "value": None,
            "shape_code": 11,
            "cells": None,
        }],
    }]

    monkeypatch.delenv("DRAW_HERO_DISABLE_EXACT_REVEALED_RED_CELLS", raising=False)
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()

    assert fixed["red_count_range"] == [1, 2, 3]
    assert fixed["red_cells_range"] == [1, 3, 6]
    assert fixed["quality_cells_ranges"]["q6"] == fixed["red_cells_range"]
    assert fixed["evidence"]["quality_cell_floors"]["q6"] == 1.0
    assert fixed["evidence"]["quality_cell_floor_item_counts"]["q6"] == 1
    assert "draw_revealed_red_exact_small_fp:1items:1cells" in fixed["notes"]

    monkeypatch.setenv("DRAW_HERO_DISABLE_EXACT_REVEALED_RED_CELLS", "1")
    legacy = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert legacy["red_count_range"] == fixed["red_count_range"]
    assert legacy["red_cells_range"] == [2, 5, 9]
    assert "draw_revealed_red_exact_small_fp:1items:1cells" not in legacy["notes"]

    monkeypatch.delenv("DRAW_HERO_DISABLE_EXACT_REVEALED_RED_CELLS", raising=False)
    snapshot["ui_contract"]["context"]["hero"] = "ahmed"
    ahmed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert ahmed["red_cells_range"] != fixed["red_cells_range"]
    assert "draw_revealed_red_exact_small_fp:1items:1cells" not in ahmed["notes"]


def test_treasure_volume_merges_same_known_red_footprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2501:1700601005561702 R3: 100162 adds four cells to its already-known red identity."""
    runtime_id = 1_700_601_005_562_205
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2501,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[{
            "info_id": 200046,
            "revealed_items_detail": [{
                "local_index": 64,
                "runtime_id": runtime_id,
                "quality": 6,
            }],
        }],
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["action_result_rows"] = [{
        "action_id": 100162,
        "tool": "至宝体量",
        "revealed_items_detail": [{
            "local_index": 64,
            "runtime_id": runtime_id,
            "quality": None,
            "cells": 4,
        }],
    }]

    monkeypatch.delenv("BIDKING_DISABLE_TREASURE_TOP_QUALITY_RESOLVE", raising=False)
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert fixed["evidence"]["min_counts"]["q6"] == 1
    assert fixed["evidence"]["quality_cell_floors"]["q6"] == 4.0
    assert fixed["evidence"]["quality_cell_floor_item_counts"]["q6"] == 1
    assert fixed["red_cells_range"][0] == 4
    assert "treasure_top_quality_resolved:action_result_100162_same_runtime_q6" in fixed["notes"]

    monkeypatch.setenv("BIDKING_DISABLE_TREASURE_TOP_QUALITY_RESOLVE", "1")
    legacy = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert legacy["evidence"]["quality_cell_floors"].get("q6") is None
    assert legacy["red_cells_range"][0] < 4


def test_treasure_volume_does_not_double_same_known_red_footprint() -> None:
    """A repeated footprint for the same runtime enriches that item; it is not a second red."""
    runtime_id = 1_700_601_005_562_205
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2501,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["skill_reveals"] = [{
        "skill_id": 1001043,
        "hero_id": 104,
        "observed_items": [{
            "local_index": 64,
            "runtime_id": runtime_id,
            "quality": 6,
            "shape_code": 22,
        }],
    }]
    snapshot["minimap_grid_items"] = [{
        "local_index": 64,
        "runtime_id": runtime_id,
        "quality": 6,
        "cells": 4,
        "shape_key": "22",
        "width": 2,
        "height": 2,
        "row": 7,
        "col": 5,
    }]
    snapshot["action_result_rows"] = [{
        "action_id": 100162,
        "tool": "至宝体量",
        "revealed_items_detail": [{
            "local_index": 64,
            "runtime_id": runtime_id,
            "quality": None,
            "cells": 4,
        }],
    }]

    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert result["evidence"]["min_counts"]["q6"] == 1
    assert result["evidence"]["quality_cell_floors"]["q6"] == 4.0
    assert result["evidence"]["quality_cell_floor_item_counts"]["q6"] == 1
    assert result["red_count_range"][0] == 1
    assert result["red_cells_range"][0] == 4


def test_treasure_tracking_joins_later_same_runtime_red_without_double_count() -> None:
    """A qualityless top-item footprint can be enriched by a later q6 reveal of that runtime."""
    runtime_id = 1_700_601_006_348_395
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2510,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["action_result_rows"] = [{
        "action_id": 100161,
        "sort": 1,
        "revealed_items_detail": [{
            "local_index": 44,
            "runtime_id": runtime_id,
            "quality": None,
            "shape_code": 44,
        }],
    }]
    snapshot["skill_reveals"] = [{
        "skill_id": 1001043,
        "sort": 2,
        "observed_items": [{
            "local_index": 44,
            "runtime_id": runtime_id,
            "quality": 6,
        }],
    }]

    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert result["evidence"]["min_counts"]["q6"] == 1
    assert result["evidence"]["quality_cell_floors"]["q6"] == 16.0
    assert result["evidence"]["quality_cell_floor_item_counts"]["q6"] == 1
    assert result["red_count_range"][0] == 1
    assert result["red_cells_range"][0] == 16
    assert "treasure_top_quality_resolved:action_result_100161_same_runtime_q6" in result["notes"]


def test_treasure_tracking_same_runtime_gold_locks_zero_red() -> None:
    """If the highest-quality treasure target is later proven gold, the vault has no red."""
    runtime_id = 1_700_601_006_348_395
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2510,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["action_result_rows"] = [{
        "action_id": 100161,
        "sort": 1,
        "revealed_items_detail": [{
            "local_index": 44,
            "runtime_id": runtime_id,
            "quality": None,
            "shape_code": 44,
        }],
    }]
    snapshot["skill_reveals"] = [{
        "skill_id": 1001043,
        "sort": 2,
        "observed_items": [{
            "local_index": 44,
            "runtime_id": runtime_id,
            "quality": 5,
        }],
    }]

    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert result["evidence"]["min_counts"]["q5"] == 1
    assert result["evidence"]["quality_cell_floors"]["q5"] == 16.0
    assert result["evidence"]["quality_cell_floor_item_counts"]["q5"] == 1
    assert result["evidence"]["fixed_counts"]["q6"] == 0
    assert result["red_count_range"] == [0, 0, 0]
    assert "public_max_quality_ceiling:5" in result["notes"]
    assert "treasure_top_quality_resolved:action_result_100161_same_runtime_q5" in result["notes"]


def test_treasure_tracking_adds_distinct_known_red_footprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2510:1700601006348107 R4: known 2x2 red + distinct 100161 4x4 top item = 2/20 floor."""
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2510,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["ui_contract"]["context"]["round"] = 4
    snapshot["skill_reveals"] = [{
        "skill_id": 1001043,
        "hero_id": 104,
        "sort": 2,
        "observed_items": [{
            "local_index": 83,
            "runtime_id": 1_700_601_006_348_409,
            "quality": 6,
            "shape_code": 22,
        }],
    }]
    snapshot["minimap_grid_items"] = [{
        "local_index": 83,
        "runtime_id": 1_700_601_006_348_409,
        "quality": 6,
        "cells": 4,
        "shape_key": "22",
        "width": 2,
        "height": 2,
        "row": 9,
        "col": 4,
    }]
    snapshot["action_result_rows"] = [{
        "action_id": 100161,
        "tool": "至宝寻踪",
        "sort": 1,
        "revealed_items_detail": [{
            "local_index": 44,
            "runtime_id": 1_700_601_006_348_395,
            "quality": None,
            "shape_code": 44,
            "cells": None,
        }],
    }]

    monkeypatch.delenv("BIDKING_DISABLE_TREASURE_TOP_QUALITY_RESOLVE", raising=False)
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert fixed["evidence"]["min_counts"]["q6"] == 2
    assert fixed["evidence"]["quality_cell_floors"]["q6"] == 20.0
    assert fixed["evidence"]["quality_cell_floor_item_counts"]["q6"] == 2
    assert fixed["red_count_range"][0] == 2
    assert fixed["red_cells_range"][0] == 20
    assert fixed["quality_cells_ranges"]["q6"] == fixed["red_cells_range"]
    assert "treasure_top_quality_resolved:action_result_100161_highest_known_q6" in fixed["notes"]

    monkeypatch.setenv("BIDKING_DISABLE_TREASURE_TOP_QUALITY_RESOLVE", "1")
    legacy = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert legacy["evidence"]["min_counts"]["q6"] == 1
    assert legacy["evidence"]["quality_cell_floors"]["q6"] == 4.0
    assert legacy["red_count_range"][0] == 1
    assert legacy["red_cells_range"][0] == 4


def test_treasure_target_and_two_distinct_revealed_reds_count_all_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2543 R4: quality marker + treasure target + full random reveal are three reds."""
    monkeypatch.setenv("AHMAD_DISABLE_RED_CRATE_SHIPWRECK_RED_BOOST", "1")
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2543,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["ui_contract"]["context"]["round"] = 4
    snapshot["action_result_rows"] = [
        {
            "action_id": 100136,
            "tool": "宝光四鉴",
            "revealed_items_detail": [{
                "local_index": 121,
                "runtime_id": 1_741_282_828_794_930,
                "quality": 6,
            }],
        },
        {
            "action_id": 100161,
            "tool": "至宝寻踪",
            "revealed_items_detail": [{
                "local_index": 97,
                "runtime_id": 1_741_282_828_794_924,
                "quality": None,
                "shape_code": 22,
            }],
        },
        {
            "action_id": 100162,
            "tool": "至宝体量",
            "revealed_items_detail": [{
                "local_index": 108,
                "runtime_id": 1_741_282_828_794_924,
                "quality": None,
                "cells": 4,
            }],
        },
        {
            "action_id": 100129,
            "tool": "随机抽检（2）",
            "revealed_items_detail": [{
                "local_index": 37,
                "runtime_id": 1_741_282_828_794_902,
                "item_id": 1086006,
                "quality": 6,
                "value": 68800,
                "shape_code": 11,
                "cells": 1,
            }],
        },
    ]

    monkeypatch.delenv("AHMAD_DISABLE_DRAW_SHAPED_OUTLINE_COUNT", raising=False)
    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()

    assert result["evidence"]["min_counts"]["q6"] == 3
    assert result["evidence"]["quality_cell_floors"]["q6"] == 5.0
    assert result["evidence"]["quality_cell_floor_item_counts"]["q6"] == 2
    assert result["red_count_range"][0] == 3
    assert result["red_cells_range"][0] >= 6
    assert "coarse_quality_reveal_source:action_result_100129" in result["notes"]

    monkeypatch.setenv("AHMAD_DISABLE_DRAW_SHAPED_OUTLINE_COUNT", "1")
    legacy = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert legacy["evidence"]["min_counts"]["q6"] == 2
    assert legacy["red_count_range"] == [2, 2, 3]


def test_draw_full_reveal_does_not_double_same_runtime_quality_marker() -> None:
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2543,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    runtime_id = 1_741_282_828_794_902
    snapshot["action_result_rows"] = [
        {
            "action_id": 100136,
            "revealed_items_detail": [{
                "local_index": 37,
                "runtime_id": runtime_id,
                "quality": 6,
            }],
        },
        {
            "action_id": 100129,
            "revealed_items_detail": [{
                "local_index": 37,
                "runtime_id": runtime_id,
                "item_id": 1086006,
                "quality": 6,
                "value": 68800,
                "shape_code": 11,
                "cells": 1,
            }],
        },
    ]

    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert result["evidence"]["min_counts"]["q6"] == 1
    assert result["evidence"]["quality_cell_floors"]["q6"] == 1.0
    assert result["evidence"]["quality_cell_floor_item_counts"]["q6"] == 1
    assert result["red_count_range"][0] == 1


def test_treasure_tracking_does_not_infer_red_without_known_q6() -> None:
    snapshot = _snapshot(
        hero="gabriela",
        map_id=2510,
        phase="bidding",
        structured_ref_inputs={},
        public_rows=[],
    )
    snapshot["skill_reveals"] = [{
        "skill_id": 1001043,
        "hero_id": 104,
        "observed_items": [{
            "local_index": 83,
            "runtime_id": 101,
            "quality": 5,
            "shape_code": 22,
        }],
    }]
    snapshot["action_result_rows"] = [{
        "action_id": 100161,
        "tool": "至宝寻踪",
        "revealed_items_detail": [{
            "local_index": 44,
            "runtime_id": 102,
            "quality": None,
            "shape_code": 44,
        }],
    }]

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))
    assert evidence.min_counts.get("q6") is None
    assert evidence.quality_cell_floors.get("q6") is None
    assert not any("highest_known_q6" in note for note in evidence.source_notes)


def test_aisha_absent_white_reveal_infers_zero_and_locks_q1(monkeypatch) -> None:
    """aisha 0白仓: 白揭示帧(1001034)全局缺席但绿(1001033)已揭示 → 白揭示窗口已关闭且为 0 →
    白=0 → q1(白+绿)=绿件数锁死。**默认 ON**; DISABLE env 才回退不推断(全库 4/4=真0白, 0回归)。"""
    snap = _snapshot(hero="aisha", structured_ref_inputs={"total_count": 30}, public_rows=[]) | {
        "skill_reveals": [
            {"skill_id": 1001033, "hero_id": 103, "revealed_items_detail": [
                {"local_index": 5, "quality": 2}, {"local_index": 1, "quality": 2}]},
        ],
    }
    monkeypatch.setenv("AISHA_DISABLE_ABSENT_WHITE_ZERO", "1")          # 回退
    ev_off = extract_evidence(snap)
    assert ev_off.fixed_counts.get("q1") is None                        # 回退 → q1 不锁
    assert "aisha_absent_white_inferred_zero" not in ev_off.source_notes

    monkeypatch.delenv("AISHA_DISABLE_ABSENT_WHITE_ZERO", raising=False)  # 默认 ON
    ev_on = extract_evidence(snap)
    assert ev_on.fixed_counts.get("q1") == 2                            # 白(0)+绿(2) 锁死
    assert "aisha_absent_white_inferred_zero" in ev_on.source_notes
    assert "aisha_absent_low_quality_q1_lock" in ev_on.source_notes


def test_aisha_absent_green_reveal_infers_zero_after_blue_and_locks_q1(monkeypatch) -> None:
    """aisha 0绿仓: 绿揭示缺席但蓝已到，绿窗口已关闭 → 绿=0，q1 锁为白件数。"""
    snap = _snapshot(hero="aisha", structured_ref_inputs={"total_count": 30}, public_rows=[]) | {
        "skill_reveals": [
            {"skill_id": 1001034, "hero_id": 103, "revealed_items_detail": [
                {"local_index": 3, "quality": 1}, {"local_index": 8, "quality": 1}]},  # 白 2 件(present)
            {"skill_id": 1001032, "hero_id": 103, "revealed_items_detail": [
                {"local_index": 5, "quality": 3}]},                                # 蓝(更靠后, 绿缺席)
        ],
    }
    monkeypatch.setenv("AISHA_DISABLE_ABSENT_GREEN_ZERO", "1")
    ev_off = extract_evidence(snap)
    assert ev_off.fixed_counts.get("q1") is None
    assert "aisha_absent_green_inferred_zero" not in ev_off.source_notes

    monkeypatch.delenv("AISHA_DISABLE_ABSENT_GREEN_ZERO", raising=False)
    ev_on = extract_evidence(snap)
    assert ev_on.fixed_counts.get("q1") == 2
    assert "aisha_absent_green_inferred_zero" in ev_on.source_notes
    assert "aisha_absent_white_inferred_zero" not in ev_on.source_notes
    assert "aisha_absent_low_quality_q1_lock" in ev_on.source_notes


def test_aisha_absent_green_without_later_reveal_stays_conservative(monkeypatch) -> None:
    """R2 只看见白、绿帧尚未到时不能锁 0；须等待蓝/紫证明绿色窗口已经关闭。"""
    snap = _snapshot(hero="aisha", structured_ref_inputs={"total_count": 30}, public_rows=[]) | {
        "skill_reveals": [
            {"skill_id": 1001034, "hero_id": 103, "revealed_items_detail": [
                {"local_index": 3, "quality": 1}, {"local_index": 8, "quality": 1}]},
        ],
    }
    monkeypatch.delenv("AISHA_DISABLE_ABSENT_GREEN_ZERO", raising=False)
    ev = extract_evidence(snap)
    assert ev.fixed_counts.get("q1") is None
    assert "aisha_absent_green_inferred_zero" not in ev.source_notes


def test_aisha_absent_green_without_white_anchor_stays_conservative(monkeypatch) -> None:
    """只保留蓝/紫等后期帧的残缺快照不能证明绿色为 0；须先有明确白色拆分作为揭示链锚点。"""
    snap = _snapshot(hero="aisha", structured_ref_inputs={"total_count": 30}, public_rows=[]) | {
        "skill_reveals": [
            {"skill_id": 1001032, "hero_id": 103, "revealed_items_detail": [
                {"local_index": 5, "quality": 3}]},
        ],
    }
    monkeypatch.delenv("AISHA_DISABLE_ABSENT_GREEN_ZERO", raising=False)
    ev = extract_evidence(snap)
    assert ev.fixed_counts.get("q1") is None
    assert "aisha_absent_green_inferred_zero" not in ev.source_notes


def test_aisha_white_green_zero_via_cells_channel(monkeypatch) -> None:
    """白绿合并=0 的正解 = 显式 cells 通道(ahmed 道具"白绿均格/总格=0"): quality_cells.q1=0 → q1=0 锁。
    与揭示缺席推断不同通道; 不依赖 env, 恒生效。"""
    snap = {
        "ui_contract": {"context": {"hero": "ahmed", "round": 2, "phase": "bidding"}},
        "hero": "ahmed",
        "structured_ref_inputs": {"total_count": 30, "quality_cells": {"q1": 0}},
    }
    ev = extract_evidence(snap)
    assert ev.fixed_counts.get("q1") == 0                               # 白绿总格0 → q1=0 锁


def test_aisha_present_white_reveal_does_not_infer(monkeypatch) -> None:
    """白揭示存在(1001034 present)时绝不走推断 —— 防误 fire(白真>0 时锁0=回归)。"""
    snap = _snapshot(hero="aisha", structured_ref_inputs={"total_count": 30}, public_rows=[]) | {
        "skill_reveals": [
            {"skill_id": 1001034, "hero_id": 103, "revealed_items_detail": [{"local_index": 3, "quality": 1}]},
            {"skill_id": 1001033, "hero_id": 103, "revealed_items_detail": [{"local_index": 5, "quality": 2}]},
        ],
    }
    monkeypatch.delenv("AISHA_DISABLE_ABSENT_WHITE_ZERO", raising=False)  # 默认 ON
    ev = extract_evidence(snap)
    assert "aisha_absent_white_inferred_zero" not in ev.source_notes    # 白present → 不推断


def test_aisha_absent_white_no_later_reveal_stays_conservative(monkeypatch) -> None:
    """只有白缺席、后续品质也没揭示(揭示窗口未确定关闭)→ 保守不锁(白帧或许还没到, 防丢包误锁)。"""
    snap = _snapshot(hero="aisha", structured_ref_inputs={"total_count": 30}, public_rows=[]) | {
        "skill_reveals": [],  # 无任何 aisha 品质揭示
    }
    monkeypatch.delenv("AISHA_DISABLE_ABSENT_WHITE_ZERO", raising=False)  # 默认 ON
    ev = extract_evidence(snap)
    assert "aisha_absent_white_inferred_zero" not in ev.source_notes


def test_ref_engine_maria_coarse_quality_and_value_from_action_rows() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="maria",
            structured_ref_inputs={"total_count": 24},
            public_rows=[],
        )
        | {
            "action_result_rows": [
                {
                    "action_id": 100134,
                    "tool": "明镜之眼",
                    "revealed_items_detail": [
                        {"local_index": 3, "quality": 1, "value": 1200},
                        {"local_index": 7, "quality": 2, "value": 800},
                        {"local_index": 11, "quality": 3, "value": 4500},
                        {"local_index": 15, "quality": 3, "value": 3200},
                    ],
                }
            ],
        }
    )

    assert evidence.min_counts["q1"] == 2
    assert evidence.min_counts["q3"] == 2
    assert evidence.split_counts == {"white": 1, "green": 1}
    assert evidence.quality_value_floors["q1"] == 2000.0
    assert evidence.quality_value_floors["q3"] == 7700.0
    assert "coarse_quality_reveal_min_counts" in evidence.source_notes
    assert "coarse_quality_reveal_source:action_result_100134" in evidence.source_notes


def test_ref_engine_maria_skill_reveal_rows_use_maria_specific_notes() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="maria",
            structured_ref_inputs={"total_count": 30},
            public_rows=[
                {
                    "info_id": 200027,
                    "revealed_items_detail": [
                        {"local_index": 58, "quality": 5},
                        {"local_index": 90, "quality": 1},
                    ],
                }
            ],
        )
        | {
            "skill_reveal_rows": [
                {
                    "skill_id": 100108,
                    "hero_id": 108,
                    "tool": "玛丽亚·总价",
                    "result": 37063,
                    "observed_items": [],
                    "revealed_items_detail": [],
                },
                {
                    "skill_id": 10010801,
                    "hero_id": 108,
                    "tool": "玛丽亚·品质",
                    "result": None,
                    "observed_items": [
                        {"local_index": 90, "quality": 1},
                        {"local_index": 43, "quality": 2},
                        {"local_index": 36, "quality": 3},
                        {"local_index": 104, "quality": 1},
                    ],
                    "revealed_items_detail": [
                        {"local_index": 90, "quality": 1},
                        {"local_index": 43, "quality": 2},
                        {"local_index": 36, "quality": 3},
                        {"local_index": 104, "quality": 1},
                    ],
                },
            ],
        }
    )

    assert evidence.min_counts["q1"] >= 2
    assert evidence.min_counts.get("q3", 0) >= 1
    assert evidence.min_counts.get("q5", 0) >= 1
    assert evidence.split_counts.get("green", 0) >= 1
    assert evidence.split_counts == {"white": 2, "green": 1}
    # 100108「玛丽亚·总价」= 低品三档(白+绿+蓝)合计价值, 不再误映射成 q1 白品 value floor
    # (2026-07-10 修: 2407 回放坐实 result = 结算 q1+q2+q3 值之和; 旧映射把白品价值抬 ~47x)。
    assert "q1" not in evidence.quality_value_floors
    assert "maria_skill_q1_value_floor" not in evidence.source_notes
    # 高品合计件数推导(玛丽亚估价核心杠杆): total_count(30) - 低品(白2+绿1+蓝1=4) = 26 件紫金红合计
    # (各档仍未知)。不做这步高品(价值大头)全靠先验 → 严重欠估。
    assert evidence.count_sums.get("q4q5q6") == 26
    assert "maria_high_tier_count_sum_derived:26" in evidence.source_notes
    assert "maria_skill_coarse_quality_min_counts" in evidence.source_notes
    assert "maria_skill_coarse_quality_source:maria_skill_10010801" in evidence.source_notes
    assert "coarse_quality_reveal_source:public_info_200027" in evidence.source_notes
    assert "coarse_quality_reveal_source:maria_skill_10010801" not in evidence.source_notes


def test_ref_engine_raven_all_item_quality_public_info_sets_min_counts() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="raven",
            structured_ref_inputs={"total_count": 30},
            public_rows=[
                {
                    "info_id": 200030,
                    "revealed_items_detail": [
                        {"local_index": 1, "quality": 1},
                        {"local_index": 2, "quality": 3},
                        {"local_index": 3, "quality": 4},
                        {"local_index": 4, "quality": 5},
                        {"local_index": 5, "quality": 6},
                    ],
                }
            ],
        )
    )

    assert evidence.min_counts["q1"] == 1
    assert evidence.min_counts["q3"] == 1
    assert evidence.min_counts["q4"] == 1
    assert evidence.min_counts["q5"] == 1
    assert evidence.min_counts["q6"] == 1
    assert "coarse_quality_reveal_source:public_info_200030" in evidence.source_notes


def test_ref_engine_raven_all_item_quality_public_info_locks_missing_tiers_to_zero() -> None:
    """Full scan with total_count match must lock absent tiers (e.g. no red) to 0."""
    evidence = extract_evidence(
        _snapshot(
            hero="raven",
            structured_ref_inputs={"total_count": 4},
            public_rows=[
                {
                    "info_id": 200030,
                    "revealed_items_detail": [
                        {"local_index": 1, "quality": 1},
                        {"local_index": 2, "quality": 3},
                        {"local_index": 3, "quality": 4},
                        {"local_index": 4, "quality": 5},
                    ],
                }
            ],
        )
    )

    assert "all_item_quality_exact_counts" in evidence.source_notes
    assert "all_item_quality_zero_q6" in evidence.source_notes
    assert evidence.fixed_counts["q6"] == 0
    assert evidence.fixed_counts["q5"] == 1
    assert evidence.min_counts["q6"] == 0

    result = run_reference_engine(
        _snapshot(
            hero="raven",
            structured_ref_inputs={"total_count": 4},
            public_rows=[
                {
                    "info_id": 200030,
                    "revealed_items_detail": [
                        {"local_index": 1, "quality": 1},
                        {"local_index": 2, "quality": 3},
                        {"local_index": 3, "quality": 4},
                        {"local_index": 4, "quality": 5},
                    ],
                }
            ],
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "ok"
    assert tuple(result["red_count_range"]) == (0, 0, 0)
    assert tuple(result["red_value_range"]) == (0, 0, 0)


def test_ref_engine_raven_r5_skill_all_item_quality_locks_missing_red_to_zero() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="raven",
            structured_ref_inputs={},
        )
        | {
            "ui_contract": {
                "context": {
                    "hero": "raven",
                    "map_id": 2410,
                    "phase": "bidding",
                    "round": 5,
                },
                "constraints": {"public_info": {}},
            },
            "skill_reveals": [
                {
                    "skill_id": 100301,
                    "hero_id": 301,
                    "observed_items": [
                        {"local_index": 1, "quality": 1},
                        {"local_index": 2, "quality": 3},
                        {"local_index": 3, "quality": 4},
                        {"local_index": 4, "quality": 5},
                    ],
                }
            ],
        }
    )

    assert evidence.total_count == 4
    assert "raven_skill_100301_all_item_quality_count" in evidence.source_notes
    assert evidence.fixed_counts["q6"] == 0
    assert "all_item_quality_zero_q6" in evidence.source_notes


def test_ref_engine_raven_r5_skill_all_item_quality_locks_present_red_to_exact() -> None:
    evidence = extract_evidence(
        _snapshot(
            hero="raven",
            structured_ref_inputs={},
        )
        | {
            "ui_contract": {
                "context": {
                    "hero": "raven",
                    "map_id": 2404,
                    "phase": "bidding",
                    "round": 5,
                },
                "constraints": {"public_info": {}},
            },
            "skill_reveals": [
                {
                    "skill_id": 100301,
                    "hero_id": 301,
                    "observed_items": [
                        {"local_index": 1, "quality": 1},
                        {"local_index": 2, "quality": 3},
                        {"local_index": 3, "quality": 4},
                        {"local_index": 4, "quality": 5},
                        {"local_index": 5, "quality": 6},
                        {"local_index": 6, "quality": 6},
                        {"local_index": 7, "quality": 6},
                    ],
                }
            ],
        }
    )
    result = run_reference_engine(
        _snapshot(
            hero="raven",
            structured_ref_inputs={},
        )
        | {
            "ui_contract": {
                "context": {
                    "hero": "raven",
                    "map_id": 2404,
                    "phase": "bidding",
                    "round": 5,
                },
                "constraints": {"public_info": {}},
            },
            "skill_reveals": [
                {
                    "skill_id": 100301,
                    "hero_id": 301,
                    "observed_items": [
                        {"local_index": 1, "quality": 1},
                        {"local_index": 2, "quality": 3},
                        {"local_index": 3, "quality": 4},
                        {"local_index": 4, "quality": 5},
                        {"local_index": 5, "quality": 6},
                        {"local_index": 6, "quality": 6},
                        {"local_index": 7, "quality": 6},
                    ],
                }
            ],
        },
        max_combos=60_000,
    ).as_dict()

    assert evidence.fixed_counts["q6"] == 3
    assert "all_item_quality_exact_q6:3" in evidence.source_notes
    assert tuple(result["red_count_range"]) == (3, 3, 3)
    assert tuple(result["quality_count_ranges"]["q6"]) == (3, 3, 3)


def test_ref_engine_partial_coarse_reveal_does_not_zero_missing_tiers() -> None:
    """Random partial reveals must stay floor-only; do not lock absent tiers to zero."""
    evidence = extract_evidence(
        _snapshot(
            hero="raven",
            structured_ref_inputs={"total_count": 40},
            public_rows=[
                {
                    "info_id": 200028,
                    "revealed_items_detail": [
                        {"local_index": 1, "quality": 1},
                        {"local_index": 2, "quality": 3},
                    ],
                }
            ],
        )
    )

    assert "all_item_quality_exact_counts" not in evidence.source_notes
    assert evidence.fixed_counts.get("q6") is None


def test_ref_engine_skips_shaped_items_for_coarse_min_counts() -> None:
    evidence = extract_evidence(
        _snapshot(
            public_rows=[
                {
                    "info_id": 200001,
                    "revealed_items_detail": [
                        {"runtime_id": 1, "quality": 4, "shape_code": 23},
                    ],
                },
                {
                    "info_id": 200028,
                    "revealed_items_detail": [
                        {"local_index": 9, "quality": 4},
                    ],
                },
            ],
        )
    )

    assert evidence.fixed_counts.get("q4") == 1
    assert evidence.min_counts.get("q4") == 1
    assert "coarse_quality_reveal_source:public_info_200028" in evidence.source_notes
    assert "coarse_quality_reveal_source:public_info_200001" not in evidence.source_notes


def test_ref_engine_treasure_value_action_locks_q6_when_merged_quality_is_q5() -> None:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "sophie",
                "map_id": 2403,
                "phase": "bidding",
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {"total_count": 34},
        "public_info_rows": [],
        "skill_reveals": [
            {
                "skill_id": 1001073,
                "hero_id": 107,
                "observed_items": [
                    {
                        "local_index": 31,
                        "runtime_id": 1425860544908193,
                        "quality": 5,
                    }
                ],
            }
        ],
        "action_result_rows": [
            {
                "action_id": 100163,
                "tool": "至宝估价",
                "revealed_items_detail": [
                    {
                        "local_index": 30,
                        "runtime_id": 1425860544908193,
                        "value": 43650,
                    }
                ],
            }
        ],
    }

    evidence = extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q6") == 0
    assert evidence.min_counts.get("q6") == 0
    assert "public_max_quality_ceiling:5" in evidence.source_notes
    assert "public_max_quality_zero_q6" in evidence.source_notes


@pytest.fixture(scope="module")
def ahmed_2309_grid_only_count_prior_result() -> dict:
    return run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2309,
            structured_ref_inputs={},
            public_rows=[{"info_id": 200009, "value": 152}],
        ),
        max_combos=8_000,
    ).as_dict()


@pytest.mark.slow  # ~22-71s: grid_only count_prior over high total range (perf regression lane)
def test_ref_engine_runs_grid_only_total_count_prior_for_ahmed(
    ahmed_2309_grid_only_count_prior_result: dict,
) -> None:
    result = ahmed_2309_grid_only_count_prior_result

    assert result["status"] == "count_prior"
    assert "total_count_from_ref_count_prior" in result["notes"]
    assert "waiting_total_count" not in result["notes"]
    assert result["combo_count"] > 0
    assert result["balanced"] is not None


@pytest.mark.slow  # ~22-71s: grid_only count_prior over high total range (perf regression lane)
def test_ref_engine_runs_after_public_total_count_for_ahmed() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ahmed",
            map_id=2309,
            structured_ref_inputs={},
            public_rows=[
                {"info_id": 200009, "value": 152},
                {"info_id": 200017, "value": 48},
            ],
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert "waiting_total_count" not in result["notes"]


@pytest.mark.slow  # ~22-71s: grid_only count_prior over high total range (perf regression lane)
def test_ref_engine_runs_grid_only_total_count_prior_for_generic_ethan() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="ethan",
            map_id=2401,
            structured_ref_inputs={},
            public_rows=[{"info_id": 200009, "value": 95}],
        ),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert "total_count_from_ref_count_prior" in result["notes"]
    assert "waiting_total_count:grid_only" not in result["notes"]
    assert result["combo_count"] > 0


def test_ref_engine_runs_map_default_prior_without_live_input() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={},
        ),
        max_combos=8_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert "total_count_from_map_default_prior" in result["notes"]
    assert result["combo_count"] > 0
    assert result["balanced"] is not None


def test_ref_engine_keeps_aisha_grid_prior_when_quality_constraints_present() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2410,
            structured_ref_inputs={
                "total_cells": 70,
                "fixed_counts": {"q3": 13, "q4": 9, "q5": 3},
                "quality_cells": {"q3": 19, "q4": 29, "q5": 3},
                "split_counts": {"white": 2, "green": 6},
                "split_quality_cells": {"white": 3, "green": 8},
            },
        ),
        max_combos=8_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert "total_count_from_ref_count_prior" in result["notes"]
    assert "total_count_prior_center_from_grid_quality_residual" in result["notes"]
    center_notes = [n for n in result["notes"] if n.startswith("total_count_prior_center:")]
    assert center_notes, result["notes"]
    assert center_notes[0] != "total_count_prior_center:23"
    assert "waiting_total_count" not in result["notes"]


@pytest.mark.slow  # ~22-71s: grid_only count_prior over high total range (perf regression lane)
def test_ref_engine_grid_only_count_prior_uses_grid_third_not_residual(
    ahmed_2309_grid_only_count_prior_result: dict,
) -> None:
    result = ahmed_2309_grid_only_count_prior_result

    assert result["status"] == "count_prior"
    assert "total_count_prior_center_from_grid_quality_residual" not in result["notes"]
    assert "total_count_prior_center:51" in result["notes"]


def test_ref_engine_keeps_victor_min_count_prior_without_total_grid() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="victor",
            map_id=2101,
            structured_ref_inputs={"min_counts": {"q4": 1}},
        ),
        max_combos=500,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert "total_count_from_ref_count_prior" in result["notes"]
    assert "waiting_total_count" not in result["notes"]


# Uses a VALID sample (was a mixed one, but all mixed samples were quarantined 2026-06-20 as
# bad-truth; this fast-path regression only needs an exact-total + q5-avg-cells warehouse, which
# this valid sample provides). See memory fatbeans-mixed-samples-bad-truth.
SECTION_50_2_FATBEANS_SAMPLES = (
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_ahmed_2401_4rounds_2401_1388889360819628_0020.json",
)

# The 2403 sample only exposes white/green via a 3-of-42-item random public reveal
# (info_id 200026). Those coarse counts are floors, not an exact warehouse breakdown,
# so the engine must NOT lock q1 to the sampled sum (which previously forced a wrong
# exact total and inflated the bid). See _apply_low_quality_split_evidence.
SECTION_50_2_COARSE_SPLIT_FLOOR_SAMPLE = (
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_ahmed_2403_2rounds_2403_1388889391900123_0022.json"
)


def _ahmed_fatbeans_snapshot(sample_path: Path, *, round_count: int = 2) -> dict:
    from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture
    from bidking_lab.live.monitor import (
        _ahmad_ref_inputs_from_batches,
        _public_info_rows,
        _skill_reveal_rows,
    )

    events = parse_fatbeans_capture(sample_path)
    batches = [batch for batch in live_batches_from_fatbeans_events(events) if batch.phase != "settled"]
    prefix = batches[:round_count]
    return {
        "ui_contract": {
            "context": {"hero": "ahmed", "phase": "bidding"},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": _ahmad_ref_inputs_from_batches(prefix, hero="ahmed") or {},
        "public_info_rows": _public_info_rows(events, {}),
        "skill_reveals": _skill_reveal_rows(events, {}),
        "skill_reveal_rows": _skill_reveal_rows(events, {}),
        "action_result_rows": [],
    }


@pytest.mark.parametrize("sample_path", SECTION_50_2_FATBEANS_SAMPLES, ids=lambda path: path.name[:40])
def test_ref_engine_exact_total_q5_avg_cells_fast_path_preserves_outputs(
    sample_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This is a fast-path MECHANICS guard (the q5 avg-cells fast path must yield stable combos/value).
    # Pin it to the LEGACY value path (env=0) + explicit safety 0.85 so it stays decoupled from ahmed's
    # production truth-value regime (which relaxes safety to 0.92 -> balanced 426830 -> 461981); that
    # regime is verified separately (test_value_regime_ahmed + the ahmed value A/B), not here.
    monkeypatch.setenv("AHMAD_USE_DROP_TABLE_VALUE", "0")
    monkeypatch.setenv("BIDKING_DISABLE_KNOWN_QUALITY_RESIDUAL_QUOTE_TRANSFORM", "1")
    snapshot = _ahmed_fatbeans_snapshot(sample_path)
    # This test isolates the q5 fast-path mechanics. Ahmed skill-average persistence has its own
    # real-path regression gate and legitimately adds q3/q4 constraints to this capture.
    snapshot["skill_reveals"] = []
    snapshot["skill_reveal_rows"] = []
    result = run_reference_engine(snapshot, max_combos=50_000, safety_factor=0.85).as_dict()

    assert result["status"] == "ok"
    assert "exact_total_avg_cells_fast_path" in result["notes"]
    assert result["combo_count"] == 171
    assert result["balanced"] == 426_830


def test_ref_engine_coarse_public_split_reveal_does_not_lock_q1() -> None:
    """A 3-of-42-item random public reveal must not lock white/green (q1).

    Regression for the live bug where "未知别墅: 随机显示N件" sample counts were treated
    as the exact white/green total, badly under-counting low items and inflating bids.
    """
    snapshot = _ahmed_fatbeans_snapshot(SECTION_50_2_COARSE_SPLIT_FLOOR_SAMPLE)
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert "split_low_quality_q1_coarse_floor_only" in result["notes"]
    assert "split_low_quality_q1_count_merged" not in result["notes"]
    assert result["evidence"]["fixed_counts"].get("q1") is None
    # Sampled white+green sum is kept only as a floor, never an exact lock.
    assert result["evidence"]["min_counts"].get("q1") is not None


@pytest.mark.parametrize("sample_path", SECTION_50_2_FATBEANS_SAMPLES, ids=lambda path: path.name[:40])
def test_ref_engine_exact_total_q5_avg_cells_fast_path_warm_run_is_bounded(
    sample_path: Path,
) -> None:
    snapshot = _ahmed_fatbeans_snapshot(sample_path)
    run_reference_engine(snapshot, max_combos=50_000)
    started = time.perf_counter()
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert "exact_total_avg_cells_fast_path" in result["notes"]
    assert elapsed_ms < 500.0


AISHA_BATCH_B_BAND_SAMPLES = (
    (
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2501_5rounds_2501_1295018669960456_0139.json",
        140,
    ),
    (
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2401_5rounds_2401_1295018668661353_0045.json",
        113,
    ),
    (
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2401_3rounds_2401_1295019017806948_0032.json",
        121,
    ),
    (
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2403_3rounds_2403_1295019017648392_0051.json",
        111,
    ),
    (
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2510_3rounds_2510_1274128128479934_0209.json",
        100,
    ),
    (
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2401_3rounds_2401_1295018668515929_0030.json",
        99,
    ),
    (
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2504_5rounds_2504_1295018708289152_0166.json",
        96,
    ),
)


def _aisha_fatbeans_snapshot(sample_path: Path, *, round_count: int | None = None) -> dict:
    from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture
    from bidking_lab.live.monitor import (
        _ahmad_ref_inputs_from_batches,
        _public_info_rows,
        _skill_reveal_rows,
    )

    events = parse_fatbeans_capture(sample_path)
    batches = [batch for batch in live_batches_from_fatbeans_events(events) if batch.phase != "settled"]
    if round_count is None:
        round_count = max(3, len(batches) - 1)
    prefix = batches[:round_count]
    sort_id = max(int(batch.sequence or 0) for batch in prefix if batch.sequence is not None)
    prefix_events = events
    if sort_id:
        prefix_events = type(events)(
            packets=tuple(row for row in events.packets if int(row.sort_id) <= sort_id),
            frames=tuple(row for row in events.frames if int(row.sort_id) <= sort_id),
            sends=tuple(row for row in events.sends if int(row.sort_id) <= sort_id),
            states=tuple(row for row in events.states if int(row.sort_id) <= sort_id),
            statuses=tuple(row for row in events.statuses if int(row.sort_id) <= sort_id),
        )
    return {
        "ui_contract": {
            "context": {"hero": "aisha", "phase": "bidding"},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": _ahmad_ref_inputs_from_batches(prefix, hero="aisha") or {},
        "public_info_rows": _public_info_rows(prefix_events, {}),
        "skill_reveals": _skill_reveal_rows(prefix_events, {}),
        "skill_reveal_rows": _skill_reveal_rows(prefix_events, {}),
        "action_result_rows": [],
    }


def _aisha_fatbeans_settlement_total_value(sample_path: Path) -> int:
    from bidking_lab.live.fatbeans import parse_fatbeans_capture

    events = parse_fatbeans_capture(sample_path)
    items_path = ROOT / "data" / "processed" / "items.json"
    values_by_id: dict[int, int] = {}
    if items_path.exists():
        import json

        for row in json.loads(items_path.read_text(encoding="utf-8-sig")):
            values_by_id[int(row["item_id"])] = int(row.get("value") or 0)
    for state in reversed(events.states):
        if not state.inventory_items:
            continue
        return sum(
            int(values_by_id.get(int(item.item_id), 0))
            for item in state.inventory_items
        )
    raise AssertionError(f"no settlement inventory in {sample_path.name}")


# Penultimate replay baselines (2026-06-13). Truth is outside ±15% of balanced today;
# gate locks quote path and forbids >15% worsening of |truth - balanced|.
# (sample, baseline_balanced, baseline_combo_count). balanced re-based 2026-06-18 to the aisha
# dynamic middle tier (p50->p75 by cell-richness); the guard is now "the 3-tier band brackets the
# actual settlement value" (the user bids within the band), not balanced ~= raw truth -- the
# balanced tier is the long-tail-robust EXPECTED, so it intentionally sits above unlucky-low vaults.
# Baselines recalibrated 2026-06-19 (over-bid fix F1/F2/F3, workflow-validated). The band-bracketing
# contract (conservative <= truth <= aggressive, asserted below) holds for both:
# - 2501: balanced 329,321 -> 308,578, truth 227,108 -> over-bid REDUCED +102k -> +81k (improvement).
# - 2505: balanced 300,500 -> 268,856, truth 333,798 -> under-bid deepened -33k -> -65k. Accepted: 2505
#   is pure-prior red (invisible, no evidence); the F1 percentile clamp stops betting the CENTRAL tier
#   on unevidenced red (user-accepted hidden-red), and the aggressive tier (390k) still covers truth.
AISHA_GOOD_REGRESSION_SAMPLES = (
    # Re-baselined 2026-06-29 after commit 2b8907e flipped AISHA_ENABLE_NO_RED_DAMP default ON
    # (author go, verified -2% on 68 NEW samples). 2501: damp improved balanced toward truth
    # (|err| 81470 -> 19480), truth still in band -> clean re-baseline.
    (
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2501_4rounds_2501_1295018669612326_0123.json",
        246_588,
        233,
    ),
    # 2505: known damp tradeoff -- this vault shows no red at reveal but settles WITH a red,
    # exactly the case no-red-damp over-suppresses. damp ON drops aggressive 414k->287k and
    # truth (333798) falls OUT of band (the conservative/aggressive bracket assertion fails).
    # damp is the author-accepted default for the broader 68-sample -2% improvement; 2505 is
    # the accepted cost. xfail until damp strength/trigger is tuned (LEVER1, separate work).
    pytest.param(
        FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2505_5rounds_2505_1295018595274342_0173.json",
        186_970,
        15,
        marks=pytest.mark.xfail(
            reason="2b8907e no-red-damp flip: 2505 is a no-red-reveal-but-settles-red vault; "
            "damp over-suppresses aggressive, truth falls out of band. Accepted damp tradeoff.",
            strict=True,
        ),
    ),
)


@pytest.mark.parametrize(
    ("sample_path", "baseline_balanced", "baseline_combo_count"),
    AISHA_GOOD_REGRESSION_SAMPLES,
    ids=[
        # pytest.param exposes .values; plain tuples are their own iterable. Handle both.
        (sample.values if hasattr(sample, "values") else sample)[0].name[:36]
        for sample in AISHA_GOOD_REGRESSION_SAMPLES
    ],
)
def test_ref_engine_aisha_good_regression_balanced_bid_does_not_worsen(
    sample_path: Path,
    baseline_balanced: int,
    baseline_combo_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # These frozen baselines predate the known-quality K / residual U quote transform.
    # Keep this historical-regression gate on the explicit rollback arm; the new
    # default behavior and its posterior no-op contract are covered independently.
    monkeypatch.setenv("BIDKING_DISABLE_KNOWN_QUALITY_RESIDUAL_QUOTE_TRANSFORM", "1")
    result = run_reference_engine(
        _aisha_fatbeans_snapshot(sample_path),
        max_combos=50_000,
    ).as_dict()
    truth = _aisha_fatbeans_settlement_total_value(sample_path)

    assert result["status"] == "count_prior"
    assert result["combo_count"] == baseline_combo_count
    assert int(result["balanced"]) == baseline_balanced
    # New-design guard: the 3-tier band must bracket the actual settlement value, so the user always
    # has a safe tier to bid (conservative wins unlucky-low vaults, aggressive covers a hidden red).
    assert result["conservative"] <= truth <= result["aggressive"]


@pytest.mark.parametrize(
    ("sample_path", "settlement_cells"),
    AISHA_BATCH_B_BAND_SAMPLES,
    ids=[sample[0].name[:36] for sample in AISHA_BATCH_B_BAND_SAMPLES],
)
def test_ref_engine_aisha_batch_b_exact_total_grid_band_no_regress(
    sample_path: Path,
    settlement_cells: int,
) -> None:
    snapshot = _aisha_fatbeans_snapshot(sample_path)
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    low, mid, high = result["total_grid_range"]

    assert low <= settlement_cells <= high
    if settlement_cells == 140:
        assert "total_grid_target_from_known_high_tier_cells" in result["notes"]
        assert result["evidence"]["total_grid_target"] == settlement_cells


def test_ref_engine_avg_value_only_q5_count_derivation_is_unique() -> None:
    # 8,522.5 can only form an integer total at count=2 under a three-item vault ceiling. The exact
    # gold catalog then confirms 7,800 + 9,245 = 17,045 and a two-cell footprint.
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2404,
            structured_ref_inputs={"total_count": 3},
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 8_522.5,
                    }
                ]
            },
        ),
        max_combos=20_000,
    ).as_dict()

    assert result["evidence"]["fixed_counts"].get("q5") == 2
    assert result["evidence"]["quality_cells"].get("q5") == 2
    assert result["evidence"]["quality_values"].get("q5") == 17_045
    assert "q5_catalog_projection:count:2" in result["notes"]


def test_ref_engine_public_q5_avg_without_catalog_item_does_not_lock_one() -> None:
    from ahmad_ref_engine import EXACT_CATALOG_AVG_NO_ITEM_MATCH_NOTE

    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2408,
            structured_ref_inputs={"total_count": 28},
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 31560.0,
                    }
                ]
            },
        ),
        max_combos=20_000,
    ).as_dict()
    q5 = (result.get("quality_count_ranges") or {}).get("q5") or (None, None, None)
    notes = tuple(str(item) for item in (result.get("notes") or ()))
    assert result["evidence"]["fixed_counts"].get("q5") is None
    assert EXACT_CATALOG_AVG_NO_ITEM_MATCH_NOTE + ":q5" in notes
    assert "q5_exact_value_cells_multiset:avg_value_only_guard:0" in notes
    assert q5[0] is None or int(q5[0]) != 1


def test_ref_engine_public_q5_integer_avg_with_catalog_item_stays_ambiguous() -> None:
    result = run_reference_engine(
        _snapshot(
            hero="aisha",
            map_id=2408,
            structured_ref_inputs={"total_count": 3},
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q5_avg_value",
                        "kind": "avg_value",
                        "quality": 5,
                        "value": 54180.0,
                    }
                ]
            },
        ),
        max_combos=20_000,
    ).as_dict()
    assert result["evidence"]["fixed_counts"].get("q5") is None
    assert result["evidence"]["quality_cells"].get("q5") is None
    assert result["evidence"]["quality_values"].get("q5") is None


def test_ref_engine_total_grid_target_residual_uses_unfixed_avg_cells() -> None:
    # q5 carries an avg_cells so its footprint is pinned (count + avg), isolating the RESIDUAL
    # per-item mechanism under test. Without a q5 footprint signal it would be an unpinned tail and
    # the total grid would fall back to a soft floor (see the soft-floor regression test below).
    evidence = extract_evidence(
        _snapshot(
            hero="aisha",
            map_id=2501,
            structured_ref_inputs={
                "total_count": 20,
                "fixed_counts": {"q3": 5, "q4": 4, "q5": 2},
                "quality_cells": {"q3": 12, "q4": 10},
                "avg_cells": {"q1": 2.0, "q5": 3.0, "q6": 3.0},
            },
        )
    )

    assert evidence.total_grid_target == 44.0
    assert RESIDUAL_AVG_CELLS_NOTE in evidence.source_notes
    assert TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE in evidence.source_notes


def test_ref_engine_total_grid_target_excludes_known_cells_unfixed_count_from_residual() -> None:
    # Live 2406 R3: villa locks total_count=38; q1/q3 counts fixed; q1/q3/q4 cells known but q4's
    # COUNT is unfixed. q4's cells (26) are in known_cells_total AND its items were also in the
    # residual*per_item term -> purple counted twice -> grid inflated to 143 vs the 105 truth. The
    # fix removes the estimated items of known-cells/unfixed-count qualities from the residual.
    evidence = extract_evidence(
        _snapshot(
            hero="aisha",
            map_id=2406,
            structured_ref_inputs={
                "total_count": 38,
                "fixed_counts": {"q1": 10, "q3": 11},
                "quality_cells": {"q1": 18, "q3": 31, "q4": 26},
                "avg_cells": {"q1": 1.8, "q3": 2.8},
            },
        )
    )

    # known 75 + residual (38-21 fixed - round(26/2.4)=11 purple) = 6 items * 4.0 fallback = 99,
    # NOT the double-counted 75 + 17*4.0 = 143.
    assert evidence.total_grid_target == 99.0
    assert TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE in evidence.source_notes


def _tiny_gold_residual_inputs():
    # Live 2408 R3: villa total_count=35, q1/q3/q4 counts+cells fixed, plus a SMALL gold tier
    # (q5 cells=5, count unfixed) = 5 tiny 1-cell golds. round(5/3.2)=2 under-counts them, leaking
    # 3 phantom items into the residual at ~4.0 cells/item -> grid target inflated to 81 vs truth 66
    # (and the displayed 估总格 then locks to that single value).
    return {
        "total_count": 35,
        "counts": {"q1": 13, "q3": 7, "q4": 7},
        "quality_cells": {"q1": 19, "q3": 15, "q4": 18, "q5": 5},
        "avg_cells": {"q1": 1.46, "q3": 2.14, "q4": 2.57},
    }


def test_ref_engine_tiny_hightier_residual_not_over_inflated() -> None:
    # Counting one item per known cell for the small gold removes it from the residual -> 69,
    # bracketing the 66 truth, instead of the over-inflated 81.
    ev = extract_evidence(_snapshot(hero="aisha", map_id=2408, structured_ref_inputs=_tiny_gold_residual_inputs()))
    assert ev.total_grid_target == 69.0


def test_ref_engine_tiny_hightier_residual_rollback(monkeypatch) -> None:
    monkeypatch.setenv("AISHA_DISABLE_TINY_HIGHTIER_RESIDUAL", "1")
    ev = extract_evidence(_snapshot(hero="aisha", map_id=2408, structured_ref_inputs=_tiny_gold_residual_inputs()))
    assert ev.total_grid_target == 81.0


def test_ref_engine_aisha_layout_grid_hint_raises_target_from_deep_minimap() -> None:
    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 25},
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["audit_aisha_layout_mode"] = "target"
    snapshot["minimap_grid_items"] = [
        {"quality": 3, "row": 14, "width": 2, "height": 1, "cells": 8},
        {"quality": 4, "row": 12, "width": 3, "height": 2, "cells": 15},
    ]
    evidence = extract_evidence(snapshot)

    # 期望 = 引擎当前正确输出。值随 footroom 路径合法演进：
    # 63→38→61.44(f5647fa 对齐)→61.0(8856631 R3 dense cap + minimap dedupe)。
    # 工作树曾被误改为 70.0（无对应引擎 commit）→ 回退至引擎实际值 61.0。
    assert evidence.total_grid_target == 61.0
    assert AISHA_LAYOUT_GRID_HINT_NOTE in evidence.source_notes
    assert AISHA_LAYOUT_FOOTROOM_NOTE in evidence.source_notes
    assert AISHA_LAYOUT_FOOTROOM_SPARSE_NOTE in evidence.source_notes
    assert any(
        note.startswith(f"{AISHA_LAYOUT_FOOTROOM_MULT_NOTE}:")
        for note in evidence.source_notes
    )


def test_minimap_items_from_snapshot_dedupes_mirrored_contract_items() -> None:
    from ahmad_ref_engine import _minimap_items_from_snapshot
    from layout_depth_policy import _known_minimap_cells

    item = {"quality": 3, "row": 10, "width": 2, "height": 2, "cells": 4}
    snapshot = {
        "minimap_grid_items": [item],
        "ui_contract": {"minimap": {"items": [dict(item)]}},
    }
    items = _minimap_items_from_snapshot(snapshot)

    assert len(items) == 1
    assert _known_minimap_cells(items) == 4


def test_occupancy_adjusted_depth_grid_discounts_lone_deep_item() -> None:
    from ahmad_ref_engine import _occupancy_adjusted_depth_grid

    # A 3x3 item alone at rows 8-10 (col-isolated) with a dense cluster above only reaching row 6.
    # raw_deepest=10 -> rectangle 100, but the deep rows 7-10 hold only the lone tail. Re-anchor to
    # the supported depth (row 6) plus the lone tail's own cells, not the full rectangle.
    items = [
        {"quality": 1, "row": 1, "width": 1, "height": 1, "cells": 1},
        {"quality": 3, "row": 3, "width": 2, "height": 2, "cells": 4},
        {"quality": 4, "row": 5, "width": 2, "height": 2, "cells": 4},
        {"quality": 4, "row": 5, "width": 1, "height": 2, "cells": 2},  # 2nd item reaching row 6
        {"quality": 3, "row": 8, "width": 3, "height": 3, "cells": 9},  # lone deep tail
    ]
    adjusted = _occupancy_adjusted_depth_grid(items, 10)
    assert adjusted < 100  # discounted below the rectangle
    # supported depth row 6 -> 60, plus the lone tail's cells in rows 7-10 (rows 8-10 x width 3 = 9)
    assert adjusted == 69


def test_occupancy_adjusted_depth_grid_keeps_rectangle_when_depth_supported() -> None:
    from ahmad_ref_engine import _occupancy_adjusted_depth_grid

    # Two items both reach the deepest row 10 -> the depth is supported, no over-statement to remove.
    items = [
        {"quality": 4, "row": 8, "width": 2, "height": 3, "cells": 6},
        {"quality": 5, "row": 9, "width": 2, "height": 2, "cells": 4},
        {"quality": 3, "row": 2, "width": 2, "height": 2, "cells": 4},
    ]
    assert _occupancy_adjusted_depth_grid(items, 10) == 100


def test_ref_engine_aisha_layout_grid_hint_skips_when_hard_total_cells_present() -> None:
    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 25, "total_cells": 120},
    )
    snapshot["ui_contract"]["context"]["round"] = 5
    snapshot["minimap_grid_items"] = [
        {"quality": 5, "row": 16, "width": 3, "height": 2, "cells": 40},
        {"quality": 4, "row": 15, "width": 2, "height": 2, "cells": 30},
    ]
    evidence = extract_evidence(snapshot)

    assert evidence.total_grid_target == 120.0
    assert AISHA_LAYOUT_GRID_HINT_NOTE not in evidence.source_notes


def test_aisha_layout_target_looks_undershot_rejects_high_soft_target() -> None:
    from ahmad_ref_engine import _aisha_layout_target_looks_undershot

    assert not _aisha_layout_target_looks_undershot(
        total_grid_target=120.0,
        known_cells=70,
        rows_below=1,
        columns=10,
        round_no=5,
    )
    assert _aisha_layout_target_looks_undershot(
        total_grid_target=70.0,
        known_cells=70,
        rows_below=8,
        columns=10,
        round_no=3,
    )


def test_ref_engine_aisha_layout_band_mode_widens_high_bound_only() -> None:
    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 25},
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["audit_aisha_layout_mode"] = "band"
    snapshot["minimap_grid_items"] = [
        {"quality": 3, "row": 14, "width": 2, "height": 1, "cells": 8},
        {"quality": 4, "row": 12, "width": 3, "height": 2, "cells": 15},
    ]
    off = run_reference_engine({**snapshot, "audit_aisha_layout_mode": "off"}, max_combos=20_000).as_dict()
    band = run_reference_engine(snapshot, max_combos=20_000).as_dict()
    off_evidence = off.get("evidence") if isinstance(off.get("evidence"), dict) else {}
    band_evidence = band.get("evidence") if isinstance(band.get("evidence"), dict) else {}

    assert "aisha_layout_band_widen_applied" in band["notes"]
    assert band["total_grid_range"][2] >= off["total_grid_range"][2]


def test_ref_engine_aisha_layout_shadow_mode_keeps_target() -> None:
    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 25},
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["audit_aisha_layout_mode"] = "shadow"
    snapshot["minimap_grid_items"] = [
        {"quality": 3, "row": 14, "width": 2, "height": 1, "cells": 8},
        {"quality": 4, "row": 12, "width": 3, "height": 2, "cells": 15},
    ]
    off = run_reference_engine({**snapshot, "audit_aisha_layout_mode": "off"}, max_combos=20_000).as_dict()
    shadow = run_reference_engine(snapshot, max_combos=20_000).as_dict()

    assert shadow["evidence"]["total_grid_target"] == off["evidence"]["total_grid_target"]
    assert AISHA_LAYOUT_GRID_HINT_NOTE in shadow["notes"]
    assert "aisha_layout_application_mode:shadow" in shadow["notes"]
    assert off["total_grid_range"] == shadow["total_grid_range"]


def test_ref_engine_aisha_early_round_lightweight_caps_combos() -> None:
    from ahmad_ref_engine import (
        AISHA_EARLY_ROUND_LIGHTWEIGHT_NOTE,
        AISHA_EARLY_ROUND_MAX_COMBOS,
    )

    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={
            "total_count": 28,
            "fixed_counts": {"q1": 8},
            "quality_cells": {"q1": 16},
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 1
    snapshot["audit_aisha_early_round"] = True

    started = time.perf_counter()
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert elapsed_ms < 2000.0
    assert AISHA_EARLY_ROUND_LIGHTWEIGHT_NOTE in result["notes"]
    assert result["combo_count"] <= AISHA_EARLY_ROUND_MAX_COMBOS
    assert result["balanced"] not in (None, 0)


def test_aisha_early_round_count_window_expands_when_cap_inverts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2507-class live bug: cap-50 window still empty after band clamp → expand restores range."""
    from ahmad_ref_engine import (
        AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND,
        AISHA_EARLY_ROUND_COUNT_WINDOW_EXPANDED_NOTE,
        RefEvidence,
        _total_count_candidates,
    )

    evidence = RefEvidence(
        hero="aisha",
        map_id=2507,
        phase="bidding",
        total_count=None,
        fixed_counts={},
        min_counts={"q1": 1, "q3": 4, "q4": 3, "q5": 1},
        count_sums={},
        avg_cells={"q4": 3.5},
        quality_cells={"q4": 28.0},
        quality_cell_floors={},
        avg_values={},
        quality_values={},
        quality_value_floors={},
        quality_value_floor_item_counts={},
        split_counts={},
        split_quality_cells={},
        split_avg_cells={},
        random_value_floors=(),
        total_grid_target=154.0,
        soft_avg_cell_keys=frozenset(),
        soft_avg_value_keys=frozenset(),
        v3_conservative="",
        v3_balanced="",
        v3_aggressive="",
        source_notes=("coarse_quality_reveal_min_counts",),
    )

    monkeypatch.delenv(AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND, raising=False)
    notes: list[str] = []
    candidates, center = _total_count_candidates(
        evidence,
        notes,
        early_round_lightweight=True,
        avg_cells_count_hint=58,
        avg_cells_count_hint_public=False,
        round_no=1,
        probs=None,
        occ_cap=56.5,
    )
    assert candidates
    assert center == 58
    assert 58 in candidates
    assert any(
        str(note).startswith(AISHA_EARLY_ROUND_COUNT_WINDOW_EXPANDED_NOTE) for note in notes
    )

    monkeypatch.setenv(AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND, "1")
    rollback_notes: list[str] = []
    rollback_candidates, _ = _total_count_candidates(
        evidence,
        rollback_notes,
        early_round_lightweight=True,
        avg_cells_count_hint=58,
        avg_cells_count_hint_public=False,
        round_no=1,
        probs=None,
        occ_cap=56.5,
    )
    assert rollback_candidates == []


def test_aisha_early_round_real_fatbeans_blank_to_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real valid fatbeans R1: rollback = missing_total_count (live blank), fix = count_prior."""
    scripts = ROOT / "scripts"
    tools = ROOT / "hero_ref" / "tools"
    for path in (scripts, tools):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from audit_aisha_gap import (
        _build_snapshot,
        _events_through_sort,
        _map_id_from_events,
        _prefix_sort_id,
    )
    from ahmad_ref_engine import AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND
    from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture
    from hero_ref_live_schedule import EARLY_ROUND_ENGINE_FLAG, hero_max_combos_for_round

    sample_name = "fatbeans_valid_aisha_2502_4rounds_2502_1295018709694048_0149.json"
    path = FATBEANS_Q4_290_SAMPLE.parent / sample_name
    if not path.is_file():
        pytest.skip(f"missing real sample {sample_name}")

    events = parse_fatbeans_capture(path)
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    prefix = pre[:1]
    sort_id = _prefix_sort_id(prefix)
    prefix_events = _events_through_sort(events, sort_id) if sort_id else events
    snapshot = _build_snapshot(
        hero="aisha",
        events=prefix_events,
        prefix_batches=prefix,
        map_id=_map_id_from_events(events),
    )
    snapshot[EARLY_ROUND_ENGINE_FLAG] = True
    snapshot.setdefault("ui_contract", {}).setdefault("context", {})["round"] = 1
    budget = hero_max_combos_for_round("aisha", 1)

    monkeypatch.setenv(AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND, "1")
    rollback = run_reference_engine(
        prepare_reference_engine_snapshot(dict(snapshot)),
        max_combos=budget,
    ).as_dict()
    assert rollback["status"] == "missing_total_count"

    monkeypatch.delenv(AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND, raising=False)
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(dict(snapshot)),
        max_combos=budget,
    ).as_dict()
    assert fixed["status"] == "count_prior"
    assert int(fixed.get("combo_count") or 0) > 0
    assert int(fixed.get("balanced") or 0) > 0


def test_ref_engine_aisha_layout_grid_hint_skips_round_two_white_only() -> None:
    from layout_depth_policy import AISHA_COUNT_IMPLIED_GRID_FLOOR_NOTE

    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 25},
    )
    snapshot["ui_contract"]["context"]["round"] = 2
    snapshot["minimap_grid_items"] = [
        {"quality": 1, "row": 15, "width": 2, "height": 1, "cells": 6},
    ]
    evidence = extract_evidence(snapshot)

    assert evidence.total_grid_target == 71.0
    assert any(
        str(note).startswith(AISHA_COUNT_IMPLIED_GRID_FLOOR_NOTE)
        for note in evidence.source_notes
    )
    assert AISHA_LAYOUT_GRID_HINT_NOTE not in evidence.source_notes


def test_ref_engine_aisha_multi_deepest_live_2409_r2_band() -> None:
    from layout_depth_policy import AISHA_MULTI_DEEPEST_FOOTROOM_NOTE

    slice_path = (
        ROOT
        / "data"
        / "fixtures"
        / "aisha_eval_slices"
        / "slices"
        / "windivert_live_2026-06-14_011556_2409_1425860638836159_reset_r02.json"
    )
    if not slice_path.is_file():
        pytest.skip("2409 eval slice missing")

    payload = json.loads(slice_path.read_text(encoding="utf-8"))
    snapshot = payload["snapshot"]
    snapshot["round"] = payload.get("eval_round", 2)
    prepared = prepare_reference_engine_snapshot(snapshot)
    result = run_reference_engine(prepared, max_combos=20_000).as_dict()
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    grid_range = result.get("total_grid_range") or (None, None, None)
    notes = tuple(str(item) for item in (result.get("notes") or ()))

    assert any(str(note).startswith(AISHA_MULTI_DEEPEST_FOOTROOM_NOTE) for note in notes)
    assert grid_range[1] is not None
    assert grid_range[0] is not None and grid_range[2] is not None
    assert int(grid_range[0]) <= int(grid_range[1]) <= int(grid_range[2])
    assert int(grid_range[1]) >= 68


def test_minimap_local_index_decodes_baoguang_row9_col4() -> None:
    from layout_depth_policy import _item_bottom_row, _item_top_row

    item = {"quality": 4, "local_index": 83, "cells": 1}
    assert _item_top_row(item) == 9
    assert _item_bottom_row(item) == 9


def test_ref_engine_aisha_deep_nearfull_warehouse_2410_live_rounds() -> None:
    """2410 live recording: settlement total_cells=96 (near-full 10-row warehouse).

    The geometry-first grid pass must NOT leave the old severe under-estimate (mid ~67,
    gap -29). Later rounds lift toward the truth (r03/r04 mid ~96, on truth), and R1 commits to
    a conservative WIDE interval that brackets the deep truth instead of a tight band that misses
    it. (Previously this case was mislabeled "shallow" and asserted mid<=72.) Bands rebased after
    the count-anchor per_item calibration (2.6 cells/item on the grid->count path): the deep mid
    rounds now track the 96-cell truth much more tightly (r02 82, r03/r04 96) instead of the old
    under-band (r02 68, r03 76).
    """
    slice_dir = ROOT / "data" / "fixtures" / "aisha_eval_slices" / "slices"
    # filename, mid_min, mid_max, expect_brackets_truth, r1_wide
    cases = (
        ("windivert_live_2026-06-14_010830_2410_1425860638330407_reset_r01.json", 70, 80, True, True),
        ("windivert_live_2026-06-14_010830_2410_1425860638330407_reset_r02.json", 76, 88, False, False),
        ("windivert_live_2026-06-14_010830_2410_1425860638330407_reset_r03.json", 88, 100, True, False),
        ("windivert_live_2026-06-14_010830_2410_1425860638330407_reset_r04.json", 88, 100, True, False),
    )
    for filename, mid_min, mid_max, brackets_truth, r1_wide in cases:
        path = slice_dir / filename
        if not path.is_file():
            pytest.skip(f"missing {filename}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        truth = int(payload["truth"]["total_cells"])
        snapshot = payload["snapshot"]
        snapshot["round"] = payload.get("eval_round", snapshot.get("round"))
        prepared = prepare_reference_engine_snapshot(snapshot)
        result = run_reference_engine(prepared, max_combos=20_000).as_dict()
        grid = result.get("total_grid_range") or (None, None, None)
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        assert evidence.get("total_grid_target") is None, filename
        assert grid[0] is not None and grid[1] is not None and grid[2] is not None, filename
        assert int(grid[0]) <= int(grid[1]) <= int(grid[2]), filename
        assert mid_min <= int(grid[1]) <= mid_max, filename
        if brackets_truth:
            assert int(grid[0]) <= truth <= int(grid[2]), filename
        if r1_wide:
            assert int(grid[2]) - int(grid[0]) >= 25, filename


def _assert_whole_grid_number(value: Any) -> None:
    if value is None:
        return
    parsed = float(value)
    assert parsed == int(round(parsed)), f"expected whole grid cells, got {value!r}"


def test_ref_engine_grid_cell_outputs_are_whole_numbers() -> None:
    from layout_depth_policy import normalize_grid_cell_target

    assert normalize_grid_cell_target(61.44285714285714) == 61.0
    assert normalize_grid_cell_target(71.25) == 71.0
    assert normalize_grid_cell_target(None) is None

    layout_snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 25},
    )
    layout_snapshot["ui_contract"]["context"]["round"] = 3
    layout_snapshot["audit_aisha_layout_mode"] = "target"
    layout_snapshot["minimap_grid_items"] = [
        {"quality": 3, "row": 14, "width": 2, "height": 1, "cells": 8},
        {"quality": 4, "row": 12, "width": 3, "height": 2, "cells": 15},
    ]
    layout_evidence = extract_evidence(layout_snapshot)
    _assert_whole_grid_number(layout_evidence.total_grid_target)

    floor_snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 25},
    )
    floor_snapshot["ui_contract"]["context"]["round"] = 2
    floor_snapshot["minimap_grid_items"] = [
        {"quality": 1, "row": 15, "width": 2, "height": 1, "cells": 6},
    ]
    floor_evidence = extract_evidence(floor_snapshot)
    _assert_whole_grid_number(floor_evidence.total_grid_target)

    live_path = ROOT / "data" / "logs" / "live" / "round_snapshots" / "r02_bidding.json"
    if live_path.is_file():
        live_snapshot = prepare_reference_engine_snapshot(
            json.loads(live_path.read_text(encoding="utf-8"))
        )
        live_result = run_reference_engine(live_snapshot, max_combos=20_000).as_dict()
        live_evidence = live_result.get("evidence") if isinstance(live_result.get("evidence"), dict) else {}
        _assert_whole_grid_number(live_evidence.get("total_grid_target"))
        for value in live_result.get("total_grid_range") or ():
            _assert_whole_grid_number(value)


def test_ref_engine_aisha_layout_grid_hint_skips_white_only_through_round_three() -> None:
    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 25},
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["minimap_grid_items"] = [
        {"quality": 1, "row": 15, "width": 2, "height": 1, "cells": 6},
    ]
    evidence = extract_evidence(snapshot)

    assert evidence.total_grid_target is None
    assert AISHA_LAYOUT_GRID_HINT_NOTE not in evidence.source_notes


HIDDEN_2601_SLOW_SAMPLE = (
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2601_3rounds_2601_1295018740835056_0215.json"
)


def _aisha_fatbeans_snapshot_at_round(
    sample_path: Path,
    *,
    round_count: int | None = None,
    include_minimap: bool = False,
) -> dict:
    from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture
    from bidking_lab.live.monitor import (
        _action_round,
        _ahmad_ref_inputs_from_batches,
        _minimap_grid_items,
        _public_info_rows,
        _skill_reveal_rows,
        load_monitor_tables,
    )

    events = parse_fatbeans_capture(sample_path)
    batches = [batch for batch in live_batches_from_fatbeans_events(events) if batch.phase != "settled"]
    # round_count = how many pre-settlement BATCHES to include (a prefix length), NOT the game round.
    # Each game round emits 2-3 state frames (public-info/skill/tool/grid), so len(batches) ~2-3x rounds.
    if round_count is None:
        round_count = max(3, len(batches) - 1)
    prefix = batches[:round_count]
    sort_id = max(int(batch.sequence or 0) for batch in prefix if batch.sequence is not None)
    prefix_events = events
    if sort_id:
        prefix_events = type(events)(
            packets=tuple(row for row in events.packets if int(row.sort_id) <= sort_id),
            frames=tuple(row for row in events.frames if int(row.sort_id) <= sort_id),
            sends=tuple(row for row in events.sends if int(row.sort_id) <= sort_id),
            states=tuple(row for row in events.states if int(row.sort_id) <= sort_id),
            statuses=tuple(row for row in events.statuses if int(row.sort_id) <= sort_id),
        )
    map_id = None
    marker = sample_path.name.split("_")
    for part in marker:
        if part.isdigit() and len(part) == 4 and part.startswith(("21", "22", "23", "24", "25", "26", "45")):
            map_id = int(part)
            break
    # ROUND FIDELITY: the engine's round-gated logic reads context.round, which LIVE sets to action_round
    # (the game bidding round). Derive it from the prefix's game state — NOT round_count (a batch/frame
    # count). Fall back to round_count only if the capture carries no round_no.
    _ar = _action_round(prefix_events)
    _round = int(_ar) if _ar is not None else int(round_count)
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "phase": "bidding",
                "round": _round,
                "map_id": map_id,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": _ahmad_ref_inputs_from_batches(prefix, hero="aisha") or {},
        "public_info_rows": _public_info_rows(prefix_events, {}),
        "skill_reveals": _skill_reveal_rows(prefix_events, {}),
        "skill_reveal_rows": _skill_reveal_rows(prefix_events, {}),
        "action_result_rows": [],
        "map_id": map_id,
    }
    if include_minimap:
        tables = load_monitor_tables()
        snapshot["minimap_grid_items"] = _minimap_grid_items(prefix, tables.items)
    return snapshot


def test_ref_engine_pinned_quality_cells_sparse_prior_routes_fast_on_hidden_sample() -> None:
    if not HIDDEN_2601_SLOW_SAMPLE.exists():
        pytest.skip("hidden fatbeans sample missing")
    snapshot = _aisha_fatbeans_snapshot_at_round(HIDDEN_2601_SLOW_SAMPLE)
    started = time.perf_counter()
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert elapsed_ms < 2000.0
    assert result["status"] == "count_prior"
    assert PINNED_QUALITY_CELLS_SPARSE_PRIOR_NOTE in result["notes"]
    assert "sparse_exact_total_prior_enumeration" in result["notes"]
    assert result["combo_count"] < 500


def test_ref_engine_hidden_high_total_early_round_stays_under_perf_gate() -> None:
    if not HIDDEN_2601_SLOW_SAMPLE.exists():
        pytest.skip("hidden fatbeans sample missing")
    snapshot = _aisha_fatbeans_snapshot_at_round(HIDDEN_2601_SLOW_SAMPLE, round_count=3)
    started = time.perf_counter()
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert elapsed_ms < 2000.0
    assert result["status"] == "count_prior"
    assert "sparse_exact_high_total_tight_prior" in result["notes"]
    assert result["combo_count"] < 500


def _ahmed_2402_r2_exact_total_q1_false_lock_snapshot() -> dict[str, Any]:
    """Minimal repro: Ahmed exact total 51 + q1 wire avg 2.4 + tight prior falsely locked q1=15."""
    return {
        "ui_contract": {
            "context": {"hero": "ahmed", "map_id": 2402, "phase": "bidding", "round": 2},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 51,
            "counts": {"q3": 19},
            "avg_cells": {"q1": 2.4000000953674316, "q5": 1.8333333730697632},
        },
        "public_info_rows": [
            {
                "info_id": 200027,
                "value": 6,
                "revealed_items_detail": [
                    {"local_index": 127, "quality": 4},
                    {"local_index": 59, "quality": 4},
                    {"local_index": 123, "quality": 4},
                    {"local_index": 11, "quality": 4},
                    {"local_index": 31, "quality": 3},
                    {"local_index": 95, "quality": 2},
                ],
            }
        ],
        "inference_input_constraints": {
            "mode": "pre_settlement_trusted_totals",
            "total_item_count": {
                "value": 51,
                "source": "packet",
                "confidence": "exact",
            },
        },
    }


AHMED_2408_Q1_AVG_UNTIGHTEN_SAMPLE = (
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_ahmed_2408_4rounds_2408_1425860538372899_0169.json"
)

# Ahmed sparse-exact tight-prior vaults where q1 avg has multiple wire-feasible counts but the
# fix must not move the published band (regression guard).
AHMED_TIGHT_PRIOR_Q1_AVG_NOOP_SAMPLES = (
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_ahmed_2401_4rounds_2401_1388889360819628_0020.json",
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_ahmed_2402_2rounds_2402_1425860622376459_0073.json",
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_ahmed_2403_3rounds_2403_1425860592332903_0089.json",
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_ahmed_2404_2rounds_2404_1425860544241423_0103.json",
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_ahmed_2406_4rounds_2406_1425860624757837_0135.json",
)


def test_ref_engine_exact_total_q1_avg_untightens_tight_prior_residual_q1() -> None:
    result = run_reference_engine(
        _ahmed_2402_r2_exact_total_q1_false_lock_snapshot(),
        max_combos=50_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert "sparse_exact_high_total_tight_prior" in result["notes"]
    assert EXACT_TOTAL_Q1_AVG_DOMAIN_NOTE in result["notes"]
    assert result["quality_count_ranges"]["q1"][1] == 10
    assert result["red_count_range"][1] >= 2
    assert result["combo_count"] >= 5


def test_ref_engine_exact_total_q1_avg_domain_rollback_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AHMAD_DISABLE_EXACT_TOTAL_Q1_AVG_DOMAIN", "1")
    # The BUG-G high-tier false-lock widen (2026-07-11) independently frees this same composition —
    # with only THIS feature rolled back the engine now lands q1 [10,10,15] instead of the historical
    # [15,15,15] defect. Disable that lever too so this rollback gate keeps measuring ONE feature.
    monkeypatch.setenv("BIDKING_DISABLE_AVG_MULTIPLE_FALSE_LOCK_WIDEN", "1")
    result = run_reference_engine(
        _ahmed_2402_r2_exact_total_q1_false_lock_snapshot(),
        max_combos=50_000,
    ).as_dict()

    assert EXACT_TOTAL_Q1_AVG_DOMAIN_NOTE not in result["notes"]
    assert result["quality_count_ranges"]["q1"] == [15, 15, 15]
    assert result["red_count_range"][1] == 1
    assert result["combo_count"] == 2


def test_ref_engine_ahmed_2408_fatbeans_q1_avg_untighten_lower_tail() -> None:
    if not AHMED_2408_Q1_AVG_UNTIGHTEN_SAMPLE.exists():
        pytest.skip("sample missing")
    snapshot = _ahmed_fatbeans_snapshot(AHMED_2408_Q1_AVG_UNTIGHTEN_SAMPLE, round_count=4)
    # Keep this a q1-domain test: retained q3/q4/q5 Ahmed skill averages intentionally bypass the
    # sparse route, which is independently covered by the skill-average persistence tests.
    snapshot["skill_reveals"] = []
    snapshot["skill_reveal_rows"] = []
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert EXACT_TOTAL_Q1_AVG_DOMAIN_NOTE in result["notes"]
    assert result["quality_count_ranges"]["q1"][0] == 10


@pytest.mark.parametrize(
    "sample_path",
    AHMED_TIGHT_PRIOR_Q1_AVG_NOOP_SAMPLES,
    ids=lambda path: path.name[:36],
)
def test_ref_engine_exact_total_q1_avg_domain_does_not_move_pinned_tight_prior_samples(
    sample_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not sample_path.exists():
        pytest.skip("sample missing")
    snapshot = _ahmed_fatbeans_snapshot(sample_path)
    baseline = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    if "sparse_exact_high_total_tight_prior" not in baseline["notes"]:
        pytest.skip("sample does not hit tight prior")
    monkeypatch.setenv("AHMAD_DISABLE_EXACT_TOTAL_Q1_AVG_DOMAIN", "1")
    rolled_back = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    assert rolled_back["quality_count_ranges"] == baseline["quality_count_ranges"]
    assert rolled_back["balanced"] == baseline["balanced"]


AISHA_0052_SAMPLE = (
    FATBEANS_SAMPLE_DIR / "fatbeans_valid_aisha_2402_3rounds_2402_1367586310602652_0052.json"
)


def test_ref_engine_aisha_0052_fatbeans_r3_live_bridge_regression() -> None:
    """Batch B #3: white/green split bridge + public q4 avg must stay reachable at r3."""
    snapshot = _aisha_fatbeans_snapshot(AISHA_0052_SAMPLE, round_count=3)
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    evidence = result["evidence"]

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 2421
    # Re-baselined 2026-06-29 (commit 2b8907e no-red-damp default ON): damp's safety factor 0.9
    # re-centers the count window, shifting q3 mid 8->9 and balanced 310949->285120. Truth (341687)
    # stays inside the conservative/aggressive band -- baseline drift, not a coverage regression.
    assert result["balanced"] == 285_120
    assert evidence["split_counts"] == {"green": 7, "white": 4}
    assert evidence["split_quality_cells"] == {"green": 10.0, "white": 8.0}
    assert evidence["fixed_counts"].get("q1") == 11
    assert "split_low_quality_q1_count_merged" in result["notes"]
    assert result["quality_count_ranges"]["q3"] == [6, 9, 11]
    assert result["quality_count_ranges"]["q4"] == [3, 6, 9]


def test_prepare_reference_engine_snapshot_sets_aisha_live_band() -> None:
    from ahmad_ref_engine import (
        AISHA_LIVE_D1_MODE,
        AISHA_LIVE_LAYOUT_MODE,
        prepare_reference_engine_snapshot,
    )

    aisha = prepare_reference_engine_snapshot(_snapshot(hero="aisha", map_id=2501))
    assert aisha["audit_aisha_layout_mode"] == AISHA_LIVE_LAYOUT_MODE
    assert aisha["audit_aisha_d1_mode"] == AISHA_LIVE_D1_MODE
    ahmed = prepare_reference_engine_snapshot(_snapshot(hero="ahmed", map_id=2401))
    assert "audit_aisha_layout_mode" not in ahmed
    assert "audit_aisha_d1_mode" not in ahmed
    explicit = prepare_reference_engine_snapshot(
        {**_snapshot(hero="aisha", map_id=2501), "audit_aisha_layout_mode": "off"}
    )
    assert explicit["audit_aisha_layout_mode"] == "off"
    assert explicit["audit_aisha_d1_mode"] == AISHA_LIVE_D1_MODE
    raven = prepare_reference_engine_snapshot(_snapshot(hero="raven", map_id=2309))
    assert raven["audit_aisha_layout_mode"] == AISHA_LIVE_LAYOUT_MODE
    assert "audit_aisha_d1_mode" not in raven


def test_ref_engine_aisha_d1_shadow_note_does_not_change_balanced() -> None:
    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 28},
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    off = run_reference_engine({**snapshot, "audit_aisha_d1_mode": "off"}, max_combos=20_000).as_dict()
    shadow = run_reference_engine({**snapshot, "audit_aisha_d1_mode": "shadow"}, max_combos=20_000).as_dict()
    assert off["balanced"] == shadow["balanced"]
    assert any(str(note).startswith("aisha_d1_shadow_q6_discount") for note in shadow["notes"])


def test_ref_engine_aisha_d1_apply_can_lower_balanced_when_red_tail_wide() -> None:
    snapshot = _snapshot(
        hero="aisha",
        map_id=2501,
        structured_ref_inputs={"total_count": 32, "min_counts": {"q6": 0}},
    )
    snapshot["ui_contract"]["context"]["round"] = 2
    off = run_reference_engine({**snapshot, "audit_aisha_d1_mode": "off"}, max_combos=30_000).as_dict()
    apply = run_reference_engine({**snapshot, "audit_aisha_d1_mode": "apply"}, max_combos=30_000).as_dict()
    if off["balanced"] is None or apply["balanced"] is None:
        pytest.skip("no reachable combo for synthetic D1 apply probe")
    assert apply["balanced"] <= off["balanced"]
    assert any(str(note).startswith("aisha_d1_apply_q6_discount") for note in apply["notes"])


def test_ref_engine_aisha_pure_prior_red_value_cap_lowers_balanced(monkeypatch) -> None:
    """Pure-prior red (no q6 evidence) caps the red value tail in live, lowering the
    quote, without touching grid/counts. Reversible via env flag."""
    snapshot = _snapshot(hero="aisha", map_id=2501, structured_ref_inputs={"total_count": 28})
    snapshot["ui_contract"]["context"]["round"] = 1

    monkeypatch.setenv("BIDKING_DISABLE_AISHA_RED_PRIOR_CAP", "1")
    uncapped = run_reference_engine(snapshot, max_combos=20_000).as_dict()
    monkeypatch.delenv("BIDKING_DISABLE_AISHA_RED_PRIOR_CAP", raising=False)
    capped = run_reference_engine(snapshot, max_combos=20_000).as_dict()

    assert uncapped["balanced"] is not None and capped["balanced"] is not None
    assert capped["balanced"] < uncapped["balanced"]
    assert any(str(n).startswith("aisha_red_prior_value_cap") for n in capped["notes"])
    assert not any(str(n).startswith("aisha_red_prior_value_cap") for n in uncapped["notes"])
    # value-only: grid and per-quality counts are identical with/without the cap
    assert capped["total_grid_range"] == uncapped["total_grid_range"]
    assert capped["quality_count_ranges"] == uncapped["quality_count_ranges"]


def test_ref_engine_aisha_layout_modes_no_effect_on_ahmed() -> None:
    snapshot = _snapshot(
        hero="ahmed",
        map_id=2401,
        structured_ref_inputs={"total_count": 18, "fixed_counts": {"q3": 4, "q4": 3}},
    )
    off = run_reference_engine({**snapshot, "audit_aisha_layout_mode": "off"}, max_combos=20_000).as_dict()
    band = run_reference_engine({**snapshot, "audit_aisha_layout_mode": "band"}, max_combos=20_000).as_dict()
    target = run_reference_engine({**snapshot, "audit_aisha_layout_mode": "target"}, max_combos=20_000).as_dict()

    for key in ("status", "balanced", "conservative", "aggressive", "total_grid_range", "combo_count"):
        assert off[key] == band[key] == target[key]
    assert not any("aisha_layout" in str(note) for note in off.get("notes", ()))


def test_ref_engine_raven_sparse_layout_early_band_notes() -> None:
    snapshot = _snapshot(
        hero="raven",
        map_id=2309,
        structured_ref_inputs={"total_count": 25},
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["audit_aisha_layout_mode"] = "band"
    snapshot["minimap_grid_items"] = [
        {"quality": 3, "row": 14, "width": 2, "height": 1, "cells": 8},
        {"quality": 4, "row": 12, "width": 3, "height": 2, "cells": 15},
    ]
    evidence = extract_evidence(snapshot)

    assert "layout_sparse_profile" in evidence.source_notes
    assert "layout_sparse_early_viewport_hint" in evidence.source_notes
    assert any(
        str(note).startswith("layout_sparse_band_widen_delta:")
        for note in evidence.source_notes
    )
    assert AISHA_LAYOUT_GRID_HINT_NOTE not in evidence.source_notes


def test_ref_engine_raven_sparse_layout_band_widens_high_bound_only() -> None:
    snapshot = _snapshot(
        hero="raven",
        map_id=2309,
        structured_ref_inputs={"total_count": 25},
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    snapshot["minimap_grid_items"] = [
        {"quality": 3, "row": 14, "width": 2, "height": 1, "cells": 8},
        {"quality": 4, "row": 12, "width": 3, "height": 2, "cells": 15},
    ]
    off = run_reference_engine({**snapshot, "audit_aisha_layout_mode": "off"}, max_combos=20_000).as_dict()
    band = run_reference_engine({**snapshot, "audit_aisha_layout_mode": "band"}, max_combos=20_000).as_dict()

    assert off["total_grid_range"][2] is not None
    assert band["total_grid_range"][2] >= off["total_grid_range"][2]
    assert "aisha_layout_band_widen_applied" in band["notes"]
    assert "layout_sparse_profile" in band["notes"]


def test_ref_engine_tiered_quote_safety_factors() -> None:
    from layout_depth_policy import REF_QUOTE_SAFETY_TIER_NOTE, quote_safety_multipliers

    # Generic tier->percentile mapping uses a non-aisha hero on a NON-tier105 map: aisha uses a
    # dynamic balanced (p50->p75 by cell-richness) + p90 aggressive, and ahmed on tier105 ships now
    # strips its conservative tier (pure-prior red floor, see test_ref_engine_ahmed_tier105_cons_red_strip);
    # every other (hero, tier) keeps the plain conservative=p25 / balanced=p50 / aggressive=p75 mapping.
    snapshot = _snapshot(
        hero="ahmed",
        map_id=2401,
        structured_ref_inputs={"total_count": 28},
    )
    snapshot["ui_contract"]["context"]["round"] = 3
    result = run_reference_engine(snapshot, max_combos=20_000, safety_factor=0.85).as_dict()
    assert result["status"] in {"ok", "count_prior"}
    conservative_mul, balanced_mul, aggressive_mul = quote_safety_multipliers(0.85)
    # ±1 tolerance: engine applies the multiplier to the full-precision float p25/p50/p75 then rounds,
    # the expected here rounds the already-rounded value_p25/p50/p75 — they can differ by one unit.
    assert abs(result["conservative"] - int(round((result["value_p25"] or 0) * conservative_mul))) <= 1
    assert abs(result["balanced"] - int(round((result["value_p50"] or 0) * balanced_mul))) <= 1
    assert abs(result["aggressive"] - int(round((result["value_p75"] or 0) * aggressive_mul))) <= 1
    assert REF_QUOTE_SAFETY_TIER_NOTE in result["notes"]


def test_ref_engine_ahmed_tier105_cons_red_strip(monkeypatch) -> None:
    """Ahmed tier105-ship conservative pure-prior red strip — DEFAULT OFF (opt-in via env).
    The 2026-06-21 workflow review found the strip over-suppresses 保守 on real (already under-estimated)
    high-red ships at the decision round, so it is OFF by default; the code is kept as an opt-in switch
    for the early-round regime. This test pins BOTH: default = no strip, and ENABLE=1 = strip fires,
    conservative-tier-only, monotonic, tier-gated."""
    from layout_depth_policy import quote_safety_multipliers

    conservative_mul, _bal_mul, aggressive_mul = quote_safety_multipliers(0.85)
    snapshot = _snapshot(hero="ahmed", map_id=2501, structured_ref_inputs={"total_count": 28})
    snapshot["ui_contract"]["context"]["round"] = 3

    # DEFAULT (no enable env): strip does NOT fire — conservative is the plain p25*mul
    monkeypatch.delenv("AHMED_ENABLE_CONS_PURE_PRIOR_RED_STRIP", raising=False)
    off = run_reference_engine(snapshot, max_combos=20_000, safety_factor=0.85).as_dict()
    assert not any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in off["notes"])
    assert abs(off["conservative"] - int(round((off["value_p25"] or 0) * conservative_mul))) <= 1

    # OPT-IN (ENABLE=1): strip fires, pushes conservative BELOW p25*mul, clamped to balanced
    monkeypatch.setenv("AHMED_ENABLE_CONS_PURE_PRIOR_RED_STRIP", "1")
    on = run_reference_engine(snapshot, max_combos=20_000, safety_factor=0.85).as_dict()
    assert any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in on["notes"])
    assert on["conservative"] <= min(
        int(round((on["value_p25"] or 0) * conservative_mul)), on["balanced"]
    )
    assert on["conservative"] < off["conservative"]
    assert on["conservative"] <= on["balanced"] <= on["aggressive"]
    # 参考/激进 keep the plain percentile mapping — the strip is conservative-tier-ONLY
    assert on["balanced"] == off["balanced"] and on["aggressive"] == off["aggressive"]

    # gated by tier: with ENABLE=1, the SAME pure-prior ahmed snapshot on a non-tier105 map does NOT strip
    villa = _snapshot(hero="ahmed", map_id=2401, structured_ref_inputs={"total_count": 28})
    villa["ui_contract"]["context"]["round"] = 3
    villa_res = run_reference_engine(villa, max_combos=20_000, safety_factor=0.85).as_dict()
    assert not any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in villa_res["notes"])


def test_ref_engine_aisha_dynamic_balanced_tier() -> None:
    """Aisha-only: balanced tier slides up by observed cell-richness (reds invisible, but total_cells
    tracks red richness), aggressive=p90 to bracket a hidden red. This synthetic snapshot has NO red
    evidence -> pure-prior red, so the 2026-06-19 over-bid fix (F1) clamps the central percentile to
    the LO floor (don't bet the central on unevidenced red); the balanced then falls below p25*mul and
    the monotonic display clamp pulls conservative DOWN to balanced. Other heroes unaffected
    (test_ref_engine_tiered_quote_safety_factors)."""
    from layout_depth_policy import quote_safety_multipliers

    snapshot = _snapshot(hero="aisha", map_id=2501, structured_ref_inputs={"total_count": 28})
    snapshot["ui_contract"]["context"]["round"] = 3
    result = run_reference_engine(snapshot, max_combos=20_000, safety_factor=0.85).as_dict()
    conservative_mul, _balanced_mul, aggressive_mul = quote_safety_multipliers(0.85)
    # 2026-06-20: pure-prior red ALSO strips the unseen-red component from the CONSERVATIVE tier
    # (保守 = safe floor that assumes the red it can't see isn't there), so conservative is now
    # (p25 - rv50*(1-keep))*mul, i.e. <= the old p25*mul, still monotonically clamped to balanced.
    # balanced/aggressive are unchanged by the strip. See AISHA_CONS_PURE_PRIOR_RED_KEEP.
    assert any(str(n).startswith("aisha_cons_pure_prior_red_strip") for n in result["notes"])
    assert result["conservative"] <= min(
        int(round((result["value_p25"] or 0) * conservative_mul)), result["balanced"]
    )
    assert result["aggressive"] >= int(round((result["value_p75"] or 0) * aggressive_mul))
    # balanced is the dynamic middle: between the conservative and aggressive tiers, and the note fires
    assert any(str(n).startswith("aisha_dynamic_balanced_pct") for n in result["notes"])
    assert result["conservative"] <= result["balanced"] <= result["aggressive"]


def test_ref_engine_public_q4_avg_cells_soft_until_count_locked() -> None:
    """Live 2401 R3: public q4 avg cells stay useful as soft prior before purple count lock."""
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 2401, "phase": "bidding", "round": 3},
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "total_cells", "value": 93},
                        {"semantic": "q4_avg_cells", "value": 2.642857074737549},
                    ]
                }
            },
        },
        "structured_ref_inputs": {
            "fixed_counts": {"q3": 16, "q1": 12},
            "min_counts": {"q3": 16, "q1": 12, "q4": 1, "q6": 1},
            "quality_cells": {"q3": 20.0, "q1": 25.0},
            "avg_cells": {
                "q4": 2.642857074737549,
                "q1": 2.0833333333333335,
                "q3": 1.25,
            },
        },
    }
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert "public_q4_avg_cells_soft_pending_count" in result["notes"]
    assert "public_quality_avg_cells_conflict_fallback" not in result["notes"]
    assert result["balanced"] not in (None, 0)


def test_ref_engine_public_q4_avg_cells_wire_precision_prunes_non_integer_counts() -> None:
    """Live 2407 R1: public q4 avg cells = 10/3 (wire float 3.3333332). Total purple cells =
    count * 10/3 must be an integer, so the purple count must be a multiple of 3. Counts like
    4/7/10 are mathematically impossible and must not appear in the (still-soft) count range.
    Regression for the live 'why is 4 purple an option?' bug."""
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 2407, "phase": "reading", "round": 1},
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "q4_avg_cells", "value": 3.3333332538604736},
                    ]
                }
            },
        },
        "structured_ref_inputs": {
            "total_count": 40,
            "min_counts": {"q4": 1},
            "avg_cells": {"q4": 3.3333332538604736},
        },
    }
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert "public_q4_avg_cells" in result["notes"]
    q4 = result["quality_count_ranges"].get("q4") or (None, None, None)
    for bound in q4:
        if bound is not None:
            assert int(bound) % 3 == 0, f"q4 count {bound} impossible for avg 10/3 (must be multiple of 3)"


def test_ref_engine_draw_hero_total_count_center_boost_scoped() -> None:
    """Draw-heroes never receive a total-count reveal or minimap quality signal, so their
    total-count prior systematically under-estimates. We boost the prior CENTER for an EXPLICIT
    allowlist (sophie/gabriela/raven/wuqilin) only. This MUST be scoped: signalled heroes (aisha)
    are NOT boosted, and isabella is deliberately EXCLUDED (boosting over-shoots its low truth).
    Regression for the 'don't mis-affect non-draw-heroes' constraint."""

    def _prior_center(hero: str) -> tuple[bool, int | None]:
        snapshot = {
            "ui_contract": {"context": {"hero": hero, "map_id": 2401, "phase": "bidding", "round": 2}},
            "structured_ref_inputs": {"min_counts": {"q3": 1}},
        }
        result = run_reference_engine(
            prepare_reference_engine_snapshot(snapshot), max_combos=8000
        ).as_dict()
        notes = result.get("notes") or []
        boosted = any(str(n).startswith("draw_hero_count_center_boost") for n in notes)
        center = next(
            (int(str(n).split(":", 1)[1]) for n in notes if str(n).startswith("total_count_prior_center:")),
            None,
        )
        return boosted, center

    boosted = {h: _prior_center(h) for h in ("sophie", "wuqilin", "aisha", "isabella")}

    # draw-hero allowlist is boosted
    assert boosted["sophie"][0] is True, "draw-hero (sophie) must be boosted"
    assert boosted["wuqilin"][0] is True, "draw-hero (wuqilin) must be boosted"
    # signalled + excluded heroes are NOT boosted (scope)
    assert boosted["aisha"][0] is False, "signalled hero (aisha) must NOT be boosted"
    assert boosted["isabella"][0] is False, "isabella is deliberately excluded (over-shoots)"
    # boosted center exceeds the un-boosted map default
    assert boosted["sophie"][1] is not None and boosted["aisha"][1] is not None
    assert boosted["sophie"][1] > boosted["aisha"][1], (
        f"draw-hero center {boosted['sophie'][1]} should exceed map default {boosted['aisha'][1]}"
    )


def test_ref_engine_draw_hero_value_scale_scoped(monkeypatch) -> None:
    """Draw-heroes over-value once their real path (1.45x count boost) is exercised, so a
    scoped per-hero multiplier re-centres the value percentiles that feed all three bid tiers.
    MUST be scoped: sophie is scaled, aisha/ahmed are untouched (1.0). Env-overridable."""
    from ahmad_ref_engine import _hero_value_scale

    # table + normalization
    assert _hero_value_scale("sophie") == 0.72
    assert _hero_value_scale("Sophie") == 0.72  # case-insensitive normalize
    assert _hero_value_scale("aisha") == 1.0
    assert _hero_value_scale("ahmed") == 1.0
    assert _hero_value_scale(None) == 1.0

    # env override for calibration A/B
    monkeypatch.setenv("HERO_VALUE_SCALE_OVERRIDE", "sophie=1.0,gabriela=0.88")
    assert _hero_value_scale("sophie") == 1.0
    assert _hero_value_scale("gabriela") == 0.88
    assert _hero_value_scale("aisha") == 1.0
    monkeypatch.delenv("HERO_VALUE_SCALE_OVERRIDE")

    # the scale fires in the engine for sophie and is absent for a control hero
    def _notes(hero: str) -> list[str]:
        snapshot = {
            "ui_contract": {"context": {"hero": hero, "map_id": 2401, "phase": "bidding", "round": 3}},
            "structured_ref_inputs": {"min_counts": {"q3": 1}},
        }
        result = run_reference_engine(
            prepare_reference_engine_snapshot(snapshot), max_combos=8000
        ).as_dict()
        return [str(n) for n in (result.get("notes") or [])]

    assert any(n.startswith("hero_value_scale:") for n in _notes("sophie")), (
        "sophie must carry the value-scale note"
    )
    assert not any(n.startswith("hero_value_scale:") for n in _notes("aisha")), (
        "aisha (1.0) must NOT be value-scaled"
    )


def _run_q6_item_signal(actions: list[dict], hero: str = "ahmed") -> dict:
    snapshot = {
        "ui_contract": {
            "context": {"hero": hero, "map_id": 2401, "phase": "bidding", "round": 3},
            "actions": {"results": actions},
        },
        "action_result_rows": actions,
        "structured_ref_inputs": {"min_counts": {"q3": 1}},
    }
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=12_000
    ).as_dict()


def test_ref_engine_q6_rare_item_signals_constrain_output() -> None:
    """珍品存量/珍品均格/珍品估价/红总格 (rare item-given q6 signals: actions 100120/100114/100126/
    100108) must bind q6 count/avg-cells/value/cells and constrain the output, for ahmed and any
    hero (action extraction is hero-agnostic). The sample lib has ~no games using these high-tier
    items, so this guards the otherwise-unexercised wiring."""
    # 珍品存量 (red count) locks q6 count
    r = _run_q6_item_signal([{"action_id": "100120", "result": 2}])
    assert r["quality_count_ranges"].get("q6") == [2, 2, 2], r["quality_count_ranges"].get("q6")
    assert r["evidence"]["fixed_counts"].get("q6") == 2

    # 珍品存量 + 珍品均格 (red avg cells) -> q6 cells = count * avg
    r = _run_q6_item_signal(
        [{"action_id": "100120", "result": 2}, {"action_id": "100114", "result": 3.0}]
    )
    assert r["quality_cells_ranges"].get("q6") == [6, 6, 6], r["quality_cells_ranges"].get("q6")

    # 珍品存量 + 珍品估价 (red value sum) -> red value pinned
    r = _run_q6_item_signal(
        [{"action_id": "100120", "result": 2}, {"action_id": "100126", "result": 500_000}]
    )
    assert r["red_value_range"] == [500_000, 500_000, 500_000], r["red_value_range"]

    # 红总格 (red total cells) binds q6 cells
    r = _run_q6_item_signal([{"action_id": "100108", "result": 30}])
    assert r["evidence"]["quality_cells"].get("q6") == 30.0

    # hero-agnostic: same lock for a draw-hero context
    r_aisha = _run_q6_item_signal([{"action_id": "100120", "result": 2}], hero="aisha")
    assert r_aisha["quality_count_ranges"].get("q6") == [2, 2, 2]


def _run_random_avg_snapshot(avg_value: float, sample_count: int = 9) -> dict:
    snapshot = {
        "ui_contract": {
            "context": {"hero": "ahmed", "map_id": 2401, "phase": "bidding", "round": 2},
            "constraints": {
                "public_info": {
                    "public_random_avg_values": [
                        {
                            "info_id": 200033,
                            "semantic": f"random_{sample_count}_avg_value",
                            "kind": "random_avg_value",
                            "sample_count": sample_count,
                            "value": avg_value,
                        }
                    ]
                }
            },
        },
        "structured_ref_inputs": {"total_count": 30},
    }
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=20_000
    ).as_dict()


def _confidence_for(hero: str, *, total_count: int | None = None) -> dict:
    sri: dict = {"min_counts": {"q3": 2}}
    if total_count is not None:
        sri["total_count"] = total_count
    snapshot = {
        "ui_contract": {"context": {"hero": hero, "map_id": 2401, "phase": "bidding", "round": 3}},
        "structured_ref_inputs": sri,
    }
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=6000
    ).as_dict()


def test_ref_engine_total_count_confidence_by_hero_class() -> None:
    """P2: draw-hero total-count error is irreducible per-sample (range width / reveal completeness
    do not predict it), so confidence is a hero-class label + empirical 1-sigma band. Draw-heroes
    are 'low' with a wide band; signalled heroes are higher with a tighter band; a locked total is
    'high' with a ~point band."""
    sophie = _confidence_for("sophie")
    ahmed = _confidence_for("ahmed")
    locked = _confidence_for("ahmed", total_count=30)

    assert sophie["total_count_confidence"] == "low"
    assert ahmed["total_count_confidence"] == "high"
    assert locked["total_count_confidence"] == "high"

    def width(r: dict) -> int:
        b = r["total_count_error_band"]
        return b[1] - b[0]

    # draw-hero band must be materially wider than the signalled hero's (inherent variance)
    assert width(sophie) > width(ahmed) + 5, (sophie["total_count_error_band"], ahmed["total_count_error_band"])
    # a locked total collapses the band to ~a point
    assert width(locked) <= 2
    # band brackets the central estimate
    est = sophie["total_count_estimate"]
    lo, hi = sophie["total_count_error_band"]
    assert lo <= est <= hi


def test_ref_engine_random_avg_high_signal_binds_floor_to_red_without_hugging() -> None:
    """A high random-N avg (random 9-item avg = 120k >= 100k threshold) is, by Monte-Carlo,
    caused by a red carrying ~97-99% of the draw value. We bind the implied floor (N*avg = 1.08M)
    to RED value (>=1 red worth ~0.95x the draw total) rather than flat-clamping the total. The
    estimate must therefore (a) anchor a red, (b) clear the hard floor, and (c) NOT hug it -- the
    value distribution keeps spread because the count prior adds the warehouse's other reds."""
    result = _run_random_avg_snapshot(120_000.0, sample_count=9)
    notes = result.get("notes") or []
    floor = 9 * 120_000

    # (a) red value anchor fired + q6 forced present
    assert any(str(n).startswith("random_avg_red_anchor_q6_value_floor") for n in notes), notes
    red_lo = (result.get("red_count_range") or (None,))[0]
    assert red_lo is not None and int(red_lo) >= 1, f"red count floor must be >=1, got {red_lo}"

    # (b) clears the hard total floor (N*avg <= truth, proven safe)
    p25, p50, p75 = result.get("value_p25"), result.get("value_p50"), result.get("value_p75")
    assert p50 is not None and p50 >= floor, f"p50={p50} must clear hard floor {floor}"

    # (c) NOT hugging: the percentiles keep spread (a flat clamp would collapse them to the floor)
    assert p75 > p25, f"value distribution must keep spread, got p25={p25} p75={p75}"
    assert not (p25 == p50 == p75 == floor), "must not hug the flat floor"

    # the high value manifests as an expensive red, not a flat total
    red_value_hi = (result.get("red_value_range") or (None, None, None))[2]
    assert red_value_hi is not None and red_value_hi >= 0.8 * floor, red_value_hi


def test_ref_engine_random_avg_low_signal_no_red_anchor() -> None:
    """Below the 100k red-anchor threshold (random 9-item avg = 50k), the red value anchor does
    NOT fire (baseline red presence is only ~83%, and the draw is not red-driven)."""
    result = _run_random_avg_snapshot(50_000.0, sample_count=9)
    notes = result.get("notes") or []
    assert not any(str(n).startswith("random_avg_red_anchor_q6_value_floor") for n in notes), notes


def test_ref_engine_public_q5_avg_cells_soft_until_count_locked() -> None:
    """Public gold avg cells stay soft prior before q5 count is locked (same as q4)."""
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 2401, "phase": "bidding", "round": 4},
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "total_cells", "value": 93},
                        {"semantic": "q5_avg_cells", "value": 3.25},
                    ]
                }
            },
        },
        "structured_ref_inputs": {
            "fixed_counts": {"q3": 16, "q1": 12, "q4": 14},
            "min_counts": {"q3": 16, "q1": 12, "q4": 14, "q6": 1},
            "quality_cells": {"q3": 20.0, "q1": 25.0, "q4": 37.0},
            "avg_cells": {
                "q5": 3.25,
                "q1": 2.0833333333333335,
                "q3": 1.25,
                "q4": 2.642857,
            },
        },
    }
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert result["evidence"]["soft_avg_cell_keys"] == ["q5"]
    assert "public_q5_avg_cells_soft_pending_count" in result["notes"]
    assert "public_quality_avg_cells_conflict_fallback" not in result["notes"]
    assert result["balanced"] not in (None, 0)


def _reduced_4548_positive_avg_value_conflict_snapshot(
    quality: str = "q4",
    *,
    value: float | None = None,
    fixed_count: int | None = None,
) -> dict[str, Any]:
    quality_number = int(quality[1])
    avg_value = (
        value
        if value is not None
        else {"q4": 6768.6875, "q5": 34288.75, "q6": 6768.6875}[quality]
    )
    fixed_counts = {"q3": 17}
    if fixed_count is not None:
        fixed_counts[quality] = fixed_count
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4548,
                "phase": "bidding",
                "round": 2,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "semantic": f"{quality}_avg_value",
                            "kind": "avg_value",
                            "quality": quality_number,
                            "value": avg_value,
                        },
                    ],
                },
            },
        },
        "structured_ref_inputs": {
            "total_count": 59,
            "fixed_counts": fixed_counts,
            "avg_cells": {
                "q1": 1.7000000476837158,
                "q5": 3.090909004211426,
            },
        },
    }


@pytest.mark.parametrize("quality", ["q4", "q5", "q6"])
def test_positive_public_quality_avg_value_conflict_preserves_presence(
    quality: str,
) -> None:
    result = run_reference_engine(
        _reduced_4548_positive_avg_value_conflict_snapshot(quality),
        max_combos=50_000,
    ).as_dict()

    count_range = result["quality_count_ranges"][quality]
    cells_range = result["quality_cells_ranges"][quality]
    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert count_range[0] >= 1
    assert cells_range[0] >= 1
    assert f"public_{quality}_avg_value_presence_fallback" in result["notes"]
    assert f"public_{quality}_avg_value_downgraded" in result["notes"]


def test_positive_public_q4_avg_value_never_returns_exact_zero_lock() -> None:
    result = run_reference_engine(
        _reduced_4548_positive_avg_value_conflict_snapshot(),
        max_combos=50_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q4"][0] >= 1
    assert result["quality_cells_ranges"]["q4"][0] >= 1
    assert result["quality_lock"]["q4"] != {
        "strength": "exact",
        "reason": "derived_single_solution",
    }


def test_zero_public_q4_avg_value_still_locks_q4_zero() -> None:
    result = run_reference_engine(
        _reduced_4548_positive_avg_value_conflict_snapshot(value=0.0),
        max_combos=50_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q4"] == [0, 0, 0]
    assert result["quality_cells_ranges"]["q4"] == [0, 0, 0]
    assert "public_q4_avg_value_presence_fallback" not in result["notes"]


def test_absent_public_q4_avg_value_keeps_sparse_exact_zero_baseline() -> None:
    snapshot = _reduced_4548_positive_avg_value_conflict_snapshot()
    snapshot["ui_contract"]["constraints"]["public_info"]["public_numeric_facts"] = []
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["quality_count_ranges"]["q4"] == [0, 0, 0]
    assert result["quality_cells_ranges"]["q4"] == [0, 0, 0]
    assert "public_q4_avg_value_presence_fallback" not in result["notes"]


def test_positive_public_q4_avg_value_keeps_existing_positive_hard_count() -> None:
    result = run_reference_engine(
        _reduced_4548_positive_avg_value_conflict_snapshot(fixed_count=8),
        max_combos=50_000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert result["quality_lock"]["q4"] == {
        "strength": "exact",
        "reason": "exact_lock",
    }


def test_positive_public_q4_avg_value_conflicting_with_hard_zero_fails_closed() -> None:
    result = run_reference_engine(
        _reduced_4548_positive_avg_value_conflict_snapshot(fixed_count=0),
        max_combos=50_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert result["quality_count_ranges"] == {}


def test_ref_engine_public_q4_avg_value_soft_until_count_locked() -> None:
    """Public purple avg value stays soft prior before q4 count lock (symmetric to avg_cells)."""
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 2402, "phase": "bidding", "round": 3},
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "total_cells", "value": 93},
                        {"semantic": "q4_avg_value", "value": 6659.21435546875},
                    ]
                }
            },
        },
        "structured_ref_inputs": {
            "total_count": 40,
            "fixed_counts": {"q3": 16, "q1": 8},
            "min_counts": {"q3": 16, "q1": 8, "q4": 1, "q6": 1},
            "quality_cells": {"q3": 20.0, "q1": 16.0},
            "avg_values": {"q4": 6659.214},
        },
    }
    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert result["evidence"]["soft_avg_value_keys"] == ["q4"]
    assert "public_q4_avg_value_soft_pending_count" in result["notes"]
    assert "public_quality_avg_value_conflict_fallback" not in result["notes"]
    assert "public_avg_value_soft_weight_v0" in result["notes"]
    assert result["balanced"] not in (None, 0)
    assert result["quality_count_ranges"]["q4"] == [14, 14, 14]


def test_ref_engine_public_q5_avg_value_soft_prefers_matching_count() -> None:
    snapshot = {
        "ui_contract": {
            "context": {"hero": "ahmed", "map_id": 4406, "phase": "bidding", "round": 4},
            "constraints": {
                "public_info": {
                    "public_avg_values": [
                        {
                            "semantic": "q5_avg_value",
                            "kind": "avg_value",
                            "quality": 5,
                            "value": 34288.75,
                        }
                    ]
                }
            },
        },
        "structured_ref_inputs": {
            "total_count": 30,
            "fixed_counts": {"q1": 10, "q3": 8},
            "min_counts": {"q1": 10, "q3": 8, "q5": 1},
        },
    }
    result = run_reference_engine(snapshot, max_combos=60_000).as_dict()

    assert result["status"] == "ok"
    assert result["combo_count"] > 0
    assert "public_q5_avg_value_soft_pending_count" in result["notes"]
    assert "public_avg_value_soft_weight_v0" in result["notes"]
    assert result["evidence"]["soft_avg_value_keys"] == ["q5"]


def test_ref_engine_soft_q5_avg_value_excludes_non_integer_product_counts() -> None:
    """Display avg 33895.5 (=203373/6) without gold total: odd counts 1/3/5 are invalid."""
    snapshot = _snapshot(
        hero="aisha",
        map_id=2406,
        structured_ref_inputs={"total_count": 34},
        public_info={
            "public_avg_values": [
                {
                    "semantic": "q5_avg_value",
                    "kind": "avg_value",
                    "quality": 5,
                    "value": 33895.5,
                }
            ]
        },
    )
    snapshot["ui_contract"]["context"]["round"] = 1
    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=20_000).as_dict()

    assert evidence.soft_avg_value_keys == frozenset({"q5"})
    assert evidence.min_counts.get("q5") == 2
    assert "public_q5_avg_value_integer_product_min:2" in evidence.source_notes
    q5_low, q5_mid, q5_high = result["quality_count_ranges"]["q5"]
    for invalid in (1, 3, 5):
        assert invalid not in (q5_low, q5_mid, q5_high)
    assert q5_low % 2 == 0
    assert result["combo_count"] > 0


def test_ref_engine_prop_100114_q6_avg_cells_stays_hard_without_public_soft() -> None:
    """Red avg cells arrive via prop 100114 (珍品均格), not public_info q6_avg_cells."""
    snapshot = {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 2401, "phase": "bidding", "round": 4},
            "actions": {
                "results": [{"action_id": 100114, "result": 4.5, "tool": "珍品均格"}],
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [{"semantic": "total_cells", "value": 93}],
                }
            },
        },
        "structured_ref_inputs": {
            "fixed_counts": {"q3": 16, "q1": 12, "q4": 14, "q5": 2},
            "min_counts": {"q3": 16, "q1": 12, "q4": 14, "q5": 2},
            "quality_cells": {"q3": 20.0, "q1": 25.0, "q4": 37.0, "q5": 8.0},
            "avg_cells": {"q6": 4.5},
        },
    }
    evidence = extract_evidence(snapshot)

    assert evidence.avg_cells.get("q6") == 4.5
    assert evidence.soft_avg_cell_keys == frozenset()
    assert "action_100114_q6_avg_cells" in evidence.source_notes
    assert "public_q6_avg_cells" not in evidence.source_notes


def test_fixed_counts_cap_soft_count_center_no_reachable_combo() -> None:
    # Regression: when the visible qualities are all fixed (hard public-info / skill / scan
    # reveals), the total is pinned near Σfixed + a small red tail. A soft grid/avg count hint
    # that over-states (footroom-inflated grid) must NOT push the candidate window above that
    # fixed-implied band, or the enumerator finds zero combos and returns no_reachable_combo
    # (observed live: 2405/2501 decision rounds). See _fixed_count_reachable_total_band.
    import ahmad_ref_engine as _eng

    snapshot = {
        "ui_contract": {"context": {"hero": "aisha", "map_id": 2405, "phase": "bidding", "round": 3}},
        # q1+q3+q4+q5 fixed = 42; only red (q6) is free, so the total is ~42 + small red.
        "structured_ref_inputs": {"fixed_counts": {"q1": 12, "q3": 13, "q4": 11, "q5": 6}},
    }
    evidence = extract_evidence(snapshot)
    assert dict(evidence.fixed_counts) == {"q1": 12, "q3": 13, "q4": 11, "q5": 6}
    assert evidence.total_count is None  # count is unlocked -> prior-enumeration path

    static_data = _eng.load_reference_static_data()
    probs, _ = _eng._quality_probabilities(evidence.map_id, static_data)

    # An inflated soft hint (55) would otherwise center the window at 55 -> [51, 59].
    capped, center = _eng._total_count_candidates(
        evidence, [], round_no=3, avg_cells_count_hint=55, probs=probs
    )
    # Window is re-anchored to the fixed-implied reachable band; it includes a feasible total
    # (fixed_sum=42 .. ~48) and never sits entirely above it.
    assert capped, "window must not be empty"
    assert min(capped) <= 42  # the fixed sum is reachable (red=0)
    assert max(capped) <= 50  # not pushed up to the inflated soft window [51, 59]
    assert 43 in capped  # the true total (42 + red 1) is enumerable
    assert center <= max(capped)

    # Without the fixed-count band (probs=None disables it), the bug reproduces: the soft hint
    # centers the window at 55 and excludes every feasible total -> no_reachable_combo.
    buggy, _ = _eng._total_count_candidates(
        evidence, [], round_no=3, avg_cells_count_hint=55, probs=None
    )
    assert min(buggy) > 48  # entirely above the fixed-implied band
    assert 43 not in buggy


def test_total_count_prior_window_includes_large_ship_map_centers() -> None:
    import ahmad_ref_engine as _eng

    evidence = extract_evidence(
        {
            "hero": "ahmed",
            "phase": "bidding",
            "map_id": 2502,
            "round": 1,
            "inference_input_constraints": {
                "warehouse_total_cells": {"value": 168, "confidence": "exact"},
            },
            "structured_ref_inputs": {
                "hero": "ahmed",
                "field_updates": [
                    {
                        "path": ["session", "warehouse_total_cells"],
                        "value": 168,
                        "source": "packet",
                        "confidence": "exact",
                    },
                ],
            },
        }
    )
    notes: list[str] = []
    candidates, center = _eng._total_count_candidates(evidence, notes, round_no=1)
    assert center is not None and center >= 50
    assert candidates
    assert max(candidates) >= 55
    assert min(candidates) <= center


def test_extract_evidence_uses_skill_reveal_hero_id_when_context_hero_unknown() -> None:
    evidence = extract_evidence(
        {
            "hero": "?",
            "phase": "bidding",
            "map_id": 2502,
            "round": 1,
            "skill_reveal_rows": [
                {"skill_id": 100204, "hero_id": 204, "result": 55},
            ],
            "structured_ref_inputs": {
                "hero": "ahmed",
                "field_updates": [
                    {
                        "path": ["session", "total_item_count"],
                        "value": 55,
                        "source": "packet",
                        "confidence": "exact",
                    }
                ],
            },
        }
    )
    assert evidence.hero == "ahmed"
    assert evidence.total_count == 55
    assert "skill_reveal_hero_id" in evidence.source_notes or "structured_hero" in evidence.source_notes


def test_high_tier_grid_target_unpinned_tail_soft_floor_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live 2410 R4: gold count is locked by a count-only scan (极品存量/200019) but its CELLS are
    # unknown — an unpinned tail (gold's 7 items have no known footprint). The high-tier total cells:
    #   - DEFAULT (soft floor, ON): do NOT hard-pin; emit a `total_grid_floor:<N>` soft lower bound at
    #     the physical floor (88 known + 8 unaccounted items, each ≥1 cell = 96) and let minimap/
    #     footroom estimate a range above it. The hard note must be absent.
    #   - ROLLBACK (AISHA_DISABLE_SOFT_GRID_FLOOR_UNPINNED=1): hard-pin at the physical floor 96 (never
    #     below — the pre-soft-floor regression: the old `known + residual` floor let it sink to 92,
    #     below the minimum, making every combo infeasible → no_reachable_combo).
    import ahmad_ref_engine as _eng

    kwargs = dict(
        total_count=46,
        total_grid_target=None,
        fixed_counts={"q1": 12, "q3": 13, "q4": 13, "q5": 7},  # gold q5 count fixed, cells unknown
        quality_cells={"q1": 24.0, "q3": 31.0, "q4": 33.0},  # gold/red cells unknown
        avg_cells={"q1": 2.0, "q3": 2.38, "q4": 2.54},
    )

    soft_notes: list[str] = []
    monkeypatch.delenv("AISHA_DISABLE_SOFT_GRID_FLOOR_UNPINNED", raising=False)
    soft_target = _eng._apply_total_grid_target_from_known_high_tier_cells(source_notes=soft_notes, **kwargs)
    assert soft_target is None  # incoming target passed through — not hard-pinned
    assert _eng.TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE not in soft_notes
    floor_notes = [n for n in soft_notes if n.startswith(_eng.TOTAL_GRID_FLOOR_NOTE_PREFIX)]
    assert floor_notes, "expected a soft total_grid_floor note"
    assert int(floor_notes[0].split(":")[1]) == 96  # physical floor, not the buggy 92

    hard_notes: list[str] = []
    monkeypatch.setenv("AISHA_DISABLE_SOFT_GRID_FLOOR_UNPINNED", "1")
    hard_target = _eng._apply_total_grid_target_from_known_high_tier_cells(source_notes=hard_notes, **kwargs)
    assert hard_target is not None and hard_target >= 96  # hard pin respects the physical floor
    assert _eng.TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE in hard_notes


def test_sparse_early_soft_target_not_hard_unblocks_deep_draw_vault_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live sophie 2403 (1-round draw game): public purple=12 and the deepest VISIBLE row is 13 (a
    # baoguang-revealed gold), so the vault is large (anchor ~124, settled 137). But total_count is
    # unknown (draw heroes sample gradually), so the sparse-early profile sets a soft target CAP
    # (known_cells + cap ≈ 29). That cap used to flow into the grid-feasibility gate as a HARD
    # equality: no count combo's grid can equal ~29 when row 13 implies ~124 -> _grids_for_counts
    # rejects every combo -> no_reachable_combo -> the overlay blanked/under-estimated (reported
    # 31/55/79; by eye ≈ 110). The fix exempts the sparse soft target from the hard gate so the
    # visible-depth anchor owns the range. Gated by the anchor's flag for a single clean rollback.
    snapshot = {
        "ui_contract": {"context": {"hero": "sophie", "map_id": 2403, "phase": "bidding", "round": 4}},
        "structured_ref_inputs": {"fixed_counts": {"q4": 12}},  # public purple; total_count unknown
        "minimap_grid_items": [  # deepest visible row 13 + a few shallow cells for fill
            {"quality": 5, "row": 13, "width": 1, "height": 1, "cells": 1},
            {"quality": 4, "row": 6, "width": 1, "height": 1, "cells": 1},
            {"quality": 2, "row": 4, "width": 1, "height": 1, "cells": 1},
        ],
    }
    evidence = extract_evidence(snapshot)
    assert evidence.total_count is None
    assert any(
        str(note).startswith("layout_sparse_early_soft_target:")
        for note in evidence.source_notes
    ), "sparse-early cap is still computed (just no longer enforced as a hard grid total)"

    monkeypatch.delenv("DISABLE_DRAW_VISIBLE_ANCHOR", raising=False)
    default = run_reference_engine(snapshot, max_combos=8000).as_dict()
    assert default["status"] == "count_prior"  # was no_reachable_combo before the fix
    mid = default["total_grid_range"][1]
    assert mid is not None and mid >= 90  # tracks the row-13 anchor (~124), not the ~29 cap
    assert any(str(note).startswith("draw_visible_depth_anchor:") for note in default["notes"])

    # Rollback to the true pre-feature behavior (blank deep vault) needs BOTH layered draw-hero
    # features off: the visible-depth anchor AND the count occupancy cap (default ON since 2026-06-23,
    # which independently caps the count low enough to fit the ~29 soft target -> count_prior). Each
    # has its own rollback flag; disabling only one leaves the other unblocking the vault.
    monkeypatch.setenv("DISABLE_DRAW_VISIBLE_ANCHOR", "1")
    monkeypatch.setenv("DRAW_HERO_DISABLE_OCC_CAP", "1")
    rolled_back = run_reference_engine(snapshot, max_combos=8000).as_dict()
    assert rolled_back["status"] == "no_reachable_combo"
