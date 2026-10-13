"""Product-path regression coverage for hostile public-row key collisions."""

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


EXPECTED_AVERAGES = {"q4": 46_000.0, "q5": 50_000.0}


class _CollisionKey:
    def __init__(self, target: str) -> None:
        self.target = target
        self.armed = False

    def __hash__(self) -> int:
        return hash(self.target)

    def __eq__(self, other: object) -> bool:
        if self.armed:
            raise AssertionError("engine compared a hostile public-row key")
        return False


def _collision_row(target: str) -> dict:
    hostile = _CollisionKey(target)
    if target == "info_id":
        row = {hostile: "200014", "value": {"status": "present_invalid"}}
    else:
        row = {"info_id": "200014", "value": {hostile: "present_invalid"}}
    hostile.armed = True
    return row


def _insert(snapshot: dict, container: str, order: str, row: dict) -> None:
    if container == "public_info_rows":
        rows = snapshot[container]
    else:
        rows = snapshot["ui_contract"]["constraints"]["public_info"][container]
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize("order", ("before", "after"))
@pytest.mark.parametrize(
    "container",
    ("public_avg_values", "public_numeric_facts", "public_info_rows"),
)
@pytest.mark.parametrize("target", ("info_id", "status"))
def test_key_collision_never_executes_protocol_or_crosses_authority(
    target: str,
    container: str,
    order: str,
    consumer: str,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    _insert(snapshot, container, order, _collision_row(target))

    if consumer == "prepare":
        snapshot = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    evidence = engine.extract_evidence(copy.deepcopy(snapshot))
    result = (
        engine.run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000)
        if consumer == "run"
        else None
    )

    assert evidence.avg_values == EXPECTED_AVERAGES
    assert evidence.hard_public_avg_values == (
        {} if target == "info_id" else EXPECTED_AVERAGES
    )
    assert evidence.total_grid_options == (
        ()
        if target == "status" and container != "public_avg_values"
        else (9,)
    )
    assert result is None or result.status == "no_reachable_combo"
    assert result is None or result.balanced is None
