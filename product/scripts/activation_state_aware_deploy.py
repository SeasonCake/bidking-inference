#!/usr/bin/env python3
"""State-aware activation deployment with a lossless trial-state rollback.

This is an explicit operator tool.  It never runs from the service and its
preflight mode does not stop the service or modify code, unit, or trial state.

The fixed-window runtime stores schema-v2 trial records while the hotfix2-r1
runtime accepts legacy integer starts.  Restoring a pre-deployment trials.json
after a failed rollout would discard trials created while the candidate was
live.  This orchestrator therefore downgrades the *current* complete v2 table
with the candidate admin before it restores old code and the old systemd unit.
"""
# ruff: noqa: E721 - authority validation deliberately requires exact builtins

from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import ModuleType
from typing import Iterator, Protocol


ACTIVATION_GROUP = (
    "sample_dashboard.py",
    "build_match_records.py",
    "public_stats.py",
    "activation_core.py",
    "membership_card_store.py",
    "sample_history_index.py",
    "activation_server.py",
    "activation_http_e2ee.py",
    "quote_preferences.py",
    "sample_collector_server.py",
    "activation_deploy_gate.py",
    "pet_usage_db_admin.py",
    "upload_lease.py",
    "upload_identity_store.py",
    "upload_identity_db_admin.py",
    "activation_device_admin.py",
    "my_records.py",
)
TRIAL_ADMIN = "trial_state_admin.py"
EXPECTED_CORE_API = 7
TRIAL_V2_READER_APIS = frozenset({5, 6, 7})
MEMBERSHIP_CONSUMERS = ("activation_core.py", "activation_server.py",
                        "sample_collector_server.py", "membership_card_store.py",
                        "my_records.py", "upload_lease.py")
TRIAL_SCHEMA_VERSION = 2
TRIAL_ADMIN_LOCK_PROTOCOL_VERSION = 3
TRIAL_ADMIN_LOCK_BACKEND = (
    "sqlite-begin-exclusive+claim-link-noreplace"
)
TRIAL_RECORD_KEYS = frozenset(
    {"schema_version", "start", "window_seconds", "last_server_now"}
)
_LEGACY_SHORT_TRIAL_HOURS = 3.0
_UNIFIED_TRIAL_HOURS = 12.0
LOCK_TIMEOUT_SECONDS = 30.0
# 2026-10-11: identified 7.19 GB main+quarantine index alone took 75.187s.
# This is a shared two-endpoint deadline, not a synthetic-startup estimate.
HEALTH_STARTUP_TIMEOUT_SECONDS = 180.0
_HEALTH_POLL_INTERVAL_SECONDS = 1.0
_HEALTH_REQUEST_TIMEOUT_SECONDS = 3.0
_PROTECTED_READ_CHUNK_SIZE = 1024 * 1024
UPLOAD_IDENTITY_SCHEMA_VERSION = 3
_UPLOAD_IDENTITY_MODE_RANK = {
    "disabled": 0,
    "compat": 1,
    "required": 2,
}
TRIAL_STATE_MODES = frozenset({"legacy-migrate", "existing-v2"})
_SERVICE_UMASK = "0077"
_SAFE_STATE_DIRECTORY_MODE = "0700"
_LIVE_AUTHORITY_ROLES = frozenset({"bindings", "paid_starts"})
# These files are written only by O_APPEND-style product paths.  A deploy may
# preserve new suffix records, but it must never accept a rewrite, truncation,
# or inode replacement of the prefix frozen while the service was stopped.
_APPEND_ONLY_PROTECTED_ROLES = frozenset(
    {"samples", "samples_quarantine", "telemetry", "pet_head"}
)
_PROTECTED_PATH_OPTIONS = (
    ("samples", "--out", True),
    ("dashboard_pass", "--dashboard-pass-file", False),
    ("export_key", "--export-key-file", False),
    ("version_manifest", "--version-manifest-file", False),
    ("activation_key", "--activation-key-file", False),
    ("activation_http_e2ee_key", "--activation-http-e2ee-private-key-file", False),
    ("master_key", "--master-key-file", False),
    ("master_key_v2", "--master-key-v2-file", False),
    ("bindings", "--bindings-file", True),
    ("revoked", "--revoked-file", False),
    ("device_blocks", "--device-blocks-file", False),
    ("device_block_key", "--device-block-key-file", False),
    ("telemetry", "--telemetry-file", True),
    ("trials", "--trials-file", True),
    ("pet_db", "--pet-usage-db-file", True),
    ("upload_identity_db", "--upload-identity-db-file", True),
)


class DeploymentError(RuntimeError):
    """Fail-closed deployment or recovery error."""


class MembershipRecoveryRequired(DeploymentError):
    """No compatible recovery target; preserve live authority and stay stopped."""


_PREFLIGHT_FAILURE_STAGES = frozenset(
    {
        "authority_before",
        "candidate_inputs",
        "candidate_runtime",
        "trial_authority",
        "authority_after",
        "immutable_authority_changed",
    }
)


class PreflightStageError(DeploymentError):
    """Privacy-safe preflight failure carrying only a fixed operator stage."""

    def __init__(self, stage: str, detail: str | None = None) -> None:
        self.stage = stage
        super().__init__(detail or stage)


@contextlib.contextmanager
def _preflight_stage(stage: str) -> Iterator[None]:
    try:
        yield
    except PreflightStageError:
        raise
    except Exception as exc:
        raise PreflightStageError(stage, str(exc)) from exc


@dataclasses.dataclass(frozen=True)
class Config:
    repo_dir: Path
    candidate_dir: Path
    server_update_script: Path
    unit_file: Path
    candidate_unit_file: Path
    state_file: Path
    upload_identity_db_file: Path
    upload_identity_mode: str
    upload_security_epoch: int
    upload_lease_seconds: int
    min_client_security_epoch: int
    min_client_version: str
    v2_min_version: str
    pet_usage_db_file: Path
    pet_usage_lease_seconds: int
    service_user: str
    service_group: str
    working_directory: Path
    service: str
    port: int
    cutover_at: int
    new_trial_hours: float
    grandfather_hours: float
    legacy_trial_hours: float
    backup_root: Path
    lock_file: Path
    python_executable: str
    lock_timeout_seconds: float = LOCK_TIMEOUT_SECONDS
    trial_state_mode: str = "legacy-migrate"
    membership_store_file: Path | None = None
    history_index_db_file: Path | None = None

    @property
    def deployed_dir(self) -> Path:
        return self.repo_dir / "scripts"

    @property
    def candidate_admin(self) -> Path:
        return self.candidate_dir / TRIAL_ADMIN


@dataclasses.dataclass(frozen=True)
class FileSnapshot:
    present: bool
    sha256: str | None
    mode: int | None


@dataclasses.dataclass(frozen=True)
class EpochState:
    present: bool
    value: int


@dataclasses.dataclass(frozen=True)
class UnitContract:
    upload_security_epoch: EpochState
    activation_security_epoch: EpochState
    protected_paths: tuple[tuple[str, Path, bool], ...]
    service_user: str | None = None
    service_group: str | None = None


@dataclasses.dataclass(frozen=True)
class TrialPolicy:
    trial_hours: float
    grandfather_hours: float
    cutover_at: int


@dataclasses.dataclass(frozen=True)
class RollbackUnitTarget:
    path: Path
    sha256: str
    upload_identity_mode: str
    upload_security_epoch: int
    activation_security_epoch: int
    trial_policy: TrialPolicy | None


@dataclasses.dataclass(frozen=True)
class ProtectedFileRead:
    sha256: str
    size: int
    metadata: os.stat_result
    ends_with_newline: bool


@dataclasses.dataclass(frozen=True)
class AppendOnlyBoundary:
    present: bool
    sha256: str | None
    size: int
    device: int | None
    inode: int | None
    mode: int | None
    uid: int | None
    gid: int | None


@dataclasses.dataclass
class Transaction:
    backup_dir: Path | None = None
    snapshots: dict[str, FileSnapshot] = dataclasses.field(default_factory=dict)
    migrated: bool = False
    trial_state_already_v2: bool = False
    code_touched: bool = False
    unit_touched: bool = False
    manifest_path: Path | None = None
    trial_state_input_sha256: str | None = None
    trial_state_input_identity: object | None = None
    trial_state_verified: bool = False
    recovered_pending_commit: bool = False
    upload_identity_mode_before: str = "disabled"
    upload_identity_mode_after: str = "disabled"
    upload_security_epoch_before: int = 0
    upload_security_epoch_after: int = 0
    activation_security_epoch_before: int = 0
    activation_security_epoch_after: int = 0
    protected_state_before: dict[str, object] | None = None
    live_authority_presence_before: dict[str, bool] | None = None
    append_only_boundaries_before: dict[str, AppendOnlyBoundary] | None = None
    candidate_may_have_served: bool = False
    membership_backup: dict[str, object] | None = None
    authority_backups: dict[str, object] | None = None


class Ops(Protocol):
    def preflight(self, config: Config) -> None: ...

    def stop(self, service: str) -> None: ...

    def start(self, service: str) -> None: ...

    def daemon_reload(self) -> None: ...

    def unit_readback(self, config: Config) -> dict[str, object]: ...

    def wait_until(self, epoch: int) -> None: ...

    def health(self, *, port: int) -> None: ...

    def checkpoint(self, stage: str) -> None: ...


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _set_loopback_response_timeout(response: object, timeout: float) -> None:
    # urllib's HTTPResponse keeps the connected socket below the buffered
    # response stream.  Reset its timeout after headers arrive so reading the
    # body cannot reuse the larger timeout calculated before connection/setup.
    try:
        sock = response.fp.raw._sock  # type: ignore[attr-defined]  # noqa: SLF001
        settimeout = sock.settimeout
    except AttributeError as exc:
        raise DeploymentError("health response transport is unsupported") from exc
    if not callable(settimeout):
        raise DeploymentError("health response transport is unsupported")
    try:
        settimeout(timeout)
    except (OSError, TypeError, ValueError) as exc:
        raise DeploymentError("health response transport is unsupported") from exc


