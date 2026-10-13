"""SAV2 shadow-only canonical support for the five shape-average tools."""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import textwrap
from types import SimpleNamespace

import pytest

from bidking_lab.inference.mean_multiset_support import (
    MeanSupportCandidate,
    solve_repeat_allowed_mean_support,
)
from bidking_lab.inference.size_avg_evidence import (
    SHAPE_AVG_EXACT_CONSTRAINT_DISABLE,
    SIZE_AVG_VALUE_SIGNAL_FLOORS,
    shape_average_value_evidence_from_action_rows,
    size_bucket_count_targets_for_structured_evidence,
    size_bucket_eval_fields,
)
import bidking_lab.inference.v2 as v2_module
from bidking_lab.inference.v2 import (
    EvidenceStoreBuilder,
    RuntimeEvidence,
    canonical_map_footprint_domain,
)
from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansCaptureEvents,
    FatbeansStateEvent,
)
from bidking_lab.live.monitor import (
    _action_result_rows,
    _shape_average_mandatory_units_by_cells,
)
from bidking_lab.runtime.snapshot import ui_contract_from_artifact


ROOT = Path(__file__).resolve().parents[1]


def _row(
    value: float,
    *,
    action_id: int = 100171,
    session_id: str = "5608:1",
) -> dict[str, object]:
    raw = struct.pack("<f", value)
    return {
        "action_id": action_id,
        "result": struct.unpack("<f", raw)[0],
        "result_field": 9,
        "result_wire_type": "float32",
        "result_raw_hex": raw.hex(),
        "session_id": session_id,
        "sort": 20,
        "round": 2,
    }


def _domain(
    candidates: list[tuple[int, int, int]],
    *,
    cells: int = 3,
    status: str = "ok",
) -> dict[str, object]:
    return {
        "map_id": 5608,
        "footprint_cells": cells,
        "catalog_status": status,
        "catalog_generation": "test-generation",
        "candidates": [
            {
                "item_id": item_id,
                "quality": quality,
                "value": value,
                "cells": cells,
            }
            for item_id, quality, value in candidates
        ],
    }


def _signature_key(row: object) -> tuple[object, ...]:
    if hasattr(row, "as_dict"):
        row = row.as_dict()
    assert isinstance(row, dict)
    return (
        row["item_count"],
        row["total_cells"],
        row["total_value"],
        tuple(sorted(row["quality_counts"].items())),
        tuple(sorted(row["quality_values"].items())),
    )


def test_signed_deviation_solver_keeps_repeat_allowed_cross_quality_support() -> None:
    candidates = (
        MeanSupportCandidate(11, 4, 100, 3),
        MeanSupportCandidate(12, 5, 200, 3),
        MeanSupportCandidate(13, 6, 300, 3),
    )

    two = solve_repeat_allowed_mean_support(
        candidates,
        total_lower=400,
        total_upper=400,
        item_count=2,
    )
    repeated = solve_repeat_allowed_mean_support(
        candidates,
        total_lower=300,
        total_upper=300,
        item_count=3,
    )

    assert two.status == "ok"
    assert {_signature_key(row) for row in two.signatures} == {
        (2, 6, 400, (("q4", 1), ("q6", 1)), (("q4", 100), ("q6", 300))),
        (2, 6, 400, (("q5", 2),), (("q5", 400),)),
    }
    assert repeated.status == "ok"
    assert {_signature_key(row) for row in repeated.signatures} == {
        (3, 9, 300, (("q4", 3),), (("q4", 300),)),
    }


