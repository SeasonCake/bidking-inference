from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402
import pet_usage_db_admin as admin  # noqa: E402


SECRET = b"pet-usage-sqlite-authority-secret"
MASTER_KEY = "S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s="
BASE_NOW = 2_100_000_000
DEVICE = "PET-SQLITE-AUTHORITY-DEVICE"
SESSION = "pet-session-sqlite-authority"


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    app = tmp_path / "application"
    authority = tmp_path / "systemd-state"
    app.mkdir(exist_ok=True)
    authority.mkdir(exist_ok=True)
    return (
        app,
        authority / "pet_usage.sqlite3",
        authority,
    )


def _initialize(tmp_path: Path) -> Path:
    app, database, _authority = _paths(tmp_path)
    if not database.exists():
        server.initialize_pet_usage_database(
            database,
            secret=SECRET,
            legacy_paths=(
                app / "pet_usage.json",
                app / "pet_usage.anchor.jsonl",
            ),
        )
    return database


def _service(tmp_path: Path, *, initialize: bool = True) -> server.ActivationService:
    app, database, _authority = _paths(tmp_path)
    if initialize:
        _initialize(tmp_path)
    return server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY,
        bindings_path=app / "bindings.json",
        revoked_path=app / "revoked.txt",
        telemetry_path=app / "telemetry.jsonl",
        trials_path=app / "trials.json",
        paid_starts_path=app / "paid_starts.json",
        pet_usage_db_path=database,
        pet_usage_path=app / "pet_usage.json",
        pet_usage_anchor_path=app / "pet_usage.anchor.jsonl",
        pet_usage_lease_seconds=15,
    )


def _token() -> str:
    issued_at = 1_700_000_000
    return core.mint_token(
        "pet-sqlite-authority-order",
        SECRET,
        iat=issued_at,
        exp=issued_at + 30 * 86_400,
    )


def _activate(
    service: server.ActivationService,
    *,
    seq: int | None = None,
    visible: bool = False,
) -> tuple[int, dict]:
    body: dict = {"token": _token(), "device": DEVICE}
    if seq is not None:
        body["pet_usage"] = {
            "session_id": SESSION,
            "seq": seq,
            "visible": visible,
        }
    return service.handle_activate(body)


def _lease(body: dict) -> dict:
    return json.loads(body["lease"].rsplit(".", 1)[0])


def _pet(body: dict) -> dict:
    return _lease(body)["feature_usage"]["pet_companion"]


def _consume_15_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[server.ActivationService, list[int], Path]:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    service = _service(tmp_path)

    code, opened = _activate(service, seq=0, visible=True)
    assert code == 200
    assert _pet(opened)["remaining_seconds"] == 43_200
    assert _pet(opened)["usage_revision"] == 1

    now[0] += 15
    code, closed = _activate(service, seq=1, visible=False)
    assert code == 200
    assert _pet(closed)["remaining_seconds"] == 43_185
    assert _pet(closed)["usage_revision"] == 2
    return service, now, _initialize(tmp_path)


def _assert_pet_state_error_preserves_calculator(
    service: server.ActivationService,
) -> None:
    code, body = _activate(service)
    assert code == 200
    assert body["ok"] is True
    assert "master_key" in body
    assert _pet(body)["status"] == "state_error"
    assert "remaining_seconds" not in _pet(body)


def test_application_backup_restore_cannot_roll_back_external_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _database, _authority = _paths(tmp_path)
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    service = _service(tmp_path)
    code, opened = _activate(service, seq=0, visible=True)
    assert code == 200
    assert _pet(opened)["usage_revision"] == 1

    backup = tmp_path / "release-backup"
    backup.mkdir()
    for path in app.iterdir():
        if path.is_file():
            shutil.copy2(path, backup / path.name)

    now[0] += 15
    code, closed = _activate(service, seq=1, visible=False)
    assert code == 200
    assert _pet(closed)["remaining_seconds"] == 43_185

    for path in backup.iterdir():
        shutil.copy2(path, app / path.name)
    restarted = _service(tmp_path)
    assert server.activation_readiness(restarted) == (
        200,
        {"ok": True, "service": "activation"},
    )
    code, body = _activate(restarted)
    assert code == 200
    assert _pet(body)["remaining_seconds"] == 43_185
    assert _pet(body)["usage_revision"] == 2


