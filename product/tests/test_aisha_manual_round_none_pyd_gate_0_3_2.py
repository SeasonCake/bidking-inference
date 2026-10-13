"""Regression for Aisha product-manual snapshots crossing the Cython engine boundary."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import get_type_hints

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = ROOT / "hero_ref" / "src"
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

import ahmad_ref_engine as engine  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "live" / "aisha_manual_round_none_pyd_gate.json"
BUILD_SCRIPT = ROOT / "scripts" / "build_live_portable.ps1"


def _manual_snapshot(ref_inputs: dict[str, object]) -> dict[str, object]:
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot["structured_ref_inputs"] = copy.deepcopy(ref_inputs)
    snapshot["ui_contract"]["constraints"]["structured_ref_inputs"] = copy.deepcopy(  # type: ignore[index]
        ref_inputs
    )
    return snapshot


def test_soft_q6_joint_round_parameter_accepts_manual_none() -> None:
    hints = get_type_hints(engine._aisha_soft_total_q6_joint_distribution)  # noqa: SLF001

    assert hints["round_no"] == int | None


@pytest.mark.parametrize(
    "ref_inputs",
    (
        {"total_count": 61, "avg_cells": {}, "fixed_counts": {}},
        {"total_count": None, "avg_cells": {}, "fixed_counts": {}, "total_cells": 191},
        {
            "total_count": None,
            "avg_cells": {},
            "fixed_counts": {},
            "quality_cells": {"q5": 33},
        },
        {
            "total_count": 61,
            "avg_cells": {},
            "fixed_counts": {},
            "quality_cells": {"q5": 33},
            "total_cells": 191,
        },
    ),
    ids=("total-count", "total-cells", "q5-cells", "full-manual"),
)
def test_product_manual_round_none_produces_quote(ref_inputs: dict[str, object]) -> None:
    snapshot = _manual_snapshot(ref_inputs)

    assert engine._snapshot_round_no(snapshot) is None  # noqa: SLF001

    result = engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(snapshot),
        max_combos=2_500,
    )

    assert result.status in {"ok", "count_prior"}
    assert result.combo_count > 0
    assert 0 < result.conservative <= result.balanced <= result.aggressive
    assert result.evidence.phase == "manual"
    assert engine.AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in result.notes


def test_release_build_executes_manual_fixture_with_stage_pyd() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    gate_start = build.index("$aishaManualPydGateFixture =")
    gate = build[gate_start : build.index("# --- 6d.", gate_start)]

    assert "aisha_manual_round_none_pyd_gate.json" in gate
    assert "$aishaManualPydGateOut = (& $verifyPy -s -c $aishaManualPydGateCode" in gate
    assert "AISHA_MANUAL_PYD_GATE_OK" in gate
    assert "max_combos=2500" in gate
