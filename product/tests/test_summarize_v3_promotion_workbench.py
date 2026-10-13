import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_promotion_workbench.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_promotion_workbench",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_workbench_marks_stop_loss_lanes() -> None:
    module = _load_module()

    result = module.summarize_workbench(
        {
            "overall_status": "not_ready",
            "blocked_gates": 2,
            "gate_dependencies": {
                "lane_status_counts": {
                    "settlement_bridge_support": {"blocked": 1, "watch": 1},
                    "table_activity_capacity": {"watch": 1},
                },
                "blocked_or_pending_gates": [
                    {
                        "gate": "settlement_count_guarded_bridge_stability",
                        "lane": "settlement_bridge_support",
                        "status": "blocked",
                    }
                ],
                "watch_gates": [
                    {
                        "gate": "settlement_count_guarded_bridge_holdout",
                        "lane": "settlement_bridge_support",
                        "status": "watch",
                    },
                    {
                        "gate": "capacity_source_expansion_shadow",
                        "lane": "table_activity_capacity",
                        "status": "watch",
                    },
                ],
            },
            "capacity_source_expansion_artifact_contract": {
                "status": "watch",
                "source_split_status": "blocked_payload_only_source_split_unresolved",
                "digest_scope": "map_id",
                "unique_round_overflow_rows": 21,
                "session_capacity_source_semantics_rows": 18,
                "server_side_expansion_rows": 3,
                "payload_verified_only_rows": 409,
                "mechanism_classes": {
                    "not_unique_round_cap_blocker": 435,
                    "session_capacity_source_semantics": 18,
                    "server_side_settlement_expansion": 3,
                },
                "source_evidence_classes": {
                    "settlement_payload_verified_only": 409,
                    "direct_action_matches_inventory": 18,
                },
                "source_context_classes": {
                    "payload_verified_partial_action_only": 349,
                    "direct_action_full_confirmed": 18,
                },
            },
            "capacity_table_audit_contract": {
                "status": "watch",
                "map_rows": 10,
                "semantic_matrix_cells": 12,
                "semantic_status_counts": {
                    "blocked_round_cap_overflow_after_temp": 14,
                    "blocked_drop_ref_overflow_after_temp": 12,
                },
                "residual_mode_counts": {
                    "round_cap_overflow": 14,
                    "drop_ref_only_overflow": 12,
                },
                "detail_mechanism_candidate_counts": {
                    "round_cap_candidate_gap": 6,
                    "drop_ref_candidate_gap": 8,
                },
                "detail_next_check_counts": {
                    "check_per_session_table_version_or_external_overlay": 6,
                    "check_drop_ref_source_semantics_or_activity_overlay": 8,
                },
                "detail_source_signal_counts": {
                    "has_full_action/no_public_total": 7,
                },
                "detail_unique_file_map_residual_rows": 15,
                "top_blocked_maps": [
                    {"map_id": "2601", "rows": 8},
                    {"map_id": "2501", "rows": 6},
                ],
                "top_detail_examples": [
                    {
                        "file": "fatbeans_valid_aisha_2601.json",
                        "map_id": "2601",
                        "next_check": (
                            "check_per_session_table_version_or_external_overlay"
                        ),
                    }
                ],
            },
            "capacity_table_acquisition_contract": {
                "status": "blocked",
                "acquisition_status": "blocked_acquisition_required",
                "detail_rows": 29,
                "unique_detail_rows": 15,
                "unique_files": 15,
                "route_counts": {
                    "table_version_or_external_overlay_required": 6,
                    "drop_ref_overlay_or_source_semantics_required": 4,
                },
                "source_strength_counts": {
                    "full_action_confirmed": 7,
                    "payload_only_or_unconfirmed": 6,
                },
                "map_counts": {"2601": 4},
                "table_overlay_metadata": {
                    "local_overlay_status": "activity_table_available_locally",
                    "activity_table_present": True,
                    "activity_table_parse_status": "ok",
                    "activity_table_rows": 6,
                    "activity_table_columns": 16,
                },
                "current_table_overlay_metadata": {
                    "local_overlay_status": "activity_table_available_locally",
                    "activity_table_present": True,
                },
                "artifact_table_overlay_metadata": {
                    "local_overlay_status": "v300_activity_listed_missing_locally",
                    "activity_table_present": False,
                },
                "table_overlay_metadata_stale": True,
                "table_overlay_metadata_delta": [
                    {
                        "key": "local_overlay_status",
                        "artifact": "v300_activity_listed_missing_locally",
                        "current": "activity_table_available_locally",
                    }
                ],
                "top_examples": [
                    {
                        "file": "fatbeans_valid_aisha_2601.json",
                        "map_id": "2601",
                        "acquisition_route": (
                            "table_version_or_external_overlay_required"
                        ),
                    }
                ],
            },
            "v3_practical_guard_loss_source_context_contract": {
                "status": "watch",
                "audit_status": "blocked_source_semantics_required",
                "maps": 5,
                "guard_loss_rows": 17,
                "status_counts": {
                    "blocked_cse_source_semantics_intersection": 1,
                    "blocked_drop_universe_or_activity_overlay": 1,
                    "watch_source_context_intersection": 3,
                },
                "cse_exact_overlap_maps": 5,
                "source_semantics_detail_maps": 3,
                "capacity_table_detail_maps": 2,
                "capacity_acquisition_example_maps": 2,
            },
            "activity_drop_universe_overlay_contract": {
                "status": "watch",
                "audit_status": "blocked_activity_overlay_source_required",
                "maps": 1,
                "files": 3,
                "guard_loss_overlap_maps": 1,
                "candidate_item_universe_covered_maps": 1,
                "hard_map_allowed": False,
                "hard_map_blocked_maps": 1,
                "status_counts": {
                    "blocked_mixed_overlay_source_required": 1,
                },
            },
            "source_parser_requirements_contract": {
                "status": "watch",
                "audit_status": "blocked_source_parser_required",
                "parser_required": True,
                "maps": 1,
                "blocked_maps": 1,
                "guard_loss_overlap_maps": 1,
                "session_capacity_maps": 1,
                "drop_ref_residual_maps": 1,
                "activity_extras_maps": 1,
                "numeric_action_semantics_maps": 1,
                "numeric_action_rows": 2,
                "numeric_session_capacity_signal_rows": 0,
                "numeric_non_session_expected_rows": 2,
                "numeric_unknown_semantic_rows": 0,
                "session_capacity_source_gap_maps": 1,
                "session_capacity_source_gap_rows": 22,
                "session_gap_exact_session_count_source_rows": 2,
                "session_gap_bucket_only_blocked_rows": 1,
                "session_gap_unresolved_session_capacity_rows": 1,
                "payload_table_gap_maps": 1,
                "payload_table_gap_rows": 1,
                "payload_table_gap_blocked_rows": 1,
                "payload_table_gap_payload_verified_rows": 1,
                "payload_table_gap_no_full_event_payload_rows": 1,
                "payload_outer_field_maps": 1,
                "payload_outer_field_rows": 1,
                "payload_outer_field_metadata_only_rows": 1,
                "payload_outer_field_capacity_candidate_rows": 0,
                "table_overlay_residual_maps": 1,
                "table_overlay_residual_rows": 1,
                "table_overlay_residual_blocked_rows": 1,
                "table_overlay_residual_local_cap_gap_rows": 1,
                "table_overlay_residual_current_table_match_rows": 1,
                "table_overlay_residual_activity_direct_rows": 0,
                "table_overlay_residual_server_transform_open_rows": 1,
                "requirement_counts": {
                    "parse_numeric_action_result_for_session_capacity_semantics": 1,
                    "find_session_capacity_source_beyond_numeric_bucket_cells": 1,
                    "resolve_session_capacity_without_exact_event_source": 1,
                    "resolve_payload_verified_table_cap_gap_without_full_source": 1,
                    "check_table_overlay_or_server_side_after_outer_fields_metadata_only": 1,
                    "resolve_current_raw_table_overlay_or_server_transform_residual": 1,
                    "inspect_drop_ref_source_semantics_or_overlay": 1,
                },
                "status_counts": {
                    "blocked_session_capacity_source_parser_required": 1,
                },
            },
        }
    )

    lanes = {row["lane"]: row for row in result["lanes"]}
    assert result["next_mode"] == "build_shadow_formal_value_workbench"
    assert lanes["settlement_bridge_support"]["verdict"] == "stop_loss"
    assert lanes["settlement_bridge_support"]["blocked_gates"] == [
        "settlement_count_guarded_bridge_stability"
    ]
    assert lanes["table_activity_capacity"]["verdict"] == "watch_only"
    contract = result["shadow_sampler_contract"]
    assert contract["status"] == "shadow_design_only"
    assert contract["shadow_only"] is True
    assert contract["affects_bid"] is False
    assert contract["can_change_live_or_formal"] is False
    assert contract["can_promote"] is False
    assert contract["stop_loss_lanes"] == ["settlement_bridge_support"]
    assert [
        row["gate"] for row in contract["frozen_gates"]
    ] == [
        "settlement_count_guarded_bridge_stability",
        "capacity_source_expansion_shadow",
    ]
    assert [
        row["gate"] for row in contract["watch_inputs"]
    ] == [
        "settlement_count_guarded_bridge_stability",
        "settlement_count_guarded_bridge_holdout",
        "capacity_source_expansion_shadow",
    ]
    assert "posterior seed stability" in contract["required_holdouts"]
    assert "support_gate_status" in contract["required_metrics"]
    assert "watch support rows/sessions gate" in contract["required_component_gates"]
    assert contract["capacity_source_expansion_contract"]["source_split_status"] == (
        "blocked_payload_only_source_split_unresolved"
    )
    assert contract["capacity_source_expansion_contract"][
        "session_capacity_source_semantics_rows"
    ] == 18
    assert contract["capacity_table_audit_contract"]["semantic_status_counts"][
        "blocked_round_cap_overflow_after_temp"
    ] == 14
    assert contract["capacity_table_audit_contract"]["top_blocked_maps"][0][
        "map_id"
    ] == "2601"
    assert contract["capacity_table_audit_contract"][
        "detail_next_check_counts"
    ]["check_drop_ref_source_semantics_or_activity_overlay"] == 8
    assert contract["capacity_table_audit_contract"][
        "detail_unique_file_map_residual_rows"
    ] == 15
    assert contract["capacity_table_audit_contract"]["top_detail_examples"][0][
        "file"
    ] == "fatbeans_valid_aisha_2601.json"
    acquisition = contract["capacity_table_acquisition_contract"]
    assert acquisition["status"] == "blocked"
    assert acquisition["route_counts"][
        "table_version_or_external_overlay_required"
    ] == 6
    assert acquisition["unique_detail_rows"] == 15
    assert acquisition["table_overlay_metadata"]["local_overlay_status"] == (
        "activity_table_available_locally"
    )
    assert acquisition["artifact_table_overlay_metadata"]["local_overlay_status"] == (
        "v300_activity_listed_missing_locally"
    )
    assert acquisition["table_overlay_metadata_stale"] is True
    guard_loss_context = contract["guard_loss_source_context_contract"]
    assert guard_loss_context["status"] == "watch"
    assert guard_loss_context["audit_status"] == "blocked_source_semantics_required"
    assert guard_loss_context["guard_loss_rows"] == 17
    assert guard_loss_context["status_counts"][
        "watch_source_context_intersection"
    ] == 3
    overlay = contract["activity_drop_universe_overlay_contract"]
    assert overlay["status"] == "watch"
    assert overlay["audit_status"] == "blocked_activity_overlay_source_required"
    assert overlay["candidate_item_universe_covered_maps"] == 1
    assert overlay["hard_map_blocked_maps"] == 1
    assert overlay["hard_map_allowed"] is False
    source_parser = contract["source_parser_requirements_contract"]
    assert source_parser["status"] == "watch"
    assert source_parser["audit_status"] == "blocked_source_parser_required"
    assert source_parser["session_capacity_maps"] == 1
    assert source_parser["drop_ref_residual_maps"] == 1
    assert source_parser["requirement_counts"][
        "parse_numeric_action_result_for_session_capacity_semantics"
    ] == 1
    assert source_parser["requirement_counts"][
        "find_session_capacity_source_beyond_numeric_bucket_cells"
    ] == 1
    assert source_parser["requirement_counts"][
        "resolve_session_capacity_without_exact_event_source"
    ] == 1
    assert source_parser["requirement_counts"][
        "resolve_payload_verified_table_cap_gap_without_full_source"
    ] == 1
    assert source_parser["requirement_counts"][
        "check_table_overlay_or_server_side_after_outer_fields_metadata_only"
    ] == 1
    assert source_parser["requirement_counts"][
        "resolve_current_raw_table_overlay_or_server_transform_residual"
    ] == 1
    assert source_parser["numeric_action_rows"] == 2
    assert source_parser["numeric_session_capacity_signal_rows"] == 0
    assert source_parser["numeric_non_session_expected_rows"] == 2
    assert source_parser["session_capacity_source_gap_rows"] == 22
    assert source_parser["session_gap_bucket_only_blocked_rows"] == 1
    assert source_parser["session_gap_unresolved_session_capacity_rows"] == 1
    assert source_parser["payload_table_gap_rows"] == 1
    assert source_parser["payload_table_gap_blocked_rows"] == 1
    assert source_parser["payload_table_gap_payload_verified_rows"] == 1
    assert source_parser["payload_outer_field_rows"] == 1
    assert source_parser["payload_outer_field_metadata_only_rows"] == 1
    assert source_parser["payload_outer_field_capacity_candidate_rows"] == 0
    assert source_parser["table_overlay_residual_rows"] == 1
    assert source_parser["table_overlay_residual_blocked_rows"] == 1
    assert source_parser["table_overlay_residual_local_cap_gap_rows"] == 1
    assert source_parser["table_overlay_residual_current_table_match_rows"] == 1
    assert source_parser["table_overlay_residual_activity_direct_rows"] == 0
    assert source_parser["table_overlay_residual_server_transform_open_rows"] == 1
    assert contract["prototype_contract"]["status"] == "missing"
    assert "change formal bid path" in contract["blocked_actions"]


