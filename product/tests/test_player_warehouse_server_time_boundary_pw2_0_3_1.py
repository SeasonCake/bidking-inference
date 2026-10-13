from __future__ import annotations

import http.client
import json
import math
from decimal import Decimal
import threading

import pytest

from test_player_warehouse_server_validation_pw2_0_3_1 import (
    _prepare_collector,
    _pw2_record,
)


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _ExplodingNumber:
    def __float__(self) -> float:
        raise AssertionError("unsafe numeric protocol must not be called")

    def __round__(self, _digits: int = 0) -> float:
        raise AssertionError("unsafe round protocol must not be called")

    def __lt__(self, _other: object) -> bool:
        raise AssertionError("unsafe comparison protocol must not be called")


def test_current_pw2_rollout_boundary_is_noon_beijing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, _normal = _prepare_collector(tmp_path, monkeypatch)

    assert collector._PW2_SERVER_CURRENT_EPOCH == 1_787_803_200.0  # noqa: SLF001


@pytest.mark.parametrize(
    ("case", "server_now", "expected"),
    (
        (
            "nextafter-below",
            lambda boundary: math.nextafter(boundary, -math.inf),
            "quarantined",
        ),
        ("below-0004", lambda boundary: boundary - 0.0004, "quarantined"),
        ("below-0006", lambda boundary: boundary - 0.0006, "quarantined"),
        ("exact", lambda boundary: boundary, "accepted"),
        ("exact-int", lambda boundary: int(boundary), "accepted"),
        (
            "nextafter-above",
            lambda boundary: math.nextafter(boundary, math.inf),
            "accepted",
        ),
        ("nan", lambda _boundary: math.nan, "quarantined"),
        ("positive-infinity", lambda _boundary: math.inf, "quarantined"),
        ("negative-infinity", lambda _boundary: -math.inf, "quarantined"),
    ),
)
def test_accept_one_qualifies_pw2_with_unrounded_single_server_time(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    server_now,
    expected: str,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    boundary = collector._PW2_SERVER_CURRENT_EPOCH  # noqa: SLF001
    raw_now = server_now(boundary)
    calls = 0

    def fake_time() -> float:
        nonlocal calls
        calls += 1
        return raw_now

    monkeypatch.setattr(collector.time, "time", fake_time)
    record = _pw2_record(session_hash=f"pw2-server-time-{case}")
    record["received_ts"] = 9_999_999_999.0

    assert collector._accept_one(record) == expected  # noqa: SLF001
    assert calls == 1

    stored_path = (
        normal
        if expected == "accepted"
        else normal.with_name("samples_quarantine.jsonl")
    )
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    if math.isfinite(raw_now):
        assert stored["received_ts"] == round(raw_now, 3)
    else:
        assert stored["received_ts"] is None
    assert stored["received_ts"] != record["received_ts"]


@pytest.mark.parametrize(
    ("case", "server_now"),
    (
        ("huge-positive-int", lambda boundary: 10**400),
        ("huge-negative-int", lambda boundary: -(10**400)),
        ("bool", lambda boundary: True),
        ("decimal", lambda boundary: Decimal(str(boundary))),
        ("int-subclass", lambda boundary: _IntSubclass(int(boundary))),
        ("float-subclass", lambda boundary: _FloatSubclass(boundary)),
        ("exploding-protocol", lambda boundary: _ExplodingNumber()),
    ),
)
def test_accept_one_quarantines_unrepresentable_server_time_without_protocol_calls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    server_now,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    boundary = collector._PW2_SERVER_CURRENT_EPOCH  # noqa: SLF001
    raw_now = server_now(boundary)
    calls = 0

    def fake_time():
        nonlocal calls
        calls += 1
        return raw_now

    monkeypatch.setattr(collector.time, "time", fake_time)
    record = _pw2_record(session_hash=f"pw2-server-time-unsafe-{case}")
    record["received_ts"] = 9_999_999_999.0

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert calls == 1

    stored = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert stored["received_ts"] is None
    assert stored["server_quarantine_reason"] == "warehouse_source_pending"
    assert stored["received_ts"] != record["received_ts"]


def test_accept_one_fails_closed_when_server_clock_moves_behind_pw2_boundary(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    boundary = collector._PW2_SERVER_CURRENT_EPOCH  # noqa: SLF001
    server_times = iter((boundary + 1.0, boundary - 1.0))
    calls = 0

    def fake_time() -> float:
        nonlocal calls
        calls += 1
        return next(server_times)

    monkeypatch.setattr(collector.time, "time", fake_time)

    first = _pw2_record(session_hash="pw2-clock-forward")
    second = _pw2_record(session_hash="pw2-clock-backward")
    assert collector._accept_one(first) == "accepted"  # noqa: SLF001
    assert collector._accept_one(second) == "quarantined"  # noqa: SLF001
    assert calls == 2

    accepted = json.loads(normal.read_text(encoding="utf-8"))
    quarantined = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert accepted["received_ts"] == round(boundary + 1.0, 3)
    assert quarantined["received_ts"] == round(boundary - 1.0, 3)
    assert quarantined["server_quarantine_reason"] == "warehouse_source_pending"


@pytest.mark.parametrize(
    "clock_error",
    (
        RuntimeError("clock runtime failure"),
        OSError("clock os failure"),
        OverflowError("clock overflow"),
        Exception("generic clock failure"),
    ),
)
def test_accept_one_quarantines_when_server_clock_read_raises(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    clock_error: Exception,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    calls = 0

    def failing_time() -> float:
        nonlocal calls
        calls += 1
        raise clock_error

    monkeypatch.setattr(collector.time, "time", failing_time)
    record = _pw2_record(
        session_hash=f"pw2-server-clock-read-{type(clock_error).__name__}"
    )
    record["received_ts"] = 9_999_999_999.0

    assert collector._accept_one(record) == "quarantined"  # noqa: SLF001
    assert calls == 1
    assert not normal.exists()

    stored = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert stored["received_ts"] is None
    assert stored["server_quarantine_reason"] == "warehouse_source_pending"
    assert stored["received_ts"] != record["received_ts"]


def test_http_ingest_keeps_batch_after_one_server_clock_read_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    boundary = collector._PW2_SERVER_CURRENT_EPOCH  # noqa: SLF001
    calls = 0

    def first_fails_then_succeeds() -> float:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first sample clock failure")
        return boundary

    monkeypatch.setattr(collector.time, "time", first_fails_then_succeeds)
    monkeypatch.setattr(collector, "_KEY", "pw2-http-test-key")
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: True)

    records = [
        _pw2_record(session_hash="pw2-http-clock-failure"),
        _pw2_record(session_hash="pw2-http-clock-success"),
    ]

    class QuietHandler(collector.Handler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def date_time_string(self, _timestamp: float | None = None) -> str:
            return "Thu, 01 Jan 1970 00:00:00 GMT"

    server = collector.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_address[1],
            timeout=5,
        )
        body = json.dumps(records).encode("utf-8")
        connection.request(
            "POST",
            "/ingest",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Api-Key": "pw2-http-test-key",
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert response.status == 200
    assert payload == {
        "accepted": 1,
        "duplicate": 0,
        "quarantined": 1,
        "rejected": 0,
    }
    assert calls == 2

    accepted = json.loads(normal.read_text(encoding="utf-8"))
    quarantined = json.loads(
        normal.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8")
    )
    assert accepted["session_hash"] == "pw2-http-clock-success"
    assert accepted["received_ts"] == round(boundary, 3)
    assert quarantined["session_hash"] == "pw2-http-clock-failure"
    assert quarantined["received_ts"] is None


def test_http_ingest_fails_closed_when_signed_identity_clock_is_unavailable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    clock_calls = 0

    def unavailable_time() -> float:
        nonlocal clock_calls
        clock_calls += 1
        raise RuntimeError("server clock unavailable")

    class UploadAuthority:
        calls = 0

        def authorize_upload_lease(self, _envelope: str, *, now: int):
            self.calls += 1
            raise AssertionError(f"unavailable clock must not yield now={now}")

    authority = UploadAuthority()
    monkeypatch.setattr(collector.time, "time", unavailable_time)
    monkeypatch.setattr(collector, "_KEY", "pw2-http-test-key")
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(collector, "_auth_ok", lambda _headers, _raw: True)
    monkeypatch.setattr(collector, "_UPLOAD_IDENTITY_MODE", "compat")
    monkeypatch.setattr(collector, "_ACT", authority)

    class QuietHandler(collector.Handler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def date_time_string(self, _timestamp: float | None = None) -> str:
            return "Thu, 01 Jan 1970 00:00:00 GMT"

    server = collector.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_address[1],
            timeout=5,
        )
        body = b"[]"
        connection.request(
            "POST",
            "/ingest",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Api-Key": "pw2-http-test-key",
                "X-Upload-Lease": "synthetic-lease-envelope",
                "X-Upload-Nonce": "a" * 32,
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert response.status == 503
    assert payload == {"error": "identity_state_error"}
    assert clock_calls == 1
    assert authority.calls == 0
    assert not normal.exists()
    assert not normal.with_name("samples_quarantine.jsonl").exists()


@pytest.mark.parametrize(
    "clock_error",
    (
        RuntimeError("reservation clock runtime failure"),
        OverflowError("reservation clock overflow"),
    ),
)
def test_http_signed_ingest_fails_closed_when_reservation_clock_read_raises(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    clock_error: Exception,
) -> None:
    collector, normal = _prepare_collector(tmp_path, monkeypatch)
    clock_calls = 0

    def authority_then_reservation_clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 1:
            return 2_000_000_000.0
        raise clock_error

    class UploadAuthority:
        calls = 0

        def authorize_upload_lease(self, _envelope: str, *, now: int):
            self.calls += 1
            assert now == 2_000_000_000
            return True, "ok", {
                "subject_kind": "paid",
                "subject_id": "a" * 64,
                "uid_hash": "b" * 16,
                "device_alias": "c" * 64,
                "security_epoch": 7,
                "lease_id": "d" * 32,
                "exp": 2_000_000_300,
            }

    class IdentityStore:
        reserve_calls = 0
        finalize_calls = 0

        def reserve_request(self, **_kwargs):
            self.reserve_calls += 1
            raise AssertionError("unavailable reservation clock must block store access")

        def finalize_request(self, **_kwargs):
            self.finalize_calls += 1
            raise AssertionError("failed reservation must not finalize")

    authority = UploadAuthority()
    identity_store = IdentityStore()
    monkeypatch.setattr(collector.time, "time", authority_then_reservation_clock)
    monkeypatch.setattr(collector, "_KEY", "pw2-http-test-key")
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(collector, "_auth_ok", lambda _headers, _raw: True)
    monkeypatch.setattr(collector, "_UPLOAD_IDENTITY_MODE", "required")
    monkeypatch.setattr(collector, "_ACT", authority)
    monkeypatch.setattr(collector, "_UPLOAD_IDENTITY_STORE", identity_store)

    class QuietHandler(collector.Handler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def date_time_string(self, _timestamp: float | None = None) -> str:
            return "Thu, 01 Jan 1970 00:00:00 GMT"

    server = collector.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_address[1],
            timeout=5,
        )
        body = b"[]"
        connection.request(
            "POST",
            "/ingest",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Api-Key": "pw2-http-test-key",
                "X-Upload-Lease": "synthetic-lease-envelope",
                "X-Upload-Nonce": "e" * 32,
            },
        )
        response = connection.getresponse()
        response_body = response.read()
        payload = json.loads(response_body.decode("utf-8"))
        upload_status = response.getheader("X-BidKing-Upload-Status")
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert response.status == 503
    assert payload == {"error": "identity_state_error"}
    assert upload_status == "identity_state_error"
    assert type(clock_error).__name__.encode("ascii") not in response_body
    assert str(clock_error).encode("ascii") not in response_body
    assert clock_calls == 2
    assert authority.calls == 1
    assert identity_store.reserve_calls == 0
    assert identity_store.finalize_calls == 0
    assert not normal.exists()
    assert not normal.with_name("samples_quarantine.jsonl").exists()
