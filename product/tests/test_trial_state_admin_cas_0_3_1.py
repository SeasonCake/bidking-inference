from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ADMIN_PATH = SCRIPTS / "trial_state_admin.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load_admin():
    spec = importlib.util.spec_from_file_location("trial_state_admin_cas_test", ADMIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: dict) -> bytes:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    return path.read_bytes()


def _replace_bytes_in_subprocess(path: Path, payload: bytes) -> None:
    environment = dict(os.environ)
    environment.update(
        {
            "BIDKING_TEST_REPLACE_PATH": str(path),
            "BIDKING_TEST_REPLACE_HEX": payload.hex(),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; from pathlib import Path; "
                "path=Path(os.environ['BIDKING_TEST_REPLACE_PATH']); "
                "path.unlink(); "
                "stream=path.open('xb'); "
                "stream.write(bytes.fromhex(os.environ['BIDKING_TEST_REPLACE_HEX'])); "
                "stream.flush(); os.fsync(stream.fileno()); stream.close()"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def _apply_args(
    *,
    state: Path,
    backup: Path,
    expected_sha256: str,
    mode: str = "migrate",
    hours: int = 12,
    cutover: int | None = None,
) -> list[str]:
    args = [
        "--state-file",
        str(state),
        "--mode",
        mode,
    ]
    if mode == "migrate":
        assert cutover is not None
        args.extend(
            [
                "--cutover-at",
                str(cutover),
                "--grandfather-hours",
                str(hours),
            ]
        )
    else:
        args.extend(["--downgrade-hours", str(hours)])
    return [
        *args,
        "--apply",
        "--expected-sha256",
        expected_sha256,
        "--backup-file",
        str(backup),
    ]


@pytest.mark.parametrize("mode", ("migrate", "downgrade"))
def test_two_real_admin_processes_are_serialized_without_lost_updates(
    tmp_path: Path,
    mode: str,
) -> None:
    now = int(time.time())
    cutover = now + 10
    if mode == "migrate":
        records = {f"device-{index}": now - 100 - index for index in range(50_000)}
        hours = (11, 12)
    else:
        records = {
            f"device-{index}": {
                "schema_version": 2,
                "start": now - 100 - index,
                "window_seconds": 3 * 3600,
                "last_server_now": now,
            }
            for index in range(50_000)
        }
        hours = (12, 13)
    state = tmp_path / "trials.json"
    original = _write_json(state, records)
    expected = hashlib.sha256(original).hexdigest()
    backups = (tmp_path / "operator-a.json", tmp_path / "operator-b.json")
    commands = [
        [
            sys.executable,
            str(ADMIN_PATH),
            *_apply_args(
                state=state,
                backup=backup,
                expected_sha256=expected,
                mode=mode,
                hours=window,
                cutover=cutover,
            ),
        ]
        for backup, window in zip(backups, hours, strict=True)
    ]

    processes = [
        subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for command in commands
    ]
    results = [process.communicate(timeout=30) for process in processes]
    returncodes = [process.returncode for process in processes]

    assert sorted(returncodes) == [0, 2], results
    winner = returncodes.index(0)
    loser = 1 - winner
    receipt = json.loads(results[winner][0])
    assert receipt["lock_backend"] == (
        "sqlite-begin-exclusive+claim-link-noreplace"
    )
    assert receipt["input_sha256"] == expected
    assert receipt["backup_sha256"] == expected
    assert receipt["output_sha256"] == hashlib.sha256(state.read_bytes()).hexdigest()
    assert backups[winner].read_bytes() == original
    assert not backups[loser].exists()
    assert "failed: ValueError" in results[loser][1]

    final = json.loads(state.read_text(encoding="utf-8"))
    if mode == "migrate":
        assert final["device-0"]["window_seconds"] == hours[winner] * 3600
    else:
        expected_expiry = records["device-0"]["start"] + 3 * 3600
        assert final["device-0"] + hours[winner] * 3600 == expected_expiry


@pytest.mark.parametrize(
    "mutation",
    ("missing", "different_contents", "same_contents_new_identity"),
)
def test_commit_time_cas_rejects_external_state_change_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    expected = hashlib.sha256(original).hexdigest()
    original_stage = admin._stage_state_output

    def stage_then_mutate(path: Path, value: dict):
        staged = original_stage(path, value)
        if mutation == "missing":
            path.unlink()
        elif mutation == "different_contents":
            _write_json(path, {"external": now - 30})
        else:
            path.unlink()
            path.write_bytes(original)
        return staged

    monkeypatch.setattr(admin, "_stage_state_output", stage_then_mutate)
    with pytest.raises(ValueError, match="changed|missing|SHA-256|identity"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=expected,
                cutover=now + 1,
            )
        )

    assert not backup.exists()
    if mutation == "missing":
        assert not state.exists()
    elif mutation == "different_contents":
        assert json.loads(state.read_text(encoding="utf-8")) == {
            "external": now - 30
        }
    else:
        assert state.read_bytes() == original


def test_identity_and_sha_are_rechecked_after_backup_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    expected = hashlib.sha256(original).hexdigest()
    original_publish = admin._publish_backup

    def publish_then_replace(path: Path, raw: bytes) -> None:
        original_publish(path, raw)
        state.unlink()
        state.write_bytes(original)

    monkeypatch.setattr(admin, "_publish_backup", publish_then_replace)
    with pytest.raises(ValueError, match="identity changed"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=expected,
                cutover=now + 1,
            )
        )

    assert state.read_bytes() == original
    assert backup.read_bytes() == original


