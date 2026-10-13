from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import psutil
import pytest
import sys
import time
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "scripts" / "run_fatbeans_webhook_monitor.py"
    spec = importlib.util.spec_from_file_location("run_fatbeans_webhook_monitor", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    missing = object()
    previous_module = sys.modules.get(spec.name, missing)
    sys.modules[spec.name] = module
    try:
        # Match the real script entry's sibling-import path without requiring
        # another test to have modified the process-wide import search path.
        sys.path.insert(0, str(path.parent))
        spec.loader.exec_module(module)
    except BaseException:
        if previous_module is missing:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous_module
        raise
    finally:
        sys.path[:] = previous_path
    return module


def test_script_loader_resolves_its_sibling_without_leaking_search_path(monkeypatch) -> None:
    scripts = ROOT / "scripts"
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if Path(entry) != scripts])
    monkeypatch.delitem(sys.modules, "package_generation_identity", raising=False)
    previous_path = list(sys.path)

    module = _module()

    assert Path(module.__file__) == scripts / "run_fatbeans_webhook_monitor.py"
    assert Path(sys.modules["package_generation_identity"].__file__) == scripts / "package_generation_identity.py"
    assert sys.path == previous_path


def test_script_loader_failure_restores_search_path_and_previous_module(monkeypatch) -> None:
    from importlib.machinery import SourceFileLoader

    previous_path = list(sys.path)
    previous_module = object()
    monkeypatch.setitem(sys.modules, "run_fatbeans_webhook_monitor", previous_module)
    original = SourceFileLoader.exec_module

    def fail_target(loader, module):
        if loader.name == "run_fatbeans_webhook_monitor":
            raise RuntimeError("synthetic loader failure")
        return original(loader, module)

    monkeypatch.setattr(SourceFileLoader, "exec_module", fail_target)
    with pytest.raises(RuntimeError, match="synthetic loader failure"):
        _module()

    assert sys.path == previous_path
    assert sys.modules["run_fatbeans_webhook_monitor"] is previous_module


def _payload(**overrides):
    payload = {
        "Protocol": "Tcp",
        "SrcIP": "127.0.0.1",
        "SrcPort": 50123,
        "DstIP": "8.133.195.27",
        "DstPort": 10000,
        "Url": None,
        "PID": 1234,
        "ProcessName": "BidKing.exe",
        "Data": base64.b64encode(b"\x00\x00\x00\x04").decode("ascii"),
        "Request": None,
        "Response": None,
    }
    payload.update(overrides)
    return payload


def test_monitor_lock_binds_fallback_to_exact_entrypoint_and_log_dir(
    tmp_path: Path,
) -> None:
    module = _module()
    monitor_path = tmp_path / "当前 包 with spaces" / "scripts" / "local_compat_monitor.py"
    log_dir = tmp_path / "当前 包 with spaces" / "data" / "logs" / "live"

    lock_path = module._acquire_lock(log_dir, monitor_path=monitor_path)
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert Path(payload["monitor_path"]) == monitor_path.resolve()
        assert Path(payload["log_dir"]) == log_dir.resolve()
        assert payload["pid"] > 0
        assert payload["started_at"] > 0
        assert abs(
            payload["process_started_at"]
            - psutil.Process(os.getpid()).create_time()
        ) < 0.001
    finally:
        lock_path.unlink(missing_ok=True)


def test_monitor_lock_classifies_absent_state(tmp_path: Path) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    inspection = module._inspect_monitor_lock(
        log_dir / "monitor.lock",
        monitor_path=tmp_path / "monitor.py",
        log_dir=log_dir,
    )
    assert inspection.state == "absent"


def test_monitor_lock_preserves_malformed_existing_lock(tmp_path: Path) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock_path = log_dir / "monitor.lock"
    lock_path.write_text("{not-json", encoding="utf-8")

    inspection = module._inspect_monitor_lock(
        lock_path,
        monitor_path=tmp_path / "monitor.py",
        log_dir=log_dir,
    )
    assert inspection.state == "unknown"
    with pytest.raises(RuntimeError):
        module._acquire_lock(log_dir, monitor_path=tmp_path / "monitor.py")

    assert lock_path.read_text(encoding="utf-8") == "{not-json"


def test_monitor_lock_preserves_live_non_python_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monitor_path = tmp_path / "scripts" / "run_wss_live_monitor.py"
    lock_path = log_dir / "monitor.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "process_started_at": 1000.0,
                "monitor_path": str(monitor_path),
                "log_dir": str(log_dir),
            }
        ),
        encoding="utf-8",
    )

    class ForeignProcess:
        def name(self) -> str:
            return "explorer.exe"

        def create_time(self) -> float:
            return 1000.0

    monkeypatch.setattr(psutil, "Process", lambda _pid: ForeignProcess())

    inspection = module._inspect_monitor_lock(
        lock_path,
        monitor_path=monitor_path,
        log_dir=log_dir,
    )
    assert inspection.state == "foreign"
    with pytest.raises(RuntimeError):
        module._acquire_lock(log_dir, monitor_path=monitor_path)

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == 4242


