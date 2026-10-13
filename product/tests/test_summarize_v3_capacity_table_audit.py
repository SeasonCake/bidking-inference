import importlib.util
from pathlib import Path
from types import SimpleNamespace

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_capacity_table_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_capacity_table_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _item(item_id: int = 1001) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=4,
        quality_color="",
        value=10_000,
        shape_w=2,
        shape_h=2,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _tables(
    *,
    items_per_session_max: int = 44,
    raw_row: list[str] | None = None,
) -> SimpleNamespace:
    item = _item()
    return SimpleNamespace(
        maps={
            2601: BidMap(
                map_id=2601,
                name="direct_capacity_map",
                description="",
                category=101,
                auction_mode="open",
                sub_pool_weights=[],
                rounds_total=5,
                entry_fee_silver=0,
                starting_budget_silver=100_000,
                drop_pool_id=9001,
                items_per_session_min=22,
                items_per_session_max=items_per_session_max,
                value_tier_ui="",
                mode_flag=4,
                bid_price_ladder=[],
                round_category_hints=[],
                raw_row=raw_row or [],
            )
        },
        drops={
            9001: DropPool(
                pool_id=9001,
                name="pool",
                description="",
                pool_type=2,
                entries=[
                    DropEntry(
                        category=101,
                        item_id=item.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    )
                ],
            )
        },
        items={item.item_id: item},
    )


def test_capacity_table_audit_marks_truth_above_sampler_possible_max() -> None:
    module = _load_module()
    details = [
        {
            "file": "sample.json#prebid_r1",
            "map_id": 2601,
            "hero_map_evidence_profile": "aisha|2601|shape+layout",
            "item_count_capacity": {
                "total_count_source": "exact",
                "total_count_target": 65,
                "truth_item_count": 65,
                "prior_items_per_session_min": 22,
                "prior_items_per_session_max": 44,
                "target_prior_max_delta": 21,
                "truth_prior_max_delta": 21,
                "target_truth_delta": 0,
                "cases": ["direct_prior_max_conflict"],
                "flags": [
                    "target_count_above_prior_max",
                    "truth_count_above_prior_max",
                ],
            },
            "consistency_bucket": "hard_capacity_conflict",
            "consistency_classes": (
                "capacity_direct_prior_max_conflict",
                "total_cells_floor_below_truth",
            ),
        }
    ]

    result = module.summarize_capacity_table_audit(
        details,
        tables=_tables(),
        selected_case="direct_prior_max_conflict",
    )

    assert len(result) == 1
    row = result[0]
    assert row["map_id"] == "2601"
    assert row["status"] == "table_possible_max_below_truth"
    assert row["table_impossible_rows"] == 1
    assert row["round_cap_impossible_rows"] == 0
    assert row["bidmap_raw_column_count"] == 0
    assert row["bidmap_drop_ref_column_index"] == 16
    assert row["bidmap_raw_col8"] is None
    assert row["bidmap_v300_flag_a"] is None
    assert row["bidmap_raw_round_cap_max"] is None
    assert row["bidmap_items_per_session_max"] == 44
    assert row["sampler_max_count_per_draw"] == 1
    assert row["sampler_possible_item_count_max"] == 44
    assert row["capacity_cases"] == {"direct_prior_max_conflict": 1}
    assert row["consistency_bucket_counts"] == {"hard_capacity_conflict": 1}
    assert row["consistency_class_counts"] == {
        "capacity_direct_prior_max_conflict": 1,
        "total_cells_floor_below_truth": 1,
    }
    assert row["target_truth_delta_counts"] == {
        "below": 0,
        "match": 1,
        "above": 0,
    }
    split = row["source_split_summary"]
    assert split["rows"] == 1
    assert split["map_prefix3_counts"] == {"260": 1}
    assert split["map_family_counts"] == {"hidden": 1}
    assert split["capture_day_counts"] == {"none": 1}
    assert split["total_count_source_counts"] == {"exact": 1}
    assert split["target_truth_delta_counts"] == {
        "below": 0,
        "match": 1,
        "above": 0,
    }
    assert split["inventory_status_counts"] == {}


