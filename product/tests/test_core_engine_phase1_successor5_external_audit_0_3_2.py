"""Source-external ownerless info-id probes for phase-1 successor5."""

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
    raise AssertionError("hostile ownerless info-id protocol executed")


class _HostileInfoId:
    __eq__ = _explode
    __hash__ = _explode
    __str__ = _explode


def _snapshot(group: str, order: str, hostile: bool) -> dict:
    snapshot = _both_authorities_snapshot()
    rows = snapshot["ui_contract"]["constraints"]["public_info"][group]
    row = {
        "info_id": _HostileInfoId() if hostile else 999_999,
        "value": 123,
    }
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)
    return snapshot


def _assert_evidence(evidence: engine.RefEvidence, hostile: bool) -> None:
    assert evidence.hard_public_avg_values == (
        {} if hostile else {"q5": 50_000.0}
    )
    # The ownerless object belongs to issue25's conservative boundary; it must
    # not erase the independent valid issue26 authority.
    assert evidence.total_grid_options == (9,)


def _assert_result(result: engine.RefResult, hostile: bool) -> None:
    assert result.balanced == (184_000 if hostile else 200_000)
    hard_floor = f"{engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE}:q5=200000"
    assert (hard_floor in result.notes) is not hostile
    assert (
        f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9" in result.notes
    )


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize("order", ("before", "after"))
@pytest.mark.parametrize(
    "group",
    ("public_avg_values", "public_numeric_facts"),
)
@pytest.mark.parametrize("hostile", (False, True), ids=("builtin", "object"))
def test_ownerless_info_id_object_is_conservative_and_atomic(
    hostile: bool,
    group: str,
    order: str,
    consumer: str,
) -> None:
    snapshot = _snapshot(group, order, hostile)

    if consumer == "extract":
        _assert_evidence(engine.extract_evidence(snapshot), hostile)
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(snapshot)
        _assert_evidence(engine.extract_evidence(prepared), hostile)
        _assert_result(engine.run_reference_engine(prepared), hostile)
    else:
        _assert_result(engine.run_reference_engine(snapshot), hostile)