def _read_regular(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise DeploymentError(f"missing file: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise DeploymentError(f"file must be a non-symlink regular file: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DeploymentError(f"cannot read file: {path}") from exc
    after = path.lstat()
    if (
        not stat.S_ISREG(after.st_mode)
        or path.is_symlink()
        or metadata.st_dev != after.st_dev
        or metadata.st_ino != after.st_ino
        or metadata.st_size != after.st_size
        or len(raw) != after.st_size
    ):
        raise DeploymentError(f"file changed while reading: {path}")
    return raw


def _read_json_object(path: Path) -> tuple[bytes, dict]:
    raw = _read_regular(path)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise DeploymentError(f"invalid UTF-8 JSON: {path}") from exc
    if type(value) is not dict:  # noqa: E721 - authority is exact JSON
        raise DeploymentError(f"JSON root is not an object: {path}")
    return raw, value


def _candidate_paths(config: Config) -> dict[str, Path]:
    paths = {
        name: config.candidate_dir / name
        for name in (*ACTIVATION_GROUP, TRIAL_ADMIN)
    }
    for path in paths.values():
        _read_regular(path)
    return paths


def _option_values(tokens: list[str], name: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == name:
            if index + 1 >= len(tokens):
                raise DeploymentError(f"unit option has no value: {name}")
            values.append(tokens[index + 1])
            index += 2
            continue
        prefix = f"{name}="
        if token.startswith(prefix):
            values.append(token[len(prefix) :])
        index += 1
    return values


def _require_single_option(tokens: list[str], name: str) -> str:
    values = _option_values(tokens, name)
    if len(values) != 1:
        raise DeploymentError(f"candidate unit must contain exactly one {name}")
    return values[0]


def _normalized_unit_lines(raw: bytes) -> list[str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise DeploymentError("candidate unit is not UTF-8") from exc
    lines: list[str] = []
    pending = ""
    for source_line in text.splitlines():
        stripped = source_line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        pending += stripped
        if pending.endswith("\\"):
            pending = pending[:-1] + " "
            continue
        lines.append(pending)
        pending = ""
    if pending:
        raise DeploymentError("candidate unit has unterminated continuation")
    return lines


def _unit_exec_tokens(path: Path) -> tuple[list[str], list[str]]:
    lines = _normalized_unit_lines(_read_regular(path))
    exec_starts = [
        line.split("=", 1)[1].strip()
        for line in lines
        if line.startswith("ExecStart=") and line.split("=", 1)[1].strip()
    ]
    if len(exec_starts) != 1:
        raise DeploymentError("unit must contain exactly one non-empty ExecStart")
    try:
        tokens = shlex.split(exec_starts[0], posix=True)
    except ValueError as exc:
        raise DeploymentError("unit ExecStart cannot be parsed") from exc
    return lines, tokens


def _unit_epoch_state(
    path: Path,
    option: str,
    *,
    missing_ok: bool,
) -> EpochState:
    _lines, tokens = _unit_exec_tokens(path)
    values = _option_values(tokens, option)
    if not values:
        if missing_ok:
            return EpochState(False, 0)
        raise DeploymentError(f"unit {option} missing")
    if len(values) != 1:
        raise DeploymentError(f"unit {option} malformed")
    try:
        epoch = int(values[0])
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeploymentError(f"unit {option} malformed") from exc
    if epoch < 0:
        raise DeploymentError(f"unit {option} malformed")
    return EpochState(True, epoch)


def _unit_trial_policy(path: Path) -> TrialPolicy:
    """Read the effective fixed-window policy from one exact service unit."""

    _lines, tokens = _unit_exec_tokens(path)
    try:
        trial_text = _require_single_option(tokens, "--trial-hours")
        grandfather_text = _require_single_option(
            tokens,
            "--trial-grandfather-hours",
        )
        if (
            re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", trial_text) is None
            or re.fullmatch(
                r"[1-9][0-9]*(?:\.[0-9]+)?",
                grandfather_text,
            )
            is None
        ):
            raise ValueError("trial durations are not canonical positive numbers")
        trial_hours = float(trial_text)
        grandfather_hours = float(grandfather_text)
        cutover_text = _require_single_option(
            tokens,
            "--trial-grandfather-cutover-at",
        )
        if re.fullmatch(r"[1-9][0-9]*", cutover_text) is None:
            raise ValueError("cutover is not a canonical positive integer")
        cutover_at = int(cutover_text)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeploymentError("unit trial policy is malformed") from exc
    if (
        not math.isfinite(trial_hours)
        or not math.isfinite(grandfather_hours)
        or trial_hours <= 0
        or grandfather_hours < trial_hours
    ):
        raise DeploymentError("unit trial policy is malformed")
    return TrialPolicy(
        trial_hours=trial_hours,
        grandfather_hours=grandfather_hours,
        cutover_at=cutover_at,
    )


def _is_unified_twelve_hour_existing_v2(config: Config) -> bool:
    return (
        config.trial_state_mode == "existing-v2"
        and config.new_trial_hours == _UNIFIED_TRIAL_HOURS
        and config.grandfather_hours == _UNIFIED_TRIAL_HOURS
        and config.legacy_trial_hours == _UNIFIED_TRIAL_HOURS
    )


def _require_unified_existing_v2_cutover_state(
    config: Config,
    value: dict,
) -> TrialPolicy | None:
    """Fence the one-way 3/12 -> 12/12 cutover at the authoritative snapshot.

    Preflight gives an early read-only rejection, while ``_migrate`` repeats this
    decision from the snapshot taken under the admin lock after the service stopped.
    A non-empty 3/12 authority requires a separately audited offline normalization;
    it must never be lazily widened by live activation requests.
    """

    if not _is_unified_twelve_hour_existing_v2(config):
        return None
    _v2_expiries_value(value)
    expected = TrialPolicy(
        trial_hours=_UNIFIED_TRIAL_HOURS,
        grandfather_hours=_UNIFIED_TRIAL_HOURS,
        cutover_at=config.cutover_at,
    )
    if _unit_trial_policy(config.candidate_unit_file) != expected:
        raise DeploymentError("candidate trial policy is not unified 12h")
    current = _unit_trial_policy(config.unit_file)
    if current.cutover_at != config.cutover_at:
        raise DeploymentError("installed trial cutover does not match candidate")
    if (
        current.trial_hours == _UNIFIED_TRIAL_HOURS
        and current.grandfather_hours == _UNIFIED_TRIAL_HOURS
    ):
        return current
    if (
        current.trial_hours == _LEGACY_SHORT_TRIAL_HOURS
        and current.grandfather_hours == _UNIFIED_TRIAL_HOURS
    ):
        if type(value) is not dict or value:
            raise DeploymentError(
                "nonempty 3h trial ledger requires audited offline normalization"
            )
        return current
    raise DeploymentError("installed trial policy cannot enter unified 12h mode")


def _unit_upload_security_epoch(path: Path, *, missing_ok: bool) -> int:
    return _unit_epoch_state(
        path,
        "--upload-security-epoch",
        missing_ok=missing_ok,
    ).value


def _unit_upload_identity_mode(path: Path) -> str:
    _lines, tokens = _unit_exec_tokens(path)
    mode = _require_single_option(tokens, "--upload-identity-mode")
    if mode not in _UPLOAD_IDENTITY_MODE_RANK:
        raise DeploymentError("unit upload identity mode is malformed")
    return mode


def _require_single_unit_setting(lines: list[str], name: str) -> str:
    values = [
        line.split("=", 1)[1].strip()
        for line in lines
        if line.startswith(f"{name}=") and line.split("=", 1)[1].strip()
    ]
    if len(values) != 1:
        raise DeploymentError(
            f"candidate unit must contain exactly one non-empty {name}"
        )
    return values[0]


def _unit_service_identity(
    config: Config,
    *,
    path: Path,
) -> dict[str, object]:
    lines, _tokens = _unit_exec_tokens(path)
    state_directories = [
        value
        for line in lines
        if line.startswith("StateDirectory=")
        for value in line.split("=", 1)[1].strip().split()
    ]
    expected_directories = [
        "bidking-activation-anchor",
        "bidking-upload-identity",
    ]
    if config.membership_store_file is not None and "bidking-membership" in state_directories:
        expected_directories.append("bidking-membership")
    if config.history_index_db_file is not None and "bidking-sample-index" in state_directories:
        expected_directories.append("bidking-sample-index")
    if sorted(state_directories) != sorted(expected_directories):
        raise DeploymentError("unit StateDirectory contract mismatch")
    settings = {
        "user": _require_single_unit_setting(lines, "User"),
        "group": _require_single_unit_setting(lines, "Group"),
        "umask": _require_single_unit_setting(lines, "UMask"),
        "state_directory_mode": _require_single_unit_setting(
            lines,
            "StateDirectoryMode",
        ),
        "working_directory": _require_single_unit_setting(
            lines,
            "WorkingDirectory",
        ),
    }
    if settings["user"] != config.service_user:
        raise DeploymentError("unit service user mismatch")
    if settings["group"] != config.service_group:
        raise DeploymentError("unit service group mismatch")
    if settings["umask"] != _SERVICE_UMASK:
        raise DeploymentError("unit UMask must be 0077")
    if settings["state_directory_mode"] != _SAFE_STATE_DIRECTORY_MODE:
        raise DeploymentError("unit StateDirectoryMode must be 0700")
    if Path(str(settings["working_directory"])).resolve() != (
        config.working_directory.resolve()
    ):
        raise DeploymentError("unit working directory mismatch")
    safe_projection = {
        "user": settings["user"],
        "group": settings["group"],
        "umask": settings["umask"],
        "state_directory_mode": settings["state_directory_mode"],
        "state_directories": sorted(state_directories),
        "working_directory_verified": True,
    }
    return {
        "schema_version": 1,
        "fragment_sha256": _sha256(_read_regular(path)),
        "effective_properties_sha256": _sha256(
            json.dumps(
                safe_projection,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ),
        "dropin_count": 0,
    }


def _resolve_unit_path(value: str, *, working_directory: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = working_directory / path
    # Keep the final path component unresolved.  The protected-state reader
    # must inspect/reject a symlink itself instead of silently following it
    # before the no-follow open boundary.
    return Path(os.path.abspath(os.fspath(path)))


def _protected_paths_from_tokens(
    tokens: list[str],
    *,
    working_directory: Path,
) -> tuple[tuple[str, Path, bool], ...]:
    paths: dict[str, tuple[Path, bool]] = {}
    for role, option, mutable in _PROTECTED_PATH_OPTIONS:
        value = _require_single_option(tokens, option)
        paths[role] = (
            _resolve_unit_path(value, working_directory=working_directory),
            mutable,
        )
    bindings, _mutable = paths["bindings"]
    samples, _mutable = paths["samples"]
    pet_db, _mutable = paths["pet_db"]
    upload_db, _mutable = paths["upload_identity_db"]
    paths.update(
        {
            "paid_starts": (bindings.with_name("paid_starts.json"), True),
            "samples_quarantine": (
                samples.with_name("samples_quarantine.jsonl"),
                True,
            ),
            "pet_head": (pet_db.with_name(f"{pet_db.stem}.head.jsonl"), True),
            "upload_identity_db_wal": (
                upload_db.with_name(upload_db.name + "-wal"),
                True,
            ),
            "upload_identity_db_shm": (
                upload_db.with_name(upload_db.name + "-shm"),
                True,
            ),
        }
    )
    membership_options = _option_values(tokens, "--membership-store-file")
    if membership_options:
        member = _resolve_unit_path(_require_single_option(tokens, "--membership-store-file"),
                                    working_directory=working_directory)
        paths["membership_db"] = (member, True)
        paths["membership_projection"] = (member.with_suffix(member.suffix + ".projection.json"), True)
        for suffix in ("-wal", "-shm", "-journal"):
            paths["membership_db" + suffix] = (member.with_name(member.name + suffix), True)
    if _option_values(tokens, "--history-index-db-file"):
        history = _resolve_unit_path(_require_single_option(tokens, "--history-index-db-file"),
                                    working_directory=working_directory)
        paths["history_index_db"] = (history, True)
        for suffix in ("-wal", "-shm", "-journal"):
            paths["history_index_db" + suffix] = (Path(str(history) + suffix), True)
    return tuple(
        (role, path, mutable)
        for role, (path, mutable) in sorted(paths.items())
    )


def _membership_validator(root: Path) -> ModuleType:
    source = root / "membership_card_store.py"
    raw = _read_regular(source)
    name = "_bidking_membership_schema_" + uuid.uuid4().hex
    module = ModuleType(name)
    module.__file__ = str(source)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(source), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    if not callable(getattr(module, "_validate_database", None)):
        raise DeploymentError("candidate membership schema validator unavailable")
    return module


@contextlib.contextmanager
def _readonly_sqlite_copy(path: Path) -> Iterator[Path]:
    """Physical source-stable snapshot; SQLite cache writes stay in private temp.

    SQLite mode=ro can create WAL/SHM caches beside a WAL-mode database. Never
    let an operator's read-only check create root-owned caches in live state.
    Copy the exact DB and existing sidecars, verify source stability, then use
    mode=ro on this private snapshot (including committed WAL frames).
    """
    sources = [path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal", ".projection.json"))]
    before = [_authority_file_identity(source) for source in sources]
    if not before[0][0]:
        raise DeploymentError("SQLite authority is missing")
    if before[2][0] and not before[1][0]:
        raise DeploymentError("SQLite WAL state is incomplete")
    with tempfile.TemporaryDirectory(prefix="bidking-authority-readonly-") as raw:
        directory = Path(raw)
        _require_private_directory(directory)
        for source, snapshot in zip(sources, before):
            if snapshot[0]:
                target = directory / source.name
                _copy_exclusive(source, target)
                if not target.is_file() or _sha256(_read_regular(target)) != snapshot[1]:
                    raise DeploymentError("SQLite snapshot sidecar copy mismatch")
        if [_authority_file_identity(source) for source in sources] != before:
            raise DeploymentError("SQLite source changed while copying")
        yield directory / path.name
        if [_authority_file_identity(source) for source in sources] != before:
            raise DeploymentError("SQLite source changed during observation")


def _authority_file_identity(path: Path) -> tuple:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return (False,)
    if not stat.S_ISREG(before.st_mode):
        raise DeploymentError("authority snapshot source is not regular")
    raw = _read_regular(path)
    after = path.lstat()
    def metadata(value):
        return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if metadata(before) != metadata(after):
        raise DeploymentError("authority identity changed while reading")
    return (True, _sha256(raw), *metadata(after))


def membership_boundary(path: Path, candidate_root: Path) -> dict[str, object]:
    """Read-only deployment observation, not account enumeration or activation."""
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise DeploymentError("membership database unavailable")
    before = path.lstat()
    module = _membership_validator(candidate_root)
    connection = None
    try:
        with _readonly_sqlite_copy(path) as snapshot:
            connection = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True, timeout=5)
            try:
                connection.execute("BEGIN")
                module._validate_database(connection)
                accounts = connection.execute("SELECT count(*) FROM accounts").fetchone()[0]
                claims = connection.execute("SELECT count(*) FROM card_claims").fetchone()[0]
            finally:
                connection.close()
                connection = None
            pending = Path(str(snapshot) + ".projection.json")
            pending_empty = True
            if pending.exists() or pending.is_symlink():
                _raw, value = _read_json_object(pending)
                if (set(value) != {"schema_version", "entries"}
                        or type(value["schema_version"]) is not int or value["schema_version"] != 1
                        or type(value["entries"]) is not dict):
                    raise DeploymentError("membership projection unavailable")
                pending_empty = not value["entries"]
        after = path.lstat()
        if (not stat.S_ISREG(after.st_mode) or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino):
            raise DeploymentError("membership database identity changed")
        return {"schema_version": module.SCHEMA_VERSION, "accounts": accounts,
                "claims": claims, "pending_empty": pending_empty}
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        raise DeploymentError("membership boundary unavailable") from exc
    finally:
        if connection is not None:
            connection.close()


def _validate_membership_location(config: Config, contract: UnitContract) -> None:
    path = config.membership_store_file
    if path is None:
        return
    if (not path.is_absolute() or path.parent.is_symlink() or not path.parent.is_dir()
            or path.is_relative_to(config.working_directory)
            or path.parent in {config.pet_usage_db_file.parent, config.upload_identity_db_file.parent}):
        raise DeploymentError("membership database must have an independent private location")
    if os.name == "posix" and stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise DeploymentError("membership database directory must be 0700")
    if path.is_symlink() or not path.is_file():
        raise DeploymentError("membership database must be explicitly initialized")
    _require_live_authority_metadata(contract, role="membership_db", metadata=path.lstat())
    if os.name == "posix":
        parent, file = path.parent.stat(), path.lstat()
        if parent.st_uid != file.st_uid or parent.st_gid != file.st_gid:
            raise DeploymentError("membership directory owner mismatch")


def require_membership_recovery(path: Path, candidate_root: Path, old_root: Path | None,
                                old_unit: Path | None, working_directory: Path,
                                *, before: dict[str, object] | None,
                                candidate_unit: Path) -> dict[str, object]:
    """Called only after stopping service, before any legacy restore/downgrade."""
    try:
        current = membership_boundary(path, candidate_root)
        if not current["pending_empty"]:
            raise DeploymentError("membership pending projection requires compatible operator recovery")
        if (type(before) is not dict or set(before) != set(current)
                or type(before.get("schema_version")) is not int
                or type(before.get("accounts")) is not int or type(before.get("claims")) is not int
                or type(before.get("pending_empty")) is not bool
                or before["accounts"] < 0 or before["claims"] < 0
                or before["schema_version"] != current["schema_version"]
                or current["accounts"] < before["accounts"] or current["claims"] < before["claims"]):
            raise DeploymentError("membership observation changed or unknown")
        if (before["accounts"] == current["accounts"] == 0
                and before["claims"] == current["claims"] == 0
                and before["pending_empty"] is current["pending_empty"] is True):
            return current
        if old_root is None or old_unit is None:
            raise DeploymentError("membership recovery target missing")
        for name in MEMBERSHIP_CONSUMERS:
            if _read_regular(old_root / name) != _read_regular(candidate_root / name):
                raise DeploymentError("membership recovery consumer mismatch")
        lines, tokens = _unit_exec_tokens(old_unit)
        if Path(_require_single_unit_setting(lines, "WorkingDirectory")).resolve() != working_directory.resolve():
            raise DeploymentError("membership recovery working directory mismatch")
        old_path = _resolve_unit_path(_require_single_option(tokens, "--membership-store-file"),
                                      working_directory=working_directory)
        if old_path != path:
            raise DeploymentError("membership recovery database mismatch")
        _candidate_lines, candidate_tokens = _unit_exec_tokens(candidate_unit)
        if (_protected_paths_from_tokens(tokens, working_directory=working_directory)
                != _protected_paths_from_tokens(candidate_tokens, working_directory=working_directory)):
            raise DeploymentError("membership recovery authority paths mismatch")
        return current
    except Exception as exc:
        raise MembershipRecoveryRequired("membership_compatible_recovery_required") from exc


def _backup_sqlite_authority(path: Path, target: Path) -> None:
    """Consistent private backup only; never a live authority restore."""
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    reader = writer = None
    try:
        with _readonly_sqlite_copy(path) as snapshot:
            reader = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True, timeout=5)
            writer = sqlite3.connect(target)
            try:
                if reader.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                    raise DeploymentError("SQLite authority cannot be backed up")
                started = time.monotonic()
                def progress(_status, _remaining, _total):
                    if time.monotonic() - started > 30:
                        raise DeploymentError("SQLite authority backup deadline exceeded")
                reader.backup(writer, pages=256, progress=progress)
            finally:
                writer.close()
                reader.close()
                writer = reader = None
    finally:
        if writer is not None:
            writer.close()
        if reader is not None:
            reader.close()
    _require_private_file(target)


def _backup_membership(config: Config, directory: Path) -> dict[str, object] | None:
    path = config.membership_store_file
    if path is None:
        return None
    sources = [path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal", ".projection.json"))]
    identities_before = [_authority_file_identity(source) for source in sources]
    boundary = membership_boundary(path, config.candidate_dir)
    target = directory / "membership.before.sqlite3"
    _backup_sqlite_authority(path, target)
    files = {"database": {"sha256": _sha256(_read_regular(target))}}
    for suffix in (".projection.json", "-wal", "-shm", "-journal"):
        source = Path(str(path) + suffix)
        if source.exists() or source.is_symlink():
            backup = (Path(str(target) + suffix) if suffix == ".projection.json"
                      else directory / ("membership.original" + suffix))
            digest = _copy_exclusive(source, backup)
            files[suffix] = {"present": True, "sha256": digest}
        else:
            files[suffix] = {"present": False}
    copied = membership_boundary(target, config.candidate_dir)
    if (copied != boundary
            or membership_boundary(path, config.candidate_dir) != boundary
            or [_authority_file_identity(source) for source in sources] != identities_before):
        raise DeploymentError("membership authority changed during stopped backup")
    return {"boundary": boundary, "files": files}


def _backup_activation_authorities(config: Config, directory: Path) -> dict[str, object]:
    contract = _validate_candidate_unit(config)
    paths = {role: path for role, path, _mutable in contract.protected_paths}
    roles = ("bindings", "paid_starts", "trials", "pet_db", "pet_head", "upload_identity_db")
    # Only the small authoritative ledgers: no raw samples, credentials or keys.
    targets = {role: paths[role] for role in roles}
    for role in ("pet_db", "upload_identity_db"):
        for suffix in ("-wal", "-shm", "-journal"):
            targets[role + suffix] = Path(str(paths[role]) + suffix)
    before = {role: _snapshot_path(path) for role, path in targets.items()}
    identities_before = {role: _authority_file_identity(path) for role, path in targets.items()}
    destination = directory / "authorities"
    destination.mkdir(mode=0o700, exist_ok=False)
    _require_private_directory(destination)
    result = {}
    for role, path in targets.items():
        snapshot = before[role]
        if not snapshot.present:
            _write_absent_marker(destination / (".absent-" + role))
            result[role] = {"present": False}
            continue
        target = destination / (role + (".sqlite3" if role in {"pet_db", "upload_identity_db"} else ".snapshot"))
        if role in {"pet_db", "upload_identity_db"}:
            _backup_sqlite_authority(path, target)
        else:
            _copy_exclusive(path, target)
        result[role] = {"present": True, "file": target.relative_to(directory).as_posix(),
                        "sha256": _sha256(_read_regular(target)), "source_sha256": snapshot.sha256}
    after = {role: _snapshot_path(path) for role, path in targets.items()}
    if before != after or identities_before != {role: _authority_file_identity(path) for role, path in targets.items()}:
        changed = ",".join(role for role in targets if before[role] != after[role])
        raise DeploymentError("stopped activation authority changed during backup: " + changed)
    return result


def _validate_history_location(config: Config, paths: dict[str, Path]) -> None:
    """Derived index is neither membership authority nor a raw-state target.

    A missing private directory is allowed: systemd StateDirectory creates it.
    This check does not initialize, migrate, back up or restore the index.
    """
    path = config.history_index_db_file
    if path is None:
        return
    workdir = getattr(config, "working_directory", config.deployed_dir.parent)
    if (not path.is_absolute() or path.resolve() != path.absolute()
            or path.is_relative_to(workdir.resolve())):
        raise DeploymentError("history database must have an independent private location")
    other_paths = [value for role, value in paths.items()
                   if not role.startswith("history_index_db")]
    if any(path == value or path.parent.is_relative_to(value.parent) for value in other_paths):
        raise DeploymentError("history database overlaps authority or raw state")
    parent = path.parent
    if parent.exists() and (not parent.is_dir() or parent.is_symlink()):
        raise DeploymentError("history database parent is not a private directory")
    if parent.exists() and os.name == "posix" and stat.S_IMODE(parent.stat().st_mode) != 0o700:
        raise DeploymentError("history database directory must be 0700")
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise DeploymentError("history database target must be a regular derived file")


def require_history_runtime_recovery(old_root: Path | None, old_unit: Path | None) -> None:
    """Reject a torn code/unit restore; leave the rebuildable index untouched."""
    try:
        if old_root is None or old_unit is None:
            raise DeploymentError("history recovery target missing")
        uses_helper = False
        collector_tree = None
        for name in ("sample_collector_server.py", "sample_dashboard.py", "public_stats.py", "my_records.py"):
            tree = ast.parse(_read_regular(old_root / name), filename=name)
            if name == "sample_collector_server.py":
                collector_tree = tree
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    uses_helper |= any(alias.name == "sample_history_index" for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    uses_helper |= node.module == "sample_history_index"
        _lines, tokens = _unit_exec_tokens(old_unit)
        history_flag = bool(_option_values(tokens, "--history-index-db-file"))
        if history_flag:
            _require_single_option(tokens, "--history-index-db-file")
            accepts_flag = any(
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and any(isinstance(arg, ast.Constant) and arg.value == "--history-index-db-file"
                        for arg in node.args)
                for node in ast.walk(collector_tree)
            )
            if not accepts_flag:
                raise DeploymentError("history recovery collector does not accept configured flag")
        if uses_helper or history_flag:
            compile(_read_regular(old_root / "sample_history_index.py"),
                    str(old_root / "sample_history_index.py"), "exec")
    except Exception as exc:
        raise DeploymentError("history_compatible_recovery_required") from exc


def _validate_candidate_unit(config: Config) -> UnitContract:
    lines, tokens = _unit_exec_tokens(config.candidate_unit_file)
    state_directories = [
        value
        for line in lines
        if line.startswith("StateDirectory=")
        for value in line.split("=", 1)[1].strip().split()
    ]
    if state_directories.count("bidking-activation-anchor") != 1:
        raise DeploymentError(
            "candidate unit must retain StateDirectory=bidking-activation-anchor"
        )
    if state_directories.count("bidking-upload-identity") != 1:
        raise DeploymentError(
            "candidate unit must contain StateDirectory=bidking-upload-identity"
        )
    expected_count = 3 if config.membership_store_file is not None and "bidking-membership" in state_directories else 2
    if config.history_index_db_file is not None:
        if state_directories.count("bidking-sample-index") != 1:
            raise DeploymentError("candidate unit must contain StateDirectory=bidking-sample-index")
        expected_count += 1
    if len(state_directories) != expected_count:
        raise DeploymentError("candidate unit StateDirectory contract mismatch")
    if not tokens:
        raise DeploymentError("candidate unit ExecStart is empty")
    if _require_single_unit_setting(lines, "User") != config.service_user:
        raise DeploymentError("candidate unit service user mismatch")
    if _require_single_unit_setting(lines, "Group") != config.service_group:
        raise DeploymentError("candidate unit service group mismatch")
    if _require_single_unit_setting(lines, "UMask") != _SERVICE_UMASK:
        raise DeploymentError("candidate unit UMask must be 0077")
    if (
        _require_single_unit_setting(lines, "StateDirectoryMode")
        != _SAFE_STATE_DIRECTORY_MODE
    ):
        raise DeploymentError("candidate unit StateDirectoryMode must be 0700")
    working_directory = Path(
        _require_single_unit_setting(lines, "WorkingDirectory")
    ).resolve()
    if working_directory != config.working_directory.resolve():
        raise DeploymentError("candidate unit working directory mismatch")
    _unit_service_identity(config, path=config.candidate_unit_file)
    expected_collector = (
        config.deployed_dir / "sample_collector_server.py"
    ).resolve()
    collector_tokens = []
    for token in tokens:
        try:
            candidate_path = Path(token).resolve()
        except (OSError, ValueError):
            continue
        if candidate_path == expected_collector:
            collector_tokens.append(token)
    if len(collector_tokens) != 1:
        raise DeploymentError(
            "candidate unit must execute the deployed sample collector"
        )
    try:
        trial_hours = float(_require_single_option(tokens, "--trial-hours"))
        grandfather_hours = float(
            _require_single_option(tokens, "--trial-grandfather-hours")
        )
        cutover = int(
            _require_single_option(tokens, "--trial-grandfather-cutover-at")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeploymentError("candidate unit trial options are malformed") from exc
    if trial_hours != config.new_trial_hours:
        raise DeploymentError("candidate unit new trial duration mismatch")
    if grandfather_hours != config.grandfather_hours:
        raise DeploymentError("candidate unit grandfather duration mismatch")
    if cutover != config.cutover_at:
        raise DeploymentError("candidate unit cutover mismatch")
    configured_state = _resolve_unit_path(
        _require_single_option(tokens, "--trials-file"),
        working_directory=working_directory,
    )
    if configured_state != Path(os.path.abspath(config.state_file)):
        raise DeploymentError("candidate unit trials file mismatch")
    upload_mode = _require_single_option(tokens, "--upload-identity-mode")
    if upload_mode != config.upload_identity_mode or upload_mode not in {
        "disabled",
        "compat",
        "required",
    }:
        raise DeploymentError("candidate unit upload identity mode mismatch")
    configured_upload_db = _resolve_unit_path(
        _require_single_option(tokens, "--upload-identity-db-file"),
        working_directory=working_directory,
    )
    if configured_upload_db != Path(os.path.abspath(config.upload_identity_db_file)):
        raise DeploymentError("candidate unit upload identity database mismatch")
    try:
        upload_epoch = int(_require_single_option(tokens, "--upload-security-epoch"))
        upload_lease_seconds = int(
            _require_single_option(tokens, "--upload-lease-seconds")
        )
        activation_epoch = int(
            _require_single_option(tokens, "--min-client-security-epoch")
        )
        pet_usage_lease_seconds = int(
            _require_single_option(tokens, "--pet-usage-lease-seconds")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeploymentError("candidate unit security option is malformed") from exc
    if upload_epoch != config.upload_security_epoch or upload_epoch < 0:
        raise DeploymentError("candidate unit upload security epoch mismatch")
    if upload_mode == "disabled" and upload_epoch != 0:
        raise DeploymentError("disabled upload identity requires epoch zero")
    if upload_mode != "disabled" and upload_epoch <= 0:
        raise DeploymentError("enabled upload identity requires a positive epoch")
    if (
        upload_lease_seconds != config.upload_lease_seconds
        or not 1 <= upload_lease_seconds <= 900
    ):
        raise DeploymentError("candidate unit upload lease seconds mismatch")
    if (
        activation_epoch != config.min_client_security_epoch
        or activation_epoch < 0
    ):
        raise DeploymentError("candidate unit activation security epoch mismatch")
    if _require_single_option(tokens, "--min-client-version") != config.min_client_version:
        raise DeploymentError("candidate unit minimum client version mismatch")
    if _require_single_option(tokens, "--v2-min-version") != config.v2_min_version:
        raise DeploymentError("candidate unit v2 minimum version mismatch")
    configured_pet_db = _resolve_unit_path(
        _require_single_option(tokens, "--pet-usage-db-file"),
        working_directory=working_directory,
    )
    if configured_pet_db != config.pet_usage_db_file.resolve():
        raise DeploymentError("candidate unit Pet database mismatch")
    if (
        pet_usage_lease_seconds != config.pet_usage_lease_seconds
        or not 5 <= pet_usage_lease_seconds <= 60
    ):
        raise DeploymentError("candidate unit Pet lease seconds mismatch")
    ingest_keys = _option_values(tokens, "--key")
    if len(ingest_keys) != 1 or not ingest_keys[0]:
        raise DeploymentError("candidate unit ingest authentication is not explicit")
    protected_paths = _protected_paths_from_tokens(
        tokens,
        working_directory=working_directory,
    )
    expected_paths = {
        "trials": Path(os.path.abspath(config.state_file)),
        "pet_db": Path(os.path.abspath(config.pet_usage_db_file)),
        "upload_identity_db": Path(os.path.abspath(config.upload_identity_db_file)),
    }
    member_options = _option_values(tokens, "--membership-store-file")
    if config.membership_store_file is None:
        if member_options:
            raise DeploymentError("candidate unit has unselected membership database")
    else:
        expected_paths["membership_db"] = Path(os.path.abspath(config.membership_store_file))
        _require_single_option(tokens, "--membership-store-file")
    history_options = _option_values(tokens, "--history-index-db-file")
    if config.history_index_db_file is None:
        if history_options:
            raise DeploymentError("candidate unit has unselected history database")
    else:
        _require_single_option(tokens, "--history-index-db-file")
        expected_paths["history_index_db"] = Path(os.path.abspath(config.history_index_db_file))
    actual_paths = {role: path for role, path, _mutable in protected_paths}
    for role, expected in expected_paths.items():
        if actual_paths.get(role) != expected:
            raise DeploymentError(f"candidate unit protected path mismatch: {role}")
    _validate_history_location(config, actual_paths)
    return UnitContract(
        upload_security_epoch=EpochState(True, upload_epoch),
        activation_security_epoch=EpochState(True, activation_epoch),
        protected_paths=protected_paths,
        service_user=config.service_user,
        service_group=config.service_group,
    )


def _run_checked(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError(f"command failed to start: {command[0]}") from exc
    if completed.returncode != 0:
        raise DeploymentError(
            f"command failed: {Path(command[0]).name} "
            f"exit={completed.returncode}"
        )
    return completed


def _parse_receipt(completed: subprocess.CompletedProcess[str], mode: str) -> dict:
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise DeploymentError(f"{mode} produced no receipt")
    try:
        receipt = json.loads(lines[-1])
    except ValueError as exc:
        raise DeploymentError(f"{mode} receipt is not JSON") from exc
    if (
        type(receipt) is not dict  # noqa: E721
        or receipt.get("ok") is not True
        or receipt.get("mode") != mode
        or receipt.get("lock_backend") != TRIAL_ADMIN_LOCK_BACKEND
        or receipt.get("lock_protocol_version")
        != TRIAL_ADMIN_LOCK_PROTOCOL_VERSION
    ):
        raise DeploymentError(f"{mode} receipt is invalid")
    return receipt


def _run_admin(
    config: Config,
    *,
    mode: str,
    state_file: Path,
    expected_sha256: str | None = None,
    backup_file: Path | None = None,
    apply: bool,
) -> dict:
    command = [
        config.python_executable,
        str(config.candidate_admin),
        "--state-file",
        str(state_file),
        "--mode",
        mode,
    ]
    if mode == "migrate":
        command.extend(
            [
                "--cutover-at",
                str(config.cutover_at),
                "--grandfather-hours",
                str(config.grandfather_hours),
            ]
        )
    else:
        command.extend(
            ["--downgrade-hours", str(config.legacy_trial_hours)]
        )
    if apply:
        if expected_sha256 is None or backup_file is None:
            raise DeploymentError("admin apply requires expected SHA and backup")
        command.extend(
            [
                "--apply",
                "--expected-sha256",
                expected_sha256,
                "--backup-file",
                str(backup_file),
            ]
        )
    completed = _run_checked(command)
    return _parse_receipt(
        completed,
        f"{'applied' if apply else 'dry_run'}_{mode}",
    )


def _preflight_trial_transform(config: Config) -> None:
    admin = _load_candidate_admin(config)
    snapshot = _preflight_admin_snapshot(admin, config.state_file)
    if config.trial_state_mode == "existing-v2":
        installed_policy = _require_unified_existing_v2_cutover_state(
            config,
            snapshot.value,
        )
        _require_existing_v2_authority(
            config,
            admin,
            snapshot.value,
            installed_policy=installed_policy,
        )
        return
    raw = snapshot.raw
    with tempfile.TemporaryDirectory(
        prefix="bidking-trial-deploy-preflight-"
    ) as temp:
        state_copy = Path(temp) / "trials.json"
        state_copy.write_bytes(raw)
        _run_admin(
            config,
            mode="migrate",
            state_file=state_copy,
            apply=False,
        )


def _preflight_upload_identity_store(config: Config) -> None:
    completed = _run_checked(
        [
            config.python_executable,
            str(config.candidate_dir / "upload_identity_db_admin.py"),
            "--db",
            str(config.upload_identity_db_file),
            "status",
        ]
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    try:
        receipt = json.loads(lines[-1]) if lines else None
    except ValueError as exc:
        raise DeploymentError("upload identity database receipt is invalid") from exc
    if receipt != {"ok": True, "state": "ready"}:
        raise DeploymentError("upload identity database is not ready")


def _validate_preflight_inputs(config: Config) -> None:
    if config.repo_dir.resolve() != (
        config.candidate_dir.resolve() / "bidking"
    ):
        raise DeploymentError(
            "candidate directory must be the server home containing bidking/"
        )
    if config.port <= 0 or config.port > 65535:
        raise DeploymentError("port out of range")
    if config.cutover_at <= 0:
        raise DeploymentError("cutover must be positive")
    if (
        config.upload_lease_seconds < 1
        or config.upload_lease_seconds > 900
        or config.pet_usage_lease_seconds < 5
        or config.pet_usage_lease_seconds > 60
        or config.min_client_security_epoch < 0
        or type(config.min_client_version) is not str
        or not config.min_client_version
        or type(config.v2_min_version) is not str
        or not config.v2_min_version
        or type(config.service_user) is not str
        or not config.service_user
        or type(config.service_group) is not str
        or not config.service_group
        or type(config.trial_state_mode) is not str
        or config.trial_state_mode not in TRIAL_STATE_MODES
    ):
        raise DeploymentError("candidate runtime contract input is invalid")
    if (
        config.new_trial_hours <= 0
        or config.grandfather_hours < config.new_trial_hours
        or config.legacy_trial_hours < config.grandfather_hours
    ):
        raise DeploymentError("trial duration relationship is invalid")
    _candidate_paths(config)
    core_text = _read_regular(
        config.candidate_dir / "activation_core.py"
    ).decode("utf-8", errors="strict")
    core_match = re.search(
        r"(?m)^ACTIVATION_CORE_API_VERSION\s*=\s*(\d+)\s*$",
        core_text,
    )
    if (
        core_match is None
        or int(core_match.group(1)) != EXPECTED_CORE_API
    ):
        raise DeploymentError("candidate activation core API is not 7")
    _read_regular(config.server_update_script)
    _read_regular(config.unit_file)
    _read_regular(config.upload_identity_db_file)
    _unit_service_identity(config, path=config.unit_file)
    _old_lines, old_tokens = _unit_exec_tokens(config.unit_file)
    old_history = _option_values(old_tokens, "--history-index-db-file")
    if old_history:
        old_path = _resolve_unit_path(_require_single_option(old_tokens, "--history-index-db-file"),
                                      working_directory=config.working_directory)
        if config.history_index_db_file is None or old_path != config.history_index_db_file:
            raise DeploymentError("existing history database cannot be disabled or moved")
    old_membership = _option_values(old_tokens, "--membership-store-file")
    if old_membership:
        old_path = _resolve_unit_path(_require_single_option(old_tokens, "--membership-store-file"),
                                      working_directory=config.working_directory)
        if config.membership_store_file is None or old_path != config.membership_store_file:
            raise DeploymentError("existing membership database cannot be disabled or moved")
    candidate_contract = _validate_candidate_unit(config)
    if config.membership_store_file is not None:
        _validate_membership_location(config, candidate_contract)
        membership_boundary(config.membership_store_file, config.candidate_dir)
    current_upload_mode = _unit_upload_identity_mode(config.unit_file)
    if (
        _UPLOAD_IDENTITY_MODE_RANK[config.upload_identity_mode]
        < _UPLOAD_IDENTITY_MODE_RANK[current_upload_mode]
    ):
        raise DeploymentError("upload identity mode downgrade")
    current_upload_epoch = _unit_epoch_state(
        config.unit_file,
        "--upload-security-epoch",
        missing_ok=True,
    )
    current_activation_epoch = _unit_epoch_state(
        config.unit_file,
        "--min-client-security-epoch",
        missing_ok=True,
    )
    candidate_upload_epoch = candidate_contract.upload_security_epoch.value
    candidate_activation_epoch = (
        candidate_contract.activation_security_epoch.value
    )
    if candidate_upload_epoch < current_upload_epoch.value:
        raise DeploymentError("upload security epoch downgrade")
    if (
        candidate_upload_epoch > current_upload_epoch.value
        and not current_upload_epoch.present
    ):
        raise DeploymentError(
            "upload security epoch bootstrap is required before enforcement"
        )
    if candidate_activation_epoch < current_activation_epoch.value:
        raise DeploymentError("activation security epoch downgrade")
    if (
        candidate_activation_epoch > current_activation_epoch.value
        and not current_activation_epoch.present
    ):
        raise DeploymentError(
            "activation security epoch bootstrap is required before enforcement"
        )
    upload_store_text = _read_regular(
        config.candidate_dir / "upload_identity_store.py"
    ).decode("utf-8", errors="strict")
    schema_match = re.search(
        r"(?m)^SCHEMA_VERSION\s*=\s*(\d+)\s*$",
        upload_store_text,
    )
    if (
        schema_match is None
        or int(schema_match.group(1)) != UPLOAD_IDENTITY_SCHEMA_VERSION
    ):
        raise DeploymentError("candidate upload identity schema mismatch")
    admin = _load_candidate_admin(config)
    _preflight_admin_snapshot(admin, config.state_file)
    _preflight_upload_identity_store(config)


class RealOps:
    def preflight(self, config: Config) -> None:
        with _preflight_stage("candidate_inputs"):
            _validate_preflight_inputs(config)
            env = os.environ.copy()
            env["HOME"] = str(config.candidate_dir.resolve())
            env["BIDKING_SERVER_UPDATE_PREFLIGHT_ONLY"] = "1"
        with _preflight_stage("candidate_runtime"):
            _run_checked(
                [
                    "bash",
                    str(config.server_update_script),
                    *ACTIVATION_GROUP,
                ],
                env=env,
                timeout=240.0,
            )
        with _preflight_stage("trial_authority"):
            _preflight_trial_transform(config)

    def stop(self, service: str) -> None:
        _run_checked(["systemctl", "stop", service], timeout=60.0)
        completed = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            timeout=15.0,
            check=False,
        )
        if completed.returncode == 0:
            raise DeploymentError("service remained active after stop")
        active = _run_checked(["systemctl", "show", service, "-p", "ActiveState", "--value"], timeout=15).stdout.strip()
        main_pid = _run_checked(["systemctl", "show", service, "-p", "MainPID", "--value"], timeout=15).stdout.strip()
        if active not in {"inactive", "failed"} or main_pid != "0":
            raise DeploymentError("service stop could not be confirmed")

    def start(self, service: str) -> None:
        _run_checked(["systemctl", "start", service], timeout=60.0)

    def daemon_reload(self) -> None:
        _run_checked(["systemctl", "daemon-reload"], timeout=60.0)

    def unit_readback(self, config: Config) -> dict[str, object]:
        def prop(name: str) -> str:
            return _run_checked(
                ["systemctl", "show", config.service, "-p", name, "--value"],
                timeout=30.0,
            ).stdout.strip()

        fragment = Path(prop("FragmentPath")).resolve()
        if fragment != config.unit_file.resolve():
            raise DeploymentError("effective systemd fragment path mismatch")
        if prop("DropInPaths"):
            raise DeploymentError("effective systemd unit has unapproved drop-ins")
        state_directories = sorted(prop("StateDirectory").split())
        _unit_service_identity(config, path=config.unit_file)
        lines, _tokens = _unit_exec_tokens(config.unit_file)
        expected_directories = sorted(value for line in lines if line.startswith("StateDirectory=")
                                      for value in line.split("=", 1)[1].strip().split())
        safe_projection = {
            "user": prop("User"),
            "group": prop("Group"),
            "umask": prop("UMask"),
            "state_directory_mode": prop("StateDirectoryMode"),
            "state_directories": state_directories,
            "working_directory_verified": (
                Path(prop("WorkingDirectory")).resolve()
                == config.working_directory.resolve()
            ),
        }
        if safe_projection != {
            "user": config.service_user,
            "group": config.service_group,
            "umask": _SERVICE_UMASK,
            "state_directory_mode": _SAFE_STATE_DIRECTORY_MODE,
            "state_directories": expected_directories,
            "working_directory_verified": True,
        }:
            raise DeploymentError("effective systemd service contract mismatch")
        return {
            "schema_version": 1,
            "fragment_sha256": _sha256(_read_regular(fragment)),
            "effective_properties_sha256": _sha256(
                json.dumps(
                    safe_projection,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ),
            "dropin_count": 0,
        }

    def wait_until(self, epoch: int) -> None:
        delay = epoch - int(time.time())
        if delay > 300:
            raise DeploymentError("cutover wait exceeds the migration safety window")
        if delay > 0:
            time.sleep(delay)

    def health(self, *, port: int) -> None:
        # Count every operator-side setup and response-read step against the
        # same hard budget.  A slow opener or a response that becomes valid
        # only after the cutoff must not extend deployment downtime.
        deadline = time.monotonic() + HEALTH_STARTUP_TIMEOUT_SECONDS
        # This is an operator-side loopback probe, not an outbound product
        # request.  Never inherit HTTP(S)_PROXY from the SSH/sudo environment:
        # routing 127.0.0.1 through a proxy can turn a healthy candidate into a
        # false URLError and trigger an unnecessary production rollback.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        endpoints = (
            ("/healthz", lambda value: type(value) is dict and value.get("ok") is True),
            (
                "/activation-readiness",
                lambda value: (
                    type(value) is dict
                    and value.get("ok") is True
                    and value.get("service") == "activation"
                ),
            ),
        )
        # The production collector must rebuild its in-memory session indexes
        # before it binds loopback.  The current authority set takes about 21
        # seconds on the target host, so the former 15-attempt cap could roll a
        # healthy candidate back before it was able to listen.  Both probes
        # share one bounded deadline: readiness never receives a fresh window
        # after healthz and a genuinely stuck process still fails closed.
        for path, predicate in endpoints:
            last_type = "not_ready"
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise DeploymentError(
                        f"health check failed: {path} ({last_type})"
                    )
                try:
                    with opener.open(  # noqa: S310 - fixed loopback, no proxy
                        f"http://127.0.0.1:{port}{path}",
                        timeout=min(_HEALTH_REQUEST_TIMEOUT_SECONDS, remaining),
                    ) as response:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0.0:
                            raise DeploymentError(
                                f"health check failed: {path} (not_ready)"
                            )
                        _set_loopback_response_timeout(
                            response,
                            min(_HEALTH_REQUEST_TIMEOUT_SECONDS, remaining),
                        )
                        value = json.loads(response.read())
                        payload_ready = response.status == 200 and predicate(value)
                        completed_at = time.monotonic()
                        if payload_ready and completed_at <= deadline:
                            break
                        last_type = (
                            "invalid_payload"
                            if completed_at <= deadline
                            else "not_ready"
                        )
                except (OSError, urllib.error.URLError):
                    last_type = "transport_error"
                except (UnicodeError, ValueError):
                    last_type = "invalid_payload"
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise DeploymentError(
                        f"health check failed: {path} ({last_type})"
                    )
                time.sleep(min(_HEALTH_POLL_INTERVAL_SECONDS, remaining))

    def checkpoint(self, stage: str) -> None:
        del stage


def _snapshot_path(path: Path) -> FileSnapshot:
    if not path.exists():
        if path.is_symlink():
            raise DeploymentError(f"absent target is a symlink: {path}")
        return FileSnapshot(False, None, None)
    raw = _read_regular(path)
    return FileSnapshot(
        True,
        _sha256(raw),
        stat.S_IMODE(path.stat().st_mode),
    )


def _protected_metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_uid),
        int(metadata.st_gid),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def _protected_stable_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_uid),
        int(metadata.st_gid),
    )


def _require_append_only_boundary(
    value: object,
    *,
    role: str,
) -> AppendOnlyBoundary:
    if type(value) is not AppendOnlyBoundary:
        raise DeploymentError(
            f"append-only protected boundary is invalid: {role}"
        )
    boundary = value
    if type(boundary.present) is not bool or type(boundary.size) is not int:
        raise DeploymentError(
            f"append-only protected boundary is invalid: {role}"
        )
    if boundary.size < 0:
        raise DeploymentError(
            f"append-only protected boundary is invalid: {role}"
        )
    identity = (
        boundary.device,
        boundary.inode,
        boundary.mode,
        boundary.uid,
        boundary.gid,
    )
    if boundary.present:
        if (
            type(boundary.sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", boundary.sha256) is None
            or any(type(item) is not int for item in identity)
        ):
            raise DeploymentError(
                f"append-only protected boundary is invalid: {role}"
            )
    elif (
        boundary.sha256 is not None
        or boundary.size != 0
        or any(item is not None for item in identity)
    ):
        raise DeploymentError(
            f"append-only protected boundary is invalid: {role}"
        )
    return boundary


def _read_protected_regular(
    path: Path,
    *,
    role: str,
    append_only_boundary: AppendOnlyBoundary | None = None,
) -> ProtectedFileRead | None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    initial_path: os.stat_result | None = None
    if no_follow:
        flags |= no_follow
    else:
        # Windows has no os.O_NOFOLLOW.  Reject a reparse/symlink or non-file
        # before opening, then bind all trusted bytes and metadata to the fd;
        # the final no-follow stat below closes an ordinary path-swap race.
        try:
            initial_path = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            initial_path = None
        except OSError as exc:
            raise DeploymentError(
                f"protected state cannot be inspected: {role}"
            ) from exc
        if initial_path is not None and not stat.S_ISREG(initial_path.st_mode):
            raise DeploymentError(
                f"protected state is not a regular file: {role}"
            )
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if initial_path is not None:
            raise DeploymentError(
                f"protected state changed while opening: {role}"
            ) from None
        try:
            os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DeploymentError(
                f"protected state cannot be inspected: {role}"
            ) from exc
        raise DeploymentError(f"protected state changed while opening: {role}")
    except OSError as exc:
        raise DeploymentError(f"protected state cannot be opened: {role}") from exc

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DeploymentError(
                f"protected state is not a regular file: {role}"
            )
        boundary = None
        if append_only_boundary is not None:
            boundary = _require_append_only_boundary(
                append_only_boundary,
                role=role,
            )
            if boundary.present:
                expected_identity = (
                    boundary.device,
                    boundary.inode,
                    boundary.mode,
                    boundary.uid,
                    boundary.gid,
                )
                if (
                    _protected_stable_identity(before) != expected_identity
                    or before.st_size < boundary.size
                ):
                    raise DeploymentError(
                        f"append-only protected state changed: {role}"
                    )
        digest = hashlib.sha256()
        total_size = 0
        # Verification reads exactly the stopped-service prefix.  It never
        # chases a growing EOF, so a multi-gigabyte samples file can accept
        # concurrent candidate uploads without weakening the frozen prefix.
        remaining = boundary.size if boundary is not None else None
        last_byte = b""
        while remaining is None or remaining > 0:
            request_size = (
                _PROTECTED_READ_CHUNK_SIZE
                if remaining is None
                else min(_PROTECTED_READ_CHUNK_SIZE, remaining)
            )
            try:
                chunk = os.read(descriptor, request_size)
            except InterruptedError:
                continue
            except OSError as exc:
                raise DeploymentError(
                    f"protected state cannot be read: {role}"
                ) from exc
            # os.read() is expected to return the exact built-in bytes type and
            # never more than the requested amount.  Validate that contract
            # before truth-testing or measuring the value so a monkeypatched,
            # corrupted, or hostile boundary object cannot invoke magic methods
            # or evade the bounded-memory guarantee.
            if (
                type(chunk) is not bytes
                or len(chunk) > request_size
            ):
                raise DeploymentError(
                    f"protected state cannot be read: {role}"
                )
            if chunk == b"":
                if remaining is not None:
                    raise DeploymentError(
                        f"append-only protected state changed: {role}"
                    )
                break
            digest.update(chunk)
            total_size += len(chunk)
            last_byte = chunk[-1:]
            if remaining is not None:
                remaining -= len(chunk)
        after = os.fstat(descriptor)
        if boundary is None:
            if (
                not stat.S_ISREG(after.st_mode)
                or _protected_metadata_identity(before)
                != _protected_metadata_identity(after)
                or total_size != after.st_size
            ):
                raise DeploymentError(
                    f"protected state changed while reading: {role}"
                )
        elif (
            not stat.S_ISREG(after.st_mode)
            or _protected_stable_identity(before)
            != _protected_stable_identity(after)
            or after.st_size < boundary.size
            or total_size != boundary.size
            or (
                boundary.present
                and digest.hexdigest() != boundary.sha256
            )
        ):
            raise DeploymentError(
                f"append-only protected state changed: {role}"
            )
    finally:
        os.close(descriptor)

    try:
        current_path = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise DeploymentError(
            f"protected state path changed after reading: {role}"
        ) from exc
    if (
        not stat.S_ISREG(current_path.st_mode)
        or current_path.st_dev != after.st_dev
        or current_path.st_ino != after.st_ino
        or (
            append_only_boundary is not None
            and _protected_stable_identity(current_path)
            != _protected_stable_identity(after)
        )
    ):
        raise DeploymentError(
            f"protected state path changed after reading: {role}"
        )
    return ProtectedFileRead(
        sha256=digest.hexdigest(),
        size=(int(after.st_size) if append_only_boundary is not None else total_size),
        metadata=after,
        ends_with_newline=(total_size == 0 or last_byte == b"\n"),
    )


def _require_live_authority_metadata(
    contract: UnitContract,
    *,
    role: str,
    metadata: os.stat_result,
) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if os.name != "posix":
        # Windows does not expose POSIX ownership or exact permission bits, but
        # its read-only bit still provides a useful source-level negative gate.
        if not mode & stat.S_IWUSR:
            raise DeploymentError(
                f"live activation authority metadata mismatch: {role}"
            )
        return
    if contract.service_user is None or contract.service_group is None:
        raise DeploymentError("live activation authority owner is unavailable")
    try:
        import grp
        import pwd

        expected_uid = pwd.getpwnam(contract.service_user).pw_uid
        expected_gid = grp.getgrnam(contract.service_group).gr_gid
    except (ImportError, KeyError) as exc:
        raise DeploymentError(
            "live activation authority owner is unavailable"
        ) from exc
    if (
        mode != 0o600
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
    ):
        raise DeploymentError(
            f"live activation authority metadata mismatch: {role}"
        )


def _capture_protected_state(
    contract: UnitContract,
    *,
    append_only_boundaries: dict[str, AppendOnlyBoundary] | None = None,
) -> tuple[
    dict[str, object],
    dict[str, bool],
    dict[str, AppendOnlyBoundary],
]:
    entries: list[dict[str, object]] = []
    immutable_entries: list[dict[str, object]] = []
    live_authority_presence: dict[str, bool] = {}
    captured_append_only_boundaries: dict[str, AppendOnlyBoundary] = {}
    present_count = 0
    append_only_roles = {
        role
        for role, _path, _mutable in contract.protected_paths
        if role in _APPEND_ONLY_PROTECTED_ROLES
    }
    if (
        append_only_boundaries is not None
        and (
            type(append_only_boundaries) is not dict
            or set(append_only_boundaries) != append_only_roles
        )
    ):
        raise DeploymentError("append-only protected snapshot is incomplete")
    for role, path, mutable in contract.protected_paths:
        append_only_boundary = None
        if role in append_only_roles and append_only_boundaries is not None:
            append_only_boundary = _require_append_only_boundary(
                append_only_boundaries[role],
                role=role,
            )
        protected = _read_protected_regular(
            path,
            role=role,
            append_only_boundary=append_only_boundary,
        )
        if (
            append_only_boundary is not None
            and append_only_boundary.present
            and protected is None
        ):
            raise DeploymentError(
                f"append-only protected state disappeared: {role}"
            )
        if protected is None:
            entry: dict[str, object] = {
                "role": role,
                "present": False,
                "mutable": mutable,
            }
            if role in _LIVE_AUTHORITY_ROLES:
                live_authority_presence[role] = False
            if role in append_only_roles:
                captured_append_only_boundaries[role] = AppendOnlyBoundary(
                    present=False,
                    sha256=None,
                    size=0,
                    device=None,
                    inode=None,
                    mode=None,
                    uid=None,
                    gid=None,
                )
        else:
            metadata = protected.metadata
            present_count += 1
            if role in append_only_roles:
                if append_only_boundaries is None:
                    if not protected.ends_with_newline:
                        raise DeploymentError(
                            "append-only protected state has an incomplete "
                            f"record: {role}"
                        )
                    captured_append_only_boundaries[role] = AppendOnlyBoundary(
                        present=True,
                        sha256=protected.sha256,
                        size=protected.size,
                        device=int(metadata.st_dev),
                        inode=int(metadata.st_ino),
                        mode=int(metadata.st_mode),
                        uid=int(metadata.st_uid),
                        gid=int(metadata.st_gid),
                    )
                else:
                    captured_append_only_boundaries[role] = append_only_boundary
            if role in _LIVE_AUTHORITY_ROLES:
                _require_live_authority_metadata(
                    contract,
                    role=role,
                    metadata=metadata,
                )
                live_authority_presence[role] = True
            entry = {
                "role": role,
                "present": True,
                "mutable": mutable,
                "sha256": protected.sha256,
                "size": protected.size,
                "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
                "uid": int(metadata.st_uid),
                "gid": int(metadata.st_gid),
            }
        entries.append(entry)
        if not mutable:
            immutable_entries.append(entry)

    def digest(value: list[dict[str, object]]) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return _sha256(payload)

    return (
        {
            "schema_version": 1,
            "role_count": len(entries),
            "present_count": present_count,
            "missing_count": len(entries) - present_count,
            "bundle_sha256": digest(entries),
            "immutable_bundle_sha256": digest(immutable_entries),
            "upload_identity_schema_version": UPLOAD_IDENTITY_SCHEMA_VERSION,
        },
        live_authority_presence,
        captured_append_only_boundaries,
    )


def _protected_state_snapshot(contract: UnitContract) -> dict[str, object]:
    (
        summary,
        _live_authority_presence,
        _append_only_boundaries,
    ) = _capture_protected_state(contract)
    return summary


def _require_immutable_state_unchanged(
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    if (
        before.get("immutable_bundle_sha256")
        != after.get("immutable_bundle_sha256")
    ):
        raise DeploymentError("immutable protected state changed during deployment")


def _require_live_authority_transition(
    before: dict[str, bool],
    after: dict[str, bool],
) -> None:
    if set(before) != _LIVE_AUTHORITY_ROLES or set(after) != _LIVE_AUTHORITY_ROLES:
        raise DeploymentError("live activation authority snapshot is incomplete")
    if any(before[role] and not after[role] for role in _LIVE_AUTHORITY_ROLES):
        raise DeploymentError(
            "live activation authority disappeared during deployment"
        )


def _require_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise DeploymentError(f"private backup path is not a directory: {path}")
    os.chmod(path, 0o700)
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise DeploymentError(f"private backup directory mode is not 0700: {path}")


def _require_private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"private backup path is not a regular file: {path}")
    os.chmod(path, 0o600)
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise DeploymentError(f"private backup file mode is not 0600: {path}")


def _copy_exclusive(source: Path, target: Path) -> str:
    raw = _read_regular(source)
    _require_private_directory(target.parent)
    try:
        with target.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise DeploymentError(f"backup already exists: {target}") from exc
    _require_private_file(target)
    if _read_regular(target) != raw:
        raise DeploymentError(f"backup verification failed: {target}")
    return _sha256(raw)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_install(source: Path, target: Path, *, mode: int = 0o644) -> None:
    raw = _read_regular(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.is_symlink():
        raise DeploymentError(f"refusing symlink deployment target: {target}")
    descriptor, raw_tmp = tempfile.mkstemp(
        prefix=f".{target.name}.deploy-",
        dir=str(target.parent),
    )
    staged = Path(raw_tmp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, mode)
        if _read_regular(staged) != raw:
            raise DeploymentError(f"staged install verification failed: {target}")
        os.replace(staged, target)
        _fsync_directory(target.parent)
        if _read_regular(target) != raw:
            raise DeploymentError(f"installed bytes mismatch: {target}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        staged.unlink(missing_ok=True)


def _write_absent_marker(path: Path) -> None:
    _require_private_directory(path.parent)
    try:
        with path.open("xb") as stream:
            stream.write(b"absent\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise DeploymentError(f"backup marker already exists: {path}") from exc
    _require_private_file(path)


def _write_manifest(transaction: Transaction, value: dict) -> None:
    if transaction.manifest_path is None:
        raise DeploymentError("transaction manifest path unavailable")
    target = transaction.manifest_path
    merged: dict = {}
    if target.exists():
        _raw, current = _read_json_object(target)
        merged.update(current)
    merged.update(value)
    payload = (
        json.dumps(merged, ensure_ascii=True, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, raw_tmp = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=str(target.parent),
    )
    staged = Path(raw_tmp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, 0o600)
        os.replace(staged, target)
        _require_private_file(target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        staged.unlink(missing_ok=True)


def _snapshot_after_stop(
    config: Config,
    transaction: Transaction,
    *,
    trial_snapshot,
) -> str:
    transaction.backup_dir = config.backup_root / (
        f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    transaction.backup_dir.mkdir(parents=True, exist_ok=False)
    _require_private_directory(transaction.backup_dir)
    transaction.manifest_path = transaction.backup_dir / "manifest.json"
    transaction.membership_backup = _backup_membership(config, transaction.backup_dir)
    transaction.authority_backups = _backup_activation_authorities(config, transaction.backup_dir)
    candidate_contract = _validate_candidate_unit(config)
    transaction.upload_identity_mode_before = _unit_upload_identity_mode(
        config.unit_file
    )
    transaction.upload_identity_mode_after = config.upload_identity_mode
    transaction.upload_security_epoch_before = _unit_epoch_state(
        config.unit_file,
        "--upload-security-epoch",
        missing_ok=True,
    ).value
    transaction.upload_security_epoch_after = config.upload_security_epoch
    transaction.activation_security_epoch_before = _unit_epoch_state(
        config.unit_file,
        "--min-client-security-epoch",
        missing_ok=True,
    ).value
    transaction.activation_security_epoch_after = config.min_client_security_epoch
    (
        transaction.protected_state_before,
        transaction.live_authority_presence_before,
        transaction.append_only_boundaries_before,
    ) = _capture_protected_state(
        candidate_contract,
    )
    manifest_files: dict[str, dict[str, object]] = {}
    targets = {
        **{
            name: config.deployed_dir / name
            for name in ACTIVATION_GROUP
        },
        "systemd-unit": config.unit_file,
    }
    for name, target in targets.items():
        snapshot = _snapshot_path(target)
        transaction.snapshots[name] = snapshot
        if snapshot.present:
            backup = transaction.backup_dir / "files" / name
            digest = _copy_exclusive(target, backup)
            if digest != snapshot.sha256:
                raise DeploymentError(f"snapshot digest mismatch: {name}")
        else:
            marker = transaction.backup_dir / "files" / f".absent-{name}"
            _write_absent_marker(marker)
        manifest_files[name] = {
            "present": snapshot.present,
            "sha256": snapshot.sha256,
            "mode": snapshot.mode,
        }
    state_sha = trial_snapshot.sha256
    transaction.trial_state_input_sha256 = state_sha
    transaction.trial_state_input_identity = trial_snapshot.identity
    _write_manifest(
        transaction,
        {
            "schema_version": 1,
            "status": "snapshotted",
            "service": config.service,
            "cutover_at": config.cutover_at,
            "trial_state_input_sha256": state_sha,
            "old_files": manifest_files,
            "membership_backup": transaction.membership_backup,
            "authority_backups": transaction.authority_backups,
            "candidate_sha256": {
                name: _sha256(
                    _read_regular(config.candidate_dir / name)
                )
                for name in (*ACTIVATION_GROUP, TRIAL_ADMIN)
            },
            "candidate_unit_sha256": _sha256(
                _read_regular(config.candidate_unit_file)
            ),
            "upload_security_epoch_before": (
                transaction.upload_security_epoch_before
            ),
            "upload_security_epoch_after": (
                transaction.upload_security_epoch_after
            ),
            "upload_identity_mode_before": (
                transaction.upload_identity_mode_before
            ),
            "upload_identity_mode_after": (
                transaction.upload_identity_mode_after
            ),
            "activation_security_epoch_before": (
                transaction.activation_security_epoch_before
            ),
            "activation_security_epoch_after": (
                transaction.activation_security_epoch_after
            ),
            "runtime_contract": {
                "trial_state_mode": config.trial_state_mode,
                "upload_identity_mode": config.upload_identity_mode,
                "upload_lease_seconds": config.upload_lease_seconds,
                "min_client_version": config.min_client_version,
                "v2_min_version": config.v2_min_version,
                "pet_usage_lease_seconds": config.pet_usage_lease_seconds,
                "service_user": config.service_user,
                "service_group": config.service_group,
                "service_umask": _SERVICE_UMASK,
                "state_directory_mode": _SAFE_STATE_DIRECTORY_MODE,
                "working_directory_verified": True,
            },
            "protected_state_before": transaction.protected_state_before,
        },
    )
    return state_sha


def _rewrite_unit_option(
    raw: bytes,
    *,
    option: str,
    expected: int | float,
    replacement: int | float,
    label: str,
) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise DeploymentError("rollback unit is not UTF-8") from exc
    pattern = re.compile(
        rf"(?<!\S)({re.escape(option)})(=|[ \t]+)([^\s]+)(?!\S)"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise DeploymentError(f"rollback {label} cannot be preserved")
    current_text = matches[0].group(3)
    try:
        if type(expected) is str:
            current = current_text
        else:
            current = float(current_text) if type(expected) is float else int(current_text)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeploymentError(f"rollback {label} cannot be preserved") from exc
    if current != expected:
        raise DeploymentError(f"rollback {label} cannot be preserved")
    if type(replacement) is str:
        replacement_text = replacement
    else:
        replacement_text = (
            str(int(replacement))
            if isinstance(replacement, (int, float)) and float(replacement).is_integer()
            else str(replacement)
        )
    return pattern.sub(
        lambda match: match.group(1) + match.group(2) + replacement_text,
        text,
        count=1,
    ).encode("utf-8")


def _prepare_rollback_unit_target(
    config: Config,
    transaction: Transaction,
) -> RollbackUnitTarget:
    """Bind rollback to one final unit before replacing any deployed file."""

    if transaction.backup_dir is None:
        raise DeploymentError("transaction backup unavailable")
    snapshot = transaction.snapshots.get("systemd-unit")
    backup = transaction.backup_dir / "files" / "systemd-unit"
    if (
        snapshot is None
        or not snapshot.present
        or snapshot.sha256 is None
        or snapshot.mode is None
        or not backup.is_file()
    ):
        raise DeploymentError("rollback unit snapshot is unavailable")
    raw = _read_regular(backup)
    if _sha256(raw) != snapshot.sha256:
        raise DeploymentError("rollback backup changed: systemd-unit")

    rollback_upload_mode = transaction.upload_identity_mode_before
    if (
        _UPLOAD_IDENTITY_MODE_RANK[transaction.upload_identity_mode_after]
        > _UPLOAD_IDENTITY_MODE_RANK[rollback_upload_mode]
    ):
        raw = _rewrite_unit_option(
            raw,
            option="--upload-identity-mode",
            expected=rollback_upload_mode,
            replacement=transaction.upload_identity_mode_after,
            label="upload-identity mode",
        )
        rollback_upload_mode = transaction.upload_identity_mode_after

    rollback_upload_epoch = transaction.upload_security_epoch_before
    if transaction.upload_security_epoch_after > rollback_upload_epoch:
        raw = _rewrite_unit_option(
            raw,
            option="--upload-security-epoch",
            expected=transaction.upload_security_epoch_before,
            replacement=transaction.upload_security_epoch_after,
            label="upload-security epoch",
        )
        rollback_upload_epoch = transaction.upload_security_epoch_after

    rollback_activation_epoch = transaction.activation_security_epoch_before
    if transaction.activation_security_epoch_after > rollback_activation_epoch:
        raw = _rewrite_unit_option(
            raw,
            option="--min-client-security-epoch",
            expected=transaction.activation_security_epoch_before,
            replacement=transaction.activation_security_epoch_after,
            label="activation-security epoch",
        )
        rollback_activation_epoch = transaction.activation_security_epoch_after

    rollback_trial_policy: TrialPolicy | None = None
    if (
        transaction.trial_state_already_v2
        and transaction.candidate_may_have_served
        and _is_unified_twelve_hour_existing_v2(config)
    ):
        current = _unit_trial_policy(backup)
        if current.cutover_at != config.cutover_at:
            raise DeploymentError("rollback unit trial cutover cannot be preserved")
        if not (
            current.trial_hours in {
                _LEGACY_SHORT_TRIAL_HOURS,
                _UNIFIED_TRIAL_HOURS,
            }
            and current.grandfather_hours == _UNIFIED_TRIAL_HOURS
        ):
            raise DeploymentError("rollback unit trial policy cannot be preserved")
        if current.trial_hours == _LEGACY_SHORT_TRIAL_HOURS:
            raw = _rewrite_unit_option(
                raw,
                option="--trial-hours",
                expected=_LEGACY_SHORT_TRIAL_HOURS,
                replacement=_UNIFIED_TRIAL_HOURS,
                label="trial policy",
            )
        rollback_trial_policy = TrialPolicy(
            trial_hours=_UNIFIED_TRIAL_HOURS,
            grandfather_hours=_UNIFIED_TRIAL_HOURS,
            cutover_at=config.cutover_at,
        )

    target = transaction.backup_dir / "files" / "systemd-unit.rollback-target"
    try:
        with target.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise DeploymentError("rollback unit target already exists") from exc
    _require_private_file(target)
    _fsync_directory(target.parent)
    if _read_regular(target) != raw:
        raise DeploymentError("rollback unit target verification failed")
    if (
        _unit_upload_identity_mode(target) != rollback_upload_mode
        or
        _unit_epoch_state(
            target,
            "--upload-security-epoch",
            missing_ok=True,
        ).value
        != rollback_upload_epoch
        or _unit_epoch_state(
            target,
            "--min-client-security-epoch",
            missing_ok=True,
        ).value
        != rollback_activation_epoch
    ):
        raise DeploymentError("rollback unit epoch target verification failed")
    if rollback_trial_policy is not None and _unit_trial_policy(target) != rollback_trial_policy:
        raise DeploymentError("rollback unit trial policy target verification failed")
    return RollbackUnitTarget(
        path=target,
        sha256=_sha256(raw),
        upload_identity_mode=rollback_upload_mode,
        upload_security_epoch=rollback_upload_epoch,
        activation_security_epoch=rollback_activation_epoch,
        trial_policy=rollback_trial_policy,
    )


def _restore_files(
    config: Config,
    transaction: Transaction,
    *,
    rollback_unit_target: RollbackUnitTarget,
) -> None:
    if transaction.backup_dir is None:
        return
    targets = {
        **{
            name: config.deployed_dir / name
            for name in ACTIVATION_GROUP
        },
        "systemd-unit": config.unit_file,
    }
    for name, target in targets.items():
        snapshot = transaction.snapshots.get(name)
        if snapshot is None:
            raise DeploymentError(f"missing rollback snapshot: {name}")
        backup = transaction.backup_dir / "files" / name
        marker = transaction.backup_dir / "files" / f".absent-{name}"
        if snapshot.present:
            if marker.exists() or not backup.is_file():
                raise DeploymentError(f"conflicting rollback metadata: {name}")
            if _sha256(_read_regular(backup)) != snapshot.sha256:
                raise DeploymentError(f"rollback backup changed: {name}")
            restore_source = (
                rollback_unit_target.path
                if name == "systemd-unit"
                else backup
            )
            _atomic_install(
                restore_source,
                target,
                mode=snapshot.mode if snapshot.mode is not None else 0o644,
            )
        else:
            if backup.exists() or _read_regular(marker) != b"absent\n":
                raise DeploymentError(f"conflicting absent metadata: {name}")
            if target.exists():
                if target.is_symlink() or not target.is_file():
                    raise DeploymentError(
                        f"cannot restore absent target safely: {target}"
                    )
                target.unlink()
        restored = _snapshot_path(target)
        expected_present = snapshot.present
        expected_sha256 = (
            rollback_unit_target.sha256
            if name == "systemd-unit"
            else snapshot.sha256
        )
        if restored.present != expected_present or restored.sha256 != expected_sha256:
            raise DeploymentError(f"rollback verification failed: {name}")


def _v2_expiries_value(value: dict) -> dict[str, int]:
    expiries: dict[str, int] = {}
    for device, record in dict.items(value):
        if type(device) is not str or not device or type(record) is not dict:
            raise DeploymentError("trial v2 authority has invalid owner/record")
        if (
            not all(type(key) is str for key in dict.keys(record))
            or frozenset(dict.keys(record)) != TRIAL_RECORD_KEYS
            or type(record.get("schema_version")) is not int
            or record.get("schema_version") != TRIAL_SCHEMA_VERSION
            or type(record.get("start")) is not int
            or type(record.get("window_seconds")) is not int
            or type(record.get("last_server_now")) is not int
        ):
            raise DeploymentError("trial v2 authority has invalid shape")
        start = record["start"]
        window = record["window_seconds"]
        last_server_now = record["last_server_now"]
        if start <= 0 or window <= 0 or last_server_now < start:
            raise DeploymentError("trial v2 authority has invalid values")
        expiries[device] = start + window
    return expiries


def _v2_expiries(state_file: Path) -> dict[str, int]:
    _raw, value = _read_json_object(state_file)
    return _v2_expiries_value(value)


def _require_core_accepts_v2(
    core: ModuleType,
    value: dict,
    *,
    label: str,
) -> None:
    strict_record = getattr(core, "_strict_trial_record", None)
    if (
        type(getattr(core, "ACTIVATION_CORE_API_VERSION", None)) is not int
        or core.ACTIVATION_CORE_API_VERSION not in TRIAL_V2_READER_APIS
        or type(getattr(core, "TRIAL_RECORD_SCHEMA_VERSION", None)) is not int
        or core.TRIAL_RECORD_SCHEMA_VERSION != TRIAL_SCHEMA_VERSION
        or not callable(strict_record)
    ):
        raise DeploymentError(f"{label} runtime does not support trial v2")
    now = int(time.time())
    for record in dict.values(value):
        try:
            strict_record(record, now=now)
        except Exception as exc:
            raise DeploymentError(
                f"{label} runtime rejected trial v2 authority"
            ) from exc


def _require_core_policy_accepts_v2(
    core: ModuleType,
    value: dict,
    *,
    policy: TrialPolicy,
    label: str,
    allow_legacy_normalization_probe: bool = False,
) -> None:
    """Exercise one runtime with its real unit policy without touching authority.

    Unified preflight may observe the runtime's in-memory 3h-to-12h projection only
    so both installed and candidate code are proven policy-aware.  The caller still
    rejects that noncanonical ledger and requires an audited offline normalization.
    """

    _require_core_accepts_v2(core, value, label=label)
    grant_trial = getattr(core, "grant_trial", None)
    if not callable(grant_trial):
        raise DeploymentError(f"{label} runtime has no trial loader")
    expiries = _v2_expiries_value(value)
    for device, record in dict.items(value):
        trial_copy = {
            owner: dict(owner_record)
            for owner, owner_record in dict.items(value)
        }
        probe_now = max(record["last_server_now"], policy.cutover_at)
        try:
            ok, reason, lease = grant_trial(
                device,
                trials=trial_copy,
                trial_hours=policy.trial_hours,
                grandfather_cutover_at=policy.cutover_at,
                grandfather_trial_hours=policy.grandfather_hours,
                now=probe_now,
            )
        except Exception as exc:
            raise DeploymentError(
                f"{label} unit trial policy cannot read v2 authority"
            ) from exc
        expected_expiry = expiries[device]
        if (
            allow_legacy_normalization_probe
            and policy.trial_hours == _UNIFIED_TRIAL_HOURS
            and policy.grandfather_hours == _UNIFIED_TRIAL_HOURS
            and record["window_seconds"]
            == int(_LEGACY_SHORT_TRIAL_HOURS * 3600)
        ):
            expected_expiry = record["start"] + int(
                _UNIFIED_TRIAL_HOURS * 3600
            )
        if probe_now >= expected_expiry:
            compatible = (ok, reason, lease) == (
                False,
                "trial_expired",
                None,
            )
        else:
            compatible = (
                ok is True
                and reason == "ok"
                and type(lease) is dict
                and lease.get("exp") == expected_expiry
            )
        if not compatible:
            raise DeploymentError(
                f"{label} unit trial policy rejects v2 authority"
            )


def _require_existing_v2_authority(
    config: Config,
    admin: ModuleType,
    value: dict,
    *,
    installed_policy: TrialPolicy | None = None,
) -> dict[str, int]:
    expiries = _v2_expiries_value(value)
    installed_core = _load_module(
        config.deployed_dir / "activation_core.py",
        "installed_activation_core",
    )
    candidate_core = getattr(admin, "activation_core", None)
    if not isinstance(candidate_core, ModuleType):
        raise DeploymentError("candidate trial runtime is unavailable")
    if installed_policy is None:
        _require_core_accepts_v2(installed_core, value, label="installed")
        _require_core_accepts_v2(candidate_core, value, label="candidate")
    else:
        candidate_policy = _unit_trial_policy(config.candidate_unit_file)
        _require_core_policy_accepts_v2(
            installed_core,
            value,
            policy=installed_policy,
            label="installed",
            allow_legacy_normalization_probe=True,
        )
        _require_core_policy_accepts_v2(
            candidate_core,
            value,
            policy=candidate_policy,
            label="candidate",
            allow_legacy_normalization_probe=True,
        )
        for record in dict.values(value):
            if record["window_seconds"] != int(_UNIFIED_TRIAL_HOURS * 3600):
                raise DeploymentError(
                    "noncanonical unified trial ledger requires audited offline normalization"
                )
    return expiries


def _verify_restored_v2_authority(
    config: Config,
    *,
    expected_expiries: dict[str, int],
) -> None:
    raw, value = _read_json_object(config.state_file)
    actual_expiries = _v2_expiries_value(value)
    if actual_expiries != expected_expiries:
        raise DeploymentError("trial v2 authority changed during rollback")
    restored_core = _load_module(
        config.deployed_dir / "activation_core.py",
        "restored_activation_core",
    )
    policy = _unit_trial_policy(config.unit_file)
    _require_core_policy_accepts_v2(
        restored_core,
        value,
        policy=policy,
        label="restored",
    )
    if _read_regular(config.state_file) != raw:
        raise DeploymentError("trial v2 authority changed during rollback verification")


def _load_module(path: Path, label: str) -> ModuleType:
    name = f"_bidking_{label}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise DeploymentError(f"cannot load restored runtime: {path}")
    module = importlib.util.module_from_spec(spec)
    marker = object()
    previous = sys.modules.get(name, marker)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DeploymentError("restored activation core cannot be imported") from exc
    finally:
        if previous is marker:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module


def _load_candidate_admin(config: Config) -> ModuleType:
    """Load the candidate core/admin pair without using installed server code."""

    marker = object()
    previous_core = sys.modules.get("activation_core", marker)
    core_path = config.candidate_dir / "activation_core.py"
    admin_path = config.candidate_admin
    core_spec = importlib.util.spec_from_file_location(
        "activation_core",
        core_path,
    )
    admin_name = f"_bidking_trial_admin_{uuid.uuid4().hex}"
    admin_spec = importlib.util.spec_from_file_location(
        admin_name,
        admin_path,
    )
    if (
        core_spec is None
        or core_spec.loader is None
        or admin_spec is None
        or admin_spec.loader is None
    ):
        raise DeploymentError("cannot load candidate trial admin")
    core_module = importlib.util.module_from_spec(core_spec)
    admin_module = importlib.util.module_from_spec(admin_spec)
    try:
        sys.modules["activation_core"] = core_module
        sys.modules[admin_name] = admin_module
        core_spec.loader.exec_module(core_module)
        admin_spec.loader.exec_module(admin_module)
    except Exception as exc:
        raise DeploymentError("candidate trial admin cannot be imported") from exc
    finally:
        sys.modules.pop(admin_name, None)
        if previous_core is marker:
            sys.modules.pop("activation_core", None)
        else:
            sys.modules["activation_core"] = previous_core
    if (
        type(getattr(admin_module, "_LOCK_PROTOCOL_VERSION", None)) is not int
        or admin_module._LOCK_PROTOCOL_VERSION
        != TRIAL_ADMIN_LOCK_PROTOCOL_VERSION
        or not callable(getattr(admin_module, "_exclusive_admin_lock", None))
        or not callable(
            getattr(
                admin_module,
                "_inspect_recoverable_pending_commit",
                None,
            )
        )
        or not callable(getattr(admin_module, "_recover_state_locked", None))
        or not callable(getattr(admin_module, "_read_state", None))
        or not callable(getattr(admin_module, "_operate_state_locked", None))
    ):
        raise DeploymentError("candidate trial admin lock protocol mismatch")
    return admin_module


def _read_admin_snapshot(admin: ModuleType, state_file: Path):
    try:
        snapshot = admin._read_state(state_file)
    except Exception as exc:
        raise DeploymentError("trial authority snapshot failed") from exc
    identity = getattr(snapshot, "identity", None)
    if (
        type(getattr(snapshot, "raw", None)) is not bytes
        or type(getattr(snapshot, "value", None)) is not dict
        or type(getattr(snapshot, "sha256", None)) is not str
        or len(snapshot.sha256) != 64
        or identity is None
        or type(getattr(identity, "device", None)) is not int
        or type(getattr(identity, "inode", None)) is not int
    ):
        raise DeploymentError("trial authority snapshot is invalid")
    return snapshot


def _preflight_admin_snapshot(admin: ModuleType, state_file: Path):
    try:
        pending = admin._inspect_recoverable_pending_commit(state_file)
    except Exception as exc:
        raise DeploymentError(
            "pending trial commit is not recoverable"
        ) from exc
    if pending is not None:
        return pending
    return _read_admin_snapshot(admin, state_file)


def _recover_admin_locked(
    admin: ModuleType,
    state_file: Path,
) -> dict | None:
    try:
        receipt = admin._recover_state_locked(state_file)
    except Exception as exc:
        raise DeploymentError("pending trial commit recovery failed") from exc
    if receipt is None:
        return None
    if (
        type(receipt) is not dict
        or receipt.get("ok") is not True
        or receipt.get("mode") != "recovered_pending_commit"
        or receipt.get("lock_backend") != TRIAL_ADMIN_LOCK_BACKEND
        or receipt.get("lock_protocol_version")
        != TRIAL_ADMIN_LOCK_PROTOCOL_VERSION
    ):
        raise DeploymentError("pending trial commit recovery receipt is invalid")
    return receipt


def _operate_admin_locked(
    config: Config,
    admin: ModuleType,
    *,
    mode: str,
    state_file: Path,
    expected_sha256: str,
    expected_identity: object,
    backup_file: Path,
) -> dict:
    _require_private_directory(backup_file.parent)
    keyword_args: dict[str, object] = {
        "state_path": state_file,
        "mode": mode,
        "apply": True,
        "expected_sha256": expected_sha256,
        "expected_identity": expected_identity,
        "backup_file": backup_file,
    }
    if mode == "migrate":
        keyword_args.update(
            {
                "cutover_at": config.cutover_at,
                "grandfather_hours": config.grandfather_hours,
            }
        )
    elif mode == "downgrade":
        keyword_args["downgrade_hours"] = config.legacy_trial_hours
    else:
        raise DeploymentError("unsupported trial admin operation")
    try:
        receipt = admin._operate_state_locked(**keyword_args)
    except Exception as exc:
        raise DeploymentError(f"trial admin {mode} failed") from exc
    _require_private_file(backup_file)
    expected_mode = f"applied_{mode}"
    if (
        type(receipt) is not dict
        or receipt.get("ok") is not True
        or receipt.get("mode") != expected_mode
        or receipt.get("lock_backend") != TRIAL_ADMIN_LOCK_BACKEND
        or receipt.get("lock_protocol_version")
        != TRIAL_ADMIN_LOCK_PROTOCOL_VERSION
    ):
        raise DeploymentError(f"trial admin {mode} receipt is invalid")
    return receipt


def _verify_legacy_runtime(
    *,
    core_path: Path,
    state_file: Path,
    expected_expiries: dict[str, int],
    legacy_trial_hours: float,
) -> None:
    _raw, value = _read_json_object(state_file)
    if set(value) != set(expected_expiries):
        raise DeploymentError("legacy rollback changed the trial owner set")
    legacy_window = int(legacy_trial_hours * 3600)
    if legacy_window <= 0:
        raise DeploymentError("legacy trial duration is invalid")
    runtime = _load_module(core_path, "legacy_activation_core")
    grant_trial = getattr(runtime, "grant_trial", None)
    if not callable(grant_trial):
        raise DeploymentError("restored activation core has no trial loader")
    for device, start in dict.items(value):
        if type(device) is not str or type(start) is not int or start <= 0:
            raise DeploymentError("legacy rollback state is invalid")
        expected_expiry = expected_expiries[device]
        if start + legacy_window != expected_expiry:
            raise DeploymentError("legacy rollback changed a trial expiry")
        probe_time = max(start, expected_expiry - 1)
        trial_copy = dict(value)
        try:
            ok, reason, lease = grant_trial(
                device,
                trials=trial_copy,
                trial_hours=legacy_trial_hours,
                now=probe_time,
            )
        except Exception as exc:
            raise DeploymentError(
                "restored activation core cannot read rollback state"
            ) from exc
        if (
            ok is not True
            or reason != "ok"
            or type(lease) is not dict
            or lease.get("exp") != expected_expiry
        ):
            raise DeploymentError(
                "restored activation core changed a trial expiry"
            )


def _installed_matches_candidate(config: Config) -> None:
    for name in ACTIVATION_GROUP:
        if _read_regular(config.deployed_dir / name) != _read_regular(
            config.candidate_dir / name
        ):
            raise DeploymentError(f"candidate install mismatch: {name}")
    if _read_regular(config.unit_file) != _read_regular(
        config.candidate_unit_file
    ):
        raise DeploymentError("candidate unit install mismatch")
    core_text = _read_regular(
        config.deployed_dir / "activation_core.py"
    ).decode("utf-8", errors="strict")
    match = re.search(
        r"(?m)^ACTIVATION_CORE_API_VERSION\s*=\s*(\d+)\s*$",
        core_text,
    )
    if match is None or int(match.group(1)) != EXPECTED_CORE_API:
        raise DeploymentError("installed activation core API is not 7")
    _v2_expiries(config.state_file)
    _validate_candidate_unit(config)


@contextlib.contextmanager
def _deployment_lock(path: Path, *, timeout: float) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(path),
        timeout=timeout,
        isolation_level=None,
    )
    try:
        connection.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        try:
            connection.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            raise DeploymentError("another deployment operator holds the lock") from exc
        yield
    finally:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        connection.close()


def _migrate(
    config: Config,
    transaction: Transaction,
    admin: ModuleType,
    state_snapshot,
) -> None:
    if transaction.backup_dir is None:
        raise DeploymentError("transaction backup unavailable")
    if config.trial_state_mode == "existing-v2":
        installed_policy = _require_unified_existing_v2_cutover_state(
            config,
            state_snapshot.value,
        )
        _require_existing_v2_authority(
            config,
            admin,
            state_snapshot.value,
            installed_policy=installed_policy,
        )
        transaction.trial_state_already_v2 = True
        return
    receipt = _operate_admin_locked(
        config,
        admin,
        mode="migrate",
        state_file=config.state_file,
        expected_sha256=state_snapshot.sha256,
        expected_identity=state_snapshot.identity,
        backup_file=transaction.backup_dir / "trials.before-migrate.json",
    )
    current_sha = _sha256(_read_regular(config.state_file))
    if receipt.get("input_sha256") != state_snapshot.sha256:
        raise DeploymentError("migration receipt input mismatch")
    if receipt.get("output_sha256") != current_sha:
        raise DeploymentError("migration receipt output mismatch")
    transaction.migrated = True


def _downgrade_current_v2(
    config: Config,
    transaction: Transaction,
    admin: ModuleType,
) -> dict[str, int]:
    if transaction.backup_dir is None:
        raise DeploymentError("transaction backup unavailable")
    current_snapshot = _read_admin_snapshot(admin, config.state_file)
    expected_expiries = _v2_expiries_value(current_snapshot.value)
    receipt = _operate_admin_locked(
        config,
        admin,
        mode="downgrade",
        state_file=config.state_file,
        expected_sha256=current_snapshot.sha256,
        expected_identity=current_snapshot.identity,
        backup_file=transaction.backup_dir / "trials.before-downgrade.json",
    )
    if receipt.get("input_sha256") != current_snapshot.sha256:
        raise DeploymentError("downgrade receipt input mismatch")
    if receipt.get("output_sha256") != _sha256(
        _read_regular(config.state_file)
    ):
        raise DeploymentError("downgrade receipt output mismatch")
    return expected_expiries


def _rollback(
    config: Config,
    transaction: Transaction,
    admin: ModuleType,
    ops: Ops,
    *,
    reason: str,
) -> None:
    ops.stop(config.service)
    if config.history_index_db_file is not None:
        require_history_runtime_recovery(
            transaction.backup_dir / "files" if transaction.backup_dir else None,
            transaction.backup_dir / "files" / "systemd-unit" if transaction.backup_dir else None,
        )
    if config.membership_store_file is not None:
        try:
            if transaction.manifest_path is not None:
                _raw, manifest = _read_json_object(transaction.manifest_path)
                pinned = manifest.get("candidate_sha256", {})
                if any(pinned.get(name) != _sha256(_read_regular(config.candidate_dir / name))
                       for name in MEMBERSHIP_CONSUMERS):
                    raise MembershipRecoveryRequired("membership_compatible_recovery_required")
            require_membership_recovery(
                config.membership_store_file, config.candidate_dir,
                transaction.backup_dir / "files" if transaction.backup_dir else None,
                transaction.backup_dir / "files" / "systemd-unit" if transaction.backup_dir else None,
                config.working_directory,
                before=(transaction.membership_backup["boundary"]
                        if transaction.membership_backup is not None else None),
                candidate_unit=config.candidate_unit_file,
            )
        except MembershipRecoveryRequired:
            if transaction.manifest_path is not None:
                _write_manifest(transaction, {"status": "membership_recovery_required",
                                               "reason": "membership_compatible_recovery_required"})
            raise
    recovered = _recover_admin_locked(admin, config.state_file)
    if recovered is not None:
        transaction.recovered_pending_commit = True
        current = _read_admin_snapshot(admin, config.state_file)
        if (
            transaction.trial_state_input_sha256 is None
            or recovered.get("input_sha256")
            != transaction.trial_state_input_sha256
            or recovered.get("output_sha256") != current.sha256
        ):
            raise DeploymentError(
                "pending trial recovery does not belong to this transaction"
            )
        _v2_expiries_value(current.value)
        transaction.migrated = True
    if not transaction.trial_state_verified:
        raise DeploymentError(
            "trial authority was not verified after stopping the service"
        )
    expected_expiries: dict[str, int] | None = None
    expected_v2_expiries: dict[str, int] | None = None
    if transaction.migrated:
        # Do not restore trials.before-migrate.json: the candidate may have
        # accepted valid new trials before the failure was detected.
        expected_expiries = _downgrade_current_v2(
            config,
            transaction,
            admin,
        )
    elif transaction.trial_state_already_v2:
        current = _read_admin_snapshot(admin, config.state_file)
        expected_v2_expiries = _v2_expiries_value(current.value)
    else:
        current = _read_admin_snapshot(admin, config.state_file)
        if (
            current.sha256 != transaction.trial_state_input_sha256
            or current.identity != transaction.trial_state_input_identity
        ):
            raise DeploymentError(
                "trial authority changed before safe rollback"
            )
    rollback_unit_target = _prepare_rollback_unit_target(config, transaction)
    _restore_files(
        config,
        transaction,
        rollback_unit_target=rollback_unit_target,
    )
    if transaction.unit_touched:
        ops.daemon_reload()
    rollback_unit_readback = ops.unit_readback(config)
    if (
        _unit_upload_identity_mode(config.unit_file)
        != rollback_unit_target.upload_identity_mode
    ):
        raise DeploymentError("rollback upload identity mode verification failed")
    if expected_expiries is not None:
        _verify_legacy_runtime(
            core_path=config.deployed_dir / "activation_core.py",
            state_file=config.state_file,
            expected_expiries=expected_expiries,
            legacy_trial_hours=config.legacy_trial_hours,
        )
    if expected_v2_expiries is not None:
        _verify_restored_v2_authority(
            config,
            expected_expiries=expected_v2_expiries,
        )
    if transaction.append_only_boundaries_before is None:
        raise DeploymentError("append-only protected snapshot is unavailable")
    (
        protected_after,
        live_authority_presence_after,
        _append_only_boundaries_after,
    ) = _capture_protected_state(
        _validate_candidate_unit(config),
        append_only_boundaries=transaction.append_only_boundaries_before,
    )
    if transaction.protected_state_before is None:
        raise DeploymentError("protected state snapshot is unavailable")
    if transaction.live_authority_presence_before is None:
        raise DeploymentError("live activation authority snapshot is unavailable")
    _require_immutable_state_unchanged(
        transaction.protected_state_before,
        protected_after,
    )
    _require_live_authority_transition(
        transaction.live_authority_presence_before,
        live_authority_presence_after,
    )
    ops.start(config.service)
    ops.health(port=config.port)
    if transaction.manifest_path is not None:
        _write_manifest(
            transaction,
            {
                "schema_version": 1,
                "status": "rolled_back",
                "reason": reason,
                "trial_state_sha256": _sha256(
                    _read_regular(config.state_file)
                ),
                "used_current_v2_downgrade": transaction.migrated,
                "trial_state_mode": config.trial_state_mode,
                "upload_security_epoch_before": (
                    transaction.upload_security_epoch_before
                ),
                "upload_security_epoch_after": (
                    transaction.upload_security_epoch_after
                ),
                "rollback_upload_security_epoch": (
                    rollback_unit_target.upload_security_epoch
                ),
                "upload_identity_mode_before": (
                    transaction.upload_identity_mode_before
                ),
                "upload_identity_mode_after": (
                    transaction.upload_identity_mode_after
                ),
                "rollback_upload_identity_mode": (
                    rollback_unit_target.upload_identity_mode
                ),
                "activation_security_epoch_before": (
                    transaction.activation_security_epoch_before
                ),
                "activation_security_epoch_after": (
                    transaction.activation_security_epoch_after
                ),
                "rollback_activation_security_epoch": (
                    rollback_unit_target.activation_security_epoch
                ),
                "rollback_trial_policy": (
                    dataclasses.asdict(rollback_unit_target.trial_policy)
                    if rollback_unit_target.trial_policy is not None
                    else None
                ),
                "protected_state_before": transaction.protected_state_before,
                "protected_state_after": protected_after,
                "unit_readback": rollback_unit_readback,
            },
        )


def apply(config: Config, ops: Ops) -> Path:
    """Apply one state-aware transaction or recover to a verified old pair."""

    ops.preflight(config)
    admin = _load_candidate_admin(config)
    transaction = Transaction()
    with _deployment_lock(
        config.lock_file,
        timeout=config.lock_timeout_seconds,
    ):
        try:
            with admin._exclusive_admin_lock(config.state_file):
                try:
                    ops.stop(config.service)
                    recovered = _recover_admin_locked(
                        admin,
                        config.state_file,
                    )
                    transaction.recovered_pending_commit = recovered is not None
                    trial_snapshot = _read_admin_snapshot(
                        admin,
                        config.state_file,
                    )
                    _snapshot_after_stop(
                        config,
                        transaction,
                        trial_snapshot=trial_snapshot,
                    )
                    transaction.trial_state_verified = True
                    ops.checkpoint("after_stop")
                    ops.checkpoint("after_snapshot")
                    _migrate(
                        config,
                        transaction,
                        admin,
                        trial_snapshot,
                    )
                    ops.checkpoint("after_migrate")
                    for name in ACTIVATION_GROUP:
                        _atomic_install(
                            config.candidate_dir / name,
                            config.deployed_dir / name,
                        )
                        transaction.code_touched = True
                        ops.checkpoint(f"after_install_{name}")
                    _atomic_install(config.candidate_unit_file, config.unit_file)
                    transaction.unit_touched = True
                    ops.checkpoint("after_unit_install")
                    ops.daemon_reload()
                    ops.checkpoint("after_daemon_reload")
                    _installed_matches_candidate(config)
                    if config.membership_store_file is not None:
                        membership_boundary(config.membership_store_file, config.candidate_dir)
                    unit_readback = ops.unit_readback(config)
                    ops.wait_until(config.cutover_at)
                    ops.checkpoint("after_cutover_wait")
                    # From this point onward the candidate process may accept a
                    # request before health or the next checkpoint returns.  A
                    # rollback must therefore keep the trial policy monotonic.
                    transaction.candidate_may_have_served = True
                    ops.start(config.service)
                    ops.checkpoint("after_start")
                    ops.health(port=config.port)
                    ops.checkpoint("after_health")
                    if config.membership_store_file is not None:
                        membership_boundary(config.membership_store_file, config.candidate_dir)
                    if transaction.append_only_boundaries_before is None:
                        raise DeploymentError(
                            "append-only protected snapshot is unavailable"
                        )
                    (
                        protected_after,
                        live_authority_presence_after,
                        _append_only_boundaries_after,
                    ) = _capture_protected_state(
                        _validate_candidate_unit(config),
                        append_only_boundaries=(
                            transaction.append_only_boundaries_before
                        ),
                    )
                    if transaction.protected_state_before is None:
                        raise DeploymentError(
                            "protected state snapshot is unavailable"
                        )
                    if transaction.live_authority_presence_before is None:
                        raise DeploymentError(
                            "live activation authority snapshot is unavailable"
                        )
                    _require_immutable_state_unchanged(
                        transaction.protected_state_before,
                        protected_after,
                    )
                    _require_live_authority_transition(
                        transaction.live_authority_presence_before,
                        live_authority_presence_after,
                    )
                    if transaction.manifest_path is None:
                        raise DeploymentError(
                            "transaction manifest unavailable"
                        )
                    _write_manifest(
                        transaction,
                        {
                            "schema_version": 1,
                            "status": "deployed",
                            "trial_state_sha256": _sha256(
                                _read_regular(config.state_file)
                            ),
                            "installed_core_api": EXPECTED_CORE_API,
                            "trial_admin_lock_protocol_version": (
                                TRIAL_ADMIN_LOCK_PROTOCOL_VERSION
                            ),
                            "trial_admin_lock_backend": (
                                TRIAL_ADMIN_LOCK_BACKEND
                            ),
                            "recovered_pending_trial_commit": (
                                transaction.recovered_pending_commit
                            ),
                            "trial_state_mode": config.trial_state_mode,
                            "upload_security_epoch_before": (
                                transaction.upload_security_epoch_before
                            ),
                            "upload_security_epoch_after": (
                                transaction.upload_security_epoch_after
                            ),
                            "activation_security_epoch_before": (
                                transaction.activation_security_epoch_before
                            ),
                            "activation_security_epoch_after": (
                                transaction.activation_security_epoch_after
                            ),
                            "protected_state_before": (
                                transaction.protected_state_before
                            ),
                            "protected_state_after": protected_after,
                            "unit_readback": unit_readback,
                        },
                    )
                    return transaction.manifest_path
                except BaseException as exc:
                    try:
                        _rollback(
                            config,
                            transaction,
                            admin,
                            ops,
                            reason=type(exc).__name__,
                        )
                    except BaseException as recovery_exc:
                        if isinstance(recovery_exc, MembershipRecoveryRequired):
                            raise recovery_exc
                        raise DeploymentError(
                            "deployment failed and state-aware recovery "
                            "did not complete; service must remain stopped"
                        ) from recovery_exc
                    raise DeploymentError(
                        "deployment failed; old code/unit and a compatible "
                        "trial-state authority were restored"
                    ) from exc
        except DeploymentError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DeploymentError(
                "trial admin lock could not cover the deployment transaction"
            ) from exc


def _inspect_preflight_mutable_path(
    contract: UnitContract,
    *,
    path: Path,
    role: str,
) -> None:
    """Validate a live mutable authority without freezing its contents."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        try:
            current = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise DeploymentError(
                f"mutable authoritative path cannot be inspected: {role}"
            ) from exc
        if not stat.S_ISREG(current.st_mode):
            raise DeploymentError(
                f"mutable authoritative path is not a regular file: {role}"
            )
        raise DeploymentError(
            f"mutable authoritative path changed while opening: {role}"
        )
    except OSError as exc:
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise DeploymentError(
                f"mutable authoritative path is not a regular file: {role}"
            ) from exc
        raise DeploymentError(
            f"mutable authoritative path cannot be opened: {role}"
        ) from exc

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DeploymentError(
                f"mutable authoritative path is not a regular file: {role}"
            )
        after = os.fstat(descriptor)
        before_identity = (
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_mode),
            int(before.st_uid),
            int(before.st_gid),
        )
        after_identity = (
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_mode),
            int(after.st_uid),
            int(after.st_gid),
        )
        if before_identity != after_identity:
            raise DeploymentError(
                f"mutable authoritative metadata changed: {role}"
            )
        if role in _LIVE_AUTHORITY_ROLES:
            _require_live_authority_metadata(
                contract,
                role=role,
                metadata=after,
            )
    finally:
        os.close(descriptor)

    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise DeploymentError(
            f"mutable authoritative path changed after inspection: {role}"
        ) from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
        or current.st_mode != after.st_mode
        or current.st_uid != after.st_uid
        or current.st_gid != after.st_gid
    ):
        raise DeploymentError(
            f"mutable authoritative path changed after inspection: {role}"
        )


def _preflight_authority_fingerprints(
    config: Config,
) -> dict[Path, tuple[bool, str | None, int | None, int | None]]:
    contract = _validate_candidate_unit(config)
    mutable_paths: dict[Path, tuple[str, ...]] = {}
    immutable_protected_paths: set[Path] = set()
    for role, path, mutable in contract.protected_paths:
        if mutable:
            mutable_paths[path] = (*mutable_paths.get(path, ()), role)
        else:
            immutable_protected_paths.add(path)

    # PRELIGHT deliberately runs while the old service remains live.  Mutable
    # authorities can therefore append or be atomically replaced between the
    # two snapshots.  Inspect their file identity and safety at each snapshot,
    # but do not require their bytes or generation to remain unchanged across
    # the whole preflight window.
    for path, roles in sorted(mutable_paths.items(), key=lambda item: str(item[0])):
        for role in roles:
            _inspect_preflight_mutable_path(
                contract,
                path=path,
                role=role,
            )

    paths = {
        config.unit_file,
        *immutable_protected_paths,
        *(
            config.deployed_dir / name
            for name in ACTIVATION_GROUP
        ),
        *config.state_file.parent.glob(
            f".{config.state_file.name}.trial-admin-*"
        ),
    }
    fingerprints: dict[
        Path,
        tuple[bool, str | None, int | None, int | None],
    ] = {}
    for path in paths:
        try:
            before = path.lstat()
        except FileNotFoundError:
            fingerprints[path] = (False, None, None, None)
            continue
        if not stat.S_ISREG(before.st_mode):
            raise DeploymentError(
                "immutable authoritative path is not a regular file"
            )
        raw = _read_regular(path)
        after = path.lstat()
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
        ):
            raise DeploymentError(
                "authoritative path changed during preflight fingerprint"
            )
        fingerprints[path] = (
            True,
            _sha256(raw),
            int(after.st_dev),
            int(after.st_ino),
        )
    return fingerprints


def preflight(config: Config, ops: Ops) -> None:
    """Validate without stopping the service or writing authoritative paths."""

    with _preflight_stage("authority_before"):
        before = _preflight_authority_fingerprints(config)
    ops.preflight(config)
    with _preflight_stage("authority_after"):
        after = _preflight_authority_fingerprints(config)
    if after != before:
        raise PreflightStageError(
            "immutable_authority_changed",
            "preflight modified an authoritative path",
        )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not (parsed > 0):
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _config_from_args(args: argparse.Namespace) -> Config:
    repo = Path(args.repo_dir).resolve()
    return Config(
        repo_dir=repo,
        candidate_dir=Path(args.candidate_dir).resolve(),
        server_update_script=Path(args.server_update_script).resolve(),
        unit_file=Path(args.unit_file).resolve(),
        candidate_unit_file=Path(args.candidate_unit_file).resolve(),
        state_file=Path(args.state_file).resolve(),
        upload_identity_db_file=Path(args.upload_identity_db_file).resolve(),
        upload_identity_mode=args.upload_identity_mode,
        upload_security_epoch=args.upload_security_epoch,
        upload_lease_seconds=args.upload_lease_seconds,
        min_client_security_epoch=args.min_client_security_epoch,
        min_client_version=args.min_client_version,
        v2_min_version=args.v2_min_version,
        pet_usage_db_file=Path(args.pet_usage_db_file).resolve(),
        pet_usage_lease_seconds=args.pet_usage_lease_seconds,
        service_user=args.service_user,
        service_group=args.service_group,
        working_directory=Path(args.working_directory).resolve(),
        service=args.service,
        port=args.port,
        cutover_at=args.cutover_at,
        new_trial_hours=args.new_trial_hours,
        grandfather_hours=args.grandfather_hours,
        legacy_trial_hours=args.legacy_trial_hours,
        backup_root=Path(
            args.backup_root
            or repo / ".activation_state_deploy_backups"
        ).resolve(),
        lock_file=Path(
            args.lock_file
            or repo / ".activation_state_deploy.lock.sqlite3"
        ).resolve(),
        python_executable=args.python_executable,
        trial_state_mode=args.trial_state_mode,
        membership_store_file=(Path(args.membership_store_file).absolute()
                               if args.membership_store_file else None),
        history_index_db_file=(Path(args.history_index_db_file).absolute()
                               if args.history_index_db_file else None),
    )


def _operator_failure_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, MembershipRecoveryRequired):
        return {"ok": False, "reason": "membership_compatible_recovery_required",
                "stage": "membership_recovery_required"}
    stage = "unclassified"
    if (
        type(exc) is PreflightStageError
        and type(exc.stage) is str
        and exc.stage in _PREFLIGHT_FAILURE_STAGES
    ):
        stage = exc.stage
    return {
        "ok": False,
        "reason": "deployment_error",
        "stage": stage,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "显式部署API7激活组；会员账本仅允许兼容恢复，不回灌购买记录"
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--server-update-script", required=True)
    parser.add_argument("--unit-file", required=True)
    parser.add_argument("--candidate-unit-file", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--membership-store-file")
    parser.add_argument("--history-index-db-file")
    parser.add_argument(
        "--trial-state-mode",
        choices=tuple(sorted(TRIAL_STATE_MODES)),
        required=True,
    )
    parser.add_argument("--upload-identity-db-file", required=True)
    parser.add_argument("--pet-usage-db-file", required=True)
    parser.add_argument(
        "--upload-identity-mode",
        choices=("disabled", "compat", "required"),
        required=True,
    )
    parser.add_argument(
        "--upload-security-epoch",
        type=_nonnegative_int,
        required=True,
    )
    parser.add_argument("--upload-lease-seconds", type=_positive_int, required=True)
    parser.add_argument(
        "--min-client-security-epoch",
        type=_nonnegative_int,
        required=True,
    )
    parser.add_argument("--min-client-version", required=True)
    parser.add_argument("--v2-min-version", required=True)
    parser.add_argument(
        "--pet-usage-lease-seconds",
        type=_positive_int,
        required=True,
    )
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--service-group", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--service", default="bidking-collector")
    parser.add_argument("--port", type=_positive_int, default=18900)
    parser.add_argument("--cutover-at", type=_positive_int, required=True)
    parser.add_argument(
        "--new-trial-hours",
        type=_positive_float,
        default=12.0,
    )
    parser.add_argument(
        "--grandfather-hours",
        type=_positive_float,
        default=12.0,
    )
    parser.add_argument(
        "--legacy-trial-hours",
        type=_positive_float,
        default=12.0,
    )
    parser.add_argument("--backup-root")
    parser.add_argument("--lock-file")
    parser.add_argument("--python-executable", default=sys.executable)
    args = parser.parse_args(argv)
    config = _config_from_args(args)
    try:
        if args.apply:
            if not hasattr(os, "geteuid") or os.geteuid() != 0:
                raise DeploymentError("--apply must be run explicitly as root")
            receipt = apply(config, RealOps())
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "deployed",
                        "receipt": str(receipt),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
        else:
            preflight(config, RealOps())
            print(
                json.dumps(
                    {"ok": True, "mode": "preflight_only"},
                    sort_keys=True,
                )
            )
        return 0
    except Exception as exc:  # noqa: BLE001 - operator boundary is fail-closed
        print(
            json.dumps(
                _operator_failure_payload(exc),
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
