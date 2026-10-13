from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_release_zip.py"
BUILD_SCRIPT = ROOT / "scripts" / "build_live_portable.ps1"
_SPEC = importlib.util.spec_from_file_location("audit_release_zip_receipt", AUDIT_SCRIPT)
assert _SPEC and _SPEC.loader
_AUDIT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AUDIT)
_ROUTE_HASH_FIELDS = tuple(sorted(_AUDIT.RELEASE_AVAILABILITY_ROUTE_HASH_FIELDS))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(source_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(source_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _valid_release_availability_receipt() -> tuple[dict[str, object], dict[str, object]]:
    contract = json.loads(
        (ROOT / "scripts" / "release_zip_contract.json").read_text(encoding="utf-8")
    )
    authority = contract["release_availability"]
    receipt = {
        "schema": authority["receipt_schema"],
        "receipt_version": authority["receipt_version"],
        "status": "pass",
        "captures": [
            {**capture, "verdict": "pass"} for capture in authority["captures"]
        ],
        "stage": {"identity_sha256": "1" * 64, "verdict": "pass"},
        "extracted_zip": {"identity_sha256": "1" * 64, "verdict": "pass"},
        "policy_census": {**authority["policy_census"], "verdict": "pass"},
        "route_scope": {
            key: authority["route_scope"][key]
            for key in _AUDIT.RELEASE_AVAILABILITY_ROUTE_COUNT_FIELDS
        }
        | {"verdict": "pass"},
        "chains": {
            key: "pass" for key in _AUDIT.RELEASE_AVAILABILITY_CHAIN_FIELDS
        },
    }
    return contract, receipt


def _receipt_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    source_root = tmp_path / "source"
    scripts = source_root / "scripts"
    scripts.mkdir(parents=True)
    protection = scripts / "protection.json"
    approval = scripts / "runtime-approval.json"
    release_contract = scripts / "release-contract.json"
    integrity_contract = scripts / "integrity-contract.json"
    native_contract = scripts / "native-contract.json"
    protection.write_text("{}\n", encoding="utf-8")
    approval.write_text("{}\n", encoding="utf-8")
    release_contract.write_text('{"contract_version":15}\n', encoding="utf-8")
    integrity_contract.write_text("{}\n", encoding="utf-8")
    native_contract.write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "init", str(source_root)], check=True, capture_output=True)
    _git(source_root, "config", "user.email", "receipt-test@example.invalid")
    _git(source_root, "config", "user.name", "Receipt Test")
    _git(
        source_root,
        "add",
        "scripts/protection.json",
        "scripts/runtime-approval.json",
        "scripts/release-contract.json",
        "scripts/integrity-contract.json",
        "scripts/native-contract.json",
    )
    _git(source_root, "commit", "-m", "fixture")
    source_commit = _git(source_root, "rev-parse", "HEAD")
    source_branch = _git(source_root, "rev-parse", "--abbrev-ref", "HEAD")

    candidate = tmp_path / "candidate.zip"
    candidate.write_bytes(b"synthetic immutable candidate")
    runtime_receipt = tmp_path / "candidate.zip.runtime-receipt.json"
    runtime_receipt.write_bytes(b"{}")
    receipt = {
        "schema": "bidking_release_build_audit_receipt_v5",
        "receipt_version": 5,
        "zip_name": candidate.name,
        "zip_bytes": candidate.stat().st_size,
        "zip_sha256": _sha256(candidate),
        "source_commit": source_commit,
        "source_branch": source_branch,
        "product_profile": "standard",
        "encrypted": False,
        "protection_manifest": None,
        "release_contract": None,
        "local_integrity_contract": None,
        "local_integrity": None,
        "native_binary_contract": None,
        "native_binary_receipt": None,
        "runtime_receipt": {
            "name": runtime_receipt.name,
            "sha256": _sha256(runtime_receipt),
        },
        "runtime_approval": {
            "path": "scripts/runtime-approval.json",
            "sha256": _sha256(approval),
        },
        "bkenc_receipt": None,
        "release_availability": None,
        "expected_manifest_fields": {
            "SourceCommit": source_commit,
            "SourceBranch": source_branch,
            "ProductProfile": "standard",
            "Encrypted": "False",
        },
        "trusted_assets": [],
        "forbidden_path_sources": ["source_root"],
    }
    receipt_path = tmp_path / "candidate.zip.build-audit-receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return source_root, receipt_path, receipt


