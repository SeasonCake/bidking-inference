import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_promotion_readiness.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_promotion_readiness",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(
    group: str,
    *,
    session_id: str,
    truth: int,
    pred: int,
    p90: int,
) -> dict[str, object]:
    return {
        "status": "ready",
        "round": 1,
        "session_id": session_id,
        "hero": group.split("|", 1)[0],
        "hero_map_id": group,
        "hero_map_evidence_profile": f"{group}|item+shape",
        "evidence_stage": "early_1_2",
        "information_density_band": "medium",
        "evidence_profile_key": "item+shape",
        "numeric_constraints": 1,
        "item_anchors": 1,
        "shape_anchors": 1,
        "quality_floor_anchors": 1,
        "v3_prior_available": True,
        "v3_robust_available": True,
        "v3_robust_affects_bid": False,
        "v3_robust_status": "ok",
        "v3_robust_prior_usable": True,
        "v3_robust_prior_trusted": True,
        "v3_robust_fallback_mode": "normal_prior",
        "v3_robust_activity_candidate": False,
        "v3_robust_prior_stress_score": 0,
        "v3_robust_reasons": "",
        "v3_truth_available": True,
        "v3_truth_decision_available": True,
        "v3_summary_available": True,
        "v3_summary_conflict_count": 0,
        "v3_summary_session_total_cells_exact": 80,
        "v3_summary_q6_cells_floor": 4,
        "v3_post_available": True,
        "v3_post_ready": True,
        "v3_post_strict_ready": False,
        "v3_post_match_scope": "summary_likelihood",
        "v3_post_formal_decision_value_p50": pred,
        "v3_post_formal_decision_value_p90": p90,
        "v3_post_q6_formal_decision_value_p50": pred // 2,
        "v3_post_q6_formal_decision_value_p90": p90 // 2,
        "v3_post_q6_count_p50": 2,
        "v3_post_q6_count_p90": 3,
        "v3_post_q6_cells_p50": 8,
        "v3_post_q6_cells_p90": 12,
        "v3_post_q6_value_p50": pred // 2,
        "v3_truth_formal_decision_value": truth,
        "v3_truth_tail_replacement_decision_value": truth + 100,
        "v3_truth_tail_replacement_value": 100,
        "v3_truth_q6_formal_decision_value": truth // 2,
        "v3_truth_q6_tail_replacement_decision_value": truth // 2 + 100,
        "v3_truth_q6_tail_replacement_value": 100,
        "v3_truth_q6_count": 2,
        "v3_truth_q6_cells": 8,
        "v3_truth_q6_raw_value": truth // 2,
        "v3_ccv_ready": True,
        "v3_ccv_match_scope": "ccv_likelihood",
        "v3_ccv_q6_count_p50": 2,
        "v3_ccv_q6_count_p90": 3,
        "v3_ccv_q6_cells_p50": 8,
        "v3_ccv_q6_cells_p90": 12,
        "v3_ccv_q6_value_p50": truth // 2,
        "v3_ccv_q6_formal_decision_value_p50": truth // 2,
        "v3_resid_ready": True,
        "v3_resid_match_scope": "residual_likelihood",
        "v3_resid_q6_count_p50": 2,
        "v3_resid_q6_count_p90": 3,
        "v3_resid_q6_cells_p50": 8,
        "v3_resid_q6_cells_p90": 12,
        "v3_resid_q6_value_p50": truth // 2,
        "v3_resid_gate_ready": True,
        "v3_resid_gate_active": False,
        "v3_resid_gate_q6_count_p50": 2,
        "v3_resid_gate_q6_count_p90": 3,
        "v3_resid_gate_q6_cells_p50": 8,
        "v3_resid_gate_q6_cells_p90": 12,
        "v3_resid_gate_q6_value_p50": truth // 2,
        "v3_cal_ready": True,
        "v3_cal_active": False,
        "v3_cal_formal_decision_value_p50": pred,
        "v3_cal_formal_decision_value_p90": p90,
        "v3_cal_q6_formal_decision_value_p50": pred // 2,
        "v3_cal_q6_formal_decision_value_p90": p90 // 2,
        "v3_under_ready": True,
        "v3_under_active": False,
        "v3_under_candidate": False,
        "v3_under_formal_decision_value_p50": pred,
        "v3_under_formal_decision_value_p90": p90,
        "v3_under_q6_formal_decision_value_p50": pred // 2,
        "v3_under_q6_formal_decision_value_p90": p90 // 2,
        "v3_fv_ready": True,
        "v3_fv_affects_bid": False,
        "v3_fv_active": False,
        "v3_fv_candidate": False,
        "v3_fv_stress_class": "none",
        "v3_fv_formal_decision_value_p50": pred,
        "v3_fv_formal_decision_value_p90": p90,
        "v3_fv_q6_formal_decision_value_p50": pred // 2,
        "v3_fv_q6_formal_decision_value_p90": p90 // 2,
        "v3_scp_ready": True,
        "v3_scp_active": False,
        "v3_scp_candidate": False,
        "v3_scp_missing_table": False,
        "v3_scp_status": "table_caps_cover_observed_shadow_only",
        "v3_cse_ready": True,
        "v3_cse_affects_bid": False,
        "v3_cse_active": False,
        "v3_cse_candidate": False,
        "v3_cse_pressure_candidate": False,
        "v3_cse_status": "within_capacity_source_semantics_shadow_only",
        "v3_post_tail_replacement_decision_value_p50": pred + 100,
        "v3_post_tail_replacement_decision_value_p90": p90 + 100,
        "v3_post_q6_tail_replacement_decision_value_p50": pred // 2 + 100,
        "v3_post_q6_tail_replacement_decision_value_p90": p90 // 2 + 100,
    }


