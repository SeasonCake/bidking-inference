from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from bidking_lab.live.warehouse_source import (
    D66AEE_PLAYER_WAREHOUSE_GENERATION,
    LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV,
    OFFICIAL_RANDOM_WAREHOUSE,
    PLAYER_CUSTOM_WAREHOUSE,
    PLAYER_WAREHOUSE_CONFIGS,
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
    PREUPDATE_OFFICIAL_GENERATION,
    RANKED_WAREHOUSE_MAP_PREFIXES,
    UNKNOWN_WAREHOUSE_GENERATION,
    UPDATE606_PLAYER_WAREHOUSE_GENERATION,
    WAREHOUSE_TABLE_CONFLICT,
    WAREHOUSE_TABLE_STALE,
    WAREHOUSE_TABLE_VERIFIED,
    classify_from_map_table,
    classify_warehouse_source,
    is_ranked_warehouse_map,
    player_warehouse_config,
    warehouse_map_name_table_sha256,
    warehouse_map_source_contract_sha256,
    warehouse_map_table_status,
    warehouse_generation_from_capture_time,
)


@pytest.mark.parametrize(
    ("map_id", "name"),
    (
        (2101, "未知快递"),
        (2201, " 未知仓库 "),
        (2401, "未知别墅"),
        (2501, "未知残骸"),
    ),
)
def test_unknown_prefix_is_not_a_source_discriminator(map_id: int, name: str) -> None:
    result = classify_warehouse_source(
        map_id,
        name,
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "postupdate_non_ranked_source_unresolved"
    assert result.official_distribution_eligible is False
    assert result.parser_replay_eligible is True
    assert result.quote_policy == "advisory_only_no_clamp"


def test_2601_without_protocol_source_remains_unknown() -> None:
    result = classify_warehouse_source(
        2601,
        "隐秘拍卖会",
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "postupdate_non_ranked_source_unresolved"
    assert result.official_distribution_eligible is False


@pytest.mark.parametrize(
    ("map_id", "name"),
    (
        (2301, "杂货集装箱"),
        (2409, "博物收藏家别墅"),
        (2102, "医院快递"),
    ),
)
def test_verified_normal_names_do_not_prove_official_source(
    map_id: int,
    name: str,
) -> None:
    result = classify_warehouse_source(
        map_id,
        name,
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "postupdate_non_ranked_source_unresolved"
    assert result.official_distribution_eligible is False
    assert result.config is None


@pytest.mark.parametrize(
    "table_status",
    (WAREHOUSE_TABLE_STALE, WAREHOUSE_TABLE_CONFLICT),
)
def test_table_status_is_diagnostic_but_nonranked_still_fails_closed(
    table_status: str,
) -> None:
    result = classify_warehouse_source(
        2409,
        "博物收藏家别墅",
        table_status=table_status,
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "postupdate_non_ranked_source_unresolved"
    assert result.official_distribution_eligible is False


def test_missing_map_fails_closed_but_missing_name_does_not_change_source_rule() -> None:
    missing_map = classify_from_map_table(
        9999,
        {},
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )
    missing_name = classify_warehouse_source(
        2409,
        "",
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert missing_map.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert missing_map.reason == "postupdate_non_ranked_source_unresolved"
    assert missing_map.table_status == "missing"
    assert missing_name.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert missing_name.reason == "postupdate_non_ranked_source_unresolved"


def test_explicit_official_source_is_not_overridden_by_map_name() -> None:
    result = classify_warehouse_source(
        2401,
        "未知别墅",
        explicit_source=OFFICIAL_RANDOM_WAREHOUSE,
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert result.qualification == OFFICIAL_RANDOM_WAREHOUSE
    assert result.reason == "explicit_official_random_source"
    assert result.official_distribution_eligible is True


def test_explicit_player_source_can_qualify_without_a_table() -> None:
    result = classify_from_map_table(
        2301,
        None,
        explicit_source=PLAYER_CUSTOM_WAREHOUSE,
    )

    assert result.qualification == PLAYER_CUSTOM_WAREHOUSE
    assert result.reason == "explicit_player_custom_source"
    assert result.config == PLAYER_WAREHOUSE_CONFIGS[2301]


def test_explicit_player_source_on_ranked_map_fails_closed() -> None:
    result = classify_warehouse_source(
        5601,
        "未知残骸",
        explicit_source=PLAYER_CUSTOM_WAREHOUSE,
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "protocol_ranked_source_conflict"
    assert result.official_distribution_eligible is False


@pytest.mark.parametrize("map_id", (5501, 5511, 5601, 5611))
def test_ranked_maps_remain_official_even_when_table_is_stale(map_id: int) -> None:
    result = classify_warehouse_source(
        map_id,
        "",
        table_status=WAREHOUSE_TABLE_STALE,
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert result.qualification == OFFICIAL_RANDOM_WAREHOUSE
    assert result.reason == "ranked_map_excludes_player_custom"
    assert result.official_distribution_eligible is True
    assert is_ranked_warehouse_map(map_id) is True


def test_ranked_map_prefix_contract_is_narrow() -> None:
    assert RANKED_WAREHOUSE_MAP_PREFIXES == frozenset({55, 56})
    assert is_ranked_warehouse_map(5401) is False
    assert is_ranked_warehouse_map(5701) is False
    assert is_ranked_warehouse_map(None) is False


@pytest.mark.parametrize(
    ("map_id", "name"),
    (
        (2101, "未知快递"),
        (2201, "未知仓库"),
        (2401, "未知别墅"),
        (2501, "未知残骸"),
        (2601, "隐秘拍卖会"),
    ),
)
def test_preupdate_same_name_samples_remain_official_random(
    map_id: int,
    name: str,
) -> None:
    result = classify_warehouse_source(
        map_id,
        name,
        source_generation=PREUPDATE_OFFICIAL_GENERATION,
    )

    assert result.qualification == OFFICIAL_RANDOM_WAREHOUSE
    assert result.reason == "preupdate_official_random_generation"
    assert result.official_distribution_eligible is True
    assert result.config is None


@pytest.mark.parametrize(
    ("capture_time", "expected"),
    (
        ("2026-07-19 23:59:59.999", PREUPDATE_OFFICIAL_GENERATION),
        ("2026-07-22T23:59:59+08:00", PREUPDATE_OFFICIAL_GENERATION),
        (
            "2026-07-23 00:00:00",
            LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        ),
        (
            "2026/08/12 23:59:59",
            LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        ),
        ("2026-08-13 00:00:00", UPDATE606_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-19 23:59:59", UPDATE606_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-20 00:00:00", D66AEE_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-26 23:59:59", D66AEE_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-27 00:00:00", D66AEE_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-27 11:59:59.999999", D66AEE_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-27T03:59:59.999999Z", D66AEE_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-27 12:00:00", POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-27T04:00:00Z", POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
        ("2026-08-27", UNKNOWN_WAREHOUSE_GENERATION),
        ("2026-08-28", POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
        ("", UNKNOWN_WAREHOUSE_GENERATION),
        ("not-a-time", UNKNOWN_WAREHOUSE_GENERATION),
    ),
)
def test_capture_time_selects_activity_generation(
    capture_time: str,
    expected: str,
) -> None:
    assert warehouse_generation_from_capture_time(capture_time) == expected


def test_unverified_generation_fails_closed_without_reclassifying_history() -> None:
    result = classify_warehouse_source(2501, "未知残骸")

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "warehouse_generation_unverified"
    assert result.official_distribution_eligible is False


def test_legacy_postupdate_generation_cannot_consume_update606_contract() -> None:
    result = classify_warehouse_source(
        2531,
        "未知残骸",
        source_generation=LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status="verified",
        owner_evidence_status="absent",
        auction_mode="open",
    )

    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "warehouse_generation_unverified"
    assert result.official_distribution_eligible is False


def test_six_creation_configs_keep_independent_fields_and_no_quote_clamp() -> None:
    assert {
        map_id: (
            config.item_count_cap,
            config.auction_fee,
            config.minimum_total_value,
        )
        for map_id, config in PLAYER_WAREHOUSE_CONFIGS.items()
    } == {
        2101: (15, 1_000, 20_000),
        2201: (20, 5_000, 150_000),
        2301: (20, 20_000, 300_000),
        2401: (25, 50_000, 500_000),
        2501: (30, 100_000, 1_000_000),
        2601: (40, 500_000, 5_000_000),
    }
    assert all(config.as_dict()["quote_clamp"] is False for config in PLAYER_WAREHOUSE_CONFIGS.values())
    assert all(
        config.auction_fee != config.minimum_total_value
        for config in PLAYER_WAREHOUSE_CONFIGS.values()
    )
    assert player_warehouse_config(2409) == PLAYER_WAREHOUSE_CONFIGS[2401]
    assert player_warehouse_config(2508) == PLAYER_WAREHOUSE_CONFIGS[2501]
    assert player_warehouse_config(5601) is None


def test_rollback_switch_restores_legacy_official_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV, raising=False)
    default = classify_warehouse_source(
        2501,
        "未知残骸",
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )
    monkeypatch.setenv(DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV, "1")
    rollback = classify_warehouse_source(
        2501,
        "未知残骸",
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )

    assert default.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert default.official_distribution_eligible is False
    assert rollback.qualification == OFFICIAL_RANDOM_WAREHOUSE
    assert rollback.official_distribution_eligible is True
    assert rollback.reason == "qualification_disabled_rollback"


def test_map_table_object_and_mapping_resolve_the_same_name() -> None:
    obj_result = classify_from_map_table(
        2401,
        {2401: SimpleNamespace(name="未知别墅")},
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
    )
    dict_result = classify_from_map_table(
        2401,
        {2401: {"name": "未知别墅"}},
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        table_status=WAREHOUSE_TABLE_VERIFIED,
    )

    assert obj_result.as_dict() == dict_result.as_dict()


def test_unapproved_map_name_table_fails_closed_with_identity() -> None:
    maps = {2401: SimpleNamespace(name="未知别墅")}
    semantic_sha256 = warehouse_map_name_table_sha256(maps)
    source_contract_sha256 = warehouse_map_source_contract_sha256(maps)

    assert warehouse_map_table_status(maps) == WAREHOUSE_TABLE_STALE
    assert (
        warehouse_map_table_status(
            maps,
            expected_sha256=source_contract_sha256,
        )
        == WAREHOUSE_TABLE_VERIFIED
    )
    result = classify_from_map_table(
        2401,
        maps,
        source_generation=POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )
    assert result.qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.reason == "postupdate_non_ranked_source_unresolved"
    assert result.table_status == WAREHOUSE_TABLE_STALE
    assert result.map_table_sha256 == semantic_sha256
    assert result.expected_map_table_sha256


def _collector_module():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server

    return sample_collector_server


def _collector_record(**overrides):
    record = {
        "map_id": 2501,
        "hero": "aisha",
        "known_value_sum": 500_000,
    }
    record.update(overrides)
    return record


def test_collector_does_not_reclassify_historical_legacy_rows() -> None:
    collector = _collector_module()
    historical_ts = datetime(
        2026,
        7,
        19,
        12,
        tzinfo=timezone.utc,
    ).timestamp()

    assert collector._is_abnormal(  # noqa: SLF001
        _collector_record(client_ts=historical_ts)
    ) is False


def test_collector_accepts_new_legacy_rows_as_mixed_source() -> None:
    collector = _collector_module()
    postupdate_ts = datetime(
        2026,
        7,
        23,
        12,
        tzinfo=timezone.utc,
    ).timestamp()

    assert collector._is_abnormal(  # noqa: SLF001
        _collector_record(client_ts=postupdate_ts)
    ) is False


def test_collector_uses_trusted_receive_time_when_client_time_is_missing() -> None:
    collector = _collector_module()
    historical_ts = datetime(
        2026,
        7,
        19,
        12,
        tzinfo=timezone.utc,
    ).timestamp()
    postupdate_ts = datetime(
        2026,
        7,
        23,
        12,
        tzinfo=timezone.utc,
    ).timestamp()

    assert collector._is_abnormal(  # noqa: SLF001
        _collector_record(received_ts=historical_ts)
    ) is False
    assert collector._is_abnormal(  # noqa: SLF001
        _collector_record(received_ts=postupdate_ts)
    ) is False


@pytest.mark.parametrize(
    "raw_client_ts",
    ("NaN", "-1e999", "0", "-1", "1" + ("0" * 1000)),
    ids=("nan", "negative-infinity", "zero", "negative", "oversized-integer"),
)
def test_collector_live_ingest_accepts_unusable_client_time_as_mixed(
    raw_client_ts: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector_module()
    postupdate_ts = datetime(
        2026,
        7,
        23,
        12,
        tzinfo=timezone.utc,
    ).timestamp()
    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", type(collector._MAP_COUNT)())
    monkeypatch.setattr(collector.time, "time", lambda: postupdate_ts)

    result = collector._accept_one(  # noqa: SLF001
        {
            "map_id": 2501,
            "hero": "aisha",
            "session_hash": f"postupdate-invalid-client-ts-{raw_client_ts}",
            "final_quality_counts": {"q1": 1},
            "known_value_sum": 500_000,
            "client_ts": json.loads(raw_client_ts),
        }
    )

    assert result == "accepted"
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["received_ts"] == postupdate_ts
    assert saved["warehouse_source_qualification"] == (
        POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    )
    assert saved["official_distribution_eligible"] is False


def test_collector_live_ingest_accepts_undated_legacy_upload_as_mixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector_module()
    postupdate_ts = datetime(
        2026,
        7,
        23,
        12,
        tzinfo=timezone.utc,
    ).timestamp()
    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", type(collector._MAP_COUNT)())
    monkeypatch.setattr(collector.time, "time", lambda: postupdate_ts)

    result = collector._accept_one(  # noqa: SLF001
        {
            "map_id": 2501,
            "hero": "aisha",
            "session_hash": "postupdate-undated-legacy",
            "final_quality_counts": {"q1": 1},
            "known_value_sum": 500_000,
        }
    )

    assert result == "accepted"
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["received_ts"] == postupdate_ts
    assert "client_ts" not in saved
    assert saved["warehouse_source_qualification"] == (
        POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    )
    assert saved["official_distribution_eligible"] is False


@pytest.mark.parametrize(
    ("qualification", "eligible", "expected"),
    (
        (OFFICIAL_RANDOM_WAREHOUSE, True, True),
        (PLAYER_CUSTOM_WAREHOUSE, False, True),
        (POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE, False, True),
    ),
)
def test_collector_rejects_partial_distribution_qualification(
    qualification: str,
    eligible: bool,
    expected: bool,
) -> None:
    collector = _collector_module()

    assert collector._is_abnormal(  # noqa: SLF001
        _collector_record(
            warehouse_source_qualification=qualification,
            official_distribution_eligible=eligible,
        )
    ) is expected


def test_collector_partial_source_is_rejected_under_legacy_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _collector_module()
    record = _collector_record(
        warehouse_source_qualification=PLAYER_CUSTOM_WAREHOUSE,
        official_distribution_eligible=False,
    )

    monkeypatch.delenv(DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV, raising=False)
    assert collector._is_abnormal(record) is True  # noqa: SLF001
    monkeypatch.setenv(DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV, "1")
    assert collector._is_abnormal(record) is True  # noqa: SLF001
