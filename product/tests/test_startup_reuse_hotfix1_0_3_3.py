from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
STARTUP = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
TRUST_BOOTSTRAP = ROOT / "native" / "bidking_trust_bootstrap" / "src" / "main.rs"
LOCAL_COMPAT_LAUNCHER = ROOT / "scripts" / "local_compat_launcher.py"


def _powershell() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    assert executable is not None
    return executable


def _function(source: str, name: str) -> str:
    match = re.search(
        rf"^function {re.escape(name)}\b.*?^\}}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, name
    return match.group(0)


def _run_ps(body: str, *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            body,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _bootstrap_python(source: str) -> str:
    marker = 'const BOOTSTRAP: &str = r#"\n'
    start = source.index(marker) + len(marker)
    return source[start : source.index('\n"#;', start)]


def _load_actual_local_compat_launcher(
    monkeypatch: pytest.MonkeyPatch,
    native: types.SimpleNamespace,
) -> types.ModuleType:
    """Load the shipped launcher source with only its native ABI dependency faked."""
    monkeypatch.setitem(sys.modules, "bidking_kernel", native)
    spec = importlib.util.spec_from_file_location(
        "actual_local_compat_launcher", LOCAL_COMPAT_LAUNCHER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _native_lifecycle(
    run_client_lifecycle: object,
    *,
    contract: tuple[int, str, bool] = (
        2,
        "__BIDKING_NATIVE_LIFECYCLE_GENERATION__",
        True,
    ),
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        client_lifecycle_contract=lambda: contract,
        run_client_lifecycle=run_client_lifecycle,
    )


def test_actual_launcher_launch_exact_builtin_busy_is_idempotent_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def busy(**_kwargs: object) -> int:
        raise RuntimeError("client_lifecycle_busy")

    launcher = _load_actual_local_compat_launcher(monkeypatch, _native_lifecycle(busy))

    assert launcher._run_native("launch", package_root="fixture") == 0
    assert capsys.readouterr().err == ""


def test_actual_launcher_diagnostics_busy_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def busy(**_kwargs: object) -> int:
        raise RuntimeError("client_lifecycle_busy")

    launcher = _load_actual_local_compat_launcher(monkeypatch, _native_lifecycle(busy))

    assert launcher._run_native("diagnostics", package_root="fixture") == 1
    captured = capsys.readouterr()
    assert captured.err == launcher._SAFE_FAILURE + "\n"
    assert "client_lifecycle_busy" not in captured.err


def test_actual_launcher_launch_other_runtime_error_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def invalid(**_kwargs: object) -> int:
        raise RuntimeError("client_lifecycle_payload_invalid")

    launcher = _load_actual_local_compat_launcher(monkeypatch, _native_lifecycle(invalid))

    assert launcher._run_native("launch", package_root="fixture") == 1
    captured = capsys.readouterr()
    assert captured.err == launcher._SAFE_FAILURE + "\n"
    assert "client_lifecycle_payload_invalid" not in captured.err


def test_actual_launcher_contract_mismatch_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _load_actual_local_compat_launcher(
        monkeypatch,
        _native_lifecycle(lambda **_kwargs: 0, contract=(1, "wrong", True)),
    )

    assert launcher._run_native("launch", package_root="fixture") == 1
    captured = capsys.readouterr()
    assert captured.err == launcher._SAFE_FAILURE + "\n"
    assert "client_lifecycle_request_rejected" not in captured.err


def test_actual_launcher_normal_native_return_code_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _load_actual_local_compat_launcher(
        monkeypatch, _native_lifecycle(lambda **_kwargs: 47)
    )

    assert launcher._run_native("launch", package_root="fixture") == 47
    assert capsys.readouterr().err == ""


class _Loader:
    def __init__(self, name: str) -> None:
        self.name = name

    def exec_module(self, module: types.ModuleType) -> None:
        if self.name == "bidking_kernel":
            module.verify_local_integrity = lambda _root: (True, "ok", 1, "manifest")
            module.recheck_local_integrity = lambda _root: (True, "ok")
            module.release_local_integrity = lambda: (True, "ok")
        elif self.name == "_bidking_local_integrity_namespace_guard":
            module.begin_namespace_lease = lambda _root, **_kwargs: types.SimpleNamespace(close=lambda: None)
        elif self.name == "_client_launcher":
            module.main = lambda _args: (_ for _ in ()).throw(
                RuntimeError("client_lifecycle_busy")
            )
            module.diagnostics_main = lambda: 0
            module.repair_main = lambda _args: 0
        else:  # pragma: no cover - the embedded bootstrap owns the inventory
            raise AssertionError(self.name)


def test_rapid_second_launch_is_an_idempotent_success_not_client_exit_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap_python(TRUST_BOOTSTRAP.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda name, _path: types.SimpleNamespace(loader=_Loader(name)),
    )
    monkeypatch.setattr(
        importlib.util,
        "module_from_spec",
        lambda spec: types.ModuleType(spec.loader.name),
    )
    previous_argv = sys.argv
    sys.argv = ["bootstrap", "root", "kernel", "launcher", "launch"]
    try:
        with pytest.raises(SystemExit) as stopped:
            exec(compile(bootstrap, "<trust-bootstrap>", "exec"), {})
    finally:
        sys.argv = previous_argv
    assert stopped.value.code == 0


def test_nonbusy_lifecycle_error_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap_python(TRUST_BOOTSTRAP.read_text(encoding="utf-8"))

    class OtherLoader(_Loader):
        def exec_module(self, module: types.ModuleType) -> None:
            super().exec_module(module)
            if self.name == "_client_launcher":
                module.main = lambda _args: (_ for _ in ()).throw(
                    RuntimeError("client_lifecycle_payload_invalid")
                )

    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda name, _path: types.SimpleNamespace(loader=OtherLoader(name)),
    )
    monkeypatch.setattr(
        importlib.util,
        "module_from_spec",
        lambda spec: types.ModuleType(spec.loader.name),
    )
    previous_argv = sys.argv
    sys.argv = ["bootstrap", "root", "kernel", "launcher", "launch"]
    try:
        with pytest.raises(RuntimeError, match="client_lifecycle_payload_invalid"):
            exec(compile(bootstrap, "<trust-bootstrap>", "exec"), {})
    finally:
        sys.argv = previous_argv


def test_trust_bootstrap_routes_repair_and_forwards_only_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap_python(TRUST_BOOTSTRAP.read_text(encoding="utf-8"))
    calls: list[list[str]] = []

    class RepairLoader(_Loader):
        def exec_module(self, module: types.ModuleType) -> None:
            super().exec_module(module)
            if self.name == "_client_launcher":
                module.repair_main = lambda args: calls.append(list(args)) or 0

    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda name, _path: types.SimpleNamespace(loader=RepairLoader(name)),
    )
    monkeypatch.setattr(
        importlib.util,
        "module_from_spec",
        lambda spec: types.ModuleType(spec.loader.name),
    )
    previous_argv = sys.argv
    sys.argv = [
        "bootstrap",
        "root",
        "kernel",
        "launcher",
        "repair",
        "--dry-run",
    ]
    try:
        with pytest.raises(SystemExit) as stopped:
            exec(compile(bootstrap, "<trust-bootstrap>", "exec"), {})
    finally:
        sys.argv = previous_argv
    assert stopped.value.code == 0
    assert calls == [["--dry-run"]]


def _overlay_identity_probe(
    tmp_path: Path,
    *,
    started_at: float,
    generation: str,
    state_generation: str | None = None,
    process_id: int | None = None,
    override_get_process: str = "",
) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    startup = STARTUP.read_text(encoding="utf-8-sig")
    discovery = (ROOT / "scripts" / "live_process_discovery.ps1").read_text(
        encoding="utf-8-sig"
    )
    state = tmp_path / "overlay.json"
    overlay = tmp_path / "ahmad_tk_overlay.py"
    overlay.write_text("# fixture\n", encoding="utf-8")
    state.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pid": os.getpid() if process_id is None else process_id,
                "started_at": started_at,
                "kind": "ahmad_overlay",
                "overlay_path": str(overlay),
                "package_generation_identity": state_generation or generation,
            }
        ),
        encoding="utf-8",
    )
    body = "\n".join(
        (
            _function(discovery, "ConvertTo-BidKingCanonicalPath"),
            _function(discovery, "Test-BidKingSamePath"),
            _function(discovery, "Move-BidKingJsonWhitespace"),
            _function(discovery, "Read-BidKingJsonStringToken"),
            _function(discovery, "Read-BidKingJsonValueWithUniqueKeys"),
            _function(discovery, "Read-BidKingJsonArrayWithUniqueKeys"),
            _function(discovery, "Read-BidKingJsonObjectWithUniqueKeys"),
            _function(discovery, "Assert-BidKingJsonObjectKeysUnique"),
            _function(startup, "Get-BidKingCurrentOverlayIdentity"),
            override_get_process,
            "$row=Get-BidKingCurrentOverlayIdentity "
            f"-StatePath {_ps_quote(state)} "
            f"-ExpectedOverlayPath {_ps_quote(overlay)} "
            f"-ExpectedPackageGenerationIdentity '{generation}'",
            "$row|ConvertTo-Json -Compress",
        )
    )
    completed = _run_ps(body)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_overlay_identity_current_aba_generation_and_query_denied(
    tmp_path: Path,
) -> None:
    import psutil

    generation = "a" * 64
    started_at = psutil.Process().create_time()
    assert _overlay_identity_probe(
        tmp_path / "current", started_at=started_at, generation=generation
    )["State"] == "current"
    assert _overlay_identity_probe(
        tmp_path / "aba", started_at=started_at - 300, generation=generation
    )["State"] == "foreign"
    assert _overlay_identity_probe(
        tmp_path / "generation",
        started_at=started_at,
        generation=generation,
        state_generation="b" * 64,
    )["State"] == "foreign"
    assert _overlay_identity_probe(
        tmp_path / "stale",
        started_at=started_at,
        generation=generation,
        process_id=2_000_000_000,
    )["State"] == "stale"
    denied = _overlay_identity_probe(
        tmp_path / "denied",
        started_at=started_at,
        generation=generation,
        override_get_process=(
            "function Get-Process { throw "
            "[System.UnauthorizedAccessException]::new('private') }"
        ),
    )
    assert denied["State"] == "unknown"