def test_capacity_table_audit_adds_raw_inventory_diagnostics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "fatbeans_20260606_sample.json"
    sample.write_text("[]", encoding="utf-8")

    inventory_items = (
        SimpleNamespace(runtime_id=101, item_id=1001, quality=4, cells=4),
        SimpleNamespace(runtime_id=102, item_id=1001, quality=4, cells=4),
    )
    action = SimpleNamespace(action_id=100100, observed_items=inventory_items)
    state = SimpleNamespace(
        sort_id=10,
        message_id=0x002D,
        round_index=2,
        map_id=2601,
        inventory_items=inventory_items,
        action_results=(action,),
        public_infos=(SimpleNamespace(info_id=200017, value=2),),
    )

    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture",
        lambda path: SimpleNamespace(states=(state,)),
    )
    monkeypatch.setattr(
        module,
        "_latest_settlement_payload",
        lambda path: (b"payload", None, {}),
    )
    monkeypatch.setattr(
        module,
        "_parse_fields",
        lambda payload: [(4, 2, b"inventory")],
    )
    monkeypatch.setattr(
        module,
        "_inventory_block_metrics",
        lambda block: {
            "inventory_slot_count": 300,
            "occupied_slot_count": 2,
            "raw_item_candidate_count": 2,
            "raw_duplicate_runtime_item_pair_count": 0,
            "slot_field_shapes": {},
            "item_field_signatures": {},
        },
    )
    monkeypatch.setattr(
        module,
        "settlement_truth_from_fatbeans",
        lambda events, *, items: SimpleNamespace(item_count=2),
    )

    result = module.summarize_capacity_table_audit(
        [
            {
                "file": f"{sample}#prebid_r1",
                "map_id": 2601,
                "hero_map_evidence_profile": "aisha|2601|shape+layout",
                "item_count_capacity": {
                    "total_count_source": "exact",
                    "total_count_target": 2,
                    "truth_item_count": 2,
                    "prior_items_per_session_max": 1,
                    "truth_prior_max_delta": 1,
                    "target_truth_delta": 0,
                    "cases": ["direct_prior_max_conflict"],
                },
                "consistency_bucket": "hard_capacity_conflict",
                "consistency_classes": ("capacity_direct_prior_max_conflict",),
            }
        ],
        tables=_tables(items_per_session_max=1),
        selected_case="direct_prior_max_conflict",
        detail_rows=2,
    )

    row = result[0]
    assert row["raw_inventory_status"] == "verified_latest_inventory"
    assert row["raw_inventory_file_count"] == 1
    assert row["raw_latest_inventory_item_count"]["max"] == 2
    assert row["raw_inventory_slot_count"]["max"] == 300
    assert row["raw_inventory_slot_headroom"]["max"] == 298
    assert row["raw_candidate_inventory_delta"]["max"] == 0
    assert row["raw_occupied_slot_inventory_delta"]["max"] == 0
    assert row["raw_full_observed_actions"] == {"100100": 1}
    assert row["raw_public_total_count_values"] == {"2": 1}
    assert row["raw_latest_inventory_truth_match_files"] == 1
    assert row["raw_detail_truth_latest_match_rows"] == 1
    assert row["raw_duplicate_runtime_id_count"]["max"] == 0
    assert row["raw_duplicate_runtime_item_pair_count"]["max"] == 0
    assert row["raw_duplicate_item_id_count"]["max"] == 1
    assert row["raw_missing_from_drop_universe_count"]["max"] == 0
    assert row["raw_known_temp_zodiac_count"]["max"] == 0
    assert row["raw_non_zodiac_missing_from_drop_universe_count"]["max"] == 0
    assert row["raw_drop_ref_excess_item_count"]["max"] == 1
    assert row["raw_drop_ref_excess_after_temp_zodiac_count"]["max"] == 1
    assert row["raw_round_cap_excess_item_count"]["max"] is None
    assert row["raw_round_cap_excess_after_temp_zodiac_count"]["max"] is None
    assert row["raw_latest_inventory_message_ids"] == {"0x002D": 1}
    assert row["raw_latest_inventory_quality_counts"] == {"4": 2}
    split = row["source_split_summary"]
    assert split["capture_day_counts"] == {"20260606": 1}
    assert split["inventory_status_counts"] == {"ok": 1}
    assert split["latest_message_id_counts"] == {"0x002D": 1}
    assert split["drop_ref_excess_after_temp_positive_files"] == 1
    assert split["round_cap_excess_after_temp_positive_files"] == 0
    assert split["non_zodiac_missing_positive_files"] == 0
    assert split["full_observed_action_counts"] == {"100100": 1}
    assert split["public_total_count_values"] == {"2": 1}
    residual = row["residual_mode_summary"]
    assert residual["mode_counts"] == {"drop_ref_only_overflow": 1}
    assert residual["by_mode"][0]["mode"] == "drop_ref_only_overflow"
    assert (
        residual["by_mode"][0]["drop_ref_excess_after_temp_zodiac_count"]["max"]
        == 1
    )
    semantic = row["capacity_semantic_summary"]
    assert semantic["status"] == "blocked_drop_ref_overflow_after_temp"
    assert semantic["blockers"] == ["drop_ref_max_below_verified_inventory"]
    assert "current_v300_drop_ref_col17" not in semantic["findings"]
    matrix = row["capacity_semantic_matrix"]
    assert len(matrix) == 1
    cell = matrix[0]
    assert cell["consistency_bucket"] == "hard_capacity_conflict"
    assert cell["residual_mode"] == "drop_ref_only_overflow"
    assert cell["map_family"] == "hidden"
    assert cell["total_count_source"] == "exact"
    assert cell["full_action_signal"] == "has_full_action"
    assert cell["public_total_signal"] == "has_public_total"
    assert cell["capture_day"] == "20260606"
    assert cell["rows"] == 1
    assert cell["files"] == 1
    assert cell["map_ids"] == {"2601": 1}
    assert cell["capacity_cases"] == {"direct_prior_max_conflict": 1}
    assert cell["semantic_status_counts"] == {
        "blocked_drop_ref_overflow_after_temp": 1
    }
    assert cell["latest_item_count"]["max"] == 2
    assert cell["drop_ref_excess_after_temp_zodiac_count"]["max"] == 1
    raw_detail = row["raw_detail_rows"][0]
    assert raw_detail["file"] == sample.name
    assert raw_detail["file_ref"] == str(sample)
    assert raw_detail["map_id"] == 2601
    assert raw_detail["latest_message_id"] == "0x002D"
    assert raw_detail["inventory_status"] == "ok"
    assert raw_detail["residual_mode"] == "drop_ref_only_overflow"
    assert raw_detail["semantic_status"] == "blocked_drop_ref_overflow_after_temp"
    assert raw_detail["drop_ref_excess_after_temp_zodiac_count"] == 1
    assert raw_detail["round_cap_excess_after_temp_zodiac_count"] is None
    detail_rows = module._collect_capacity_detail_rows(result, limit=1)
    assert detail_rows == [
        {
            "map_id": "2601",
            "map_status": "table_possible_max_below_truth",
            "map_semantic_status": "blocked_drop_ref_overflow_after_temp",
            **raw_detail,
        }
    ]
    assert "2601:" in module._format_capacity_detail_rows(detail_rows)
    assert "/residual=drop_ref_only_overflow" in module._format_capacity_detail_rows(
        detail_rows
    )
    detail_summary = module._capacity_detail_summary(detail_rows, top=8)
    assert detail_summary["rows"] == 1
    assert detail_summary["unique_file_map_residual_rows"] == 1
    assert detail_summary["unique_files"] == 1
    assert detail_summary["semantic_status_counts"] == {
        "blocked_drop_ref_overflow_after_temp": 1
    }
    assert detail_summary["residual_mode_counts"] == {"drop_ref_only_overflow": 1}
    assert detail_summary["mechanism_candidate_counts"] == {
        "drop_ref_candidate_gap": 1
    }
    assert detail_summary["next_check_counts"] == {
        "check_drop_ref_source_semantics_or_activity_overlay": 1
    }
    assert detail_summary["source_signal_counts"] == {
        "has_full_action/has_public_total": 1
    }
    assert (
        "next_checks=check_drop_ref_source_semantics_or_activity_overlay:1"
        in module._format_capacity_detail_summary(detail_summary)
    )
    merged = module._merge_capacity_semantic_matrix(result, top=8)
    assert merged[0]["rows"] == 1
    assert merged[0]["semantic_status_counts"] == {
        "blocked_drop_ref_overflow_after_temp": 1
    }


