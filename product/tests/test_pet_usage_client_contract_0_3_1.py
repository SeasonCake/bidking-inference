from __future__ import annotations

import base64
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path

import pytest

from bidking_lab.secure import activation
from bidking_lab.secure import client_activate as client
from bidking_lab.secure import enforce


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TOOLS = ROOT / "hero_ref" / "tools"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402


SECRET = b"pet-client-contract-activation-secret"
MASTER_KEY = bytes(range(32))
MASTER_KEY_B64 = base64.b64encode(MASTER_KEY).decode("ascii")
BASE_NOW = 2_000_000_000
SESSION_ID = "PET-CLIENT-SESSION-0001"


def _overlay():
    name = "ahmad_tk_overlay_pet_a3_aba_contract"
    spec = importlib.util.spec_from_file_location(
        name,
        TOOLS / "ahmad_tk_overlay.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _service(tmp_path: Path) -> server.ActivationService:
    authority = tmp_path / "pet-authority"
    authority.mkdir(exist_ok=True)
    database = authority / "pet_usage.sqlite3"
    if not database.exists():
        server.initialize_pet_usage_database(
            database,
            secret=SECRET,
            legacy_paths=(
                tmp_path / "pet_usage.json",
                tmp_path / "pet_usage.anchor.jsonl",
            ),
        )
    return server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY_B64,
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
        paid_starts_path=tmp_path / "paid_starts.json",
        pet_usage_db_path=database,
        pet_usage_path=tmp_path / "pet_usage.json",
        pet_usage_lease_seconds=15,
    )


def _token(uid: str, days: int | None, *, roaming: bool = False) -> str:
    iat = 1_700_000_000
    return core.mint_token(
        uid,
        SECRET,
        iat=iat,
        exp=(None if days is None else iat + days * 86_400),
        roaming=roaming,
    )


def _server_activate(
    service: server.ActivationService,
    *,
    token: str | None,
    device: str,
    event: dict | None = None,
) -> tuple[int, dict]:
    body: dict = {"device": device}
    if token is not None:
        body["token"] = token
    if event is not None:
        body["pet_usage"] = event
    return service.handle_activate(body)


def _lease(body: dict) -> dict:
    return json.loads(body["lease"].rsplit(".", 1)[0])


def _resign_pet_time(
    body: dict,
    *,
    server_now: int,
) -> dict:
    mutated = json.loads(json.dumps(body))
    lease = _lease(mutated)
    pet = lease["feature_usage"]["pet_companion"]
    pet["server_now"] = server_now
    pet["visibility_lease_exp"] = server_now
    mutated["lease"] = core.sign_lease(lease, SECRET)
    return mutated


def _raw_quota_pet() -> dict:
    return {
        "kind": "quota",
        "total_seconds": 43_200,
        "status": "available",
        "server_now": BASE_NOW,
        "duration_days": 30,
        "entitlement_id": "a" * 64,
        "usage_revision": 1,
        "remaining_seconds": 43_200,
        "last_seq": -1,
        "visible": False,
        "visibility_lease_exp": BASE_NOW,
        "entitlement_exp": BASE_NOW + 86_400,
    }


def _raw_paid_pet_result(pet: dict) -> client.ActivationResult:
    return client.ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        source="server",
        master_key=MASTER_KEY,
        lease_exp=BASE_NOW + 3_600,
        paid_exp=BASE_NOW + 86_400,
        plan_kind="30d",
        feature_entitlements={"pet_companion": "quota"},
        feature_usage={"pet_companion": pet},
    )


def _raw_pet_result(
    pet: dict,
    *,
    plan_kind: str,
    entitlement: str,
    paid_exp: int | None,
    mode: str = "paid",
    trial_exp: int | None = None,
) -> client.ActivationResult:
    return client.ActivationResult(
        activated=True,
        mode=mode,
        reason="ok",
        source="server",
        trial_exp=trial_exp,
        master_key=MASTER_KEY,
        lease_exp=BASE_NOW + 3_600,
        paid_exp=paid_exp,
        plan_kind=plan_kind,
        feature_entitlements={"pet_companion": entitlement},
        feature_usage={"pet_companion": pet},
    )


def _rotating_activation_candidate(
    *,
    token: str,
    device: str,
) -> client.ActivationCandidate:
    identity = client.activation_identity_snapshot(token)
    assert identity is not None
    lease_exp = BASE_NOW + 3_600
    return client.ActivationCandidate(
        result=client.ActivationResult(
            activated=True,
            mode="paid",
            reason="ok",
            source="server",
            master_key=MASTER_KEY,
            lease_exp=lease_exp,
            paid_exp=BASE_NOW + 30 * 86_400,
            plan_kind="30d",
            feature_entitlements={"pet_companion": "quota"},
            feature_usage={"pet_companion": _raw_quota_pet()},
        ),
        identity_snapshot=identity,
        ui_token=token,
        commit_plan=client._ActivationCommitPlan(
            kind="grant",
            lease={
                "uid": f"candidate-{token[-12:]}",
                "device": device,
                "exp": lease_exp,
            },
            master_key=MASTER_KEY,
            device=device,
            token=token,
            mode="paid",
            now=BASE_NOW,
            exp=lease_exp,
            rotating_ui_token=True,
        ),
    )


def _run_supported_identity_writer(
    writer_kind: str,
    *,
    token_a: str,
    token_b: str,
    device: str,
) -> None:
    if writer_kind == "clear":
        assert enforce.clear_activation()["cleared"] is True
        return
    if writer_kind == "save_token":
        assert client.save_token(token_b)
        assert client.save_token(token_a)
        return
    if writer_kind == "ui_token":
        for token in (token_b, token_a):
            identity = client.activation_identity_snapshot(token)
            assert identity is not None
            assert client._commit_ui_token(
                token,
                expected_revision=identity.revision,
            )
        return
    if writer_kind == "candidate":
        for token in (token_b, token_a):
            outcome = client.commit_activation_candidate(
                _rotating_activation_candidate(
                    token=token,
                    device=device,
                )
            )
            assert outcome.committed is True
        return
    raise AssertionError(f"unsupported identity writer: {writer_kind}")


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(client, "_package_dir", lambda: tmp_path / "package")
    monkeypatch.setattr(client, "_activate_urls", lambda _ingest: ("https://unit/activate",))
    monkeypatch.setattr(client.time, "time", lambda: BASE_NOW)
    client._CACHE_INVALIDATED_IN_PROCESS = False
    client._RUNTIME_ACTIVATION_DENIED.clear()
    with enforce._LOCK:
        enforce._LAST = None
        enforce._LAST_ONLINE_GRANT = None
        enforce._LAST_ONLINE_IDENTITY = None
        enforce._LAST_PET_USAGE_AUTHORITY = None
        enforce._LAST_HEARTBEAT = None
        enforce._HEARTBEAT_FAILURE_COUNT = 0
        enforce._HEARTBEAT_FAILURE_STARTED_AT = None
        enforce._PET_USAGE_IDENTITY_GENERATION = 0
        enforce._LAST_OBSERVED_PET_USAGE_IDENTITY = None
    yield
    client._CACHE_INVALIDATED_IN_PROCESS = False
    client._RUNTIME_ACTIVATION_DENIED.clear()
    activation.set_provider(activation._DevStubProvider())


@pytest.mark.parametrize(
    ("days", "expected_plan", "expected_kind", "expected_total"),
    [
        (29, "custom", "none", None),
        (30, "30d", "quota", 43_200),
        (31, "custom", "quota", 86_400),
        (60, "custom", "quota", 86_400),
        (89, "custom", "quota", 86_400),
        (90, "90d", "full", None),
        (365, "365d", "full", None),
        (None, "legacy_permanent", "full", None),
    ],
)
def test_real_server_signed_duration_matrix_reaches_client_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    days: int | None,
    expected_plan: str,
    expected_kind: str,
    expected_total: int | None,
) -> None:
    device = f"PET-CLIENT-{days}"
    token = _token(f"pet-client-{days}", days)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    code, body = _server_activate(
        _service(tmp_path),
        token=token,
        device=device,
    )
    monkeypatch.setattr(client, "_post_activate", lambda *_args: (code, body))

    result = client.activate(ingest_url="https://unit", now=BASE_NOW)
    state = enforce.activation_state(result, now=BASE_NOW)

    assert result.activated is True
    assert state["plan_kind"] == expected_plan
    assert state["feature_entitlements"]["pet_companion"] == expected_kind
    pet = state["feature_usage"]["pet_companion"]
    assert pet["kind"] == expected_kind
    if expected_total is not None:
        assert pet["total_seconds"] == expected_total
        assert pet["remaining_seconds"] == expected_total


