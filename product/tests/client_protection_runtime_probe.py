"""Compile the client-protection boundary and execute real package runtime paths.

This is a source-side release probe, not a pytest test.  It mirrors the three
Cython layouts used by ``build_live_portable.ps1``, removes protected ``.py``
files, then proves that imports and two real monitor fixtures work from native
modules only.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from typing import BinaryIO, Sequence

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "client_protection_manifest.json"
RUNTIME_SOURCE_MANIFEST = ROOT / "scripts" / "runtime_python_source_manifest.json"
RELEASE_ZIP_CONTRACT = ROOT / "scripts" / "release_zip_contract.json"
IDENTITY_SCHEMA = "bidking_client_protection_build_python_identity_v1"
LEGACY_CP3133_SHA256 = (
    "d87063e5597f257004c731b66c59c56c91038861c6877b1a3dca6b8c4e919125"
)
SETUP_COMMON = """\
import sys
from setuptools import Extension, setup
from Cython.Build import cythonize
import Cython.Compiler.Options as _cyopt
_cyopt.docstrings = False
COMPILE_FLAGS = ["/O2", "/GL", "/guard:cf"]
LINK_FLAGS = ["/LTCG", "/OPT:REF", "/OPT:ICF", "/GUARD:CF",
              "/DYNAMICBASE", "/NXCOMPAT", "/HIGHENTROPYVA"]
"""
IDENTITY_PROBE = """\
import json,platform,struct,sys,sysconfig
print(json.dumps({
    "abi_tag": sysconfig.get_config_var("SOABI"),
    "cache_tag": sys.implementation.cache_tag,
    "executable": sys.executable,
    "machine": platform.machine(),
    "platform": sysconfig.get_platform(),
    "pointer_bits": struct.calcsize("P") * 8,
    "version": ".".join(map(str, sys.version_info[:3])),
}, sort_keys=True, separators=(",", ":")))
"""
COMPILE_TOOLCHAIN_BOOTSTRAP = r"""
import importlib
import importlib.metadata
import json
import runpy
import sys
from pathlib import Path

if not (
    sys.flags.ignore_environment
    and sys.flags.no_user_site
    and sys.flags.no_site
    and sys.flags.dont_write_bytecode
):
    raise RuntimeError("compile child isolation flags missing")
if "sitecustomize" in sys.modules or "usercustomize" in sys.modules:
    raise RuntimeError("compile child customizer executed")

base = Path(sys.base_prefix).resolve(strict=True)
toolchain_root = Path(sys.argv[1]).resolve(strict=True)
expected = json.loads(sys.argv[2])
setup_script = Path(sys.argv[3]).resolve(strict=True)
setup_args = sys.argv[4:]
canonical_toolchain = (base / "Lib" / "site-packages").resolve(strict=True)
if toolchain_root != canonical_toolchain:
    raise RuntimeError("compile child toolchain root mismatch")

stdlib_paths = []
for raw in sys.path:
    if not raw:
        continue
    resolved = Path(raw).resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise RuntimeError("compile child inherited path outside runtime") from exc
    if "site-packages" in {part.casefold() for part in resolved.parts}:
        raise RuntimeError("compile child inherited unapproved site-packages")
    if resolved not in stdlib_paths:
        stdlib_paths.append(resolved)
sys.path[:] = [str(toolchain_root), *(str(path) for path in stdlib_paths)]

for distribution_name, module_name in (
    ("setuptools", "setuptools"),
    ("cython", "Cython"),
):
    distribution = importlib.metadata.distribution(distribution_name)
    if distribution.version != expected[distribution_name]:
        raise RuntimeError(
            f"compile child {distribution_name} version mismatch"
        )
    distribution_root = Path(distribution.locate_file("")).resolve(strict=True)
    if distribution_root != toolchain_root:
        raise RuntimeError(
            f"compile child {distribution_name} distribution root mismatch"
        )
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__).resolve(strict=True)
    try:
        module_path.relative_to(toolchain_root)
    except ValueError as exc:
        raise RuntimeError(
            f"compile child {distribution_name} module root mismatch"
        ) from exc

if "sitecustomize" in sys.modules or "usercustomize" in sys.modules:
    raise RuntimeError("compile child customizer executed")
sys.argv = [str(setup_script), *setup_args]
runpy.run_path(str(setup_script), run_name="__main__")
"""
RUNTIME_STAGE_BOOTSTRAP = r"""
import hashlib
import importlib.metadata
import json
import os
import stat
import sys
from pathlib import Path

if not (
    sys.flags.ignore_environment
    and sys.flags.no_user_site
    and sys.flags.no_site
    and sys.flags.dont_write_bytecode
):
    raise RuntimeError("runtime child isolation flags missing")
if "sitecustomize" in sys.modules or "usercustomize" in sys.modules:
    raise RuntimeError("runtime child customizer executed")

def normalized_name(value):
    return value.casefold().replace("_", "-").replace(".", "-")

def is_reparse(path):
    details = path.lstat()
    return path.is_symlink() or bool(
        getattr(details, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )

def require_plain_tree(root):
    if is_reparse(root):
        raise RuntimeError("runtime dependency root is a reparse point")
    rows = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*dirs, *files]:
            child = current_path / name
            if is_reparse(child):
                raise RuntimeError("runtime dependency tree contains a reparse point")
        for name in files:
            child = current_path / name
            if not child.is_file():
                raise RuntimeError("runtime dependency member is not a regular file")
            relative = child.relative_to(root).as_posix()
            digest = hashlib.sha256(child.read_bytes()).hexdigest()
            rows.append((relative, child.stat().st_size, digest))
    rows.sort()
    payload = json.dumps(rows, ensure_ascii=True, separators=(",", ":"))
    return rows, hashlib.sha256(payload.encode("ascii")).hexdigest()

base = Path(sys.base_prefix).resolve(strict=True)
config = json.loads(sys.argv[1])
stage = Path(config["stage"]).resolve(strict=True)
stage_paths = [Path(value).resolve(strict=True) for value in config["module_paths"]]
dependency_root = Path(config["dependency_root"]).resolve(strict=True)
expected = config["runtime_dependencies"]
expected_inventory = config["inventory"]
code = sys.argv[2]
code_args = sys.argv[3:]
if not stage.is_dir() or not stage.is_absolute():
    raise RuntimeError("runtime child Stage is invalid")
for stage_path in [*stage_paths, dependency_root]:
    try:
        stage_path.relative_to(stage)
    except ValueError as exc:
        raise RuntimeError("runtime child path is outside Stage") from exc
if not dependency_root.is_dir():
    raise RuntimeError("runtime dependency root is invalid")
rows, inventory_sha256 = require_plain_tree(dependency_root)
if len(rows) != expected_inventory["file_count"]:
    raise RuntimeError("runtime dependency file count mismatch")
if sum(row[1] for row in rows) != expected_inventory["total_bytes"]:
    raise RuntimeError("runtime dependency byte count mismatch")
if inventory_sha256 != expected_inventory["sha256"]:
    raise RuntimeError("runtime dependency inventory mismatch")

distributions = {}
for distribution in importlib.metadata.distributions(path=[str(dependency_root)]):
    name = distribution.metadata.get("Name")
    if not isinstance(name, str) or not name:
        raise RuntimeError("runtime dependency distribution name missing")
    normalized = normalized_name(name)
    distributions.setdefault(normalized, []).append(distribution)
if set(distributions) != set(expected):
    raise RuntimeError("runtime dependency distribution population mismatch")
