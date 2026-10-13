from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "client_protection_manifest.json"
RANKMAP_MODULES = (
    "bidking_lab.extract.current_rankmap_package_contract",
    "bidking_lab.live.current_rankmap_runtime",
)
RANKMAP_SOURCES = tuple(module.replace(".", "/") + ".py" for module in RANKMAP_MODULES)


def test_current_rankmap_modules_compile_and_import_native_only(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = {
        relative
        for tree in manifest["critical_native"]["package_trees"]
        for relative in tree["protected_modules"]
    }
    assert set(RANKMAP_SOURCES) <= declared

    stage = tmp_path / "stage"
    source_root = stage / "src"
    shutil.copytree(ROOT / "src", source_root)
    setup_script = source_root / "_setup_rankmap.py"
    setup_script.write_text(
        """
from Cython.Build import cythonize
from setuptools import Extension, setup

modules = {
    "bidking_lab.extract.current_rankmap_package_contract":
        "bidking_lab/extract/current_rankmap_package_contract.py",
    "bidking_lab.live.current_rankmap_runtime":
        "bidking_lab/live/current_rankmap_runtime.py",
}
extensions = [Extension(name, [source]) for name, source in modules.items()]
setup(
    ext_modules=cythonize(
        extensions,
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
    assert completed.returncode == 0, "source-level Cython compile failed"

    for source in RANKMAP_SOURCES:
        source_path = source_root / source
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
                f"sys.path.insert(0,{str(source_root)!r});"
                f"names={RANKMAP_MODULES!r};"
                "mods=[importlib.import_module(name) for name in names];"
                "assert all(str(mod.__file__).lower().endswith('.pyd') for mod in mods);"
                "assert hasattr(mods[0],'CURRENT_RANKMAP_PACKAGE');"
                "assert hasattr(mods[1],'bind_current_rankmap_algorithm_prior')"
            ),
        ],
        cwd=stage,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert smoke.returncode == 0, "native-only RankMap import failed"
    assert all(not (source_root / source).exists() for source in RANKMAP_SOURCES)
