"""Deterministic trigger checks, not real-engine numeric or Windows UI proof.

Real refresh/signatures/worker Future callback/queue/drain/apply/cache are used.
Only the executor computation and widget/activation environment are synthetic.
"""
import copy
import json
import os
import queue
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from test_live_overlay import _ahmad_overlay_module, _ahmad_server_module


@pytest.fixture
def rig(tmp_path, monkeypatch):
    m = _ahmad_overlay_module()
    server = _ahmad_server_module()
    now = SimpleNamespace(value=time.time())
    monkeypatch.setattr(m, "time", SimpleNamespace(**(vars(time) | {"time": lambda: now.value})))
    monkeypatch.setattr(m.AhmadTkOverlay, "_activation_product_denied", lambda self: False)
    o = object.__new__(m.AhmadTkOverlay)
    o.snapshot_path = tmp_path / "latest_snapshot.json"
    o.root = SimpleNamespace(after=lambda *args: None, after_idle=lambda *args: None)
    o.interval_ms = 1000
    o.exit_when_pids = ()
    o._manual_active = o._manual_edit_enabled = False
    o._manual_live_session_id = ""
    o.manual_status = SimpleNamespace(configure=lambda **kw: None)
    o._last_summary = o._last_live_summary = {}
    o._last_live_snapshot = {}
    o._last_signature = o._last_inference_signature = None
    o._last_bid_signature = ()
    o._last_capture_status_signature = None
    o._ref_cache_signature = o._ref_cache_summary = o._ref_cache_snapshot = None
    o._summary_result_queue = queue.Queue()
    o._summary_worker_running = False
    o._summary_worker_seq = 0
    o._summary_worker_signature = o._summary_worker_pending = None
    o._compute_pending_trace_seq = 0
    o._compute_terminal_trace_seq = -1
    o._compute_monitor_generation = 0
    o._compute_pending = False
    o._ref_engine_executor_mode = "process"
    o._exit_cleanup_done = False
    for name in ("_poll_compute_status", "_drain_ref_engine_liveness_results",
                 "_drain_manual_summary_results", "_sync_compute_indicator",
                 "_auto_sync_manual_inputs", "_sync_game_launch_candidate",
                 "_record_ui_health", "_record_ui_runtime_status", "_mark_compute_timestamp"):
        setattr(o, name, lambda *args, **kw: None)
    o._update_activation_ui = lambda: False
    o._should_reset_manual_for_summary = lambda s: False
    o._summary_with_ethan_r5_outline_continuity = lambda s: s
    o._summary_with_quality_continuity = lambda s: s
    rendered = []
    o.render = o.render_standby = lambda s: rendered.append(copy.deepcopy(s))
    o.render_missing = lambda s: None
    capture = {}
    o._capture_status = lambda: capture
    calls = []

    class Executor:
        def submit(self, fn, snapshot, **kw):
            assert fn is m.summarize_snapshot
            assert kw["snapshot_path"] == o.snapshot_path
            f = Future()
            calls.append((f, copy.deepcopy(snapshot)))
            return f

        def shutdown(self, **kw):
            pass

    executor = Executor()
    o._ref_engine_executor = executor
    o._ensure_ref_engine_executor = lambda: executor
    monkeypatch.setattr(m, "_shutdown_ref_engine_executor", lambda *a, **k: None)
    o._start_ref_engine_liveness_warmup = lambda: None
    snapshot = {"inference_signature": ["monitor", 1], "latest_bids": {},
                "structured_ref_inputs": {"total_count": 3},
                "ui_contract": {"context": {"hero": "gabriela", "phase": "reading",
                                            "session_id": "session-a", "round": 1}}}

    def publish(at=None):
        o.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        stamp = now.value if at is None else at
        os.utime(o.snapshot_path, (stamp, stamp))

    def summary(s=None, stale=False):
        s = snapshot if s is None else s
        result = {"status": "ok", "context": copy.deepcopy(s["ui_contract"]["context"]),
                  "reference": {"conservative": "18,731", "balanced": "22,762", "aggressive": "28,570"},
                  "ahmed_ref": {"status": "count_prior"},
                  "quality_composition": server._quality_composition_payload({
                      "status": "ok", "quality_lock": {"q6": {
                          "strength": "probabilistic", "reason": "synthetic_trigger_control"}},
                      "quality_count_cells_candidates": {
                          "q6": [{"count": 1, "cells": 2, "prob": 1.0}]}})}
        if stale:
            result["stale"] = {"reason": "stale_snapshot", "keep_last_quote": True}
            result["flags"] = [{"label": "快照过期"}]
        return result

    def finish(stale=False, error=False):
        f, s = calls[-1]
        if error:
            f.set_exception(RuntimeError("synthetic failure"))
        else:
            f.set_result(summary(s, stale))
        o.refresh()

    publish()
    return SimpleNamespace(m=m, o=o, now=now, snapshot=snapshot, publish=publish,
                           summary=summary, finish=finish, calls=calls, capture=capture,
                           rendered=rendered, executor=executor)


