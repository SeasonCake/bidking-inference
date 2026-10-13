"""Run the compiled diagnostic smoke and exchange only a canonical local receipt."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


_SCHEMA = "bidking_native_diagnostic_smoke_receipt_v1"
_MAX_RECEIPT_BYTES = 256
_MAX_STATUS = 255
_GENERIC_READ = 0x80000000
_DELETE = 0x00010000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_BEGIN = 0
_FILE_DISPOSITION_INFO_CLASS = 4
_SAFE_REASONS = frozenset(
    {
        "ok",
        "unknown",
        "native_module_missing",
        "native_module_load_failed",
        "native_symbol_missing",
        "native_access_denied",
        "native_exception",
        "native_status_invalid",
        "native_status_nonzero",
        "client_lifecycle_unavailable",
        "client_lifecycle_request_rejected",
        "client_lifecycle_busy",
        "client_lifecycle_payload_invalid",
        "client_lifecycle_start_failed",
    }
)


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


class _FileDispositionInformation(ctypes.Structure):
    _fields_ = (("delete_file", wintypes.BOOLEAN),)


def _windows_functions() -> tuple[object, ...]:
    if os.name != "nt":
        raise OSError("windows_required")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    set_pointer = kernel32.SetFilePointerEx
    set_pointer.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    )
    set_pointer.restype = wintypes.BOOL
    read_file = kernel32.ReadFile
    read_file.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    read_file.restype = wintypes.BOOL
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return (
        create_file,
        get_information,
        set_pointer,
        read_file,
        set_information,
        close_handle,
    )


def _canonical_receipt(status: int, reason: str) -> bytes:
    payload = {"reason": reason, "schema": _SCHEMA, "status": status}
    return (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def _validate_values(status: object, reason: object) -> tuple[int, str]:
    if type(status) is not int or not 0 <= status <= _MAX_STATUS:  # noqa: E721
        raise ValueError("invalid_receipt")
    if (
        type(reason) is not str  # noqa: E721 - exact built-in required
        or not 2 <= len(reason) <= 64
        or reason not in _SAFE_REASONS
    ):
        raise ValueError("invalid_receipt")
    return status, reason


def _reject_json_constant(_value: str) -> object:
    raise ValueError("invalid_receipt")


def _reject_json_float(_value: str) -> object:
    raise ValueError("invalid_receipt")


def _parse_receipt(raw: bytes) -> tuple[int, str]:
    if not 0 < len(raw) <= _MAX_RECEIPT_BYTES:
        raise ValueError("invalid_receipt")
    text = raw.decode("utf-8", "strict")
    if text.encode("ascii", "strict") != raw:
        raise ValueError("invalid_receipt")
    try:
        payload = json.loads(
            text,
            parse_constant=_reject_json_constant,
            parse_float=_reject_json_float,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid_receipt") from error
    if type(payload) is not dict or set(payload) != {  # noqa: E721
        "reason",
        "schema",
        "status",
    }:
        raise ValueError("invalid_receipt")
    if type(payload["schema"]) is not str or payload["schema"] != _SCHEMA:  # noqa: E721
        raise ValueError("invalid_receipt")
    status, reason = _validate_values(payload["status"], payload["reason"])
    if raw != _canonical_receipt(status, reason):
        raise ValueError("invalid_receipt")
    return status, reason


def _load_receipt(path: Path) -> tuple[int, str]:
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("invalid_receipt")
    file_attributes = getattr(before, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if file_attributes & reparse_attribute:
        raise ValueError("invalid_receipt")
    if not 0 < before.st_size <= _MAX_RECEIPT_BYTES:
        raise ValueError("invalid_receipt")

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
            or current.st_size != before.st_size
            or getattr(current, "st_file_attributes", 0) & reparse_attribute
        ):
            raise ValueError("invalid_receipt")
        chunks: list[bytes] = []
        remaining = _MAX_RECEIPT_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if os.read(descriptor, 1):
            raise ValueError("invalid_receipt")
    finally:
        os.close(descriptor)
    if len(raw) != before.st_size:
        raise ValueError("invalid_receipt")
    return _parse_receipt(raw)


def _file_time_value(value: _FileTime) -> int:
    return (int(value.high) << 32) | int(value.low)


def _read_windows_handle(handle: int, set_pointer: object, read_file: object) -> bytes:
    if not set_pointer(handle, 0, None, _FILE_BEGIN):
        raise OSError("receipt_read_failed")
    chunks: list[bytes] = []
    remaining = _MAX_RECEIPT_BYTES + 1
    while remaining > 0:
        buffer = ctypes.create_string_buffer(remaining)
        received = wintypes.DWORD(0)
        if not read_file(handle, buffer, remaining, ctypes.byref(received), None):
            raise OSError("receipt_read_failed")
        if received.value == 0:
            break
        chunks.append(buffer.raw[: received.value])
        remaining -= received.value
    raw = b"".join(chunks)
    if not 0 < len(raw) <= _MAX_RECEIPT_BYTES:
        raise ValueError("invalid_receipt")
    return raw


def _windows_snapshot(handle: int, functions: tuple[object, ...]) -> dict[str, object]:
    _, get_information, set_pointer, read_file, _, _ = functions
    information = _ByHandleFileInformation()
    if not get_information(handle, ctypes.byref(information)):
        raise OSError("receipt_identity_failed")
    if (
        information.attributes & _FILE_ATTRIBUTE_DIRECTORY
        or information.attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or information.number_of_links != 1
    ):
        raise ValueError("invalid_receipt")
    size = (int(information.file_size_high) << 32) | int(information.file_size_low)
    raw = _read_windows_handle(handle, set_pointer, read_file)
    if size != len(raw):
        raise ValueError("invalid_receipt")
    return {
        "attributes": int(information.attributes),
        "bytes": size,
        "creation_time": _file_time_value(information.creation_time),
        "file_index": (
            (int(information.file_index_high) << 32) | int(information.file_index_low)
        ),
        "last_write_time": _file_time_value(information.last_write_time),
        "number_of_links": int(information.number_of_links),
        "raw": raw,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "volume_serial_number": int(information.volume_serial_number),
    }


def _open_windows_receipt(
    path: Path,
    *,
    access: int,
    share: int,
    functions: tuple[object, ...],
) -> int:
    create_file = functions[0]
    handle = create_file(
        str(path),
        access,
        share,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle or handle is None:
        raise OSError("receipt_open_failed")
    return int(handle)


def _close_windows_handle(handle: int | None, functions: tuple[object, ...]) -> bool:
    if handle is None:
        return True
    return bool(functions[5](handle))


def _same_windows_snapshot(
    left: dict[str, object],
    right: dict[str, object],
) -> bool:
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


def _bind_windows_receipt(
    path: Path,
) -> tuple[int, dict[str, object], tuple[object, ...]]:
    functions = _windows_functions()
    handle = _open_windows_receipt(
        path,
        access=_GENERIC_READ,
        share=_FILE_SHARE_READ | _FILE_SHARE_DELETE,
        functions=functions,
    )
    try:
        snapshot = _windows_snapshot(handle, functions)
        status, reason = _parse_receipt(snapshot["raw"])
        if status != 0 or reason != "ok":
            raise ValueError("invalid_receipt")
    except BaseException:
        _close_windows_handle(handle, functions)
        raise
    return handle, snapshot, functions


def _delete_bound_windows_receipt(
    path: Path,
    bound_handle: int,
    baseline: dict[str, object],
    functions: tuple[object, ...],
) -> tuple[str, int | None]:
    cleanup_handle: int | None = None
    try:
        if not _same_windows_snapshot(
            baseline,
            _windows_snapshot(bound_handle, functions),
        ):
            return "cleanup_conflict", bound_handle
        cleanup_handle = _open_windows_receipt(
            path,
            access=_GENERIC_READ | _DELETE,
            share=_FILE_SHARE_READ,
            functions=functions,
        )
        current = _windows_snapshot(cleanup_handle, functions)
        if not _same_windows_snapshot(baseline, current):
            return "cleanup_conflict", bound_handle
        disposition = _FileDispositionInformation(1)
        set_information = functions[4]
        if not set_information(
            cleanup_handle,
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            return "cleanup_conflict", bound_handle
        if not _close_windows_handle(bound_handle, functions):
            bound_handle = None
            return "cleanup_conflict", bound_handle
        bound_handle = None
        if not _close_windows_handle(cleanup_handle, functions):
            cleanup_handle = None
            return "cleanup_conflict", bound_handle
        cleanup_handle = None
        try:
            os.lstat(path)
        except FileNotFoundError:
            return "deleted", bound_handle
        return "cleanup_conflict", bound_handle
    except BaseException:
        return "cleanup_conflict", bound_handle
    finally:
        _close_windows_handle(cleanup_handle, functions)


def _emit_status(payload: dict[str, object]) -> None:
    raw = (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _hold(receipt_path: Path) -> int:
    bound_handle: int | None = None
    functions: tuple[object, ...] | None = None
    try:
        bound_handle, baseline, functions = _bind_windows_receipt(receipt_path)
        _emit_status(
            {
                "bytes": baseline["bytes"],
                "file_index": baseline["file_index"],
                "sha256": baseline["sha256"],
                "status": "bound",
                "volume_serial_number": baseline["volume_serial_number"],
            }
        )
        command = sys.stdin.buffer.readline(32)
        if command == b"abort\n":
            _close_windows_handle(bound_handle, functions)
            bound_handle = None
            _emit_status({"status": "retained"})
            return 0
        if command != b"delete\n":
            _close_windows_handle(bound_handle, functions)
            bound_handle = None
            _emit_status({"status": "cleanup_conflict"})
            return 3
        status, bound_handle = _delete_bound_windows_receipt(
            receipt_path,
            bound_handle,
            baseline,
            functions,
        )
        _close_windows_handle(bound_handle, functions)
        bound_handle = None
        _emit_status({"status": status})
        return 0 if status == "deleted" else 3
    except BaseException:
        if functions is not None:
            _close_windows_handle(bound_handle, functions)
        try:
            _emit_status({"status": "bind_failed"})
        except BaseException:
            pass
        return 2


def _write_receipt(path: Path, status: int, reason: str) -> None:
    raw = _canonical_receipt(status, reason)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("receipt_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run(receipt_path: Path, expected_generation: str) -> int:
    if (
        type(expected_generation) is not str  # noqa: E721 - exact built-in required
        or len(expected_generation) != 64
        or any(character not in "0123456789abcdef" for character in expected_generation)
    ):
        return 1
    sys.path.insert(0, os.getcwd())
    try:
        import bidking_kernel  # type: ignore
        import _client_launcher  # type: ignore

        contract = bidking_kernel.client_lifecycle_contract()
        if tuple(contract) != (2, expected_generation, True):
            return 1
        if not callable(_client_launcher.repair_main):
            return 1
        receipt = _client_launcher.diagnostics_receipt_main()
        if type(receipt) is not tuple or len(receipt) != 2:  # noqa: E721
            return 1
        status, reason = _validate_values(receipt[0], receipt[1])
        _write_receipt(receipt_path, status, reason)
        return status
    except BaseException:
        return 1


def _validate(receipt_path: Path) -> int:
    try:
        status, reason = _load_receipt(receipt_path)
    except BaseException:
        return 1
    return 0 if status == 0 and reason == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 3 and arguments[0] == "run":
        path = Path(arguments[1])
        if not path.is_absolute():
            return 1
        return _run(path, arguments[2])
    if len(arguments) == 2 and arguments[0] == "validate":
        path = Path(arguments[1])
        if not path.is_absolute():
            return 1
        return _validate(path)
    if len(arguments) == 2 and arguments[0] == "hold":
        path = Path(arguments[1])
        if not path.is_absolute():
            return 1
        return _hold(path)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