def _bind_protection_manifest(
    source_root: Path, receipt: dict[str, object]
) -> None:
    protection = source_root / "scripts" / "protection.json"
    receipt["protection_manifest"] = {
        "path": "scripts/protection.json",
        "sha256": _sha256(protection),
    }


def test_build_receipt_binds_zip_source_commit_and_input_hashes(tmp_path: Path) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)

    loaded = _AUDIT.load_build_audit_receipt(receipt_path, source_root)

    assert loaded["zip_path"].name == receipt["zip_name"]
    assert loaded["product_profile"] == "standard"
    assert loaded["encrypted"] is False
    assert loaded["protection_manifest"] is None
    assert dict(loaded["expected_manifest_fields"]) == receipt[
        "expected_manifest_fields"
    ]


@pytest.mark.parametrize(
    "mutation",
    ("capture_swap", "private_field", "package_mismatch", "census", "chain"),
)
def test_v5_release_availability_receipt_is_strict_and_support_safe(
    tmp_path: Path,
    mutation: str,
) -> None:
    contract, receipt = _valid_release_availability_receipt()
    contract_path = tmp_path / "release-contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    if mutation == "capture_swap":
        receipt["captures"][0], receipt["captures"][1] = (
            receipt["captures"][1],
            receipt["captures"][0],
        )
    elif mutation == "private_field":
        receipt["captures"][0]["path"] = "forbidden"
    elif mutation == "package_mismatch":
        receipt["extracted_zip"]["identity_sha256"] = "2" * 64
    elif mutation == "census":
        receipt["policy_census"]["source_map"]["sampleable"] = 100
    else:
        receipt["chains"]["summary_api_ui"] = "fail"

    with pytest.raises(ValueError, match="release_availability"):
        _AUDIT._validate_release_availability_receipt(receipt, contract_path)


def test_v5_release_availability_receipt_accepts_exact_five_capture_authority(
    tmp_path: Path,
) -> None:
    contract, receipt = _valid_release_availability_receipt()
    contract_path = tmp_path / "release-contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    loaded = _AUDIT._validate_release_availability_receipt(receipt, contract_path)

    assert len(loaded["captures"]) == 5
    assert loaded["policy_census"]["direct_root"]["sampleable"] == 81
    assert loaded["policy_census"]["source_map"]["sampleable"] == 101
    assert loaded["route_scope"]["ranked_production_unsupported_count"] == 0
    assert set(contract["release_availability"]["route_scope"]) == (
        _AUDIT.RELEASE_AVAILABILITY_ROUTE_FIELDS
    )
    assert contract["release_availability"]["route_scope"] == (
        _AUDIT.EXPECTED_RELEASE_AVAILABILITY_ROUTE_SCOPE
    )
    rendered = json.dumps(loaded, sort_keys=True)
    assert not any(
        private in rendered.casefold()
        for private in ("capture_path", "session_id", "player", "item_id")
    )


@pytest.mark.parametrize("field", _ROUTE_HASH_FIELDS)
@pytest.mark.parametrize(
    "mutation",
    ("missing", "stale", "extra", "wrong_type", "uppercase", "length"),
)
def test_v5_release_availability_rejects_invalid_bound_route_hash_contract(
    tmp_path: Path,
    field: str,
    mutation: str,
) -> None:
    contract, receipt = _valid_release_availability_receipt()
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
    contract_path = tmp_path / f"route-{mutation}-{field}.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="release_availability_contract_invalid",
    ):
        _AUDIT._validate_release_availability_receipt(receipt, contract_path)


@pytest.mark.parametrize(
    "field",
    tuple(sorted(_AUDIT.RELEASE_AVAILABILITY_ROUTE_COUNT_FIELDS)),
)
@pytest.mark.parametrize("mutation", ("stale", "wrong_type"))
def test_v5_release_availability_rejects_invalid_bound_route_count_contract(
    tmp_path: Path,
    field: str,
    mutation: str,
) -> None:
    contract, receipt = _valid_release_availability_receipt()
    route = contract["release_availability"]["route_scope"]
    route[field] = route[field] + 1 if mutation == "stale" else False
    contract_path = tmp_path / f"route-count-{mutation}-{field}.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="release_availability_contract_invalid",
    ):
        _AUDIT._validate_release_availability_receipt(receipt, contract_path)