def test_monitor_lock_preserves_live_owner_with_unknown_epoch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monitor_path = tmp_path / "scripts" / "run_wss_live_monitor.py"
    lock_path = log_dir / "monitor.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "monitor_path": str(monitor_path),
                "log_dir": str(log_dir),
            }
        ),
        encoding="utf-8",
    )

    class CurrentProcess:
        def name(self) -> str:
            return "python.exe"

        def create_time(self) -> float:
            return 1000.0

    monkeypatch.setattr(psutil, "Process", lambda _pid: CurrentProcess())

    inspection = module._inspect_monitor_lock(
        lock_path,
        monitor_path=monitor_path,
        log_dir=log_dir,
    )
    assert inspection.state == "unknown"
    with pytest.raises(RuntimeError):
        module._acquire_lock(log_dir, monitor_path=monitor_path)

    assert lock_path.exists()


def test_monitor_lock_preserves_live_foreign_python_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "current" / "logs"
    log_dir.mkdir(parents=True)
    monitor_path = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    lock_path = log_dir / "monitor.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "process_started_at": 1000.0,
                "monitor_path": str(tmp_path / "foreign" / monitor_path.name),
                "log_dir": str(log_dir),
            }
        ),
        encoding="utf-8",
    )

    class ForeignPythonProcess:
        def name(self) -> str:
            return "python.exe"

        def create_time(self) -> float:
            return 1000.0

    monkeypatch.setattr(psutil, "Process", lambda _pid: ForeignPythonProcess())

    inspection = module._inspect_monitor_lock(
        lock_path,
        monitor_path=monitor_path,
        log_dir=log_dir,
    )
    assert inspection.state == "foreign"
    with pytest.raises(RuntimeError):
        module._acquire_lock(log_dir, monitor_path=monitor_path)

    assert lock_path.exists()