def test_capacity_table_audit_filters_selected_case() -> None:
    module = _load_module()
    details = [
        {
            "file": "sample.json#prebid_r1",
            "map_id": 2601,
            "hero_map_evidence_profile": "aisha|2601|shape+layout",
            "item_count_capacity": {
                "total_count_source": "floor",
                "total_count_target": 40,
                "truth_item_count": 65,
                "prior_items_per_session_max": 44,
                "target_prior_max_delta": -4,
                "truth_prior_max_delta": 21,
                "target_truth_delta": -25,
                "cases": ["target_lower_bound_truth_above_prior"],
            },
            "consistency_bucket": "lower_bound_under_truth",
            "consistency_classes": ("capacity_truth_above_prior_not_targeted",),
        }
    ]

    assert (
        module.summarize_capacity_table_audit(
            details,
            tables=_tables(),
            selected_case="direct_prior_max_conflict",
        )
        == []
    )
    assert len(
        module.summarize_capacity_table_audit(
            details,
            tables=_tables(),
            selected_case="target_lower_bound_truth_above_prior",
        )
    ) == 1
    assert (
        module.summarize_capacity_table_audit(
            details,
            tables=_tables(),
            selected_case="all",
            selected_bucket="hard_capacity_conflict",
        )
        == []
    )
    assert len(
        module.summarize_capacity_table_audit(
            details,
            tables=_tables(),
            selected_case="all",
            selected_bucket="lower_bound_under_truth",
        )
    ) == 1


