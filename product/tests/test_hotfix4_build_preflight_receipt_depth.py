from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
PARENT = "ce434b01f53315317bbc6a78a48d8c889aff14dc"
WINDOWS_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
PWSH = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
HOSTS = (WINDOWS_POWERSHELL, PWSH)


def _git_show(spec: str) -> bytes:
    return subprocess.run(
        ["git", "show", spec],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _receipt_body(build_path: Path) -> str:
    captures = ",\n".join(
        "[ordered]@{bytes="
        + str(1000 + index)
        + ";sha256='"
        + str(index) * 64
        + "';verdict='pass'}"
        for index in range(1, 6)
    )
    return f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  {_ps_quote(build_path)}, [ref]$tokens, [ref]$errors
)
if ($errors.Count -ne 0) {{ throw ($errors | ForEach-Object Message | Out-String) }}
$definition = $ast.Find({{
  param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
  $node.Name -eq 'Write-BuildPreflightReceipt'
}}, $true)
if (-not $definition) {{ throw 'preflight receipt function missing' }}
Invoke-Expression $definition.Extent.Text
$Version = '0.3.2-hotfix4'
$SourceCommit = 'a' * 40
$SourceBranch = 'audit-safe'
$Encrypt = $true
$SplitCompatibilityRuntime = $true
$ProductProfile = 'public-safe'
$PackageInputsDirty = $false
$PackageInputStatus = @()
$PackageDataBaselineID = 'baseline-safe'
$PackageDataManifestSHA256 = 'a' * 64
$PackageDataFingerprintSHA256 = 'b' * 64
$ReleaseZipContractSHA256 = 'c' * 64
$RuntimePythonSourceManifestSHA256 = 'd' * 64
$RuntimeInventoryApprovalSHA256 = 'e' * 64
$NativeBinaryContractSHA256 = 'f' * 64
$NativeBinaryDistribution = 'unsigned'
$StaticDataSourceSHA256 = '1' * 64
$ActivationServiceEndpointsSHA256 = '2' * 64
$ActivationServiceEndpointsGeneration = 1
$ActivationHttpE2EEConfigSHA256 = '3' * 64
$MitmproxyInventorySHA256 = '4' * 64
$PetAssetsInventorySHA256 = '5' * 64
$ReleaseCaptureReceipts = @(
{captures}
)
$LocalIntegrityPublicKeyB64 = 'audit-present'
$LocalIntegrityPublicKeySHA256 = '6' * 64
$Zip = 'C:/preflight-safe/candidate.zip'
$Stage = 'C:/preflight-safe/stage'
Write-BuildPreflightReceipt 'preflight_only' $true 'verified'
"""


def _run_receipt(host: Path, build_path: Path) -> tuple[dict[str, object], str]:
    assert host.is_file(), f"required PowerShell host missing: {host}"
    encoded = base64.b64encode(_receipt_body(build_path).encode("utf-16-le")).decode(
        "ascii"
    )
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
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    lines = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip().startswith("BUILD_PREFLIGHT_RECEIPT=")
    ]
    assert len(lines) == 1, completed.stdout + completed.stderr
    return (
        json.loads(lines[0].split("=", 1)[1]),
        completed.stdout + completed.stderr,
    )


def _assert_support_safe_captures(payload: dict[str, object]) -> None:
    availability = payload["release_availability"]
    assert isinstance(availability, dict)
    assert availability["schema"] == "bidking_release_availability_preflight_v1"
    assert availability["verdict"] == "pass"
    captures = availability["captures"]
    assert isinstance(captures, list) and len(captures) == 5
    for index, capture in enumerate(captures, start=1):
        assert isinstance(capture, dict)
        assert set(capture) == {"bytes", "sha256", "verdict"}
        assert capture == {
            "bytes": 1000 + index,
            "sha256": str(index) * 64,
            "verdict": "pass",
        }
        rendered = json.dumps(capture, sort_keys=True).casefold()
        assert "path" not in rendered
        assert "raw" not in rendered


def test_preflight_receipt_uses_explicit_nested_capture_depth() -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    start = source.index("function Write-BuildPreflightReceipt")
    end = source.index("\nif ($InspectOnly)", start)
    function = source[start:end]

    assert function.count("ConvertTo-Json") == 1
    assert "$receipt | ConvertTo-Json -Depth 5 -Compress" in function


@pytest.mark.parametrize("host", HOSTS)
def test_parent_default_depth_serializes_capture_objects_as_strings(
    tmp_path: Path, host: Path
) -> None:
    parent_build = tmp_path / "parent_build_live_portable.ps1"
    parent_build.write_bytes(_git_show(f"{PARENT}:scripts/build_live_portable.ps1"))

    payload, stderr = _run_receipt(host, parent_build)
    captures = payload["release_availability"]["captures"]
    assert isinstance(captures, list) and len(captures) == 5
    assert all(isinstance(capture, str) for capture in captures)
    if host == PWSH:
        assert "depth" in stderr.casefold()


@pytest.mark.parametrize("host", HOSTS)
def test_source_depth_preserves_exact_five_support_safe_capture_objects(
    host: Path,
) -> None:
    payload, stderr = _run_receipt(host, BUILD)

    _assert_support_safe_captures(payload)
    assert "depth" not in stderr.casefold()
