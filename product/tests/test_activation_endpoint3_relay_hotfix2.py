from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_endpoint3_relay as relay  # noqa: E402
import activation_endpoint3_relay_state_admin as relay_state  # noqa: E402


KEY = b"relay-contract-key-material-32-bytes-minimum"
OTHER_DIGESTS = frozenset(
    hashlib.sha256(value).hexdigest()
    for value in (b"activation-key", b"master-key", b"device-block-key")
)


def _initialize_replay(path: Path, *, security_epoch: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert relay_state.initialize_replay_database(
        path,
        security_epoch=security_epoch,
        auth_window_seconds=relay.AUTH_WINDOW_SECONDS,
    ) in {"initialized", "already_ready"}


class _UpstreamState:
    def __init__(self) -> None:
        self.status = 200
        self.body = b'{"ok":true}'
        self.content_type = "application/json; charset=utf-8"
        self.extra_headers: tuple[tuple[str, str], ...] = ()
        self.calls: list[tuple[str, str, bytes, dict[str, str]]] = []


def _upstream_handler(state: _UpstreamState):
    class UpstreamHandler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length else b""
            state.calls.append(
                (self.command, self.path, body, dict(self.headers.items()))
            )
            payload = b"" if self.command == "HEAD" else state.body
            self.send_response(state.status)
            self.send_header("Content-Type", state.content_type)
            self.send_header("Content-Length", str(len(payload)))
            for name, value in state.extra_headers:
                self.send_header(name, value)
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        do_GET = _handle
        do_HEAD = _handle
        do_POST = _handle

        def log_message(self, *_args) -> None:
            pass

    return UpstreamHandler


def _serve(handler) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _app_server(
    tmp_path: Path,
    *,
    mode: str,
    upstream_base: str,
    key: bytes = KEY,
    logger=None,
    clock=None,
):
    replay_path = tmp_path / f"{mode}-{time.time_ns()}.sqlite3"
    if mode == "origin":
        _initialize_replay(replay_path)
    app = relay.RelayApplication(
        mode=mode,
        enabled=True,
        key=key,
        forbidden_key_sha256=OTHER_DIGESTS,
        upstream_base=upstream_base,
        replay_db_path=replay_path,
        allow_http_loopback=True,
        timeout_seconds=2.0,
        logger=logger,
        clock=clock,
    )
    return _serve(relay.make_handler(app))


def _request(
    server: ThreadingHTTPServer,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
    request_headers = dict(headers or {})
    if body or method == "POST":
        request_headers.setdefault("Content-Length", str(len(body)))
    conn.request(method, path, body=body, headers=request_headers)
    response = conn.getresponse()
    payload = response.read()
    result_headers = {name.lower(): value for name, value in response.getheaders()}
    conn.close()
    return response.status, payload, result_headers


def _raw_request(server: ThreadingHTTPServer, request: bytes) -> bytes:
    chunks: list[bytes] = []
    with socket.create_connection(("127.0.0.1", server.server_port), timeout=4) as sock:
        sock.settimeout(4)
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


@pytest.fixture
def relay_chain(tmp_path: Path):
    state = _UpstreamState()
    collector, collector_thread = _serve(_upstream_handler(state))
    origin, origin_thread = _app_server(
        tmp_path,
        mode="origin",
        upstream_base=f"http://127.0.0.1:{collector.server_port}",
    )
    edge, edge_thread = _app_server(
        tmp_path,
        mode="edge",
        upstream_base=f"http://127.0.0.1:{origin.server_port}",
    )
    try:
        yield state, edge, origin
    finally:
        for server, thread in (
            (edge, edge_thread),
            (origin, origin_thread),
            (collector, collector_thread),
        ):
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


@pytest.mark.parametrize("status", [200, 401, 403, 426, 429, 503])
def test_real_loopback_preserves_authoritative_status_and_json_bytes(
    relay_chain,
    status: int,
) -> None:
    state, edge, _origin = relay_chain
    state.status = status
    state.body = json.dumps(
        {"ok": status == 200, "reason": "fixed_reason"},
        separators=(",", ":"),
    ).encode("ascii")

    code, body, headers = _request(
        edge,
        "POST",
        "/activate",
        body=b'{"client_version":"0.3.1-hotfix2"}',
        headers={"Content-Type": "application/json"},
    )

    assert (code, body) == (status, state.body)
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert len(state.calls) == 1
    assert state.calls[0][0:3] == (
        "POST",
        "/activate",
        b'{"client_version":"0.3.1-hotfix2"}',
    )
    collector_headers = {name.lower() for name in state.calls[0][3]}
    assert not any(name.startswith("x-bidking-relay-") for name in collector_headers)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/version"),
        ("HEAD", "/version"),
        ("GET", "/healthz"),
        ("HEAD", "/healthz"),
        ("GET", "/activation-readiness"),
        ("HEAD", "/activation-readiness"),
    ],
)
def test_real_loopback_allows_only_read_routes(relay_chain, method: str, path: str) -> None:
    state, edge, _origin = relay_chain
    code, body, _headers = _request(edge, method, path)
    assert code == 200
    assert body == (b"" if method == "HEAD" else state.body)
    assert len(state.calls) == 1
    assert state.calls[0][0:2] == (("GET" if method == "HEAD" else method), path)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/activate", 405),
        ("POST", "/healthz", 405),
        ("PUT", "/activate", 405),
        ("OPTIONS", "/version", 405),
        ("GET", "/unknown", 404),
        ("GET", "/version?x=1", 400),
        ("GET", "http://private.invalid/version", 400),
    ],
)
def test_inbound_method_path_and_query_fail_closed_without_upstream(
    relay_chain,
    method: str,
    path: str,
    expected: int,
) -> None:
    state, edge, _origin = relay_chain
    code, _body, _headers = _request(edge, method, path)
    assert code == expected
    assert state.calls == []