def test_readiness_blocks_formal_when_below_rate_is_high() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
    )

    assert result["overall_status"] == "not_ready"
    gates = {row["name"]: row for row in result["gates"]}
    assert gates["archive_data_quality"]["status"] == "pass"
    assert gates["shared_shadow_pipeline"]["status"] == "pass"
    assert gates["v3_practical_archive_live_guard_metrics"]["status"] == "pending"
    assert gates["prior_robustness"]["status"] == "pass"
    assert gates["prior_stress_capacity_table_drift"]["status"] == "pass"
    assert gates["settlement_count_prior_shadow"]["status"] == "watch"
    assert gates["settlement_count_prior_shadow"]["active_rows"] == 0
    assert gates["capacity_source_expansion_shadow"]["status"] == "watch"
    assert gates["capacity_source_expansion_shadow"]["active_rows"] == 0
    assert gates["capacity_source_expansion_shadow"]["pressure_candidate_rows"] == 0
    assert gates["settlement_count_formal_value_link"]["status"] == "blocked"
    assert gates["settlement_count_formal_value_link"]["scp_candidate_formal_rows"] == 0
    assert gates["settlement_count_cells_value_bridge"]["status"] == "blocked"
    assert gates["settlement_count_cells_value_bridge"]["count_cells_value_bridge_rows"] == 0
    assert gates["settlement_count_cells_value_bridge_holdout"]["status"] == "blocked"
    assert gates["settlement_count_cells_value_bridge_holdout"]["applied_rows"] == 0
    assert gates["settlement_count_guarded_bridge_holdout"]["status"] == "blocked"
    assert gates["settlement_count_guarded_bridge_holdout"]["applied_rows"] == 0
    assert gates["settlement_count_guarded_bridge_stability"]["status"] == "blocked"
    assert gates["settlement_count_guarded_bridge_stability"]["overall_status"] == (
        "not_evaluated"
    )
    assert gates["formal_baseline_metrics"]["status"] == "blocked"
    assert "holdout_candidate_rows" in gates["ccv_sampler"]
    assert "applied_ccv_hurts_groups" in gates["ccv_sampler"]
    assert "ccv_directionality" in gates
    assert "map_direction_hurts" in gates["ccv_directionality"]
    assert "ccv_direction_holdout" in gates
    assert "map_candidate_rows" in gates["ccv_direction_holdout"]
    assert "shadow_sampler_prototype" not in gates
    assert "holdout_candidate_rows" in gates["tail_value_review"]
    assert "tail_under_combined_holdout" in gates
    assert "formal_value_sampler_holdout" in gates
    assert "candidate_rows" in gates["formal_value_sampler_holdout"]
    assert "mixed_value_floor_watch_rows" in gates["formal_value_sampler_holdout"]
    assert gates["v2_archive_readiness"]["status"] == "pending"
    assert "ccv_holdout" in result
    assert "applied_ccv_hurts_groups" in result["ccv_holdout"]
    assert "map_applied_ccv_hurts_groups" in result["ccv_holdout"]
    assert "ccv_directionality" in result
    assert "ccv_direction_holdout" in result
    assert "tail_holdout" in result
    assert "tail_under_holdout" in result
    assert "formal_value_sampler_holdout" in result
    assert "mixed_value_floor_watch_rows" in result["formal_value_sampler_holdout"]
    assert "settlement_count_formal_value_link" in result
    assert "settlement_count_cells_value_bridge" in result
    assert "settlement_count_cells_value_bridge_holdout" in result
    assert "settlement_count_guarded_bridge_holdout" in result
    assert "settlement_count_guarded_bridge_stability" in result
    assert "gate_dependencies" in result
    dependencies = result["gate_dependencies"]
    assert "formal_value_shadow_sampler" in dependencies["blocked_or_pending_lanes"]
    assert "settlement_bridge_support" in dependencies["blocked_or_pending_lanes"]
    assert "v2_archive_after_promotion" in dependencies["blocked_or_pending_lanes"]
    dependency_gates = {
        row["gate"]: row for row in dependencies["blocked_or_pending_gates"]
    }
    assert dependency_gates["formal_baseline_metrics"]["lane"] == (
        "formal_value_shadow_sampler"
    )
    assert dependency_gates["settlement_count_guarded_bridge_holdout"]["lane"] == (
        "settlement_bridge_support"
    )
    assert dependency_gates["settlement_count_guarded_bridge_stability"]["lane"] == (
        "settlement_bridge_support"
    )
    assert dependency_gates["v2_archive_readiness"]["status"] == "pending"
    assert "prior_stress_detail_summary" in result
    assert result["prior_stress_detail_summary"]["rows"] == 0
    assert result["v3_practical_archive_live_guard_metrics"]["status"] == "not_supplied"
    assert result["shadow_sampler_prototype_contract"]["status"] == "not_supplied"
    assert (
        "attach v3 practical archive-live guard brief JSON before promotion review"
        in result["next_actions"]
    )


def test_readiness_attaches_shadow_sampler_prototype_contract() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]
    prototype = {
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
                                "q6_count|map_id,evidence_profile_key|down_only:"
                                "q6_count:map_id=2501|"
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
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        shadow_sampler_prototype=prototype,
    )

    gates = {row["name"]: row for row in result["gates"]}
    gate = gates["shadow_sampler_prototype"]
    assert gate["status"] == "blocked"
    assert gate["contract_status"] == "blocked"
    assert gate["prototype_status"] == "blocked_seed_instability"
    assert gate["component_status_counts"] == {"blocked_seed_instability": 1}
    assert gate["support_gate_status_counts"] == {"watch_low_support": 1}
    assert gate["guard_trial_status"] == "requires_source_support_gate"
    assert gate["guard_trial_action_counts"] == {
        "require_source_support_gate": 1
    }
    assert gate["low_support_watch_metrics"][0]["support_rows"] == 8
    contract = result["shadow_sampler_prototype_contract"]
    assert contract["status"] == "blocked"
    assert contract["shadow_safe"] is True
    assert contract["blocking_component_statuses"] == [
        {
            "component": "q6_count",
            "status": "blocked_seed_instability",
            "support_gate": "watch_low_support",
        }
    ]
    assert contract["guard_trial_status"] == "requires_source_support_gate"
    assert contract["guard_trial_contract"]["component_actions"][0][
        "requires_source_parser"
    ] is True
    assert result["shadow_sampler_prototype"] == prototype
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    assert dependency_gates["shadow_sampler_prototype"]["lane"] == (
        "sampler_safety_holdout"
    )


def test_readiness_attaches_shadow_sampler_guard_trial_contract() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]
    trial = {
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
        "required_verification": ["archive", "posterior_seed"],
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        shadow_sampler_guard_trial=trial,
    )

    gates = {row["name"]: row for row in result["gates"]}
    gate = gates["shadow_sampler_guard_trial"]
    assert gate["status"] == "blocked"
    assert gate["contract_status"] == "blocked"
    assert gate["trial_status"] == "blocked_guarded_shadow_trial"
    assert gate["sampler_status"] == "blocked_seed_instability"
    assert gate["component_status_counts"] == {
        "blocked_seed_instability": 1,
        "sample_limited": 2,
    }
    contract = result["shadow_sampler_guard_trial_contract"]
    assert contract["status"] == "blocked"
    assert contract["trial_options"]["component_move_cells"] is False
    assert contract["blocking_component_statuses"] == [
        {
            "component": "q6_value",
            "status": "blocked_seed_instability",
            "support_gate": "pass",
        }
    ]
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    assert dependency_gates["shadow_sampler_guard_trial"]["lane"] == (
        "sampler_safety_holdout"
    )


