# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import pytest

from bidking_lab.secure import client_activate as ca
from bidking_lab.secure import enforce

ROOT = Path(__file__).resolve().parents[1]


class _Clock:
    def __init__(self) -> None:
        self.epoch = 2_000_000
        self.monotonic = 100.0

    def advance(self, seconds: float) -> None:
        self.epoch += int(seconds)
        self.monotonic += float(seconds)


def _paid_grant(clock: _Clock, *, seconds: int = 3_600) -> ca.ActivationResult:
    return ca.ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        source="server",
        lease_exp=clock.epoch + seconds,
        paid_exp=clock.epoch + seconds,
        plan_kind="365d",
        feature_entitlements={"pet_companion": "full"},
    )


def _trial_grant(clock: _Clock, *, seconds: int = 3_600) -> ca.ActivationResult:
    return ca.ActivationResult(
        activated=True,
        mode="trial",
        reason="ok",
        source="server",
        lease_exp=clock.epoch + seconds,
        trial_exp=clock.epoch + seconds,
        plan_kind="trial",
        feature_entitlements={"pet_companion": "none"},
    )


def _failure(
    reason: str = "offline",
    *,
    failure_class: str = "tls_connection_reset",
) -> ca.ActivationResult:
    stage = {
        "dns_lookup_failed": "dns",
        "tls_connection_reset": "tls",
        "tls_handshake_failed": "tls",
    }.get(failure_class, "tcp")
    return ca.ActivationResult(
        activated=False,
        mode="paid",
        reason=reason,
        source="server",
        transport_failure_classes=((failure_class,) if failure_class else ()),
        transport_summary=ca.ActivationTransportSummary(
            attempt_count=(2 if failure_class else 0),
            endpoint_count=2,
            stage_counts=tuple(
                (name, 2 if name == stage and failure_class else 0)
                for name in ("dns", "tcp", "tls", "http", "unknown")
            ),
            all_endpoints_failed=bool(failure_class),
        ),
    )


@pytest.fixture()
def activation_runtime(monkeypatch):
    clock = _Clock()
    identity = {"token": "TOKEN-A", "device": "DEVICE-A"}
    old_values = {
        name: getattr(enforce, name)
        for name in (
            "_LAST",
            "_LAST_ONLINE_GRANT",
            "_LAST_ONLINE_IDENTITY",
            "_LAST_ONLINE_CONFIRMED_AT",
            "_LAST_HEARTBEAT",
            "_HEARTBEAT_FAILURE_COUNT",
            "_HEARTBEAT_FAILURE_STARTED_AT",
            "_ACTIVATION_REQUEST_GENERATION",
        )
        if hasattr(enforce, name)
    }
    old_identity_source_revision = ca._ACTIVATION_IDENTITY_SOURCE_REVISION
    old_clear_epoch = ca._ACTIVATION_CLEAR_EPOCH
    old_clear_state = ca._ACTIVATION_CLEAR_STATE
    monkeypatch.setattr(enforce.time, "time", lambda: clock.epoch)
    monkeypatch.setattr(enforce.time, "monotonic", lambda: clock.monotonic)
    monkeypatch.setattr(
        ca,
        "resolve_token",
        lambda ui_token=None: str(ui_token).strip() if ui_token else identity["token"],
    )
    monkeypatch.setattr(ca, "device_fingerprint", lambda: identity["device"])
    ca._ACTIVATION_IDENTITY_SOURCE_REVISION = 0
    ca._ACTIVATION_CLEAR_EPOCH = 0
    ca._ACTIVATION_CLEAR_STATE = None
    enforce._LAST = None
    for name, value in (
        ("_LAST_ONLINE_GRANT", None),
        ("_LAST_ONLINE_IDENTITY", None),
        ("_LAST_ONLINE_CONFIRMED_AT", None),
        ("_LAST_HEARTBEAT", None),
        ("_HEARTBEAT_FAILURE_COUNT", 0),
        ("_HEARTBEAT_FAILURE_STARTED_AT", None),
        ("_ACTIVATION_REQUEST_GENERATION", 0),
    ):
        if hasattr(enforce, name):
            setattr(enforce, name, value)
    try:
        yield clock, identity
    finally:
        enforce._LAST = old_values.get("_LAST")
        for name in (
            "_LAST_ONLINE_GRANT",
            "_LAST_ONLINE_IDENTITY",
            "_LAST_ONLINE_CONFIRMED_AT",
            "_LAST_HEARTBEAT",
            "_HEARTBEAT_FAILURE_COUNT",
            "_HEARTBEAT_FAILURE_STARTED_AT",
            "_ACTIVATION_REQUEST_GENERATION",
        ):
            if hasattr(enforce, name):
                setattr(enforce, name, old_values.get(name))
        ca._ACTIVATION_IDENTITY_SOURCE_REVISION = old_identity_source_revision
        ca._ACTIVATION_CLEAR_EPOCH = old_clear_epoch
        ca._ACTIVATION_CLEAR_STATE = old_clear_state