def test_workbench_keeps_archive_quality_as_usable_watch() -> None:
    module = _load_module()

    result = module.summarize_workbench(
        {
            "overall_status": "not_ready",
            "blocked_gates": 0,
            "gate_dependencies": {
                "lane_status_counts": {
                    "archive_pipeline_quality": {"pass": 1, "watch": 1}
                },
                "blocked_or_pending_gates": [],
                "watch_gates": [
                    {
                        "gate": "archive_data_quality",
                        "lane": "archive_pipeline_quality",
                        "status": "watch",
                    }
                ],
            },
        }
    )

    assert result["lanes"][0]["lane"] == "archive_pipeline_quality"
    assert result["lanes"][0]["verdict"] == "usable_watch"
    assert (
        result["shadow_sampler_contract"]["status"]
        == "ready_for_shadow_prototype"
    )
    assert result["shadow_sampler_contract"]["prototype_contract"]["status"] == "missing"


def test_shadow_sampler_contract_blocks_pending_prerequisites() -> None:
    module = _load_module()

    result = module.summarize_workbench(
        {
            "overall_status": "not_ready",
            "blocked_gates": 1,
            "gate_dependencies": {
                "lane_status_counts": {
                    "profile_sample_depth": {"blocked": 1},
                },
                "blocked_or_pending_gates": [
                    {
                        "gate": "profile_sample_depth",
                        "lane": "profile_sample_depth",
                        "status": "blocked",
                        "focus": "low_sample_profiles=7",
                    }
                ],
                "watch_gates": [],
            },
        }
    )

    contract = result["shadow_sampler_contract"]
    assert contract["status"] == "blocked_pending_prerequisites"
    assert contract["can_start_shadow_prototype"] is False
    assert contract["blocking_gates"] == [
        {
            "gate": "profile_sample_depth",
            "lane": "profile_sample_depth",
            "status": "blocked",
            "focus": "low_sample_profiles=7",
        }
    ]