def test_readiness_attaches_shadow_sampler_value_source_profile_audit() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]
    audit = {
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
                "hurt_evidence_profiles": ["public:max_item_cells+item+shape"],
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
            "stop adding manual q6_value excludes; design source/profile "
            "parser or higher-level value guard"
        ),
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        shadow_sampler_value_source_profile_audit=audit,
    )

    gates = {row["name"]: row for row in result["gates"]}
    gate = gates["shadow_sampler_value_source_profile_audit"]
    assert gate["status"] == "blocked"
    assert gate["contract_status"] == "blocked"
    assert gate["audit_status"] == "blocked_risk_migration"
    assert gate["component"] == "q6_value"
    assert gate["risk_migration_detected"] is True
    assert gate["source_profile_parser_status"] == (
        "blocked_mixed_map_profile_risk"
    )
    assert gate["latest_map_only_hurt_label_count"] == 1
    assert gate["introduced_hurt_labels"] == [
        "q6_value|map_id|up_only:q6_value:2405"
    ]
    contract = result["shadow_sampler_value_source_profile_contract"]
    assert contract["status"] == "blocked"
    assert contract["shadow_safe"] is True
    assert contract["profile_semantic_migration_detected"] is True
    assert contract["run_summaries"][1]["support_gate"] == "watch_low_support"
    assert result["shadow_sampler_value_source_profile_audit"] == audit
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    assert dependency_gates["shadow_sampler_value_source_profile_audit"]["lane"] == (
        "sampler_safety_holdout"
    )


def test_readiness_attaches_shadow_sampler_value_map_profile_details() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]
    details = {
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
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        shadow_sampler_value_map_profile_details=details,
    )

    gates = {row["name"]: row for row in result["gates"]}
    gate = gates["shadow_sampler_value_map_profile_details"]
    assert gate["status"] == "blocked"
    assert gate["contract_status"] == "blocked"
    assert gate["details_status"] == "blocked_map_only_details_ready"
    assert gate["label_count"] == 1
    assert gate["candidate_rows"] == 16
    contract = result["shadow_sampler_value_map_profile_details_contract"]
    assert contract["status"] == "blocked"
    assert contract["shadow_safe"] is True
    assert contract["labels_with_row_count_mismatch"] == []
    assert result["shadow_sampler_value_map_profile_details"] == details
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    assert dependency_gates["shadow_sampler_value_map_profile_details"]["lane"] == (
        "sampler_safety_holdout"
    )


def test_readiness_attaches_shadow_sampler_value_profile_guardability() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]
    guardability = {
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
            "keep q6_value inactive; current source/profile clusters do not "
            "separate hurt from helped rows"
        ),
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        shadow_sampler_value_profile_guardability=guardability,
    )

    gates = {row["name"]: row for row in result["gates"]}
    gate = gates["shadow_sampler_value_profile_guardability"]
    assert gate["status"] == "blocked"
    assert gate["contract_status"] == "blocked"
    assert gate["guardability_status"] == "blocked_no_stable_profile_guard"
    assert gate["detail_rows"] == 168
    assert gate["cluster_count"] == 120
    assert gate["candidate_cluster_count"] == 0
    contract = result["shadow_sampler_value_profile_guardability_contract"]
    assert contract["status"] == "blocked"
    assert contract["shadow_safe"] is True
    assert contract["source_details_status"] == "blocked_map_only_details_ready"
    assert result["shadow_sampler_value_profile_guardability"] == guardability
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    assert dependency_gates["shadow_sampler_value_profile_guardability"]["lane"] == (
        "sampler_safety_holdout"
    )


