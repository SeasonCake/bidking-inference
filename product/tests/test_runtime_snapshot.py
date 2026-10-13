import pytest

from bidking_lab.runtime import (
    action_result_rows_from_results,
    import_overview_from_summary,
    layout_replay_rows_from_stages,
    packet_action_rows_from_sends,
    player_bid_candidate_rows_from_bids,
    tactical_panel_from_rows,
    tactical_snapshot_from_rows,
    tactical_summary_rows,
    ui_contract_from_artifact,
)
from bidking_lab.live.layout import (
    LayoutEstimatePolicy,
    LayoutEvidence,
    LayoutItemEvidence,
)


def test_import_overview_from_summary_keeps_packet_metadata() -> None:
    overview = import_overview_from_summary(
        {
            "file": "sample.json",
            "packets": 103,
            "frames": 118,
            "states": 5,
            "batches": 5,
            "map_id": 2408,
            "round": 4,
            "inventory_count": 42,
            "inventory_cells": 114,
            "known_value_sum": 753522,
        }
    )

    assert overview.file == "sample.json"
    assert overview.packets == "103"
    assert overview.frames == "118"
    assert overview.states == "5"
    assert overview.live_batches == "5"
    assert overview.map_id == "2408"
    assert overview.round_no == "4"
    assert overview.settlement_items == "42"
    assert overview.settlement_cells == "114"
    assert overview.known_loot_value == "753522"


def test_fatbeans_diagnostic_rows_are_frontend_neutral() -> None:
    sends = [
        {"kind": "heartbeat", "value": 100136},
        {
            "kind": "action",
            "value": 100136,
            "sort_id": 65,
            "capture_time": "2026-05-29T11:19:08.979+08:00",
        },
    ]
    results = [
        {
            "action_id": 100136,
            "result": None,
            "observed_items": ("a", "b", "c", "d"),
        }
    ]
    bids = [
        {"name": "玩家A", "values": (288888, 750000, 444444)},
        {"name": "无出价", "values": ()},
    ]
    item_names = {100136: "宝光四鉴"}

    assert packet_action_rows_from_sends(sends, item_names) == (
        {
            "sort": "65",
            "时间": "19:08.979+08:00",
            "action_id": "100136",
            "道具": "宝光四鉴",
        },
    )
    assert action_result_rows_from_results(results, item_names) == (
        {
            "action_id": "100136",
            "道具": "宝光四鉴",
            "结果": "",
            "揭示物品数": "4",
        },
    )
    assert player_bid_candidate_rows_from_bids(bids) == (
        {
            "玩家": "玩家A",
            "最大出价候选": "750000",
            "values": "288888,750000,444444",
        },
    )


def test_tactical_snapshot_from_rows_keeps_summary_frontend_neutral() -> None:
    snapshot = tactical_snapshot_from_rows(
        bid_rows=[
            {
                "建议": "小幅进攻，等新信息确认",
                "当前最高": "爆米花 723,333",
                "风险带": "进攻区",
                "仓储": "后验 55/76/101 (中)",
                "停止价": "1,148,540",
                "证据": "结算前最后状态+总仓储",
            }
        ],
        warehouse_rows=[
            {
                "价值 P10/P50/P90": "144,190 / 380,266 / 872,719",
                "总格 P10/P50/P90": "55 / 76 / 101",
                "置信度": "中",
                "说明": "匹配样本或区间宽度仍有不确定性",
            }
        ],
        tool_rows=[
            {
                "道具": "随机抽检（2）",
                "信息ROI": 274.05,
                "价值区间压缩": "685,137",
                "仓储区间压缩": 45,
            }
        ],
        layout_stage_rows=[
            {
                "阶段": "R2 / sort 45",
                "已知格": 157,
                "覆盖": "100%",
                "最深行": 17,
                "布局估计": "157",
                "置信": "高",
                "风险": "低",
            }
        ],
    )

    assert snapshot.price_decision == "小幅进攻，等新信息确认"
    assert snapshot.highest_bid == "爆米花 723,333"
    assert snapshot.risk_band == "进攻区"
    assert snapshot.stop_price == "1,148,540"
    assert snapshot.evidence == "结算前最后状态+总仓储"
    assert snapshot.warehouse_note == "中；匹配样本或区间宽度仍有不确定性"
    assert snapshot.value_range == "144,190 / 380,266 / 872,719"
    assert snapshot.warehouse_range == "55 / 76 / 101"
    assert snapshot.next_tool_hint == (
        "随机抽检（2），ROI 274.05；价值压缩 685,137，仓储压缩 45"
    )
    assert len(snapshot.layout_stages) == 1
    assert snapshot.layout_stages[0].stage == "R2 / sort 45"


def test_tactical_summary_rows_match_compact_panel_shape() -> None:
    snapshot = tactical_snapshot_from_rows(
        bid_rows=[
            {
                "建议": "可守不抢",
                "当前最高": "玩家A 300,000",
                "风险带": "防守区",
                "停止价": "420,000",
                "证据": "R2 MC 后验",
            }
        ],
        warehouse_rows=[
            {
                "价值 P10/P50/P90": "200,000 / 350,000 / 500,000",
                "总格 P10/P50/P90": "80 / 100 / 125",
                "置信度": "中",
                "说明": "仓储区间仍宽",
            }
        ],
        tool_rows=[],
    )

    rows = tactical_summary_rows(snapshot)

    assert [row.topic for row in rows] == [
        "当前最高价是否可追",
        "当前价值区间",
        "当前仓储区间",
        "下一次优先使用道具",
    ]
    assert rows[0].conclusion == "可守不抢"
    assert rows[0].detail == "玩家A 300,000 / 防守区 / 停止价 420,000"
    assert rows[1].detail == "R2 MC 后验"
    assert rows[2].detail == "中；仓储区间仍宽"
    assert rows[3].conclusion == "暂无建议"


def test_tactical_snapshot_prefers_decision_value_and_keeps_raw_detail() -> None:
    snapshot = tactical_snapshot_from_rows(
        bid_rows=[
            {
                "建议": "可守不抢",
                "证据": "v2 decision_value",
                "决策价值 P10/P50/P90": "180,000 / 260,000 / 420,000",
                "原始价值 P10/P50/P90": "180,000 / 260,000 / 1,200,000",
                "上界风险": "高 / raw P90 +780,000",
                "后验诊断": "relaxed_exact_bucket_targets:q4:count=3:cells=9",
            }
        ],
        warehouse_rows=[
            {
                "价值 P10/P50/P90": "200,000 / 350,000 / 500,000",
                "总格 P10/P50/P90": "80 / 100 / 125",
            }
        ],
    )

    assert snapshot.value_range == "180,000 / 260,000 / 420,000"
    assert snapshot.evidence == (
        "v2 decision_value；raw 180,000 / 260,000 / 1,200,000；"
        "上界 高 / raw P90 +780,000；"
        "relaxed_exact_bucket_targets:q4:count=3:cells=9"
    )


