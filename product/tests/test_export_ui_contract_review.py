from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _review_module():
    path = ROOT / "scripts" / "export_ui_contract_review.py"
    spec = importlib.util.spec_from_file_location("export_ui_contract_review", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(
    *,
    display_label: str = "",
    baseline_official: bool = True,
    shadow_affects_bid: bool = False,
    truth_q6_count: int = 1,
    truth_q6_value: int | None = None,
    truth_q6_decision_value: int | None = None,
    truth_q6_tail_replacement_value: int = 0,
    q6_tail_replacement_miss: bool = False,
    q6_below_drop_prior: bool = False,
    q6_decision_value_range: str = "0 / 120,000 / 300,000",
    tail_shadow_active: bool = False,
    layout_conflict: bool = False,
    layout_bottom_row: int = 16,
    unknown_minimap_quality: bool = False,
    v2_match: str = "12/80",
    fallback_active: bool = False,
    include_tail_shadow: bool = True,
    evidence_profile_key: str = "public:max_quality+shape+layout",
    q6_quality_only_deep_local_risk: bool = False,
) -> dict:
    q6_decision_value = (
        truth_q6_decision_value
        if truth_q6_decision_value is not None
        else 486510 if truth_q6_count else 0
    )
    shadows = [
        {
            "label": "profile_b5",
            "active": False,
            "display_mode": "debug_only",
            "affects_bid": shadow_affects_bid,
            "q6_decision_value_p90": 0,
        },
    ]
    if include_tail_shadow:
        shadows.append(
            {
                "label": "aisha_deep_floor1",
                "active": tail_shadow_active,
                "display_mode": "risk_reference_candidate",
                "affects_bid": False,
                "q6_decision_value_p90": 300000 if tail_shadow_active else 0,
            }
        )
    return {
        "file": "sample.json",
        "known_value_sum": 800000,
        "v2_posterior_rows": [
            {
                "匹配": v2_match,
            }
        ],
        "model_eval": {
            "v2_q6_count_p90_under_prior_by": 0.5,
            "v2_q6_cells_p90_under_prior_by": 2.5,
            "q6_count_cell_prior_risk": True,
            "q6_count_cell_prior_gap": "count_low;cells_low",
            "q6_count_cell_prior_floor_value": 486510,
        },
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2501,
                "round": 4,
                "known_value_sum": 800000,
            },
            "source": {
                "file": "sample.json",
                "n_trials": 80,
                "shadow_trials": 80,
            },
            "baseline": {
                "official": baseline_official,
                "affects_bid": True,
                "decision": {
                    "action": "可守不抢",
                    "risk_band": "防守区",
                    "current_highest": "玩家A 500,000",
                    "stop_price": "680,000",
                },
                "posterior": {
                    "decision_value_range": "300,000 / 500,000 / 700,000",
                    "raw_value_range": "300,000 / 540,000 / 900,000",
                    "q6_sample_rate": "12.0%",
                    "q6_prior_rate": "77.5%",
                    "q6_prior_expected_count": "1.50",
                    "q6_prior_expected_cells": "5.6",
                    "q6_prior_expected_value": "486,510",
                    "q6_decision_value_range": q6_decision_value_range,
                    "q6_count_range": "0 / 1 / 1",
                    "q6_cells_range": "0 / 4 / 6",
                    "total_cells_range": "90 / 108 / 130",
                },
            },
            "q6_risk_reference": {
                "risk": False,
                "affects_bid": False,
                "bid_floor_applied": False,
                "minimum_bid_floor": "",
            },
            "fallback": {
                "active": fallback_active,
                "mode": "v1_map_prior_zero_match" if fallback_active else "",
                "affects_bid": False,
                "decision": {
                    "action": "停止追价" if fallback_active else "",
                },
                "posterior": {
                    "raw_value_range": "180,000 / 280,000 / 420,000"
                    if fallback_active
                    else "",
                    "match_text": "22/80" if fallback_active else "",
                },
            },
            "truth": {
                "available": True,
                "source": "settlement_or_sample_replay",
                "total_items": 36,
                "total_cells": 108,
                "q6": {
                    "count": truth_q6_count,
                    "cells": 16 if truth_q6_count else 0,
                    "value": (
                        truth_q6_value
                        if truth_q6_value is not None
                        else 486510 if truth_q6_count else 0
                    ),
                    "decision_value": (
                        q6_decision_value
                    ),
                    "trimmed_tail_value": 0,
                    "tail_replacement_value": truth_q6_tail_replacement_value,
                    "decision_value_with_tail_replacement": (
                        (486510 if truth_q6_count else 0)
                        + truth_q6_tail_replacement_value
                    ),
                },
            },
            "constraints": {
                "summary": {
                    "input_total_item_count": 36,
                    "input_warehouse_total_cells": 108,
                    "known_grid_items": 2,
                    "known_purple_item_count": 10,
                    "known_gold_item_count": 3,
                    "known_red_item_count": truth_q6_count,
                    "shape_target_count": 4,
                    "category_target_count": 2,
                    "category_exclusion_count": 1,
                    "public_constraint_key": "max_quality",
                    "evidence_profile_key": evidence_profile_key,
                    "information_density_band": "high",
                },
                "public_info": {
                    "input_constraints_mode": "pre_settlement_trusted_totals",
                    "evidence_profile_key": evidence_profile_key,
                    "random_sample_avg_values": "200000.0000",
                    "random_sample_avg_signal_values": "good",
                },
            },
            "minimap": {
                "status": "available",
                "known_items": 2,
                "quality_counts": {"q5": 1, "q6": truth_q6_count},
                "category_counts": {"能源": 1},
                "rows_hint": 13,
                "scrollable": False,
                "items": [
                    {
                        "row": 2,
                        "col": 4,
                        "width": 4,
                        "height": 4,
                        "quality": (
                            None
                            if unknown_minimap_quality
                            else 6 if truth_q6_count else 5
                        ),
                        "item_id": 1086001,
                        "item_name": "民用垂直起降飞行器",
                        "shape_key": "44",
                        "display_label": display_label,
                    },
                ],
            },
            "shadows": shadows,
            "diagnostics": {
                "layout": {
                    "conflict": layout_conflict,
                    "conflict_root": "footprint_overlap" if layout_conflict else "",
                    "bottom_row": layout_bottom_row,
                    "bottom_row_risk": True,
                    "bottom_row_risk_threshold": 13,
                },
                "q6": {
                    "no_plannable_control": q6_decision_value <= 0,
                    "zero_q6_proven_control": False,
                    "below_drop_prior": q6_below_drop_prior,
                    "top_size_band": "q6_top_huge",
                    "quality_only_local_count": 1,
                    "quality_only_deepest_local_index": 142,
                    "quality_only_deepest_start_row": 15,
                    "quality_only_deep_local_risk": (
                        q6_quality_only_deep_local_risk
                    ),
                    "quality_only_deep_row_threshold": 13,
                    "tail_replacement_p90_misses_truth": q6_tail_replacement_miss,
                    "tail_replacement_p90_under_by": (
                        truth_q6_tail_replacement_value
                        if q6_tail_replacement_miss
                        else 0
                    ),
                    "tail_replacement_estimate_p90": (
                        truth_q6_tail_replacement_value - 1_000
                        if truth_q6_tail_replacement_value
                        else 0
                    ),
                    "tail_replacement_estimate_p90_misses_truth": (
                        q6_tail_replacement_miss
                    ),
                    "tail_replacement_estimate_p90_under_by": (
                        1_000 if q6_tail_replacement_miss else 0
                    ),
                    "tail_replacement_count": (
                        1 if truth_q6_tail_replacement_value else 0
                    ),
                    "tail_replacement_items": (
                        "tail:1039000->93000"
                        if truth_q6_tail_replacement_value
                        else ""
                    ),
                    "tail_replacement_source": (
                        "map_weighted_p50"
                        if truth_q6_tail_replacement_value
                        else ""
                    ),
                },
                "sampling": {
                    "processing_seconds": 1.25,
                    "n_trials": 80,
                    "shadow_trials": 80,
                },
            },
        },
    }


