# -*- coding: utf-8 -*-
"""Clearing/rebinding must revoke runtime access before persistence or I/O."""
from __future__ import annotations

import base64
import json
import threading

import pytest

from bidking_lab.secure import activation
from bidking_lab.secure import client_activate as ca
from bidking_lab.secure import enforce


_DEVICE = "DEVICE-CLEAR-RUNTIME-DENY"
_TOKEN_A = "TOKEN-CLEAR-A"
_TOKEN_B = "TOKEN-CLEAR-B"
_KEY_A = b"A" * 32
_KEY_B = b"B" * 32
_EXP = 9_000_000_000


def _success_response(
    token: str,
    *,
    key: bytes | None = None,
) -> tuple[int, dict[str, object]]:
    key = key or (_KEY_A if token == _TOKEN_A else _KEY_B)
    lease = {
        "uid": f"uid-{token}",
        "device": _DEVICE,
        "exp": _EXP,
    }
    return (
        200,
        {
            "ok": True,
            "mode": "paid",
            "lease": json.dumps(lease) + ".sig",
            "master_key": base64.b64encode(key).decode("ascii"),
        },
    )


@pytest.fixture()
def isolated_clear_runtime(tmp_path, monkeypatch):
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    token_file = app_dir / "activation.txt"
    token_file.write_text(_TOKEN_A, encoding="utf-8")

    old_provider = activation._provider
    old_revision = ca._ACTIVATION_IDENTITY_SOURCE_REVISION
    old_clear_epoch = ca._ACTIVATION_CLEAR_EPOCH
    old_clear_state = ca._ACTIVATION_CLEAR_STATE
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
    monkeypatch.setenv("BIDKING_INTEGRITY_DIGEST", "clear-runtime-digest")
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "_package")
    monkeypatch.setattr(ca, "device_fingerprint", lambda: _DEVICE)
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: ["https://activation.invalid/activate"],
    )
    monkeypatch.setattr(ca.time, "time", lambda: 2_000_000)
    monkeypatch.setattr(enforce.time, "time", lambda: 2_000_000)
    ca._CACHE_INVALIDATED_IN_PROCESS = False
    ca._RUNTIME_ACTIVATION_DENIED.clear()
    ca._ACTIVATION_IDENTITY_SOURCE_REVISION = 0
    ca._ACTIVATION_CLEAR_EPOCH = 0
    ca._ACTIVATION_CLEAR_STATE = None
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
        ca._ACTIVATION_CLEAR_EPOCH = old_clear_epoch
        ca._ACTIVATION_CLEAR_STATE = old_clear_state
        for name, value in old_enforce.items():
            setattr(enforce, name, value)


def _grant_a(monkeypatch: pytest.MonkeyPatch) -> ca.ActivationResult:
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    result = enforce.startup_activate(force_server=True)
    assert result.activated is True
    assert activation.is_activated() is True
    assert activation.data_key() == _KEY_A
    return result


def _assert_runtime_denied(telemetry_calls: list[object]) -> None:
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
    assert activation.is_activated() is False
    with pytest.raises(ca.DataKeyUnavailable):
        activation.data_key()
    assert activation.integrity_digest() is None
    activation.report_suspicious("clear-runtime-verdict")
    assert telemetry_calls == []


def test_direct_client_clear_revokes_all_enforce_product_views(
    monkeypatch,
    isolated_clear_runtime,
) -> None:
    granted_a = _grant_a(monkeypatch)
    telemetry_calls: list[object] = []
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda *args, **kwargs: telemetry_calls.append((args, kwargs)),
    )

    info = ca.clear_activation()

    assert info["cleared"] is True
    assert enforce.current().activated is False
    assert enforce.should_run_full() is False
    assert enforce.should_run_full(granted_a) is False
    assert enforce.activation_state()["run_full"] is False
    assert enforce.activation_state(granted_a)["run_full"] is False
    _assert_runtime_denied(telemetry_calls)

    assert ca.save_token(_TOKEN_B) is True
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(
            token,
            key=_KEY_B,
        ),
    )
    recovered = enforce.startup_activate(force_server=True)

    assert recovered.activated is True
    assert enforce.current() is recovered
    assert enforce.should_run_full() is True
    assert enforce.activation_state()["run_full"] is True
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set() is False
    assert activation.is_activated() is True
    assert activation.data_key() == _KEY_B


