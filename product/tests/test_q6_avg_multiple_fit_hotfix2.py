from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    COMPLETE_GT5_JOINT_SUPPORT_DISABLE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


@pytest.fixture(autouse=True)
def _historical_candidate_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep this hotfix2 test on its named pre-complete-support wire."""
    monkeypatch.setenv(COMPLETE_GT5_JOINT_SUPPORT_DISABLE, "1")


def _run(*, include_integer_gold_avg: bool) -> dict:
    actions = [{"action_id": 100114, "result": 4.5, "tool": "珍品均格"}]
    if include_integer_gold_avg:
        actions.insert(0, {"action_id": 100113, "result": 3.0, "tool": "极品均格"})
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2401,
                "phase": "bidding",
                "round": 4,
            },
            "actions": {"results": actions},
        },
        "action_result_rows": actions,
        "structured_ref_inputs": {
            "total_count": 40,
            "min_counts": {"q3": 1},
        },
    }
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=50_000,
    ).as_dict()


def test_non_integer_red_avg_fits_count_multiples_without_gold_tool() -> None:
    result = _run(include_integer_gold_avg=False)

    assert result["status"] == "count_prior"
    assert "action_100114_q6_avg_cells" in result["notes"]
    red_counts = result["quality_count_ranges"]["q6"]
    red_cells = result["quality_cells_ranges"]["q6"]
    assert red_counts == [2, 2, 4]
    assert red_cells == [9, 9, 18]
    assert all(count % 2 == 0 for count in red_counts)
    assert any(count != red_counts[0] for count in red_counts[1:])


def test_integer_gold_avg_does_not_mask_red_multiple_fit() -> None:
    without_gold = _run(include_integer_gold_avg=False)
    with_integer_gold = _run(include_integer_gold_avg=True)

    assert with_integer_gold["quality_count_ranges"]["q6"] == without_gold[
        "quality_count_ranges"
    ]["q6"]
    assert with_integer_gold["quality_cells_ranges"]["q6"] == without_gold[
        "quality_cells_ranges"
    ]["q6"]
