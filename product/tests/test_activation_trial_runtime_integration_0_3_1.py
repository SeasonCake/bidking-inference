from __future__ import annotations

import json
import math
import selectors
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402
import sample_collector_server as collector  # noqa: E402


SECRET = b"activation-trial-runtime-integration"
MASTER_KEY = "S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s="
BURST_SIZE = 800


def _service(
    root: Path,
    *,
    trial_hours: float = 3.0,
    cutover: int | None = None,
    grandfather_hours: float | None = None,
) -> server.ActivationService:
    application = root / "application"
    authority = root / "authority"
    application.mkdir(parents=True, exist_ok=True)
    authority.mkdir(parents=True, exist_ok=True)
    database = authority / "pet_usage.sqlite3"
    if not database.exists():
        server.initialize_pet_usage_database(
            database,
            secret=SECRET,
            legacy_paths=(
                application / "pet_usage.json",
                application / "pet_usage.anchor.jsonl",
            ),
        )
    return server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY,
        bindings_path=application / "bindings.json",
        revoked_path=application / "revoked.txt",
        telemetry_path=application / "telemetry.jsonl",
        trials_path=application / "trials.json",
        paid_starts_path=application / "paid_starts.json",
        pet_usage_db_path=database,
        pet_usage_path=application / "pet_usage.json",
        trial_hours=trial_hours,
        trial_grandfather_cutover_at=cutover,
        trial_grandfather_hours=grandfather_hours,
    )


def _paid_token(uid: str, *, now: int) -> str:
    issued_at = now - 60
    return core.mint_token(
        uid,
        SECRET,
        iat=issued_at,
        exp=issued_at + 30 * 86_400,
    )


def _fixed_trial_record(
    *,
    start: int,
    window_seconds: int,
    now: int,
) -> dict:
    return {
        "schema_version": core.TRIAL_RECORD_SCHEMA_VERSION,
        "start": start,
        "window_seconds": window_seconds,
        "last_server_now": now,
    }


def _request_bytes(body: dict) -> bytes:
    encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    return (
        b"POST /activate HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Connection: close\r\n"
        b"Content-Length: "
        + str(len(encoded)).encode("ascii")
        + b"\r\n\r\n"
        + encoded
    )


def _read_socket_group(
    indexed_sockets: list[tuple[int, socket.socket]],
    *,
    started: float,
    deadline: float,
) -> tuple[list[tuple[int, float, bytes]], int, int]:
    selector = selectors.DefaultSelector()
    completed: list[tuple[int, float, bytes]] = []
    resets = 0
    for index, connection in indexed_sockets:
        connection.setblocking(False)
        selector.register(connection, selectors.EVENT_READ, (index, bytearray()))
    while selector.get_map() and time.monotonic() < deadline:
        timeout = max(0.0, min(0.05, deadline - time.monotonic()))
        for key, _mask in selector.select(timeout=timeout):
            connection = key.fileobj
            index, body = key.data
            try:
                chunk = connection.recv(65_536)
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                resets += 1
                chunk = b""
            if chunk:
                body.extend(chunk)
                continue
            completed.append(
                (index, time.monotonic() - started, bytes(body))
            )
            selector.unregister(connection)
            connection.close()
    pending = [key.fileobj for key in selector.get_map().values()]
    for connection in pending:
        selector.unregister(connection)
        connection.close()
    selector.close()
    return completed, len(pending), resets


