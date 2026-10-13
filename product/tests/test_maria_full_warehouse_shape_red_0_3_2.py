import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_ref_engine import extract_evidence, run_reference_engine  # noqa: E402
from hero_ref.tools.ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from hero_ref.tools.ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402


SESSION_ID = "maria-shape-contract:1"


def _snapshot(
    *,
    hero: str = "maria",
    phase: str = "bidding",
    shape_code: int = 54,
    include_target_in_low_scan: bool = False,
    action_source: str = "root",
    skill_source: str = "rows",
    qualification: str = "official_random_warehouse",
) -> dict[str, Any]:
    full_items = [
        {"runtime_id": 101, "local_index": 1, "shape_code": shape_code},
        {"runtime_id": 102, "local_index": 22, "shape_code": 11},
        {"runtime_id": 103, "local_index": 41, "shape_code": 12},
        {"runtime_id": 104, "local_index": 51, "shape_code": 11},
        {"runtime_id": 105, "local_index": 61, "shape_code": 11},
        {"runtime_id": 106, "local_index": 71, "shape_code": 11},
    ]
    low_items = [
        {"runtime_id": 102, "quality": 1},
        {"runtime_id": 103, "quality": 3},
    ]
    if include_target_in_low_scan:
        low_items.append({"runtime_id": 101, "quality": 3})
    action_row = {
        "sort": 7,
        "action_id": 100100,
        "session_id": SESSION_ID,
        "source_kind": "direct_action_response",
        "revealed_items": len(full_items),
        "revealed_items_detail": full_items,
    }
    skill_row = {
        "sort": 3,
        "hero_id": 108,
        "skill_id": 10010801,
        "revealed_items": len(low_items),
        "observed_items": low_items,
    }
    snapshot: dict[str, Any] = {
        "session_id": SESSION_ID,
        "hero": hero,
        "phase": phase,
        "structured_ref_inputs": {
            "total_count": 6,
            "total_cells": 26 if shape_code in (45, 54) else 14,
        },
        "ui_contract": {
            "context": {
                "session_id": SESSION_ID,
                "hero": hero,
                "hero_id": 108 if hero == "maria" else 107,
                "map_id": 5606,
                "phase": phase,
                "action_round": 2,
                "warehouse_source_qualification": qualification,
            },
            "constraints": {"public_info": {}},
            "actions": {"results": []},
        },
        "action_result_rows": [],
        "skill_reveal_rows": [],
        "skill_reveals": [],
    }
    if action_source == "root":
        snapshot["action_result_rows"] = [action_row]
    elif action_source == "ui":
        snapshot["ui_contract"]["actions"]["results"] = [action_row]
    else:  # pragma: no cover - test helper guard
        raise AssertionError(action_source)
    if skill_source == "rows":
        snapshot["skill_reveal_rows"] = [skill_row]
    elif skill_source == "alias":
        snapshot["skill_reveals"] = [skill_row]
    else:  # pragma: no cover - test helper guard
        raise AssertionError(skill_source)
    return snapshot


@pytest.fixture(autouse=True)
def _validated_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine,
        "_maria_validated_5x4_catalog_qualities",
        lambda: (frozenset({3, 6}), "ok"),
    )
    monkeypatch.setattr(
        engine,
        "_catalog_min_value_by_quality_and_cells",
        lambda: {(6, 20): 519_800},
    )
    monkeypatch.setattr(
        engine,
        "_catalog_cell_sizes_by_table_quality",
        lambda: {
            1: (1, 2),
            2: (1, 2),
            3: (1, 2),
            4: (1, 2, 4),
            5: (1, 2, 3),
            6: (1, 2, 20),
        },
    )


@pytest.mark.parametrize("shape_code", [45, 54])
@pytest.mark.parametrize("action_source", ["root", "ui"])
@pytest.mark.parametrize("skill_source", ["rows", "alias"])
def test_maria_complete_full_warehouse_5x4_complement_is_hard_red_evidence(
    shape_code: int,
    action_source: str,
    skill_source: str,
) -> None:
    evidence = extract_evidence(
        _snapshot(
            shape_code=shape_code,
            action_source=action_source,
            skill_source=skill_source,
        )
    )

    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors["q6"] == 20.0
    assert evidence.quality_cell_floor_item_counts["q6"] == 1
    assert evidence.quality_catalog_min_value_floors["q6"] == 519_800.0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}:count:1:shape:4x5"
        in evidence.source_notes
    )
    assert any(
        note.startswith(engine.KNOWN_Q6_FOOTPRINT_VALUE_FLOOR_NOTE)
        for note in evidence.source_notes
    )


