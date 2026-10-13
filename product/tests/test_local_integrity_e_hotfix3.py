from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "scripts" / "release_native_binary_contract.json"
PROTECTION_PATH = ROOT / "scripts" / "client_protection_manifest.json"
HARDENING_PATH = ROOT / "scripts" / "native_release_hardening.py"
BUILD_PATH = ROOT / "scripts" / "build_live_portable.ps1"
BOOTSTRAP_PATH = ROOT / "native" / "bidking_trust_bootstrap" / "src" / "main.rs"
BOOTSTRAP_ICON_PATH = ROOT / "hero_ref" / "assets" / "bidking_cat.ico"
ENGINE_PATH = ROOT / "hero_ref" / "src" / "ahmad_ref_engine.py"
ENGINE_PXD = ROOT / "scripts" / "cython" / "ahmad_ref_engine.pxd"
PARENT = "74e3bc766277705daf8fe77514e2bcd5b79950f9"
CYTHON_PYTHON = Path(r"C:\Python313\python.exe")

_SPEC = importlib.util.spec_from_file_location("native_release_hardening_e", HARDENING_PATH)
assert _SPEC and _SPEC.loader
_HARDENING = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HARDENING)


_EXECUTION_MANIFEST = (
    b"<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\r\n"
    b"<assembly xmlns='urn:schemas-microsoft-com:asm.v1' manifestVersion='1.0'>\r\n"
    b'  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">\r\n'
    b"    <security>\r\n"
    b"      <requestedPrivileges>\r\n"
    b"        <requestedExecutionLevel level='requireAdministrator' uiAccess='false' />\r\n"
    b"      </requestedPrivileges>\r\n"
    b"    </security>\r\n"
    b"  </trustInfo>\r\n"
    b"</assembly>\r\n"
)


def _launcher_icon_resources() -> dict[tuple[int, int, int], bytes]:
    icon = BOOTSTRAP_ICON_PATH.read_bytes()
    reserved, icon_type, count = struct.unpack_from("<HHH", icon, 0)
    assert reserved == 0 and icon_type == 1 and count > 0
    group = bytearray(struct.pack("<HHH", 0, 1, count))
    resources: dict[tuple[int, int, int], bytes] = {}
    for index in range(count):
        entry = struct.unpack_from("<BBBBHHII", icon, 6 + index * 16)
        width, height, colors, entry_reserved, planes, bit_count, size, offset = entry
        image_id = index + 1
        image = icon[offset : offset + size]
        assert len(image) == size
        resources[(3, image_id, 1033)] = image
        group.extend(
            struct.pack(
                "<BBBBHHIH",
                width,
                height,
                colors,
                entry_reserved,
                planes or 1,
                bit_count,
                size,
                image_id,
            )
        )
    resources[(14, 1, 1033)] = bytes(group)
    return resources


