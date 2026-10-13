from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "hero_ref" / "tools" / "network_connection_status.py"
    spec = importlib.util.spec_from_file_location("network_connection_status_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wifi_direct_fresh_websocket_error_is_game_failure_not_wss_failure() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=True,
        player_log_error_counts={"websocket_unable_to_connect": 3},
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={"phase": "helper_running"},
    )

    assert status["game_connection_state"] == "failed"
    assert status["wss_intercept_state"] == "ready"
    assert status["capture_decode_state"] == "idle"


def test_tcp_success_does_not_override_fresh_websocket_failure() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=True,
        independent_game_connected=False,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status={"source": "wss_local", "active_flows": 0},
        helper_status={"phase": "helper_running"},
    )

    assert status["game_connection_state"] == "failed"


def test_independent_lobby_evidence_marks_game_connected_without_global_direct_claim() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=True,
        independent_game_connected=True,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={"phase": "helper_running"},
    )

    assert status["game_connection_state"] == "connected"
    assert "clash_mode" not in status


def test_established_without_fresh_log_or_wss_data_stays_connecting() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=True,
        capture_status={
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
        },
        helper_status={"phase": "helper_running"},
    )

    assert status["game_connection_state"] == "connecting"
    assert status["wss_intercept_state"] == "ready"


def test_lobby_connected_with_explicit_helper_failure_is_local_intercept_failure() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=True,
        independent_game_connected=True,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={
            "phase": "exited_unexpectedly",
            "error_code": "wss_redirector_start_failed",
        },
    )

    assert status["game_connection_state"] == "connected"
    assert status["wss_intercept_state"] == "failed"


def test_helper_start_failure_is_local_intercept_failure_until_current_health() -> None:
    module = _module()
    helper = {
        "phase": "helper_start_failed",
        "launch_id": "current-launch",
        "error_code": "wss_capture_helper_start_failed",
    }

    failed = module.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status=helper,
    )
    recovered = module.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        capture_status={
            "source": "wss_local",
            "active_flows": 1,
            "raw_packets": 1,
            "accepted_frames": 1,
            "active_session_id": "current-session",
        },
        helper_status=helper,
    )

    assert failed["game_connection_state"] == "connecting"
    assert failed["wss_intercept_state"] == "failed"
    assert failed["capture_decode_state"] == "idle"
    assert failed["evidence"]["helper_phase"] == "helper_start_failed"
    assert (
        failed["evidence"]["helper_error_code"]
        == "wss_capture_helper_start_failed"
    )
    assert recovered["game_connection_state"] == "connected"
    assert recovered["wss_intercept_state"] == "attached"
    assert recovered["capture_decode_state"] == "recognized"


def test_stable_missing_redirector_is_local_failure_without_guessing_lobby() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        capture_status={
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
        },
        helper_status={
            "phase": "helper_running",
            "launch_id": "current-launch",
            "redirector_state": "missing_stable",
            "error_code": "wss_redirector_missing_stable",
        },
    )

    assert status["game_connection_state"] == "connecting"
    assert status["wss_intercept_state"] == "failed"
    assert status["capture_decode_state"] == "idle"
    assert (
        status["evidence"]["helper_error_code"]
        == "wss_redirector_missing_stable"
    )


def test_current_wss_flow_overrides_stable_missing_redirector_sidecar() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        capture_status={
            "source": "wss_local",
            "active_flows": 1,
            "raw_packets": 1,
            "accepted_frames": 1,
            "active_session_id": "current-session",
        },
        helper_status={
            "phase": "helper_running",
            "launch_id": "current-launch",
            "redirector_state": "missing_stable",
            "error_code": "wss_redirector_missing_stable",
        },
    )

    assert status["game_connection_state"] == "connected"
    assert status["wss_intercept_state"] == "attached"
    assert status["capture_decode_state"] == "recognized"


def test_old_launch_missing_redirector_does_not_taint_current_launch() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={
            "phase": "helper_running",
            "launch_id": "old-launch",
            "redirector_state": "missing_stable",
            "error_code": "wss_redirector_missing_stable",
        },
    )

    assert status["game_connection_state"] == "connecting"
    assert status["wss_intercept_state"] == "ready"
    assert status["evidence"]["helper_error_code"] == ""


