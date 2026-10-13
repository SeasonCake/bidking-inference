#!/usr/bin/env python3
"""Private transactional index for trusted sample-upload identities.

The database stores only server-derived opaque identifiers.  It never stores an
activation token, raw device fingerprint, signed lease, request body, URL, IP, or
exception text, and it is deliberately separate from public/quarantine JSONL files.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


SCHEMA_VERSION = 3
APPLICATION_ID = 0x42554931  # "BUI1"
CLAIM_SECONDS = 15
_MAX_SAFE_EPOCH = 253_402_300_799
_HEX_16 = re.compile(r"^[0-9a-f]{16}$")
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DISPOSITIONS = frozenset({"accepted", "quarantined", "duplicate"})
_SUBJECT_KINDS = frozenset({"paid", "trial"})
_RESPONSE_KEYS = frozenset(
    {"accepted", "duplicate", "quarantined", "rejected"}
)
_SCHEMA_SQL = """
CREATE TABLE upload_requests (
    lease_key TEXT NOT NULL,
    nonce_key TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'final')),
    claimed_until INTEGER NOT NULL,
    claim_id TEXT NOT NULL,
    response_json TEXT,
    PRIMARY KEY (lease_key, nonce_key)
) WITHOUT ROWID;
CREATE TABLE upload_identities (
    id INTEGER PRIMARY KEY,
    session_hash TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('paid', 'trial')),
    uid_hash TEXT,
    subject_id TEXT NOT NULL,
    device_alias TEXT NOT NULL,
    security_epoch INTEGER NOT NULL,
    received_ts REAL NOT NULL,
    disposition TEXT NOT NULL CHECK (
        disposition IN ('pending', 'accepted', 'quarantined', 'duplicate')
    ),
    intended_disposition TEXT NOT NULL CHECK (
        intended_disposition IN ('accepted', 'quarantined', 'duplicate')
    ),
    body_sha256 TEXT NOT NULL,
    lease_key TEXT NOT NULL,
    nonce_key TEXT NOT NULL,
    UNIQUE(session_hash, subject_id, body_sha256, lease_key, nonce_key)
);
CREATE INDEX upload_identity_session_idx ON upload_identities(session_hash);
"""


class UploadIdentityStateError(RuntimeError):
    """Stable fail-closed state error without private path details."""


@dataclass(frozen=True)
class RequestReservation:
    state: str
    response: Optional[dict[str, int]] = None
    claim_id: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class IdentityReservation:
    state: str
    intended_disposition: Optional[str] = None
    claim_id: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class SessionAdminTarget:
    device_block_id: str
    subject_kinds: tuple[str, ...]
    dispositions: tuple[str, ...]
    security_epochs: tuple[int, ...]
    record_count: int


def _digest_opaque(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _exact_hex(value, pattern: re.Pattern[str], *, label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:  # noqa: E721
        raise UploadIdentityStateError(f"invalid {label}")
    return value


def _exact_positive_int(value, *, label: str) -> int:
    if (  # noqa: E721
        type(value) is not int
        or value <= 0
        or value > _MAX_SAFE_EPOCH
    ):
        raise UploadIdentityStateError(f"invalid {label}")
    return value


def _schema_fingerprint(connection: sqlite3.Connection) -> tuple:
    objects = tuple(
        (
            object_type,
            name,
            table_name,
            " ".join(sql.split()) if type(sql) is str else sql,
        )
        for object_type, name, table_name, sql in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    )
    tables = ("upload_requests", "upload_identities")
    columns = tuple(
        (table, tuple(connection.execute(f'PRAGMA table_xinfo("{table}")')))
        for table in tables
    )
    indexes = tuple(
        (
            table,
            tuple(
                sorted(
                    (name, unique, origin, partial)
                    for _, name, unique, origin, partial in connection.execute(
                        f'PRAGMA index_list("{table}")'
                    )
                )
            ),
        )
        for table in tables
    )
    index_columns = tuple(
        (
            table,
            name,
            tuple(connection.execute(f'PRAGMA index_xinfo("{name}")')),
        )
        for table, table_indexes in indexes
        for name, _, _, _ in table_indexes
    )
    return objects, columns, indexes, index_columns


def _expected_schema_fingerprint() -> tuple:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_SCHEMA_SQL)
        return _schema_fingerprint(connection)
    finally:
        connection.close()


_EXPECTED_SCHEMA_FINGERPRINT = _expected_schema_fingerprint()


def _database_sidecars(path: Path) -> tuple[Path, Path]:
    return Path(f"{path}-wal"), Path(f"{path}-shm")


def _open_database_read_only(
    path: Path,
    *,
    immutable: bool = False,
) -> sqlite3.Connection:
    try:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not resolved.is_file():
            raise UploadIdentityStateError("identity database is unavailable")
        query = "mode=ro&immutable=1" if immutable else "mode=ro"
        return sqlite3.connect(f"{resolved.as_uri()}?{query}", uri=True, timeout=5.0)
    except (OSError, sqlite3.Error) as exc:
        raise UploadIdentityStateError("identity database is unavailable") from exc


def _validate_schema_connection(connection: sqlite3.Connection) -> None:
    try:
        app_id = connection.execute("PRAGMA application_id").fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()
        result = connection.execute("PRAGMA quick_check").fetchone()
        fingerprint = _schema_fingerprint(connection)
    except sqlite3.Error as exc:
        raise UploadIdentityStateError("identity database schema mismatch") from exc
    if (
        app_id != (APPLICATION_ID,)
        or version != (SCHEMA_VERSION,)
        or result != ("ok",)
        or fingerprint != _EXPECTED_SCHEMA_FINGERPRINT
    ):
        raise UploadIdentityStateError("identity database schema mismatch")


def _validate_database_path_read_only(path: Path) -> None:
    connection = _open_database_read_only(path, immutable=True)
    try:
        _validate_schema_connection(connection)
    finally:
        connection.close()


def _same_file_identity(expected: os.stat_result, actual: os.stat_result) -> bool:
    return (
        stat.S_ISREG(actual.st_mode)
        and expected.st_dev == actual.st_dev
        and expected.st_ino == actual.st_ino
    )


def _validate_published_file_identity(path: Path, expected: os.stat_result) -> None:
    sidecars = _database_sidecars(path)
    try:
        if any(os.path.lexists(sidecar) for sidecar in sidecars):
            raise UploadIdentityStateError("identity database initialization failed")
        actual = os.stat(path, follow_symlinks=False)
        if not _same_file_identity(expected, actual):
            raise UploadIdentityStateError("identity database initialization failed")
        if any(os.path.lexists(sidecar) for sidecar in sidecars):
            raise UploadIdentityStateError("identity database initialization failed")
    except OSError as exc:
        raise UploadIdentityStateError("identity database initialization failed") from exc


def _fsync_file(path: Path, expected: os.stat_result) -> None:
    access = os.O_RDWR if os.name == "nt" else os.O_RDONLY
    flags = access | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UploadIdentityStateError("identity database initialization failed") from exc
    try:
        if not _same_file_identity(expected, os.fstat(descriptor)):
            raise UploadIdentityStateError("identity database initialization failed")
        os.fsync(descriptor)
    except OSError as exc:
        raise UploadIdentityStateError("identity database initialization failed") from exc
    finally:
        os.close(descriptor)


def _fsync_parent_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path.parent, flags)
    except OSError as exc:
        raise UploadIdentityStateError("identity database initialization failed") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise UploadIdentityStateError("identity database initialization failed") from exc
    finally:
        os.close(descriptor)


def _remove_private_temp_database(path: Path) -> None:
    for candidate in (*_database_sidecars(path), path):
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            raise UploadIdentityStateError("identity database initialization failed") from exc


def _create_private_database(path: Path) -> None:
    try:
        connection = sqlite3.connect(str(path), timeout=5.0)
    except sqlite3.Error as exc:
        raise UploadIdentityStateError("identity database initialization failed") from exc
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.executescript(_SCHEMA_SQL)
        connection.commit()
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if (
            type(checkpoint) is not tuple
            or len(checkpoint) != 3
            or any(type(value) is not int for value in checkpoint)
            or checkpoint[0] != 0
        ):
            raise UploadIdentityStateError("identity database initialization failed")
    except sqlite3.Error as exc:
        connection.rollback()
        raise UploadIdentityStateError("identity database initialization failed") from exc
    finally:
        connection.close()


def initialize_upload_identity_database(path: Path) -> None:
    """Create the authority database explicitly; the runtime never guesses migration."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(target):
        _validate_database_path_read_only(target)
        return
    if any(os.path.lexists(sidecar) for sidecar in _database_sidecars(target)):
        raise UploadIdentityStateError("identity database initialization failed")
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.init-",
            suffix=".sqlite3",
            dir=target.parent,
        )
    except OSError as exc:
        raise UploadIdentityStateError("identity database initialization failed") from exc
    temp_path = Path(temp_name)
    try:
        temp_identity = os.fstat(descriptor)
        os.close(descriptor)
        _create_private_database(temp_path)
        for sidecar in _database_sidecars(temp_path):
            try:
                sidecar.unlink(missing_ok=True)
            except OSError as exc:
                raise UploadIdentityStateError(
                    "identity database initialization failed"
                ) from exc
        _validate_database_path_read_only(temp_path)
        _fsync_file(temp_path, temp_identity)
        if any(os.path.lexists(sidecar) for sidecar in _database_sidecars(target)):
            raise UploadIdentityStateError("identity database initialization failed")
        try:
            os.link(temp_path, target, follow_symlinks=False)
        except FileExistsError:
            _validate_database_path_read_only(target)
            return
        except OSError as exc:
            raise UploadIdentityStateError(
                "identity database initialization failed"
            ) from exc
        try:
            target_identity = os.stat(target, follow_symlinks=False)
        except OSError as exc:
            raise UploadIdentityStateError(
                "identity database initialization failed"
            ) from exc
        if not _same_file_identity(temp_identity, target_identity):
            raise UploadIdentityStateError("identity database initialization failed")
        _fsync_file(target, temp_identity)
        _validate_database_path_read_only(target)
        _validate_published_file_identity(target, temp_identity)
        _fsync_parent_directory(target)
    finally:
        _remove_private_temp_database(temp_path)


