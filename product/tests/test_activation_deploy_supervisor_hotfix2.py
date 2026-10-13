from __future__ import annotations

import hashlib
import dataclasses
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Callable, Sequence

import pytest


def _history_command_fixture(tmp_path):
    import test_activation_state_aware_deploy_0_3_1 as state_tests
    config, ops = _fixture(tmp_path)
    history = tmp_path / "history-private" / "history.sqlite3"
    history.parent.mkdir(mode=0o700)
    command = json.loads(config.command_file.read_text(encoding="utf-8"))
    upload = tmp_path / "upload-private" / "upload.sqlite3"
    pet = tmp_path / "pet-private" / "pet.sqlite3"
    command[command.index("--upload-identity-db-file") + 1] = str(upload)
    command[command.index("--pet-usage-db-file") + 1] = str(pet)
    command.extend(["--history-index-db-file", str(history)])
    _write_private_json(config.command_file, command)
    text = state_tests._unit_text(config.deployed_dir.parent / "trials.json", cutover=1785438705,
                                  upload_identity_db=upload, pet_usage_db=pet,
                                  upload_mode="disabled", upload_epoch=0, activation_epoch=0)
    text = text.replace("bidking-upload-identity\n", "bidking-upload-identity bidking-sample-index\n")
    config.candidate_unit_file.write_text(text.rstrip() + " --history-index-db-file " + history.as_posix() + "\n", encoding="utf-8")
    return dataclasses.replace(config, history_index_db_file=history), ops


def test_history_deployment_supervisor_fingerprint_and_exact_child(tmp_path):
    config, _ops = _history_command_fixture(tmp_path)
    command = supervisor._load_command(config)
    assert command[command.index("--history-index-db-file") + 1] == str(config.history_index_db_file)
    assert supervisor._config_fingerprint(config) != supervisor._config_fingerprint(
        dataclasses.replace(config, history_index_db_file=None))


@pytest.mark.parametrize("mutation", ["missing_child_flag", "wrong_child_path", "unselected", "move_existing"])
def test_history_deployment_supervisor_rejects_torn_child(tmp_path, mutation):
    config, _ops = _history_command_fixture(tmp_path)
    command = json.loads(config.command_file.read_text(encoding="utf-8"))
    index = command.index("--history-index-db-file")
    if mutation == "missing_child_flag":
        del command[index:index + 2]
    elif mutation == "wrong_child_path":
        command[index + 1] = str(tmp_path / "wrong.sqlite3")
    elif mutation == "unselected":
        config = dataclasses.replace(config, history_index_db_file=None)
    else:
        config.unit_file.write_text(config.candidate_unit_file.read_text(encoding="utf-8").replace(
            config.history_index_db_file.as_posix(), (tmp_path / "previous.sqlite3").as_posix()), encoding="utf-8")
    _write_private_json(config.command_file, command)
    with pytest.raises(supervisor.SupervisorError):
        supervisor._load_command(config)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SUPERVISOR_PATH = SCRIPTS / "activation_deploy_supervisor.py"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "activation_deploy_supervisor_test",
    SUPERVISOR_PATH,
)
assert spec is not None and spec.loader is not None
supervisor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = supervisor
spec.loader.exec_module(supervisor)

OLD_ABSENT_MODULES = (
    "activation_http_e2ee.py",
    "upload_lease.py",
    "upload_identity_store.py",
    "upload_identity_db_admin.py",
)


class FakeOps:
    def __init__(self) -> None:
        self.running = True
        self.health_fails = False
        self.stop_count = 0
        self.start_count = 0
        self.reload_count = 0
        self.health_count = 0
        self.preflight_count = 0

    def preflight(self, config: supervisor.Config) -> None:
        assert config.supervisor_service == "bidking-deploy-supervisor-hotfix2-v5"
        self.preflight_count += 1

    def stop(self, service: str) -> None:
        assert service == "bidking-collector"
        self.running = False
        self.stop_count += 1

    def start(self, service: str) -> None:
        assert service == "bidking-collector"
        self.running = True
        self.start_count += 1

    def daemon_reload(self) -> None:
        self.reload_count += 1

    def health(self, *, port: int) -> None:
        assert port == 18900
        self.health_count += 1
        if not self.running or self.health_fails:
            raise supervisor.SupervisorError("injected health failure")


class FakeRunner:
    def __init__(
        self,
        action: Callable[[Sequence[str]], int] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.action = action
        self.error = error
        self.calls = 0

    def run(
        self,
        command: Sequence[str],
        *,
        state_dir: Path,
        timeout: float,
        deploy_tool_descriptor: int,
    ) -> int:
        assert state_dir.is_dir()
        assert timeout == 420.0
        metadata = os.fstat(deploy_tool_descriptor)
        assert stat.S_ISREG(metadata.st_mode)
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.action is None:
            return 0
        return self.action(command)


def _unit_text(*, upload_epoch: int = 0, activation_epoch: int = 0) -> str:
    return (
        "[Service]\n"
        "ExecStart=/usr/bin/python3 /srv/bidking/scripts/activation_server.py "
        f"--upload-security-epoch {upload_epoch} "
        f"--min-client-security-epoch {activation_epoch}\n"
    )


def _write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)