def test_known_runtime_compatibility_failure_is_not_reported_as_game_failure() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        independent_game_connected=True,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={
            "phase": "exited_unexpectedly",
            "launch_id": "current-launch",
            "error_code": "wss_runtime_compatibility_failed",
        },
    )

    assert status["game_connection_state"] == "connected"
    assert status["wss_intercept_state"] == "failed"
    assert status["capture_decode_state"] == "idle"
    assert (
        status["evidence"]["helper_error_code"]
        == "wss_runtime_compatibility_failed"
    )


@pytest.mark.parametrize(
    ("capture_status", "expected_decode_state"),
    [
        (
            {
                "source": "wss_local",
                "active_flows": 1,
                "accepted_frames": 0,
            },
            "idle",
        ),
        (
            {
                "source": "wss_local",
                "raw_packets": 8,
                "accepted_frames": 0,
            },
            "unrecognized",
        ),
        (
            {
                "source": "wss_local",
                "accepted_frames": 3,
            },
            "recognized",
        ),
        (
            {
                "source": "wss_local",
                "accepted_frames": 0,
                "active_session_id": "current-session",
            },
            "recognized",
        ),
    ],
)
def test_current_wss_health_overrides_older_runtime_compatibility_failure(
    capture_status: dict[str, object],
    expected_decode_state: str,
) -> None:
    module = _module()

    status = module.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        capture_status=capture_status,
        helper_status={
            "phase": "exited_unexpectedly",
            "launch_id": "current-launch",
            "error_code": "wss_runtime_compatibility_failed",
            "runtime_compatibility_state": "failed_known_historical_driver",
        },
    )

    assert status["game_connection_state"] == "connected"
    assert status["wss_intercept_state"] == "attached"
    assert status["capture_decode_state"] == expected_decode_state


def test_stale_runtime_compatibility_failure_does_not_taint_current_launch() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={
            "phase": "exited_unexpectedly",
            "launch_id": "old-launch",
            "error_code": "wss_runtime_compatibility_failed",
        },
    )

    assert status["game_connection_state"] == "connecting"
    assert status["wss_intercept_state"] == "ready"
    assert status["evidence"]["helper_error_code"] == ""


def test_wss_data_with_zero_accepted_frames_is_unrecognized_not_network_failure() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=True,
        capture_status={
            "source": "wss_local",
            "active_flows": 1,
            "raw_packets": 8,
            "accepted_frames": 0,
        },
        helper_status={"phase": "helper_running"},
    )

    assert status["game_connection_state"] == "connected"
    assert status["wss_intercept_state"] == "attached"
    assert status["capture_decode_state"] == "unrecognized"


@pytest.mark.parametrize(
    ("capture_status", "expected_wss_state", "expected_decode_state"),
    [
        (
            {
                "source": "wss_local",
                "active_flows": 1,
                "accepted_frames": 0,
            },
            "attached",
            "idle",
        ),
        (
            {
                "source": "wss_local",
                "active_flows": 1,
                "raw_packets": 8,
                "accepted_frames": 0,
            },
            "attached",
            "unrecognized",
        ),
        (
            {
                "source": "wss_local",
                "accepted_frames": 3,
            },
            "attached",
            "recognized",
        ),
        (
            {
                "source": "wss_local",
                "accepted_frames": 0,
                "active_session_id": "recovered-session",
            },
            "attached",
            "recognized",
        ),
    ],
)
def test_current_wss_evidence_overrides_older_player_log_failure(
    capture_status: dict[str, object],
    expected_wss_state: str,
    expected_decode_state: str,
) -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=True,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=capture_status,
        helper_status={"phase": "helper_running"},
    )

    assert status["game_connection_state"] == "connected"
    assert status["wss_intercept_state"] == expected_wss_state
    assert status["capture_decode_state"] == expected_decode_state