class _IntSubclass(int):
    pass


class _StrSubclass(str):
    pass


@pytest.mark.parametrize(
    ("schema", "version", "removed_fields", "expected"),
    (
        (
            "bidking_release_build_audit_receipt_v5",
            5,
            (),
            (True, False, False, False, False),
        ),
        (
            "bidking_release_build_audit_receipt_v4",
            4,
            ("release_availability",),
            (False, True, False, False, False),
        ),
        (
            "bidking_release_build_audit_receipt_v3",
            3,
            ("release_availability",),
            (False, False, True, False, False),
        ),
        (
            "bidking_release_build_audit_receipt_v2",
            2,
            (
                "release_availability",
                "native_binary_contract",
                "native_binary_receipt",
            ),
            (False, False, False, True, False),
        ),
        (
            "bidking_release_build_audit_receipt_v1",
            1,
            (
                "local_integrity_contract",
                "local_integrity",
                "release_availability",
                "native_binary_contract",
                "native_binary_receipt",
            ),
            (False, False, False, False, True),
        ),
    ),
)
def test_build_receipt_classifier_preserves_exact_v5_v4_v3_v2_v1(
    tmp_path: Path,
    schema: str,
    version: int,
    removed_fields: tuple[str, ...],
    expected: tuple[bool, bool, bool, bool, bool],
) -> None:
    _source_root, _receipt_path, receipt = _receipt_fixture(tmp_path)
    receipt["schema"] = schema
    receipt["receipt_version"] = version
    if version == 3:
        receipt["native_signing_policy"] = None
    for field in removed_fields:
        receipt.pop(field)

    assert _AUDIT._classify_build_audit_receipt(receipt) == expected


@pytest.mark.parametrize(
    "version",
    (True, 5.0, "5", None, _IntSubclass(5)),
)
def test_build_receipt_classifier_rejects_non_exact_builtin_int_version(
    tmp_path: Path, version: object
) -> None:
    _source_root, _receipt_path, receipt = _receipt_fixture(tmp_path)
    receipt["receipt_version"] = version

    with pytest.raises(ValueError, match="invalid_build_receipt_identity"):
        _AUDIT._classify_build_audit_receipt(receipt)


@pytest.mark.parametrize(
    "schema",
    (True, 5.0, 5, None, _StrSubclass("bidking_release_build_audit_receipt_v5")),
)
def test_build_receipt_classifier_rejects_non_exact_builtin_string_schema(
    tmp_path: Path, schema: object
) -> None:
    _source_root, _receipt_path, receipt = _receipt_fixture(tmp_path)
    receipt["schema"] = schema

    with pytest.raises(ValueError, match="invalid_build_receipt_identity"):
        _AUDIT._classify_build_audit_receipt(receipt)


@pytest.mark.parametrize("mutation", ("missing", "extra", "unknown_version"))
def test_build_receipt_classifier_rejects_missing_extra_or_unknown_fields(
    tmp_path: Path, mutation: str
) -> None:
    _source_root, _receipt_path, receipt = _receipt_fixture(tmp_path)
    if mutation == "missing":
        receipt.pop("native_binary_receipt")
    elif mutation == "extra":
        receipt["unreviewed_authority"] = None
    else:
        receipt["receipt_version"] = 6

    with pytest.raises(ValueError, match="invalid_build_receipt_fields"):
        _AUDIT._classify_build_audit_receipt(receipt)


