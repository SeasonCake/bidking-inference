"""Source-external cross-authority probes for phase-1 successor2."""

from __future__ import annotations

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


def _snapshot_with_issue26_semantic(surface: str, hostile: bool) -> dict:
    snapshot = _both_authorities_snapshot()
    semantic: str = (
        _HostileSemantic("total_avg_cells") if hostile else "total_avg_cells"
    )
    if surface == "aggregate":
        row = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ][1]
    else:
        row = snapshot["public_info_rows"][2]
    row["semantic"] = semantic
    return snapshot


def _assert_evidence(evidence: engine.RefEvidence, hostile: bool) -> None:
    # A malformed issue26 alias owns only the issue26 arm.  The independent,
    # valid issue25 q5 authority must survive every public consumer.
    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == (() if hostile else (9,))


def _assert_result(result: engine.RefResult, hostile: bool) -> None:
    assert result.balanced == 200_000
    assert (
        f"{engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE}:q5=200000"
        in result.notes
    )
    total_grid_note = f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9"
    assert (total_grid_note in result.notes) is not hostile


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize("surface", ("aggregate", "raw"))
@pytest.mark.parametrize("hostile", (False, True), ids=("builtin", "subclass"))
def test_issue26_semantic_subclass_only_invalidates_issue26(
    hostile: bool,
    surface: str,
    consumer: str,
) -> None:
    snapshot = _snapshot_with_issue26_semantic(surface, hostile)

    if consumer == "extract":
        _assert_evidence(engine.extract_evidence(snapshot), hostile)
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(snapshot)
        _assert_evidence(engine.extract_evidence(prepared), hostile)
        _assert_result(engine.run_reference_engine(prepared), hostile)
    else:
        _assert_result(engine.run_reference_engine(snapshot), hostile)
