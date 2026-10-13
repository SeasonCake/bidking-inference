from __future__ import annotations

import ast
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("failure_cleanup_base_f01", ROOT / "tests/test_startup_failure_cleanup_0_3_5.py")
base = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(base)

RESET_ACTUAL = r'''
Reset-Fixture
$init=@($tree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -ceq '$script:BidKingLaunchState'})
if($init.Count -ne 1){throw 'startup state producer not unique'}
. ([scriptblock]::Create($init[0].Extent.Text))
'''


@pytest.fixture(scope="module")
def dependency_rows():
    return base._run_ps(r'''
$results=@{}
foreach($case in @('initial_unknown','confirmed_absent','dead','alive','invalid_handle','epoch_changed','reused','query_absent','query_fallback_unknown','query_throws')) {
''' + RESET_ACTUAL + r'''
  $ManagedLaunch=$false;$NoOverlay=$false;$UseWssCapture=$true
  $script:proc=New-FakeProcess
  $script:BidKingLaunchState.Monitor=New-BidKingOwnedStartupProcess $script:proc 'monitor' $Monitor
  $script:BidKingLaunchState.MonitorState='alive'
  if($case -eq 'confirmed_absent'){$script:BidKingLaunchState.OverlayState='not_created'}
  if($case -eq 'reused'){$script:BidKingLaunchState.OverlayState='reused_preserved'}
  if($case -in @('dead','alive','invalid_handle','epoch_changed')) {
    $ui=New-FakeProcess;$ui.Id=4102
    $script:BidKingLaunchState.Overlay=New-BidKingOwnedStartupProcess $ui 'overlay' $Overlay
    if($case -eq 'dead'){$ui.HasExited=$true}
    if($case -eq 'invalid_handle'){$ui.SafeHandle.IsInvalid=$true}
    if($case -eq 'epoch_changed'){$ui.StartTime=$ui.StartTime.AddTicks(1)}
  }
  if($case -like 'query_*') {
    # Query cases consume real discovery qualification, not a preset absence flag.
    $discoveryPath=Join-Path (Split-Path -Parent '__SOURCE__') 'live_process_discovery.ps1'
    $discoveryTree=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($discoveryPath),[ref]$tokens,[ref]$parseErrors)
    if($parseErrors.Count){throw 'discovery parse error'}
    foreach($name in @('Reset-BidKingProcessDiscoveryState','Enable-BidKingPidFallback','Get-BidKingOverlayProcesses')) {
      $node=@($discoveryTree.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true))
      if($node.Count -ne 1){throw 'discovery function not unique'}
      . ([scriptblock]::Create($node[0].Extent.Text))
    }
    $caller=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-OverlayProcesses'},$true))[0]
    . ([scriptblock]::Create($caller.Extent.Text))
    function Get-CimInstance {[CmdletBinding()]param($ClassName,$OperationTimeoutSec);if($case -ne 'query_absent'){throw 'synthetic query failure'};return @()}
    function Get-BidKingProcessFromStateFile {if($case -eq 'query_throws'){throw 'synthetic fallback failure'};return @()}
    Reset-BidKingProcessDiscoveryState
    $outer=@($tree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})[0]
    $statements=@($outer.Body.Statements)
    $index=-1
    for($i=0;$i -lt $statements.Count;$i++){if($statements[$i] -is [Management.Automation.Language.AssignmentStatementAst] -and $statements[$i].Left.Extent.Text -ceq '$OverlayProcs'){$index=$i}}
    if($index -lt 1){throw 'query producer not found'}
    try {. ([scriptblock]::Create(($statements[($index-1)..($index+1)]|ForEach-Object {$_.Extent.Text}) -join "`n"))} catch {}
  }
  Complete-BidKingStartupAttempt
  $results[$case]=@{monitor=$script:BidKingLaunchState.MonitorState;overlay=$script:BidKingLaunchState.OverlayState;calls=@($script:proc.Calls)}
}
$results|ConvertTo-Json -Depth 5 -Compress
'''.replace('__SOURCE__', str(ROOT / 'scripts/start_live_npcap_overlay.ps1').replace("'", "''")))


