"""Controlled lifecycle fixtures; never start the product or access real SCM."""
import inspect
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_wss_live_monitor as wss


def test_known_red_game_exit_with_ui_open(tmp_path):
    states = iter([True, False])
    guard = wss.WssIdleLifecycleGuard(
        process_name="fixture.exe", overlay_state_path=tmp_path / "ui.json",
        policy_state_path=tmp_path / "policy.json", status_path=tmp_path / "status.json",
        game_state_reader=lambda _: next(states), ui_state_reader=lambda _: True,
        policy_state_reader=lambda _: True, poll_seconds=0.05,
    )
    assert not guard.tick(0)
    assert guard.tick(1), "A bound running game exit must stop even while UI stays open"


def test_known_red_capture_shutdown_precedes_worker_wait():
    source = inspect.getsource(wss.main)
    assert "monitor.stop()" not in source[source.index("    except KeyboardInterrupt:"):], (
        "Unbudgeted five-second worker wait currently precedes capture shutdown"
    )


def bound_guard(tmp_path, evidence, alive, ui=True, enabled=True):
    return wss.WssIdleLifecycleGuard(
        process_name="fixture.exe", overlay_state_path=tmp_path / "ui.json",
        policy_state_path=tmp_path / "policy.json", status_path=tmp_path / "status.json",
        launch_id="fixture-launch", monitor_started_at_unix_ms=1234,
        game_evidence_reader=lambda: evidence[0], bound_game_reader=lambda *identity: alive[0],
        ui_state_reader=lambda _: ui, policy_state_reader=lambda _: True,
        poll_seconds=0.05, enabled=enabled,
    )


@pytest.mark.parametrize("ui", [True, False, None])
def test_exact_instance_exit_ignores_ui_and_is_latched(tmp_path, ui):
    evidence, alive = [(True, 100, 123.0)], [True]
    guard = bound_guard(tmp_path, evidence, alive, ui)
    assert not guard.tick(0)
    alive[0] = False
    evidence[0] = (True, 101, 124.0)  # Fast relaunch must never be adopted.
    assert guard.tick(0.1)
    assert guard.bound_game == (100, 123.0)
    alive[0] = True
    assert guard.tick(0.2)
    assert guard.exit_observed_monotonic == 0.1


def test_waiting_unknown_and_second_game_do_not_prove_bound_exit(tmp_path):
    evidence, alive = [(False, None, None)], [None]
    guard = bound_guard(tmp_path, evidence, alive, False)
    for now in range(20):
        assert not guard.tick(now)
    evidence[0] = (True, 100, None)
    assert not guard.tick(21)
    assert guard.bound_game is None
    evidence[0] = (True, 100, 123.0)
    alive[0] = True
    assert not guard.tick(22)
    evidence[0] = (True, 101, 124.0)
    assert not guard.tick(23)  # Original is also still alive.
    alive[0] = None
    assert not guard.tick(24)
    assert guard.phase == "game_unknown"


def test_bound_identity_reader_recycled_and_access_denied(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 5.0))
    assert wss._bound_game_running_state(111, 4.0) is False
    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", denied)
    assert wss._bound_game_running_state(111, 4.0) is None


