from __future__ import annotations

import io
import json
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
import zipfile

import pytest

from test_fatbeans_webhook_monitor import _module as _monitor_module
from test_live_overlay import _ahmad_overlay_module


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _monitor(module: Any, tmp_path: Path, *, debounce: float = 30.0) -> Any:
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
            debounce_seconds=debounce,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )


def _read_status(monitor: Any) -> dict[str, Any]:
    return json.loads(monitor.compute_status_path.read_text(encoding="utf-8-sig"))


def test_monitor_semantic_pending_precedes_debounce_and_is_privacy_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path, debounce=30.0)
    builds: list[bool] = []
    monkeypatch.setattr(module, "_semantic_signature_from_payload", lambda _payload: ("valid", 1))
    monkeypatch.setattr(
        module,
        "build_monitor_artifact_from_payload",
        lambda *args, **kwargs: builds.append(True),
    )

    received = time.monotonic()
    monitor.accept_row(
        {"SortID": 1, "Direct": "REV", "private_marker": "must-not-leak"},
        schedule_process=True,
        frame_received_monotonic=received,
        parser_recognized_monotonic=received + 0.001,
    )
    monitor._process_if_due()

    status = _read_status(monitor)
    assert status["event"] == "semantic_pending"
    assert status["compute_pending"] is True
    assert status["trace_seq"] == 1
    assert status["timestamps_monotonic"]["wss_frame_received"] == round(received, 6)
    assert status["timestamps_monotonic"]["semantic_recognized"] >= received
    assert builds == [], "trusted feedback must not bypass the existing inference debounce"
    serialized = json.dumps(status, ensure_ascii=False)
    for forbidden in (
        "private_marker",
        "must-not-leak",
        "session_id",
        "player",
        "token",
        "payload",
        str(tmp_path),
    ):
        assert forbidden not in serialized


def test_webhook_accept_path_announces_semantic_before_worker_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path, debounce=30.0)
    monkeypatch.setattr(
        module,
        "_webhook_payload_to_row",
        lambda _payload, *, sort_id, **_kwargs: {
            "SortID": sort_id,
            "Direct": "REV",
        },
    )
    monkeypatch.setattr(
        module,
        "_semantic_signature_from_payload",
        lambda _payload: ("webhook", 1),
    )

    result = monitor.accept_payload({"private_marker": "must-not-leak"})

    assert result["accepted"] is True
    status = _read_status(monitor)
    assert status["event"] == "semantic_pending"
    assert status["trace_seq"] == 1
    assert status["counters"]["scheduled_rows"] == 1
    assert "private_marker" not in json.dumps(status)


@pytest.mark.parametrize("case", ["invalid", "unchanged", "bid_only"])
def test_monitor_does_not_signal_untrusted_or_unchanged_rows(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path, debounce=30.0)
    signature = ("same", 1)
    monkeypatch.setattr(
        module,
        "_semantic_signature_from_payload",
        lambda _payload: None if case == "invalid" else signature,
    )
    if case == "unchanged":
        monitor._last_semantic_signature = signature
    monitor.accept_row(
        {"SortID": 1, "Direct": "REV"},
        schedule_process=case != "bid_only",
    )
    monitor._process_if_due()
    assert not monitor.compute_status_path.exists()


def test_monitor_snapshot_publish_and_partial_error_are_explicit_terminal_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path, debounce=0.0)
    monkeypatch.setattr(module, "_semantic_signature_from_payload", lambda _payload: ("valid", 1))
    monkeypatch.setattr(
        module,
        "build_monitor_artifact_from_payload",
        lambda _payload, *, file, **_kwargs: {"file": file, "phase": "bidding"},
    )
    monkeypatch.setattr(module, "parse_fatbeans_capture_payload", lambda *a, **k: SimpleNamespace(states=()))
    monkeypatch.setattr(module, "write_monitor_logs", lambda *args, **kwargs: None)

    monitor.accept_row({"SortID": 1, "Direct": "REV"})
    monitor._process_if_due()
    published = _read_status(monitor)
    assert published["event"] == "snapshot_published"
    assert published["compute_pending"] is True
    assert published["counters"]["snapshot_seq"] == 1
    assert published["timestamps_monotonic"]["snapshot_published"] >= published[
        "timestamps_monotonic"
    ]["semantic_recognized"]

    monkeypatch.setattr(module, "_semantic_signature_from_payload", lambda _payload: ("valid", 2))
    monkeypatch.setattr(
        module,
        "build_monitor_artifact_from_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("incomplete length private")),
    )
    monitor.accept_row({"SortID": 2, "Direct": "REV"})
    monitor._process_if_due()
    partial = _read_status(monitor)
    assert partial["event"] == "partial"
    assert partial["compute_pending"] is False
    assert "private" not in json.dumps(partial)


