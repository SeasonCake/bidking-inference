from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import importlib.util
import json
import marshal
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_release_zip.py"
_SPEC = importlib.util.spec_from_file_location("audit_release_zip", SCRIPT)
assert _SPEC and _SPEC.loader
_AUDIT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AUDIT)
_MOAT_SCRIPT = ROOT / "scripts" / "encrypt_moat_files.py"
_MOAT_SPEC = importlib.util.spec_from_file_location(
    "encrypt_moat_files", _MOAT_SCRIPT
)
assert _MOAT_SPEC and _MOAT_SPEC.loader
_MOAT = importlib.util.module_from_spec(_MOAT_SPEC)
_MOAT_SPEC.loader.exec_module(_MOAT)

_FIXED_GITIGNORED_INPUT_ENV = "BIDKING_RELEASE_TEST_HYDRATION_ROOT"
_BUILD_SCRIPT = ROOT / "scripts" / "build_live_portable.ps1"
_CURRENT_RANKMAP_RELEASE_MEMBERS = (
    "data/raw/tables/RankMap.txt",
    "data/processed/rankmap_drop_weights_current.json",
    "data/processed/rankmap_drop_priors_current.json",
    "data/processed/rankmap_distribution_authority_current.json",
    "data/processed/auction_hall_6001_shadow_distribution.json",
)

_PORTABLE_PROFILE_ONLY_ISSUES = frozenset(
    {
        "plaintext_wss_module",
        "plaintext_launcher_module",
        "public_launcher_technical_detail",
        "public_diagnostics_technical_detail",
    }
)


def _profile_audit_issues(
    tmp_path: Path,
    *,
    manifest_profile: str,
    manifest_encrypted: str,
    expected_fields: tuple[tuple[str, str], ...],
    include_unsafe_member: bool = False,
) -> list[str]:
    archive = tmp_path / "profile-audit.zip"
    wss_member = next(iter(_AUDIT.FORBIDDEN_PLAINTEXT_WSS_MEMBERS))
    launcher_member = next(iter(_AUDIT.FORBIDDEN_PLAINTEXT_LAUNCHER_MEMBERS))
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "BUILD_MANIFEST.txt",
            f"ProductProfile: {manifest_profile}\r\nEncrypted: {manifest_encrypted}\r\n",
        )
        output.writestr(wss_member, b"portable source")
        output.writestr(launcher_member, b"portable source")
        output.writestr(
            _AUDIT.PUBLIC_LAUNCHER_MEMBER,
            next(iter(_AUDIT.FORBIDDEN_PUBLIC_LAUNCHER_TERMS)),
        )
        output.writestr(
            _AUDIT.PUBLIC_DIAGNOSTICS_MEMBER,
            next(iter(_AUDIT.FORBIDDEN_PUBLIC_DIAGNOSTICS_TERMS)),
        )
        if include_unsafe_member:
            output.writestr("../unsafe.txt", b"x")
    return _AUDIT.audit_zip(
        archive,
        product_profile="standard",
        expected_manifest_fields=expected_fields,
    )


@pytest.mark.parametrize(
    ("manifest_profile", "manifest_encrypted", "expected_fields", "high_protection"),
    (
        ("standard", "False", (("ProductProfile", "standard"), ("Encrypted", "False")), False),
        ("standard", "True", (("ProductProfile", "standard"), ("Encrypted", "True")), True),
        ("network-diagnostic", "False", (("ProductProfile", "standard"), ("Encrypted", "False")), True),
        ("standard", "False", (("ProductProfile", "standard"),), True),
        ("standard", "False", (("ProductProfile", "standard"), ("Encrypted", "True")), True),
    ),
)
def test_profile_aware_plaintext_audit_is_exact_and_fail_closed(
    tmp_path: Path,
    manifest_profile: str,
    manifest_encrypted: str,
    expected_fields: tuple[tuple[str, str], ...],
    high_protection: bool,
) -> None:
    issues = set(
        _profile_audit_issues(
            tmp_path,
            manifest_profile=manifest_profile,
            manifest_encrypted=manifest_encrypted,
            expected_fields=expected_fields,
        )
    )
    if high_protection:
        assert _PORTABLE_PROFILE_ONLY_ISSUES <= issues
    else:
        assert not (_PORTABLE_PROFILE_ONLY_ISSUES & issues)


def test_portable_profile_keeps_archive_path_safety_fail_closed(tmp_path: Path) -> None:
    issues = _profile_audit_issues(
        tmp_path,
        manifest_profile="standard",
        manifest_encrypted="False",
        expected_fields=(("ProductProfile", "standard"), ("Encrypted", "False")),
        include_unsafe_member=True,
    )
    assert "unsafe_member_path" in issues


def _powershell_runtime_hosts() -> tuple[tuple[Path, str, int], ...]:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    hosts = (
        (
            system_root
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe",
            "Desktop",
            5,
        ),
        (program_files / "PowerShell" / "7" / "pwsh.exe", "Core", 7),
    )
    for executable, _edition, _major in hosts:
        assert executable.is_file(), f"required PowerShell host missing: {executable.name}"
    return hosts


def _run_builder_function_probe(
    executable: Path,
    body: str,
    *,
    environment: dict[str, str | None] | None = None,
) -> object:
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            "$content = [IO.File]::ReadAllText($env:BIDKING_TEST_BUILD_SCRIPT)",
            "$start = $content.IndexOf('function "
            "ConvertTo-ControlledWindowsAbsolutePath')",
            "$end = $content.IndexOf('function Invoke-NativeArtifactPrivacyGate')",
            "if ($start -lt 0 -or $end -le $start) { throw 'probe extraction failed' }",
            ". ([scriptblock]::Create($content.Substring($start, $end - $start)))",
            body,
        )
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    child_environment = dict(os.environ)
    child_environment["BIDKING_TEST_BUILD_SCRIPT"] = str(_BUILD_SCRIPT)
    if environment:
        for name, value in environment.items():
            if value is None:
                child_environment.pop(name, None)
            else:
                child_environment[name] = value
    completed = subprocess.run(
        [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        cwd=ROOT,
        env=child_environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, (
        f"PowerShell probe failed ({executable.name}); child output redacted"
    )
    payload_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert payload_lines, f"PowerShell probe returned no payload ({executable.name})"
    return json.loads(payload_lines[-1])


def _run_runtime_gate_spec_probe(executable: Path, body: str) -> object:
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            "$content = [IO.File]::ReadAllText($env:BIDKING_TEST_BUILD_SCRIPT)",
            "$start = $content.IndexOf('function New-RuntimeFixtureGateCode')",
            "$end = $content.IndexOf('$prevEAP2 =', $start)",
            "if ($start -lt 0 -or $end -le $start) { throw 'probe extraction failed' }",
            ". ([scriptblock]::Create($content.Substring($start, $end - $start)))",
            body,
        )
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    environment = dict(os.environ)
    environment["BIDKING_TEST_BUILD_SCRIPT"] = str(_BUILD_SCRIPT)
    completed = subprocess.run(
        [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, (
        f"runtime gate spec probe failed ({executable.name}); child output redacted"
    )
    payload_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert payload_lines, f"runtime gate spec probe returned no payload ({executable.name})"
    return json.loads(payload_lines[-1])


def _run_client_protection_manifest_probe(
    executable: Path,
    repository: Path,
) -> subprocess.CompletedProcess[str]:
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$content = [IO.File]::ReadAllText($env:BIDKING_TEST_BUILD_SCRIPT)",
            "$start = $content.IndexOf('$ClientProtectionManifestPath = Join-Path $Repo')",
            "$end = $content.IndexOf('$WssOperationalContract = $ClientProtection.wss_operational_abi')",
            "if ($start -lt 0 -or $end -le $start) { throw 'probe extraction failed' }",
            "$Repo = $env:BIDKING_TEST_REPOSITORY",
            ". ([scriptblock]::Create($content.Substring($start, $end - $start)))",
            "[Console]::Out.Write(($BuildOnlySourceModules -join '|'))",
        )
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    environment = dict(os.environ)
    environment["BIDKING_TEST_BUILD_SCRIPT"] = str(_BUILD_SCRIPT)
    environment["BIDKING_TEST_REPOSITORY"] = str(repository)
    return subprocess.run(
        [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _copy_only_fixed_input(
    tmp_path: Path,
    relative: str,
    expected_sha256: str,
) -> Path:
    repository_input = ROOT / Path(relative)
    hydration_root = os.environ.get(_FIXED_GITIGNORED_INPUT_ENV)
    source = repository_input
    if not source.is_file() and hydration_root:
        source = Path(hydration_root) / Path(relative)
    if not source.is_file():
        pytest.skip(
            "setup-input missing: exact archive must set "
            f"{_FIXED_GITIGNORED_INPUT_ENV} to a read-only hydration root"
        )
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected_sha256:
        pytest.fail(f"setup-input hash mismatch: {relative}")
    hydrated = tmp_path / "copy-only-hydration" / Path(relative)
    hydrated.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, hydrated)
    if hashlib.sha256(hydrated.read_bytes()).hexdigest() != expected_sha256:
        pytest.fail(f"copy-only hydration hash mismatch: {relative}")
    return hydrated


def _zip(tmp_path: Path, members: dict[str, bytes]) -> Path:
    path = tmp_path / "candidate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 0
            archive.writestr(info, payload)
    return path


def _zip_rows(tmp_path: Path, members: list[tuple[str, bytes]]) -> Path:
    path = tmp_path / "candidate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 0
            archive.writestr(info, payload)
    return path


def _pyc(
    filename: str,
    source: str = "VALUE = 1\n",
    *,
    flags: int = 0,
) -> bytes:
    code = compile(source, filename, "exec")
    header = importlib.util.MAGIC_NUMBER + flags.to_bytes(4, "little") + (b"\0" * 8)
    return header + marshal.dumps(code)


def _asset_manifest(
    tmp_path: Path,
    prefix: str,
    members: dict[str, bytes],
) -> Path:
    normalized_prefix = prefix.rstrip("/") + "/"
    rows = []
    for name, payload in members.items():
        if not name.startswith(normalized_prefix):
            continue
        relative = name[len(normalized_prefix) :]
        rows.append(
            (
                relative.casefold(),
                relative,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
        )
    rows.sort(key=lambda row: row[0])
    serialized = "".join(
        f"{relative}|{size}|{sha256}\n" for _folded, relative, size, sha256 in rows
    ).encode("utf-8")
    manifest = {
        "schema_version": 1,
        "asset_id": "test-pinned-runtime",
        "inventory": {
            "file_count": len(rows),
            "total_bytes": sum(row[2] for row in rows),
            "inventory_sha256": hashlib.sha256(serialized).hexdigest(),
        },
    }
    path = tmp_path / "asset-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _release_contract(
    tmp_path: Path,
    *,
    launcher: bytes = b"launcher",
    python: bytes = b"python",
) -> tuple[Path, dict[str, object]]:
    dependencies = {"demo-dep": "1.0"}
    dependency_rows = b"demo-dep==1.0\n"
    contract: dict[str, object] = {
        "schema": "bidking_release_zip_contract_v1",
        "contract_version": 1,
        "required_members": {
            "common": [
                "BUILD_MANIFEST.txt",
                "BidKingLive.exe",
                "使用说明.txt",
                "LICENSE.txt",
                "公共大数据平台.html",
                "scripts/start_live_npcap_overlay.ps1",
                "scripts/collect_diagnostics.ps1",
                "runtime/python/python.exe",
                "runtime/python/python313.zip",
                "runtime/python/python313._pth",
                "data/processed/upload_config.enc",
            ],
            "encrypted": [],
            "standard": [],
            "network-diagnostic": [],
        },
        "required_manifest_fields": [
            "PackageVersion",
            "SourceCommit",
            "PackageInputsDirty",
            "ReleaseZipContractSHA256",
            "RuntimeDependencyInventorySHA256",
            "LauncherSHA256",
            "StaticDataSourceSHA256",
            "Encrypted",
            "ProductProfile",
            "PythonVersion",
        ],
        "fixed_manifest_values": {
            "PackageInputsDirty": "False",
            "RuntimeDependencyInventorySHA256": hashlib.sha256(
                dependency_rows
            ).hexdigest(),
            "LauncherSHA256": hashlib.sha256(launcher).hexdigest(),
            "StaticDataSourceSHA256": hashlib.sha256(b"static-source").hexdigest(),
            "Encrypted": "False",
            "PythonVersion": "3.13.3",
        },
        "fixed_member_sha256": {
            "BidKingLive.exe": hashlib.sha256(launcher).hexdigest(),
            "runtime/python/python.exe": hashlib.sha256(python).hexdigest(),
        },
        "runtime_dependencies": dependencies,
        "archive_limits": dict(_AUDIT.RELEASE_ARCHIVE_LIMITS),
        "runtime_inventory": {
            "approval_required": False,
            "approval_schema": _AUDIT.RUNTIME_INVENTORY_APPROVAL_SCHEMA,
            "prefix": _AUDIT.RUNTIME_INVENTORY_PREFIX,
            "receipt_required": False,
            "receipt_schema": _AUDIT.RUNTIME_INVENTORY_RECEIPT_SCHEMA,
        },
        "bkenc_authentication": {
            "envelope_aad": _AUDIT.BKENC_AUTHENTICATION_AAD_SCHEME,
            "receipt_required": False,
            "receipt_schema": _AUDIT.BKENC_AUTHENTICATION_RECEIPT_SCHEMA,
        },
        "sensitive_prefixes": [
            "data/processed/",
            "data/raw/tables/",
            "external_references/Model/",
        ],
        "member_protection": {
            "data/processed/upload_config.enc": {
                "mode": "bkenc1",
                "required": True,
            },
            "data/processed/heroes.json": {
                "mode": "plaintext",
                "required": True,
            },
            "data/raw/tables/Item.txt": {
                "mode": _AUDIT.BKENC2_GZIP_FORMAT,
                "required": True,
            },
            "external_references/Model/StaticData.cs": {
                "mode": _AUDIT.BKENC2_GZIP_FORMAT,
                "required": True,
            },
        },
        "public_text_members": [
            "使用说明.txt",
            "LICENSE.txt",
            "公共大数据平台.html",
        ],
        "mutually_exclusive_text_terms": [
            {
                "label": "trial_duration",
                "left": ["试用 3 小时", "试用为【3 小时】", "免费体验 3 小时"],
                "right": [
                    "试用 12 小时",
                    "试用为【12 小时】",
                    "免费体验 12 小时",
                    "公测服务器临时提供/刷新 12 小时",
                ],
            }
        ],
    }
    path = tmp_path / "release-contract.json"
    path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path, contract


def _release_contract_v16_with_launcher_wrappers(
    tmp_path: Path,
) -> tuple[Path, dict[str, object], bytes]:
    path, contract = _release_contract(tmp_path)
    tracked = json.loads(
        (ROOT / "scripts" / "release_zip_contract.json").read_text(encoding="utf-8")
    )
    wrapper = _AUDIT.LAUNCHER_WRAPPER_BYTES
    contract["contract_version"] = 16
    contract["required_members"]["common"].extend(
        _AUDIT.LAUNCHER_WRAPPER_MEMBERS
    )
    contract["launcher_wrappers"] = tracked["launcher_wrappers"]
    contract["release_availability"] = tracked["release_availability"]
    for member in _AUDIT.LAUNCHER_WRAPPER_MEMBERS:
        contract["fixed_member_sha256"][member] = hashlib.sha256(wrapper).hexdigest()
    path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path, contract, wrapper


def _release_members(
    contract_path: Path,
    *,
    launcher: bytes = b"launcher",
    python: bytes = b"python",
) -> dict[str, bytes]:
    contract_hash = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    return {
        "BUILD_MANIFEST.txt": (
            "PackageVersion: v0.3.1\n"
            "SourceCommit: abcdef\n"
            "PackageInputsDirty: False\n"
            f"ReleaseZipContractSHA256: {contract_hash}\n"
            "RuntimeDependencyInventorySHA256: "
            "d15e4e89dcc6e96bd39e1a05c5c8a3cb3dab65d00c7623cd2c635ece8b51a4ee\n"
            f"LauncherSHA256: {hashlib.sha256(launcher).hexdigest()}\n"
            f"StaticDataSourceSHA256: {hashlib.sha256(b'static-source').hexdigest()}\n"
            "Encrypted: False\n"
            "ProductProfile: standard\n"
            "PythonVersion: 3.13.3\n"
        ).encode(),
        "BidKingLive.exe": launcher,
        "使用说明.txt": "免费体验 3 小时".encode(),
        "LICENSE.txt": "专有软件许可".encode(),
        "公共大数据平台.html": "公共大数据".encode(),
        "scripts/start_live_npcap_overlay.ps1": b"Write-Host ok",
        "scripts/collect_diagnostics.ps1": b"Write-Host ok",
        "runtime/python/python.exe": python,
        "runtime/python/python313.zip": b"stdlib",
        "runtime/python/python313._pth": b"python313.zip\nimport site\n",
        "runtime/python/Lib/site-packages/demo_dep-1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: demo-dep\nVersion: 1.0\n"
        ),
        "data/processed/upload_config.enc": b"BKENC1\nupload",
        "data/processed/heroes.json": b"{}",
        "data/raw/tables/Item.txt": b"BKENC2\nitem",
        "external_references/Model/StaticData.cs": b"BKENC2\nstatic",
    }


def _runtime_entries_for_members(
    members: dict[str, bytes],
) -> list[dict[str, object]]:
    runtime_directories = {_AUDIT.RUNTIME_INVENTORY_PREFIX}
    for name in tuple(members):
        if not name.startswith(_AUDIT.RUNTIME_INVENTORY_PREFIX):
            continue
        relative = name[len(_AUDIT.RUNTIME_INVENTORY_PREFIX) :].rstrip("/")
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            runtime_directories.add(
                f"{_AUDIT.RUNTIME_INVENTORY_PREFIX}{parent.as_posix()}/"
            )
            parent = parent.parent
    for directory in runtime_directories:
        members.setdefault(directory, b"")

    entries: list[dict[str, object]] = []
    for name, payload in sorted(members.items()):
        if not name.startswith(_AUDIT.RUNTIME_INVENTORY_PREFIX):
            continue
        is_directory = name.endswith("/")
        entries.append(
            {
                "path": name,
                "type": "directory" if is_directory else "file",
                "size": 0 if is_directory else len(payload),
                "sha256": None if is_directory else hashlib.sha256(payload).hexdigest(),
                "compression_method": zipfile.ZIP_STORED,
            }
        )
    return entries


def _runtime_approval_for_members(
    tmp_path: Path,
    members: dict[str, bytes],
) -> Path:
    entries = _runtime_entries_for_members(members)
    payload = {
        "schema": _AUDIT.RUNTIME_INVENTORY_APPROVAL_SCHEMA,
        "approval_version": 1,
        "prefix": _AUDIT.RUNTIME_INVENTORY_PREFIX,
        "file_count": sum(entry["type"] == "file" for entry in entries),
        "directory_count": sum(entry["type"] == "directory" for entry in entries),
        "total_bytes": sum(int(entry["size"]) for entry in entries),
        "inventory_sha256": _AUDIT._runtime_inventory_sha256(entries),
    }
    path = tmp_path / "runtime-approval.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _runtime_approval_for_filesystem(
    tmp_path: Path,
    runtime_root: Path,
    *,
    name: str = "runtime-filesystem-approval.json",
) -> Path:
    entries = _AUDIT._runtime_inventory_entries_from_filesystem(runtime_root)
    payload = {
        "schema": _AUDIT.RUNTIME_INVENTORY_APPROVAL_SCHEMA,
        "approval_version": 1,
        **{
            key: value
            for key, value in _AUDIT._runtime_inventory_summary(entries).items()
            if key != "entries"
        },
    }
    path = tmp_path / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _runtime_receipt_for_approval(
    tmp_path: Path,
    approval_path: Path,
    members: dict[str, bytes],
) -> Path:
    approval_raw = approval_path.read_bytes()
    approval = json.loads(approval_raw.decode("utf-8"))
    entries = _runtime_entries_for_members(members)
    payload = {
        "schema": _AUDIT.RUNTIME_INVENTORY_RECEIPT_SCHEMA,
        "receipt_version": 1,
        "prefix": approval["prefix"],
        "file_count": approval["file_count"],
        "directory_count": approval["directory_count"],
        "total_bytes": approval["total_bytes"],
        "inventory_sha256": approval["inventory_sha256"],
        "approval_sha256": _AUDIT._runtime_inventory_approval_sha256(approval),
        "entries": entries,
    }
    path = tmp_path / "runtime-receipt.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _release_candidate_with_runtime_receipt(
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, bytes]]:
    contract_path, contract = _release_contract(tmp_path)
    contract["runtime_inventory"]["receipt_required"] = True
    contract["runtime_inventory"]["approval_required"] = True
    contract["runtime_inventory"]["approval_schema"] = (
        _AUDIT.RUNTIME_INVENTORY_APPROVAL_SCHEMA
    )
    members = _release_members(contract_path)
    approval_path = _runtime_approval_for_members(tmp_path, members)
    approval_sha256 = _AUDIT._runtime_inventory_approval_sha256(
        json.loads(approval_path.read_text(encoding="utf-8"))
    )
    contract["fixed_manifest_values"]["RuntimeInventoryApprovalSHA256"] = (
        approval_sha256
    )
    contract["required_manifest_fields"].append("RuntimeInventoryApprovalSHA256")
    contract["required_manifest_fields"].append("RuntimeInventoryReceiptSHA256")
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    members = _release_members(contract_path)
    receipt_path = _runtime_receipt_for_approval(tmp_path, approval_path, members)
    receipt_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    members["BUILD_MANIFEST.txt"] += (
        f"RuntimeInventoryApprovalSHA256: {approval_sha256}\n"
        f"RuntimeInventoryReceiptSHA256: {receipt_sha256}\n"
    ).encode()
    return contract_path, approval_path, receipt_path, members


