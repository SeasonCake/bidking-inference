from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from bidking_lab.live import monitor as monitor_module
from bidking_lab.live import version_check
from bidking_lab.live.sample_upload import (
    WAREHOUSE_SOURCE_SCHEMA,
    WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260903,
    trim_artifact,
)
from bidking_lab.live.warehouse_source import (
    D66AEE_BIDMAP_BUILD,
    D66AEE_BIDMAP_NAME_TABLE_SHA256,
    D66AEE_BIDMAP_SOURCE_CONTRACT_SHA256,
    D66AEE_PLAYER_WAREHOUSE_GENERATION,
    HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20260903_BIDMAP_BUILD,
    OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256,
    OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256,
    OFFICIAL_20260903_PACKAGE_BIDMAP,
    OFFICIAL_20260903_PACKAGE_ITEM,
    OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_RANDOM_WAREHOUSE,
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    UNKNOWN_WAREHOUSE_GENERATION,
    UPDATED_BIDMAP_BUILD,
    UPDATED_BIDMAP_NAME_TABLE_SHA256,
    UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256,
    classify_from_map_table,
    warehouse_generation_from_capture_time,
    warehouse_generation_from_live_evidence,
    warehouse_package_generation_from_exact_inputs,
)


ROOT = Path(__file__).resolve().parents[1]


def _collector_module():
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server

    return sample_collector_server


def _current_maps() -> dict[int, dict]:
    rows = json.loads((ROOT / "data/processed/maps.json").read_text(encoding="utf-8"))
    return {row["map_id"]: row for row in rows}


def _prepare_collector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    collector = _collector_module()
    normal = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", normal)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", type(collector._MAP_COUNT)())
    monkeypatch.setattr(
        collector.time,
        "time",
        lambda: collector._PW2_SERVER_OFFICIAL_20260903_EPOCH + 1,
    )
    return collector, normal


def _v5_record(
    collector,
    *,
    map_id: int = 5601,
    session_hash: str = "eb1-v5",
) -> dict:
    mode = (
        "auction_hall"
        if map_id == 6001
        else collector._PW2_OFFICIAL_20260903_TRUSTED_AUCTION_MODE_BY_MAP.get(
            map_id,
            "sealed",
        )
    )
    qualification, eligible, reason, conflict = (
        collector._pw2_expected_source_values(
            auction_mode=mode,
            owner_status="absent",
            source_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        )
    )
    return {
        "map_id": map_id,
        "session_hash": session_hash,
        "final_quality_counts": {"q1": 1},
        "known_value_sum": 10_000,
        "warehouse_source_schema": WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260903,
        "warehouse_source_qualification": qualification,
        "warehouse_map_name": f"eb1-map-{map_id}",
        "warehouse_map_name_source": "bid_map_table",
        "warehouse_table_status": "verified",
        "warehouse_source_reason": reason,
        "warehouse_source_generation": (
            OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
        ),
        "warehouse_map_profile_generation": (
            OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
        ),
        "warehouse_game_version": (
            OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
        ),
        "warehouse_map_table_build": OFFICIAL_20260903_BIDMAP_BUILD,
        "warehouse_map_table_sha256": (
            OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256
        ),
        "warehouse_expected_map_table_sha256": (
            OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256
        ),
        "warehouse_map_source_contract_sha256": (
            OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256
        ),
        "warehouse_expected_map_source_contract_sha256": (
            OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256
        ),
        "warehouse_auction_mode": mode,
        "warehouse_owner_evidence_status": "absent",
        "warehouse_source_conflict": conflict,
        "warehouse_quarantine_required": conflict,
        "official_distribution_eligible": eligible,
        "game_version": OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    }