def test_readiness_attaches_live_practical_guard_brief() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]
    policy_matrix = {
        "status": "watch",
        "comparison_rows": 23,
        "policies": {
            "v2": {
                "rows": 23,
                "p50_mae": 130_000,
                "p50_under_rate": 0.6,
                "p90_coverage": 0.8,
                "p90_extreme_over_rate": 0.2,
            },
            "v3_guarded": {
                "rows": 23,
                "p50_mae": 120_000,
                "p50_under_rate": 0.6,
                "p90_coverage": 0.57,
                "p90_extreme_over_rate": 0.09,
            },
            "v3_unguarded": {
                "rows": 23,
                "p50_mae": 120_000,
                "p50_under_rate": 0.6,
                "p90_coverage": 1.0,
                "p90_extreme_over_rate": 0.57,
            },
        },
        "deltas_vs_v2": {
            "v3_guarded": {
                "p50_mae_delta": -10_000,
                "p90_coverage_delta": -0.23,
                "p90_extreme_over_delta": -0.11,
            },
            "v3_unguarded": {
                "p50_mae_delta": -10_000,
                "p90_coverage_delta": 0.2,
                "p90_extreme_over_delta": 0.37,
            },
        },
    }
    guard_case_summary = {
        "rows": 23,
        "p50_improved_rows": 4,
        "p50_worsened_rows": 2,
        "p50_unchanged_rows": 17,
        "p90_coverage_gained_rows": 1,
        "p90_coverage_lost_rows": 3,
        "p90_coverage_unchanged_rows": 19,
        "p90_extreme_over_reduced_rows": 8,
        "p90_extreme_over_added_rows": 0,
        "p90_extreme_over_unchanged_rows": 15,
        "p90_coverage_loss_context": {
            "rows": 3,
            "by_guard_kind": {"live_prior_only_raise_guard": 3},
            "by_guard_flag": {
                "q6_prior_floor_watch": 3,
                "settlement_count_prior_candidate": 2,
            },
            "by_evidence_profile": {"shape+layout": 3},
            "median_guarded_p90_shortfall": 125_000,
            "median_unguarded_p90_excess": 75_000,
            "median_p90_guard_cut": 200_000,
        },
    }
    brief = {
        "total_rows": 49,
        "source_counts": {"windivert_archive": 49},
        "overall": {
            "rows": 49,
            "formal_mode_counts": {"v2": 15, "v3_practical": 34},
            "formal_mode_reason_counts": {
                "v2_mode_requested": 15,
                "v3_practical_ready_live_guarded": 34,
            },
            "v3_practical_formal_rows": 34,
            "v3_practical_live_guard_rows": 23,
            "v3_practical_live_guard_rate": 0.68,
            "v3_practical_live_guard_reason_counts": {
                "live_prior_only_raise_guard": 23,
            },
            "v3_practical_unguarded_rows": 23,
            "v3_practical_guard_comparison_rows": 23,
            "v3_practical_guarded_mae_on_comparison": 120_000,
            "v3_practical_unguarded_mae_on_comparison": 120_000,
            "v3_practical_guarded_minus_unguarded_mae": 0.0,
            "v3_practical_guarded_minus_unguarded_median_p50": 0.0,
            "v3_practical_guarded_minus_unguarded_median_p90": -30_000,
            "v3_practical_guarded_p90_coverage_on_comparison": 0.57,
            "v3_practical_unguarded_p90_coverage_on_comparison": 1.0,
            "v3_practical_guarded_minus_unguarded_p90_coverage": -0.43,
            "v3_practical_guarded_p90_extreme_over_on_comparison": 0.09,
            "v3_practical_unguarded_p90_extreme_over_on_comparison": 0.57,
            "v3_practical_guarded_minus_unguarded_p90_extreme_over": -0.48,
            "v3_practical_guard_case_summary": guard_case_summary,
            "formal_policy_comparison": policy_matrix,
        },
        "prebid_overall": {
            "rows": 49,
            "formal_mode_counts": {"v2": 15, "v3_practical": 34},
            "formal_mode_reason_counts": {
                "v2_mode_requested": 15,
                "v3_practical_ready_live_guarded": 34,
            },
            "v3_practical_formal_rows": 34,
            "v3_practical_live_guard_rows": 23,
            "v3_practical_live_guard_reason_counts": {
                "live_prior_only_raise_guard": 23,
            },
            "v3_practical_unguarded_rows": 23,
            "v3_practical_guard_comparison_rows": 23,
            "v3_practical_guarded_mae_on_comparison": 120_000,
            "v3_practical_unguarded_mae_on_comparison": 120_000,
            "v3_practical_guarded_minus_unguarded_mae": 0.0,
            "v3_practical_guarded_minus_unguarded_median_p50": 0.0,
            "v3_practical_guarded_minus_unguarded_median_p90": -30_000,
            "v3_practical_guarded_p90_coverage_on_comparison": 0.57,
            "v3_practical_unguarded_p90_coverage_on_comparison": 1.0,
            "v3_practical_guarded_minus_unguarded_p90_coverage": -0.43,
            "v3_practical_guarded_p90_extreme_over_on_comparison": 0.09,
            "v3_practical_unguarded_p90_extreme_over_on_comparison": 0.57,
            "v3_practical_guarded_minus_unguarded_p90_extreme_over": -0.48,
            "v3_practical_guard_case_summary": guard_case_summary,
            "formal_policy_comparison": policy_matrix,
        },
        "by_map_id": {
            "2410": {
                "rows": 10,
                "v3_practical_guard_case_summary": {
                    "rows": 8,
                    "p50_worsened_rows": 0,
                    "p90_coverage_lost_rows": 8,
                    "p90_extreme_over_added_rows": 0,
                },
            },
        },
        "by_map_family": {
            "villa": {
                "rows": 45,
                "v3_practical_guard_case_summary": {
                    "rows": 21,
                    "p50_worsened_rows": 0,
                    "p90_coverage_lost_rows": 9,
                    "p90_extreme_over_added_rows": 0,
                },
            },
        },
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        live_practical_guard_brief=brief,
        guard_loss_source_context={
            "status": "blocked_source_semantics_required",
            "summary": {
                "maps": 2,
                "guard_loss_rows": 7,
                "status_counts": {
                    "blocked_cse_source_semantics_intersection": 1,
                    "watch_source_context_intersection": 1,
                },
                "cse_exact_overlap_maps": 2,
                "source_semantics_detail_maps": 1,
                "capacity_table_detail_maps": 1,
                "capacity_acquisition_example_maps": 1,
            },
            "rows": [
                {
                    "map_id": "2410",
                    "status": "blocked_cse_source_semantics_intersection",
                },
                {
                    "map_id": "2401",
                    "status": "watch_source_context_intersection",
                },
            ],
        },
        activity_drop_universe_overlay={
            "status": "blocked_activity_overlay_source_required",
            "shadow_only": True,
            "affects_bid": False,
            "hard_map_allowed": False,
            "summary": {
                "maps": 1,
                "files": 3,
                "guard_loss_overlap_maps": 1,
                "candidate_item_universe_covered_maps": 1,
                "hard_map_blocked_maps": 1,
                "status_counts": {
                    "blocked_mixed_overlay_source_required": 1,
                },
            },
            "rows": [
                {
                    "map_id": "2524",
                    "status": "blocked_mixed_overlay_source_required",
                }
            ],
        },
        source_parser_requirements={
            "status": "blocked_source_parser_required",
            "shadow_only": True,
            "affects_bid": False,
            "parser_required": True,
            "summary": {
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
            "rows": [
                {
                    "map_id": "2410",
                    "status": (
                        "blocked_session_capacity_source_parser_required"
                    ),
                }
            ],
        },
    )

    gates = {row["name"]: row for row in result["gates"]}
    guard_gate = gates["v3_practical_archive_live_guard_metrics"]
    assert guard_gate["status"] == "watch"
    assert guard_gate["overall"]["v3_practical_guard_comparison_rows"] == 23
    metrics = result["v3_practical_archive_live_guard_metrics"]
    assert metrics["status"] == "watch"
    assert metrics["overall"]["v3_practical_formal_rows"] == 34
    assert (
        metrics["overall"]["v3_practical_guarded_minus_unguarded_p90_coverage"]
        == -0.43
    )
    assert metrics["contract_checks"]["overall"]["status"] == "watch"
    assert metrics["contract_checks"]["prebid_overall"]["status"] == "watch"
    assert metrics["contract_checks"]["overall"]["formal_policy_comparison_rows"] == 23
    assert metrics["contract_checks"]["overall"]["guard_case_summary_rows"] == 23
    assert (
        metrics["contract_checks"]["overall"][
            "guard_case_summary_p90_coverage_lost_rows"
        ]
        == 3
    )
    assert metrics["overall"]["v3_practical_guard_case_summary"][
        "p50_worsened_rows"
    ] == 2
    assert metrics["overall"]["v3_practical_guard_case_summary"][
        "p90_coverage_loss_context"
    ]["by_guard_flag"] == {
        "q6_prior_floor_watch": 3,
        "settlement_count_prior_candidate": 2,
    }
    assert (
        metrics["by_map_id"]["2410"]["v3_practical_guard_case_summary"][
            "p90_coverage_lost_rows"
        ]
        == 8
    )
    assert (
        metrics["by_map_family"]["villa"]["v3_practical_guard_case_summary"][
            "p90_coverage_lost_rows"
        ]
        == 9
    )
    assert metrics["overall"]["formal_policy_comparison"]["comparison_rows"] == 23
    guard_loss_context = result["v3_practical_guard_loss_source_context_contract"]
    assert guard_loss_context["status"] == "watch"
    assert guard_loss_context["audit_status"] == "blocked_source_semantics_required"
    assert guard_loss_context["maps"] == 2
    assert guard_loss_context["guard_loss_rows"] == 7
    assert guard_loss_context["status_counts"] == {
        "blocked_cse_source_semantics_intersection": 1,
        "watch_source_context_intersection": 1,
    }
    overlay = result["activity_drop_universe_overlay_contract"]
    assert overlay["status"] == "watch"
    assert overlay["audit_status"] == "blocked_activity_overlay_source_required"
    assert overlay["maps"] == 1
    assert overlay["candidate_item_universe_covered_maps"] == 1
    assert overlay["hard_map_blocked_maps"] == 1
    assert overlay["status_counts"] == {
        "blocked_mixed_overlay_source_required": 1
    }
    source_parser = result["source_parser_requirements_contract"]
    assert source_parser["status"] == "watch"
    assert source_parser["audit_status"] == "blocked_source_parser_required"
    assert source_parser["session_capacity_maps"] == 1
    assert source_parser["drop_ref_residual_maps"] == 1
    assert source_parser["requirement_counts"] == {
        "parse_numeric_action_result_for_session_capacity_semantics": 1,
        "find_session_capacity_source_beyond_numeric_bucket_cells": 1,
        "resolve_session_capacity_without_exact_event_source": 1,
        "resolve_payload_verified_table_cap_gap_without_full_source": 1,
        "check_table_overlay_or_server_side_after_outer_fields_metadata_only": 1,
        "resolve_current_raw_table_overlay_or_server_transform_residual": 1,
        "inspect_drop_ref_source_semantics_or_overlay": 1,
    }
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
    assert (
        "review v3 practical guard coverage/extreme-over tradeoff by slice before promotion"
        in result["next_actions"]
    )


def test_readiness_blocks_malformed_live_practical_guard_brief() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        live_practical_guard_brief={
            "overall": {
                "v3_practical_formal_rows": 34,
                "v3_practical_guard_comparison_rows": 0,
            }
        },
    )

    gates = {row["name"]: row for row in result["gates"]}
    assert gates["v3_practical_archive_live_guard_metrics"]["status"] == "blocked"
    assert result["v3_practical_archive_live_guard_metrics"]["status"] == "blocked"
    assert (
        "regenerate v3 practical brief with paired guarded/unguarded rows"
        in result["next_actions"]
    )


