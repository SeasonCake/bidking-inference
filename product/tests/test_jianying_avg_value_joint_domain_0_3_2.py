from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "hero_ref" / "src", ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_ref_engine as engine  # noqa: E402
import bidking_lab.inference.v2 as v2_module  # noqa: E402
from bidking_lab.inference.q6_full_avg_evidence import (  # noqa: E402
    integer_total_bounds_from_q6_full_average,
    q6_full_average_value_evidence_from_public_rows,
)


def _snapshot(
    *,
    map_id: int = 9901,
    total_count: int | None = 3,
    avg_value: float | None = None,
    total_value: int | None = None,
    avg_cells: float | None = None,
    minimap_items: list[dict] | None = None,
    physical_candidate_domains: list[dict] | None = None,
) -> dict:
    structured: dict = {}
    if total_count is not None:
        structured["total_count"] = total_count
    if total_value is not None:
        structured["quality_values"] = {"q6": total_value}
    if avg_cells is not None:
        structured["avg_cells"] = {"q6": avg_cells}
    if minimap_items is not None:
        structured["minimap_items"] = minimap_items
    if physical_candidate_domains is not None:
        structured["physical_candidate_domains"] = physical_candidate_domains
    constraints: dict = {}
    if avg_value is not None:
        raw = struct.pack("<f", float(avg_value))
        parsed = struct.unpack("<f", raw)[0]
        public_avg_row = {
            "info_id": 200038,
            "semantic": "q6_avg_value",
            "kind": "avg_value",
            "quality": 6,
            "value": parsed,
            "value_field": 9,
            "value_wire_type": "float32",
            "value_raw_hex": raw.hex(),
            "value_endian": "little",
            "value_conflicts": [],
            "session_id": f"{map_id}:joint",
        }
        constraints["public_info"] = {
            "public_numeric_facts": [public_avg_row],
            "public_avg_values": [public_avg_row],
        }
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": map_id,
                "phase": "bidding",
                "session_id": f"{map_id}:joint",
            },
            "constraints": constraints,
        },
        "structured_ref_inputs": structured,
        "session_id": f"{map_id}:joint",
    }
    if minimap_items is not None:
        snapshot["minimap_grid_items"] = minimap_items
    if physical_candidate_domains is not None:
        snapshot["physical_candidate_domains"] = physical_candidate_domains
    return snapshot


def _install_q6_catalog(
    monkeypatch: pytest.MonkeyPatch,
    rows: tuple[tuple[int, int, int], ...],
    *,
    map_id: int = 9901,
) -> None:
    pairs = tuple(sorted({(value, cells) for _item_id, value, cells in rows}))
    original = engine._validated_map_quality_catalog

    def catalog(requested_map: int, quality: int):
        if requested_map == map_id and quality == 6:
            return pairs, rows, "ok", "physical-category-v1:joint-test"
        return original(requested_map, quality)

    monkeypatch.setattr(engine, "_validated_map_quality_catalog", catalog)


def test_public_q6_avg_equal_catalog_price_is_joint_support_not_count_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(monkeypatch, ((1, 100, 1),))
    snapshot = _snapshot(avg_value=100.0)

    evidence = engine.extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q6") is None
    assert evidence.quality_values.get("q6") is None
    assert evidence.quality_count_cells_options["q6"] == (
        (1, 1),
        (2, 2),
        (3, 3),
    )
    assert evidence.quality_count_cells_value_options["q6"] == (
        (1, 1, 100),
        (2, 2, 200),
        (3, 3, 300),
    )
    assert evidence.q6_full_avg_support["count_one"] == {
        "candidate_count": 1,
        "common_facts": {"quality": 6, "cells": 1, "value": 100},
        "correlated_item_fact": {
            "identity": "catalog:1",
            "item_id": 1,
            "cells": 1,
            "value": 100,
        },
    }
    assert evidence.q6_full_avg_support["global_correlated_item_fact"] is None
    assert "exact_catalog_item_value_lock:q6:100" not in evidence.source_notes
    assert "q6_full_avg_multiset_support:ok:3" in evidence.source_notes

    results = [
        engine.run_reference_engine(snapshot, max_combos=limit).as_dict()
        for limit in (8_000, 50_000)
    ]
    for result in results:
        assert result["evidence"]["fixed_counts"].get("q6") is None
        assert result["evidence"]["quality_count_cells_options"]["q6"] == [
            {"count": 1, "cells": 1},
            {"count": 2, "cells": 2},
            {"count": 3, "cells": 3},
        ]
        assert result["evidence"]["quality_count_cells_value_options"]["q6"] == [
            [1, 1, 100],
            [2, 2, 200],
            [3, 3, 300],
        ]
        assert result["red_value_range"][0] >= 100
        assert result["red_value_range"][1] in {100, 200, 300}
        assert result["red_value_range"][2] in {100, 200, 300}
        assert len({result[key] for key in ("conservative", "balanced", "aggressive")}) > 1
        assert result["balanced"] != 100
    assert results[0] == results[1]


def test_no_independent_count_only_publishes_global_conservative_floors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(
        monkeypatch,
        (
            (1, 375_000, 20),
            (2, 100_000, 1),
        ),
    )

    evidence = engine.extract_evidence(
        _snapshot(total_count=None, avg_value=375_000.0)
    )

    assert evidence.fixed_counts.get("q6") is None
    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cells.get("q6") is None
    assert evidence.quality_cell_floors["q6"] == 1
    assert evidence.quality_min_total_value_floors["q6"] == 375_000
    assert evidence.quality_count_cells_options.get("q6") is None
    assert evidence.quality_count_cells_value_options.get("q6") is None
    assert evidence.q6_full_avg_support["status"] == "missing_ceiling"
    assert evidence.q6_full_avg_support["global_floors"] == {
        "count": 1,
        "cells": 1,
        "total_value": 375_000,
    }


