#!/usr/bin/env python3
"""Run the public candidate's deterministic local verification."""

from __future__ import annotations

import compileall
import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml", ".json"}
BINARY_SUFFIXES = {".dll", ".exe", ".pyd", ".so", ".dylib", ".zip", ".tar", ".7z", ".rar"}
DOCUMENTATION_IMAGES = {
    "docs/assets/screenshots/bidking-ui-compact-dark-historical.png": {
        "bytes": 27_416,
        "width": 428,
        "height": 455,
        "sha256": "3c96e862909e0d3d2701446098f118a4a756b7e50c2564c01d9c17af7ba0c122",
    },
    "docs/assets/screenshots/bidking-live-gameplay-historical.png": {
        "bytes": 5_539_463,
        "width": 3_834,
        "height": 1_872,
        "sha256": "de5008799cfec1b40d5706bc1f1058de00889d58c9f56e2313391a12ae2006d5",
    },
}
LEGACY_SNAPSHOTS = {
    "legacy/source-v0.2.0-hotfix1": {
        "files": 56,
        "bytes": 1_589_297,
        "manifest_sha256": "4a3af572d1376e7d268e9b60b32499f3fa97f2746144e7377d1d87d548b48247",
        "suffixes": {".py"},
    },
    "legacy/data-v0.2.7-hotfix3": {
        "files": 7,
        "bytes": 641_335,
        "manifest_sha256": "1cb2ff181202b370d1e0c6da5cc5667a8719be49e91b465ca66be506d5a15f99",
        "suffixes": {".json"},
    },
}
FORBIDDEN_PNG_METADATA_CHUNKS = {b"eXIf", b"iTXt", b"tEXt", b"zTXt"}
BANNED_SOURCE_FRAGMENTS = (
    "bidking_lab",
    "data/processed",
    "data\\processed",
    "windivert",
    "mitmproxy",
    "activation_core",
    "release_zip_contract",
    "c:\\users\\",
    "c:\\tmp\\",
)
REQUIRED_PUBLIC_FILES = (
    "README.md",
    "README.en.md",
    "README.zh-CN.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "DCO",
    "LICENSE",
    "MAINTAINING.md",
    "SECURITY.md",
    "SUPPORT.md",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    "docs/PUBLIC_API.md",
    "docs/INPUT_SCHEMA.md",
    "docs/assets/screenshots/README.md",
    "legacy/README.md",
)


def _tree_manifest_digest(root: Path) -> tuple[int, int, str]:
    files = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    lines = [
        f"{path.relative_to(root).as_posix()}\t{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in files
    ]
    payload = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
    return len(files), sum(path.stat().st_size for path in files), hashlib.sha256(payload).hexdigest()


def legacy_snapshot_errors() -> list[str]:
    errors: list[str] = []
    allowed_url = "https://github.com/SeasonCake/bidking-lab"
    banned_fragments = (
        "http://",
        "c:\\users\\",
        "d:\\",
        "g:\\",
        "master_key",
        "activation_core",
        "upload_config",
        "authorization:",
        "-----begin private key-----",
    )
    for relative, expected in LEGACY_SNAPSHOTS.items():
        root = ROOT / relative
        if not root.is_dir():
            errors.append(f"legacy snapshot is missing: {relative}")
            continue
        files = [
            path
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ]
        count, byte_count, digest = _tree_manifest_digest(root)
        if count != expected["files"]:
            errors.append(f"legacy snapshot file count changed: {relative}")
        if byte_count != expected["bytes"]:
            errors.append(f"legacy snapshot byte count changed: {relative}")
        if digest != expected["manifest_sha256"]:
            errors.append(f"legacy snapshot manifest digest changed: {relative}")
        for path in files:
            nested = path.relative_to(ROOT).as_posix()
            if path.suffix.casefold() not in expected["suffixes"]:
                errors.append(f"unexpected legacy file type: {nested}")
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
            lowered = text.casefold().replace(allowed_url.casefold(), "")
            for fragment in banned_fragments:
                if fragment in lowered:
                    errors.append(f"private fragment {fragment!r} in {nested}")
            if "https://" in lowered:
                errors.append(f"unreviewed URL in legacy snapshot: {nested}")
            if path.suffix.casefold() == ".json":
                try:
                    json.loads(text)
                except json.JSONDecodeError:
                    errors.append(f"invalid legacy JSON: {nested}")
    return errors