@pytest.mark.parametrize("old_outcome", ["success", "error", "partial"])
def test_new_semantic_pending_bypasses_blocked_idle_full_upgrade(
    old_outcome: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path, debounce=0.0)
    build_entered = threading.Event()
    release_build = threading.Event()
    build_calls = 0
    thread_errors: list[BaseException] = []

    def semantic_signature(payload: str | bytes) -> tuple[str, int]:
        return ("rows", len(json.loads(payload)))

    def build_artifact(_payload: str, *, file: str, **_kwargs: Any) -> dict[str, Any]:
        nonlocal build_calls
        build_calls += 1
        if build_calls == 2:
            build_entered.set()
            assert release_build.wait(timeout=2.0)
            if old_outcome == "error":
                raise RuntimeError("fixed-old-build-error")
            if old_outcome == "partial":
                raise ValueError("incomplete length")
        return {"file": file, "phase": "bidding"}

    monkeypatch.setattr(module, "_semantic_signature_from_payload", semantic_signature)
    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", build_artifact)
    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture_payload",
        lambda *args, **kwargs: SimpleNamespace(states=()),
    )
    monkeypatch.setattr(module, "write_monitor_logs", lambda *args, **kwargs: None)

    monitor.accept_row({"SortID": 1, "Direct": "REV"})
    monitor._process_if_due()
    assert _read_status(monitor)["event"] == "snapshot_published"

    overlay_module = _ahmad_overlay_module()
    overlay = _overlay(overlay_module, tmp_path / "logs")
    overlay._poll_compute_status()
    assert overlay.pixel_cat.running and overlay.border_glow.running
    overlay._clear_compute_pending("summary_applied", rendered=True)
    assert not overlay.pixel_cat.running and not overlay.border_glow.running

    def blocked_full_upgrade() -> None:
        try:
            monitor._process_snapshot(force=True)
        except BaseException as exc:  # noqa: BLE001 - surface worker-thread failures
            thread_errors.append(exc)

    worker = threading.Thread(target=blocked_full_upgrade, daemon=True)
    worker.start()
    assert build_entered.wait(timeout=1.0)
    try:
        started = time.monotonic()
        monitor.accept_row({"SortID": 2, "Direct": "REV"})
        elapsed = time.monotonic() - started
        status = _read_status(monitor)
        overlay._poll_compute_status()

        assert elapsed < 0.5
        assert status["event"] == "semantic_pending"
        assert status["trace_seq"] == 2
        assert status["counters"]["scheduled_rows"] == 2
        assert overlay._compute_pending is True
        assert overlay.pixel_cat.running and overlay.border_glow.running
    finally:
        release_build.set()
        worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert thread_errors == []
    final_status = _read_status(monitor)
    assert final_status["event"] == "semantic_pending"
    assert final_status["compute_pending"] is True
    assert final_status["trace_seq"] == 2
    assert overlay._compute_pending is True
    assert overlay.pixel_cat.running and overlay.border_glow.running