@pytest.mark.parametrize(
    "mutation",
    ("zip_bytes", "zip_sha256", "source_commit", "source_hash", "runtime_hash"),
)
def test_build_receipt_rejects_identity_tamper(
    tmp_path: Path, mutation: str
) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)
    if mutation == "zip_bytes":
        receipt["zip_bytes"] += 1
    elif mutation == "zip_sha256":
        receipt["zip_sha256"] = "0" * 64
    elif mutation == "source_commit":
        receipt["source_commit"] = "0" * 40
        receipt["expected_manifest_fields"]["SourceCommit"] = "0" * 40
    elif mutation == "source_hash":
        receipt["protection_manifest"] = {
            "path": "scripts/protection.json",
            "sha256": "0" * 64,
        }
    else:
        receipt["runtime_receipt"]["sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError):
        _AUDIT.load_build_audit_receipt(receipt_path, source_root)


@pytest.mark.parametrize(
    "path_value",
    ("../outside.json", "/absolute.json", "scripts/../protection.json"),
)
def test_build_receipt_rejects_source_path_escape(
    tmp_path: Path, path_value: str
) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)
    receipt["protection_manifest"] = {
        "path": path_value,
        "sha256": _sha256(source_root / "scripts/protection.json"),
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError):
        _AUDIT.load_build_audit_receipt(receipt_path, source_root)


def test_build_receipt_cli_rejects_manual_identity_arguments(tmp_path: Path) -> None:
    source_root, receipt_path, _receipt = _receipt_fixture(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(AUDIT_SCRIPT),
            "--build-receipt",
            str(receipt_path),
            "--source-root",
            str(source_root),
            "--expect-field",
            "SourceCommit=decoy",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "必须独占" in result.stdout


def test_build_script_inspect_and_preflight_exit_before_mutation() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    first_mutation = build.index("# --- 1. 清理 + 目录 ---")
    inspect_exit = build.index("if ($InspectOnly)")
    preflight_exit = build.index("if ($PreflightOnly)")
    preflight_segment = build[build.index("# 成品源码溯源") : first_mutation]

    assert inspect_exit < first_mutation
    assert preflight_exit < first_mutation
    assert "Remove-Item" not in preflight_segment
    assert "New-Item" not in preflight_segment
    assert "Set-Content" not in preflight_segment
    assert "Copy-Item" not in preflight_segment
    assert "& $BuildPython -E -B -c" in preflight_segment
    assert "& $BuildPython -B $releaseAssetVerifier" in preflight_segment
    assert "& $BuildPython -B $integrityChecker --verify" in preflight_segment
    assert "& $BuildPython -B $integrityChecker --list-json" in preflight_segment
    assert "& $BuildPython -E -B $keyVerifier" in preflight_segment


@pytest.mark.parametrize("legacy_parameter", ("NativeSigningPolicyFile", "SignToolPath"))
def test_builder_rejects_legacy_signed_only_parameter_before_execution(
    legacy_parameter: str,
) -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(BUILD_SCRIPT),
            "-InspectOnly",
            f"-{legacy_parameter}",
            str(ROOT / "must-not-be-read"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert result.returncode != 0
    assert "NamedParameterNotFound" in result.stderr
    assert "缺引擎静态先验输入" not in result.stderr


def test_primary_zip_audit_consumes_build_receipt_without_manual_field_rebuild() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    section = build[
        build.index("# --- 7a. 冻结构建审计 authority receipt") : build.index(
            "if ($SplitCompatibilityRuntime)",
            build.index("# --- 7a. 冻结构建审计 authority receipt"),
        )
    ]

    assert 'schema = "bidking_release_build_audit_receipt_v5"' in section
    assert "release_availability = if ($Encrypt)" in section
    assert "native_signing_policy =" not in section
    assert '"--build-receipt", $BuildAuditReceiptPath' in section
    assert '"--source-root", $Repo' in section
    assert '"--expect-field"' not in section
    assert '"--release-contract"' not in section
    assert '"--runtime-receipt"' not in section
    assert '"--trusted-asset"' not in section


def test_unencrypted_build_receipt_drops_unproduced_wss_hash_expectations() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    section = build[
        build.index("$ExpectedBuildManifestFields =") : build.index(
            "$TrustedAssetReceipts = @()",
            build.index("$ExpectedBuildManifestFields ="),
        )
    ]

    assert "if (-not $Encrypt)" in section
    assert '$ExpectedBuildManifestFields.Remove("WssAddonBootstrapSHA256")' in section
    assert '$ExpectedBuildManifestFields.Remove("WssAddonLoaderSHA256")' in section


def test_unencrypted_build_receipt_omits_full_protection_authority() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    section = build[
        build.index("$BuildAuditReceipt = [ordered]@{") : build.index(
            "$BuildAuditReceiptJson =",
            build.index("$BuildAuditReceipt = [ordered]@{"),
        )
    ]

    assert "protection_manifest = if ($Encrypt)" in section
    assert '"scripts/client_protection_manifest.json" $ClientProtectionManifestPath' in section


@pytest.mark.parametrize(
    "field",
    (
        "protection_manifest",
        "release_contract",
        "local_integrity_contract",
        "local_integrity",
        "native_binary_contract",
        "native_binary_receipt",
        "bkenc_receipt",
        "release_availability",
    ),
)
def test_unencrypted_receipt_rejects_each_encryption_only_authority(
    tmp_path: Path, field: str
) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)
    receipt[field] = True
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected_encryption_receipt"):
        _AUDIT.load_build_audit_receipt(receipt_path, source_root)


def test_legacy_v1_unencrypted_build_receipt_remains_readable(tmp_path: Path) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)
    receipt["schema"] = "bidking_release_build_audit_receipt_v1"
    receipt["receipt_version"] = 1
    receipt.pop("local_integrity_contract")
    receipt.pop("local_integrity")
    receipt.pop("native_binary_contract")
    receipt.pop("native_binary_receipt")
    receipt.pop("release_availability")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    loaded = _AUDIT.load_build_audit_receipt(receipt_path, source_root)

    assert loaded["encrypted"] is False
    assert loaded["local_integrity"] is None


