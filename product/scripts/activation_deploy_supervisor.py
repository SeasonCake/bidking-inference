"""External fail-safe supervisor for one state-aware activation deployment.

The state-aware deployer can restore its own transaction after ordinary Python
exceptions.  It cannot execute that recovery path after SIGKILL or an OOM kill.
This supervisor is intended to run as a small systemd service outside the SSH
session.  It snapshots only the deployed code group and systemd unit, launches
the audited deployer exactly once, and restores that old pair if the child does
not finish with a healthy, byte-exact candidate.

No activation, trial, revoke, device-block, upload identity, Pet, sample, or
manifest authority is copied or restored here.
"""
# ruff: noqa: E721 - security boundaries require exact built-in types

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Protocol, Sequence

SCHEMA_VERSION = 6
CHUNK_SIZE = 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_STATE_AWARE_SOURCE_BYTES = 2 * 1024 * 1024
DEFAULT_CHILD_TIMEOUT_SECONDS = 420.0
# The production installer runs on POSIX and writes every owned code/unit target as
# 0644.  Windows cannot represent POSIX owner/group bits and reports writable files
# as 0666; retaining that value keeps the source-level harness meaningful there
# without weakening the Linux production identity contract.
DEPLOYED_FILE_MODE = 0o644 if os.name == "posix" else 0o666
_PHASES = frozenset(
    {"prepared", "apply_started", "candidate_deployed", "old_recovered", "membership_recovery_required"}
)
_UNIT_EPOCH_OPTIONS = (
    "--upload-security-epoch",
    "--min-client-security-epoch",
)


class SupervisorError(RuntimeError):
    """Fail-closed external deployment supervisor error."""


class MembershipRecoveryRequired(SupervisorError):
    """An incompatible legacy rollback cannot preserve member authority."""


_STATE_AWARE_MODULE_FILE = (
    Path(__file__).resolve().with_name("activation_state_aware_deploy.py")
)


def _load_state_aware_source_exact(
    path: Path,
) -> tuple[types.ModuleType, tuple[str, int, int, int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SupervisorError("state-aware deploy source cannot be opened") from exc
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_STATE_AWARE_SOURCE_BYTES:
            raise SupervisorError("state-aware deploy source is unsafe")
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != before.st_dev
            or current.st_ino != before.st_ino
        ):
            raise SupervisorError("state-aware deploy source path is unsafe")
        while True:
            try:
                chunk = os.read(descriptor, CHUNK_SIZE)
            except InterruptedError:
                continue
            except OSError as exc:
                raise SupervisorError("state-aware deploy source cannot be read") from exc
            if type(chunk) is not bytes or len(chunk) > CHUNK_SIZE:
                raise SupervisorError("state-aware deploy source read is invalid")
            if chunk == b"":
                break
            chunks.append(chunk)
            digest.update(chunk)
            size += len(chunk)
            if size > MAX_STATE_AWARE_SOURCE_BYTES:
                raise SupervisorError("state-aware deploy source is unsafe")
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_mode != after.st_mode
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or size != after.st_size
        ):
            raise SupervisorError("state-aware deploy source changed while loading")
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != after.st_dev
            or current.st_ino != after.st_ino
        ):
            raise SupervisorError("state-aware deploy source path changed while loading")
        source = b"".join(chunks)
        identity = (
            digest.hexdigest(),
            size,
            stat.S_IMODE(after.st_mode),
            int(after.st_dev),
            int(after.st_ino),
        )
        module_name = f"{__name__}._source_exact_state_aware_deploy"
        module = types.ModuleType(module_name)
        module.__file__ = str(path)
        module.__package__ = ""
        sys.modules[module_name] = module
        try:
            code = compile(source, str(path), "exec", dont_inherit=True, optimize=0)
            exec(code, module.__dict__)
        except BaseException as exc:
            sys.modules.pop(module_name, None)
            raise SupervisorError("state-aware deploy source cannot be loaded") from exc
        return module, identity
    finally:
        os.close(descriptor)


state_aware_deploy, _LOADED_STATE_AWARE_IDENTITY = _load_state_aware_source_exact(
    _STATE_AWARE_MODULE_FILE
)
ACTIVATION_GROUP = state_aware_deploy.ACTIVATION_GROUP
StateAwareRealOps = state_aware_deploy.RealOps


@dataclasses.dataclass(frozen=True)
class Config:
    operation_id: str
    state_dir: Path
    deployed_dir: Path
    candidate_dir: Path
    unit_file: Path
    candidate_unit_file: Path
    deploy_tool: Path
    command_file: Path
    child_backup_root: Path
    child_lock_file: Path
    supervisor_service: str
    supervisor_unit_file: Path
    python_executable: str
    service: str
    port: int
    child_timeout_seconds: float = DEFAULT_CHILD_TIMEOUT_SECONDS
    membership_store_file: Path | None = None
    history_index_db_file: Path | None = None


class Ops(Protocol):
    def preflight(self, config: Config) -> None: ...

    def stop(self, service: str) -> None: ...

    def start(self, service: str) -> None: ...

    def daemon_reload(self) -> None: ...

    def health(self, *, port: int) -> None: ...


class Runner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        state_dir: Path,
        timeout: float,
        deploy_tool_descriptor: int,
    ) -> int: ...


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise SupervisorError("JSON object keys are invalid")
        result[key] = value
    return result


def _read_json(path: Path) -> object:
    try:
        raw = _read_small_regular(path, max_bytes=MAX_JSON_BYTES)
    except SupervisorError as exc:
        raise SupervisorError("private state cannot be read") from exc
    if not raw:
        raise SupervisorError("private state size is invalid")
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_json_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                SupervisorError("non-finite JSON value")
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise SupervisorError("private state is invalid") from exc


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    descriptor, raw_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(raw_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, 0o600)
        os.replace(staged, path)
        _require_private_file(path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        staged.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_private_directory(path: Path) -> None:
    metadata = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        raise SupervisorError("private state path is not a directory")
    if os.name == "posix":
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise SupervisorError("private state directory metadata is unsafe")


def _require_private_file(path: Path) -> None:
    metadata = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise SupervisorError("private state path is not a regular file")
    if os.name == "posix":
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SupervisorError("private state file metadata is unsafe")


def _open_regular(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SupervisorError("deployment file cannot be opened") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SupervisorError("deployment file is not regular")
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != before.st_dev
            or current.st_ino != before.st_ino
        ):
            raise SupervisorError("deployment file identity is unstable")
        return descriptor, before
    except BaseException:
        os.close(descriptor)
        raise


def _read_exact_chunk(descriptor: int, requested: int) -> bytes:
    while True:
        try:
            chunk = os.read(descriptor, requested)
        except InterruptedError:
            continue
        except OSError as exc:
            raise SupervisorError("deployment file cannot be read") from exc
        if type(chunk) is not bytes or len(chunk) > requested:
            raise SupervisorError("deployment file read boundary is invalid")
        return chunk


def _open_hashed_regular(
    path: Path,
) -> tuple[int, tuple[str, int, int, int, int]]:
    descriptor, before = _open_regular(path)
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = _read_exact_chunk(descriptor, CHUNK_SIZE)
            if chunk == b"":
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_mode != after.st_mode
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or size != after.st_size
        ):
            raise SupervisorError("deployment file changed while reading")
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != after.st_dev
            or current.st_ino != after.st_ino
        ):
            raise SupervisorError("deployment file path changed after reading")
        return descriptor, (
            digest.hexdigest(),
            size,
            stat.S_IMODE(after.st_mode),
            int(after.st_dev),
            int(after.st_ino),
        )
    except BaseException:
        os.close(descriptor)
        raise


