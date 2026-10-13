"""Owner contracts for cross-authority row-domain isolation."""

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
    raise AssertionError("hostile foreign-domain scalar protocol executed")


class _HostileAlias(str):
    __eq__ = _explode
    __hash__ = str.__hash__
    __str__ = _explode
    lower = _explode
    strip = _explode


GROUP_ORDERS = (
    ("public_avg_values", "before"),
    ("public_avg_values", "after"),
    ("public_numeric_facts", "before"),
    ("public_numeric_facts", "after"),
)


def _insert(snapshot: dict, group: str, order: str, row: dict) -> None:
    rows = snapshot["ui_contract"]["constraints"]["public_info"][group]
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)


def _consume(snapshot: dict, consumer: str) -> tuple[engine.RefEvidence, engine.RefResult | None]:
    if consumer == "extract":
        return engine.extract_evidence(copy.deepcopy(snapshot)), None
    if consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
        return (
            engine.extract_evidence(prepared),
            engine.run_reference_engine(prepared, max_combos=8_000),
        )
    result = engine.run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000)
    return engine.extract_evidence(copy.deepcopy(snapshot)), result


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize(("group", "order"), GROUP_ORDERS)
@pytest.mark.parametrize("tier", ("q4", "q5"))
def test_issue26_info_owner_rejects_foreign_issue25_semantic_locally(
    tier: str,
    group: str,
    order: str,
    consumer: str,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    row = {
        "info_id": 200014,
        "semantic": "total_avg_cells",
        "kind": "avg_cells",
        "value": 2.25,
    }
    row["semantic"] = _HostileAlias(f"{tier}_avg_value")
    _insert(snapshot, group, order, row)

    evidence, result = _consume(snapshot, consumer)

    assert evidence.hard_public_avg_values == {
        "q4": 46_000.0,
        "q5": 50_000.0,
    }
    issue26_conflict = group == "public_numeric_facts"
    assert evidence.total_grid_options == (() if issue26_conflict else (9,))
    assert result is None or result.status in {
        "count_prior",
        "no_reachable_combo",
    }


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize(("group", "order"), GROUP_ORDERS)
@pytest.mark.parametrize("tier", ("q4", "q5"))
def test_issue25_info_owner_cannot_poison_issue26_with_foreign_semantic(
    tier: str,
    group: str,
    order: str,
    consumer: str,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    row = {
        "info_id": 200036 if tier == "q4" else 200037,
        "semantic": _HostileAlias("total_avg_cells"),
        "kind": "avg_value",
        "quality": 4 if tier == "q4" else 5,
        "value": 46_000 if tier == "q4" else 50_000,
    }
    _insert(snapshot, group, order, row)

    evidence, result = _consume(snapshot, consumer)
    expected_hard = {"q4": 46_000.0, "q5": 50_000.0}
    expected_hard.pop(tier)

    assert evidence.hard_public_avg_values == expected_hard
    assert evidence.total_grid_options == (9,)
    if result is not None:
        assert f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9" in result.notes
