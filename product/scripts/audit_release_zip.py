# -*- coding: utf-8 -*-
"""Fail-closed privacy and bytecode audit for public portable ZIPs."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import importlib.util
import json
import marshal
import os
import re
import stat
import struct
import subprocess
import sys
import types
import unicodedata
import zipfile
import zlib
from pathlib import Path, PurePosixPath


_NATIVE_HARDENING_PATH = Path(__file__).with_name("native_release_hardening.py")
_NATIVE_HARDENING_SPEC = importlib.util.spec_from_file_location(
    "bidking_native_release_hardening", _NATIVE_HARDENING_PATH
)
if _NATIVE_HARDENING_SPEC is None or _NATIVE_HARDENING_SPEC.loader is None:
    raise RuntimeError("native release hardening module unavailable")
_NATIVE_HARDENING = importlib.util.module_from_spec(_NATIVE_HARDENING_SPEC)
_NATIVE_HARDENING_SPEC.loader.exec_module(_NATIVE_HARDENING)


PROJECT_PREFIXES = ("hero_ref/", "src/", "scripts/")
RUNTIME_PREFIX = "runtime/python/"
BUILD_AUDIT_RECEIPT_SCHEMA = "bidking_release_build_audit_receipt_v5"
NATIVE_BUILD_AUDIT_RECEIPT_SCHEMA = "bidking_release_build_audit_receipt_v4"
SIGNED_BUILD_AUDIT_RECEIPT_SCHEMA = "bidking_release_build_audit_receipt_v3"
PREVIOUS_BUILD_AUDIT_RECEIPT_SCHEMA = "bidking_release_build_audit_receipt_v2"
LEGACY_BUILD_AUDIT_RECEIPT_SCHEMA = "bidking_release_build_audit_receipt_v1"
LEGACY_BUILD_AUDIT_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "receipt_version",
        "zip_name",
        "zip_bytes",
        "zip_sha256",
        "source_commit",
        "source_branch",
        "product_profile",
        "encrypted",
        "protection_manifest",
        "release_contract",
        "runtime_receipt",
        "runtime_approval",
        "bkenc_receipt",
        "expected_manifest_fields",
        "trusted_assets",
        "forbidden_path_sources",
    }
)
PREVIOUS_BUILD_AUDIT_RECEIPT_FIELDS = LEGACY_BUILD_AUDIT_RECEIPT_FIELDS | {
    "local_integrity_contract",
    "local_integrity",
}
SIGNED_BUILD_AUDIT_RECEIPT_FIELDS = PREVIOUS_BUILD_AUDIT_RECEIPT_FIELDS | {
    "native_binary_contract",
    "native_signing_policy",
    "native_binary_receipt",
}
NATIVE_BUILD_AUDIT_RECEIPT_FIELDS = PREVIOUS_BUILD_AUDIT_RECEIPT_FIELDS | {
    "native_binary_contract",
    "native_binary_receipt",
}
BUILD_AUDIT_RECEIPT_FIELDS = NATIVE_BUILD_AUDIT_RECEIPT_FIELDS | {
    "release_availability",
}
BUILD_AUDIT_FILE_IDENTITY_FIELDS = frozenset({"path", "sha256"})
BUILD_AUDIT_ADJACENT_IDENTITY_FIELDS = frozenset({"name", "sha256"})
BUILD_AUDIT_TRUSTED_ASSET_FIELDS = frozenset({"prefix", "path", "sha256"})
BUILD_AUDIT_LOCAL_INTEGRITY_FIELDS = frozenset(
    {
        "manifest_name",
        "manifest_sha256",
        "signature_name",
        "public_key_b64",
        "public_key_sha256",
        "entry_count",
        "native_gate_verified",
    }
)
RELEASE_AVAILABILITY_FIELDS = frozenset(
    {
        "schema",
        "receipt_version",
        "status",
        "captures",
        "stage",
        "extracted_zip",
        "policy_census",
        "route_scope",
        "chains",
    }
)
RELEASE_AVAILABILITY_PACKAGE_FIELDS = frozenset({"identity_sha256", "verdict"})
RELEASE_AVAILABILITY_CAPTURE_FIELDS = frozenset({"bytes", "sha256", "verdict"})
RELEASE_AVAILABILITY_CHAIN_FIELDS = frozenset(
    {
        "accepted_scheduled_snapshot",
        "summary_api_ui",
        "current_authority_2409",
    }
)
RELEASE_AVAILABILITY_ROUTE_COUNT_FIELDS = frozenset(
    {
        "changed_route_count",
        "new_only_count",
        "legacy_only_count",
        "forbidden_count",
        "current_activity_alias_sampleable_count",
        "ranked_production_unsupported_count",
    }
)
RELEASE_AVAILABILITY_ROUTE_HASH_FIELDS = frozenset(
    {
        "changed_route_ids_sha256",
        "new_only_ids_sha256",
        "legacy_only_ids_sha256",
        "forbidden_ids_sha256",
        "policy_root_ids_sha256",
        "current_activity_alias_ids_sha256",
        "ranked_production_unsupported_ids_sha256",
    }
)
RELEASE_AVAILABILITY_ROUTE_FIELDS = (
    RELEASE_AVAILABILITY_ROUTE_COUNT_FIELDS
    | RELEASE_AVAILABILITY_ROUTE_HASH_FIELDS
)
EXPECTED_RELEASE_AVAILABILITY_ROUTE_SCOPE = {
    "changed_route_count": 16,
    "changed_route_ids_sha256": (
        "f43e639dbbdeb3d855cce7aa4eb21a94a8923ca21ef49f3d944e8496a89adbec"
    ),
    "new_only_count": 42,
    "new_only_ids_sha256": (
        "493adee43dcb7af1214ee561b971f679b9823859b82d2393d67da9082482fb98"
    ),
    "legacy_only_count": 8,
    "legacy_only_ids_sha256": (
        "ff51a78e43e1e3af45d851b8d2f7077a2dbd761cfaf25824824ae43ec37515a7"
    ),
    "forbidden_count": 66,
    "forbidden_ids_sha256": (
        "53db1aad267ce4b33f05e4b1fbc2fa6ce24576681f385d1a87ec369c9829f32a"
    ),
    "policy_root_ids_sha256": (
        "228cc1f5ef22b92c118e6ce6564615eb581767a3e426677503b1b2a3d77d3f64"
    ),
    "current_activity_alias_sampleable_count": 20,
    "current_activity_alias_ids_sha256": (
        "ba37e047d27d9793961abe16268a1bb769e96a97457b7cf06e36879e50cf5d6e"
    ),
    "ranked_production_unsupported_count": 0,
    "ranked_production_unsupported_ids_sha256": (
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    ),
}
EXPECTED_RELEASE_AVAILABILITY_CENSUS = {
    "policy_roots": 101,
    "direct_root": {"complete": 23, "degraded": 58, "sampleable": 81,
                    "missing": 20, "empty": 0, "unsupported": 20,
                    "unsupported_ids_sha256": "31e3ab6f0e998e13539d94c1c0df977781c4d86cff819b41f8221edd1c9c920c"},
    "source_map": {"complete": 23, "degraded": 78, "sampleable": 101,
                   "missing": 0, "empty": 0, "unsupported": 0,
                   "unsupported_ids_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},
}
CURRENT_MODEL_RELEASE_CENSUS = {
    "policy_roots": 112,
    **{layer: {"complete": 112, "degraded": 0, "sampleable": 112,
               "missing": 0, "empty": 0, "unsupported": 0,
               "unsupported_ids_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}
       for layer in ("direct_root", "source_map")},
}
CURRENT_MODEL_RELEASE_ROUTES = {
    **EXPECTED_RELEASE_AVAILABILITY_ROUTE_SCOPE,
    "policy_root_ids_sha256": "78bab49717c2a3884ea49e8452cc779544ce4ee8bad5dce49e513e5bbfb4c99d",
}
CURRENT_MODEL_RELEASE_AUTHORITY = {
    "map_id": 2409,
    "canonical_input_sha256": "8407441f9e70e8ba4be28dec8e570252806da61c7ca3b6696a416e21ef78d87d",
    "package_generation": "76b7ca-2026-10-08-723",
    "distribution_authority": "modeled_not_official_drop",
    "scope_map_count": 112,
    "scope_map_ids_sha256": "78bab49717c2a3884ea49e8452cc779544ce4ee8bad5dce49e513e5bbfb4c99d",
    "runtime_open_claim": False,
    "historical_capture_scope": "compatibility_replay_not_current_generation_live",
}


def _availability_uses_current_model(availability: dict[str, object]) -> bool:
    authority = availability.get("current_authority")
    legacy = {key: CURRENT_MODEL_RELEASE_AUTHORITY[key] for key in ("map_id", "canonical_input_sha256")}
    for expected, current in ((CURRENT_MODEL_RELEASE_AUTHORITY, True), (legacy, False)):
        if (type(authority) is dict and set(authority) == set(expected)
                and all(type(authority[key]) is type(value) and authority[key] == value
                        for key, value in expected.items())):
            return current
    raise ValueError("release_availability_authority_contract_invalid")


def _availability_census_is_exact(value: object, *, current_model: bool) -> bool:
    expected = CURRENT_MODEL_RELEASE_CENSUS if current_model else EXPECTED_RELEASE_AVAILABILITY_CENSUS
    if (type(value) is not dict or set(value) != set(expected)
            or type(value.get("policy_roots")) is not int or value["policy_roots"] != expected["policy_roots"]):
        return False
    return all(type(value[layer]) is dict and set(value[layer]) == set(expected[layer])
               and all(type(value[layer][key]) is type(frozen) and value[layer][key] == frozen
                       for key, frozen in expected[layer].items())
               for layer in ("direct_root", "source_map"))


LAUNCHER_WRAPPER_MEMBERS = ("其他启动方式/启动.bat", "其他启动方式/start.bat")
LAUNCHER_WRAPPER_BYTES = (
    b"@echo off\r\n"
    b"setlocal\r\n"
    b'if not exist "%~dp0..\\BidKingLive.exe" exit /b 1\r\n'
    b'if exist "%~dp0..\\BidKingLive.exe\\*" exit /b 1\r\n'
    b'start "" "%~dp0..\\BidKingLive.exe"\r\n'
    b"exit /b %errorlevel%\r\n"
)
LAUNCHER_WRAPPER_SHA256 = hashlib.sha256(LAUNCHER_WRAPPER_BYTES).hexdigest()
BUILD_AUDIT_NATIVE_BINARY_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "receipt_version",
        "distribution",
        "contract_sha256",
        "member_count",
        "members",
    }
)
BUILD_AUDIT_FORBIDDEN_PATH_SOURCES = frozenset(
    {"source_root", "current_user_profile"}
)
FORBIDDEN_PLAINTEXT_WSS_MEMBERS = frozenset(
    {
        "scripts/_local_compat_core.py",
        "scripts/bidking_wss_capture_core.py",
        "scripts/mitm_bidking_wss_addon.py",
        "scripts/run_wss_live_monitor.py",
    }
)
FORBIDDEN_PLAINTEXT_LAUNCHER_MEMBERS = frozenset(
    {
        "scripts/live_process_discovery.ps1",
        "scripts/local_compat_launcher.py",
    }
)
BUNDLED_NPCAP_INSTALLER_MEMBER = "vendor/npcap-installer.exe"
PUBLIC_LAUNCHER_MEMBER = "scripts/start_live_npcap_overlay.ps1"
PUBLIC_DIAGNOSTICS_MEMBER = "scripts/collect_diagnostics.ps1"
PROTECTED_ADDON_BOOTSTRAP_MEMBER = "scripts/local_compat_addon.py"
PROTECTED_ADDON_LOADER_SUFFIX = "_local_compat_addon_loader.pyd"
PROTECTED_CLIENT_LAUNCHER_PREFIX = "scripts/_client_launcher"
NATIVE_KERNEL_PREFIX = "src/bidking_kernel"
FORBIDDEN_REVERSIBLE_LIFECYCLE_TERMS = (
    b"_powershell_payload_b64",
    b"_diagnostics_payload_b64",
    b"_decode_payload",
    b"_run_powershell_source",
)
REQUIRED_CLIENT_LAUNCHER_TERMS = (
    b"diagnostics_receipt_main",
)
REQUIRED_NATIVE_LIFECYCLE_TERMS = (
    b"client_lifecycle_contract",
    b"run_client_lifecycle",
    b"client_lifecycle_request_rejected",
    b"client_lifecycle_payload_invalid",
)
PRODUCT_PROFILE_MANIFEST_MEMBER = "build_manifest.txt"
ACTIVATION_SERVICE_ENDPOINTS_MEMBER = "activation_service_endpoints.json"
ACTIVATION_HTTP_E2EE_MEMBER = "activation_http_e2ee.json"
CLIENT_PROTECTION_SCHEMA = "bidking_client_protection_v2"
RELEASE_ZIP_CONTRACT_SCHEMA = "bidking_release_zip_contract_v1"
RUNTIME_INVENTORY_RECEIPT_SCHEMA = "bidking_runtime_inventory_receipt_v1"
RUNTIME_INVENTORY_APPROVAL_SCHEMA = "bidking_runtime_inventory_approval_v1"
BKENC_AUTHENTICATION_RECEIPT_SCHEMA = (
    "bidking_bkenc_authentication_receipt_v1"
)
BKENC1_MAGIC = b"BKENC1\n"
BKENC2_MAGIC = b"BKENC2\n"
BKENC1_NONCE_BYTES = 12
BKENC1_TAG_BYTES = 16
BKENC_KEY_BYTES = 32
BKENC1_MEMBER_AAD_SCHEME = "bidking_bkenc1_member_path_v1"
BKENC_AUTHENTICATION_AAD_SCHEME = "bidking_bkenc_member_path_by_format_v1"
BKENC1_MEMBER_AAD_DOMAIN = b"bidking-bkenc1-member-path-v1\x00"
BKENC2_MEMBER_AAD_DOMAIN = b"bidking-bkenc2-gzip-member-path-v1\x00"
BKENC2_GZIP_FORMAT = "bkenc2-gzip"
BKENC2_LENGTH = struct.Struct(">Q")
BKENC_RECEIPT_HMAC_DOMAIN = b"bidking-bkenc-authentication-receipt-v1\x00"
BKENC_RECEIPT_KEY_DOMAIN = b"bidking-bkenc-authentication-receipt-key-v1\x00"
RUNTIME_INVENTORY_PREFIX = "runtime/python/"
BKENC_AUTHENTICATION_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "receipt_version",
        "contract_sha256",
        "member_count",
        "member_set_sha256",
        "entries",
        "authentication_tag",
    }
)
BKENC_AUTHENTICATION_ENTRY_FIELDS = frozenset(
    {
        "path",
        "envelope_size",
        "envelope_sha256",
        "compression_method",
        "compressed_size",
        "compressed_sha256",
        "date_time",
        "create_system",
        "flag_bits",
        "external_attr",
    }
)
RUNTIME_INVENTORY_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "receipt_version",
        "prefix",
        "file_count",
        "directory_count",
        "total_bytes",
        "inventory_sha256",
        "approval_sha256",
        "entries",
    }
)
RUNTIME_INVENTORY_APPROVAL_FIELDS = frozenset(
    {
        "schema",
        "approval_version",
        "prefix",
        "file_count",
        "directory_count",
        "total_bytes",
        "inventory_sha256",
    }
)
RUNTIME_INVENTORY_ENTRY_FIELDS = frozenset(
    {"path", "type", "size", "sha256", "compression_method"}
)
RELEASE_ARCHIVE_LIMITS = {
    "max_entries": 10_000,
    "max_member_uncompressed_bytes": 128 * 1024 * 1024,
    "max_total_uncompressed_bytes": 512 * 1024 * 1024,
    "max_compression_ratio": 200,
    "max_member_path_chars": 240,
    "allowed_compression_methods": [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED],
}
_CLIENT_PROTECTION_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_CLIENT_PROTECTION_ABI_TAG_RE = re.compile(
    r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+\Z"
)
_CLIENT_PROTECTION_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_DOS_83_ALIAS_RE = re.compile(r"~[1-9][0-9]*\Z")
_WINDOWS_FORBIDDEN_MEMBER_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_SUPERSCRIPT_DIGITS = str.maketrans({"¹": "1", "²": "2", "³": "3"})
_WINDOWS_RESERVED_MEMBER_BASENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
DISTRIBUTION_TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".pyw",
        ".ps1",
        ".bat",
        ".cmd",
        ".txt",
        ".md",
        ".json",
        ".html",
        ".js",
        ".css",
        ".toml",
        ".ini",
        ".cfg",
        ".yaml",
        ".yml",
        ".xml",
        ".csv",
    }
)
NETWORK_DIAGNOSTIC_FORBIDDEN_TEXT = (
    "¥12.88",
    "¥11.11",
    "¥28.88",
    "¥68.88",
    "¥19.99",
    "¥39.99",
    "¥89.99",
    "90/365 天有效期内可用",
    "打开「购买与续费」文件夹",
    "点此输入/购买 token",
)
_TRIAL_DURATION_LAYOUT_CHARS = frozenset("[]()【】（）:：")
_TRIAL_DURATION_IGNORABLE_CATEGORY_GROUPS = frozenset({"C", "M", "Z"})
# Unicode 15.1 DerivedCoreProperties.txt, Default_Ignorable_Code_Point.
# Keep the complete 4,174-code-point property explicit and reviewable because
# unicodedata does not expose derived properties.
_TRIAL_DURATION_DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
FORBIDDEN_PUBLIC_LAUNCHER_TERMS = (
    b"release.bid-king.cn",
    b"run_wss_live_monitor",
    b"mitm_bidking_wss_addon",
    b"bidking_wss_capture_core",
    b"bidking_disable_wss_capture",
    b"serverport",
    b"mitmdump",
    b"npcapmonitor",
    b"wssaddon",
)
FORBIDDEN_PUBLIC_DIAGNOSTICS_TERMS = (
    b"release.bid-king.cn",
    b"run_wss_live_monitor",
    b"mitm_bidking_wss_addon",
    b"bidking_wss_capture_core",
    b"capture_source_status",
    b"serverport",
    b"mitmdump",
    b"windivert",
    b"npcap",
    b"0x2d",
)
FORBIDDEN_PROTECTED_ADDON_TERMS = (
    b"release.bid-king.cn",
    b"class bidkingwssaddon",
    b"allowed_send_message_ids",
    b"bidking_wss_core_payload",
    b"bidking_wss_addon_payload",
    b"base64",
    b"gzip",
)


def _is_exact_type(value: object, expected: type[object]) -> bool:
    return type(value) is expected  # noqa: E721 - bool/subclasses must fail closed


def _release_availability_route_contract_is_exact(value: object, *, current_model: bool = False) -> bool:
    if (
        not _is_exact_type(value, dict)
        or set(value) != RELEASE_AVAILABILITY_ROUTE_FIELDS
        or any(
            not _is_exact_type(value.get(key), int)
            for key in RELEASE_AVAILABILITY_ROUTE_COUNT_FIELDS
        )
        or any(
            not _is_exact_type(value.get(key), str)
            or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
            for key in RELEASE_AVAILABILITY_ROUTE_HASH_FIELDS
        )
    ):
        return False
    return value == (CURRENT_MODEL_RELEASE_ROUTES if current_model else EXPECTED_RELEASE_AVAILABILITY_ROUTE_SCOPE)


def _release_archive_limits_are_exact(value: object) -> bool:
    if not _is_exact_type(value, dict) or set(value) != set(RELEASE_ARCHIVE_LIMITS):
        return False
    for key, expected in RELEASE_ARCHIVE_LIMITS.items():
        actual = value[key]
        if _is_exact_type(expected, list):
            if (
                not _is_exact_type(actual, list)
                or any(not _is_exact_type(item, int) for item in actual)
                or actual != expected
            ):
                return False
        elif not _is_exact_type(actual, int) or actual != expected:
            return False
    return True


def _normalized_member(raw: str) -> str | None:
    name = raw.replace("\\", "/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        return None
    return path.as_posix()


def _canonical_release_member_name(raw: object, *, is_dir: bool) -> str | None:
    """Return one exact POSIX relative identity without normalizing aliases."""
    if type(raw) is not str or not raw or "\x00" in raw:
        return None
    if raw != unicodedata.normalize("NFC", raw):
        return None
    if "\\" in raw or any(ord(char) < 0x20 for char in raw):
        return None
    if raw.startswith("/") or "//" in raw:
        return None
    if is_dir:
        if not raw.endswith("/"):
            return None
        candidate = raw[:-1]
    else:
        if raw.endswith("/"):
            return None
        candidate = raw
    parts = candidate.split("/")
    if (
        not candidate
        or any(part in {"", ".", ".."} for part in parts)
        or any(_windows_release_member_segment_key(part) is None for part in parts)
        or PurePosixPath(candidate).as_posix() != candidate
    ):
        return None
    return candidate


def _windows_release_member_segment_key(segment: str) -> str | None:
    """Project one ZIP segment to its case-insensitive Win32 disk identity."""
    if (
        not segment
        or segment.endswith((" ", "."))
        or any(char in _WINDOWS_FORBIDDEN_MEMBER_CHARS for char in segment)
    ):
        return None
    basename = segment.split(".", 1)[0].rstrip(" .")
    if _WINDOWS_DOS_83_ALIAS_RE.search(basename):
        return None
    basename = basename.translate(_WINDOWS_RESERVED_SUPERSCRIPT_DIGITS).upper()
    if basename in _WINDOWS_RESERVED_MEMBER_BASENAMES:
        return None
    return unicodedata.normalize("NFC", segment.rstrip(" .")).casefold()


def _windows_release_member_key(name: str) -> tuple[str, ...] | None:
    """Return the Win32 extraction key shared by files and directories."""
    projected = tuple(
        _windows_release_member_segment_key(segment)
        for segment in name.split("/")
    )
    if any(segment is None for segment in projected):
        return None
    return tuple(segment for segment in projected if segment is not None)


def _archive_structure_issues(archive: zipfile.ZipFile) -> list[str]:
    """Reject dangerous entry metadata before any member payload is read."""
    infos = archive.infolist()
    if len(infos) > RELEASE_ARCHIVE_LIMITS["max_entries"]:
        return ["release_archive_limits_exceeded"]

    allowed_compression = set(RELEASE_ARCHIVE_LIMITS["allowed_compression_methods"])
    total_uncompressed = 0
    issues: list[str] = []
    for info in infos:
        raw_name = info.orig_filename
        if (
            not _is_exact_type(raw_name, str)
            or len(raw_name) > RELEASE_ARCHIVE_LIMITS["max_member_path_chars"]
        ):
            issues.append("release_member_path_invalid")

        unix_type = (info.external_attr >> 16) & 0o170000
        windows_attributes = info.external_attr & 0xFFFF
        if (
            unix_type not in {0, stat.S_IFREG, stat.S_IFDIR}
            or unix_type == stat.S_IFDIR
            and not info.is_dir()
            or windows_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            issues.append("release_member_type_invalid")

        if (
            info.flag_bits & 0x1
            or info.compress_type not in allowed_compression
            or info.file_size < 0
            or info.compress_size < 0
            or (
                info.file_size > 0
                and (
                    info.compress_size == 0
                    or info.file_size / info.compress_size
                    > RELEASE_ARCHIVE_LIMITS["max_compression_ratio"]
                )
            )
        ):
            issues.append("release_member_compression_invalid")

        total_uncompressed += max(0, info.file_size)
        if (
            info.file_size
            > RELEASE_ARCHIVE_LIMITS["max_member_uncompressed_bytes"]
            or total_uncompressed
            > RELEASE_ARCHIVE_LIMITS["max_total_uncompressed_bytes"]
        ):
            issues.append("release_archive_limits_exceeded")

    return list(dict.fromkeys(issues))


_RELEASE_MEMBER_IDENTITY_ISSUES = frozenset(
    {
        "release_archive_limits_exceeded",
        "release_member_path_invalid",
        "release_member_type_invalid",
        "release_member_compression_invalid",
        "release_member_duplicate",
        "release_member_casefold_collision",
        "release_member_windows_collision",
    }
)


def _release_member_identity_failed(issues: list[str]) -> bool:
    return any(issue in _RELEASE_MEMBER_IDENTITY_ISSUES for issue in issues)


def _strict_release_member_index(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, list[zipfile.ZipInfo]], dict[str, str], list[str]]:
    """Index raw ZIP members only after proving one canonical path per entry."""
    members: dict[str, list[zipfile.ZipInfo]] = {}
    casefolded: dict[str, str] = {}
    windows_names: dict[tuple[str, ...], str] = {}
    windows_files: set[tuple[str, ...]] = set()
    windows_directories: set[tuple[str, ...]] = set()
    issues: list[str] = []
    all_names: set[str] = set()
    for info in archive.infolist():
        raw_name = info.orig_filename
        name = _canonical_release_member_name(raw_name, is_dir=info.is_dir())
        if name is None:
            issues.append("release_member_path_invalid")
            continue
        folded = unicodedata.normalize("NFC", name.casefold())
        windows_key = _windows_release_member_key(name)
        if windows_key is None:
            issues.append("release_member_path_invalid")
            continue
        duplicate = name in all_names
        casefold_collision = folded in casefolded
        windows_ancestors = tuple(
            windows_key[:depth] for depth in range(1, len(windows_key))
        )
        windows_collision = (
            windows_key in windows_names
            or any(ancestor in windows_files for ancestor in windows_ancestors)
            or (
                windows_key in windows_files
                if info.is_dir()
                else windows_key in windows_directories
            )
        )
        if duplicate:
            issues.append("release_member_duplicate")
        if casefold_collision:
            issues.append("release_member_casefold_collision")
        if windows_collision:
            issues.append("release_member_windows_collision")
        if duplicate or casefold_collision or windows_collision:
            continue
        all_names.add(name)
        casefolded[folded] = name
        windows_names[windows_key] = name
        windows_directories.update(windows_ancestors)
        if info.is_dir():
            windows_directories.add(windows_key)
        else:
            windows_files.add(windows_key)
            members[name] = [info]
    return members, casefolded, issues


def _forbidden_needles(paths: tuple[str, ...]) -> tuple[bytes, ...]:
    values: set[bytes] = set()
    for raw in paths:
        text = str(raw or "").strip().rstrip("\\/")
        if not text:
            continue
        for variant in (text, text.replace("\\", "/"), text.replace("/", "\\")):
            values.add(variant.encode("utf-8", errors="strict").lower())
            values.add(variant.encode("utf-16le", errors="strict").lower())
    return tuple(sorted(values, key=len, reverse=True))


_WINDOWS_USER_PATH_RE = re.compile(
    r"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?![<\[])[^\\/\x00\r\n\t\"']+"
)


def _contains_windows_user_path(data: bytes) -> bool:
    """Detect concrete user paths without flagging redaction regex literals.

    Diagnostic sanitizers intentionally contain ``C:\\Users\\<user>`` and regex
    source such as ``C:\\Users\\[^...``.  Neither contains a private username.
    Concrete paths remain blocked in both UTF-8/ASCII and UTF-16LE content.
    """
    candidates = [data.decode("utf-8", errors="ignore")]
    if b"\x00" in data:
        candidates.append(data.decode("utf-16le", errors="ignore"))
        candidates.append(data[1:].decode("utf-16le", errors="ignore"))
    return any(_WINDOWS_USER_PATH_RE.search(text) for text in candidates)


def audit_private_build_artifact(
    path: Path,
    *,
    forbidden_paths: tuple[str, ...] = (),
) -> list[str]:
    """Scan one build artifact without exposing its path or matched private bytes."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"artifact_unreadable: {type(exc).__name__}"]
    lowered = data.lower()
    needles = _forbidden_needles(forbidden_paths)
    if _contains_windows_user_path(data) or any(
        needle and needle in lowered for needle in needles
    ):
        return ["private_build_path"]
    return []


def _runtime_bytecode_issues(
    data: bytes,
    name: str,
    needles: tuple[bytes, ...],
) -> list[str]:
    """Inspect runtime bytecode filenames without exposing matched private paths."""
    label = f"runtime_bytecode_unreadable: {name}"
    if len(data) < 16 or data[:4] != importlib.util.MAGIC_NUMBER:
        return [label]
    flags = int.from_bytes(data[4:8], "little")
    if flags & ~0b11:
        return [label]
    try:
        root = marshal.loads(data[16:])
    except (EOFError, TypeError, ValueError):
        return [label]
    if not isinstance(root, types.CodeType):
        return [label]

    stack = [root]
    seen: set[int] = set()
    while stack:
        code = stack.pop()
        identity = id(code)
        if identity in seen:
            continue
        seen.add(identity)
        filename = code.co_filename
        try:
            encoded = (
                filename.encode("utf-8", errors="surrogatepass").lower(),
                filename.encode("utf-16le", errors="surrogatepass").lower(),
            )
        except (AttributeError, UnicodeError):
            return [label]
        if _contains_windows_user_path(encoded[0]) or any(
            needle and needle in payload
            for payload in encoded
            for needle in needles
        ):
            return [f"runtime_bytecode_private_path: {name}"]
        stack.extend(
            value for value in code.co_consts if isinstance(value, types.CodeType)
        )
    return []


def _distribution_text_occurrences(data: bytes, text: str) -> int:
    """Count an exact public phrase across supported distribution encodings."""
    encoded = {
        text.encode("utf-8"),
        text.encode("utf-16le"),
        text.encode("utf-16be"),
        text.encode("gb18030"),
    }
    return max(data.count(needle) for needle in encoded)


def _manifest_fields(archive: zipfile.ZipFile) -> dict[str, str] | None:
    manifests = [
        info
        for info in archive.infolist()
        if not info.is_dir()
        and (_normalized_member(info.filename) or "").casefold()
        == PRODUCT_PROFILE_MANIFEST_MEMBER
    ]
    if len(manifests) != 1:
        return None
    try:
        raw = archive.read(manifests[0])
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return None
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        fields: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip():
                fields[key.strip().casefold()] = value.strip()
        if fields:
            return fields
    return None


def _canonical_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def _strict_build_manifest(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, str] | None, list[str]]:
    """Read one unambiguous BUILD_MANIFEST without trusting duplicate fields."""
    manifests = [
        info
        for info in archive.infolist()
        if not info.is_dir()
        and (_normalized_member(info.filename) or "").casefold()
        == "build_manifest.txt"
    ]
    if len(manifests) != 1:
        return None, ["release_manifest_invalid"]
    try:
        raw = archive.read(manifests[0])
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return None, ["release_manifest_invalid"]
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        fields: dict[str, str] = {}
        display_keys: dict[str, str] = {}
        invalid = False
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator or not key.strip():
                continue
            normalized = key.strip().casefold()
            if normalized in fields:
                invalid = True
                break
            fields[normalized] = value.strip()
            display_keys[normalized] = key.strip()
        if fields and not invalid:
            return fields, []
    return None, ["release_manifest_invalid"]