def test_compute_status_writer_serializes_and_rejects_stale_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path)
    old_write_entered = threading.Event()
    release_old_write = threading.Event()
    errors: list[BaseException] = []

    def controlled_atomic_write(path: Path, payload: dict[str, Any]) -> None:
        if payload["trace_seq"] == 1:
            old_write_entered.set()
            assert release_old_write.wait(timeout=2.0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(module, "_atomic_write_json", controlled_atomic_write)

    def write_status(trace_seq: int, event: str) -> None:
        try:
            monitor._write_compute_status(
                event=event,
                compute_pending=True,
                trace_seq=trace_seq,
                timestamps={},
                counters={"semantic_seq": trace_seq},
            )
        except BaseException as exc:  # noqa: BLE001 - surface thread failure
            errors.append(exc)

    old_writer = threading.Thread(target=write_status, args=(1, "snapshot_published"))
    new_writer = threading.Thread(target=write_status, args=(2, "semantic_pending"))
    old_writer.start()
    assert old_write_entered.wait(timeout=1.0)
    new_writer.start()
    release_old_write.set()
    old_writer.join(timeout=2.0)
    new_writer.join(timeout=2.0)

    assert errors == []
    assert not old_writer.is_alive() and not new_writer.is_alive()
    assert _read_status(monitor)["trace_seq"] == 2
    assert _read_status(monitor)["event"] == "semantic_pending"

    # A late call from trace 1 must not overwrite the already-published trace 2.
    write_status(1, "error")
    assert _read_status(monitor)["trace_seq"] == 2
    assert _read_status(monitor)["event"] == "semantic_pending"


@pytest.mark.parametrize("old_outcome", ["success", "error", "partial"])
def test_reset_generation_rejects_same_signature_old_compute_result(
    old_outcome: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path, debounce=0.0)
    build_entered = threading.Event()
    release_build = threading.Event()
    build_calls = 0
    written_artifacts: list[dict[str, Any]] = []

    def build_artifact(_payload: str, *, file: str, **_kwargs: Any) -> dict[str, Any]:
        nonlocal build_calls
        build_calls += 1
        if build_calls == 2:
            build_entered.set()
            assert release_build.wait(timeout=2.0)
            if old_outcome == "error":
                raise RuntimeError("fixed-old-generation-error")
            if old_outcome == "partial":
                raise ValueError("incomplete length")
        return {"file": file, "phase": "bidding", "build_call": build_calls}

    monkeypatch.setattr(
        module,
        "_semantic_signature_from_payload",
        lambda _payload: ("same-semantic-tuple",),
    )
    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", build_artifact)
    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture_payload",
        lambda *args, **kwargs: SimpleNamespace(states=()),
    )
    monkeypatch.setattr(
        module,
        "write_monitor_logs",
        lambda artifact, **_kwargs: written_artifacts.append(dict(artifact)),
    )

    monitor.accept_row({"SortID": 1, "Direct": "REV", "generation": 0})
    monitor._process_if_due()
    assert len(written_artifacts) == 1

    old_worker = threading.Thread(
        target=lambda: monitor._process_snapshot(force=True),
        daemon=True,
    )
    old_worker.start()
    assert build_entered.wait(timeout=1.0)

    monitor.reset_rows()
    monitor.accept_row({"SortID": 1, "Direct": "REV", "generation": 1})
    before = _read_status(monitor)
    assert before["event"] == "semantic_pending"
    assert before["compute_pending"] is True
    assert before["trace_seq"] == 2

    release_build.set()
    old_worker.join(timeout=2.0)
    assert not old_worker.is_alive()

    after = _read_status(monitor)
    assert after == before
    assert len(written_artifacts) == 1
    assert monitor._last_processed_count == 0
    assert monitor._last_semantic_signature is None


@pytest.mark.parametrize("entry", ["row", "bid_only_row", "payload"])
def test_stopped_monitor_rejects_new_producer_input(
    entry: str,
    tmp_path: Path,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path)
    monitor.start()
    monitor.stop()
    stopped = _read_status(monitor)
    assert stopped["event"] == "stopped"
    assert stopped["compute_pending"] is False

    if entry == "payload":
        result = monitor.accept_payload({"private": "not-parsed-after-stop"})
    else:
        result = monitor.accept_row(
            {"SortID": 1, "Direct": "REV"},
            schedule_process=entry != "bid_only_row",
        )

    assert result["accepted"] is False
    assert result["reason"] == "stopped"
    assert monitor.status()["rows"] == 0
    assert _read_status(monitor) == stopped


@pytest.mark.parametrize("boundary", ["reset", "stop"])
def test_late_pending_commit_cannot_overwrite_lifecycle_terminal(
    boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path, debounce=30.0)
    if boundary == "stop":
        monitor.start()

    monkeypatch.setattr(
        module,
        "_semantic_signature_from_payload",
        lambda _payload: ("trusted-semantic",),
    )
    commit_entered = threading.Event()
    release_commit = threading.Event()
    errors: list[BaseException] = []
    original_commit = monitor._commit_pending_status_if_current

    def delayed_commit(**kwargs: Any) -> bool:
        commit_entered.set()
        assert release_commit.wait(timeout=2.0)
        return original_commit(**kwargs)

    monkeypatch.setattr(monitor, "_commit_pending_status_if_current", delayed_commit)

    def accept_scheduled_row() -> None:
        try:
            result = monitor.accept_row(
                {"SortID": 1, "Direct": "REV"},
                schedule_process=True,
            )
            assert result["accepted"] is True
        except BaseException as exc:  # noqa: BLE001 - surface producer-thread failures
            errors.append(exc)

    producer = threading.Thread(target=accept_scheduled_row, daemon=True)
    producer.start()
    assert commit_entered.wait(timeout=1.0)

    if boundary == "reset":
        monitor.reset_rows()
    else:
        monitor.stop()
    terminal = _read_status(monitor)
    expected_event = "reset" if boundary == "reset" else "stopped"
    assert terminal["event"] == expected_event
    assert terminal["compute_pending"] is False

    release_commit.set()
    producer.join(timeout=2.0)
    assert not producer.is_alive()
    assert errors == []
    assert _read_status(monitor) == terminal