def test_q6_total_equal_catalog_price_preserves_exact_total_single_item_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(
        monkeypatch,
        (
            (1, 200, 2),
            (2, 100, 1),
        ),
    )
    monkeypatch.setattr(
        engine,
        "_exact_item_values_by_table_quality",
        lambda: {6: frozenset({200})},
    )

    evidence = engine.extract_evidence(_snapshot(total_value=200))

    assert evidence.fixed_counts.get("q6") == 1
    assert evidence.quality_cells["q6"] == 2
    assert evidence.quality_count_cells_options["q6"] == ((1, 2),)
    assert "exact_catalog_item_value_lock:q6:200" in evidence.source_notes


def test_independent_total_avg_and_catalog_may_lock_unique_q6_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(
        monkeypatch,
        (
            (1, 200, 2),
            (2, 100, 1),
        ),
    )

    evidence = engine.extract_evidence(
        _snapshot(avg_value=100.0, total_value=200)
    )

    assert evidence.fixed_counts["q6"] == 2
    assert evidence.quality_cells["q6"] == 2
    assert evidence.quality_values["q6"] == 200
    assert evidence.quality_count_cells_options["q6"] == ((2, 2),)
    assert "q6_full_avg_multiset_support:ok:1" in evidence.source_notes


def test_same_price_duplicates_and_repetition_preserve_many_joint_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(
        monkeypatch,
        (
            (1, 100, 1),
            (2, 100, 4),
        ),
    )

    evidence = engine.extract_evidence(
        _snapshot(total_count=2, avg_value=100.0)
    )

    assert evidence.fixed_counts.get("q6") is None
    assert evidence.quality_cells.get("q6") is None
    assert evidence.quality_count_cells_options["q6"] == (
        (1, 1),
        (1, 4),
        (2, 2),
        (2, 5),
        (2, 8),
    )
    assert evidence.q6_full_avg_support["count_one"]["candidate_count"] == 2
    assert evidence.q6_full_avg_support["count_one"][
        "correlated_item_fact"
    ] is None


def test_ancient_sword_box_and_two_other_items_share_one_mean_in_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(
        monkeypatch,
        (
            (1046007, 143_100, 3),
            (1116028, 143_100, 3),
            (9000001, 127_260, 1),
            (9000002, 158_940, 4),
        ),
    )

    evidence = engine.extract_evidence(
        _snapshot(total_count=2, avg_value=143_100.0)
    )

    assert (1, 3, 143_100) in evidence.quality_count_cells_value_options["q6"]
    assert (2, 5, 286_200) in evidence.quality_count_cells_value_options["q6"]
    assert (2, 6, 286_200) in evidence.quality_count_cells_value_options["q6"]
    assert evidence.q6_full_avg_support["count_one"]["candidate_count"] == 2
    assert evidence.q6_full_avg_support["count_one"][
        "correlated_item_fact"
    ] is None
    assert evidence.q6_full_avg_support["global_correlated_item_fact"] is None


@pytest.mark.parametrize(
    ("value", "rows", "expected_cells"),
    (
        (
            888_888,
            ((1016009, 888_888, 1), (1016014, 888_888, 4)),
            {1, 4},
        ),
        (
            4_000_000,
            ((1006004, 4_000_000, 3), (1006006, 4_000_000, 6)),
            {3, 6},
        ),
    ),
)
def test_same_price_cross_cell_product_keeps_every_count_one_arm(
    monkeypatch: pytest.MonkeyPatch,
    value: int,
    rows: tuple[tuple[int, int, int], ...],
    expected_cells: set[int],
) -> None:
    _install_q6_catalog(monkeypatch, rows)

    evidence = engine.extract_evidence(
        _snapshot(total_count=1, avg_value=float(value))
    )

    assert {
        cells
        for count, cells, total_value in evidence.quality_count_cells_value_options[
            "q6"
        ]
        if count == 1 and total_value == value
    } == expected_cells
    assert evidence.quality_cells.get("q6") is None
    assert evidence.q6_known_item_facts == ()
    assert evidence.q6_full_avg_support["global_correlated_item_fact"] is None


def test_new_droppable_expands_product_support_without_special_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(monkeypatch, ((1016009, 888_888, 1),))
    before = engine.extract_evidence(
        _snapshot(total_count=1, avg_value=888_888.0)
    )
    assert before.q6_known_item_facts == (("catalog:1016009", 1, 888_888),)

    _install_q6_catalog(
        monkeypatch,
        ((1016009, 888_888, 1), (9999999, 888_888, 6)),
    )
    after = engine.extract_evidence(
        _snapshot(total_count=1, avg_value=888_888.0)
    )

    assert after.q6_known_item_facts == ()
    assert after.quality_count_cells_value_options["q6"] == (
        (1, 1, 888_888),
        (1, 6, 888_888),
    )
    assert after.q6_full_avg_support["count_one"]["candidate_count"] == 2


def test_strict_q6_deviation_solver_exhausts_more_than_twelve_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(monkeypatch, ((1, 100, 1),))

    evidence = engine.extract_evidence(
        _snapshot(total_count=13, avg_value=100.0)
    )

    states = evidence.quality_count_cells_value_options["q6"]
    assert states == tuple((count, count, count * 100) for count in range(1, 14))
    assert evidence.q6_full_avg_support["status"] == "ok"