def test_maria_shape_red_inference_has_exact_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = extract_evidence(_snapshot())
    monkeypatch.setenv(engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_DISABLE, "1")
    rollback = extract_evidence(_snapshot())

    assert default.min_counts["q6"] == 1
    assert default.quality_cell_floors["q6"] == 20.0
    assert rollback.min_counts.get("q6") == 0
    assert rollback.quality_cell_floors.get("q6") is None
    assert rollback.quality_catalog_min_value_floors.get("q6") is None
    assert not any(
        engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE in note
        for note in rollback.source_notes
    )


@pytest.mark.parametrize(
    "qualification",
    [
        "official_random_warehouse",
        "postupdate_unknown_warehouse_source",
        "player_custom_verified",
    ],
)
def test_direct_physical_evidence_is_stable_across_warehouse_source_pools(
    qualification: str,
) -> None:
    evidence = extract_evidence(_snapshot(qualification=qualification))

    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors["q6"] == 20.0


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_kill_switch_payload_matches_pre_feature_path_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    monkeypatch.setenv(engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_DISABLE, "1")
    rollback = run_reference_engine(_snapshot(), max_combos=max_combos).as_dict()
    monkeypatch.delenv(engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_DISABLE)
    monkeypatch.setattr(
        engine,
        "_maria_full_warehouse_shape_red_items",
        lambda _snapshot, **_kwargs: ((), "disabled"),
    )
    legacy_equivalent = run_reference_engine(
        _snapshot(), max_combos=max_combos
    ).as_dict()

    assert json.dumps(rollback, sort_keys=True, ensure_ascii=False) == json.dumps(
        legacy_equivalent,
        sort_keys=True,
        ensure_ascii=False,
    )


def test_same_runtime_direct_red_fact_is_not_double_counted() -> None:
    snapshot = _snapshot()
    target = snapshot["action_result_rows"][0]["revealed_items_detail"][0]
    target.update(
        {
            "quality": 6,
            "item_id": 1916038,
            "value": 843_200,
        }
    )

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors["q6"] == 20.0
    assert evidence.quality_cell_floor_item_counts["q6"] == 1
    assert evidence.quality_value_floors["q6"] == 843_200.0
    assert evidence.quality_value_floor_item_counts["q6"] == 1
    assert evidence.quality_catalog_min_value_floors.get("q6") is None


def test_full_outline_count_equal_to_exact_total_is_positive_control() -> None:
    snapshot = _snapshot()

    evidence = extract_evidence(snapshot)

    assert snapshot["structured_ref_inputs"]["total_count"] == len(
        snapshot["action_result_rows"][0]["revealed_items_detail"]
    )
    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors["q6"] == 20.0


@pytest.mark.parametrize(
    "coercion_fault",
    [
        "skill_runtime_string",
        "skill_quality_string",
        "outline_shape_float",
        "action_id_float",
        "action_sort_float",
        "action_revealed_count_float",
    ],
)
def test_maria_field_specific_contract_rejects_integral_coercions(
    coercion_fault: str,
) -> None:
    snapshot = _snapshot()
    action = snapshot["action_result_rows"][0]
    skill = snapshot["skill_reveal_rows"][0]
    if coercion_fault == "skill_runtime_string":
        skill["observed_items"][0]["runtime_id"] = "102"
    elif coercion_fault == "skill_quality_string":
        skill["observed_items"][0]["quality"] = "1"
    elif coercion_fault == "outline_shape_float":
        action["revealed_items_detail"][0]["shape_code"] = 54.0
    elif coercion_fault == "action_id_float":
        action["action_id"] = 100100.0
    elif coercion_fault == "action_sort_float":
        action["sort"] = 7.0
    elif coercion_fault == "action_revealed_count_float":
        action["revealed_items"] = 6.0
    else:  # pragma: no cover - parameter guard
        raise AssertionError(coercion_fault)

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert evidence.quality_cell_floors.get("q6") is None
    assert not any(
        note.startswith(f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}:count:")
        for note in evidence.source_notes
    )


def test_canonical_decimal_wire_fields_remain_accepted() -> None:
    snapshot = _snapshot()
    action = snapshot["action_result_rows"][0]
    action.update({"action_id": "100100", "sort": "7", "revealed_items": "6"})
    action["revealed_items_detail"][0]["shape_code"] = "54"
    skill = snapshot["skill_reveal_rows"][0]
    skill.update(
        {
            "hero_id": "108",
            "skill_id": "10010801",
            "sort": "3",
            "revealed_items": "2",
        }
    )

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors["q6"] == 20.0


