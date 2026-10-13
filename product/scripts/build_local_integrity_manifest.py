"""Build the signed, offline local-integrity manifest for encrypted candidates.

The private Ed25519 seed is an external release input.  This module never creates a
key, copies it into Stage, or prints it.  Runtime verification lives in the native
``bidking_kernel`` trust root; this build helper only freezes the exact Stage bytes.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import stat
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


MANIFEST_SCHEMA = "bidking_local_integrity_manifest_v1"
CONTRACT_SCHEMA = "bidking_local_integrity_contract_v1"
MANIFEST_VERSION = 1
_REPARSE_POINT = 0x400


class ContractError(RuntimeError):
    """A safe, non-secret reason why the manifest cannot be formed."""


def _safe_relative(raw: str) -> PurePosixPath:
    if not raw or "\\" in raw:
        raise ContractError("member_path_invalid")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError("member_path_invalid")
    return path


def _identity(info: os.stat_result) -> tuple[int, int]:
    identity = (int(info.st_dev), int(info.st_ino))
    if identity[1] == 0:
        raise ContractError("file_identity_unavailable")
    return identity


def _reject_reparse_or_symlink(info: os.stat_result) -> None:
    attributes = int(getattr(info, "st_file_attributes", 0))
    if stat.S_ISLNK(info.st_mode) or attributes & _REPARSE_POINT:
        raise ContractError("reparse_or_symlink_rejected")


class _DirectoryLock:
    """One non-share-write/delete directory handle plus its stable identity."""

    def __init__(self, path: Path) -> None:
        self._descriptor: int | None = None
        self._windows_handle: int | None = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _FileInformation(ctypes.Structure):
                _fields_ = [
                    ("attributes", wintypes.DWORD),
                    ("creation_time", wintypes.FILETIME),
                    ("access_time", wintypes.FILETIME),
                    ("write_time", wintypes.FILETIME),
                    ("volume_serial", wintypes.DWORD),
                    ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD),
                    ("links", wintypes.DWORD),
                    ("file_index_high", wintypes.DWORD),
                    ("file_index_low", wintypes.DWORD),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE
            handle = create_file(
                str(path),
                0x8000_0000,
                0x0000_0001,
                None,
                3,
                0x0200_0000 | 0x0020_0000,
                None,
            )
            if handle == wintypes.HANDLE(-1).value:
                raise ContractError("stable_directory_open_failed")
            get_information = kernel32.GetFileInformationByHandle
            get_information.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_FileInformation),
            ]
            get_information.restype = wintypes.BOOL
            information = _FileInformation()
            if not get_information(handle, ctypes.byref(information)):
                close_handle = kernel32.CloseHandle
                close_handle.argtypes = [wintypes.HANDLE]
                close_handle.restype = wintypes.BOOL
                close_handle(handle)
                raise ContractError("directory_identity_unavailable")
            self._windows_handle = int(handle)
            self.identity = (
                int(information.volume_serial),
                (int(information.file_index_high) << 32)
                | int(information.file_index_low),
            )
        else:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                self._descriptor = os.open(path, flags)
            except OSError as exc:
                raise ContractError("stable_directory_open_failed") from exc
            self.identity = _identity(os.fstat(self._descriptor))

    def close(self) -> None:
        if self._windows_handle is not None:
            import ctypes
            from ctypes import wintypes

            close_handle = ctypes.WinDLL(
                "kernel32", use_last_error=True
            ).CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(self._windows_handle)
            self._windows_handle = None
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None


class _StablePathGuard:
    """Retain and recheck every package directory used by manifest collection."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root))
        self._directories: dict[str, _DirectoryLock] = {}
        self._directory_paths: dict[str, Path] = {}
        self._file_identities: set[tuple[int, int]] = set()
        self._ensure_directory(())

    def close(self) -> None:
        for handle in reversed(list(self._directories.values())):
            handle.close()
        self._directories.clear()
        self._directory_paths.clear()

    def __enter__(self) -> _StablePathGuard:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _ensure_directory(self, parts: tuple[str, ...]) -> Path:
        current = self.root
        prefixes: list[str] = []
        for part in ("", *parts):
            if part:
                prefixes.append(part)
                current = current / part
            key = "/".join(prefixes).casefold()
            if key in self._directories:
                continue
            try:
                before = current.stat(follow_symlinks=False)
            except FileNotFoundError as exc:
                raise ContractError("required_directory_missing") from exc
            _reject_reparse_or_symlink(before)
            if not stat.S_ISDIR(before.st_mode):
                raise ContractError("required_directory_not_directory")
            rebound = _DirectoryLock(current)
            try:
                after = current.stat(follow_symlinks=False)
                _reject_reparse_or_symlink(after)
                if not stat.S_ISDIR(after.st_mode):
                    raise ContractError("directory_identity_changed")
                self._directories[key] = rebound
                self._directory_paths[key] = current
            except Exception:
                rebound.close()
                raise
        return current

    def recheck(self) -> None:
        for key, existing in self._directories.items():
            current = self._directory_paths[key]
            try:
                info = current.stat(follow_symlinks=False)
            except FileNotFoundError as exc:
                raise ContractError("directory_identity_changed") from exc
            _reject_reparse_or_symlink(info)
            rebound = _DirectoryLock(current)
            try:
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or rebound.identity != existing.identity
                ):
                    raise ContractError("directory_identity_changed")
            finally:
                rebound.close()

    def ensure_directory(self, relative: str) -> Path:
        safe = _safe_relative(relative)
        return self._ensure_directory(tuple(safe.parts))

    def read_file(self, relative: str) -> bytes:
        safe = _safe_relative(relative)
        self._ensure_directory(tuple(safe.parts[:-1]))
        path = self.root.joinpath(*safe.parts)
        payload, identity = _read_stable_file_with_identity(path)
        if identity in self._file_identities:
            raise ContractError("member_file_identity_collision")
        self._file_identities.add(identity)
        return payload