def test_real_trial_signed_usage_is_600_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-CLIENT-TRIAL"
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    code, body = _server_activate(
        _service(tmp_path),
        token=None,
        device=device,
    )
    monkeypatch.setattr(client, "_post_activate", lambda *_args: (code, body))

    result = client.activate(ingest_url="https://unit", now=BASE_NOW)
    state = enforce.activation_state(result, now=BASE_NOW)

    assert result.activated is True
    assert state["feature_entitlements"] == {"pet_companion": "trial"}
    assert state["feature_usage"]["pet_companion"]["total_seconds"] == 600


def test_product_pet_usage_event_reaches_server_and_transport_failure_is_pet_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-PRODUCT-EVENT-DEVICE"
    token = _token("pet-product-event", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    events: list[dict | None] = []

    def _post(_url, post_device, post_token, _timeout, event=None):
        events.append(None if event is None else dict(event))
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _post)
    initial = enforce.startup_activate(force_server=True)
    assert initial.feature_usage["pet_companion"]["status"] == "available"

    visible_event = {
        "session_id": SESSION_ID,
        "seq": 0,
        "visible": True,
    }
    active = enforce.refresh_pet_usage(visible_event)
    active_pet = active.feature_usage["pet_companion"]
    assert active_pet["status"] == "active"
    assert active_pet["last_seq"] == 0
    assert active_pet["visible"] is True
    assert events == [None, visible_event]
    assert enforce.current() is active
    assert enforce.should_run_full() is True

    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW + 15)
    hidden_event = {
        "session_id": SESSION_ID,
        "seq": 1,
        "visible": False,
    }
    paused = enforce.refresh_pet_usage(hidden_event)
    paused_pet = paused.feature_usage["pet_companion"]
    assert paused_pet["status"] == "paused"
    assert paused_pet["last_seq"] == 1
    assert paused_pet["visible"] is False
    assert paused_pet["remaining_seconds"] == 43_185
    assert events == [None, visible_event, hidden_event]

    monkeypatch.setattr(client, "_post_activate", lambda *_args: None)
    failed = enforce.refresh_pet_usage(
        {
            "session_id": SESSION_ID,
            "seq": 2,
            "visible": True,
        }
    )
    assert failed.activated is False
    assert failed.reason == "offline"
    assert enforce.current() is paused
    assert enforce.should_run_full() is True


def test_product_pet_usage_aba_only_submits_current_a_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay_module = _overlay()
    device = "PET-PRODUCT-ABA-DEVICE"
    token_a = _token("pet-product-aba-a", 30)
    token_b = _token("pet-product-aba-b", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    calls: list[tuple[str, dict | None, str | None]] = []

    def _post(_url, post_device, post_token, _timeout, event=None):
        code, body = _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )
        pet_status = None
        if event is not None and code == 200:
            pet_status = _lease(body)["feature_usage"]["pet_companion"][
                "status"
            ]
        calls.append(
            (
                "a" if post_token == token_a else "b",
                None if event is None else dict(event),
                pet_status,
            )
        )
        return code, body

    monkeypatch.setattr(client, "_post_activate", _post)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token_a)
    assert enforce.startup_activate(force_server=True).activated
    stale_a_authority = enforce.pet_usage_request_authority_snapshot()
    assert stale_a_authority is not None

    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    overlay._act_state = enforce.activation_state()
    overlay._pet_usage_request_queue = overlay_module.queue.Queue()
    overlay._pet_usage_worker_running = True
    overlay._pet_usage_identity_marker = None
    overlay._pet_usage_authority_snapshot = None
    overlay._pet_usage_session_id = "unused-session-marker"
    overlay._pet_usage_next_seq = 0

    assert overlay._queue_product_pet_usage_event(
        "start",
        overlay_module.pet_access_from_activation(overlay._act_state),
    )
    old_a_session = overlay._pet_usage_session_id

    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token_b)
    assert enforce.startup_activate(force_server=True).activated
    overlay._act_state = enforce.activation_state()
    assert overlay._queue_product_pet_usage_event(
        "start",
        overlay_module.pet_access_from_activation(overlay._act_state),
    )

    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token_a)
    assert enforce.startup_activate(force_server=True).activated
    overlay._act_state = enforce.activation_state()
    assert overlay._queue_product_pet_usage_event(
        "start",
        overlay_module.pet_access_from_activation(overlay._act_state),
    )
    current_a_session = overlay._pet_usage_session_id
    assert current_a_session != old_a_session

    threading.Thread(
        target=overlay._run_product_pet_usage_worker,
        daemon=True,
    ).start()
    overlay._pet_usage_request_queue.join()

    event_calls = [entry for entry in calls if entry[1] is not None]
    assert event_calls == [
        (
            "a",
            {
                "session_id": current_a_session,
                "seq": 0,
                "visible": True,
            },
            "active",
        )
    ]
    calls_before_stale_send = len(calls)
    assert enforce.refresh_pet_usage(
        {
            "session_id": old_a_session,
            "seq": 0,
            "visible": True,
        },
        expected_authority=stale_a_authority,
    ) is None
    assert len(calls) == calls_before_stale_send
    assert enforce.should_run_full() is True


def test_product_pet_usage_aba_before_transport_is_zero_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-PRODUCT-TRANSPORT-START-DEVICE"
    token_a = _token("pet-product-transport-start-a", 30)
    token_b = _token("pet-product-transport-start-b", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    calls: list[dict | None] = []

    def _post(_url, post_device, post_token, _timeout, event=None):
        calls.append(None if event is None else dict(event))
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _post)
    assert client.save_token(token_a)
    assert enforce.startup_activate(force_server=True).activated
    authority_a1 = enforce.pet_usage_request_authority_snapshot()
    assert authority_a1 is not None
    calls.clear()

    original_activate = client.activate
    identity_switched = False

    def _activate_after_aba(*args, **kwargs):
        nonlocal identity_switched
        if not identity_switched:
            identity_switched = True
            assert client.save_token(token_b)
            assert client.save_token(token_a)
        return original_activate(*args, **kwargs)

    monkeypatch.setattr(client, "activate", _activate_after_aba)
    stale_event = {
        "session_id": "a" * 32,
        "seq": 0,
        "visible": True,
    }
    result = enforce.refresh_pet_usage(
        stale_event,
        expected_authority=authority_a1,
    )

    assert identity_switched is True
    assert result is not None and result.activated is False
    assert result.reason == "state_error"
    assert calls == []
    assert enforce.should_run_full() is False


def test_pet_transport_start_gate_releases_identity_writer_during_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-PRODUCT-TRANSPORT-LOCK-DEVICE"
    token_a = _token("pet-product-transport-lock-a", 30)
    token_b = _token("pet-product-transport-lock-b", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)

    def _post(_url, post_device, post_token, _timeout, event=None):
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _post)
    assert client.save_token(token_a)
    assert enforce.startup_activate(force_server=True).activated
    authority_a1 = enforce.pet_usage_request_authority_snapshot()
    assert authority_a1 is not None

    transport_entered = threading.Event()
    release_transport = threading.Event()

    def _blocking_post(
        _url,
        post_device,
        post_token,
        _timeout,
        event=None,
    ):
        transport_entered.set()
        assert release_transport.wait(timeout=2.0)
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _blocking_post)
    result_box: list[client.ActivationResult | None] = []

    request_thread = threading.Thread(
        target=lambda: result_box.append(
            enforce.refresh_pet_usage(
                {
                    "session_id": "b" * 32,
                    "seq": 0,
                    "visible": True,
                },
                expected_authority=authority_a1,
            )
        ),
        daemon=True,
    )
    request_thread.start()
    assert transport_entered.wait(timeout=1.0)

    identity_write_done = threading.Event()

    def _write_identity() -> None:
        assert client.save_token(token_b)
        assert client.save_token(token_a)
        identity_write_done.set()

    writer = threading.Thread(target=_write_identity, daemon=True)
    writer.start()
    assert identity_write_done.wait(timeout=1.0)
    release_transport.set()
    request_thread.join(timeout=2.0)
    writer.join(timeout=2.0)

    assert not request_thread.is_alive()
    assert result_box and result_box[0] is not None
    assert result_box[0].activated is False
    assert result_box[0].reason == "state_error"


