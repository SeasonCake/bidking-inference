"""Application-layer authenticated encryption for the emergency HTTP activation path.

This protocol is deliberately separate from the normal HTTPS endpoint generation.
HTTP is only a carrier: activation credentials, device identity, master keys and
leases are always inside an X25519 + HKDF-SHA256 + AES-256-GCM envelope.
"""
# ruff: noqa: E721 - protocol boundaries deliberately require exact builtins

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import stat
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


SCHEMA_VERSION = 1
CONFIG_FILE = "activation_http_e2ee.json"
ENDPOINT_ID = "endpoint_4"
ROUTE = "/activate-e2ee"
MAX_CONFIG_BYTES = 4096
MAX_ENVELOPE_BYTES = 65_536
MAX_CLOCK_SKEW_SECONDS = 120
REPLAY_TTL_SECONDS = 300
REPLAY_CAPACITY = 4096
_EXACT_PATH_TYPES = frozenset({Path, type(Path())})

_DISABLED_CONFIG_KEYS = frozenset({"schema_version", "enabled"})
_ENABLED_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "enabled",
        "url",
        "key_id",
        "server_public_key",
    }
)
_REQUEST_ENVELOPE_KEYS = frozenset(
    {"schema_version", "key_id", "client_public_key", "nonce", "ciphertext"}
)
_REQUEST_PLAINTEXT_KEYS = frozenset(
    {"schema_version", "issued_at", "request_id", "activation"}
)
_RESPONSE_ENVELOPE_KEYS = frozenset(
    {"schema_version", "key_id", "nonce", "ciphertext"}
)
_RESPONSE_PLAINTEXT_KEYS = frozenset(
    {"schema_version", "request_id", "status", "response", "server_time"}
)
_KEY_ID_RE = re.compile(r"x25519-[0-9a-f]{16}\Z", re.ASCII)
_PROTOCOL_INFO = b"bidking-activation-http-e2ee-v1\x00"
_REQUEST_AAD_PREFIX = b"bidking-activation-http-e2ee-request-v1\x00"
_RESPONSE_AAD_PREFIX = b"bidking-activation-http-e2ee-response-v1\x00"


class ActivationHttpE2EEError(ValueError):
    """Fixed public protocol failure; never include sensitive input in its text."""


@dataclass(frozen=True)
class ActivationHttpE2EEConfig:
    enabled: bool
    raw_sha256: str
    url: str | None = None
    key_id: str | None = None
    server_public_key: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ClientRequestContext:
    key_id: str
    request_id: bytes = field(repr=False)
    request_digest: bytes = field(repr=False)
    response_key: bytes = field(repr=False)


@dataclass(frozen=True)
class ServerRequestContext:
    key_id: str
    request_id: bytes = field(repr=False)
    request_digest: bytes = field(repr=False)
    response_key: bytes = field(repr=False)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:  # noqa: E721 - trust boundary
            raise ActivationHttpE2EEError("invalid object keys")
        result[key] = value
    return result


def _json_load_exact(raw: bytes, *, max_bytes: int) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > max_bytes:  # noqa: E721
        raise ActivationHttpE2EEError("invalid payload size")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ActivationHttpE2EEError("non-finite number")
            ),
        )
    except (UnicodeError, ValueError, ActivationHttpE2EEError, RecursionError) as exc:
        raise ActivationHttpE2EEError("invalid JSON") from exc
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ActivationHttpE2EEError("invalid JSON object")
    return value


def _json_bytes(value: dict[str, object]) -> bytes:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ActivationHttpE2EEError("invalid JSON object")
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ActivationHttpE2EEError("invalid JSON value") from exc


def _b64_encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64_decode(value: object, *, length: int) -> bytes:
    if type(value) is not str or not value or value != value.strip():  # noqa: E721
        raise ActivationHttpE2EEError("invalid encoded value")
    try:
        value.encode("ascii")
        decoded = base64.b64decode(value, validate=True)
    except (UnicodeError, ValueError) as exc:
        raise ActivationHttpE2EEError("invalid encoded value") from exc
    if len(decoded) != length or _b64_encode(decoded) != value:
        raise ActivationHttpE2EEError("invalid encoded value")
    return decoded


