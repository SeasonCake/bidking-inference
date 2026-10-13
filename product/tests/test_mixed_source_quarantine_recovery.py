from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_recovery():
    path = ROOT / "scripts" / "recover_mixed_source_samples_from_quarantine.py"
    spec = importlib.util.spec_from_file_location(
        "mixed_source_quarantine_recovery", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row(session_hash: str, map_id: int = 5601, **overrides):
    row = {
        "map_id": map_id,
        "session_hash": session_hash,
        "final_quality_counts": "q1=1",
        "inventory_count": 1,
        "known_value_sum": 100,
        "client_ts": 1783771340.6,
        "warehouse_source_qualification": (
            "postupdate_unknown_warehouse_source"
        ),
        "official_distribution_eligible": False,
        "server_quarantine_reason": "warehouse_source_pending",
    }
    row.update(overrides)
    return row


def _legacy_live_row(session_hash: str, map_id: int = 5601) -> dict:
    """Live legacy input has no partial PW2 projection; the server normalizes it."""
    row = _row(session_hash, map_id=map_id)
    row.pop("warehouse_source_qualification")
    row.pop("official_distribution_eligible")
    row.pop("server_quarantine_reason")
    return row


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _reset_collector(collector, tmp_path: Path, monkeypatch) -> Path:
    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", type(collector._MAP_COUNT)())
    return out


def test_recovery_preserves_posix_owner_before_atomic_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = _load_recovery()
    calls: list[tuple] = []

    class _Stat:
        def __init__(self, uid: int, gid: int) -> None:
            self.st_uid = uid
            self.st_gid = gid

    class _Path:
        def __init__(self, uid: int, gid: int) -> None:
            self._stat = _Stat(uid, gid)

        def stat(self):
            return self._stat

    source = _Path(1000, 1000)
    destination = _Path(0, 0)
    monkeypatch.setattr(
        recovery.shutil,
        "copystat",
        lambda src, dst: calls.append(("copystat", src, dst)),
    )
    monkeypatch.setattr(
        recovery.os,
        "chown",
        lambda path, uid, gid, **kwargs: calls.append(
            ("chown", path, uid, gid, kwargs)
        ),
        raising=False,
    )

    recovery._copy_stat_and_owner(source, destination)

    assert calls == [
        ("copystat", source, destination),
        (
            "chown",
            destination,
            1000,
            1000,
            {"follow_symlinks": False},
        ),
    ]


def test_recovery_owner_preservation_failure_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = _load_recovery()

    class _Path:
        def __init__(self, uid: int) -> None:
            self.uid = uid

        def stat(self):
            return type("Stat", (), {"st_uid": self.uid, "st_gid": self.uid})()

    monkeypatch.setattr(recovery.shutil, "copystat", lambda *_args: None)

    def fail_chown(*_args, **_kwargs):
        raise PermissionError("owner preservation failed")

    monkeypatch.setattr(recovery.os, "chown", fail_chown, raising=False)

    with pytest.raises(PermissionError, match="owner preservation failed"):
        recovery._copy_stat_and_owner(_Path(1000), _Path(0))


def test_collector_accepts_fieldless_legacy_ranked_and_open_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server as collector

    out = _reset_collector(collector, tmp_path, monkeypatch)
    assert collector._accept_one(  # noqa: SLF001
        _legacy_live_row("ranked-live")
    ) == "accepted"
    assert collector._accept_one(  # noqa: SLF001
        _legacy_live_row("unknown-live", map_id=2501)
    ) == "accepted"

    saved = {row["session_hash"]: row for row in _read(out)}
    assert saved["ranked-live"]["warehouse_source_qualification"] == (
        "official_random_warehouse"
    )
    assert saved["ranked-live"]["official_distribution_eligible"] is True
    assert saved["unknown-live"]["warehouse_source_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert saved["unknown-live"]["official_distribution_eligible"] is False
    assert not out.with_name("samples_quarantine.jsonl").exists()


@pytest.mark.parametrize(
    ("map_id", "qualification", "eligible"),
    [
        (5601, "player_custom_warehouse", False),
        (2501, "official_random_warehouse", False),
        (2501, "postupdate_unknown_warehouse_source", True),
        (2501, "player_custom_warehouse", True),
        (2501, "unsupported", False),
    ],
)
def test_collector_keeps_source_conflicts_fail_closed(
    map_id: int,
    qualification: object,
    eligible: object,
) -> None:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server as collector

    assert collector._is_abnormal(  # noqa: SLF001
        _row(
            "source-conflict",
            map_id=map_id,
            warehouse_source_qualification=qualification,
            official_distribution_eligible=eligible,
        )
    )


def test_recovery_dry_run_and_apply_are_exact(tmp_path: Path) -> None:
    recovery = _load_recovery()
    main = tmp_path / "samples.jsonl"
    quarantine = tmp_path / "samples_quarantine.jsonl"
    _write(
        main,
        [
            _row(
                "already-main",
                warehouse_source_qualification="official_random_warehouse",
                official_distribution_eligible=True,
            )
        ],
    )
    _write(
        quarantine,
        [
            _row("ranked-good"),
            _row("already-main"),
            _row("unknown-good", map_id=2501),
            _row(
                "player-good",
                map_id=2501,
                warehouse_source_qualification="player_custom_warehouse",
            ),
            _row(
                "missing-good",
                map_id=2501,
                warehouse_source_qualification=None,
                official_distribution_eligible=None,
            ),
            _row("update606-unopened-old-reason", map_id=5506),
            _row("update606-unopened-other-route", map_id=6001),
            _row(
                "update606-unopened-current-reason",
                map_id=5506,
                server_quarantine_reason="update606_map_unopened",
            ),
            _row("incomplete", settlement_incomplete=True),
            _row("short", total_item_count=2),
            _row("invalid-value", known_value_sum=0),
            _row(
                "ranked-player",
                warehouse_source_qualification="player_custom_warehouse",
                official_distribution_eligible=False,
            ),
        ],
    )

    before_main = main.read_bytes()
    before_quarantine = quarantine.read_bytes()
    dry = recovery.recover(main, quarantine, apply=False, backup_dir=None)
    assert dry["counts"]["official_promoted"] == 1
    assert dry["counts"]["mixed_promoted"] == 3
    assert dry["counts"]["duplicate_removed"] == 1
    assert dry["counts"]["retained_update606_map_unopened"] == 3
    assert dry["counts"]["retained_total"] == 7
    assert main.read_bytes() == before_main
    assert quarantine.read_bytes() == before_quarantine

    backup = tmp_path / "backup"
    applied = recovery.recover(
        main,
        quarantine,
        apply=True,
        backup_dir=backup,
    )
    assert applied["counts"]["main_rows_after"] == 5
    assert applied["counts"]["quarantine_rows_after"] == 7
    assert (backup / main.name).read_bytes() == before_main
    assert (backup / quarantine.name).read_bytes() == before_quarantine

    main_rows = {row["session_hash"]: row for row in _read(main)}
    assert main_rows["ranked-good"]["warehouse_source_qualification"] == (
        "official_random_warehouse"
    )
    assert main_rows["ranked-good"]["official_distribution_eligible"] is True
    assert main_rows["unknown-good"]["official_distribution_eligible"] is False
    assert main_rows["player-good"]["warehouse_source_qualification"] == (
        "player_custom_warehouse"
    )
    assert main_rows["missing-good"]["warehouse_source_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert {
        row["session_hash"] for row in _read(quarantine)
    } == {
        "update606-unopened-old-reason",
        "update606-unopened-other-route",
        "update606-unopened-current-reason",
        "incomplete",
        "short",
        "invalid-value",
        "ranked-player",
    }


def test_recovery_unopened_map_authority_matches_collector() -> None:
    recovery = _load_recovery()
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server as collector

    assert recovery.UPDATE606_UNOPENED_MAP_IDS == (
        collector._UPDATE606_UNOPENED_MAP_IDS  # noqa: SLF001
    )


def _old_update606_missing_map_row(
    recovery,
    policy,
    session_hash: str,
    *,
    map_id: int,
) -> dict:
    row = _row(
        session_hash,
        map_id=map_id,
        warehouse_source_qualification=recovery.UNKNOWN_SOURCE,
        official_distribution_eligible=False,
    )
    row.update(
        {
            "received_ts": policy.collector._PW2_SERVER_POSTUPDATE_EPOCH + 1,
            "warehouse_source_schema": recovery._OLD_PW2_SCHEMA,
            "warehouse_map_name": "",
            "warehouse_map_name_source": "bid_map_table",
            "warehouse_table_status": "missing",
            "warehouse_source_reason": "pw2_map_contract_untrusted",
            "warehouse_source_generation": recovery._OLD_PW2_GENERATION,
            "warehouse_map_table_build": recovery._OLD_PW2_MAP_BUILD,
            "warehouse_map_table_sha256": recovery._OLD_PW2_MAP_SHA256,
            "warehouse_expected_map_table_sha256": (
                recovery._OLD_PW2_MAP_SHA256
            ),
            "warehouse_map_source_contract_sha256": (
                recovery._OLD_PW2_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_expected_map_source_contract_sha256": (
                recovery._OLD_PW2_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_auction_mode": "",
            "warehouse_owner_evidence_status": "absent",
            "warehouse_source_conflict": True,
            "warehouse_quarantine_required": True,
        }
    )
    return row


def _d66aee_false_stale_row(
    policy,
    session_hash: str,
    *,
    map_id: int,
    auction_mode: str,
    owner_status: str = "absent",
) -> dict:
    collector = policy.collector
    row = _row(
        session_hash,
        map_id=map_id,
        warehouse_source_qualification="postupdate_unknown_warehouse_source",
        official_distribution_eligible=False,
    )
    row.update(
        {
            "received_ts": collector._PW2_SERVER_POSTUPDATE_EPOCH + 1,
            "warehouse_source_schema": collector._PW2_LEGACY_FULL_MAP_SCHEMA,
            "warehouse_map_name": f"trusted-map-{map_id}",
            "warehouse_map_name_source": "bid_map_table",
            "warehouse_table_status": "stale",
            "warehouse_source_reason": "pw2_map_contract_untrusted",
            "warehouse_source_generation": (
                collector._UPDATE606_PLAYER_WAREHOUSE_GENERATION
            ),
            "warehouse_map_table_build": collector._PW2_LEGACY_FULL_MAP_BUILD,
            "warehouse_map_table_sha256": (
                collector._PW2_TRUSTED_MAP_TABLE_SHA256
            ),
            "warehouse_expected_map_table_sha256": (
                collector._PW2_LEGACY_FULL_MAP_EXPECTED_SHA256
            ),
            "warehouse_map_source_contract_sha256": (
                collector._PW2_TRUSTED_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_expected_map_source_contract_sha256": (
                collector._PW2_LEGACY_FULL_MAP_EXPECTED_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_auction_mode": auction_mode,
            "warehouse_owner_evidence_status": owner_status,
            "warehouse_source_conflict": True,
            "warehouse_quarantine_required": True,
        }
    )
    return row


@pytest.mark.parametrize(
    (
        "map_id",
        "auction_mode",
        "owner_status",
        "qualification",
        "eligible",
        "conflict",
    ),
    (
        (
            2408,
            "open",
            "absent",
            "postupdate_unknown_warehouse_source",
            False,
            False,
        ),
        (2408, "open", "valid", "player_custom_warehouse", False, False),
        (4501, "sealed", "absent", "official_random_warehouse", True, False),
        (
            4501,
            "sealed",
            "valid",
            "postupdate_unknown_warehouse_source",
            False,
            True,
        ),
    ),
)
def test_current_policy_reprojects_exact_d66aee_false_stale_rows(
    map_id: int,
    auction_mode: str,
    owner_status: str,
    qualification: str,
    eligible: bool,
    conflict: bool,
) -> None:
    recovery = _load_recovery()
    policy = recovery.load_update606_policy(ROOT / "scripts")
    row = _d66aee_false_stale_row(
        policy,
        f"d66aee-{map_id}-{owner_status}",
        map_id=map_id,
        auction_mode=auction_mode,
        owner_status=owner_status,
    )

    label, prepared = policy.prepare(row)

    if conflict:
        assert label == "collector_warehouse_source_pending"
        assert prepared is None
        return
    assert label == "d66aee_false_stale_reprojected"
    assert prepared is not None
    assert policy.collector._pw2_server_contract_valid(prepared) is True
    assert policy.collector._quarantine_reason(prepared) is None
    assert prepared["warehouse_source_schema"] == policy.collector._PW2_SCHEMA
    assert prepared["warehouse_source_generation"] == (
        policy.collector._POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
    )
    assert prepared["warehouse_source_qualification"] == qualification
    assert prepared["official_distribution_eligible"] is eligible
    assert prepared["warehouse_source_conflict"] is False
    assert prepared["warehouse_quarantine_required"] is False
    assert prepared["server_recovery_reason"] == (
        "d66aee_legacy_full_map_contract_reprojection"
    )
    assert row["warehouse_table_status"] == "stale"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("warehouse_map_table_sha256", "0" * 64),
        ("warehouse_expected_map_table_sha256", "0" * 64),
        ("warehouse_map_source_contract_sha256", "0" * 64),
        ("warehouse_table_status", "verified"),
        ("warehouse_auction_mode", "sealed"),
        ("warehouse_owner_evidence_status", "unknown"),
        ("received_ts", 0),
    ),
)
def test_current_policy_retains_d66aee_false_stale_near_matches(
    field: str,
    value: object,
) -> None:
    recovery = _load_recovery()
    policy = recovery.load_update606_policy(ROOT / "scripts")
    row = _d66aee_false_stale_row(
        policy,
        f"d66aee-near-{field}",
        map_id=2408,
        auction_mode="open",
    )
    row[field] = value

    label, prepared = policy.prepare(row)

    assert label == "collector_warehouse_source_pending"
    assert prepared is None


@pytest.mark.parametrize(
    ("map_id", "qualification", "eligible", "auction_mode"),
    [
        (2531, "postupdate_unknown_warehouse_source", False, "open"),
        (4531, "official_random_warehouse", True, "sealed"),
        (5611, "official_random_warehouse", True, "sealed"),
    ],
)
def test_update606_policy_reprojects_exact_old_missing_map_rows(
    map_id: int,
    qualification: str,
    eligible: bool,
    auction_mode: str,
) -> None:
    recovery = _load_recovery()
    policy = recovery.load_update606_policy(ROOT / "scripts")
    row = _old_update606_missing_map_row(
        recovery,
        policy,
        f"old-{map_id}",
        map_id=map_id,
    )

    label, prepared = policy.prepare(row)

    assert label == "update606_legacy_reprojected"
    assert prepared is not None
    assert policy.collector._pw2_server_contract_valid(prepared) is True
    assert policy.collector._quarantine_reason(prepared) is None
    assert prepared["warehouse_source_schema"] == policy.collector._PW2_SCHEMA
    assert prepared["warehouse_source_generation"] == (
        policy.collector._POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
    )
    assert prepared["warehouse_source_qualification"] == qualification
    assert prepared["official_distribution_eligible"] is eligible
    assert prepared["warehouse_auction_mode"] == auction_mode
    assert prepared["warehouse_source_conflict"] is False
    assert prepared["warehouse_quarantine_required"] is False
    assert prepared["warehouse_map_name"]
    assert prepared["server_recovery_reason"] == (
        "update606_legacy_map_authority_reprojection"
    )
    assert row["warehouse_map_name"] == ""
    assert row["warehouse_source_conflict"] is True


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ({"map_id": 5506}, "collector_update606_map_unopened"),
        (
            {"warehouse_map_table_sha256": "0" * 64},
            "collector_warehouse_source_pending",
        ),
        (
            {"warehouse_owner_evidence_status": "malformed_conflict"},
            "collector_warehouse_source_pending",
        ),
        ({"received_ts": 0}, "collector_warehouse_source_pending"),
        (
            {"warehouse_source_schema": "player_warehouse_source_pw2_update606_v2"},
            "collector_warehouse_source_pending",
        ),
        ({"received_ts": 10**400}, "collector_warehouse_source_pending"),
    ],
)
def test_update606_policy_keeps_unverified_historical_rows_quarantined(
    mutation: dict,
    expected_reason: str,
) -> None:
    recovery = _load_recovery()
    policy = recovery.load_update606_policy(ROOT / "scripts")
    row = _old_update606_missing_map_row(
        recovery,
        policy,
        "blocked",
        map_id=2531,
    )
    row.update(mutation)

    label, prepared = policy.prepare(row)

    assert label == expected_reason
    assert prepared is None