def _run_simultaneous_http_burst(
    service: server.ActivationService,
    requests: list[bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[int, float, bytes]], dict]:
    assert len(requests) == BURST_SIZE
    monkeypatch.setattr(collector, "_ACT", service)
    monkeypatch.setattr(collector, "_RATE_MAX", 1_000_000)
    monkeypatch.setattr(
        collector.Handler,
        "log_message",
        lambda self, format, *args: None,
    )
    with collector._ACT_METRICS_LOCK:
        collector._ACT_METRICS.update(
            window_started=time.monotonic(),
            requests=0,
            decision_ok=0,
            decision_failed=0,
            response_write_ok=0,
            response_write_broken_pipe=0,
            response_write_failed=0,
            elapsed_total_ms=0.0,
            elapsed_max_ms=0.0,
        )

    class BurstServer(collector.SafeThreadingHTTPServer):
        request_queue_size = 1_024

    httpd = BurstServer(("127.0.0.1", 0), collector.Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    connections: list[socket.socket] = []
    try:
        for _index in range(BURST_SIZE):
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            connection.settimeout(4.0)
            connection.connect(httpd.server_address)
            connections.append(connection)

        started = time.monotonic()
        deadline = started + 4.0
        for connection, request in zip(connections, requests, strict=True):
            connection.sendall(request)
        indexed = list(enumerate(connections))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    _read_socket_group,
                    indexed[:400],
                    started=started,
                    deadline=deadline,
                ),
                pool.submit(
                    _read_socket_group,
                    indexed[400:],
                    started=started,
                    deadline=deadline,
                ),
            ]
        results = [future.result() for future in futures]
        completed = [item for result in results for item in result[0]]
        timed_out = sum(result[1] for result in results)
        resets = sum(result[2] for result in results)
        with collector._ACT_METRICS_LOCK:
            metrics = dict(collector._ACT_METRICS)

        assert len(completed) == BURST_SIZE
        assert timed_out == 0
        assert resets == 0
        assert all(
            b"HTTP/1.0 200" in response or b"HTTP/1.1 200" in response
            for _index, _elapsed, response in completed
        )
        latencies = sorted(elapsed for _index, elapsed, _response in completed)
        assert latencies[math.ceil(BURST_SIZE * 0.95) - 1] < 4.0
        assert max(latencies) < 4.0
        assert metrics["requests"] == BURST_SIZE
        assert metrics["decision_ok"] == BURST_SIZE
        assert metrics["decision_failed"] == 0
        assert metrics["response_write_ok"] == BURST_SIZE
        assert metrics["response_write_broken_pipe"] == 0
        assert metrics["response_write_failed"] == 0
        return completed, metrics
    finally:
        for connection in connections:
            try:
                connection.close()
            except OSError:
                pass
        httpd.shutdown()
        httpd.server_close()
        server_thread.join(timeout=5)


def _http_json(response: bytes) -> dict:
    _headers, body = response.split(b"\r\n\r\n", 1)
    return json.loads(body)


def _signed_lease(payload: dict) -> dict:
    return json.loads(payload["lease"].rsplit(".", 1)[0])


def _install_write_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    real_write = server._atomic_write_json
    writes: list[str] = []
    guard = threading.Lock()

    def tracked_write(path: Path, value) -> None:
        with guard:
            writes.append(path.name)
        real_write(path, value)

    monkeypatch.setattr(server, "_atomic_write_json", tracked_write)
    return writes


def test_800_distinct_existing_paid_heartbeats_are_bounded_and_write_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = int(time.time())
    service = _service(tmp_path)
    bindings: dict[str, str] = {}
    starts: dict[str, int] = {}
    requests: list[bytes] = []
    for index in range(BURST_SIZE):
        uid = f"distinct-paid-{index:04d}"
        device = f"DISTINCT-PAID-{index:04d}"
        bindings[uid] = device
        starts[uid] = now - 60
        requests.append(
            _request_bytes(
                {
                    "token": _paid_token(uid, now=now),
                    "device": device,
                }
            )
        )
    server._atomic_write_json(service.bindings_path, bindings)
    server._atomic_write_json(service.paid_starts_path, starts)
    writes = _install_write_spy(monkeypatch)

    _completed, metrics = _run_simultaneous_http_burst(
        service,
        requests,
        monkeypatch,
    )

    assert writes == []
    assert metrics["elapsed_max_ms"] < 4_000


def test_800_mixed_paid_old_12h_and_new_3h_preserve_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = int(time.time())
    cutover = now - 3_600
    monkeypatch.setattr(server.time, "time", lambda: now)
    service = _service(
        tmp_path,
        trial_hours=3,
        cutover=cutover,
        grandfather_hours=12,
    )
    bindings: dict[str, str] = {}
    starts: dict[str, int] = {}
    trials: dict[str, dict] = {}
    request_bodies: list[dict] = []
    expected_trial_exp: dict[int, int] = {}

    for index in range(600):
        uid = f"mixed-paid-{index:04d}"
        device = f"MIXED-PAID-{index:04d}"
        bindings[uid] = device
        starts[uid] = now - 60
        request_bodies.append(
            {"token": _paid_token(uid, now=now), "device": device}
        )
    for offset in range(100):
        device = f"MIXED-OLD-{offset:03d}"
        start = cutover - 600 - offset
        trials[device] = _fixed_trial_record(
            start=start,
            window_seconds=12 * 3_600,
            now=now,
        )
        index = len(request_bodies)
        request_bodies.append({"device": device})
        expected_trial_exp[index] = start + 12 * 3_600
    for offset in range(100):
        device = f"MIXED-NEW-{offset:03d}"
        start = cutover + 1 + offset
        trials[device] = _fixed_trial_record(
            start=start,
            window_seconds=3 * 3_600,
            now=now,
        )
        index = len(request_bodies)
        request_bodies.append({"device": device})
        expected_trial_exp[index] = start + 3 * 3_600

    server._atomic_write_json(service.bindings_path, bindings)
    server._atomic_write_json(service.paid_starts_path, starts)
    server._atomic_write_json(service.trials_path, trials)
    writes = _install_write_spy(monkeypatch)
    completed, metrics = _run_simultaneous_http_burst(
        service,
        [_request_bytes(body) for body in request_bodies],
        monkeypatch,
    )

    assert writes == []
    assert metrics["elapsed_max_ms"] < 4_000
    responses = {
        index: _signed_lease(_http_json(response))
        for index, _elapsed, response in completed
        if index in expected_trial_exp
    }
    assert set(responses) == set(expected_trial_exp)
    assert {
        index: lease["exp"] for index, lease in responses.items()
    } == expected_trial_exp
    persisted = json.loads(service.trials_path.read_text(encoding="utf-8"))
    assert persisted == trials