def test_archive_replay_signals_only_real_semantic_transitions(tmp_path: Path) -> None:
    module = _monitor_module()
    monitor = _monitor(module, tmp_path, debounce=30.0)
    fixture = ROOT / "tests" / "fixtures" / "live" / "settlement_5604_f3_split_npcap_live.jsonl"
    rows = [
        json.loads(line)
        for line in fixture.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    changed_at: list[int] = []
    previous_seq = 0
    for index, row in enumerate(rows, start=1):
        monitor.accept_row(row, schedule_process=True)
        monitor._process_if_due()
        if monitor._compute_trace_seq != previous_seq:
            changed_at.append(index)
            previous_seq = monitor._compute_trace_seq

    # This fixture contains continuous transport/bid traffic around seven
    # parser-confirmed start/item/round/settlement state transitions. Rows in
    # between must not produce a false computing acknowledgement.
    assert changed_at == [1, 3, 9, 12, 17, 19, 25]
    status = _read_status(monitor)
    assert status["event"] == "semantic_pending"
    assert status["trace_seq"] == len(changed_at)


def test_wss_pipe_reader_adds_privacy_safe_monotonic_arrival_timestamp() -> None:
    import run_wss_live_monitor as module

    sink: queue.Queue[tuple[float, str] | None] = queue.Queue()
    before = time.monotonic()
    module._pipe_reader(io.StringIO("frame-one\n"), sink)
    after = time.monotonic()
    item = sink.get_nowait()
    assert item is not None
    arrived, line = item
    assert before <= arrived <= after
    assert line == "frame-one"
    assert sink.get_nowait() is None


def test_wss_record_preserves_addon_capture_monotonic_timestamp() -> None:
    import base64
    import run_wss_live_monitor as module

    raw = bytearray(16)
    raw[:4] = len(raw).to_bytes(4, "big")
    raw[12:16] = (0x0021).to_bytes(4, "big")
    captured = time.monotonic()
    line = module.FRAME_STREAM_PREFIX + json.dumps(
        {
            "captured_at_ms": 1,
            "captured_monotonic": captured,
            "flow_id": "synthetic-flow",
            "direct": "REV",
            "message_id": 0x0021,
            "data_length": len(raw),
            "data_b64": base64.b64encode(raw).decode("ascii"),
        },
        separators=(",", ":"),
    )

    parsed = module.parse_frame_record(line)
    assert parsed is not None
    assert parsed["captured_monotonic"] == captured


class _Root:
    def after_idle(self, callback: Any) -> None:
        callback()


class _Status:
    def __init__(self) -> None:
        self.text = ""

    def cget(self, key: str) -> str:
        assert key == "bg"
        return "#000000"

    def configure(self, **kwargs: Any) -> None:
        self.text = str(kwargs.get("text", self.text))


class _Cat:
    def __init__(self) -> None:
        self.mapped = False
        self.running = False

    def set_background(self, _bg: str) -> None:
        pass

    def winfo_ismapped(self) -> bool:
        return self.mapped

    def pack(self, **_kwargs: Any) -> None:
        self.mapped = True

    def pack_forget(self) -> None:
        self.mapped = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class _Glow:
    def __init__(self) -> None:
        self.running = False

    def refresh_background(self) -> None:
        pass

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


def _overlay(module: Any, tmp_path: Path) -> Any:
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = _Root()
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.status = _Status()
    overlay.pixel_cat = _Cat()
    overlay.border_glow = _Glow()
    overlay._summary_worker_running = False
    overlay._summary_worker_pending = None
    overlay._compute_pending = False
    overlay._compute_pending_trace_seq = 0
    overlay._compute_terminal_trace_seq = -1
    overlay._compute_monitor_generation = 0
    overlay._compute_status_event = "idle"
    overlay._last_compute_status_signature = None
    overlay._compute_latency_timestamps = {}
    overlay._compute_indicator_visible_seq = 0
    overlay._compute_indicator_idle_scheduled_seq = 0
    overlay._record_ui_runtime_status = lambda *args, **kwargs: None
    return overlay


def _write_overlay_compute_status(
    path: Path,
    *,
    event: str,
    pending: bool,
    trace_seq: int,
    snapshot_seq: int = 0,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event": event,
                "compute_pending": pending,
                "trace_seq": trace_seq,
                "timestamps_monotonic": {},
                "counters": {
                    "semantic_seq": trace_seq,
                    "snapshot_seq": snapshot_seq,
                },
            }
        ),
        encoding="utf-8",
    )