def test_review_row_keeps_manual_check_fields_without_flags() -> None:
    module = _review_module()
    row = module.review_row_from_artifact(_artifact(), source_path="snapshot.json")

    assert row["hero"] == "aisha"
    assert row["map_id"] == 2501
    assert row["baseline_action"] == "可守不抢"
    assert row["v2_match_text"] == "12/80"
    assert row["v2_matched"] == 12
    assert row["v2_total"] == 80
    assert row["fallback_active"] is False
    assert row["q6_prior_rate"] == "77.5%"
    assert row["q6_prior_expected_count"] == "1.50"
    assert row["q6_prior_expected_cells"] == "5.6"
    assert row["q6_prior_expected_value"] == "486,510"
    assert row["q6_count_p90_under_prior_by"] == 0.5
    assert row["q6_cells_p90_under_prior_by"] == 2.5
    assert row["q6_count_cell_prior_risk"] is True
    assert row["q6_count_cell_prior_gap"] == "count_low;cells_low"
    assert row["q6_count_cell_prior_floor_value"] == 486510
    assert row["q6_risk_affects_bid"] is False
    assert row["q6_risk_bid_floor_applied"] is False
    assert row["q6_risk_minimum_bid_floor"] == ""
    assert row["layout_bottom_row"] == 16
    assert row["layout_bottom_row_risk"] is True
    assert row["layout_bottom_row_risk_threshold"] == 13
    assert row["q6_quality_only_local_count"] == 1
    assert row["q6_quality_only_deepest_local_index"] == 142
    assert row["q6_quality_only_deepest_start_row"] == 15
    assert row["q6_quality_only_deep_local_risk"] is False
    assert row["q6_quality_only_deep_row_threshold"] == 13
    assert row["truth_q6_count"] == 1
    assert row["q6_no_plannable_control"] is False
    assert row["q6_zero_q6_proven_control"] is False
    assert row["truth_q6_tail_replacement_value"] == 0
    assert row["q6_tail_replacement_p90_misses_truth"] is False
    assert row["q6_tail_replacement_p90_under_by"] == 0
    assert row["q6_tail_replacement_estimate_p90"] == 0
    assert row["q6_tail_replacement_estimate_p90_misses_truth"] is False
    assert row["q6_tail_replacement_estimate_p90_under_by"] == 0
    assert row["input_constraints_mode"] == "pre_settlement_trusted_totals"
    assert row["public_constraint_key"] == "max_quality"
    assert row["evidence_profile_key"] == "public:max_quality+shape+layout"
    assert row["information_density_band"] == "high"
    assert row["random_sample_avg_values"] == "200000.0000"
    assert row["minimap_nonempty_display_labels"] == 0
    assert row["minimap_unknown_quality_items"] == 0
    assert row["minimap_items_with_names"] == 1
    assert row["shadow_affects_bid_count"] == 0
    assert row["review_flags"] == ""
    assert row["manual_review_focus"] == (
        "check_hero_map_round;check_public_constraints;check_minimap_colors"
    )
    assert row["minimap_item_sample"][0]["item_name"] == "民用垂直起降飞行器"


