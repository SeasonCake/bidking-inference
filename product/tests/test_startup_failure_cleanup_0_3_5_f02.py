"""Absence qualification: actual provider/caller/producer and failure consumer.

Only CIM rows, state-file fallback, game state and process actions are doubles.
No OS process enumeration, service changes or product/UI launch occurs.
"""
from __future__ import annotations

import importlib.util
import base64
import json
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("failure_cleanup_f01_for_f02", ROOT / "tests/test_startup_failure_cleanup_0_3_5_f01.py")
f01 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(f01)


@pytest.fixture(scope='module', autouse=True)
def captured_ps(tmp_path_factory):
    """Persist the actual PS inputs/results; never replace the consumer result."""
    directory = tmp_path_factory.mktemp('f02-ps')
    real_run = f01.base.subprocess.run
    records = []
    def run(command, *args, **kwargs):
        result = real_run(command, *args, **kwargs)
        if '-EncodedCommand' in command:
            stem = directory / str(len(records))
            stem.with_suffix('.ps1').write_text(base64.b64decode(command[-1]).decode('utf-16le'), encoding='utf-8')
            stem.with_suffix('.stdout').write_text(result.stdout, encoding='utf-8')
            stem.with_suffix('.stderr').write_text(result.stderr, encoding='utf-8')
            stem.with_suffix('.json').write_text(json.dumps(dict(command=command, timeout=kwargs.get('timeout'), returncode=result.returncode)), encoding='utf-8')
            records.append(result)
        return result
    with mock.patch.object(f01.base.subprocess, 'run', run):
        yield records

PROVIDER = r'''
$discovery=[IO.File]::ReadAllText('__DISCOVERY__')
$discoveryTree=[Management.Automation.Language.Parser]::ParseInput($discovery,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'discovery parse error'}
foreach($name in @('Reset-BidKingProcessDiscoveryState','Enable-BidKingPidFallback','Get-BidKingOverlayProcesses')) {
 $node=@($discoveryTree.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true))
 if($node.Count -ne 1){throw 'provider function not unique'}
 . ([scriptblock]::Create($node[0].Extent.Text))
}
$caller=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-OverlayProcesses'},$true))
if($caller.Count -ne 1){throw 'caller not unique'}
. ([scriptblock]::Create($caller[0].Extent.Text))
$outer=@($tree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})[0]
$statements=@($outer.Body.Statements);$index=-1
for($i=0;$i -lt $statements.Count;$i++){if($statements[$i] -is [Management.Automation.Language.AssignmentStatementAst] -and $statements[$i].Left.Extent.Text -ceq '$OverlayProcs'){$index=$i}}
if($index -lt 1){throw 'producer missing'}
$producer=($statements[($index-1)..($index+1)]|ForEach-Object {$_.Extent.Text}) -join "`n"
$script:cimCalls=0;$script:fallbackCalls=0;$script:scenario='empty'
function New-Row {param($command,$name='pythonw.exe',$id=4102);return [pscustomobject]@{Name=$name;CommandLine=$command;ProcessId=$id}}
function Get-CimInstance {
 [CmdletBinding()]param($ClassName,$OperationTimeoutSec)
 if($ClassName -cne 'Win32_Process' -or $OperationTimeoutSec -ne 3){throw 'CIM contract changed'}
 $script:cimCalls++
 switch($script:scenario) {
  'empty' {return @()}
  'unrelated' {return (New-Row 'python unrelated.py')}
  'nonpython_null' {return (New-Row $null 'unrelated.exe')}
  'known' {return (New-Row 'pythonw ahmad_tk_overlay.py')}
  'null' {return (New-Row $null)}
  'blank' {return (New-Row '')}
  'whitespace' {return (New-Row " `t`r`n")}
  'missing' {return [pscustomobject]@{Name='pythonw.exe';ProcessId=4102}}
  'mixed_null' {return @((New-Row 'python unrelated.py'),(New-Row $null))}
  'known_mixed' {return @((New-Row $null),(New-Row 'pythonw ahmad_tk_overlay.py' 'pythonw.exe' 4103))}
  'cim_failed' {throw 'synthetic CIM failure'}
  'cim_partial' {New-Row 'python unrelated.py';throw 'synthetic partial CIM failure'}
  'cim_partial_known' {New-Row 'pythonw ahmad_tk_overlay.py';throw 'synthetic partial CIM failure'}
  'field_throws' {$p=New-Row $null;$p.PSObject.Properties.Remove('CommandLine');$p|Add-Member ScriptProperty CommandLine {throw 'synthetic inaccessible field'};return $p}
  default {throw 'unexpected query'}
 }
}
function Get-BidKingProcessFromStateFile {
 param($StatePath,$CommandMarker,$DiscoverySource,$StatusPath)
 if($CommandMarker -cne 'ahmad_tk_overlay.py' -or $DiscoverySource -cne 'ahmad_overlay.pid'){throw 'fallback contract changed'}
 $script:fallbackCalls++
 if($script:scenario -eq 'fallback_known'){return (New-Row 'fallback marker')}
 return @()
}
function Reset-DiscoveryFixture {
 Reset-Fixture
 $init=@($tree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -ceq '$script:BidKingLaunchState'})[0]
 . ([scriptblock]::Create($init.Extent.Text))
 Reset-BidKingProcessDiscoveryState
 $script:cimCalls=0;$script:fallbackCalls=0
 $script:proc=New-FakeProcess
 $script:BidKingLaunchState.Monitor=New-BidKingOwnedStartupProcess $script:proc 'monitor' $Monitor
 $script:BidKingLaunchState.MonitorState='alive'
}
function Read-Outcome {
 $state=$script:BidKingLaunchState.OverlayState
 Complete-BidKingStartupAttempt
 return @{overlay=$state;monitor=$script:BidKingLaunchState.MonitorState;calls=@($script:proc.Calls);qualified=[bool]$script:BidKingOverlayAbsenceQualified;cim_available=[bool]$script:BidKingCimProcessEnumerationAvailable;cim_calls=$script:cimCalls;fallback_calls=$script:fallbackCalls;ids=@($OverlayProcs|ForEach-Object {$_.ProcessId})}
}
'''.replace('__DISCOVERY__', str(ROOT / 'scripts/live_process_discovery.ps1').replace("'", "''"))

