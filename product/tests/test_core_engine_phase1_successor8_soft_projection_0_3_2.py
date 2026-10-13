"""Owner contracts for legacy-only q4/q5 scalar projection."""

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
    raise AssertionError("hostile soft public-average scalar protocol executed")


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


def _snapshot(
    *,
    tier: str,
    alias_field: str,
    group: str,
    order: str,
    hostile: bool,
) -> dict:
    snapshot = _both_authorities_snapshot()
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    public_info["public_avg_values"] = [
        {"semantic": "unrelated_metric", "value": 1}
    ]
    snapshot["public_info_rows"] = [
        row
        for row in snapshot["public_info_rows"]
        if row.get("info_id") not in (200036, 200037)
    ]
    alias = (
        f"{tier}_avg_value" if alias_field == "semantic" else tier.removeprefix("q")
    )
    row = {
        alias_field: _HostileAlias(alias) if hostile else alias,
        "value": 45_000 if tier == "q4" else 50_000,
    }
    if alias_field == "quality":
        row["kind"] = "avg_value"
    rows = public_info[group]
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)
    return snapshot


def _assert_evidence(evidence: engine.RefEvidence, tier: str) -> None:
    expected = 45_000.0 if tier == "q4" else 50_000.0
    assert evidence.hard_public_avg_values == {}
    assert evidence.avg_values == {tier: expected}
    assert evidence.total_grid_options == (9,)


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize(("group", "order"), GROUP_ORDERS)
@pytest.mark.parametrize("alias_field", ("semantic", "quality"))
@pytest.mark.parametrize("tier", ("q4", "q5"))
def test_soft_scalar_subclass_matches_builtin_without_authorizing_hard_floor(
    tier: str,
    alias_field: str,
    group: str,
    order: str,
    consumer: str,
) -> None:
    builtin = _snapshot(
        tier=tier,
        alias_field=alias_field,
        group=group,
        order=order,
        hostile=False,
    )
    hostile = _snapshot(
        tier=tier,
        alias_field=alias_field,
        group=group,
        order=order,
        hostile=True,
    )

    if consumer == "extract":
        builtin_evidence = engine.extract_evidence(copy.deepcopy(builtin))
        hostile_evidence = engine.extract_evidence(copy.deepcopy(hostile))
        _assert_evidence(builtin_evidence, tier)
        _assert_evidence(hostile_evidence, tier)
    elif consumer == "prepare":
        builtin_prepared = engine.prepare_reference_engine_snapshot(
            copy.deepcopy(builtin)
        )
        hostile_prepared = engine.prepare_reference_engine_snapshot(
            copy.deepcopy(hostile)
        )
        _assert_evidence(engine.extract_evidence(builtin_prepared), tier)
        _assert_evidence(engine.extract_evidence(hostile_prepared), tier)
        assert engine.run_reference_engine(hostile_prepared).as_dict() == (
            engine.run_reference_engine(builtin_prepared).as_dict()
        )
    else:
        assert engine.run_reference_engine(copy.deepcopy(hostile)).as_dict() == (
            engine.run_reference_engine(copy.deepcopy(builtin)).as_dict()
        )


@pytest.mark.parametrize("tier", ("q4", "q5"))
def test_stale_exact_raw_row_does_not_hide_aggregate_soft_subclass(
    tier: str,
) -> None:
    builtin = _snapshot(
        tier=tier,
        alias_field="semantic",
        group="public_avg_values",
        order="after",
        hostile=False,
    )
    hostile = _snapshot(
        tier=tier,
        alias_field="semantic",
        group="public_avg_values",
        order="after",
        hostile=True,
    )
    stale_row = {
        "info_id": 200036 if tier == "q4" else 200037,
        "value": 45_000 if tier == "q4" else 50_000,
        "session_id": "stale-session",
    }
    builtin["public_info_rows"].append(copy.deepcopy(stale_row))
    hostile["public_info_rows"].append(copy.deepcopy(stale_row))

    builtin_evidence = engine.extract_evidence(copy.deepcopy(builtin))
    hostile_evidence = engine.extract_evidence(copy.deepcopy(hostile))
    _assert_evidence(builtin_evidence, tier)
    _assert_evidence(hostile_evidence, tier)
    assert engine.run_reference_engine(hostile).as_dict() == (
        engine.run_reference_engine(builtin).as_dict()
    )