class UploadIdentityStore:
    def __init__(self, path: Path, *, create: bool = False) -> None:
        self.path = Path(path)
        if create and not self.path.exists():
            initialize_upload_identity_database(self.path)
        self._validate_file()
        self._validate_schema()

    def _validate_file(self) -> None:
        try:
            if self.path.is_symlink() or not self.path.is_file():
                raise UploadIdentityStateError("identity database is unavailable")
        except OSError as exc:
            raise UploadIdentityStateError("identity database is unavailable") from exc

    def _connect(self) -> sqlite3.Connection:
        self._validate_file()
        try:
            connection = sqlite3.connect(str(self.path), timeout=5.0)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except sqlite3.Error as exc:
            raise UploadIdentityStateError("identity database is unavailable") from exc

    def _validate_schema(self) -> None:
        connection = _open_database_read_only(self.path)
        try:
            _validate_schema_connection(connection)
        finally:
            connection.close()

    @staticmethod
    def _assert_current_request_claim(
        connection: sqlite3.Connection,
        *,
        lease_key: str,
        nonce_key: str,
        body_sha256: str,
        claim_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT body_sha256, state, claim_id FROM upload_requests "
            "WHERE lease_key=? AND nonce_key=?",
            (lease_key, nonce_key),
        ).fetchone()
        if row != (body_sha256, "pending", claim_id):
            raise UploadIdentityStateError("request claim lost")

    def reserve_request(
        self,
        *,
        lease_id: str,
        nonce: str,
        body_sha256: str,
        expires_at: int,
        now: int,
    ) -> RequestReservation:
        lease_id = _exact_hex(lease_id, _HEX_32, label="lease id")
        nonce = _exact_hex(nonce, _HEX_32, label="request nonce")
        body_sha256 = _exact_hex(body_sha256, _HEX_64, label="request digest")
        expires_at = _exact_positive_int(expires_at, label="request expiry")
        now = _exact_positive_int(now, label="server time")
        if expires_at <= now:
            raise UploadIdentityStateError("request lease expired")
        lease_key = _digest_opaque(lease_id)
        nonce_key = _digest_opaque(nonce)
        next_claim_id = _exact_hex(
            secrets.token_hex(16),
            _HEX_32,
            label="request claim",
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM upload_requests WHERE expires_at < ? AND state = 'final'",
                (now,),
            )
            row = connection.execute(
                "SELECT body_sha256, state, claimed_until, claim_id, response_json "
                "FROM upload_requests WHERE lease_key=? AND nonce_key=?",
                (lease_key, nonce_key),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO upload_requests (lease_key, nonce_key, body_sha256, "
                    "expires_at, state, claimed_until, claim_id, response_json) "
                    "VALUES (?, ?, ?, ?, 'pending', ?, ?, NULL)",
                    (
                        lease_key,
                        nonce_key,
                        body_sha256,
                        expires_at,
                        now + CLAIM_SECONDS,
                        next_claim_id,
                    ),
                )
                connection.commit()
                return RequestReservation("reserved", claim_id=next_claim_id)
            stored_digest, state, claimed_until, current_claim_id, response_json = row
            if stored_digest != body_sha256:
                connection.commit()
                return RequestReservation("replay_rejected")
            if state == "final":
                try:
                    response = json.loads(response_json)
                except (TypeError, ValueError) as exc:
                    raise UploadIdentityStateError("stored replay response is invalid") from exc
                if not _valid_response(response):
                    raise UploadIdentityStateError("stored replay response is invalid")
                connection.commit()
                return RequestReservation("replayed", response=dict(response))
            if type(claimed_until) is not int or claimed_until > now:  # noqa: E721
                connection.commit()
                return RequestReservation("in_flight")
            if next_claim_id == current_claim_id:
                next_claim_id = _exact_hex(
                    secrets.token_hex(16),
                    _HEX_32,
                    label="request claim",
                )
                if next_claim_id == current_claim_id:
                    raise UploadIdentityStateError("request claim did not rotate")
            updated = connection.execute(
                "UPDATE upload_requests SET claimed_until=?, expires_at=?, claim_id=? "
                "WHERE lease_key=? AND nonce_key=? AND state='pending' "
                "AND claimed_until<=? AND claim_id=?",
                (
                    now + CLAIM_SECONDS,
                    expires_at,
                    next_claim_id,
                    lease_key,
                    nonce_key,
                    now,
                    current_claim_id,
                ),
            )
            if updated.rowcount != 1:
                raise UploadIdentityStateError("request claim changed")
            connection.commit()
            return RequestReservation("resumed", claim_id=next_claim_id)
        except UploadIdentityStateError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise UploadIdentityStateError("request replay state failed") from exc
        finally:
            connection.close()

    def finalize_request(
        self,
        *,
        lease_id: str,
        nonce: str,
        body_sha256: str,
        response: dict[str, int],
        claim_id: str,
    ) -> None:
        lease_id = _exact_hex(lease_id, _HEX_32, label="lease id")
        nonce = _exact_hex(nonce, _HEX_32, label="request nonce")
        body_sha256 = _exact_hex(body_sha256, _HEX_64, label="request digest")
        claim_id = _exact_hex(claim_id, _HEX_32, label="request claim")
        if not _valid_response(response):
            raise UploadIdentityStateError("invalid replay response")
        lease_key = _digest_opaque(lease_id)
        nonce_key = _digest_opaque(nonce)
        payload = json.dumps(response, sort_keys=True, separators=(",", ":"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_current_request_claim(
                connection,
                lease_key=lease_key,
                nonce_key=nonce_key,
                body_sha256=body_sha256,
                claim_id=claim_id,
            )
            updated = connection.execute(
                "UPDATE upload_requests SET state='final', response_json=? "
                "WHERE lease_key=? AND nonce_key=? AND state='pending' AND claim_id=?",
                (payload, lease_key, nonce_key, claim_id),
            )
            if updated.rowcount != 1:
                raise UploadIdentityStateError("request claim lost")
            connection.commit()
        except UploadIdentityStateError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise UploadIdentityStateError("request replay finalize failed") from exc
        finally:
            connection.close()

    def prepare_identity(
        self,
        *,
        session_hash: str,
        subject_kind: str,
        uid_hash: Optional[str],
        subject_id: str,
        device_alias: str,
        security_epoch: int,
        received_ts: float,
        intended_disposition: str,
        body_sha256: str,
        lease_id: str,
        nonce: str,
        claim_id: str,
    ) -> IdentityReservation:
        values = _validated_identity_values(
            session_hash=session_hash,
            subject_kind=subject_kind,
            uid_hash=uid_hash,
            subject_id=subject_id,
            device_alias=device_alias,
            security_epoch=security_epoch,
            received_ts=received_ts,
            disposition=intended_disposition,
            body_sha256=body_sha256,
            lease_id=lease_id,
            nonce=nonce,
        )
        claim_id = _exact_hex(claim_id, _HEX_32, label="request claim")
        lease_key = _digest_opaque(values["lease_id"])
        nonce_key = _digest_opaque(values["nonce"])
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_current_request_claim(
                connection,
                lease_key=lease_key,
                nonce_key=nonce_key,
                body_sha256=values["body_sha256"],
                claim_id=claim_id,
            )
            exact = connection.execute(
                "SELECT disposition, intended_disposition FROM upload_identities "
                "WHERE session_hash=? AND subject_id=? AND body_sha256=? "
                "AND lease_key=? AND nonce_key=?",
                (
                    values["session_hash"],
                    values["subject_id"],
                    values["body_sha256"],
                    lease_key,
                    nonce_key,
                ),
            ).fetchone()
            if exact is not None:
                disposition, intended = exact
                if disposition == "pending":
                    connection.commit()
                    return IdentityReservation("resumed", intended, claim_id)
                connection.commit()
                return IdentityReservation("existing", disposition, claim_id)
            same_identity = connection.execute(
                "SELECT 1 FROM upload_identities WHERE session_hash=? "
                "AND subject_id=? AND body_sha256=? LIMIT 1",
                (
                    values["session_hash"],
                    values["subject_id"],
                    values["body_sha256"],
                ),
            ).fetchone()
            session_exists = connection.execute(
                "SELECT 1 FROM upload_identities WHERE session_hash=? LIMIT 1",
                (values["session_hash"],),
            ).fetchone()
            intended = (
                "quarantined"
                if session_exists is not None and same_identity is None
                else values["disposition"]
            )
            connection.execute(
                "INSERT INTO upload_identities (session_hash, subject_kind, uid_hash, "
                "subject_id, device_alias, security_epoch, received_ts, disposition, "
                "intended_disposition, body_sha256, lease_key, nonce_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    values["session_hash"],
                    values["subject_kind"],
                    values["uid_hash"],
                    values["subject_id"],
                    values["device_alias"],
                    values["security_epoch"],
                    values["received_ts"],
                    intended,
                    values["body_sha256"],
                    lease_key,
                    nonce_key,
                ),
            )
            connection.commit()
            return IdentityReservation("prepared", intended, claim_id)
        except UploadIdentityStateError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise UploadIdentityStateError("identity intent failed") from exc
        finally:
            connection.close()

    def finalize_identity(
        self,
        *,
        session_hash: str,
        subject_id: str,
        body_sha256: str,
        lease_id: str,
        nonce: str,
        disposition: str,
        claim_id: str,
        append_action: Optional[Callable[[], None]] = None,
    ) -> None:
        if type(session_hash) is not str or _SESSION.fullmatch(session_hash) is None:  # noqa: E721
            raise UploadIdentityStateError("invalid session hash")
        subject_id = _exact_hex(subject_id, _HEX_64, label="subject id")
        body_sha256 = _exact_hex(body_sha256, _HEX_64, label="sample digest")
        lease_key = _digest_opaque(_exact_hex(lease_id, _HEX_32, label="lease id"))
        nonce_key = _digest_opaque(_exact_hex(nonce, _HEX_32, label="request nonce"))
        claim_id = _exact_hex(claim_id, _HEX_32, label="request claim")
        if type(disposition) is not str or disposition not in _DISPOSITIONS:  # noqa: E721
            raise UploadIdentityStateError("invalid disposition")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_current_request_claim(
                connection,
                lease_key=lease_key,
                nonce_key=nonce_key,
                body_sha256=body_sha256,
                claim_id=claim_id,
            )
            row = connection.execute(
                "SELECT disposition, intended_disposition FROM upload_identities "
                "WHERE session_hash=? AND subject_id=? AND body_sha256=? "
                "AND lease_key=? AND nonce_key=?",
                (session_hash, subject_id, body_sha256, lease_key, nonce_key),
            ).fetchone()
            if row is None:
                raise UploadIdentityStateError("identity intent missing")
            current_disposition, intended = row
            if current_disposition != "pending":
                raise UploadIdentityStateError("identity intent already finalized")
            if disposition != intended and disposition != "duplicate":
                raise UploadIdentityStateError("identity disposition mismatch")
            if append_action is not None:
                try:
                    append_action()
                except Exception as exc:  # noqa: BLE001
                    raise UploadIdentityStateError("public append failed") from exc
            updated = connection.execute(
                "UPDATE upload_identities SET disposition=? "
                "WHERE session_hash=? AND subject_id=? AND body_sha256=? "
                "AND lease_key=? AND nonce_key=? AND disposition='pending'",
                (
                    disposition,
                    session_hash,
                    subject_id,
                    body_sha256,
                    lease_key,
                    nonce_key,
                ),
            )
            if updated.rowcount != 1:
                raise UploadIdentityStateError("identity claim lost")
            connection.commit()
        except UploadIdentityStateError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise UploadIdentityStateError("identity finalize failed") from exc
        finally:
            connection.close()

    def redacted_session(self, session_hash: str) -> list[dict]:
        if type(session_hash) is not str or _SESSION.fullmatch(session_hash) is None:  # noqa: E721
            raise UploadIdentityStateError("invalid session hash")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT subject_kind, uid_hash, subject_id, device_alias, "
                "security_epoch, received_ts, disposition FROM upload_identities "
                "WHERE session_hash=? ORDER BY id",
                (session_hash,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise UploadIdentityStateError("identity lookup failed") from exc
        finally:
            connection.close()
        return [
            {
                "subject_kind": row[0],
                "uid_alias": row[1] if row[1] is not None else row[2][:16],
                "device_alias": row[3][:16],
                "security_epoch": row[4],
                "received_ts": row[5],
                "disposition": row[6],
            }
            for row in rows
        ]

    def session_admin_target(self, session_hash: str) -> SessionAdminTarget:
        """Resolve one trusted session to one private device-block identifier."""
        if type(session_hash) is not str or _SESSION.fullmatch(session_hash) is None:  # noqa: E721
            raise UploadIdentityStateError("invalid session hash")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT subject_kind, device_alias, security_epoch, disposition "
                "FROM upload_identities WHERE session_hash=? "
                "AND disposition!='pending' ORDER BY id",
                (session_hash,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise UploadIdentityStateError("identity lookup failed") from exc
        finally:
            connection.close()
        if not rows:
            raise UploadIdentityStateError("trusted session unavailable")
        devices: set[str] = set()
        subject_kinds: set[str] = set()
        security_epochs: set[int] = set()
        dispositions: set[str] = set()
        for subject_kind, device_alias, security_epoch, disposition in rows:
            if subject_kind not in _SUBJECT_KINDS or disposition not in _DISPOSITIONS:
                raise UploadIdentityStateError("invalid session identity")
            devices.add(_exact_hex(device_alias, _HEX_64, label="device block id"))
            subject_kinds.add(subject_kind)
            security_epochs.add(
                _exact_positive_int(security_epoch, label="security epoch")
            )
            dispositions.add(disposition)
        if len(devices) != 1:
            raise UploadIdentityStateError("session identity conflict")
        return SessionAdminTarget(
            device_block_id=next(iter(devices)),
            subject_kinds=tuple(sorted(subject_kinds)),
            dispositions=tuple(sorted(dispositions)),
            security_epochs=tuple(sorted(security_epochs)),
            record_count=len(rows),
        )

    def sessions_for_uid_hash(self, uid_hash: str) -> frozenset[str]:
        """Return only trusted accepted sessions for the authenticated UID alias."""
        uid_hash = _exact_hex(uid_hash, _HEX_16, label="uid hash")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT session_hash FROM upload_identities "
                "WHERE uid_hash=? AND disposition='accepted'",
                (uid_hash,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise UploadIdentityStateError("identity lookup failed") from exc
        finally:
            connection.close()
        return frozenset(
            row[0]
            for row in rows
            if type(row[0]) is str and _SESSION.fullmatch(row[0]) is not None
        )


def _valid_response(value) -> bool:
    return (
        type(value) is dict
        and all(type(key) is str for key in dict.keys(value))
        and frozenset(dict.keys(value)) == _RESPONSE_KEYS
        and all(type(value[key]) is int and value[key] >= 0 for key in _RESPONSE_KEYS)
    )


def _validated_identity_values(**raw) -> dict:
    session_hash = raw["session_hash"]
    if type(session_hash) is not str or _SESSION.fullmatch(session_hash) is None:  # noqa: E721
        raise UploadIdentityStateError("invalid session hash")
    subject_kind = raw["subject_kind"]
    if type(subject_kind) is not str or subject_kind not in _SUBJECT_KINDS:  # noqa: E721
        raise UploadIdentityStateError("invalid subject kind")
    uid_hash = raw["uid_hash"]
    if subject_kind == "paid":
        uid_hash = _exact_hex(uid_hash, _HEX_16, label="uid hash")
    elif uid_hash is not None:
        raise UploadIdentityStateError("trial identity cannot have uid hash")
    subject_id = _exact_hex(raw["subject_id"], _HEX_64, label="subject id")
    device_alias = _exact_hex(raw["device_alias"], _HEX_64, label="device alias")
    security_epoch = _exact_positive_int(raw["security_epoch"], label="security epoch")
    received_ts = raw["received_ts"]
    if (
        type(received_ts) not in (int, float)
        or not math.isfinite(received_ts)
        or received_ts <= 0
        or received_ts > _MAX_SAFE_EPOCH
    ):
        raise UploadIdentityStateError("invalid received timestamp")
    disposition = raw["disposition"]
    if type(disposition) is not str or disposition not in _DISPOSITIONS:  # noqa: E721
        raise UploadIdentityStateError("invalid disposition")
    return {
        "session_hash": session_hash,
        "subject_kind": subject_kind,
        "uid_hash": uid_hash,
        "subject_id": subject_id,
        "device_alias": device_alias,
        "security_epoch": security_epoch,
        "received_ts": float(received_ts),
        "disposition": disposition,
        "body_sha256": _exact_hex(raw["body_sha256"], _HEX_64, label="sample digest"),
        "lease_id": _exact_hex(raw["lease_id"], _HEX_32, label="lease id"),
        "nonce": _exact_hex(raw["nonce"], _HEX_32, label="request nonce"),
    }