for name, version in expected.items():
    matches = distributions.get(name, [])
    if len(matches) != 1 or matches[0].version != version:
        raise RuntimeError("runtime dependency distribution identity mismatch")
    distribution_root = Path(matches[0].locate_file("")).resolve(strict=True)
    if distribution_root != dependency_root:
        raise RuntimeError("runtime dependency distribution root mismatch")

stdlib_paths = []
for raw in sys.path:
    if not raw:
        continue
    resolved = Path(raw).resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise RuntimeError("runtime child inherited path outside runtime") from exc
    if "site-packages" in {part.casefold() for part in resolved.parts}:
        raise RuntimeError("runtime child inherited site-packages")
    if resolved not in stdlib_paths:
        stdlib_paths.append(resolved)
sys.path[:] = [
    *(str(path) for path in stage_paths),
    str(dependency_root),
    *(str(path) for path in stdlib_paths),
]
if "sitecustomize" in sys.modules or "usercustomize" in sys.modules:
    raise RuntimeError("runtime child customizer executed")
sys.argv = ["-c", *code_args]
exec(compile(code, "<client-protection-runtime-smoke>", "exec"), {"__name__": "__main__"})
"""


class BuildPythonIdentityError(RuntimeError):
    """The selected build interpreter is not the exact expected file/runtime."""


@dataclass(frozen=True)
class RuntimeDependencyStage:
    root: Path
    runtime_dependencies: dict[str, str]
    file_count: int
    total_bytes: int
    inventory_sha256: str


if os.name == "nt":
    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CREATE_FILE = _KERNEL32.CreateFileW
    _CREATE_FILE.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CREATE_FILE.restype = wintypes.HANDLE
    _GET_FILE_INFORMATION = _KERNEL32.GetFileInformationByHandle
    _GET_FILE_INFORMATION.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _GET_FILE_INFORMATION.restype = wintypes.BOOL
    _CLOSE_HANDLE = _KERNEL32.CloseHandle
    _CLOSE_HANDLE.argtypes = [wintypes.HANDLE]
    _CLOSE_HANDLE.restype = wintypes.BOOL

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@dataclass(frozen=True)
class WindowsFileIdentity:
    volume_serial_number: int
    file_index: int
    file_size: int

    def receipt(self) -> dict[str, object]:
        return {
            "volume_serial": f"{self.volume_serial_number:08x}",
            "file_index": f"{self.file_index:016x}",
            "file_size": self.file_size,
        }


@dataclass
class _HeldExecutable:
    path: Path
    stream: BinaryIO = field(repr=False)
    file_identity: WindowsFileIdentity

    @property
    def closed(self) -> bool:
        return self.stream.closed

    def current_file_identity(self) -> WindowsFileIdentity:
        if self.closed:
            raise BuildPythonIdentityError("bound build Python handle is closed")
        return _windows_file_identity(self.stream)

    def sha256(self) -> str:
        if self.closed:
            raise BuildPythonIdentityError("bound build Python handle is closed")
        offset = self.stream.tell()
        self.stream.seek(0)
        try:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: self.stream.read(1024 * 1024), b""):
                hasher.update(chunk)
            return hasher.hexdigest()
        finally:
            self.stream.seek(offset)

    def close(self) -> None:
        if not self.closed:
            self.stream.close()

    def __enter__(self) -> _HeldExecutable:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _windows_file_identity(stream: BinaryIO) -> WindowsFileIdentity:
    if os.name != "nt":
        raise BuildPythonIdentityError(
            "client-protection build Python identity requires Windows"
        )
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
    information = _ByHandleFileInformation()
    if not _GET_FILE_INFORMATION(handle, ctypes.byref(information)):
        error = ctypes.WinError(ctypes.get_last_error())
        raise BuildPythonIdentityError(
            f"cannot read bound build Python Windows file identity: {error}"
        ) from error
    return WindowsFileIdentity(
        volume_serial_number=int(information.dwVolumeSerialNumber),
        file_index=(int(information.nFileIndexHigh) << 32)
        | int(information.nFileIndexLow),
        file_size=(int(information.nFileSizeHigh) << 32)
        | int(information.nFileSizeLow),
    )


def _open_held_executable(path: Path) -> _HeldExecutable:
    if os.name != "nt":
        raise BuildPythonIdentityError(
            "client-protection build Python identity requires Windows"
        )
    raw_handle = _CREATE_FILE(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if raw_handle == _INVALID_HANDLE_VALUE:
        error = ctypes.WinError(ctypes.get_last_error())
        raise BuildPythonIdentityError(
            f"cannot hold build Python executable identity: {path}: {error}"
        ) from error
    try:
        descriptor = msvcrt.open_osfhandle(
            raw_handle,
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
    except Exception:
        _CLOSE_HANDLE(raw_handle)
        raise
    try:
        stream = os.fdopen(descriptor, "rb", closefd=True)
    except Exception:
        os.close(descriptor)
        raise
    try:
        return _HeldExecutable(
            path=path,
            stream=stream,
            file_identity=_windows_file_identity(stream),
        )
    except Exception:
        stream.close()
        raise


def _resolve_executable(path: Path, *, purpose: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BuildPythonIdentityError(f"{purpose} does not exist: {path}") from exc
    if not resolved.is_file():
        raise BuildPythonIdentityError(f"{purpose} is not a file: {path}")
    return resolved


@dataclass(frozen=True)
class BuildPythonContract:
    profile: str
    version: str
    cache_tag: str
    abi_tag: str
    platform: str
    executable_sha256: str
    contract_source: str
    contract_sha256: str | None
    release_eligible: bool


@dataclass(frozen=True)
class BuildPythonIdentity:
    requested_executable: str
    executable: Path
    executable_sha256: str
    version: str
    cache_tag: str
    abi_tag: str
    platform: str
    machine: str
    pointer_bits: int
    selection: str
    contract: BuildPythonContract
    file_identity: WindowsFileIdentity
    _held_executable: _HeldExecutable = field(repr=False, compare=False)

    def receipt(self) -> dict[str, object]:
        return {
            "schema": IDENTITY_SCHEMA,
            "selection": self.selection,
            "contract_profile": self.contract.profile,
            "contract_source": self.contract.contract_source,
            "contract_sha256": self.contract.contract_sha256,
            "release_eligible": self.contract.release_eligible,
            "requested_executable": self.requested_executable,
            "resolved_executable": str(self.executable),
            "executable_sha256": self.executable_sha256,
            "version": self.version,
            "cache_tag": self.cache_tag,
            "abi_tag": self.abi_tag,
            "platform": self.platform,
            "machine": self.machine,
            "pointer_bits": self.pointer_bits,
            "windows_file_identity": self.file_identity.receipt(),
        }

    def close(self) -> None:
        self._held_executable.close()

    def __enter__(self) -> BuildPythonIdentity:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _runtime_source_contract(
    manifest_path: Path = RUNTIME_SOURCE_MANIFEST,
) -> BuildPythonContract:
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        python = manifest["python"]
        build_layout = python["build_layout"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BuildPythonIdentityError(
            f"runtime Python source manifest is invalid: {exc}"
        ) from exc
    if manifest.get("schema") != "bidking_runtime_python_source_v1":
        raise BuildPythonIdentityError("runtime Python source manifest schema mismatch")
    values = {
        "version": python.get("version"),
        "cache_tag": python.get("cache_tag"),
        "abi_tag": python.get("abi_tag"),
        "platform": python.get("platform"),
        "executable_sha256": build_layout.get("python_exe_sha256"),
    }
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise BuildPythonIdentityError("runtime Python source identity is incomplete")
    executable_sha256 = values["executable_sha256"]
    if len(executable_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in executable_sha256
    ):
        raise BuildPythonIdentityError("runtime Python executable SHA-256 is invalid")
    return BuildPythonContract(
        profile="runtime-source",
        version=values["version"],
        cache_tag=values["cache_tag"],
        abi_tag=values["abi_tag"],
        platform=values["platform"],
        executable_sha256=executable_sha256,
        contract_source=manifest_path.relative_to(ROOT).as_posix(),
        contract_sha256=hashlib.sha256(raw).hexdigest(),
        release_eligible=True,
    )


def _normalize_distribution_name(value: str) -> str:
    return value.casefold().replace("_", "-").replace(".", "-")


def _is_exact_type(value: object, expected: type[object]) -> bool:
    return value.__class__ is expected


def _strict_json_document(path: Path, *, purpose: str) -> tuple[bytes, dict[str, object]]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    try:
        raw = path.read_bytes()
        document = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BuildPythonIdentityError(f"{purpose} is invalid: {exc}") from exc
    if not _is_exact_type(document, dict):
        raise BuildPythonIdentityError(f"{purpose} must be an object")
    return raw, document


def _runtime_dependency_contract(
    manifest_path: Path = RUNTIME_SOURCE_MANIFEST,
    release_contract_path: Path = RELEASE_ZIP_CONTRACT,
) -> dict[str, str]:
    manifest_raw, manifest = _strict_json_document(
        manifest_path,
        purpose="runtime dependency source manifest",
    )
    _, release_contract = _strict_json_document(
        release_contract_path,
        purpose="runtime dependency release contract",
    )
    dependencies = manifest.get("runtime_dependencies")
    release_dependencies = release_contract.get("runtime_dependencies")
    wheels = manifest.get("wheels")
    fixed = release_contract.get("fixed_manifest_values")
    if (
        manifest.get("schema") != "bidking_runtime_python_source_v1"
        or manifest.get("manifest_version") != 1
        or _is_exact_type(manifest.get("manifest_version"), bool)
        or not _is_exact_type(dependencies, dict)
        or not _is_exact_type(release_dependencies, dict)
        or not _is_exact_type(wheels, list)
        or not _is_exact_type(fixed, dict)
        or len(dependencies) != 12
    ):
        raise BuildPythonIdentityError("runtime dependency contract shape mismatch")
    expected_manifest_sha256 = fixed.get("RuntimePythonSourceManifestSHA256")
    if (
        not _is_exact_type(expected_manifest_sha256, str)
        or hashlib.sha256(manifest_raw).hexdigest() != expected_manifest_sha256
    ):
        raise BuildPythonIdentityError("runtime dependency manifest identity mismatch")

    normalized: dict[str, str] = {}
    for name, version in dependencies.items():
        if (
            not _is_exact_type(name, str)
            or not name
            or name != name.strip()
            or not _is_exact_type(version, str)
            or not version
            or version != version.strip()
        ):
            raise BuildPythonIdentityError("runtime dependency entry is invalid")
        canonical = _normalize_distribution_name(name)
        if canonical in normalized:
            raise BuildPythonIdentityError("runtime dependency names are not unique")
        normalized[canonical] = version

    normalized_release: dict[str, str] = {}
    for name, version in release_dependencies.items():
        if not _is_exact_type(name, str) or not _is_exact_type(version, str):
            raise BuildPythonIdentityError("release runtime dependency entry is invalid")
        canonical = _normalize_distribution_name(name)
        if canonical in normalized_release:
            raise BuildPythonIdentityError(
                "release runtime dependency names are not unique"
            )
        normalized_release[canonical] = version
    if normalized_release != normalized:
        raise BuildPythonIdentityError("runtime dependency authorities disagree")

    runtime_wheels: dict[str, str] = {}
    for wheel in wheels:
        if not _is_exact_type(wheel, dict):
            raise BuildPythonIdentityError("runtime dependency wheel entry is invalid")
        if wheel.get("role") != "runtime":
            continue
        name = wheel.get("distribution")
        version = wheel.get("version")
        if not _is_exact_type(name, str) or not _is_exact_type(version, str):
            raise BuildPythonIdentityError("runtime dependency wheel identity is invalid")
        canonical = _normalize_distribution_name(name)
        if canonical in runtime_wheels:
            raise BuildPythonIdentityError("runtime dependency wheel is duplicated")
        runtime_wheels[canonical] = version
    if runtime_wheels != normalized:
        raise BuildPythonIdentityError("runtime dependency wheel population mismatch")
    return dict(sorted(normalized.items()))


def _legacy_cp3133_contract() -> BuildPythonContract:
    return BuildPythonContract(
        profile="legacy-cp3133-identity-control",
        version="3.13.3",
        cache_tag="cpython-313",
        abi_tag="cp313-win_amd64",
        platform="win-amd64",
        executable_sha256=LEGACY_CP3133_SHA256,
        contract_source="built-in-frozen-legacy-control",
        contract_sha256=None,
        release_eligible=False,
    )


def _probe_executable(executable: Path) -> dict[str, object]:
    result = subprocess.run(
        [str(executable), "-E", "-s", "-S", "-B", "-c", IDENTITY_PROBE],
        cwd=executable.parent,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    lines = result.stdout.splitlines()
    if result.returncode or len(lines) != 1:
        detail = (result.stderr or result.stdout)[-2000:]
        raise BuildPythonIdentityError(
            f"build Python identity probe failed rc={result.returncode}: {detail}"
        )
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise BuildPythonIdentityError("build Python identity output is invalid") from exc
    if not isinstance(value, dict):
        raise BuildPythonIdentityError("build Python identity output must be an object")
    return value


def _validate_runtime_payload(
    actual: dict[str, object],
    contract: BuildPythonContract,
    expected_file_identity: WindowsFileIdentity,
    *,
    expected_machine: str | None = None,
    expected_pointer_bits: int | None = None,
) -> tuple[str, int]:
    reported_raw = actual.get("executable")
    if not isinstance(reported_raw, str) or not Path(reported_raw).is_absolute():
        raise BuildPythonIdentityError("build Python reported a non-absolute executable")
    reported = _resolve_executable(
        Path(reported_raw),
        purpose="build Python reported executable",
    )
    with _open_held_executable(reported) as reported_handle:
        if reported_handle.file_identity != expected_file_identity:
            raise BuildPythonIdentityError(
                "build Python reported executable is not the selected file identity"
            )

    expected = {
        "version": contract.version,
        "cache_tag": contract.cache_tag,
        "abi_tag": contract.abi_tag,
        "platform": contract.platform,
    }
    mismatches = [
        f"{name}={actual.get(name)!r} expected={value!r}"
        for name, value in expected.items()
        if actual.get(name) != value
    ]
    machine = actual.get("machine")
    pointer_bits = actual.get("pointer_bits")
    if not isinstance(machine, str) or machine.casefold() not in {"amd64", "x86_64"}:
        mismatches.append(f"machine={machine!r} expected='AMD64'")
    elif expected_machine is not None and machine.casefold() != expected_machine.casefold():
        mismatches.append(f"machine={machine!r} expected={expected_machine!r}")
    if pointer_bits != 64:
        mismatches.append(f"pointer_bits={pointer_bits!r} expected=64")
    elif expected_pointer_bits is not None and pointer_bits != expected_pointer_bits:
        mismatches.append(
            f"pointer_bits={pointer_bits!r} expected={expected_pointer_bits!r}"
        )
    if mismatches:
        raise BuildPythonIdentityError(
            "build Python runtime identity mismatch: " + "; ".join(mismatches)
        )
    assert isinstance(machine, str)
    assert isinstance(pointer_bits, int)
    return machine, pointer_bits


def _bind_build_python(
    build_python: Path | None,
    contract: BuildPythonContract,
) -> BuildPythonIdentity:
    selection = "explicit" if build_python is not None else "current-sys-executable"
    requested = Path(build_python) if build_python is not None else Path(sys.executable)
    if not requested.is_absolute():
        raise BuildPythonIdentityError(
            "build Python must be an absolute path; PATH lookup is prohibited"
        )
    resolved = _resolve_executable(requested, purpose="build Python")
    held_executable: _HeldExecutable | None = None
    try:
        held_executable = _open_held_executable(resolved)
        executable_sha256 = held_executable.sha256()
        if executable_sha256 != contract.executable_sha256:
            raise BuildPythonIdentityError(
                "build Python executable SHA-256 does not match the selected contract"
            )
        actual = _probe_executable(resolved)
        machine, pointer_bits = _validate_runtime_payload(
            actual,
            contract,
            held_executable.file_identity,
        )
        return BuildPythonIdentity(
            requested_executable=str(requested),
            executable=resolved,
            executable_sha256=executable_sha256,
            version=contract.version,
            cache_tag=contract.cache_tag,
            abi_tag=contract.abi_tag,
            platform=contract.platform,
            machine=machine,
            pointer_bits=pointer_bits,
            selection=selection,
            contract=contract,
            file_identity=held_executable.file_identity,
            _held_executable=held_executable,
        )
    except Exception:
        if held_executable is not None:
            held_executable.close()
        raise


def _require_bound_executable(
    identity: BuildPythonIdentity,
    candidate: Path,
) -> Path:
    if not candidate.is_absolute():
        raise BuildPythonIdentityError("child Python executable must be absolute")
    held = identity._held_executable
    if held.current_file_identity() != identity.file_identity:
        raise BuildPythonIdentityError(
            "child Python executable changed after identity binding"
        )
    if held.sha256() != identity.executable_sha256:
        raise BuildPythonIdentityError(
            "child Python executable SHA-256 changed after identity binding"
        )
    candidate_resolved = _resolve_executable(
        candidate,
        purpose="child Python executable",
    )
    with _open_held_executable(candidate_resolved) as candidate_handle:
        if candidate_handle.file_identity != identity.file_identity:
            raise BuildPythonIdentityError(
                "child Python executable changed after identity binding"
            )
        if candidate_handle.sha256() != identity.executable_sha256:
            raise BuildPythonIdentityError(
                "child Python executable SHA-256 changed after identity binding"
            )
    return identity.executable


def _revalidate_bound_runtime(identity: BuildPythonIdentity) -> Path:
    executable = _require_bound_executable(identity, identity.executable)
    actual = _probe_executable(executable)
    _validate_runtime_payload(
        actual,
        identity.contract,
        identity.file_identity,
        expected_machine=identity.machine,
        expected_pointer_bits=identity.pointer_bits,
    )
    return _require_bound_executable(identity, executable)


def _run_python(
    identity: BuildPythonIdentity,
    args: Sequence[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
) -> None:
    executable = _revalidate_bound_runtime(identity)
    child_args = list(args)
    while child_args and child_args[0] in {"-E", "-s", "-S", "-B"}:
        child_args.pop(0)
    try:
        result = subprocess.run(
            [str(executable), "-E", "-s", "-S", "-B", *child_args],
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    finally:
        _revalidate_bound_runtime(identity)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-4000:])


def _run_compile_python(
    identity: BuildPythonIdentity,
    setup_script: Path,
    setup_args: Sequence[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
) -> None:
    cwd_resolved = cwd.resolve(strict=True)
    if not cwd_resolved.is_dir():
        raise ValueError("compile child cwd must be a directory")
    setup_resolved = setup_script.resolve(strict=True)
    if not setup_resolved.is_file() or setup_resolved.suffix.casefold() != ".py":
        raise ValueError("compile child setup script must be a Python file")
    if setup_resolved.parent != cwd_resolved:
        raise ValueError("compile child setup script must be inside its cwd")
    child_args = list(setup_args)
    if any(
        not isinstance(value, str) or not value or value.startswith("-") or "\x00" in value
        for value in child_args
    ):
        raise ValueError("compile argument must be a nonempty non-option string")

    try:
        manifest = json.loads(RUNTIME_SOURCE_MANIFEST.read_bytes())
        build_tools = manifest["build_tools"]
        expected_tools = {
            "setuptools": build_tools["setuptools"],
            "cython": build_tools["cython"],
        }
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BuildPythonIdentityError(
            f"compile toolchain manifest is invalid: {exc}"
        ) from exc
    if any(not isinstance(value, str) or not value for value in expected_tools.values()):
        raise BuildPythonIdentityError("compile toolchain identity is incomplete")

    executable = _revalidate_bound_runtime(identity)
    toolchain_root = (executable.parent / "Lib" / "site-packages").resolve(
        strict=True
    )
    try:
        result = subprocess.run(
            [
                str(executable),
                "-E",
                "-s",
                "-S",
                "-B",
                "-c",
                COMPILE_TOOLCHAIN_BOOTSTRAP,
                str(toolchain_root),
                json.dumps(expected_tools, sort_keys=True, separators=(",", ":")),
                str(setup_resolved),
                *child_args,
            ],
            cwd=cwd_resolved,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    finally:
        _revalidate_bound_runtime(identity)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-4000:])


def _is_reparse_point(path: Path) -> bool:
    details = path.lstat()
    return path.is_symlink() or bool(
        getattr(details, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _resolve_plain_root(path: Path, *, purpose: str) -> Path:
    absolute = path.absolute()
    if not absolute.exists() or _is_reparse_point(absolute):
        raise BuildPythonIdentityError(f"{purpose} is invalid or a reparse point")
    resolved = absolute.resolve(strict=True)
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise BuildPythonIdentityError(f"{purpose} traverses a reparse point")
    return resolved


def _require_plain_member(path: Path, root: Path, *, purpose: str) -> Path:
    root_resolved = root.resolve(strict=True)
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise BuildPythonIdentityError(f"{purpose} is outside its authority root") from exc
    if ".." in relative.parts:
        raise BuildPythonIdentityError(f"{purpose} escapes its authority root")
    current = root_resolved
    if _is_reparse_point(current):
        raise BuildPythonIdentityError(f"{purpose} authority root is a reparse point")
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            raise BuildPythonIdentityError(f"{purpose} contains a reparse point")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise BuildPythonIdentityError(f"{purpose} resolves outside its authority root") from exc
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory_rows(paths: dict[str, Path]) -> tuple[list[tuple[str, int, str]], str]:
    rows = [
        (relative, path.stat().st_size, _sha256_file(path))
        for relative, path in paths.items()
    ]
    rows.sort()
    payload = json.dumps(rows, ensure_ascii=True, separators=(",", ":"))
    return rows, hashlib.sha256(payload.encode("ascii")).hexdigest()


def _runtime_dependency_tree_inventory(
    root: Path,
) -> tuple[list[tuple[str, int, str]], str]:
    root_resolved = _resolve_plain_root(root, purpose="runtime dependency Stage root")
    if not root_resolved.is_dir():
        raise BuildPythonIdentityError("runtime dependency Stage root is invalid")
    files: dict[str, Path] = {}
    for current, directories, names in os.walk(root_resolved, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *names]:
            child = current_path / name
            if _is_reparse_point(child):
                raise BuildPythonIdentityError(
                    "runtime dependency Stage contains a reparse point"
                )
        for name in names:
            child = current_path / name
            if not child.is_file():
                raise BuildPythonIdentityError(
                    "runtime dependency Stage member is not a regular file"
                )
            relative = child.relative_to(root_resolved).as_posix()
            if relative in files:
                raise BuildPythonIdentityError(
                    "runtime dependency Stage contains duplicate members"
                )
            files[relative] = child
    return _inventory_rows(files)


def _verify_distribution_record(
    package_path: importlib.metadata.PackagePath,
    resolved: Path,
    *,
    display_path: str,
) -> None:
    if package_path.size is not None and resolved.stat().st_size != package_path.size:
        raise BuildPythonIdentityError(
            f"runtime dependency recorded size mismatch: {display_path}"
        )
    recorded_hash = package_path.hash
    if recorded_hash is None:
        return
    if recorded_hash.mode != "sha256":
        raise BuildPythonIdentityError(
            f"runtime dependency recorded hash is unsupported: {display_path}"
        )
    actual_digest = hashlib.sha256(resolved.read_bytes()).digest()
    actual_record = base64.urlsafe_b64encode(actual_digest).rstrip(b"=").decode("ascii")
    if actual_record != recorded_hash.value:
        raise BuildPythonIdentityError(
            f"runtime dependency recorded hash mismatch: {display_path}"
        )


def _assemble_runtime_dependency_stage(
    identity: BuildPythonIdentity,
    stage: Path,
) -> RuntimeDependencyStage:
    stage_resolved = _resolve_plain_root(stage, purpose="runtime dependency Stage")
    if not stage_resolved.is_dir():
        raise BuildPythonIdentityError("runtime dependency Stage is invalid")
    executable = _revalidate_bound_runtime(identity)
    source_root = (executable.parent / "Lib" / "site-packages").resolve(strict=True)
    if not source_root.is_dir() or _is_reparse_point(source_root):
        raise BuildPythonIdentityError("runtime dependency source root is invalid")
    expected = _runtime_dependency_contract()

    indexed: dict[str, list[importlib.metadata.Distribution]] = {}
    for distribution in importlib.metadata.distributions(path=[str(source_root)]):
        name = distribution.metadata.get("Name")
        if not isinstance(name, str) or not name:
            continue
        normalized = _normalize_distribution_name(name)
        if normalized in expected:
            indexed.setdefault(normalized, []).append(distribution)

    selected: dict[str, Path] = {}
    owners: dict[str, str] = {}
    for name, version in expected.items():
        matches = indexed.get(name, [])
        if len(matches) != 1:
            raise BuildPythonIdentityError(
                f"runtime dependency distribution count mismatch: {name}"
            )
        distribution = matches[0]
        if distribution.version != version:
            raise BuildPythonIdentityError(
                f"runtime dependency version mismatch: {name}"
            )
        distribution_root = Path(distribution.locate_file("")).resolve(strict=True)
        if distribution_root != source_root:
            raise BuildPythonIdentityError(
                f"runtime dependency distribution root mismatch: {name}"
            )
        distribution_files = distribution.files
        if distribution_files is None:
            raise BuildPythonIdentityError(
                f"runtime dependency file inventory missing: {name}"
            )
        copied_for_distribution = 0
        for package_path in distribution_files:
            logical_path = Path(str(package_path))
            logical_parts = logical_path.parts
            if logical_path.is_absolute() or "\\" in str(package_path):
                raise BuildPythonIdentityError(
                    f"runtime dependency record path is invalid: {name}"
                )
            if ".." in logical_parts:
                scripts_root = _resolve_plain_root(
                    executable.parent / "Scripts",
                    purpose="runtime dependency build Scripts root",
                )
                located = Path(distribution.locate_file(package_path)).resolve(
                    strict=True
                )
                located = _require_plain_member(
                    located,
                    scripts_root,
                    purpose=f"runtime dependency excluded build script {name}",
                )
                if not located.is_file() or located.suffix.casefold() != ".exe":
                    raise BuildPythonIdentityError(
                        f"runtime dependency escaped record is invalid: {name}"
                    )
                _verify_distribution_record(
                    package_path,
                    located,
                    display_path=str(package_path),
                )
                continue
            located = Path(distribution.locate_file(package_path))
            resolved = _require_plain_member(
                located,
                source_root,
                purpose=f"runtime dependency file {name}",
            )
            if "__pycache__" in {part.casefold() for part in resolved.parts}:
                continue
            if not resolved.is_file():
                raise BuildPythonIdentityError(
                    f"runtime dependency member is not a file: {name}"
                )
            relative = resolved.relative_to(source_root).as_posix()
            previous_owner = owners.get(relative)
            if previous_owner is not None and previous_owner != name:
                raise BuildPythonIdentityError(
                    f"runtime dependency member has multiple owners: {relative}"
                )
            _verify_distribution_record(
                package_path,
                resolved,
                display_path=relative,
            )
            selected[relative] = resolved
            owners[relative] = name
            copied_for_distribution += 1
        if copied_for_distribution == 0:
            raise BuildPythonIdentityError(
                f"runtime dependency has no Stage-owned files: {name}"
            )

    source_rows_before, source_inventory_before = _inventory_rows(selected)
    dependency_root = stage_resolved / "runtime" / "python" / "Lib" / "site-packages"
    if dependency_root.exists():
        raise BuildPythonIdentityError("runtime dependency Stage root already exists")
    dependency_root.mkdir(parents=True)
    for relative, source in selected.items():
        destination = dependency_root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    source_rows_after, source_inventory_after = _inventory_rows(selected)
    stage_rows, stage_inventory = _runtime_dependency_tree_inventory(dependency_root)
    if (
        source_rows_after != source_rows_before
        or source_inventory_after != source_inventory_before
        or stage_rows != source_rows_before
        or stage_inventory != source_inventory_before
    ):
        raise BuildPythonIdentityError(
            "runtime dependency source or Stage changed during assembly"
        )
    return RuntimeDependencyStage(
        root=dependency_root.resolve(strict=True),
        runtime_dependencies=expected,
        file_count=len(stage_rows),
        total_bytes=sum(row[1] for row in stage_rows),
        inventory_sha256=stage_inventory,
    )


def _validate_runtime_dependency_stage(
    dependency_stage: RuntimeDependencyStage,
    stage: Path,
) -> None:
    stage_resolved = _resolve_plain_root(stage, purpose="runtime dependency Stage")
    dependency_root = _require_plain_member(
        dependency_stage.root,
        stage_resolved,
        purpose="runtime dependency Stage root",
    )
    if dependency_root != dependency_stage.root.resolve(strict=True):
        raise BuildPythonIdentityError("runtime dependency Stage root changed")
    rows, inventory_sha256 = _runtime_dependency_tree_inventory(dependency_root)
    if (
        len(rows) != dependency_stage.file_count
        or sum(row[1] for row in rows) != dependency_stage.total_bytes
        or inventory_sha256 != dependency_stage.inventory_sha256
    ):
        raise BuildPythonIdentityError("runtime dependency Stage inventory changed")
    if dependency_stage.runtime_dependencies != _runtime_dependency_contract():
        raise BuildPythonIdentityError("runtime dependency Stage authority changed")


def _run_runtime_python(
    identity: BuildPythonIdentity,
    code: str,
    code_args: Sequence[str],
    stage: Path,
    *,
    module_paths: Sequence[Path],
    dependency_stage: RuntimeDependencyStage,
    env: dict[str, str] | None = None,
) -> None:
    stage_resolved = _resolve_plain_root(stage, purpose="runtime child Stage")
    if not stage_resolved.is_dir():
        raise ValueError("runtime child Stage must be a directory")
    explicit_paths: list[Path] = []
    for path in module_paths:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("runtime child module path must be a directory")
        try:
            resolved.relative_to(stage_resolved)
        except ValueError as exc:
            raise ValueError("runtime child module path is outside the explicit Stage") from exc
        if resolved not in explicit_paths:
            explicit_paths.append(resolved)
    if not explicit_paths:
        raise ValueError("runtime child requires explicit Stage module paths")
    if not isinstance(code, str) or not code:
        raise ValueError("runtime child code must be nonempty")
    if any(not isinstance(value, str) or "\x00" in value for value in code_args):
        raise ValueError("runtime child argument is invalid")

    _validate_runtime_dependency_stage(dependency_stage, stage_resolved)
    runtime_config = {
        "stage": str(stage_resolved),
        "module_paths": [str(path) for path in explicit_paths],
        "dependency_root": str(dependency_stage.root),
        "runtime_dependencies": dependency_stage.runtime_dependencies,
        "inventory": {
            "file_count": dependency_stage.file_count,
            "total_bytes": dependency_stage.total_bytes,
            "sha256": dependency_stage.inventory_sha256,
        },
    }

    executable = _revalidate_bound_runtime(identity)
    try:
        result = subprocess.run(
            [
                str(executable),
                "-E",
                "-s",
                "-S",
                "-B",
                "-c",
                RUNTIME_STAGE_BOOTSTRAP,
                json.dumps(runtime_config, sort_keys=True, separators=(",", ":")),
                code,
                *code_args,
            ],
            cwd=stage_resolved,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    finally:
        _validate_runtime_dependency_stage(dependency_stage, stage_resolved)
        _revalidate_bound_runtime(identity)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-4000:])


def _native_file(directory: Path, stem: str) -> Path:
    matches = [
        directory / f"{stem}{suffix}"
        for suffix in EXTENSION_SUFFIXES
        if (directory / f"{stem}{suffix}").is_file()
    ]
    if len(matches) != 1:
        raise RuntimeError(f"native output count mismatch: {stem}={len(matches)}")
    return matches[0]


def _package_targets(stage: Path, critical: dict[str, object]) -> list[str]:
    targets = [str(item) for item in critical["package_modules"]]
    for tree in critical["package_trees"]:
        targets.extend(str(item) for item in tree["protected_modules"])
    return sorted(set(targets))


def _build_only_source_authorities(manifest: dict[str, object]) -> set[str]:
    raw = manifest.get("build_only_source_authorities")
    if (
        type(raw) is not list
        or len(raw) != 3
        or any(type(item) is not str for item in raw)
    ):
        raise RuntimeError("client protection build-only source contract invalid")
    folded: set[str] = set()
    normalized: set[str] = set()
    for item in raw:
        if (
            not item
            or item != item.strip()
            or "\\" in item
            or item.startswith("/")
            or ":" in item
            or not item.endswith(".py")
            or any(part in {"", ".", ".."} for part in item.split("/"))
        ):
            raise RuntimeError("client protection build-only source contract invalid")
        folded_item = item.casefold()
        if folded_item in folded:
            raise RuntimeError("client protection build-only source contract invalid")
        folded.add(folded_item)
        normalized.add(item)
    return normalized


def _verify_exhaustive_source_contract(manifest: dict[str, object]) -> None:
    critical = manifest["critical_native"]
    build_only = _build_only_source_authorities(manifest)
    declared = {
        *(f"hero_ref/src/{item}.py" for item in critical["engine_modules"]),
        *(f"hero_ref/tools/{item}.py" for item in critical["tool_modules"]),
        *(f"src/{item}" for item in critical["package_modules"]),
        *(
            f"src/{item}"
            for tree in critical["package_trees"]
            for item in tree["protected_modules"]
        ),
        *manifest["plaintext_allowlist"],
        *manifest["encrypted_stage_excludes"],
        *build_only,
        critical["overlay"]["source"],
    }
    actual = {
        path.relative_to(ROOT).as_posix()
        for raw_root in manifest["source_roots"]
        for path in (ROOT / raw_root).rglob("*.py")
    }
    missing = sorted(actual - declared)
    stale = sorted(declared - actual)
    if missing or stale:
        raise RuntimeError(
            "client protection source contract mismatch: "
            f"unlisted={len(missing)} stale={len(stale)}"
        )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-python",
        type=Path,
        help="absolute build interpreter path; defaults to the current sys.executable",
    )
    parser.add_argument(
        "--identity-only",
        action="store_true",
        help="verify and print the frozen interpreter identity without compiling",
    )
    parser.add_argument(
        "--legacy-cp3133-identity-only",
        action="store_true",
        help=(
            "verify the frozen CPython 3.13.3 legacy control only; never compile or "
            "claim 3.13.15 eligibility"
        ),
    )
    args = parser.parse_args(argv)
    if args.legacy_cp3133_identity_only and args.build_python is None:
        parser.error("--legacy-cp3133-identity-only requires --build-python")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    contract = (
        _legacy_cp3133_contract()
        if args.legacy_cp3133_identity_only
        else _runtime_source_contract()
    )
    try:
        build_python = _bind_build_python(args.build_python, contract)
    except BuildPythonIdentityError as exc:
        print(f"CLIENT_PROTECTION_BUILD_PYTHON_REJECTED {exc}", file=sys.stderr)
        return 2
    print(
        "CLIENT_PROTECTION_BUILD_PYTHON_IDENTITY "
        + json.dumps(build_python.receipt(), sort_keys=True, separators=(",", ":"))
    )
    if args.identity_only or args.legacy_cp3133_identity_only:
        build_python.close()
        return 0

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        critical = manifest["critical_native"]
        _verify_exhaustive_source_contract(manifest)
        temp = Path(tempfile.mkdtemp(prefix="bidking_client_protection_"))
    except BaseException:
        build_python.close()
        raise
    stage = temp / "stage"
    try:
        shutil.copytree(ROOT / "hero_ref" / "src", stage / "hero_ref" / "src")
        shutil.copytree(ROOT / "hero_ref" / "tools", stage / "hero_ref" / "tools")
        shutil.copytree(ROOT / "hero_ref" / "assets", stage / "hero_ref" / "assets")
        shutil.copytree(ROOT / "src", stage / "src")
        runtime_dependency_stage = _assemble_runtime_dependency_stage(
            build_python,
            stage,
        )

        engine_build = temp / "engine"
        engine_build.mkdir()
        engine_modules = [str(item) for item in critical["engine_modules"]]
        for module in engine_modules:
            shutil.copy2(stage / "hero_ref" / "src" / f"{module}.py", engine_build)
        shutil.copy2(
            ROOT / "scripts" / "cython" / "ahmad_ref_engine.pxd", engine_build
        )
        (engine_build / "_setup.py").write_text(
            SETUP_COMMON
            + """\
