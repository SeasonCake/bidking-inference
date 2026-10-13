from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_live_portable.ps1"
RELEASE_CONTRACT = ROOT / "scripts" / "release_zip_contract.json"
PACKAGE_MANIFEST = ROOT / "scripts" / "package_data_manifest.json"
HELPER_END = "# BUILD_STAGE_MUTABLE_COPY_HELPER_END"
VALIDATOR_END = "# BUILD_STAGE_MUTABLE_CONTRACT_VALIDATOR_END"
PLAINTEXT_MEMBER = "data/processed/heroes.json"
STATIC_MEMBER = (
    "external_references/AuctionAnalyzer4.13.3/_decompiled/"
    "MapBidCalculator/MapBidCalculator/Models/StaticData.cs"
)


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


def _helper_source() -> str:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    start = source.index("function Copy-BuildStageMutableLeaf")
    end = source.index(HELPER_END, start)
    return source[start:end]


def _validator_source() -> str:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    start = source.index("function Get-ValidatedMutableStageMoatMembers")
    end = source.index(VALIDATOR_END, start)
    return source[start:end]


def _run_helper_probe(
    tmp_path: Path,
    body: str,
    *,
    helper_source: str | None = None,
) -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "stage_mutable_copy_probe.ps1"
    probe.write_text(
        '$ErrorActionPreference = "Stop"\n'
        + (helper_source or _helper_source())
        + "\n"
        + body
        + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
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


def _inject_helper(marker: str, snippet: str) -> str:
    source = _helper_source()
    assert source.count(marker) == 1
    return source.replace(marker, marker + "\n" + snippet, 1)


def _run_contract_probe(
    tmp_path: Path,
    contract: object,
) -> subprocess.CompletedProcess[str]:
    contract_path = tmp_path / "release_contract.json"
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False),
        encoding="utf-8",
    )
    probe = tmp_path / "stage_mutable_contract_probe.ps1"
    probe.write_text(
        '$ErrorActionPreference = "Stop"\n'
        + _validator_source()
        + "\n"
        + f"$contract = Get-Content -LiteralPath {_ps_quote(contract_path)} "
        "-Raw -Encoding utf8 | ConvertFrom-Json\n"
        + f"$manifest = Get-Content -LiteralPath {_ps_quote(PACKAGE_MANIFEST)} "
        "-Raw -Encoding utf8 | ConvertFrom-Json\n"
        + "try {\n"
        + "    $members = @(Get-ValidatedMutableStageMoatMembers "
        "-ReleaseContract $contract -PackageDataFiles @($manifest.files) "
        f"-StaticDataMember {_ps_quote(STATIC_MEMBER)} "
        f"-PlaintextPackageMember {_ps_quote(PLAINTEXT_MEMBER)})\n"
        + "    [Console]::WriteLine(\"COUNT=$($members.Count)\")\n"
        + "    [Console]::WriteLine(\"MEMBERS=$(ConvertTo-Json "
        "-InputObject @($members) -Compress)\")\n"
        + "    exit 0\n"
        + "} catch {\n"
        + "    [Console]::Error.WriteLine($_.Exception.Message)\n"
        + "    exit 17\n"
        + "}\n",
        encoding="utf-8",
    )
    return subprocess.run(
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


def _tampered_contract(case: str) -> object:
    contract = json.loads(RELEASE_CONTRACT.read_text(encoding="utf-8"))
    protection = contract["member_protection"]
    drop = "data/raw/tables/Drop.txt"
    if case == "protection-null":
        contract["member_protection"] = None
    elif case == "rule-null":
        protection[drop] = None
    elif case == "required-string-true":
        protection[drop]["required"] = "true"
    elif case == "required-int-one":
        protection[drop]["required"] = 1
    elif case == "required-int-zero":
        protection[drop]["required"] = 0
    elif case == "required-null":
        protection[drop]["required"] = None
    elif case == "required-array":
        protection[drop]["required"] = [True]
    elif case == "required-object":
        protection[drop]["required"] = {"value": True}
    elif case == "required-false":
        protection[drop]["required"] = False
    elif case == "missing-required":
        del protection[drop]["required"]
    elif case == "missing-mode":
        del protection[drop]["mode"]
    elif case == "extra-rule-field":
        protection[drop]["extra"] = False
    elif case == "wrong-mode":
        protection[drop]["mode"] = "BKENC2-GZIP"
    elif case == "wrong-mode-type":
        protection[drop]["mode"] = 2
    elif case == "missing-member":
        del protection[drop]
    elif case == "heroes-substitution":
        protection[PLAINTEXT_MEMBER] = {
            "mode": "bkenc2-gzip",
            "required": True,
        }
    elif case == "same-count-replacement":
        protection[PLAINTEXT_MEMBER] = {
            "mode": "bkenc2-gzip",
            "required": True,
        }
        protection[drop] = {"mode": "plaintext", "required": True}
    elif case == "same-count-unknown":
        protection[drop] = {"mode": "plaintext", "required": True}
        protection["data/processed/not-in-package.json"] = {
            "mode": "bkenc2-gzip",
            "required": True,
        }
    elif case == "static-heroes-replacement":
        protection[STATIC_MEMBER] = {"mode": "plaintext", "required": True}
        protection[PLAINTEXT_MEMBER] = {
            "mode": "bkenc2-gzip",
            "required": True,
        }
    else:  # pragma: no cover - test table is the authority
        raise AssertionError(case)
    return contract


def test_stage_mutable_copy_does_not_overwrite_preexisting_destination(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    stage = tmp_path / "stage"
    destination_parent = stage / "data"
    source_root.mkdir()
    destination_parent.mkdir(parents=True)
    source = source_root / "payload.bin"
    payload = b"immutable-source"
    source.write_bytes(payload)
    expected_hash = hashlib.sha256(payload).hexdigest()
    destination = destination_parent / "payload.bin"
    destination.write_bytes(b"preexisting")
    body = "\n".join(
        (
            "try {",
            "    Copy-BuildStageMutableLeaf "
            f"-StageRoot {_ps_quote(stage)} "
            f"-SourcePath {_ps_quote(source)} "
            f"-DestinationPath {_ps_quote(destination)} "
            f"-ExpectedSHA256 '{expected_hash}' -ExpectedBytes {len(payload)}",
            "    [Console]::Error.WriteLine('unexpected_success')",
            "    exit 0",
            "} catch {",
            "    [Console]::Error.WriteLine($_.Exception.Message)",
            "    exit 17",
            "}",
        )
    )

    result = _run_helper_probe(tmp_path, body)

    assert result.returncode == 17
    assert "stage_mutable_copy_destination_invalid" in result.stderr
    assert source.read_bytes() == payload
    assert destination.read_bytes() == b"preexisting"


def test_stage_mutable_copy_rejects_wrong_preopen_expected_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    stage = tmp_path / "stage"
    destination = stage / "data" / "source.bin"
    stage.joinpath("data").mkdir(parents=True)
    payload = b"wrong-before-open-must-not-copy"
    source.write_bytes(payload)
    expected_hash = hashlib.sha256(b"different-object").hexdigest()
    expected_bytes = len(payload)
    body = "\n".join(
        (
            "try {",
            "    Copy-BuildStageMutableLeaf "
            f"-StageRoot {_ps_quote(stage)} "
            f"-SourcePath {_ps_quote(source)} "
            f"-DestinationPath {_ps_quote(destination)} "
            f"-ExpectedSHA256 '{expected_hash}' -ExpectedBytes {expected_bytes}",
            "    [Console]::Error.WriteLine('unexpected_success')",
            "    exit 0",
            "} catch {",
            "    [Console]::Error.WriteLine($_.Exception.Message)",
            "    exit 17",
            "}",
        )
    )

    result = _run_helper_probe(tmp_path, body)

    assert result.returncode == 17
    assert "stage_mutable_copy_source_identity_mismatch" in result.stderr
    assert source.read_bytes() == payload
    assert not destination.exists()


def test_stage_mutable_copy_source_handle_blocks_same_attribute_replacement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    replacement = tmp_path / "replacement.bin"
    backup = tmp_path / "source-replace-backup.bin"
    stage = tmp_path / "stage"
    destination = stage / "data" / "source.bin"
    stage.joinpath("data").mkdir(parents=True)
    payload = b"source-handle-bound-object"
    replacement_payload = b"R" * len(payload)
    source.write_bytes(payload)
    replacement.write_bytes(replacement_payload)
    expected_hash = hashlib.sha256(payload).hexdigest()
    operation_code = (
        f"[IO.File]::Replace({_ps_quote(replacement)}, $sourceFullPath, "
        f"{_ps_quote(backup)})"
    )
    snippet = "\n".join(
        (
            "$operationBlocked = $false",
            "try {",
            f"    {operation_code}",
            "    throw [InvalidOperationException]::new('operation_not_blocked')",
            "} catch [System.IO.IOException] {",
            "    $operationBlocked = $true",
            "} catch [System.UnauthorizedAccessException] {",
            "    $operationBlocked = $true",
            "}",
            "if (-not $operationBlocked) {",
            "    throw [InvalidOperationException]::new('operation_not_blocked')",
            "}",
        )
    )
    body = (
        "Copy-BuildStageMutableLeaf "
        f"-StageRoot {_ps_quote(stage)} -SourcePath {_ps_quote(source)} "
        f"-DestinationPath {_ps_quote(destination)} "
        f"-ExpectedSHA256 '{expected_hash}' -ExpectedBytes {len(payload)}"
    )

    result = _run_helper_probe(
        tmp_path,
        body,
        helper_source=_inject_helper(
            "# STAGE_MUTABLE_SOURCE_HANDLE_BOUND",
            snippet,
        ),
    )

    assert result.returncode == 0, result.stderr
    assert source.read_bytes() == payload
    assert destination.read_bytes() == payload
    assert not backup.exists()
    assert replacement.read_bytes() == replacement_payload


def test_stage_mutable_copy_destination_handle_blocks_same_attribute_replacement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    replacement = tmp_path / "replacement.bin"
    backup = tmp_path / "destination-replace-backup.bin"
    stage = tmp_path / "stage"
    destination = stage / "data" / "source.bin"
    stage.joinpath("data").mkdir(parents=True)
    payload = b"destination-handle-bound-object"
    replacement_payload = b"R" * len(payload)
    source.write_bytes(payload)
    replacement.write_bytes(replacement_payload)
    expected_hash = hashlib.sha256(payload).hexdigest()
    operation_code = (
        f"[IO.File]::Replace({_ps_quote(replacement)}, "
        f"$destinationFullPath, {_ps_quote(backup)})"
    )
    snippet = "\n".join(
        (
            "$operationBlocked = $false",
            "try {",
            f"    {operation_code}",
            "    throw [InvalidOperationException]::new('operation_not_blocked')",
            "} catch [System.IO.IOException] {",
            "    $operationBlocked = $true",
            "} catch [System.UnauthorizedAccessException] {",
            "    $operationBlocked = $true",
            "}",
            "if (-not $operationBlocked) {",
            "    throw [InvalidOperationException]::new('operation_not_blocked')",
            "}",
        )
    )
    body = (
        "Copy-BuildStageMutableLeaf "
        f"-StageRoot {_ps_quote(stage)} -SourcePath {_ps_quote(source)} "
        f"-DestinationPath {_ps_quote(destination)} "
        f"-ExpectedSHA256 '{expected_hash}' -ExpectedBytes {len(payload)}"
    )

    result = _run_helper_probe(
        tmp_path,
        body,
        helper_source=_inject_helper(
            "# STAGE_MUTABLE_DESTINATION_HANDLE_BOUND",
            snippet,
        ),
    )

    assert result.returncode == 0, result.stderr
    assert source.read_bytes() == payload
    assert destination.read_bytes() == payload
    assert not backup.exists()
    assert replacement.read_bytes() == replacement_payload


@pytest.mark.skipif(os.name != "nt", reason="Windows ReadOnly package-input contract")
def test_stage_mutable_copy_handles_three_real_readonly_package_inputs(
    tmp_path: Path,
) -> None:
    fingerprints = json.loads(RELEASE_CONTRACT.with_name(
        "package_data_fingerprints.json"
    ).read_text(encoding="utf-8"))["files"]
    members = (
        "data/raw/tables/Drop.txt",
        "data/raw/tables/Hero.txt",
        "data/processed/quality_combo_presolve_q456.json",
    )
    missing = [member for member in members if not (ROOT / member).is_file()]
    if missing:
        pytest.skip("requires hydrated exact package inputs")
    stage = tmp_path / "stage"
    calls: list[str] = []
    source_attributes: dict[str, int] = {}
    for member in members:
        source = ROOT / member
        destination = stage / member
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_attributes[member] = source.stat().st_file_attributes
        identity = fingerprints[member]
        calls.append(
            "Copy-BuildStageMutableLeaf "
            f"-StageRoot {_ps_quote(stage)} -SourcePath {_ps_quote(source)} "
            f"-DestinationPath {_ps_quote(destination)} "
            f"-ExpectedSHA256 '{identity['sha256']}' "
            f"-ExpectedBytes {identity['size']}"
        )

    result = _run_helper_probe(tmp_path, "\n".join(calls))

    assert result.returncode == 0, result.stderr
    for member in members:
        source = ROOT / member
        destination = stage / member
        identity = fingerprints[member]
        assert source.stat().st_file_attributes == source_attributes[member]
        assert source_attributes[member] & stat.FILE_ATTRIBUTE_READONLY
        assert destination.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY == 0
        assert destination.stat().st_size == identity["size"]
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == identity["sha256"]
        with destination.open("r+b"):
            pass


def test_build_routes_exact_contract_bkenc2_members_through_mutable_copy() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    contract = json.loads(RELEASE_CONTRACT.read_text(encoding="utf-8"))
    manifest = json.loads(PACKAGE_MANIFEST.read_text(encoding="utf-8"))
    bkenc2_members = {
        member
        for member, rule in contract["member_protection"].items()
        if rule == {"mode": "bkenc2-gzip", "required": True}
    }
    package_members = set(manifest["files"])

    assert len(bkenc2_members) == 21
    assert bkenc2_members == (package_members - {PLAINTEXT_MEMBER}) | {
        STATIC_MEMBER
    }
    validation_at = build.index("$MutableStageMoatMembers = @(")
    stage_mutation_at = build.index("# --- 1. 清理 + 目录 ---")
    key_check_at = build.index('$keyVerifier = Join-Path $Repo "scripts\\verify_release_master_key.py"')
    assert validation_at < key_check_at < stage_mutation_at
    assert "Get-ValidatedMutableStageMoatMembers" in build[:stage_mutation_at]
    assert '$staticRel = $StaticDataSourceRelativePath' in build
    assert '.Value.required -eq $true' not in build
    assert "$MutableStageMoatMembers -ccontains $rel" in build
    assert "$MutableStageMoatMembers -ccontains $StaticDataMoatMember" in build
    assert build.count("Copy-BuildStageMutableLeaf `") == 2
    assert "[System.IO.File]::Copy" not in _helper_source()
    assert "[System.IO.FileMode]::Open" in _helper_source()
    assert "[System.IO.FileMode]::CreateNew" in _helper_source()
    assert "[System.IO.FileShare]::Read" in _helper_source()
    assert "[System.IO.FileShare]::None" in _helper_source()
    assert "$sourceHasher.ComputeHash($sourceStream)" in _helper_source()
    assert "$destinationHasher.ComputeHash($destinationStream)" in _helper_source()
    assert "$PackageDataFingerprintDocument.files.PSObject.Properties" in build
    assert "-ExpectedSHA256 ([string]$expectedIdentity.SHA256)" in build
    assert "-ExpectedBytes ([int64]$expectedIdentity.Bytes)" in build
    assert "-ExpectedSHA256 $ExpectedStaticDataSourceSHA256" in build
    assert "Copy-Item -LiteralPath $src -Destination $dst -Force" in build
    assert "scripts/encrypt_moat_files.py" in build


def test_mutable_stage_contract_accepts_exact_current_twenty_one_before_stage(
    tmp_path: Path,
) -> None:
    contract = json.loads(RELEASE_CONTRACT.read_text(encoding="utf-8"))
    expected = sorted(
        member
        for member, rule in contract["member_protection"].items()
        if rule == {"mode": "bkenc2-gzip", "required": True}
    )
    stage = tmp_path / "stage-must-remain-absent"

    result = _run_contract_probe(tmp_path, contract)

    assert result.returncode == 0, result.stderr
    assert "COUNT=21" in result.stdout
    members_line = next(
        line for line in result.stdout.splitlines() if line.startswith("MEMBERS=")
    )
    assert json.loads(members_line.split("=", 1)[1]) == expected
    assert not stage.exists()


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (
        ("protection-null", "release_member_protection_invalid"),
        ("rule-null", "release_member_protection_invalid"),
        ("required-string-true", "release_member_protection_invalid"),
        ("required-int-one", "release_member_protection_invalid"),
        ("required-int-zero", "release_member_protection_invalid"),
        ("required-null", "release_member_protection_invalid"),
        ("required-array", "release_member_protection_invalid"),
        ("required-object", "release_member_protection_invalid"),
        ("missing-required", "release_member_protection_invalid"),
        ("missing-mode", "release_member_protection_invalid"),
        ("extra-rule-field", "release_member_protection_invalid"),
        ("wrong-mode", "release_member_protection_invalid"),
        ("wrong-mode-type", "release_member_protection_invalid"),
        ("required-false", "mutable_stage_moat_contract_invalid"),
        ("missing-member", "mutable_stage_moat_contract_invalid"),
        ("heroes-substitution", "mutable_stage_moat_contract_invalid"),
        ("same-count-replacement", "mutable_stage_moat_contract_invalid"),
        ("same-count-unknown", "mutable_stage_moat_contract_invalid"),
        ("static-heroes-replacement", "mutable_stage_moat_contract_invalid"),
    ),
)
def test_mutable_stage_contract_tamper_fails_before_stage_mutation(
    tmp_path: Path,
    case: str,
    expected_error: str,
) -> None:
    stage = tmp_path / "stage-must-remain-absent"

    result = _run_contract_probe(tmp_path, _tampered_contract(case))

    assert result.returncode == 17
    assert expected_error in result.stderr
    assert not stage.exists()