def test_current_session_reuse_precedes_component_certificate_and_dependency_work() -> None:
    source = STARTUP.read_text(encoding="utf-8-sig")
    reuse = source.index("startup_reuse_ready")
    assert reuse < source.index("$MitmdumpPath = Resolve-WssMitmdumpPath")
    assert reuse < source.index("Enable-BidKingTemporaryCurrentUserTrust")
    assert reuse < source.index("$ProbeOut =")


def test_guard_uses_real_reused_overlay_pid_and_distinguishes_not_ready() -> None:
    source = STARTUP.read_text(encoding="utf-8-sig")
    assert "$OverlayPid = [int]$OverlayProcs[0].ProcessId" in source
    assert "Get-PidProbeState ([int]$OverlayPid)" in source
    assert "startup_guard_not_ready" in source
    assert "Start-Sleep -Seconds 4" not in source[source.index("# Capture-readiness guard") :]
    guard = _function(source, "Get-BidKingStartupGuardDecision")
    body = (
        _function(source, "Get-OverlayExitClass")
        + "\n"
        + guard
        + "\n$rows=@("
        + "(Get-BidKingStartupGuardDecision alive alive $null not_ready $false),"
        + "(Get-BidKingStartupGuardDecision alive alive $null ready $false),"
        + "(Get-BidKingStartupGuardDecision alive alive $null not_ready $true),"
        + "(Get-BidKingStartupGuardDecision alive dead 7 not_ready $false)"
        + ")\n$rows|ConvertTo-Json -Compress"
    )
    completed = _run_ps(body)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == [
        "wait",
        "ready",
        "not_ready",
        "overlay_exception",
    ]


