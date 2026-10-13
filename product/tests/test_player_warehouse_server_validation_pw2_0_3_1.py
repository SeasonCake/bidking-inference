from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

from bidking_lab.live.sample_upload import WAREHOUSE_SOURCE_SCHEMA
from bidking_lab.live.warehouse_source import (
    OFFICIAL_RANDOM_WAREHOUSE,
    PLAYER_CUSTOM_WAREHOUSE,
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
    UPDATED_BIDMAP_BUILD,
    UPDATED_BIDMAP_NAME_TABLE_SHA256,
    UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256,
)

D66_SCHEMA = "player_warehouse_source_pw2_d66aee_v3"
D66_GENERATION = "d66aee-2026-08-20-599"
D66_BUILD = "24830618"
D66_NAME_TABLE_SHA256 = (
    "6e2f8aa772c23184042cdfb0b4ce99c822676707378f9a1dc2bcaf4c10ae237f"
)
D66_SOURCE_CONTRACT_SHA256 = (
    "bdae544b3b324b4037d99fbc07e94e1ae55a20d9777f1f06ba15db32e54895f5"
)
CATEGORY_AUTHORITY_GENERATION = (
    "item-table-sha256:"
    "7ac11867652b21ebbd62461cd95da84addfe721dea5c01307e4dd568fd60933c"
)


class _IntSubclass(int):
    pass


class _StrSubclass(str):
    pass


class _ListSubclass(list):
    pass


class _DictSubclass(dict):
    pass


def _collector_module():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server

    return sample_collector_server


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
    return collector, normal


def _pw2_record(
    *,
    map_id: int = 2408,
    session_hash: str = "pw2-server-validation",
    auction_mode: str = "open",
    owner_status: str = "absent",
    qualification: str = POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
    eligible: bool = False,
    reason: str = "pw2_open_owner_block_absent",
    source_conflict: bool = False,
    quarantine_required: bool = False,
) -> dict[str, Any]:
    return {
        "map_id": map_id,
        "session_hash": session_hash,
        "final_quality_counts": {"q1": 1},
        "known_value_sum": 10_000,
        "warehouse_source_schema": WAREHOUSE_SOURCE_SCHEMA,
        "warehouse_source_qualification": qualification,
        "warehouse_map_name": f"trusted-map-{map_id}",
        "warehouse_map_name_source": "bid_map_table",
        "warehouse_table_status": "verified",
        "warehouse_source_reason": reason,
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
        "warehouse_auction_mode": auction_mode,
        "warehouse_owner_evidence_status": owner_status,
        "warehouse_source_conflict": source_conflict,
        "warehouse_quarantine_required": quarantine_required,
        "official_distribution_eligible": eligible,
        "game_version": POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    }


def _add_category_fact(
    record: dict[str, Any],
    *,
    physical_map_id: int | None = None,
    model_map_id: int | None = None,
) -> dict[str, Any]:
    physical = record["map_id"] if physical_map_id is None else physical_map_id
    model = physical if model_map_id is None else model_map_id
    record.update(
        {
            "physical_map_id": physical,
            "model_map_id": model,
            "warehouse_generation": POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
            "category_authority_generation": CATEGORY_AUTHORITY_GENERATION,
            "settled_items_v1": [
                {"item_id": 1_010_001, "quality": 1, "primary_category": 101},
                {"item_id": 1_010_001, "quality": 6, "primary_category": 101},
            ],
        }
    )
    return record


def _legacy_d66aee_false_stale_record(
    *,
    map_id: int = 2408,
    session_hash: str = "pw2-d66aee-false-stale",
    auction_mode: str = "open",
    owner_status: str = "absent",
) -> dict[str, Any]:
    record = _pw2_record(
        map_id=map_id,
        session_hash=session_hash,
        auction_mode=auction_mode,
        owner_status=owner_status,
        qualification=POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
        eligible=False,
        reason="pw2_map_contract_untrusted",
        source_conflict=True,
        quarantine_required=True,
    )
    record.update(
        {
            "warehouse_source_schema": (
                "player_warehouse_source_pw2_update606_v2"
            ),
            "warehouse_table_status": "stale",
            "warehouse_source_generation": "cd4af1-2026-08-13-606",
            "warehouse_map_table_build": "24704978",
            "warehouse_map_table_sha256": D66_NAME_TABLE_SHA256,
            "warehouse_expected_map_table_sha256": (
                "4efb7c67856b6260fbcf040d0e89dc755e9fe8d476276d0074d637b63052d6b9"
            ),
            "warehouse_map_source_contract_sha256": (
                D66_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_expected_map_source_contract_sha256": (
                "a5f019d77de19599a981c26d4b3be7eff32e990fb2c93344542570314d3caea3"
            ),
            "game_version": D66_GENERATION,
        }
    )
    return record