def test_readiness_blocks_live_practical_guard_brief_without_policy_matrix() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]
    stats = {
        "rows": 4,
        "formal_mode_counts": {"v2": 1, "v3_practical": 3},
        "formal_mode_reason_counts": {
            "v2_mode_requested": 1,
            "v3_practical_ready_live_guarded": 3,
        },
        "v3_practical_formal_rows": 3,
        "v3_practical_live_guard_rows": 2,
        "v3_practical_live_guard_reason_counts": {"guard": 2},
        "v3_practical_unguarded_rows": 2,
        "v3_practical_guard_comparison_rows": 2,
        "v3_practical_guarded_mae_on_comparison": 100.0,
        "v3_practical_unguarded_mae_on_comparison": 90.0,
        "v3_practical_guarded_minus_unguarded_mae": 10.0,
        "v3_practical_guarded_minus_unguarded_median_p50": -20.0,
        "v3_practical_guarded_minus_unguarded_median_p90": -50.0,
        "v3_practical_guarded_p90_coverage_on_comparison": 0.5,
        "v3_practical_unguarded_p90_coverage_on_comparison": 1.0,
        "v3_practical_guarded_minus_unguarded_p90_coverage": -0.5,
        "v3_practical_guarded_p90_extreme_over_on_comparison": 0.0,
        "v3_practical_unguarded_p90_extreme_over_on_comparison": 0.5,
        "v3_practical_guarded_minus_unguarded_p90_extreme_over": -0.5,
        "v3_practical_guard_case_summary": {
            "rows": 2,
            "p50_worsened_rows": 1,
            "p90_coverage_lost_rows": 1,
            "p90_extreme_over_added_rows": 0,
        },
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        live_practical_guard_brief={
            "overall": dict(stats),
            "prebid_overall": dict(stats),
        },
    )

    metrics = result["v3_practical_archive_live_guard_metrics"]
    assert metrics["status"] == "blocked"
    assert metrics["contract_checks"]["overall"]["status"] == "blocked"
    assert (
        "formal_policy_comparison"
        in metrics["contract_checks"]["overall"]["missing_keys"]
    )


def test_readiness_blocks_live_practical_guard_brief_without_prebid_contract() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]
    brief = {
        "overall": {
            "rows": 4,
            "formal_mode_counts": {"v3_practical": 4},
            "formal_mode_reason_counts": {"v3_practical_ready_live_guarded": 4},
            "v3_practical_formal_rows": 4,
            "v3_practical_live_guard_rows": 4,
            "v3_practical_live_guard_reason_counts": {"guard": 4},
            "v3_practical_unguarded_rows": 4,
            "v3_practical_guard_comparison_rows": 4,
            "v3_practical_guarded_mae_on_comparison": 1.0,
            "v3_practical_unguarded_mae_on_comparison": 1.0,
            "v3_practical_guarded_minus_unguarded_mae": 0.0,
            "v3_practical_guarded_minus_unguarded_median_p50": 0.0,
            "v3_practical_guarded_minus_unguarded_median_p90": -10.0,
            "v3_practical_guarded_p90_coverage_on_comparison": 0.5,
            "v3_practical_unguarded_p90_coverage_on_comparison": 1.0,
            "v3_practical_guarded_minus_unguarded_p90_coverage": -0.5,
            "v3_practical_guarded_p90_extreme_over_on_comparison": 0.0,
            "v3_practical_unguarded_p90_extreme_over_on_comparison": 0.5,
            "v3_practical_guarded_minus_unguarded_p90_extreme_over": -0.5,
            "v3_practical_guard_case_summary": {
                "rows": 4,
                "p50_worsened_rows": 0,
                "p90_coverage_lost_rows": 1,
                "p90_extreme_over_added_rows": 0,
            },
            "formal_policy_comparison": {
                "status": "watch",
                "comparison_rows": 4,
                "policies": {"v2": {}, "v3_guarded": {}, "v3_unguarded": {}},
                "deltas_vs_v2": {},
            },
        },
        "prebid_overall": {
            "rows": 4,
            "v3_practical_formal_rows": 4,
            "v3_practical_guard_comparison_rows": 0,
        },
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        live_practical_guard_brief=brief,
    )

    metrics = result["v3_practical_archive_live_guard_metrics"]
    assert metrics["status"] == "blocked"
    assert metrics["contract_checks"]["overall"]["status"] == "watch"
    assert metrics["contract_checks"]["prebid_overall"]["status"] == "blocked"
    assert (
        "formal_mode_counts"
        in metrics["contract_checks"]["prebid_overall"]["missing_keys"]
    )


