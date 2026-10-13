from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import psutil
import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
DISCOVERY = ROOT / "scripts" / "live_process_discovery.ps1"
LAUNCHER = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
DIAGNOSTICS = ROOT / "scripts" / "collect_diagnostics.ps1"
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
PACKAGE_GENERATION_IDENTITY = "a" * 64


def _ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _run_powershell(body: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    if not POWERSHELL:
        pytest.skip("Windows PowerShell is unavailable")
    script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
        "$ErrorActionPreference = 'Stop';"
        + body
    )
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _json_stdout(proc: subprocess.CompletedProcess[str]) -> dict:
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"missing JSON output; stderr={proc.stderr}"
    return json.loads(lines[-1])


def test_process_discovery_keeps_normal_cim_command_line_matching(tmp_path: Path) -> None:
    status = tmp_path / "startup_status.log"
    package_root = tmp_path / "当前 包 with spaces"
    monitor = package_root / "scripts" / "run_wss_live_monitor.py"
    log_dir = package_root / "data" / "logs" / "live"
    foreign_monitor = tmp_path / "旧包" / "scripts" / "run_wss_live_monitor.py"
    monitor_command = str(monitor).replace("\\", "/").upper()
    log_command = str(log_dir).replace("\\", "/").upper()
    body = f"""
. {_ps_quote(DISCOVERY)}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  @(
    [pscustomobject]@{{Name='python.exe'; CommandLine='python.exe "{monitor_command}" --log-dir "{log_command}" --package-generation-identity {PACKAGE_GENERATION_IDENTITY}'; ProcessId=101}},
    [pscustomobject]@{{Name='python.exe'; CommandLine='python.exe "{foreign_monitor}" --log-dir "{log_dir}" --package-generation-identity {PACKAGE_GENERATION_IDENTITY}'; ProcessId=104}},
    [pscustomobject]@{{Name='python.exe'; CommandLine='python.exe "{monitor}" --log-dir "{tmp_path / "foreign-logs"}" --package-generation-identity {PACKAGE_GENERATION_IDENTITY}'; ProcessId=105}},
    [pscustomobject]@{{Name='pythonw.exe'; CommandLine='ahmad_tk_overlay.py'; ProcessId=102}},
    [pscustomobject]@{{Name='python.exe'; CommandLine='unrelated.py'; ProcessId=103}}
  )
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(tmp_path / "missing.lock")} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
$overlay = @(Get-BidKingOverlayProcesses -OverlayPidPath {_ps_quote(tmp_path / "missing.pid")} -StatusPath {_ps_quote(status)})
[pscustomobject]@{{
  live = @($live | ForEach-Object {{ $_.ProcessId }})
  overlay = @($overlay | ForEach-Object {{ $_.ProcessId }})
  status_exists = Test-Path -LiteralPath {_ps_quote(status)}
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"live": [101], "overlay": [102], "status_exists": False}


def test_process_discovery_rejects_current_monitor_path_used_as_decoy_argument(
    tmp_path: Path,
) -> None:
    status = tmp_path / "startup_status.log"
    package_root = tmp_path / "当前 包 with spaces"
    monitor = package_root / "scripts" / "run_wss_live_monitor.py"
    log_dir = package_root / "data" / "logs" / "live"
    foreign_monitor = tmp_path / "旧包" / "scripts" / "other_monitor.py"
    body = f"""