def test_review_row_flags_ui_contract_regressions() -> None:
    module = _review_module()
    row = module.review_row_from_artifact(
        _artifact(
            display_label="飞行器",
            baseline_official=False,
            shadow_affects_bid=True,
            truth_q6_count=0,
            truth_q6_tail_replacement_value=93_000,
            q6_tail_replacement_miss=True,
            tail_shadow_active=True,
            layout_conflict=True,
            v2_match="0/80",
            q6_quality_only_deep_local_risk=True,
        )
    )

    flags = set(row["review_flags"].split(";"))
    assert "baseline_not_official" in flags
    assert "shadow_affects_bid" in flags
    assert "compact_minimap_label_present" in flags
    assert "zero_q6_truth_with_active_tail_shadow" in flags
    assert "layout_conflict" in flags
    assert "zero_posterior_match" in flags
    assert "zero_match_without_fallback" in flags
    assert "q6_quality_only_deep_local_risk" in flags
    assert "q6_tail_replacement_truth_miss" in flags
    assert row["minimap_nonempty_display_labels"] == 1
    assert row["shadow_affects_bid_count"] == 1


def test_export_review_rows_writes_csv_and_jsonl(tmp_path: Path) -> None:
    module = _review_module()
    snapshot = tmp_path / "latest_snapshot.json"
    snapshot.write_text(
        json.dumps(_artifact(), ensure_ascii=False),
        encoding="utf-8-sig",
    )

    result = module.export_review_rows([snapshot], out_dir=tmp_path / "review")

    assert result["exported"] == 1
    assert result["errors"] == 0
    assert result["summary"]["total_rows"] == 1
    assert result["summary"]["rows_with_review_flags"] == 0
    csv_text = Path(result["review_csv"]).read_text(encoding="utf-8-sig")
    jsonl_text = Path(result["review_jsonl"]).read_text(encoding="utf-8")
    summary = json.loads(Path(result["summary_json"]).read_text(encoding="utf-8"))
    assert "baseline_action" in csv_text
    assert "可守不抢" in csv_text
    assert '"manual_review_focus"' in jsonl_text
    assert summary["hero_map_counts"] == {"aisha:shipwreck": 1}