def _build_numeric_resources(
    resources: dict[tuple[int, int, int], bytes], *, resource_rva: int
) -> bytes:
    types: dict[int, dict[int, dict[int, bytes]]] = {}
    for (resource_type, name, language), payload in resources.items():
        types.setdefault(resource_type, {}).setdefault(name, {})[language] = payload

    root_size = 16 + 8 * len(types)
    cursor = root_size
    type_offsets: dict[int, int] = {}
    name_offsets: dict[tuple[int, int], int] = {}
    data_offsets: dict[tuple[int, int, int], int] = {}
    for resource_type, names in sorted(types.items()):
        type_offsets[resource_type] = cursor
        cursor += 16 + 8 * len(names)
        for name, languages in sorted(names.items()):
            name_offsets[(resource_type, name)] = cursor
            cursor += 16 + 8 * len(languages)
            for language in sorted(languages):
                data_offsets[(resource_type, name, language)] = cursor
                cursor += 16
    cursor = (cursor + 3) & ~3
    payload_offsets: dict[tuple[int, int, int], int] = {}
    for key in sorted(resources):
        payload_offsets[key] = cursor
        cursor += len(resources[key])
        cursor = (cursor + 3) & ~3

    blob = bytearray(cursor)
    struct.pack_into("<HH", blob, 12, 0, len(types))
    for type_index, (resource_type, names) in enumerate(sorted(types.items())):
        struct.pack_into(
            "<II",
            blob,
            16 + type_index * 8,
            resource_type,
            0x8000_0000 | type_offsets[resource_type],
        )
        type_offset = type_offsets[resource_type]
        struct.pack_into("<HH", blob, type_offset + 12, 0, len(names))
        for name_index, (name, languages) in enumerate(sorted(names.items())):
            struct.pack_into(
                "<II",
                blob,
                type_offset + 16 + name_index * 8,
                name,
                0x8000_0000 | name_offsets[(resource_type, name)],
            )
            name_offset = name_offsets[(resource_type, name)]
            struct.pack_into("<HH", blob, name_offset + 12, 0, len(languages))
            for language_index, (language, payload) in enumerate(
                sorted(languages.items())
            ):
                key = (resource_type, name, language)
                struct.pack_into(
                    "<II",
                    blob,
                    name_offset + 16 + language_index * 8,
                    language,
                    data_offsets[key],
                )
                struct.pack_into(
                    "<IIII",
                    blob,
                    data_offsets[key],
                    resource_rva + payload_offsets[key],
                    len(payload),
                    0,
                    0,
                )
                start = payload_offsets[key]
                blob[start : start + len(payload)] = payload
    return bytes(blob)