def test_pw2_server_owned_map_projection_matches_current_trusted_contract() -> None:
    collector = _collector_module()
    projected = {
        **{
            map_id: "open"
            for first, last in (
                (2101, 2107),
                (2201, 2205),
                (2301, 2310),
                (2401, 2410),
                (2501, 2550),
                (2601, 2601),
            )
            for map_id in range(first, last + 1)
        },
        **{
            map_id: "training"
            for first, last in (
                (3101, 3107),
                (3201, 3205),
                (3301, 3310),
                (3401, 3410),
                (3501, 3510),
            )
            for map_id in range(first, last + 1)
        },
        **{
            map_id: "sealed"
            for first, last in ((4401, 4410), (4501, 4550), (5601, 5611))
            for map_id in range(first, last + 1)
        },
    }

    assert collector._PW2_TRUSTED_MAP_TABLE_BUILD == D66_BUILD  # noqa: SLF001
    assert (  # noqa: SLF001
        collector._PW2_TRUSTED_MAP_TABLE_SHA256
        == D66_NAME_TABLE_SHA256
    )
    assert (  # noqa: SLF001
        collector._PW2_TRUSTED_SOURCE_CONTRACT_SHA256
        == D66_SOURCE_CONTRACT_SHA256
    )
    assert collector._PW2_CURRENT_MAP_TABLE_BUILD == UPDATED_BIDMAP_BUILD  # noqa: SLF001
    assert collector._PW2_CURRENT_MAP_TABLE_SHA256 == (  # noqa: SLF001
        UPDATED_BIDMAP_NAME_TABLE_SHA256
    )
    assert collector._PW2_CURRENT_SOURCE_CONTRACT_SHA256 == (  # noqa: SLF001
        UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256
    )
    assert set(collector._PW2_AUTHORITIES) == {  # noqa: SLF001
        D66_SCHEMA,
        WAREHOUSE_SOURCE_SCHEMA,
        collector._PW2_OFFICIAL_20260903_SCHEMA,  # noqa: SLF001
        collector._PW2_HOTFIX_20260903_SCHEMA,  # noqa: SLF001
    }
    assert collector._PW2_TRUSTED_AUCTION_MODE_BY_MAP == projected  # noqa: SLF001


