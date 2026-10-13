from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_practical_guard_loss_source_context.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_practical_guard_loss_source_context",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _guard_stats(loss_rows: int, *, flags: dict[str, int]) -> dict:
    return {
        "v3_practical_guard_case_summary": {
            "rows": loss_rows,
            "p50_worsened_rows": 0,
            "p90_coverage_lost_rows": loss_rows,
            "p90_extreme_over_added_rows": 0,
            "p90_coverage_loss_context": {
                "rows": loss_rows,
                "by_guard_kind": {"live_prior_only_raise_guard": loss_rows},
                "by_guard_flag": flags,
                "by_evidence_profile": {"shape+layout": loss_rows},
                "median_guarded_p90_shortfall": 100_000,
                "median_unguarded_p90_excess": 80_000,
                "median_p90_guard_cut": 180_000,
            },
        }
    }


def test_guard_loss_source_context_classifies_cross_artifact_overlap() -> None:
    module = _load_module()
    guard_doc = {
        "v3_practical_archive_live_guard_metrics": {
            "by_map_id": {
                "2410": _guard_stats(
                    8,
                    flags={
                        "capacity_source_candidate": 8,
                        "q6_prior_floor_watch": 8,
                        "settlement_count_prior_candidate": 8,
                    },
                ),
                "2524": _guard_stats(
                    4,
                    flags={
                        "capacity_source_candidate": 4,
                        "q6_prior_floor_watch": 4,
                        "settlement_count_prior_candidate": 4,
                    },
                ),
                "2401": _guard_stats(
                    3,
                    flags={
                        "q6_prior_floor_watch": 3,
                        "q6_prior_tail_ceiling": 3,
                        "settlement_count_prior_candidate": 3,
                    },
                ),
            }
        }
    }
    cse = {
        "entries": [
            {
                "scope": "map_id",
                "group": "2410",
                "status": "watch_capacity_source_expansion_shadow_only",
                "gate_reason": "observed_unique_round_over_cap_source_expansion",
                "mechanism_classes": (
                    "not_unique_round_cap_blocker:18,"
                    "session_capacity_source_semantics:1"
                ),
                "source_context_classes": (
                    "payload_verified_empty_action_results:3,"
                    "payload_verified_partial_action_only:14"
                ),
                "source_evidence_classes": "settlement_payload_verified_only:17",
                "session_capacity_source_semantics_rows": 1,
                "server_side_expansion_rows": 0,
                "unique_round_overflow_rows": 1,
                "non_zodiac_missing_max": 0,
            },
            {
                "scope": "map_id",
                "group": "2524",
                "status": "blocked_drop_universe_gap_shadow_only",
                "gate_reason": "non_zodiac_drop_universe_gap",
                "mechanism_classes": "not_unique_round_cap_blocker:3",
                "source_context_classes": "payload_verified_partial_action_only:3",
                "source_evidence_classes": "settlement_payload_verified_only:3",
                "session_capacity_source_semantics_rows": 0,
                "server_side_expansion_rows": 0,
                "unique_round_overflow_rows": 0,
                "non_zodiac_missing_max": 54,
            },
        ]
    }
    source_semantics = {
        "detail_rows": [
            {
                "map_id": 2410,
                "map_family": "villa",
                "mechanism_class": "session_capacity_source_semantics",
                "source_context_class": "payload_verified_empty_action_results",
                "source_evidence_class": "settlement_payload_verified_only",
                "unique_residual_mode": "unique_round_cap_overflow_after_temp",
                "unique_round_cap_excess_after_temp_zodiac_count": 3,
                "unique_drop_ref_excess_after_temp_zodiac_count": 13,
            },
            {
                "map_id": 2401,
                "map_family": "villa",
                "mechanism_class": "not_unique_round_cap_blocker",
                "source_context_class": "payload_verified_partial_action_only",
                "source_evidence_class": "settlement_payload_verified_only",
                "unique_residual_mode": "instance_round_cap_overflow_after_temp",
                "unique_round_cap_excess_after_temp_zodiac_count": 0,
                "unique_drop_ref_excess_after_temp_zodiac_count": 10,
            },
        ]
    }
    capacity_table = {
        "detail_rows": [
            {
                "map_id": 2401,
                "semantic_status": "watch_activity_extras_explain_drop_ref_gap",
                "residual_mode": "within_drop_ref",
                "total_count_source": "exact",
                "truth_prior_max_delta": 2,
            }
        ]
    }
    capacity_acquisition = {
        "top_examples": [
            {
                "map_id": 2401,
                "acquisition_route": "activity_extras_table_verification_required",
                "next_check": "verify_activity_extras_table",
                "source_strength": "full_action_confirmed",
            }
        ]
    }
    activity_mapping = {
        "map_results": [
            {
                "map_id": "2524",
                "files": 3,
                "winner_counts": {"minus10": 2, "minus20": 1},
                "item_winner_counts": {"minus10": 2, "minus20": 1},
                "rankmap_labels": {"白色DOWN红色UP": 3},
                "rankmap_category_weight_profiles": {"profile": 3},
            }
        ],
        "file_results": [
            {
                "map_id": 2524,
                "candidates": [
                    {
                        "status": "ok",
                        "candidate_map_id": 2514,
                        "drop_pool_id": 2514,
                        "missing_item_rate": 0,
                    },
                    {
                        "status": "ok",
                        "candidate_map_id": 2504,
                        "drop_pool_id": 2504,
                        "missing_item_rate": 0,
                    },
                ],
            }
        ],
    }

    result = module.summarize_guard_loss_source_context(
        guard_doc=guard_doc,
        source_semantics=source_semantics,
        cse=cse,
        capacity_table=capacity_table,
        capacity_acquisition=capacity_acquisition,
        activity_mapping=activity_mapping,
    )

    assert result["status"] == "blocked_source_semantics_required"
    rows = {row["map_id"]: row for row in result["rows"]}
    assert rows["2410"]["status"] == "blocked_cse_source_semantics_intersection"
    assert rows["2410"]["reasons"] == [
        "cse_exact_session_capacity_source_semantics",
        "source_semantics_unique_round_overflow",
    ]
    assert rows["2410"]["next_checks"][0]["check"] == (
        "build_source_parser_for_session_capacity_or_round_cap_semantics"
    )
    assert rows["2410"]["evidence_examples"]["source_semantics"][0][
        "unique_residual_mode"
    ] == "unique_round_cap_overflow_after_temp"
    assert rows["2524"]["status"] == "blocked_drop_universe_or_activity_overlay"
    assert "cse_non_zodiac_drop_universe_gap" in rows["2524"]["reasons"]
    assert "activity_mapping_mixed_winner" in rows["2524"]["reasons"]
    assert {
        row["check"] for row in rows["2524"]["next_checks"]
    } >= {
        "build_activity_drop_universe_overlay_or_activity_source_parser",
        "treat_activity_mapping_as_reference_not_single_truth_table",
    }
    assert rows["2524"]["activity_mapping"]["status"] == (
        "watch_mixed_activity_mapping"
    )
    assert rows["2524"]["activity_mapping"]["winner_counts"] == {
        "minus10": 2,
        "minus20": 1,
    }
    assert rows["2524"]["evidence_examples"]["activity_mapping"][0][
        "candidate_examples"
    ][0]["candidate_map_id"] == 2514
    assert rows["2401"]["status"] == "watch_source_context_intersection"
    assert {
        row["check"] for row in rows["2401"]["next_checks"]
    } >= {
        "inspect_instance_round_source_semantics_detail",
        "verify_activity_extras_table_against_current_raw_version",
        "verify_activity_extras_table",
    }
    assert rows["2401"]["capacity_table"]["semantic_status_counts"] == {
        "watch_activity_extras_explain_drop_ref_gap": 1
    }
    assert rows["2401"]["capacity_acquisition_examples"][
        "acquisition_route_counts"
    ] == {"activity_extras_table_verification_required": 1}
    assert rows["2401"]["evidence_examples"]["capacity_acquisition"][0][
        "next_check"
    ] == "verify_activity_extras_table"
    assert result["summary"]["guard_loss_rows"] == 15
    assert result["summary"]["activity_mapping_maps"] == 1