@pytest.mark.parametrize(
    ("method", "path", "expected_route"),
    [
        ("CONNECT", "private-method-canary.invalid:443", "unknown"),
        ("FOO", "/private-method-canary", "unknown"),
        ("PUT", "/activate", "activate"),
    ],
)
def test_real_server_rejects_every_parsed_unsupported_method_once_without_upstream(
    tmp_path: Path,
    method: str,
    path: str,
    expected_route: str,
) -> None:
    lines: list[str] = []
    state = _UpstreamState()
    upstream, upstream_thread = _serve(_upstream_handler(state))
    edge, edge_thread = _app_server(
        tmp_path,
        mode="edge",
        upstream_base=f"http://127.0.0.1:{upstream.server_port}",
        logger=lines.append,
    )
    try:
        code, body, headers = _request(edge, method, path)
    finally:
        edge.shutdown()
        edge.server_close()
        edge_thread.join(timeout=3)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=3)

    assert code == 405
    assert body == b'{"ok":false,"reason":"relay_method_not_allowed"}'
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["connection"] == "close"
    assert state.calls == []
    assert len(lines) == 1
    assert lines[0].startswith(
        f"route_id={expected_route} status_family=4xx "
        "upstream_class=request_rejected "
    )
    assert "private-method-canary" not in lines[0]
    assert method not in lines[0]


def test_real_server_legal_get_and_post_each_reach_upstream_and_log_once(
    tmp_path: Path,
) -> None:
    lines: list[str] = []
    state = _UpstreamState()
    upstream, upstream_thread = _serve(_upstream_handler(state))
    edge, edge_thread = _app_server(
        tmp_path,
        mode="edge",
        upstream_base=f"http://127.0.0.1:{upstream.server_port}",
        logger=lines.append,
    )
    try:
        get_code, get_body, get_headers = _request(edge, "GET", "/healthz")
        post_code, post_body, post_headers = _request(
            edge,
            "POST",
            "/activate",
            body=b'{"client_version":"0.3.1-hotfix2"}',
            headers={"Content-Type": "application/json"},
        )
    finally:
        edge.shutdown()
        edge.server_close()
        edge_thread.join(timeout=3)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=3)

    assert (get_code, get_body) == (200, state.body)
    assert (post_code, post_body) == (200, state.body)
    assert "connection" not in get_headers
    assert "connection" not in post_headers
    assert [call[0:2] for call in state.calls] == [
        ("GET", relay.ORIGIN_PREFIX + "/healthz"),
        ("POST", relay.ORIGIN_PREFIX + "/activate"),
    ]
    assert len(lines) == 2
    assert lines[0].startswith("route_id=healthz status_family=2xx ")
    assert lines[1].startswith("route_id=activate status_family=2xx ")


