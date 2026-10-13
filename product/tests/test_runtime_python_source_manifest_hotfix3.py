from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = ROOT / "scripts" / "runtime_python_source_manifest.json"
INVENTORY_MANIFEST = ROOT / "scripts" / "runtime_python_inventory_manifest.json"
RELEASE_CONTRACT = ROOT / "scripts" / "release_zip_contract.json"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_runtime_python_source_is_exact_cp313_patch_line() -> None:
    source = _load(SOURCE_MANIFEST)
    contract = _load(RELEASE_CONTRACT)
    python = source["python"]

    assert source["schema"] == "bidking_runtime_python_source_v1"
    assert source["manifest_version"] == 1
    assert python["version"] == "3.13.15"
    assert python["cache_tag"] == "cpython-313"
    assert python["abi_tag"] == "cp313-win_amd64"
    assert python["platform"] == "win-amd64"
    assert python["release_url"] == (
        "https://www.python.org/downloads/release/python-31315/"
    )
    assert python["embed"] == {
        "filename": "python-3.13.15-embed-amd64.zip",
        "url": (
            "https://www.python.org/ftp/python/3.13.15/"
            "python-3.13.15-embed-amd64.zip"
        ),
        "bytes": 11009825,
        "sha256": (
            "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
        ),
        "python_exe_sha256": (
            "85b71d8c6ec1905935f74be0c9869aae198d00e98f39df699ec66f9c5a84cecd"
        ),
    }
    assert source["runtime_dependencies"] == contract["runtime_dependencies"]
    assert source["layout_tools"] == {"pip": "26.2.1"}


def test_runtime_python_wheel_freeze_covers_runtime_and_build_tools() -> None:
    source = _load(SOURCE_MANIFEST)
    wheels = source["wheels"]
    assert len(wheels) == 15
    assert len({row["distribution"] for row in wheels}) == len(wheels)
    assert len({row["filename"] for row in wheels}) == len(wheels)

    by_role: dict[str, dict[str, str]] = {"runtime": {}, "build": {}}
    for row in wheels:
        assert row["role"] in by_role
        assert isinstance(row["bytes"], int) and row["bytes"] > 0
        assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        assert row["filename"].endswith(".whl")
        by_role[row["role"]][row["distribution"]] = row["version"]

    assert by_role["runtime"] == source["runtime_dependencies"]
    assert by_role["build"] == source["build_tools"]


def test_release_contract_binds_runtime_python_source_and_inventory() -> None:
    source_sha256 = hashlib.sha256(SOURCE_MANIFEST.read_bytes()).hexdigest()
    inventory = _load(INVENTORY_MANIFEST)
    inventory_sha256 = hashlib.sha256(
        json.dumps(
            inventory,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    contract = _load(RELEASE_CONTRACT)
    fixed = contract["fixed_manifest_values"]

    assert contract["contract_version"] == 25
    assert "RuntimePythonSourceManifestSHA256" in contract[
        "required_manifest_fields"
    ]
    assert fixed["RuntimePythonSourceManifestSHA256"] == source_sha256
    assert inventory == {
        "approval_version": 1,
        "directory_count": 217,
        "file_count": 2485,
        "inventory_sha256": (
            "3697aa54b4dd4d0bb442d7e7156a89d49dae20a2e940828c758b2c0e18a5b82e"
        ),
        "prefix": "runtime/python/",
        "schema": "bidking_runtime_inventory_approval_v1",
        "total_bytes": 106141084,
    }
    assert fixed["RuntimeInventoryApprovalSHA256"] == inventory_sha256
    assert fixed["PythonVersion"] == "3.13.15"
    assert contract["fixed_member_sha256"]["runtime/python/python.exe"] == (
        "85b71d8c6ec1905935f74be0c9869aae198d00e98f39df699ec66f9c5a84cecd"
    )


def test_live_build_fail_closed_consumes_runtime_python_source() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    legacy = (ROOT / "scripts" / "build_release_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '[string]$PythonVersion = "3.13.15"' in build
    assert '[string]$BuildPython = "C:\\Python31315\\python.exe"' in build
    assert '"scripts/runtime_python_source_manifest.json"' in build
    assert "$BuildPython -E -s -c" in build
    assert "python.build_layout.python_exe_sha256" in build
    assert "python.embed.sha256" in build
    assert "python.embed.python_exe_sha256" in build
    assert "runtime dependency source 与 release contract 不一致" in build
    assert "RuntimePythonSourceManifestSHA256:" in build
    assert build.index("$embedSHA256 -cne") < build.index("Expand-Archive")
    assert '[string]$PythonVersion = "3.13.15"' in legacy