mods = sys.argv[1:]
sys.argv = [sys.argv[0], "build_ext", "--inplace"]
exts = [Extension(m, [m + ".py"], extra_compile_args=COMPILE_FLAGS,
                  extra_link_args=LINK_FLAGS) for m in mods]
setup(ext_modules=cythonize(exts, language_level=3, quiet=True,
                            compiler_directives={"embedsignature": False,
                                                 "emit_code_comments": False}))
""",
            encoding="ascii",
        )
        _run_compile_python(
            build_python,
            engine_build / "_setup.py",
            engine_modules,
            engine_build,
        )
        for module in engine_modules:
            shutil.copy2(
                _native_file(engine_build, module), stage / "hero_ref" / "src"
            )
            (stage / "hero_ref" / "src" / f"{module}.py").unlink()

        tools = stage / "hero_ref" / "tools"
        tool_modules = [str(item) for item in critical["tool_modules"]]
        overlay = critical["overlay"]
        overlay_source = Path(str(overlay["source"])).name
        overlay_native = str(overlay["native_module"])
        (tools / "_setup.py").write_text(
            SETUP_COMMON
            + f"""\
_cyopt.error_on_unknown_names = False
mods = sys.argv[1:]
sys.argv = [sys.argv[0], "build_ext", "--inplace"]
exts = [Extension({overlay_native!r}, [{overlay_source!r}],
                  extra_compile_args=COMPILE_FLAGS, extra_link_args=LINK_FLAGS)]