def test_pet_transport_begin_does_not_use_worker_entered_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-PRODUCT-BEGIN-NO-EVENT-DEVICE"
    token = _token("pet-product-begin-no-event", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    calls: list[dict | None] = []

    def _post(_url, post_device, post_token, _timeout, event=None):
        calls.append(None if event is None else dict(event))
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _post)
    assert client.save_token(token)
    assert enforce.startup_activate(force_server=True).activated
    authority = enforce.pet_usage_request_authority_snapshot()
    assert authority is not None
    calls.clear()

    class _ForbiddenEnteredEvent:
        def __init__(self) -> None:
            raise AssertionError("worker-entered is not a transport begin")

    monkeypatch.setattr(
        client,
        "threading",
        types.SimpleNamespace(
            Event=_ForbiddenEnteredEvent,
            Thread=threading.Thread,
        ),
    )
    result = enforce.refresh_pet_usage(
        {
            "session_id": "c" * 32,
            "seq": 0,
            "visible": True,
        },
        expected_authority=authority,
    )

    assert result is not None and result.activated is True
    assert result.reason == "ok"
    assert calls == [
        {
            "session_id": "c" * 32,
            "seq": 0,
            "visible": True,
        }
    ]


def test_pet_identity_writer_wins_before_transport_begin_with_zero_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-PRODUCT-WRITER-FIRST-DEVICE"
    token_a = _token("pet-product-writer-first-a", 30)
    token_b = _token("pet-product-writer-first-b", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)

    def _post(_url, post_device, post_token, _timeout, event=None):
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _post)
    assert client.save_token(token_a)
    assert enforce.startup_activate(force_server=True).activated
    authority_a1 = enforce.pet_usage_request_authority_snapshot()
    assert authority_a1 is not None

    begin_calls: list[dict] = []

    def _begin(_url, _device, _token, _timeout, event):
        begin_calls.append(dict(event))
        raise AssertionError("stale event reached transport begin")

    monkeypatch.setattr(client, "_begin_activation_post", _begin)
    writer_has_barrier = threading.Event()
    let_writer_commit = threading.Event()

    def _write_identity_first() -> None:
        with client._ACTIVATION_TRANSPORT_START_LOCK:
            writer_has_barrier.set()
            assert let_writer_commit.wait(timeout=2.0)
            assert client.save_token(token_b)
            assert client.save_token(token_a)

    writer = threading.Thread(target=_write_identity_first, daemon=True)
    writer.start()
    assert writer_has_barrier.wait(timeout=1.0)

    result_box: list[client.ActivationResult | None] = []
    request = threading.Thread(
        target=lambda: result_box.append(
            enforce.refresh_pet_usage(
                {
                    "session_id": "d" * 32,
                    "seq": 0,
                    "visible": True,
                },
                expected_authority=authority_a1,
            )
        ),
        daemon=True,
    )
    request.start()
    let_writer_commit.set()
    request.join(timeout=2.0)
    writer.join(timeout=2.0)

    assert not request.is_alive()
    assert not writer.is_alive()
    assert result_box and result_box[0] is not None
    assert result_box[0].activated is False
    assert result_box[0].reason == "state_error"
    assert begin_calls == []


def test_pet_transport_begin_wins_and_response_wait_is_outside_identity_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-PRODUCT-BEGIN-FIRST-DEVICE"
    token_a = _token("pet-product-begin-first-a", 30)
    token_b = _token("pet-product-begin-first-b", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)

    def _post(_url, post_device, post_token, _timeout, event=None):
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _post)
    assert client.save_token(token_a)
    assert enforce.startup_activate(force_server=True).activated
    authority_a1 = enforce.pet_usage_request_authority_snapshot()
    assert authority_a1 is not None

    begin_called = threading.Event()
    release_response = threading.Event()
    begin_calls: list[dict] = []

    class _FakeHandle:
        def result(self):
            assert release_response.wait(timeout=2.0)
            return _server_activate(
                service,
                token=token_a,
                device=device,
                event=begin_calls[0],
            )

    def _begin(_url, _device, _token, _timeout, event):
        begin_calls.append(dict(event))
        begin_called.set()
        return _FakeHandle()

    monkeypatch.setattr(client, "_begin_activation_post", _begin)
    result_box: list[client.ActivationResult | None] = []
    request = threading.Thread(
        target=lambda: result_box.append(
            enforce.refresh_pet_usage(
                {
                    "session_id": "e" * 32,
                    "seq": 0,
                    "visible": True,
                },
                expected_authority=authority_a1,
            )
        ),
        daemon=True,
    )
    request.start()
    assert begin_called.wait(timeout=1.0)

    identity_write_done = threading.Event()

    def _write_identity() -> None:
        assert client.save_token(token_b)
        assert client.save_token(token_a)
        identity_write_done.set()

    writer = threading.Thread(target=_write_identity, daemon=True)
    writer.start()
    assert identity_write_done.wait(timeout=1.0)
    release_response.set()
    request.join(timeout=2.0)
    writer.join(timeout=2.0)

    assert not request.is_alive()
    assert result_box and result_box[0] is not None
    assert result_box[0].activated is False
    assert result_box[0].reason == "state_error"
    assert begin_calls == [
        {
            "session_id": "e" * 32,
            "seq": 0,
            "visible": True,
        }
    ]