def test_overlay_rendered_trace_rejects_late_equal_pending_but_accepts_next_trace(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    status_path = tmp_path / module.COMPUTE_STATUS_FILENAME

    overlay._set_compute_pending("semantic_pending", trace_seq=7)
    _write_overlay_compute_status(
        status_path,
        event="snapshot_published",
        pending=True,
        trace_seq=7,
        snapshot_seq=1,
    )
    overlay._poll_compute_status()
    assert overlay._compute_pending is True, "same-trace progress remains valid before terminal"

    overlay._clear_compute_pending("summary_applied", rendered=True)
    assert overlay._compute_pending is False
    _write_overlay_compute_status(
        status_path,
        event="snapshot_published",
        pending=True,
        trace_seq=7,
        snapshot_seq=2,
    )
    overlay._poll_compute_status()
    assert overlay._compute_pending is False
    assert not overlay.pixel_cat.mapped and not overlay.pixel_cat.running
    assert not overlay.border_glow.running

    _write_overlay_compute_status(
        status_path,
        event="semantic_pending",
        pending=True,
        trace_seq=8,
    )
    overlay._poll_compute_status()
    assert overlay._compute_pending is True
    assert overlay.pixel_cat.running and overlay.border_glow.running

    _write_overlay_compute_status(
        status_path,
        event="stopped",
        pending=False,
        trace_seq=8,
    )
    overlay._poll_compute_status()
    assert overlay._compute_pending is False
    _write_overlay_compute_status(
        status_path,
        event="snapshot_published",
        pending=True,
        trace_seq=8,
        snapshot_seq=3,
    )
    overlay._poll_compute_status()
    assert overlay._compute_pending is False

    _write_overlay_compute_status(
        status_path,
        event="reset",
        pending=False,
        trace_seq=0,
    )
    overlay._poll_compute_status()
    _write_overlay_compute_status(
        status_path,
        event="semantic_pending",
        pending=True,
        trace_seq=1,
    )
    overlay._poll_compute_status()
    assert overlay._compute_pending is True
    assert overlay.pixel_cat.running and overlay.border_glow.running


def test_pre_reset_worker_cannot_render_or_clear_new_generation_pending(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 5
    overlay._summary_worker_signature = ("old-generation",)
    overlay._summary_worker_mode = "thread_rollback"
    overlay._summary_worker_executor = None
    overlay._summary_worker_pending = None
    overlay._compute_pending = True
    overlay._compute_pending_trace_seq = 9
    applied: list[tuple[dict[str, Any], dict[str, Any]]] = []
    cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
    overlay._apply_live_summary = (
        lambda snapshot, summary: applied.append((snapshot, summary))
    )
    overlay._cache_live_summary_result = (
        lambda snapshot, summary: cached.append((snapshot, summary))
    )
    overlay._maybe_start_pending_summary_worker = lambda: None

    status_path = tmp_path / module.COMPUTE_STATUS_FILENAME
    _write_overlay_compute_status(
        status_path,
        event="reset",
        pending=False,
        trace_seq=0,
    )
    overlay._poll_compute_status()
    _write_overlay_compute_status(
        status_path,
        event="semantic_pending",
        pending=True,
        trace_seq=1,
    )
    overlay._poll_compute_status()
    assert overlay._compute_monitor_generation == 1
    assert overlay._compute_pending is True

    overlay._summary_result_queue.put(
        {
            "seq": 5,
            "compute_trace_seq": 9,
            "compute_monitor_generation": 0,
            "signature": ("old-generation",),
            "snapshot": {"session_id": "old", "phase": "bidding"},
            "summary": {"status": "ok", "session_id": "old"},
            "mode": "thread_rollback",
        }
    )
    overlay._drain_live_summary_results()

    assert applied == []
    assert cached == []
    assert overlay._compute_pending is True
    assert overlay._compute_pending_trace_seq == 1
    assert overlay._compute_terminal_trace_seq == 0


def test_stopped_terminal_discards_same_trace_worker_result(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 5
    overlay._summary_worker_signature = ("stopped",)
    overlay._summary_worker_mode = "thread_rollback"
    overlay._summary_worker_executor = None
    overlay._summary_worker_pending = None
    overlay._set_compute_pending("semantic_pending", trace_seq=4)
    applied: list[tuple[dict[str, Any], dict[str, Any]]] = []
    cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
    overlay._apply_live_summary = (
        lambda snapshot, summary: applied.append((snapshot, summary))
    )
    overlay._cache_live_summary_result = (
        lambda snapshot, summary: cached.append((snapshot, summary))
    )
    overlay._maybe_start_pending_summary_worker = lambda: None

    status_path = tmp_path / module.COMPUTE_STATUS_FILENAME
    _write_overlay_compute_status(
        status_path,
        event="stopped",
        pending=False,
        trace_seq=4,
    )
    overlay._poll_compute_status()
    overlay._summary_result_queue.put(
        {
            "seq": 5,
            "compute_trace_seq": 4,
            "compute_monitor_generation": 0,
            "signature": ("stopped",),
            "snapshot": {"session_id": "stopped", "phase": "bidding"},
            "summary": {"status": "ok", "session_id": "stopped"},
            "mode": "thread_rollback",
        }
    )
    overlay._drain_live_summary_results()

    assert applied == []
    assert cached == []
    assert overlay._compute_pending is False
    assert overlay._compute_terminal_trace_seq == 4


@pytest.mark.parametrize(
    ("archive", "hero", "map_id", "trace_seq"),
    (
        (
            Path(
                r"C:\Users\example\Desktop\bidking-live-v0.3.2-hotfix1-encrypted"
                r"\data\logs\live\exports"
                r"\HeroRefDiag-20260809-214659-5603_1902361429209339.zip"
            ),
            "maria",
            5603,
            23,
        ),
        (
            Path(
                r"C:\Users\example\Desktop\bidking-live-v0.3.2-hotfix1-encrypted"
                r"\data\logs\live\exports"
                r"\HeroRefDiag-20260809-220632-2406_1902361429962396.zip"
            ),
            "aisha",
            2406,
            9,
        ),
    ),
)
def test_author_terminal_diagnostics_do_not_relight_same_trace(
    archive: Path,
    hero: str,
    map_id: int,
    trace_seq: int,
    tmp_path: Path,
) -> None:
    if not archive.is_file():
        pytest.skip(f"author diagnostic unavailable: {archive.name}")
    with zipfile.ZipFile(archive) as package:
        monitor = json.loads(package.read("monitor_compute_status.json"))
        runtime = json.loads(package.read("hero_ref_ui_runtime_status.json"))

    assert runtime["summary_worker_running"] is False
    last_summary = runtime["last_summary"]
    assert last_summary["status"] == "ok"
    assert last_summary["hero"] == hero
    assert last_summary["map_id"] == map_id
    assert last_summary["round"] == 4
    assert last_summary["phase"] == "settled"
    assert last_summary["reference_source"] == "settlement"
    assert "render_applied" in runtime["compute_latency"]["timestamps_monotonic"]
    assert monitor["event"] == "snapshot_published"
    assert monitor["compute_pending"] is True
    assert monitor["trace_seq"] == trace_seq == runtime["compute_latency"]["trace_seq"]

    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    overlay._set_compute_pending("semantic_pending", trace_seq=trace_seq)
    overlay._clear_compute_pending("summary_applied", rendered=True)
    (tmp_path / module.COMPUTE_STATUS_FILENAME).write_text(
        json.dumps(monitor),
        encoding="utf-8",
    )
    overlay._poll_compute_status()

    assert overlay._compute_pending is False
    assert not overlay.pixel_cat.mapped and not overlay.pixel_cat.running
    assert not overlay.border_glow.running


def test_overlay_trusted_pending_shows_before_worker_and_all_terminal_events_stop(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    status_path = tmp_path / module.COMPUTE_STATUS_FILENAME
    status_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event": "semantic_pending",
                "compute_pending": True,
                "trace_seq": 4,
                "timestamps_monotonic": {"semantic_recognized": time.monotonic()},
                "counters": {"semantic_seq": 4, "snapshot_seq": 0},
            }
        ),
        encoding="utf-8",
    )
    overlay._poll_compute_status()
    assert overlay._compute_pending is True
    assert overlay._summary_worker_running is False
    assert overlay.pixel_cat.mapped and overlay.pixel_cat.running
    assert overlay.border_glow.running
    assert overlay._compute_latency_timestamps["indicator_visible"] >= overlay._compute_latency_timestamps[
        "overlay_detected"
    ]

    for terminal in (
        "error",
        "partial",
        "degraded",
        "skipped_bootstrap",
        "stopped",
        "reset",
    ):
        status_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event": terminal,
                    "compute_pending": False,
                    "trace_seq": 4,
                    "timestamps_monotonic": {},
                    "counters": {"semantic_seq": 4, "snapshot_seq": 0},
                }
            ),
            encoding="utf-8",
        )
        overlay._last_compute_status_signature = None
        overlay._compute_pending = True
        overlay._summary_worker_running = True
        overlay._poll_compute_status()
        assert overlay._compute_pending is False
        assert overlay._summary_worker_running is True
        assert not overlay.pixel_cat.mapped and not overlay.pixel_cat.running
        assert not overlay.border_glow.running

    # A restarted monitor begins its local counter at zero. Its reset event must
    # clear the previous process's higher sequence so the next seq=1 is accepted.
    assert overlay._compute_pending_trace_seq == 4
    status_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "event": "reset",
                "compute_pending": False,
                "trace_seq": 0,
                "timestamps_monotonic": {},
                "counters": {"semantic_seq": 0, "snapshot_seq": 0},
            }
        ),
        encoding="utf-8",
    )
    overlay._last_compute_status_signature = None
    overlay._poll_compute_status()
    assert overlay._compute_pending_trace_seq == 0


