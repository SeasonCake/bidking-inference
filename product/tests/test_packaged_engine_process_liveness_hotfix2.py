from __future__ import annotations

import copy
import importlib.util
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location("ahmad_tk_overlay", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeProcess:
    def __init__(self) -> None:
        self.terminated = 0
        self.killed = 0
        self.joined: list[float] = []
        self._alive = True

    def terminate(self) -> None:
        self.terminated += 1
        self._alive = False

    def kill(self) -> None:
        self.killed += 1
        self._alive = False

    def join(self, timeout: float) -> None:
        self.joined.append(timeout)

    def is_alive(self) -> bool:
        return self._alive


class _FakeExecutor:
    def __init__(self, future: Any | None = None, *, futures: list[Any] | None = None) -> None:
        self.future = future
        self.futures = list(futures or [])
        self.process = _FakeProcess()
        self._processes = {1: self.process}
        self.shutdown_calls: list[tuple[bool, bool]] = []
        self.submissions: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        self.submissions.append((fn, args, dict(kwargs)))
        if self.futures:
            return self.futures.pop(0)
        if self.future is None:
            raise AssertionError("unexpected submit")
        return self.future

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


class _SlowProbeFuture:
    def __init__(self, module: Any, delay: float) -> None:
        self.module = module
        self.delay = delay

    def result(self, timeout: float | None = None) -> tuple[str, int, int]:
        assert timeout is not None
        time.sleep(min(self.delay, timeout + 0.01))
        if self.delay > timeout:
            raise TimeoutError("injected probe hang")
        return self.module.REF_ENGINE_CHILD_LIVENESS_IDENTITY


class _GatedProbeFuture:
    def __init__(self, module: Any) -> None:
        self.module = module
        self.entered = threading.Event()
        self.release = threading.Event()

    def result(self, timeout: float | None = None) -> tuple[str, int, int]:
        self.entered.set()
        assert self.release.wait(timeout=timeout)
        return self.module.REF_ENGINE_CHILD_LIVENESS_IDENTITY


class _BlockingPutQueue(queue.Queue):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def put(
        self,
        item: Any,
        block: bool = True,
        timeout: float | None = None,
    ) -> None:
        self.entered.set()
        assert self.release.wait(timeout=2.0)
        super().put(item, block=block, timeout=timeout)


class _BlockingFactory:
    def __init__(self, executor: Any) -> None:
        self.executor = executor
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, _overlay: Any) -> Any:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=2.0)
        return self.executor


class _SlowFactory:
    """Deterministic child-pool bootstrap delay for the Tk-thread boundary."""

    def __init__(self, executor: Any, delay: float = 0.15) -> None:
        self.executor = executor
        self.delay = delay
        self.entered = threading.Event()
        self.calls = 0

    def __call__(self, _overlay: Any) -> Any:
        self.calls += 1
        self.entered.set()
        time.sleep(self.delay)
        return self.executor


class _CallbackFuture:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.value = result
        self.error = error
        self.callback: Any = None

    def result(self, timeout: float | None = None) -> Any:
        del timeout
        if self.error is not None:
            raise self.error
        return self.value

    def add_done_callback(self, callback: Any) -> None:
        self.callback = callback

    def finish(self) -> None:
        assert self.callback is not None
        self.callback(self)


def _bare_overlay(module: Any) -> Any:
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = Path("latest_snapshot.json")
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = False
    overlay._summary_worker_seq = 0
    overlay._summary_worker_signature = None
    overlay._summary_worker_pending = None
    overlay._summary_worker_mode = None
    overlay._summary_worker_executor = None
    overlay._ref_engine_executor = None
    overlay._ref_engine_transient_executor = None
    overlay._ref_engine_executor_mode = None
    overlay._ref_engine_liveness_state = "not_started"
    overlay._ref_engine_liveness_results = queue.Queue()
    overlay._ref_engine_liveness_generation = 0
    overlay._ref_engine_liveness_lock = threading.Lock()
    overlay._ref_engine_fallback_attempted_signature = None
    overlay._ref_engine_pool_disabled = False
    overlay._last_inference_signature = None
    overlay._last_signature = None
    overlay._exit_cleanup_done = False
    overlay._record_ui_health = lambda _row: None
    overlay._record_ui_runtime_status = lambda _event, **_kwargs: None
    return overlay


def test_packaged_liveness_warmup_is_async_and_does_not_block_ui_tick(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    future = _SlowProbeFuture(module, delay=0.08)
    executor = _FakeExecutor(future)
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: executor,
    )
    monkeypatch.setattr(module, "REF_ENGINE_LIVENESS_TIMEOUT_SECONDS", 0.5)

    started = time.perf_counter()
    module.AhmadTkOverlay._start_ref_engine_liveness_warmup(overlay)
    returned_in = time.perf_counter() - started

    assert returned_in < 0.05
    heartbeat: list[float] = []
    time.sleep(0.01)
    heartbeat.append(time.perf_counter())
    assert heartbeat

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        module.AhmadTkOverlay._drain_ref_engine_liveness_results(overlay)
        if overlay._ref_engine_liveness_state == "ready":
            break
        time.sleep(0.01)
    assert overlay._ref_engine_liveness_state == "ready"
    assert overlay._ref_engine_executor is executor
    assert executor.process.terminated == 0