exts.extend(Extension(m, [m + ".py"], extra_compile_args=COMPILE_FLAGS,
                      extra_link_args=LINK_FLAGS) for m in mods)
setup(ext_modules=cythonize(exts, language_level=3, quiet=True,
                            compiler_directives={{"embedsignature": False,
                                                 "emit_code_comments": False}}))
""",
            encoding="ascii",
        )
        _run_compile_python(
            build_python,
            tools / "_setup.py",
            tool_modules,
            tools,
        )
        _native_file(tools, overlay_native)
        (tools / overlay_source).unlink()
        for module in tool_modules:
            _native_file(tools, module)
            (tools / f"{module}.py").unlink()

        package_root = stage / "src"
        package_targets = _package_targets(stage, critical)
        (package_root / "_setup.py").write_text(
            SETUP_COMMON
            + """\
mods = sys.argv[1:]
sys.argv = [sys.argv[0], "build_ext", "--inplace"]
exts = [Extension(m[:-3].replace("/", "."), [m],
                  extra_compile_args=COMPILE_FLAGS,
                  extra_link_args=LINK_FLAGS) for m in mods]
setup(ext_modules=cythonize(exts, language_level=3, quiet=True,
                            compiler_directives={"embedsignature": False,
                                                 "emit_code_comments": False}))
