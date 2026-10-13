from __future__ import annotations

import importlib.util
import ast
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location(
        "startup_failure_namespace_guard", ROOT / "scripts/local_integrity_namespace_guard.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _records(guard):
    return [guard._SecurityRecord("directory", name, "original", False)
            for name in (".", "parent", "parent/leaf")]


def test_restore_attempts_remaining_records_after_first_error(guard, monkeypatch, tmp_path):
    attempted = []
    monkeypatch.setattr(guard, "_record_path", lambda root, relative: root / relative)
    monkeypatch.setattr(guard, "_reject_reparse", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard, "_security_descriptor", lambda path: ("original", False, "owner"))

    def restore(path, *_args, **_kwargs):
        attempted.append(path)
        if path.name == "leaf":
            raise OSError("synthetic descriptor failure")

    monkeypatch.setattr(guard, "_set_security_descriptor", restore)
    with pytest.raises(Exception):
        guard._restore_records(tmp_path, _records(guard))
    assert attempted == [tmp_path / "parent/leaf", tmp_path / "parent", tmp_path]


def test_close_partial_failure_keeps_journal_and_retries(guard, monkeypatch, tmp_path):
    journal = tmp_path / "journal.json"
    journal.write_text("fixture", encoding="utf-8")
    attempted = []
    failures = {"leaf"}
    monkeypatch.setattr(guard, "_record_path", lambda root, relative: root / relative)
    monkeypatch.setattr(guard, "_reject_reparse", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard, "_security_descriptor", lambda path: ("original", False, "owner"))

    def restore(path, *_args, **_kwargs):
        attempted.append(path.name)
        if path.name in failures:
            raise OSError("synthetic descriptor failure")

    monkeypatch.setattr(guard, "_set_security_descriptor", restore)
    lease = guard.NamespaceLease(tmp_path, _records(guard), journal, "d", "f", "owner", None)
    with pytest.raises(Exception):
        lease.close()
    assert journal.exists() and not lease._closed
    assert attempted == ["leaf", "parent", tmp_path.name]
    failures.clear()
    lease.close()
    assert lease._closed and not journal.exists()
    count = len(attempted)
    lease.close()
    assert len(attempted) == count


def test_restore_reparse_failure_does_not_skip_safe_siblings(guard, monkeypatch, tmp_path):
    restored = []
    monkeypatch.setattr(guard, "_record_path", lambda root, relative: root / relative)
    monkeypatch.setattr(guard, "_security_descriptor", lambda path: ("original", False, "owner"))

    def reject(path, **_kwargs):
        if path.name == "leaf":
            raise guard.NamespaceGuardError("namespace_reparse_rejected")

    monkeypatch.setattr(guard, "_reject_reparse", reject)
    monkeypatch.setattr(guard, "_set_security_descriptor", lambda path, *_args, **_kwargs: restored.append(path))
    with pytest.raises(Exception):
        guard._restore_records(tmp_path, _records(guard))
    assert restored == [tmp_path / "parent", tmp_path]


def test_restore_success_is_reverse_order(guard, monkeypatch, tmp_path):
    restored = []
    monkeypatch.setattr(guard, "_record_path", lambda root, relative: root / relative)
    monkeypatch.setattr(guard, "_reject_reparse", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard, "_security_descriptor", lambda path: ("original", False, "owner"))
    monkeypatch.setattr(guard, "_set_security_descriptor", lambda path, *_args, **_kwargs: restored.append(path))
    guard._restore_records(tmp_path, _records(guard))
    assert restored == [tmp_path / "parent/leaf", tmp_path / "parent", tmp_path]


def test_begin_preserves_original_and_rollback_failure(guard, monkeypatch, tmp_path):
    primary = OSError("synthetic original apply error")
    rollback = OSError("synthetic rollback error")
    monkeypatch.setattr(guard, "_require_windows", lambda: None)
    monkeypatch.setattr(guard, "_reject_reparse", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard, "_prepare_recovery_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(guard, "_journal_path", lambda *args: tmp_path / "journal.json")
    monkeypatch.setattr(guard, "_protected_paths", lambda root: ([root], []))
    monkeypatch.setattr(guard, "_security_descriptor", lambda path: ("original", False, "owner"))
    monkeypatch.setattr(guard, "_relative_member", lambda *args: ".")
    monkeypatch.setattr(guard, "_record_path", lambda root, relative: root)
    monkeypatch.setattr(guard, "_write_journal", lambda path, *args: path.write_text("original", encoding="utf-8"))
    attempts = []

    def setter(*args, **kwargs):
        raise primary

    def restore(root, records):
        attempts.extend(records)
        raise rollback

    monkeypatch.setattr(guard, "_set_security_descriptor", setter)
    monkeypatch.setattr(guard, "_restore_records", restore)
    with pytest.raises(OSError) as caught:
        guard._begin_namespace_lease(tmp_path, locked_directory_sddl="d", locked_file_sddl="f",
                                     expected_owner="owner", recovery_root=tmp_path,
                                     require_elevated=False, secure_recovery=False)
    assert caught.value is primary
    assert caught.value.namespace_restore_error is rollback
    assert caught.value.namespace_restore_state == "failed"
    assert len(attempts) == 1  # Even the attempted setter may have changed the ACL.
    assert (tmp_path / "journal.json").read_text(encoding="utf-8") == "original"


def _run_bootstrap(monkeypatch, tmp_path, *, verify=True, begin_error=None,
                   close_error=False, release_error=False, release_throws=False,
                   launcher_error=None, exit_code=0, child_run=None):
    source = (ROOT / "native/bidking_trust_bootstrap/src/main.rs").read_text(encoding="utf-8")
    embedded = re.search(r'const BOOTSTRAP: &str = r#"(.*?)"#;', source, re.S).group(1)
    tree = ast.parse(embedded)
    tree.body = [node for node in tree.body if not isinstance(node, ast.FunctionDef) or node.name != "load_exact"]
    events = []

    def verify_call(root):
        events.append("verify")
        return (verify, "ok" if verify else "integrity_file_missing", 0, "fixture")

    def close():
        events.append("close")
        if close_error:
            raise RuntimeError("fixture restore failed")

    def begin(*args, **kwargs):
        events.append("begin")
        if begin_error:
            raise begin_error
        return SimpleNamespace(close=close)

    def release():
        events.append("release")
        if release_throws:
            raise RuntimeError("fixture release failed")
        return (not release_error, "integrity_handle_release_failed" if release_error else "ok")

    def main(*args):
        events.append("launch")
        if launcher_error:
            raise launcher_error
        return exit_code

    kernel = SimpleNamespace(verify_local_integrity=verify_call,
                             recheck_local_integrity=lambda root: (events.append("recheck") is None, "ok"),
                             release_local_integrity=release,
                             startup_timing_event=lambda *args: None)
    modules = {"bidking_kernel": kernel,
               "_bidking_local_integrity_namespace_guard": SimpleNamespace(begin_namespace_lease=begin),
               "_client_launcher": SimpleNamespace(main=main)}
    run = "a" * 32
    monkeypatch.setenv("BIDKING_STARTUP_TIMING_RUN_ID", run)
    monkeypatch.setenv("BIDKING_STARTUP_TIMING_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["fixture", "root", "kernel", "launcher", "launch"])
    if child_run is not False:
        (tmp_path / (run + "--cleanup-powershell.json")).write_text(json.dumps({
            "schema": "bidking_startup_cleanup_v1", "run_id": child_run or run, "phase": "powershell",
            "monitor": "not_created", "overlay": "not_created", "message_key": "runtime_missing"}), encoding="utf-8")
    caught = None
    try:
        exec(compile(tree, "actual-bootstrap", "exec"), {"load_exact": lambda name, path: modules[name]})
    except BaseException as exc:
        caught = exc
    final = tmp_path / (run + "--cleanup-bootstrap.json")
    record = json.loads(final.read_text(encoding="utf-8")) if final.is_file() else None
    return caught, events, record


@pytest.mark.parametrize("kind", ["normal", "close", "release", "release_throws", "both", "primary", "verify", "wrong_run", "missing_child"])
def test_actual_bootstrap_unwinds_and_keeps_primary(monkeypatch, tmp_path, kind):
    primary = RuntimeError("synthetic primary") if kind == "primary" else None
    caught, events, record = _run_bootstrap(
        monkeypatch, tmp_path, verify=kind != "verify", close_error=kind in {"close", "both", "primary"},
        release_error=kind in {"release", "both", "primary"}, release_throws=kind == "release_throws",
        launcher_error=primary, child_run="b" * 32 if kind == "wrong_run" else (False if kind == "missing_child" else None))
    assert events[-1] == "release"
    if kind == "primary":
        assert caught is primary
        assert record["primary_error"] and record["acl_restore"] == "failed"
    else:
        assert isinstance(caught, SystemExit)
        expected = 109 if kind == "verify" else (211 if kind in {"close", "both"} else (138 if kind in {"release", "release_throws"} else 0))
        assert caught.code == expected
    if kind == "verify":
        assert events == ["verify", "release"]
        assert record["acl_restore"] == "not_acquired"
    else:
        assert events.index("verify") < events.index("begin") < events.index("recheck") < events.index("launch") < events.index("close") < events.index("release")
    if kind in {"wrong_run", "missing_child"}:
        assert record["monitor"] == record["overlay"] == "unknown"
    elif kind == "normal":
        assert record["acl_restore"] == "restored" and record["handle_release"] == "released"


def test_actual_bootstrap_begin_rollback_status(monkeypatch, tmp_path):
    primary = RuntimeError("synthetic primary begin")
    primary.namespace_restore_state = "failed"
    caught, events, record = _run_bootstrap(monkeypatch, tmp_path, begin_error=primary)
    assert caught is primary and events == ["verify", "begin", "release"]
    assert record["acl_restore"] == "failed" and record["handle_release"] == "released"


WINDOWS_PS = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")


def _run_ps(body: str, *, timeout=40, expected_exit=0):
    if not WINDOWS_PS.is_file():
        pytest.skip("Windows PowerShell consumer unavailable")
    script = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$text=[IO.File]::ReadAllText('__SOURCE__')
$parseErrors=$null;$tokens=$null
$tree=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw ($parseErrors | Out-String)}
$names=@('New-BidKingOwnedStartupProcess','Get-BidKingOwnedStartupProcessState','Get-BidKingFailureCleanupGameState','Stop-BidKingOwnedStartupProcess','Complete-BidKingStartupAttempt','Show-FatalBox','Write-BidKingStartupFailure')
foreach($name in $names){
  $node=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true))
  if($node.Count -ne 1){throw "function missing: $name"}
  . ([scriptblock]::Create($node[0].Extent.Text))
}
# Spawn seams now invalidate the phase inventory in the discovery module.
# Load that exact dependency instead of testing an incomplete caller fragment.
$discovery=[IO.File]::ReadAllText('__DISCOVERY__')
$discoveryTree=[Management.Automation.Language.Parser]::ParseInput($discovery,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw ($parseErrors | Out-String)}
$invalidate=@($discoveryTree.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Invalidate-BidKingProcessInventory'},$true))
if($invalidate.Count -ne 1){throw 'inventory invalidation dependency missing'}
. ([scriptblock]::Create($invalidate[0].Extent.Text))
$script:BidKingProcessInventoryGeneration=0
$script:events=[Collections.Generic.List[string]]::new()
function Write-BidKingStartupTiming {param($Event,$Result,$Topology);$script:events.Add("timing:$Event/$Result");return $true}
function Write-BidKingStartupStatus {param($StatusPath,$Event);$script:events.Add("status:$Event")}
$Repo='C:/owned-package';$Monitor='C:/owned-package/monitor.py';$Overlay='C:/owned-package/overlay.py'
$ExpectedPackageGenerationIdentity='a'*64;$LaunchId='fixture-launch';$ProcessName='BidKing.exe'
$UseWssCapture=$true;$ManagedLaunch=$false
function Reset-Fixture {
  $script:BidKingLaunchState=@{Succeeded=$false;FailureCode='unknown';MessageKey='startup_failed';PreserveChildren=$false;Monitor=$null;Overlay=$null;MonitorState='not_created';OverlayState='not_created'}
  $script:games=[Collections.Generic.List[string]]::new();$script:games.Add('absent')
  $script:gameCalls=0;$script:mutateEpoch=$false;$script:events.Clear()
}
function Get-BidKingFailureCleanupGameState {
  $script:gameCalls++
  if($script:mutateEpoch -and $script:gameCalls -eq 2){$script:proc.StartTime=$script:proc.StartTime.AddTicks(1)}
  $v=$script:games[0];if($script:games.Count -gt 1){$script:games.RemoveAt(0)};return $v
}
function New-FakeProcess {
  $p=[pscustomobject]@{Id=4101;SafeHandle=[pscustomobject]@{IsClosed=$false;IsInvalid=$false};StartTime=[DateTime]::new(2026,9,7,1,2,3,[DateTimeKind]::Utc);HasExited=$false;Graceful=$false;KillWorks=$true;Calls=[Collections.Generic.List[string]]::new()}
  $p|Add-Member ScriptMethod Refresh {}
  $p|Add-Member ScriptMethod CloseMainWindow {$this.Calls.Add('close');if($this.Graceful){$this.HasExited=$true};return $this.Graceful}
  $p|Add-Member ScriptMethod WaitForExit {param($ms);$this.Calls.Add("wait:$ms");return $this.HasExited}
  $p|Add-Member ScriptMethod Kill {$this.Calls.Add('kill');if($this.KillWorks){$this.HasExited=$true}}
  $p|Add-Member ScriptMethod Dispose {$this.Calls.Add('dispose')}
  return $p
}
''' .replace("__SOURCE__", str(ROOT / "scripts/start_live_npcap_overlay.ps1").replace("'", "''")).replace(
        "__DISCOVERY__", str(ROOT / "scripts/live_process_discovery.ps1").replace("'", "''")) + body
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run([str(WINDOWS_PS), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                            capture_output=True, encoding="utf-8", timeout=timeout)
    assert result.returncode == expected_exit, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def process_cases():
    return _run_ps(r'''
$results=@{}
foreach($case in @('owned','graceful','exited','epoch','generation','launch','root','entry','handle','game_active','game_unknown','game_flips','game_before_force','epoch_at_action','kill_failed')){
  Reset-Fixture;$script:proc=New-FakeProcess
  $record=New-BidKingOwnedStartupProcess $script:proc 'monitor' $Monitor
  switch($case){
    'graceful' {$script:proc.Graceful=$true}
    'exited' {$script:proc.HasExited=$true}
    'epoch' {$script:proc.StartTime=$script:proc.StartTime.AddTicks(1)}
    'generation' {$record.Generation='b'*64}
    'launch' {$record.LaunchId='another-launch'}
    'root' {$record.PackageRoot='C:/other-package'}
    'entry' {$record.EntryPath='C:/other-package/monitor.py'}
    'handle' {$script:proc.SafeHandle.IsInvalid=$true}
    'game_active' {$script:games.Clear();$script:games.Add('active')}
    'game_unknown' {$script:games.Clear();$script:games.Add('unknown')}
    'game_flips' {$script:games.Add('active')}
    'game_before_force' {$script:games.Add('absent');$script:games.Add('active')}
    'epoch_at_action' {$script:mutateEpoch=$true}
    'kill_failed' {$script:proc.KillWorks=$false}
  }
  $state=Stop-BidKingOwnedStartupProcess $record
  $results[$case]=@{state=$state;calls=@($script:proc.Calls)}
}
$results|ConvertTo-Json -Depth 5 -Compress
''')


@pytest.mark.parametrize("case,state", [
    ("owned", "reclaimed"), ("graceful", "reclaimed"), ("exited", "exited"),
    ("epoch", "preserved_changed"), ("generation", "preserved_unknown"),
    ("launch", "preserved_unknown"), ("root", "preserved_unknown"), ("entry", "preserved_unknown"),
    ("handle", "preserved_unknown"), ("game_active", "preserved_game"), ("game_unknown", "preserved_game"),
    ("game_flips", "preserved_game"), ("game_before_force", "preserved_game"),
    ("epoch_at_action", "preserved_changed"), ("kill_failed", "cleanup_failed")])
def test_owned_process_control_actual_ps_function(process_cases, case, state):
    observed = process_cases[case]
    assert observed["state"] == state
    if case in {"owned", "kill_failed"}:
        assert observed["calls"] == ["close", "wait:2000", "kill", "wait:5000"]
    elif case in {"graceful", "game_before_force"}:
        assert observed["calls"] == ["close", "wait:2000"]
    else:
        assert observed["calls"] == []


def test_failed_attempt_reverse_cleanup_and_success_preservation():
    result = _run_ps(r'''
$results=@{}
foreach($case in @('early','overlay_dead','overlay_alive','reused','success_no_overlay','success_keep_monitor')){
  Reset-Fixture
  if($case -ne 'early'){
    $script:proc=New-FakeProcess
    $script:BidKingLaunchState.Monitor=New-BidKingOwnedStartupProcess $script:proc 'monitor' $Monitor
    $script:BidKingLaunchState.MonitorState='alive'
  }
  if($case -in @('overlay_dead','overlay_alive')){
    $ui=New-FakeProcess;$ui.Id=4102;$ui.HasExited=($case -eq 'overlay_dead')
    $script:BidKingLaunchState.Overlay=New-BidKingOwnedStartupProcess $ui 'overlay' $Overlay
  }
  if($case -eq 'reused'){$script:BidKingLaunchState.Monitor=$null;$script:BidKingLaunchState.MonitorState='reused_preserved'}
  if($case -like 'success*'){$script:BidKingLaunchState.Succeeded=$true}
  Complete-BidKingStartupAttempt
  $results[$case]=@{monitor=$script:BidKingLaunchState.MonitorState;overlay=$script:BidKingLaunchState.OverlayState;events=@($script:events);calls=@(if($case -ne 'early'){$script:proc.Calls})}
}
$ManagedLaunch=$true;Reset-Fixture
Show-FatalBox 'must not enter a real MessageBox' 'BidKing Live · 启动锁已变化'
$results['deferred_message']=$script:BidKingLaunchState.MessageKey
$results|ConvertTo-Json -Depth 6 -Compress
''')
    assert result["early"]["monitor"] == "not_created"
    assert result["overlay_dead"]["monitor"] == "reclaimed" and result["overlay_dead"]["overlay"] == "exited"
    assert result["overlay_alive"]["monitor"] == "preserved_unknown"
    assert result["reused"]["monitor"] == "reused_preserved"
    for case in ("success_no_overlay", "success_keep_monitor"):
        assert result[case]["calls"] == ["dispose"] and result[case]["events"] == []
    assert result["deferred_message"] == "lock_changed"


def test_bootstrap_observation_write_failure_is_not_success_evidence(monkeypatch, tmp_path):
    def reject_replace(*args):
        raise OSError("synthetic receipt write failure")

    monkeypatch.setattr(os, "replace", reject_replace)
    caught, events, record = _run_bootstrap(monkeypatch, tmp_path, exit_code=206)
    assert isinstance(caught, SystemExit) and caught.code == 206
    assert events[-2:] == ["close", "release"] and record is None


@pytest.mark.parametrize("exit_class,exit_code", [("overlay_exception", 207), ("overlay_exited_cleanly", 208), ("monitor_exit", 209), ("process_query_unknown", 210)])
def test_actual_guard_failure_exits_after_actual_outer_finally(exit_class, exit_code):
    body = r'''
Reset-Fixture;$ManagedLaunch=$true;$UseWssCapture=$false
$OverlayExitClass='__CLASS__'
$script:proc=New-FakeProcess
$script:BidKingLaunchState.Monitor=New-BidKingOwnedStartupProcess $script:proc 'monitor' $Monitor
$script:BidKingLaunchState.MonitorState='alive'
$guard=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -ceq '$OverlayExitClass -ne "running"'},$true))
if($guard.Count -ne 1){throw 'guard path not unique'}
$outer=@($tree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})
if($outer.Count -ne 1){throw 'outer finally path not unique'}
$finalCode=($outer[0].Finally.Statements|ForEach-Object {$_.Extent.Text}) -join "`n"
try {
  . ([scriptblock]::Create($guard[0].Extent.Text))
  throw 'failure reached success continuation'
} finally {
  . ([scriptblock]::Create($finalCode))
  @{code=$script:BidKingLaunchState.FailureCode;monitor=$script:BidKingLaunchState.MonitorState;events=@($script:events);calls=@($script:proc.Calls)}|ConvertTo-Json -Depth 5 -Compress
}
'''.replace("__CLASS__", exit_class)
    value = _run_ps(body, expected_exit=exit_code)
    assert value["code"] == exit_class
    assert "timing:startup_terminal/failed" in value["events"]
    assert not any(event.endswith("/ok") for event in value["events"])
    assert value["monitor"] == ("preserved_unknown" if exit_class == "process_query_unknown" else "reclaimed")


def test_actual_ps_receipt_is_current_run_only_and_safe(tmp_path):
    body = r'''
