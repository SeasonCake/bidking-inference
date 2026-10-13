"""Hold the verified portable package as a read-only Windows execution tree.

The signed native gate retains every package file handle after validating signed
coverage.  This module closes the
separate child-namespace gap: after elevation it replaces every package directory
DACL with one that permits read/execute but not file/subdirectory creation, keeps a
crash-recovery journal outside the package, and restores the original descriptors
when the managed launcher exits.

Only the generated public launcher calls :func:`begin_namespace_lease`.  Test-only
callers use the underscored constructor with an explicit policy; there is no runtime
flag or environment fallback that weakens the production owner/DACL contract.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable


_REPARSE_POINT = 0x400
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x0000_0001
_GROUP_SECURITY_INFORMATION = 0x0000_0002
_DACL_SECURITY_INFORMATION = 0x0000_0004
_SDDL_REVISION_1 = 1
_SE_DACL_PROTECTED = 0x1000
_SE_DACL_AUTO_INHERITED = 0x0400
_PROTECTED_DACL_SECURITY_INFORMATION = 0x8000_0000
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x2000_0000
_ADMINISTRATORS_SID = "S-1-5-32-544"

# SYSTEM can recover a crashed lease.  Administrators can read/execute and restore
# owner/DACL, but neither the elevated product nor a normal Users token can create,
# delete or overwrite package children or named file streams while the lease is live.
_PRODUCTION_DIRECTORY_SDDL = (
    "O:BAG:BAD:P"
    "(A;OICI;FA;;;SY)"
    "(A;OICI;FRFXRCWDWO;;;BA)"
    "(A;OICI;FRFX;;;BU)"
)
_PRODUCTION_FILE_SDDL = (
    "O:BAG:BAD:P"
    "(A;;FA;;;SY)"
    "(A;;FRFXRCWDWO;;;BA)"
    "(A;;FRFX;;;BU)"
)
_RECOVERY_DIRECTORY_SDDL = "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
_JOURNAL_SCHEMA = "bidking_local_integrity_namespace_recovery_v2"
_DEFAULT_DATA_STREAM = "::$DATA"
_WINDOWS_ZONE_IDENTIFIER_STREAM = ":Zone.Identifier:$DATA"
_WINDOWS_ZONE_IDENTIFIER_MAX_BYTES = 16 * 1024
_ERROR_HANDLE_EOF = 38
_ERROR_NO_MORE_FILES = 18


class NamespaceGuardError(RuntimeError):
    """Safe, non-secret reason why the package namespace could not be held."""


class NamespaceRestoreError(NamespaceGuardError):
    """Some original descriptors remain unrestored; the journal must survive."""

    def __init__(self, failed_count: int, attempted_count: int) -> None:
        super().__init__("namespace_restore_incomplete")
        self.failed_count = failed_count
        self.attempted_count = attempted_count


def _timing_event(
    callback: Callable[[str, str], object] | None, event: str, result: str
) -> None:
    if callback is None:
        return
    try:
        callback(event, result)
    except BaseException:
        # Observability must never change a security decision or cleanup path.
        pass


if os.name == "nt":
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _get_named_security_info = _advapi32.GetNamedSecurityInfoW
    _get_named_security_info.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _get_named_security_info.restype = wintypes.DWORD

    _convert_sd_to_string = (
        _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
    )
    _convert_sd_to_string.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _convert_sd_to_string.restype = wintypes.BOOL

    _convert_string_to_sd = (
        _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    )
    _convert_string_to_sd.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _convert_string_to_sd.restype = wintypes.BOOL

    _get_sd_control = _advapi32.GetSecurityDescriptorControl
    _get_sd_control.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _get_sd_control.restype = wintypes.BOOL

    _set_file_security = _advapi32.SetFileSecurityW
    _set_file_security.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _set_file_security.restype = wintypes.BOOL

    _get_sd_owner = _advapi32.GetSecurityDescriptorOwner
    _get_sd_group = _advapi32.GetSecurityDescriptorGroup
    for _get_sd_sid in (_get_sd_owner, _get_sd_group):
        _get_sd_sid.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        ]
        _get_sd_sid.restype = wintypes.BOOL
    _get_sd_dacl = _advapi32.GetSecurityDescriptorDacl
    _get_sd_dacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    _get_sd_dacl.restype = wintypes.BOOL
    _set_named_security_info = _advapi32.SetNamedSecurityInfoW
    _set_named_security_info.argtypes = [
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _set_named_security_info.restype = wintypes.DWORD

    _convert_sid_to_string = _advapi32.ConvertSidToStringSidW
    _convert_sid_to_string.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _convert_sid_to_string.restype = wintypes.BOOL

    _local_free = _kernel32.LocalFree
    _local_free.argtypes = [ctypes.c_void_p]
    _local_free.restype = ctypes.c_void_p

    _is_user_an_admin = _shell32.IsUserAnAdmin
    _is_user_an_admin.argtypes = []
    _is_user_an_admin.restype = wintypes.BOOL

    class _Win32FindStreamData(ctypes.Structure):
        _fields_ = [
            ("stream_size", ctypes.c_longlong),
            ("stream_name", wintypes.WCHAR * 296),
        ]

    _find_first_stream = _kernel32.FindFirstStreamW
    _find_first_stream.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _find_first_stream.restype = wintypes.HANDLE

    _find_next_stream = _kernel32.FindNextStreamW
    _find_next_stream.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    _find_next_stream.restype = wintypes.BOOL

    _find_close = _kernel32.FindClose
    _find_close.argtypes = [wintypes.HANDLE]
    _find_close.restype = wintypes.BOOL


@dataclass(frozen=True)
class _SecurityRecord:
    kind: str
    relative: str
    sddl: str
    protected: bool


def _require_windows() -> None:
    if os.name != "nt":
        raise NamespaceGuardError("namespace_guard_windows_only")


def _reject_reparse(path: Path, *, directory: bool | None = None) -> None:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise NamespaceGuardError("namespace_directory_unreadable") from exc
    attributes = int(getattr(info, "st_file_attributes", 0))
    if stat.S_ISLNK(info.st_mode) or attributes & _REPARSE_POINT:
        raise NamespaceGuardError("namespace_reparse_rejected")
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise NamespaceGuardError("namespace_directory_type_invalid")
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise NamespaceGuardError("namespace_file_type_invalid")


def _sid_string(sid: ctypes.c_void_p) -> str:
    output = ctypes.c_void_p()
    if not _convert_sid_to_string(sid, ctypes.byref(output)):
        raise NamespaceGuardError("namespace_owner_unreadable")
    try:
        return ctypes.wstring_at(output)
    finally:
        _local_free(output)


def _security_descriptor(path: Path) -> tuple[str, bool, str]:
    _require_windows()
    owner = ctypes.c_void_p()
    group = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    information = (
        _OWNER_SECURITY_INFORMATION
        | _GROUP_SECURITY_INFORMATION
        | _DACL_SECURITY_INFORMATION
    )
    code = _get_named_security_info(
        str(path),
        _SE_FILE_OBJECT,
        information,
        ctypes.byref(owner),
        ctypes.byref(group),
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if code != 0 or not descriptor.value:
        raise NamespaceGuardError("namespace_security_read_failed")
    output = ctypes.c_void_p()
    try:
        length = wintypes.DWORD()
        if not _convert_sd_to_string(
            descriptor,
            _SDDL_REVISION_1,
            information,
            ctypes.byref(output),
            ctypes.byref(length),
        ):
            raise NamespaceGuardError("namespace_security_read_failed")
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not _get_sd_control(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise NamespaceGuardError("namespace_security_read_failed")
        return (
            ctypes.wstring_at(output),
            bool(control.value & _SE_DACL_PROTECTED),
            _sid_string(owner),
        )
    finally:
        if output.value:
            _local_free(output)
        _local_free(descriptor)


def _parsed_descriptor(sddl: str) -> ctypes.c_void_p:
    descriptor = ctypes.c_void_p()
    size = wintypes.DWORD()
    if not _convert_string_to_sd(
        sddl,
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(size),
    ):
        raise NamespaceGuardError("namespace_security_policy_invalid")
    return descriptor


def _canonical_sddl(sddl: str) -> str:
    descriptor = _parsed_descriptor(sddl)
    output = ctypes.c_void_p()
    try:
        length = wintypes.DWORD()
        information = (
            _OWNER_SECURITY_INFORMATION
            | _GROUP_SECURITY_INFORMATION
            | _DACL_SECURITY_INFORMATION
        )
        if not _convert_sd_to_string(
            descriptor,
            _SDDL_REVISION_1,
            information,
            ctypes.byref(output),
            ctypes.byref(length),
        ):
            raise NamespaceGuardError("namespace_security_policy_invalid")
        return ctypes.wstring_at(output)
    finally:
        if output.value:
            _local_free(output)
        _local_free(descriptor)


def _dacl_aces(sddl: str) -> str:
    marker = sddl.find("D:")
    if marker < 0:
        raise NamespaceGuardError("namespace_security_policy_invalid")
    first_ace = sddl.find("(", marker)
    if first_ace < 0:
        raise NamespaceGuardError("namespace_security_policy_invalid")
    return sddl[first_ace:]


def _set_security_descriptor(path: Path, sddl: str, *, protected: bool) -> None:
    descriptor = _parsed_descriptor(sddl)
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not _get_sd_control(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ) or bool(control.value & _SE_DACL_PROTECTED) != protected:
            raise NamespaceGuardError("namespace_security_policy_invalid")
        information = (
            _OWNER_SECURITY_INFORMATION
            | _GROUP_SECURITY_INFORMATION
            | _DACL_SECURITY_INFORMATION
        )
        if control.value & _SE_DACL_AUTO_INHERITED:
            # The legacy setter clears AI even when it is present in the saved
            # descriptor. The auto-inheritance API must only serve saved AI
            # records: using it for legacy records would add AI instead.
            owner = ctypes.c_void_p()
            group = ctypes.c_void_p()
            dacl = ctypes.c_void_p()
            defaulted = wintypes.BOOL()
            present = wintypes.BOOL()
            if (
                not _get_sd_owner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted))
                or not _get_sd_group(descriptor, ctypes.byref(group), ctypes.byref(defaulted))
                or not _get_sd_dacl(
                    descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
                )
                or not present.value
            ):
                raise NamespaceGuardError("namespace_security_policy_invalid")
            information |= (
                _PROTECTED_DACL_SECURITY_INFORMATION
                if protected else _UNPROTECTED_DACL_SECURITY_INFORMATION
            )
            if _set_named_security_info(
                str(path), _SE_FILE_OBJECT, information, owner, group, dacl, None
            ) != 0:
                raise NamespaceGuardError("namespace_security_apply_failed")
        elif not _set_file_security(str(path), information, descriptor):
            raise NamespaceGuardError("namespace_security_apply_failed")
        actual_sddl, actual_protected, _owner = _security_descriptor(path)
        if actual_sddl != _canonical_sddl(sddl) or actual_protected != protected:
            raise NamespaceGuardError("namespace_security_apply_unverified")
    finally:
        _local_free(descriptor)


def _package_identity(root: Path) -> tuple[int, int]:
    info = root.stat(follow_symlinks=False)
    identity = (int(info.st_dev), int(info.st_ino))
    if identity[1] == 0:
        raise NamespaceGuardError("namespace_root_identity_unavailable")
    return identity


def _valid_windows_zone_identifier(payload: bytes) -> bool:
    if not payload or len(payload) > _WINDOWS_ZONE_IDENTIFIER_MAX_BYTES:
        return False
    # Omit one download-writer string terminator from the parsing view only.
    # Stream size, identity and snapshots continue to use the original bytes.
    if payload.endswith(b"\x00"):
        payload = payload[:-1]
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if "\x00" in text:
        return False
    section_seen = False
    zone_id_seen = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("["):
            if section_seen or line.casefold() != "[zonetransfer]":
                return False
            section_seen = True
            continue
        if not section_seen or "=" not in line:
            return False
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip()
        if not key or any(
            not (character.isascii() and (character.isalnum() or character in "_-."))
            for character in key
        ):
            return False
        if key.casefold() == "zoneid":
            if zone_id_seen or raw_value.strip() not in {"3", "4"}:
                return False
            zone_id_seen = True
    return section_seen and zone_id_seen


def _read_zone_identifier(
    path: Path, stream_name: str, expected_size: int
) -> bytes:
    if (
        expected_size <= 0
        or expected_size > _WINDOWS_ZONE_IDENTIFIER_MAX_BYTES
    ):
        raise NamespaceGuardError("namespace_named_stream_rejected")
    stream_path = str(path) + stream_name
    try:
        with open(stream_path, "rb") as stream:
            before = os.fstat(stream.fileno())
            payload = stream.read(_WINDOWS_ZONE_IDENTIFIER_MAX_BYTES + 1)
        with open(stream_path, "rb") as rebound:
            after = os.fstat(rebound.fileno())
    except OSError as exc:
        raise NamespaceGuardError("namespace_stream_inventory_unavailable") from exc
    if (
        len(payload) != expected_size
        or int(before.st_size) != expected_size
        or int(after.st_size) != expected_size
        or (int(before.st_dev), int(before.st_ino))
        != (int(after.st_dev), int(after.st_ino))
        or not _valid_windows_zone_identifier(payload)
    ):
        raise NamespaceGuardError("namespace_named_stream_rejected")
    return payload


def _file_streams(path: Path) -> list[tuple[str, int]]:
    """Return the complete NTFS data-stream namespace for one ordinary file."""

    _require_windows()
    _reject_reparse(path, directory=False)
    data = _Win32FindStreamData()
    handle = _find_first_stream(str(path), 0, ctypes.byref(data), 0)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        raise NamespaceGuardError("namespace_stream_inventory_unavailable")
    streams: list[tuple[str, int]] = []
    try:
        while True:
            name = str(data.stream_name)
            if not name or data.stream_size < 0:
                raise NamespaceGuardError("namespace_stream_inventory_invalid")
            streams.append((name, int(data.stream_size)))
            if _find_next_stream(handle, ctypes.byref(data)):
                continue
            error = ctypes.get_last_error()
            if error not in {_ERROR_HANDLE_EOF, _ERROR_NO_MORE_FILES}:
                raise NamespaceGuardError("namespace_stream_inventory_unavailable")
            break
    finally:
        _find_close(handle)
    streams.sort(key=lambda item: item[0].casefold())
    folded = [name.casefold() for name, _size in streams]
    if len(folded) != len(set(folded)):
        raise NamespaceGuardError("namespace_named_stream_rejected")
    expected = {_DEFAULT_DATA_STREAM.casefold()}
    actual = set(folded)
    if actual == expected:
        return streams
    zone_folded = _WINDOWS_ZONE_IDENTIFIER_STREAM.casefold()
    if actual != expected | {zone_folded}:
        raise NamespaceGuardError("namespace_named_stream_rejected")
    zone_name, zone_size = next(
        (name, size)
        for name, size in streams
        if name.casefold() == zone_folded
    )
    _read_zone_identifier(path, zone_name, zone_size)
    return streams


def _protected_paths(root: Path) -> tuple[list[Path], list[Path]]:
    _reject_reparse(root, directory=True)
    directories_result = [root]
    files_result: list[Path] = []

    def reject_walk_error(exc: OSError) -> None:
        raise NamespaceGuardError("namespace_directory_unreadable") from exc

    for current, directories, files in os.walk(
        root, followlinks=False, onerror=reject_walk_error
    ):
        current_path = Path(current)
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        for name in directories:
            child = current_path / name
            _reject_reparse(child, directory=True)
            directories_result.append(child)
        for name in files:
            child = current_path / name
            _reject_reparse(child, directory=False)
            _file_streams(child)
            files_result.append(child)
    return directories_result, files_result


def _relative_member(root: Path, path: Path) -> str:
    if path == root:
        return "."
    relative = path.relative_to(root).as_posix()
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != relative
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise NamespaceGuardError("namespace_recovery_path_invalid")
    return relative


def _record_path(root: Path, relative: str) -> Path:
    if relative == ".":
        return root
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != relative
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise NamespaceGuardError("namespace_recovery_path_invalid")
    return root.joinpath(*parsed.parts)


def _journal_path(root: Path, recovery_root: Path) -> Path:
    identity = _package_identity(root)
    key = hashlib.sha256(
        f"{os.path.normcase(str(root))}\0{identity[0]}\0{identity[1]}".encode(
            "utf-8"
        )
    ).hexdigest()
    return recovery_root / f"{key}.json"


def _write_journal(
    path: Path, root: Path, records: list[_SecurityRecord]
) -> None:
    payload = {
        "identity": list(_package_identity(root)),
        "records": [record.__dict__ for record in records],
        "root": str(root),
        "schema": _JOURNAL_SCHEMA,
    }
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_journal(path: Path, root: Path) -> list[_SecurityRecord]:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NamespaceGuardError("namespace_recovery_invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"identity", "records", "root", "schema"}
        or payload["schema"] != _JOURNAL_SCHEMA
        or payload["root"] != str(root)
        or payload["identity"] != list(_package_identity(root))
        or not isinstance(payload["records"], list)
        or not payload["records"]
    ):
        raise NamespaceGuardError("namespace_recovery_invalid")
    records: list[_SecurityRecord] = []
    for raw in payload["records"]:
        if (
            not isinstance(raw, dict)
            or set(raw) != {"kind", "protected", "relative", "sddl"}
            or raw["kind"] not in {"directory", "file"}
            or not isinstance(raw["relative"], str)
            or not isinstance(raw["sddl"], str)
            or not raw["sddl"]
            or not isinstance(raw["protected"], bool)
        ):
            raise NamespaceGuardError("namespace_recovery_invalid")
        _record_path(root, raw["relative"])
        records.append(_SecurityRecord(**raw))
    return records


def _restore_records(root: Path, records: list[_SecurityRecord]) -> None:
    failures: dict[int, Exception] = {}
    indexed = list(enumerate(records))
    # Original records contain the canonical SDDL returned by Windows. This
    # only chooses application order; the setter still parses/validates each
    # descriptor. AI parents can propagate into children already restored.
    has_auto_inherited = any(
        "AI" in record.sddl.partition("D:")[2].partition("(")[0]
        for record in records
    )
    if has_auto_inherited:
        ordered = sorted(indexed, key=lambda pair: len(PurePosixPath(pair[1].relative).parts))
    else:
        ordered = reversed(indexed)
    for index, record in ordered:
        try:
            target = _record_path(root, record.relative)
            _reject_reparse(target, directory=record.kind == "directory")
            _set_security_descriptor(target, record.sddl, protected=record.protected)
        except Exception as exc:
            # A failed member never authorizes weaker validation of siblings.
            # Keep the selected order, attempt each independently, and retain the full
            # journal unless every original descriptor has been restored.
            failures[index] = exc
    # Do not trust per-setter readback: a later parent write can change a child.
    # Count each failed record once, including inaccessible/changed siblings.
    for index, record in indexed:
        try:
            target = _record_path(root, record.relative)
            _reject_reparse(target, directory=record.kind == "directory")
            actual_sddl, actual_protected, _owner = _security_descriptor(target)
            if actual_sddl != record.sddl or actual_protected != record.protected:
                raise NamespaceGuardError("namespace_restore_verification_failed")
        except Exception as exc:
            failures.setdefault(index, exc)
    if failures:
        raise NamespaceRestoreError(len(failures), len(records)) from next(iter(failures.values()))


def _prepare_recovery_root(path: Path, *, secure: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _reject_reparse(path, directory=True)
    if secure:
        _set_security_descriptor(path, _RECOVERY_DIRECTORY_SDDL, protected=True)
        actual, protected, owner = _security_descriptor(path)
        if (
            _dacl_aces(actual)
            != _dacl_aces(_canonical_sddl(_RECOVERY_DIRECTORY_SDDL))
            or not protected
            or owner != _ADMINISTRATORS_SID
        ):
            raise NamespaceGuardError("namespace_recovery_acl_invalid")


def _attempt_namespace_create(directory: Path) -> None:
    marker = directory / f".bidking-integrity-deny-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except PermissionError:
        return
    except OSError as exc:
        raise NamespaceGuardError("namespace_write_denial_unproven") from exc
    else:
        os.close(descriptor)
        try:
            marker.unlink()
        except OSError:
            pass
        raise NamespaceGuardError("namespace_write_denial_unproven")


def _attempt_named_stream_create(file_path: Path) -> None:
    marker = f"{file_path}:.bidking-integrity-deny-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except PermissionError:
        return
    except OSError as exc:
        raise NamespaceGuardError("namespace_stream_write_denial_unproven") from exc
    else:
        os.close(descriptor)
        try:
            os.unlink(marker)
        except OSError:
            pass
        raise NamespaceGuardError("namespace_stream_write_denial_unproven")


class NamespaceLease:
    """Restore exact original directory and file security on managed exit."""

    def __init__(
        self,
        root: Path,
        records: list[_SecurityRecord],
        journal: Path,
        expected_directory_sddl: str,
        expected_file_sddl: str,
        expected_owner: str,
        timing_event: Callable[[str, str], object] | None,
    ) -> None:
        self._root = root
        self._records = records
        self._journal = journal
        self._expected_directory_sddl = expected_directory_sddl
        self._expected_file_sddl = expected_file_sddl
        self._expected_owner = expected_owner
        self._timing_event = timing_event
        self._closed = False

    def attest(self) -> None:
        representative_file: Path | None = None
        for record in self._records:
            target = _record_path(self._root, record.relative)
            is_directory = record.kind == "directory"
            _reject_reparse(target, directory=is_directory)
            actual, protected, owner = _security_descriptor(target)
            expected = _canonical_sddl(
                self._expected_directory_sddl
                if is_directory
                else self._expected_file_sddl
            )
            if (
                _dacl_aces(actual) != _dacl_aces(expected)
                or not protected
                or owner != self._expected_owner
            ):
                raise NamespaceGuardError("namespace_lock_attestation_failed")
            if not is_directory and representative_file is None:
                representative_file = target
        # This is an effective-token control, not merely an ACL string comparison.
        _attempt_namespace_create(self._root)
        if representative_file is None:
            raise NamespaceGuardError("namespace_file_inventory_empty")
        _attempt_named_stream_create(representative_file)

    def close(self) -> None:
        if self._closed:
            return
        _timing_event(self._timing_event, "acl_restore", "begin")
        try:
            _restore_records(self._root, self._records)
            try:
                self._journal.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise NamespaceGuardError("namespace_recovery_cleanup_failed") from exc
            self._closed = True
            _timing_event(self._timing_event, "acl_restore", "ok")
        except BaseException:
            _timing_event(self._timing_event, "acl_restore", "failed")
            raise

    def __enter__(self) -> NamespaceLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _begin_namespace_lease(
    package_root: str | os.PathLike[str],
    *,
    locked_directory_sddl: str,
    locked_file_sddl: str,
    expected_owner: str,
    recovery_root: str | os.PathLike[str],
    require_elevated: bool,
    secure_recovery: bool,
    timing_event: Callable[[str, str], object] | None = None,
) -> NamespaceLease:
    _require_windows()
    if require_elevated and not _is_user_an_admin():
        raise NamespaceGuardError("namespace_guard_elevation_required")
    root = Path(os.path.abspath(package_root))
    _reject_reparse(root, directory=True)
    recovery = Path(os.path.abspath(recovery_root))
    _prepare_recovery_root(recovery, secure=secure_recovery)
    journal = _journal_path(root, recovery)
    if journal.exists():
        stale = _read_journal(journal, root)
        _restore_records(root, stale)
        journal.unlink()

    _timing_event(timing_event, "acl_inventory", "begin")
    directories, files = _protected_paths(root)
    records: list[_SecurityRecord] = []
    for target in directories:
        sddl, protected, _owner = _security_descriptor(target)
        records.append(
            _SecurityRecord(
                kind="directory",
                relative=_relative_member(root, target),
                sddl=sddl,
                protected=protected,
            )
        )
    for target in files:
        sddl, protected, _owner = _security_descriptor(target)
        records.append(
            _SecurityRecord(
                kind="file",
                relative=_relative_member(root, target),
                sddl=sddl,
                protected=protected,
            )
        )
    _timing_event(timing_event, "acl_inventory", "ok")
    _timing_event(timing_event, "acl_journal", "begin")
    _write_journal(journal, root, records)
    _timing_event(timing_event, "acl_journal", "ok")

    applied: list[_SecurityRecord] = []
    active_timing_event = "acl_apply"
    try:
        # Root first prevents any new top-level load root; descendants are then
        # sealed parent-before-child.  Existing files receive a separate DACL so
        # inheritable owner grants cannot leave their named-stream namespaces open.
        # The native full coverage/stream recheck follows this entire operation.
        _timing_event(timing_event, "acl_apply", "begin")
        for record in records:
            target = _record_path(root, record.relative)
            # Include the attempted record: the OS may have changed security
            # before a setter/verification error becomes visible to Python.
            applied.append(record)
            _set_security_descriptor(
                target,
                locked_directory_sddl
                if record.kind == "directory"
                else locked_file_sddl,
                protected=True,
            )
        _timing_event(timing_event, "acl_apply", "ok")
        lease = NamespaceLease(
            root,
            records,
            journal,
            locked_directory_sddl,
            locked_file_sddl,
            expected_owner,
            timing_event,
        )
        active_timing_event = "acl_attest"
        _timing_event(timing_event, "acl_attest", "begin")
        lease.attest()
        _timing_event(timing_event, "acl_attest", "ok")
        return lease
    except BaseException as original_error:
        _timing_event(timing_event, active_timing_event, "failed")
        original_error.namespace_restore_state = "not_acquired"
        if applied:
            try:
                # An AI parent restore can affect even children that the lock
                # attempt never reached. All original records are already saved.
                _restore_records(root, records)
                original_error.namespace_restore_state = "restored"
            except BaseException as restore_error:
                # Preserve the initiating error and the recovery failure as
                # distinct facts.  The public launcher reads only the safe
                # presence/count projection; no original paths enter UI copy.
                original_error.namespace_restore_error = restore_error
                original_error.namespace_restore_state = "failed"
                _timing_event(timing_event, "acl_restore", "failed")
        raise


def begin_namespace_lease(
    package_root: str | os.PathLike[str],
    *,
    timing_event: Callable[[str, str], object] | None = None,
) -> NamespaceLease:
    """Seal the elevated public package until the managed launcher exits."""

    program_data = os.environ.get("ProgramData")
    if not program_data:
        raise NamespaceGuardError("namespace_recovery_root_missing")
    return _begin_namespace_lease(
        package_root,
        locked_directory_sddl=_PRODUCTION_DIRECTORY_SDDL,
        locked_file_sddl=_PRODUCTION_FILE_SDDL,
        expected_owner=_ADMINISTRATORS_SID,
        recovery_root=Path(program_data)
        / "BidKing"
        / "local-integrity-namespace-recovery",
        require_elevated=True,
        secure_recovery=True,
        timing_event=timing_event,
    )
