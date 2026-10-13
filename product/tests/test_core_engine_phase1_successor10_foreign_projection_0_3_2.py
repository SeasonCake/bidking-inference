"""Owner contracts for exact foreign issue26 projection rows."""

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


GROUP_ORDERS = (
    ("public_avg_values", "before"),
    ("public_avg_values", "after"),
    ("public_numeric_facts", "before"),
    ("public_numeric_facts", "after"),
)


def _snapshot(tier: str, group: str, order: str) -> dict:
    snapshot = _two_quality_authorities_snapshot()
    row = {
        "info_id": 200014,
        "semantic": f"{tier}_avg_value",
        "kind": "avg_cells",
        "value": 2.25,
    }
    rows = snapshot["ui_contract"]["constraints"]["public_info"][group]
    if order == "before":
        rows.insert(0, row)
    else:
        rows.append(row)
    return snapshot


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize(("group", "order"), GROUP_ORDERS)
@pytest.mark.parametrize("tier", ("q4", "q5"))
def test_foreign_exact_semantic_never_reaches_legacy_quality_average(
    tier: str,
    group: str,
    order: str,
    consumer: str,
) -> None:
    snapshot = _snapshot(tier, group, order)
    if consumer == "prepare":
        snapshot = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    evidence = engine.extract_evidence(copy.deepcopy(snapshot))
    if consumer == "run":
        engine.run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000)

    assert evidence.hard_public_avg_values == {
        "q4": 46_000.0,
        "q5": 50_000.0,
    }
    assert evidence.avg_values == {"q4": 46_000.0, "q5": 50_000.0}
    assert evidence.total_grid_options == (
        () if group == "public_numeric_facts" else (9,)
    )
