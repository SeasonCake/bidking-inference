from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import psutil
import pytest


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = ROOT / "scripts" / "live_process_discovery.ps1"
LOCK_OWNER = ROOT / "scripts" / "run_fatbeans_webhook_monitor.py"
HELPER = ROOT / "scripts" / "package_generation_identity.py"
LAUNCHER = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
CURRENT_GENERATION = "a" * 64
STALE_GENERATION = "b" * 64
PORTABLE_P0_PARENT = "22fc39f710dbe8a7d15f4aff74f490bba9aaa649"


def _powershell() -> str:
    command = shutil.which("powershell.exe") or shutil.which("powershell")
    if command is None:
        raise RuntimeError("Windows PowerShell is required")
    return command


def _ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(body: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            body,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


def _write_test_package(root: Path) -> tuple[Path, Path, Path, Path]:
    scripts = root / "scripts"
    runtime_dir = root / "runtime"
    processed = root / "data" / "processed"
    scripts.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    processed.mkdir(parents=True)
    helper = scripts / HELPER.name
    shutil.copy2(HELPER, helper)
    monitor = scripts / "local_compat_monitor.py"
    (scripts / "start_live_npcap_overlay.ps1").write_bytes(
        b"launcher-generation-1\n"
    )
    (scripts / "live_process_discovery.ps1").write_bytes(
        b"discovery-generation-1\n"
    )
    (scripts / "_client_launcher.cp313-win_amd64.pyd").write_bytes(
        b"compiled-launcher-generation-1\n"
    )
    (scripts / "_local_compat_monitor.cp313-win_amd64.pyd").write_bytes(
        b"compiled-monitor-generation-1\n"
    )
    (scripts / "local_compat_addon.py").write_bytes(
        b"wss-operational-bootstrap-generation-1\n"
    )
    (scripts / "_local_compat_addon_loader.pyd").write_bytes(
        b"wss-operational-loader-generation-1\n"
    )
    runtime = runtime_dir / "python.exe"
    upload = processed / "upload_config.enc"
    monitor.write_bytes(b"monitor-generation-1\n")
    runtime.write_bytes(b"runtime-generation-1\n")
    upload.write_bytes(b"encrypted-upload-generation-1\n")
    manifest = root / "BUILD_MANIFEST.txt"
    manifest.write_text(
        "PackageVersion: 0.3.3-hotfix2\n"
        + "SourceCommit: "
        + "1" * 40
        + "\nEncryptedUploadConfig: True\n"
        + "ProductProfile: standard\n"
        + "Encrypted: True\n"
        + "NativeBinaryDistribution: unsigned\n",
        encoding="utf-8",
        newline="\n",
    )
    return helper, monitor, runtime, upload


def _run_identity(
    root: Path,
    *,
    helper: Path | None = None,
    monitor: Path | None = None,
    runtime: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    helper_path = helper or root / "scripts" / HELPER.name
    monitor_path = monitor or root / "scripts" / "local_compat_monitor.py"
    runtime_path = runtime or root / "runtime" / "python.exe"
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(helper_path),
            "--package-root",
            str(root),
            "--monitor",
            str(monitor_path),
            "--runtime",
            str(runtime_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )


def _write_portable_test_package(root: Path) -> tuple[Path, Path, Path]:
    scripts = root / "scripts"
    runtime_dir = root / "runtime" / "python"
    scripts.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    helper = scripts / HELPER.name
    shutil.copy2(HELPER, helper)
    monitor = scripts / "run_wss_live_monitor.py"
    runtime = runtime_dir / "pythonw.exe"
    (root / "BidKingLive.exe").write_bytes(b"portable-launcher-generation-1\n")
    monitor.write_bytes(b"portable-monitor-generation-1\n")
    runtime.write_bytes(b"portable-pythonw-generation-1\n")
    (scripts / "start_live_npcap_overlay.ps1").write_bytes(
        b"portable-startup-generation-1\n"
    )
    (scripts / "mitm_bidking_wss_addon.py").write_bytes(
        b"portable-addon-generation-1\n"
    )
    (scripts / "_local_compat_core.py").write_bytes(
        b"portable-core-generation-1\n"
    )
    (root / "BUILD_MANIFEST.txt").write_text(
        "PackageVersion: 0.3.3-hotfix2\n"
        + "SourceCommit: "
        + "1" * 40
        + "\nEncryptedUploadConfig: False\n"
        + "ProductProfile: standard\n"
        + "Encrypted: False\n"
        + "NativeBinaryDistribution: NotRequired\n",
        encoding="utf-8",
        newline="\n",
    )
    return helper, monitor, runtime


def _identity_digest(completed: subprocess.CompletedProcess[str]) -> str:
    assert completed.returncode == 0, completed.stderr
    match = re.fullmatch(
        r"BIDKING_PACKAGE_GENERATION_IDENTITY=([0-9a-f]{64})\n?",
        completed.stdout,
    )
    assert match is not None
    assert completed.stderr == ""
    return match.group(1)


def _launcher_stale_cleanup_functions() -> str:
    launcher = LAUNCHER.read_text(encoding="utf-8-sig")
    functions: list[str] = []
    for name in ("Test-BidKingGameRunning", "Invoke-BidKingStalePackageCleanup"):
        match = re.search(
            rf"function {name}(?:\(\))? \{{.*?^\}}",
            launcher,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert match is not None
        functions.append(match.group(0))
    return "\n".join(functions)


def _lock_payload(
    *,
    process: psutil.Process,
    monitor: Path,
    log_dir: Path,
    generation: str,
    **extra: object,
) -> dict[str, object]:
    return {
        "pid": process.pid,
        "started_at": process.create_time(),
        "process_started_at": process.create_time(),
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
        "package_generation_identity": generation,
        **extra,
    }


def test_package_generation_identity_is_deterministic_and_path_bound(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    _helper, monitor, runtime, upload = _write_test_package(first_root)
    # The encrypted public package embeds discovery in _client_launcher and removes
    # the plaintext discovery script before its final exposure gate.
    (first_root / "scripts" / "live_process_discovery.ps1").unlink()
    initial = _identity_digest(_run_identity(first_root))
    assert _identity_digest(_run_identity(first_root)) == initial

    second_root = tmp_path / "second"
    shutil.copytree(first_root, second_root)
    assert _identity_digest(_run_identity(second_root)) == initial

    original_monitor = monitor.read_bytes()
    monitor.write_bytes(original_monitor + b"changed")
    changed_monitor = _identity_digest(_run_identity(first_root))
    assert changed_monitor != initial
    monitor.write_bytes(original_monitor)

    original_runtime = runtime.read_bytes()
    runtime.write_bytes(original_runtime + b"changed")
    changed_runtime = _identity_digest(_run_identity(first_root))
    assert changed_runtime not in {initial, changed_monitor}
    runtime.write_bytes(original_runtime)

    original_upload = upload.read_bytes()
    upload.write_bytes(original_upload + b"changed")
    changed_upload = _identity_digest(_run_identity(first_root))
    assert changed_upload not in {initial, changed_monitor, changed_runtime}
    upload.write_bytes(original_upload)

    native_launcher = first_root / "scripts" / "_client_launcher.cp313-win_amd64.pyd"
    original_native_launcher = native_launcher.read_bytes()
    native_launcher.write_bytes(original_native_launcher + b"changed")
    changed_native_launcher = _identity_digest(_run_identity(first_root))
    assert changed_native_launcher not in {
        initial,
        changed_monitor,
        changed_runtime,
        changed_upload,
    }
    native_launcher.write_bytes(original_native_launcher)

    addon_bootstrap = first_root / "scripts" / "local_compat_addon.py"
    original_addon_bootstrap = addon_bootstrap.read_bytes()
    addon_bootstrap.write_bytes(original_addon_bootstrap + b"changed")
    changed_addon_bootstrap = _identity_digest(_run_identity(first_root))
    assert changed_addon_bootstrap not in {
        initial,
        changed_monitor,
        changed_runtime,
        changed_upload,
        changed_native_launcher,
    }
    addon_bootstrap.write_bytes(original_addon_bootstrap)

    addon_loader = first_root / "scripts" / "_local_compat_addon_loader.pyd"
    original_addon_loader = addon_loader.read_bytes()
    addon_loader.write_bytes(original_addon_loader + b"changed")
    changed_addon_loader = _identity_digest(_run_identity(first_root))
    assert changed_addon_loader not in {
        initial,
        changed_monitor,
        changed_runtime,
        changed_upload,
        changed_native_launcher,
        changed_addon_bootstrap,
    }
    addon_loader.write_bytes(original_addon_loader)

    # The same bytes bound to different critical relative paths are a new generation.
    monitor.write_bytes(original_runtime)
    runtime.write_bytes(original_monitor)
    swapped = _identity_digest(_run_identity(first_root))
    assert swapped not in {initial, changed_monitor, changed_runtime, changed_upload}


def test_package_generation_identity_binds_manifest_bytes_and_fields(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    _write_test_package(root)
    manifest = root / "BUILD_MANIFEST.txt"
    initial = _identity_digest(_run_identity(root))

    original = manifest.read_text(encoding="utf-8")
    manifest.write_text(original + "Notice: fixed-public-text\n", encoding="utf-8")
    manifest_bytes_changed = _identity_digest(_run_identity(root))
    assert manifest_bytes_changed != initial

    manifest.write_text(
        original.replace("0.3.3-hotfix2", "0.3.3-hotfix1"),
        encoding="utf-8",
    )
    version_changed = _identity_digest(_run_identity(root))
    assert version_changed not in {initial, manifest_bytes_changed}

    manifest.write_text(original.replace("1" * 40, "2" * 40), encoding="utf-8")
    commit_changed = _identity_digest(_run_identity(root))
    assert commit_changed not in {initial, manifest_bytes_changed, version_changed}


@pytest.mark.parametrize(
    "manifest_mutation",
    [
        "PackageVersion: 0.3.1-hotfix2\nSourceCommit: "
        + "1" * 40
        + "\nEncryptedUploadConfig: True\nPackageVersion: 0.3.1-hotfix2\n",
        "PackageVersion: ０.３.１-hotfix2\nSourceCommit: "
        + "1" * 40
        + "\nEncryptedUploadConfig: True\n",
        "PackageVersion: 0.3.1-hotfix2 \nSourceCommit: "
        + "1" * 40
        + "\nEncryptedUploadConfig: True\n",
        "PackageVersion: 0.3.1-hotfix2\nSourceCommit: "
        + "1" * 40
        + "\nEncryptedUploadConfig: true\n",
    ],
)
def test_package_generation_identity_rejects_malformed_manifest_without_paths(
    tmp_path: Path,
    manifest_mutation: str,
) -> None:
    root = tmp_path / "package"
    _write_test_package(root)
    (root / "BUILD_MANIFEST.txt").write_text(
        manifest_mutation,
        encoding="utf-8",
    )
    completed = _run_identity(root)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert re.fullmatch(
        r"BIDKING_PACKAGE_GENERATION_IDENTITY_ERROR=[A-Za-z]+\n?",
        completed.stderr,
    )
    assert str(tmp_path) not in completed.stderr


def test_package_generation_identity_rejects_missing_config_and_outside_member(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    helper, _monitor, runtime, upload = _write_test_package(root)
    upload.unlink()
    missing = _run_identity(root)
    assert missing.returncode == 2
    assert str(tmp_path) not in missing.stderr
    upload.write_bytes(b"encrypted-upload-generation-1\n")

    outside = tmp_path / "outside-monitor.py"
    outside.write_bytes(b"outside")
    outside_result = _run_identity(
        root,
        helper=helper,
        monitor=outside,
        runtime=runtime,
    )
    assert outside_result.returncode == 2
    assert str(tmp_path) not in outside_result.stderr


def test_packaged_generation_requires_exact_native_launcher_and_monitor(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    _write_test_package(root)
    scripts = root / "scripts"
    duplicate = scripts / "_client_launcher.other.pyd"
    duplicate.write_bytes(b"decoy")
    duplicate_result = _run_identity(root)
    assert duplicate_result.returncode == 2
    assert str(tmp_path) not in duplicate_result.stderr

    duplicate.unlink()
    (scripts / "_local_compat_monitor.cp313-win_amd64.pyd").unlink()
    missing_result = _run_identity(root)
    assert missing_result.returncode == 2
    assert str(tmp_path) not in missing_result.stderr


def test_packaged_generation_requires_fixed_wss_operational_members(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    _write_test_package(root)
    scripts = root / "scripts"
    loader = scripts / "_local_compat_addon_loader.pyd"
    loader.rename(scripts / "_local_compat_addon_loader.cp313-win_amd64.pyd")
    renamed = _run_identity(root)
    assert renamed.returncode == 2
    assert str(tmp_path) not in renamed.stderr

    (scripts / "_local_compat_addon_loader.cp313-win_amd64.pyd").rename(loader)
    (scripts / "local_compat_addon.py").unlink()
    missing_bootstrap = _run_identity(root)
    assert missing_bootstrap.returncode == 2
    assert str(tmp_path) not in missing_bootstrap.stderr


def test_standard_portable_generation_binds_actual_public_startup_members(
    tmp_path: Path,
) -> None:
    root = tmp_path / "portable"
    helper, monitor, runtime = _write_portable_test_package(root)

    initial = _identity_digest(
        _run_identity(root, helper=helper, monitor=monitor, runtime=runtime)
    )
    assert (
        _identity_digest(
            _run_identity(root, helper=helper, monitor=monitor, runtime=runtime)
        )
        == initial
    )

    changed: set[str] = {initial}
    for relative in (
        "BidKingLive.exe",
        "runtime/python/pythonw.exe",
        "scripts/run_wss_live_monitor.py",
        "scripts/package_generation_identity.py",
        "scripts/start_live_npcap_overlay.ps1",
        "scripts/mitm_bidking_wss_addon.py",
        "scripts/_local_compat_core.py",
    ):
        member = root / relative
        original = member.read_bytes()
        member.write_bytes(original + b"changed")
        digest = _identity_digest(
            _run_identity(root, helper=helper, monitor=monitor, runtime=runtime)
        )
        assert digest not in changed
        changed.add(digest)
        member.write_bytes(original)


def test_portable_parent_known_red_is_green_in_successor_package_shape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "portable"
    helper, monitor, runtime = _write_portable_test_package(root)
    parent_helper = root / "scripts" / "parent_package_generation_identity.py"
    parent = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "show",
            f"{PORTABLE_P0_PARENT}:scripts/package_generation_identity.py",
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert parent.returncode == 0
    parent_helper.write_bytes(parent.stdout)

    known_red = _run_identity(
        root,
        helper=parent_helper,
        monitor=monitor,
        runtime=runtime,
    )
    assert known_red.returncode == 2
    assert re.fullmatch(
        r"BIDKING_PACKAGE_GENERATION_IDENTITY_ERROR=[A-Za-z]+\n?",
        known_red.stderr,
    )
    assert str(tmp_path) not in known_red.stderr
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        _identity_digest(
            _run_identity(root, helper=helper, monitor=monitor, runtime=runtime)
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda text: text.replace("ProductProfile: standard\n", ""),
        lambda text: text.replace(
            "ProductProfile: standard\n",
            "ProductProfile: standard\nProductProfile: standard\n",
        ),
        lambda text: text.replace(
            "ProductProfile: standard\n", "ProductProfile: Standard\n"
        ),
        lambda text: text.replace(
            "Encrypted: False\n", "Encrypted: false\n"
        ),
        lambda text: text.replace(
            "NativeBinaryDistribution: NotRequired\n",
            "NativeBinaryDistribution: notrequired\n",
        ),
        lambda text: text.replace(
            "Encrypted: False\n", "Encrypted: True\n"
        ),
        lambda text: text.replace(
            "EncryptedUploadConfig: False\n", "EncryptedUploadConfig: true\n"
        ),
        lambda text: text.replace(
            "ProductProfile: standard\n", "ProductProfile:  standard\n"
        ),
    ),
)
def test_standard_portable_generation_rejects_manifest_field_and_profile_mixes(
    tmp_path: Path,
    mutation: object,
) -> None:
    root = tmp_path / "portable"
    helper, monitor, runtime = _write_portable_test_package(root)
    manifest = root / "BUILD_MANIFEST.txt"
    original = manifest.read_text(encoding="utf-8")
    assert callable(mutation)
    manifest.write_text(mutation(original), encoding="utf-8", newline="\n")

    completed = _run_identity(root, helper=helper, monitor=monitor, runtime=runtime)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert re.fullmatch(
        r"BIDKING_PACKAGE_GENERATION_IDENTITY_ERROR=[A-Za-z]+\n?",
        completed.stderr,
    )
    assert str(tmp_path) not in completed.stderr


def test_standard_portable_generation_rejects_missing_outside_and_unexpected_members(
    tmp_path: Path,
) -> None:
    root = tmp_path / "portable"
    helper, monitor, runtime = _write_portable_test_package(root)
    core = root / "scripts" / "_local_compat_core.py"
    core.unlink()
    missing = _run_identity(root, helper=helper, monitor=monitor, runtime=runtime)
    assert missing.returncode == 2
    assert str(tmp_path) not in missing.stderr

    core.write_bytes(b"portable-core-generation-1\n")
    outside_runtime = tmp_path / "outside-pythonw.exe"
    outside_runtime.write_bytes(b"outside")
    outside = _run_identity(
        root,
        helper=helper,
        monitor=monitor,
        runtime=outside_runtime,
    )
    assert outside.returncode == 2
    assert str(tmp_path) not in outside.stderr

    (root / "data" / "processed").mkdir(parents=True)
    (root / "data" / "processed" / "upload_config.enc").write_bytes(b"forbidden")
    unexpected = _run_identity(root, helper=helper, monitor=monitor, runtime=runtime)
    assert unexpected.returncode == 2
    assert str(tmp_path) not in unexpected.stderr


def test_standard_portable_generation_rejects_reparse_member_without_traversing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "portable"
    helper, monitor, runtime = _write_portable_test_package(root)
    previous_module = sys.modules.pop("package_generation_identity", None)
    sys.path.insert(0, str(root / "scripts"))
    try:
        import package_generation_identity as identity_module
    finally:
        sys.path.pop(0)

    original_lstat = identity_module.os.lstat
    target = root / "BidKingLive.exe"

    def synthetic_reparse(path: str | os.PathLike[str]) -> os.stat_result:
        result = original_lstat(path)
        if Path(path) == target:
            return type(
                "SyntheticStat",
                (),
                {
                    "st_mode": result.st_mode,
                    "st_nlink": result.st_nlink,
                    "st_file_attributes": 0x400,
                },
            )()
        return result

    try:
        monkeypatch.setattr(identity_module.os, "lstat", synthetic_reparse)
        with pytest.raises(ValueError, match="regular file"):
            identity_module.compute_package_generation_identity(
                package_root=root,
                monitor_path=monitor,
                runtime_path=runtime,
                identity_helper_path=helper,
            )

        def synthetic_multiple_links(
            path: str | os.PathLike[str],
        ) -> os.stat_result:
            result = original_lstat(path)
            if Path(path) == target:
                return type(
                    "SyntheticStat",
                    (),
                    {
                        "st_mode": result.st_mode,
                        "st_nlink": 2,
                        "st_file_attributes": 0,
                    },
                )()
            return result

        monkeypatch.setattr(identity_module.os, "lstat", synthetic_multiple_links)
        with pytest.raises(ValueError, match="link topology"):
            identity_module.compute_package_generation_identity(
                package_root=root,
                monitor_path=monitor,
                runtime_path=runtime,
                identity_helper_path=helper,
            )
    finally:
        sys.modules.pop("package_generation_identity", None)
        if previous_module is not None:
            sys.modules["package_generation_identity"] = previous_module


def test_extracted_portable_generation_and_startup_preflight_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "extracted-portable"
    helper, monitor, runtime = _write_portable_test_package(root)
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        _identity_digest(
            _run_identity(root, helper=helper, monitor=monitor, runtime=runtime)
        ),
    )

    startup = LAUNCHER.read_text(encoding="utf-8-sig")
    helper_call = startup.index("& $PythonPath -E -B $PackageIdentityHelper")
    unavailable = startup.index("package_generation_identity_unavailable", helper_call)
    assert "--package-root $Repo" in startup[helper_call:unavailable]
    assert "--monitor $Monitor" in startup[helper_call:unavailable]
    assert "--runtime $MonitorPythonPath" in startup[helper_call:unavailable]
    assert "$PackageIdentityExit -ne 0" in startup[helper_call:unavailable]


def test_generation_digest_validation_rejects_str_subclass_without_magic() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from package_generation_identity import is_generation_digest
    finally:
        sys.path.pop(0)

    class ExplosiveString(str):
        def __str__(self) -> str:
            raise AssertionError("must not stringify")

        def strip(self, *args: object, **kwargs: object) -> str:
            raise AssertionError("must not strip")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("must not compare")

    assert is_generation_digest(CURRENT_GENERATION)
    assert not is_generation_digest(ExplosiveString(CURRENT_GENERATION))


def test_monitor_independently_recomputes_generation_before_locking(
    tmp_path: Path,
) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from package_generation_identity import compute_package_generation_identity
        from run_fatbeans_webhook_monitor import (
            _validated_package_generation_identity,
        )
    finally:
        sys.path.pop(0)

    monitor = ROOT / "scripts" / "run_wss_live_monitor.py"
    expected = compute_package_generation_identity(
        package_root=ROOT,
        monitor_path=monitor,
        runtime_path=Path(sys.executable),
    ).digest
    assert (
        _validated_package_generation_identity(expected, monitor_path=monitor)
        == expected
    )
    with pytest.raises(RuntimeError, match="mismatch"):
        _validated_package_generation_identity(STALE_GENERATION, monitor_path=monitor)
    with pytest.raises(RuntimeError, match="unavailable") as error:
        _validated_package_generation_identity(
            expected,
            monitor_path=tmp_path / "PRIVATE-MONITOR-CANARY.py",
        )
    assert str(tmp_path) not in str(error.value)


def test_cim_command_identity_requires_exact_generation_digest(tmp_path: Path) -> None:
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    current = "a" * 64
    stale = "b" * 64
    body = f"""
. {_ps_quote(DISCOVERY)}
$base = 'python.exe "{monitor}" --log-dir "{log_dir}"'
$current = Test-BidKingMonitorCommandIdentity `
  -CommandLine ($base + ' --package-generation-identity {current}') `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{current}'
$missing = Test-BidKingMonitorCommandIdentity `
  -CommandLine $base `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{current}'
$stale = Test-BidKingMonitorCommandIdentity `
  -CommandLine ($base + ' --package-generation-identity {stale}') `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{current}'
[pscustomobject]@{{current=[bool]$current;missing=[bool]$missing;stale=[bool]$stale}} |
  ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "current": True,
        "missing": False,
        "stale": False,
    }


def test_cim_generation_conflicts_block_only_same_path_unverified_processes(
    tmp_path: Path,
) -> None:
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    foreign = tmp_path / "foreign" / "scripts" / "run_wss_live_monitor.py"
    base = f'python.exe "{monitor}" --log-dir "{log_dir}"'
    body = f"""
. {_ps_quote(DISCOVERY)}
function Get-CimInstance {{
  [CmdletBinding()]param([string]$ClassName,[int]$OperationTimeoutSec)
  @(
    [pscustomobject]@{{Name='python.exe';ProcessId=1;CommandLine='{base} --package-generation-identity {CURRENT_GENERATION}'}},
    [pscustomobject]@{{Name='python.exe';ProcessId=2;CommandLine='{base}'}},
    [pscustomobject]@{{Name='python.exe';ProcessId=3;CommandLine='{base} --package-generation-identity {STALE_GENERATION}'}},
    [pscustomobject]@{{Name='python.exe';ProcessId=4;CommandLine='{base} --package-generation-identity invalid'}},
    [pscustomobject]@{{Name='python.exe';ProcessId=5;CommandLine='python.exe "{foreign}" --log-dir "{log_dir}" --package-generation-identity {STALE_GENERATION}'}},
    [pscustomobject]@{{Name='python.exe';ProcessId=6;CommandLine='python.exe "{monitor}" --log-dir "{tmp_path / 'foreign-logs'}" --package-generation-identity {CURRENT_GENERATION}'}},
    [pscustomobject]@{{Name='pythonw.exe';ProcessId=7;CommandLine='unrelated.py'}}
  )
}}
Reset-BidKingProcessDiscoveryState
$conflicts=@(Get-BidKingMonitorGenerationConflicts -StatusPath {_ps_quote(tmp_path / 'status.log')} -ExpectedMonitorPath {_ps_quote(monitor)} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}')
@($conflicts | ForEach-Object {{ [pscustomobject]@{{pid=$_.ProcessId;state=$_.GenerationState;marker=$_.CommandLine}} }}) | ConvertTo-Json -Compress
"""
    result = _run_powershell(body)
    assert result == [
        {"pid": 2, "state": "stale_package", "marker": "unverified_monitor_generation"},
        {"pid": 3, "state": "stale_package", "marker": "unverified_monitor_generation"},
        {"pid": 4, "state": "unknown", "marker": "unverified_monitor_generation"},
        {"pid": 5, "state": "foreign_context", "marker": "unverified_monitor_generation"},
        {"pid": 6, "state": "foreign_context", "marker": "unverified_monitor_generation"},
    ]


def test_lock_fallback_distinguishes_current_stale_and_malformed_generation(
    tmp_path: Path,
) -> None:
    process = psutil.Process()
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    lock = log_dir / "monitor.lock"
    log_dir.mkdir(parents=True)
    base = {
        "pid": process.pid,
        "process_started_at": process.create_time(),
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
    }
    current = "a" * 64

    def classify(
        extra: dict[str, object],
        *,
        raw_text: str | None = None,
    ) -> str:
        lock.write_text(
            raw_text
            if raw_text is not None
            else json.dumps({**base, **extra}, separators=(",", ":")),
            encoding="utf-8",
        )
        body = f"""
. {_ps_quote(DISCOVERY)}
$identity = Get-BidKingMonitorLockIdentity `
  -StatePath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{current}'
[pscustomobject]@{{state=[string]$identity.State}} | ConvertTo-Json -Compress
"""
        return str(_run_powershell(body)["state"])

    assert classify({"package_generation_identity": current}) == "current"
    assert classify({"package_generation_identity": "b" * 64}) == "stale_package"
    assert classify({}) == "stale_package"
    assert classify({"package_generation_identity": True}) == "unknown"
    duplicate = json.dumps(
        {**base, "package_generation_identity": current},
        separators=(",", ":"),
    )[:-1] + f',"package_generation_identity":"{"b" * 64}"}}'
    assert classify({}, raw_text=duplicate) == "unknown"
    assert (
        classify(
            {
                "monitor_path": str(tmp_path / "foreign" / monitor.name),
                "package_generation_identity": current,
            }
        )
        == "foreign"
    )


@pytest.mark.parametrize(
    ("literal_key", "escaped_key", "stale_value"),
    [
        (
            "package_generation_identity",
            r"package_generation_\u0069dentity",
            STALE_GENERATION,
        ),
        ("pid", r"p\u0069d", 999_999_999),
        ("monitor_path", r"monitor_p\u0061th", "C:/foreign/monitor.py"),
        ("log_dir", r"log_d\u0069r", "C:/foreign/logs"),
    ],
)
@pytest.mark.parametrize("escaped_first", [False, True])
def test_lock_rejects_literal_and_unicode_escaped_duplicate_keys_in_direct_and_fallback(
    tmp_path: Path,
    literal_key: str,
    escaped_key: str,
    stale_value: object,
    escaped_first: bool,
) -> None:
    process = psutil.Process()
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    lock = log_dir / "monitor.lock"
    status = tmp_path / "status.log"
    log_dir.mkdir(parents=True)
    current_values: dict[str, object] = {
        "pid": process.pid,
        "process_started_at": process.create_time(),
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
        "package_generation_identity": CURRENT_GENERATION,
    }
    if escaped_first:
        payload = {
            key: value for key, value in current_values.items() if key != literal_key
        }
        raw = json.dumps(payload, separators=(",", ":"))[:-1]
        raw += f',"{escaped_key}":{json.dumps(stale_value)}'
        raw += f',"{literal_key}":{json.dumps(current_values[literal_key])}}}'
    else:
        payload = {**current_values, literal_key: stale_value}
        raw = json.dumps(payload, separators=(",", ":"))[:-1]
        raw += f',"{escaped_key}":{json.dumps(current_values[literal_key])}}}'
    lock.write_text(raw, encoding="utf-8")

    body = f"""
. {_ps_quote(DISCOVERY)}
$identity = Get-BidKingMonitorLockIdentity `
  -StatePath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
function Get-CimInstance {{
  [CmdletBinding()]param([string]$ClassName,[int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('synthetic-cim-unavailable')
}}
Reset-BidKingProcessDiscoveryState
$fallback = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -StatusPath {_ps_quote(status)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}')
$statusText = if (Test-Path -LiteralPath {_ps_quote(status)}) {{
  [System.IO.File]::ReadAllText({_ps_quote(status)})
}} else {{ '' }}
[pscustomobject]@{{
  direct_state = [string]$identity.State
  fallback_count = [int]$fallback.Count
  lock_exists = [bool](Test-Path -LiteralPath {_ps_quote(lock)})
  status = $statusText
}} | ConvertTo-Json -Compress
"""
    result = _run_powershell(body)
    assert result["direct_state"] == "unknown"
    assert result["fallback_count"] == 0
    assert result["lock_exists"] is True
    assert "monitor_lock_identity_unverified" in str(result["status"])


def _python_lock_state(
    lock: Path,
    *,
    monitor: Path,
    log_dir: Path,
    generation: str,
) -> str:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from run_fatbeans_webhook_monitor import _inspect_monitor_lock
    finally:
        sys.path.pop(0)
    return _inspect_monitor_lock(
        lock,
        monitor_path=monitor,
        log_dir=log_dir,
        package_generation_identity=generation,
    ).state


def _invalid_utf8_lock_bytes(
    *,
    process: psutil.Process,
    monitor: Path,
    log_dir: Path,
    location: str,
) -> bytes:
    values: dict[str, object] = {
        "pid": process.pid,
        "process_started_at": process.create_time(),
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
        "package_generation_identity": CURRENT_GENERATION,
    }

    def encoded_value(name: str, value: object) -> bytes:
        clean = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if location != name:
            return clean
        text = str(value)
        quoted = json.dumps(text, ensure_ascii=False).encode()
        return quoted[:-1] + b"\xff\""

    pairs = [
        json.dumps(name).encode() + b":" + encoded_value(name, value)
        for name, value in values.items()
    ]
    if location == "extra_value":
        pairs.append(b'"ignored_meta":"safe\xff"')
    elif location == "extra_key":
        pairs.append(b'"ignored_\xffmeta":0')
    return b"{" + b",".join(pairs) + b"}"


@pytest.mark.parametrize(
    "location",
    [
        "extra_value",
        "extra_key",
        "package_generation_identity",
        "pid",
        "monitor_path",
        "log_dir",
    ],
)
def test_lock_invalid_utf8_is_unknown_in_python_direct_and_cim_fallback(
    tmp_path: Path,
    location: str,
) -> None:
    process = psutil.Process()
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    lock = log_dir / "monitor.lock"
    status = tmp_path / "status.log"
    log_dir.mkdir(parents=True)
    lock.write_bytes(
        _invalid_utf8_lock_bytes(
            process=process,
            monitor=monitor,
            log_dir=log_dir,
            location=location,
        )
    )
    python_state = _python_lock_state(
        lock,
        monitor=monitor,
        log_dir=log_dir,
        generation=CURRENT_GENERATION,
    )

    body = f"""
. {_ps_quote(DISCOVERY)}
$identity = Get-BidKingMonitorLockIdentity `
  -StatePath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
function Get-CimInstance {{
  [CmdletBinding()]param([string]$ClassName,[int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('synthetic-cim-unavailable')
}}
Reset-BidKingProcessDiscoveryState
$fallback = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -StatusPath {_ps_quote(status)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}')
[pscustomobject]@{{
  direct_state = [string]$identity.State
  fallback_count = [int]$fallback.Count
  lock_exists = [bool](Test-Path -LiteralPath {_ps_quote(lock)})
}} | ConvertTo-Json -Compress
"""
    result = _run_powershell(body)
    assert python_state == "unknown"
    assert result == {
        "direct_state": "unknown",
        "fallback_count": 0,
        "lock_exists": True,
    }


def test_lock_nonduplicate_current_identity_remains_reusable_direct_and_fallback(
    tmp_path: Path,
) -> None:
    process = psutil.Process()
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    lock = log_dir / "monitor.lock"
    status = tmp_path / "status.log"
    log_dir.mkdir(parents=True)
    lock.write_text(
        json.dumps(
            _lock_payload(
                process=process,
                monitor=monitor,
                log_dir=log_dir,
                generation=CURRENT_GENERATION,
            ),
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    body = f"""
. {_ps_quote(DISCOVERY)}
$identity = Get-BidKingMonitorLockIdentity `
  -StatePath {_ps_quote(lock)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
function Get-CimInstance {{
  [CmdletBinding()]param([string]$ClassName,[int]$OperationTimeoutSec)
  throw [System.InvalidOperationException]::new('synthetic-cim-unavailable')
}}
Reset-BidKingProcessDiscoveryState
$fallback = @(Get-BidKingLiveProcesses `
  -MonitorLockPath {_ps_quote(lock)} `
  -StatusPath {_ps_quote(status)} `
  -ExpectedMonitorPath {_ps_quote(monitor)} `
  -ExpectedLogDir {_ps_quote(log_dir)} `
  -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}')
[pscustomobject]@{{
  direct_state = [string]$identity.State
  fallback_count = [int]$fallback.Count
  lock_exists = [bool](Test-Path -LiteralPath {_ps_quote(lock)})
}} | ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "direct_state": "current",
        "fallback_count": 1,
        "lock_exists": True,
    }
    assert (
        _python_lock_state(
            lock,
            monitor=monitor,
            log_dir=log_dir,
            generation=CURRENT_GENERATION,
        )
        == "current"
    )


def test_monitor_lock_owner_persists_validated_generation_digest(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from run_fatbeans_webhook_monitor import _acquire_lock, _inspect_monitor_lock
    finally:
        sys.path.pop(0)

    monitor = tmp_path / "scripts" / "run_wss_live_monitor.py"
    monitor.parent.mkdir(parents=True)
    monitor.write_text("# monitor\n", encoding="utf-8")
    log_dir = tmp_path / "data" / "logs" / "live"
    digest = "c" * 64
    lock = _acquire_lock(
        log_dir,
        monitor_path=monitor,
        package_generation_identity=digest,
    )
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["package_generation_identity"] == digest
    duplicate_raw = lock.read_text(encoding="utf-8")[:-1] + (
        f',"package_generation_identity":"{"d" * 64}"}}'
    )
    lock.write_text(duplicate_raw, encoding="utf-8")
    assert (
        _inspect_monitor_lock(
            lock,
            monitor_path=monitor,
            log_dir=log_dir,
            package_generation_identity=digest,
        ).state
        == "unknown"
    )


@pytest.mark.parametrize(
    "generation",
    [STALE_GENERATION, None],
    ids=["mismatch", "old-lock"],
)
def test_stale_same_path_monitor_is_stopped_and_lock_is_atomically_removed(
    tmp_path: Path,
    generation: str | None,
) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    process = psutil.Process(child.pid)
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    log_dir.mkdir(parents=True)
    lock = log_dir / "monitor.lock"
    status = log_dir / "startup_status.log"
    payload = _lock_payload(
        process=process,
        monitor=monitor,
        log_dir=log_dir,
        generation=generation or STALE_GENERATION,
    )
    if generation is None:
        payload.pop("package_generation_identity")
    lock.write_text(json.dumps(payload), encoding="utf-8")
    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_stale_cleanup_functions()}
$LockPath={_ps_quote(lock)}
$Monitor={_ps_quote(monitor)}
$LogPath={_ps_quote(log_dir)}
$StartupStatus={_ps_quote(status)}
$ExpectedPackageGenerationIdentity='{CURRENT_GENERATION}'
$ProcessName='BidKing.exe'
function Test-BidKingGameRunning {{ return $false }}
$result=Invoke-BidKingStalePackageCleanup
$statusText=if(Test-Path -LiteralPath $StartupStatus){{[string](Get-Content -LiteralPath $StartupStatus -Raw)}}else{{''}}
[pscustomobject]@{{succeeded=$result.Succeeded;state=$result.State;lock_exists=(Test-Path -LiteralPath $LockPath);status=$statusText}} | ConvertTo-Json -Compress
"""
    try:
        result = _run_powershell(body)
        assert result["succeeded"] is True
        assert result["state"] == "replaced"
        assert result["lock_exists"] is False
        assert "monitor_package_generation_stale action=replaced_safely" in str(
            result["status"]
        )
        assert str(tmp_path) not in str(result["status"])
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_stale_monitor_is_preserved_while_game_is_running(tmp_path: Path) -> None:
    process = psutil.Process()
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    log_dir.mkdir(parents=True)
    lock = log_dir / "monitor.lock"
    status = log_dir / "startup_status.log"
    lock.write_text(
        json.dumps(
            _lock_payload(
                process=process,
                monitor=monitor,
                log_dir=log_dir,
                generation=STALE_GENERATION,
            )
        ),
        encoding="utf-8",
    )
    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_stale_cleanup_functions()}
$LockPath={_ps_quote(lock)}
$Monitor={_ps_quote(monitor)}
$LogPath={_ps_quote(log_dir)}
$StartupStatus={_ps_quote(status)}
$ExpectedPackageGenerationIdentity='{CURRENT_GENERATION}'
$ProcessName='BidKing.exe'
function Test-BidKingGameRunning {{ return $true }}
$script:stopCalls=0
function Stop-Process {{ [CmdletBinding()]param([int]$Id,[switch]$Force);$script:stopCalls+=1 }}
$result=Invoke-BidKingStalePackageCleanup
$statusText=[string](Get-Content -LiteralPath $StartupStatus -Raw)
[pscustomobject]@{{succeeded=$result.Succeeded;state=$result.State;lock_exists=(Test-Path -LiteralPath $LockPath);stop_calls=$script:stopCalls;status=$statusText}} | ConvertTo-Json -Compress
"""
    result = _run_powershell(body)
    assert result["succeeded"] is False
    assert result["state"] == "game_active"
    assert result["lock_exists"] is True
    assert result["stop_calls"] == 0
    assert "monitor_package_generation_stale action=blocked_game_active" in str(
        result["status"]
    )
    assert str(tmp_path) not in str(result["status"])


def test_stale_cleanup_fails_closed_on_pid_epoch_or_generation_aba(
    tmp_path: Path,
) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    process = psutil.Process(child.pid)
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    log_dir.mkdir(parents=True)
    lock = log_dir / "monitor.lock"
    status = log_dir / "startup_status.log"
    stale_payload = _lock_payload(
        process=process,
        monitor=monitor,
        log_dir=log_dir,
        generation=STALE_GENERATION,
    )
    current_payload = dict(stale_payload)
    current_payload["package_generation_identity"] = CURRENT_GENERATION
    current_payload["nonce"] = "replacement-generation"
    lock.write_text(json.dumps(stale_payload), encoding="utf-8")
    replacement_json = json.dumps(current_payload).replace("'", "''")
    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_stale_cleanup_functions()}
$LockPath={_ps_quote(lock)}
$Monitor={_ps_quote(monitor)}
$LogPath={_ps_quote(log_dir)}
$StartupStatus={_ps_quote(status)}
$ExpectedPackageGenerationIdentity='{CURRENT_GENERATION}'
$ProcessName='BidKing.exe'
function Test-BidKingGameRunning {{ return $false }}
$script:identityCalls=0
$script:identityImpl=${{function:Get-BidKingMonitorLockIdentity}}
function Get-BidKingMonitorLockIdentity {{
  param([string]$StatePath,[string]$ExpectedMonitorPath,[string]$ExpectedLogDir,[string]$ExpectedPackageGenerationIdentity)
  $script:identityCalls+=1
  $identity=& $script:identityImpl -StatePath $StatePath -ExpectedMonitorPath $ExpectedMonitorPath -ExpectedLogDir $ExpectedLogDir -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  if($script:identityCalls -eq 1){{[System.IO.File]::WriteAllText($StatePath,'{replacement_json}',(New-Object System.Text.UTF8Encoding($false)))}}
  return $identity
}}
$script:stopCalls=0
function Stop-Process {{ [CmdletBinding()]param([int]$Id,[switch]$Force);$script:stopCalls+=1 }}
$result=Invoke-BidKingStalePackageCleanup
[pscustomobject]@{{succeeded=$result.Succeeded;state=$result.State;lock_exists=(Test-Path -LiteralPath $LockPath);stop_calls=$script:stopCalls}} | ConvertTo-Json -Compress
"""
    try:
        result = _run_powershell(body)
        assert result == {
            "succeeded": False,
            "state": "changed",
            "lock_exists": True,
            "stop_calls": 0,
        }
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_stale_cleanup_preserves_replacement_lock_after_stop(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    process = psutil.Process(child.pid)
    monitor = tmp_path / "current" / "scripts" / "run_wss_live_monitor.py"
    log_dir = tmp_path / "current" / "data" / "logs" / "live"
    log_dir.mkdir(parents=True)
    lock = log_dir / "monitor.lock"
    status = log_dir / "startup_status.log"
    payload = _lock_payload(
        process=process,
        monitor=monitor,
        log_dir=log_dir,
        generation=STALE_GENERATION,
    )
    replacement = dict(payload)
    replacement["nonce"] = "replacement-after-stop"
    lock.write_text(json.dumps(payload), encoding="utf-8")
    replacement_json = json.dumps(replacement).replace("'", "''")
    body = f"""
. {_ps_quote(DISCOVERY)}
{_launcher_stale_cleanup_functions()}
$LockPath={_ps_quote(lock)}
$Monitor={_ps_quote(monitor)}
$LogPath={_ps_quote(log_dir)}
$StartupStatus={_ps_quote(status)}
$ExpectedPackageGenerationIdentity='{CURRENT_GENERATION}'
$ProcessName='BidKing.exe'
function Test-BidKingGameRunning {{ return $false }}
function Stop-Process {{
  [CmdletBinding()]param([int]$Id,[switch]$Force)
  Microsoft.PowerShell.Management\\Stop-Process -Id $Id -Force -ErrorAction Stop
  [System.IO.File]::WriteAllText($LockPath,'{replacement_json}',(New-Object System.Text.UTF8Encoding($false)))
}}
$result=Invoke-BidKingStalePackageCleanup
$raw=[System.IO.File]::ReadAllText($LockPath)
[pscustomobject]@{{succeeded=$result.Succeeded;state=$result.State;lock_exists=(Test-Path -LiteralPath $LockPath);replacement_preserved=$raw.Contains('replacement-after-stop')}} | ConvertTo-Json -Compress
"""
    try:
        result = _run_powershell(body)
        assert result == {
            "succeeded": False,
            "state": "lock_changed",
            "lock_exists": True,
            "replacement_preserved": True,
        }
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_public_launcher_and_monitor_parser_carry_generation_identity() -> None:
    launcher = (ROOT / "scripts" / "start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8"
    )
    discovery = DISCOVERY.read_text(encoding="utf-8")
    wss = (ROOT / "scripts" / "run_wss_live_monitor.py").read_text(encoding="utf-8")
    npcap = (ROOT / "scripts" / "run_npcap_live_monitor.py").read_text(
        encoding="utf-8"
    )
    build = BUILD.read_text(encoding="utf-8-sig")
    assert "ExpectedPackageGenerationIdentity" in discovery
    assert "--package-generation-identity" in launcher
    assert "--package-generation-identity" in wss
    assert "--package-generation-identity" in npcap
    assert "Invoke-BidKingStalePackageCleanup" in launcher
    assert "Get-BidKingMonitorGenerationConflicts" in launcher
    assert "monitor_package_generation_unverified action=preserved" in launcher
    assert "monitor_package_generation_unverified action=blocked_cim_conflict" in launcher
    assert build.count('"scripts/package_generation_identity.py"') == 1
    assert build.count('"package_generation_identity.py"') == 2
    assert "--startup $publicLauncherScript" in build
    assert "--process-discovery $processDiscoveryDst" in build
    assert "$env:BIDKING_LIFECYCLE_GENERATION = $lifecycleGeneration" in build
