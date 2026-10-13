import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_payload_outer_field_semantics_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_payload_outer_field_semantics_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_2410_outer_fields_are_metadata_not_capacity_targets() -> None:
    module = _load_module()
    root = Path(__file__).resolve().parents[1]
    payload_gap = {
        "rows": [
            {
                "file": (
                    "fatbeans_valid_ethan_2410_1rounds_2410_"
                    "1295019008815241_0283.json"
                ),
                "map_id": 2410,
                "status": "blocked_payload_verified_table_cap_gap_without_full_source",
                "table_delta": {
                    "inventory_count": 57,
                    "unique_non_temp_item_id_count": 53,
                    "bidmap_items_per_session_max": 40,
                    "bidmap_raw_round_cap_max": 50,
                },
                "payload": {
                    "inventory_slot_count": 250,
                    "occupied_slot_count": 57,
                    "raw_item_candidate_count": 57,
                },
            }
        ]
    }

    result = module.summarize_payload_outer_field_semantics(
        payload_table_gap=payload_gap,
        sample_root=root / "data" / "samples" / "fatbeans",
        focus_maps=["2410"],
    )

    assert result["status"] == "watch_payload_outer_fields_metadata_only"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["summary"]["metadata_only_rows"] == 1
    assert result["summary"]["capacity_candidate_rows"] == 0
    assert result["summary"]["target_match_count"] == 0
    row = result["rows"][0]
    assert row["status"] == "watch_outer_fields_metadata_only"
    assert row["metadata_matches"] == {
        "payload_field2_matches_map_id": True,
        "payload_field20_matches_capture_time": True,
        "wrapper_field5_equals_loss_units": True,
        "wrapper_field3_equals_field4": True,
    }
    assert row["payload_field20_epoch_delta_seconds"] == 0
    assert row["settlement_loss_units"] == 4107
    assert row["target_matches"] == []
    assert row["next_checks"] == [
        "check_per_session_table_version_or_external_overlay",
        "inspect_server_side_settlement_expansion_or_source_transform",
    ]


def test_timestamp_parser_accepts_seven_fraction_digits() -> None:
    module = _load_module()

    assert (
        module._epoch_delta_seconds(
            1780586901,
            "2026-06-04T23:28:21.2537776+08:00",
        )
        == 0
    )