def test_signed_deviation_solver_matches_small_bruteforce_oracle() -> None:
    candidates = (
        MeanSupportCandidate(21, 4, 90, 2),
        MeanSupportCandidate(22, 5, 140, 2),
        MeanSupportCandidate(23, 6, 190, 2),
    )
    for count in range(1, 5):
        for total in range(180, 571, 17):
            solved = solve_repeat_allowed_mean_support(
                candidates,
                total_lower=total,
                total_upper=total + 8,
                item_count=count,
            )
            expected: set[tuple[object, ...]] = set()
            for chosen in itertools.product(candidates, repeat=count):
                total_value = sum(item.value for item in chosen)
                if not total <= total_value <= total + 8:
                    continue
                quality_counts: dict[str, int] = {}
                quality_values: dict[str, int] = {}
                for item in chosen:
                    key = f"q{item.quality}"
                    quality_counts[key] = quality_counts.get(key, 0) + 1
                    quality_values[key] = quality_values.get(key, 0) + item.value
                expected.add(
                    (
                        count,
                        count * 2,
                        total_value,
                        tuple(sorted(quality_counts.items())),
                        tuple(sorted(quality_values.items())),
                    )
                )
            assert solved.status == "ok"
            assert {_signature_key(row) for row in solved.signatures} == expected


def test_solver_count_one_keeps_equal_value_ids_and_mandatory_unit() -> None:
    candidates = (
        MeanSupportCandidate(31, 6, 143_100, 3),
        MeanSupportCandidate(32, 6, 143_100, 3),
        MeanSupportCandidate(33, 4, 100, 3),
    )
    count_one = solve_repeat_allowed_mean_support(
        candidates,
        total_lower=143_100,
        total_upper=143_100,
        item_count=1,
    )
    mandatory = solve_repeat_allowed_mean_support(
        candidates,
        total_lower=143_200,
        total_upper=143_200,
        item_count=2,
        mandatory_units=(
            {"identity": "runtime:1", "candidate_item_ids": [31, 32]},
        ),
    )

    assert count_one.count_one_candidate_item_ids == (31, 32)
    assert len(count_one.signatures) == 1
    assert mandatory.status == "ok"
    assert len(mandatory.signatures) == 1
    assert mandatory.signatures[0].quality_counts == (0, 0, 0, 1, 0, 1)


def test_solver_complexity_guard_returns_no_partial_support() -> None:
    solved = solve_repeat_allowed_mean_support(
        (
            MeanSupportCandidate(41, 4, 100, 3),
            MeanSupportCandidate(42, 5, 200, 3),
        ),
        total_lower=200,
        total_upper=400,
        item_count=2,
        layer_signature_budget=1,
    )

    assert solved.status == "deviation_complexity_guard"
    assert solved.signatures == ()
    assert solved.count_one_candidate_item_ids == ()


def test_real_5608_three_cell_domain_keeps_ancient_sword_and_box() -> None:
    domain = canonical_map_footprint_domain(5608, 3)
    by_id = {candidate.item_id: candidate for candidate in domain.candidates}

    assert domain.catalog_status == "ok"
    assert {1046007, 1116028}.issubset(by_id)
    assert by_id[1046007].value == by_id[1116028].value == 143_100
    assert all(candidate.cells == 3 for candidate in domain.candidates)


def test_selector_uses_current_map_positive_reachable_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: (
            {
                51: (4, 100, 3, 1, (101,)),
                52: (5, 200, 3, 1, (102,)),
                53: (6, 300, 3, 1, (103,)),
            },
            {77: frozenset({51, 53})},
            "ok",
            "single-snapshot:test",
        ),
    )

    domain = canonical_map_footprint_domain(77, 3)
    unreachable = canonical_map_footprint_domain(78, 3)

    assert domain.catalog_status == "ok"
    assert [candidate.item_id for candidate in domain.candidates] == [51, 53]
    assert domain.catalog_generation == "single-snapshot:test"
    assert unreachable.catalog_status == "map_catalog_unavailable"
    assert unreachable.candidates == ()


