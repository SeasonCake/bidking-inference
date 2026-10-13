"""Actual fixed-member inputs, not a full build or encrypted release audit."""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import time
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGET = '诊断工具/activation_transport_diagnostic.py'
OLD_PIN = '18e8473e48f8a8f01a9e911f0f60dce129bcb9fa32e2ea6d1f34a38a6c02967f'
DIRECT = {
    '诊断工具/收集诊断.bat': 'scripts/collect_diagnostics.bat',
    '诊断工具/启动修复.bat': 'scripts/startup_repair.bat',
    '诊断工具/激活网络诊断.bat': 'scripts/activation_transport_diagnostic.bat',
    TARGET: 'scripts/activation_transport_diagnostic.py',
    'scripts/package_data_fingerprints.json': 'scripts/package_data_fingerprints.json',
}


def identity(path):
    raw = path.read_bytes()
    return {'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}


@pytest.fixture(scope='module')
def auditor():
    path = ROOT / 'scripts/audit_release_zip.py'
    spec = importlib.util.spec_from_file_location('fixed_inputs_release_auditor', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixed_family_checker(auditor, archive, contract):
    members, _casefolded, issues = auditor._strict_release_member_index(archive)
    if issues:
        return issues
    required = {name for group in ('common', 'encrypted', 'standard')
                for name in contract['required_members'][group]}
    return auditor._fixed_member_issues(archive, contract['fixed_member_sha256'], required, members)


@pytest.fixture(scope='module')
def mini_stage(tmp_path_factory):
    started = time.monotonic()
    root = tmp_path_factory.mktemp('fixed-mini-stage')
    stage = root/'stage'
    stage.mkdir()
    for relative in ('诊断工具','scripts','其他启动方式','runtime/python'):
        (stage/relative).mkdir(parents=True,exist_ok=True)
    tracked = [*DIRECT.values(),'scripts/build_live_portable.ps1','scripts/runtime_python_source_manifest.json',
               'scripts/audit_release_zip.py','scripts/release_zip_contract.json']
    before = {rel:identity(ROOT/rel) for rel in tracked}
    manifest = json.loads((ROOT/'scripts/runtime_python_source_manifest.json').read_text('utf-8'))
    embed_spec = manifest['python']['embed']
    embed = ROOT/'dist/.cache'/embed_spec['filename']
    assert identity(embed) == {key:embed_spec[key] for key in ('bytes','sha256')}, 'runtime archive input assembly failure'
    # Only these actual builder AST assignments/copy commands/BAT producer run.
    script = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$Repo='__REPO__';$Stage='__STAGE__'
$scriptsDst=Join-Path $Stage 'scripts';$diagnosticsDir=Join-Path $Stage '诊断工具';$subDir=Join-Path $Stage '其他启动方式'
$e=$null;$t=$null;$tree=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Repo 'scripts/build_live_portable.ps1'),[ref]$t,[ref]$e)
if($e.Count){throw 'builder parse error'}
$executed=[Collections.Generic.List[string]]::new()
foreach($function in @('Initialize-TrustedFileHashCommand','Get-VerifiedReleaseDiagnosticInputs')) {
 $nodes=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $function},$true))
 if($nodes.Count -ne 1){throw "builder function not unique: $function"}
 . ([scriptblock]::Create($nodes[0].Extent.Text));$executed.Add($nodes[0].Extent.Text)
}
$TrustedFileHashCommand=Initialize-TrustedFileHashCommand -PowerShellHome $PSHOME
$ReleaseZipContract=Get-Content -LiteralPath (Join-Path $Repo 'scripts/release_zip_contract.json') -Raw -Encoding utf8|ConvertFrom-Json
foreach($variable in @('$ReleaseDiagnosticInputs')) {
 $nodes=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq $variable},$true))
 if($nodes.Count -ne 1){throw "builder preflight call not unique: $variable"}
 . ([scriptblock]::Create($nodes[0].Extent.Text));$executed.Add($nodes[0].Extent.Text)
}
foreach($variable in @('$diagBat','$repairBat','$transportDiagPy','$transportDiagBat','$packageDataFingerprintPath','$packageDataFingerprintDst','$bat','$batBytes')) {
 $nodes=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq $variable},$true))
 if($nodes.Count -ne 1){throw "builder assignment not unique: $variable"}
 . ([scriptblock]::Create($nodes[0].Extent.Text));$executed.Add($nodes[0].Extent.Text)
}
foreach($exact in @(
 'Copy-Item -LiteralPath $diagBat -Destination (Join-Path $diagnosticsDir "收集诊断.bat") -Force',
 'Copy-Item -LiteralPath $repairBat -Destination (Join-Path $diagnosticsDir "启动修复.bat") -Force',
 'Copy-Item $transportDiagPy (Join-Path $diagnosticsDir "activation_transport_diagnostic.py") -Force',
 'Copy-Item $transportDiagBat (Join-Path $diagnosticsDir "激活网络诊断.bat") -Force',
 'Copy-Item -LiteralPath $packageDataFingerprintPath -Destination $packageDataFingerprintDst -Force'
)) {
 $nodes=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst] -and $n.Extent.Text -ceq $exact},$true))
 if($nodes.Count -ne 1){throw 'builder copy not unique'}
 . ([scriptblock]::Create($nodes[0].Extent.Text));$executed.Add($nodes[0].Extent.Text)
}
$loop=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.ForEachStatementAst] -and $n.Variable.Extent.Text -ceq '$batName'},$true))
if($loop.Count -ne 1){throw 'builder wrapper loop not unique'}
. ([scriptblock]::Create($loop[0].Extent.Text));$executed.Add($loop[0].Extent.Text)
@{executed=@($executed)}|ConvertTo-Json -Depth 4 -Compress
'''.replace('__REPO__',str(ROOT).replace("'","''")).replace('__STAGE__',str(stage).replace("'","''"))
    (root/'assembly.ps1').write_text(script,encoding='utf-8')
    command = ['C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-NonInteractive','-WindowStyle','Hidden',
               '-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()]
    completed = subprocess.run(command,capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (root/'assembly.stdout').write_bytes(completed.stdout)
    (root/'assembly.stderr').write_bytes(completed.stderr)
    (root/'assembly.run.json').write_text(json.dumps(dict(command=command,exit=completed.returncode,hard_timeout=20)),encoding='utf-8')
    assert completed.returncode == 0, completed.stdout.decode('utf-8','replace')+completed.stderr.decode('utf-8','replace')
    with zipfile.ZipFile(embed) as archive:
        info = archive.getinfo('python.exe')
        runtime = archive.read(info)
    assert hashlib.sha256(runtime).hexdigest() == embed_spec['python_exe_sha256']
    with (stage/'runtime/python/python.exe').open('xb') as output:
        output.write(runtime)
    contract = json.loads((ROOT/'scripts/release_zip_contract.json').read_text('utf-8'))
    assert set(contract['fixed_member_sha256']) == {
        *DIRECT, '其他启动方式/启动.bat', '其他启动方式/start.bat', 'runtime/python/python.exe',
    }
    files = {name:(stage/name).read_bytes() for name in contract['fixed_member_sha256']}
    archive_path = root/'fixed-members-only.zip'
    with zipfile.ZipFile(archive_path,'x',compression=zipfile.ZIP_STORED) as archive:
        for name,raw in files.items():
            archive.writestr(name,raw)
    after = {rel:identity(ROOT/rel) for rel in tracked}
    assert before == after
    assert time.monotonic()-started < 30
    inputs = {'source_before':before,'source_after':after,'archive':{'path':str(embed),**identity(embed)},
              'members':{name:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in files.items()},
              'seconds':time.monotonic()-started,'runtime_executed':False}
    (root/'INPUTS_OUTPUTS.json').write_text(json.dumps(inputs,ensure_ascii=False,indent=2),encoding='utf-8')
    return root,archive_path,files,contract


@pytest.mark.parametrize('member',DIRECT)
def test_direct_fixed_member_pin_matches_current_copy_source(member):
    contract = json.loads((ROOT/'scripts/release_zip_contract.json').read_text('utf-8'))
    assert member in contract['required_members']['common']
    assert contract['fixed_member_sha256'][member] == identity(ROOT/DIRECT[member])['sha256']


def test_all_eight_actual_builder_inputs_match_fixed_contract(mini_stage, auditor):
    root,path,files,contract = mini_stage
    scope = {'source': str(ROOT/'scripts/audit_release_zip.py'),
             'identity': identity(ROOT/'scripts/audit_release_zip.py'),
             'functions': ['_strict_release_member_index', '_fixed_member_issues']}
    (root/'CHECKER_SCOPE.json').write_text(json.dumps(scope,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(path) as archive:
        issues = fixed_family_checker(auditor, archive, contract)
    (root/'CURRENT_CHECK.json').write_text(json.dumps({'issues':issues},ensure_ascii=False),encoding='utf-8')
    assert issues == []


@pytest.mark.parametrize('case',['old_pin','wrong_byte','missing'])
@pytest.mark.parametrize('member', [TARGET, 'scripts/package_data_fingerprints.json'])
def test_same_fixed_checker_rejects_diagnostic_regressions(mini_stage, auditor, case, member):
    root,path,files,contract = mini_stage
    contract = copy.deepcopy(contract)
    # Only the target differs; the other seven exact files/expected pins remain.
    changed = dict(files)
    if case == 'old_pin':
        contract['fixed_member_sha256'][member] = (OLD_PIN if member == TARGET else
            'ab7a881237ff2cdba323fe820937b2c1e575adcc766a916a12e6116ac54e7610')
    elif case == 'wrong_byte':
        raw = changed[member]
        changed[member] = bytes([raw[0]^1])+raw[1:]
        assert len(changed[member]) == len(raw)
    else:
        del changed[member]
    payload = io.BytesIO()
    with zipfile.ZipFile(payload,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,raw in changed.items():
            archive.writestr(name,raw)
    payload.seek(0)
    with zipfile.ZipFile(payload) as archive:
        issues = fixed_family_checker(auditor, archive, contract)
    expected = ['release_fixed_member_'+('missing' if case == 'missing' else 'hash_mismatch')+': '+member]
    suffix = 'diagnostic' if member == TARGET else 'fingerprint'
    (root/('CONTROL_'+suffix+'_'+case+'.json')).write_text(json.dumps({'issues':issues,'expected':expected},ensure_ascii=False),encoding='utf-8')
    assert issues == expected


@pytest.fixture(scope='module')
def early_checks(tmp_path_factory):
    """One PS process; each independently named control gets isolated input files."""
    root = tmp_path_factory.mktemp('fixed-preflight')
    diagnostics = {member: source for member, source in DIRECT.items() if member != 'scripts/package_data_fingerprints.json'}
    original = json.loads((ROOT/'scripts/release_zip_contract.json').read_text('utf-8'))
    cases = []
    for member in diagnostics:
        for mutation in ('good', 'wrong_byte', 'missing', 'old_pin'):
            key = str(len(cases))
            repo = root/key
            (repo/'scripts').mkdir(parents=True)
            contract = copy.deepcopy(original)
            for current, source in diagnostics.items():
                raw = (ROOT/source).read_bytes()
                if current == member and mutation == 'missing':
                    continue
                if current == member and mutation == 'wrong_byte':
                    raw = bytes([raw[0]^1])+raw[1:]
                (repo/source).write_bytes(raw)
            if mutation == 'old_pin':
                contract['fixed_member_sha256'][member] = OLD_PIN
            contract_path = repo/'contract.json'
            contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding='utf-8')
            cases.append({'key': key, 'member': member, 'mutation': mutation,
                          'repo': str(repo), 'contract': str(contract_path)})
    cases_path = root/'cases.json'
    cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
    script = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$e=$null;$t=$null;$tree=[Management.Automation.Language.Parser]::ParseFile('__BUILD__',[ref]$t,[ref]$e)
if($e.Count){throw 'builder parse error'}
foreach($function in @('Initialize-TrustedFileHashCommand','Get-VerifiedReleaseDiagnosticInputs')) {
 $nodes=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $function},$true))
 if($nodes.Count -ne 1){throw "builder function not unique: $function"}
 . ([scriptblock]::Create($nodes[0].Extent.Text))
}
$calls=@($tree.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$ReleaseDiagnosticInputs'},$true))
if($calls.Count -ne 1){throw 'builder preflight call not unique'}
$TrustedFileHashCommand=Initialize-TrustedFileHashCommand -PowerShellHome $PSHOME
$results=@(foreach($case in (Get-Content -LiteralPath '__CASES__' -Raw -Encoding utf8|ConvertFrom-Json)) {
 $Repo=$case.repo
 $ReleaseZipContract=Get-Content -LiteralPath $case.contract -Raw -Encoding utf8|ConvertFrom-Json
 $postCheckReached=$false
 try {
  . ([scriptblock]::Create($calls[0].Extent.Text))
  $postCheckReached=$true
  @{key=$case.key;post_check_reached=$postCheckReached;inputs=$ReleaseDiagnosticInputs;error=$null}
 } catch {
  @{key=$case.key;post_check_reached=$postCheckReached;inputs=$null;error=$_.Exception.Message}
 }
})
ConvertTo-Json -InputObject $results -Depth 5 -Compress
'''.replace('__BUILD__', str(ROOT/'scripts/build_live_portable.ps1').replace("'", "''")).replace('__CASES__', str(cases_path).replace("'", "''"))
    (root/'probe.ps1').write_text(script, encoding='utf-8')
    started = time.monotonic()
    completed = subprocess.run(
        ['C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe', '-NoProfile', '-NonInteractive',
         '-WindowStyle', 'Hidden', '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode()],
        capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    (root/'stdout.txt').write_bytes(completed.stdout)
    (root/'stderr.txt').write_bytes(completed.stderr)
    (root/'RUN.json').write_text(json.dumps({'exit_code': completed.returncode, 'seconds': time.monotonic()-started,
                                           'hard_timeout': 30, 'full_builder': False}), encoding='utf-8')
    assert completed.returncode == 0, completed.stderr.decode('utf-8', 'replace')
    rows = {row['key']: row for row in json.loads(completed.stdout)}
    assert len(rows) == len(cases)
    return {(case['member'], case['mutation']): (case, rows[case['key']]) for case in cases}


@pytest.mark.parametrize('member', [member for member in DIRECT if member != 'scripts/package_data_fingerprints.json'])
@pytest.mark.parametrize('mutation', ['good', 'wrong_byte', 'missing', 'old_pin'])
def test_actual_builder_preflight_rejects_fixed_copy_regressions(early_checks, member, mutation):
    case, result = early_checks[member, mutation]
    if mutation == 'good':
        assert result['error'] is None
        assert result['post_check_reached'] is True
        assert {name: Path(path) for name, path in result['inputs'].items()} == {
            name: Path(case['repo'])/source for name, source in DIRECT.items()
            if name != 'scripts/package_data_fingerprints.json'
        }
    else:
        assert result['post_check_reached'] is False
        reason = 'missing' if mutation == 'missing' else 'hash_mismatch'
        assert result['error'] == f'release_fixed_copy_{reason}: {member}'


def test_fixed_copy_gate_is_before_preflight_returns_and_stage_creation():
    source = (ROOT/'scripts/build_live_portable.ps1').read_text('utf-8-sig')
    call = '$ReleaseDiagnosticInputs = Get-VerifiedReleaseDiagnosticInputs -RepositoryRoot $Repo -Contract $ReleaseZipContract'
    assert source.count(call) == 1
    gate = source.index(call)
    inspect_return = source.index('if ($InspectOnly) {')
    preflight_return = source.index('if ($PreflightOnly) {')
    build_phase = source.index('# --- 1. 清理 + 目录 ---')
    assert gate < inspect_return < preflight_return < build_phase
