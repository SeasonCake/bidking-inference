#!/usr/bin/env python3
"""Run the public candidate's deterministic local verification."""

from __future__ import annotations

import compileall
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml", ".json", ".cs", ".csproj",
                 ".ps1", ".cpp", ".c", ".h", ".hpp", ".txt", ".tsv", ".csv", ".svg"}
BINARY_SUFFIXES = {".dll", ".exe", ".pyd", ".pdb", ".so", ".dylib", ".zip", ".tar", ".7z", ".rar"}
DOCUMENTATION_IMAGES = {
    "docs/assets/charts/penalty-teaching.png": {
        "bytes": 73_956, "width": 1500, "height": 810,
        "sha256": "ecf01c85d245b0b3bb1cecfbb8b5b79b1b7a5ad056a1af4cfbddfa34cf560379",
    },
    "docs/assets/charts/quality-mix.png": {
        "bytes": 78_388, "width": 1500, "height": 810,
        "sha256": "2e996a394796775f53b1e17b655c2f0ceebab3408f0f7fa64c5bd2a281dd2b67",
    },
    "docs/assets/charts/session-coverage.png": {
        "bytes": 49_082, "width": 1500, "height": 810,
        "sha256": "af30250294999a53f213d6fc904ec9eb74d6f65decdf282c7e7b2a700a685ea9",
    },
    "docs/assets/charts/catalog-coverage.png": {
        "bytes": 48_626, "width": 1500, "height": 810,
        "sha256": "ab7a178c84a02927a086fecd8c39298c7ede9c14f7813b2f8e9a85d5a2d730e6",
    },
    "docs/assets/charts/aisha-real-synthetic.png": {
        "bytes": 58_806, "width": 1500, "height": 810,
        "sha256": "fb222978219dc3360bf23311b63216944d0edf93356d5b722c1f051040df2a30",
    },
    "docs/assets/screenshots/bidking-v0.3.4-standby.png": {
        "bytes": 150_824,
        "width": 996,
        "height": 864,
        "sha256": "77405db63d9d2b1a61470c01b98026f8003ff082d62d23e4bbf0ff6a88ea16e8",
    },
    "docs/assets/screenshots/bidking-v0.3.4-live-bidding.png": {
        "bytes": 2_910_877,
        "width": 1_905,
        "height": 1_362,
        "sha256": "f92c78f221b20371abd457913b2967c1665cf1580e5586aca8218a94d154c91d",
    },
    "docs/assets/screenshots/bidking-v0.3.4-settlement.png": {
        "bytes": 2_689_792,
        "width": 1_910,
        "height": 1_249,
        "sha256": "3c84d46f4926a5cfcede63149d37980aeb79a79a3807d6a0eef36ed6531b7b96",
    },
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
    "docs/research/README.zh-CN.md",
    "docs/research/README.md",
    "docs/research/PROVENANCE.json",
    ".github/ISSUE_TEMPLATE/research_question.yml",
)


def _safe_relative(value: object) -> bool:
    return (isinstance(value, str) and bool(value)
            and re.fullmatch(r"[A-Za-z0-9_. /-]+", value) is not None
            and not value.startswith(("/", " "))
            and all(part not in ("", ".", "..") for part in value.split("/")))


def text_boundary_errors(relative: str, content: str) -> list[str]:
    # This exact already-public snapshot is a legitimate research input.
    content = content.replace("legacy/data-v0.2.7-hotfix3/data/processed", "public-legacy-data")
    if relative == "docs/research/PROVENANCE.json":
        # Source-relative locators in structured provenance are metadata, not imports.
        # Only the path field receives this exception; prose, code and other fields do not.
        try:
            document = json.loads(content)
            for entry in document.get("entries", []):
                for source in entry.get("sources", []):
                    if _safe_relative(source.get("path")):
                        source["path"] = "reviewed-source-relative-locator"
            content = json.dumps(document, ensure_ascii=False)
        except (json.JSONDecodeError, AttributeError, TypeError):
            return ["invalid structured research provenance"]
    lowered = content.casefold()
    return [f"private fragment {fragment!r} in {relative}"
            for fragment in BANNED_SOURCE_FRAGMENTS if fragment.casefold() in lowered]