def _fixture(tmp_path: Path) -> tuple[supervisor.Config, FakeOps]:
    repo = tmp_path / "repo"
    deployed = repo / "scripts"
    candidate = tmp_path / "candidate"
    deployed.mkdir(parents=True)
    candidate.mkdir()
    for name in supervisor.ACTIVATION_GROUP:
        (deployed / name).write_bytes(f"old:{name}\n".encode("ascii"))
        (candidate / name).write_bytes(f"candidate:{name}\n".encode("ascii"))
        if os.name == "posix":
            os.chmod(candidate / name, 0o600)
    unit_file = tmp_path / "systemd" / "bidking-collector.service"
    unit_file.parent.mkdir()
    unit_file.write_text(_unit_text(), encoding="utf-8")
    candidate_unit = candidate / "bidking-collector.service"
    candidate_unit.write_text(_unit_text(), encoding="utf-8")
    if os.name == "posix":
        os.chmod(candidate_unit, 0o600)
    deploy_tool = candidate / "activation_state_aware_deploy.py"
    shutil.copyfile(SCRIPTS / "activation_state_aware_deploy.py", deploy_tool)
    if os.name == "posix":
        os.chmod(
            deploy_tool,
            stat.S_IMODE(
                (SCRIPTS / "activation_state_aware_deploy.py").stat().st_mode
            ),
        )
    server_update = candidate / "server_update.sh"
    server_update.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    state_file = repo / "trials.json"
    state_file.write_text('{"keep":"old"}\n', encoding="utf-8")
    upload_db = tmp_path / "upload_identity.sqlite3"
    upload_db.write_bytes(b"upload-private-state")
    pet_db = tmp_path / "pet_usage.sqlite3"
    pet_db.write_bytes(b"pet-private-state")
    child_backup = tmp_path / "child-backup"
    child_lock = tmp_path / "child-lock.sqlite3"
    supervisor_unit = tmp_path / "systemd" / "bidking-deploy-supervisor-hotfix2-v5.service"
    command_file = tmp_path / "apply-command.json"
    command = [
        str(Path(sys.executable).resolve()),
        str(deploy_tool.resolve()),
        "--apply",
        "--repo-dir",
        str(repo.resolve()),
        "--candidate-dir",
        str(candidate.resolve()),
        "--server-update-script",
        str(server_update.resolve()),
        "--unit-file",
        str(unit_file.resolve()),
        "--candidate-unit-file",
        str(candidate_unit.resolve()),
        "--state-file",
        str(state_file.resolve()),
        "--trial-state-mode",
        "existing-v2",
        "--upload-identity-db-file",
        str(upload_db.resolve()),
        "--pet-usage-db-file",
        str(pet_db.resolve()),
        "--upload-identity-mode",
        "disabled",
        "--upload-security-epoch",
        "0",
        "--upload-lease-seconds",
        "300",
        "--min-client-security-epoch",
        "0",
        "--min-client-version",
        "0.3.1",
        "--v2-min-version",
        "0.2.7",
        "--pet-usage-lease-seconds",
        "15",
        "--service-user",
        "ubuntu",
        "--service-group",
        "ubuntu",
        "--working-directory",
        str(repo.resolve()),
        "--service",
        "bidking-collector",
        "--port",
        "18900",
        "--cutover-at",
        "1785438705",
        "--new-trial-hours",
        "3",
        "--grandfather-hours",
        "12",
        "--legacy-trial-hours",
        "12",
        "--backup-root",
        str(child_backup.resolve()),
        "--lock-file",
        str(child_lock.resolve()),
        "--python-executable",
        str(Path(sys.executable).resolve()),
    ]
    _write_private_json(command_file, command)
    config = supervisor.Config(
        operation_id="activation-http-e2ee-hotfix2-v5",
        state_dir=(tmp_path / "supervisor-state").resolve(),
        deployed_dir=deployed.resolve(),
        candidate_dir=candidate.resolve(),
        unit_file=unit_file.resolve(),
        candidate_unit_file=candidate_unit.resolve(),
        deploy_tool=deploy_tool.resolve(),
        command_file=command_file.resolve(),
        child_backup_root=child_backup.resolve(),
        child_lock_file=child_lock.resolve(),
        supervisor_service="bidking-deploy-supervisor-hotfix2-v5",
        supervisor_unit_file=supervisor_unit.resolve(),
        python_executable=str(Path(sys.executable).resolve()),
        service="bidking-collector",
        port=18900,
        child_timeout_seconds=420.0,
    )
    supervisor_command = [
        str(Path(sys.executable).resolve()),
        str(SUPERVISOR_PATH.resolve()),
        "--operation-id",
        config.operation_id,
        "--state-dir",
        str(config.state_dir),
        "--deployed-dir",
        str(config.deployed_dir),
        "--candidate-dir",
        str(config.candidate_dir),
        "--unit-file",
        str(config.unit_file),
        "--candidate-unit-file",
        str(config.candidate_unit_file),
        "--deploy-tool",
        str(config.deploy_tool),
        "--command-file",
        str(config.command_file),
        "--child-backup-root",
        str(config.child_backup_root),
        "--child-lock-file",
        str(config.child_lock_file),
        "--supervisor-service",
        config.supervisor_service,
        "--supervisor-unit-file",
        str(config.supervisor_unit_file),
        "--python-executable",
        config.python_executable,
        "--service",
        config.service,
        "--port",
        str(config.port),
        "--child-timeout-seconds",
        str(config.child_timeout_seconds),
    ]
    assert all(not any(character.isspace() for character in item) for item in supervisor_command)
    supervisor_unit.write_text(
        "[Unit]\nDescription=BidKing one-shot deployment supervisor\n"
        "StartLimitBurst=3\nStartLimitIntervalSec=15min\n"
        "[Service]\nType=oneshot\nUser=root\nGroup=root\nUMask=0077\n"
        "KillMode=control-group\nOOMPolicy=stop\nOOMScoreAdjust=-900\n"
        "Restart=on-failure\nRestartSec=2s\nRemainAfterExit=no\n"
        "TimeoutStartSec=15min\nExecStart="
        + " ".join(supervisor_command)
        + "\n",
        encoding="utf-8",
    )
    return config, FakeOps()


def _member_supervisor_fixture(tmp_path: Path):
    import test_activation_state_aware_deploy_0_3_1 as member_tests
    member_root = tmp_path / "member"
    member_root.mkdir()
    member = member_tests._member_config(member_root)
    config, ops = _fixture(tmp_path / "supervisor")
    tool = member.candidate_dir / "activation_state_aware_deploy.py"
    shutil.copy2(SCRIPTS / "activation_state_aware_deploy.py", tool)
    config = dataclasses.replace(config, deployed_dir=member.deployed_dir,
                                  candidate_dir=member.candidate_dir, unit_file=member.unit_file,
                                  candidate_unit_file=member.candidate_unit_file,
                                  deploy_tool=tool, membership_store_file=member.membership_store_file)
    command = json.loads(config.command_file.read_text(encoding="utf-8"))
    replacements = {
        "--repo-dir": member.repo_dir, "--candidate-dir": member.candidate_dir,
        "--unit-file": member.unit_file, "--candidate-unit-file": member.candidate_unit_file,
        "--state-file": member.state_file, "--working-directory": member.working_directory,
        "--pet-usage-db-file": member.pet_usage_db_file,
        "--upload-identity-db-file": member.upload_identity_db_file, "--cutover-at": member.cutover_at,
    }
    command[1] = str(tool.resolve())
    for key, value in replacements.items():
        command[command.index(key) + 1] = str(value)
    command.extend(["--membership-store-file", str(member.membership_store_file)])
    _write_private_json(config.command_file, command)
    return config, ops, member, member_tests


def test_member036_supervisor_command_exact_path_and_fingerprint(tmp_path):
    config, ops, member, _tests = _member_supervisor_fixture(tmp_path)
    command = supervisor._load_command(config)
    assert command[command.index("--membership-store-file") + 1] == str(member.membership_store_file)
    other = dataclasses.replace(config, membership_store_file=member.membership_store_file.with_name("other.sqlite3"))
    assert supervisor._config_fingerprint(other) != supervisor._config_fingerprint(config)
    with pytest.raises(supervisor.SupervisorError):
        supervisor._load_command(other)
    command[command.index("--membership-store-file") + 1] += "-wrong"
    _write_private_json(config.command_file, command)
    with pytest.raises(supervisor.SupervisorError):
        supervisor._load_command(config)