def test_capacity_detail_summary_deduplicates_file_level_rows() -> None:
    module = _load_module()
    rows = [
        {
            "file": "a.json",
            "file_ref": "a.json",
            "map_id": "2601",
            "map_family": "hidden",
            "residual_mode": "round_cap_overflow",
            "semantic_status": "blocked_round_cap_overflow_after_temp",
            "truth_prior_max_delta": 21,
            "drop_ref_excess_after_temp_zodiac_count": 20,
            "round_cap_excess_after_temp_zodiac_count": 4,
            "full_observed_action_ids": [100100],
            "public_total_count_values": [],
        },
        {
            "file": "a.json",
            "file_ref": "a.json",
            "map_id": "2601",
            "map_family": "hidden",
            "residual_mode": "round_cap_overflow",
            "semantic_status": "blocked_round_cap_overflow_after_temp",
            "truth_prior_max_delta": 21,
            "drop_ref_excess_after_temp_zodiac_count": 20,
            "round_cap_excess_after_temp_zodiac_count": 4,
            "full_observed_action_ids": [100100],
            "public_total_count_values": [],
        },
        {
            "file": "b.json",
            "file_ref": "b.json",
            "map_id": "2401",
            "map_family": "villa",
            "residual_mode": "within_drop_ref",
            "semantic_status": "watch_activity_extras_explain_drop_ref_gap",
            "truth_prior_max_delta": 2,
            "drop_ref_excess_after_temp_zodiac_count": 0,
            "round_cap_excess_after_temp_zodiac_count": 0,
            "full_observed_action_ids": [],
            "public_total_count_values": [42],
        },
    ]

    summary = module._capacity_detail_summary(rows, top=8)

    assert summary["rows"] == 3
    assert summary["unique_file_map_residual_rows"] == 2
    assert summary["mechanism_candidate_counts"] == {
        "activity_extras_candidate": 1,
        "round_cap_candidate_gap": 1,
    }
    assert summary["next_check_counts"] == {
        "check_per_session_table_version_or_external_overlay": 1,
        "verify_activity_extras_table": 1,
    }
    assert summary["source_signal_counts"] == {
        "has_full_action/no_public_total": 1,
        "no_full_action/has_public_total": 1,
    }