@pytest.mark.parametrize(
    "writer_kind",
    ("clear", "save_token", "candidate", "ui_token"),
)
@pytest.mark.parametrize("writer_order", ("writer_first", "begin_first"))
def test_pet_transport_begin_and_all_identity_writers_share_one_lock_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_kind: str,
    writer_order: str,
) -> None:
    device = f"PET-PRODUCT-LOCK-ORDER-{writer_kind}"
    token_a = _token(f"pet-lock-order-{writer_kind}-a", 30)
    token_b = _token(f"pet-lock-order-{writer_kind}-b", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    monkeypatch.setattr(client, "_ACTIVATION_IDENTITY_SOURCE_REVISION", 0)
    monkeypatch.setattr(client, "_ACTIVATION_CLEAR_EPOCH", 0)
    monkeypatch.setattr(client, "_ACTIVATION_CLEAR_STATE", None)
    monkeypatch.setattr(enforce, "_ACTIVATION_REQUEST_GENERATION", 0)

    def _post(_url, post_device, post_token, _timeout, event=None):
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _post)
    assert client.save_token(token_a)
    assert enforce.startup_activate(force_server=True).activated
    authority_a1 = enforce.pet_usage_request_authority_snapshot()
    assert authority_a1 is not None

    begin_called = threading.Event()
    release_response = threading.Event()
    begin_calls: list[dict] = []

    class _FakeHandle:
        def result(self):
            assert release_response.wait(timeout=3.0)
            return _server_activate(
                service,
                token=token_a,
                device=device,
                event=begin_calls[0],
            )

    def _begin(_url, _device, _token, _timeout, event):
        begin_calls.append(dict(event))
        begin_called.set()
        return _FakeHandle()

    monkeypatch.setattr(client, "_begin_activation_post", _begin)
    event = {
        "session_id": "f" * 32,
        "seq": 0,
        "visible": True,
    }

    if writer_order == "writer_first":
        _run_supported_identity_writer(
            writer_kind,
            token_a=token_a,
            token_b=token_b,
            device=device,
        )
        result = enforce.refresh_pet_usage(
            event,
            expected_authority=authority_a1,
        )
        assert result is None
        assert begin_calls == []
        assert enforce.should_run_full() is True
        return

    result_box: list[client.ActivationResult | None] = []
    request = threading.Thread(
        target=lambda: result_box.append(
            enforce.refresh_pet_usage(
                event,
                expected_authority=authority_a1,
            )
        ),
        daemon=True,
    )
    request.start()
    assert begin_called.wait(timeout=1.0)

    writer_done = threading.Event()
    writer_errors: list[BaseException] = []

    def _write_identity() -> None:
        try:
            _run_supported_identity_writer(
                writer_kind,
                token_a=token_a,
                token_b=token_b,
                device=device,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            writer_errors.append(exc)
        finally:
            writer_done.set()

    writer = threading.Thread(target=_write_identity, daemon=True)
    writer.start()
    assert writer_done.wait(timeout=2.0)
    assert writer_errors == []
    release_response.set()
    request.join(timeout=3.0)
    writer.join(timeout=3.0)

    assert not request.is_alive()
    assert not writer.is_alive()
    assert result_box and result_box[0] is not None
    assert result_box[0].activated is False
    assert result_box[0].reason == "state_error"
    assert begin_calls == [event]
    assert enforce.should_run_full() is (writer_kind == "clear")


def test_pet_transport_partial_begin_and_enforce_clear_cannot_deadlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-PRODUCT-PARTIAL-BEGIN-CLEAR"
    token = _token("pet-partial-begin-clear", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    monkeypatch.setattr(client, "_ACTIVATION_IDENTITY_SOURCE_REVISION", 0)
    monkeypatch.setattr(client, "_ACTIVATION_CLEAR_EPOCH", 0)
    monkeypatch.setattr(client, "_ACTIVATION_CLEAR_STATE", None)
    monkeypatch.setattr(enforce, "_ACTIVATION_REQUEST_GENERATION", 0)

    def _post(_url, post_device, post_token, _timeout, event=None):
        return _server_activate(
            service,
            token=post_token,
            device=post_device,
            event=event,
        )

    monkeypatch.setattr(client, "_post_activate", _post)
    assert client.save_token(token)
    assert enforce.startup_activate(force_server=True).activated
    authority = enforce.pet_usage_request_authority_snapshot()
    assert authority is not None

    first_transport_acquired = threading.Event()
    release_first_transport = threading.Event()
    blocked_transport_waiter = threading.Event()
    original_transport_lock = client._ACTIVATION_TRANSPORT_START_LOCK

    class _PauseFirstTransportAcquire:
        def __init__(self) -> None:
            self._state_lock = threading.Lock()
            self._first = True

        def __enter__(self):
            if not original_transport_lock.acquire(blocking=False):
                blocked_transport_waiter.set()
                original_transport_lock.acquire()
            with self._state_lock:
                first = self._first
                self._first = False
            if first:
                first_transport_acquired.set()
                assert release_first_transport.wait(timeout=3.0)
            return self

        def __exit__(self, *_args):
            original_transport_lock.release()
            return False

    monkeypatch.setattr(
        client,
        "_ACTIVATION_TRANSPORT_START_LOCK",
        _PauseFirstTransportAcquire(),
    )

    begin_called = threading.Event()
    release_response = threading.Event()

    class _FakeHandle:
        def result(self):
            assert release_response.wait(timeout=3.0)
            return _server_activate(
                service,
                token=token,
                device=device,
                event={
                    "session_id": "1" * 32,
                    "seq": 0,
                    "visible": True,
                },
            )

    def _begin(*_args):
        begin_called.set()
        return _FakeHandle()

    monkeypatch.setattr(client, "_begin_activation_post", _begin)
    request_results: list[client.ActivationResult | None] = []
    request = threading.Thread(
        target=lambda: request_results.append(
            enforce.refresh_pet_usage(
                {
                    "session_id": "1" * 32,
                    "seq": 0,
                    "visible": True,
                },
                expected_authority=authority,
            )
        ),
        daemon=True,
    )
    request.start()
    assert first_transport_acquired.wait(timeout=1.0)

    clear_done = threading.Event()
    clear_results: list[dict] = []

    def _clear() -> None:
        clear_results.append(enforce.clear_activation())
        clear_done.set()

    clear = threading.Thread(target=_clear, daemon=True)
    clear.start()
    assert blocked_transport_waiter.wait(timeout=1.0)
    release_first_transport.set()
    assert begin_called.wait(timeout=1.0)
    assert clear_done.wait(timeout=2.0)
    assert clear_results and clear_results[0]["cleared"] is True

    release_response.set()
    request.join(timeout=3.0)
    clear.join(timeout=3.0)
    assert not request.is_alive()
    assert not clear.is_alive()
    assert request_results and request_results[0] is not None
    assert request_results[0].activated is False
    assert request_results[0].reason == "state_error"
    assert enforce.should_run_full() is True


def test_post_activate_serializes_exact_pet_usage_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return 200

        def read(self):
            return b'{"ok": false, "reason": "state_error"}'

    def _open(request, *, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(client, "urlopen_verified", _open)
    event = {
        "session_id": SESSION_ID,
        "seq": 7,
        "visible": False,
    }

    assert client._post_activate(
        "https://unit/activate",
        "DEVICE",
        "TOKEN",
        1.25,
        event,
    ) == (200, {"ok": False, "reason": "state_error"})
    assert captured["pet_usage"] == event
    assert captured["device"] == "DEVICE"
    assert captured["token"] == "TOKEN"
    assert captured["timeout"] == 1.25


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_usage",
        "usage_bool",
        "schema_bool",
        "revision_huge",
        "remaining_bool",
        "remaining_over_total",
        "lease_expired",
        "wrong_duration",
        "extra_field",
        "server_state_error",
    ],
)
def test_signed_malformed_pet_contract_denies_only_pet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    device = f"PET-MALFORMED-{mutation}"
    token = _token(f"pet-malformed-{mutation}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    code, body = _server_activate(
        _service(tmp_path),
        token=token,
        device=device,
    )
    lease = _lease(body)
    pet = lease["feature_usage"]["pet_companion"]
    if mutation == "missing_usage":
        lease.pop("feature_usage")
    elif mutation == "usage_bool":
        lease["feature_usage"] = True
    elif mutation == "schema_bool":
        lease["feature_usage"]["schema_version"] = True
    elif mutation == "revision_huge":
        pet["usage_revision"] = 10**400
    elif mutation == "remaining_bool":
        pet["remaining_seconds"] = True
    elif mutation == "remaining_over_total":
        pet["remaining_seconds"] = pet["total_seconds"] + 1
    elif mutation == "lease_expired":
        pet["visibility_lease_exp"] = pet["server_now"] - 1
        pet["visible"] = True
        pet["status"] = "active"
    elif mutation == "wrong_duration":
        pet["duration_days"] = 31
    elif mutation == "extra_field":
        pet["used_seconds"] = 0
    elif mutation == "server_state_error":
        lease["feature_entitlements"] = {"pet_companion": "none"}
        lease["feature_usage"]["pet_companion"] = {
            "kind": "quota",
            "status": "state_error",
            "server_now": BASE_NOW,
        }
    body["lease"] = core.sign_lease(lease, SECRET)
    monkeypatch.setattr(client, "_post_activate", lambda *_args: (code, body))

    result = client.activate(ingest_url="https://unit", now=BASE_NOW)
    state = enforce.activation_state(result, now=BASE_NOW)

    assert result.activated is True
    assert enforce.should_run_full(result, now=BASE_NOW) is True
    assert state["feature_entitlements"] == {"pet_companion": "none"}
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"


def test_invalid_signed_envelope_syntax_denies_only_pet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-INVALID-SIGNATURE"
    token = _token("pet-invalid-signature", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    code, body = _server_activate(
        _service(tmp_path),
        token=token,
        device=device,
    )
    payload = body["lease"].rsplit(".", 1)[0]
    body["lease"] = f"{payload}.not-a-server-signature"
    monkeypatch.setattr(client, "_post_activate", lambda *_args: (code, body))

    result = client.activate(ingest_url="https://unit", now=BASE_NOW)
    state = enforce.activation_state(result, now=BASE_NOW)

    assert result.activated is True
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"


def test_usage_revision_rollback_is_pet_only_state_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-REVISION-ROLLBACK"
    token = _token("pet-revision-rollback", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    code1, body1 = _server_activate(
        service,
        token=token,
        device=device,
        event={"session_id": SESSION_ID, "seq": 0, "visible": True},
    )
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW + 15)
    code2, body2 = _server_activate(
        service,
        token=token,
        device=device,
        event={"session_id": SESSION_ID, "seq": 1, "visible": False},
    )
    responses = iter(
        ((code1, body1), (code2, body2), (code1, body1), (code1, body1))
    )
    monkeypatch.setattr(client, "_post_activate", lambda *_args: next(responses))

    first = enforce.startup_activate(force_server=True)
    second = enforce.startup_activate(force_server=True)
    replay = enforce.startup_activate(force_server=True)
    replay_again = enforce.startup_activate(force_server=True)
    state = enforce.activation_state(replay, now=BASE_NOW + 15)
    repeated_state = enforce.activation_state(replay_again, now=BASE_NOW + 15)

    assert first.feature_usage["pet_companion"]["usage_revision"] == 1
    assert second.feature_usage["pet_companion"]["usage_revision"] == 2
    assert second.feature_usage["pet_companion"]["status"] == "paused"
    assert replay.activated is True
    assert enforce.should_run_full(replay, now=BASE_NOW + 15) is True
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert repeated_state["feature_usage"]["pet_companion"]["status"] == "state_error"


def test_higher_revision_server_time_rollback_is_pet_only_state_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-SERVER-TIME-ROLLBACK"
    token = _token("pet-server-time-rollback", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    code1, body1 = _server_activate(
        service,
        token=token,
        device=device,
        event={"session_id": SESSION_ID, "seq": 0, "visible": True},
    )
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW + 15)
    code2, body2 = _server_activate(
        service,
        token=token,
        device=device,
        event={"session_id": SESSION_ID, "seq": 1, "visible": False},
    )
    rollback_body = _resign_pet_time(body2, server_now=BASE_NOW - 1)
    responses = iter(
        ((code1, body1), (code2, rollback_body), (code2, rollback_body))
    )
    monkeypatch.setattr(client, "_post_activate", lambda *_args: next(responses))

    first = enforce.startup_activate(force_server=True)
    rollback = enforce.startup_activate(force_server=True)
    repeated = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert first.feature_usage["pet_companion"]["usage_revision"] == 1
    assert rollback.activated is True
    assert repeated.activated is True
    assert rollback.feature_usage["pet_companion"]["status"] == "state_error"
    assert repeated.feature_usage["pet_companion"]["status"] == "state_error"
    assert enforce.current() is repeated
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert enforce.should_run_full(repeated, now=BASE_NOW + 15) is True


def test_same_credential_revision_progress_does_not_reset_pet_time_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-SERVER-TIME-SAME-IDENTITY"
    token = _token("pet-server-time-same-identity", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    code1, body1 = _server_activate(
        service,
        token=token,
        device=device,
        event={"session_id": SESSION_ID, "seq": 0, "visible": True},
    )
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW + 15)
    code2, body2 = _server_activate(
        service,
        token=token,
        device=device,
        event={"session_id": SESSION_ID, "seq": 1, "visible": False},
    )
    rollback_body = _resign_pet_time(body2, server_now=BASE_NOW - 1)
    responses = iter(((code1, body1), (code2, rollback_body)))
    monkeypatch.setattr(client, "_post_activate", lambda *_args: next(responses))

    before = client.activation_identity_snapshot(token)
    first = enforce.startup_activate(ui_token=token, force_server=True)
    after_first = client.activation_identity_snapshot(token)
    rollback = enforce.startup_activate(ui_token=token, force_server=True)
    after_second = client.activation_identity_snapshot(token)
    state = enforce.activation_state()

    assert before is not None
    assert after_first is not None
    assert after_second is not None
    assert after_first.revision == before.revision + 2
    assert after_second.revision == after_first.revision + 2
    assert first.feature_usage["pet_companion"]["server_now"] == BASE_NOW
    assert rollback.activated is True
    assert rollback.feature_usage["pet_companion"]["status"] == "state_error"
    assert enforce.current() is rollback
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True


def test_new_identity_resets_pet_server_time_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_a = "PET-SERVER-TIME-IDENTITY-A"
    device_b = "PET-SERVER-TIME-IDENTITY-B"
    token_a = _token("pet-server-time-identity-a", 30)
    token_b = _token("pet-server-time-identity-b", 30)
    service = _service(tmp_path)
    current_device = {"value": device_a}
    monkeypatch.setattr(
        client,
        "device_fingerprint",
        lambda: current_device["value"],
    )
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token_a)
    code_a, body_a = _server_activate(
        service,
        token=token_a,
        device=device_a,
        event={"session_id": f"{SESSION_ID}-A", "seq": 0, "visible": True},
    )
    code_b, body_b = _server_activate(
        service,
        token=token_b,
        device=device_b,
    )
    lower_body_b = _resign_pet_time(body_b, server_now=BASE_NOW - 1)
    responses = iter(((code_a, body_a), (code_b, lower_body_b)))
    monkeypatch.setattr(client, "_post_activate", lambda *_args: next(responses))

    first = enforce.startup_activate(force_server=True)
    current_device["value"] = device_b
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token_b)
    second = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert first.feature_usage["pet_companion"]["server_now"] == BASE_NOW
    assert second.activated is True
    assert second.feature_usage["pet_companion"]["server_now"] == BASE_NOW - 1
    assert second.feature_usage["pet_companion"]["status"] == "available"
    assert state["feature_usage"]["pet_companion"]["status"] == "available"
    assert state["run_full"] is True