def _bkenc_release_candidate(
    tmp_path: Path,
) -> tuple[Path, Path, Path, bytes, dict[str, bytes], Path]:
    key = bytes(range(32))
    key_file = tmp_path / "synthetic-bkenc-key.txt"
    key_file.write_text(base64.b64encode(key).decode("ascii"), encoding="ascii")
    contract_path, contract = _release_contract(tmp_path)
    contract["bkenc_authentication"]["receipt_required"] = True
    contract["required_manifest_fields"].append(
        "BkencAuthenticationReceiptSHA256"
    )
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    members = _release_members(contract_path)
    protected = sorted(
        (name, rule["mode"])
        for name, rule in contract["member_protection"].items()
        if rule["mode"] in {"bkenc1", _AUDIT.BKENC2_GZIP_FORMAT}
    )
    for index, (name, mode) in enumerate(protected, start=1):
        nonce = index.to_bytes(12, "big")
        plain = f"synthetic plaintext for {name}".encode()
        if mode == _AUDIT.BKENC2_GZIP_FORMAT:
            magic = _AUDIT.BKENC2_MAGIC
            plain = _AUDIT.BKENC2_LENGTH.pack(len(plain)) + gzip.compress(
                plain,
                mtime=0,
            )
            aad = _AUDIT._bkenc2_member_aad(name)
        else:
            magic = _AUDIT.BKENC1_MAGIC
            aad = _AUDIT._bkenc1_member_aad(name)
        members[name] = (
            magic
            + nonce
            + AESGCM(key).encrypt(
                nonce,
                plain,
                aad,
            )
        )
    candidate = _zip(tmp_path, members)
    receipt = tmp_path / "bkenc-auth-receipt.json"
    _AUDIT.write_bkenc_authentication_receipt(
        candidate,
        receipt,
        contract_path,
        key_file,
    )
    receipt_sha256 = hashlib.sha256(receipt.read_bytes()).hexdigest()
    members["BUILD_MANIFEST.txt"] += (
        f"BkencAuthenticationReceiptSHA256: {receipt_sha256}\n"
    ).encode()
    candidate = _zip(tmp_path, members)
    return contract_path, receipt, key_file, key, members, candidate


def test_known_good_zip_passes(tmp_path: Path) -> None:
    path = _zip(
        tmp_path,
        {
            "hero_ref/tools/panel.py": b"print('ok')\n",
            "src/bidking_lab/live/monitor.cp313-win_amd64.pyd": b"binary",
            "scripts/start_live_npcap_overlay.ps1": b"Write-Host ok",
            "scripts/_local_compat_core.cp313-win_amd64.pyd": b"binary",
            "scripts/_local_compat_monitor.cp313-win_amd64.pyd": b"binary",
            "scripts/local_compat_monitor.py": (
                b"from _local_compat_monitor import main\n"
            ),
            "scripts/local_compat_addon.py": (
                b"from _local_compat_addon_loader import load_addons\n"
            ),
            "scripts/_local_compat_addon_loader.pyd": b"native binary",
        },
    )

    assert _AUDIT.audit_zip(path, forbidden_paths=(r"C:\build\bidking-lab",)) == []


def test_native_lifecycle_artifact_contract_accepts_narrow_abi(tmp_path: Path) -> None:
    path = _zip(
        tmp_path,
        {
            "scripts/_client_launcher.cp313-win_amd64.pyd": (
                b"main diagnostics_main diagnostics_receipt_main repair_main "
                b"client_lifecycle_contract"
            ),
            "src/bidking_kernel.cp313-win_amd64.pyd": b" ".join(
                _AUDIT.REQUIRED_NATIVE_LIFECYCLE_TERMS
            ),
        },
    )

    assert _AUDIT.audit_zip(path) == []


@pytest.mark.parametrize("missing", _AUDIT.REQUIRED_CLIENT_LAUNCHER_TERMS)
def test_client_lifecycle_receipt_missing_fails_closed(
    tmp_path: Path,
    missing: bytes,
) -> None:
    payload = b" ".join(
        term for term in _AUDIT.REQUIRED_CLIENT_LAUNCHER_TERMS if term != missing
    )
    path = _zip(
        tmp_path,
        {"scripts/_client_launcher.cp313-win_amd64.pyd": payload},
    )

    assert _AUDIT.audit_zip(path) == ["client_lifecycle_receipt_missing"]


@pytest.mark.parametrize(
    "forbidden",
    _AUDIT.FORBIDDEN_REVERSIBLE_LIFECYCLE_TERMS,
)
def test_reversible_client_lifecycle_payload_fails_closed(
    tmp_path: Path,
    forbidden: bytes,
) -> None:
    path = _zip(
        tmp_path,
        {
            "scripts/_client_launcher.cp313-win_amd64.pyd": (
                b"binary diagnostics_receipt_main " + forbidden
            )
        },
    )

    assert _AUDIT.audit_zip(path) == ["reversible_client_lifecycle_payload"]


@pytest.mark.parametrize(
    "missing",
    _AUDIT.REQUIRED_NATIVE_LIFECYCLE_TERMS,
)
def test_native_lifecycle_helper_missing_symbol_or_safe_error_fails_closed(
    tmp_path: Path,
    missing: bytes,
) -> None:
    payload = b" ".join(
        term for term in _AUDIT.REQUIRED_NATIVE_LIFECYCLE_TERMS if term != missing
    )
    path = _zip(
        tmp_path,
        {"src/bidking_kernel.cp313-win_amd64.pyd": payload},
    )

    assert _AUDIT.audit_zip(path) == ["native_lifecycle_helper_missing"]


def test_known_good_positive_release_contract_passes(tmp_path: Path) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["runtime/"] = b""
    path = _zip(tmp_path, members)

    assert _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
        expected_manifest_fields=(
            ("PackageVersion", "v0.3.1"),
            ("SourceCommit", "abcdef"),
        ),
    ) == []


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("direct_root", "sampleable", 80),
        ("source_map", "unsupported", 1),
        ("route_scope", "ranked_production_unsupported_count", 1),
        (
            "route_scope",
            "ranked_production_unsupported_ids_sha256",
            "30304d395c315131761d8af09ca9976e1c689c12d184f2f65182120320dde548",
        ),
    ),
)
def test_release_contract_rejects_stale_rankmap_availability_census(
    tmp_path: Path,
    section: str,
    field: str,
    value: object,
) -> None:
    contract_path, contract, wrapper = _release_contract_v16_with_launcher_wrappers(
        tmp_path
    )
    availability = contract["release_availability"]
    if section == "route_scope":
        availability[section][field] = value
    else:
        availability["policy_census"][section][field] = value
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    members = _release_members(contract_path)
    members.update(
        {member: wrapper for member in _AUDIT.LAUNCHER_WRAPPER_MEMBERS}
    )
    members["runtime/"] = b""
    candidate = _zip(tmp_path, members)

    assert "release_contract_invalid" in _AUDIT.audit_zip(
        candidate,
        release_contract=contract_path,
    )


def test_release_contract_accepts_current_rankmap_availability_census(
    tmp_path: Path,
) -> None:
    contract_path, _contract, wrapper = (
        _release_contract_v16_with_launcher_wrappers(tmp_path)
    )
    members = _release_members(contract_path)
    members.update(
        {member: wrapper for member in _AUDIT.LAUNCHER_WRAPPER_MEMBERS}
    )
    members["runtime/"] = b""

    assert _AUDIT.audit_zip(
        _zip(tmp_path, members),
        release_contract=contract_path,
    ) == []


@pytest.mark.parametrize(
    "field",
    tuple(sorted(_AUDIT.RELEASE_AVAILABILITY_ROUTE_HASH_FIELDS)),
)
@pytest.mark.parametrize(
    "mutation",
    ("missing", "stale", "extra", "wrong_type", "uppercase", "length"),
)
def test_release_contract_rejects_invalid_route_hash_contract(
    tmp_path: Path,
    field: str,
    mutation: str,
) -> None:
    contract_path, contract, wrapper = _release_contract_v16_with_launcher_wrappers(
        tmp_path
    )
    contract["contract_version"] = 17
    route = contract["release_availability"]["route_scope"]
    if mutation == "missing":
        route.pop(field)
    elif mutation == "stale":
        route[field] = "0" * 64
    elif mutation == "extra":
        route[f"unexpected_{field}"] = route[field]
    elif mutation == "wrong_type":
        route[field] = 0
    elif mutation == "uppercase":
        route[field] = route[field].upper()
    else:
        route[field] = route[field][:-1]
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    members = _release_members(contract_path)
    members.update(
        {member: wrapper for member in _AUDIT.LAUNCHER_WRAPPER_MEMBERS}
    )
    members["runtime/"] = b""

    assert "release_contract_invalid" in _AUDIT.audit_zip(
        _zip(tmp_path, members),
        release_contract=contract_path,
    )


@pytest.mark.parametrize(
    "field",
    tuple(sorted(_AUDIT.RELEASE_AVAILABILITY_ROUTE_COUNT_FIELDS)),
)
@pytest.mark.parametrize("mutation", ("stale", "wrong_type"))
def test_release_contract_rejects_invalid_route_count_contract(
    tmp_path: Path,
    field: str,
    mutation: str,
) -> None:
    contract_path, contract, wrapper = _release_contract_v16_with_launcher_wrappers(
        tmp_path
    )
    contract["contract_version"] = 17
    route = contract["release_availability"]["route_scope"]
    route[field] = route[field] + 1 if mutation == "stale" else False
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    members = _release_members(contract_path)
    members.update(
        {member: wrapper for member in _AUDIT.LAUNCHER_WRAPPER_MEMBERS}
    )
    members["runtime/"] = b""

    assert "release_contract_invalid" in _AUDIT.audit_zip(
        _zip(tmp_path, members),
        release_contract=contract_path,
    )


def test_bkenc_real_aead_known_good_candidate_passes_api_and_cli(
    tmp_path: Path,
) -> None:
    contract, receipt, key_file, key, _members, candidate = (
        _bkenc_release_candidate(tmp_path)
    )

    assert _AUDIT.audit_zip(
        candidate,
        release_contract=contract,
        bkenc_receipt=receipt,
        bkenc_key=key,
    ) == []
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(candidate),
            "--release-contract",
            str(contract),
            "--bkenc-receipt",
            str(receipt),
            "--bkenc-key-file",
            str(key_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert base64.b64encode(key).decode("ascii") not in result.stdout


def test_bkenc_receipt_writer_cli_matches_api_without_secret_output(
    tmp_path: Path,
) -> None:
    contract, receipt, key_file, key, _members, candidate = (
        _bkenc_release_candidate(tmp_path)
    )
    cli_receipt = tmp_path / "bkenc-auth-receipt-cli.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--write-bkenc-receipt",
            str(candidate),
            str(cli_receipt),
            str(contract),
            str(key_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert cli_receipt.read_bytes() == receipt.read_bytes()
    assert base64.b64encode(key).decode("ascii") not in result.stdout


def test_bkenc_path_binding_rejects_pre_receipt_member_swap_api_and_cli(
    tmp_path: Path,
) -> None:
    contract, receipt, key_file, key, members, _candidate = (
        _bkenc_release_candidate(tmp_path)
    )
    left = "data/raw/tables/Item.txt"
    right = "external_references/Model/StaticData.cs"
    members[left], members[right] = members[right], members[left]
    swapped = _zip(tmp_path, members)

    api_receipt = tmp_path / "swapped-api-receipt.json"
    with pytest.raises(ValueError, match="authentication tag"):
        _AUDIT.write_bkenc_authentication_receipt(
            swapped,
            api_receipt,
            contract,
            key_file,
        )
    assert not api_receipt.exists()

    cli_receipt = tmp_path / "swapped-cli-receipt.json"
    writer = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--write-bkenc-receipt",
            str(swapped),
            str(cli_receipt),
            str(contract),
            str(key_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert writer.returncode == 1
    assert not cli_receipt.exists()
    assert base64.b64encode(key).decode("ascii") not in (
        writer.stdout + writer.stderr
    )

    # The original valid receipt is already synchronized into BUILD_MANIFEST.
    # Even with that complete positive surface, the swapped candidate must fail.
    assert "release_member_bkenc_authentication_invalid" in _AUDIT.audit_zip(
        swapped,
        release_contract=contract,
        bkenc_receipt=receipt,
        bkenc_key=key,
    )
    auditor = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(swapped),
            "--release-contract",
            str(contract),
            "--bkenc-receipt",
            str(receipt),
            "--bkenc-key-file",
            str(key_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert auditor.returncode == 1
    assert "release_member_bkenc_authentication_invalid" in auditor.stdout
    assert base64.b64encode(key).decode("ascii") not in (
        auditor.stdout + auditor.stderr
    )


def test_known_good_release_candidate_with_external_runtime_receipt_passes_cli(
    tmp_path: Path,
) -> None:
    (
        contract_path,
        approval_path,
        receipt_path,
        members,
    ) = _release_candidate_with_runtime_receipt(tmp_path)
    candidate = _zip(tmp_path, members)

    assert (
        _AUDIT.audit_zip(
            candidate,
            release_contract=contract_path,
            runtime_receipt=receipt_path,
            runtime_approval=approval_path,
        )
        == []
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            str(candidate),
            "--release-contract",
            str(contract_path),
            "--runtime-receipt",
            str(receipt_path),
            "--runtime-approval",
            str(approval_path),
        ],
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0
    assert b"[OK]" in result.stdout


@pytest.mark.parametrize(
    "mutation",
    ("python313-mutated", "extra-runtime-dll", "missing-runtime-file"),
)
def test_runtime_receipt_rejects_mutation_and_unlisted_runtime_member(
    tmp_path: Path,
    mutation: str,
) -> None:
    (
        contract_path,
        approval_path,
        receipt_path,
        members,
    ) = _release_candidate_with_runtime_receipt(tmp_path)
    if mutation == "python313-mutated":
        members["runtime/python/python313.zip"] = b"mutated-stdlib"
    elif mutation == "extra-runtime-dll":
        members["runtime/python/extra_untracked.dll"] = b"unreviewed-runtime"
    else:
        members.pop("runtime/python/python313.zip")
    candidate = _zip(tmp_path, members)

    issues = _AUDIT.audit_zip(
        candidate,
        release_contract=contract_path,
        runtime_receipt=receipt_path,
        runtime_approval=approval_path,
    )

    assert "release_runtime_inventory_mismatch" in issues


def test_runtime_receipt_is_required_externally_and_bound_to_build_manifest(
    tmp_path: Path,
) -> None:
    (
        contract_path,
        approval_path,
        receipt_path,
        members,
    ) = _release_candidate_with_runtime_receipt(tmp_path)
    candidate = _zip(tmp_path, members)

    assert "release_runtime_inventory_receipt_missing" in _AUDIT.audit_zip(
        candidate,
        release_contract=contract_path,
        runtime_approval=approval_path,
    )

    members["BUILD_MANIFEST.txt"] = members["BUILD_MANIFEST.txt"].replace(
        b"RuntimeInventoryReceiptSHA256: ",
        b"RuntimeInventoryReceiptSHA256: deadbeef",
    )
    candidate = _zip(tmp_path, members)
    assert (
        "release_runtime_inventory_receipt_hash_mismatch"
        in _AUDIT.audit_zip(
            candidate,
            release_contract=contract_path,
            runtime_receipt=receipt_path,
            runtime_approval=approval_path,
        )
    )


def test_runtime_receipt_rejects_changed_zip_compression_attributes(
    tmp_path: Path,
) -> None:
    (
        contract_path,
        approval_path,
        receipt_path,
        members,
    ) = _release_candidate_with_runtime_receipt(tmp_path)
    candidate = tmp_path / "candidate.zip"
    with zipfile.ZipFile(candidate, "w") as archive:
        for name, payload in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = (
                zipfile.ZIP_DEFLATED
                if name == "runtime/python/python313.zip"
                else zipfile.ZIP_STORED
            )
            archive.writestr(info, payload)

    assert (
        "release_runtime_inventory_mismatch"
        in _AUDIT.audit_zip(
            candidate,
            release_contract=contract_path,
            runtime_receipt=receipt_path,
            runtime_approval=approval_path,
        )
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "extra-receipt-field",
        "extra-entry-field",
        "wrong-version",
        "missing-root",
        "missing-parent",
    ),
)
def test_runtime_receipt_schema_and_directory_tree_are_canonical(
    tmp_path: Path,
    mutation: str,
) -> None:
    (
        contract_path,
        approval_path,
        receipt_path,
        members,
    ) = _release_candidate_with_runtime_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if mutation == "extra-receipt-field":
        receipt["unexpected"] = True
    elif mutation == "extra-entry-field":
        receipt["entries"][0]["unexpected"] = True
    elif mutation == "wrong-version":
        receipt["receipt_version"] = 2
    elif mutation == "missing-root":
        receipt["entries"] = [
            entry
            for entry in receipt["entries"]
            if entry["path"] != _AUDIT.RUNTIME_INVENTORY_PREFIX
        ]
    else:
        receipt["entries"].append(
            {
                "path": "runtime/python/Lib/private.py",
                "type": "file",
                "size": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
                "compression_method": zipfile.ZIP_STORED,
            }
        )
        receipt["entries"].sort(key=lambda entry: entry["path"])
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    candidate = _zip(tmp_path, members)

    assert (
        "release_runtime_inventory_receipt_invalid"
        in _AUDIT.audit_zip(
            candidate,
            release_contract=contract_path,
            runtime_receipt=receipt_path,
            runtime_approval=approval_path,
        )
    )


def test_runtime_receipt_writer_freezes_canonical_stage_tree(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime" / "python"
    (runtime_root / "Lib").mkdir(parents=True)
    (runtime_root / "python313.zip").write_bytes(b"stdlib")
    (runtime_root / "Lib" / "empty.py").write_bytes(b"")
    approval = _runtime_approval_for_filesystem(tmp_path, runtime_root)
    receipt = tmp_path / "runtime-receipt.json"

    _AUDIT.write_runtime_inventory_receipt(runtime_root, receipt, approval)
    cli_receipt = tmp_path / "runtime-receipt-cli.json"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            "--write-runtime-receipt",
            str(runtime_root),
            str(cli_receipt),
            str(approval),
        ],
        check=False,
        capture_output=True,
    )

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert result.returncode == 0
    assert b"[OK]" in result.stdout
    assert cli_receipt.read_bytes() == receipt.read_bytes()
    assert payload["schema"] == _AUDIT.RUNTIME_INVENTORY_RECEIPT_SCHEMA
    assert payload["file_count"] == 2
    assert payload["directory_count"] == 2
    assert payload["total_bytes"] == len(b"stdlib")
    assert payload["entries"] == sorted(
        payload["entries"],
        key=lambda entry: entry["path"],
    )
    rows = {entry["path"]: entry for entry in payload["entries"]}
    assert rows["runtime/python/"]["type"] == "directory"
    assert rows["runtime/python/Lib/"]["type"] == "directory"
    assert rows["runtime/python/Lib/"]["compression_method"] == zipfile.ZIP_STORED
    assert (
        rows["runtime/python/python313.zip"]["compression_method"]
        == zipfile.ZIP_DEFLATED
    )
    assert (
        rows["runtime/python/Lib/empty.py"]["compression_method"]
        == zipfile.ZIP_STORED
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "extra-runtime-dll",
        "missing-runtime-file",
        "tampered-runtime-file",
        "old-import-site",
        "old-sitecustomize",
        "old-runtime-layout",
    ),
)
def test_runtime_receipt_writer_rejects_pre_freeze_stage_divergence(
    tmp_path: Path,
    mutation: str,
) -> None:
    runtime_root = tmp_path / "runtime" / "python"
    site_packages = runtime_root / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    runtime_file = runtime_root / "python313.zip"
    runtime_file.write_bytes(b"stdlib")
    runtime_pth = runtime_root / "python313._pth"
    runtime_pth.write_bytes(b"python313.zip\r\n.\r\nLib\\site-packages\r\n")
    approval = _runtime_approval_for_filesystem(tmp_path, runtime_root)
    if mutation == "extra-runtime-dll":
        (runtime_root / "extra_untracked.dll").write_bytes(b"unreviewed-runtime")
    elif mutation == "missing-runtime-file":
        runtime_file.unlink()
    elif mutation == "tampered-runtime-file":
        runtime_file.write_bytes(b"tampered-stdlib")
    elif mutation in {"old-import-site", "old-runtime-layout"}:
        runtime_pth.write_bytes(runtime_pth.read_bytes() + b"import site\r\n")
    if mutation in {"old-sitecustomize", "old-runtime-layout"}:
        (site_packages / "sitecustomize.py").write_bytes(
            b"import os\r\n"
            b"_rt = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\r\n"
            b"_tcl = os.path.join(_rt, 'tcl')\r\n"
            b"if os.path.isdir(_tcl):\r\n"
            b"    os.environ.setdefault('TCL_LIBRARY', os.path.join(_tcl, 'tcl8.6'))\r\n"
            b"    os.environ.setdefault('TK_LIBRARY', os.path.join(_tcl, 'tk8.6'))\r\n"
        )
    receipt = tmp_path / "receipt-must-not-exist.json"

    with pytest.raises(ValueError, match="runtime inventory is not approved"):
        _AUDIT.write_runtime_inventory_receipt(runtime_root, receipt, approval)

    assert not receipt.exists()


def test_stage_runtime_receipt_and_zip_auditor_interoperate_on_complete_candidate(
    tmp_path: Path,
) -> None:
    contract_path, contract = _release_contract(tmp_path)
    contract["runtime_inventory"]["receipt_required"] = True
    contract["runtime_inventory"]["approval_required"] = True
    contract["required_manifest_fields"].append("RuntimeInventoryApprovalSHA256")
    contract["required_manifest_fields"].append("RuntimeInventoryReceiptSHA256")
    members = _release_members(contract_path)
    stage_root = tmp_path / "stage"
    for name, payload in members.items():
        destination = stage_root / Path(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    runtime_root = stage_root / "runtime" / "python"
    approval_path = _runtime_approval_for_filesystem(tmp_path, runtime_root)
    approval_sha256 = _AUDIT._runtime_inventory_approval_sha256(
        json.loads(approval_path.read_text(encoding="utf-8"))
    )
    contract["fixed_manifest_values"]["RuntimeInventoryApprovalSHA256"] = (
        approval_sha256
    )
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    (stage_root / "BUILD_MANIFEST.txt").write_bytes(
        _release_members(contract_path)["BUILD_MANIFEST.txt"]
    )
    receipt_path = tmp_path / "stage-runtime-receipt.json"
    _AUDIT.write_runtime_inventory_receipt(
        runtime_root,
        receipt_path,
        approval_path,
    )
    build_manifest = stage_root / "BUILD_MANIFEST.txt"
    build_manifest.write_bytes(
        build_manifest.read_bytes()
        + (
            "RuntimeInventoryApprovalSHA256: "
            f"{approval_sha256}\n"
            "RuntimeInventoryReceiptSHA256: "
            f"{hashlib.sha256(receipt_path.read_bytes()).hexdigest()}\n"
        ).encode()
    )
    candidate = tmp_path / "complete-candidate.zip"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            "--write-canonical-zip",
            str(stage_root),
            str(candidate),
        ],
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0
    assert b"[OK]" in result.stdout
    second_candidate = tmp_path / "complete-candidate-second.zip"
    _AUDIT.write_canonical_release_zip(stage_root, second_candidate)
    assert second_candidate.read_bytes() == candidate.read_bytes()
    with zipfile.ZipFile(candidate) as archive:
        infos = archive.infolist()
        assert [info.orig_filename for info in infos] == sorted(
            info.orig_filename for info in infos
        )
        assert all("\\" not in info.orig_filename for info in infos)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
        assert all(info.create_system == 0 for info in infos)
    assert (
        _AUDIT.audit_zip(
            candidate,
            release_contract=contract_path,
            runtime_receipt=receipt_path,
            runtime_approval=approval_path,
        )
        == []
    )
    audit_result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            str(candidate),
            "--release-contract",
            str(contract_path),
            "--runtime-receipt",
            str(receipt_path),
            "--runtime-approval",
            str(approval_path),
        ],
        check=False,
        capture_output=True,
    )
    assert audit_result.returncode == 0
    assert b"[OK]" in audit_result.stdout


