"""Original PowerShell harness, synthetic process rows, no real process control."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
from test_wmi_startup_fallback import (  # noqa: E402
    _json_stdout, _launcher_restart_function, _ps_quote, _run_powershell,
)

DISCOVERY = ROOT / "scripts/live_process_discovery.ps1"
LAUNCHER = ROOT / "scripts/start_live_npcap_overlay.ps1"


def _fixture(tmp_path: Path) -> str:
    return f"""
. {_ps_quote(DISCOVERY)}
Reset-BidKingProcessDiscoveryState
$script:Queries=0
$script:Rows=@(
 [pscustomobject]@{{Name='python.exe';ProcessId=101;CommandLine='python.exe "C:/fixture/monitor.py" --log-dir "C:/fixture/logs" --package-generation-identity {"a" * 64}'}},
 [pscustomobject]@{{Name='python.exe';ProcessId=102;CommandLine='python.exe "C:/fixture/monitor.py" --log-dir "C:/fixture/logs" --package-generation-identity {"b" * 64}'}},
 [pscustomobject]@{{Name='pythonw.exe';ProcessId=201;CommandLine='pythonw.exe ahmad_tk_overlay.py'}},
 [pscustomobject]@{{Name='python.exe';ProcessId=202;CommandLine=$null}},
 [pscustomobject]@{{Name='unrelated.exe';ProcessId=999;CommandLine=$null}}
)
function Get-CimInstance {{
 [CmdletBinding()]
 param([string]$ClassName,[int]$OperationTimeoutSec)
 $script:Queries++
 if($ClassName -cne 'Win32_Process' -or $OperationTimeoutSec -ne 3){{throw 'query contract changed'}}
 if($script:QueryFailure){{throw 'synthetic query failure'}}
 return $script:Rows
}}
$script:Status={_ps_quote(tmp_path / "startup_status.log")}
$script:LiveArgs=@{{MonitorLockPath={_ps_quote(tmp_path / "missing.lock")};StatusPath=$script:Status;ExpectedMonitorPath='C:/fixture/monitor.py';ExpectedLogDir='C:/fixture/logs';ExpectedPackageGenerationIdentity='{"a" * 64}'}}
$script:ConflictArgs=@{{StatusPath=$script:Status;ExpectedMonitorPath='C:/fixture/monitor.py';ExpectedLogDir='C:/fixture/logs';ExpectedPackageGenerationIdentity='{"a" * 64}'}}
$script:OverlayArgs=@{{OverlayPidPath={_ps_quote(tmp_path / "missing.pid")};StatusPath=$script:Status}}
"""


def test_explicit_phase_reuses_one_query_for_three_consumers(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$live=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$conflicts=@(Get-BidKingMonitorGenerationConflicts @script:ConflictArgs -InventoryPhase 'final_check')
$overlay=@(Get-BidKingOverlayProcesses @script:OverlayArgs -InventoryPhase 'final_check')
[pscustomobject]@{queries=$script:Queries;live=@($live.ProcessId);conflicts=@($conflicts.ProcessId);overlay=@($overlay.ProcessId);absence=$script:BidKingOverlayAbsenceQualified}|ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"queries": 1, "live": [101], "conflicts": [102], "overlay": [201], "absence": False}


def test_unchanged_phase_reuses_but_input_mutation_requires_invalidation(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$first=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$again=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$unchangedQueries=$script:Queries
$script:Rows=@([pscustomobject]@{Name='python.exe';ProcessId=303;CommandLine='python.exe C:/other/monitor.py --log-dir C:/fixture/logs --package-generation-identity aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'})
Invalidate-BidKingProcessInventory -Reason 'process_start' -StatusPath $script:Status
$changed=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$conflicts=@(Get-BidKingMonitorGenerationConflicts @script:ConflictArgs -InventoryPhase 'final_check')
[pscustomobject]@{first=@($first.ProcessId);again=@($again.ProcessId);unchanged_queries=$unchangedQueries;changed_count=$changed.Count;conflicts=@($conflicts.GenerationState);queries=$script:Queries}|ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"first": [101], "again": [101], "unchanged_queries": 1,
                      "changed_count": 0, "conflicts": ["foreign_context"], "queries": 2}


def test_new_phase_refresh_and_new_launch_never_reuse_previous_rows(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$first=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$script:Rows=@()
$next=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'next_phase')
$afterNext=$script:Queries
$script:Rows=@([pscustomobject]@{Name='pythonw.exe';ProcessId=404;CommandLine='pythonw.exe ahmad_tk_overlay.py'})
$refreshed=@(Get-BidKingOverlayProcesses @script:OverlayArgs -InventoryPhase 'next_phase' -Refresh)
$afterRefresh=$script:Queries
Reset-BidKingProcessDiscoveryState
$afterReset=@(Get-BidKingOverlayProcesses @script:OverlayArgs -InventoryPhase 'next_phase')
[pscustomobject]@{first=@($first.ProcessId);next_count=$next.Count;after_next=$afterNext;refreshed=@($refreshed.ProcessId);after_refresh=$afterRefresh;after_reset=@($afterReset.ProcessId);queries=$script:Queries}|ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"first": [101], "next_count": 0, "after_next": 2,
                      "refreshed": [404], "after_refresh": 3, "after_reset": [404], "queries": 4}


def test_expired_success_is_fresh_and_unphased_callers_keep_original_reads(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$first=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$script:Rows=@()
$script:BidKingProcessInventory.CapturedTick=[Diagnostics.Stopwatch]::GetTimestamp()-[Diagnostics.Stopwatch]::Frequency
$expired=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$legacy1=@(Get-BidKingLiveProcesses @script:LiveArgs)
$legacy2=@(Get-BidKingLiveProcesses @script:LiveArgs)
[pscustomobject]@{first=@($first.ProcessId);expired_count=$expired.Count;legacy1=$legacy1.Count;legacy2=$legacy2.Count;queries=$script:Queries}|ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"first": [101], "expired_count": 0, "legacy1": 0, "legacy2": 0, "queries": 4}


def test_uncached_observer_invalidates_an_older_phase_observation(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$first=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$script:Rows=@()
$legacy=@(Get-BidKingLiveProcesses @script:LiveArgs)
$next=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
[pscustomobject]@{first=@($first.ProcessId);legacy=$legacy.Count;next=$next.Count;queries=$script:Queries}|ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"first": [101], "legacy": 0, "next": 0, "queries": 3}


def test_query_failure_is_not_absence_and_refresh_can_recover(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$script:QueryFailure=$true
$live=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$overlay=@(Get-BidKingOverlayProcesses @script:OverlayArgs -InventoryPhase 'final_check')
$conflicts=@(Get-BidKingMonitorGenerationConflicts @script:ConflictArgs -InventoryPhase 'final_check')
$failureQueries=$script:Queries
$absence=$script:BidKingOverlayAbsenceQualified
$available=$script:BidKingCimProcessEnumerationAvailable
$script:QueryFailure=$false
$next=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'next_phase')
$noRetryQueries=$script:Queries
$recovered=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'next_phase' -Refresh)
[pscustomobject]@{failure_queries=$failureQueries;absence=$absence;available=$available;next_count=$next.Count;no_retry_queries=$noRetryQueries;recovered=@($recovered.ProcessId);queries=$script:Queries;recovered_available=$script:BidKingCimProcessEnumerationAvailable}|ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"failure_queries": 1, "absence": False, "available": False,
                      "next_count": 0, "no_retry_queries": 1, "recovered": [101],
                      "queries": 2, "recovered_available": True}


def test_complete_empty_query_qualifies_absence_and_mutation_revokes_it(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$script:Rows=@()
$empty=@(Get-BidKingOverlayProcesses @script:OverlayArgs -InventoryPhase 'overlay_check')
$qualified=$script:BidKingOverlayAbsenceQualified
Invalidate-BidKingProcessInventory -Reason 'process_start' -StatusPath $script:Status
$revoked=$script:BidKingOverlayAbsenceQualified
$script:Rows=@([pscustomobject]@{Name='python.exe';ProcessId=505;CommandLine=$null})
$unknown=@(Get-BidKingOverlayProcesses @script:OverlayArgs -InventoryPhase 'overlay_check')
[pscustomobject]@{empty=$empty.Count;qualified=$qualified;revoked=$revoked;unknown=$unknown.Count;unknown_absence=$script:BidKingOverlayAbsenceQualified;queries=$script:Queries}|ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"empty": 0, "qualified": True, "revoked": False,
                      "unknown": 0, "unknown_absence": False, "queries": 2}


def test_inventory_status_never_serializes_process_rows_or_private_phase(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$null=Get-BidKingProcessInventory -PhaseToken 'final_check' -StatusPath $script:Status
$null=Get-BidKingProcessInventory -PhaseToken 'final_check' -StatusPath $script:Status
Invalidate-BidKingProcessInventory -Reason 'PRIVATE_TOKEN_C:/private' -StatusPath $script:Status
$null=Get-BidKingProcessInventory -PhaseToken 'PRIVATE_TOKEN_C:/private' -StatusPath $script:Status
$text=[IO.File]::ReadAllText($script:Status)
[pscustomobject]@{has_private=$text.Contains('PRIVATE_TOKEN') -or $text.Contains('C:/fixture') -or $text.Contains('CommandLine') -or $text.Contains('ProcessId');has_query_count=$text.Contains('query_count=1');has_cache_hit=$text.Contains('cache_hit=true');reason_unknown=$text.Contains('invalidation_reason=unknown')}|ConvertTo-Json -Compress
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"has_private": False, "has_query_count": True,
                      "has_cache_hit": True, "reason_unknown": True}


def test_consumer_field_failure_preserves_fallback_and_revokes_absence(tmp_path: Path) -> None:
    body = _fixture(tmp_path) + """