def test_overlay_warmup_and_newer_pending_keep_indicator_without_duplicate_cpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_seq = 0
    overlay._summary_worker_signature = None
    overlay._summary_worker_mode = None
    overlay._summary_worker_executor = None
    overlay._ref_engine_executor_mode = "liveness"
    overlay._ref_engine_fallback_attempted_signature = None
    overlay._summary_phase = lambda _snapshot: "bidding"
    overlay._activation_product_denied = lambda: False
    overlay._ensure_ref_engine_executor = lambda: module._REF_ENGINE_WARMUP_PENDING

    assert overlay._start_live_summary_worker(
        snapshot={"phase": "bidding"},
        signature=(1, 1),
        started_at=time.perf_counter(),
    )
    assert overlay._summary_worker_running is True
    assert overlay._summary_worker_mode == "liveness"
    assert overlay._summary_worker_pending["signature"] == (1, 1)
    assert overlay.pixel_cat.running and overlay.border_glow.running

    overlay._set_compute_pending("semantic_pending", trace_seq=2)
    assert overlay._start_live_summary_worker(
        snapshot={"phase": "bidding", "new": True},
        signature=(2, 2),
        started_at=time.perf_counter(),
    )
    assert overlay._summary_worker_pending["signature"] == (2, 2)
    assert overlay._summary_worker_seq == 1, "coalescing must not launch a second CPU worker"
    assert overlay._compute_pending and overlay.pixel_cat.running