def test_5608_count_one_shadow_does_not_choose_equal_value_item() -> None:
    evidence = shape_average_value_evidence_from_action_rows(
        [_row(143_100.0)],
        current_session_id="5608:1",
        posterior_targets=[
            {
                "cells": 3,
                "count_exact": 1,
                "count_provenance": ["semantic_full_outline"],
            }
        ],
        candidate_domains_by_cells={
            3: canonical_map_footprint_domain(5608, 3)
        },
    )[0]

    assert evidence["support_status"] == "ok"
    assert evidence["count_one"] == {
        "candidate_item_ids": [1046007, 1116028],
        "candidate_count": 2,
        "unique_item_id": None,
    }
    assert evidence["composition_signature_kind"] == (
        "quality_count_cells_value"
    )
    assert evidence["reachable_item_multiset_count"] is None
    assert evidence["item_multiset_uniqueness"] == "not_computed"
    assert evidence["common_facts"]["correlated_item_fact"] is None
    assert evidence["common_facts"]["quality_count_floors"] == {"q6": 1}
    assert evidence["common_facts"]["quality_cells_floors"] == {"q6": 3}
    assert evidence["common_facts"]["quality_value_floors"] == {
        "q6": 143_100
    }
    assert evidence["active_constraints"] == []
    assert evidence["hard_eligible"] is False
    assert evidence["quote_effect"] == "shadow"


def test_shadow_domain_is_bound_to_current_session_map() -> None:
    wrong_map_domain = _domain([(51, 6, 143_100)])
    wrong_map_domain["map_id"] = 5609

    evidence = shape_average_value_evidence_from_action_rows(
        [_row(143_100.0)],
        current_session_id="5608:1",
        posterior_targets=[{"cells": 3, "count_exact": 1}],
        candidate_domains_by_cells={3: wrong_map_domain},
    )[0]

    assert evidence["support_status"] == "catalog_map_mismatch"
    assert evidence["support_complete"] is False
    assert evidence["joint_support"] == []
    assert evidence["count_one"]["unique_item_id"] is None
    assert evidence["common_facts"] == {}


def test_float32_midpoint_ties_require_exact_roundtrip_per_total() -> None:
    raw_hex = "edbe0b48"
    row = _row(struct.unpack("<f", bytes.fromhex(raw_hex))[0])
    row["result_raw_hex"] = raw_hex

    evidence = shape_average_value_evidence_from_action_rows(
        [row],
        current_session_id="5608:1",
        posterior_targets=[{"cells": 3, "count_exact": 128}],
        candidate_domains_by_cells={
            3: _domain([(52, 4, 143_099), (53, 6, 143_100)])
        },
    )[0]

    assert evidence["total_value_interval"] == [18_316_762, 18_316_762]
    assert evidence["reachable_total_values"] == [18_316_762]
    assert evidence["joint_support"] == [
        {
            "item_count": 128,
            "total_cells": 384,
            "total_value": 18_316_762,
            "quality_counts": {"q4": 38, "q6": 90},
            "quality_cells": {"q4": 114, "q6": 270},
            "quality_values": {"q4": 5_437_762, "q6": 12_879_000},
        }
    ]
    assert evidence["support_status"] == "ok"
    assert evidence["support_complete"] is True


def test_conflicting_duplicate_action_rows_are_rejected_atomically() -> None:
    def result(value: float) -> FatbeansActionResult:
        raw = struct.pack("<f", value)
        return FatbeansActionResult(
            action_id=100171,
            result=struct.unpack("<f", raw)[0],
            result_field=9,
            result_wire_type="float32",
            result_raw_hex=raw.hex(),
        )

    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=tuple(
            FatbeansStateEvent(
                sort_id=sort_id,
                capture_time=f"2026-08-09T00:00:{sort_id}+08:00",
                message_id=0x0027,
                session_id="5608:1",
                map_id=5608,
                round_index=2,
                action_results=(result(value),),
            )
            for sort_id, value in ((20, 100.0), (21, 200.0))
        ),
    )
    rows = _action_result_rows(events, {}, session_id="5608:1")
    evidence_rows = shape_average_value_evidence_from_action_rows(
        rows,
        current_session_id="5608:1",
        posterior_targets=[{"cells": 3, "count_exact": 1}],
        candidate_domains_by_cells={3: _domain([(55, 4, 100), (56, 6, 200)])},
    )

    assert [row["result"] for row in rows] == [200.0, 100.0]
    assert len(evidence_rows) == 2
    for evidence in evidence_rows:
        assert evidence["status"] == "unavailable"
        assert evidence["conflicts"] == ["duplicate_action_conflict"]
        assert evidence["support_status"] == "raw_evidence_unavailable"
        assert evidence["joint_support"] == []
        assert evidence["common_facts"] == {}

    contract = ui_contract_from_artifact(
        {
            "session_id": "5608:1",
            "action_result_rows": rows,
            "shape_average_value_evidence": evidence_rows,
            "v2_posterior_rows": [{"诊断": ""}],
        }
    )
    eval_fields = size_bucket_eval_fields(
        posterior_diagnostics="",
        action_result_rows=rows,
    )
    assert contract["diagnostics"]["size_bucket"]["reading_active"] is False
    assert contract["diagnostics"]["size_bucket"][
        "shape_average_value_evidence"
    ] == evidence_rows
    assert eval_fields["action_size_avg_tool_count"] == 0
    assert eval_fields["action_100172_used"] is False


