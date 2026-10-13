"""Actual PowerShell smoke entry: cwd, pytest arguments and exit-code contract."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOSTS = (
    Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe',
    Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'PowerShell/7/pwsh.exe',
)
SCENARIOS = ('repository_cwd', 'foreign_cwd', 'pytest_failure', 'empty_selection', 'explicit_slow', 'missing_path')


def _ps_literal(value: str | Path) -> str:
    return "'"+str(value).replace("'", "''")+"'"


@pytest.fixture(scope='module', params=HOSTS, ids=('winps51', 'ps7'))
def smoke_cases(request, tmp_path_factory):
    host = request.param
    if not host.is_file():
        pytest.skip(f'PowerShell host unavailable: {host.name}')
    root = tmp_path_factory.mktemp('smoke-entry')
    foreign = root/'foreign cwd'
    foreign.mkdir()
    probe = root/'test_probe.py'
    probe.write_text(
        'from pathlib import Path\nimport pytest\n'
        f'def test_working_directory(): assert Path.cwd() == Path({str(ROOT)!r})\n'
        'def test_pass(): assert True\n'
        'def test_intentional_failure(): assert False\n'
        '@pytest.mark.slow\ndef test_slow(): assert True\n', encoding='utf-8',
    )
    env = os.environ.copy()
    env.update(BIDKING_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONIOENCODING='utf-8')
    env.pop('PYTHONHOME', None)
    rows = {}
    for scenario in SCENARIOS:
        evidence = root/scenario
        evidence.mkdir()
        cwd = ROOT if scenario == 'repository_cwd' else foreign
        arguments = [str(probe), '-p', 'no:cacheprovider', '--basetemp', str(evidence/'tmp'),
                     '--junitxml', str(evidence/'junit.xml')]
        if scenario in ('repository_cwd', 'foreign_cwd'):
            arguments += ['-k', 'test_working_directory or test_pass']
        elif scenario == 'pytest_failure':
            arguments += ['-k', 'test_intentional_failure']
        elif scenario == 'empty_selection':
            arguments += ['-k', 'never_selected_control']
        elif scenario == 'explicit_slow':
            arguments += ['--collect-only', '-m', 'slow']
        else:
            arguments[0] = str(root/'missing_test.py')
        # Named array is the established script API; raw -p can bind -PytestArgs
        # as a PowerShell parameter abbreviation instead of reaching pytest.
        script = (
            '$ErrorActionPreference="Stop"; '
            f'& {_ps_literal(ROOT/"scripts/test_smoke.ps1")} -PytestArgs @('
            + ','.join(_ps_literal(value) for value in arguments) + '); '
            '$testExit=$LASTEXITCODE; '
            'Write-Output ("SMOKE_CALLER_CWD="+(Get-Location).Path); '
            'exit $testExit'
        )
        command = [str(host), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-EncodedCommand',
                   base64.b64encode(script.encode('utf-16le')).decode()]
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=30,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        (evidence/'stdout.txt').write_bytes(completed.stdout)
        (evidence/'stderr.txt').write_bytes(completed.stderr)
        (evidence/'RUN.json').write_text(json.dumps({'command': command, 'cwd': str(cwd),
                                                   'exit_code': completed.returncode, 'timeout_seconds': 30}), encoding='utf-8')
        rows[scenario] = (cwd, evidence, completed)
    return rows


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_smoke_entry_preserves_location_selection_and_exit(smoke_cases, scenario):
    cwd, evidence, completed = smoke_cases[scenario]
    stdout = completed.stdout.decode('utf-8', 'replace')
    assert f'SMOKE_CALLER_CWD={cwd}' in stdout
    expected_exit = {'pytest_failure': 1, 'empty_selection': 5, 'missing_path': 4}.get(scenario, 0)
    assert completed.returncode == expected_exit, stdout+completed.stderr.decode('utf-8', 'replace')
    if scenario in ('repository_cwd', 'foreign_cwd'):
        cases = list(ET.parse(evidence/'junit.xml').iter('testcase'))
        assert {case.get('name') for case in cases} == {'test_working_directory', 'test_pass'}
        assert all(len(case) == 0 for case in cases)
        assert '2 passed, 2 deselected' in stdout
    elif scenario == 'pytest_failure':
        cases = list(ET.parse(evidence/'junit.xml').iter('testcase'))
        assert len(cases) == 1 and cases[0].find('failure') is not None
    elif scenario == 'explicit_slow':
        assert 'test_probe.py::test_slow' in stdout
        assert '1/4 tests collected (3 deselected)' in stdout
    elif scenario == 'empty_selection':
        assert '4 deselected' in stdout
    else:
        assert b'file or directory not found' in completed.stderr