Reset-Fixture;$ManagedLaunch=$true
$env:BIDKING_STARTUP_TIMING_RUN_ID='cccccccccccccccccccccccccccccccc'
$env:BIDKING_STARTUP_TIMING_DIR='__DIRECTORY__'
Show-FatalBox 'sensitive probe body must not enter the receipt' 'BidKing Live · 运行时缺失'
Complete-BidKingStartupAttempt
$path=Join-Path $env:BIDKING_STARTUP_TIMING_DIR ($env:BIDKING_STARTUP_TIMING_RUN_ID+'--cleanup-powershell.json')
$receipt=[IO.File]::ReadAllText($path)|ConvertFrom-Json
$env:BIDKING_STARTUP_TIMING_RUN_ID='invalid-run'
Complete-BidKingStartupAttempt
@{receipt=$receipt;invalid_exists=(Test-Path -LiteralPath (Join-Path $env:BIDKING_STARTUP_TIMING_DIR 'invalid-run--cleanup-powershell.json'))}|ConvertTo-Json -Depth 5 -Compress
'''.replace("__DIRECTORY__", str(tmp_path).replace("'", "''"))
    result = _run_ps(body)
    assert result["receipt"]["run_id"] == "c" * 32
    assert result["receipt"]["message_key"] == "runtime_missing"
    assert result["receipt"]["monitor"] == "not_created"
    assert not result["invalid_exists"]
    assert "sensitive probe" not in json.dumps(result)


@pytest.mark.parametrize("monitor_fails,expected_code", [(False, 206), (True, 202)])
def test_actual_spawn_seams_register_only_created_children_and_unwind(monitor_fails, expected_code):
    body = r'''