def test_readiness_attaches_guarded_bridge_stability_matrix() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        scp_guarded_bridge_stability={
            "overall_status": "blocked_applied_hurt",
            "status_reasons": ["applied_hurts_present"],
            "posterior_trials": 64,
            "posterior_seeds": [0, 1],
            "run_count": 2,
            "watch_runs": 1,
            "required_selected_groups": ["2506"],
            "stable_selected_groups": ["2506"],
            "union_selected_groups": ["2501", "2506"],
            "selected_signature_counts": {"2506:2": 1, "2501:1,2506:1": 1},
            "hurt_group_counts": {"2501": 1},
            "min_applied_rows": 20,
            "min_applied_rows_required": 20,
            "selected_group_support_summary": [
                {
                    "group": "2501",
                    "run_count": 1,
                    "min_applied_rows": 53,
                    "hurt_run_count": 1,
                }
            ],
            "selected_group_support_gap": [],
            "selected_group_guard_summary": [
                {
                    "group": "2501",
                    "run_count": 1,
                    "guard_status_counts": {"watch_train_guard": 1},
                }
            ],
            "selected_group_instability_summary": [
                {
                    "group": "2501",
                    "status": "blocked_train_holdout_instability",
                }
            ],
        },
    )

    gates = {row["name"]: row for row in result["gates"]}
    stability = gates["settlement_count_guarded_bridge_stability"]
    assert stability["status"] == "blocked"
    assert stability["overall_status"] == "blocked_applied_hurt"
    assert stability["contract_check"]["status"] == "watch"
    assert stability["posterior_trials"] == 64
    assert stability["posterior_seeds"] == [0, 1]
    assert stability["status_reasons"] == ["applied_hurts_present"]
    assert stability["hurt_group_counts"] == {"2501": 1}
    assert stability["selected_group_support_summary"][0]["group"] == "2501"
    assert stability["selected_group_guard_summary"][0]["group"] == "2501"
    assert stability["selected_group_instability_summary"][0]["status"] == (
        "blocked_train_holdout_instability"
    )
    assert result["settlement_count_guarded_bridge_stability"]["status"] == (
        "blocked_applied_hurt"
    )
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    assert "2501:blocked_train_holdout_instability" in dependency_gates[
        "settlement_count_guarded_bridge_stability"
    ]["focus"]


def test_readiness_blocks_malformed_guarded_bridge_stability_matrix() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        scp_guarded_bridge_stability={
            "overall_status": "watch",
            "run_count": 1,
            "watch_runs": 1,
            "required_selected_groups": ["2506"],
            "stable_selected_groups": [],
            "union_selected_groups": ["2506"],
            "selected_signature_counts": {"2506:1": 1},
            "hurt_group_counts": {},
            "min_applied_rows": 20,
            "min_applied_rows_required": 20,
            "selected_group_support_summary": [],
            "selected_group_support_gap": [],
            "selected_group_guard_summary": [],
            "selected_group_instability_summary": [],
        },
    )

    gates = {row["name"]: row for row in result["gates"]}
    stability = gates["settlement_count_guarded_bridge_stability"]
    assert stability["status"] == "blocked"
    assert stability["contract_check"]["status"] == "blocked"
    assert "posterior_trials" in stability["contract_check"]["missing_keys"]
    assert "posterior_seeds" in stability["contract_check"]["missing_keys"]
    assert "watch stability must cover all required selected groups" in stability["reason"]


def _cse_artifact(*, active: bool = False) -> dict[str, object]:
    return {
        "affects_bid": False,
        "active": active,
        "generated_at": "2026-06-08",
        "source": "archive_settlement_source_semantics_audit",
        "group_bys": ["map_id", "map_family"],
        "table_overlay_metadata": {"raw_file_version": 303},
        "cohorts": [{"label": "default_archive", "status": "ok"}],
        "entries": [
            {
                "scope": "map_id",
                "group": "2506",
                "status": "watch_capacity_source_expansion_shadow_only",
                "gate_reason": "observed_unique_round_over_cap_source_expansion",
                "source": "archive_capacity_source_expansion_shadow:default_archive",
                "archive_sessions": 21,
                "mechanism_classes": "session_capacity_source_semantics:1",
                "source_evidence_classes": "settlement_payload_verified_only:1",
                "source_context_classes": "payload_verified_partial_action_only:1",
                "unique_round_overflow_rows": 1,
                "server_side_expansion_rows": 0,
                "session_capacity_source_semantics_rows": 1,
                "public_total_match_rows": 0,
                "full_action_rows": 0,
                "payload_verified_only_rows": 1,
                "payload_inventory_mismatch_rows": 0,
                "non_zodiac_missing_max": 0,
            }
        ],
    }


def test_readiness_attaches_capacity_source_expansion_artifact_contract() -> None:
    module = _load_module()
    rows = [
        {
            **_row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700),
            "v3_cse_candidate": True,
            "v3_cse_pressure_candidate": True,
            "v3_cse_status": "watch_capacity_source_expansion_shadow_only",
        }
        for idx in range(4)
    ]

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        capacity_source_expansion_artifact=_cse_artifact(),
    )

    gates = {row["name"]: row for row in result["gates"]}
    cse = gates["capacity_source_expansion_shadow"]
    assert cse["status"] == "watch"
    assert cse["artifact_contract"]["status"] == "watch"
    assert cse["artifact_contract"]["entries"] == 1
    assert cse["artifact_contract"]["candidate_entries"] == 1
    assert cse["artifact_contract"]["group_bys"] == ["map_id", "map_family"]
    assert cse["artifact_contract"]["digest_scope"] == "map_id"
    assert cse["artifact_contract"]["source_split_status"] == (
        "blocked_payload_only_source_split_unresolved"
    )
    assert cse["artifact_contract"]["unique_round_overflow_rows"] == 1
    assert cse["artifact_contract"]["session_capacity_source_semantics_rows"] == 1
    assert cse["artifact_contract"]["server_side_expansion_rows"] == 0
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["watch_gates"]
    }
    assert "source_split=blocked_payload_only_source_split_unresolved" in (
        dependency_gates["capacity_source_expansion_shadow"]["focus"]
    )
    assert result["capacity_source_expansion_artifact_contract"]["cohorts"] == 1


def test_readiness_blocks_malformed_capacity_source_expansion_artifact() -> None:
    module = _load_module()
    rows = [
        _row("aisha|2506", session_id=f"s{idx}", truth=1_000, pred=500, p90=700)
        for idx in range(4)
    ]

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        capacity_source_expansion_artifact={
            **_cse_artifact(active=True),
            "entries": [{"scope": "map_id", "group": "2506"}],
        },
    )

    gates = {row["name"]: row for row in result["gates"]}
    cse = gates["capacity_source_expansion_shadow"]
    assert cse["status"] == "blocked"
    assert cse["artifact_contract"]["status"] == "blocked"
    assert "artifact active must be false" in cse["reason"]
    assert "status" in cse["artifact_contract"]["entry_missing_key_counts"]