def test_strict_q6_deviation_solver_keeps_4m_cross_cells_through_count_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(
        monkeypatch,
        ((1, 4_000_000, 1), (2, 4_000_000, 4)),
    )

    evidence = engine.extract_evidence(
        _snapshot(total_count=3, avg_value=4_000_000.0)
    )

    assert evidence.quality_count_cells_value_options["q6"] == (
        (1, 1, 4_000_000),
        (1, 4, 4_000_000),
        (2, 2, 8_000_000),
        (2, 5, 8_000_000),
        (2, 8, 8_000_000),
        (3, 3, 12_000_000),
        (3, 6, 12_000_000),
        (3, 9, 12_000_000),
        (3, 12, 12_000_000),
    )


def test_current_map_max_q6_float32_support_survives_wide_n17_total_window() -> None:
    _pairs, rows, status, generation = engine._validated_map_quality_catalog(5608, 6)
    assert status == "ok"
    assert generation.startswith("physical-category-v1:")
    item_id, item_value, item_cells = max(rows, key=lambda row: row[1])
    assert (item_id, item_value, item_cells) == (1006001, 19_371_213, 2)

    raw = struct.pack("<f", float(item_value))
    mean_evidence = q6_full_average_value_evidence_from_public_rows(
        [
            {
                "info_id": 200038,
                "semantic": "q6_avg_value",
                "kind": "avg_value",
                "quality": 6,
                "value": struct.unpack("<f", raw)[0],
                "value_field": 9,
                "value_wire_type": "float32",
                "value_raw_hex": raw.hex(),
                "value_endian": "little",
                "value_conflicts": [],
                "session_id": "5608:max-q6",
            }
        ],
        current_session_id="5608:max-q6",
    )
    bounds = integer_total_bounds_from_q6_full_average(mean_evidence, 17)
    assert bounds.status == "ok"
    assert bounds.upper is not None and bounds.lower is not None
    assert bounds.upper - bounds.lower + 1 == 35

    evidence = engine.extract_evidence(
        _snapshot(map_id=5608, total_count=17, avg_value=float(item_value))
    )
    states = evidence.quality_count_cells_value_options["q6"]
    assert {count for count, _cells, _value in states} == set(range(1, 18))
    assert (1, item_cells, item_value) in states
    assert (17, item_cells * 17, item_value * 17) in states


def test_strict_q6_complexity_guard_discards_every_partial_joint_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(
        monkeypatch,
        ((1, 80, 1), (2, 100, 2), (3, 120, 3)),
    )
    original = engine._exact_fixed_count_cells_value_candidates_from_catalog

    def guarded_solver(
        catalog_pairs,
        total_lower,
        total_upper,
        fixed_count,
        *args,
        **kwargs,
    ):
        if fixed_count == 2:
            return "deviation_complexity_guard", ()
        return original(
            catalog_pairs,
            total_lower,
            total_upper,
            fixed_count,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        engine,
        "_exact_fixed_count_cells_value_candidates_from_catalog",
        guarded_solver,
    )

    evidence = engine.extract_evidence(
        _snapshot(total_count=3, avg_value=100.0)
    )

    assert evidence.q6_full_avg_support["status"] == "deviation_complexity_guard"
    assert evidence.quality_count_cells_value_options.get("q6") is None
    assert evidence.fixed_counts.get("q6") is None
    assert not any(
        note.startswith("q6_full_avg_multiset_support:ok:")
        for note in evidence.source_notes
    )


def test_strict_joint_total_is_terminal_against_aisha_residual_repricing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pairs, rows, status, generation = engine._validated_map_quality_catalog(5608, 6)
    assert status == "ok"
    assert (1046007, 143_100, 3) in rows
    assert (1916001, 519_800, 20) in rows
    domain = {
        "key": "runtime:97001",
        "runtime_id": 97001,
        "local_index": 2,
        "observed_quality": None,
        "shape_key": "13",
        "cells": 3,
        "required_categories": [104],
        "excluded_categories": [],
        "excluded_qualities": [1, 2, 3, 4, 5],
        "candidate_item_ids": [1046007],
        "candidate_qualities": [6],
        "candidate_values": [143_100],
        "catalog_status": "ok",
        "catalog_generation": generation,
        "hard_eligible": True,
        "quality": 6,
        "quality_source": "catalog_red_domain",
        "exact_item_id": 1046007,
        "exact_value": 143_100,
        "sources": ["audit:p1-2"],
    }
    item = {
        "runtime_id": 97001,
        "local_index": 2,
        "item_id": 1046007,
        "quality": 6,
        "shape_key": "13",
        "cells": 3,
        "value": 143_100,
        "categories": [104],
        "excluded_categories": [],
        "excluded_qualities": [1, 2, 3, 4, 5],
        "quality_source": "catalog_red_domain",
    }
    snapshot = _snapshot(
        map_id=5608,
        total_count=3,
        avg_value=331_450.0,
        minimap_items=[item],
        physical_candidate_domains=[domain],
    )
    snapshot["structured_ref_inputs"].update(
        {
            "total_cells": 24,
            "fixed_counts": {"q1": 1, "q3": 0, "q4": 0, "q5": 0},
            "quality_cells": {"q1": 1, "q3": 0, "q4": 0, "q5": 0},
        }
    )

    current = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(engine.AISHA_KNOWN_RED_RESIDUAL_VALUE_DISABLE, "1")
    historical_repricing_disabled = engine.run_reference_engine(
        snapshot,
        max_combos=8_000,
    ).as_dict()

    assert current["evidence"]["quality_count_cells_value_options"]["q6"] == [
        [2, 23, 662_900]
    ]
    assert current["red_value_range"] == [662_900, 662_900, 662_900]
    for key in ("conservative", "balanced", "aggressive"):
        assert current[key] == historical_repricing_disabled[key]
    assert not any(
        note.startswith(engine.AISHA_KNOWN_RED_RESIDUAL_VALUE_NOTE)
        for note in current["notes"]
    )