def test_summarize_review_rows_counts_flags_and_groups() -> None:
    module = _review_module()
    rows = [
        module.review_row_from_artifact(_artifact(), source_path="a.json"),
        module.review_row_from_artifact(
            _artifact(
                display_label="飞行器",
                shadow_affects_bid=True,
                truth_q6_count=0,
                truth_q6_tail_replacement_value=93_000,
                q6_tail_replacement_miss=True,
                tail_shadow_active=True,
                layout_conflict=True,
                v2_match="0/80",
                q6_quality_only_deep_local_risk=True,
            ),
            source_path="b.json",
        ),
    ]

    summary = module.summarize_review_rows(rows, errors=[{"path": "bad.json"}])

    assert summary["total_rows"] == 2
    assert summary["error_rows"] == 1
    assert summary["rows_with_review_flags"] == 1
    assert summary["flag_counts"]["compact_minimap_label_present"] == 1
    assert summary["flag_counts"]["layout_conflict"] == 1
    assert summary["q6_quality_only_deep_local_risk_rows"] == 1
    assert summary["q6_tail_replacement_value_rows"] == 1
    assert summary["q6_tail_replacement_truth_miss_rows"] == 1
    assert summary["flag_counts"]["shadow_affects_bid"] == 1
    assert summary["flag_counts"]["q6_tail_replacement_truth_miss"] == 1
    assert summary["flag_counts"]["zero_match_without_fallback"] == 1
    assert summary["hero_map_counts"] == {"aisha:shipwreck": 2}
    assert summary["zero_q6_truth_rows"] == 1
    assert summary["q6_no_plannable_control_rows"] == 1
    assert summary["q6_zero_q6_proven_control_rows"] == 0
    assert summary["q6_risk_affects_bid_rows"] == 0
    assert summary["q6_risk_bid_floor_applied_rows"] == 0
    assert summary["zero_posterior_match_rows"] == 1
    assert summary["zero_match_with_fallback_rows"] == 0
    assert summary["fallback_active_rows"] == 0
    assert summary["tail_shadow_candidate_rows"] == 2
    assert summary["active_tail_shadow_candidate_rows"] == 1
    assert summary["minimap_text_regression_rows"] == 1
    assert summary["shadow_affects_bid_rows"] == 1


def test_unknown_minimap_quality_is_informational_not_a_flag() -> None:
    module = _review_module()
    row = module.review_row_from_artifact(
        _artifact(unknown_minimap_quality=True),
        source_path="outline_only.json",
    )
    summary = module.summarize_review_rows([row])

    assert row["minimap_unknown_quality_items"] == 1
    assert "minimap_item_missing_quality" not in row["review_flags"]
    assert row["review_flags"] == ""
    assert summary["minimap_unknown_quality_rows"] == 1