def test_source_pinned_runtime_approval_rejects_pre_freeze_extra_and_joint_rebind(
    tmp_path: Path,
) -> None:
    contract_path, contract = _release_contract(tmp_path)
    contract["runtime_inventory"]["receipt_required"] = True
    contract["runtime_inventory"]["approval_required"] = True
    contract["required_manifest_fields"].extend(
        ("RuntimeInventoryApprovalSHA256", "RuntimeInventoryReceiptSHA256")
    )
    stage_root = tmp_path / "stage"
    for name, payload in _release_members(contract_path).items():
        destination = stage_root / Path(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    runtime_root = stage_root / "runtime" / "python"
    approved = _runtime_approval_for_filesystem(
        tmp_path,
        runtime_root,
        name="source-approved-runtime.json",
    )
    approved_sha256 = _AUDIT._runtime_inventory_approval_sha256(
        json.loads(approved.read_text(encoding="utf-8"))
    )
    contract["fixed_manifest_values"]["RuntimeInventoryApprovalSHA256"] = (
        approved_sha256
    )
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    (stage_root / "BUILD_MANIFEST.txt").write_bytes(
        _release_members(contract_path)["BUILD_MANIFEST.txt"]
    )
    (runtime_root / "extra_untracked.dll").write_bytes(b"unreviewed-runtime")

    rejected_receipt = tmp_path / "writer-must-not-create-receipt.json"
    writer = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            "--write-runtime-receipt",
            str(runtime_root),
            str(rejected_receipt),
            str(approved),
        ],
        check=False,
        capture_output=True,
    )
    assert writer.returncode == 1
    assert b"[FAIL]" in writer.stdout
    assert not rejected_receipt.exists()

    rebound_approval = _runtime_approval_for_filesystem(
        tmp_path,
        runtime_root,
        name="rebound-runtime-approval.json",
    )
    adversarial_receipt = tmp_path / "adversarial-rebound-receipt.json"
    _AUDIT.write_runtime_inventory_receipt(
        runtime_root,
        adversarial_receipt,
        rebound_approval,
    )
    rebound_sha256 = _AUDIT._runtime_inventory_approval_sha256(
        json.loads(rebound_approval.read_text(encoding="utf-8"))
    )
    build_manifest = stage_root / "BUILD_MANIFEST.txt"
    build_manifest.write_bytes(
        build_manifest.read_bytes()
        + (
            "RuntimeInventoryApprovalSHA256: "
            f"{rebound_sha256}\n"
            "RuntimeInventoryReceiptSHA256: "
            f"{hashlib.sha256(adversarial_receipt.read_bytes()).hexdigest()}\n"
        ).encode()
    )
    candidate = tmp_path / "candidate-with-extra-runtime.zip"
    _AUDIT.write_canonical_release_zip(stage_root, candidate)

    issues = _AUDIT.audit_zip(
        candidate,
        release_contract=contract_path,
        runtime_receipt=adversarial_receipt,
        runtime_approval=rebound_approval,
    )
    assert (
        "release_manifest_field_mismatch: runtimeinventoryapprovalsha256"
        in issues
    )
    cli = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            str(candidate),
            "--release-contract",
            str(contract_path),
            "--runtime-receipt",
            str(adversarial_receipt),
            "--runtime-approval",
            str(rebound_approval),
        ],
        check=False,
        capture_output=True,
    )
    assert cli.returncode == 1
    assert b"runtimeinventoryapprovalsha256" in cli.stdout


