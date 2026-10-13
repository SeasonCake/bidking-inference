from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "activation_http_e2ee.json"
BUILD_PATH = ROOT / "scripts" / "build_live_portable.ps1"
CONTRACT_PATH = ROOT / "scripts" / "release_zip_contract.json"
AUDIT_PATH = ROOT / "scripts" / "audit_release_zip.py"
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.secure import activation_http_e2ee as protocol  # noqa: E402


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "audit_release_zip_http_e2ee_packaging",
        AUDIT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _identity_issues(raw: bytes, fields: dict[str, str]) -> list[str]:
    audit = _load_audit()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("activation_http_e2ee.json", raw)
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        members = {
            "activation_http_e2ee.json": [
                archive.getinfo("activation_http_e2ee.json")
            ]
        }
        return audit._activation_http_e2ee_identity_issues(
            archive,
            members,
            fields,
        )


def _fields(raw: bytes) -> dict[str, str]:
    config = protocol.parse_config_bytes(raw)
    return {
        "activationhttpe2eeconfigsha256": hashlib.sha256(raw).hexdigest(),
        "activationhttpe2eeenabled": "True" if config.enabled else "False",
        "activationhttpe2eekeyid": config.key_id if config.enabled else "disabled",
    }


def test_tracked_config_is_canonical_enabled_production_identity() -> None:
    raw = CONFIG_PATH.read_bytes()
    assert raw == (
        b'{"enabled":true,"key_id":"x25519-c92a2efc4a9f4118",'
        b'"schema_version":1,"server_public_key":'
        b'"G0jLiTH6n/UevqLmAHSZDtSqVxNUdf4DY5MLYd6qqw0=",'
        b'"url":"http://203.0.113.10:80/activate-e2ee"}\n'
    )
    config = protocol.parse_config_bytes(raw)
    assert config.enabled is True
    assert config.key_id == "x25519-c92a2efc4a9f4118"
    assert config.url == "http://203.0.113.10:80/activate-e2ee"


def test_activation_configs_are_lf_pinned_build_inputs() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    build = BUILD_PATH.read_text(encoding="utf-8")
    assert "activation_service_endpoints.json text eol=lf" in attributes
    assert "activation_http_e2ee.json text eol=lf" in attributes
    assert '".gitattributes", "LICENSE"' in build


def test_build_and_release_contract_bind_http_e2ee_config() -> None:
    build = BUILD_PATH.read_text(encoding="utf-8")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert '"activation_http_e2ee.json"' in build
    assert "ActivationHttpE2EEConfigSHA256:" in build
    assert "ActivationHttpE2EEEnabled:" in build
    assert "ActivationHttpE2EEKeyID:" in build
    assert contract["contract_version"] == 14
    assert "activation_http_e2ee.json" in contract["required_members"]["common"]
    assert contract["fixed_manifest_values"]["ActivationHttpE2EEEnabled"] == "True"
    assert contract["fixed_manifest_values"]["ActivationHttpE2EEKeyID"] == (
        "x25519-c92a2efc4a9f4118"
    )


def test_build_config_probes_survive_windows_powershell_argument_marshalling() -> None:
    build = BUILD_PATH.read_text(encoding="utf-8")
    assert (
        '$PythonSourceProbeLauncher = "import base64,sys;exec('
        'base64.b64decode(sys.argv.pop(1)))"'
    ) in build
    assert "-c $ActivationEndpointProbe " not in build
    assert "-c $ActivationHttpE2EEProbe " not in build
    assert "-E -s -c $PythonSourceProbeLauncher" not in build
    assert build.count("-E -B -c $PythonSourceProbeLauncher") == 2

    powershell = Path(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    if not powershell.is_file():
        pytest.skip("requires Windows PowerShell 5")

    source = (
        "import json,sys\n"
        'print(json.dumps({"value": sys.argv[1]}, '
        'sort_keys=True, separators=(",", ":")))\n'
    )
    command = (
        "$ErrorActionPreference='Stop';"
        "$launcher='import base64,sys;exec(base64.b64decode(sys.argv.pop(1)))';"
        "$encoded=[Convert]::ToBase64String("
        "[Text.Encoding]::UTF8.GetBytes($env:BIDKING_PROBE_SOURCE));"
        f"& '{sys.executable}' -E -c $launcher $encoded 'a b'"
    )
    completed = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "BIDKING_PROBE_SOURCE": source},
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.strip() == '{"value":"a b"}'


def test_artifact_identity_accepts_exact_tracked_enabled_config() -> None:
    raw = CONFIG_PATH.read_bytes()
    assert _identity_issues(raw, _fields(raw)) == []


def test_artifact_identity_accepts_same_enabled_config_without_trailing_lf() -> None:
    raw = (
        b'{"enabled":true,"key_id":"x25519-c92a2efc4a9f4118",'
        b'"schema_version":1,"server_public_key":'
        b'"G0jLiTH6n/UevqLmAHSZDtSqVxNUdf4DY5MLYd6qqw0=",'
        b'"url":"http://203.0.113.10:80/activate-e2ee"}'
    )
    assert _identity_issues(raw, _fields(raw)) == []


def test_artifact_identity_rejects_manifest_drift() -> None:
    raw = CONFIG_PATH.read_bytes()
    fields = _fields(raw)
    fields["activationhttpe2eeconfigsha256"] = "0" * 64
    fields["activationhttpe2eeenabled"] = "False"
    fields["activationhttpe2eekeyid"] = "x25519-0000000000000000"
    assert _identity_issues(raw, fields) == [
        "release_activation_http_e2ee_config_hash_mismatch",
        "release_activation_http_e2ee_config_enabled_mismatch",
        "release_activation_http_e2ee_config_key_mismatch",
    ]


def test_artifact_identity_rejects_invalid_or_missing_config() -> None:
    audit = _load_audit()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("activation_http_e2ee.json", b"{}")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        members = {
            "activation_http_e2ee.json": [
                archive.getinfo("activation_http_e2ee.json")
            ]
        }
        assert audit._activation_http_e2ee_identity_issues(
            archive,
            members,
            {},
        ) == ["release_activation_http_e2ee_config_invalid"]
        assert audit._activation_http_e2ee_identity_issues(
            archive,
            {},
            {},
        ) == ["release_activation_http_e2ee_config_missing"]