def test_physical_category_shape_domain_is_a_mandatory_value_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(
        monkeypatch,
        (
            (1, 100, 3),
            (2, 200, 3),
            (3, 150, 1),
        ),
    )
    domain = {
        "key": "runtime:97002",
        "runtime_id": 97002,
        "local_index": 4,
        "observed_quality": None,
        "shape_key": "13",
        "cells": 3,
        "required_categories": [107],
        "excluded_categories": [106],
        "excluded_qualities": [1, 2, 3, 4, 5],
        "candidate_item_ids": [1, 2],
        "candidate_qualities": [6],
        "candidate_values": [100, 200],
        "catalog_status": "ok",
        "catalog_generation": "physical-category-v1:joint-test",
        "hard_eligible": True,
        "quality": 6,
        "quality_source": "catalog_red_domain",
        "sources": ["action:100157", "action_negative:100156"],
    }

    evidence = engine.extract_evidence(
        _snapshot(
            total_count=2,
            avg_value=150.0,
            physical_candidate_domains=[domain],
        )
    )

    assert evidence.fixed_counts["q6"] == 2
    assert evidence.quality_cells["q6"] == 6
    assert evidence.quality_count_cells_value_options["q6"] == ((2, 6, 300),)
    assert evidence.q6_full_avg_support["physical_domain_residuals"] == [
        {
            "identity": "local:4",
            "candidate_count": 2,
            "candidate_ids": [1, 2],
            "value_cells_options": [[100, 3], [200, 3]],
        }
    ]
    assert evidence.q6_full_avg_support["count_one"]["candidate_count"] == 0


def test_physical_category_shape_domain_catalog_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(monkeypatch, ((1, 150, 1),))
    domain = {
        "key": "runtime:97003",
        "runtime_id": 97003,
        "local_index": 5,
        "observed_quality": None,
        "shape_key": "13",
        "cells": 3,
        "required_categories": [107],
        "excluded_categories": [],
        "excluded_qualities": [1, 2, 3, 4, 5],
        "candidate_item_ids": [1],
        "candidate_qualities": [6],
        "candidate_values": [150],
        "catalog_status": "ok",
        "catalog_generation": "physical-category-v1:joint-test",
        "hard_eligible": True,
        "quality": 6,
        "quality_source": "catalog_red_domain",
        "sources": ["action:100157"],
    }

    evidence = engine.extract_evidence(
        _snapshot(
            total_count=1,
            avg_value=150.0,
            physical_candidate_domains=[domain],
        )
    )

    assert evidence.fixed_counts.get("q6") is None
    assert evidence.quality_count_cells_value_options.get("q6") is None
    assert evidence.q6_full_avg_support["status"] == (
        "physical_domain_catalog_conflict"
    )


def test_public_total_avg_conflict_never_falls_through_to_arithmetic_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(monkeypatch, ((1, 100, 1),))

    evidence = engine.extract_evidence(
        _snapshot(avg_value=100.0, total_value=150)
    )

    assert evidence.fixed_counts.get("q6") is None
    assert evidence.quality_count_cells_options.get("q6") is None
    assert "q6_full_avg_multiset_support:no_support:0" in evidence.source_notes


def test_map_unreachable_catalog_price_is_not_global_count_one_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_rows = (
        (1, 100, 1),
        (2, 200, 2),
    )
    monkeypatch.setattr(
        engine,
        "_validated_q6_catalog",
        lambda: (
            ((100, 1), (200, 2)),
            base_rows,
            "ok",
        ),
    )
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: (
            {
                1: (6, 100, 1, 1, (107,)),
                2: (6, 200, 1, 2, (107,)),
            },
            {9901: frozenset({2})},
            "ok",
            "physical-category-v1:reachable-test",
        ),
    )
    evidence = engine.extract_evidence(
        _snapshot(total_count=2, avg_value=100.0)
    )

    assert evidence.fixed_counts.get("q6") is None
    assert evidence.quality_count_cells_options.get("q6") is None
    assert "q6_full_avg_multiset_support:no_support:0" in evidence.source_notes


def test_lane_a_canonical_helper_excludes_weight_zero_and_fails_bad_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processed = [
        {
            "item_id": 1,
            "quality": 6,
            "value": 100,
            "shape_w": 1,
            "shape_h": 1,
            "tags": [107],
        },
        {
            "item_id": 2,
            "quality": 6,
            "value": 200,
            "shape_w": 1,
            "shape_h": 2,
            "tags": [106],
        },
    ]
    legacy_positive_hints = {"1": 107, "2": 106}
    weights = {
        "5608": {
            "n_items": 2,
            "items": {
                "1": [1.0, 100, 100],
                "2": [0.0, 200, 200],
            },
        }
    }

    def load_json(path: Path):
        if path.name == "items_droppable.json":
            return processed
        if path.name == "item_category_mem_0625.json":
            return legacy_positive_hints
        if path.name == "drop_weights_all_maps_0627.json":
            return weights
        raise AssertionError(path)

    monkeypatch.setattr(v2_module, "load_json", load_json)
    monkeypatch.setattr(
        v2_module,
        "_complete_physical_category_authority",
        lambda _root, _processed: (
            {1: frozenset({107}), 2: frozenset({106})},
            "ok",
            "test-authority",
        ),
    )
    catalog, reachable, status, generation = (
        v2_module._validated_physical_category_catalog.__wrapped__()
    )
    assert status == "ok"
    assert set(catalog) == {1, 2}
    assert reachable == {5608: frozenset({1})}
    assert generation.startswith("physical-category-v1:")

    weights["5608"]["n_items"] = 3
    catalog, reachable, status, generation = (
        v2_module._validated_physical_category_catalog.__wrapped__()
    )
    assert catalog == {}
    assert reachable == {}
    assert status == "catalog_generation_mismatch"
    assert generation == ""


