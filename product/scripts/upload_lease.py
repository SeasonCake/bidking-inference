#!/usr/bin/env python3
"""Server-only signed short upload lease protocol.

Only opaque, server-derived identity aliases cross the wire.  Raw activation
tokens, UIDs, device fingerprints, URLs, addresses, and request bodies are not
part of this envelope.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any


SCHEMA_VERSION = 1
KIND = "sample_upload"
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900
_MAX_SAFE_EPOCH = 253_402_300_799
_HEX_16 = re.compile(r"^[0-9a-f]{16}$")
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SUBJECT_KINDS = frozenset({"paid", "trial"})
_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "subject_kind",
        "subject_id",
        "uid_hash",
        "device_alias",
        "security_epoch",
        "lease_id",
        "iat",
        "exp",
        "entitlement_exp",
    }
)


class UploadLeaseError(ValueError):
    """Fixed protocol failure without private source material."""


class UploadLeaseExpiredError(UploadLeaseError):
    """The signed envelope is authentic but no longer current."""


def signing_key(secret: bytes) -> bytes:
    if type(secret) is not bytes or len(secret) < 16:  # noqa: E721
        raise UploadLeaseError("invalid upload signing authority")
    return hmac.new(
        secret,
        b"bidking/upload-lease/signing/v1",
        hashlib.sha256,
    ).digest()


def opaque_subject_id(secret: bytes, *, subject_kind: str, authority_id: str) -> str:
    if type(subject_kind) is not str or subject_kind not in _SUBJECT_KINDS:  # noqa: E721
        raise UploadLeaseError("invalid upload subject kind")
    if type(authority_id) is not str or not authority_id:  # noqa: E721
        raise UploadLeaseError("invalid upload subject authority")
    return hmac.new(
        signing_key(secret),
        b"subject\x00" + subject_kind.encode("ascii") + b"\x00" + authority_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def opaque_device_alias(secret: bytes, device: str) -> str:
    if type(device) is not str or not device:  # noqa: E721
        raise UploadLeaseError("invalid upload device authority")
    return hmac.new(
        signing_key(secret),
        b"device\x00" + device.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def paid_uid_hash(uid: str) -> str:
    if type(uid) is not str or not uid:  # noqa: E721
        raise UploadLeaseError("invalid upload paid authority")
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:16]


def sign(payload: dict[str, Any], secret: bytes) -> str:
    stable = validate_payload(payload)
    encoded = json.dumps(
        stable,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    signature = hmac.new(signing_key(secret), encoded, hashlib.sha256).hexdigest()
    return encoded.decode("ascii") + "." + signature


def verify(envelope: Any, secret: bytes, *, now: int) -> dict[str, Any]:
    now = _exact_epoch(now, label="server time")
    if type(envelope) is not str or envelope.count(".") != 1:  # noqa: E721
        raise UploadLeaseError("invalid upload lease")
    raw, supplied = envelope.rsplit(".", 1)
    if _HEX_64.fullmatch(supplied) is None:
        raise UploadLeaseError("invalid upload lease")
    try:
        encoded = raw.encode("ascii")
    except UnicodeEncodeError as exc:
        raise UploadLeaseError("invalid upload lease") from exc
    expected = hmac.new(signing_key(secret), encoded, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise UploadLeaseError("invalid upload lease")
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise UploadLeaseError("invalid upload lease") from exc
    stable = validate_payload(decoded)
    if stable["iat"] > now:
        raise UploadLeaseError("invalid upload lease time")
    if stable["exp"] <= now:
        raise UploadLeaseExpiredError("expired upload lease")
    if stable["exp"] - stable["iat"] > MAX_TTL_SECONDS:
        raise UploadLeaseError("invalid upload lease lifetime")
    entitlement_exp = stable["entitlement_exp"]
    if entitlement_exp is not None and entitlement_exp <= now:
        raise UploadLeaseExpiredError("expired upload entitlement")
    return stable


def validate_payload(value: Any) -> dict[str, Any]:
    stable = _exact_dict(value)
    if frozenset(stable) != _PAYLOAD_KEYS:
        raise UploadLeaseError("invalid upload lease fields")
    if stable["schema_version"] != SCHEMA_VERSION or stable["kind"] != KIND:
        raise UploadLeaseError("invalid upload lease schema")
    subject_kind = stable["subject_kind"]
    if type(subject_kind) is not str or subject_kind not in _SUBJECT_KINDS:  # noqa: E721
        raise UploadLeaseError("invalid upload subject kind")
    _exact_hex(stable["subject_id"], _HEX_64, label="subject id")
    _exact_hex(stable["device_alias"], _HEX_64, label="device alias")
    _exact_hex(stable["lease_id"], _HEX_32, label="lease id")
    uid_hash = stable["uid_hash"]
    if subject_kind == "paid":
        _exact_hex(uid_hash, _HEX_16, label="uid hash")
    elif uid_hash is not None:
        raise UploadLeaseError("trial upload lease has uid hash")
    epoch = stable["security_epoch"]
    if type(epoch) is not int or epoch <= 0:  # noqa: E721
        raise UploadLeaseError("invalid upload security epoch")
    iat = _exact_epoch(stable["iat"], label="lease issued time")
    exp = _exact_epoch(stable["exp"], label="lease expiry")
    if exp <= iat or exp - iat > MAX_TTL_SECONDS:
        raise UploadLeaseError("invalid upload lease lifetime")
    entitlement_exp = stable["entitlement_exp"]
    if entitlement_exp is not None:
        entitlement_exp = _exact_epoch(entitlement_exp, label="entitlement expiry")
        if entitlement_exp < exp:
            raise UploadLeaseError("upload lease exceeds entitlement")
    return dict(stable)


def _exact_dict(value: Any) -> dict[str, Any]:
    if type(value) is not dict:  # noqa: E721
        raise UploadLeaseError("invalid upload lease mapping")
    pairs = []
    for key, item in dict.items(value):
        if type(key) is not str:  # noqa: E721
            raise UploadLeaseError("invalid upload lease key")
        pairs.append((key, item))
    if len({key for key, _ in pairs}) != len(pairs):
        raise UploadLeaseError("duplicate upload lease key")
    return {key: item for key, item in pairs}


def _exact_hex(value: Any, pattern: re.Pattern[str], *, label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:  # noqa: E721
        raise UploadLeaseError(f"invalid upload {label}")
    return value


def _exact_epoch(value: Any, *, label: str) -> int:
    if type(value) is not int or value <= 0 or value > _MAX_SAFE_EPOCH:  # noqa: E721
        raise UploadLeaseError(f"invalid upload {label}")
    return value