def test_server_accepts_current_and_d66_authorities_without_cross_generation_mix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    current = _pw2_record(session_hash="current-authority")
    d66 = _pw2_record(session_hash="d66-authority")
    d66.update(
        {
            "warehouse_source_schema": D66_SCHEMA,
            "warehouse_source_generation": D66_GENERATION,
            "warehouse_map_table_build": D66_BUILD,
            "warehouse_map_table_sha256": D66_NAME_TABLE_SHA256,
            "warehouse_expected_map_table_sha256": D66_NAME_TABLE_SHA256,
            "warehouse_map_source_contract_sha256": D66_SOURCE_CONTRACT_SHA256,
            "warehouse_expected_map_source_contract_sha256": (
                D66_SOURCE_CONTRACT_SHA256
            ),
            "game_version": D66_GENERATION,
        }
    )

    assert collector._accept_one(current) == "accepted"  # noqa: SLF001
    assert collector._accept_one(d66) == "accepted"  # noqa: SLF001
    stored = [
        json.loads(line) for line in normal.read_text(encoding="utf-8").splitlines()
    ]
    assert {
        (row["warehouse_source_schema"], row["warehouse_source_generation"])
        for row in stored
    } == {
        (WAREHOUSE_SOURCE_SCHEMA, POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
        (D66_SCHEMA, D66_GENERATION),
    }

    mixed = _pw2_record(session_hash="mixed-authority")
    mixed["warehouse_source_schema"] = D66_SCHEMA
    assert collector._accept_one(mixed) == "quarantined"  # noqa: SLF001


@pytest.mark.parametrize(
    (
        "map_id",
        "auction_mode",
        "owner_status",
        "qualification",
        "eligible",
        "reason",
    ),
    (
        (
            2408,
            "open",
            "absent",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_open_owner_block_absent",
        ),
        (
            2408,
            "open",
            "valid",
            PLAYER_CUSTOM_WAREHOUSE,
            False,
            "pw2_open_valid_owner_block",
        ),
        (
            4501,
            "sealed",
            "absent",
            OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "pw2_sealed_owner_block_absent",
        ),
    ),
)
def test_collector_reprojects_only_exact_d66aee_false_stale_cohort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    map_id: int,
    auction_mode: str,
    owner_status: str,
    qualification: str,
    eligible: bool,
    reason: str,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _legacy_d66aee_false_stale_record(
        map_id=map_id,
        session_hash=f"d66aee-{map_id}-{owner_status}",
        auction_mode=auction_mode,
        owner_status=owner_status,
    )

    assert collector._accept_one(record) == "accepted"  # noqa: SLF001
    stored = json.loads(normal.read_text(encoding="utf-8"))
    assert stored["warehouse_source_schema"] == D66_SCHEMA
    assert stored["warehouse_source_generation"] == D66_GENERATION
    assert stored["warehouse_table_status"] == "verified"
    assert stored["warehouse_map_table_build"] == D66_BUILD
    assert stored["warehouse_map_table_sha256"] == (
        D66_NAME_TABLE_SHA256
    )
    assert stored["warehouse_map_source_contract_sha256"] == (
        D66_SOURCE_CONTRACT_SHA256
    )
    assert stored["warehouse_source_qualification"] == qualification
    assert stored["official_distribution_eligible"] is eligible
    assert stored["warehouse_source_reason"] == reason
    assert stored["warehouse_source_conflict"] is False
    assert stored["warehouse_quarantine_required"] is False
    assert stored["server_authority_reprojection"] == (
        "d66aee_legacy_full_map_contract"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("warehouse_source_schema", "future-schema"),
        ("warehouse_map_table_sha256", "0" * 64),
        ("warehouse_expected_map_table_sha256", "0" * 64),
        ("warehouse_map_source_contract_sha256", "0" * 64),
        ("warehouse_expected_map_source_contract_sha256", "0" * 64),
        ("warehouse_table_status", "verified"),
        ("warehouse_auction_mode", "sealed"),
        ("warehouse_owner_evidence_status", "unknown"),
        ("warehouse_owner_evidence_status", "malformed_conflict"),
        ("warehouse_source_conflict", False),
        ("warehouse_quarantine_required", False),
        ("official_distribution_eligible", True),
    ),
)
def test_d66aee_false_stale_reprojection_rejects_near_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _legacy_d66aee_false_stale_record(
        session_hash=f"d66aee-near-match-{field}",
    )
    record[field] = value

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert not normal.exists()
    stored = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert stored.get("server_authority_reprojection") is None
    assert stored["server_quarantine_reason"] == "warehouse_source_pending"


def test_d66aee_false_stale_reprojection_preserves_real_owner_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _legacy_d66aee_false_stale_record(
        map_id=4501,
        session_hash="d66aee-sealed-owner-conflict",
        auction_mode="sealed",
        owner_status="valid",
    )

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    stored = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert stored.get("server_authority_reprojection") is None
    assert stored["warehouse_table_status"] == "stale"
    assert stored["warehouse_owner_evidence_status"] == "valid"
    assert stored["server_quarantine_reason"] == "warehouse_source_pending"


