from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansObservedItem,
    FatbeansSkillReveal,
    FatbeansStateEvent,
)
from bidking_lab.live.monitor import (
    MonitorTables,
    _fresh_session_game_version,
    build_monitor_artifact_from_events,
    load_monitor_tables,
)
from bidking_lab.live.version_check import (
    detect_game_version_strict_disk,
    detect_game_version_strict_disk_snapshot,
)
from bidking_lab.live.warehouse_source import (
    HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    UNKNOWN_WAREHOUSE_GENERATION,
)


def _events(
    session_id: str | None = "5601:90000000000000058",
    *,
    map_id: int = 5601,
) -> FatbeansCaptureEvents:
    reveal = FatbeansSkillReveal(
        skill_id=1003031,
        hero_id=303,
        round_index=None,
        observed_items=(
            FatbeansObservedItem(
                local_index=1,
                runtime_id=10,
                item_id=None,
                quality=None,
                value=None,
                shape_code=11,
                cells=1,
            ),
        ),
    )
    state = FatbeansStateEvent(
        sort_id=1,
        capture_time="2026-09-03T16:46:00+08:00",
        message_id=0x21,
        session_id=session_id,
        map_id=map_id,
        round_index=1,
        skill_reveals=(reveal,),
    )
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        states=(state,),
        statuses=(),
    )


def _live_tables(
    version_path: Path,
    *,
    package_generation: str,
) -> MonitorTables:
    loaded = load_monitor_tables()
    return MonitorTables(
        maps=loaded.maps,
        drops=loaded.drops,
        items=loaded.items,
        rankmap_distribution_status="ok",
        rankmap_distribution_receipt={"status": "current_rankmap_algorithm_prior"},
        warehouse_package_generation=package_generation,
        warehouse_game_version=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        warehouse_game_version_path=str(version_path),
        warehouse_live_identity_check=True,
    )


def test_strict_disk_snapshot_is_stable_and_ignores_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    version_path = tmp_path / "DefaultPackage.version"
    version_path.write_text(
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    monkeypatch.setenv("BIDKING_GAME_VERSION", "forged-env-generation")
    snapshot = detect_game_version_strict_disk_snapshot(version_path)
    assert snapshot is not None
    assert snapshot.path == version_path.resolve()
    assert snapshot.value == HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    assert detect_game_version_strict_disk(version_path) == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    )