@pytest.mark.parametrize(
    "noncanonical_action_id",
    [True, " 100100", "+100100", "-100100", "0100100"],
)
def test_noncanonical_decimal_action_id_is_rejected(
    noncanonical_action_id: object,
) -> None:
    snapshot = _snapshot()
    snapshot["action_result_rows"][0]["action_id"] = noncanonical_action_id

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert evidence.quality_cell_floors.get("q6") is None


MARIA_DECIMAL_FIELDS = (
    "action_id",
    "hero_id",
    "skill_id",
    "action_sort",
    "skill_sort",
    "action_revealed_count",
    "skill_revealed_count",
    "shape",
)


def _set_maria_decimal_field(snapshot: dict[str, Any], field: str, value: str) -> None:
    action = snapshot["action_result_rows"][0]
    skill = snapshot["skill_reveal_rows"][0]
    if field == "action_id":
        action["action_id"] = value
    elif field == "hero_id":
        skill["hero_id"] = value
    elif field == "skill_id":
        skill["skill_id"] = value
    elif field == "action_sort":
        action["sort"] = value
    elif field == "skill_sort":
        skill["sort"] = value
    elif field == "action_revealed_count":
        action["revealed_items"] = value
    elif field == "skill_revealed_count":
        skill["revealed_items"] = value
    elif field == "shape":
        action["revealed_items_detail"][0]["shape_code"] = value
    else:  # pragma: no cover - parameter guard
        raise AssertionError(field)


@pytest.mark.parametrize("field", MARIA_DECIMAL_FIELDS)
@pytest.mark.parametrize("digit_count", [4_300, 4_301])
def test_extreme_ascii_decimal_field_is_bounded_before_integer_conversion(
    field: str,
    digit_count: int,
) -> None:
    snapshot = _snapshot()
    _set_maria_decimal_field(snapshot, field, "9" * digit_count)

    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.min_counts.get("q6") in (None, 0)
    assert evidence.quality_cell_floors.get("q6") is None
    assert result["evidence"]["min_counts"].get("q6") in (None, 0)
    assert result["evidence"]["quality_cell_floors"].get("q6") is None


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("action_id", str(engine.MARIA_PROTOBUF_UINT64_MAX + 1)),
        ("hero_id", str(engine.MARIA_PROTOBUF_UINT64_MAX + 1)),
        ("skill_id", str(engine.MARIA_PROTOBUF_UINT64_MAX + 1)),
        ("action_sort", str(engine.MARIA_PROTOBUF_UINT64_MAX + 1)),
        ("skill_sort", str(engine.MARIA_PROTOBUF_UINT64_MAX + 1)),
        ("action_revealed_count", str(engine.MARIA_REVEALED_ITEMS_MAX + 1)),
        ("skill_revealed_count", str(engine.MARIA_REVEALED_ITEMS_MAX + 1)),
        ("shape", str(engine.MARIA_SHAPE_CODE_MAX + 1)),
    ],
)
def test_each_maria_decimal_field_rejects_its_protocol_overflow_without_raising(
    field: str,
    invalid_value: str,
) -> None:
    snapshot = _snapshot()
    _set_maria_decimal_field(snapshot, field, invalid_value)

    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.min_counts.get("q6") in (None, 0)
    assert evidence.quality_cell_floors.get("q6") is None
    assert result["evidence"]["min_counts"].get("q6") in (None, 0)
    assert result["evidence"]["quality_cell_floors"].get("q6") is None


@pytest.mark.parametrize(
    "invalid_value",
    ["0" * 4_301, "0" * 4_300 + "1"],
)
@pytest.mark.parametrize("field", MARIA_DECIMAL_FIELDS)
def test_extreme_zero_and_leading_zero_strings_are_bounded_noops(
    field: str,
    invalid_value: str,
) -> None:
    snapshot = _snapshot()
    _set_maria_decimal_field(snapshot, field, invalid_value)

    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.min_counts.get("q6") in (None, 0)
    assert evidence.quality_cell_floors.get("q6") is None
    assert result["evidence"]["min_counts"].get("q6") in (None, 0)
    assert result["evidence"]["quality_cell_floors"].get("q6") is None


def test_maria_decimal_authoritative_maxima_are_accepted_without_narrowing() -> None:
    uint64_max = engine.MARIA_PROTOBUF_UINT64_MAX

    assert engine._maria_decimal_int(
        uint64_max,
        maximum=uint64_max,
    ) == uint64_max
    assert engine._maria_decimal_int(
        str(uint64_max),
        maximum=uint64_max,
    ) == uint64_max
    assert engine._maria_decimal_int(
        str(engine.MARIA_REVEALED_ITEMS_MAX),
        maximum=engine.MARIA_REVEALED_ITEMS_MAX,
    ) == engine.MARIA_REVEALED_ITEMS_MAX
    assert engine._maria_shape(str(engine.MARIA_SHAPE_CODE_MAX)) == (9, 9)