def test_rejected_envelope_closes_before_unread_body_can_become_next_request(
    tmp_path: Path,
) -> None:
    lines: list[str] = []
    state = _UpstreamState()
    upstream, upstream_thread = _serve(_upstream_handler(state))
    edge, edge_thread = _app_server(
        tmp_path,
        mode="edge",
        upstream_base=f"http://127.0.0.1:{upstream.server_port}",
        logger=lines.append,
    )
    smuggled = (
        b"GET /healthz HTTP/1.1\r\n"
        b"Host: private-body-canary.invalid\r\n"
        b"Connection: close\r\n\r\n"
    )
    request = (
        b"FOO /activate HTTP/1.1\r\n"
        b"Host: relay.invalid\r\n"
        + f"Content-Length: {len(smuggled)}\r\n\r\n".encode("ascii")
        + smuggled
    )
    try:
        raw_response = _raw_request(edge, request)
    finally:
        edge.shutdown()
        edge.server_close()
        edge_thread.join(timeout=3)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=3)

    assert raw_response.count(b"HTTP/1.1 ") == 1
    assert raw_response.startswith(b"HTTP/1.1 405 ")
    assert b"Connection: close\r\n" in raw_response
    assert b'{"ok":false,"reason":"relay_method_not_allowed"}' in raw_response
    assert state.calls == []
    assert len(lines) == 1
    assert lines[0].startswith(
        "route_id=activate status_family=4xx upstream_class=request_rejected "
    )
    assert "private-body-canary" not in lines[0]


def test_post_rejects_non_json_malformed_and_oversized_without_upstream(relay_chain) -> None:
    state, edge, _origin = relay_chain
    for body, content_type, expected in (
        (b"{}", "text/plain", 415),
        (b"not-json", "application/json", 400),
        (b'{"value":NaN}', "application/json", 400),
        (b"x" * (relay.MAX_BODY_BYTES + 1), "application/json", 413),
    ):
        code, _payload, _headers = _request(
            edge,
            "POST",
            "/activate",
            body=body,
            headers={"Content-Type": content_type},
        )
        assert code == expected
    assert state.calls == []


def test_redirect_and_malformed_upstream_are_fixed_privacy_safe_503(relay_chain) -> None:
    state, edge, _origin = relay_chain
    state.status = 302
    state.extra_headers = (("Location", "https://private.invalid/path"),)
    code, body, headers = _request(edge, "GET", "/healthz")
    assert code == 503
    assert json.loads(body) == {"ok": False, "reason": "relay_upstream_unavailable"}
    assert "location" not in headers
    assert len(state.calls) == 1

    state.calls.clear()
    state.status = 200
    state.body = b"not-json"
    code, body, _headers = _request(edge, "GET", "/healthz")
    assert code == 503
    assert json.loads(body) == {"ok": False, "reason": "relay_upstream_unavailable"}
    assert len(state.calls) == 1


def test_response_headers_are_allowlisted(relay_chain) -> None:
    state, edge, _origin = relay_chain
    state.extra_headers = (
        ("Set-Cookie", "private=1"),
        ("Location", "https://private.invalid"),
        ("X-Secret", "private-canary"),
        ("Server", "private-origin"),
    )
    code, _body, headers = _request(edge, "GET", "/version")
    assert code == 200
    assert "set-cookie" not in headers
    assert "location" not in headers
    assert "x-secret" not in headers
    assert "private-origin" not in headers.get("server", "")