def test_q6_exact_total_branch_remains_authoritative_across_catalog_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = engine._validated_map_quality_catalog

    def catalog(requested_map: int, quality: int):
        if requested_map == 9901 and quality == 6:
            return (), (), "catalog_mismatch", "physical-category-v1:mismatch-test"
        return original(requested_map, quality)

    monkeypatch.setattr(engine, "_validated_map_quality_catalog", catalog)
    actions = [
        {"action_id": 100114, "result": 15.0},
        {"action_id": 100126, "result": 375_000},
    ]
    snapshot = _snapshot(total_count=1)
    snapshot["ui_contract"]["actions"] = {"results": actions}
    snapshot["action_result_rows"] = actions

    evidence = engine.extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q6") == 1
    assert evidence.quality_count_cells_options.get("q6") is None
    assert "exact_catalog_item_value_lock:q6:375000" in evidence.source_notes
    assert "q6_catalog_value_joint_constraint:catalog_mismatch:0" in evidence.source_notes


def test_activity_model_map_identity_drives_reachability_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[tuple[int, int]] = []

    def catalog(map_id: int, quality: int):
        requested.append((map_id, quality))
        if map_id == 2501 and quality == 6:
            return (
                ((100, 1),),
                ((1, 100, 1),),
                "ok",
                "physical-category-v1:model-map-test",
            )
        return (), (), "map_catalog_unavailable", ""

    monkeypatch.setattr(engine, "_validated_map_quality_catalog", catalog)
    snapshot = _snapshot(map_id=2541, total_count=2, avg_value=100.0)
    snapshot["ui_contract"]["context"]["model_map_id"] = 2501
    snapshot["model_map_id"] = 2501

    evidence = engine.extract_evidence(snapshot)

    assert (2501, 6) in requested
    assert (2541, 6) not in requested
    assert evidence.quality_count_cells_options["q6"] == ((1, 1), (2, 2))


def test_correlated_jianying_item_anchor_is_one_known_item_not_whole_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(monkeypatch, ((1076005, 375_000, 15),))
    domain = {
        "key": "runtime:97001",
        "runtime_id": 97001,
        "local_index": 2,
        "observed_quality": None,
        "shape_key": "35",
        "cells": 15,
        "required_categories": [107],
        "excluded_categories": [106],
        "excluded_qualities": [1, 2, 3, 4, 5],
        "candidate_item_ids": [1076005],
        "candidate_qualities": [6],
        "candidate_values": [375000],
        "catalog_status": "ok",
        "catalog_generation": "physical-category-v1:joint-test",
        "hard_eligible": True,
        "quality": 6,
        "quality_source": "catalog_red_domain",
        "exact_item_id": 1076005,
        "exact_value": 375000,
        "sources": ["action:100157", "action_negative:100156"],
    }
    item = {
        "runtime_id": 97001,
        "local_index": 2,
        "item_id": 1076005,
        "quality": 6,
        "shape_key": "35",
        "cells": 15,
        "value": 375000,
        "categories": [107],
        "excluded_categories": [106],
        "excluded_qualities": [1, 2, 3, 4, 5],
        "quality_source": "catalog_red_domain",
    }
    snapshot = _snapshot(
        avg_value=375_000.0,
        minimap_items=[item],
        physical_candidate_domains=[domain],
    )

    evidence = engine.extract_evidence(snapshot)

    assert evidence.q6_known_item_facts == (("local:2", 15, 375_000),)
    assert evidence.fixed_counts.get("q6") is None
    assert {count for count, _cells in evidence.quality_count_cells_options["q6"]} == {
        1,
        2,
        3,
    }
    assert evidence.min_counts.get("q6") == 1