@pytest.mark.parametrize("case", ["initial_unknown", "confirmed_absent", "dead", "alive", "invalid_handle", "epoch_changed", "reused", "query_absent", "query_fallback_unknown", "query_throws"])
def test_overlay_dependency_requires_positive_absence_or_exit(dependency_rows, case):
    row = dependency_rows[case]
    allowed = case in {"confirmed_absent", "dead", "query_absent"}
    assert _dependency_observer(row, allowed)


def _dependency_observer(row, overlay_proven_gone):
    return (row["monitor"] == ("reclaimed" if overlay_proven_gone else "preserved_unknown")
            and ("kill" in row["calls"]) is overlay_proven_gone)


def test_same_dependency_observer_rejects_saved_formal_red_without_rerun():
    # Frozen observations from the different-owner receipt 3c26002d...; the
    # checker keeps a known-red control without depending on a scratch path or
    # executing the superseded product again.
    old_calls = ["close", "wait:2000", "kill", "wait:5000", "dispose"]
    rows = {"reused_overlay": ("reused_preserved", "reclaimed"),
            "handle_unknown": ("preserved_unknown", "reclaimed"),
            "epoch_changed": ("preserved_changed", "reclaimed")}
    assert all(not _dependency_observer({"monitor": monitor, "calls": old_calls}, False)
               for _overlay, monitor in rows.values())
    assert _dependency_observer({"monitor": "reclaimed", "calls": old_calls}, True)
    assert old_calls[2] == "kill"


def _native_wrap(body):
    source = (ROOT / "native/bidking_kernel/src/client_lifecycle.rs").read_text(encoding="utf-8")
    prefix = ast.literal_eval(re.search(r'const LAUNCH_WRAPPER_PREFIX: &\[u8\] =\s*(b".*?");', source, re.S).group(1)).decode()
    suffix = re.search(r'const LAUNCH_WRAPPER_SUFFIX: &\[u8\] = br#"(.*?)"#;', source, re.S).group(1)
    return r'''
$outer=@($tree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})[0]
$catchCode=($outer.CatchClauses[0].Body.Statements|ForEach-Object {$_.Extent.Text}) -join "`n"
$finallyCode=($outer.Finally.Statements|ForEach-Object {$_.Extent.Text}) -join "`n"
''' + body + r'''
$end='[Console]::Out.WriteLine((@{managed=[bool]$ManagedLaunch;failure=$script:BidKingLaunchState.FailureCode;key=$script:BidKingLaunchState.MessageKey;monitor=$script:BidKingLaunchState.MonitorState;overlay=$script:BidKingLaunchState.OverlayState;calls=@($script:proc.Calls)}|ConvertTo-Json -Depth 5 -Compress))'
$paramsSource='param([string]$PythonPath,[string]$LogDir,[string]$DiagnosticProfile,[switch]$NoAutoElevate,[switch]$ManagedLaunch,[switch]$Restart,[switch]$NoTaskbar)'
$payload=$paramsSource+"`ntry {"+$body+"`n} catch {"+$catchCode+"`n} finally {"+$finallyCode+"`n"+$end+"`n}"
$prefix=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PREFIX__'))
$suffix=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SUFFIX__'))
& ([scriptblock]::Create($prefix+[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))+$suffix))
'''.replace("__PREFIX__", base64.b64encode(prefix.encode()).decode()).replace("__SUFFIX__", base64.b64encode(suffix.encode()).decode())


