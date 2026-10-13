"""Install and verify an immutable release runtime component.

The package-provided descriptor is the authority.  Runtime lookup never consults
PATH, Program Files, or an arbitrary environment variable.  A component is
accepted only after its archive identity and complete extracted inventory match.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
from typing import Any, BinaryIO
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid
import zipfile


CHUNK_SIZE = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 45.0
LOCK_WAIT_SECONDS = 120.0
LOCK_STALE_SECONDS = 10 * 60
READY_SENTINEL = "BIDKING_COMPONENT_READY="
FAILURE_SENTINEL = "BIDKING_COMPONENT_FAILURE="
_WINDOWS_ZONE_IDENTIFIER_SUFFIX = ":Zone.Identifier"
_REPARSE_POINT = 0x400


class ComponentError(ValueError):
    """Fail-closed runtime component error."""


def _fixed_failure_class(error: BaseException) -> str:
    """Map only stable exception types/codes; never expose paths or messages."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PermissionError):
            return "component_write_denied"
        if isinstance(current, OSError):
            winerror = getattr(current, "winerror", None)
            if winerror in {5, 19, 32, 33}:
                return "component_write_denied"
            if winerror in {577, 1260}:
                return "component_execution_blocked"
        current = current.__cause__ or current.__context__
    return "unknown"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _plain_entry_info(path: Path, *, expect_directory: bool) -> os.stat_result:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ComponentError("component path is missing") from exc
    expected_type = stat.S_ISDIR if expect_directory else stat.S_ISREG
    if (
        not expected_type(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT
    ):
        raise ComponentError("component path is unsafe")
    if not expect_directory and (
        int(info.st_ino) == 0 or int(info.st_nlink) != 1
    ):
        raise ComponentError("component path is hardlinked")
    return info


def _entry_exists(path: Path) -> bool:
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ComponentError("component path is unreadable") from exc
    return True


def _validate_existing_directory_ancestors(path: Path) -> None:
    for candidate in (path, *path.parents):
        if _entry_exists(candidate):
            _plain_entry_info(candidate, expect_directory=True)


def _strict_tree_files(root: Path) -> list[tuple[str, Path, os.stat_result]]:
    _plain_entry_info(root, expect_directory=True)
    pending = [root]
    namespace: set[str] = set()
    identities: set[tuple[int, int]] = set()
    files: list[tuple[str, Path, os.stat_result]] = []
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda entry: entry.name.casefold(),
                )
        except OSError as exc:
            raise ComponentError("component payload is unreadable") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                # Windows DirEntry.stat() can omit file identity/link count; the
                # path-based no-follow stat is required for hardlink rejection.
                info = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise ComponentError("component member is unreadable") from exc
            if (
                entry.is_symlink()
                or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT
            ):
                raise ComponentError("component payload contains a reparse point")
            relative = path.relative_to(root).as_posix()
            folded = relative.casefold()
            if not relative or folded in namespace:
                raise ComponentError(
                    "component payload contains a case-insensitive path collision"
                )
            namespace.add(folded)
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ComponentError("component payload contains an invalid member")
            identity = (int(info.st_dev), int(info.st_ino))
            if identity[1] == 0 or identity in identities or int(info.st_nlink) != 1:
                raise ComponentError("component payload contains a hardlink")
            identities.add(identity)
            files.append((relative, path, info))
    files.sort(key=lambda item: item[0].casefold())
    return files


def _validate_existing_cache_tree(component_root: Path) -> None:
    if not _entry_exists(component_root):
        return
    _plain_entry_info(component_root, expect_directory=True)
    try:
        _strict_tree_files(component_root)
    except ComponentError as exc:
        raise ComponentError("bundled runtime cache topology is unsafe") from exc


