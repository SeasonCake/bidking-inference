"""Tier-local scalar-subclass contracts for phase-1 successor3."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
from test_core_engine_phase1_composition_0_3_2 import (  # noqa: E402
    _both_authorities_snapshot,
)


def _explode(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("hostile scalar protocol executed")


class _HostileStr(str):
    __eq__ = _explode
    __hash__ = str.__hash__
    __str__ = _explode
    lower = _explode
    strip = _explode


class _HostileInt(int):
    __eq__ = _explode
    __float__ = _explode
    __hash__ = int.__hash__
    __int__ = _explode
    __str__ = _explode


class _HostileFloat(float):
    __eq__ = _explode
    __hash__ = float.__hash__
    __int__ = _explode
    __str__ = _explode


class _HostileObject:
    __eq__ = _explode
    __hash__ = _explode
    __str__ = _explode


def _two_quality_authorities_snapshot() -> dict:
    snapshot = _both_authorities_snapshot()
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    public_info["public_avg_values"].insert(
        0,
        {
            "info_id": 200036,
            "semantic": "q4_avg_value",
            "kind": "avg_value",
            "quality": 4,
            "value": 46_000,
        },
    )
    snapshot["public_info_rows"].insert(
        0,
        {"info_id": 200036, "value": 46_000, "session_id": "session-a"},
    )
    return snapshot


def _hostile_alias(field: str, tier: str, scalar: str) -> object:
    quality = int(tier[1:])
    if field == "semantic":
        return _HostileStr(f"{tier}_avg_value")
    if field == "quality":
        if scalar == "int":
            return _HostileInt(quality)
        if scalar == "float":
            return _HostileFloat(float(quality))
        return _HostileStr(f"bucket-{tier}")
    info_id = 200_032 + quality
    if scalar == "int":
        return _HostileInt(info_id)
    if scalar == "float":
        return _HostileFloat(float(info_id))
    return _HostileStr(str(info_id))


@pytest.mark.parametrize("tier", ("q4", "q5"))
@pytest.mark.parametrize(
    ("field", "scalar"),
    (
        ("semantic", "str"),
        ("info_id", "int"),
        ("info_id", "float"),
        ("info_id", "str"),
        ("quality", "int"),
        ("quality", "float"),
        ("quality", "str"),
    ),
)
def test_hostile_scalar_payload_claims_only_its_actual_tier(
    field: str,
    scalar: str,
    tier: str,
) -> None:
    row = {field: _hostile_alias(field, tier, scalar)}
    claims, aliases_valid = engine._hard_public_avg_value_row_claims(row)

    assert claims == frozenset((tier,))
    assert aliases_valid is False


@pytest.mark.parametrize("tier", ("q4", "q5"))
@pytest.mark.parametrize("surface", ("aggregate", "raw"))
@pytest.mark.parametrize("scalar", ("int", "float", "str"))
def test_hostile_numeric_equal_info_id_invalidates_only_its_tier_in_product(
    scalar: str,
    surface: str,
    tier: str,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    row = {
        "info_id": _hostile_alias("info_id", tier, scalar),
        "value": 50_001,
    }
    if surface == "aggregate":
        snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ].append(row)
    else:
        snapshot["public_info_rows"].append(row)

    expected = {"q4": 46_000.0, "q5": 50_000.0}
    expected.pop(tier)
    evidence = engine.extract_evidence(copy.deepcopy(snapshot))
    result = engine.run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000)

    assert evidence.hard_public_avg_values == expected
    assert result.combo_count >= 0


@pytest.mark.parametrize("surface", ("aggregate", "raw"))
def test_hostile_unrelated_issue26_semantic_preserves_issue25_floor(
    surface: str,
) -> None:
    snapshot = _both_authorities_snapshot()
    if surface == "aggregate":
        row = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ][1]
    else:
        row = snapshot["public_info_rows"][2]
    row["semantic"] = _HostileStr("total_avg_cells")

    evidence = engine.extract_evidence(copy.deepcopy(snapshot))
    result = engine.run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000)

    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == ()
    assert result.balanced == 200_000


@pytest.mark.parametrize(
    "malformed_semantic",
    (
        float("nan"),
        _HostileFloat(float("nan")),
        _HostileObject(),
    ),
    ids=("nan", "hostile-nan", "hostile-object"),
)
@pytest.mark.parametrize(
    ("aggregate_index", "raw_index"),
    ((0, 2), (1, 3)),
    ids=("total-count", "total-avg-cells"),
)
@pytest.mark.parametrize("surface", ("aggregate", "raw"))
def test_exact_issue26_domain_owner_quarantines_malformed_semantic_locally(
    surface: str,
    aggregate_index: int,
    raw_index: int,
    malformed_semantic: object,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    if surface == "aggregate":
        row = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ][aggregate_index]
    else:
        row = snapshot["public_info_rows"][raw_index]
    row["semantic"] = malformed_semantic

    evidence = engine.extract_evidence(copy.deepcopy(snapshot))

    assert evidence.hard_public_avg_values == {
        "q4": 46_000.0,
        "q5": 50_000.0,
    }
    assert evidence.total_grid_options == ()
