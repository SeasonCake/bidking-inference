from __future__ import annotations

from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_live_portable.ps1"
BOOTSTRAP_END = "function ConvertTo-NativeDiagnosticSmokeProcessArgument"


def _powershell_exe() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(
        system_root,
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    if candidate.is_file():
        return str(candidate)
    discovered = shutil.which("powershell.exe")
    if discovered:
        return discovered
    pytest.skip("Windows PowerShell is unavailable")


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _bootstrap_source() -> str:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    start = source.index("function Initialize-TrustedFileHashCommand")
    end = source.index(BOOTSTRAP_END, start)
    return source[start:end]


def _native_artifact_lookup_source() -> str:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    start = source.index("function Get-ClientNativeModuleArtifact")
    end = source.index("$ProductProfile =", start)
    return source[start:end]


def _run_probe(
    tmp_path: Path,
    body: str,
    *,
    poisoned_module_path: bool = False,
    prelude: str = "",
    executable: str | None = None,
) -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "hash_trust_probe.ps1"
    probe.write_text(
        '$ErrorActionPreference = "Stop"\n'
        + prelude
        + ("\n" if prelude else "")
        + _bootstrap_source()
        + "\n"
        + body
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    if poisoned_module_path:
        poisoned = tmp_path / "poisoned-module-path"
        poisoned.mkdir(exist_ok=True)
        env["PSModulePath"] = str(poisoned)
    return subprocess.run(
        [
            executable or _powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _trusted_hash_body(payload: Path) -> str:
    return "\n".join(
        (
            "$trusted = Initialize-TrustedFileHashCommand "
            "-PowerShellHome $PSHOME",
            f"$result = & $trusted -LiteralPath {_ps_quote(payload)} "
            "-Algorithm SHA256",
            '[Console]::WriteLine("HASH=$($result.Hash.ToLowerInvariant())")',
        )
    )


def _write_fake_utility_module(
    fake_home: Path,
    *,
    root_module: str = "Microsoft.PowerShell.Utility.psm1",
    invalid_manifest: bool = False,
) -> None:
    module_dir = (
        fake_home / "Modules" / "Microsoft.PowerShell.Utility"
    )
    module_dir.mkdir(parents=True)
    manifest = module_dir / "Microsoft.PowerShell.Utility.psd1"
    if invalid_manifest:
        manifest.write_text("@{ invalid manifest", encoding="utf-8")
        return
    manifest.write_text(
        "\n".join(
            (
                "@{",
                f"    RootModule = '{root_module}'",
                "    ModuleVersion = '1.0.0'",
                "    FunctionsToExport = @('Get-FileHash')",
                "    CmdletsToExport = @()",
                "    AliasesToExport = @()",
                "}",
            )
        ),
        encoding="utf-8",
    )
    (module_dir / root_module).write_text(
        "\n".join(
            (
                "function Get-FileHash {",
                "    [pscustomobject]@{ Hash = '00' }",
                "}",
                "Export-ModuleMember -Function Get-FileHash",
            )
        ),
        encoding="utf-8",
    )


def test_build_hash_bootstrap_precedes_all_derived_paths_and_hashes() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    initialize_at = source.index(
        "$TrustedFileHashCommand = Initialize-TrustedFileHashCommand"
    )
    dist_at = source.index('$Dist = Join-Path $Repo "dist"')
    trusted_hash_targets = re.findall(
        r"&\s+\$TrustedFileHashCommand\s+"
        r"-LiteralPath\s+(\$[A-Za-z][A-Za-z0-9]*)\s+"
        r"-Algorithm\s+SHA256",
        source,
    )
    expected_hash_targets = Counter(
        {
            "$PSCommandPath": 1,
            "$RuntimePythonSourceManifestPath": 1,
            "$BuildPython": 1,
            "$ActivationServiceEndpointsPath": 1,
            "$ActivationHttpE2EEPath": 1,
            "$ClientProtectionManifestPath": 1,
            "$NativeBinaryContractPath": 1,
            "$ReleaseZipContractPath": 1,
            "$StaticDataSourcePath": 1,
            "$packageDataManifestPath": 1,
            "$packageDataFingerprintPath": 1,
            "$sourcePath": 1,
            "$encOut": 2,
            "$embedZip": 1,
            "$embeddedPython": 1,
            "$activationEndpointsDst": 1,
            "$activationHttpE2EEDst": 1,
            "$payloadPath": 1,
            "$staticSrc": 1,
            "$runtimeComponentDescriptor": 1,
            "$RuntimeComponentArchive": 1,
            "$NativeBinaryReceiptPath": 1,
            "$RuntimeInventoryReceiptPath": 1,
            "$stagedLauncher": 2,
            "$FullPath": 1,
            "$mitmproxyManifestPath": 2,
            "$mitmproxyManifestDst": 1,
            "$extractedMitmproxyManifest": 1,
            "$packageDataFingerprintDst": 1,
            "$boundedMotwHelper": 3,
            "$addonBootstrapPath": 1,
            "$Zip": 1,
            "$BuildAuditReceiptPath": 1,
        }
    )

    assert trusted_hash_targets
    assert initialize_at < source.index("& $TrustedFileHashCommand") < dist_at
    assert Counter(trusted_hash_targets) == expected_hash_targets
    trusted_command_name = '"Microsoft.PowerShell.Utility\\Get-FileHash"'
    assert source.count(trusted_command_name) == 1
    assert "Get-FileHash" not in source.replace(
        trusted_command_name,
        "",
    )
    assert "$ClientProtectionManifestSHA256 =" in source
    assert "$ReleaseZipContractSHA256 =" in source
    assert "$PackageDataManifestSHA256 =" in source
    assert "$PackageDataFingerprintSHA256 =" in source
    assert "$GeneratedUploadConfigSHA256 =" in source
    assert "$FinalUploadConfigSHA256 =" in source
    assert "$RuntimeInventoryReceiptSHA256 =" in source
    assert "$ExpectedStaticDataSourceSHA256" in source
    assert "$NativeBinaryContractSHA256" in source
    assert "$NativeBinaryDistribution" in source
    assert "$NativeSigningPolicyFile" not in source
    assert "$SignToolPath" not in source
    assert "$NativeBinaryReceiptSHA256" in source
    assert "trusted_file_hash_module_missing" in source
    assert "trusted_file_hash_import_failed" in source
    assert "trusted_file_hash_command_untrusted" in source


def test_native_artifact_lookup_does_not_prefix_match_sibling_modules() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "function Get-ClientNativeModuleArtifact" in source
    assert "$ModuleName.$_.pyd" in source
    assert "$expectedNames -ccontains $_.Name" in source
    assert "native_module_artifact_untrusted" in source
    assert source.count("Get-ClientNativeModuleArtifact -Directory") == 4
    assert source.count("-AllowedAbiTags @($ClientProtection.native_abi_tags)") == 4


def test_native_artifact_lookup_selects_exact_module_not_prefix_sibling(
    tmp_path: Path,
) -> None:
    (tmp_path / "ocr.cp313-win_amd64.pyd").write_bytes(b"exact")
    (tmp_path / "ocr_helper.cp313-win_amd64.pyd").write_bytes(b"sibling")
    probe = tmp_path / "native_lookup_probe.ps1"
    probe.write_text(
        '$ErrorActionPreference = "Stop"\n'
        + _native_artifact_lookup_source()
        + "\n"
        + "$match = Get-ClientNativeModuleArtifact "
        + f"-Directory {_ps_quote(tmp_path)} -ModuleName 'ocr' "
        + "-AllowedAbiTags @('cp313-win_amd64')\n"
        + '[Console]::WriteLine("MATCH=$($match.Name)")\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            _powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "MATCH=ocr.cp313-win_amd64.pyd" in result.stdout
    assert "ocr_helper" not in result.stdout


def test_native_artifact_lookup_rejects_near_match_abi_name(
    tmp_path: Path,
) -> None:
    (tmp_path / "ocr.cp313-win_amd64.pyd").write_bytes(b"exact")
    (tmp_path / "ocr.fake.pyd").write_bytes(b"near")
    probe = tmp_path / "native_lookup_reject_probe.ps1"
    probe.write_text(
        '$ErrorActionPreference = "Stop"\n'
        + _native_artifact_lookup_source()
        + "\n"
        + "Get-ClientNativeModuleArtifact "
        + f"-Directory {_ps_quote(tmp_path)} -ModuleName 'ocr' "
        + "-AllowedAbiTags @('cp313-win_amd64')\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            _powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "native_module_artifact_untrusted" in result.stderr


def test_trusted_hash_bootstrap_uses_system_module_with_no_profile(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"bidking-build-hash-trust")
    expected = hashlib.sha256(payload.read_bytes()).hexdigest()

    result = _run_probe(tmp_path, _trusted_hash_body(payload))

    assert result.returncode == 0, result.stderr
    assert f"HASH={expected}" in result.stdout


def test_trusted_hash_bootstrap_accepts_powershell7_system_cmdlet(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is unavailable")
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"bidking-powershell7-hash-trust")
    expected = hashlib.sha256(payload.read_bytes()).hexdigest()
    body = "\n".join(
        (
            "$trusted = Initialize-TrustedFileHashCommand "
            "-PowerShellHome $PSHOME",
            '[Console]::WriteLine("TYPE=$($trusted.CommandType)")',
            f"$result = & $trusted -LiteralPath {_ps_quote(payload)} "
            "-Algorithm SHA256",
            '[Console]::WriteLine("HASH=$($result.Hash.ToLowerInvariant())")',
        )
    )

    result = _run_probe(
        tmp_path,
        body,
        poisoned_module_path=True,
        executable=pwsh,
    )

    assert result.returncode == 0, result.stderr
    assert "TYPE=Cmdlet" in result.stdout
    assert f"HASH={expected}" in result.stdout


def test_trusted_hash_bootstrap_survives_unavailable_auto_discovery(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"bidking-polluted-module-path")
    expected = hashlib.sha256(payload.read_bytes()).hexdigest()
    management_prelude = (
        "$management = [IO.Path]::Combine("
        "$PSHOME, 'Modules', 'Microsoft.PowerShell.Management', "
        "'Microsoft.PowerShell.Management.psd1')\n"
        "Microsoft.PowerShell.Core\\Import-Module "
        "-Name $management -Force -Scope Local -ErrorAction Stop\n"
        "$PSModuleAutoLoadingPreference = 'None'"
    )

    plain_probe = tmp_path / "plain_get_file_hash.ps1"
    plain_probe.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + management_prelude
        + "\n"
        + f"Get-FileHash -LiteralPath {_ps_quote(payload)} -Algorithm SHA256\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    poisoned = tmp_path / "poisoned-module-path"
    poisoned.mkdir()
    env["PSModulePath"] = str(poisoned)
    plain = subprocess.run(
        [
            _powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(plain_probe),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    trusted = _run_probe(
        tmp_path,
        _trusted_hash_body(payload),
        poisoned_module_path=True,
        prelude=management_prelude,
    )

    assert plain.returncode != 0
    assert "Get-FileHash" in plain.stderr
    assert trusted.returncode == 0, trusted.stderr
    assert f"HASH={expected}" in trusted.stdout


@pytest.mark.parametrize(
    ("fixture_kind", "expected_error"),
    (
        ("missing", "trusted_file_hash_module_missing"),
        ("invalid_manifest", "trusted_file_hash_import_failed"),
        ("untrusted_root", "trusted_file_hash_command_untrusted"),
    ),
)
def test_trusted_hash_bootstrap_fails_closed_before_side_effects(
    tmp_path: Path,
    fixture_kind: str,
    expected_error: str,
) -> None:
    fake_home = tmp_path / "fake-pshome"
    if fixture_kind == "invalid_manifest":
        _write_fake_utility_module(fake_home, invalid_manifest=True)
    elif fixture_kind == "untrusted_root":
        _write_fake_utility_module(fake_home, root_module="evil.psm1")
    marker = tmp_path / "derived-stage-side-effect.txt"
    body = "\n".join(
        (
            "$null = Initialize-TrustedFileHashCommand "
            f"-PowerShellHome {_ps_quote(fake_home)}",
            f"[IO.File]::WriteAllText({_ps_quote(marker)}, 'unexpected')",
        )
    )

    result = _run_probe(tmp_path, body, poisoned_module_path=True)

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("spoof_kind", ("alias", "function", "external_script"))
def test_trusted_hash_bootstrap_ignores_unqualified_spoof(
    tmp_path: Path,
    spoof_kind: str,
) -> None:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"bidking-unqualified-spoof")
    expected = hashlib.sha256(payload.read_bytes()).hexdigest()
    if spoof_kind == "alias":
        spoof = "Set-Alias -Name Get-FileHash -Value Write-Output -Force"
    elif spoof_kind == "function":
        spoof = "function global:Get-FileHash { 'PRIVATE_SPOOF' }"
    else:
        external = tmp_path / "Get-FileHash.ps1"
        external.write_text("'PRIVATE_SPOOF'\n", encoding="utf-8")
        spoof = (
            f"$env:PATH = {_ps_quote(tmp_path)} + ';' + $env:PATH"
        )

    result = _run_probe(
        tmp_path,
        spoof + "\n" + _trusted_hash_body(payload),
    )

    assert result.returncode == 0, result.stderr
    assert f"HASH={expected}" in result.stdout
    assert "PRIVATE_SPOOF" not in result.stdout


def test_build_script_powershell_ast_remains_valid() -> None:
    command = (
        "$tokens=$null;$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"{_ps_quote(BUILD_SCRIPT)},[ref]$tokens,[ref]$errors)|Out-Null;"
        'Write-Output "AST_ERRORS=$($errors.Count)";'
        "if($errors.Count){exit 2}"
    )
    result = subprocess.run(
        [_powershell_exe(), "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "AST_ERRORS=0" in result.stdout


def test_standard_portable_build_smokes_the_actual_generation_helper() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    manifest_at = source.index("Write-CandidateBuildManifest\nWrite-Host")
    smoke_at = source.index("portable package generation helper 实包预检通过")
    encrypted_at = source.index("if ($Encrypt) {", manifest_at)
    assert manifest_at < smoke_at < encrypted_at
    portable_python_at = source.index("$portableGenerationPython = Join-Path $RuntimePy \"python.exe\"")
    verify_python_at = source.index("$verifyPy = Join-Path $RuntimePy \"python.exe\"")
    assert manifest_at < portable_python_at < smoke_at < verify_python_at
    for token in (
        "& $portableGenerationPython -E -B $portableGenerationHelper",
        "--package-root $Stage",
        "--monitor $portableGenerationMonitor",
        "--runtime $portableGenerationRuntime",
        "--identity-helper $portableGenerationHelper",
        "BidKingLive.exe",
        "run_wss_live_monitor.py",
        "mitm_bidking_wss_addon.py",
        "_local_compat_core.py",
        "BIDKING_PACKAGE_GENERATION_IDENTITY=[0-9a-f]{64}",
    ):
        assert token in source


def test_hash_bootstrap_has_no_environment_or_policy_mutation() -> None:
    source = _bootstrap_source()
    forbidden = (
        "$env:PSModulePath",
        "SetEnvironmentVariable",
        "Set-ItemProperty",
        "New-ItemProperty",
        "Set-ExecutionPolicy",
        "Install-Module",
    )

    assert not re.search(r"\bSet-Alias\b", source, flags=re.IGNORECASE)
    for token in forbidden:
        assert token not in source