def _directory_inventory(root: Path) -> dict[str, Any]:
    rows: list[tuple[str, str, int, str]] = []
    suffix_counts: Counter[str] = Counter()
    for relative, path, info in _strict_tree_files(root):
        folded = relative.casefold()
        size = int(info.st_size)
        sha256 = _sha256_file(path)
        rows.append((folded, relative, size, sha256))
        suffix_counts[path.suffix.lower()] += 1

    rows.sort(key=lambda row: row[0])
    serialized = "".join(
        f"{relative}|{size}|{sha256}\n"
        for _folded, relative, size, sha256 in rows
    ).encode("utf-8")
    return {
        "file_count": len(rows),
        "total_bytes": sum(row[2] for row in rows),
        "inventory_sha256": hashlib.sha256(serialized).hexdigest(),
        "suffix_counts": dict(sorted(suffix_counts.items())),
        "files": {
            relative: {"bytes": size, "sha256": sha256}
            for _folded, relative, size, sha256 in rows
        },
    }


def _read_descriptor(path: Path) -> dict[str, Any]:
    try:
        descriptor = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComponentError("component descriptor is unreadable") from exc
    if not isinstance(descriptor, dict) or descriptor.get("schema_version") != 1:
        raise ComponentError("unsupported component descriptor schema")

    component_id = descriptor.get("component_id")
    version = descriptor.get("version")
    inventory = descriptor.get("inventory")
    archive = descriptor.get("archive")
    mirrors = descriptor.get("mirrors")
    if not isinstance(component_id, str) or not component_id.strip():
        raise ComponentError("component descriptor has no component_id")
    if (
        "/" in component_id
        or "\\" in component_id
        or component_id in {".", ".."}
    ):
        raise ComponentError("component_id is not cache-safe")
    if not isinstance(version, str) or not version.strip():
        raise ComponentError("component descriptor has no version")
    if "/" in version or "\\" in version or version in {".", ".."}:
        raise ComponentError("component version is not cache-safe")
    if not isinstance(inventory, dict):
        raise ComponentError("component descriptor has no inventory")
    if not isinstance(archive, dict):
        raise ComponentError("component descriptor has no archive identity")
    if not isinstance(mirrors, list) or not all(
        isinstance(value, str) for value in mirrors
    ):
        raise ComponentError("component mirrors must be a string array")

    for key in ("file_count", "total_bytes"):
        value = inventory.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ComponentError(f"component inventory has invalid {key}")
    inventory_sha256 = inventory.get("inventory_sha256")
    if (
        not isinstance(inventory_sha256, str)
        or len(inventory_sha256) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in inventory_sha256)
    ):
        raise ComponentError("component inventory has invalid sha256")

    file_name = archive.get("file_name")
    archive_bytes = archive.get("bytes")
    archive_sha256 = archive.get("sha256")
    payload_prefix = archive.get("payload_prefix")
    if (
        not isinstance(file_name, str)
        or not file_name
        or Path(file_name).name != file_name
    ):
        raise ComponentError("component archive has an unsafe file_name")
    if (
        isinstance(archive_bytes, bool)
        or not isinstance(archive_bytes, int)
        or archive_bytes < 1
    ):
        raise ComponentError("component archive has invalid bytes")
    if (
        not isinstance(archive_sha256, str)
        or len(archive_sha256) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in archive_sha256)
    ):
        raise ComponentError("component archive has invalid sha256")
    if (
        not isinstance(payload_prefix, str)
        or not payload_prefix
        or "/" in payload_prefix
        or "\\" in payload_prefix
        or payload_prefix in {".", ".."}
    ):
        raise ComponentError("component archive has an unsafe payload_prefix")

    required_files = descriptor.get("required_files", {})
    if not isinstance(required_files, dict):
        raise ComponentError("component required_files must be an object")
    for relative, identity in required_files.items():
        if (
            not isinstance(relative, str)
            or not relative
            or not isinstance(identity, dict)
        ):
            raise ComponentError("component required_files entry is invalid")
        safe_relative = PurePosixPath(relative)
        if (
            safe_relative.is_absolute()
            or ".." in safe_relative.parts
            or "\\" in relative
        ):
            raise ComponentError("component required file path is unsafe")
        size = identity.get("bytes")
        sha256 = identity.get("sha256")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(sha256, str)
            or len(sha256) != 64
        ):
            raise ComponentError("component required file identity is invalid")
    return descriptor


