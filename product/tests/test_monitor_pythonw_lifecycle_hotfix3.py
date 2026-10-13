from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
DISCOVERY = ROOT / "scripts" / "live_process_discovery.ps1"
WINDOWS_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
PWSH = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
HOSTS = (WINDOWS_POWERSHELL, PWSH)


def _ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _run_powershell(host: Path, body: str, *, cwd: Path) -> object:
    assert host.is_file(), f"required PowerShell host missing: {host}"
    encoded = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [
            str(host),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-OutputFormat",
            "Text",
            "-EncodedCommand",
            encoded,
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    candidates = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    assert candidates, output
    return json.loads(candidates[-1])


@pytest.mark.parametrize("host", HOSTS)
def test_windowed_python_selector_is_shared_and_formal_missing_fails_closed(
    tmp_path: Path,
    host: Path,
) -> None:
    runtime = tmp_path / "runtime with spaces" / "python"
    runtime.mkdir(parents=True)
    python = runtime / "python.exe"
    pythonw = runtime / "pythonw.exe"
    python.write_bytes(b"console")
    pythonw.write_bytes(b"windowed")
    body = f"""
$errors=$null
$tokens=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
  {_ps_quote(LAUNCHER)},[ref]$tokens,[ref]$errors
)
if ($errors.Count -ne 0) {{ throw ($errors | ForEach-Object Message | Out-String) }}
$definition=$ast.Find({{
  param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
  $node.Name -eq 'Resolve-BidKingWindowedPythonPath'
}},$true)
if (-not $definition) {{ throw 'windowed selector missing' }}
Invoke-Expression $definition.Extent.Text
$present=Resolve-BidKingWindowedPythonPath -PythonPath {_ps_quote(python)}
Remove-Item -LiteralPath {_ps_quote(pythonw)}
$formalMissing=Resolve-BidKingWindowedPythonPath -PythonPath {_ps_quote(python)}
$engineering=Resolve-BidKingWindowedPythonPath -PythonPath {_ps_quote(python)} -AllowConsoleFallback
[ordered]@{{
  present=$present
  formal_missing_is_null=($null -eq $formalMissing)
  engineering=$engineering
}} | ConvertTo-Json -Compress
"""
    result = _run_powershell(host, body, cwd=tmp_path)
    assert Path(result["present"]) == pythonw
    assert result["formal_missing_is_null"] is True
    assert Path(result["engineering"]) == python

    source = LAUNCHER.read_text(encoding="utf-8")
    assert source.count("Resolve-BidKingWindowedPythonPath") == 3
    assert "Start-Process -FilePath $MonitorPythonPath" in source
    assert "Start-Process -FilePath $PythonPath -WorkingDirectory $Repo" not in source
    monitor_call = source.index("-AllowConsoleFallback:$AllowEngineeringRuntime")
    missing_gate = source.index("if (-not $MonitorPythonPath)")
    monitor_start = source.index("Start-Process -FilePath $MonitorPythonPath")
    overlay_call = source.index("-AllowConsoleFallback", monitor_start)
    assert monitor_call < missing_gate < monitor_start < overlay_call


@pytest.mark.parametrize("host", HOSTS)
def test_monitor_start_statement_preserves_pid_args_and_log_redirection(
    tmp_path: Path,
    host: Path,
) -> None:
    runtime = tmp_path / "package with spaces" / "runtime" / "python"
    runtime.mkdir(parents=True)
    pythonw = runtime / "pythonw.exe"
    pythonw.write_bytes(b"windowed")
    repo = tmp_path / "package with spaces"
    monitor_out = repo / "data" / "logs" / "live" / "monitor.stdout.log"
    monitor_err = repo / "data" / "logs" / "live" / "monitor.stderr.log"
    body = f"""
$errors=$null
$tokens=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
  {_ps_quote(LAUNCHER)},[ref]$tokens,[ref]$errors
)
if ($errors.Count -ne 0) {{ throw ($errors | ForEach-Object Message | Out-String) }}
$assignment=@($ast.FindAll({{
  param($node)
  $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
  $node.Extent.Text -match 'Start-Process -FilePath \\$MonitorPythonPath'
}},$true))
if ($assignment.Count -ne 1) {{ throw 'monitor Start-Process assignment not unique' }}
$MonitorPythonPath={_ps_quote(pythonw)}
$Repo={_ps_quote(repo)}
$MonitorOut={_ps_quote(monitor_out)}
$MonitorErr={_ps_quote(monitor_err)}
$MonitorArgs=@('monitor.py','--log-dir','data logs','--package-generation-identity',('a'*64))
$script:captured=$null
function Start-Process {{
  [CmdletBinding()]param(
    [string]$FilePath,
    [string]$WorkingDirectory,
    [string]$WindowStyle,
    [switch]$PassThru,
    [object[]]$ArgumentList,
    [string]$RedirectStandardOutput,
    [string]$RedirectStandardError
  )
  $script:captured=[ordered]@{{
    file_path=$FilePath
    working_directory=$WorkingDirectory
    window_style=$WindowStyle
    pass_thru=[bool]$PassThru
    arguments=@($ArgumentList)
    stdout=$RedirectStandardOutput
    stderr=$RedirectStandardError
  }}
  [pscustomobject]@{{Id=4242}}
}}
Invoke-Expression $assignment[0].Extent.Text
$script:captured['pid']=$Started.Id
$script:captured | ConvertTo-Json -Compress -Depth 4
"""
    result = _run_powershell(host, body, cwd=tmp_path)
    assert Path(result["file_path"]) == pythonw
    assert Path(result["working_directory"]) == repo
    assert result["window_style"] == "Hidden"
    assert result["pass_thru"] is True
    assert result["arguments"] == [
        "monitor.py",
        "--log-dir",
        "data logs",
        "--package-generation-identity",
        "a" * 64,
    ]
    assert Path(result["stdout"]) == monitor_out
    assert Path(result["stderr"]) == monitor_err
    assert result["pid"] == 4242


@pytest.mark.parametrize("host", HOSTS)
def test_package_generation_identity_uses_monitor_runtime_authority(
    tmp_path: Path,
    host: Path,
) -> None:
    repo = tmp_path / "package with spaces"
    runtime = repo / "runtime" / "python"
    scripts = repo / "scripts"
    runtime.mkdir(parents=True)
    scripts.mkdir(parents=True)
    python = runtime / "python.exe"
    pythonw = runtime / "pythonw.exe"
    python.write_bytes(b"console-runtime")
    pythonw.write_bytes(b"windowed-runtime")
    helper = scripts / "package_generation_identity.py"
    monitor = scripts / "run_fatbeans_webhook_monitor.py"
    helper.write_bytes(b"helper")
    monitor.write_bytes(b"monitor")
    fake_python = tmp_path / "capture identity args.ps1"
    fake_python.write_text(
        "$global:BidKingIdentityArgs=@($args)\n"
        "'BIDKING_PACKAGE_GENERATION_IDENTITY=' + ('a' * 64)\n",
        encoding="utf-8",
    )
    body = f"""
$errors=$null
$tokens=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
  {_ps_quote(LAUNCHER)},[ref]$tokens,[ref]$errors
)
if ($errors.Count -ne 0) {{ throw ($errors | ForEach-Object Message | Out-String) }}
$assignment=@($ast.FindAll({{
  param($node)
  $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
  $node.Left.Extent.Text -eq '$PackageIdentityOutput'
}},$true))
if ($assignment.Count -ne 1) {{ throw 'package identity assignment not unique' }}
$PythonPath={_ps_quote(fake_python)}
$MonitorPythonPath={_ps_quote(pythonw)}
$PackageIdentityHelper={_ps_quote(helper)}
$Repo={_ps_quote(repo)}
$Monitor={_ps_quote(monitor)}
$global:BidKingIdentityArgs=$null
Invoke-Expression $assignment[0].Extent.Text
[ordered]@{{
  arguments=@($global:BidKingIdentityArgs)
  output=([string]$PackageIdentityOutput).Trim()
}} | ConvertTo-Json -Compress -Depth 4
"""
    result = _run_powershell(host, body, cwd=tmp_path)
    assert result["arguments"] == [
        "-E",
        str(helper),
        "--package-root",
        str(repo),
        "--monitor",
        str(monitor),
        "--runtime",
        str(pythonw),
    ]
    assert result["output"] == "BIDKING_PACKAGE_GENERATION_IDENTITY=" + "a" * 64

    source = LAUNCHER.read_text(encoding="utf-8")
    identity_start = source.index("$PackageIdentityOutput = (")
    identity_end = source.index("$PackageIdentityExit = $LASTEXITCODE", identity_start)
    identity_block = source[identity_start:identity_end]
    assert "--runtime $MonitorPythonPath" in identity_block
    assert "--runtime $PythonPath" not in identity_block


@pytest.mark.parametrize("host", HOSTS)
def test_process_discovery_accepts_pythonw_monitor_identity(
    tmp_path: Path,
    host: Path,
) -> None:
    runtime = tmp_path / "package with spaces" / "runtime" / "python"
    monitor = tmp_path / "package with spaces" / "scripts" / "local_compat_monitor.py"
    log_dir = tmp_path / "package with spaces" / "data" / "logs" / "live"
    generation = "b" * 64
    command_line = (
        f'"{runtime / "pythonw.exe"}" "{monitor}" --log-dir "{log_dir}" '
        f"--package-generation-identity {generation}"
    )
    body = f"""
. {_ps_quote(DISCOVERY)}
$accepted=Test-BidKingMonitorCommandIdentity `
  -CommandLine '{command_line.replace("'", "''")}' `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{generation}'
[ordered]@{{accepted=[bool]$accepted}} | ConvertTo-Json -Compress
"""
    result = _run_powershell(host, body, cwd=tmp_path)
    assert result == {"accepted": True}


@pytest.mark.parametrize("host", HOSTS)
def test_pythonw_removes_parent_python_console_and_keeps_log_redirection(
    tmp_path: Path,
    host: Path,
) -> None:
    python = Path(r"C:\Python31315\python.exe")
    pythonw = Path(r"C:\Python31315\pythonw.exe")
    assert python.is_file() and pythonw.is_file()
    script = tmp_path / "windowed probe.py"
    console_receipt = tmp_path / "console receipt.json"
    windowed_receipt = tmp_path / "windowed receipt.json"
    console_stdout = tmp_path / "console-monitor.stdout.log"
    console_stderr = tmp_path / "console-monitor.stderr.log"
    windowed_stdout = tmp_path / "windowed-monitor.stdout.log"
    windowed_stderr = tmp_path / "windowed-monitor.stderr.log"
    script.write_text(
        "import ctypes, json, sys\n"
        "from pathlib import Path\n"
        "receipt = Path(sys.argv[1])\n"
        "handle = int(ctypes.windll.kernel32.GetConsoleWindow())\n"
        "receipt.write_text(json.dumps({'console': handle}), encoding='utf-8')\n"
        "print('MONITOR_STDOUT_SENTINEL', flush=True)\n"
        "print('MONITOR_STDERR_SENTINEL', file=sys.stderr, flush=True)\n",
        encoding="utf-8",
    )
    body = f"""
$consoleArguments=@('"{script}"','"{console_receipt}"')
$consoleProcess=Start-Process -FilePath {_ps_quote(python)} -WorkingDirectory {_ps_quote(tmp_path)} `
  -WindowStyle Hidden -PassThru -Wait -ArgumentList $consoleArguments `
  -RedirectStandardOutput {_ps_quote(console_stdout)} -RedirectStandardError {_ps_quote(console_stderr)}
$windowedArguments=@('"{script}"','"{windowed_receipt}"')
$windowedProcess=Start-Process -FilePath {_ps_quote(pythonw)} -WorkingDirectory {_ps_quote(tmp_path)} `
  -WindowStyle Hidden -PassThru -Wait -ArgumentList $windowedArguments `
  -RedirectStandardOutput {_ps_quote(windowed_stdout)} -RedirectStandardError {_ps_quote(windowed_stderr)}
[ordered]@{{console_exit=$consoleProcess.ExitCode;windowed_exit=$windowedProcess.ExitCode}} | ConvertTo-Json -Compress
"""
    result = _run_powershell(host, body, cwd=tmp_path)
    assert result == {"console_exit": 0, "windowed_exit": 0}
    assert json.loads(console_receipt.read_text(encoding="utf-8"))["console"] != 0
    assert json.loads(windowed_receipt.read_text(encoding="utf-8")) == {"console": 0}
    for stdout, stderr in (
        (console_stdout, console_stderr),
        (windowed_stdout, windowed_stderr),
    ):
        assert "MONITOR_STDOUT_SENTINEL" in stdout.read_text(encoding="utf-8-sig")
        assert "MONITOR_STDERR_SENTINEL" in stderr.read_text(encoding="utf-8-sig")
