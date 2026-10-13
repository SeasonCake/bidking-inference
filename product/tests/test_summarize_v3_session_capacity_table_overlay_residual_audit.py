import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_session_capacity_table_overlay_residual_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_session_capacity_table_overlay_residual_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_2410_current_raw_table_leaves_overlay_or_server_residual() -> None:
    module = _load_module()
    root = Path(__file__).resolve().parents[1]
    payload_table_gap = {
        "rows": [
            {
                "file": (
                    "fatbeans_valid_ethan_2410_1rounds_2410_"
                    "1295019008815241_0283.json"
                ),
                "map_id": 2410,
                "map_family": "villa",
                "status": (
                    "blocked_payload_verified_table_cap_gap_without_full_source"
                ),
                "table_delta": {
                    "inventory_count": 57,
                    "unique_non_temp_item_id_count": 53,
                    "bidmap_items_per_session_max": 40,
                    "bidmap_raw_round_cap_max": 50,
                    "inventory_minus_bidmap_items_per_session": 17,
                    "unique_non_temp_minus_bidmap_raw_round_cap": 3,
                },
                "payload": {
                    "status": "ok",
                    "raw_candidate_inventory_delta": 0,
                    "occupied_slot_inventory_delta": 0,
                },
                "event_payload": {
                    "full_action_payload_count": 0,
                    "full_skill_payload_count": 0,
                    "full_public_payload_count": 0,
                },
            }
        ]
    }
    payload_outer_fields = {
        "rows": [
            {
                "file": (
                    "fatbeans_valid_ethan_2410_1rounds_2410_"
                    "1295019008815241_0283.json"
                ),
                "map_id": 2410,
                "status": "watch_outer_fields_metadata_only",
                "target_matches": [],
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
                "server_side_expansion_rows": 0,
                "session_capacity_source_semantics_rows": 1,
            }
        ]
    }

    result = module.summarize_session_capacity_table_overlay_residual(
        payload_table_gap=payload_table_gap,
        payload_outer_fields=payload_outer_fields,
        cse=cse,
        sample_root=root / "data" / "samples" / "fatbeans",
        raw_root=root / "data" / "raw",
        focus_maps=["2410"],
    )

    assert result["status"] == (
        "blocked_table_overlay_or_server_side_residual_required"
    )
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["summary"]["blocked_rows"] == 1
    assert result["summary"]["local_table_cap_gap_rows"] == 1
    assert result["summary"]["current_table_cap_matches_payload_delta_rows"] == 1
    assert result["summary"]["capture_version_or_hash_rows"] == 0
    assert result["summary"]["drop_multiplicity_candidate_rows"] == 0
    assert result["summary"]["activity_overlay_direct_candidate_rows"] == 0
    assert result["summary"]["outer_fields_metadata_only_rows"] == 1
    assert result["summary"]["server_transform_open_rows"] == 1
    row = result["rows"][0]
    assert row["status"] == "blocked_table_overlay_or_server_side_residual"
    assert row["mechanism_class"] == (
        "table_version_or_external_overlay_or_server_side_transform_required"
    )
    assert row["current_table_cap_matches_payload_delta"] is True
    assert row["payload_verified"] is True
    assert row["no_full_event_source"] is True
    assert row["local_table_cap_gap"] is True
    assert row["capture_has_table_version_or_hash"] is False
    assert row["drop_multiplicity_candidate"] is False
    assert row["activity_overlay_direct_candidate"] is False
    assert row["outer_fields_metadata_only"] is True
    hypotheses = set(row["remaining_minimal_hypotheses"])
    assert {
        "per_session_or_historical_table_version",
        "external_overlay_table_not_in_current_raw_tables",
        "server_side_settlement_expansion_or_source_transform",
    }.issubset(hypotheses)
    assert "current_drop_leaf_nmax_not_count_expansion" in row[
        "disproven_or_weak_paths"
    ]
    table_context = row["current_table_context"]
    bidmap = table_context["bidmap"]
    drop = table_context["drop"]
    activity = table_context["activity_overlay"]
    assert table_context["raw_tables_file_version"] == "308"
    assert bidmap["items_per_session_max"] == 40
    assert bidmap["round_cap_max"] == 50
    assert bidmap["raw_col16"] == "[[]]"
    assert bidmap["raw_col17"] == "[9999,2410,20,40]"
    assert drop["leaf_n_max_max"] == 1
    assert activity["map_in_activity_range"] is False


def test_ignores_non_blocked_payload_gap_rows() -> None:
    module = _load_module()
    result = module.summarize_session_capacity_table_overlay_residual(
        payload_table_gap={
            "rows": [
                {
                    "file": "x.json",
                    "map_id": 2410,
                    "status": "watch_full_event_payload_source_observed",
                }
            ]
        },
        payload_outer_fields={},
        cse={},
    )

    assert result["status"] == "watch_table_overlay_residual_audit_only"
    assert result["summary"]["rows"] == 0
    assert result["rows"] == []
