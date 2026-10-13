"""Atomically publish and identity-bind a support-safe build failure receipt."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Callable


SCHEMA = "bidking_release_availability_failure_publication_v1"
CHECKER_SCHEMA = "bidking_release_availability_receipt_v1"
SAFE_ERROR_TYPES = frozenset(
    {
        "AssertionError",
        "AttributeError",
        "FileNotFoundError",
        "ImportError",
        "KeyError",
        "ModuleNotFoundError",
        "OSError",
        "PermissionError",
        "RuntimeError",
        "TypeError",
        "ValueError",
    }
)
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_CREATE_NEW = 1
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_MOVEFILE_WRITE_THROUGH = 0x00000008
_FILE_BEGIN = 0
_MAX_BYTES = 256


class PublicationError(RuntimeError):
    pass


class _FileTime(ctypes.Structure):
    _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = (
        ("attributes", wintypes.DWORD),
        ("creation_time", _FileTime),
        ("last_access_time", _FileTime),
        ("last_write_time", _FileTime),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    )


class _WindowsFunctions:
    def __init__(self) -> None:
        if os.name != "nt":
            raise PublicationError("windows_required")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.create_file = kernel32.CreateFileW
        self.create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        self.create_file.restype = wintypes.HANDLE
        self.get_information = kernel32.GetFileInformationByHandle
        self.get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        )
        self.get_information.restype = wintypes.BOOL
        self.set_pointer = kernel32.SetFilePointerEx
        self.set_pointer.argtypes = (
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        )
        self.set_pointer.restype = wintypes.BOOL
        self.read_file = kernel32.ReadFile
        self.read_file.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        self.read_file.restype = wintypes.BOOL
        self.write_file = kernel32.WriteFile
        self.write_file.argtypes = (
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        self.write_file.restype = wintypes.BOOL
        self.flush_file = kernel32.FlushFileBuffers
        self.flush_file.argtypes = (wintypes.HANDLE,)
        self.flush_file.restype = wintypes.BOOL
        self.move_file = kernel32.MoveFileExW
        self.move_file.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        self.move_file.restype = wintypes.BOOL
        self.close_handle = kernel32.CloseHandle
        self.close_handle.argtypes = (wintypes.HANDLE,)
        self.close_handle.restype = wintypes.BOOL


def _canonical_failure(error_type: str) -> bytes:
    if type(error_type) is not str or error_type not in SAFE_ERROR_TYPES:  # noqa: E721
        raise PublicationError("invalid_error_type")
    return (
        json.dumps(
            {"schema": CHECKER_SCHEMA, "status": "fail", "error": error_type},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _file_time(value: _FileTime) -> int:
    return (int(value.high) << 32) | int(value.low)


def _open(
    api: _WindowsFunctions,
    path: Path,
    *,
    access: int,
    share: int,
    disposition: int,
    flags: int,
) -> int:
    handle = api.create_file(
        str(path),
        access,
        share,
        None,
        disposition,
        flags,
        None,
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        raise PublicationError("open_failed")
    return int(handle)


def _close(api: _WindowsFunctions, handle: int | None) -> None:
    if handle is not None:
        api.close_handle(handle)


def _write(api: _WindowsFunctions, handle: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        chunk = raw[offset:]
        written = wintypes.DWORD(0)
        buffer = ctypes.create_string_buffer(chunk)
        if (
            not api.write_file(
                handle,
                buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            )
            or written.value <= 0
        ):
            raise PublicationError("write_failed")
        offset += written.value
    if not api.flush_file(handle):
        raise PublicationError("flush_failed")


def _read(api: _WindowsFunctions, handle: int) -> bytes:
    if not api.set_pointer(handle, 0, None, _FILE_BEGIN):
        raise PublicationError("read_failed")
    buffer = ctypes.create_string_buffer(_MAX_BYTES + 1)
    received = wintypes.DWORD(0)
    if not api.read_file(
        handle,
        buffer,
        len(buffer),
        ctypes.byref(received),
        None,
    ):
        raise PublicationError("read_failed")
    raw = buffer.raw[: received.value]
    if not 0 < len(raw) <= _MAX_BYTES:
        raise PublicationError("invalid_bytes")
    return raw


def _information(api: _WindowsFunctions, handle: int) -> _ByHandleFileInformation:
    information = _ByHandleFileInformation()
    if not api.get_information(handle, ctypes.byref(information)):
        raise PublicationError("identity_failed")
    return information


def _file_index(information: _ByHandleFileInformation) -> int:
    return (int(information.file_index_high) << 32) | int(information.file_index_low)


def _directory_identity(api: _WindowsFunctions, handle: int) -> dict[str, int]:
    information = _information(api, handle)
    if (
        not information.attributes & _FILE_ATTRIBUTE_DIRECTORY
        or information.attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise PublicationError("parent_identity_invalid")
    return {
        "attributes": int(information.attributes),
        "creation_time": _file_time(information.creation_time),
        "file_index": _file_index(information),
        "number_of_links": int(information.number_of_links),
        "volume_serial_number": int(information.volume_serial_number),
    }


def _require_directory_path(
    api: _WindowsFunctions,
    path: Path,
    baseline: dict[str, int],
) -> None:
    current_handle: int | None = None
    try:
        current_handle = _open(
            api,
            path,
            access=_GENERIC_READ,
            share=_FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            disposition=_OPEN_EXISTING,
            flags=_FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        )
        if _directory_identity(api, current_handle) != baseline:
            raise PublicationError("parent_identity_changed")
    finally:
        _close(api, current_handle)


def _snapshot(api: _WindowsFunctions, handle: int) -> dict[str, object]:
    information = _information(api, handle)
    if (
        information.attributes & _FILE_ATTRIBUTE_DIRECTORY
        or information.attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or information.number_of_links != 1
    ):
        raise PublicationError("identity_invalid")
    raw = _read(api, handle)
    size = (int(information.file_size_high) << 32) | int(information.file_size_low)
    if size != len(raw):
        raise PublicationError("identity_invalid")
    return {
        "attributes": int(information.attributes),
        "bytes": size,
        "creation_time": _file_time(information.creation_time),
        "file_index": _file_index(information),
        "last_write_time": _file_time(information.last_write_time),
        "number_of_links": int(information.number_of_links),
        "raw": raw,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "volume_serial_number": int(information.volume_serial_number),
    }


def _same(left: dict[str, object], right: dict[str, object]) -> bool:
    fields = (
        "attributes",
        "bytes",
        "creation_time",
        "file_index",
        "last_write_time",
        "number_of_links",
        "raw",
        "sha256",
        "volume_serial_number",
    )
    return all(left[field] == right[field] for field in fields)


def publish_failure_receipt(
    output: Path,
    error_type: str,
    *,
    after_temp_bound: Callable[[Path, Path], None] | None = None,
    after_move: Callable[[Path, dict[str, object]], None] | None = None,
    open_target: Callable[[_WindowsFunctions, Path], int] | None = None,
    after_target_bound: Callable[[Path, dict[str, object]], None] | None = None,
    after_final_bound: Callable[[Path, dict[str, object]], None] | None = None,
) -> dict[str, object]:
    output = Path(os.path.abspath(os.fspath(output)))
    if (
        not output.is_absolute()
        or not output.parent.is_dir()
        or os.path.lexists(output)
    ):
        raise PublicationError("output_invalid")
    raw = _canonical_failure(error_type)
    temporary = output.with_name(f".{output.name}.tmp-{uuid.uuid4().hex}")
    api = _WindowsFunctions()
    parent_handle: int | None = None
    source_handle: int | None = None
    target_handle: int | None = None
    final_parent_handle: int | None = None
    final_path_handle: int | None = None
    try:
        parent_handle = _open(
            api,
            output.parent,
            access=_GENERIC_READ,
            share=_FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            disposition=_OPEN_EXISTING,
            flags=_FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        )
        parent_baseline = _directory_identity(api, parent_handle)
        if os.path.lexists(output):
            raise PublicationError("output_invalid")
        source_handle = _open(
            api,
            temporary,
            access=_GENERIC_READ | _GENERIC_WRITE,
            share=_FILE_SHARE_READ | _FILE_SHARE_DELETE,
            disposition=_CREATE_NEW,
            flags=_FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        )
        _write(api, source_handle, raw)
        baseline = _snapshot(api, source_handle)
        if baseline["raw"] != raw:
            raise PublicationError("source_bytes_changed")
        if after_temp_bound is not None:
            after_temp_bound(temporary, output)
        if not _same(baseline, _snapshot(api, source_handle)):
            raise PublicationError("source_identity_changed")
        _require_directory_path(api, output.parent, parent_baseline)
        if not api.move_file(str(temporary), str(output), _MOVEFILE_WRITE_THROUGH):
            raise PublicationError("move_failed")
        if after_move is not None:
            after_move(output, baseline)
        if not _same(baseline, _snapshot(api, source_handle)):
            raise PublicationError("source_identity_changed")
        _require_directory_path(api, output.parent, parent_baseline)
        target_handle = (
            open_target(api, output)
            if open_target is not None
            else _open(
                api,
                output,
                access=_GENERIC_READ,
                share=_FILE_SHARE_READ | _FILE_SHARE_WRITE,
                disposition=_OPEN_EXISTING,
                flags=_FILE_FLAG_OPEN_REPARSE_POINT,
            )
        )
        current = _snapshot(api, target_handle)
        if not _same(baseline, current) or current["raw"] != raw:
            raise PublicationError("target_identity_changed")
        if after_target_bound is not None:
            after_target_bound(output, current)
        if not _same(current, _snapshot(api, target_handle)):
            raise PublicationError("target_identity_changed")

        final_parent_handle = _open(
            api,
            output.parent,
            access=_GENERIC_READ,
            share=_FILE_SHARE_READ | _FILE_SHARE_WRITE,
            disposition=_OPEN_EXISTING,
            flags=_FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        )
        if _directory_identity(api, final_parent_handle) != parent_baseline:
            raise PublicationError("parent_identity_changed")
        final_path_handle = _open(
            api,
            output,
            access=_GENERIC_READ,
            share=_FILE_SHARE_READ | _FILE_SHARE_WRITE,
            disposition=_OPEN_EXISTING,
            flags=_FILE_FLAG_OPEN_REPARSE_POINT,
        )
        final_current = _snapshot(api, final_path_handle)
        if not _same(current, final_current) or final_current["raw"] != raw:
            raise PublicationError("target_identity_changed")
        if after_final_bound is not None:
            after_final_bound(output, final_current)
        if (
            _directory_identity(api, final_parent_handle) != parent_baseline
            or not _same(current, _snapshot(api, target_handle))
            or not _same(final_current, _snapshot(api, final_path_handle))
        ):
            raise PublicationError("target_identity_changed")
        return {
            "attributes": final_current["attributes"],
            "bytes": final_current["bytes"],
            "creation_time": final_current["creation_time"],
            "file_index": final_current["file_index"],
            "last_write_time": final_current["last_write_time"],
            "number_of_links": final_current["number_of_links"],
            "schema": SCHEMA,
            "sha256": final_current["sha256"],
            "status": "published",
            "volume_serial_number": final_current["volume_serial_number"],
        }
    finally:
        _close(api, final_path_handle)
        _close(api, final_parent_handle)
        _close(api, target_handle)
        _close(api, source_handle)
        _close(api, parent_handle)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--error-type", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        receipt = publish_failure_receipt(args.output, args.error_type)
    except BaseException:
        print(
            '{"schema":"bidking_release_availability_failure_publication_v1","status":"publication_failed"}'
        )
        return 1
    print(json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