def test_shadow_sampler_contract_blocks_attached_prototype_blockers() -> None:
    module = _load_module()

    result = module.summarize_workbench(
        {
            "overall_status": "not_ready",
            "blocked_gates": 0,
            "gate_dependencies": {
                "lane_status_counts": {},
                "blocked_or_pending_gates": [],
                "watch_gates": [],
            },
            "shadow_sampler_prototype": {
                "interface": "v3_ccvc_evidence_driven_count_cell_value_sampler",
                "shadow_only": True,
                "affects_bid": False,
                "active": False,
                "status": "blocked_seed_instability",
                "posterior_seeds": [0, 1],
                "stable_watch_candidate_labels": [],
                "min_watch_support_rows": 20,
                "min_watch_support_sessions": 8,
                "component_statuses": [
                    {
                        "component": "q6_count",
                        "status": "blocked_seed_instability",
                        "support_gate": {
                            "status": "watch_low_support",
                            "low_support_watch_metrics": [
                                {
                                    "posterior_seed": 1,
                                    "watch_label": (
                                        "q6_count|map_id,evidence_profile_key|"
                                        "down_only:q6_count:map_id=2501|"
                                        "evidence_profile_key=tool:category+item+shape"
                                    ),
                                    "support_rows": 8,
                                    "support_sessions": 3,
                                }
                            ],
                            "stable_low_support_watch_metrics": [],
                        },
                    }
                ],
                "guard_trial_contract": {
                    "interface": "v3_ccvc_shadow_sampler_guard_trial_contract",
                    "status": "requires_source_support_gate",
                    "shadow_only": True,
                    "affects_bid": False,
                    "active": False,
                    "can_promote": False,
                    "action_counts": {"require_source_support_gate": 1},
                    "component_actions": [
                        {
                            "component": "q6_count",
                            "trial_action": "require_source_support_gate",
                            "requires_source_parser": True,
                        }
                    ],
                },
            },
        }
    )

    contract = result["shadow_sampler_contract"]
    prototype = contract["prototype_contract"]
    assert contract["status"] == "shadow_prototype_blocked"
    assert contract["can_start_shadow_prototype"] is False
    assert prototype["status"] == "blocked"
    assert prototype["prototype_status"] == "blocked_seed_instability"
    assert prototype["component_status_counts"] == {"blocked_seed_instability": 1}
    assert prototype["support_gate_status_counts"] == {"watch_low_support": 1}
    assert prototype["guard_trial_status"] == "requires_source_support_gate"
    assert prototype["guard_trial_action_counts"] == {
        "require_source_support_gate": 1
    }
    assert prototype["guard_trial_contract"]["component_actions"][0][
        "requires_source_parser"
    ] is True
    assert prototype["blocking_component_statuses"] == [
        {
            "component": "q6_count",
            "status": "blocked_seed_instability",
            "support_gate": "watch_low_support",
        }
    ]
    assert prototype["low_support_watch_metrics"][0]["support_rows"] == 8