. {_ps_quote(DISCOVERY)}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  @(
    [pscustomobject]@{{
      Name='python.exe'
      CommandLine='python.exe "{foreign_monitor}" --probe "{monitor}" --log-dir "{log_dir}" --package-generation-identity {PACKAGE_GENERATION_IDENTITY}'
      ProcessId=4242
    }}
  )
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(tmp_path / "missing.lock")} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
[pscustomobject]@{{
  live_count = $live.Count
  adopted_pid = if ($live.Count -gt 0) {{ $live[0].ProcessId }} else {{ $null }}
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"live_count": 0, "adopted_pid": None}


@pytest.mark.parametrize(
    ("interpreter_args", "expected"),
    [
        ("-E -B", True),
        ("-X utf8 -u", True),
        ("-m package.module", False),
        ('-c "print(1)"', False),
        ("--unknown", False),
    ],
)
def test_monitor_identity_accepts_only_strict_python_entrypoint_position(
    tmp_path: Path,
    interpreter_args: str,
    expected: bool,
) -> None:
    monitor = tmp_path / "当前 包" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "当前 包" / "data" / "logs" / "live"
    body = f"""
. {_ps_quote(DISCOVERY)}
$matched = Test-BidKingMonitorCommandIdentity `
  -CommandLine ('python.exe {interpreter_args} "{monitor}" --log-dir "{log_dir}" --package-generation-identity {PACKAGE_GENERATION_IDENTITY}') `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}'
[pscustomobject]@{{matched=[bool]$matched}} | ConvertTo-Json -Compress
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"matched": expected}


@pytest.mark.parametrize(
    "script_arguments",
    [
        '--log-dir "{current}" --log-dir "{foreign}"',
        '--log-dir "{foreign}" --log-dir "{current}"',
        "--log-dir",
        '--log-directory "{current}"',
    ],
)
def test_monitor_identity_rejects_ambiguous_or_invalid_log_dir_arguments(
    tmp_path: Path,
    script_arguments: str,
) -> None:
    monitor = tmp_path / "当前 包" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "当前 包" / "data" / "logs" / "live"
    foreign_log_dir = tmp_path / "旧包" / "data" / "logs" / "live"
    arguments = script_arguments.format(
        current=log_dir,
        foreign=foreign_log_dir,
    )
    body = f"""
. {_ps_quote(DISCOVERY)}
$matched = Test-BidKingMonitorCommandIdentity `
  -CommandLine ('python.exe "{monitor}" {arguments} --package-generation-identity {PACKAGE_GENERATION_IDENTITY}') `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}'
[pscustomobject]@{{matched=[bool]$matched}} | ConvertTo-Json -Compress
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"matched": False}


