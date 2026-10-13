from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from bidking_lab.secure import activation_http_e2ee as protocol  # noqa: E402
from bidking_lab.secure import client_activate  # noqa: E402
import sample_collector_server as collector  # noqa: E402


NOW = 2_000_000_000
TOKEN = "synthetic-token-private-canary"
DEVICE = "synthetic-device-private-canary"
MASTER_KEY = base64.b64encode(b"K" * 32).decode("ascii")


@pytest.fixture(autouse=True)
def _fictional_release_inputs(monkeypatch):
    """Keep metadata deterministic and simulate a global IP without naming a host.

    Only the documentation address's stdlib classification is substituted; URL,
    credential, key, route and transport checks still execute their real code.
    Socket interoperability tests below continue to connect only to loopback.
    """
    monkeypatch.setattr(client_activate, "_client_version", lambda: "0.3.1-hotfix2")
    original = protocol.ipaddress.ip_address

    class FictionalGlobalIPv4(protocol.ipaddress.IPv4Address):
        @property
        def is_global(self):
            return True

        @property
        def is_private(self):
            return False

    def address(value):
        if value == "203.0.113.10":
            return FictionalGlobalIPv4(value)
        return original(value)

    monkeypatch.setattr(protocol.ipaddress, "ip_address", address)


def _private_bytes(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_bytes(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _key_material():
    private = X25519PrivateKey.generate()
    public = _public_bytes(private)
    key_id = protocol.key_id_for_public_key(public)
    return private, public, key_id


def _config(*, url: str = "http://203.0.113.10:18900/activate-e2ee"):
    _private, public, key_id = _key_material()
    return protocol.ActivationHttpE2EEConfig(
        enabled=True,
        raw_sha256="0" * 64,
        url=url,
        key_id=key_id,
        server_public_key=public,
    ), _private


def _activation_payload() -> dict[str, object]:
    return {
        "device": DEVICE,
        "token": TOKEN,
        "client_version": "0.3.1-hotfix2",
        "client_security_epoch": 1,
        "upload_lease_schema": 1,
    }


def _response() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "paid",
        "lease": "synthetic-signed-lease-private-canary",
        "master_key": MASTER_KEY,
        "upload_lease": "synthetic-upload-lease-private-canary",
    }


def test_protocol_round_trip_keeps_activation_and_response_secrets_out_of_http():
    config, private = _config()
    request, client_context = protocol.build_client_request(
        config,
        _activation_payload(),
        now=NOW,
    )
    for canary in (TOKEN, DEVICE, "0.3.1-hotfix2"):
        assert canary.encode("ascii") not in request

    activation, server_context = protocol.open_server_request(
        request,
        private,
        key_id=config.key_id,
        public_key=config.server_public_key,
        now=NOW,
    )
    assert activation == _activation_payload()
    encrypted_response = protocol.build_server_response(
        server_context,
        200,
        _response(),
        now=NOW,
    )
    for canary in (
        MASTER_KEY,
        "synthetic-signed-lease-private-canary",
        "synthetic-upload-lease-private-canary",
    ):
        assert canary.encode("ascii") not in encrypted_response
    assert protocol.open_client_response(
        encrypted_response,
        client_context,
        now=NOW,
    ) == (200, _response())


@pytest.mark.parametrize("target", ["request", "response"])
def test_protocol_rejects_one_byte_ciphertext_tamper(target: str):
    config, private = _config()
    request, client_context = protocol.build_client_request(
        config,
        _activation_payload(),
        now=NOW,
    )
    if target == "request":
        envelope = json.loads(request)
        ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
        ciphertext[-1] ^= 1
        envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        tampered = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(protocol.ActivationHttpE2EEError):
            protocol.open_server_request(
                tampered,
                private,
                key_id=config.key_id,
                public_key=config.server_public_key,
                now=NOW,
            )
        return
    activation, server_context = protocol.open_server_request(
        request,
        private,
        key_id=config.key_id,
        public_key=config.server_public_key,
        now=NOW,
    )
    assert activation == _activation_payload()
    response = protocol.build_server_response(
        server_context,
        200,
        _response(),
        now=NOW,
    )
    envelope = json.loads(response)
    ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
    ciphertext[-1] ^= 1
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    tampered = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.open_client_response(tampered, client_context, now=NOW)


def test_protocol_rejects_wrong_server_key_and_response_rebinding():
    config, private = _config()
    wrong_private = X25519PrivateKey.generate()
    request, client_context = protocol.build_client_request(
        config,
        _activation_payload(),
        now=NOW,
    )
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.open_server_request(
            request,
            wrong_private,
            key_id=config.key_id,
            public_key=config.server_public_key,
            now=NOW,
        )
    _activation, server_context = protocol.open_server_request(
        request,
        private,
        key_id=config.key_id,
        public_key=config.server_public_key,
        now=NOW,
    )
    response = protocol.build_server_response(
        server_context,
        200,
        _response(),
        now=NOW,
    )
    other_request, other_context = protocol.build_client_request(
        config,
        _activation_payload(),
        now=NOW,
    )
    assert other_request != request
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.open_client_response(response, other_context, now=NOW)
    assert protocol.open_client_response(response, client_context, now=NOW)[0] == 200


def test_protocol_rejects_stale_request_and_stale_response():
    config, private = _config()
    request, client_context = protocol.build_client_request(
        config,
        _activation_payload(),
        now=NOW,
    )
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.open_server_request(
            request,
            private,
            key_id=config.key_id,
            public_key=config.server_public_key,
            now=NOW + protocol.MAX_CLOCK_SKEW_SECONDS + 1,
        )
    _activation, server_context = protocol.open_server_request(
        request,
        private,
        key_id=config.key_id,
        public_key=config.server_public_key,
        now=NOW,
    )
    response = protocol.build_server_response(
        server_context,
        200,
        _response(),
        now=NOW,
    )
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.open_client_response(
            response,
            client_context,
            now=NOW + protocol.MAX_CLOCK_SKEW_SECONDS + 1,
        )


def test_server_protocol_rejects_same_envelope_replay():
    config, private = _config()
    server = protocol.ServerProtocol(private, config.key_id, config.server_public_key)
    request, _context = protocol.build_client_request(
        config,
        _activation_payload(),
        now=NOW,
    )
    assert server.open_request(request, now=NOW)[0] == _activation_payload()
    with pytest.raises(protocol.ActivationHttpE2EEError):
        server.open_request(request, now=NOW)


def test_config_disabled_and_enabled_exact_shapes(tmp_path: Path):
    disabled = b'{"enabled":false,"schema_version":1}\n'
    parsed = protocol.parse_config_bytes(disabled)
    assert parsed.enabled is False
    assert parsed.url is None

    _private, public, key_id = _key_material()
    enabled = json.dumps(
        {
            "schema_version": 1,
            "enabled": True,
            "url": "http://203.0.113.10:18900/activate-e2ee",
            "key_id": key_id,
            "server_public_key": base64.b64encode(public).decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    parsed = protocol.parse_config_bytes(enabled)
    assert parsed.enabled is True
    assert parsed.key_id == key_id
    assert parsed.server_public_key == public

    path = tmp_path / protocol.CONFIG_FILE
    path.write_bytes(enabled)
    assert protocol.load_config(path) == parsed


def test_missing_default_config_is_off_but_explicit_missing_path_is_an_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    missing = tmp_path / protocol.CONFIG_FILE
    monkeypatch.setattr(protocol, "config_path", lambda: missing)
    assert protocol.load_config() is None
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.load_config(missing)

    missing.write_bytes(b'{"enabled":true,"schema_version":1}')
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.load_config()


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(extra=True),
        lambda value: value.update(schema_version=True),
        lambda value: value.update(enabled="true"),
        lambda value: value.update(url="https://203.0.113.10:18900/activate-e2ee"),
        lambda value: value.update(url="http://127.0.0.1:18900/activate-e2ee"),
        lambda value: value.update(url="http://100.64.0.1:80/activate-e2ee"),
        lambda value: value.update(url="http://203.0.113.10:18900/activate"),
        lambda value: value.update(url="http://203.0.113.10/activate-e2ee"),
        lambda value: value.update(key_id="x25519-0000000000000000"),
        lambda value: value.update(server_public_key="not-base64"),
    ],
)
def test_enabled_config_malformed_values_fail_closed(mutator):
    _private, public, key_id = _key_material()
    value = {
        "schema_version": 1,
        "enabled": True,
        "url": "http://203.0.113.10:18900/activate-e2ee",
        "key_id": key_id,
        "server_public_key": base64.b64encode(public).decode("ascii"),
    }
    mutator(value)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.parse_config_bytes(raw)


def test_config_duplicate_keys_and_bytes_subclass_fail_closed():
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.parse_config_bytes(b'{"schema_version":1,"enabled":false,"enabled":true}')

    class EvilBytes(bytes):
        magic_calls = 0

        def __bytes__(self):
            self.magic_calls += 1
            return b'{"schema_version":1,"enabled":false}'

    value = EvilBytes(b'{"schema_version":1,"enabled":false}')
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.parse_config_bytes(value)
    assert value.magic_calls == 0


@pytest.mark.parametrize(
    "public_key",
    [bytes(32), bytes([1]) + bytes(31)],
)
def test_enabled_config_rejects_low_order_x25519_public_key(public_key: bytes):
    raw = json.dumps(
        {
            "schema_version": 1,
            "enabled": True,
            "url": "http://203.0.113.10:80/activate-e2ee",
            "key_id": protocol.key_id_for_public_key(public_key),
            "server_public_key": base64.b64encode(public_key).decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.parse_config_bytes(raw)


def test_private_key_loader_requires_exact_key_and_no_padded_file(tmp_path: Path):
    private, public, key_id = _key_material()
    path = tmp_path / "activation_http_e2ee_key.txt"
    path.write_bytes(base64.b64encode(_private_bytes(private)) + b"\n")
    loaded, loaded_id, loaded_public = protocol.load_server_private_key(path)
    assert _public_bytes(loaded) == public
    assert loaded_id == key_id
    assert loaded_public == public
    path.write_bytes(b" " + base64.b64encode(_private_bytes(private)) + b"\n")
    with pytest.raises(protocol.ActivationHttpE2EEError):
        protocol.load_server_private_key(path)


def test_key_admin_is_no_clobber_and_never_prints_private_key(tmp_path: Path):
    path = tmp_path / "activation_http_e2ee_key.txt"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "activation_http_e2ee_key_admin.py"),
        "init",
        "--private-key-file",
        str(path),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    first = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    before = path.read_bytes()
    second = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    assert path.read_bytes() == before
    assert base64.b64encode(base64.b64decode(before.strip())).decode("ascii") not in first.stdout
    assert json.loads(first.stdout)["state"] == "created"
    assert json.loads(second.stdout)["state"] == "existing"


@contextmanager
def _real_handler_server(monkeypatch: pytest.MonkeyPatch):
    config, private = _config(url="http://127.0.0.1:1/activate-e2ee")
    server_protocol = protocol.ServerProtocol(private, config.key_id, config.server_public_key)
    monkeypatch.setattr(collector, "_ACT", object())
    monkeypatch.setattr(collector, "_ACTIVATION_HTTP_E2EE", server_protocol)
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(
        collector,
        "_handle_activation_body",
        lambda body: (200, _response()) if body == _activation_payload() else (400, {}),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), collector.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        loopback_config = protocol.ActivationHttpE2EEConfig(
            enabled=True,
            raw_sha256=config.raw_sha256,
            url=f"http://127.0.0.1:{server.server_port}/activate-e2ee",
            key_id=config.key_id,
            server_public_key=config.server_public_key,
        )
        yield loopback_config
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_real_handler_and_client_transport_interoperate_without_plaintext(monkeypatch):
    with _real_handler_server(monkeypatch) as config:
        result = client_activate._post_activate_http_e2ee(
            config,
            DEVICE,
            TOKEN,
            3,
        )
    assert result == (200, _response())


def test_real_handler_marks_encrypted_authority_response_no_store(monkeypatch):
    with _real_handler_server(monkeypatch) as config:
        raw_request, context = protocol.build_client_request(
            config,
            _activation_payload(),
        )
        request = urllib.request.Request(
            config.url,
            data=raw_request,
            headers={"Content-Type": "application/vnd.bidking.activation-e2ee+json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
            raw_response = response.read()
            assert response.headers.get("Cache-Control") == "no-store"
            assert response.headers.get_content_type() == (
                "application/vnd.bidking.activation-e2ee+json"
            )
        assert protocol.open_client_response(raw_response, context) == (
            200,
            _response(),
        )
        for canary in (
            "synthetic-master-key-private-canary",
            "synthetic-signed-lease-private-canary",
            "synthetic-upload-lease-private-canary",
        ):
            assert canary.encode("ascii") not in raw_response


def test_client_emergency_carrier_never_follows_http_redirect():
    hits: list[str] = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            # Consume the complete encrypted request before replying.  On Windows,
            # closing a socket with unread request bytes can replace the already
            # written 302 with WSAECONNABORTED, which tests a TCP abort rather than
            # the no-redirect contract.
            content_length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(content_length)
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/redirect-target")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        config, _private = _config(
            url=f"http://127.0.0.1:{server.server_port}/activate-e2ee"
        )
        result = client_activate._post_activate_http_e2ee(
            config,
            DEVICE,
            TOKEN,
            3,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert isinstance(result, client_activate._ActivationPostFailure)
    assert result.failure_class == "http_reachable_invalid_payload"
    assert hits == ["/activate-e2ee"]


def _endpoint(endpoint_id: str):
    return client_activate._ActivationEndpointCandidate(
        endpoint_id=endpoint_id,
        base=f"https://{endpoint_id}.example.test",
        activation_url=f"https://{endpoint_id}.example.test/activate",
        telemetry_url=f"https://{endpoint_id}.example.test/telemetry",
    )


def test_client_uses_emergency_only_after_every_https_endpoint_network_fails(monkeypatch):
    config, _private = _config()
    https_calls: list[str] = []
    emergency_calls: list[str] = []
    monkeypatch.setattr(
        client_activate,
        "_activation_endpoint_candidates",
        lambda _ingest: [_endpoint("endpoint_1"), _endpoint("endpoint_2")],
    )

    def fail_https(url, _device, _token, _timeout):
        https_calls.append(url)
        return client_activate._ActivationPostFailure(
            "connect_timeout",
            stage="tcp",
        )

    monkeypatch.setattr(client_activate, "_post_activate", fail_https)
    monkeypatch.setattr(client_activate, "_load_activation_http_e2ee_config", lambda: config)

    def deny_emergency(*args, **kwargs):
        del args, kwargs
        emergency_calls.append("called")
        return 401, {"ok": False, "mode": "paid", "reason": "bad_token"}

    monkeypatch.setattr(client_activate, "_post_activate_http_e2ee", deny_emergency)
    result = client_activate.activate(ui_token="synthetic", now=NOW, defer_commit=True)
    assert len(https_calls) == 2
    assert emergency_calls == ["called"]
    assert result.reason == "bad_token"
    assert result.activated is False
    assert result.transport_summary.endpoint_count == 3
    assert result.transport_summary.stage_counts[-2] == ("http", 1)
    trace = client_activate.consume_activation_transport_trace()
    assert [row[0] for row in trace] == ["endpoint_1", "endpoint_2", "endpoint_4"]
    serialized_trace = json.dumps(trace, ensure_ascii=True)
    assert "203.0.113.10" not in serialized_trace
    assert protocol.ROUTE not in serialized_trace


@pytest.mark.parametrize(
    "https_result",
    [
        (403, {"ok": False, "mode": "paid", "reason": "bad_token"}),
        client_activate._ActivationPostFailure(
            "http_reachable_invalid_payload",
            retry_fallback=False,
            stage="http",
        ),
        None,
    ],
)
def test_client_never_uses_emergency_after_https_authority_or_unknown_failure(
    monkeypatch,
    https_result,
):
    config, _private = _config()
    monkeypatch.setattr(
        client_activate,
        "_activation_endpoint_candidates",
        lambda _ingest: [_endpoint("endpoint_1")],
    )
    monkeypatch.setattr(client_activate, "_post_activate", lambda *args: https_result)
    monkeypatch.setattr(client_activate, "_load_activation_http_e2ee_config", lambda: config)
    monkeypatch.setattr(
        client_activate,
        "_post_activate_http_e2ee",
        lambda *args, **kwargs: pytest.fail("emergency channel must not run"),
    )
    result = client_activate.activate(ui_token="synthetic", now=NOW, defer_commit=True)
    if isinstance(result, client_activate.ActivationCandidate):
        result = result.result
    assert result.activated is False


def test_runtime_kill_switch_only_disables_config(monkeypatch):
    config, _private = _config()
    monkeypatch.setattr(protocol, "load_config", lambda: config)
    monkeypatch.delenv("BIDKING_DISABLE_ACTIVATION_HTTP_E2EE", raising=False)
    assert client_activate._load_activation_http_e2ee_config() == config
    monkeypatch.setenv("BIDKING_DISABLE_ACTIVATION_HTTP_E2EE", "1")
    assert client_activate._load_activation_http_e2ee_config() is None
    monkeypatch.setenv("BIDKING_DISABLE_ACTIVATION_HTTP_E2EE", "0")
    assert client_activate._load_activation_http_e2ee_config() == config


def test_server_disabled_route_returns_fixed_error_without_processing(monkeypatch):
    monkeypatch.setattr(collector, "_ACT", object())
    monkeypatch.setattr(collector, "_ACTIVATION_HTTP_E2EE", None)
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(
        collector,
        "_handle_activation_body",
        lambda _body: pytest.fail("disabled route must not process activation"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), collector.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/activate-e2ee",
            data=b"{}",
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as captured:
            urllib.request.urlopen(request, timeout=3)  # noqa: S310
        assert captured.value.code == 503
        body = captured.value.read()
        assert TOKEN.encode() not in body
        assert DEVICE.encode() not in body
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
