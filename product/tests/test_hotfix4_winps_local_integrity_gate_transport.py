from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
TRANSPORT_PARENT = "761d10641dd5c7629e96a7ffa2aca01942ee7529"
MARKER_PARENT = "20077f3b69a009a46838f1de0c8e531a67a2a704"
NEWLINE_PARENT = "9ea4774b5ba84fb6c9747a3d07ce4b997e62bf56"
PYTHON = Path(r"C:\Python31315\python.exe")
WINPS = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
PWSH = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
HOSTS = (WINPS, PWSH)


def _transport_block(source: str) -> str:
    function_start = source.index("function Invoke-LocalIntegrityGate")
    function_end = source.index("# 含激活链", function_start)
    return source[function_start:function_end]


def _marker_pattern_block(source: str) -> str:
    pattern_start = source.index("$integrityGateMarkerPattern = (")
    pattern_end = source.index("    if ($integrityGateExit", pattern_start)
    return source[pattern_start:pattern_end]


def _git_text(revision: str, relative: str) -> str:
    return subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_host(host: Path, script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(host),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _run_marker_probe(
    *,
    host: Path,
    source: str,
    output: str,
    gate_exit: int,
    tmp_path: Path,
    case: str,
) -> subprocess.CompletedProcess[str]:
    block = _transport_block(source)
    synthetic_gate = (
        "import sys\n"
        "sys.argv.pop(1)\n"
        f"sys.stdout.write({output!r})\n"
        f"raise SystemExit({gate_exit})\n"
    )
    probe = tmp_path / f"marker-{case}-{host.stem}.ps1"
    probe.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$localIntegrityGateCode = @'\n"
        f"{synthetic_gate}"
        "'@\n"
        f"{block}"
        "try {\n"
        "  Invoke-LocalIntegrityGate `\n"
        "    -Label 'synthetic-marker' `\n"
        f"    -PythonPath {_powershell_literal(str(PYTHON))} `\n"
        "    -PackageRoot 'synthetic package root'\n"
        "  Write-Output 'MARKER_ACCEPTED'\n"
        "} catch {\n"
        "  Write-Output 'MARKER_REJECTED'\n"
        "  exit 91\n"
        "}\n",
        encoding="utf-8",
    )
    return _run_host(host, probe)


def _run_direct_grammar_probe(
    *,
    host: Path,
    source: str,
    marker: str,
    tmp_path: Path,
    case: str,
) -> subprocess.CompletedProcess[str]:
    encoded_marker = base64.b64encode(marker.encode("utf-8")).decode("ascii")
    probe = tmp_path / f"grammar-{case}-{host.stem}.ps1"
    probe.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"{_marker_pattern_block(source)}"
        "$marker = [Text.Encoding]::UTF8.GetString(\n"
        f"  [Convert]::FromBase64String('{encoded_marker}'))\n"
        "$matched = [System.Text.RegularExpressions.Regex]::IsMatch(\n"
        "  $marker,\n"
        "  $integrityGateMarkerPattern,\n"
        "  [System.Text.RegularExpressions.RegexOptions]::CultureInvariant)\n"
        "if ($matched) { Write-Output 'GRAMMAR_MATCH' } "
        "else { Write-Output 'GRAMMAR_REJECT' }\n",
        encoding="utf-8",
    )
    return _run_host(host, probe)


def _caller_issues(source: str) -> set[str]:
    issues: set[str] = set()
    if "-c $localIntegrityGateCode" in source.replace("`\n", ""):
        issues.add("raw_python_c_caller")
    if source.count("Invoke-LocalIntegrityGate `") != 2:
        issues.add("safe_caller_count")
    if source.count("$localIntegrityGateEntry $localIntegrityGatePayload $PackageRoot") != 1:
        issues.add("single_encoded_native_entry")
    return issues


def test_exact_blocked_source_is_red_and_successor_callers_are_green() -> None:
    parent = _git_text(TRANSPORT_PARENT, "scripts/build_live_portable.ps1")
    assert _caller_issues(parent) == {
        "raw_python_c_caller",
        "safe_caller_count",
        "single_encoded_native_entry",
    }
    assert _caller_issues(BUILD.read_text(encoding="utf-8-sig")) == set()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows host boundary")
