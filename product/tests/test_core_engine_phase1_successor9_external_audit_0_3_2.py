"""External audit for successor9 cross-domain projection isolation."""

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


@pytest.mark.parametrize("consumer", ("extract", "prepare"))
@pytest.mark.parametrize("order", ("before", "after"))
@pytest.mark.parametrize("tier", ("q4", "q5"))
def test_issue26_owner_foreign_exact_semantic_never_reprojects_issue25_average(
    tier: str,
    order: str,
    consumer: str,
) -> None:
    snapshot = _two_quality_authorities_snapshot()
    rows = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_avg_values"
    ]
    issue26_row = {
        "info_id": 200014,
        "semantic": f"{tier}_avg_value",
        "kind": "avg_cells",
        "value": 2.25,
    }
    if order == "before":
        rows.insert(0, issue26_row)
    else:
        rows.append(issue26_row)

    if consumer == "prepare":
        snapshot = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    evidence = engine.extract_evidence(copy.deepcopy(snapshot))

    assert evidence.hard_public_avg_values == {
        "q4": 46_000.0,
        "q5": 50_000.0,
    }
    assert evidence.avg_values == {"q4": 46_000.0, "q5": 50_000.0}
    assert evidence.total_grid_options == (9,)