@pytest.mark.parametrize("edge", ["nonzero", "restart", "compatible"])
def test_member036_supervisor_recovery_keeps_grants_or_stops_not_old_success(tmp_path, monkeypatch, edge):
    config, ops, member, tests = _member_supervisor_fixture(tmp_path)
    service, a, b, now = tests._member_purchase(member, monkeypatch)
    if edge != "compatible":
        (config.deployed_dir / "activation_core.py").write_text("ACTIVATION_CORE_API_VERSION = 5\n", encoding="utf-8")
    original_db = member.membership_store_file.read_bytes()
    snapshot = service.membership_store.read("synthetic-deploy-A", now=now)
    def install(_command):
        for name in supervisor.ACTIVATION_GROUP:
            shutil.copyfile(config.candidate_dir / name, config.deployed_dir / name)
        shutil.copyfile(config.candidate_unit_file, config.unit_file)
        return 1
    runner = FakeRunner(action=install)
    if edge == "restart":
        state = supervisor._prepare(config)
        supervisor._write_phase(config, state, "apply_started", child_returncode=None)
        install([])
    if edge == "compatible":
        assert supervisor.supervise(config, ops, runner) == "old_recovered"
        assert ops.running and ops.start_count == 1
    else:
        with pytest.raises(supervisor.MembershipRecoveryRequired):
            supervisor.supervise(config, ops, runner)
        assert not ops.running and ops.start_count == 0
        assert json.loads((config.state_dir / "operation.json").read_text())["phase"] == "membership_recovery_required"
        assert (config.deployed_dir / "membership_card_store.py").exists()
        assert (config.deployed_dir / "activation_core.py").read_bytes() == (config.candidate_dir / "activation_core.py").read_bytes()
        with pytest.raises(supervisor.MembershipRecoveryRequired):
            supervisor.supervise(config, ops, runner)
        if edge == "restart":
            assert runner.calls == 0
    assert member.membership_store_file.read_bytes() == original_db
    assert service.membership_store.read("synthetic-deploy-A", now=now) == snapshot


def test_member036_supervisor_rejects_prior_operation_schema_not_silent_migration(tmp_path):
    config, ops = _fixture(tmp_path)
    state = supervisor._prepare(config)
    state["schema_version"] = 5
    _write_private_json(config.state_dir / "operation.json", state)
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="state identity"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0


def test_member036_completed_operation_does_not_cache_missing_authority_as_healthy(tmp_path):
    config, ops, member, tests = _member_supervisor_fixture(tmp_path)
    def install(_command):
        for name in supervisor.ACTIVATION_GROUP:
            shutil.copyfile(config.candidate_dir / name, config.deployed_dir / name)
        shutil.copyfile(config.candidate_unit_file, config.unit_file)
        return 0
    runner = FakeRunner(action=install)
    assert supervisor.supervise(config, ops, runner) == "candidate_deployed"
    member.membership_store_file.unlink()
    with pytest.raises(supervisor.MembershipRecoveryRequired):
        supervisor.supervise(config, ops, runner)
    assert not ops.running and ops.start_count == 0


def _install_candidate(config: supervisor.Config, ops: FakeOps) -> None:
    for name in supervisor.ACTIVATION_GROUP:
        shutil.copyfile(config.candidate_dir / name, config.deployed_dir / name)
        if os.name == "posix":
            os.chmod(config.deployed_dir / name, supervisor.DEPLOYED_FILE_MODE)
    shutil.copyfile(config.candidate_unit_file, config.unit_file)
    if os.name == "posix":
        os.chmod(config.unit_file, supervisor.DEPLOYED_FILE_MODE)
    ops.running = True


def _remove_old_modules(config: supervisor.Config) -> None:
    for name in OLD_ABSENT_MODULES:
        (config.deployed_dir / name).unlink()


def _assert_deployed_matches(config: supervisor.Config, *, candidate: bool) -> None:
    state = supervisor._load_state(config)
    source_root = config.candidate_dir if candidate else config.state_dir / "files"
    for name in supervisor.ACTIVATION_GROUP:
        target = config.deployed_dir / name
        if not candidate and state["old_files"][name]["present"] is False:
            assert not target.exists()
        else:
            assert target.read_bytes() == (source_root / name).read_bytes()
    source_unit = (
        config.candidate_unit_file
        if candidate
        else config.state_dir / "files" / "systemd-unit"
    )
    assert config.unit_file.read_bytes() == source_unit.read_bytes()