def documentation_image_errors() -> list[str]:
    errors: list[str] = []
    actual_images = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "docs").rglob("*.png")
        if path.is_file()
    }
    expected_images = set(DOCUMENTATION_IMAGES)
    for relative in sorted(actual_images - expected_images):
        errors.append(f"documentation image is not allowlisted: {relative}")
    for relative in sorted(expected_images - actual_images):
        errors.append(f"allowlisted documentation image is missing: {relative}")

    for relative, expected in DOCUMENTATION_IMAGES.items():
        path = ROOT / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        if len(data) != expected["bytes"]:
            errors.append(f"documentation image byte count changed: {relative}")
        if hashlib.sha256(data).hexdigest() != expected["sha256"]:
            errors.append(f"documentation image SHA-256 changed: {relative}")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            errors.append(f"documentation image is not a PNG: {relative}")
            continue

        position = 8
        dimensions: tuple[int, int] | None = None
        found_iend = False
        while position + 12 <= len(data):
            length = struct.unpack(">I", data[position : position + 4])[0]
            kind = data[position + 4 : position + 8]
            chunk_end = position + 12 + length
            if chunk_end > len(data):
                errors.append(f"documentation PNG is truncated: {relative}")
                break
            if kind == b"IHDR" and length == 13:
                dimensions = struct.unpack(">II", data[position + 8 : position + 16])
            if kind in FORBIDDEN_PNG_METADATA_CHUNKS:
                errors.append(
                    f"documentation PNG contains private-capable metadata {kind!r}: {relative}"
                )
            position = chunk_end
            if kind == b"IEND":
                found_iend = True
                break
        if not found_iend:
            errors.append(f"documentation PNG has no IEND chunk: {relative}")
        if dimensions != (expected["width"], expected["height"]):
            errors.append(f"documentation image dimensions changed: {relative}")
    return errors


def public_boundary_errors() -> list[str]:
    errors = documentation_image_errors() + legacy_snapshot_errors()
    for relative in REQUIRED_PUBLIC_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"required public-maintenance file is missing: {relative}")
    if not (ROOT / "LICENSE").is_file():
        errors.append("final LICENSE is missing")
    if (ROOT / "LICENSE-DECISION.md").exists():
        errors.append("obsolete license decision placeholder is still present")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if 'license = "MIT"' not in pyproject:
        errors.append("pyproject does not declare the MIT SPDX expression")
    if 'license-files = ["LICENSE", "NOTICE.md"]' not in pyproject:
        errors.append("pyproject does not bind package metadata to public license files")
    relationship = (ROOT / "PROJECT_RELATIONSHIP.md").read_text(encoding="utf-8")
    if "evidence-first-agent-skills" not in relationship:
        errors.append("companion skills repository is not linked")
    scan_roots = [ROOT / "src", ROOT / "tests", ROOT / "examples", ROOT / "docs"]
    for scan_root in scan_roots:
        for path in scan_root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if path.suffix.casefold() in BINARY_SUFFIXES:
                errors.append(f"binary is outside the public boundary: {relative}")
                continue
            if path.suffix.casefold() in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="replace").casefold()
                for fragment in BANNED_SOURCE_FRAGMENTS:
                    if fragment.casefold() in text:
                        errors.append(f"private fragment {fragment!r} in {relative}")
    for fixture_path in sorted((ROOT / "examples").glob("*.json")):
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        if type(fixture) is not dict or fixture.get("synthetic") is not True:
            relative = fixture_path.relative_to(ROOT).as_posix()
            errors.append(f"example fixture is not explicitly synthetic: {relative}")
    return errors


def main() -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    if not compileall.compile_dir(ROOT / "src", quiet=1):
        raise SystemExit("compileall failed")
    if not compileall.compile_dir(
        ROOT / "legacy" / "source-v0.2.0-hotfix1", quiet=1
    ):
        raise SystemExit("legacy compileall failed")
    errors = public_boundary_errors()
    if errors:
        raise SystemExit("\n".join(errors))
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        stderr=subprocess.PIPE,
    )
    sys.stderr.write(completed.stderr)
    subprocess.run(
        [sys.executable, "scripts/run_example.py", "examples/synthetic_session.json"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/run_example.py",
            "examples/synthetic_multidimensional.json",
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    test_lines = [line for line in completed.stderr.splitlines() if line.startswith("test_")]
    print(
        json.dumps(
            {"result": "PASS", "tests": len(test_lines), "private_boundary_errors": 0}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