def test_origin_auth_hmac_expiry_replay_and_tampering(tmp_path: Path) -> None:
    state = _UpstreamState()
    collector, collector_thread = _serve(_upstream_handler(state))
    now = 1_700_000_000
    origin, origin_thread = _app_server(
        tmp_path,
        mode="origin",
        upstream_base=f"http://127.0.0.1:{collector.server_port}",
        clock=lambda: now,
    )
    body = b'{"token":"private-canary"}'
    try:
        def headers_for(*, timestamp=now, nonce="a" * 32, method="POST", path="/activate", raw=body):
            signed_path = relay.ORIGIN_PREFIX + path
            return relay.signed_headers(
                KEY,
                timestamp=timestamp,
                nonce=nonce,
                method=method,
                path=signed_path,
                body=raw,
            ) | {"Content-Type": "application/json"}

        headers = headers_for()
        code, _payload, _ = _request(
            origin,
            "POST",
            relay.ORIGIN_PREFIX + "/activate",
            body=body,
            headers=headers,
        )
        assert code == 200
        assert len(state.calls) == 1

        code, _payload, _ = _request(
            origin,
            "POST",
            relay.ORIGIN_PREFIX + "/activate",
            body=body,
            headers=headers,
        )
        assert code == 403
        assert len(state.calls) == 1

        for label, request_method, request_path, request_body, request_headers in (
            (
                "expired",
                "POST",
                "/activate",
                body,
                headers_for(timestamp=now - relay.AUTH_WINDOW_SECONDS - 1, nonce="b" * 32),
            ),
            (
                "future",
                "POST",
                "/activate",
                body,
                headers_for(timestamp=now + relay.AUTH_WINDOW_SECONDS + 1, nonce="c" * 32),
            ),
            (
                "body",
                "POST",
                "/activate",
                b'{"token":"tampered"}',
                headers_for(nonce="d" * 32),
            ),
            (
                "path",
                "POST",
                "/activate",
                body,
                headers_for(nonce="e" * 32, path="/healthz"),
            ),
            (
                "method",
                "POST",
                "/activate",
                body,
                headers_for(nonce="f" * 32, method="GET"),
            ),
        ):
            del label
            code, _payload, _ = _request(
                origin,
                request_method,
                relay.ORIGIN_PREFIX + request_path,
                body=request_body,
                headers=request_headers,
            )
            assert code in (401, 403)
        assert len(state.calls) == 1
    finally:
        origin.shutdown()
        origin.server_close()
        origin_thread.join(timeout=3)
        collector.shutdown()
        collector.server_close()
        collector_thread.join(timeout=3)


def test_config_key_missing_empty_shared_or_unverified_fails_closed(tmp_path: Path) -> None:
    common = {
        "mode": "edge",
        "enabled": True,
        "upstream_base": "http://127.0.0.1:1",
        "replay_db_path": tmp_path / "replay.sqlite3",
        "allow_http_loopback": True,
    }
    for key, digests in (
        (None, OTHER_DIGESTS),
        (b"", OTHER_DIGESTS),
        (KEY, frozenset()),
        (KEY, OTHER_DIGESTS | {hashlib.sha256(KEY).hexdigest()}),
    ):
        with pytest.raises((TypeError, ValueError)):
            relay.RelayApplication(
                key=key,
                forbidden_key_sha256=digests,
                **common,
            )


class _ExplosiveStr(str):
    calls = 0

    def _boom(self, *_args, **_kwargs):
        type(self).calls += 1
        raise AssertionError("magic method used")

    __str__ = _boom
    __bool__ = _boom
    __eq__ = _boom
    __hash__ = _boom
    encode = _boom
    lower = _boom
    strip = _boom


def test_public_security_helpers_reject_str_subclasses_without_magic(tmp_path: Path) -> None:
    bad = _ExplosiveStr("POST")
    _ExplosiveStr.calls = 0
    with pytest.raises((TypeError, ValueError)):
        relay.sign_relay_request(
            KEY,
            timestamp=int(time.time()),
            nonce="a" * 32,
            method=bad,
            path=relay.ORIGIN_PREFIX + "/activate",
            body=b"{}",
        )
    with pytest.raises((TypeError, ValueError)):
        relay.RelayApplication(
            mode=bad,
            enabled=True,
            key=KEY,
            forbidden_key_sha256=OTHER_DIGESTS,
            upstream_base="http://127.0.0.1:1",
            replay_db_path=tmp_path / "replay.sqlite3",
            allow_http_loopback=True,
        )
    assert _ExplosiveStr.calls == 0