def test_preload_eb1_then_disk_681_new_session_blocks_rankmap_and_pw2(
    tmp_path: Path,
) -> None:
    version_path = tmp_path / "DefaultPackage.version"
    version_path.write_text(
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    tables = _live_tables(
        version_path,
        package_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    )
    # Steam replaces the generation after monitor load but before the game's
    # first semantic session frame.
    version_path.write_text(
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    artifact = build_monitor_artifact_from_events(
        _events(),
        tables=tables,
        n_trials=0,
        roi_trials=0,
        shadow_trials=0,
        run_debug_shadows=False,
    )
    assert artifact["warehouse_game_version_session_stable"] is True
    assert artifact["warehouse_source_generation"] == UNKNOWN_WAREHOUSE_GENERATION
    assert artifact["warehouse_source"]["game_version"] == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    )
    assert artifact["warehouse_source"]["quarantine_required"] is True
    assert artifact["formal_mode_reason"] == "game_generation_unverified"
    assert artifact["inference_input_constraints"] == {
        "mode": "game_generation_unverified",
        "map_id": 5601,
        "package_generation": OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        "observed_game_version": HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        "session_game_version_stable": True,
    }
    assert artifact["bid_rows"] == []


@pytest.mark.parametrize("map_id", (2401, 2501))
def test_preload_eb1_then_disk_681_blocks_non_rankmap_inference(
    tmp_path: Path,
    map_id: int,
) -> None:
    version_path = tmp_path / "DefaultPackage.version"
    version_path.write_text(
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    tables = _live_tables(
        version_path,
        package_generation=OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    )
    version_path.write_text(
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )

    artifact = build_monitor_artifact_from_events(
        _events(f"{map_id}:post-update", map_id=map_id),
        tables=tables,
        n_trials=0,
        roi_trials=0,
        shadow_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["warehouse_source_generation"] == UNKNOWN_WAREHOUSE_GENERATION
    assert artifact["formal_mode_reason"] == "game_generation_unverified"
    assert artifact["inference_input_constraints"] == {
        "mode": "game_generation_unverified",
        "map_id": map_id,
        "package_generation": OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        "observed_game_version": HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        "session_game_version_stable": True,
    }
    assert artifact["map_rows"] == []
    assert artifact["bid_rows"] == []


def test_same_session_disk_drift_is_sticky_and_blocks_rankmap(
    tmp_path: Path,
) -> None:
    version_path = tmp_path / "DefaultPackage.version"
    version_path.write_text(
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    tables = _live_tables(
        version_path,
        package_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    )
    session_id = "5601:90000000000000058"
    assert _fresh_session_game_version(tables, session_id) == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        True,
    )
    version_path.write_text("e8d0e7-2026-09-03-753", encoding="utf-8")
    artifact = build_monitor_artifact_from_events(
        _events(session_id),
        tables=tables,
        n_trials=0,
        roi_trials=0,
        shadow_trials=0,
        run_debug_shadows=False,
    )
    assert artifact["warehouse_game_version_session_stable"] is False
    assert artifact["warehouse_source_generation"] == UNKNOWN_WAREHOUSE_GENERATION
    assert artifact["warehouse_source"]["game_version"] == "e8d0e7-2026-09-03-753"
    assert artifact["inference_input_constraints"]["mode"] == (
        "game_generation_unverified"
    )
    assert artifact["bid_rows"] == []

    # Returning the disk leaf to its first value cannot rehabilitate a session
    # that already crossed a generation boundary.
    version_path.write_text(
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    assert _fresh_session_game_version(tables, session_id) == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        False,
    )


def test_live_frame_without_confirmed_session_is_fail_closed(
    tmp_path: Path,
) -> None:
    version_path = tmp_path / "DefaultPackage.version"
    version_path.write_text(
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    tables = _live_tables(
        version_path,
        package_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    )

    artifact = build_monitor_artifact_from_events(
        _events(None),
        tables=tables,
        n_trials=0,
        roi_trials=0,
        shadow_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["warehouse_game_version_session_stable"] is False
    assert artifact["warehouse_source_generation"] == UNKNOWN_WAREHOUSE_GENERATION
    assert artifact["warehouse_source"]["game_version"] == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    )
    assert artifact["warehouse_source"]["quarantine_required"] is True
    assert artifact["bid_rows"] == []


def test_conflicted_session_tombstone_survives_many_later_sessions(
    tmp_path: Path,
) -> None:
    version_path = tmp_path / "DefaultPackage.version"
    version_path.write_text(
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    tables = _live_tables(
        version_path,
        package_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    )
    conflicted = "5601:conflicted"
    assert _fresh_session_game_version(tables, conflicted)[1] is True
    version_path.write_text("e8d0e7-2026-09-03-753", encoding="utf-8")
    assert _fresh_session_game_version(tables, conflicted)[1] is False

    for index in range(160):
        _fresh_session_game_version(tables, f"5601:later-{index}")

    version_path.write_text(
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        encoding="utf-8",
    )
    assert _fresh_session_game_version(tables, conflicted) == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        False,
    )


def test_offline_explicit_tables_keep_static_compatibility() -> None:
    tables = replace(
        load_monitor_tables(),
        warehouse_live_identity_check=False,
        warehouse_game_version=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        warehouse_package_generation=HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    )
    assert _fresh_session_game_version(tables, "5601:offline") == (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        True,
    )