def test_success_accepts_only_byte_exact_healthy_candidate(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)

    def apply(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        return 0

    runner = FakeRunner(apply)
    assert supervisor.supervise(config, ops, runner) == "candidate_deployed"
    assert runner.calls == 1
    assert ops.preflight_count == 1
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (0, 0, 0)
    _assert_deployed_matches(config, candidate=True)
    state = supervisor._load_state(config)
    assert state["phase"] == "candidate_deployed"
    assert state["child_returncode"] == 0


def test_upgrade_prepare_and_success_preserve_old_absence_contract(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    _remove_old_modules(config)

    def apply(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        return 0

    assert supervisor.supervise(config, ops, FakeRunner(apply)) == "candidate_deployed"
    state = supervisor._load_state(config)
    assert state["schema_version"] == 6
    for name, entry in state["candidate_files"].items():
        source_path = (
            config.candidate_unit_file
            if name == "systemd-unit"
            else config.candidate_dir / name
        )
        assert set(entry) == {
            "sha256",
            "size",
            "source_mode",
            "deployed_mode",
        }
        assert entry["source_mode"] == supervisor._hash_regular(source_path)[2]
        assert entry["deployed_mode"] == supervisor.DEPLOYED_FILE_MODE
    for name in OLD_ABSENT_MODULES:
        assert state["old_files"][name] == {"present": False}
        assert not (config.state_dir / "files" / name).exists()
        assert (config.deployed_dir / name).read_bytes() == (
            config.candidate_dir / name
        ).read_bytes()
    assert list((config.state_dir / "removed").iterdir()) == []


def test_nonzero_child_removes_only_candidate_files_that_were_old_absent(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    _remove_old_modules(config)

    def fail_after_install(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        ops.running = False
        return 23

    assert (
        supervisor.supervise(config, ops, FakeRunner(fail_after_install))
        == "old_recovered"
    )
    _assert_deployed_matches(config, candidate=False)
    assert list((config.state_dir / "removed").iterdir()) == []
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (1, 1, 1)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics")
def test_candidate_source_mode_and_deployed_mode_are_independently_bound(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)

    def apply(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        return 0

    assert supervisor.supervise(config, ops, FakeRunner(apply)) == "candidate_deployed"
    state = supervisor._load_state(config)
    for name, entry in state["candidate_files"].items():
        assert entry["source_mode"] == 0o600
        assert entry["deployed_mode"] == 0o644
        target = config.unit_file if name == "systemd-unit" else config.deployed_dir / name
        assert stat.S_IMODE(target.stat(follow_symlinks=False).st_mode) == 0o644


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics")
def test_wrong_deployed_mode_is_not_accepted_as_candidate_success(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)

    def apply_with_wrong_mode(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        first = config.deployed_dir / supervisor.ACTIVATION_GROUP[0]
        os.chmod(first, 0o600)
        return 0

    assert (
        supervisor.supervise(config, ops, FakeRunner(apply_with_wrong_mode))
        == "old_recovered"
    )
    _assert_deployed_matches(config, candidate=False)


def test_apply_started_recovery_resumes_an_already_quarantined_new_module(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    _remove_old_modules(config)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    _install_candidate(config, ops)
    first = OLD_ABSENT_MODULES[0]
    supervisor._quarantine_candidate_for_old_absence(
        target=config.deployed_dir / first,
        quarantine=config.state_dir / "removed" / first,
        candidate_entry=state["candidate_files"][first],
    )
    ops.running = False

    runner = FakeRunner()
    assert supervisor.supervise(config, ops, runner) == "old_recovered"
    assert runner.calls == 0
    _assert_deployed_matches(config, candidate=False)
    assert list((config.state_dir / "removed").iterdir()) == []


def test_old_absent_recovery_refuses_foreign_file_without_removing_it(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    _remove_old_modules(config)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    target = config.deployed_dir / OLD_ABSENT_MODULES[0]
    target.write_bytes(b"foreign-live-file")
    ops.running = False

    with pytest.raises(supervisor.SupervisorError, match="is not the candidate"):
        supervisor.supervise(config, ops, FakeRunner())
    assert target.read_bytes() == b"foreign-live-file"
    assert not (config.state_dir / "removed" / target.name).exists()
    assert (ops.stop_count, ops.start_count) == (1, 0)


def test_old_absent_recovery_restores_a_path_replaced_during_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ops = _fixture(tmp_path)
    _remove_old_modules(config)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    _install_candidate(config, ops)
    target = config.deployed_dir / OLD_ABSENT_MODULES[0]
    quarantine = config.state_dir / "removed" / target.name
    real_rename = supervisor.os.rename
    injected = False

    def replace_then_rename(source: object, destination: object) -> None:
        nonlocal injected
        if Path(source) == target and Path(destination) == quarantine and not injected:
            injected = True
            replacement = target.with_name(f".{target.name}.foreign")
            replacement.write_bytes(b"foreign-race-file")
            os.replace(replacement, target)
        real_rename(source, destination)

    monkeypatch.setattr(supervisor.os, "rename", replace_then_rename)
    ops.running = False
    with pytest.raises(supervisor.SupervisorError, match="changed before quarantine"):
        supervisor.supervise(config, ops, FakeRunner())
    assert injected
    assert target.read_bytes() == b"foreign-race-file"
    assert not quarantine.exists()
    assert ops.start_count == 0


def test_old_absent_symlink_fails_closed_without_moving_or_unlinking(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    _remove_old_modules(config)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    target = config.deployed_dir / OLD_ABSENT_MODULES[0]
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"outside")
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    ops.running = False

    with pytest.raises(supervisor.SupervisorError, match="cannot be opened"):
        supervisor.supervise(config, ops, FakeRunner())
    assert target.is_symlink()
    assert outside.read_bytes() == b"outside"
    assert ops.start_count == 0


def test_old_absent_path_appearing_during_prepare_blocks_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ops = _fixture(tmp_path)
    _remove_old_modules(config)
    target = config.deployed_dir / OLD_ABSENT_MODULES[0]
    real_snapshot = supervisor._snapshot_files

    def snapshot_then_appear(paths: dict[str, Path]) -> dict[str, dict[str, object]]:
        result = real_snapshot(paths)
        target.write_bytes(b"appeared-during-prepare")
        return result

    monkeypatch.setattr(supervisor, "_snapshot_files", snapshot_then_appear)
    with pytest.raises(supervisor.SupervisorError, match="changed during preparation"):
        supervisor._prepare(config)
    assert target.read_bytes() == b"appeared-during-prepare"
    assert not (config.state_dir / "operation.json").exists()


def test_old_absent_prepare_requires_same_filesystem_for_atomic_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ops = _fixture(tmp_path)
    _remove_old_modules(config)
    removed_dir = config.state_dir / "removed"
    real_stat = supervisor._stat_no_follow

    def cross_device_removed(path: Path) -> object:
        metadata = real_stat(path)
        if path == removed_dir:
            assert metadata is not None
            return types.SimpleNamespace(
                st_mode=metadata.st_mode,
                st_dev=metadata.st_dev + 1,
            )
        return metadata

    monkeypatch.setattr(supervisor, "_stat_no_follow", cross_device_removed)
    with pytest.raises(supervisor.SupervisorError, match="not atomic"):
        supervisor._prepare(config)
    assert not (config.state_dir / "operation.json").exists()


@pytest.mark.parametrize(
    "invalid_entry",
    (
        {"present": 0},
        {"present": False, "sha256": "0" * 64},
        {"present": True},
    ),
)
def test_old_absent_state_shape_is_exact_and_strongly_typed(
    tmp_path: Path,
    invalid_entry: dict[str, object],
) -> None:
    config, _ops = _fixture(tmp_path)
    _remove_old_modules(config)
    supervisor._prepare(config)
    operation_file = config.state_dir / "operation.json"
    state = json.loads(operation_file.read_text(encoding="utf-8"))
    state["old_files"][OLD_ABSENT_MODULES[0]] = invalid_entry
    _write_private_json(operation_file, state)
    with pytest.raises(
        supervisor.SupervisorError,
        match="old operation|old absent|presence",
    ):
        supervisor._load_state(config)


def test_old_file_table_rejects_mapping_subclasses_without_magic_calls() -> None:
    class MagicDict(dict[object, object]):
        calls = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            type(self).calls += 1
            raise RuntimeError("must not iterate")

        def __contains__(self, key: object) -> bool:
            type(self).calls += 1
            raise RuntimeError("must not inspect")

    with pytest.raises(supervisor.SupervisorError, match="table is invalid"):
        supervisor._validate_old_file_table(MagicDict())
    assert MagicDict.calls == 0

    valid = {
        name: {
            "present": True,
            "sha256": "0" * 64,
            "size": 0,
            "mode": 0o644,
        }
        for name in (*supervisor.ACTIVATION_GROUP, "systemd-unit")
    }
    valid[supervisor.ACTIVATION_GROUP[0]] = MagicDict()
    with pytest.raises(supervisor.SupervisorError, match="entry is invalid"):
        supervisor._validate_old_file_table(valid)
    assert MagicDict.calls == 0


def test_old_absent_state_rejects_an_unexpected_private_backup(tmp_path: Path) -> None:
    config, _ops = _fixture(tmp_path)
    _remove_old_modules(config)
    supervisor._prepare(config)
    backup = config.state_dir / "files" / OLD_ABSENT_MODULES[0]
    backup.write_bytes(b"unexpected-backup")
    if os.name == "posix":
        os.chmod(backup, 0o600)
    with pytest.raises(supervisor.SupervisorError, match="unexpected backup"):
        supervisor._load_state(config)


def test_old_absent_state_rejects_foreign_or_unexpected_quarantine(
    tmp_path: Path,
) -> None:
    config, _ops = _fixture(tmp_path)
    _remove_old_modules(config)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    removed_dir = config.state_dir / "removed"
    foreign = removed_dir / OLD_ABSENT_MODULES[0]
    foreign.write_bytes(b"foreign-quarantine")
    with pytest.raises(supervisor.SupervisorError, match="does not match the candidate"):
        supervisor._load_state(config)
    foreign.unlink()
    (removed_dir / "unexpected.py").write_bytes(b"unexpected")
    with pytest.raises(supervisor.SupervisorError, match="unexpected entry"):
        supervisor._load_state(config)


def test_removed_candidate_is_only_valid_during_apply_started(tmp_path: Path) -> None:
    config, _ops = _fixture(tmp_path)
    _remove_old_modules(config)
    supervisor._prepare(config)
    name = OLD_ABSENT_MODULES[0]
    shutil.copyfile(config.candidate_dir / name, config.state_dir / "removed" / name)
    with pytest.raises(supervisor.SupervisorError, match="not empty outside recovery"):
        supervisor._load_state(config)


def test_old_absent_upgrade_still_requires_every_candidate_file(tmp_path: Path) -> None:
    config, _ops = _fixture(tmp_path)
    _remove_old_modules(config)
    (config.candidate_dir / OLD_ABSENT_MODULES[0]).unlink()
    with pytest.raises(supervisor.SupervisorError, match="cannot be opened"):
        supervisor._prepare(config)
    assert not (config.state_dir / "operation.json").exists()


def test_nonzero_child_restores_old_pair_without_rolling_back_authority(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    authority = config.deployed_dir.parent / "trials.json"

    def fail_after_partial_install(_command: Sequence[str]) -> int:
        first = supervisor.ACTIVATION_GROUP[0]
        shutil.copyfile(config.candidate_dir / first, config.deployed_dir / first)
        shutil.copyfile(config.candidate_unit_file, config.unit_file)
        authority.write_text('{"keep":"new-live-write"}\n', encoding="utf-8")
        ops.running = False
        return 17

    assert (
        supervisor.supervise(config, ops, FakeRunner(fail_after_partial_install))
        == "old_recovered"
    )
    _assert_deployed_matches(config, candidate=False)
    assert json.loads(authority.read_text(encoding="utf-8")) == {
        "keep": "new-live-write"
    }
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (1, 1, 1)


def test_runner_exception_after_partial_install_recovers_old_pair(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)

    class RaisingRunner(FakeRunner):
        def run(
            self,
            command: Sequence[str],
            *,
            state_dir: Path,
            timeout: float,
            deploy_tool_descriptor: int,
        ) -> int:
            self.calls += 1
            assert stat.S_ISREG(os.fstat(deploy_tool_descriptor).st_mode)
            first = supervisor.ACTIVATION_GROUP[0]
            shutil.copyfile(config.candidate_dir / first, config.deployed_dir / first)
            ops.running = False
            raise OSError("injected runner failure")

    assert supervisor.supervise(config, ops, RaisingRunner()) == "old_recovered"
    _assert_deployed_matches(config, candidate=False)
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (1, 1, 1)


def test_restarted_supervisor_never_retries_apply_and_recovers(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)
    supervisor._load_command(config)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    first = supervisor.ACTIVATION_GROUP[0]
    shutil.copyfile(config.candidate_dir / first, config.deployed_dir / first)
    ops.running = False
    runner = FakeRunner()
    assert supervisor.supervise(config, ops, runner) == "old_recovered"
    assert runner.calls == 0
    _assert_deployed_matches(config, candidate=False)


def test_recovery_does_not_depend_on_consumed_command_or_candidate_inputs(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    config.command_file.unlink()
    for path in config.candidate_dir.iterdir():
        path.unlink()
    config.candidate_dir.rmdir()
    ops.running = False
    runner = FakeRunner()
    assert supervisor.supervise(config, ops, runner) == "old_recovered"
    assert runner.calls == 0
    _assert_deployed_matches(config, candidate=False)


def test_restarted_supervisor_restarts_even_when_old_bytes_look_healthy(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    runner = FakeRunner()
    assert supervisor.supervise(config, ops, runner) == "old_recovered"
    assert runner.calls == 0
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (1, 1, 1)


@pytest.mark.parametrize(
    "drift",
    (
        "command",
        "deploy_tool",
        "old",
        "candidate",
        "candidate_mode",
        "child_identity",
    ),
)
def test_prepared_restart_revalidates_every_apply_input_before_mutation(
    tmp_path: Path,
    drift: str,
) -> None:
    config, ops = _fixture(tmp_path)
    supervisor._prepare(config)
    if drift == "command":
        command = json.loads(config.command_file.read_text(encoding="utf-8"))
        command[command.index("--cutover-at") + 1] = "1785438706"
        _write_private_json(config.command_file, command)
        expected = "prepared deploy command changed"
    elif drift == "deploy_tool":
        config.deploy_tool.write_text("# replaced deploy tool\n", encoding="utf-8")
        expected = "deploy tool identity differ"
    elif drift == "old":
        (config.deployed_dir / supervisor.ACTIVATION_GROUP[0]).write_bytes(b"old-drift")
        expected = "prepared old deployment bytes changed"
    elif drift == "candidate":
        (config.candidate_dir / supervisor.ACTIVATION_GROUP[0]).write_bytes(
            b"candidate-drift"
        )
        expected = "prepared candidate bytes changed"
    elif drift == "candidate_mode":
        if os.name != "posix":
            pytest.skip("POSIX mode semantics")
        os.chmod(config.candidate_dir / supervisor.ACTIVATION_GROUP[0], 0o644)
        expected = "prepared candidate bytes changed"
    else:
        config.child_lock_file.write_bytes(b"claimed")
        expected = "already consumed"
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match=expected):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    state = supervisor._load_state(config)
    assert state["phase"] == "prepared"
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (0, 0, 0)


def test_child_deploy_tool_must_equal_the_supervisor_runtime_module(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    config.deploy_tool.write_text("# divergent child deploy tool\n", encoding="utf-8")
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="deploy tool identity differ"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (0, 0, 0)


def test_matching_replaced_module_and_child_cannot_rebind_loaded_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ops = _fixture(tmp_path)
    replacement = tmp_path / "replacement_state_aware.py"
    replacement.write_text("# same replacement bytes\n", encoding="utf-8")
    shutil.copyfile(replacement, config.deploy_tool)
    monkeypatch.setattr(supervisor, "_STATE_AWARE_MODULE_FILE", replacement)
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="runtime deploy module changed"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (0, 0, 0)


def test_prepared_child_same_bytes_path_replacement_is_rejected(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    state = supervisor._prepare(config)
    before = dict(state["deploy_tool_identity"]["child"])
    replacement = config.deploy_tool.with_suffix(".replacement")
    shutil.copyfile(config.deploy_tool, replacement)
    os.chmod(replacement, int(before["mode"]))
    os.replace(replacement, config.deploy_tool)
    after = supervisor._persistent_file_identity(config.deploy_tool)
    assert after[:3] == (
        before["sha256"],
        before["size"],
        before["mode"],
    )
    assert after[3:] != (before["device"], before["inode"])
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="prepared child deploy tool changed"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert supervisor._load_state(config)["phase"] == "prepared"
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (0, 0, 0)


def test_runtime_module_same_bytes_path_swap_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ops = _fixture(tmp_path)
    replacement = tmp_path / "same_bytes_state_aware.py"
    shutil.copyfile(SCRIPTS / "activation_state_aware_deploy.py", replacement)
    monkeypatch.setattr(supervisor, "_STATE_AWARE_MODULE_FILE", replacement)
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="runtime deploy module changed"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()


def test_state_aware_source_loader_ignores_timestamp_pyc_semantics(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "activation_state_aware_deploy.py"
    source_a = b"ACTIVATION_GROUP = ('A.py',)\nclass RealOps:\n    pass\n"
    source_b = b"ACTIVATION_GROUP = ('B.py',)\nclass RealOps:\n    pass\n"
    assert len(source_a) == len(source_b)
    module_path.write_bytes(source_a)
    fixed_time = int(time.time()) - 120
    os.utime(module_path, (fixed_time, fixed_time))
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    command = [
        sys.executable,
        "-c",
        "import activation_state_aware_deploy as m; print(m.ACTIVATION_GROUP[0])",
    ]
    first = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert first.stdout.strip() == "A.py"
    assert list((tmp_path / "__pycache__").glob("activation_state_aware_deploy.*.pyc"))
    module_path.write_bytes(source_b)
    os.utime(module_path, (fixed_time, fixed_time))
    stale = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert stale.stdout.strip() == "A.py"
    loaded, identity = supervisor._load_state_aware_source_exact(module_path)
    assert loaded.ACTIVATION_GROUP == ("B.py",)
    assert identity[0] == hashlib.sha256(source_b).hexdigest()
    assert identity[1] == len(source_b)


def test_state_aware_source_loader_ignores_preloaded_standard_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_path = tmp_path / "activation_state_aware_deploy.py"
    source_b = b"ACTIVATION_GROUP = ('B.py',)\nclass RealOps:\n    pass\n"
    module_path.write_bytes(source_b)
    poison = types.ModuleType("activation_state_aware_deploy")
    poison.ACTIVATION_GROUP = ("A.py",)
    poison.RealOps = object
    poison.__file__ = str(module_path)
    monkeypatch.setitem(sys.modules, "activation_state_aware_deploy", poison)
    loaded, identity = supervisor._load_state_aware_source_exact(module_path)
    assert loaded is not poison
    assert loaded.ACTIVATION_GROUP == ("B.py",)
    assert identity[0] == hashlib.sha256(source_b).hexdigest()


def test_child_deploy_tool_symlink_is_rejected_before_state_or_runner(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)
    target = tmp_path / "real_state_aware.py"
    shutil.copyfile(config.deploy_tool, target)
    config.deploy_tool.unlink()
    try:
        config.deploy_tool.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlink is unavailable: {exc}")
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="cannot be opened"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()


def test_prepared_state_persists_exact_runtime_and_child_file_identity(
    tmp_path: Path,
) -> None:
    config, _ops = _fixture(tmp_path)
    state = supervisor._prepare(config)
    assert state["schema_version"] == 6
    identity = state["deploy_tool_identity"]
    assert identity["runtime_module_path"] == str(
        supervisor._STATE_AWARE_MODULE_FILE
    )
    runtime = identity["runtime"]
    child = identity["child"]
    assert set(runtime) == {"sha256", "size", "mode", "device", "inode"}
    assert set(child) == {"sha256", "size", "mode", "device", "inode"}
    assert tuple(runtime[key] for key in ("sha256", "size", "mode")) == tuple(
        child[key] for key in ("sha256", "size", "mode")
    )
    assert (runtime["device"], runtime["inode"]) != (
        child["device"],
        child["inode"],
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_mode", True),
        ("source_mode", -1),
        ("deployed_mode", True),
        ("deployed_mode", 0o600),
    ),
)
def test_candidate_state_rejects_malformed_source_or_deployed_mode(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    config, _ops = _fixture(tmp_path)
    supervisor._prepare(config)
    operation_file = config.state_dir / "operation.json"
    state = json.loads(operation_file.read_text(encoding="utf-8"))
    state["candidate_files"][supervisor.ACTIVATION_GROUP[0]][field] = value
    _write_private_json(operation_file, state)
    with pytest.raises(supervisor.SupervisorError, match="candidate operation file metadata"):
        supervisor._load_state(config)


def test_candidate_state_shape_is_exact_and_rejects_mapping_subclasses() -> None:
    expected = (*supervisor.ACTIVATION_GROUP, "systemd-unit")
    valid = {
        name: {
            "sha256": "0" * 64,
            "size": 0,
            "source_mode": 0o600,
            "deployed_mode": supervisor.DEPLOYED_FILE_MODE,
        }
        for name in expected
    }
    first = expected[0]
    valid[first] = {**valid[first], "mode": 0o600}
    with pytest.raises(supervisor.SupervisorError, match="entry is invalid"):
        supervisor._validate_file_table(valid)

    class MagicDict(dict[object, object]):
        calls = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            type(self).calls += 1
            raise RuntimeError("must not iterate")

        def __contains__(self, key: object) -> bool:
            type(self).calls += 1
            raise RuntimeError("must not inspect")

    with pytest.raises(supervisor.SupervisorError, match="table is invalid"):
        supervisor._validate_file_table(MagicDict())
    assert MagicDict.calls == 0


@pytest.mark.parametrize("field", ("sha256", "size", "mode", "device", "inode"))
def test_prepared_state_rejects_malformed_child_identity_without_mutation(
    tmp_path: Path,
    field: str,
) -> None:
    config, ops = _fixture(tmp_path)
    supervisor._prepare(config)
    operation_file = config.state_dir / "operation.json"
    value = json.loads(operation_file.read_text(encoding="utf-8"))
    value["deploy_tool_identity"]["child"][field] = True
    _write_private_json(operation_file, value)
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="identity record is invalid"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (0, 0, 0)


@pytest.mark.skipif(os.name != "posix", reason="requires /proc descriptor execution")
def test_real_runner_executes_the_pinned_descriptor_after_path_replacement(
    tmp_path: Path,
) -> None:
    script = tmp_path / "deploy.py"
    marker = tmp_path / "marker.txt"
    script.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('A', encoding='ascii')\n",
        encoding="utf-8",
    )
    descriptor, _identity = supervisor._open_hashed_regular(script)
    os.lseek(descriptor, 0, os.SEEK_SET)
    replacement = tmp_path / "replacement.py"
    replacement.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('B', encoding='ascii')\n",
        encoding="utf-8",
    )
    os.replace(replacement, script)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    try:
        assert (
            supervisor.RealRunner().run(
                [sys.executable, str(script)],
                state_dir=state_dir,
                timeout=30.0,
                deploy_tool_descriptor=descriptor,
            )
            == 0
        )
    finally:
        os.close(descriptor)
    assert marker.read_text(encoding="ascii") == "A"


def test_zero_exit_with_candidate_mismatch_recovers_old_pair(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)

    def incomplete_success(_command: Sequence[str]) -> int:
        first = supervisor.ACTIVATION_GROUP[0]
        shutil.copyfile(config.candidate_dir / first, config.deployed_dir / first)
        ops.running = True
        return 0

    assert (
        supervisor.supervise(config, ops, FakeRunner(incomplete_success))
        == "old_recovered"
    )
    _assert_deployed_matches(config, candidate=False)


def test_zero_exit_with_unhealthy_candidate_recovers_old_runtime(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)
    health_calls = 0

    def health(*, port: int) -> None:
        nonlocal health_calls
        assert port == 18900
        health_calls += 1
        if health_calls == 1:
            raise supervisor.SupervisorError("candidate is unhealthy")
        assert ops.running

    ops.health = health  # type: ignore[method-assign]

    def unhealthy_success(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        return 0

    assert (
        supervisor.supervise(config, ops, FakeRunner(unhealthy_success))
        == "old_recovered"
    )
    _assert_deployed_matches(config, candidate=False)
    assert health_calls == 2
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (1, 1, 1)


def test_completed_candidate_reentry_never_runs_apply_again(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)

    def apply(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        return 0

    assert supervisor.supervise(config, ops, FakeRunner(apply)) == "candidate_deployed"
    second_runner = FakeRunner()
    assert supervisor.supervise(config, ops, second_runner) == "candidate_deployed"
    assert second_runner.calls == 0
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (0, 0, 0)


def test_completed_recovery_reentry_never_runs_apply_again(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)

    def fail(_command: Sequence[str]) -> int:
        ops.running = False
        return 1

    assert supervisor.supervise(config, ops, FakeRunner(fail)) == "old_recovered"
    second_runner = FakeRunner()
    assert supervisor.supervise(config, ops, second_runner) == "old_recovered"
    assert second_runner.calls == 0
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (1, 1, 1)


def test_completed_candidate_detects_later_byte_drift_without_recovery(
    tmp_path: Path,
) -> None:
    config, ops = _fixture(tmp_path)

    def apply(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        return 0

    assert supervisor.supervise(config, ops, FakeRunner(apply)) == "candidate_deployed"
    (config.deployed_dir / supervisor.ACTIVATION_GROUP[0]).write_bytes(b"drift")
    with pytest.raises(supervisor.SupervisorError, match="candidate deployment bytes changed"):
        supervisor.supervise(config, ops, FakeRunner())
    assert (ops.stop_count, ops.start_count, ops.reload_count) == (0, 0, 0)


def test_epoch_change_blocks_before_state_or_runner(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)
    config.candidate_unit_file.write_text(
        _unit_text(upload_epoch=1),
        encoding="utf-8",
    )
    command = json.loads(config.command_file.read_text(encoding="utf-8"))
    command[command.index("--upload-security-epoch") + 1] = "1"
    _write_private_json(config.command_file, command)
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="epoch change"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()


@pytest.mark.parametrize(
    "raw_epoch",
    ("-1", "+1", "1.0", "true", "\u0661", "2147483648"),
)
def test_unit_epoch_rejects_noncanonical_or_oversized_values(
    tmp_path: Path,
    raw_epoch: str,
) -> None:
    unit = tmp_path / "candidate.service"
    unit.write_text(
        "[Service]\nExecStart=/usr/bin/python3 server.py "
        f"--upload-security-epoch {raw_epoch}\n",
        encoding="utf-8",
    )
    with pytest.raises(supervisor.SupervisorError, match="epoch is invalid"):
        supervisor._unit_epoch(unit, "--upload-security-epoch")


@pytest.mark.parametrize(
    ("option", "replacement"),
    [
        ("--repo-dir", "wrong-repo"),
        ("--candidate-dir", "wrong-candidate"),
        ("--service", "wrong-service"),
        ("--port", "18901"),
        ("--backup-root", "wrong-backup"),
        ("--lock-file", "wrong-lock"),
    ],
)
def test_command_identity_mismatch_blocks_before_state(
    tmp_path: Path,
    option: str,
    replacement: str,
) -> None:
    config, ops = _fixture(tmp_path)
    command = json.loads(config.command_file.read_text(encoding="utf-8"))
    command[command.index(option) + 1] = replacement
    _write_private_json(config.command_file, command)
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="identity option mismatch"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()


@pytest.mark.parametrize(
    "mutation",
    ("missing_apply", "migration_mode", "duplicate_option"),
)
def test_command_mode_must_be_one_exact_non_migrating_apply(
    tmp_path: Path,
    mutation: str,
) -> None:
    config, ops = _fixture(tmp_path)
    command = json.loads(config.command_file.read_text(encoding="utf-8"))
    if mutation == "missing_apply":
        command.remove("--apply")
    elif mutation == "migration_mode":
        command[command.index("--trial-state-mode") + 1] = "legacy-migrate"
    else:
        command.extend(("--port", "18900"))
    _write_private_json(config.command_file, command)
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()


def test_unknown_child_option_is_rejected_before_state_or_apply(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)
    command = json.loads(config.command_file.read_text(encoding="utf-8"))
    command.extend(("--future-unsafe-option", "1"))
    _write_private_json(config.command_file, command)
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="option set is invalid"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()


@pytest.mark.parametrize("consumed", ("backup", "lock"))
def test_consumed_child_operation_identity_blocks_before_apply(
    tmp_path: Path,
    consumed: str,
) -> None:
    config, ops = _fixture(tmp_path)
    target = config.child_backup_root if consumed == "backup" else config.child_lock_file
    if consumed == "backup":
        target.mkdir()
    else:
        target.write_bytes(b"consumed")
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="already consumed"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert not config.state_dir.exists()


def test_corrupt_private_backup_fails_closed_without_running_apply(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)
    state = supervisor._prepare(config)
    supervisor._write_phase(config, state, "apply_started", child_returncode=None)
    first = supervisor.ACTIVATION_GROUP[0]
    backup = config.state_dir / "files" / first
    backup.write_bytes(b"corrupt-backup")
    if os.name == "posix":
        os.chmod(backup, 0o600)
    (config.deployed_dir / first).write_bytes(b"corrupt-live")
    ops.running = False
    runner = FakeRunner()
    with pytest.raises(supervisor.SupervisorError, match="backup digest mismatch"):
        supervisor.supervise(config, ops, runner)
    assert runner.calls == 0
    assert ops.start_count == 0


def test_bool_runner_return_is_not_accepted_as_zero(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)

    def bool_return(_command: Sequence[str]) -> int:
        _install_candidate(config, ops)
        return False  # type: ignore[return-value]

    assert supervisor.supervise(config, ops, FakeRunner(bool_return)) == "old_recovered"
    _assert_deployed_matches(config, candidate=False)


def test_private_snapshot_contains_only_code_group_and_unit(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)
    state = supervisor._prepare(config)
    assert set(state["old_files"]) == {
        *supervisor.ACTIVATION_GROUP,
        "systemd-unit",
    }
    assert {path.name for path in (config.state_dir / "files").iterdir()} == {
        *supervisor.ACTIVATION_GROUP,
        "systemd-unit",
    }
    assert "trials.json" not in state["old_files"]
    assert ops.stop_count == 0


def test_state_file_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    config, ops = _fixture(tmp_path)
    supervisor._prepare(config)
    operation_file = config.state_dir / "operation.json"
    operation_file.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
    if os.name == "posix":
        os.chmod(operation_file, 0o600)
    with pytest.raises(supervisor.SupervisorError, match="keys are invalid"):
        supervisor.supervise(config, ops, FakeRunner())


def test_backup_files_are_private_regular_files(tmp_path: Path) -> None:
    config, _ops = _fixture(tmp_path)
    supervisor._prepare(config)
    for path in (config.state_dir / "files").iterdir():
        metadata = path.stat(follow_symlinks=False)
        assert stat.S_ISREG(metadata.st_mode)
        if os.name == "posix":
            assert stat.S_IMODE(metadata.st_mode) == 0o600


def test_supervisor_systemd_boundary_requires_group_kill_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ops = _fixture(tmp_path)
    monkeypatch.setattr(supervisor.os, "getpid", lambda: 4242)
    values = {
        "MainPID": "4242",
        "User": "root",
        "Group": "root",
        "Type": "oneshot",
        "KillMode": "control-group",
        "OOMPolicy": "stop",
        "OOMScoreAdjust": "-900",
        "Restart": "on-failure",
        "RestartUSec": "2s",
        "RemainAfterExit": "no",
        "TimeoutStartUSec": "15min",
        "StartLimitBurst": "3",
        "StartLimitIntervalUSec": "15min",
        "FragmentPath": str(config.supervisor_unit_file),
        "DropInPaths": "",
    }
    supervisor._validate_supervisor_properties(config, values)
    for key, invalid in (
        ("MainPID", "4243"),
        ("User", "ubuntu"),
        ("Group", "ubuntu"),
        ("Type", "simple"),
        ("KillMode", "process"),
        ("OOMPolicy", "continue"),
        ("OOMScoreAdjust", "0"),
        ("Restart", "no"),
        ("RestartUSec", "100ms"),
        ("RemainAfterExit", "yes"),
        ("TimeoutStartUSec", "infinity"),
        ("StartLimitBurst", "0"),
        ("StartLimitIntervalUSec", "infinity"),
        ("DropInPaths", "/etc/systemd/system/example.conf"),
    ):
        bad = dict(values)
        bad[key] = invalid
        with pytest.raises(supervisor.SupervisorError, match="boundary is unsafe"):
            supervisor._validate_supervisor_properties(config, bad)


@pytest.mark.parametrize(
    "invalid_kind",
    ("oversized", "bool", "bytes_subclass", "bytearray", "memoryview", "magic"),
)
def test_file_reader_rejects_non_exact_or_oversized_chunks_without_magic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_kind: str,
) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"bounded-reader\n")
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    real_read = supervisor.os.read
    magic_calls = 0

    class BytesSubclass(bytes):
        pass

    class MagicChunk:
        def __bool__(self) -> bool:
            nonlocal magic_calls
            magic_calls += 1
            raise RuntimeError("must not truth-test")

        def __len__(self) -> int:
            nonlocal magic_calls
            magic_calls += 1
            raise RuntimeError("must not measure")

    values: dict[str, object] = {
        "oversized": b"x" * (supervisor.CHUNK_SIZE + 1),
        "bool": False,
        "bytes_subclass": BytesSubclass(b"bounded-reader\n"),
        "bytearray": bytearray(b"bounded-reader\n"),
        "memoryview": memoryview(b"bounded-reader\n"),
        "magic": MagicChunk(),
    }

    def invalid_read(descriptor: int, requested: int):
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != target_identity:
            return real_read(descriptor, requested)
        return values[invalid_kind]

    monkeypatch.setattr(supervisor.os, "read", invalid_read)
    with pytest.raises(supervisor.SupervisorError, match="read boundary is invalid"):
        supervisor._hash_regular(target)
    assert magic_calls == 0


def test_file_reader_retries_eintr_and_accepts_short_exact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.bin"
    payload = b"short-exact-byte-chunks\n"
    target.write_bytes(payload)
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    real_read = supervisor.os.read
    calls = 0

    def interrupted_then_short(descriptor: int, requested: int) -> bytes:
        nonlocal calls
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != target_identity:
            return real_read(descriptor, requested)
        calls += 1
        if calls == 1:
            raise InterruptedError("synthetic EINTR")
        return real_read(descriptor, min(requested, 3))

    monkeypatch.setattr(supervisor.os, "read", interrupted_then_short)
    digest, size, _mode = supervisor._hash_regular(target)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert size == len(payload)
    assert calls > 3


def test_child_oom_score_reset_failure_is_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(*_args: object, **_kwargs: object) -> int:
        raise OSError("injected procfs denial")

    monkeypatch.setattr(supervisor.Path, "write_text", fail_write)
    with pytest.raises(OSError, match="procfs denial"):
        supervisor._set_child_oom_score()


@pytest.mark.skipif(os.name != "posix", reason="production runner is POSIX-only")
def test_real_runner_timeout_kills_the_complete_child_process_group(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    grandchild_pid = tmp_path / "grandchild.pid"
    body = (
        "import pathlib,subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(grandchild_pid)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )
    deploy_tool = tmp_path / "timeout_deploy.py"
    deploy_tool.write_text(body, encoding="utf-8")
    descriptor, _identity = supervisor._open_hashed_regular(deploy_tool)
    try:
        result = supervisor.RealRunner().run(
            [sys.executable, str(deploy_tool)],
            state_dir=state_dir,
            timeout=1.0,
            deploy_tool_descriptor=descriptor,
        )
    finally:
        os.close(descriptor)
    assert result == -int(supervisor.signal.SIGKILL)
    pid = int(grandchild_pid.read_text(encoding="ascii"))
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        proc_stat = Path(f"/proc/{pid}/stat")
        if not proc_stat.exists():
            break
        fields = proc_stat.read_text(encoding="ascii").split()
        if len(fields) >= 3 and fields[2] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail("timed-out deploy grandchild remained live")
    for name in ("child.stdout", "child.stderr"):
        assert stat.S_IMODE((state_dir / name).stat().st_mode) == 0o600


@pytest.mark.parametrize("outcome", ("candidate_deployed", "old_recovered"))
def test_main_treats_both_verified_terminal_outcomes_as_systemd_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
) -> None:
    config, _ops = _fixture(tmp_path)
    monkeypatch.setattr(supervisor.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(supervisor, "_config_from_args", lambda _args: config)
    monkeypatch.setattr(supervisor, "supervise", lambda *_args: outcome)
    argv: list[str] = []
    for option in (
        "--operation-id",
        "--state-dir",
        "--deployed-dir",
        "--candidate-dir",
        "--unit-file",
        "--candidate-unit-file",
        "--deploy-tool",
        "--command-file",
        "--child-backup-root",
        "--child-lock-file",
        "--supervisor-service",
        "--supervisor-unit-file",
    ):
        argv.extend((option, "placeholder"))
    assert supervisor.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "outcome": outcome}


def test_supervisor_unit_passes_systemd_syntax_gate_when_available(
    tmp_path: Path,
) -> None:
    config, _ops = _fixture(tmp_path)
    systemd_analyze = shutil.which("systemd-analyze")
    if os.name != "posix" or systemd_analyze is None:
        pytest.skip("systemd-analyze is unavailable on this host")
    completed = subprocess.run(
        [systemd_analyze, "verify", str(config.supervisor_unit_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert completed.returncode == 0, completed.stderr