def test_tactical_panel_from_rows_keeps_render_sections_together() -> None:
    panel = tactical_panel_from_rows(
        bid_rows=[
            {
                "建议": "小幅进攻",
                "当前最高": "玩家A 500,000",
                "风险带": "进攻区",
                "停止价": 650000,
            },
            {
                "建议": "",
                "当前最高": "玩家B 480,000",
            },
        ],
        warehouse_rows=[
            {
                "总格 P10/P50/P90": "90 / 120 / 150",
                "价值 P10/P50/P90": "300k / 500k / 800k",
                "置信度": "中",
            }
        ],
        tool_rows=[
            {
                "道具": "随机抽检（2）",
                "信息ROI": 12.5,
                "价值压缩": 100000,
                "仓储压缩": 8,
            }
        ],
        layout_stage_rows=[
            {
                "阶段": "R1 / sort 10",
                "已知格": 40,
                "覆盖": "33%",
                "最深行": 12,
                "布局估计": "弱",
                "置信": "低",
                "风险": "中",
            }
        ],
        layout_note="底部稀疏，只作布局深度证据",
    )

    assert [row.topic for row in panel.summary_rows] == [
        "当前最高价是否可追",
        "当前价值区间",
        "当前仓储区间",
        "下一次优先使用道具",
    ]
    assert panel.layout_stages[0].stage == "R1 / sort 10"
    assert panel.warehouse_rows == (
        {
            "总格 P10/P50/P90": "90 / 120 / 150",
            "价值 P10/P50/P90": "300k / 500k / 800k",
            "置信度": "中",
        },
    )
    assert panel.tool_rows[0]["信息ROI"] == "12.5"
    assert panel.bid_rows == (
        {
            "建议": "小幅进攻",
            "当前最高": "玩家A 500,000",
            "风险带": "进攻区",
            "停止价": "650000",
        },
    )
    assert panel.layout_note == "底部稀疏，只作布局深度证据"