def test_packaged_liveness_hang_is_hard_terminated_and_never_falls_to_thread(
    monkeypatch,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    future = _SlowProbeFuture(module, delay=0.2)
    executor = _FakeExecutor(future)
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: executor,
    )
    monkeypatch.setattr(module, "REF_ENGINE_LIVENESS_TIMEOUT_SECONDS", 0.02)
    thread_starts: list[str] = []
    real_thread = module.threading.Thread

    def _thread(*args: Any, **kwargs: Any) -> Any:
        thread_starts.append(str(kwargs.get("name") or "thread"))
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(
        module.threading,
        "Thread",
        _thread,
    )

    module.AhmadTkOverlay._start_ref_engine_liveness_warmup(overlay)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        module.AhmadTkOverlay._drain_ref_engine_liveness_results(overlay)
        if overlay._ref_engine_liveness_state == "failed":
            break
        time.sleep(0.01)

    assert overlay._ref_engine_liveness_state == "failed"
    assert executor.process.terminated == 1
    assert executor.shutdown_calls == [(False, True)]
    assert thread_starts == ["hero-ref-engine-liveness"]


def test_packaged_ready_executor_is_used_instead_of_legacy_thread(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    executor = _FakeExecutor()
    overlay._ref_engine_liveness_state = "ready"
    overlay._ref_engine_executor = executor
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.delenv("HERO_REF_DISABLE_PROCESS_ENGINE", raising=False)

    assert module.AhmadTkOverlay._ensure_ref_engine_executor(overlay) is executor
    assert overlay._ref_engine_executor_mode == "process"


def test_process_timeout_terminates_worker_not_only_future(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    executor = _FakeExecutor()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 2
    overlay._summary_worker_signature = (2, 2)
    overlay._summary_worker_started_wall = 0.0
    overlay._summary_worker_mode = "process"
    overlay._summary_worker_executor = executor
    overlay._ref_engine_executor = executor
    overlay._last_inference_signature = (2, 2)
    overlay._last_signature = (2, 2)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(module, "SUMMARY_PROCESS_WORKER_TIMEOUT_SECONDS", 1.0)

    module.AhmadTkOverlay._drain_live_summary_results(overlay)

    assert executor.process.terminated == 1
    assert executor.shutdown_calls == [(False, True)]
    assert overlay._ref_engine_liveness_state == "failed"


def test_source_child_probe_runs_in_a_real_process_with_fixed_identity() -> None:
    module = _overlay_module()
    executor = ProcessPoolExecutor(max_workers=1)
    try:
        future = executor.submit(module._ref_engine_child_liveness_probe, False)
        assert future.result(timeout=20.0) == module.REF_ENGINE_CHILD_LIVENESS_IDENTITY
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize(
    ("fields", "requires_native"),
    (
        (
            {
                "ProductProfile": "standard",
                "Encrypted": "False",
                "NativeBinaryDistribution": "NotRequired",
            },
            False,
        ),
        (
            {
                "ProductProfile": "standard",
                "Encrypted": "True",
                "NativeBinaryDistribution": "NotRequired",
            },
            True,
        ),
    ),
)
def test_packaged_liveness_profile_is_exact_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fields: dict[str, str], requires_native: bool
) -> None:
    module = _overlay_module()
    manifest = tmp_path / "BUILD_MANIFEST.txt"
    manifest.write_text("\n".join(f"{key}: {value}" for key, value in fields.items()), encoding="utf-8")
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    monkeypatch.setattr(module, "_require_plain_descendant", lambda *_args, **_kwargs: manifest)
    assert module._packaged_ref_engine_requires_native() is requires_native
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\nEncrypted: False", encoding="utf-8")
    assert module._packaged_ref_engine_requires_native() is True


def test_hard_shutdown_terminates_a_real_running_process() -> None:
    module = _overlay_module()
    executor = ProcessPoolExecutor(max_workers=1)
    executor.submit(time.sleep, 30.0)
    deadline = time.monotonic() + 5.0
    processes: list[Any] = []
    while time.monotonic() < deadline:
        processes = list(executor._processes.values())
        if processes and all(process.is_alive() for process in processes):
            break
        time.sleep(0.01)
    assert processes and all(process.is_alive() for process in processes)

    module._shutdown_ref_engine_executor(executor, hard=True)

    assert all(not process.is_alive() for process in processes)


def test_real_process_summary_matches_parent_for_tracked_bidding_fixture() -> None:
    module = _overlay_module()
    fixture = (
        ROOT
        / "tests"
        / "fixtures"
        / "HeroRefDiag-20260705-231932-5608_1683352518960938"
        / "round_snapshots"
        / "r05_bidding.json"
    )
    snapshot = json.loads(fixture.read_text(encoding="utf-8"))
    # Keep both arms inside the live window so wall-clock stale labels do not
    # obscure the calculation/serialization equality this gate is proving.
    snapshot["created_at"] = time.time()
    parent_result = module.summarize_snapshot(snapshot, snapshot_path=fixture)
    executor = ProcessPoolExecutor(max_workers=1)
    try:
        child_result = executor.submit(
            module.summarize_snapshot,
            snapshot,
            snapshot_path=fixture,
        ).result(timeout=30.0)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    def _without_runtime_measurements(result: dict[str, Any]) -> dict[str, Any]:
        comparable = copy.deepcopy(result)
        diagnostics = comparable.get("diagnostics")
        if isinstance(diagnostics, dict):
            diagnostics.pop("performance", None)
        return comparable

    assert _without_runtime_measurements(child_result) == _without_runtime_measurements(
        parent_result
    )


def test_process_success_applies_result_without_starting_summary_thread(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    summary = {"status": "ok", "context": {"phase": "bidding"}}
    future = _CallbackFuture(result=summary)
    executor = _FakeExecutor(future)
    overlay._ref_engine_liveness_state = "ready"
    overlay._ref_engine_executor = executor
    applied: list[tuple[dict[str, Any], dict[str, Any]]] = []
    overlay._mark_summary_performance = lambda *_args: None
    overlay._apply_live_summary = lambda snapshot, result: applied.append((snapshot, result))
    overlay._maybe_start_pending_summary_worker = lambda: None
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)

    snapshot = {"session_id": "good"}
    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot=snapshot,
        signature=(1, 1),
        started_at=time.perf_counter(),
    )
    assert overlay._summary_worker_mode == "process"
    assert len(executor.submissions) == 1

    future.finish()
    module.AhmadTkOverlay._drain_live_summary_results(overlay)

    assert applied == [(snapshot, summary)]
    assert executor.shutdown_calls == []
    assert overlay._summary_worker_running is False


