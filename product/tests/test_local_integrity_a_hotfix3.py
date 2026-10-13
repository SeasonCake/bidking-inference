from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
CONTRACT = ROOT / "scripts" / "local_integrity_contract.json"
BUILDER = ROOT / "scripts" / "build_local_integrity_manifest.py"
KERNEL = ROOT / "native" / "bidking_kernel" / "src" / "lib.rs"
KERNEL_BUILD = ROOT / "native" / "bidking_kernel" / "build.rs"
AUDITOR = ROOT / "scripts" / "audit_release_zip.py"
NAMESPACE_GUARD = ROOT / "scripts" / "local_integrity_namespace_guard.py"
A_SOURCE = "e01f60969e766093fb3e6ba49c153a44f00fbc61"
JUNCTION_SUCCESSOR_SOURCE = "9d3aeea3e2d816a5e9897f66f7dff031925cc918"
NAMESPACE_SUCCESSOR_SOURCE = "6dd76d1424945288a7dd3a34d3f2f66f3b2feb1e"


def _load_builder_from(path: Path, name: str = "build_local_integrity_manifest"):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_builder():
    return _load_builder_from(BUILDER)


def _load_auditor():
    spec = importlib.util.spec_from_file_location("audit_release_zip_local_integrity", AUDITOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_namespace_guard():
    spec = importlib.util.spec_from_file_location(
        "local_integrity_namespace_guard_test", NAMESPACE_GUARD
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _load_namespace_guard_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _write_signing_key(path: Path) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    path.write_text(base64.b64encode(raw).decode("ascii"), encoding="ascii")
    return key


def _minimal_protection_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "critical_native": {
                    "engine_modules": ["engine_a", "engine_b", "engine_c"],
                    "tool_modules": [],
                    "overlay": {
                        "source": "hero_ref/tools/overlay.py",
                        "native_module": "_protected_overlay",
                    },
                    "package_modules": [],
                    "package_trees": [
                        {
                            "path": "bidking_lab/capture",
                            "protected_modules": ["bidking_lab/capture/parser.py"],
                        },
                        {
                            "path": "bidking_lab/secure",
                            "protected_modules": ["bidking_lab/secure/enforce.py"],
                        },
                    ],
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _minimal_stage(tmp_path: Path) -> tuple[Path, Path]:
    stage = tmp_path / "stage with spaces"
    files = {
        "BidKingLive.exe": b"MZ-launcher",
        "activation_service_endpoints.json": b"{}",
        "activation_http_e2ee.json": b"{}",
        "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs": b"table",
        "runtime/python/python.exe": b"MZ-python",
        "scripts/start_live_npcap_overlay.ps1": b"exit 0",
        "scripts/package_data_fingerprints.json": b"{\"baseline_id\":\"test\"}\n",
        "hero_ref/src/engine_a.cp313-win_amd64.pyd": b"MZ-a",
        "hero_ref/src/engine_b.cp313-win_amd64.pyd": b"MZ-b",
        "hero_ref/src/engine_c.cp313-win_amd64.pyd": b"MZ-c",
        "hero_ref/tools/_protected_overlay.cp313-win_amd64.pyd": b"MZ-overlay",
        "src/bidking_kernel.cp313-win_amd64.pyd": b"MZ-kernel",
        "src/bidking_lab/capture/parser.cp313-win_amd64.pyd": b"MZ-parser",
        "src/bidking_lab/secure/enforce.cp313-win_amd64.pyd": b"MZ-secure",
        "data/processed/catalog.enc": b"BKENC2\nprocessed",
        "data/raw/tables/Item.txt": b"BKENC2\nitems",
    }
    for relative, payload in files.items():
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    protection = tmp_path / "client-protection.json"
    _minimal_protection_manifest(protection)
    return stage, protection


def test_portable_runtime_disables_automatic_site_hooks() -> None:
    build = BUILD.read_text(encoding="utf-8-sig")
    pth_start = build.index("# 配置 ._pth")
    pth_end = build.index("# --- 3. site-packages", pth_start)
    runtime_pth = build[pth_start:pth_end]

    assert '"import site"' not in runtime_pth
    assert 'Set-Content -Path (Join-Path $dstSite "sitecustomize.py")' not in build
    assert '"import os",\n    "_rt = os.path.dirname' not in build
    assert '$env:TCL_LIBRARY = Join-Path $RuntimeRoot "tcl\\tcl8.6"' in build
    assert '$env:TK_LIBRARY = Join-Path $RuntimeRoot "tcl\\tk8.6"' in build


def test_encrypted_launcher_runs_native_integrity_before_product_code() -> None:
    build = BUILD.read_text(encoding="utf-8-sig")
    shell_at = build.index("$publicLauncherShell = @'")
    shell_end = build.index("'@.Replace", shell_at)
    shell = build[shell_at:shell_end]

    gate_at = shell.index("verify_local_integrity")
    seal_at = shell.index("begin_namespace_lease")
    namespace_recheck_at = shell.index("recheck_local_integrity")
    product_at = shell.index("from _client_launcher import main")
    assert gate_at < seal_at < namespace_recheck_at < product_at
    assert '"-E", "-s", "-S", "-B", "-c"' in shell
    assert "local_integrity_failed" in shell
    assert 'glob("bidking_kernel*.pyd")' in shell
    assert '_load_exact("bidking_kernel", _candidates[0])' in shell
    assert "sys.modules[name] = module" in shell
    assert "_namespace_lease.close()" in shell
    assert "_kernel.release_local_integrity()" in shell
    assert "except BaseException as _bootstrap_error:" in shell
    assert "if _namespace_lease is not None:" in shell
    assert shell.index("_namespace_lease.close()") < shell.index(
        "_kernel.release_local_integrity()"
    )
    assert "from _client_launcher import diagnostics_main" in build
    assert '__BIDKING_INTEGRITY_BOOTSTRAP__", $integrityBootstrap' in build
    assert "base64.b64decode(sys.argv.pop(1),validate=True)" in build
    assert "& $PythonPath -E -s -S -B -c $pythonBootstrapEntry $diagnosticsPayload" in build
    assert "& $verifyPy -E -s -B -c $verifyCode" in build
    assert "& $verifyPy -s -B -c $gateCode" in build

    public_bootstrap_start = shell.index('$integrityBootstrap = @"\n') + len(
        '$integrityBootstrap = @"\n'
    )
    public_bootstrap_end = shell.index('\n"@\n$runnerCode', public_bootstrap_start)
    compile(shell[public_bootstrap_start:public_bootstrap_end], "<public-integrity>", "exec")

    build_gate_start = build.index("$localIntegrityGateCode = @'\n") + len(
        "$localIntegrityGateCode = @'\n"
    )
    build_gate_end = build.index("\n'@\nfunction Invoke-LocalIntegrityGate", build_gate_start)
    compile(build[build_gate_start:build_gate_end], "<build-integrity>", "exec")


def test_build_requires_offline_signer_and_binds_public_key_into_kernel() -> None:
    build = BUILD.read_text(encoding="utf-8-sig")
    kernel_build = KERNEL_BUILD.read_text(encoding="utf-8")

    assert "IntegritySigningKeyFile" in build
    assert "BIDKING_INTEGRITY_PUBLIC_KEY_B64" in build
    assert "build_local_integrity_manifest.py" in build
    assert "local_integrity_contract.json" in build
    assert "BIDKING_INTEGRITY_PUBLIC_KEY_B64" in kernel_build
    assert "INTEGRITY_PUBLIC_KEY" in kernel_build
    assert "BIDKING_INTEGRITY_PRIVATE" not in build


def test_native_verifier_uses_signed_manifest_and_stable_file_identity() -> None:
    kernel = KERNEL.read_text(encoding="utf-8")

    assert "ed25519_dalek" in kernel
    assert "verify_local_integrity" in kernel
    assert "read_stable_file" in kernel
    assert "dwVolumeSerialNumber" in kernel
    assert "nFileIndexHigh" in kernel
    assert "share_mode(FILE_SHARE_READ)" in kernel
    assert "VERIFIED_LOCAL_INTEGRITY_HANDLES" in kernel
    assert "*retained = verified_handles" in kernel
    assert "integrity_signature_invalid" in kernel
    assert "integrity_file_identity_changed" in kernel
    assert "integrity_file_hash_mismatch" in kernel
    assert "collect_covered_paths" in kernel
    assert "integrity_coverage_mismatch" in kernel
    assert "StablePathGuard" in kernel
    assert "FILE_FLAG_BACKUP_SEMANTICS" in kernel
    assert "integrity_member_identity_collision" in kernel
    assert "VERIFIED_LOCAL_INTEGRITY_NAMESPACE" in kernel
    assert "collect_package_namespace" in kernel
    assert "FindFirstStreamW" in kernel
    assert "FindNextStreamW" in kernel
    assert "snapshot_file_streams" in kernel
    assert "integrity_named_stream_rejected" in kernel
    assert "integrity_stream_namespace_changed" in kernel
    assert "recheck_local_integrity" in kernel
    assert "release_local_integrity" in kernel


@pytest.mark.skipif(sys.platform != "win32", reason="Windows namespace control")
def test_exact_junction_successor_known_fail_allows_post_gate_child(
    tmp_path: Path,
) -> None:
    source = subprocess.run(
        ["git", "show", f"{JUNCTION_SUCCESSOR_SOURCE}:native/bidking_kernel/src/lib.rs"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert "FILE_SHARE_READ" in source
    assert "FILE_FLAG_BACKUP_SEMANTICS" in source
    assert "ReadDirectoryChangesW" not in source

    package = tmp_path / "post-gate-parent"
    src = package / "src"
    src.mkdir(parents=True)
    existing = src / "existing.py"
    existing.write_text("SIGNED = True\n", encoding="utf-8")
    handles = [
        _open_windows_read_handle(package, directory=True),
        _open_windows_read_handle(src, directory=True),
        _open_windows_read_handle(existing, directory=False),
    ]
    try:
        with pytest.raises(OSError):
            existing.write_text("OVERWRITTEN = True\n", encoding="utf-8")
        injected = src / "injected_after_gate.py"
        injected.write_text("MARKER = 'external-child'\n", encoding="utf-8")
        spec = importlib.util.spec_from_file_location("injected_after_gate", injected)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.MARKER == "external-child"
    finally:
        for handle in reversed(handles):
            _close_windows_handle(handle)


def _open_windows_read_handle(path: Path, *, directory: bool) -> int:
    import ctypes
    from ctypes import wintypes

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
    flags = 0x0020_0000 | (0x0200_0000 if directory else 0)
    handle = create_file(str(path), 0x8000_0000, 0x0000_0001, None, 3, flags, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    return int(handle)


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    assert close_handle(handle)


def _current_windows_sid() -> str:
    completed = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    row = next(csv.reader([completed.stdout.strip()]))
    assert len(row) >= 2 and row[1].startswith("S-1-")
    return row[1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ADS control")
def test_exact_namespace_successor_known_fail_allows_ads_import(
    tmp_path: Path,
) -> None:
    source = subprocess.run(
        [
            "git",
            "show",
            f"{NAMESPACE_SUCCESSOR_SOURCE}:scripts/local_integrity_namespace_guard.py",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    source_path = tmp_path / "parent_namespace_guard.py"
    source_path.write_text(source, encoding="utf-8", newline="\n")
    parent_guard = _load_namespace_guard_from(
        source_path, "parent_namespace_guard_ads_known_fail"
    )

    package = tmp_path / "parent-package"
    existing = package / "src/existing.py"
    existing.parent.mkdir(parents=True)
    existing.write_text("SIGNED = True\n", encoding="utf-8")
    handle = _open_windows_read_handle(existing, directory=False)
    sid = _current_windows_sid()
    locked_sddl = (
        f"O:{sid}G:{sid}D:P"
        "(A;OICI;FA;;;SY)"
        f"(A;OICI;FRFXRCWDWO;;;{sid})"
    )
    lease = parent_guard._begin_namespace_lease(
        package,
        locked_sddl=locked_sddl,
        expected_owner=sid,
        recovery_root=tmp_path / "parent-recovery",
        require_elevated=False,
        secure_recovery=False,
    )
    stream = Path(f"{existing}:injected_after_gate.py")
    child_script = """
import importlib.util, json, pathlib, sys
stream = pathlib.Path(sys.argv[1])
result = {}
try:
    stream.write_text("MARKER = 'ads-loaded'\\n", encoding="utf-8")
except OSError:
    result["write"] = "blocked"
else:
    result["write"] = "created"
    spec = importlib.util.spec_from_file_location("ads_injected", stream)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result["import"] = module.MARKER
print(json.dumps(result, sort_keys=True))
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", child_script, str(stream)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert json.loads(completed.stdout) == {
            "import": "ads-loaded",
            "write": "created",
        }
        lease.attest()
    finally:
        lease.close()
        try:
            stream.unlink()
        except FileNotFoundError:
            pass
        _close_windows_handle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows namespace control")
def test_namespace_lease_blocks_low_privilege_child_across_all_load_classes(
    tmp_path: Path,
) -> None:
    guard = _load_namespace_guard()
    package = tmp_path / "package"
    targets = {
        "python": package / "runtime/python/Lib/site-packages/injected.py",
        "tcl": package / "runtime/python/tcl/tk8.6/injected.tcl",
        "dll": package / "runtime/python/injected.dll",
        "native": package / "src/injected.cp313-win_amd64.pyd",
        "data": package / "data/processed/injected.json",
        "directory": package / "hero_ref/src/injected-package",
        "hardlink": package / "scripts/injected-hardlink.py",
    }
    for target in targets.values():
        target.parent.mkdir(parents=True, exist_ok=True)
    existing = package / "src/existing.cp313-win_amd64.pyd"
    existing.write_bytes(b"SIGNED-NATIVE")
    unsigned_existing = package / "vendor/mitmproxy/mitmdump.exe"
    unsigned_existing.parent.mkdir(parents=True, exist_ok=True)
    unsigned_existing.write_bytes(b"EXISTING-VENDOR")
    stream_targets = {
        "signed_python": package / "src/existing.py",
        "signed_tcl": package / "runtime/python/tcl/tk8.6/existing.tcl",
        "signed_dll": package / "runtime/python/existing.dll",
        "signed_native": existing,
        "signed_data": package / "data/processed/existing.json",
        "unsigned_exe": unsigned_existing,
    }
    for path in stream_targets.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"EXISTING")
    original_security = {
        path: guard._security_descriptor(path)
        for path in (package, package / "src", unsigned_existing)
    }
    handles = [
        _open_windows_read_handle(existing, directory=False),
        _open_windows_read_handle(unsigned_existing, directory=False),
    ]
    sid = _current_windows_sid()
    locked_directory_sddl = (
        f"O:{sid}G:{sid}D:P"
        "(A;OICI;FA;;;SY)"
        f"(A;OICI;FRFXRCWDWO;;;{sid})"
    )
    locked_file_sddl = (
        f"O:{sid}G:{sid}D:P"
        "(A;;FA;;;SY)"
        f"(A;;FRFXRCWDWO;;;{sid})"
    )
    lease = guard._begin_namespace_lease(
        package,
        locked_directory_sddl=locked_directory_sddl,
        locked_file_sddl=locked_file_sddl,
        expected_owner=sid,
        recovery_root=tmp_path / "recovery",
        require_elevated=False,
        secure_recovery=False,
    )
    child_script = """
import json, os, pathlib, sys
targets = json.loads(sys.argv[1])
source = pathlib.Path(sys.argv[2])
unsigned_existing = pathlib.Path(sys.argv[3])
result = {}
for label, raw in targets.items():
    target = pathlib.Path(raw)
    try:
        if label == 'directory':
            target.mkdir()
        elif label == 'hardlink':
            os.link(source, target)
        else:
            target.write_bytes(b'INJECTED')
    except OSError:
        result[label] = 'blocked'
    else:
        result[label] = 'created'
try:
    source.write_bytes(b'OVERWRITE')
except OSError:
    result['existing_overwrite'] = 'blocked'
else:
    result['existing_overwrite'] = 'written'
try:
    unsigned_existing.write_bytes(b'OVERWRITE')
except OSError:
    result['unsigned_existing_overwrite'] = 'blocked'
else:
    result['unsigned_existing_overwrite'] = 'written'
stream_targets = json.loads(sys.argv[4])
for label, raw in stream_targets.items():
    stream = pathlib.Path(raw + ':injected_after_gate.py')
    try:
        stream.write_text("MARKER = 'ads-loaded'\\n", encoding='utf-8')
    except OSError:
        result['ads_' + label] = 'blocked'
    else:
        result['ads_' + label] = 'created'
        if label == 'signed_python':
            import importlib.util
            spec = importlib.util.spec_from_file_location('ads_injected', stream)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result['ads_import'] = module.MARKER
sys.path.insert(0, str(pathlib.Path(targets['python']).parent))
try:
    __import__('injected')
except ModuleNotFoundError:
    result['python_import'] = 'blocked'
else:
    result['python_import'] = 'loaded'
print(json.dumps(result, sort_keys=True))
"""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                child_script,
                json.dumps({key: str(value) for key, value in targets.items()}),
                str(existing),
                str(unsigned_existing),
                json.dumps(
                    {key: str(value) for key, value in stream_targets.items()}
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        assert set(result.values()) == {"blocked"}
        lease.attest()
    finally:
        lease.close()
        for handle in handles:
            _close_windows_handle(handle)

    restored = package / "src/post-close.py"
    restored.write_text("RESTORED = True\n", encoding="utf-8")
    assert restored.is_file()
    assert {
        path: guard._security_descriptor(path)
        for path in (package, package / "src", unsigned_existing)
    } == original_security
    assert not list((tmp_path / "recovery").glob("*.json"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ADS control")
@pytest.mark.parametrize(
    "relative",
    ["src/signed.py", "vendor/unsigned.exe"],
    ids=["signed-member", "unsigned-member"],
)
def test_namespace_lease_rejects_preexisting_named_stream(
    tmp_path: Path, relative: str
) -> None:
    guard = _load_namespace_guard()
    package = tmp_path / "package"
    member = package / relative
    member.parent.mkdir(parents=True)
    member.write_bytes(b"BASE")
    stream = Path(f"{member}:preexisting")
    stream.write_bytes(b"STREAM")
    original = {
        path: guard._security_descriptor(path)
        for path in (package, member.parent, member)
    }
    try:
        with pytest.raises(
            guard.NamespaceGuardError, match="namespace_named_stream_rejected"
        ):
            guard._begin_namespace_lease(
                package,
                locked_directory_sddl=(
                    f"O:{_current_windows_sid()}G:{_current_windows_sid()}D:P"
                    "(A;OICI;FA;;;SY)"
                    f"(A;OICI;FRFXRCWDWO;;;{_current_windows_sid()})"
                ),
                locked_file_sddl=(
                    f"O:{_current_windows_sid()}G:{_current_windows_sid()}D:P"
                    "(A;;FA;;;SY)"
                    f"(A;;FRFXRCWDWO;;;{_current_windows_sid()})"
                ),
                expected_owner=_current_windows_sid(),
                recovery_root=tmp_path / "recovery",
                require_elevated=False,
                secure_recovery=False,
            )
        assert {
            path: guard._security_descriptor(path)
            for path in (package, member.parent, member)
        } == original
        assert not list((tmp_path / "recovery").glob("*.json"))
    finally:
        stream.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"[ZoneTransfer]\r\nZoneId=3\r\n", True),
        (
            b"\xef\xbb\xbf[zonetransfer]\nzoneid = 4\n"
            b"HostUrl=https://example.test/a?b=c\n",
            True,
        ),
        (b"; browser metadata\n[ZoneTransfer]\nZoneId=3\n", True),
        (b"", False),
        (b"[ZoneTransfer]\nZoneId=2\n", False),
        (b"[ZoneTransfer]\nZoneId=3\nZoneId=4\n", False),
        (b"[Other]\nZoneId=3\n", False),
        (b"ZoneId=3\n", False),
        (b"[ZoneTransfer]\nBad Key=value\nZoneId=3\n", False),
        (b"[ZoneTransfer]\x00ZoneId=3\n", False),
        (b"\xff[ZoneTransfer]\nZoneId=3\n", False),
    ],
)
def test_zone_identifier_payload_contract(payload: bytes, expected: bool) -> None:
    guard = _load_namespace_guard()
    assert guard._valid_windows_zone_identifier(payload) is expected


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ADS control")
def test_namespace_lease_accepts_only_valid_internet_zone_identifier(
    tmp_path: Path,
) -> None:
    guard = _load_namespace_guard()
    package = tmp_path / "package"
    member = package / "src/signed.py"
    member.parent.mkdir(parents=True)
    member.write_bytes(b"BASE")
    stream = Path(f"{member}:Zone.Identifier")
    stream.write_bytes(
        b"[ZoneTransfer]\r\nZoneId=3\r\n"
        b"HostUrl=https://example.test/download.zip\r\n"
    )
    original = {
        path: guard._security_descriptor(path)
        for path in (package, member.parent, member)
    }
    lease = guard._begin_namespace_lease(
        package,
        locked_directory_sddl=(
            f"O:{_current_windows_sid()}G:{_current_windows_sid()}D:P"
            "(A;OICI;FA;;;SY)"
            f"(A;OICI;FRFXRCWDWO;;;{_current_windows_sid()})"
        ),
        locked_file_sddl=(
            f"O:{_current_windows_sid()}G:{_current_windows_sid()}D:P"
            "(A;;FA;;;SY)"
            f"(A;;FRFXRCWDWO;;;{_current_windows_sid()})"
        ),
        expected_owner=_current_windows_sid(),
        recovery_root=tmp_path / "recovery",
        require_elevated=False,
        secure_recovery=False,
    )
    try:
        lease.attest()
        assert stream.read_bytes().startswith(b"[ZoneTransfer]")
    finally:
        lease.close()
    assert {
        path: guard._security_descriptor(path)
        for path in (package, member.parent, member)
    } == original
    stream.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ADS control")
@pytest.mark.parametrize(
    "payload",
    [
        b"[ZoneTransfer]\nZoneId=2\n",
        b"[ZoneTransfer]\nZoneId=3\nZoneId=4\n",
        b"[Other]\nZoneId=3\n",
        b"[ZoneTransfer]\x00ZoneId=3\n",
    ],
)
def test_namespace_lease_rejects_malformed_zone_identifier(
    tmp_path: Path, payload: bytes
) -> None:
    guard = _load_namespace_guard()
    package = tmp_path / "package"
    member = package / "src/signed.py"
    member.parent.mkdir(parents=True)
    member.write_bytes(b"BASE")
    stream = Path(f"{member}:Zone.Identifier")
    stream.write_bytes(payload)
    try:
        with pytest.raises(
            guard.NamespaceGuardError, match="namespace_named_stream_rejected"
        ):
            guard._begin_namespace_lease(
                package,
                locked_directory_sddl=(
                    f"O:{_current_windows_sid()}G:{_current_windows_sid()}D:P"
                    "(A;OICI;FA;;;SY)"
                    f"(A;OICI;FRFXRCWDWO;;;{_current_windows_sid()})"
                ),
                locked_file_sddl=(
                    f"O:{_current_windows_sid()}G:{_current_windows_sid()}D:P"
                    "(A;;FA;;;SY)"
                    f"(A;;FRFXRCWDWO;;;{_current_windows_sid()})"
                ),
                expected_owner=_current_windows_sid(),
                recovery_root=tmp_path / "recovery",
                require_elevated=False,
                secure_recovery=False,
            )
    finally:
        stream.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ADS control")
def test_file_acl_blocks_existing_stream_modify_delete_and_rename(
    tmp_path: Path,
) -> None:
    guard = _load_namespace_guard()
    member = tmp_path / "existing.py"
    member.write_bytes(b"BASE")
    stream = Path(f"{member}:existing")
    renamed = Path(f"{member}:renamed")
    stream.write_bytes(b"STREAM")
    original_directory_sddl, original_directory_protected, _owner = (
        guard._security_descriptor(tmp_path)
    )
    original_sddl, original_protected, _owner = guard._security_descriptor(member)
    sid = _current_windows_sid()
    locked_directory_sddl = (
        f"O:{sid}G:{sid}D:P(A;OICI;FA;;;SY)"
        f"(A;OICI;FRFXRCWDWO;;;{sid})"
    )
    locked_file_sddl = (
        f"O:{sid}G:{sid}D:P(A;;FA;;;SY)(A;;FRFXRCWDWO;;;{sid})"
    )
    guard._set_security_descriptor(tmp_path, locked_directory_sddl, protected=True)
    guard._set_security_descriptor(member, locked_file_sddl, protected=True)
    child = """
import json, os, pathlib, sys
stream, renamed = map(pathlib.Path, sys.argv[1:])
result = {}
for operation in ('modify', 'delete', 'rename'):
    try:
        if operation == 'modify':
            stream.write_bytes(b'CHANGED')
        elif operation == 'delete':
            stream.unlink()
        else:
            os.replace(stream, renamed)
    except OSError:
        result[operation] = 'blocked'
    else:
        result[operation] = 'allowed'
print(json.dumps(result, sort_keys=True))
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", child, str(stream), str(renamed)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        assert set(result.values()) == {"blocked"}, result
        assert stream.read_bytes() == b"STREAM"
        assert not renamed.exists()
    finally:
        guard._set_security_descriptor(
            member, original_sddl, protected=original_protected
        )
        guard._set_security_descriptor(
            tmp_path,
            original_directory_sddl,
            protected=original_directory_protected,
        )
        stream.unlink(missing_ok=True)
        renamed.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL restore control")
def test_namespace_lease_exception_restores_files_and_replays_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = _load_namespace_guard()
    package = tmp_path / "package"
    for relative in ("src/one.py", "vendor/two.exe"):
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("ascii"))
    protected = [package, package / "src", package / "vendor", *(package.rglob("*.*"))]
    original = {path: guard._security_descriptor(path) for path in protected}
    real_set = guard._set_security_descriptor
    calls = 0

    def fail_once(path: Path, sddl: str, *, protected: bool) -> None:
        nonlocal calls
        calls += 1
        if calls == 5:
            raise guard.NamespaceGuardError("injected_security_apply_failure")
        real_set(path, sddl, protected=protected)

    sid = _current_windows_sid()
    directory_sddl = (
        f"O:{sid}G:{sid}D:P(A;OICI;FA;;;SY)(A;OICI;FRFXRCWDWO;;;{sid})"
    )
    file_sddl = f"O:{sid}G:{sid}D:P(A;;FA;;;SY)(A;;FRFXRCWDWO;;;{sid})"
    monkeypatch.setattr(guard, "_set_security_descriptor", fail_once)
    with pytest.raises(
        guard.NamespaceGuardError, match="injected_security_apply_failure"
    ):
        guard._begin_namespace_lease(
            package,
            locked_directory_sddl=directory_sddl,
            locked_file_sddl=file_sddl,
            expected_owner=sid,
            recovery_root=tmp_path / "recovery",
            require_elevated=False,
            secure_recovery=False,
        )
    monkeypatch.setattr(guard, "_set_security_descriptor", real_set)
    assert {path: guard._security_descriptor(path) for path in protected} == original
    assert len(list((tmp_path / "recovery").glob("*.json"))) == 1

    lease = guard._begin_namespace_lease(
        package,
        locked_directory_sddl=directory_sddl,
        locked_file_sddl=file_sddl,
        expected_owner=sid,
        recovery_root=tmp_path / "recovery",
        require_elevated=False,
        secure_recovery=False,
    )
    lease.close()
    assert {path: guard._security_descriptor(path) for path in protected} == original
    assert not list((tmp_path / "recovery").glob("*.json"))


def test_namespace_guard_public_contract_has_no_weak_runtime_switch() -> None:
    guard = _load_namespace_guard()
    build = BUILD.read_text(encoding="utf-8-sig")
    protection = json.loads(
        (ROOT / "scripts/client_protection_manifest.json").read_text(encoding="utf-8")
    )

    assert guard._ADMINISTRATORS_SID == "S-1-5-32-544"
    assert "O:BA" in guard._PRODUCTION_DIRECTORY_SDDL
    assert "(A;OICI;FRFX;;;BU)" in guard._PRODUCTION_DIRECTORY_SDDL
    assert "O:BA" in guard._PRODUCTION_FILE_SDDL
    assert "(A;;FRFX;;;BU)" in guard._PRODUCTION_FILE_SDDL
    assert "require_elevated=True" in NAMESPACE_GUARD.read_text(encoding="utf-8")
    assert "secure_recovery=True" in NAMESPACE_GUARD.read_text(encoding="utf-8")
    assert "local_integrity_namespace_guard.py" in build
    assert (
        "scripts/local_integrity_namespace_guard.py"
        in protection["packaged_plaintext_python_allowlist"]
    )
    assert "BIDKING_DISABLE_LOCAL_INTEGRITY" not in build
    assert "BIDKING_DISABLE_LOCAL_INTEGRITY" not in NAMESPACE_GUARD.read_text(
        encoding="utf-8"
    )


def test_protected_public_launcher_routes_mutable_logs_outside_package() -> None:
    launcher = (ROOT / "scripts/local_compat_launcher.py").read_text(encoding="utf-8")
    lifecycle = (
        ROOT / "native/bidking_kernel/src/client_lifecycle.rs"
    ).read_text(encoding="utf-8")
    overlay = (ROOT / "scripts/start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "_run_powershell_source" not in launcher
    assert 'env::var_os("LOCALAPPDATA")' in lifecycle
    assert '.env("BIDKING_EMBED_LOG_DIR", runtime_log_dir)' in lifecycle
    assert "log_folded.starts_with(&package_prefix)" in lifecycle
    assert "LogDir = $env:BIDKING_EMBED_LOG_DIR" in lifecycle
    assert "[System.IO.Path]::IsPathRooted($LogDir)" in overlay


def test_builder_signs_space_path_and_tamper_fails_signature(tmp_path: Path) -> None:
    builder = _load_builder()
    auditor = _load_auditor()
    stage, protection = _minimal_stage(tmp_path)
    key_path = tmp_path / "offline signer.key"
    private_key = _write_signing_key(key_path)

    receipt = builder.build_signed_manifest(
        stage=stage,
        contract_path=CONTRACT,
        protection_manifest_path=protection,
        signing_key_path=key_path,
        release_id="v0.3.2-hotfix3:test",
    )
    manifest_path = stage / "integrity" / "local_integrity_manifest.json"
    signature_path = stage / "integrity" / "local_integrity_manifest.sig"
    manifest_bytes = manifest_path.read_bytes()
    signature = base64.b64decode(
        signature_path.read_text(encoding="ascii").strip(), validate=True
    )
    private_key.public_key().verify(signature, manifest_bytes)

    manifest = json.loads(manifest_bytes)
    paths = {entry["path"] for entry in manifest["entries"]}
    assert "BidKingLive.exe" in paths
    fingerprint_entry = next(
        entry
        for entry in manifest["entries"]
        if entry["path"] == "scripts/package_data_fingerprints.json"
    )
    assert fingerprint_entry == {
        "bytes": len(b'{"baseline_id":"test"}\n'),
        "path": "scripts/package_data_fingerprints.json",
        "role": "entry",
        "sha256": hashlib.sha256(b'{"baseline_id":"test"}\n').hexdigest(),
    }
    assert "src/bidking_kernel.cp313-win_amd64.pyd" in paths
    assert receipt["entry_count"] == len(paths)
    assert receipt["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()

    tampered = manifest_bytes.replace(
        b'"release_id":"v0.3.2-hotfix3:test"',
        b'"release_id":"v0.3.2-hotfix3:evil"',
        1,
    )
    assert tampered != manifest_bytes
    with pytest.raises(Exception):
        private_key.public_key().verify(signature, tampered)

    public_receipt = builder.public_key_receipt(key_path)
    authority = {
        "manifest_name": "integrity/local_integrity_manifest.json",
        "manifest_sha256": receipt["manifest_sha256"],
        "signature_name": "integrity/local_integrity_manifest.sig",
        "public_key_b64": public_receipt["public_key_b64"],
        "public_key_sha256": public_receipt["public_key_sha256"],
        "entry_count": receipt["entry_count"],
        "native_gate_verified": True,
    }
    candidate = tmp_path / "candidate.zip"
    with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(stage).as_posix())
    with zipfile.ZipFile(candidate) as archive:
        assert auditor._local_integrity_issues(archive, authority) == []

    fingerprint_tampered_candidate = tmp_path / "candidate-fingerprint-tampered.zip"
    with zipfile.ZipFile(candidate) as source, zipfile.ZipFile(
        fingerprint_tampered_candidate, "w", compression=zipfile.ZIP_STORED
    ) as target:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "scripts/package_data_fingerprints.json":
                payload = b"X" + payload[1:]
            target.writestr(info, payload)
    with zipfile.ZipFile(fingerprint_tampered_candidate) as archive:
        assert "local_integrity_member_hash_mismatch" in auditor._local_integrity_issues(
            archive, authority
        )

    tampered_candidate = tmp_path / "candidate-tampered.zip"
    with zipfile.ZipFile(candidate) as source, zipfile.ZipFile(
        tampered_candidate, "w", compression=zipfile.ZIP_STORED
    ) as target:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "data/raw/tables/Item.txt":
                payload += b"tampered"
            target.writestr(info, payload)
    with zipfile.ZipFile(tampered_candidate) as archive:
        assert "local_integrity_member_size_mismatch" in auditor._local_integrity_issues(
            archive, authority
        )

    injected_candidate = tmp_path / "candidate-injected.zip"
    with zipfile.ZipFile(candidate) as source, zipfile.ZipFile(
        injected_candidate, "w", compression=zipfile.ZIP_STORED
    ) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr("src/unlisted.cp313-win_amd64.pyd", b"MZ-injected")
    with zipfile.ZipFile(injected_candidate) as archive:
        assert "local_integrity_coverage_mismatch" in auditor._local_integrity_issues(
            archive, authority
        )


def test_cleanup_must_precede_sign_and_post_sign_delete_or_tamper_fails(
    tmp_path: Path,
) -> None:
    builder = _load_builder()
    auditor = _load_auditor()

    def prepare(root: Path) -> tuple[Path, Path, Path]:
        root.mkdir()
        stage, protection = _minimal_stage(root)
        bytecode = (
            stage
            / "scripts"
            / "__pycache__"
            / "local_compat_addon.cpython-313.pyc"
        )
        bytecode.parent.mkdir()
        bytecode.write_bytes(b"attempt3-regenerated-bytecode")
        key_path = root / "offline-signer.key"
        _write_signing_key(key_path)
        return stage, protection, key_path

    def sign(stage: Path, protection: Path, key_path: Path) -> dict[str, object]:
        receipt = builder.build_signed_manifest(
            stage=stage,
            contract_path=CONTRACT,
            protection_manifest_path=protection,
            signing_key_path=key_path,
            release_id="v0.3.2-hotfix3:cleanup-order",
        )
        public_receipt = builder.public_key_receipt(key_path)
        return {
            "manifest_name": "integrity/local_integrity_manifest.json",
            "manifest_sha256": receipt["manifest_sha256"],
            "signature_name": "integrity/local_integrity_manifest.sig",
            "public_key_b64": public_receipt["public_key_b64"],
            "public_key_sha256": public_receipt["public_key_sha256"],
            "entry_count": receipt["entry_count"],
            "native_gate_verified": True,
        }

    def verify(stage: Path, authority: dict[str, object], name: str) -> list[str]:
        candidate = tmp_path / name
        with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
        with zipfile.ZipFile(candidate) as archive:
            return auditor._local_integrity_issues(archive, authority)

    red_stage, red_protection, red_key = prepare(tmp_path / "sign-before-cleanup")
    red_authority = sign(red_stage, red_protection, red_key)
    shutil.rmtree(red_stage / "scripts" / "__pycache__")
    red_issues = verify(red_stage, red_authority, "sign-before-cleanup.zip")
    assert "local_integrity_member_invalid" in red_issues
    assert "local_integrity_coverage_mismatch" in red_issues

    final_stage, final_protection, final_key = prepare(tmp_path / "cleanup-before-sign")
    shutil.rmtree(final_stage / "scripts" / "__pycache__")
    final_authority = sign(final_stage, final_protection, final_key)
    assert verify(final_stage, final_authority, "final-tree.zip") == []

    item = final_stage / "data" / "raw" / "tables" / "Item.txt"
    item.write_bytes(item.read_bytes() + b"tampered-after-sign")
    assert "local_integrity_member_size_mismatch" in verify(
        final_stage, final_authority, "tampered-after-sign.zip"
    )
    item.write_bytes(b"BKENC2\nitems")

    (final_stage / "scripts" / "start_live_npcap_overlay.ps1").unlink()
    deleted_issues = verify(final_stage, final_authority, "deleted-after-sign.zip")
    assert "local_integrity_member_invalid" in deleted_issues
    assert "local_integrity_coverage_mismatch" in deleted_issues


def test_builder_rejects_missing_key_member_and_reparse_input(tmp_path: Path) -> None:
    builder = _load_builder()
    stage, protection = _minimal_stage(tmp_path)
    key_path = tmp_path / "signer.key"
    _write_signing_key(key_path)

    with pytest.raises(builder.ContractError, match="signing_key_missing"):
        builder.build_signed_manifest(
            stage=stage,
            contract_path=CONTRACT,
            protection_manifest_path=protection,
            signing_key_path=tmp_path / "missing.key",
            release_id="missing-key",
        )

    (stage / "BidKingLive.exe").unlink()
    with pytest.raises(builder.ContractError, match="required_member_missing"):
        builder.build_signed_manifest(
            stage=stage,
            contract_path=CONTRACT,
            protection_manifest_path=protection,
            signing_key_path=key_path,
            release_id="missing-member",
        )

    (stage / "BidKingLive.exe").write_bytes(b"MZ-launcher")
    source = tmp_path / "outside.bin"
    source.write_bytes(b"outside")
    link = stage / "data" / "processed" / "linked.bin"
    try:
        os.symlink(source, link)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    with pytest.raises(builder.ContractError, match="reparse_or_symlink_rejected"):
        builder.build_signed_manifest(
            stage=stage,
            contract_path=CONTRACT,
            protection_manifest_path=protection,
            signing_key_path=key_path,
            release_id="reparse",
        )


def _make_windows_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction control")
def test_exact_a_source_known_fail_accepts_fixed_member_ancestor_junction(
    tmp_path: Path,
) -> None:
    parent_source = subprocess.run(
        ["git", "show", f"{A_SOURCE}:scripts/build_local_integrity_manifest.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    parent_path = tmp_path / "parent_builder.py"
    parent_path.write_text(parent_source, encoding="utf-8", newline="\n")
    parent_builder = _load_builder_from(parent_path, "parent_integrity_builder")
    stage, _protection = _minimal_stage(tmp_path)
    ancestor = stage / "external_references" / "AuctionAnalyzer4.13.3"
    child_relative = Path(
        "_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs"
    )
    external = tmp_path / "external-parent-control"
    external_child = external / child_relative
    external_child.parent.mkdir(parents=True)
    external_child.write_bytes(b"EXTERNAL-BYTES")
    shutil.rmtree(ancestor)
    _make_windows_junction(ancestor, external)
    try:
        final_member = ancestor / child_relative
        assert not (
            int(final_member.stat(follow_symlinks=False).st_file_attributes) & 0x400
        )
        assert parent_builder._read_stable_file(final_member) == b"EXTERNAL-BYTES"
    finally:
        os.rmdir(ancestor)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction control")
def test_builder_rejects_package_root_junction(tmp_path: Path) -> None:
    builder = _load_builder()
    real_stage, protection = _minimal_stage(tmp_path)
    key_path = tmp_path / "signer.key"
    _write_signing_key(key_path)
    junction_stage = tmp_path / "junction-stage"
    _make_windows_junction(junction_stage, real_stage)
    try:
        with pytest.raises(
            builder.ContractError, match="reparse_or_symlink_rejected"
        ):
            builder.build_signed_manifest(
                stage=junction_stage,
                contract_path=CONTRACT,
                protection_manifest_path=protection,
                signing_key_path=key_path,
                release_id="root-junction",
            )
    finally:
        os.rmdir(junction_stage)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction control")
@pytest.mark.parametrize(
    ("ancestor_relative", "child_relative"),
    [
        (
            "external_references/AuctionAnalyzer4.13.3",
            "_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs",
        ),
        ("data/processed", "catalog.enc"),
        ("data/processed/nested", "inside.enc"),
    ],
    ids=["fixed-deep-member", "tree-root", "tree-nested-directory"],
)
def test_builder_rejects_ancestor_junction_for_every_member_shape(
    tmp_path: Path,
    ancestor_relative: str,
    child_relative: str,
) -> None:
    builder = _load_builder()
    stage, protection = _minimal_stage(tmp_path)
    key_path = tmp_path / "signer.key"
    _write_signing_key(key_path)
    ancestor = stage / ancestor_relative
    original_child = ancestor / child_relative
    original_child.parent.mkdir(parents=True, exist_ok=True)
    original_child.write_bytes(b"IN-STAGE-BYTES")
    external = tmp_path / f"external-{ancestor.name}"
    external_child = external / child_relative
    external_child.parent.mkdir(parents=True, exist_ok=True)
    external_child.write_bytes(b"EXTERNAL-BYTES")
    shutil.rmtree(ancestor)
    _make_windows_junction(ancestor, external)
    try:
        assert int(ancestor.stat(follow_symlinks=False).st_file_attributes) & 0x400
        assert not (
            int(original_child.stat(follow_symlinks=False).st_file_attributes) & 0x400
        )
        with pytest.raises(
            builder.ContractError, match="reparse_or_symlink_rejected"
        ):
            builder.build_signed_manifest(
                stage=stage,
                contract_path=CONTRACT,
                protection_manifest_path=protection,
                signing_key_path=key_path,
                release_id=f"ancestor-junction-{ancestor.name}",
            )
    finally:
        os.rmdir(ancestor)


def test_builder_rejects_cross_entry_hardlink_identity(tmp_path: Path) -> None:
    builder = _load_builder()
    stage, protection = _minimal_stage(tmp_path)
    key_path = tmp_path / "signer.key"
    _write_signing_key(key_path)
    source = stage / "data" / "processed" / "catalog.enc"
    alias = stage / "data" / "raw" / "tables" / "Item.txt"
    alias.unlink()
    try:
        os.link(source, alias)
    except OSError:
        pytest.skip("hardlink creation unavailable")
    assert source.stat().st_ino == alias.stat().st_ino
    with pytest.raises(
        builder.ContractError, match="member_file_identity_collision"
    ):
        builder.build_signed_manifest(
            stage=stage,
            contract_path=CONTRACT,
            protection_manifest_path=protection,
            signing_key_path=key_path,
            release_id="hardlink-collision",
        )