$script:Rows=@([pscustomobject]@{Name='python.exe';ProcessId=606;CommandLine='python.exe monitor.py'})
function Test-BidKingMonitorCommandIdentity {throw 'PRIVATE_ROW_DETAIL'}
$script:BidKingOverlayAbsenceQualified=$true
$live=@(Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase 'final_check')
$revokedBeforeOverlay=$script:BidKingOverlayAbsenceQualified
$overlay=@(Get-BidKingOverlayProcesses @script:OverlayArgs -InventoryPhase 'final_check')
$text=[IO.File]::ReadAllText($script:Status)
[pscustomobject]@{live=$live.Count;overlay=$overlay.Count;available=$script:BidKingCimProcessEnumerationAvailable;absence=$script:BidKingOverlayAbsenceQualified;revoked_before_overlay=$revokedBeforeOverlay;queries=$script:Queries;private=$text.Contains('PRIVATE_ROW_DETAIL')}|ConvertTo-Json -Compress
"""
    result = _json_stdout(_run_powershell(body))
    assert result == {"live": 0, "overlay": 0, "available": False,
                      "absence": False, "revoked_before_overlay": False, "queries": 1, "private": False}


def test_launcher_wires_explicit_fresh_creation_phases_and_mutation_finally() -> None:
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    fast_begin = source.index("# A normal second click is an idempotent request.")
    fast_end = source.index("# --- 0.3.0 WSS cold-start preflight ---")
    assert "Get-BidKingProcessInventory" not in source[fast_begin:fast_end]
    assert "InventoryPhase" not in source[fast_begin:fast_end]
    assert 'Assert-BidKingMonitorGenerationConflictAbsent -InventoryPhase "monitor_pre_spawn"' in source
    assert 'Get-LiveProcesses -InventoryPhase "monitor_pre_spawn"' in source
    assert 'Get-OverlayProcesses -InventoryPhase "overlay_pre_spawn" -Refresh:' in source
    for marker in ("$Started = Start-Process", "$StartedOverlay = Start-Process"):
        start = source.index(marker)
        before = source[source.rfind('Invalidate-BidKingProcessInventory', 0, start):start]
        after = source[start:source.index("$MonitorPid = $Started.Id", start)] if marker == "$Started = Start-Process" else source[start:source.index("if ($StartedOverlay -and $StartedOverlay.Id)", start)]
        assert 'Reason "process_start"' in before
        assert '} finally {' in after and 'Reason "process_start"' in after


@pytest.mark.parametrize("stop_fails", [False, True])
def test_actual_restart_phase_invalidates_even_after_failed_mutation(tmp_path: Path,
                                                                   stop_fails: bool) -> None:
    body = _fixture(tmp_path) + _launcher_restart_function() + f"""