def test_monitor_readiness_distinguishes_slow_ready_from_invalid_state(
    tmp_path: Path,
) -> None:
    source = STARTUP.read_text(encoding="utf-8-sig")
    discovery = (ROOT / "scripts" / "live_process_discovery.ps1").read_text(
        encoding="utf-8-sig"
    )
    state = tmp_path / "network_connection_status.json"
    current = "current-launch"
    body = "\n".join(
        (
            _function(discovery, "Move-BidKingJsonWhitespace"),
            _function(discovery, "Read-BidKingJsonStringToken"),
            _function(discovery, "Read-BidKingJsonValueWithUniqueKeys"),
            _function(discovery, "Read-BidKingJsonArrayWithUniqueKeys"),
            _function(discovery, "Read-BidKingJsonObjectWithUniqueKeys"),
            _function(discovery, "Assert-BidKingJsonObjectKeysUnique"),
            _function(source, "Get-BidKingMonitorReadinessState"),
            f"$path={_ps_quote(state)}",
            f"$rows=@((Get-BidKingMonitorReadinessState $path '{current}'))",
            "[IO.File]::WriteAllText($path,'{\"schema_version\":1,\"launch_id\":\"old\",\"wss_intercept_state\":\"ready\"}')",
            f"$rows += Get-BidKingMonitorReadinessState $path '{current}'",
            f"[IO.File]::WriteAllText($path,'{{\"schema_version\":1,\"launch_id\":\"{current}\",\"wss_intercept_state\":\"starting\"}}')",
            f"$rows += Get-BidKingMonitorReadinessState $path '{current}'",
            f"[IO.File]::WriteAllText($path,'{{\"schema_version\":1,\"launch_id\":\"{current}\",\"wss_intercept_state\":\"ready\"}}')",
            f"$rows += Get-BidKingMonitorReadinessState $path '{current}'",
            "[IO.File]::WriteAllText($path,'{broken')",
            f"$rows += Get-BidKingMonitorReadinessState $path '{current}'",
            "$rows|ConvertTo-Json -Compress",
        )
    )
    completed = _run_ps(body)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == [
        "not_ready",
        "not_ready",
        "not_ready",
        "ready",
        "unknown",
    ]