def test_update606_unopened_routes_never_enter_main_for_pw2_or_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    full_pw2 = _pw2_record(
        map_id=5506,
        session_hash="update606-5506-full-pw2",
        auction_mode="sealed",
        qualification=OFFICIAL_RANDOM_WAREHOUSE,
        eligible=True,
        reason="pw2_sealed_owner_block_absent",
    )
    fieldless_legacy = {
        "map_id": 6001,
        "session_hash": "update606-6001-fieldless-legacy",
        "final_quality_counts": {"q1": 1},
        "known_value_sum": 10_000,
    }

    assert collector._accept_one(full_pw2) == "quarantined"  # noqa: SLF001
    assert collector._accept_one(fieldless_legacy) == "quarantined"  # noqa: SLF001
    assert not normal.exists()
    stored = [
        json.loads(line)
        for line in normal.with_name("samples_quarantine.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {row["map_id"] for row in stored} == {5506, 6001}
    assert {
        row["server_quarantine_reason"] for row in stored
    } == {"update606_map_unopened"}

    import public_stats  # noqa: PLC0415
    import sample_dashboard  # noqa: PLC0415

    assert all(public_stats._stable_public_row(row) is None for row in stored)  # noqa: SLF001
    quarantine = normal.with_name("samples_quarantine.jsonl")
    assert list(sample_dashboard._load_samples(quarantine)) == []  # noqa: SLF001
    assert sample_dashboard._rows_for_client(stored) == []  # noqa: SLF001


@pytest.mark.parametrize(
    ("map_id", "auction_mode", "qualification", "eligible", "reason"),
    (
        (
            2533,
            "open",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_open_owner_block_absent",
        ),
        (
            4533,
            "sealed",
            OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "pw2_sealed_owner_block_absent",
        ),
        (
            4537,
            "sealed",
            OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "pw2_sealed_owner_block_absent",
        ),
        (
            2540,
            "open",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_open_owner_block_absent",
        ),
        (
            4540,
            "sealed",
            OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "pw2_sealed_owner_block_absent",
        ),
        (
            2541,
            "open",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_open_owner_block_absent",
        ),
        (
            4541,
            "sealed",
            OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "pw2_sealed_owner_block_absent",
        ),
        (
            5611,
            "sealed",
            OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "pw2_sealed_owner_block_absent",
        ),
        (
            6001,
            "auction_hall",
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_auction_hall_owner_block_absent",
        ),
    ),
)
def test_pw2_server_accepts_verified_current_activity_shipwreck_cohort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    map_id: int,
    auction_mode: str,
    qualification: str,
    eligible: bool,
    reason: str,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _pw2_record(
        map_id=map_id,
        session_hash=f"pw2-current-activity-{map_id}",
        auction_mode=auction_mode,
        qualification=qualification,
        eligible=eligible,
        reason=reason,
    )

    assert collector._accept_one(record) == "accepted"  # noqa: SLF001
    assert json.loads(normal.read_text(encoding="utf-8"))["map_id"] == map_id


def test_current_category_fact_persists_only_after_exact_server_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _add_category_fact(
        _pw2_record(
            map_id=6001,
            session_hash="current-category-positive",
            auction_mode="auction_hall",
            reason="pw2_auction_hall_owner_block_absent",
        )
    )

    assert collector._accept_one(record) == "accepted"  # noqa: SLF001
    stored = json.loads(normal.read_text(encoding="utf-8"))
    assert {
        key: stored[key]
        for key in collector._SETTLEMENT_CATEGORY_FIELDS  # noqa: SLF001
    } == {
        key: record[key]
        for key in collector._SETTLEMENT_CATEGORY_FIELDS  # noqa: SLF001
    }
    # Duplicate item types are valid: runtime identities were already checked
    # before this deidentified projection was produced.
    assert len(stored["settled_items_v1"]) == 2


def test_current_raven_category_relation_survives_only_in_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _add_category_fact(
        _pw2_record(map_id=5101, session_hash="raven-category-positive"),
        model_map_id=2501,
    )

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert not normal.exists()
    stored = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert stored["physical_map_id"] == 5101
    assert stored["model_map_id"] == 2501
    assert stored["settled_items_v1"] == record["settled_items_v1"]


@pytest.mark.parametrize(
    ("case", "mutate"),
    (
        ("partial", lambda rec: rec.pop("model_map_id")),
        ("wrong-physical", lambda rec: rec.update({"physical_map_id": 6002})),
        ("wrong-model", lambda rec: rec.update({"model_map_id": 2601})),
        (
            "wrong-fact-generation",
            lambda rec: rec.update({"warehouse_generation": D66_GENERATION}),
        ),
        (
            "wrong-root-generation",
            lambda rec: rec.update(
                {"warehouse_source_generation": D66_GENERATION}
            ),
        ),
        (
            "wrong-authority",
            lambda rec: rec.update({"category_authority_generation": "wrong"}),
        ),
        ("bool-map", lambda rec: rec.update({"physical_map_id": True})),
        (
            "subclass-model",
            lambda rec: rec.update({"model_map_id": _IntSubclass(6001)}),
        ),
        (
            "subclass-generation",
            lambda rec: rec.update(
                {
                    "warehouse_generation": _StrSubclass(
                        POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
                    )
                }
            ),
        ),
        (
            "subclass-items",
            lambda rec: rec.update(
                {"settled_items_v1": _ListSubclass(rec["settled_items_v1"])}
            ),
        ),
        (
            "subclass-row",
            lambda rec: rec["settled_items_v1"].__setitem__(
                0, _DictSubclass(rec["settled_items_v1"][0])
            ),
        ),
        (
            "bad-quality",
            lambda rec: rec["settled_items_v1"][0].update({"quality": 7}),
        ),
        (
            "bad-category",
            lambda rec: rec["settled_items_v1"][0].update(
                {"primary_category": 999}
            ),
        ),
        (
            "extra-row-key",
            lambda rec: rec["settled_items_v1"][0].update({"runtime_id": 7}),
        ),
        (
            "oversize",
            lambda rec: rec.update(
                {"settled_items_v1": rec["settled_items_v1"] * 257}
            ),
        ),
    ),
)
def test_invalid_current_category_fact_is_stripped_without_poisoning_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    mutate,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _add_category_fact(
        _pw2_record(
            map_id=6001,
            session_hash=f"category-negative-{case}",
            auction_mode="auction_hall",
            reason="pw2_auction_hall_owner_block_absent",
        )
    )
    mutate(record)

    # Malformed optional facts do not discard an otherwise valid settlement,
    # but they can never become complete posterior samples.
    disposition = collector._accept_one(record)  # noqa: SLF001
    assert disposition == (
        "quarantined" if case == "wrong-root-generation" else "accepted"
    )
    target = (
        normal.with_name("samples_quarantine.jsonl")
        if disposition == "quarantined"
        else normal
    )
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert collector._SETTLEMENT_CATEGORY_FIELDS.isdisjoint(stored)  # noqa: SLF001


def test_old_schema_remains_readable_without_category_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _pw2_record(session_hash="old-schema-no-category-upgrade")
    record.update(
        {
            "warehouse_source_schema": D66_SCHEMA,
            "warehouse_source_generation": D66_GENERATION,
            "warehouse_map_table_build": D66_BUILD,
            "warehouse_map_table_sha256": D66_NAME_TABLE_SHA256,
            "warehouse_expected_map_table_sha256": D66_NAME_TABLE_SHA256,
            "warehouse_map_source_contract_sha256": D66_SOURCE_CONTRACT_SHA256,
            "warehouse_expected_map_source_contract_sha256": (
                D66_SOURCE_CONTRACT_SHA256
            ),
            "game_version": D66_GENERATION,
        }
    )

    assert collector._accept_one(record) == "accepted"  # noqa: SLF001
    stored = json.loads(normal.read_text(encoding="utf-8"))
    assert collector._SETTLEMENT_CATEGORY_FIELDS.isdisjoint(stored)  # noqa: SLF001


@pytest.mark.parametrize("map_id", (2551, 4551, 5501, 6002, 9999))
def test_pw2_server_keeps_unverified_activity_adjacent_and_unknown_maps_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    map_id: int,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _pw2_record(
        map_id=map_id,
        session_hash=f"pw2-unverified-activity-adjacent-{map_id}",
    )

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert not normal.exists()


@pytest.mark.parametrize(
    ("case", "mutate"),
    (
        (
            "open-valid-forged-official",
            lambda rec: rec.update(
                {
                    "warehouse_owner_evidence_status": "valid",
                    "warehouse_source_qualification": OFFICIAL_RANDOM_WAREHOUSE,
                    "official_distribution_eligible": True,
                    "warehouse_source_reason": "pw2_open_valid_owner_block",
                }
            ),
        ),
        (
            "map-contract-actual-expected-mismatch",
            lambda rec: rec.update(
                {"warehouse_map_source_contract_sha256": "0" * 64}
            ),
        ),
        (
            "map-table-actual-expected-mismatch",
            lambda rec: rec.update(
                {"warehouse_expected_map_table_sha256": "f" * 64}
            ),
        ),
        (
            "unknown-owner-evidence-status",
            lambda rec: rec.update(
                {"warehouse_owner_evidence_status": "future_status"}
            ),
        ),
        (
            "wrong-trusted-auction-mode",
            lambda rec: rec.update({"warehouse_auction_mode": "sealed"}),
        ),
        (
            "invalid-boolean-flag",
            lambda rec: rec.update({"warehouse_source_conflict": 0}),
        ),
        (
            "unknown-map-contract-entry",
            lambda rec: rec.update({"map_id": 9999}),
        ),
        (
            "invalid-source-generation",
            lambda rec: rec.update(
                {"warehouse_source_generation": "future_generation"}
            ),
        ),
        (
            "incomplete-pw2-fields",
            lambda rec: rec.pop("warehouse_source_reason"),
        ),
        (
            "sealed-absent-forged-nonofficial",
            lambda rec: rec.update(
                {
                    "map_id": 4501,
                    "warehouse_auction_mode": "sealed",
                    "warehouse_source_qualification": (
                        POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
                    ),
                    "official_distribution_eligible": False,
                    "warehouse_source_reason": "pw2_sealed_owner_block_absent",
                }
            ),
        ),
    ),
)
def test_pw2_server_quarantines_forged_or_incomplete_source_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    mutate,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _pw2_record(session_hash=case)
    mutate(record)

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert not normal.exists()
    stored = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert stored["server_quarantine_reason"] == "warehouse_source_pending"


@pytest.mark.parametrize(
    "record",
    (
        _pw2_record(
            session_hash="pw2-open-malformed",
            owner_status="malformed_conflict",
            reason="pw2_owner_evidence_conflict",
            source_conflict=True,
            quarantine_required=True,
        ),
        _pw2_record(
            map_id=4501,
            session_hash="pw2-sealed-valid",
            auction_mode="sealed",
            owner_status="valid",
            reason="pw2_sealed_owner_block_conflict",
            source_conflict=True,
            quarantine_required=True,
        ),
    ),
)
def test_pw2_server_quarantines_canonical_conflict_combinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, Any],
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    stored = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert stored["warehouse_source_conflict"] is True
    assert stored["warehouse_quarantine_required"] is True


def test_pw2_server_validation_is_not_bypassed_by_client_qualification_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "BIDKING_DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION",
        "1",
    )
    forged = _pw2_record(
        owner_status="valid",
        qualification=OFFICIAL_RANDOM_WAREHOUSE,
        eligible=True,
        reason="pw2_open_valid_owner_block",
    )

    assert collector._accept_one(forged) == "quarantined"  # noqa: SLF001
    assert not normal.exists()