def _synthetic_hardened_pe(
    *,
    signed: bool = False,
    guard: bool = True,
    manifest: bytes | None = None,
    launcher_ui: bool = False,
) -> bytes:
    resources: dict[tuple[int, int, int], bytes] = {}
    if manifest is not None:
        resources[(24, 1, 1033)] = manifest
    if launcher_ui:
        resources.update(_launcher_icon_resources())
    resource_rva = 0x1100
    resource_blob = (
        _build_numeric_resources(resources, resource_rva=resource_rva)
        if resources
        else b""
    )
    raw_size = max(0x600, (0x100 + len(resource_blob) + 0x1FF) & ~0x1FF)
    blob = bytearray(0x200 + raw_size + (0x20 if signed else 0))
    blob[:2] = b"MZ"
    struct.pack_into("<I", blob, 0x3C, 0x80)
    blob[0x80:0x84] = b"PE\0\0"
    file_header = 0x84
    struct.pack_into("<H", blob, file_header, 0x8664)
    struct.pack_into("<H", blob, file_header + 2, 1)
    struct.pack_into("<H", blob, file_header + 16, 0xF0)
    optional = file_header + 20
    struct.pack_into("<H", blob, optional, 0x20B)
    struct.pack_into("<H", blob, optional + 68, 2 if launcher_ui else 3)
    dll = 0x0020 | 0x0040 | 0x0100 | (0x4000 if guard else 0)
    struct.pack_into("<H", blob, optional + 70, dll)
    struct.pack_into("<I", blob, optional + 108, 16)
    directory = optional + 112
    if signed:
        struct.pack_into("<II", blob, directory + 4 * 8, len(blob) - 0x20, 0x20)
    struct.pack_into("<II", blob, directory + 10 * 8, 0x1000, 0xA0)
    section = optional + 0xF0
    blob[section : section + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", blob, section + 8, raw_size, 0x1000, raw_size, 0x200)
    struct.pack_into(
        "<I",
        blob,
        0x200 + 144,
        (0x0000_0100 | 0x0000_0400) if guard else 0,
    )
    if resource_blob:
        resource = 0x300
        struct.pack_into(
            "<II", blob, directory + 2 * 8, resource_rva, len(resource_blob)
        )
        blob[resource : resource + len(resource_blob)] = resource_blob
    return bytes(blob)


def _contract_and_manifest() -> tuple[dict[str, object], dict[str, object]]:
    return (
        _HARDENING.load_contract(CONTRACT_PATH),
        json.loads(PROTECTION_PATH.read_text(encoding="utf-8")),
    )


def _native_fixture() -> tuple[
    dict[str, bytes], dict[str, object], dict[str, object], dict[str, object]
]:
    contract, manifest = _contract_and_manifest()
    available = set(contract["inventory"]["exact_members"])
    available.add("src/bidking_kernel.cp313-win_amd64.pyd")
    available.update(_HARDENING._protected_members(manifest))
    pe = _synthetic_hardened_pe()
    members = {name: pe for name in available}
    members["BidKingLive.exe"] = _synthetic_hardened_pe(
        manifest=_EXECUTION_MANIFEST, launcher_ui=True
    )
    expected = _HARDENING.expected_first_party_members(available, contract, manifest)
    assert len(expected) == 117
    assert contract["distribution"] == {
        "mode": "unsigned",
        "publisher_claim": False,
        "receipt_schema": "bidking_native_binary_receipt_v2",
        "receipt_member": "integrity/native_binary_receipt.json",
        "forbidden_build_inputs": [
            "certificate",
            "native_signing_policy",
            "signtool",
        ],
    }
    receipt_members = []
    for name in sorted(expected):
        payload = members[name]
        receipt_members.append(
            {
                "member": name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "pe": _HARDENING.parse_pe_hardening(
                    payload, inspect_execution_manifest=(name == "BidKingLive.exe")
                ),
            }
        )
    receipt = {
        "schema": _HARDENING.RECEIPT_SCHEMA,
        "receipt_version": 2,
        "distribution": "unsigned",
        "contract_sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
        "member_count": len(receipt_members),
        "members": receipt_members,
    }
    return members, contract, manifest, receipt


def test_parent_known_red_requires_authenticode_signing_inputs() -> None:
    result = subprocess.run(
        ["git", "show", f"{PARENT}:scripts/build_live_portable.ps1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    parent = result.stdout
    assert "NativeSigningPolicyFile" in parent
    assert "SignToolPath" in parent
    assert "sign-stage" in parent
    assert "BIDKING_AUTHENTICODE_SIGNER_SHA256" in parent


@pytest.mark.skipif(
    sys.platform != "win32" or not CYTHON_PYTHON.is_file(),
    reason="Windows CPython extension input absent",
)
def test_parent_path_check_allows_extension_replacement_but_stable_handle_blocks_it(
    tmp_path: Path,
) -> None:
    probe = subprocess.run(
        [
            str(CYTHON_PYTHON),
            "-c",
            "import _testcapi; print(_testcapi.__file__)",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    source = Path(probe.stdout.strip())
    assert source.is_file()

    unheld = tmp_path / "unheld"
    unheld.mkdir()
    target = unheld / "_testcapi.pyd"
    replacement = unheld / "replacement.pyd"
    shutil.copyfile(source, target)
    shutil.copyfile(source, replacement)
    replacement.write_bytes(replacement.read_bytes() + b"E-PARENT-REPLACE")
    checked_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    checked_identity = (target.stat().st_dev, target.stat().st_ino)
    os.replace(replacement, target)
    assert hashlib.sha256(target.read_bytes()).hexdigest() != checked_sha
    assert (target.stat().st_dev, target.stat().st_ino) != checked_identity
    imported = subprocess.run(
        [
            str(CYTHON_PYTHON),
            "-c",
            (
                "import importlib.util, pathlib; "
                f"p=pathlib.Path({str(target)!r}); "
                "s=importlib.util.spec_from_file_location('_testcapi', p); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert imported.returncode == 0, imported.stderr

    held = tmp_path / "held"
    held.mkdir()
    target = held / "_testcapi.pyd"
    replacement = held / "replacement.pyd"
    shutil.copyfile(source, target)
    shutil.copyfile(source, replacement)

    import ctypes
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
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
    close_handle = ctypes.windll.kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = create_file(
        str(target),
        0x80000000,
        0x00000001,
        None,
        3,
        0x00200000,
        None,
    )
    assert handle != wintypes.HANDLE(-1).value
    try:
        with pytest.raises(PermissionError):
            os.replace(replacement, target)
    finally:
        assert close_handle(handle)
    os.replace(replacement, target)


def test_native_contract_freezes_109_plus_7_unsigned_without_publisher_claim() -> None:
    contract, manifest = _contract_and_manifest()
    available = set(contract["inventory"]["exact_members"])
    available.add("src/bidking_kernel.cp313-win_amd64.pyd")
    available.update(_HARDENING._protected_members(manifest))
    expected = _HARDENING.expected_first_party_members(available, contract, manifest)

    protected = _HARDENING._protected_members(manifest)
    assert "src/bidking_lab/inference/q6_full_avg_evidence.cp313-win_amd64.pyd" in protected
    assert (
        "src/bidking_lab/extract/"
        "current_rankmap_package_contract.cp313-win_amd64.pyd"
    ) in protected
    assert "src/bidking_lab/live/current_rankmap_runtime.cp313-win_amd64.pyd" in protected
    assert "src/bidking_lab/live/fact_receipts.cp313-win_amd64.pyd" in protected
    assert "src/bidking_lab/live/catalog_value.cp313-win_amd64.pyd" in protected
    assert len(protected) == 110
    assert len(expected) == 117
    assert contract["msvc"]["compile_flags"] == ["/O2", "/GL", "/guard:cf"]
    assert contract["msvc"]["link_flags"][:4] == [
        "/LTCG",
        "/OPT:REF",
        "/OPT:ICF",
        "/GUARD:CF",
    ]
    assert contract["pe"]["embedded_authenticode_allowed"] is False
    assert contract["bootstrap"]["execution_manifest"] == {
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
    }
    assert contract["distribution"]["mode"] == "unsigned"
    assert contract["distribution"]["publisher_claim"] is False
    assert contract["distribution"]["forbidden_build_inputs"] == [
        "certificate",
        "native_signing_policy",
        "signtool",
    ]


def test_native_contract_rejects_stale_catalog_value_protected_count(
    tmp_path: Path,
) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["protected_cython_module_count"] = 108
    path = tmp_path / "stale-native-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        _HARDENING.NativeContractError,
        match="native_binary_contract_malformed",
    ):
        _HARDENING.load_contract(path)


class _IntSubclass(int):
    pass


class _StrSubclass(str):
    pass


def test_hardening_cli_has_only_unsigned_inspection_command() -> None:
    source = HARDENING_PATH.read_text(encoding="utf-8")
    assert 'add_parser("inspect-stage")' in source
    assert 'add_parser("sign-stage")' not in source
    assert 'add_parser("validate-policy")' not in source
    assert "--signtool" not in source
    assert "--policy" not in source


def test_ambient_msvc_override_is_fail_closed() -> None:
    contract, _manifest = _contract_and_manifest()
    _HARDENING.reject_ambient_msvc_overrides(contract, {})
    with pytest.raises(
        _HARDENING.NativeContractError,
        match="native_build_environment_override",
    ):
        _HARDENING.reject_ambient_msvc_overrides(contract, {"CL": "/Od"})


def test_pe_parser_requires_cfg_and_reports_embedded_signature() -> None:
    parsed = _HARDENING.parse_pe_hardening(_synthetic_hardened_pe())
    assert parsed["machine"] == "amd64"
    assert parsed["embedded_authenticode"] is False

    with pytest.raises(
        _HARDENING.NativeContractError,
        match="native_binary_pe_hardening_missing",
    ):
        _HARDENING.parse_pe_hardening(_synthetic_hardened_pe(guard=False))
    assert (
        _HARDENING.parse_pe_hardening(_synthetic_hardened_pe(signed=True))[
            "embedded_authenticode"
        ]
        is True
    )


def test_bootstrap_execution_manifest_is_exact_and_rejects_missing_asinvoker_or_uiaccess() -> None:
    contract, _manifest = _contract_and_manifest()
    valid = _HARDENING.parse_pe_hardening(
        _synthetic_hardened_pe(manifest=_EXECUTION_MANIFEST),
        inspect_execution_manifest=True,
    )
    _HARDENING.verify_bootstrap_execution_manifest(valid, contract)
    assert valid["execution_manifest"]["requested_execution_level"] == (
        "requireAdministrator"
    )
    assert valid["execution_manifest"]["ui_access"] is False

    mutations = (
        _synthetic_hardened_pe(),
        _synthetic_hardened_pe(
            manifest=_EXECUTION_MANIFEST.replace(
                b"requireAdministrator", b"asInvoker"
            )
        ),
        _synthetic_hardened_pe(
            manifest=_EXECUTION_MANIFEST.replace(
                b"uiAccess='false'", b"uiAccess='true'"
            )
        ),
    )
    for payload in mutations:
        with pytest.raises(
            _HARDENING.NativeContractError,
            match="native_bootstrap_execution_manifest_mismatch",
        ):
            _HARDENING.verify_bootstrap_execution_manifest(
                _HARDENING.parse_pe_hardening(
                    payload, inspect_execution_manifest=True
                ),
                contract,
            )


def test_bootstrap_user_experience_binds_gui_subsystem_and_exact_cat_icon() -> None:
    contract, _manifest = _contract_and_manifest()
    payload = _synthetic_hardened_pe(
        manifest=_EXECUTION_MANIFEST, launcher_ui=True
    )
    parsed = _HARDENING.parse_pe_hardening(
        payload, inspect_execution_manifest=True
    )
    _HARDENING.verify_bootstrap_user_experience(parsed, contract)
    assert parsed["subsystem"] == 2
    assert parsed["launcher_icon"]["image_count"] == 7

    console_payload = bytearray(payload)
    optional = 0x84 + 20
    struct.pack_into("<H", console_payload, optional + 68, 3)
    with pytest.raises(
        _HARDENING.NativeContractError,
        match="native_bootstrap_user_experience_mismatch",
    ):
        _HARDENING.verify_bootstrap_user_experience(
            _HARDENING.parse_pe_hardening(
                bytes(console_payload), inspect_execution_manifest=True
            ),
            contract,
        )

    no_icon = _synthetic_hardened_pe(manifest=_EXECUTION_MANIFEST)
    with pytest.raises(
        _HARDENING.NativeContractError,
        match="native_bootstrap_user_experience_mismatch",
    ):
        _HARDENING.verify_bootstrap_user_experience(
            _HARDENING.parse_pe_hardening(
                no_icon, inspect_execution_manifest=True
            ),
            contract,
        )


def test_native_receipt_accepts_unsigned_and_rejects_extra_tamper_or_signed_input() -> None:
    members, contract, manifest, receipt = _native_fixture()
    assert (
        _HARDENING.receipt_issues(
            members,
            contract=contract,
            protection_manifest=manifest,
            receipt=receipt,
        )
        == []
    )

    name = sorted(members)[0]
    tampered = dict(members)
    tampered[name] = members[name] + b"x"
    assert any(
        issue.startswith("native_binary_identity_mismatch")
        for issue in _HARDENING.receipt_issues(
            tampered,
            contract=contract,
            protection_manifest=manifest,
            receipt=receipt,
        )
    )

    extra = dict(members)
    extra["scripts/unreviewed.cp313-win_amd64.pyd"] = members[name]
    assert "native_binary_inventory_mismatch" in _HARDENING.receipt_issues(
        extra,
        contract=contract,
        protection_manifest=manifest,
        receipt=receipt,
    )

    signed = dict(members)
    signed[name] = _synthetic_hardened_pe(signed=True)
    assert any(
        issue.startswith("native_binary_signed_input_forbidden")
        for issue in _HARDENING.receipt_issues(
            signed,
            contract=contract,
            protection_manifest=manifest,
            receipt=receipt,
        )
    )

    fake_claim = json.loads(json.dumps(receipt))
    fake_claim["native_signing_policy"] = {"publisher": "fake"}
    assert "native_binary_receipt_invalid" in _HARDENING.receipt_issues(
        members,
        contract=contract,
        protection_manifest=manifest,
        receipt=fake_claim,
    )


@pytest.mark.parametrize(
    "version",
    (True, 1.0, "2", None, _IntSubclass(2)),
)
def test_native_receipt_requires_exact_builtin_version(version: object) -> None:
    members, contract, manifest, receipt = _native_fixture()
    receipt["receipt_version"] = version

    assert "native_binary_receipt_invalid" in _HARDENING.receipt_issues(
        members,
        contract=contract,
        protection_manifest=manifest,
        receipt=receipt,
    )


def test_native_receipt_requires_exact_builtin_schema() -> None:
    members, contract, manifest, receipt = _native_fixture()
    receipt["schema"] = _StrSubclass(_HARDENING.RECEIPT_SCHEMA)

    assert "native_binary_receipt_invalid" in _HARDENING.receipt_issues(
        members,
        contract=contract,
        protection_manifest=manifest,
        receipt=receipt,
    )


@pytest.mark.parametrize("mutation", ("missing", "extra", "member_extra"))
def test_native_receipt_rejects_missing_or_unknown_fields(mutation: str) -> None:
    members, contract, manifest, receipt = _native_fixture()
    if mutation == "missing":
        receipt.pop("receipt_version")
    elif mutation == "extra":
        receipt["unreviewed_authority"] = None
    else:
        receipt["members"][0]["unreviewed_authority"] = None

    assert "native_binary_receipt_invalid" in _HARDENING.receipt_issues(
        members,
        contract=contract,
        protection_manifest=manifest,
        receipt=receipt,
    )


@pytest.mark.skipif(not CYTHON_PYTHON.is_file(), reason="CPython 3.13 Cython input absent")
def test_augmenting_pxd_internalizes_log_fact_without_product_native_build(
    tmp_path: Path,
) -> None:
    parent_dir = tmp_path / "parent"
    source_dir = tmp_path / "source"
    parent_dir.mkdir()
    source_dir.mkdir()
    shutil.copy2(ENGINE_PATH, parent_dir / ENGINE_PATH.name)
    shutil.copy2(ENGINE_PATH, source_dir / ENGINE_PATH.name)
    shutil.copy2(ENGINE_PXD, source_dir / ENGINE_PXD.name)

    def translate(directory: Path) -> str:
        result = subprocess.run(
            [str(CYTHON_PYTHON), "-m", "cython", "-3", ENGINE_PATH.name],
            cwd=directory,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        return (directory / "ahmad_ref_engine.c").read_text(
            encoding="utf-8", errors="replace"
        )

    parent_c = translate(parent_dir)
    source_c = translate(source_dir)
    assert "_log_fact" in parent_c
    assert "static double __pyx_f_16ahmad_ref_engine__log_fact(int" in source_c
    assert "__pyx_pw_16ahmad_ref_engine" in parent_c
    assert "__pyx_pw_16ahmad_ref_engine" in source_c
    assert "__pyx_n_u_log_fact" in parent_c
    assert "__pyx_n_u_log_fact" not in source_c


def test_bootstrap_and_build_order_keep_ed25519_without_authenticode() -> None:
    bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    build = BUILD_PATH.read_text(encoding="utf-8-sig")
    assert "WinVerifyTrust" not in bootstrap
    assert "WTHelperGetProvCertFromChain" not in bootstrap
    assert ".share_mode(FILE_SHARE_READ)" in bootstrap
    assert "bootstrap_self_identity_mismatch" in bootstrap
    assert "verify_preload_roots(root, &executable)?" in bootstrap
    assert '["-E", "-s", "-S", "-B", "-c", BOOTSTRAP]' in bootstrap
    assert (
        bootstrap.index("kernel.verify_local_integrity(root)")
        < bootstrap.index("guard.begin_namespace_lease(")
        < bootstrap.index("kernel.recheck_local_integrity(root)")
        < bootstrap.index('launcher = load_exact("_client_launcher"')
        < bootstrap.index("namespace_lease.close()")
        < bootstrap.index("kernel.release_local_integrity()")
    )
    kernel_build = build.index("maturin build --release -i $BuildPython")
    native_receipt = build.index("inspect-stage", kernel_build)
    runtime_gate = build.index(
        "# --- 6c. synthetic ABI smoke + 真实 release availability gate ---",
        native_receipt,
    )
    final_cleanup = build.index("# --- 6d. 最终字节码清理门", runtime_gate)
    final_exposure_gate = build.index("# 群发包技术暴露门", final_cleanup)
    thin_stage_cleanup = build.index(
        "Remove-Item -LiteralPath $vendorFull -Recurse -Force",
        final_exposure_gate,
    )
    full_tree_manifest = build.index("$LocalIntegrityBuilder sign", thin_stage_cleanup)
    signed_tree_gate = build.index("Invoke-LocalIntegrityGate `", full_tree_manifest)
    signed_tree_label = build.index('-Label "最终Stage签名后"', signed_tree_gate)
    runtime_receipt = build.index("--write-runtime-receipt", signed_tree_gate)
    canonical_zip = build.index("--write-canonical-zip", runtime_receipt)
    bkenc_receipt = build.index("--write-bkenc-receipt", canonical_zip)
    final_canonical_zip = build.index("--write-canonical-zip", canonical_zip + 1)
    zip_audit = build.index("# --- 7a. 冻结构建审计 authority receipt", final_canonical_zip)
    assert (
        kernel_build
        < native_receipt
        < runtime_gate
        < final_cleanup
        < final_exposure_gate
        < thin_stage_cleanup
        < full_tree_manifest
        < signed_tree_gate
        < signed_tree_label
        < runtime_receipt
        < canonical_zip
        < bkenc_receipt
        < final_canonical_zip
        < zip_audit
    )
    post_sign_finalization = build[full_tree_manifest:zip_audit]
    for covered_tree_mutation in (
        "Remove-Item -LiteralPath $vendorFull",
        "Remove-Item -LiteralPath $cacheDir.FullName",
        "Remove-Item -LiteralPath $bytecodeFile.FullName",
        "Get-ChildItem -LiteralPath $projectRoot",
        "Get-ChildItem -LiteralPath $RuntimePy -Recurse",
        "Copy-Item",
        "Move-Item",
        "Set-Content",
        "WriteAllBytes",
    ):
        assert covered_tree_mutation not in post_sign_finalization
    assert build.count("maturin build --release -i $BuildPython") == 1
    assert "LocalIntegrityPublicKeyFile" not in build
    assert "IntegritySigningKeyFile" in build
    assert "NativeSigningPolicyFile" not in build
    assert "SignToolPath" not in build
    assert "BIDKING_AUTHENTICODE_SIGNER_SHA256" not in build
    assert "native_signing_policy.json" not in build
    assert "NativeBinaryDistribution: $NativeBinaryDistribution" in build
    assert "native trust bootstrap 未复用完整性 A 派生的 Ed25519 公钥身份" in build
    assert "-Ccontrol-flow-guard=checks" in build
    for flag in ("/O2", "/GL", "/LTCG", "/OPT:REF", "/OPT:ICF", "/GUARD:CF"):
        assert flag in build