@pytest.mark.parametrize(
    ("canonical", "alias", "expected_issue"),
    (
        ("BidKingLive.exe", "./BidKingLive.exe", "release_member_path_invalid"),
        (
            "BidKingLive.exe",
            "bidkinglive.exe",
            "release_member_case_mismatch: BidKingLive.exe",
        ),
        (
            "runtime/python/python.exe",
            r"runtime\python\python.exe",
            "release_member_path_invalid",
        ),
        (
            "runtime/python/python.exe",
            "runtime//python/python.exe",
            "release_member_path_invalid",
        ),
    ),
)
def test_positive_release_contract_rejects_noncanonical_required_member_aliases(
    tmp_path: Path,
    canonical: str,
    alias: str,
    expected_issue: str,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    payload = members.pop(canonical)
    if "\\" in alias:
        placeholder = alias.replace("\\", "_")
        members[placeholder] = payload
    else:
        placeholder = alias
        members[alias] = payload
    path = _zip(tmp_path, members)
    if placeholder != alias:
        raw = path.read_bytes()
        original = placeholder.encode("ascii")
        replacement = alias.encode("ascii")
        assert raw.count(original) == 2
        path.write_bytes(raw.replace(original, replacement))

    issues = _AUDIT.audit_zip(path, release_contract=contract_path)

    assert expected_issue in issues


@pytest.mark.parametrize(
    ("alias", "expected_issue"),
    (
        ("/notes.txt", "release_member_path_invalid"),
        ("C:/notes.txt", "release_member_path_invalid"),
        ("notes/./detail.txt", "release_member_path_invalid"),
        ("notes/../detail.txt", "release_member_path_invalid"),
        ("notes/e\u0301.txt", "release_member_path_invalid"),
        ("Data/raw/tables/Shadow.txt", "release_member_case_mismatch"),
    ),
)
def test_positive_release_contract_rejects_other_noncanonical_member_paths(
    tmp_path: Path,
    alias: str,
    expected_issue: str,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members[alias] = b"untrusted"
    path = _zip(tmp_path, members)

    assert expected_issue in _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    )


def test_positive_release_contract_rejects_duplicate_and_casefold_colliding_members(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    canonical = list(_release_members(contract_path).items())

    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = _zip_rows(
            tmp_path,
            [*canonical, ("notes.txt", b"one"), ("notes.txt", b"two")],
        )
    assert "release_member_duplicate" in _AUDIT.audit_zip(
        duplicate,
        release_contract=contract_path,
    )

    collision = _zip_rows(
        tmp_path,
        [*canonical, ("notes/Alpha.txt", b"one"), ("notes/alpha.txt", b"two")],
    )
    assert "release_member_casefold_collision" in _AUDIT.audit_zip(
        collision,
        release_contract=contract_path,
    )


def test_positive_release_contract_rejects_raw_nul_member_path(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["notes/badXname.txt"] = b"untrusted"
    path = _zip(tmp_path, members)
    raw = path.read_bytes()
    needle = b"notes/badXname.txt"
    replacement = b"notes/bad\x00name.txt"
    assert raw.count(needle) == 2
    path.write_bytes(raw.replace(needle, replacement))
    with zipfile.ZipFile(path) as archive:
        assert any("\x00" in info.orig_filename for info in archive.infolist())

    assert "release_member_path_invalid" in _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    )


def test_release_contract_cli_accepts_canonical_and_rejects_dot_root_alias(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    canonical = _zip(tmp_path, members)
    command = [
        sys.executable,
        "-I",
        "-B",
        str(SCRIPT),
        str(canonical),
        "--release-contract",
        str(contract_path),
    ]
    good = subprocess.run(
        command,
        check=False,
        capture_output=True,
    )
    assert good.returncode == 0
    assert b"[OK]" in good.stdout

    launcher = members.pop("BidKingLive.exe")
    members["./BidKingLive.exe"] = launcher
    alias = _zip(tmp_path, members)
    bad = subprocess.run(
        [*command[:4], str(alias), *command[5:]],
        check=False,
        capture_output=True,
    )
    assert bad.returncode == 1
    assert b"[FAIL]" in bad.stdout
    assert b"release_member_path_invalid" in bad.stdout


def test_release_contract_cli_rejects_trailing_dot_alias_that_overwrites_on_windows(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = list(_release_members(contract_path).items())
    alias_payload = b"trailing-dot-alias"
    alias = _zip_rows(
        tmp_path,
        [*members, ("BidKingLive.exe.", alias_payload)],
    )
    with zipfile.ZipFile(alias) as archive:
        raw_names = [info.orig_filename for info in archive.infolist()]
    assert "BidKingLive.exe" in raw_names
    assert "BidKingLive.exe." in raw_names

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            str(alias),
            "--release-contract",
            str(contract_path),
        ],
        check=False,
        capture_output=True,
    )

    assert result.returncode == 1
    assert b"[FAIL]" in result.stdout
    assert b"release_member_path_invalid" in result.stdout

    if os.name == "nt":
        extracted = tmp_path / "windows-extract"
        with zipfile.ZipFile(alias) as archive:
            archive.extractall(extracted)
        assert (extracted / "BidKingLive.exe").read_bytes() == alias_payload
        assert (extracted / "BidKingLive.exe.").read_bytes() == alias_payload


@pytest.mark.parametrize(
    "invalid_name",
    (
        "notes/trailing-space.txt ",
        "notes/trailing-dot.txt.",
        "notes/report.txt:payload",
        "CON",
        "NUL.txt",
        "notes/COM1.log",
        "notes/LPT9.archive",
        "notes/COM¹.txt",
        "notes/LPT³.archive",
        "notes/BIDKIN~1.EXE",
        "notes/CON .txt",
    ),
)
def test_positive_release_contract_rejects_windows_unsafe_member_names(
    tmp_path: Path,
    invalid_name: str,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members[invalid_name] = b"untrusted"
    path = _zip(tmp_path, members)

    assert "release_member_path_invalid" in _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    )


def test_positive_release_contract_accepts_windows_reserved_name_neighbours(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    for name in (
        "notes/console.txt",
        "notes/nulled.txt",
        "notes/COM10.txt",
        "notes/LPT0.txt",
        "notes/COM¹0.txt",
        "notes/BIDKIN~X.EXE",
        "notes/BIDKIN~0.EXE",
        "notes/space name.txt",
        "notes/file.name",
    ):
        members[name] = b"safe"
    path = _zip(tmp_path, members)

    assert _AUDIT.audit_zip(path, release_contract=contract_path) == []


@pytest.mark.parametrize(
    "aliases",
    (
        (("notes", b"file"), ("notes/", b"")),
        (("notes/", b""), ("NOTES/", b"")),
        (("notes.txt", b"one"), ("NOTES.TXT", b"two")),
    ),
)
def test_positive_release_contract_rejects_windows_file_directory_collisions(
    tmp_path: Path,
    aliases: tuple[tuple[str, bytes], tuple[str, bytes]],
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    rows = [*list(_release_members(contract_path).items()), *aliases]
    path = _zip_rows(tmp_path, rows)

    assert "release_member_windows_collision" in _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    )


@pytest.mark.parametrize(
    "aliases",
    (
        (("notes", b"file"), ("notes/child.txt", b"child")),
        (("notes/child.txt", b"child"), ("notes", b"file")),
    ),
)
def test_positive_release_contract_rejects_implicit_windows_file_directory_collisions(
    tmp_path: Path,
    aliases: tuple[tuple[str, bytes], tuple[str, bytes]],
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    rows = [*list(_release_members(contract_path).items()), *aliases]
    path = _zip_rows(tmp_path, rows)

    assert "release_member_windows_collision" in _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    )

    if os.name == "nt":
        with zipfile.ZipFile(path) as archive:
            with pytest.raises(OSError):
                archive.extractall(tmp_path / "windows-collision-extract")


@pytest.mark.parametrize(
    ("rows", "expected_issues"),
    (
        (
            [
                ("notes/CANARY_PRIVATE_MEMBER_7F9A.txt.", b"private-build"),
            ],
            ("release_member_path_invalid",),
        ),
        (
            [
                ("notes/CANARY_PRIVATE_MEMBER_7F9A.txt", b"private-build"),
                ("notes/CANARY_PRIVATE_MEMBER_7F9A.txt", b"private-build"),
            ],
            (
                "release_member_duplicate",
                "release_member_casefold_collision",
                "release_member_windows_collision",
            ),
        ),
        (
            [
                ("notes/CANARY_PRIVATE_MEMBER_7F9A.txt", b"private-build"),
                ("notes/canary_private_member_7f9a.txt", b"private-build"),
            ],
            (
                "release_member_casefold_collision",
                "release_member_windows_collision",
            ),
        ),
    ),
)
def test_release_contract_member_identity_failure_redacts_untrusted_names(
    tmp_path: Path,
    rows: list[tuple[str, bytes]],
    expected_issues: tuple[str, ...],
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    all_rows = [*list(_release_members(contract_path).items()), *rows]
    if "release_member_duplicate" in expected_issues:
        with pytest.warns(UserWarning, match="Duplicate name"):
            path = _zip_rows(tmp_path, all_rows)
    else:
        path = _zip_rows(tmp_path, all_rows)

    issues = _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
        forbidden_paths=("private-build",),
    )

    assert issues == list(expected_issues)
    assert all("CANARY_PRIVATE_MEMBER_7F9A" not in issue for issue in issues)
    assert all("canary_private_member_7f9a" not in issue for issue in issues)


@pytest.mark.parametrize(
    "aliases",
    (
        (
            ("notes/CANARY_PRIVATE_MEMBER_7F9A", b"private-build"),
            ("notes/CANARY_PRIVATE_MEMBER_7F9A/child.txt", b"private-build"),
        ),
        (
            ("notes/CANARY_PRIVATE_MEMBER_7F9A/child.txt", b"private-build"),
            ("notes/CANARY_PRIVATE_MEMBER_7F9A", b"private-build"),
        ),
    ),
)
def test_release_contract_cli_redacts_implicit_collision_names_in_both_orders(
    tmp_path: Path,
    aliases: tuple[tuple[str, bytes], tuple[str, bytes]],
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    path = _zip_rows(
        tmp_path,
        [*list(_release_members(contract_path).items()), *aliases],
    )

    issues = _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
        forbidden_paths=("private-build",),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            str(path),
            "--release-contract",
            str(contract_path),
            "--forbid-path",
            "private-build",
        ],
        check=False,
        capture_output=True,
    )

    assert issues == ["release_member_windows_collision"]
    assert result.returncode == 1
    assert b"[FAIL]" in result.stdout
    assert b"release_member_windows_collision" in result.stdout
    assert b"CANARY_PRIVATE_MEMBER_7F9A" not in result.stdout
    assert b"CANARY_PRIVATE_MEMBER_7F9A" not in result.stderr

    if os.name == "nt":
        with zipfile.ZipFile(path) as archive:
            with pytest.raises(OSError):
                archive.extractall(tmp_path / "windows-private-collision-extract")


def test_positive_release_contract_accepts_implicit_windows_directories(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["notes/child.txt"] = b"child"
    members["notes/sibling.txt"] = b"sibling"
    path = _zip(tmp_path, members)

    assert _AUDIT.audit_zip(path, release_contract=contract_path) == []

    if os.name == "nt":
        extracted = tmp_path / "windows-positive-extract"
        with zipfile.ZipFile(path) as archive:
            archive.extractall(extracted)
        assert (extracted / "notes" / "child.txt").read_bytes() == b"child"
        assert (extracted / "notes" / "sibling.txt").read_bytes() == b"sibling"


def test_global_archive_entry_budget_rejects_oversized_inventory(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "entry-budget.zip"
    with zipfile.ZipFile(candidate, "w") as archive:
        for index in range(_AUDIT.RELEASE_ARCHIVE_LIMITS["max_entries"] + 1):
            archive.writestr(f"rows/{index}.txt", b"")

    assert _AUDIT.audit_zip(candidate) == ["release_archive_limits_exceeded"]


def test_global_archive_size_and_path_budgets_fail_before_payload_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        _AUDIT.RELEASE_ARCHIVE_LIMITS,
        "max_member_uncompressed_bytes",
        4,
    )
    too_large = _zip(tmp_path, {"payload.bin": b"12345"})
    assert _AUDIT.audit_zip(too_large) == ["release_archive_limits_exceeded"]

    total_root = tmp_path / "total"
    total_root.mkdir()
    monkeypatch.setitem(
        _AUDIT.RELEASE_ARCHIVE_LIMITS,
        "max_member_uncompressed_bytes",
        10,
    )
    monkeypatch.setitem(
        _AUDIT.RELEASE_ARCHIVE_LIMITS,
        "max_total_uncompressed_bytes",
        8,
    )
    too_much = _zip(total_root, {"a.bin": b"12345", "b.bin": b"67890"})
    assert _AUDIT.audit_zip(too_much) == ["release_archive_limits_exceeded"]

    path_root = tmp_path / "path"
    path_root.mkdir()
    monkeypatch.setitem(
        _AUDIT.RELEASE_ARCHIVE_LIMITS,
        "max_member_path_chars",
        20,
    )
    long_path = _zip(path_root, {f"notes/{'x' * 20}.txt": b"safe"})
    assert _AUDIT.audit_zip(long_path) == ["release_member_path_invalid"]


def test_global_archive_compression_budget_and_method_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_AUDIT.RELEASE_ARCHIVE_LIMITS, "max_compression_ratio", 10)
    ratio_path = tmp_path / "ratio.zip"
    with zipfile.ZipFile(
        ratio_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("payload.txt", b"A" * 100_000)
    assert _AUDIT.audit_zip(ratio_path) == [
        "release_member_compression_invalid"
    ]

    method_path = tmp_path / "method.zip"
    with zipfile.ZipFile(
        method_path,
        "w",
        compression=zipfile.ZIP_BZIP2,
    ) as archive:
        archive.writestr("payload.txt", b"not-release-compression")
    assert _AUDIT.audit_zip(method_path) == [
        "release_member_compression_invalid"
    ]


@pytest.mark.parametrize("kind", ("unix-symlink", "windows-reparse"))
def test_global_archive_rejects_symlink_and_reparse_entries(
    tmp_path: Path,
    kind: str,
) -> None:
    candidate = tmp_path / f"{kind}.zip"
    with zipfile.ZipFile(candidate, "w") as archive:
        if kind == "unix-symlink":
            entry = zipfile.ZipInfo("link")
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            payload = b"target"
        else:
            entry = zipfile.ZipInfo("junction/")
            entry.create_system = 0
            entry.external_attr = 0x400 | 0x10
            payload = b""
        archive.writestr(entry, payload)

    assert _AUDIT.audit_zip(candidate) == ["release_member_type_invalid"]


def test_positive_release_contract_rejects_missing_public_candidate_surface(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    for name in (
        "BidKingLive.exe",
        "使用说明.txt",
        "LICENSE.txt",
        "公共大数据平台.html",
        "runtime/python/python.exe",
        "data/processed/upload_config.enc",
    ):
        del members[name]
    path = _zip(tmp_path, members)

    issues = _AUDIT.audit_zip(path, release_contract=contract_path)

    assert "release_member_missing: BidKingLive.exe" in issues
    assert "release_member_missing: 使用说明.txt" in issues
    assert "release_member_missing: LICENSE.txt" in issues
    assert "release_member_missing: 公共大数据平台.html" in issues
    assert "release_member_missing: runtime/python/python.exe" in issues
    assert "release_member_missing: data/processed/upload_config.enc" in issues
    assert "release_fixed_member_missing: BidKingLive.exe" in issues
    assert "release_fixed_member_missing: runtime/python/python.exe" in issues
    assert "release_protected_member_missing: data/processed/upload_config.enc" in issues


def test_positive_release_contract_rejects_manifest_omission_and_external_mismatch(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["BUILD_MANIFEST.txt"] = members["BUILD_MANIFEST.txt"].replace(
        b"SourceCommit: abcdef\n",
        b"",
    )
    path = _zip(tmp_path, members)

    issues = _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
        expected_manifest_fields=(("PackageVersion", "v9.9.9"),),
    )

    assert "release_manifest_field_missing: SourceCommit" in issues
    assert "release_manifest_field_mismatch: packageversion" in issues


def test_positive_release_contract_rejects_changed_fixed_asset_and_dependency(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["BidKingLive.exe"] = b"replacement"
    members[
        "runtime/python/Lib/site-packages/demo_dep-1.0.dist-info/METADATA"
    ] = b"Metadata-Version: 2.1\nName: demo-dep\nVersion: 2.0\n"
    path = _zip(tmp_path, members)

    issues = _AUDIT.audit_zip(path, release_contract=contract_path)

    assert "release_fixed_member_hash_mismatch: BidKingLive.exe" in issues
    assert "runtime_dependency_mismatch: demo-dep" in issues


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    (
        ("good", None),
        ("missing", "release_member_missing: 诊断工具/激活网络诊断.bat"),
        ("wrong_path", "release_member_missing: 诊断工具/激活网络诊断.bat"),
        ("casefold", "release_member_case_mismatch: 诊断工具/激活网络诊断.bat"),
        ("alias", "release_member_path_invalid"),
        (
            "same_size_tamper",
            "release_fixed_member_hash_mismatch: 诊断工具/激活网络诊断.bat",
        ),
    ),
)
def test_diagnostic_tool_members_are_required_at_exact_paths_and_bytes(
    tmp_path: Path,
    mutation: str,
    expected_issue: str | None,
) -> None:
    contract_path, contract = _release_contract(tmp_path)
    payloads = {
        "诊断工具/收集诊断.bat": (
            ROOT / "scripts" / "collect_diagnostics.bat"
        ).read_bytes(),
        "诊断工具/激活网络诊断.bat": (
            ROOT / "scripts" / "activation_transport_diagnostic.bat"
        ).read_bytes(),
        "诊断工具/activation_transport_diagnostic.py": (
            ROOT / "scripts" / "activation_transport_diagnostic.py"
        ).read_bytes(),
    }
    contract["required_members"]["common"].extend(payloads)
    contract["fixed_member_sha256"].update(
        {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}
    )
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    members = _release_members(contract_path)
    members.update(payloads)
    target = "诊断工具/激活网络诊断.bat"
    if mutation == "missing":
        del members[target]
    elif mutation == "wrong_path":
        members["诊断工具/activation_transport_diagnostic.bat"] = members.pop(target)
    elif mutation == "casefold":
        members["诊断工具/激活网络诊断.BAT"] = members.pop(target)
    elif mutation == "alias":
        members["诊断工具/./激活网络诊断.bat"] = members.pop(target)
    elif mutation == "same_size_tamper":
        members[target] = b"#" + members[target][1:]

    issues = _AUDIT.audit_zip(
        _zip(tmp_path, members),
        release_contract=contract_path,
    )

    if expected_issue is None:
        assert issues == []
    else:
        assert expected_issue in issues


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    (
        ("good", None),
        (
            "missing",
            "release_member_missing: scripts/package_data_fingerprints.json",
        ),
        (
            "wrong_path",
            "release_member_missing: scripts/package_data_fingerprints.json",
        ),
        (
            "casefold",
            "release_member_case_mismatch: scripts/package_data_fingerprints.json",
        ),
        (
            "alias",
            "release_member_path_invalid",
        ),
        (
            "changed_bytes",
            "release_fixed_member_hash_mismatch: scripts/package_data_fingerprints.json",
        ),
    ),
)
def test_package_fingerprint_manifest_is_required_at_exact_path_and_bytes(
    tmp_path: Path,
    mutation: str,
    expected_issue: str | None,
) -> None:
    contract_path, contract = _release_contract(tmp_path)
    target = "scripts/package_data_fingerprints.json"
    payload = (ROOT / target).read_bytes()
    contract["required_members"]["common"].append(target)
    contract["fixed_member_sha256"][target] = hashlib.sha256(payload).hexdigest()
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    members = _release_members(contract_path)
    members[target] = payload
    if mutation == "missing":
        del members[target]
    elif mutation == "wrong_path":
        members["package_data_fingerprints.json"] = members.pop(target)
    elif mutation == "casefold":
        members["scripts/PACKAGE_DATA_FINGERPRINTS.json"] = members.pop(target)
    elif mutation == "alias":
        members["scripts/./package_data_fingerprints.json"] = members.pop(target)
    elif mutation == "changed_bytes":
        members[target] += b"\n"

    issues = _AUDIT.audit_zip(
        _zip(tmp_path, members),
        release_contract=contract_path,
    )
    if expected_issue is None:
        assert issues == []
    else:
        assert expected_issue in issues


def test_release_contract_rejects_fixed_member_not_declared_required(
    tmp_path: Path,
) -> None:
    contract_path, contract = _release_contract(tmp_path)
    contract["fixed_member_sha256"]["scripts/unrequired.json"] = "0" * 64
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    issues = _AUDIT.audit_zip(
        _zip(tmp_path, _release_members(contract_path)),
        release_contract=contract_path,
    )
    assert "release_contract_invalid" in issues


@pytest.mark.parametrize(
    "mutation",
    ("missing", "changed", "powershell", "python", "direct_wait"),
)
def test_v16_launcher_wrappers_are_exact_async_root_exe_only(
    tmp_path: Path,
    mutation: str,
) -> None:
    contract_path, _contract, wrapper = _release_contract_v16_with_launcher_wrappers(
        tmp_path
    )
    members = _release_members(contract_path)
    members.update({member: wrapper for member in _AUDIT.LAUNCHER_WRAPPER_MEMBERS})
    target = _AUDIT.LAUNCHER_WRAPPER_MEMBERS[0]
    if mutation == "missing":
        del members[target]
    elif mutation == "changed":
        members[target] = wrapper + b"rem"
    elif mutation == "powershell":
        members[target] = b'@echo off\r\npowershell -File "..\\scripts\\start.ps1"\r\n'
    elif mutation == "python":
        members[target] = b'@echo off\r\n..\\runtime\\python\\python.exe app.py\r\n'
    else:
        members[target] = b'@echo off\r\n"%~dp0..\\BidKingLive.exe"\r\n'
    path = _zip(tmp_path, members)

    issues = _AUDIT.audit_zip(path, release_contract=contract_path)

    if mutation == "missing":
        assert f"release_member_missing: {target}" in issues
    else:
        assert f"release_launcher_wrapper_invalid: {target}" in issues


def test_v16_launcher_wrappers_pass_with_same_ascii_crlf_bytes(
    tmp_path: Path,
) -> None:
    contract_path, _contract, wrapper = _release_contract_v16_with_launcher_wrappers(
        tmp_path
    )
    members = _release_members(contract_path)
    members.update({member: wrapper for member in _AUDIT.LAUNCHER_WRAPPER_MEMBERS})
    path = _zip(tmp_path, members)

    issues = _AUDIT.audit_zip(path, release_contract=contract_path)

    assert issues == []
    assert wrapper.isascii()
    assert b"\r\n" in wrapper and b"\n" not in wrapper.replace(b"\r\n", b"")
    assert hashlib.sha256(wrapper).hexdigest() == _AUDIT.LAUNCHER_WRAPPER_SHA256
    assert wrapper.count(b"BidKingLive.exe") == 3
    assert b"start \"\"" in wrapper
    assert b"powershell" not in wrapper.lower()
    assert b"python" not in wrapper.lower()


def test_positive_release_contract_rejects_unexpected_distribution(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members[
        "runtime/python/Lib/site-packages/surprise-1.0.dist-info/METADATA"
    ] = b"Metadata-Version: 2.1\nName: surprise\nVersion: 1.0\n"
    path = _zip(tmp_path, members)

    assert "runtime_dependency_unexpected: surprise" in _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    )


def test_positive_release_contract_rejects_plaintext_moat_and_undeclared_new_table(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["data/raw/tables/Item.txt"] = b"plaintext"
    members["data/raw/tables/NewTable.txt"] = b"BKENC2\nnew"
    path = _zip(tmp_path, members)

    issues = _AUDIT.audit_zip(path, release_contract=contract_path)

    assert "release_member_requires_bkenc2_gzip: data/raw/tables/Item.txt" in issues
    assert (
        "release_member_protection_undeclared: data/raw/tables/NewTable.txt"
        in issues
    )


def test_positive_release_contract_rejects_legacy_bkenc1_for_compressed_moat(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["data/raw/tables/Item.txt"] = b"BKENC1\nlegacy-envelope"

    issues = _AUDIT.audit_zip(
        _zip(tmp_path, members),
        release_contract=contract_path,
    )

    assert "release_member_requires_bkenc2_gzip: data/raw/tables/Item.txt" in issues


@pytest.mark.parametrize("member", _CURRENT_RANKMAP_RELEASE_MEMBERS)
@pytest.mark.parametrize("mutation", ("missing", "plaintext", "bkenc1"))
def test_current_rankmap_release_members_fail_closed_under_auditor(
    tmp_path: Path,
    member: str,
    mutation: str,
) -> None:
    contract_path, contract = _release_contract(tmp_path)
    for name in _CURRENT_RANKMAP_RELEASE_MEMBERS:
        contract["member_protection"][name] = {
            "mode": _AUDIT.BKENC2_GZIP_FORMAT,
            "required": True,
        }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    members = _release_members(contract_path)
    members.update(
        {name: b"BKENC2\nrankmap" for name in _CURRENT_RANKMAP_RELEASE_MEMBERS}
    )
    if mutation == "missing":
        members.pop(member)
        expected = f"release_protected_member_missing: {member}"
    elif mutation == "plaintext":
        members[member] = b"rankmap plaintext"
        expected = f"release_member_requires_bkenc2_gzip: {member}"
    else:
        members[member] = b"BKENC1\nlegacy"
        expected = f"release_member_requires_bkenc2_gzip: {member}"

    assert expected in _AUDIT.audit_zip(
        _zip(tmp_path, members),
        release_contract=contract_path,
    )


def test_bkenc_receipt_writer_rejects_authenticated_invalid_gzip(
    tmp_path: Path,
) -> None:
    contract, _receipt, key_file, key, members, _candidate = (
        _bkenc_release_candidate(tmp_path)
    )
    member = "data/raw/tables/Item.txt"
    nonce = b"\x7f" * 12
    invalid_payload = _AUDIT.BKENC2_LENGTH.pack(5) + b"not-gzip"
    members[member] = _AUDIT.BKENC2_MAGIC + nonce + AESGCM(key).encrypt(
        nonce,
        invalid_payload,
        _AUDIT._bkenc2_member_aad(member),
    )
    malformed = _zip(tmp_path, members)

    with pytest.raises(ValueError, match="BKENC2"):
        _AUDIT.write_bkenc_authentication_receipt(
            malformed,
            tmp_path / "malformed-receipt.json",
            contract,
            key_file,
        )


@pytest.mark.parametrize(
    "mutation",
    ["header-only", "forged-header", "truncated", "bit-flip"],
)
def test_bkenc_real_authentication_rejects_corrupt_envelopes(
    tmp_path: Path,
    mutation: str,
) -> None:
    contract_path, receipt, _key_file, key, members, _candidate = (
        _bkenc_release_candidate(tmp_path)
    )
    original = members["data/raw/tables/Item.txt"]
    if mutation == "header-only":
        members["data/raw/tables/Item.txt"] = _AUDIT.BKENC2_MAGIC
    elif mutation == "forged-header":
        members["data/raw/tables/Item.txt"] = (
            _AUDIT.BKENC2_MAGIC
            + (b"\0" * _AUDIT.BKENC1_NONCE_BYTES)
            + (b"\0" * _AUDIT.BKENC1_TAG_BYTES)
        )
    elif mutation == "truncated":
        members["data/raw/tables/Item.txt"] = original[:-1]
    else:
        changed = bytearray(original)
        changed[-1] ^= 0x01
        members["data/raw/tables/Item.txt"] = bytes(changed)
    candidate = _zip(tmp_path, members)

    assert "release_member_bkenc_authentication_invalid" in _AUDIT.audit_zip(
        candidate,
        release_contract=contract_path,
        bkenc_receipt=receipt,
        bkenc_key=key,
    )


def test_bkenc_real_authentication_rejects_wrong_key_and_plaintext(
    tmp_path: Path,
) -> None:
    contract, receipt, _key_file, key, members, candidate = (
        _bkenc_release_candidate(tmp_path)
    )
    assert "release_bkenc_authentication_receipt_invalid" in _AUDIT.audit_zip(
        candidate,
        release_contract=contract,
        bkenc_receipt=receipt,
        bkenc_key=bytes(reversed(key)),
    )

    members["data/raw/tables/Item.txt"] = b"plaintext"
    plaintext_candidate = _zip(tmp_path, members)
    issues = _AUDIT.audit_zip(
        plaintext_candidate,
        release_contract=contract,
        bkenc_receipt=receipt,
        bkenc_key=key,
    )
    assert "release_member_requires_bkenc2_gzip: data/raw/tables/Item.txt" in issues
    assert "release_member_bkenc_authentication_invalid" in issues


def test_bkenc_receipt_rejects_member_swap_rename_and_duplicate(
    tmp_path: Path,
) -> None:
    contract, receipt, _key_file, key, members, _candidate = (
        _bkenc_release_candidate(tmp_path)
    )
    left = "data/raw/tables/Item.txt"
    right = "external_references/Model/StaticData.cs"
    members[left], members[right] = members[right], members[left]
    swapped = _zip(tmp_path, members)
    assert "release_member_bkenc_authentication_invalid" in _AUDIT.audit_zip(
        swapped,
        release_contract=contract,
        bkenc_receipt=receipt,
        bkenc_key=key,
    )

    members[left], members[right] = members[right], members[left]
    renamed_payload = members.pop(left)
    members["data/raw/tables/RenamedItem.txt"] = renamed_payload
    renamed = _zip(tmp_path, members)
    rename_issues = _AUDIT.audit_zip(
        renamed,
        release_contract=contract,
        bkenc_receipt=receipt,
        bkenc_key=key,
    )
    assert f"release_protected_member_missing: {left}" in rename_issues
    assert "release_member_bkenc_authentication_invalid" in rename_issues

    members.pop("data/raw/tables/RenamedItem.txt")
    members[left] = renamed_payload
    duplicate = _zip_rows(
        tmp_path,
        [*members.items(), (left, renamed_payload)],
    )
    assert "release_member_duplicate" in _AUDIT.audit_zip(
        duplicate,
        release_contract=contract,
        bkenc_receipt=receipt,
        bkenc_key=key,
    )


def test_bkenc_receipt_hmac_rejects_joint_receipt_and_manifest_rebind(
    tmp_path: Path,
) -> None:
    contract, receipt, _key_file, key, members, _candidate = (
        _bkenc_release_candidate(tmp_path)
    )
    left = "data/raw/tables/Item.txt"
    right = "external_references/Model/StaticData.cs"
    members[left], members[right] = members[right], members[left]

    forged = json.loads(receipt.read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in forged["entries"]}
    rows[left]["path"], rows[right]["path"] = right, left
    forged["entries"] = sorted(rows.values(), key=lambda row: row["path"])
    forged_receipt = tmp_path / "attacker-rebound-receipt.json"
    forged_receipt.write_text(
        json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    old_hash = hashlib.sha256(receipt.read_bytes()).hexdigest().encode()
    new_hash = hashlib.sha256(forged_receipt.read_bytes()).hexdigest().encode()
    members["BUILD_MANIFEST.txt"] = members["BUILD_MANIFEST.txt"].replace(
        old_hash,
        new_hash,
    )
    rebound = _zip(tmp_path, members)

    assert "release_bkenc_authentication_receipt_invalid" in _AUDIT.audit_zip(
        rebound,
        release_contract=contract,
        bkenc_receipt=forged_receipt,
        bkenc_key=key,
    )


def test_bkenc_receipt_rejects_zip_recompression(
    tmp_path: Path,
) -> None:
    contract, receipt, _key_file, key, members, _candidate = (
        _bkenc_release_candidate(tmp_path)
    )
    repacked = tmp_path / "repacked.zip"
    with zipfile.ZipFile(
        repacked,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)

    assert "release_bkenc_authentication_receipt_mismatch" in _AUDIT.audit_zip(
        repacked,
        release_contract=contract,
        bkenc_receipt=receipt,
        bkenc_key=key,
    )


def test_positive_release_contract_detects_multiencoding_public_text_conflict(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["使用说明.txt"] = "免费体验 3 小时".encode("utf-16")
    members["LICENSE.txt"] = "当前免费试用 12 小时".encode("gb18030")
    path = _zip(tmp_path, members)

    assert "release_public_text_conflict: trial_duration" in _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    )


@pytest.mark.parametrize(
    "twelve_hour_trial_copy",
    (
        "当前免费试用\n12 小时",
        "当前免费试用【12 小时】",
        "当前免费试用　12　小时",
        "当前免费试用12小时",
        "当前免费试用：12小时",
        "当前免费试用\u008512小时",
        "当前免费试用\u202812小时",
        "当前免费试用\u202912小时",
        "当前免费试用\u200b12小时",
        "当前免费试用\u206012小时",
        "当前免费试用\ufeff12小时",
        "当前免费试用\u034f12小时",
        "当前免费试用\x0012小时",
        "当前免费试用\x0112小时",
        "当前免费试用\u115f12小时",
        "当前免费试用\u116012小时",
        "当前免费试用\u316412小时",
        "当前免费试用\uffa012小时",
        "当前免费试用\U0001bca012小时",
        "当前免费试用\U000e010012小时",
    ),
)
def test_release_contract_detects_trial_duration_layout_variants(
    tmp_path: Path,
    twelve_hour_trial_copy: str,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["使用说明.txt"] = "免费体验 3 小时".encode("utf-8")
    members["LICENSE.txt"] = twelve_hour_trial_copy.encode("utf-8")
    path = _zip(tmp_path, members)

    assert "release_public_text_conflict: trial_duration" in _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    )


@pytest.mark.parametrize(
    "visible_separator",
    (
        "\u115e",
        "\U0001bc9f",
        "\U0001f600",
    ),
)
def test_release_contract_preserves_visible_unicode_in_trial_copy(
    tmp_path: Path,
    visible_separator: str,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["使用说明.txt"] = "免费体验 3 小时".encode("utf-8")
    members["LICENSE.txt"] = (
        f"当前免费试用{visible_separator}12小时"
    ).encode("utf-8")
    path = _zip(tmp_path, members)

    assert _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    ) == []


def test_trial_duration_default_ignorable_ranges_match_unicode_15_1() -> None:
    ranges = _AUDIT._TRIAL_DURATION_DEFAULT_IGNORABLE_RANGES
    assert sum(end - start + 1 for start, end in ranges) == 4174

    for code_point in (
        0x115F,
        0x1160,
        0x3164,
        0xFFA0,
        0x1BCA0,
        0x1D173,
        0xE0100,
        0xE0FFF,
    ):
        assert _AUDIT._is_default_ignorable_code_point(chr(code_point))

    for code_point in (
        0x115E,
        0x1161,
        0x3163,
        0x3165,
        0x1BC9F,
        0x1F600,
    ):
        assert not _AUDIT._is_default_ignorable_code_point(chr(code_point))


def test_tracked_release_contract_matches_fixed_inputs_and_package_data_policy(
    tmp_path: Path,
) -> None:
    contract_path = ROOT / "scripts" / "release_zip_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    dependencies = contract["runtime_dependencies"]
    dependency_rows = "".join(
        f"{name}=={dependencies[name]}\n" for name in sorted(dependencies)
    ).encode()
    fixed = contract["fixed_manifest_values"]
    assert contract["contract_version"] == 26
    fingerprint_member = "scripts/package_data_fingerprints.json"
    fingerprint_source = ROOT / fingerprint_member
    assert fingerprint_member in contract["required_members"]["common"]
    assert contract["fixed_member_sha256"][fingerprint_member] == hashlib.sha256(
        fingerprint_source.read_bytes()
    ).hexdigest()
    diagnostic_sources = {
        "诊断工具/收集诊断.bat": ROOT / "scripts" / "collect_diagnostics.bat",
        "诊断工具/启动修复.bat": ROOT / "scripts" / "startup_repair.bat",
        "诊断工具/激活网络诊断.bat": ROOT
        / "scripts"
        / "activation_transport_diagnostic.bat",
        "诊断工具/activation_transport_diagnostic.py": ROOT
        / "scripts"
        / "activation_transport_diagnostic.py",
    }
    assert diagnostic_sources.keys() <= set(contract["required_members"]["common"])
    for member, source in diagnostic_sources.items():
        assert contract["fixed_member_sha256"][member] == hashlib.sha256(
            source.read_bytes()
        ).hexdigest()
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert (
        'Copy-Item $transportDiagBat (Join-Path $diagnosticsDir "激活网络诊断.bat") -Force'
        in build
    )
    assert contract["launcher_wrappers"] == {
        "members": list(_AUDIT.LAUNCHER_WRAPPER_MEMBERS),
        "target": "BidKingLive.exe",
        "encoding": "ascii",
        "line_endings": "crlf",
        "sha256": _AUDIT.LAUNCHER_WRAPPER_SHA256,
    }
    assert contract["release_availability"]["policy_census"]["direct_root"][
        "sampleable"
    ] == 112
    assert contract["release_availability"]["policy_census"]["source_map"][
        "sampleable"
    ] == 112
    assert contract["release_availability"]["route_scope"][
        "ranked_production_unsupported_count"
    ] == 0
    assert "activation_service_endpoints.json" in contract["required_members"][
        "common"
    ]
    assert "ActivationServiceEndpointsSHA256" in contract[
        "required_manifest_fields"
    ]
    assert {
        "integrity/local_integrity_manifest.json",
        "integrity/local_integrity_manifest.sig",
    } <= set(contract["required_members"]["encrypted"])
    local_integrity_contract = json.loads(
        (ROOT / "scripts" / "local_integrity_contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert {"path": "scripts", "role": "entry"} in local_integrity_contract[
        "trees"
    ]
    assert {
        "LocalIntegrityPublicKeySHA256",
        "LocalIntegrityManifestSHA256",
        "LocalIntegrityEntryCount",
        "LocalIntegrityVerified",
    } <= set(contract["required_manifest_fields"])
    assert fixed["LocalIntegrityVerified"] == "True"
    assert contract["archive_limits"] == _AUDIT.RELEASE_ARCHIVE_LIMITS
    assert contract["runtime_inventory"] == {
        "approval_required": True,
        "approval_schema": _AUDIT.RUNTIME_INVENTORY_APPROVAL_SCHEMA,
        "prefix": _AUDIT.RUNTIME_INVENTORY_PREFIX,
        "receipt_required": True,
        "receipt_schema": _AUDIT.RUNTIME_INVENTORY_RECEIPT_SCHEMA,
    }
    assert contract["bkenc_authentication"] == {
        "envelope_aad": _AUDIT.BKENC_AUTHENTICATION_AAD_SCHEME,
        "receipt_required": True,
        "receipt_schema": _AUDIT.BKENC_AUTHENTICATION_RECEIPT_SCHEMA,
    }
    assert "RuntimeInventoryApprovalSHA256" in contract["required_manifest_fields"]
    assert "RuntimeInventoryReceiptSHA256" in contract["required_manifest_fields"]
    assert (
        "BkencAuthenticationReceiptSHA256"
        in contract["required_manifest_fields"]
    )
    assert contract["native_binary"] == {
        "contract_source": "scripts/release_native_binary_contract.json",
        "distribution": "unsigned",
        "receipt_member": "integrity/native_binary_receipt.json",
        "receipt_schema": "bidking_native_binary_receipt_v2",
    }
    assert "integrity/native_signing_policy.json" not in contract["required_members"][
        "encrypted"
    ]
    assert "NativeBinaryContractSHA256" in contract["required_manifest_fields"]
    assert "NativeBinaryDistribution" in contract["required_manifest_fields"]
    assert "NativeSigningPolicySHA256" not in contract["required_manifest_fields"]
    assert "NativeBinaryReceiptSHA256" in contract["required_manifest_fields"]
    assert "LauncherSHA256" not in fixed
    assert (
        hashlib.sha256(dependency_rows).hexdigest()
        == fixed["RuntimeDependencyInventorySHA256"]
    )
    approval_path = ROOT / "scripts" / "runtime_python_inventory_manifest.json"
    approval_raw = approval_path.read_bytes()
    approval = json.loads(approval_raw.decode("utf-8"))
    assert _AUDIT._runtime_inventory_approval_sha256(approval) == fixed[
        "RuntimeInventoryApprovalSHA256"
    ]
    assert approval == {
        "approval_version": 1,
        "directory_count": 217,
        "file_count": 2485,
        "inventory_sha256": (
            "3697aa54b4dd4d0bb442d7e7156a89d49dae20a2e940828c758b2c0e18a5b82e"
        ),
        "prefix": _AUDIT.RUNTIME_INVENTORY_PREFIX,
        "schema": _AUDIT.RUNTIME_INVENTORY_APPROVAL_SCHEMA,
        "total_bytes": 106141084,
    }

    static_data = _copy_only_fixed_input(
        tmp_path,
        (
            "external_references/AuctionAnalyzer4.13.3/_decompiled/"
            "MapBidCalculator/MapBidCalculator/Models/StaticData.cs"
        ),
        fixed["StaticDataSourceSHA256"],
    )
    assert (
        hashlib.sha256(static_data.read_bytes()).hexdigest()
        == fixed["StaticDataSourceSHA256"]
    )

    package_data = json.loads(
        (ROOT / "scripts" / "package_data_manifest.json").read_text(encoding="utf-8")
    )["files"]
    protection = contract["member_protection"]
    assert {
        name: protection[name] for name in _CURRENT_RANKMAP_RELEASE_MEMBERS
    } == {
        name: {"mode": _AUDIT.BKENC2_GZIP_FORMAT, "required": True}
        for name in _CURRENT_RANKMAP_RELEASE_MEMBERS
    }
    assert set(package_data) <= set(protection)
    assert all(protection[name]["required"] is True for name in package_data)
    assert set(_MOAT._contract_moat_members(contract_path)) | {
        _MOAT._UPLOAD_CONFIG_REL
    } == {
        name
        for name, rule in protection.items()
        if rule["mode"] in {"bkenc1", _AUDIT.BKENC2_GZIP_FORMAT}
    }
    assert protection[_MOAT._UPLOAD_CONFIG_REL]["mode"] == "bkenc1"
    assert all(
        protection[name]["mode"] == _AUDIT.BKENC2_GZIP_FORMAT
        for name in _MOAT._MOAT_RELS
    )


def test_native_inventory_count_contract_consumers_remain_exact() -> None:
    contract_path = ROOT / "scripts" / "release_zip_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract["fixed_manifest_values"]["NativeBinaryCount"] == "119"
    build_source = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    audit_source = (ROOT / "scripts" / "audit_release_zip.py").read_text(
        encoding="utf-8"
    )
    assert "protected_cython_module_count -ne 112" in build_source
    assert "$NativeBinaryCount -ne 119" in build_source
    assert "119个unsigned一方native binary" in build_source
    assert 'fields.get("nativebinarycount", "") != "119"' in audit_source


def test_rankmap_release_protection_contract_is_complete_and_builder_driven() -> None:
    contract_path = ROOT / "scripts" / "release_zip_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    protection = contract["member_protection"]
    package_data = json.loads(
        (ROOT / "scripts" / "package_data_manifest.json").read_text(
            encoding="utf-8"
        )
    )["files"]

    assert set(package_data) <= set(protection)
    assert {
        name: protection[name] for name in _CURRENT_RANKMAP_RELEASE_MEMBERS
    } == {
        name: {"mode": _AUDIT.BKENC2_GZIP_FORMAT, "required": True}
        for name in _CURRENT_RANKMAP_RELEASE_MEMBERS
    }
    assert set(_CURRENT_RANKMAP_RELEASE_MEMBERS) <= set(_MOAT._MOAT_RELS)
    assert _MOAT._contract_moat_members(contract_path) == sorted(
        _MOAT._MOAT_RELS
    )
    build = _BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    assert '--release-contract $ReleaseZipContractPath' in build
    assert '$encMoat = Join-Path $Repo "scripts\\encrypt_moat_files.py"' in build


def test_moat_writer_binds_every_contract_member_to_canonical_path(
    tmp_path: Path,
) -> None:
    from bidking_lab.secure.data_crypt import decrypt_bytes

    stage = tmp_path / "stage"
    plaintext: dict[str, bytes] = {}
    for index, member in enumerate(_MOAT._MOAT_RELS, start=1):
        payload = f"synthetic moat member {index}".encode()
        path = stage / Path(member)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        plaintext[member] = payload
    key = bytes(range(32))
    encoded_key = base64.b64encode(key).decode("ascii")
    result = subprocess.run(
        [
            sys.executable,
            str(_MOAT_SCRIPT),
            "--stage",
            str(stage),
            "--release-contract",
            str(ROOT / "scripts" / "release_zip_contract.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "BIDKING_DATA_KEY": encoded_key, "PYTHONUTF8": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert encoded_key not in result.stdout + result.stderr

    for member, expected in plaintext.items():
        blob = (stage / Path(member)).read_bytes()
        assert blob.startswith(_AUDIT.BKENC2_MAGIC)
        assert decrypt_bytes(blob, key, member_path=member) == expected

    left, right = _MOAT._MOAT_RELS[:2]
    with pytest.raises(InvalidTag):
        decrypt_bytes(
            (stage / Path(left)).read_bytes(),
            key,
            member_path=right,
        )


def test_release_contract_rejects_ambiguous_archive_and_runtime_receipt_policy(
    tmp_path: Path,
) -> None:
    _base_path, base = _release_contract(tmp_path)
    variants: list[dict[str, object]] = []

    missing_limits = json.loads(json.dumps(base))
    missing_limits.pop("archive_limits")
    variants.append(missing_limits)

    bool_entry_limit = json.loads(json.dumps(base))
    bool_entry_limit["archive_limits"]["max_entries"] = True
    variants.append(bool_entry_limit)

    bool_compression_method = json.loads(json.dumps(base))
    bool_compression_method["archive_limits"]["allowed_compression_methods"] = [
        False,
        zipfile.ZIP_DEFLATED,
    ]
    variants.append(bool_compression_method)

    invalid_runtime_schema = json.loads(json.dumps(base))
    invalid_runtime_schema["runtime_inventory"]["receipt_schema"] = "untrusted"
    variants.append(invalid_runtime_schema)

    invalid_approval_schema = json.loads(json.dumps(base))
    invalid_approval_schema["runtime_inventory"]["approval_schema"] = "untrusted"
    variants.append(invalid_approval_schema)

    non_boolean_runtime_requirement = json.loads(json.dumps(base))
    non_boolean_runtime_requirement["runtime_inventory"]["receipt_required"] = 1
    variants.append(non_boolean_runtime_requirement)

    non_boolean_approval_requirement = json.loads(json.dumps(base))
    non_boolean_approval_requirement["runtime_inventory"]["approval_required"] = 1
    variants.append(non_boolean_approval_requirement)

    missing_bkenc_authentication = json.loads(json.dumps(base))
    missing_bkenc_authentication.pop("bkenc_authentication")
    variants.append(missing_bkenc_authentication)

    invalid_bkenc_schema = json.loads(json.dumps(base))
    invalid_bkenc_schema["bkenc_authentication"]["receipt_schema"] = "untrusted"
    variants.append(invalid_bkenc_schema)

    non_boolean_bkenc_requirement = json.loads(json.dumps(base))
    non_boolean_bkenc_requirement["bkenc_authentication"]["receipt_required"] = 1
    variants.append(non_boolean_bkenc_requirement)

    empty_normalized_trial_term = json.loads(json.dumps(base))
    empty_normalized_trial_term["mutually_exclusive_text_terms"][0]["right"] = [
        "【 】"
    ]
    variants.append(empty_normalized_trial_term)

    empty_unicode_format_trial_term = json.loads(json.dumps(base))
    empty_unicode_format_trial_term["mutually_exclusive_text_terms"][0]["right"] = [
        "\u200b\u2060\ufeff\u034f\x00"
    ]
    variants.append(empty_unicode_format_trial_term)

    for index, variant in enumerate(variants):
        contract_path = tmp_path / f"invalid-contract-{index}.json"
        contract_path.write_text(
            json.dumps(variant, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        candidate_root = tmp_path / f"candidate-{index}"
        candidate_root.mkdir()
        candidate = _zip(candidate_root, _release_members(contract_path))
        assert _AUDIT.audit_zip(
            candidate,
            release_contract=contract_path,
        ) == ["release_contract_invalid"]


def test_release_contract_trial_duration_ignores_pet_quota_copy() -> None:
    contract = json.loads(
        (ROOT / "scripts" / "release_zip_contract.json").read_text(encoding="utf-8")
    )
    duration = next(
        item
        for item in contract["mutually_exclusive_text_terms"]
        if item["label"] == "trial_duration"
    )
    assert duration["left"] == [
        "试用 12 小时",
        "试用为【12 小时】",
        "免费体验 12 小时",
        "公测服务器临时提供/刷新 12 小时",
    ]
    assert duration["right"] == [
        "试用 3 小时",
        "试用为【3 小时】",
        "免费体验 3 小时",
    ]
    public_copy = (
        (ROOT / "scripts" / "pkg_assets" / "使用说明.txt").read_text(
            encoding="utf-8-sig"
        )
        + (ROOT / "LICENSE").read_text(encoding="utf-8-sig")
        + (ROOT / "scripts" / "build_live_portable.ps1").read_text(
            encoding="utf-8-sig"
        )
    )
    assert any(term in public_copy for term in duration["left"])
    assert not any(term in public_copy for term in duration["right"])
    assert "30 天 12 小时" in public_copy


def test_release_contract_accepts_three_hour_trial_with_twelve_hour_pet_quota(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["使用说明.txt"] = (
        "软件标准新设备试用为【3 小时】；"
        "猫猫：免费 10 分钟 ｜ 30 天 12 小时。"
    ).encode("utf-8")
    path = _zip(tmp_path, members)

    assert _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    ) == []


def test_trial_duration_normalization_preserves_public_member_boundaries(
    tmp_path: Path,
) -> None:
    contract_path, _contract = _release_contract(tmp_path)
    members = _release_members(contract_path)
    members["使用说明.txt"] = (
        "免费体验 3 小时；本段末尾单独提到试用"
    ).encode("utf-8")
    members["LICENSE.txt"] = (
        "12 小时是另一个公开权益中的独立时长。"
        "猫猫：免费 10 分钟 ｜ 30 天 12 小时。"
    ).encode("utf-8")
    path = _zip(tmp_path, members)

    assert _AUDIT.audit_zip(
        path,
        release_contract=contract_path,
    ) == []


def test_network_diagnostic_zip_counts_forbidden_text_in_project_sources(
    tmp_path: Path,
) -> None:
    forbidden = "点此输入/购买 token"
    prices = (
        "¥12.88",
        "¥11.11",
        "¥28.88",
        "¥68.88",
        "¥19.99",
        "¥39.99",
        "¥89.99",
    )
    path = _zip(
        tmp_path,
        {
            "BUILD_MANIFEST.txt": (
                b"PackageVersion: v0.3.0-hotfix1\n"
                b"ProductProfile: network-diagnostic\n"
            ),
            "hero_ref/tools/ahmad_tk_overlay.py": (
                (
                    "\n".join(
                        f'P{index} = "{price}"'
                        for index, price in enumerate(prices)
                    )
                    + f'\nA = "{forbidden}"\nB = "{forbidden}"\n'
                ).encode()
            ),
            "hero_ref/tools/product_profile.py": (
                f'STANDARD = "{forbidden}"\n'.encode()
            ),
        },
    )

    assert _AUDIT.audit_zip(
        path,
        product_profile="network-diagnostic",
    ) == [
        *[
            "network_diagnostic_forbidden_text: hero_ref/tools/ahmad_tk_overlay.py"
            for _ in prices
        ],
        "network_diagnostic_forbidden_text: hero_ref/tools/ahmad_tk_overlay.py",
        "network_diagnostic_forbidden_text: hero_ref/tools/ahmad_tk_overlay.py",
        "network_diagnostic_forbidden_text: hero_ref/tools/product_profile.py",
    ]


@pytest.mark.parametrize(
    "suffix",
    sorted(_AUDIT.DISTRIBUTION_TEXT_SUFFIXES),
)
def test_network_diagnostic_zip_scans_every_distribution_text_suffix(
    tmp_path: Path,
    suffix: str,
) -> None:
    member = f"surface/probe{suffix}"
    path = _zip(
        tmp_path,
        {
            "BUILD_MANIFEST.txt": (
                b"ProductProfile: network-diagnostic\n"
            ),
            member: "90/365 天有效期内可用".encode(),
        },
    )

    assert _AUDIT.audit_zip(path) == [
        f"network_diagnostic_forbidden_text: {member}"
    ]


def test_network_diagnostic_zip_allows_runtime_composition(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "BUILD_MANIFEST.txt": (
                b"ProductProfile: network-diagnostic\n"
            ),
            "hero_ref/tools/product_profile.py": (
                'STANDARD = "".join(("点此输入/", "购买 token"))\n'.encode()
            ),
            "hero_ref/tools/ahmad_tk_overlay.py": (
                b"from product_profile import STANDARD\n"
            ),
        },
    )

    assert _AUDIT.audit_zip(
        path,
        product_profile="network-diagnostic",
    ) == []


def test_current_packaged_profile_sources_pass_network_diagnostic_text_gate(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "BUILD_MANIFEST.txt": (
                b"ProductProfile: network-diagnostic\n"
            ),
            "hero_ref/tools/product_profile.py": (
                ROOT / "hero_ref" / "tools" / "product_profile.py"
            ).read_bytes(),
            "hero_ref/tools/ahmad_tk_overlay.py": (
                ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
            ).read_bytes(),
        },
    )

    assert _AUDIT.audit_zip(
        path,
        product_profile="network-diagnostic",
    ) == []


def test_standard_zip_keeps_public_commercial_text(tmp_path: Path) -> None:
    path = _zip(
        tmp_path,
        {
            "BUILD_MANIFEST.txt": b"ProductProfile: standard\n",
            "hero_ref/tools/ahmad_tk_overlay.py": (
                "PRICE = '¥12.88'\nACTION = '点此输入/购买 token'\n".encode()
            ),
        },
    )

    assert _AUDIT.audit_zip(path, product_profile="standard") == []


def test_expected_product_profile_must_match_manifest(tmp_path: Path) -> None:
    path = _zip(
        tmp_path,
        {
            "BUILD_MANIFEST.txt": b"ProductProfile: standard\n",
            "hero_ref/tools/panel.py": b"print('ok')\n",
        },
    )

    assert _AUDIT.audit_zip(
        path,
        product_profile="network-diagnostic",
    ) == ["product_profile_manifest_mismatch"]


def test_plaintext_wss_product_modules_fail_with_stable_label(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "scripts/run_wss_live_monitor.py": b"private implementation",
            "scripts/mitm_bidking_wss_addon.py": b"private implementation",
            "scripts/bidking_wss_capture_core.py": b"private implementation",
            "scripts/_local_compat_core.py": b"private implementation",
        },
    )

    assert _AUDIT.audit_zip(path) == ["plaintext_wss_module"] * 4


def test_native_wss_addon_bytecode_fails_with_stable_label(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "scripts/_local_compat_addon.cp313-win_amd64.pyd": b"binary",
        },
    )

    assert _AUDIT.audit_zip(path) == ["incompatible_wss_addon_bytecode"]


def test_reversible_wss_addon_payload_and_missing_loader_fail_closed(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "scripts/local_compat_addon.py": (
                b"import base64, gzip\n"
                b"source = gzip.decompress(base64.b64decode('payload'))\n"
            ),
        },
    )

    assert _AUDIT.audit_zip(path) == [
        "reversible_wss_addon_payload",
        "protected_wss_addon_loader_missing",
    ]


def test_protected_wss_addon_duplicate_loader_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "scripts/local_compat_addon.py",
            b"from _local_compat_addon_loader import load_addons\n",
        )
        archive.writestr("scripts/_local_compat_addon_loader.pyd", b"first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("scripts/_local_compat_addon_loader.pyd", b"second")

    assert _AUDIT.audit_zip(path) == ["protected_wss_addon_loader_missing"]


def test_bundled_npcap_installer_fails_with_stable_label(tmp_path: Path) -> None:
    path = _zip(tmp_path, {"vendor/npcap-installer.exe": b"unused driver setup"})

    assert _AUDIT.audit_zip(path) == ["bundled_npcap_installer"]


def test_plaintext_launcher_components_and_technical_shell_fail(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "scripts/local_compat_launcher.py": b"private implementation",
            "scripts/live_process_discovery.ps1": b"private implementation",
            "scripts/start_live_npcap_overlay.ps1": (
                b"run_wss_live_monitor --internal"
            ),
            "scripts/collect_diagnostics.ps1": b"Npcap internal details",
        },
    )

    assert _AUDIT.audit_zip(path) == [
        "plaintext_launcher_module",
        "plaintext_launcher_module",
        "public_launcher_technical_detail",
        "public_diagnostics_technical_detail",
    ]


def test_project_pycache_and_bytecode_fail(tmp_path: Path) -> None:
    path = _zip(
        tmp_path,
        {
            "src/bidking_lab/__pycache__/monitor.cpython-313.pyc": b"bytecode",
            "hero_ref/tools/overlay.pyc": b"bytecode",
            "scripts/helper.pyo": b"bytecode",
        },
    )

    issues = _AUDIT.audit_zip(path)

    assert (
        len([issue for issue in issues if issue.startswith("project_bytecode:")]) == 3
    )


def test_embedded_workspace_and_windows_user_paths_fail_without_echoing_value(
    tmp_path: Path,
) -> None:
    private = r"C:\private-build\bidking-lab"
    path = _zip(
        tmp_path,
        {
            "hero_ref/tools/a.pyd": f"compiled from {private}\\a.py".encode(),
            "BidKingLive.exe": r"C:\Users\example\app.py".encode("utf-16le"),
        },
    )

    issues = _AUDIT.audit_zip(path, forbidden_paths=(private,))

    assert {issue.split(":", 1)[0] for issue in issues} == {"private_build_path"}
    assert all(private not in issue and "release-user" not in issue for issue in issues)


def test_redaction_patterns_and_safe_user_placeholders_are_not_private_paths(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "scripts/collect_diagnostics.ps1": (
                rb"'(?i)\b[A-Z]:\\Users\\[^\\/\s]+'"
                b"\nreplacement=C:\\Users\\example\n"
            ),
            "src/bidking_lab/secure/diagnostic_redaction.py": (
                rb'r"(?i)\b[A-Z]:\\Users\\[^\\/\s]+"'
                b'\nreplacement = r"C:\\Users\\<user>"\n'
                b'build = r"C:\\Users\\<build-user>"\n'
            ),
        },
    )

    assert _AUDIT.audit_zip(path) == []


def test_utf8_unicode_windows_user_path_fails_without_echoing_value(
    tmp_path: Path,
) -> None:
    canary = "CANARY_用户_7F9A"
    payload = rf"C:\Users\{canary}\private.py".encode("utf-8")
    path = _zip(tmp_path, {"src/bidking_lab/unicode.bin": payload})

    issues = _AUDIT.audit_zip(path)

    assert issues == ["private_build_path: src/bidking_lab/unicode.bin"]
    assert all(canary not in issue for issue in issues)


def test_utf16le_windows_user_path_is_detected_at_both_byte_alignments(
    tmp_path: Path,
) -> None:
    raw = r"C:\Users\example\private.py".encode("utf-16le")
    even_root = tmp_path / "even"
    odd_root = tmp_path / "odd"
    even_root.mkdir()
    odd_root.mkdir()
    even = _zip(even_root, {"src/bidking_lab/even.bin": raw})
    odd = _zip(odd_root, {"src/bidking_lab/odd.bin": b"X" + raw})

    assert _AUDIT.audit_zip(even) == ["private_build_path: src/bidking_lab/even.bin"]
    assert _AUDIT.audit_zip(odd) == ["private_build_path: src/bidking_lab/odd.bin"]


def test_controlled_rust_paths_match_on_windows_powershell_51_and_pwsh7() -> None:
    temp_case_body = r'''
[Environment]::SetEnvironmentVariable('USERPROFILE', 'C:\bk-probe-profile', 'Process')
[Environment]::SetEnvironmentVariable('CARGO_HOME', $null, 'Process')
[Environment]::SetEnvironmentVariable('RUSTUP_HOME', $null, 'Process')
try {
    $mappings = @(Get-ControlledRustPathMappings -RepositoryRoot 'C:\bk-probe-repo')
    $tempMappings = @($mappings | Where-Object PublicPrefix -CEQ '<build-temp>')
    $actual = ConvertTo-ControlledWindowsAbsolutePath ([IO.Path]::GetTempPath())
    $actualBound = @($tempMappings | Where-Object {
        [string]::Equals($_.Source, $actual, [StringComparison]::OrdinalIgnoreCase)
    }).Count
    [pscustomobject]@{
        Pass=$true; Count=$tempMappings.Count; ActualBound=$actualBound
    } | ConvertTo-Json -Compress
} catch {
    [pscustomobject]@{ Pass=$false; Count=0; ActualBound=0 } |
        ConvertTo-Json -Compress
}
'''
    temp_cases = {
        "temp-empty": (None, r"C:\bk-probe-temp-a", True, 1),
        "tmp-empty": (r"C:\bk-probe-temp-a", None, True, 1),
        "same": (r"C:\bk-probe-temp-a", r"C:\bk-probe-temp-a", True, 1),
        "different": (
            r"C:\bk-probe-temp-a",
            r"C:\bk-probe-temp-b",
            True,
            2,
        ),
        "relative-temp": ("relative-temp", r"C:\bk-probe-temp-b", False, 0),
        "malformed-tmp": (r"C:\bk-probe-temp-a", "C:drive-relative", False, 0),
    }
    path_and_restore_body = r'''
$validPaths = @('C:\repo', 'D:/repo/path', '\\server\share\repo', '//server/share/repo')
$invalidPaths = @(
    'repo', 'C:repo', '\repo', '/repo', '\\?\C:\repo', '\\.\C:\repo',
    '\\server', 'C:\repo\..\other', 'C:\repo=name', 'C:\repo.', 'C:\repo ',
    'C:\repo|name', 'C:\CON', 'C:\repo\NUL.txt'
)
$validAccepted = 0
foreach ($candidate in $validPaths) {
    try { $null = ConvertTo-ControlledWindowsAbsolutePath $candidate; $validAccepted++ } catch {}
}
$invalidRejected = 0
foreach ($candidate in $invalidPaths) {
    try { $null = ConvertTo-ControlledWindowsAbsolutePath $candidate } catch { $invalidRejected++ }
}
[Environment]::SetEnvironmentVariable('CARGO_ENCODED_RUSTFLAGS', 'previous-public', 'Process')
[Environment]::SetEnvironmentVariable('CARGO_ENCODED_RUSTFLAGS', 'injected-public', 'Process')
Restore-ControlledCargoEncodedRustFlags -WasPresent $true -PreviousValue 'previous-public'
$restorePresent = (
    [Environment]::GetEnvironmentVariable('CARGO_ENCODED_RUSTFLAGS') -ceq 'previous-public'
)
[Environment]::SetEnvironmentVariable('CARGO_ENCODED_RUSTFLAGS', 'injected-public', 'Process')
Restore-ControlledCargoEncodedRustFlags -WasPresent $false -PreviousValue $null
$restoreAbsent = (
    $null -eq [Environment]::GetEnvironmentVariable('CARGO_ENCODED_RUSTFLAGS')
)
[pscustomobject]@{
    Edition=$PSVersionTable.PSEdition
    Version=$PSVersionTable.PSVersion.ToString()
    ValidAccepted=$validAccepted
    InvalidRejected=$invalidRejected
    RestorePresent=$restorePresent
    RestoreAbsent=$restoreAbsent
} | ConvertTo-Json -Depth 5 -Compress
'''
    comparable_results = []
    for executable, expected_edition, expected_major in _powershell_runtime_hosts():
        case_results = {}
        for name, (temp, tmp, should_pass, expected_count) in temp_cases.items():
            case_result = _run_builder_function_probe(
                executable,
                temp_case_body,
                environment={"TEMP": temp, "TMP": tmp},
            )
            assert isinstance(case_result, dict)
            assert case_result == {
                "Pass": should_pass,
                "Count": expected_count,
                "ActualBound": 1 if should_pass else 0,
            }
            case_results[name] = case_result
        result = _run_builder_function_probe(executable, path_and_restore_body)
        assert isinstance(result, dict)
        assert result["Edition"] == expected_edition
        assert int(str(result["Version"]).split(".", 1)[0]) == expected_major
        assert result["ValidAccepted"] == 4
        assert result["InvalidRejected"] == 14
        assert result["RestorePresent"] is True
        assert result["RestoreAbsent"] is True
        comparable_results.append(
            {
                "Cases": case_results,
                "ValidAccepted": result["ValidAccepted"],
                "InvalidRejected": result["InvalidRejected"],
                "RestorePresent": result["RestorePresent"],
                "RestoreAbsent": result["RestoreAbsent"],
            }
        )
    assert comparable_results[0] == comparable_results[1]


def test_real_rustc_requires_actual_temp_remap_for_privacy_gate(tmp_path: Path) -> None:
    rustc = shutil.which("rustc.exe")
    if rustc is None:
        pytest.skip("rustc.exe unavailable for real native privacy A/B")
    build_temp = str(tmp_path)
    host = _powershell_runtime_hosts()[1][0]
    mapping_body = r'''
$mappings = @(Get-ControlledRustPathMappings -RepositoryRoot $env:BIDKING_TEST_REPO)
@($mappings | ForEach-Object {
    [pscustomobject]@{ Source=[string]$_.Source; PublicPrefix=[string]$_.PublicPrefix }
}) | ConvertTo-Json -Compress
'''
    mapping_payload = _run_builder_function_probe(
        host,
        mapping_body,
        environment={
            "BIDKING_TEST_REPO": str(ROOT),
            "USERPROFILE": r"C:\bk-probe-profile",
            "CARGO_HOME": os.environ.get(
                "CARGO_HOME",
                str(Path.home() / ".cargo"),
            ),
            "RUSTUP_HOME": os.environ.get(
                "RUSTUP_HOME",
                str(Path.home() / ".rustup"),
            ),
            "TEMP": "",
            "TMP": build_temp,
        },
    )
    assert isinstance(mapping_payload, list)
    mappings = [
        (str(row["Source"]), str(row["PublicPrefix"]))
        for row in mapping_payload
    ]
    assert sum(prefix == "<build-temp>" for _source, prefix in mappings) == 1

    source = tmp_path / "main.rs"
    source.write_text(
        'static SOURCE_FILE: &str = file!();\n'
        'fn main() { println!("{}", SOURCE_FILE); }\n',
        encoding="utf-8",
    )
    red = tmp_path / "omitted-temp.exe"
    green = tmp_path / "complete-remap.exe"
    base = [rustc, str(source), "-Cstrip=debuginfo"]
    omitted_flags = [
        f"--remap-path-prefix={mapping_source}={prefix}"
        for mapping_source, prefix in mappings
        if prefix != "<build-temp>"
    ]
    complete_flags = [
        f"--remap-path-prefix={mapping_source}={prefix}"
        for mapping_source, prefix in mappings
    ]
    compile_environment = dict(os.environ)
    compile_environment["TEMP"] = ""
    compile_environment["TMP"] = build_temp
    for artifact, flags in ((red, omitted_flags), (green, complete_flags)):
        completed = subprocess.run(
            [*base, "-o", str(artifact), *flags],
            cwd=ROOT,
            env=compile_environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, "real rustc probe failed; output redacted"

    assert build_temp.encode("ascii") in red.read_bytes()
    forbidden_sources = tuple(source_path for source_path, _prefix in mappings)
    assert _AUDIT.audit_private_build_artifact(
        red,
        forbidden_paths=forbidden_sources,
    ) == ["private_build_path"]
    assert build_temp.encode("ascii") not in green.read_bytes()
    assert _AUDIT.audit_private_build_artifact(
        green,
        forbidden_paths=forbidden_sources,
    ) == []


def test_native_artifact_privacy_gate_rejects_private_paths_without_echo(
    tmp_path: Path,
) -> None:
    canary = "CANARY_NATIVE_USER_7F9A"
    private_root = tmp_path / "private-source"
    red = tmp_path / "red.exe"
    red.write_bytes(
        rf"C:\Users\{canary}\.cargo\registry\src\crate\src\lib.rs".encode()
        + b"\x00"
        + str(private_root / "src" / "main.rs").encode("utf-16le")
    )

    assert _AUDIT.audit_private_build_artifact(
        red,
        forbidden_paths=(str(private_root),),
    ) == ["private_build_path"]
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            "--audit-private-build-artifact",
            str(red),
            "--forbid-path",
            str(private_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert completed.stdout.strip() == "[FAIL] private_build_path"
    assert canary not in completed.stdout + completed.stderr
    assert str(private_root) not in completed.stdout + completed.stderr


def test_native_artifact_privacy_gate_accepts_public_remaps_and_near_paths(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "green.exe"
    artifact.write_bytes(
        b"<cargo-registry>/curve25519-dalek/src/window.rs\n"
        b"<build-user>/source/main.rs\n"
        rb"C:\Userz\near-name\main.rs"
    )

    assert _AUDIT.audit_private_build_artifact(
        artifact,
        forbidden_paths=(r"C:\private-source",),
    ) == []
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            "--audit-private-build-artifact",
            str(artifact),
            "--forbid-path",
            r"C:\private-source",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "[OK] native artifact privacy gate"


def test_native_artifact_privacy_gate_rejects_mixed_audit_modes(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.exe"
    artifact.write_bytes(b"public")
    other_zip = tmp_path / "other.zip"

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SCRIPT),
            str(other_zip),
            "--audit-private-build-artifact",
            str(artifact),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert completed.stdout.strip() == (
        "[FAIL] native artifact privacy mode only accepts --forbid-path"
    )


def test_exact_pinned_runtime_may_contain_upstream_builder_path(
    tmp_path: Path,
) -> None:
    prefix = "vendor/mitmproxy"
    members = {
        f"{prefix}/mitmdump.exe": b"official runtime",
        f"{prefix}/_internal/upstream.pyd": (
            rb"compiled from C:\Users\example\project"
        ),
    }
    path = _zip(tmp_path, members)
    manifest = _asset_manifest(tmp_path, prefix, members)

    assert (
        _AUDIT.audit_zip(
            path,
            trusted_assets=((prefix, manifest),),
        )
        == []
    )


def test_pinned_runtime_never_bypasses_explicit_local_path(
    tmp_path: Path,
) -> None:
    prefix = "vendor/mitmproxy"
    private = r"C:\Users\example"
    members = {
        f"{prefix}/mitmdump.exe": b"official runtime",
        f"{prefix}/_internal/upstream.pyd": (
            f"compiled from {private}\\project".encode()
        ),
    }
    path = _zip(tmp_path, members)
    manifest = _asset_manifest(tmp_path, prefix, members)

    assert _AUDIT.audit_zip(
        path,
        forbidden_paths=(private,),
        trusted_assets=((prefix, manifest),),
    ) == ["private_build_path: vendor/mitmproxy/_internal/upstream.pyd"]


def test_changed_pinned_runtime_fails_closed(tmp_path: Path) -> None:
    prefix = "vendor/mitmproxy"
    reviewed = {
        f"{prefix}/mitmdump.exe": b"official runtime",
        f"{prefix}/_internal/upstream.pyd": (
            rb"compiled from C:\Users\example\project"
        ),
    }
    manifest = _asset_manifest(tmp_path, prefix, reviewed)
    changed = dict(reviewed)
    changed[f"{prefix}/_internal/upstream.pyd"] += b" changed"
    path = _zip(tmp_path, changed)

    assert _AUDIT.audit_zip(
        path,
        trusted_assets=((prefix, manifest),),
    ) == [
        "trusted_asset_inventory_mismatch: vendor/mitmproxy",
        "private_build_path: vendor/mitmproxy/_internal/upstream.pyd",
    ]


def test_pinned_runtime_directory_symlink_fails_closed(tmp_path: Path) -> None:
    prefix = "vendor/mitmproxy"
    reviewed = {
        f"{prefix}/mitmdump.exe": b"official runtime",
        f"{prefix}/_internal/upstream.pyd": (
            rb"compiled from C:\Users\example\project"
        ),
    }
    manifest = _asset_manifest(tmp_path, prefix, reviewed)
    path = tmp_path / "candidate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in reviewed.items():
            archive.writestr(name, payload)
        symlink = zipfile.ZipInfo(f"{prefix}/link/")
        symlink.create_system = 3
        symlink.external_attr = (0o120777 << 16) | 0x10
        archive.writestr(symlink, b"target")

    assert _AUDIT.audit_zip(
        path,
        trusted_assets=((prefix, manifest),),
    ) == ["release_member_type_invalid"]


def test_unpinned_vendor_binary_keeps_private_path_blocked(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "vendor/other/runtime.pyd": (
                rb"compiled from C:\Users\example\project"
            )
        },
    )

    assert _AUDIT.audit_zip(path) == ["private_build_path: vendor/other/runtime.pyd"]


def test_runtime_bytecode_with_distribution_filename_is_allowed(
    tmp_path: Path,
) -> None:
    path = _zip(
        tmp_path,
        {
            "runtime/python/Lib/site-packages/pkg/__pycache__/mod.cpython-313.pyc": (
                _pyc("runtime/python/Lib/site-packages/pkg/mod.py")
            )
        },
    )

    assert _AUDIT.audit_zip(path) == []


def test_runtime_bytecode_private_filename_fails_without_echoing_path(
    tmp_path: Path,
) -> None:
    canary = "CANARY_USER_7F9A"
    member = "runtime/python/Lib/site-packages/pkg/__pycache__/mod.cpython-313.pyc"
    path = _zip(
        tmp_path,
        {
            member: _pyc(
                rf"C:\Users\{canary}\private-repo\pkg\mod.py",
                "def outer():\n    def inner():\n        return 1\n    return inner\n",
            )
        },
    )

    issues = _AUDIT.audit_zip(path)

    assert issues == [f"runtime_bytecode_private_path: {member}"]
    assert all(canary not in issue for issue in issues)
    assert all("C:\\Users\\" not in issue for issue in issues)


def test_runtime_bytecode_explicit_forbidden_path_fails(tmp_path: Path) -> None:
    member = "runtime/python/Lib/site-packages/pkg/__pycache__/mod.cpython-313.pyc"
    path = _zip(
        tmp_path,
        {member: _pyc(r"D:\release\bidking-lab\pkg\mod.py")},
    )

    assert _AUDIT.audit_zip(
        path,
        forbidden_paths=(r"D:\release\bidking-lab",),
    ) == [f"runtime_bytecode_private_path: {member}"]


def test_malformed_runtime_bytecode_fails_closed(tmp_path: Path) -> None:
    member = "runtime/python/Lib/site-packages/pkg/__pycache__/mod.cpython-313.pyc"
    path = _zip(tmp_path, {member: b"not-a-pyc"})

    assert _AUDIT.audit_zip(path) == [f"runtime_bytecode_unreadable: {member}"]


def test_runtime_bytecode_reserved_header_flags_fail_closed(tmp_path: Path) -> None:
    member = "runtime/python/Lib/site-packages/pkg/__pycache__/mod.cpython-313.pyc"
    path = _zip(
        tmp_path,
        {
            member: _pyc(
                "runtime/python/Lib/site-packages/pkg/mod.py",
                flags=0x80000000,
            )
        },
    )

    assert _AUDIT.audit_zip(path) == [f"runtime_bytecode_unreadable: {member}"]


def test_unsafe_zip_member_path_fails(tmp_path: Path) -> None:
    path = _zip(tmp_path, {"../outside.py": b"bad"})

    assert _AUDIT.audit_zip(path) == ["unsafe_member_path"]


def test_unsafe_member_path_does_not_echo_private_member(tmp_path: Path) -> None:
    canary = "CANARY_USER_7F9A"
    path = _zip(tmp_path, {f"C:/Users/{canary}/private.py": b"bad"})

    issues = _AUDIT.audit_zip(path)

    assert issues == ["unsafe_member_path"]
    assert all(canary not in issue for issue in issues)
    assert all("C:/Users/" not in issue for issue in issues)


def test_build_orders_final_cleanup_zip_audit_and_extracted_smoke() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    runtime_gate = build.index(
        "# --- 6c. synthetic ABI smoke + 真实 release availability gate"
    )
    final_cleanup = build.index("# --- 6d. 最终字节码清理门")
    runtime_approval = build.index("$RuntimeInventoryApprovalPath =")
    runtime_receipt = build.index("--write-runtime-receipt")
    canonical_zip = build.index("--write-canonical-zip")
    bkenc_receipt = build.index("--write-bkenc-receipt")
    zip_audit = build.index("# --- 7a. 冻结构建审计 authority receipt")
    extracted_smoke = build.index("# --- 7b. 解压后成品烟测")
    component_audit = build.index("if ($SplitCompatibilityRuntime)", zip_audit)
    primary_audit = build[zip_audit:component_audit]

    assert (
        runtime_approval
        < runtime_gate
        < final_cleanup
        < runtime_receipt
        < canonical_zip
        < bkenc_receipt
        < zip_audit
        < extracted_smoke
    )
    assert '"hero_ref", "src", "scripts"' in build[final_cleanup:canonical_zip]
    assert "$runtimeCacheDirs" in build[final_cleanup:canonical_zip]
    assert "$remainingRuntimeBytecode" in build[final_cleanup:canonical_zip]
    assert 'Compress-Archive -Path "$Stage\\*"' not in build
    auditor_assignment = build.index(
        '$zipAuditor = Join-Path $Repo "scripts\\audit_release_zip.py"'
    )
    preflight_exit = build.index("if ($PreflightOnly)")
    stage_cleanup = build.index("# --- 1. 清理 + 目录 ---")
    assert preflight_exit < auditor_assignment < stage_cleanup
    assert auditor_assignment < runtime_gate
    assert "$zipAuditor" in build[zip_audit:extracted_smoke]
    assert '"--build-receipt", $BuildAuditReceiptPath' in primary_audit
    assert '"--source-root", $Repo' in primary_audit
    assert '"--bkenc-key-file", $MasterKeyFile' in primary_audit
    assert 'schema = "bidking_release_build_audit_receipt_v5"' in primary_audit
    assert "release_availability = if ($Encrypt)" in primary_audit
    assert "--trusted-asset" not in primary_audit
    assert "--product-profile" not in primary_audit
    assert "--release-contract" not in primary_audit
    assert "--runtime-receipt" not in primary_audit
    assert "--runtime-approval" not in primary_audit
    assert "--bkenc-receipt" not in primary_audit
    assert "--write-runtime-allowlist" not in build
    assert '"scripts/runtime_python_inventory_manifest.json"' in build
    assert (
        "$RuntimeInventoryApprovalSHA256 -cne "
        "$ExpectedRuntimeInventoryApprovalSHA256"
    ) in build
    assert 'SourceCommit = [string]$SourceCommit' in primary_audit
    assert "RuntimeInventoryReceiptSHA256 =" in primary_audit
    assert "BkencAuthenticationReceiptSHA256 =" in primary_audit
    assert "RuntimeInventoryApprovalSHA256 =" in primary_audit
    assert "vendor/mitmproxy" in build[zip_audit:extracted_smoke]
    assert "Expand-Archive" in build[extracted_smoke:]
    assert "CURRENT_AUTHORITY_ABI_SMOKE_OK" in build[extracted_smoke:]
    assert "RUNTIME_GATE_OK" not in build
    assert build.count("RELEASE_AVAILABILITY_OK") == 1
    assert "& $extractedVerifyPy -I -B -c $verifyCode" in build[extracted_smoke:]
    assert "& $extractedVerifyPy -I -B -c $gateCode" in build[extracted_smoke:]


def test_build_runtime_fixtures_use_current_authority_and_keep_unauthorized_root_closed() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    for expected in (
        'MapId = 5604; OriginalPoolId = 2704; ExpectedStatus = "sampleable"',
        'MapId = 2508; OriginalPoolId = 2508; ExpectedStatus = "sampleable"',
        'ExpectedSupportStatus = ""; SupportUnresolvedProbabilityPpm = 0',
        'PackageGeneration = "76b7ca-2026-10-08-723"; ModelScopeCount = 112',
    ):
        assert expected in build
    assert build.count("except DropPoolResolutionError as exc:") == 1
    helper_start = build.index("function New-RuntimeFixtureGateCode")
    helper_end = build.index("$prevEAP2 =", helper_start)
    helper = build[helper_start:helper_end]
    assert "fixture 当前权威合同缺失" in helper
    assert "fixture 当前权威状态未批准" in helper
    assert "fixture 当前权威合同类型无效" in helper
    assert "fixture 当前权威合同未批准" in helper
    assert '$approvedGateSpecs.ContainsKey($mapId)' in helper
    fixture_parse = helper.index("parsed = parse_fatbeans_capture_payload(payload)")
    offline_import = helper.index("from dataclasses import replace")
    offline_mode = helper.index("tables = replace(")
    table_load = helper.index("load_monitor_tables()", offline_mode)
    offline_flag = helper.index(
        "warehouse_live_identity_check=False", table_load
    )
    assert offline_import < offline_mode < table_load < offline_flag < fixture_parse
    assert helper.count("warehouse_live_identity_check=False") == 1
    assert helper.index("runtime fixture map identity mismatch") > fixture_parse
    for forbidden in (
        "approved_authority_pairs",
        "authority_map_id",
        "gate_maps",
        "dataclasses.replace",
        "original incomplete Drop authority did not fail closed",
        "flatten_pool(authority_map.drop_pool_id",
    ):
        assert forbidden not in helper
    assert helper.count("flatten_pool(1066, tables.drops, tables.items)") == 1
    assert helper.count("unauthorized root and leaf did not fail closed") == 1
    assert build.count("assert art.get('map_id') == expected_map_id") == 1
    assert build.count("assert art.get('map_alias_mode') == ''") == 1
    assert build.count("assert art.get('hero') == expected_hero") == 1
    assert build.count("assert art.get('inventory_count') == expected_inventory_count") == 1
    assert build.count("final_decision_value") >= 1
    assert build.count("assert {1086008, 1096009}.issubset(recovered_ids)") == 1
    assert build.count("assert support is None") == 1
    assert "if expected_map_id == 2508:" in helper
    assert "require_current_fixture_authority(tables, expected_map_id" in helper
    assert "degraded_sampleable" not in helper
    assert build.count("New-RuntimeFixtureGateCode -GateSpec $gateSpec") == 2
    assert "运行时 gate 样本缺失" in build
    assert "解压后运行时 gate 样本缺失" in build
    assert "跳过运行时 gate" not in build


def test_build_runtime_fixture_gate_binds_exact_current_authority_status_contract() -> None:
    body = r'''
function New-Spec {
    param($MapId, $OriginalPoolId, $ExpectedStatus, $ExpectedSupportStatus, $Ppm)
    return [PSCustomObject]@{
        MapId = $MapId
        OriginalPoolId = $OriginalPoolId
        ExpectedStatus = $ExpectedStatus
        ExpectedSupportStatus = $ExpectedSupportStatus
        SupportUnresolvedProbabilityPpm = $Ppm
        PackageGeneration = '76b7ca-2026-10-08-723'; ModelScopeCount = 112
        Hero = 'ahmed'
        InventoryCount = $(if ($MapId -eq 2508) { 47 } else { 33 })
        SettlementIncomplete = ($MapId -eq 2508)
    }
}
function Test-Accepted {
    param([object]$Spec)
    try {
        $null = New-RuntimeFixtureGateCode -GateSpec $Spec -GateFixture 'C:\fixture.jsonl'
        return $true
    } catch {
        return $false
    }
}
$missingSupport = [PSCustomObject]@{
    MapId = 2508
    OriginalPoolId = 2508
    ExpectedStatus = 'sampleable'
    SupportUnresolvedProbabilityPpm = 0
    PackageGeneration = '76b7ca-2026-10-08-723'; ModelScopeCount = 112
    Hero = 'ahmed'; InventoryCount = 47; SettlementIncomplete = $true
}
$result = [ordered]@{
    canonical_5604 = Test-Accepted (New-Spec 5604 2704 'sampleable' '' 0)
    canonical_2508 = Test-Accepted (New-Spec 2508 2508 'sampleable' '' 0)
    old_degraded_2508 = Test-Accepted (New-Spec 2508 2508 'degraded_sampleable' 'legacy_incomplete_conditional_known_support' 73)
    status_swap_5604 = Test-Accepted (New-Spec 5604 2704 'degraded_sampleable' 'legacy_incomplete_conditional_known_support' 73)
    pool_swap = Test-Accepted (New-Spec 2508 2704 'degraded_sampleable' 'legacy_incomplete_conditional_known_support' 73)
    support_swap = Test-Accepted (New-Spec 2508 2508 'degraded_sampleable' '' 73)
    ppm_swap = Test-Accepted (New-Spec 2508 2508 'degraded_sampleable' 'legacy_incomplete_conditional_known_support' 0)
    unknown_map = Test-Accepted (New-Spec 9999 2508 'degraded_sampleable' 'legacy_incomplete_conditional_known_support' 73)
    string_map = Test-Accepted (New-Spec '2508' 2508 'degraded_sampleable' 'legacy_incomplete_conditional_known_support' 73)
    uppercase_status = Test-Accepted (New-Spec 2508 2508 'DEGRADED_SAMPLEABLE' 'legacy_incomplete_conditional_known_support' 73)
    missing_support = Test-Accepted $missingSupport
}
$wrongGeneration = New-Spec 2508 2508 'sampleable' '' 0
$wrongGeneration.PackageGeneration = 'b049c3-2026-09-10-597'
$result.wrong_generation = Test-Accepted $wrongGeneration
$wrongScope = New-Spec 2508 2508 'sampleable' '' 0
$wrongScope.ModelScopeCount = 18
$result.wrong_scope = Test-Accepted $wrongScope
$wrongCount = New-Spec 2508 2508 'sampleable' '' 0
$wrongCount.InventoryCount = 51
$result.wrong_count = Test-Accepted $wrongCount
$wrongHero = New-Spec 2508 2508 'sampleable' '' 0
$wrongHero.Hero = 'aisha'
$result.wrong_hero = Test-Accepted $wrongHero
$wrongPartial = New-Spec 2508 2508 'sampleable' '' 0
$wrongPartial.SettlementIncomplete = $false
$result.wrong_partial = Test-Accepted $wrongPartial
$wrongCurrentSupport = New-Spec 2508 2508 'sampleable' 'legacy_incomplete_conditional_known_support' 0
$result.wrong_current_support = Test-Accepted $wrongCurrentSupport
$wrongCurrentPpm = New-Spec 2508 2508 'sampleable' '' 73
$result.wrong_current_ppm = Test-Accepted $wrongCurrentPpm
$stringScope = New-Spec 2508 2508 'sampleable' '' 0
$stringScope.ModelScopeCount = '112'
$result.string_scope = Test-Accepted $stringScope
[Console]::Out.Write(($result | ConvertTo-Json -Depth 4 -Compress))
'''
    for executable, _edition, _major in _powershell_runtime_hosts():
        result = _run_runtime_gate_spec_probe(executable, body)
        assert result["canonical_5604"] is True
        assert result["canonical_2508"] is True
        assert all(
            accepted is False
            for name, accepted in result.items()
            if name not in {"canonical_5604", "canonical_2508"}
        )


def test_build_requires_fixed_release_assets_and_manifest_identity() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '"scripts/release_zip_contract.json"' in build
    assert "缺 BidKingLive.exe 正式启动器" in build
    assert "缺使用说明.txt 正式资产" in build
    assert "StaticData.cs 与受审 release ZIP 合同身份不一致" in build
    for field in (
        "ReleaseZipContractSHA256",
        "RuntimeDependencyInventorySHA256",
        "RuntimeInventoryReceiptSHA256",
        "LauncherSHA256",
        "StaticDataSourceSHA256",
    ):
        assert f'"{field}: ${field}"' in build
    assert build.count("ahmad_live_panel_server.summarize_snapshot") == 1
    assert build.count("New-RuntimeFixtureGateCode -GateSpec $gateSpec") == 2
    assert "CARGO_ENCODED_RUSTFLAGS" in build
    assert "$releaseRustFlags = @($ControlledRustRemapFlags)" in build
    assert "$bootstrapRustFlags = @($ControlledRustRemapFlags)" in build
    assert 'PublicPrefix = "<cargo-registry>"' in build
    assert 'PublicPrefix = "<cargo-home>"' in build
    assert 'PublicPrefix = "<rust-toolchain>"' in build
    assert 'PublicPrefix = "<build-temp>"' in build
    assert 'PublicPrefix = "<build-user>"' in build
    preflight = build[
        build.index("# --- 0. 前置检查 ---") : build.index(
            "# 第三方 WSS runtime"
        )
    ]
    ambient_match = re.search(
        r"foreach \(\$ambientNativeOverride in @\((.*?)\n\s*\)\)",
        preflight,
        flags=re.DOTALL,
    )
    assert ambient_match is not None
    assert set(re.findall(r'"([A-Z_]+)"', ambient_match.group(1))) == {
        "CL",
        "_CL_",
        "LINK",
        "_LINK_",
        "RUSTFLAGS",
        "CARGO_BUILD_RUSTFLAGS",
        "CARGO_ENCODED_RUSTFLAGS",
    }
    assert "$origEncodedRustFlags +" not in build
    assert "[System.IO.Path]::IsPathFullyQualified" not in build
    assert (
        "$null -ne [Environment]::GetEnvironmentVariable($ambientNativeOverride)"
        in preflight
    )
    assert build.count(
        "Restore-ControlledCargoEncodedRustFlags `"
    ) == 2
    kernel_block = build[
        build.index("# --- 4c. Rust 安全内核") : build.index(
            '$whl = Get-ChildItem (Join-Path $kernelDir "target\\wheels")'
        )
    ]
    assert (
        kernel_block.index("try {")
        < kernel_block.index(
            '[Environment]::SetEnvironmentVariable(\n'
            '            "CARGO_ENCODED_RUSTFLAGS"'
        )
        < kernel_block.index("maturin build --release")
        < kernel_block.index("} finally {")
        < kernel_block.index("Restore-ControlledCargoEncodedRustFlags")
    )
    assert (
        "$NativePrivacyForbiddenPaths = @(\n"
        "        $ControlledRustPathMappings | ForEach-Object { [string]$_.Source }\n"
        "    )"
    ) in build
    assert build.count("Invoke-NativeArtifactPrivacyGate `") == 3


def test_update606_contract_modules_are_exact_build_only_stage_exclusions(
    tmp_path: Path,
) -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    manifest = json.loads(
        (ROOT / "scripts/client_protection_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    build_only_sources = set(manifest["build_only_source_authorities"])
    exclusions = {value.replace("/", "\\") for value in build_only_sources}
    assert exclusions == {
        r"src\bidking_lab\extract\official_20261008_model_profile.py",
        r"src\bidking_lab\extract\update606_package_input_contract.py",
        r"src\bidking_lab\extract\update606_release_contract.py",
    }
    assert "$ClientProtection.build_only_source_authorities" in build
    for exclusion in exclusions:
        assert f'"{exclusion}"' not in build
    copy_at = build.index('Copy-Tree "src\\bidking_lab"')
    remove_at = build.index(
        "Remove-Item -LiteralPath $stagedBuildOnlyModule -Force", copy_at
    )
    native_at = build.index("# --- 4b-1.", remove_at)
    final_assert_at = build.index("最终 Stage 残留 build-only 模块")
    canonical_zip_at = build.index("--write-canonical-zip")
    assert copy_at < remove_at < native_at < final_assert_at < canonical_zip_at
    assert "Remove-Item $stagedBuildOnlyModule" not in build

    near_name = r"src\bidking_lab\extract\update606_release_contract_runtime.py"
    assert near_name not in exclusions
    staged_paths = exclusions | {near_name}
    for relative in staged_paths:
        path = tmp_path / Path(relative.replace("\\", "/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    for relative in exclusions:
        (tmp_path / Path(relative.replace("\\", "/"))).unlink()
    assert (
        tmp_path / Path(near_name.replace("\\", "/"))
    ).read_text(encoding="utf-8") == "fixture"


def test_builder_rejects_malformed_build_only_source_authority_contract(
    tmp_path: Path,
) -> None:
    manifest = json.loads(
        (ROOT / "scripts/client_protection_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    variants: list[object] = [
        [
            manifest["build_only_source_authorities"][0],
            manifest["build_only_source_authorities"][0],
        ],
        [
            manifest["build_only_source_authorities"][0],
            manifest["build_only_source_authorities"][0].upper(),
        ],
        [
            "src/bidking_lab/extract/../secret.py",
            manifest["build_only_source_authorities"][1],
        ],
        [
            "/src/bidking_lab/extract/secret.py",
            manifest["build_only_source_authorities"][1],
        ],
        [
            "C:/src/bidking_lab/extract/secret.py",
            manifest["build_only_source_authorities"][1],
        ],
        [
            r"src\bidking_lab\extract\secret.py",
            manifest["build_only_source_authorities"][1],
        ],
        True,
        manifest["build_only_source_authorities"][0],
    ]
    documents: list[dict[str, object]] = []
    for value in variants:
        document = json.loads(json.dumps(manifest))
        document["build_only_source_authorities"] = value
        documents.append(document)
    missing = json.loads(json.dumps(manifest))
    missing.pop("build_only_source_authorities")
    documents.append(missing)

    for executable, _edition, _major in _powershell_runtime_hosts():
        baseline_root = tmp_path / executable.stem / "baseline"
        baseline_manifest = baseline_root / "scripts/client_protection_manifest.json"
        baseline_manifest.parent.mkdir(parents=True)
        baseline_manifest.write_text(json.dumps(manifest), encoding="utf-8")
        baseline = _run_client_protection_manifest_probe(executable, baseline_root)
        assert baseline.returncode == 0
        assert set(baseline.stdout.split("|")) == {
            r"src\bidking_lab\extract\official_20261008_model_profile.py",
            r"src\bidking_lab\extract\update606_package_input_contract.py",
            r"src\bidking_lab\extract\update606_release_contract.py",
        }

        for index, document in enumerate(documents):
            variant_root = tmp_path / executable.stem / f"invalid-{index}"
            variant_manifest = variant_root / "scripts/client_protection_manifest.json"
            variant_manifest.parent.mkdir(parents=True)
            variant_manifest.write_text(json.dumps(document), encoding="utf-8")
            completed = _run_client_protection_manifest_probe(
                executable,
                variant_root,
            )
            assert completed.returncode != 0


def test_update606_contract_modules_have_no_runtime_importers() -> None:
    target_modules = {
        "bidking_lab.extract.update606_package_input_contract",
        "bidking_lab.extract.update606_release_contract",
    }
    target_leaves = {name.rsplit(".", 1)[-1] for name in target_modules}

    def imports_target(path: Path) -> bool:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name in target_modules for alias in node.names
            ):
                return True
            if isinstance(node, ast.ImportFrom):
                if node.module in target_modules:
                    return True
                if node.module in {None, "bidking_lab.extract"} and any(
                    alias.name in target_leaves for alias in node.names
                ):
                    return True
        return False

    manifest = json.loads(
        (ROOT / "scripts/client_protection_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    build_only_paths = {
        ROOT / relative
        for relative in manifest["build_only_source_authorities"]
    }
    runtime_importers = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src/bidking_lab").rglob("*.py")
        if path not in build_only_paths and imports_target(path)
    }
    assert runtime_importers == set()

    script_importers = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "scripts").rglob("*.py")
        if imports_target(path)
    }
    assert script_importers == {
        "scripts/build_processed_data.py",
        "scripts/calibration/assemble_update606_final_inputs.py",
        "scripts/calibration/build_item_category_authority.py",
        "scripts/calibration/build_update606_package_tables.py",
        "scripts/calibration/carry_forward_update606_legacy_distribution.py",
        "scripts/check_update606_release_contract.py",
    }

    final_input_checker = ast.parse(
        (ROOT / "scripts/check_update606_final_inputs.py").read_text(
            encoding="utf-8-sig"
        )
    )
    current_authority_imports = {
        alias.name
        for node in ast.walk(final_input_checker)
        if isinstance(node, ast.ImportFrom)
        and node.module
        == "bidking_lab.extract.current_rankmap_package_contract"
        for alias in node.names
    }
    assert current_authority_imports == {
        "CURRENT_PACKAGE_GENERATION",
        "CURRENT_RANKMAP_AUTHORITY_MODE",
        "CURRENT_RANKMAP_MAP_IDS",
        "CURRENT_RANKMAP_WORKFLOW_INPUT_FINGERPRINT",
    }


def test_packaged_runtime_imports_without_build_only_or_repo_source_fallback(
    tmp_path: Path,
) -> None:
    manifest = json.loads(
        (ROOT / "scripts/client_protection_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    build_only = set(manifest["build_only_source_authorities"])
    protected = {
        *(f"src/{relative}" for relative in manifest["critical_native"]["package_modules"]),
        *(
            f"src/{relative}"
            for tree in manifest["critical_native"]["package_trees"]
            for relative in tree["protected_modules"]
        ),
    }
    plaintext = {
        relative
        for key in ("plaintext_allowlist", "packaged_plaintext_python_allowlist")
        for relative in manifest[key]
        if relative.startswith("src/bidking_lab/")
    }
    runtime_sources = protected | plaintext
    assert runtime_sources.isdisjoint(build_only)
    assert runtime_sources < {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src/bidking_lab").rglob("*.py")
    }
    runtime_gateway_source = (
        ROOT / "src/bidking_lab/live/current_rankmap_runtime.py"
    ).read_text(encoding="utf-8")
    assert "ExtensionFileLoader" in runtime_gateway_source
    assert 'filename.endswith(".pyd")' in runtime_gateway_source

    stage = tmp_path / "runtime-stage"
    for relative in sorted(runtime_sources):
        source = ROOT / relative
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for relative in build_only:
        assert not (stage / relative).exists()

    authority = stage / "src/bidking_lab/extract/current_rankmap_package_contract.py"
    assert authority.is_file()
    assert not (stage / "src/bidking_lab/extract/update606_package_input_contract.py").exists()
    assert not (stage / "src/bidking_lab/extract/update606_release_contract.py").exists()

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    held_python = Path(r"C:\Python31315\python.exe")
    assert held_python.is_file()

    def run(root: Path) -> subprocess.CompletedProcess[str]:
        variant_src = root / "src"
        variant_code = "\n".join(
            (
                "import importlib, pathlib, sys",
                "assert sys.version_info[:2] == (3, 13)",
                f"stage = pathlib.Path({str(variant_src)!r}).resolve()",
                f"repo = pathlib.Path({str(ROOT / 'src')!r}).resolve()",
                "assert repo not in {pathlib.Path(p).resolve() for p in sys.path if p}",
                "sys.path.insert(0, str(stage))",
                "names = ('bidking_lab.live', 'bidking_lab.live.monitor', "
                "'bidking_lab.live.current_rankmap_runtime')",
                "modules = [importlib.import_module(name) for name in names]",
                "for name, module in tuple(sys.modules.items()):",
                "    if not name.startswith('bidking_lab') or not getattr(module, '__file__', None):",
                "        continue",
                "    pathlib.Path(module.__file__).resolve().relative_to(stage)",
                "assert hasattr(modules[1], 'load_monitor_tables')",
                "assert hasattr(modules[2], 'bind_current_rankmap_algorithm_prior')",
                "print('PACKAGED_RUNTIME_IMPORT_OK')",
            )
        )
        return subprocess.run(
            [str(held_python), "-I", "-B", "-c", variant_code],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    positive = run(stage)
    assert positive.returncode == 0, positive.stderr
    assert positive.stdout.strip() == "PACKAGED_RUNTIME_IMPORT_OK"

    missing = tmp_path / "missing-runtime-authority"
    shutil.copytree(stage, missing)
    (missing / authority.relative_to(stage)).unlink()
    missing_result = run(missing)
    assert missing_result.returncode != 0
    assert "current_rankmap_package_contract" in missing_result.stderr
    assert "update606_package_input_contract" not in missing_result.stderr
    assert "update606_release_contract" not in missing_result.stderr

    wrong = tmp_path / "wrong-runtime-authority"
    shutil.copytree(stage, wrong)
    (wrong / authority.relative_to(stage)).write_text(
        "CURRENT_PACKAGE_GENERATION = 'wrong-generation'\n",
        encoding="ascii",
    )
    wrong_result = run(wrong)
    assert wrong_result.returncode != 0
    assert "current_rankmap_package_contract" in wrong_result.stderr
    assert "update606_package_input_contract" not in wrong_result.stderr
    assert "update606_release_contract" not in wrong_result.stderr

    def run_cached_variant(
        root: Path,
        mutation: str,
        *,
        preload_runtime: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        variant_src = root / "src"
        variant_code = "\n".join(
            (
                "import importlib, importlib.util, pathlib, sys, types",
                "assert sys.version_info[:2] == (3, 13)",
                f"stage = pathlib.Path({str(variant_src)!r}).resolve()",
                f"repo = pathlib.Path({str(ROOT / 'src')!r}).resolve()",
                "assert repo not in {pathlib.Path(p).resolve() for p in sys.path if p}",
                "sys.path.insert(0, str(stage))",
                "authority_name = 'bidking_lab.extract.current_rankmap_package_contract'",
                "authority = importlib.import_module(authority_name)",
                (
                    "runtime = importlib.import_module("
                    "'bidking_lab.live.current_rankmap_runtime')"
                    if preload_runtime
                    else "runtime = None"
                ),
                mutation,
                "importlib.invalidate_caches()",
                "for name in tuple(sys.modules):",
                "    if name == 'bidking_lab.live' or (name.startswith('bidking_lab.live.') "
                f"and not ({preload_runtime!r} and name == "
                "'bidking_lab.live.current_rankmap_runtime')):",
                "        del sys.modules[name]",
                "importlib.import_module('bidking_lab.live.monitor')",
                "print('CACHED_AUTHORITY_ACCEPTED')",
            )
        )
        return subprocess.run(
            [str(held_python), "-I", "-B", "-c", variant_code],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    canonical_cached = tmp_path / "canonical-cached-runtime-authority"
    shutil.copytree(stage, canonical_cached)
    canonical_cached_result = run_cached_variant(canonical_cached, "pass")
    assert canonical_cached_result.returncode == 0, canonical_cached_result.stderr
    assert canonical_cached_result.stdout.strip() == "CACHED_AUTHORITY_ACCEPTED"

    cached_missing = tmp_path / "cached-missing-runtime-authority"
    shutil.copytree(stage, cached_missing)
    cached_missing_result = run_cached_variant(
        cached_missing,
        "pathlib.Path(authority.__file__).unlink()",
    )
    assert cached_missing_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_missing_result.stderr

    cached_runtime_missing = tmp_path / "cached-runtime-missing-authority"
    shutil.copytree(stage, cached_runtime_missing)
    cached_runtime_missing_result = run_cached_variant(
        cached_runtime_missing,
        "pathlib.Path(authority.__file__).unlink()",
        preload_runtime=True,
    )
    assert cached_runtime_missing_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_runtime_missing_result.stderr

    cached_rebound = tmp_path / "cached-rebound-runtime-authority"
    shutil.copytree(stage, cached_rebound)
    cached_rebound_result = run_cached_variant(
        cached_rebound,
        "pathlib.Path(authority.__file__).write_text("
        "\"CURRENT_PACKAGE_GENERATION = 'wrong-generation'\\n\", encoding='ascii')",
    )
    assert cached_rebound_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_rebound_result.stderr

    cached_case_alias = tmp_path / "cached-case-alias-runtime-authority"
    shutil.copytree(stage, cached_case_alias)
    cached_case_alias_result = run_cached_variant(
        cached_case_alias,
        "authority.__file__ = authority.__file__.lower(); "
        "authority.__spec__.origin = authority.__file__; "
        "authority.__spec__.loader.path = authority.__file__",
    )
    assert cached_case_alias_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_case_alias_result.stderr

    cached_foreign = tmp_path / "cached-foreign-runtime-authority"
    shutil.copytree(stage, cached_foreign)
    foreign_authority = tmp_path / "foreign-current-rankmap-authority.py"
    shutil.copy2(authority, foreign_authority)
    cached_foreign_result = run_cached_variant(
        cached_foreign,
        "foreign_spec = importlib.util.spec_from_file_location("
        f"authority_name, {str(foreign_authority)!r}); "
        "foreign = importlib.util.module_from_spec(foreign_spec); "
        "sys.modules[authority_name] = foreign; "
        "foreign_spec.loader.exec_module(foreign)",
    )
    assert cached_foreign_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_foreign_result.stderr

    cached_plain = tmp_path / "cached-plain-runtime-authority"
    shutil.copytree(stage, cached_plain)
    cached_plain_result = run_cached_variant(
        cached_plain,
        "sys.modules[authority_name] = types.ModuleType(authority_name)",
    )
    assert cached_plain_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_plain_result.stderr

    cached_plain_clone = tmp_path / "cached-plain-clone-runtime-authority"
    shutil.copytree(stage, cached_plain_clone)
    cached_plain_clone_result = run_cached_variant(
        cached_plain_clone,
        "clone = types.ModuleType(authority_name); "
        "clone.__dict__.update(authority.__dict__); "
        "sys.modules[authority_name] = clone",
    )
    assert cached_plain_clone_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_plain_clone_result.stderr

    cached_partial_spec = tmp_path / "cached-partial-spec-runtime-authority"
    shutil.copytree(stage, cached_partial_spec)
    cached_partial_spec_result = run_cached_variant(
        cached_partial_spec,
        "authority.__spec__ = importlib.util.spec_from_loader(authority_name, None)",
    )
    assert cached_partial_spec_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_partial_spec_result.stderr

    cached_proxy = tmp_path / "cached-proxy-runtime-authority"
    shutil.copytree(stage, cached_proxy)
    cached_proxy_result = run_cached_variant(
        cached_proxy,
        "sys.modules[authority_name] = types.SimpleNamespace("
        "__name__=authority_name, __file__=authority.__file__, "
        "__spec__=authority.__spec__)",
    )
    assert cached_proxy_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_proxy_result.stderr

    cached_hardlink = tmp_path / "cached-hardlink-runtime-authority"
    shutil.copytree(stage, cached_hardlink)
    hardlink_authority = cached_hardlink / authority.relative_to(stage)
    hardlink_backing = cached_hardlink / "authority-backing.py"
    shutil.copy2(hardlink_authority, hardlink_backing)
    hardlink_authority.unlink()
    os.link(hardlink_backing, hardlink_authority)
    assert hardlink_authority.stat().st_nlink == 2
    cached_hardlink_result = run_cached_variant(cached_hardlink, "pass")
    assert cached_hardlink_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_hardlink_result.stderr

    cached_reparse = tmp_path / "cached-reparse-runtime-authority"
    shutil.copytree(stage, cached_reparse)
    extract_dir = cached_reparse / "src/bidking_lab/extract"
    extract_target = tmp_path / "cached-reparse-extract-target"
    extract_dir.rename(extract_target)
    junction = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(extract_dir), str(extract_target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert junction.returncode == 0, junction.stderr
    cached_reparse_result = run_cached_variant(cached_reparse, "pass")
    assert cached_reparse_result.returncode != 0
    assert "current_rankmap_package_contract" in cached_reparse_result.stderr
