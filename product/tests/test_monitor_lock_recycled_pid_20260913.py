"""Owned synthetic locks; never launch/stop a product, service, or game."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import psutil
import pytest

from test_startup_lock_repair_hotfix1 import _ps_quote, _run_powershell, DISCOVERY, REPAIR, ROOT


def _fixture(tmp_path: Path, *, delta: float = 10.0, invalid: str = ""):
    process = psutil.Process()
    monitor = tmp_path / "scripts" / "local_compat_monitor.py"
    logs = tmp_path / "logs"
    logs.mkdir()
    lock = logs / "monitor.lock"
    payload = dict(pid=process.pid, process_started_at=process.create_time() - delta,
                   monitor_path=str(monitor), log_dir=str(logs), package_generation_identity="a" * 64)
    raw = json.dumps(payload)
    if invalid == "duplicate":
        raw = raw[:-1] + ',"pid":1}'
    elif invalid == "oversize":
        raw += " " * 65537
    elif invalid == "generation":
        raw = raw.replace("a" * 64, "bad")
    elif invalid == "epoch":
        payload["process_started_at"] = "bad"
        raw = json.dumps(payload)
    lock.write_text(raw, encoding="utf-8")
    args = (f"-StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(monitor)} "
            f"-ExpectedLogDir {_ps_quote(logs)} -ExpectedPackageGenerationIdentity '{'a' * 64}'")
    return process, lock, args


def _probe(tmp_path: Path, *, name="python", delta=10.0, invalid="", override="", quarantine=False):
    process, lock, args = _fixture(tmp_path, delta=delta, invalid=invalid)
    raw = lock.read_bytes()
    body = f"""
. {_ps_quote(DISCOVERY)}
$real = Microsoft.PowerShell.Management\\Get-Process -Id {process.pid}
$script:stopCalls=0
function Stop-Process {{$script:stopCalls++; throw 'forbidden stop'}}
function Get-Process {{ [CmdletBinding()]param([int]$Id)
  return [pscustomobject]@{{ProcessName='{name}';StartTime=$real.StartTime}}
}}
{override}
$identity=Get-BidKingMonitorLockIdentity {args}
{'function Remove-BidKingMonitorLockIfFingerprint { return "unknown" }' if quarantine else ''}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup {args} -ExpectedIdentity $identity
$again=Invoke-BidKingDeadMonitorLockCleanup {args}
[pscustomobject]@{{state=$identity.State;reason=$identity.Reason;succeeded=$cleanup.Succeeded;
cleanup=$cleanup.State;again=$again.State;exists=(Test-Path -LiteralPath {_ps_quote(lock)});
stop_calls=$script:stopCalls}}|ConvertTo-Json -Compress
"""
    result = _run_powershell(body)
    assert process.is_running()
    assert result["stop_calls"] == 0
    if result["exists"]:
        assert lock.read_bytes() == raw
    return result, lock, raw


@pytest.mark.parametrize("name", ["python", "unrelated-editor"])
@pytest.mark.parametrize("quarantine", [False, True])
def test_later_epoch_recovers_only_old_lock(tmp_path, name, quarantine):
    result, lock, raw = _probe(tmp_path, name=name, quarantine=quarantine)
    assert result["state"] == "stale", result
    assert result["reason"] == "pid_reused", result
    assert result["succeeded"] is True
    assert result["cleanup"] == ("quarantined" if quarantine else "reclaimed")
    assert result["again"] == "absent"
    assert result["exists"] is False
    siblings = list(lock.parent.glob("monitor.lock.quarantine.*"))
    assert len(siblings) == int(quarantine)
    if quarantine:
        assert siblings[0].read_bytes() == raw


@pytest.mark.parametrize("name,delta,state,reason", [
    ("python", 0, "current", "current"),
    ("unrelated-editor", 0, "foreign", "process_name_mismatch"),
    ("python", -10, "unknown", "pid_epoch_mismatch"),
    ("unrelated-editor", -10, "unknown", "pid_epoch_mismatch"),
    ("python", 1, "current", "current"),
])
def test_same_earlier_and_tolerance_epochs_preserved(tmp_path, name, delta, state, reason):
    result, _, _ = _probe(tmp_path, name=name, delta=delta)
    assert (result["state"], result["reason"]) == (state, reason)
    assert result["succeeded"] is False
    assert result["exists"] is True


@pytest.mark.parametrize("invalid", ["duplicate", "oversize", "generation", "epoch"])
def test_invalid_recycled_lock_is_preserved(tmp_path, invalid):
    result, _, _ = _probe(tmp_path, invalid=invalid)
    assert result["state"] == "unknown"
    assert result["exists"] is True
    assert result["succeeded"] is False


@pytest.mark.parametrize("override,reason", [
    ("function Get-Process { throw [UnauthorizedAccessException]::new('denied') }", "process_query_unknown"),
    ("function Get-Process { [pscustomobject]@{ProcessName='editor';StartTime=$null} }", "pid_epoch_unknown"),
])
def test_query_failure_preserves_recycled_lock(tmp_path, override, reason):
    result, _, _ = _probe(tmp_path, override=override)
    assert result["reason"] == reason
    assert result["state"] == "unknown"
    assert result["exists"] is True


@pytest.mark.parametrize("phase,change", [(2, "process"), (4, "process"), (4, "query"), (5, "process"), (3, "file")])
def test_recycled_identity_races_preserve_lock(tmp_path, phase, change):
    process, lock, args = _fixture(tmp_path)
    raw = lock.read_bytes()
    body = f"""
