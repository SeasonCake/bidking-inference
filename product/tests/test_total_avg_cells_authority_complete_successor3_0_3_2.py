"""Successor3 regressions for total-average raw/prepared authority."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_ref_engine import (  # noqa: E402
    TOTAL_AVG_CELLS_GRID_OPTIONS_DISABLE,
    _TOTAL_AVG_CELLS_PREPARED_DECISION_KEY,
    _TOTAL_AVG_CELLS_PREPARED_MARKER_KEY,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


ROLLBACK = TOTAL_AVG_CELLS_GRID_OPTIONS_DISABLE


def _source() -> dict:
    return {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _fact(semantic: str, value: object) -> dict:
    return {
        "info_id": {
            "total_item_count": 200017,
            "total_avg_cells": 200014,
        }[semantic],
        "semantic": semantic,
        "kind": "count" if semantic == "total_item_count" else "avg_cells",
        "value": value,
    }


def _snapshot() -> dict:
    source = _source()
    facts = [
        _fact("total_item_count", 150),
        _fact("total_avg_cells", 2.01),
    ]
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2401,
                "phase": "bidding",
                "warehouse_source_qualification": source["qualification"],
                "warehouse_source": copy.deepcopy(source),
            },
            "constraints": {
                "public_info": {"public_numeric_facts": copy.deepcopy(facts)}
            },
            "source": {
                "warehouse_source_qualification": source["qualification"],
                "warehouse_source": copy.deepcopy(source),
            },
        },
        "structured_ref_inputs": {
            "total_count": 150,
            "fixed_counts": {"q1": 150, "q3": 0, "q4": 0, "q5": 0, "q6": 0},
        },
        "public_info_rows": [
            {"info_id": 200017, "value": 150},
            {"info_id": 200014, "value": 2.01},
        ],
        "warehouse_source_qualification": source["qualification"],
        "warehouse_source": copy.deepcopy(source),
    }


def _without_total_average(snapshot: dict) -> dict:
    control = copy.deepcopy(snapshot)
    control["public_info_rows"] = [
        row
        for row in control["public_info_rows"]
        if row.get("info_id") != 200014
    ]
    public_info = control["ui_contract"]["constraints"]["public_info"]
    public_info["public_numeric_facts"] = [
        row
        for row in public_info["public_numeric_facts"]
        if row.get("info_id") != 200014
    ]
    return control


def _projection(snapshot: dict) -> dict:
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    return {
        key: result.get(key)
        for key in (
            "status",
            "conservative",
            "balanced",
            "aggressive",
            "combo_count",
            "quality_count_ranges",
            "quality_cells_ranges",
            "quality_count_candidates",
            "quality_cells_candidates",
            "quality_count_cells_candidates",
            "quality_lock",
            "total_grid_range",
        )
    }


def _assert_total_average_noop(snapshot: dict) -> None:
    assert extract_evidence(snapshot).total_grid_options == ()
    assert _projection(snapshot) == _projection(_without_total_average(snapshot))


@pytest.mark.parametrize("info_id", (200014.0, 200017.0))
@pytest.mark.parametrize("location", ("raw", "aggregate"))
@pytest.mark.parametrize("reverse", (False, True))
def test_numeric_equal_wrong_type_alias_fails_closed(
    info_id: float,
    location: str,
    reverse: bool,
) -> None:
    snapshot = _snapshot()
    bad = {
        "info_id": info_id,
        "value": 2.02 if info_id == 200014.0 else 151,
    }
    rows = (
        snapshot["public_info_rows"]
        if location == "raw"
        else snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
    )
    if reverse:
        rows.insert(0, bad)
    else:
        rows.append(bad)
    _assert_total_average_noop(snapshot)


class _RowSubclass(dict):
    pass


class _ListSubclass(list):
    pass


@pytest.mark.parametrize("location", ("raw", "aggregate"))
@pytest.mark.parametrize("container", (False, True))
def test_targeted_row_or_container_subclass_fails_closed(
    location: str,
    container: bool,
) -> None:
    snapshot = _snapshot()
    if location == "raw":
        rows = snapshot["public_info_rows"]
    else:
        rows = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
    rows.append(_RowSubclass({"info_id": 200014, "value": 2.02}))
    if container:
        replacement = _ListSubclass(rows)
        if location == "raw":
            snapshot["public_info_rows"] = replacement
        else:
            snapshot["ui_contract"]["constraints"]["public_info"][
                "public_numeric_facts"
            ] = replacement
    _assert_total_average_noop(snapshot)


@pytest.mark.parametrize("identity", ("session_id", "session_epoch", "epoch"))
@pytest.mark.parametrize("missing", (False, True))
def test_present_identity_requires_exact_nonempty_current_anchor(
    identity: str,
    missing: bool,
) -> None:
    snapshot = _snapshot()
    value: str | int = "" if identity == "session_id" else 9
    if not missing:
        snapshot["ui_contract"]["context"][identity] = "" if identity == "session_id" else 9.0
    for row in snapshot["public_info_rows"]:
        row[identity] = value
    _assert_total_average_noop(snapshot)


def test_identity_free_monitor_rows_remain_compatible() -> None:
    assert extract_evidence(_snapshot()).total_grid_options == (301, 302)


def test_prepared_marker_is_bound_to_current_session() -> None:
    first = _snapshot()
    first["ui_contract"]["context"]["session_id"] = "session-a"
    for row in first["public_info_rows"]:
        row["session_id"] = "session-a"
    prepared = prepare_reference_engine_snapshot(first)

    second = _snapshot()
    second["ui_contract"]["context"]["session_id"] = "session-b"
    for row in second["public_info_rows"]:
        row["session_id"] = "session-b"
    second[_TOTAL_AVG_CELLS_PREPARED_MARKER_KEY] = prepared[
        _TOTAL_AVG_CELLS_PREPARED_MARKER_KEY
    ]
    second[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY] = copy.deepcopy(
        prepared[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY]
    )
    _assert_total_average_noop(second)


def test_prepared_marker_is_bound_to_full_targeted_raw_ledger() -> None:
    first = _snapshot()
    prepared = prepare_reference_engine_snapshot(first)
    second = _snapshot()
    second["public_info_rows"].append(
        {"info_id": 200014, "value": 9.99, "stale": True}
    )
    second[_TOTAL_AVG_CELLS_PREPARED_MARKER_KEY] = prepared[
        _TOTAL_AVG_CELLS_PREPARED_MARKER_KEY
    ]
    second[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY] = copy.deepcopy(
        prepared[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY]
    )
    _assert_total_average_noop(second)


@pytest.mark.parametrize("missing", ("marker", "decision"))
def test_one_sided_marker_damage_remains_fail_closed(missing: str) -> None:
    prepared = prepare_reference_engine_snapshot(_snapshot())
    prepared.pop(
        _TOTAL_AVG_CELLS_PREPARED_MARKER_KEY
        if missing == "marker"
        else _TOTAL_AVG_CELLS_PREPARED_DECISION_KEY
    )
    _assert_total_average_noop(prepared)


def test_repeated_prepare_is_idempotent() -> None:
    once = prepare_reference_engine_snapshot(_snapshot())
    assert prepare_reference_engine_snapshot(copy.deepcopy(once)) == once


@pytest.mark.parametrize("max_combos", (1, 2, 3, 4, 8_000, 50_000))
def test_rollback_omits_successor_only_empty_evidence_member(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    monkeypatch.setenv(ROLLBACK, "1")
    result = run_reference_engine(_snapshot(), max_combos=max_combos).as_dict()
    assert "total_grid_options" not in result["evidence"]
