"""Current producer-to-upload-to-collector schema, with old schemas retained."""
from __future__ import annotations

import copy
import sys
from types import SimpleNamespace

import pytest

from test_engine_generation_0_3_6 import GENERATION, ROOT
from bidking_lab.extract.bid_map_table import load_bid_map_table
from bidking_lab.live import monitor, sample_upload, warehouse_source
from bidking_lab.live.fatbeans import FatbeansCaptureEvents, FatbeansInventoryItem, FatbeansStateEvent

sys.path.insert(0, str(ROOT / "scripts"))
import sample_collector_server as collector


def artifact(map_id=4510, *, owner="absent"):
    maps = load_bid_map_table(ROOT / "data/raw/tables/BidMap.txt")
    source = warehouse_source.classify_from_map_table(
        map_id, maps, source_generation=GENERATION, profile_generation=GENERATION,
        game_version=GENERATION, owner_evidence_status=owner,
    ).as_dict()
    item_id, cells = (1006010, 4) if map_id == 5601 else (1016015, 6)
    session = f"{map_id}:123456789"
    events = FatbeansCaptureEvents(packets=(), frames=(), sends=(), statuses=(), states=(FatbeansStateEvent(
        sort_id=1, capture_time="2026-10-11T14:00:00+08:00", message_id=0x2D,
        session_id=session, map_id=map_id, round_index=5,
        inventory_items=(FatbeansInventoryItem(runtime_id=1, item_id=item_id, quality=6, cells=cells),),
    ),))
    fact = monitor._settlement_category_fact_v1(
        events, session_id=session, physical_map_id=map_id, model_map_id=map_id,
        tables=SimpleNamespace(maps=maps, warehouse_package_generation=GENERATION),
        warehouse_generation=GENERATION, settlement_incomplete=False,
    )
    assert fact is not None
    return dict(
        phase="settled", session_id=session, map_id=map_id, model_map_id=map_id,
        hero="aisha", round=5, warehouse_source=source,
        warehouse_source_generation=GENERATION, settlement_category_fact_v1=fact,
        final_quality_counts={"q6": 1}, final_quality_cells={"q6": cells},
        inventory_count=1, inventory_cells=cells, settlement_incomplete=False,
    )


def record(map_id=4510, **kwargs):
    value = sample_upload.trim_artifact(artifact(map_id, **kwargs))
    assert value is not None
    value["received_ts"] = collector._PW2_SERVER_OFFICIAL_20261008_EPOCH + 86400
    return value


@pytest.mark.parametrize("map_id,eligible", [(2501, False), (4510, True), (5601, True)])
def test_new_schema_and_category_reach_collector_without_promoting_source(map_id, eligible):
    value = record(map_id)
    assert value["warehouse_source_schema"] == "player_warehouse_source_pw2_76b7ca_v8"
    assert value["game_version"] == GENERATION
    assert value["official_distribution_eligible"] is eligible
    assert collector._pw2_server_contract_valid(value) is True
    category = collector._validated_current_settlement_category_fields(
        value, pw2_contract_valid=collector._pw2_server_contract_valid(value))
    assert category is not None
    assert category["category_authority_identity"] == monitor.OFFICIAL_20261008_CATEGORY_AUTHORITY_IDENTITY
    assert len(category["settled_items_v1"]) == 1
    assert "session_id" not in value and "uid_hash" not in value


@pytest.mark.parametrize("field,bad", [
    ("warehouse_source_schema", "player_warehouse_source_pw2_b049c3_v7"),
    ("warehouse_map_table_build", "25221911"),
    ("game_version", GENERATION + "-mixed"),
    ("warehouse_game_version", "b049c3-2026-09-10-597"),
    ("warehouse_map_source_contract_sha256", "0" * 64),
    ("warehouse_map_profile_generation", "unknown_warehouse_generation"),
    ("warehouse_table_status", "stale"),
])
def test_mixed_input_and_missing_generation_do_not_enter_current_contract(field, bad):
    value = record()
    value[field] = bad
    assert collector._pw2_server_contract_valid(value) is False


def test_category_mismatch_does_not_forge_current_category_or_widen_scope():
    value = record()
    value["category_authority_identity"] = "18256cb459f2c748bb1e83e6c899df255189a0f557e8a0be990c05ef865f079a"
    assert collector._validated_current_settlement_category_fields(
        value, pw2_contract_valid=collector._pw2_server_contract_valid(value)) is None
    value = record()
    value["received_ts"] = collector._PW2_SERVER_OFFICIAL_20261008_EPOCH - 0.0004
    assert collector._pw2_server_contract_valid(value) is False
    # A malformed/player owner keeps its qualification; new version is no bypass.
    value = record(4510, owner="valid")
    assert value["official_distribution_eligible"] is False
    assert value["warehouse_quarantine_required"] is True


def test_prepost_disk_version_recheck_stays_active_for_new_schema(monkeypatch):
    from bidking_lab.live import version_check
    good = record()
    monkeypatch.setattr(version_check, "detect_game_version_strict_disk", lambda: GENERATION)
    sample_upload._revalidate_exact_game_version_before_upload(good)
    assert good["official_distribution_eligible"] is True
    changed = copy.deepcopy(good)
    monkeypatch.setattr(version_check, "detect_game_version_strict_disk", lambda: GENERATION + "-changed")
    sample_upload._revalidate_exact_game_version_before_upload(changed)
    assert changed["warehouse_source_generation"] == "unknown_warehouse_generation"
    assert changed["official_distribution_eligible"] is False
    assert changed["warehouse_quarantine_required"] is True


def test_old_schema_generation_mapping_is_not_retargeted():
    assert sample_upload._EXACT_DISK_WAREHOUSE_SCHEMAS["player_warehouse_source_pw2_b049c3_v7"] == "b049c3-2026-09-10-597"
    assert collector._PW2_AUTHORITIES["player_warehouse_source_pw2_b049c3_v7"]["map_table_build"] == "25221911"