def test_identical_shape_action_replay_keeps_only_latest_row() -> None:
    raw = struct.pack("<f", 100.0)
    result = FatbeansActionResult(
        action_id=100171,
        result=struct.unpack("<f", raw)[0],
        result_field=9,
        result_wire_type="float32",
        result_raw_hex=raw.hex(),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=tuple(
            FatbeansStateEvent(
                sort_id=sort_id,
                capture_time=f"2026-08-09T00:00:{sort_id}+08:00",
                message_id=0x0027,
                session_id="5608:1",
                map_id=5608,
                round_index=2,
                action_results=(result,),
            )
            for sort_id in (20, 21)
        ),
    )

    rows = _action_result_rows(events, {}, session_id="5608:1")

    assert len(rows) == 1
    assert rows[0]["sort"] == 21
    assert rows[0]["result"] == 100.0


@pytest.mark.parametrize(
    ("count_exact", "global_limits", "expected_status"),
    [
        (
            3,
            {"exact_total_item_count": 2},
            "count_exceeds_exact_total_item_count",
        ),
        (
            2,
            {"exact_warehouse_total_cells": 5},
            "footprint_cells_exceed_exact_warehouse_total_cells",
        ),
    ],
)
def test_exact_footprint_count_cannot_exceed_global_exact_bounds(
    count_exact: int,
    global_limits: dict[str, int],
    expected_status: str,
) -> None:
    evidence = shape_average_value_evidence_from_action_rows(
        [_row(100.0)],
        current_session_id="5608:1",
        posterior_targets=[{"cells": 3, "count_exact": count_exact}],
        candidate_domains_by_cells={3: _domain([(57, 4, 100)])},
        **global_limits,
    )[0]

    assert evidence["status"] == "conflict"
    assert evidence["support_status"] == expected_status
    assert evidence["conflicts"] == [expected_status]
    assert evidence["count_status"] == "conflict"
    assert evidence["count_value"] is None
    assert evidence["total_value_interval"] is None
    assert evidence["support_complete"] is False
    assert evidence["joint_support"] == []
    assert evidence["count_arms"] == []
    assert evidence["common_facts"] == {}
    assert evidence["quote_effect"] == "none_new_structured_evidence_only"


def test_strict_low_average_bypasses_legacy_signal_floor_for_joint_support() -> None:
    low_avg = 100.0
    assert low_avg < SIZE_AVG_VALUE_SIGNAL_FLOORS[3]
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=1,
            local_index=0,
            shape_key="3-cell",
            cells=3,
            sources=("action:100100",),
        )
    )
    store = builder.build()
    count_targets = size_bucket_count_targets_for_structured_evidence(
        store,
        footprint_cells=(3,),
        total_item_count=None,
        layout_footprint_count=1,
        trusted_footprint_count=1,
    )

    evidence = shape_average_value_evidence_from_action_rows(
        [_row(low_avg)],
        current_session_id="5608:1",
        posterior_targets=count_targets,
        candidate_domains_by_cells={3: _domain([(54, 4, 100)])},
    )[0]

    assert count_targets == (
        {
            "cells": 3,
            "count_provenance": ["semantic_full_outline"],
            "count_exact": 1,
        },
    )
    assert evidence["count_status"] == "exact"
    assert evidence["support_status"] == "ok"
    assert evidence["support_complete"] is True
    assert evidence["joint_support"] == [
        {
            "item_count": 1,
            "total_cells": 3,
            "total_value": 100,
            "quality_counts": {"q4": 1},
            "quality_cells": {"q4": 3},
            "quality_values": {"q4": 100},
        }
    ]
    assert evidence["quote_effect"] == "shadow"