def test_capacity_table_audit_classifies_residual_modes() -> None:
    module = _load_module()

    summary = module._residual_mode_summary(
        [
            {
                "status": "ok",
                "latest_item_count": 44,
                "drop_ref_excess_after_temp_zodiac_count": 0,
                "round_cap_excess_after_temp_zodiac_count": 0,
                "non_zodiac_missing_from_drop_universe_count": 0,
            },
            {
                "status": "ok",
                "latest_item_count": 50,
                "drop_ref_excess_after_temp_zodiac_count": 6,
                "round_cap_excess_after_temp_zodiac_count": 0,
                "non_zodiac_missing_from_drop_universe_count": 0,
                "full_observed_action_ids": [100100],
            },
            {
                "status": "ok",
                "latest_item_count": 60,
                "drop_ref_excess_after_temp_zodiac_count": 16,
                "round_cap_excess_after_temp_zodiac_count": 8,
                "non_zodiac_missing_from_drop_universe_count": 0,
                "public_total_count_values": [60],
            },
            {
                "status": "ok",
                "latest_item_count": 55,
                "drop_ref_excess_after_temp_zodiac_count": 11,
                "round_cap_excess_after_temp_zodiac_count": 0,
                "non_zodiac_missing_from_drop_universe_count": 1,
            },
        ],
        top=8,
    )

    assert summary["mode_counts"] == {
        "drop_ref_only_overflow": 1,
        "drop_universe_gap": 1,
        "round_cap_overflow": 1,
        "within_drop_ref": 1,
    }
    by_mode = {row["mode"]: row for row in summary["by_mode"]}
    assert by_mode["drop_ref_only_overflow"]["full_observed_action_counts"] == {
        "100100": 1,
    }
    assert by_mode["round_cap_overflow"]["public_total_count_values"] == {"60": 1}
    assert (
        by_mode["drop_universe_gap"]["non_zodiac_missing_from_drop_universe_count"][
            "max"
        ]
        == 1
    )


def test_capacity_table_audit_quantifies_zodiac_adjusted_count_gap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "sample.json"
    sample.write_text("[]", encoding="utf-8")

    inventory_items = (
        SimpleNamespace(runtime_id=101, item_id=1001, quality=4, cells=4),
        SimpleNamespace(runtime_id=102, item_id=1306006, quality=3, cells=4),
        SimpleNamespace(runtime_id=103, item_id=1001, quality=4, cells=4),
    )
    state = SimpleNamespace(
        sort_id=10,
        message_id=0x002D,
        round_index=2,
        map_id=2601,
        inventory_items=inventory_items,
    )

    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture",
        lambda path: SimpleNamespace(states=(state,)),
    )
    monkeypatch.setattr(
        module,
        "settlement_truth_from_fatbeans",
        lambda events, *, items: SimpleNamespace(item_count=3),
    )

    raw_row = ["0"] * 23
    raw_row[8] = "1"
    raw_row[14] = "[2,2,2,2,2]"
    raw_row[16] = "[[]]"
    raw_row[17] = "[9999,2601,1,1]"
    result = module.summarize_capacity_table_audit(
        [
            {
                "file": f"{sample}#prebid_r1",
                "map_id": 2601,
                "hero_map_evidence_profile": "aisha|2601|shape+layout",
                "item_count_capacity": {
                    "total_count_source": "exact",
                    "total_count_target": 3,
                    "truth_item_count": 3,
                    "prior_items_per_session_max": 1,
                    "truth_prior_max_delta": 2,
                    "target_truth_delta": 0,
                    "cases": ["direct_prior_max_conflict"],
                },
                "consistency_bucket": "hard_capacity_conflict",
                "consistency_classes": ("capacity_direct_prior_max_conflict",),
            }
        ],
        tables=_tables(items_per_session_max=1, raw_row=raw_row),
        selected_case="direct_prior_max_conflict",
    )

    row = result[0]
    assert row["bidmap_raw_col8"] == "1"
    assert row["bidmap_v300_flag_a"] == 1
    assert row["raw_known_temp_zodiac_count"]["max"] == 1
    assert row["raw_non_zodiac_missing_from_drop_universe_count"]["max"] == 0
    assert row["raw_drop_ref_excess_item_count"]["max"] == 2
    assert row["raw_drop_ref_excess_after_temp_zodiac_count"]["max"] == 1
    assert row["raw_round_cap_excess_item_count"]["max"] == 1
    assert row["raw_round_cap_excess_after_temp_zodiac_count"]["max"] == 0
    split = row["source_split_summary"]
    assert split["drop_ref_excess_after_temp_zodiac_count"]["max"] == 1
    assert split["drop_ref_excess_after_temp_positive_files"] == 1
    assert split["round_cap_excess_after_temp_zodiac_count"]["max"] == 0
    assert split["round_cap_excess_after_temp_positive_files"] == 0
    assert split["non_zodiac_missing_from_drop_universe_count"]["max"] == 0
    semantic = row["capacity_semantic_summary"]
    assert semantic["status"] == "blocked_drop_ref_overflow_after_temp"
    assert "current_v300_drop_ref_col17" in semantic["findings"]
    assert "current_v300_col16_unused" in semantic["findings"]
    assert "drop_entry_nmax_not_multi_count_driver" in semantic["findings"]