def test_monitor_lock_reclaims_only_proven_dead_pid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monitor_path = tmp_path / "scripts" / "run_wss_live_monitor.py"
    lock_path = log_dir / "monitor.lock"
    dead_pid = 2_000_000_000
    lock_path.write_text(
        json.dumps(
            {
                "pid": dead_pid,
                "process_started_at": 1000.0,
                "monitor_path": str(monitor_path),
                "log_dir": str(log_dir),
            }
        ),
        encoding="utf-8",
    )
    real_process = psutil.Process

    def process_for(pid: int):
        if pid == dead_pid:
            raise psutil.NoSuchProcess(pid)
        return real_process(pid)

    monkeypatch.setattr(psutil, "Process", process_for)

    inspection = module._inspect_monitor_lock(
        lock_path,
        monitor_path=monitor_path,
        log_dir=log_dir,
    )
    assert inspection.state == "stale"
    acquired = module._acquire_lock(log_dir, monitor_path=monitor_path)
    try:
        payload = json.loads(acquired.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
    finally:
        acquired.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows lock replacement contract")
def test_monitor_lock_reclaim_preserves_replacement_after_final_reread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monitor_path = tmp_path / "scripts" / "run_wss_live_monitor.py"
    lock_path = log_dir / "monitor.lock"
    stale_raw = json.dumps(
        {
            "pid": 2_000_000_000,
            "process_started_at": 1000.0,
            "monitor_path": str(monitor_path),
            "log_dir": str(log_dir),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    replacement_raw = json.dumps(
        {
            "pid": os.getpid(),
            "process_started_at": psutil.Process(os.getpid()).create_time(),
            "monitor_path": str(monitor_path),
            "log_dir": str(log_dir),
            "nonce": "replacement-after-reread",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    lock_path.write_bytes(stale_raw)

    real_read_bytes = Path.read_bytes
    reads = 0

    def replace_after_second_read(path: Path) -> bytes:
        nonlocal reads
        raw = real_read_bytes(path)
        if path == lock_path:
            reads += 1
            if reads == 2:
                replacement = log_dir / "replacement.lock"
                replacement.write_bytes(replacement_raw)
                os.replace(replacement, lock_path)
        return raw

    monkeypatch.setattr(Path, "read_bytes", replace_after_second_read)

    with pytest.raises(RuntimeError, match="changed"):
        module._acquire_lock(log_dir, monitor_path=monitor_path)

    assert reads == 2
    assert real_read_bytes(lock_path) == replacement_raw


@pytest.mark.skipif(os.name != "nt", reason="Windows lock replacement contract")
def test_monitor_lock_reclaim_blocks_replace_after_handle_bound_reread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monitor_path = tmp_path / "scripts" / "run_wss_live_monitor.py"
    lock_path = log_dir / "monitor.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 2_000_000_000,
                "process_started_at": 1000.0,
                "monitor_path": str(monitor_path),
                "log_dir": str(log_dir),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    replacement = log_dir / "replacement.lock"
    replacement_raw = b'{"nonce":"after-handle-reread"}'
    real_mark_delete = module._mark_lock_delete_on_close
    replacement_blocked = False

    def attempt_replace_then_delete(fd: int) -> None:
        nonlocal replacement_blocked
        replacement.write_bytes(replacement_raw)
        try:
            os.replace(replacement, lock_path)
        except PermissionError:
            replacement_blocked = True
        real_mark_delete(fd)

    monkeypatch.setattr(
        module,
        "_mark_lock_delete_on_close",
        attempt_replace_then_delete,
    )

    acquired = module._acquire_lock(log_dir, monitor_path=monitor_path)
    try:
        assert replacement_blocked is True
        assert replacement.read_bytes() == replacement_raw
        payload = json.loads(acquired.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
    finally:
        acquired.unlink(missing_ok=True)


def test_live_starters_default_to_v3_practical_formal_mode() -> None:
    module = _module()
    config = module.WebhookMonitorConfig(
        log_dir=ROOT / ".tmp" / "logs",
        raw_dir=ROOT / ".tmp" / "raw",
        process_name="BidKing.exe",
        server_ports=(10000,),
        n_trials=20,
        roi_trials=0,
        shadow_trials=20,
        full_shadow_trials=20,
        run_debug_shadows=False,
        seed=1,
    )

    assert config.formal_mode == "v3_practical"
    windivert_start = (ROOT / "scripts" / "start_live_windivert_overlay.ps1").read_text(
        encoding="utf-8-sig"
    )
    hero_start = (ROOT / "apps" / "hero_ref" / "Start-HeroRef.ps1").read_text(
        encoding="utf-8-sig"
    )
    ahmad_start = (
        ROOT / "hero_ref" / "start_ahmad_live.ps1"
    ).read_text(encoding="utf-8-sig")

    assert '[string]$FormalMode = "v3_practical"' in windivert_start
    assert '"--formal-mode", "v3_practical"' in hero_start
    assert 'FormalMode = "v3_practical"' in hero_start
    assert '[string]$FormalMode = "v3_practical"' in ahmad_start


def test_webhook_payload_to_row_infers_send_and_data_length() -> None:
    module = _module()

    row = module._webhook_payload_to_row(_payload(), sort_id=7)

    assert row["SortID"] == 7
    assert row["Direct"] == "SEND"
    assert row["Protocol"] == "Tcp"
    assert row["DstPort"] == 10000
    assert row["DataLength"] == 4
    assert row["ProcessName"] == "BidKing.exe"


def test_webhook_payload_to_row_infers_receive_from_server_port() -> None:
    module = _module()

    row = module._webhook_payload_to_row(
        _payload(
            SrcIP="8.133.195.27",
            SrcPort=10000,
            DstIP="127.0.0.1",
            DstPort=50123,
        ),
        sort_id=8,
    )

    assert row["Direct"] == "REV"
    assert row["SrcPort"] == 10000


def test_webhook_payload_to_row_filters_non_target_process() -> None:
    module = _module()

    assert (
        module._webhook_payload_to_row(
            _payload(ProcessName="chrome.exe"),
            sort_id=1,
            process_name="BidKing.exe",
        )
        is None
    )


def test_webhook_monitor_processes_accumulated_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    raw_dir = tmp_path / "raw"
    calls: dict[str, object] = {}

    def fake_build(payload, *, file, **_kwargs):
        calls["payload"] = json.loads(payload)
        calls["file"] = file
        return {
            "created_at": 1000.0,
            "file": file,
            "ui_contract": {
                "baseline": {
                    "decision": {"action": "可守不抢"},
                    "posterior": {"status": "ok"},
                }
            },
        }

    def fake_write(artifact, *, log_dir, append_logs=True):
        calls["artifact"] = dict(artifact)
        calls["log_dir"] = Path(log_dir)
        calls["append_logs"] = append_logs

    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(module, "write_monitor_logs", fake_write)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )

    result = monitor.accept_payload(_payload())
    monitor._process_snapshot(force=True)

    assert result["accepted"] is True
    assert raw_dir.joinpath("fatbeans_webhook_live.json").exists()
    assert calls["file"] == "fatbeans_webhook_live.json"
    assert calls["log_dir"] == log_dir
    rows = calls["payload"]
    assert rows[0]["Direct"] == "SEND"
    artifact = calls["artifact"]
    assert artifact["source"] == "fatbeans_webhook"
    assert artifact["capture_rows"] == 1
    assert artifact["webhook_packets"] == 1
    assert calls["append_logs"] is True


def test_capture_row_monitor_uses_configured_source_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    raw_dir = tmp_path / "raw"
    calls: dict[str, object] = {}

    def fake_build(payload, *, file, **_kwargs):
        calls["payload"] = json.loads(payload)
        return {"created_at": 1000.0, "file": file}

    def fake_write(artifact, *, log_dir, append_logs=True):
        calls["artifact"] = dict(artifact)
        calls["log_dir"] = Path(log_dir)
        calls["append_logs"] = append_logs

    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(module, "write_monitor_logs", fake_write)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            file_name="windivert_live.json",
            source_name="windivert",
            packet_count_key="windivert_frames",
        ),
        tables=object(),
    )

    row = module._webhook_payload_to_row(_payload(), sort_id=1)
    assert row is not None
    monitor.accept_row(row)
    monitor._process_snapshot(force=True)

    artifact = calls["artifact"]
    assert artifact["source"] == "windivert"
    assert artifact["capture_rows"] == 1
    assert artifact["windivert_frames"] == 1
    assert "webhook_packets" not in artifact
    assert calls["append_logs"] is True


def test_monitor_passes_cached_local_player_id_hint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    raw_dir = tmp_path / "raw"
    module._write_cached_local_player_id(
        module._local_player_cache_path(log_dir),
        900000000000123,
    )
    hints: list[int | None] = []

    def fake_build(payload, *, file, local_player_id_hint=None, **_kwargs):
        hints.append(local_player_id_hint)
        return {"created_at": 1000.0, "file": file}

    monkeypatch.setattr(module, "_semantic_signature_from_payload", lambda _payload: None)
    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture_payload",
        lambda _payload, **_kwargs: SimpleNamespace(
            states=(SimpleNamespace(player_id=900000000000123),),
        ),
    )
    monkeypatch.setattr(module, "write_monitor_logs", lambda *args, **kwargs: None)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )

    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=True)

    assert hints == [900000000000123]