def fresh(r):
    r.o.refresh()
    assert len(r.calls) == 1
    r.finish()
    assert r.o._ref_cache_signature is not None, "positive cache control"
    assert r.m.detail_composition_view(r.o._last_live_summary).quality("q6").candidates


def test_fresh_no_change_is_known_good(rig):
    r = rig
    fresh(r)
    for _ in range(8):
        r.o.refresh()
    assert len(r.calls) == 1


def test_age_transition_then_idle_is_known_red(rig):
    r = rig
    fresh(r)
    before = copy.deepcopy(r.o._last_live_summary["reference"])
    r.now.value += 61
    r.o.refresh()
    assert len(r.calls) == 2
    r.finish(stale=True)
    for _ in range(8):
        if not r.calls[-1][0].done():
            r.finish(stale=True)
        r.now.value += 2
        r.o.refresh()
    assert len(r.calls) == 2, "same old input must compute fresh + age transition only"
    assert r.o._last_live_summary["reference"] == before
    assert r.o._last_live_summary["stale"]["keep_last_quote"] is True
    assert r.o._last_live_summary["flags"] == [{"label": "快照过期"}]


def stale(r):
    fresh(r)
    r.now.value += 61
    r.o.refresh()
    r.finish(stale=True)
    assert len(r.calls) == 2


def test_initial_stale_computes_once(rig):
    r = rig
    r.now.value += 61
    r.o.refresh()
    r.finish(stale=True)
    for _ in range(5):
        r.o.refresh()
    assert len(r.calls) == 1


def test_fresh_worker_inflight_across_age_does_not_consume_transition(rig):
    r = rig
    r.o.refresh()
    r.now.value += 61
    for _ in range(4):
        r.o.refresh()
    assert len(r.calls) == 1
    assert getattr(r.o, "_stale_refresh_requested_identity", None) is None
    r.finish(stale=False)
    assert len(r.calls) == 2
    r.finish(stale=True)
    assert len(r.calls) == 2


def test_worker_started_fresh_but_returns_valid_stale_needs_no_second_pass(rig):
    r = rig
    r.o.refresh()
    r.now.value += 61
    r.finish(stale=True)
    for _ in range(4):
        r.o.refresh()
    assert len(r.calls) == 1