def test_open_generation_pins_identity_across_real_same_content_replacements(
    tmp_path: Path,
) -> None:
    admin = _load_admin()
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": int(time.time()) - 60})
    generation = admin._open_state_generation(state)
    try:
        initial = generation.snapshot
        for _index in range(32):
            _replace_bytes_in_subprocess(state, original)
            current = admin._read_state(state)
            assert current.identity != initial.identity
            assert current.sha256 == initial.sha256
            try:
                held = generation.refresh()
            except ValueError as exc:
                # Some POSIX filesystems change ctime when the last pathname is
                # unlinked.  That is a safe rejection of the held generation.
                assert "generation changed" in str(exc)
            else:
                assert held.identity == initial.identity
                assert held.sha256 == initial.sha256
    finally:
        generation.close()


def test_real_process_same_content_replacement_before_claim_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    original_stage = admin._stage_state_output

    def stage_then_replace(path: Path, value: dict):
        staged = original_stage(path, value)
        _replace_bytes_in_subprocess(path, original)
        return staged

    monkeypatch.setattr(admin, "_stage_state_output", stage_then_replace)
    with pytest.raises(ValueError, match="identity"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )

    assert state.read_bytes() == original
    assert not backup.exists()
    assert _pending_admin_artifacts(tmp_path) == []


def test_staging_failure_leaves_state_and_backup_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"

    def fail_staging(_path: Path, _value: dict):
        raise OSError("synthetic state write failure")

    monkeypatch.setattr(admin, "_stage_state_output", fail_staging)
    with pytest.raises(OSError, match="state write failure"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )
    assert state.read_bytes() == original
    assert not backup.exists()


def test_staging_fsync_failure_leaves_state_and_backup_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError(errno.EINVAL, "synthetic file fsync unsupported")

    monkeypatch.setattr(admin.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="fsync unsupported"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )
    assert state.read_bytes() == original
    assert not backup.exists()
    assert _pending_admin_artifacts(tmp_path) == []


def test_backup_failure_leaves_authoritative_state_untouched(
    tmp_path: Path,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("blocked", encoding="utf-8")
    backup = blocker / "backup.json"

    with pytest.raises(OSError):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )
    assert state.read_bytes() == original
    assert not backup.exists()


def test_replace_failure_keeps_authority_and_leaves_exact_recovery_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"

    def fail_replace(
        _staged_generation,
        _state: Path,
        *,
        output_sha256: str,
        output_identity,
    ) -> None:
        assert len(output_sha256) == 64
        assert output_identity is not None
        raise OSError("synthetic atomic replace failure")

    monkeypatch.setattr(admin, "_publish_staged_state_no_clobber", fail_replace)
    with pytest.raises(OSError, match="atomic replace failure"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )
    assert state.read_bytes() == original
    assert backup.read_bytes() == original
    assert [
        path
        for path in tmp_path.glob(".trials.json.trial-admin-*")
        if not path.name.endswith("-lock.sqlite3")
    ] == []


