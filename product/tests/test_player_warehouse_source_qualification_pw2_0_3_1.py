from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from bidking_lab.extract.current_rankmap_package_contract import (
    CURRENT_PACKAGE_GENERATION,
    CURRENT_STEAM_BUILD_ID,
    CURRENT_TEXT_ASSET_IDENTITIES,
)
from bidking_lab.live.warehouse_source import (
    HOTFIX_20260903_BIDMAP_BUILD,
    HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_RANDOM_WAREHOUSE,
    OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256,
    OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256,
    OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    PLAYER_CUSTOM_WAREHOUSE,
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
    WAREHOUSE_TABLE_STALE,
    WAREHOUSE_TABLE_VERIFIED,
    UPDATED_BIDMAP_BUILD,
    UPDATED_BIDMAP_NAME_TABLE_SHA256,
    UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256,
    classify_from_map_table,
    warehouse_map_name_table_sha256,
    warehouse_map_source_contract_sha256,
    warehouse_map_table_status,
)
from bidking_lab.live.sample_upload import (
    WAREHOUSE_SOURCE_SCHEMA,
    trim_artifact,
)


def _maps(mode: str, *, map_id: int = 2408):
    return {
        map_id: SimpleNamespace(
            name=f"trusted-{mode}-{map_id}",
            auction_mode=mode,
        )
    }


@pytest.mark.parametrize(
    ("auction_mode", "owner_status", "qualification", "eligible", "reason"),
    (
        (
            "open",
            "valid",
            PLAYER_CUSTOM_WAREHOUSE,
            False,
            "pw2_open_valid_owner_block",
        ),
        (
            "open",
            "absent",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_open_owner_block_absent",
        ),
        (
            "sealed",
            "absent",
            OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "pw2_sealed_owner_block_absent",
        ),
        (
            "sealed",
            "valid",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_sealed_owner_block_conflict",
        ),
        (
            "training",
            "valid",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_training_owner_block_conflict",
        ),
    ),
)
def test_pw2_trusted_mode_and_owner_evidence_matrix(
    auction_mode: str,
    owner_status: str,
    qualification: str,
    eligible: bool,
    reason: str,
) -> None:
    result = classify_from_map_table(
        2408,
        _maps(auction_mode),
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
        owner_evidence_status=owner_status,
    )

    assert result.qualification == qualification
    assert result.official_distribution_eligible is eligible
    assert result.reason == reason
    assert result.auction_mode == auction_mode
    assert result.owner_evidence_status == owner_status
    assert result.quarantine_required is (
        owner_status == "valid" and auction_mode != "open"
    )