def test_capture_reclaimed_before_slow_original_worker_and_total_budget(tmp_path, monkeypatch):
    order = []
    child = subprocess.Popen([sys.executable, "-B", "-c", "import time;time.sleep(20)"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    monkeypatch.setattr(wss, "_stop_capture_job", lambda *args: "empty")
    monkeypatch.setattr(wss, "close_windows_job_handle", lambda job: None)
    def stop(timeout):
        assert child.poll() is not None
        order.append("worker_wait")
        time.sleep(timeout)
        return False
    monitor = SimpleNamespace(request_stop=lambda: order.append("request"), stop=stop)
    preloader = SimpleNamespace(release=lambda **kwargs: order.append("service") or "not_owned")
    try:
        result = wss._shutdown_capture_group(monitor=monitor, child=child, child_job=object(),
            preloader=preloader, budget_seconds=0.25, ui_result_reader=lambda: "not_running")
        assert result["components"]["helper"] == "exited"
        assert result["components"]["monitor_worker"] == "remaining"
        assert result["complete"] is False
        assert result["elapsed_seconds"] < 0.5
        assert order == ["request", "service", "worker_wait"]
        print(json.dumps(result))
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)


def test_helper_exception_does_not_skip_service_worker_or_honest_failure(monkeypatch):
    monkeypatch.setattr(wss, "_stop_capture_job", lambda *args: "unknown")
    monkeypatch.setattr(wss, "close_windows_job_handle", lambda job: None)
    def denied(*args, **kwargs):
        raise OSError("fixture denial")
    child = SimpleNamespace(poll=lambda: None, terminate=denied, wait=denied)
    result = wss._shutdown_capture_group(
        monitor=SimpleNamespace(request_stop=lambda: None, stop=lambda **kwargs: True),
        child=child, child_job=1, preloader=SimpleNamespace(release=lambda **kwargs: "unknown"),
        budget_seconds=0, ui_result_reader=lambda: "unknown")
    assert result["components"]["helper"] == "remaining"
    assert result["components"]["monitor_worker"] == "stopped"
    assert result["complete"] is False


def test_release_deadline_never_runs_scm_after_expiry():
    calls = []
    preloader = wss.WinDivertCompatibilityPreloader(runner=lambda cmd: calls.append(cmd))
    preloader._release_deadline = time.monotonic() - 1
    assert preloader._run(["sc.exe", "stop", "fixture"]) is None
    assert calls == []
    assert preloader.release_result == "deadline_exceeded"


@pytest.fixture
def overlay_module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hero_ref" / "tools"))
    import ahmad_tk_overlay
    return ahmad_tk_overlay


def request_payload():
    return dict(group_exit_schema_version=1, owner_kind="wss_monitor_game_exit_group",
                launch_id="launch", monitor_pid=101, monitor_started_at_unix_ms=10001,
                phase="stop_requested", stop_reason="bound_game_exited")


@pytest.mark.parametrize("change", [
    {"launch_id": "old"}, {"monitor_pid": 102}, {"monitor_started_at_unix_ms": 10002},
    {"monitor_pid": True}, {"group_exit_schema_version": True}, {"owner_kind": "foreign"},
    {"phase": "stopped"}, {"stop_reason": "helper_failed"},
])
def test_ui_rejects_stale_foreign_or_malformed_group_request(tmp_path, overlay_module, change):
    (tmp_path / "startup_session.json").write_text(json.dumps({"launch_id": "launch"}))
    payload = request_payload()
    payload.update(change)
    (tmp_path / "lifecycle_status.json").write_text(json.dumps(payload))
    assert overlay_module._game_exit_group_request(tmp_path, "launch", {101: 10001}) is None


def test_ui_accepts_bound_request_but_never_adopts_new_launch(tmp_path, overlay_module):
    (tmp_path / "startup_session.json").write_text(json.dumps({"launch_id": "launch"}))
    (tmp_path / "lifecycle_status.json").write_text(json.dumps(request_payload()))
    assert overlay_module._game_exit_group_request(tmp_path, "launch", {101: 10001})
    (tmp_path / "startup_session.json").write_text(json.dumps({"launch_id": "new"}))
    assert overlay_module._game_exit_group_request(tmp_path, "launch", {101: 10001}) is None


def test_ui_normal_exit_uses_existing_cleanup_without_monitor_kill_or_modal(tmp_path, overlay_module):
    (tmp_path / "startup_session.json").write_text(json.dumps({"launch_id": "launch"}))
    (tmp_path / "lifecycle_status.json").write_text(json.dumps(request_payload()))
    events = []
    obj = SimpleNamespace(snapshot_path=tmp_path / "snapshot.json", _group_exit_launch_id="launch",
        _group_exit_monitor_identities={101: 10001}, keep_monitor_on_close=False,
        root=SimpleNamespace(destroy=lambda: events.append("destroy")),
        _clear_compute_pending=lambda reason: events.append(reason),
        _save_ui_prefs_if_enabled=lambda: None, _destroy_pet_companion=lambda: None,
        _run_exit_cleanup=lambda: events.append("cleanup"),
        _notify_watched_monitor_exit=lambda: pytest.fail("normal exit must not show modal"))
    obj._group_exit_pools_reaped = True
    obj._write_game_exit_ack = lambda: overlay_module.AhmadTkOverlay._write_game_exit_ack(obj)
    obj._finish_ui_exit = lambda: overlay_module.AhmadTkOverlay._finish_ui_exit(obj)
    overlay_module.AhmadTkOverlay._poll_game_exit_group(obj)
    assert obj.keep_monitor_on_close is True
    assert events == ["bound_game_exited", "cleanup", "destroy"]
    assert json.loads((tmp_path / "lifecycle_ui_exit.json").read_text())["pools_reaped"] is True


def make_monitor(tmp_path):
    from test_fatbeans_webhook_monitor import _module
    from bidking_lab.live.monitor import MonitorTables
    module = _module()
    return module.FatbeansWebhookMonitor(config=module.WebhookMonitorConfig(
        log_dir=tmp_path / "logs", raw_dir=tmp_path / "raw", process_name="fixture.exe",
        server_ports=(10000,), n_trials=20, roi_trials=0, shadow_trials=1,
        full_shadow_trials=1, run_debug_shadows=False, seed=1,
        archive_round_snapshots=False), tables=MonitorTables({}, {}, {}, warehouse_game_version="fixture"))


def test_stop_short_budget_keeps_original_non_daemon_flush_owner(tmp_path, monkeypatch):
    monitor = make_monitor(tmp_path)
    release = threading.Event()
    entered = threading.Event()
    calls = []
    def flush(**kwargs):
        calls.append("flush")
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(monitor, "_process_snapshot", flush)
    original = monitor._worker
    try:
        started = time.monotonic()
        assert monitor.stop(timeout=0.05) is False
        assert time.monotonic() - started < 0.2
        assert entered.wait(0.3)
        assert original.is_alive() and original.daemon is False
        assert monitor.stop(timeout=0.01) is False
        release.set()
        assert monitor.stop(timeout=2) is True
        assert monitor._worker is original and calls == ["flush"]
    finally:
        release.set()
        monitor.stop(timeout=3)


def test_failed_tail_write_retains_owner_until_recovery(tmp_path, monkeypatch):
    monitor = make_monitor(tmp_path)
    recover = threading.Event()
    attempts = []
    def flush(**kwargs):
        attempts.append(time.monotonic())
        if not recover.is_set():
            raise PermissionError("fixture write denied")
    monkeypatch.setattr(monitor, "_process_snapshot", flush)
    try:
        assert monitor.stop(timeout=0.06) is False
        assert monitor._worker.is_alive()
        assert not monitor._shutdown_complete.is_set()
        assert monitor._shutdown_stage == "flushing_fast_failed"
        recover.set()
        assert monitor.stop(timeout=1) is True
        assert len(attempts) >= 2
    finally:
        recover.set()
        monitor.stop(timeout=3)


def test_stop_does_not_wait_for_slow_snapshot_state_lock(tmp_path):
    monitor = make_monitor(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def held():
        with monitor._lock:
            entered.set()
            release.wait(3)
    locker = threading.Thread(target=held)
    locker.start()
    try:
        assert entered.wait(1)
        start = time.monotonic()
        monitor.request_stop()
        assert monitor.stop(timeout=0.02) is False
        assert time.monotonic() - start < 0.2
        assert monitor._stop.is_set()
    finally:
        release.set()
        locker.join(1)
        assert monitor.stop(timeout=2)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows owned Job fixture")
def test_actual_owned_child_job_empties_before_close():
    child = subprocess.Popen([sys.executable, "-B", "-c", "import time;time.sleep(20)"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    job = None
    try:
        job = wss.assign_windows_kill_on_close_job(child)
        start = time.monotonic()
        measured = time.perf_counter()
        assert wss._stop_capture_job(job, start + 1) == "empty"
        child.wait(timeout=1)
        assert child.poll() is not None
        print(json.dumps({"real_owned_job_empty_seconds": time.perf_counter() - measured}))
    finally:
        wss.close_windows_job_handle(job)
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)


def test_group_completion_requires_every_component_readback(monkeypatch):
    monkeypatch.setattr(wss, "_stop_capture_job", lambda *args: "empty")
    monkeypatch.setattr(wss, "close_windows_job_handle", lambda job: None)
    args = dict(monitor=SimpleNamespace(request_stop=lambda: None, stop=lambda **kwargs: True),
        child=SimpleNamespace(poll=lambda: 0, wait=lambda **kwargs: 0), child_job=1,
        preloader=SimpleNamespace(release=lambda **kwargs: "absent"), budget_seconds=0.1)
    result = wss._shutdown_capture_group(**args, ui_result_reader=lambda: "exited")
    assert result["resources_complete"] is True
    assert result["complete"] is False and result["owner_process_state"] == "exit_pending"
    assert wss._shutdown_capture_group(**args, ui_result_reader=lambda: "unknown")["resources_complete"] is False


@pytest.mark.parametrize("scenario,expected", [
    ("absent", "absent"), ("still_present", "remaining"), ("query_error", "unknown"),
    ("replace_before_stop", "cleanup_failed"), ("replace_before_delete", "cleanup_failed"),
    ("access_denied", "cleanup_failed"),
])
def test_release_rechecks_exact_owned_service_and_requires_absence(tmp_path, scenario, expected):
    from test_wss_capture_bridge import _sc_driver_config, _sc_result
    @contextmanager
    def owned_lock():
        yield True
    calls = []
    queries = 0
    driver = tmp_path / "fixture.sys"
    def runner(command):
        nonlocal queries
        calls.append(command[1])
        assert command[2] == "BidKingWinDivertR1"
        if command[1] == "qc":
            queries += 1
            changed = scenario == "replace_before_stop" or (scenario == "replace_before_delete" and queries == 2)
            return _sc_driver_config(str(driver) + (".foreign" if changed else ""))
        if command[1] == "stop" and scenario == "access_denied":
            return _sc_result(5, "[SC] OpenService FAILED 5")
        if command[1] == "query":
            if scenario == "still_present":
                return _sc_result(0, "STATE : 1 STOPPED")
            if scenario == "query_error":
                raise OSError("fixture query failure")
            return _sc_result(1060, "[SC] OpenService FAILED 1060")
        return _sc_result(0)
    preloader = wss.WinDivertCompatibilityPreloader(runner=runner, lock_factory=owned_lock)
    preloader._claim_service_ownership("created", driver)
    preloader.state = "preloaded"
    assert preloader.release(deadline=time.monotonic() + 1) == expected
    if scenario == "replace_before_stop":
        assert calls == ["qc", "query"]
    elif scenario == "replace_before_delete":
        assert calls == ["qc", "stop", "qc", "query"]
    elif scenario == "access_denied":
        assert "delete" not in calls
    else:
        assert calls == ["qc", "stop", "qc", "delete", "query"]


def test_ui_ack_needs_exact_ui_and_monitor_identity_before_exit_readback(tmp_path, monkeypatch):
    evidence, alive = [(True, 100, 123.0)], [True]
    guard = bound_guard(tmp_path, evidence, alive)
    guard.last_ui_state = False
    assert wss._group_ui_exit_state(guard) == "unknown"
    guard.last_ui_state = True
    guard.bound_ui = (432, 567.0)
    ack = dict(group_exit_schema_version=1, launch_id=guard.launch_id,
               monitor_pid=wss.os.getpid(), monitor_started_at_unix_ms=1234,
               ui_pid=432, ui_started_at=567.0, pools_reaped=True)
    path = tmp_path / "lifecycle_ui_exit.json"
    calls = []
    monkeypatch.setattr(wss, "_bound_game_running_state", lambda *args: calls.append(args) or False)
    path.write_text(json.dumps(ack))
    assert wss._group_ui_exit_state(guard) == "exited"
    assert calls == [(432, 567.0)]
    for key, value in (("launch_id", "old"), ("monitor_started_at_unix_ms", 1235),
                       ("ui_pid", 433), ("ui_started_at", 568.0), ("pools_reaped", False)):
        calls.clear()
        path.write_text(json.dumps(dict(ack, **{key: value})))
        assert wss._group_ui_exit_state(guard) == "unknown"
        assert calls == []


def test_ui_pool_shutdown_denial_is_never_acknowledged_as_reaped(overlay_module):
    class Process:
        def terminate(self):
            raise OSError("denied")
        def kill(self):
            raise OSError("denied")
        def join(self, **kwargs):
            pass
        def is_alive(self):
            return True
    pool = SimpleNamespace(_processes={1: Process()}, shutdown=lambda **kwargs: None)
    assert overlay_module._shutdown_ref_engine_executor(pool, hard=True) is False