def test_monitor_caches_inferred_local_player_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    raw_dir = tmp_path / "raw"

    monkeypatch.setattr(module, "_semantic_signature_from_payload", lambda _payload: None)
    monkeypatch.setattr(
        module,
        "build_monitor_artifact_from_payload",
        lambda _payload, *, file, local_player_id_hint=None, **_kwargs: {
            "created_at": 1000.0,
            "file": file,
            "local_player_id_hint": local_player_id_hint,
        },
    )
    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture_payload",
        lambda _payload, **_kwargs: SimpleNamespace(
            states=(SimpleNamespace(player_id=900000000000123),),
        ),
    )
    monkeypatch.setattr(module, "write_monitor_logs", lambda *args, **kwargs: None)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )

    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=True)

    payload = json.loads(
        module._local_player_cache_path(log_dir).read_text(encoding="utf-8-sig")
    )
    assert payload["local_player_id"] == 900000000000123


def test_monitor_archives_raw_rows_before_reset(tmp_path: Path) -> None:
    module = _module()
    raw_dir = tmp_path / "raw"
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=raw_dir,
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            file_name="windivert_live.json",
            source_name="windivert",
        ),
        tables=object(),
    )
    row = module._webhook_payload_to_row(_payload(), sort_id=1)
    assert row is not None
    row["SessionID"] = "2407:9000000000000008"
    row["MessageID"] = "0x0077"

    monitor.accept_row(row, schedule_process=False)
    monitor.reset_rows()

    archives = list(raw_dir.glob("archive/reset/windivert_live_*_reset.json"))
    assert len(archives) == 1
    archived_rows = json.loads(archives[0].read_text(encoding="utf-8"))
    assert isinstance(archived_rows, list)
    assert archived_rows[0]["SessionID"] == "2407:9000000000000008"


def test_reset_rows_clears_inference_rate_limit_for_new_session(tmp_path: Path) -> None:
    # Regression: a new live session must get its first inference (R1) promptly.
    # Before the fix, reset_rows() left `_last_inference_at` set, so a lobby/menu
    # inference fired just before the session boundary (engine: not_structured_hero)
    # would let min_inference_interval_seconds throttle the new session's R1, and
    # the first emitted snapshot landed on R2 ("第二轮才显示").
    module = _module()
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=2.0,
            file_name="windivert_live.json",
            source_name="windivert",
        ),
        tables=object(),
    )

    calls: list[bool] = []
    monitor._process_snapshot = lambda *, force=False, expected_schedule_owner=None: calls.append(force)  # type: ignore[assignment]

    def _accept_new_row(session_id: str) -> None:
        row = module._webhook_payload_to_row(_payload(), sort_id=1)
        assert row is not None
        row["SessionID"] = session_id
        row["MessageID"] = "0x0025"
        monitor.accept_row(row, schedule_process=True)
        # accept_row also enqueues; drain so it does not interfere.
        try:
            monitor._queue.get_nowait()
        except Exception:
            pass

    # Simulate a lobby inference that fired right before the new game starts.
    _accept_new_row("2531:1")
    monitor._last_inference_at = time.monotonic()
    monitor._process_if_due()
    assert calls == [], "interval throttle should suppress while last inference is recent"

    # New session detected -> reset. The throttle clock must clear so R1 fires.
    monitor.reset_rows()
    assert monitor._last_inference_at == 0.0
    _accept_new_row("2534:9")
    monitor._process_if_due()
    assert calls == [False], "new-session R1 inference must fire despite a recent prior inference"


def test_reset_rows_retains_snapshot_for_session_ahead_fallback(tmp_path: Path) -> None:
    # 【0.2.4-hotfix3 回归修复】reset_rows 【不再删除】latest_snapshot.json。
    # 0868536 曾在此删快照修"跨对局延迟", 但它默认"会话推进=真有新局跟上"; 局间主界面买/卖误触发
    # 会话推进 → 删了快照却无真状态帧跟上 → UI 空白卡死(需重启)。改由 0868536 同时加的 session-ahead
    # 逻辑显示"等待新对局"standby, 保留旧快照做兜底(真新局首帧快照会原子覆盖)。
    # 故这里断言: reset 后快照【仍在】(兜底), 但 raw 行照旧清空(reset 的本职)。
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    latest = log_dir / "latest_snapshot.json"
    latest.write_text(
        json.dumps({"session_id": "2403:old", "phase": "settled"}),
        encoding="utf-8",
    )
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            file_name="windivert_live.json",
            source_name="windivert",
        ),
        tables=object(),
    )
    row = module._webhook_payload_to_row(_payload(), sort_id=1)
    assert row is not None
    monitor.accept_row(row, schedule_process=False)
    monitor.reset_rows()
    assert latest.exists()  # 快照保留做兜底, 不再空白卡死
    assert monitor.status()["rows"] == 0  # raw 行仍被清空(reset 本职)


def _round_archive_monitor(module, tmp_path: Path, *, enabled: bool = True):
    return module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            file_name="windivert_live.json",
            source_name="windivert",
            archive_round_snapshots=enabled,
        ),
        tables=object(),
    )


