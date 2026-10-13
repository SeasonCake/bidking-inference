from __future__ import annotations

import base64
import datetime as dt
import io
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bidking_lab.secure import client_activate as ca
from scripts import activation_transport_diagnostic as diag


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self._status

    def read(self):
        return self._raw


def _valid_trial(device: str, now: int) -> dict:
    exp = now + 600
    lease = json.dumps(
        {"uid": f"trial:{device}", "device": device, "exp": exp},
        separators=(",", ":"),
    ) + ".signature"
    return {
        "ok": True,
        "mode": "trial",
        "lease": lease,
        "trial_exp": exp,
        "master_key": base64.b64encode(b"k" * 32).decode("ascii"),
    }


def _valid_paid(device: str, now: int) -> dict:
    exp = now + 600
    lease = json.dumps(
        {"uid": "paid-user", "device": device, "exp": exp},
        separators=(",", ":"),
    ) + ".signature"
    return {
        "ok": True,
        "mode": "paid",
        "lease": lease,
        "master_key": base64.b64encode(b"p" * 32).decode("ascii"),
    }


@pytest.fixture(autouse=True)
def _isolated_activation(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(ca._ENV_DISABLE_ACTIVATION_DIRECT_RETRY, raising=False)
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "package")
    monkeypatch.setattr(ca, "device_fingerprint", lambda: "DEVICE-DIRECT-RETRY")
    monkeypatch.setattr(ca, "_load_activation_http_e2ee_config", lambda: None)


def test_same_endpoint_direct_retry_precedes_existing_fallback_order(monkeypatch):
    now = 2_000_000
    calls: list[tuple[type, str, bytes]] = []
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )

    def open_path(request, *, timeout):
        assert timeout == 4.0
        calls.append((type(request), request.full_url, request.data))
        if len(calls) == 1:
            raise urllib.error.URLError(ssl.SSLError("PRIVATE_PROXY_TLS"))
        if len(calls) == 2:
            raise urllib.error.URLError(ConnectionRefusedError("PRIVATE_DIRECT"))
        if len(calls) == 3:
            raise urllib.error.URLError(socket.timeout("PRIVATE_PROXY_TIMEOUT"))
        return _Response(_valid_trial("DEVICE-DIRECT-RETRY", now))

    monkeypatch.setattr(ca, "urlopen_verified", open_path)

    result = ca.activate(ui_token="", now=now, force_server=True)

    assert result.activated is True
    assert [(kind, url) for kind, url, _body in calls] == [
        (ca._ActivationProxiedHTTPSRequest, "https://primary.invalid/activate"),
        (ca._ActivationDirectHTTPSRequest, "https://primary.invalid/activate"),
        (ca._ActivationProxiedHTTPSRequest, "https://fallback.invalid/activate"),
        (ca._ActivationDirectHTTPSRequest, "https://fallback.invalid/activate"),
    ]
    assert len({body for _kind, _url, body in calls}) == 1
    assert b"DEVICE-DIRECT-RETRY" in calls[0][2]


@pytest.mark.parametrize(
    ("token", "response_factory", "expected_mode"),
    [
        ("", _valid_trial, "trial"),
        ("existing-credential", _valid_paid, "paid"),
    ],
    ids=["cold-start", "existing-credential-online-recheck"],
)
def test_direct_primary_success_covers_cold_start_and_existing_credential(
    monkeypatch,
    token,
    response_factory,
    expected_mode,
):
    now = 2_000_000
    calls = 0

    def recover(request, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        if type(request) is ca._ActivationProxiedHTTPSRequest:
            raise urllib.error.URLError(ssl.SSLError("PRIVATE_PROXY_TLS"))
        return _Response(response_factory("DEVICE-DIRECT-RETRY", now))

    monkeypatch.setattr(ca, "urlopen_verified", recover)
    result = ca.activate(ui_token=token, now=now, force_server=True)

    assert calls == 2
    assert result.activated is True
    assert result.mode == expected_mode
    assert result.reason == "ok"


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500, 503])
def test_any_http_status_stops_direct_and_cross_endpoint_fallback(
    monkeypatch,
    status,
):
    calls = 0
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )

    def response(request, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        payload = {"ok": False, "mode": "paid", "reason": "state_error"}
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            "PRIVATE_HTTP_DETAIL",
            {},
            io.BytesIO(json.dumps(payload).encode("utf-8")),
        )

    monkeypatch.setattr(ca, "urlopen_verified", response)
    result = ca.activate(ui_token="credential", force_server=True)

    assert result.activated is False
    assert calls == 1
    assert result.transport_summary.attempt_count == 1
    assert dict(result.transport_summary.stage_counts)["http"] == 1


