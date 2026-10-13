from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "client_protection_manifest.json"
MODULE = "bidking_lab.live.catalog_value"
SOURCE = "bidking_lab/live/catalog_value.py"


def test_catalog_value_compiles_and_imports_native_only(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = {
        relative
        for tree in manifest["critical_native"]["package_trees"]
        for relative in tree["protected_modules"]
    }
    assert SOURCE in declared

    stage = tmp_path / "stage"
    source_root = stage / "src"
    shutil.copytree(ROOT / "src", source_root)
    setup_script = source_root / "_setup_catalog_value.py"
    setup_script.write_text(
        """
from Cython.Build import cythonize
from setuptools import Extension, setup

setup(
    ext_modules=cythonize(
        [Extension("bidking_lab.live.catalog_value", ["bidking_lab/live/catalog_value.py"])],
        language_level=3,
        quiet=True,
        compiler_directives={
            "embedsignature": False,
            "emit_code_comments": False,
        },
    )
)
""".lstrip(),
        encoding="ascii",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-E",
            "-s",
            "-B",
            str(setup_script),
            "build_ext",
            "--inplace",
        ],
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, "catalog_value Cython compile failed"

    source_path = source_root / SOURCE
    matches = list(source_path.parent.glob(f"{source_path.stem}*.pyd"))
    assert len(matches) == 1
    source_path.unlink()
    source_path.with_suffix(".c").unlink(missing_ok=True)

    smoke = subprocess.run(
        [
            sys.executable,
            "-E",
            "-s",
            "-B",
            "-c",
            (
                "import importlib,sys;"
                "from types import SimpleNamespace as N;"
                f"sys.path.insert(0,{str(source_root)!r});"
                f"mod=importlib.import_module({MODULE!r});"
                "assert str(mod.__file__).lower().endswith('.pyd');"
                "out=mod.summarize_catalog_values((N(item_id=7),),{7:N(value=42)});"
                "assert out.known_value_sum==42;"
                "assert out.catalog_value_complete is True;"
                "assert out.unknown_item_ids==()"
            ),
        ],
        cwd=stage,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert smoke.returncode == 0, "native-only catalog_value import failed"
    assert not source_path.exists()