def test_process_crash_hard_fences_primary_then_rewarms_off_tk(
    monkeypatch,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    primary_future = _CallbackFuture(error=RuntimeError("PRIVATE_PATH_CANARY"))
    primary = _FakeExecutor(primary_future)
    fallback = _FakeExecutor(
        futures=[_CallbackFuture(result=module.REF_ENGINE_CHILD_LIVENESS_IDENTITY)]
    )
    overlay._ref_engine_liveness_state = "ready"
    overlay._ref_engine_executor = primary
    overlay._mark_summary_performance = lambda *_args: None
    overlay._apply_live_summary = lambda *_args: None
    overlay._maybe_start_pending_summary_worker = lambda: None
    health: list[dict[str, Any]] = []
    overlay._record_ui_health = lambda row: health.append(row)
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: fallback,
    )

    snapshot = {"session_id": "crash"}
    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot=snapshot,
        signature=(2, 2),
        started_at=time.perf_counter(),
    )
    primary_future.finish()
    module.AhmadTkOverlay._drain_live_summary_results(overlay)

    assert primary.process.terminated == 1
    assert overlay._ref_engine_liveness_state == "failed"
    assert "PRIVATE_PATH_CANARY" not in str(health)

    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot=snapshot,
        signature=(2, 2),
        started_at=time.perf_counter(),
    )
    assert overlay._summary_worker_mode == "liveness"
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and overlay._ref_engine_liveness_results.empty():
        time.sleep(0.01)
    module.AhmadTkOverlay._drain_live_summary_results(overlay)

    assert overlay._ref_engine_liveness_state in {"failed", "pending", "ready"}
    assert fallback.shutdown_calls == []
    assert fallback.process.terminated == 0


def test_failed_packaged_probe_never_implicitly_runs_cpu_summary_thread(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay._ref_engine_liveness_state = "failed"
    started_threads: list[str] = []

    class _ThreadSentinel:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args
            started_threads.append(str(kwargs.get("name") or "thread"))

        def start(self) -> None:
            return

    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(module.threading, "Thread", _ThreadSentinel)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )

    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={"session_id": "no-thread"},
        signature=(3, 3),
        started_at=time.perf_counter(),
    )
    assert started_threads == ["hero-ref-engine-liveness"]
    assert overlay._summary_worker_mode == "liveness"


def test_failed_packaged_probe_rewarmup_never_creates_pool_on_tk_path(monkeypatch) -> None:
    """A slow retry must remain a pending state, not stall refresh for pool creation."""
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay._ref_engine_liveness_state = "failed"
    overlay._ref_engine_liveness_retry_at = 0.0
    executor = _FakeExecutor(_CallbackFuture(result=module.REF_ENGINE_CHILD_LIVENESS_IDENTITY))
    factory = _SlowFactory(executor)
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: factory(_self),
    )

    started = time.perf_counter()
    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={"session_id": "slow-start"},
        signature=(33, 1),
        started_at=started,
    )
    returned_in = time.perf_counter() - started

    assert returned_in < 0.08
    assert overlay._summary_worker_mode == "liveness"
    assert factory.entered.wait(timeout=0.5)
    assert executor.submissions == []