def _verify_payload(root: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    actual = _directory_inventory(root)
    expected = descriptor["inventory"]
    for key in ("file_count", "total_bytes", "inventory_sha256"):
        if actual[key] != expected.get(key):
            raise ComponentError(f"component inventory mismatch: {key}")

    expected_suffixes = descriptor.get("suffix_counts")
    if expected_suffixes is not None:
        if not isinstance(expected_suffixes, dict):
            raise ComponentError("component suffix_counts must be an object")
        normalized_suffixes = {
            str(key).lower(): int(value)
            for key, value in expected_suffixes.items()
        }
        if actual["suffix_counts"] != normalized_suffixes:
            raise ComponentError("component inventory mismatch: suffix_counts")

    for relative, expected_file in descriptor.get("required_files", {}).items():
        actual_file = actual["files"].get(relative)
        if actual_file is None:
            raise ComponentError("component required file is missing")
        for key in ("bytes", "sha256"):
            if actual_file.get(key) != expected_file.get(key):
                raise ComponentError("component required file identity mismatch")
    return actual


def _read_bundled_runtime_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComponentError("bundled runtime manifest is unreadable") from exc
    if (
        type(manifest) is not dict  # noqa: E721
        or manifest.get("schema_version") != 1
        or manifest.get("asset_id") != "mitmproxy-windows-runtime"
        or type(manifest.get("version")) is not str  # noqa: E721
        or not manifest["version"]
        or "/" in manifest["version"]
        or "\\" in manifest["version"]
        or manifest["version"] in {".", ".."}
        or type(manifest.get("inventory")) is not dict  # noqa: E721
        or type(manifest.get("required_files")) is not dict  # noqa: E721
    ):
        raise ComponentError("bundled runtime manifest is invalid")
    inventory = manifest["inventory"]
    if (
        type(inventory.get("file_count")) is not int  # noqa: E721
        or inventory["file_count"] < 1
        or type(inventory.get("total_bytes")) is not int  # noqa: E721
        or inventory["total_bytes"] < 1
        or type(inventory.get("inventory_sha256")) is not str  # noqa: E721
        or re.fullmatch(r"[0-9a-f]{64}", inventory["inventory_sha256"])
        is None
    ):
        raise ComponentError("bundled runtime inventory is invalid")
    for relative, identity in manifest["required_files"].items():
        parsed = (
            PurePosixPath(relative)
            if type(relative) is str  # noqa: E721
            else None
        )
        if (
            parsed is None
            or not relative
            or parsed.is_absolute()
            or ".." in parsed.parts
            or "." in parsed.parts
            or "\\" in relative
            or type(identity) is not dict  # noqa: E721
            or type(identity.get("bytes")) is not int  # noqa: E721
            or identity["bytes"] < 0
            or type(identity.get("sha256")) is not str  # noqa: E721
            or re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]) is None
        ):
            raise ComponentError("bundled runtime required file is invalid")
    return manifest


def _verify_bundled_runtime(
    root: Path,
    manifest: dict[str, Any],
    *,
    require_zone_free: bool,
) -> dict[str, Any]:
    actual = _directory_inventory(root)
    expected = manifest["inventory"]
    for key in ("file_count", "total_bytes", "inventory_sha256"):
        if actual[key] != expected[key]:
            raise ComponentError(f"bundled runtime inventory mismatch: {key}")
    for relative, expected_file in manifest["required_files"].items():
        actual_file = actual["files"].get(relative)
        if actual_file is None or any(
            actual_file[key] != expected_file[key] for key in ("bytes", "sha256")
        ):
            raise ComponentError("bundled runtime required file mismatch")
    if require_zone_free and os.name == "nt":
        for relative in actual["files"]:
            member = root.joinpath(*PurePosixPath(relative).parts)
            if os.path.exists(str(member) + _WINDOWS_ZONE_IDENTIFIER_SUFFIX):
                raise ComponentError("bundled runtime cache has a download marker")
    return actual