@pytest.mark.parametrize("case,code", [("before_query", 1), ("certificate_unknown", 202), ("certificate_proven_absent", 202), ("registration_failure", 206), ("reused_late", 1)])
def test_actual_native_wrapper_catch_finally_preserves_dependencies(case, code):
    setup = RESET_ACTUAL + r'''
$script:proc=New-FakeProcess
$script:BidKingLaunchState.Monitor=New-BidKingOwnedStartupProcess $script:proc 'monitor' $Monitor
$script:BidKingLaunchState.MonitorState='alive'
$UseWssCapture=$true;$Started=$script:proc;$MonitorStartedAt=1
function Get-BidKingInternetCafeResult {return @{}}
function Write-BidKingInternetCafeReceipt {}
function Show-BidKingInternetCafeFailure {}
'''
    if case.startswith("certificate"):
        if case == "certificate_proven_absent":
            setup += "$script:BidKingLaunchState.OverlayState='not_created'\n"
        body = r'''
$cert=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.TryStatementAst] -and $n.CatchClauses.Count -eq 1 -and $n.CatchClauses[0].Body.Extent.Text.Contains('"certificate_monitor_bind"') -and $n.Body.Statements.Count -lt 20},$true))
if($cert.Count -ne 1){throw 'certificate catch not unique'}
$certCatch=($cert[0].CatchClauses[0].Body.Statements|ForEach-Object {$_.Extent.Text}) -join "`n"
$body="try {throw 'synthetic bind failure'} catch {"+$certCatch+"`n}"
'''
    elif case == "registration_failure":
        body = r'''
$PythonWindowed='fake';$OverlayArgs=@();$OverlayErr='unused';$StartedOverlay=$null
function Start-Process {param($FilePath,$WorkingDirectory,$ArgumentList,[switch]$PassThru,$RedirectStandardError);return (New-FakeProcess)}
function New-BidKingOwnedStartupProcess {throw 'synthetic registration failure'}
$spawn=@($tree.FindAll({param($n) $n -is [Management.Automation.Language.TryStatementAst] -and $n.Body.Statements.Count -eq 3 -and $n.Body.Statements[0].Extent.Text.Contains('Start-Process -FilePath $PythonWindowed')},$true))[0]
$siblings=@($spawn.Parent.Statements);$position=[array]::IndexOf($siblings,$spawn)
if($position -lt 1){throw 'pre-registration producer missing'}
$beforeSpawn=$siblings[$position-1]
if($beforeSpawn -isnot [Management.Automation.Language.AssignmentStatementAst] -or $beforeSpawn.Left.Extent.Text -cne '$script:BidKingLaunchState.OverlayState'){throw 'absence invalidation seam changed'}
$script:BidKingLaunchState.OverlayState='not_created'
$body=$beforeSpawn.Extent.Text+"`n"+$spawn.Extent.Text
'''
    elif case == "reused_late":
        body = '$body=\'$script:BidKingLaunchState.OverlayState="reused_preserved";$script:BidKingLaunchState.Succeeded=$true;throw "synthetic late failure"\'\n'
    else:
        # The actual default remains unknown across a failure after monitor
        # creation but before the later overlay query, even if an old fast-path
        # observation happened to say absent.
        body = '$FastOverlayIdentity=@{State="absent"};$body=\'throw "synthetic before query failure"\'\n'
    result = base._run_ps(setup + _native_wrap(body), expected_exit=code)
    assert result["managed"] is True
    allowed = case == "certificate_proven_absent"
    assert result["monitor"] == ("reclaimed" if allowed else "preserved_unknown")
    assert ("kill" in result["calls"]) is allowed
    if case.startswith("certificate"):
        assert result["failure"] == "unknown"


@pytest.mark.parametrize("case,expected_exit,icons,installer", [
    ("missing", 202, [], 0), ("install_failed", 202, ["Information"], 1),
    ("installed", 0, ["Information"], 1), ("wss", 0, [], 0),
    ("unmanaged_missing", 0, ["Warning"], 0), ("unmanaged_failed", 0, ["Information", "Warning"], 1)])