def test_log_projection_rejects_str_subclasses_without_magic() -> None:
    lines: list[str] = []
    bad = _ExplosiveStr("activate")
    _ExplosiveStr.calls = 0
    relay.emit_fixed_log(
        lines.append,
        route_id=bad,
        status_family=bad,
        upstream_class=bad,
        write_result=bad,
        latency_bucket=bad,
    )
    assert _ExplosiveStr.calls == 0
    assert lines == [
        "route_id=unknown status_family=other upstream_class=protocol_failure "
        "write_result=failed latency_bucket=ge_2000ms "
        f"relay_version={relay.RELAY_VERSION}"
    ]


def test_key_separation_compares_every_protected_digest(monkeypatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real_compare = relay.hmac.compare_digest

    def tracked(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(relay.hmac, "compare_digest", tracked)
    relay._validate_key_separation(KEY, OTHER_DIGESTS)
    assert len(calls) == len(OTHER_DIGESTS)


def test_fixed_log_schema_never_echoes_private_request_fields(tmp_path: Path) -> None:
    lines: list[str] = []
    state = _UpstreamState()
    state.body = b'{"ok":true,"token":"upstream-private-canary"}'
    collector, collector_thread = _serve(_upstream_handler(state))
    origin, origin_thread = _app_server(
        tmp_path,
        mode="origin",
        upstream_base=f"http://127.0.0.1:{collector.server_port}",
        logger=lines.append,
    )
    edge, edge_thread = _app_server(
        tmp_path,
        mode="edge",
        upstream_base=f"http://127.0.0.1:{origin.server_port}",
        logger=lines.append,
    )
    request_canary = "request-token-device-url-ip-header-canary"
    try:
        code, _body, _headers = _request(
            edge,
            "POST",
            "/activate",
            body=json.dumps({"token": request_canary}).encode("ascii"),
            headers={
                "Content-Type": "application/json",
                "X-Private-Canary": request_canary,
            },
        )
        assert code == 200
    finally:
        for server, thread in (
            (edge, edge_thread),
            (origin, origin_thread),
            (collector, collector_thread),
        ):
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    joined = "\n".join(lines)
    assert request_canary not in joined
    assert "upstream-private-canary" not in joined
    for line in lines:
        assert set(part.split("=", 1)[0] for part in line.split()) == {
            "route_id",
            "status_family",
            "upstream_class",
            "write_result",
            "latency_bucket",
            "relay_version",
        }


class _BrokenPipeStream:
    def write(self, _body: bytes) -> None:
        raise BrokenPipeError("private-canary")


class _FailedStream:
    def write(self, _body: bytes) -> None:
        raise OSError("private-canary")


def test_write_result_has_fixed_ok_broken_pipe_and_failed_classes() -> None:
    class Good:
        def write(self, body: bytes) -> None:
            assert body == b"{}"

    assert relay.write_body(Good(), b"{}") == "ok"
    assert relay.write_body(_BrokenPipeStream(), b"{}") == "broken_pipe"
    assert relay.write_body(_FailedStream(), b"{}") == "failed"


def test_replay_guard_survives_new_instance_and_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "nonce.sqlite3"
    _initialize_replay(path)
    now = int(time.time())
    nonce = "a" * 32
    first = relay.SQLiteReplayGuard(path)
    assert first.claim(nonce, expires_at=now + 30, now=now) is True
    second = relay.SQLiteReplayGuard(path)
    assert second.claim(nonce, expires_at=now + 30, now=now) is False

    results: list[bool] = []
    lock = threading.Lock()

    def claim_once() -> None:
        result = relay.SQLiteReplayGuard(path).claim(
            "b" * 32,
            expires_at=now + 30,
            now=now,
        )
        with lock:
            results.append(result)

    threads = [threading.Thread(target=claim_once) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert results.count(True) == 1
    assert results.count(False) == 15


def test_network_failure_is_fixed_503_without_exception_text(tmp_path: Path) -> None:
    lines: list[str] = []
    edge, thread = _app_server(
        tmp_path,
        mode="edge",
        upstream_base="http://127.0.0.1:1",
        logger=lines.append,
    )
    try:
        code, body, _headers = _request(edge, "GET", "/healthz")
    finally:
        edge.shutdown()
        edge.server_close()
        thread.join(timeout=3)
    assert code == 503
    assert json.loads(body) == {"ok": False, "reason": "relay_upstream_unavailable"}
    assert len(lines) == 1
    assert "upstream_class=network_failure" in lines[0]
    assert "refused" not in lines[0].lower()


def test_disabled_application_returns_fixed_503_without_state_or_upstream(tmp_path: Path) -> None:
    state = _UpstreamState()
    collector, collector_thread = _serve(_upstream_handler(state))
    replay_path = tmp_path / "disabled.sqlite3"
    app = relay.RelayApplication(
        mode="edge",
        enabled=False,
        key=KEY,
        forbidden_key_sha256=OTHER_DIGESTS,
        upstream_base=f"http://127.0.0.1:{collector.server_port}",
        replay_db_path=replay_path,
        allow_http_loopback=True,
    )
    edge, edge_thread = _serve(relay.make_handler(app))
    try:
        code, body, _headers = _request(edge, "GET", "/healthz")
    finally:
        edge.shutdown()
        edge.server_close()
        edge_thread.join(timeout=3)
        collector.shutdown()
        collector.server_close()
        collector_thread.join(timeout=3)
    assert code == 503
    assert json.loads(body) == {"ok": False, "reason": "relay_disabled"}
    assert state.calls == []
    assert not replay_path.exists()


def test_preflight_is_fixed_privacy_safe_and_default_disabled(tmp_path: Path) -> None:
    key_file = tmp_path / "relay.key"
    key_file.write_bytes(KEY)
    digest_args = [
        item
        for digest in sorted(OTHER_DIGESTS)
        for item in ("--forbidden-key-sha256", digest)
    ]
    command = [
        sys.executable,
        str(SCRIPTS / "activation_endpoint3_relay.py"),
        "--mode",
        "edge",
        "--preflight",
        "--key-file",
        str(key_file),
        "--upstream-base",
        "http://127.0.0.1:9",
        "--replay-db",
        str(tmp_path / "replay.sqlite3"),
        "--relay-security-epoch",
        "1",
        "--auth-window-seconds",
        str(relay.AUTH_WINDOW_SECONDS),
        "--allow-http-loopback",
        *digest_args,
    ]
    disabled = subprocess.run(command, capture_output=True, text=True, check=False)
    assert disabled.returncode == 0
    disabled_payload = json.loads(disabled.stdout)
    assert disabled_payload["enabled"] is False
    assert not (tmp_path / "replay.sqlite3").exists()
    assert "127.0.0.1" not in disabled.stdout
    assert str(tmp_path) not in disabled.stdout
    assert KEY.decode("ascii") not in disabled.stdout

    enabled_env = dict(os.environ)
    enabled_env[relay.ENABLE_ENV] = "1"
    enabled = subprocess.run(
        [*command, "--enable"],
        env=enabled_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert enabled.returncode == 0
    enabled_payload = json.loads(enabled.stdout)
    assert enabled_payload["enabled"] is True
    assert enabled_payload["eligible"] is True

    rollback_env = dict(enabled_env)
    rollback_env[relay.ENABLE_ENV] = "0"
    rolled_back = subprocess.run(
        [*command, "--enable"],
        env=rollback_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rolled_back.returncode == 0
    assert json.loads(rolled_back.stdout)["enabled"] is False


def test_enabled_origin_preflight_reports_missing_store_without_creating_it(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "relay.key"
    key_file.write_bytes(KEY)
    replay_dir = tmp_path / "authoritative-relay-state"
    replay_path = replay_dir / "replay.sqlite3"
    digest_args = [
        item
        for digest in sorted(OTHER_DIGESTS)
        for item in ("--forbidden-key-sha256", digest)
    ]
    env = dict(os.environ)
    env[relay.ENABLE_ENV] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "activation_endpoint3_relay.py"),
            "--mode",
            "origin",
            "--enable",
            "--preflight",
            "--key-file",
            str(key_file),
            "--upstream-base",
            "http://127.0.0.1:18900",
            "--replay-db",
            str(replay_path),
            "--relay-security-epoch",
            "1",
            "--auth-window-seconds",
            str(relay.AUTH_WINDOW_SECONDS),
            "--allow-http-loopback",
            *digest_args,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["eligible"] is False
    assert payload["replay_store_state"] == "missing"
    assert not replay_dir.exists()
