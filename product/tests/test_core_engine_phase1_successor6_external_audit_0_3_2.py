"""Source-external owned-tier isolation probes for phase-1 successor6."""

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


def _explode(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("hostile owned-tier alias protocol executed")


class _HostileAlias:
    __eq__ = _explode
    __hash__ = _explode
    __str__ = _explode


CASES = (
    ("q4", "info_id", "public_avg_values", "before", "extract"),
    ("q5", "info_id", "public_avg_values", "after", "prepare"),
    ("q4", "semantic", "public_numeric_facts", "before", "run"),
    ("q5", "semantic", "public_numeric_facts", "after", "extract"),
    ("q4", "quality", "public_info_rows", "before", "prepare"),
    ("q5", "quality", "public_info_rows", "after", "run"),
)


def _snapshot(
    tier: str,
    field: str,
    container: str,
    order: str,
    hostile: bool,
) -> dict:
    snapshot = _two_quality_authorities_snapshot()
    quality = int(tier[1:])
    row = {
        "info_id": 200_032 + quality,
        "semantic": f"{tier}_avg_value",
        "kind": "avg_value",
        "quality": quality,
        "value": 46_000 if tier == "q4" else 50_000,
    }
    if hostile:
        row[field] = _HostileAlias()

    if container == "public_info_rows":
        row = {**row, "session_id": "session-a"}
        rows = snapshot[container]
    else:
        rows = snapshot["ui_contract"]["constraints"]["public_info"][container]
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)
    return snapshot


def _assert_evidence(
    evidence: engine.RefEvidence,
    tier: str,
    hostile: bool,
) -> None:
    expected = {"q4": 46_000.0, "q5": 50_000.0}
    if hostile:
        expected.pop(tier)
    assert evidence.hard_public_avg_values == expected
    assert evidence.total_grid_options == (9,)


def _assert_result(result: engine.RefResult, tier: str, hostile: bool) -> None:
    if not hostile or tier == "q5":
        assert result.status == "no_reachable_combo"
        assert result.balanced is None
    else:
        assert result.status == "count_prior"
        assert result.balanced == 200_000
        assert (
            f"{engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE}:q5=200000"
            in result.notes
        )
    assert f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9" in result.notes


@pytest.mark.parametrize("hostile", (False, True), ids=("builtin", "object"))
@pytest.mark.parametrize(
    ("tier", "field", "container", "order", "consumer"),
    CASES,
)
def test_uninspectable_sibling_only_invalidates_its_exact_owned_tier(
    tier: str,
    field: str,
    container: str,
    order: str,
    consumer: str,
    hostile: bool,
) -> None:
    snapshot = _snapshot(tier, field, container, order, hostile)

    if consumer == "extract":
        _assert_evidence(engine.extract_evidence(copy.deepcopy(snapshot)), tier, hostile)
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
        _assert_evidence(engine.extract_evidence(prepared), tier, hostile)
        _assert_result(engine.run_reference_engine(prepared), tier, hostile)
    else:
        _assert_result(engine.run_reference_engine(copy.deepcopy(snapshot)), tier, hostile)