def _hash_regular(path: Path) -> tuple[str, int, int]:
    descriptor, identity = _open_hashed_regular(path)
    try:
        return identity[:3]
    finally:
        os.close(descriptor)


def _persistent_file_identity(path: Path) -> tuple[str, int, int, int, int]:
    descriptor, identity = _open_hashed_regular(path)
    try:
        return identity
    finally:
        os.close(descriptor)


def _read_small_regular(path: Path, *, max_bytes: int) -> bytes:
    descriptor, before = _open_regular(path)
    chunks: list[bytes] = []
    size = 0
    try:
        while True:
            requested = min(CHUNK_SIZE, max_bytes + 1 - size)
            chunk = _read_exact_chunk(descriptor, requested)
            if chunk == b"":
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise SupervisorError("deployment file is too large")
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_mode != after.st_mode
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or size != after.st_size
        ):
            raise SupervisorError("deployment file changed while reading")
    finally:
        os.close(descriptor)
    current = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
    ):
        raise SupervisorError("deployment file path changed after reading")
    return b"".join(chunks)


def _copy_regular_exclusive(source: Path, target: Path) -> tuple[str, int, int]:
    descriptor, before = _open_regular(source)
    digest = hashlib.sha256()
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(target.parent, 0o700)
    try:
        out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        os.close(descriptor)
        raise SupervisorError("private backup cannot be created") from exc
    try:
        with os.fdopen(out, "wb") as stream:
            while True:
                chunk = _read_exact_chunk(descriptor, CHUNK_SIZE)
                if chunk == b"":
                    break
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_mode != after.st_mode
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or size != after.st_size
        ):
            raise SupervisorError("deployment file changed during backup")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    current = os.stat(source, follow_symlinks=False)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
    ):
        target.unlink(missing_ok=True)
        raise SupervisorError("deployment file path changed during backup")
    os.chmod(target, 0o600)
    _require_private_file(target)
    _fsync_directory(target.parent)
    target_sha, target_size, target_mode = _hash_regular(target)
    source_mode = stat.S_IMODE(before.st_mode)
    if (
        target_sha != digest.hexdigest()
        or target_size != size
        or (os.name == "posix" and target_mode != 0o600)
    ):
        target.unlink(missing_ok=True)
        raise SupervisorError("private backup verification failed")
    return digest.hexdigest(), size, source_mode


def _stat_no_follow(path: Path) -> os.stat_result | None:
    try:
        return os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SupervisorError("deployment path cannot be inspected") from exc


def _path_is_absent(path: Path) -> bool:
    return _stat_no_follow(path) is None