def research_manifest_errors(manifest: dict, root: Path) -> list[str]:
    """Check declared source identities and exact fixture bytes; no private repo required."""
    errors = []
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest.get("schema_version") != 1):
        return ["research provenance requires schema_version=1"]
    entries, fixtures = manifest.get("entries"), manifest.get("fixtures")
    if not isinstance(entries, list) or not entries or not isinstance(fixtures, list):
        return ["research provenance requires entries and fixture list"]
    ids, covered = set(), set()
    kinds = {"historical_adaptation", "synthetic_teaching", "historical_aggregate",
             "historical_derivative", "research_scaffolding"}
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("invalid research entry")
            continue
        identity = entry.get("id")
        if not isinstance(identity, str) or not identity or identity in ids:
            errors.append("missing or duplicate research entry id")
            continue
        ids.add(identity)
        if entry.get("kind") not in kinds or not entry.get("adaptation"):
            errors.append(f"research classification or adaptation missing: {identity}")
        sources = entry.get("sources", [])
        if not isinstance(sources, list):
            errors.append(f"invalid research sources: {identity}")
            continue
        if entry.get("kind") in {"historical_adaptation", "historical_aggregate"} and not sources:
            errors.append(f"historical research source missing: {identity}")
        for source in sources:
            if (not isinstance(source, dict) or not _safe_relative(source.get("path"))
                    or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit", "")))
                    or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("git_blob", "")))):
                errors.append(f"invalid research source identity: {identity}")
        paths = entry.get("files", [])
        if not isinstance(paths, list) or not paths:
            errors.append(f"research entry has no files: {identity}")
            continue
        for relative in paths:
            if not _safe_relative(relative) or not (root / relative).is_file():
                errors.append(f"invalid or missing research file in {identity}")
            else:
                covered.add(relative)
    actual = {p.relative_to(root).as_posix() for p in (root / "research").rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    for relative in sorted(actual - covered):
        errors.append(f"research file has no provenance entry: {relative}")
    fixture_paths = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not _safe_relative(fixture.get("path")):
            errors.append("invalid research fixture entry")
            continue
        relative = fixture["path"]
        if relative in fixture_paths:
            errors.append(f"duplicate research fixture: {relative}")
        fixture_paths.add(relative)
        path = root / relative
        if fixture.get("kind") not in {"synthetic", "historical_aggregate"}:
            errors.append(f"unclassified research fixture: {relative}")
        if not path.is_file():
            errors.append(f"missing research fixture: {relative}")
            continue
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != fixture.get("sha256"):
            errors.append(f"research fixture bytes changed: {relative}")
        if fixture.get("kind") == "historical_aggregate":
            try:
                value = json.loads(payload)
                if value.get("kind") != "historical_aggregate":
                    errors.append(f"historical fixture mislabeled: {relative}")
            except (ValueError, AttributeError):
                errors.append(f"invalid historical aggregate fixture: {relative}")
        elif fixture.get("kind") == "synthetic" and path.suffix == ".json":
            try:
                value = json.loads(payload)
                if isinstance(value, dict) and (value.get("kind") == "historical_aggregate"
                                                or value.get("synthetic") is False):
                    errors.append(f"synthetic fixture contradicts its content: {relative}")
            except ValueError:
                errors.append(f"invalid synthetic JSON fixture: {relative}")
    actual_fixtures = {p for p in actual if "fixtures" in Path(p).parts}
    for relative in sorted(actual_fixtures - fixture_paths):
        errors.append(f"research fixture not reviewed: {relative}")
    return errors


def research_provenance_errors() -> list[str]:
    try:
        manifest = json.loads((ROOT / "docs/research/PROVENANCE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["research provenance missing or invalid JSON"]
    return research_manifest_errors(manifest, ROOT)


def local_document_link_errors(root: Path) -> list[str]:
    """Check local destinations only; this does not claim remote URL availability."""
    pages = list(root.glob("*.md")) + list((root / "docs").rglob("*.md"))
    pages += list((root / "research").rglob("*.md"))
    if (root / "legacy/README.md").is_file():
        pages.append(root / "legacy/README.md")
    errors = []
    for path in pages:
        content = path.read_text(encoding="utf-8")
        targets = re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", content)
        targets += re.findall(r'(?:href|src)="([^"]+)"', content)
        for target in targets:
            target = target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            if not (path.parent / unquote(parsed.path)).exists():
                errors.append(f"missing local link in {path.relative_to(root).as_posix()}: {target}")
    return errors


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
    errors = (documentation_image_errors() + legacy_snapshot_errors()
              + research_provenance_errors() + local_document_link_errors(ROOT))
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
    scan_roots = [ROOT / name for name in ("src", "tests", "examples", "docs", "research")]
    for scan_root in scan_roots:
        for path in scan_root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if path.suffix.casefold() in BINARY_SUFFIXES:
                errors.append(f"binary is outside the public boundary: {relative}")
                continue
            if path.suffix.casefold() in TEXT_SUFFIXES:
                content = path.read_text(encoding="utf-8", errors="replace")
                errors.extend(text_boundary_errors(relative, content))
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
    if not compileall.compile_dir(ROOT / "research", quiet=1):
        raise SystemExit("research compileall failed")
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
        check=False,
        text=True,
        stderr=subprocess.PIPE,
    )
    sys.stderr.write(completed.stderr)
    if completed.returncode:
        return completed.returncode
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
    # FunctionTestCase and TestCase have different verbose line formats.
    # Read unittest's actual run summary instead of counting name prefixes.
    summary = re.search(r"Ran (\d+) tests? in ", completed.stderr)
    if summary is None:
        raise SystemExit("unittest did not provide a run-count summary")
    tests_run = int(summary.group(1))
    skipped_match = re.search(r"skipped=(\d+)", completed.stderr)
    expected_match = re.search(r"expected failures=(\d+)", completed.stderr)
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    expected_failures = int(expected_match.group(1)) if expected_match else 0
    print(
        json.dumps(
            {"result": "PASS", "tests": tests_run, "passed": tests_run - skipped - expected_failures,
             "failed": 0, "skipped": skipped, "expected_failures": expected_failures,
             "private_boundary_errors": 0}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
