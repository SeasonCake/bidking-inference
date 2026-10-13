from __future__ import annotations

import json
import math
import selectors
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402
import sample_collector_server as collector  # noqa: E402


SECRET = b"activation-paid-heartbeat-write-elision"
MASTER_KEY = "S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s="


def _service(tmp_path: Path) -> server.ActivationService:
    application = tmp_path / "application"
    authority = tmp_path / "authority"
    application.mkdir()
    authority.mkdir()
    database = authority / "pet_usage.sqlite3"
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
    )


def _token(
    uid: str,
    *,
    roaming: bool = False,
    permanent: bool = False,
) -> str:
    issued_at = int(time.time()) - 1
    return core.mint_token(
        uid,
        SECRET,
        iat=issued_at,
        exp=None if permanent else issued_at + 30 * 86_400,
        roaming=roaming,
    )


def _install_write_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    real_write = server._atomic_write_json
    writes: list[str] = []

    def tracked_write(path: Path, value) -> None:
        writes.append(path.name)
        real_write(path, value)

    monkeypatch.setattr(server, "_atomic_write_json", tracked_write)
    return writes


def test_paid_authority_writes_only_when_semantics_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    writes = _install_write_spy(monkeypatch)
    token = _token("paid-heartbeat-stable")

    first_code, _first = service.handle_activate(
        {"token": token, "device": "PAID-STABLE-DEVICE"}
    )
    assert first_code == 200
    assert writes == ["paid_starts.json", "bindings.json"]

    writes.clear()
    heartbeat_code, _heartbeat = service.handle_activate(
        {"token": token, "device": "PAID-STABLE-DEVICE"}
    )
    assert heartbeat_code == 200
    assert writes == []

    roaming = _token("paid-roaming-change", roaming=True)
    first_roaming_code, _first_roaming = service.handle_activate(
        {"token": roaming, "device": "PAID-ROAMING-A"}
    )
    assert first_roaming_code == 200
    writes.clear()

    changed_code, _changed = service.handle_activate(
        {"token": roaming, "device": "PAID-ROAMING-B"}
    )
    assert changed_code == 200
    assert writes == ["bindings.json"]

    permanent = _token("paid-permanent-first", permanent=True)
    writes.clear()
    permanent_code, _permanent = service.handle_activate(
        {"token": permanent, "device": "PAID-PERMANENT-DEVICE"}
    )
    assert permanent_code == 200
    assert writes == ["bindings.json"]


def test_existing_binding_without_paid_start_persists_only_missing_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    token = _token("paid-start-migration")
    server._atomic_write_json(
        service.bindings_path,
        {"paid-start-migration": "PAID-MIGRATION-DEVICE"},
    )
    server._atomic_write_json(service.paid_starts_path, {})
    writes = _install_write_spy(monkeypatch)

    code, _payload = service.handle_activate(
        {"token": token, "device": "PAID-MIGRATION-DEVICE"}
    )

    assert code == 200
    assert writes == ["paid_starts.json"]


@pytest.mark.parametrize("failed_name", ["paid_starts.json", "bindings.json"])
def test_changed_paid_authority_write_failure_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_name: str,
) -> None:
    service = _service(tmp_path)
    token = _token(f"paid-write-failure-{failed_name}")
    real_write = server._atomic_write_json

    def failed_write(path: Path, value) -> None:
        if path.name == failed_name:
            raise OSError("synthetic durable write failure")
        real_write(path, value)

    monkeypatch.setattr(server, "_atomic_write_json", failed_write)
    code, payload = service.handle_activate(
        {"token": token, "device": "PAID-WRITE-FAILURE-DEVICE"}
    )

    assert code == 503
    assert payload == {"ok": False, "mode": "paid", "reason": "state_error"}


def test_unchanged_heartbeat_never_calls_failing_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    token = _token("paid-heartbeat-no-writer")
    first_code, _first = service.handle_activate(
        {"token": token, "device": "PAID-NO-WRITER-DEVICE"}
    )
    assert first_code == 200

    def unexpected_write(_path: Path, _value) -> None:
        raise AssertionError("unchanged paid authority must not be rewritten")

    monkeypatch.setattr(server, "_atomic_write_json", unexpected_write)
    code, _payload = service.handle_activate(
        {"token": token, "device": "PAID-NO-WRITER-DEVICE"}
    )
    assert code == 200


