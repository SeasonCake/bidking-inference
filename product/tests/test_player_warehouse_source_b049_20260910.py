"""b049 client source/upload guards; deferred server tests live in the companion module."""
from __future__ import annotations

import base64
import json
import sys
import os
from types import SimpleNamespace
from pathlib import Path

import pytest

from bidking_lab.live import monitor, sample_upload, warehouse_source as warehouse
from test_official_b049_minimal_compat_20260910 import CANDIDATE, GENERATION, STATIC
from test_player_warehouse_source_pw2_68171c_v6 import _collector_module, _v6_record

OLD = "68171c-2026-09-03-907"
ROOT = Path(__file__).resolve().parents[1]
OLD_CATEGORY_ROOT = Path(os.environ.get("BIDKING_OLD_CATEGORY_ROOT", "C:/example/bidking-workspace/scratch/bidking_035_small_hotfix_20260910_01/initial_sources_frozen"))


def _old_package(name):
    rows = json.loads((STATIC / "baseline_rows" / (name + ".json")).read_bytes())
    return base64.b64encode("\n".join("\t".join(row) for row in rows).encode("utf-8"))


def test_exact_table_pair_and_mixed_negative_controls():
    bidmap, items = [(CANDIDATE / name).read_bytes() for name in ("BidMap.txt", "Item.txt")]
    assert warehouse.warehouse_package_generation_from_exact_inputs(bidmap, items) == GENERATION
    assert warehouse.warehouse_package_generation_from_exact_inputs(_old_package("BidMap"), _old_package("Item")) == OLD
    for left, right in ((bidmap, _old_package("Item")), (_old_package("BidMap"), items), (bidmap + b"x", items), (None, items)):
        assert warehouse.warehouse_package_generation_from_exact_inputs(left, right) == warehouse.UNKNOWN_WAREHOUSE_GENERATION


@pytest.mark.parametrize("game,package,expected", [(GENERATION, GENERATION, GENERATION), (OLD, OLD, OLD),
    (GENERATION, OLD, "unknown_warehouse_generation"), (OLD, GENERATION, "unknown_warehouse_generation"),
    (GENERATION + "-suffix", GENERATION, "unknown_warehouse_generation"), (None, GENERATION, "unknown_warehouse_generation"),
    (GENERATION, None, "unknown_warehouse_generation")])
def test_live_generation_requires_exact_pair(game, package, expected):
    assert warehouse.warehouse_generation_from_live_evidence("2026-09-10T12:00:00+08:00", game_version=game, package_generation=package) == expected


@pytest.mark.parametrize("map_id,owner,expected", [(5601, "absent", warehouse.OFFICIAL_RANDOM_WAREHOUSE),
    (4511, "absent", warehouse.OFFICIAL_RANDOM_WAREHOUSE), (2511, "absent", warehouse.POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE),
    (2511, "valid", warehouse.PLAYER_CUSTOM_WAREHOUSE), (4511, "valid", warehouse.POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE),
    (6001, "absent", warehouse.POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE)])
def test_warehouse_source_separation_is_preserved(map_id, owner, expected):
    maps = {row["map_id"]: row for row in json.loads((CANDIDATE / "maps.json").read_bytes())}
    result = warehouse.classify_from_map_table(map_id, maps, source_generation=GENERATION,
        profile_generation=GENERATION, game_version=GENERATION, owner_evidence_status=owner)
    assert result.qualification == expected
    assert result.official_distribution_eligible is (expected == warehouse.OFFICIAL_RANDOM_WAREHOUSE)
    assert result.map_table_build == "25221911"


def _v7_record(collector):
    record = _v6_record(collector, session_hash="synthetic-b049-v7")
    for field in ("game_version", "warehouse_game_version", "warehouse_source_generation", "warehouse_map_profile_generation"):
        record[field] = GENERATION
    record["warehouse_source_schema"] = "player_warehouse_source_pw2_b049c3_v7"
    record["warehouse_map_table_build"] = "25221911"
    # This seam is the server-owned normalized record after ingest timestamps it.
    record["received_ts"] = collector.time.time()
    return record


def test_upload_revalidates_b049_game_version_before_post(monkeypatch):
    record = _v7_record(_collector_module())
    monkeypatch.setattr("bidking_lab.live.version_check.detect_game_version_strict_disk", lambda: OLD)
    sample_upload._revalidate_exact_game_version_before_upload(record)
    assert record["warehouse_source_generation"] == warehouse.UNKNOWN_WAREHOUSE_GENERATION
    assert record["official_distribution_eligible"] is False
    assert record["warehouse_quarantine_required"] is True


