#!/usr/bin/env python3
"""Run the public candidate's deterministic local verification."""

from __future__ import annotations

import compileall
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml", ".json"}
BINARY_SUFFIXES = {".dll", ".exe", ".pyd", ".so", ".dylib", ".zip", ".tar", ".7z", ".rar"}
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
)


def public_boundary_errors() -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_PUBLIC_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"required public-maintenance file is missing: {relative}")
    if not (ROOT / "LICENSE").is_file():
        errors.append("final LICENSE is missing")
    if (ROOT / "LICENSE-DECISION.md").exists():
        errors.append("obsolete license decision placeholder is still present")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if 'license = { file = "LICENSE" }' not in pyproject:
        errors.append("pyproject does not bind package metadata to LICENSE")
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
    fixture = json.loads((ROOT / "examples" / "synthetic_session.json").read_text(encoding="utf-8"))
    if fixture.get("synthetic") is not True:
        errors.append("example fixture is not explicitly synthetic")
    return errors


def main() -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    if not compileall.compile_dir(ROOT / "src", quiet=1):
        raise SystemExit("compileall failed")
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
    test_lines = [line for line in completed.stderr.splitlines() if line.startswith("test_")]
    print(
        json.dumps(
            {"result": "PASS", "tests": len(test_lines), "private_boundary_errors": 0}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