def test_shadow_sampler_contract_blocks_attached_guarded_trial() -> None:
    module = _load_module()

    result = module.summarize_workbench(
        {
            "overall_status": "not_ready",
            "blocked_gates": 0,
            "gate_dependencies": {
                "lane_status_counts": {},
                "blocked_or_pending_gates": [],
                "watch_gates": [],
            },
            "shadow_sampler_guard_trial": {
                "interface": "v3_ccvc_shadow_sampler_guarded_trial",
                "status": "blocked_guarded_shadow_trial",
                "sampler_status": "blocked_seed_instability",
                "shadow_only": True,
                "affects_bid": False,
                "active": False,
                "can_promote": False,
                "source_prototype_status": "blocked_seed_instability",
                "source_guard_trial_status": "shadow_guard_trial_design",
                "trial_options": {
                    "component_move_cells": False,
                    "excluded_components": ["q6_cells", "q6_count"],
                    "candidate_exclude_labels": ["q6_value:2510"],
                },
                "component_status_counts": {
                    "blocked_seed_instability": 1,
                    "sample_limited": 2,
                },
                "support_gate_status_counts": {"no_watch": 2, "pass": 1},
                "guarded_sampler_result": {
                    "component_statuses": [
                        {
                            "component": "q6_value",
                            "status": "blocked_seed_instability",
                            "support_gate": {"status": "pass"},
                        }
                    ]
                },
            },
        }
    )

    contract = result["shadow_sampler_contract"]
    trial = contract["guarded_trial_contract"]
    assert contract["status"] == "shadow_guarded_trial_blocked"
    assert contract["can_start_shadow_prototype"] is False
    assert trial["status"] == "blocked"
    assert trial["trial_status"] == "blocked_guarded_shadow_trial"
    assert trial["sampler_status"] == "blocked_seed_instability"
    assert trial["component_status_counts"] == {
        "blocked_seed_instability": 1,
        "sample_limited": 2,
    }
    assert trial["support_gate_status_counts"] == {"no_watch": 2, "pass": 1}
    assert trial["trial_options"]["excluded_components"] == [
        "q6_cells",
        "q6_count",
    ]