def test_update606_policy_dry_run_and_apply_only_promote_current_valid_rows(
    tmp_path: Path,
) -> None:
    recovery = _load_recovery()
    policy = recovery.load_update606_policy(ROOT / "scripts")
    main = tmp_path / "samples.jsonl"
    quarantine = tmp_path / "samples_quarantine.jsonl"
    _write(main, [_row("already-main")])

    historical = [
        _old_update606_missing_map_row(
            recovery,
            policy,
            f"historical-{map_id}",
            map_id=map_id,
        )
        for map_id in (2531, 4531, 5611)
    ]
    _label, current_valid = policy.prepare(historical[0])
    assert current_valid is not None
    current_valid = dict(current_valid)
    current_valid["session_hash"] = "current-valid"
    current_valid.pop("server_recovery_reason")
    current_valid.pop("server_recovery_source_generation")
    current_valid["server_quarantine_reason"] = "warehouse_source_pending"
    invalid_hash = _old_update606_missing_map_row(
        recovery,
        policy,
        "invalid-hash",
        map_id=2531,
    )
    invalid_hash["warehouse_map_table_sha256"] = "0" * 64
    d66aee_false_stale = _d66aee_false_stale_row(
        policy,
        "d66aee-false-stale",
        map_id=2408,
        auction_mode="open",
    )
    _write(
        quarantine,
        [
            *historical,
            current_valid,
            d66aee_false_stale,
            _old_update606_missing_map_row(
                recovery,
                policy,
                "unopened",
                map_id=5506,
            ),
            invalid_hash,
            _row("incomplete", settlement_incomplete=True),
        ],
    )
    before_main = main.read_bytes()
    before_quarantine = quarantine.read_bytes()

    dry = recovery.recover(
        main,
        quarantine,
        apply=False,
        backup_dir=None,
        policy=policy,
    )

    assert dry["counts"]["policy_update606_legacy_reprojected"] == 3
    assert dry["counts"]["policy_d66aee_false_stale_reprojected"] == 1
    assert dry["counts"]["policy_current_collector_accept"] == 1
    assert dry["counts"]["promoted_total"] == 5
    assert dry["counts"]["retained_total"] == 3
    assert main.read_bytes() == before_main
    assert quarantine.read_bytes() == before_quarantine

    backup = tmp_path / "backup"
    applied = recovery.recover(
        main,
        quarantine,
        apply=True,
        backup_dir=backup,
        policy=policy,
    )

    assert applied["counts"]["promoted_total"] == 5
    assert (backup / main.name).read_bytes() == before_main
    assert (backup / quarantine.name).read_bytes() == before_quarantine
    promoted = {
        row["session_hash"]: row
        for row in _read(main)
        if row["session_hash"] != "already-main"
    }
    assert set(promoted) == {
        "historical-2531",
        "historical-4531",
        "historical-5611",
        "current-valid",
        "d66aee-false-stale",
    }
    assert all(
        policy.collector._pw2_server_contract_valid(row) is True
        and policy.collector._quarantine_reason(row) is None
        and "server_quarantine_reason" not in row
        for row in promoted.values()
    )
    assert all(
        promoted[f"historical-{map_id}"]["server_recovery_reason"]
        == "update606_legacy_map_authority_reprojection"
        for map_id in (2531, 4531, 5611)
    )
    assert {
        row["session_hash"] for row in _read(quarantine)
    } == {"unopened", "invalid-hash", "incomplete"}


