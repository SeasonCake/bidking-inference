"""Fail-closed verifier for immutable release asset directories.

The tracked JSON manifest is the authority.  This tool never snapshots or updates
the manifest during a build; a deliberate source review is required whenever an
asset inventory changes.
"""

from __future__ import annotations

import argparse
from collections import Counter
import fnmatch
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError("asset root is missing")

    rows: list[tuple[str, str, int, str]] = []
    casefolded: set[str] = set()
    suffix_counts: Counter[str] = Counter()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError("asset inventory contains a symbolic link")
        relative = path.relative_to(root).as_posix()
        folded = relative.casefold()
        if folded in casefolded:
            raise ValueError("asset inventory contains a case-insensitive path collision")
        casefolded.add(folded)
        size = path.stat().st_size
        sha256 = _sha256_file(path)
        rows.append((folded, relative, size, sha256))
        suffix_counts[path.suffix.lower()] += 1

    rows.sort(key=lambda row: row[0])
    serialized = "".join(
        f"{relative}|{size}|{sha256}\n"
        for _folded, relative, size, sha256 in rows
    ).encode("utf-8")
    return {
        "file_count": len(rows),
        "total_bytes": sum(row[2] for row in rows),
        "inventory_sha256": hashlib.sha256(serialized).hexdigest(),
        "suffix_counts": dict(sorted(suffix_counts.items())),
        "files": {
            relative: {"bytes": size, "sha256": sha256}
            for _folded, relative, size, sha256 in rows
        },
    }


def verify(root: Path, manifest_path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("release asset manifest is unreadable") from exc

    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported release asset manifest schema")
    asset_id = manifest.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise ValueError("release asset manifest has no asset_id")

    actual = inventory(root)
    expected = manifest.get("inventory")
    if not isinstance(expected, dict):
        raise ValueError("release asset manifest has no inventory")
    for key in ("file_count", "total_bytes", "inventory_sha256"):
        if actual[key] != expected.get(key):
            raise ValueError(f"release asset inventory mismatch: {key}")

    expected_suffixes = manifest.get("suffix_counts")
    if expected_suffixes is not None:
        if not isinstance(expected_suffixes, dict):
            raise ValueError("release asset suffix_counts must be an object")
        normalized_suffixes = {
            str(key).lower(): int(value) for key, value in expected_suffixes.items()
        }
        if actual["suffix_counts"] != normalized_suffixes:
            raise ValueError("release asset inventory mismatch: suffix_counts")

    required_files = manifest.get("required_files", {})
    if not isinstance(required_files, dict):
        raise ValueError("release asset required_files must be an object")
    for relative, expected_file in required_files.items():
        if not isinstance(relative, str) or not isinstance(expected_file, dict):
            raise ValueError("release asset required_files entry is invalid")
        actual_file = actual["files"].get(relative)
        if actual_file is None:
            raise ValueError("release asset required file is missing")
        for key in ("bytes", "sha256"):
            if actual_file.get(key) != expected_file.get(key):
                raise ValueError("release asset required file identity mismatch")

    forbidden_globs = manifest.get("forbidden_globs", [])
    if not isinstance(forbidden_globs, list) or not all(
        isinstance(pattern, str) for pattern in forbidden_globs
    ):
        raise ValueError("release asset forbidden_globs must be a string array")
    for relative in actual["files"]:
        lowered = relative.casefold()
        if any(fnmatch.fnmatch(lowered, pattern.casefold()) for pattern in forbidden_globs):
            raise ValueError("release asset inventory contains a forbidden file")

    return {
        "asset_id": asset_id,
        "version": manifest.get("version"),
        "file_count": actual["file_count"],
        "total_bytes": actual["total_bytes"],
        "inventory_sha256": actual["inventory_sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = verify(args.root.resolve(), args.manifest.resolve())
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    else:
        print(
            "[OK] release asset verified: "
            f"{result['asset_id']} files={result['file_count']} "
            f"bytes={result['total_bytes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
