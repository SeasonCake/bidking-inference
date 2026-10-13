"""Source-external soft-only scalar probes for phase-1 successor7."""

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
    raise AssertionError("hostile soft-only scalar protocol executed")


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
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]

    # Remove the hard/current q5 ledger, leaving only the historical aggregate
    # soft fallback under test. The independent issue26 ledger remains intact.
    public_info["public_avg_values"] = [
        {"semantic": "unrelated_metric", "value": 1}
    ]
    snapshot["public_info_rows"] = [
        row for row in snapshot["public_info_rows"] if row.get("info_id") != 200037
    ]
    row = {
        "semantic": (
            _HostileSemantic("q5_avg_value") if hostile else "q5_avg_value"
        ),
        "value": 50_000,
    }
    rows = public_info[group]
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)
    return snapshot


def _assert_evidence(evidence: engine.RefEvidence) -> None:
    assert evidence.hard_public_avg_values == {}
    assert evidence.avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == (9,)


def _assert_result(result: engine.RefResult) -> None:
    assert result.status == "count_prior"
    assert result.balanced == 184_000
    assert "public_q5_avg_value" in result.notes
    assert f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9" in result.notes


@pytest.mark.parametrize("hostile", (False, True), ids=("builtin", "subclass"))
@pytest.mark.parametrize(("group", "order", "consumer"), CASES)
def test_soft_only_semantic_subclass_preserves_builtin_fallback(
    group: str,
    order: str,
    consumer: str,
    hostile: bool,
) -> None:
    snapshot = _snapshot(group, order, hostile)

    if consumer == "extract":
        _assert_evidence(engine.extract_evidence(copy.deepcopy(snapshot)))
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
        _assert_evidence(engine.extract_evidence(prepared))
        _assert_result(engine.run_reference_engine(prepared))
    else:
        _assert_result(engine.run_reference_engine(copy.deepcopy(snapshot)))