def test_round_snapshot_archive_keeps_latest_per_round_phase(tmp_path: Path) -> None:
    module = _module()
    monitor = _round_archive_monitor(module, tmp_path)
    archive_dir = tmp_path / "logs" / "round_snapshots"

    monitor._archive_round_snapshot(
        {"session_id": "2402:1", "round": 3, "phase": "bidding", "n_trials": 10}
    )
    monitor._archive_round_snapshot(
        {"session_id": "2402:1", "round": 3, "phase": "bidding", "n_trials": 10, "v": 2}
    )
    monitor._archive_round_snapshot(
        {"session_id": "2402:1", "round": 3, "phase": "settled", "n_trials": 500}
    )

    files = sorted(p.name for p in archive_dir.glob("*.json"))
    assert files == ["r03_bidding.json", "r03_settled.json"]
    bidding = json.loads((archive_dir / "r03_bidding.json").read_text(encoding="utf-8"))
    assert bidding["v"] == 2  # latest write for the round/phase wins


def test_round_snapshot_archive_clears_on_new_session(tmp_path: Path) -> None:
    module = _module()
    monitor = _round_archive_monitor(module, tmp_path)
    archive_dir = tmp_path / "logs" / "round_snapshots"

    monitor._archive_round_snapshot(
        {"session_id": "2402:1", "round": 2, "phase": "bidding"}
    )
    monitor._archive_round_snapshot(
        {"session_id": "2402:2", "round": 1, "phase": "bidding"}
    )

    files = sorted(p.name for p in archive_dir.glob("*.json"))
    assert files == ["r01_bidding.json"]  # previous game's files were cleared


def test_round_snapshot_archive_can_be_disabled(tmp_path: Path) -> None:
    module = _module()
    monitor = _round_archive_monitor(module, tmp_path, enabled=False)
    monitor._archive_round_snapshot(
        {"session_id": "2402:1", "round": 3, "phase": "bidding"}
    )
    assert not (tmp_path / "logs" / "round_snapshots").exists()


def _idle_upgrade_monitor(module, tmp_path: Path, **cfg):
    base = dict(
        log_dir=tmp_path / "logs",
        raw_dir=tmp_path / "raw",
        process_name="BidKing.exe",
        server_ports=(10000,),
        n_trials=500,
        roi_trials=0,
        shadow_trials=20,
        full_shadow_trials=20,
        run_debug_shadows=False,
        seed=1,
        fast_n_trials=10,
        file_name="windivert_live.json",
        source_name="windivert",
    )
    base.update(cfg)
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(**base), tables=object()
    )
    monitor._rows = [{"SortID": 1}, {"SortID": 2}]
    monitor._last_processed_count = 2
    monitor._last_full_processed_count = 0
    monitor._last_packet_at = time.monotonic() - 60.0
    monitor._last_fast_seconds = 0.05
    return monitor


def test_idle_full_upgrade_runs_full_when_settled(tmp_path: Path) -> None:
    module = _module()
    monitor = _idle_upgrade_monitor(module, tmp_path)
    calls: list[bool] = []
    monitor._dispatch_idle_full = lambda: calls.append(True)

    monitor._maybe_run_idle_full_upgrade()

    assert calls == [True]


def test_idle_full_upgrade_waits_while_packets_recent(tmp_path: Path) -> None:
    module = _module()
    monitor = _idle_upgrade_monitor(module, tmp_path)
    monitor._last_packet_at = time.monotonic()  # a packet just arrived
    calls: list[bool] = []
    monitor._dispatch_idle_full = lambda: calls.append(True)

    monitor._maybe_run_idle_full_upgrade()

    assert calls == []


def test_idle_full_upgrade_skips_when_already_full(tmp_path: Path) -> None:
    module = _module()
    monitor = _idle_upgrade_monitor(module, tmp_path)
    monitor._last_full_processed_count = 2  # already upgraded this state
    calls: list[bool] = []
    monitor._dispatch_idle_full = lambda: calls.append(True)

    monitor._maybe_run_idle_full_upgrade()

    assert calls == []


def test_idle_full_upgrade_skips_heavy_and_marks_handled(tmp_path: Path) -> None:
    module = _module()
    # ratio = 500 / 10 = 50; 0.2s fast => ~10s full > 6s budget => skip.
    monitor = _idle_upgrade_monitor(module, tmp_path, full_upgrade_max_seconds=6.0)
    monitor._last_fast_seconds = 0.2
    calls: list[bool] = []
    monitor._dispatch_idle_full = lambda: calls.append(True)

    monitor._maybe_run_idle_full_upgrade()

    assert calls == []
    assert monitor._last_full_processed_count == 2  # marked so it is not retried


def test_idle_full_upgrade_disabled_by_config(tmp_path: Path) -> None:
    module = _module()
    monitor = _idle_upgrade_monitor(module, tmp_path, full_upgrade_when_idle=False)
    calls: list[bool] = []
    monitor._dispatch_idle_full = lambda: calls.append(True)

    monitor._maybe_run_idle_full_upgrade()

    assert calls == []