def test_ui_contract_separates_baseline_and_shadow_references() -> None:
    contract = ui_contract_from_artifact(
        {
            "file": "sample.json",
            "created_at": 123.0,
            "processing_seconds": 1.25,
            "n_trials": 200,
            "shadow_trials": 80,
            "session_id": "2501:session-a",
            "hero": "aisha",
            "map_id": 2501,
            "round": 4,
            "known_value_sum": 800000,
            "inventory_count": 36,
            "inventory_cells": 108,
            "inference_input_constraints": {
                "mode": "pre_settlement_trusted_totals",
                "warehouse_total_cells": {
                    "value": 108,
                    "source": "packet",
                    "confidence": "exact",
                    "sequence": 80,
                },
                "total_item_count": {
                    "value": 36,
                    "source": "packet",
                    "confidence": "exact",
                    "sequence": 80,
                },
            },
            "bid_rows": [
                {
                    "建议": "可守不抢",
                    "当前最高": "玩家A 500,000",
                    "风险带": "防守区",
                    "探价(P10)": "300,000",
                    "防守价": "450,000",
                    "抢仓上限": "620,000",
                    "停止价": "680,000",
                    "证据": "v2 decision_value",
                    "轮次": "R4/5",
                    "信息强度": "中",
                }
            ],
            "v2_posterior_rows": [
                {
                    "匹配": "12/200",
                    "价值口径": "decision_value",
                    "决策价值 P10/P50/P90": "300,000 / 500,000 / 700,000",
                    "原始价值 P10/P50/P90": "300,000 / 540,000 / 900,000",
                    "剩余空间 P10/P50/P90": "12 / 22 / 34",
                    "q6空间压力 P10/P50/P90": "0.00 / 0.22 / 0.55",
                    "q6空间溢出率": "3.0%",
                    "q6样本率": "12.0%",
                    "q6掉落先验": "80.0%",
                    "q6先验件数": "1.50",
                    "q6先验格数": "7.0",
                    "q6先验价值": "486,510",
                    "q6决策价值 P10/P50/P90": "0 / 120,000 / 300,000",
                    "q6件数 P10/P50/P90": "0 / 1 / 1",
                    "q6格数 P10/P50/P90": "0 / 4 / 6",
                    "q6先验风险": "是",
                    "q6先验缺口": "件数P90低1.00",
                    "q6先验风险参考": "486,510",
                    "q6实战门控": "shipwreck_positive_net",
                    "q6实战参考P90": "486,510",
                    "诊断": "q6_below_drop_prior:0.12<prior:0.80",
                }
            ],
            "warehouse_rows": [
                {
                    "价值 P10/P50/P90": "320,000 / 540,000 / 760,000",
                    "总格 P10/P50/P90": "90 / 108 / 130",
                }
            ],
            "fallback_map_rows": [
                {
                    "匹配": "18/80",
                    "价值 P10/P50/P90": "220,000 / 420,000 / 620,000",
                    "总格 P10/P50/P90": "95 / 118 / 140",
                }
            ],
            "fallback_warehouse_rows": [
                {
                    "匹配": "18/80",
                    "置信度": "低",
                    "价值 P10/P50/P90": "220,000 / 420,000 / 620,000",
                    "总格 P10/P50/P90": "95 / 118 / 140",
                }
            ],
            "fallback_bid_rows": [
                {
                    "建议": "低置信防守",
                    "当前最高": "玩家A 500,000",
                    "风险带": "高风险抢仓",
                    "探价(P10)": "220,000",
                    "防守价": "294,000",
                    "抢仓上限": "560,000",
                    "停止价": "620,000",
                    "证据": "v1 fallback（v2无匹配）",
                    "轮次": "R4/5",
                    "信息强度": "低",
                    "仓储": "后验 95/118/140 (低)",
                    "依据": "早期/低信息阶段保守折价",
                    "补信息": "优先补轮廓或具体物品",
                    "价值口径": "raw_value",
                    "原始价值 P10/P50/P90": "220,000 / 420,000 / 620,000",
                    "fallback": "是",
                    "fallback_mode": "v1_map_prior_zero_match",
                    "fallback_note": "v2 后验无匹配时的 map-prior 低置信参考；不替代 baseline v2",
                },
                {
                    "证据": "玩家价位",
                    "当前最高": "玩家A 500,000",
                    "风险带": "高风险抢仓",
                }
            ],
            "panel": {
                "layout_stages": [
                    {
                        "stage": "R4 / sort 80",
                        "known_cells": "108",
                        "estimate": "108/120/140",
                        "confidence": "中",
                        "risk": "中",
                    }
                ]
            },
            "q6_residual_sampler_shadows": [
                {
                    "label": "profile_b5",
                    "active": True,
                    "gate": "shipwreck_profile_v1",
                    "evidence_profile_key": "shape+layout",
                    "trials": 80,
                    "q6_decision_value_p90": 420000,
                    "q6_count_p90": 2,
                    "q6_cells_p90": 8,
                },
                {
                    "label": "aisha_deep_floor1",
                    "active": True,
                    "gate": "aisha_shipwreck_deep_v1",
                    "evidence_profile_key": "shape+layout",
                    "trials": 80,
                    "q6_decision_value_p90": 486510,
                    "q6_count_p90": 2,
                    "q6_cells_p90": 9,
                },
                {
                    "label": "aisha_deep11_floor1",
                    "active": True,
                    "gate": "aisha_shipwreck_deep11_v1",
                    "evidence_profile_key": "shape+layout",
                    "trials": 80,
                    "q6_decision_value_p90": 520000,
                    "q6_count_p90": 3,
                    "q6_cells_p90": 12,
                },
                {
                    "label": "aisha_hidden_floor15",
                    "active": True,
                    "gate": "aisha_hidden_v1",
                    "evidence_profile_key": "shape+layout",
                    "trials": 80,
                    "q6_decision_value_p90": 1400000,
                    "q6_count_p90": 5,
                    "q6_cells_p90": 18,
                },
                {
                    "label": "aisha_villa_floor05",
                    "active": True,
                    "gate": "aisha_villa_shape_layout_v1",
                    "evidence_profile_key": "shape+layout",
                    "trials": 80,
                    "q6_decision_value_p90": 380000,
                    "q6_count_p90": 1,
                    "q6_cells_p90": 4,
                },
                {
                    "label": "ethan_shipwreck_layout_conditional_c4_cells15",
                    "active": True,
                    "gate": "ethan_shipwreck_layout_v1",
                    "evidence_profile_key": "public:random_avg+layout",
                    "trials": 80,
                    "q6_decision_value_p90": 510000,
                    "q6_count_p90": 4,
                    "q6_cells_p90": 15,
                },
            ],
            "category_grid_items": [
                {
                    "category": 108,
                    "category_label": "能源",
                    "quality": 6,
                    "item_id": 1086001,
                    "item_name": "民用垂直起降飞行器",
                    "local_index": 14,
                    "cells": 16,
                    "shape_key": "44",
                    "row": 2,
                    "col": 4,
                    "width": 4,
                    "height": 4,
                    "source": "packet",
                },
                {
                    "category": 106,
                    "category_label": "古董",
                    "quality": 5,
                    "item_id": 1065001,
                    "item_name": "青铜古镜",
                    "local_index": 40,
                    "cells": 4,
                    "shape_key": "22",
                    "row": 5,
                    "col": 1,
                    "width": 2,
                    "height": 2,
                    "source": "packet",
                },
            ],
            "action_send_rows": [
                {
                    "sort": 65,
                    "time": "2026-06-03 00:49:33.683",
                    "action_id": 100136,
                    "tool": "宝光四鉴",
                },
            ],
            "action_result_rows": [
                {
                    "sort": 66,
                    "time": "2026-06-03 00:49:34.620",
                    "action_id": 100136,
                    "tool": "宝光四鉴",
                    "result": 12,
                    "result_field": 14,
                    "revealed_items": 0,
                },
            ],
            "model_eval": {
                "final_value": 800000,
                "final_cells": 108,
                "final_q5_count": 1,
                "final_q5_cells": 4,
                "final_q5_value": 120000,
                "final_q6_count": 1,
                "final_q6_cells": 16,
                "final_q6_value": 486510,
                "final_top_item_name": "沉船红货",
                "final_top_item_quality": 6,
                "final_top_item_value": 486510,
                "final_top_item_cells": 16,
                "q6_residual_boost_shadow_q6_p90_delta": 120000,
                "q6_residual_boost_shadow_helped": True,
                "q6_residual_deep_floor_shadow_q6_p90_delta": 186510,
                "q6_residual_deep_floor_shadow_helped": True,
                "q6_residual_deep11_floor_shadow_q6_p90_delta": 220000,
                "q6_residual_deep11_floor_shadow_helped": True,
                "q6_residual_ethan_shipwreck_layout_conditional_shadow_q6_p90_delta": 210000,
                "q6_residual_ethan_shipwreck_layout_conditional_shadow_helped": True,
                "shape_target_count": 1,
                "category_target_count": 2,
                "category_exclusion_count": 1,
                "anchor_count": 3,
                "random_sample_avg_values": "n=6:avg=96897.66",
                "random_sample_avg_signal_values": "n=6:avg=96897.66",
                "public_constraint_key": "max_quality",
                "evidence_profile_key": "public:max_quality+random_avg+tool:category+shape",
                "evidence_stage": "full_5",
                "information_density_score": 5,
                "information_density_band": "high",
                "posterior_diagnostics": "public_max_quality:6",
                "layout_conflict": False,
                "q6_aisha_bottom_row_risk": True,
                "layout_bottom_row": 16,
                "layout_bottom_row_risk_threshold": 15,
                "q6_quality_only_local_count": 1,
                "q6_quality_only_deepest_local_index": 142,
                "q6_quality_only_deepest_start_row": 15,
                "q6_quality_only_deep_local_risk": True,
                "q6_quality_only_deep_row_threshold": 13,
                "q6_p90_misses_truth": True,
                "q6_plannable_p90_misses_truth": False,
                "q6_tail_replacement_p90_misses_truth": True,
                "v2_q6_tail_replacement_decision_value_p90_under_by": 93_000,
                "v2_q6_tail_replacement_estimate_p90": 80_000,
                "q6_tail_replacement_estimate_p90_misses_truth": True,
                "v2_q6_tail_replacement_estimate_p90_under_by": 13_000,
                "q6_false_low_risk": False,
                "q6_below_drop_prior": True,
                "q6_top_size_band": "q6_top_large",
                "q6_no_plannable_control": True,
                "q6_zero_q6_proven_control": False,
                "final_q6_decision_value": 0,
                "final_q6_trimmed_tail_value": 486_510,
                "final_q6_tail_replacement_value": 93_000,
                "final_q6_tail_replacement_count": 1,
                "final_q6_tail_replacement_items": "tail:486510->93000",
                "final_q6_tail_replacement_source": "map_weighted_p50",
                "final_q6_decision_value_with_tail_replacement": 93_000,
                "relaxed_exact_used": False,
                "monitor_processing_seconds": 1.25,
                "monitor_n_trials": 200,
                "monitor_roi_trials": 50,
                "monitor_shadow_trials": 80,
            },
        }
    )

    assert contract["schema_version"] == 1
    assert contract["mode"] == "baseline_first_shadow_reference"
    assert contract["source"]["n_trials"] == 200
    assert contract["context"]["session_id"] == "2501:session-a"
    assert contract["actions"]["latest_result"]["tool"] == "宝光四鉴"
    assert contract["actions"]["latest_result"]["result"] == "12"
    assert contract["baseline"]["official"] is True
    assert contract["baseline"]["affects_bid"] is True
    assert contract["baseline"]["decision"]["action"] == "可守不抢"
    assert contract["baseline"]["decision"]["probe_bid"] == "300,000"
    assert contract["baseline"]["decision"]["defend_bid"] == "450,000"
    assert contract["baseline"]["decision"]["attack_bid"] == "620,000"
    assert contract["baseline"]["posterior"]["value_basis"] == "decision_value"
    assert contract["baseline"]["posterior"]["match_text"] == "12/200"
    assert contract["baseline"]["posterior"]["matched"] == 12
    assert contract["baseline"]["posterior"]["total"] == 200
    assert contract["baseline"]["posterior"]["status"] == "matched"
    assert contract["baseline"]["posterior"]["total_cells_range"] == "90 / 108 / 130"
    assert (
        contract["baseline"]["posterior"]["total_item_count_status"]
        == "exact_input_constraint"
    )
    assert contract["baseline"]["posterior"]["input_total_item_count"] == 36
    assert contract["baseline"]["posterior"]["input_warehouse_total_cells"] == 108
    assert contract["baseline"]["posterior"]["q6_prior_expected_count"] == "1.50"
    assert contract["baseline"]["posterior"][
        "remaining_cells_after_layout_range"
    ] == "12 / 22 / 34"
    assert contract["baseline"]["posterior"]["q6_space_overflow_rate"] == "3.0%"
    assert contract["fallback"]["active"] is True
    assert contract["fallback"]["affects_bid"] is False
    assert contract["fallback"]["mode"] == "v1_map_prior_zero_match"
    assert contract["fallback"]["decision"]["action"] == "低置信防守"
    assert contract["fallback"]["decision"]["probe_bid"] == "220,000"
    assert contract["fallback"]["decision"]["defend_bid"] == "294,000"
    assert contract["fallback"]["decision"]["warehouse_status"] == "后验 95/118/140 (低)"
    assert contract["fallback"]["decision"]["rationale"] == "早期/低信息阶段保守折价"
    assert contract["fallback"]["decision"]["next_info_hint"] == "优先补轮廓或具体物品"
    assert contract["fallback"]["decision"]["player_risks"] == [
        {
            "current_bid": "玩家A 500,000",
            "risk_band": "高风险抢仓",
        }
    ]
    assert contract["fallback"]["posterior"]["match_text"] == "18/80"
    assert contract["fallback"]["posterior"]["confidence"] == "低"
    assert contract["q6_risk_reference"] == {
        "risk": True,
        "prior_gap": "件数P90低1.00",
        "prior_reference_p90": "486,510",
        "practical_gate": "shipwreck_positive_net",
        "practical_reference_p90": "486,510",
        "display_mode": "risk_reference",
        "affects_bid": False,
        "bid_floor_applied": False,
            "minimum_bid_floor": "",
            "note": (
                "q6 risk is displayed as a reference only; formal bid thresholds "
                "are recomputed from the current formal_mode."
            ),
        }
    assert [shadow["label"] for shadow in contract["shadows"]] == [
        "profile_b5",
        "aisha_deep_floor1",
        "aisha_deep11_floor1",
        "aisha_hidden_floor15",
        "aisha_villa_floor05",
        "ethan_shipwreck_layout_conditional_c4_cells15",
    ]
    assert contract["shadows"][0]["role"] == "diagnostic_shadow"
    assert contract["shadows"][0]["display_mode"] == "debug_only"
    assert contract["shadows"][0]["affects_bid"] is False
    assert contract["shadows"][0]["q6_p90_delta"] == 120000
    assert contract["shadows"][1]["role"] == "tail_risk_reference_candidate"
    assert contract["shadows"][1]["display_mode"] == "risk_reference_candidate"
    assert contract["shadows"][2]["role"] == "aisha_deep11_tail_risk_shadow"
    assert (
        contract["shadows"][2]["display_mode"]
        == "shadow_only_aisha_deep11_review"
    )
    assert contract["shadows"][2]["q6_p90_delta"] == 220000
    assert contract["shadows"][3]["role"] == "hidden_tail_risk_shadow"
    assert contract["shadows"][3]["display_mode"] == "shadow_only_hidden_tail_review"
    assert contract["shadows"][4]["role"] == "villa_tail_risk_shadow"
    assert (
        contract["shadows"][4]["display_mode"]
        == "shadow_only_pending_no_q6_controls"
    )
    assert contract["shadows"][5]["role"] == "shipwreck_layout_q6_likelihood_shadow"
    assert (
        contract["shadows"][5]["display_mode"]
        == "shadow_only_ethan_shipwreck_q6_likelihood_review"
    )
    assert contract["shadows"][5]["q6_p90_delta"] == 210000
    assert contract["minimap"]["status"] == "available"
    assert contract["minimap"]["known_items"] == 2
    assert contract["minimap"]["columns"] == 10
    assert contract["minimap"]["default_cells"] == 130
    assert contract["minimap"]["max_cells"] == 250
    assert contract["minimap"]["viewport_rows"] == 13
    assert contract["minimap"]["max_rows"] == 25
    assert contract["minimap"]["rows_hint"] == 13
    assert contract["minimap"]["scrollable"] is False
    assert contract["minimap"]["quality_counts"] == {"q5": 1, "q6": 1}
    assert contract["minimap"]["category_counts"] == {"能源": 1, "古董": 1}
    assert contract["minimap"]["items"][0]["row"] == 2
    assert contract["minimap"]["items"][0]["width"] == 4
    assert contract["minimap"]["items"][0]["item_name"] == "民用垂直起降飞行器"
    assert contract["minimap"]["items"][0]["display_label"] == ""
    assert "short_name" not in contract["minimap"]["items"][0]
    assert "Q6" in contract["minimap"]["items"][0]["tooltip"]
    assert contract["truth"]["available"] is True
    assert contract["truth"]["complete"] is True
    assert contract["truth"]["partial"] is False
    assert contract["truth"]["source"] == "settlement_or_sample_replay"
    assert contract["truth"]["total_items"] == 36
    assert contract["truth"]["total_cells"] == 108
    assert contract["truth"]["q6"] == {
        "count": 1,
        "cells": 16,
        "value": 486510,
        "decision_value": 0,
        "trimmed_tail_value": 486510,
        "tail_replacement_value": 93000,
        "decision_value_with_tail_replacement": 93000,
    }
    assert contract["truth"]["top_item"]["name"] == "沉船红货"
    assert contract["constraints"]["summary"]["known_gold_item_count"] == 1
    assert contract["constraints"]["summary"]["known_red_item_count"] == 1
    assert contract["constraints"]["summary"]["category_exclusion_count"] == 1
    assert contract["constraints"]["summary"]["input_total_item_count"] == 36
    assert contract["constraints"]["summary"]["input_warehouse_total_cells"] == 108
    assert contract["constraints"]["public_info"]["input_constraints_mode"] == (
        "pre_settlement_trusted_totals"
    )
    assert contract["constraints"]["public_info"]["public_constraint_key"] == (
        "max_quality"
    )
    assert contract["constraints"]["exclusions"]["note"] == (
        "specific_category_ids_are_in_posterior_diagnostics"
    )
    assert contract["diagnostics"]["posterior"] == "public_max_quality:6"
    assert contract["diagnostics"]["layout"]["bottom_row_risk"] is True
    assert contract["diagnostics"]["q6"]["below_drop_prior"] is True
    assert contract["diagnostics"]["q6"]["no_plannable_control"] is True
    assert contract["diagnostics"]["q6"]["zero_q6_proven_control"] is False
    assert contract["diagnostics"]["q6"]["plannable_p90_misses_truth"] is False
    assert contract["diagnostics"]["q6"]["tail_replacement_p90_misses_truth"] is True
    assert contract["diagnostics"]["q6"]["tail_replacement_p90_under_by"] == 93000
    assert contract["diagnostics"]["q6"]["tail_replacement_estimate_p90"] == 80000
    assert (
        contract["diagnostics"]["q6"][
            "tail_replacement_estimate_p90_misses_truth"
        ]
        is True
    )
    assert contract["diagnostics"]["q6"]["tail_replacement_estimate_p90_under_by"] == 13000
    assert contract["diagnostics"]["q6"]["tail_replacement_count"] == 1
    assert contract["diagnostics"]["q6"]["tail_replacement_items"] == (
        "tail:486510->93000"
    )
    assert contract["diagnostics"]["q6"]["quality_only_local_count"] == 1
    assert contract["diagnostics"]["q6"]["quality_only_deepest_local_index"] == 142
    assert contract["diagnostics"]["q6"]["quality_only_deepest_start_row"] == 15
    assert contract["diagnostics"]["q6"]["quality_only_deep_local_risk"] is True
    assert contract["diagnostics"]["q6"]["quality_only_deep_row_threshold"] == 13
    assert contract["diagnostics"]["sampling"]["n_trials"] == 200
    assert contract["interaction"]["compact"]["purpose"] == "always_on_top_core_tips"
    assert "constraints.summary" in contract["interaction"]["hover"]["fields"]
    assert "truth" in contract["interaction"]["detail"]["fields"]
    assert contract["interaction"]["detail"]["collapsible"] is True
    assert contract["interaction"]["detail"]["renderers"] == ()