def test_game_capture_liveness_epoch_drops_previous_process_recovery_evidence() -> None:
    module = _module()
    epoch = module.GameCaptureLivenessEpoch()
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    epoch.open_flow("flow-A")
    epoch.record_raw_frame("flow-A")
    epoch.record_accepted_frame("old-A")

    assert epoch.capture_status() == {
        "source": "wss_local",
        "active_flows": 1,
        "sniffed_packets": 1,
        "raw_packets": 1,
        "accepted_frames": 1,
        "ignored_frames": 0,
        "active_session_id": "old-A",
    }

    assert epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
    )
    new_process_capture = epoch.capture_status()
    failed = module.derive_network_connection_status(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=new_process_capture,
        helper_status={"phase": "helper_running"},
    )

    assert new_process_capture["active_flows"] == 0
    assert new_process_capture["raw_packets"] == 0
    assert new_process_capture["accepted_frames"] == 0
    assert new_process_capture["active_session_id"] == ""
    assert failed["game_connection_state"] == "failed"


def test_game_capture_liveness_epoch_recovers_only_after_new_process_data() -> None:
    module = _module()
    epoch = module.GameCaptureLivenessEpoch()
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    epoch.record_accepted_frame("old-A")
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
    )
    epoch.open_flow("flow-B")
    epoch.record_raw_frame("flow-B")

    receiving = module.derive_network_connection_status(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=epoch.capture_status(),
        helper_status={"phase": "helper_running"},
    )
    epoch.record_accepted_frame("session-B")
    recognized = module.derive_network_connection_status(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=epoch.capture_status(),
        helper_status={"phase": "helper_running"},
    )

    assert receiving["game_connection_state"] == "connected"
    assert receiving["capture_decode_state"] == "unrecognized"
    assert recognized["game_connection_state"] == "connected"
    assert recognized["capture_decode_state"] == "recognized"
    assert epoch.capture_status()["active_session_id"] == "session-B"


def test_game_capture_liveness_epoch_same_process_data_recovers_after_error() -> None:
    module = _module()
    epoch = module.GameCaptureLivenessEpoch()
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    failed = module.derive_network_connection_status(
        game_running=True,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=epoch.capture_status(),
        helper_status={"phase": "helper_running"},
    )
    epoch.open_flow("flow-A")
    epoch.record_raw_frame("flow-A")
    recovered = module.derive_network_connection_status(
        game_running=True,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=epoch.capture_status(),
        helper_status={"phase": "helper_running"},
    )

    assert failed["game_connection_state"] == "failed"
    assert recovered["game_connection_state"] == "connected"


def test_game_capture_liveness_epoch_late_create_time_does_not_reset() -> None:
    module = _module()
    epoch = module.GameCaptureLivenessEpoch()
    assert epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=None,
    )
    epoch.open_flow("flow-A")
    epoch.record_raw_frame("flow-A")
    epoch.record_accepted_frame("session-A")

    assert not epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    assert not epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=None,
    )
    assert epoch.capture_status()["accepted_frames"] == 1
    assert epoch.capture_status()["active_session_id"] == "session-A"


def test_game_capture_liveness_epoch_keeps_known_identity_unresolved_after_gap() -> None:
    module = _module()
    epoch = module.GameCaptureLivenessEpoch()
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    epoch.open_flow("flow-A")
    epoch.record_raw_frame("flow-A")
    epoch.record_accepted_frame("session-A")

    assert not epoch.observe_process(
        game_running=None,
        game_pid=None,
        game_started_at=None,
    )
    assert not epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=None,
    )
    epoch.open_flow("flow-B-unresolved")
    epoch.record_raw_frame("flow-B-unresolved")
    unresolved_capture = epoch.capture_status()
    unresolved = module.derive_network_connection_status(
        game_running=True,
        game_pid=42,
        game_started_at=None,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=unresolved_capture,
        helper_status={"phase": "helper_running"},
    )

    assert unresolved_capture["active_flows"] == 0
    assert unresolved_capture["raw_packets"] == 0
    assert unresolved_capture["accepted_frames"] == 0
    assert unresolved_capture["active_session_id"] == ""
    assert unresolved["game_connection_state"] == "failed"
    assert unresolved["wss_intercept_state"] == "ready"
    assert unresolved["capture_decode_state"] == "idle"