@pytest.mark.parametrize("kind", ["ref", "round", "session", "monitor", "bid", "mtime"])
def test_new_input_and_publication_restore_necessary_refresh(rig, kind):
    r = rig
    stale(r)
    old_ref = r.o._ref_cache_signature
    if kind == "ref":
        r.snapshot["structured_ref_inputs"]["total_count"] = 4
    elif kind in {"round", "session"}:
        r.snapshot["ui_contract"]["context"]["round" if kind == "round" else "session_id"] = (
            2 if kind == "round" else "session-b")
    elif kind == "monitor":
        r.snapshot["inference_signature"][1] += 1
    elif kind == "bid":
        r.snapshot["latest_bids"] = {"p1": 123}
        r.snapshot["ui_contract"]["baseline"] = {"decision": {"current_highest": "123"}}
    r.publish()
    r.o.refresh()
    expected = 3 if kind in {"ref", "round", "session"} else 2
    assert len(r.calls) == expected
    if expected == 3:
        r.finish()
        assert r.o._ref_cache_signature != old_ref
    if kind == "bid":
        assert r.o._last_live_summary["reference"]["current_highest"] == "1*3"
    r.now.value += 61
    r.o.refresh()
    assert len(r.calls) == expected + 1
    r.finish(stale=True)
    for _ in range(3):
        r.o.refresh()
    assert len(r.calls) == expected + 1


def test_manual_background_uses_live_identity_and_returns_live(rig):
    r = rig
    fresh(r)
    r.o._manual_active = True
    manual = {"status": "ok", "context": {"phase": "settled", "session_id": "manual"}}
    r.o._last_summary = manual
    r.now.value += 61
    r.o.refresh()
    r.finish(stale=True)
    for _ in range(4):
        r.o.refresh()
    assert len(r.calls) == 2
    assert r.o._last_summary is manual
    live = r.o._last_live_summary
    r.o._manual_active = False
    r.o._apply_live_summary(r.o._last_live_snapshot, live, force=True)
    r.o.refresh()
    assert r.o._last_summary is live
    assert len(r.calls) == 2


def test_capture_heartbeats_are_not_identity_but_session_change_is(rig):
    r = rig
    fresh(r)
    r.capture.update(ts=time.time(), active_session_id="session-b")
    r.o.refresh()
    assert len(r.calls) == 2
    # Synthetic unchanged valid result exercises the trigger independently of
    # real server's standby projection (covered by the existing server tests).
    r.finish()
    for _ in range(4):
        r.capture["ts"] = time.time()
        r.o.refresh()
    assert len(r.calls) == 2
    r.capture["active_session_id"] = "session-c"
    r.o.refresh()
    assert len(r.calls) == 3


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_stale_failure_retains_real_bounded_retry_not_old_cache(rig, failure, monkeypatch):
    r = rig
    fresh(r)
    cached = r.o._ref_cache_summary
    r.now.value += 61
    r.o.refresh()
    assert len(r.calls) == 2
    r.o._ref_engine_executor_mode = "process_fallback"
    if failure == "error":
        r.finish(error=True)
    else:
        monkeypatch.setattr(r.m, "SUMMARY_PROCESS_WORKER_TIMEOUT_SECONDS", 0)
        r.o.refresh()
    assert len(r.calls) == 3, "one real fallback, never old-cache pseudo recovery"
    assert r.o._ref_cache_summary is cached
    assert getattr(r.o, "_stale_refresh_completed_identity", None) is None
    if failure == "error":
        r.finish(error=True)
    else:
        monkeypatch.setattr(r.m, "SUMMARY_FALLBACK_PROCESS_TIMEOUT_SECONDS", 0)
        r.o.refresh()
    for _ in range(6):
        r.o.refresh()
    assert len(r.calls) == 3, "fallback failure must not loop"
    assert getattr(r.o, "_stale_refresh_completed_identity", None) is None
    r.snapshot["structured_ref_inputs"]["total_count"] = 8
    r.publish()
    r.o.refresh()
    assert len(r.calls) == 4, "new valid input remains eligible"


def test_invalid_stale_result_is_not_success_and_new_input_recovers(rig):
    r = rig
    fresh(r)
    r.now.value += 61
    r.o.refresh()
    o = r.o
    o._summary_result_queue.put({"seq": o._summary_worker_seq,
                                 "signature": o._summary_worker_signature,
                                 "snapshot": copy.deepcopy(r.snapshot), "summary": None})
    o.refresh()
    for _ in range(4):
        o.refresh()
    assert len(r.calls) == 2
    assert getattr(o, "_stale_refresh_completed_identity", None) is None
    r.snapshot["structured_ref_inputs"]["total_count"] = 7
    r.publish()
    o.refresh()
    assert len(r.calls) == 3