def test_q6_joint_rollback_restores_legacy_lock_and_unrelated_bytes_are_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(monkeypatch, ((1, 100, 1),))
    monkeypatch.setattr(
        engine,
        "_exact_item_values_by_table_quality",
        lambda: {6: frozenset({100})},
    )
    relevant = _snapshot(avg_value=100.0)
    default = engine.extract_evidence(relevant)
    assert default.fixed_counts.get("q6") is None

    monkeypatch.setenv(engine.Q6_CATALOG_VALUE_JOINT_DISABLE, "1")
    rollback = engine.extract_evidence(relevant)
    assert rollback.fixed_counts["q6"] == 1
    assert "exact_catalog_item_value_lock:q6:100" in rollback.source_notes

    unrelated = _snapshot(avg_value=None)
    monkeypatch.delenv(engine.Q6_CATALOG_VALUE_JOINT_DISABLE)
    default_wire = json.dumps(
        engine.run_reference_engine(unrelated, max_combos=8_000).as_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    monkeypatch.setenv(engine.Q6_CATALOG_VALUE_JOINT_DISABLE, "1")
    rollback_wire = json.dumps(
        engine.run_reference_engine(unrelated, max_combos=8_000).as_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert default_wire == rollback_wire


def test_lane_c_kill_switch_is_fresh_process_stable_at_8k_and_50k() -> None:
    script = r'''
import json
import os
import struct
import sys
from pathlib import Path

root = Path.cwd()
sys.path[:0] = [str(root / "hero_ref" / "src"), str(root / "src")]
import ahmad_ref_engine as engine

original = engine._validated_map_quality_catalog
def catalog(map_id, quality):
    if (map_id, quality) == (9901, 6):
        return ((100, 1),), ((1, 100, 1),), "ok", "physical-category-v1:fresh"
    return original(map_id, quality)
engine._validated_map_quality_catalog = catalog

session_id = "9901:fresh"
constraints = {}
if os.environ["LANE_C_RELEVANT"] == "1":
    raw = struct.pack("<f", 100.0)
    row = {
        "info_id": 200038,
        "semantic": "q6_avg_value",
        "kind": "avg_value",
        "quality": 6,
        "value": struct.unpack("<f", raw)[0],
        "value_field": 9,
        "value_wire_type": "float32",
        "value_raw_hex": raw.hex(),
        "value_endian": "little",
        "value_conflicts": [],
        "session_id": session_id,
    }
    constraints["public_info"] = {
        "public_numeric_facts": [row],
        "public_avg_values": [row],
    }
snapshot = {
    "session_id": session_id,
    "ui_contract": {
        "context": {
            "hero": "aisha",
            "map_id": 9901,
            "phase": "bidding",
            "session_id": session_id,
        },
        "constraints": constraints,
    },
    "structured_ref_inputs": {"total_count": 3},
}
result = engine.run_reference_engine(
    snapshot,
    max_combos=int(os.environ["LANE_C_MAX_COMBOS"]),
).as_dict()
wire = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
print(json.dumps({
    "wire": wire,
    "fixed": result["evidence"]["fixed_counts"].get("q6"),
    "pair_states": result["evidence"]["quality_count_cells_options"].get("q6"),
    "value_states": result["evidence"].get(
        "quality_count_cells_value_options", {}
    ).get("q6"),
    "support": result["evidence"].get("q6_full_avg_support", {}),
    "red_value_range": result["red_value_range"],
    "quotes": [result["conservative"], result["balanced"], result["aggressive"]],
}, ensure_ascii=True, sort_keys=True))
'''

    def run(*, disabled: bool, limit: int, relevant: bool) -> dict:
        env = dict(os.environ)
        env["LANE_C_MAX_COMBOS"] = str(limit)
        env["LANE_C_RELEVANT"] = "1" if relevant else "0"
        if disabled:
            env[engine.Q6_FULL_AVG_MULTISET_DISABLE] = "1"
        else:
            env.pop(engine.Q6_FULL_AVG_MULTISET_DISABLE, None)
        completed = subprocess.run(  # noqa: S603 - exact current interpreter
            [sys.executable, "-B", "-c", script],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(completed.stdout)

    default = {
        limit: run(disabled=False, limit=limit, relevant=True)
        for limit in (8_000, 50_000)
    }
    rollback = {
        limit: run(disabled=True, limit=limit, relevant=True)
        for limit in (8_000, 50_000)
    }
    assert default[8_000] == default[50_000]
    assert rollback[8_000] == rollback[50_000]
    assert default[8_000]["fixed"] is None
    assert default[8_000]["value_states"] == [
        [1, 1, 100],
        [2, 2, 200],
        [3, 3, 300],
    ]
    assert default[8_000]["support"]["status"] == "ok"
    assert rollback[8_000]["fixed"] is None
    assert rollback[8_000]["value_states"] is None
    assert rollback[8_000]["pair_states"] == [
        {"count": 1, "cells": 1},
        {"count": 2, "cells": 2},
        {"count": 3, "cells": 3},
    ]
    assert rollback[8_000]["support"] == {}

    unrelated_default = {
        limit: run(disabled=False, limit=limit, relevant=False)["wire"]
        for limit in (8_000, 50_000)
    }
    unrelated_rollback = {
        limit: run(disabled=True, limit=limit, relevant=False)["wire"]
        for limit in (8_000, 50_000)
    }
    assert unrelated_default == unrelated_rollback


def test_settled_200038_is_diagnostic_only_and_never_injects_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_q6_catalog(monkeypatch, ((1, 100, 1),))
    snapshot = _snapshot(total_count=3, avg_value=100.0)
    snapshot["phase"] = "settled"
    snapshot["ui_contract"]["context"]["phase"] = "settled"
    without_200038 = json.loads(json.dumps(snapshot))
    without_200038["ui_contract"]["constraints"]["public_info"] = {}

    evidence = engine.extract_evidence(snapshot)
    baseline_evidence = engine.extract_evidence(without_200038)

    assert evidence.avg_values.get("q6") is None
    assert evidence.quality_count_cells_value_options.get("q6") is None
    assert evidence.q6_full_avg_support == baseline_evidence.q6_full_avg_support
    assert evidence == baseline_evidence
    assert (
        engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
        == engine.run_reference_engine(
            without_200038,
            max_combos=8_000,
        ).as_dict()
    )


def test_public_q5_noncatalog_fraction_is_soft_without_joint_support() -> None:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
            },
            "constraints": {
                "public_info": {
                    "public_avg_values": [
                        {
                            "semantic": "q5_avg_value",
                            "kind": "avg_value",
                            "quality": 5,
                            "value": 1560.125,
                        }
                    ]
                }
            },
        },
        "structured_ref_inputs": {"total_count": 15},
    }

    result = engine.run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["evidence"]["fixed_counts"].get("q5") is None
    assert result["evidence"]["quality_count_cells_options"].get("q5") is None
    assert "q5_exact_value_cells_multiset:ok:0" in result["notes"]
    assert len(set(result["quality_count_ranges"]["q5"])) > 1


