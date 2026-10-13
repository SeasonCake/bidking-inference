from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from bidking_lab.live import monitor as monitor_module
from bidking_lab.live import sample_upload
from bidking_lab.live.sample_upload import (
    WAREHOUSE_SOURCE_SCHEMA_HOTFIX_20260903,
    trim_artifact,
)
from bidking_lab.live.warehouse_source import (
    HOTFIX_20260903_BIDMAP_BUILD,
    HOTFIX_20260903_PACKAGE_BIDMAP,
    HOTFIX_20260903_PACKAGE_ITEM,
    HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256,
    OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256,
    OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_RANDOM_WAREHOUSE,
    POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
    UNKNOWN_WAREHOUSE_GENERATION,
    classify_from_map_table,
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
        lambda: collector._PW2_SERVER_HOTFIX_20260903_EPOCH + 1,
    )
    return collector, normal


def _v6_record(
    collector,
    *,
    map_id: int = 5601,
    session_hash: str = "681-v6",
) -> dict:
    mode = collector._PW2_HOTFIX_20260903_TRUSTED_AUCTION_MODE_BY_MAP.get(
        map_id,
        "sealed",
    )
    qualification, eligible, reason, conflict = (
        collector._pw2_expected_source_values(
            auction_mode=mode,
            owner_status="absent",
            source_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        )
    )
    return {
        "map_id": map_id,
        "session_hash": session_hash,
        "final_quality_counts": {"q1": 1},
        "known_value_sum": 10_000,
        "warehouse_source_schema": WAREHOUSE_SOURCE_SCHEMA_HOTFIX_20260903,
        "warehouse_source_qualification": qualification,
        "warehouse_map_name": f"681-map-{map_id}",
        "warehouse_map_name_source": "bid_map_table",
        "warehouse_table_status": "verified",
        "warehouse_source_reason": reason,
        "warehouse_source_generation": (
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
        ),
        "warehouse_map_profile_generation": (
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
        ),
        "warehouse_game_version": HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        "warehouse_map_table_build": HOTFIX_20260903_BIDMAP_BUILD,
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
        "game_version": HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    }


def test_681_generation_is_exact_and_mixed_pairs_fail_closed() -> None:
    bid_map = (ROOT / "data/raw/tables/BidMap.txt").read_bytes()
    item = (ROOT / "data/raw/tables/Item.txt").read_bytes()
    assert (len(bid_map), len(item)) == (
        HOTFIX_20260903_PACKAGE_BIDMAP["bytes"],
        HOTFIX_20260903_PACKAGE_ITEM["bytes"],
    )
    assert warehouse_package_generation_from_exact_inputs(
        bid_map,
        item,
    ) == HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    assert warehouse_package_generation_from_exact_inputs(
        bid_map[:-1] + bytes([bid_map[-1] ^ 1]),
        item,
    ) == UNKNOWN_WAREHOUSE_GENERATION

    assert warehouse_generation_from_live_evidence(
        "2026-09-03T16:46:00+08:00",
        game_version=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        package_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    ) == HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    for game_version, package_generation in (
        (
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        ),
        ("e8d0e7-2026-09-03-753", HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION),
        (
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION + "-extra",
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        ),
        ("", HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION),
    ):
        assert warehouse_generation_from_live_evidence(
            "2026-09-03T16:46:00+08:00",
            game_version=game_version,
            package_generation=package_generation,
        ) == UNKNOWN_WAREHOUSE_GENERATION


def test_clean_681_routes_only_the_sealed_current_scope() -> None:
    maps = _current_maps()
    for map_id in (5203, 5219, 5601, 5611):
        result = classify_from_map_table(
            map_id,
            maps,
            source_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            profile_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            game_version=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            owner_evidence_status="absent",
        )
        assert result.qualification == OFFICIAL_RANDOM_WAREHOUSE
        assert result.official_distribution_eligible is True
        assert result.quarantine_required is False
        assert result.auction_mode == "sealed"
        assert result.map_table_build == HOTFIX_20260903_BIDMAP_BUILD
    for map_id in (5506, 6001, 1060):
        result = classify_from_map_table(
            map_id,
            maps,
            source_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            profile_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            game_version=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            owner_evidence_status="absent",
        )
        assert result.official_distribution_eligible is False
        assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE


@pytest.mark.parametrize(
    ("source_generation", "profile_generation", "game_version"),
    (
        (
            UNKNOWN_WAREHOUSE_GENERATION,
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            "e8d0e7-2026-09-03-753",
        ),
        (
            UNKNOWN_WAREHOUSE_GENERATION,
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION + "-extra",
        ),
        (
            OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        ),
        (
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        ),
    ),
)
def test_681_classification_quarantines_unknown_prefix_and_mixed_identity(
    source_generation: str,
    profile_generation: str,
    game_version: str,
) -> None:
    result = classify_from_map_table(
        5601,
        _current_maps(),
        source_generation=source_generation,
        profile_generation=profile_generation,
        game_version=game_version,
        owner_evidence_status="absent",
    )
    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.official_distribution_eligible is False
    assert result.source_conflict is True
    assert result.quarantine_required is True


def test_trim_selects_v6_without_rewriting_v5() -> None:
    source = classify_from_map_table(
        5601,
        _current_maps(),
        source_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        profile_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        game_version=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        owner_evidence_status="absent",
    )
    record = trim_artifact(
        {
            "phase": "settled",
            "session_id": "5601:681",
            "map_id": 5601,
            "model_map_id": 5601,
            "warehouse_source_generation": source.source_generation,
            "warehouse_source": source.as_dict(),
            "final_quality_counts": {"q6": 1},
            "known_value_sum": 100_000,
        }
    )
    assert record is not None
    assert record["warehouse_source_schema"] == WAREHOUSE_SOURCE_SCHEMA_HOTFIX_20260903
    assert record["warehouse_game_version"] == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    )