def test_pw2_server_generation_is_not_authorized_by_client_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    forged = _pw2_record(session_hash="pw2-client-generation-forgery")
    forged.update(
        {
            "warehouse_source_generation": "preupdate_official_random",
            "warehouse_source_qualification": OFFICIAL_RANDOM_WAREHOUSE,
            "official_distribution_eligible": True,
            "warehouse_source_reason": "preupdate_official_random_generation",
        }
    )

    assert collector._accept_one(forged) == "quarantined"  # noqa: SLF001
    assert not normal.exists()


def test_pw2_partial_projection_cannot_downgrade_to_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    forged = _pw2_record(session_hash="pw2-trigger-strip-forgery")
    for key in (
        "warehouse_map_source_contract_sha256",
        "warehouse_expected_map_source_contract_sha256",
        "warehouse_auction_mode",
        "warehouse_owner_evidence_status",
        "warehouse_source_conflict",
        "warehouse_quarantine_required",
    ):
        forged.pop(key)
    forged.update(
        {
            "warehouse_source_qualification": OFFICIAL_RANDOM_WAREHOUSE,
            "official_distribution_eligible": True,
            "warehouse_source_reason": "client-forged-official",
        }
    )

    assert collector._accept_one(forged) == "quarantined"  # noqa: SLF001
    assert not normal.exists()