def test_v2_encrypted_receipt_binds_local_integrity_authority(tmp_path: Path) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)
    release_contract = source_root / "scripts" / "release-contract.json"
    release_contract.write_text('{"contract_version":13}\n', encoding="utf-8")
    integrity_contract = source_root / "scripts" / "integrity-contract.json"
    bkenc_receipt = tmp_path / "candidate.zip.bkenc-receipt.json"
    bkenc_receipt.write_text("{}\n", encoding="utf-8")
    public_key = bytes(range(32))

    receipt["schema"] = "bidking_release_build_audit_receipt_v2"
    receipt["receipt_version"] = 2
    receipt.pop("native_binary_contract")
    receipt.pop("native_binary_receipt")
    receipt.pop("release_availability")
    receipt["encrypted"] = True
    _bind_protection_manifest(source_root, receipt)
    receipt["release_contract"] = {
        "path": "scripts/release-contract.json",
        "sha256": _sha256(release_contract),
    }
    receipt["local_integrity_contract"] = {
        "path": "scripts/integrity-contract.json",
        "sha256": _sha256(integrity_contract),
    }
    receipt["local_integrity"] = {
        "manifest_name": "integrity/local_integrity_manifest.json",
        "manifest_sha256": "1" * 64,
        "signature_name": "integrity/local_integrity_manifest.sig",
        "public_key_b64": base64.b64encode(public_key).decode("ascii"),
        "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
        "entry_count": 17,
        "native_gate_verified": True,
    }
    receipt["bkenc_receipt"] = {
        "name": bkenc_receipt.name,
        "sha256": _sha256(bkenc_receipt),
    }
    receipt["expected_manifest_fields"].update(
        {
            "Encrypted": "True",
            "PackageInputsDirty": "False",
            "EncryptedUploadConfig": "True",
            "ReleaseKeyCacheVerified": "True",
            "UploadConfigRoundTripVerified": "True",
            "BundledCABundle": "True",
            "LocalIntegrityVerified": "True",
            "LocalIntegrityPublicKeySHA256": hashlib.sha256(public_key).hexdigest(),
            "LocalIntegrityManifestSHA256": "1" * 64,
            "LocalIntegrityEntryCount": "17",
        }
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    loaded = _AUDIT.load_build_audit_receipt(receipt_path, source_root)

    assert loaded["encrypted"] is True
    assert loaded["local_integrity_contract"] == integrity_contract
    assert loaded["local_integrity"]["entry_count"] == 17

    release_contract.write_text('{"contract_version":14}\n', encoding="utf-8")
    receipt["release_contract"]["sha256"] = _sha256(release_contract)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="previous_encrypted_native_authority_missing"):
        _AUDIT.load_build_audit_receipt(receipt_path, source_root)

    release_contract.write_text('{"contract_version":13}\n', encoding="utf-8")
    receipt["release_contract"]["sha256"] = _sha256(release_contract)

    receipt["local_integrity"]["public_key_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_local_integrity_public_key"):
        _AUDIT.load_build_audit_receipt(receipt_path, source_root)