def test_real_identity_a_to_b_to_a_resets_pet_time_floor_each_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_a = "PET-SERVER-TIME-ABA-A"
    device_b = "PET-SERVER-TIME-ABA-B"
    token_a = _token("pet-server-time-aba-a", 30)
    token_b = _token("pet-server-time-aba-b", 30)
    service = _service(tmp_path)
    current_device = {"value": device_a}
    monkeypatch.setattr(
        client,
        "device_fingerprint",
        lambda: current_device["value"],
    )
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    code_a, body_a = _server_activate(
        service,
        token=token_a,
        device=device_a,
    )
    code_b, body_b = _server_activate(
        service,
        token=token_b,
        device=device_b,
    )
    lower_body_b = _resign_pet_time(body_b, server_now=BASE_NOW - 10)
    lower_body_a = _resign_pet_time(body_a, server_now=BASE_NOW - 20)
    responses = iter(
        ((code_a, body_a), (code_b, lower_body_b), (code_a, lower_body_a))
    )
    monkeypatch.setattr(client, "_post_activate", lambda *_args: next(responses))

    first_a = enforce.startup_activate(ui_token=token_a, force_server=True)
    current_device["value"] = device_b
    second_b = enforce.startup_activate(ui_token=token_b, force_server=True)
    current_device["value"] = device_a
    third_a = enforce.startup_activate(ui_token=token_a, force_server=True)
    state = enforce.activation_state()

    assert first_a.feature_usage["pet_companion"]["server_now"] == BASE_NOW
    assert second_b.feature_usage["pet_companion"]["server_now"] == BASE_NOW - 10
    assert third_a.feature_usage["pet_companion"]["server_now"] == BASE_NOW - 20
    assert second_b.feature_usage["pet_companion"]["status"] == "available"
    assert third_a.feature_usage["pet_companion"]["status"] == "available"
    assert state["feature_usage"]["pet_companion"]["status"] == "available"
    assert state["run_full"] is True


def test_same_revision_conflict_is_pet_only_state_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-REVISION-CONFLICT"
    token = _token("pet-revision-conflict", 30)
    service = _service(tmp_path)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    code, first_body = _server_activate(
        service,
        token=token,
        device=device,
        event={"session_id": SESSION_ID, "seq": 0, "visible": True},
    )
    conflicting_lease = _lease(first_body)
    conflicting_lease["feature_usage"]["pet_companion"]["remaining_seconds"] -= 1
    conflicting_body = {
        **first_body,
        "lease": core.sign_lease(conflicting_lease, SECRET),
    }
    responses = iter(((code, first_body), (code, conflicting_body)))
    monkeypatch.setattr(client, "_post_activate", lambda *_args: next(responses))

    first = enforce.startup_activate(force_server=True)
    conflicting = enforce.startup_activate(force_server=True)
    state = enforce.activation_state(conflicting, now=BASE_NOW)

    assert first.feature_usage["pet_companion"]["usage_revision"] == 1
    assert conflicting.activated is True
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert enforce.should_run_full(conflicting, now=BASE_NOW) is True