def test_failed_packaged_probe_respects_retry_backoff_for_new_inputs(monkeypatch) -> None:
    """A failed child cannot make each incoming snapshot create another fallback pool."""
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay._ref_engine_liveness_state = "failed"
    overlay._ref_engine_liveness_retry_at = 100.0
    factory_calls: list[str] = []
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: factory_calls.append("created") or _FakeExecutor(),
    )

    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={"session_id": "backoff-a"},
        signature=(34, 1),
        started_at=0.0,
    )
    assert overlay._summary_worker_mode == "liveness"
    assert factory_calls == []


def test_ethan_r5_outline_wait_keeps_only_same_session_map_monotonic_quote() -> None:
    """The R5 skill-frame gap is a labelled transition, never a cross-game stale quote."""
    module = _overlay_module()
    overlay = _bare_overlay(module)

    valid = {
        "status": "ok",
        "context": {
            "hero": "ethan",
            "session_id": "ethan-session",
            "map_id": 2401,
            "round": 4,
            "phase": "bidding",
        },
        "reference": {
            "conservative": "40,000",
            "balanced": "109,000",
            "aggressive": "1,013,071",
            "current_highest": "80,000",
            "v3_balanced": "90,000",
        },
        "ahmed_ref": {"status": "ok", "balanced": 109000, "notes": []},
        "flags": [],
    }
    assert module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(overlay, valid) is valid

    waiting = {
        "status": "ok",
        "context": {
            "hero": "ethan",
            "session_id": "ethan-session",
            "map_id": 2401,
            "round": 5,
            "phase": "bidding",
        },
        "reference": {"balanced": "-", "current_highest": "101,000", "v3_balanced": "95,000"},
        "ahmed_ref": {
            "status": "missing_total_count",
            "notes": ["hero_ref_waiting", "hero_ref_wait:全仓轮廓技能帧"],
        },
        "flags": [],
    }
    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(overlay, waiting)
    assert display["reference"]["balanced"] == "109,000"
    assert display["reference"]["current_highest"] == "101,000"
    assert display["continuity"]["reason"] == "ethan_r5_outline_updating"
    assert display["flags"][0]["label"] == "R5全仓轮廓更新中"
    assert "ethan_r5_outline_transition" in display["ahmed_ref"]["notes"]

    new_skill = dict(valid)
    new_skill["context"] = dict(valid["context"], round=5)
    new_skill["reference"] = dict(valid["reference"], balanced="120,000")
    new_skill["ahmed_ref"] = {"status": "ok", "balanced": 120000, "notes": ["skill:1002085"]}
    assert module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(overlay, new_skill) is new_skill

    error = dict(waiting)
    error["ahmed_ref"] = {"status": "error", "notes": ["ref_engine_process_failed"]}
    assert module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(overlay, error) is error
    assert overlay._last_ethan_r5_outline_quote is None

    cross_session = dict(waiting)
    cross_session["context"] = dict(waiting["context"], session_id="next-session")
    assert module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(overlay, cross_session) is cross_session

    terminal = dict(waiting)
    terminal["context"] = dict(waiting["context"], phase="settled")
    assert module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(overlay, terminal) is terminal


def test_ethan_r5_outline_wait_preserves_current_composition_schema_conflict() -> None:
    """Quote continuity must not turn a current schema conflict back into ready data."""
    module = _overlay_module()
    overlay = _bare_overlay(module)

    cached = _ethan_continuity_quote(4, 109_000)
    cached["quality_composition"] = {
        "schema_version": 1,
        "status": "ready",
        "qualities": {},
    }
    assert module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, cached
    ) is cached

    waiting = _ethan_continuity_wait()
    conflict = {
        "schema_version": 1,
        "status": "conflict",
        "reason": "结构化组合字段冲突",
        "qualities": {},
    }
    waiting["quality_composition"] = conflict

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, waiting
    )

    assert display["reference"]["balanced"] == "109,000"
    assert display["quality_composition"] is conflict
    assert module.detail_composition_view(display).status == "conflict"


class _EthanMapIdIntSubclass(int):
    pass


class _EthanListSubclass(list):
    pass


class _EthanDictSubclass(dict):
    pass


class _EthanHostileValue:
    def __bool__(self) -> bool:
        raise RuntimeError("hostile bool")

    def __eq__(self, _other: object) -> bool:
        raise RuntimeError("hostile equality")

    def __str__(self) -> str:
        raise RuntimeError("hostile string")


class _EthanHostileContext(dict):
    def get(self, _key: object, _default: object = None) -> object:
        raise RuntimeError("hostile context get")


def _ethan_continuity_quote(
    round_no: int,
    balanced: int,
    *,
    map_id: Any = 2401,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "context": {
            "hero": "ethan",
            "session_id": "ethan-authority-session",
            "map_id": map_id,
            "round": round_no,
            "phase": "bidding",
        },
        "reference": {"balanced": f"{balanced:,}", "current_highest": "80,000"},
        "ahmed_ref": {"status": "ok", "balanced": balanced, "notes": notes or []},
        "flags": [],
    }


