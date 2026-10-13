"""Source-external audit probes for the 0.3.2 phase-1 engine composition."""

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
from test_core_engine_phase1_composition_0_3_2 import (  # noqa: E402
    _both_authorities_snapshot,
)


def _explode(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("hostile semantic protocol executed")


class _HostileSemantic(str):
    __eq__ = _explode
    __hash__ = str.__hash__
    __str__ = _explode
    strip = _explode


def _hostile_issue25_sibling_snapshot(group: str, reverse: bool) -> dict:
    snapshot = _both_authorities_snapshot()
    row = {
        "semantic": _HostileSemantic("q5_avg_value"),
        "value": 50_001,
    }
    rows = snapshot["ui_contract"]["constraints"]["public_info"][group]
    rows.insert(0, row) if reverse else rows.append(row)
    return snapshot


def _assert_issue25_rejected_issue26_preserved(snapshot: dict) -> None:
    evidence = engine.extract_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.hard_public_avg_values == {}
    assert evidence.total_grid_options == (9,)
    assert evidence.total_grid_target == 9.0
    assert result["balanced"] == 184_000
    assert result["total_grid_range"] == [9, 9, 9]
    assert not any(
        note.startswith(engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE)
        for note in result["notes"]
    )
    assert (
        f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9"
        in result["notes"]
    )


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("group", ("public_avg_values", "public_numeric_facts"))
def test_prepare_projection_rejects_hostile_issue25_and_preserves_issue26(
    group: str,
    reverse: bool,
) -> None:
    snapshot = _hostile_issue25_sibling_snapshot(group, reverse)
    prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))

    _assert_issue25_rejected_issue26_preserved(prepared)


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("group", ("public_avg_values", "public_numeric_facts"))
def test_direct_extract_matches_prepare_without_running_hostile_protocols(
    group: str,
    reverse: bool,
) -> None:
    snapshot = _hostile_issue25_sibling_snapshot(group, reverse)

    evidence = engine.extract_evidence(snapshot)
    assert evidence.hard_public_avg_values == {}
    assert evidence.total_grid_options == (9,)


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("group", ("public_avg_values", "public_numeric_facts"))
def test_direct_run_matches_prepare_without_running_hostile_protocols(
    group: str,
    reverse: bool,
) -> None:
    snapshot = _hostile_issue25_sibling_snapshot(group, reverse)

    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert result["balanced"] == 184_000
    assert result["total_grid_range"] == [9, 9, 9]