""",
            encoding="ascii",
        )
        _run_compile_python(
            build_python,
            package_root / "_setup.py",
            package_targets,
            package_root,
        )
        for relative in package_targets:
            source = package_root / relative
            _native_file(source.parent, source.stem)
            source.unlink()
            source.with_suffix(".c").unlink(missing_ok=True)

        fixture_specs = [
            {
                "path": ROOT
                / "tests"
                / "fixtures"
                / "live"
                / "settlement_5604_f3_split_npcap_live.jsonl",
                "map_id": 5604,
                "original_pool_id": 2704,
                "expected_status": "sampleable",
                "package_generation": "76b7ca-2026-10-08-723", "model_scope_count": 112,
                "support_status": "", "support_unresolved_probability_ppm": 0,
                "hero": "ahmed",
                "inventory_count": 33,
                "settlement_incomplete": False,
            },
            {
                "path": ROOT
                / "tests"
                / "fixtures"
                / "live"
                / "settlement_2508_tail_desync_npcap_live.jsonl",
                "map_id": 2508,
                "original_pool_id": 2508,
                "expected_status": "sampleable",
                "package_generation": "76b7ca-2026-10-08-723", "model_scope_count": 112,
                "hero": "ahmed",
                "inventory_count": 47,
                "settlement_incomplete": True,
                "declared_item_count": 51,
                "support_status": "", "support_unresolved_probability_ppm": 0,
            },
        ]
        fixtures = [spec["path"] for spec in fixture_specs]
        runtime_specs = [
            {key: value for key, value in spec.items() if key != "path"}
            for spec in fixture_specs
        ]
        if not all(path.is_file() for path in fixtures):
            raise RuntimeError("client protection runtime fixture missing")
        protected_names = [
            str(overlay["native_module"]),
            *tool_modules,
            *[
                relative[:-3].replace("/", ".")
                for relative in package_targets
            ],
        ]
        smoke = """\
