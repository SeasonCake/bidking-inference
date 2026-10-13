from __future__ import annotations
# ruff: noqa: E721 - product contract distinguishes exact builtins

import dataclasses
import errno
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest


def _history_config(tmp_path: Path, *, enabled: bool = True):
    """Unit-only synthetic profile, without live consumer copies or DB setup."""
    repo = tmp_path / "repo"
    repo.mkdir()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    history = tmp_path / "history" / "history.sqlite3"
    history.parent.mkdir(mode=0o700)
    upload = tmp_path / "upload" / "upload.sqlite3"
    pet = tmp_path / "pet" / "pet.sqlite3"
    unit = tmp_path / "unit"
    text = _unit_text(repo / "trials.json", cutover=1785438705,
                      upload_identity_db=upload, pet_usage_db=pet,
                      upload_mode="disabled", upload_epoch=0, activation_epoch=0)
    if enabled:
        text = text.replace("bidking-upload-identity\n", "bidking-upload-identity bidking-sample-index\n")
        text = text.rstrip() + " --history-index-db-file " + history.as_posix() + "\n"
    unit.write_text(text, encoding="utf-8")
    return deploy.Config(
        repo_dir=repo, candidate_dir=candidate, server_update_script=candidate / "server_update.sh",
        unit_file=unit, candidate_unit_file=unit, state_file=repo / "trials.json",
        upload_identity_db_file=upload, upload_identity_mode="disabled", upload_security_epoch=0,
        upload_lease_seconds=300, min_client_security_epoch=0, min_client_version="0.3.1",
        v2_min_version="0.2.7", pet_usage_db_file=pet, pet_usage_lease_seconds=15,
        service_user="ubuntu", service_group="ubuntu", working_directory=repo,
        service="bidking-collector", port=18900, cutover_at=1785438705,
        new_trial_hours=3, grandfather_hours=12, legacy_trial_hours=12,
        backup_root=repo / "backup", lock_file=repo / "lock", python_executable=sys.executable,
        history_index_db_file=history if enabled else None,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_history_deployment_unit_selected_or_legacy_omitted(tmp_path, enabled):
    config = _history_config(tmp_path, enabled=enabled)
    contract = deploy._validate_candidate_unit(config)
    paths = {role: path for role, path, _ in contract.protected_paths}
    assert ("history_index_db" in paths) is enabled
    if enabled:
        assert paths["history_index_db"] == config.history_index_db_file
        assert not config.history_index_db_file.exists()  # no implicit initialization


@pytest.mark.parametrize("mutation", ["missing_flag", "wrong_path", "duplicate_flag", "missing_directory", "unselected"])
def test_history_deployment_unit_rejects_torn_selection(tmp_path, mutation):
    config = _history_config(tmp_path)
    text = config.candidate_unit_file.read_text(encoding="utf-8")
    clause = " --history-index-db-file " + config.history_index_db_file.as_posix()
    if mutation == "missing_flag":
        text = text.replace(clause, "")
    elif mutation == "wrong_path":
        text = text.replace(config.history_index_db_file.as_posix(), (tmp_path / "wrong.sqlite3").as_posix())
    elif mutation == "duplicate_flag":
        text = text.rstrip() + clause + "\n"
    elif mutation == "missing_directory":
        text = text.replace(" bidking-sample-index", "")
    else:
        config = dataclasses.replace(config, history_index_db_file=None)
    config.candidate_unit_file.write_text(text, encoding="utf-8")
    with pytest.raises(deploy.DeploymentError):
        deploy._validate_candidate_unit(config)


@pytest.mark.parametrize("role", ["samples", "membership_db", "pet_db", "upload_identity_db"])
def test_history_deployment_rejects_authority_and_raw_collisions(tmp_path, role):
    config = _history_config(tmp_path)
    target = tmp_path / "authority" / "authority.sqlite3"
    target.parent.mkdir()
    config = dataclasses.replace(config, history_index_db_file=target)
    with pytest.raises(deploy.DeploymentError, match="overlaps"):
        deploy._validate_history_location(config, {role: target})
    nested = target.parent / "nested" / "history.sqlite3"
    with pytest.raises(deploy.DeploymentError, match="overlaps"):
        deploy._validate_history_location(dataclasses.replace(config, history_index_db_file=nested), {role: target})


@pytest.mark.parametrize("case", ["legacy", "paired", "missing_helper", "missing_flag_consumer"])
def test_history_deployment_recovery_pair_guard(tmp_path, case):
    root = tmp_path / "old"
    root.mkdir()
    for name in ("sample_collector_server.py", "sample_dashboard.py", "public_stats.py", "my_records.py"):
        (root / name).write_text("# synthetic old consumer\n", encoding="utf-8")
    unit = root / "unit"
    unit.write_text("[Service]\nExecStart=/usr/bin/python3 collector.py\n", encoding="utf-8")
    if case != "legacy":
        (root / "sample_collector_server.py").write_text(
            "import sample_history_index\nparser.add_argument('--history-index-db-file')\n", encoding="utf-8")
        unit.write_text("[Service]\nExecStart=/usr/bin/python3 collector.py --history-index-db-file /private/history.sqlite3\n", encoding="utf-8")
        if case != "missing_helper":
            (root / "sample_history_index.py").write_text("# synthetic paired helper\n", encoding="utf-8")
        if case == "missing_flag_consumer":
            (root / "sample_collector_server.py").write_text("import sample_history_index\n", encoding="utf-8")
    if case in {"legacy", "paired"}:
        deploy.require_history_runtime_recovery(root, unit)
    else:
        with pytest.raises(deploy.DeploymentError, match="history_compatible_recovery_required"):
            deploy.require_history_runtime_recovery(root, unit)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEPLOY_PATH = SCRIPTS / "activation_state_aware_deploy.py"

spec = importlib.util.spec_from_file_location(
    "activation_state_aware_deploy_test",
    DEPLOY_PATH,
)
assert spec is not None and spec.loader is not None
deploy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = deploy
spec.loader.exec_module(deploy)

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import activation_server as activation_server_product  # noqa: E402


# Exact hotfix2-r1 grant_trial semantics used by the production rollback target.
# The product wrapper loads the complete backed-up activation_core.py; this compact
# clone keeps the focused fixture limited to the actual legacy-state consumer.
HOTFIX2_R1_TRIAL_LOADER = """\
from __future__ import annotations
import time
ACTIVATION_CORE_API_VERSION = 3

def _strict_state_timestamp(value, *, now):
    if type(value) is not int or value <= 0 or value > 253402300799:
        raise ValueError("bad timestamp")
    if value > now + 300:
        raise ValueError("future timestamp")
    return value

def grant_trial(device, *, trials, trial_hours=3.0, now=None):
    now = int(time.time()) if now is None else int(now)
    window = int(trial_hours * 3600)
    has_record = device in trials
    rec = trials.get(device)
    if not has_record:
        start = now
        trials[device] = now
    elif isinstance(rec, dict):
        used = rec.get("used")
        if type(used) is not int or used < 0:
            return False, "state_error", None
        start = now - used
        if start <= 0:
            return False, "state_error", None
        trials[device] = start
    else:
        try:
            start = _strict_state_timestamp(rec, now=now)
        except (TypeError, ValueError, OverflowError):
            return False, "state_error", None
    if now >= start + window:
        return False, "trial_expired", None
    exp = start + window
    lease = {
        "uid": f"trial:{device}",
        "device": device,
        "exp": exp,
        "trial": True,
        "plan_kind": "trial",
        "feature_entitlements": {"pet_companion": "none"},
    }
    return True, "ok", lease
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _HealthProbeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert 0.0 < seconds <= 1.0
        self.sleeps.append(seconds)
        self.now += seconds


class _HealthProbeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self._raw = json.dumps(payload).encode("ascii")
        self.read_timeouts: list[float] = []

        class Socket:
            def settimeout(inner_self, timeout: float) -> None:
                self.read_timeouts.append(timeout)

        class Raw:
            _sock = Socket()

        class File:
            raw = Raw()

        self.fp = File()

    def __enter__(self) -> _HealthProbeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def read(self) -> bytes:
        return self._raw


def test_real_health_probe_waits_for_measured_startup_beyond_old_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _HealthProbeClock()
    calls: list[tuple[str, float]] = []
    refused = 21

    class Opener:
        def open(self, url: str, *, timeout: float) -> _HealthProbeResponse:
            nonlocal refused
            calls.append((url, timeout))
            if refused:
                refused -= 1
                raise OSError("synthetic connection refused")
            if url.endswith("/healthz"):
                return _HealthProbeResponse({"ok": True})
            return _HealthProbeResponse({"ok": True, "service": "activation"})

    def build_opener(handler: object) -> Opener:
        assert getattr(handler, "proxies", None) == {}
        return Opener()

    monkeypatch.setattr(deploy, "time", clock)
    monkeypatch.setattr(deploy.urllib.request, "build_opener", build_opener)

    deploy.RealOps().health(port=18900)

    assert clock.now == 21.0
    assert len(calls) == 23
    assert all(0.0 < timeout <= 3.0 for _url, timeout in calls)


def test_real_health_probe_deadline_is_bounded_and_error_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _HealthProbeClock()
    calls: list[tuple[str, float]] = []

    class Opener:
        def open(self, url: str, *, timeout: float) -> _HealthProbeResponse:
            calls.append((url, timeout))
            raise OSError("private-host-canary private-body-canary")

    monkeypatch.setattr(deploy, "time", clock)
    monkeypatch.setattr(deploy, "HEALTH_STARTUP_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(
        deploy.urllib.request,
        "build_opener",
        lambda _handler: Opener(),
    )

    with pytest.raises(deploy.DeploymentError) as caught:
        deploy.RealOps().health(port=18900)

    assert str(caught.value) == (
        "health check failed: /healthz (transport_error)"
    )
    assert "private" not in str(caught.value)
    assert clock.now == 5.0
    assert len(calls) == 5
    assert [timeout for _url, timeout in calls] == [3.0, 3.0, 3.0, 2.0, 1.0]


def test_real_health_probes_share_one_total_startup_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _HealthProbeClock()
    calls: list[tuple[str, float, float]] = []
    health_refusals = 4

    class Opener:
        def open(self, url: str, *, timeout: float) -> _HealthProbeResponse:
            nonlocal health_refusals
            calls.append((url, timeout, clock.now))
            if url.endswith("/healthz"):
                if health_refusals:
                    health_refusals -= 1
                    raise OSError("synthetic connection refused")
                return _HealthProbeResponse({"ok": True})
            raise OSError("synthetic readiness refusal")

    monkeypatch.setattr(deploy, "time", clock)
    monkeypatch.setattr(deploy, "HEALTH_STARTUP_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(
        deploy.urllib.request,
        "build_opener",
        lambda _handler: Opener(),
    )

    with pytest.raises(
        deploy.DeploymentError,
        match=r"^health check failed: /activation-readiness \(transport_error\)$",
    ):
        deploy.RealOps().health(port=18900)

    assert clock.now == 5.0
    assert len(calls) == 6
    assert calls[-1] == (
        "http://127.0.0.1:18900/activation-readiness",
        1.0,
        4.0,
    )


def test_real_health_probe_counts_opener_setup_against_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _HealthProbeClock()
    open_calls = 0

    class Opener:
        def open(self, _url: str, *, timeout: float) -> _HealthProbeResponse:
            nonlocal open_calls
            del timeout
            open_calls += 1
            return _HealthProbeResponse({"ok": True})

    def build_opener(_handler: object) -> Opener:
        clock.now = 6.0
        return Opener()

    monkeypatch.setattr(deploy, "time", clock)
    monkeypatch.setattr(deploy, "HEALTH_STARTUP_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(deploy.urllib.request, "build_opener", build_opener)

    with pytest.raises(
        deploy.DeploymentError,
        match=r"^health check failed: /healthz \(not_ready\)$",
    ):
        deploy.RealOps().health(port=18900)

    assert open_calls == 0


@pytest.mark.parametrize(
    ("slow_path", "expected_failure_path"),
    (
        ("/healthz", "/healthz"),
        ("/activation-readiness", "/activation-readiness"),
    ),
)
def test_real_health_probe_rejects_valid_response_completed_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
    slow_path: str,
    expected_failure_path: str,
) -> None:
    clock = _HealthProbeClock()

    class SlowResponse(_HealthProbeResponse):
        def read(self) -> bytes:
            clock.now = 6.0
            return super().read()

    class Opener:
        def open(self, url: str, *, timeout: float) -> _HealthProbeResponse:
            del timeout
            if url.endswith("/healthz"):
                payload = {"ok": True}
            else:
                payload = {"ok": True, "service": "activation"}
            if url.endswith(slow_path):
                return SlowResponse(payload)
            return _HealthProbeResponse(payload)

    monkeypatch.setattr(deploy, "time", clock)
    monkeypatch.setattr(deploy, "HEALTH_STARTUP_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(
        deploy.urllib.request,
        "build_opener",
        lambda _handler: Opener(),
    )

    with pytest.raises(deploy.DeploymentError) as caught:
        deploy.RealOps().health(port=18900)

    assert str(caught.value) == (
        f"health check failed: {expected_failure_path} (not_ready)"
    )


def test_real_health_probe_times_acceptance_after_payload_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _HealthProbeClock()

    class SlowStatusResponse(_HealthProbeResponse):
        @property
        def status(self) -> int:
            clock.now = 6.0
            return 200

        @status.setter
        def status(self, _value: int) -> None:
            pass

    class Opener:
        def open(self, url: str, *, timeout: float) -> _HealthProbeResponse:
            del timeout
            if url.endswith("/healthz"):
                return _HealthProbeResponse({"ok": True})
            return SlowStatusResponse({"ok": True, "service": "activation"})

    monkeypatch.setattr(deploy, "time", clock)
    monkeypatch.setattr(deploy, "HEALTH_STARTUP_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(
        deploy.urllib.request,
        "build_opener",
        lambda _handler: Opener(),
    )

    with pytest.raises(
        deploy.DeploymentError,
        match=(
            r"^health check failed: /activation-readiness "
            r"\(not_ready\)$"
        ),
    ):
        deploy.RealOps().health(port=18900)


def test_real_health_probe_ignores_operator_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            payload = (
                {"ok": True}
                if self.path == "/healthz"
                else {"ok": True, "service": "activation"}
            )
            raw = json.dumps(payload).encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    try:
        deploy.RealOps().health(port=server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _unit_text(
    state_file: Path,
    *,
    cutover: int | None,
    upload_identity_db: Path,
    pet_usage_db: Path,
    upload_mode: str = "compat",
    upload_epoch: int = 1,
    activation_epoch: int = 1,
) -> str:
    workdir = state_file.parent
    parts = [
        "/usr/bin/python3",
        (state_file.parent / "scripts" / "sample_collector_server.py").as_posix(),
        "--key",
        "synthetic-ingest-key",
        "--out",
        (workdir / "samples.jsonl").as_posix(),
        "--dashboard-pass-file",
        (workdir / "dashboard_pass.txt").as_posix(),
        "--export-key-file",
        (workdir / "export_key.txt").as_posix(),
        "--version-manifest-file",
        (workdir / "version_manifest.json").as_posix(),
        "--activation-key-file",
        (workdir / "activation_key.txt").as_posix(),
        "--activation-http-e2ee-private-key-file",
        (workdir / "activation_http_e2ee_key.txt").as_posix(),
        "--master-key-file",
        (workdir / "master_key.txt").as_posix(),
        "--master-key-v2-file",
        (workdir / "master_key_v2.txt").as_posix(),
        "--v2-min-version",
        "0.2.7",
        "--min-client-security-epoch",
        str(activation_epoch),
        "--min-client-version",
        "0.3.1",
        "--bindings-file",
        (workdir / "bindings.json").as_posix(),
        "--revoked-file",
        (workdir / "revoked.txt").as_posix(),
        "--device-blocks-file",
        (workdir / "device_blocks.json").as_posix(),
        "--device-block-key-file",
        (workdir / "device_block_key.txt").as_posix(),
        "--telemetry-file",
        (workdir / "activation_telemetry.jsonl").as_posix(),
        "--trials-file",
        state_file.as_posix(),
        "--pet-usage-db-file",
        pet_usage_db.as_posix(),
        "--pet-usage-lease-seconds",
        "15",
        "--upload-identity-mode",
        upload_mode,
        "--upload-identity-db-file",
        upload_identity_db.as_posix(),
        "--upload-security-epoch",
        str(upload_epoch),
        "--upload-lease-seconds",
        "300",
        "--trial-hours",
        "3" if cutover is not None else "12",
    ]
    if cutover is not None:
        parts.extend(
            [
                "--trial-grandfather-cutover-at",
                str(cutover),
                "--trial-grandfather-hours",
                "12",
            ]
        )
    return (
        "[Service]\n"
        "User=ubuntu\n"
        "Group=ubuntu\n"
        "UMask=0077\n"
        "WorkingDirectory=" + workdir.as_posix() + "\n"
        "StateDirectoryMode=0700\n"
        "StateDirectory=bidking-activation-anchor bidking-upload-identity\n"
        "ExecStart=\n"
        f"ExecStart={' '.join(parts)}\n"
    )


class FakeOps:
    def __init__(
        self,
        config: deploy.Config,
        *,
        fail_stage: str | None = None,
        corrupt_candidate_state: bool = False,
    ) -> None:
        self.config = config
        self.fail_stage = fail_stage
        self.corrupt_candidate_state = corrupt_candidate_state
        self.running = True
        self.start_count = 0
        self.stop_count = 0
        self.health_count = 0
        self.daemon_reload_count = 0
        self.candidate_started = False
        self.candidate_new_expiry: int | None = None

    def preflight(self, config: deploy.Config) -> None:
        deploy._validate_preflight_inputs(config)
        deploy._preflight_trial_transform(config)

    def stop(self, service: str) -> None:
        assert service == self.config.service
        self.running = False
        self.stop_count += 1

    def start(self, service: str) -> None:
        assert service == self.config.service
        self.running = True
        self.start_count += 1
        core = (
            self.config.deployed_dir / "activation_core.py"
        ).read_text(encoding="utf-8")
        if f"ACTIVATION_CORE_API_VERSION = {deploy.EXPECTED_CORE_API}" in core:
            self.candidate_started = True
            state = json.loads(
                self.config.state_file.read_text(encoding="utf-8")
            )
            if self.corrupt_candidate_state:
                state["candidate-new"] = {"schema_version": 2}
            else:
                now = int(time.time())
                self.candidate_new_expiry = now + 3 * 3600
                state["candidate-new"] = {
                    "schema_version": 2,
                    "start": now,
                    "window_seconds": 3 * 3600,
                    "last_server_now": now,
                }
            self.config.state_file.write_text(
                json.dumps(state, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    def daemon_reload(self) -> None:
        self.daemon_reload_count += 1

    def unit_readback(self, config: deploy.Config) -> dict[str, object]:
        return deploy._unit_service_identity(config, path=config.unit_file)

    def wait_until(self, epoch: int) -> None:
        assert epoch == self.config.cutover_at

    def health(self, *, port: int) -> None:
        assert port == self.config.port
        assert self.running
        self.health_count += 1
        core = (
            self.config.deployed_dir / "activation_core.py"
        ).read_text(encoding="utf-8")
        state = json.loads(
            self.config.state_file.read_text(encoding="utf-8")
        )
        if f"ACTIVATION_CORE_API_VERSION = {deploy.EXPECTED_CORE_API}" in core:
            assert all(type(value) is dict for value in state.values())
        elif "ACTIVATION_CORE_API_VERSION = 3" in core:
            assert all(type(value) is int for value in state.values())
        else:
            raise AssertionError("unknown activation runtime in clone")

    def checkpoint(self, stage: str) -> None:
        if stage == self.fail_stage:
            raise RuntimeError(f"injected failure: {stage}")


def _clone(tmp_path: Path) -> tuple[deploy.Config, FakeOps, dict]:
    home = tmp_path / "home"
    repo = home / "bidking"
    deployed = repo / "scripts"
    deployed.mkdir(parents=True)
    candidate = home
    for name in (*deploy.ACTIVATION_GROUP, deploy.TRIAL_ADMIN):
        source = (
            ROOT / "src" / "bidking_lab" / "secure" / name
            if name == "activation_http_e2ee.py"
            else ROOT / "src" / "bidking_lab" / "live" / name
            if name == "quote_preferences.py"
            else SCRIPTS / name
        )
        shutil.copy2(source, candidate / name)
    shutil.copy2(SCRIPTS / "server_update.sh", candidate / "server_update.sh")

    old_core = deployed / "activation_core.py"
    old_core.write_text(HOTFIX2_R1_TRIAL_LOADER, encoding="utf-8")
    for name in deploy.ACTIVATION_GROUP:
        if name != "activation_core.py":
            (deployed / name).write_text(
                f"# hotfix2-r1 clone: {name}\n",
                encoding="utf-8",
            )

    now = int(time.time())
    cutover = now
    state_file = repo / "trials.json"
    old_starts = {
        "old-a": now - 60,
        "old-b": now - 120,
    }
    state_file.write_text(
        json.dumps(old_starts, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    old_expiries = {
        device: start + 12 * 3600
        for device, start in old_starts.items()
    }
    upload_identity_dir = tmp_path / "upload-identity"
    upload_identity_dir.mkdir()
    upload_identity_db = upload_identity_dir / "upload_identity.sqlite3"
    subprocess.run(
        [
            sys.executable,
            str(candidate / "upload_identity_db_admin.py"),
            "--db",
            str(upload_identity_db),
            "init",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pet_dir = tmp_path / "pet"
    pet_dir.mkdir()
    pet_db = pet_dir / "pet_usage.sqlite3"
    pet_head = pet_dir / "pet_usage.head.jsonl"
    activation_server_product.initialize_pet_usage_database(pet_db, secret=b"synthetic-deploy-pet-backup")
    unit_file = tmp_path / "systemd" / "bidking-collector.service"
    unit_file.parent.mkdir()
    unit_file.write_text(
        _unit_text(
            state_file,
            cutover=None,
            upload_identity_db=upload_identity_db,
            pet_usage_db=pet_db,
        ),
        encoding="utf-8",
    )
    candidate_unit = home / "bidking-collector.service"
    candidate_unit.write_text(
        _unit_text(
            state_file,
            cutover=cutover,
            upload_identity_db=upload_identity_db,
            pet_usage_db=pet_db,
        ),
        encoding="utf-8",
    )

    config = deploy.Config(
        repo_dir=repo,
        candidate_dir=candidate,
        server_update_script=candidate / "server_update.sh",
        unit_file=unit_file,
        candidate_unit_file=candidate_unit,
        state_file=state_file,
        upload_identity_db_file=upload_identity_db,
        upload_identity_mode="compat",
        upload_security_epoch=1,
        upload_lease_seconds=300,
        min_client_security_epoch=1,
        min_client_version="0.3.1",
        v2_min_version="0.2.7",
        pet_usage_db_file=pet_db,
        pet_usage_lease_seconds=15,
        service_user="ubuntu",
        service_group="ubuntu",
        working_directory=repo,
        service="bidking-collector",
        port=18900,
        cutover_at=cutover,
        new_trial_hours=3.0,
        grandfather_hours=12.0,
        legacy_trial_hours=12.0,
        backup_root=repo / ".activation_state_deploy_backups",
        lock_file=repo / ".activation_state_deploy.lock.sqlite3",
        python_executable=sys.executable,
        lock_timeout_seconds=0.2,
    )
    evidence = {
        "old_core": old_core.read_bytes(),
        "old_unit": unit_file.read_bytes(),
        "old_state": state_file.read_bytes(),
        "old_expiries": old_expiries,
        "pet_db": pet_db,
        "pet_head": pet_head,
        "pet_db_sha": _sha(pet_db),
        "pet_head_sha": _sha(pet_head),
        "upload_identity_db": upload_identity_db,
        "upload_identity_db_sha": _sha(upload_identity_db),
    }
    return config, FakeOps(config), evidence


def _assert_pet_untouched(evidence: dict) -> None:
    assert _sha(evidence["pet_db"]) == evidence["pet_db_sha"]
    assert _sha(evidence["pet_head"]) == evidence["pet_head_sha"]
    assert _sha(evidence["upload_identity_db"]) == evidence["upload_identity_db_sha"]


def _member_config(tmp_path: Path):
    home = tmp_path / "home"
    repo = home / "bidking"
    repo.mkdir(parents=True)
    old = repo / "scripts"
    old.mkdir()
    for name in (*deploy.ACTIVATION_GROUP, deploy.TRIAL_ADMIN):
        source = (ROOT / "src" / "bidking_lab" / "secure" / name if name == "activation_http_e2ee.py"
                  else ROOT / "src" / "bidking_lab" / "live" / name if name == "quote_preferences.py"
                  else SCRIPTS / name)
        shutil.copy2(source, home / name)
        shutil.copy2(source, old / name)
    member_dir = tmp_path / "membership"
    member_dir.mkdir(mode=0o700)
    member = member_dir / "membership.sqlite3"
    subprocess.run([sys.executable, "-B", "-c",
                    "import membership_card_store; from pathlib import Path; import sys; "
                    "membership_card_store.MembershipCardStore.initialize(Path(sys.argv[1]))", str(member)],
                   cwd=home, capture_output=True, check=True, timeout=5)
    state = repo / "trials.json"
    state.write_text("{}\n", encoding="utf-8")
    pet = tmp_path / "pet" / "pet.sqlite3"
    upload = tmp_path / "upload" / "upload.sqlite3"
    now = int(time.time())
    text = _unit_text(state, cutover=now, upload_identity_db=upload, pet_usage_db=pet,
                      upload_mode="disabled", upload_epoch=0, activation_epoch=0)
    text = text.rstrip() + f" --membership-store-file {member.as_posix()}\n"
    current_unit, candidate_unit = tmp_path / "old.service", home / "candidate.service"
    current_unit.write_text(text, encoding="utf-8")
    candidate_unit.write_text(text, encoding="utf-8")
    config = deploy.Config(
        repo_dir=repo, candidate_dir=home, server_update_script=home / "server_update.sh",
        unit_file=current_unit, candidate_unit_file=candidate_unit, state_file=state,
        upload_identity_db_file=upload, upload_identity_mode="disabled", upload_security_epoch=0,
        upload_lease_seconds=300, min_client_security_epoch=0, min_client_version="0.3.1",
        v2_min_version="0.2.7", pet_usage_db_file=pet, pet_usage_lease_seconds=15,
        service_user="ubuntu", service_group="ubuntu", working_directory=repo,
        service="bidking-collector", port=18900, cutover_at=now, new_trial_hours=3,
        grandfather_hours=12, legacy_trial_hours=12, backup_root=tmp_path / "backup",
        lock_file=tmp_path / "deploy.lock", python_executable=sys.executable,
        trial_state_mode="existing-v2", membership_store_file=member,
    )
    return config


def _member_recovery(config, before):
    return deploy.require_membership_recovery(
        config.membership_store_file, config.candidate_dir, config.deployed_dir,
        config.unit_file, config.working_directory, before=before,
        candidate_unit=config.candidate_unit_file,
    )


def test_member036_unit_and_readonly_boundary_representative(tmp_path):
    config = _member_config(tmp_path)
    before = {p.name: p.read_bytes() for p in config.membership_store_file.parent.iterdir()}
    contract = deploy._validate_candidate_unit(config)
    deploy._validate_membership_location(config, contract)
    boundary = deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
    assert boundary == {"schema_version": 1, "accounts": 0, "claims": 0, "pending_empty": True}
    assert before == {p.name: p.read_bytes() for p in config.membership_store_file.parent.iterdir()}
    assert deploy.EXPECTED_CORE_API == 7 and "membership_card_store.py" in deploy.ACTIVATION_GROUP
    assert config.upload_identity_mode == "disabled" and config.upload_security_epoch == 0


@pytest.mark.parametrize("fault", ["wrong_path", "missing_option", "duplicate_option"])
def test_member036_unit_path_refuses(tmp_path, fault):
    config = _member_config(tmp_path)
    text = config.candidate_unit_file.read_text(encoding="utf-8")
    option = f" --membership-store-file {config.membership_store_file.as_posix()}"
    replacement = "" if fault == "missing_option" else option + option if fault == "duplicate_option" else option + "-wrong"
    config.candidate_unit_file.write_text(text.replace(option, replacement), encoding="utf-8")
    with pytest.raises(deploy.DeploymentError):
        deploy._validate_candidate_unit(config)


@pytest.mark.parametrize("fault", ["missing", "bad_db", "bad_pending"])
def test_member036_invalid_boundary_refuses(tmp_path, fault):
    config = _member_config(tmp_path)
    member = config.membership_store_file
    if fault == "missing":
        member.unlink()
    elif fault == "bad_db":
        member.write_bytes(b"synthetic-invalid-db")
    else:
        Path(str(member) + ".projection.json").write_text('{"entries":{}}', encoding="utf-8")
    with pytest.raises(deploy.DeploymentError):
        deploy.membership_boundary(member, config.candidate_dir)


@pytest.mark.parametrize("api", [5, 6, 7])
def test_member036_historical_trial_v2_capability_is_not_current_candidate(api):
    module = ModuleType("synthetic-trial-reader")
    module.ACTIVATION_CORE_API_VERSION = api
    module.TRIAL_RECORD_SCHEMA_VERSION = 2
    module._strict_trial_record = lambda record, now: record
    deploy._require_core_accepts_v2(module, {}, label="synthetic")
    module.ACTIVATION_CORE_API_VERSION = 8
    with pytest.raises(deploy.DeploymentError):
        deploy._require_core_accepts_v2(module, {}, label="synthetic")


def test_member036_empty_legacy_restore_requires_known_empty_before(tmp_path):
    config = _member_config(tmp_path)
    before = deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
    (config.deployed_dir / "activation_core.py").write_text("ACTIVATION_CORE_API_VERSION = 5\n", encoding="utf-8")
    assert _member_recovery(config, before) == before
    with pytest.raises(deploy.MembershipRecoveryRequired):
        _member_recovery(config, None)


class _MemberSchemaIntSubclass(int):
    pass


@pytest.mark.parametrize("schema", [True, 1.0, False, 0.0, "1", None, _MemberSchemaIntSubclass(1)],
                         ids=["bool_true", "float_one", "bool_false", "float_zero", "string", "none", "int_subclass"])
def test_member036_before_schema_requires_exact_integer_not_coercible_siblings(tmp_path, schema):
    # This empty authority needs only STORE; no concurrently-owned collector,
    # dashboard or records source is copied/read for the observation type gate.
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    shutil.copy2(SCRIPTS / "membership_card_store.py", candidate / "membership_card_store.py")
    member = tmp_path / "membership.sqlite3"
    subprocess.run([sys.executable, "-B", "-c",
                    "import membership_card_store; from pathlib import Path; import sys; "
                    "membership_card_store.MembershipCardStore.initialize(Path(sys.argv[1]))", str(member)],
                   cwd=candidate, capture_output=True, check=True, timeout=5)
    before = deploy.membership_boundary(member, candidate)
    def recover(observation):
        return deploy.require_membership_recovery(member, candidate, None, None, tmp_path,
                                                   before=observation, candidate_unit=tmp_path / "unused.service")
    assert type(before["schema_version"]) is int and before["schema_version"] == 1
    assert recover(before) == before
    with pytest.raises(deploy.MembershipRecoveryRequired):
        recover({**before, "schema_version": schema})


def test_member036_pending_claim_blocks_legacy_even_with_zero_rows(tmp_path):
    config = _member_config(tmp_path)
    before = deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
    Path(str(config.membership_store_file) + ".projection.json").write_text(
        '{"schema_version":1,"entries":{"synthetic":"pending"}}', encoding="utf-8")
    (config.deployed_dir / "activation_core.py").write_text("ACTIVATION_CORE_API_VERSION = 5\n", encoding="utf-8")
    with pytest.raises(deploy.MembershipRecoveryRequired):
        _member_recovery(config, before)


def test_member036_sqlite_consistent_backup_no_clobber_and_pending(tmp_path):
    config = _member_config(tmp_path)
    projection = Path(str(config.membership_store_file) + ".projection.json")
    projection.write_text('{"schema_version":1,"entries":{"synthetic":"pending"}}', encoding="utf-8")
    target = tmp_path / "snapshot"
    target.mkdir(mode=0o700)
    observed = deploy._backup_membership(config, target)
    assert observed["boundary"]["pending_empty"] is False
    assert (target / "membership.before.sqlite3.projection.json").read_bytes() == projection.read_bytes()
    assert deploy.membership_boundary(target / "membership.before.sqlite3", config.candidate_dir) == observed["boundary"]
    with pytest.raises(FileExistsError):
        deploy._backup_membership(config, target)


def _member_purchase(config, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    secret = b"synthetic-deploy-member-only"
    config.pet_usage_db_file.parent.mkdir(mode=0o700)
    activation_server_product.initialize_pet_usage_database(config.pet_usage_db_file, secret=secret)
    service = activation_server_product.ActivationService(
        secret=secret, master_key_b64="S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s=",
        bindings_path=config.repo_dir / "bindings.json", revoked_path=config.repo_dir / "revoked.txt",
        telemetry_path=config.repo_dir / "telemetry.jsonl", trials_path=config.state_file,
        pet_usage_db_path=config.pet_usage_db_file, membership_store_path=config.membership_store_file,
    )
    core = activation_server_product.activation_core
    a = core.mint_token("synthetic-deploy-A", secret, iat=now, exp=now + 30 * 86400)
    b = core.mint_token("synthetic-deploy-B", secret, iat=now, exp=now + 90 * 86400)
    assert service.handle_activate({"device": "synthetic-device", "token": a})[0] == 200
    result = service.handle_activate({"device": "synthetic-device", "token": a,
                                      "card_token": b, "card_redemption_schema": 1})
    assert result[0] == 200 and result[1]["card_redemption"]["status"] == "redeemed"
    return service, a, b, now


@pytest.mark.parametrize("fault", ["old_api5", "no_store", "no_member_flag", "other_auth_path", "unknown_before", "bad_db"])
def test_member036_used_authority_refuses_unsafe_restore_without_losing_rights(tmp_path, monkeypatch, fault):
    config = _member_config(tmp_path)
    service, a, b, now = _member_purchase(config, monkeypatch)
    before = deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
    snapshot = service.membership_store.read("synthetic-deploy-A", now=now)
    original = config.membership_store_file.read_bytes()
    if fault == "old_api5":
        (config.deployed_dir / "activation_core.py").write_text("ACTIVATION_CORE_API_VERSION = 5\n", encoding="utf-8")
    elif fault == "no_store":
        (config.deployed_dir / "membership_card_store.py").unlink()
    elif fault == "no_member_flag":
        config.unit_file.write_text(config.unit_file.read_text().replace(
            f" --membership-store-file {config.membership_store_file.as_posix()}", ""), encoding="utf-8")
    elif fault == "other_auth_path":
        config.unit_file.write_text(config.unit_file.read_text().replace("--bindings-file ", "--bindings-file wrong-"), encoding="utf-8")
    elif fault == "unknown_before":
        before = None
    elif fault == "bad_db":
        with sqlite3.connect(config.membership_store_file) as connection:
            connection.execute("PRAGMA user_version=9")
        original = config.membership_store_file.read_bytes()
    with pytest.raises(deploy.MembershipRecoveryRequired):
        _member_recovery(config, before)
    assert config.membership_store_file.read_bytes() == original
    if fault != "bad_db":
        assert service.membership_store.read("synthetic-deploy-A", now=now) == snapshot
        assert service.authorize_my_records(b, now=now)[2]["uid"] == "synthetic-deploy-A"


def test_member036_exact_compatible_api7_recovery_preserves_live_grants(tmp_path, monkeypatch):
    config = _member_config(tmp_path)
    service, a, b, now = _member_purchase(config, monkeypatch)
    before = deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
    snapshot = service.membership_store.read("synthetic-deploy-A", now=now)
    assert _member_recovery(config, before) == before
    assert snapshot.membership_exp == now + 120 * 86400
    assert snapshot.my_records_exp == snapshot.full_pet_exp == snapshot.membership_exp
    assert snapshot.private_dashboard_exp is None and snapshot.pet_purchased_quota_seconds == 43200
    assert service.membership_store.read("synthetic-deploy-A", now=now) == snapshot


def test_member036_real_rollback_guard_stops_before_legacy_mutation(tmp_path, monkeypatch):
    config = _member_config(tmp_path)
    service, a, b, now = _member_purchase(config, monkeypatch)
    ops = FakeOps(config)
    ops.stop(config.service)
    admin = deploy._load_candidate_admin(config)
    transaction = deploy.Transaction(trial_state_already_v2=True)
    deploy._snapshot_after_stop(config, transaction, trial_snapshot=deploy._read_admin_snapshot(admin, config.state_file))
    transaction.trial_state_verified = True
    transaction.candidate_may_have_served = True
    backup_core = transaction.backup_dir / "files" / "activation_core.py"
    backup_core.write_text("ACTIVATION_CORE_API_VERSION = 5\n", encoding="utf-8")
    live_core = (config.deployed_dir / "activation_core.py").read_bytes()
    db = config.membership_store_file.read_bytes()
    with pytest.raises(deploy.MembershipRecoveryRequired):
        deploy._rollback(config, transaction, admin, ops, reason="synthetic-post-start-failure")
    assert not ops.running and ops.start_count == 0
    assert config.membership_store_file.read_bytes() == db
    assert (config.deployed_dir / "activation_core.py").read_bytes() == live_core
    assert json.loads(transaction.manifest_path.read_text())["status"] == "membership_recovery_required"


def test_member036_real_compatible_rollback_never_restores_old_purchase_snapshot(tmp_path, monkeypatch):
    config = _member_config(tmp_path)
    service, a, b, now = _member_purchase(config, monkeypatch)
    ops = FakeOps(config)
    ops.stop(config.service)
    admin = deploy._load_candidate_admin(config)
    transaction = deploy.Transaction(trial_state_already_v2=True)
    deploy._snapshot_after_stop(config, transaction, trial_snapshot=deploy._read_admin_snapshot(admin, config.state_file))
    transaction.trial_state_verified = True
    transaction.candidate_may_have_served = True
    c = activation_server_product.activation_core.mint_token(
        "synthetic-deploy-C", service.secret, iat=now, exp=now + 30 * 86400)
    assert service.handle_activate({"device": "synthetic-device", "token": a,
                                    "card_token": c, "card_redemption_schema": 1})[0] == 200
    snapshot = service.membership_store.read("synthetic-deploy-A", now=now)
    deploy._rollback(config, transaction, admin, ops, reason="synthetic-compatible-repair")
    assert ops.running and ops.start_count == 1
    assert service.membership_store.read("synthetic-deploy-A", now=now) == snapshot
    assert snapshot.membership_exp == now + 150 * 86400
    assert snapshot.full_pet_exp == snapshot.my_records_exp == now + 120 * 86400
    assert snapshot.pet_purchased_quota_seconds == 86400
    assert len(service.membership_store.claims_for_account("synthetic-deploy-A")) == 3


def test_member036_restored_dataclass_core_load_is_isolated():
    previous = dict(sys.modules)
    core = deploy._load_module(SCRIPTS / "activation_core.py", "member036-test")
    assert core.ACTIVATION_CORE_API_VERSION == 7
    deploy._require_core_accepts_v2(core, {}, label="current")
    assert set(sys.modules) == set(previous)
    assert all(sys.modules[name] is module for name, module in previous.items())


def test_member036_independent_statedirectory_effective_readback(tmp_path, monkeypatch):
    config = _member_config(tmp_path)
    for path in (config.unit_file, config.candidate_unit_file):
        path.write_text(path.read_text().replace("StateDirectory=bidking-activation-anchor ",
                                               "StateDirectory=bidking-membership bidking-activation-anchor "), encoding="utf-8")
    deploy._validate_candidate_unit(config)
    values = {"FragmentPath": str(config.unit_file), "DropInPaths": "", "User": "ubuntu", "Group": "ubuntu",
              "UMask": "0077", "StateDirectoryMode": "0700", "WorkingDirectory": str(config.working_directory),
              "StateDirectory": "bidking-activation-anchor bidking-membership bidking-upload-identity"}
    monkeypatch.setattr(deploy, "_run_checked", lambda command, **kw: subprocess.CompletedProcess(command, 0, values[command[-2]], ""))
    assert deploy.RealOps().unit_readback(config)["dropin_count"] == 0
    values["StateDirectory"] = "bidking-activation-anchor bidking-upload-identity"
    with pytest.raises(deploy.DeploymentError, match="service contract"):
        deploy.RealOps().unit_readback(config)


@pytest.mark.parametrize("active,pid,allowed", [("inactive", "0", True), ("failed", "0", True),
                                               ("active", "0", False), ("inactive", "12", False), ("", "0", False)])
def test_member036_stop_must_be_confirmed_before_recovery(monkeypatch, active, pid, allowed):
    monkeypatch.setattr(deploy.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 3, "", ""))
    def run(command, **kw):
        value = active if "ActiveState" in command else pid if "MainPID" in command else ""
        return subprocess.CompletedProcess(command, 0, value, "")
    monkeypatch.setattr(deploy, "_run_checked", run)
    if allowed:
        deploy.RealOps().stop("synthetic-only")
    else:
        with pytest.raises(deploy.DeploymentError, match="stop could not be confirmed"):
            deploy.RealOps().stop("synthetic-only")


def test_member036_backup_reads_committed_wal_not_stale_main_file(tmp_path, monkeypatch):
    config = _member_config(tmp_path)
    keeper = sqlite3.connect(config.membership_store_file)
    try:
        assert keeper.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        keeper.execute("BEGIN")
        keeper.execute("SELECT count(*) FROM accounts").fetchone()
        service, a, b, now = _member_purchase(config, monkeypatch)
        assert Path(str(config.membership_store_file) + "-wal").exists()
        target = tmp_path / "wal-backup"
        target.mkdir(mode=0o700)
        observed = deploy._backup_membership(config, target)
        assert observed["boundary"]["accounts"] == 1 and observed["boundary"]["claims"] == 2
        assert observed["files"]["-wal"]["present"] is True
        assert deploy.membership_boundary(target / "membership.before.sqlite3", config.candidate_dir)["claims"] == 2
    finally:
        keeper.close()


def test_member036_authority_truncation_cannot_be_reclassified_as_unused(tmp_path, monkeypatch):
    config = _member_config(tmp_path)
    _member_purchase(config, monkeypatch)
    before = deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
    with sqlite3.connect(config.membership_store_file) as connection:
        connection.execute("DELETE FROM card_claims")
        connection.execute("DELETE FROM accounts")
    assert deploy.membership_boundary(config.membership_store_file, config.candidate_dir)["claims"] == 0
    with pytest.raises(deploy.MembershipRecoveryRequired):
        _member_recovery(config, before)


def test_member036_pending_unknown_cannot_restart_even_exact_compatible_pair(tmp_path):
    config = _member_config(tmp_path)
    before = deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
    Path(str(config.membership_store_file) + ".projection.json").write_text(
        '{"schema_version":1,"entries":{"synthetic":"pending"}}', encoding="utf-8")
    with pytest.raises(deploy.MembershipRecoveryRequired):
        _member_recovery(config, before)


def test_member036_small_authority_backup_is_actual_private_copy_not_fingerprint(tmp_path, monkeypatch):
    config = _member_config(tmp_path)
    service, a, b, now = _member_purchase(config, monkeypatch)
    config.upload_identity_db_file.parent.mkdir(mode=0o700)
    with sqlite3.connect(config.upload_identity_db_file) as db:
        db.execute("CREATE TABLE synthetic_upload(value TEXT)")
        db.execute("INSERT INTO synthetic_upload VALUES ('noncredential-private-fixture')")
    samples = config.repo_dir / "samples.jsonl"
    samples.write_bytes(b"synthetic-raw-samples-not-to-back-up")
    target = tmp_path / "authority-backup"
    target.mkdir(mode=0o700)
    backups = deploy._backup_activation_authorities(config, target)
    assert all(backups[role]["present"] for role in ("bindings", "paid_starts", "trials", "pet_db", "pet_head", "upload_identity_db"))
    for role in ("bindings", "paid_starts", "trials", "pet_head"):
        source = {"bindings": config.repo_dir / "bindings.json", "paid_starts": config.repo_dir / "paid_starts.json",
                  "trials": config.state_file, "pet_head": config.pet_usage_db_file.with_name(f"{config.pet_usage_db_file.stem}.head.jsonl")}[role]
        copy = target / backups[role]["file"]
        assert copy.read_bytes() == source.read_bytes()
        if os.name == "posix":
            assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    with sqlite3.connect(target / backups["upload_identity_db"]["file"]) as db:
        assert db.execute("SELECT value FROM synthetic_upload").fetchone() == ("noncredential-private-fixture",)
    assert not any("samples" in p.name or "key" in p.name for p in (target / "authorities").iterdir())
    assert samples.read_bytes() == b"synthetic-raw-samples-not-to-back-up"
    with pytest.raises(FileExistsError):
        deploy._backup_activation_authorities(config, target)


@pytest.mark.parametrize("fault", ["omit_committed_wal", "replace_same_bytes"])
def test_member036_readonly_snapshot_rejects_sidecar_loss_or_generation_swap(tmp_path, monkeypatch, fault):
    config = _member_config(tmp_path)
    keeper = sqlite3.connect(config.membership_store_file)
    real_copy = deploy._copy_exclusive
    swapped = False
    try:
        keeper.execute("PRAGMA journal_mode=WAL")
        keeper.execute("BEGIN")
        keeper.execute("SELECT count(*) FROM accounts").fetchone()
        _member_purchase(config, monkeypatch)
        def copy(source, target):
            nonlocal swapped
            if fault == "omit_committed_wal" and source.name.endswith("-wal"):
                return _sha(source)  # Known damaged producer: no copied WAL.
            result = real_copy(source, target)
            if fault == "replace_same_bytes" and source == config.membership_store_file and not swapped:
                # A same-byte change still changes the source generation; no
                # deletion/replacement of an open Windows SQLite handle needed.
                os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1_000_000_000))
                swapped = True
            return result
        monkeypatch.setattr(deploy, "_copy_exclusive", copy)
        with pytest.raises(deploy.DeploymentError):
            deploy.membership_boundary(config.membership_store_file, config.candidate_dir)
    finally:
        keeper.close()


def _assert_legacy_pair(
    config: deploy.Config,
    ops: FakeOps,
    evidence: dict,
    *,
    expected_new_record: bool,
) -> None:
    assert ops.running
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == evidence["old_core"]
    assert config.unit_file.read_bytes() == evidence["old_unit"]
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert all(type(value) is int for value in state.values())
    expected_devices = set(evidence["old_expiries"])
    if expected_new_record:
        expected_devices.add("candidate-new")
    assert set(state) == expected_devices
    for device, expected_expiry in evidence["old_expiries"].items():
        assert state[device] + 12 * 3600 == expected_expiry
    if expected_new_record:
        assert ops.candidate_new_expiry is not None
        assert (
            state["candidate-new"] + 12 * 3600
            == ops.candidate_new_expiry
        )
    deploy._verify_legacy_runtime(
        core_path=config.deployed_dir / "activation_core.py",
        state_file=config.state_file,
        expected_expiries={
            device: start + 12 * 3600
            if device != "candidate-new"
            else start + 12 * 3600
            for device, start in state.items()
        },
        legacy_trial_hours=12.0,
    )
    _assert_pet_untouched(evidence)


def test_preflight_only_does_not_write_authoritative_paths(tmp_path: Path) -> None:
    config, ops, evidence = _clone(tmp_path)
    before = {
        path: path.read_bytes()
        for path in (
            config.state_file,
            config.unit_file,
            *(
                config.deployed_dir / name
                for name in deploy.ACTIVATION_GROUP
            ),
        )
    }

    deploy.preflight(config, ops)

    assert ops.running
    assert ops.stop_count == ops.start_count == 0
    assert not config.lock_file.exists()
    assert not config.backup_root.exists()
    for path, raw in before.items():
        assert path.read_bytes() == raw
    _assert_pet_untouched(evidence)


def _protected_path(config: deploy.Config, role: str) -> Path:
    contract = deploy._validate_candidate_unit(config)
    return next(path for item_role, path, _mutable in contract.protected_paths if item_role == role)


def test_preflight_allows_live_mutable_sample_append(tmp_path: Path) -> None:
    config, _ops, _evidence = _clone(tmp_path)
    samples = _protected_path(config, "samples")
    samples.write_bytes(b"before\n")

    class AppendDuringPreflight(FakeOps):
        def preflight(self, config: deploy.Config) -> None:
            super().preflight(config)
            with samples.open("ab") as stream:
                stream.write(b"live-upload\n")

    deploy.preflight(config, AppendDuringPreflight(config))

    assert samples.read_bytes() == b"before\nlive-upload\n"


def test_preflight_allows_live_mutable_atomic_replacement(tmp_path: Path) -> None:
    config, _ops, _evidence = _clone(tmp_path)
    samples = _protected_path(config, "samples")
    samples.write_bytes(b"before\n")

    class ReplaceDuringPreflight(FakeOps):
        def preflight(self, config: deploy.Config) -> None:
            super().preflight(config)
            replacement = samples.with_suffix(".replacement")
            replacement.write_bytes(b"after\n")
            replacement.replace(samples)

    deploy.preflight(config, ReplaceDuringPreflight(config))

    assert samples.read_bytes() == b"after\n"


def test_preflight_still_rejects_immutable_authority_change(tmp_path: Path) -> None:
    config, _ops, _evidence = _clone(tmp_path)
    revoked = _protected_path(config, "revoked")
    revoked.write_bytes(b"before\n")

    class MutateImmutableDuringPreflight(FakeOps):
        def preflight(self, config: deploy.Config) -> None:
            super().preflight(config)
            revoked.write_bytes(b"after\n")

    with pytest.raises(
        deploy.DeploymentError,
        match="preflight modified an authoritative path",
    ):
        deploy.preflight(config, MutateImmutableDuringPreflight(config))


def test_preflight_rejects_mutable_path_replaced_by_directory(tmp_path: Path) -> None:
    config, _ops, _evidence = _clone(tmp_path)
    samples = _protected_path(config, "samples")
    samples.write_bytes(b"before\n")

    class ReplaceWithDirectoryDuringPreflight(FakeOps):
        def preflight(self, config: deploy.Config) -> None:
            super().preflight(config)
            samples.unlink()
            samples.mkdir()

    with pytest.raises(
        deploy.DeploymentError,
        match="mutable authoritative path is not a regular file: samples",
    ):
        deploy.preflight(config, ReplaceWithDirectoryDuringPreflight(config))


def test_preflight_failure_payload_exposes_only_fixed_stage() -> None:
    private_detail = "token=private-canary path=C:/Users/example"
    payload = deploy._operator_failure_payload(
        deploy.PreflightStageError("candidate_runtime", private_detail)
    )

    assert payload == {
        "ok": False,
        "reason": "deployment_error",
        "stage": "candidate_runtime",
    }
    assert private_detail not in json.dumps(payload, sort_keys=True)


def test_unclassified_operator_failure_does_not_echo_exception() -> None:
    private_detail = "Bearer private-canary"
    payload = deploy._operator_failure_payload(RuntimeError(private_detail))

    assert payload == {
        "ok": False,
        "reason": "deployment_error",
        "stage": "unclassified",
    }
    assert private_detail not in json.dumps(payload, sort_keys=True)


@pytest.mark.parametrize(
    "stage",
    [
        "candidate_checks",
        "private-canary",
        "candidate_runtime\nprivate-canary",
        True,
        1,
    ],
)
def test_preflight_failure_payload_clamps_unknown_stage(stage: object) -> None:
    error = deploy.PreflightStageError("candidate_runtime")
    error.stage = stage

    assert deploy._operator_failure_payload(error) == {
        "ok": False,
        "reason": "deployment_error",
        "stage": "unclassified",
    }


def test_preflight_failure_payload_rejects_stage_string_subclass() -> None:
    class HostileStage(str):
        def __hash__(self) -> int:
            raise AssertionError("stage subclass must not be hashed")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("stage subclass must not be compared")

    error = deploy.PreflightStageError("candidate_runtime")
    error.stage = HostileStage("candidate_runtime")

    assert deploy._operator_failure_payload(error) == {
        "ok": False,
        "reason": "deployment_error",
        "stage": "unclassified",
    }


def test_real_preflight_environment_failure_keeps_candidate_inputs_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ops, _evidence = _clone(tmp_path)
    monkeypatch.setattr(deploy.os.environ, "copy", lambda: 1 / 0)

    with pytest.raises(deploy.PreflightStageError) as caught:
        deploy.RealOps().preflight(config)

    assert caught.value.stage == "candidate_inputs"


def _prepare_claimed_pending_migration(config: deploy.Config) -> dict[Path, bytes]:
    admin = deploy._load_candidate_admin(config)
    with admin._exclusive_admin_lock(config.state_file):
        snapshot = admin._read_state(config.state_file)
        transformed = admin.activation_core.migrate_trials_for_cutover(
            snapshot.value,
            cutover_at=config.cutover_at,
            grandfather_trial_hours=config.grandfather_hours,
            now=config.cutover_at,
        )
        staged_path, output = admin._stage_state_output(
            config.state_file,
            transformed,
        )
        claimed_path = admin._allocate_claim_path(config.state_file)
        admin._write_commit_intent(
            config.state_file,
            staged_path=staged_path,
            claimed_path=claimed_path,
            input_snapshot=snapshot,
            output_sha256=hashlib.sha256(output).hexdigest(),
        )
        admin._claim_authoritative_state(
            config.state_file,
            claimed_path,
            expected_sha256=snapshot.sha256,
            expected_identity=snapshot.identity,
        )
    assert not config.state_file.exists()
    return {
        path: path.read_bytes()
        for path in config.state_file.parent.glob(
            ".trials.json.trial-admin-*"
        )
        if not path.name.endswith("-lock.sqlite3")
    }


def test_preflight_is_read_only_then_apply_recovers_claimed_pending_commit(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    pending_before = _prepare_claimed_pending_migration(config)

    deploy.preflight(config, ops)

    assert not config.state_file.exists()
    assert {
        path: path.read_bytes() for path in pending_before
    } == pending_before
    assert ops.stop_count == ops.start_count == 0
    assert not config.backup_root.exists()

    manifest_path = deploy.apply(config, ops)

    assert ops.running
    assert config.state_file.exists()
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert set(state) == {"old-a", "old-b", "candidate-new"}
    assert all(record["schema_version"] == 2 for record in state.values())
    assert not list(
        config.state_file.parent.glob(".trials.json.trial-admin-commit.json")
    )
    assert not list(
        config.state_file.parent.glob(".trials.json.trial-admin-claimed-*")
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["recovered_pending_trial_commit"] is True
    assert manifest["trial_admin_lock_protocol_version"] == 3
    assert (
        manifest["trial_admin_lock_backend"]
        == "sqlite-begin-exclusive+claim-link-noreplace"
    )
    _assert_pet_untouched(evidence)


def test_noncooperating_same_content_replacement_after_snapshot_fail_stops(
    tmp_path: Path,
) -> None:
    config, _unused, evidence = _clone(tmp_path)

    class ReplacingOps(FakeOps):
        def checkpoint(self, stage: str) -> None:
            if stage != "after_snapshot":
                return
            replacement = self.config.state_file.with_name(
                "external-same-content.json"
            )
            replacement.write_bytes(self.config.state_file.read_bytes())
            os.replace(replacement, self.config.state_file)

    ops = ReplacingOps(config)
    with pytest.raises(
        deploy.DeploymentError,
        match="recovery did not complete",
    ):
        deploy.apply(config, ops)

    assert not ops.running
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == evidence["old_core"]
    assert config.unit_file.read_bytes() == evidence["old_unit"]
    assert config.state_file.read_bytes() == evidence["old_state"]
    _assert_pet_untouched(evidence)


def test_success_pairs_api5_unit_and_v2_state(tmp_path: Path) -> None:
    config, ops, evidence = _clone(tmp_path)

    manifest_path = deploy.apply(config, ops)

    assert ops.running and ops.candidate_started
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == (config.candidate_dir / "activation_core.py").read_bytes()
    assert config.unit_file.read_bytes() == config.candidate_unit_file.read_bytes()
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert set(state) == {"old-a", "old-b", "candidate-new"}
    assert all(
        type(record) is dict and record["schema_version"] == 2
        for record in state.values()
    )
    for device, expected_expiry in evidence["old_expiries"].items():
        assert (
            state[device]["start"] + state[device]["window_seconds"]
            == expected_expiry
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "deployed"
    assert manifest["installed_core_api"] == 7
    assert manifest["old_files"]["activation_core.py"]["sha256"] == hashlib.sha256(
        evidence["old_core"]
    ).hexdigest()
    _assert_pet_untouched(evidence)


def test_existing_v2_mode_redeploys_without_rewriting_mixed_windows(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    deploy.apply(config, ops)
    config = dataclasses.replace(config, trial_state_mode="existing-v2")
    before = config.state_file.read_bytes()
    repeat_ops = FakeOps(config)

    deploy.preflight(config, repeat_ops)

    assert config.state_file.read_bytes() == before
    manifest_path = deploy.apply(config, repeat_ops)
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert {record["window_seconds"] for record in state.values()} == {
        3 * 3600,
        12 * 3600,
    }
    assert all(record["schema_version"] == 2 for record in state.values())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "deployed"
    assert manifest["trial_state_mode"] == "existing-v2"
    assert not (manifest_path.parent / "trials.before-migrate.json").exists()
    _assert_pet_untouched(evidence)


@pytest.mark.parametrize("invalid_mode", [True, 1, "existing_v2", "legacy"])
def test_trial_state_mode_is_exact_and_explicit(
    tmp_path: Path,
    invalid_mode: object,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    config = dataclasses.replace(config, trial_state_mode=invalid_mode)

    with pytest.raises(
        deploy.DeploymentError,
        match="runtime contract input is invalid",
    ):
        deploy.preflight(config, ops)

    assert config.state_file.read_bytes() == evidence["old_state"]
    assert ops.stop_count == ops.start_count == 0


def test_existing_v2_mode_rollback_preserves_live_v2_and_restores_api5(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    deploy.apply(config, ops)
    config = dataclasses.replace(config, trial_state_mode="existing-v2")
    old_core = (config.deployed_dir / "activation_core.py").read_bytes()
    old_unit = config.unit_file.read_bytes()
    repeat_ops = FakeOps(config, fail_stage="after_health")

    with pytest.raises(
        deploy.DeploymentError,
        match="compatible trial-state authority",
    ):
        deploy.apply(config, repeat_ops)

    assert repeat_ops.running
    assert (config.deployed_dir / "activation_core.py").read_bytes() == old_core
    assert config.unit_file.read_bytes() == old_unit
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert {record["window_seconds"] for record in state.values()} == {
        3 * 3600,
        12 * 3600,
    }
    latest_manifest = max(
        config.backup_root.glob("*/manifest.json"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    manifest = json.loads(latest_manifest.read_text(encoding="utf-8"))
    assert manifest["status"] == "rolled_back"
    assert manifest["trial_state_mode"] == "existing-v2"
    _assert_pet_untouched(evidence)


FAILURE_STAGES = (
    "after_stop",
    "after_snapshot",
    "after_migrate",
    "after_install_activation_core.py",
    "after_install_activation_server.py",
    "after_install_sample_collector_server.py",
    "after_install_activation_deploy_gate.py",
    "after_install_pet_usage_db_admin.py",
    "after_install_upload_lease.py",
    "after_install_upload_identity_store.py",
    "after_install_upload_identity_db_admin.py",
    "after_install_activation_device_admin.py",
    "after_install_my_records.py",
    "after_unit_install",
    "after_daemon_reload",
    "after_cutover_wait",
    "after_start",
    "after_health",
)


@pytest.mark.parametrize("stage", FAILURE_STAGES)
def test_each_stage_failure_restores_compatible_pair_without_losing_new_trials(
    tmp_path: Path,
    stage: str,
) -> None:
    config, _unused, evidence = _clone(tmp_path)
    ops = FakeOps(config, fail_stage=stage)

    with pytest.raises(
        deploy.DeploymentError,
        match="compatible trial-state authority",
    ):
        deploy.apply(config, ops)

    _assert_legacy_pair(
        config,
        ops,
        evidence,
        expected_new_record=stage in {"after_start", "after_health"},
    )
    if stage not in {"after_stop"}:
        manifests = list(config.backup_root.glob("*/manifest.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["status"] == "rolled_back"
    if stage in {"after_start", "after_health"}:
        # The pre-cutover backup has only the two old owners.  The restored
        # authority retaining candidate-new proves rollback used current v2.
        before = next(
            config.backup_root.glob("*/trials.before-migrate.json")
        )
        assert set(json.loads(before.read_text(encoding="utf-8"))) == {
            "old-a",
            "old-b",
        }
        assert "candidate-new" in json.loads(
            config.state_file.read_text(encoding="utf-8")
        )


def test_invalid_current_v2_never_pairs_with_old_runtime(tmp_path: Path) -> None:
    config, _unused, evidence = _clone(tmp_path)
    ops = FakeOps(
        config,
        fail_stage="after_start",
        corrupt_candidate_state=True,
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="recovery did not complete",
    ):
        deploy.apply(config, ops)

    assert not ops.running
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() != evidence["old_core"]
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert type(state["candidate-new"]) is dict
    _assert_pet_untouched(evidence)


def test_deployment_lock_rejects_a_second_operator(tmp_path: Path) -> None:
    lock = tmp_path / "deploy.sqlite3"
    entered = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with deploy._deployment_lock(lock, timeout=1.0):
            entered.set()
            assert release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(timeout=3)
    try:
        with pytest.raises(
            deploy.DeploymentError,
            match="another deployment operator",
        ):
            with deploy._deployment_lock(lock, timeout=0.05):
                pass
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "pause_stage",
    ("after_stop", "after_migrate", "after_start", "after_health"),
)
def test_deploy_holds_the_exact_admin_lock_for_the_entire_transaction(
    tmp_path: Path,
    pause_stage: str,
) -> None:
    config, _unused, evidence = _clone(tmp_path)
    paused = threading.Event()
    release_deploy = threading.Event()
    contender_started = threading.Event()
    contender_entered = threading.Event()
    deploy_errors: list[BaseException] = []
    contender_errors: list[BaseException] = []

    class PausingOps(FakeOps):
        def checkpoint(self, stage: str) -> None:
            if stage == pause_stage:
                paused.set()
                if not release_deploy.wait(timeout=10):
                    raise RuntimeError("test did not release deployment")

    ops = PausingOps(config)

    def run_deploy() -> None:
        try:
            deploy.apply(config, ops)
        except BaseException as exc:
            deploy_errors.append(exc)

    admin = deploy._load_candidate_admin(config)

    def run_contender() -> None:
        contender_started.set()
        try:
            with admin._exclusive_admin_lock(config.state_file):
                contender_entered.set()
        except BaseException as exc:
            contender_errors.append(exc)

    deployment_thread = threading.Thread(target=run_deploy)
    deployment_thread.start()
    assert paused.wait(timeout=10)
    contender_thread = threading.Thread(target=run_contender)
    contender_thread.start()
    assert contender_started.wait(timeout=3)
    try:
        assert not contender_entered.wait(timeout=0.25)
    finally:
        release_deploy.set()
    deployment_thread.join(timeout=10)
    contender_thread.join(timeout=10)

    assert not deployment_thread.is_alive()
    assert not contender_thread.is_alive()
    assert deploy_errors == []
    assert contender_errors == []
    assert contender_entered.is_set()
    assert ops.running
    _assert_pet_untouched(evidence)


def test_lock_protocol_mismatch_is_rejected_before_stop(tmp_path: Path) -> None:
    config, ops, evidence = _clone(tmp_path)
    admin_text = config.candidate_admin.read_text(encoding="utf-8")
    config.candidate_admin.write_text(
        admin_text.replace(
            "_LOCK_PROTOCOL_VERSION = 3",
            "_LOCK_PROTOCOL_VERSION = 2",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="lock protocol mismatch",
    ):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert ops.running
    assert config.state_file.read_bytes() == evidence["old_state"]
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == evidence["old_core"]
    assert config.unit_file.read_bytes() == evidence["old_unit"]
    _assert_pet_untouched(evidence)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            " bidking-upload-identity",
            "",
            "StateDirectory=bidking-upload-identity",
        ),
        (
            "--upload-identity-mode compat",
            "--upload-identity-mode disabled",
            "upload identity mode mismatch",
        ),
        (
            "--upload-security-epoch 1",
            "--upload-security-epoch 2",
            "upload security epoch mismatch",
        ),
    ],
)
def test_upload_identity_unit_contract_fails_before_service_stop(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    text = config.candidate_unit_file.read_text(encoding="utf-8")
    assert old in text
    config.candidate_unit_file.write_text(text.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(deploy.DeploymentError, match=message):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]
    _assert_pet_untouched(evidence)


def test_current_upload_epoch_cannot_be_downgraded_before_service_stop(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    current = config.candidate_unit_file.read_text(encoding="utf-8").replace(
        "--upload-security-epoch 1",
        "--upload-security-epoch 2",
    )
    config.unit_file.write_text(current, encoding="utf-8")

    with pytest.raises(deploy.DeploymentError, match="upload security epoch downgrade"):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]
    _assert_pet_untouched(evidence)


def test_upload_epoch_receipt_is_monotonic_and_rollback_keeps_high_epoch(
    tmp_path: Path,
) -> None:
    config, _ops, evidence = _clone(tmp_path)
    config.unit_file.write_text(
        _unit_text(
            config.state_file,
            cutover=config.cutover_at,
            upload_identity_db=config.upload_identity_db_file,
            pet_usage_db=config.pet_usage_db_file,
            upload_epoch=2,
        ),
        encoding="utf-8",
    )
    config.candidate_unit_file.write_text(
        _unit_text(
            config.state_file,
            cutover=config.cutover_at,
            upload_identity_db=config.upload_identity_db_file,
            pet_usage_db=config.pet_usage_db_file,
            upload_epoch=3,
        ),
        encoding="utf-8",
    )
    config = dataclasses.replace(config, upload_security_epoch=3)
    ops = FakeOps(config, fail_stage="after_health")

    with pytest.raises(deploy.DeploymentError, match="deployment failed"):
        deploy.apply(config, ops)

    assert deploy._unit_upload_security_epoch(
        config.unit_file,
        missing_ok=False,
    ) == 3
    manifests = list(config.backup_root.glob("*/manifest.json"))
    assert len(manifests) == 1
    receipt = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert receipt["upload_security_epoch_before"] == 2
    assert receipt["upload_security_epoch_after"] == 3
    assert receipt["rollback_upload_security_epoch"] == 3
    assert _sha(evidence["upload_identity_db"]) == evidence["upload_identity_db_sha"]


def test_disabled_to_compat_failure_rolls_back_with_enabled_identity_contract(
    tmp_path: Path,
) -> None:
    config, _unused, evidence = _clone(tmp_path)
    config.unit_file.write_text(
        _unit_text(
            config.state_file,
            cutover=config.cutover_at,
            upload_identity_db=config.upload_identity_db_file,
            pet_usage_db=config.pet_usage_db_file,
            upload_mode="disabled",
            upload_epoch=0,
        ),
        encoding="utf-8",
    )
    ops = FakeOps(config, fail_stage="after_health")

    with pytest.raises(deploy.DeploymentError, match="deployment failed"):
        deploy.apply(config, ops)

    assert ops.running
    restored = config.unit_file.read_text(encoding="utf-8")
    assert "--upload-identity-mode compat" in restored
    assert "--upload-security-epoch 1" in restored
    receipt_path = next(config.backup_root.glob("*/manifest.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "rolled_back"
    assert receipt["upload_identity_mode_before"] == "disabled"
    assert receipt["upload_identity_mode_after"] == "compat"
    assert receipt["rollback_upload_identity_mode"] == "compat"
    assert receipt["upload_security_epoch_before"] == 0
    assert receipt["upload_security_epoch_after"] == 1
    assert receipt["rollback_upload_security_epoch"] == 1
    assert _sha(evidence["upload_identity_db"]) == evidence["upload_identity_db_sha"]


def test_disabled_to_compat_success_records_identity_mode_transition(
    tmp_path: Path,
) -> None:
    config, _unused, evidence = _clone(tmp_path)
    config.unit_file.write_text(
        _unit_text(
            config.state_file,
            cutover=config.cutover_at,
            upload_identity_db=config.upload_identity_db_file,
            pet_usage_db=config.pet_usage_db_file,
            upload_mode="disabled",
            upload_epoch=0,
        ),
        encoding="utf-8",
    )
    ops = FakeOps(config)

    receipt_path = deploy.apply(config, ops)

    assert ops.running
    installed = config.unit_file.read_text(encoding="utf-8")
    assert "--upload-identity-mode compat" in installed
    assert "--upload-security-epoch 1" in installed
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "deployed"
    assert receipt["upload_identity_mode_before"] == "disabled"
    assert receipt["upload_identity_mode_after"] == "compat"
    assert receipt["upload_security_epoch_before"] == 0
    assert receipt["upload_security_epoch_after"] == 1
    assert receipt["runtime_contract"]["upload_identity_mode"] == "compat"
    assert _sha(evidence["upload_identity_db"]) == evidence["upload_identity_db_sha"]


def test_corrupt_upload_identity_database_fails_before_service_stop(tmp_path: Path) -> None:
    config, ops, evidence = _clone(tmp_path)
    config.upload_identity_db_file.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(deploy.DeploymentError, match="command failed"):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_server_update_allows_only_the_two_explicit_operator_tools() -> None:
    text = (SCRIPTS / "server_update.sh").read_text(encoding="utf-8")
    assert "activation_state_aware_deploy.py trial_state_admin.py" in text
    assert "activation_state_aware_deploy.py" not in text.split("FILES=(", 1)[1].split(
        ")", 1
    )[0]


def test_candidate_unit_requires_explicit_upload_lease_seconds(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    text = config.candidate_unit_file.read_text(encoding="utf-8")
    config.candidate_unit_file.write_text(
        text.replace(" --upload-lease-seconds 300", "", 1),
        encoding="utf-8",
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="upload-lease-seconds|upload lease seconds",
    ):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_activation_security_epoch_cannot_be_downgraded(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    current = config.unit_file.read_text(encoding="utf-8").replace(
        "--min-client-security-epoch 1",
        "--min-client-security-epoch 2",
        1,
    )
    config.unit_file.write_text(current, encoding="utf-8")

    with pytest.raises(
        deploy.DeploymentError,
        match="activation security epoch downgrade",
    ):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_success_receipt_records_protected_state_bundle(tmp_path: Path) -> None:
    config, ops, _evidence = _clone(tmp_path)

    manifest_path = deploy.apply(config, ops)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["protected_state_before"]["schema_version"] == 1
    assert len(manifest["protected_state_before"]["bundle_sha256"]) == 64
    assert manifest["protected_state_before"]["role_count"] == 21
    assert manifest["protected_state_after"]["schema_version"] == 1
    assert manifest["unit_readback"]["schema_version"] == 1
    assert manifest["unit_readback"]["dropin_count"] == 0
    assert (
        manifest["protected_state_before"]["immutable_bundle_sha256"]
        == manifest["protected_state_after"]["immutable_bundle_sha256"]
    )
    assert (
        manifest["protected_state_before"]["bundle_sha256"]
        != manifest["protected_state_after"]["bundle_sha256"]
    )
    assert str(tmp_path) not in manifest_path.read_text(encoding="utf-8")


def test_only_live_activation_authorities_change_mutability_contract(
    tmp_path: Path,
) -> None:
    config, _ops, _evidence = _clone(tmp_path)
    contract = deploy._validate_candidate_unit(config)
    mutable = {
        role: is_mutable
        for role, _path, is_mutable in contract.protected_paths
    }

    assert len(mutable) == 21
    assert mutable["bindings"] is True
    assert mutable["paid_starts"] is True
    for role in (
        "activation_key",
        "activation_http_e2ee_key",
        "master_key",
        "master_key_v2",
        "revoked",
        "device_blocks",
        "device_block_key",
        "dashboard_pass",
        "export_key",
        "version_manifest",
    ):
        assert mutable[role] is False


def test_protected_snapshot_rejects_metadata_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "revoked.txt"
    target.write_text("member-a\n", encoding="utf-8")
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("revoked", target, False),),
    )
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    real_read = deploy.os.read
    changed = False

    def read_then_change_metadata(descriptor: int, size: int) -> bytes:
        nonlocal changed
        raw = real_read(descriptor, size)
        metadata = os.fstat(descriptor)
        if not changed and (metadata.st_dev, metadata.st_ino) == target_identity:
            changed = True
            current = target.stat()
            os.utime(
                target,
                ns=(current.st_atime_ns, current.st_mtime_ns + 2_000_000_000),
            )
        return raw

    monkeypatch.setattr(deploy.os, "read", read_then_change_metadata)

    with pytest.raises(
        deploy.DeploymentError,
        match="protected state changed while reading",
    ):
        deploy._protected_state_snapshot(contract)


def test_protected_snapshot_binds_digest_to_the_open_file_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "revoked.txt"
    original = b"authority-A\n"
    replacement = b"authority-B\n"
    assert len(original) == len(replacement)
    target.write_bytes(original)
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("revoked", target, False),),
    )
    expected = deploy._protected_state_snapshot(contract)
    real_read_bytes = Path.read_bytes

    def read_replaced_then_restored(path: Path) -> bytes:
        if path != target:
            return real_read_bytes(path)
        before = path.stat()
        with path.open("wb") as stream:
            stream.write(replacement)
        observed = real_read_bytes(path)
        with path.open("wb") as stream:
            stream.write(original)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert real_read_bytes(path) == original
        return observed

    monkeypatch.setattr(Path, "read_bytes", read_replaced_then_restored)

    assert deploy._protected_state_snapshot(contract) == expected


def test_protected_handle_reader_retries_eintr_and_collects_short_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "revoked.txt"
    target.write_bytes(b"authority-state-with-several-short-chunks\n")
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("revoked", target, False),),
    )
    expected = deploy._protected_state_snapshot(contract)
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    real_read = deploy.os.read
    calls = 0

    def interrupted_then_short(descriptor: int, size: int) -> bytes:
        nonlocal calls
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != target_identity:
            return real_read(descriptor, size)
        calls += 1
        if calls == 1:
            raise InterruptedError(errno.EINTR, "synthetic signal")
        return real_read(descriptor, min(size, 3))

    monkeypatch.setattr(deploy.os, "read", interrupted_then_short)

    assert deploy._protected_state_snapshot(contract) == expected
    assert calls > 3


def test_protected_handle_reader_hashes_large_state_incrementally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "samples.jsonl"
    payload = (b"bounded-protected-state\n" * 262_145) + b"tail\n"
    assert len(payload) > 4 * 1024 * 1024
    target.write_bytes(payload)
    expected_digest = hashlib.sha256(payload).hexdigest()
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("samples", target, True),),
    )
    real_sha256 = deploy.hashlib.sha256
    constructor_sizes: list[int] = []

    def reject_buffered_hash(data: bytes = b""):
        constructor_sizes.append(len(data))
        if len(data) > 1024 * 1024:
            raise AssertionError("protected state was buffered before hashing")
        return real_sha256(data)

    monkeypatch.setattr(deploy.hashlib, "sha256", reject_buffered_hash)

    summary = deploy._protected_state_snapshot(contract)
    metadata = target.stat()
    expected_entry = {
        "role": "samples",
        "present": True,
        "mutable": True,
        "sha256": expected_digest,
        "size": len(payload),
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
        "uid": int(metadata.st_uid),
        "gid": int(metadata.st_gid),
    }
    expected_bundle = real_sha256(
        json.dumps(
            [expected_entry],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()

    assert summary["present_count"] == 1
    assert summary["missing_count"] == 0
    assert summary["bundle_sha256"] == expected_bundle
    assert expected_digest not in json.dumps(summary, sort_keys=True)
    assert max(constructor_sizes) < 1024 * 1024


def test_apply_preserves_live_sample_append_during_post_health_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ops, _evidence = _clone(tmp_path)
    samples = _protected_path(config, "samples")
    original = (b'{"sample":1}\n' * 100_000)
    appended = b'{"sample":2}\n'
    samples.write_bytes(original)
    samples_identity = (samples.stat().st_dev, samples.stat().st_ino)
    real_read = deploy.os.read
    append_count = 0

    def append_after_candidate_read(descriptor: int, size: int) -> bytes:
        nonlocal append_count
        chunk = real_read(descriptor, size)
        metadata = os.fstat(descriptor)
        if (
            ops.candidate_started
            and append_count == 0
            and (metadata.st_dev, metadata.st_ino) == samples_identity
        ):
            append_count += 1
            with samples.open("ab") as stream:
                stream.write(appended)
                stream.flush()
                os.fsync(stream.fileno())
        return chunk

    monkeypatch.setattr(deploy.os, "read", append_after_candidate_read)

    manifest_path = deploy.apply(config, ops)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert append_count == 1
    assert samples.read_bytes() == original + appended
    assert manifest["status"] == "deployed"
    assert ops.running and ops.candidate_started


def test_rollback_preserves_live_sample_append_during_recovery_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    ops.fail_stage = "after_health"
    samples = _protected_path(config, "samples")
    original = b'{"sample":1}\n'
    appended = b'{"sample":2}\n'
    samples.write_bytes(original)
    samples_identity = (samples.stat().st_dev, samples.stat().st_ino)
    real_read = deploy.os.read
    append_count = 0

    def append_during_recovery_read(descriptor: int, size: int) -> bytes:
        nonlocal append_count
        chunk = real_read(descriptor, size)
        metadata = os.fstat(descriptor)
        if (
            ops.candidate_started
            and append_count == 0
            and (metadata.st_dev, metadata.st_ino) == samples_identity
        ):
            append_count += 1
            with samples.open("ab") as stream:
                stream.write(appended)
                stream.flush()
                os.fsync(stream.fileno())
        return chunk

    monkeypatch.setattr(deploy.os, "read", append_during_recovery_read)

    with pytest.raises(deploy.DeploymentError, match="old code/unit"):
        deploy.apply(config, ops)

    receipt_path = next(config.backup_root.glob("*/manifest.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert append_count == 1
    assert samples.read_bytes() == original + appended
    assert receipt["status"] == "rolled_back"
    assert ops.running
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == evidence["old_core"]


def test_apply_allows_append_only_file_created_during_candidate_window(
    tmp_path: Path,
) -> None:
    config, _unused, _evidence = _clone(tmp_path)
    quarantine = _protected_path(config, "samples_quarantine")
    quarantine.unlink(missing_ok=True)
    appended = b'{"quarantine":1}\n'

    class CreateAppendOnlyOps(FakeOps):
        def checkpoint(self, stage: str) -> None:
            if stage == "after_health":
                with quarantine.open("ab") as stream:
                    stream.write(appended)

    ops = CreateAppendOnlyOps(config)
    manifest_path = deploy.apply(config, ops)

    assert quarantine.read_bytes() == appended
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "deployed"
    assert ops.running and ops.candidate_started


@pytest.mark.parametrize("mutation", ("rewrite", "truncate", "replace"))
def test_append_only_prefix_mutation_blocks_recovery_start(
    tmp_path: Path,
    mutation: str,
) -> None:
    config, _unused, evidence = _clone(tmp_path)
    samples = _protected_path(config, "samples")
    original = b'{"sample":"before"}\n'
    samples.write_bytes(original)

    class MutateSamplePrefixOps(FakeOps):
        def checkpoint(self, stage: str) -> None:
            if stage != "after_health":
                return
            if mutation == "rewrite":
                samples.write_bytes(b'{"sample":"after!"}\n')
                assert samples.stat().st_size == len(original)
            elif mutation == "truncate":
                samples.write_bytes(b"")
            else:
                replacement = samples.with_suffix(".replacement")
                replacement.write_bytes(original)
                replacement.replace(samples)

    ops = MutateSamplePrefixOps(config)
    with pytest.raises(
        deploy.DeploymentError,
        match="recovery did not complete",
    ):
        deploy.apply(config, ops)

    assert not ops.running
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == evidence["old_core"]


def test_append_only_snapshot_rejects_incomplete_initial_record(
    tmp_path: Path,
) -> None:
    target = tmp_path / "samples.jsonl"
    target.write_bytes(b'{"partial":true}')
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("samples", target, True),),
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="append-only protected state has an incomplete record: samples",
    ):
        deploy._protected_state_snapshot(contract)


def test_append_only_snapshot_rejects_non_exact_boundaries_without_magic(
    tmp_path: Path,
) -> None:
    target = tmp_path / "samples.jsonl"
    target.write_bytes(b'{"sample":1}\n')
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("samples", target, True),),
    )
    _summary, _presence, boundaries = deploy._capture_protected_state(contract)
    magic_calls = 0

    class MappingSubclass(dict):
        def __iter__(self):
            nonlocal magic_calls
            magic_calls += 1
            raise RuntimeError("must not iterate a mapping subclass")

    class MagicBoundary:
        @property
        def present(self) -> bool:
            nonlocal magic_calls
            magic_calls += 1
            raise RuntimeError("must not inspect an untrusted boundary")

    with pytest.raises(
        deploy.DeploymentError,
        match="append-only protected snapshot is incomplete",
    ):
        deploy._capture_protected_state(
            contract,
            append_only_boundaries=MappingSubclass(boundaries),
        )

    invalid = dict(boundaries)
    invalid["samples"] = MagicBoundary()
    with pytest.raises(
        deploy.DeploymentError,
        match="append-only protected boundary is invalid: samples",
    ):
        deploy._capture_protected_state(
            contract,
            append_only_boundaries=invalid,
        )
    assert magic_calls == 0


@pytest.mark.parametrize(
    "invalid_kind",
    ("oversized", "bool", "bytes_subclass", "bytearray", "memoryview", "magic"),
)
def test_append_only_prefix_reader_rejects_invalid_chunks_without_magic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_kind: str,
) -> None:
    target = tmp_path / "samples.jsonl"
    target.write_bytes(b'{"sample":1}\n')
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("samples", target, True),),
    )
    _summary, _presence, boundaries = deploy._capture_protected_state(contract)
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    real_read = deploy.os.read
    magic_calls = 0

    class BytesSubclass(bytes):
        pass

    class MagicChunk:
        def __bool__(self) -> bool:
            nonlocal magic_calls
            magic_calls += 1
            raise RuntimeError("must not truth-test an untrusted read result")

        def __len__(self) -> int:
            nonlocal magic_calls
            magic_calls += 1
            raise RuntimeError("must not measure an untrusted read result")

    values: dict[str, object] = {
        "oversized": b"x" * (target.stat().st_size + 1),
        "bool": False,
        "bytes_subclass": BytesSubclass(b'{"sample":1}\n'),
        "bytearray": bytearray(b'{"sample":1}\n'),
        "memoryview": memoryview(b'{"sample":1}\n'),
        "magic": MagicChunk(),
    }

    def invalid_read(descriptor: int, size: int):
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != target_identity:
            return real_read(descriptor, size)
        return values[invalid_kind]

    monkeypatch.setattr(deploy.os, "read", invalid_read)

    with pytest.raises(
        deploy.DeploymentError,
        match="protected state cannot be read: samples",
    ):
        deploy._capture_protected_state(
            contract,
            append_only_boundaries=boundaries,
        )
    assert magic_calls == 0


@pytest.mark.parametrize(
    "invalid_kind",
    ("oversized", "bool", "bytes_subclass", "bytearray", "memoryview", "magic"),
)
def test_protected_handle_reader_rejects_non_exact_or_oversized_chunks_without_magic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_kind: str,
) -> None:
    target = tmp_path / "samples.jsonl"
    target.write_bytes(b"authority-state\n")
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("samples", target, True),),
    )
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    real_read = deploy.os.read
    magic_calls = 0

    class BytesSubclass(bytes):
        pass

    class MagicChunk:
        def __bool__(self) -> bool:
            nonlocal magic_calls
            magic_calls += 1
            raise RuntimeError("must not truth-test an untrusted read result")

        def __len__(self) -> int:
            nonlocal magic_calls
            magic_calls += 1
            raise RuntimeError("must not measure an untrusted read result")

    values: dict[str, object] = {
        "oversized": b"x" * (deploy._PROTECTED_READ_CHUNK_SIZE + 1),
        "bool": False,
        "bytes_subclass": BytesSubclass(b"authority-state\n"),
        "bytearray": bytearray(b"authority-state\n"),
        "memoryview": memoryview(b"authority-state\n"),
        "magic": MagicChunk(),
    }

    def invalid_read(descriptor: int, size: int):
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != target_identity:
            return real_read(descriptor, size)
        return values[invalid_kind]

    monkeypatch.setattr(deploy.os, "read", invalid_read)

    with pytest.raises(
        deploy.DeploymentError,
        match="protected state cannot be read: samples",
    ):
        deploy._protected_state_snapshot(contract)
    assert magic_calls == 0


@pytest.mark.parametrize(
    "mutation",
    ("short_read", "append", "truncate", "path_swap", "handle_mode"),
)
def test_protected_handle_reader_rejects_unstable_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    target = tmp_path / "revoked.txt"
    target.write_bytes(b"authority-state\n")
    replacement = tmp_path / "replacement.txt"
    replacement.write_bytes(b"other-authority\n")
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    replacement_metadata = replacement.stat()
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("revoked", target, False),),
    )
    real_read = deploy.os.read
    real_stat = deploy.os.stat
    mutated = False

    def unstable_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        metadata = os.fstat(descriptor)
        if mutated or (metadata.st_dev, metadata.st_ino) != target_identity:
            return real_read(descriptor, size)
        mutated = True
        if mutation == "short_read":
            return b""
        raw = real_read(descriptor, size)
        if mutation == "append":
            with target.open("ab") as stream:
                stream.write(b"x")
        elif mutation == "truncate":
            with target.open("wb"):
                pass
        elif mutation == "handle_mode":
            os.chmod(target, 0o444)
        return raw

    def swapped_path_stat(path, *args, **kwargs):
        value = real_stat(path, *args, **kwargs)
        if (
            mutation == "path_swap"
            and mutated
            and Path(path) == target
            and kwargs.get("follow_symlinks") is False
        ):
            return replacement_metadata
        return value

    monkeypatch.setattr(deploy.os, "read", unstable_read)
    monkeypatch.setattr(deploy.os, "stat", swapped_path_stat)
    try:
        with pytest.raises(
            deploy.DeploymentError,
            match="protected state (?:changed|path changed)",
        ):
            deploy._protected_state_snapshot(contract)
    finally:
        if target.exists():
            os.chmod(target, 0o600)


def test_protected_handle_reader_rejects_nonregular_target(tmp_path: Path) -> None:
    target = tmp_path / "revoked.txt"
    target.mkdir()
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("revoked", target, False),),
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="protected state is not a regular file",
    ):
        deploy._protected_state_snapshot(contract)


def test_protected_handle_reader_rejects_no_follow_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "revoked.txt"
    target.write_bytes(b"authority-state\n")
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("revoked", target, False),),
    )
    real_open = deploy.os.open
    synthetic_nonblock = 1 << 28
    monkeypatch.setattr(
        deploy.os,
        "O_NONBLOCK",
        synthetic_nonblock,
        raising=False,
    )

    def reject_no_follow(path, flags, *args, **kwargs):
        if Path(path) == target:
            assert flags & synthetic_nonblock
            if getattr(os, "O_NOFOLLOW", 0):
                assert flags & os.O_NOFOLLOW
            raise OSError(errno.ELOOP, "synthetic no-follow rejection")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(deploy.os, "open", reject_no_follow)

    with pytest.raises(
        deploy.DeploymentError,
        match="protected state cannot be opened",
    ):
        deploy._protected_state_snapshot(contract)


def test_protected_handle_reader_rejects_path_disappearance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "revoked.txt"
    target.write_bytes(b"authority-state\n")
    contract = deploy.UnitContract(
        upload_security_epoch=deploy.EpochState(True, 1),
        activation_security_epoch=deploy.EpochState(True, 1),
        protected_paths=(("revoked", target, False),),
    )
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    real_read = deploy.os.read
    real_stat = deploy.os.stat
    read_started = False

    def record_read(descriptor: int, size: int) -> bytes:
        nonlocal read_started
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == target_identity:
            read_started = True
        return real_read(descriptor, size)

    def disappeared_path_stat(path, *args, **kwargs):
        if (
            read_started
            and Path(path) == target
            and kwargs.get("follow_symlinks") is False
        ):
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(deploy.os, "read", record_read)
    monkeypatch.setattr(deploy.os, "stat", disappeared_path_stat)

    with pytest.raises(
        deploy.DeploymentError,
        match="protected state path changed after reading",
    ):
        deploy._protected_state_snapshot(contract)


def test_protected_unit_path_does_not_resolve_the_final_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_resolve(_path: Path, *args, **kwargs) -> Path:
        del args, kwargs
        raise AssertionError("protected unit path must not call Path.resolve")

    monkeypatch.setattr(Path, "resolve", reject_resolve)

    assert deploy._resolve_unit_path(
        "authority.json",
        working_directory=tmp_path,
    ) == tmp_path / "authority.json"


def test_backup_and_receipt_are_root_only_by_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, ops, _evidence = _clone(tmp_path)
    chmod_calls: dict[Path, int] = {}
    real_chmod = deploy.os.chmod

    def recording_chmod(path: str | os.PathLike[str], mode: int) -> None:
        chmod_calls[Path(path).resolve()] = mode
        real_chmod(path, mode)

    monkeypatch.setattr(deploy.os, "chmod", recording_chmod)

    manifest_path = deploy.apply(config, ops)

    assert chmod_calls[manifest_path.parent.resolve()] == 0o700
    for path in manifest_path.parent.rglob("*"):
        if path.is_dir():
            assert chmod_calls[path.resolve()] == 0o700
        elif path.is_file():
            assert chmod_calls[path.resolve()] == 0o600
        if os.name == "posix" and path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    if os.name == "posix":
        assert stat.S_IMODE(manifest_path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("User=ubuntu", "User=root", "service user mismatch"),
        ("Group=ubuntu", "Group=root", "service group mismatch"),
        ("UMask=0077", "UMask=0022", "UMask must be 0077"),
        (
            "StateDirectoryMode=0700",
            "StateDirectoryMode=0755",
            "StateDirectoryMode must be 0700",
        ),
        (
            "--upload-lease-seconds 300",
            "--upload-lease-seconds 301",
            "upload lease seconds mismatch",
        ),
        (
            "--min-client-version 0.3.1",
            "--min-client-version 0.3.0",
            "minimum client version mismatch",
        ),
        (
            "--v2-min-version 0.2.7",
            "--v2-min-version 0.2.6",
            "v2 minimum version mismatch",
        ),
        (
            "--pet-usage-lease-seconds 15",
            "--pet-usage-lease-seconds 61",
            "Pet lease seconds mismatch",
        ),
    ],
)
def test_candidate_unit_runtime_contract_is_exact_once_before_stop(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    text = config.candidate_unit_file.read_text(encoding="utf-8")
    assert text.count(old) == 1
    config.candidate_unit_file.write_text(
        text.replace(old, new, 1),
        encoding="utf-8",
    )

    with pytest.raises(deploy.DeploymentError, match=message):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_upload_epoch_increase_requires_an_explicit_bootstrap_unit(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    current = config.unit_file.read_text(encoding="utf-8")
    config.unit_file.write_text(
        current.replace(" --upload-security-epoch 1", "", 1),
        encoding="utf-8",
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="upload security epoch bootstrap is required",
    ):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_activation_epoch_increase_requires_an_explicit_bootstrap_unit(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    current = config.unit_file.read_text(encoding="utf-8")
    config.unit_file.write_text(
        current.replace(" --min-client-security-epoch 1", "", 1),
        encoding="utf-8",
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="activation security epoch bootstrap is required",
    ):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_candidate_working_directory_is_explicit_and_exact(tmp_path: Path) -> None:
    config, ops, evidence = _clone(tmp_path)
    text = config.candidate_unit_file.read_text(encoding="utf-8")
    config.candidate_unit_file.write_text(
        text.replace(
            f"WorkingDirectory={config.working_directory.as_posix()}",
            f"WorkingDirectory={(tmp_path / 'other').as_posix()}",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="working directory mismatch",
    ):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_current_unit_service_identity_is_checked_before_stop(tmp_path: Path) -> None:
    config, ops, evidence = _clone(tmp_path)
    current = config.unit_file.read_text(encoding="utf-8")
    config.unit_file.write_text(
        current.replace("UMask=0077", "UMask=0022", 1),
        encoding="utf-8",
    )

    with pytest.raises(deploy.DeploymentError, match="UMask must be 0077"):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_disabled_epoch_zero_can_bootstrap_a_current_unit_without_upload_epoch(
    tmp_path: Path,
) -> None:
    config, _ops, _evidence = _clone(tmp_path)
    current = config.unit_file.read_text(encoding="utf-8")
    config.unit_file.write_text(
        current.replace(
            "--upload-identity-mode compat",
            "--upload-identity-mode disabled",
            1,
        ).replace(" --upload-security-epoch 1", "", 1),
        encoding="utf-8",
    )
    candidate = config.candidate_unit_file.read_text(encoding="utf-8")
    candidate = candidate.replace(
        "--upload-identity-mode compat",
        "--upload-identity-mode disabled",
        1,
    ).replace(
        "--upload-security-epoch 1",
        "--upload-security-epoch 0",
        1,
    )
    config.candidate_unit_file.write_text(candidate, encoding="utf-8")
    config = dataclasses.replace(
        config,
        upload_identity_mode="disabled",
        upload_security_epoch=0,
    )
    ops = FakeOps(config)

    manifest_path = deploy.apply(config, ops)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["upload_security_epoch_before"] == 0
    assert manifest["upload_security_epoch_after"] == 0
    assert manifest["runtime_contract"]["upload_identity_mode"] == "disabled"


def test_upload_identity_mode_cannot_be_downgraded_before_service_stop(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    candidate = config.candidate_unit_file.read_text(encoding="utf-8")
    candidate = candidate.replace(
        "--upload-identity-mode compat",
        "--upload-identity-mode disabled",
        1,
    ).replace(
        "--upload-security-epoch 1",
        "--upload-security-epoch 0",
        1,
    )
    config.candidate_unit_file.write_text(candidate, encoding="utf-8")
    config = dataclasses.replace(
        config,
        upload_identity_mode="disabled",
        upload_security_epoch=0,
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="upload identity mode downgrade",
    ):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]


def test_dual_epoch_rollback_keeps_both_candidate_epochs(tmp_path: Path) -> None:
    config, _ops, _evidence = _clone(tmp_path)
    current = config.unit_file.read_text(encoding="utf-8")
    current = current.replace(
        "--upload-security-epoch 1",
        "--upload-security-epoch 2",
        1,
    ).replace(
        "--min-client-security-epoch 1",
        "--min-client-security-epoch 2",
        1,
    )
    candidate = config.candidate_unit_file.read_text(encoding="utf-8")
    candidate = candidate.replace(
        "--upload-security-epoch 1",
        "--upload-security-epoch 3",
        1,
    ).replace(
        "--min-client-security-epoch 1",
        "--min-client-security-epoch 3",
        1,
    )
    config.unit_file.write_text(current, encoding="utf-8")
    config.candidate_unit_file.write_text(candidate, encoding="utf-8")
    config = dataclasses.replace(
        config,
        upload_security_epoch=3,
        min_client_security_epoch=3,
    )
    ops = FakeOps(config, fail_stage="after_health")

    with pytest.raises(deploy.DeploymentError, match="deployment failed"):
        deploy.apply(config, ops)

    assert deploy._unit_epoch_state(
        config.unit_file,
        "--upload-security-epoch",
        missing_ok=False,
    ).value == 3
    assert deploy._unit_epoch_state(
        config.unit_file,
        "--min-client-security-epoch",
        missing_ok=False,
    ).value == 3
    receipt_path = next(config.backup_root.glob("*/manifest.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["rollback_upload_security_epoch"] == 3
    assert receipt["rollback_activation_security_epoch"] == 3


def test_effective_unit_readback_failure_rolls_back_before_candidate_start(
    tmp_path: Path,
) -> None:
    config, _unused, evidence = _clone(tmp_path)

    class ReadbackFailingOps(FakeOps):
        def __init__(self, current: deploy.Config) -> None:
            super().__init__(current)
            self.readback_count = 0

        def unit_readback(self, current: deploy.Config) -> dict[str, object]:
            self.readback_count += 1
            if self.readback_count == 1:
                raise deploy.DeploymentError("effective systemd service contract mismatch")
            return super().unit_readback(current)

    ops = ReadbackFailingOps(config)

    with pytest.raises(deploy.DeploymentError, match="deployment failed"):
        deploy.apply(config, ops)

    assert ops.running
    assert not ops.candidate_started
    assert ops.readback_count == 2
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == evidence["old_core"]


def test_immutable_protected_state_drift_blocks_recovery_start(
    tmp_path: Path,
) -> None:
    config, _unused, evidence = _clone(tmp_path)
    revoked = config.state_file.with_name("revoked.txt")
    revoked.write_text("before\n", encoding="utf-8")

    class MutatingOps(FakeOps):
        def checkpoint(self, stage: str) -> None:
            if stage == "after_health":
                revoked.write_text("after\n", encoding="utf-8")

    ops = MutatingOps(config)

    with pytest.raises(
        deploy.DeploymentError,
        match="recovery did not complete",
    ):
        deploy.apply(config, ops)

    assert not ops.running
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == evidence["old_core"]


@pytest.mark.parametrize(
    ("role", "initial_value", "candidate_value"),
    (
        ("bindings", None, {"member-a": "device-a"}),
        (
            "paid_starts",
            {"member-a": 1},
            {"member-a": 1, "member-b": 2},
        ),
    ),
)
@pytest.mark.parametrize("rollback", (False, True), ids=("deployed", "rolled-back"))
def test_live_activation_authority_writes_are_preserved_across_deploy_and_rollback(
    tmp_path: Path,
    role: str,
    initial_value: dict[str, object] | None,
    candidate_value: dict[str, object],
    rollback: bool,
) -> None:
    config, _unused, evidence = _clone(tmp_path)
    target = config.state_file.with_name(f"{role}.json")
    if initial_value is not None:
        activation_server_product._atomic_write_json(target, initial_value)

    class AuthorityWritingOps(FakeOps):
        def checkpoint(self, stage: str) -> None:
            if stage != "after_health":
                return
            activation_server_product._atomic_write_json(target, candidate_value)
            if rollback:
                raise RuntimeError("fail after legitimate online authority write")

    ops = AuthorityWritingOps(config)
    if rollback:
        with pytest.raises(deploy.DeploymentError, match="deployment failed"):
            deploy.apply(config, ops)
        assert ops.running
        assert (
            config.deployed_dir / "activation_core.py"
        ).read_bytes() == evidence["old_core"]
        receipt_path = next(config.backup_root.glob("*/manifest.json"))
    else:
        receipt_path = deploy.apply(config, ops)
        assert ops.running
        assert ops.candidate_started

    assert json.loads(target.read_text(encoding="utf-8")) == candidate_value
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == ("rolled_back" if rollback else "deployed")
    assert (
        receipt["protected_state_before"]["immutable_bundle_sha256"]
        == receipt["protected_state_after"]["immutable_bundle_sha256"]
    )
    assert (
        receipt["protected_state_before"]["bundle_sha256"]
        != receipt["protected_state_after"]["bundle_sha256"]
    )


@pytest.mark.parametrize(
    "mutation",
    ("delete_present", "create_read_only"),
)
@pytest.mark.parametrize("rollback", (False, True), ids=("deployed", "rolled-back"))
def test_live_activation_authority_invalid_transition_blocks_service_recovery(
    tmp_path: Path,
    mutation: str,
    rollback: bool,
) -> None:
    config, _unused, evidence = _clone(tmp_path)
    target = config.state_file.with_name("bindings.json")
    if mutation == "delete_present":
        activation_server_product._atomic_write_json(
            target,
            {"member-a": "device-a"},
        )

    class InvalidAuthorityOps(FakeOps):
        def checkpoint(self, stage: str) -> None:
            if stage != "after_health":
                return
            if mutation == "delete_present":
                target.unlink()
            else:
                activation_server_product._atomic_write_json(
                    target,
                    {"member-a": "device-a"},
                )
                os.chmod(target, 0o444)
            if rollback:
                raise RuntimeError("fail after invalid online authority mutation")

    ops = InvalidAuthorityOps(config)
    with pytest.raises(
        deploy.DeploymentError,
        match="recovery did not complete",
    ):
        deploy.apply(config, ops)

    assert not ops.running
    assert (
        config.deployed_dir / "activation_core.py"
    ).read_bytes() == evidence["old_core"]
    manifest_path = next(config.backup_root.glob("*/manifest.json"))
    receipt = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "snapshotted"
    assert set(receipt["protected_state_before"]) == {
        "schema_version",
        "role_count",
        "present_count",
        "missing_count",
        "bundle_sha256",
        "immutable_bundle_sha256",
        "upload_identity_schema_version",
    }
    serialized = json.dumps(receipt, sort_keys=True)
    assert "member-a" not in serialized
    assert "device-a" not in serialized
    assert str(target) not in serialized


def test_candidate_upload_identity_schema_must_remain_exact_v3(
    tmp_path: Path,
) -> None:
    config, ops, evidence = _clone(tmp_path)
    store = config.candidate_dir / "upload_identity_store.py"
    text = store.read_text(encoding="utf-8")
    assert "SCHEMA_VERSION = 3" in text
    store.write_text(
        text.replace("SCHEMA_VERSION = 3", "SCHEMA_VERSION = 2", 1),
        encoding="utf-8",
    )

    with pytest.raises(
        deploy.DeploymentError,
        match="upload identity schema mismatch",
    ):
        deploy.apply(config, ops)

    assert ops.stop_count == ops.start_count == 0
    assert config.state_file.read_bytes() == evidence["old_state"]