def test_http_error_subclass_is_zero_magic_fixed_http_stop(monkeypatch):
    magic_calls = {"code": 0, "read": 0}

    class ExplodingCode:
        def __get__(self, _instance, _owner):
            magic_calls["code"] += 1
            raise RuntimeError("PRIVATE_HTTP_CODE_MAGIC")

        def __set__(self, instance, value):
            object.__setattr__(instance, "_stored_code", value)

    class CountingBody(io.BytesIO):
        def read(self, *args, **kwargs):
            magic_calls["read"] += 1
            return super().read(*args, **kwargs)

    class HostileHTTPError(urllib.error.HTTPError):
        code = ExplodingCode()

    error = HostileHTTPError(
        "https://private.invalid/activate",
        418,
        "PRIVATE_HTTP_DETAIL",
        {},
        CountingBody(b'{"ok": false, "reason": "PRIVATE_BODY"}'),
    )
    calls = 0

    def fail(_request, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise error

    monkeypatch.setattr(ca, "urlopen_verified", fail)
    outcome = ca._post_activate(
        "https://primary.invalid/activate",
        "device",
        "credential",
        2.0,
    )

    assert calls == 1
    assert magic_calls == {"code": 0, "read": 0}
    assert type(outcome) is ca._ActivationPostFailure
    assert outcome.failure_class == "http_reachable_invalid_payload"
    assert outcome.retry_fallback is False
    assert outcome.stage == "http"
    assert "PRIVATE" not in repr(outcome)


def test_http_200_payload_invalid_stops_direct_and_fallback(monkeypatch):
    calls = 0
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )

    def invalid(_request, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        return _Response({"ok": "true"})

    monkeypatch.setattr(ca, "urlopen_verified", invalid)
    result = ca.activate(ui_token="credential", force_server=True)

    assert calls == 1
    assert result.transport_failure_classes == (
        "http_reachable_invalid_payload",
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [(403, "bad_token"), (503, "state_error")],
)
def test_direct_http_response_stops_existing_fallback(
    monkeypatch,
    status,
    reason,
):
    calls: list[tuple[type, str]] = []
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )

    def respond(request, *, timeout):
        del timeout
        calls.append((type(request), request.full_url))
        if type(request) is ca._ActivationProxiedHTTPSRequest:
            raise urllib.error.URLError(ssl.SSLError("PRIVATE_PROXY_TLS"))
        payload = {"ok": False, "mode": "paid", "reason": reason}
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            "PRIVATE_HTTP_DETAIL",
            {},
            io.BytesIO(json.dumps(payload).encode("utf-8")),
        )

    monkeypatch.setattr(ca, "urlopen_verified", respond)
    result = ca.activate(ui_token="credential", force_server=True)

    assert result.reason == reason
    assert calls == [
        (ca._ActivationProxiedHTTPSRequest, "https://primary.invalid/activate"),
        (ca._ActivationDirectHTTPSRequest, "https://primary.invalid/activate"),
    ]