@pytest.mark.parametrize("outcome", ["success", "error", "invalid"])
@pytest.mark.parametrize("queued_snapshot", [False, True])
def test_old_worker_cannot_render_or_clear_newer_compute_trace(
    outcome: str,
    queued_snapshot: bool,
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 5
    overlay._summary_worker_signature = ("old",)
    overlay._summary_worker_mode = "thread_rollback"
    overlay._summary_worker_executor = None
    overlay._summary_worker_pending = (
        {
            "snapshot": {"phase": "bidding", "version": "new"},
            "signature": ("new",),
            "started_at": time.perf_counter(),
        }
        if queued_snapshot
        else None
    )
    overlay._compute_pending = True
    overlay._compute_pending_trace_seq = 2
    overlay._sync_compute_indicator()
    overlay._record_ui_health = lambda *_args, **_kwargs: None
    overlay._record_ui_runtime_status = lambda *_args, **_kwargs: None
    applied: list[tuple[dict[str, Any], dict[str, Any]]] = []
    cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
    resumed: list[bool] = []
    overlay._apply_live_summary = lambda snapshot, summary: applied.append((snapshot, summary))
    overlay._cache_live_summary_result = (
        lambda snapshot, summary: cached.append((snapshot, summary))
    )
    overlay._maybe_start_pending_summary_worker = lambda: resumed.append(True)

    item: dict[str, Any] = {
        "seq": 5,
        "compute_trace_seq": 1,
        "signature": ("old",),
        "snapshot": {"phase": "bidding", "version": "old"},
        "mode": "thread_rollback",
    }
    if outcome == "success":
        item["summary"] = {"status": "ok", "version": "old"}
    elif outcome == "error":
        item["error"] = "fixed-private-safe-code"
    else:
        item["summary"] = None
    overlay._summary_result_queue.put(item)

    overlay._drain_live_summary_results()

    assert applied == []
    assert overlay._compute_pending is True
    assert overlay.pixel_cat.running and overlay.border_glow.running
    assert resumed == [True]
    assert bool(cached) is (outcome == "success")


def test_superseded_valid_result_populates_existing_ref_cache_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    overlay._summary_phase = lambda value: str(value.get("phase") or "")
    overlay._ref_cache_signature = None
    overlay._ref_cache_summary = None
    overlay._ref_cache_snapshot = None
    snapshot = {"phase": "bidding", "ref": "same"}
    summary = {
        "status": "ok",
        "phase": "bidding",
        "ahmed_ref": {"status": "ok"},
    }
    monkeypatch.setattr(
        module,
        "detail_composition_view",
        lambda _summary: SimpleNamespace(status="ready"),
    )
    monkeypatch.setattr(
        module,
        "ref_input_signature_from_snapshot",
        lambda value: (value["ref"],),
    )

    overlay._cache_live_summary_result(snapshot, summary)

    assert overlay._ref_cache_signature == ("same",)
    assert overlay._ref_cache_summary is summary
    assert overlay._ref_cache_snapshot is snapshot


def test_pending_cache_hit_stops_indicator_without_starting_cpu_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    snapshot = {"phase": "bidding", "ref": "same"}
    overlay._summary_worker_pending = {
        "snapshot": snapshot,
        "signature": ("latest",),
        "started_at": time.perf_counter(),
    }
    overlay._ref_cache_signature = ("same",)
    overlay._ref_cache_summary = {
        "status": "ok",
        "reference": {},
        "diagnostics": {},
    }
    overlay._summary_phase = lambda value: str(value.get("phase") or "")
    overlay._merge_cached_summary_with_fresh_snapshot = (
        lambda summary, _snapshot: summary
    )
    overlay._mark_summary_performance = lambda *_args, **_kwargs: None
    overlay._apply_live_summary = lambda *_args, **_kwargs: None
    started: list[dict[str, Any]] = []
    overlay._start_live_summary_worker = lambda **kwargs: started.append(kwargs)
    monkeypatch.setattr(
        module,
        "ref_input_signature_from_snapshot",
        lambda value: (value["ref"],),
    )
    overlay._set_compute_pending("snapshot_detected", trace_seq=1)
    assert overlay.pixel_cat.running and overlay.border_glow.running

    overlay._maybe_start_pending_summary_worker()

    assert started == []
    assert overlay._compute_pending is False
    assert not overlay.pixel_cat.running and not overlay.border_glow.running


def test_compute_latency_segments_are_monotonic_and_privacy_safe(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    overlay._compute_pending = True
    overlay._compute_pending_trace_seq = 9
    overlay._compute_status_event = "snapshot_published"
    overlay._compute_latency_timestamps = {
        "wss_frame_received": 10.0,
        "parser_recognized": 10.01,
        "monitor_state_committed": 10.02,
        "semantic_recognized": 10.03,
        "snapshot_published": 11.0,
        "overlay_detected": 11.2,
        "indicator_visible": 11.21,
        "worker_submitted": 11.22,
        "summary_completed": 11.5,
        "render_applied": 11.51,
        "private_path": 999.0,
    }
    diagnostics = overlay._compute_latency_diagnostics()
    assert diagnostics["segments_ms"] == {
        "capture_to_semantic": 30.0,
        "capture_to_parser": 10.0,
        "parser_to_state_commit": 10.0,
        "state_commit_to_semantic": 10.0,
        "semantic_to_snapshot_publish": 970.0,
        "publish_to_ui_detect": 200.0,
        "detect_to_indicator": 10.0,
        "detect_to_worker_submit": 20.0,
        "compute": 280.0,
        "result_to_render": 10.0,
    }
    assert "private_path" not in json.dumps(diagnostics)


def test_overlay_default_poll_matches_existing_300ms_floor() -> None:
    source = (ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py").read_text(
        encoding="utf-8"
    )
    assert 'parser.add_argument("--interval-ms", type=int, default=300)' in source
    assert "self.interval_ms = max(300, int(interval_ms))" in source


def test_real_tk_pixel_cat_and_border_glow_visible_lifecycle(tmp_path: Path) -> None:
    tk = pytest.importorskip("tkinter")
    module = _ahmad_overlay_module()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"real Tk unavailable: {exc}")
    overlay = None
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "latest_snapshot.json",
            interval_ms=100_000,
        )
        root.update_idletasks()
        root.update()
        overlay._set_compute_pending("semantic_pending", trace_seq=1)
        root.update_idletasks()
        root.update()
        assert overlay.pixel_cat.winfo_ismapped()
        assert overlay.pixel_cat._running is True
        assert overlay.border_glow._running is True
        assert all(edge.winfo_ismapped() for edge in overlay.border_glow._edges)

        overlay._clear_compute_pending("summary_applied", rendered=True)
        root.update_idletasks()
        root.update()
        assert not overlay.pixel_cat.winfo_ismapped()
        assert overlay.pixel_cat._running is False
        assert overlay.border_glow._running is False
        assert all(not edge.winfo_ismapped() for edge in overlay.border_glow._edges)
    finally:
        if overlay is not None:
            overlay._exit_cleanup_done = True
            overlay._hide_pixel_cat()
        root.destroy()


def test_indicator_pending_survives_bounded_cpu_pressure(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(module, tmp_path)
    done = threading.Event()

    def _bounded_pressure() -> None:
        deadline = time.perf_counter() + 0.05
        value = 0
        while time.perf_counter() < deadline:
            value = (value * 33 + 17) % 1_000_003
        assert value >= 0
        done.set()

    worker = threading.Thread(target=_bounded_pressure)
    worker.start()
    started = time.monotonic()
    overlay._set_compute_pending("semantic_pending", trace_seq=1)
    elapsed = time.monotonic() - started
    worker.join(timeout=1.0)
    assert done.is_set()
    assert elapsed < 0.5
    assert overlay.pixel_cat.running and overlay.border_glow.running