def test_concurrent_same_and_wrong_device_preserve_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    token = _token("paid-concurrent-device")
    first_code, _first = service.handle_activate(
        {"token": token, "device": "PAID-CONCURRENT-CORRECT"}
    )
    assert first_code == 200
    writes = _install_write_spy(monkeypatch)

    with ThreadPoolExecutor(max_workers=16) as pool:
        same = [
            pool.submit(
                service.handle_activate,
                {"token": token, "device": "PAID-CONCURRENT-CORRECT"},
            )
            for _ in range(15)
        ]
        wrong = pool.submit(
            service.handle_activate,
            {"token": token, "device": "PAID-CONCURRENT-WRONG"},
        )

    assert [future.result()[0] for future in same] == [200] * 15
    assert wrong.result()[0] == 403
    assert writes == []
    assert json.loads(service.bindings_path.read_text(encoding="utf-8")) == {
        "paid-concurrent-device": "PAID-CONCURRENT-CORRECT"
    }


def _read_socket_group(
    indexed_sockets: list[tuple[int, socket.socket]],
    *,
    started: float,
    deadline: float,
) -> tuple[list[tuple[float, bytes]], int, int]:
    selector = selectors.DefaultSelector()
    completed: list[tuple[float, bytes]] = []
    connection_resets = 0
    for index, connection in indexed_sockets:
        connection.setblocking(False)
        selector.register(
            connection,
            selectors.EVENT_READ,
            (index, bytearray()),
        )
    while selector.get_map() and time.monotonic() < deadline:
        timeout = max(0.0, min(0.05, deadline - time.monotonic()))
        for key, _mask in selector.select(timeout=timeout):
            connection = key.fileobj
            _index, body = key.data
            try:
                chunk = connection.recv(65_536)
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                connection_resets += 1
                chunk = b""
            if chunk:
                body.extend(chunk)
                continue
            completed.append((time.monotonic() - started, bytes(body)))
            selector.unregister(connection)
            connection.close()
    pending = [key.fileobj for key in selector.get_map().values()]
    for connection in pending:
        selector.unregister(connection)
        connection.close()
    selector.close()
    return completed, len(pending), connection_resets


def test_800_simultaneous_existing_paid_heartbeats_are_bounded_and_write_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    token = _token("paid-800-simultaneous")
    device = "PAID-800-SIMULTANEOUS-DEVICE"
    first_code, _first = service.handle_activate(
        {"token": token, "device": device}
    )
    assert first_code == 200
    writes = _install_write_spy(monkeypatch)
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
    encoded = json.dumps(
        {"token": token, "device": device},
        separators=(",", ":"),
    ).encode("utf-8")
    request = (
        b"POST /activate HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Connection: close\r\n"
        b"Content-Length: "
        + str(len(encoded)).encode("ascii")
        + b"\r\n\r\n"
        + encoded
    )
    connections: list[socket.socket] = []

    try:
        for _index in range(800):
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            connection.settimeout(4.0)
            connection.connect(httpd.server_address)
            connections.append(connection)

        started = time.monotonic()
        deadline = started + 4.0
        for connection in connections:
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
        completed = [
            response
            for result in results
            for response in result[0]
        ]
        timed_out = sum(result[1] for result in results)
        connection_resets = sum(result[2] for result in results)
        latencies = sorted(elapsed for elapsed, _response in completed)
        http_200 = sum(
            b"HTTP/1.0 200" in response or b"HTTP/1.1 200" in response
            for _elapsed, response in completed
        )
        with collector._ACT_METRICS_LOCK:
            metrics = dict(collector._ACT_METRICS)

        assert len(connections) == 800
        assert len(completed) == 800
        assert http_200 == 800
        assert timed_out == 0
        assert connection_resets == 0
        assert writes == []
        assert latencies[math.ceil(len(latencies) * 0.95) - 1] < 4.0
        assert max(latencies) < 4.0
        assert metrics["requests"] == 800
        assert metrics["decision_ok"] == 800
        assert metrics["decision_failed"] == 0
        assert metrics["response_write_ok"] == 800
        assert metrics["response_write_broken_pipe"] == 0
        assert metrics["response_write_failed"] == 0
    finally:
        for connection in connections:
            try:
                connection.close()
            except OSError:
                pass
        httpd.shutdown()
        httpd.server_close()
        server_thread.join(timeout=5)