def test_legacy_json_pair_never_authorizes_or_resets_external_balance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _now, _database = _consume_15_seconds(tmp_path, monkeypatch)
    app, _database, _authority = _paths(tmp_path)
    (app / "pet_usage.json").write_text(
        '{"schema_version":2,"revision":1,"records":{}}',
        encoding="utf-8",
    )
    (app / "pet_usage.anchor.jsonl").write_text(
        '{"revision":1,"tag":"old-release-backup"}\n',
        encoding="utf-8",
    )

    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)
    full_token = core.mint_token(
        "pet-sqlite-full-plan",
        SECRET,
        iat=1_700_000_000,
        exp=1_700_000_000 + 90 * 86_400,
    )
    code, full = service.handle_activate(
        {"token": full_token, "device": "PET-SQLITE-FULL-DEVICE"}
    )
    assert code == 200
    assert full["ok"] is True
    assert _pet(full)["status"] == "state_error"

    (app / "pet_usage.json").unlink()
    (app / "pet_usage.anchor.jsonl").unlink()
    restarted = _service(tmp_path)
    code, body = _activate(restarted)
    assert code == 200
    assert _pet(body)["remaining_seconds"] == 43_185
    assert _pet(body)["usage_revision"] == 2


@pytest.mark.parametrize(
    "failure",
    ["missing", "corrupt", "schema_version", "schema_object"],
)
def test_missing_corrupt_or_wrong_schema_database_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    service = _service(tmp_path)
    if failure == "missing":
        database.unlink()
    elif failure == "corrupt":
        database.write_bytes(b"not-a-sqlite-database")
    elif failure == "schema_version":
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA user_version=999")
    else:
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE unexpected_state(value TEXT)")

    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_missing_corrupt_or_linked_external_head_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    head = server._pet_usage_head_path(database)
    service = _service(tmp_path)
    if failure == "missing":
        head.unlink()
    elif failure == "corrupt":
        head.write_bytes(b'{"not":"a-valid-head"}\n')

    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)


@pytest.mark.parametrize(
    ("kind", "name", "table", "sql"),
    [
        (
            "trigger",
            "sqlite_hidden_trigger",
            "pet_usage_commits",
            "CREATE TRIGGER sqlite_hidden_trigger BEFORE INSERT "
            "ON pet_usage_commits BEGIN SELECT RAISE(IGNORE); END",
        ),
        (
            "view",
            "sqlite_hidden_view",
            "sqlite_hidden_view",
            "CREATE VIEW sqlite_hidden_view AS SELECT 1 AS value",
        ),
        (
            "index",
            "sqlite_hidden_index",
            "pet_usage_commits",
            "CREATE INDEX sqlite_hidden_index "
            "ON pet_usage_commits (ledger_sha256)",
        ),
    ],
)
def test_sqlite_prefix_schema_objects_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    name: str,
    table: str,
    sql: str,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    service = _service(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            """
            INSERT INTO sqlite_schema(type, name, tbl_name, rootpage, sql)
            VALUES (?, ?, ?, 0, ?)
            """,
            (kind, name, table, sql),
        )
        connection.execute("PRAGMA writable_schema=OFF")

    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)


