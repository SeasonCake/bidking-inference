from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_endpoint3_relay as relay  # noqa: E402
import activation_endpoint3_relay_deploy_preflight as relay_preflight  # noqa: E402
import activation_endpoint3_relay_state_admin as relay_state  # noqa: E402


KEY = b"relay-state-preflight-test-key-material"
DIGESTS = tuple(
    hashlib.sha256(value).hexdigest()
    for value in (b"activation", b"master", b"device-block")
)


def _init(path: Path, *, epoch: int = 2, window: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert relay_state.initialize_replay_database(
        path,
        security_epoch=epoch,
        auth_window_seconds=window,
    ) in {"initialized", "already_ready"}


def _unit_text(
    *,
    enabled: bool,
    epoch: int,
    key_file: Path,
    replay_db: Path,
    duplicate_epoch: bool = False,
) -> str:
    digest_args = " ".join(
        f"--forbidden-key-sha256 {digest}" for digest in DIGESTS
    )
    enable_arg = " --enable" if enabled else ""
    duplicate = f" --relay-security-epoch {epoch}" if duplicate_epoch else ""
    return (
        "[Unit]\nDescription=synthetic endpoint3 relay\n"
        "[Service]\n"
        "User=relay-user\n"
        "Group=relay-group\n"
        "UMask=0077\n"
        "WorkingDirectory=/opt/bidking\n"
        "StateDirectory=bidking-endpoint3-relay\n"
        "StateDirectoryMode=0700\n"
        f"Environment={relay.ENABLE_ENV}={'1' if enabled else '0'}\n"
        "ExecStart=/usr/bin/python3 /opt/bidking/activation_endpoint3_relay.py "
        "--mode origin --host 127.0.0.1 --port 18931"
        f"{enable_arg} --key-file {key_file.as_posix()} "
        "--upstream-base http://127.0.0.1:18900 "
        f"--replay-db {replay_db.as_posix()} "
        f"--relay-security-epoch {epoch}{duplicate} "
        "--auth-window-seconds 30 --timeout-seconds 5 "
        f"--allow-http-loopback {digest_args}\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )


def _preflight_args(
    *,
    candidate: Path,
    installed: Path,
    rollback: Path,
    key_file: Path,
    replay_db: Path,
    installed_absent: bool,
    epoch: int = 2,
) -> list[str]:
    info = key_file.lstat()
    args = [
        "--candidate-unit",
        str(candidate),
        "--installed-unit",
        str(installed),
        "--rollback-unit",
        str(rollback),
        "--expected-python",
        "/usr/bin/python3",
        "--expected-relay-script",
        "/opt/bidking/activation_endpoint3_relay.py",
        "--expected-user",
        "relay-user",
        "--expected-group",
        "relay-group",
        "--expected-umask",
        "0077",
        "--expected-working-directory",
        "/opt/bidking",
        "--expected-state-directory",
        "bidking-endpoint3-relay",
        "--expected-state-directory-mode",
        "0700",
        "--key-file",
        str(key_file),
        "--expected-key-uid",
        str(info.st_uid),
        "--expected-key-gid",
        str(info.st_gid),
        "--expected-key-mode",
        "0600",
        "--replay-db",
        str(replay_db),
        "--relay-security-epoch",
        str(epoch),
        "--auth-window-seconds",
        "30",
        "--timeout-seconds",
        "5",
    ]
    if installed_absent:
        args.append("--installed-unit-absent")
    return args


def _fixture(
    tmp_path: Path,
    *,
    installed_absent: bool = True,
    candidate_epoch: int = 2,
    installed_epoch: int = 1,
    rollback_epoch: int | None = None,
    duplicate_epoch: bool = False,
) -> tuple[list[str], dict[str, Path]]:
    state_dir = tmp_path / "bidking-endpoint3-relay"
    state_dir.mkdir()
    try:
        state_dir.chmod(0o700)
    except OSError:
        pass
    key_file = state_dir / "relay.key"
    key_file.write_bytes(KEY)
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    replay_db = state_dir / "relay.sqlite3"
    _init(replay_db, epoch=candidate_epoch)
    candidate = tmp_path / "candidate.service"
    candidate.write_text(
        _unit_text(
            enabled=True,
            epoch=candidate_epoch,
            key_file=key_file,
            replay_db=replay_db,
            duplicate_epoch=duplicate_epoch,
        ),
        encoding="utf-8",
    )
    installed = tmp_path / "installed.service"
    if not installed_absent:
        installed.write_text(
            _unit_text(
                enabled=False,
                epoch=installed_epoch,
                key_file=key_file,
                replay_db=replay_db,
            ),
            encoding="utf-8",
        )
    rollback = tmp_path / "rollback.service"
    rollback.write_text(
        _unit_text(
            enabled=False,
            epoch=rollback_epoch if rollback_epoch is not None else candidate_epoch,
            key_file=key_file,
            replay_db=replay_db,
        ),
        encoding="utf-8",
    )
    paths = {
        "state_dir": state_dir,
        "key": key_file,
        "db": replay_db,
        "candidate": candidate,
        "installed": installed,
        "rollback": rollback,
    }
    return (
        _preflight_args(
            candidate=candidate,
            installed=installed,
            rollback=rollback,
            key_file=key_file,
            replay_db=replay_db,
            installed_absent=installed_absent,
            epoch=candidate_epoch,
        ),
        paths,
    )


def test_status_missing_is_explicit_and_does_not_create_parent(tmp_path: Path) -> None:
    parent = tmp_path / "missing-state"
    path = parent / "relay.sqlite3"
    command = [
        sys.executable,
        str(SCRIPTS / "activation_endpoint3_relay_state_admin.py"),
        "--db",
        str(path),
        "--relay-security-epoch",
        "2",
        "--auth-window-seconds",
        "30",
        "status",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["state"] == "missing"
    assert not parent.exists()


def test_offline_init_creates_exact_state_and_status_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "relay.sqlite3"
    _init(path)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    snapshot = relay_state.inspect_replay_database(
        path,
        expected_security_epoch=2,
        expected_auth_window_seconds=30,
    )
    after = (path.read_bytes(), path.stat().st_mtime_ns)
    assert snapshot.ready
    assert snapshot.database_schema_version == relay_state.REPLAY_SCHEMA_VERSION
    assert snapshot.relay_security_epoch == 2
    assert snapshot.auth_window_seconds == 30
    assert before == after


@pytest.mark.parametrize("kind", ["corrupt", "legacy"])
def test_status_rejects_corrupt_or_incompatible_bytes_without_mutation(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "relay.sqlite3"
    if kind == "corrupt":
        path.write_bytes(b"not-a-sqlite-database")
    else:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE relay_nonce (nonce TEXT PRIMARY KEY, expires_at INTEGER NOT NULL) WITHOUT ROWID"
            )
    before = path.read_bytes()
    snapshot = relay_state.inspect_replay_database(
        path,
        expected_security_epoch=2,
        expected_auth_window_seconds=30,
    )
    assert snapshot.state in {"corrupt", "incompatible"}
    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()


def test_offline_init_never_clobbers_existing_corrupt_or_mismatched_state(
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"private-corrupt-canary")
    before = corrupt.read_bytes()
    with pytest.raises(relay_state.RelayReplayStateError):
        relay_state.initialize_replay_database(
            corrupt,
            security_epoch=2,
            auth_window_seconds=30,
        )
    assert corrupt.read_bytes() == before

    mismatched = tmp_path / "mismatched.sqlite3"
    _init(mismatched, epoch=1)
    before = mismatched.read_bytes()
    with pytest.raises(relay_state.RelayReplayStateError, match="config_mismatch"):
        relay_state.initialize_replay_database(
            mismatched,
            security_epoch=2,
            auth_window_seconds=30,
        )
    assert mismatched.read_bytes() == before


def test_offline_init_rechecks_post_link_final_identity(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    _init(replacement)
    connection = sqlite3.connect(replacement)
    try:
        connection.execute(
            "INSERT INTO relay_nonce(nonce, expires_at) VALUES (?, ?)",
            ("a" * 32, 1_900_000_000),
        )
        connection.commit()
    finally:
        connection.close()
    replacement_bytes = replacement.read_bytes()
    real_require = relay_state.require_ready_replay_database
    replaced = False

    def replace_after_readonly_validation(
        path: Path, *, security_epoch: int, auth_window_seconds: int
    ):
        nonlocal replaced
        snapshot = real_require(
            path,
            security_epoch=security_epoch,
            auth_window_seconds=auth_window_seconds,
        )
        if path == target and not replaced:
            target.unlink()
            os.link(replacement, target, follow_symlinks=False)
            replaced = True
        return snapshot

    monkeypatch.setattr(
        relay_state,
        "require_ready_replay_database",
        replace_after_readonly_validation,
    )

    with pytest.raises(
        relay_state.RelayReplayStateError, match="published_identity_mismatch"
    ):
        _init(target)

    assert replaced is True
    assert target.read_bytes() == replacement_bytes
    connection = sqlite3.connect(target)
    try:
        assert connection.execute("SELECT COUNT(*) FROM relay_nonce").fetchone() == (1,)
    finally:
        connection.close()
    preserved_private = list(tmp_path.glob(f".{target.name}.init-*"))
    assert len(preserved_private) == 1
    assert relay_state.require_ready_replay_database(
        preserved_private[0],
        security_epoch=2,
        auth_window_seconds=30,
    ).ready
    private_connection = sqlite3.connect(preserved_private[0])
    try:
        assert private_connection.execute(
            "SELECT COUNT(*) FROM relay_nonce"
        ).fetchone() == (0,)
    finally:
        private_connection.close()


def test_offline_init_rechecks_late_final_sidecars(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    markers = {
        Path(f"{target}-journal"): b"late-journal-marker",
        Path(f"{target}-wal"): b"late-wal-marker",
        Path(f"{target}-shm"): b"late-shm-marker",
    }
    real_require = relay_state.require_ready_replay_database
    published_bytes: bytes | None = None

    def add_sidecars_after_readonly_validation(
        path: Path, *, security_epoch: int, auth_window_seconds: int
    ):
        nonlocal published_bytes
        snapshot = real_require(
            path,
            security_epoch=security_epoch,
            auth_window_seconds=auth_window_seconds,
        )
        if path == target and published_bytes is None:
            published_bytes = target.read_bytes()
            for sidecar, marker in markers.items():
                sidecar.write_bytes(marker)
        return snapshot

    monkeypatch.setattr(
        relay_state,
        "require_ready_replay_database",
        add_sidecars_after_readonly_validation,
    )

    with pytest.raises(relay_state.RelayReplayStateError, match="busy_or_dirty"):
        _init(target)

    assert published_bytes is not None
    assert target.read_bytes() == published_bytes
    assert {path: path.read_bytes() for path in markers} == markers
    assert not list(tmp_path.glob(f".{target.name}.init-*"))


def test_concurrent_offline_initializers_publish_one_exact_database(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    real_link = os.link
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def synchronized_link(source, destination, **kwargs):  # noqa: ANN001
        barrier.wait(timeout=5)
        return real_link(source, destination, **kwargs)

    def initialize() -> None:
        try:
            outcomes.append(
                relay_state.initialize_replay_database(
                    target,
                    security_epoch=2,
                    auth_window_seconds=30,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    monkeypatch.setattr(relay_state.os, "link", synchronized_link)
    workers = [threading.Thread(target=initialize) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    assert sorted(outcomes) == ["already_ready", "initialized"]
    assert relay_state.inspect_replay_database(
        target,
        expected_security_epoch=2,
        expected_auth_window_seconds=30,
    ).ready
    assert not list(tmp_path.glob(f".{target.name}.init-*"))


def test_publish_race_preserves_malformed_replacement_and_sidecars(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    main_marker = b"replacement-malformed-main"
    sidecar_markers = {
        Path(f"{target}-journal"): b"replacement-journal",
        Path(f"{target}-wal"): b"replacement-wal",
        Path(f"{target}-shm"): b"replacement-shm",
    }
    real_link = os.link
    injected = False

    def inject_before_link(source, destination, **kwargs):  # noqa: ANN001
        nonlocal injected
        if not injected:
            target.write_bytes(main_marker)
            for sidecar, marker in sidecar_markers.items():
                sidecar.write_bytes(marker)
            injected = True
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(relay_state.os, "link", inject_before_link)

    with pytest.raises(
        relay_state.RelayReplayStateError, match="concurrent_target_conflict"
    ):
        _init(target)

    assert injected is True
    assert target.read_bytes() == main_marker
    assert {path: path.read_bytes() for path in sidecar_markers} == sidecar_markers
    assert not list(tmp_path.glob(f".{target.name}.init-*"))


def test_private_temp_cleanup_preserves_competing_main_after_validation(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    real_link = os.link
    real_validate = relay_state._validate_published_file_identity
    captured: dict[str, Path] = {}
    marker = b"competing-private-temp-main"

    def capture_link(source, destination, **kwargs):  # noqa: ANN001
        captured["temporary"] = Path(source)
        return real_link(source, destination, **kwargs)

    def replace_temp_after_validation(
        path: Path, expected: os.stat_result
    ) -> None:
        real_validate(path, expected)
        temporary = captured["temporary"]
        temporary.unlink()
        temporary.write_bytes(marker)

    monkeypatch.setattr(relay_state.os, "link", capture_link)
    monkeypatch.setattr(
        relay_state,
        "_validate_published_file_identity",
        replace_temp_after_validation,
    )

    with pytest.raises(
        relay_state.RelayReplayStateError, match="private_cleanup_conflict"
    ):
        _init(target)

    temporary = captured["temporary"]
    assert temporary.read_bytes() == marker
    assert relay_state.require_ready_replay_database(
        target,
        security_epoch=2,
        auth_window_seconds=30,
    ).ready


def test_private_temp_cleanup_preserves_competing_sidecars_after_validation(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    real_link = os.link
    real_validate = relay_state._validate_published_file_identity
    captured: dict[str, Path] = {}
    markers: dict[Path, bytes] = {}

    def capture_link(source, destination, **kwargs):  # noqa: ANN001
        captured["temporary"] = Path(source)
        return real_link(source, destination, **kwargs)

    def add_sidecars_after_validation(
        path: Path, expected: os.stat_result
    ) -> None:
        real_validate(path, expected)
        for sidecar in relay_state._database_sidecars(captured["temporary"]):
            marker = ("competitor:" + sidecar.name).encode("ascii")
            sidecar.write_bytes(marker)
            markers[sidecar] = marker

    monkeypatch.setattr(relay_state.os, "link", capture_link)
    monkeypatch.setattr(
        relay_state,
        "_validate_published_file_identity",
        add_sidecars_after_validation,
    )

    with pytest.raises(
        relay_state.RelayReplayStateError, match="private_cleanup_conflict"
    ):
        _init(target)

    assert {path: path.read_bytes() for path in markers} == markers
    assert relay_state.require_ready_replay_database(
        target,
        security_epoch=2,
        auth_window_seconds=30,
    ).ready
    assert not captured["temporary"].exists()


def test_private_temp_same_inode_mutation_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    real_link = os.link
    real_validate = relay_state._validate_published_file_identity
    captured: dict[str, Path] = {}
    marker = b"same-inode-private-temp-mutation"

    def capture_link(source, destination, **kwargs):  # noqa: ANN001
        captured["temporary"] = Path(source)
        return real_link(source, destination, **kwargs)

    def mutate_temp_after_validation(
        path: Path, expected: os.stat_result
    ) -> None:
        real_validate(path, expected)
        with captured["temporary"].open("r+b") as stream:
            stream.seek(0)
            stream.truncate()
            stream.write(marker)
            stream.flush()
            os.fsync(stream.fileno())

    monkeypatch.setattr(relay_state.os, "link", capture_link)
    monkeypatch.setattr(
        relay_state,
        "_validate_published_file_identity",
        mutate_temp_after_validation,
    )

    with pytest.raises(
        relay_state.RelayReplayStateError, match="private_cleanup_conflict"
    ):
        _init(target)

    assert captured["temporary"].read_bytes() == marker
    assert target.read_bytes() == marker


def test_private_temp_hardlink_alias_mutation_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    alias = tmp_path / "competitor-alias.sqlite3"
    real_link = os.link
    real_validate = relay_state._validate_published_file_identity
    captured: dict[str, Path] = {}
    marker = b"hardlink-alias-private-temp-mutation"

    def capture_link(source, destination, **kwargs):  # noqa: ANN001
        captured["temporary"] = Path(source)
        return real_link(source, destination, **kwargs)

    def mutate_through_alias(
        path: Path, expected: os.stat_result
    ) -> None:
        real_validate(path, expected)
        real_link(captured["temporary"], alias)
        with alias.open("r+b") as stream:
            stream.seek(0)
            stream.truncate()
            stream.write(marker)
            stream.flush()
            os.fsync(stream.fileno())

    monkeypatch.setattr(relay_state.os, "link", capture_link)
    monkeypatch.setattr(
        relay_state,
        "_validate_published_file_identity",
        mutate_through_alias,
    )

    with pytest.raises(
        relay_state.RelayReplayStateError, match="private_cleanup_conflict"
    ):
        _init(target)

    assert captured["temporary"].read_bytes() == marker
    assert target.read_bytes() == marker
    assert alias.read_bytes() == marker


def test_private_temp_extra_hardlink_alias_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    alias = tmp_path / "competitor-alias.sqlite3"
    real_link = os.link
    real_validate = relay_state._validate_published_file_identity
    captured: dict[str, Path] = {}

    def capture_link(source, destination, **kwargs):  # noqa: ANN001
        captured["temporary"] = Path(source)
        return real_link(source, destination, **kwargs)

    def add_alias_after_validation(
        path: Path, expected: os.stat_result
    ) -> None:
        real_validate(path, expected)
        real_link(captured["temporary"], alias)

    monkeypatch.setattr(relay_state.os, "link", capture_link)
    monkeypatch.setattr(
        relay_state,
        "_validate_published_file_identity",
        add_alias_after_validation,
    )

    with pytest.raises(
        relay_state.RelayReplayStateError, match="private_cleanup_conflict"
    ):
        _init(target)

    assert captured["temporary"].exists()
    assert relay_state.require_ready_replay_database(
        target,
        security_epoch=2,
        auth_window_seconds=30,
    ).ready
    assert alias.read_bytes() == target.read_bytes()


def test_cleanup_conflict_preserves_primary_failure(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    real_link = os.link
    captured: dict[str, Path] = {}
    marker = b"competing-private-sidecar"

    def capture_link(source, destination, **kwargs):  # noqa: ANN001
        captured["temporary"] = Path(source)
        return real_link(source, destination, **kwargs)

    def fail_after_publish(
        _path: Path, _expected: os.stat_result
    ) -> None:
        sidecar = relay_state._database_sidecars(captured["temporary"])[1]
        sidecar.write_bytes(marker)
        raise relay_state.RelayReplayStateError("primary_failure")

    monkeypatch.setattr(relay_state.os, "link", capture_link)
    monkeypatch.setattr(
        relay_state,
        "_validate_published_file_identity",
        fail_after_publish,
    )

    with pytest.raises(
        relay_state.RelayReplayStateError, match="primary_failure"
    ) as raised:
        _init(target)

    assert raised.value.cleanup_state == "failed"
    assert relay_state._database_sidecars(captured["temporary"])[1].read_bytes() == marker


def test_final_is_revalidated_after_safe_private_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "relay.sqlite3"
    marker = b"post-cleanup-final-mutation"
    real_cleanup = relay_state._cleanup_owned_private_files

    def mutate_final_after_cleanup(*args, **kwargs):  # noqa: ANN002,ANN003
        real_cleanup(*args, **kwargs)
        with target.open("r+b") as stream:
            stream.seek(0)
            stream.truncate()
            stream.write(marker)
            stream.flush()
            os.fsync(stream.fileno())

    monkeypatch.setattr(
        relay_state,
        "_cleanup_owned_private_files",
        mutate_final_after_cleanup,
    )

    with pytest.raises(relay_state.RelayReplayStateError):
        _init(target)

    assert target.read_bytes() == marker


def test_private_cleanup_deletes_only_recorded_same_regular_objects(
    tmp_path: Path,
) -> None:
    paths = tuple(tmp_path / name for name in ("main", "journal", "wal", "shm"))
    for path in paths:
        path.write_bytes(path.name.encode("ascii"))
    fingerprints = {
        path: relay_state._stable_regular_file_fingerprint(path)
        for path in paths
    }

    relay_state._cleanup_owned_private_files(paths, fingerprints)

    assert not any(path.exists() for path in paths)
    relay_state._cleanup_owned_private_files(paths, fingerprints)


def test_private_cleanup_preserves_unknown_and_replaced_regular_objects(
    tmp_path: Path,
) -> None:
    owned = tmp_path / "owned"
    unknown = tmp_path / "unknown"
    owned.write_bytes(b"first-generation")
    expected = relay_state._stable_regular_file_fingerprint(owned)
    owned.unlink()
    owned.write_bytes(b"replacement-generation")
    unknown.write_bytes(b"unknown-generation")

    with pytest.raises(
        relay_state.RelayReplayStateError, match="private_cleanup_conflict"
    ):
        relay_state._cleanup_owned_private_files(
            (owned, unknown),
            {owned: expected},
        )

    assert owned.read_bytes() == b"replacement-generation"
    assert unknown.read_bytes() == b"unknown-generation"


def test_private_cleanup_preserves_symlink_identity_and_stat_failures(
    tmp_path: Path, monkeypatch
) -> None:
    symlink_view = tmp_path / "symlink-view"
    unreadable = tmp_path / "unreadable"
    symlink_view.write_bytes(b"preserve-symlink-view")
    unreadable.write_bytes(b"preserve-unreadable")
    fingerprints = {
        path: relay_state._stable_regular_file_fingerprint(path)
        for path in (symlink_view, unreadable)
    }
    real_stat = os.stat

    def unsafe_stat(path, *args, **kwargs):  # noqa: ANN001
        candidate = Path(path)
        if candidate == symlink_view:
            values = list(real_stat(path, *args, **kwargs))
            values[0] = stat.S_IFLNK
            return os.stat_result(values)
        if candidate == unreadable:
            raise PermissionError("synthetic no-follow stat failure")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(relay_state.os, "stat", unsafe_stat)

    with pytest.raises(
        relay_state.RelayReplayStateError, match="private_cleanup_conflict"
    ):
        relay_state._cleanup_owned_private_files(
            (symlink_view, unreadable),
            fingerprints,
        )

    assert symlink_view.read_bytes() == b"preserve-symlink-view"
    assert unreadable.read_bytes() == b"preserve-unreadable"


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO contract")
@pytest.mark.parametrize("writer_open", [False, True])
def test_private_cleanup_rejects_fifo_without_blocking(
    tmp_path: Path, writer_open: bool
) -> None:
    fifo = tmp_path / "private-fifo"
    os.mkfifo(fifo, 0o600)
    writer_descriptor: int | None = None
    if writer_open:
        writer_descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    try:
        with pytest.raises(
            relay_state.RelayReplayStateError, match="private_cleanup_conflict"
        ):
            relay_state._cleanup_owned_private_files((fifo,), {})
    finally:
        if writer_descriptor is not None:
            os.close(writer_descriptor)
    assert fifo.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO contract")
@pytest.mark.parametrize("writer_open", [False, True])
def test_primary_failure_survives_private_fifo_without_blocking(
    tmp_path: Path, monkeypatch, writer_open: bool
) -> None:
    target = tmp_path / "relay.sqlite3"
    real_link = os.link
    real_validate = relay_state._validate_published_file_identity
    captured: dict[str, Path] = {}
    writers: list[int] = []

    def capture_link(source, destination, **kwargs):  # noqa: ANN001
        captured["temporary"] = Path(source)
        return real_link(source, destination, **kwargs)

    def replace_private_with_fifo_then_fail(
        path: Path, expected: os.stat_result
    ) -> None:
        real_validate(path, expected)
        temporary = captured["temporary"]
        temporary.unlink()
        os.mkfifo(temporary, 0o600)
        if writer_open:
            writers.append(os.open(temporary, os.O_RDWR | os.O_NONBLOCK))
        raise relay_state.RelayReplayStateError("primary_failure")

    monkeypatch.setattr(relay_state.os, "link", capture_link)
    monkeypatch.setattr(
        relay_state,
        "_validate_published_file_identity",
        replace_private_with_fifo_then_fail,
    )

    started = time.monotonic()
    try:
        with pytest.raises(
            relay_state.RelayReplayStateError, match="primary_failure"
        ) as raised:
            _init(target)
    finally:
        for descriptor in writers:
            os.close(descriptor)

    assert time.monotonic() - started < 1.0
    assert raised.value.cleanup_state == "failed"
    assert captured["temporary"].exists()
    assert relay_state.require_ready_replay_database(
        target,
        security_epoch=2,
        auth_window_seconds=30,
    ).ready


def test_runtime_rejects_missing_or_corrupt_store_without_creating_state(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "relay.sqlite3"
    with pytest.raises(relay.RelayConfigurationError, match="replay_store_not_ready"):
        relay.RelayApplication(
            mode="origin",
            enabled=True,
            key=KEY,
            forbidden_key_sha256=frozenset(DIGESTS),
            upstream_base="http://127.0.0.1:18900",
            replay_db_path=missing,
            allow_http_loopback=True,
            security_epoch=2,
            auth_window_seconds=30,
        )
    assert not missing.parent.exists()
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"bad")
    before = corrupt.read_bytes()
    with pytest.raises(relay.RelayConfigurationError, match="replay_store_not_ready"):
        relay.RelayApplication(
            mode="origin",
            enabled=True,
            key=KEY,
            forbidden_key_sha256=frozenset(DIGESTS),
            upstream_base="http://127.0.0.1:18900",
            replay_db_path=corrupt,
            allow_http_loopback=True,
            security_epoch=2,
            auth_window_seconds=30,
        )
    assert corrupt.read_bytes() == before


def test_origin_preflight_ready_is_zero_write_and_reports_fixed_state(tmp_path: Path) -> None:
    key_file = tmp_path / "relay.key"
    key_file.write_bytes(KEY)
    replay_db = tmp_path / "state" / "relay.sqlite3"
    _init(replay_db)
    before = (replay_db.read_bytes(), replay_db.stat().st_mtime_ns)
    env = dict(os.environ)
    env[relay.ENABLE_ENV] = "1"
    digest_args = [
        item for digest in DIGESTS for item in ("--forbidden-key-sha256", digest)
    ]
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "activation_endpoint3_relay.py"),
            "--mode",
            "origin",
            "--enable",
            "--preflight",
            "--key-file",
            str(key_file),
            "--upstream-base",
            "http://127.0.0.1:18900",
            "--replay-db",
            str(replay_db),
            "--relay-security-epoch",
            "2",
            "--auth-window-seconds",
            "30",
            "--allow-http-loopback",
            *digest_args,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["eligible"] is True
    assert payload["replay_store_state"] == "ready"
    assert payload["relay_security_epoch"] == 2
    assert (replay_db.read_bytes(), replay_db.stat().st_mtime_ns) == before
    assert str(tmp_path) not in completed.stdout
    assert KEY.decode("ascii") not in completed.stdout


def test_origin_preflight_corrupt_store_is_explicit_and_zero_write(tmp_path: Path) -> None:
    key_file = tmp_path / "relay.key"
    key_file.write_bytes(KEY)
    replay_db = tmp_path / "relay.sqlite3"
    replay_db.write_bytes(b"corrupt-private-state-canary")
    before = (replay_db.read_bytes(), replay_db.stat().st_mtime_ns)
    env = dict(os.environ)
    env[relay.ENABLE_ENV] = "1"
    digest_args = [
        item for digest in DIGESTS for item in ("--forbidden-key-sha256", digest)
    ]
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "activation_endpoint3_relay.py"),
            "--mode",
            "origin",
            "--enable",
            "--preflight",
            "--key-file",
            str(key_file),
            "--upstream-base",
            "http://127.0.0.1:18900",
            "--replay-db",
            str(replay_db),
            "--relay-security-epoch",
            "2",
            "--auth-window-seconds",
            "30",
            "--allow-http-loopback",
            *digest_args,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["eligible"] is False
    assert payload["replay_store_state"] == "corrupt"
    assert (replay_db.read_bytes(), replay_db.stat().st_mtime_ns) == before
    assert "corrupt-private-state-canary" not in completed.stdout


def test_unit_preflight_receipt_is_privacy_safe_and_authoritative_zero_write(
    tmp_path: Path, capsys
) -> None:
    args, paths = _fixture(tmp_path)
    before = {
        name: (path.read_bytes(), path.stat().st_mtime_ns)
        for name, path in paths.items()
        if path.is_file()
    }
    expected_code = 0 if os.name == "posix" else 4
    assert relay_preflight.main(args) == expected_code
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload["status"] == (
        "eligible" if os.name == "posix" else "target_host_prelight_pending"
    )
    assert payload["installed_unit_state"] == "missing"
    assert payload["relay_security_epoch_before"] == 0
    assert payload["relay_security_epoch_after"] == 2
    assert payload["rollback_relay_security_epoch"] == 2
    assert payload["authoritative_unchanged"] is True
    assert payload["authoritative_before_sha256"] == payload["authoritative_after_sha256"]
    after = {
        name: (path.read_bytes(), path.stat().st_mtime_ns)
        for name, path in paths.items()
        if path.is_file()
    }
    assert after == before
    assert str(tmp_path) not in stdout
    assert KEY.decode("ascii") not in stdout
    assert "127.0.0.1" not in stdout


def test_unit_preflight_allows_monotonic_current_epoch_and_disabled_rollback(
    tmp_path: Path, capsys
) -> None:
    args, _paths = _fixture(
        tmp_path,
        installed_absent=False,
        candidate_epoch=2,
        installed_epoch=1,
        rollback_epoch=2,
    )
    expected_code = 0 if os.name == "posix" else 4
    assert relay_preflight.main(args) == expected_code
    payload = json.loads(capsys.readouterr().out)
    assert payload["installed_unit_state"] == "ready"
    assert payload["relay_security_epoch_before"] == 1
    assert payload["relay_security_epoch_after"] == 2


@pytest.mark.parametrize(
    ("fixture_kwargs", "reason"),
    [
        ({"duplicate_epoch": True}, "unit_option_not_exact_once"),
        (
            {"candidate_epoch": 2, "rollback_epoch": 1},
            "rollback_security_epoch_downgrade",
        ),
    ],
)
def test_unit_preflight_rejects_duplicate_or_rollback_epoch_regression(
    tmp_path: Path, capsys, fixture_kwargs: dict[str, object], reason: str
) -> None:
    args, _paths = _fixture(tmp_path, **fixture_kwargs)
    assert relay_preflight.main(args) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "mode": "preflight_only",
        "reason": reason,
        "schema_version": 1,
        "status": "blocked",
    }


def test_unit_preflight_rejects_unexpected_execstart_positional_argument(
    tmp_path: Path, capsys
) -> None:
    args, paths = _fixture(tmp_path)
    candidate = paths["candidate"]
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            "/opt/bidking/activation_endpoint3_relay.py ",
            "/opt/bidking/activation_endpoint3_relay.py private-positional-canary ",
        ),
        encoding="utf-8",
    )
    assert relay_preflight.main(args) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "unit_unexpected_positional_argument"


def test_unit_preflight_rejects_key_symlink_without_disclosing_target(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    args, paths = _fixture(tmp_path)
    target = paths["key"]
    link = tmp_path / "relay-key-link"
    index = args.index("--key-file") + 1
    args[index] = str(link)
    for unit_name in ("candidate", "rollback"):
        unit_path = paths[unit_name]
        unit_path.write_text(
            unit_path.read_text(encoding="utf-8").replace(
                target.as_posix(), link.as_posix()
            ),
            encoding="utf-8",
        )
    real_lstat = Path.lstat
    target_info = target.lstat()
    synthetic = list(target_info)
    synthetic[0] = stat.S_IFLNK | 0o777

    def lstat_with_synthetic_link(path: Path):
        if path == link:
            return os.stat_result(synthetic)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat_with_synthetic_link)
    assert relay_preflight.main(args) == 2
    stdout = capsys.readouterr().out
    assert json.loads(stdout)["reason"] == "unsafe_file_type"
    assert str(target) not in stdout


def test_security_epoch_is_covered_by_relay_signature(tmp_path: Path) -> None:
    path = tmp_path / "relay.sqlite3"
    _init(path, epoch=2)
    guard = relay.SQLiteReplayGuard(path, security_epoch=2, auth_window_seconds=30)
    now = 1_700_000_000
    body = b"{}"
    raw_path = relay.ORIGIN_PREFIX + "/activate"
    headers = relay.signed_headers(
        KEY,
        security_epoch=1,
        timestamp=now,
        nonce="a" * 32,
        method="POST",
        path=raw_path,
        body=body,
    )
    with pytest.raises(relay.RelayRequestError, match="relay_auth_failed"):
        relay._verify_auth(
            key=KEY,
            replay_guard=guard,
            security_epoch=2,
            auth_window_seconds=30,
            headers={name.lower(): (value,) for name, value in headers.items()},
            method="POST",
            raw_path=raw_path,
            body=body,
            now=now,
        )
