from __future__ import annotations

import json
import hashlib
import hmac
import os
import sqlite3
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sample_collector_server as collector  # noqa: E402
import activation_core  # noqa: E402
import activation_server  # noqa: E402
import upload_lease  # noqa: E402
import my_records  # noqa: E402
from bidking_lab.live import sample_upload  # noqa: E402
from bidking_lab.secure import client_activate, enforce  # noqa: E402
from upload_identity_store import (  # noqa: E402
    UploadIdentityStateError,
    UploadIdentityStore,
    initialize_upload_identity_database,
)


SECRET = b"trusted-upload-identity-test-secret"
MASTER_KEY = "S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s="
DEVICE_BLOCK_KEY = b"trusted-upload-dedicated-device-block-key"
_SCHEMA2_SQL = """
CREATE TABLE upload_requests (
    lease_key TEXT NOT NULL,
    nonce_key TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'final')),
    claimed_until INTEGER NOT NULL,
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
_SCHEMA3_SQL = _SCHEMA2_SQL.replace(
    "    response_json TEXT,",
    "    claim_id TEXT NOT NULL,\n    response_json TEXT,",
)


def _service(
    tmp_path: Path,
    *,
    device_block_key: bytes | None = DEVICE_BLOCK_KEY,
) -> activation_server.ActivationService:
    pet_db = tmp_path / "pet.sqlite3"
    activation_server.initialize_pet_usage_database(
        pet_db,
        secret=SECRET,
        legacy_paths=(tmp_path / "pet.json", tmp_path / "pet.anchor.jsonl"),
    )
    return activation_server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY,
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
        paid_starts_path=tmp_path / "paid_starts.json",
        pet_usage_db_path=pet_db,
        pet_usage_path=tmp_path / "pet.json",
        device_blocks_path=tmp_path / "device_blocks.json",
        device_block_key=device_block_key,
        upload_security_epoch=7,
        upload_lease_seconds=300,
    )


def _request_mac(
    key: bytes,
    *,
    timestamp: str,
    nonce: str,
    lease: str,
    body: bytes,
) -> str:
    lease_digest = hashlib.sha256(lease.encode("ascii")).hexdigest()
    message = (
        b"bidking/upload-request/v1\x00"
        + timestamp.encode("ascii")
        + b"\x00"
        + nonce.encode("ascii")
        + b"\x00"
        + lease_digest.encode("ascii")
        + b"\x00"
        + body
    )
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _create_upload_identity_database(
    path: Path,
    *,
    schema_sql: str,
    version: int,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            f"PRAGMA application_id={collector.upload_identity_store.APPLICATION_ID}"
        )
        connection.execute(f"PRAGMA user_version={version}")
        connection.executescript(schema_sql)
        connection.commit()
    finally:
        connection.close()


def _run_upload_identity_admin(path: Path, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "upload_identity_db_admin.py"),
            "--db",
            str(path),
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _reserve_request_claim(
    store: UploadIdentityStore,
    *,
    lease_id: str,
    nonce: str,
    body_sha256: str,
    expires_at: int = 2_000_000_300,
    now: int = 2_000_000_000,
) -> str:
    reservation = store.reserve_request(
        lease_id=lease_id,
        nonce=nonce,
        body_sha256=body_sha256,
        expires_at=expires_at,
        now=now,
    )
    assert reservation.state in {"reserved", "resumed"}
    assert type(reservation.claim_id) is str
    return reservation.claim_id


def _reserve_claim(
    store: UploadIdentityStore,
    identity: collector.UploadIdentity,
    body_sha256: str,
    *,
    now: int = 2_000_000_000,
) -> str:
    return _reserve_request_claim(
        store,
        lease_id=identity.lease_id,
        nonce=identity.request_nonce,
        body_sha256=body_sha256,
        expires_at=identity.lease_exp,
        now=now,
    )


def _accept_signed(
    store: UploadIdentityStore,
    rec: dict,
    identity: collector.UploadIdentity,
    body_sha256: str,
    *,
    request_recovery: bool = False,
    now: int = 2_000_000_000,
) -> str:
    claim_id = _reserve_claim(store, identity, body_sha256, now=now)
    return collector._accept_one(
        rec,
        upload_identity=identity,
        body_sha256=body_sha256,
        identity_store=store,
        request_recovery=request_recovery,
        request_claim_id=claim_id,
    )


def test_client_uid_hash_is_not_a_collector_authority() -> None:
    """A distributed client must not choose the identity persisted by collector."""
    assert "uid_hash" not in collector._ALLOWED


def test_required_mode_rejects_a_request_without_upload_lease() -> None:
    decision = collector._authorize_upload_identity(
        {},
        b"{}",
        mode="required",
        service=None,
        now=2_000_000_000,
    )

    assert decision.http_code == 401
    assert decision.reason == "identity_required"
    assert decision.identity is None


def test_compat_missing_is_legacy_but_invalid_supplied_never_downgrades() -> None:
    legacy = collector._authorize_upload_identity(
        {}, b"{}", mode="compat", service=None, now=2_000_000_000
    )
    invalid = collector._authorize_upload_identity(
        {"X-Upload-Lease": "broken", "X-Upload-Nonce": "a" * 32},
        b"{}",
        mode="compat",
        service=None,
        now=2_000_000_000,
    )
    assert legacy.identity == collector.UploadIdentity("legacy_unverified")
    assert (invalid.http_code, invalid.reason, invalid.identity) == (
        503,
        "identity_state_error",
        None,
    )


def test_disabled_rollback_ignores_new_client_identity_headers() -> None:
    decision = collector._authorize_upload_identity(
        {
            "X-Upload-Lease": "still-present-after-rollback",
            "X-Upload-Nonce": "a" * 32,
        },
        b"{}",
        mode="disabled",
        service=None,
        now=2_000_000_000,
    )

    assert decision == collector.UploadIdentityDecision(
        200,
        "legacy_unverified",
        collector.UploadIdentity("legacy_unverified"),
    )


def test_schema2_init_and_status_fail_closed_without_modifying_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schema2.sqlite3"
    _create_upload_identity_database(path, schema_sql=_SCHEMA2_SQL, version=2)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO upload_requests VALUES (?, ?, ?, ?, 'pending', ?, NULL)",
            ("a" * 64, "b" * 64, "c" * 64, 2_000_000_300, 2_000_000_015),
        )
        connection.commit()
    finally:
        connection.close()
    before = path.read_bytes()

    initialized = _run_upload_identity_admin(path, "init")
    after_init = path.read_bytes()
    status = _run_upload_identity_admin(path, "status")

    assert initialized.returncode == 3
    assert json.loads(initialized.stdout) == {"ok": False, "state": "state_error"}
    assert status.returncode == 3
    assert json.loads(status.stdout) == {"ok": False, "state": "state_error"}
    assert before == after_init == path.read_bytes()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert "claim_id" not in {
            row[1] for row in connection.execute("PRAGMA table_info(upload_requests)")
        }
        assert connection.execute("SELECT COUNT(*) FROM upload_requests").fetchone() == (
            1,
        )
    finally:
        connection.close()
    with pytest.raises(UploadIdentityStateError, match="schema mismatch"):
        UploadIdentityStore(path)


def test_schema3_fresh_init_and_existing_init_are_byte_stable(tmp_path: Path) -> None:
    path = tmp_path / "schema3.sqlite3"
    initialize_upload_identity_database(path)
    store = UploadIdentityStore(path)
    before = path.read_bytes()

    initialized = _run_upload_identity_admin(path, "init")
    after_init = path.read_bytes()
    status = _run_upload_identity_admin(path, "status")

    assert initialized.returncode == 0
    assert json.loads(initialized.stdout) == {"ok": True, "state": "initialized"}
    assert status.returncode == 0
    assert json.loads(status.stdout) == {"ok": True, "state": "ready"}
    assert before == after_init == path.read_bytes()
    connection = sqlite3.connect(path)
    try:
        columns = connection.execute("PRAGMA table_info(upload_requests)").fetchall()
    finally:
        connection.close()
    claim = next(row for row in columns if row[1] == "claim_id")
    assert claim[2:4] == ("TEXT", 1)
    assert [(row[1], row[5]) for row in columns if row[5]] == [
        ("lease_key", 1),
        ("nonce_key", 2),
    ]
    assert store.reserve_request(
        lease_id="a" * 32,
        nonce="b" * 32,
        body_sha256="c" * 64,
        expires_at=2_000_000_300,
        now=2_000_000_000,
    ).state == "reserved"


def test_fresh_init_never_writes_a_replacement_at_the_final_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "upload_identity.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    connection = sqlite3.connect(replacement)
    try:
        connection.execute("CREATE TABLE owner_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO owner_marker VALUES ('replacement-owner')")
        connection.commit()
    finally:
        connection.close()
    replacement_bytes = replacement.read_bytes()
    real_connect = sqlite3.connect
    replaced = False

    def replace_final_after_exclusive_temp(database, *args, **kwargs):  # noqa: ANN001
        nonlocal replaced
        database_path = Path(database) if type(database) is str else None
        if (
            not replaced
            and database_path is not None
            and not kwargs.get("uri", False)
            and database_path.parent == target.parent
            and (
                database_path == target
                or database_path.name.startswith(f".{target.name}.init-")
            )
        ):
            target.write_bytes(replacement_bytes)
            replaced = True
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(
        collector.upload_identity_store.sqlite3,
        "connect",
        replace_final_after_exclusive_temp,
    )

    with pytest.raises(UploadIdentityStateError):
        initialize_upload_identity_database(target)

    assert replaced is True
    assert target.read_bytes() == replacement_bytes


def test_concurrent_fresh_initializers_publish_one_exact_schema3(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "upload_identity.sqlite3"
    real_link = os.link
    link_barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def synchronized_link(source, destination, **kwargs):  # noqa: ANN001
        link_barrier.wait(timeout=5)
        return real_link(source, destination, **kwargs)

    def initialize() -> None:
        try:
            initialize_upload_identity_database(target)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    monkeypatch.setattr(collector.upload_identity_store.os, "link", synchronized_link)
    workers = [threading.Thread(target=initialize) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    assert not list(tmp_path.glob(f".{target.name}.init-*"))
    assert not Path(f"{target}-wal").exists()
    assert not Path(f"{target}-shm").exists()
    UploadIdentityStore(target)


def test_post_link_validation_rechecks_final_file_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "upload_identity.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    initialize_upload_identity_database(replacement)
    connection = sqlite3.connect(replacement)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            """
            INSERT INTO upload_requests (
                lease_key, nonce_key, body_sha256, expires_at, state,
                claimed_until, claim_id, response_json
            ) VALUES (?, ?, ?, ?, 'final', 0, ?, ?)
            """,
            ("a" * 32, "b" * 32, "c" * 64, 2_000_000_300, "d" * 32, "{}"),
        )
        connection.commit()
    finally:
        connection.close()
    replacement_bytes = replacement.read_bytes()
    real_validate = collector.upload_identity_store._validate_database_path_read_only
    replaced = False

    def replace_final_before_validation(path: Path) -> None:
        nonlocal replaced
        if Path(path) == target and not replaced:
            target.unlink()
            os.link(replacement, target, follow_symlinks=False)
            replaced = True
        real_validate(path)

    monkeypatch.setattr(
        collector.upload_identity_store,
        "_validate_database_path_read_only",
        replace_final_before_validation,
    )

    with pytest.raises(UploadIdentityStateError):
        initialize_upload_identity_database(target)

    assert replaced is True
    assert target.read_bytes() == replacement_bytes
    connection = sqlite3.connect(
        f"{target.resolve().as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        assert connection.execute("SELECT COUNT(*) FROM upload_requests").fetchone() == (
            1,
        )
    finally:
        connection.close()


def test_post_link_validation_rechecks_final_sidecars(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "upload_identity.sqlite3"
    wal = Path(f"{target}-wal")
    shm = Path(f"{target}-shm")
    wal_marker = b"late-final-wal"
    shm_marker = b"late-final-shm"
    real_validate = collector.upload_identity_store._validate_database_path_read_only
    published_bytes: bytes | None = None

    def add_sidecars_before_validation(path: Path) -> None:
        nonlocal published_bytes
        if Path(path) == target and published_bytes is None:
            published_bytes = target.read_bytes()
            wal.write_bytes(wal_marker)
            shm.write_bytes(shm_marker)
        real_validate(path)

    monkeypatch.setattr(
        collector.upload_identity_store,
        "_validate_database_path_read_only",
        add_sidecars_before_validation,
    )

    with pytest.raises(UploadIdentityStateError):
        initialize_upload_identity_database(target)

    assert published_bytes is not None
    assert target.read_bytes() == published_bytes
    assert wal.read_bytes() == wal_marker
    assert shm.read_bytes() == shm_marker


def test_malformed_publish_race_preserves_main_and_sidecars(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "upload_identity.sqlite3"
    main_marker = b"replacement-malformed-main"
    wal_marker = b"replacement-wal-marker"
    shm_marker = b"replacement-shm-marker"
    real_link = os.link
    replaced = False

    def replace_before_link(source, destination, **kwargs):  # noqa: ANN001
        nonlocal replaced
        if not replaced:
            Path(destination).write_bytes(main_marker)
            Path(f"{destination}-wal").write_bytes(wal_marker)
            Path(f"{destination}-shm").write_bytes(shm_marker)
            replaced = True
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(collector.upload_identity_store.os, "link", replace_before_link)

    with pytest.raises(UploadIdentityStateError):
        initialize_upload_identity_database(target)

    assert replaced is True
    assert target.read_bytes() == main_marker
    assert Path(f"{target}-wal").read_bytes() == wal_marker
    assert Path(f"{target}-shm").read_bytes() == shm_marker
    assert not list(tmp_path.glob(f".{target.name}.init-*"))


def test_failed_no_clobber_publish_cleans_private_temp_and_sidecars(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "upload_identity.sqlite3"

    def fail_link(*_args, **_kwargs):
        raise OSError("link unavailable")

    monkeypatch.setattr(collector.upload_identity_store.os, "link", fail_link)

    with pytest.raises(UploadIdentityStateError):
        initialize_upload_identity_database(target)

    assert not target.exists()
    assert not list(tmp_path.glob(f".{target.name}.init-*"))
    assert not list(tmp_path.glob(f".{target.name}.init-*-wal"))
    assert not list(tmp_path.glob(f".{target.name}.init-*-shm"))


@pytest.mark.parametrize(
    "request_schema",
    [
        _SCHEMA2_SQL,
        _SCHEMA3_SQL.replace("claim_id TEXT NOT NULL", "claim_id TEXT"),
        _SCHEMA3_SQL.replace(
            "PRIMARY KEY (lease_key, nonce_key)",
            "PRIMARY KEY (nonce_key, lease_key)",
        ),
        _SCHEMA3_SQL.replace(
            "state TEXT NOT NULL CHECK (state IN ('pending', 'final'))",
            "state TEXT NOT NULL",
        ),
        _SCHEMA3_SQL.replace(
            "claim_id TEXT NOT NULL,",
            "claim_id TEXT NOT NULL,\n    unexpected_private_field TEXT,",
        ),
    ],
    ids=[
        "version-forged-missing-claim",
        "nullable-claim",
        "wrong-primary-key-order",
        "missing-state-check",
        "extra-column",
    ],
)
def test_schema3_structure_mismatch_is_rejected_without_mutation(
    tmp_path: Path,
    request_schema: str,
) -> None:
    path = tmp_path / "forged-schema3.sqlite3"
    _create_upload_identity_database(path, schema_sql=request_schema, version=3)
    before = path.read_bytes()

    initialized = _run_upload_identity_admin(path, "init")
    status = _run_upload_identity_admin(path, "status")

    assert initialized.returncode == 3
    assert status.returncode == 3
    assert before == path.read_bytes()
    with pytest.raises(UploadIdentityStateError, match="schema mismatch"):
        UploadIdentityStore(path)


def test_reused_nonce_with_changed_body_is_rejected(tmp_path: Path) -> None:
    store = UploadIdentityStore(tmp_path / "upload_identity.sqlite3", create=True)
    first = store.reserve_request(
        lease_id="a" * 32,
        nonce="b" * 32,
        body_sha256="c" * 64,
        expires_at=2_000_000_300,
        now=2_000_000_000,
    )
    changed = store.reserve_request(
        lease_id="a" * 32,
        nonce="b" * 32,
        body_sha256="d" * 64,
        expires_at=2_000_000_300,
        now=2_000_000_001,
    )

    assert first.state == "reserved"
    assert changed.state == "replay_rejected"


def test_two_store_instances_reserve_one_request_only_once(tmp_path: Path) -> None:
    path = tmp_path / "upload_identity.sqlite3"
    stores = [UploadIdentityStore(path, create=True), UploadIdentityStore(path)]
    barrier = threading.Barrier(3)
    results: list[str] = []

    def reserve(store: UploadIdentityStore) -> None:
        barrier.wait()
        results.append(
            store.reserve_request(
                lease_id="a" * 32,
                nonce="b" * 32,
                body_sha256="c" * 64,
                expires_at=2_000_000_300,
                now=2_000_000_000,
            ).state
        )

    workers = [threading.Thread(target=reserve, args=(store,)) for store in stores]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=5)

    assert not any(worker.is_alive() for worker in workers)
    assert sorted(results) == ["in_flight", "reserved"]


def test_resumed_request_fences_the_still_live_previous_owner(tmp_path: Path) -> None:
    path = tmp_path / "upload_identity.sqlite3"
    store_a = UploadIdentityStore(path, create=True)
    store_b = UploadIdentityStore(path)
    request = {
        "lease_id": "a" * 32,
        "nonce": "b" * 32,
        "body_sha256": "c" * 64,
    }
    reserved = store_a.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_000,
    )
    resumed = store_b.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_015,
    )
    assert (reserved.state, resumed.state) == ("reserved", "resumed")
    assert reserved.claim_id not in repr(reserved)
    assert resumed.claim_id not in repr(resumed)

    store_b.finalize_request(
        **request,
        response={"accepted": 0, "duplicate": 1, "quarantined": 0, "rejected": 0},
        claim_id=resumed.claim_id,
    )
    with pytest.raises(UploadIdentityStateError, match="request claim lost"):
        store_a.finalize_request(
            **request,
            response={
                "accepted": 1,
                "duplicate": 0,
                "quarantined": 0,
                "rejected": 0,
            },
            claim_id=reserved.claim_id,
        )
    replay = store_a.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_016,
    )
    assert replay.response == {
        "accepted": 0,
        "duplicate": 1,
        "quarantined": 0,
        "rejected": 0,
    }


def test_resumed_claim_fences_old_identity_append_and_finalize(tmp_path: Path) -> None:
    path = tmp_path / "upload_identity.sqlite3"
    store_a = UploadIdentityStore(path, create=True)
    store_b = UploadIdentityStore(path)
    request = {
        "lease_id": "a" * 32,
        "nonce": "b" * 32,
        "body_sha256": "c" * 64,
    }
    identity = {
        "session_hash": "fenced-session",
        "subject_kind": "paid",
        "uid_hash": "d" * 16,
        "subject_id": "e" * 64,
        "device_alias": "f" * 64,
        "security_epoch": 7,
        "received_ts": 2_000_000_000.0,
        "intended_disposition": "accepted",
        **request,
    }
    claim_a = store_a.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_000,
    ).claim_id
    assert type(claim_a) is str
    store_a.prepare_identity(**identity, claim_id=claim_a)

    claim_b = store_b.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_015,
    ).claim_id
    assert type(claim_b) is str and claim_b != claim_a
    with pytest.raises(UploadIdentityStateError, match="request claim lost"):
        store_a.prepare_identity(**identity, claim_id=claim_a)
    assert store_b.prepare_identity(**identity, claim_id=claim_b).state == "resumed"
    store_b.finalize_identity(
        session_hash=identity["session_hash"],
        subject_id=identity["subject_id"],
        **request,
        disposition="duplicate",
        claim_id=claim_b,
    )
    response_b = {"accepted": 0, "duplicate": 1, "quarantined": 0, "rejected": 0}
    store_b.finalize_request(**request, response=response_b, claim_id=claim_b)

    append_canary: list[str] = []
    with pytest.raises(UploadIdentityStateError, match="request claim lost"):
        store_a.finalize_identity(
            session_hash=identity["session_hash"],
            subject_id=identity["subject_id"],
            **request,
            disposition="accepted",
            claim_id=claim_a,
            append_action=lambda: append_canary.append("old-owner-wrote"),
        )
    assert append_canary == []
    assert store_a.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_016,
    ).response == response_b


def test_crashed_owner_can_be_resumed_once_with_a_new_claim(tmp_path: Path) -> None:
    path = tmp_path / "upload_identity.sqlite3"
    store_a = UploadIdentityStore(path, create=True)
    store_b = UploadIdentityStore(path)
    request = {
        "lease_id": "a" * 32,
        "nonce": "b" * 32,
        "body_sha256": "c" * 64,
    }
    identity = {
        "session_hash": "crash-recovery-session",
        "subject_kind": "trial",
        "uid_hash": None,
        "subject_id": "d" * 64,
        "device_alias": "e" * 64,
        "security_epoch": 7,
        "received_ts": 2_000_000_000.0,
        "intended_disposition": "accepted",
        **request,
    }
    claim_a = store_a.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_000,
    ).claim_id
    assert type(claim_a) is str
    store_a.prepare_identity(**identity, claim_id=claim_a)

    claim_b = store_b.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_015,
    ).claim_id
    assert type(claim_b) is str and claim_b != claim_a
    assert store_b.prepare_identity(**identity, claim_id=claim_b).state == "resumed"
    public_rows: list[str] = []
    store_b.finalize_identity(
        session_hash=identity["session_hash"],
        subject_id=identity["subject_id"],
        **request,
        disposition="accepted",
        claim_id=claim_b,
        append_action=lambda: public_rows.append("one-row"),
    )
    response = {"accepted": 1, "duplicate": 0, "quarantined": 0, "rejected": 0}
    store_b.finalize_request(**request, response=response, claim_id=claim_b)

    assert public_rows == ["one-row"]
    replay = store_a.reserve_request(
        **request,
        expires_at=2_000_000_300,
        now=2_000_000_016,
    )
    assert replay.state == "replayed"
    assert replay.response == response


def test_claim_resume_fails_closed_if_rng_does_not_rotate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        collector.upload_identity_store.secrets,
        "token_hex",
        lambda _size: "a" * 32,
    )
    store = UploadIdentityStore(tmp_path / "upload_identity.sqlite3", create=True)
    request = {
        "lease_id": "b" * 32,
        "nonce": "c" * 32,
        "body_sha256": "d" * 64,
        "expires_at": 2_000_000_300,
    }
    assert store.reserve_request(**request, now=2_000_000_000).claim_id == "a" * 32
    with pytest.raises(UploadIdentityStateError, match="claim did not rotate"):
        store.reserve_request(**request, now=2_000_000_015)


def test_upload_authority_rejects_protocol_subclasses_without_magic(tmp_path: Path) -> None:
    calls: list[str] = []

    class BombStr(str):
        def __eq__(self, _other):
            calls.append("eq")
            raise RuntimeError("must not run")

        def __ne__(self, _other):
            calls.append("ne")
            raise RuntimeError("must not run")

    payload = {
        "schema_version": 1,
        "kind": "sample_upload",
        "subject_kind": "paid",
        "subject_id": "a" * 64,
        "uid_hash": "b" * 16,
        "device_alias": "c" * 64,
        "security_epoch": 7,
        "lease_id": "d" * 32,
        "iat": 2_000_000_000,
        "exp": 2_000_000_300,
        "entitlement_exp": None,
    }
    payload["subject_id"] = BombStr("a" * 64)

    with pytest.raises(upload_lease.UploadLeaseError):
        upload_lease.validate_payload(payload)
    store = UploadIdentityStore(tmp_path / "identity.sqlite3", create=True)
    with pytest.raises(UploadIdentityStateError):
        store.reserve_request(
            lease_id=BombStr("a" * 32),
            nonce="b" * 32,
            body_sha256="c" * 64,
            expires_at=2_000_000_300,
            now=2_000_000_000,
        )
    assert calls == []


def test_legacy_identity_never_persists_client_claim(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    rec = {
        "map_id": 2401,
        "final_quality_counts": "q1=1",
        "session_hash": "legacy-upload-session",
        "known_value_sum": 99_999,
        "uid_hash": "0123456789abcdef",
    }

    assert collector._accept_one(rec) == "accepted"
    stored = json.loads(out.read_text(encoding="utf-8"))
    assert stored["identity_trust"] == "legacy_unverified"
    assert "uid_hash" not in stored


def test_paid_upload_lease_rechecks_revocation(tmp_path: Path, monkeypatch) -> None:
    now = 2_000_000_000
    monkeypatch.setattr(activation_server.time, "time", lambda: now)
    service = _service(tmp_path)
    token = activation_core.mint_token("paid-user", SECRET, iat=now - 10)
    code, response = service.handle_activate(
        {
            "token": token,
            "device": "device-a",
            "client_security_epoch": 1,
            "upload_lease_schema": 1,
        }
    )

    assert code == 200
    payload = upload_lease.verify(response["upload_lease"], SECRET, now=now)
    assert payload["subject_kind"] == "paid"
    assert payload["uid_hash"] == upload_lease.paid_uid_hash("paid-user")
    assert payload["security_epoch"] == 7
    assert service.authorize_upload_lease(response["upload_lease"], now=now)[0] is True

    (tmp_path / "revoked.txt").write_text("paid-user\n", encoding="utf-8")
    allowed, reason, identity = service.authorize_upload_lease(
        response["upload_lease"],
        now=now + 1,
    )
    assert (allowed, reason, identity) == (False, "identity_revoked", None)


def test_expired_signed_upload_lease_has_a_distinct_fixed_reason(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = 2_000_000_000
    monkeypatch.setattr(activation_server.time, "time", lambda: now)
    service = _service(tmp_path)
    token = activation_core.mint_token("expiring-user", SECRET, iat=now - 10)
    code, response = service.handle_activate(
        {
            "token": token,
            "device": "device-a",
            "client_security_epoch": 1,
            "upload_lease_schema": 1,
        }
    )
    assert code == 200
    assert service.authorize_upload_lease(
        response["upload_lease"],
        now=now + 301,
    ) == (False, "identity_expired", None)


def test_upload_lease_rechecks_security_epoch_and_device_block(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = 2_000_000_000
    block_key = b"upload-device-block-key-v1-32bytes"
    monkeypatch.setattr(activation_server.time, "time", lambda: now)
    service = _service(tmp_path, device_block_key=block_key)
    token = activation_core.mint_token("device-block-user", SECRET, iat=now - 10)
    code, response = service.handle_activate(
        {
            "token": token,
            "device": "device-to-block",
            "client_security_epoch": 1,
            "upload_lease_schema": 1,
        }
    )
    assert code == 200

    service.upload_security_epoch = 8
    assert service.authorize_upload_lease(
        response["upload_lease"],
        now=now + 1,
    ) == (False, "identity_epoch_mismatch", None)
    service.upload_security_epoch = 7
    activation_server._atomic_write_json(
        tmp_path / "device_blocks.json",
        {
            "schema_version": 1,
            "blocked": {
                activation_server.device_block_id(block_key, "device-to-block"): {
                    "blocked_at": now,
                    "reason": "confirmed_abuse",
                    "evidence_ref": "INC-ABCDEF123456",
                    "actor": "author",
                }
            },
        },
    )
    assert service.authorize_upload_lease(
        response["upload_lease"],
        now=now + 1,
    ) == (False, "identity_device_revoked", None)


def test_upload_lease_uses_the_device_block_authority_identifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = 2_000_000_000
    monkeypatch.setattr(activation_server.time, "time", lambda: now)
    service = _service(tmp_path)
    token = activation_core.mint_token(
        "device-authority-user",
        SECRET,
        iat=now - 10,
    )
    code, response = service.handle_activate(
        {
            "token": token,
            "device": "device-authority-A",
            "client_security_epoch": 1,
            "upload_lease_schema": 1,
        },
    )

    assert code == 200
    payload = upload_lease.verify(response["upload_lease"], SECRET, now=now)
    assert payload["device_alias"] == activation_server.device_block_id(
        DEVICE_BLOCK_KEY,
        "device-authority-A",
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("identity_required", "identity_required"),
        ("lease_expired", "lease_expired"),
        ("uid_revoked", "uid_revoked"),
        ("device_revoked", "device_revoked"),
    ],
)
def test_client_upload_status_uses_only_fixed_server_failure_classes(
    header: str,
    expected: str,
) -> None:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from bidking_lab.live import sample_upload

    headers = Message()
    headers["X-BidKing-Upload-Status"] = header
    error = urllib.error.HTTPError("https://redacted.invalid", 401, "x", headers, None)
    assert sample_upload._upload_failure_class(error) == expected


def test_client_upload_status_does_not_consume_exception_class_protocol() -> None:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from bidking_lab.live import sample_upload

    calls: list[str] = []

    class SpoofedError(Exception):
        def __getattribute__(self, name):
            if name == "__class__":
                calls.append(name)
                raise RuntimeError("must not run")
            return super().__getattribute__(name)

    assert sample_upload._upload_failure_class(SpoofedError()) == "unknown"
    assert calls == []


def test_trusted_identity_stays_out_of_public_jsonl(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "samples.jsonl"
    store = UploadIdentityStore(tmp_path / "identity.sqlite3", create=True)
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_quarantine_reason", lambda *_args, **_kwargs: None)
    uid_hash = upload_lease.paid_uid_hash("paid-user")
    identity = collector.UploadIdentity(
        trust="server_signed",
        subject_kind="paid",
        subject_id="a" * 64,
        uid_hash=uid_hash,
        device_alias="b" * 64,
        security_epoch=7,
        lease_id="c" * 32,
        lease_exp=2_000_000_300,
        request_nonce="d" * 32,
    )
    rec = {
        "map_id": 2401,
        "final_quality_counts": "q1=1",
        "session_hash": "trusted-session",
    }

    assert _accept_signed(store, rec, identity, "e" * 64) == "accepted"
    stored = json.loads(out.read_text(encoding="utf-8"))
    assert stored["identity_trust"] == "server_signed"
    assert "uid_hash" not in stored
    assert store.sessions_for_uid_hash(uid_hash) == frozenset({"trusted-session"})


def test_trusted_duplicate_and_identity_conflict_keep_private_dispositions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    out = tmp_path / "samples.jsonl"
    store = UploadIdentityStore(tmp_path / "identity.sqlite3", create=True)
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_quarantine_reason", lambda *_args, **_kwargs: None)
    uid_hash = upload_lease.paid_uid_hash("paid-user")

    def identity(*, subject: str, nonce: str) -> collector.UploadIdentity:
        return collector.UploadIdentity(
            trust="server_signed",
            subject_kind="paid",
            subject_id=subject,
            uid_hash=uid_hash,
            device_alias="b" * 64,
            security_epoch=7,
            lease_id="c" * 32,
            lease_exp=2_000_000_300,
            request_nonce=nonce,
        )

    rec = {
        "map_id": 2401,
        "final_quality_counts": "q1=1",
        "session_hash": "duplicate-session",
    }
    assert _accept_signed(
        store,
        rec,
        identity(subject="a" * 64, nonce="d" * 32),
        "e" * 64,
    ) == "accepted"
    assert _accept_signed(
        store,
        rec,
        identity(subject="a" * 64, nonce="f" * 32),
        "e" * 64,
    ) == "duplicate"
    assert _accept_signed(
        store,
        rec,
        identity(subject="1" * 64, nonce="2" * 32),
        "3" * 64,
    ) == "quarantined"

    assert [
        row["disposition"] for row in store.redacted_session("duplicate-session")
    ] == ["accepted", "duplicate", "quarantined"]
    assert store.sessions_for_uid_hash(uid_hash) == frozenset({"duplicate-session"})


def test_pending_identity_recovers_after_public_append_without_duplication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    out = tmp_path / "samples.jsonl"
    store = UploadIdentityStore(tmp_path / "identity.sqlite3", create=True)
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", {"crash-session"})
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_quarantine_reason", lambda *_args, **_kwargs: None)
    identity = collector.UploadIdentity(
        trust="server_signed",
        subject_kind="paid",
        subject_id="a" * 64,
        uid_hash="b" * 16,
        device_alias="c" * 64,
        security_epoch=7,
        lease_id="d" * 32,
        lease_exp=2_000_000_300,
        request_nonce="e" * 32,
    )
    first_claim = _reserve_claim(store, identity, "f" * 64)
    store.prepare_identity(
        session_hash="crash-session",
        subject_kind="paid",
        uid_hash="b" * 16,
        subject_id="a" * 64,
        device_alias="c" * 64,
        security_epoch=7,
        received_ts=2_000_000_000.0,
        intended_disposition="accepted",
        body_sha256="f" * 64,
        lease_id="d" * 32,
        nonce="e" * 32,
        claim_id=first_claim,
    )
    recovered_claim = _reserve_claim(
        store,
        identity,
        "f" * 64,
        now=2_000_000_015,
    )

    assert collector._accept_one(
        {
            "map_id": 2401,
            "final_quality_counts": "q1=1",
            "session_hash": "crash-session",
        },
        upload_identity=identity,
        body_sha256="f" * 64,
        identity_store=store,
        request_recovery=True,
        request_claim_id=recovered_claim,
    ) == "duplicate"
    assert store.redacted_session("crash-session")[0]["disposition"] == "accepted"
    assert not out.exists()


def test_request_recovery_reconstructs_original_counts_after_identity_finalize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    out = tmp_path / "samples.jsonl"
    store = UploadIdentityStore(tmp_path / "identity.sqlite3", create=True)
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", {"finalized-before-response"})
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_quarantine_reason", lambda *_args, **_kwargs: None)
    identity = collector.UploadIdentity(
        trust="server_signed",
        subject_kind="paid",
        subject_id="a" * 64,
        uid_hash="b" * 16,
        device_alias="c" * 64,
        security_epoch=7,
        lease_id="d" * 32,
        lease_exp=2_000_000_300,
        request_nonce="e" * 32,
    )
    first = store.reserve_request(
        lease_id="d" * 32,
        nonce="e" * 32,
        body_sha256="f" * 64,
        expires_at=2_000_000_300,
        now=2_000_000_000,
    )
    assert first.state == "reserved"
    assert type(first.claim_id) is str
    store.prepare_identity(
        session_hash="finalized-before-response",
        subject_kind="paid",
        uid_hash="b" * 16,
        subject_id="a" * 64,
        device_alias="c" * 64,
        security_epoch=7,
        received_ts=2_000_000_000.0,
        intended_disposition="accepted",
        body_sha256="f" * 64,
        lease_id="d" * 32,
        nonce="e" * 32,
        claim_id=first.claim_id,
    )
    store.finalize_identity(
        session_hash="finalized-before-response",
        subject_id="a" * 64,
        body_sha256="f" * 64,
        lease_id="d" * 32,
        nonce="e" * 32,
        disposition="accepted",
        claim_id=first.claim_id,
    )
    resumed = store.reserve_request(
        lease_id="d" * 32,
        nonce="e" * 32,
        body_sha256="f" * 64,
        expires_at=2_000_000_300,
        now=2_000_000_016,
    )
    assert resumed.state == "resumed"
    assert type(resumed.claim_id) is str

    result = collector._accept_one(
        {
            "map_id": 2401,
            "final_quality_counts": "q1=1",
            "session_hash": "finalized-before-response",
        },
        upload_identity=identity,
        body_sha256="f" * 64,
        identity_store=store,
        request_recovery=True,
        request_claim_id=resumed.claim_id,
    )
    assert result == "accepted"
    assert not out.exists()


def test_private_records_join_only_through_trusted_server_index(tmp_path: Path) -> None:
    store = UploadIdentityStore(tmp_path / "identity.sqlite3", create=True)
    uid_hash = upload_lease.paid_uid_hash("private-paid")
    subject_id = "a" * 64
    claim_id = _reserve_request_claim(
        store,
        lease_id="d" * 32,
        nonce="e" * 32,
        body_sha256="c" * 64,
    )
    store.prepare_identity(
        session_hash="private-owned",
        subject_kind="paid",
        uid_hash=uid_hash,
        subject_id=subject_id,
        device_alias="b" * 64,
        security_epoch=7,
        received_ts=2_000_000_000.0,
        intended_disposition="accepted",
        body_sha256="c" * 64,
        lease_id="d" * 32,
        nonce="e" * 32,
        claim_id=claim_id,
    )
    store.finalize_identity(
        session_hash="private-owned",
        subject_id=subject_id,
        body_sha256="c" * 64,
        lease_id="d" * 32,
        nonce="e" * 32,
        disposition="accepted",
        claim_id=claim_id,
    )
    samples = tmp_path / "samples.jsonl"
    samples.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"session_hash": "private-owned", "known_value_sum": 10},
                {"session_hash": "other-session", "known_value_sum": 999},
                {
                    "session_hash": "forged-legacy",
                    "uid_hash": uid_hash,
                    "known_value_sum": 888,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    records = my_records.load_user_samples(
        samples,
        uid_hash,
        identity_store=store,
    )
    assert [record["known_value_sum"] for record in records] == [10]


def test_upload_identity_admin_inspection_is_redacted(tmp_path: Path) -> None:
    store = UploadIdentityStore(tmp_path / "identity.sqlite3", create=True)
    subject_id = "a" * 64
    device_alias = "b" * 64
    uid_hash = "c" * 16
    claim_id = _reserve_request_claim(
        store,
        lease_id="e" * 32,
        nonce="f" * 32,
        body_sha256="d" * 64,
    )
    store.prepare_identity(
        session_hash="admin-session",
        subject_kind="paid",
        uid_hash=uid_hash,
        subject_id=subject_id,
        device_alias=device_alias,
        security_epoch=7,
        received_ts=2_000_000_000.0,
        intended_disposition="quarantined",
        body_sha256="d" * 64,
        lease_id="e" * 32,
        nonce="f" * 32,
        claim_id=claim_id,
    )
    store.finalize_identity(
        session_hash="admin-session",
        subject_id=subject_id,
        body_sha256="d" * 64,
        lease_id="e" * 32,
        nonce="f" * 32,
        disposition="quarantined",
        claim_id=claim_id,
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "upload_identity_db_admin.py"),
            "--db",
            str(store.path),
            "inspect-session",
            "--session-hash",
            "admin-session",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["records"] == [
        {
            "device_alias": device_alias[:16],
            "disposition": "quarantined",
            "received_ts": 2_000_000_000.0,
            "security_epoch": 7,
            "subject_kind": "paid",
            "uid_alias": uid_hash,
        }
    ]
    combined = completed.stdout + completed.stderr
    for private_value in (subject_id, device_alias, "e" * 32, "f" * 32):
        assert private_value not in combined


@pytest.mark.parametrize("subject_kind", ["paid", "trial"])
def test_device_admin_can_inspect_block_and_unblock_a_trusted_session(
    tmp_path: Path,
    subject_kind: str,
) -> None:
    store = UploadIdentityStore(tmp_path / "session-admin.sqlite3", create=True)
    uid_hash = (
        upload_lease.paid_uid_hash("session-paid")
        if subject_kind == "paid"
        else None
    )
    block_id = activation_server.device_block_id(
        DEVICE_BLOCK_KEY,
        "session-device",
    )
    session_hash = f"{subject_kind}-admin-session"
    claim_id = _reserve_request_claim(
        store,
        lease_id="c" * 32,
        nonce="d" * 32,
        body_sha256="b" * 64,
    )
    store.prepare_identity(
        session_hash=session_hash,
        subject_kind=subject_kind,
        uid_hash=uid_hash,
        subject_id="a" * 64,
        device_alias=block_id,
        security_epoch=7,
        received_ts=2_000_000_000.0,
        intended_disposition="accepted",
        body_sha256="b" * 64,
        lease_id="c" * 32,
        nonce="d" * 32,
        claim_id=claim_id,
    )
    store.finalize_identity(
        session_hash=session_hash,
        subject_id="a" * 64,
        body_sha256="b" * 64,
        lease_id="c" * 32,
        nonce="d" * 32,
        disposition="accepted",
        claim_id=claim_id,
    )
    state = tmp_path / "activation-state"
    state.mkdir()
    (state / "device_block_key.txt").write_bytes(DEVICE_BLOCK_KEY)
    command = [
        sys.executable,
        str(SCRIPTS / "activation_device_admin.py"),
        "--state-dir",
        str(state),
        "--upload-identity-db-file",
        str(store.path),
        "--upload-security-epoch",
        "7",
    ]
    inspect = subprocess.run(
        command + ["inspect-session", "--session-hash", session_hash],
        capture_output=True,
        text=True,
        check=False,
    )
    assert inspect.returncode == 0
    assert session_hash not in inspect.stdout
    assert block_id not in inspect.stdout
    assert json.loads(inspect.stdout)["candidate"]["device_alias"] == block_id[:16]

    blocked = subprocess.run(
        command
        + [
            "block-session",
            "--session-hash",
            session_hash,
            "--reason",
            "confirmed_abuse",
            "--evidence-ref",
            "INC-ABCDEF123456",
            "--apply",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode == 0
    assert block_id in activation_server._load_device_blocks_state(
        state / "device_blocks.json"
    )["blocked"]

    unblocked = subprocess.run(
        command
        + [
            "unblock-session",
            "--session-hash",
            session_hash,
            "--reason",
            "manual_appeal",
            "--evidence-ref",
            "APL-ABCDEF123456",
            "--apply",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert unblocked.returncode == 0
    assert activation_server._load_device_blocks_state(
        state / "device_blocks.json"
    )["blocked"] == {}


@pytest.mark.parametrize("failure", ["identity_required", "lease_expired"])
def test_uploader_refreshes_once_and_retries_once_only_for_refreshable_identity_failure(
    monkeypatch,
    failure: str,
) -> None:
    calls = {"network": 0, "refresh": 0}
    lease = {
        "value": "stale-opaque-lease" if failure == "lease_expired" else None
    }
    nonces = []

    class Response:
        def getcode(self):
            return 200

        def read(self):
            return b'{"accepted":1,"duplicate":0,"quarantined":0,"rejected":0}'

    def urlopen(request, timeout):  # noqa: ANN001
        calls["network"] += 1
        nonces.append(request.get_header("X-upload-nonce"))
        if calls["network"] == 1:
            headers = Message()
            headers["X-BidKing-Upload-Status"] = failure
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "denied",
                headers,
                None,
            )
        assert request.get_header("X-upload-lease") == "fresh-opaque-lease"
        return Response()

    def refresh(*, timeout):
        calls["refresh"] += 1
        lease["value"] = "fresh-opaque-lease"
        return object()

    identity = client_activate.ActivationIdentitySnapshot(
        token="TOKEN-A",
        device="DEVICE-A",
        revision=4,
        clear_epoch=1,
    )
    monkeypatch.setattr(
        client_activate,
        "activation_identity_snapshot",
        lambda *_: identity,
    )
    monkeypatch.setattr(
        client_activate,
        "current_upload_authority_snapshot",
        lambda: client_activate.UploadAuthoritySnapshot(
            identity=identity,
            lease=lease["value"],
        ),
    )
    monkeypatch.setattr(enforce, "refresh_activation", refresh)
    monkeypatch.setattr(sample_upload, "urlopen_verified", urlopen)
    monkeypatch.setattr(sample_upload, "_activation_upload_denied", lambda: False)
    monkeypatch.setattr(sample_upload, "_write_upload_status", lambda _line: None)

    sample_upload._post(
        "https://example.invalid/ingest",
        "ingest-key",
        {"map_id": 1},
    )

    assert calls == {"network": 2, "refresh": 1}
    if failure == "lease_expired":
        assert len(set(nonces)) == 2


@pytest.mark.parametrize(
    "failure",
    [
        "uid_revoked",
        "device_revoked",
        "identity_invalid",
        "identity_state_error",
        "request_replay",
        "request_in_flight",
    ],
)
def test_uploader_never_refreshes_or_retries_other_authoritative_failures(
    monkeypatch,
    failure: str,
) -> None:
    calls = {"network": 0, "refresh": 0}
    identity = client_activate.ActivationIdentitySnapshot(
        token="TOKEN-A",
        device="DEVICE-A",
        revision=4,
        clear_epoch=1,
    )

    def urlopen(request, timeout):  # noqa: ANN001
        calls["network"] += 1
        headers = Message()
        headers["X-BidKing-Upload-Status"] = failure
        raise urllib.error.HTTPError(request.full_url, 401, "denied", headers, None)

    def refresh(*, timeout):
        calls["refresh"] += 1
        return object()

    monkeypatch.setattr(
        client_activate,
        "current_upload_authority_snapshot",
        lambda: client_activate.UploadAuthoritySnapshot(
            identity=identity,
            lease="opaque-lease",
        ),
    )
    monkeypatch.setattr(client_activate, "activation_identity_snapshot", lambda *_: identity)
    monkeypatch.setattr(enforce, "refresh_activation", refresh)
    monkeypatch.setattr(sample_upload, "urlopen_verified", urlopen)
    monkeypatch.setattr(sample_upload, "_activation_upload_denied", lambda: False)
    monkeypatch.setattr(sample_upload, "_write_upload_status", lambda _line: None)

    sample_upload._post("https://example.invalid/ingest", "key", {"map_id": 1})

    assert calls == {"network": 1, "refresh": 0}


def test_uploader_does_not_retransmit_after_identity_changes_during_refresh(
    monkeypatch,
) -> None:
    calls = {"network": 0, "refresh": 0}
    current = {
        "identity": client_activate.ActivationIdentitySnapshot(
            token="TOKEN-A",
            device="DEVICE-A",
            revision=4,
            clear_epoch=1,
        ),
        "lease": None,
    }

    def urlopen(request, timeout):  # noqa: ANN001
        calls["network"] += 1
        headers = Message()
        headers["X-BidKing-Upload-Status"] = "identity_required"
        raise urllib.error.HTTPError(request.full_url, 401, "denied", headers, None)

    def refresh(*, timeout):
        calls["refresh"] += 1
        current["identity"] = client_activate.ActivationIdentitySnapshot(
            token="TOKEN-B",
            device="DEVICE-A",
            revision=6,
            clear_epoch=1,
        )
        current["lease"] = "identity-B-lease"
        return object()

    monkeypatch.setattr(
        client_activate,
        "current_upload_authority_snapshot",
        lambda: client_activate.UploadAuthoritySnapshot(
            identity=current["identity"],
            lease=current["lease"],
        ),
    )
    monkeypatch.setattr(
        client_activate,
        "activation_identity_snapshot",
        lambda *_: current["identity"],
    )
    monkeypatch.setattr(enforce, "refresh_activation", refresh)
    monkeypatch.setattr(sample_upload, "urlopen_verified", urlopen)
    monkeypatch.setattr(sample_upload, "_activation_upload_denied", lambda: False)
    monkeypatch.setattr(sample_upload, "_write_upload_status", lambda _line: None)

    sample_upload._post("https://example.invalid/ingest", "key", {"map_id": 1})

    assert calls == {"network": 1, "refresh": 1}


def test_uploader_never_refreshes_or_retries_more_than_once(monkeypatch) -> None:
    calls = {"network": 0, "refresh": 0}
    lease = {"value": None}
    identity = client_activate.ActivationIdentitySnapshot(
        token="TOKEN-A",
        device="DEVICE-A",
        revision=4,
        clear_epoch=1,
    )

    def urlopen(request, timeout):  # noqa: ANN001
        calls["network"] += 1
        headers = Message()
        headers["X-BidKing-Upload-Status"] = (
            "identity_required" if calls["network"] == 1 else "lease_expired"
        )
        raise urllib.error.HTTPError(request.full_url, 401, "denied", headers, None)

    def refresh(*, timeout):
        calls["refresh"] += 1
        lease["value"] = "fresh-opaque-lease"
        return object()

    monkeypatch.setattr(client_activate, "activation_identity_snapshot", lambda *_: identity)
    monkeypatch.setattr(
        client_activate,
        "current_upload_authority_snapshot",
        lambda: client_activate.UploadAuthoritySnapshot(
            identity=identity,
            lease=lease["value"],
        ),
    )
    monkeypatch.setattr(enforce, "refresh_activation", refresh)
    monkeypatch.setattr(sample_upload, "urlopen_verified", urlopen)
    monkeypatch.setattr(sample_upload, "_activation_upload_denied", lambda: False)
    monkeypatch.setattr(sample_upload, "_write_upload_status", lambda _line: None)

    sample_upload._post("https://example.invalid/ingest", "key", {"map_id": 1})

    assert calls == {"network": 2, "refresh": 1}


def test_client_upload_lease_is_identity_bound_and_aba_safe(monkeypatch) -> None:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from bidking_lab.secure import client_activate

    now = int(time.time())
    envelope = upload_lease.sign(
        {
            "schema_version": 1,
            "kind": "sample_upload",
            "subject_kind": "paid",
            "subject_id": "a" * 64,
            "uid_hash": "b" * 16,
            "device_alias": "c" * 64,
            "security_epoch": 7,
            "lease_id": "d" * 32,
            "iat": now,
            "exp": now + 300,
            "entitlement_exp": None,
        },
        SECRET,
    )
    identity_a = client_activate.ActivationIdentitySnapshot("token-a", "device-a", 2, 0)
    identity_b = client_activate.ActivationIdentitySnapshot("token-b", "device-a", 4, 0)
    identity_a_after_aba = client_activate.ActivationIdentitySnapshot(
        "token-a", "device-a", 6, 0
    )
    client_activate._RUNTIME_ACTIVATION_DENIED.clear()
    client_activate._install_process_upload_lease((envelope, now + 300), identity_a)
    monkeypatch.setattr(client_activate, "activation_identity_snapshot", lambda *_: identity_a)
    assert client_activate.current_upload_lease(now=now) == envelope
    monkeypatch.setattr(client_activate, "activation_identity_snapshot", lambda *_: identity_b)
    assert client_activate.current_upload_lease(now=now) is None
    monkeypatch.setattr(
        client_activate,
        "activation_identity_snapshot",
        lambda *_: identity_a_after_aba,
    )
    assert client_activate.current_upload_lease(now=now) is None


def test_required_http_ingest_replays_idempotently_and_rechecks_revoke(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    token = activation_core.mint_token("http-paid", SECRET)
    code, activation = service.handle_activate(
        {
            "token": token,
            "device": "http-device",
            "client_security_epoch": 1,
            "upload_lease_schema": 1,
        }
    )
    assert code == 200
    store = UploadIdentityStore(tmp_path / "http-identity.sqlite3", create=True)
    monkeypatch.setattr(collector, "_ACT", service)
    monkeypatch.setattr(collector, "_UPLOAD_IDENTITY_MODE", "required")
    monkeypatch.setattr(collector, "_UPLOAD_IDENTITY_STORE", store)
    monkeypatch.setattr(collector, "_KEY", "ingest-test-key")
    monkeypatch.setattr(collector, "_OUT", tmp_path / "http-samples.jsonl")
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_quarantine_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(collector, "_rate_ok", lambda *_args, **_kwargs: True)
    server = collector.SafeThreadingHTTPServer(("127.0.0.1", 0), collector.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(
        payload: dict,
        nonce: str,
        *,
        signed_nonce: str | None = None,
        header_lease: str | None = None,
        signed_lease: str | None = None,
    ):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = _request_mac(
            b"ingest-test-key",
            timestamp=timestamp,
            nonce=signed_nonce or nonce,
            lease=signed_lease or activation["upload_lease"],
            body=raw,
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/ingest",
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Sig-Ts": timestamp,
                "X-Sig": signature,
                "X-Upload-Lease": header_lease or activation["upload_lease"],
                "X-Upload-Nonce": nonce,
            },
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    sample = {
        "map_id": 2401,
        "final_quality_counts": "q1=1",
        "session_hash": "http-trusted-session",
    }
    try:
        first = post(sample, "1" * 32)
        replay = post(sample, "1" * 32)
        assert first == replay
        assert first[1]["accepted"] == 1
        with pytest.raises(urllib.error.HTTPError) as nonce_mutation:
            post(
                {**sample, "session_hash": "mutated-nonce"},
                "2" * 32,
                signed_nonce="3" * 32,
            )
        assert nonce_mutation.value.code == 401
        _, second_activation = service.handle_activate(
            {
                "token": token,
                "device": "http-device",
                "client_security_epoch": 1,
                "upload_lease_schema": 1,
            }
        )
        with pytest.raises(urllib.error.HTTPError) as lease_mutation:
            post(
                {**sample, "session_hash": "mutated-lease"},
                "4" * 32,
                header_lease=second_activation["upload_lease"],
                signed_lease=activation["upload_lease"],
            )
        assert lease_mutation.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as changed:
            post({**sample, "known_value_sum": 1}, "1" * 32)
        assert changed.value.code == 409

        original_accept = collector._accept_one
        store_b = UploadIdentityStore(store.path)
        takeover: dict[str, object] = {}

        def accept_with_live_takeover(record, **kwargs):  # noqa: ANN001
            if (
                record.get("session_hash") == "handler-fenced-session"
                and not takeover
            ):
                upload_identity = kwargs["upload_identity"]
                body_digest = kwargs["body_sha256"]
                resumed = store_b.reserve_request(
                    lease_id=upload_identity.lease_id,
                    nonce=upload_identity.request_nonce,
                    body_sha256=body_digest,
                    expires_at=upload_identity.lease_exp,
                    now=int(time.time()) + 15,
                )
                assert resumed.state == "resumed"
                assert type(resumed.claim_id) is str
                disposition = original_accept(
                    record,
                    **{
                        **kwargs,
                        "identity_store": store_b,
                        "request_recovery": True,
                        "request_claim_id": resumed.claim_id,
                    },
                )
                response_b = {
                    "accepted": int(disposition == "accepted"),
                    "duplicate": int(disposition == "duplicate"),
                    "quarantined": int(disposition == "quarantined"),
                    "rejected": int(disposition == "rejected"),
                }
                store_b.finalize_request(
                    lease_id=upload_identity.lease_id,
                    nonce=upload_identity.request_nonce,
                    body_sha256=body_digest,
                    response=response_b,
                    claim_id=resumed.claim_id,
                )
                takeover["response"] = response_b
            return original_accept(record, **kwargs)

        monkeypatch.setattr(collector, "_accept_one", accept_with_live_takeover)
        fenced_sample = {
            **sample,
            "session_hash": "handler-fenced-session",
        }
        rows_before = len(
            (tmp_path / "http-samples.jsonl").read_text(encoding="utf-8").splitlines()
        )
        with pytest.raises(urllib.error.HTTPError) as old_owner:
            post(fenced_sample, "5" * 32)
        assert old_owner.value.code == 503
        replay_after_takeover = post(fenced_sample, "5" * 32)
        assert replay_after_takeover == (200, takeover["response"])
        rows_after = len(
            (tmp_path / "http-samples.jsonl").read_text(encoding="utf-8").splitlines()
        )
        assert rows_after == rows_before + 1

        (tmp_path / "revoked.txt").write_text("http-paid\n", encoding="utf-8")
        with pytest.raises(urllib.error.HTTPError) as revoked:
            post({**sample, "session_hash": "after-revoke"}, "2" * 32)
        assert revoked.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