@pytest.mark.parametrize("table", ["pet_usage_state", "pet_usage_commits"])
def test_weakened_sqlite_constraints_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    table: str,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    service = _service(tmp_path)
    columns = {
        "pet_usage_state": (
            "singleton INTEGER PRIMARY KEY, "
            "schema_version INTEGER, "
            "revision INTEGER, "
            "ledger_json TEXT, "
            "ledger_sha256 TEXT, "
            "chain_tag TEXT"
        ),
        "pet_usage_commits": (
            "revision INTEGER PRIMARY KEY, "
            "ledger_sha256 TEXT, "
            "previous_tag TEXT, "
            "tag TEXT"
        ),
    }
    column_names = {
        "pet_usage_state": (
            "singleton, schema_version, revision, ledger_json, "
            "ledger_sha256, chain_tag"
        ),
        "pet_usage_commits": (
            "revision, ledger_sha256, previous_tag, tag"
        ),
    }
    with sqlite3.connect(database) as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO weakened_source")
        connection.execute(
            f"CREATE TABLE {table} ({columns[table]}) WITHOUT ROWID"
        )
        connection.execute(
            f"INSERT INTO {table} ({column_names[table]}) "
            f"SELECT {column_names[table]} FROM weakened_source"
        )
        connection.execute("DROP TABLE weakened_source")
        if table == "pet_usage_state":
            connection.execute(
                """
                INSERT INTO pet_usage_state (
                    singleton, schema_version, revision, ledger_json,
                    ledger_sha256, chain_tag
                )
                SELECT 2, schema_version, revision, ledger_json,
                       ledger_sha256, chain_tag
                FROM pet_usage_state
                WHERE singleton = 1
                """
            )

    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)


