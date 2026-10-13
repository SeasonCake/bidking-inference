#!/usr/bin/env python3
"""Offline, fail-closed migration/rollback for the trial-duration cutover.

The activation service must be stopped.  Dry-run is the default.  Applying requires
the SHA-256 of the exact input and an exclusive backup path so a concurrent or stale
operator view cannot overwrite authoritative trial history.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import BinaryIO

import activation_core


_ADMIN_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_BACKEND = "sqlite-begin-exclusive+claim-link-noreplace"
_LOCK_PROTOCOL_VERSION = 3
_COMMIT_INTENT_SCHEMA = 1


@dataclasses.dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int


@dataclasses.dataclass(frozen=True)
class _StateSnapshot:
    raw: bytes
    value: dict
    sha256: str
    identity: _FileIdentity


@dataclasses.dataclass
class _OpenGeneration:
    path: Path
    stream: BinaryIO
    snapshot: _StateSnapshot

    def refresh(self) -> _StateSnapshot:
        return _snapshot_from_open_stream(self.stream)

    def close(self) -> None:
        self.stream.close()


@dataclasses.dataclass(frozen=True)
class _CommitIntent:
    staged_name: str
    claimed_name: str
    input_sha256: str
    output_sha256: str
    input_identity: _FileIdentity
    output_identity: _FileIdentity


def _identity_from_stat(value: os.stat_result) -> _FileIdentity:
    return _FileIdentity(device=int(value.st_dev), inode=int(value.st_ino))


def _windows_open_file(
    path: Path,
    *,
    writable: bool,
    create_new: bool,
) -> BinaryIO:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    desired_access = 0x80000000 | (0x40000000 if writable else 0)
    share_delete = 0x00000001 | 0x00000002 | 0x00000004
    creation = 1 if create_new else 3
    handle = create_file(
        str(path),
        desired_access,
        share_delete,
        None,
        creation,
        0x00000080,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if create_new and error in {80, 183}:
            raise FileExistsError(error, "file already exists", str(path))
        if not create_new and error in {2, 3}:
            raise FileNotFoundError(error, "file does not exist", str(path))
        raise OSError(error, "CreateFileW failed", str(path))
    flags = getattr(os, "O_BINARY", 0) | (
        os.O_RDWR if writable else os.O_RDONLY
    )
    flags |= getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), flags)
    except Exception:
        kernel32.CloseHandle(handle)
        raise
    try:
        return os.fdopen(
            descriptor,
            "r+b" if writable else "rb",
            buffering=0,
        )
    except Exception:
        os.close(descriptor)
        raise


def _open_file(
    path: Path,
    *,
    writable: bool,
    create_new: bool = False,
) -> BinaryIO:
    if os.name == "nt":
        return _windows_open_file(
            path,
            writable=writable,
            create_new=create_new,
        )
    flags = os.O_RDWR if writable else os.O_RDONLY
    if create_new:
        flags |= os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        return os.fdopen(
            descriptor,
            "r+b" if writable else "rb",
            buffering=0,
        )
    except Exception:
        os.close(descriptor)
        raise


def _windows_identity_from_stream(stream: BinaryIO) -> _FileIdentity:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileId128(ctypes.Structure):
        _fields_ = [("identifier", ctypes.c_ubyte * 16)]

    class _FileIdInfo(ctypes.Structure):
        _fields_ = [
            ("volume_serial_number", ctypes.c_ulonglong),
            ("file_id", _FileId128),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    get_information.restype = wintypes.BOOL
    information = _FileIdInfo()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
    if not get_information(
        handle,
        18,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "GetFileInformationByHandleEx failed")
    file_id = int.from_bytes(
        bytes(information.file_id.identifier),
        byteorder="little",
        signed=False,
    )
    return _FileIdentity(
        device=int(information.volume_serial_number),
        inode=file_id,
    )


def _identity_from_open_stream(stream: BinaryIO) -> _FileIdentity:
    if os.name == "nt":
        return _windows_identity_from_stream(stream)
    return _identity_from_stat(os.fstat(stream.fileno()))


def _snapshot_from_open_stream(stream: BinaryIO) -> _StateSnapshot:
    opened_before = os.fstat(stream.fileno())
    identity_before = _identity_from_open_stream(stream)
    stream.seek(0)
    raw = stream.read()
    opened_after = os.fstat(stream.fileno())
    identity_after = _identity_from_open_stream(stream)
    if (
        identity_before != identity_after
        or opened_before.st_size != opened_after.st_size
        or opened_before.st_mtime_ns != opened_after.st_mtime_ns
        or opened_before.st_ctime_ns != opened_after.st_ctime_ns
        or opened_after.st_size != len(raw)
    ):
        raise ValueError("trial state generation changed while reading")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("trial state is not valid UTF-8 JSON") from exc
    if type(value) is not dict:  # noqa: E721 - signed state rejects subclasses
        raise ValueError("trial state root is not an object")
    return _StateSnapshot(
        raw=raw,
        value=value,
        sha256=hashlib.sha256(raw).hexdigest(),
        identity=identity_before,
    )


def _open_state_generation(path: Path) -> _OpenGeneration:
    try:
        path_stat = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("trial state file is missing") from exc
    if not stat.S_ISREG(path_stat.st_mode) or path.is_symlink():
        raise ValueError("trial state must be a regular file")
    try:
        stream = _open_file(path, writable=False)
    except FileNotFoundError as exc:
        raise ValueError("trial state file changed while opening") from exc
    try:
        snapshot = _snapshot_from_open_stream(stream)
        try:
            current_stream = _open_file(path, writable=False)
        except FileNotFoundError as exc:
            raise ValueError("trial state file changed while reading") from exc
        try:
            current = _snapshot_from_open_stream(current_stream)
        finally:
            current_stream.close()
        if (
            current.identity != snapshot.identity
            or current.sha256 != snapshot.sha256
        ):
            raise ValueError("trial state file changed while reading")
        return _OpenGeneration(
            path=path,
            stream=stream,
            snapshot=snapshot,
        )
    except BaseException:
        stream.close()
        raise


def _read_state(path: Path) -> _StateSnapshot:
    generation = _open_state_generation(path)
    try:
        return generation.snapshot
    finally:
        generation.close()


def _serialize_json(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def _fsync_parent_directory(path: Path) -> None:
    """Persist namespace changes where the platform exposes directory fsync."""

    if os.name == "nt":
        return
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_stable_temp(path: Path) -> tuple[Path, BinaryIO]:
    prefix = f".{path.name}.trial-admin-"
    for _attempt in range(128):
        candidate = path.with_name(f"{prefix}{uuid.uuid4().hex}")
        try:
            return candidate, _open_file(
                candidate,
                writable=True,
                create_new=True,
            )
        except FileExistsError:
            continue
    raise RuntimeError("cannot allocate unique trial state generation")


def _stage_state_output(
    path: Path,
    value: dict,
) -> tuple[_OpenGeneration, bytes]:
    payload = _serialize_json(value)
    tmp, stream = _create_stable_temp(path)
    try:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        snapshot = _snapshot_from_open_stream(stream)
        if snapshot.raw != payload:
            raise RuntimeError("staged trial state verification failed")
        return (
            _OpenGeneration(
                path=tmp,
                stream=stream,
                snapshot=snapshot,
            ),
            payload,
        )
    except Exception:
        stream.close()
        tmp.unlink(missing_ok=True)
        raise


def _publish_backup(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != raw:
            raise RuntimeError("trial state backup verification failed")
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise


def _assert_state_unchanged(path: Path, expected: _StateSnapshot) -> None:
    current = _read_state(path)
    if current.identity != expected.identity:
        raise ValueError("trial state file identity changed before commit")
    if current.sha256 != expected.sha256:
        raise ValueError("trial state SHA-256 changed before commit")


def _commit_intent_path(state_path: Path) -> Path:
    return state_path.with_name(f".{state_path.name}.trial-admin-commit.json")


def _allocate_claim_path(state_path: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{state_path.name}.trial-admin-claimed-",
        dir=str(state_path.parent),
    )
    os.close(descriptor)
    path = Path(raw_path)
    path.unlink()
    return path


def _write_commit_intent(
    state_path: Path,
    *,
    staged_generation: _OpenGeneration | None = None,
    claimed_path: Path,
    input_generation: _OpenGeneration | None = None,
    output_sha256: str,
    staged_path: Path | _OpenGeneration | None = None,
    input_snapshot: _StateSnapshot | None = None,
) -> _CommitIntent:
    close_staged = False
    close_input = False
    if staged_generation is None:
        if isinstance(staged_path, _OpenGeneration):
            staged_generation = staged_path
        elif isinstance(staged_path, Path):
            staged_generation = _open_state_generation(staged_path)
            close_staged = True
        else:
            raise TypeError("staged_generation is required")
    if input_generation is None:
        input_generation = _open_state_generation(state_path)
        close_input = True
        if input_snapshot is not None:
            _assert_snapshot_matches(
                input_generation.snapshot,
                sha256=input_snapshot.sha256,
                identity=input_snapshot.identity,
                label="input trial state",
            )
    intent_path = _commit_intent_path(state_path)
    try:
        staged_snapshot = staged_generation.refresh()
        _assert_snapshot_matches(
            staged_snapshot,
            sha256=output_sha256,
            identity=staged_generation.snapshot.identity,
            label="staged trial state",
        )
        input_anchor = input_generation.snapshot
        _assert_snapshot_matches(
            input_generation.refresh(),
            sha256=input_anchor.sha256,
            identity=input_anchor.identity,
            label="held input trial state",
        )
        intent = _CommitIntent(
            staged_name=staged_generation.path.name,
            claimed_name=claimed_path.name,
            input_sha256=input_anchor.sha256,
            output_sha256=output_sha256,
            input_identity=input_anchor.identity,
            output_identity=staged_snapshot.identity,
        )
        payload = _serialize_json(
            {
                "schema_version": _COMMIT_INTENT_SCHEMA,
                "state_name": state_path.name,
                "staged_name": intent.staged_name,
                "claimed_name": intent.claimed_name,
                "input_sha256": intent.input_sha256,
                "output_sha256": intent.output_sha256,
                "input_device": intent.input_identity.device,
                "input_inode": intent.input_identity.inode,
                "output_device": intent.output_identity.device,
                "output_inode": intent.output_identity.inode,
            }
        )
        descriptor, raw_tmp = tempfile.mkstemp(
            prefix=f".{intent_path.name}.",
            dir=str(intent_path.parent),
        )
        tmp = Path(raw_tmp)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if intent_path.exists():
                raise ValueError("pending trial state commit already exists")
            os.link(tmp, intent_path)
            _fsync_parent_directory(intent_path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            tmp.unlink(missing_ok=True)
        return intent
    finally:
        if close_input:
            input_generation.close()
        if close_staged:
            staged_generation.close()


def _strict_commit_name(
    value: object,
    *,
    field: str,
    state_path: Path,
    required_prefix: str,
) -> str:
    if type(value) is not str or not value or len(value) > 255:  # noqa: E721
        raise ValueError(f"invalid pending commit {field}")
    candidate = Path(value)
    if candidate.name != value or candidate.parent != Path("."):
        raise ValueError(f"invalid pending commit {field}")
    if not value.startswith(required_prefix):
        raise ValueError(f"invalid pending commit {field}")
    resolved = state_path.with_name(value)
    if resolved.parent != state_path.parent:
        raise ValueError(f"invalid pending commit {field}")
    return value


def _read_commit_intent(state_path: Path) -> _CommitIntent | None:
    intent_path = _commit_intent_path(state_path)
    if not intent_path.exists():
        return None
    snapshot = _read_state(intent_path)
    value = snapshot.value
    expected_keys = {
        "schema_version",
        "state_name",
        "staged_name",
        "claimed_name",
        "input_sha256",
        "output_sha256",
        "input_device",
        "input_inode",
        "output_device",
        "output_inode",
    }
    if set(value) != expected_keys:
        raise ValueError("invalid pending trial state commit shape")
    if (
        type(value["schema_version"]) is not int  # noqa: E721
        or value["schema_version"] != _COMMIT_INTENT_SCHEMA
        or type(value["state_name"]) is not str  # noqa: E721
        or value["state_name"] != state_path.name
    ):
        raise ValueError("invalid pending trial state commit header")
    staged_name = _strict_commit_name(
        value["staged_name"],
        field="staged name",
        state_path=state_path,
        required_prefix=f".{state_path.name}.trial-admin-",
    )
    claimed_name = _strict_commit_name(
        value["claimed_name"],
        field="claimed name",
        state_path=state_path,
        required_prefix=f".{state_path.name}.trial-admin-claimed-",
    )
    if (
        staged_name == claimed_name
        or staged_name.startswith(f".{state_path.name}.trial-admin-claimed-")
    ):
        raise ValueError("invalid pending trial state commit generations")
    input_sha256 = value["input_sha256"]
    output_sha256 = value["output_sha256"]
    if (
        type(input_sha256) is not str  # noqa: E721
        or type(output_sha256) is not str  # noqa: E721
        or len(input_sha256) != 64
        or len(output_sha256) != 64
        or any(character not in "0123456789abcdef" for character in input_sha256)
        or any(character not in "0123456789abcdef" for character in output_sha256)
    ):
        raise ValueError("invalid pending trial state commit digest")
    device = value["input_device"]
    inode = value["input_inode"]
    output_device = value["output_device"]
    output_inode = value["output_inode"]
    if (
        type(device) is not int  # noqa: E721
        or type(inode) is not int  # noqa: E721
        or type(output_device) is not int  # noqa: E721
        or type(output_inode) is not int  # noqa: E721
        or device < 0
        or inode < 0
        or output_device < 0
        or output_inode < 0
    ):
        raise ValueError("invalid pending trial state commit identity")
    return _CommitIntent(
        staged_name=staged_name,
        claimed_name=claimed_name,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        input_identity=_FileIdentity(device=device, inode=inode),
        output_identity=_FileIdentity(
            device=output_device,
            inode=output_inode,
        ),
    )


def _snapshot_if_present(path: Path) -> _StateSnapshot | None:
    try:
        return _read_state(path)
    except ValueError:
        if not path.exists():
            return None
        raise


def _assert_snapshot_matches(
    snapshot: _StateSnapshot,
    *,
    sha256: str,
    identity: _FileIdentity | None = None,
    label: str,
) -> None:
    if identity is not None and snapshot.identity != identity:
        raise ValueError(f"{label} identity changed before commit")
    if snapshot.sha256 != sha256:
        raise ValueError(f"{label} SHA-256 changed before commit")


def _claim_authoritative_state(
    state_path: Path,
    claimed_path: Path,
    *,
    expected_generation: _OpenGeneration | None = None,
    expected_sha256: str | None = None,
    expected_identity: _FileIdentity | None = None,
) -> None:
    close_generation = False
    if expected_generation is None:
        expected_generation = _open_state_generation(state_path)
        close_generation = True
    try:
        expected = expected_generation.snapshot
        if expected_sha256 is not None or expected_identity is not None:
            _assert_snapshot_matches(
                expected,
                sha256=expected_sha256 or expected.sha256,
                identity=expected_identity or expected.identity,
                label="input trial state",
            )
        _assert_snapshot_matches(
            expected_generation.refresh(),
            sha256=expected.sha256,
            identity=expected.identity,
            label="held input trial state",
        )
        os.rename(state_path, claimed_path)
        _fsync_parent_directory(state_path)
        held = expected_generation.refresh()
        _assert_snapshot_matches(
            held,
            sha256=expected.sha256,
            identity=expected.identity,
            label="held input trial state",
        )
        claimed = _read_state(claimed_path)
        _assert_snapshot_matches(
            claimed,
            sha256=expected.sha256,
            identity=expected.identity,
            label="claimed trial state",
        )
    finally:
        if close_generation:
            expected_generation.close()


def _publish_staged_state_no_clobber(
    staged_generation: _OpenGeneration,
    state_path: Path,
    *,
    output_sha256: str,
    output_identity: _FileIdentity,
) -> None:
    staged = staged_generation.refresh()
    _assert_snapshot_matches(
        staged,
        sha256=output_sha256,
        identity=output_identity,
        label="staged trial state",
    )
    staged_path_snapshot = _read_state(staged_generation.path)
    _assert_snapshot_matches(
        staged_path_snapshot,
        sha256=output_sha256,
        identity=output_identity,
        label="staged trial state path",
    )
    try:
        os.link(staged_generation.path, state_path)
    except FileExistsError as exc:
        raise ValueError("trial state was recreated during commit") from exc
    _fsync_parent_directory(state_path)
    committed = _read_state(state_path)
    _assert_snapshot_matches(
        committed,
        sha256=output_sha256,
        identity=output_identity,
        label="committed trial state",
    )
    held_after_publish = staged_generation.refresh()
    _assert_snapshot_matches(
        held_after_publish,
        sha256=output_sha256,
        identity=output_identity,
        label="held staged trial state",
    )


def _restore_claim_no_clobber(claimed_path: Path, state_path: Path) -> bool:
    if not claimed_path.exists():
        return state_path.exists()
    if state_path.exists():
        return True
    try:
        os.link(claimed_path, state_path)
    except FileExistsError:
        return True
    _fsync_parent_directory(state_path)
    return True


def _cleanup_commit_artifacts(
    state_path: Path,
    *,
    staged_path: Path,
    claimed_path: Path,
) -> None:
    staged_path.unlink(missing_ok=True)
    claimed_path.unlink(missing_ok=True)
    _commit_intent_path(state_path).unlink(missing_ok=True)
    _fsync_parent_directory(state_path)


def _commit_staged_state(
    state_path: Path,
    *,
    staged_generation: _OpenGeneration,
    input_generation: _OpenGeneration,
    output_sha256: str,
) -> None:
    claimed_path = _allocate_claim_path(state_path)
    try:
        intent = _write_commit_intent(
            state_path,
            staged_generation=staged_generation,
            claimed_path=claimed_path,
            input_generation=input_generation,
            output_sha256=output_sha256,
        )
    except BaseException:
        if not _commit_intent_path(state_path).exists():
            staged_generation.path.unlink(missing_ok=True)
            claimed_path.unlink(missing_ok=True)
        raise
    input_snapshot = input_generation.snapshot
    try:
        _claim_authoritative_state(
            state_path,
            claimed_path,
            expected_generation=input_generation,
        )
        _publish_staged_state_no_clobber(
            staged_generation,
            state_path,
            output_sha256=output_sha256,
            output_identity=intent.output_identity,
        )
    except BaseException:
        # Ordinary failures restore the claimed input only if no external writer
        # has recreated the authority.  If restoration itself fails, retain the
        # durable intent and both generations for the next locked recovery.
        try:
            _restore_claim_no_clobber(claimed_path, state_path)
        except OSError:
            pass
        current = None
        try:
            current = _snapshot_if_present(state_path)
        except ValueError:
            pass
        if (
            current is not None
            and current.sha256 == input_snapshot.sha256
            and current.identity == input_snapshot.identity
        ):
            _cleanup_commit_artifacts(
                state_path,
                staged_path=staged_generation.path,
                claimed_path=claimed_path,
            )
        raise
    _cleanup_commit_artifacts(
        state_path,
        staged_path=staged_generation.path,
        claimed_path=claimed_path,
    )


def _recover_pending_commit(state_path: Path) -> _CommitIntent | None:
    intent = _read_commit_intent(state_path)
    if intent is None:
        return None
    staged_path = state_path.with_name(intent.staged_name)
    claimed_path = state_path.with_name(intent.claimed_name)
    current = None
    claimed = None
    staged = None
    try:
        if state_path.exists():
            current = _open_state_generation(state_path)
        if claimed_path.exists():
            claimed = _open_state_generation(claimed_path)

        if (
            current is not None
            and current.snapshot.sha256 == intent.output_sha256
        ):
            _assert_snapshot_matches(
                current.refresh(),
                sha256=intent.output_sha256,
                identity=intent.output_identity,
                label="pending committed trial state",
            )
            _cleanup_commit_artifacts(
                state_path,
                staged_path=staged_path,
                claimed_path=claimed_path,
            )
            return intent

        staged = _open_state_generation(staged_path)
        _assert_snapshot_matches(
            staged.refresh(),
            sha256=intent.output_sha256,
            identity=intent.output_identity,
            label="pending staged trial state",
        )
        if current is not None:
            _assert_snapshot_matches(
                current.refresh(),
                sha256=intent.input_sha256,
                identity=intent.input_identity,
                label="pending input trial state",
            )
            if claimed is not None:
                raise ValueError(
                    "pending trial state claim conflicts with live input"
                )
            _claim_authoritative_state(
                state_path,
                claimed_path,
                expected_generation=current,
            )
        else:
            if claimed is None:
                raise ValueError(
                    "pending trial state commit lost both generations"
                )
            _assert_snapshot_matches(
                claimed.refresh(),
                sha256=intent.input_sha256,
                identity=intent.input_identity,
                label="pending claimed trial state",
            )

        _publish_staged_state_no_clobber(
            staged,
            state_path,
            output_sha256=intent.output_sha256,
            output_identity=intent.output_identity,
        )
        _cleanup_commit_artifacts(
            state_path,
            staged_path=staged_path,
            claimed_path=claimed_path,
        )
        return intent
    finally:
        for generation in (staged, claimed, current):
            if generation is not None:
                generation.close()


def _inspect_recoverable_pending_commit(
    state_path: Path,
) -> _StateSnapshot | None:
    """Return the eventual output without mutating a recoverable pending commit."""

    intent = _read_commit_intent(state_path)
    if intent is None:
        return None
    staged_path = state_path.with_name(intent.staged_name)
    claimed_path = state_path.with_name(intent.claimed_name)
    current = _snapshot_if_present(state_path)
    claimed = _snapshot_if_present(claimed_path)
    if current is not None and current.sha256 == intent.output_sha256:
        _assert_snapshot_matches(
            current,
            sha256=intent.output_sha256,
            identity=intent.output_identity,
            label="pending committed trial state",
        )
        return current
    staged = _read_state(staged_path)
    _assert_snapshot_matches(
        staged,
        sha256=intent.output_sha256,
        identity=intent.output_identity,
        label="pending staged trial state",
    )
    if current is not None:
        _assert_snapshot_matches(
            current,
            sha256=intent.input_sha256,
            identity=intent.input_identity,
            label="pending input trial state",
        )
        if claimed is not None:
            raise ValueError("pending trial state claim conflicts with live input")
        return staged
    if claimed is None:
        raise ValueError("pending trial state commit lost both generations")
    _assert_snapshot_matches(
        claimed,
        sha256=intent.input_sha256,
        identity=intent.input_identity,
        label="pending claimed trial state",
    )
    return staged


@contextlib.contextmanager
def _exclusive_admin_lock(state_path: Path):
    """Serialize migrate and downgrade operators on Windows and Linux.

    SQLite's ``BEGIN EXCLUSIVE`` is an OS-backed cross-process lock on both
    supported platforms.  The sidecar never stores trial records or identifiers.
    """

    lock_path = state_path.with_name(f".{state_path.name}.trial-admin-lock.sqlite3")
    connection = sqlite3.connect(
        str(lock_path),
        timeout=_ADMIN_LOCK_TIMEOUT_SECONDS,
        isolation_level=None,
    )
    try:
        connection.execute(
            f"PRAGMA busy_timeout={int(_ADMIN_LOCK_TIMEOUT_SECONDS * 1000)}"
        )
        connection.execute("BEGIN EXCLUSIVE")
        yield
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        # The database carries no state; its transaction exists only as an
        # OS-backed mutex.  Releasing it must not turn an already committed trial
        # file replacement into a reported failure.
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        connection.close()


def _transform(args: argparse.Namespace, current: dict, *, now: int) -> dict:
    if args.mode == "migrate":
        if (
            args.cutover_at is None
            or args.grandfather_hours is None
            or args.downgrade_hours is not None
            or args.refresh_at is not None
            or args.refresh_hours is not None
        ):
            raise ValueError("migrate mode requires only cutover/grandfather values")
        return activation_core.migrate_trials_for_cutover(
            current,
            cutover_at=args.cutover_at,
            grandfather_trial_hours=args.grandfather_hours,
            now=now,
        )
    if args.mode == "downgrade":
        if (
            args.downgrade_hours is None
            or args.cutover_at is not None
            or args.grandfather_hours is not None
            or args.refresh_at is not None
            or args.refresh_hours is not None
        ):
            raise ValueError("downgrade mode requires only --downgrade-hours")
        return activation_core.downgrade_trials_for_legacy_runtime(
            current,
            legacy_trial_hours=args.downgrade_hours,
            now=now,
        )
    if (
        args.refresh_at is None
        or args.refresh_hours is None
        or args.cutover_at is not None
        or args.grandfather_hours is not None
        or args.downgrade_hours is not None
    ):
        raise ValueError(
            "refresh mode requires only --refresh-at and --refresh-hours"
        )
    return activation_core.reset_trials_for_public_refresh(
        current,
        refresh_at=args.refresh_at,
        trial_hours=args.refresh_hours,
        now=now,
    )


def _recover_state_locked(state_path: Path) -> dict | None:
    """Recover one durable pending commit while the caller owns the admin lock."""

    recovered = _recover_pending_commit(state_path)
    if recovered is None:
        return None
    return {
        "ok": True,
        "mode": "recovered_pending_commit",
        "input_sha256": recovered.input_sha256,
        "output_sha256": recovered.output_sha256,
        "lock_backend": _LOCK_BACKEND,
        "lock_protocol_version": _LOCK_PROTOCOL_VERSION,
    }


def _operate_state_locked(
    *,
    state_path: Path,
    mode: str,
    cutover_at: int | None = None,
    grandfather_hours: float | None = None,
    downgrade_hours: float | None = None,
    refresh_at: int | None = None,
    refresh_hours: float | None = None,
    apply: bool,
    expected_sha256: str | None = None,
    expected_identity: _FileIdentity | None = None,
    backup_file: Path | None = None,
    now: int | None = None,
) -> dict:
    """Transform one state while the caller owns ``_exclusive_admin_lock``.

    This deliberately does not acquire the lock or recover a pending intent.
    The standalone CLI performs recovery first; the state-aware deployment
    wrapper does so only after the service is stopped, then holds the same lock
    across migration, install, health validation, and any rollback.
    """

    if mode not in {"migrate", "downgrade", "refresh"}:
        raise ValueError("unsupported trial state operation")
    args = argparse.Namespace(
        mode=mode,
        cutover_at=cutover_at,
        grandfather_hours=grandfather_hours,
        downgrade_hours=downgrade_hours,
        refresh_at=refresh_at,
        refresh_hours=refresh_hours,
    )
    if _read_commit_intent(state_path) is not None:
        raise ValueError("pending trial state commit requires recovery")
    input_generation = _open_state_generation(state_path)
    try:
        snapshot = input_generation.snapshot
        if (
            expected_identity is not None
            and (
                type(expected_identity) is not _FileIdentity
                or snapshot.identity != expected_identity
            )
        ):
            raise ValueError("input trial state identity does not match")
        expected = str(expected_sha256 or "").strip().lower()
        if apply and (len(expected) != 64 or expected != snapshot.sha256):
            raise ValueError("input SHA-256 does not match")
        if apply and backup_file is None:
            raise ValueError("backup_file is required with apply")

        transformed = _transform(
            args,
            snapshot.value,
            now=int(time.time()) if now is None else int(now),
        )
        summary = {"records": len(transformed)}
        if mode == "refresh":
            summary.update(
                input_records=len(snapshot.value),
                removed_records=len(snapshot.value) - len(transformed),
            )
        if not apply:
            return {
                "ok": True,
                "mode": f"dry_run_{mode}",
                **summary,
                "input_sha256": snapshot.sha256,
                "lock_backend": _LOCK_BACKEND,
                "lock_protocol_version": _LOCK_PROTOCOL_VERSION,
            }

        staged_generation, output = _stage_state_output(state_path, transformed)
        assert backup_file is not None
        try:
            _assert_snapshot_matches(
                input_generation.refresh(),
                sha256=snapshot.sha256,
                identity=snapshot.identity,
                label="held input trial state",
            )
            _assert_state_unchanged(state_path, snapshot)
            _publish_backup(backup_file, snapshot.raw)
            _assert_snapshot_matches(
                input_generation.refresh(),
                sha256=snapshot.sha256,
                identity=snapshot.identity,
                label="held input trial state",
            )
            _assert_state_unchanged(state_path, snapshot)
            _commit_staged_state(
                state_path,
                staged_generation=staged_generation,
                input_generation=input_generation,
                output_sha256=hashlib.sha256(output).hexdigest(),
            )
        except BaseException:
            # A published intent owns both generations until locked recovery.
            if not _commit_intent_path(state_path).exists():
                staged_generation.path.unlink(missing_ok=True)
            raise
        finally:
            staged_generation.close()

        return {
            "ok": True,
            "mode": f"applied_{mode}",
            **summary,
            "input_sha256": snapshot.sha256,
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "backup_sha256": snapshot.sha256,
            "lock_backend": _LOCK_BACKEND,
            "lock_protocol_version": _LOCK_PROTOCOL_VERSION,
        }
    finally:
        input_generation.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="离线迁移、等到期点回滚或授权补发试用；默认只检查不写入",
    )
    parser.add_argument("--state-file", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("migrate", "downgrade", "refresh"),
    )
    parser.add_argument("--cutover-at", type=int)
    parser.add_argument("--grandfather-hours", type=float)
    parser.add_argument("--downgrade-hours", type=float)
    parser.add_argument("--refresh-at", type=int)
    parser.add_argument("--refresh-hours", type=float)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--backup-file")
    args = parser.parse_args(argv)

    state_path = Path(args.state_file)
    with _exclusive_admin_lock(state_path):
        receipt_value = _recover_state_locked(state_path)
        if receipt_value is None:
            receipt_value = _operate_state_locked(
                state_path=state_path,
                mode=args.mode,
                cutover_at=args.cutover_at,
                grandfather_hours=args.grandfather_hours,
                downgrade_hours=args.downgrade_hours,
                refresh_at=args.refresh_at,
                refresh_hours=args.refresh_hours,
                apply=args.apply,
                expected_sha256=args.expected_sha256,
                backup_file=(
                    Path(args.backup_file)
                    if args.backup_file is not None
                    else None
                ),
            )
        receipt = json.dumps(receipt_value, sort_keys=True)
        try:
            print(receipt)
        except (BrokenPipeError, OSError):
            # The authoritative replace has already committed.  A closed output
            # pipe cannot safely be reclassified as a failed migration.
            pass
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[trial-state-admin] failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
