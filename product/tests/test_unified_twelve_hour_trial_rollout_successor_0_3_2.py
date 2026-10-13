from __future__ import annotations

import dataclasses
import importlib.util
import json
import shlex
import shutil
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
for path in (SCRIPTS, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import activation_state_aware_deploy as deploy  # noqa: E402
import test_activation_state_aware_deploy_0_3_1 as base  # noqa: E402

# Reuse the mature fixture, but keep all product calls and exception identities on
# this test module's exact imported source.
base.deploy = deploy


THREE_HOURS = 3 * 3600
TWELVE_HOURS = 12 * 3600


def _record(*, start: int, window: int, last: int) -> dict:
    return {
        "schema_version": 2,
        "start": start,
        "window_seconds": window,
        "last_server_now": last,
    }


def _unit_policy(path: Path) -> tuple[float, float, int]:
    exec_lines = [
        line.split("=", 1)[1]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("ExecStart=") and line != "ExecStart="
    ]
    assert len(exec_lines) == 1
    tokens = shlex.split(exec_lines[0])

    def value(option: str) -> str:
        positions = [index for index, token in enumerate(tokens) if token == option]
        assert len(positions) == 1 and positions[0] + 1 < len(tokens)
        return tokens[positions[0] + 1]

    return (
        float(value("--trial-hours")),
        float(value("--trial-grandfather-hours")),
        int(value("--trial-grandfather-cutover-at")),
    )


def _load_core(path: Path):
    spec = importlib.util.spec_from_file_location(
        f"_unified_rollout_core_{time.time_ns()}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UnifiedRuntimeOps(base.FakeOps):
    """Exercise each start with the actual restored core and unit trial options."""

    def start(self, service: str) -> None:
        assert service == self.config.service
        self.running = True
        self.start_count += 1
        core_path = self.config.deployed_dir / "activation_core.py"
        core_text = core_path.read_text(encoding="utf-8")
        if "ACTIVATION_CORE_API_VERSION = 5" not in core_text:
            raise AssertionError("unified rollout fixture requires API5")
        self.candidate_started = True
        core = _load_core(core_path)
        trial_hours, grandfather_hours, cutover_at = _unit_policy(
            self.config.unit_file
        )
        state = json.loads(self.config.state_file.read_text(encoding="utf-8"))
        now = int(time.time())
        ok, reason, lease = core.grant_trial(
            "candidate-new",
            trials=state,
            trial_hours=trial_hours,
            grandfather_cutover_at=cutover_at,
            grandfather_trial_hours=grandfather_hours,
            now=now,
        )
        if (ok, reason) != (True, "ok") or lease is None:
            raise AssertionError(f"restored trial runtime rejected state: {reason}")
        self.candidate_new_expiry = lease["exp"]
        self.config.state_file.write_text(
            json.dumps(state, sort_keys=True) + "\n", encoding="utf-8"
        )


class InjectAfterStopOps(UnifiedRuntimeOps):
    def stop(self, service: str) -> None:
        super().stop(service)
        if self.stop_count != 1:
            return
        now = int(time.time())
        self.config.state_file.write_text(
            json.dumps(
                {
                    "late-three-hour": _record(
                        start=now - 60,
                        window=THREE_HOURS,
                        last=now,
                    )
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def _unified_clone(tmp_path: Path, *, state: dict) -> tuple[deploy.Config, dict]:
    config, _ops, evidence = base._clone(tmp_path)
    shutil.copy2(SCRIPTS / "activation_core.py", config.deployed_dir / "activation_core.py")

    # Model the current production policy: API5 runtime, explicit 3/12 unit.
    old_unit = config.candidate_unit_file.read_text(encoding="utf-8")
    assert "--trial-hours 3" in old_unit
    config.unit_file.write_text(old_unit, encoding="utf-8")

    # Candidate is exact 12/12 while retaining the cutover and authority paths.
    config.candidate_unit_file.write_text(
        old_unit.replace("--trial-hours 3", "--trial-hours 12", 1),
        encoding="utf-8",
    )
    config.state_file.write_text(
        json.dumps(state, sort_keys=True) + "\n", encoding="utf-8"
    )
    config = dataclasses.replace(
        config,
        new_trial_hours=12.0,
        grandfather_hours=12.0,
        legacy_trial_hours=12.0,
        trial_state_mode="existing-v2",
    )
    evidence = dict(evidence)
    evidence["old_core"] = (config.deployed_dir / "activation_core.py").read_bytes()
    evidence["old_unit"] = config.unit_file.read_bytes()
    evidence["old_state"] = config.state_file.read_bytes()
    return config, evidence


def _find_cause(exc: BaseException, text: str) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if text in str(current):
            return True
        current = current.__cause__
    return False


def test_nonempty_three_hour_ledger_is_rejected_before_service_stop(
    tmp_path: Path,
) -> None:
    now = int(time.time())
    config, evidence = _unified_clone(
        tmp_path,
        state={
            "old": _record(start=now - 60, window=THREE_HOURS, last=now)
        },
    )
    ops = UnifiedRuntimeOps(config)

    with pytest.raises(deploy.DeploymentError, match="offline normalization"):
        deploy.preflight(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]
    assert config.unit_file.read_bytes() == evidence["old_unit"]


@pytest.mark.parametrize(
    "replacement",
    (
        "--trial-hours 3e0",
        "--trial-hours 3 --trial-hours 12",
    ),
)
def test_noncanonical_or_duplicate_installed_policy_fails_closed(
    tmp_path: Path,
    replacement: str,
) -> None:
    config, evidence = _unified_clone(tmp_path, state={})
    current = config.unit_file.read_text(encoding="utf-8")
    config.unit_file.write_text(
        current.replace("--trial-hours 3", replacement, 1),
        encoding="utf-8",
    )
    ops = UnifiedRuntimeOps(config)

    with pytest.raises(
        deploy.DeploymentError,
        match="trial policy|exactly one --trial-hours",
    ):
        deploy.preflight(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_empty_preflight_cannot_hide_three_hour_record_arriving_before_stopped_snapshot(
    tmp_path: Path,
) -> None:
    config, evidence = _unified_clone(tmp_path, state={})
    ops = InjectAfterStopOps(config)

    with pytest.raises(deploy.DeploymentError) as caught:
        deploy.apply(config, ops)

    assert _find_cause(caught.value, "offline normalization")
    assert ops.running is True
    assert ops.candidate_started is True  # rollback restart of the old API5 runtime
    assert config.unit_file.read_bytes() == evidence["old_unit"]
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["late-three-hour"]["window_seconds"] == THREE_HOURS


@pytest.mark.parametrize("failure_stage", ("after_start", "after_health"))
def test_candidate_start_makes_twelve_hour_rollback_policy_monotonic(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    config, evidence = _unified_clone(tmp_path, state={})
    ops = UnifiedRuntimeOps(config, fail_stage=failure_stage)

    with pytest.raises(
        deploy.DeploymentError, match="compatible trial-state authority"
    ):
        deploy.apply(config, ops)

    assert ops.running is True and ops.start_count == 2
    assert (config.deployed_dir / "activation_core.py").read_bytes() == evidence[
        "old_core"
    ]
    assert _unit_policy(config.unit_file) == (12.0, 12.0, config.cutover_at)
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["candidate-new"]["window_seconds"] == TWELVE_HOURS
    assert state["candidate-new"]["start"] + TWELVE_HOURS == ops.candidate_new_expiry


def test_restored_runtime_verifier_uses_actual_three_twelve_unit_policy(
    tmp_path: Path,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    start = config.cutover_at
    record = _record(
        start=start,
        window=TWELVE_HOURS,
        last=max(int(time.time()), start),
    )
    config.state_file.write_text(
        json.dumps({"candidate-new": record}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        deploy.DeploymentError, match="restored unit trial policy rejects"
    ):
        deploy._verify_restored_v2_authority(
            config,
            expected_expiries={"candidate-new": record["start"] + TWELVE_HOURS},
        )


@pytest.mark.parametrize("expired", (False, True))
def test_restored_runtime_verifier_accepts_exact_twelve_twelve_policy(
    tmp_path: Path,
    expired: bool,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    start = (
        config.cutover_at - 13 * 3600
        if expired
        else config.cutover_at
    )
    record = _record(
        start=start,
        window=TWELVE_HOURS,
        last=(
            max(int(time.time()), start + TWELVE_HOURS)
            if expired
            else max(int(time.time()), start)
        ),
    )
    config.state_file.write_text(
        json.dumps({"candidate-new": record}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config.unit_file.write_bytes(config.candidate_unit_file.read_bytes())

    deploy._verify_restored_v2_authority(
        config,
        expected_expiries={"candidate-new": record["start"] + TWELVE_HOURS},
    )


def test_restored_runtime_verifier_probes_dormant_pre_cutover_record_read_only(
    tmp_path: Path,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    record = _record(
        start=config.cutover_at - 3600,
        window=TWELVE_HOURS,
        last=config.cutover_at - 1,
    )
    config.state_file.write_text(
        json.dumps({"dormant": record}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config.unit_file.write_bytes(config.candidate_unit_file.read_bytes())
    before = config.state_file.read_bytes()

    deploy._verify_restored_v2_authority(
        config,
        expected_expiries={"dormant": record["start"] + TWELVE_HOURS},
    )

    assert config.state_file.read_bytes() == before


def test_restored_runtime_verifier_accepts_mixed_pre_cutover_active_and_expired(
    tmp_path: Path,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    records = {
        "active": _record(
            start=config.cutover_at - 3600,
            window=TWELVE_HOURS,
            last=config.cutover_at - 1,
        ),
        "expired": _record(
            start=config.cutover_at - 13 * 3600,
            window=TWELVE_HOURS,
            last=config.cutover_at - 1,
        ),
    }
    config.state_file.write_text(
        json.dumps(records, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config.unit_file.write_bytes(config.candidate_unit_file.read_bytes())
    before = config.state_file.read_bytes()

    deploy._verify_restored_v2_authority(
        config,
        expected_expiries={
            device: record["start"] + TWELVE_HOURS
            for device, record in records.items()
        },
    )

    assert config.state_file.read_bytes() == before


@pytest.mark.parametrize(
    ("window", "error"),
    (
        (THREE_HOURS, "offline normalization"),
        (6 * 3600, "installed unit trial policy rejects"),
    ),
)
def test_unified_existing_v2_preflight_rejects_noncanonical_window_without_writes(
    tmp_path: Path,
    window: int,
    error: str,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    config.unit_file.write_bytes(config.candidate_unit_file.read_bytes())
    now = max(int(time.time()), config.cutover_at)
    config.state_file.write_text(
        json.dumps(
            {"legacy-window": _record(start=now - 60, window=window, last=now)},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    before = config.state_file.read_bytes()
    ops = UnifiedRuntimeOps(config)

    with pytest.raises(deploy.DeploymentError, match=error):
        deploy.preflight(config, ops)

    assert config.state_file.read_bytes() == before
    assert ops.stop_count == ops.start_count == 0


def test_unified_existing_v2_preflight_accepts_dormant_exact_record_read_only(
    tmp_path: Path,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    config.unit_file.write_bytes(config.candidate_unit_file.read_bytes())
    record = _record(
        start=config.cutover_at - 3600,
        window=TWELVE_HOURS,
        last=config.cutover_at - 1,
    )
    config.state_file.write_text(
        json.dumps({"dormant": record}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = config.state_file.read_bytes()
    ops = UnifiedRuntimeOps(config)

    deploy.preflight(config, ops)

    assert config.state_file.read_bytes() == before
    assert ops.stop_count == ops.start_count == 0


def test_rollback_unit_install_is_prebound_to_final_twelve_twelve_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    ops = UnifiedRuntimeOps(config, fail_stage="after_start")
    real_atomic_install = deploy._atomic_install
    installed_unit_policies: list[tuple[float, float, int]] = []

    def guarded_atomic_install(source: Path, target: Path, *, mode: int = 0o644) -> None:
        if target == config.unit_file:
            installed_unit_policies.append(_unit_policy(source))
        real_atomic_install(source, target, mode=mode)

    monkeypatch.setattr(deploy, "_atomic_install", guarded_atomic_install)

    with pytest.raises(
        deploy.DeploymentError, match="compatible trial-state authority"
    ):
        deploy.apply(config, ops)

    assert installed_unit_policies == [
        (12.0, 12.0, config.cutover_at),
        (12.0, 12.0, config.cutover_at),
    ]
    assert ops.running is True
    assert ops.stop_count == 2
    assert ops.start_count == 2


@pytest.mark.parametrize("fail_after_install", (False, True))
def test_rollback_unit_write_failure_never_exposes_three_twelve_or_restarts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_after_install: bool,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    ops = UnifiedRuntimeOps(config, fail_stage="after_start")
    real_atomic_install = deploy._atomic_install
    unit_install_count = 0
    rollback_source_policy: tuple[float, float, int] | None = None

    def failing_atomic_install(source: Path, target: Path, *, mode: int = 0o644) -> None:
        nonlocal unit_install_count, rollback_source_policy
        if target != config.unit_file:
            real_atomic_install(source, target, mode=mode)
            return
        unit_install_count += 1
        if unit_install_count == 1:
            real_atomic_install(source, target, mode=mode)
            return
        rollback_source_policy = _unit_policy(source)
        if fail_after_install:
            real_atomic_install(source, target, mode=mode)
        raise OSError("injected rollback unit install failure")

    monkeypatch.setattr(deploy, "_atomic_install", failing_atomic_install)

    with pytest.raises(
        deploy.DeploymentError,
        match="state-aware recovery did not complete",
    ):
        deploy.apply(config, ops)

    assert rollback_source_policy == (12.0, 12.0, config.cutover_at)
    assert _unit_policy(config.unit_file) == (12.0, 12.0, config.cutover_at)
    assert ops.running is False
    assert ops.start_count == 1


@pytest.mark.parametrize("failure_site", ("target_directory", "unit_directory"))
def test_rollback_fsync_failure_keeps_twelve_twelve_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    config, _evidence = _unified_clone(tmp_path, state={})
    ops = UnifiedRuntimeOps(config, fail_stage="after_start")
    real_fsync_directory = deploy._fsync_directory
    unit_directory_syncs = 0

    def failing_fsync_directory(path: Path) -> None:
        nonlocal unit_directory_syncs
        if failure_site == "target_directory" and path.name == "files":
            raise OSError("injected rollback target directory fsync failure")
        if path == config.unit_file.parent:
            unit_directory_syncs += 1
            if failure_site == "unit_directory" and unit_directory_syncs == 2:
                raise OSError("injected rollback unit directory fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(deploy, "_fsync_directory", failing_fsync_directory)

    with pytest.raises(
        deploy.DeploymentError,
        match="state-aware recovery did not complete",
    ):
        deploy.apply(config, ops)

    assert _unit_policy(config.unit_file) == (12.0, 12.0, config.cutover_at)
    assert ops.running is False
    assert ops.start_count == 1