@pytest.mark.parametrize(
    "schema",
    (None, "", "future-pw2", 1, True),
)
def test_pw2_schema_is_required_and_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema: object,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _pw2_record(session_hash=f"pw2-schema-{schema!r}")
    if schema is None:
        record.pop("warehouse_source_schema")
    else:
        record["warehouse_source_schema"] = schema

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert not normal.exists()


@pytest.mark.parametrize(
    "record",
    (
        _pw2_record(),
        _pw2_record(
            session_hash="pw2-open-valid",
            owner_status="valid",
            qualification=PLAYER_CUSTOM_WAREHOUSE,
            reason="pw2_open_valid_owner_block",
        ),
        _pw2_record(
            map_id=4501,
            session_hash="pw2-sealed-absent",
            auction_mode="sealed",
            qualification=OFFICIAL_RANDOM_WAREHOUSE,
            eligible=True,
            reason="pw2_sealed_owner_block_absent",
        ),
    ),
)
def test_pw2_server_accepts_only_canonical_nonconflicting_combinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, Any],
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)

    assert collector._accept_one(record) == "accepted"  # noqa: SLF001
    stored = json.loads(normal.read_text(encoding="utf-8"))
    assert stored["warehouse_source_qualification"] == (
        record["warehouse_source_qualification"]
    )
    assert stored["official_distribution_eligible"] is (
        record["official_distribution_eligible"]
    )