def test_q6_below_prior_review_class_splits_true_miss_from_noise() -> None:
    module = _review_module()
    miss = module.review_row_from_artifact(
        _artifact(
            q6_below_drop_prior=True,
            truth_q6_count=2,
            truth_q6_value=486510,
            q6_decision_value_range="0 / 120,000 / 300,000",
            tail_shadow_active=True,
        ),
        source_path="miss.json",
    )
    zero_noise = module.review_row_from_artifact(
        _artifact(
            q6_below_drop_prior=True,
            truth_q6_count=0,
            truth_q6_value=0,
            q6_decision_value_range="0 / 0 / 180,000",
        ),
        source_path="zero.json",
    )
    no_plannable_tail = module.review_row_from_artifact(
        _artifact(
            q6_below_drop_prior=True,
            truth_q6_count=1,
            truth_q6_value=1_039_000,
            truth_q6_decision_value=0,
            truth_q6_tail_replacement_value=93_000,
            q6_decision_value_range="0 / 0 / 180,000",
        ),
        source_path="tail.json",
    )
    summary = module.summarize_review_rows([miss, zero_noise, no_plannable_tail])

    miss_flags = set(miss["review_flags"].split(";"))
    zero_flags = set(zero_noise["review_flags"].split(";"))
    assert miss["q6_below_drop_prior_class"] == "truth_p90_miss"
    assert miss["q6_below_drop_prior_under_by"] == 186510
    assert miss["q6_below_drop_prior_actionable"] is True
    assert miss["q6_actionable_shadow_status"] == "active_shadow_candidate"
    assert miss["q6_actionable_followup_bucket"] == "shadow_observation"
    assert miss["q6_actionable_followup_reason"] == "covered_by_active_shadow"
    assert "q6_below_drop_prior_truth_miss" in miss_flags
    assert zero_noise["q6_below_drop_prior_class"] == "truth_zero_noise"
    assert zero_noise["q6_below_drop_prior_actionable"] is False
    assert zero_noise["q6_actionable_shadow_status"] == ""
    assert "q6_below_drop_prior_truth_miss" not in zero_flags
    tail_flags = set(no_plannable_tail["review_flags"].split(";"))
    assert no_plannable_tail["q6_below_drop_prior_class"] == (
        "no_plannable_tail_review"
    )
    assert no_plannable_tail["q6_below_drop_prior_actionable"] is False
    assert no_plannable_tail["q6_below_drop_prior_under_by"] == 0
    assert "q6_below_drop_prior_truth_miss" not in tail_flags
    assert summary["q6_below_drop_prior_rows"] == 3
    assert summary["q6_below_drop_prior_actionable_rows"] == 1
    assert summary["q6_below_drop_prior_no_plannable_rows"] == 2
    assert summary["q6_below_drop_prior_zero_q6_proven_rows"] == 0
    assert summary["q6_below_drop_prior_class_counts"] == {
        "no_plannable_tail_review": 1,
        "truth_p90_miss": 1,
        "truth_zero_noise": 1,
    }
    assert summary["q6_actionable_miss_by_hero_map"] == {"aisha:shipwreck": 1}
    assert summary["q6_actionable_miss_by_evidence_profile"] == {
        "public:max_quality+shape+layout": 1,
    }
    assert summary["q6_actionable_miss_by_hero_map_profile"] == {
        "aisha:shipwreck:public:max_quality+shape+layout": 1,
    }
    assert summary["q6_actionable_shadow_status_counts"] == {
        "active_shadow_candidate": 1,
    }
    assert summary["q6_actionable_miss_by_hero_map_shadow_status"] == {
        "aisha:shipwreck:active_shadow_candidate": 1,
    }
    assert summary["q6_actionable_followup_bucket_counts"] == {
        "shadow_observation": 1,
    }
    assert summary["q6_actionable_followup_by_hero_map"] == {
        "aisha:shipwreck:shadow_observation": 1,
    }
    assert summary["q6_actionable_under_by_by_hero_map"] == {
        "aisha:shipwreck": {
            "count": 1,
            "max": 186510,
            "median": 186510,
        }
    }


def test_q6_actionable_followup_marks_uncovered_low_bottom_shipwreck() -> None:
    module = _review_module()
    row = module.review_row_from_artifact(
        _artifact(
            q6_below_drop_prior=True,
            truth_q6_count=2,
            truth_q6_value=486510,
            q6_decision_value_range="0 / 0 / 300,000",
            include_tail_shadow=False,
            evidence_profile_key="shape+layout",
            layout_bottom_row=9,
        ),
        source_path="low_bottom.json",
    )
    summary = module.summarize_review_rows([row])

    assert row["q6_actionable_shadow_status"] == "no_shadow_candidate"
    assert row["q6_actionable_followup_bucket"] == (
        "aisha_shipwreck_low_bottom_floor_risky"
    )
    assert row["q6_actionable_followup_reason"] == (
        "below_current_deep_floor_gate_and_no_q6_controls_raise"
    )
    assert summary["q6_actionable_followup_bucket_counts"] == {
        "aisha_shipwreck_low_bottom_floor_risky": 1,
    }
    assert summary["q6_actionable_followup_by_hero_map"] == {
        "aisha:shipwreck:aisha_shipwreck_low_bottom_floor_risky": 1,
    }


def test_hidden_tail_review_shadow_counts_as_pending_candidate() -> None:
    module = _review_module()

    status = module._q6_actionable_shadow_status(
        [
            {
                "label": "aisha_hidden_floor15",
                "active": True,
                "display_mode": "shadow_only_hidden_tail_review",
            }
        ],
        actionable=True,
    )

    assert status == "active_pending_shadow_candidate"


def test_zero_match_with_fallback_is_counted_separately() -> None:
    module = _review_module()
    row = module.review_row_from_artifact(
        _artifact(v2_match="0/80", fallback_active=True),
        source_path="zero_match.json",
    )
    summary = module.summarize_review_rows([row])

    flags = set(row["review_flags"].split(";"))
    assert "zero_posterior_match" in flags
    assert "zero_match_without_fallback" not in flags
    assert row["fallback_active"] is True
    assert row["fallback_action"] == "停止追价"
    assert row["fallback_match_text"] == "22/80"
    assert summary["zero_match_with_fallback_rows"] == 1
    assert summary["fallback_active_rows"] == 1