@pytest.mark.parametrize(
    "owner_status",
    ("malformed_conflict", "unknown_status", "", True, 1),
)
def test_pw2_malformed_or_invalid_owner_evidence_fails_closed(
    owner_status: object,
) -> None:
    result = classify_from_map_table(
        2408,
        _maps("open"),
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
        owner_evidence_status=owner_status,
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.official_distribution_eligible is False
    assert result.reason == "pw2_owner_evidence_conflict"
    assert result.quarantine_required is True


@pytest.mark.parametrize(
    ("maps", "table_status"),
    (
        (None, WAREHOUSE_TABLE_VERIFIED),
        (_maps("open"), WAREHOUSE_TABLE_STALE),
    ),
)
def test_pw2_missing_or_stale_map_contract_cannot_promote_valid_owner(
    maps,
    table_status: str,
) -> None:
    result = classify_from_map_table(
        2408,
        maps,
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=table_status,
        owner_evidence_status="valid",
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.official_distribution_eligible is False
    assert result.reason == "pw2_map_contract_untrusted"
    assert result.quarantine_required is True


def test_pw2_map_name_and_unrelated_heuristics_are_not_inputs() -> None:
    normal_name = classify_from_map_table(
        2408,
        {2408: SimpleNamespace(name="普通官方名字", auction_mode="open")},
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
        owner_evidence_status="absent",
    )
    suspicious_name = classify_from_map_table(
        2408,
        {2408: SimpleNamespace(name="未知玩家自定义仓", auction_mode="open")},
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
        owner_evidence_status="absent",
    )

    assert normal_name.qualification == suspicious_name.qualification
    assert normal_name.reason == suspicious_name.reason
    assert normal_name.official_distribution_eligible is False


@pytest.mark.parametrize("map_id", (2408, 2501))
def test_pw2_named_open_owner_positive_controls(map_id: int) -> None:
    result = classify_from_map_table(
        map_id,
        _maps("open", map_id=map_id),
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
        owner_evidence_status="valid",
    )

    assert result.qualification == PLAYER_CUSTOM_WAREHOUSE
    assert result.reason == "pw2_open_valid_owner_block"
    assert result.official_distribution_eligible is False


def test_current_map_source_contract_identity_is_exact_and_drift_is_stale() -> None:
    assert (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
        == CURRENT_PACKAGE_GENERATION
    )
    assert HOTFIX_20260903_BIDMAP_BUILD == CURRENT_STEAM_BUILD_ID == "25096377"
    assert CURRENT_TEXT_ASSET_IDENTITIES["BidMap"].utf8_sha256 == (
        "f93a30b6bbcad22a003cf1363919af1d42a2eb600ec9ee006dc37865a0fc15a6"
    )
    maps_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "processed"
        / "maps.json"
    )
    rows = json.loads(maps_path.read_text(encoding="utf-8"))
    maps = {row["map_id"]: row for row in rows}
    assert len(maps) == 246
    assert (
        warehouse_map_name_table_sha256(maps)
        == OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256
    )
    assert (
        warehouse_map_source_contract_sha256(maps)
        == OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256
    )
    assert (
        warehouse_map_table_status(
            maps,
            profile_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        )
        == WAREHOUSE_TABLE_VERIFIED
    )
    # The hotfix does not rewrite PW2's name + auction-mode projection, so the
    # sealed historical eb1 profile remains independently verifiable.
    assert (
        warehouse_map_table_status(
            maps,
            profile_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        )
        == WAREHOUSE_TABLE_VERIFIED
    )

    drifted = {map_id: dict(row) for map_id, row in maps.items()}
    drifted[2408]["auction_mode"] = "sealed"
    assert (
        warehouse_map_table_status(
            drifted,
            profile_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        )
        == WAREHOUSE_TABLE_STALE
    )
    assert warehouse_map_table_status(_maps("open")) == WAREHOUSE_TABLE_STALE


@pytest.mark.parametrize("map_id", (5506,))
def test_update606_unopened_routes_are_source_quarantined_even_under_rollback(
    map_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BIDKING_DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION", "1"
    )
    result = classify_from_map_table(
        map_id,
        _maps("sealed", map_id=map_id),
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
        owner_evidence_status="absent",
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "update606_map_unopened"
    assert result.official_distribution_eligible is False
    assert result.source_conflict is True
    assert result.quarantine_required is True


@pytest.mark.parametrize(
    ("owner_status", "qualification", "reason"),
    (
        (
            "absent",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            "pw2_auction_hall_owner_block_absent",
        ),
        (
            "valid",
            PLAYER_CUSTOM_WAREHOUSE,
            "pw2_auction_hall_valid_owner_block",
        ),
    ),
)
def test_current_6001_is_observed_but_never_distribution_eligible(
    owner_status: str,
    qualification: str,
    reason: str,
) -> None:
    result = classify_from_map_table(
        6001,
        _maps("auction_hall", map_id=6001),
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
        owner_evidence_status=owner_status,
    )

    assert result.qualification == qualification
    assert result.reason == reason
    assert result.official_distribution_eligible is False
    assert result.source_conflict is False
    assert result.quarantine_required is False


def _collector_module():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server

    return sample_collector_server


def test_pw2_conflict_upload_is_privacy_safe_and_server_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = {
        "phase": "settled",
        "session_id": "4501:pw2-conflict",
        "map_id": 4501,
        "final_quality_counts": {"q1": 1},
        "known_value_sum": 10_000,
        "warehouse_source": {
            "qualification": POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            "map_name": "trusted-sealed-4501",
            "map_name_source": "bid_map_table",
            "table_status": WAREHOUSE_TABLE_VERIFIED,
            "reason": "pw2_sealed_owner_block_conflict",
            "source_generation": POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
            "map_table_build": "24332441",
            "map_table_sha256": "name-contract",
            "expected_map_table_sha256": "name-contract",
            "map_source_contract_sha256": "source-contract",
            "expected_map_source_contract_sha256": "source-contract",
            "auction_mode": "sealed",
            "owner_evidence_status": "valid",
            "source_conflict": True,
            "quarantine_required": True,
            "official_distribution_eligible": False,
            "owner_identity_alias": "must-not-upload",
            "owner_display_alias": "must-not-upload",
        },
    }
    record = trim_artifact(artifact)
    assert record is not None
    assert record["warehouse_auction_mode"] == "sealed"
    assert record["warehouse_owner_evidence_status"] == "valid"
    assert record["warehouse_source_conflict"] is True
    assert record["warehouse_quarantine_required"] is True
    assert "owner_identity_alias" not in json.dumps(record, sort_keys=True)
    assert "owner_display_alias" not in json.dumps(record, sort_keys=True)

    collector = _collector_module()
    normal = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", normal)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", type(collector._MAP_COUNT)())

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert not normal.exists()
    quarantine = normal.with_name("samples_quarantine.jsonl")
    stored = json.loads(quarantine.read_text(encoding="utf-8"))
    assert stored["server_quarantine_reason"] == "warehouse_source_pending"
    assert stored["warehouse_source_conflict"] is True
    assert stored["warehouse_quarantine_required"] is True


def test_pw2_open_absent_upload_remains_mixed_not_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector_module()
    normal = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", normal)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", type(collector._MAP_COUNT)())
    record = {
        "map_id": 2408,
        "session_hash": "pw2-open-absent",
        "final_quality_counts": {"q1": 1},
        "known_value_sum": 10_000,
        "warehouse_source_schema": WAREHOUSE_SOURCE_SCHEMA,
        "warehouse_source_qualification": (
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
        ),
        "warehouse_map_name": "奢华养老院",
        "warehouse_map_name_source": "bid_map_table",
        "warehouse_table_status": WAREHOUSE_TABLE_VERIFIED,
        "warehouse_source_reason": "pw2_open_owner_block_absent",
        "warehouse_source_generation": POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        "warehouse_map_table_build": UPDATED_BIDMAP_BUILD,
        "warehouse_map_table_sha256": UPDATED_BIDMAP_NAME_TABLE_SHA256,
        "warehouse_expected_map_table_sha256": (
            UPDATED_BIDMAP_NAME_TABLE_SHA256
        ),
        "warehouse_map_source_contract_sha256": (
            UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256
        ),
        "warehouse_expected_map_source_contract_sha256": (
            UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256
        ),
        "warehouse_auction_mode": "open",
        "warehouse_owner_evidence_status": "absent",
        "official_distribution_eligible": False,
        "warehouse_source_conflict": False,
        "warehouse_quarantine_required": False,
        "game_version": POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    }

    assert collector._accept_one(record) == "accepted"  # noqa: SLF001
    stored = json.loads(normal.read_text(encoding="utf-8"))
    assert stored["warehouse_source_qualification"] == (
        POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    )
    assert stored["warehouse_source_conflict"] is False
    assert stored["warehouse_quarantine_required"] is False


@pytest.mark.parametrize(
    ("source_conflict", "quarantine_required"),
    (
        ({}, False),
        (False, []),
        ("false", False),
        (False, 0),
    ),
)
def test_pw2_malformed_server_conflict_flags_fail_closed_without_type_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_conflict: object,
    quarantine_required: object,
) -> None:
    collector = _collector_module()
    normal = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", normal)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", type(collector._MAP_COUNT)())
    record = {
        "map_id": 2408,
        "session_hash": "pw2-malformed-flags",
        "final_quality_counts": {"q1": 1},
        "known_value_sum": 10_000,
        "warehouse_source_qualification": (
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
        ),
        "official_distribution_eligible": False,
        "warehouse_source_conflict": source_conflict,
        "warehouse_quarantine_required": quarantine_required,
    }

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert not normal.exists()
    quarantine = normal.with_name("samples_quarantine.jsonl")
    stored = json.loads(quarantine.read_text(encoding="utf-8"))
    assert stored["server_quarantine_reason"] == "warehouse_source_pending"
