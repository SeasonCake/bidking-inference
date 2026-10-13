from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = ROOT / "hero_ref" / "src"
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from ahmad_ref_engine import extract_evidence, run_reference_engine  # noqa: E402


_DEFAULT = object()


def _settled_snapshot(
    *,
    total_items: int = 56,
    total_cells: int = 154,
    layout_cells: int = 164,
    final_quality_counts: str = "q1=3;q2=10;q3=15;q4=12;q5=8;q6=8",
    final_quality_cells: str = "q1=3;q2=25;q3=43;q4=34;q5=27;q6=22",
    settlement_incomplete: bool = False,
    settlement_parse_residual: int = 0,
    captured_items: int | None = 56,
    captured_cells: int | None = 154,
    declared_items: int | None = None,
    declared_cells: int | None = None,
    inventory_count: int | None | object = _DEFAULT,
    inventory_cells: int | None | object = _DEFAULT,
) -> dict:
    if inventory_count is _DEFAULT:
        inventory_count = total_items
    if inventory_cells is _DEFAULT:
        inventory_cells = total_cells
    return {
        "phase": "settled",
        "hero": "aisha",
        "map_id": 4541,
        "settlement_incomplete": settlement_incomplete,
        "settlement_parse_residual": settlement_parse_residual,
        "inventory_count": inventory_count,
        "inventory_cells": inventory_cells,
        "final_quality_counts": final_quality_counts,
        "final_quality_cells": final_quality_cells,
        "layout_stage_rows": [{"布局估计": f"71/{layout_cells}/170"}],
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 4541, "phase": "settled"},
            "truth": {
                "available": True,
                "complete": True,
                "partial": False,
                "captured_items": captured_items,
                "captured_cells": captured_cells,
                "declared_items": declared_items,
                "declared_cells": declared_cells,
                "total_items": total_items,
                "total_cells": total_cells,
                "total_value": 2_163_815,
            },
        },
    }


def test_complete_4541_truth_cells_override_larger_stale_layout() -> None:
    snapshot = _settled_snapshot()

    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_target == 154
    assert "settlement_review_complete_truth_over_layout_replay" in evidence.source_notes
    assert "settlement_review_total_grid_from_layout_replay" not in evidence.source_notes
    assert result["status"] == "ok"
    assert "hard_conflict:quality_cells_total_grid" not in result["notes"]


def test_complete_4542_larger_truth_cells_remain_unchanged() -> None:
    snapshot = _settled_snapshot(
        total_items=68,
        total_cells=190,
        final_quality_counts="q1=5;q2=12;q3=17;q4=20;q5=9;q6=5",
        final_quality_cells="q1=12;q2=27;q3=43;q4=53;q5=39;q6=16",
        captured_items=68,
        captured_cells=190,
    )

    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_target == 190
    assert "settlement_review_complete_truth_over_layout_replay" not in evidence.source_notes
    assert result["status"] == "ok"


@pytest.mark.parametrize(
    ("changes", "expected_note"),
    [
        ({"settlement_incomplete": True}, "settlement_review_total_grid_from_layout_replay"),
        (
            {"captured_items": 55, "declared_items": 56},
            "settlement_review_total_grid_from_layout_replay",
        ),
        (
            {"captured_cells": 153},
            "settlement_review_total_grid_from_layout_replay",
        ),
        (
            {"declared_items": 55},
            "settlement_review_total_grid_from_layout_replay",
        ),
        (
            {"declared_cells": 153},
            "settlement_review_total_grid_from_layout_replay",
        ),
        (
            {"final_quality_counts": "q1=3;q2=10;q3=15;q4=12;q5=8;q6=7"},
            "settlement_review_total_grid_from_layout_replay",
        ),
        (
            {"final_quality_cells": "q1=3;q2=25;q3=43;q4=34;q5=27;q6=21"},
            "settlement_review_total_grid_from_layout_replay",
        ),
    ],
)
def test_unclosed_settlement_does_not_promote_item_cells(
    changes: dict,
    expected_note: str,
) -> None:
    evidence = extract_evidence(_settled_snapshot(**changes))

    assert evidence.total_grid_target == 164
    assert expected_note in evidence.source_notes
    assert "settlement_review_complete_truth_over_layout_replay" not in evidence.source_notes


@pytest.mark.parametrize(
    "changes",
    [
        {"inventory_count": None},
        {"inventory_cells": None},
        {"captured_items": None},
        {"captured_cells": None},
    ],
)
def test_missing_required_settlement_fields_do_not_promote_item_cells(
    changes: dict,
) -> None:
    evidence = extract_evidence(_settled_snapshot(**changes))

    assert evidence.total_grid_target == 164
    assert "settlement_review_total_grid_from_layout_replay" in evidence.source_notes
    assert "settlement_review_complete_truth_over_layout_replay" not in evidence.source_notes


def test_declared_fields_are_optional_when_required_truth_is_closed() -> None:
    evidence = extract_evidence(
        _settled_snapshot(declared_items=None, declared_cells=None)
    )

    assert evidence.total_grid_target == 154
    assert "settlement_review_complete_truth_over_layout_replay" in evidence.source_notes


@pytest.mark.parametrize("settlement_parse_residual", (1, 2462))
def test_parse_residual_is_diagnostic_not_a_complete_truth_gate(
    settlement_parse_residual: int,
) -> None:
    evidence = extract_evidence(
        _settled_snapshot(settlement_parse_residual=settlement_parse_residual)
    )

    assert evidence.total_grid_target == 154
    assert "settlement_review_complete_truth_over_layout_replay" in evidence.source_notes


def test_strict_truth_rollback_restores_legacy_runtime_and_engine_in_fresh_process() -> None:
    code = r'''
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import extract_evidence
from bidking_lab.runtime import ui_contract_from_artifact

runtime_truth = ui_contract_from_artifact(
    {
        "phase": "settled",
        "inventory_count": 56,
        "inventory_cells": 154,
        "known_value_sum": 2163815,
    }
)["truth"]
snapshot = {
    "phase": "settled",
    "hero": "aisha",
    "settlement_incomplete": False,
    "settlement_parse_residual": 0,
    "final_quality_counts": "q1=3;q2=10;q3=15;q4=12;q5=8;q6=8",
    "final_quality_cells": "q1=3;q2=25;q3=43;q4=34;q5=27;q6=22",
    "layout_stage_rows": [{"布局估计": "71/164/170"}],
    "ui_contract": {
        "context": {"hero": "aisha", "phase": "settled"},
        "truth": {
            "available": True,
            "complete": True,
            "partial": False,
            "total_items": 56,
            "total_cells": 154,
            "total_value": 2163815,
        },
    },
}
print(json.dumps({
    "runtime_complete": runtime_truth["complete"],
    "engine_total_cells": extract_evidence(snapshot).total_grid_target,
}))
'''

    def run(*, rollback: bool) -> dict[str, object]:
        env = dict(os.environ)
        env.pop("BIDKING_DISABLE_STRICT_COMPLETE_SETTLEMENT_TRUTH", None)
        if rollback:
            env["BIDKING_DISABLE_STRICT_COMPLETE_SETTLEMENT_TRUTH"] = "1"
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(completed.stdout)

    assert run(rollback=False) == {
        "runtime_complete": False,
        "engine_total_cells": 164.0,
    }
    assert run(rollback=True) == {
        "runtime_complete": True,
        "engine_total_cells": 154.0,
    }