def test_unknown_count_keeps_all_bounded_n_and_cross_quality_signatures() -> None:
    evidence = shape_average_value_evidence_from_action_rows(
        [_row(200.0)],
        current_session_id="5608:1",
        candidate_domains_by_cells={
            3: _domain([(61, 4, 100), (62, 5, 200), (63, 6, 300)])
        },
        exact_total_item_count=2,
    )[0]

    assert evidence["support_status"] == "ok"
    assert [arm["item_count"] for arm in evidence["count_arms"]] == [1, 2]
    assert evidence["common_facts"]["item_count_range"] == [1, 2]
    assert evidence["common_facts"]["item_count_floor"] == 1
    assert evidence["common_facts"]["total_cells_floor"] == 3
    assert evidence["common_facts"]["total_value_floor"] == 200
    assert evidence["common_facts"]["correlated_item_fact"] is None
    assert evidence["count_one"]["unique_item_id"] == 62
    signatures = {_signature_key(row) for row in evidence["joint_support"]}
    assert (1, 3, 200, (("q5", 1),), (("q5", 200),)) in signatures
    assert (2, 6, 400, (("q4", 1), ("q6", 1)), (("q4", 100), ("q6", 300))) in signatures
    assert (2, 6, 400, (("q5", 2),), (("q5", 400),)) in signatures
    assert evidence["active_constraints"] == []
    assert evidence["hard_eligible"] is False


def test_unknown_count_without_independent_ceiling_keeps_raw_interval_only() -> None:
    evidence = shape_average_value_evidence_from_action_rows(
        [_row(143_100.0)],
        current_session_id="5608:1",
        candidate_domains_by_cells={3: _domain([(71, 6, 143_100)])},
    )[0]

    assert evidence["status"] == "interval_only"
    assert evidence["support_status"] == "count_unknown_unbounded"
    assert evidence["joint_support"] == []
    assert evidence["count_one"]["unique_item_id"] is None
    assert evidence["common_facts"] == {}
    assert evidence["active_constraints"] == []


def test_exact_warehouse_cells_is_a_complete_count_ceiling() -> None:
    evidence = shape_average_value_evidence_from_action_rows(
        [_row(200.0)],
        current_session_id="5608:1",
        candidate_domains_by_cells={
            3: _domain([(74, 4, 100), (75, 5, 200), (76, 6, 300)])
        },
        exact_warehouse_total_cells=6,
    )[0]

    assert evidence["support_status"] == "ok"
    assert [arm["item_count"] for arm in evidence["count_arms"]] == [1, 2]
    assert evidence["common_facts"]["total_cells_range"] == [3, 6]