def test_ui_contract_marks_incomplete_settlement_as_partial_truth() -> None:
    contract = ui_contract_from_artifact(
        {
            "known_value_sum": 107_049,
            "inventory_count": 17,
            "inventory_cells": 38,
            "settlement_incomplete": True,
            "settlement_incomplete_detail": {
                "captured_items": 17,
                "declared_items": 30,
                "captured_value": 107_049,
                "source": "action:100115",
            },
            "final_q6_count": 0,
            "final_q6_cells": 0,
            "final_q6_value": 0,
        }
    )

    truth = contract["truth"]
    assert truth["available"] is True
    assert truth["complete"] is False
    assert truth["partial"] is True
    assert truth["source"] == "partial_settlement_capture"
    assert truth["captured_items"] == 17
    assert truth["captured_cells"] == 38
    assert truth["declared_items"] == 30
    assert truth["declared_cells"] is None
    assert truth["total_value"] == 107_049
    assert truth["total_items"] == 17
    assert truth["total_cells"] == 38
    assert truth["q6"]["count"] == 0
    assert truth["q6"]["cells"] == 0
    assert truth["q6"]["value"] == 0


def _strict_settlement_artifact(**changes: object) -> dict[str, object]:
    artifact: dict[str, object] = {
        "phase": "settled",
        "known_value_sum": 2_163_815,
        "inventory_count": 56,
        "inventory_cells": 154,
        "settlement_incomplete": False,
        "settlement_parse_residual": 0,
        "final_quality_counts": "q1=3;q2=10;q3=15;q4=12;q5=8;q6=8",
        "final_quality_cells": "q1=3;q2=25;q3=43;q4=34;q5=27;q6=22",
    }
    artifact.update(changes)
    return artifact