Reset-Fixture;$ManagedLaunch=$true;$UseWssCapture=$true
$MonitorPythonPath='fixture-monitor-python';$PythonWindowed='fixture-overlay-python'
$MonitorArgs=@('-B',$Monitor);$OverlayArgs=@('-B',$Overlay)
$MonitorOut='unused';$MonitorErr='unused';$OverlayErr='unused'
$script:monitorFails=__FAIL__;$script:proc=New-FakeProcess
function Start-Process {
  param($FilePath,$WorkingDirectory,$WindowStyle,[switch]$PassThru,$ArgumentList,$RedirectStandardOutput,$RedirectStandardError)
  if($FilePath -ceq 'fixture-monitor-python' -and -not $script:monitorFails){return $script:proc}
  throw 'synthetic spawn failure'
}
function Get-BidKingInternetCafeResult {return @{}}
function Write-BidKingInternetCafeReceipt {}
function Show-BidKingInternetCafeFailure {Show-FatalBox 'fixed fixture classification' 'BidKing Live · 启动组件缺失'}
$monitorTry=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.TryStatementAst] -and $n.Body.Statements.Count -eq 3 -and $n.Body.Statements[0].Extent.Text.Contains('Start-Process -FilePath $MonitorPythonPath')},$true))
$overlayTry=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.TryStatementAst] -and $n.Body.Statements.Count -eq 3 -and $n.Body.Statements[0].Extent.Text.Contains('Start-Process -FilePath $PythonWindowed')},$true))
if($monitorTry.Count -ne 1 -or $overlayTry.Count -ne 1){throw 'spawn seam not unique'}
$outer=@($tree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})
$finalCode=($outer[0].Finally.Statements|ForEach-Object {$_.Extent.Text}) -join "`n"
try {
  . ([scriptblock]::Create($monitorTry[0].Extent.Text))
  . ([scriptblock]::Create($overlayTry[0].Extent.Text))
  throw 'spawn failure reached success'
} finally {
  . ([scriptblock]::Create($finalCode))
  @{code=$script:BidKingLaunchState.FailureCode;monitor=$script:BidKingLaunchState.MonitorState;overlay=$script:BidKingLaunchState.OverlayState;events=@($script:events);calls=@($script:proc.Calls)}|ConvertTo-Json -Depth 5 -Compress
}
'''.replace("__FAIL__", "$true" if monitor_fails else "$false")
    value = _run_ps(body, expected_exit=expected_code)
    assert value["code"] == ("unknown" if monitor_fails else "overlay_spawn_failed")
    assert value["monitor"] == ("not_created" if monitor_fails else "reclaimed")
    assert value["overlay"] == "not_created"
    assert value["calls"] == ([] if monitor_fails else ["close", "wait:2000", "kill", "wait:5000", "dispose"])
    assert not any(event.endswith("/ok") for event in value["events"])


def test_actual_game_query_denied_is_unknown_not_absent():
    value = _run_ps(r'''