def _legacy_record(
    *,
    schema: str,
    generation: str,
    build: str,
    name_sha256: str,
    source_sha256: str,
    session_hash: str,
) -> dict:
    return {
        "map_id": 5601,
        "session_hash": session_hash,
        "final_quality_counts": {"q1": 1},
        "known_value_sum": 10_000,
        "warehouse_source_schema": schema,
        "warehouse_source_qualification": OFFICIAL_RANDOM_WAREHOUSE,
        "warehouse_map_name": "legacy-ranked",
        "warehouse_map_name_source": "bid_map_table",
        "warehouse_table_status": "verified",
        "warehouse_source_reason": "pw2_sealed_owner_block_absent",
        "warehouse_source_generation": generation,
        "warehouse_map_table_build": build,
        "warehouse_map_table_sha256": name_sha256,
        "warehouse_expected_map_table_sha256": name_sha256,
        "warehouse_map_source_contract_sha256": source_sha256,
        "warehouse_expected_map_source_contract_sha256": source_sha256,
        "warehouse_auction_mode": "sealed",
        "warehouse_owner_evidence_status": "absent",
        "warehouse_source_conflict": False,
        "warehouse_quarantine_required": False,
        "official_distribution_eligible": True,
        "game_version": generation,
    }


def test_historical_v5_identity_remains_frozen_after_current_pair_advances() -> None:
    bid_map = (ROOT / "data/raw/tables/BidMap.txt").read_bytes()
    item = (ROOT / "data/raw/tables/Item.txt").read_bytes()
    assert OFFICIAL_20260903_PACKAGE_BIDMAP == {
        "bytes": 138_356,
        "sha256": (
            "34be4bbb51a730e4b410dad87490b7ecb78f7e3b9034c1b88c20743b0a7d9b5e"
        ),
        "decoded_sha256": (
            "e4edc0d3cb6109f5f3409f4a14b54df1b1becceffcd8968991527b9636c61625"
        ),
        "decoded_bytes": 103_766,
        "raw_text_sha256": (
            "9a3b8fc6771f0398918fb8aba9bb524efb814843d470318830f40005fc242cfa"
        ),
        "raw_text_bytes": 104_014,
    }
    assert OFFICIAL_20260903_PACKAGE_ITEM == {
        "bytes": 689_916,
        "sha256": (
            "9cdcca9b2f55133d647a303fc38a6b6c9f9eb33630cf51008e09c8a0c0d9ee2e"
        ),
        "decoded_sha256": (
            "04e79becd4913f3dd9f51823e8173b358a37a6b9f681909b7272e0f025db0f99"
        ),
        "decoded_bytes": 517_435,
        "raw_text_sha256": (
            "72c3430e82e82a4b01a1fa42e79335b50ab6bbbb7f09c9b438b2511caef566c3"
        ),
        "raw_text_bytes": 518_864,
    }
    package_generation = warehouse_package_generation_from_exact_inputs(
        bid_map,
        item,
    )
    assert package_generation == HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    tampered_item = item[:-1] + bytes([item[-1] ^ 1])
    assert warehouse_package_generation_from_exact_inputs(
        bid_map,
        tampered_item,
    ) == UNKNOWN_WAREHOUSE_GENERATION
    assert warehouse_generation_from_live_evidence(
        "2026-09-03T13:00:00+08:00",
        game_version=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        package_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    ) == OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION

    for game_version in (
        "e8d0e7-2026-09-03-753",
        "eb1e8f-2026-09-03-711-extra",
        "2026-09-03",
        "",
        None,
    ):
        assert warehouse_generation_from_live_evidence(
            "2026-09-03T13:00:00+08:00",
            game_version=game_version,
            package_generation=package_generation,
        ) == UNKNOWN_WAREHOUSE_GENERATION
    assert warehouse_generation_from_live_evidence(
        "2026-09-03T13:00:00+08:00",
        game_version=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        package_generation=UNKNOWN_WAREHOUSE_GENERATION,
    ) == UNKNOWN_WAREHOUSE_GENERATION
    assert warehouse_generation_from_live_evidence(
        "2026-09-02T23:59:59+08:00",
        game_version="e8d0e7-2026-09-03-753",
        package_generation=UNKNOWN_WAREHOUSE_GENERATION,
    ) == POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
    assert warehouse_generation_from_capture_time(
        "2026-09-03T13:00:00+08:00"
    ) == UNKNOWN_WAREHOUSE_GENERATION


