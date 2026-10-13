"""Regression coverage for issue26 invalid-sentinel domain isolation."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
from test_core_engine_phase1_successor3_tier_projection_0_3_2 import (  # noqa: E402
    _two_quality_authorities_snapshot,
)


EXPECTED_AVERAGES = {"q4": 46_000.0, "q5": 50_000.0}


class _HostileKey:
    def __init__(self) -> None:
        self.armed = False

    def __hash__(self) -> int:
        if self.armed:
            raise AssertionError("stable sentinel hashed an untrusted key")
        return 902_611


def _insert(snapshot: dict, group: str, order: str, row: dict) -> None:
    rows = snapshot["ui_contract"]["constraints"]["public_info"][group]
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize("group", ("public_avg_values", "public_numeric_facts"))
def test_exact_issue26_invalid_sentinel_remains_authoritative(
    group: str,
    consumer: str,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    _insert(
        snapshot,
        group,
        "after",
        {"info_id": "200014", "value": {"status": "present_invalid"}},
    )

    if consumer == "prepare":
        snapshot = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    evidence = engine.extract_evidence(copy.deepcopy(snapshot))
    result = (
        engine.run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000)
        if consumer == "run"
        else None
    )

    assert evidence.avg_values == EXPECTED_AVERAGES
    assert evidence.hard_public_avg_values == EXPECTED_AVERAGES
    assert evidence.total_grid_options == (
        () if group == "public_numeric_facts" else (9,)
    )
    assert result is None or result.status == "no_reachable_combo"


@pytest.mark.parametrize("consumer", ("extract", "prepare"))
@pytest.mark.parametrize("info_id", (200014, "200014"))
@pytest.mark.parametrize("order", ("before", "after"))
@pytest.mark.parametrize("group", ("public_avg_values", "public_numeric_facts"))
@pytest.mark.parametrize("tier", ("q4", "q5"))
def test_extended_issue26_sentinel_never_reaches_quality_average(
    tier: str,
    group: str,
    order: str,
    info_id: int | str,
    consumer: str,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    _insert(
        snapshot,
        group,
        order,
        {
            "info_id": info_id,
            "semantic": f"{tier}_avg_value",
            "value": {"status": "present_invalid"},
        },
    )

    if consumer == "prepare":
        snapshot = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    evidence = engine.extract_evidence(copy.deepcopy(snapshot))

    assert evidence.avg_values == EXPECTED_AVERAGES
    assert evidence.hard_public_avg_values == EXPECTED_AVERAGES
    assert evidence.total_grid_options == (
        () if group == "public_numeric_facts" else (9,)
    )


@pytest.mark.parametrize("info_id", (200014, "200014"))
@pytest.mark.parametrize("order", ("before", "after"))
@pytest.mark.parametrize("group", ("public_avg_values", "public_numeric_facts"))
def test_extended_issue26_sentinel_cannot_change_product_result(
    group: str,
    order: str,
    info_id: int | str,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    _insert(
        snapshot,
        group,
        order,
        {
            "info_id": info_id,
            "semantic": "q4_avg_value",
            "value": {"status": "present_invalid"},
        },
    )

    result = engine.run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000)

    assert result.status == "no_reachable_combo"
    assert result.conservative is None
    assert result.balanced is None
    assert result.aggressive is None


@pytest.mark.parametrize(
    "row",
    (
        {
            "info_id": "200014",
            "value": {"status": "present_invalid", "detail": "foreign"},
        },
        {
            "info_id": "200014",
            "value": {"status": "present_invalid"},
            "kind": "avg_cells",
        },
    ),
)
def test_issue26_invalid_sentinel_requires_the_complete_exact_schema(row: dict) -> None:
    snapshot = _two_quality_authorities_snapshot()
    _insert(snapshot, "public_numeric_facts", "after", row)

    evidence = engine.extract_evidence(copy.deepcopy(snapshot))

    assert evidence.avg_values == EXPECTED_AVERAGES
    assert evidence.hard_public_avg_values == EXPECTED_AVERAGES
    assert evidence.total_grid_options == ()


@pytest.mark.parametrize("nested", (False, True))
def test_issue26_invalid_sentinel_does_not_hash_foreign_keys(nested: bool) -> None:
    hostile = _HostileKey()
    value: dict = {"status": "present_invalid"}
    row: dict = {"info_id": "200014", "value": value}
    (value if nested else row)[hostile] = "foreign"
    hostile.armed = True

    assert engine._stable_total_avg_cells_invalid_row(row) is False