$function=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-BidKingFailureCleanupGameState'},$true))[0]
. ([scriptblock]::Create($function.Extent.Text))
function Get-Process { [CmdletBinding()]param($Name);throw [UnauthorizedAccessException]::new('synthetic query denied') }
$denied=Get-BidKingFailureCleanupGameState
function Get-Process { [CmdletBinding()]param($Name);[pscustomobject]@{Id=42} }
$active=Get-BidKingFailureCleanupGameState
@{denied=$denied;active=$active}|ConvertTo-Json -Compress
''')
    assert value == {"denied": "unknown", "active": "active"}


def test_actual_outer_exception_revokes_success_before_final_receipt():
    result = _run_ps(r'''
Reset-Fixture;$script:BidKingLaunchState.Succeeded=$true
$outer=@($tree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})[0]
$catchCode=($outer.CatchClauses[0].Body.Statements|ForEach-Object {$_.Extent.Text}) -join "`n"
$finallyCode=($outer.Finally.Statements|ForEach-Object {$_.Extent.Text}) -join "`n"
try {
  try {throw 'synthetic final-stage failure'} catch {. ([scriptblock]::Create($catchCode))}
} finally {
  . ([scriptblock]::Create($finallyCode))
  @{succeeded=$script:BidKingLaunchState.Succeeded;events=@($script:events)}|ConvertTo-Json -Depth 4 -Compress
}
''', expected_exit=1)
    assert result["succeeded"] is False
    assert "timing:startup_terminal/failed" in result["events"]


def test_close_journal_unlink_failure_stays_unclosed(guard, monkeypatch, tmp_path):
    journal = tmp_path / "journal.json"
    journal.write_text("fixture", encoding="utf-8")
    restores = []
    monkeypatch.setattr(guard, "_restore_records", lambda root, records: restores.append(len(records)))
    original_unlink = Path.unlink

    def reject(path, *args, **kwargs):
        if path == journal:
            raise OSError("synthetic unlink failure")
        return original_unlink(path, *args, **kwargs)

    lease = guard.NamespaceLease(tmp_path, _records(guard), journal, "d", "f", "owner", None)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", reject)
        with pytest.raises(guard.NamespaceGuardError, match="namespace_recovery_cleanup_failed"):
            lease.close()
    assert journal.exists() and not lease._closed and restores == [3]
    lease.close()
    assert not journal.exists() and lease._closed and restores == [3, 3]


def _rust_function(source, name):
    match = re.search(r"    fn " + re.escape(name) + r"\b.*?\n    }", source, re.S)
    assert match, name
    return match.group(0)


def test_actual_rust_cleanup_copy_classifier_and_owned_wait_failure(tmp_path):
    rustc = Path("C:/Users/example/.cargo/bin/rustc.exe")
    if not rustc.is_file():
        pytest.skip("isolated Rust compiler unavailable")
    root_source = (ROOT / "native/bidking_trust_bootstrap/src/main.rs").read_text(encoding="utf-8")
    lifecycle_source = (ROOT / "native/bidking_kernel/src/client_lifecycle.rs").read_text(encoding="utf-8")
    match = re.search(r"    let status = match child.wait\(\) \{.*?\n    Ok\(status.code\(\).unwrap_or\(1\)\)", lifecycle_source, re.S)
    assert match
    code = _rust_function(root_source, "cleanup_display_state") + "\n" + _rust_function(root_source, "failure_reason")
    code += r'''