def test_strict_disk_version_ignores_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_file = tmp_path / "DefaultPackage.version"
    version_file.write_text(
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    monkeypatch.setattr(version_check, "_find_game_version_file", lambda: version_file)
    monkeypatch.setenv("BIDKING_GAME_VERSION", "e8d0e7-2026-09-03-753")
    assert version_check.detect_game_version() == "e8d0e7-2026-09-03-753"
    assert version_check.detect_game_version_strict_disk() == (
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
    )


def test_historical_v5_category_authority_cannot_borrow_current_disk() -> None:
    assert monitor_module._settlement_category_authority(  # noqa: SLF001
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
    ) is None


def test_eb1_map_scope_and_unfrozen_generation_are_fail_closed() -> None:
    maps = _current_maps()
    for map_id in (5203, 5219, 5601, 5611):
        result = classify_from_map_table(
            map_id,
            maps,
            source_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            profile_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            game_version=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            owner_evidence_status="absent",
        )
        assert result.qualification == OFFICIAL_RANDOM_WAREHOUSE
        assert result.official_distribution_eligible is True
        assert result.quarantine_required is False
        assert result.auction_mode == "sealed"
    for map_id in (5506, 6001):
        result = classify_from_map_table(
            map_id,
            maps,
            source_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            profile_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            game_version=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            owner_evidence_status="absent",
        )
        assert result.official_distribution_eligible is False
        assert result.quarantine_required is True
        assert result.reason == "official_20260903_map_unopened"

    unfrozen = classify_from_map_table(
        5601,
        maps,
        source_generation=UNKNOWN_WAREHOUSE_GENERATION,
        profile_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        game_version="e8d0e7-2026-09-03-753",
        owner_evidence_status="absent",
    )
    assert unfrozen.reason == "warehouse_generation_unverified"
    assert unfrozen.quarantine_required is True


def test_trim_uses_v5_and_preserves_current_category_100_through_111() -> None:
    maps = _current_maps()
    source = classify_from_map_table(
        5601,
        maps,
        source_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        profile_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        game_version=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        owner_evidence_status="absent",
    )
    artifact = {
        "phase": "settled",
        "session_id": "5601:eb1",
        "map_id": 5601,
        "model_map_id": 5601,
        "warehouse_source_generation": source.source_generation,
        "warehouse_source": source.as_dict(),
        "final_quality_counts": {"q6": 2},
        "known_value_sum": 8_000_000,
        "settlement_category_fact_v1": {
            "schema": "settlement_category_fact_v2",
            "physical_map_id": 5601,
            "model_map_id": 5601,
            "warehouse_generation": source.source_generation,
            "category_authority_generation": (
                monitor_module.OFFICIAL_20260903_CATEGORY_AUTHORITY_GENERATION
            ),
            "category_authority_identity": (
                monitor_module.OFFICIAL_20260903_CATEGORY_AUTHORITY_IDENTITY
            ),
            "settled_items_v1": [
                {"item_id": 1006010, "quality": 6, "primary_category": 100},
                {"item_id": 1116003, "quality": 6, "primary_category": 111},
            ],
        },
    }
    record = trim_artifact(artifact)
    assert record is not None
    assert record["warehouse_source_schema"] == (
        WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260903
    )
    assert record["warehouse_game_version"] == (
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
    )
    assert record["category_authority_identity"] == (
        monitor_module.OFFICIAL_20260903_CATEGORY_AUTHORITY_IDENTITY
    )
    assert [row["primary_category"] for row in record["settled_items_v1"]] == [
        100,
        111,
    ]


def test_server_v5_routes_current_maps_and_keeps_dormant_maps_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    for map_id in (5203, 5219, 5601, 5611):
        assert collector._accept_one(  # noqa: SLF001
            _v5_record(
                collector,
                map_id=map_id,
                session_hash=f"v5-{map_id}",
            )
        ) == "accepted"
    assert collector._accept_one(  # noqa: SLF001
        _v5_record(collector, map_id=5601, session_hash="v5-5601")
    ) == "duplicate"

    for map_id in (5506, 6001):
        assert collector._accept_one(  # noqa: SLF001
            _v5_record(
                collector,
                map_id=map_id,
                session_hash=f"v5-dormant-{map_id}",
            )
        ) == "quarantined"
    assert len(normal.read_text(encoding="utf-8").splitlines()) == 4
    quarantine = normal.with_name("samples_quarantine.jsonl")
    assert len(quarantine.read_text(encoding="utf-8").splitlines()) == 2


def test_server_rejects_unfrozen_and_cross_generation_pw2_without_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    v5_e8 = _v5_record(collector, session_hash="v5-e8")
    v5_e8["game_version"] = "e8d0e7-2026-09-03-753"
    v5_e8["warehouse_game_version"] = "e8d0e7-2026-09-03-753"
    assert collector._accept_one(v5_e8) == "quarantined"  # noqa: SLF001
    mixed = _v5_record(collector, session_hash="v5-hash-under-v4-schema")
    mixed["warehouse_source_schema"] = WAREHOUSE_SOURCE_SCHEMA
    assert collector._accept_one(mixed) == "quarantined"  # noqa: SLF001

    v4 = _legacy_record(
        schema=WAREHOUSE_SOURCE_SCHEMA,
        generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        build=UPDATED_BIDMAP_BUILD,
        name_sha256=UPDATED_BIDMAP_NAME_TABLE_SHA256,
        source_sha256=UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256,
        session_hash="v4-historical",
    )
    assert collector._accept_one(v4) == "accepted"  # noqa: SLF001
    v4_new_game = dict(v4, session_hash="v4-eb1")
    v4_new_game["game_version"] = OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
    assert collector._accept_one(v4_new_game) == "quarantined"  # noqa: SLF001

    d66 = _legacy_record(
        schema=collector._PW2_SCHEMA,  # noqa: SLF001
        generation=D66AEE_PLAYER_WAREHOUSE_GENERATION,
        build=D66AEE_BIDMAP_BUILD,
        name_sha256=D66AEE_BIDMAP_NAME_TABLE_SHA256,
        source_sha256=D66AEE_BIDMAP_SOURCE_CONTRACT_SHA256,
        session_hash="d66-historical",
    )
    assert collector._accept_one(d66) == "accepted"  # noqa: SLF001

    stored = [
        json.loads(line)
        for line in normal.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["session_hash"] for row in stored} == {
        "v4-historical",
        "d66-historical",
    }


def test_server_v5_category_identity_and_raw_100_111_are_generation_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _v5_record(collector, session_hash="v5-category")
    record.update(
        {
            "physical_map_id": 5601,
            "model_map_id": 5601,
            "warehouse_generation": (
                OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
            ),
            "category_authority_generation": (
                collector._OFFICIAL_20260903_CATEGORY_AUTHORITY_GENERATION
            ),
            "category_authority_identity": (
                collector._OFFICIAL_20260903_CATEGORY_AUTHORITY_IDENTITY
            ),
            "settled_items_v1": [
                {"item_id": 1006010, "quality": 6, "primary_category": 100},
                {"item_id": 1116003, "quality": 6, "primary_category": 111},
            ],
        }
    )
    assert collector._accept_one(record) == "accepted"  # noqa: SLF001
    stored = json.loads(normal.read_text(encoding="utf-8"))
    assert stored["category_authority_identity"] == (
        collector._OFFICIAL_20260903_CATEGORY_AUTHORITY_IDENTITY
    )
    assert [row["primary_category"] for row in stored["settled_items_v1"]] == [
        100,
        111,
    ]

    wrong = dict(record, session_hash="v5-category-wrong")
    wrong["category_authority_identity"] = "0" * 64
    assert collector._accept_one(wrong) == "accepted"  # noqa: SLF001
    rows = [json.loads(line) for line in normal.read_text(encoding="utf-8").splitlines()]
    wrong_stored = rows[-1]
    assert "category_authority_identity" not in wrong_stored
    assert "settled_items_v1" not in wrong_stored

    unfrozen = dict(record, session_hash="v5-category-e8")
    unfrozen["game_version"] = "e8d0e7-2026-09-03-753"
    unfrozen["warehouse_game_version"] = "e8d0e7-2026-09-03-753"
    assert collector._accept_one(unfrozen) == "quarantined"  # noqa: SLF001
    quarantined = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert "category_authority_identity" not in quarantined
    assert "settled_items_v1" not in quarantined
