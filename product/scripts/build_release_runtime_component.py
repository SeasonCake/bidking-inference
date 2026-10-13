"""Build a deterministic, immutable release runtime component archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlparse
import zipfile

try:
    from scripts.verify_release_asset_inventory import inventory, verify
except ModuleNotFoundError:  # direct ``python scripts/...py`` execution
    from verify_release_asset_inventory import inventory, verify


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mirror_url(base: str, file_name: str) -> str:
    normalized = base.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("runtime component mirror base must use HTTPS")
    return f"{normalized}/{quote(file_name)}"


def build_component(
    *,
    source_root: Path,
    asset_manifest_path: Path,
    output_dir: Path,
    descriptor_path: Path,
    mirror_bases: list[str],
) -> dict[str, object]:
    if not mirror_bases:
        raise ValueError("runtime component requires at least one HTTPS mirror")
    source_root = source_root.resolve()
    asset_manifest_path = asset_manifest_path.resolve()
    output_dir = output_dir.resolve()
    descriptor_path = descriptor_path.resolve()

    verified = verify(source_root, asset_manifest_path)
    asset_manifest = json.loads(asset_manifest_path.read_text(encoding="utf-8"))
    actual = inventory(source_root)
    inventory_sha = str(verified["inventory_sha256"]).lower()
    version = str(verified.get("version") or "unknown")
    component_id = "bidking-local-compat-runtime"
    archive_name = f"bidking-runtime-{version}-{inventory_sha[:12]}.zip"
    archive_path = output_dir / archive_name

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_archive = archive_path.with_name(f"{archive_path.name}.part")
    temporary_archive.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary_archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for relative in sorted(actual["files"], key=str.casefold):
                path = source_root.joinpath(*relative.split("/"))
                info = zipfile.ZipInfo(
                    f"runtime/{relative}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                info.external_attr = 0
                with path.open("rb") as source, archive.open(info, "w") as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(chunk)
        temporary_archive.replace(archive_path)
    finally:
        temporary_archive.unlink(missing_ok=True)

    descriptor = {
        "schema_version": 1,
        "component_id": component_id,
        "version": version,
        "inventory": {
            "file_count": actual["file_count"],
            "total_bytes": actual["total_bytes"],
            "inventory_sha256": inventory_sha,
        },
        "suffix_counts": actual["suffix_counts"],
        "required_files": asset_manifest.get("required_files", {}),
        "archive": {
            "file_name": archive_name,
            "bytes": archive_path.stat().st_size,
            "sha256": _sha256_file(archive_path),
            "payload_prefix": "runtime",
        },
        "mirrors": [_mirror_url(base, archive_name) for base in mirror_bases],
    }
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_text(
        json.dumps(descriptor, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "archive_path": str(archive_path),
        "descriptor_path": str(descriptor_path),
        "component_id": component_id,
        "version": version,
        "file_count": actual["file_count"],
        "total_bytes": actual["total_bytes"],
        "inventory_sha256": inventory_sha,
        "archive_bytes": descriptor["archive"]["bytes"],
        "archive_sha256": descriptor["archive"]["sha256"],
        "mirrors": descriptor["mirrors"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--asset-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--descriptor-out", required=True, type=Path)
    parser.add_argument("--mirror-base", action="append", default=[])
    args = parser.parse_args(argv)

    try:
        result = build_component(
            source_root=args.source_root,
            asset_manifest_path=args.asset_manifest,
            output_dir=args.output_dir,
            descriptor_path=args.descriptor_out,
            mirror_bases=list(args.mirror_base),
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