def test_ui_contract_promotes_only_closed_strict_settlement_truth() -> None:
    truth = ui_contract_from_artifact(_strict_settlement_artifact())["truth"]

    assert truth["available"] is True
    assert truth["complete"] is True
    assert truth["partial"] is False
    assert truth["captured_items"] == 56
    assert truth["captured_cells"] == 154
    assert truth["declared_items"] is None
    assert truth["declared_cells"] is None
    assert truth["total_items"] == 56
    assert truth["total_cells"] == 154


@pytest.mark.parametrize("settlement_parse_residual", (1, 2462))
def test_ui_contract_treats_parse_residual_as_diagnostic_only(
    settlement_parse_residual: int,
) -> None:
    truth = ui_contract_from_artifact(
        _strict_settlement_artifact(
            settlement_parse_residual=settlement_parse_residual,
        )
    )["truth"]

    assert truth["complete"] is True
    assert truth["partial"] is False
    assert truth["total_items"] == 56
    assert truth["total_cells"] == 154


def test_ui_contract_keeps_optional_matching_declared_settlement_fields() -> None:
    truth = ui_contract_from_artifact(
        _strict_settlement_artifact(
            settlement_incomplete_detail={
                "captured_items": 56,
                "declared_items": 56,
                "declared_cells": 154,
            }
        )
    )["truth"]

    assert truth["complete"] is True
    assert truth["declared_items"] == 56
    assert truth["declared_cells"] == 154


def test_ui_contract_rejects_conflicting_declared_settlement_cells() -> None:
    truth = ui_contract_from_artifact(
        _strict_settlement_artifact(
            settlement_incomplete_detail={
                "captured_items": 56,
                "declared_items": 56,
                "declared_cells": 153,
            }
        )
    )["truth"]

    assert truth["available"] is True
    assert truth["complete"] is False
    assert truth["partial"] is True
    assert truth["declared_cells"] == 153


def test_ui_contract_rejects_unclosed_quality_settlement_truth() -> None:
    truth = ui_contract_from_artifact(
        _strict_settlement_artifact(
            final_quality_cells="q1=3;q2=25;q3=43;q4=34;q5=27;q6=21"
        )
    )["truth"]

    assert truth["available"] is True
    assert truth["complete"] is False
    assert truth["partial"] is True


def test_layout_replay_rows_from_stages_are_frontend_neutral() -> None:
    layout = LayoutEvidence(
        sequence=12,
        items=(
            LayoutItemEvidence(
                cells=4,
                row=1,
                col=1,
                width=2,
                height=2,
                bottom_row=2,
                right_col=2,
                quality=4,
                shape_key="22",
                local_index=0,
            ),
        ),
        max_row=2,
        total_cells=4,
        bounding_cells=20,
        sparsity_ratio=0.8,
        bottom_tail_item_count=1,
        known_quality_count=1,
        unknown_quality_count=0,
    )
    stage = {
        "sort_id": 99,
        "round_no": 2,
        "phase": "reading",
        "layout": layout,
        "final_total_cells": 40,
        "known_cell_ratio": 0.1,
        "bounding_cell_error": -20,
    }

    rows = layout_replay_rows_from_stages((stage,))

    assert rows == (
        {
            "sort": "99",
            "R": "2",
            "phase": "reading",
            "已知件": "1",
            "已知格": "4",
            "最深行": "2",
            "边界格": "20",
            "空洞率": "80%",
            "最终格": "40",
            "已知覆盖": "10%",
            "边界误差": "-20",
            "布局估计": "4/?/?",
            "估计置信": "低",
            "风险": "低：底部证据稀疏，仓储可能被高估或低估",
        },
    )


def test_layout_replay_rows_can_include_sample_fit_estimate() -> None:
    layout = LayoutEvidence(
        sequence=12,
        items=(
            LayoutItemEvidence(
                cells=30,
                row=1,
                col=1,
                width=5,
                height=6,
                bottom_row=6,
                right_col=5,
                quality=4,
                shape_key="56",
                local_index=0,
            ),
            LayoutItemEvidence(
                cells=6,
                row=9,
                col=1,
                width=3,
                height=2,
                bottom_row=10,
                right_col=3,
                quality=3,
                shape_key="32",
                local_index=80,
            ),
        ),
        max_row=10,
        total_cells=36,
        bounding_cells=100,
        sparsity_ratio=0.40,
        bottom_tail_item_count=2,
        known_quality_count=2,
        unknown_quality_count=0,
    )
    stage = {
        "sort_id": 99,
        "round_no": 2,
        "phase": "reading",
        "layout": layout,
        "final_total_cells": 95,
        "known_cell_ratio": 36 / 95,
        "bounding_cell_error": 5,
    }

    rows = layout_replay_rows_from_stages(
        (stage,),
        comparison_policy=LayoutEstimatePolicy(
            name="sample-fit-test",
            medium_p50_margin=6,
        ),
    )

    assert rows[0]["布局估计"] == "36/80/100"
    assert rows[0]["样本拟合估计"] == "36/94/100"


def test_tactical_snapshot_from_rows_has_empty_state_defaults() -> None:
    snapshot = tactical_snapshot_from_rows()

    assert snapshot.price_decision == "暂无出价后验"
    assert snapshot.value_range == "暂无后验"
    assert snapshot.warehouse_range == "暂无后验"
    assert snapshot.next_tool_hint == "暂无建议"
    assert snapshot.layout_stages == ()


def test_ui_contract_exposes_size_bucket_diagnostics() -> None:
    contract = ui_contract_from_artifact(
        {
            "action_result_rows": [
                {
                    "action_id": 100172,
                    "tool": "四格均价",
                    "result": 120000,
                    "sort": 5,
                }
            ],
            "v2_posterior_rows": [
                {
                    "诊断": (
                        "size_bucket:4:avg=120000:tier=plane_yongle_singleton:"
                        "strength=soft"
                    ),
                }
            ],
        }
    )
    size_bucket = contract["diagnostics"]["size_bucket"]
    assert size_bucket["reading_active"] is True
    assert size_bucket["active"] is True
    assert "四格均价" in size_bucket["latest_reading_label"]
    assert "4格均价" in size_bucket["latest_target_label"]
    assert size_bucket["inference_matches_reading"] is True


