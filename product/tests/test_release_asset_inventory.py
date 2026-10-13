from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from scripts.verify_release_asset_inventory import inventory, verify


ROOT = Path(__file__).resolve().parents[1]


def _write_manifest(path: Path, root: Path, **extra: object) -> Path:
    current = inventory(root)
    payload = {
        "schema_version": 1,
        "asset_id": "test-assets",
        "inventory": {
            "file_count": current["file_count"],
            "total_bytes": current["total_bytes"],
            "inventory_sha256": current["inventory_sha256"],
        },
        **extra,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_inventory_known_good_and_changed_byte_known_fail(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "nested").mkdir(parents=True)
    (assets / "a.bin").write_bytes(b"one")
    (assets / "nested" / "b.bin").write_bytes(b"two")
    manifest = _write_manifest(tmp_path / "manifest.json", assets)

    assert verify(assets, manifest)["file_count"] == 2

    (assets / "a.bin").write_bytes(b"ONE")
    with pytest.raises(ValueError, match="inventory_sha256"):
        verify(assets, manifest)


def test_inventory_extra_file_and_missing_file_fail_closed(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "required.bin").write_bytes(b"required")
    manifest = _write_manifest(tmp_path / "manifest.json", assets)

    (assets / "extra.bin").write_bytes(b"extra")
    with pytest.raises(ValueError, match="file_count"):
        verify(assets, manifest)

    (assets / "extra.bin").unlink()
    (assets / "required.bin").unlink()
    with pytest.raises(ValueError, match="file_count"):
        verify(assets, manifest)


def test_forbidden_private_runtime_artifact_fails_closed(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "mitmproxy-ca-cert.cer").write_bytes(b"private")
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        assets,
        forbidden_globs=["*mitmproxy-ca*"],
    )

    with pytest.raises(ValueError, match="forbidden file"):
        verify(assets, manifest)


def test_pet_asset_manifest_matches_current_source_tree() -> None:
    result = verify(
        ROOT / "hero_ref" / "assets" / "pet_companion",
        ROOT / "scripts" / "pet_companion_assets_manifest.json",
    )

    assert result == {
        "asset_id": "pet-companion-assets",
        "version": "0.3.0-hotfix1-settlement-reactions",
        "file_count": 150,
        "total_bytes": 6_407_775,
        "inventory_sha256": (
            "06ae02428b968873778bfe1d3c64506db19ef156d1a495788b37ca281a0e3e96"
        ),
    }


def test_pet_asset_git_archive_preserves_manifest_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    readme_rel = Path("hero_ref/assets/pet_companion/README.md")
    readme = repo / readme_rel
    readme.parent.mkdir(parents=True)
    shutil.copy2(ROOT / ".gitattributes", repo / ".gitattributes")
    shutil.copy2(ROOT / readme_rel, readme)

    commands = (
        ["git", "init", "--quiet"],
        ["git", "config", "core.autocrlf", "true"],
        ["git", "config", "user.name", "release-test"],
        ["git", "config", "user.email", "release-test@invalid"],
        ["git", "add", ".gitattributes", readme_rel.as_posix()],
        ["git", "commit", "--quiet", "-m", "fixture"],
    )
    for command in commands:
        result = subprocess.run(command, cwd=repo, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr

    archive = tmp_path / "source.zip"
    result = subprocess.run(
        ["git", "archive", "--format=zip", f"--output={archive}", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    with zipfile.ZipFile(archive) as source_zip:
        assert source_zip.read(readme_rel.as_posix()) == readme.read_bytes()


def test_runtime_manifest_rejects_incomplete_payload(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = ROOT / "dist" / "verification" / "mitmproxy-runtime-12.2.3-20260723"
    if source.is_dir():
        shutil.copy2(source / "mitmdump.exe", runtime / "mitmdump.exe")
    else:
        (runtime / "mitmdump.exe").write_bytes(b"synthetic")

    with pytest.raises(ValueError, match="inventory mismatch"):
        verify(runtime, ROOT / "scripts" / "mitmproxy_runtime_manifest.json")


def test_pyinstaller_build_verifies_pet_assets_and_rejects_onefile() -> None:
    build = (ROOT / "hero_ref" / "build_ahmad_ref_ui_exe.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "--add-data" in build
    assert "$PetAssets;hero_ref\\assets\\pet_companion" in build
    assert "OneFile 输出无法在构建后逐文件核对 Pet 素材 inventory" in build
    assert "$PackagedPetCandidates" in build
    assert "PyInstaller Pet asset inventory verification failed" in build