def _copy_default_stream_tree(source_root: Path, destination_root: Path) -> None:
    destination_root.mkdir(parents=True)
    for relative, source, _info in _strict_tree_files(source_root):
        destination = destination_root.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with source.open("rb") as input_stream, destination.open("xb") as output:
                shutil.copyfileobj(input_stream, output, length=CHUNK_SIZE)
                output.flush()
                os.fsync(output.fileno())
        except OSError as exc:
            raise ComponentError("bundled runtime copy failed") from exc


def ensure_bundled_runtime_copy(
    source_root: Path,
    manifest_path: Path,
    *,
    cache_root: Path | None = None,
) -> Path:
    """Create a verified default-stream-only execution copy of bundled WSS."""

    source_root = _absolute_path(source_root)
    manifest_path = _absolute_path(manifest_path)
    _plain_entry_info(source_root, expect_directory=True)
    _plain_entry_info(manifest_path, expect_directory=False)
    manifest = _read_bundled_runtime_manifest(manifest_path)
    _verify_bundled_runtime(source_root, manifest, require_zone_free=False)
    root = _absolute_path(cache_root or _default_cache_root())
    _validate_existing_directory_ancestors(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ComponentError("bundled runtime cache is unavailable") from exc
    _plain_entry_info(root, expect_directory=True)
    asset_root = root / "mitmproxy-bundled"
    try:
        asset_root.mkdir(exist_ok=True)
    except OSError as exc:
        raise ComponentError("bundled runtime cache is unavailable") from exc
    _plain_entry_info(asset_root, expect_directory=True)
    identity = (
        f"{manifest['version']}-"
        f"{manifest['inventory']['inventory_sha256'][:16]}"
    )
    component_root = asset_root / identity
    payload_root = component_root / "payload"
    _validate_existing_cache_tree(component_root)
    try:
        _verify_bundled_runtime(payload_root, manifest, require_zone_free=True)
        _write_ready_marker(component_root, manifest_path, manifest)
        return payload_root
    except ComponentError:
        pass

    lock_path = component_root.parent / f".{component_root.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = _acquire_lock(lock_path)
    temporary_root: Path | None = None
    try:
        _validate_existing_cache_tree(component_root)
        try:
            _verify_bundled_runtime(payload_root, manifest, require_zone_free=True)
            _write_ready_marker(component_root, manifest_path, manifest)
            return payload_root
        except ComponentError:
            pass
        temporary_root = component_root.parent / f".install-{uuid.uuid4().hex}"
        temporary_payload = temporary_root / "payload"
        _copy_default_stream_tree(source_root, temporary_payload)
        _verify_bundled_runtime(
            temporary_payload,
            manifest,
            require_zone_free=True,
        )
        displaced: Path | None = None
        _validate_existing_cache_tree(component_root)
        if _entry_exists(component_root):
            displaced = component_root.with_name(
                f"{component_root.name}.invalid-{uuid.uuid4().hex}"
            )
            os.replace(component_root, displaced)
        os.replace(temporary_root, component_root)
        temporary_root = None
        _write_ready_marker(component_root, manifest_path, manifest)
        _verify_bundled_runtime(payload_root, manifest, require_zone_free=True)
        if displaced is not None:
            shutil.rmtree(displaced, ignore_errors=True)
        return payload_root
    finally:
        os.close(lock_descriptor)
        lock_path.unlink(missing_ok=True)
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)