def test_complete_empty_low_quality_marker_is_valid_zero_scan() -> None:
    snapshot = _snapshot()
    snapshot["skill_reveal_rows"][0].update(
        {
            "sort": "3",
            "revealed_items": "0",
            "observed_items": [],
        }
    )

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors["q6"] == 20.0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}:count:1:shape:4x5"
        in evidence.source_notes
    )


def test_missing_exact_total_is_byte_stable_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"].pop("total_count")

    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert json.dumps(default, sort_keys=True, ensure_ascii=False) == json.dumps(
        rollback,
        sort_keys=True,
        ensure_ascii=False,
    )


@pytest.mark.parametrize("outline_delta", [-1, 1])
def test_partial_or_extra_full_outline_cannot_drive_complement(
    outline_delta: int,
) -> None:
    snapshot = _snapshot()
    items = snapshot["action_result_rows"][0]["revealed_items_detail"]
    if outline_delta < 0:
        items.pop()
    else:
        items.append(
            {
                "runtime_id": 107,
                "local_index": 81,
                "shape_code": 11,
            }
        )
    snapshot["action_result_rows"][0]["revealed_items"] = len(items)

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert evidence.quality_cell_floors.get("q6") is None
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "full_warehouse_total_count_mismatch"
    ) in evidence.source_notes


@pytest.mark.parametrize("identity_fault", ["duplicate", "missing"])
def test_duplicate_or_missing_full_outline_identity_fails_closed(
    identity_fault: str,
) -> None:
    snapshot = _snapshot()
    items = snapshot["action_result_rows"][0]["revealed_items_detail"]
    if identity_fault == "duplicate":
        items[1]["runtime_id"] = items[0]["runtime_id"]
    else:
        items[0].pop("runtime_id")

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert evidence.quality_cell_floors.get("q6") is None
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "malformed_full_warehouse"
    ) in evidence.source_notes


def test_explicit_hard_zero_red_count_suppresses_shape_complement() -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["fixed_counts"] = {"q6": 0}

    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert result["status"] != "no_reachable_combo"
    assert result["evidence"]["fixed_counts"]["q6"] == 0
    assert result["evidence"]["min_counts"].get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "hard_q6_count_conflict"
    ) in result["notes"]


def test_explicit_hard_red_cells_below_shape_suppresses_complement() -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["quality_cells"] = {"q6": 0}

    evidence = extract_evidence(snapshot)

    assert evidence.quality_cells["q6"] == 0
    assert evidence.min_counts.get("q6") == 0
    assert evidence.quality_cell_floors.get("q6") is None
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "hard_q6_cells_conflict"
    ) in evidence.source_notes


def test_explicit_hard_red_value_below_catalog_floor_suppresses_complement() -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["quality_values"] = {"q6": 100_000}

    evidence = extract_evidence(snapshot)

    assert evidence.quality_values["q6"] == 100_000
    assert evidence.min_counts.get("q6") == 0
    assert evidence.quality_catalog_min_value_floors.get("q6") is None
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "hard_q6_value_conflict"
    ) in evidence.source_notes


def test_direct_same_runtime_non_red_identity_suppresses_complement() -> None:
    snapshot = _snapshot()
    snapshot["action_result_rows"].append(
        {
            "action_id": 100999,
            "revealed_items_detail": [
                {"runtime_id": 101, "quality": 4, "cells": 20}
            ],
        }
    )

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert evidence.quality_cell_floors.get("q6") is None
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "direct_runtime_quality_conflict"
    ) in evidence.source_notes


def test_direct_same_runtime_red_cells_conflict_does_not_add_twenty_cell_floor() -> None:
    snapshot = _snapshot()
    snapshot["action_result_rows"].append(
        {
            "action_id": 100999,
            "revealed_items_detail": [
                {"runtime_id": 101, "quality": 6, "cells": 19}
            ],
        }
    )

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors["q6"] == 19.0
    assert evidence.quality_catalog_min_value_floors.get("q6") is None
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "direct_runtime_q6_cells_conflict"
    ) in evidence.source_notes