def _install_results(monkeypatch, *results: ca.ActivationResult) -> None:
    queue = list(results)
    monkeypatch.setattr(ca, "activate", lambda **_kwargs: queue.pop(0))


def _install_traced_results(monkeypatch, *rows) -> None:
    queue = list(rows)

    def activate(**_kwargs):
        result, trace = queue.pop(0)
        ca._ACTIVATION_TRANSPORT_TRACE_LOCAL.rows = list(trace)
        return result

    monkeypatch.setattr(ca, "activate", activate)


def test_product_token_save_and_clear_advance_atomic_identity_revision(
    monkeypatch,
) -> None:
    old_revision = ca._ACTIVATION_IDENTITY_SOURCE_REVISION
    old_clear_epoch = ca._ACTIVATION_CLEAR_EPOCH
    old_clear_state = ca._ACTIVATION_CLEAR_STATE
    old_runtime_denied = ca._RUNTIME_ACTIVATION_DENIED.is_set()
    ca._ACTIVATION_IDENTITY_SOURCE_REVISION = 0
    ca._ACTIVATION_CLEAR_EPOCH = 0
    ca._ACTIVATION_CLEAR_STATE = None
    ca._RUNTIME_ACTIVATION_DENIED.clear()
    monkeypatch.setattr(ca, "_atomic_write_bytes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ca, "_clear_persisted_tokens", lambda: True)
    monkeypatch.setattr(
        ca,
        "_clear_cache",
        lambda: {"cache_cleared": True, "cache_invalidated": False},
    )
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", "TOKEN-A")
    try:
        assert ca.save_token("TOKEN-B") is True
        assert ca._ACTIVATION_IDENTITY_SOURCE_REVISION == 2

        result = ca.clear_activation()
        assert result["cleared"] is True
        assert result["env_was_set"] is True
        assert ca._ACTIVATION_IDENTITY_SOURCE_REVISION == 6
        assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
    finally:
        ca._ACTIVATION_IDENTITY_SOURCE_REVISION = old_revision
        ca._ACTIVATION_CLEAR_EPOCH = old_clear_epoch
        ca._ACTIVATION_CLEAR_STATE = old_clear_state
        if old_runtime_denied:
            ca._RUNTIME_ACTIVATION_DENIED.set()
        else:
            ca._RUNTIME_ACTIVATION_DENIED.clear()


def test_one_or_two_transport_heartbeat_failures_keep_current_process_grant(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    grant = _paid_grant(clock)
    first = _failure("offline", failure_class="tls_connection_reset")
    second = _failure("offline", failure_class="connect_timeout")
    recovered = _paid_grant(clock)
    _install_results(monkeypatch, grant, first, second, recovered)

    assert enforce.startup_activate() is grant
    for expected_failure in (first, second):
        clock.advance(2)
        assert enforce.refresh_activation() is expected_failure
        assert enforce.current() is grant
        assert enforce.should_run_full() is True
        state = enforce.activation_state()
        assert state["run_full"] is True
        assert state["transport_grace"] is True
        assert state["reason"] == "transport_grace"
        assert state["needs_token"] is False
        assert state["text"] == "激活连接波动，正在重试"
        assert state["transport_reason"] == expected_failure.reason
        assert state["transport_failure_classes"] == (
            expected_failure.transport_failure_classes
        )

    clock.advance(2)
    assert enforce.refresh_activation() is recovered
    assert enforce.current() is recovered
    state = enforce.activation_state()
    assert state["run_full"] is True
    assert not state.get("transport_grace", False)


def test_third_consecutive_transport_failure_fails_closed(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    grant = _paid_grant(clock)
    failures = [_failure() for _ in range(3)]
    _install_results(monkeypatch, grant, *failures)

    enforce.startup_activate()
    for failure in failures[:2]:
        clock.advance(2)
        assert enforce.refresh_activation() is failure
        assert enforce.should_run_full() is True
    clock.advance(2)
    assert enforce.refresh_activation() is failures[2]
    assert enforce.current() is failures[2]
    assert enforce.should_run_full() is False
    assert enforce.activation_state()["run_full"] is False


@pytest.mark.parametrize("reason", ["state_error", "rate_limited", "server_error"])
def test_http_response_failures_never_receive_transport_grace(
    monkeypatch,
    activation_runtime,
    reason,
) -> None:
    clock, _identity = activation_runtime
    grant = _paid_grant(clock)
    failure = _failure(reason, failure_class="")
    _install_results(monkeypatch, grant, failure)

    enforce.startup_activate()
    clock.advance(1)
    assert enforce.refresh_activation() is failure
    state = enforce.activation_state()
    assert state["run_full"] is False
    assert not state.get("transport_grace", False)
    assert state["needs_token"] is False


def test_unhandled_local_exception_does_not_masquerade_as_transport_grace(
    monkeypatch,
    activation_runtime,
) -> None:
    """The exception fallback was not covered by the earlier transport-state source."""
    clock, _identity = activation_runtime
    grant = _paid_grant(clock)
    calls = iter((grant, RuntimeError("private transport details")))

    def _activate(**_kwargs):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ca, "activate", _activate)
    enforce.startup_activate()
    clock.advance(1)
    returned = enforce.refresh_activation()

    assert returned.activated is False
    assert returned.reason == "error:RuntimeError"
    assert enforce.current() is returned
    assert enforce.should_run_full() is False
    state = enforce.activation_state()
    assert state["run_full"] is False
    assert not state.get("transport_grace", False)
    assert state["needs_token"] is False


def test_online_grant_must_be_recent_before_transport_grace(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    grant = _paid_grant(clock, seconds=3_600)
    failure = _failure()
    _install_results(monkeypatch, grant, failure)

    assert enforce.startup_activate() is grant
    clock.advance(enforce._ACTIVATION_ONLINE_GRANT_RECENCY_SECONDS + 1)
    assert enforce.refresh_activation() is failure
    assert enforce.current() is failure
    assert enforce.should_run_full() is False
    assert not enforce.activation_state().get("transport_grace", False)


def test_online_grant_recency_is_rechecked_after_network_io(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    grant = _paid_grant(clock, seconds=3_600)
    failure = _failure(failure_class="connect_timeout")
    calls = 0

    def _activate(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return grant
        clock.advance(2)
        return failure

    monkeypatch.setattr(ca, "activate", _activate)

    assert enforce.startup_activate() is grant
    clock.advance(enforce._ACTIVATION_ONLINE_GRANT_RECENCY_SECONDS - 1)
    assert enforce.refresh_activation() is failure
    assert enforce.current() is failure
    assert enforce.should_run_full() is False
    state = enforce.activation_state()
    assert state["run_full"] is False
    assert not state.get("transport_grace", False)


def test_product_refresh_ring_survives_network_failure_and_recovery(
    monkeypatch,
    tmp_path: Path,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    grant = _paid_grant(clock)
    failure = _failure()
    recovered = _paid_grant(clock)
    _install_traced_results(
        monkeypatch,
        (grant, (("endpoint_1", "http", "http_response", clock.epoch),)),
        (
            failure,
            (
                ("endpoint_1", "tls", "tls_connection_reset", clock.epoch + 1),
                ("endpoint_2", "tcp", "connect_timeout", clock.epoch + 1),
            ),
        ),
        (
            recovered,
            (("endpoint_1", "http", "http_response", clock.epoch + 2),),
        ),
    )

    assert enforce.startup_activate() is grant
    clock.advance(1)
    assert enforce.refresh_activation() is failure
    clock.advance(1)
    assert enforce.refresh_activation() is recovered

    history = ca.read_activation_recheck_history(now=clock.epoch)
    assert len(history["sequences"]) == 1
    assert history["sequences"][0]["attempt_count"] == 2
    assert history["sequences"][0]["recovered_at"] == clock.epoch


def test_cold_start_network_failure_is_diagnostic_only_not_grace(
    monkeypatch,
    tmp_path: Path,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    failure = _failure()
    _install_traced_results(
        monkeypatch,
        (
            failure,
            (
                ("endpoint_1", "tls", "tls_connection_reset", clock.epoch),
                ("endpoint_2", "tcp", "connect_timeout", clock.epoch),
            ),
        ),
    )

    assert enforce.startup_activate() is failure
    assert enforce.should_run_full() is False
    assert not enforce.activation_state().get("transport_grace", False)
    history = ca.read_activation_recheck_history(now=clock.epoch)
    assert history["sequences"][0]["recovered_at"] is None
    assert history["sequences"][0]["attempt_count"] == 2


def test_transport_grace_expires_without_waiting_for_another_heartbeat(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    grant = _paid_grant(clock)
    failure = _failure()
    _install_results(monkeypatch, grant, failure)

    enforce.startup_activate()
    clock.advance(1)
    enforce.refresh_activation()
    assert enforce.should_run_full() is True

    clock.advance(15)
    assert enforce.current() is failure
    assert enforce.should_run_full() is False
    state = enforce.activation_state()
    assert state["run_full"] is False
    assert state["needs_token"] is False
    assert not state.get("transport_grace", False)


@pytest.mark.parametrize(
    "reason",
    [
        "bad_token",
        "expired",
        "trial_expired",
        "revoked",
        "device_revoked",
        "device_mismatch",
        "upgrade_required",
        "server_rejected",
    ],
)
def test_authoritative_rejection_never_uses_transport_grace(
    monkeypatch,
    activation_runtime,
    reason,
) -> None:
    clock, _identity = activation_runtime
    grant = _paid_grant(clock)
    denial = _failure(reason, failure_class="")
    _install_results(monkeypatch, grant, denial)

    enforce.startup_activate()
    clock.advance(1)
    assert enforce.refresh_activation() is denial
    assert enforce.current() is denial
    assert enforce.should_run_full() is False
    assert not enforce.activation_state().get("transport_grace", False)


@pytest.mark.parametrize("changed_identity", ["token", "device"])
def test_token_or_device_change_fails_closed_immediately(
    monkeypatch,
    activation_runtime,
    changed_identity,
) -> None:
    clock, identity = activation_runtime
    grant = _paid_grant(clock)
    failure = _failure()
    _install_results(monkeypatch, grant, failure)

    enforce.startup_activate()
    identity[changed_identity] += "-CHANGED"
    clock.advance(1)
    assert enforce.refresh_activation() is failure
    assert enforce.current() is failure
    assert enforce.should_run_full() is False


@pytest.mark.parametrize("changed_identity", ["token", "device"])
def test_inflight_token_or_device_change_never_uses_old_grant_grace(
    monkeypatch,
    activation_runtime,
    changed_identity,
) -> None:
    """The identity used before I/O is insufficient when local identity changes during I/O."""
    clock, identity = activation_runtime
    grant = _paid_grant(clock)
    failure = _failure()
    _install_results(monkeypatch, grant)
    assert enforce.startup_activate() is grant

    def _change_identity_during_request(**_kwargs):
        identity[changed_identity] += "-CHANGED"
        return failure

    monkeypatch.setattr(ca, "activate", _change_identity_during_request)
    clock.advance(1)
    assert enforce.refresh_activation() is failure
    assert enforce.current() is failure
    assert enforce.should_run_full() is False
    assert not enforce.activation_state().get("transport_grace", False)


def test_inflight_identity_read_failure_fails_closed(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    grant = _paid_grant(clock)
    failure = _failure()
    _install_results(monkeypatch, grant)
    assert enforce.startup_activate() is grant

    calls = 0

    def _device_then_unknown():
        nonlocal calls
        calls += 1
        if calls == 1:
            return "DEVICE-A"
        raise OSError("private device lookup failure")

    monkeypatch.setattr(ca, "device_fingerprint", _device_then_unknown)
    monkeypatch.setattr(ca, "activate", lambda **_kwargs: failure)
    clock.advance(1)
    assert enforce.refresh_activation() is failure
    assert enforce.current() is failure
    assert enforce.should_run_full() is False
    assert not enforce.activation_state().get("transport_grace", False)


def test_success_response_during_identity_change_is_not_committed_to_new_identity(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, identity = activation_runtime
    original = _paid_grant(clock)
    stale_success = _paid_grant(clock)
    _install_results(monkeypatch, original)
    assert enforce.startup_activate() is original

    def _switch_before_success_returns(**_kwargs):
        identity["token"] = "TOKEN-B"
        return stale_success

    monkeypatch.setattr(ca, "activate", _switch_before_success_returns)
    clock.advance(1)
    returned = enforce.refresh_activation()
    assert returned is not stale_success
    assert returned.activated is False
    assert enforce.current() is returned
    assert enforce.should_run_full() is False


@pytest.mark.parametrize("changed_identity", ("token", "device"))
def test_identity_change_after_post_read_before_commit_lock_fails_closed(
    monkeypatch,
    activation_runtime,
    changed_identity,
) -> None:
    """The final commit must not trust an identity read completed before its lock."""
    clock, identity = activation_runtime
    original = _paid_grant(clock)
    stale_success = _paid_grant(clock)
    _install_results(monkeypatch, original)
    assert enforce.startup_activate() is original

    armed = False
    inner_lock = threading.Lock()

    class _IdentitySwitchingLock:
        def __enter__(self):
            nonlocal armed
            inner_lock.acquire()
            if armed:
                identity[changed_identity] += "-CHANGED"
                armed = False
            return self

        def __exit__(self, exc_type, exc, tb):
            inner_lock.release()
            return False

    monkeypatch.setattr(enforce, "_LOCK", _IdentitySwitchingLock())

    def _success_then_switch_at_commit(**_kwargs):
        nonlocal armed
        armed = True
        return stale_success

    monkeypatch.setattr(ca, "activate", _success_then_switch_at_commit)
    clock.advance(1)
    returned = enforce.refresh_activation()

    assert identity[changed_identity].endswith("-CHANGED")
    assert returned is not stale_success
    assert returned.activated is False
    assert enforce.current() is returned
    assert enforce.should_run_full() is False
    assert not enforce.activation_state().get("transport_grace", False)


@pytest.mark.parametrize("changed_identity", ("token", "device"))
def test_identity_change_inside_commit_snapshot_fails_closed(
    monkeypatch,
    activation_runtime,
    changed_identity,
) -> None:
    """A token/device switch inside the combined commit read must not assemble stale A."""
    clock, identity = activation_runtime
    original = _paid_grant(clock)
    stale_success = _paid_grant(clock)
    _install_results(monkeypatch, original)
    assert enforce.startup_activate() is original

    final_snapshot = False
    switched = False
    inner_lock = threading.Lock()

    class _CommitSnapshotLock:
        def __enter__(self):
            nonlocal final_snapshot
            inner_lock.acquire()
            if armed:
                final_snapshot = True
            return self

        def __exit__(self, exc_type, exc, tb):
            inner_lock.release()
            return False

    original_resolve_token = ca.resolve_token
    original_device_fingerprint = ca.device_fingerprint

    def _resolve_token(ui_token=None):
        nonlocal switched
        value = original_resolve_token(ui_token)
        if final_snapshot and changed_identity == "token" and not switched:
            identity["token"] = "TOKEN-B"
            switched = True
        return value

    def _device_fingerprint():
        nonlocal switched
        value = original_device_fingerprint()
        if final_snapshot and changed_identity == "device" and not switched:
            identity["device"] = "DEVICE-B"
            switched = True
        return value

    armed = False
    monkeypatch.setattr(enforce, "_LOCK", _CommitSnapshotLock())
    monkeypatch.setattr(ca, "resolve_token", _resolve_token)
    monkeypatch.setattr(ca, "device_fingerprint", _device_fingerprint)

    def _success_then_arm_commit(**_kwargs):
        nonlocal armed
        armed = True
        return stale_success

    monkeypatch.setattr(ca, "activate", _success_then_arm_commit)
    clock.advance(1)
    returned = enforce.refresh_activation()

    assert switched is True
    assert identity[changed_identity].endswith("-B")
    assert returned is not stale_success
    assert returned.activated is False
    assert enforce.current() is returned
    assert enforce.should_run_full() is False


def test_stale_a_request_cannot_reopen_grace_after_a_b_a_refreshes(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    initial = _paid_grant(clock)
    grant_b = _paid_grant(clock)
    grant_a2 = _paid_grant(clock)
    stale_failure = _failure()
    outer_started = threading.Event()
    release_outer = threading.Event()
    calls_without_ui_token = 0

    def _activate(*, ui_token=None, **_kwargs):
        nonlocal calls_without_ui_token
        if ui_token == "TOKEN-B":
            return grant_b
        if ui_token == "TOKEN-A":
            return grant_a2
        calls_without_ui_token += 1
        if calls_without_ui_token == 1:
            return initial
        outer_started.set()
        assert release_outer.wait(timeout=5)
        return stale_failure

    monkeypatch.setattr(ca, "activate", _activate)
    assert enforce.startup_activate() is initial

    returned: list[object] = []
    worker = threading.Thread(target=lambda: returned.append(enforce.refresh_activation()))
    worker.start()
    assert outer_started.wait(timeout=5)
    assert enforce.startup_activate(ui_token="TOKEN-B", force_server=True) is grant_b
    assert enforce.startup_activate(ui_token="TOKEN-A", force_server=True) is grant_a2
    release_outer.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    assert returned == [stale_failure]
    assert enforce.current() is grant_a2
    assert enforce._LAST_HEARTBEAT is grant_a2
    assert enforce._HEARTBEAT_FAILURE_COUNT == 0
    assert enforce._ACTIVATION_REQUEST_GENERATION == 4
    assert not enforce.activation_state().get("transport_grace", False)


@pytest.mark.parametrize("changed_identity", ("token", "device"))
def test_versioned_identity_write_a_b_a_invalidates_inflight_success(
    monkeypatch,
    activation_runtime,
    changed_identity,
) -> None:
    """A -> B -> A remains stale because product identity writes advance revision."""
    clock, identity = activation_runtime
    original = _paid_grant(clock)
    stale_success = _paid_grant(clock)
    started = threading.Event()
    release = threading.Event()

    _install_results(monkeypatch, original)
    assert enforce.startup_activate() is original

    def _wait_then_succeed(**_kwargs):
        started.set()
        assert release.wait(timeout=5)
        return stale_success

    monkeypatch.setattr(ca, "activate", _wait_then_succeed)
    returned: list[object] = []
    worker = threading.Thread(target=lambda: returned.append(enforce.refresh_activation()))
    worker.start()
    assert started.wait(timeout=5)

    original_value = identity[changed_identity]
    accepted_b, _result_b, revision_b = ca._run_activation_identity_write(
        lambda: identity.__setitem__(changed_identity, f"{original_value}-B")
    )
    accepted_a, _result_a, revision_a = ca._run_activation_identity_write(
        lambda: identity.__setitem__(changed_identity, original_value)
    )
    assert accepted_b is True
    assert accepted_a is True
    assert revision_b == 2
    assert revision_a == 4

    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    assert len(returned) == 1
    assert returned[0] is not stale_success
    assert returned[0].activated is False
    assert enforce.current() is returned[0]
    assert enforce.should_run_full() is False


def test_validated_ui_token_owned_identity_write_commits_success(
    monkeypatch,
    activation_runtime,
) -> None:
    """The one versioned write owned by a validated UI token remains a valid commit."""
    _clock, identity = activation_runtime
    grant = _paid_grant(_clock)

    def _persist_validated_token(*, ui_token=None, **_kwargs):
        accepted, _result, revision = ca._run_activation_identity_write(
            lambda: identity.__setitem__("token", str(ui_token))
        )
        assert accepted is True
        assert revision == 2
        return grant

    monkeypatch.setattr(ca, "activate", _persist_validated_token)
    returned = enforce.startup_activate(
        ui_token="TOKEN-B",
        force_server=True,
    )

    assert returned is grant
    assert identity["token"] == "TOKEN-B"
    assert enforce.current() is grant
    assert enforce.should_run_full() is True


def test_disk_or_manually_seeded_last_grant_cannot_open_grace(
    monkeypatch,
    activation_runtime,
) -> None:
    clock, _identity = activation_runtime
    old = _paid_grant(clock)
    failure = _failure()
    enforce._LAST = old
    _install_results(monkeypatch, failure)

    assert enforce.refresh_activation() is failure
    assert enforce.current() is failure
    assert enforce.should_run_full() is False


@pytest.mark.parametrize("mode", ["paid", "trial"])
def test_transport_grace_never_extends_local_entitlement_expiry(
    monkeypatch,
    activation_runtime,
    mode,
) -> None:
    clock, _identity = activation_runtime
    grant = (
        _paid_grant(clock, seconds=5)
        if mode == "paid"
        else _trial_grant(clock, seconds=5)
    )
    failure = _failure()
    _install_results(monkeypatch, grant, failure)

    enforce.startup_activate()
    clock.advance(1)
    enforce.refresh_activation()
    assert enforce.should_run_full() is True
    clock.advance(5)
    assert enforce.should_run_full() is False
    assert enforce.activation_state()["run_full"] is False


def _overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location(
        "ahmad_tk_overlay_activation_grace_0_3_1",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_real_tk_expired_then_transport_failure_clears_old_latch_and_hides_token(
    monkeypatch,
    tmp_path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    current = {
        "run_full": False,
        "mode": "paid",
        "reason": "expired",
        "seconds_left": 0,
        "needs_token": True,
        "warn": True,
        "text": "会员已到期 · 需续费 token",
    }
    monkeypatch.setattr(enforce, "activation_state", lambda: dict(current))
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    overlay = None
    try:
        root.withdraw()
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "activation-grace" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        overlay._act_ready = True
        root.deiconify()
        root.update()

        assert overlay._update_activation_ui() is True
        root.update()
        assert overlay._expired_latched is True
        assert overlay.activation_action.winfo_ismapped()

        for reason in (
            "offline",
            "no_server",
            "state_error",
            "rate_limited",
            "server_error",
            "error:RuntimeError",
        ):
            current.update(
                run_full=False,
                mode="paid",
                reason="expired",
                needs_token=True,
                warn=True,
                text="会员已到期 · 需续费 token",
            )
            assert overlay._update_activation_ui() is True
            root.update()
            assert overlay._expired_latched is True

            current.update(
                run_full=False,
                mode="none",
                reason=reason,
                needs_token=False,
                warn=True,
                text=f"{reason} · 无需重复更换 token",
            )
            assert overlay._update_activation_ui() is True
            root.update()
            assert overlay._expired_latched is False
            assert not overlay.activation_action.winfo_ismapped()
            assert "试用已结束" not in str(overlay.activation_label.cget("text"))

        current.update(
            run_full=True,
            mode="paid",
            reason="transport_grace",
            transport_grace=True,
            seconds_left=29 * 86_400,
            needs_token=False,
            warn=False,
            text="激活连接波动，正在重试",
        )
        assert overlay._update_activation_ui() is False
        root.update()
        assert overlay._expired_latched is False
        assert not overlay.activation_action.winfo_ismapped()
        assert str(overlay.activation_label.cget("text")) == "激活连接波动，正在重试"
        assert str(overlay.activation_label.cget("fg")) == module.WARN

        current.update(
            run_full=True,
            mode="paid",
            reason="ok",
            transport_grace=False,
            seconds_left=29 * 86_400,
            needs_token=False,
            warn=False,
            text="会员 · 剩余 29 天 0 时",
        )
        assert overlay._update_activation_ui() is False
        root.update()
        assert overlay._expired_latched is False
        assert str(overlay.activation_label.cget("text")) == "会员 · 剩余 29 天 0 时"
    finally:
        if overlay is not None:
            try:
                overlay.destroy()
            except Exception:
                pass
        try:
            root.destroy()
        except tk.TclError:
            pass