def test_response_body_failure_after_headers_never_duplicates_request(monkeypatch):
    calls = 0

    class BodyFails(_Response):
        def read(self):
            raise ConnectionResetError("PRIVATE_AFTER_HTTP")

    def response(_request, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        return BodyFails({})

    monkeypatch.setattr(ca, "urlopen_verified", response)
    outcome = ca._post_activate(
        "https://primary.invalid/activate",
        "device",
        "credential",
        1.5,
    )

    assert calls == 1
    assert type(outcome) is ca._ActivationPostFailure
    assert outcome.failure_class == "http_reachable_invalid_payload"
    assert outcome.retry_fallback is False
    assert "PRIVATE" not in repr(outcome)


def test_unknown_or_protocol_failure_cannot_trigger_direct_retry(monkeypatch):
    calls = 0

    class Hostile(Exception):
        @property
        def reason(self):
            raise RuntimeError("PRIVATE_REASON")

    def fail(_request, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise Hostile("PRIVATE_EXCEPTION")

    monkeypatch.setattr(ca, "urlopen_verified", fail)
    outcome = ca._post_activate(
        "https://primary.invalid/activate",
        "device",
        "credential",
        2.0,
    )

    assert calls == 1
    assert type(outcome) is ca._ActivationPostFailure
    assert outcome.failure_class == "transport_unknown"
    assert "PRIVATE" not in repr(outcome)


def test_identity_change_between_proxy_failure_and_retry_stops_second_send(
    monkeypatch,
):
    calls = 0
    revision = ca._ACTIVATION_IDENTITY_SOURCE_REVISION
    clear_epoch = ca._ACTIVATION_CLEAR_EPOCH
    monkeypatch.setattr(ca, "_ACTIVATION_IDENTITY_SOURCE_REVISION", revision)

    def fail_and_rebind(_request, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        ca._ACTIVATION_IDENTITY_SOURCE_REVISION = revision + 2
        raise urllib.error.URLError(ssl.SSLError("PRIVATE_PROXY_TLS"))

    monkeypatch.setattr(ca, "urlopen_verified", fail_and_rebind)
    with ca._activation_direct_retry_fence(revision, clear_epoch):
        outcome = ca._post_activate(
            "https://primary.invalid/activate",
            "device",
            "credential",
            2.0,
        )

    assert calls == 1
    assert type(outcome) is ca._ActivationPostFailure
    assert outcome.failure_class == "transport_unknown"
    assert outcome.retry_fallback is False
    assert outcome.prior_failure_classes == ("tls_handshake_failed",)
    assert "PRIVATE" not in repr(outcome)


def test_rollback_switch_disables_second_send(monkeypatch):
    calls = 0
    monkeypatch.setenv(ca._ENV_DISABLE_ACTIVATION_DIRECT_RETRY, "1")

    def fail(_request, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raise urllib.error.URLError(ssl.SSLError("PRIVATE_PROXY_TLS"))

    monkeypatch.setattr(ca, "urlopen_verified", fail)
    outcome = ca._post_activate(
        "https://primary.invalid/activate",
        "device",
        "credential",
        2.0,
    )

    assert calls == 1
    assert type(outcome) is ca._ActivationPostFailure
    assert outcome.failure_class == "tls_handshake_failed"


def test_direct_policy_uses_empty_proxy_map_and_preserves_timeout(monkeypatch):
    proxy_args: list[tuple] = []
    open_calls: list[tuple[type, float]] = []

    class Handler:
        pass

    class Opener:
        def open(self, request, timeout):
            open_calls.append((type(request), timeout))
            return object()

    def proxy_handler(*args):
        proxy_args.append(args)
        return Handler()

    monkeypatch.setattr(ca.urllib.request, "ProxyHandler", proxy_handler)
    monkeypatch.setattr(ca.urllib.request, "HTTPSHandler", lambda **_kw: Handler())
    monkeypatch.setattr(ca.urllib.request, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(ca, "verified_ssl_context", lambda: object())

    proxied = ca._ActivationProxiedHTTPSRequest(
        "https://primary.invalid/activate",
        data=b"{}",
        method="POST",
    )
    direct = ca._ActivationDirectHTTPSRequest(
        "https://primary.invalid/activate",
        data=b"{}",
        method="POST",
    )
    ca._activation_urlopen_verified(proxied, timeout=1.25)
    ca._activation_urlopen_verified(direct, timeout=2.5)

    assert proxy_args == [(), ({},)]
    assert open_calls == [
        (ca._ActivationProxiedHTTPSRequest, 1.25),
        (ca._ActivationDirectHTTPSRequest, 2.5),
    ]


def test_request_subclass_and_switch_protocol_values_are_zero_magic(monkeypatch):
    class RequestSubclass(ca._ActivationDirectHTTPSRequest):
        def header_items(self):
            raise AssertionError("request magic must not run")

    delegated = []
    monkeypatch.setattr(
        ca,
        "_shared_urlopen_verified",
        lambda target, *, timeout: delegated.append((target, timeout)),
    )
    request = RequestSubclass("https://primary.invalid/activate")
    ca._activation_urlopen_verified(request, timeout=3.0)
    assert delegated == [(request, 3.0)]

    class StrSubclass(str):
        def __eq__(self, _other):
            raise AssertionError("switch magic must not run")

        def __hash__(self):
            raise AssertionError("switch magic must not run")

    monkeypatch.setattr(
        ca.os,
        "environ",
        {ca._ENV_DISABLE_ACTIVATION_DIRECT_RETRY: StrSubclass("1")},
    )
    assert ca._activation_direct_retry_enabled() is False


def test_fresh_process_default_and_rollback_switch():
    script = (
        "import sys;"
        f"sys.path.insert(0, {str(ROOT / 'src')!r});"
        "from bidking_lab.secure import client_activate as c;"
        "print('enabled' if c._activation_direct_retry_enabled() else 'disabled')"
    )
    clean_env = os.environ.copy()
    clean_env.pop(ca._ENV_DISABLE_ACTIVATION_DIRECT_RETRY, None)
    default = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    rollback_env = dict(clean_env)
    rollback_env[ca._ENV_DISABLE_ACTIVATION_DIRECT_RETRY] = "1"
    rollback = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=rollback_env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )

    assert default.stdout.strip() == "enabled"
    assert rollback.stdout.strip() == "disabled"


def _tls_contexts(tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "activation-test-cert.pem"
    key_path = tmp_path / "activation-test-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(str(cert_path), str(key_path))
    client = ssl.create_default_context(cafile=str(cert_path))
    assert client.verify_mode == ssl.CERT_REQUIRED
    assert client.check_hostname is True
    return server, client


def test_real_https_redirect_is_an_http_stop_not_a_followed_request(
    monkeypatch,
    tmp_path,
):
    paths: list[str] = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/redirect-target")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            paths.append(self.path)
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server_context, client_context = _tls_contexts(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.setenv("no_proxy", "127.0.0.1")
        monkeypatch.setattr(ca, "verified_ssl_context", lambda: client_context)
        outcome = ca._post_activate(
            f"https://127.0.0.1:{server.server_port}/activate",
            "device",
            "credential",
            3.0,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert outcome == (302, {})
    assert paths == ["/activate"]


def test_real_local_proxy_tls_failure_then_direct_primary_success(
    monkeypatch,
    tmp_path,
):
    proxy_connects: list[str] = []
    direct_bodies: list[bytes] = []

    class ActivationHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            direct_bodies.append(self.rfile.read(size))
            raw = json.dumps({"ok": False, "mode": "paid", "reason": "bad_token"}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    class BrokenConnectProxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            proxy_connects.append(self.path)
            self.send_response(200, "Connection Established")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *_args):
            pass

    server_context, client_context = _tls_contexts(tmp_path)
    activation = ThreadingHTTPServer(("127.0.0.1", 0), ActivationHandler)
    activation.socket = server_context.wrap_socket(
        activation.socket,
        server_side=True,
    )
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), BrokenConnectProxy)
    threads = [
        threading.Thread(target=activation.serve_forever, daemon=True),
        threading.Thread(target=proxy.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        monkeypatch.setenv("HTTPS_PROXY", proxy_url)
        monkeypatch.setenv("https_proxy", proxy_url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.setattr(ca.urllib.request, "proxy_bypass", lambda _host: False)
        monkeypatch.setattr(ca, "verified_ssl_context", lambda: client_context)
        outcome = ca._post_activate(
            f"https://127.0.0.1:{activation.server_port}/activate",
            "device",
            "credential",
            3.0,
        )
    finally:
        proxy.shutdown()
        activation.shutdown()
        proxy.server_close()
        activation.server_close()
        for thread in threads:
            thread.join(timeout=5)

    assert outcome == (403, {"ok": False, "mode": "paid", "reason": "bad_token"})
    assert len(proxy_connects) == 1
    assert proxy_connects[0].endswith(str(activation.server_port))
    assert len(direct_bodies) == 1
    assert b"credential" in direct_bodies[0]
    serialized = repr(outcome)
    assert proxy_url not in serialized


def test_diagnostic_fixed_guidance_is_exact_and_privacy_safe():
    classification = "PACKAGED_PYTHON_FAIL_WINDOWS_CURL_ACTIVATION_READY"
    guidance = diag.activation_support_guidance(classification)

    assert guidance == {
        "code": "system_proxy_tls_path_incompatible",
        "message": "系统代理/TLS路径不兼容；可关闭非必要代理，或由客户端直连重试。",
    }
    serialized = json.dumps(guidance, ensure_ascii=False)
    for forbidden in (
        "http://",
        "https://",
        "127.0.0.1",
        "token",
        "device",
        "header",
        "payload",
        "exception",
    ):
        assert forbidden not in serialized.lower()

    class ClassificationSubclass(str):
        def __eq__(self, _other):
            raise AssertionError("classification magic must not run")

    assert diag.activation_support_guidance(ClassificationSubclass(classification)) == {
        "code": "no_fixed_action",
        "message": "",
    }


def test_diagnostic_report_wires_fixed_guidance_without_private_inputs(monkeypatch):
    monkeypatch.setattr(
        diag,
        "inspect_system_dns",
        lambda _host: {
            "status": "stable",
            "answers": ["203.0.113.9"],
            "attempts": 1,
            "successful_attempts": 1,
            "error_types": [],
        },
    )
    monkeypatch.setattr(
        diag,
        "inspect_trusted_dns",
        lambda _host: {"status": "available", "answers": ["203.0.113.9"]},
    )
    monkeypatch.setattr(diag, "compare_dns", lambda *_args: "consistent")
    monkeypatch.setattr(
        diag,
        "check_python",
        lambda name, endpoint, _url: {
            "name": name,
            "transport": "packaged_python",
            "endpoint": endpoint,
            "ok": False,
            "stage": "tls",
            "failure_class": "tls_handshake_failed",
        },
    )
    monkeypatch.setattr(
        diag,
        "check_curl",
        lambda name, endpoint, _url, _curl: {
            "name": name,
            "transport": "windows_curl",
            "endpoint": endpoint,
            "ok": True,
            "stage": "success",
            "status": 200,
            "payload_valid": True,
        },
    )
    monkeypatch.setattr(diag, "inspect_local_activation", lambda: {})
    monkeypatch.setattr(diag, "inspect_activation_recheck_history", lambda: {})
    monkeypatch.setattr(diag, "inspect_windows_network_state", lambda: {})
    monkeypatch.setattr(diag, "inspect_windows_proxy_state", lambda: {})
    monkeypatch.setattr(diag, "_curl_version", lambda _path: "fixed")

    report = diag.build_report()

    assert report["classification"] == (
        "PACKAGED_PYTHON_FAIL_WINDOWS_CURL_ACTIVATION_READY"
    )
    assert report["support_guidance"] == {
        "code": "system_proxy_tls_path_incompatible",
        "message": "系统代理/TLS路径不兼容；可关闭非必要代理，或由客户端直连重试。",
    }
    serialized = json.dumps(report, ensure_ascii=False).lower()
    for forbidden in ("private", "token", "device", "header"):
        assert forbidden not in serialized
