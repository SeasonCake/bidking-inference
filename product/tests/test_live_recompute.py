from __future__ import annotations

import sys
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from live_recompute import (  # noqa: E402
    AUTO_REQUEST_KEY,
    DIRTY_SINCE_KEY,
    maybe_schedule_live_dirty_recompute,
    update_live_dirty_clock,
)


def test_update_live_dirty_clock_resets_when_clean() -> None:
    ss = {}

    assert update_live_dirty_clock(ss, dirty=True, version=3, now=10.0) == 10.0
    assert ss[DIRTY_SINCE_KEY] == 10.0

    assert update_live_dirty_clock(ss, dirty=False, version=3, now=11.0) is None
    assert DIRTY_SINCE_KEY not in ss


def test_live_dirty_recompute_requires_ready_arm() -> None:
    ss = {}

    queued = maybe_schedule_live_dirty_recompute(
        ss,
        dirty=True,
        version=1,
        inference_ready=True,
        worker_running=False,
        auto_enabled=True,
        armed=False,
        has_pending_request=False,
        now=10.0,
    )

    assert queued is False
    assert "_request_bg_hint_manual" not in ss


def test_live_dirty_recompute_queues_one_manual_request() -> None:
    ss = {}

    queued = maybe_schedule_live_dirty_recompute(
        ss,
        dirty=True,
        version=2,
        inference_ready=True,
        worker_running=False,
        auto_enabled=True,
        armed=True,
        has_pending_request=False,
        now=20.0,
    )

    assert queued is True
    assert ss["_request_bg_hint_manual"] is True
    assert ss[AUTO_REQUEST_KEY] is True
    assert ss["_bg_infer_status"] == "idle"


def test_live_dirty_recompute_waits_for_debounce() -> None:
    ss = {}

    queued = maybe_schedule_live_dirty_recompute(
        ss,
        dirty=True,
        version=2,
        inference_ready=True,
        worker_running=False,
        auto_enabled=True,
        armed=True,
        has_pending_request=False,
        now=20.0,
        debounce_sec=0.5,
    )
    queued_after_wait = maybe_schedule_live_dirty_recompute(
        ss,
        dirty=True,
        version=2,
        inference_ready=True,
        worker_running=False,
        auto_enabled=True,
        armed=True,
        has_pending_request=False,
        now=20.6,
        debounce_sec=0.5,
    )

    assert queued is False
    assert queued_after_wait is True


def test_live_dirty_recompute_does_not_queue_while_running() -> None:
    ss = {}

    queued = maybe_schedule_live_dirty_recompute(
        ss,
        dirty=True,
        version=2,
        inference_ready=True,
        worker_running=True,
        auto_enabled=True,
        armed=True,
        has_pending_request=False,
        now=20.0,
    )

    assert queued is False
    assert "_request_bg_hint_manual" not in ss