CASES = ['empty', 'unrelated', 'nonpython_null', 'known', 'null', 'blank', 'whitespace', 'missing', 'mixed_null', 'known_mixed', 'cim_failed', 'cim_partial', 'cim_partial_known', 'field_throws', 'fallback_empty', 'fallback_known']


@pytest.fixture(scope='module')
def discovery_rows():
    body = PROVIDER + r'''
$rows=@{}
foreach($script:scenario in @(__CASES__)) {
 Reset-DiscoveryFixture
 $ManagedLaunch=$false;$NoOverlay=$false;$UseWssCapture=$true
 if($script:scenario -like 'fallback_*'){Enable-BidKingPidFallback}
 . ([scriptblock]::Create($producer))
 $rows[$script:scenario]=Read-Outcome
}
$rows|ConvertTo-Json -Depth 6 -Compress
'''.replace('__CASES__', ','.join("'" + case + "'" for case in CASES))
    rows = f01.base._run_ps(body)
    print('PROVIDER_ROWS=' + json.dumps(rows, sort_keys=True))
    return rows


@pytest.mark.parametrize('case', CASES)
def test_actual_provider_qualifies_only_proven_absence(discovery_rows, case):
    row = discovery_rows[case]
    allowed = case in {'empty', 'unrelated', 'nonpython_null'}
    assert f01._dependency_observer(row, allowed), row
    assert row['qualified'] is allowed
    known = case in {'known', 'known_mixed', 'fallback_known', 'cim_partial_known'}
    assert row['overlay'] == ('not_created' if allowed else ('reused_preserved' if known else 'preserved_unknown'))
    assert row['ids'] == ([4103] if case == 'known_mixed' else ([4102] if known else []))
    # Missing metadata must not mark the CIM transport unavailable.
    fallback = case in {'cim_failed', 'cim_partial', 'cim_partial_known', 'fallback_empty', 'fallback_known'}
    assert row['cim_available'] is not fallback
    assert row['cim_calls'] == (0 if case.startswith('fallback_') else 1)
    assert row['fallback_calls'] == int(fallback)