def _read_stable_file_with_identity(
    path: Path,
) -> tuple[bytes, tuple[int, int]]:
    try:
        before = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ContractError("required_member_missing") from exc
    _reject_reparse_or_symlink(before)
    if not stat.S_ISREG(before.st_mode):
        raise ContractError("required_member_not_file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractError("stable_file_open_failed") from exc
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_handle = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    try:
        after_path = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ContractError("file_identity_changed") from exc
    _reject_reparse_or_symlink(after_path)
    identities = {
        _identity(before),
        _identity(opened),
        _identity(after_handle),
        _identity(after_path),
    }
    if len(identities) != 1:
        raise ContractError("file_identity_changed")
    payload = b"".join(chunks)
    if len(payload) != opened.st_size or opened.st_size != after_handle.st_size:
        raise ContractError("file_identity_changed")
    return payload, _identity(opened)


def _read_stable_file(path: Path) -> bytes:
    return _read_stable_file_with_identity(path)[0]


def _read_json(path: Path, *, expected_schema: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_stable_file(path), object_pairs_hook=_strict_json_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("json_invalid") from exc
    if not isinstance(value, dict):
        raise ContractError("json_invalid")
    if expected_schema is not None and value.get("schema") != expected_schema:
        raise ContractError("schema_invalid")
    return value


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("json_duplicate_key")
        result[key] = value
    return result


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    if not path.is_file():
        raise ContractError("signing_key_missing")
    raw_file = _read_stable_file(path)
    if len(raw_file) == 32:
        raw_key = raw_file
    else:
        text = raw_file.strip()
        if len(text) == 64:
            try:
                raw_key = bytes.fromhex(text.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                raw_key = b""
        else:
            try:
                raw_key = base64.b64decode(text, validate=True)
            except (binascii.Error, ValueError):
                raw_key = b""
    if len(raw_key) != 32:
        raise ContractError("signing_key_invalid")
    return Ed25519PrivateKey.from_private_bytes(raw_key)


def _public_key_receipt(private_key: Ed25519PrivateKey) -> dict[str, Any]:
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return {
        "schema": "bidking_local_integrity_public_key_v1",
        "public_key_b64": base64.b64encode(public_key).decode("ascii"),
        "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
    }


def public_key_receipt(signing_key_path: Path) -> dict[str, Any]:
    return _public_key_receipt(_load_private_key(signing_key_path))


def _iter_tree_files(
    stage: Path,
    relative_root: str,
    guard: _StablePathGuard,
) -> list[Path]:
    root = guard.ensure_directory(relative_root)
    result: list[Path] = []
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            directory = current_path / name
            relative = directory.relative_to(stage).as_posix()
            guard.ensure_directory(relative)
        for name in files:
            candidate = current_path / name
            _reject_reparse_or_symlink(candidate.stat(follow_symlinks=False))
            result.append(candidate)
    if not result:
        raise ContractError("required_tree_empty")
    return result


def _native_artifact(stage: Path, root: str, module_source: str) -> str:
    source = PurePosixPath(module_source)
    module_dir = stage / root / Path(*source.parent.parts)
    stem = source.stem
    candidates = sorted(
        path
        for path in module_dir.glob(f"{stem}*.pyd")
        if path.is_file() and (path.name == f"{stem}.pyd" or path.name.startswith(f"{stem}."))
    )
    if len(candidates) != 1:
        raise ContractError("protected_native_artifact_count_invalid")
    plaintext = module_dir / f"{stem}.py"
    if plaintext.exists():
        raise ContractError("protected_plaintext_present")
    return candidates[0].relative_to(stage).as_posix()


def _protected_roles(stage: Path, protection: dict[str, Any]) -> dict[str, str]:
    try:
        critical = protection["critical_native"]
        engine_modules = list(critical["engine_modules"])
        tool_modules = list(critical["tool_modules"])
        overlay = dict(critical["overlay"])
        package_modules = list(critical["package_modules"])
        package_trees = list(critical["package_trees"])
    except (KeyError, TypeError) as exc:
        raise ContractError("protection_manifest_invalid") from exc

    roles: dict[str, str] = {}
    for module in engine_modules:
        roles[_native_artifact(stage, "hero_ref/src", f"{module}.py")] = "engine-native"
    for module in tool_modules:
        roles[_native_artifact(stage, "hero_ref/tools", f"{module}.py")] = "tool-native"
    overlay_module = str(overlay.get("native_module", ""))
    if not overlay_module:
        raise ContractError("protection_manifest_invalid")
    roles[
        _native_artifact(stage, "hero_ref/tools", f"{overlay_module}.py")
    ] = "overlay-native"

    for module in package_modules:
        roles[_native_artifact(stage, "src", str(module))] = "package-native"
    for tree in package_trees:
        try:
            tree_path = str(tree["path"]).replace("\\", "/")
            modules = list(tree["protected_modules"])
        except (KeyError, TypeError) as exc:
            raise ContractError("protection_manifest_invalid") from exc
        role = "package-native"
        if tree_path == "bidking_lab/capture":
            role = "parser-native"
        elif tree_path == "bidking_lab/secure":
            role = "secure-native"
        for module in modules:
            roles[_native_artifact(stage, "src", str(module))] = role

    kernel_candidates = sorted((stage / "src").glob("bidking_kernel*.pyd"))
    if len(kernel_candidates) != 1:
        raise ContractError("trust_root_native_artifact_count_invalid")
    roles[kernel_candidates[0].relative_to(stage).as_posix()] = "trust-root-native"
    return roles


def _collect_entries(
    stage: Path,
    contract: dict[str, Any],
    protection: dict[str, Any],
) -> list[dict[str, Any]]:
    stage = Path(os.path.abspath(stage))
    selected: dict[str, str] = {}

    with _StablePathGuard(stage) as guard:
        for item in contract.get("fixed_members", []):
            relative = _safe_relative(str(item.get("path", ""))).as_posix()
            role = str(item.get("role", ""))
            if not role:
                raise ContractError("contract_role_invalid")
            selected[relative] = role

        for item in contract.get("trees", []):
            prefix = _safe_relative(str(item.get("path", ""))).as_posix()
            role = str(item.get("role", ""))
            if not role:
                raise ContractError("contract_role_invalid")
            for path in _iter_tree_files(stage, prefix, guard):
                relative = path.relative_to(stage).as_posix()
                selected.setdefault(relative, role)

        native_roles = _protected_roles(stage, protection)
        for relative, role in native_roles.items():
            if relative not in selected:
                raise ContractError("protected_native_outside_contract")
            selected[relative] = role

        folded = [path.casefold() for path in selected]
        if len(folded) != len(set(folded)):
            raise ContractError("member_case_collision")

        entries: list[dict[str, Any]] = []
        for relative in sorted(selected, key=str.casefold):
            payload = guard.read_file(relative)
            entries.append(
                {
                    "bytes": len(payload),
                    "path": relative,
                    "role": selected[relative],
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        guard.recheck()

    counts = Counter(str(entry["role"]) for entry in entries)
    for role, minimum in contract.get("required_role_minimums", {}).items():
        if type(minimum) is not int or minimum < 1:
            raise ContractError("contract_role_minimum_invalid")
        if counts[str(role)] < minimum:
            raise ContractError("required_role_minimum_not_met")
    return entries


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def build_signed_manifest(
    *,
    stage: Path,
    contract_path: Path,
    protection_manifest_path: Path,
    signing_key_path: Path,
    release_id: str,
) -> dict[str, Any]:
    if not release_id or len(release_id) > 256 or any(ch in release_id for ch in "\r\n"):
        raise ContractError("release_id_invalid")
    stage = Path(os.path.abspath(stage))
    contract = _read_json(contract_path, expected_schema=CONTRACT_SCHEMA)
    if (
        set(contract)
        != {
            "schema",
            "contract_version",
            "manifest_member",
            "signature_member",
            "fixed_members",
            "trees",
            "required_role_minimums",
        }
        or contract["contract_version"] != 1
        or type(contract["fixed_members"]) is not list
        or type(contract["trees"]) is not list
        or type(contract["required_role_minimums"]) is not dict
    ):
        raise ContractError("contract_invalid")
    protection = _read_json(protection_manifest_path)
    private_key = _load_private_key(signing_key_path)
    public_receipt = _public_key_receipt(private_key)
    entries = _collect_entries(stage, contract, protection)
    manifest = {
        "entries": entries,
        "manifest_version": MANIFEST_VERSION,
        "public_key_sha256": public_receipt["public_key_sha256"],
        "release_id": release_id,
        "schema": MANIFEST_SCHEMA,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    signature = private_key.sign(manifest_bytes)

    manifest_member = _safe_relative(str(contract.get("manifest_member", "")))
    signature_member = _safe_relative(str(contract.get("signature_member", "")))
    manifest_path = stage / Path(*manifest_member.parts)
    signature_path = stage / Path(*signature_member.parts)
    _atomic_write(manifest_path, manifest_bytes)
    _atomic_write(signature_path, base64.b64encode(signature) + b"\n")
    return {
        "schema": "bidking_local_integrity_build_receipt_v1",
        "entry_count": len(entries),
        "manifest_member": manifest_member.as_posix(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "public_key_sha256": public_receipt["public_key_sha256"],
        "signature_member": signature_member.as_posix(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    public_key = subparsers.add_parser("public-key")
    public_key.add_argument("--signing-key", type=Path, required=True)
    sign_manifest = subparsers.add_parser("sign")
    sign_manifest.add_argument("--stage", type=Path, required=True)
    sign_manifest.add_argument("--contract", type=Path, required=True)
    sign_manifest.add_argument("--protection-manifest", type=Path, required=True)
    sign_manifest.add_argument("--signing-key", type=Path, required=True)
    sign_manifest.add_argument("--release-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "public-key":
            result = public_key_receipt(args.signing_key)
        else:
            result = build_signed_manifest(
                stage=args.stage,
                contract_path=args.contract,
                protection_manifest_path=args.protection_manifest,
                signing_key_path=args.signing_key,
                release_id=args.release_id,
            )
    except ContractError as exc:
        print(f"LOCAL_INTEGRITY_ERROR={exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