def test_staged_same_content_replacement_before_publish_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    original_publish = admin._publish_staged_state_no_clobber

    def replace_then_publish(
        staged_generation,
        destination: Path,
        *,
        output_sha256: str,
        output_identity,
    ) -> None:
        _replace_bytes_in_subprocess(
            staged_generation.path,
            staged_generation.snapshot.raw,
        )
        original_publish(
            staged_generation,
            destination,
            output_sha256=output_sha256,
            output_identity=output_identity,
        )

    monkeypatch.setattr(
        admin,
        "_publish_staged_state_no_clobber",
        replace_then_publish,
    )
    with pytest.raises(ValueError, match="identity"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )

    assert state.read_bytes() == original
    assert backup.read_bytes() == original
    assert _pending_admin_artifacts(tmp_path) == []


def test_same_content_replacement_after_hardlink_publish_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    original_link = admin.os.link

    def link_then_replace(source, destination, *args, **kwargs):
        result = original_link(source, destination, *args, **kwargs)
        if Path(destination) == state and "claimed" not in Path(source).name:
            _replace_bytes_in_subprocess(state, Path(source).read_bytes())
        return result

    monkeypatch.setattr(admin.os, "link", link_then_replace)
    with pytest.raises(ValueError, match="identity"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )

    assert state.read_bytes() != original
    assert backup.read_bytes() == original
    assert (tmp_path / ".trials.json.trial-admin-commit.json").exists()
    assert len(list(tmp_path.glob(".trials.json.trial-admin-claimed-*"))) == 1
    assert len(_pending_admin_artifacts(tmp_path)) == 3


def test_hardlink_unsupported_restores_original_without_pending_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    original_link = admin.os.link

    def fail_staged_link(source, destination, *args, **kwargs):
        if Path(destination) == state and "claimed" not in Path(source).name:
            raise OSError(errno.ENOTSUP, "synthetic hardlink unsupported")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(admin.os, "link", fail_staged_link)
    with pytest.raises(OSError, match="hardlink unsupported"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )

    assert state.read_bytes() == original
    assert backup.read_bytes() == original
    assert _pending_admin_artifacts(tmp_path) == []


def test_parent_fsync_failure_after_claim_restores_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _load_admin()
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    original_fsync_parent = admin._fsync_parent_directory
    calls = 0

    def fail_second_parent_fsync(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EINVAL, "synthetic parent fsync unsupported")
        original_fsync_parent(path)

    monkeypatch.setattr(
        admin,
        "_fsync_parent_directory",
        fail_second_parent_fsync,
    )
    with pytest.raises(OSError, match="parent fsync unsupported"):
        admin.main(
            _apply_args(
                state=state,
                backup=backup,
                expected_sha256=hashlib.sha256(original).hexdigest(),
                cutover=now + 1,
            )
        )

    assert state.read_bytes() == original
    assert backup.read_bytes() == original
    assert _pending_admin_artifacts(tmp_path) == []