@pytest.mark.parametrize("host", HOSTS, ids=("winps5", "pwsh7"))
def test_both_integrity_callers_use_one_encoded_transport(
    host: Path,
    tmp_path: Path,
) -> None:
    assert host.is_file()
    assert PYTHON.is_file()
    source = BUILD.read_text(encoding="utf-8-sig")
    block = _transport_block(source)
    synthetic_gate = """\
import sys
stage = sys.argv.pop(1)
if stage not in {"synthetic stage with spaces", "synthetic extracted with spaces"}:
    raise SystemExit(9)
print("BIDKING_LOCAL_INTEGRITY=ok:1:a1b2c3d4e5f60708")
"""
    probe = tmp_path / f"gate-{host.stem}.ps1"
    probe.write_text(
        "$ErrorActionPreference = \"Stop\"\n"
        "$localIntegrityGateCode = @'\n"
        f"{synthetic_gate}"
        "'@\n"
        f"{block}"
        "Invoke-LocalIntegrityGate `\n"
        "  -Label 'synthetic-stage' `\n"
        f"  -PythonPath {_powershell_literal(str(PYTHON))} `\n"
        "  -PackageRoot 'synthetic stage with spaces'\n"
        "Invoke-LocalIntegrityGate `\n"
        "  -Label 'synthetic-extracted' `\n"
        f"  -PythonPath {_powershell_literal(str(PYTHON))} `\n"
        "  -PackageRoot 'synthetic extracted with spaces'\n"
        'Write-Output "LOCAL_INTEGRITY_BOTH_CALLERS_OK"\n',
        encoding="utf-8",
    )
    result = _run_host(host, probe)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BIDKING_LOCAL_INTEGRITY=ok:1:a1b2c3d4e5f60708" not in result.stderr
    assert "LOCAL_INTEGRITY_BOTH_CALLERS_OK" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows host boundary")
@pytest.mark.parametrize("host", HOSTS, ids=("winps5", "pwsh7"))
def test_exact_marker_parent_accepts_prefixed_substring_known_red(
    host: Path,
    tmp_path: Path,
) -> None:
    result = _run_marker_probe(
        host=host,
        source=_git_text(MARKER_PARENT, "scripts/build_live_portable.ps1"),
        output=(
            "prefix:BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53:suffix\n"
        ),
        gate_exit=0,
        tmp_path=tmp_path,
        case="exact-parent-substring",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MARKER_ACCEPTED" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows host boundary")
@pytest.mark.parametrize("host", HOSTS, ids=("winps5", "pwsh7"))
@pytest.mark.parametrize("newline", ("\n", "\r"), ids=("isolated-lf", "isolated-cr"))
def test_exact_newline_parent_accepts_split_eol_known_red(
    host: Path,
    newline: str,
    tmp_path: Path,
) -> None:
    result = _run_direct_grammar_probe(
        host=host,
        source=_git_text(NEWLINE_PARENT, "scripts/build_live_portable.ps1"),
        marker="BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53" + newline,
        tmp_path=tmp_path,
        case="exact-parent-split-eol",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GRAMMAR_MATCH" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows host boundary")