def test_681_category_authority_is_bound_to_current_item_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = monitor_module._settlement_category_authority(  # noqa: SLF001
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    )
    assert authority is not None
    assert authority.generation == (
        monitor_module.HOTFIX_20260903_CATEGORY_AUTHORITY_GENERATION
    )
    assert authority.identity == (
        monitor_module.HOTFIX_20260903_CATEGORY_AUTHORITY_IDENTITY
    )

    monkeypatch.setattr(
        monitor_module,
        "HOTFIX_20260903_CATEGORY_AUTHORITY_IDENTITY",
        "0" * 64,
    )
    assert monitor_module._settlement_category_authority(  # noqa: SLF001
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    ) is None


def test_upload_preflight_drift_is_sticky_and_keeps_fresh_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector_module()
    record = _v6_record(collector)
    versions = iter(
        (
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            "e8d0e7-2026-09-03-753",
        )
    )
    monkeypatch.setattr(
        "bidking_lab.live.version_check.detect_game_version_strict_disk",
        lambda: next(versions),
    )
    sample_upload._revalidate_exact_game_version_before_upload(record)  # noqa: SLF001
    assert record["warehouse_source_generation"] == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    )
    sample_upload._revalidate_exact_game_version_before_upload(record)  # noqa: SLF001
    assert record["game_version"] == "e8d0e7-2026-09-03-753"
    assert record["warehouse_game_version"] == "e8d0e7-2026-09-03-753"
    assert record["warehouse_source_generation"] == UNKNOWN_WAREHOUSE_GENERATION
    assert record["warehouse_quarantine_required"] is True
    assert record["official_distribution_eligible"] is False


def test_upload_thread_entry_rechecks_and_posts_only_fresh_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector_module()
    record = _v6_record(collector, session_hash="upload-thread-drift")
    versions = iter(
        (
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            "e8d0e7-2026-09-03-753",
        )
    )
    monkeypatch.setattr(
        "bidking_lab.live.version_check.detect_game_version_strict_disk",
        lambda: next(versions),
    )
    sample_upload._revalidate_exact_game_version_before_upload(record)  # noqa: SLF001

    posted: list[dict] = []
    monkeypatch.setattr(sample_upload, "_upload_authority_current", lambda epoch: True)
    monkeypatch.setattr(
        sample_upload,
        "_post",
        lambda url, key, payload, tic_skip=None: posted.append(dict(payload)),
    )
    sample_upload._post_if_authorized(0, "https://example.invalid", "", record)  # noqa: SLF001

    assert len(posted) == 1
    payload = posted[0]
    assert payload["game_version"] == "e8d0e7-2026-09-03-753"
    assert payload["warehouse_game_version"] == "e8d0e7-2026-09-03-753"
    assert payload["warehouse_source_generation"] == UNKNOWN_WAREHOUSE_GENERATION
    assert payload["warehouse_quarantine_required"] is True
    assert payload["official_distribution_eligible"] is False


def test_server_v6_accepts_current_routes_and_quarantines_wrong_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    for map_id in (5203, 5219, 5601, 5611):
        assert collector._accept_one(  # noqa: SLF001
            _v6_record(
                collector,
                map_id=map_id,
                session_hash=f"v6-{map_id}",
            )
        ) == "accepted"
    for map_id in (5506, 6001, 1060):
        assert collector._accept_one(  # noqa: SLF001
            _v6_record(
                collector,
                map_id=map_id,
                session_hash=f"v6-bad-{map_id}",
            )
        ) == "quarantined"

    old_schema = _v6_record(collector, session_hash="v5-borrow-681")
    old_schema["warehouse_source_schema"] = collector._PW2_OFFICIAL_20260903_SCHEMA
    assert collector._accept_one(old_schema) == "quarantined"  # noqa: SLF001
    wrong_game = _v6_record(collector, session_hash="v6-e8")
    wrong_game["game_version"] = "e8d0e7-2026-09-03-753"
    wrong_game["warehouse_game_version"] = "e8d0e7-2026-09-03-753"
    assert collector._accept_one(wrong_game) == "quarantined"  # noqa: SLF001
    prefixed = _v6_record(collector, session_hash="v6-prefixed")
    prefixed["game_version"] = HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION + "-extra"
    prefixed["warehouse_game_version"] = prefixed["game_version"]
    assert collector._accept_one(prefixed) == "quarantined"  # noqa: SLF001

    assert len(normal.read_text(encoding="utf-8").splitlines()) == 4
    quarantine = normal.with_name("samples_quarantine.jsonl")
    assert len(quarantine.read_text(encoding="utf-8").splitlines()) == 6


def test_server_v6_binds_category_identity_without_migrating_old_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    record = _v6_record(collector, session_hash="v6-category")
    record.update(
        {
            "physical_map_id": 5601,
            "model_map_id": 5601,
            "warehouse_generation": HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            "category_authority_generation": (
                collector._HOTFIX_20260903_CATEGORY_AUTHORITY_GENERATION
            ),
            "category_authority_identity": (
                collector._HOTFIX_20260903_CATEGORY_AUTHORITY_IDENTITY
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
        collector._HOTFIX_20260903_CATEGORY_AUTHORITY_IDENTITY
    )

    wrong = dict(record, session_hash="v6-category-wrong")
    wrong["category_authority_identity"] = "0" * 64
    assert collector._accept_one(wrong) == "accepted"  # noqa: SLF001
    stored_rows = [
        json.loads(line)
        for line in normal.read_text(encoding="utf-8").splitlines()
    ]
    assert "category_authority_identity" not in stored_rows[-1]
    assert "settled_items_v1" not in stored_rows[-1]