def test_catalog_failure_and_rollback_drop_all_support_but_keep_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = {
        "rows": [_row(143_100.0)],
        "current_session_id": "5608:1",
        "posterior_targets": [{"cells": 3, "count_exact": 1}],
        "candidate_domains_by_cells": {
            3: _domain([(81, 6, 143_100)])
        },
    }
    default = shape_average_value_evidence_from_action_rows(**kwargs)[0]
    monkeypatch.setenv(SHAPE_AVG_EXACT_CONSTRAINT_DISABLE, "1")
    rollback = shape_average_value_evidence_from_action_rows(**kwargs)[0]

    assert default["support_status"] == "ok"
    assert rollback["support_status"] == "rollback_disabled"
    assert rollback["status"] == "interval_only"
    assert rollback["avg_interval"] == default["avg_interval"]
    assert rollback["raw_bits_hex"] == default["raw_bits_hex"]
    assert rollback["joint_support"] == []
    assert rollback["active_constraints"] == []
    assert rollback["quote_effect"] == "none_new_structured_evidence_only"

    monkeypatch.delenv(SHAPE_AVG_EXACT_CONSTRAINT_DISABLE)
    failed = shape_average_value_evidence_from_action_rows(
        [_row(143_100.0)],
        current_session_id="5608:1",
        posterior_targets=[{"cells": 3, "count_exact": 1}],
        candidate_domains_by_cells={
            3: _domain([], status="catalog_generation_mismatch")
        },
    )[0]
    assert failed["support_status"] == "catalog_generation_mismatch"
    assert failed["joint_support"] == []
    assert failed["active_constraints"] == []

    generation_mismatch = shape_average_value_evidence_from_action_rows(
        [_row(143_100.0)],
        current_session_id="5608:1",
        posterior_targets=[{"cells": 3, "count_exact": 1}],
        candidate_domains_by_cells={3: _domain([(82, 6, 143_100)])},
        mandatory_units_by_cells={
            3: [
                {
                    "identity": "runtime:82",
                    "candidate_item_ids": [82],
                    "catalog_generation": "different-generation",
                }
            ]
        },
    )[0]
    assert generation_mismatch["support_status"] == "catalog_generation_mismatch"
    assert generation_mismatch["joint_support"] == []

    identity_mismatch = shape_average_value_evidence_from_action_rows(
        [_row(143_100.0)],
        current_session_id="5608:1",
        posterior_targets=[{"cells": 3, "count_exact": 1}],
        candidate_domains_by_cells={3: _domain([(83, 6, 143_100)])},
        mandatory_units_by_cells={
            3: [
                {
                    "identity": "runtime:83",
                    "candidate_item_ids": [83],
                    "expected_quality": 6,
                    "expected_cells": 3,
                    "expected_value": 143_101,
                }
            ]
        },
    )[0]
    assert identity_mismatch["support_status"] == (
        "mandatory_catalog_identity_mismatch"
    )
    assert identity_mismatch["joint_support"] == []


def test_physical_units_are_identity_deduped_and_exact_anchor_wins() -> None:
    problem = SimpleNamespace(
        physical_candidate_domains=(
            SimpleNamespace(
                hard_eligible=True,
                physical_support_eligible=True,
                catalog_status="ok",
                catalog_generation="test-generation",
                cells=3,
                key="runtime:900",
                candidate_item_ids=(91, 92),
            ),
            SimpleNamespace(
                hard_eligible=False,
                physical_support_eligible=True,
                catalog_status="ok",
                catalog_generation="test-generation",
                cells=3,
                key="runtime:901",
                candidate_item_ids=(93, 94),
            ),
        ),
        anchors=(
            SimpleNamespace(
                cells=3,
                key="runtime:900",
                item_id=92,
                quality=6,
                value=143_100,
            ),
        ),
    )

    assert _shape_average_mandatory_units_by_cells(problem) == {
        3: (
            {
                "identity": "runtime:900",
                "candidate_item_ids": [92],
                "expected_quality": 6,
                "expected_cells": 3,
                "expected_value": 143_100,
            },
            {
                "identity": "runtime:901",
                "candidate_item_ids": [93, 94],
                "catalog_generation": "test-generation",
            },
        )
    }


def test_ui_contract_uses_root_evidence_without_frontend_recalculation() -> None:
    root_evidence = shape_average_value_evidence_from_action_rows(
        [_row(143_100.0)],
        current_session_id="5608:1",
        posterior_targets=[{"cells": 3, "count_exact": 1}],
        candidate_domains_by_cells={3: _domain([(101, 6, 143_100)])},
    )
    contract = ui_contract_from_artifact(
        {
            "session_id": "5608:1",
            "shape_average_value_evidence": root_evidence,
            # This incompatible row would produce a different answer if the UI
            # recomputed instead of consuming the root engine view-model.
            "action_result_rows": [_row(999.0)],
            "v2_posterior_rows": [{"诊断": ""}],
        }
    )

    projected = contract["diagnostics"]["size_bucket"][
        "shape_average_value_evidence"
    ]
    assert projected == root_evidence
    assert projected[0]["support_status"] == "ok"
    assert projected[0]["count_one"]["unique_item_id"] == 101