def test_signed_v3_receipt_is_recognized_but_never_reused_as_v4(
    tmp_path: Path,
) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)
    receipt["schema"] = "bidking_release_build_audit_receipt_v3"
    receipt["receipt_version"] = 3
    receipt.pop("release_availability")
    receipt["native_signing_policy"] = None
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="signed_build_receipt_not_current"):
        _AUDIT.load_build_audit_receipt(receipt_path, source_root)


def test_v4_encrypted_receipt_binds_unsigned_native_identities(
    tmp_path: Path,
) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)
    release_contract = source_root / "scripts" / "release-contract.json"
    integrity_contract = source_root / "scripts" / "integrity-contract.json"
    native_contract = source_root / "scripts" / "native-contract.json"
    bkenc_receipt = tmp_path / "candidate.zip.bkenc-receipt.json"
    native_receipt = tmp_path / "candidate.zip.native-binary-receipt.json"
    bkenc_receipt.write_text("{}\n", encoding="utf-8")
    native_receipt_payload = {
        "schema": "bidking_native_binary_receipt_v2",
        "receipt_version": 2,
        "distribution": "unsigned",
        "member_count": 116,
        "members": [],
        "contract_sha256": _sha256(native_contract),
    }
    native_receipt.write_text(
        json.dumps(native_receipt_payload),
        encoding="utf-8",
    )
    public_key = bytes(range(32))

    receipt["schema"] = "bidking_release_build_audit_receipt_v4"
    receipt["receipt_version"] = 4
    receipt.pop("release_availability")
    receipt["encrypted"] = True
    _bind_protection_manifest(source_root, receipt)
    receipt["release_contract"] = {
        "path": "scripts/release-contract.json",
        "sha256": _sha256(release_contract),
    }
    receipt["local_integrity_contract"] = {
        "path": "scripts/integrity-contract.json",
        "sha256": _sha256(integrity_contract),
    }
    receipt["local_integrity"] = {
        "manifest_name": "integrity/local_integrity_manifest.json",
        "manifest_sha256": "1" * 64,
        "signature_name": "integrity/local_integrity_manifest.sig",
        "public_key_b64": base64.b64encode(public_key).decode("ascii"),
        "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
        "entry_count": 17,
        "native_gate_verified": True,
    }
    receipt["native_binary_contract"] = {
        "path": "scripts/native-contract.json",
        "sha256": _sha256(native_contract),
    }
    receipt["native_binary_receipt"] = {
        "name": native_receipt.name,
        "sha256": _sha256(native_receipt),
    }
    receipt["bkenc_receipt"] = {
        "name": bkenc_receipt.name,
        "sha256": _sha256(bkenc_receipt),
    }
    receipt["expected_manifest_fields"].update(
        {
            "Encrypted": "True",
            "PackageInputsDirty": "False",
            "EncryptedUploadConfig": "True",
            "ReleaseKeyCacheVerified": "True",
            "UploadConfigRoundTripVerified": "True",
            "BundledCABundle": "True",
            "LocalIntegrityVerified": "True",
            "LocalIntegrityPublicKeySHA256": hashlib.sha256(public_key).hexdigest(),
            "LocalIntegrityManifestSHA256": "1" * 64,
            "LocalIntegrityEntryCount": "17",
            "NativeBinaryContractSHA256": _sha256(native_contract),
            "NativeBinaryDistribution": "unsigned",
            "NativeBinaryReceiptSHA256": _sha256(native_receipt),
            "NativeBinaryVerified": "True",
            "NativeBinaryCount": "116",
        }
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    loaded = _AUDIT.load_build_audit_receipt(receipt_path, source_root)
    assert loaded["local_integrity_contract"] == integrity_contract
    assert loaded["native_binary_contract"] == native_contract
    assert loaded["native_signing_policy"] is None
    assert loaded["native_binary_receipt"] == native_receipt

    release_contract.write_text('{"contract_version":16}\n', encoding="utf-8")
    receipt["release_contract"]["sha256"] = _sha256(release_contract)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(
        ValueError,
        match="previous_release_availability_authority_missing",
    ):
        _AUDIT.load_build_audit_receipt(receipt_path, source_root)
    release_contract.write_text('{"contract_version":15}\n', encoding="utf-8")
    receipt["release_contract"]["sha256"] = _sha256(release_contract)

    for invalid_version in (True, 1.0, "1", None):
        invalid_native_receipt = dict(native_receipt_payload)
        invalid_native_receipt["receipt_version"] = invalid_version
        native_receipt.write_text(
            json.dumps(invalid_native_receipt), encoding="utf-8"
        )
        receipt["native_binary_receipt"]["sha256"] = _sha256(native_receipt)
        receipt["expected_manifest_fields"]["NativeBinaryReceiptSHA256"] = (
            _sha256(native_receipt)
        )
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with pytest.raises(ValueError, match="native_binary_identity_mismatch"):
            _AUDIT.load_build_audit_receipt(receipt_path, source_root)

    for mutation in ("missing", "extra"):
        invalid_native_receipt = dict(native_receipt_payload)
        if mutation == "missing":
            invalid_native_receipt.pop("receipt_version")
        else:
            invalid_native_receipt["unreviewed_authority"] = None
        native_receipt.write_text(
            json.dumps(invalid_native_receipt), encoding="utf-8"
        )
        receipt["native_binary_receipt"]["sha256"] = _sha256(native_receipt)
        receipt["expected_manifest_fields"]["NativeBinaryReceiptSHA256"] = (
            _sha256(native_receipt)
        )
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with pytest.raises(ValueError, match="native_binary_identity_mismatch"):
            _AUDIT.load_build_audit_receipt(receipt_path, source_root)

    native_receipt.write_text(json.dumps(native_receipt_payload), encoding="utf-8")
    receipt["native_binary_receipt"]["sha256"] = _sha256(native_receipt)
    receipt["expected_manifest_fields"]["NativeBinaryReceiptSHA256"] = _sha256(
        native_receipt
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    _AUDIT.load_build_audit_receipt(receipt_path, source_root)

    v16_contract, availability_receipt = _valid_release_availability_receipt()
    release_contract.write_text(json.dumps(v16_contract), encoding="utf-8")
    receipt["schema"] = "bidking_release_build_audit_receipt_v5"
    receipt["receipt_version"] = 5
    receipt["release_availability"] = availability_receipt
    receipt["release_contract"]["sha256"] = _sha256(release_contract)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    loaded_v5 = _AUDIT.load_build_audit_receipt(receipt_path, source_root)

    assert loaded_v5["release_availability"]["status"] == "pass"
    assert len(loaded_v5["release_availability"]["captures"]) == 5


def test_legacy_encrypted_receipt_cannot_downgrade_v13_integrity(
    tmp_path: Path,
) -> None:
    source_root, receipt_path, receipt = _receipt_fixture(tmp_path)
    release_contract = source_root / "scripts" / "release-contract.json"
    bkenc_receipt = tmp_path / "candidate.zip.bkenc-receipt.json"
    bkenc_receipt.write_text("{}\n", encoding="utf-8")
    receipt["schema"] = "bidking_release_build_audit_receipt_v1"
    receipt["receipt_version"] = 1
    receipt.pop("local_integrity_contract")
    receipt.pop("local_integrity")
    receipt.pop("native_binary_contract")
    receipt.pop("native_binary_receipt")
    receipt.pop("release_availability")
    receipt["encrypted"] = True
    _bind_protection_manifest(source_root, receipt)
    receipt["release_contract"] = {
        "path": "scripts/release-contract.json",
        "sha256": _sha256(release_contract),
    }
    receipt["bkenc_receipt"] = {
        "name": bkenc_receipt.name,
        "sha256": _sha256(bkenc_receipt),
    }
    receipt["expected_manifest_fields"].update(
        {
            "Encrypted": "True",
            "PackageInputsDirty": "False",
            "EncryptedUploadConfig": "True",
            "ReleaseKeyCacheVerified": "True",
            "UploadConfigRoundTripVerified": "True",
            "BundledCABundle": "True",
        }
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(
        ValueError, match="legacy_encrypted_integrity_authority_missing"
    ):
        _AUDIT.load_build_audit_receipt(receipt_path, source_root)