def _atomic_restore(source: Path, target: Path, *, expected_sha: str, mode: int) -> None:
    source_sha, _size, _source_mode = _hash_regular(source)
    if source_sha != expected_sha:
        raise SupervisorError("private backup digest mismatch")
    if target.exists() or target.is_symlink():
        current = os.stat(target, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode):
            raise SupervisorError("deployment target is unsafe")
    descriptor, raw_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    staged = Path(raw_name)
    source_descriptor, source_before = _open_regular(source)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            while True:
                chunk = _read_exact_chunk(source_descriptor, CHUNK_SIZE)
                if chunk == b"":
                    break
                stream.write(chunk)
                digest.update(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if digest.hexdigest() != expected_sha:
            raise SupervisorError("restored bytes changed while staging")
        source_after = os.fstat(source_descriptor)
        if (
            source_before.st_dev != source_after.st_dev
            or source_before.st_ino != source_after.st_ino
            or source_before.st_mode != source_after.st_mode
            or source_before.st_size != source_after.st_size
            or source_before.st_mtime_ns != source_after.st_mtime_ns
            or source_before.st_ctime_ns != source_after.st_ctime_ns
        ):
            raise SupervisorError("private backup changed while restoring")
        source_current = os.stat(source, follow_symlinks=False)
        if (
            not stat.S_ISREG(source_current.st_mode)
            or source_current.st_dev != source_after.st_dev
            or source_current.st_ino != source_after.st_ino
        ):
            raise SupervisorError("private backup path changed while restoring")
        os.chmod(staged, mode)
        os.replace(staged, target)
        _fsync_directory(target.parent)
    finally:
        os.close(source_descriptor)
        if descriptor >= 0:
            os.close(descriptor)
        staged.unlink(missing_ok=True)
    current_sha, _size, current_mode = _hash_regular(target)
    if current_sha != expected_sha or current_mode != mode:
        raise SupervisorError("restored deployment file verification failed")


def _unit_epoch(path: Path, option: str) -> int:
    try:
        text = _read_small_regular(path, max_bytes=1024 * 1024).decode(
            "utf-8",
            errors="strict",
        )
    except (SupervisorError, UnicodeError) as exc:
        raise SupervisorError("systemd unit is not valid UTF-8") from exc
    occurrences = re.findall(rf"(?<!\S){re.escape(option)}(?=$|[=\s])", text)
    if not occurrences:
        return 0
    matches = re.findall(
        rf"(?<!\S){re.escape(option)}(?:=|\s+)([^\s]+)(?!\S)",
        text,
    )
    if len(occurrences) != 1 or len(matches) != 1:
        raise SupervisorError("systemd unit epoch option is ambiguous")
    raw = matches[0]
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise SupervisorError("systemd unit epoch is invalid")
    value = int(raw)
    if value > 2**31 - 1:
        raise SupervisorError("systemd unit epoch is invalid")
    return value


def _validate_equal_epoch_boundary(config: Config) -> None:
    for option in _UNIT_EPOCH_OPTIONS:
        if _unit_epoch(config.unit_file, option) != _unit_epoch(
            config.candidate_unit_file, option
        ):
            raise SupervisorError("external recovery cannot cover an epoch change")


def _identity_json(identity: tuple[str, int, int, int, int]) -> dict[str, object]:
    digest, size, mode, device, inode = identity
    return {
        "sha256": digest,
        "size": size,
        "mode": mode,
        "device": device,
        "inode": inode,
    }


def _validate_identity_json(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != {
        "sha256",
        "size",
        "mode",
        "device",
        "inode",
    }:
        raise SupervisorError("deploy tool identity record is invalid")
    digest = value["sha256"]
    size = value["size"]
    mode = value["mode"]
    device = value["device"]
    inode = value["inode"]
    if (
        type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or type(size) is not int
        or size < 0
        or type(mode) is not int
        or not 0 <= mode <= 0o777
        or type(device) is not int
        or device < 0
        or type(inode) is not int
        or inode <= 0
    ):
        raise SupervisorError("deploy tool identity record is invalid")
    return dict(value)


def _deploy_tool_identity(config: Config) -> dict[str, object]:
    module_identity = _persistent_file_identity(_STATE_AWARE_MODULE_FILE)
    child_identity = _persistent_file_identity(config.deploy_tool)
    if module_identity != _LOADED_STATE_AWARE_IDENTITY:
        raise SupervisorError("supervisor runtime deploy module changed")
    if child_identity[:3] != module_identity[:3]:
        raise SupervisorError("supervisor and child deploy tool identity differ")
    return {
        "runtime_module_path": str(_STATE_AWARE_MODULE_FILE),
        "runtime": _identity_json(module_identity),
        "child": _identity_json(child_identity),
    }


def _validate_deploy_tool_identity_record(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != {
        "runtime_module_path",
        "runtime",
        "child",
    }:
        raise SupervisorError("deploy tool identity record is invalid")
    runtime_path = value["runtime_module_path"]
    if (
        type(runtime_path) is not str
        or not runtime_path
        or Path(runtime_path).resolve() != _STATE_AWARE_MODULE_FILE
    ):
        raise SupervisorError("deploy tool identity record is invalid")
    return {
        "runtime_module_path": runtime_path,
        "runtime": _validate_identity_json(value["runtime"]),
        "child": _validate_identity_json(value["child"]),
    }


def _identity_tuple(value: object) -> tuple[str, int, int, int, int]:
    identity = _validate_identity_json(value)
    return (
        str(identity["sha256"]),
        int(identity["size"]),
        int(identity["mode"]),
        int(identity["device"]),
        int(identity["inode"]),
    )


def _open_prepared_deploy_tool(
    config: Config,
    state: dict[str, object],
) -> int:
    identity_record = _validate_deploy_tool_identity_record(
        state["deploy_tool_identity"]
    )
    descriptor, actual = _open_hashed_regular(config.deploy_tool)
    try:
        if actual != _identity_tuple(identity_record["child"]):
            raise SupervisorError("prepared child deploy tool changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _config_fingerprint(config: Config) -> str:
    value = {
        "candidate_dir": str(config.candidate_dir.resolve()),
        "candidate_unit_file": str(config.candidate_unit_file.resolve()),
        "child_backup_root": str(config.child_backup_root.resolve()),
        "child_lock_file": str(config.child_lock_file.resolve()),
        "child_timeout_seconds": config.child_timeout_seconds,
        "command_file": str(config.command_file.resolve()),
        "deployed_dir": str(config.deployed_dir.resolve()),
        "deploy_tool": str(config.deploy_tool.resolve()),
        "operation_id": config.operation_id,
        "port": config.port,
        "python_executable": str(Path(config.python_executable).resolve()),
        "service": config.service,
        "supervisor_service": config.supervisor_service,
        "supervisor_unit_file": str(config.supervisor_unit_file.resolve()),
        "unit_file": str(config.unit_file.resolve()),
        "membership_store_file": (str(config.membership_store_file.absolute())
                                  if config.membership_store_file is not None else None),
        "history_index_db_file": (str(config.history_index_db_file.absolute())
                                 if config.history_index_db_file is not None else None),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_paths(config: Config, *, candidate: bool) -> dict[str, Path]:
    root = config.candidate_dir if candidate else config.deployed_dir
    values = {name: root / name for name in ACTIVATION_GROUP}
    values["systemd-unit"] = (
        config.candidate_unit_file if candidate else config.unit_file
    )
    return values


def _snapshot_files(paths: dict[str, Path]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name, path in paths.items():
        digest, size, mode = _hash_regular(path)
        result[name] = {
            "sha256": digest,
            "size": size,
            "source_mode": mode,
            "deployed_mode": DEPLOYED_FILE_MODE,
        }
    return result


def _snapshot_old_files(
    paths: dict[str, Path],
    *,
    files_dir: Path,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name, path in paths.items():
        metadata = _stat_no_follow(path)
        if metadata is None:
            if name == "systemd-unit":
                raise SupervisorError("deployed systemd unit is absent")
            result[name] = {"present": False}
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise SupervisorError("old deployment path is unsafe")
        digest, size, mode = _copy_regular_exclusive(path, files_dir / name)
        result[name] = {
            "present": True,
            "sha256": digest,
            "size": size,
            "mode": mode,
        }
    return result


def _validate_absent_restore_boundary(
    config: Config,
    old_files: dict[str, dict[str, object]],
) -> None:
    if not any(entry["present"] is False for entry in old_files.values()):
        return
    removed_dir = config.state_dir / "removed"
    removed_metadata = _stat_no_follow(removed_dir)
    if removed_metadata is None or not stat.S_ISDIR(removed_metadata.st_mode):
        raise SupervisorError("removed-file state path is unsafe")
    deployed_metadata = _stat_no_follow(config.deployed_dir)
    if (
        deployed_metadata is None
        or not stat.S_ISDIR(deployed_metadata.st_mode)
        or deployed_metadata.st_dev != removed_metadata.st_dev
    ):
        raise SupervisorError("old-absent rollback is not atomic on this filesystem")
    if any(
        entry["present"] is False and name == "systemd-unit"
        for name, entry in old_files.items()
    ):
        raise SupervisorError("deployed systemd unit cannot be absent")


def _child_identity_is_unused(config: Config) -> None:
    if (
        config.child_backup_root.exists()
        or config.child_backup_root.is_symlink()
        or config.child_lock_file.exists()
        or config.child_lock_file.is_symlink()
    ):
        raise SupervisorError("child operation identity is already consumed")


def _command_fingerprint(command: Sequence[str]) -> str:
    if type(command) is not list or any(type(item) is not str for item in command):
        raise SupervisorError("deploy command identity is invalid")
    payload = json.dumps(
        command,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _prepare(
    config: Config,
    command: list[str] | None = None,
) -> dict[str, object]:
    _validate_equal_epoch_boundary(config)
    _child_identity_is_unused(config)
    if command is None:
        command = _load_command(config)
    membership_before = (state_aware_deploy.membership_boundary(config.membership_store_file,
                                                              config.candidate_dir)
                         if config.membership_store_file is not None else None)
    deploy_tool_identity = _deploy_tool_identity(config)
    config.state_dir.mkdir(parents=True, exist_ok=False)
    if os.name == "posix":
        os.chmod(config.state_dir, 0o700)
    _require_private_directory(config.state_dir)
    files_dir = config.state_dir / "files"
    files_dir.mkdir()
    if os.name == "posix":
        os.chmod(files_dir, 0o700)
    _require_private_directory(files_dir)
    removed_dir = config.state_dir / "removed"
    removed_dir.mkdir()
    if os.name == "posix":
        os.chmod(removed_dir, 0o700)
    _require_private_directory(removed_dir)
    old_files = _snapshot_old_files(
        _file_paths(config, candidate=False),
        files_dir=files_dir,
    )
    _validate_absent_restore_boundary(config, old_files)
    candidate_files = _snapshot_files(_file_paths(config, candidate=True))
    if not _matches_old(config, old_files):
        raise SupervisorError("old deployment bytes changed during preparation")
    state: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": config.operation_id,
        "config_sha256": _config_fingerprint(config),
        "command_sha256": _command_fingerprint(command),
        "deploy_tool_identity": deploy_tool_identity,
        "phase": "prepared",
        "old_files": old_files,
        "candidate_files": candidate_files,
        "child_returncode": None,
        "membership_before": membership_before,
    }
    _write_json_atomic(config.state_dir / "operation.json", state)
    return state


def _validate_present_file_entry(entry: object) -> dict[str, object]:
    if type(entry) is not dict or set(entry) != {"sha256", "size", "mode"}:
        raise SupervisorError("operation file entry is invalid")
    digest = entry["sha256"]
    size = entry["size"]
    mode = entry["mode"]
    if (
        type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or type(size) is not int
        or size < 0
        or type(mode) is not int
        or mode < 0
        or mode > 0o777
    ):
        raise SupervisorError("operation file metadata is invalid")
    return {"sha256": digest, "size": size, "mode": mode}


def _validate_file_table(value: object) -> dict[str, dict[str, object]]:
    expected = set((*ACTIVATION_GROUP, "systemd-unit"))
    if type(value) is not dict or set(value) != expected:
        raise SupervisorError("operation file table is invalid")
    result: dict[str, dict[str, object]] = {}
    for name in sorted(expected):
        entry = value[name]
        if type(entry) is not dict or set(entry) != {
            "sha256",
            "size",
            "source_mode",
            "deployed_mode",
        }:
            raise SupervisorError("candidate operation file entry is invalid")
        digest = entry["sha256"]
        size = entry["size"]
        source_mode = entry["source_mode"]
        deployed_mode = entry["deployed_mode"]
        if (
            type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or type(size) is not int
            or size < 0
            or type(source_mode) is not int
            or source_mode < 0
            or source_mode > 0o777
            or type(deployed_mode) is not int
            or deployed_mode != DEPLOYED_FILE_MODE
        ):
            raise SupervisorError("candidate operation file metadata is invalid")
        result[name] = {
            "sha256": digest,
            "size": size,
            "source_mode": source_mode,
            "deployed_mode": deployed_mode,
        }
    return result


def _validate_old_file_table(value: object) -> dict[str, dict[str, object]]:
    expected = set((*ACTIVATION_GROUP, "systemd-unit"))
    if type(value) is not dict or set(value) != expected:
        raise SupervisorError("old operation file table is invalid")
    result: dict[str, dict[str, object]] = {}
    for name in sorted(expected):
        entry = value[name]
        if type(entry) is not dict or "present" not in entry:
            raise SupervisorError("old operation file entry is invalid")
        present = entry["present"]
        if type(present) is not bool:
            raise SupervisorError("old operation presence marker is invalid")
        if present is False:
            if set(entry) != {"present"} or name == "systemd-unit":
                raise SupervisorError("old absent file entry is invalid")
            result[name] = {"present": False}
            continue
        if set(entry) != {"present", "sha256", "size", "mode"}:
            raise SupervisorError("old operation file entry is invalid")
        validated = _validate_present_file_entry(
            {
                "sha256": entry["sha256"],
                "size": entry["size"],
                "mode": entry["mode"],
            }
        )
        result[name] = {"present": True, **validated}
    return result


def _load_state(config: Config) -> dict[str, object]:
    _require_private_directory(config.state_dir)
    operation_file = config.state_dir / "operation.json"
    _require_private_file(operation_file)
    files_dir = config.state_dir / "files"
    _require_private_directory(files_dir)
    removed_dir = config.state_dir / "removed"
    _require_private_directory(removed_dir)
    value = _read_json(operation_file)
    if type(value) is not dict or set(value) != {
        "schema_version",
        "operation_id",
        "config_sha256",
        "command_sha256",
        "deploy_tool_identity",
        "phase",
        "old_files",
        "candidate_files",
        "child_returncode",
        "membership_before",
    }:
        raise SupervisorError("operation state shape is invalid")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or type(value["operation_id"]) is not str
        or value["operation_id"] != config.operation_id
        or type(value["config_sha256"]) is not str
        or value["config_sha256"] != _config_fingerprint(config)
        or type(value["command_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["command_sha256"]) is None
        or type(value["phase"]) is not str
        or value["phase"] not in _PHASES
        or (
            value["child_returncode"] is not None
            and type(value["child_returncode"]) is not int
        )
    ):
        raise SupervisorError("operation state identity is invalid")
    value["old_files"] = _validate_old_file_table(value["old_files"])
    value["candidate_files"] = _validate_file_table(value["candidate_files"])
    value["deploy_tool_identity"] = _validate_deploy_tool_identity_record(
        value["deploy_tool_identity"]
    )
    for name, entry in value["old_files"].items():
        backup_path = files_dir / name
        if entry["present"] is True:
            _require_private_file(backup_path)
        elif not _path_is_absent(backup_path):
            raise SupervisorError("old absent file has an unexpected backup")
    allowed_removed = {
        name for name, entry in value["old_files"].items() if entry["present"] is False
    }
    actual_removed = {path.name for path in removed_dir.iterdir()}
    if not actual_removed.issubset(allowed_removed):
        raise SupervisorError("removed-file state contains an unexpected entry")
    if value["phase"] != "apply_started" and actual_removed:
        raise SupervisorError("removed-file state is not empty outside recovery")
    for name in actual_removed:
        identity = _persistent_file_identity(removed_dir / name)
        if not _entry_matches_deployed_identity(
            value["candidate_files"][name], identity
        ):
            raise SupervisorError("removed-file state does not match the candidate")
    return value


def _write_phase(
    config: Config,
    state: dict[str, object],
    phase: str,
    *,
    child_returncode: int | None,
) -> None:
    if phase not in _PHASES:
        raise SupervisorError("operation phase is invalid")
    state["phase"] = phase
    state["child_returncode"] = child_returncode
    _write_json_atomic(config.state_dir / "operation.json", state)


def _matches(
    config: Config,
    table: dict[str, dict[str, object]],
    *,
    candidate: bool,
) -> bool:
    for name, path in _file_paths(config, candidate=candidate).items():
        try:
            digest, size, mode = _hash_regular(path)
        except SupervisorError:
            return False
        entry = table[name]
        expected_mode = entry["source_mode"] if candidate else entry["deployed_mode"]
        if (
            digest != entry["sha256"]
            or size != entry["size"]
            or mode != expected_mode
        ):
            return False
    return True


def _matches_old(
    config: Config,
    table: dict[str, dict[str, object]],
) -> bool:
    for name, path in _file_paths(config, candidate=False).items():
        entry = table[name]
        if entry["present"] is False:
            try:
                if not _path_is_absent(path):
                    return False
            except SupervisorError:
                return False
            continue
        try:
            digest, size, mode = _hash_regular(path)
        except SupervisorError:
            return False
        if (
            digest != entry["sha256"]
            or size != entry["size"]
            or mode != entry["mode"]
        ):
            return False
    return True


def _entry_matches_deployed_identity(
    entry: dict[str, object],
    identity: tuple[str, int, int, int, int],
) -> bool:
    return (
        identity[0] == entry["sha256"]
        and identity[1] == entry["size"]
        and identity[2] == entry["deployed_mode"]
    )


def _restore_quarantined_without_clobber(quarantine: Path, target: Path) -> None:
    moved = os.stat(quarantine, follow_symlinks=False)
    try:
        os.link(quarantine, target, follow_symlinks=False)
    except OSError as exc:
        raise SupervisorError("foreign deployment path could not be restored safely") from exc
    restored = os.stat(target, follow_symlinks=False)
    if restored.st_dev != moved.st_dev or restored.st_ino != moved.st_ino:
        raise SupervisorError("foreign deployment path restore identity changed")
    quarantine.unlink()
    _fsync_directory(target.parent)
    _fsync_directory(quarantine.parent)


def _quarantine_candidate_for_old_absence(
    *,
    target: Path,
    quarantine: Path,
    candidate_entry: dict[str, object],
) -> None:
    target_metadata = _stat_no_follow(target)
    quarantine_metadata = _stat_no_follow(quarantine)
    if target_metadata is None:
        if quarantine_metadata is None:
            return
        identity = _persistent_file_identity(quarantine)
        if not _entry_matches_deployed_identity(candidate_entry, identity):
            raise SupervisorError("quarantined deployment file is not the candidate")
        return
    if quarantine_metadata is not None:
        raise SupervisorError("old-absent rollback quarantine is ambiguous")
    identity = _persistent_file_identity(target)
    if not _entry_matches_deployed_identity(candidate_entry, identity):
        raise SupervisorError("old-absent deployment target is not the candidate")
    try:
        os.rename(target, quarantine)
    except OSError as exc:
        raise SupervisorError("candidate deployment file cannot be quarantined") from exc
    try:
        moved_identity = _persistent_file_identity(quarantine)
    except SupervisorError:
        _restore_quarantined_without_clobber(quarantine, target)
        raise
    if not _entry_matches_deployed_identity(candidate_entry, moved_identity):
        _restore_quarantined_without_clobber(quarantine, target)
        raise SupervisorError("deployment target changed before quarantine")
    _fsync_directory(target.parent)
    _fsync_directory(quarantine.parent)


def _purge_quarantined_candidate(
    quarantine: Path,
    candidate_entry: dict[str, object],
) -> None:
    if _path_is_absent(quarantine):
        return
    identity = _persistent_file_identity(quarantine)
    if not _entry_matches_deployed_identity(candidate_entry, identity):
        raise SupervisorError("quarantined deployment file changed")
    quarantine.unlink()
    _fsync_directory(quarantine.parent)


def _restore_old(
    config: Config,
    state: dict[str, object],
    ops: Ops,
    *,
    child_returncode: int | None,
) -> str:
    old_files = _validate_old_file_table(state["old_files"])
    candidate_files = _validate_file_table(state["candidate_files"])
    # Once apply_started is durable, healthy loopback plus old on-disk bytes is
    # insufficient proof that the running process loaded the old runtime.  A
    # killed child may have restored files while a candidate process remained
    # alive.  Always establish one explicit old-runtime linearization point:
    # stop, restore the exact old pair, daemon-reload, start once, then probe.
    ops.stop(config.service)
    backup_dir = config.state_dir / "files"
    if config.history_index_db_file is not None:
        try:
            state_aware_deploy.require_history_runtime_recovery(backup_dir, backup_dir / "systemd-unit")
        except state_aware_deploy.DeploymentError as exc:
            raise SupervisorError("history runtime recovery target is incompatible") from exc
    if config.membership_store_file is not None:
        try:
            if not _matches(config, candidate_files, candidate=True):
                raise state_aware_deploy.MembershipRecoveryRequired("membership_compatible_recovery_required")
            state_aware_deploy.require_membership_recovery(
                config.membership_store_file, config.candidate_dir, backup_dir,
                backup_dir / "systemd-unit", config.deployed_dir.parent,
                before=state["membership_before"],
                candidate_unit=config.candidate_unit_file,
            )
        except state_aware_deploy.MembershipRecoveryRequired as exc:
            _write_phase(config, state, "membership_recovery_required", child_returncode=child_returncode)
            raise MembershipRecoveryRequired("membership_compatible_recovery_required") from exc
    removed_dir = config.state_dir / "removed"
    for name, target in _file_paths(config, candidate=False).items():
        entry = old_files[name]
        if entry["present"] is True:
            _atomic_restore(
                backup_dir / name,
                target,
                expected_sha=str(entry["sha256"]),
                mode=int(entry["mode"]),
            )
        else:
            _quarantine_candidate_for_old_absence(
                target=target,
                quarantine=removed_dir / name,
                candidate_entry=candidate_files[name],
            )
    if not _matches_old(config, old_files):
        raise SupervisorError("old deployment pair did not restore exactly")
    for name, entry in old_files.items():
        if entry["present"] is False:
            _purge_quarantined_candidate(
                removed_dir / name,
                candidate_files[name],
            )
    ops.daemon_reload()
    ops.start(config.service)
    ops.health(port=config.port)
    _write_phase(
        config,
        state,
        "old_recovered",
        child_returncode=child_returncode,
    )
    return "old_recovered"


def _load_command(config: Config) -> list[str]:
    _require_private_file(config.command_file)
    value = _read_json(config.command_file)
    if type(value) is not list or not value or len(value) > 128:
        raise SupervisorError("deploy command is invalid")
    command: list[str] = []
    for item in value:
        if (
            type(item) is not str
            or not item
            or len(item) > 4096
            or "\x00" in item
            or "\r" in item
            or "\n" in item
        ):
            raise SupervisorError("deploy command argument is invalid")
        command.append(item)
    if (
        Path(command[0]).resolve() != Path(config.python_executable).resolve()
        or len(command) < 3
        or Path(command[1]).resolve() != config.deploy_tool.resolve()
    ):
        raise SupervisorError("deploy command identity is invalid")
    options: dict[str, str | None] = {}
    index = 2
    while index < len(command):
        option = command[index]
        if not option.startswith("--") or option in options:
            raise SupervisorError("deploy command options are invalid")
        if option == "--apply":
            options[option] = None
            index += 1
            continue
        if option == "--preflight-only" or index + 1 >= len(command):
            raise SupervisorError("deploy command options are invalid")
        argument = command[index + 1]
        if argument.startswith("--"):
            raise SupervisorError("deploy command options are invalid")
        options[option] = argument
        index += 2
    expected = {
        "--repo-dir": str(config.deployed_dir.parent.resolve()),
        "--candidate-dir": str(config.candidate_dir.resolve()),
        "--unit-file": str(config.unit_file.resolve()),
        "--candidate-unit-file": str(config.candidate_unit_file.resolve()),
        "--backup-root": str(config.child_backup_root.resolve()),
        "--lock-file": str(config.child_lock_file.resolve()),
        "--service": config.service,
        "--port": str(config.port),
        "--python-executable": str(Path(config.python_executable).resolve()),
        "--working-directory": str(config.deployed_dir.parent.resolve()),
        "--trial-state-mode": "existing-v2",
        "--upload-security-epoch": str(
            _unit_epoch(config.candidate_unit_file, "--upload-security-epoch")
        ),
        "--min-client-security-epoch": str(
            _unit_epoch(config.candidate_unit_file, "--min-client-security-epoch")
        ),
    }
    _lines, member_tokens = state_aware_deploy._unit_exec_tokens(config.candidate_unit_file)
    _old_lines, old_tokens = state_aware_deploy._unit_exec_tokens(config.unit_file)
    history_options = state_aware_deploy._option_values(member_tokens, "--history-index-db-file")
    old_history = state_aware_deploy._option_values(old_tokens, "--history-index-db-file")
    if old_history:
        previous = state_aware_deploy._resolve_unit_path(
            state_aware_deploy._require_single_option(old_tokens, "--history-index-db-file"),
            working_directory=config.deployed_dir.parent,
        )
        if config.history_index_db_file is None or previous != config.history_index_db_file:
            raise SupervisorError("existing history database cannot be disabled or moved")
    if config.history_index_db_file is not None:
        actual = state_aware_deploy._resolve_unit_path(
            state_aware_deploy._require_single_option(member_tokens, "--history-index-db-file"),
            working_directory=config.deployed_dir.parent,
        )
        if actual != config.history_index_db_file:
            raise SupervisorError("candidate history database identity mismatch")
        expected["--history-index-db-file"] = str(config.history_index_db_file)
        # Derived data must never be pointed at membership or raw state.
        state_aware_deploy._validate_history_location(config, dict(
            (role, path) for role, path, _mutable in state_aware_deploy._protected_paths_from_tokens(
                member_tokens, working_directory=config.deployed_dir.parent)))
    elif history_options:
        raise SupervisorError("candidate history database configuration is missing")
    old_membership = state_aware_deploy._option_values(old_tokens, "--membership-store-file")
    if old_membership:
        old_path = state_aware_deploy._resolve_unit_path(
            state_aware_deploy._require_single_option(old_tokens, "--membership-store-file"),
            working_directory=config.deployed_dir.parent,
        )
        if config.membership_store_file is None or old_path != config.membership_store_file:
            raise SupervisorError("existing membership database cannot be disabled or moved")
    member_options = state_aware_deploy._option_values(member_tokens, "--membership-store-file")
    if config.membership_store_file is not None:
        configured = state_aware_deploy._resolve_unit_path(
            state_aware_deploy._require_single_option(member_tokens, "--membership-store-file"),
            working_directory=config.deployed_dir.parent,
        )
        if configured != config.membership_store_file:
            raise SupervisorError("candidate membership database identity mismatch")
        expected["--membership-store-file"] = str(config.membership_store_file)
    elif member_options:
        raise SupervisorError("candidate membership database configuration is missing")
    allowed_options = {
        *expected,
        "--apply",
        "--server-update-script",
        "--state-file",
        "--upload-identity-db-file",
        "--pet-usage-db-file",
        "--upload-identity-mode",
        "--upload-lease-seconds",
        "--min-client-version",
        "--v2-min-version",
        "--pet-usage-lease-seconds",
        "--service-user",
        "--service-group",
        "--cutover-at",
        "--new-trial-hours",
        "--grandfather-hours",
        "--legacy-trial-hours",
    }
    if set(options) != allowed_options:
        raise SupervisorError("deploy command option set is invalid")
    if "--apply" not in options or options["--apply"] is not None:
        raise SupervisorError("deploy command apply mode is invalid")
    for option, expected_value in expected.items():
        value = options.get(option)
        if value is None:
            raise SupervisorError("deploy command identity option is missing")
        actual_value = value
        if option in {
            "--repo-dir",
            "--candidate-dir",
            "--unit-file",
            "--candidate-unit-file",
            "--backup-root",
            "--lock-file",
            "--python-executable",
            "--working-directory",
            "--membership-store-file",
            "--history-index-db-file",
        }:
            actual_value = str(Path(value).resolve())
        if actual_value != expected_value:
            raise SupervisorError("deploy command identity option mismatch")
    return command


def _validate_config(config: Config) -> None:
    if (
        type(config.operation_id) is not str
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{7,127}", config.operation_id) is None
        or type(config.service) is not str
        or re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", config.service) is None
        or type(config.supervisor_service) is not str
        or re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", config.supervisor_service) is None
        or type(config.port) is not int
        or not 1 <= config.port <= 65535
        or type(config.child_timeout_seconds) is not float
        or not 1.0 <= config.child_timeout_seconds <= 3600.0
    ):
        raise SupervisorError("supervisor configuration is invalid")
    resolved_paths = (
        config.state_dir,
        config.deployed_dir,
        config.candidate_dir,
        config.unit_file,
        config.candidate_unit_file,
        config.deploy_tool,
        config.command_file,
        config.child_backup_root,
        config.child_lock_file,
        config.supervisor_unit_file,
    )
    if any(not path.is_absolute() for path in resolved_paths):
        raise SupervisorError("supervisor paths must be absolute")
    if config.membership_store_file is not None and not config.membership_store_file.is_absolute():
        raise SupervisorError("membership database path must be absolute")
    if config.history_index_db_file is not None and not config.history_index_db_file.is_absolute():
        raise SupervisorError("history database path must be absolute")
    if config.state_dir == config.child_backup_root:
        raise SupervisorError("supervisor and child backup roots must differ")
    if config.service == config.supervisor_service:
        raise SupervisorError("supervisor and product services must differ")
    resolved = tuple(path.resolve() for path in resolved_paths)
    if len(set(resolved)) != len(resolved):
        raise SupervisorError("supervisor paths must be distinct")
    private_paths = (
        config.state_dir,
        config.child_backup_root,
        config.child_lock_file,
    )
    for private_path in private_paths:
        for product_root in (config.deployed_dir, config.candidate_dir):
            if (
                private_path == product_root
                or private_path.is_relative_to(product_root)
                or product_root.is_relative_to(private_path)
            ):
                raise SupervisorError("private state overlaps product files")
    if (
        config.state_dir.is_relative_to(config.child_backup_root)
        or config.child_backup_root.is_relative_to(config.state_dir)
        or config.child_lock_file.is_relative_to(config.state_dir)
        or config.child_lock_file.is_relative_to(config.child_backup_root)
    ):
        raise SupervisorError("operation state paths overlap")


def supervise(config: Config, ops: Ops, runner: Runner) -> str:
    """Run exactly one apply, or recover after any previously-started attempt."""

    _validate_config(config)
    ops.preflight(config)
    if config.state_dir.exists() or config.state_dir.is_symlink():
        state = _load_state(config)
    else:
        command = _load_command(config)
        state = _prepare(config, command)
    phase = str(state["phase"])
    if phase == "membership_recovery_required":
        ops.stop(config.service)
        raise MembershipRecoveryRequired("membership_compatible_recovery_required")
    if phase == "candidate_deployed":
        candidate_files = _validate_file_table(state["candidate_files"])
        if not _matches(config, candidate_files, candidate=False):
            raise SupervisorError("recorded candidate deployment bytes changed")
        if config.membership_store_file is not None:
            try:
                state_aware_deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
            except Exception:
                return _restore_old(config, state, ops, child_returncode=state["child_returncode"])
        ops.health(port=config.port)
        return phase
    if phase == "old_recovered":
        old_files = _validate_old_file_table(state["old_files"])
        if not _matches_old(config, old_files):
            raise SupervisorError("recorded old deployment bytes changed")
        ops.health(port=config.port)
        return phase
    if phase == "apply_started":
        # A restarted supervisor must never retry the apply.  This is the
        # crash/OOM/SSH-disconnect recovery edge.
        return _restore_old(
            config,
            state,
            ops,
            child_returncode=state["child_returncode"],
        )
    if phase != "prepared":
        raise SupervisorError("operation phase cannot be resumed")
    command = _load_command(config)
    if _command_fingerprint(command) != state["command_sha256"]:
        raise SupervisorError("prepared deploy command changed")
    if _deploy_tool_identity(config) != state["deploy_tool_identity"]:
        raise SupervisorError("prepared child deploy tool changed")
    old_files = _validate_old_file_table(state["old_files"])
    candidate_files = _validate_file_table(state["candidate_files"])
    if not _matches_old(config, old_files):
        raise SupervisorError("prepared old deployment bytes changed")
    if not _matches(config, candidate_files, candidate=True):
        raise SupervisorError("prepared candidate bytes changed")
    _validate_equal_epoch_boundary(config)
    _child_identity_is_unused(config)
    deploy_tool_descriptor = _open_prepared_deploy_tool(config, state)
    try:
        _write_phase(config, state, "apply_started", child_returncode=None)
    except BaseException:
        os.close(deploy_tool_descriptor)
        raise
    try:
        returncode = runner.run(
            command,
            state_dir=config.state_dir,
            timeout=config.child_timeout_seconds,
            deploy_tool_descriptor=deploy_tool_descriptor,
        )
    except Exception:
        state = _load_state(config)
        return _restore_old(
            config,
            state,
            ops,
            child_returncode=None,
        )
    finally:
        os.close(deploy_tool_descriptor)
    if type(returncode) is not int or not -(2**31) <= returncode < 2**31:
        state = _load_state(config)
        return _restore_old(
            config,
            state,
            ops,
            child_returncode=None,
        )
    state = _load_state(config)
    if returncode == 0:
        candidate_files = _validate_file_table(state["candidate_files"])
        if _matches(config, candidate_files, candidate=False):
            try:
                ops.health(port=config.port)
                if config.membership_store_file is not None:
                    state_aware_deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
            except Exception:
                pass
            else:
                _write_phase(
                    config,
                    state,
                    "candidate_deployed",
                    child_returncode=0,
                )
                return "candidate_deployed"
    return _restore_old(
        config,
        state,
        ops,
        child_returncode=returncode,
    )


class RealOps:
    def __init__(self) -> None:
        self._state_aware = StateAwareRealOps()

    def preflight(self, config: Config) -> None:
        names = (
            "MainPID",
            "User",
            "Group",
            "Type",
            "KillMode",
            "OOMPolicy",
            "OOMScoreAdjust",
            "Restart",
            "RestartUSec",
            "RemainAfterExit",
            "TimeoutStartUSec",
            "StartLimitBurst",
            "StartLimitIntervalUSec",
            "FragmentPath",
            "DropInPaths",
        )
        values: dict[str, str] = {}
        for name in names:
            completed = subprocess.run(
                [
                    "systemctl",
                    "show",
                    config.supervisor_service,
                    "-p",
                    name,
                    "--value",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30.0,
            )
            values[name] = completed.stdout.strip()
        _validate_supervisor_properties(config, values)

    def stop(self, service: str) -> None:
        self._state_aware.stop(service)

    def start(self, service: str) -> None:
        self._state_aware.start(service)

    def daemon_reload(self) -> None:
        self._state_aware.daemon_reload()

    def health(self, *, port: int) -> None:
        self._state_aware.health(port=port)


def _validate_supervisor_properties(
    config: Config,
    values: dict[str, str],
) -> None:
    expected_keys = {
        "MainPID",
        "User",
        "Group",
        "Type",
        "KillMode",
        "OOMPolicy",
        "OOMScoreAdjust",
        "Restart",
        "RestartUSec",
        "RemainAfterExit",
        "TimeoutStartUSec",
        "StartLimitBurst",
        "StartLimitIntervalUSec",
        "FragmentPath",
        "DropInPaths",
    }
    if type(values) is not dict or set(values) != expected_keys:
        raise SupervisorError("supervisor systemd readback is incomplete")
    if any(type(key) is not str or type(value) is not str for key, value in values.items()):
        raise SupervisorError("supervisor systemd readback is invalid")
    try:
        main_pid = int(values["MainPID"])
        oom_score = int(values["OOMScoreAdjust"])
    except ValueError as exc:
        raise SupervisorError("supervisor systemd readback is invalid") from exc
    try:
        fragment = Path(values["FragmentPath"]).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise SupervisorError("supervisor systemd fragment is invalid") from exc
    if (
        main_pid != os.getpid()
        or values["User"] != "root"
        or values["Group"] != "root"
        or values["Type"] != "oneshot"
        or values["KillMode"] != "control-group"
        or values["OOMPolicy"] != "stop"
        or oom_score != -900
        or values["Restart"] != "on-failure"
        or values["RestartUSec"] != "2s"
        or values["RemainAfterExit"] != "no"
        or values["TimeoutStartUSec"] != "15min"
        or values["StartLimitBurst"] != "3"
        or values["StartLimitIntervalUSec"] != "15min"
        or fragment != config.supervisor_unit_file.resolve()
        or values["DropInPaths"] != ""
    ):
        raise SupervisorError("supervisor systemd boundary is unsafe")
    _hash_regular(config.supervisor_unit_file)


def _set_child_oom_score() -> None:
    # The supervisor is deliberately protected from the OOM killer.  The
    # memory-heavy child must not inherit that protection: otherwise the live
    # collector or another workload can become the preferred victim.  Any
    # failure here aborts Popen before the deployer executes product code.
    Path("/proc/self/oom_score_adj").write_text("0\n", encoding="ascii")


class RealRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        state_dir: Path,
        timeout: float,
        deploy_tool_descriptor: int,
    ) -> int:
        if os.name != "posix":
            raise SupervisorError("descriptor-backed deploy execution is unavailable")
        descriptor_metadata = os.fstat(deploy_tool_descriptor)
        if not stat.S_ISREG(descriptor_metadata.st_mode):
            raise SupervisorError("prepared child deploy descriptor is invalid")
        os.lseek(deploy_tool_descriptor, 0, os.SEEK_SET)
        proc_descriptor = Path(f"/proc/self/fd/{deploy_tool_descriptor}")
        proc_metadata = os.stat(proc_descriptor)
        if (
            proc_metadata.st_dev != descriptor_metadata.st_dev
            or proc_metadata.st_ino != descriptor_metadata.st_ino
        ):
            raise SupervisorError("prepared child deploy descriptor changed")
        pinned_command = list(command)
        pinned_command[1] = str(proc_descriptor)
        stdout_path = state_dir / "child.stdout"
        stderr_path = state_dir / "child.stderr"
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            if os.name == "posix":
                os.chmod(stdout_path, 0o600)
                os.chmod(stderr_path, 0o600)
            process = subprocess.Popen(
                pinned_command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                pass_fds=(deploy_tool_descriptor,),
                start_new_session=True,
                preexec_fn=_set_child_oom_score if os.name == "posix" else None,
            )
            try:
                return process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=30.0)
                return -int(signal.SIGKILL)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not parsed > 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _config_from_args(args: argparse.Namespace) -> Config:
    return Config(
        operation_id=args.operation_id,
        state_dir=Path(args.state_dir).resolve(),
        deployed_dir=Path(args.deployed_dir).resolve(),
        candidate_dir=Path(args.candidate_dir).resolve(),
        unit_file=Path(args.unit_file).resolve(),
        candidate_unit_file=Path(args.candidate_unit_file).resolve(),
        deploy_tool=Path(args.deploy_tool).resolve(),
        command_file=Path(args.command_file).resolve(),
        child_backup_root=Path(args.child_backup_root).resolve(),
        child_lock_file=Path(args.child_lock_file).resolve(),
        supervisor_service=args.supervisor_service,
        supervisor_unit_file=Path(args.supervisor_unit_file).resolve(),
        python_executable=args.python_executable,
        service=args.service,
        port=args.port,
        child_timeout_seconds=args.child_timeout_seconds,
        membership_store_file=(Path(args.membership_store_file).absolute()
                               if args.membership_store_file else None),
        history_index_db_file=(Path(args.history_index_db_file).absolute()
                               if args.history_index_db_file else None),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="supervise one state-aware activation deployment"
    )
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--deployed-dir", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--unit-file", required=True)
    parser.add_argument("--candidate-unit-file", required=True)
    parser.add_argument("--deploy-tool", required=True)
    parser.add_argument("--command-file", required=True)
    parser.add_argument("--membership-store-file")
    parser.add_argument("--history-index-db-file")
    parser.add_argument("--child-backup-root", required=True)
    parser.add_argument("--child-lock-file", required=True)
    parser.add_argument("--supervisor-service", required=True)
    parser.add_argument("--supervisor-unit-file", required=True)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--service", default="bidking-collector")
    parser.add_argument("--port", type=_positive_int, default=18900)
    parser.add_argument(
        "--child-timeout-seconds",
        type=_positive_float,
        default=DEFAULT_CHILD_TIMEOUT_SECONDS,
    )
    args = parser.parse_args(argv)
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print(
            json.dumps({"ok": False, "reason": "external_recovery_error"}),
            file=sys.stderr,
        )
        return 2
    config = _config_from_args(args)
    try:
        outcome = supervise(config, RealOps(), RealRunner())
    except MembershipRecoveryRequired:
        print(json.dumps({"ok": False, "reason": "membership_compatible_recovery_required"}), file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - fixed privacy-safe operator boundary
        print(
            json.dumps({"ok": False, "reason": "external_recovery_error"}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"ok": True, "outcome": outcome}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