def test_package_shaped_warm_reuse_ab_avoids_old_fixed_unknown_guard(
    tmp_path: Path,
) -> None:
    import psutil

    generation = "c" * 64
    process_id = os.getpid()
    started_at = psutil.Process().create_time()
    monitor_path = tmp_path / "local_compat_monitor.py"
    overlay_path = tmp_path / "ahmad_tk_overlay.py"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monitor_path.write_text("# monitor\n", encoding="utf-8")
    overlay_path.write_text("# overlay\n", encoding="utf-8")
    monitor_state = log_dir / "monitor.lock"
    overlay_state = log_dir / "ahmad_overlay.pid"
    monitor_state.write_text(
        json.dumps(
            {
                "pid": process_id,
                "process_started_at": started_at,
                "monitor_path": str(monitor_path),
                "log_dir": str(log_dir),
                "package_generation_identity": generation,
            }
        ),
        encoding="utf-8",
    )
    overlay_state.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pid": process_id,
                "started_at": started_at,
                "kind": "ahmad_overlay",
                "overlay_path": str(overlay_path),
                "package_generation_identity": generation,
            }
        ),
        encoding="utf-8",
    )
    parent = subprocess.run(
        [
            "git",
            "show",
            "1624314c0e1049428d4f11dd891d095beca0a784:"
            "scripts/start_live_npcap_overlay.ps1",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    source = STARTUP.read_text(encoding="utf-8-sig")
    body = "\n".join(
        (
            f". {_ps_quote(ROOT / 'scripts' / 'live_process_discovery.ps1')}",
            _function(source, "Get-BidKingCurrentOverlayIdentity"),
            _function(parent, "Get-StartedProcessProbeState"),
            _function(parent, "Get-OverlayExitClass"),
            "$new=[Diagnostics.Stopwatch]::StartNew()",
            "$monitor=Get-BidKingMonitorLockIdentity "
            f"-StatePath {_ps_quote(monitor_state)} "
            f"-ExpectedMonitorPath {_ps_quote(monitor_path)} "
            f"-ExpectedLogDir {_ps_quote(log_dir)} "
            f"-ExpectedPackageGenerationIdentity '{generation}'",
            "$overlay=Get-BidKingCurrentOverlayIdentity "
            f"-StatePath {_ps_quote(overlay_state)} "
            f"-ExpectedOverlayPath {_ps_quote(overlay_path)} "
            f"-ExpectedPackageGenerationIdentity '{generation}'",
            "$new.Stop()",
            "$old=[Diagnostics.Stopwatch]::StartNew()",
            "Start-Sleep -Seconds 4",
            "$oldOverlay=Get-StartedProcessProbeState $null",
            "$oldClass=Get-OverlayExitClass alive $oldOverlay.State $oldOverlay.ExitCode",
            "$old.Stop()",
            "[pscustomobject]@{new_ms=$new.ElapsedMilliseconds;"
            "old_ms=$old.ElapsedMilliseconds;monitor=$monitor.State;"
            "overlay=$overlay.State;old_class=$oldClass}|ConvertTo-Json -Compress",
        )
    )
    completed = _run_ps(body, timeout=15)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    print(
        "warm_reuse_ab "
        f"new_ms={result['new_ms']} old_ms={result['old_ms']} "
        f"old_class={result['old_class']}"
    )
    assert result["monitor"] == result["overlay"] == "current"
    assert result["old_class"] == "process_query_unknown"
    assert result["old_ms"] >= 3900
    assert result["new_ms"] < 2000
    assert result["old_ms"] > result["new_ms"] * 2