def _public_bytes(key: X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def key_id_for_public_key(public_key: bytes) -> str:
    if type(public_key) is not bytes or len(public_key) != 32:  # noqa: E721
        raise ActivationHttpE2EEError("invalid public key")
    return "x25519-" + hashlib.sha256(public_key).hexdigest()[:16]


def _require_usable_peer_public_key(public_key: bytes) -> None:
    """Reject low-order X25519 inputs while parsing the trusted config."""
    try:
        probe_private = X25519PrivateKey.from_private_bytes(b"B" * 32)
        shared = probe_private.exchange(
            X25519PublicKey.from_public_bytes(public_key)
        )
    except (TypeError, ValueError) as exc:
        raise ActivationHttpE2EEError("invalid public key") from exc
    if type(shared) is not bytes or len(shared) != 32:  # noqa: E721
        raise ActivationHttpE2EEError("invalid public key")


def _derive_keys(
    private_key: X25519PrivateKey,
    peer_public_key: bytes,
    *,
    key_id: str,
    client_public_key: bytes,
    server_public_key: bytes,
) -> tuple[bytes, bytes]:
    try:
        shared = private_key.exchange(X25519PublicKey.from_public_bytes(peer_public_key))
    except (TypeError, ValueError) as exc:
        raise ActivationHttpE2EEError("invalid peer key") from exc
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=hashlib.sha256(client_public_key + server_public_key).digest(),
        info=_PROTOCOL_INFO + key_id.encode("ascii"),
    ).derive(shared)
    return material[:32], material[32:]


def _request_aad(key_id: str, client_public_key: bytes, nonce: bytes) -> bytes:
    return (
        _REQUEST_AAD_PREFIX
        + key_id.encode("ascii")
        + b"\x00"
        + client_public_key
        + nonce
    )


def _response_aad(key_id: str, request_digest: bytes, nonce: bytes) -> bytes:
    return (
        _RESPONSE_AAD_PREFIX
        + key_id.encode("ascii")
        + b"\x00"
        + request_digest
        + nonce
    )


def _strict_public_http_url(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():  # noqa: E721
        raise ActivationHttpE2EEError("invalid emergency URL")
    try:
        value.encode("ascii")
        parsed = urlsplit(value)
        port = parsed.port
        address = ipaddress.ip_address(parsed.hostname or "")
    except (UnicodeError, ValueError) as exc:
        raise ActivationHttpE2EEError("invalid emergency URL") from exc
    if (
        parsed.scheme != "http"
        or not isinstance(address, ipaddress.IPv4Address)
        or not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.netloc != f"{address.compressed}:{port}"
        or parsed.path != ROUTE
        or parsed.query
        or parsed.fragment
    ):
        raise ActivationHttpE2EEError("invalid emergency URL")
    return value


def parse_config_bytes(raw: bytes) -> ActivationHttpE2EEConfig:
    value = _json_load_exact(raw, max_bytes=MAX_CONFIG_BYTES)
    keys = frozenset(value)
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != SCHEMA_VERSION
        or type(value.get("enabled")) is not bool
    ):
        raise ActivationHttpE2EEError("invalid config root")
    enabled = value["enabled"]
    if enabled is False:
        if keys != _DISABLED_CONFIG_KEYS:
            raise ActivationHttpE2EEError("invalid disabled config")
        return ActivationHttpE2EEConfig(
            enabled=False,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
        )
    if keys != _ENABLED_CONFIG_KEYS:
        raise ActivationHttpE2EEError("invalid enabled config")
    key_id = value.get("key_id")
    if type(key_id) is not str or _KEY_ID_RE.fullmatch(key_id) is None:  # noqa: E721
        raise ActivationHttpE2EEError("invalid key id")
    public_key = _b64_decode(value.get("server_public_key"), length=32)
    if key_id_for_public_key(public_key) != key_id:
        raise ActivationHttpE2EEError("key identity mismatch")
    _require_usable_peer_public_key(public_key)
    return ActivationHttpE2EEConfig(
        enabled=True,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        url=_strict_public_http_url(value.get("url")),
        key_id=key_id,
        server_public_key=public_key,
    )