def test_webhook_monitor_writes_fast_snapshot_without_appending_logs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    calls: list[dict[str, object]] = []

    def fake_build(
        payload,
        *,
        file,
        n_trials,
        roi_trials,
        shadow_trials,
        run_debug_shadows,
        **_kwargs,
    ):
        return {
            "created_at": 1000.0,
            "file": file,
            "payload_rows": len(json.loads(payload)),
            "n_trials": n_trials,
            "roi_trials": roi_trials,
            "shadow_trials": shadow_trials,
            "run_debug_shadows": run_debug_shadows,
        }

    def fake_write(artifact, *, log_dir, append_logs=True):
        calls.append(
            {
                "artifact": dict(artifact),
                "log_dir": Path(log_dir),
                "append_logs": append_logs,
            }
        )

    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(module, "write_monitor_logs", fake_write)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=500,
            roi_trials=250,
            shadow_trials=None,
            full_shadow_trials=20,
            run_debug_shadows=True,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            fast_n_trials=40,
        ),
        tables=object(),
    )

    monitor.accept_row({"SortID": 1}, schedule_process=True)
    monitor._process_snapshot(force=False)
    monitor._process_snapshot(force=True)

    fast = calls[0]["artifact"]
    full = calls[1]["artifact"]
    assert fast["snapshot_mode"] == "fast"
    assert fast["n_trials"] == 40
    assert fast["roi_trials"] == 0
    assert fast["shadow_trials"] == 20
    assert fast["run_debug_shadows"] is False
    assert fast["raw_capture"] == str(monitor.raw_jsonl_path.resolve())
    assert fast["raw_capture_jsonl"] == str(monitor.raw_jsonl_path.resolve())
    assert calls[0]["append_logs"] is False
    assert full["snapshot_mode"] == "full"
    assert full["n_trials"] == 500
    assert full["roi_trials"] == 250
    assert full["shadow_trials"] == 20
    assert full["inference_profile"]["n_trials"] == 500
    assert full["run_debug_shadows"] is True
    assert full["raw_capture"] == str(monitor.raw_path.resolve())
    assert full["raw_capture_jsonl"] == str(monitor.raw_jsonl_path.resolve())
    assert calls[1]["append_logs"] is True
    assert monitor.status()["last_full_processed_count"] == 1


def test_settled_fast_snapshot_still_appends_logs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """结算帧即使停在 fast 也必须落 sessions.jsonl + 触发上传。

    深仓晚轮的 full 升级会被 _maybe_run_idle_full_upgrade 的「too heavy」保护跳过(保 live
    响应), 结算帧便永远停在 fast。若 append_logs 仍只看 full, 这一局就漏写 sessions、漏上传
    -> 对局记录/服务器收不到 = 数据采集断流。结算真值来自 0x2d 帧、与推理 trials 无关, 故 fast
    结算帧落盘是准确且必须的。回归守卫: 别再把 append_logs 改回只 == "full"。"""
    module = _module()
    calls: list[dict[str, object]] = []

    def fake_build(payload, *, file, **_kwargs):
        return {"created_at": 1000.0, "file": file, "phase": "settled"}

    def fake_write(artifact, *, log_dir, append_logs=True):
        calls.append({"append_logs": append_logs, "phase": artifact.get("phase")})

    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(module, "write_monitor_logs", fake_write)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=500,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            fast_n_trials=40,
        ),
        tables=object(),
    )

    # 需 >= FAST_BOOTSTRAP_MIN_ROWS(3)行, 否则 fast 结算帧被 bootstrap 守卫整帧跳过。
    for sort_id in (1, 2, 3, 4):
        monitor.accept_row({"SortID": sort_id}, schedule_process=True)
    monitor._process_snapshot(force=False)

    assert calls, "fast 结算帧应当调用 write_monitor_logs"
    assert calls[0]["phase"] == "settled"
    assert calls[0]["append_logs"] is True  # 修复点: 结算帧 fast 也落盘+上传


def test_webhook_monitor_appends_jsonl_on_fast_without_rewriting_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    writes: list[Path] = []

    def fake_atomic(path: Path, value: Any) -> None:
        writes.append(path)

    monkeypatch.setattr(module, "_atomic_write_json", fake_atomic)
    monkeypatch.setattr(
        module,
        "build_monitor_artifact_from_payload",
        lambda *args, **kwargs: {"created_at": 1.0, "file": kwargs["file"]},
    )
    monkeypatch.setattr(
        module,
        "write_monitor_logs",
        lambda *args, **kwargs: None,
    )

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=500,
            roi_trials=0,
            shadow_trials=None,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            fast_n_trials=10,
            file_name="windivert_live.json",
        ),
        tables=object(),
    )

    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=False)

    assert monitor.raw_jsonl_path.exists()
    assert not monitor.raw_path.exists()
    assert writes == []

    monitor.accept_row({"SortID": 2, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=True)
    assert writes == [monitor.raw_path]


def test_webhook_monitor_start_clears_stale_working_files(tmp_path: Path) -> None:
    module = _module()
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=500,
            roi_trials=0,
            shadow_trials=None,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )

    monitor.raw_path.parent.mkdir(parents=True, exist_ok=True)
    monitor.raw_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    monitor.raw_path.write_text("stale", encoding="utf-8")
    monitor.raw_jsonl_path.write_text("stale\n", encoding="utf-8")
    monitor.start()
    monitor.stop()

    assert not monitor.raw_path.exists()
    assert not monitor.raw_jsonl_path.exists()