def test_ui_contract_exposes_v3_practical_diagnostics_shadow_only() -> None:
    contract = ui_contract_from_artifact(
        {
            "model_eval": {
                "v3_practical_available": True,
                "v3_practical_ready": False,
                "v3_practical_affects_bid": False,
                "v3_practical_active": False,
                "v3_practical_candidate": True,
                "v3_practical_source": "formal_value",
                "v3_practical_mode": "value_floor_watch",
                "v3_practical_status": "watch_raise_candidate",
                "v3_practical_recommendation": "raise_watch",
                "v3_practical_confidence": "medium",
                "v3_practical_source_lanes": "formal_value,underestimate_repair",
                "v3_practical_risk_flags": "value_floor_candidate,q6_under_candidate",
                "v3_practical_reason": "formal value floor lifts underestimation watch",
                "v3_practical_formal_decision_value_p50": 480000,
                "v3_practical_formal_decision_value_p90": 780000,
                "v3_practical_baseline_formal_decision_value_p50": 420000,
                "v3_practical_baseline_formal_decision_value_p90": 650000,
                "v3_practical_delta_formal_decision_value_p50": 60000,
                "v3_practical_delta_formal_decision_value_p90": 130000,
                "v3_practical_total_value_p90": 980000,
                "v3_practical_baseline_total_value_p90": 860000,
                "v3_practical_delta_total_value_p90": 120000,
                "v3_practical_raw_total_gap_to_formal_p90": 200000,
                "v3_practical_baseline_raw_total_gap_to_formal_p90": 210000,
                "v3_practical_q6_formal_decision_value_p50": 120000,
                "v3_practical_q6_formal_decision_value_p90": 260000,
                "v3_practical_baseline_q6_formal_decision_value_p50": 80000,
                "v3_practical_baseline_q6_formal_decision_value_p90": 210000,
                "v3_practical_delta_q6_formal_decision_value_p50": 40000,
                "v3_practical_delta_q6_formal_decision_value_p90": 50000,
                "v3_practical_q6_value_p90": 420000,
                "v3_practical_baseline_q6_value_p90": 360000,
                "v3_practical_delta_q6_value_p90": 60000,
                "v3_practical_q6_raw_gap_to_formal_p90": 160000,
                "v3_practical_baseline_q6_raw_gap_to_formal_p90": 150000,
            }
        }
    )

    practical = contract["diagnostics"]["v3_practical"]

    assert practical["available"] is True
    assert practical["affects_bid"] is False
    assert practical["active"] is False
    assert practical["recommendation"] == "raise_watch"
    assert practical["formal_decision_value_p90"] == 780000
    assert practical["baseline_formal_decision_value_p90"] == 650000
    assert practical["delta_formal_decision_value_p50"] == 60000
    assert practical["delta_formal_decision_value_p90"] == 130000
    assert practical["total_value_p90"] == 980000
    assert practical["raw_total_gap_to_formal_p90"] == 200000
    assert practical["q6_formal_decision_value_p90"] == 260000
    assert practical["baseline_q6_formal_decision_value_p90"] == 210000
    assert practical["delta_q6_formal_decision_value_p90"] == 50000
    assert practical["q6_value_p90"] == 420000
    assert practical["q6_raw_gap_to_formal_p90"] == 160000
    assert "diagnostics.v3_practical" in contract["interaction"]["hover"]["fields"]


def test_ui_contract_marks_v3_formal_mode_and_keeps_v2_reference() -> None:
    contract = ui_contract_from_artifact(
        {
            "formal_mode_requested": "v3_practical",
            "formal_mode": "v3_practical",
            "formal_mode_reason": "v3_practical_ready",
            "bid_rows": [
                {
                    "建议": "小幅进攻",
                    "当前最高": "玩家A 500,000",
                    "风险带": "进攻区",
                    "探价(P10)": "300,000",
                    "防守价": "560,000",
                    "抢仓上限": "900,000",
                    "停止价": "900,000",
                    "证据": "v3 practical formal",
                    "价值口径": "decision_value",
                    "决策价值 P10/P50/P90": "300,000 / 640,000 / 900,000",
                    "原始价值 P10/P50/P90": "340,000 / 700,000 / 1,050,000",
                }
            ],
            "v2_bid_rows": [
                {
                    "建议": "可守不抢",
                    "当前最高": "玩家A 500,000",
                    "风险带": "防守区",
                    "探价(P10)": "220,000",
                    "防守价": "420,000",
                    "抢仓上限": "650,000",
                    "停止价": "650,000",
                    "证据": "v2 decision_value",
                }
            ],
        }
    )

    assert contract["mode"] == "v3_practical_formal_with_v2_reference"
    assert contract["source"]["formal_mode"] == "v3_practical"
    assert contract["baseline"]["source"] == "v3_practical"
    assert contract["baseline"]["decision"]["action"] == "小幅进攻"
    assert contract["baseline"]["decision"]["stop_price"] == "900,000"
    assert (
        contract["baseline"]["posterior"]["decision_value_range"]
        == "300,000 / 640,000 / 900,000"
    )
    assert (
        contract["baseline"]["posterior"]["raw_value_range"]
        == "340,000 / 700,000 / 1,050,000"
    )
    assert contract["v2_reference"]["available"] is True
    assert contract["v2_reference"]["affects_bid"] is False
    assert contract["v2_reference"]["decision"]["action"] == "可守不抢"
    assert contract["v2_reference"]["decision"]["stop_price"] == "650,000"


def test_ui_contract_uses_exact_input_totals_when_posterior_range_is_missing() -> None:
    contract = ui_contract_from_artifact(
        {
            "inference_input_constraints": {
                "mode": "pre_settlement_trusted_totals",
                "warehouse_total_cells": {"value": 98},
                "total_item_count": {"value": 38},
            },
        }
    )

    posterior = contract["baseline"]["posterior"]

    assert posterior["total_cells_range"] == "98 / 98 / 98"
    assert posterior["total_item_count_range"] == "38 / 38 / 38"
    assert posterior["input_warehouse_total_cells"] == 98
    assert posterior["input_total_item_count"] == 38


def test_ui_contract_exposes_activity_map_alias() -> None:
    alias = {
        "mode": "activity_shipwreck_minus20",
        "source_map_id": 2527,
        "model_map_id": 2507,
        "family": "shipwreck",
    }
    contract = ui_contract_from_artifact(
        {
            "map_id": 2527,
            "model_map_id": 2507,
            "map_alias": alias,
            "map_alias_mode": "activity_shipwreck_minus20",
            "inference_input_constraints": {
                "mode": "pre_settlement",
                "map_alias": alias,
            },
        }
    )

    assert contract["source"]["model_map_id"] == 2507
    assert contract["source"]["map_alias_mode"] == "activity_shipwreck_minus20"
    assert contract["context"]["map_id"] == 2527
    assert contract["context"]["model_map_id"] == 2507
    assert contract["constraints"]["summary"]["map_alias_label"] == (
        "活动图 2527->旧沉船 2507"
    )