def _component_cache_path(
    cache_root: Path,
    descriptor: dict[str, Any],
) -> Path:
    inventory_sha = str(descriptor["inventory"]["inventory_sha256"]).lower()
    identity = f"{descriptor['version']}-{inventory_sha[:16]}"
    return cache_root / str(descriptor["component_id"]) / identity


def _ready_marker(component_root: Path, descriptor_path: Path) -> Path:
    return component_root / "ready.json"


def _write_ready_marker(
    component_root: Path,
    descriptor_path: Path,
    descriptor: dict[str, Any],
) -> None:
    component_id = descriptor.get("component_id", descriptor.get("asset_id"))
    if type(component_id) is not str or not component_id:  # noqa: E721
        raise ComponentError("component ready identity is invalid")
    payload = {
        "schema_version": 1,
        "component_id": component_id,
        "version": descriptor["version"],
        "inventory_sha256": descriptor["inventory"]["inventory_sha256"],
        "descriptor_sha256": _sha256_file(descriptor_path),
    }
    marker = _ready_marker(component_root, descriptor_path)
    temporary = marker.with_name(f"{marker.name}.tmp-{uuid.uuid4().hex}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, marker)


def _verify_bundled_ready_marker(
    component_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
    marker = _ready_marker(component_root, manifest_path)
    info = _plain_entry_info(marker, expect_directory=False)
    if int(info.st_size) < 2 or int(info.st_size) > 4096:
        raise ComponentError("bundled runtime ready identity is invalid")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComponentError("bundled runtime ready identity is unreadable") from exc
    expected = {
        "schema_version": 1,
        "component_id": manifest["asset_id"],
        "version": manifest["version"],
        "inventory_sha256": manifest["inventory"]["inventory_sha256"],
        "descriptor_sha256": _sha256_file(manifest_path),
    }
    if type(payload) is not dict or payload != expected:  # noqa: E721
        raise ComponentError("bundled runtime ready identity mismatch")


def validate_bundled_runtime_copy(
    payload_root: Path,
    manifest_path: Path,
    *,
    cache_root: Path | None = None,
) -> Path:
    """Validate one exact content-addressed bundled WSS cache payload.

    This is the consumer-side counterpart of ``ensure_bundled_runtime_copy``.
    It never discovers arbitrary runtimes: the caller supplies the package
    manifest and the fixed cache root, and the payload must be the one exact
    content-addressed location derived from both.
    """

    payload_root = _absolute_path(payload_root)
    manifest_path = _absolute_path(manifest_path)
    _plain_entry_info(manifest_path, expect_directory=False)
    manifest = _read_bundled_runtime_manifest(manifest_path)
    root = _absolute_path(cache_root or _default_cache_root())
    _validate_existing_directory_ancestors(root)
    _plain_entry_info(root, expect_directory=True)
    asset_root = root / "mitmproxy-bundled"
    identity = (
        f"{manifest['version']}-"
        f"{manifest['inventory']['inventory_sha256'][:16]}"
    )
    component_root = asset_root / identity
    expected_payload = component_root / "payload"
    if os.path.normcase(os.fspath(payload_root)) != os.path.normcase(
        os.fspath(expected_payload)
    ):
        raise ComponentError("bundled runtime cache identity mismatch")
    for directory in (asset_root, component_root, expected_payload):
        _plain_entry_info(directory, expect_directory=True)
    _validate_existing_cache_tree(component_root)
    _verify_bundled_runtime(
        expected_payload,
        manifest,
        require_zone_free=True,
    )
    _verify_bundled_ready_marker(component_root, manifest_path, manifest)
    return expected_payload


def _verify_archive_file(path: Path, descriptor: dict[str, Any]) -> None:
    expected = descriptor["archive"]
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ComponentError("component archive is unreadable") from exc
    if size != expected["bytes"]:
        raise ComponentError("component archive size mismatch")
    if _sha256_file(path) != str(expected["sha256"]).lower():
        raise ComponentError("component archive sha256 mismatch")


def _download_to(
    url: str,
    destination: Path,
    expected_bytes: int,
    *,
    timeout: float,
) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ComponentError("component mirror must use HTTPS")
    request = Request(
        url,
        headers={
            "User-Agent": "BidKing-Live-Component/1",
            "Accept": "application/octet-stream",
        },
    )
    try:
        response = urlopen(request, timeout=timeout)  # noqa: S310 - HTTPS enforced
    except OSError as exc:
        raise ComponentError("component mirror request failed") from exc
    with response:
        final_url = response.geturl()
        if urlparse(final_url).scheme.lower() != "https":
            raise ComponentError("component mirror redirected away from HTTPS")
        with destination.open("wb") as stream:
            copied = _copy_bounded(response, stream, expected_bytes)
    if copied != expected_bytes:
        raise ComponentError("component mirror returned an unexpected size")


def _copy_bounded(source: BinaryIO, destination: BinaryIO, limit: int) -> int:
    copied = 0
    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            return copied
        copied += len(chunk)
        if copied > limit:
            raise ComponentError("component archive exceeds declared size")
        destination.write(chunk)


def _safe_archive_members(
    archive: zipfile.ZipFile,
    descriptor: dict[str, Any],
) -> list[tuple[zipfile.ZipInfo, str]]:
    prefix = str(descriptor["archive"]["payload_prefix"])
    expected_prefix = f"{prefix}/"
    rows: list[tuple[zipfile.ZipInfo, str]] = []
    casefolded: set[str] = set()
    total_bytes = 0
    for member in archive.infolist():
        name = member.filename
        if member.is_dir():
            continue
        if "\\" in name or not name.startswith(expected_prefix):
            raise ComponentError("component archive member is outside payload root")
        relative = name[len(expected_prefix) :]
        parsed = PurePosixPath(relative)
        if (
            not relative
            or parsed.is_absolute()
            or ".." in parsed.parts
            or "." in parsed.parts
        ):
            raise ComponentError("component archive has an unsafe member")
        folded = relative.casefold()
        if folded in casefolded:
            raise ComponentError(
                "component archive has a duplicate or case-colliding member"
            )
        casefolded.add(folded)
        unix_mode = (member.external_attr >> 16) & 0xFFFF
        if (unix_mode & 0o170000) == 0o120000:
            raise ComponentError("component archive contains a symbolic link")
        total_bytes += member.file_size
        rows.append((member, relative))

    expected = descriptor["inventory"]
    if len(rows) != expected["file_count"]:
        raise ComponentError("component archive file_count mismatch")
    if total_bytes != expected["total_bytes"]:
        raise ComponentError("component archive total_bytes mismatch")
    return rows


def _extract_archive(
    archive_path: Path,
    destination: Path,
    descriptor: dict[str, Any],
) -> Path:
    payload_root = destination / str(descriptor["archive"]["payload_prefix"])
    payload_root.mkdir(parents=True)
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ComponentError("component archive is not a readable ZIP") from exc
    with archive:
        members = _safe_archive_members(archive, descriptor)
        for member, relative in members:
            target = payload_root.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(member) as source, target.open("wb") as output:
                    copied = _copy_bounded(source, output, member.file_size)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise ComponentError("component archive member is unreadable") from exc
            if copied != member.file_size:
                raise ComponentError("component archive member size mismatch")
    return payload_root


def _acquire_lock(lock_path: Path) -> int:
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0
            if age > LOCK_STALE_SECONDS:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise ComponentError("timed out waiting for component install lock")
            time.sleep(0.2)
            continue
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        return descriptor


def _default_cache_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise ComponentError("LOCALAPPDATA is unavailable")
    return Path(local_app_data) / "BidKingLive" / "components"


def ensure_component(
    descriptor_path: Path,
    *,
    cache_root: Path | None = None,
    local_archive: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    descriptor_path = descriptor_path.resolve()
    descriptor = _read_descriptor(descriptor_path)
    root = (cache_root or _default_cache_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    component_root = _component_cache_path(root, descriptor)
    payload_root = component_root / str(descriptor["archive"]["payload_prefix"])

    try:
        _verify_payload(payload_root, descriptor)
        _write_ready_marker(component_root, descriptor_path, descriptor)
        return payload_root
    except ComponentError:
        pass

    lock_path = component_root.parent / f".{component_root.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = _acquire_lock(lock_path)
    temporary_root: Path | None = None
    try:
        try:
            _verify_payload(payload_root, descriptor)
            _write_ready_marker(component_root, descriptor_path, descriptor)
            return payload_root
        except ComponentError:
            pass

        temporary_root = component_root.parent / f".install-{uuid.uuid4().hex}"
        temporary_root.mkdir(parents=True)
        archive_path = temporary_root / str(descriptor["archive"]["file_name"])
        if local_archive is not None:
            source = local_archive.resolve()
            if not source.is_file():
                raise ComponentError("local component archive is missing")
            shutil.copyfile(source, archive_path)
        else:
            errors: list[str] = []
            mirrors = descriptor["mirrors"]
            if not mirrors:
                raise ComponentError("component descriptor has no download mirror")
            for index, url in enumerate(mirrors):
                try:
                    _download_to(
                        url,
                        archive_path,
                        int(descriptor["archive"]["bytes"]),
                        timeout=timeout,
                    )
                    break
                except ComponentError as exc:
                    errors.append(f"mirror#{index + 1}:{exc}")
                    archive_path.unlink(missing_ok=True)
            else:
                raise ComponentError(
                    "all component mirrors failed: " + "; ".join(errors)
                )

        _verify_archive_file(archive_path, descriptor)
        install_root = temporary_root / "component"
        payload = _extract_archive(archive_path, install_root, descriptor)
        _verify_payload(payload, descriptor)
        archive_path.unlink(missing_ok=True)

        displaced: Path | None = None
        if component_root.exists():
            displaced = component_root.with_name(
                f"{component_root.name}.invalid-{uuid.uuid4().hex}"
            )
            os.replace(component_root, displaced)
        os.replace(install_root, component_root)
        _write_ready_marker(component_root, descriptor_path, descriptor)
        _verify_payload(payload_root, descriptor)
        if displaced is not None:
            shutil.rmtree(displaced, ignore_errors=True)
        return payload_root
    finally:
        os.close(lock_descriptor)
        lock_path.unlink(missing_ok=True)
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor", type=Path)
    parser.add_argument("--bundled-root", type=Path)
    parser.add_argument("--bundled-manifest", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--local-archive", type=Path)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        bundled_mode = args.bundled_root is not None or args.bundled_manifest is not None
        if bundled_mode:
            if (
                args.bundled_root is None
                or args.bundled_manifest is None
                or args.descriptor is not None
                or args.local_archive is not None
            ):
                raise ComponentError("bundled runtime arguments are incomplete")
            payload = ensure_bundled_runtime_copy(
                args.bundled_root,
                args.bundled_manifest,
                cache_root=args.cache_root,
            )
        else:
            if args.descriptor is None:
                raise ComponentError("component descriptor is required")
            payload = ensure_component(
                args.descriptor,
                cache_root=args.cache_root,
                local_archive=args.local_archive,
                timeout=max(1.0, float(args.timeout)),
            )
    except (ComponentError, OSError) as exc:
        print(f"{FAILURE_SENTINEL}{_fixed_failure_class(exc)}", file=sys.stderr)
        safe_detail = str(exc) if isinstance(exc, ComponentError) else "component operation failed"
        print(f"[FAIL] {safe_detail}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {"status": "ready", "runtime_root": str(payload)},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    else:
        print(f"{READY_SENTINEL}{payload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
