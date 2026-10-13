"""Main-path preload failure controls; no helper process or SCM operation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_wss_live_monitor as monitor  # noqa: E402


class HelperCreationReached(BaseException):
    """Stop before subprocess creation even inside an Exception handler."""


def _install_stubs(monkeypatch, tmp_path, *, state, phase="verify_product",
                   stop_raises=False, snapshot_close_raises=False):
    events = []
    log_dir = tmp_path / "logs"
    addon = tmp_path / "addon.py"
    addon.write_bytes(b"stub")
    (tmp_path / "BUILD_MANIFEST.txt").write_bytes(b"stub")
    launch_id = "10000000-0000-4000-8000-000000000001"

    class Snapshot:
        package_generation_identity = "a" * 64

        def close(self):
            events.append("snapshot.close")
            if snapshot_close_raises:
                raise OSError("synthetic private-path token")

        def assert_unchanged(self):
            events.append("snapshot.check")

    class FakeMonitor:
        def start(self):
            events.append("monitor.start")

        def stop(self):
            events.append("monitor.stop")
            if stop_raises:
                raise OSError("synthetic private-path token")

    class Preloader:
        def __init__(self):
            self.state = state
            self.phase = phase

        def prepare(self, _driver):
            return self.state

        def release(self):
            events.append("preloader.release")

    preloader = Preloader()
    monkeypatch.setattr(sys, "argv", [
        "stub_monitor", "--log-dir", str(log_dir), "--addon", str(addon),
        "--confdir", str(tmp_path / "ca"), "--no-lock",
        "--lifecycle-launch-id", launch_id,
        "--internet-cafe-runtime-identity", "b" * 64,
        "--internet-cafe-component-identity", "c" * 64,
        "--internet-cafe-owner-sid", "S-1-5-21-1-2-3-4",
    ])
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "resolve_mitmdump", lambda *_a, **_k: tmp_path / "mitmdump.exe")
    monkeypatch.setattr(monitor, "ca_is_trusted_for_current_user", lambda *_a: True)
    monkeypatch.setattr(monitor, "_game_process_ids", lambda *_a: ())
    monkeypatch.setattr(monitor, "_validated_wss_operational_snapshot", lambda **_k: (Snapshot(), {}))
    monkeypatch.setattr(monitor, "load_monitor_tables", lambda: object())
    monkeypatch.setattr(monitor, "FatbeansWebhookMonitor", lambda **_k: FakeMonitor())
    monkeypatch.setattr(monitor, "_start_version_and_activation_threads", lambda *_a: None)
    monkeypatch.setattr(monitor, "build_mitmdump_command", lambda **_k: ["never-run"])
    monkeypatch.setattr(monitor, "WinDivertCompatibilityPreloader", lambda: preloader)
    monkeypatch.setattr(monitor, "_bundled_windivert_driver", lambda *_a: tmp_path / "driver.sys")
    monkeypatch.setattr(monitor, "_attempt_automatic_stale_product_service_repair",
                        lambda *_a, **_k: monitor._WinDivertAutoRepairOutcome(preload_state=state))
    monkeypatch.setattr(monitor, "_windows_elevation_state", lambda: "elevated")

    def child_spy(*_a, **_k):
        events.append("helper.create")
        raise HelperCreationReached()

    monkeypatch.setattr(monitor.subprocess, "Popen", child_spy)
    return events, log_dir, launch_id


@pytest.mark.parametrize("state,error_code", [
    ("failed_service_conflict", "wss_stale_product_service_conflict"),
    ("failed_same_path_mixed_generation", "wss_same_path_mixed_generation"),
])
def test_terminal_verify_product_does_not_create_helper(monkeypatch, tmp_path, state,
                                                       error_code, capsys):
    events, log_dir, launch_id = _install_stubs(monkeypatch, tmp_path, state=state)
    assert monitor.main() == 6
    assert events == ["monitor.start", "monitor.stop", "snapshot.close"]
    payload = json.loads((log_dir / monitor.WSS_HELPER_STATUS_FILENAME).read_bytes())
    assert payload["phase"] == "helper_start_failed" and payload["helper_pid"] == 0
    assert payload["launch_id"] == launch_id and payload["monitor_epoch"] == launch_id
    assert payload["package_generation_identity"] == "a" * 64
    assert payload["runtime_identity"] == "b" * 64
    assert payload["component_identity"] == "c" * 64
    assert payload["owner_sid"] == "S-1-5-21-1-2-3-4"
    assert payload["monitor_started_at_unix_ms"] > 0
    assert payload["windivert_preload_state"] == state
    assert payload["windivert_preload_phase"] == "verify_product"
    assert payload["error_code"] == error_code
    captured = capsys.readouterr()
    assert captured.err.strip() == f"[error] {error_code}"
    assert "capture ready" not in captured.out


@pytest.mark.parametrize("state", ["not_needed", "preloaded", "already_preloaded", "not_applicable"])
def test_ready_preload_reaches_helper_creation(monkeypatch, tmp_path, state):
    events, _, _ = _install_stubs(monkeypatch, tmp_path, state=state, phase="complete")
    with pytest.raises(HelperCreationReached):
        monitor.main()
    assert "helper.create" in events


def test_unproven_service_conflict_is_not_new_terminal_scope(monkeypatch, tmp_path):
    events, _, _ = _install_stubs(monkeypatch, tmp_path,
                                state="failed_service_conflict", phase="query_product")
    with pytest.raises(HelperCreationReached):
        monitor.main()
    assert "helper.create" in events


@pytest.mark.parametrize("stop_raises,snapshot_close_raises", [(True, False), (False, True), (True, True)])
def test_cleanup_exception_preserves_primary_classification(monkeypatch, tmp_path, capsys,
                                                          stop_raises, snapshot_close_raises):
    events, log_dir, _ = _install_stubs(monkeypatch, tmp_path, state="failed_service_conflict",
                                      stop_raises=stop_raises,
                                      snapshot_close_raises=snapshot_close_raises)
    assert monitor.main() == 6
    assert events == ["monitor.start", "monitor.stop", "snapshot.close"]
    payload = json.loads((log_dir / monitor.WSS_HELPER_STATUS_FILENAME).read_bytes())
    assert payload["windivert_preload_state"] == "failed_service_conflict"
    assert payload["error_code"] == "wss_stale_product_service_conflict"
    captured = capsys.readouterr()
    assert captured.err.strip() == "[error] wss_stale_product_service_conflict"
    assert "synthetic private-path token" not in captured.out + captured.err