def test_ui_contract_distinguishes_red_crate_activity_map_alias() -> None:
    alias = {
        "mode": "red_crate_shipwreck_minus40",
        "source_map_id": 2545,
        "model_map_id": 2505,
        "family": "shipwreck",
    }
    contract = ui_contract_from_artifact(
        {
            "map_id": 2545,
            "model_map_id": 2505,
            "map_alias": alias,
            "map_alias_mode": "red_crate_shipwreck_minus40",
            "inference_input_constraints": {
                "mode": "pre_settlement",
                "map_alias": alias,
            },
        }
    )

    assert contract["constraints"]["summary"]["map_alias_label"] == (
        "红箱活动图 2545→旧沉船 2505"
    )


def test_ui_contract_exposes_zero_public_avg_cells() -> None:
    contract = ui_contract_from_artifact(
        {
            "public_info_rows": [
                {
                    "info_id": 200015,
                    "value": 0.0,
                    "value_field": 0,
                },
            ],
        }
    )

    public_info = contract["constraints"]["public_info"]

    assert public_info["public_avg_cells"] == [
        {
            "info_id": 200015,
            "semantic": "q5_avg_cells",
            "kind": "avg_cells",
            "quality": 5,
            "label": "金均格",
            "value": 0.0,
            "display_value": "0.00",
            "text": "金均格 0.00",
        }
    ]
    assert public_info["public_numeric_facts"][0]["semantic"] == "q5_avg_cells"


def test_ui_contract_exposes_public_numeric_soft_facts() -> None:
    contract = ui_contract_from_artifact(
        {
            "public_info_rows": [
                {
                    "info_id": 200013,
                    "value": 2.909090995788574,
                },
                {
                    "info_id": 200037,
                    "value": 32507.6,
                },
                {
                    "info_id": 200033,
                    "value": 15296.33,
                },
            ],
        }
    )

    public_info = contract["constraints"]["public_info"]

    assert public_info["public_avg_cells"][0]["semantic"] == "q4_avg_cells"
    assert public_info["public_avg_cells"][0]["display_value"] == "2.90"
    assert public_info["public_avg_values"][0]["text"] == "金均价 32,507.60"
    assert public_info["public_random_avg_values"][0]["text"] == (
        "随机9均价 15,296.33"
    )
    assert public_info["public_numeric_summary"] == (
        "紫均格 2.90 / 金均价 32,507.60 / 随机9均价 15,296.33"
    )


def test_ui_contract_exposes_public_auction_category_total_value() -> None:
    contract = ui_contract_from_artifact(
        {
            "public_info_rows": [
                {
                    "info_id": 200044,
                    "value": 1_430_456,
                    "value_field": 12,
                },
            ],
        }
    )

    public_info = contract["constraints"]["public_info"]

    assert public_info["public_exact_subset_values"] == [
        {
            "info_id": 200044,
            "semantic": "auction_category_total_value",
            "kind": "exact_subset_value",
            "label": "竞拍类型总价",
            "value": 1_430_456.0,
            "display_value": "1,430,456",
            "text": "竞拍类型总价 1,430,456",
        }
    ]
    assert public_info["public_numeric_summary"] == "竞拍类型总价 1,430,456"
    assert contract["diagnostics"]["rare_signals"]["public_info"][0]["info_id"] == 200044


def test_ui_contract_records_rare_signals_as_diagnostics() -> None:
    contract = ui_contract_from_artifact(
        {
            "action_send_rows": [
                {"action_id": 100121, "tool": "终极审计"},
            ],
            "action_result_rows": [
                {
                    "action_id": 100121,
                    "tool": "终极审计",
                    "result": 728211,
                    "revealed_items": 0,
                },
                {
                    "action_id": 100127,
                    "tool": "全知全能",
                    "result": None,
                    "revealed_items": 42,
                    "revealed_items_detail": [{"local_index": 1, "quality": 6}],
                },
                {
                    "action_id": 100114,
                    "tool": "珍品均格",
                    "result": 2.5,
                    "revealed_items": 0,
                },
            ],
            "public_info_rows": [
                {"info_id": 200035, "value": 18502.75},
                {"info_id": 200038, "value": 34288.75},
                {"info_id": 200031, "value": 124892.0},
            ],
        }
    )

    rare = contract["diagnostics"]["rare_signals"]

    assert rare["present"] is True
    assert rare["role_counts"] == {
        "diagnostic_only": 3,
        "ref_v0_constraint": 2,
        "soft_value_floor": 1,
    }
    assert rare["actions"][0]["action_id"] == 100114
    assert rare["actions"][1]["action_id"] == 100121
    assert rare["actions"][1]["ref_v0_role"] == "diagnostic_only"
    assert rare["actions"][2]["revealed_items"] == 42
    assert rare["actions"][2]["has_revealed_detail"] is True
    assert rare["public_info"][0]["info_id"] == 200031
    assert rare["public_info"][0]["ref_v0_role"] == "soft_value_floor"
    assert rare["public_info"][1]["info_id"] == 200035
    assert rare["public_info"][1]["ref_v0_role"] == "diagnostic_only"
    assert rare["public_info"][2]["info_id"] == 200038
    assert rare["public_info"][2]["ref_v0_role"] == "ref_v0_constraint"


def test_ui_contract_minimap_includes_quality_only_markers() -> None:
    contract = ui_contract_from_artifact(
        {
            "minimap_grid_items": [
                {
                    "category": 106,
                    "category_label": "古董",
                    "quality": 5,
                    "item_id": 1065001,
                    "item_name": "青铜古镜",
                    "local_index": 14,
                    "cells": 4,
                    "shape_key": "22",
                    "row": 2,
                    "col": 5,
                    "width": 2,
                    "height": 2,
                    "source": "packet",
                    "render_mode": "footprint",
                }
            ],
            "action_result_rows": [
                {
                    "sort": 66,
                    "time": "2026-06-03 00:49:34.620",
                    "action_id": 100136,
                    "tool": "宝光四鉴",
                    "result": 12,
                    "result_field": 14,
                    "revealed_items": 4,
                    "revealed_summary": "Q3x1 / Q2x3 / pos 14,76,75,43",
                    "revealed_items_detail": [
                        {
                            "local_index": 14,
                            "runtime_id": 101,
                            "item_id": None,
                            "quality": 2,
                            "value": None,
                            "shape_code": None,
                            "cells": None,
                        },
                        {
                            "local_index": 76,
                            "runtime_id": None,
                            "item_id": None,
                            "quality": 3,
                            "value": None,
                            "shape_code": None,
                            "cells": None,
                        },
                        {
                            "local_index": 75,
                            "runtime_id": None,
                            "item_id": None,
                            "quality": 2,
                            "value": None,
                            "shape_code": None,
                            "cells": None,
                        },
                        {
                            "local_index": 43,
                            "runtime_id": None,
                            "item_id": None,
                            "quality": 2,
                            "value": None,
                            "shape_code": None,
                            "cells": None,
                        },
                    ],
                }
            ],
            "public_info_rows": [
                {
                    "sort": 11,
                    "time": "2026-06-04 03:44:38.710",
                    "info_id": 200027,
                    "map_id": 2401,
                    "value": 6,
                    "value_field": 6,
                    "revealed_items": 1,
                    "revealed_summary": "Q4x1 / pos 44",
                    "revealed_items_detail": [
                        {
                            "local_index": 44,
                            "runtime_id": 501,
                            "item_id": None,
                            "quality": 4,
                            "value": None,
                            "shape_code": None,
                            "cells": None,
                        },
                        {
                            "local_index": None,
                            "runtime_id": 502,
                            "item_id": None,
                            "quality": 6,
                            "value": None,
                            "shape_code": None,
                            "cells": None,
                        },
                    ],
                },
            ],
        }
    )

    minimap = contract["minimap"]
    markers = [item for item in minimap["items"] if item["render_mode"] == "marker"]

    assert minimap["known_items"] == 5
    assert minimap["quality_counts"] == {"q2": 2, "q3": 1, "q4": 1, "q5": 1}
    assert minimap["quality_reveal_count"] == 6
    assert minimap["quality_reveal_counts"] == {
        "q2": 3,
        "q3": 1,
        "q4": 1,
        "q6": 1,
    }
    assert minimap["quality_reveal_marker_count"] == 4
    assert minimap["quality_reveal_unplaced_count"] == 1
    assert minimap["quality_reveal_unplaced_counts"] == {"q6": 1}
    assert {marker["local_index"] for marker in markers} == {43, 44, 75, 76}
    assert all(marker["width"] == 1 and marker["height"] == 1 for marker in markers)