def test_raw_activation_result_pet_authority_never_calls_value_protocols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    class BombValue:
        def __eq__(self, _other):
            calls["count"] += 1
            raise RuntimeError("pet authority equality protocol executed")

        def __ne__(self, _other):
            calls["count"] += 1
            raise RuntimeError("pet authority inequality protocol executed")

    device = "PET-RAW-AUTHORITY-NOMAGIC"
    token = _token("pet-raw-authority-nomagic", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    stable_pet = _raw_quota_pet()
    invalid_pet = dict(stable_pet)
    invalid_pet["remaining_seconds"] = BombValue()

    responses = iter(
        (_raw_paid_pet_result(stable_pet), _raw_paid_pet_result(invalid_pet))
    )
    monkeypatch.setattr(client, "activate", lambda **_kwargs: next(responses))

    first = enforce.startup_activate(force_server=True)
    invalid = enforce.startup_activate(force_server=True)
    current = enforce.current()
    state = enforce.activation_state()

    assert first.feature_usage["pet_companion"]["status"] == "available"
    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert current is invalid
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert enforce.should_run_full(invalid, now=BASE_NOW) is True
    assert calls["count"] == 0


def test_raw_pet_authority_rejects_absurd_server_epoch_and_recovers_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-RAW-AUTHORITY-EPOCH-BOUND"
    token = _token("pet-raw-authority-epoch-bound", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    stable_pet = _raw_quota_pet()
    invalid_pet = dict(stable_pet)
    invalid_pet["usage_revision"] = 2
    invalid_pet["server_now"] = 10**400
    recovered_pet = dict(stable_pet)
    recovered_pet["usage_revision"] = 2
    recovered_pet["server_now"] = BASE_NOW + 1
    recovered_pet["visibility_lease_exp"] = BASE_NOW + 1

    responses = iter(
        (
            _raw_paid_pet_result(stable_pet),
            _raw_paid_pet_result(invalid_pet),
            _raw_paid_pet_result(recovered_pet),
        )
    )
    monkeypatch.setattr(client, "activate", lambda **_kwargs: next(responses))

    first = enforce.startup_activate(force_server=True)
    invalid = enforce.startup_activate(force_server=True)
    invalid_current = enforce.current()
    invalid_state = enforce.activation_state()
    recovered = enforce.startup_activate(force_server=True)
    recovered_state = enforce.activation_state()

    assert first.feature_usage["pet_companion"]["status"] == "available"
    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert invalid_current is invalid
    assert invalid_state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert invalid_state["run_full"] is True
    assert recovered.feature_usage["pet_companion"]["status"] == "available"
    assert recovered.feature_usage["pet_companion"]["usage_revision"] == 2
    assert recovered_state["feature_usage"]["pet_companion"]["status"] == "available"
    assert recovered_state["run_full"] is True


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("server_now", 253_402_300_800),
        ("server_now", -1),
        ("server_now", True),
        ("visibility_lease_exp", 253_402_300_800),
        ("visibility_lease_exp", -1),
        ("entitlement_exp", 253_402_300_800),
        ("entitlement_exp", -1),
    ],
)
def test_raw_pet_authority_epoch_fields_fail_closed_in_product_state(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    bad_value: int | bool,
) -> None:
    device = f"PET-RAW-EPOCH-{field}-{bad_value}"
    token = _token(f"pet-raw-epoch-{field}-{bad_value}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    invalid_pet = _raw_quota_pet()
    invalid_pet[field] = bad_value
    monkeypatch.setattr(
        client,
        "activate",
        lambda **_kwargs: _raw_paid_pet_result(invalid_pet),
    )

    invalid = enforce.startup_activate(force_server=True)
    current = enforce.current()
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert current is invalid
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert enforce.should_run_full(invalid, now=BASE_NOW) is True


def test_raw_pet_authority_epoch_subclass_is_rejected_without_magic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    class BombInt(int):
        def _called(self):
            calls["count"] += 1
            raise RuntimeError("epoch integer protocol executed")

        __bool__ = _called

        def __eq__(self, _other):
            return self._called()

        def __lt__(self, _other):
            return self._called()

        def __le__(self, _other):
            return self._called()

        def __gt__(self, _other):
            return self._called()

        def __ge__(self, _other):
            return self._called()

    device = "PET-RAW-EPOCH-SUBCLASS"
    token = _token("pet-raw-epoch-subclass", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    invalid_pet = _raw_quota_pet()
    invalid_pet["server_now"] = BombInt(BASE_NOW)
    monkeypatch.setattr(
        client,
        "activate",
        lambda **_kwargs: _raw_paid_pet_result(invalid_pet),
    )

    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert calls["count"] == 0


@pytest.mark.parametrize(
    ("status", "mutation"),
    [
        ("active", {"visible": False}),
        ("available", {"last_seq": 0}),
        ("paused", {"last_seq": -1}),
        ("exhausted", {"remaining_seconds": 1}),
        ("sequence_conflict", {"visibility_lease_exp": BASE_NOW}),
        (
            "session_conflict",
            {"visible": False, "visibility_lease_exp": BASE_NOW + 15},
        ),
    ],
)
def test_raw_pet_authority_status_specific_fields_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    mutation: dict,
) -> None:
    device = f"PET-RAW-STATUS-{status}"
    token = _token(f"pet-raw-status-{status}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    pet = _raw_quota_pet()
    pet["status"] = status
    if status == "active":
        pet.update(
            {
                "visible": True,
                "last_seq": 0,
                "visibility_lease_exp": BASE_NOW + 15,
            }
        )
    elif status == "paused":
        pet.update({"remaining_seconds": 43_199, "last_seq": 0})
    elif status == "exhausted":
        pet.update({"remaining_seconds": 0, "last_seq": 0})
    elif status == "sequence_conflict":
        pet.update(
            {
                "visible": True,
                "last_seq": 0,
                "visibility_lease_exp": BASE_NOW + 15,
            }
        )
    elif status == "session_conflict":
        pet.update({"visible": False, "last_seq": 0})
    pet.update(mutation)
    monkeypatch.setattr(
        client,
        "activate",
        lambda **_kwargs: _raw_paid_pet_result(pet),
    )

    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True


@pytest.mark.parametrize(
    ("pet", "plan_kind", "entitlement", "paid_exp"),
    [
        (
            {
                "kind": "none",
                "status": "locked",
                "server_now": 253_402_300_799,
            },
            "custom",
            "none",
            BASE_NOW + 86_400,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": 253_402_300_799,
            },
            "legacy_permanent",
            "full",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": 253_402_300_798,
                "duration_days": 90,
                "entitlement_exp": 253_402_300_799,
            },
            "90d",
            "full",
            253_402_300_799,
        ),
        (
            {
                **_raw_quota_pet(),
                "server_now": 253_402_300_798,
                "visibility_lease_exp": 253_402_300_798,
                "entitlement_exp": 253_402_300_799,
            },
            "30d",
            "quota",
            253_402_300_799,
        ),
    ],
)
def test_raw_pet_authority_accepts_safe_epoch_boundary_for_each_shape(
    monkeypatch: pytest.MonkeyPatch,
    pet: dict,
    plan_kind: str,
    entitlement: str,
    paid_exp: int | None,
) -> None:
    device = f"PET-RAW-EPOCH-BOUNDARY-{plan_kind}"
    token = _token(f"pet-raw-epoch-boundary-{plan_kind}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    expected_status = pet["status"]
    monkeypatch.setattr(
        client,
        "activate",
        lambda **_kwargs: _raw_pet_result(
            pet,
            plan_kind=plan_kind,
            entitlement=entitlement,
            paid_exp=paid_exp,
        ),
    )

    result = enforce.startup_activate(force_server=True)
    current = enforce.current()
    state = enforce.activation_state()

    assert result.feature_usage["pet_companion"]["status"] == expected_status
    assert current is result
    assert state["feature_usage"]["pet_companion"]["status"] == expected_status
    assert state["run_full"] is True


@pytest.mark.parametrize(
    ("pet", "plan_kind", "entitlement", "paid_exp"),
    [
        (
            {
                "kind": "none",
                "status": "locked",
                "server_now": 253_402_300_800,
            },
            "custom",
            "none",
            BASE_NOW + 86_400,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": 253_402_300_800,
            },
            "legacy_permanent",
            "full",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW,
                "duration_days": 90,
                "entitlement_exp": 253_402_300_800,
            },
            "90d",
            "full",
            253_402_300_800,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW + 1,
                "duration_days": 90,
                "entitlement_exp": BASE_NOW + 1,
            },
            "90d",
            "full",
            BASE_NOW + 86_400,
        ),
    ],
)
def test_raw_pet_authority_rejects_invalid_epoch_for_each_shape(
    monkeypatch: pytest.MonkeyPatch,
    pet: dict,
    plan_kind: str,
    entitlement: str,
    paid_exp: int | None,
) -> None:
    device = f"PET-RAW-EPOCH-SHAPE-{plan_kind}"
    token = _token(f"pet-raw-epoch-shape-{plan_kind}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    monkeypatch.setattr(
        client,
        "activate",
        lambda **_kwargs: _raw_pet_result(
            pet,
            plan_kind=plan_kind,
            entitlement=entitlement,
            paid_exp=paid_exp,
        ),
    )

    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True