def test_monitor_identity_rejects_log_dir_before_script_entrypoint(
    tmp_path: Path,
) -> None:
    monitor = tmp_path / "当前 包" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "当前 包" / "data" / "logs" / "live"
    body = f"""
. {_ps_quote(DISCOVERY)}
$matched = Test-BidKingMonitorCommandIdentity `
  -CommandLine ('python.exe --log-dir "{log_dir}" "{monitor}" --package-generation-identity {PACKAGE_GENERATION_IDENTITY}') `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}'
[pscustomobject]@{{matched=[bool]$matched}} | ConvertTo-Json -Compress
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"matched": False}


def _launcher_restart_function() -> str:
    launcher = LAUNCHER.read_text(encoding="utf-8-sig")
    match = re.search(
        r"function Invoke-BidKingRestartCleanup\(.*?^\}",
        launcher,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return (
        f"$ExpectedPackageGenerationIdentity = '{PACKAGE_GENERATION_IDENTITY}'\n"
        + match.group(0)
    )


@pytest.mark.parametrize(
    "identity_mode",
    ["foreign_monitor", "missing_identity", "pid_epoch_mismatch"],
)
def test_launcher_restart_preserves_unverified_live_monitor_lock(
    tmp_path: Path,
    identity_mode: str,
) -> None:
    process = psutil.Process(os.getpid())
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    payload: dict[str, object] = {
        "pid": process.pid,
        "started_at": process.create_time(),
        "process_started_at": process.create_time(),
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
        "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
    }
    if identity_mode == "foreign_monitor":
        payload["monitor_path"] = str(tmp_path / "foreign" / monitor.name)
    elif identity_mode == "missing_identity":
        payload.pop("monitor_path")
    elif identity_mode == "pid_epoch_mismatch":
        payload["process_started_at"] = process.create_time() - 600
    lock.write_text(json.dumps(payload), encoding="utf-8")

    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_restart_function()}
$LockPath = {_ps_quote(lock)}
$Monitor = {_ps_quote(monitor)}
$LogPath = {_ps_quote(log_dir)}
$StartupStatus = {_ps_quote(status)}
$OverlayPidPath = {_ps_quote(tmp_path / "overlay.pid")}
$script:liveCalls = 0
$script:stopCalls = 0
function Get-LiveProcesses {{
  $script:liveCalls += 1
  [pscustomobject]@{{ProcessId={process.pid}; CommandLine='verified_current_monitor'}}
}}
function Get-OverlayProcesses {{ @() }}
function Stop-Process {{
  [CmdletBinding()]
  param([int]$Id, [switch]$Force)
  $script:stopCalls += 1
}}
$result = Invoke-BidKingRestartCleanup
$statusText = if (Test-Path -LiteralPath $StartupStatus) {{
  [string](Get-Content -LiteralPath $StartupStatus -Raw)
}} else {{ '' }}
[pscustomobject]@{{
  succeeded = $result.Succeeded
  lock_state = $result.LockState
  lock_exists = Test-Path -LiteralPath $LockPath
  live_calls = $script:liveCalls
  stop_calls = $script:stopCalls
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["succeeded"] is False
    if identity_mode == "pid_epoch_mismatch":
        # A later lifetime now proves this is an old lock.  This restart fixture
        # deliberately puts the leaf outside ExpectedLogDir, so it stays blocked.
        assert result["lock_state"] == "stale"
    else:
        assert result["lock_state"] in {"foreign", "unknown"}
    assert result["lock_exists"] is True
    assert result["live_calls"] == 0
    assert result["stop_calls"] == 0
    assert f"monitor_restart_lock_preserved state={result['lock_state']}" in result[
        "status"
    ]
    assert str(tmp_path) not in result["status"]


def test_launcher_restart_does_not_touch_decoy_monitor(tmp_path: Path) -> None:
    lock = tmp_path / "missing.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    foreign_monitor = tmp_path / "foreign" / "scripts" / "other_monitor.py"
    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_restart_function()}
$LockPath = {_ps_quote(lock)}
$Monitor = {_ps_quote(monitor)}
$LogPath = {_ps_quote(log_dir)}
$StartupStatus = {_ps_quote(status)}
$OverlayPidPath = {_ps_quote(tmp_path / "overlay.pid")}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  [pscustomobject]@{{
    Name='python.exe'
    CommandLine='python.exe "{foreign_monitor}" --probe "{monitor}" --log-dir "{log_dir}" --package-generation-identity {PACKAGE_GENERATION_IDENTITY}'
    ProcessId=4242
  }}
}}
function Get-LiveProcesses {{
  Get-BidKingLiveProcesses `
    -MonitorLockPath $LockPath `
    -ExpectedMonitorPath $Monitor `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
    -StatusPath $StartupStatus
}}
function Get-OverlayProcesses {{ @() }}
$script:stopCalls = 0
function Stop-Process {{
  [CmdletBinding()]
  param([int]$Id, [switch]$Force)
  $script:stopCalls += 1
}}
Reset-BidKingProcessDiscoveryState
$result = Invoke-BidKingRestartCleanup
[pscustomobject]@{{
  succeeded = $result.Succeeded
  killed = @($result.KilledMonitorPids)
  stop_calls = $script:stopCalls
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"succeeded": True, "killed": [], "stop_calls": 0}


def test_launcher_restart_removes_only_verified_current_lock_after_owned_stop(
    tmp_path: Path,
) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    process = psutil.Process(child.pid)
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "当前 包" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "当前 包" / "data" / "logs" / "live"
    lock.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "started_at": process.create_time(),
                "process_started_at": process.create_time(),
                "monitor_path": str(monitor),
                "log_dir": str(log_dir),
                "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            }
        ),
        encoding="utf-8",
    )

    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_restart_function()}
$LockPath = {_ps_quote(lock)}
$Monitor = {_ps_quote(monitor)}
$LogPath = {_ps_quote(log_dir)}
$StartupStatus = {_ps_quote(status)}
$OverlayPidPath = {_ps_quote(tmp_path / "overlay.pid")}
function Get-LiveProcesses {{
  if (Get-Process -Id {process.pid} -ErrorAction SilentlyContinue) {{
    [pscustomobject]@{{ProcessId={process.pid}; CommandLine='verified_current_monitor'}}
  }}
}}
function Get-OverlayProcesses {{ @() }}
$result = Invoke-BidKingRestartCleanup
[pscustomobject]@{{
  succeeded = $result.Succeeded
  lock_state = $result.LockState
  lock_exists = Test-Path -LiteralPath $LockPath
  killed = @($result.KilledMonitorPids)
}} | ConvertTo-Json -Compress -Depth 5
"""
    try:
        result = _json_stdout(_run_powershell(body))
        assert result["succeeded"] is True
        assert result["lock_state"] == "current"
        assert result["lock_exists"] is False
        assert result["killed"] == [process.pid]
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_launcher_restart_binds_delete_to_open_lock_handle(
    tmp_path: Path,
) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    process = psutil.Process(child.pid)
    lock = tmp_path / "monitor.lock"
    replacement = tmp_path / "replacement.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "当前 包" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "当前 包" / "data" / "logs" / "live"
    lock.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "started_at": process.create_time(),
                "process_started_at": process.create_time(),
                "monitor_path": str(monitor),
                "log_dir": str(log_dir),
                "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            }
        ),
        encoding="utf-8",
    )
    replacement_json = json.dumps(
        {
            "pid": os.getpid(),
            "process_started_at": psutil.Process(os.getpid()).create_time(),
            "monitor_path": str(monitor),
            "log_dir": str(log_dir),
            "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            "nonce": "replacement-after-handle-bind",
        }
    ).replace("'", "''")

    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_restart_function()}