def test_ui_contract_minimap_restores_omitted_local_zero_marker_identity() -> None:
    contract = ui_contract_from_artifact(
        {
            "public_info_rows": [
                {
                    "info_id": 200040,
                    "map_id": 2401,
                    "revealed_items_detail": [
                        {
                            "local_index": 21,
                            "runtime_id": 1741282964991057,
                            "quality": 6,
                        },
                        {
                            "local_index": 30,
                            "runtime_id": 1741282964991064,
                            "quality": 6,
                        },
                        {
                            # Raw 200040 omits protobuf field 1 for local index 0.
                            "local_index": None,
                            "runtime_id": 1741282964991045,
                            "quality": 6,
                        },
                        {
                            "local_index": 57,
                            "runtime_id": 1741282964991062,
                            "quality": 4,
                        },
                        {
                            "local_index": 67,
                            "runtime_id": 1741282964991062,
                            "quality": 4,
                        },
                    ],
                }
            ]
        }
    )

    minimap = contract["minimap"]
    markers = [item for item in minimap["items"] if item["render_mode"] == "marker"]
    local_zero = next(item for item in markers if item["local_index"] == 0)

    assert minimap["known_items"] == 4
    assert minimap["quality_counts"] == {"q4": 1, "q6": 3}
    assert minimap["quality_reveal_marker_count"] == 4
    assert minimap["quality_reveal_unplaced_count"] == 0
    assert local_zero["row"] == 1
    assert local_zero["col"] == 1
    assert local_zero["runtime_id"] == 1741282964991045
    assert sum(item["quality"] == 4 for item in markers) == 1


def test_ui_contract_minimap_preserves_named_public_marker() -> None:
    contract = ui_contract_from_artifact(
        {
            "public_info_rows": [
                {
                    "sort": 11,
                    "time": "2026-06-04 03:44:38.710",
                    "info_id": 200027,
                    "map_id": 2401,
                    "value": 6,
                    "value_field": 6,
                    "revealed_items": 1,
                    "revealed_summary": "Q6x1 / test_item / pos 42",
                    "revealed_items_detail": [
                        {
                            "local_index": 42,
                            "runtime_id": 501,
                            "item_id": 1001,
                            "item_name": "test_item",
                            "quality": 6,
                            "value": 20000,
                            "shape_code": 22,
                            "cells": 4,
                        },
                    ],
                },
            ],
        }
    )

    marker = contract["minimap"]["items"][0]
    assert marker["render_mode"] == "footprint"
    assert marker["width"] == 2
    assert marker["height"] == 2
    assert marker["item_id"] == 1001
    assert marker["item_name"] == "test_item"
    assert marker["display_label"] == ""
    assert marker["shape_key"] == "22"
    assert "test_item" in marker["tooltip"]


def test_ui_contract_minimap_grid_without_footprint_does_not_block_public_markers() -> None:
    contract = ui_contract_from_artifact(
        {
            "minimap_grid_items": [
                {
                    "quality": 4,
                    "runtime_id": 501,
                    "local_index": 45,
                    "cells": 1,
                    "source": "packet",
                    "layout_source": "live_grid",
                },
                {
                    "category": 106,
                    "category_label": "古董",
                    "quality": 5,
                    "item_id": 1065001,
                    "item_name": "青铜古镜",
                    "local_index": 14,
                    "cells": 4,
                    "shape_key": "22",
                    "row": 2,
                    "col": 5,
                    "width": 2,
                    "height": 2,
                    "source": "packet",
                    "layout_source": "live_grid",
                },
            ],
            "public_info_rows": [
                {
                    "info_id": 200001,
                    "tool": "",
                    "revealed_items_detail": [
                        {"local_index": 45, "runtime_id": 501, "quality": 4},
                        {"local_index": 65, "runtime_id": 502, "quality": 4},
                    ],
                }
            ],
            "skill_reveal_rows": [
                {
                    "skill_id": 10010801,
                    "hero_id": 108,
                    "tool": "玛丽亚·品质",
                    "revealed_items_detail": [
                        {"local_index": 36, "runtime_id": 601, "quality": 3},
                        {"local_index": 90, "runtime_id": 602, "quality": 1},
                    ],
                }
            ],
        }
    )

    minimap = contract["minimap"]
    markers = [item for item in minimap["items"] if item["render_mode"] == "marker"]
    footprints = [item for item in minimap["items"] if item["render_mode"] == "footprint"]

    assert len(footprints) == 1
    assert {marker["local_index"] for marker in markers} == {36, 45, 65, 90}
    assert minimap["quality_reveal_marker_count"] == 4


def test_ui_contract_minimap_shows_treasure_value_action_without_quality() -> None:
    contract = ui_contract_from_artifact(
        {
            "action_result_rows": [
                {
                    "action_id": 100163,
                    "tool": "至宝估价",
                    "revealed_items_detail": [
                        {
                            "local_index": 30,
                            "runtime_id": 1425860544908193,
                            "value": 43650,
                        }
                    ],
                }
            ],
        }
    )

    treasure_items = [
        item
        for item in contract["minimap"]["items"]
        if item.get("local_index") == 30 or item.get("value") == 43650
    ]
    assert len(treasure_items) == 1
    item = treasure_items[0]
    assert item["render_mode"] == "footprint"
    assert item["value"] == 43650
    assert item["display_label"] == "43,650"
    assert "43,650" in item["tooltip"]
    assert item["shape_key"] == "11"


def test_ui_contract_treasure_value_merges_onto_existing_runtime_footprint() -> None:
    contract = ui_contract_from_artifact(
        {
            "action_result_rows": [
                {
                    "action_id": 100163,
                    "tool": "至宝估价",
                    "revealed_items_detail": [
                        {
                            "local_index": 8,
                            "runtime_id": 1425860547015628,
                            "value": 29700,
                        }
                    ],
                }
            ],
            "minimap_grid_items": [
                {
                    "row": 1,
                    "col": 8,
                    "width": 2,
                    "height": 2,
                    "quality": 5,
                    "runtime_id": 1425860547015628,
                    "item_id": 1105005,
                    "item_name": "浪漫主义风景素描原作",
                    "local_index": 7,
                    "cells": 4,
                    "shape_key": "22",
                    "source": "packet",
                    "layout_source": "live_grid",
                }
            ],
        }
    )

    matched = [
        item
        for item in contract["minimap"]["items"]
        if item.get("runtime_id") == 1425860547015628
    ]
    assert len(matched) == 1
    item = matched[0]
    assert item["value"] == 29700
    assert item["display_label"] == "29,700"
    assert item["render_mode"] == "footprint"
    assert item["local_index"] == 7
    assert item["width"] == 2