def test_game_capture_liveness_epoch_new_identity_after_gap_needs_own_data() -> None:
    module = _module()
    epoch = module.GameCaptureLivenessEpoch()
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    epoch.record_raw_frame("flow-A")
    epoch.record_accepted_frame("session-A")
    epoch.observe_process(
        game_running=None,
        game_pid=None,
        game_started_at=None,
    )
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=None,
    )

    assert epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
    )
    new_identity_capture = epoch.capture_status()
    failed = module.derive_network_connection_status(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=new_identity_capture,
        helper_status={"phase": "helper_running"},
    )
    epoch.open_flow("flow-B")
    epoch.record_raw_frame("flow-B")
    receiving = module.derive_network_connection_status(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
        player_log_error_counts={"websocket_unable_to_connect": 1},
        capture_status=epoch.capture_status(),
        helper_status={"phase": "helper_running"},
    )

    assert new_identity_capture["raw_packets"] == 0
    assert new_identity_capture["accepted_frames"] == 0
    assert new_identity_capture["active_session_id"] == ""
    assert failed["game_connection_state"] == "failed"
    assert receiving["game_connection_state"] == "connected"
    assert receiving["capture_decode_state"] == "unrecognized"


def test_game_capture_liveness_epoch_same_identity_gap_drops_stale_active_flow() -> None:
    module = _module()
    epoch = module.GameCaptureLivenessEpoch()
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    epoch.open_flow("flow-A")
    epoch.record_raw_frame("flow-A")
    epoch.record_accepted_frame("session-A")
    epoch.observe_process(
        game_running=None,
        game_pid=None,
        game_started_at=None,
    )
    epoch.close_flow("flow-A")

    assert not epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    recovered_capture = epoch.capture_status()

    assert recovered_capture["active_flows"] == 0
    assert recovered_capture["raw_packets"] == 1
    assert recovered_capture["accepted_frames"] == 1
    assert recovered_capture["active_session_id"] == "session-A"


def test_game_capture_liveness_epoch_exit_and_pid_change_still_clear() -> None:
    module = _module()
    epoch = module.GameCaptureLivenessEpoch()
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    epoch.record_accepted_frame("session-A")

    assert epoch.observe_process(
        game_running=False,
        game_pid=None,
        game_started_at=None,
    )
    assert epoch.capture_status()["accepted_frames"] == 0
    assert epoch.observe_process(
        game_running=True,
        game_pid=43,
        game_started_at=2000.0,
    )
    epoch.record_accepted_frame("session-B")
    assert epoch.observe_process(
        game_running=True,
        game_pid=44,
        game_started_at=2000.0,
    )
    assert epoch.capture_status()["accepted_frames"] == 0
    assert epoch.capture_status()["active_session_id"] == ""


def test_player_log_window_ignores_error_present_before_cold_start(tmp_path: Path) -> None:
    module = _module()
    player_log = tmp_path / "Player.log"
    player_log.write_text(
        "websocket error : Unable to connect to the remote server\n",
        encoding="utf-8",
    )
    window = module.PlayerLogConnectionWindow(player_log)
    player_log.write_text(
        player_log.read_text(encoding="utf-8") + "GameLogic:DoConnect()\n",
        encoding="utf-8",
    )

    counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )

    assert counts == {}


def test_player_log_window_counts_only_new_fixed_error_and_never_returns_text(
    tmp_path: Path,
) -> None:
    module = _module()
    player_log = tmp_path / "Player.log"
    player_log.write_text("historical private content\n", encoding="utf-8")
    window = module.PlayerLogConnectionWindow(player_log)
    with player_log.open("a", encoding="utf-8") as handle:
        handle.write(
            "websocket error : Unable to connect to the remote server "
            "PRIVATE_PLAYER PRIVATE_TOKEN\n"
        )

    counts = window.observe(
        game_running=True,
        game_pid=43,
        game_started_at=1001.0,
    )

    assert counts == {"websocket_unable_to_connect": 1}
    assert "PRIVATE" not in repr(counts)