@pytest.mark.parametrize(
    ("key", "quality", "map_id"),
    (("q4", 4, 2401), ("q5", 5, 2401), ("q6", 6, 5608)),
)
def test_q4_q5_q6_catalog_equal_public_avg_never_means_bucket_count_one(
    key: str,
    quality: int,
    map_id: int,
) -> None:
    _pairs, rows, status, _generation = engine._validated_map_quality_catalog(
        map_id, quality
    )
    assert status == "ok"
    _item_id, value, _cells = min(rows, key=lambda row: (row[1], row[0]))
    public_row = {
        "semantic": f"{key}_avg_value",
        "kind": "avg_value",
        "quality": quality,
        "value": value,
    }
    session_id = f"{map_id}:catalog-equal"
    if key == "q6":
        raw = struct.pack("<f", float(value))
        public_row.update(
            {
                "info_id": 200038,
                "value": struct.unpack("<f", raw)[0],
                "value_field": 9,
                "value_wire_type": "float32",
                "value_raw_hex": raw.hex(),
                "value_endian": "little",
                "value_conflicts": [],
                "session_id": session_id,
            }
        )
    snapshot = {
        "session_id": session_id,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": map_id,
                "phase": "bidding",
                "session_id": session_id,
            },
            "constraints": {
                "public_info": {
                    "public_avg_values": [public_row],
                    "public_numeric_facts": [public_row] if key == "q6" else [],
                }
            },
        },
        "structured_ref_inputs": {"total_count": 3},
    }

    evidence = engine.extract_evidence(snapshot)

    assert evidence.fixed_counts.get(key) is None
    assert not any(
        note.startswith(f"exact_catalog_item_value_lock:{key}:")
        for note in evidence.source_notes
    )
    if key in {"q5", "q6"}:
        assert {
            count
            for count, _candidate_cells in evidence.quality_count_cells_options[key]
        } == {1, 2, 3}


def test_strict_q6_support_is_identical_in_summary_api_panel_and_tk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import threading  # noqa: PLC0415
    from http.server import ThreadingHTTPServer  # noqa: PLC0415
    from urllib.request import urlopen  # noqa: PLC0415

    tools = ROOT / "hero_ref" / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from ahmad_live_panel_server import (  # noqa: PLC0415
        ServerConfig,
        make_handler,
        summarize_snapshot,
    )
    from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: PLC0415

    _install_q6_catalog(
        monkeypatch,
        (
            (1016009, 888_888, 1),
            (1016014, 888_888, 4),
        ),
    )
    snapshot = _snapshot(total_count=2, avg_value=888_888.0)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    direct = engine.run_reference_engine(snapshot, max_combos=50_000).as_dict()
    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=10,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )

    expected_states = [
        [1, 1, 888_888],
        [1, 4, 888_888],
        [2, 2, 1_777_776],
        [2, 5, 1_777_776],
        [2, 8, 1_777_776],
    ]
    for surface in (direct, summary["ahmed_ref"], payload["ahmed_ref"]):
        assert surface["evidence"]["quality_count_cells_value_options"][
            "q6"
        ] == expected_states
        assert surface["evidence"]["q6_full_avg_support"]["joint_states"] == (
            expected_states
        )
        assert surface["evidence"]["fixed_counts"].get("q6") is None
        assert surface["red_value_range"][0] >= 888_888
    assert tk_row["ref_v0"]["evidence"][
        "quality_count_cells_value_options"
    ]["q6"] == expected_states
    assert tk_row["ref_v0"]["evidence"]["q6_full_avg_support"][
        "joint_states"
    ] == expected_states
    assert {
        key: summary["ahmed_ref"][key]
        for key in ("conservative", "balanced", "aggressive")
    } == {
        key: payload["ahmed_ref"][key]
        for key in ("conservative", "balanced", "aggressive")
    }
    assert tk_row["ref_v0"]["value_range"] == {
        "p25": direct["value_p25"],
        "p50": direct["value_p50"],
        "p75": direct["value_p75"],
    }


def test_strict_q6_joint_cells_are_terminal_against_h7_in_every_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import threading  # noqa: PLC0415
    from http.server import ThreadingHTTPServer  # noqa: PLC0415
    from urllib.request import urlopen  # noqa: PLC0415

    tools = ROOT / "hero_ref" / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from ahmad_live_panel_server import (  # noqa: PLC0415
        ServerConfig,
        make_handler,
        summarize_snapshot,
    )
    from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: PLC0415

    snapshot = _snapshot(map_id=5608, total_count=2, avg_value=143_100.0)
    snapshot.update({"hero": "ahmed", "round": 2})
    snapshot["ui_contract"]["context"].update({"hero": "ahmed", "round": 2})
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    direct = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=10,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )

    expected_states = [
        [1, 3, 143_100],
        [2, 4, 286_200],
        [2, 6, 286_200],
    ]
    for surface in (direct, summary["ahmed_ref"], payload["ahmed_ref"]):
        assert surface["evidence"]["quality_count_cells_value_options"][
            "q6"
        ] == expected_states
        assert surface["red_count_range"] == [1, 1, 1]
        assert surface["red_cells_range"] == [3, 3, 3]
        assert surface["red_value_range"] == [143_100, 143_100, 143_100]
        assert surface["quality_cells_ranges"]["q6"] == [3, 3, 3]
        assert not any(
            note.startswith("red_cells_tail_hedge_hi:")
            for note in surface["notes"]
        )
    assert summary["red"]["cells_range"] == "3 / 3 / 3"
    assert payload["red"]["cells_range"] == "3 / 3 / 3"
    assert tk_row["red"]["cells_range"] == "3 / 3 / 3"
    assert tk_row["ref_v0"]["evidence"][
        "quality_count_cells_value_options"
    ]["q6"] == expected_states

    monkeypatch.setenv(engine.Q6_FULL_AVG_MULTISET_DISABLE, "1")
    rollback = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert rollback["evidence"].get(
        "quality_count_cells_value_options", {}
    ).get("q6") is None
    assert rollback["red_cells_range"][2] == 6
    assert any(
        note.startswith("red_cells_tail_hedge_hi:3->6:")
        for note in rollback["notes"]
    )