def test_webhook_monitor_skips_duplicate_fast_inference_for_same_sort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    build_calls = 0

    def fake_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return {"created_at": 1.0, "file": kwargs["file"]}

    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(module, "write_monitor_logs", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_atomic_write_json", lambda *args, **kwargs: None)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=500,
            roi_trials=0,
            shadow_trials=None,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            fast_n_trials=10,
        ),
        tables=object(),
    )

    monitor.accept_row({"SortID": 5, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=False)
    monitor._queue.put_nowait(object())
    monitor._process_if_due()
    assert build_calls == 1


def test_webhook_monitor_skips_fast_write_when_semantics_do_not_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    build_calls = 0
    write_calls = 0

    def fake_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return {"created_at": 1.0, "file": kwargs["file"]}

    def fake_write(*args, **kwargs):
        nonlocal write_calls
        write_calls += 1

    monkeypatch.setattr(
        module,
        "_semantic_signature_from_payload",
        lambda _payload: ("same-session", "same-facts"),
    )
    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(module, "write_monitor_logs", fake_write)
    monkeypatch.setattr(module, "_atomic_write_json", lambda *args, **kwargs: None)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=500,
            roi_trials=0,
            shadow_trials=None,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            fast_n_trials=10,
        ),
        tables=object(),
    )

    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=False)
    monitor.accept_row({"SortID": 2, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=False)

    assert build_calls == 1
    assert write_calls == 1
    assert monitor.status()["last_processed_count"] == 2


def test_capture_row_can_wait_for_state_before_scheduling_inference(
    tmp_path: Path,
) -> None:
    module = _module()
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )
    calls: list[bool] = []
    monitor._process_snapshot = lambda *, force, expected_schedule_owner=None: calls.append(force)

    monitor.accept_row({"SortID": 1}, schedule_process=False)
    monitor._process_if_due()
    assert calls == []
    assert monitor.status()["last_requested_count"] == 0

    monitor.accept_row({"SortID": 2}, schedule_process=True)
    monitor._process_if_due()
    assert calls == [False]
    assert monitor.status()["last_requested_count"] == 2


def test_should_skip_fast_bootstrap_settled_snapshot() -> None:
    module = _module()
    artifact = {"phase": "settled", "ui_contract": {"context": {"phase": "settled"}}}
    assert module._should_skip_fast_bootstrap_snapshot(
        artifact,
        row_count=1,
        snapshot_mode="fast",
    )
    assert not module._should_skip_fast_bootstrap_snapshot(
        artifact,
        row_count=3,
        snapshot_mode="fast",
    )
    assert not module._should_skip_fast_bootstrap_snapshot(
        artifact,
        row_count=1,
        snapshot_mode="full",
    )


def test_webhook_monitor_skips_fast_bootstrap_settled_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "build_monitor_artifact_from_payload",
        lambda *args, **kwargs: {
            "created_at": 1000.0,
            "phase": "settled",
            "ui_contract": {"context": {"phase": "settled"}},
        },
    )
    monkeypatch.setattr(
        module,
        "write_monitor_logs",
        lambda artifact, **kwargs: calls.append(dict(artifact)),
    )

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=500,
            roi_trials=0,
            shadow_trials=None,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            fast_n_trials=10,
            source_name="windivert",
        ),
        tables=object(),
    )

    monitor.accept_row({"SortID": 1, "Direct": "REV"})
    monitor._process_snapshot(force=False)
    assert calls == []


def test_process_if_due_triggers_on_pending_rows_without_schedule_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """0x0077 heartbeats append rows but skip schedule_process; inference must still run."""
    module = _module()
    monkeypatch.delenv(module.FATBEANS_DISABLE_MONITOR_HEARTBEAT_REFRESH, raising=False)
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )
    calls: list[bool] = []
    monitor._process_snapshot = lambda *, force=False, expected_schedule_owner=None: calls.append(force)

    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    monitor._process_if_due()
    assert calls == [False]
    monitor._last_processed_count = 1

    monitor.accept_row(
        {"SortID": 2, "Direct": "REV", "MessageID": "0x0077"},
        schedule_process=False,
    )
    monitor._process_if_due()
    assert calls == [False, False]


def test_monitor_heartbeat_refresh_rollback_skips_pending_row_schedule(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setenv(module.FATBEANS_DISABLE_MONITOR_HEARTBEAT_REFRESH, "1")
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )
    calls: list[bool] = []
    monitor._process_snapshot = lambda *, force=False, expected_schedule_owner=None: calls.append(force)

    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    monitor._process_if_due()
    monitor._last_processed_count = 1
    monitor.accept_row(
        {"SortID": 2, "Direct": "REV", "MessageID": "0x0077"},
        schedule_process=False,
    )
    monitor._process_if_due()
    assert calls == [False]


def test_semantic_skip_refreshes_snapshot_age_when_rows_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.delenv(module.FATBEANS_DISABLE_MONITOR_HEARTBEAT_REFRESH, raising=False)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    latest = log_dir / "latest_snapshot.json"
    old_created = 1_000.0
    session_id = "2401:9000000000000017"
    signature = (session_id, 2401, 1, None, None, (), (), (), ())

    latest.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "phase": "reading",
                "created_at": old_created,
            }
        ),
        encoding="utf-8",
    )

    build_calls = 0

    def fake_build(*_args, **_kwargs):
        nonlocal build_calls
        build_calls += 1
        return {
            "created_at": time.time(),
            "session_id": session_id,
            "phase": "reading",
        }

    monkeypatch.setattr(
        module,
        "_semantic_signature_from_payload",
        lambda _payload: signature,
    )
    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(module, "write_monitor_logs", lambda *args, **kwargs: None)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            fast_n_trials=10,
            source_name="npcap",
        ),
        tables=object(),
    )

    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=False)
    assert build_calls == 1

    latest.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "phase": "reading",
                "created_at": old_created,
            }
        ),
        encoding="utf-8",
    )
    monitor.accept_row(
        {"SortID": 2, "Direct": "REV", "MessageID": "0x0077"},
        schedule_process=False,
    )
    monitor._process_snapshot(force=False)

    assert build_calls == 1
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["created_at"] > old_created
    assert payload.get("snapshot_heartbeat_at") is not None


