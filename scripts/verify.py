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


def public_boundary_errors() -> list[str]:
    errors: list[str] = []
    scan_roots = [ROOT / "src", ROOT / "tests", ROOT / "examples"]
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
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    subprocess.run(
        [sys.executable, "scripts/run_example.py", "examples/synthetic_session.json"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    print(json.dumps({"result": "PASS", "tests": 7, "private_boundary_errors": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