@pytest.mark.parametrize("host", HOSTS, ids=("winps5", "pwsh7"))
@pytest.mark.parametrize(
    ("case", "ending", "accepted"),
    (
        ("no_eol", "", True),
        ("one_crlf", "\r\n", True),
        ("isolated_lf", "\n", False),
        ("isolated_cr", "\r", False),
        ("double_crlf", "\r\n\r\n", False),
        ("double_lf", "\n\n", False),
        ("double_cr", "\r\r", False),
        ("crlf_then_lf", "\r\n\n", False),
        ("lf_then_cr", "\n\r", False),
        ("cr_then_crlf", "\r\r\n", False),
        ("unicode_nel", "\u0085", False),
        ("unicode_line_separator", "\u2028", False),
        ("unicode_paragraph_separator", "\u2029", False),
    ),
)
def test_marker_newline_grammar_accepts_only_none_or_one_crlf(
    host: Path,
    case: str,
    ending: str,
    accepted: bool,
    tmp_path: Path,
) -> None:
    result = _run_direct_grammar_probe(
        host=host,
        source=BUILD.read_text(encoding="utf-8-sig"),
        marker="BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53" + ending,
        tmp_path=tmp_path,
        case=case,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if accepted:
        assert "GRAMMAR_MATCH" in result.stdout
        assert "GRAMMAR_REJECT" not in result.stdout
    else:
        assert "GRAMMAR_REJECT" in result.stdout
        assert "GRAMMAR_MATCH" not in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows host boundary")
@pytest.mark.parametrize("host", HOSTS, ids=("winps5", "pwsh7"))
@pytest.mark.parametrize(
    ("case", "output", "gate_exit", "accepted"),
    (
        (
            "canonical",
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\n",
            0,
            True,
        ),
        (
            "prefix_substring",
            "prefix:BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\n",
            0,
            False,
        ),
        (
            "suffix_substring",
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53:suffix\n",
            0,
            False,
        ),
        ("bare_marker", "BIDKING_LOCAL_INTEGRITY=ok:\n", 0, False),
        (
            "duplicate_marker",
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\n"
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\n",
            0,
            False,
        ),
        (
            "conflicting_marker",
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\n"
            "BIDKING_LOCAL_INTEGRITY=ok:2657:1111111111111111\n",
            0,
            False,
        ),
        (
            "leading_garbage",
            "garbage\nBIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\n",
            0,
            False,
        ),
        (
            "trailing_garbage",
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\ngarbage\n",
            0,
            False,
        ),
        (
            "multiline_disguise",
            "\nBIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\n\n",
            0,
            False,
        ),
        (
            "wrong_status",
            "BIDKING_LOCAL_INTEGRITY=failed:2656:248d168409456a53\n",
            0,
            False,
        ),
        (
            "zero_count",
            "BIDKING_LOCAL_INTEGRITY=ok:0:248d168409456a53\n",
            0,
            False,
        ),
        (
            "leading_zero_count",
            "BIDKING_LOCAL_INTEGRITY=ok:02656:248d168409456a53\n",
            0,
            False,
        ),
        (
            "uppercase_hex",
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248D168409456A53\n",
            0,
            False,
        ),
        (
            "short_hex",
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248d1684\n",
            0,
            False,
        ),
        (
            "canonical_nonzero",
            "BIDKING_LOCAL_INTEGRITY=ok:2656:248d168409456a53\n",
            7,
            False,
        ),
        ("missing", "", 0, False),
    ),
)
def test_integrity_function_requires_one_complete_canonical_marker(
    host: Path,
    case: str,
    output: str,
    gate_exit: int,
    accepted: bool,
    tmp_path: Path,
) -> None:
    result = _run_marker_probe(
        host=host,
        source=BUILD.read_text(encoding="utf-8-sig"),
        output=output,
        gate_exit=gate_exit,
        tmp_path=tmp_path,
        case=case,
    )
    combined = result.stdout + result.stderr
    if accepted:
        assert result.returncode == 0, combined
        assert "MARKER_ACCEPTED" in result.stdout
        assert "MARKER_REJECTED" not in result.stdout
    else:
        assert result.returncode == 91, combined
        assert "MARKER_REJECTED" in result.stdout
        assert "MARKER_ACCEPTED" not in result.stdout


def test_builder_locks_two_callers_marker_and_exit_semantics() -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    block = _transport_block(source)
    compact = " ".join(block.replace("`", "").split())
    assert "[System.Text.Encoding]::UTF8.GetBytes(" in block
    assert "[Convert]::ToBase64String($localIntegrityGateBytes)" in block
    assert "[System.Security.Cryptography.SHA256]::Create()" in block
    assert "len(sys.argv)==3" in block
    assert "sys.argv[0]=='-c'" in block
    assert "base64.b64decode(sys.argv[1],validate=True)" in block
    assert "hashlib.sha256(_raw).hexdigest()" in block
    assert "compile(_raw," in block
    assert "-c $localIntegrityGateEntry $localIntegrityGatePayload $PackageRoot" in compact
    assert "$integrityGateExit -ne 0 -or" in block
    assert "\\ABIDKING_LOCAL_INTEGRITY=ok:" in block
    assert "[1-9][0-9]*:[0-9a-f]{16}(?:\\r\\n)?\\z" in block
    assert "[0-9a-f]{16}\\r?\\n?\\z" not in block
    assert "[System.Text.RegularExpressions.Regex]::IsMatch(" in block
    assert "[System.Text.RegularExpressions.RegexOptions]::CultureInvariant" in block
    assert "-Label \"最终Stage签名后\"" in source
    assert "-PythonPath $verifyPy" in source
    assert "-PackageRoot $Stage" in source
    assert "-Label \"解压成品\"" in source
    assert "-PythonPath $extractedVerifyPy" in source
    assert "-PackageRoot $zipSmokeFull" in source
    assert "$localIntegrityGateCode $Stage" not in compact
    assert "$localIntegrityGateCode $zipSmokeFull" not in compact


@pytest.mark.skipif(sys.platform != "win32", reason="Windows host boundary")
@pytest.mark.parametrize("host", HOSTS, ids=("winps5", "pwsh7"))
@pytest.mark.parametrize(
    ("case", "arguments"),
    (
        ("extra", "$localIntegrityGatePayload 'stage' 'extra'"),
        ("missing_payload", "'stage'"),
        ("missing_stage", "$localIntegrityGatePayload"),
        ("misordered", "'stage' $localIntegrityGatePayload"),
        ("malformed_payload", "'***not-base64***' 'stage'"),
        ("empty_payload", "'' 'stage'"),
        ("empty_stage", "$localIntegrityGatePayload ''"),
        ("wrong_payload", "$wrongPayload 'stage'"),
    ),
)
def test_local_integrity_gate_rejects_nonexact_envelope_before_real_gate(
    host: Path,
    case: str,
    arguments: str,
    tmp_path: Path,
) -> None:
    assert host.is_file()
    assert PYTHON.is_file()
    source = BUILD.read_text(encoding="utf-8-sig")
    block = _transport_block(source)
    synthetic_gate = """\
import sys
sys.argv.pop(1)
print("REAL_GATE_EXECUTED")
print("BIDKING_LOCAL_INTEGRITY=ok:1:synthetic")
"""
    wrong_payload = base64.b64encode(b'print("WRONG_PAYLOAD_EXECUTED")').decode(
        "ascii"
    )
    probe = tmp_path / f"invalid-{case}-{host.stem}.ps1"
    probe.write_text(
        "$ErrorActionPreference = \"Continue\"\n"
        "$localIntegrityGateCode = @'\n"
        f"{synthetic_gate}"
        "'@\n"
        f"{block}"
        f"$wrongPayload = {_powershell_literal(wrong_payload)}\n"
        f"& {_powershell_literal(str(PYTHON))} -E -s -S -B -c "
        f"$localIntegrityGateEntry {arguments}\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = _run_host(host, probe)
    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "REAL_GATE_EXECUTED" not in combined
    assert "WRONG_PAYLOAD_EXECUTED" not in combined
    assert "BIDKING_LOCAL_INTEGRITY=ok:" not in combined


@pytest.mark.skipif(sys.platform != "win32", reason="Windows host boundary")
@pytest.mark.parametrize("host", HOSTS, ids=("winps5", "pwsh7"))
def test_builder_ast_locks_native_and_safe_caller_counts(
    host: Path,
    tmp_path: Path,
) -> None:
    assert host.is_file()
    probe = tmp_path / f"ast-{host.stem}.ps1"
    probe.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$tokens = $null\n"
        "$errors = $null\n"
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile(\n"
        f"  {_powershell_literal(str(BUILD))}, [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count -ne 0) { throw ($errors | Out-String) }\n"
        "$commands = @($ast.FindAll({\n"
        "  param($node)\n"
        "  $node -is [System.Management.Automation.Language.CommandAst]\n"
        "}, $true))\n"
        "$safe = @($commands | Where-Object {\n"
        "  $_.GetCommandName() -ceq 'Invoke-LocalIntegrityGate'\n"
        "})\n"
        "$entry = @($commands | Where-Object {\n"
        "  $_.Extent.Text -clike '*$localIntegrityGateEntry*'\n"
        "})\n"
        "$raw = @($commands | Where-Object {\n"
        "  $_.Extent.Text -clike '*$localIntegrityGateCode*'\n"
        "})\n"
        "if ($safe.Count -ne 2 -or $entry.Count -ne 1 -or $raw.Count -ne 0) {\n"
        "  throw \"caller_contract:$($safe.Count):$($entry.Count):$($raw.Count)\"\n"
        "}\n"
        "Write-Output 'AST_CALLER_CONTRACT_OK'\n",
        encoding="utf-8",
    )
    result = _run_host(host, probe)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AST_CALLER_CONTRACT_OK" in result.stdout