def test_shadow_sampler_contract_blocks_attached_value_source_profile_audit() -> None:
    module = _load_module()

    result = module.summarize_workbench(
        {
            "overall_status": "not_ready",
            "blocked_gates": 0,
            "gate_dependencies": {
                "lane_status_counts": {},
                "blocked_or_pending_gates": [],
                "watch_gates": [],
            },
            "shadow_sampler_value_source_profile_audit": {
                "interface": "v3_ccvc_q6_value_source_profile_audit",
                "status": "blocked_risk_migration",
                "shadow_only": True,
                "affects_bid": False,
                "active": False,
                "can_promote": False,
                "component": "q6_value",
                "run_count": 2,
                "runs": [
                    {
                        "label": "baseline",
                        "audit_probe": False,
                        "component_status": "blocked_seed_instability",
                        "sampler_status": "blocked_seed_instability",
                        "support_gate": "pass",
                        "source_profile_parser_required": True,
                        "hurt_label_count": 2,
                        "hurt_map_ids": ["2510"],
                        "hurt_evidence_profiles": ["public:total+item+shape"],
                        "hurt_group_field_counts": {"map_id": 1},
                    },
                    {
                        "label": "probe",
                        "audit_probe": True,
                        "component_status": "blocked_seed_instability",
                        "sampler_status": "blocked_seed_instability",
                        "support_gate": "watch_low_support",
                        "source_profile_parser_required": True,
                        "hurt_label_count": 2,
                        "hurt_map_ids": ["2405"],
                        "hurt_evidence_profiles": [
                            "public:max_item_cells+item+shape"
                        ],
                        "hurt_group_field_counts": {"evidence_profile_key": 1},
                    },
                ],
                "migration": {
                    "status": "evaluated",
                    "risk_migration_detected": True,
                    "introduced_hurt_labels": [
                        "q6_value|map_id|up_only:q6_value:2405"
                    ],
                    "removed_hurt_labels": [
                        "q6_value|map_id|up_only:q6_value:2510"
                    ],
                },
                "source_profile_parser": {
                    "status": "blocked_mixed_map_profile_risk",
                    "profile_semantic_migration_detected": True,
                    "latest_map_only_hurt_label_count": 1,
                    "latest_profile_hurt_label_count": 1,
                },
                "next_action": (
                    "stop adding manual q6_value excludes; design "
                    "source/profile parser or higher-level value guard"
                ),
            },
        }
    )

    contract = result["shadow_sampler_contract"]
    value_source_profile = contract["value_source_profile_contract"]
    assert contract["status"] == "shadow_value_source_profile_blocked"
    assert contract["can_start_shadow_prototype"] is False
    assert value_source_profile["status"] == "blocked"
    assert value_source_profile["audit_status"] == "blocked_risk_migration"
    assert value_source_profile["risk_migration_detected"] is True
    assert value_source_profile["source_profile_parser_status"] == (
        "blocked_mixed_map_profile_risk"
    )
    assert value_source_profile["latest_map_only_hurt_label_count"] == 1
    assert value_source_profile["run_summaries"][1]["support_gate"] == (
        "watch_low_support"
    )


