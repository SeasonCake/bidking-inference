"""Build verifier output must work on non-Chinese Windows without changing checks."""
import os
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_all_seven_auditor_processes_keep_isolation_and_explicit_utf8():
    text = (ROOT / 'scripts/build_live_portable.ps1').read_text(encoding='utf-8-sig')
    calls = [line.strip() for line in text.splitlines()
             if '& $BuildPython' in line and any(name in line for name in ('$zipAuditor','@zipAuditArgs','@componentZipAuditArgs'))]
    assert len(calls) == 7
    assert all('$BuildPython -X utf8 ' in line for line in calls)
    assert sum('-I -B $zipAuditor' in line for line in calls) == 4
    assert sum('-E -B $zipAuditor' in line for line in calls) == 1
    assert sum('-E -B @zipAuditArgs' in line for line in calls) == 1
    assert sum('-E @componentZipAuditArgs' in line for line in calls) == 1
    for mode in ('--audit-private-build-artifact','--write-runtime-receipt',
                 '--write-canonical-zip','--write-bkenc-receipt','--build-receipt','--trusted-asset'):
        assert mode in text


def _load_auditor():
    path = ROOT / 'scripts/audit_release_zip.py'
    spec = importlib.util.spec_from_file_location('utf8_family_real_auditor', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _actual_auditor_cli(tmp_path, mode, *, utf8, bad=False):
    audit = _load_auditor()
    runtime = tmp_path / 'synthetic_input'
    runtime.mkdir()
    (runtime / 'Lib').mkdir()
    (runtime / 'Lib' / 'synthetic.txt').write_text('toy',encoding='ascii')
    output = tmp_path / ('runtime.receipt.json' if mode=='runtime' else 'synthetic.zip')
    if mode=='runtime':
        entries = audit._runtime_inventory_entries_from_filesystem(runtime)
        approval = {'schema':audit.RUNTIME_INVENTORY_APPROVAL_SCHEMA,'approval_version':1,
                    **audit._runtime_inventory_identity(entries)}
        if bad:
            approval['file_count'] += 1
        approval_path = tmp_path / 'synthetic.approval.json'
        approval_path.write_text(json.dumps(approval),encoding='utf-8')
        arguments = ['--write-runtime-receipt',str(runtime),str(output),str(approval_path)]
    else:
        arguments = ['--write-canonical-zip',str(runtime),str(output)]
        if bad:
            # The existing writer/audit mode conflict must remain rejected.
            arguments.append(str(tmp_path / 'unapproved.zip'))
    # Same real CLI: only make the pre-fix non-UTF8 stdout deterministic.  -I
    # ignores hostile env; no monkeypatch of auditor verification or messages.
    entry = "import runpy,sys;\nif not sys.flags.utf8_mode: sys.stdout.reconfigure(encoding='cp1252',errors='strict')\n" \
            "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    command = [sys.executable, *(['-X','utf8'] if utf8 else ['-X','utf8=0']),'-I','-B','-c',entry,
               str(ROOT / 'scripts/audit_release_zip.py'),*arguments]
    environment = {**os.environ,'PYTHONUTF8':'0','PYTHONIOENCODING':'ascii'}
    result = subprocess.run(command,capture_output=True,env=environment,timeout=10)
    return result, output


def test_actual_runtime_receipt_output_old_cp1252_red_then_explicit_utf8_green(tmp_path):
    old = tmp_path / 'old'; old.mkdir()
    result, output = _actual_auditor_cli(old,'runtime',utf8=False)
    assert result.returncode != 0 and b'UnicodeEncodeError' in result.stderr
    assert output.is_file(), 'Original failure was after the real receipt write'
    new = tmp_path / 'new'; new.mkdir()
    result, output = _actual_auditor_cli(new,'runtime',utf8=True)
    assert result.returncode == 0, result.stderr
    assert '[OK] runtime/python canonical receipt 已冻结' in result.stdout.decode('utf-8')
    assert json.loads(output.read_text('utf-8'))['schema']=='bidking_runtime_inventory_receipt_v1'


def test_actual_runtime_receipt_bad_approval_still_refused_with_utf8(tmp_path):
    result, output = _actual_auditor_cli(tmp_path,'runtime',utf8=True,bad=True)
    assert result.returncode == 1 and not output.exists()
    assert '[FAIL] runtime receipt 生成失败: ValueError' in result.stdout.decode('utf-8')
    assert '[OK]' not in result.stdout.decode('utf-8')


def test_actual_canonical_zip_output_old_cp1252_red_then_utf8_green(tmp_path):
    old = tmp_path / 'old'; old.mkdir()
    result, output = _actual_auditor_cli(old,'zip',utf8=False)
    assert result.returncode != 0 and b'UnicodeEncodeError' in result.stderr
    assert output.is_file()
    new = tmp_path / 'new'; new.mkdir()
    result, output = _actual_auditor_cli(new,'zip',utf8=True)
    assert result.returncode == 0 and output.is_file(), result.stderr
    assert '[OK] canonical POSIX release ZIP 已生成' in result.stdout.decode('utf-8')


def test_actual_canonical_zip_conflicting_mode_still_refused_with_utf8(tmp_path):
    result, output = _actual_auditor_cli(tmp_path,'zip',utf8=True,bad=True)
    assert result.returncode == 2 and not output.exists()
    assert '[FAIL] canonical ZIP 写入模式不能同时传待审计 ZIP' in result.stdout.decode('utf-8')


def test_builder_explicit_utf8_keeps_key_check_and_six_hour_limit():
    text = (ROOT / 'scripts/build_live_portable.ps1').read_text(encoding='utf-8-sig')
    assert '$BuildPython -X utf8 -E -B $keyVerifier --key-file $MasterKeyFile --max-cache-age-hours 6' in text
    assert 'if ($verifyExit -ne 0)' in text
    assert '$ReleaseKeyCacheVerified = $true' in text


def test_utf8_output_survives_ignored_and_hostile_encoding_environment():
    env = {**os.environ, 'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'ascii'}
    result = subprocess.run([sys.executable, '-X', 'utf8', '-E', '-B', '-c',
        "import sys; assert sys.flags.utf8_mode == 1; print('\\u6821\\u9a8c\\u901a\\u8fc7')"],
        capture_output=True, env=env, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode('utf-8').strip() == '校验通过'


def test_real_verifier_bad_key_still_rejected_with_utf8(tmp_path):
    key = tmp_path / 'synthetic_invalid_key.txt'
    key.write_text('not-a-key', encoding='ascii')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-E', '-B',
        str(ROOT / 'scripts/verify_release_master_key.py'), '--key-file', str(key),
        '--max-cache-age-hours', '6'], capture_output=True, timeout=10)
    assert result.returncode == 2
    assert '[verify_release_master_key] FAIL:' in result.stderr.decode('utf-8')
    assert '[verify_release_master_key] OK:' not in result.stdout.decode('utf-8')
