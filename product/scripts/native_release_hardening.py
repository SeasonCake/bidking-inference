"""Fail-closed unsigned native inventory, PE hardening, and identity receipts.

The standard first-party native distribution is intentionally unsigned.  This module
does not accept certificate, signing-policy, signtool, password, PIN, or key inputs.
It records exact post-build identities while rejecting embedded Authenticode bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


CONTRACT_SCHEMA = "bidking_release_native_binary_contract_v2"
RECEIPT_SCHEMA = "bidking_native_binary_receipt_v2"
RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "receipt_version",
        "distribution",
        "contract_sha256",
        "member_count",
        "members",
    }
)
RECEIPT_MEMBER_FIELDS = frozenset(
    {
        "member",
        "bytes",
        "sha256",
        "pe",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

_DLL_CHARACTERISTICS = {
    "high_entropy_va": 0x0020,
    "dynamic_base": 0x0040,
    "nx_compat": 0x0100,
    "guard_cf": 0x4000,
}
_GUARD_FLAGS = {
    "cf_instrumented": 0x0000_0100,
    "cf_function_table_present": 0x0000_0400,
}


class NativeContractError(RuntimeError):
    """One stable non-secret reason why a native candidate is inadmissible."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NativeContractError("json_duplicate_key")
        result[key] = value
    return result


def _decode_json(raw: bytes) -> dict[str, Any]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise NativeContractError("json_bom_forbidden")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeContractError("json_invalid") from exc
    if type(value) is not dict:
        raise NativeContractError("json_invalid")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    return _decode_json(path.read_bytes())


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(value: object) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise NativeContractError("native_binary_member_invalid")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or value != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise NativeContractError("native_binary_member_invalid")
    return value


def _exact_string_list(value: object, *, label: str) -> list[str]:
    if type(value) is not list or not value or any(type(item) is not str for item in value):
        raise NativeContractError(f"{label}_invalid")
    result = [str(item) for item in value]
    if len(set(result)) != len(result):
        raise NativeContractError(f"{label}_invalid")
    return result