def _read_regular_file(path: Path, *, max_bytes: int, private: bool = False) -> bytes:
    if type(path) not in _EXACT_PATH_TYPES:
        raise ActivationHttpE2EEError("invalid path")
    try:
        before = path.lstat()
    except OSError as exc:
        raise ActivationHttpE2EEError("file unavailable") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > max_bytes
        or (private and os.name == "posix" and stat.S_IMODE(before.st_mode) & 0o077)
    ):
        raise ActivationHttpE2EEError("unsafe file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ActivationHttpE2EEError("file unavailable") from exc
    try:
        opened = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != identity
            or opened.st_size != before.st_size
            or (private and os.name == "posix" and stat.S_IMODE(opened.st_mode) & 0o077)
        ):
            raise ActivationHttpE2EEError("file identity changed")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                raise ActivationHttpE2EEError("file was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ActivationHttpE2EEError("file grew while reading")
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != identity
            or after.st_size != opened.st_size
            or getattr(after, "st_mtime_ns", None) != getattr(opened, "st_mtime_ns", None)
        ):
            raise ActivationHttpE2EEError("file changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def config_path() -> Path:
    return Path(__file__).resolve().parents[3] / CONFIG_FILE


def load_config(path: Path | None = None) -> ActivationHttpE2EEConfig | None:
    candidate = config_path() if path is None else path
    try:
        raw = _read_regular_file(candidate, max_bytes=MAX_CONFIG_BYTES)
    except ActivationHttpE2EEError:
        if path is None:
            try:
                candidate.lstat()
            except FileNotFoundError:
                return None
            except OSError:
                pass
        raise
    return parse_config_bytes(raw)


def load_server_private_key(path: Path) -> tuple[X25519PrivateKey, str, bytes]:
    raw = _read_regular_file(path, max_bytes=256, private=True)
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise ActivationHttpE2EEError("invalid private key") from exc
    private_bytes = _b64_decode(text.strip(), length=32)
    if text not in {text.strip(), text.strip() + "\n", text.strip() + "\r\n"}:
        raise ActivationHttpE2EEError("invalid private key")
    try:
        private_key = X25519PrivateKey.from_private_bytes(private_bytes)
    except (TypeError, ValueError) as exc:
        raise ActivationHttpE2EEError("invalid private key") from exc
    public_key = _public_bytes(private_key.public_key())
    return private_key, key_id_for_public_key(public_key), public_key


def build_client_request(
    config: ActivationHttpE2EEConfig,
    activation: dict[str, object],
    *,
    now: int | None = None,
    random_bytes: Callable[[int], bytes] = os.urandom,
) -> tuple[bytes, ClientRequestContext]:
    if (
        type(config) is not ActivationHttpE2EEConfig
        or config.enabled is not True
        or type(config.key_id) is not str
        or type(config.server_public_key) is not bytes
        or type(activation) is not dict
        or any(type(key) is not str for key in activation)
    ):
        raise ActivationHttpE2EEError("invalid client request")
    issued_at = int(time.time()) if now is None else now
    if type(issued_at) is not int or issued_at < 0:  # noqa: E721
        raise ActivationHttpE2EEError("invalid request time")
    request_id = random_bytes(16)
    nonce = random_bytes(12)
    if type(request_id) is not bytes or len(request_id) != 16:  # noqa: E721
        raise ActivationHttpE2EEError("invalid request randomness")
    if type(nonce) is not bytes or len(nonce) != 12:  # noqa: E721
        raise ActivationHttpE2EEError("invalid request randomness")
    client_private = X25519PrivateKey.generate()
    client_public = _public_bytes(client_private.public_key())
    request_key, response_key = _derive_keys(
        client_private,
        config.server_public_key,
        key_id=config.key_id,
        client_public_key=client_public,
        server_public_key=config.server_public_key,
    )
    plaintext = _json_bytes(
        {
            "schema_version": SCHEMA_VERSION,
            "issued_at": issued_at,
            "request_id": _b64_encode(request_id),
            "activation": activation,
        }
    )
    ciphertext = AESGCM(request_key).encrypt(
        nonce,
        plaintext,
        _request_aad(config.key_id, client_public, nonce),
    )
    envelope = _json_bytes(
        {
            "schema_version": SCHEMA_VERSION,
            "key_id": config.key_id,
            "client_public_key": _b64_encode(client_public),
            "nonce": _b64_encode(nonce),
            "ciphertext": _b64_encode(ciphertext),
        }
    )
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise ActivationHttpE2EEError("request envelope too large")
    return envelope, ClientRequestContext(
        key_id=config.key_id,
        request_id=request_id,
        request_digest=hashlib.sha256(envelope).digest(),
        response_key=response_key,
    )


def open_server_request(
    raw: bytes,
    private_key: X25519PrivateKey,
    *,
    key_id: str,
    public_key: bytes,
    now: int | None = None,
) -> tuple[dict[str, object], ServerRequestContext]:
    if (
        not isinstance(private_key, X25519PrivateKey)
        or type(key_id) is not str
        or _KEY_ID_RE.fullmatch(key_id) is None
        or type(public_key) is not bytes
        or len(public_key) != 32
        or key_id_for_public_key(public_key) != key_id
    ):
        raise ActivationHttpE2EEError("invalid server key")
    envelope = _json_load_exact(raw, max_bytes=MAX_ENVELOPE_BYTES)
    if (
        frozenset(envelope) != _REQUEST_ENVELOPE_KEYS
        or type(envelope.get("schema_version")) is not int
        or envelope.get("schema_version") != SCHEMA_VERSION
        or envelope.get("key_id") != key_id
    ):
        raise ActivationHttpE2EEError("invalid request envelope")
    client_public = _b64_decode(envelope.get("client_public_key"), length=32)
    nonce = _b64_decode(envelope.get("nonce"), length=12)
    ciphertext_value = envelope.get("ciphertext")
    if type(ciphertext_value) is not str:  # noqa: E721
        raise ActivationHttpE2EEError("invalid request envelope")
    try:
        ciphertext = base64.b64decode(ciphertext_value, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ActivationHttpE2EEError("invalid request envelope") from exc
    if (
        not 16 <= len(ciphertext) <= MAX_ENVELOPE_BYTES
        or _b64_encode(ciphertext) != ciphertext_value
    ):
        raise ActivationHttpE2EEError("invalid request envelope")
    request_key, response_key = _derive_keys(
        private_key,
        client_public,
        key_id=key_id,
        client_public_key=client_public,
        server_public_key=public_key,
    )
    try:
        plaintext_raw = AESGCM(request_key).decrypt(
            nonce,
            ciphertext,
            _request_aad(key_id, client_public, nonce),
        )
    except (InvalidTag, ValueError) as exc:
        raise ActivationHttpE2EEError("request authentication failed") from exc
    plaintext = _json_load_exact(plaintext_raw, max_bytes=MAX_ENVELOPE_BYTES)
    if (
        frozenset(plaintext) != _REQUEST_PLAINTEXT_KEYS
        or type(plaintext.get("schema_version")) is not int
        or plaintext.get("schema_version") != SCHEMA_VERSION
        or type(plaintext.get("issued_at")) is not int
        or type(plaintext.get("activation")) is not dict
        or any(type(key) is not str for key in plaintext["activation"])
    ):
        raise ActivationHttpE2EEError("invalid request plaintext")
    request_id = _b64_decode(plaintext.get("request_id"), length=16)
    current = int(time.time()) if now is None else now
    issued_at = plaintext["issued_at"]
    if (
        type(current) is not int
        or current < 0
        or issued_at < 0
        or abs(current - issued_at) > MAX_CLOCK_SKEW_SECONDS
    ):
        raise ActivationHttpE2EEError("request time outside window")
    return dict(plaintext["activation"]), ServerRequestContext(
        key_id=key_id,
        request_id=request_id,
        request_digest=hashlib.sha256(raw).digest(),
        response_key=response_key,
    )


def build_server_response(
    context: ServerRequestContext,
    status: int,
    response: dict[str, object],
    *,
    now: int | None = None,
    random_bytes: Callable[[int], bytes] = os.urandom,
) -> bytes:
    if (
        type(context) is not ServerRequestContext
        or type(status) is not int
        or not 100 <= status <= 599
        or type(response) is not dict
        or any(type(key) is not str for key in response)
    ):
        raise ActivationHttpE2EEError("invalid server response")
    server_time = int(time.time()) if now is None else now
    if type(server_time) is not int or server_time < 0:  # noqa: E721
        raise ActivationHttpE2EEError("invalid server time")
    nonce = random_bytes(12)
    if type(nonce) is not bytes or len(nonce) != 12:  # noqa: E721
        raise ActivationHttpE2EEError("invalid response randomness")
    plaintext = _json_bytes(
        {
            "schema_version": SCHEMA_VERSION,
            "request_id": _b64_encode(context.request_id),
            "status": status,
            "response": response,
            "server_time": server_time,
        }
    )
    ciphertext = AESGCM(context.response_key).encrypt(
        nonce,
        plaintext,
        _response_aad(context.key_id, context.request_digest, nonce),
    )
    envelope = _json_bytes(
        {
            "schema_version": SCHEMA_VERSION,
            "key_id": context.key_id,
            "nonce": _b64_encode(nonce),
            "ciphertext": _b64_encode(ciphertext),
        }
    )
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise ActivationHttpE2EEError("response envelope too large")
    return envelope


def open_client_response(
    raw: bytes,
    context: ClientRequestContext,
    *,
    now: int | None = None,
) -> tuple[int, dict[str, object]]:
    if type(context) is not ClientRequestContext:
        raise ActivationHttpE2EEError("invalid client context")
    envelope = _json_load_exact(raw, max_bytes=MAX_ENVELOPE_BYTES)
    if (
        frozenset(envelope) != _RESPONSE_ENVELOPE_KEYS
        or type(envelope.get("schema_version")) is not int
        or envelope.get("schema_version") != SCHEMA_VERSION
        or envelope.get("key_id") != context.key_id
    ):
        raise ActivationHttpE2EEError("invalid response envelope")
    nonce = _b64_decode(envelope.get("nonce"), length=12)
    ciphertext_value = envelope.get("ciphertext")
    if type(ciphertext_value) is not str:  # noqa: E721
        raise ActivationHttpE2EEError("invalid response envelope")
    try:
        ciphertext = base64.b64decode(ciphertext_value, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ActivationHttpE2EEError("invalid response envelope") from exc
    if (
        not 16 <= len(ciphertext) <= MAX_ENVELOPE_BYTES
        or _b64_encode(ciphertext) != ciphertext_value
    ):
        raise ActivationHttpE2EEError("invalid response envelope")
    try:
        plaintext_raw = AESGCM(context.response_key).decrypt(
            nonce,
            ciphertext,
            _response_aad(context.key_id, context.request_digest, nonce),
        )
    except (InvalidTag, ValueError) as exc:
        raise ActivationHttpE2EEError("response authentication failed") from exc
    plaintext = _json_load_exact(plaintext_raw, max_bytes=MAX_ENVELOPE_BYTES)
    if (
        frozenset(plaintext) != _RESPONSE_PLAINTEXT_KEYS
        or type(plaintext.get("schema_version")) is not int
        or plaintext.get("schema_version") != SCHEMA_VERSION
        or type(plaintext.get("status")) is not int
        or not 100 <= plaintext["status"] <= 599
        or type(plaintext.get("response")) is not dict
        or any(type(key) is not str for key in plaintext["response"])
        or _b64_decode(plaintext.get("request_id"), length=16) != context.request_id
        or type(plaintext.get("server_time")) is not int
    ):
        raise ActivationHttpE2EEError("invalid response plaintext")
    current = int(time.time()) if now is None else now
    if (
        type(current) is not int
        or current < 0
        or plaintext["server_time"] < 0
        or abs(current - plaintext["server_time"]) > MAX_CLOCK_SKEW_SECONDS
    ):
        raise ActivationHttpE2EEError("response time outside window")
    return plaintext["status"], dict(plaintext["response"])


class ReplayGuard:
    """Bounded in-process replay gate; freshness also limits restart exposure."""

    def __init__(self, *, capacity: int = REPLAY_CAPACITY) -> None:
        if type(capacity) is not int or not 1 <= capacity <= REPLAY_CAPACITY:
            raise ActivationHttpE2EEError("invalid replay capacity")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._seen: OrderedDict[bytes, int] = OrderedDict()

    def claim(self, request_digest: bytes, *, now: int | None = None) -> bool:
        if type(request_digest) is not bytes or len(request_digest) != 32:  # noqa: E721
            return False
        current = int(time.time()) if now is None else now
        if type(current) is not int or current < 0:  # noqa: E721
            return False
        with self._lock:
            expired = [key for key, expiry in self._seen.items() if expiry < current]
            for key in expired:
                self._seen.pop(key, None)
            if request_digest in self._seen:
                return False
            self._seen[request_digest] = current + REPLAY_TTL_SECONDS
            self._seen.move_to_end(request_digest)
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
            return True


class ServerProtocol:
    def __init__(self, private_key: X25519PrivateKey, key_id: str, public_key: bytes):
        if (
            not isinstance(private_key, X25519PrivateKey)
            or type(key_id) is not str
            or type(public_key) is not bytes
            or key_id_for_public_key(public_key) != key_id
        ):
            raise ActivationHttpE2EEError("invalid server protocol key")
        self._private_key = private_key
        self.key_id = key_id
        self.public_key = bytes(public_key)
        self._replay = ReplayGuard()

    @classmethod
    def from_key_file(cls, path: Path) -> "ServerProtocol":
        return cls(*load_server_private_key(path))

    def open_request(
        self,
        raw: bytes,
        *,
        now: int | None = None,
    ) -> tuple[dict[str, object], ServerRequestContext]:
        activation, context = open_server_request(
            raw,
            self._private_key,
            key_id=self.key_id,
            public_key=self.public_key,
            now=now,
        )
        if not self._replay.claim(context.request_digest, now=now):
            raise ActivationHttpE2EEError("request replayed")
        return activation, context
