"""Source-external malformed issue26-domain probes for successor3."""

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
    raise AssertionError("hostile malformed semantic protocol executed")


class _HostileNaN(float):
    __eq__ = _explode
    __float__ = _explode
    __hash__ = float.__hash__
    __str__ = _explode


class _HostileObject:
    __eq__ = _explode
    __hash__ = _explode
    __str__ = _explode


def _semantic(case: str) -> object:
    if case == "builtin":
        return "total_avg_cells"
    if case == "nan":
        return float("nan")
    if case == "nan_subclass":
        return _HostileNaN(float("nan"))
    return _HostileObject()


def _snapshot(case: str, surface: str) -> dict:
    snapshot = _both_authorities_snapshot()
    if surface == "aggregate":
        row = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ][1]
    else:
        row = snapshot["public_info_rows"][2]
    # Exact info_id=200014 already assigns this row to issue26.  A malformed
    # sibling semantic must not be reinterpreted as an issue25 q4/q5 claim.
    row["semantic"] = _semantic(case)
    return snapshot


def _assert_evidence(evidence: engine.RefEvidence, malformed: bool) -> None:
    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == (() if malformed else (9,))


def _assert_result(result: engine.RefResult, malformed: bool) -> None:
    assert result.balanced == 200_000
    assert (
        f"{engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE}:q5=200000"
        in result.notes
    )
    total_grid_note = f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9"
    assert (total_grid_note in result.notes) is not malformed


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize("surface", ("aggregate", "raw"))
@pytest.mark.parametrize(
    "case",
    ("builtin", "nan", "nan_subclass", "object"),
)
def test_malformed_issue26_semantic_does_not_pollute_issue25(
    case: str,
    surface: str,
    consumer: str,
) -> None:
    snapshot = _snapshot(case, surface)
    malformed = case != "builtin"

    if consumer == "extract":
        _assert_evidence(engine.extract_evidence(snapshot), malformed)
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(snapshot)
        _assert_evidence(engine.extract_evidence(prepared), malformed)
        _assert_result(engine.run_reference_engine(prepared), malformed)
    else:
        _assert_result(engine.run_reference_engine(snapshot), malformed)