def test_raw_pet_authority_rejects_30d_full_top_level_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-RAW-TOPLEVEL-30D-FULL"
    token = _token("pet-raw-toplevel-30d-full", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    result = _raw_pet_result(
        {
            "kind": "full",
            "status": "active",
            "server_now": BASE_NOW,
        },
        plan_kind="30d",
        entitlement="full",
        paid_exp=BASE_NOW + 86_400,
    )
    monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

    invalid = enforce.startup_activate(force_server=True)
    current = enforce.current()
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert current is invalid
    assert state["feature_entitlements"] == {"pet_companion": "none"}
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert enforce.should_run_full(invalid, now=BASE_NOW) is True


@pytest.mark.parametrize(
    (
        "pet",
        "plan_kind",
        "entitlement",
        "paid_exp",
        "mode",
        "trial_exp",
    ),
    [
        (
            _raw_quota_pet(),
            "30d",
            "quota",
            BASE_NOW + 86_400,
            "paid",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW,
                "duration_days": 120,
                "entitlement_exp": BASE_NOW + 86_400,
            },
            "custom",
            "full",
            BASE_NOW + 86_400,
            "paid",
            None,
        ),
        (
            {
                "kind": "none",
                "status": "locked",
                "server_now": BASE_NOW,
            },
            "custom",
            "none",
            BASE_NOW + 86_400,
            "paid",
            None,
        ),
        (
            {
                **_raw_quota_pet(),
                "total_seconds": 86_400,
                "duration_days": 31,
                "remaining_seconds": 86_400,
            },
            "custom",
            "quota",
            BASE_NOW + 86_400,
            "paid",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW,
                "duration_days": 90,
                "entitlement_exp": BASE_NOW + 86_400,
            },
            "90d",
            "full",
            BASE_NOW + 86_400,
            "paid",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW,
                "duration_days": 365,
                "entitlement_exp": BASE_NOW + 86_400,
            },
            "365d",
            "full",
            BASE_NOW + 86_400,
            "paid",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW,
            },
            "legacy_permanent",
            "full",
            None,
            "paid",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW,
            },
            "permanent",
            "full",
            None,
            "paid",
            None,
        ),
        (
            {
                "kind": "trial",
                "total_seconds": 600,
                "status": "available",
                "server_now": BASE_NOW,
                "entitlement_id": "a" * 64,
                "usage_revision": 1,
                "remaining_seconds": 600,
                "last_seq": -1,
                "visible": False,
                "visibility_lease_exp": BASE_NOW,
                "entitlement_exp": BASE_NOW + 86_400,
            },
            "trial",
            "trial",
            None,
            "trial",
            BASE_NOW + 86_400,
        ),
    ],
)
def test_raw_pet_authority_accepts_each_signed_plan_pair(
    monkeypatch: pytest.MonkeyPatch,
    pet: dict,
    plan_kind: str,
    entitlement: str,
    paid_exp: int | None,
    mode: str,
    trial_exp: int | None,
) -> None:
    device = f"PET-RAW-VALID-PLAN-{plan_kind}"
    token = _token(f"pet-raw-valid-plan-{plan_kind}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    result = _raw_pet_result(
        pet,
        plan_kind=plan_kind,
        entitlement=entitlement,
        paid_exp=paid_exp,
        mode=mode,
        trial_exp=trial_exp,
    )
    monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

    accepted = enforce.startup_activate(force_server=True)
    current = enforce.current()
    state = enforce.activation_state()

    assert accepted.feature_usage["pet_companion"]["status"] != "state_error"
    assert current is accepted
    assert state["feature_entitlements"] == {"pet_companion": entitlement}
    assert state["feature_usage"]["pet_companion"]["kind"] == entitlement
    assert state["run_full"] is True


@pytest.mark.parametrize(
    (
        "pet",
        "plan_kind",
        "entitlement",
        "paid_exp",
        "mode",
        "trial_exp",
    ),
    [
        (
            _raw_quota_pet(),
            "365d",
            "quota",
            BASE_NOW + 86_400,
            "paid",
            None,
        ),
        (
            _raw_quota_pet(),
            "legacy_permanent",
            "quota",
            None,
            "paid",
            None,
        ),
        (
            _raw_quota_pet(),
            "permanent",
            "quota",
            None,
            "paid",
            None,
        ),
        (
            _raw_quota_pet(),
            "365d",
            "full",
            BASE_NOW + 86_400,
            "paid",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW,
            },
            "trial",
            "full",
            None,
            "trial",
            BASE_NOW + 86_400,
        ),
        (
            _raw_quota_pet(),
            "30d",
            "quota",
            BASE_NOW + 172_800,
            "paid",
            None,
        ),
        (
            {
                "kind": "full",
                "status": "active",
                "server_now": BASE_NOW,
                "duration_days": 90,
                "entitlement_exp": BASE_NOW + 86_400,
            },
            "90d",
            "full",
            BASE_NOW + 172_800,
            "paid",
            None,
        ),
        (
            {
                "kind": "trial",
                "total_seconds": 600,
                "status": "available",
                "server_now": BASE_NOW,
                "entitlement_id": "a" * 64,
                "usage_revision": 1,
                "remaining_seconds": 600,
                "last_seq": -1,
                "visible": False,
                "visibility_lease_exp": BASE_NOW,
                "entitlement_exp": BASE_NOW + 86_400,
            },
            "trial",
            "trial",
            None,
            "trial",
            BASE_NOW + 172_800,
        ),
    ],
)
def test_raw_pet_authority_rejects_plan_shape_and_exp_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    pet: dict,
    plan_kind: str,
    entitlement: str,
    paid_exp: int | None,
    mode: str,
    trial_exp: int | None,
) -> None:
    device = f"PET-RAW-INVALID-PLAN-{plan_kind}-{entitlement}"
    token = _token(f"pet-raw-invalid-plan-{plan_kind}-{entitlement}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    result = _raw_pet_result(
        pet,
        plan_kind=plan_kind,
        entitlement=entitlement,
        paid_exp=paid_exp,
        mode=mode,
        trial_exp=trial_exp,
    )
    monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert state["feature_entitlements"] == {"pet_companion": "none"}
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True