def test_non_shape_value_semantics_do_not_enter_sav2() -> None:
    rows = [
        {**_row(143_100.0), "action_id": 100163},
        {**_row(143_100.0), "action_id": 100174},
        {**_row(143_100.0), "action_id": 200038},
    ]

    assert shape_average_value_evidence_from_action_rows(
        rows,
        current_session_id="5608:1",
    ) == ()


def test_fresh_process_rollback_changes_only_shadow_support() -> None:
    script = textwrap.dedent(
        """
        from dataclasses import asdict
        import json
        import struct
        from types import SimpleNamespace

        from bidking_lab.inference.bid_strategy import recommend_bid_strategy
        from bidking_lab.inference.size_avg_evidence import (
            shape_average_value_evidence_from_action_rows,
        )

        raw = struct.pack("<f", 143100.0)
        row = {
            "action_id": 100171,
            "result": struct.unpack("<f", raw)[0],
            "result_field": 9,
            "result_wire_type": "float32",
            "result_raw_hex": raw.hex(),
            "session_id": "5608:1",
            "sort": 20,
        }
        domain = {
            "map_id": 5608,
            "footprint_cells": 3,
            "catalog_status": "ok",
            "catalog_generation": "fresh-process-test",
            "candidates": [
                {"item_id": 201, "quality": 6, "value": 143100, "cells": 3}
            ],
        }
        evidence = shape_average_value_evidence_from_action_rows(
            [row],
            current_session_id="5608:1",
            posterior_targets=[{"cells": 3, "count_exact": 1}],
            candidate_domains_by_cells={3: domain},
        )[0]
        quotes = {
            str(samples): asdict(recommend_bid_strategy(
                latest_bids={"npc": 120000},
                value_summary=SimpleNamespace(
                    p10=300000,
                    p50=400000,
                    p90=500000,
                ),
                evidence_label="unchanged-posterior",
                posterior_samples=samples,
                round_no=2,
            ))
            for samples in (8000, 50000)
        }
        print(json.dumps({
            "raw": {
                key: evidence[key]
                for key in (
                    "action_id", "footprint_cells", "raw_bits_hex", "avg_interval",
                    "count_value", "count_status", "total_value_interval",
                )
            },
            "support_status": evidence["support_status"],
            "joint_support": evidence["joint_support"],
            "formal_posterior": {
                "count": [12, 18, 24],
                "cells": [80, 100, 120],
                "value": [300000, 400000, 500000],
            },
            "quotes": quotes,
        }, sort_keys=True))
        """
    )

    def run(disabled: bool) -> dict[str, object]:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            (str(ROOT / "src"), str(ROOT / "hero_ref" / "src"))
        )
        if disabled:
            env[SHAPE_AVG_EXACT_CONSTRAINT_DISABLE] = "1"
        else:
            env.pop(SHAPE_AVG_EXACT_CONSTRAINT_DISABLE, None)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    default = run(False)
    rollback = run(True)

    assert default["support_status"] == "ok"
    assert default["joint_support"]
    assert rollback["support_status"] == "rollback_disabled"
    assert rollback["joint_support"] == []
    assert rollback["raw"] == default["raw"]
    assert rollback["formal_posterior"] == default["formal_posterior"]
    assert rollback["quotes"] == default["quotes"]
    assert default["quotes"]["8000"] == default["quotes"]["50000"]


def test_new_solver_is_listed_in_client_protection_manifest() -> None:
    manifest = json.loads(
        (ROOT / "scripts" / "client_protection_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    inference_tree = next(
        tree
        for tree in manifest["critical_native"]["package_trees"]
        if tree["path"] == "bidking_lab/inference"
    )
    assert (
        "bidking_lab/inference/mean_multiset_support.py"
        in inference_tree["protected_modules"]
    )
