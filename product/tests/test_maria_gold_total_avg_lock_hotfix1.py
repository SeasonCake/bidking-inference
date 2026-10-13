from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    _quality_count_matches_value_inputs,
    run_reference_engine,
)


def _maria_4501_gold_total_avg_snapshot() -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": "maria",
                "map_id": 4501,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "info_id": 200037,
                            "semantic": "q5_avg_value",
                            "kind": "avg_value",
                            "quality": 5,
                            "value": 69200.3359375,
                        }
                    ]
                }
            },
            "actions": {
                "results": [
                    {
                        "action_id": 100125,
                        "tool": "金品总价",
                        "result": 207601,
                    }
                ]
            },
        },
        "structured_ref_inputs": {
            "total_count": 40,
            "fixed_counts": {"q1": 9, "q3": 13},
            "count_sums": {"q4q5q6": 18},
        },
    }


def test_maria_gold_total_and_public_avg_lock_three_without_fallback() -> None:
    result = run_reference_engine(
        _maria_4501_gold_total_avg_snapshot(),
        max_combos=60_000,
    ).as_dict()

    assert result["status"] == "ok"
    assert result["combo_count"] > 0
    assert result["evidence"]["avg_values"]["q5"] == 69200.3
    assert result["evidence"]["fixed_counts"]["q5"] == 3
    assert result["quality_count_ranges"]["q5"] == [3, 3, 3]
    assert result["quality_cells_ranges"]["q5"] == [23, 23, 28]
    assert "quality_value_q5_count_derived_product_tolerance" in result["notes"]
    assert "quality_value_q5_avg_count_conflict" not in result["notes"]
    assert "public_quality_avg_value_conflict_fallback" not in result["notes"]
    assert "public_q5_avg_value_downgraded" not in result["notes"]


def test_total_avg_pair_tolerance_rejects_material_value_conflict() -> None:
    assert _quality_count_matches_value_inputs(3, 69200.3, 207601)
    assert not _quality_count_matches_value_inputs(3, 69200.3, 207603)