def test_actual_npcap_branch_defers_only_managed_failure(case, expected_exit, icons, installer):
    script = RESET_ACTUAL + r'''
class F01MessageBox {
 static [Collections.Generic.List[string]] $Calls=[Collections.Generic.List[string]]::new()
 static [int] Show([string]$message,[string]$title,[string]$buttons,[string]$icon){[F01MessageBox]::Calls.Add($icon);return 1}
}
$script:case='__CASE__';$script:installerCalls=0
function Add-Type {param($AssemblyName)}
function Get-Service {[CmdletBinding()]param($Name);if($script:case -eq 'installed'){[pscustomobject]@{Name='fake'}}}
function Test-Path {[CmdletBinding()]param($Path);return ($Path -like '*npcap-installer.exe' -and $script:case -notin @('missing','unmanaged_missing'))}
function Start-Process {param($FilePath,[switch]$Wait);$script:installerCalls++}
function Start-Sleep {param($Seconds)}
$ManagedLaunch=($script:case -notlike 'unmanaged*');$UseWssCapture=($script:case -eq 'wss');$NpcapOk=$false
$block=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -ceq '-not $UseWssCapture -and -not $NpcapOk'},$true))[0]
$code=$block.Extent.Text.Replace('[System.Windows.Forms.MessageBox]::Show','[F01MessageBox]::Show')
try {. ([scriptblock]::Create($code))} finally {
 Complete-BidKingStartupAttempt
 @{icons=@([F01MessageBox]::Calls);installer=$script:installerCalls;key=$script:BidKingLaunchState.MessageKey;events=@($script:events)}|ConvertTo-Json -Depth 5 -Compress
}
'''.replace("__CASE__", case)
    result = base._run_ps(script, expected_exit=expected_exit)
    assert result["icons"] == icons and result["installer"] == installer
    if expected_exit:
        assert result["key"] == ("npcap_installer_missing" if case == "missing" else "npcap_install_incomplete")
        assert "timing:startup_terminal/failed" in result["events"]


def test_new_npcap_keys_survive_actual_bootstrap_whitelist(monkeypatch, tmp_path):
    original_write = Path.write_text
    for key in ("npcap_install_incomplete", "npcap_installer_missing"):
        directory = tmp_path / key
        directory.mkdir()
        def write(path, data, *args, **kwargs):
            if path.name.endswith("--cleanup-powershell.json"):
                value = json.loads(data)
                value["message_key"] = key
                data = json.dumps(value)
            return original_write(path, data, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, "write_text", write)
            caught, events, record = base._run_bootstrap(patch, directory, exit_code=202)
        assert isinstance(caught, SystemExit) and caught.code == 202
        assert events[-1] == "release" and record["message_key"] == key