def test_enforce_clear_denies_before_persistence_and_blocked_offline_refresh(
    monkeypatch,
    isolated_clear_runtime,
) -> None:
    _grant_a(monkeypatch)
    telemetry_calls: list[object] = []
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda *args, **kwargs: telemetry_calls.append((args, kwargs)),
    )
    clear_entered = threading.Event()
    release_clear = threading.Event()
    original_clear_tokens = ca._clear_persisted_tokens
    observed_boundary: dict[str, object] = {}

    def _blocked_clear_tokens() -> bool:
        observed_boundary.update(
            {
                "runtime_denied": ca._RUNTIME_ACTIVATION_DENIED.is_set(),
                "generation": enforce._ACTIVATION_REQUEST_GENERATION,
                "revision": ca._ACTIVATION_IDENTITY_SOURCE_REVISION,
            }
        )
        clear_entered.set()
        assert release_clear.wait(timeout=5)
        return original_clear_tokens()

    monkeypatch.setattr(ca, "_clear_persisted_tokens", _blocked_clear_tokens)
    monkeypatch.setattr(ca, "_post_activate", lambda *_args, **_kwargs: None)
    returned: list[dict[str, object]] = []
    worker = threading.Thread(target=lambda: returned.append(enforce.clear_activation()))
    worker.start()
    assert clear_entered.wait(timeout=5)

    try:
        assert observed_boundary["runtime_denied"] is True
        assert observed_boundary["generation"] == 2
        assert int(observed_boundary["revision"]) >= 3
        _assert_runtime_denied(telemetry_calls)
    finally:
        release_clear.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert returned and returned[0]["token_cleared"] is True
    assert enforce.current().activated is False
    _assert_runtime_denied(telemetry_calls)


@pytest.mark.parametrize(
    "refresh_response",
    (
        None,
        (401, {"ok": False, "mode": "paid", "reason": "bad_token"}),
    ),
)
def test_clear_failure_or_authoritative_refresh_failure_keeps_runtime_denied(
    monkeypatch,
    isolated_clear_runtime,
    refresh_response,
) -> None:
    _grant_a(monkeypatch)
    telemetry_calls: list[object] = []
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda *args, **kwargs: telemetry_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        ca,
        "_clear_persisted_tokens",
        lambda: (_ for _ in ()).throw(OSError("synthetic clear failure")),
    )
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda *_args, **_kwargs: refresh_response,
    )

    info = enforce.clear_activation()

    assert info["cleared"] is False
    _assert_runtime_denied(telemetry_calls)

    repeated = enforce.clear_activation()
    assert repeated["cleared"] is False
    _assert_runtime_denied(telemetry_calls)


def test_clear_failure_same_old_identity_success_cannot_reenable_runtime(
    monkeypatch,
    isolated_clear_runtime,
) -> None:
    _grant_a(monkeypatch)
    telemetry_calls: list[object] = []
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda *args, **kwargs: telemetry_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        ca,
        "_clear_persisted_tokens",
        lambda: (_ for _ in ()).throw(OSError("synthetic clear failure")),
    )
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )

    info = enforce.clear_activation()

    assert info["cleared"] is False
    assert isolated_clear_runtime.read_text(encoding="utf-8").strip() == _TOKEN_A
    assert enforce.current().activated is False
    _assert_runtime_denied(telemetry_calls)


def test_failed_clear_epoch_cannot_be_unlocked_by_later_identity_write(
    monkeypatch,
    isolated_clear_runtime,
) -> None:
    _grant_a(monkeypatch)
    monkeypatch.setattr(
        ca,
        "_clear_persisted_tokens",
        lambda: (_ for _ in ()).throw(OSError("synthetic clear failure")),
    )
    monkeypatch.setattr(ca, "_post_activate", lambda *_args, **_kwargs: None)

    info = enforce.clear_activation()
    assert info["cleared"] is False
    assert ca.save_token(_TOKEN_B) is True

    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(
            token,
            key=_KEY_B,
        ),
    )
    result = enforce.startup_activate(force_server=True)

    assert result.activated is False
    assert result.reason == "local_clear_incomplete"
    _assert_runtime_denied([])


def test_clear_aba_and_stale_success_cannot_reenable_runtime(
    monkeypatch,
    isolated_clear_runtime,
) -> None:
    _grant_a(monkeypatch)
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(token),
    )
    stale_a = ca.activate(force_server=True, defer_commit=True)
    assert isinstance(stale_a, ca.ActivationCandidate)

    monkeypatch.setattr(ca, "_post_activate", lambda *_args, **_kwargs: None)
    enforce.clear_activation()
    assert ca.save_token(_TOKEN_B) is True
    assert ca.save_token(_TOKEN_A) is True

    outcome = ca.commit_activation_candidate(stale_a)

    assert outcome.committed is False
    _assert_runtime_denied([])


def test_only_new_current_identity_server_commit_clears_clear_deny(
    monkeypatch,
    isolated_clear_runtime,
) -> None:
    _grant_a(monkeypatch)
    monkeypatch.setattr(ca, "_post_activate", lambda *_args, **_kwargs: None)
    enforce.clear_activation()
    assert ca.save_token(_TOKEN_B) is True
    _assert_runtime_denied([])

    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda _url, _device, token, _timeout: _success_response(
            token,
            key=_KEY_B,
        ),
    )
    recovered = enforce.startup_activate(force_server=True)

    assert recovered.activated is True
    assert not ca._RUNTIME_ACTIVATION_DENIED.is_set()
    assert activation.is_activated() is True
    assert activation.data_key() == _KEY_B
