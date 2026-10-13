import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_session_capacity_source_gap_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_session_capacity_source_gap_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_2410_unique_overflow_keeps_bucket_only_source_gap() -> None:
    module = _load_module()
    root = Path(__file__).resolve().parents[1]
    source_semantics = {
        "detail_rows": [
            {
                "file": (
                    "fatbeans_valid_ethan_2410_1rounds_2410_"
                    "1295019008815241_0283.json"
                ),
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
                "event_public_total_match": False,
                "event_action_result_count_all": 2,
            }
        ]
    }

    result = module.summarize_session_capacity_source_gap(
        source_semantics=source_semantics,
        sample_root=root / "data" / "samples" / "fatbeans",
        focus_maps=["2410"],
    )

    assert result["status"] == "blocked_session_capacity_source_gap"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["summary"]["session_capacity_rows"] == 1
    assert result["summary"]["bucket_only_blocked_rows"] == 1
    assert result["summary"]["exact_session_count_source_rows"] == 0
    row = result["rows"][0]
    assert row["status"] == "blocked_session_capacity_source_gap_bucket_only"
    digest = row["event_source_digest"]
    assert digest["inventory"] == {
        "total_item_count": 57,
        "warehouse_total_cells": 176,
    }
    assert digest["action_id_counts"] == {"100105": 2}
    assert digest["session_count_source_count"] == 0
    assert digest["warehouse_cells_source_count"] == 0
    assert digest["bucket_source_count"] == 2


def test_2410_public_total_row_is_not_the_session_capacity_blocker() -> None:
    module = _load_module()
    root = Path(__file__).resolve().parents[1]
    source_semantics = {
        "detail_rows": [
            {
                "file": (
                    "fatbeans_valid_aisha_2410_2rounds_2410_"
                    "1295018574148404_0093.json"
                ),
                "map_id": 2410,
                "map_family": "villa",
                "unique_residual_mode": "unique_drop_ref_only_overflow_after_temp",
                "mechanism_class": "not_unique_round_cap_blocker",
                "source_context_class": "public_total_confirmed",
                "source_evidence_class": "public_total_confirmed",
                "inventory_count": 48,
                "unique_non_temp_item_id_count": 41,
                "event_public_total_match": True,
                "event_action_result_count_all": 5,
            }
        ]
    }

    result = module.summarize_session_capacity_source_gap(
        source_semantics=source_semantics,
        sample_root=root / "data" / "samples" / "fatbeans",
        focus_maps=["2410"],
    )

    assert result["status"] == "watch_session_capacity_source_gap_audit_only"
    assert result["summary"]["exact_session_count_source_rows"] == 1
    assert result["summary"]["session_capacity_rows"] == 0
    row = result["rows"][0]
    assert row["status"] == "watch_exact_session_count_source_observed"
    digest = row["event_source_digest"]
    assert digest["session_count_source_count"] == 3
    assert digest["session_count_sources"][0]["path"] == [
        "session",
        "total_item_count",
    ]
    assert digest["session_count_sources"][0]["matches_inventory"] is True
