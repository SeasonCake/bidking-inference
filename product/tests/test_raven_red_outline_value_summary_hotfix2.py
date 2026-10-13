"""Raven R5 complete-outline red floor/P50/P90 summary (hotfix2 RF1)."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
    ROOT / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    RAVEN_RED_OUTLINE_VALUE_SUMMARY_DISABLE,
    run_reference_engine,
)
from ahmad_tk_overlay import AhmadTkOverlay  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import (  # noqa: E402
    _action_result_rows,
    _ahmad_ref_inputs_from_batches,
    _public_info_rows,
    _skill_reveal_rows,
    _skill_reveals_for_ref,
    load_monitor_tables,
)
from bidking_lab.runtime.snapshot import _ui_public_numeric_contract  # noqa: E402
from bidking_lab.live.warehouse_source import (  # noqa: E402
    classify_from_map_table,
    warehouse_generation_from_capture_time,
)


REAL_CASES = (
    (
        "fatbeans_valid_raven_2501_5rounds_2501_1683352345697832_7832.json",
        2501,
        683_896,
        [1, 1, 1, 1, 4, 16],
    ),
    (
        "fatbeans_valid_raven_5601_5rounds_5601_1741283127441662_1662.json",
        5601,
        322_896,
        [1, 1, 1, 1, 4],
    ),
    (
        "fatbeans_valid_raven_5602_5rounds_5602_1741283128377413_7413.json",
        5602,
        519_192,
        [4, 4, 15],
    ),
    (
        "fatbeans_valid_raven_5610_5rounds_5610_1741283127296058_6058.json",
        5610,
        444_696,
        [1, 1, 4, 9],
    ),
    (
        "fatbeans_valid_raven_5610_5rounds_5610_1741283128047769_7769.json",
        5610,
        258_276,
        [1, 2, 4],
    ),
)


def _quality_items(*qualities: int) -> list[dict[str, int]]:
    return [
        {"runtime_id": index, "local_index": index, "quality": quality}
        for index, quality in enumerate(qualities, 1)
    ]


def _outline_items(*shapes: int) -> list[dict[str, int]]:
    return [
        {"runtime_id": index, "local_index": index, "shape_code": shape}
        for index, shape in enumerate(shapes, 1)
    ]


def _snapshot(
    *,
    hero: str = "raven",
    phase: str = "bidding",
    round_no: int = 5,
    map_id: int = 5602,
    qualities: tuple[int, ...] = (1, 3, 4, 5, 6, 6),
    shapes: tuple[int, ...] = (11, 12, 13, 14, 22, 23),
) -> dict[str, Any]:
    source = {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
    }
    quality_items = _quality_items(*qualities)
    outline_items = _outline_items(*shapes)
    action_row = {
        "action_id": 100100,
        "sort": 20,
        "revealed_items_detail": outline_items,
    }
    skill_row = {
        "skill_id": 100301,
        "hero_id": 301,
        "sort": 21,
        "observed_items": quality_items,
    }
    return {
        "created_at": 0,
        "hero": hero,
        "map_id": map_id,
        "round": round_no,
        "phase": phase,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": True,
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": map_id,
                "round": round_no,
                "phase": phase,
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": source["qualification"],
                "official_distribution_eligible": True,
            },
            "constraints": {"public_info": {}},
            "actions": {"results": [copy.deepcopy(action_row)]},
        },
        "action_result_rows": [action_row],
        "skill_reveal_rows": [copy.deepcopy(skill_row)],
        "skill_reveals": [skill_row],
    }


def _real_snapshot(sample_name: str, map_id: int) -> dict[str, Any]:
    sample = ROOT / "data" / "samples" / "fatbeans" / sample_name
    events = parse_fatbeans_capture(sample)
    batches = [
        batch
        for batch in live_batches_from_fatbeans_events(events)
        if batch.phase != "settled"
    ]
    tables = load_monitor_tables()
    action_rows = _action_result_rows(events, tables.items)
    skill_rows = _skill_reveal_rows(events, tables.items)
    public_rows = _public_info_rows(events, tables.items)
    capture_times = [
        state.capture_time
        for state in events.states
        if state.capture_time is not None
    ]
    generation = warehouse_generation_from_capture_time(
        max(capture_times) if capture_times else None
    )
    source = classify_from_map_table(
        map_id,
        tables.maps,
        source_generation=generation,
    ).as_dict()
    return {
        "hero": "raven",
        "map_id": map_id,
        "round": 5,
        "phase": "bidding",
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
        "ui_contract": {
            "context": {
                "hero": "raven",
                "map_id": map_id,
                "round": 5,
                "phase": "bidding",
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": source["qualification"],
                "official_distribution_eligible": source[
                    "official_distribution_eligible"
                ],
            },
            "constraints": {
                "public_info": _ui_public_numeric_contract(public_rows),
            },
            "actions": {"results": copy.deepcopy(action_rows)},
        },
        "structured_ref_inputs": (
            _ahmad_ref_inputs_from_batches(
                batches,
                hero="raven",
                items=tables.items,
            )
            or {}
        ),
        "public_info_rows": public_rows,
        "action_result_rows": action_rows,
        "skill_reveal_rows": skill_rows,
        "skill_reveals": _skill_reveals_for_ref(skill_rows),
    }


def _summary(snapshot: dict[str, Any], *, max_combos: int = 8_000) -> dict[str, Any]:
    return run_reference_engine(snapshot, max_combos=max_combos).as_dict()


def _without_additive_summary(result: dict[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(result)
    stable.pop("red_outline_value_summary", None)
    return stable


def test_complete_runtime_pairing_publishes_display_only_summary() -> None:
    result = _summary(_snapshot())
    summary = result["red_outline_value_summary"]

    assert summary == {
        "status": "outline_complete",
        "eligibility_complete": True,
        "floor_kind": "per_item_footprint_catalog_min",
        "floor_value": 233_496,
        "p50_value": 442_015,
        "p90_value": 671_002,
        "quality_scan_count": 6,
        "outline_count": 6,
        "red_count": 2,
        "paired_red_count": 2,
        "footprint_cells": [4, 6],
        "catalog_context": (
            "drop_weights_all_maps_0627:5602;"
            "items_droppable:q6-validated;"
            "red_longtail_normalized_0627:validated;"
            "sampling:with_replacement"
        ),
        "source_ids": [100100, 100301],
        "conflicts": [],
    }
    assert summary["floor_value"] <= summary["p50_value"] <= summary["p90_value"]


def test_default_and_rollback_only_change_additive_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    default = _summary(snapshot)
    monkeypatch.setenv(RAVEN_RED_OUTLINE_VALUE_SUMMARY_DISABLE, "1")
    rollback = _summary(snapshot)

    assert _without_additive_summary(default) == _without_additive_summary(rollback)
    assert default["red_outline_value_summary"]["status"] == "outline_complete"
    assert rollback["red_outline_value_summary"]["status"] == "unavailable"
    assert rollback["red_outline_value_summary"]["conflicts"] == ["disabled"]


def test_product_and_audit_combo_budgets_are_identical_on_complete_support() -> None:
    product = _summary(_snapshot(), max_combos=8_000)
    audit = _summary(_snapshot(), max_combos=50_000)

    assert product == audit


def test_zero_red_is_a_complete_zero_value_state() -> None:
    result = _summary(
        _snapshot(
            qualities=(1, 1, 3, 4, 5),
            shapes=(11, 12, 13, 14, 16),
        )
    )
    summary = result["red_outline_value_summary"]

    assert result["red_count_range"] == [0, 0, 0]
    assert summary["status"] == "outline_complete"
    assert summary["eligibility_complete"] is True
    assert summary["red_count"] == 0
    assert summary["floor_value"] == summary["p50_value"] == summary["p90_value"] == 0


def test_local_identity_fallback_is_exact_and_runtime_conflict_fails_closed() -> None:
    local = _snapshot()
    for group in (
        local["skill_reveal_rows"],
        local["skill_reveals"],
    ):
        for item in group[0]["observed_items"]:
            item.pop("runtime_id")
    for group in (
        local["action_result_rows"],
        local["ui_contract"]["actions"]["results"],
    ):
        for item in group[0]["revealed_items_detail"]:
            item.pop("runtime_id")
    assert _summary(local)["red_outline_value_summary"]["status"] == "outline_complete"

    conflict = _snapshot()
    for group in (
        conflict["action_result_rows"],
        conflict["ui_contract"]["actions"]["results"],
    ):
        group[0]["revealed_items_detail"][-1]["runtime_id"] = 999
    summary = _summary(conflict)["red_outline_value_summary"]
    assert summary["status"] == "conflict"
    assert summary["eligibility_complete"] is False
    assert summary["conflicts"] == ["runtime_identity_set_mismatch"]


def test_malformed_identity_never_coerces_into_a_physical_pair() -> None:
    snapshot = _snapshot()
    for group in (snapshot["skill_reveal_rows"], snapshot["skill_reveals"]):
        group[0]["observed_items"][-1]["runtime_id"] = "6e0"
        group[0]["observed_items"][-1].pop("local_index")

    summary = _summary(snapshot)["red_outline_value_summary"]
    assert summary["status"] == "conflict"
    assert summary["eligibility_complete"] is False
    assert summary["conflicts"] == ["missing_stable_identity"]


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("missing_outline", "missing_full_outline"),
        ("missing_quality", "missing_full_quality_scan"),
        ("duplicate_runtime", "duplicate_quality_runtime_id"),
        ("shape_missing", "red_outline_shape_missing"),
        ("shape_cells_conflict", "red_outline_shape_cells_conflict"),
        ("count_mismatch", "item_count_mismatch"),
    ],
)
def test_incomplete_or_malformed_evidence_fails_closed(
    mutation: str,
    expected_reason: str,
) -> None:
    snapshot = _snapshot()
    if mutation == "missing_outline":
        snapshot["action_result_rows"] = []
        snapshot["ui_contract"]["actions"]["results"] = []
    elif mutation == "missing_quality":
        snapshot["skill_reveal_rows"] = []
        snapshot["skill_reveals"] = []
    elif mutation == "duplicate_runtime":
        for group in (snapshot["skill_reveal_rows"], snapshot["skill_reveals"]):
            group[0]["observed_items"][-1]["runtime_id"] = 5
    elif mutation == "shape_missing":
        for group in (
            snapshot["action_result_rows"],
            snapshot["ui_contract"]["actions"]["results"],
        ):
            group[0]["revealed_items_detail"][-1].pop("shape_code")
    elif mutation == "shape_cells_conflict":
        for group in (
            snapshot["action_result_rows"],
            snapshot["ui_contract"]["actions"]["results"],
        ):
            group[0]["revealed_items_detail"][-1]["cells"] = 5
    elif mutation == "count_mismatch":
        for group in (
            snapshot["action_result_rows"],
            snapshot["ui_contract"]["actions"]["results"],
        ):
            group[0]["revealed_items_detail"].pop()

    summary = _summary(snapshot)["red_outline_value_summary"]
    assert summary["eligibility_complete"] is False
    assert expected_reason in summary["conflicts"]


def test_latest_row_wins_but_conflicting_latest_rows_fail_closed() -> None:
    snapshot = _snapshot()
    stale = copy.deepcopy(snapshot["action_result_rows"][0])
    stale["sort"] = 1
    stale["revealed_items_detail"][-1]["shape_code"] = 11
    snapshot["action_result_rows"].append(stale)
    assert _summary(snapshot)["red_outline_value_summary"]["status"] == "outline_complete"

    conflict = _snapshot()
    same_sort = copy.deepcopy(conflict["action_result_rows"][0])
    same_sort["revealed_items_detail"][-1]["shape_code"] = 11
    conflict["action_result_rows"].append(same_sort)
    summary = _summary(conflict)["red_outline_value_summary"]
    assert summary["status"] == "conflict"
    assert summary["conflicts"] == [
        "row_selection:conflicting_latest_rows:ok"
    ]


@pytest.mark.parametrize(
    ("hero", "phase", "round_no", "reason"),
    [
        ("aisha", "bidding", 5, "hero_not_raven"),
        ("ahmed", "bidding", 5, "hero_not_raven"),
        ("gabriela", "bidding", 5, "hero_not_raven"),
        ("ethan", "bidding", 5, "hero_not_raven"),
        ("maria", "bidding", 5, "hero_not_raven"),
        ("ivan", "bidding", 5, "hero_not_raven"),
        ("carlos", "bidding", 5, "hero_not_raven"),
        ("raven", "settled", 5, "phase_not_bidding"),
        ("raven", "bidding", 4, "round_before_r5"),
    ],
)
def test_noneligible_contexts_are_noop(
    hero: str,
    phase: str,
    round_no: int,
    reason: str,
) -> None:
    result = _summary(_snapshot(hero=hero, phase=phase, round_no=round_no))
    summary = result["red_outline_value_summary"]
    assert summary["status"] == "unavailable"
    assert summary["eligibility_complete"] is False
    assert summary["conflicts"] == [reason]


def test_catalog_or_weight_failure_does_not_invent_a_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_raven_red_outline_value_distribution",
        lambda _map_id, _shapes: (
            "drop_weight_map_unavailable",
            None,
            None,
            None,
            "",
        ),
    )
    summary = _summary(_snapshot())["red_outline_value_summary"]
    assert summary["status"] == "conflict"
    assert summary["eligibility_complete"] is False
    assert summary["floor_value"] is None
    assert summary["conflicts"] == [
        "value_distribution:drop_weight_map_unavailable"
    ]


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_reason"),
    [
        (
            "unknown",
            "unavailable",
            "warehouse_source_not_official_distribution_eligible",
        ),
        (
            "player_custom",
            "unavailable",
            "warehouse_source_not_official_distribution_eligible",
        ),
        ("missing", "unavailable", "warehouse_source_eligibility_missing"),
        ("malformed", "conflict", "warehouse_source_eligibility_malformed"),
        ("contradictory", "conflict", "warehouse_source_eligibility_conflict"),
    ],
)
def test_nonofficial_or_untrusted_source_never_uses_official_value_weights(
    mode: str,
    expected_status: str,
    expected_reason: str,
) -> None:
    snapshot = _snapshot()
    context = snapshot["ui_contract"]["context"]
    if mode in {"unknown", "player_custom"}:
        qualification = (
            "postupdate_unknown_warehouse_source"
            if mode == "unknown"
            else "player_custom_warehouse"
        )
        source = {
            "qualification": qualification,
            "official_distribution_eligible": False,
        }
        snapshot["warehouse_source"] = copy.deepcopy(source)
        snapshot["warehouse_source_qualification"] = qualification
        snapshot["official_distribution_eligible"] = False
        context["warehouse_source"] = copy.deepcopy(source)
        context["warehouse_source_qualification"] = qualification
        context["official_distribution_eligible"] = False
    elif mode == "missing":
        snapshot.pop("warehouse_source")
        snapshot.pop("warehouse_source_qualification")
        snapshot.pop("official_distribution_eligible")
        context.pop("warehouse_source")
        context.pop("warehouse_source_qualification")
        context.pop("official_distribution_eligible")
    elif mode == "malformed":
        snapshot["official_distribution_eligible"] = "true"
    elif mode == "contradictory":
        context["official_distribution_eligible"] = False
        context["warehouse_source"]["official_distribution_eligible"] = False

    summary = _summary(snapshot)["red_outline_value_summary"]
    assert summary["status"] == expected_status
    assert summary["eligibility_complete"] is False
    assert summary["floor_value"] is None
    assert summary["conflicts"] == [expected_reason]


@pytest.mark.parametrize(
    ("sample_name", "map_id", "expected_floor", "expected_cells"),
    REAL_CASES,
)
def test_real_raven_complete_outline_samples(
    sample_name: str,
    map_id: int,
    expected_floor: int,
    expected_cells: list[int],
) -> None:
    result = _summary(_real_snapshot(sample_name, map_id))
    summary = result["red_outline_value_summary"]

    assert result["status"] in {"ok", "count_prior"}
    assert summary["status"] == "outline_complete"
    assert summary["eligibility_complete"] is True
    assert summary["floor_value"] == expected_floor
    assert summary["footprint_cells"] == expected_cells
    assert summary["red_count"] == len(expected_cells)
    assert expected_floor <= summary["p50_value"] <= summary["p90_value"]


def test_real_product_summary_and_tk_consume_the_same_additive_contract() -> None:
    product = summarize_snapshot(
        _snapshot(),
        snapshot_path=Path("synthetic-raven-r5.json"),
    )

    assert (
        product["red_outline_value_summary"]
        == product["ahmed_ref"]["red_outline_value_summary"]
    )
    assert AhmadTkOverlay._outline_value_summary_text(None, product) == (
        "底价 233,496 · P50 442,015 · P90 671,002"
    )


def _warehouse_contract_owners(
    snapshot: dict[str, Any],
) -> tuple[tuple[dict[str, Any], str], ...]:
    context = snapshot["ui_contract"]["context"]
    return (
        (snapshot, "warehouse_source_qualification"),
        (snapshot["warehouse_source"], "qualification"),
        (context, "warehouse_source_qualification"),
        (context["warehouse_source"], "qualification"),
    )


def _warehouse_eligibility_owners(
    snapshot: dict[str, Any],
) -> tuple[tuple[dict[str, Any], str], ...]:
    context = snapshot["ui_contract"]["context"]
    return (
        (snapshot, "official_distribution_eligible"),
        (snapshot["warehouse_source"], "official_distribution_eligible"),
        (context, "official_distribution_eligible"),
        (context["warehouse_source"], "official_distribution_eligible"),
    )


@pytest.mark.parametrize(
    "container_path",
    [
        ("warehouse_source",),
        ("ui_contract",),
        ("ui_contract", "context"),
        ("ui_contract", "context", "warehouse_source"),
    ],
    ids=("snapshot_source", "ui_contract", "context", "context_source"),
)
@pytest.mark.parametrize(
    "malformed_value",
    [None, []],
    ids=("null", "wrong_type"),
)
def test_product_present_malformed_warehouse_contract_container_is_conflict(
    container_path: tuple[str, ...],
    malformed_value: object,
) -> None:
    snapshot = _snapshot()
    owner: dict[str, Any] = snapshot
    for key in container_path[:-1]:
        value = owner[key]
        assert isinstance(value, dict)
        owner = value
    final_key = container_path[-1]
    assert final_key in owner
    owner[final_key] = malformed_value

    product = summarize_snapshot(
        snapshot,
        snapshot_path=Path(
            "synthetic-raven-r5-malformed-container-"
            f"{'-'.join(container_path)}-{type(malformed_value).__name__}.json"
        ),
    )
    summary = product["red_outline_value_summary"]

    assert summary["status"] == "conflict"
    assert summary["eligibility_complete"] is False
    assert summary["floor_value"] is None
    assert summary["conflicts"] == ["warehouse_source_eligibility_malformed"]


@pytest.mark.parametrize(
    ("field_kind", "owners_factory"),
    [
        ("qualification", _warehouse_contract_owners),
        ("official_distribution_eligible", _warehouse_eligibility_owners),
    ],
)
@pytest.mark.parametrize(
    "owner_index",
    range(4),
    ids=("snapshot", "snapshot_source", "context", "context_source"),
)
def test_product_present_null_warehouse_eligibility_is_malformed(
    field_kind: str,
    owners_factory: Any,
    owner_index: int,
) -> None:
    snapshot = _snapshot()
    owner, key = owners_factory(snapshot)[owner_index]
    assert key in owner
    owner[key] = None

    product = summarize_snapshot(
        snapshot,
        snapshot_path=Path(
            f"synthetic-raven-r5-null-{field_kind}-{owner_index}.json"
        ),
    )
    summary = product["red_outline_value_summary"]

    assert summary["status"] == "conflict"
    assert summary["eligibility_complete"] is False
    assert summary["floor_value"] is None
    assert summary["conflicts"] == ["warehouse_source_eligibility_malformed"]


@pytest.mark.parametrize(
    "qualification",
    [
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
    ],
)
def test_product_nonofficial_qualification_rejects_true_distribution_flags(
    qualification: str,
) -> None:
    snapshot = _snapshot()
    for owner, key in _warehouse_contract_owners(snapshot):
        owner[key] = qualification
        owner["official_distribution_eligible"] = True

    product = summarize_snapshot(
        snapshot,
        snapshot_path=Path(f"synthetic-raven-r5-{qualification}.json"),
    )
    summary = product["red_outline_value_summary"]

    assert summary["status"] == "unavailable"
    assert summary["eligibility_complete"] is False
    assert summary["floor_value"] is None
    assert summary["conflicts"] == [
        "warehouse_source_not_official_distribution_eligible"
    ]


def test_product_missing_nested_distribution_flags_fails_closed() -> None:
    snapshot = _snapshot()
    context = snapshot["ui_contract"]["context"]
    snapshot["warehouse_source"].pop("official_distribution_eligible")
    context["warehouse_source"].pop("official_distribution_eligible")

    product = summarize_snapshot(
        snapshot,
        snapshot_path=Path("synthetic-raven-r5-missing-nested-flags.json"),
    )
    summary = product["red_outline_value_summary"]

    assert summary["status"] == "unavailable"
    assert summary["eligibility_complete"] is False
    assert summary["floor_value"] is None
    assert summary["conflicts"] == ["warehouse_source_eligibility_missing"]


@pytest.mark.parametrize(
    ("qualification", "expected_reason"),
    [
        (
            "postupdate_unknown_warehouse_source",
            "warehouse_source_eligibility_conflict",
        ),
        (True, "warehouse_source_eligibility_malformed"),
    ],
)
def test_product_conflicting_or_malformed_qualification_fails_closed(
    qualification: object,
    expected_reason: str,
) -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"]["context"]["warehouse_source"]["qualification"] = (
        qualification
    )

    product = summarize_snapshot(
        snapshot,
        snapshot_path=Path(
            f"synthetic-raven-r5-bad-qualification-{qualification}.json"
        ),
    )
    summary = product["red_outline_value_summary"]

    assert summary["status"] == "conflict"
    assert summary["eligibility_complete"] is False
    assert summary["floor_value"] is None
    assert summary["conflicts"] == [expected_reason]


def _clear_raven_catalog_caches() -> None:
    engine._validated_quality_catalog.cache_clear()
    engine._validated_q4_catalog.cache_clear()
    engine._validated_q5_catalog.cache_clear()
    engine._validated_q6_catalog.cache_clear()
    engine._validated_q6_outline_catalog.cache_clear()
    engine._raven_red_outline_value_distribution.cache_clear()


@pytest.mark.parametrize(
    "duplicate_mode",
    ["same_value", "conflict_before", "conflict_after"],
)
def test_product_duplicate_q6_catalog_identity_is_order_independent_conflict(
    monkeypatch: pytest.MonkeyPatch,
    duplicate_mode: str,
) -> None:
    original_loader = engine._moat_json
    processed_path = ROOT / "data" / "processed" / "items_droppable.json"
    original_payload = original_loader(processed_path)
    assert isinstance(original_payload, list)
    original_row = next(
        row
        for row in original_payload
        if isinstance(row, dict) and row.get("quality") == 6
    )
    duplicate = copy.deepcopy(original_row)
    if duplicate_mode != "same_value":
        duplicate["value"] = int(duplicate["value"]) + 1
    target_index = original_payload.index(original_row)
    mutated = copy.deepcopy(original_payload)
    if duplicate_mode == "conflict_before":
        mutated.insert(target_index, duplicate)
    else:
        mutated.insert(target_index + 1, duplicate)

    def duplicate_catalog_loader(path: object, key: object = None) -> object:
        if Path(path) == processed_path:
            return copy.deepcopy(mutated)
        return original_loader(path, key)

    monkeypatch.setattr(engine, "_moat_json", duplicate_catalog_loader)
    _clear_raven_catalog_caches()
    try:
        product = summarize_snapshot(
            _snapshot(),
            snapshot_path=Path(
                f"synthetic-raven-r5-duplicate-{duplicate_mode}.json"
            ),
        )
    finally:
        _clear_raven_catalog_caches()

    summary = product["red_outline_value_summary"]
    assert summary["status"] == "conflict"
    assert summary["eligibility_complete"] is False
    assert summary["floor_value"] is None
    assert summary["conflicts"] == [
        "value_distribution:catalog_duplicate_item_id"
    ]
