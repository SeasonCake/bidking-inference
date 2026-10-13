"""Fail-closed release availability gate for the encrypted hotfix3 package.

All author captures remain external. The checker verifies their exact byte
identity, loads every product module and table from the explicit package root,
and emits only support-safe hashes, sizes, counts and verdicts.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import ctypes
from ctypes import wintypes
import dataclasses
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from types import ModuleType
from typing import Any


RECEIPT_SCHEMA = "bidking_release_availability_receipt_v1"
CAPTURE_IDENTITY_RECEIPT_SCHEMA = "bidking_release_capture_identity_receipt_v1"
CAPTURE_IDENTITY_ENV = "BIDKING_RELEASE_CAPTURE_IDENTITY_RECEIPT"
_RELEASE_CAPTURE_HOLDER_CONTROL_FRAMES = frozenset(
    {
        b"release\n",
        b"release\r\n",
        b"\xef\xbb\xbfrelease\n",
        b"\xef\xbb\xbfrelease\r\n",
    }
)
CURRENT_2409_INPUT_SHA256 = (
    "8407441f9e70e8ba4be28dec8e570252806da61c7ca3b6696a416e21ef78d87d"
)
EXPECTED_POLICY_CENSUS = {
    "policy_roots": 101,
    "direct_root": {
        "complete": 23,
        "degraded": 58,
        "sampleable": 81,
        "missing": 20,
        "empty": 0,
        "unsupported": 20,
        "unsupported_ids_sha256": (
            "31e3ab6f0e998e13539d94c1c0df977781c4d86cff819b41f8221edd1c9c920c"
        ),
    },
    "source_map": {
        "complete": 23,
        "degraded": 78,
        "sampleable": 101,
        "missing": 0,
        "empty": 0,
        "unsupported": 0,
        "unsupported_ids_sha256": (
            "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
        ),
    },
}
EXPECTED_ROUTE_SCOPE = {
    "changed_route_count": 16,
    "new_only_count": 42,
    "legacy_only_count": 8,
    "forbidden_count": 66,
    "current_activity_alias_sampleable_count": 20,
    "ranked_production_unsupported_count": 0,
    "changed_route_ids_sha256": (
        "f43e639dbbdeb3d855cce7aa4eb21a94a8923ca21ef49f3d944e8496a89adbec"
    ),
    "new_only_ids_sha256": (
        "493adee43dcb7af1214ee561b971f679b9823859b82d2393d67da9082482fb98"
    ),
    "legacy_only_ids_sha256": (
        "ff51a78e43e1e3af45d851b8d2f7077a2dbd761cfaf25824824ae43ec37515a7"
    ),
    "forbidden_ids_sha256": (
        "53db1aad267ce4b33f05e4b1fbc2fa6ce24576681f385d1a87ec369c9829f32a"
    ),
    "policy_root_ids_sha256": (
        "228cc1f5ef22b92c118e6ce6564615eb581767a3e426677503b1b2a3d77d3f64"
    ),
    "current_activity_alias_ids_sha256": (
        "ba37e047d27d9793961abe16268a1bb769e96a97457b7cf06e36879e50cf5d6e"
    ),
    "ranked_production_unsupported_ids_sha256": (
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    ),
}
# Historical capture compatibility is tested against this exact modeled scope.
# It is not a claim that those old captures were recorded on this generation,
# that the server currently opens these maps, or that we possess official Drop.
CURRENT_MODEL_GENERATION = "76b7ca-2026-10-08-723"
CURRENT_MODEL_MAP_IDS = frozenset({
    *range(2101, 2108), *range(2201, 2206), *range(2301, 2311),
    *range(2401, 2411), *range(2501, 2511), *range(2512, 2531), 2601,
    *range(4401, 4411), *range(4501, 4511), *range(4512, 4531),
    *range(5601, 5612),
})
CURRENT_MODEL_MAP_IDS_SHA256 = "78bab49717c2a3884ea49e8452cc779544ce4ee8bad5dce49e513e5bbfb4c99d"
CURRENT_MODEL_CENSUS = {
    "policy_roots": 112,
    **{layer: {"complete": 112, "degraded": 0, "sampleable": 112,
               "missing": 0, "empty": 0, "unsupported": 0,
               "unsupported_ids_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}
       for layer in ("direct_root", "source_map")},
}
CURRENT_MODEL_ROUTE_SCOPE = {**EXPECTED_ROUTE_SCOPE,
                           "policy_root_ids_sha256": CURRENT_MODEL_MAP_IDS_SHA256}


def _uses_current_model(tables: Any) -> bool:
    receipt = getattr(tables, "rankmap_distribution_receipt", {})
    if not isinstance(receipt, dict) or receipt.get("package_generation") != CURRENT_MODEL_GENERATION:
        if isinstance(receipt, dict) and receipt and receipt.get("package_generation") != "b049c3-2026-09-10-597":
            raise AssertionError("current_model_generation_unknown")
        return False
    if (getattr(tables, "rankmap_distribution_status", None) != "ok"
            or receipt.get("status") != "current_rankmap_algorithm_prior"
            or receipt.get("authority_mode") != "rankmap_drop_model_algorithm_prior"
            or type(receipt.get("scope_map_count")) is not int or receipt["scope_map_count"] != 112
            or receipt.get("scope") != "physical_map_conditioned_ordinary_and_ranked"
            or receipt.get("model_distribution_authority") is not True
            or receipt.get("official_distribution_authority") is not False
            or receipt.get("runtime_open_claim") is not False
            or receipt.get("drop_or_dropmap_textasset") is not False
            or receipt.get("claims_item_level_drop_graph") is not False
            or receipt.get("drop_or_dropmap_absence_is_not_a_blocker") is not True
            or "ranked_5606" in receipt or "chenmei_5506" in receipt
            or getattr(tables.drops, "package_generation", None) != CURRENT_MODEL_GENERATION
            or getattr(tables.drops, "authority_mode", None) != "rankmap_drop_model_algorithm_prior"
            or getattr(tables.drops, "bound_map_ids", frozenset()) != CURRENT_MODEL_MAP_IDS):
        raise AssertionError("current_model_authority_contract_failed")
    return True


_CAPTURE_CONTRACTS = (
    {
        "map_id": 2536,
        "model_map_id": 2506,
        "bytes": 30_381,
        "sha256": "fb9154cf98c1cb995d3ccb23ddeaa5bb703aafe6185ea05f111d197a2f17d82f",
        "prefix_rows": 9,
        "round": 2,
        "phase": "bidding",
        "unresolved_probability_ppm": 73,
    },
    {
        "map_id": 2408,
        "model_map_id": 2408,
        "bytes": 12_457,
        "sha256": "417c5e64f1fcb7e86e4d2792b2ac7319ca092a5376cccd186d85ea84e46964a5",
        "prefix_rows": 3,
        "round": 1,
        "phase": "bidding",
        "unresolved_probability_ppm": 50,
    },
    {
        "map_id": 2409,
        "model_map_id": 2409,
        "bytes": 31_637,
        "sha256": "27402d6deb00a831de46254cd7680de789418e38329cef846eb43763d8397ea6",
        "prefix_rows": 1,
        "round": 1,
        "phase": "reading",
        "unresolved_probability_ppm": 50,
    },
    {
        "map_id": 4501,
        "model_map_id": 4501,
        "bytes": 36_360,
        "sha256": "0b7dcb7f5ad3d40c7a3b7000ee725d514a3a26a407e599dc99cd1c81389d3df0",
        "prefix_rows": 1,
        "round": 1,
        "phase": "reading",
        "unresolved_probability_ppm": 103,
    },
    {
        "map_id": 4538,
        "model_map_id": 4508,
        "bytes": 30_187,
        "sha256": "d2073b9039ad611e3d527a9e774f8cafaf211a784f267903e9f5b09baa0c5262",
        "prefix_rows": 1,
        "round": 1,
        "phase": "reading",
        "unresolved_probability_ppm": 73,
    },
)


_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_CAPTURE_IDENTITY_FIELDS = frozenset(
    {
        "bytes",
        "sha256",
        "volume_serial_number",
        "file_index",
        "number_of_links",
        "verdict",
    }
)


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


def _windows_kernel32() -> Any:
    if os.name != "nt":
        raise OSError("capture_stable_identity_windows_only")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _controlled_capture_path(path: Path) -> Path:
    raw = str(path)
    if (
        not raw
        or raw != raw.strip()
        or any(ord(character) < 0x20 for character in raw)
        or raw.startswith(("\\\\?\\", "\\\\.\\"))
        or any(part in {"", ".", ".."} for part in Path(raw).parts[1:])
        or not os.path.isabs(raw)
    ):
        raise ValueError("capture_path_invalid")
    full = Path(os.path.abspath(raw))
    if str(full) != os.path.normpath(raw):
        raise ValueError("capture_path_invalid")
    return full


def _validate_capture_ancestor_chain(path: Path) -> None:
    ancestors: list[Path] = []
    current = path.parent
    while True:
        ancestors.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for ancestor in reversed(ancestors):
        info = ancestor.stat(follow_symlinks=False)
        attributes = int(getattr(info, "st_file_attributes", 0))
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("capture_path_reparse")


def _open_capture_handle(path: Path) -> int:
    kernel32 = _windows_kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_SEQUENTIAL_SCAN,
        None,
    )
    if handle in (None, _INVALID_HANDLE_VALUE):
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise FileNotFoundError(error, "capture_stable_open_failed")
        raise OSError(error, "capture_stable_open_failed")
    return int(handle)


def _close_capture_handle(handle: int) -> None:
    kernel32 = _windows_kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(wintypes.HANDLE(handle))


def _capture_handle_information(handle: int) -> dict[str, int]:
    kernel32 = _windows_kernel32()
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    information = _ByHandleFileInformation()
    if not get_information(wintypes.HANDLE(handle), ctypes.byref(information)):
        raise OSError(ctypes.get_last_error(), "capture_identity_unavailable")
    return {
        "file_attributes": int(information.file_attributes),
        "volume_serial_number": int(information.volume_serial_number),
        "file_index": (int(information.file_index_high) << 32)
        | int(information.file_index_low),
        "bytes": (int(information.file_size_high) << 32)
        | int(information.file_size_low),
        "number_of_links": int(information.number_of_links),
    }


def _capture_final_path(handle: int) -> Path:
    kernel32 = _windows_kernel32()
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_final_path.restype = wintypes.DWORD
    capacity = 32768
    buffer = ctypes.create_unicode_buffer(capacity)
    length = get_final_path(wintypes.HANDLE(handle), buffer, capacity, 0)
    if length == 0 or length >= capacity:
        raise OSError(ctypes.get_last_error(), "capture_final_path_unavailable")
    raw = buffer.value
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return Path(os.path.abspath(raw))


def _same_capture_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _validate_capture_information(
    information: dict[str, int],
    *,
    contract: dict[str, Any],
) -> None:
    if (
        information["file_attributes"]
        & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT)
        or information["file_index"] == 0
        or information["number_of_links"] != 1
        or information["bytes"] != contract["bytes"]
    ):
        raise ValueError("capture_identity_mismatch")


def _capture_identity_receipt(
    information: dict[str, int],
    contract: dict[str, Any],
) -> dict[str, Any]:
    return {
        "bytes": contract["bytes"],
        "sha256": contract["sha256"],
        "volume_serial_number": information["volume_serial_number"],
        "file_index": information["file_index"],
        "number_of_links": information["number_of_links"],
        "verdict": "pass",
    }


def _validate_expected_capture_identity(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    if (
        expected.__class__ is not dict
        or set(expected) != _CAPTURE_IDENTITY_FIELDS
        or expected.get("verdict") != "pass"
        or any(expected.get(key).__class__ is not int for key in (
            "bytes",
            "volume_serial_number",
            "file_index",
            "number_of_links",
        ))
        or not isinstance(expected.get("sha256"), str)
        or actual != expected
    ):
        raise ValueError("capture_stable_identity_mismatch")


class _StableCaptureLease:
    def __init__(
        self,
        *,
        path: Path,
        stream: Any,
        information: dict[str, int],
        contract: dict[str, Any],
        raw: bytes,
    ) -> None:
        self.path = path
        self.stream = stream
        self.information = information
        self.contract = contract
        self.raw = raw

    @classmethod
    def open(
        cls,
        path: Path,
        contract: dict[str, Any],
        expected_identity: dict[str, Any] | None = None,
    ) -> _StableCaptureLease:
        import msvcrt

        controlled = _controlled_capture_path(path)
        _validate_capture_ancestor_chain(controlled)
        raw_handle = _open_capture_handle(controlled)
        descriptor: int | None = None
        stream: Any = None
        try:
            information = _capture_handle_information(raw_handle)
            _validate_capture_information(information, contract=contract)
            if not _same_capture_path(_capture_final_path(raw_handle), controlled):
                raise ValueError("capture_path_reparse")
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOINHERIT", 0)
            )
            descriptor = msvcrt.open_osfhandle(raw_handle, flags)
            raw_handle = 0
            stream = os.fdopen(descriptor, "rb", buffering=0)
            descriptor = None
            raw = stream.read()
            if (
                len(raw) != contract["bytes"]
                or hashlib.sha256(raw).hexdigest() != contract["sha256"]
            ):
                raise ValueError("capture_identity_mismatch")
            lease = cls(
                path=controlled,
                stream=stream,
                information=information,
                contract=contract,
                raw=raw,
            )
            lease.assert_unchanged()
            receipt = lease.identity_receipt()
            if expected_identity is not None:
                _validate_expected_capture_identity(receipt, expected_identity)
            return lease
        except BaseException:
            if stream is not None:
                stream.close()
            elif descriptor is not None:
                os.close(descriptor)
            elif raw_handle:
                _close_capture_handle(raw_handle)
            raise

    def identity_receipt(self) -> dict[str, Any]:
        return _capture_identity_receipt(self.information, self.contract)

    def assert_unchanged(self) -> None:
        import msvcrt

        _validate_capture_ancestor_chain(self.path)
        handle = int(msvcrt.get_osfhandle(self.stream.fileno()))
        current = _capture_handle_information(handle)
        _validate_capture_information(current, contract=self.contract)
        if current != self.information:
            raise ValueError("capture_stable_identity_mismatch")
        if not _same_capture_path(_capture_final_path(handle), self.path):
            raise ValueError("capture_path_reparse")
        self.stream.seek(0)
        raw = self.stream.read()
        if (
            len(raw) != self.contract["bytes"]
            or hashlib.sha256(raw).hexdigest() != self.contract["sha256"]
        ):
            raise ValueError("capture_identity_mismatch")
        rebound = _open_capture_handle(self.path)
        try:
            rebound_information = _capture_handle_information(rebound)
            if (
                rebound_information != self.information
                or not _same_capture_path(_capture_final_path(rebound), self.path)
            ):
                raise ValueError("capture_path_identity_changed")
        finally:
            _close_capture_handle(rebound)

    def close(self) -> None:
        self.stream.close()

    def __enter__(self) -> _StableCaptureLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _parse_capture_identity_receipt(raw: str) -> tuple[dict[str, Any], ...]:
    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("capture_identity_receipt_invalid")
            value[key] = item
        return value

    try:
        value = json.loads(raw, object_pairs_hook=strict_object)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("capture_identity_receipt_invalid") from exc
    if (
        value.__class__ is not dict
        or set(value) != {"schema", "receipt_version", "status", "captures"}
        or value.get("schema") != CAPTURE_IDENTITY_RECEIPT_SCHEMA
        or value.get("receipt_version") != 1
        or value.get("status") != "pass"
        or value.get("captures").__class__ is not list
        or len(value["captures"]) != len(_CAPTURE_CONTRACTS)
    ):
        raise ValueError("capture_identity_receipt_invalid")
    captures = tuple(value["captures"])
    for actual, contract in zip(captures, _CAPTURE_CONTRACTS, strict=True):
        if (
            actual.__class__ is not dict
            or set(actual) != _CAPTURE_IDENTITY_FIELDS
            or actual.get("bytes") != contract["bytes"]
            or actual.get("sha256") != contract["sha256"]
            or actual.get("number_of_links") != 1
            or actual.get("verdict") != "pass"
            or actual.get("volume_serial_number").__class__ is not int
            or actual.get("file_index").__class__ is not int
            or actual["file_index"] <= 0
        ):
            raise ValueError("capture_identity_receipt_invalid")
    return captures


def _ids_sha256(values: set[int] | frozenset[int]) -> str:
    payload = json.dumps(sorted(values), separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _canonical_2409_snapshot() -> dict[str, Any]:
    return {
        "hero": "aisha",
        "map_id": 2409,
        "round": 3,
        "phase": "bidding",
        "structured_ref_inputs": {
            "source": "release-availability-current-authority",
            "hero": "aisha",
            "counts": {"q1": 17, "q3": 12},
            "quality_cells": {"q1": 31, "q3": 34},
            "avg_cells": {
                "q1": 31 / 17,
                "q3": 34 / 12,
                "q4": 39 / 12,
                "q5": 23 / 6,
            },
            "total_count": 51,
        },
        "skill_reveal_rows": [
            {
                "sort": 10,
                "hero_id": 103,
                "skill_id": 1001032,
                "revealed_items": 12,
                "revealed_items_detail": [],
            }
        ],
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2409,
                "round": 3,
                "phase": "bidding",
            },
            "constraints": {"public_info": {}},
            "actions": {"results": []},
        },
    }


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _package_paths(package_root: Path) -> tuple[Path, Path, Path, Path]:
    root = package_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("package_root_invalid")
    paths = (
        root / "src",
        root / "hero_ref" / "src",
        root / "hero_ref" / "tools",
        root / "scripts",
    )
    if any(not path.is_dir() for path in paths):
        raise ValueError("package_root_incomplete")
    return paths


def _activate_package(package_root: Path) -> Path:
    root = package_root.resolve(strict=True)
    paths = _package_paths(root)
    for path in reversed(paths):
        sys.path.insert(0, str(path))
    return root


def _assert_module_within_package(module: ModuleType, package_root: Path) -> Path:
    raw = getattr(module, "__file__", None)
    if not isinstance(raw, str) or not raw:
        raise RuntimeError("package_module_origin_missing")
    origin = Path(raw).resolve(strict=True)
    try:
        origin.relative_to(package_root)
    except ValueError as exc:
        raise RuntimeError("package_module_origin_outside") from exc
    if not origin.is_file():
        raise RuntimeError("package_module_origin_invalid")
    return origin


def _assert_native_product_module(module: ModuleType, package_root: Path) -> Path:
    origin = _assert_module_within_package(module, package_root)
    if origin.suffix.casefold() != ".pyd":
        raise RuntimeError("package_product_module_not_native")
    return origin


def _load_exact_script(name: str, path: Path, package_root: Path) -> ModuleType:
    script = path.resolve(strict=True)
    try:
        script.relative_to(package_root)
    except ValueError as exc:
        raise RuntimeError("package_script_origin_outside") from exc
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError("package_script_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _assert_module_within_package(module, package_root)
    return module


def _parse_capture_raw(raw: bytes, contract: dict[str, Any]) -> list[Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if parsed.__class__ is not list or len(parsed) < contract["prefix_rows"]:
        raise ValueError("capture_shape_mismatch")
    return parsed


def _read_exact_capture(path: Path, contract: dict[str, Any]) -> list[Any]:
    with _StableCaptureLease.open(path, contract) as lease:
        return _parse_capture_raw(lease.raw, contract)


def _positive_quote(summary: dict[str, Any]) -> bool:
    ref = summary.get("ahmed_ref")
    return bool(
        isinstance(ref, dict)
        and ref.get("status") not in {"", "no_quote", "unavailable"}
        and all(
            isinstance(ref.get(key), int) and ref[key] > 0
            for key in ("conservative", "balanced", "aggressive")
        )
    )


def _summary_api_ui_contract(
    summary: dict[str, Any],
    *,
    require_degraded_flag: bool = True,
) -> None:
    if summary.get("status") != "ok" or not _positive_quote(summary):
        raise AssertionError("summary_quote_contract_failed")
    ref = summary["ahmed_ref"]
    quotes = tuple(
        ref[key] for key in ("conservative", "balanced", "aggressive")
    )
    if not quotes[0] < quotes[1] < quotes[2]:
        raise AssertionError("summary_quote_order_contract_failed")
    api_payload = json.loads(
        json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    )
    if not _positive_quote(api_payload):
        raise AssertionError("api_quote_contract_failed")
    flags = api_payload.get("flags")
    labels = {
        row.get("label")
        for row in flags
        if isinstance(flags, list) and isinstance(row, dict)
    }
    if require_degraded_flag and "掉落资料降级" not in labels:
        raise AssertionError("ui_degraded_flag_missing")


def _close_monitor_capture(monitor: object | None) -> None:
    if monitor is None:
        return
    worker = getattr(monitor, "_worker", None)
    if worker is not None and callable(getattr(worker, "is_alive", None)):
        if worker.is_alive():
            stop = getattr(monitor, "stop", None)
            if not callable(stop):
                raise RuntimeError("monitor_capture_cleanup_failed")
            stop()
            if worker.is_alive():
                raise RuntimeError("monitor_capture_cleanup_failed")
            writer = getattr(monitor, "_fact_receipts", None)
            writer_thread = getattr(
                writer,
                "_worker",
                getattr(writer, "_thread", None),
            )
            if (
                writer_thread is not None
                and callable(getattr(writer_thread, "is_alive", None))
                and writer_thread.is_alive()
            ):
                raise RuntimeError("monitor_capture_cleanup_failed")
            return
    writer = getattr(monitor, "_fact_receipts", None)
    if writer is None:
        return
    close = getattr(writer, "close", None)
    if not callable(close):
        raise RuntimeError("monitor_capture_cleanup_failed")
    close()
    writer_thread = getattr(writer, "_worker", getattr(writer, "_thread", None))
    if (
        writer_thread is not None
        and callable(getattr(writer_thread, "is_alive", None))
        and writer_thread.is_alive()
    ):
        raise RuntimeError("monitor_capture_cleanup_failed")


def _check_monitor_capture(
    *,
    rows: list[Any],
    contract: dict[str, Any],
    tables: Any,
    monitor_script: ModuleType,
    panel: ModuleType,
) -> None:
    current_model = _uses_current_model(tables)
    prefix = rows[: contract["prefix_rows"]]
    if any(not isinstance(row, dict) for row in prefix):
        raise ValueError("capture_row_shape_mismatch")
    published: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="bidking-release-availability-") as temp:
        root = Path(temp)
        config = monitor_script.WebhookMonitorConfig(
            log_dir=root / "logs",
            raw_dir=root / "raw",
            process_name="BidKing.exe",
            server_ports=(10_000,),
            n_trials=80,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        )
        monitor: object | None = None
        original_writer = monitor_script.write_monitor_logs
        primary_error: BaseException | None = None
        primary_traceback = None
        cleanup_error: BaseException | None = None
        try:
            monitor = monitor_script.FatbeansWebhookMonitor(config=config, tables=tables)
            monitor._persist_raw_rows = lambda *_args, **_kwargs: None
            monitor._archive_round_snapshot = lambda *_args, **_kwargs: None
            monitor_script.write_monitor_logs = (
                lambda artifact, **_kwargs: published.append(dict(artifact))
            )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                receipts = [
                    monitor.accept_row(row, schedule_process=True) for row in prefix
                ]
                monitor._process_snapshot(force=True)
            if not receipts or not all(
                receipt.get("accepted") is True for receipt in receipts
            ):
                raise AssertionError("capture_acceptance_failed")
            if monitor._last_requested_count != len(prefix) or not published:
                raise AssertionError("capture_scheduling_failed")
            snapshot = published[-1]
            support = snapshot.get("drop_pool_support")
            if not (
                snapshot.get("map_id") == contract["map_id"]
                and snapshot.get("model_map_id") == contract["model_map_id"]
                and snapshot.get("phase") == contract["phase"]
                and snapshot.get("round") == contract["round"]
                and (
                    (current_model and contract["model_map_id"] in CURRENT_MODEL_MAP_IDS
                     and support in (None, {}))
                    or (not current_model and isinstance(support, dict)
                        and support.get("status") == "legacy_incomplete_conditional_known_support"
                        and support.get("quote_quality") == "degraded"
                        and support.get("unresolved_probability_ppm") == contract["unresolved_probability_ppm"])
                )
            ):
                raise AssertionError("snapshot_contract_failed")
            snapshot["created_at"] = time.time()
            summary = panel.summarize_snapshot(
                snapshot,
                snapshot_path=root / "latest_snapshot.json",
            )
            _summary_api_ui_contract(summary, require_degraded_flag=not current_model)
            if current_model and (summary.get("drop_pool_support") not in (None, {})
                                  or summary.get("diagnostics", {}).get("drop_pool_support") not in (None, {})):
                raise AssertionError("current_model_consumer_support_mismatch")
        except BaseException as exc:
            primary_error = exc
            primary_traceback = exc.__traceback__
        finally:
            monitor_script.write_monitor_logs = original_writer
            try:
                _close_monitor_capture(monitor)
            except BaseException as exc:
                cleanup_error = exc
        if primary_error is not None:
            raise primary_error.with_traceback(primary_traceback)
        if cleanup_error is not None:
            raise RuntimeError("monitor_capture_cleanup_failed") from None


def _route_sets(engine: ModuleType) -> tuple[set[int], set[int], set[int]]:
    changed = {2511, 4511, 5201, 5202, 5505, 5506, *range(5601, 5611)}
    new_only = {*range(2531, 2551), *range(4531, 4551), 5611, 6001}
    legacy_only = set(range(5501, 5511)) - {5505, 5506}
    current_activity_alias = {*range(2531, 2541), *range(4531, 4541)}
    ranked_unsupported: set[int] = set()
    forbidden = changed | new_only | legacy_only
    if (
        len(changed) != EXPECTED_ROUTE_SCOPE["changed_route_count"]
        or len(new_only) != EXPECTED_ROUTE_SCOPE["new_only_count"]
        or len(legacy_only) != EXPECTED_ROUTE_SCOPE["legacy_only_count"]
        or len(forbidden) != EXPECTED_ROUTE_SCOPE["forbidden_count"]
        or _ids_sha256(changed)
        != EXPECTED_ROUTE_SCOPE["changed_route_ids_sha256"]
        or _ids_sha256(new_only)
        != EXPECTED_ROUTE_SCOPE["new_only_ids_sha256"]
        or _ids_sha256(legacy_only)
        != EXPECTED_ROUTE_SCOPE["legacy_only_ids_sha256"]
        or _ids_sha256(forbidden)
        != EXPECTED_ROUTE_SCOPE["forbidden_ids_sha256"]
        or len(current_activity_alias)
        != EXPECTED_ROUTE_SCOPE["current_activity_alias_sampleable_count"]
        or _ids_sha256(current_activity_alias)
        != EXPECTED_ROUTE_SCOPE["current_activity_alias_ids_sha256"]
        or len(ranked_unsupported)
        != EXPECTED_ROUTE_SCOPE["ranked_production_unsupported_count"]
        or _ids_sha256(ranked_unsupported)
        != EXPECTED_ROUTE_SCOPE["ranked_production_unsupported_ids_sha256"]
        or set(engine.UPDATE606_CHANGED_ROUTE_MAP_IDS) != changed
        or set(engine._AISHA_Q5_TOTAL_CELLS_FORBIDDEN_WEIGHT_MAP_IDS) != forbidden
    ):
        raise AssertionError("route_scope_contract_failed")
    return changed, new_only, legacy_only


def _policy_census(
    tables: Any,
    monitor: ModuleType,
    engine: ModuleType,
    drop_engine: ModuleType,
) -> dict[str, Any]:
    if _uses_current_model(tables):
        _route_sets(engine)  # The established exclusions/changed routes remain exact.
        if _ids_sha256(CURRENT_MODEL_MAP_IDS) != CURRENT_MODEL_MAP_IDS_SHA256:
            raise AssertionError("current_model_scope_identity_failed")
        layers = {}
        for layer in ("direct_root", "source_map"):
            for map_id in sorted(CURRENT_MODEL_MAP_IDS):
                model_id = map_id
                if layer == "source_map":
                    alias = monitor._activity_shipwreck_alias(map_id, tables.maps, tables.drops)
                    if alias:
                        model_id = alias["model_map_id"]
                if model_id not in CURRENT_MODEL_MAP_IDS:
                    raise AssertionError("current_model_route_not_sampleable")
                bid_map = tables.maps.get(model_id)
                if bid_map is None:
                    raise AssertionError("current_model_route_not_sampleable")
                flattened = drop_engine.flatten_pool(bid_map.drop_pool_id, tables.drops, tables.items, map_id=model_id)
                if (not flattened.item_ids or flattened.resolution_status != "complete"
                        or flattened.resolution_generation != CURRENT_MODEL_GENERATION
                        or flattened.resolution_policy != "rankmap_drop_model_algorithm_prior"
                        or monitor._drop_pool_support_receipt(model_id, tables) != {}):
                    raise AssertionError("current_model_route_not_sampleable")
            layers[layer] = dict(CURRENT_MODEL_CENSUS[layer])
        # Preserve separate legacy-route compatibility: these 20 excluded old
        # activity ids remain explicit aliases, not current physical model roots.
        for source in (*range(2531, 2541), *range(4531, 4541)):
            alias = monitor._activity_shipwreck_alias(source, tables.maps, tables.drops)
            if (not alias or alias.get("source_map_id") != source
                    or alias.get("model_map_id") not in CURRENT_MODEL_MAP_IDS
                    or monitor._drop_pool_support_receipt(alias["model_map_id"], tables) != {}):
                raise AssertionError("historical_activity_alias_not_sampleable")
        return {"policy_roots": 112, **layers}
    policy = getattr(tables.drops, "resolution_policy", None)
    roots = set(getattr(policy, "allowed_root_pool_ids", ()))
    changed, new_only, legacy_only = _route_sets(engine)
    if (
        len(roots) != EXPECTED_POLICY_CENSUS["policy_roots"]
        or _ids_sha256(roots) != EXPECTED_ROUTE_SCOPE["policy_root_ids_sha256"]
        or roots.intersection(changed | new_only | legacy_only)
    ):
        raise AssertionError("policy_root_contract_failed")
    def classify(*, source_alias: bool) -> dict[str, Any]:
        counts = {"complete": 0, "degraded": 0, "missing": 0, "empty": 0}
        unsupported_ids: list[int] = []
        for source_map_id in sorted(roots):
            model_map_id = source_map_id
            if source_alias:
                alias = monitor._activity_shipwreck_alias(
                    source_map_id,
                    tables.maps,
                    tables.drops,
                )
                if alias:
                    model_map_id = int(alias["model_map_id"])
            try:
                support = monitor._drop_pool_support_receipt(model_map_id, tables)
            except drop_engine.DropPoolResolutionError as exc:
                if exc.reason not in {"missing_pool", "empty_pool"}:
                    raise
                counts["missing" if exc.reason == "missing_pool" else "empty"] += 1
                unsupported_ids.append(source_map_id)
            else:
                counts["degraded" if support else "complete"] += 1
        return {
            **counts,
            "sampleable": counts["complete"] + counts["degraded"],
            "unsupported": counts["missing"] + counts["empty"],
            "unsupported_ids_sha256": _ids_sha256(frozenset(unsupported_ids)),
        }

    receipt = {
        "policy_roots": len(roots),
        "direct_root": classify(source_alias=False),
        "source_map": classify(source_alias=True),
    }
    if receipt != EXPECTED_POLICY_CENSUS:
        raise AssertionError("policy_census_contract_failed")
    return receipt


def _check_current_authority_2409(
    *, tables: Any, monitor: ModuleType, panel: ModuleType
) -> None:
    current_model = _uses_current_model(tables)
    support = monitor._drop_pool_support_receipt(2409, tables)
    if not (
        (current_model and support == {})
        or (not current_model and isinstance(support, dict)
            and support.get("status") == "legacy_incomplete_conditional_known_support"
            and support.get("quote_quality") == "degraded")
    ):
        raise AssertionError("current_authority_2409_unavailable")
    snapshot = _canonical_2409_snapshot()
    if _canonical_sha256(snapshot) != CURRENT_2409_INPUT_SHA256:
        raise AssertionError("current_authority_2409_input_mismatch")
    snapshot["drop_pool_support"] = support
    if current_model:
        snapshot["rankmap_distribution_support"] = {
            **tables.rankmap_distribution_receipt, "active_map_id": 2409,
            "map_rebound": False, "pool_rebound": False,
        }
    snapshot["created_at"] = time.time()
    with tempfile.TemporaryDirectory(prefix="bidking-release-2409-") as temp:
        summary = panel.summarize_snapshot(
            snapshot,
            snapshot_path=Path(temp) / "latest_snapshot.json",
        )
    _summary_api_ui_contract(summary, require_degraded_flag=not current_model)
    if current_model and (
        summary.get("rankmap_distribution_support") != snapshot["rankmap_distribution_support"]
        or summary.get("diagnostics", {}).get("rankmap_distribution_support") != snapshot["rankmap_distribution_support"]
    ):
        raise AssertionError("current_model_consumer_receipt_lost")


def _canonical_current_ranked_snapshot(map_id: int) -> dict[str, Any]:
    snapshot = _canonical_2409_snapshot()
    snapshot["map_id"] = map_id
    snapshot["model_map_id"] = map_id
    snapshot["map_alias"] = {}
    snapshot["map_alias_mode"] = ""
    snapshot["structured_ref_inputs"]["source"] = (
        "release-availability-current-rankmap-authority"
    )
    snapshot["ui_contract"]["context"]["map_id"] = map_id
    return snapshot


def _check_current_ranked_authority(
    *,
    tables: Any,
    monitor: ModuleType,
    panel: ModuleType,
    drop_engine: ModuleType,
) -> None:
    current_model = _uses_current_model(tables)
    ranked_map_ids = set(range(5601, 5612))
    current_scope = set(monitor.CURRENT_RANKMAP_MAP_IDS)
    receipt = tables.rankmap_distribution_receipt
    if (
        tables.rankmap_distribution_status != "ok"
        or not ranked_map_ids.issubset(current_scope)
        or 5506 in current_scope
        or not isinstance(receipt, dict)
        or receipt.get("status") != "current_rankmap_algorithm_prior"
        or receipt.get("authority_mode")
        != "rankmap_drop_model_algorithm_prior"
        or (not current_model and (receipt.get("scope_map_count") != 18
            or receipt.get("ranked_5606") != "modeled_empirically_calibrated"
            or receipt.get("runtime_open_claim") is not True
            or receipt.get("chenmei_5506") != "pending_isolated"))
        or receipt.get("drop_or_dropmap_textasset") is not False
        or receipt.get("claims_item_level_drop_graph") is not False
    ):
        raise AssertionError("current_ranked_authority_contract_failed")
    bound_pool_ids = getattr(tables.drops, "bound_pool_ids", frozenset())
    for map_id in sorted(ranked_map_ids):
        bid_map = tables.maps.get(map_id)
        if bid_map is None or bid_map.drop_pool_id not in bound_pool_ids:
            raise AssertionError("current_ranked_original_pool_unavailable")
        flattened = drop_engine.flatten_pool(
            bid_map.drop_pool_id,
            tables.drops,
            tables.items,
            map_id=map_id,
        )
        if (
            not flattened.item_ids
            or flattened.resolution_status != "complete"
            or flattened.resolution_policy
            != "rankmap_drop_model_algorithm_prior"
            or flattened.resolution_generation
            != receipt.get("package_generation")
            or monitor._drop_pool_support_receipt(map_id, tables) != {}
        ):
            raise AssertionError("current_ranked_original_pool_not_sampleable")
        support = {
            **receipt,
            "active_map_id": map_id,
            "map_rebound": False,
            "pool_rebound": False,
        }
        snapshot = _canonical_current_ranked_snapshot(map_id)
        if (
            snapshot["map_id"] != map_id
            or snapshot["model_map_id"] != map_id
            or snapshot["map_alias"] != {}
            or snapshot["map_alias_mode"] != ""
        ):
            raise AssertionError("current_ranked_map_identity_mismatch")
        snapshot["rankmap_distribution_support"] = support
        snapshot["created_at"] = time.time()
        with tempfile.TemporaryDirectory(
            prefix="bidking-release-current-ranked-"
        ) as temp:
            summary = panel.summarize_snapshot(
                snapshot,
                snapshot_path=Path(temp) / "latest_snapshot.json",
            )
        _summary_api_ui_contract(summary, require_degraded_flag=False)
        if (
            summary.get("rankmap_distribution_support") != support
            or summary.get("diagnostics", {}).get(
                "rankmap_distribution_support"
            )
            != support
        ):
            raise AssertionError("current_ranked_consumer_receipt_lost")


def _package_identity(package_root: Path, modules: tuple[ModuleType, ...]) -> str:
    files = {
        _assert_module_within_package(module, package_root) for module in modules
    }
    files.update(
        {
            package_root / "data" / "raw" / "tables" / name
            for name in ("BidMap.txt", "Item.txt", "Drop.txt", "RankMap.txt")
        }
    )
    files.update(
        {
            package_root
            / "data"
            / "processed"
            / name
            for name in (
                "drop_weights_all_maps_0627.json",
                "rankmap_drop_weights_current.json",
                "rankmap_drop_priors_current.json",
                "rankmap_distribution_authority_current.json",
                "auction_hall_6001_shadow_distribution.json",
            )
        }
    )
    files.add(package_root / "scripts" / "package_data_fingerprints.json")
    rows: list[str] = []
    for path in sorted(files, key=lambda value: value.as_posix().casefold()):
        resolved = path.resolve(strict=True)
        try:
            relative = resolved.relative_to(package_root).as_posix()
        except ValueError as exc:
            raise RuntimeError("package_identity_path_outside") from exc
        raw = resolved.read_bytes()
        rows.append(f"{relative}|{len(raw)}|{hashlib.sha256(raw).hexdigest()}\n")
    return hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def _check_package_with_leases(
    *,
    package_root: Path,
    leases: tuple[_StableCaptureLease, ...],
    require_native: bool,
) -> dict[str, Any]:
    root = _activate_package(package_root)
    monitor = importlib.import_module("bidking_lab.live.monitor")
    drop_engine = importlib.import_module("bidking_lab.simulation.basic_mc")
    engine = importlib.import_module("ahmad_ref_engine")
    panel = importlib.import_module("ahmad_live_panel_server")
    monitor_script = _load_exact_script(
        "bidking_release_fatbeans_monitor",
        root / "scripts" / "run_fatbeans_webhook_monitor.py",
        root,
    )
    modules = (monitor, drop_engine, engine, panel, monitor_script)
    for module in modules:
        _assert_module_within_package(module, root)
    if require_native:
        for module in (monitor, drop_engine, engine, panel):
            _assert_native_product_module(module, root)
    tables = dataclasses.replace(
        monitor.load_monitor_tables(
            tables_dir=root / "data" / "raw" / "tables"
        ),
        warehouse_live_identity_check=False,
    )
    current_model = _uses_current_model(tables)
    routes = CURRENT_MODEL_ROUTE_SCOPE if current_model else EXPECTED_ROUTE_SCOPE
    captures: list[dict[str, Any]] = []
    for lease, contract in zip(leases, _CAPTURE_CONTRACTS, strict=True):
        rows = _parse_capture_raw(lease.raw, contract)
        _check_monitor_capture(
            rows=rows,
            contract=contract,
            tables=tables,
            monitor_script=monitor_script,
            panel=panel,
        )
        captures.append(
            {
                "bytes": contract["bytes"],
                "sha256": contract["sha256"],
                "verdict": "pass",
            }
        )
    census = _policy_census(tables, monitor, engine, drop_engine)
    _check_current_authority_2409(tables=tables, monitor=monitor, panel=panel)
    _check_current_ranked_authority(
        tables=tables,
        monitor=monitor,
        panel=panel,
        drop_engine=drop_engine,
    )
    for lease in leases:
        lease.assert_unchanged()
    return {
        "schema": RECEIPT_SCHEMA,
        "receipt_version": 1,
        "status": "pass",
        "captures": captures,
        "package": {
            "identity_sha256": _package_identity(root, modules),
            "verdict": "pass",
        },
        "policy_census": {**census, "verdict": "pass"},
        "route_scope": {
            "changed_route_count": routes["changed_route_count"],
            "new_only_count": routes["new_only_count"],
            "legacy_only_count": routes["legacy_only_count"],
            "forbidden_count": routes["forbidden_count"],
            "current_activity_alias_sampleable_count": routes[
                "current_activity_alias_sampleable_count"
            ],
            "ranked_production_unsupported_count": routes[
                "ranked_production_unsupported_count"
            ],
            "verdict": "pass",
        },
        "chains": {
            "accepted_scheduled_snapshot": "pass",
            "summary_api_ui": "pass",
            "current_authority_2409": "pass",
        },
    }


def check_package(
    *,
    package_root: Path,
    capture_paths: tuple[Path, Path, Path, Path, Path],
    require_native: bool = True,
    capture_identity_receipt: tuple[dict[str, Any], ...] | None = None,
) -> dict[str, Any]:
    if capture_identity_receipt is not None and (
        type(capture_identity_receipt) is not tuple
        or len(capture_identity_receipt) != len(_CAPTURE_CONTRACTS)
    ):
        raise ValueError("capture_identity_receipt_invalid")
    with ExitStack() as stack:
        leases = tuple(
            stack.enter_context(
                _StableCaptureLease.open(
                    path,
                    contract,
                    None
                    if capture_identity_receipt is None
                    else capture_identity_receipt[index],
                )
            )
            for index, (path, contract) in enumerate(
                zip(capture_paths, _CAPTURE_CONTRACTS, strict=True)
            )
        )
        return _check_package_with_leases(
            package_root=package_root,
            leases=leases,
            require_native=require_native,
        )


def _capture_paths_from_args(args: argparse.Namespace) -> tuple[Path, ...]:
    return (
        args.capture_2536,
        args.capture_2408,
        args.capture_2409,
        args.capture_4501,
        args.capture_4538,
    )


def _release_capture_holder_requested(stream: Any) -> bool:
    return stream.read(16) in _RELEASE_CAPTURE_HOLDER_CONTROL_FRAMES


def _hold_capture_leases(capture_paths: tuple[Path, ...]) -> int:
    with ExitStack() as stack:
        leases = tuple(
            stack.enter_context(_StableCaptureLease.open(path, contract))
            for path, contract in zip(
                capture_paths, _CAPTURE_CONTRACTS, strict=True
            )
        )
        receipt = {
            "schema": CAPTURE_IDENTITY_RECEIPT_SCHEMA,
            "receipt_version": 1,
            "status": "pass",
            "captures": [lease.identity_receipt() for lease in leases],
        }
        print(json.dumps(receipt, separators=(",", ":"), sort_keys=True), flush=True)
        if not _release_capture_holder_requested(sys.stdin.buffer):
            return 1
        for lease in leases:
            lease.assert_unchanged()
        print(
            json.dumps(
                {
                    "schema": CAPTURE_IDENTITY_RECEIPT_SCHEMA,
                    "status": "released",
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--hold-captures", action="store_true")
    parser.add_argument("--capture-2536", required=True, type=Path)
    parser.add_argument("--capture-2408", required=True, type=Path)
    parser.add_argument("--capture-2409", required=True, type=Path)
    parser.add_argument("--capture-4501", required=True, type=Path)
    parser.add_argument("--capture-4538", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        capture_paths = _capture_paths_from_args(args)
        if args.hold_captures:
            if args.package_root is not None:
                raise ValueError("capture_holder_package_root_forbidden")
            return _hold_capture_leases(capture_paths)
        if args.package_root is None:
            raise ValueError("package_root_missing")
        identity_raw = os.environ.get(CAPTURE_IDENTITY_ENV)
        if identity_raw is None:
            raise ValueError("capture_identity_receipt_missing")
        identity_receipt = _parse_capture_identity_receipt(identity_raw)
        receipt = check_package(
            package_root=args.package_root,
            capture_paths=capture_paths,
            capture_identity_receipt=identity_receipt,
        )
    except Exception as exc:  # noqa: BLE001 - formal checker emits type only
        print(
            json.dumps(
                {"schema": RECEIPT_SCHEMA, "status": "fail", "error": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