def test_readiness_blocks_prior_robustness_on_activity_candidate() -> None:
    module = _load_module()
    rows = [
        {
            **_row("aisha|2526", session_id=f"s{idx}", truth=1_000, pred=1_000, p90=1_200),
            "v3_robust_status": "prior_unavailable",
            "v3_robust_prior_usable": False,
            "v3_robust_prior_trusted": False,
            "v3_robust_fallback_mode": "missing_prior_truth_only",
            "v3_robust_activity_candidate": True,
            "v3_robust_reasons": "activity_map_id_candidate",
        }
        for idx in range(2)
    ]

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
    )

    gates = {row["name"]: row for row in result["gates"]}
    assert gates["prior_robustness"]["status"] == "blocked"
    assert gates["prior_robustness"]["robust_activity_candidate"] == 2
    assert gates["prior_robustness"]["robust_prior_trusted"] == 0
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    assert dependency_gates["prior_robustness"]["lane"] == "table_activity_capacity"
    assert "activity_candidate_rows=2" in dependency_gates["prior_robustness"]["focus"]
    assert "separate activity/prior-drift rows before formal promotion" in result["next_actions"]


def test_readiness_surfaces_prior_stress_capacity_groups() -> None:
    module = _load_module()
    rows = [
        {
            **_row(
                "ethan|2501",
                session_id=f"s{idx}",
                truth=1_000,
                pred=700,
                p90=900,
            ),
            "map_id": 2501,
            "v3_robust_status": "prior_stressed",
            "v3_robust_prior_trusted": False,
            "v3_robust_prior_stress_score": 2,
            "v3_robust_reasons": "total_count_above_prior;total_cells_above_prior",
            "v3_prior_expected_count": 2,
            "v3_prior_expected_cells": 20,
            "v3_prior_q6_expected_cells": 4,
            "v3_prior_items_per_session_max": 5,
            "v3_summary_session_total_count_exact": 7,
            "v3_summary_session_total_cells_exact": 48,
            "v3_summary_q6_cells_floor": 8,
            "v3_truth_item_count": 7,
            "v3_truth_total_cells": 48,
            "v3_post_total_cells_p50": 40,
            "v3_post_total_cells_p90": 50,
            "v3_truth_q6_cells": 8,
            "v3_post_q6_cells_p50": 6,
            "v3_post_q6_cells_p90": 10,
        }
        for idx in range(2)
    ]

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
    )

    gates = {row["name"]: row for row in result["gates"]}
    drift = gates["prior_stress_capacity_table_drift"]
    assert drift["status"] == "blocked"
    assert drift["detail_rows"] == 2
    assert drift["capacity_flag_hits"] == 4
    assert drift["capacity_flag_counts"] == {
        "target_count_above_prior_max": 2,
        "truth_count_above_prior_max": 2,
    }
    assert drift["capacity_count_summary"]["target_prior_max_delta"]["avg"] == 2
    assert drift["capacity_count_summary"]["truth_prior_max_delta"]["avg"] == 2
    assert drift["capacity_count_summary"]["target_truth_delta"]["avg"] == 0
    assert drift["capacity_count_summary"]["case_counts"] == {
        "direct_prior_max_conflict": 2
    }
    assert drift["detail_contract"]["status"] == "watch"
    assert drift["detail_contract"]["rows"] == 2
    assert drift["detail_contract"]["capacity_flag_hits"] == 4
    assert drift["detail_contract"]["case_counts"] == {
        "direct_prior_max_conflict": 2
    }
    assert drift["detail_contract"]["top_map_groups"][0]["value"] == "2501"
    assert drift["consistency_bucket_counts"] == {
        "hard_capacity_conflict": 2
    }
    assert drift["consistency_class_counts"]["capacity_direct_prior_max_conflict"] == 2
    assert drift["top_map_groups"][0]["value"] == "2501"
    assert drift["top_map_groups"][0]["capacity_flag_hits"] == 4
    assert (
        drift["top_map_groups"][0]["capacity_count_summary"][
            "target_prior_max_delta"
        ]["max"]
        == 2
    )
    assert drift["top_map_groups"][0]["capacity_count_summary"]["case_counts"] == {
        "direct_prior_max_conflict": 2
    }
    assert drift["top_profile_groups"][0]["value"] == "ethan|2501|item+shape"
    assert result["prior_stress_detail_summary"]["rows"] == 2
    assert result["prior_stress_detail_summary"]["top_map_groups"][0]["value"] == "2501"
    assert (
        result["prior_stress_detail_summary"]["capacity_count_summary"][
            "truth_prior_max_delta"
        ]["avg"]
        == 2
    )
    assert result["prior_stress_detail_summary"]["consistency_bucket_counts"] == {
        "hard_capacity_conflict": 2
    }
    assert (
        "audit prior-stressed capacity/table drift by map/profile before promotion"
        in result["next_actions"]
    )
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    drift_dependency = dependency_gates["prior_stress_capacity_table_drift"]
    assert drift_dependency["lane"] == "table_activity_capacity"
    assert drift_dependency["focus"] == (
        "detail_rows=2;capacity_flag_hits=4;"
        "top_cases=direct_prior_max_conflict:2"
    )


