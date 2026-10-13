from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_source_parser_requirements_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_source_parser_requirements_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_source_parser_requirements_classifies_2410_style_blocker() -> None:
    module = _load_module()
    source_semantics = {
        "detail_rows": [
            {
                "file": "round.json",
                "map_id": 2410,
                "map_family": "villa",
                "unique_residual_mode": "unique_round_cap_overflow_after_temp",
                "mechanism_class": "session_capacity_source_semantics",
                "source_context_class": "payload_verified_empty_action_results",
                "source_evidence_class": "settlement_payload_verified_only",
                "inventory_count": 57,
                "unique_non_temp_item_id_count": 53,
                "bidmap_items_per_session_max": 40,
                "bidmap_raw_round_cap_max": 50,
                "unique_round_cap_excess_after_temp_zodiac_count": 3,
                "unique_drop_ref_excess_after_temp_zodiac_count": 13,
                "non_zodiac_missing_from_drop_universe_count": 0,
                "event_action_result_count_all": 2,
                "event_action_observed_item_count_max": 0,
                "event_public_total_count_values": [],
                "event_public_total_match": False,
            },
            {
                "file": "drop.json",
                "map_id": 2410,
                "map_family": "villa",
                "unique_residual_mode": "unique_drop_ref_only_overflow_after_temp",
                "mechanism_class": "not_unique_round_cap_blocker",
                "source_context_class": "payload_verified_partial_action_only",
                "source_evidence_class": "settlement_payload_verified_only",
                "inventory_count": 52,
                "unique_non_temp_item_id_count": 47,
                "unique_round_cap_excess_after_temp_zodiac_count": 0,
                "unique_drop_ref_excess_after_temp_zodiac_count": 7,
                "non_zodiac_missing_from_drop_universe_count": 0,
                "event_action_result_count_all": 5,
                "event_action_observed_item_count_max": 17,
            },
            {
                "file": "activity.json",
                "map_id": 2410,
                "map_family": "villa",
                "unique_residual_mode": "activity_extras_only_drop_ref_gap",
                "mechanism_class": "not_unique_round_cap_blocker",
                "source_context_class": "payload_verified_partial_action_only",
                "source_evidence_class": "settlement_payload_verified_only",
                "inventory_count": 40,
                "unique_non_temp_item_id_count": 38,
            },
        ],
    }
    guard_loss_context = {
        "rows": [
            {
                "map_id": "2410",
                "status": "blocked_cse_source_semantics_intersection",
                "reasons": ["source_semantics_unique_round_overflow"],
                "guard": {"p90_coverage_lost_rows": 8, "rows": 8},
            }
        ]
    }
    cse = {
        "entries": [
            {
                "scope": "map_id",
                "group": "2410",
                "status": "watch_capacity_source_expansion_shadow_only",
                "gate_reason": "observed_unique_round_over_cap_source_expansion",
                "archive_sessions": 19,
                "unique_round_overflow_rows": 1,
                "session_capacity_source_semantics_rows": 1,
                "server_side_expansion_rows": 0,
                "non_zodiac_missing_max": 0,
                "mechanism_classes": (
                    "not_unique_round_cap_blocker:18,"
                    "session_capacity_source_semantics:1"
                ),
                "source_context_classes": (
                    "payload_verified_empty_action_results:3,"
                    "payload_verified_partial_action_only:14"
                ),
            }
        ],
    }
    payload_only = {
        "rows": [
            {
                "file": "round.json",
                "map_id": 2410,
                "source_context_class": "payload_verified_empty_action_results",
                "mechanism_class": "session_capacity_source_semantics",
                "unique_round_excess_after_temp": 3,
                "map_id_holdout_covered": False,
                "prebid_cse_candidate_windows": 1,
                "prebid_pressure_windows": 0,
                "source_action_payload_shape_class": "numeric_only_result",
                "source_action_ids": {"100105": 2},
                "source_action_result_fields": {"14": 2},
                "source_action_result_blocks": 2,
                "source_action_item_payload_blocks": 0,
                "source_action_observed_item_count": 0,
            }
        ]
    }
    numeric_action_semantics = {
        "rows": [
            {
                "file": "round.json",
                "map_id": 2410,
                "sort_id": 17,
                "message_id": "0x0027",
                "block_source": "direct_action",
                "action_id": 100105,
                "result": 56,
                "result_field": 14,
                "expected_semantic": "bucket_total_cells",
                "expected_path": ["bucket", "3", "total_cells"],
                "expected_value": 56,
                "expected_match": True,
                "parser_implication": "not_session_capacity_signal",
                "status": "watch_expected_semantic_match",
                "inventory": {
                    "total_item_count": 57,
                    "warehouse_total_cells": 176,
                },
            },
            {
                "file": "round.json",
                "map_id": 2410,
                "sort_id": 28,
                "message_id": "0x002d",
                "block_source": "state_snapshot",
                "action_id": 100105,
                "result": 56,
                "result_field": 14,
                "expected_semantic": "bucket_total_cells",
                "expected_path": ["bucket", "3", "total_cells"],
                "expected_value": 56,
                "expected_match": True,
                "parser_implication": "not_session_capacity_signal",
                "status": "watch_expected_semantic_match",
                "inventory": {
                    "total_item_count": 57,
                    "warehouse_total_cells": 176,
                },
            },
        ]
    }
    session_capacity_source_gap = {
        "rows": [
            {
                "file": "round.json",
                "map_id": 2410,
                "map_family": "villa",
                "unique_residual_mode": "unique_round_cap_overflow_after_temp",
                "mechanism_class": "session_capacity_source_semantics",
                "source_context_class": "payload_verified_empty_action_results",
                "status": "blocked_session_capacity_source_gap_bucket_only",
                "event_source_digest": {
                    "inventory": {
                        "total_item_count": 57,
                        "warehouse_total_cells": 176,
                    },
                    "session_count_source_count": 0,
                    "warehouse_cells_source_count": 0,
                    "bucket_source_count": 2,
                    "action_id_counts": {"100105": 2},
                    "public_info_id_counts": {"200015": 2},
                    "skill_id_counts": {"1002081": 2},
                },
            }
        ]
    }
    payload_table_gap = {
        "rows": [
            {
                "file": "round.json",
                "map_id": 2410,
                "status": "blocked_payload_verified_table_cap_gap_without_full_source",
                "reasons": [
                    "payload_inventory_verified",
                    "no_full_event_payload_source",
                ],
                "next_checks": [
                    "check_per_session_table_version_or_external_overlay",
                    "inspect_server_side_settlement_expansion_or_source_transform",
                ],
                "table_delta": {
                    "inventory_count": 57,
                    "unique_non_temp_item_id_count": 53,
                    "bidmap_items_per_session_max": 40,
                    "bidmap_raw_round_cap_max": 50,
                    "inventory_minus_bidmap_items_per_session": 17,
                    "unique_non_temp_minus_bidmap_raw_round_cap": 3,
                },
                "payload": {
                    "inventory_slot_count": 250,
                    "occupied_slot_count": 57,
                    "raw_item_candidate_count": 57,
                    "raw_candidate_inventory_delta": 0,
                    "occupied_slot_inventory_delta": 0,
                },
                "event_payload": {
                    "full_action_payload_count": 0,
                    "full_skill_payload_count": 0,
                    "skill_observed_item_count_max": 35,
                },
            }
        ]
    }
    payload_outer_fields = {
        "rows": [
            {
                "file": "round.json",
                "map_id": 2410,
                "status": "watch_outer_fields_metadata_only",
                "metadata_matches": {
                    "payload_field2_matches_map_id": True,
                    "payload_field20_matches_capture_time": True,
                    "wrapper_field5_equals_loss_units": True,
                    "wrapper_field3_equals_field4": True,
                },
                "target_matches": [],
                "settlement_loss_units": 4107,
                "payload_field20_epoch_delta_seconds": 0,
                "next_checks": [
                    "check_per_session_table_version_or_external_overlay",
                    "inspect_server_side_settlement_expansion_or_source_transform",
                ],
            }
        ]
    }
    table_overlay_residual = {
        "rows": [
            {
                "file": "round.json",
                "map_id": 2410,
                "status": "blocked_table_overlay_or_server_side_residual",
                "mechanism_class": (
                    "table_version_or_external_overlay_or_"
                    "server_side_transform_required"
                ),
                "local_table_cap_gap": True,
                "current_table_cap_matches_payload_delta": True,
                "raw_table_newer_than_capture": True,
                "capture_has_table_version_or_hash": False,
                "drop_multiplicity_candidate": False,
                "activity_overlay_direct_candidate": False,
                "outer_fields_metadata_only": True,
                "remaining_minimal_hypotheses": [
                    "per_session_or_historical_table_version",
                    "external_overlay_table_not_in_current_raw_tables",
                    "server_side_settlement_expansion_or_source_transform",
                ],
                "disproven_or_weak_paths": [
                    "current_drop_leaf_nmax_not_count_expansion",
                    "current_activity_rankmap_not_direct_2410_source",
                ],
                "current_table_context": {
                    "raw_tables_file_version": "303",
                    "bidmap": {
                        "items_per_session_max": 40,
                        "round_cap_max": 50,
                    },
                    "drop": {"leaf_n_max_max": 1},
                    "activity_overlay": {"map_activity_range": None},
                },
            }
        ]
    }

    result = module.summarize_source_parser_requirements(
        source_semantics=source_semantics,
        guard_loss_context=guard_loss_context,
        cse=cse,
        payload_only=payload_only,
        numeric_action_semantics=numeric_action_semantics,
        session_capacity_source_gap=session_capacity_source_gap,
        payload_table_gap=payload_table_gap,
        payload_outer_fields=payload_outer_fields,
        table_overlay_residual=table_overlay_residual,
        focus_maps=["2410"],
    )

    assert result["status"] == "blocked_source_parser_required"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    row = result["rows"][0]
    assert row["status"] == "blocked_session_capacity_source_parser_required"
    requirements = {item["requirement"] for item in row["requirements"]}
    assert requirements >= {
        "parse_numeric_action_result_for_session_capacity_semantics",
        "decode_numeric_only_action_result_fields",
        "find_session_capacity_source_beyond_numeric_bucket_cells",
        "resolve_session_capacity_without_exact_event_source",
        "resolve_payload_verified_table_cap_gap_without_full_source",
        "check_table_overlay_or_server_side_after_outer_fields_metadata_only",
        "resolve_current_raw_table_overlay_or_server_transform_residual",
        "inspect_drop_ref_source_semantics_or_overlay",
        "separate_activity_extras_from_capacity_overflow",
        "link_parser_result_back_to_guard_loss_cases",
    }
    assert row["source_semantics"]["session_capacity_source_semantics_rows"] == 1
    assert row["source_semantics"]["unique_drop_ref_only_overflow_rows"] == 1
    assert row["source_semantics"]["activity_extras_only_rows"] == 1
    assert row["payload_only"]["numeric_only_result_rows"] == 1
    assert row["payload_only"]["source_action_result_fields"] == {"14": 2}
    assert row["numeric_action_semantics"]["rows"] == 2
    assert row["numeric_action_semantics"]["session_capacity_signal_rows"] == 0
    assert row["numeric_action_semantics"]["non_session_expected_rows"] == 2
    assert row["numeric_action_semantics"]["expected_semantic_counts"] == {
        "bucket_total_cells": 2
    }
    assert row["session_capacity_source_gap"]["rows"] == 1
    assert row["session_capacity_source_gap"]["exact_session_count_source_rows"] == 0
    assert row["session_capacity_source_gap"]["bucket_only_blocked_rows"] == 1
    assert row["evidence_examples"]["session_capacity_source_gap"][0][
        "session_count_source_count"
    ] == 0
    assert row["payload_table_gap"]["rows"] == 1
    assert row["payload_table_gap"]["blocked_rows"] == 1
    assert row["payload_table_gap"]["payload_verified_rows"] == 1
    assert row["payload_table_gap"]["no_full_event_payload_rows"] == 1
    assert row["evidence_examples"]["payload_table_gap"][0][
        "unique_non_temp_minus_bidmap_raw_round_cap"
    ] == 3
    assert row["payload_outer_fields"]["rows"] == 1
    assert row["payload_outer_fields"]["metadata_only_rows"] == 1
    assert row["payload_outer_fields"]["capacity_candidate_rows"] == 0
    assert row["table_overlay_residual"]["rows"] == 1
    assert row["table_overlay_residual"]["blocked_rows"] == 1
    assert row["table_overlay_residual"]["local_table_cap_gap_rows"] == 1
    assert row["table_overlay_residual"][
        "current_table_cap_matches_payload_delta_rows"
    ] == 1
    assert row["table_overlay_residual"][
        "activity_overlay_direct_candidate_rows"
    ] == 0
    assert row["table_overlay_residual"]["server_transform_open_rows"] == 1
    assert row["evidence_examples"]["payload_outer_fields"][0][
        "metadata_matches"
    ]["payload_field20_matches_capture_time"] is True
    assert row["evidence_examples"]["table_overlay_residual"][0][
        "raw_tables_file_version"
    ] == "303"
    assert row["evidence_examples"]["table_overlay_residual"][0][
        "drop_leaf_n_max_max"
    ] == 1
    assert row["evidence_examples"]["numeric_action_semantics"][0][
        "parser_implication"
    ] == "not_session_capacity_signal"
    assert result["summary"]["session_capacity_maps"] == 1
    assert result["summary"]["drop_ref_residual_maps"] == 1
    assert result["summary"]["numeric_action_semantics_maps"] == 1
    assert result["summary"]["numeric_action_rows"] == 2
    assert result["summary"]["numeric_session_capacity_signal_rows"] == 0
    assert result["summary"]["numeric_non_session_expected_rows"] == 2
    assert result["summary"]["session_capacity_source_gap_maps"] == 1
    assert result["summary"]["session_capacity_source_gap_rows"] == 1
    assert result["summary"]["session_gap_exact_session_count_source_rows"] == 0
    assert result["summary"]["session_gap_bucket_only_blocked_rows"] == 1
    assert result["summary"]["session_gap_unresolved_session_capacity_rows"] == 1
    assert result["summary"]["payload_table_gap_maps"] == 1
    assert result["summary"]["payload_table_gap_rows"] == 1
    assert result["summary"]["payload_table_gap_blocked_rows"] == 1
    assert result["summary"]["payload_table_gap_payload_verified_rows"] == 1
    assert result["summary"]["payload_table_gap_no_full_event_payload_rows"] == 1
    assert result["summary"]["payload_outer_field_maps"] == 1
    assert result["summary"]["payload_outer_field_rows"] == 1
    assert result["summary"]["payload_outer_field_metadata_only_rows"] == 1
    assert result["summary"]["payload_outer_field_capacity_candidate_rows"] == 0
    assert result["summary"]["table_overlay_residual_maps"] == 1
    assert result["summary"]["table_overlay_residual_rows"] == 1
    assert result["summary"]["table_overlay_residual_blocked_rows"] == 1
    assert result["summary"]["table_overlay_residual_local_cap_gap_rows"] == 1
    assert result["summary"]["table_overlay_residual_current_table_match_rows"] == 1
    assert result["summary"]["table_overlay_residual_activity_direct_rows"] == 0
    assert result["summary"]["table_overlay_residual_server_transform_open_rows"] == 1
