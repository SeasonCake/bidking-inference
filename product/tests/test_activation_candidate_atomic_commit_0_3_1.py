# -*- coding: utf-8 -*-
"""Activation candidate side effects must follow the final enforce commit gate."""
from __future__ import annotations

import base64
import json
import threading

import pytest

from bidking_lab.secure import activation
from bidking_lab.secure import client_activate as ca
from bidking_lab.secure import enforce


_DEVICE = "DEVICE-CANDIDATE-ATOMIC"
_TOKEN_A = "TOKEN-A"
_TOKEN_B = "TOKEN-B"
_KEY_A = b"A" * 32
_KEY_B = b"B" * 32
_EXP = 9_000_000_000


def _success_response(
    token: str,
    *,
    key: bytes | None = None,
    upload_lease: str | None = None,
) -> tuple[int, dict[str, object]]:
    key = key or (_KEY_A if token == _TOKEN_A else _KEY_B)
    lease = {
        "uid": f"uid-{token}",
        "device": _DEVICE,
        "exp": _EXP,
    }
    body = {
            "ok": True,
            "mode": "paid",
            "lease": json.dumps(lease) + ".sig",
            "master_key": base64.b64encode(key).decode("ascii"),
        }
    if upload_lease is not None:
        body["upload_lease"] = upload_lease
    return 200, body


def _upload_lease(label: str) -> str:
    payload = {
        "schema_version": 1,
        "kind": "sample_upload",
        "subject_kind": "paid",
        "subject_id": (label * 64)[:64],
        "uid_hash": "a" * 16,
        "device_alias": "b" * 64,
        "security_epoch": 7,
        "lease_id": "c" * 32,
        "iat": 2_000_000,
        "exp": 2_000_300,
        "entitlement_exp": None,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "." + "0" * 64


@pytest.fixture()
def isolated_activation_runtime(tmp_path, monkeypatch):
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    token_file = app_dir / "activation.txt"
    token_file.write_text(_TOKEN_A, encoding="utf-8")

    old_provider = activation._provider
    old_upload_lease = ca._PROCESS_UPLOAD_LEASE
    old_revision = ca._ACTIVATION_IDENTITY_SOURCE_REVISION
    old_runtime_denied = ca._RUNTIME_ACTIVATION_DENIED.is_set()
    old_enforce = {
        name: getattr(enforce, name)
        for name in (
            "_LAST",
            "_LAST_ONLINE_GRANT",
            "_LAST_ONLINE_IDENTITY",
            "_LAST_HEARTBEAT",
            "_HEARTBEAT_FAILURE_COUNT",
            "_HEARTBEAT_FAILURE_STARTED_AT",
            "_ACTIVATION_REQUEST_GENERATION",
        )
    }
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.delenv("BIDKING_ACTIVATION_FILE", raising=False)
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "_package")
    monkeypatch.setattr(ca, "device_fingerprint", lambda: _DEVICE)
    monkeypatch.setattr(ca, "_activate_urls", lambda _ingest: ["https://activation.invalid/activate"])
    monkeypatch.setattr(ca.time, "time", lambda: 2_000_000)
    monkeypatch.setattr(enforce.time, "time", lambda: 2_000_000)
    ca._CACHE_INVALIDATED_IN_PROCESS = False
    ca._RUNTIME_ACTIVATION_DENIED.clear()
    ca._PROCESS_UPLOAD_LEASE = None
    ca._ACTIVATION_IDENTITY_SOURCE_REVISION = 0
    enforce._LAST = None
    enforce._LAST_ONLINE_GRANT = None
    enforce._LAST_ONLINE_IDENTITY = None
    enforce._LAST_HEARTBEAT = None
    enforce._HEARTBEAT_FAILURE_COUNT = 0
    enforce._HEARTBEAT_FAILURE_STARTED_AT = None
    enforce._ACTIVATION_REQUEST_GENERATION = 0
    try:
        yield token_file
    finally:
        activation._provider = old_provider
        ca._CACHE_INVALIDATED_IN_PROCESS = False
        if old_runtime_denied:
            ca._RUNTIME_ACTIVATION_DENIED.set()
        else:
            ca._RUNTIME_ACTIVATION_DENIED.clear()
        ca._ACTIVATION_IDENTITY_SOURCE_REVISION = old_revision
        ca._PROCESS_UPLOAD_LEASE = old_upload_lease
        for name, value in old_enforce.items():
            setattr(enforce, name, value)