#[derive(Debug)] struct LifecycleError;
impl LifecycleError { fn start()->Self {Self} }
struct Status; impl Status {fn code(&self)->Option<i32>{Some(0)}}
struct FakeChild {first_error:bool, kill_error:bool, waits:u32, kills:u32}
impl FakeChild {
 fn wait(&mut self)->Result<Status,()> {self.waits+=1;if self.first_error && self.waits==1 {Err(())}else{Ok(Status)}}
 fn kill(&mut self)->Result<(),()> {self.kills+=1;if self.kill_error{Err(())}else{Ok(())}}
}
fn actual_wait(child:&mut FakeChild)->Result<i32,LifecycleError> {
''' + match.group(0) + r'''
}
#[test] fn cleanup_states(){
 assert_eq!(cleanup_display_state("restored","released","reclaimed","exited"),"complete");
 assert_eq!(cleanup_display_state("not_acquired","released","not_created","not_created"),"complete");
 assert_eq!(cleanup_display_state("unknown","released","not_created","not_created"),"unknown");
 assert_eq!(cleanup_display_state("restored","released","reused_preserved","exited"),"unknown");
 assert_eq!(cleanup_display_state("restored","released","preserved_game","exited"),"game_preserved");
 assert_eq!(cleanup_display_state("failed","released","reclaimed","exited"),"incomplete");
 assert_eq!(cleanup_display_state("restored","failed","reclaimed","exited"),"incomplete");
 assert!(!failure_reason("integrity_file_missing","unknown").contains("删除"));
 assert!(failure_reason("overlay_spawn_failed","startup_failed").contains("悬浮窗"));
}
#[test] fn owned_wait(){
 for first_error in [false,true] {for kill_error in [false,true]{
   let mut child=FakeChild{first_error,kill_error,waits:0,kills:0};
   let result=actual_wait(&mut child);
   assert_eq!(result.is_err(),first_error);
   assert_eq!(child.kills,if first_error{1}else{0});
   assert_eq!(child.waits,if first_error{2}else{1});
 }}
}
'''
    path = tmp_path / "actual_cleanup_functions.rs"
    executable = tmp_path / "actual_cleanup_functions.exe"
    path.write_text(code, encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    compiled = subprocess.run([str(rustc), "--edition", "2024", "--test", str(path), "-o", str(executable)], capture_output=True, encoding="utf-8", timeout=60, creationflags=flags)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(executable)], capture_output=True, encoding="utf-8", timeout=20, creationflags=flags)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