import hashlib, importlib, json, os, sys
from dataclasses import replace
import _cffi_backend
import annotated_types
import certifi
import cffi
import cryptography
import numpy
import PIL
import psutil
import pycparser
import pydantic
import pydantic_core
import typing_extensions
import typing_inspection
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image
from pathlib import Path
dependency_root = (Path.cwd() / "runtime" / "python" / "Lib" / "site-packages").resolve()
dependency_modules = (
    _cffi_backend, annotated_types, certifi, cffi, cryptography, numpy, PIL,
    psutil, pycparser, pydantic, pydantic_core, typing_extensions,
    typing_inspection,
)
assert all(
    Path(module.__file__).resolve().is_relative_to(dependency_root)
    for module in dependency_modules
)
assert int(numpy.arange(3).sum()) == 3
assert Image.core is not None
assert len(AESGCM.generate_key(bit_length=128)) == 16
assert psutil.Process().pid > 0
assert Path(certifi.where()).resolve().is_relative_to(dependency_root)
assert Path(certifi.where()).stat().st_size > 10000
names = __PROTECTED_NAMES__
fixture_specs = __FIXTURE_SPECS__
mods = [importlib.import_module(name) for name in names]
assert all(str(mod.__file__).lower().endswith(".pyd") for mod in mods)
overlay = mods[0]
panel = importlib.import_module("ahmad_live_panel_server")
assert str(panel.__file__).lower().endswith(".pyd")
assert overlay._flag_color("risk").startswith("#")
assert overlay._flag_color("watch").startswith("#")
assert overlay.AhmadTkOverlay.__name__ == "AhmadTkOverlay"
authority_module = importlib.import_module("bidking_lab.data.item_category_authority")
assert str(authority_module.__file__).lower().endswith(".pyd")
from bidking_lab.secure.data_crypt import encrypt_compressed_bytes
records = []
sources = {
    "item_table": {
        "archive_sha256": "2" * 64,
        "columns": 41,
        "compression": "none",
        "hash_mode": "raw_uncompressed_bytes",
        "rows": 0,
        "sha256": "1" * 64,
    },
    "items_json": {
        "hash_mode": "canonical_json_utf8",
        "overlap_rows": 0,
        "rows": 0,
        "sha256": "3" * 64,
    },
    "legacy_category": {
        "hash_mode": "canonical_json_utf8",
        "overlap_rows": 0,
        "rows": 0,
        "sha256": "4" * 64,
    },
    "items_droppable": {
        "hash_mode": "canonical_json_utf8",
        "overlap_rows": 0,
        "rows": 0,
        "sha256": "5" * 64,
    },
}
document = {
    "generation": "item-table-sha256:" + "1" * 64,
    "primary_category_policy": authority_module.PRIMARY_CATEGORY_POLICY,
    "records": records,
    "records_sha256": authority_module.sha256_json(records),
    "schema_version": authority_module.SCHEMA_VERSION,
    "sources": sources,
    "taxonomy": authority_module.TAXONOMY,
}
document["authority_identity"] = authority_module.sha256_json(
    authority_module.authority_identity_payload(document)
)
authority_path = Path("data/processed/item_category_authority_0_3_2.json")
authority_path.parent.mkdir(parents=True, exist_ok=True)
authority_path.write_bytes(
    encrypt_compressed_bytes(
        json.dumps(document, ensure_ascii=False).encode("utf-8"),
        bytes(range(32)),
        member_path="data/processed/item_category_authority_0_3_2.json",
    )
)
authority, loaded_document = authority_module.load_item_category_authority_document(
    authority_path,
    expected_item_table_sha256="1" * 64,
)
assert authority.identity == document["authority_identity"]
assert not authority.records
assert loaded_document["authority_identity"] == document["authority_identity"]
from bidking_lab.live.fatbeans import parse_fatbeans_capture_payload
from bidking_lab.live.monitor import load_monitor_tables, build_monitor_artifact_from_payload
from bidking_lab.simulation.basic_mc import (
    DropPoolResolutionError,
    flatten_pool,
)
def require_current_fixture_authority(tables, map_id, pool_id, generation, scope_count):
    assert generation == '76b7ca-2026-10-08-723' and type(scope_count) is int and scope_count == 112
    receipt = tables.rankmap_distribution_receipt
    assert tables.rankmap_distribution_status == 'ok' and isinstance(receipt, dict)
    assert receipt.get('status') == 'current_rankmap_algorithm_prior'
    assert receipt.get('package_generation') == generation
    assert receipt.get('authority_mode') == 'rankmap_drop_model_algorithm_prior'
    assert type(receipt.get('scope_map_count')) is int and receipt['scope_map_count'] == scope_count
    assert receipt.get('scope') == 'physical_map_conditioned_ordinary_and_ranked'
    assert receipt.get('model_distribution_authority') is True
    assert receipt.get('official_distribution_authority') is False
    assert receipt.get('runtime_open_claim') is False
    assert receipt.get('drop_or_dropmap_textasset') is False
    assert receipt.get('claims_item_level_drop_graph') is False
    assert receipt.get('drop_or_dropmap_absence_is_not_a_blocker') is True
    assert tables.drops.package_generation == generation
    assert tables.drops.authority_mode == 'rankmap_drop_model_algorithm_prior'
    ids = tables.drops.bound_map_ids
    assert len(ids) == scope_count and all(type(mid) is int for mid in ids)
    assert hashlib.sha256(json.dumps(sorted(ids), separators=(',', ':')).encode('ascii')).hexdigest() == '78bab49717c2a3884ea49e8452cc779544ce4ee8bad5dce49e513e5bbfb4c99d'
    assert map_id in ids and tables.maps[map_id].drop_pool_id == pool_id
    flattened = flatten_pool(pool_id, tables.drops, tables.items, map_id=map_id)
    assert flattened.item_ids and flattened.resolution_status == 'complete'
    assert flattened.resolution_policy == 'rankmap_drop_model_algorithm_prior'
    assert flattened.resolution_generation == generation