def test_pet_busy_keeps_cutover_trial_grant_and_bounds_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = int(time.time())
    cutover = now - 10
    monkeypatch.setattr(server.time, "time", lambda: now)
    service = _service(
        tmp_path,
        trial_hours=3,
        cutover=cutover,
        grandfather_hours=12,
    )
    real_transaction = server._pet_usage_sqlite_transaction
    entered = threading.Event()
    release = threading.Event()
    choose_first = threading.Lock()
    should_delay = True

    @contextmanager
    def delayed_first_transaction(path: Path, *, secret: bytes, now: int):
        nonlocal should_delay
        with choose_first:
            delay = should_delay
            should_delay = False
        if delay:
            entered.set()
            assert release.wait(timeout=5)
        with real_transaction(path, secret=secret, now=now) as transaction:
            yield transaction

    monkeypatch.setattr(
        server,
        "_pet_usage_sqlite_transaction",
        delayed_first_transaction,
    )
    paid = _paid_token("pet-busy-paid", now=now)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(
            service.handle_activate,
            {
                "token": paid,
                "device": "PET-BUSY-PAID",
                "pet_usage": {
                    "session_id": "pet-busy-paid-session",
                    "seq": 0,
                    "visible": True,
                },
            },
        )
        assert entered.wait(timeout=3)
        readiness = pool.submit(server.activation_readiness, service)
        trial = pool.submit(
            service.handle_activate,
            {"device": "PET-BUSY-NEW-TRIAL"},
        )
        completed, _pending = wait((readiness, trial), timeout=1.5)
        completed_before_release = len(completed) == 2
        release.set()
        first_result = first.result(timeout=5)
        readiness_result = readiness.result(timeout=5)
        trial_result = trial.result(timeout=5)

    assert completed_before_release
    assert first_result[0] == 200
    assert readiness_result == (
        503,
        {"ok": False, "service": "activation", "reason": "state_error"},
    )
    assert trial_result[0] == 200
    trial_lease = _signed_lease(trial_result[1])
    assert trial_lease["exp"] == now + 3 * 3_600
    assert trial_lease["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert server.activation_readiness(service) == (
        200,
        {"ok": True, "service": "activation"},
    )


def test_trial_write_failure_does_not_change_paid_zero_write_or_api_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = int(time.time())
    cutover = now - 10
    monkeypatch.setattr(server.time, "time", lambda: now)
    service = _service(
        tmp_path,
        trial_hours=3,
        cutover=cutover,
        grandfather_hours=12,
    )
    paid = _paid_token("write-failure-paid", now=now)
    assert service.handle_activate(
        {"token": paid, "device": "WRITE-FAILURE-PAID"}
    )[0] == 200
    real_write = server._atomic_write_json
    writes: list[str] = []

    def fail_trial_only(path: Path, value) -> None:
        writes.append(path.name)
        if path == service.trials_path:
            raise OSError("synthetic trial authority failure")
        real_write(path, value)

    monkeypatch.setattr(server, "_atomic_write_json", fail_trial_only)
    trial_code, trial_payload = service.handle_activate(
        {"device": "WRITE-FAILURE-TRIAL"}
    )
    paid_code, _paid_payload = service.handle_activate(
        {"token": paid, "device": "WRITE-FAILURE-PAID"}
    )

    assert (trial_code, trial_payload) == (
        503,
        {"ok": False, "mode": "trial", "reason": "state_error"},
    )
    assert paid_code == 200
    assert writes == ["trials.json"]
    assert server._REQUIRED_ACTIVATION_CORE_API_VERSION == 5
    monkeypatch.setattr(core, "ACTIVATION_CORE_API_VERSION", 4)
    assert server.activation_readiness(service) == (
        503,
        {
            "ok": False,
            "service": "activation",
            "reason": "activation_core_api_mismatch",
        },
    )