$LockPath = {_ps_quote(lock)}
$ReplacementPath = {_ps_quote(replacement)}
$Monitor = {_ps_quote(monitor)}
$LogPath = {_ps_quote(log_dir)}
$StartupStatus = {_ps_quote(status)}
$OverlayPidPath = {_ps_quote(tmp_path / "overlay.pid")}
$script:replacementBlocked = $false
$script:deleteImpl = ${{function:Set-BidKingMonitorLockDeleteOnClose}}
function Set-BidKingMonitorLockDeleteOnClose {{
  param([Microsoft.Win32.SafeHandles.SafeFileHandle]$SafeHandle)
  [System.IO.File]::WriteAllText(
    $ReplacementPath,
    '{replacement_json}',
    (New-Object System.Text.UTF8Encoding($false))
  )
  try {{
    Move-Item `
      -LiteralPath $ReplacementPath `
      -Destination $LockPath `
      -Force `
      -ErrorAction Stop
  }} catch {{
    $script:replacementBlocked = $true
  }}
  & $script:deleteImpl -SafeHandle $SafeHandle
}}
function Get-LiveProcesses {{
  if (Get-Process -Id {process.pid} -ErrorAction SilentlyContinue) {{
    [pscustomobject]@{{ProcessId={process.pid}; CommandLine='verified_current_monitor'}}
  }}
}}
function Get-OverlayProcesses {{ @() }}
$result = Invoke-BidKingRestartCleanup
[pscustomobject]@{{
  succeeded = $result.Succeeded
  lock_state = $result.LockState
  lock_exists = Test-Path -LiteralPath $LockPath
  replacement_blocked = $script:replacementBlocked
  replacement_preserved = Test-Path -LiteralPath $ReplacementPath
}} | ConvertTo-Json -Compress -Depth 5
"""
    try:
        result = _json_stdout(_run_powershell(body))
        assert result == {
            "succeeded": True,
            "lock_state": "current",
            "lock_exists": False,
            "replacement_blocked": True,
            "replacement_preserved": True,
        }
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_launcher_restart_preserves_lock_when_stop_does_not_end_owner(
    tmp_path: Path,
) -> None:
    process = psutil.Process(os.getpid())
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    lock.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "started_at": process.create_time(),
                "process_started_at": process.create_time(),
                "monitor_path": str(monitor),
                "log_dir": str(log_dir),
                "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            }
        ),
        encoding="utf-8",
    )

    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_restart_function()}
$LockPath = {_ps_quote(lock)}
$Monitor = {_ps_quote(monitor)}
$LogPath = {_ps_quote(log_dir)}
$StartupStatus = {_ps_quote(status)}
$OverlayPidPath = {_ps_quote(tmp_path / "overlay.pid")}
$script:stopCalls = 0
$script:clockCalls = 0
function Get-Date {{
  [CmdletBinding()]
  param([string]$Format)
  if ($Format) {{ return [DateTime]::UtcNow.ToString($Format) }}
  $script:clockCalls += 1
  return [DateTime]::UtcNow.AddSeconds($script:clockCalls * 10)
}}
function Get-LiveProcesses {{
  [pscustomobject]@{{ProcessId={process.pid}; CommandLine='verified_current_monitor'}}
}}
function Get-OverlayProcesses {{ @() }}
function Stop-Process {{
  [CmdletBinding()]
  param([int]$Id, [switch]$Force)
  $script:stopCalls += 1
}}
function Start-Sleep {{ param([int]$Milliseconds) }}
$result = Invoke-BidKingRestartCleanup
$statusText = if (Test-Path -LiteralPath $StartupStatus) {{
  [string](Get-Content -LiteralPath $StartupStatus -Raw)
}} else {{ '' }}
[pscustomobject]@{{
  succeeded = $result.Succeeded
  lock_state = $result.LockState
  lock_exists = Test-Path -LiteralPath $LockPath
  stop_calls = $script:stopCalls
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["succeeded"] is False
    assert result["lock_state"] == "current"
    assert result["lock_exists"] is True
    assert result["stop_calls"] >= 1
    assert "monitor_restart_lock_preserved state=current" in result["status"]
    assert str(tmp_path) not in result["status"]


def test_launcher_restart_preserves_current_lock_if_fingerprint_changes(
    tmp_path: Path,
) -> None:
    process = psutil.Process(os.getpid())
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    payload = {
        "pid": process.pid,
        "started_at": process.create_time(),
        "process_started_at": process.create_time(),
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
        "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
    }
    lock.write_text(json.dumps(payload), encoding="utf-8")
    replacement = dict(payload)
    replacement["process_started_at"] = process.create_time() + 1
    replacement_json = json.dumps(replacement).replace("'", "''")

    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_restart_function()}
$LockPath = {_ps_quote(lock)}
$Monitor = {_ps_quote(monitor)}
$LogPath = {_ps_quote(log_dir)}
$StartupStatus = {_ps_quote(status)}
$OverlayPidPath = {_ps_quote(tmp_path / "overlay.pid")}
$script:liveCalls = 0
function Get-LiveProcesses {{
  $script:liveCalls += 1
  if ($script:liveCalls -eq 1) {{
    [pscustomobject]@{{ProcessId={process.pid}; CommandLine='verified_current_monitor'}}
  }}
}}
function Get-OverlayProcesses {{ @() }}
function Stop-Process {{
  [CmdletBinding()]
  param([int]$Id, [switch]$Force)
  [System.IO.File]::WriteAllText(
    $LockPath,
    '{replacement_json}',
    (New-Object System.Text.UTF8Encoding($false))
  )
}}
$result = Invoke-BidKingRestartCleanup
[pscustomobject]@{{
  succeeded = $result.Succeeded
  lock_state = $result.LockState
  lock_exists = Test-Path -LiteralPath $LockPath
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {
        "succeeded": False,
        "lock_state": "current",
        "lock_exists": True,
    }


def test_launcher_restart_preserves_replacement_with_same_identity_fields(
    tmp_path: Path,
) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    process = psutil.Process(child.pid)
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    payload = {
        "pid": process.pid,
        "started_at": process.create_time(),
        "process_started_at": process.create_time(),
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
        "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
        "kind": "original",
    }
    replacement = dict(payload)
    replacement["kind"] = "replacement"
    replacement["nonce"] = "different-raw-bytes"
    lock.write_text(json.dumps(payload), encoding="utf-8")
    replacement_json = json.dumps(replacement).replace("'", "''")

    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_restart_function()}
$LockPath = {_ps_quote(lock)}
$Monitor = {_ps_quote(monitor)}
$LogPath = {_ps_quote(log_dir)}
$StartupStatus = {_ps_quote(status)}
$OverlayPidPath = {_ps_quote(tmp_path / "overlay.pid")}
function Get-LiveProcesses {{
  if (Get-Process -Id {process.pid} -ErrorAction SilentlyContinue) {{
    [pscustomobject]@{{ProcessId={process.pid}; CommandLine='verified_current_monitor'}}
  }}
}}
function Get-OverlayProcesses {{ @() }}
function Stop-Process {{
  [CmdletBinding()]
  param([int]$Id, [switch]$Force)
  Microsoft.PowerShell.Management\\Stop-Process -Id $Id -Force -ErrorAction Stop
  [System.IO.File]::WriteAllText(
    $LockPath,
    '{replacement_json}',
    (New-Object System.Text.UTF8Encoding($false))
  )
}}
$result = Invoke-BidKingRestartCleanup
$raw = if (Test-Path -LiteralPath $LockPath) {{
  [System.IO.File]::ReadAllText($LockPath)
}} else {{ '' }}
[pscustomobject]@{{
  succeeded = $result.Succeeded
  lock_state = $result.LockState
  lock_exists = Test-Path -LiteralPath $LockPath
  replacement_preserved = $raw.Contains('different-raw-bytes')
}} | ConvertTo-Json -Compress -Depth 5
"""
    try:
        result = _json_stdout(_run_powershell(body))
        assert result == {
            "succeeded": False,
            "lock_state": "current",
            "lock_exists": True,
            "replacement_preserved": True,
        }
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_process_discovery_preserves_lock_when_process_query_is_denied(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "data" / "logs" / "live"
    lock.write_text(
        json.dumps(
            {
                "pid": 4242,
                "process_started_at": 1000.0,
                "monitor_path": str(monitor),
                "log_dir": str(log_dir),
                "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            }
        ),
        encoding="utf-8",
    )

    body = f"""
. {_ps_quote(DISCOVERY)}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('forced_cim_failure')
}}
function Get-Process {{
  [CmdletBinding()]
  param([int]$Id, [string]$Name, [object]$ErrorAction)
  throw [System.UnauthorizedAccessException]::new('forced_access_denied')
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
$statusText = [string](Get-Content -LiteralPath {_ps_quote(status)} -Raw)
[pscustomobject]@{{
  live_count = $live.Count
  lock_exists = Test-Path -LiteralPath {_ps_quote(lock)}
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["live_count"] == 0
    assert result["lock_exists"] is True
    assert "monitor_lock_identity_unverified" in result["status"]
    assert "state=unknown" in result["status"]
    assert "forced_access_denied" not in result["status"]
    assert str(tmp_path) not in result["status"]


def test_process_discovery_preserves_lock_when_start_time_is_unknown(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "data" / "logs" / "live"
    lock.write_text(
        json.dumps(
            {
                "pid": 4242,
                "process_started_at": 1000.0,
                "monitor_path": str(monitor),
                "log_dir": str(log_dir),
                "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            }
        ),
        encoding="utf-8",
    )

    body = f"""
. {_ps_quote(DISCOVERY)}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('forced_cim_failure')
}}
function Get-Process {{
  [CmdletBinding()]
  param([int]$Id, [string]$Name, [object]$ErrorAction)
  $process = [pscustomobject]@{{ProcessName='python'}}
  $process | Add-Member -MemberType ScriptProperty -Name StartTime -Value {{
    throw [System.UnauthorizedAccessException]::new('forced_start_time_denied')
  }}
  return $process
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
$statusText = [string](Get-Content -LiteralPath {_ps_quote(status)} -Raw)
[pscustomobject]@{{
  live_count = $live.Count
  lock_exists = Test-Path -LiteralPath {_ps_quote(lock)}
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["live_count"] == 0
    assert result["lock_exists"] is True
    assert "monitor_lock_identity_unverified" in result["status"]
    assert "state=unknown" in result["status"]
    assert "forced_start_time_denied" not in result["status"]
    assert str(tmp_path) not in result["status"]


def test_process_discovery_preserves_stale_lock_if_raw_bytes_change(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "data" / "logs" / "live"
    dead_pid = 2_000_000_000
    payload = {
        "pid": dead_pid,
        "process_started_at": 1000.0,
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
        "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
        "kind": "original",
    }
    replacement = dict(payload)
    replacement["kind"] = "replacement"
    replacement["nonce"] = "cold-start-replacement"
    lock.write_text(json.dumps(payload), encoding="utf-8")
    replacement_json = json.dumps(replacement).replace("'", "''")

    body = f"""
. {_ps_quote(DISCOVERY)}
$script:identityImpl = ${{function:Get-BidKingMonitorLockIdentity}}
function Get-BidKingMonitorLockIdentity {{
  param(
    [string]$StatePath,
    [string]$ExpectedMonitorPath,
    [string]$ExpectedLogDir,
    [string]$ExpectedPackageGenerationIdentity
  )
  $identity = & $script:identityImpl `
    -StatePath $StatePath `
    -ExpectedMonitorPath $ExpectedMonitorPath `
    -ExpectedLogDir $ExpectedLogDir `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  [System.IO.File]::WriteAllText(
    $StatePath,
    '{replacement_json}',
    (New-Object System.Text.UTF8Encoding($false))
  )
  return $identity
}}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('forced_cim_failure')
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
$raw = [System.IO.File]::ReadAllText({_ps_quote(lock)})
$statusText = [string](Get-Content -LiteralPath {_ps_quote(status)} -Raw)
[pscustomobject]@{{
  live_count = $live.Count
  lock_exists = Test-Path -LiteralPath {_ps_quote(lock)}
  replacement_preserved = $raw.Contains('cold-start-replacement')
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["live_count"] == 0
    assert result["lock_exists"] is True
    assert result["replacement_preserved"] is True
    assert "monitor_lock_identity_unverified" in result["status"]
    assert str(tmp_path) not in result["status"]


def test_process_discovery_preserves_stale_lock_if_atomic_open_fails(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "data" / "logs" / "live"
    lock.write_text(
        json.dumps(
            {
                "pid": 2_000_000_000,
                "process_started_at": 1000.0,
                "monitor_path": str(monitor),
                "log_dir": str(log_dir),
                "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            }
        ),
        encoding="utf-8",
    )

    body = f"""
. {_ps_quote(DISCOVERY)}
function Initialize-BidKingMonitorLockAtomicDelete {{
  throw [System.UnauthorizedAccessException]::new('PRIVATE_ATOMIC_OPEN_DETAIL')
}}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('forced_cim_failure')
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
$statusText = [string](Get-Content -LiteralPath {_ps_quote(status)} -Raw)
[pscustomobject]@{{
  live_count = $live.Count
  lock_exists = Test-Path -LiteralPath {_ps_quote(lock)}
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["live_count"] == 0
    assert result["lock_exists"] is True
    assert "monitor_lock_identity_unverified" in result["status"]
    assert "state=unknown" in result["status"]
    assert "PRIVATE_ATOMIC_OPEN_DETAIL" not in result["status"]
    assert str(tmp_path) not in result["status"]


def test_process_discovery_cim_failure_uses_live_pid_state_once(tmp_path: Path) -> None:
    process = psutil.Process(os.getpid())
    lock = tmp_path / "monitor.lock"
    overlay_pid = tmp_path / "ahmad_overlay.pid"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "scripts" / "local_compat_monitor.py"
    log_dir = tmp_path / "data" / "logs" / "live"
    lock.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "started_at": process.create_time(),
                "process_started_at": process.create_time(),
                "monitor_path": str(monitor),
                "log_dir": str(log_dir),
                "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            }
        ),
        encoding="utf-8",
    )
    overlay_pid.write_text(
        json.dumps({"pid": process.pid, "started_at": process.create_time()}),
        encoding="utf-8",
    )

    body = f"""
. {_ps_quote(DISCOVERY)}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('forced_cim_failure')
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
$overlay = @(Get-BidKingOverlayProcesses -OverlayPidPath {_ps_quote(overlay_pid)} -StatusPath {_ps_quote(status)})
$statusText = [string](Get-Content -LiteralPath {_ps_quote(status)} -Raw)
[pscustomobject]@{{
  live_pid = $live[0].ProcessId
  live_marker = $live[0].CommandLine
  overlay_pid = $overlay[0].ProcessId
  overlay_marker = $overlay[0].CommandLine
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["live_pid"] == process.pid
    assert result["overlay_pid"] == process.pid
    assert result["live_marker"] == "verified_current_monitor"
    assert result["overlay_marker"] == "ahmad_tk_overlay.py"
    assert result["status"].count("cim_process_enumeration_unavailable_fallback") == 1
    assert "forced_cim_failure" not in result["status"]


@pytest.mark.parametrize("identity_mode", ["missing", "foreign_monitor", "foreign_log_dir"])
def test_process_discovery_cim_failure_never_reuses_unverified_monitor_lock(
    tmp_path: Path,
    identity_mode: str,
) -> None:
    process = psutil.Process(os.getpid())
    lock = tmp_path / "monitor.lock"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "current" / "scripts" / "local_compat_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    payload: dict[str, object] = {
        "pid": process.pid,
        "started_at": process.create_time(),
    }
    if identity_mode != "missing":
        payload["monitor_path"] = str(monitor)
        payload["log_dir"] = str(log_dir)
        payload["package_generation_identity"] = PACKAGE_GENERATION_IDENTITY
    if identity_mode == "foreign_monitor":
        payload["monitor_path"] = str(tmp_path / "foreign" / monitor.name)
    elif identity_mode == "foreign_log_dir":
        payload["log_dir"] = str(tmp_path / "foreign-logs")
    lock.write_text(json.dumps(payload), encoding="utf-8")

    body = f"""
. {_ps_quote(DISCOVERY)}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('forced_cim_failure')
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
$statusText = [string](Get-Content -LiteralPath {_ps_quote(status)} -Raw)
[pscustomobject]@{{
  live_count = $live.Count
  lock_exists = Test-Path -LiteralPath {_ps_quote(lock)}
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["live_count"] == 0
    assert result["lock_exists"] is True
    assert "monitor_lock_identity_unverified" in result["status"]
    assert "forced_cim_failure" not in result["status"]
    assert str(tmp_path) not in result["status"]


def test_process_discovery_reclaims_stale_pid_state(tmp_path: Path) -> None:
    lock = tmp_path / "monitor.lock"
    overlay_pid = tmp_path / "ahmad_overlay.pid"
    status = tmp_path / "startup_status.log"
    monitor = tmp_path / "scripts" / "run_wss_live_monitor.py"
    lock.write_text(
        json.dumps(
            {
                "pid": 2_000_000_000,
                "started_at": 1_000.0,
                "process_started_at": 1_000.0,
                "monitor_path": str(monitor),
                "log_dir": str(tmp_path),
                "package_generation_identity": PACKAGE_GENERATION_IDENTITY,
            }
        ),
        encoding="utf-8",
    )
    overlay_pid.write_text("2000000000", encoding="ascii")

    body = f"""
. {_ps_quote(DISCOVERY)}
function Get-CimInstance {{
  [CmdletBinding()]
  param([string]$ClassName, [int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('forced_cim_failure')
}}
Reset-BidKingProcessDiscoveryState
$live = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(tmp_path)} `
  -ExpectedPackageGenerationIdentity '{PACKAGE_GENERATION_IDENTITY}' `
  -StatusPath {_ps_quote(status)})
$overlay = @(Get-BidKingOverlayProcesses -OverlayPidPath {_ps_quote(overlay_pid)} -StatusPath {_ps_quote(status)})
$statusText = [string](Get-Content -LiteralPath {_ps_quote(status)} -Raw)
[pscustomobject]@{{
  live_count = $live.Count
  overlay_count = $overlay.Count
  lock_exists = Test-Path -LiteralPath {_ps_quote(lock)}
  overlay_pid_exists = Test-Path -LiteralPath {_ps_quote(overlay_pid)}
  status = $statusText
}} | ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result["live_count"] == 0
    assert result["overlay_count"] == 0
    assert result["lock_exists"] is False
    assert result["overlay_pid_exists"] is False
    assert result["status"].count("stale_process_state_reclaimed") == 2


def test_diagnostics_structures_cim_failure_and_still_writes_report(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    copied = scripts / DIAGNOSTICS.name
    shutil.copy2(DIAGNOSTICS, copied)

    body = f"""
$env:BIDKING_DIAG_NOEXPLORER = '1'
$env:BIDKING_DIAG_NONINTERACTIVE = '1'
$env:BIDKING_DIAG_SKIP_NETWORK = '1'
function Get-CimInstance {{
  [CmdletBinding()]
  param(
    [string]$ClassName,
    [string]$Filter,
    [int]$OperationTimeoutSec
  )
  throw [System.InvalidOperationException]::new('forced_cim_failure')
}}
. {_ps_quote(copied)}
"""
    proc = _run_powershell(body, timeout=60)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "forced_cim_failure" not in proc.stderr

    reports = list(tmp_path.glob("BidKing诊断_*.txt"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8-sig")
    assert report.count("wmi_cim_unavailable:") == 1
    assert "diagnostics_continue=true" in report
    assert "系统详细信息: WMI/CIM 不可用, 已降级；其余诊断继续。" in report
    assert "历史 monitor 日志不作为本次启动证据" in report
    assert "6. 抓包健康度" in report
    assert "forced_cim_failure" not in report


def test_wmi_fallback_is_packaged_and_never_mutates_winmgmt() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8-sig")
    discovery = DISCOVERY.read_text(encoding="utf-8-sig")
    diagnostics = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    build = BUILD.read_text(encoding="utf-8-sig")
    overlay = (ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py").read_text(
        encoding="utf-8-sig"
    )

    assert ". $ProcessDiscovery" in launcher
    assert "Get-BidKingLiveProcesses" in launcher
    assert "-ExpectedMonitorPath $Monitor" in launcher
    assert "-ExpectedLogDir $LogPath" in launcher
    assert "Get-BidKingOverlayProcesses" in launcher
    assert "Get-CimInstance Win32_Process" not in launcher
    assert build.count('"scripts/live_process_discovery.ps1"') == 1
    assert '"live_process_discovery.ps1"' in build
    assert "startup_status.log" in overlay
    assert "startup_session.json" in launcher
    assert "monitor_log_session.json" in launcher
    assert "launch_id" in launcher and "started_at" in launcher
    combined = launcher + discovery + diagnostics
    assert "Set-Service -Name Winmgmt" not in combined
    assert "Start-Service -Name Winmgmt" not in combined
    assert "winmgmt /resetrepository" not in combined


@pytest.mark.parametrize(
    "script",
    [
        DISCOVERY,
        LAUNCHER,
        DIAGNOSTICS,
        ROOT / "scripts" / "remove_wss_capture_ca.ps1",
        ROOT / "scripts" / "wss_environment_preflight.ps1",
        BUILD,
    ],
)
def test_wmi_powershell_scripts_parse_in_windows_powershell_51(script: Path) -> None:
    body = f"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  {_ps_quote(script)},
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) {{
  $errors | ForEach-Object {{ Write-Error $_.Message }}
  exit 1
}}
"""
    proc = _run_powershell(body)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
