import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_session_capacity_payload_table_gap_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_session_capacity_payload_table_gap_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_2410_payload_verified_table_cap_gap_without_full_source() -> None:
    module = _load_module()
    root = Path(__file__).resolve().parents[1]
    session_gap = {
        "rows": [
            {
                "file": (
                    "fatbeans_valid_ethan_2410_1rounds_2410_"
                    "1295019008815241_0283.json"
                ),
                "map_id": 2410,
                "map_family": "villa",
                "status": "blocked_session_capacity_source_gap_bucket_only",
                "unique_residual_mode": "unique_round_cap_overflow_after_temp",
                "mechanism_class": "session_capacity_source_semantics",
                "source_context_class": "payload_verified_empty_action_results",
                "source_evidence_class": "settlement_payload_verified_only",
                "inventory_count": 57,
                "unique_non_temp_item_id_count": 53,
                "bidmap_items_per_session_max": 40,
                "bidmap_raw_round_cap_max": 50,
            }
        ]
    }

    result = module.summarize_session_capacity_payload_table_gap(
        session_capacity_source_gap=session_gap,
        sample_root=root / "data" / "samples" / "fatbeans",
        focus_maps=["2410"],
    )

    assert result["status"] == "blocked_payload_table_gap_required"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["summary"]["blocked_rows"] == 1
    assert result["summary"]["payload_verified_rows"] == 1
    assert result["summary"]["no_full_event_payload_rows"] == 1
    row = result["rows"][0]
    assert row["status"] == "blocked_payload_verified_table_cap_gap_without_full_source"
    assert row["table_delta"]["inventory_minus_bidmap_items_per_session"] == 17
    assert row["table_delta"]["unique_non_temp_minus_bidmap_raw_round_cap"] == 3
    assert row["payload"]["inventory_slot_count"] == 250
    assert row["payload"]["occupied_slot_count"] == 57
    assert row["payload"]["raw_item_candidate_count"] == 57
    assert row["payload"]["raw_candidate_inventory_delta"] == 0
    assert row["payload"]["occupied_slot_inventory_delta"] == 0
    assert row["payload"]["payload_field20_present"] is True
    assert row["event_payload"]["full_action_payload_count"] == 0
    assert row["event_payload"]["full_skill_payload_count"] == 0
    assert row["event_payload"]["skill_observed_item_count_max"] == 35
    assert row["event_payload"]["skill_id_counts"] == {"1002081": 2}
    assert set(row["next_checks"]) == {
        "check_per_session_table_version_or_external_overlay",
        "inspect_server_side_settlement_expansion_or_source_transform",
        "decode_payload_outer_fields_as_metadata_not_capacity",
    }


def test_ignores_non_blocked_session_gap_rows() -> None:
    module = _load_module()
    root = Path(__file__).resolve().parents[1]
    session_gap = {
        "rows": [
            {
                "file": (
                    "fatbeans_valid_aisha_2410_2rounds_2410_"
                    "1295018574148404_0093.json"
                ),
                "map_id": 2410,
                "status": "watch_exact_session_count_source_observed",
                "inventory_count": 48,
                "unique_non_temp_item_id_count": 41,
                "bidmap_items_per_session_max": 40,
                "bidmap_raw_round_cap_max": 50,
            }
        ]
    }

    result = module.summarize_session_capacity_payload_table_gap(
        session_capacity_source_gap=session_gap,
        sample_root=root / "data" / "samples" / "fatbeans",
        focus_maps=["2410"],
    )

    assert result["status"] == "watch_payload_table_gap_audit_only"
    assert result["summary"]["rows"] == 0
    assert result["rows"] == []
