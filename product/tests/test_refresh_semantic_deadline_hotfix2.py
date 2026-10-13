"""REF-P2: real wire parser and monitor, virtual time, numeric processor seam.

No game/network/user capture inputs. Temporary tables are deliberately empty:
the processor replacement projects parsed actions, never claims quote accuracy.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from test_fatbeans_webhook_monitor import _module, _payload
from test_live_fatbeans import (
    _fatbeans_row, _rev_direct_action_frame, _rev_state_with_bids_frame,
    _rev_status_frame, _send_bid_frame,
)
from test_live_refresh_signatures import _bid_capture_payload


SESSION = "2401:synthetic-ref-p2"


def _state(session=SESSION):
    return _fatbeans_row("REV", _rev_state_with_bids_frame(
        session=session, map_id=2401, round_no=1, bids=()), sort_id=0)


def _action(result=12):
    return _fatbeans_row("REV", _rev_direct_action_frame(
        action_id=100003, result=result), sort_id=0)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = _module()
    clock = SimpleNamespace(value=10.0)
    # Replace only this module's clock reference, not time used by threads/writers.
    monkeypatch.setattr(module, "time", SimpleNamespace(
        **(vars(module.time) | {"monotonic": lambda: clock.value})))
    tables = SimpleNamespace(items={}, maps={}, drop_pools={}, fixture="empty-numeric-seam")
    monitor = module.FatbeansWebhookMonitor(config=module.WebhookMonitorConfig(
        log_dir=tmp_path / "logs", raw_dir=tmp_path / "raw", process_name="BidKing.exe",
        server_ports=(10000,), n_trials=500, roi_trials=0, shadow_trials=None,
        full_shadow_trials=20, run_debug_shadows=False, seed=1,
        archive_round_snapshots=False, full_upgrade_when_idle=False), tables=tables)
    builds = []

    def build(payload, **kwargs):
        assert kwargs["tables"] is tables
        events = module.parse_fatbeans_capture_payload(payload)
        signature = module._semantic_signature_from_payload(payload)
        actions = [(a.action_id, a.result) for s in events.states for a in s.action_results]
        builds.append({"at": clock.value, "signature": signature, "actions": actions})
        return {"session_id": signature[0], "phase": "bidding", "round": 1,
                "created_at": module.time.time(), "processor_actions": actions}

    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", build)
    # Real write_monitor_logs and its final publication gate remain exercised.
    yield SimpleNamespace(module=module, monitor=monitor, clock=clock, builds=builds,
                          build=build, path=tmp_path)
    monitor._fact_receipts.close()


def _accept(rig, row, *, scheduled=True, payload=False):
    if payload:
        result = rig.monitor.accept_payload(_payload(
            Data=row["Data"], SrcIP="8.133.195.27", DstIP="127.0.0.1",
            SrcPort=10000, DstPort=60213))
    else:
        result = rig.monitor.accept_row(row, schedule_process=scheduled)
    assert result["accepted"], result


@pytest.mark.parametrize("entry", ["row", "payload"])
def test_real_result_continuous_duplicate_ready_at_first_deadline(rig, entry):
    _accept(rig, _state(), payload=entry == "payload")
    _accept(rig, _action(), payload=entry == "payload")
    assert rig.monitor._last_probed_semantic_signature[5]
    for step in range(1, 121):
        rig.clock.value = 10 + step * .25
        _accept(rig, _action(), payload=entry == "payload")
        rig.monitor._process_if_due()
    assert rig.builds and rig.builds[0]["at"] == 11.0
    assert (100003, 12.0) in rig.builds[0]["actions"]
    assert len(rig.builds) == 1
    snap = json.loads((rig.path / "logs/latest_snapshot.json").read_text(encoding="utf-8"))
    assert [100003, 12.0] in snap["processor_actions"]
    assert rig.monitor._snapshot_publish_seq == 1


def test_quiet_result_and_min_interval_positive_control(rig):
    rig.monitor._last_inference_at = 9.5
    _accept(rig, _state())
    _accept(rig, _action())
    rig.clock.value = 11.0
    rig.monitor._process_if_due()
    assert not rig.builds
    rig.clock.value = 11.5
    rig.monitor._process_if_due()
    assert rig.builds[0]["at"] == 11.5


def test_different_results_coalesce_without_restarting_deadline(rig):
    _accept(rig, _state())
    for step in range(5):
        rig.clock.value = 10 + step * .25
        _accept(rig, _action(12 + step))
        rig.monitor._process_if_due()
    assert len(rig.builds) == 1
    assert rig.builds[0]["at"] == 11
    assert {v for _, v in rig.builds[0]["actions"]} == {12, 13, 14, 15, 16}


@pytest.mark.parametrize("start,debounce,interval,last,ready", [
    (0., 1., 2., 0., 2.), (10., 2.5, 4., 9., 13.), (10., 0., 0., 0., 10.),
])
def test_configured_deadline_boundaries(rig, start, debounce, interval, last, ready):
    rig.clock.value = start
    rig.monitor.config = replace(rig.monitor.config, debounce_seconds=debounce,
                                 min_inference_interval_seconds=interval)
    rig.monitor._last_inference_at = last
    _accept(rig, _state())
    if ready > start:
        rig.clock.value = ready - .001
        _accept(rig, _action())
        rig.monitor._process_if_due()
        assert not rig.builds
    rig.clock.value = ready
    rig.monitor._process_if_due()
    assert rig.builds[0]["at"] == ready


@pytest.mark.parametrize("kind", ["send", "heartbeat", "malformed"])
def test_nonsemantic_rows_never_create_deadline(rig, kind):
    if kind == "send":
        row = _fatbeans_row("SEND", _send_bid_frame(session=SESSION, value=123), sort_id=0)
    elif kind == "heartbeat":
        row = _fatbeans_row("REV", _rev_status_frame(session=SESSION, player_id=9), sort_id=0)
    else:
        row = {"Direct": "REV", "Data": "not-valid-base64"}
    for step in range(8):
        rig.clock.value = 10 + .25 * step
        _accept(rig, row, scheduled=kind == "malformed")
        rig.monitor._process_if_due()
    assert not rig.builds
    assert rig.monitor._pending_semantic_anchor is None
    assert rig.monitor._active_compute_trace is None


def test_bid_display_only_uses_trailing_refresh_without_new_semantic_batch(rig):
    first = json.loads(_bid_capture_payload(SESSION, sort_id=1, leader_value=100))[0]
    _accept(rig, first)
    rig.clock.value = 11
    rig.monitor._process_if_due()
    assert len(rig.builds) == 1
    for step in range(1, 13):
        rig.clock.value = 11 + step * .25
        row = json.loads(_bid_capture_payload(SESSION, sort_id=step + 1, leader_value=200 + step))[0]
        _accept(rig, row)
        rig.monitor._process_if_due()
        assert rig.monitor._pending_semantic_anchor is None
    assert len(rig.builds) == 1
    assert rig.monitor._last_processed_count == 1
    rig.clock.value = 15
    rig.monitor._process_if_due()
    assert len(rig.builds) == 1
    assert rig.monitor._last_processed_count == 13
    snap = json.loads((rig.path / "logs/latest_snapshot.json").read_text(encoding="utf-8"))
    assert snap["latest_bids"]["Carol"] == 212


@pytest.mark.parametrize("reset", [False, True])
def test_new_session_does_not_borrow_old_anchor(rig, reset):
    _accept(rig, _state())
    rig.clock.value = 10.75
    if reset:
        rig.monitor.reset_rows()
    _accept(rig, _state("2401:new-synthetic-session"))
    rig.clock.value = 11
    rig.monitor._process_if_due()
    assert not rig.builds
    rig.clock.value = 11.75
    rig.monitor._process_if_due()
    assert rig.builds[0]["signature"][0] == "2401:new-synthetic-session"


@pytest.mark.parametrize("old_outcome", ["success", "error", "partial", "reset"])
def test_busy_worker_successor_preserves_own_deadline_and_latest_publication(rig, monkeypatch, old_outcome):
    _accept(rig, _state())
    entered, release = threading.Event(), threading.Event()
    errors = []

    def blocked_build(payload, **kwargs):
        artifact = rig.build(payload, **kwargs)
        entered.set()
        assert release.wait(2)
        if old_outcome == "error":
            raise RuntimeError("synthetic-processor-failure")
        if old_outcome == "partial":
            raise ValueError("incomplete length")
        return artifact

    monkeypatch.setattr(rig.module, "build_monitor_artifact_from_payload", blocked_build)
    def work():
        try:
            rig.monitor._process_if_due()
        except BaseException as exc:
            errors.append(exc)
    rig.clock.value = 11
    worker = threading.Thread(target=work)
    worker.start()
    assert entered.wait(2)
    try:
        rig.clock.value = 11.25
        if old_outcome == "reset":
            rig.monitor.reset_rows()
            _accept(rig, _state("2401:new-worker-session"))
        _accept(rig, _action(12))
        assert rig.monitor._pending_semantic_anchor[2] == 11.25
        rig.clock.value = 12
        _accept(rig, _action(14))
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and not errors
    assert not (rig.path / "logs/latest_snapshot.json").exists()
    assert rig.monitor._pending_semantic_anchor[2] == 11.25
    monkeypatch.setattr(rig.module, "build_monitor_artifact_from_payload", rig.build)
    rig.clock.value = 12.249
    rig.monitor._process_if_due()
    assert len(rig.builds) == 1
    rig.clock.value = 12.25
    rig.monitor._process_if_due()
    assert len(rig.builds) == 2 and rig.monitor._snapshot_publish_seq == 1
    assert rig.monitor._pending_semantic_anchor is None
    assert {v for _, v in rig.builds[-1]["actions"]} == {12, 14}


@pytest.mark.parametrize("boundary", ["reset", "stop", "new_rows"])
def test_late_real_parser_probe_cannot_restore_old_anchor(rig, monkeypatch, boundary):
    entered, release = threading.Event(), threading.Event()
    original = rig.module._semantic_signature_from_payload
    errors = []
    first = True
    def delayed(payload):
        nonlocal first
        signature = original(payload)
        if first:
            first = False
            entered.set()
            assert release.wait(2)
        return signature
    monkeypatch.setattr(rig.module, "_semantic_signature_from_payload", delayed)
    # Actual start/stop lifecycle, no server; worker loop waits for our test stop.
    if boundary == "stop":
        rig.monitor._worker = threading.Thread(target=lambda: rig.monitor._stop.wait(2))
        rig.monitor.start()
    def accept():
        try:
            _accept(rig, _state())
        except BaseException as exc:
            errors.append(exc)
    producer = threading.Thread(target=accept)
    producer.start()
    assert entered.wait(2)
    try:
        rig.clock.value = 10.75
        if boundary == "stop":
            rig.monitor.stop()
        else:
            if boundary == "reset":
                rig.monitor.reset_rows()
            _accept(rig, _state("2401:new-probe-session"))
    finally:
        release.set()
        producer.join(2)
    assert not producer.is_alive() and not errors
    if boundary == "stop":
        assert rig.monitor._pending_semantic_anchor is None
        assert not rig.monitor.accept_row(_state())["accepted"]
    else:
        assert rig.monitor._pending_semantic_anchor[2] == 10.75
        assert rig.monitor._pending_semantic_anchor[1] == "2401:new-probe-session"


def test_failed_probe_worker_recovery_does_not_block_next_batch(rig, monkeypatch):
    original = rig.module._semantic_signature_from_payload
    def fails(_payload):
        raise ValueError("synthetic-parser-failure")
    monkeypatch.setattr(rig.module, "_semantic_signature_from_payload", fails)
    _accept(rig, _state())
    assert rig.monitor._pending_semantic_anchor is None
    monkeypatch.setattr(rig.module, "_semantic_signature_from_payload", original)
    rig.clock.value = 11
    rig.monitor._process_if_due()
    assert len(rig.builds) == 1 and rig.monitor._snapshot_publish_seq == 1
    rig.clock.value = 11.25
    _accept(rig, _action())
    for step in range(1, 8):
        rig.clock.value = 11.25 + step * .25
        _accept(rig, _action())
        rig.monitor._process_if_due()
    assert rig.builds[-1]["at"] == 13
    assert len(rig.builds) == 2


@pytest.mark.parametrize("reset", [False, True])
def test_session_change_between_ready_check_and_row_claim_cannot_borrow_deadline(rig, monkeypatch, reset):
    _accept(rig, _state())
    original = rig.monitor._process_snapshot
    def change_then_claim(**kwargs):
        rig.clock.value = 11.0
        if reset:
            rig.monitor.reset_rows()
        _accept(rig, _state("2401:claim-race-session"))
        return original(**kwargs)
    monkeypatch.setattr(rig.monitor, "_process_snapshot", change_then_claim)
    rig.clock.value = 11.0
    rig.monitor._process_if_due()
    assert not rig.builds
    assert rig.monitor._pending_semantic_anchor[2] == 11.0
    monkeypatch.setattr(rig.monitor, "_process_snapshot", original)
    rig.clock.value = 11.999
    rig.monitor._process_if_due()
    assert not rig.builds
    rig.clock.value = 12.0
    rig.monitor._process_if_due()
    assert rig.builds[0]["signature"][0] == "2401:claim-race-session"


@pytest.mark.parametrize("result", [12, 14])
def test_same_batch_change_at_claim_keeps_ready_latest_rows(rig, monkeypatch, result):
    _accept(rig, _state())
    _accept(rig, _action(12))
    original = rig.monitor._process_snapshot
    def append_then_claim(**kwargs):
        _accept(rig, _action(result))
        return original(**kwargs)
    monkeypatch.setattr(rig.monitor, "_process_snapshot", append_then_claim)
    rig.clock.value = 11
    rig.monitor._process_if_due()
    assert len(rig.builds) == 1 and rig.builds[0]["at"] == 11
    assert rig.builds[0]["actions"][-1] == (100003, result)


def test_stop_after_ready_clears_pending_and_rejects_claim(rig, monkeypatch):
    rig.monitor._worker = threading.Thread(target=lambda: rig.monitor._stop.wait(2))
    rig.monitor.start()
    _accept(rig, _state())
    assert rig.monitor._pending_semantic_anchor is not None
    original = rig.monitor._process_snapshot
    def stop_then_claim(**kwargs):
        rig.monitor.stop()
        return original(**kwargs)
    monkeypatch.setattr(rig.monitor, "_process_snapshot", stop_then_claim)
    rig.clock.value = 11
    rig.monitor._process_if_due()
    assert rig.monitor._pending_semantic_anchor is None
    assert not rig.builds


@pytest.mark.parametrize("probe", ["normal", "none", "raises"])
@pytest.mark.parametrize("entry", ["row", "payload"])
@pytest.mark.parametrize("boundary", ["same", "new_session", "reset"])
def test_f01_recovered_session_uses_actual_recognition_anchor(rig, monkeypatch, probe, entry, boundary):
    _accept(rig, _state(), payload=entry == "payload")
    _accept(rig, _action(12), payload=entry == "payload")
    rig.clock.value = 10.8
    if boundary == "reset":
        rig.monitor.reset_rows()
    session = SESSION if boundary == "same" else "2401:recovered-f01-session"
    with monkeypatch.context() as failure:
        if probe == "none":
            failure.setattr(rig.module, "_semantic_signature_from_payload", lambda _: None)
        elif probe == "raises":
            def fails(_payload):
                raise ValueError("f01 synthetic failed lightweight probe")
            failure.setattr(rig.module, "_semantic_signature_from_payload", fails)
        _accept(rig, _state(session), payload=entry == "payload")
        _accept(rig, _action(99), payload=entry == "payload")
    rig.clock.value = 11
    rig.monitor._process_if_due()
    rearmed = boundary == "new_session" and probe != "normal"
    ready = 11 if boundary == "same" else 12 if rearmed else 11.8
    if boundary != "same":
        assert not rig.builds and not (rig.path / "logs/latest_snapshot.json").exists()
        if rearmed:
            assert rig.monitor._pending_semantic_anchor == (rig.monitor._row_generation, session, 11)
            assert rig.monitor._active_compute_trace["semantic_recognized"] == 11
            assert rig.monitor._latest_scheduled_timing["monitor_state_committed"] == 10.8
        rig.clock.value = ready - .001
        rig.monitor._process_if_due()
        assert not rig.builds
        rig.clock.value = ready
        rig.monitor._process_if_due()
    assert len(rig.builds) == 1 and rig.builds[0]["at"] == ready
    assert rig.builds[0]["signature"][0] == session
    snap = json.loads((rig.path / "logs/latest_snapshot.json").read_text(encoding="utf-8"))
    assert snap["session_id"] == session and [100003, 99] in snap["processor_actions"]


@pytest.mark.parametrize("successor", ["new_row", "new_session", "reset", "stop", "same_rows_probe"])
def test_f01_recovery_window_preserves_later_owner(rig, monkeypatch, successor):
    if successor == "stop":
        rig.monitor._worker = threading.Thread(target=lambda: rig.monitor._stop.wait(2))
        rig.monitor.start()
    _accept(rig, _state())
    rig.clock.value = 10.8
    with monkeypatch.context() as failure:
        failure.setattr(rig.module, "_semantic_signature_from_payload", lambda _: None)
        _accept(rig, _state("2401:recovered-f01-session"))
        _accept(rig, _action(99))
    original = rig.monitor._adopt_recovered_semantic_if_current
    successor_anchor = []
    def later_then_adopt(**kwargs):
        assert kwargs["pending_recognized_at"] == 11
        rig.clock.value = 11.2
        if successor == "stop":
            rig.monitor.stop()
        elif successor == "reset":
            rig.monitor.reset_rows()
            _accept(rig, _state("2401:successor-f01-session"))
        elif successor == "new_session":
            _accept(rig, _state("2401:successor-f01-session"))
        elif successor == "same_rows_probe":
            # The earlier producer's probe can finish after the row claim.
            # Keep exact row count, but let its trusted trace win first.
            rig.monitor._last_semantic_probe_count = 0
            rig.monitor._probe_pending_semantic()
        else:
            _accept(rig, _action(100))
        successor_anchor.append(rig.monitor._pending_semantic_anchor)
        return original(**kwargs)
    monkeypatch.setattr(rig.monitor, "_adopt_recovered_semantic_if_current", later_then_adopt)
    rig.clock.value = 11
    rig.monitor._process_if_due()
    assert not rig.builds and rig.monitor._pending_semantic_anchor == successor_anchor[0]
    if successor == "stop":
        assert successor_anchor == [None]
        return
    assert successor_anchor[0][2] == 11.2
    monkeypatch.setattr(rig.monitor, "_adopt_recovered_semantic_if_current", original)
    rig.clock.value = 12.199
    rig.monitor._process_if_due()
    assert not rig.builds
    rig.clock.value = 12.2
    rig.monitor._process_if_due()
    assert len(rig.builds) == 1
    assert rig.builds[0]["signature"][0] == successor_anchor[0][1]


@pytest.mark.parametrize("force", [False, True])
def test_f01_unscheduled_direct_and_full_recovery_keep_existing_behavior(rig, monkeypatch, force):
    _accept(rig, _state())
    rig.clock.value = 10.8
    with monkeypatch.context() as failure:
        failure.setattr(rig.module, "_semantic_signature_from_payload", lambda _: None)
        _accept(rig, _state("2401:direct-f01-session"))
    rig.clock.value = 11
    rig.monitor._process_snapshot(force=force)
    assert len(rig.builds) == 1 and rig.builds[0]["at"] == 11
    assert rig.builds[0]["signature"][0] == "2401:direct-f01-session"


def test_f01_rearmed_same_session_failed_probes_do_not_restart_batch(rig, monkeypatch):
    _accept(rig, _state())
    rig.clock.value = 10.8
    with monkeypatch.context() as failure:
        failure.setattr(rig.module, "_semantic_signature_from_payload", lambda _: None)
        _accept(rig, _state("2401:recovered-f01-session"))
    rig.clock.value = 11
    rig.monitor._process_if_due()
    assert not rig.builds and rig.monitor._pending_semantic_anchor[2] == 11
    for step in range(1, 5):
        rig.clock.value = 11 + step * .25
        with monkeypatch.context() as failure:
            failure.setattr(rig.module, "_semantic_signature_from_payload", lambda _: None)
            _accept(rig, _action(20 + step))
        rig.monitor._process_if_due()
    assert len(rig.builds) == 1 and rig.builds[0]["at"] == 12
    assert {value for _, value in rig.builds[0]["actions"]} == {21, 22, 23, 24}