def test_shadow_sampler_contract_blocks_attached_value_map_profile_details() -> None:
    module = _load_module()

    result = module.summarize_workbench(
        {
            "overall_status": "not_ready",
            "blocked_gates": 0,
            "gate_dependencies": {
                "lane_status_counts": {},
                "blocked_or_pending_gates": [],
                "watch_gates": [],
            },
            "shadow_sampler_value_map_profile_details": {
                "interface": "v3_ccvc_q6_value_map_profile_details",
                "status": "blocked_map_only_details_ready",
                "shadow_only": True,
                "affects_bid": False,
                "active": False,
                "can_promote": False,
                "component": "q6_value",
                "source_audit_status": "blocked_risk_migration",
                "source_profile_parser_status": "blocked_mixed_map_profile_risk",
                "label_count": 1,
                "candidate_rows": 16,
                "candidate_sessions_sum": 5,
                "labels_with_row_count_mismatch": [],
                "labels": [
                    {
                        "watch_label": "q6_value|map_id|up_only:q6_value:2502",
                        "candidate_rows": 16,
                    }
                ],
                "next_action": "review q6_value map-only row/source clusters",
            },
        }
    )

    contract = result["shadow_sampler_contract"]
    details = contract["value_map_profile_details_contract"]
    assert contract["status"] == "shadow_value_map_profile_details_blocked"
    assert contract["can_start_shadow_prototype"] is False
    assert details["status"] == "blocked"
    assert details["details_status"] == "blocked_map_only_details_ready"
    assert details["source_profile_parser_status"] == (
        "blocked_mixed_map_profile_risk"
    )
    assert details["candidate_rows"] == 16
    assert details["labels_with_row_count_mismatch"] == []