. {_ps_quote(DISCOVERY)}
$real=Microsoft.PowerShell.Management\\Get-Process -Id {process.pid}
$script:calls=0;$script:stops=0
function Stop-Process {{$script:stops++;throw 'forbidden stop'}}
function Get-Process {{[CmdletBinding()]param([int]$Id)
  $script:calls++
  $start=$real.StartTime
  if($script:calls -ge {phase}){{
    if('{change}' -eq 'query'){{throw 'query denied'}}
    if('{change}' -eq 'process'){{$start=$start.AddSeconds(20)}}
    if('{change}' -eq 'file'){{[IO.File]::WriteAllText({_ps_quote(lock)},'{{"replacement":true}}')}}
  }}
  [pscustomobject]@{{ProcessName='unrelated-editor';StartTime=$start}}
}}
$identity=Get-BidKingMonitorLockIdentity {args}
{'function Remove-BidKingMonitorLockIfFingerprint { return "unknown" }' if phase == 5 else ''}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup {args} -ExpectedIdentity $identity
[pscustomobject]@{{succeeded=$cleanup.Succeeded;state=$cleanup.State;calls=$script:calls;stops=$script:stops}}|ConvertTo-Json -Compress
"""
    result = _run_powershell(body)
    assert result["succeeded"] is False, result
    assert result["state"] == "changed", result
    assert result["calls"] >= phase
    assert result["stops"] == 0
    assert process.is_running()
    assert lock.read_bytes() == (b'{"replacement":true}' if change == "file" else raw)
    assert list(lock.parent.glob("*.quarantine.*")) == []


def test_real_non_python_host_survives_exact_old_lock_recovery(tmp_path):
    _, lock, args = _fixture(tmp_path)
    result = _run_powershell(f"""
