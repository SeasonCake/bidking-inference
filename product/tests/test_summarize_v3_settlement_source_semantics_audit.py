import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_settlement_source_semantics_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_settlement_source_semantics_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _blocker_row(path: Path, *, map_id: int = 2501) -> dict:
    return {
        "file": path.name,
        "path": str(path),
        "status": "ok",
        "map_id": map_id,
        "map_family": "shipwreck",
        "capture_day": "20260606",
        "session_token_prefix6": "129501",
        "bidmap_rounds_total": 30,
        "bidmap_sub_pool_kind": "weighted_parent",
        "inventory_count": 60,
        "non_temp_inventory_count": 58,
        "unique_non_temp_item_id_count": 55,
        "bidmap_items_per_session_max": 44,
        "bidmap_raw_round_cap_max": 50,
        "non_zodiac_missing_from_drop_universe_count": 0,
        "unique_drop_ref_excess_after_temp_zodiac_count": 11,
        "unique_round_cap_excess_after_temp_zodiac_count": 5,
        "raw_candidate_inventory_delta": 0,
        "occupied_slot_inventory_delta": 0,
        "unique_residual_mode": "unique_round_cap_overflow_after_temp",
    }


def test_table_overlay_metadata_marks_listed_missing_activity(tmp_path: Path) -> None:
    module = _load_module()
    raw = tmp_path / "data" / "raw"
    tables = raw / "tables"
    tables.mkdir(parents=True)
    (raw / "fileVersion").write_text("300\n", encoding="utf-8")
    (tables / "fileVersion").write_text("300\n", encoding="utf-8")
    (raw / "filelist.txt").write_text(
        "Ver:300|FileCount:2\nTables/Activity.txt|x\n",
        encoding="utf-8",
    )

    metadata = module._table_overlay_metadata(tmp_path)

    assert metadata["raw_file_version"] == "300"
    assert metadata["raw_tables_file_version"] == "300"
    assert metadata["activity_table_present"] is False
    assert metadata["activity_table_listed_in_filelist"] is True
    assert metadata["local_overlay_status"] == "v300_activity_listed_missing_locally"


def test_evidence_and_mechanism_classification_prefers_external_signals(
    tmp_path: Path,
) -> None:
    module = _load_module()
    row = _blocker_row(tmp_path / "case.json")

    assert (
        module._source_evidence_class(
            row,
            {"event_public_total_inventory_delta": (0,)},
        )
        == "public_total_matches_inventory"
    )
    assert (
        module._source_evidence_class(
            row,
            {"event_direct_full_observed_action_ids": (100100,)},
        )
        == "direct_action_matches_inventory"
    )
    assert (
        module._source_evidence_class(
            row,
            {"event_full_observed_action_ids": (100200,)},
        )
        == "full_action_matches_inventory"
    )
    assert module._source_evidence_class(row, {}) == "settlement_payload_verified_only"
    assert (
        module._source_context_class(
            row,
            {"event_public_total_inventory_delta": (0,)},
        )
        == "public_total_confirmed"
    )
    assert (
        module._source_context_class(
            row,
            {"event_direct_full_observed_action_ids": (100100,)},
        )
        == "direct_action_full_confirmed"
    )
    assert (
        module._source_context_class(
            row,
            {"event_public_total_count_values": (61,)},
        )
        == "payload_verified_public_total_nonmatch"
    )
    assert (
        module._source_context_class(
            row,
            {
                "event_action_result_count_all": 2,
                "event_action_observed_item_count_max": 10,
            },
        )
        == "payload_verified_partial_action_only"
    )
    assert module._source_context_class(row, {}) == "payload_verified_no_external_source"
    assert (
        module._mechanism_class(
            row,
            {"event_public_total_inventory_delta": (0,)},
        )
        == "server_side_settlement_expansion"
    )
    assert module._mechanism_class(row, {}) == "session_capacity_source_semantics"

    overlay_row = {**row, "non_zodiac_missing_from_drop_universe_count": 1}
    assert module._mechanism_class(overlay_row, {}) == "external_overlay_table"