def require_approved_runtime_fixture_spec(spec):
    approved = {
        5604: dict(map_id=5604, original_pool_id=2704, expected_status='sampleable',
            package_generation='76b7ca-2026-10-08-723', model_scope_count=112, support_status='',
            support_unresolved_probability_ppm=0, hero='ahmed', inventory_count=33, settlement_incomplete=False),
        2508: dict(map_id=2508, original_pool_id=2508, expected_status='sampleable',
            package_generation='76b7ca-2026-10-08-723', model_scope_count=112, support_status='',
            support_unresolved_probability_ppm=0, hero='ahmed', inventory_count=47, settlement_incomplete=True,
            declared_item_count=51),
    }
    expected = approved.get(spec.get('map_id'))
    assert expected is not None and set(spec) == set(expected)
    assert all(type(spec[key]) is type(value) and spec[key] == value for key, value in expected.items())
assert len(sys.argv[1:]) == len(fixture_specs)
# This source-side probe replays historical fixtures and must not borrow the
# developer machine's currently installed game generation.  Keep the exact
# packaged tables while selecting the monitor's explicit offline path.
tables = replace(
    load_monitor_tables(),
    warehouse_live_identity_check=False,
)
for index, (fixture, spec) in enumerate(zip(sys.argv[1:], fixture_specs, strict=True)):
    require_approved_runtime_fixture_spec(spec)
    require_current_fixture_authority(tables, spec['map_id'], spec['original_pool_id'], spec['package_generation'], spec['model_scope_count'])
    raw = open(fixture, encoding="utf-8", errors="replace").read()
    try:
        json.loads(raw)
        payload = raw
    except Exception:
        payload = "[" + ",".join(line for line in raw.splitlines() if line.strip()) + "]"
    parsed = parse_fatbeans_capture_payload(payload)
    settled = next(
        state for state in reversed(parsed.states) if state.message_id == 0x002D
    )
    fixture_map_id = settled.map_id
    if fixture_map_id != spec["map_id"]:
        raise AssertionError("runtime fixture map identity mismatch")
    assert tables.maps[spec["map_id"]].drop_pool_id == spec["original_pool_id"]
    assert len(settled.inventory_items) == spec["inventory_count"]
    if spec["map_id"] == 2508:
        recovered_ids = {item.item_id for item in settled.inventory_items}
        assert {1086008, 1096009}.issubset(recovered_ids)
    assert spec["expected_status"] == "sampleable"
    artifact = build_monitor_artifact_from_payload(
        payload, file="protection-current-authority", tables=tables,
        n_trials=40, roi_trials=20,
    )
    assert artifact["map_id"] == spec["map_id"]
    assert artifact["model_map_id"] == spec["map_id"]
    assert artifact["map_alias_mode"] == ""
    assert artifact["hero"] == spec["hero"]
    assert artifact["phase"] == "settled"
    assert artifact["inventory_count"] == spec["inventory_count"]
    assert artifact["settlement_incomplete"] is spec["settlement_incomplete"]
    assert artifact["catalog_value_complete"] is True
    if spec["settlement_incomplete"]:
        detail = artifact.get("settlement_incomplete_detail")
        assert isinstance(detail, dict)
        assert detail["captured_items"] == spec["inventory_count"]
        assert detail["declared_items"] == spec["declared_item_count"]
        assert artifact.get("model_eval_skipped_reason") == "settlement_incomplete"
        assert "model_eval" not in artifact
        truth = artifact["ui_contract"]["truth"]
        assert truth["complete"] is False
        assert truth["partial"] is True
        assert truth["source"] == "partial_settlement_capture"
    assert (
        isinstance(artifact.get("final_decision_value"), int)
        and artifact["final_decision_value"] > 0
    )
    snapshot_path = Path(f"native-summary-{{index}}.json")
    snapshot_path.write_text(json.dumps(artifact), encoding="utf-8")
    summary = panel.summarize_snapshot(artifact, snapshot_path=snapshot_path)
    assert isinstance(summary, dict) and summary
    support = artifact.get("drop_pool_support")
    assert support is None
    assert summary.get("drop_pool_support") == support
    assert summary.get("diagnostics", {}).get("drop_pool_support") == support
