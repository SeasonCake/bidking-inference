from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_wss_addon_bootstrap import (  # noqa: E402
    _BOOTSTRAP,
    load_operational_contract,
    render_protected_addon,
)
from run_wss_live_monitor import (  # noqa: E402
    WSS_OPERATIONAL_GENERATION,
    _validated_wss_operational_snapshot,
    _validated_wss_operational_environment,
)


@pytest.fixture(scope="module")
def protected_operational_package(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, str]:
    if os.name != "nt" or sys.implementation.cache_tag != "cpython-313":
        pytest.skip("Windows CPython 3.13 stable-ABI compiler probe required")
    root = tmp_path_factory.mktemp("wss-operational-package")
    scripts = root / "scripts"
    scripts.mkdir()
    bootstrap, native_source = render_protected_addon(
        core_source=(SCRIPTS / "bidking_wss_capture_core.py").read_text(
            encoding="utf-8"
        ),
        addon_source=(SCRIPTS / "mitm_bidking_wss_addon.py").read_text(
            encoding="utf-8"
        ),
        content_key=bytes(range(32)),
        key_share_a=bytes(reversed(range(32))),
        nonces=(b"\x11" * 12, b"\x22" * 12),
    )
    (scripts / "local_compat_addon.py").write_text(
        bootstrap, encoding="utf-8", newline="\n"
    )
    (scripts / "_local_compat_addon_loader.c").write_text(
        native_source, encoding="utf-8", newline="\n"
    )
    setup_path = scripts / "_setup_wss_operational.py"
    setup_path.write_text(
        "\n".join(
            (
                "from setuptools import Extension, setup",
                "setup(ext_modules=[Extension(",
                "    '_local_compat_addon_loader',",
                "    ['_local_compat_addon_loader.c'],",
                "    define_macros=[('Py_LIMITED_API', '0x030D0000')],",
                "    py_limited_api=True,",
                "    libraries=['bcrypt'],",
                ")])",
            )
        ),
        encoding="utf-8",
        newline="\n",
    )
    build = subprocess.run(
        [sys.executable, "-B", str(setup_path), "build_ext", "--inplace"],
        cwd=scripts,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert build.returncode == 0, f"{build.stdout}\n{build.stderr}"
    loaders = list(scripts.glob("_local_compat_addon_loader*.pyd"))
    assert len(loaders) == 1
    fixed_loader = scripts / "_local_compat_addon_loader.pyd"
    if loaders[0] != fixed_loader:
        loaders[0].replace(fixed_loader)
    return root, "a" * 64


def _operational_env(root: Path, generation: str) -> dict[str, str]:
    contract = load_operational_contract(
        SCRIPTS / "client_protection_manifest.json"
    )
    env = dict(os.environ)
    env.update(
        {
            "BIDKING_WSS_OPERATIONAL_ABI": contract.abi_version,
            "BIDKING_WSS_PACKAGE_ROOT": str(root.resolve()),
            "BIDKING_WSS_PACKAGE_GENERATION": generation,
            "BIDKING_WSS_OPERATIONAL_GENERATION": contract.generation_sha256,
            "BIDKING_WSS_RUNTIME_ROLE": "main",
        }
    )
    return env


def _run_import(root: Path, env: dict[str, str], body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-E", "-s", "-B", "-c", body],
        cwd=root / "scripts",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def test_native_operational_entrypoint_loads_without_source_export(
    protected_operational_package: tuple[Path, str],
) -> None:
    root, generation = protected_operational_package
    completed = _run_import(
        root,
        _operational_env(root, generation),
        (
            "import local_compat_addon as addon; "
            "import _local_compat_addon_loader as loader; "
            "assert len(addon.addons) == 1; "
            "assert not hasattr(loader, 'source'); "
            "assert callable(loader.load_addons); "
            "assert all(name != 'source' for name in dir(loader))"
        ),
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("BIDKING_WSS_OPERATIONAL_ABI", "wrong-abi"),
        ("BIDKING_WSS_PACKAGE_ROOT", r"C:\foreign-package"),
        ("BIDKING_WSS_PACKAGE_GENERATION", "not-a-generation"),
        ("BIDKING_WSS_OPERATIONAL_GENERATION", "b" * 64),
        ("BIDKING_WSS_RUNTIME_ROLE", "mitmdump"),
    ],
)
def test_native_operational_identity_mismatch_fails_closed(
    protected_operational_package: tuple[Path, str],
    field: str,
    value: str,
) -> None:
    root, generation = protected_operational_package
    env = _operational_env(root, generation)
    env[field] = value
    completed = _run_import(
        root,
        env,
        (
            "try:\n"
            " import local_compat_addon\n"
            "except RuntimeError as exc:\n"
            " assert str(exc) == 'wss operational identity rejected'\n"
            " print('REJECTED')\n"
            "else:\n"
            " raise SystemExit(19)\n"
        ),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "REJECTED"
    assert str(root) not in completed.stdout


def _write_packaged_identity(root: Path, *, generation: str) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    bootstrap = scripts / "local_compat_addon.py"
    loader = scripts / "_local_compat_addon_loader.pyd"
    contract = load_operational_contract(
        SCRIPTS / "client_protection_manifest.json"
    )
    bootstrap.write_bytes(_BOOTSTRAP.encode("utf-8"))
    loader.write_bytes(b"fixed-native-loader")
    (root / "BUILD_MANIFEST.txt").write_text(
        "\n".join(
            (
                f"WssOperationalABI: {contract.abi_version}",
                f"WssOperationalGeneration: {contract.generation_sha256}",
                "WssAddonBootstrapSHA256: "
                + hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
                "WssAddonLoaderSHA256: "
                + hashlib.sha256(loader.read_bytes()).hexdigest(),
                "",
            )
        ),
        encoding="utf-8",
        newline="\n",
    )
    assert len(generation) == 64
    return bootstrap


def test_monitor_binds_manifest_members_and_generation_without_path_leak(
    tmp_path: Path,
) -> None:
    generation = "c" * 64
    addon = _write_packaged_identity(tmp_path, generation=generation)
    environment = _validated_wss_operational_environment(
        package_root=tmp_path,
        addon_path=addon,
        package_generation_identity=generation,
    )
    assert environment["BIDKING_WSS_PACKAGE_ROOT"] == str(tmp_path.resolve())
    assert environment["BIDKING_WSS_PACKAGE_GENERATION"] == generation
    assert environment["BIDKING_WSS_RUNTIME_ROLE"] == "mitmdump"

    (tmp_path / "scripts" / "_local_compat_addon_loader.pyd").write_bytes(
        b"tampered"
    )
    with pytest.raises(RuntimeError) as caught:
        _validated_wss_operational_environment(
            package_root=tmp_path,
            addon_path=addon,
            package_generation_identity=generation,
        )
    assert str(caught.value) == "wss operational identity unavailable"
    assert str(tmp_path) not in str(caught.value)


def test_monitor_rejects_manifest_generation_outside_frozen_authority(
    tmp_path: Path,
) -> None:
    generation = "c" * 64
    addon = _write_packaged_identity(tmp_path, generation=generation)
    manifest = tmp_path / "BUILD_MANIFEST.txt"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            WSS_OPERATIONAL_GENERATION,
            "b" * 64,
        ),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(RuntimeError) as caught:
        _validated_wss_operational_environment(
            package_root=tmp_path,
            addon_path=addon,
            package_generation_identity=generation,
        )
    assert str(caught.value) == "wss operational identity unavailable"
    assert str(tmp_path) not in str(caught.value)


def test_monitor_holds_one_snapshot_through_package_identity_and_child_boundary(
    tmp_path: Path,
) -> None:
    generation = "c" * 64
    addon = _write_packaged_identity(tmp_path, generation=generation)
    manifest = tmp_path / "BUILD_MANIFEST.txt"
    loader = tmp_path / "scripts" / "_local_compat_addon_loader.pyd"
    blocked: list[str] = []

    def validate_package_generation() -> str:
        for label, path in (
            ("manifest", manifest),
            ("bootstrap", addon),
            ("loader", loader),
        ):
            try:
                path.write_bytes(b"replacement-during-package-generation")
            except OSError:
                blocked.append(label)
        return generation

    snapshot, environment = _validated_wss_operational_snapshot(
        package_root=tmp_path,
        addon_path=addon,
        package_generation_validator=validate_package_generation,
    )
    try:
        assert blocked == ["manifest", "bootstrap", "loader"]
        assert environment["BIDKING_WSS_PACKAGE_GENERATION"] == generation
        assert (
            environment["BIDKING_WSS_OPERATIONAL_GENERATION"]
            == WSS_OPERATIONAL_GENERATION
        )
        snapshot.assert_unchanged()
        child_read = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "root=Path(sys.argv[1]); "
                    "assert (root/'scripts/local_compat_addon.py').read_bytes(); "
                    "assert (root/'scripts/_local_compat_addon_loader.pyd').read_bytes()"
                ),
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert child_read.returncode == 0, child_read.stderr
        with pytest.raises(OSError):
            addon.write_bytes(b"replacement-before-child-load")
        with pytest.raises(OSError):
            loader.write_bytes(b"replacement-before-child-load")
    finally:
        snapshot.close()

    addon.write_bytes(b"replacement-after-child-exit")
    loader.write_bytes(b"replacement-after-child-exit")


def test_monitor_frozen_generation_matches_protection_contract() -> None:
    contract = load_operational_contract(SCRIPTS / "client_protection_manifest.json")
    assert WSS_OPERATIONAL_GENERATION == contract.generation_sha256


def test_source_tree_monitor_keeps_plaintext_engineering_path(tmp_path: Path) -> None:
    addon = tmp_path / "scripts" / "mitm_bidking_wss_addon.py"
    addon.parent.mkdir()
    addon.write_text("addons = []\n", encoding="utf-8")
    assert (
        _validated_wss_operational_environment(
            package_root=tmp_path,
            addon_path=addon,
            package_generation_identity="",
        )
        == {}
    )