def _activation_service_endpoint_identity_issues(
    archive: zipfile.ZipFile,
    members: dict[str, list[zipfile.ZipInfo]],
    fields: dict[str, str],
) -> list[str]:
    """Bind the exact packaged endpoint generation to BUILD_MANIFEST."""
    infos = members.get(ACTIVATION_SERVICE_ENDPOINTS_MEMBER, ())
    if len(infos) != 1:
        return ["release_activation_endpoint_config_missing"]
    try:
        raw = archive.read(infos[0])
        source_root = Path(__file__).resolve().parent.parent / "src"
        if str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))
        from bidking_lab.live.version_check import (  # noqa: PLC0415
            parse_activation_service_endpoint_config_bytes,
        )

        config = parse_activation_service_endpoint_config_bytes(raw)
    except Exception:
        return ["release_activation_endpoint_config_invalid"]
    issues: list[str] = []
    if fields.get("activationserviceendpointssha256") != hashlib.sha256(raw).hexdigest():
        issues.append("release_activation_endpoint_config_hash_mismatch")
    if fields.get("activationserviceendpointsgeneration") != str(config.generation):
        issues.append("release_activation_endpoint_config_generation_mismatch")
    expected_enabled = "True" if config.endpoint_3_enabled else "False"
    if fields.get("activationserviceendpoint3enabled") != expected_enabled:
        issues.append("release_activation_endpoint_config_enabled_mismatch")
    return issues


def _activation_http_e2ee_identity_issues(
    archive: zipfile.ZipFile,
    members: dict[str, list[zipfile.ZipInfo]],
    fields: dict[str, str],
) -> list[str]:
    """Bind the exact emergency HTTP carrier config to BUILD_MANIFEST."""
    infos = members.get(ACTIVATION_HTTP_E2EE_MEMBER, ())
    if len(infos) != 1:
        return ["release_activation_http_e2ee_config_missing"]
    try:
        raw = archive.read(infos[0])
        source_root = Path(__file__).resolve().parent.parent / "src"
        if str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))
        from bidking_lab.secure.activation_http_e2ee import (  # noqa: PLC0415
            parse_config_bytes,
        )

        config = parse_config_bytes(raw)
    except Exception:
        return ["release_activation_http_e2ee_config_invalid"]
    issues: list[str] = []
    if fields.get("activationhttpe2eeconfigsha256") != hashlib.sha256(raw).hexdigest():
        issues.append("release_activation_http_e2ee_config_hash_mismatch")
    expected_enabled = "True" if config.enabled else "False"
    if fields.get("activationhttpe2eeenabled") != expected_enabled:
        issues.append("release_activation_http_e2ee_config_enabled_mismatch")
    expected_key_id = config.key_id if config.enabled else "disabled"
    if fields.get("activationhttpe2eekeyid") != expected_key_id:
        issues.append("release_activation_http_e2ee_config_key_mismatch")
    return issues