def test_raw_pet_top_level_projection_rejects_protocols_without_magic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    class BombStr(str):
        __hash__ = str.__hash__

        def _called(self):
            calls["count"] += 1
            raise RuntimeError("top-level Pet protocol executed")

        def __eq__(self, _other):
            return self._called()

        def __ne__(self, _other):
            return self._called()

        def __bool__(self):
            return self._called()

    device = "PET-RAW-TOPLEVEL-NOMAGIC"
    token = _token("pet-raw-toplevel-nomagic", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    mutations = (
        lambda result: setattr(result, "plan_kind", BombStr("30d")),
        lambda result: setattr(
            result,
            "feature_entitlements",
            {"pet_companion": BombStr("quota")},
        ),
        lambda result: setattr(
            result,
            "feature_entitlements",
            {BombStr("pet_companion"): "quota"},
        ),
        lambda result: setattr(
            result,
            "feature_usage",
            {BombStr("pet_companion"): _raw_quota_pet()},
        ),
    )
    for mutate in mutations:
        result = _raw_paid_pet_result(_raw_quota_pet())
        mutate(result)
        calls["count"] = 0
        monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

        invalid = enforce.startup_activate(force_server=True)
        state = enforce.activation_state()

        assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
        assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
        assert state["run_full"] is True
        assert calls["count"] == 0


@pytest.mark.parametrize(
    "field_name",
    (
        "mode",
        "plan_kind",
        "feature_entitlements",
        "feature_usage",
        "paid_exp",
        "trial_exp",
    ),
)
@pytest.mark.parametrize("collision_first", (False, True))
def test_raw_pet_top_level_collision_key_never_escapes_startup(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    collision_first: bool,
) -> None:
    calls = {"count": 0}

    class BombKey(str):
        __hash__ = str.__hash__

        def __eq__(self, _other):
            calls["count"] += 1
            raise RuntimeError("top-level ActivationResult key equality executed")

        def __ne__(self, _other):
            calls["count"] += 1
            raise RuntimeError("top-level ActivationResult key inequality executed")

    device = f"PET-RAW-TOPLEVEL-COLLISION-{field_name}-{collision_first}"
    token = _token(
        f"pet-raw-toplevel-collision-{field_name}-{collision_first}",
        30,
    )
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    result = _raw_paid_pet_result(_raw_quota_pet())
    fields = object.__getattribute__(result, "__dict__")
    value = dict.pop(fields, field_name)
    exact_items = list(dict.items(fields))
    dict.clear(fields)
    ordered_items = (
        [(BombKey(field_name), value), *exact_items]
        if collision_first
        else [*exact_items, (BombKey(field_name), value)]
    )
    for key, item in ordered_items:
        dict.__setitem__(fields, key, item)
    calls["count"] = 0
    monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

    invalid = enforce.startup_activate(force_server=True)
    current = enforce.current()
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert current is invalid
    assert enforce.should_run_full() is True
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert calls["count"] == 0


@pytest.mark.parametrize("reverse", (False, True))
def test_raw_pet_top_level_exact_shape_is_order_independent(
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
) -> None:
    device = f"PET-RAW-TOPLEVEL-EXACT-ORDER-{reverse}"
    token = _token(f"pet-raw-toplevel-exact-order-{reverse}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    result = _raw_paid_pet_result(_raw_quota_pet())
    fields = object.__getattribute__(result, "__dict__")
    items = list(dict.items(fields))
    if reverse:
        items.reverse()
    dict.clear(fields)
    for key, value in items:
        dict.__setitem__(fields, key, value)
    monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

    valid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert valid.activated is True
    assert valid.feature_usage["pet_companion"]["status"] == "available"
    assert enforce.current() is valid
    assert enforce.should_run_full() is True
    assert state["feature_usage"]["pet_companion"]["status"] == "available"
    assert state["run_full"] is True


@pytest.mark.parametrize(
    "container_kind",
    ("dict_subclass", "feature_usage_mapping"),
)
def test_raw_pet_top_level_mapping_protocols_are_never_called(
    monkeypatch: pytest.MonkeyPatch,
    container_kind: str,
) -> None:
    calls = {"count": 0}

    class BombDict(dict):
        def _called(self):
            calls["count"] += 1
            raise RuntimeError("top-level mapping protocol executed")

        __bool__ = _called
        __iter__ = _called
        __len__ = _called
        items = _called
        keys = _called
        values = _called

        def __getitem__(self, _key):
            return self._called()

    class BombMapping:
        def _called(self):
            calls["count"] += 1
            raise RuntimeError("feature usage mapping protocol executed")

        __bool__ = _called
        __iter__ = _called
        __len__ = _called
        keys = _called
        items = _called

        def __getitem__(self, _key):
            return self._called()

        def get(self, *_args, **_kwargs):
            return self._called()

    device = f"PET-RAW-TOPLEVEL-MAPPING-{container_kind}"
    token = _token(f"pet-raw-toplevel-mapping-{container_kind}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    result = _raw_paid_pet_result(_raw_quota_pet())
    if container_kind == "dict_subclass":
        object.__setattr__(
            result,
            "__dict__",
            BombDict(object.__getattribute__(result, "__dict__")),
        )
    else:
        object.__setattr__(result, "feature_usage", BombMapping())
    calls["count"] = 0
    monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert enforce.current() is invalid
    assert enforce.should_run_full() is True
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert calls["count"] == 0


def test_raw_pet_top_level_non_string_key_is_rejected_without_magic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    class BombKey:
        def __hash__(self):
            return str.__hash__("feature_usage")

        def _called(self, *_args):
            calls["count"] += 1
            raise RuntimeError("non-string ActivationResult key protocol executed")

        __bool__ = _called
        __eq__ = _called
        __ne__ = _called
        __str__ = _called

    device = "PET-RAW-TOPLEVEL-NONSTRING-KEY"
    token = _token("pet-raw-toplevel-nonstring-key", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    result = _raw_paid_pet_result(_raw_quota_pet())
    fields = object.__getattribute__(result, "__dict__")
    usage = dict.pop(fields, "feature_usage")
    dict.__setitem__(fields, BombKey(), usage)
    calls["count"] = 0
    monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert enforce.current() is invalid
    assert enforce.should_run_full() is True
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert calls["count"] == 0


@pytest.mark.parametrize("mutation", ("missing", "extra", "logical_duplicate"))
def test_raw_pet_top_level_requires_complete_unique_allowed_key_set(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    calls = {"count": 0}

    class DuplicateKey(str):
        def __hash__(self):
            return str.__hash__(self) + 1

        def __eq__(self, _other):
            calls["count"] += 1
            raise RuntimeError("duplicate ActivationResult key equality executed")

        def __ne__(self, _other):
            calls["count"] += 1
            raise RuntimeError("duplicate ActivationResult key inequality executed")

    device = f"PET-RAW-TOPLEVEL-KEYSET-{mutation}"
    token = _token(f"pet-raw-toplevel-keyset-{mutation}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    result = _raw_paid_pet_result(_raw_quota_pet())
    fields = object.__getattribute__(result, "__dict__")
    if mutation == "missing":
        dict.pop(fields, "feature_usage")
    elif mutation == "extra":
        dict.__setitem__(fields, "unexpected_pet_field", "invalid")
    else:
        dict.__setitem__(
            fields,
            DuplicateKey("feature_usage"),
            {"pet_companion": _raw_quota_pet()},
        )
    calls["count"] = 0
    monkeypatch.setattr(client, "activate", lambda **_kwargs: result)

    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert enforce.current() is invalid
    assert enforce.should_run_full() is True
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert calls["count"] == 0


def test_raw_pet_authority_rejects_protocol_key_without_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    class BombKey(str):
        __hash__ = str.__hash__

        def __eq__(self, _other):
            calls["count"] += 1
            raise RuntimeError("pet authority key equality executed")

        def __ne__(self, _other):
            calls["count"] += 1
            raise RuntimeError("pet authority key inequality executed")

    device = "PET-RAW-AUTHORITY-KEY-NOMAGIC"
    token = _token("pet-raw-authority-key-nomagic", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    stable_pet = _raw_quota_pet()
    invalid_pet = dict(stable_pet)
    remaining = invalid_pet.pop("remaining_seconds")
    invalid_pet[BombKey("remaining_seconds")] = remaining
    calls["count"] = 0
    responses = iter(
        (_raw_paid_pet_result(stable_pet), _raw_paid_pet_result(invalid_pet))
    )
    monkeypatch.setattr(client, "activate", lambda **_kwargs: next(responses))

    enforce.startup_activate(force_server=True)
    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True
    assert calls["count"] == 0


@pytest.mark.parametrize(
    "mutation",
    ("missing_field", "extra_field", "bool_for_int"),
)
def test_raw_pet_authority_requires_complete_exact_builtin_fields(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    device = f"PET-RAW-AUTHORITY-{mutation}"
    token = _token(f"pet-raw-authority-{mutation}", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    stable_pet = _raw_quota_pet()
    invalid_pet = dict(stable_pet)
    if mutation == "missing_field":
        invalid_pet.pop("remaining_seconds")
    elif mutation == "extra_field":
        invalid_pet["used_seconds"] = 0
    else:
        invalid_pet["remaining_seconds"] = True
    responses = iter(
        (_raw_paid_pet_result(stable_pet), _raw_paid_pet_result(invalid_pet))
    )
    monkeypatch.setattr(client, "activate", lambda **_kwargs: next(responses))

    enforce.startup_activate(force_server=True)
    invalid = enforce.startup_activate(force_server=True)
    state = enforce.activation_state()

    assert invalid.activated is True
    assert invalid.feature_usage["pet_companion"]["status"] == "state_error"
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert state["run_full"] is True


def test_elapsed_visibility_lease_denies_only_pet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = "PET-VISIBILITY-LEASE"
    token = _token("pet-visibility-lease", 30)
    monkeypatch.setattr(client, "device_fingerprint", lambda: device)
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", token)
    code, body = _server_activate(
        _service(tmp_path),
        token=token,
        device=device,
        event={"session_id": SESSION_ID, "seq": 0, "visible": True},
    )
    monkeypatch.setattr(client, "_post_activate", lambda *_args: (code, body))

    result = client.activate(ingest_url="https://unit", now=BASE_NOW)
    state = enforce.activation_state(result, now=BASE_NOW + 16)

    assert result.activated is True
    assert enforce.should_run_full(result, now=BASE_NOW + 16) is True
    assert state["feature_entitlements"] == {"pet_companion": "none"}
    assert state["feature_usage"]["pet_companion"]["status"] == "state_error"


def test_pet_nested_protocol_objects_are_never_called() -> None:
    calls = {"count": 0}

    class BombDict(dict):
        def get(self, *_args, **_kwargs):
            calls["count"] += 1
            raise RuntimeError("boom")

        def items(self):
            calls["count"] += 1
            raise RuntimeError("boom")

        def __iter__(self):
            calls["count"] += 1
            raise RuntimeError("boom")

        def __len__(self):
            calls["count"] += 1
            raise RuntimeError("boom")

        def __bool__(self):
            calls["count"] += 1
            raise RuntimeError("boom")

    lease = {
        "uid": "pet-nomagic",
        "device": "PET-NOMAGIC",
        "exp": BASE_NOW + 3600,
        "paid_exp": BASE_NOW + 86_400,
        "plan_kind": "30d",
        "feature_entitlements": {"pet_companion": "quota"},
        "feature_usage": BombDict(),
    }
    fields = client._validated_activation_fields(
        lease=lease,
        master_key_b64=MASTER_KEY_B64,
        mode="paid",
        device="PET-NOMAGIC",
        now=BASE_NOW,
        signed_envelope_valid=True,
    )

    assert fields is not None
    assert fields["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert calls["count"] == 0
