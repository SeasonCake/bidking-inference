#!/usr/bin/env python3
"""Offline hotfix5 rollback-object preparation and read-only verification.

No subcommand uploads an object, switches a manifest, restarts a service, or opens a
production control channel.  ``verify-object`` performs anonymous public HTTPS reads;
the other subcommands are local-only.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat
from typing import Callable
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile


SCHEMA_VERSION = 1
SEED_VERSION = "0.3.1-hotfix5"
SEED_BYTES = 98_639_078
SEED_SHA256 = "f52c3e50540047f275738f65eb62161e64a755cbe734e3463046f229b5c536cb"
SEED_MEMBER_COUNT = 3_315
HISTORICAL_URL = (
    "https://downloads.bidking.example.invalid/"
    "bidking-live-v0.3.1-hotfix5-encrypted.zip"
)
HISTORICAL_MANIFEST_SHA256 = (
    "5aba98759d9947ebe77ac02cc8b53427bddd7da6dedbf80d645bef4310adf7bb"
)
MAX_JSON_BYTES = 128 * 1024
MAX_MANIFEST_BYTES = 32 * 1024
USER_AGENT = "BidKing-Rollback-Object-Verifier/1"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_OP_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,95}$", re.ASCII)
_HOST_RE = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.ASCII,
)
_PREFIX_RE = re.compile(r"^[a-z0-9](?:[a-z0-9/_-]{0,126}[a-z0-9])?/?\Z", re.ASCII)
_KEY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9/_.-]{0,254}[a-z0-9])?\Z", re.ASCII)
_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)$", re.ASCII)
_WINDOWS_RESERVED_COMPONENTS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
    | {
        f"{prefix}{superscript}"
        for prefix in ("com", "lpt")
        for superscript in "\u00b9\u00b2\u00b3"
    }
)
_WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')
_INPUT_KEYS = frozenset(
    {
        "schema_version",
        "operation_id",
        "observed_at",
        "public_base",
        "object_prefix",
        "expected_current_manifest_sha256",
        "rollback_manifest",
    }
)
_MANIFEST_KEYS = frozenset({"latest", "min", "url", "notice", "game_version"})
_ROLLBACK_FIELDS = frozenset({"latest", "min", "notice", "game_version"})
_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "operation_id",
        "observed_at",
        "seed",
        "historical_public_object",
        "immutable_object",
        "expected_current_manifest_sha256",
        "rollback_manifest_sha256",
        "historical_manifest_sha256",
        "switch_contract",
        "future_gates",
        "not_run",
    }
)
_SEED_KEYS = frozenset(
    {"version", "bytes", "sha256", "members", "duplicates", "crc", "range_0_0_sha256"}
)
_HISTORICAL_KEYS = frozenset({"url", "lifecycle"})
_OBJECT_KEYS = frozenset({"key", "url", "bytes", "sha256", "verification_arms"})
_SWITCH_KEYS = frozenset(
    {"cas_required", "backup_required", "exclusive_lock_required", "atomic_replace_required"}
)
_FUTURE_GATE_KEYS = frozenset(
    {
        "historical_key_overwrite_forbidden",
        "anonymous_full_get_exact_readback_required",
        "root_backup_live_check_required",
    }
)
_NOT_RUN_KEYS = frozenset(
    {
        "upload",
        "anonymous_object_readback",
        "root_backup_live_check",
        "manifest_switch",
        "restart",
        "deploy",
        "production",
        "build",
        "e07",
    }
)
_OBJECT_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "operation_id",
        "url",
        "bytes",
        "sha256",
        "head_status",
        "range_status",
        "full_get_status",
        "range_0_0_verified",
        "anonymous_requests",
        "redirects_followed",
    }
)


class RollbackPreparationError(ValueError):
    """A fixed, privacy-safe preparation failure."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        return None


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:  # noqa: E721
            raise RollbackPreparationError("json_object_keys_invalid")
        result[key] = value
    return result