def test_public_and_private_map_surfaces_annotate_mixed_source() -> None:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import public_stats
    import sample_dashboard

    official = _row(
        "official",
        map_id=5601,
        warehouse_source_qualification="official_random_warehouse",
        official_distribution_eligible=True,
        server_quarantine_reason=None,
    )
    unknown = _row(
        "unknown",
        map_id=2501,
        server_quarantine_reason=None,
    )
    player = _row(
        "player",
        map_id=2501,
        warehouse_source_qualification="player_custom_warehouse",
        server_quarantine_reason=None,
    )
    aggregate = public_stats.aggregate_public([official, unknown, player])
    by_map = {row["map_id"]: row for row in aggregate["maps"]}
    assert by_map[5601]["mixed_source_n"] == 0
    assert by_map[2501]["n"] == 2
    assert aggregate["maps_by_source"]["unknown"][0]["map_id"] == 2501
    assert aggregate["maps_by_source"]["player_custom"][0]["map_id"] == 2501
    assert aggregate["totals"]["samples"] == 3
    assert aggregate["totals"]["received_samples"] == 3
    assert aggregate["totals"]["mixed_source_samples"] == 2
    public_html = public_stats.build_public_html(aggregate, generated_at=0)
    assert '<option value="player_custom">玩家仓</option>' in public_html
    assert "默认展示<b>主统计（已接纳）</b>" in public_html
    assert "来源冲突与服务端 quarantine 永不纳入" in public_html
    assert "玩法、明拍/暗拍、活动批次、来源" in public_html

    rows = sample_dashboard._rows_for_client(  # noqa: SLF001
        [official, unknown, player]
    )
    assert [row["source_mixed"] for row in rows] == [False, True, True]
    private_html = sample_dashboard.build_dashboard_html(
        [official, unknown, player]
    )
    assert "含玩家仓/来源混合" in private_html
    assert "不代表纯官方随机仓掉率" in private_html
