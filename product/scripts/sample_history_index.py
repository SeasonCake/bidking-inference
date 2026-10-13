"""Private, rebuildable JSONL metadata index; original files remain authority.

No record payload, uid, token or credential is copied into this database. Its
reuse contract is one controlled append-only writer, not arbitrary same-stat
in-place mutation detection. Dates/eligibility come from the caller's explicitly
versioned existing parser. Opening an index never loads its historical hash set.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from typing import Callable, Iterable, Iterator
import uuid

_SCHEMA = 3
_APPLICATION_ID = 0x424B4858
_ANCHOR_BYTES = 4096
_BATCH = 256
_KINDS = frozenset({"main", "quarantine"})
_QUARANTINE_REASONS = frozenset({"warehouse_source_pending", "update606_map_unopened",
    "settlement_incomplete", "catalog_value_incomplete", "declared_shortfall", "invalid_value", "other"})
_UID_HASH = re.compile(r"[0-9a-f]{16}")


def _owner_tag(uid_hash: object) -> bytes | None:
    if type(uid_hash) is not str or _UID_HASH.fullmatch(uid_hash) is None:
        return None
    # Private acceleration only, never authorization or an exported identity.
    return hashlib.sha256(b"bidking-history-legacy-owner-v1\0" + uid_hash.encode("ascii")).digest()


def _association(session_key: bytes | None, owner_tag: bytes | None, digest: str) -> str:
    """Derived metadata consistency, not keyed authentication/authorization."""
    frame = b"history-row-association-v1\0" + digest.encode("ascii")
    for value in (session_key, owner_tag):
        frame += b"\0" if value is None else b"\1" + len(value).to_bytes(8, "big") + value
    return hashlib.sha256(frame).hexdigest()


class HistoryIndexError(RuntimeError):
    """Index not ready/stale/unavailable; never treat this as a membership miss."""


@dataclass(frozen=True)
class Projection:
    public_day: str | None = None
    play_day: str | None = None
    dashboard_allowed: bool = False
    play_error: bool = False
    quarantine_reason: str | None = None

    def __post_init__(self) -> None:
        for value in (self.public_day, self.play_day):
            if value is not None and (type(value) is not str or date.fromisoformat(value).isoformat() != value):
                raise ValueError("invalid projected day")
        if type(self.dashboard_allowed) is not bool or type(self.play_error) is not bool:
            raise ValueError("invalid projection flags")
        if self.quarantine_reason is not None and type(self.quarantine_reason) is not str:
            raise ValueError("invalid reason projection")


def _key(value: object) -> bytes:
    """Standard JSON scalar keys with the old Python set/Counter equivalence.

    True/1/1.0 share a key, but string '1' does not. The standard json decoder
    uses singleton NaN/Infinity constants; keep that historical JSONL behavior.
    Unsupported/unhashable values fail instead of becoming coerced strings.
    """
    if type(value) is str:
        return b"s:" + value.encode("utf-8", errors="surrogatepass")
    if type(value) in (bool, int):
        return f"n:{int(value)}/1".encode("ascii")
    if type(value) is float:
        if math.isnan(value):
            return b"nan"
        if math.isinf(value):
            return b"+inf" if value > 0 else b"-inf"
        numerator, denominator = value.as_integer_ratio()
        return f"n:{numerator}/{denominator}".encode("ascii")
    raise TypeError("unsupported history key")


def _state(path: Path) -> dict:
    try:
        st = path.stat()
    except FileNotFoundError:
        return {"exists": False, "device": 0, "inode": 0, "size": 0, "mtime_ns": 0}
    if not path.is_file():
        raise HistoryIndexError("source_not_regular_file")
    return {"exists": True, "device": int(st.st_dev), "inode": int(st.st_ino),
            "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def _anchors(path: Path, size: int) -> tuple[str, str, int]:
    if size == 0:
        return hashlib.sha256(b"").hexdigest(), hashlib.sha256(b"").hexdigest(), 0
    with path.open("rb") as stream:
        head = stream.read(min(_ANCHOR_BYTES, size))
        stream.seek(max(0, size - _ANCHOR_BYTES))
        tail = stream.read(min(_ANCHOR_BYTES, size))
    return hashlib.sha256(head).hexdigest(), hashlib.sha256(tail).hexdigest(), len(head) + len(tail)


class HistoryIndex:
    def __init__(self, db_path: Path, main: Path, quarantine: Path, *,
                 projector: Callable[[dict, str], Projection], parser_version: str):
        if type(parser_version) is not str or not parser_version:
            raise ValueError("explicit parser version required")
        self.path = Path(db_path).resolve()
        self.sources = {"main": Path(main).resolve(), "quarantine": Path(quarantine).resolve()}
        if self.path.is_relative_to(self.sources["main"].parent):
            raise ValueError("history database must be outside public sample directory")
        self.projector = projector
        self.parser_version = parser_version
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._read_cursors: set[sqlite3.Cursor] = set()
        self.ready = False
        self._needs_rebuild = False
        self.last_refresh: dict = {}

    def _connect(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA cache_size=-8192")
        connection.execute("PRAGMA temp_store=FILE")
        return connection

    @staticmethod
    def _assert_owned(path: Path) -> None:
        with path.open("rb") as stream:
            header = stream.read(100)
        if (len(header) < 100 or header[:16] != b"SQLite format 3\0"
                or int.from_bytes(header[68:72], "big") != _APPLICATION_ID):
            # An explicit mistaken CLI path must not rebuild a membership DB
            # or another file. Damaged ownership header needs operator action.
            raise HistoryIndexError("database_not_owned_history_index")

    @staticmethod
    def _schema(connection: sqlite3.Connection) -> None:
        connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
        connection.executescript("""
            CREATE TABLE configuration (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                parser_version TEXT NOT NULL, generation TEXT NOT NULL);
            CREATE TABLE sources (kind TEXT PRIMARY KEY, info TEXT NOT NULL);
            CREATE TABLE rows (kind TEXT NOT NULL, offset INTEGER NOT NULL, length INTEGER NOT NULL,
                digest TEXT NOT NULL, session_key BLOB, map_json TEXT, parsed INTEGER NOT NULL,
                public_day TEXT, play_day TEXT, dashboard_allowed INTEGER NOT NULL,
                play_error INTEGER NOT NULL, quarantine_reason TEXT, owner_tag BLOB,
                association TEXT NOT NULL, PRIMARY KEY(kind,offset)) WITHOUT ROWID;
            CREATE INDEX rows_public ON rows(kind,public_day,offset);
            CREATE INDEX rows_play ON rows(kind,play_day,offset);
            CREATE INDEX rows_play_error ON rows(kind,play_error,offset);
            CREATE INDEX rows_session ON rows(kind,session_key,offset);
            CREATE INDEX rows_owner ON rows(kind,owner_tag,offset);
            CREATE TABLE seen (kind TEXT NOT NULL, key BLOB NOT NULL, n INTEGER NOT NULL,
                PRIMARY KEY(kind,key)) WITHOUT ROWID;
        """)

    def _info(self, connection: sqlite3.Connection, kind: str) -> dict:
        row = connection.execute("SELECT info FROM sources WHERE kind=?", (kind,)).fetchone()
        if row is None:
            raise HistoryIndexError("source_metadata_missing")
        return json.loads(row[0])

    def _configuration(self, connection: sqlite3.Connection) -> tuple:
        row = connection.execute("SELECT version,parser_version,generation FROM configuration WHERE id=1").fetchone()
        if row is None or row[:2] != (_SCHEMA, self.parser_version):
            raise HistoryIndexError("parser_or_schema_changed")
        connection.execute("SELECT offset,length,digest,session_key,owner_tag,association FROM rows LIMIT 0")
        return row

    def _check_reusable(self, connection: sqlite3.Connection) -> bool:
        self._configuration(connection)
        for kind, path in self.sources.items():
            old = self._info(connection, kind)
            if old["path_digest"] != hashlib.sha256(os.fsencode(path)).hexdigest():
                return False
            now = _state(path)
            if (now["exists"], now["device"], now["inode"]) != (old["exists"], old["device"], old["inode"]):
                return False
            if now["size"] < old["size"]:
                return False
            if now["size"] == old["size"] and now["mtime_ns"] != old["mtime_ns"]:
                return False
            if old["exists"]:
                head, tail, consumed = _anchors(path, old["size"])
                self.last_refresh["validation_bytes"] += consumed
                if (head, tail) != (old["head"], old["tail"]):
                    return False
        return True

    def refresh(self) -> dict:
        """Validate/rebuild or scan only appended bytes, publishing after commit."""
        started = time.monotonic()
        with self._lock:
            completed = False
            try:
                self.last_refresh = {"rebuilt": False, "scan_bytes": 0, "scan_lines": 0, "validation_bytes": 0}
                if self._connection is None and self.path.is_file():
                    self._assert_owned(self.path)
                    try:
                        self._connection = self._connect(self.path)
                    except sqlite3.DatabaseError:
                        self._connection = None
                reusable = False
                if self._connection is not None:
                    try:
                        reusable = not self._needs_rebuild and self._check_reusable(self._connection)
                    except (sqlite3.DatabaseError, HistoryIndexError, KeyError, ValueError):
                        reusable = False
                if not reusable:
                    self.ready = False
                    self._rebuild()
                    self.last_refresh["rebuilt"] = True
                else:
                    connection = self._connection
                    assert connection is not None
                    with connection:
                        for kind in self.sources:
                            old = self._info(connection, kind)
                            if _state(self.sources[kind])["size"] > old["size"]:
                                self._scan(connection, kind, old)
                self.ready = True
                self._needs_rebuild = False
                completed = True
            except Exception:
                self.ready = False
                raise
            finally:
                # Interrupted transactions must not retain a published ready,
                # including BaseException subclasses that bypass except above.
                # Successful normal refresh keeps readers serialized by _lock.
                if not completed:
                    self.ready = False
                self.last_refresh["seconds"] = round(time.monotonic() - started, 6)
            return dict(self.last_refresh)

    def _rebuild(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt" and self.path.parent.stat().st_mode & 0o077:
            raise HistoryIndexError("index_directory_not_private")
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".building-", dir=self.path.parent)
        os.close(fd)
        temporary_path = Path(temporary)
        connection = self._connect(temporary_path)
        try:
            self._schema(connection)
            with connection:
                connection.execute("INSERT INTO configuration VALUES (1,?,?,?)",
                                   (_SCHEMA, self.parser_version, uuid.uuid4().hex))
                for kind, path in self.sources.items():
                    info = {**_state(path), "size": 0, "mtime_ns": 0, "tail_start": None,
                        "path_digest": hashlib.sha256(os.fsencode(path)).hexdigest(),
                        "counts": {"parsed": 0, "public_unknown": 0, "play_unknown": 0, "dashboard_parsed": 0,
                                   "dashboard_unknown": 0, "unique": 0}, "maps": [], "quarantine": {}}
                    self._scan(connection, kind, info)
            connection.close()
            if self._connection is not None:
                # sqlite3.Connection.close() alone can retain the database
                # handle until active statements finish (notably on Windows).
                # Cancel old-generation metadata readers before publication.
                for cursor in self._read_cursors:
                    cursor.close()
                self._read_cursors.clear()
                self._connection.close()
                self._connection = None
            if self.path.exists():
                # Preserve the last derived generation. This is not a history
                # file cleanup and does not authorize deleting raw samples.
                backup = self.path.with_name(self.path.name + ".previous-" + uuid.uuid4().hex)
                shutil.copyfile(self.path, backup)
                os.chmod(backup, 0o600)
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
            self._connection = self._connect(self.path)
        finally:
            connection.close()
            if temporary_path.exists():
                temporary_path.unlink()

    def _scan(self, connection: sqlite3.Connection, kind: str, info: dict) -> None:
        path = self.sources[kind]
        before = _state(path)
        anchor_before = _anchors(path, before["size"]) if before["exists"] else _anchors(path, 0)
        self.last_refresh["validation_bytes"] += anchor_before[2]
        maps = Counter({json.loads(key): n for key, n in info["maps"]})
        counts = info["counts"]
        summary = info["quarantine"]
        start = info["size"]
        if info["tail_start"] is not None:
            start = info["tail_start"]
            row = connection.execute("SELECT session_key,map_json,parsed,public_day,play_day,dashboard_allowed,quarantine_reason "
                                     "FROM rows WHERE kind=? AND offset=?", (kind, start)).fetchone()
            if row is not None:
                key, map_json, parsed, public_day, play_day, dashboard, reason = row
                if key is not None:
                    connection.execute("UPDATE seen SET n=n-1 WHERE kind=? AND key=?", (kind, key))
                    removed = connection.execute("DELETE FROM seen WHERE kind=? AND key=? AND n=0", (kind, key)).rowcount
                    counts["unique"] -= removed
                if map_json is not None:
                    value = json.loads(map_json)
                    maps[value] -= 1
                    if maps[value] == 0:
                        del maps[value]
                counts["parsed"] -= parsed
                counts["public_unknown"] -= int(bool(parsed) and public_day is None)
                counts["play_unknown"] -= int(bool(parsed) and play_day is None)
                counts["dashboard_parsed"] -= dashboard
                counts["dashboard_unknown"] -= int(bool(dashboard) and play_day is None)
                if reason is not None:
                    summary[reason] -= 1
                connection.execute("DELETE FROM rows WHERE kind=? AND offset=?", (kind, start))
        tail_start = None
        if before["exists"]:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = before["size"] - start
                while remaining > 0:
                    offset = stream.tell()
                    raw = stream.readline(remaining)
                    if not raw:
                        raise HistoryIndexError("source_short_read")
                    remaining -= len(raw)
                    self.last_refresh["scan_bytes"] += len(raw)
                    self.last_refresh["scan_lines"] += 1
                    if not raw.endswith(b"\n"):
                        tail_start = offset
                    parsed = dashboard = error = 0
                    session_key = map_json = public_day = play_day = reason = owner_tag = None
                    if raw.strip():
                        # Match the legacy history loader's strict UTF-8 and
                        # non-object failure behavior, not a new soft parser.
                        text = raw.decode("utf-8")
                        try:
                            record = json.loads(text)
                        except json.JSONDecodeError:
                            if kind == "quarantine":
                                reason = "unreadable"
                        else:
                            if type(record) is not dict:
                                if kind == "main":
                                    raise HistoryIndexError("history_record_not_object")
                                reason = "unreadable"  # legacy quarantine get() exception was skipped
                            else:
                                projection = self.projector(record, kind)
                                if type(projection) is not Projection:
                                    raise TypeError("invalid parser projection")
                                parsed = 1
                                owner_tag = _owner_tag(record.get("uid_hash")) if kind == "main" else None
                                public_day, play_day = projection.public_day, projection.play_day
                                dashboard, error = int(projection.dashboard_allowed), int(projection.play_error)
                                h = record.get("session_hash")
                                if h:
                                    session_key = _key(h)
                                    inserted = connection.execute("INSERT OR IGNORE INTO seen VALUES (?,?,1)", (kind, session_key)).rowcount
                                    if not inserted:
                                        connection.execute("UPDATE seen SET n=n+1 WHERE kind=? AND key=?", (kind, session_key))
                                    counts["unique"] += inserted
                                mid = record.get("map_id")
                                if kind == "main" and mid is not None:
                                    _key(mid)  # reject unsupported old Counter keys
                                    map_json = json.dumps(mid, ensure_ascii=True)
                                    maps[mid] += 1
                                if kind == "quarantine":
                                    candidate_reason = projection.quarantine_reason
                                    reason = candidate_reason if candidate_reason in _QUARANTINE_REASONS else "other"
                    counts["parsed"] += parsed
                    counts["public_unknown"] += int(bool(parsed) and public_day is None)
                    counts["play_unknown"] += int(bool(parsed) and play_day is None)
                    counts["dashboard_parsed"] += dashboard
                    counts["dashboard_unknown"] += int(bool(dashboard) and play_day is None)
                    if reason is not None:
                        summary[reason] = summary.get(reason, 0) + 1
                    digest = hashlib.sha256(raw).hexdigest()
                    connection.execute("INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (kind, offset, len(raw), digest, session_key, map_json,
                         parsed, public_day, play_day, dashboard, error, reason, owner_tag,
                         _association(session_key, owner_tag, digest)))
        after = _state(path)
        if ((after["exists"], after["device"], after["inode"]) != (before["exists"], before["device"], before["inode"])
                or after["size"] < before["size"]
                or (after["size"] == before["size"] and after["mtime_ns"] != before["mtime_ns"])):
            raise HistoryIndexError("source_changed_during_scan")
        head, tail, read = _anchors(path, before["size"]) if before["exists"] else _anchors(path, 0)
        self.last_refresh["validation_bytes"] += read
        if (head, tail) != anchor_before[:2]:
            raise HistoryIndexError("source_prefix_changed_during_scan")
        info = {**info, **before, "tail_start": tail_start, "head": head, "tail": tail,
                "maps": [[json.dumps(key, ensure_ascii=True), n] for key, n in maps.items()]}
        connection.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (kind, json.dumps(info, ensure_ascii=True)))

    def _require_ready(self) -> sqlite3.Connection:
        if not self.ready or self._connection is None:
            raise HistoryIndexError("history_index_not_ready")
        return self._connection

    def contains(self, kind: str, session_hash: object) -> bool:
        if kind not in _KINDS:
            raise ValueError("invalid source kind")
        with self._lock:
            connection = self._require_ready()
            if not session_hash:
                return False
            return connection.execute("SELECT 1 FROM seen WHERE kind=? AND key=?", (kind, _key(session_hash))).fetchone() is not None

    def summary(self) -> dict:
        with self._lock:
            connection = self._require_ready()
            main = self._info(connection, "main")
            quarantine = self._info(connection, "quarantine")
            return {"main_unique": main["counts"]["unique"], "quarantine_unique": quarantine["counts"]["unique"],
                    "map_count": Counter({json.loads(key): n for key, n in main["maps"]}),
                    "quarantine": dict(quarantine["quarantine"]), "quarantine_bytes": quarantine["size"]}

    def rows(self, *, view: str, start: date | None = None, end: date | None = None,
             snapshot_bytes: int | None = None, session_hashes: Iterable[object] | None = None,
             legacy_uid_hash: str | None = None) -> IndexedRows:
        if view not in {"public", "dashboard", "my"}:
            raise ValueError("invalid view")
        if view == "my" and session_hashes is None and legacy_uid_hash is None:
            raise ValueError("private candidate scope required")
        if legacy_uid_hash is not None and (view != "my" or session_hashes is not None or _owner_tag(legacy_uid_hash) is None):
            raise ValueError("invalid private candidate scope")
        return IndexedRows(self, view, start, end, snapshot_bytes, session_hashes, legacy_uid_hash)

    def close(self) -> None:
        with self._lock:
            for cursor in self._read_cursors:
                cursor.close()
            self._read_cursors.clear()
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self.ready = False


class IndexedRows:
    """Repeatable selected-offset reader. Counter metadata does not imply loaded payload."""
    def __init__(self, index: HistoryIndex, view: str, start: date | None, end: date | None,
                 snapshot_bytes: int | None, session_hashes: Iterable[object] | None, legacy_uid_hash: str | None):
        self.index, self.view, self.start, self.end = index, view, start, end
        with index._lock:
            connection = index._require_ready()
            self.generation = index._configuration(connection)[2]
            self.source_info = index._info(connection, "main")
        self.snapshot_bytes = self.source_info["size"] if snapshot_bytes is None else int(snapshot_bytes)
        if not 0 <= self.snapshot_bytes <= self.source_info["size"]:
            raise HistoryIndexError("snapshot_not_indexed")
        self.session_keys = None if session_hashes is None else {_key(h) for h in session_hashes}
        self.legacy_uid_hash = legacy_uid_hash
        self.request_id = uuid.uuid4().hex
        self.last_parsed_count = self.last_matched_count = self.last_unknown_date_count = 0
        self.passes = self.bytes_read = self.rows_read = 0

    def _partial(self, stream, fragment) -> dict | None:
        if fragment is None:
            return None
        offset, length = fragment
        stream.seek(offset)
        raw = stream.read(self.snapshot_bytes - offset)
        self.bytes_read += len(raw)
        self.rows_read += 1
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        if type(record) is not dict:
            return None
        if self.legacy_uid_hash is not None and record.get("uid_hash") != self.legacy_uid_hash:
            return None
        if self.session_keys is not None:
            h = record.get("session_hash")
            if not h or _key(h) not in self.session_keys:
                return None
        projection = self.index.projector(record, "main")
        if self.view == "dashboard" and not projection.dashboard_allowed:
            return None
        day = projection.public_day if self.view == "public" else projection.play_day
        self.last_parsed_count += 1
        if self.start is not None or self.end is not None:
            if day is None:
                self.last_unknown_date_count += 1
            if day is None:
                return record if self.view != "public" and projection.play_error else None
            if self.start is not None and day < self.start.isoformat():
                return None
            if self.end is not None and day > self.end.isoformat():
                return None
        return record

    def __iter__(self) -> Iterator[dict]:
        self.passes += 1
        index = self.index
        field = "public_day" if self.view == "public" else "play_day"
        predicates = ["kind='main'", "parsed=1", "offset+length<=?"]
        values: list = [self.snapshot_bytes]
        if self.view == "dashboard":
            predicates.append("dashboard_allowed=1")
        if self.session_keys is not None:
            predicates.append("session_key IN (SELECT key FROM history_requested_sessions WHERE request=?)")
            values.append(self.request_id)
        if self.legacy_uid_hash is not None:
            predicates.append("owner_tag=?")
            values.append(_owner_tag(self.legacy_uid_hash))
        base_predicates, base_values = list(predicates), list(values)
        dated = self.start is not None or self.end is not None
        if dated:
            dates = []
            if self.start is not None:
                dates.append(field + ">=?")
                values.append(self.start.isoformat())
            if self.end is not None:
                dates.append(field + "<=?")
                values.append(self.end.isoformat())
            condition = " AND ".join(dates)
            predicates.append("(" + condition + ")")
        where = " AND ".join(predicates)
        # Filter private sessions only after the caller obtains its existing
        # server-authorized session set; no raw UID is accepted or persisted.
        self.bytes_read = self.rows_read = 0
        path = index.sources["main"]
        state = _state(path)
        expected = self.source_info
        if (state["exists"], state["device"], state["inode"]) != (expected["exists"], expected["device"], expected["inode"]) or state["size"] < self.snapshot_bytes:
            raise HistoryIndexError("source_identity_changed")
        if state["size"] == expected["size"] and state["mtime_ns"] != expected["mtime_ns"]:
            raise HistoryIndexError("source_modified_without_append")
        with index._lock:
            connection = index._require_ready()
            if index._configuration(connection)[2] != self.generation:
                raise HistoryIndexError("index_generation_changed")
            if self.session_keys is not None:
                connection.execute("CREATE TEMP TABLE IF NOT EXISTS history_requested_sessions "
                                   "(request TEXT NOT NULL,key BLOB NOT NULL,PRIMARY KEY(request,key)) WITHOUT ROWID")
                connection.executemany("INSERT OR IGNORE INTO history_requested_sessions VALUES (?,?)",
                                       ((self.request_id, key) for key in self.session_keys))
                connection.commit()
            # Global parsed/unknown counts are cheap metadata for the latest
            # snapshot. Older snapshots use metadata only, not raw JSONL scans.
            base = "kind='main' AND parsed=1 AND offset+length<=?"
            if self.view == "dashboard":
                base += " AND dashboard_allowed=1"
            count_values: list = [self.snapshot_bytes]
            if self.session_keys is not None:
                base += " AND session_key IN (SELECT key FROM history_requested_sessions WHERE request=?)"
                count_values.append(self.request_id)
            if self.legacy_uid_hash is not None:
                base += " AND owner_tag=?"
                count_values.append(_owner_tag(self.legacy_uid_hash))
            if self.session_keys is not None or self.legacy_uid_hash is not None:
                # Also validate out-of-window/unknown-date candidate metadata
                # before reporting private logical counts. A changed column
                # must not silently become somebody else's count. This is an
                # unkeyed consistency check, not malicious-DB authentication.
                cursor = connection.execute("SELECT session_key,owner_tag,digest,association FROM rows WHERE " + base, count_values)
                for session_key, tag, digest, association in cursor:
                    if _association(session_key, tag, digest) != association:
                        index.ready = False
                        index._needs_rebuild = True
                        self.last_parsed_count = self.last_unknown_date_count = 0
                        raise HistoryIndexError("private_candidate_metadata_mismatch")
            fragment = connection.execute("SELECT offset,length FROM rows WHERE kind='main' AND offset<? "
                                          "AND offset+length>? ORDER BY offset DESC LIMIT 1",
                                          (self.snapshot_bytes, self.snapshot_bytes)).fetchone()
            # A captured unsealed terminal row may have since been sealed and
            # replayed. Its old cached counts include the terminal record, but
            # current prefix metadata excludes it; _partial adds it once.
            if fragment is None and self.snapshot_bytes == self.source_info["size"] and self.session_keys is None and self.legacy_uid_hash is None:
                counts = self.source_info["counts"]
                self.last_parsed_count = counts["dashboard_parsed"] if self.view == "dashboard" else counts["parsed"]
                self.last_unknown_date_count = counts[("public_unknown" if self.view == "public" else
                    "dashboard_unknown" if self.view == "dashboard" else "play_unknown")]
            else:
                row = connection.execute("SELECT COUNT(*),SUM(" + field + " IS NULL) FROM rows WHERE " + base, count_values).fetchone()
                self.last_parsed_count, self.last_unknown_date_count = int(row[0]), int(row[1] or 0)
            if not dated:
                self.last_unknown_date_count = 0
            request_connection = connection
        matched = 0
        if not state["exists"]:
            self.last_matched_count = 0
            if self.session_keys is not None:
                with index._lock:
                    connection.execute("DELETE FROM history_requested_sessions WHERE request=?", (self.request_id,))
                    connection.commit()
            return
        cursor = None
        try:
            with path.open("rb") as stream:
                # Execute the date-range selection and offset ordering once.
                # Repeating LIMIT/offset batches would rescan and sort the
                # remaining window quadratically; SQLite keeps temp work on
                # disk, and only this bounded metadata batch enters Python.
                with index._lock:
                    table = "rows"
                    if dated:
                        table += " INDEXED BY " + ("rows_public" if self.view == "public" else "rows_play")
                    selected = "SELECT offset,length,digest,session_key FROM " + table + " WHERE " + where
                    query_values = list(values)
                    if dated and self.view != "public":
                        selected += " UNION ALL SELECT offset,length,digest,session_key FROM rows INDEXED BY rows_play_error WHERE " + " AND ".join(base_predicates) + " AND play_error=1 AND play_day IS NULL"
                        query_values.extend(base_values)
                    selected += " ORDER BY offset"
                    cursor = connection.execute(selected, query_values)
                    index._read_cursors.add(cursor)
                while True:
                    with index._lock:
                        connection = index._require_ready()
                        if index._configuration(connection)[2] != self.generation:
                            raise HistoryIndexError("index_generation_changed")
                        batch = cursor.fetchmany(_BATCH)
                    if not batch:
                        break
                    for offset, length, digest, session_key in batch:
                        with index._lock:
                            if index._require_ready() is not connection:
                                raise HistoryIndexError("index_generation_changed")
                        stream.seek(offset)
                        raw = stream.read(length)
                        self.bytes_read += len(raw)
                        self.rows_read += 1
                        if len(raw) != length or hashlib.sha256(raw).hexdigest() != digest:
                            raise HistoryIndexError("selected_row_changed")
                        record = json.loads(raw.decode("utf-8"))
                        if self.legacy_uid_hash is not None and record.get("uid_hash") != self.legacy_uid_hash:
                            # Treat a wrong candidate as unhealthy, not as a
                            # permission grant or somebody else's statistics.
                            self.last_parsed_count = self.last_unknown_date_count = 0
                            raise HistoryIndexError("legacy_owner_candidate_mismatch")
                        if self.session_keys is not None and _key(record.get("session_hash")) not in self.session_keys:
                            self.last_parsed_count = self.last_unknown_date_count = 0
                            raise HistoryIndexError("signed_session_candidate_mismatch")
                        matched += 1
                        yield record
                partial = self._partial(stream, fragment)
                if partial is not None:
                    matched += 1
                    yield partial
        finally:
            if cursor is not None:
                with index._lock:
                    index._read_cursors.discard(cursor)
                    try:
                        cursor.close()
                    except sqlite3.ProgrammingError:
                        pass  # a concurrent generation rebuild closed the old connection
            if self.session_keys is not None:
                with index._lock:
                    if index._connection is request_connection:
                        index._connection.execute("DELETE FROM history_requested_sessions WHERE request=?", (self.request_id,))
                        index._connection.commit()
        self.last_matched_count = matched
