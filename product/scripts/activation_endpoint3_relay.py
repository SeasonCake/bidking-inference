"""Provider-neutral endpoint_3 relay and Seoul-origin adapter.

The public edge role accepts only the four activation surfaces frozen for hotfix2.
It signs exactly one request to the separately configured origin adapter.  The
origin role authenticates that envelope, claims the nonce in a SQLite replay store,
and forwards exactly once to the loopback collector.  Existing collector routes are
not changed by this module.

This file deliberately contains no domain, address, certificate, cloud-account,
activation key, master key, or device-block key.  Infrastructure supplies a base URL
and a relay-only HMAC key.  Both roles are disabled unless the command-line switch
and the environment kill switch are explicitly enabled.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

from activation_endpoint3_relay_state_admin import (
    RelayReplayStateError,
    inspect_replay_database,
    require_ready_replay_database,
)


RELAY_VERSION = "endpoint3-relay-v1"
ENABLE_ENV = "BIDKING_ENDPOINT3_RELAY_ENABLED"
ORIGIN_PREFIX = "/bidking-relay/v1"
MAX_BODY_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
AUTH_WINDOW_SECONDS = 30
_SIGNING_DOMAIN = b"bidking-endpoint3-relay-v1\0"
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TIMESTAMP_RE = re.compile(r"^[0-9]{1,12}$", re.ASCII)
_JSON_CONTENT_TYPE_RE = re.compile(
    r"^application/json(?:;[ ]*charset=utf-8)?$",
    re.IGNORECASE | re.ASCII,
)
_ALLOWED_METHODS = {
    "/version": frozenset({"GET", "HEAD"}),
    "/healthz": frozenset({"GET", "HEAD"}),
    "/activation-readiness": frozenset({"GET", "HEAD"}),
    "/activate": frozenset({"POST"}),
}
_ROUTE_IDS = {
    "/version": "version",
    "/healthz": "healthz",
    "/activation-readiness": "activation_readiness",
    "/activate": "activate",
}
_LOG_ROUTE_IDS = frozenset({*(_ROUTE_IDS.values()), "unknown"})
_LOG_STATUS_FAMILIES = frozenset({"2xx", "4xx", "5xx", "other"})
_LOG_UPSTREAM_CLASSES = frozenset(
    {
        "http_response",
        "network_failure",
        "protocol_failure",
        "request_rejected",
        "auth_rejected",
        "disabled",
    }
)
_LOG_WRITE_RESULTS = frozenset({"ok", "broken_pipe", "failed"})
_LOG_LATENCY_BUCKETS = frozenset(
    {"lt_100ms", "100_to_500ms", "500_to_2000ms", "ge_2000ms"}
)


class RelayConfigurationError(ValueError):
    """A fixed, non-secret configuration failure."""


class RelayRequestError(ValueError):
    def __init__(self, code: int, reason: str, *, upstream_class: str = "request_rejected"):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.upstream_class = upstream_class


@dataclass(frozen=True)
class RelayResponse:
    code: int
    body: bytes
    upstream_class: str


def _exact_str(value, *, field: str) -> str:
    if type(value) is not str:  # noqa: E721 - authority boundary rejects subclasses
        raise TypeError(f"{field} must be an exact string")
    return value


def _exact_bytes(value, *, field: str) -> bytes:
    if type(value) is not bytes:  # noqa: E721 - authority boundary rejects subclasses
        raise TypeError(f"{field} must be exact bytes")
    return value


def _validate_key(key) -> bytes:
    raw = _exact_bytes(key, field="relay key")
    if not (32 <= len(raw) <= 4096):
        raise RelayConfigurationError("relay_key_unavailable")
    return raw


def _validate_forbidden_digests(values) -> frozenset[str]:
    if type(values) is not frozenset:  # noqa: E721 - fixed config shape
        raise TypeError("forbidden key digests must be an exact frozenset")
    if len(values) < 3:
        raise RelayConfigurationError("relay_key_separation_unverified")
    checked: set[str] = set()
    for value in values:
        text = _exact_str(value, field="forbidden key digest")
        if _DIGEST_RE.fullmatch(text) is None:
            raise RelayConfigurationError("invalid_forbidden_key_digest")
        checked.add(text)
    return frozenset(checked)


def _validate_key_separation(key: bytes, forbidden: frozenset[str]) -> None:
    actual = hashlib.sha256(key).digest()
    shared = 0
    for item in forbidden:
        shared |= int(hmac.compare_digest(actual, bytes.fromhex(item)))
    if shared:
        raise RelayConfigurationError("relay_key_reuses_protected_key")


def _canonical_message(
    *,
    security_epoch: int,
    timestamp: int,
    nonce: str,
    method: str,
    path: str,
    body: bytes,
) -> bytes:
    if type(security_epoch) is not int or security_epoch <= 0:  # noqa: E721
        raise TypeError("security epoch must be an exact positive integer")
    if type(timestamp) is not int:  # noqa: E721
        raise TypeError("timestamp must be an exact integer")
    nonce = _exact_str(nonce, field="nonce")
    method = _exact_str(method, field="method")
    path = _exact_str(path, field="path")
    body = _exact_bytes(body, field="body")
    if timestamp < 0 or _NONCE_RE.fullmatch(nonce) is None:
        raise ValueError("invalid relay envelope")
    if method not in {"GET", "HEAD", "POST"}:
        raise ValueError("invalid relay method")
    if not path.startswith(ORIGIN_PREFIX + "/") or "?" in path or "#" in path:
        raise ValueError("invalid relay path")
    body_sha256 = hashlib.sha256(body).hexdigest()
    fields = (
        "1",
        RELAY_VERSION,
        str(security_epoch),
        str(timestamp),
        nonce,
        method,
        path,
        body_sha256,
    )
    return _SIGNING_DOMAIN + "\n".join(fields).encode("ascii")


def sign_relay_request(
    key,
    *,
    security_epoch: int = 1,
    timestamp: int,
    nonce: str,
    method: str,
    path: str,
    body: bytes,
) -> str:
    secret = _validate_key(key)
    message = _canonical_message(
        security_epoch=security_epoch,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body=body,
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def signed_headers(
    key,
    *,
    security_epoch: int = 1,
    timestamp: int,
    nonce: str,
    method: str,
    path: str,
    body: bytes,
) -> dict[str, str]:
    return {
        "X-BidKing-Relay-Version": RELAY_VERSION,
        "X-BidKing-Relay-Security-Epoch": str(security_epoch),
        "X-BidKing-Relay-Timestamp": str(timestamp),
        "X-BidKing-Relay-Nonce": nonce,
        "X-BidKing-Relay-Signature": sign_relay_request(
            key,
            security_epoch=security_epoch,
            timestamp=timestamp,
            nonce=nonce,
            method=method,
            path=path,
            body=body,
        ),
    }


class SQLiteReplayGuard:
    """Cross-thread and restart-safe nonce claims for the short HMAC window."""

    def __init__(
        self,
        path: Path,
        *,
        security_epoch: int = 1,
        auth_window_seconds: int = AUTH_WINDOW_SECONDS,
    ) -> None:
        if not isinstance(path, Path):
            raise TypeError("replay path must be a pathlib Path")
        self.path = path
        self.security_epoch = security_epoch
        self.auth_window_seconds = auth_window_seconds
        self._lock = threading.Lock()
        try:
            require_ready_replay_database(
                self.path,
                security_epoch=security_epoch,
                auth_window_seconds=auth_window_seconds,
            )
            info = self.path.lstat()
        except (OSError, TypeError, RelayReplayStateError) as exc:
            raise RelayConfigurationError("replay_store_not_ready") from exc
        self._identity = (int(info.st_dev), int(info.st_ino))

    def _connect(self) -> sqlite3.Connection:
        before = self.path.lstat()
        if (int(before.st_dev), int(before.st_ino)) != self._identity:
            raise RelayConfigurationError("replay_store_identity_changed")
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.execute("PRAGMA busy_timeout=2000")
        after = self.path.lstat()
        if (int(after.st_dev), int(after.st_ino)) != self._identity:
            connection.close()
            raise RelayConfigurationError("replay_store_identity_changed")
        return connection

    def claim(self, nonce: str, *, expires_at: int, now: int) -> bool:
        nonce = _exact_str(nonce, field="nonce")
        if _NONCE_RE.fullmatch(nonce) is None:
            return False
        if type(expires_at) is not int or type(now) is not int:  # noqa: E721
            return False
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM relay_nonce WHERE expires_at < ?", (now,))
            try:
                connection.execute(
                    "INSERT INTO relay_nonce(nonce, expires_at) VALUES (?, ?)",
                    (nonce, expires_at),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return False
            connection.commit()
            return True


def _single_header(headers: Mapping[str, tuple[str, ...]], name: str) -> str | None:
    values = headers.get(name.lower())
    if values is None:
        return None
    if type(values) is not tuple or len(values) != 1:  # noqa: E721
        raise RelayRequestError(400, "relay_bad_request")
    value = values[0]
    if type(value) is not str:  # noqa: E721
        raise RelayRequestError(400, "relay_bad_request")
    return value


def _header_tuples(message) -> dict[str, tuple[str, ...]]:
    names: set[str] = set()
    result: dict[str, tuple[str, ...]] = {}
    for name in message.keys():
        if type(name) is not str:  # noqa: E721
            continue
        lowered = name.lower()
        if lowered in names:
            continue
        names.add(lowered)
        values = message.get_all(name) or []
        if all(type(value) is str for value in values):  # noqa: E721
            result[lowered] = tuple(values)
    return result


def _public_path(mode: str, raw_path: str) -> str:
    raw_path = _exact_str(raw_path, field="request path")
    if (
        not raw_path.startswith("/")
        or raw_path.startswith("//")
        or "?" in raw_path
        or "#" in raw_path
        or "%" in raw_path
        or "\\" in raw_path
        or any(ord(char) < 0x20 or ord(char) > 0x7E for char in raw_path)
    ):
        raise RelayRequestError(400, "relay_bad_request")
    if mode == "origin":
        if not raw_path.startswith(ORIGIN_PREFIX + "/"):
            raise RelayRequestError(404, "relay_not_found")
        return raw_path[len(ORIGIN_PREFIX) :]
    return raw_path


def _validate_envelope(
    *,
    mode: str,
    method: str,
    raw_path: str,
    headers: Mapping[str, tuple[str, ...]],
) -> tuple[str, int]:
    method = _exact_str(method, field="method")
    if method not in {"GET", "HEAD", "POST"}:
        raise RelayRequestError(405, "relay_method_not_allowed")
    public_path = _public_path(mode, raw_path)
    methods = _ALLOWED_METHODS.get(public_path)
    if methods is None:
        raise RelayRequestError(404, "relay_not_found")
    if method not in methods:
        raise RelayRequestError(405, "relay_method_not_allowed")
    if _single_header(headers, "transfer-encoding") is not None:
        raise RelayRequestError(400, "relay_bad_request")
    content_length = _single_header(headers, "content-length")
    if content_length is None:
        length = 0
        if method == "POST":
            raise RelayRequestError(411, "relay_length_required")
    elif re.fullmatch(r"[0-9]{1,9}", content_length, re.ASCII) is None:
        raise RelayRequestError(400, "relay_bad_request")
    else:
        length = int(content_length)
    if length > MAX_BODY_BYTES:
        raise RelayRequestError(413, "relay_payload_too_large")
    if method in {"GET", "HEAD"} and length != 0:
        raise RelayRequestError(400, "relay_bad_request")
    if method == "POST":
        content_type = _single_header(headers, "content-type")
        if content_type is None or _JSON_CONTENT_TYPE_RE.fullmatch(content_type) is None:
            raise RelayRequestError(415, "relay_json_required")
    return public_path, length


def _verify_auth(
    *,
    key: bytes,
    replay_guard: SQLiteReplayGuard,
    security_epoch: int,
    auth_window_seconds: int,
    headers: Mapping[str, tuple[str, ...]],
    method: str,
    raw_path: str,
    body: bytes,
    now: int,
) -> None:
    version = _single_header(headers, "x-bidking-relay-version")
    security_epoch_text = _single_header(headers, "x-bidking-relay-security-epoch")
    timestamp_text = _single_header(headers, "x-bidking-relay-timestamp")
    nonce = _single_header(headers, "x-bidking-relay-nonce")
    signature = _single_header(headers, "x-bidking-relay-signature")
    if (
        version != RELAY_VERSION
        or security_epoch_text != str(security_epoch)
        or timestamp_text is None
        or nonce is None
        or signature is None
        or _TIMESTAMP_RE.fullmatch(timestamp_text) is None
        or _NONCE_RE.fullmatch(nonce) is None
        or _DIGEST_RE.fullmatch(signature) is None
    ):
        raise RelayRequestError(401, "relay_auth_failed", upstream_class="auth_rejected")
    timestamp = int(timestamp_text)
    if abs(now - timestamp) > auth_window_seconds:
        raise RelayRequestError(401, "relay_auth_failed", upstream_class="auth_rejected")
    expected = sign_relay_request(
        key,
        security_epoch=security_epoch,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=raw_path,
        body=body,
    )
    if not hmac.compare_digest(expected, signature):
        raise RelayRequestError(401, "relay_auth_failed", upstream_class="auth_rejected")
    if not replay_guard.claim(
        nonce,
        expires_at=timestamp + auth_window_seconds,
        now=now,
    ):
        raise RelayRequestError(403, "relay_replay_rejected", upstream_class="auth_rejected")


def _validate_upstream_base(
    value,
    *,
    mode: str,
    allow_http_loopback: bool,
) -> tuple[str, str, int, str]:
    value = _exact_str(value, field="upstream base")
    if type(allow_http_loopback) is not bool:  # noqa: E721
        raise TypeError("allow_http_loopback must be an exact boolean")
    parsed = urlsplit(value)
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not parsed.hostname
    ):
        raise RelayConfigurationError("invalid_upstream_base")
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    loopback = hostname in {"127.0.0.1", "localhost", "::1"}
    if scheme == "http" and not (allow_http_loopback and loopback):
        raise RelayConfigurationError("https_upstream_required")
    if scheme not in {"http", "https"}:
        raise RelayConfigurationError("invalid_upstream_scheme")
    if mode == "origin" and not loopback:
        raise RelayConfigurationError("origin_collector_must_be_loopback")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise RelayConfigurationError("invalid_upstream_port") from exc
    authority = parsed.netloc
    return scheme, hostname, port, authority


def _status_family(code: int) -> str:
    if type(code) is not int:  # noqa: E721
        return "other"
    if 200 <= code < 300:
        return "2xx"
    if 400 <= code < 500:
        return "4xx"
    if 500 <= code < 600:
        return "5xx"
    return "other"


def _latency_bucket(elapsed_ms: float) -> str:
    if type(elapsed_ms) not in {float, int} or elapsed_ms < 0:  # noqa: E721
        return "ge_2000ms"
    if elapsed_ms < 100:
        return "lt_100ms"
    if elapsed_ms < 500:
        return "100_to_500ms"
    if elapsed_ms < 2000:
        return "500_to_2000ms"
    return "ge_2000ms"


def emit_fixed_log(
    logger: Callable[[str], object],
    *,
    route_id,
    status_family,
    upstream_class,
    write_result,
    latency_bucket,
) -> None:
    route = route_id if type(route_id) is str and route_id in _LOG_ROUTE_IDS else "unknown"  # noqa: E721
    family = (
        status_family
        if type(status_family) is str and status_family in _LOG_STATUS_FAMILIES  # noqa: E721
        else "other"
    )
    upstream = (
        upstream_class
        if type(upstream_class) is str and upstream_class in _LOG_UPSTREAM_CLASSES  # noqa: E721
        else "protocol_failure"
    )
    write = (
        write_result
        if type(write_result) is str and write_result in _LOG_WRITE_RESULTS  # noqa: E721
        else "failed"
    )
    latency = (
        latency_bucket
        if type(latency_bucket) is str and latency_bucket in _LOG_LATENCY_BUCKETS  # noqa: E721
        else "ge_2000ms"
    )
    line = (
        f"route_id={route} status_family={family} upstream_class={upstream} "
        f"write_result={write} latency_bucket={latency} relay_version={RELAY_VERSION}"
    )
    try:
        logger(line)
    except Exception:  # noqa: BLE001 - observability cannot break the relay
        pass


def write_body(stream, body: bytes) -> str:
    body = _exact_bytes(body, field="response body")
    try:
        stream.write(body)
        return "ok"
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        return "broken_pipe"
    except OSError:
        return "failed"


def _fixed_error(code: int, reason: str, upstream_class: str) -> RelayResponse:
    body = json.dumps(
        {"ok": False, "reason": reason},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return RelayResponse(code=code, body=body, upstream_class=upstream_class)


def _strict_json_loads(raw: bytes):
    raw = _exact_bytes(raw, field="json body")

    def reject_constant(_value: str):
        raise ValueError("non-standard JSON constant")

    return json.loads(raw.decode("utf-8"), parse_constant=reject_constant)


class RelayApplication:
    def __init__(
        self,
        *,
        mode,
        enabled,
        key,
        forbidden_key_sha256,
        upstream_base,
        replay_db_path,
        allow_http_loopback=False,
        timeout_seconds=5.0,
        security_epoch=1,
        auth_window_seconds=AUTH_WINDOW_SECONDS,
        initialize_replay_store=True,
        logger=None,
        clock=None,
        nonce_factory=None,
    ) -> None:
        mode = _exact_str(mode, field="mode")
        if mode not in {"edge", "origin"}:
            raise RelayConfigurationError("invalid_relay_mode")
        if type(enabled) is not bool:  # noqa: E721
            raise TypeError("enabled must be an exact boolean")
        if type(timeout_seconds) not in {int, float} or not (0 < timeout_seconds <= 30):  # noqa: E721
            raise RelayConfigurationError("invalid_timeout")
        if type(security_epoch) is not int or security_epoch <= 0:  # noqa: E721
            raise RelayConfigurationError("invalid_relay_security_epoch")
        if (
            type(auth_window_seconds) is not int  # noqa: E721
            or not (1 <= auth_window_seconds <= 300)
        ):
            raise RelayConfigurationError("invalid_auth_window")
        if type(initialize_replay_store) is not bool:  # noqa: E721
            raise TypeError("initialize_replay_store must be an exact boolean")
        secret = _validate_key(key)
        forbidden = _validate_forbidden_digests(forbidden_key_sha256)
        _validate_key_separation(secret, forbidden)
        scheme, hostname, port, authority = _validate_upstream_base(
            upstream_base,
            mode=mode,
            allow_http_loopback=allow_http_loopback,
        )
        if not isinstance(replay_db_path, Path):
            raise TypeError("replay_db_path must be a pathlib Path")
        self.mode = mode
        self.enabled = enabled
        self.key = secret
        self.forbidden_key_sha256 = forbidden
        self.upstream_base = upstream_base
        self.upstream_scheme = scheme
        self.upstream_hostname = hostname
        self.upstream_port = port
        self.upstream_authority = authority
        self.timeout_seconds = float(timeout_seconds)
        self.security_epoch = security_epoch
        self.auth_window_seconds = auth_window_seconds
        self.logger = logger if callable(logger) else print
        self.clock = clock if callable(clock) else time.time
        self.nonce_factory = nonce_factory if callable(nonce_factory) else lambda: secrets.token_hex(16)
        self.replay_db_path = replay_db_path
        self.replay_guard = (
            SQLiteReplayGuard(
                replay_db_path,
                security_epoch=security_epoch,
                auth_window_seconds=auth_window_seconds,
            )
            if mode == "origin" and enabled and initialize_replay_store
            else None
        )

    def inspect(
        self,
        *,
        method: str,
        raw_path: str,
        headers: Mapping[str, tuple[str, ...]],
    ) -> tuple[str, int]:
        if not self.enabled:
            raise RelayRequestError(503, "relay_disabled", upstream_class="disabled")
        return _validate_envelope(
            mode=self.mode,
            method=method,
            raw_path=raw_path,
            headers=headers,
        )

    def process(
        self,
        *,
        method: str,
        raw_path: str,
        public_path: str,
        headers: Mapping[str, tuple[str, ...]],
        body: bytes,
    ) -> RelayResponse:
        body = _exact_bytes(body, field="request body")
        if method == "POST":
            try:
                _strict_json_loads(body)
            except (UnicodeError, ValueError):
                return _fixed_error(400, "relay_bad_json", "request_rejected")
        if self.mode == "origin":
            if self.replay_guard is None:
                return _fixed_error(503, "relay_disabled", "disabled")
            try:
                _verify_auth(
                    key=self.key,
                    replay_guard=self.replay_guard,
                    security_epoch=self.security_epoch,
                    auth_window_seconds=self.auth_window_seconds,
                    headers=headers,
                    method=method,
                    raw_path=raw_path,
                    body=body,
                    now=int(self.clock()),
                )
            except RelayRequestError as exc:
                return _fixed_error(exc.code, exc.reason, exc.upstream_class)
            upstream_path = public_path
            # The existing collector has GET semantics for these public probes but
            # does not implement BaseHTTPRequestHandler.do_HEAD.  The authenticated
            # origin adapter performs one GET and lets both HTTP handlers suppress
            # the body for HEAD, preserving the expected status without touching
            # current public collector routes.
            upstream_method = "GET" if method == "HEAD" else method
            outbound_headers = {"Content-Type": "application/json"} if method == "POST" else {}
        else:
            upstream_path = ORIGIN_PREFIX + public_path
            upstream_method = method
            now = int(self.clock())
            nonce = self.nonce_factory()
            if type(nonce) is not str or _NONCE_RE.fullmatch(nonce) is None:  # noqa: E721
                return _fixed_error(503, "relay_upstream_unavailable", "protocol_failure")
            outbound_headers = signed_headers(
                self.key,
                security_epoch=self.security_epoch,
                timestamp=now,
                nonce=nonce,
                method=method,
                path=upstream_path,
                body=body,
            )
            if method == "POST":
                outbound_headers["Content-Type"] = "application/json"
        return self._upstream_request(
            method=upstream_method,
            path=upstream_path,
            body=body,
            headers=outbound_headers,
        )

    def _upstream_request(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> RelayResponse:
        connection: http.client.HTTPConnection | None = None
        try:
            if self.upstream_scheme == "https":
                connection = http.client.HTTPSConnection(
                    self.upstream_hostname,
                    self.upstream_port,
                    timeout=self.timeout_seconds,
                    context=ssl.create_default_context(),
                )
            else:
                connection = http.client.HTTPConnection(
                    self.upstream_hostname,
                    self.upstream_port,
                    timeout=self.timeout_seconds,
                )
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            code = response.status
            if type(code) is not int or not (200 <= code < 600) or 300 <= code < 400:  # noqa: E721
                response.read(MAX_RESPONSE_BYTES + 1)
                return _fixed_error(503, "relay_upstream_unavailable", "protocol_failure")
            if method == "HEAD":
                response.read(MAX_RESPONSE_BYTES + 1)
                return RelayResponse(code=code, body=b"", upstream_class="http_response")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                return _fixed_error(503, "relay_upstream_unavailable", "protocol_failure")
            content_types = [
                value
                for name, value in response.getheaders()
                if name.lower() == "content-type"
            ]
            if (
                len(content_types) != 1
                or _JSON_CONTENT_TYPE_RE.fullmatch(content_types[0]) is None
            ):
                return _fixed_error(503, "relay_upstream_unavailable", "protocol_failure")
            try:
                _strict_json_loads(payload)
            except (UnicodeError, ValueError):
                return _fixed_error(503, "relay_upstream_unavailable", "protocol_failure")
            return RelayResponse(code=code, body=payload, upstream_class="http_response")
        except (socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, OSError):
            return _fixed_error(503, "relay_upstream_unavailable", "network_failure")
        except http.client.HTTPException:
            return _fixed_error(503, "relay_upstream_unavailable", "protocol_failure")
        finally:
            if connection is not None:
                connection.close()

    def preflight_snapshot(self) -> dict[str, object]:
        route_contract = json.dumps(
            {path: sorted(methods) for path, methods in sorted(_ALLOWED_METHODS.items())},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        upstream_config = (
            f"{self.mode}|{self.upstream_scheme}|{self.upstream_authority}"
        ).encode("utf-8")
        if self.mode == "origin":
            replay_snapshot = inspect_replay_database(
                self.replay_db_path,
                expected_security_epoch=self.security_epoch,
                expected_auth_window_seconds=self.auth_window_seconds,
            )
            replay_state = replay_snapshot.state
            replay_ready = replay_snapshot.ready
        else:
            replay_state = "not_applicable"
            replay_ready = True
        eligible = self.enabled and replay_ready
        return {
            "schema_version": 1,
            "relay_version": RELAY_VERSION,
            "mode": self.mode,
            "enabled": self.enabled,
            "eligible": eligible,
            "key_separation_verified": True,
            "relay_key_id": hmac.new(
                self.key,
                b"bidking-endpoint3-relay-key-id-v1",
                hashlib.sha256,
            ).hexdigest(),
            "replay_store_configured": replay_ready,
            "replay_store_state": replay_state,
            "relay_security_epoch": self.security_epoch,
            "max_body_bytes": MAX_BODY_BYTES,
            "auth_window_seconds": self.auth_window_seconds,
            "route_contract_sha256": hashlib.sha256(route_contract).hexdigest(),
            "upstream_config_sha256": hashlib.sha256(upstream_config).hexdigest(),
        }


def _route_id(mode: str, raw_path: str) -> str:
    try:
        return _ROUTE_IDS.get(_public_path(mode, raw_path), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def make_handler(application: RelayApplication) -> type[BaseHTTPRequestHandler]:
    if not isinstance(application, RelayApplication):
        raise TypeError("application must be RelayApplication")

    class RelayHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def __getattr__(self, name: str):
            if name.startswith("do_"):
                return self._handle
            raise AttributeError(name)

        def _handle(self) -> None:
            started = time.monotonic()
            route_id = _route_id(application.mode, self.path)
            response = _fixed_error(500, "relay_internal_error", "protocol_failure")
            write_result = "failed"
            try:
                headers = _header_tuples(self.headers)
                try:
                    public_path, length = application.inspect(
                        method=self.command,
                        raw_path=self.path,
                        headers=headers,
                    )
                except RelayRequestError as exc:
                    self.close_connection = True
                    response = _fixed_error(exc.code, exc.reason, exc.upstream_class)
                else:
                    body = self.rfile.read(length) if length else b""
                    if len(body) != length:
                        response = _fixed_error(400, "relay_bad_request", "request_rejected")
                    else:
                        response = application.process(
                            method=self.command,
                            raw_path=self.path,
                            public_path=public_path,
                            headers=headers,
                            body=body,
                        )
                write_result = self._send_relay_response(response)
            except Exception:  # noqa: BLE001 - fixed fail-closed HTTP boundary
                self.close_connection = True
                response = _fixed_error(500, "relay_internal_error", "protocol_failure")
                try:
                    write_result = self._send_relay_response(response)
                except Exception:  # noqa: BLE001
                    write_result = "failed"
            finally:
                emit_fixed_log(
                    application.logger,
                    route_id=route_id,
                    status_family=_status_family(response.code),
                    upstream_class=response.upstream_class,
                    write_result=write_result,
                    latency_bucket=_latency_bucket(
                        (time.monotonic() - started) * 1000.0
                    ),
                )

        def _send_relay_response(self, response: RelayResponse) -> str:
            try:
                self.send_response(response.code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if self.close_connection:
                    self.send_header("Connection", "close")
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
                return "broken_pipe"
            except OSError:
                self.close_connection = True
                return "failed"
            if self.command == "HEAD":
                return "ok"
            result = write_body(self.wfile, response.body)
            if result != "ok":
                self.close_connection = True
            return result

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def do_TRACE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._handle()

        def log_message(self, *_args) -> None:
            pass

    return RelayHandler


def _read_key_file(path_text: str) -> bytes:
    path_text = _exact_str(path_text, field="key file")
    path = Path(path_text)
    if path.is_symlink() or not path.is_file():
        raise RelayConfigurationError("relay_key_unavailable")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RelayConfigurationError("relay_key_unavailable") from exc
    if len(raw) > 4096:
        raise RelayConfigurationError("relay_key_unavailable")
    value = raw.strip()
    if b"\n" in value or b"\r" in value:
        raise RelayConfigurationError("relay_key_unavailable")
    return _validate_key(value)


def _enabled(enable_flag: bool) -> bool:
    return type(enable_flag) is bool and enable_flag and os.environ.get(ENABLE_ENV) == "1"  # noqa: E721


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("edge", "origin"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18930)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--forbidden-key-sha256", action="append", default=[])
    parser.add_argument("--upstream-base", required=True)
    parser.add_argument("--replay-db", required=True)
    parser.add_argument("--relay-security-epoch", type=int, required=True)
    parser.add_argument("--auth-window-seconds", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--allow-http-loopback", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.umask(0o077)
    try:
        key = _read_key_file(args.key_file)
        digests = frozenset(args.forbidden_key_sha256)
        app = RelayApplication(
            mode=args.mode,
            enabled=_enabled(args.enable),
            key=key,
            forbidden_key_sha256=digests,
            upstream_base=args.upstream_base,
            replay_db_path=Path(args.replay_db),
            allow_http_loopback=args.allow_http_loopback,
            timeout_seconds=args.timeout_seconds,
            security_epoch=args.relay_security_epoch,
            auth_window_seconds=args.auth_window_seconds,
            initialize_replay_store=not args.preflight,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "relay_version": RELAY_VERSION,
                    "status": "blocked",
                    "reason": type(exc).__name__,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    if args.preflight:
        snapshot = app.preflight_snapshot()
        print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0 if (not app.enabled or snapshot["eligible"] is True) else 2
    if not app.enabled:
        emit_fixed_log(
            print,
            route_id="unknown",
            status_family="5xx",
            upstream_class="disabled",
            write_result="failed",
            latency_bucket="lt_100ms",
        )
        return 3
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