def test_current_messagebox_sink_keeps_actions_and_four_old_bodies(tmp_path):
    current = (ROOT / "native/bidking_trust_bootstrap/src/main.rs").read_text(encoding="utf-8")
    common = r'''
use std::collections::BTreeMap;
use std::cell::RefCell;
use std::path::PathBuf;
use std::fs;
const FAILURE_LOG_SCHEMA:&str="bidking_public_startup_failure_v1";
const MB_OK:u32=0;const MB_ICONERROR:u32=16;const MB_SETFOREGROUND:u32=65536;
#[derive(Clone)]struct Value(String);
impl Value {fn as_str(&self)->Option<&str>{Some(&self.0)}}
type Observation=BTreeMap<String,Value>;
thread_local! {static OBS:RefCell<Option<Observation>>=RefCell::new(None);static CAPTURE:RefCell<String>=RefCell::new(String::new());}
fn cleanup_observation()->Option<Observation>{OBS.with(|v|v.borrow().clone())}
unsafe fn MessageBoxW(_hwnd:isize,text:*const u16,_title:*const u16,_flags:u32)->i32 {
 let mut units=Vec::new();unsafe {for i in 0..8192 {let value=*text.add(i);if value==0 {break;}units.push(value);}}
 CAPTURE.with(|v|*v.borrow_mut()=String::from_utf16_lossy(&units));1
}
fn choose(complete:bool){OBS.with(|v|*v.borrow_mut()=if complete {Some([
 ("acl_restore","restored"),("handle_release","released"),("monitor","not_created"),("overlay","not_created"),("message_key","startup_failed")
].into_iter().map(|(k,v)|(k.to_string(),Value(v.to_string()))).collect())} else {None});}
fn captured()->String {CAPTURE.with(|v|v.borrow().clone())}
'''
    def function(text, name):
        return re.search(r"    (?:pub )?fn " + name + r"\b.*?\n    }", text, re.S).group(0)
    code = common + function(current, "wide")
    code += "\nmod current {use super::*;\n" + "\n".join(function(current, fn) for fn in ("cleanup_display_state", "failure_reason", "report_failure")) + "\n}\n"
    code += r'''
fn select(state:&str,key:&str){
 if state=="unknown" {choose(false);return}
 let (acl,mon)=match state {"incomplete"=>("failed","reclaimed"),"game"=>("restored","preserved_game"),_=>("restored","reclaimed")};
 OBS.with(|v|*v.borrow_mut()=Some([("acl_restore",acl),("handle_release","released"),("monitor",mon),("overlay","exited"),("message_key",key)].into_iter().map(|(k,v)|(k.to_string(),Value(v.to_string()))).collect()));
}
#[test]fn known_integrity_actions(){for state in ["unknown","incomplete","complete","game"]{
 for reason in ["integrity_named_stream_rejected","integrity_file_missing","integrity_directory_missing","integrity_file_hash_mismatch","integrity_coverage_mismatch","package_generation_identity_component_missing"]{
  select(state,"startup_failed");current::report_failure(reason);let m=captured();
  assert!(m.contains("新的空目录"));assert!(!m.contains("请删除"));
  if reason=="integrity_named_stream_rejected"{assert!(m.contains("解除锁定"))}else{assert!(m.contains("完整解压"))}
  if state=="unknown"||state=="incomplete"{assert!(m.contains("保留程序目录"))}
 }
}}
#[test]fn unchanged_four_overlay_bodies(){for state in ["unknown","incomplete","complete","game"]{
 // Exact accepted UTF-16 text from the frozen pre-F01 consumer, not merely
 // substring assertions.  Old/new consumers were also compared during intake.
 let (cleanup,next)=match state {
  "complete"=>("本次启动的收尾已完成；关闭本提示后启动器退出。","先关闭本提示；游戏退出后重试，仍失败时运行“诊断工具\\启动修复.bat”或收集诊断。"),
  "game"=>("为避免中断对局，仍保留后台读取。","先关闭本提示，等游戏完全退出后再启动计算器。"),
  "incomplete"=>("部分资源未能完成收尾，请暂时保留程序目录。","先关闭本提示和游戏；仍无法退出时，运行“诊断工具\\收集诊断.bat”并私发支持人员。"),
  _=>("部分后台仍保留或状态未确认，请暂时保留程序目录。","先关闭本提示和游戏；仍无法退出时，运行“诊断工具\\收集诊断.bat”并私发支持人员。")
 };
 let old:Vec<u16>=format!("悬浮窗未能启动。\r\n\r\n收尾：{}\r\n下一步：{}\r\n\r\n错误代码：overlay_spawn_failed\r\n诊断位置：本机 BidKingLive 日志目录。",cleanup,next).encode_utf16().collect();
 select(state,"startup_failed");current::report_failure("overlay_spawn_failed");let new:Vec<u16>=captured().encode_utf16().collect();
 assert_eq!(old,new,"old accepted body changed: {}",state);println!("UTF16_IDENTICAL:{}:{}",state,new.len());
}}
#[test]fn new_npcap_recovery(){for key in ["npcap_install_incomplete","npcap_installer_missing"]{
 select("incomplete",key);current::report_failure("unknown");let m=captured();
 assert!(m.contains("Npcap"));assert!(m.contains("保留程序目录"));
 if key=="npcap_installer_missing"{assert!(m.contains("https://npcap.com"))}else{assert!(m.contains("vendor\\npcap-installer.exe"))}
 println!("NPCAP_COPY:{}:{}",key,m.replace('\n',"|"));
}}
'''
    source = tmp_path / "f01_copy_consumer.rs"
    executable = tmp_path / "f01_copy_consumer.exe"
    source.write_text(code, encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    compile_result = subprocess.run(["C:/Users/example/.cargo/bin/rustc.exe", "--edition", "2024", "--test", "-C", "debug-assertions=no", str(source), "-o", str(executable)], capture_output=True, encoding="utf-8", timeout=180, creationflags=flags)
    assert compile_result.returncode == 0, compile_result.stderr
    env = os.environ.copy();env["LOCALAPPDATA"] = str(tmp_path / "local")
    result = subprocess.run([str(executable), "--test-threads=1", "--nocapture"], env=env, capture_output=True, encoding="utf-8", timeout=60, creationflags=flags)
    (tmp_path / "COPY.stdout").write_text(result.stdout, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("UTF16_IDENTICAL:") == 4
