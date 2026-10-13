"""Real UI ack producer under controlled file conflicts; no UI or game launch."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from bidking_lab import _atomic_json
from test_wss_game_exit_group_20260913 import overlay_module, wss


def ack_subject(tmp_path):
    target = tmp_path / "lifecycle_ui_exit.json"
    previous = b'{"launch_id":"previous-launch"}'
    target.write_bytes(previous)
    obj = SimpleNamespace(
        snapshot_path=tmp_path / "snapshot.json",
        _group_exit_launch_id="current-launch",
        _group_exit_monitor_identities={os.getpid(): 1234},
        _group_exit_pools_reaped=True,
        _group_exit_cleanup_errors=[],
    )
    guard = SimpleNamespace(
        status_path=tmp_path / "lifecycle_status.json",
        bound_ui=(os.getpid(), psutil.Process().create_time()),
        launch_id="current-launch",
        monitor_started_at_unix_ms=1234,
    )
    return obj, guard, target, previous


def test_ui_ack_retries_transient_replace_and_reader_accepts_only_bound_exit(
    tmp_path, monkeypatch, overlay_module
):
    obj, guard, target, previous = ack_subject(tmp_path)
    replace = os.replace
    attempts, delays = [], []

    def transient(source, destination):
        assert target.read_bytes() == previous
        attempts.append(Path(source))
        if len(attempts) < 3:
            raise PermissionError(13, "fixture reader lock")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", transient)
    monkeypatch.setattr(_atomic_json.time, "sleep", delays.append)
    overlay_module.AhmadTkOverlay._write_game_exit_ack(obj)
    ack = json.loads(target.read_text("utf-8"))
    assert ack["launch_id"] == "current-launch"
    assert ack["pools_reaped"] is True
    assert len(attempts) == 3 and delays == [0.05, 0.1]
    assert list(tmp_path.iterdir()) == [target]
    monkeypatch.setattr(wss, "_bound_game_running_state", lambda *args: True)
    assert wss._group_ui_exit_state(guard) == "remaining"
    monkeypatch.setattr(wss, "_bound_game_running_state", lambda *args: False)
    assert wss._group_ui_exit_state(guard) == "exited"
    guard.launch_id = "different-launch"
    assert wss._group_ui_exit_state(guard) == "unknown"


@pytest.mark.parametrize("failure,expected_attempts,category", [
    (PermissionError(13, "fixture private detail"), 5, "permission"),
    (OSError(5, "fixture private detail"), 1, "io"),
])
def test_ui_ack_failed_publish_preserves_old_ack_and_reader_stays_unknown(
    tmp_path, monkeypatch, overlay_module, capsys, failure, expected_attempts, category
):
    obj, guard, target, previous = ack_subject(tmp_path)
    attempts, delays = [], []

    def fail(source, destination):
        attempts.append(Path(source))
        raise failure

    monkeypatch.setattr(os, "replace", fail)
    monkeypatch.setattr(_atomic_json.time, "sleep", delays.append)
    overlay_module.AhmadTkOverlay._write_game_exit_ack(obj)
    assert len(attempts) == expected_attempts
    assert target.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [target]
    monkeypatch.setattr(wss, "_bound_game_running_state", lambda *args: False)
    assert wss._group_ui_exit_state(guard) == "unknown"
    diagnostic = capsys.readouterr().err
    assert f"ui_exit_ack_write_failed category={category}" in diagnostic
    assert "fixture private detail" not in diagnostic
    if category == "permission":
        assert delays == pytest.approx([0.05, 0.1, 0.15, 0.2])
    else:
        assert delays == []


def test_ui_ack_uses_own_temp_without_overwriting_existing_foreign_temp(
    tmp_path, overlay_module
):
    obj, _, target, _ = ack_subject(tmp_path)
    foreign_temp = tmp_path / "lifecycle_ui_exit.tmp"
    foreign_temp.write_bytes(b"another writer's evidence")
    overlay_module.AhmadTkOverlay._write_game_exit_ack(obj)
    assert json.loads(target.read_text("utf-8"))["launch_id"] == "current-launch"
    assert foreign_temp.read_bytes() == b"another writer's evidence"
    assert set(tmp_path.iterdir()) == {target, foreign_temp}


def test_ui_ack_without_bound_identity_does_not_publish(tmp_path, overlay_module):
    obj, _, target, previous = ack_subject(tmp_path)
    obj._group_exit_monitor_identities = {}
    overlay_module.AhmadTkOverlay._write_game_exit_ack(obj)
    assert target.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [target]