def test_strict_q6_joint_requires_complete_combo_support_before_publication() -> None:
    snapshot = _snapshot(map_id=5608, total_count=2, avg_value=143_100.0)
    snapshot.update({"hero": "ahmed", "round": 2})
    snapshot["ui_contract"]["context"].update({"hero": "ahmed", "round": 2})

    for limit in (1, 2, 3, 4, 5):
        result = engine.run_reference_engine(
            snapshot,
            max_combos=limit,
        ).as_dict()
        assert result["status"] == "incomplete_support"
        assert result["combo_count"] == 0
        assert result["conservative"] is None
        assert result["balanced"] is None
        assert result["aggressive"] is None
        assert result["red_count_range"] == [None, None, None]
        assert result["red_cells_range"] == [None, None, None]
        assert result["red_value_range"] == [None, None, None]
        assert result["quality_count_ranges"] == {}
        assert result["quality_cells_ranges"] == {}
        assert result["total_grid_range"] == [None, None, None]
        assert result["quality_count_candidates"] == {}
        assert result["quality_cells_candidates"] == {}
        assert result["quality_count_cells_candidates"] == {}
        assert result["quality_lock"] == {}
        evidence = result["evidence"]
        assert evidence.get("quality_count_cells_options", {}).get("q6") is None
        assert (
            evidence.get("quality_count_cells_value_options", {}).get("q6")
            is None
        )
        assert evidence["q6_full_avg_support"] == {
            "status": "incomplete_support",
            "joint_states": [],
        }
        assert engine.Q6_FULL_AVG_COMBO_SUPPORT_INCOMPLETE_NOTE in result["notes"]

    complete = [
        engine.run_reference_engine(snapshot, max_combos=limit).as_dict()
        for limit in (6, 8_000, 50_000)
    ]
    assert all(result["combo_count"] == 6 for result in complete)
    assert all(
        engine.Q6_FULL_AVG_COMBO_SUPPORT_INCOMPLETE_NOTE not in result["notes"]
        for result in complete
    )
    assert complete[0] == complete[1] == complete[2]


def test_q6_incomplete_publication_precedes_total_grid_early_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(map_id=5608, total_count=2, avg_value=143_100.0)
    snapshot.update({"hero": "ahmed", "round": 2})
    snapshot["ui_contract"]["context"].update({"hero": "ahmed", "round": 2})
    prepared_snapshot = engine.prepare_reference_engine_snapshot(snapshot)
    base_evidence = engine.extract_evidence(snapshot)
    overlap_evidence = replace(
        base_evidence,
        total_grid_options=(2, 3),
        avg_values={**base_evidence.avg_values, "q5": 50_000.0},
        hard_public_avg_values={
            **base_evidence.hard_public_avg_values,
            "q5": 50_000.0,
        },
    )
    monkeypatch.setattr(engine, "extract_evidence", lambda _snapshot: overlap_evidence)

    entrypoints = (
        (engine._run_reference_engine_impl, snapshot),
        (engine.run_reference_engine, snapshot),
        (engine.run_reference_engine, prepared_snapshot),
    )
    for runner, candidate_snapshot in entrypoints:
        for limit in (1, 2, 3, 4, 5):
            result = runner(
                candidate_snapshot,
                max_combos=limit,
            )
            public = result.as_dict()
            assert public["status"] == "incomplete_support"
            assert public["combo_count"] == 0
            assert public["conservative"] is None
            assert public["balanced"] is None
            assert public["aggressive"] is None
            assert public["red_count_range"] == [None, None, None]
            assert public["red_cells_range"] == [None, None, None]
            assert public["red_value_range"] == [None, None, None]
            assert public["quality_count_ranges"] == {}
            assert public["quality_cells_ranges"] == {}
            assert public["quality_count_candidates"] == {}
            assert public["quality_cells_candidates"] == {}
            assert public["quality_count_cells_candidates"] == {}
            assert public["quality_lock"] == {}
            evidence = public["evidence"]
            assert evidence.get("quality_count_cells_options", {}).get("q6") is None
            assert (
                evidence.get("quality_count_cells_value_options", {}).get("q6")
                is None
            )
            assert evidence["q6_full_avg_support"] == {
                "status": "incomplete_support",
                "joint_states": [],
            }
            assert evidence["avg_values"]["q5"] == 50_000.0
            assert result.evidence.hard_public_avg_values["q5"] == 50_000.0
            assert engine.Q6_FULL_AVG_COMBO_SUPPORT_INCOMPLETE_NOTE in public[
                "notes"
            ]
