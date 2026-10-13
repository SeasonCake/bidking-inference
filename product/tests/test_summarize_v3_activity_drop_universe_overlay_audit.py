from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_activity_drop_universe_overlay_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_activity_drop_universe_overlay_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _candidate(scheme: str, candidate_map_id: int) -> dict:
    return {
        "status": "ok",
        "candidate_map_id": candidate_map_id,
        "drop_pool_id": candidate_map_id,
        "scheme": scheme,
        "missing_item_rate": 0.0,
        "missing_item_count": 0,
        "zero_item_probability_items": 0,
        "log_likelihood_per_item": -1.5,
        "item_log_likelihood_per_item": -5.5,
    }


def test_activity_drop_universe_overlay_blocks_mixed_guard_loss_map() -> None:
    module = _load_module()
    activity_mapping = {
        "map_results": [
            {
                "map_id": "2524",
                "files": 2,
                "winner_counts": {"minus10": 1, "minus20": 1},
                "item_winner_counts": {"minus10": 1, "minus20": 1},
                "rankmap_labels": {"白色DOWN红色UP": 2},
            }
        ],
        "file_results": [
            {
                "map_id": 2524,
                "file": "a.json",
                "inventory_count": 54,
                "best_scheme": "minus10",
                "best_item_scheme": "minus10",
                "candidates": [_candidate("minus10", 2514), _candidate("minus20", 2504)],
            },
            {
                "map_id": 2524,
                "file": "b.json",
                "inventory_count": 54,
                "best_scheme": "minus20",
                "best_item_scheme": "minus20",
                "candidates": [_candidate("minus10", 2514), _candidate("minus20", 2504)],
            },
        ],
    }
    guard_loss_context = {
        "rows": [
            {
                "map_id": "2524",
                "status": "blocked_drop_universe_or_activity_overlay",
                "reasons": ["cse_exact_drop_universe_gap"],
                "guard": {"p90_coverage_lost_rows": 4, "rows": 4},
                "cse_map_entry": {
                    "status": "blocked_drop_universe_gap_shadow_only",
                    "gate_reason": "non_zodiac_drop_universe_gap",
                    "non_zodiac_missing_max": 54,
                },
            }
        ]
    }

    result = module.summarize_activity_drop_universe_overlay(
        activity_mapping=activity_mapping,
        guard_loss_context=guard_loss_context,
    )

    assert result["status"] == "blocked_activity_overlay_source_required"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    row = result["rows"][0]
    assert row["status"] == "blocked_mixed_overlay_source_required"
    assert row["hard_map_allowed"] is False
    assert row["activity_mapping"]["all_candidates_item_covered"] is True
    assert row["activity_mapping"]["candidate_map_ids"] == {"2504": 2, "2514": 2}
    assert row["activity_mapping"]["missing_item_rate_max"] == 0.0
    assert row["guard_loss_overlap"]["p90_coverage_lost_rows"] == 4
    assert "activity_mapping_mixed_winner" in row["reasons"]
    assert result["summary"]["candidate_item_universe_covered_maps"] == 1
    assert result["summary"]["hard_map_blocked_maps"] == 1


def test_activity_drop_universe_overlay_keeps_single_mapping_watch_only() -> None:
    module = _load_module()
    activity_mapping = {
        "map_results": [
            {
                "map_id": "2521",
                "files": 1,
                "winner_counts": {"minus10": 1},
                "item_winner_counts": {"minus10": 1},
            }
        ],
        "file_results": [
            {
                "map_id": 2521,
                "file": "single.json",
                "inventory_count": 40,
                "best_scheme": "minus10",
                "best_item_scheme": "minus10",
                "candidates": [_candidate("minus10", 2511)],
            }
        ],
    }

    result = module.summarize_activity_drop_universe_overlay(
        activity_mapping=activity_mapping,
        guard_loss_context=None,
    )

    assert result["status"] == "watch_activity_overlay_reference_only"
    row = result["rows"][0]
    assert row["status"] == "watch_overlay_reference_only"
    assert row["hard_map_allowed"] is False
    assert row["activity_mapping"]["all_candidates_item_covered"] is True