. {_ps_quote(DISCOVERY)}
$real=Get-Process -Id $PID
$payload=[IO.File]::ReadAllText({_ps_quote(lock)})|ConvertFrom-Json
$payload.pid=$PID
$payload.process_started_at=(([DateTimeOffset]$real.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()/1000.0)-10.0
[IO.File]::WriteAllText({_ps_quote(lock)},($payload|ConvertTo-Json -Compress))
$identity=Get-BidKingMonitorLockIdentity {args}
$result=Invoke-BidKingDeadMonitorLockCleanup {args} -ExpectedIdentity $identity
[pscustomobject]@{{reason=$identity.Reason;name=$identity.ProcessName;succeeded=$result.Succeeded;alive=([bool](Get-Process -Id $PID));exists=(Test-Path -LiteralPath {_ps_quote(lock)})}}|ConvertTo-Json -Compress
""")
    assert result == dict(reason="pid_reused", name="powershell", succeeded=True, alive=True, exists=False)


def _repair_setup(logs: Path) -> str:
    return f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(sys.executable)}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(logs)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
"""


@pytest.mark.parametrize("quarantine", [False, True])
def test_repair_apply_consumes_recycled_lock_without_process_stop(tmp_path, quarantine):
    process, lock, args = _fixture(tmp_path)
    result = _run_powershell(_repair_setup(lock.parent) + f"""
$script:stops=0
function Stop-BidKingVerifiedProcess {{$script:stops++;throw 'forbidden stop'}}
function Get-BidKingGameState {{throw 'recycled lock must not depend on game'}}
$initial=Get-BidKingRepairSnapshot -CurrentGeneration '{'a' * 64}'
{'function Remove-BidKingMonitorLockIfFingerprint { return "unknown" }' if quarantine else ''}
$result=Invoke-BidKingRepairApply -Initial $initial -CurrentGeneration '{'a' * 64}'
[pscustomobject]@{{initial=$initial.Classification;reason=$initial.Identity.Reason;classification=$result.Classification;action=$result.Action;relieved=$result.Relieved;stops=$script:stops;exists=(Test-Path -LiteralPath {_ps_quote(lock)})}}|ConvertTo-Json -Compress
""")
    assert result == dict(initial="dead_lock", reason="pid_reused", classification="repaired",
                          action="dead_lock_quarantined" if quarantine else "dead_lock_removed",
                          relieved=True, stops=0, exists=False)
    assert process.is_running()


@pytest.mark.parametrize("reason", ["pid_epoch_unknown", "lock_nonregular", "lock_query_unknown"])
def test_repair_receipt_fixed_reason_and_relief_are_independent(tmp_path, reason):
    source = REPAIR.read_text(encoding="utf-8-sig")
    function = re.search(r"function Write-BidKingRepairReceipt \{.*?^\}", source, re.M | re.S).group(0)
    # Redirect only the output destination of the real receipt function.
    function = re.sub(r"\[Environment\]::GetFolderPath\(\s*\[Environment\+SpecialFolder\]::MyDocuments\s*\)",
                      lambda _: _ps_quote(tmp_path), function)
    assert "[Environment+SpecialFolder]::MyDocuments" not in function
    result = _run_powershell(function + """
Write-BidKingRepairReceipt -Classification foreign_or_unknown -Action preserved -Relieved:$false -NextStep send_diagnostics_privately -LockReason pid_epoch_unknown
$rejected=$false
try {Write-BidKingRepairReceipt -Classification repaired -Action dead_lock_removed -Relieved:$true -NextStep start_current_package -LockReason 'private-path-or-free-text'}catch{$rejected=$true}
[pscustomobject]@{rejected=$rejected}|ConvertTo-Json -Compress
""".replace("pid_epoch_unknown", reason))
    assert result["rejected"] is True
    receipts = list((tmp_path / "BidKingLive" / "exports").glob("*.txt"))
    assert len(receipts) == 1
    text = receipts[0].read_text(encoding="utf-8")
    assert f"lock_reason: {reason}" in text
    assert "obstacle_relieved: false" in text
    assert "classification: foreign_or_unknown" in text
    assert "private-path-or-free-text" not in text


def test_startup_and_repair_bind_frozen_identity_and_consume_outcomes():
    startup = (ROOT / "scripts" / "start_live_npcap_overlay.ps1").read_text(encoding="utf-8-sig")
    repair = REPAIR.read_text(encoding="utf-8-sig")
    assert "-ExpectedIdentity $EarlyMonitorIdentity" in startup
    assert "-ExpectedIdentity $PreStartIdentity" in startup
    assert "reason={1} obstacle_relieved=false" in startup
    assert "reason={0} obstacle_relieved=true" in startup
    assert "-ExpectedIdentity $current.Identity" in repair
    assert repair.count("-LockReason $snapshot.Identity.Reason") == 3
    assert 'return $(if ($result.Relieved) { 0 } else { 1 })' in repair


def test_recycled_lock_reparse_leaf_preserves_target(tmp_path):
    process, lock, args = _fixture(tmp_path)
    target = lock.parent / "unrelated-target.json"
    raw = lock.read_bytes()
    target.write_bytes(raw)
    lock.unlink()  # Exact owned fixture leaf, before creating the test symlink.
    try:
        os.symlink(target, lock)
    except OSError as error:
        pytest.skip(f"file symlink capability unavailable: winerror={error.winerror}")
    result = _run_powershell(f"""
. {_ps_quote(DISCOVERY)}
$identity=Get-BidKingMonitorLockIdentity {args}
$result=Invoke-BidKingDeadMonitorLockCleanup {args} -ExpectedIdentity $identity
[pscustomobject]@{{reason=$identity.Reason;succeeded=$result.Succeeded;state=$result.State}}|ConvertTo-Json -Compress
""")
    assert result == dict(reason="lock_nonregular", succeeded=False, state="changed")
    assert process.is_running()
    assert lock.is_symlink()
    assert target.read_bytes() == raw


@pytest.mark.parametrize("classification,reason,relieved,expected_exit", [
    ("foreign_or_unknown", "pid_epoch_unknown", False, 1),
    ("no_obstacle", "lock_absent", False, 0),
    ("dead_lock", "pid_reused", True, 0),
    ("dead_lock", "pid_reused", False, 1),
])
def test_repair_entry_distinguishes_completion_from_relief(tmp_path, classification, reason, relieved, expected_exit):
    source = REPAIR.read_text(encoding="utf-8-sig")
    function = re.search(r"function Invoke-BidKingStartupRepair \{.*?^\}", source, re.M | re.S).group(0)
    # Supply an authorized-host substitute; retain the real decision/receipt/exit flow.
    function, count = re.subn(r"\$isAdmin = \(\[Security\.Principal\.WindowsPrincipal\].*?\n    \)",
                              "$isAdmin = $true", function, count=1, flags=re.S)
    assert count == 1
    result_class = "repaired" if relieved else "cleanup_failed"
    result_action = "dead_lock_removed" if relieved else "preserved"
    result = _run_powershell(_repair_setup(tmp_path) + function + f"""
$TrustCleanupRequested=$false;$DryRun=$false
function Test-BidKingRepairInputContract {{return $true}}
function Get-BidKingCurrentGeneration {{return '{'a' * 64}'}}
function Get-BidKingRepairTrustIdentities {{return @()}}
function Get-BidKingCurrentLaunchWssDecision {{return $null}}
function Get-BidKingRepairSnapshot {{return [pscustomobject]@{{Classification='{classification}';Identity=[pscustomobject]@{{Reason='{reason}'}}}}}}
function Confirm-BidKingRepairApply {{return $true}}
function Show-BidKingRepairMessage {{}}
function Invoke-BidKingRepairApply {{return [pscustomobject]@{{Classification='{result_class}';Action='{result_action}';Relieved=${str(relieved).lower()};NextStep='retry_repair'}}}}
$script:receipt=$null
function Write-BidKingRepairReceipt {{param($Classification,$Action,[bool]$Relieved,$NextStep,$LockReason)
  $script:receipt=[pscustomobject]@{{classification=$Classification;action=$Action;relieved=$Relieved;reason=$LockReason}}
}}
$code=Invoke-BidKingStartupRepair
[pscustomobject]@{{code=$code;receipt=$script:receipt}}|ConvertTo-Json -Compress
""")
    assert result["code"] == expected_exit, result
    assert result["receipt"]["reason"] == reason
    assert result["receipt"]["relieved"] is relieved
    assert result["receipt"]["classification"] == (result_class if classification == "dead_lock" else classification)


def _nonleaf_consumer_probe(tmp_path, consumer, replacement, phase="snapshot", absent_pid=False):
    process, lock, args = _fixture(tmp_path)
    raw = lock.read_bytes()
    backup = tmp_path / "old-lock.backup"
    target = tmp_path / "junction-target"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_bytes(b"unrelated target must survive")
    create = {
        "directory": f"$null=New-Item -ItemType Directory -Path {_ps_quote(lock)}",
        "junction": f"$null=New-Item -ItemType Junction -Path {_ps_quote(lock)} -Target {_ps_quote(target)}",
        "file": f"[IO.File]::WriteAllText({_ps_quote(lock)},'replacement')",
        "absent": "",
    }[replacement]
    change = (f"if([IO.File]::Exists({_ps_quote(lock)})){{[IO.File]::Move({_ps_quote(lock)},{_ps_quote(backup)})}}\n" + create)
    pre = f"""
. {_ps_quote(DISCOVERY)}
$script:stops=0
function Stop-Process {{$script:stops++;throw 'FORBIDDEN stop'}}
function Stop-BidKingVerifiedProcess {{$script:stops++;throw 'FORBIDDEN stop'}}
$arguments=@{{StatePath={_ps_quote(lock)};ExpectedMonitorPath={_ps_quote(tmp_path/'scripts/local_compat_monitor.py')};ExpectedLogDir={_ps_quote(lock.parent)};ExpectedPackageGenerationIdentity='{'a'*64}'}}
"""
    if absent_pid:
        pre += """
function Get-Process {[CmdletBinding()]param([int]$Id)
  $PSCmdlet.ThrowTerminatingError([Management.Automation.ErrorRecord]::new([Exception]::new('fixture absent'),'NoProcessFoundForGivenId',[Management.Automation.ErrorCategory]::ObjectNotFound,$Id))
}
"""
    if phase in ("snapshot", "readback"):
        threshold = 4 if phase == "readback" else (2 if consumer == "repair" else 1)
        pre += f"""
$script:readIdentity=${{function:Get-BidKingMonitorLockIdentity}};$script:identityCalls=0
function Get-BidKingMonitorLockIdentity {{param($StatePath,$ExpectedMonitorPath,$ExpectedLogDir,$ExpectedPackageGenerationIdentity)
  $identity=& $script:readIdentity @PSBoundParameters
  $script:identityCalls++
  if($script:identityCalls -eq {threshold}){{{change}}}
  return $identity
}}
"""
    else:
        pre += f"""
$script:clean=${{function:Invoke-BidKingDeadMonitorLockCleanup}}
function Invoke-BidKingDeadMonitorLockCleanup {{param($StatePath,$ExpectedMonitorPath,$ExpectedLogDir,$ExpectedPackageGenerationIdentity,$ExpectedIdentity)
  $result=& $script:clean @PSBoundParameters
  if($result.Succeeded){{{change}}}
  return $result
}}
"""
    if consumer == "helper":
        pre += """
$identity=Get-BidKingMonitorLockIdentity @arguments
$result=Invoke-BidKingDeadMonitorLockCleanup @arguments -ExpectedIdentity $identity
[pscustomobject]@{relieved=$result.Succeeded;state=$result.State;stops=$script:stops}|ConvertTo-Json -Compress
"""
    elif consumer == "repair":
        source = REPAIR.read_text(encoding="utf-8-sig")
        for name in ("Get-BidKingRepairSnapshot", "Test-BidKingRepairSnapshotStable", "Invoke-BidKingRepairApply"):
            pre += re.search(r"^function " + name + r"\b.*?^\}", source, re.M | re.S).group(0) + "\n"
        pre += """
$MonitorLockPath=$arguments.StatePath;$MonitorPath=$arguments.ExpectedMonitorPath;$LogPath=$arguments.ExpectedLogDir
function Get-BidKingRepairMonitorOwnership {return $null}
$initial=Get-BidKingRepairSnapshot -CurrentGeneration ('a'*64)
$result=Invoke-BidKingRepairApply -Initial $initial -CurrentGeneration ('a'*64)
[pscustomobject]@{relieved=$result.Relieved;state=$result.Classification;stops=$script:stops}|ConvertTo-Json -Compress
"""
    else:
        source = (ROOT / "scripts/start_live_npcap_overlay.ps1").read_text(encoding="utf-8-sig")
        start = "$EarlyMonitorIdentity = Get-BidKingMonitorLockIdentity" if consumer == "early" else "$PreStartIdentity = Get-BidKingMonitorLockIdentity"
        end = "# A normal second click is an idempotent request." if consumer == "early" else 'if ($PreStartIdentity.State -eq "stale_package")'
        block = source[source.index(start):source.index(end)]
        pre += f"""
$LockPath=$arguments.StatePath;$Monitor=$arguments.ExpectedMonitorPath;$LogPath=$arguments.ExpectedLogDir
$ExpectedPackageGenerationIdentity='{'a'*64}';$StartupStatus={_ps_quote(tmp_path/'startup.log')}
function Show-FatalBox {{}}
function Write-BidKingStartupFailure {{throw 'BLOCKED'}}
try{{{block}}}catch{{if($_.Exception.Message -ne 'BLOCKED'){{throw}}}}
$status=[IO.File]::ReadAllText($StartupStatus)
[pscustomobject]@{{relieved=$status.Contains('obstacle_relieved=true');state=$status;stops=$script:stops}}|ConvertTo-Json -Compress
"""
    result = _run_powershell(pre)
    assert result["stops"] == 0
    assert process.is_running()
    assert marker.read_bytes() == b"unrelated target must survive"
    if backup.exists():
        assert backup.read_bytes() == raw
    if replacement in ("directory", "junction"):
        assert lock.is_dir()
    elif replacement == "file":
        assert lock.read_bytes() == b"replacement"
    else:
        assert not lock.exists()
    return result


@pytest.mark.parametrize("consumer", ["helper", "early", "prestart", "repair"])
@pytest.mark.parametrize("replacement", ["directory", "junction", "file", "absent"])
def test_nonleaf_snapshot_replacement_is_not_false_relief(tmp_path, consumer, replacement):
    result = _nonleaf_consumer_probe(tmp_path, consumer, replacement)
    assert result["relieved"] is (replacement == "absent"), result


@pytest.mark.parametrize("replacement", ["directory", "junction"])
def test_nonleaf_absent_pid_replacement_preserved(tmp_path, replacement):
    result = _nonleaf_consumer_probe(tmp_path, "helper", replacement, absent_pid=True)
    assert result["relieved"] is False, result


@pytest.mark.parametrize("consumer", ["early", "prestart", "repair"])
@pytest.mark.parametrize("replacement", ["directory", "junction", "file", "absent"])
def test_nonleaf_consumer_requires_actual_success_readback(tmp_path, consumer, replacement):
    result = _nonleaf_consumer_probe(tmp_path, consumer, replacement, phase="after_cleanup")
    assert result["relieved"] is (replacement == "absent"), result


@pytest.mark.parametrize("replacement", ["directory", "junction", "file", "absent"])
def test_nonleaf_helper_rechecks_after_identity_readback(tmp_path, replacement):
    result = _nonleaf_consumer_probe(tmp_path, "helper", replacement, phase="readback")
    assert result["relieved"] is (replacement == "absent"), result


def test_nonleaf_path_primitive_distinguishes_invalid_from_missing(tmp_path):
    leaf = tmp_path / "regular.lock"
    leaf.write_bytes(b"fixture")
    directory = tmp_path / "directory.lock"
    directory.mkdir()
    result = _run_powershell(f"""
. {_ps_quote(DISCOVERY)}
[pscustomobject]@{{
  missing=(Get-BidKingMonitorLockPathState -StatePath {_ps_quote(tmp_path/'missing.lock')});
  regular=(Get-BidKingMonitorLockPathState -StatePath {_ps_quote(leaf)});
  directory=(Get-BidKingMonitorLockPathState -StatePath {_ps_quote(directory)});
  invalid=(Get-BidKingMonitorLockPathState -StatePath ([string][char]0))
}}|ConvertTo-Json -Compress
""")
    assert result == dict(missing="absent", regular="file", directory="nonregular", invalid="unknown")
    assert leaf.read_bytes() == b"fixture" and directory.is_dir()


def test_nonleaf_path_query_unknown_preserves_identity_and_cleanup(tmp_path):
    process, lock, args = _fixture(tmp_path)
    raw = lock.read_bytes()
    result = _run_powershell(f"""
. {_ps_quote(DISCOVERY)}
$script:queries=0
function Get-Process {{$script:queries++;throw 'unexpected query'}}
function Get-BidKingMonitorLockPathState {{ return 'unknown' }}
$identity=Get-BidKingMonitorLockIdentity {args}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup {args}
[pscustomobject]@{{state=$identity.State;reason=$identity.Reason;succeeded=$cleanup.Succeeded;cleanup=$cleanup.State;queries=$script:queries}}|ConvertTo-Json -Compress
""")
    assert result == dict(state="unknown", reason="lock_query_unknown", succeeded=False, cleanup="unknown", queries=0)
    assert lock.read_bytes() == raw and process.is_running()