def _load_json(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                RollbackPreparationError("json_non_finite")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RollbackPreparationError("json_invalid") from exc
    if type(value) is not dict:  # noqa: E721
        raise RollbackPreparationError("json_root_invalid")
    return value


def _keys(value: object, expected: frozenset[str], reason: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != expected:  # noqa: E721
        raise RollbackPreparationError(reason)
    return value


def _text(
    value: object,
    reason: str,
    *,
    ascii_only: bool = True,
    pattern: re.Pattern[str] | None = None,
    maximum: int = 512,
) -> str:
    if (
        type(value) is not str  # noqa: E721
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
        or "\r" in value
    ):
        raise RollbackPreparationError(reason)
    if ascii_only:
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise RollbackPreparationError(reason) from exc
    if pattern is not None and pattern.fullmatch(value) is None:
        raise RollbackPreparationError(reason)
    return value


def _digest(value: object, reason: str) -> str:
    return _text(value, reason, pattern=_DIGEST_RE, maximum=64)


def _timestamp(value: object, reason: str) -> str:
    text = _text(value, reason, maximum=40)
    if not text.endswith("Z"):
        raise RollbackPreparationError(reason)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise RollbackPreparationError(reason) from exc
    if parsed.isoformat().replace("+00:00", "Z") != text:
        raise RollbackPreparationError(reason)
    return text


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular_snapshot(
    path: Path,
    *,
    maximum: int | None = None,
) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RollbackPreparationError("input_path_unsafe")
    if before.st_size <= 0 or (maximum is not None and before.st_size > maximum):
        raise RollbackPreparationError("input_size_invalid")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size != before.st_size
        ):
            raise RollbackPreparationError("input_identity_changed")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise RollbackPreparationError("input_truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RollbackPreparationError("input_grew")
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            raise RollbackPreparationError("input_changed_during_read")
        final = path.lstat()
        if (
            stat.S_ISLNK(final.st_mode)
            or not stat.S_ISREG(final.st_mode)
            or (final.st_dev, final.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise RollbackPreparationError("input_path_replaced")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def _read_regular(path: Path, *, maximum: int | None = None) -> bytes:
    raw, _info = _read_regular_snapshot(path, maximum=maximum)
    return raw


def _public_base(value: object) -> str:
    text = _text(value, "public_base_invalid", maximum=1024)
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RollbackPreparationError("public_base_invalid") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or type(host) is not str  # noqa: E721
        or host != host.lower()
        or _HOST_RE.fullmatch(host) is None
        or parsed.netloc != host
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RollbackPreparationError("public_base_invalid")
    return urlunsplit(("https", host, "", "", ""))


def _object_url(value: object, *, expected_key: str | None = None) -> str:
    text = _text(value, "public_object_url_invalid", maximum=2_048)
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RollbackPreparationError("public_object_url_invalid") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or type(host) is not str  # noqa: E721
        or host != host.lower()
        or _HOST_RE.fullmatch(host) is None
        or parsed.netloc != host
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path in {"", "/"}
    ):
        raise RollbackPreparationError("public_object_url_invalid")
    if expected_key is not None and text != f"https://{host}/" + quote(expected_key, safe="/-_."):
        raise RollbackPreparationError("public_object_url_invalid")
    return text


def _object_key(value: object) -> str:
    key = _text(value, "public_object_key_invalid", pattern=_KEY_RE, maximum=256)
    if (
        key.startswith("/")
        or "//" in key
        or any(part in {"", ".", ".."} for part in key.split("/"))
        or not key.endswith(
            "bidking-live-v0.3.1-hotfix5-encrypted-" + SEED_SHA256[:16] + ".zip"
        )
    ):
        raise RollbackPreparationError("public_object_key_invalid")
    return key


def _manifest_fields(value: object) -> dict[str, str]:
    item = _keys(value, _ROLLBACK_FIELDS, "rollback_manifest_shape_invalid")
    latest = _text(item["latest"], "rollback_latest_invalid", maximum=64)
    minimum = _text(item["min"], "rollback_min_invalid", maximum=64)
    notice = _text(
        item["notice"],
        "rollback_notice_invalid",
        ascii_only=False,
        maximum=1_000,
    )
    game_version = item["game_version"]
    if type(game_version) is not str or len(game_version) > 128 or "\x00" in game_version:  # noqa: E721
        raise RollbackPreparationError("rollback_game_version_invalid")
    if latest != SEED_VERSION or minimum != "0.3.1":
        raise RollbackPreparationError("rollback_version_contract_invalid")
    return {
        "latest": latest,
        "min": minimum,
        "notice": notice,
        "game_version": game_version,
    }


def _zip_member_identity(info: zipfile.ZipInfo) -> tuple[tuple[str, ...], bool]:
    name = info.orig_filename
    if (
        not name
        or name != info.filename
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name, re.ASCII) is not None
    ):
        raise RollbackPreparationError("seed_member_path_unsafe")

    is_directory = info.is_dir()
    path = name[:-1] if is_directory else name
    if not path:
        raise RollbackPreparationError("seed_member_path_unsafe")
    components = path.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise RollbackPreparationError("seed_member_path_unsafe")

    canonical: list[str] = []
    for component in components:
        normalized = unicodedata.normalize("NFC", component)
        folded = normalized.casefold()
        reserved_stem = folded.split(".", 1)[0]
        if (
            normalized.endswith((".", " "))
            or any(ord(character) < 32 for character in normalized)
            or any(character in _WINDOWS_FORBIDDEN_CHARS for character in normalized)
            or reserved_stem in _WINDOWS_RESERVED_COMPONENTS
        ):
            raise RollbackPreparationError("seed_member_path_unsafe")
        canonical.append(folded)

    unix_type = stat.S_IFMT(info.external_attr >> 16)
    if unix_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise RollbackPreparationError("seed_member_type_unsafe")
    if is_directory and unix_type not in {0, stat.S_IFDIR}:
        raise RollbackPreparationError("seed_member_type_unsafe")
    if not is_directory and unix_type not in {0, stat.S_IFREG}:
        raise RollbackPreparationError("seed_member_type_unsafe")
    if not is_directory and info.external_attr & 0x10:
        raise RollbackPreparationError("seed_member_type_unsafe")
    if info.external_attr & 0x08:
        raise RollbackPreparationError("seed_member_type_unsafe")
    return tuple(canonical), is_directory


def _verify_zip_members(infos: list[zipfile.ZipInfo]) -> None:
    identities = [_zip_member_identity(info) for info in infos]
    names = [info.filename for info in infos]
    if len(set(names)) != len(names):
        raise RollbackPreparationError("seed_duplicate_member")

    explicit: dict[tuple[str, ...], bool] = {}
    files: set[tuple[str, ...]] = set()
    for identity, is_directory in identities:
        if identity in explicit:
            raise RollbackPreparationError("seed_member_alias_collision")
        if any(identity[:end] in files for end in range(1, len(identity))):
            raise RollbackPreparationError("seed_member_alias_collision")
        if not is_directory and any(
            len(existing) > len(identity) and existing[: len(identity)] == identity
            for existing in explicit
        ):
            raise RollbackPreparationError("seed_member_alias_collision")
        explicit[identity] = is_directory
        if not is_directory:
            files.add(identity)


def _verify_seed(path: Path) -> dict[str, object]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RollbackPreparationError("seed_path_unsafe")
    if before.st_size != SEED_BYTES:
        raise RollbackPreparationError("seed_identity_mismatch")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size != SEED_BYTES
            ):
                raise RollbackPreparationError("seed_identity_changed")
            digest = hashlib.sha256()
            first = b""
            total = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if not first:
                    first = chunk[:1]
                digest.update(chunk)
                total += len(chunk)
            if total != SEED_BYTES or digest.hexdigest() != SEED_SHA256:
                raise RollbackPreparationError("seed_identity_mismatch")
            stream.seek(0)
            try:
                with zipfile.ZipFile(stream) as archive:
                    infos = archive.infolist()
                    if len(infos) != SEED_MEMBER_COUNT:
                        raise RollbackPreparationError("seed_member_count_mismatch")
                    _verify_zip_members(infos)
                    if archive.testzip() is not None:
                        raise RollbackPreparationError("seed_crc_mismatch")
            except (zipfile.BadZipFile, RuntimeError) as exc:
                raise RollbackPreparationError("seed_zip_invalid") from exc
            after = os.fstat(stream.fileno())
            final = path.lstat()
            if (
                (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
                or getattr(after, "st_ctime_ns", None)
                != getattr(opened, "st_ctime_ns", None)
                or stat.S_ISLNK(final.st_mode)
                or not stat.S_ISREG(final.st_mode)
                or (final.st_dev, final.st_ino) != (after.st_dev, after.st_ino)
            ):
                raise RollbackPreparationError("seed_changed_during_verification")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return {
        "version": SEED_VERSION,
        "bytes": SEED_BYTES,
        "sha256": SEED_SHA256,
        "members": SEED_MEMBER_COUNT,
        "duplicates": 0,
        "crc": "pass",
        "range_0_0_sha256": _sha(first),
    }


def _write_outputs(output_dir: Path, outputs: dict[str, bytes]) -> None:
    if output_dir.exists():
        raise RollbackPreparationError("output_directory_exists")
    output_dir.mkdir(mode=0o700, parents=False)
    try:
        for name, raw in outputs.items():
            descriptor = os.open(
                output_dir / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                view = memoryview(raw)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        if os.name == "posix":
            descriptor = os.open(output_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except BaseException:
        for name in outputs:
            try:
                (output_dir / name).unlink()
            except FileNotFoundError:
                pass
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise


def _prepare(args: argparse.Namespace) -> dict[str, object]:
    seed = _verify_seed(args.seed)
    input_raw = _read_regular(args.input, maximum=MAX_JSON_BYTES)
    envelope = _keys(
        _load_json(input_raw),
        _INPUT_KEYS,
        "input_envelope_shape_invalid",
    )
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:  # noqa: E721
        raise RollbackPreparationError("input_schema_invalid")
    operation_id = _text(envelope["operation_id"], "operation_id_invalid", pattern=_OP_RE)
    observed_at = _timestamp(envelope["observed_at"], "observed_at_invalid")
    public_base = _public_base(envelope["public_base"])
    prefix = _text(envelope["object_prefix"], "object_prefix_invalid", pattern=_PREFIX_RE)
    if prefix.startswith("/") or "//" in prefix or any(part in {"", ".", ".."} for part in prefix.rstrip("/").split("/")):
        raise RollbackPreparationError("object_prefix_invalid")
    prefix = prefix.rstrip("/") + "/"
    current_sha = _digest(
        envelope["expected_current_manifest_sha256"],
        "current_manifest_sha256_invalid",
    )
    fields = _manifest_fields(envelope["rollback_manifest"])
    key = (
        prefix
        + "bidking-live-v0.3.1-hotfix5-encrypted-"
        + SEED_SHA256[:16]
        + ".zip"
    )
    object_url = public_base + "/" + quote(key, safe="/-_.")
    if object_url == HISTORICAL_URL:
        raise RollbackPreparationError("immutable_key_not_new")
    manifest = {**fields, "url": object_url}
    manifest_raw = _canonical(manifest)
    plan = {
        "schema_version": 1,
        "status": "offline_candidate_ready_upload_pending",
        "operation_id": operation_id,
        "observed_at": observed_at,
        "seed": seed,
        "historical_public_object": {
            "url": HISTORICAL_URL,
            "lifecycle": "historical_identity_public_unavailable",
        },
        "immutable_object": {
            "key": key,
            "url": object_url,
            "bytes": SEED_BYTES,
            "sha256": SEED_SHA256,
            "verification_arms": ["anonymous_head", "anonymous_range_0_0", "anonymous_full_get"],
        },
        "expected_current_manifest_sha256": current_sha,
        "rollback_manifest_sha256": _sha(manifest_raw),
        "historical_manifest_sha256": HISTORICAL_MANIFEST_SHA256,
        "switch_contract": {
            "cas_required": True,
            "backup_required": True,
            "exclusive_lock_required": True,
            "atomic_replace_required": True,
        },
        "future_gates": {
            "historical_key_overwrite_forbidden": True,
            "anonymous_full_get_exact_readback_required": True,
            "root_backup_live_check_required": True,
        },
        "not_run": {key: True for key in sorted(_NOT_RUN_KEYS)},
    }
    plan_raw = _canonical(plan)
    receipt = {
        "schema_version": 1,
        "status": "offline_prepack_verified",
        "operation_id": operation_id,
        "observed_at": observed_at,
        "input_sha256": _sha(input_raw),
        "plan_sha256": _sha(plan_raw),
        "rollback_manifest_sha256": _sha(manifest_raw),
        "seed": seed,
        "historical_public_object": plan["historical_public_object"],
        "immutable_object": plan["immutable_object"],
        "future_gates": plan["future_gates"],
        "secret_material_included": False,
        "production_bucket_credentials_included": False,
        "production_paths_included": False,
        "not_run": plan["not_run"],
    }
    receipt_raw = _canonical(receipt)
    _write_outputs(
        args.output_dir,
        {
            "hotfix5.immutable-object-plan.json": plan_raw,
            "version_manifest.hotfix5-rollback.candidate.json": manifest_raw,
            "hotfix5.offline-prepack-receipt.json": receipt_raw,
        },
    )
    return {
        "status": plan["status"],
        "operation_id": operation_id,
        "plan_sha256": _sha(plan_raw),
        "rollback_manifest_sha256": _sha(manifest_raw),
        "offline_prepack_receipt_sha256": _sha(receipt_raw),
        "immutable_object_key": key,
        "seed_sha256": SEED_SHA256,
        "upload_not_run": True,
        "manifest_switch_not_run": True,
    }


def _validate_plan(value: object) -> dict[str, object]:
    plan = _keys(value, _PLAN_KEYS, "plan_shape_invalid")
    if type(plan["schema_version"]) is not int or plan["schema_version"] != 1:  # noqa: E721
        raise RollbackPreparationError("plan_schema_invalid")
    if plan["status"] != "offline_candidate_ready_upload_pending":
        raise RollbackPreparationError("plan_status_invalid")
    _text(plan["operation_id"], "plan_operation_invalid", pattern=_OP_RE)
    _timestamp(plan["observed_at"], "plan_observed_at_invalid")
    seed = _keys(plan["seed"], _SEED_KEYS, "plan_seed_invalid")
    expected_seed = {
        "version": SEED_VERSION,
        "bytes": SEED_BYTES,
        "sha256": SEED_SHA256,
        "members": SEED_MEMBER_COUNT,
        "duplicates": 0,
        "crc": "pass",
    }
    if any(type(seed.get(key)) is not type(value) or seed.get(key) != value for key, value in expected_seed.items()):
        raise RollbackPreparationError("plan_seed_invalid")
    _digest(seed["range_0_0_sha256"], "plan_seed_invalid")
    historical = _keys(plan["historical_public_object"], _HISTORICAL_KEYS, "plan_historical_invalid")
    if historical != {
        "url": HISTORICAL_URL,
        "lifecycle": "historical_identity_public_unavailable",
    }:
        raise RollbackPreparationError("plan_historical_invalid")
    obj = _keys(plan["immutable_object"], _OBJECT_KEYS, "plan_object_invalid")
    if (
        type(obj["key"]) is not str  # noqa: E721
        or type(obj["url"]) is not str  # noqa: E721
        or obj["url"] == HISTORICAL_URL
        or obj["bytes"] != SEED_BYTES
        or obj["sha256"] != SEED_SHA256
        or obj["verification_arms"]
        != ["anonymous_head", "anonymous_range_0_0", "anonymous_full_get"]
    ):
        raise RollbackPreparationError("plan_object_invalid")
    key = _object_key(obj["key"])
    _object_url(obj["url"], expected_key=key)
    _digest(plan["expected_current_manifest_sha256"], "plan_current_sha_invalid")
    _digest(plan["rollback_manifest_sha256"], "plan_manifest_sha_invalid")
    if plan["historical_manifest_sha256"] != HISTORICAL_MANIFEST_SHA256:
        raise RollbackPreparationError("plan_historical_manifest_invalid")
    switch = _keys(plan["switch_contract"], _SWITCH_KEYS, "plan_switch_invalid")
    if any(type(switch[key]) is not bool or switch[key] is not True for key in switch):  # noqa: E721
        raise RollbackPreparationError("plan_switch_invalid")
    future_gates = _keys(plan["future_gates"], _FUTURE_GATE_KEYS, "plan_future_gates_invalid")
    if any(
        type(future_gates[key]) is not bool or future_gates[key] is not True
        for key in future_gates
    ):
        raise RollbackPreparationError("plan_future_gates_invalid")
    not_run = _keys(plan["not_run"], _NOT_RUN_KEYS, "plan_not_run_invalid")
    if any(type(not_run[key]) is not bool or not_run[key] is not True for key in not_run):  # noqa: E721
        raise RollbackPreparationError("plan_not_run_invalid")
    return plan


def _content_length(headers, reason: str) -> int:  # noqa: ANN001
    value = headers.get("Content-Length")
    if type(value) is not str or _DECIMAL_RE.fullmatch(value) is None:  # noqa: E721
        raise RollbackPreparationError(reason)
    return int(value)


def _default_open(request: Request, timeout: float):
    context = ssl.create_default_context()
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _request(url: str, *, method: str, range_header: str | None = None) -> Request:
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if range_header is not None:
        headers["Range"] = range_header
    request = Request(url, headers=headers, method=method)
    lowered = {key.casefold() for key in request.headers}
    if lowered & {"authorization", "cookie", "proxy-authorization", "x-api-key"}:
        raise RollbackPreparationError("anonymous_request_contract_invalid")
    return request


def _status(response) -> int:  # noqa: ANN001
    value = getattr(response, "status", None)
    if type(value) is not int:  # noqa: E721
        raise RollbackPreparationError("object_response_invalid")
    return value


def verify_public_object(
    plan: dict[str, object],
    *,
    timeout: float,
    opener: Callable[[Request, float], object] = _default_open,
) -> dict[str, object]:
    plan = _validate_plan(plan)
    obj = plan["immutable_object"]
    url = obj["url"]
    expected_bytes = obj["bytes"]
    expected_sha = obj["sha256"]
    range_sha = plan["seed"]["range_0_0_sha256"]
    if type(timeout) is not float or not 0 < timeout <= 120:  # noqa: E721
        raise RollbackPreparationError("timeout_invalid")
    try:
        with opener(_request(url, method="HEAD"), timeout) as response:
            if _status(response) != 200 or response.geturl() != url:
                raise RollbackPreparationError("object_head_invalid")
            if _content_length(response.headers, "object_head_invalid") != expected_bytes:
                raise RollbackPreparationError("object_head_invalid")
        with opener(_request(url, method="GET", range_header="bytes=0-0"), timeout) as response:
            if _status(response) != 206 or response.geturl() != url:
                raise RollbackPreparationError("object_range_invalid")
            expected_range = f"bytes 0-0/{expected_bytes}"
            if response.headers.get("Content-Range") != expected_range:
                raise RollbackPreparationError("object_range_invalid")
            body = response.read(2)
            if len(body) != 1 or _sha(body) != range_sha:
                raise RollbackPreparationError("object_range_invalid")
        total = 0
        digest = hashlib.sha256()
        with opener(_request(url, method="GET"), timeout) as response:
            if _status(response) != 200 or response.geturl() != url:
                raise RollbackPreparationError("object_full_get_invalid")
            if _content_length(response.headers, "object_full_get_invalid") != expected_bytes:
                raise RollbackPreparationError("object_full_get_invalid")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_bytes:
                    raise RollbackPreparationError("object_full_get_invalid")
                digest.update(chunk)
    except RollbackPreparationError:
        raise
    except (HTTPError, URLError, OSError, TimeoutError, ssl.SSLError) as exc:
        raise RollbackPreparationError("object_transport_failed") from exc
    if total != expected_bytes or digest.hexdigest() != expected_sha:
        raise RollbackPreparationError("object_full_get_invalid")
    return {
        "schema_version": 1,
        "status": "verified",
        "operation_id": plan["operation_id"],
        "url": url,
        "bytes": expected_bytes,
        "sha256": expected_sha,
        "head_status": 200,
        "range_status": 206,
        "full_get_status": 200,
        "range_0_0_verified": True,
        "anonymous_requests": True,
        "redirects_followed": False,
    }


def _write_exclusive(path: Path, raw: bytes) -> None:
    if path.exists():
        raise RollbackPreparationError("receipt_path_exists")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _object_receipt(value: object, plan: dict[str, object]) -> dict[str, object]:
    receipt = _keys(value, _OBJECT_RECEIPT_KEYS, "object_receipt_invalid")
    expected = {
        "schema_version": 1,
        "status": "verified",
        "operation_id": plan["operation_id"],
        "url": plan["immutable_object"]["url"],
        "bytes": SEED_BYTES,
        "sha256": SEED_SHA256,
        "head_status": 200,
        "range_status": 206,
        "full_get_status": 200,
        "range_0_0_verified": True,
        "anonymous_requests": True,
        "redirects_followed": False,
    }
    if any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value for key, value in expected.items()):
        raise RollbackPreparationError("object_receipt_invalid")
    return receipt


def _manifest(value: object, *, rollback: bool) -> dict[str, str]:
    manifest = _keys(value, _MANIFEST_KEYS, "manifest_shape_invalid")
    for key in _MANIFEST_KEYS:
        raw = manifest[key]
        if type(raw) is not str or "\x00" in raw or len(raw) > 2_048:  # noqa: E721
            raise RollbackPreparationError("manifest_field_invalid")
    if rollback:
        fields = _manifest_fields({key: manifest[key] for key in _ROLLBACK_FIELDS})
        if any(manifest[key] != value for key, value in fields.items()):
            raise RollbackPreparationError("manifest_field_invalid")
        _object_url(manifest["url"])
    return manifest


def _require_absent(path: Path, reason: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        parent = path.parent.lstat()
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            raise RollbackPreparationError(reason) from None
        return
    raise RollbackPreparationError(reason)


def _manifest_preflight(args: argparse.Namespace) -> dict[str, object]:
    plan_raw = _read_regular(args.plan, maximum=MAX_JSON_BYTES)
    plan = _validate_plan(_load_json(plan_raw))
    current_raw = _read_regular(args.current_manifest, maximum=MAX_MANIFEST_BYTES)
    candidate_raw = _read_regular(args.candidate_manifest, maximum=MAX_MANIFEST_BYTES)
    object_receipt_raw = _read_regular(args.object_receipt, maximum=MAX_JSON_BYTES)
    current = _manifest(_load_json(current_raw), rollback=False)
    candidate = _manifest(_load_json(candidate_raw), rollback=True)
    _object_receipt(_load_json(object_receipt_raw), plan)
    if _sha(current_raw) != plan["expected_current_manifest_sha256"]:
        raise RollbackPreparationError("current_manifest_cas_mismatch")
    if _sha(candidate_raw) != plan["rollback_manifest_sha256"]:
        raise RollbackPreparationError("rollback_manifest_identity_mismatch")
    if candidate["url"] != plan["immutable_object"]["url"]:
        raise RollbackPreparationError("rollback_manifest_object_mismatch")
    if current["url"] == candidate["url"]:
        raise RollbackPreparationError("rollback_manifest_not_a_switch")
    if current["min"] != candidate["min"]:
        raise RollbackPreparationError("rollback_min_version_changed")
    _require_absent(args.backup_path, "backup_path_exists_or_unsafe")
    _require_absent(args.operation_dir, "operation_directory_exists_or_unsafe")
    info = args.current_manifest.lstat()
    return {
        "schema_version": 1,
        "status": "eligible_pending_exclusive_lock_and_apply",
        "operation_id": plan["operation_id"],
        "plan_sha256": _sha(plan_raw),
        "current_manifest_sha256": _sha(current_raw),
        "rollback_manifest_sha256": _sha(candidate_raw),
        "object_receipt_sha256": _sha(object_receipt_raw),
        "manifest_owner": int(info.st_uid),
        "manifest_group": int(info.st_gid),
        "manifest_mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "manifest_bytes": int(info.st_size),
        "backup_path_absent": True,
        "operation_directory_absent": True,
        "exclusive_lock_required": True,
        "atomic_replace_required": True,
        "apply_not_run": True,
        "production_not_run": True,
    }


def _template() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_id": None,
        "observed_at": None,
        "public_base": None,
        "object_prefix": None,
        "expected_current_manifest_sha256": None,
        "rollback_manifest": {
            "latest": SEED_VERSION,
            "min": "0.3.1",
            "notice": None,
            "game_version": None,
        },
        "seed_pins": {
            "bytes": SEED_BYTES,
            "sha256": SEED_SHA256,
            "members": SEED_MEMBER_COUNT,
            "historical_manifest_sha256": HISTORICAL_MANIFEST_SHA256,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("template")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--seed", required=True, type=Path)
    prepare.add_argument("--input", required=True, type=Path)
    prepare.add_argument("--output-dir", required=True, type=Path)
    verify = sub.add_parser("verify-object")
    verify.add_argument("--plan", required=True, type=Path)
    verify.add_argument("--receipt-out", required=True, type=Path)
    verify.add_argument("--timeout", type=float, default=30.0)
    preflight = sub.add_parser("manifest-preflight")
    preflight.add_argument("--plan", required=True, type=Path)
    preflight.add_argument("--current-manifest", required=True, type=Path)
    preflight.add_argument("--candidate-manifest", required=True, type=Path)
    preflight.add_argument("--object-receipt", required=True, type=Path)
    preflight.add_argument("--backup-path", required=True, type=Path)
    preflight.add_argument("--operation-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "template":
            print(json.dumps(_template(), ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == "prepare":
            result = _prepare(args)
        elif args.command == "verify-object":
            plan = _validate_plan(_load_json(_read_regular(args.plan, maximum=MAX_JSON_BYTES)))
            result = verify_public_object(plan, timeout=float(args.timeout))
            _write_exclusive(args.receipt_out, _canonical(result))
        elif args.command == "manifest-preflight":
            result = _manifest_preflight(args)
        else:  # pragma: no cover - argparse owns this branch
            raise RollbackPreparationError("command_invalid")
    except (OSError, TypeError, RollbackPreparationError) as exc:
        reason = str(exc) if type(exc) is RollbackPreparationError else type(exc).__name__
        print(json.dumps({"status": "blocked", "reason": reason}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
