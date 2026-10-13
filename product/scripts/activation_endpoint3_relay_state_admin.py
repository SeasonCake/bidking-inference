#!/usr/bin/env python3
"""Offline administration and read-only inspection for endpoint_3 replay state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


REPLAY_SCHEMA_VERSION = 1
REPLAY_APPLICATION_ID = 0x424B5233
MAX_AUTH_WINDOW_SECONDS = 300
_METADATA_SQL = (
    "CREATE TABLE relay_metadata ("
    "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
    "relay_security_epoch INTEGER NOT NULL CHECK(relay_security_epoch > 0), "
    "auth_window_seconds INTEGER NOT NULL "
    "CHECK(auth_window_seconds BETWEEN 1 AND 300)"
    ") WITHOUT ROWID"
)
_NONCE_SQL = (
    "CREATE TABLE relay_nonce ("
    "nonce TEXT PRIMARY KEY, "
    "expires_at INTEGER NOT NULL CHECK(expires_at >= 0)"
    ") WITHOUT ROWID"
)
_EXPECTED_TABLE_SQL = {
    "relay_metadata": _METADATA_SQL,
    "relay_nonce": _NONCE_SQL,
}
_EXPECTED_COLUMNS = {
    "relay_metadata": (
        (0, "singleton", "INTEGER", 1, None, 1),
        (1, "relay_security_epoch", "INTEGER", 1, None, 0),
        (2, "auth_window_seconds", "INTEGER", 1, None, 0),
    ),
    "relay_nonce": (
        (0, "nonce", "TEXT", 1, None, 1),
        (1, "expires_at", "INTEGER", 1, None, 0),
    ),
}


class RelayReplayStateError(RuntimeError):
    """The replay database is missing, unsafe, corrupt, or incompatible."""


@dataclass(frozen=True)
class ReplayStateSnapshot:
    state: str
    database_schema_version: int | None = None
    relay_security_epoch: int | None = None
    auth_window_seconds: int | None = None
    sha256: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def public_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "database_schema_version": self.database_schema_version,
            "relay_security_epoch": self.relay_security_epoch,
            "auth_window_seconds": self.auth_window_seconds,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class _StableFileFingerprint:
    device: int
    inode: int
    link_count: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


def _positive_int(value, *, field: str) -> int:
    if type(value) is not int or value <= 0:  # noqa: E721 - authority boundary
        raise TypeError(f"{field} must be an exact positive integer")
    return value


def _regular_file_snapshot(path: Path) -> tuple[os.stat_result, bytes]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RelayReplayStateError("unsafe")
    raw = path.read_bytes()
    after = path.lstat()
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise RelayReplayStateError("changed")
    return after, raw


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(path.resolve().as_posix(), safe="/:\\") + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _database_sidecars(path: Path) -> tuple[Path, Path, Path]:
    return tuple(
        path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm")
    )


def _same_file_identity(expected: os.stat_result, actual: os.stat_result) -> bool:
    return (
        stat.S_ISREG(actual.st_mode)
        and expected.st_dev == actual.st_dev
        and expected.st_ino == actual.st_ino
    )


def _stable_regular_file_fingerprint(path: Path) -> _StableFileFingerprint:
    """Read one no-follow regular-file generation through a stable handle."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RelayReplayStateError("unsafe_private_state")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    path_view = os.stat(path, follow_symlinks=False)
    handle_stable_fields = (
        "st_dev",
        "st_ino",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if (
        not stat.S_ISREG(after.st_mode)
        or not stat.S_ISREG(path_view.st_mode)
        or any(
            getattr(before, field) != getattr(after, field)
            for field in handle_stable_fields
        )
        or after.st_dev != path_view.st_dev
        or after.st_ino != path_view.st_ino
        or total != after.st_size
    ):
        raise RelayReplayStateError("private_identity_mismatch")
    return _StableFileFingerprint(
        device=int(after.st_dev),
        inode=int(after.st_ino),
        link_count=int(after.st_nlink),
        size=int(after.st_size),
        mtime_ns=int(after.st_mtime_ns),
        ctime_ns=int(after.st_ctime_ns),
        sha256=digest.hexdigest(),
    )


def _fingerprint_matches(
    expected: _StableFileFingerprint,
    actual: _StableFileFingerprint,
    *,
    expected_link_count: int | None = None,
    allow_ctime_change: bool = False,
) -> bool:
    return (
        expected.device == actual.device
        and expected.inode == actual.inode
        and expected.size == actual.size
        and expected.mtime_ns == actual.mtime_ns
        and (allow_ctime_change or expected.ctime_ns == actual.ctime_ns)
        and expected.sha256 == actual.sha256
        and actual.link_count
        == (
            expected.link_count
            if expected_link_count is None
            else expected_link_count
        )
    )


def _validate_published_file_identity(path: Path, expected: os.stat_result) -> None:
    """Linearize a successful publish without trusting a path-only schema check."""

    sidecars = _database_sidecars(path)
    try:
        if any(os.path.lexists(sidecar) for sidecar in sidecars):
            raise RelayReplayStateError("busy_or_dirty")
        actual = os.stat(path, follow_symlinks=False)
        if not _same_file_identity(expected, actual):
            raise RelayReplayStateError("published_identity_mismatch")
        if any(os.path.lexists(sidecar) for sidecar in sidecars):
            raise RelayReplayStateError("busy_or_dirty")
    except OSError as exc:
        raise RelayReplayStateError("published_identity_mismatch") from exc


def _cleanup_owned_private_files(
    paths: tuple[Path, ...],
    owned_fingerprints: dict[Path, _StableFileFingerprint],
) -> None:
    """Unlink only unchanged private generations proven to be ours."""

    conflict = False
    for path in paths:
        try:
            actual = _stable_regular_file_fingerprint(path)
        except FileNotFoundError:
            continue
        except (OSError, RelayReplayStateError):
            conflict = True
            continue
        expected = owned_fingerprints.get(path)
        if expected is None or not _fingerprint_matches(expected, actual):
            conflict = True
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            conflict = True
    if conflict:
        raise RelayReplayStateError("private_cleanup_conflict")


def _finish_private_cleanup(
    paths: tuple[Path, ...],
    owned_fingerprints: dict[Path, _StableFileFingerprint],
    *,
    published: bool,
    directory: Path,
) -> None:
    cleanup_error: RelayReplayStateError | None = None
    durability_error: OSError | None = None
    try:
        _cleanup_owned_private_files(paths, owned_fingerprints)
    except RelayReplayStateError as exc:
        cleanup_error = exc
    if published:
        try:
            _fsync_directory(directory)
        except OSError as exc:
            durability_error = exc
    if cleanup_error is not None:
        raise cleanup_error
    if durability_error is not None:
        raise RelayReplayStateError("state_error") from durability_error


def inspect_replay_database(
    path: Path,
    *,
    expected_security_epoch: int | None = None,
    expected_auth_window_seconds: int | None = None,
) -> ReplayStateSnapshot:
    """Inspect exact replay state without creating SQLite files or sidecars."""

    if not isinstance(path, Path):
        raise TypeError("replay path must be a pathlib Path")
    if expected_security_epoch is not None:
        _positive_int(expected_security_epoch, field="relay security epoch")
    if expected_auth_window_seconds is not None:
        _positive_int(expected_auth_window_seconds, field="auth window")
        if expected_auth_window_seconds > MAX_AUTH_WINDOW_SECONDS:
            raise TypeError("auth window exceeds fixed maximum")
    try:
        _info, raw = _regular_file_snapshot(path)
    except FileNotFoundError:
        return ReplayStateSnapshot("missing")
    except RelayReplayStateError as exc:
        return ReplayStateSnapshot(str(exc))
    except OSError:
        return ReplayStateSnapshot("unreadable")

    if any(os.path.lexists(sidecar) for sidecar in _database_sidecars(path)):
        return ReplayStateSnapshot("busy_or_dirty", sha256=hashlib.sha256(raw).hexdigest())

    digest = hashlib.sha256(raw).hexdigest()
    connection: sqlite3.Connection | None = None
    try:
        connection = _readonly_connection(path)
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchall()
            if quick_check != [("ok",)]:
                return ReplayStateSnapshot("corrupt", sha256=digest)
            application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if application_id != REPLAY_APPLICATION_ID:
                return ReplayStateSnapshot(
                    "incompatible", database_schema_version=user_version, sha256=digest
                )
            rows = connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
            expected_rows = [
                ("table", name, sql)
                for name, sql in sorted(_EXPECTED_TABLE_SQL.items())
            ]
            if rows != expected_rows:
                return ReplayStateSnapshot(
                    "incompatible", database_schema_version=user_version, sha256=digest
                )
            for table, expected in _EXPECTED_COLUMNS.items():
                actual = tuple(connection.execute(f"PRAGMA table_info({table})").fetchall())
                if actual != expected:
                    return ReplayStateSnapshot(
                        "incompatible", database_schema_version=user_version, sha256=digest
                    )
            metadata = connection.execute(
                "SELECT singleton, relay_security_epoch, auth_window_seconds "
                "FROM relay_metadata"
            ).fetchall()
            if len(metadata) != 1 or metadata[0][0] != 1:
                return ReplayStateSnapshot(
                    "incompatible", database_schema_version=user_version, sha256=digest
                )
            security_epoch = metadata[0][1]
            auth_window = metadata[0][2]
            if (
                type(security_epoch) is not int  # noqa: E721
                or security_epoch <= 0
                or type(auth_window) is not int  # noqa: E721
                or not (1 <= auth_window <= MAX_AUTH_WINDOW_SECONDS)
            ):
                return ReplayStateSnapshot(
                    "incompatible", database_schema_version=user_version, sha256=digest
                )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return ReplayStateSnapshot("corrupt", sha256=digest)

    state = "ready"
    if user_version != REPLAY_SCHEMA_VERSION:
        state = "incompatible"
    elif expected_security_epoch is not None and security_epoch != expected_security_epoch:
        state = "config_mismatch"
    elif (
        expected_auth_window_seconds is not None
        and auth_window != expected_auth_window_seconds
    ):
        state = "config_mismatch"
    return ReplayStateSnapshot(
        state,
        database_schema_version=user_version,
        relay_security_epoch=security_epoch,
        auth_window_seconds=auth_window,
        sha256=digest,
    )


def require_ready_replay_database(
    path: Path,
    *,
    security_epoch: int,
    auth_window_seconds: int,
) -> ReplayStateSnapshot:
    snapshot = inspect_replay_database(
        path,
        expected_security_epoch=security_epoch,
        expected_auth_window_seconds=auth_window_seconds,
    )
    if not snapshot.ready:
        raise RelayReplayStateError(snapshot.state)
    return snapshot


def _fsync_file(path: Path) -> None:
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def initialize_replay_database(
    path: Path,
    *,
    security_epoch: int,
    auth_window_seconds: int,
) -> str:
    """Create exact state offline without replacing any existing target."""

    if not isinstance(path, Path):
        raise TypeError("replay path must be a pathlib Path")
    security_epoch = _positive_int(security_epoch, field="relay security epoch")
    auth_window_seconds = _positive_int(auth_window_seconds, field="auth window")
    if auth_window_seconds > MAX_AUTH_WINDOW_SECONDS:
        raise TypeError("auth window exceeds fixed maximum")
    parent_info = path.parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise RelayReplayStateError("unsafe_parent")
    existing = inspect_replay_database(
        path,
        expected_security_epoch=security_epoch,
        expected_auth_window_seconds=auth_window_seconds,
    )
    if existing.ready:
        return "already_ready"
    if existing.state != "missing":
        raise RelayReplayStateError(existing.state)

    token = secrets.token_hex(16)
    temporary = path.with_name(f".{path.name}.init-{token}")
    sidecars = _database_sidecars(temporary)
    private_paths = (temporary, *sidecars)
    owned_private_fingerprints: dict[Path, _StableFileFingerprint] = {}
    published = False
    outcome: str | None = None
    published_fingerprint: _StableFileFingerprint | None = None
    try:
        try:
            connection = sqlite3.connect(temporary)
            try:
                created_info = os.stat(temporary, follow_symlinks=False)
                if not stat.S_ISREG(created_info.st_mode):
                    raise RelayReplayStateError("unsafe_private_state")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute(f"PRAGMA application_id={REPLAY_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={REPLAY_SCHEMA_VERSION}")
                connection.execute(_METADATA_SQL)
                connection.execute(_NONCE_SQL)
                connection.execute(
                    "INSERT INTO relay_metadata("
                    "singleton, relay_security_epoch, auth_window_seconds"
                    ") VALUES (1, ?, ?)",
                    (security_epoch, auth_window_seconds),
                )
                connection.commit()
            finally:
                connection.close()
            os.chmod(temporary, 0o600)
            _fsync_file(temporary)
            require_ready_replay_database(
                temporary,
                security_epoch=security_epoch,
                auth_window_seconds=auth_window_seconds,
            )
            unpublished_fingerprint = _stable_regular_file_fingerprint(temporary)
            if (
                created_info.st_dev != unpublished_fingerprint.device
                or created_info.st_ino != unpublished_fingerprint.inode
                or unpublished_fingerprint.link_count != 1
            ):
                raise RelayReplayStateError("private_identity_mismatch")
            owned_private_fingerprints[temporary] = unpublished_fingerprint
            if any(os.path.lexists(sidecar) for sidecar in _database_sidecars(path)):
                raise RelayReplayStateError("busy_or_dirty")
            try:
                os.link(temporary, path, follow_symlinks=False)
                published = True
            except FileExistsError:
                concurrent = inspect_replay_database(
                    path,
                    expected_security_epoch=security_epoch,
                    expected_auth_window_seconds=auth_window_seconds,
                )
                if concurrent.ready:
                    outcome = "already_ready"
                else:
                    raise RelayReplayStateError("concurrent_target_conflict")
            if published:
                _fsync_directory(path.parent)
                published_fingerprint = _stable_regular_file_fingerprint(temporary)
                if not _fingerprint_matches(
                    unpublished_fingerprint,
                    published_fingerprint,
                    expected_link_count=2,
                    allow_ctime_change=True,
                ):
                    raise RelayReplayStateError("published_identity_mismatch")
                owned_private_fingerprints[temporary] = published_fingerprint
                final_info = os.stat(path, follow_symlinks=False)
                if not _same_file_identity(created_info, final_info) or (
                    os.name == "posix" and stat.S_IMODE(final_info.st_mode) != 0o600
                ):
                    raise RelayReplayStateError("published_identity_mismatch")
                require_ready_replay_database(
                    path,
                    security_epoch=security_epoch,
                    auth_window_seconds=auth_window_seconds,
                )
                _validate_published_file_identity(path, created_info)
                outcome = "initialized"
        except (OSError, sqlite3.Error) as exc:
            raise RelayReplayStateError("state_error") from exc
    except BaseException as primary_error:
        try:
            _finish_private_cleanup(
                private_paths,
                owned_private_fingerprints,
                published=published,
                directory=path.parent,
            )
        except (OSError, RelayReplayStateError):
            setattr(primary_error, "cleanup_state", "failed")
            if hasattr(primary_error, "add_note"):
                primary_error.add_note("private_cleanup_or_durability_failure")
        raise

    _finish_private_cleanup(
        private_paths,
        owned_private_fingerprints,
        published=published,
        directory=path.parent,
    )
    if outcome == "initialized":
        if published_fingerprint is None:
            raise RelayReplayStateError("published_identity_mismatch")
        _validate_published_file_identity(path, created_info)
        final_fingerprint = _stable_regular_file_fingerprint(path)
        if not _fingerprint_matches(
            published_fingerprint,
            final_fingerprint,
            expected_link_count=1,
            allow_ctime_change=True,
        ):
            raise RelayReplayStateError("published_identity_mismatch")
        require_ready_replay_database(
            path,
            security_epoch=security_epoch,
            auth_window_seconds=auth_window_seconds,
        )
        if _stable_regular_file_fingerprint(path) != final_fingerprint:
            raise RelayReplayStateError("published_identity_mismatch")
        _validate_published_file_identity(path, created_info)
    elif outcome == "already_ready":
        require_ready_replay_database(
            path,
            security_epoch=security_epoch,
            auth_window_seconds=auth_window_seconds,
        )
    else:
        raise RelayReplayStateError("state_error")
    return outcome


def _positive_arg(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _auth_window_arg(value: str) -> int:
    parsed = _positive_arg(value)
    if parsed > MAX_AUTH_WINDOW_SECONDS:
        raise argparse.ArgumentTypeError("auth window exceeds fixed maximum")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--relay-security-epoch", required=True, type=_positive_arg)
    parser.add_argument("--auth-window-seconds", required=True, type=_auth_window_arg)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("init")
    args = parser.parse_args(argv)
    path = Path(args.db)
    try:
        if args.command == "init":
            state = initialize_replay_database(
                path,
                security_epoch=args.relay_security_epoch,
                auth_window_seconds=args.auth_window_seconds,
            )
            snapshot = require_ready_replay_database(
                path,
                security_epoch=args.relay_security_epoch,
                auth_window_seconds=args.auth_window_seconds,
            )
            payload = {"ok": True, "operation": "init", "state": state, **snapshot.public_dict()}
            code = 0
        else:
            snapshot = inspect_replay_database(
                path,
                expected_security_epoch=args.relay_security_epoch,
                expected_auth_window_seconds=args.auth_window_seconds,
            )
            payload = {"ok": snapshot.ready, "operation": "status", **snapshot.public_dict()}
            code = 0 if snapshot.ready else 2
    except (OSError, TypeError, ValueError, RelayReplayStateError) as exc:
        payload = {
            "ok": False,
            "operation": args.command,
            "state": str(exc) if type(exc) is RelayReplayStateError else type(exc).__name__,
        }
        code = 3
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