def test_superseded_stale_completion_does_not_close_new_input(rig):
    r = rig
    fresh(r)
    r.now.value += 61
    r.o.refresh()
    old = r.calls[-1]
    r.snapshot["structured_ref_inputs"]["total_count"] = 9
    r.publish(at=r.now.value - 61)
    r.o.refresh()
    assert r.o._summary_worker_pending is not None
    old[0].set_result(r.summary(old[1], True))
    r.o.refresh()
    assert len(r.calls) == 3
    assert getattr(r.o, "_stale_refresh_completed_identity", None) is None
    assert r.o._last_live_snapshot["structured_ref_inputs"]["total_count"] == 3
    r.finish(stale=True)
    for _ in range(4):
        r.o.refresh()
    assert len(r.calls) == 3
    assert r.o._last_live_snapshot["structured_ref_inputs"]["total_count"] == 9


@pytest.mark.parametrize("rejection", ["generation", "terminal", "activation"])
def test_rejected_completion_cannot_mark_stale_success(rig, rejection, monkeypatch):
    r = rig
    fresh(r)
    r.now.value += 61
    r.o.refresh()
    if rejection == "generation":
        r.o._compute_monitor_generation += 1
    elif rejection == "terminal":
        r.o._compute_terminal_trace_seq = r.o._compute_pending_trace_seq
    else:
        monkeypatch.setattr(r.m.AhmadTkOverlay, "_activation_product_denied", lambda self: True)
        r.o._render_degraded = lambda: None
    r.finish(stale=True)
    assert getattr(r.o, "_stale_refresh_completed_identity", None) is None


def test_positive_control_detects_legacy_age_trigger_in_real_refresh_chain(rig, monkeypatch):
    def legacy_needs(self):
        summary = self._last_live_summary if self._manual_active else self._last_summary
        if not summary or summary.get("status") == "stale_snapshot":
            return False
        age = self._snapshot_file_age_seconds()
        return age is not None and age >= rig.m.STALE_SNAPSHOT_SECONDS

    monkeypatch.setattr(rig.m.AhmadTkOverlay, "_needs_stale_refresh", legacy_needs)
    with pytest.raises(AssertionError, match="same old input must compute"):
        test_age_transition_then_idle_is_known_red(rig)
    assert len(rig.calls) == 11, "manually checked positive-control submit count"


def test_stale_retry_can_complete_successfully(rig):
    r = rig
    fresh(r)
    r.now.value += 61
    r.o.refresh()
    r.o._ref_engine_executor_mode = "process_fallback"
    r.finish(error=True)
    assert len(r.calls) == 3
    r.finish(stale=True)
    assert r.o._stale_refresh_completed_identity is not None
    for _ in range(5):
        r.o.refresh()
    assert len(r.calls) == 3


def test_ref_error_keeps_its_existing_one_retry_without_false_completion(rig):
    r = rig
    fresh(r)
    r.now.value += 61
    r.o.refresh()
    for _ in range(2):
        f, s = r.calls[-1]
        result = r.summary(s, True)
        result["ahmed_ref"]["status"] = "error"
        f.set_result(result)
        r.o.refresh()
    for _ in range(5):
        r.o.refresh()
    assert len(r.calls) == 3
    assert getattr(r.o, "_stale_refresh_completed_identity", None) is None


@pytest.mark.parametrize("payload", ["", "{not-json", "{}"])
def test_missing_or_invalid_file_never_borrows_stale_success(rig, payload):
    r = rig
    stale(r)
    r.o.snapshot_path.write_text(payload, encoding="utf-8")
    r.o.refresh()
    assert len(r.calls) == 2
    r.snapshot["structured_ref_inputs"]["total_count"] = 12
    r.publish()
    r.o.refresh()
    assert len(r.calls) == 3