def test_monitor_category_profile_keeps_generations_separate():
    new = monitor._current_category_authority_profile(GENERATION)
    old = monitor._current_category_authority_profile(OLD)
    assert new["identity"] == "18256cb459f2c748bb1e83e6c899df255189a0f557e8a0be990c05ef865f079a"
    assert old["identity"] == "d23d90c0d4f432f94a129435417063177ab21646fa4987dce5118ccce5a55db5"
    assert monitor._current_category_authority_profile(GENERATION + "-suffix") is None
    assert new["item_rows"] == 1432 and old["item_rows"] == 1427
    assert monitor._current_category_authority_profile("eb1e8f-2026-09-03-711")["item_rows"] == 1427


def test_live_category_loader_accepts_exact_old_and_new_profiles(monkeypatch):
    monkeypatch.setattr(monitor, "project_root", lambda: OLD_CATEGORY_ROOT)
    assert monitor._settlement_category_authority(OLD) is not None
    assert monitor._settlement_category_authority(GENERATION) is None
    monkeypatch.setattr(monitor, "project_root", lambda: ROOT)
    assert monitor._settlement_category_authority(GENERATION) is not None
    assert monitor._settlement_category_authority(OLD) is None


@pytest.mark.parametrize("physical,model", [(4510, 4510), (5602, 5602), (5101, 2501)])
def test_live_complete_category_fact_and_upload_survive_b049(monkeypatch, physical, model):
    from bidking_lab.live.fatbeans import FatbeansCaptureEvents, FatbeansInventoryItem, FatbeansStateEvent
    maps = {row["map_id"]: row for row in json.loads((ROOT / "data/processed/maps.json").read_bytes())}
    session = f"{physical}:991000002"
    events = FatbeansCaptureEvents(packets=(), frames=(), sends=(), statuses=(), states=(FatbeansStateEvent(
        sort_id=1, capture_time="2026-09-10T17:30:00+08:00", message_id=0x002D, session_id=session,
        map_id=physical, round_index=5, inventory_items=(FatbeansInventoryItem(runtime_id=7,
            item_id=1006010, quality=6, cells=4),)),))
    args = dict(session_id=session, physical_map_id=physical, model_map_id=model, settlement_incomplete=False)
    monkeypatch.setattr(monitor, "project_root", lambda: OLD_CATEGORY_ROOT)
    old = monitor._settlement_category_fact_v1(events, **args, warehouse_generation=OLD,
        tables=SimpleNamespace(maps=maps, warehouse_package_generation=OLD))
    assert old is not None and old["settled_items_v1"] == [{"item_id": 1006010, "quality": 6, "primary_category": 100}]
    monkeypatch.setattr(monitor, "project_root", lambda: ROOT)
    fact = monitor._settlement_category_fact_v1(events, **args, warehouse_generation=GENERATION,
        tables=SimpleNamespace(maps=maps, warehouse_package_generation=GENERATION))
    assert fact is not None and fact["settled_items_v1"] == old["settled_items_v1"]
    source = warehouse.classify_from_map_table(physical, maps, source_generation=GENERATION,
        profile_generation=GENERATION, game_version=GENERATION, owner_evidence_status="absent").as_dict()
    artifact = dict(phase="settled", session_id=session, map_id=physical, model_map_id=model,
        warehouse_source_generation=GENERATION, warehouse_source=source,
        final_quality_counts={"q6": 1}, settlement_category_fact_v1=fact)
    assert sample_upload._settlement_category_upload_candidate(artifact) is not None
    assert monitor._settlement_category_fact_v1(events, **dict(args, settlement_incomplete=True), warehouse_generation=GENERATION,
        tables=SimpleNamespace(maps=maps, warehouse_package_generation=GENERATION)) is None


@pytest.mark.parametrize("rows", [1427, 1431, 1433, True, "1432"])
def test_new_category_row_check_does_not_accept_arbitrary_size(monkeypatch, rows):
    original = monitor.load_item_category_authority_document
    def wrong_rows(path):
        authority, document = original(path)
        document["sources"]["item_table"]["rows"] = rows
        return authority, document
    monkeypatch.setattr(monitor, "load_item_category_authority_document", wrong_rows)
    assert monitor._settlement_category_authority(GENERATION) is None