def test_exact_schema_with_extra_singleton_row_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    service = _service(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            INSERT INTO pet_usage_state (
                singleton, schema_version, revision, ledger_json,
                ledger_sha256, chain_tag
            )
            SELECT 2, schema_version, revision, ledger_json,
                   ledger_sha256, chain_tag
            FROM pet_usage_state
            WHERE singleton = 1
            """
        )

    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)


@pytest.mark.parametrize(
    ("trigger_sql", "error"),
    [
        (
            """
            CREATE TEMP TRIGGER swallow_pet_commit
            BEFORE INSERT ON pet_usage_commits
            BEGIN
                SELECT RAISE(IGNORE);
            END
            """,
            "commit row unavailable",
        ),
        (
            """
            CREATE TEMP TRIGGER swallow_pet_state
            BEFORE UPDATE ON pet_usage_state
            BEGIN
                SELECT RAISE(IGNORE);
            END
            """,
            "concurrent revision",
        ),
    ],
)
def test_swallowed_sqlite_writes_require_exact_rowcount(
    tmp_path: Path,
    trigger_sql: str,
    error: str,
) -> None:
    database = _initialize(tmp_path)
    connection = server._connect_pet_usage_database(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        state, chain_tag = server._load_pet_usage_sqlite_state(
            connection,
            secret=SECRET,
            now=BASE_NOW,
        )
        connection.execute(trigger_sql)
        with pytest.raises(server.StateFileError, match=error):
            server._write_pet_usage_sqlite_revision(
                connection,
                secret=SECRET,
                state=state,
                previous_tag=chain_tag,
            )
        connection.rollback()
    finally:
        connection.close()

    with sqlite3.connect(database) as persisted:
        assert persisted.execute(
            "SELECT revision FROM pet_usage_state WHERE singleton=1"
        ).fetchone() == (0,)
        assert persisted.execute(
            "SELECT revision FROM pet_usage_commits"
        ).fetchall() == []


def test_post_commit_tampering_is_rejected_before_pet_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    _initialize(tmp_path)
    service = _service(tmp_path)
    real_connect = server._connect_pet_usage_database
    tampered = False

    class CommitTamperingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __getattr__(self, name: str):
            return getattr(self.connection, name)

        def commit(self) -> None:
            nonlocal tampered
            self.connection.commit()
            if not tampered:
                tampered = True
                self.connection.execute(
                    """
                    DELETE FROM pet_usage_commits
                    WHERE revision = (SELECT MAX(revision) FROM pet_usage_commits)
                    """
                )
                self.connection.commit()

    def tampering_connect(path: Path):
        return CommitTamperingConnection(real_connect(path))

    monkeypatch.setattr(server, "_connect_pet_usage_database", tampering_connect)
    code, body = _activate(service, seq=0, visible=True)
    assert code == 200
    assert body["ok"] is True
    assert "master_key" in body
    assert _pet(body)["status"] == "state_error"
    assert tampered is True
    assert server.activation_readiness(service)[0] == 503


def test_normal_wal_commit_is_revalidated_before_pet_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    database = _initialize(tmp_path)
    service = _service(tmp_path)

    code, opened = _activate(service, seq=0, visible=True)
    assert code == 200
    assert _pet(opened)["status"] == "active"
    assert _pet(opened)["usage_revision"] == 1
    now[0] += 15
    code, closed = _activate(service, seq=1, visible=False)
    assert code == 200
    assert _pet(closed)["remaining_seconds"] == 43_185
    assert _pet(closed)["usage_revision"] == 2
    assert server.activation_readiness(service)[0] == 200

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert connection.execute(
            "SELECT revision FROM pet_usage_state WHERE singleton=1"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT revision FROM pet_usage_commits ORDER BY revision"
        ).fetchall() == [(1,), (2,)]


def _checkpoint_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0


def _restore_database_file(database: Path, backup: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(f"{database.name}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    shutil.copy2(backup, database)


def test_database_only_backup_restore_is_rejected_by_external_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    database = _initialize(tmp_path)
    _checkpoint_database(database)
    backup = tmp_path / "pet-usage-revision-zero.sqlite3"
    shutil.copy2(database, backup)

    service = _service(tmp_path)
    code, opened = _activate(service, seq=0, visible=True)
    assert code == 200
    assert _pet(opened)["usage_revision"] == 1
    now[0] += 15
    code, closed = _activate(service, seq=1, visible=False)
    assert code == 200
    assert _pet(closed)["remaining_seconds"] == 43_185
    assert _pet(closed)["usage_revision"] == 2
    _checkpoint_database(database)

    _restore_database_file(database, backup)
    restarted = _service(tmp_path)
    assert server.activation_readiness(restarted)[0] == 503
    _assert_pet_state_error_preserves_calculator(restarted)


def test_head_only_backup_restore_cannot_hide_newer_database_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    head = server._pet_usage_head_path(database)
    backup = tmp_path / "pet-usage-revision-zero.head.jsonl"
    shutil.copy2(head, backup)

    service = _service(tmp_path)
    code, opened = _activate(service, seq=0, visible=True)
    assert code == 200
    assert _pet(opened)["usage_revision"] == 1
    shutil.copy2(backup, head)

    restarted = _service(tmp_path)
    assert server.activation_readiness(restarted)[0] == 503
    _assert_pet_state_error_preserves_calculator(restarted)


def test_intent_without_database_commit_is_aborted_on_next_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service = _service(tmp_path)
    real_append = server._append_pet_usage_head_event

    def crash_after_intent(path: Path, **kwargs):
        result = real_append(path, **kwargs)
        if kwargs["kind"] == "intent":
            raise OSError("simulated crash after durable intent")
        return result

    monkeypatch.setattr(server, "_append_pet_usage_head_event", crash_after_intent)
    code, failed = _activate(service, seq=0, visible=True)
    assert code == 200
    assert failed["ok"] is True
    assert "master_key" in failed
    assert _pet(failed)["status"] == "state_error"

    monkeypatch.setattr(server, "_append_pet_usage_head_event", real_append)
    assert server.activation_readiness(service)[0] == 200
    code, recovered = _activate(service, seq=0, visible=True)
    assert code == 200
    assert _pet(recovered)["status"] == "active"
    assert _pet(recovered)["usage_revision"] == 1


def test_database_commit_without_head_commit_is_finalized_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service = _service(tmp_path)
    real_append = server._append_pet_usage_head_event

    def crash_before_head_commit(path: Path, **kwargs):
        if kwargs["kind"] == "commit":
            raise OSError("simulated crash before durable head commit")
        return real_append(path, **kwargs)

    monkeypatch.setattr(server, "_append_pet_usage_head_event", crash_before_head_commit)
    code, failed = _activate(service, seq=0, visible=True)
    assert code == 200
    assert failed["ok"] is True
    assert "master_key" in failed
    assert _pet(failed)["status"] == "state_error"

    monkeypatch.setattr(server, "_append_pet_usage_head_event", real_append)
    assert server.activation_readiness(service)[0] == 200
    code, replayed = _activate(service, seq=0, visible=True)
    assert code == 200
    assert _pet(replayed)["status"] == "active"
    assert _pet(replayed)["remaining_seconds"] == 43_200
    assert _pet(replayed)["usage_revision"] == 1


class _CommitWindowConnection:
    """Run another service instance after SQLite COMMIT but before A relocks."""

    def __init__(self, connection: sqlite3.Connection, callback) -> None:
        self._connection = connection
        self._callback = callback
        self._fired = False

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def commit(self) -> None:
        self._connection.commit()
        if not self._fired:
            self._fired = True
            self._callback()


def _interleave_after_first_pet_commit(
    monkeypatch: pytest.MonkeyPatch,
    callback,
) -> None:
    real_connect = server._connect_pet_usage_database
    wrapped = False

    def connect(path: Path):
        nonlocal wrapped
        connection = real_connect(path)
        if not wrapped:
            wrapped = True
            return _CommitWindowConnection(connection, callback)
        return connection

    monkeypatch.setattr(server, "_connect_pet_usage_database", connect)


def test_cross_instance_readiness_finalizes_same_intent_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service_a = _service(tmp_path)
    service_b = _service(tmp_path)
    readiness = []
    _interleave_after_first_pet_commit(
        monkeypatch,
        lambda: readiness.append(server.activation_readiness(service_b)),
    )

    code, body = _activate(service_a, seq=0, visible=True)

    assert readiness == [(200, {"ok": True, "service": "activation"})]
    assert code == 200
    assert _pet(body)["status"] == "active"
    assert _pet(body)["usage_revision"] == 1
    assert server.activation_readiness(service_a) == (
        200,
        {"ok": True, "service": "activation"},
    )
    database = _initialize(tmp_path)
    head = server._load_pet_usage_head_state(
        server._pet_usage_head_path(database),
        secret=SECRET,
    )
    assert head["sequence"] == 2
    assert head["committed"][0] == 1
    assert head["pending"] is None


def test_cross_instance_next_revision_makes_stale_finalizer_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service_a = _service(tmp_path)
    service_b = _service(tmp_path)
    nested = []

    def advance_authority() -> None:
        assert server.activation_readiness(service_b) == (
            200,
            {"ok": True, "service": "activation"},
        )
        nested.append(_activate(service_b, seq=1, visible=False))

    _interleave_after_first_pet_commit(monkeypatch, advance_authority)
    code, stale = _activate(service_a, seq=0, visible=True)

    assert code == 200
    assert _pet(stale)["status"] == "state_error"
    assert nested[0][0] == 200
    assert _pet(nested[0][1])["usage_revision"] == 2
    assert server.activation_readiness(service_a) == (
        200,
        {"ok": True, "service": "activation"},
    )
    code, current = _activate(service_a, seq=1, visible=False)
    assert code == 200
    assert _pet(current)["usage_revision"] == 2
    assert _pet(current)["status"] == "paused"
    database = _initialize(tmp_path)
    head = server._load_pet_usage_head_state(
        server._pet_usage_head_path(database),
        secret=SECRET,
    )
    assert head["sequence"] == 4
    assert head["committed"][0] == 2
    assert head["pending"] is None


def test_same_process_database_and_head_restore_hits_revision_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    database = _initialize(tmp_path)
    head = server._pet_usage_head_path(database)
    _checkpoint_database(database)
    database_backup = tmp_path / "revision-zero.sqlite3"
    head_backup = tmp_path / "revision-zero.head.jsonl"
    shutil.copy2(database, database_backup)
    shutil.copy2(head, head_backup)

    service, now, _database = _consume_15_seconds(tmp_path, monkeypatch)
    _checkpoint_database(database)
    _restore_database_file(database, database_backup)
    shutil.copy2(head_backup, head)

    assert service._pet_usage_revision_floor == 2
    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)


def test_torn_head_tail_is_truncated_but_complete_bad_record_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    head = server._pet_usage_head_path(database)
    service = _service(tmp_path)

    with head.open("ab") as stream:
        stream.write(b'{"schema_version":1,"sequence":')
        stream.flush()
    assert server.activation_readiness(service)[0] == 200
    assert head.read_bytes().endswith(b"\n")

    with head.open("ab") as stream:
        stream.write(b'{"bad":"complete"}\n')
    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)


def test_busy_database_fails_readiness_and_pet_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    service = _service(tmp_path)
    blocker = sqlite3.connect(database, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        assert server.activation_readiness(service)[0] == 503
        _assert_pet_state_error_preserves_calculator(service)
    finally:
        blocker.rollback()
        blocker.close()
    assert server.activation_readiness(service)[0] == 200


def test_readonly_database_fails_readiness_and_pet_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    database = _initialize(tmp_path)
    service = _service(tmp_path)
    real_connect = server.sqlite3.connect

    def readonly_connect(*args, **kwargs):
        if isinstance(args[0], str) and args[0].startswith(
            database.resolve().as_uri()
        ):
            return real_connect(
                f"file:{database.as_posix()}?mode=ro",
                uri=True,
                timeout=0.0,
                isolation_level=None,
                check_same_thread=False,
            )
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(server.sqlite3, "connect", readonly_connect)
    assert server.activation_readiness(service)[0] == 503
    _assert_pet_state_error_preserves_calculator(service)


def test_failed_sqlite_commit_rolls_back_without_half_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    database = _initialize(tmp_path)
    service = _service(tmp_path)
    _activate(service, seq=0, visible=True)
    now[0] += 15
    real_write = server._write_pet_usage_sqlite_revision

    def partial_then_fail(connection, **kwargs):
        connection.execute(
            """
            INSERT INTO pet_usage_commits (
                revision, ledger_sha256, previous_tag, tag
            ) VALUES (999, ?, ?, ?)
            """,
            ("0" * 64, "1" * 64, "2" * 64),
        )
        raise sqlite3.OperationalError("simulated crash before state head update")

    monkeypatch.setattr(server, "_write_pet_usage_sqlite_revision", partial_then_fail)
    code, failed = _activate(service, seq=1, visible=False)
    assert code == 200
    assert _pet(failed)["status"] == "state_error"
    monkeypatch.setattr(server, "_write_pet_usage_sqlite_revision", real_write)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT revision FROM pet_usage_state WHERE singleton=1"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT revision FROM pet_usage_commits ORDER BY revision"
        ).fetchall() == [(1,)]

    restarted = _service(tmp_path)
    code, recovered = _activate(restarted, seq=1, visible=False)
    assert code == 200
    assert _pet(recovered)["remaining_seconds"] == 43_185
    assert _pet(recovered)["usage_revision"] == 2


def test_offline_admin_requires_explicit_empty_init_and_refuses_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, database, authority = _paths(tmp_path)
    key = tmp_path / "activation_key.txt"
    key.write_bytes(SECRET)
    common = [
        "pet_usage_db_admin.py",
        "--database",
        str(database),
        "--activation-key-file",
        str(key),
        "--legacy-ledger-file",
        str(app / "pet_usage.json"),
        "--legacy-anchor-file",
        str(app / "pet_usage.anchor.jsonl"),
    ]
    assert admin.main(common) == 2
    assert not database.exists()
    assert not server._pet_usage_head_path(database).exists()

    (app / "pet_usage.json").write_text("{}", encoding="utf-8")
    assert admin.main([*common, "--initialize-empty"]) == 1
    assert not database.exists()
    assert not server._pet_usage_head_path(database).exists()
    (app / "pet_usage.json").unlink()

    assert admin.main([*common, "--initialize-empty"]) == 0
    assert database.is_file()
    assert server._pet_usage_head_path(database).is_file()
    assert admin.main([*common, "--initialize-empty"]) == 1
    captured = capsys.readouterr()
    assert str(database) not in captured.out + captured.err
    assert SECRET.decode("utf-8") not in captured.out + captured.err
    assert authority.is_dir()
