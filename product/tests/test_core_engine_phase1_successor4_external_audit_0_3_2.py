"""Source-external cross-container protocol probes for successor4."""

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
    raise AssertionError("hostile cross-container semantic protocol executed")


class _HostileSemantic:
    __eq__ = _explode
    __hash__ = _explode
    __str__ = _explode


def _snapshot(hostile: bool, order: str) -> dict:
    snapshot = _both_authorities_snapshot()
    rows = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_avg_values"
    ]
    row = {
        # This is an issue26-looking row in the wrong aggregate container.  It
        # must not reach either authority's legacy protocol consumers.
        "info_id": 200014,
        "semantic": _HostileSemantic() if hostile else "total_avg_cells",
        "value": 2.25,
    }
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)
    return snapshot


def _assert_evidence(evidence: engine.RefEvidence) -> None:
    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == (9,)


def _assert_result(result: engine.RefResult) -> None:
    assert result.balanced == 200_000
    assert (
        f"{engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE}:q5=200000"
        in result.notes
    )
    assert (
        f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9" in result.notes
    )


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize("order", ("before", "after"))
@pytest.mark.parametrize("hostile", (False, True), ids=("builtin", "object"))
def test_cross_container_issue26_row_never_reaches_legacy_protocols(
    hostile: bool,
    order: str,
    consumer: str,
) -> None:
    snapshot = _snapshot(hostile, order)

    if consumer == "extract":
        _assert_evidence(engine.extract_evidence(snapshot))
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(snapshot)
        _assert_evidence(engine.extract_evidence(prepared))
        _assert_result(engine.run_reference_engine(prepared))
    else:
        _assert_result(engine.run_reference_engine(snapshot))
