from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location("overlay_pid_liveness_0_3_1", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SequenceProbe:
    def __init__(self, states: list[str]) -> None:
        self.states = list(states)
        self.closed = False

    def poll(self) -> str:
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def close(self) -> None:
        self.closed = True


class _Kernel32:
    def __init__(self, *, handle: int = 99, wait_results: list[int] | None = None) -> None:
        self.handle = handle
        self.wait_results = list(wait_results or [0x00000102])
        self.open_calls: list[tuple[int, bool, int]] = []
        self.closed: list[int] = []

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
        self.open_calls.append((access, inherit, pid))
        return self.handle

    def WaitForSingleObject(self, handle: int, _timeout: int) -> int:
        assert handle == self.handle
        if len(self.wait_results) > 1:
            return self.wait_results.pop(0)
        return self.wait_results[0]

    def CloseHandle(self, handle: int) -> int:
        self.closed.append(handle)
        return 1


def test_psutil_query_exception_is_unknown_not_dead() -> None:
    module = _overlay_module()

    class QueryError(Exception):
        pass

    psutil = SimpleNamespace(
        pid_exists=lambda _pid: True,
        Process=lambda _pid: (_ for _ in ()).throw(QueryError("private detail")),
    )

    state, create_time = module._psutil_pid_liveness(4242, psutil_module=psutil)

    assert state == module._PID_UNKNOWN
    assert create_time is None


def test_explicit_missing_pid_is_dead() -> None:
    module = _overlay_module()
    psutil = SimpleNamespace(pid_exists=lambda _pid: False)

    state, create_time = module._psutil_pid_liveness(4242, psutil_module=psutil)

    assert state == module._PID_DEAD
    assert create_time is None


def test_windows_limited_handle_binds_identity_and_reports_liveness() -> None:
    module = _overlay_module()
    kernel32 = _Kernel32(wait_results=[0x00000102, 0x00000000])

    handle = module._open_windows_process_handle(4242, kernel32=kernel32)

    assert handle is not None
    assert handle.poll() == module._PID_ALIVE
    assert handle.poll() == module._PID_DEAD
    handle.close()
    assert kernel32.open_calls == [(0x00101000, False, 4242)]
    assert kernel32.closed == [99]


def test_unknown_or_nonconsecutive_dead_never_confirms_exit() -> None:
    module = _overlay_module()
    probe = _SequenceProbe(
        [
            module._PID_DEAD,
            module._PID_UNKNOWN,
            module._PID_DEAD,
            module._PID_ALIVE,
        ]
    )
    group = module._WatchedPidGroup((4242,), probe_factory=lambda _pid: probe)

    assert group.poll() == module._PID_UNKNOWN
    assert group.poll() == module._PID_UNKNOWN
    assert group.poll() == module._PID_UNKNOWN
    assert group.poll() == module._PID_ALIVE


def test_two_consecutive_identity_bound_dead_observations_confirm_exit() -> None:
    module = _overlay_module()
    probe = _SequenceProbe([module._PID_DEAD, module._PID_DEAD])
    group = module._WatchedPidGroup((4242,), probe_factory=lambda _pid: probe)

    assert group.poll() == module._PID_UNKNOWN
    assert group.poll() == module._PID_DEAD


def test_create_time_change_marks_original_process_dead() -> None:
    module = _overlay_module()

    class Process:
        def is_running(self) -> bool:
            return True

        def create_time(self) -> float:
            return 2000.0

    psutil = SimpleNamespace(
        pid_exists=lambda _pid: True,
        Process=lambda _pid: Process(),
    )

    state, observed = module._psutil_pid_liveness(
        4242,
        expected_create_time=1000.0,
        psutil_module=psutil,
    )

    assert state == module._PID_DEAD
    assert observed == 2000.0


def test_probe_handles_access_denied_without_false_exit() -> None:
    module = _overlay_module()

    class AccessDenied(Exception):
        pass

    psutil = SimpleNamespace(
        AccessDenied=AccessDenied,
        pid_exists=lambda _pid: True,
        Process=lambda _pid: (_ for _ in ()).throw(AccessDenied()),
    )
    probe = module._WatchedPidProbe(
        4242,
        handle_factory=lambda _pid: None,
        psutil_module=psutil,
    )
    group = module._WatchedPidGroup((4242,), probe_factory=lambda _pid: probe)

    assert group.poll() == module._PID_UNKNOWN
    assert group.poll() == module._PID_UNKNOWN
    assert group.exited() is False


def test_exit_cleanup_closes_identity_handle_even_when_monitor_is_kept() -> None:
    module = _overlay_module()
    probe = _SequenceProbe([module._PID_ALIVE])
    group = module._WatchedPidGroup((4242,), probe_factory=lambda _pid: probe)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._exit_cleanup_done = False
    overlay._watched_pid_group = group
    overlay.keep_monitor_on_close = True

    module.AhmadTkOverlay._run_exit_cleanup(overlay)

    assert probe.closed is True
    assert overlay._exit_cleanup_done is True


def test_real_refresh_path_waits_for_two_explicit_dead_polls(tmp_path: Path) -> None:
    module = _overlay_module()
    probe = _SequenceProbe([module._PID_DEAD, module._PID_DEAD])
    group = module._WatchedPidGroup((4242,), probe_factory=lambda _pid: probe)

    class Root:
        def __init__(self) -> None:
            self.destroyed = False
            self.after_calls: list[tuple[int, object]] = []

        def after(self, interval: int, callback: object) -> None:
            self.after_calls.append((interval, callback))

        def destroy(self) -> None:
            self.destroyed = True

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 300
    overlay.exit_when_pids = (4242,)
    overlay._watched_pid_group = group
    overlay._watched_pid_exited_fn = group.exited
    overlay._exit_cleanup_done = False
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.diagnostic_profile = "portable"
    overlay._ui_interaction_busy = lambda: False
    overlay._drain_live_summary_results = lambda: None
    overlay._drain_manual_summary_results = lambda: None
    overlay._update_activation_ui = lambda: True
    overlay._render_degraded = lambda: None
    overlay._record_ui_runtime_status = lambda *_args, **_kwargs: None
    overlay._notify_watched_monitor_exit = lambda: None
    overlay._save_ui_prefs_if_enabled = lambda: None
    overlay._destroy_pet_companion = lambda: None
    def completed_cleanup() -> None:
        overlay._exit_cleanup_done = True
        overlay._group_exit_pools_reaped = True
    overlay._run_exit_cleanup = completed_cleanup

    module.AhmadTkOverlay.refresh(overlay)

    assert overlay.root.destroyed is False
    assert len(overlay.root.after_calls) == 1

    module.AhmadTkOverlay.refresh(overlay)

    assert overlay.root.destroyed is True
    assert len(overlay.root.after_calls) == 1