def test_player_log_window_rebinds_same_pid_when_create_time_changes(
    tmp_path: Path,
) -> None:
    module = _module()
    player_log = tmp_path / "Player.log"
    player_log.write_text("monitor baseline\n", encoding="utf-8")
    window = module.PlayerLogConnectionWindow(player_log)
    with player_log.open("a", encoding="utf-8") as handle:
        handle.write("websocket error : Unable to connect to the remote server\n")

    first_counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    rebound_counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
    )
    with player_log.open("a", encoding="utf-8") as handle:
        handle.write("websocket error : Unable to connect to the remote server\n")
    new_process_counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
    )

    assert first_counts == {"websocket_unable_to_connect": 1}
    assert rebound_counts == {}
    assert window._game_started_at == 2000.0
    assert new_process_counts == {"websocket_unable_to_connect": 1}


def test_player_log_window_same_process_identity_accumulates(tmp_path: Path) -> None:
    module = _module()
    player_log = tmp_path / "Player.log"
    player_log.write_text("monitor baseline\n", encoding="utf-8")
    window = module.PlayerLogConnectionWindow(player_log)
    with player_log.open("a", encoding="utf-8") as handle:
        handle.write("websocket error : Unable to connect to the remote server\n")

    first_counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    with player_log.open("a", encoding="utf-8") as handle:
        handle.write("websocket error : Unable to connect to the remote server\n")
    second_counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )

    assert first_counts == {"websocket_unable_to_connect": 1}
    assert second_counts == {"websocket_unable_to_connect": 2}


def test_player_log_window_missing_create_time_does_not_reset_known_identity(
    tmp_path: Path,
) -> None:
    module = _module()
    player_log = tmp_path / "Player.log"
    player_log.write_text("monitor baseline\n", encoding="utf-8")
    window = module.PlayerLogConnectionWindow(player_log)
    with player_log.open("a", encoding="utf-8") as handle:
        handle.write("websocket error : Unable to connect to the remote server\n")
    window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    with player_log.open("a", encoding="utf-8") as handle:
        handle.write("websocket error : Unable to connect to the remote server\n")

    counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=None,
    )

    assert counts == {"websocket_unable_to_connect": 2}
    assert window._game_started_at == 1000.0


def test_player_log_window_late_create_time_binding_keeps_current_counts(
    tmp_path: Path,
) -> None:
    module = _module()
    player_log = tmp_path / "Player.log"
    player_log.write_text("monitor baseline\n", encoding="utf-8")
    window = module.PlayerLogConnectionWindow(player_log)
    with player_log.open("a", encoding="utf-8") as handle:
        handle.write("websocket error : Unable to connect to the remote server\n")

    unknown_start_counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=None,
    )
    known_start_counts = window.observe(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )

    assert unknown_start_counts == {"websocket_unable_to_connect": 1}
    assert known_start_counts == {"websocket_unable_to_connect": 1}
    assert window._game_started_at == 1000.0


def test_game_not_running_is_waiting_not_connection_failure() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=False,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={"phase": "helper_running"},
    )

    assert status["game_connection_state"] == "not_running"
    assert status["wss_intercept_state"] == "ready"


def test_unknown_process_query_and_zero_data_stays_unknown() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        game_running=None,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={"phase": "helper_running"},
    )

    assert status["game_connection_state"] == "unknown"
    assert status["capture_decode_state"] == "idle"


def test_stale_helper_launch_is_ignored() -> None:
    module = _module()

    status = module.derive_network_connection_status(
        launch_id="current",
        game_running=True,
        capture_status={"source": "wss_local", "accepted_frames": 0},
        helper_status={
            "launch_id": "old",
            "phase": "exited_unexpectedly",
            "error_code": "wss_redirector_start_failed",
        },
    )

    assert status["wss_intercept_state"] == "ready"


def test_diagnostic_script_keeps_multi_egress_and_clash_unknown_fail_safe() -> None:
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert 'Add-Line "  [提示] 多出口风险' in script
    assert "本诊断不猜测游戏实际出口" in script
    assert '$clashMode = "unknown"' in script
    assert "off_native/on_direct/on_proxy/unknown" in script
    assert "A running Clash/TUN stack" in script