def _wait_for_path(path: Path, process: subprocess.Popen, *, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"helper exited before seam: rc={process.returncode}; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        time.sleep(0.01)
    process.kill()
    stdout, stderr = process.communicate()
    raise AssertionError(
        f"helper did not reach seam; stdout={stdout!r}; stderr={stderr!r}"
    )


def _pending_admin_artifacts(root: Path) -> list[Path]:
    return [
        path
        for path in root.glob(".trials.json.trial-admin-*")
        if not path.name.endswith("-lock.sqlite3")
    ]


def _seam_helper_code(*, phase: str) -> str:
    if phase == "pause_before_publish":
        wrapper = """
        ready = Path(os.environ["BIDKING_TEST_READY"])
        release = Path(os.environ["BIDKING_TEST_RELEASE"])
        ready.write_text("ready", encoding="ascii")
        deadline = time.monotonic() + 15
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("test seam release timed out")
            time.sleep(0.01)
        return original(
            staged,
            state,
            output_sha256=output_sha256,
            output_identity=output_identity,
        )
        """
    elif phase == "crash_before_publish":
        wrapper = """
        Path(os.environ["BIDKING_TEST_READY"]).write_text("ready", encoding="ascii")
        os._exit(79)
        """
    elif phase == "crash_after_publish":
        wrapper = """
        original(
            staged,
            state,
            output_sha256=output_sha256,
            output_identity=output_identity,
        )
        Path(os.environ["BIDKING_TEST_READY"]).write_text("ready", encoding="ascii")
        os._exit(81)
        """
    else:
        raise ValueError("unknown test seam phase")
    return f"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, {str(SCRIPTS)!r})
import trial_state_admin as admin

original = admin._publish_staged_state_no_clobber
def paused_publish(
    staged,
    state,
    *,
    output_sha256,
    output_identity,
):
{wrapper}

admin._publish_staged_state_no_clobber = paused_publish
try:
    code = admin.main(json.loads(os.environ["BIDKING_TEST_ARGS"]))
except Exception:
    code = 2
raise SystemExit(code)
"""


def _start_seam_helper(
    *,
    args: list[str],
    ready: Path,
    release: Path,
    phase: str,
) -> subprocess.Popen:
    environment = dict(os.environ)
    environment.update(
        {
            "BIDKING_TEST_ARGS": json.dumps(args),
            "BIDKING_TEST_READY": str(ready),
            "BIDKING_TEST_RELEASE": str(release),
        }
    )
    return subprocess.Popen(
        [sys.executable, "-c", _seam_helper_code(phase=phase)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )


@pytest.mark.parametrize("mode", ("migrate", "downgrade"))
@pytest.mark.parametrize("same_contents", (False, True))
def test_noncooperating_process_cannot_overwrite_in_final_commit_seam(
    tmp_path: Path,
    same_contents: bool,
    mode: str,
) -> None:
    now = int(time.time())
    state = tmp_path / "trials.json"
    records = (
        {"old": now - 60}
        if mode == "migrate"
        else {
            "old": {
                "schema_version": 2,
                "start": now - 60,
                "window_seconds": 3 * 3600,
                "last_server_now": now,
            }
        }
    )
    original = _write_json(state, records)
    external = (
        original
        if same_contents
        else _write_json(tmp_path / "external.json", {"external": now})
    )
    (tmp_path / "external.json").unlink(missing_ok=True)
    backup = tmp_path / "backup.json"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    args = _apply_args(
        state=state,
        backup=backup,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        mode=mode,
        cutover=now + 1 if mode == "migrate" else None,
    )
    process = _start_seam_helper(
        args=args,
        ready=ready,
        release=release,
        phase="pause_before_publish",
    )
    _wait_for_path(ready, process)
    assert not state.exists()
    writer_environment = dict(os.environ)
    writer_environment.update(
        {
            "BIDKING_TEST_EXTERNAL_STATE": str(state),
            "BIDKING_TEST_EXTERNAL_HEX": external.hex(),
        }
    )
    writer = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; from pathlib import Path; "
                "Path(os.environ['BIDKING_TEST_EXTERNAL_STATE']).write_bytes("
                "bytes.fromhex(os.environ['BIDKING_TEST_EXTERNAL_HEX']))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=writer_environment,
    )
    assert writer.returncode == 0, writer.stderr
    release.write_text("release", encoding="ascii")
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode == 2, (stdout, stderr)
    assert state.read_bytes() == external
    assert backup.read_bytes() == original
    assert (tmp_path / ".trials.json.trial-admin-commit.json").exists()
    assert len(list(tmp_path.glob(".trials.json.trial-admin-claimed-*"))) == 1
    assert len(_pending_admin_artifacts(tmp_path)) == 3

    retry = subprocess.run(
        [sys.executable, str(ADMIN_PATH), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert retry.returncode == 2
    assert "failed: ValueError" in retry.stderr
    assert state.read_bytes() == external


def test_crash_after_claim_is_recovered_from_durable_commit_intent(
    tmp_path: Path,
) -> None:
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    ready = tmp_path / "ready"
    release = tmp_path / "unused-release"
    args = _apply_args(
        state=state,
        backup=backup,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        cutover=now + 1,
    )
    process = _start_seam_helper(
        args=args,
        ready=ready,
        release=release,
        phase="crash_before_publish",
    )
    _wait_for_path(ready, process)
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode == 79, (stdout, stderr)
    assert not state.exists()
    assert backup.read_bytes() == original
    assert (tmp_path / ".trials.json.trial-admin-commit.json").exists()

    recovered = subprocess.run(
        [sys.executable, str(ADMIN_PATH), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 0, recovered.stderr
    receipt = json.loads(recovered.stdout)
    assert receipt["mode"] == "recovered_pending_commit"
    assert receipt["input_sha256"] == hashlib.sha256(original).hexdigest()
    assert receipt["output_sha256"] == hashlib.sha256(state.read_bytes()).hexdigest()
    migrated = json.loads(state.read_text(encoding="utf-8"))
    assert migrated["old"]["window_seconds"] == 12 * 3600
    assert backup.read_bytes() == original
    assert not (tmp_path / ".trials.json.trial-admin-commit.json").exists()
    assert list(tmp_path.glob(".trials.json.trial-admin-claimed-*")) == []
    assert _pending_admin_artifacts(tmp_path) == []


def test_recovery_rejects_same_content_staged_generation_replacement(
    tmp_path: Path,
) -> None:
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    ready = tmp_path / "ready"
    args = _apply_args(
        state=state,
        backup=backup,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        cutover=now + 1,
    )
    process = _start_seam_helper(
        args=args,
        ready=ready,
        release=tmp_path / "unused-release",
        phase="crash_before_publish",
    )
    _wait_for_path(ready, process)
    process.communicate(timeout=15)
    assert process.returncode == 79

    staged = [
        path
        for path in _pending_admin_artifacts(tmp_path)
        if "claimed" not in path.name
        and "lock.sqlite3" not in path.name
        and not path.name.endswith("-commit.json")
    ]
    assert len(staged) == 1
    staged_payload = staged[0].read_bytes()
    _replace_bytes_in_subprocess(staged[0], staged_payload)

    recovered = subprocess.run(
        [sys.executable, str(ADMIN_PATH), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 2
    assert "failed: ValueError" in recovered.stderr
    assert not state.exists()
    assert backup.read_bytes() == original
    assert (tmp_path / ".trials.json.trial-admin-commit.json").exists()
    assert len(_pending_admin_artifacts(tmp_path)) == 3


def test_crash_after_no_clobber_publish_is_finalized_without_reapplying(
    tmp_path: Path,
) -> None:
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    ready = tmp_path / "ready"
    args = _apply_args(
        state=state,
        backup=backup,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        cutover=now + 1,
    )
    process = _start_seam_helper(
        args=args,
        ready=ready,
        release=tmp_path / "unused-release",
        phase="crash_after_publish",
    )
    _wait_for_path(ready, process)
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode == 81, (stdout, stderr)
    committed = state.read_bytes()
    assert committed != original
    assert (tmp_path / ".trials.json.trial-admin-commit.json").exists()

    recovered = subprocess.run(
        [sys.executable, str(ADMIN_PATH), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 0, recovered.stderr
    receipt = json.loads(recovered.stdout)
    assert receipt["mode"] == "recovered_pending_commit"
    assert receipt["output_sha256"] == hashlib.sha256(committed).hexdigest()
    assert state.read_bytes() == committed
    assert backup.read_bytes() == original
    assert _pending_admin_artifacts(tmp_path) == []


def test_recovery_rejects_same_content_external_replacement_after_publish_crash(
    tmp_path: Path,
) -> None:
    now = int(time.time())
    state = tmp_path / "trials.json"
    original = _write_json(state, {"old": now - 60})
    backup = tmp_path / "backup.json"
    ready = tmp_path / "ready"
    args = _apply_args(
        state=state,
        backup=backup,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        cutover=now + 1,
    )
    process = _start_seam_helper(
        args=args,
        ready=ready,
        release=tmp_path / "unused-release",
        phase="crash_after_publish",
    )
    _wait_for_path(ready, process)
    process.communicate(timeout=15)
    assert process.returncode == 81

    committed = state.read_bytes()
    replacement = tmp_path / "same-output-new-identity.json"
    replacement.write_bytes(committed)
    os.replace(replacement, state)

    recovered = subprocess.run(
        [sys.executable, str(ADMIN_PATH), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 2
    assert "failed: ValueError" in recovered.stderr
    assert state.read_bytes() == committed
    assert backup.read_bytes() == original
    assert (tmp_path / ".trials.json.trial-admin-commit.json").exists()