def test_refresh_snapshot_heartbeat_skips_cross_session(tmp_path: Path) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    latest = log_dir / "latest_snapshot.json"
    latest.write_text(
        json.dumps(
            {
                "session_id": "2401:old",
                "phase": "reading",
                "created_at": 1000.0,
            }
        ),
        encoding="utf-8",
    )
    assert not module._refresh_latest_snapshot_heartbeat(
        log_dir,
        semantic_signature=("2405:new", 2405, 1, None, None, (), (), (), ()),
    )
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["created_at"] == 1000.0


def test_refresh_snapshot_heartbeat_skips_settled_phase(tmp_path: Path) -> None:
    module = _module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    latest = log_dir / "latest_snapshot.json"
    latest.write_text(
        json.dumps(
            {
                "session_id": "2401:1",
                "phase": "settled",
                "created_at": 1000.0,
            }
        ),
        encoding="utf-8",
    )
    assert not module._refresh_latest_snapshot_heartbeat(
        log_dir,
        semantic_signature=("2401:1", 2401, 5, None, None, (), (), (), ()),
    )


def test_webhook_monitor_refreshes_bids_without_second_inference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from test_live_refresh_signatures import _bid_capture_payload

    module = _module()
    session = "2401:9000000000000017"
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    build_calls = 0

    def fake_build(payload: str, *, file: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal build_calls
        build_calls += 1
        sig = module._semantic_signature_from_payload(payload)
        return {
            "session_id": session,
            "phase": "bidding",
            "balanced": 555555,
            "inference_signature": list(sig or ()),
            "bid_rows": [{"当前最高": ""}],
            "formal_mode": "v3_practical",
            "created_at": time.time(),
            "latest_bids": {},
        }

    def fake_write(artifact: dict[str, Any], *, log_dir: Path, **kwargs: Any) -> None:
        module._atomic_write_json(log_dir / "latest_snapshot.json", artifact)

    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fake_build)
    monkeypatch.setattr(module, "write_monitor_logs", fake_write)

    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=500,
            roi_trials=0,
            shadow_trials=None,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
            fast_n_trials=10,
            archive_round_snapshots=False,
        ),
        tables=object(),
    )

    row1 = json.loads(_bid_capture_payload(session, sort_id=1, leader_value=100_000))[0]
    monitor.accept_row(row1, schedule_process=True)
    monitor._process_snapshot(force=False)
    assert build_calls == 1

    row2 = json.loads(_bid_capture_payload(session, sort_id=2, leader_value=400_000))[0]
    monitor.accept_row(row2, schedule_process=True)
    monitor._process_snapshot(force=False)
    assert build_calls == 1

    snap = json.loads((log_dir / "latest_snapshot.json").read_text(encoding="utf-8"))
    assert snap["balanced"] == 555555
    assert snap["latest_bids"]["Carol"] == 400_000
    assert snap["bid_rows"][0]["当前最高"] == "Carol 400,000"


def _stuck_monitor(module, tmp_path: Path):
    return module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
        ),
        tables=object(),
    )


def test_maybe_dump_stuck_session_captures_frames_when_no_inference(tmp_path: Path) -> None:
    # 诊断: 会话重置后帧累积却从未推理(卡死态)→ 把累积帧 dump 到 raw/archive/stuck/,
    # 供导出诊断包后回放坐实"卡在上一局结算"的根因。
    module = _module()
    monitor = _stuck_monitor(module, tmp_path)
    monitor._rows = [{"SortID": i, "MessageID": "0x0001", "Direct": "SEND"} for i in range(5)]
    monitor._last_processed_count = 0            # reset 后从未产出快照
    monitor._last_reset_at = time.monotonic() - 20.0  # 20s 前重置过, 超过 8s 判定窗
    monitor._stuck_dump_done = False
    monitor._maybe_dump_stuck_session()
    stuck_dir = tmp_path / "raw" / "archive" / "stuck"
    dumps = list(stuck_dir.glob("stuck_*.json")) if stuck_dir.is_dir() else []
    assert len(dumps) == 1, dumps
    assert len(json.loads(dumps[0].read_text(encoding="utf-8"))) == 5
    assert monitor._stuck_dump_done is True
    # 同一次 reset 不重复 dump
    monitor._maybe_dump_stuck_session()
    assert len(list(stuck_dir.glob("stuck_*.json"))) == 1


def test_maybe_dump_stuck_session_skips_when_inference_happened(tmp_path: Path) -> None:
    # 负例: reset 后已产出过快照(processed>0)= 正常不卡 → 不 dump。
    module = _module()
    monitor = _stuck_monitor(module, tmp_path)
    monitor._rows = [{"SortID": i} for i in range(5)]
    monitor._last_processed_count = 3            # 已产出过快照
    monitor._last_reset_at = time.monotonic() - 20.0
    monitor._stuck_dump_done = False
    monitor._maybe_dump_stuck_session()
    stuck_dir = tmp_path / "raw" / "archive" / "stuck"
    assert not stuck_dir.exists() or not list(stuck_dir.glob("*.json"))