@pytest.mark.parametrize('case', ['null', 'blank', 'whitespace', 'missing', 'cim_failed', 'fallback_empty', 'reset', 'no_overlay'])
def test_prior_qualified_result_does_not_authorize_later_unknown(case):
    body = PROVIDER + r'''
Reset-DiscoveryFixture
$ManagedLaunch=$false;$NoOverlay=$false;$UseWssCapture=$true
$script:scenario='empty'
. ([scriptblock]::Create($producer))
if(-not $script:BidKingOverlayAbsenceQualified -or $script:BidKingLaunchState.OverlayState -ne 'not_created'){throw 'first known-good query did not qualify'}
$script:scenario='__CASE__'
if($script:scenario -eq 'reset') {
 Reset-BidKingProcessDiscoveryState
 $script:BidKingLaunchState.OverlayState='preserved_unknown'
 $OverlayProcs=@()
} else {
 if($script:scenario -eq 'fallback_empty'){Enable-BidKingPidFallback}
 if($script:scenario -eq 'no_overlay'){$NoOverlay=$true}
 . ([scriptblock]::Create($producer))
}
Read-Outcome|ConvertTo-Json -Depth 6 -Compress
'''.replace('__CASE__', case)
    row = f01.base._run_ps(body)
    print('REPEAT_' + case + '=' + json.dumps(row, sort_keys=True))
    assert f01._dependency_observer(row, False), row
    assert row['overlay'] == 'preserved_unknown'
    if case != 'no_overlay':
        assert row['qualified'] is False
    assert row['cim_calls'] == (1 if case in {'reset', 'no_overlay', 'fallback_empty'} else 2)


@pytest.mark.parametrize('case', ['empty', 'null', 'blank', 'whitespace', 'missing'])
def test_actual_native_wrapper_preserves_dependency_at_pre_spawn_write_failure(case, captured_ps):
    setup = PROVIDER + r'''
Reset-DiscoveryFixture
$NoOverlay=$false;$NoTaskbar=$true;$UseWssCapture=$true;$KeepMonitorOnClose=$false
$script:scenario='__CASE__'
$MonitorPid=4101;$Snapshot='fake';$LockPath='fake';$OverlayErr='fake'
function Q {param($value);return $value}
function Resolve-BidKingWindowedPythonPath {param($PythonPath,[switch]$AllowConsoleFallback);return 'fake-pythonw'}
function Set-Content {param($Path,$Encoding);if($Path -cne $OverlayErr){throw 'wrong seam'};throw 'synthetic overlay log write denied'}
function Start-Process {throw 'unexpected spawn reached'}
'''.replace('__CASE__', case)
    body = r'''
$spawnIf=$statements[$index+2]
if($spawnIf -isnot [Management.Automation.Language.IfStatementAst] -or $spawnIf.Clauses[0].Item1.Extent.Text -cne '-not $NoOverlay -and -not $OverlayProcs'){throw 'actual pre-spawn consumer changed'}
$body=$producer+"`n"+$spawnIf.Extent.Text
'''
    row = f01.base._run_ps(setup + f01._native_wrap(body), expected_exit=1)
    assert 'synthetic overlay log write denied' in captured_ps[-1].stderr
    assert 'unexpected spawn reached' not in captured_ps[-1].stderr
    print('NATIVE_' + case + '=' + json.dumps(row, sort_keys=True))
    assert row['managed'] is True and row['failure'] == 'unknown' and row['key'] == 'startup_failed'
    assert f01._dependency_observer(row, case == 'empty'), row
    assert row['overlay'] == ('not_created' if case == 'empty' else 'preserved_unknown')


def test_same_checker_rejects_saved_formal_incomplete_rows_and_accepts_empty():
    # Frozen D/DISCOVERY[_SIBLING]_CHECKS + PROVIDER_CHAIN_CHECKS observations;
    # audit binding 45410891... preserves the original executions, not rerun here.
    row = {'overlay': 'not_created', 'monitor': 'reclaimed',
           'calls': ['close', 'wait:2000', 'kill', 'wait:5000', 'dispose']}
    assert not f01._dependency_observer(row, False)  # null / blank / whitespace
    assert f01._dependency_observer(row, True)       # actual empty positive
    assert row['calls'][2] == 'kill'


def test_provider_initial_qualification_is_unknown_before_first_query():
    row = f01.base._run_ps(PROVIDER + r'''
$initial=@($discoveryTree.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -ceq '$script:BidKingOverlayAbsenceQualified'})
if($initial.Count -ne 1){throw 'qualification initialization not unique'}
. ([scriptblock]::Create($initial[0].Extent.Text))
@{qualified=[bool]$script:BidKingOverlayAbsenceQualified;cim_calls=$script:cimCalls}|ConvertTo-Json -Compress
''')
    assert row == {'qualified': False, 'cim_calls': 0}