def load_contract(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    expected = {
        "schema",
        "contract_version",
        "protected_cython_module_count",
        "additional_native_binary_count",
        "bootstrap",
        "cython",
        "msvc",
        "pe",
        "inventory",
        "distribution",
    }
    if (
        set(value) != expected
        or type(value.get("schema")) is not str
        or value["schema"] != CONTRACT_SCHEMA
    ):
        raise NativeContractError("native_binary_contract_malformed")
    if (
        type(value["contract_version"]) is not int
        or value["contract_version"] != 2
        or type(value["protected_cython_module_count"]) is not int
        or value["protected_cython_module_count"] != 112
        or type(value["additional_native_binary_count"]) is not int
        or value["additional_native_binary_count"] != 7
    ):
        raise NativeContractError("native_binary_contract_malformed")

    msvc = value["msvc"]
    pe = value["pe"]
    inventory = value["inventory"]
    distribution = value["distribution"]
    cython = value["cython"]
    bootstrap = value["bootstrap"]
    if any(type(item) is not dict for item in (msvc, pe, inventory, distribution, cython, bootstrap)):
        raise NativeContractError("native_binary_contract_malformed")
    if (
        set(bootstrap)
        != {
            "crate",
            "staged_member",
            "python_member",
            "manifest_member",
            "signature_member",
            "user_experience",
            "execution_manifest",
        }
        or bootstrap
        != {
            "crate": "native/bidking_trust_bootstrap",
            "staged_member": "BidKingLive.exe",
            "python_member": "runtime/python/python.exe",
            "manifest_member": "integrity/local_integrity_manifest.json",
            "signature_member": "integrity/local_integrity_manifest.sig",
            "user_experience": {
                "subsystem": 2,
                "icon_source": "hero_ref/assets/bidking_cat.ico",
                "icon_source_bytes": 24747,
                "icon_source_sha256": "11e495dea7c8c6beec526c7a1c06ec59e4cca8f5a1d6aa33565ef2e904240c67",
                "resource_type": 14,
                "resource_name": 1,
                "resource_language": 1033,
                "codepage": 0,
                "image_count": 7,
                "group_bytes": 104,
                "group_sha256": "893589c5f4cc0ae85e517ce823f6b872c0dea66026c5f98c585a8d47e74369e3",
                "image_payload_sha256": "48d36cdbc76f7ba3be37787fa6d3e71c3fab54bdd54ad0c2c817602261d4be39",
            },
            "execution_manifest": {
                "source": "native/bidking_trust_bootstrap/build.rs",
                "linker_args": [
                    "/MANIFEST:EMBED",
                    "/MANIFESTUAC:level='requireAdministrator' uiAccess='false'",
                ],
                "resource_type": 24,
                "resource_name": 1,
                "resource_language": 1033,
                "embedded_bytes": 392,
                "embedded_sha256": "165c5c883fd4fd36758bcba6baf2faffb77d2f4872ffd5ee918a16f91de5a8a8",
                "codepage": 0,
                "requested_execution_level": "requireAdministrator",
                "ui_access": False,
                "createprocess_pre_uac_error": 740,
            },
        }
        or set(cython)
        != {
            "compiler_directives",
            "binding_false_groups",
            "build_only_augmentations",
        }
        or cython.get("build_only_augmentations")
        != [
            {
                "source": "hero_ref/src/ahmad_ref_engine.py",
                "declaration": "scripts/cython/ahmad_ref_engine.pxd",
                "internal_symbols": ["_log_fact"],
            }
        ]
        or set(msvc)
        != {"compile_flags", "link_flags", "forbidden_environment"}
        or set(pe)
        != {
            "machine",
            "format",
            "required_dll_characteristics",
            "required_guard_flags",
            "embedded_authenticode_allowed",
        }
        or pe.get("required_dll_characteristics")
        != ["dynamic_base", "nx_compat", "high_entropy_va", "guard_cf"]
        or pe.get("required_guard_flags")
        != ["cf_instrumented", "cf_function_table_present"]
        or set(inventory)
        != {
            "exact_members",
            "exactly_one_globs",
            "protected_members_from",
            "first_party_prefixes",
            "vendor_prefixes",
        }
        or inventory.get("exact_members")
        != [
            "BidKingLive.exe",
            "scripts/_client_launcher.cp313-win_amd64.pyd",
            "scripts/_local_compat_addon_loader.pyd",
            "scripts/_local_compat_core.cp313-win_amd64.pyd",
            "scripts/_local_compat_monitor.cp313-win_amd64.pyd",
            "scripts/_runtime_component.cp313-win_amd64.pyd",
        ]
        or inventory.get("exactly_one_globs") != ["src/bidking_kernel*.pyd"]
        or inventory.get("protected_members_from")
        != "scripts/client_protection_manifest.json"
        or inventory.get("first_party_prefixes")
        != ["hero_ref/src/", "hero_ref/tools/", "scripts/", "src/"]
        or inventory.get("vendor_prefixes") != ["runtime/", "vendor/"]
        or set(distribution)
        != {
            "mode",
            "publisher_claim",
            "receipt_schema",
            "receipt_member",
            "forbidden_build_inputs",
        }
    ):
        raise NativeContractError("native_binary_contract_malformed")
    if _exact_string_list(msvc.get("compile_flags"), label="compile_flags") != [
        "/O2",
        "/GL",
        "/guard:cf",
    ]:
        raise NativeContractError("native_binary_contract_malformed")
    if _exact_string_list(msvc.get("link_flags"), label="link_flags") != [
        "/LTCG",
        "/OPT:REF",
        "/OPT:ICF",
        "/GUARD:CF",
        "/DYNAMICBASE",
        "/NXCOMPAT",
        "/HIGHENTROPYVA",
    ]:
        raise NativeContractError("native_binary_contract_malformed")
    if set(_exact_string_list(msvc.get("forbidden_environment"), label="environment")) != {
        "CL",
        "_CL_",
        "LINK",
        "_LINK_",
    }:
        raise NativeContractError("native_binary_contract_malformed")
    if (
        cython.get("compiler_directives")
        != {"embedsignature": False, "emit_code_comments": False}
        or cython.get("binding_false_groups") != ["local-compat"]
        or pe.get("machine") != "amd64"
        or pe.get("format") != "pe32+"
        or pe.get("embedded_authenticode_allowed") is not False
        or distribution.get("mode") != "unsigned"
        or distribution.get("publisher_claim") is not False
        or distribution.get("receipt_schema") != RECEIPT_SCHEMA
        or distribution.get("receipt_member") != "integrity/native_binary_receipt.json"
        or distribution.get("forbidden_build_inputs")
        != ["certificate", "native_signing_policy", "signtool"]
    ):
        raise NativeContractError("native_binary_contract_malformed")
    execution_manifest = bootstrap["execution_manifest"]
    user_experience = bootstrap["user_experience"]
    if (
        type(execution_manifest["resource_type"]) is not int
        or type(execution_manifest["resource_name"]) is not int
        or type(execution_manifest["resource_language"]) is not int
        or type(execution_manifest["embedded_bytes"]) is not int
        or type(execution_manifest["embedded_sha256"]) is not str
        or _SHA256.fullmatch(execution_manifest["embedded_sha256"]) is None
        or type(execution_manifest["codepage"]) is not int
        or type(execution_manifest["ui_access"]) is not bool
        or type(execution_manifest["createprocess_pre_uac_error"]) is not int
    ):
        raise NativeContractError("native_binary_contract_malformed")
    if (
        type(user_experience["subsystem"]) is not int
        or type(user_experience["icon_source_bytes"]) is not int
        or type(user_experience["icon_source_sha256"]) is not str
        or _SHA256.fullmatch(user_experience["icon_source_sha256"]) is None
        or type(user_experience["resource_type"]) is not int
        or type(user_experience["resource_name"]) is not int
        or type(user_experience["resource_language"]) is not int
        or type(user_experience["codepage"]) is not int
        or type(user_experience["image_count"]) is not int
        or type(user_experience["group_bytes"]) is not int
        or type(user_experience["group_sha256"]) is not str
        or _SHA256.fullmatch(user_experience["group_sha256"]) is None
        or type(user_experience["image_payload_sha256"]) is not str
        or _SHA256.fullmatch(user_experience["image_payload_sha256"]) is None
    ):
        raise NativeContractError("native_binary_contract_malformed")
    _safe_member(distribution.get("receipt_member"))
    for field in ("staged_member", "python_member", "manifest_member", "signature_member"):
        _safe_member(bootstrap.get(field))
    _safe_member(bootstrap["execution_manifest"]["source"])
    _safe_member(bootstrap["user_experience"]["icon_source"])
    icon_source = path.parent.parent / bootstrap["user_experience"]["icon_source"]
    if (
        not icon_source.is_file()
        or icon_source.stat().st_size != user_experience["icon_source_bytes"]
        or _sha256_file(icon_source) != user_experience["icon_source_sha256"]
    ):
        raise NativeContractError("native_bootstrap_icon_source_mismatch")
    for item in _exact_string_list(inventory.get("exact_members"), label="exact_members"):
        _safe_member(item)
    for prefix in _exact_string_list(inventory.get("first_party_prefixes"), label="prefixes"):
        if not prefix.endswith("/"):
            raise NativeContractError("native_binary_contract_malformed")
        _safe_member(prefix[:-1])
    return value


def load_receipt_bytes(raw: bytes) -> dict[str, Any]:
    return _decode_json(raw)


def reject_ambient_msvc_overrides(contract: Mapping[str, Any], env: Mapping[str, str]) -> None:
    for name in contract["msvc"]["forbidden_environment"]:
        if env.get(name):
            raise NativeContractError("native_build_environment_override")


def _u16(blob: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(blob):
        raise NativeContractError("native_binary_pe_malformed")
    return struct.unpack_from("<H", blob, offset)[0]


def _u32(blob: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(blob):
        raise NativeContractError("native_binary_pe_malformed")
    return struct.unpack_from("<I", blob, offset)[0]


def _pe_resources(blob: bytes) -> dict[tuple[int, int, int], tuple[bytes, int]]:
    """Return every numeric PE resource without loading or executing the image."""
    if len(blob) < 0x40 or blob[:2] != b"MZ":
        raise NativeContractError("native_binary_pe_malformed")
    pe_offset = _u32(blob, 0x3C)
    if pe_offset + 24 > len(blob) or blob[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise NativeContractError("native_binary_pe_malformed")
    file_header = pe_offset + 4
    section_count = _u16(blob, file_header + 2)
    optional_size = _u16(blob, file_header + 16)
    optional = file_header + 20
    if _u16(blob, optional) != 0x20B or optional_size < 136:
        raise NativeContractError("native_binary_pe_unsupported")
    if optional + optional_size > len(blob) or section_count == 0 or section_count > 96:
        raise NativeContractError("native_binary_pe_malformed")
    if _u32(blob, optional + 108) < 3:
        return {}
    resource_rva = _u32(blob, optional + 112 + 2 * 8)
    resource_size = _u32(blob, optional + 112 + 2 * 8 + 4)
    if resource_rva == 0 and resource_size == 0:
        return {}
    if resource_rva == 0 or resource_size < 16 or resource_size > 16 * 1024 * 1024:
        raise NativeContractError("native_binary_pe_resource_malformed")

    section_table = optional + optional_size
    sections: list[tuple[int, int, int, int]] = []
    for index in range(section_count):
        section = section_table + index * 40
        if section + 40 > len(blob):
            raise NativeContractError("native_binary_pe_malformed")
        virtual_size = _u32(blob, section + 8)
        virtual_address = _u32(blob, section + 12)
        raw_size = _u32(blob, section + 16)
        raw_pointer = _u32(blob, section + 20)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer, raw_size))

    def rva_offset(rva: int, size: int) -> int:
        for virtual_address, span, raw_pointer, raw_size in sections:
            if virtual_address <= rva < virtual_address + span:
                relative = rva - virtual_address
                if relative + size > raw_size or raw_pointer + relative + size > len(blob):
                    break
                return raw_pointer + relative
        raise NativeContractError("native_binary_pe_resource_malformed")

    resource_base = rva_offset(resource_rva, resource_size)

    def directory(relative: int) -> list[tuple[int, int]]:
        if relative < 0 or relative + 16 > resource_size:
            raise NativeContractError("native_binary_pe_resource_malformed")
        offset = resource_base + relative
        named = _u16(blob, offset + 12)
        numeric = _u16(blob, offset + 14)
        count = named + numeric
        if count > 4096 or relative + 16 + count * 8 > resource_size:
            raise NativeContractError("native_binary_pe_resource_malformed")
        return [
            (_u32(blob, offset + 16 + index * 8), _u32(blob, offset + 20 + index * 8))
            for index in range(count)
        ]

    resources: dict[tuple[int, int, int], tuple[bytes, int]] = {}
    for resource_type, type_value in directory(0):
        if resource_type & 0x8000_0000 or type_value & 0x8000_0000 == 0:
            raise NativeContractError("native_binary_pe_resource_malformed")
        for resource_name, name_value in directory(type_value & 0x7FFF_FFFF):
            if resource_name & 0x8000_0000 or name_value & 0x8000_0000 == 0:
                raise NativeContractError("native_binary_pe_resource_malformed")
            for language, data_relative in directory(name_value & 0x7FFF_FFFF):
                if language & 0x8000_0000 or data_relative & 0x8000_0000:
                    raise NativeContractError("native_binary_pe_resource_malformed")
                if data_relative + 16 > resource_size:
                    raise NativeContractError("native_binary_pe_resource_malformed")
                data_entry = resource_base + data_relative
                data_rva = _u32(blob, data_entry)
                data_size = _u32(blob, data_entry + 4)
                codepage = _u32(blob, data_entry + 8)
                reserved = _u32(blob, data_entry + 12)
                if data_size == 0 or data_size > 1024 * 1024 or reserved != 0:
                    raise NativeContractError("native_binary_pe_resource_malformed")
                start = rva_offset(data_rva, data_size)
                key = (resource_type, resource_name, language)
                if key in resources:
                    raise NativeContractError("native_binary_pe_resource_ambiguous")
                resources[key] = (blob[start : start + data_size], codepage)
    return resources


def _pe_execution_manifest(blob: bytes) -> dict[str, object] | None:
    """Extract the single numeric RT_MANIFEST/1 resource without loading the PE."""

    candidates = [
        (key, value) for key, value in _pe_resources(blob).items() if key[0] == 24
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise NativeContractError("native_binary_pe_manifest_ambiguous")
    (resource_type, resource_name, language), (payload, codepage) = candidates[0]
    try:
        text = payload.decode("utf-8")
        root = ET.fromstring(text)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise NativeContractError("native_binary_pe_manifest_malformed") from exc
    asm_v1 = "urn:schemas-microsoft-com:asm.v1"
    asm_v3 = "urn:schemas-microsoft-com:asm.v3"
    if root.tag != f"{{{asm_v1}}}assembly" or root.attrib != {"manifestVersion": "1.0"}:
        raise NativeContractError("native_binary_pe_manifest_malformed")
    trust = list(root)
    if len(trust) != 1 or trust[0].tag != f"{{{asm_v3}}}trustInfo" or trust[0].attrib:
        raise NativeContractError("native_binary_pe_manifest_malformed")
    security = list(trust[0])
    if len(security) != 1 or security[0].tag != f"{{{asm_v3}}}security" or security[0].attrib:
        raise NativeContractError("native_binary_pe_manifest_malformed")
    privileges = list(security[0])
    if (
        len(privileges) != 1
        or privileges[0].tag != f"{{{asm_v3}}}requestedPrivileges"
        or privileges[0].attrib
    ):
        raise NativeContractError("native_binary_pe_manifest_malformed")
    levels = list(privileges[0])
    if (
        len(levels) != 1
        or levels[0].tag != f"{{{asm_v3}}}requestedExecutionLevel"
        or set(levels[0].attrib) != {"level", "uiAccess"}
        or list(levels[0])
        or (levels[0].text or "").strip()
    ):
        raise NativeContractError("native_binary_pe_manifest_malformed")
    ui_access = levels[0].attrib["uiAccess"]
    if ui_access not in {"true", "false"}:
        raise NativeContractError("native_binary_pe_manifest_malformed")
    return {
        "resource_type": resource_type,
        "resource_name": resource_name,
        "resource_language": language,
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "requested_execution_level": levels[0].attrib["level"],
        "ui_access": ui_access == "true",
        "codepage": codepage,
    }


def _pe_launcher_icon(blob: bytes) -> dict[str, object] | None:
    resources = _pe_resources(blob)
    groups = [(key, value) for key, value in resources.items() if key[0] == 14]
    if not groups:
        return None
    if len(groups) != 1:
        raise NativeContractError("native_bootstrap_icon_ambiguous")
    (resource_type, resource_name, language), (group, codepage) = groups[0]
    if len(group) < 6:
        raise NativeContractError("native_bootstrap_icon_malformed")
    reserved, icon_type, image_count = struct.unpack_from("<HHH", group, 0)
    if reserved != 0 or icon_type != 1 or image_count == 0 or image_count > 64:
        raise NativeContractError("native_bootstrap_icon_malformed")
    if len(group) != 6 + image_count * 14:
        raise NativeContractError("native_bootstrap_icon_malformed")
    image_payloads: list[bytes] = []
    image_ids: list[int] = []
    for index in range(image_count):
        entry = struct.unpack_from("<BBBBHHIH", group, 6 + index * 14)
        width, height, colors, entry_reserved, planes, bit_count, size, image_id = entry
        if (
            not (width == 0 or 1 <= width <= 255)
            or not (height == 0 or 1 <= height <= 255)
            or colors != 0
            or entry_reserved != 0
            or planes != 1
            or bit_count != 32
            or size == 0
            or image_id in image_ids
        ):
            raise NativeContractError("native_bootstrap_icon_malformed")
        image = resources.get((3, image_id, language))
        if image is None or image[1] != 0 or len(image[0]) != size:
            raise NativeContractError("native_bootstrap_icon_malformed")
        image_ids.append(image_id)
        image_payloads.append(image[0])
    icon_entries = [key for key in resources if key[0] == 3]
    if sorted(icon_entries) != sorted((3, image_id, language) for image_id in image_ids):
        raise NativeContractError("native_bootstrap_icon_ambiguous")
    return {
        "resource_type": resource_type,
        "resource_name": resource_name,
        "resource_language": language,
        "codepage": codepage,
        "image_count": image_count,
        "group_bytes": len(group),
        "group_sha256": _sha256_bytes(group),
        "image_payload_sha256": _sha256_bytes(b"".join(image_payloads)),
    }


def verify_bootstrap_execution_manifest(
    pe: Mapping[str, object], contract: Mapping[str, Any]
) -> None:
    expected = contract["bootstrap"]["execution_manifest"]
    actual = pe.get("execution_manifest")
    field_map = {
        "resource_type": "resource_type",
        "resource_name": "resource_name",
        "resource_language": "resource_language",
        "bytes": "embedded_bytes",
        "sha256": "embedded_sha256",
        "codepage": "codepage",
        "requested_execution_level": "requested_execution_level",
        "ui_access": "ui_access",
    }
    if type(actual) is not dict or any(
        actual.get(actual_key) != expected[expected_key]
        for actual_key, expected_key in field_map.items()
    ):
        raise NativeContractError("native_bootstrap_execution_manifest_mismatch")


def verify_bootstrap_user_experience(
    pe: Mapping[str, object], contract: Mapping[str, Any]
) -> None:
    expected = contract["bootstrap"]["user_experience"]
    actual = pe.get("launcher_icon")
    field_map = {
        "resource_type": "resource_type",
        "resource_name": "resource_name",
        "resource_language": "resource_language",
        "codepage": "codepage",
        "image_count": "image_count",
        "group_bytes": "group_bytes",
        "group_sha256": "group_sha256",
        "image_payload_sha256": "image_payload_sha256",
    }
    if (
        pe.get("subsystem") != expected["subsystem"]
        or type(actual) is not dict
        or any(
            actual.get(actual_key) != expected[expected_key]
            for actual_key, expected_key in field_map.items()
        )
    ):
        raise NativeContractError("native_bootstrap_user_experience_mismatch")


def verify_bootstrap_pre_uac_createprocess(
    executable: Path, *, expected_winerror: int
) -> None:
    if os.name != "nt":
        raise NativeContractError("native_bootstrap_elevation_probe_unavailable")
    try:
        process = subprocess.Popen(
            [str(executable)],
            cwd=executable.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        if type(getattr(exc, "winerror", None)) is int and exc.winerror == expected_winerror:
            return
        raise NativeContractError("native_bootstrap_elevation_probe_failed") from exc
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    raise NativeContractError("native_bootstrap_elevation_probe_failed")


def parse_pe_hardening(
    blob: bytes, *, inspect_execution_manifest: bool = False
) -> dict[str, object]:
    if len(blob) < 0x40 or blob[:2] != b"MZ":
        raise NativeContractError("native_binary_pe_malformed")
    pe_offset = _u32(blob, 0x3C)
    if pe_offset + 24 > len(blob) or blob[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise NativeContractError("native_binary_pe_malformed")
    file_header = pe_offset + 4
    machine = _u16(blob, file_header)
    section_count = _u16(blob, file_header + 2)
    optional_size = _u16(blob, file_header + 16)
    optional = file_header + 20
    if machine != 0x8664 or _u16(blob, optional) != 0x20B or optional_size < 120:
        raise NativeContractError("native_binary_pe_unsupported")
    if optional + optional_size > len(blob) or section_count == 0 or section_count > 96:
        raise NativeContractError("native_binary_pe_malformed")
    dll_characteristics = _u16(blob, optional + 70)
    directory_count = _u32(blob, optional + 108)
    if directory_count < 11:
        raise NativeContractError("native_binary_pe_hardening_missing")
    directory = optional + 112
    security_offset = _u32(blob, directory + 4 * 8)
    security_size = _u32(blob, directory + 4 * 8 + 4)
    load_config_rva = _u32(blob, directory + 10 * 8)
    load_config_size = _u32(blob, directory + 10 * 8 + 4)
    if load_config_rva == 0 or load_config_size < 148:
        raise NativeContractError("native_binary_pe_hardening_missing")

    section_table = optional + optional_size
    load_config_offset: int | None = None
    for index in range(section_count):
        section = section_table + index * 40
        if section + 40 > len(blob):
            raise NativeContractError("native_binary_pe_malformed")
        virtual_size = _u32(blob, section + 8)
        virtual_address = _u32(blob, section + 12)
        raw_size = _u32(blob, section + 16)
        raw_pointer = _u32(blob, section + 20)
        span = max(virtual_size, raw_size)
        if virtual_address <= load_config_rva < virtual_address + span:
            relative = load_config_rva - virtual_address
            if relative >= raw_size:
                raise NativeContractError("native_binary_pe_hardening_missing")
            load_config_offset = raw_pointer + relative
            break
    if load_config_offset is None or load_config_offset + 148 > len(blob):
        raise NativeContractError("native_binary_pe_hardening_missing")
    guard_flags = _u32(blob, load_config_offset + 144)
    required_dll = sum(_DLL_CHARACTERISTICS.values())
    required_guard = sum(_GUARD_FLAGS.values())
    if (
        dll_characteristics & required_dll != required_dll
        or guard_flags & required_guard != required_guard
    ):
        raise NativeContractError("native_binary_pe_hardening_missing")
    embedded_signature = (
        security_offset > 0
        and security_size >= 8
        and security_offset + security_size <= len(blob)
    )
    return {
        "machine": "amd64",
        "format": "pe32+",
        "subsystem": _u16(blob, optional + 68),
        "dll_characteristics": dll_characteristics,
        "guard_flags": guard_flags,
        "embedded_authenticode": embedded_signature,
        "execution_manifest": (
            _pe_execution_manifest(blob) if inspect_execution_manifest else None
        ),
        "launcher_icon": (
            _pe_launcher_icon(blob) if inspect_execution_manifest else None
        ),
    }


def _protected_members(manifest: Mapping[str, Any]) -> set[str]:
    critical = manifest["critical_native"]
    result = {
        *(f"hero_ref/src/{name}.cp313-win_amd64.pyd" for name in critical["engine_modules"]),
        *(f"hero_ref/tools/{name}.cp313-win_amd64.pyd" for name in critical["tool_modules"]),
        (
            "hero_ref/tools/"
            f"{critical['overlay']['native_module']}.cp313-win_amd64.pyd"
        ),
    }
    package_sources = list(critical["package_modules"])
    for tree in critical["package_trees"]:
        package_sources.extend(tree["protected_modules"])
    result.update(f"src/{source[:-3]}.cp313-win_amd64.pyd" for source in package_sources)
    return result


def expected_first_party_members(
    available: Iterable[str],
    contract: Mapping[str, Any],
    protection_manifest: Mapping[str, Any],
) -> set[str]:
    normalized = {_safe_member(item) for item in available}
    expected = set(
        _exact_string_list(
            contract["inventory"]["exact_members"], label="exact_members"
        )
    )
    expected.update(_protected_members(protection_manifest))
    for raw_pattern in contract["inventory"]["exactly_one_globs"]:
        pattern = _safe_member(raw_pattern)
        prefix, marker, suffix = pattern.partition("*")
        if not marker or "*" in suffix:
            raise NativeContractError("native_binary_contract_malformed")
        matches = sorted(
            item
            for item in normalized
            if item.startswith(prefix) and item.endswith(suffix)
        )
        if len(matches) != 1:
            raise NativeContractError("native_binary_inventory_mismatch")
        expected.add(matches[0])
    if len(expected) != (
        contract["protected_cython_module_count"]
        + contract["additional_native_binary_count"]
    ):
        raise NativeContractError("native_binary_inventory_mismatch")
    return expected


def actual_first_party_members(
    available: Iterable[str], contract: Mapping[str, Any]
) -> set[str]:
    prefixes = tuple(contract["inventory"]["first_party_prefixes"])
    exact_executables = {
        contract["bootstrap"]["staged_member"],
    }
    return {
        item
        for item in (_safe_member(value) for value in available)
        if item in exact_executables
        or (item.lower().endswith(".pyd") and item.startswith(prefixes))
    }


def _stage_member_paths(stage: Path) -> dict[str, Path]:
    root = stage.resolve(strict=True)
    members: dict[str, Path] = {}
    casefolded: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        folded = relative.casefold()
        if folded in casefolded:
            raise NativeContractError("native_binary_inventory_mismatch")
        members[relative] = path
        casefolded.add(folded)
    return members


def inspect_stage(
    stage: Path,
    *,
    contract_path: Path,
    protection_manifest_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    reject_ambient_msvc_overrides(contract, os.environ)
    protection = _read_json(protection_manifest_path)
    members = _stage_member_paths(stage)
    expected = expected_first_party_members(members, contract, protection)
    actual = actual_first_party_members(members, contract)
    if actual != expected:
        raise NativeContractError("native_binary_inventory_mismatch")
    receipt_members: list[dict[str, object]] = []
    for member in sorted(expected):
        path = members[member]
        pe = parse_pe_hardening(
            path.read_bytes(),
            inspect_execution_manifest=(
                member == contract["bootstrap"]["staged_member"]
            ),
        )
        if pe["embedded_authenticode"] is not False:
            raise NativeContractError("native_binary_signed_input_forbidden")
        if member == contract["bootstrap"]["staged_member"]:
            verify_bootstrap_execution_manifest(pe, contract)
            verify_bootstrap_user_experience(pe, contract)
        receipt_members.append(
            {
                "member": member,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "pe": pe,
            }
        )
    verify_bootstrap_pre_uac_createprocess(
        members[contract["bootstrap"]["staged_member"]],
        expected_winerror=contract["bootstrap"]["execution_manifest"][
            "createprocess_pre_uac_error"
        ],
    )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "receipt_version": 2,
        "distribution": "unsigned",
        "contract_sha256": _sha256_file(contract_path),
        "member_count": len(receipt_members),
        "members": receipt_members,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )
    return receipt


def receipt_issues(
    members: Mapping[str, bytes],
    *,
    contract: Mapping[str, Any],
    protection_manifest: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> list[str]:
    try:
        expected = expected_first_party_members(members, contract, protection_manifest)
        actual = actual_first_party_members(members, contract)
    except NativeContractError as exc:
        return [str(exc)]
    issues: list[str] = []
    if actual != expected:
        issues.append("native_binary_inventory_mismatch")
    if (
        type(receipt) is not dict
        or set(receipt) != RECEIPT_FIELDS
        or type(receipt.get("schema")) is not str
        or receipt["schema"] != RECEIPT_SCHEMA
        or type(receipt.get("receipt_version")) is not int
        or receipt["receipt_version"] != 2
        or receipt.get("distribution") != "unsigned"
        or type(receipt.get("contract_sha256")) is not str
        or _SHA256.fullmatch(receipt["contract_sha256"]) is None
        or type(receipt.get("member_count")) is not int
        or receipt["member_count"] != len(expected)
        or type(receipt.get("members")) is not list
    ):
        return [*issues, "native_binary_receipt_invalid"]
    by_member: dict[str, Mapping[str, Any]] = {}
    for item in receipt["members"]:
        if (
            type(item) is not dict
            or set(item) != RECEIPT_MEMBER_FIELDS
            or type(item.get("member")) is not str
        ):
            return [*issues, "native_binary_receipt_invalid"]
        member = item["member"]
        if member in by_member:
            return [*issues, "native_binary_receipt_invalid"]
        by_member[member] = item
    if set(by_member) != expected:
        issues.append("native_binary_receipt_invalid")
        return issues
    for member in sorted(expected):
        payload = members.get(member)
        item = by_member[member]
        if payload is None:
            continue
        try:
            pe = parse_pe_hardening(
                payload,
                inspect_execution_manifest=(
                    member == contract["bootstrap"]["staged_member"]
                ),
            )
        except NativeContractError:
            issues.append(f"native_binary_pe_hardening_missing: {member}")
            continue
        if pe["embedded_authenticode"] is not False:
            issues.append(f"native_binary_signed_input_forbidden: {member}")
        if member == contract["bootstrap"]["staged_member"]:
            try:
                verify_bootstrap_execution_manifest(pe, contract)
            except NativeContractError:
                issues.append(
                    f"native_bootstrap_execution_manifest_mismatch: {member}"
                )
            try:
                verify_bootstrap_user_experience(pe, contract)
            except NativeContractError:
                issues.append(
                    f"native_bootstrap_user_experience_mismatch: {member}"
                )
        if item.get("bytes") != len(payload) or item.get("sha256") != _sha256_bytes(payload):
            issues.append(f"native_binary_identity_mismatch: {member}")
        if item.get("pe") != pe:
            issues.append(f"native_binary_pe_receipt_mismatch: {member}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect = subparsers.add_parser("inspect-stage")
    inspect.add_argument("--stage", type=Path, required=True)
    inspect.add_argument("--contract", type=Path, required=True)
    inspect.add_argument("--protection-manifest", type=Path, required=True)
    inspect.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        contract = load_contract(args.contract)
        reject_ambient_msvc_overrides(contract, os.environ)
        if args.command == "inspect-stage":
            inspect_stage(
                args.stage,
                contract_path=args.contract,
                protection_manifest_path=args.protection_manifest,
                receipt_path=args.receipt,
            )
    except (NativeContractError, OSError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1
    print("[OK] native release contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