def test_late_a_success_cannot_overwrite_committed_b_provider_or_cache(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    """Real startup path: B commits first; stale A must have zero side effects."""
    token_file = isolated_activation_runtime
    a_started = threading.Event()
    release_a = threading.Event()

    def _post_activate(_url, _device, token, _timeout):
        if token == _TOKEN_A:
            a_started.set()
            assert release_a.wait(timeout=5)
        return _success_response(token)

    monkeypatch.setattr(ca, "_post_activate", _post_activate)
    returned_a: list[object] = []
    worker = threading.Thread(
        target=lambda: returned_a.append(enforce.startup_activate(force_server=True))
    )
    worker.start()
    assert a_started.wait(timeout=5)

    assert ca.save_token(_TOKEN_B) is True
    result_b = enforce.startup_activate(force_server=True)
    assert result_b.activated is True
    assert activation.data_key() == _KEY_B
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_B) is not None

    release_a.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    assert token_file.read_text(encoding="utf-8").strip() == _TOKEN_B
    assert enforce.current() is result_b
    assert activation.data_key() == _KEY_B
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_B) is not None
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_A) is None


def test_upload_lease_commit_is_identity_atomic_for_late_a_and_aba(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    token_file = isolated_activation_runtime
    first_a_started = threading.Event()
    release_first_a = threading.Event()
    calls = 0
    first_a_lease = _upload_lease("1")
    b_lease = _upload_lease("2")
    current_a_lease = _upload_lease("3")

    def _post_activate(_url, _device, token, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_a_started.set()
            assert release_first_a.wait(timeout=5)
            return _success_response(token, upload_lease=first_a_lease)
        return _success_response(
            token,
            upload_lease=(b_lease if token == _TOKEN_B else current_a_lease),
        )

    monkeypatch.setattr(ca, "_post_activate", _post_activate)
    returned_first: list[object] = []
    worker = threading.Thread(
        target=lambda: returned_first.append(
            enforce.startup_activate(force_server=True)
        )
    )
    worker.start()
    assert first_a_started.wait(timeout=5)

    assert ca.save_token(_TOKEN_B) is True
    result_b = enforce.startup_activate(force_server=True)
    assert result_b.activated is True
    assert ca.current_upload_lease(now=2_000_001) == b_lease

    assert ca.save_token(_TOKEN_A) is True
    result_a = enforce.startup_activate(force_server=True)
    assert result_a.activated is True
    assert ca.current_upload_lease(now=2_000_001) == current_a_lease

    release_first_a.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert token_file.read_text(encoding="utf-8").strip() == _TOKEN_A
    assert enforce.current() is result_a
    assert ca.current_upload_lease(now=2_000_001) == current_a_lease
    assert first_a_lease != ca.current_upload_lease(now=2_000_001)


def test_deferred_success_has_no_provider_or_cache_side_effect_before_commit(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    old_provider = activation._provider
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )

    candidate = ca.activate(
        ingest_url="https://activation.invalid/ingest",
        force_server=True,
        defer_commit=True,
    )

    assert isinstance(candidate, ca.ActivationCandidate)
    assert candidate.result.activated is True
    assert activation._provider is old_provider
    assert not ca._cache_file().exists()
    assert isolated_activation_runtime.read_text(encoding="utf-8").strip() == _TOKEN_A

    outcome = ca.commit_activation_candidate(candidate)
    assert outcome.committed is True
    assert outcome.result is candidate.result
    assert activation.data_key() == _KEY_A
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_A) is not None


def test_a_b_a_late_first_a_cannot_overwrite_newer_a_commit(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    first_a_started = threading.Event()
    release_first_a = threading.Event()
    calls = 0
    newer_key = b"N" * 32

    def _post_activate(_url, _device, token, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_a_started.set()
            assert release_first_a.wait(timeout=5)
            return _success_response(token, key=_KEY_A)
        return _success_response(token, key=newer_key)

    monkeypatch.setattr(ca, "_post_activate", _post_activate)
    returned_first: list[object] = []
    worker = threading.Thread(
        target=lambda: returned_first.append(
            enforce.startup_activate(force_server=True)
        )
    )
    worker.start()
    assert first_a_started.wait(timeout=5)

    assert ca.save_token(_TOKEN_B) is True
    assert ca.save_token(_TOKEN_A) is True
    result_newer = enforce.startup_activate(force_server=True)
    assert result_newer.activated is True
    assert activation.data_key() == newer_key

    release_first_a.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    assert enforce.current() is result_newer
    assert activation.data_key() == newer_key
    cached = ca._load_cache(device=_DEVICE, token=_TOKEN_A)
    assert cached is not None
    assert base64.b64decode(cached["mk"]) == newer_key


@pytest.mark.parametrize("failed_step", ("token", "cache", "provider"))
def test_candidate_commit_failure_restores_token_cache_and_provider_together(
    monkeypatch,
    isolated_activation_runtime,
    failed_step,
) -> None:
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True
    assert activation.data_key() == _KEY_A
    old_cache = ca._cache_file().read_bytes()

    def _raise_commit_error(*_args, **_kwargs):
        raise OSError(f"synthetic {failed_step} write failure")

    if failed_step == "token":
        monkeypatch.setattr(ca, "save_token", _raise_commit_error)
    elif failed_step == "cache":
        monkeypatch.setattr(ca, "_save_cache", _raise_commit_error)
    else:
        monkeypatch.setattr(ca, "_install_provider", _raise_commit_error)

    failed = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert failed.activated is False
    assert failed.reason == "local_commit_failed"
    assert enforce.current() is failed
    assert enforce.should_run_full() is False
    assert isolated_activation_runtime.read_text(
        encoding="utf-8"
    ).strip() == _TOKEN_A
    assert ca._cache_file().read_bytes() == old_cache
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_A) is not None
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_B) is None
    assert activation.is_activated() is False
    with pytest.raises(ca.DataKeyUnavailable):
        activation.data_key()


def test_current_success_commits_provider_and_cache_exactly_once(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    save_token = ca.save_token
    save_cache = ca._save_cache
    install_provider = ca._install_provider
    calls = {"token": 0, "cache": 0, "provider": 0}

    def _save_token_once(token):
        calls["token"] += 1
        return save_token(token)

    def _save_once(**kwargs):
        calls["cache"] += 1
        return save_cache(**kwargs)

    def _install_once(*args, **kwargs):
        calls["provider"] += 1
        return install_provider(*args, **kwargs)

    monkeypatch.setattr(ca, "save_token", _save_token_once)
    monkeypatch.setattr(ca, "_save_cache", _save_once)
    monkeypatch.setattr(ca, "_install_provider", _install_once)

    result = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert result.activated is True
    assert calls == {"token": 1, "cache": 1, "provider": 1}
    assert enforce.current() is result
    assert isolated_activation_runtime.read_text(
        encoding="utf-8"
    ).strip() == _TOKEN_B
    assert activation.data_key() == _KEY_B


def test_provider_setter_partial_mutation_then_exception_still_fails_closed(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True
    old_cache = ca._cache_file().read_bytes()

    def _mutate_then_raise(provider):
        activation._provider = provider
        raise OSError("synthetic provider setter failure")

    monkeypatch.setattr(activation, "set_provider", _mutate_then_raise)
    failed = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert failed.activated is False
    assert failed.reason == "local_commit_failed"
    assert enforce.current() is failed
    assert isolated_activation_runtime.read_text(
        encoding="utf-8"
    ).strip() == _TOKEN_A
    assert ca._cache_file().read_bytes() == old_cache
    assert activation.is_activated() is False


def test_stale_authoritative_denial_cannot_clear_newer_b_cache(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    a_started = threading.Event()
    release_a = threading.Event()

    def _post_activate(_url, _device, token, _timeout):
        if token == _TOKEN_A:
            a_started.set()
            assert release_a.wait(timeout=5)
            return 401, {"ok": False, "mode": "paid", "reason": "bad_token"}
        return _success_response(token)

    monkeypatch.setattr(ca, "_post_activate", _post_activate)
    returned_a: list[object] = []
    worker = threading.Thread(
        target=lambda: returned_a.append(enforce.startup_activate(force_server=True))
    )
    worker.start()
    assert a_started.wait(timeout=5)

    assert ca.save_token(_TOKEN_B) is True
    result_b = enforce.startup_activate(force_server=True)
    assert result_b.activated is True
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_B) is not None

    release_a.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    assert returned_a and returned_a[0].activated is False
    assert enforce.current() is result_b
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_B) is not None
    assert activation.data_key() == _KEY_B


def test_provider_partial_write_and_restore_exception_never_escape_startup(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    """A failed rollback must not leave a successful provider outside enforce."""
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True

    def _mutate_then_raise(provider):
        activation._provider = provider
        raise OSError("synthetic provider setter failure")

    def _restore_raises(_snapshot):
        raise OSError("synthetic persistence restore failure")

    monkeypatch.setattr(activation, "set_provider", _mutate_then_raise)
    monkeypatch.setattr(ca, "_restore_activation_persistence", _restore_raises)

    failed = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert failed.activated is False
    assert failed.reason == "local_commit_failed"
    assert enforce.current() is failed
    assert enforce.should_run_full() is False
    assert activation.is_activated() is False
    with pytest.raises(ca.DataKeyUnavailable):
        activation.data_key()
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_B) is None


def test_restore_false_invalidates_partial_cache_and_denies_runtime(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    server_tokens: list[str] = []

    def _post_activate(_url, _device, token, _timeout):
        server_tokens.append(token)
        return _success_response(token)

    monkeypatch.setattr(ca, "_post_activate", _post_activate)
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True

    install_provider = ca._install_provider
    monkeypatch.setattr(ca, "_install_provider", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        ca,
        "_restore_activation_persistence",
        lambda _snapshot: False,
    )

    failed = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert failed.activated is False
    assert failed.reason == "local_commit_failed"
    assert enforce.current() is failed
    assert enforce.should_run_full() is False
    assert ca._CACHE_INVALIDATED_IN_PROCESS is True
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_B) is None
    assert activation.is_activated() is False

    # The uncertain B token is only a credential, never an offline grant.
    # Restoring the normal provider step must perform another server request.
    monkeypatch.setattr(ca, "_install_provider", install_provider)
    retry = enforce.startup_activate(force_server=False)
    assert retry.activated is True
    assert server_tokens == [_TOKEN_A, _TOKEN_B, _TOKEN_B]
    assert activation.data_key() == _KEY_B


def test_cache_cleanup_exception_after_failed_restore_remains_fail_closed(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True

    monkeypatch.setattr(ca, "_install_provider", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        ca,
        "_restore_activation_persistence",
        lambda _snapshot: False,
    )

    def _clear_cache_raises():
        raise OSError("synthetic cache cleanup failure")

    monkeypatch.setattr(ca, "_clear_cache", _clear_cache_raises)

    failed = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert failed.activated is False
    assert failed.reason == "local_commit_failed"
    assert enforce.current() is failed
    assert enforce.should_run_full() is False
    assert ca._CACHE_INVALIDATED_IN_PROCESS is True
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_B) is None
    assert activation.is_activated() is False


def test_fail_closed_provider_step_exception_uses_emergency_denial(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True
    old_cache = ca._cache_file().read_bytes()

    monkeypatch.setattr(ca, "_install_provider", lambda *_args, **_kwargs: False)

    def _deny_step_raises(_device):
        raise OSError("synthetic deny provider failure")

    monkeypatch.setattr(ca, "_force_fail_closed_provider", _deny_step_raises)

    failed = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert failed.activated is False
    assert failed.reason == "local_commit_failed"
    assert enforce.current() is failed
    assert enforce.should_run_full() is False
    assert isolated_activation_runtime.read_text(
        encoding="utf-8"
    ).strip() == _TOKEN_A
    assert ca._cache_file().read_bytes() == old_cache
    assert activation.is_activated() is False
    with pytest.raises(ca.DataKeyUnavailable):
        activation.data_key()


def test_enforce_final_boundary_contains_unexpected_commit_exception(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True
    assert activation.is_activated() is True

    def _commit_raises(_candidate):
        raise OSError("synthetic unexpected commit failure")

    monkeypatch.setattr(ca, "commit_activation_candidate", _commit_raises)

    failed = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert failed.activated is False
    assert failed.reason == "local_commit_failed"
    assert enforce.current() is failed
    assert enforce.should_run_full() is False
    assert activation.is_activated() is False
    with pytest.raises(ca.DataKeyUnavailable):
        activation.data_key()
    assert ca._load_cache(device=_DEVICE, token=_TOKEN_A) is None


def test_all_recovery_setters_failing_cannot_leave_partial_provider_authorized(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    """A provider that mutates before raising must be denied without any setter."""
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True
    assert activation.data_key() == _KEY_A

    def _provider_writes_then_raises(provider):
        activation._provider = provider
        raise OSError("synthetic partial provider commit")

    def _recovery_raises(*_args, **_kwargs):
        raise OSError("synthetic recovery failure")

    monkeypatch.setattr(activation, "set_provider", _provider_writes_then_raises)
    monkeypatch.setattr(ca, "_restore_activation_persistence", _recovery_raises)
    monkeypatch.setattr(ca, "_clear_cache", _recovery_raises)
    monkeypatch.setattr(ca, "_force_fail_closed_provider", _recovery_raises)
    monkeypatch.setattr(ca, "_emergency_fail_closed_provider", _recovery_raises)

    failed = enforce.startup_activate(
        ui_token=_TOKEN_B,
        force_server=True,
    )

    assert failed.activated is False
    assert failed.reason == "local_commit_failed"
    assert enforce.current() is failed
    assert enforce.should_run_full() is False
    assert ca._CACHE_INVALIDATED_IN_PROCESS is True
    assert activation.is_activated() is False
    with pytest.raises(ca.DataKeyUnavailable):
        activation.data_key()


def test_runtime_deny_latch_covers_accessors_until_new_full_server_commit(
    monkeypatch,
    isolated_activation_runtime,
) -> None:
    """Transport retries cannot reopen a denied provider; a full commit can."""
    monkeypatch.setenv("BIDKING_INTEGRITY_DIGEST", "synthetic-digest")
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    original = enforce.startup_activate(force_server=True)
    assert original.activated is True
    assert activation.is_activated() is True
    assert activation.data_key() == _KEY_A
    assert activation.integrity_digest() == "synthetic-digest"

    def _must_not_report(*_args, **_kwargs):
        raise AssertionError("denied provider attempted telemetry")

    monkeypatch.setattr(ca, "urlopen_verified", _must_not_report)
    with monkeypatch.context() as recovery:
        recovery.setattr(
            ca,
            "_clear_cache",
            lambda: (_ for _ in ()).throw(OSError("synthetic cleanup failure")),
        )
        recovery.setattr(
            ca,
            "_emergency_fail_closed_provider",
            lambda _device: (_ for _ in ()).throw(
                OSError("synthetic provider failure")
            ),
        )
        assert ca.force_activation_runtime_fail_closed(_DEVICE) is False

    assert activation.is_activated() is False
    with pytest.raises(ca.DataKeyUnavailable):
        activation.data_key()
    assert activation.integrity_digest() is None
    activation.report_suspicious("synthetic-verdict")

    # A transport-only failure has no authoritative grant commit and therefore
    # cannot clear the process deny latch.
    monkeypatch.setattr(ca, "_post_activate", lambda *_args, **_kwargs: None)
    retry_failed = enforce.startup_activate(force_server=True)
    assert retry_failed.activated is False
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
    assert activation.is_activated() is False

    # Only another complete, current-identity server grant re-enables access.
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    recovered = enforce.startup_activate(force_server=True)
    assert recovered.activated is True
    assert not ca._RUNTIME_ACTIVATION_DENIED.is_set()
    assert activation.is_activated() is True
    assert activation.data_key() == _KEY_A


@pytest.mark.parametrize("candidate_kind", ("grant", "authoritative_denial"))
def test_stale_candidate_cannot_clear_existing_runtime_deny_latch(
    monkeypatch,
    isolated_activation_runtime,
    candidate_kind,
) -> None:
    if candidate_kind == "grant":
        response = _success_response(_TOKEN_A)
    else:
        response = 401, {
            "ok": False,
            "mode": "paid",
            "reason": "bad_token",
        }
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, _token, _timeout: response,
    )
    candidate = ca.activate(force_server=True, defer_commit=True)
    assert isinstance(candidate, ca.ActivationCandidate)

    assert ca.save_token(_TOKEN_B) is True
    ca._RUNTIME_ACTIVATION_DENIED.set()
    outcome = ca.commit_activation_candidate(candidate)

    assert outcome.committed is False
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