def test_shadow_sampler_contract_blocks_attached_value_profile_guardability() -> None:
    module = _load_module()

    result = module.summarize_workbench(
        {
            "overall_status": "not_ready",
            "blocked_gates": 0,
            "gate_dependencies": {
                "lane_status_counts": {},
                "blocked_or_pending_gates": [],
                "watch_gates": [],
            },
            "shadow_sampler_value_profile_guardability": {
                "interface": "v3_ccvc_q6_value_profile_guardability",
                "status": "blocked_no_stable_profile_guard",
                "shadow_only": True,
                "affects_bid": False,
                "active": False,
                "can_promote": False,
                "component": "q6_value",
                "source_details_status": "blocked_map_only_details_ready",
                "detail_rows": 168,
                "cluster_count": 120,
                "candidate_cluster_count": 0,
                "overfit_risk_cluster_count": 0,
                "mixed_cluster_count": 0,
                "next_action": (
                    "keep q6_value inactive; current source/profile clusters "
                    "do not separate hurt from helped rows"
                ),
            },
        }
    )

    contract = result["shadow_sampler_contract"]
    guardability = contract["value_profile_guardability_contract"]
    assert contract["status"] == "shadow_value_profile_guardability_blocked"
    assert contract["can_start_shadow_prototype"] is False
    assert guardability["status"] == "blocked"
    assert guardability["guardability_status"] == "blocked_no_stable_profile_guard"
    assert guardability["source_details_status"] == "blocked_map_only_details_ready"
    assert guardability["detail_rows"] == 168
    assert guardability["cluster_count"] == 120
    assert guardability["candidate_cluster_count"] == 0