try:
    flatten_pool(1066, tables.drops, tables.items)
except DropPoolResolutionError as exc:
    assert exc.reason == "unknown_item"
    assert exc.pool_id == 1066
    assert exc.item_id == 1106013
else:
    raise AssertionError("unauthorized root and leaf did not fail closed")
print("CLIENT_PROTECTION_RUNTIME_OK")
""".replace("__PROTECTED_NAMES__", repr(protected_names)).replace(
            "__FIXTURE_SPECS__", repr(runtime_specs)
        )
        env = dict(os.environ)
        env["BIDKING_DATA_KEY"] = base64.b64encode(bytes(range(32))).decode("ascii")
        env.pop("PYTHONPATH", None)
        env["BIDKING_PROJECT_ROOT"] = str(ROOT)
        env["BIDKING_LAB_ROOT"] = str(ROOT)
        _run_runtime_python(
            build_python,
            smoke,
            [str(path) for path in fixtures],
            stage,
            module_paths=(stage, tools, stage / "hero_ref" / "src", package_root),
            dependency_stage=runtime_dependency_stage,
            env=env,
        )
        print(
            "CLIENT_PROTECTION_RUNTIME_OK "
            f"engine={len(engine_modules)} tools={len(tool_modules) + 1} "
            f"package={len(package_targets)}"
        )
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)
        build_python.close()


if __name__ == "__main__":
    raise SystemExit(main())