def _decode_distribution_text(data: bytes) -> str | None:
    """Decode public package copy from the supported Windows distribution forms."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeError:
            return None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    return None


def _is_default_ignorable_code_point(char: str) -> bool:
    code_point = ord(char)
    return any(
        start <= code_point <= end
        for start, end in _TRIAL_DURATION_DEFAULT_IGNORABLE_RANGES
    )


def _normalize_trial_duration_copy(value: str) -> str:
    """Ignore bounded Unicode layout variants around an explicit trial duration."""
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        char
        for char in normalized
        if not (
            _is_default_ignorable_code_point(char)
            or unicodedata.category(char)[0]
            in _TRIAL_DURATION_IGNORABLE_CATEGORY_GROUPS
            or char in _TRIAL_DURATION_LAYOUT_CHARS
        )
    )


def _runtime_dependency_rows(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, list[str]], bool]:
    """Project installed distribution identities from packaged METADATA files."""
    found: dict[str, list[str]] = {}
    invalid = False
    suffix = ".dist-info/metadata"
    prefix = "runtime/python/lib/site-packages/"
    for info in archive.infolist():
        name = (_normalized_member(info.filename) or "").casefold()
        if info.is_dir() or not name.startswith(prefix) or not name.endswith(suffix):
            continue
        try:
            text = archive.read(info).decode("utf-8")
        except (OSError, RuntimeError, UnicodeError, zipfile.BadZipFile):
            invalid = True
            continue
        metadata: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            normalized = key.strip().casefold()
            if separator and normalized in {"name", "version"} and normalized not in metadata:
                metadata[normalized] = value.strip()
        if not metadata.get("name") or not metadata.get("version"):
            invalid = True
            continue
        package = _canonical_distribution_name(metadata["name"])
        found.setdefault(package, []).append(metadata["version"])
    return found, invalid


def _runtime_inventory_sha256(entries: list[dict[str, object]]) -> str:
    serialized = "".join(
        (
            f"{entry['path']}|{entry['type']}|{entry['size']}|"
            f"{entry['sha256'] or '-'}|{entry['compression_method']}\n"
        )
        for entry in entries
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _path_has_reparse_semantics(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return True
    attributes = getattr(details, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _canonical_filesystem_entries(
    root: Path,
    *,
    prefix: str = "",
) -> list[tuple[str, Path, bool]]:
    base = Path(root)
    if not base.is_dir() or _path_has_reparse_semantics(base):
        raise ValueError("filesystem inventory root is unsafe")
    entries: list[tuple[str, Path, bool]] = []
    for directory, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        current = Path(directory)
        safe_directories: list[str] = []
        for name in dirnames:
            child = current / name
            if _path_has_reparse_semantics(child) or not child.is_dir():
                raise ValueError("filesystem inventory contains unsafe directory")
            relative = child.relative_to(base).as_posix()
            member = f"{prefix}{relative}/"
            if _canonical_release_member_name(member, is_dir=True) is None:
                raise ValueError("filesystem inventory contains unsafe path")
            entries.append((member, child, True))
            safe_directories.append(name)
        dirnames[:] = safe_directories

        for name in filenames:
            child = current / name
            if _path_has_reparse_semantics(child) or not child.is_file():
                raise ValueError("filesystem inventory contains unsafe file")
            relative = child.relative_to(base).as_posix()
            member = f"{prefix}{relative}"
            if _canonical_release_member_name(member, is_dir=False) != member:
                raise ValueError("filesystem inventory contains unsafe path")
            entries.append((member, child, False))
    entries.sort(key=lambda entry: entry[0])
    return entries


def _runtime_inventory_entries_from_filesystem(
    runtime_root: Path,
) -> list[dict[str, object]]:
    if not RUNTIME_INVENTORY_PREFIX.endswith("/"):
        raise ValueError("runtime inventory root is unsafe")

    entries: list[dict[str, object]] = [
        {
            "path": RUNTIME_INVENTORY_PREFIX,
            "type": "directory",
            "size": 0,
            "sha256": None,
            "compression_method": zipfile.ZIP_STORED,
        }
    ]
    for member, child, is_directory in _canonical_filesystem_entries(
        Path(runtime_root),
        prefix=RUNTIME_INVENTORY_PREFIX,
    ):
        if is_directory:
            entries.append(
                {
                    "path": member,
                    "type": "directory",
                    "size": 0,
                    "sha256": None,
                    "compression_method": zipfile.ZIP_STORED,
                }
            )
        else:
            data = child.read_bytes()
            entries.append(
                {
                    "path": member,
                    "type": "file",
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "compression_method": (
                        zipfile.ZIP_DEFLATED if data else zipfile.ZIP_STORED
                    ),
                }
            )
    return entries


def _runtime_inventory_summary(
    entries: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "prefix": RUNTIME_INVENTORY_PREFIX,
        "file_count": sum(entry["type"] == "file" for entry in entries),
        "directory_count": sum(entry["type"] == "directory" for entry in entries),
        "total_bytes": sum(int(entry["size"]) for entry in entries),
        "inventory_sha256": _runtime_inventory_sha256(entries),
        "entries": entries,
    }


def _load_runtime_inventory_approval(path: Path) -> tuple[bytes, dict[str, object]]:
    """Load the source-reviewed runtime identity; it never comes from Stage."""
    raw = Path(path).read_bytes()
    approval = json.loads(raw.decode("utf-8"))
    if (
        not _is_exact_type(approval, dict)
        or set(approval) != RUNTIME_INVENTORY_APPROVAL_FIELDS
        or approval.get("schema") != RUNTIME_INVENTORY_APPROVAL_SCHEMA
        or not _is_exact_type(approval.get("approval_version"), int)
        or approval["approval_version"] != 1
        or approval.get("prefix") != RUNTIME_INVENTORY_PREFIX
        or not _is_exact_type(approval.get("file_count"), int)
        or approval["file_count"] <= 0
        or not _is_exact_type(approval.get("directory_count"), int)
        or approval["directory_count"] <= 0
        or not _is_exact_type(approval.get("total_bytes"), int)
        or approval["total_bytes"] <= 0
        or not _is_exact_type(approval.get("inventory_sha256"), str)
        or _CLIENT_PROTECTION_SHA256_RE.fullmatch(approval["inventory_sha256"])
        is None
    ):
        raise ValueError("invalid runtime inventory approval")
    return raw, approval


def _runtime_inventory_approval_sha256(approval: dict[str, object]) -> str:
    canonical = json.dumps(
        approval,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _runtime_inventory_identity(
    entries: list[dict[str, object]],
) -> dict[str, object]:
    summary = _runtime_inventory_summary(entries)
    return {
        key: summary[key]
        for key in (
            "prefix",
            "file_count",
            "directory_count",
            "total_bytes",
            "inventory_sha256",
        )
    }


def write_runtime_inventory_receipt(
    runtime_root: Path,
    receipt_path: Path,
    approval_path: Path,
) -> None:
    """Freeze final Stage runtime only when it matches the reviewed source asset."""
    _approval_raw, approval = _load_runtime_inventory_approval(approval_path)
    entries = _runtime_inventory_entries_from_filesystem(runtime_root)
    if _runtime_inventory_identity(entries) != {
        key: approval[key]
        for key in (
            "prefix",
            "file_count",
            "directory_count",
            "total_bytes",
            "inventory_sha256",
        )
    }:
        raise ValueError("runtime inventory is not approved")

    payload = {
        "schema": RUNTIME_INVENTORY_RECEIPT_SCHEMA,
        "receipt_version": 1,
        "approval_sha256": _runtime_inventory_approval_sha256(approval),
        **_runtime_inventory_summary(entries),
    }
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_canonical_release_zip(stage_root: Path, zip_path: Path) -> None:
    """Write one deterministic POSIX-name ZIP from a verified Stage tree."""
    root = Path(stage_root).resolve(strict=True)
    destination = Path(zip_path)
    destination_parent = destination.parent.resolve(strict=True)
    destination_resolved = destination_parent / destination.name
    try:
        destination_resolved.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("ZIP destination must stay outside Stage")

    entries = _canonical_filesystem_entries(root)
    if len(entries) > RELEASE_ARCHIVE_LIMITS["max_entries"]:
        raise ValueError("Stage entry limit exceeded")
    total_bytes = 0
    for member, child, is_directory in entries:
        if len(member) > RELEASE_ARCHIVE_LIMITS["max_member_path_chars"]:
            raise ValueError("Stage member path limit exceeded")
        if is_directory:
            continue
        size = child.stat().st_size
        total_bytes += size
        if (
            size > RELEASE_ARCHIVE_LIMITS["max_member_uncompressed_bytes"]
            or total_bytes > RELEASE_ARCHIVE_LIMITS["max_total_uncompressed_bytes"]
        ):
            raise ValueError("Stage size limit exceeded")

    with zipfile.ZipFile(
        destination_resolved,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for member, child, is_directory in entries:
            info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 0
            if is_directory:
                info.compress_type = zipfile.ZIP_STORED
                data = b""
            else:
                data = child.read_bytes()
                info.compress_type = (
                    zipfile.ZIP_DEFLATED if data else zipfile.ZIP_STORED
                )
            archive.writestr(info, data, compresslevel=9)


def _load_bkenc_key_file(path: Path) -> bytes:
    try:
        compact = b"".join(Path(path).read_bytes().split())
        key = base64.b64decode(compact, validate=True)
    except (OSError, ValueError) as exc:
        raise ValueError("invalid BKENC audit key") from exc
    if len(key) != BKENC_KEY_BYTES:
        raise ValueError("invalid BKENC audit key")
    return key


def _bkenc_contract_members(
    contract_path: Path,
) -> tuple[bytes, dict[str, tuple[str, bool]], bool]:
    raw = Path(contract_path).read_bytes()
    contract = json.loads(raw.decode("utf-8"))
    authentication = contract.get("bkenc_authentication")
    protection = contract.get("member_protection")
    if (
        type(contract) is not dict
        or type(authentication) is not dict
        or set(authentication)
        != {"envelope_aad", "receipt_required", "receipt_schema"}
        or authentication.get("envelope_aad")
        != BKENC_AUTHENTICATION_AAD_SCHEME
        or type(authentication.get("receipt_required")) is not bool
        or authentication.get("receipt_schema")
        != BKENC_AUTHENTICATION_RECEIPT_SCHEMA
        or type(protection) is not dict
    ):
        raise ValueError("invalid BKENC contract")

    members: dict[str, tuple[str, bool]] = {}
    folded: set[str] = set()
    for raw_name, rule in protection.items():
        if (
            type(raw_name) is not str
            or _canonical_release_member_name(raw_name, is_dir=False) != raw_name
            or type(rule) is not dict
            or set(rule) != {"mode", "required"}
            or rule.get("mode")
            not in {"bkenc1", BKENC2_GZIP_FORMAT, "plaintext"}
            or type(rule.get("required")) is not bool
            or raw_name.casefold() in folded
        ):
            raise ValueError("invalid BKENC contract")
        folded.add(raw_name.casefold())
        if rule["mode"] in {"bkenc1", BKENC2_GZIP_FORMAT}:
            members[raw_name] = (rule["mode"], rule["required"])
    if not members:
        raise ValueError("empty BKENC contract")
    return raw, members, authentication["receipt_required"]


def _bkenc1_member_aad(member_path: str) -> bytes:
    if _canonical_release_member_name(member_path, is_dir=False) != member_path:
        raise ValueError("invalid BKENC member path")
    return BKENC1_MEMBER_AAD_DOMAIN + member_path.encode("utf-8")


def _bkenc2_member_aad(member_path: str) -> bytes:
    if _canonical_release_member_name(member_path, is_dir=False) != member_path:
        raise ValueError("invalid BKENC member path")
    return BKENC2_MEMBER_AAD_DOMAIN + member_path.encode("utf-8")


def _decompress_bkenc2(payload: bytes) -> bytes:
    if len(payload) < BKENC2_LENGTH.size:
        raise ValueError("invalid BKENC2 compressed payload")
    expected = BKENC2_LENGTH.unpack(payload[: BKENC2_LENGTH.size])[0]
    if expected > RELEASE_ARCHIVE_LIMITS["max_member_uncompressed_bytes"]:
        raise ValueError("invalid BKENC2 plaintext size")
    decoder = zlib.decompressobj(wbits=31)
    try:
        plaintext = decoder.decompress(payload[BKENC2_LENGTH.size :], expected + 1)
    except zlib.error as exc:
        raise ValueError("invalid BKENC2 compressed payload") from exc
    if (
        len(plaintext) != expected
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise ValueError("invalid BKENC2 compressed payload")
    return plaintext


def _decrypt_bkenc(
    blob: bytes,
    key: bytes,
    member_path: str,
    mode: str,
) -> None:
    expected_magic = BKENC1_MAGIC if mode == "bkenc1" else BKENC2_MAGIC
    if (
        type(blob) is not bytes
        or type(key) is not bytes
        or len(key) != BKENC_KEY_BYTES
        or mode not in {"bkenc1", BKENC2_GZIP_FORMAT}
        or not blob.startswith(expected_magic)
        or len(blob)
        < len(expected_magic) + BKENC1_NONCE_BYTES + BKENC1_TAG_BYTES
    ):
        raise ValueError("invalid BKENC envelope")
    body = blob[len(expected_magic) :]
    nonce = body[:BKENC1_NONCE_BYTES]
    ciphertext = body[BKENC1_NONCE_BYTES:]
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ValueError("BKENC authentication unavailable") from exc
    try:
        plaintext = AESGCM(key).decrypt(
            nonce,
            ciphertext,
            _bkenc1_member_aad(member_path)
            if mode == "bkenc1"
            else _bkenc2_member_aad(member_path),
        )
    except InvalidTag as exc:
        raise ValueError("invalid BKENC authentication tag") from exc
    if mode == BKENC2_GZIP_FORMAT:
        plaintext = _decompress_bkenc2(plaintext)
    del plaintext


def _zip_compressed_payload(path: Path, info: zipfile.ZipInfo) -> bytes:
    with Path(path).open("rb") as stream:
        stream.seek(info.header_offset)
        header = stream.read(30)
        if len(header) != 30:
            raise ValueError("truncated ZIP local header")
        (
            signature,
            _extract_version,
            _flags,
            _method,
            _time,
            _date,
            _crc,
            _compressed_size,
            _uncompressed_size,
            filename_size,
            extra_size,
        ) = struct.unpack("<IHHHHHIIIHH", header)
        if signature != 0x04034B50:
            raise ValueError("invalid ZIP local header")
        stream.seek(filename_size + extra_size, os.SEEK_CUR)
        compressed = stream.read(info.compress_size)
    if len(compressed) != info.compress_size:
        raise ValueError("truncated ZIP compressed payload")
    return compressed


def _bkenc_member_set_sha256(paths: list[str]) -> str:
    return hashlib.sha256(
        "".join(f"{path}\n" for path in paths).encode("utf-8")
    ).hexdigest()


def _bkenc_receipt_body(payload: dict[str, object]) -> bytes:
    body = {
        key: value for key, value in payload.items() if key != "authentication_tag"
    }
    return json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _bkenc_receipt_tag(payload: dict[str, object], key: bytes) -> str:
    receipt_key = hmac.new(
        key,
        BKENC_RECEIPT_KEY_DOMAIN,
        hashlib.sha256,
    ).digest()
    return hmac.new(
        receipt_key,
        BKENC_RECEIPT_HMAC_DOMAIN + _bkenc_receipt_body(payload),
        hashlib.sha256,
    ).hexdigest()


def _bkenc_entries_from_zip(
    zip_path: Path,
    archive: zipfile.ZipFile,
    contract_members: dict[str, tuple[str, bool]],
    key: bytes,
) -> list[dict[str, object]]:
    members, _folded, issues = _strict_release_member_index(archive)
    if issues:
        raise ValueError("invalid release member identity")
    entries: list[dict[str, object]] = []
    for member in sorted(contract_members):
        infos = members.get(member, ())
        if len(infos) != 1:
            if contract_members[member][1]:
                raise ValueError("missing BKENC member")
            continue
        info = infos[0]
        blob = archive.read(info)
        _decrypt_bkenc(blob, key, member, contract_members[member][0])
        compressed = _zip_compressed_payload(zip_path, info)
        entries.append(
            {
                "path": member,
                "envelope_size": len(blob),
                "envelope_sha256": hashlib.sha256(blob).hexdigest(),
                "compression_method": info.compress_type,
                "compressed_size": len(compressed),
                "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
                "date_time": list(info.date_time),
                "create_system": info.create_system,
                "flag_bits": info.flag_bits,
                "external_attr": info.external_attr,
            }
        )
    return entries


def write_bkenc_authentication_receipt(
    zip_path: Path,
    receipt_path: Path,
    contract_path: Path,
    key_file: Path,
) -> None:
    """Authenticate every contract-encrypted member without persisting plaintext."""
    contract_raw, contract_members, _receipt_required = _bkenc_contract_members(
        contract_path
    )
    key = _load_bkenc_key_file(key_file)
    with zipfile.ZipFile(zip_path) as archive:
        if _archive_structure_issues(archive):
            raise ValueError("invalid ZIP structure")
        entries = _bkenc_entries_from_zip(
            Path(zip_path), archive, contract_members, key
        )
    paths = [str(entry["path"]) for entry in entries]
    payload: dict[str, object] = {
        "schema": BKENC_AUTHENTICATION_RECEIPT_SCHEMA,
        "receipt_version": 1,
        "contract_sha256": hashlib.sha256(contract_raw).hexdigest(),
        "member_count": len(entries),
        "member_set_sha256": _bkenc_member_set_sha256(paths),
        "entries": entries,
    }
    payload["authentication_tag"] = _bkenc_receipt_tag(payload, key)
    Path(receipt_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_bkenc_authentication_receipt(
    receipt_path: Path,
    *,
    contract_sha256: str,
    contract_members: dict[str, tuple[str, bool]],
    key: bytes,
) -> tuple[bytes, list[dict[str, object]]]:
    raw = Path(receipt_path).read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    entries = payload.get("entries")
    if (
        type(payload) is not dict
        or set(payload) != BKENC_AUTHENTICATION_RECEIPT_FIELDS
        or payload.get("schema") != BKENC_AUTHENTICATION_RECEIPT_SCHEMA
        or type(payload.get("receipt_version")) is not int
        or payload["receipt_version"] != 1
        or payload.get("contract_sha256") != contract_sha256
        or type(entries) is not list
        or type(payload.get("member_count")) is not int
        or payload["member_count"] != len(entries)
        or type(payload.get("member_set_sha256")) is not str
        or type(payload.get("authentication_tag")) is not str
        or re.fullmatch(r"[0-9a-f]{64}", payload["authentication_tag"]) is None
        or not hmac.compare_digest(
            payload["authentication_tag"], _bkenc_receipt_tag(payload, key)
        )
    ):
        raise ValueError("invalid BKENC receipt")

    normalized: list[dict[str, object]] = []
    for entry in entries:
        if (
            type(entry) is not dict
            or set(entry) != BKENC_AUTHENTICATION_ENTRY_FIELDS
            or type(entry.get("path")) is not str
            or entry["path"] not in contract_members
            or type(entry.get("envelope_size")) is not int
            or entry["envelope_size"] <= 0
            or type(entry.get("envelope_sha256")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", entry["envelope_sha256"]) is None
            or type(entry.get("compression_method")) is not int
            or entry["compression_method"]
            not in RELEASE_ARCHIVE_LIMITS["allowed_compression_methods"]
            or type(entry.get("compressed_size")) is not int
            or entry["compressed_size"] <= 0
            or type(entry.get("compressed_sha256")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", entry["compressed_sha256"]) is None
            or type(entry.get("date_time")) is not list
            or len(entry["date_time"]) != 6
            or any(type(value) is not int for value in entry["date_time"])
            or type(entry.get("create_system")) is not int
            or type(entry.get("flag_bits")) is not int
            or type(entry.get("external_attr")) is not int
        ):
            raise ValueError("invalid BKENC receipt entry")
        normalized.append(dict(entry))
    paths = [str(entry["path"]) for entry in normalized]
    if (
        paths != sorted(paths)
        or len(paths) != len(set(paths))
        or payload["member_set_sha256"] != _bkenc_member_set_sha256(paths)
    ):
        raise ValueError("invalid BKENC receipt member set")
    return raw, normalized


def _bkenc_authentication_issues(
    archive: zipfile.ZipFile,
    archive_path: Path,
    contract_raw: bytes,
    contract_members: dict[str, tuple[str, bool]],
    receipt_required: bool,
    receipt_path: Path | None,
    key: bytes | None,
    build_fields: dict[str, str],
) -> list[str]:
    if receipt_path is None:
        return (
            ["release_bkenc_authentication_receipt_missing"]
            if receipt_required
            else []
        )
    if key is None:
        return ["release_bkenc_authentication_key_missing"]
    try:
        receipt_raw, receipt_entries = _load_bkenc_authentication_receipt(
            receipt_path,
            contract_sha256=hashlib.sha256(contract_raw).hexdigest(),
            contract_members=contract_members,
            key=key,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return ["release_bkenc_authentication_receipt_invalid"]
    if (
        build_fields.get("bkencauthenticationreceiptsha256", "").casefold()
        != hashlib.sha256(receipt_raw).hexdigest()
    ):
        return ["release_bkenc_authentication_receipt_hash_mismatch"]
    try:
        actual_entries = _bkenc_entries_from_zip(
            archive_path, archive, contract_members, key
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
    ):
        return ["release_member_bkenc_authentication_invalid"]
    if actual_entries != receipt_entries:
        return ["release_bkenc_authentication_receipt_mismatch"]
    return []


def _load_runtime_inventory_document(
    path: Path,
    *,
    schema: str,
    version_field: str,
    expected_fields: frozenset[str],
) -> tuple[bytes, list[dict[str, object]]]:
    raw = Path(path).read_bytes()
    document = json.loads(raw.decode("utf-8"))
    entries = document["entries"]
    if (
        not _is_exact_type(document, dict)
        or set(document) != expected_fields
        or document.get("schema") != schema
        or not _is_exact_type(document.get(version_field), int)
        or document[version_field] != 1
        or document.get("prefix") != RUNTIME_INVENTORY_PREFIX
        or not _is_exact_type(entries, list)
    ):
        raise ValueError("invalid runtime inventory document")

    normalized_entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if (
            not _is_exact_type(entry, dict)
            or set(entry) != RUNTIME_INVENTORY_ENTRY_FIELDS
        ):
            raise ValueError("invalid runtime inventory entry")
        path_value = entry.get("path")
        entry_type = entry.get("type")
        size = entry.get("size")
        digest = entry.get("sha256")
        compression_method = entry.get("compression_method")
        is_directory = entry_type == "directory"
        if (
            not _is_exact_type(path_value, str)
            or not path_value.startswith(RUNTIME_INVENTORY_PREFIX)
            or _canonical_release_member_name(path_value, is_dir=is_directory)
            != path_value.rstrip("/")
            or entry_type not in {"file", "directory"}
            or not _is_exact_type(size, int)
            or size < 0
            or not _is_exact_type(compression_method, int)
            or compression_method
            not in RELEASE_ARCHIVE_LIMITS["allowed_compression_methods"]
            or path_value in seen
        ):
            raise ValueError("invalid runtime inventory entry")
        if is_directory:
            if (
                size != 0
                or digest is not None
                or compression_method != zipfile.ZIP_STORED
            ):
                raise ValueError("invalid runtime inventory directory")
        elif (
            not _is_exact_type(digest, str)
            or _CLIENT_PROTECTION_SHA256_RE.fullmatch(digest) is None
        ):
            raise ValueError("invalid runtime inventory file")
        seen.add(path_value)
        normalized_entries.append(
            {
                "path": path_value,
                "type": entry_type,
                "size": size,
                "sha256": digest,
                "compression_method": compression_method,
            }
        )
    if RUNTIME_INVENTORY_PREFIX not in seen:
        raise ValueError("missing runtime inventory root")
    for entry in normalized_entries:
        relative = str(entry["path"])[len(RUNTIME_INVENTORY_PREFIX) :].rstrip("/")
        if not relative:
            if entry["type"] != "directory":
                raise ValueError("invalid runtime inventory root")
            continue
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            parent_path = f"{RUNTIME_INVENTORY_PREFIX}{parent.as_posix()}/"
            if parent_path not in seen:
                raise ValueError("missing runtime inventory parent")
            parent = parent.parent
    if normalized_entries != sorted(
        normalized_entries, key=lambda entry: str(entry["path"])
    ):
        raise ValueError("unsorted runtime inventory")
    if (
        not _is_exact_type(document.get("file_count"), int)
        or not _is_exact_type(document.get("directory_count"), int)
        or not _is_exact_type(document.get("total_bytes"), int)
        or document["file_count"]
        != sum(entry["type"] == "file" for entry in normalized_entries)
        or document["directory_count"]
        != sum(entry["type"] == "directory" for entry in normalized_entries)
        or document["total_bytes"]
        != sum(int(entry["size"]) for entry in normalized_entries)
        or document.get("inventory_sha256")
        != _runtime_inventory_sha256(normalized_entries)
    ):
        raise ValueError("invalid runtime inventory identity")
    return raw, normalized_entries


def _runtime_inventory_receipt_issues(
    archive: zipfile.ZipFile,
    receipt_path: Path | None,
    approval_path: Path | None,
    runtime_contract: object,
    build_fields: dict[str, str],
) -> list[str]:
    expected_contract_fields = {
        "prefix",
        "receipt_schema",
        "receipt_required",
        "approval_schema",
        "approval_required",
    }
    if (
        not _is_exact_type(runtime_contract, dict)
        or set(runtime_contract) != expected_contract_fields
    ):
        return ["release_contract_invalid"]
    if (
        runtime_contract.get("prefix") != RUNTIME_INVENTORY_PREFIX
        or runtime_contract.get("receipt_schema") != RUNTIME_INVENTORY_RECEIPT_SCHEMA
        or runtime_contract.get("approval_schema")
        != RUNTIME_INVENTORY_APPROVAL_SCHEMA
        or not _is_exact_type(runtime_contract.get("receipt_required"), bool)
        or not _is_exact_type(runtime_contract.get("approval_required"), bool)
    ):
        return ["release_contract_invalid"]
    if approval_path is None:
        if runtime_contract["approval_required"]:
            return ["release_runtime_inventory_approval_missing"]
        return []
    if receipt_path is None:
        if runtime_contract["receipt_required"]:
            return ["release_runtime_inventory_receipt_missing"]
        return []

    try:
        _approval_raw, approval = _load_runtime_inventory_approval(approval_path)
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeError,
    ):
        return ["release_runtime_inventory_approval_invalid"]

    approval_sha256 = _runtime_inventory_approval_sha256(approval)
    if (
        build_fields.get("runtimeinventoryapprovalsha256", "").casefold()
        != approval_sha256
    ):
        return ["release_runtime_inventory_approval_hash_mismatch"]

    try:
        receipt_raw, normalized_entries = _load_runtime_inventory_document(
            receipt_path,
            schema=RUNTIME_INVENTORY_RECEIPT_SCHEMA,
            version_field="receipt_version",
            expected_fields=RUNTIME_INVENTORY_RECEIPT_FIELDS,
        )
        receipt = json.loads(receipt_raw.decode("utf-8"))
        if receipt.get("approval_sha256") != approval_sha256:
            raise ValueError("receipt is not bound to approval")
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeError,
    ):
        return ["release_runtime_inventory_receipt_invalid"]

    receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
    if (
        build_fields.get("runtimeinventoryreceiptsha256", "").casefold()
        != receipt_sha256
    ):
        return ["release_runtime_inventory_receipt_hash_mismatch"]
    if _runtime_inventory_identity(normalized_entries) != {
        key: approval[key]
        for key in (
            "prefix",
            "file_count",
            "directory_count",
            "total_bytes",
            "inventory_sha256",
        )
    }:
        return ["release_runtime_inventory_not_approved"]

    actual_entries: list[dict[str, object]] = []
    try:
        for info in archive.infolist():
            path_value = info.orig_filename
            if not path_value.startswith(RUNTIME_INVENTORY_PREFIX):
                continue
            if info.is_dir():
                actual_entries.append(
                    {
                        "path": path_value,
                        "type": "directory",
                        "size": 0,
                        "sha256": None,
                        "compression_method": info.compress_type,
                    }
                )
            else:
                data = archive.read(info)
                actual_entries.append(
                    {
                        "path": path_value,
                        "type": "file",
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "compression_method": info.compress_type,
                    }
                )
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return ["release_runtime_inventory_mismatch"]
    actual_entries.sort(key=lambda entry: str(entry["path"]))
    if actual_entries != normalized_entries:
        return ["release_runtime_inventory_mismatch"]
    return []


def _fixed_member_issues(
    archive: zipfile.ZipFile,
    fixed_member_sha256: object,
    required_names: set[str],
    members: dict[str, list[zipfile.ZipInfo]],
) -> list[str]:
    """Check reviewed fixed bytes after the caller's strict ZIP identity gate.

    Kept callable for bounded real-input tests; this is the final ZIP check itself,
    not a second algorithm or a producer-derived set of expected hashes.
    """
    if (
        type(fixed_member_sha256) is not dict
        or any(
            type(name) is not str
            or re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None
            for name, digest in fixed_member_sha256.items()
        )
    ):
        return ["release_contract_invalid"]
    fixed_names: set[str] = set()
    for name in fixed_member_sha256:
        normalized = _canonical_release_member_name(name, is_dir=False)
        if normalized is None or normalized != name:
            return ["release_contract_invalid"]
        fixed_names.add(name)
    if not fixed_names <= required_names:
        return ["release_contract_invalid"]
    issues: list[str] = []
    for name, digest in fixed_member_sha256.items():
        infos = members.get(name, ())
        if len(infos) != 1:
            issues.append(f"release_fixed_member_missing: {name}")
            continue
        try:
            actual = hashlib.sha256(archive.read(infos[0])).hexdigest()
        except (OSError, RuntimeError, zipfile.BadZipFile):
            issues.append(f"release_fixed_member_unreadable: {name}")
            continue
        if actual != digest:
            issues.append(f"release_fixed_member_hash_mismatch: {name}")
    return issues


def _release_contract_issues(
    archive: zipfile.ZipFile,
    contract_path: Path,
    expected_fields: tuple[tuple[str, str], ...],
    runtime_receipt: Path | None,
    runtime_approval: Path | None,
    bkenc_receipt: Path | None,
    bkenc_key: bytes | None,
) -> list[str]:
    """Enforce the positive, externally pinned public-candidate ZIP contract."""
    try:
        contract_raw = contract_path.read_bytes()
        contract = json.loads(contract_raw.decode("utf-8"))
        if (
            type(contract) is not dict
            or contract.get("schema") != RELEASE_ZIP_CONTRACT_SCHEMA
            or type(contract.get("contract_version")) is not int
            or contract["contract_version"] <= 0
        ):
            raise ValueError("unsupported contract")
        required_members = contract["required_members"]
        required_manifest_fields = contract["required_manifest_fields"]
        fixed_manifest_values = contract["fixed_manifest_values"]
        fixed_member_sha256 = contract["fixed_member_sha256"]
        dependencies = contract["runtime_dependencies"]
        sensitive_prefixes = contract["sensitive_prefixes"]
        protection = contract["member_protection"]
        public_text_members = contract["public_text_members"]
        text_exclusions = contract["mutually_exclusive_text_terms"]
        archive_limits = contract["archive_limits"]
        runtime_inventory = contract["runtime_inventory"]
        bkenc_authentication = contract["bkenc_authentication"]
        native_binary = contract.get("native_binary")
        launcher_wrappers = contract.get("launcher_wrappers")
        release_availability = contract.get("release_availability")
        if any(
            type(value) is not expected
            for value, expected in (
                (required_members, dict),
                (required_manifest_fields, list),
                (fixed_manifest_values, dict),
                (fixed_member_sha256, dict),
                (dependencies, dict),
                (sensitive_prefixes, list),
                (protection, dict),
                (public_text_members, list),
                (text_exclusions, list),
                (archive_limits, dict),
                (runtime_inventory, dict),
                (bkenc_authentication, dict),
            )
        ) or not _release_archive_limits_are_exact(archive_limits):
            raise ValueError("invalid contract")
        if native_binary is not None and type(native_binary) is not dict:
            raise ValueError("invalid native binary contract")
        if contract["contract_version"] >= 16 and (
            not _is_exact_type(launcher_wrappers, dict)
            or not _is_exact_type(release_availability, dict)
        ):
            raise ValueError("release availability contract missing")
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeError,
    ):
        return ["release_contract_invalid"]

    try:
        bkenc_contract_raw, bkenc_contract_members, bkenc_receipt_required = (
            _bkenc_contract_members(contract_path)
        )
        if bkenc_contract_raw != contract_raw:
            raise ValueError("contract changed during audit")
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeError,
    ):
        return ["release_contract_invalid"]

    members, casefolded_members, issues = _strict_release_member_index(archive)
    if _release_member_identity_failed(issues):
        return issues
    if issues:
        return issues

    fields, manifest_issues = _strict_build_manifest(archive)
    issues.extend(manifest_issues)
    if fields is None:
        fields = {}

    def safe_contract_member(value: object) -> tuple[str, str]:
        if type(value) is not str:
            raise ValueError("invalid member")
        normalized = _canonical_release_member_name(value, is_dir=False)
        if normalized is None or normalized != value:
            raise ValueError("invalid member")
        return value, value

    try:
        required: list[str] = []
        for group in ("common",):
            group_values = required_members.get(group)
            if type(group_values) is not list:
                raise ValueError("invalid required group")
            required.extend(group_values)
        encrypted = fields.get("encrypted", "").casefold() == "true"
        if encrypted:
            encrypted_values = required_members.get("encrypted")
            if type(encrypted_values) is not list:
                raise ValueError("invalid required group")
            required.extend(encrypted_values)
        profile = fields.get("productprofile", "").casefold()
        if profile not in {"standard", "network-diagnostic"}:
            raise ValueError("invalid profile")
        profile_values = required_members.get(profile)
        if type(profile_values) is not list:
            raise ValueError("invalid profile group")
        required.extend(profile_values)
        required_pairs = [safe_contract_member(value) for value in required]
        if (
            len({name for name, _ in required_pairs}) != len(required_pairs)
            or len({name.casefold() for name, _ in required_pairs})
            != len(required_pairs)
        ):
            raise ValueError("duplicate required member")
    except (AttributeError, TypeError, ValueError):
        return [*issues, "release_contract_invalid"]

    if contract["contract_version"] >= 16:
        expected_launcher_contract = {
            "members": list(LAUNCHER_WRAPPER_MEMBERS),
            "target": "BidKingLive.exe",
            "encoding": "ascii",
            "line_endings": "crlf",
            "sha256": LAUNCHER_WRAPPER_SHA256,
        }
        try:
            census_contract = release_availability["policy_census"]
            route_contract = release_availability["route_scope"]
            current_model = _availability_uses_current_model(release_availability)
            availability_contract_valid = (
                set(release_availability)
                == {
                    "receipt_schema",
                    "receipt_version",
                    "captures",
                    "policy_census",
                    "route_scope",
                    "current_authority",
                }
                and release_availability["receipt_schema"]
                == "bidking_release_availability_receipt_v1"
                and release_availability["receipt_version"] == 1
                and _is_exact_type(release_availability["captures"], list)
                and len(release_availability["captures"]) == 5
                and _availability_census_is_exact(census_contract, current_model=current_model)
                and _release_availability_route_contract_is_exact(route_contract, current_model=current_model)
                and release_availability["current_authority"]["map_id"] == 2409
                and re.fullmatch(
                    r"[0-9a-f]{64}",
                    release_availability["current_authority"].get(
                        "canonical_input_sha256", ""
                    ),
                )
                is not None
            )
        except (KeyError, TypeError, ValueError):
            availability_contract_valid = False
        if (
            launcher_wrappers != expected_launcher_contract
            or not availability_contract_valid
            or any(member not in required for member in LAUNCHER_WRAPPER_MEMBERS)
            or any(
                fixed_member_sha256.get(member) != LAUNCHER_WRAPPER_SHA256
                for member in LAUNCHER_WRAPPER_MEMBERS
            )
        ):
            return [*issues, "release_contract_invalid"]
        for member in LAUNCHER_WRAPPER_MEMBERS:
            infos = members.get(member, ())
            if len(infos) != 1:
                continue
            try:
                wrapper_raw = archive.read(infos[0])
            except (OSError, RuntimeError, zipfile.BadZipFile):
                issues.append(f"release_launcher_wrapper_unreadable: {member}")
                continue
            if wrapper_raw != LAUNCHER_WRAPPER_BYTES:
                issues.append(f"release_launcher_wrapper_invalid: {member}")

    for name, display in required_pairs:
        if len(members.get(name, ())) != 1:
            if name.casefold() in casefolded_members:
                issues.append(f"release_member_case_mismatch: {display}")
            else:
                issues.append(f"release_member_missing: {display}")

    if (
        type(required_manifest_fields) is not list
        or any(type(value) is not str or not value for value in required_manifest_fields)
        or type(fixed_manifest_values) is not dict
        or any(
            type(key) is not str or type(value) is not str
            for key, value in fixed_manifest_values.items()
        )
    ):
        return [*issues, "release_contract_invalid"]
    for key in required_manifest_fields:
        if key.casefold() not in fields:
            issues.append(f"release_manifest_field_missing: {key}")

    expected: dict[str, str] = {}
    for key, value in fixed_manifest_values.items():
        expected[key.casefold()] = value
    expected["releasezipcontractsha256"] = hashlib.sha256(contract_raw).hexdigest()
    for key, value in expected_fields:
        if (
            not key
            or type(key) is not str
            or type(value) is not str
            or key.casefold() in expected
            and expected[key.casefold()] != value
        ):
            return [*issues, "release_contract_invalid"]
        expected[key.casefold()] = value
    for key, value in expected.items():
        if fields.get(key) != value:
            issues.append(f"release_manifest_field_mismatch: {key}")

    if ACTIVATION_SERVICE_ENDPOINTS_MEMBER in required:
        issues.extend(
            _activation_service_endpoint_identity_issues(
                archive,
                members,
                fields,
            )
        )
    if ACTIVATION_HTTP_E2EE_MEMBER in required:
        issues.extend(
            _activation_http_e2ee_identity_issues(
                archive,
                members,
                fields,
            )
        )

    issues.extend(
        _runtime_inventory_receipt_issues(
            archive,
            runtime_receipt,
            runtime_approval,
            runtime_inventory,
            fields,
        )
    )
    archive_path = Path(str(archive.filename))
    issues.extend(
        _bkenc_authentication_issues(
            archive,
            archive_path,
            contract_raw,
            bkenc_contract_members,
            bkenc_receipt_required,
            bkenc_receipt,
            bkenc_key,
            fields,
        )
    )

    fixed_issues = _fixed_member_issues(
        archive, fixed_member_sha256,
        {name for name, _display in required_pairs}, members,
    )
    issues.extend(fixed_issues)
    if "release_contract_invalid" in fixed_issues:
        return issues

    if (
        type(dependencies) is not dict
        or not dependencies
        or any(
            type(name) is not str
            or _canonical_distribution_name(name) != name
            or type(version) is not str
            or not version
            for name, version in dependencies.items()
        )
    ):
        return [*issues, "release_contract_invalid"]
    serialized_dependencies = "".join(
        f"{name}=={dependencies[name]}\n" for name in sorted(dependencies)
    ).encode("utf-8")
    inventory_sha256 = hashlib.sha256(serialized_dependencies).hexdigest()
    if (
        fixed_manifest_values.get("RuntimeDependencyInventorySHA256")
        != inventory_sha256
    ):
        return [*issues, "release_contract_invalid"]
    runtime_dependencies, dependency_invalid = _runtime_dependency_rows(archive)
    if dependency_invalid:
        issues.append("runtime_dependency_inventory_invalid")
    for name in sorted(set(runtime_dependencies) - set(dependencies)):
        issues.append(f"runtime_dependency_unexpected: {name}")
    for name, version in dependencies.items():
        versions = runtime_dependencies.get(name, [])
        if versions != [version]:
            issues.append(f"runtime_dependency_mismatch: {name}")

    try:
        normalized_prefixes = []
        for value in sensitive_prefixes:
            name, _display = safe_contract_member(value.rstrip("/"))
            normalized_prefixes.append(f"{name}/")
        normalized_protection: dict[str, tuple[str, bool, str]] = {}
        protection_casefolded: set[str] = set()
        for raw_name, raw_rule in protection.items():
            name, display = safe_contract_member(raw_name)
            if (
                type(raw_rule) is not dict
                or raw_rule.get("mode")
                not in {"bkenc1", BKENC2_GZIP_FORMAT, "plaintext"}
                or type(raw_rule.get("required")) is not bool
                or name.casefold() in protection_casefolded
            ):
                raise ValueError("invalid protection rule")
            protection_casefolded.add(name.casefold())
            normalized_protection[name] = (
                raw_rule["mode"],
                raw_rule["required"],
                display,
            )
    except (AttributeError, TypeError, ValueError):
        return [*issues, "release_contract_invalid"]

    for name in members:
        matched_prefixes = [
            prefix
            for prefix in normalized_prefixes
            if name.casefold().startswith(prefix.casefold())
        ]
        if not matched_prefixes:
            continue
        if not any(name.startswith(prefix) for prefix in matched_prefixes):
            issues.append("release_member_case_mismatch")
        elif name not in normalized_protection:
            issues.append(
                f"release_member_protection_undeclared: {name}"
            )
    for name, (mode, required_member, display) in normalized_protection.items():
        infos = members.get(name, ())
        if required_member and len(infos) != 1:
            issues.append(f"release_protected_member_missing: {display}")
            continue
        if len(infos) != 1:
            continue
        try:
            data = archive.read(infos[0])
        except (OSError, RuntimeError, zipfile.BadZipFile):
            issues.append(f"release_protected_member_unreadable: {display}")
            continue
        is_bkenc1 = data.startswith(BKENC1_MAGIC)
        is_bkenc2 = data.startswith(BKENC2_MAGIC)
        encrypted_member = is_bkenc1 or is_bkenc2
        if mode == "bkenc1" and not is_bkenc1:
            issues.append(f"release_member_requires_bkenc1: {display}")
        elif mode == BKENC2_GZIP_FORMAT and not is_bkenc2:
            issues.append(f"release_member_requires_bkenc2_gzip: {display}")
        elif mode == "plaintext" and encrypted_member:
            issues.append(f"release_member_requires_plaintext: {display}")

    public_text_values: list[str] = []
    for raw_name in public_text_members:
        try:
            folded, display = safe_contract_member(raw_name)
        except ValueError:
            return [*issues, "release_contract_invalid"]
        infos = members.get(folded, ())
        if not infos:
            continue
        try:
            text = _decode_distribution_text(archive.read(infos[0]))
        except (OSError, RuntimeError, zipfile.BadZipFile):
            text = None
        if text is None:
            issues.append(f"release_public_text_unreadable: {display}")
        else:
            public_text_values.append(text)
    combined_text = "\n".join(public_text_values)
    for exclusion in text_exclusions:
        if (
            type(exclusion) is not dict
            or type(exclusion.get("label")) is not str
            or type(exclusion.get("left")) is not list
            or type(exclusion.get("right")) is not list
            or any(
                type(term) is not str or not term
                for term in (*exclusion["left"], *exclusion["right"])
            )
        ):
            return [*issues, "release_contract_invalid"]
        comparison_text = combined_text
        left_terms = exclusion["left"]
        right_terms = exclusion["right"]
        if exclusion["label"] == "trial_duration":
            comparison_text = "\x00".join(
                _normalize_trial_duration_copy(text)
                for text in public_text_values
            )
            left_terms = [
                _normalize_trial_duration_copy(term) for term in left_terms
            ]
            right_terms = [
                _normalize_trial_duration_copy(term) for term in right_terms
            ]
            if any(not term for term in (*left_terms, *right_terms)):
                return [*issues, "release_contract_invalid"]
        if (
            any(term in comparison_text for term in left_terms)
            and any(term in comparison_text for term in right_terms)
        ):
            issues.append(f"release_public_text_conflict: {exclusion['label']}")
    if native_binary is not None:
        try:
            if set(native_binary) != {
                "contract_source",
                "distribution",
                "receipt_member",
                "receipt_schema",
            } or native_binary["contract_source"] != (
                "scripts/release_native_binary_contract.json"
            ) or native_binary["distribution"] != (
                "unsigned"
            ) or native_binary["receipt_member"] != (
                "integrity/native_binary_receipt.json"
            ) or native_binary["receipt_schema"] != (
                "bidking_native_binary_receipt_v2"
            ):
                raise ValueError("native binary contract source invalid")
            native_contract_path = contract_path.with_name(
                "release_native_binary_contract.json"
            )
            protection_path = contract_path.with_name(
                "client_protection_manifest.json"
            )
            native_contract_raw = native_contract_path.read_bytes()
            protection_raw = protection_path.read_bytes()
            native_contract = _NATIVE_HARDENING.load_contract(
                native_contract_path
            )
            protection_manifest = json.loads(protection_raw.decode("utf-8"))
            if members.get("integrity/native_signing_policy.json"):
                issues.append("native_binary_signing_policy_forbidden")
                return issues
            receipt_name = native_binary["receipt_member"]
            receipt_infos = members.get(receipt_name, ())
            if len(receipt_infos) != 1:
                issues.append("native_binary_receipt_missing")
                return issues
            receipt_raw = archive.read(receipt_infos[0])
            receipt = _NATIVE_HARDENING.load_receipt_bytes(receipt_raw)
            if (
                receipt.get("schema") != native_binary["receipt_schema"]
                or fields.get("nativebinarycontractsha256", "").casefold()
                != hashlib.sha256(native_contract_raw).hexdigest()
                or fields.get("nativebinaryreceiptsha256", "").casefold()
                != hashlib.sha256(receipt_raw).hexdigest()
                or fields.get("nativebinarydistribution", "").casefold()
                != "unsigned"
                or receipt.get("distribution") != "unsigned"
                or fields.get("nativebinaryverified", "").casefold() != "true"
                or fields.get("nativebinarycount", "") != "119"
                or receipt.get("contract_sha256")
                != hashlib.sha256(native_contract_raw).hexdigest()
            ):
                issues.append("native_binary_receipt_invalid")
                return issues
            unique_names = [
                name for name, infos in members.items() if len(infos) == 1
            ]
            native_names = _NATIVE_HARDENING.actual_first_party_members(
                unique_names, native_contract
            )
            binary_members = {
                name: archive.read(members[name][0]) for name in native_names
            }
            native_issues = _NATIVE_HARDENING.receipt_issues(
                binary_members,
                contract=native_contract,
                protection_manifest=protection_manifest,
                receipt=receipt,
            )
            issues.extend(native_issues)
        except (
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UnicodeError,
            zipfile.BadZipFile,
        ):
            issues.append("native_binary_contract_malformed")
    return issues


def _client_protection_issues(
    archive: zipfile.ZipFile,
    manifest_path: Path,
    build_fields: dict[str, str],
) -> list[str]:
    """Enforce the value-tiered native boundary without logging protected names."""

    def strict_list(parent: dict[str, object], key: str) -> list[str]:
        value = parent.get(key)
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise ValueError(f"invalid {key}")
        folded = [item.casefold() for item in value]
        if len(folded) != len(set(folded)):
            raise ValueError(f"duplicate {key}")
        return value

    def safe_path(value: object, *, suffix: str | None = None) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\\" in value
            or value.startswith("/")
            or ":" in value
        ):
            raise ValueError("unsafe protection path")
        parts = value.split("/")
        if any(not part or part in {".", ".."} for part in parts):
            raise ValueError("unsafe protection path")
        normalized = PurePosixPath(value).as_posix()
        if normalized != value or (suffix is not None and not value.endswith(suffix)):
            raise ValueError("unsafe protection path")
        return value

    try:
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("invalid manifest")
        critical = manifest["critical_native"]
        manifest_version = manifest.get("manifest_version")
        if (
            manifest.get("schema") != CLIENT_PROTECTION_SCHEMA
            or not isinstance(manifest_version, int)
            or isinstance(manifest_version, bool)
            or manifest_version <= 0
        ):
            raise ValueError("unsupported schema")
        if not isinstance(critical, dict):
            raise ValueError("invalid protection boundary")

        abi_tags = strict_list(manifest, "native_abi_tags")
        if any(_CLIENT_PROTECTION_ABI_TAG_RE.fullmatch(tag) is None for tag in abi_tags):
            raise ValueError("invalid native ABI")

        source_roots = [
            safe_path(value) for value in strict_list(manifest, "source_roots")
        ]
        folded_roots = [root.casefold() for root in source_roots]
        if any(
            right.startswith(f"{left}/")
            for left in folded_roots
            for right in folded_roots
            if left != right
        ):
            raise ValueError("overlapping source roots")

        plaintext_allowlist = {
            safe_path(value, suffix=".py").casefold()
            for value in strict_list(manifest, "plaintext_allowlist")
        }
        packaged_plaintext_python_allowlist = {
            safe_path(value, suffix=".py").casefold()
            for value in strict_list(
                manifest,
                "packaged_plaintext_python_allowlist",
            )
        }
        encrypted_excludes = {
            safe_path(value, suffix=".py").casefold()
            for value in strict_list(manifest, "encrypted_stage_excludes")
        }
        build_only_sources = {
            safe_path(value, suffix=".py").casefold()
            for value in strict_list(manifest, "build_only_source_authorities")
        }
        if len(build_only_sources) != 3:
            raise ValueError("invalid build-only source authorities")

        engine_modules = strict_list(critical, "engine_modules")
        tool_modules = strict_list(critical, "tool_modules")
        if any(
            _CLIENT_PROTECTION_IDENTIFIER_RE.fullmatch(module) is None
            for module in (*engine_modules, *tool_modules)
        ):
            raise ValueError("invalid native module")

        package_modules = [
            safe_path(value, suffix=".py")
            for value in strict_list(critical, "package_modules")
        ]
        trees = critical.get("package_trees")
        if not isinstance(trees, list) or not trees:
            raise ValueError("invalid package trees")
        tree_paths: list[str] = []
        tree_modules: list[str] = []
        for tree in trees:
            if not isinstance(tree, dict):
                raise ValueError("invalid package tree")
            tree_path = safe_path(tree.get("path"))
            modules = [
                safe_path(value, suffix=".py")
                for value in strict_list(tree, "protected_modules")
            ]
            if any(
                module != f"{tree_path}/{PurePosixPath(module).name}"
                and not module.startswith(f"{tree_path}/")
                for module in modules
            ):
                raise ValueError("tree module outside tree")
            tree_paths.append(tree_path)
            tree_modules.extend(modules)
        folded_tree_paths = [path.casefold() for path in tree_paths]
        if (
            len(folded_tree_paths) != len(set(folded_tree_paths))
            or any(
                right.startswith(f"{left}/")
                for left in folded_tree_paths
                for right in folded_tree_paths
                if left != right
            )
        ):
            raise ValueError("duplicate package tree")

        overlay = critical.get("overlay")
        if not isinstance(overlay, dict):
            raise ValueError("invalid overlay")
        overlay_source = safe_path(overlay.get("source"), suffix=".py")
        overlay_native = overlay.get("native_module")
        overlay_sha256 = overlay.get("normalized_shell_sha256")
        if (
            not isinstance(overlay_native, str)
            or _CLIENT_PROTECTION_IDENTIFIER_RE.fullmatch(overlay_native) is None
            or not isinstance(overlay_sha256, str)
            or _CLIENT_PROTECTION_SHA256_RE.fullmatch(overlay_sha256) is None
        ):
            raise ValueError("invalid overlay")

        wss_operational = manifest.get("wss_operational_abi")
        wss_expected_fields = {
            "schema",
            "contract_version",
            "abi_version",
            "native_module",
            "entrypoint",
            "source_export",
            "runtime_roles",
            "source_members",
            "bootstrap_sha256",
            "generation_sha256",
        }
        if (
            type(wss_operational) is not dict
            or set(wss_operational) != wss_expected_fields
            or wss_operational.get("schema") != "bidking_wss_operational_abi_v1"
            or type(wss_operational.get("contract_version")) is not int
            or wss_operational.get("contract_version") != 1
            or wss_operational.get("abi_version")
            != "bidking_wss_addon_operational_v1"
            or wss_operational.get("native_module")
            != "_local_compat_addon_loader"
            or wss_operational.get("entrypoint") != "load_addons"
            or wss_operational.get("source_export") is not False
        ):
            raise ValueError("invalid WSS operational ABI")
        wss_runtime_roles = wss_operational.get("runtime_roles")
        if (
            type(wss_runtime_roles) is not dict
            or wss_runtime_roles
            != {"main": "cpython-313", "mitmdump": "cpython-314"}
        ):
            raise ValueError("invalid WSS runtime roles")
        wss_source_members = wss_operational.get("source_members")
        if type(wss_source_members) is not list or len(wss_source_members) != 2:
            raise ValueError("invalid WSS source identities")
        normalized_wss_sources: dict[str, str] = {}
        for entry in wss_source_members:
            if type(entry) is not dict or set(entry) != {"path", "sha256"}:
                raise ValueError("invalid WSS source identity")
            source_path = entry.get("path")
            source_sha256 = entry.get("sha256")
            if (
                source_path
                not in {
                    "scripts/bidking_wss_capture_core.py",
                    "scripts/mitm_bidking_wss_addon.py",
                }
                or type(source_sha256) is not str
                or _CLIENT_PROTECTION_SHA256_RE.fullmatch(source_sha256) is None
                or source_path in normalized_wss_sources
            ):
                raise ValueError("invalid WSS source identity")
            normalized_wss_sources[str(source_path)] = source_sha256
        if set(normalized_wss_sources) != {
            "scripts/bidking_wss_capture_core.py",
            "scripts/mitm_bidking_wss_addon.py",
        }:
            raise ValueError("invalid WSS source set")
        wss_bootstrap_sha256 = wss_operational.get("bootstrap_sha256")
        wss_generation_sha256 = wss_operational.get("generation_sha256")
        if (
            type(wss_bootstrap_sha256) is not str
            or _CLIENT_PROTECTION_SHA256_RE.fullmatch(wss_bootstrap_sha256) is None
            or type(wss_generation_sha256) is not str
            or _CLIENT_PROTECTION_SHA256_RE.fullmatch(wss_generation_sha256) is None
        ):
            raise ValueError("invalid WSS operational digest")
        wss_generation_document = dict(wss_operational)
        wss_generation_document.pop("generation_sha256")
        actual_wss_generation = hashlib.sha256(
            json.dumps(
                wss_generation_document,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
        if actual_wss_generation != wss_generation_sha256:
            raise ValueError("invalid WSS operational generation")

        protected_sources = [
            *(f"hero_ref/src/{module}.py" for module in engine_modules),
            *(f"hero_ref/tools/{module}.py" for module in tool_modules),
            *(f"src/{relative}" for relative in package_modules),
            *(f"src/{relative}" for relative in tree_modules),
        ]
        folded_protected = [source.casefold() for source in protected_sources]
        if len(folded_protected) != len(set(folded_protected)):
            raise ValueError("overlapping protected modules")
        overlay_source_folded = overlay_source.casefold()
        protected_set = set(folded_protected)
        source_plaintext = plaintext_allowlist | {overlay_source_folded}
        if (
            plaintext_allowlist & protected_set
            or encrypted_excludes & protected_set
            or plaintext_allowlist & encrypted_excludes
            or overlay_source_folded in protected_set
            or overlay_source_folded in plaintext_allowlist
            or overlay_source_folded in encrypted_excludes
            or packaged_plaintext_python_allowlist & protected_set
            or packaged_plaintext_python_allowlist & encrypted_excludes
            or build_only_sources & protected_set
            or build_only_sources & plaintext_allowlist
            or build_only_sources & encrypted_excludes
            or build_only_sources & packaged_plaintext_python_allowlist
            or overlay_source_folded in build_only_sources
            or not source_plaintext <= packaged_plaintext_python_allowlist
            or any(
                source.startswith(RUNTIME_INVENTORY_PREFIX)
                for source in packaged_plaintext_python_allowlist
            )
            or any(
                any(
                    source.startswith(f"{root}/")
                    for root in folded_roots
                )
                and source not in source_plaintext
                for source in packaged_plaintext_python_allowlist
            )
        ):
            raise ValueError("overlapping protection categories")

        all_declared_sources = (
            protected_set
            | plaintext_allowlist
            | encrypted_excludes
            | build_only_sources
            | {overlay_source_folded}
        )
        if any(
            not any(
                source == root or source.startswith(f"{root}/")
                for root in folded_roots
            )
            for source in all_declared_sources
        ):
            raise ValueError("source outside protected roots")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return ["client_protection_manifest_invalid"]

    issues: list[str] = []
    if build_fields.get("encrypted", "").casefold() != "true":
        issues.append("client_protection_encryption_required")
    if build_fields.get("clientprotectionschema") != CLIENT_PROTECTION_SCHEMA:
        issues.append("client_protection_schema_mismatch")
    expected_hash = hashlib.sha256(raw_manifest).hexdigest()
    if build_fields.get("clientprotectionmanifestsha256", "").casefold() != expected_hash:
        issues.append("client_protection_manifest_hash_mismatch")
    if (
        build_fields.get("wssoperationalabi")
        != wss_operational["abi_version"]
        or build_fields.get("wssoperationalgeneration", "").casefold()
        != wss_generation_sha256
        or build_fields.get("wssaddonbootstrapsha256", "").casefold()
        != wss_bootstrap_sha256
        or _CLIENT_PROTECTION_SHA256_RE.fullmatch(
            build_fields.get("wssaddonloadersha256", "").casefold()
        )
        is None
    ):
        issues.append("wss_operational_manifest_mismatch")

    member_entries = [
        (name.casefold(), name, info)
        for info in archive.infolist()
        if not info.is_dir()
        if (name := _normalized_member(info.filename)) is not None
    ]
    folded_members = [folded for folded, _, _ in member_entries]

    wss_bootstrap_entries = [
        info
        for folded, _, info in member_entries
        if folded == PROTECTED_ADDON_BOOTSTRAP_MEMBER
    ]
    wss_loader_entries = [
        info
        for folded, _, info in member_entries
        if folded == f"scripts/{PROTECTED_ADDON_LOADER_SUFFIX}"
    ]
    if len(wss_bootstrap_entries) != 1:
        issues.append("wss_operational_bootstrap_invalid")
    else:
        try:
            wss_bootstrap = archive.read(wss_bootstrap_entries[0])
        except (OSError, RuntimeError, zipfile.BadZipFile):
            issues.append("wss_operational_bootstrap_invalid")
        else:
            if hashlib.sha256(wss_bootstrap).hexdigest() != wss_bootstrap_sha256:
                issues.append("wss_operational_bootstrap_invalid")
    if len(wss_loader_entries) != 1:
        issues.append("wss_operational_loader_identity_mismatch")
    else:
        try:
            wss_loader = archive.read(wss_loader_entries[0])
        except (OSError, RuntimeError, zipfile.BadZipFile):
            issues.append("wss_operational_loader_identity_mismatch")
        else:
            if hashlib.sha256(wss_loader).hexdigest() != build_fields.get(
                "wssaddonloadersha256", ""
            ).casefold():
                issues.append("wss_operational_loader_identity_mismatch")

    native_expectations: dict[str, set[str]] = {}

    def add_native_expectation(source: str, native_stem: str | None = None) -> None:
        source_path = PurePosixPath(source)
        stem = native_stem or source_path.stem
        directory = source_path.parent.as_posix()
        expected = {
            f"{directory}/{stem}.{tag}.pyd".casefold() for tag in abi_tags
        }
        native_expectations[source.casefold()] = expected

    for module in engine_modules:
        add_native_expectation(f"hero_ref/src/{module}.py")
    for module in tool_modules:
        add_native_expectation(f"hero_ref/tools/{module}.py")
    for relative in package_modules:
        add_native_expectation(f"src/{relative}")
    for relative in tree_modules:
        add_native_expectation(f"src/{relative}")
    add_native_expectation(overlay_source, str(overlay_native))

    allowed_native_members = set().union(*native_expectations.values())
    for source, expected_names in native_expectations.items():
        exact = sum(name in expected_names for name in folded_members)
        if exact == 0:
            issues.append("protected_native_module_missing")
        elif exact > 1:
            issues.append("protected_native_module_duplicate")

        source_path = PurePosixPath(source)
        native_stem = (
            str(overlay_native)
            if source == overlay_source_folded
            else source_path.stem
        )
        near_prefix = (
            f"{source_path.parent.as_posix()}/{native_stem}.".casefold()
        )
        if any(
            name.startswith(near_prefix)
            and name.endswith(".pyd")
            and name not in expected_names
            for name in folded_members
        ):
            issues.append("protected_native_module_untrusted")

    for name in folded_members:
        if (
            name.endswith(".pyd")
            and any(
                name.startswith(f"{root}/")
                for root in folded_roots
            )
            and name not in allowed_native_members
        ):
            issues.append("protected_native_module_unexpected")

    allowed_plaintext = packaged_plaintext_python_allowlist
    for name in folded_members:
        if not name.endswith(".py"):
            continue
        if name.startswith(RUNTIME_INVENTORY_PREFIX):
            continue
        if name not in allowed_plaintext:
            issues.append("plaintext_sensitive_module")
        if name in build_only_sources:
            issues.append("build_only_source_present")

    shell_infos = [
        info
        for folded, _, info in member_entries
        if folded == overlay_source_folded
    ]
    if not shell_infos:
        issues.append("protected_overlay_shell_missing")
    elif len(shell_infos) != 1:
        issues.append("protected_overlay_shell_invalid")
    else:
        try:
            shell = archive.read(shell_infos[0])
            shell_text = shell.decode("utf-8-sig")
        except (OSError, RuntimeError, UnicodeError, zipfile.BadZipFile):
            issues.append("protected_overlay_shell_unreadable")
        else:
            normalized_shell = shell_text.replace("\r\n", "\n").replace("\r", "\n")
            actual_shell_sha256 = hashlib.sha256(
                normalized_shell.encode("utf-8")
            ).hexdigest()
            if actual_shell_sha256 != overlay_sha256:
                issues.append("protected_overlay_shell_invalid")

    for relative in encrypted_excludes:
        if relative in folded_members:
            issues.append("excluded_sensitive_tool_present")
    return issues


def _network_diagnostic_text_issues(
    archive: zipfile.ZipFile,
) -> list[str]:
    """Reject store assets and scan distribution text without exposing matches."""
    issues: list[str] = []
    for info in archive.infolist():
        name = _normalized_member(info.filename)
        if name is None or info.is_dir():
            continue
        if name.casefold() in {
            "hero_ref/assets/shop_qr.png",
            "购买与续费/店铺二维码.png",
            "购买与续费/打开店铺.html",
        }:
            issues.append(f"network_diagnostic_store_asset_forbidden: {name}")
        if PurePosixPath(name.casefold()).suffix not in DISTRIBUTION_TEXT_SUFFIXES:
            continue
        try:
            data = archive.read(info)
        except (OSError, RuntimeError, zipfile.BadZipFile):
            issues.append(f"network_diagnostic_text_unreadable: {name}")
            continue
        for forbidden in NETWORK_DIAGNOSTIC_FORBIDDEN_TEXT:
            count = _distribution_text_occurrences(data, forbidden)
            issues.extend(
                f"network_diagnostic_forbidden_text: {name}" for _ in range(count)
            )
    return issues


def _trusted_asset_members(
    archive: zipfile.ZipFile,
    specs: tuple[tuple[str, Path], ...],
) -> tuple[set[str], list[str]]:
    """Return members covered by an exact, externally reviewed asset inventory.

    Some immutable third-party binaries contain their upstream builder's path.
    They may bypass only the generic ``C:\\Users\\example heuristic, and only when
    the complete ZIP subtree matches the tracked manifest byte-for-byte. Exact
    forbidden build/user paths remain blocked for every member.
    """
    trusted: set[str] = set()
    issues: list[str] = []
    for raw_prefix, manifest_path in specs:
        normalized = _normalized_member(str(raw_prefix).strip().rstrip("\\/"))
        if normalized is None:
            issues.append("trusted_asset_inventory_mismatch")
            continue
        prefix = normalized.rstrip("/") + "/"
        label = prefix.rstrip("/")
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            expected = manifest["inventory"]
            expected_identity = (
                int(expected["file_count"]),
                int(expected["total_bytes"]),
                str(expected["inventory_sha256"]),
            )
            if manifest.get("schema_version") != 1:
                raise ValueError("unsupported manifest")
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            issues.append(f"trusted_asset_inventory_mismatch: {label}")
            continue

        rows: list[tuple[str, str, int, str]] = []
        selected: set[str] = set()
        casefolded: set[str] = set()
        invalid = False
        for info in archive.infolist():
            name = _normalized_member(info.filename)
            if name is None or not name.casefold().startswith(prefix.casefold()):
                continue
            unix_mode = (info.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                invalid = True
                continue
            if info.is_dir():
                continue
            relative = name[len(prefix) :]
            folded = relative.casefold()
            if not relative or folded in casefolded:
                invalid = True
                continue
            casefolded.add(folded)
            try:
                data = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                invalid = True
                continue
            rows.append(
                (
                    folded,
                    relative,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                )
            )
            selected.add(name)

        rows.sort(key=lambda row: row[0])
        serialized = "".join(
            f"{relative}|{size}|{sha256}\n" for _folded, relative, size, sha256 in rows
        ).encode("utf-8")
        actual_identity = (
            len(rows),
            sum(row[2] for row in rows),
            hashlib.sha256(serialized).hexdigest(),
        )
        if invalid or actual_identity != expected_identity:
            issues.append(f"trusted_asset_inventory_mismatch: {label}")
            continue
        trusted.update(selected)
    return trusted, issues


def _local_integrity_issues(
    archive: zipfile.ZipFile,
    authority: dict[str, object],
) -> list[str]:
    """Verify the build-receipt-bound Ed25519 manifest and every covered member."""

    try:
        if (
            type(authority) is not dict
            or set(authority) != BUILD_AUDIT_LOCAL_INTEGRITY_FIELDS
            or authority["native_gate_verified"] is not True
            or type(authority["entry_count"]) is not int
            or authority["entry_count"] <= 0
        ):
            raise ValueError("authority")
        manifest_name = str(authority["manifest_name"])
        signature_name = str(authority["signature_name"])
        for name in (manifest_name, signature_name):
            path = PurePosixPath(name)
            if (
                not name
                or path.as_posix() != name
                or path.is_absolute()
                or any(part in ("", ".", "..") for part in path.parts)
            ):
                raise ValueError("member")
        public_key = base64.b64decode(
            str(authority["public_key_b64"]), validate=True
        )
        if (
            len(public_key) != 32
            or hashlib.sha256(public_key).hexdigest()
            != _require_sha256(authority["public_key_sha256"])
        ):
            raise ValueError("key")
        expected_manifest_sha256 = _require_sha256(authority["manifest_sha256"])
    except (TypeError, ValueError, binascii.Error):
        return ["local_integrity_authority_invalid"]

    members: dict[str, list[zipfile.ZipInfo]] = {}
    for info in archive.infolist():
        normalized = _normalized_member(info.filename)
        if normalized is not None and not info.is_dir():
            members.setdefault(normalized.casefold(), []).append(info)

    def read_one(name: str) -> bytes:
        matches = members.get(name.casefold(), [])
        if len(matches) != 1:
            raise ValueError("member_count")
        return archive.read(matches[0])

    try:
        manifest_raw = read_one(manifest_name)
        signature_raw = read_one(signature_name)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        return ["local_integrity_metadata_missing"]
    if hashlib.sha256(manifest_raw).hexdigest() != expected_manifest_sha256:
        return ["local_integrity_manifest_hash_mismatch"]

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError:
        return ["local_integrity_signature_verifier_unavailable"]
    try:
        signature = base64.b64decode(signature_raw.strip(), validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, manifest_raw)
    except (InvalidSignature, TypeError, ValueError, binascii.Error):
        return ["local_integrity_signature_invalid"]

    try:
        manifest = json.loads(
            manifest_raw.decode("utf-8"), object_pairs_hook=_strict_json_object
        )
        if (
            type(manifest) is not dict
            or set(manifest)
            != {
                "entries",
                "manifest_version",
                "public_key_sha256",
                "release_id",
                "schema",
            }
            or manifest["schema"] != "bidking_local_integrity_manifest_v1"
            or manifest["manifest_version"] != 1
            or type(manifest["release_id"]) is not str
            or not manifest["release_id"]
            or manifest["public_key_sha256"]
            != hashlib.sha256(public_key).hexdigest()
            or type(manifest["entries"]) is not list
            or len(manifest["entries"]) != authority["entry_count"]
        ):
            raise ValueError("manifest")
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return ["local_integrity_manifest_invalid"]

    issues: list[str] = []
    seen: set[str] = set()
    roles: dict[str, int] = {}
    for entry in manifest["entries"]:
        try:
            if (
                type(entry) is not dict
                or set(entry) != {"bytes", "path", "role", "sha256"}
                or type(entry["bytes"]) is not int
                or entry["bytes"] < 0
                or type(entry["role"]) is not str
                or not entry["role"]
            ):
                raise ValueError("entry")
            path_text = entry["path"]
            path = PurePosixPath(path_text) if type(path_text) is str else None
            if (
                path is None
                or not path_text
                or path.as_posix() != path_text
                or path.is_absolute()
                or any(part in ("", ".", "..") for part in path.parts)
                or path_text.casefold() in seen
            ):
                raise ValueError("entry_path")
            seen.add(path_text.casefold())
            roles[entry["role"]] = roles.get(entry["role"], 0) + 1
            expected_sha256 = _require_sha256(entry["sha256"])
            payload = read_one(path_text)
        except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile):
            issues.append("local_integrity_member_invalid")
            continue
        if len(payload) != entry["bytes"]:
            issues.append("local_integrity_member_size_mismatch")
        elif hashlib.sha256(payload).hexdigest() != expected_sha256:
            issues.append("local_integrity_member_hash_mismatch")
    covered_prefixes = (
        "runtime/python/",
        "scripts/",
        "hero_ref/src/",
        "hero_ref/tools/",
        "src/",
        "data/processed/",
        "data/raw/tables/",
    )
    covered_fixed = {
        "bidkinglive.exe",
        "activation_service_endpoints.json",
        "activation_http_e2ee.json",
        (
            "external_references/auctionanalyzer4.13.3/_decompiled/"
            "mapbidcalculator/mapbidcalculator/models/staticdata.cs"
        ),
    }
    archive_covered = {
        name
        for name in members
        if name in covered_fixed or name.startswith(covered_prefixes)
    }
    if archive_covered != seen:
        issues.append("local_integrity_coverage_mismatch")
    required_role_minimums = {
        "activation-endpoint": 2,
        "engine-native": 3,
        "entry": 1,
        "launcher": 1,
        "overlay-native": 1,
        "parser-native": 1,
        "proprietary-data": 1,
        "runtime": 1,
        "secure-native": 1,
        "trust-root-native": 1,
    }
    if any(
        roles.get(role, 0) < minimum
        for role, minimum in required_role_minimums.items()
    ):
        issues.append("local_integrity_role_coverage_invalid")
    return issues


def audit_zip(
    path: Path,
    *,
    forbidden_paths: tuple[str, ...] = (),
    trusted_assets: tuple[tuple[str, Path], ...] = (),
    product_profile: str | None = None,
    protection_manifest: Path | None = None,
    release_contract: Path | None = None,
    runtime_receipt: Path | None = None,
    runtime_approval: Path | None = None,
    bkenc_receipt: Path | None = None,
    bkenc_key: bytes | None = None,
    local_integrity: dict[str, object] | None = None,
    expected_manifest_fields: tuple[tuple[str, str], ...] = (),
) -> list[str]:
    """Return stable issue labels without exposing matched private path contents."""
    issues: list[str] = []
    needles = _forbidden_needles(forbidden_paths)
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"zip_unreadable: {type(exc).__name__}"]

    with archive:
        structure_issues = _archive_structure_issues(archive)
        issues.extend(structure_issues)
        if _release_member_identity_failed(structure_issues):
            return issues
        build_fields = _manifest_fields(archive) or {}
        expected_field_set = frozenset(expected_manifest_fields)
        portable_no_encrypt = (
            protection_manifest is None
            and release_contract is None
            and local_integrity is None
            and bkenc_receipt is None
            and product_profile == "standard"
            and build_fields.get("productprofile") == "standard"
            and build_fields.get("encrypted", "").casefold() == "false"
            and ("ProductProfile", "standard") in expected_field_set
            and ("Encrypted", "False") in expected_field_set
        )
        high_protection_audit = not portable_no_encrypt
        manifest_profile = build_fields.get("productprofile")
        manifest_profile = manifest_profile.lower() if manifest_profile else None
        expected_profile = (
            str(product_profile).strip().lower()
            if product_profile is not None
            else manifest_profile
        )
        if (
            product_profile is not None
            and manifest_profile != expected_profile
        ):
            issues.append("product_profile_manifest_mismatch")
        if release_contract is not None:
            contract_issues = _release_contract_issues(
                archive,
                release_contract,
                expected_manifest_fields,
                runtime_receipt,
                runtime_approval,
                bkenc_receipt,
                bkenc_key,
            )
            issues.extend(contract_issues)
            if _release_member_identity_failed(contract_issues):
                return issues
        if local_integrity is not None:
            issues.extend(_local_integrity_issues(archive, local_integrity))
        if expected_profile == "network-diagnostic":
            issues.extend(_network_diagnostic_text_issues(archive))
        if (
            protection_manifest is not None
            or build_fields.get("encrypted", "").casefold() == "true"
        ):
            manifest_path = protection_manifest or Path(__file__).with_name(
                "client_protection_manifest.json"
            )
            issues.extend(
                _client_protection_issues(archive, manifest_path, build_fields)
            )
        trusted_members, trusted_issues = _trusted_asset_members(
            archive, trusted_assets
        )
        issues.extend(trusted_issues)
        protected_addon_loaders: list[str] = []
        protected_addon_bootstrap_present = False
        for info in archive.infolist():
            name = _normalized_member(info.filename)
            if name is None:
                issues.append("unsafe_member_path")
                continue
            lowered_name = name.lower()
            is_project = lowered_name.startswith(PROJECT_PREFIXES)
            if high_protection_audit and lowered_name in FORBIDDEN_PLAINTEXT_WSS_MEMBERS:
                issues.append("plaintext_wss_module")
            if high_protection_audit and lowered_name in FORBIDDEN_PLAINTEXT_LAUNCHER_MEMBERS:
                issues.append("plaintext_launcher_module")
            if lowered_name == BUNDLED_NPCAP_INSTALLER_MEMBER:
                issues.append("bundled_npcap_installer")
            if high_protection_audit and lowered_name == PROTECTED_ADDON_BOOTSTRAP_MEMBER:
                protected_addon_bootstrap_present = True
            if lowered_name.startswith(
                "scripts/_local_compat_addon"
            ) and lowered_name.endswith(".pyd") and high_protection_audit:
                if lowered_name.endswith(PROTECTED_ADDON_LOADER_SUFFIX):
                    protected_addon_loaders.append(name)
                else:
                    issues.append("incompatible_wss_addon_bytecode")
            parts = PurePosixPath(lowered_name).parts
            if high_protection_audit and is_project and (
                "__pycache__" in parts or lowered_name.endswith((".pyc", ".pyo"))
            ):
                issues.append(f"project_bytecode: {name}")
            if high_protection_audit and is_project and lowered_name.endswith((".c", ".cc", ".cpp", ".cxx")):
                issues.append("generated_native_source")
            if high_protection_audit and is_project and lowered_name.endswith((".pxd", ".pyx", ".pxi")):
                issues.append("cython_declaration_source")
            if high_protection_audit and is_project and lowered_name.endswith((".obj", ".lib", ".exp", ".pdb")):
                issues.append("project_native_build_artifact")
            if info.is_dir():
                continue
            try:
                data = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                issues.append(f"member_unreadable: {name} ({type(exc).__name__})")
                continue
            lowered = data.lower()
            if lowered_name.startswith(RUNTIME_PREFIX):
                if lowered_name.endswith(".pyc"):
                    issues.extend(_runtime_bytecode_issues(data, name, needles))
                elif any(needle and needle in lowered for needle in needles):
                    issues.append(f"private_build_path: {name}")
                continue
            if high_protection_audit and lowered_name == PUBLIC_LAUNCHER_MEMBER and any(
                term in lowered for term in FORBIDDEN_PUBLIC_LAUNCHER_TERMS
            ):
                issues.append("public_launcher_technical_detail")
            if high_protection_audit and lowered_name == PUBLIC_DIAGNOSTICS_MEMBER and any(
                term in lowered for term in FORBIDDEN_PUBLIC_DIAGNOSTICS_TERMS
            ):
                issues.append("public_diagnostics_technical_detail")
            if high_protection_audit and lowered_name == PROTECTED_ADDON_BOOTSTRAP_MEMBER and any(
                term in lowered for term in FORBIDDEN_PROTECTED_ADDON_TERMS
            ):
                issues.append("reversible_wss_addon_payload")
            if high_protection_audit and (
                lowered_name.startswith(PROTECTED_CLIENT_LAUNCHER_PREFIX)
                and lowered_name.endswith(".pyd")
                and any(
                    term in lowered
                    for term in FORBIDDEN_REVERSIBLE_LIFECYCLE_TERMS
                )
            ):
                issues.append("reversible_client_lifecycle_payload")
            if high_protection_audit and (
                lowered_name.startswith(PROTECTED_CLIENT_LAUNCHER_PREFIX)
                and lowered_name.endswith(".pyd")
                and any(term not in lowered for term in REQUIRED_CLIENT_LAUNCHER_TERMS)
            ):
                issues.append("client_lifecycle_receipt_missing")
            if high_protection_audit and (
                lowered_name.startswith(NATIVE_KERNEL_PREFIX)
                and lowered_name.endswith(".pyd")
                and any(
                    term not in lowered for term in REQUIRED_NATIVE_LIFECYCLE_TERMS
                )
            ):
                issues.append("native_lifecycle_helper_missing")
            generic_private_path = (
                name not in trusted_members and _contains_windows_user_path(data)
            )
            explicit_private_path = any(
                needle and needle in lowered for needle in needles
            )
            if generic_private_path or explicit_private_path:
                issues.append(f"private_build_path: {name}")
        if high_protection_audit and protected_addon_bootstrap_present and len(protected_addon_loaders) != 1:
            issues.append("protected_wss_addon_loader_missing")
    return issues


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("duplicate_or_invalid_json_key")
        result[key] = value
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid_sha256")
    return value


def _resolve_receipt_source_file(
    source_root: Path, identity: object
) -> Path:
    if type(identity) is not dict or set(identity) != BUILD_AUDIT_FILE_IDENTITY_FIELDS:
        raise ValueError("invalid_source_file_identity")
    relative = identity["path"]
    if type(relative) is not str or not relative:
        raise ValueError("invalid_source_relative_path")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or relative != pure.as_posix()
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise ValueError("unsafe_source_relative_path")
    root = source_root.resolve(strict=True)
    candidate = root.joinpath(*pure.parts).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("source_path_escape") from exc
    if not candidate.is_file() or _sha256_file(candidate) != _require_sha256(
        identity["sha256"]
    ):
        raise ValueError("source_file_identity_mismatch")
    return candidate


def _resolve_receipt_adjacent_file(
    receipt_path: Path, identity: object
) -> Path:
    if (
        type(identity) is not dict
        or set(identity) != BUILD_AUDIT_ADJACENT_IDENTITY_FIELDS
    ):
        raise ValueError("invalid_adjacent_file_identity")
    name = identity["name"]
    if (
        type(name) is not str
        or not name
        or Path(name).name != name
        or "/" in name
        or "\\" in name
    ):
        raise ValueError("unsafe_adjacent_file_name")
    parent = receipt_path.resolve(strict=True).parent
    candidate = (parent / name).resolve(strict=True)
    if candidate.parent != parent or not candidate.is_file():
        raise ValueError("adjacent_file_missing")
    if _sha256_file(candidate) != _require_sha256(identity["sha256"]):
        raise ValueError("adjacent_file_identity_mismatch")
    return candidate


def _validate_release_availability_receipt(
    value: object,
    release_contract_path: Path,
) -> dict[str, object]:
    """Validate support-safe Stage/unpacked-ZIP product availability authority."""

    try:
        contract = json.loads(
            release_contract_path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
        availability = contract["release_availability"]
        if (
            not _is_exact_type(contract.get("contract_version"), int)
            or contract["contract_version"] < 16
            or not _is_exact_type(availability, dict)
            or set(availability)
            != {
                "receipt_schema",
                "receipt_version",
                "captures",
                "policy_census",
                "route_scope",
                "current_authority",
            }
            or availability["receipt_schema"]
            != "bidking_release_availability_receipt_v1"
            or availability["receipt_version"] != 1
        ):
            raise ValueError("availability contract identity")
        expected_captures = availability["captures"]
        if (
            not _is_exact_type(expected_captures, list)
            or len(expected_captures) != 5
            or any(
                not _is_exact_type(item, dict)
                or set(item) != {"bytes", "sha256"}
                or not _is_exact_type(item["bytes"], int)
                or item["bytes"] <= 0
                or re.fullmatch(r"[0-9a-f]{64}", item["sha256"] or "") is None
                for item in expected_captures
            )
        ):
            raise ValueError("availability capture contract")
        expected_census = availability["policy_census"]
        expected_routes = availability["route_scope"]
        current_model = _availability_uses_current_model(availability)
        if (
            not _is_exact_type(expected_census, dict)
            or set(expected_census) != {"policy_roots", "direct_root", "source_map"}
            or not _availability_census_is_exact(expected_census, current_model=current_model)
            or not _release_availability_route_contract_is_exact(expected_routes, current_model=current_model)
        ):
            raise ValueError("availability census contract")
        for layer in ("direct_root", "source_map"):
            layer_value = expected_census[layer]
            if (
                not _is_exact_type(layer_value, dict)
                or set(layer_value)
                != {
                    "complete",
                    "degraded",
                    "sampleable",
                    "missing",
                    "empty",
                    "unsupported",
                    "unsupported_ids_sha256",
                }
                or any(
                    not _is_exact_type(layer_value[key], int)
                    for key in (
                        "complete",
                        "degraded",
                        "sampleable",
                        "missing",
                        "empty",
                        "unsupported",
                    )
                )
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    layer_value["unsupported_ids_sha256"] or "",
                )
                is None
            ):
                raise ValueError("availability census layer contract")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("release_availability_contract_invalid") from exc

    if (
        not _is_exact_type(value, dict)
        or set(value) != RELEASE_AVAILABILITY_FIELDS
        or value.get("schema") != availability["receipt_schema"]
        or value.get("receipt_version") != availability["receipt_version"]
        or value.get("status") != "pass"
    ):
        raise ValueError("release_availability_receipt_invalid")
    captures = value["captures"]
    if not _is_exact_type(captures, list) or len(captures) != len(expected_captures):
        raise ValueError("release_availability_capture_invalid")
    for actual, expected in zip(captures, expected_captures, strict=True):
        if (
            not _is_exact_type(actual, dict)
            or set(actual) != RELEASE_AVAILABILITY_CAPTURE_FIELDS
            or actual.get("verdict") != "pass"
            or actual.get("bytes") != expected["bytes"]
            or actual.get("sha256") != expected["sha256"]
        ):
            raise ValueError("release_availability_capture_invalid")

    identities: list[str] = []
    for key in ("stage", "extracted_zip"):
        identity = value[key]
        if (
            not _is_exact_type(identity, dict)
            or set(identity) != RELEASE_AVAILABILITY_PACKAGE_FIELDS
            or identity.get("verdict") != "pass"
            or re.fullmatch(r"[0-9a-f]{64}", identity.get("identity_sha256") or "")
            is None
        ):
            raise ValueError("release_availability_package_invalid")
        identities.append(identity["identity_sha256"])
    if identities[0] != identities[1]:
        raise ValueError("release_availability_package_mismatch")

    census = value["policy_census"]
    if (
        not _is_exact_type(census, dict)
        or set(census) != {"policy_roots", "direct_root", "source_map", "verdict"}
        or census.get("verdict") != "pass"
        or {key: census[key] for key in expected_census} != expected_census
        or not _availability_census_is_exact({key: census[key] for key in expected_census}, current_model=current_model)
    ):
        raise ValueError("release_availability_census_invalid")
    routes = value["route_scope"]
    if (
        not _is_exact_type(routes, dict)
        or set(routes) != RELEASE_AVAILABILITY_ROUTE_COUNT_FIELDS | {"verdict"}
        or routes.get("verdict") != "pass"
        or any(routes[key] != expected_routes[key] for key in RELEASE_AVAILABILITY_ROUTE_COUNT_FIELDS)
    ):
        raise ValueError("release_availability_route_invalid")
    chains = value["chains"]
    if (
        not _is_exact_type(chains, dict)
        or set(chains) != RELEASE_AVAILABILITY_CHAIN_FIELDS
        or any(chains[key] != "pass" for key in RELEASE_AVAILABILITY_CHAIN_FIELDS)
    ):
        raise ValueError("release_availability_chain_invalid")
    return dict(value)


def _classify_build_audit_receipt(
    payload: dict[str, object],
) -> tuple[bool, bool, bool, bool, bool]:
    schema = payload.get("schema")
    receipt_version = payload.get("receipt_version")
    if type(schema) is not str or type(receipt_version) is not int:
        raise ValueError("invalid_build_receipt_identity")
    expected_fields = {
        (BUILD_AUDIT_RECEIPT_SCHEMA, 5): BUILD_AUDIT_RECEIPT_FIELDS,
        (NATIVE_BUILD_AUDIT_RECEIPT_SCHEMA, 4): NATIVE_BUILD_AUDIT_RECEIPT_FIELDS,
        (SIGNED_BUILD_AUDIT_RECEIPT_SCHEMA, 3): SIGNED_BUILD_AUDIT_RECEIPT_FIELDS,
        (PREVIOUS_BUILD_AUDIT_RECEIPT_SCHEMA, 2): (
            PREVIOUS_BUILD_AUDIT_RECEIPT_FIELDS
        ),
        (LEGACY_BUILD_AUDIT_RECEIPT_SCHEMA, 1): LEGACY_BUILD_AUDIT_RECEIPT_FIELDS,
    }.get((schema, receipt_version))
    if expected_fields is None or set(payload) != expected_fields:
        raise ValueError("invalid_build_receipt_fields")
    return (
        receipt_version == 5,
        receipt_version == 4,
        receipt_version == 3,
        receipt_version == 2,
        receipt_version == 1,
    )


def load_build_audit_receipt(
    receipt_path: Path, source_root: Path
) -> dict[str, object]:
    """Load one strict, path-safe authority receipt for an immutable ZIP audit."""

    raw = receipt_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("receipt_bom_forbidden")
    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_json_object)
    if type(payload) is not dict:
        raise ValueError("invalid_build_receipt_fields")
    current_receipt, native_receipt, signed_receipt, previous_receipt, legacy_receipt = (
        _classify_build_audit_receipt(payload)
    )
    if signed_receipt:
        raise ValueError("signed_build_receipt_not_current")
    integrity_receipt = current_receipt or native_receipt or previous_receipt
    native_authority_receipt = current_receipt or native_receipt
    if (
        type(payload["encrypted"]) is not bool
        or payload["product_profile"] not in ("standard", "network-diagnostic")
        or type(payload["source_commit"]) is not str
        or re.fullmatch(r"[0-9a-f]{40}", payload["source_commit"]) is None
        or type(payload["source_branch"]) is not str
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", payload["source_branch"]
        )
        is None
    ):
        raise ValueError("invalid_build_receipt_identity")
    root = source_root.resolve(strict=True)
    try:
        git_result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("source_commit_readback_failed") from exc
    if (
        git_result.returncode != 0
        or git_result.stdout.strip() != payload["source_commit"]
    ):
        raise ValueError("source_commit_readback_mismatch")

    zip_name = payload["zip_name"]
    if (
        type(zip_name) is not str
        or not zip_name.lower().endswith(".zip")
        or Path(zip_name).name != zip_name
        or "/" in zip_name
        or "\\" in zip_name
    ):
        raise ValueError("unsafe_receipt_zip_name")
    if type(payload["zip_bytes"]) is not int or payload["zip_bytes"] <= 0:
        raise ValueError("invalid_receipt_zip_size")
    zip_path = receipt_path.resolve(strict=True).parent / zip_name
    zip_path = zip_path.resolve(strict=True)
    if (
        not zip_path.is_file()
        or zip_path.stat().st_size != payload["zip_bytes"]
        or _sha256_file(zip_path) != _require_sha256(payload["zip_sha256"])
    ):
        raise ValueError("receipt_zip_identity_mismatch")

    expected = payload["expected_manifest_fields"]
    if type(expected) is not dict or not expected:
        raise ValueError("invalid_expected_manifest_fields")
    for key, value in expected.items():
        if type(key) is not str or not key or type(value) is not str or not value:
            raise ValueError("invalid_expected_manifest_field")
    required_expected = {
        "SourceCommit": payload["source_commit"],
        "SourceBranch": payload["source_branch"],
        "ProductProfile": payload["product_profile"],
        "Encrypted": str(payload["encrypted"]),
    }
    if payload["encrypted"]:
        required_expected.update(
            {
                "PackageInputsDirty": "False",
                "EncryptedUploadConfig": "True",
                "ReleaseKeyCacheVerified": "True",
                "UploadConfigRoundTripVerified": "True",
                "BundledCABundle": "True",
            }
        )
        if integrity_receipt:
            required_expected["LocalIntegrityVerified"] = "True"
        if native_authority_receipt:
            required_expected.update(
                {
                    "NativeBinaryDistribution": "unsigned",
                    "NativeBinaryVerified": "True",
                }
            )
    if any(expected.get(key) != value for key, value in required_expected.items()):
        raise ValueError("receipt_manifest_authority_mismatch")

    runtime_approval = _resolve_receipt_source_file(
        source_root, payload["runtime_approval"]
    )
    runtime_receipt = _resolve_receipt_adjacent_file(
        receipt_path, payload["runtime_receipt"]
    )

    release_contract_raw = payload["release_contract"]
    local_integrity_contract_raw = payload.get("local_integrity_contract")
    local_integrity_raw = payload.get("local_integrity")
    bkenc_receipt_raw = payload["bkenc_receipt"]
    if payload["encrypted"]:
        protection_manifest = _resolve_receipt_source_file(
            source_root, payload["protection_manifest"]
        )
        release_contract = _resolve_receipt_source_file(
            source_root, release_contract_raw
        )
        if integrity_receipt:
            local_integrity_contract = _resolve_receipt_source_file(
                source_root, local_integrity_contract_raw
            )
            if (
                type(local_integrity_raw) is not dict
                or set(local_integrity_raw) != BUILD_AUDIT_LOCAL_INTEGRITY_FIELDS
                or type(local_integrity_raw["entry_count"]) is not int
                or local_integrity_raw["entry_count"] <= 0
                or local_integrity_raw["native_gate_verified"] is not True
            ):
                raise ValueError("invalid_local_integrity_receipt")
            for key in (
                "manifest_name",
                "signature_name",
            ):
                value = local_integrity_raw[key]
                if (
                    type(value) is not str
                    or PurePosixPath(value).as_posix() != value
                    or PurePosixPath(value).is_absolute()
                    or any(
                        part in ("", ".", "..")
                        for part in PurePosixPath(value).parts
                    )
                ):
                    raise ValueError("invalid_local_integrity_member")
            public_key_b64 = local_integrity_raw["public_key_b64"]
            if type(public_key_b64) is not str:
                raise ValueError("invalid_local_integrity_public_key")
            try:
                public_key = base64.b64decode(public_key_b64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("invalid_local_integrity_public_key") from exc
            if (
                len(public_key) != 32
                or hashlib.sha256(public_key).hexdigest()
                != _require_sha256(local_integrity_raw["public_key_sha256"])
            ):
                raise ValueError("invalid_local_integrity_public_key")
            _require_sha256(local_integrity_raw["manifest_sha256"])
            local_integrity = dict(local_integrity_raw)
            if (
                expected.get("LocalIntegrityPublicKeySHA256")
                != local_integrity["public_key_sha256"]
                or expected.get("LocalIntegrityManifestSHA256")
                != local_integrity["manifest_sha256"]
                or expected.get("LocalIntegrityEntryCount")
                != str(local_integrity["entry_count"])
            ):
                raise ValueError("receipt_manifest_authority_mismatch")
        else:
            try:
                legacy_contract = json.loads(
                    release_contract.read_text(encoding="utf-8"),
                    object_pairs_hook=_strict_json_object,
                )
                legacy_contract_version = legacy_contract["contract_version"]
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                raise ValueError("legacy_release_contract_invalid") from None
            if (
                type(legacy_contract_version) is not int
                or legacy_contract_version >= 13
            ):
                raise ValueError("legacy_encrypted_integrity_authority_missing")
            local_integrity_contract = None
            local_integrity = None
        if native_authority_receipt:
            native_binary_contract = _resolve_receipt_source_file(
                source_root, payload["native_binary_contract"]
            )
            native_binary_receipt = _resolve_receipt_adjacent_file(
                receipt_path, payload["native_binary_receipt"]
            )
            native_signing_policy = None
            try:
                native_receipt = _NATIVE_HARDENING.load_receipt_bytes(
                    native_binary_receipt.read_bytes()
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                raise ValueError("invalid_native_binary_receipt") from None
            if (
                type(native_receipt) is not dict
                or set(native_receipt) != BUILD_AUDIT_NATIVE_BINARY_RECEIPT_FIELDS
                or type(native_receipt.get("schema")) is not str
                or native_receipt["schema"] != "bidking_native_binary_receipt_v2"
                or type(native_receipt.get("receipt_version")) is not int
                or native_receipt["receipt_version"] != 2
                or native_receipt.get("distribution") != "unsigned"
                or type(native_receipt.get("member_count")) is not int
                or native_receipt["member_count"] <= 0
                or type(native_receipt.get("members")) is not list
                or expected.get("NativeBinaryContractSHA256")
                != _sha256_file(native_binary_contract)
                or expected.get("NativeBinaryDistribution") != "unsigned"
                or expected.get("NativeBinaryReceiptSHA256")
                != _sha256_file(native_binary_receipt)
                or expected.get("NativeBinaryCount")
                != str(native_receipt["member_count"])
                or native_receipt.get("contract_sha256")
                != _sha256_file(native_binary_contract)
            ):
                raise ValueError("native_binary_identity_mismatch")
        else:
            native_binary_contract = None
            native_signing_policy = None
            native_binary_receipt = None
            if previous_receipt:
                try:
                    previous_contract = json.loads(
                        release_contract.read_text(encoding="utf-8"),
                        object_pairs_hook=_strict_json_object,
                    )
                    previous_contract_version = previous_contract["contract_version"]
                except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                    raise ValueError("previous_release_contract_invalid") from None
                if (
                    type(previous_contract_version) is not int
                    or previous_contract_version >= 14
                ):
                    raise ValueError("previous_encrypted_native_authority_missing")
        if current_receipt:
            release_availability = _validate_release_availability_receipt(
                payload["release_availability"],
                release_contract,
            )
        else:
            release_availability = None
            try:
                native_contract = json.loads(
                    release_contract.read_text(encoding="utf-8"),
                    object_pairs_hook=_strict_json_object,
                )
                native_contract_version = native_contract["contract_version"]
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                raise ValueError("previous_release_contract_invalid") from None
            if (
                not _is_exact_type(native_contract_version, int)
                or native_contract_version >= 16
            ):
                raise ValueError("previous_release_availability_authority_missing")
        bkenc_receipt = _resolve_receipt_adjacent_file(
            receipt_path, bkenc_receipt_raw
        )
    else:
        if (
            payload["protection_manifest"] is not None
            or
            release_contract_raw is not None
            or (
                integrity_receipt
                and (
                    local_integrity_contract_raw is not None
                    or local_integrity_raw is not None
                )
            )
            or bkenc_receipt_raw is not None
            or (
                native_authority_receipt
                and (
                    payload["native_binary_contract"] is not None
                    or payload["native_binary_receipt"] is not None
                )
            )
            or (current_receipt and payload["release_availability"] is not None)
        ):
            raise ValueError("unexpected_encryption_receipt")
        protection_manifest = None
        release_contract = None
        local_integrity_contract = None
        local_integrity = None
        native_binary_contract = None
        native_signing_policy = None
        native_binary_receipt = None
        bkenc_receipt = None
        release_availability = None

    trusted_assets_raw = payload["trusted_assets"]
    if type(trusted_assets_raw) is not list:
        raise ValueError("invalid_trusted_assets")
    trusted_assets: list[tuple[str, Path]] = []
    seen_prefixes: set[str] = set()
    for item in trusted_assets_raw:
        if type(item) is not dict or set(item) != BUILD_AUDIT_TRUSTED_ASSET_FIELDS:
            raise ValueError("invalid_trusted_asset")
        prefix = item["prefix"]
        prefix_path = PurePosixPath(prefix) if type(prefix) is str else None
        if (
            type(prefix) is not str
            or not prefix
            or prefix_path is None
            or prefix != prefix_path.as_posix()
            or prefix_path.is_absolute()
            or any(part in ("", ".", "..") for part in prefix_path.parts)
            or prefix in seen_prefixes
        ):
            raise ValueError("invalid_trusted_asset_prefix")
        seen_prefixes.add(prefix)
        trusted_assets.append(
            (
                prefix,
                _resolve_receipt_source_file(
                    source_root,
                    {"path": item["path"], "sha256": item["sha256"]},
                ),
            )
        )

    forbidden_sources = payload["forbidden_path_sources"]
    if (
        type(forbidden_sources) is not list
        or any(type(item) is not str for item in forbidden_sources)
        or len(set(forbidden_sources)) != len(forbidden_sources)
        or not set(forbidden_sources).issubset(BUILD_AUDIT_FORBIDDEN_PATH_SOURCES)
        or "source_root" not in forbidden_sources
    ):
        raise ValueError("invalid_forbidden_path_sources")
    forbidden_paths = [str(source_root.resolve(strict=True))]
    if "current_user_profile" in forbidden_sources:
        current_profile = os.environ.get("USERPROFILE")
        if current_profile:
            forbidden_paths.append(current_profile)

    return {
        "zip_path": zip_path,
        "forbidden_paths": tuple(forbidden_paths),
        "trusted_assets": tuple(trusted_assets),
        "product_profile": payload["product_profile"],
        "protection_manifest": protection_manifest,
        "release_contract": release_contract,
        "local_integrity_contract": local_integrity_contract,
        "local_integrity": local_integrity,
        "native_binary_contract": native_binary_contract,
        "native_signing_policy": native_signing_policy,
        "native_binary_receipt": native_binary_receipt,
        "runtime_receipt": runtime_receipt,
        "runtime_approval": runtime_approval,
        "bkenc_receipt": bkenc_receipt,
        "release_availability": release_availability,
        "encrypted": payload["encrypted"],
        "expected_manifest_fields": tuple(expected.items()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", type=Path, nargs="?")
    parser.add_argument(
        "--build-receipt",
        type=Path,
        help=(
            "strict build-produced audit authority receipt; replaces manual ZIP, "
            "manifest, receipt, asset and expected-field argument reconstruction"
        ),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        help="source root used to resolve and hash receipt-bound tracked inputs",
    )
    parser.add_argument(
        "--write-runtime-receipt",
        nargs=3,
        type=Path,
        metavar=("RUNTIME_ROOT", "RECEIPT", "APPROVAL"),
        help=(
            "freeze the final Stage runtime/python tree only when it matches "
            "the source-reviewed approval manifest"
        ),
    )
    parser.add_argument(
        "--write-canonical-zip",
        nargs=2,
        type=Path,
        metavar=("STAGE_ROOT", "ZIP"),
        help="write a deterministic POSIX-name ZIP from the verified Stage tree",
    )
    parser.add_argument(
        "--write-bkenc-receipt",
        nargs=4,
        type=Path,
        metavar=("ZIP", "RECEIPT", "CONTRACT", "KEY_FILE"),
        help=(
            "authenticate every contract-declared BKENC member and write an "
            "external key-authenticated receipt without persisting plaintext"
        ),
    )
    parser.add_argument(
        "--audit-private-build-artifact",
        type=Path,
        help=(
            "scan one native build artifact for repository, user-profile and "
            "other explicitly forbidden source paths without echoing private bytes"
        ),
    )
    parser.add_argument(
        "--forbid-path",
        action="append",
        default=[],
        help="absolute build/user path forbidden in package files and runtime bytecode",
    )
    parser.add_argument(
        "--product-profile",
        choices=("standard", "network-diagnostic"),
        help=(
            "expected packaged product profile; must match BUILD_MANIFEST and "
            "enables profile-specific whole-ZIP text gates"
        ),
    )
    parser.add_argument(
        "--protection-manifest",
        type=Path,
        help=(
            "value-tiered client protection manifest; encrypted packages also "
            "default to scripts/client_protection_manifest.json"
        ),
    )
    parser.add_argument(
        "--release-contract",
        type=Path,
        help=(
            "externally reviewed positive candidate contract; enforces required "
            "members, manifest identity, runtime dependencies and protection policy"
        ),
    )
    parser.add_argument(
        "--runtime-receipt",
        type=Path,
        help=(
            "external canonical runtime/python inventory frozen from the final "
            "Stage before compression"
        ),
    )
    parser.add_argument(
        "--runtime-approval",
        type=Path,
        help=(
            "source-reviewed runtime/python identity pinned by the release contract"
        ),
    )
    parser.add_argument(
        "--bkenc-receipt",
        type=Path,
        help="external key-authenticated BKENC member receipt",
    )
    parser.add_argument(
        "--bkenc-key-file",
        type=Path,
        help="base64 32-byte audit key; never printed or copied into artifacts",
    )
    parser.add_argument(
        "--expect-field",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="external BUILD_MANIFEST expectation; repeat for every dynamic identity",
    )
    parser.add_argument(
        "--trusted-asset",
        action="append",
        nargs=2,
        default=[],
        metavar=("ZIP_PREFIX", "MANIFEST"),
        help=(
            "allow upstream build metadata only when a complete ZIP subtree "
            "matches the reviewed release-asset manifest"
        ),
    )
    args = parser.parse_args()
    if args.audit_private_build_artifact is not None:
        incompatible = any(
            (
                args.zip_path is not None,
                args.build_receipt is not None,
                args.source_root is not None,
                args.write_runtime_receipt is not None,
                args.write_canonical_zip is not None,
                args.write_bkenc_receipt is not None,
                args.product_profile is not None,
                args.protection_manifest is not None,
                args.release_contract is not None,
                args.runtime_receipt is not None,
                args.runtime_approval is not None,
                args.bkenc_receipt is not None,
                args.bkenc_key_file is not None,
                bool(args.expect_field),
                bool(args.trusted_asset),
            )
        )
        if incompatible:
            print("[FAIL] native artifact privacy mode only accepts --forbid-path")
            return 2
        issues = audit_private_build_artifact(
            args.audit_private_build_artifact,
            forbidden_paths=tuple(args.forbid_path),
        )
        if issues:
            for issue in issues:
                print(f"[FAIL] {issue}")
            return 1
        print("[OK] native artifact privacy gate")
        return 0
    write_modes = sum(
        mode is not None
        for mode in (
            args.write_runtime_receipt,
            args.write_canonical_zip,
            args.write_bkenc_receipt,
        )
    )
    if write_modes > 1:
        print("[FAIL] receipt 与 ZIP 写入模式不能同时使用")
        return 2
    if args.write_runtime_receipt is not None:
        if args.zip_path is not None:
            print("[FAIL] receipt 写入模式不能同时传 ZIP")
            return 2
        runtime_root, receipt_path, approval_path = args.write_runtime_receipt
        try:
            write_runtime_inventory_receipt(
                runtime_root,
                receipt_path,
                approval_path,
            )
        except (OSError, TypeError, ValueError) as exc:
            print(f"[FAIL] runtime receipt 生成失败: {type(exc).__name__}")
            return 1
        print("[OK] runtime/python canonical receipt 已冻结")
        return 0
    if args.write_canonical_zip is not None:
        if args.zip_path is not None:
            print("[FAIL] canonical ZIP 写入模式不能同时传待审计 ZIP")
            return 2
        stage_root, zip_path = args.write_canonical_zip
        try:
            write_canonical_release_zip(stage_root, zip_path)
        except (OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
            print(f"[FAIL] canonical ZIP 生成失败: {type(exc).__name__}")
            return 1
        print("[OK] canonical POSIX release ZIP 已生成")
        return 0
    if args.write_bkenc_receipt is not None:
        if args.zip_path is not None:
            print("[FAIL] BKENC receipt 写入模式不能同时传待审计 ZIP")
            return 2
        zip_path, receipt_path, contract_path, key_file = args.write_bkenc_receipt
        try:
            write_bkenc_authentication_receipt(
                zip_path,
                receipt_path,
                contract_path,
                key_file,
            )
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UnicodeError,
            zipfile.BadZipFile,
        ) as exc:
            print(
                "[FAIL] BKENC receipt 生成失败: "
                f"{type(exc).__name__}"
            )
            return 1
        print("[OK] BKENC 逐成员 AEAD receipt 已冻结")
        return 0
    receipt_inputs: dict[str, object] | None = None
    if args.build_receipt is not None:
        legacy_inputs_present = any(
            (
                args.zip_path is not None,
                bool(args.forbid_path),
                args.product_profile is not None,
                args.protection_manifest is not None,
                args.release_contract is not None,
                args.runtime_receipt is not None,
                args.runtime_approval is not None,
                args.bkenc_receipt is not None,
                bool(args.expect_field),
                bool(args.trusted_asset),
            )
        )
        if legacy_inputs_present or args.source_root is None:
            print("[FAIL] --build-receipt 必须独占审计身份参数并显式传 --source-root")
            return 2
        try:
            receipt_inputs = load_build_audit_receipt(
                args.build_receipt, args.source_root
            )
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UnicodeError,
        ) as exc:
            print(f"[FAIL] build receipt 无效: {type(exc).__name__}")
            return 2
        args.zip_path = receipt_inputs["zip_path"]
        if receipt_inputs["encrypted"] and args.bkenc_key_file is None:
            print("[FAIL] 加密 build receipt 审计缺 BKENC key file")
            return 2
    elif args.source_root is not None:
        print("[FAIL] --source-root 仅与 --build-receipt 配合使用")
        return 2
    if args.zip_path is None:
        print("[FAIL] 缺少待审计 ZIP")
        return 2
    if not args.zip_path.is_file():
        print(f"[FAIL] ZIP 不存在: {args.zip_path}")
        return 2
    expected_manifest_fields: list[tuple[str, str]] = []
    if receipt_inputs is not None:
        expected_manifest_fields.extend(receipt_inputs["expected_manifest_fields"])
    else:
        for raw in args.expect_field:
            key, separator, value = raw.partition("=")
            if not separator or not key or not value:
                print("[FAIL] --expect-field 必须为非空 KEY=VALUE")
                return 2
            expected_manifest_fields.append((key, value))
    bkenc_key: bytes | None = None
    if args.bkenc_key_file is not None:
        try:
            bkenc_key = _load_bkenc_key_file(args.bkenc_key_file)
        except ValueError:
            print("[FAIL] BKENC 审计密钥不可用")
            return 2
    issues = audit_zip(
        args.zip_path,
        forbidden_paths=(
            receipt_inputs["forbidden_paths"]
            if receipt_inputs is not None
            else tuple(args.forbid_path)
        ),
        trusted_assets=(
            receipt_inputs["trusted_assets"]
            if receipt_inputs is not None
            else tuple((prefix, Path(manifest)) for prefix, manifest in args.trusted_asset)
        ),
        product_profile=(
            receipt_inputs["product_profile"]
            if receipt_inputs is not None
            else args.product_profile
        ),
        protection_manifest=(
            receipt_inputs["protection_manifest"]
            if receipt_inputs is not None
            else args.protection_manifest
        ),
        release_contract=(
            receipt_inputs["release_contract"]
            if receipt_inputs is not None
            else args.release_contract
        ),
        runtime_receipt=(
            receipt_inputs["runtime_receipt"]
            if receipt_inputs is not None
            else args.runtime_receipt
        ),
        runtime_approval=(
            receipt_inputs["runtime_approval"]
            if receipt_inputs is not None
            else args.runtime_approval
        ),
        bkenc_receipt=(
            receipt_inputs["bkenc_receipt"]
            if receipt_inputs is not None
            else args.bkenc_receipt
        ),
        bkenc_key=bkenc_key,
        local_integrity=(
            receipt_inputs["local_integrity"]
            if receipt_inputs is not None
            else None
        ),
        expected_manifest_fields=tuple(expected_manifest_fields),
    )
    if issues:
        print("[FAIL] 公版 ZIP 隐私/字节码扫描失败:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print(
        "[OK] 公版 ZIP 负向扫描通过：无项目 pycache/pyc/pyo、"
        "高价值产品明文、本地兼容明文模块或构建机私有路径。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