$script:Rows=@($script:Rows | Where-Object {{$_.ProcessId -in @(101,201)}})
$LockPath={_ps_quote(tmp_path / "missing.lock")}
$OverlayPidPath={_ps_quote(tmp_path / "missing.pid")}
$Monitor='C:/fixture/monitor.py';$LogPath='C:/fixture/logs';$StartupStatus=$script:Status
$script:Stops=0
function Get-BidKingMonitorLockIdentity {{[pscustomobject]@{{State='absent';Reason='missing'}}}}
function Get-LiveProcesses {{param([string]$InventoryPhase,[switch]$Refresh);Get-BidKingLiveProcesses @script:LiveArgs -InventoryPhase $InventoryPhase -Refresh:$Refresh}}
function Get-OverlayProcesses {{param([string]$InventoryPhase,[switch]$Refresh);Get-BidKingOverlayProcesses @script:OverlayArgs -InventoryPhase $InventoryPhase -Refresh:$Refresh}}
function Stop-Process {{
 [CmdletBinding()]
 param([int]$Id,[switch]$Force)
 $script:Stops++
 if(${str(stop_fails).lower()}){{
  $script:Rows=@([pscustomobject]@{{Name='python.exe';ProcessId=303;CommandLine='python.exe C:/other/monitor.py --log-dir C:/fixture/logs --package-generation-identity {"a" * 64}'}})
  throw 'PRIVATE_STOP_DETAIL'
 }}
 $script:Rows=@($script:Rows | Where-Object {{$_.ProcessId -ne $Id}})
}}
$failed=$false;$result=$null
try {{$result=Invoke-BidKingRestartCleanup}} catch {{$failed=$true}}
$cacheEmpty=$null -eq $script:BidKingProcessInventory
$queryCount=$script:Queries
$newConflicts=@(Get-BidKingMonitorGenerationConflicts @script:ConflictArgs -InventoryPhase 'restart_after')
$text=[IO.File]::ReadAllText($script:Status)
[pscustomobject]@{{failed=$failed;succeeded=if($result){{$result.Succeeded}}else{{$null}};stops=$script:Stops;query_count=$queryCount;cache_empty=$cacheEmpty;new_conflicts=@($newConflicts | ForEach-Object {{$_.GenerationState}});after_queries=$script:Queries;private=$text.Contains('PRIVATE_STOP_DETAIL')}}|ConvertTo-Json -Compress -Depth 5
"""
    result = _json_stdout(_run_powershell(body))
    if stop_fails:
        assert result == {"failed": True, "succeeded": None, "stops": 1,
                          "query_count": 1, "cache_empty": True,
                          "new_conflicts": ["foreign_context"], "after_queries": 2, "private": False}
    else:
        assert result == {"failed": False, "succeeded": True, "stops": 2,
                          "query_count": 4, "cache_empty": False,
                          "new_conflicts": [], "after_queries": 5, "private": False}