def test_blue_5x4_visible_to_maria_is_not_inferred_red() -> None:
    evidence = extract_evidence(_snapshot(include_target_in_low_scan=True))

    assert evidence.min_counts.get("q6") in (None, 0)
    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.min_counts["q3"] == 2
    assert not any(
        note.startswith(engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE)
        for note in evidence.source_notes
    )


@pytest.mark.parametrize(
    ("hero", "phase"),
    [("aisha", "bidding"), ("maria", "reading"), ("maria", "settled")],
)
def test_maria_shape_red_inference_is_hero_and_phase_scoped(
    hero: str,
    phase: str,
) -> None:
    evidence = extract_evidence(_snapshot(hero=hero, phase=phase))

    assert evidence.min_counts.get("q6") in (None, 0)
    assert evidence.quality_cell_floors.get("q6") is None


def test_stale_full_warehouse_session_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot["action_result_rows"][0]["session_id"] = "other-session"

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "malformed_full_warehouse"
    ) in evidence.source_notes


def test_stale_maria_quality_session_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot["skill_reveal_rows"][0]["session_id"] = "other-session"

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "malformed_maria_quality_markers"
    ) in evidence.source_notes


def test_partial_maria_quality_row_without_complete_count_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot["skill_reveal_rows"][0].pop("revealed_items")

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "unproven_maria_quality_marker_completeness"
    ) in evidence.source_notes


def test_conflicting_latest_full_warehouse_rows_fail_closed() -> None:
    snapshot = _snapshot()
    conflicting = copy.deepcopy(snapshot["action_result_rows"][0])
    conflicting["revealed_items_detail"][0]["shape_code"] = 44
    snapshot["action_result_rows"].append(conflicting)

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "conflicting_full_warehouse"
    ) in evidence.source_notes


def test_conflicting_latest_full_warehouse_quality_fails_closed() -> None:
    snapshot = _snapshot()
    conflicting = copy.deepcopy(snapshot["action_result_rows"][0])
    conflicting["revealed_items_detail"][0]["quality"] = 6
    snapshot["action_result_rows"].append(conflicting)

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "conflicting_full_warehouse"
    ) in evidence.source_notes


def test_malformed_shape_cells_conflict_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["cells"] = 19

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "malformed_full_warehouse"
    ) in evidence.source_notes


def test_malformed_explicit_quality_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["quality"] = "red"

    evidence = extract_evidence(snapshot)

    assert evidence.min_counts.get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "malformed_full_warehouse"
    ) in evidence.source_notes


def test_catalog_with_any_third_quality_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_maria_validated_5x4_catalog_qualities",
        lambda: (frozenset({3, 5, 6}), "ok"),
    )

    evidence = extract_evidence(_snapshot())

    assert evidence.min_counts.get("q6") == 0
    assert (
        f"{engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE}_suppressed:"
        "catalog_not_blue_red_only"
    ) in evidence.source_notes


def test_non_5x4_outline_is_unchanged() -> None:
    snapshot = _snapshot(shape_code=24)
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default["evidence"]["min_counts"].get("q6") == 0
    assert not any(
        note.startswith(engine.MARIA_FULL_WAREHOUSE_SHAPE_RED_NOTE)
        for note in default["notes"]
    )


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_engine_support_lock_and_quote_share_the_inferred_red_floor(
    max_combos: int,
) -> None:
    result = run_reference_engine(_snapshot(), max_combos=max_combos).as_dict()

    assert result["evidence"]["min_counts"]["q6"] == 1
    assert result["quality_count_ranges"]["q6"][0] >= 1
    assert result["quality_cells_ranges"]["q6"][0] >= 20
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "partial_reveal_min",
    }
    assert result["red_value_range"][0] >= 519_800
    assert result["value_p25"] >= 519_800
    assert result["value_p50"] >= 519_800
    assert result["value_p75"] >= 519_800


def test_panel_and_tk_diagnostic_adapter_consume_the_same_red_floor() -> None:
    snapshot_path = Path("synthetic-maria-full-warehouse-shape.json")
    summary = summarize_snapshot(_snapshot(), snapshot_path=snapshot_path)
    diagnostic = _summary_diagnostic_row(
        summary,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )

    assert summary["red"]["count_range"].startswith("1 / ")
    assert summary["red"]["cells_range"].startswith("20 / ")
    assert summary["red"]["value_range"].startswith("519,800 / ")
    assert diagnostic["red"]["count_range"] == summary["red"]["count_range"]
    assert diagnostic["red"]["cells_range"] == summary["red"]["cells_range"]
    assert diagnostic["red"]["value_range"] == summary["red"]["value_range"]
    assert diagnostic["ref_v0"]["evidence"]["min_counts"]["q6"] == 1
