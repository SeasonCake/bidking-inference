from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT = "2f43ca998cb3e1a1ef232f4b3e3caa0fc2152926"
MEMBER = "scripts/package_data_fingerprints.json"


def _git_text(relative: str) -> str:
    return subprocess.run(
        ["git", "show", f"{PARENT}:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _contract_issues(
    build: str,
    release: dict[str, object],
    local: dict[str, object],
    fingerprint: bytes,
) -> set[str]:
    issues: set[str] = set()
    required = release["required_members"]["common"]
    fixed = release["fixed_member_sha256"]
    local_fixed = {
        row["path"]: row["role"] for row in local["fixed_members"]
    }
    digest = hashlib.sha256(fingerprint).hexdigest()
    if MEMBER not in required:
        issues.add("release_required_missing")
    if fixed.get(MEMBER) != digest:
        issues.add("release_fixed_identity_missing")
    if local_fixed.get(MEMBER) != "entry":
        issues.add("local_integrity_fixed_member_missing")
    if (
        '$packageDataFingerprintDst = Join-Path $scriptsDst "package_data_fingerprints.json"'
        not in build
        or "Copy-Item -LiteralPath $packageDataFingerprintPath -Destination "
        "$packageDataFingerprintDst -Force"
        not in build
        or "$StagedPackageDataFingerprintSHA256 -cne "
        "$PackageDataFingerprintSHA256"
        not in build
    ):
        issues.add("stage_exact_copy_missing")
    return issues


def test_exact_parent_is_known_red_and_source_contract_is_green() -> None:
    fingerprint = (ROOT / MEMBER).read_bytes()
    parent_issues = _contract_issues(
        _git_text("scripts/build_live_portable.ps1"),
        json.loads(_git_text("scripts/release_zip_contract.json")),
        json.loads(_git_text("scripts/local_integrity_contract.json")),
        fingerprint,
    )
    assert parent_issues == {
        "release_required_missing",
        "release_fixed_identity_missing",
        "local_integrity_fixed_member_missing",
        "stage_exact_copy_missing",
    }

    source_issues = _contract_issues(
        (ROOT / "scripts/build_live_portable.ps1").read_text(encoding="utf-8-sig"),
        json.loads(
            (ROOT / "scripts/release_zip_contract.json").read_text(encoding="utf-8")
        ),
        json.loads(
            (ROOT / "scripts/local_integrity_contract.json").read_text(
                encoding="utf-8"
            )
        ),
        fingerprint,
    )
    assert source_issues == set()


def test_build_script_parses_on_both_supported_powershell_hosts() -> None:
    hosts = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "PowerShell/7/pwsh.exe",
    )
    build = ROOT / "scripts/build_live_portable.ps1"
    for host in hosts:
        assert host.is_file()
        completed = subprocess.run(
            [
                str(host),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[void][scriptblock]::Create([IO.File]::ReadAllText("
                "$env:BIDKING_TEST_BUILD_SCRIPT)); "
                "[Console]::Out.Write('AST_OK')",
            ],
            env={**os.environ, "BIDKING_TEST_BUILD_SCRIPT": str(build)},
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == "AST_OK"
