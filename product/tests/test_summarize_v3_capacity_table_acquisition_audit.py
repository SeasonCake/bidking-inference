import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_capacity_table_acquisition_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_capacity_table_acquisition_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_capacity_table_acquisition_audit_routes_unique_detail_rows() -> None:
    module = _load_module()
    capacity_audit = {
        "case": "direct_prior_max_conflict",
        "bucket": "all",
        "rows": [
            {
                "map_id": "2601",
                "map_name": "hidden",
                "status": "table_possible_max_below_truth",
                "bidmap_items_per_session_min": 22,
                "bidmap_items_per_session_max": 44,
                "bidmap_raw_column_count": 23,
                "bidmap_v300_flag_a": 1,
                "bidmap_raw_col14": "[60,60,60,60,60]",
                "bidmap_raw_col16": "[[]]",
                "bidmap_raw_col17": "[9999,2601,22,44]",
                "bidmap_raw_round_cap_max": 60,
                "sampler_possible_item_count_max": 44,
            },
            {
                "map_id": "2401",
                "map_name": "villa",
                "status": "table_possible_max_below_truth",
                "bidmap_v300_flag_a": 1,
                "bidmap_raw_col16": "[[]]",
            },
        ],
        "detail_rows": [
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
                "map_id": "2601",
                "map_family": "hidden",
                "residual_mode": "drop_ref_only_overflow",
                "semantic_status": "blocked_drop_ref_overflow_after_temp",
                "truth_prior_max_delta": 11,
                "drop_ref_excess_after_temp_zodiac_count": 8,
                "round_cap_excess_after_temp_zodiac_count": 0,
                "full_observed_action_ids": [],
                "public_total_count_values": [],
            },
            {
                "file": "c.json",
                "file_ref": "c.json",
                "map_id": "2401",
                "map_family": "villa",
                "residual_mode": "within_drop_ref",
                "semantic_status": "watch_activity_extras_explain_drop_ref_gap",
                "truth_prior_max_delta": 2,
                "drop_ref_excess_after_temp_zodiac_count": 0,
                "round_cap_excess_after_temp_zodiac_count": 0,
                "full_observed_action_ids": [100100],
                "public_total_count_values": [],
            },
        ],
    }
    cse_artifact = {
        "table_overlay_metadata": {
            "local_overlay_status": "v300_activity_listed_missing_locally",
            "activity_table_present": False,
        }
    }

    result = module.summarize_acquisition_audit(
        capacity_audit,
        cse_artifact=cse_artifact,
        current_table_overlay_metadata={
            "local_overlay_status": "v300_activity_listed_missing_locally",
            "activity_table_present": False,
        },
        top=8,
    )

    assert result["status"] == "blocked_acquisition_required"
    assert result["detail_rows"] == 4
    assert result["unique_detail_rows"] == 3
    assert result["acquisition_route_counts"] == {
        "missing_activity_table_overlay_required": 1,
        "payload_only_drop_ref_source_semantics_required": 1,
        "table_version_or_external_overlay_required": 1,
    }
    assert result["next_check_counts"] == {
        "check_drop_ref_source_semantics_or_activity_overlay": 1,
        "check_per_session_table_version_or_external_overlay": 1,
        "verify_activity_extras_table": 1,
    }
    assert result["source_strength_counts"] == {
        "full_action_confirmed": 2,
        "payload_only_or_unconfirmed": 1,
    }
    examples_by_map = {row["map_id"]: row for row in result["top_examples"]}
    assert examples_by_map["2601"]["table_context"]["bidmap_raw_col14"] == (
        "[60,60,60,60,60]"
    )


def test_capacity_table_acquisition_audit_uses_current_overlay_when_artifact_stale() -> None:
    module = _load_module()
    capacity_audit = {
        "case": "direct_prior_max_conflict",
        "bucket": "all",
        "rows": [
            {
                "map_id": "2401",
                "map_name": "villa",
                "status": "table_possible_max_below_truth",
                "bidmap_v300_flag_a": 1,
                "bidmap_raw_col16": "[[]]",
            },
        ],
        "detail_rows": [
            {
                "file": "c.json",
                "file_ref": "c.json",
                "map_id": "2401",
                "map_family": "villa",
                "residual_mode": "within_drop_ref",
                "semantic_status": "watch_activity_extras_explain_drop_ref_gap",
                "truth_prior_max_delta": 2,
                "drop_ref_excess_after_temp_zodiac_count": 0,
                "round_cap_excess_after_temp_zodiac_count": 0,
                "full_observed_action_ids": [100100],
                "public_total_count_values": [],
            },
        ],
    }
    cse_artifact = {
        "table_overlay_metadata": {
            "raw_file_version": "300",
            "raw_tables_file_version": "300",
            "raw_filelist_header": "Ver:300|FileCount:4299",
            "raw_tables_filelist_header": "Ver:300|FileCount:4299",
            "local_overlay_status": "v300_activity_listed_missing_locally",
            "activity_table_present": False,
            "activity_table_listed_in_filelist": True,
        }
    }
    current_overlay = {
        "raw_file_version": "303",
        "raw_tables_file_version": "303",
        "raw_filelist_header": "Ver:303|FileCount:4550",
        "raw_tables_filelist_header": "Ver:303|FileCount:4550",
        "local_overlay_status": "activity_table_available_locally",
        "activity_table_present": True,
        "activity_table_listed_in_filelist": True,
        "activity_table_parse_status": "ok",
        "activity_table_rows": 6,
        "activity_table_columns": 16,
    }

    result = module.summarize_acquisition_audit(
        capacity_audit,
        cse_artifact=cse_artifact,
        current_table_overlay_metadata=current_overlay,
        top=8,
    )

    assert result["status"] == "blocked_acquisition_required"
    assert result["acquisition_route_counts"] == {
        "activity_extras_table_verification_required": 1,
    }
    assert result["table_overlay_metadata"]["local_overlay_status"] == (
        "activity_table_available_locally"
    )
    assert result["artifact_table_overlay_metadata"]["local_overlay_status"] == (
        "v300_activity_listed_missing_locally"
    )
    assert result["table_overlay_metadata_stale"] is True
    delta_keys = {row["key"] for row in result["table_overlay_metadata_delta"]}
    assert {
        "raw_file_version",
        "raw_tables_file_version",
        "local_overlay_status",
        "activity_table_present",
    }.issubset(delta_keys)


def test_capacity_table_acquisition_audit_accepts_missing_details() -> None:
    module = _load_module()

    result = module.summarize_acquisition_audit(
        {"rows": [], "detail_rows": []},
        current_table_overlay_metadata={},
    )

    assert result["status"] == "blocked_acquisition_required"
    assert "detail rows are missing" in result["reason"]
    assert result["unique_detail_rows"] == 0