def test_capacity_table_audit_marks_activity_extras_when_zodiac_covers_gap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "sample.json"
    sample.write_text("[]", encoding="utf-8")

    inventory_items = (
        SimpleNamespace(runtime_id=101, item_id=1001, quality=4, cells=4),
        SimpleNamespace(runtime_id=102, item_id=1306006, quality=3, cells=4),
        SimpleNamespace(runtime_id=103, item_id=1001, quality=4, cells=4),
    )
    state = SimpleNamespace(
        sort_id=10,
        message_id=0x002D,
        round_index=2,
        map_id=2601,
        inventory_items=inventory_items,
    )

    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture",
        lambda path: SimpleNamespace(states=(state,)),
    )
    monkeypatch.setattr(
        module,
        "settlement_truth_from_fatbeans",
        lambda events, *, items: SimpleNamespace(item_count=3),
    )

    raw_row = ["0"] * 23
    raw_row[8] = "1"
    raw_row[14] = "[3,3,3,3,3]"
    raw_row[16] = "[[]]"
    raw_row[17] = "[9999,2601,1,2]"
    result = module.summarize_capacity_table_audit(
        [
            {
                "file": f"{sample}#prebid_r1",
                "map_id": 2601,
                "hero_map_evidence_profile": "aisha|2601|shape+layout",
                "item_count_capacity": {
                    "total_count_source": "exact",
                    "total_count_target": 3,
                    "truth_item_count": 3,
                    "prior_items_per_session_max": 2,
                    "truth_prior_max_delta": 1,
                    "target_truth_delta": 0,
                    "cases": ["direct_prior_max_conflict"],
                },
                "consistency_bucket": "hard_capacity_conflict",
                "consistency_classes": ("capacity_direct_prior_max_conflict",),
            }
        ],
        tables=_tables(items_per_session_max=2, raw_row=raw_row),
        selected_case="direct_prior_max_conflict",
    )

    semantic = result[0]["capacity_semantic_summary"]
    assert semantic["status"] == "watch_activity_extras_explain_drop_ref_gap"
    assert semantic["blockers"] == []
    assert "temporary_zodiac_explains_drop_ref_gap" in semantic["findings"]
    matrix = result[0]["capacity_semantic_matrix"]
    assert matrix[0]["residual_mode"] == "within_drop_ref"
    assert matrix[0]["semantic_status_counts"] == {
        "watch_activity_extras_explain_drop_ref_gap": 1
    }