def _ethan_continuity_wait(*, map_id: Any = 2401) -> dict[str, Any]:
    return {
        "status": "ok",
        "context": {
            "hero": "ethan",
            "session_id": "ethan-authority-session",
            "map_id": map_id,
            "round": 5,
            "phase": "bidding",
        },
        "reference": {"balanced": "-", "current_highest": "101,000"},
        "ahmed_ref": {
            "status": "missing_total_count",
            "notes": ["hero_ref_waiting", "hero_ref_wait:全仓轮廓技能帧"],
        },
        "flags": [],
    }


@pytest.mark.parametrize("late_round", [3, 2])
def test_ethan_lower_round_frame_clears_r5_continuity_authority(
    late_round: int,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(late_round, 73_000)
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert "continuity" not in display
    assert display["reference"]["balanced"] == "-"


@pytest.mark.parametrize(
    "wait_map",
    [2401.0, _EthanMapIdIntSubclass(2401), True],
)
def test_ethan_numeric_equal_wrong_type_map_cannot_cross_continuity_authority(
    wait_map: Any,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait(map_id=wait_map)
    )

    assert "continuity" not in display
    assert display["reference"]["balanced"] == "-"


def test_ethan_r5_without_strict_outline_skill_does_not_replace_r4_authority() -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(5, 222_000)
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert display["continuity"]["source_round"] == 4
    assert display["reference"]["balanced"] == "109,000"


@pytest.mark.parametrize(
    "marker_patch",
    [
        {"stale": {"reason": "snapshot_age"}},
        {"continuity": {"reason": "cached"}},
        {"performance": {"ref_cache_hit": True}},
    ],
)
def test_ethan_r5_nonlive_marker_cannot_replace_r4_authority(
    marker_patch: dict[str, Any],
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    marked = _ethan_continuity_quote(5, 222_000, notes=["skill:1002085"])
    marked.update(marker_patch)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(overlay, marked)

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert display["continuity"]["source_round"] == 4
    assert display["reference"]["balanced"] == "109,000"


@pytest.mark.parametrize(
    ("ref_balanced", "reference_balanced"),
    [
        (2**53, 2**53 + 1),
        (str(2**53), str(2**53 + 1)),
        (10**100, 10**100 + 1),
        (str(10**60), str(10**60 + 1)),
    ],
)
def test_ethan_distinct_large_money_values_cannot_replace_r4_authority(
    ref_balanced: int | str,
    reference_balanced: int | str,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    candidate = _ethan_continuity_quote(
        5, 222_000, notes=["skill:1002085"]
    )
    candidate["ahmed_ref"]["balanced"] = ref_balanced
    candidate["reference"]["balanced"] = reference_balanced
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, candidate
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert display["continuity"]["source_round"] == 4
    assert display["reference"]["balanced"] == "109,000"


@pytest.mark.parametrize(
    ("ref_balanced", "reference_balanced"),
    [
        ("1+09000", 109_000),
        ("1,,09000", 109_000),
        ("1e++5", 100_000),
        ("1e,5", 100_000),
        ("01", 1),
        ("+01", 1),
        ("109,00", 10_900),
        ("109000,", 109_000),
        ("1e5", 100_000),
        ("1.", 1),
        (".5", 0.5),
        ("1_000", 1_000),
        ("１２３", 123),
    ],
)
def test_ethan_malformed_money_string_cannot_replace_r4_authority(
    ref_balanced: str,
    reference_balanced: int | float,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    candidate = _ethan_continuity_quote(
        5, 222_000, notes=["skill:1002085"]
    )
    candidate["ahmed_ref"]["balanced"] = ref_balanced
    candidate["reference"]["balanced"] = reference_balanced
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, candidate
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert display["continuity"]["source_round"] == 4
    assert display["reference"]["balanced"] == "109,000"


@pytest.mark.parametrize(
    ("balanced", "reference_balanced"),
    [
        ("109000", 109_000),
        ("109,000", 109_000),
        ("+109000", 109_000),
        ("+109,000", 109_000),
        ("109000.0", 109_000),
        ("109000.5", 109_000.5),
        ("-109000", -109_000),
    ],
)
def test_ethan_canonical_money_string_can_replace_r4_authority(
    balanced: str,
    reference_balanced: int | float,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    candidate = _ethan_continuity_quote(
        5, 222_000, notes=["skill:1002085"]
    )
    candidate["ahmed_ref"]["balanced"] = balanced
    candidate["reference"]["balanced"] = reference_balanced
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, candidate
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert display["continuity"]["source_round"] == 5
    assert display["reference"]["balanced"] == reference_balanced


@pytest.mark.parametrize("sign", [1, -1])
def test_ethan_integer_outside_finite_float_domain_cannot_replace_r4_authority(
    sign: int,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    outside = sign * (int(Decimal("1.7976931348623157E+308")) + 1)
    candidate = _ethan_continuity_quote(
        5, 222_000, notes=["skill:1002085"]
    )
    candidate["ahmed_ref"]["balanced"] = outside
    candidate["reference"]["balanced"] = outside
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, candidate
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert display["continuity"]["source_round"] == 4
    assert display["reference"]["balanced"] == "109,000"


@pytest.mark.parametrize(
    "malformed_ref",
    [
        [],
        _EthanListSubclass(),
        _EthanDictSubclass({"status": "ok"}),
        _EthanHostileValue(),
    ],
)
def test_ethan_same_authority_malformed_r5_ref_preserves_r4(
    malformed_ref: Any,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    candidate = _ethan_continuity_quote(
        5, 222_000, notes=["skill:1002085"]
    )
    candidate["ahmed_ref"] = malformed_ref
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, candidate
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert display["continuity"]["source_round"] == 4
    assert display["reference"]["balanced"] == "109,000"


@pytest.mark.parametrize("mismatch_field", ["reference", "ahmed_ref"])
def test_ethan_r5_quote_mismatch_preserves_same_authority_r4(
    mismatch_field: str,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_quote(4, 109_000)
    )
    mismatched = _ethan_continuity_quote(5, 222_000, notes=["skill:1002085"])
    mismatched[mismatch_field]["balanced"] = 333_000
    module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, mismatched
    )

    display = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, _ethan_continuity_wait()
    )

    assert display["continuity"]["source_round"] == 4
    assert display["reference"]["balanced"] == "109,000"


@pytest.mark.parametrize(
    "hostile_field",
    [
        "summary_status",
        "phase",
        "ref_status",
        "ref_balanced",
        "ref_balanced_huge_int",
        "reference_balanced",
        "context",
    ],
)
def test_ethan_hostile_protocol_values_return_original_without_exception(
    hostile_field: str,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    hostile = _ethan_continuity_quote(4, 109_000)
    value = _EthanHostileValue()
    if hostile_field == "summary_status":
        hostile["status"] = value
    elif hostile_field == "phase":
        hostile["context"]["phase"] = value
    elif hostile_field == "ref_status":
        hostile["ahmed_ref"]["status"] = value
    elif hostile_field == "ref_balanced":
        hostile["ahmed_ref"]["balanced"] = value
    elif hostile_field == "ref_balanced_huge_int":
        hostile["ahmed_ref"]["balanced"] = 10**1000
    elif hostile_field == "reference_balanced":
        hostile["reference"]["balanced"] = value
    else:
        hostile["context"] = _EthanHostileContext()

    result = module.AhmadTkOverlay._summary_with_ethan_r5_outline_continuity(
        overlay, hostile
    )

    assert result is hostile


def test_live_process_coalesces_same_input_and_keeps_only_latest_pending(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    first = _CallbackFuture(
        result={"status": "ok", "context": {"phase": "bidding", "session_id": "first"}}
    )
    second = _CallbackFuture(
        result={"status": "ok", "context": {"phase": "bidding", "session_id": "latest"}}
    )
    executor = _FakeExecutor(futures=[first, second])
    overlay._ref_engine_liveness_state = "ready"
    overlay._ref_engine_executor = executor
    overlay._ref_cache_signature = None
    overlay._ref_cache_summary = None
    applied: list[str] = []
    overlay._mark_summary_performance = lambda *_args: None
    overlay._apply_live_summary = lambda _snapshot, result: applied.append(
        str(result["context"]["session_id"])
    )
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(module, "ref_input_signature_from_snapshot", lambda row: (row["ref"],))

    started_at = time.perf_counter()
    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={"ref": "first"},
        signature=(4, 4),
        started_at=started_at,
    )
    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={"ref": "same"},
        signature=(4, 4),
        started_at=started_at,
    )
    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={"ref": "middle"},
        signature=(5, 5),
        started_at=started_at,
    )
    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={"ref": "latest"},
        signature=(6, 6),
        started_at=started_at,
    )
    assert len(executor.submissions) == 1
    assert overlay._summary_worker_pending["signature"] == (6, 6)

    first.finish()
    module.AhmadTkOverlay._drain_live_summary_results(overlay)
    # A newer snapshot is already queued, so the completed first result may
    # populate memoization but must not briefly paint stale content.
    assert applied == []
    assert len(executor.submissions) == 2
    assert overlay._summary_worker_signature == (6, 6)

    second.finish()
    module.AhmadTkOverlay._drain_live_summary_results(overlay)
    assert applied == ["latest"]
    assert overlay._summary_worker_running is False


def test_bounded_fallback_timeout_hard_stops_and_does_not_retry_same_signature(
    monkeypatch,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    executor = _FakeExecutor()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 7
    overlay._summary_worker_signature = (7, 7)
    overlay._summary_worker_started_wall = 0.0
    overlay._summary_worker_mode = "process_fallback"
    overlay._summary_worker_executor = executor
    overlay._ref_engine_fallback_attempted_signature = (7, 7)
    overlay._last_inference_signature = (7, 7)
    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(module, "SUMMARY_FALLBACK_PROCESS_TIMEOUT_SECONDS", 1.0)

    module.AhmadTkOverlay._drain_live_summary_results(overlay)

    assert executor.process.terminated == 1
    assert overlay._summary_worker_running is False
    assert overlay._last_inference_signature == (7, 7)


def test_settlement_preempts_and_hard_stops_bounded_fallback(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    executor = _FakeExecutor()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 8
    overlay._summary_worker_signature = (8, 8)
    overlay._summary_worker_mode = "process_fallback"
    overlay._summary_worker_executor = executor
    overlay._summary_phase = lambda _snapshot: "settled"
    overlay._mark_summary_performance = lambda *_args: None
    overlay._apply_live_summary = lambda *_args: None
    monkeypatch.setattr(
        module,
        "summarize_snapshot",
        lambda _snapshot, *, snapshot_path: {"status": "ok"},
    )

    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={"phase": "settled"},
        signature=(9, 9),
        started_at=time.perf_counter(),
    )

    assert executor.process.terminated == 1
    assert overlay._summary_worker_executor is None
    assert overlay._summary_worker_running is False


def test_exit_before_probe_publish_keeps_closed_and_hard_stops_local_executor(
    monkeypatch,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay.keep_monitor_on_close = True
    future = _GatedProbeFuture(module)
    executor = _FakeExecutor(future)
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: executor,
    )

    module.AhmadTkOverlay._start_ref_engine_liveness_warmup(overlay)
    assert future.entered.wait(timeout=1.0)
    module.AhmadTkOverlay._run_exit_cleanup(overlay)
    future.release.set()

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and executor.process.is_alive():
        time.sleep(0.01)
    module.AhmadTkOverlay._drain_ref_engine_liveness_results(overlay)
    assert overlay._ref_engine_liveness_state == "closed"
    assert not executor.process.is_alive()


def test_exit_between_probe_check_and_ready_put_cannot_reopen_or_orphan(
    monkeypatch,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay.keep_monitor_on_close = True
    results = _BlockingPutQueue()
    overlay._ref_engine_liveness_results = results
    executor = _FakeExecutor(_SlowProbeFuture(module, delay=0.0))
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: executor,
    )

    module.AhmadTkOverlay._start_ref_engine_liveness_warmup(overlay)
    assert results.entered.wait(timeout=1.0)
    cleanup = threading.Thread(
        target=module.AhmadTkOverlay._run_exit_cleanup,
        args=(overlay,),
    )
    cleanup.start()
    results.release.set()
    cleanup.join(timeout=2.0)
    assert not cleanup.is_alive()

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and executor.process.is_alive():
        time.sleep(0.01)
    module.AhmadTkOverlay._drain_ref_engine_liveness_results(overlay)
    try:
        assert overlay._ref_engine_liveness_state == "closed"
        assert not executor.process.is_alive()
    finally:
        module._shutdown_ref_engine_executor(executor, hard=True)


def test_exit_after_ready_put_before_drain_hard_stops_queued_executor(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay.keep_monitor_on_close = True
    executor = _FakeExecutor(_SlowProbeFuture(module, delay=0.0))
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: executor,
    )

    module.AhmadTkOverlay._start_ref_engine_liveness_warmup(overlay)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and overlay._ref_engine_liveness_results.empty():
        time.sleep(0.01)
    assert not overlay._ref_engine_liveness_results.empty()

    module.AhmadTkOverlay._run_exit_cleanup(overlay)
    module.AhmadTkOverlay._drain_ref_engine_liveness_results(overlay)
    try:
        assert overlay._ref_engine_liveness_state == "closed"
        assert not executor.process.is_alive()
    finally:
        module._shutdown_ref_engine_executor(executor, hard=True)


def test_old_generation_ready_is_rejected_and_real_worker_is_terminated() -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay._ref_engine_liveness_state = "pending"
    overlay._ref_engine_liveness_generation = 2
    executor = ProcessPoolExecutor(max_workers=1)
    executor.submit(time.sleep, 30.0)
    deadline = time.monotonic() + 5.0
    processes: list[Any] = []
    while time.monotonic() < deadline:
        processes = list(executor._processes.values())
        if processes and all(process.is_alive() for process in processes):
            break
        time.sleep(0.01)
    assert processes and all(process.is_alive() for process in processes)
    overlay._ref_engine_liveness_results.put(
        {
            "status": "ready",
            "reason": "probe_failed",
            "executor": executor,
            "generation": 1,
        }
    )

    try:
        module.AhmadTkOverlay._drain_ref_engine_liveness_results(overlay)
        assert overlay._ref_engine_liveness_state == "pending"
        assert all(not process.is_alive() for process in processes)
    finally:
        module._shutdown_ref_engine_executor(executor, hard=True)


def test_delayed_probe_from_replaced_generation_cannot_publish_ready(monkeypatch) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    first_future = _GatedProbeFuture(module)
    first = _FakeExecutor(first_future)
    second = _FakeExecutor(_SlowProbeFuture(module, delay=0.0))
    executors = [first, second]
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: executors.pop(0),
    )

    module.AhmadTkOverlay._start_ref_engine_liveness_warmup(overlay)
    assert first_future.entered.wait(timeout=1.0)
    first_generation = overlay._ref_engine_liveness_generation
    first_token = overlay._ref_engine_liveness_token
    with overlay._ref_engine_liveness_lock:
        overlay._ref_engine_liveness_state = "not_started"
    module.AhmadTkOverlay._start_ref_engine_liveness_warmup(overlay)
    assert overlay._ref_engine_liveness_generation == first_generation + 1
    assert overlay._ref_engine_liveness_token is not first_token

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and overlay._ref_engine_liveness_results.empty():
        time.sleep(0.01)
    assert not overlay._ref_engine_liveness_results.empty()
    first_future.release.set()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and first.process.is_alive():
        time.sleep(0.01)

    module.AhmadTkOverlay._drain_ref_engine_liveness_results(overlay)
    assert overlay._ref_engine_liveness_state == "ready"
    assert overlay._ref_engine_executor is second
    assert not first.process.is_alive()
    assert second.process.is_alive()


def test_exit_while_packaged_identity_check_is_paused_never_calls_factory(
    monkeypatch,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay.keep_monitor_on_close = True
    overlay._ref_engine_liveness_state = "failed"
    overlay._ref_engine_liveness_generation = 4
    overlay._ref_engine_liveness_token = object()
    packaged_entered = threading.Event()
    packaged_release = threading.Event()
    executor = _FakeExecutor()
    factory_calls: list[str] = []

    def _packaged() -> bool:
        packaged_entered.set()
        assert packaged_release.wait(timeout=2.0)
        return True

    monkeypatch.setattr(module, "_is_packaged_build", _packaged)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: factory_calls.append("factory") or executor,
    )
    result: list[Any] = []
    ensure = threading.Thread(
        target=lambda: result.append(
            module.AhmadTkOverlay._ensure_ref_engine_executor(overlay)
        )
    )
    ensure.start()
    assert packaged_entered.wait(timeout=1.0)
    module.AhmadTkOverlay._run_exit_cleanup(overlay)
    packaged_release.set()
    ensure.join(timeout=2.0)
    try:
        assert not ensure.is_alive()
        assert result == [None]
        assert factory_calls == []
        assert executor.submissions == []
        assert overlay._ref_engine_liveness_state == "closed"
    finally:
        module._shutdown_ref_engine_executor(executor, hard=True)


def test_exit_while_source_executor_factory_is_paused_hard_stops_new_executor(
    monkeypatch,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay.keep_monitor_on_close = True
    overlay._ref_engine_liveness_state = "source_ready"
    overlay._ref_engine_liveness_generation = 5
    overlay._ref_engine_liveness_token = object()
    executor = _FakeExecutor()
    factory = _BlockingFactory(executor)
    monkeypatch.setattr(module, "_is_packaged_build", lambda: False)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: factory(_self),
    )
    result: list[Any] = []
    ensure = threading.Thread(
        target=lambda: result.append(
            module.AhmadTkOverlay._ensure_ref_engine_executor(overlay)
        )
    )
    ensure.start()
    assert factory.entered.wait(timeout=1.0)
    module.AhmadTkOverlay._run_exit_cleanup(overlay)
    factory.release.set()
    ensure.join(timeout=2.0)
    try:
        assert not ensure.is_alive()
        assert result == [None]
        assert factory.calls == 1
        assert not executor.process.is_alive()
        assert executor.submissions == []
        assert overlay._ref_engine_executor is None
    finally:
        module._shutdown_ref_engine_executor(executor, hard=True)


def test_exit_while_packaged_rewarmup_factory_is_paused_hard_stops_new_executor(
    monkeypatch,
) -> None:
    module = _overlay_module()
    overlay = _bare_overlay(module)
    overlay.keep_monitor_on_close = True
    overlay._ref_engine_liveness_state = "failed"
    overlay._ref_engine_liveness_generation = 6
    overlay._ref_engine_liveness_token = object()
    executor = _FakeExecutor()
    factory = _BlockingFactory(executor)
    monkeypatch.setattr(module, "_is_packaged_build", lambda: True)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_create_ref_engine_executor",
        lambda _self: factory(_self),
    )
    result: list[Any] = []
    ensure = threading.Thread(
        target=lambda: result.append(
            module.AhmadTkOverlay._ensure_ref_engine_executor(overlay)
        )
    )
    ensure.start()
    assert factory.entered.wait(timeout=1.0)
    module.AhmadTkOverlay._run_exit_cleanup(overlay)
    factory.release.set()
    ensure.join(timeout=2.0)
    try:
        assert not ensure.is_alive()
        assert result in ([None], [module._REF_ENGINE_WARMUP_PENDING])
        assert factory.calls == 1
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and executor.process.is_alive():
            time.sleep(0.01)
        assert not executor.process.is_alive()
        assert executor.submissions in (
            [],
            [(module._ref_engine_child_liveness_probe, (True,), {})],
        )
        assert overlay._ref_engine_executor_mode is None
    finally:
        module._shutdown_ref_engine_executor(executor, hard=True)
