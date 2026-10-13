"""Source-external foreign-owner isolation probes for phase-1 successor8."""

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
    raise AssertionError("hostile foreign-owner semantic protocol executed")


class _HostileSemantic(str):
    __eq__ = _explode
    __hash__ = str.__hash__
    __str__ = _explode
    lower = _explode
    strip = _explode


CASES = (
    ("public_avg_values", "before", "extract"),
    ("public_avg_values", "after", "prepare"),
    ("public_avg_values", "before", "run"),
    ("public_numeric_facts", "after", "extract"),
    ("public_numeric_facts", "before", "prepare"),
    ("public_numeric_facts", "after", "run"),
)


def _snapshot(group: str, order: str, hostile: bool) -> dict:
    snapshot = _both_authorities_snapshot()
    semantic: str = (
        _HostileSemantic("q5_avg_value") if hostile else "total_avg_cells"
    )
    row = {
        # The exact issue26 info-id owns this row. A duplicate or
        # malformed/foreign semantic sibling invalidates issue26 only; it must
        # never invalidate the independent issue25 q5 authority.
        "info_id": 200014,
        "semantic": semantic,
        "kind": "avg_cells",
        "value": 2.25,
    }
    rows = snapshot["ui_contract"]["constraints"]["public_info"][group]
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)
    return snapshot


def _assert_evidence(
    evidence: engine.RefEvidence,
    group: str,
) -> None:
    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == (
        () if group == "public_numeric_facts" else (9,)
    )


def _assert_result(result: engine.RefResult, group: str) -> None:
    assert result.status == "count_prior"
    assert result.balanced == 200_000
    assert (
        f"{engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE}:q5=200000"
        in result.notes
    )
    grid_note = f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9"
    assert (grid_note in result.notes) is not (group == "public_numeric_facts")


@pytest.mark.parametrize("hostile", (False, True), ids=("builtin", "subclass"))
@pytest.mark.parametrize(("group", "order", "consumer"), CASES)
def test_exact_issue26_owner_never_pollutes_issue25_q5(
    group: str,
    order: str,
    consumer: str,
    hostile: bool,
) -> None:
    snapshot = _snapshot(group, order, hostile)

    if consumer == "extract":
        _assert_evidence(
            engine.extract_evidence(copy.deepcopy(snapshot)), group
        )
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
        _assert_evidence(engine.extract_evidence(prepared), group)
        _assert_result(engine.run_reference_engine(prepared), group)
    else:
        _assert_result(
            engine.run_reference_engine(copy.deepcopy(snapshot)), group
        )
