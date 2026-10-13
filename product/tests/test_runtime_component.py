from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from scripts.build_release_runtime_component import build_component
from scripts.runtime_component import ComponentError, ensure_component
from scripts.verify_release_asset_inventory import inventory


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    (source / "_internal").mkdir(parents=True)
    (source / "runner.exe").write_bytes(b"runner-v1")
    (source / "_internal" / "library.bin").write_bytes(b"library-v1")
    actual = inventory(source)
    manifest = tmp_path / "asset-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_id": "fixture-runtime",
                "version": "1.2.3",
                "inventory": {
                    "file_count": actual["file_count"],
                    "total_bytes": actual["total_bytes"],
                    "inventory_sha256": actual["inventory_sha256"],
                },
                "required_files": {
                    "runner.exe": actual["files"]["runner.exe"],
                },
            }
        ),
        encoding="utf-8",
    )
    return source, manifest


def _build(tmp_path: Path) -> tuple[Path, Path]:
    source, manifest = _fixture(tmp_path)
    descriptor = tmp_path / "descriptor.json"
    result = build_component(
        source_root=source,
        asset_manifest_path=manifest,
        output_dir=tmp_path / "dist",
        descriptor_path=descriptor,
        mirror_bases=["https://downloads.example.test/components"],
    )
    return descriptor, Path(str(result["archive_path"]))


def test_component_build_is_reproducible_and_installs_verified_cache(
    tmp_path: Path,
) -> None:
    descriptor, archive = _build(tmp_path)
    first_bytes = archive.read_bytes()

    source = tmp_path / "source"
    manifest = tmp_path / "asset-manifest.json"
    second = build_component(
        source_root=source,
        asset_manifest_path=manifest,
        output_dir=tmp_path / "dist-2",
        descriptor_path=tmp_path / "descriptor-2.json",
        mirror_bases=["https://downloads.example.test/components"],
    )
    assert Path(str(second["archive_path"])).read_bytes() == first_bytes

    cache_root = tmp_path / "cache"
    payload = ensure_component(
        descriptor,
        cache_root=cache_root,
        local_archive=archive,
    )
    assert (payload / "runner.exe").read_bytes() == b"runner-v1"
    assert (payload.parent / "ready.json").is_file()

    # A changed cached byte cannot persist as a trusted runtime. The same immutable
    # archive repairs the content-addressed cache before it is returned.
    (payload / "runner.exe").write_bytes(b"tampered")
    repaired = ensure_component(
        descriptor,
        cache_root=cache_root,
        local_archive=archive,
    )
    assert repaired == payload
    assert (repaired / "runner.exe").read_bytes() == b"runner-v1"

    (payload / "_internal" / "library.bin").unlink()
    repaired_missing = ensure_component(
        descriptor,
        cache_root=cache_root,
        local_archive=archive,
    )
    assert (repaired_missing / "_internal" / "library.bin").read_bytes() == b"library-v1"

    (payload / "unexpected.bin").write_bytes(b"not-in-manifest")
    repaired_extra = ensure_component(
        descriptor,
        cache_root=cache_root,
        local_archive=archive,
    )
    assert not (repaired_extra / "unexpected.bin").exists()


def test_component_archive_tamper_and_descriptor_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    descriptor, archive = _build(tmp_path)
    tampered = tmp_path / "tampered.zip"
    tampered.write_bytes(archive.read_bytes() + b"x")
    with pytest.raises(ComponentError, match="size mismatch"):
        ensure_component(
            descriptor,
            cache_root=tmp_path / "cache-tampered",
            local_archive=tampered,
        )

    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    payload["inventory"]["file_count"] += 1
    mismatched = tmp_path / "descriptor-mismatch.json"
    mismatched.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ComponentError, match="file_count mismatch"):
        ensure_component(
            mismatched,
            cache_root=tmp_path / "cache-mismatch",
            local_archive=archive,
        )


def test_component_rejects_unsafe_members_even_with_matching_archive_identity(
    tmp_path: Path,
) -> None:
    descriptor, _archive = _build(tmp_path)
    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("runtime/../escape.bin", b"x")

    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    from scripts.runtime_component import _sha256_file

    payload["archive"]["bytes"] = unsafe.stat().st_size
    payload["archive"]["sha256"] = _sha256_file(unsafe)
    payload["inventory"]["file_count"] = 1
    payload["inventory"]["total_bytes"] = 1
    unsafe_descriptor = tmp_path / "unsafe-descriptor.json"
    unsafe_descriptor.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ComponentError, match="unsafe member"):
        ensure_component(
            unsafe_descriptor,
            cache_root=tmp_path / "cache-unsafe",
            local_archive=unsafe,
        )


def test_component_mirrors_must_be_https(tmp_path: Path) -> None:
    source, manifest = _fixture(tmp_path)
    with pytest.raises(ValueError, match="requires at least one"):
        build_component(
            source_root=source,
            asset_manifest_path=manifest,
            output_dir=tmp_path / "empty-dist",
            descriptor_path=tmp_path / "empty-descriptor.json",
            mirror_bases=[],
        )
    with pytest.raises(ValueError, match="must use HTTPS"):
        build_component(
            source_root=source,
            asset_manifest_path=manifest,
            output_dir=tmp_path / "dist",
            descriptor_path=tmp_path / "descriptor.json",
            mirror_bases=["http://downloads.example.test/components"],
        )