def test_settlement_source_semantics_aggregates_unique_round_blocker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text("[]", encoding="utf-8")
    second.write_text("[]", encoding="utf-8")
    rows = (
        _blocker_row(first),
        _blocker_row(second, map_id=2503),
    )
    diagnostics = {
        first.name: {
            "event_state_count": 4,
            "event_message_id_counts": {"0x002D": 2, "0x0027": 1},
            "event_settlement_state_count": 2,
            "event_inventory_state_count": 1,
            "event_latest_inventory_count": 60,
            "event_latest_inventory_count_delta": 0,
            "event_action_result_count_all": 1,
            "event_direct_action_state_count": 1,
            "event_action_observed_item_count_max": 60,
            "event_direct_action_observed_item_count_max": 60,
            "event_full_observed_action_ids": (100100,),
            "event_direct_full_observed_action_ids": (100100,),
            "event_public_total_count_values": (),
            "event_public_total_inventory_delta": (),
            "event_public_total_match": False,
        },
        second.name: {
            "event_state_count": 3,
            "event_message_id_counts": {"0x002D": 1},
            "event_settlement_state_count": 1,
            "event_inventory_state_count": 1,
            "event_latest_inventory_count": 60,
            "event_latest_inventory_count_delta": 0,
            "event_action_result_count_all": 0,
            "event_direct_action_state_count": 0,
            "event_action_observed_item_count_max": 0,
            "event_direct_action_observed_item_count_max": 0,
            "event_full_observed_action_ids": (),
            "event_direct_full_observed_action_ids": (),
            "event_public_total_count_values": (60,),
            "event_public_total_inventory_delta": (0,),
            "event_public_total_match": True,
        },
    }
    monkeypatch.setattr(module, "_base_rows", lambda paths, *, tables: (rows, []))
    monkeypatch.setattr(
        module,
        "_source_diagnostic_for_path",
        lambda path, *, inventory_count: diagnostics[Path(path).name],
    )
    monkeypatch.setattr(
        module,
        "_table_overlay_metadata",
        lambda root: {
            "raw_file_version": "300",
            "raw_tables_file_version": "300",
            "raw_filelist_header": "Ver:300|FileCount:2",
            "raw_tables_filelist_header": None,
            "activity_table_present": False,
            "activity_table_listed_in_filelist": True,
            "local_overlay_status": "v300_activity_listed_missing_locally",
        },
    )

    result = module.summarize_settlement_source_semantics_audit(
        [tmp_path],
        tables=SimpleNamespace(),
        group_by="unique_residual_mode",
        top=8,
    )

    assert result["errors"] == []
    assert result["settlement_rows"] == 2
    assert result["overall"]["unique_above_round_after_temp_zodiac_rows"] == 2
    assert result["overall"]["source_evidence_classes"] == {
        "direct_action_matches_inventory": 1,
        "public_total_matches_inventory": 1,
    }
    assert result["overall"]["source_context_classes"] == {
        "direct_action_full_confirmed": 1,
        "public_total_confirmed": 1,
    }
    assert result["overall"]["mechanism_classes"] == {
        "server_side_settlement_expansion": 2,
    }
    assert result["overall"]["event_public_total_match_rows"] == 1
    assert result["overall"]["event_direct_full_action_rows"] == 1
    assert result["overall"]["payload_inventory_mismatch_rows"] == 0
    assert result["rows"][0]["group"] == "unique_round_cap_overflow_after_temp"
    assert result["rows"][0]["event_message_id_counts"] == {"0x002D": 3, "0x0027": 1}
    assert "detail_rows" not in result

    detailed = module.summarize_settlement_source_semantics_audit(
        [tmp_path],
        tables=SimpleNamespace(),
        group_by="unique_residual_mode",
        top=8,
        details=1,
    )

    assert len(detailed["detail_rows"]) == 1
    detail = detailed["detail_rows"][0]
    assert detail["file"] == first.name
    assert detail["map_id"] == 2501
    assert detail["unique_round_cap_excess_after_temp_zodiac_count"] == 5
    assert detail["source_evidence_class"] == "direct_action_matches_inventory"
    assert detail["mechanism_class"] == "server_side_settlement_expansion"
    assert detail["event_message_id_counts"] == {"0x002D": 2, "0x0027": 1}
    assert detail["event_full_observed_action_ids"] == [100100]
