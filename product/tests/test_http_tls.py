from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from bidking_lab import http_tls
from bidking_lab.live import sample_upload, version_check
from bidking_lab.secure import client_activate


@pytest.fixture(autouse=True)
def _clear_context_cache():
    http_tls.verified_ssl_context.cache_clear()
    yield
    http_tls.verified_ssl_context.cache_clear()


def test_context_uses_explicit_bundle_and_keeps_strict_verification(tmp_path, monkeypatch):
    bundle = tmp_path / "cacert.pem"
    bundle.write_bytes(b"x" * 10_001)
    seen = {}

    class _Context:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True

    def _factory(*, cafile):
        seen["cafile"] = cafile
        return _Context()

    monkeypatch.setattr(http_tls, "_bundled_ca_file", lambda: bundle)
    monkeypatch.setattr(http_tls.ssl, "create_default_context", _factory)

    context = http_tls.verified_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert Path(seen["cafile"]) == bundle


def test_https_passes_explicit_context_but_http_keeps_test_transport(monkeypatch):
    sentinel = object()
    calls = []

    def _urlopen(target, **kwargs):
        calls.append((target, kwargs))
        return object()

    monkeypatch.setattr(http_tls, "verified_ssl_context", lambda: sentinel)
    monkeypatch.setattr(http_tls.urllib.request, "urlopen", _urlopen)

    http_tls.urlopen_verified("https://api.example/healthz", timeout=4)
    http_tls.urlopen_verified("http://127.0.0.1/healthz", timeout=2)

    assert calls[0][0].full_url == "https://api.example/healthz"
    assert calls[0][0].get_header("User-agent") == http_tls.PRODUCT_USER_AGENT
    assert calls[0][1] == {"timeout": 4, "context": sentinel}
    assert calls[1][0].full_url == "http://127.0.0.1/healthz"
    assert calls[1][0].get_header("User-agent") == http_tls.PRODUCT_USER_AGENT
    assert calls[1][1] == {"timeout": 2}


def test_verified_transport_preserves_explicit_user_agent(monkeypatch):
    seen = []

    def _urlopen(target, **_kwargs):
        seen.append(target)
        return object()

    monkeypatch.setattr(http_tls.urllib.request, "urlopen", _urlopen)
    request = http_tls.urllib.request.Request(
        "http://127.0.0.1/healthz",
        headers={"User-Agent": "Explicit-Agent/2"},
    )

    http_tls.urlopen_verified(request, timeout=2)

    assert seen[0] is request
    assert request.get_header("User-agent") == "Explicit-Agent/2"


def test_missing_bundle_fails_closed(monkeypatch):
    monkeypatch.setattr(http_tls, "_bundled_ca_file", lambda: (_ for _ in ()).throw(
        RuntimeError("trusted CA bundle is unavailable")
    ))
    with pytest.raises(RuntimeError, match="CA bundle"):
        http_tls.verified_ssl_context()


def test_all_product_https_paths_use_verified_transport(monkeypatch):
    calls = []

    class _Response:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

        def read(self):
            return self._payload

    def _activation(target, *, timeout):
        calls.append(("activation", target.full_url, timeout))
        return _Response(b'{"ok": true}')

    def _upload(target, *, timeout):
        calls.append(("upload", target.full_url, timeout))
        return _Response(b'{"accepted": 1}')

    def _version(target, *, timeout):
        calls.append(("version", str(target), timeout))
        return _Response(b'{"latest": "0.2.8", "min": "0.2.0"}')

    monkeypatch.setattr(client_activate, "urlopen_verified", _activation)
    monkeypatch.setattr(sample_upload, "urlopen_verified", _upload)
    monkeypatch.setattr(version_check, "urlopen_verified", _version)
    monkeypatch.setattr(client_activate, "_client_version", lambda: "0.2.8")
    monkeypatch.setattr(sample_upload, "_write_upload_status", lambda _line: None)

    assert client_activate._post_activate(
        "https://api.example/activate", "device", "token", 4
    ) == (200, {"ok": True})
    sample_upload._post(
        "https://api.example/ingest", "key", {"map_id": 1, "hero": "aisha"}
    )
    assert version_check.check_against_server("https://api.example/ingest")["ok"] is True

    assert [name for name, _url, _timeout in calls] == [
        "activation",
        "upload",
        "version",
    ]