def test_readiness_attaches_capacity_table_audit_focus() -> None:
    module = _load_module()
    rows = [
        {
            **_row(
                "ethan|2501",
                session_id=f"s{idx}",
                truth=1_000,
                pred=700,
                p90=900,
            ),
            "map_id": 2501,
            "v3_robust_status": "prior_stressed",
            "v3_robust_prior_trusted": False,
            "v3_robust_prior_stress_score": 2,
            "v3_robust_reasons": "total_count_above_prior;total_cells_above_prior",
            "v3_prior_expected_count": 2,
            "v3_prior_expected_cells": 20,
            "v3_prior_q6_expected_cells": 4,
            "v3_prior_items_per_session_max": 5,
            "v3_summary_session_total_count_exact": 7,
            "v3_summary_session_total_cells_exact": 48,
            "v3_summary_q6_cells_floor": 8,
            "v3_truth_item_count": 7,
            "v3_truth_total_cells": 48,
            "v3_post_total_cells_p50": 40,
            "v3_post_total_cells_p90": 50,
            "v3_truth_q6_cells": 8,
            "v3_post_q6_cells_p50": 6,
            "v3_post_q6_cells_p90": 10,
        }
        for idx in range(2)
    ]
    capacity_table_audit = {
        "case": "all",
        "bucket": "all",
        "errors": [],
        "rows": [
            {
                "map_id": "2501",
                "map_name": "未知残骸",
                "rows": 2,
                "status": "table_possible_max_below_truth",
                "capacity_cases": {"direct_prior_max_conflict": 2},
                "capacity_semantic_summary": {
                    "status": "blocked_round_cap_overflow_after_temp",
                    "drop_ref_excess_after_temp_zodiac_max": 13,
                    "round_cap_excess_after_temp_zodiac_max": 7,
                    "non_zodiac_missing_from_drop_universe_max": 0,
                },
                "residual_mode_summary": {
                    "mode_counts": {"round_cap_overflow": 2},
                },
                "source_split_summary": {
                    "truth_prior_max_delta": {"max": 16},
                },
            }
        ],
        "semantic_matrix": [
            {
                "rows": 2,
                "files": 1,
                "map_ids": {"2501": 2},
                "map_family": "shipwreck",
                "residual_mode": "round_cap_overflow",
                "semantic_status_counts": {
                    "blocked_round_cap_overflow_after_temp": 2,
                },
                "capacity_cases": {"direct_prior_max_conflict": 2},
                "total_count_source": "exact",
                "full_action_signal": "no_full_action",
                "public_total_signal": "has_public_total",
            }
        ],
        "detail_summary": {
            "rows": 3,
            "unique_file_map_residual_rows": 1,
            "unique_files": 1,
            "mechanism_candidate_counts": {"round_cap_candidate_gap": 1},
            "next_check_counts": {
                "check_per_session_table_version_or_external_overlay": 1,
            },
            "source_signal_counts": {"no_full_action/has_public_total": 1},
            "map_counts": {"2501": 1},
            "top_examples": [
                {
                    "file": "fatbeans_valid_aisha_2501.json",
                    "map_id": "2501",
                    "residual_mode": "round_cap_overflow",
                    "semantic_status": "blocked_round_cap_overflow_after_temp",
                }
            ],
        },
    }
    capacity_table_acquisition = {
        "status": "blocked_acquisition_required",
        "detail_rows": 3,
        "unique_detail_rows": 1,
        "unique_files": 1,
        "acquisition_route_counts": {
            "table_version_or_external_overlay_required": 1,
        },
        "source_strength_counts": {
            "public_total_confirmed": 1,
        },
        "map_counts": {"2501": 1},
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
                "file": "fatbeans_valid_aisha_2501.json",
                "map_id": "2501",
                "acquisition_route": (
                    "table_version_or_external_overlay_required"
                ),
            }
        ],
    }

    result = module.summarize_readiness(
        rows,
        [],
        min_windows=2,
        min_sessions=2,
        folds=2,
        capacity_table_audit=capacity_table_audit,
        capacity_table_acquisition_audit=capacity_table_acquisition,
    )

    gates = {row["name"]: row for row in result["gates"]}
    drift = gates["prior_stress_capacity_table_drift"]
    assert drift["status"] == "blocked"
    assert drift["capacity_table_audit_contract"]["status"] == "watch"
    assert drift["capacity_table_audit_contract"]["semantic_status_counts"] == {
        "blocked_round_cap_overflow_after_temp": 2,
    }
    assert drift["capacity_table_audit_contract"]["residual_mode_counts"] == {
        "round_cap_overflow": 2,
    }
    assert drift["capacity_table_audit_contract"][
        "detail_mechanism_candidate_counts"
    ] == {"round_cap_candidate_gap": 1}
    assert drift["capacity_table_audit_contract"]["detail_next_check_counts"] == {
        "check_per_session_table_version_or_external_overlay": 1,
    }
    assert drift["capacity_table_audit_contract"][
        "detail_unique_file_map_residual_rows"
    ] == 1
    assert drift["capacity_table_audit_contract"]["top_detail_examples"][0][
        "file"
    ] == "fatbeans_valid_aisha_2501.json"
    assert drift["capacity_table_audit_contract"]["top_blocked_maps"][0][
        "map_id"
    ] == "2501"
    assert drift["capacity_table_acquisition_contract"]["status"] == "blocked"
    assert drift["capacity_table_acquisition_contract"]["route_counts"] == {
        "table_version_or_external_overlay_required": 1,
    }
    assert drift["capacity_table_acquisition_contract"][
        "source_strength_counts"
    ] == {"public_total_confirmed": 1}
    assert drift["capacity_table_acquisition_contract"][
        "table_overlay_metadata"
    ]["local_overlay_status"] == "activity_table_available_locally"
    assert drift["capacity_table_acquisition_contract"][
        "artifact_table_overlay_metadata"
    ]["local_overlay_status"] == "v300_activity_listed_missing_locally"
    assert drift["capacity_table_acquisition_contract"][
        "table_overlay_metadata_stale"
    ] is True
    assert result["capacity_table_acquisition_contract"]["unique_detail_rows"] == 1
    dependency_gates = {
        row["gate"]: row
        for row in result["gate_dependencies"]["blocked_or_pending_gates"]
    }
    focus = dependency_gates["prior_stress_capacity_table_drift"]["focus"]
    assert "semantic_status=blocked_round_cap_overflow_after_temp:2" in focus
    assert "residual_modes=round_cap_overflow:2" in focus
    assert "detail_mechanisms=round_cap_candidate_gap:1" in focus
    assert (
        "detail_next_checks="
        "check_per_session_table_version_or_external_overlay:1"
    ) in focus
    assert (
        "acquisition_routes=table_version_or_external_overlay_required:1"
        in focus
    )
    assert "overlay=activity_table_available_locally" in focus
    assert "top_maps=2501" in focus


def test_prior_stress_detail_contract_blocks_malformed_summary() -> None:
    module = _load_module()
    result = module.summarize_prior_stress_detail_contract(
        {
            "overall": {
                "rows": 1,
                "capacity_flag_counts": {"truth_count_above_prior_max": 1},
            },
            "by_group": [],
        },
        top_map_groups=[],
        top_profile_groups=[],
    )

    assert result["status"] == "blocked"
    assert "capacity_count_summary" in result["missing_keys"]
    assert "top map groups are missing" in result["reason"]


def test_readiness_blocks_archive_data_quality_on_parse_errors() -> None:
    module = _load_module()
    rows = [
        _row("ethan|2502", session_id="s1", truth=1_000, pred=1_000, p90=1_200),
        _row("ethan|2502", session_id="s2", truth=1_000, pred=1_000, p90=1_200),
    ]

    result = module.summarize_readiness(
        rows,
        [{"file": "bad.json", "error": "ValueError"}],
        min_windows=2,
        min_sessions=2,
        folds=2,
    )

    gates = {row["name"]: row for row in result["gates"]}
    assert gates["archive_data_quality"]["status"] == "blocked"
    assert result["blocked_gates"] >= 1
