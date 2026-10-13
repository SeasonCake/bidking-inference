from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_wss_live_monitor as wss_monitor  # noqa: E402


SERVICE_NAME = "BidKingWinDivertR1"


def _sc_result(
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _frozen_sc_result(result: object) -> wss_monitor._ScCommandResult:
    projected = wss_monitor._project_sc_command_result(result)
    assert projected is not None
    return projected


def _service_status(state: int, *, service_name: str = SERVICE_NAME) -> SimpleNamespace:
    return _sc_result(
        0,
        "\n".join(
            (
                f"SERVICE_NAME: {service_name}",
                "        TYPE               : 1  KERNEL_DRIVER",
                f"        STATE              : {state}  STOPPED",
            )
        ),
    )


def _service_config(
    driver_path: str,
    *,
    service_type: int = 1,
    start_type: int = 3,
    error_control: int = 1,
    dependencies: tuple[str, ...] = (),
    include_error_control: bool = True,
    include_dependencies: bool = True,
) -> SimpleNamespace:
    lines = [
        "[SC] QueryServiceConfig SUCCESS",
        f"        TYPE               : {service_type}  KERNEL_DRIVER",
        f"        START_TYPE         : {start_type}  DEMAND_START",
    ]
    if include_error_control:
        lines.append(f"        ERROR_CONTROL      : {error_control}  NORMAL")
    lines.extend(
        (
            f"        BINARY_PATH_NAME   : {driver_path}",
            "        LOAD_ORDER_GROUP   :",
            "        TAG                : 0",
            "        DISPLAY_NAME       : BidKing WinDivert Compatibility",
        )
    )
    if include_dependencies:
        if dependencies:
            lines.append(f"        DEPENDENCIES       : {dependencies[0]}")
            lines.extend(f"                             {item}" for item in dependencies[1:])
        else:
            lines.append("        DEPENDENCIES       :")
    lines.append("        SERVICE_START_NAME :")
    return _sc_result(0, "\n".join(lines))


def _trusted_identity(path: Path, *, epoch: int = 1, held_driver=None, held_manifest=None):
    return wss_monitor._StaleBidKingDriverIdentity(
        normalized_path=wss_monitor._normalize_windows_driver_path(str(path)),
        driver_file_identity=(11, epoch, 94144, 123456789),
        driver_sha256=wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
        signer_thumbprint=wss_monitor.KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT,
        package_manifest_sha256=wss_monitor.KNOWN_OLD_BIDKING_PACKAGE_MANIFEST_SHA256,
        manifest_file_identity=(22, 1, 2048, 987654321),
        held_driver=held_driver,
        held_manifest=held_manifest,
    )


@contextmanager
def _lock(acquired: bool = True):
    yield acquired


def _assert_no_mutation(calls: list[list[str]]) -> None:
    assert not any(
        len(command) > 1
        and command[1] in {"create", "start", "stop", "delete", "config"}
        for command in calls
    )


def _repairer(
    *,
    runner,
    inventory_reader,
    elevated: bool = True,
    lock_acquired: bool = True,
    service_name: str = SERVICE_NAME,
):
    return wss_monitor.WinDivertStaleProductServiceRepair(
        runner=runner,
        service_name=service_name,
        elevation_checker=lambda: 1 if elevated else 0,
        lock_factory=lambda: _lock(lock_acquired),
        inventory_reader=inventory_reader,
    )


def test_full_sc_driver_config_parser_requires_normal_error_and_no_dependencies(
    tmp_path: Path,
) -> None:
    driver = tmp_path / "WinDivert64.sys"

    parsed = wss_monitor._parse_sc_driver_service_config(
        _frozen_sc_result(_service_config(str(driver)))
    )

    assert parsed is not None
    assert parsed.error_control == 1
    assert parsed.dependencies == ()


@pytest.mark.parametrize(
    "config",
    (
        _service_config("C:\\old\\WinDivert64.sys", include_error_control=False),
        _service_config("C:\\old\\WinDivert64.sys", include_dependencies=False),
        _service_config("C:\\old\\WinDivert64.sys", error_control=0),
        _service_config("C:\\old\\WinDivert64.sys", dependencies=("Tcpip",)),
    ),
)
def test_full_sc_driver_config_match_rejects_missing_or_unsafe_fields(config) -> None:
    assert not wss_monitor._driver_service_config_matches(
        _frozen_sc_result(config),
        expected_driver_path="C:\\old\\WinDivert64.sys",
    )


def test_full_sc_driver_config_parser_rejects_duplicate_identity_fields() -> None:
    config = _service_config("C:\\old\\WinDivert64.sys")
    config.stdout += "\n        ERROR_CONTROL      : 1  NORMAL"

    assert wss_monitor._parse_sc_driver_service_config(
        _frozen_sc_result(config)
    ) is None


def test_stable_file_digest_detects_in_read_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "WinDivert64.sys"
    target.write_bytes(b"before")
    original = wss_monitor._sha256_file

    def mutate_then_hash(path: Path) -> str:
        digest = original(path)
        path.write_bytes(b"after-with-a-different-size")
        return digest

    monkeypatch.setattr(wss_monitor, "_sha256_file", mutate_then_hash)

    assert wss_monitor._stable_file_digest(target) is None


class _IntSubclass(int):
    pass


class _ExplodingLinkCount:
    @property
    def st_nlink(self):
        raise RuntimeError("link count unavailable")


@pytest.mark.parametrize(
    "link_count",
    (
        None,
        True,
        _IntSubclass(1),
        _ExplodingLinkCount(),
    ),
)
def test_file_identity_requires_exact_builtin_single_link(link_count) -> None:
    file_stat = SimpleNamespace(
        st_dev=11,
        st_ino=22,
        st_size=33,
        st_mtime_ns=44,
        st_ctime_ns=55,
    )
    if link_count is not None:
        if isinstance(link_count, _ExplodingLinkCount):
            file_stat = link_count
            file_stat.st_dev = 11
            file_stat.st_ino = 22
            file_stat.st_size = 33
            file_stat.st_mtime_ns = 44
            file_stat.st_ctime_ns = 55
        else:
            file_stat.st_nlink = link_count

    assert wss_monitor._file_identity_tuple(file_stat) is None


def test_stable_file_digest_rejects_link_count_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DriftPath:
        def __init__(self) -> None:
            self.stat_calls = 0

        def lstat(self):
            return SimpleNamespace(
                st_mode=wss_monitor.stat_module.S_IFREG,
                st_file_attributes=0,
            )

        def stat(self):
            self.stat_calls += 1
            return SimpleNamespace(
                st_mode=wss_monitor.stat_module.S_IFREG,
                st_dev=11,
                st_ino=22,
                st_nlink=1 if self.stat_calls == 1 else 2,
                st_size=33,
                st_mtime_ns=44,
                st_ctime_ns=55,
            )

    target = DriftPath()
    monkeypatch.setattr(wss_monitor, "_sha256_file", lambda _path: "a" * 64)

    assert wss_monitor._stable_file_digest(target) is None


def _published_inventory_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    package_root = tmp_path / "bidking-live-v0.3.2-hotfix2-encrypted"
    driver = (
        package_root
        / "vendor"
        / "mitmproxy"
        / "_internal"
        / "mitmproxy_windows"
        / "WinDivert64.sys"
    )
    driver.parent.mkdir(parents=True)
    driver.write_bytes(b"published-driver")
    manifest = package_root / "BUILD_MANIFEST.txt"
    manifest.write_bytes(b"published-manifest")
    return package_root, driver, manifest


def _install_external_hardlink(target: Path, backing: Path) -> None:
    backing.write_bytes(target.read_bytes())
    target.unlink()
    os.link(backing, target)
    assert target.stat().st_nlink == 2
    assert backing.stat().st_nlink == 2


def _patch_held_snapshot_hashes(
    monkeypatch: pytest.MonkeyPatch,
    digest_for_path,
) -> None:
    """Keep fixture bytes synthetic while exercising the real held-file protocol."""

    monkeypatch.setattr(
        wss_monitor._HeldStableFile,
        "_digest_from_held_stream",
        lambda snapshot: digest_for_path(snapshot.path),
    )


def test_known_inventory_accepts_regular_single_link_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _package_root, driver, manifest = _published_inventory_paths(tmp_path)
    assert driver.stat().st_nlink == 1
    assert manifest.stat().st_nlink == 1

    def expected_digest(path: Path) -> str:
        if path.name.casefold() == "build_manifest.txt":
            return "f7b9f8459d17ea886754a2126c9b94ab95b47ff28343cfca0caedd536f9d0146"
        return wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256

    _patch_held_snapshot_hashes(monkeypatch, expected_digest)
    monkeypatch.setattr(
        wss_monitor,
        "_read_authenticode_identity",
        lambda _path: wss_monitor._AuthenticodeIdentity(
            status="Valid",
            signer_thumbprint=(
                wss_monitor.KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT
            ),
        ),
    )

    identity = wss_monitor._inspect_known_old_bidking_driver(str(driver))

    assert identity is not None
    assert identity.package_manifest_sha256 == (
        "f7b9f8459d17ea886754a2126c9b94ab95b47ff28343cfca0caedd536f9d0146"
    )


@pytest.mark.parametrize("linked_member", ("manifest", "driver", "both"))
def test_known_inventory_rejects_external_hardlinked_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    linked_member: str,
) -> None:
    _package_root, driver, manifest = _published_inventory_paths(tmp_path)
    if linked_member in {"driver", "both"}:
        _install_external_hardlink(driver, tmp_path / "outside-driver.bin")
    if linked_member in {"manifest", "both"}:
        _install_external_hardlink(manifest, tmp_path / "outside-manifest.txt")

    def expected_digest(path: Path) -> str:
        if path.name.casefold() == "build_manifest.txt":
            return "f7b9f8459d17ea886754a2126c9b94ab95b47ff28343cfca0caedd536f9d0146"
        return wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256

    _patch_held_snapshot_hashes(monkeypatch, expected_digest)
    monkeypatch.setattr(
        wss_monitor,
        "_read_authenticode_identity",
        lambda _path: wss_monitor._AuthenticodeIdentity(
            status="Valid",
            signer_thumbprint=(
                wss_monitor.KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT
            ),
        ),
    )

    assert wss_monitor._inspect_known_old_bidking_driver(str(driver)) is None


def test_external_hardlink_alias_rewrite_remains_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _package_root, driver, manifest = _published_inventory_paths(tmp_path)
    alias = tmp_path / "outside-driver.bin"
    _install_external_hardlink(driver, alias)

    def rewrite_alias_during_digest(path: Path) -> str:
        if path.name.casefold() == "build_manifest.txt":
            return "f7b9f8459d17ea886754a2126c9b94ab95b47ff28343cfca0caedd536f9d0146"
        alias.write_bytes(b"rewritten-outside")
        return wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256

    _patch_held_snapshot_hashes(monkeypatch, rewrite_alias_during_digest)
    monkeypatch.setattr(
        wss_monitor,
        "_read_authenticode_identity",
        lambda _path: pytest.fail("hardlink must fail before signature authority"),
    )

    assert wss_monitor._inspect_known_old_bidking_driver(str(driver)) is None


@pytest.mark.parametrize(
    "failure_kind",
    ("none", "driver_hash", "manifest_hash", "signature", "signer"),
)
def test_known_old_inventory_requires_exact_layout_hashes_and_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    package_root = tmp_path / "bidking-live-v0.3.0-hotfix2-r1-encrypted"
    driver = (
        package_root
        / "vendor"
        / "mitmproxy"
        / "_internal"
        / "mitmproxy_windows"
        / "WinDivert64.sys"
    )
    driver.parent.mkdir(parents=True)
    driver.write_bytes(b"driver")
    manifest = package_root / "BUILD_MANIFEST.txt"
    manifest.write_text("manifest", encoding="utf-8")

    def expected_digest(path: Path) -> str:
        if path.name.casefold() == "build_manifest.txt":
            return (
                "0" * 64
                if failure_kind == "manifest_hash"
                else wss_monitor.KNOWN_OLD_BIDKING_PACKAGE_MANIFEST_SHA256
            )
        return (
            "0" * 64
            if failure_kind == "driver_hash"
            else wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256
        )

    signature = wss_monitor._AuthenticodeIdentity(
        status="NotSigned" if failure_kind == "signature" else "Valid",
        signer_thumbprint=(
            "f" * 40
            if failure_kind == "signer"
            else wss_monitor.KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT
        ),
    )
    _patch_held_snapshot_hashes(monkeypatch, expected_digest)
    monkeypatch.setattr(
        wss_monitor,
        "_read_authenticode_identity",
        lambda _path: signature,
    )

    identity = wss_monitor._inspect_known_old_bidking_driver(str(driver))

    if failure_kind == "none":
        assert identity is not None
        assert identity.normalized_path == wss_monitor._normalize_windows_driver_path(
            str(driver)
        )
    else:
        assert identity is None


def test_known_published_hotfix1_inventory_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "bidking-live-v0.3.2-hotfix1-encrypted"
    driver = (
        package_root
        / "vendor"
        / "mitmproxy"
        / "_internal"
        / "mitmproxy_windows"
        / "WinDivert64.sys"
    )
    driver.parent.mkdir(parents=True)
    driver.write_bytes(b"driver")
    manifest = package_root / "BUILD_MANIFEST.txt"
    manifest.write_text("manifest", encoding="utf-8")
    hotfix1_manifest_sha256 = (
        "8bac0b68a8a9508014116ac1956e5da1cdd766618f30f252921d6f57d9fc3409"
    )

    def expected_digest(path: Path) -> str:
        if path.name.casefold() == "build_manifest.txt":
            return hotfix1_manifest_sha256
        return wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256

    _patch_held_snapshot_hashes(monkeypatch, expected_digest)
    monkeypatch.setattr(
        wss_monitor,
        "_read_authenticode_identity",
        lambda _path: wss_monitor._AuthenticodeIdentity(
            status="Valid",
            signer_thumbprint=(
                wss_monitor.KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT
            ),
        ),
    )

    identity = wss_monitor._inspect_known_old_bidking_driver(str(driver))

    assert identity is not None
    assert identity.package_manifest_sha256 == hotfix1_manifest_sha256


@pytest.mark.parametrize("replaced", ("manifest", "driver", "both"))
def test_held_inventory_rejects_signature_window_aba_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replaced: str,
) -> None:
    _package_root, driver, manifest = _published_inventory_paths(tmp_path)
    originals = {driver: driver.read_bytes(), manifest: manifest.read_bytes()}

    def expected_digest(path: Path) -> str:
        return (
            "f7b9f8459d17ea886754a2126c9b94ab95b47ff28343cfca0caedd536f9d0146"
            if path.name.casefold() == "build_manifest.txt"
            else wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256
        )

    _patch_held_snapshot_hashes(monkeypatch, expected_digest)

    def signature_while_replaced(_path: Path):
        targets = (
            (manifest,)
            if replaced == "manifest"
            else (driver,)
            if replaced == "driver"
            else (driver, manifest)
        )
        for target in targets:
            saved = target.with_name(target.name + ".saved")
            impostor = target.with_name(target.name + ".impostor")
            os.replace(target, saved)
            impostor.write_bytes(b"signature-window-impostor")
            os.replace(impostor, target)
            os.replace(target, impostor)
            os.replace(saved, target)
            impostor.unlink()
        return wss_monitor._AuthenticodeIdentity(
            status="Valid",
            signer_thumbprint=wss_monitor.KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT,
        )

    monkeypatch.setattr(
        wss_monitor,
        "_read_authenticode_identity",
        signature_while_replaced,
    )

    assert wss_monitor._inspect_known_old_bidking_driver(str(driver)) is None
    assert {path: path.read_bytes() for path in originals} == originals


def test_held_inventory_handle_blocks_replacement_until_released(tmp_path: Path) -> None:
    target = tmp_path / "WinDivert64.sys"
    replacement = tmp_path / "replacement.sys"
    target.write_bytes(b"approved")
    replacement.write_bytes(b"replacement")

    held = wss_monitor._open_held_stable_file(target)

    assert held is not None
    try:
        with pytest.raises(PermissionError):
            os.replace(replacement, target)
        assert held.assert_unchanged()
    finally:
        held.close()
    os.replace(replacement, target)
    assert target.read_bytes() == b"replacement"


def test_held_inventory_signature_error_releases_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _package_root, driver, _manifest = _published_inventory_paths(tmp_path)
    replacement = tmp_path / "replacement.sys"
    replacement.write_bytes(b"replacement")

    def expected_digest(path: Path) -> str:
        return (
            "f7b9f8459d17ea886754a2126c9b94ab95b47ff28343cfca0caedd536f9d0146"
            if path.name.casefold() == "build_manifest.txt"
            else wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256
        )

    _patch_held_snapshot_hashes(monkeypatch, expected_digest)
    monkeypatch.setattr(
        wss_monitor,
        "_read_authenticode_identity",
        lambda _path: (_ for _ in ()).throw(OSError("signature failure")),
    )

    assert wss_monitor._inspect_known_old_bidking_driver(str(driver)) is None
    os.replace(replacement, driver)
    assert driver.read_bytes() == b"replacement"


def test_known_old_inventory_rejects_same_file_outside_frozen_package_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = tmp_path / "foreign" / "WinDivert64.sys"
    driver.parent.mkdir()
    driver.write_bytes(b"driver")
    monkeypatch.setattr(
        wss_monitor,
        "_stable_file_digest",
        lambda _path: pytest.fail("foreign layout must fail before file inspection"),
    )

    assert wss_monitor._inspect_known_old_bidking_driver(str(driver)) is None


def test_explicit_repair_deletes_only_twice_verified_stopped_old_product_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    inspections = 0

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        if command == ["sc.exe", "delete", SERVICE_NAME]:
            return _sc_result(0, "[SC] DeleteService SUCCESS")
        raise AssertionError(command)

    def inventory_reader(path: str):
        nonlocal inspections
        inspections += 1
        assert wss_monitor._normalize_windows_driver_path(path) == (
            wss_monitor._normalize_windows_driver_path(str(old_driver))
        )
        return _trusted_identity(old_driver)

    repairer = _repairer(runner=runner, inventory_reader=inventory_reader)

    assert repairer.repair(current_driver) == "repaired_restart_required"
    assert repairer.phase == "complete"
    assert inspections == 2
    assert calls == [
        ["sc.exe", "query", SERVICE_NAME],
        ["sc.exe", "qc", SERVICE_NAME],
        ["sc.exe", "query", SERVICE_NAME],
        ["sc.exe", "qc", SERVICE_NAME],
        ["sc.exe", "delete", SERVICE_NAME],
    ]
    assert not any("WinDivert" == command[-1] for command in calls)


def test_explicit_repair_is_idempotent_when_product_service_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return _sc_result(1060, "[SC] OpenService FAILED 1060")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "not_present"
    assert calls == [["sc.exe", "query", SERVICE_NAME]]
    _assert_no_mutation(calls)


def test_repair_classifies_same_path_as_mixed_generation_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "\u65b0\u65e7\u5305 \u540c\u76ee\u5f55" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "query":
            return _service_status(1)
        if command[1] == "qc":
            return _service_config(str(current_driver))
        raise AssertionError(command)

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail(
            "same-path identity is not a frozen old-package inventory"
        ),
    )

    assert repairer.repair(current_driver) == "failed_same_path_mixed_generation"
    assert repairer.phase == "verify_product"
    assert calls == [
        ["sc.exe", "query", SERVICE_NAME],
        ["sc.exe", "qc", SERVICE_NAME],
    ]
    _assert_no_mutation(calls)


@pytest.mark.parametrize(
    ("elevated", "lock_acquired", "expected"),
    (
        (False, True, "failed_not_elevated"),
        (True, False, "failed_repair_busy"),
    ),
)
def test_repair_requires_elevation_and_exclusive_lock_before_scm_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    elevated: bool,
    lock_acquired: bool,
    expected: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []
    repairer = _repairer(
        runner=lambda command: calls.append(command),
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
        elevated=elevated,
        lock_acquired=lock_acquired,
    )

    assert repairer.repair(current_driver) == expected
    assert calls == []


@pytest.mark.parametrize("state", (2, 4))
def test_repair_rejects_start_pending_and_running_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: int,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return _service_status(state)

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "failed_service_conflict"
    _assert_no_mutation(calls)


def test_repair_rejects_query_output_for_any_other_service_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return _service_status(1, service_name="BidKingWinDivertR2")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "failed_service_conflict"
    _assert_no_mutation(calls)


@pytest.mark.parametrize(
    "config_kind",
    ("type", "start", "error", "dependencies", "current", "foreign"),
)
def test_repair_rejects_untrusted_config_or_inventory_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_kind: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "query":
            return _service_status(1)
        path = current_driver if config_kind == "current" else old_driver
        return _service_config(
            str(path),
            service_type=2 if config_kind == "type" else 1,
            start_type=2 if config_kind == "start" else 3,
            error_control=0 if config_kind == "error" else 1,
            dependencies=("Tcpip",) if config_kind == "dependencies" else (),
        )

    repairer = _repairer(
        runner=runner,
        inventory_reader=(
            (lambda _path: None)
            if config_kind == "foreign"
            else (lambda _path: _trusted_identity(old_driver))
        ),
    )

    expected = (
        "failed_same_path_mixed_generation"
        if config_kind == "current"
        else "failed_service_conflict"
    )
    assert repairer.repair(current_driver) == expected
    _assert_no_mutation(calls)


@pytest.mark.parametrize("change_kind", ("state", "config", "file_identity"))
def test_repair_rejects_toctou_change_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change_kind: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    changed_driver = tmp_path / "changed" / "WinDivert64.sys"
    calls: list[list[str]] = []
    query_count = 0
    config_count = 0
    inspect_count = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal query_count, config_count
        calls.append(command)
        if command[1] == "query":
            query_count += 1
            return _service_status(4 if change_kind == "state" and query_count == 2 else 1)
        if command[1] == "qc":
            config_count += 1
            path = (
                changed_driver
                if change_kind == "config" and config_count == 2
                else old_driver
            )
            return _service_config(str(path))
        raise AssertionError(command)

    def inventory_reader(_path: str):
        nonlocal inspect_count
        inspect_count += 1
        return _trusted_identity(
            old_driver,
            epoch=2 if change_kind == "file_identity" and inspect_count == 2 else 1,
        )

    repairer = _repairer(runner=runner, inventory_reader=inventory_reader)

    assert repairer.repair(current_driver) == "failed_service_conflict"
    _assert_no_mutation(calls)


def test_repair_holds_verified_inventory_handles_through_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    probes = []

    class HeldProbe:
        def __init__(self) -> None:
            self.closed = False
            self.assertions = 0

        def assert_unchanged(self) -> bool:
            self.assertions += 1
            return not self.closed

        def close(self) -> None:
            self.closed = True

    def runner(command: list[str]) -> SimpleNamespace:
        if command[1] == "query":
            return _service_status(1)
        if command[1] == "qc":
            return _service_config(str(old_driver))
        if command[1] == "delete":
            assert probes and all(not probe.closed for probe in probes)
            return _sc_result(0, "[SC] DeleteService SUCCESS")
        raise AssertionError(command)

    def inventory_reader(_path: str):
        probe = HeldProbe()
        probes.append(probe)
        return _trusted_identity(old_driver, held_driver=probe)

    repairer = _repairer(runner=runner, inventory_reader=inventory_reader)

    assert repairer.repair(current_driver) == "repaired_restart_required"
    assert len(probes) == 2
    assert all(probe.assertions == 1 and probe.closed for probe in probes)


@pytest.mark.parametrize("failure_phase", ("query", "qc"))
@pytest.mark.parametrize(
    ("error_code", "expected"),
    (
        (1060, "not_present"),
        (5, "failed_access_denied"),
        (1072, "failed_marked_for_delete"),
    ),
)
def test_repair_second_pass_errors_remain_idempotent_or_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
    error_code: int,
    expected: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    query_count = 0
    config_count = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal query_count, config_count
        calls.append(command)
        if command[1] == "query":
            query_count += 1
            if failure_phase == "query" and query_count == 2:
                return _sc_result(error_code, f"[SC] OpenService FAILED {error_code}")
            return _service_status(1)
        if command[1] == "qc":
            config_count += 1
            if failure_phase == "qc" and config_count == 2:
                return _sc_result(
                    error_code,
                    f"[SC] QueryServiceConfig FAILED {error_code}",
                )
            return _service_config(str(old_driver))
        raise AssertionError(command)

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    assert repairer.repair(current_driver) == expected
    if expected == "not_present":
        assert repairer.phase == "complete"
    _assert_no_mutation(calls)


@pytest.mark.parametrize(
    ("error_code", "expected"),
    ((5, "failed_access_denied"), (1072, "failed_marked_for_delete")),
)
def test_repair_rejects_scm_access_and_marked_for_delete_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_code: int,
    expected: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return _sc_result(error_code, f"[SC] OpenService FAILED {error_code}")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == expected
    _assert_no_mutation(calls)


def test_preloader_and_repair_share_the_exclusive_service_lifecycle_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=lambda command: calls.append(command),
        lock_factory=lambda: _lock(False),
    )

    assert preloader.prepare(driver) == "failed_service_conflict"
    assert calls == []


def test_cli_repair_is_explicit_and_emits_only_fixed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mitmdump = tmp_path / "PRIVATE_TOKEN" / "mitmdump.exe"
    mitmdump.parent.mkdir()
    mitmdump.write_bytes(b"exe")
    calls: list[Path] = []

    class FakeRepair:
        phase = "complete"

        def repair(self, path: Path) -> str:
            calls.append(path)
            return "repaired_restart_required"

    monkeypatch.setattr(wss_monitor, "resolve_mitmdump", lambda *_a, **_k: mitmdump)
    monkeypatch.setattr(wss_monitor, "WinDivertStaleProductServiceRepair", FakeRepair)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_wss_live_monitor.py", "--repair-stale-windivert-service"],
    )

    assert wss_monitor.main() == 0
    assert calls == [wss_monitor._bundled_windivert_driver(mitmdump)]
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.strip() == (
        "[wss-compat] stale_product_service_repair=repaired_restart_required "
        "phase=complete"
    )
    assert "PRIVATE_TOKEN" not in output.out


def test_cli_repair_sanitizes_non_enum_state_and_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mitmdump = tmp_path / "mitmdump.exe"
    mitmdump.write_bytes(b"exe")

    class FakeRepair:
        phase = "C:\\Users\\example\\PRIVATE_TOKEN"

        def repair(self, _path: Path) -> str:
            return "PRIVATE_TOKEN"

    monkeypatch.setattr(wss_monitor, "resolve_mitmdump", lambda *_a, **_k: mitmdump)
    monkeypatch.setattr(wss_monitor, "WinDivertStaleProductServiceRepair", FakeRepair)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_wss_live_monitor.py", "--repair-stale-windivert-service"],
    )

    assert wss_monitor.main() == 6
    output = capsys.readouterr()
    assert output.out.strip() == (
        "[wss-compat] stale_product_service_repair=failed_unclassified "
        "phase=not_started"
    )
    assert "PRIVATE_TOKEN" not in output.out


def test_default_monitor_start_never_invokes_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mitmdump = tmp_path / "mitmdump.exe"
    mitmdump.write_bytes(b"exe")

    class UnexpectedRepair:
        def __init__(self):
            pytest.fail("repair must remain explicit")

    monkeypatch.setattr(wss_monitor, "resolve_mitmdump", lambda *_a, **_k: mitmdump)
    monkeypatch.setattr(wss_monitor, "WinDivertStaleProductServiceRepair", UnexpectedRepair)
    monkeypatch.setattr(
        wss_monitor,
        "ca_is_trusted_for_current_user",
        lambda _certificate: False,
    )
    monkeypatch.setattr(sys, "argv", ["run_wss_live_monitor.py"])

    assert wss_monitor.main() == 3


def test_repair_never_accepts_legacy_or_other_service_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []
    repairer = _repairer(
        runner=lambda command: calls.append(command),
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
        service_name="WinDivert",
    )

    assert repairer.repair(current_driver) == "failed_service_conflict"
    assert calls == []


def test_repair_repr_and_states_do_not_retain_private_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "PRIVATE_TOKEN" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    repairer = _repairer(
        runner=lambda _command: _sc_result(
            5,
            "",
            "[SC] OpenService FAILED 5 C:\\Users\\example PRIVATE_TOKEN",
        ),
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "failed_access_denied"
    assert "PRIVATE_TOKEN" not in repr(repairer)
    assert "Users" not in repr(repairer)


def test_repair_rejects_exact_bool_completed_process_returncode_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1] == "query":
            return subprocess.CompletedProcess(
                command,
                False,
                stdout=_service_status(1).stdout,
                stderr="",
            )
        if command[1] == "qc":
            return subprocess.CompletedProcess(
                command,
                False,
                stdout=_service_config(str(old_driver)).stdout,
                stderr="",
            )
        return subprocess.CompletedProcess(command, False, stdout="", stderr="")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    result = repairer.repair(current_driver)
    _assert_no_mutation(calls)
    assert result == "failed_unclassified"


def test_repair_rejects_arabic_indic_sc_numeric_fields_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "query":
            return _service_status(1)
        if command[1] == "qc":
            return _service_config(
                str(old_driver),
                service_type="١",  # type: ignore[arg-type]
                start_type="٣",  # type: ignore[arg-type]
                error_control="١",  # type: ignore[arg-type]
            )
        return _sc_result(0, "[SC] DeleteService SUCCESS")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    result = repairer.repair(current_driver)
    _assert_no_mutation(calls)
    assert result == "failed_service_conflict"


def test_repair_rejects_exact_bool_elevation_before_scm_or_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "query":
            return _service_status(1)
        if command[1] == "qc":
            return _service_config(str(old_driver))
        return _sc_result(0, "[SC] DeleteService SUCCESS")

    repairer = wss_monitor.WinDivertStaleProductServiceRepair(
        runner=runner,
        elevation_checker=lambda: True,
        lock_factory=lambda: _lock(True),
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    result = repairer.repair(current_driver)
    assert calls == []
    assert result == "failed_not_elevated"


def test_repair_never_invokes_numeric_or_text_magic_protocols(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    class MagicInt:
        def __init__(self, value: int) -> None:
            self.value = value
            self.called = False

        def __int__(self) -> int:
            self.called = True
            return self.value

    class MagicText:
        def __init__(self, value: str) -> None:
            self.value = value
            self.called = False

        def __str__(self) -> str:
            self.called = True
            return self.value

    returncode = MagicInt(0)
    stdout = MagicText(_service_status(1).stdout)
    stderr = MagicText("[SC] DeleteService FAILED 1060")

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "query":
            return SimpleNamespace(
                returncode=returncode,
                stdout=stdout,
                stderr="",
            )
        if command[1] == "qc":
            return _service_config(str(old_driver))
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    result = repairer.repair(current_driver)
    assert not returncode.called
    assert not stdout.called
    assert not stderr.called
    _assert_no_mutation(calls)
    assert result == "failed_unclassified"


@pytest.mark.parametrize(
    "returncode",
    (
        True,
        False,
        -1,
        wss_monitor._SC_DWORD_MAX + 1,
    ),
)
def test_sc_projection_rejects_bool_and_out_of_range_returncodes(
    returncode: object,
) -> None:
    result = SimpleNamespace(returncode=returncode, stdout="", stderr="")

    assert wss_monitor._project_sc_command_result(result) is None


def test_sc_projection_rejects_builtin_subclasses_and_missing_fields() -> None:
    class IntSubclass(int):
        pass

    class StrSubclass(str):
        pass

    assert wss_monitor._project_sc_command_result(
        SimpleNamespace(returncode=IntSubclass(0), stdout="", stderr="")
    ) is None
    assert wss_monitor._project_sc_command_result(
        SimpleNamespace(returncode=0, stdout=StrSubclass(""), stderr="")
    ) is None
    assert wss_monitor._project_sc_command_result(
        SimpleNamespace(returncode=0, stdout="", stderr=StrSubclass(""))
    ) is None
    assert wss_monitor._project_sc_command_result(
        SimpleNamespace(returncode=0, stdout="")
    ) is None


@pytest.mark.parametrize(
    "checker_result",
    (True, False, -1, 2),
)
def test_elevation_projection_rejects_bool_and_out_of_range_values(
    monkeypatch: pytest.MonkeyPatch,
    checker_result: object,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")

    assert wss_monitor._windows_elevation_state(lambda: checker_result) == "unknown"


def test_elevation_projection_rejects_int_subclass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IntSubclass(int):
        pass

    monkeypatch.setattr(wss_monitor.os, "name", "nt")

    assert wss_monitor._windows_elevation_state(lambda: IntSubclass(1)) == "unknown"


@pytest.mark.parametrize(
    "result",
    (
        _sc_result(0, "STATE : ١ STOPPED"),
        _sc_result(0, "STATE : 0 STOPPED"),
        _sc_result(0, "STATE : 8 STOPPED"),
        _sc_result(5, "", "[SC] OpenService FAILED ٥"),
        _sc_result(5, "", "[SC] OpenService FAILED 4294967296"),
    ),
)
def test_sc_numeric_fields_reject_unicode_and_out_of_range_values(
    result: SimpleNamespace,
) -> None:
    frozen = _frozen_sc_result(result)
    if "STATE" in result.stdout:
        assert wss_monitor._sc_service_state(frozen) is None
    else:
        assert wss_monitor._sc_error_code(frozen) is None


@pytest.mark.parametrize(
    "config",
    (
        _service_config("C:\\old\\WinDivert64.sys", start_type=5),
        _service_config("C:\\old\\WinDivert64.sys", error_control=4),
        _service_config(
            "C:\\old\\WinDivert64.sys",
            service_type=wss_monitor._SC_DWORD_MAX + 1,
        ),
    ),
)
def test_sc_config_numeric_fields_reject_out_of_range_values(
    config: SimpleNamespace,
) -> None:
    assert wss_monitor._parse_sc_driver_service_config(
        _frozen_sc_result(config)
    ) is None


def test_repair_rejects_missing_projected_field_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=_service_status(1).stdout)

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "failed_unclassified"
    _assert_no_mutation(calls)


def test_preloader_prepare_rejects_missing_projected_field_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="STATE : 1 STOPPED")

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )

    assert preloader.prepare(driver) == "failed_unclassified"
    _assert_no_mutation(calls)
    assert "STATE" not in repr(preloader)


def test_preloader_cleanup_rejects_invalid_projection_before_stop_or_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    config_queries = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal config_queries
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _sc_result(0, "STATE : 4 RUNNING")
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            config_queries += 1
            if config_queries == 1:
                return _service_config(str(driver.resolve()))
            return SimpleNamespace(
                returncode=0,
                stdout="PRIVATE_TOKEN",
            )
        return _sc_result(0)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )
    assert preloader.prepare(driver) == "already_preloaded"

    preloader.release()

    assert preloader.state == "failed_unclassified"
    assert preloader.phase == "cleanup"
    _assert_no_mutation(calls)
    assert "PRIVATE_TOKEN" not in repr(preloader)


def test_repair_rejects_embedded_nul_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "query":
            return _sc_result(0, "PRIVATE_CANARY\x00\n" + _service_status(1).stdout)
        if command[1] == "qc":
            return _service_config(str(old_driver))
        return _sc_result(0, "[SC] DeleteService SUCCESS")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    assert repairer.repair(current_driver) == "failed_unclassified"
    _assert_no_mutation(calls)
    assert "PRIVATE_CANARY" not in repr(repairer)


def test_repair_rejects_oversized_stdout_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    oversized_status = (
        "X" * 1_048_577
        + "\n"
        + _service_status(1).stdout
    )

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "query":
            return _sc_result(0, oversized_status)
        if command[1] == "qc":
            return _service_config(str(old_driver))
        return _sc_result(0, "[SC] DeleteService SUCCESS")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    assert repairer.repair(current_driver) == "failed_unclassified"
    _assert_no_mutation(calls)


def test_repair_folds_returncode_getter_exception_without_delete_or_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    class RaisingResult:
        @property
        def returncode(self) -> int:
            raise RuntimeError("PRIVATE_CANARY")

        stdout = ""
        stderr = ""

    def runner(command: list[str]) -> RaisingResult:
        calls.append(command)
        return RaisingResult()

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "failed_unclassified"
    _assert_no_mutation(calls)
    captured = capsys.readouterr()
    assert "PRIVATE_CANARY" not in captured.out
    assert "PRIVATE_CANARY" not in captured.err
    assert "PRIVATE_CANARY" not in repr(repairer)


def test_repair_projects_mutable_raw_query_result_only_once_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    class MutableResult:
        def __init__(self) -> None:
            self.reads = {"returncode": 0, "stdout": 0, "stderr": 0}

        @property
        def returncode(self) -> int:
            self.reads["returncode"] += 1
            return 0

        @property
        def stdout(self) -> str:
            self.reads["stdout"] += 1
            if self.reads["stdout"] == 1:
                return "MUTABLE_UNVERIFIED"
            return _service_status(1).stdout

        @property
        def stderr(self) -> str:
            self.reads["stderr"] += 1
            return ""

    mutable_query = MutableResult()

    def runner(command: list[str]) -> object:
        calls.append(command)
        if command[1] == "query":
            return mutable_query
        if command[1] == "qc":
            return _service_config(str(old_driver))
        return _sc_result(0, "[SC] DeleteService SUCCESS")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    assert repairer.repair(current_driver) == "failed_service_conflict"
    _assert_no_mutation(calls)
    assert mutable_query.reads == {"returncode": 1, "stdout": 1, "stderr": 1}


@pytest.mark.parametrize("field_name", ("stdout", "stderr"))
def test_sc_projection_rejects_nul_in_each_text_field(field_name: str) -> None:
    values = {"stdout": "", "stderr": ""}
    values[field_name] = "SAFE\x00TAIL"

    assert wss_monitor._project_sc_command_result(
        _sc_result(0, values["stdout"], values["stderr"])
    ) is None


@pytest.mark.parametrize("field_name", ("stdout", "stderr"))
@pytest.mark.parametrize(
    ("size_delta", "accepted"),
    ((0, True), (1, False)),
)
def test_sc_projection_enforces_each_ascii_text_byte_limit(
    field_name: str,
    size_delta: int,
    accepted: bool,
) -> None:
    values = {"stdout": "", "stderr": ""}
    values[field_name] = "X" * (wss_monitor._SC_TEXT_MAX_BYTES + size_delta)

    projected = wss_monitor._project_sc_command_result(
        _sc_result(0, values["stdout"], values["stderr"])
    )

    assert (projected is not None) is accepted


@pytest.mark.parametrize("field_name", ("stdout", "stderr"))
def test_sc_projection_enforces_utf8_bytes_not_character_count(
    field_name: str,
) -> None:
    exact = "é" * (wss_monitor._SC_TEXT_MAX_BYTES // 2)
    values = {"stdout": "", "stderr": ""}
    values[field_name] = exact
    assert wss_monitor._project_sc_command_result(
        _sc_result(0, values["stdout"], values["stderr"])
    ) is not None

    values[field_name] = exact + "é"
    assert wss_monitor._project_sc_command_result(
        _sc_result(0, values["stdout"], values["stderr"])
    ) is None


@pytest.mark.parametrize(
    "invalid_kind",
    ("nul", "oversized"),
)
def test_repair_rejects_invalid_stderr_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_kind: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []
    invalid_stderr = (
        "PRIVATE_CANARY\x00"
        if invalid_kind == "nul"
        else "X" * (wss_monitor._SC_TEXT_MAX_BYTES + 1)
    )

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return _sc_result(0, _service_status(1).stdout, invalid_stderr)

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "failed_unclassified"
    _assert_no_mutation(calls)
    assert "PRIVATE_CANARY" not in repr(repairer)


@pytest.mark.parametrize("raising_field", ("stdout", "stderr"))
def test_repair_folds_text_getter_exception_once_without_delete_or_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raising_field: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    class RaisingResult:
        def __init__(self) -> None:
            self.reads = {"returncode": 0, "stdout": 0, "stderr": 0}

        @property
        def returncode(self) -> int:
            self.reads["returncode"] += 1
            return 0

        @property
        def stdout(self) -> str:
            self.reads["stdout"] += 1
            if raising_field == "stdout":
                raise RuntimeError("PRIVATE_CANARY")
            return _service_status(1).stdout

        @property
        def stderr(self) -> str:
            self.reads["stderr"] += 1
            if raising_field == "stderr":
                raise RuntimeError("PRIVATE_CANARY")
            return ""

    raw_result = RaisingResult()

    def runner(command: list[str]) -> RaisingResult:
        calls.append(command)
        return raw_result

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "failed_unclassified"
    _assert_no_mutation(calls)
    assert all(reads <= 1 for reads in raw_result.reads.values())
    captured = capsys.readouterr()
    assert "PRIVATE_CANARY" not in captured.out
    assert "PRIVATE_CANARY" not in captured.err
    assert "PRIVATE_CANARY" not in repr(repairer)


def test_repair_folds_runner_exception_without_delete_or_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        raise RuntimeError("PRIVATE_CANARY")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    assert repairer.repair(current_driver) == "failed_unclassified"
    _assert_no_mutation(calls)
    captured = capsys.readouterr()
    assert "PRIVATE_CANARY" not in captured.out
    assert "PRIVATE_CANARY" not in captured.err
    assert "PRIVATE_CANARY" not in repr(repairer)


@pytest.mark.parametrize("failure_type", (KeyboardInterrupt, SystemExit))
def test_sc_runner_does_not_swallow_process_control_baseexceptions(
    failure_type: type[BaseException],
) -> None:
    def runner(_command: list[str]) -> SimpleNamespace:
        raise failure_type("PRIVATE_CANARY")

    with pytest.raises(failure_type):
        wss_monitor._run_projected_sc_command(runner, ["sc.exe", "query", SERVICE_NAME])


@pytest.mark.parametrize("failure_type", (KeyboardInterrupt, SystemExit))
def test_sc_getter_does_not_swallow_process_control_baseexceptions(
    failure_type: type[BaseException],
) -> None:
    class RaisingResult:
        @property
        def returncode(self) -> int:
            raise failure_type("PRIVATE_CANARY")

        stdout = ""
        stderr = ""

    with pytest.raises(failure_type):
        wss_monitor._project_sc_command_result(RaisingResult())


def test_repair_projects_mutable_raw_qc_result_only_once_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    class MutableResult:
        def __init__(self) -> None:
            self.reads = {"returncode": 0, "stdout": 0, "stderr": 0}

        @property
        def returncode(self) -> int:
            self.reads["returncode"] += 1
            return 0

        @property
        def stdout(self) -> str:
            self.reads["stdout"] += 1
            if self.reads["stdout"] == 1:
                return "MUTABLE_UNVERIFIED"
            return _service_config(str(old_driver)).stdout

        @property
        def stderr(self) -> str:
            self.reads["stderr"] += 1
            return ""

    mutable_qc = MutableResult()

    def runner(command: list[str]) -> object:
        calls.append(command)
        if command[1] == "query":
            return _service_status(1)
        if command[1] == "qc":
            return mutable_qc
        return _sc_result(0, "[SC] DeleteService SUCCESS")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    assert repairer.repair(current_driver) == "failed_service_conflict"
    _assert_no_mutation(calls)
    assert mutable_qc.reads == {"returncode": 1, "stdout": 1, "stderr": 1}


def test_preloader_projects_mutable_legacy_state_once_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []

    class MutableResult:
        def __init__(self) -> None:
            self.reads = {"returncode": 0, "stdout": 0, "stderr": 0}

        @property
        def returncode(self) -> int:
            self.reads["returncode"] += 1
            return 0

        @property
        def stdout(self) -> str:
            self.reads["stdout"] += 1
            return (
                "MUTABLE_UNVERIFIED"
                if self.reads["stdout"] == 1
                else "STATE : 1 STOPPED"
            )

        @property
        def stderr(self) -> str:
            self.reads["stderr"] += 1
            return ""

    mutable_legacy = MutableResult()

    def runner(command: list[str]) -> object:
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return mutable_legacy
        return _sc_result(1060, "[SC] OpenService FAILED 1060")

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )

    assert preloader.prepare(driver) == "failed_service_conflict"
    _assert_no_mutation(calls)
    assert mutable_legacy.reads == {"returncode": 1, "stdout": 1, "stderr": 1}


def test_preloader_cleanup_projects_mutable_qc_once_before_stop_or_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    qc_calls = 0

    class MutableResult:
        def __init__(self) -> None:
            self.reads = {"returncode": 0, "stdout": 0, "stderr": 0}

        @property
        def returncode(self) -> int:
            self.reads["returncode"] += 1
            return 0

        @property
        def stdout(self) -> str:
            self.reads["stdout"] += 1
            if self.reads["stdout"] == 1:
                return "MUTABLE_UNVERIFIED"
            return _service_config(str(driver.resolve())).stdout

        @property
        def stderr(self) -> str:
            self.reads["stderr"] += 1
            return ""

    mutable_cleanup_qc = MutableResult()

    def runner(command: list[str]) -> object:
        nonlocal qc_calls
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _sc_result(0, "STATE : 4 RUNNING")
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            qc_calls += 1
            if qc_calls == 1:
                return _service_config(str(driver.resolve()))
            return mutable_cleanup_qc
        return _sc_result(0)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )
    assert preloader.prepare(driver) == "already_preloaded"

    preloader.release()

    assert preloader.state == "failed_service_conflict"
    assert preloader.phase == "cleanup"
    _assert_no_mutation(calls)
    assert mutable_cleanup_qc.reads == {
        "returncode": 1,
        "stdout": 1,
        "stderr": 1,
    }


def test_preloader_prepare_folds_runner_exception_without_mutation_or_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        raise RuntimeError("PRIVATE_CANARY")

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )

    assert preloader.prepare(driver) == "failed_unclassified"
    _assert_no_mutation(calls)
    captured = capsys.readouterr()
    assert "PRIVATE_CANARY" not in captured.out
    assert "PRIVATE_CANARY" not in captured.err
    assert "PRIVATE_CANARY" not in repr(preloader)


def test_preloader_cleanup_folds_runner_exception_before_stop_or_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    preparing = True

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if not preparing:
            raise RuntimeError("PRIVATE_CANARY")
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _sc_result(0, "STATE : 4 RUNNING")
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(driver.resolve()))
        return _sc_result(0)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )
    assert preloader.prepare(driver) == "already_preloaded"
    preparing = False

    preloader.release()

    assert preloader.state == "failed_unclassified"
    assert preloader.phase == "cleanup"
    _assert_no_mutation(calls)
    captured = capsys.readouterr()
    assert "PRIVATE_CANARY" not in captured.out
    assert "PRIVATE_CANARY" not in captured.err
    assert "PRIVATE_CANARY" not in repr(preloader)


def _current_and_old_driver_paths(tmp_path: Path) -> tuple[Path, Path]:
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir(parents=True)
    current_driver.write_bytes(b"current")
    return current_driver, tmp_path / "old" / "WinDivert64.sys"


def _successful_repair_runner(
    calls: list[list[str]],
    old_driver: Path,
    *,
    delete_result_factory=lambda: _sc_result(0, "[SC] DeleteService SUCCESS"),
):
    def runner(command: list[str]) -> object:
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        if command == ["sc.exe", "delete", SERVICE_NAME]:
            return delete_result_factory()
        raise AssertionError(command)

    return runner


@pytest.mark.parametrize("getter_name", ("returncode", "stdout", "stderr"))
@pytest.mark.parametrize("same_instance", (False, True))
def test_delete_projection_reentry_is_rejected_before_second_scm_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    getter_name: str,
    same_instance: bool,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver, old_driver = _current_and_old_driver_paths(tmp_path)
    calls: list[list[str]] = []
    nested_results: list[str] = []
    triggered = False
    repairer: wss_monitor.WinDivertStaleProductServiceRepair
    nested: wss_monitor.WinDivertStaleProductServiceRepair

    class ReentrantDeleteResult:
        def _trigger(self) -> None:
            nonlocal triggered
            if triggered:
                return
            triggered = True
            nested_results.append(nested.repair(current_driver))

        @property
        def returncode(self) -> int:
            if getter_name == "returncode":
                self._trigger()
            return 0

        @property
        def stdout(self) -> str:
            if getter_name == "stdout":
                self._trigger()
            return "[SC] DeleteService SUCCESS"

        @property
        def stderr(self) -> str:
            if getter_name == "stderr":
                self._trigger()
            return ""

    runner = _successful_repair_runner(
        calls,
        old_driver,
        delete_result_factory=ReentrantDeleteResult,
    )
    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )
    nested = (
        repairer
        if same_instance
        else _repairer(
            runner=runner,
            inventory_reader=lambda _path: _trusted_identity(old_driver),
        )
    )

    assert repairer.repair(current_driver) == "repaired_restart_required"
    assert nested_results == ["failed_repair_busy"]
    assert sum(command[1] == "delete" for command in calls) == 1
    assert repairer.state == "repaired_restart_required"
    assert repairer.phase == "complete"
    if not same_instance:
        assert nested.state == "not_started"
        assert nested.phase == "not_started"


@pytest.mark.parametrize("same_instance", (False, True))
def test_runner_reentry_is_rejected_before_second_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_instance: bool,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver, old_driver = _current_and_old_driver_paths(tmp_path)
    calls: list[list[str]] = []
    nested_results: list[str] = []
    triggered = False
    repairer: wss_monitor.WinDivertStaleProductServiceRepair
    nested: wss_monitor.WinDivertStaleProductServiceRepair

    def runner(command: list[str]) -> object:
        nonlocal triggered
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        if command == ["sc.exe", "delete", SERVICE_NAME]:
            if not triggered:
                triggered = True
                nested_results.append(nested.repair(current_driver))
            return _sc_result(0, "[SC] DeleteService SUCCESS")
        raise AssertionError(command)

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )
    nested = (
        repairer
        if same_instance
        else _repairer(
            runner=runner,
            inventory_reader=lambda _path: _trusted_identity(old_driver),
        )
    )

    assert repairer.repair(current_driver) == "repaired_restart_required"
    assert nested_results == ["failed_repair_busy"]
    assert sum(command[1] == "delete" for command in calls) == 1
    assert repairer.state == "repaired_restart_required"
    assert repairer.phase == "complete"


def test_repair_blocks_cross_instance_prepare_before_preloader_state_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver, old_driver = _current_and_old_driver_paths(tmp_path)
    preload_driver = tmp_path / "preload" / "WinDivert64.sys"
    preload_driver.parent.mkdir()
    preload_driver.write_bytes(b"audited")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    repair_calls: list[list[str]] = []
    preloader_calls: list[list[str]] = []
    nested_results: list[str] = []
    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=lambda command: preloader_calls.append(command),
        lock_factory=lambda: _lock(True),
    )
    triggered = False

    def runner(command: list[str]) -> object:
        nonlocal triggered
        repair_calls.append(command)
        if not triggered:
            triggered = True
            nested_results.append(preloader.prepare(preload_driver))
        if command[1] == "query":
            return _service_status(1)
        if command[1] == "qc":
            return _service_config(str(old_driver))
        return _sc_result(0, "[SC] DeleteService SUCCESS")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    assert repairer.repair(current_driver) == "repaired_restart_required"
    assert nested_results == ["failed_service_conflict"]
    assert preloader_calls == []
    assert preloader.state == "not_applicable"
    assert preloader.phase == "not_started"
    assert preloader._service_ownership is None
    assert sum(command[1] == "delete" for command in repair_calls) == 1


def test_prepare_blocks_cross_instance_repair_and_finishes_outer_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited")
    current_driver, old_driver = _current_and_old_driver_paths(tmp_path / "repair")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    repair_calls: list[list[str]] = []
    nested_results: list[str] = []
    repairer = _repairer(
        runner=_successful_repair_runner(repair_calls, old_driver),
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )
    prepare_calls: list[list[str]] = []
    triggered = False

    def runner(command: list[str]) -> object:
        nonlocal triggered
        prepare_calls.append(command)
        if not triggered:
            triggered = True
            nested_results.append(repairer.repair(current_driver))
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _sc_result(0, "STATE : 4 RUNNING")
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(driver.resolve()))
        raise AssertionError(command)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )

    assert preloader.prepare(driver) == "already_preloaded"
    assert nested_results == ["failed_repair_busy"]
    assert repair_calls == []
    assert preloader.state == "already_preloaded"
    assert preloader.phase == "complete"
    assert preloader._service_ownership == "verified_existing"


def test_prepare_same_instance_reentry_does_not_reset_outer_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    nested_results: list[str] = []
    triggered = False
    preloader: wss_monitor.WinDivertCompatibilityPreloader

    def runner(command: list[str]) -> object:
        nonlocal triggered
        calls.append(command)
        if not triggered:
            triggered = True
            nested_results.append(preloader.prepare(driver))
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _sc_result(0, "STATE : 4 RUNNING")
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(driver.resolve()))
        raise AssertionError(command)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )

    assert preloader.prepare(driver) == "already_preloaded"
    assert nested_results == ["failed_service_conflict"]
    assert calls == [
        ["sc.exe", "query", "WinDivert"],
        ["sc.exe", "query", SERVICE_NAME],
        ["sc.exe", "qc", SERVICE_NAME],
    ]
    assert preloader.state == "already_preloaded"
    assert preloader.phase == "complete"
    assert preloader._service_ownership == "verified_existing"


def test_repair_blocks_cross_instance_release_before_cleanup_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver, old_driver = _current_and_old_driver_paths(tmp_path)
    cleanup_driver = tmp_path / "cleanup" / "WinDivert64.sys"
    cleanup_driver.parent.mkdir()
    cleanup_driver.write_bytes(b"audited")
    cleanup_calls: list[list[str]] = []
    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=lambda command: cleanup_calls.append(command),
        lock_factory=lambda: _lock(True),
    )
    preloader.state = "preloaded"
    preloader.phase = "complete"
    preloader._claim_service_ownership("created_this_run", cleanup_driver)
    ownership_before = (
        preloader._service_ownership,
        preloader._expected_driver_path,
    )
    repair_calls: list[list[str]] = []
    triggered = False

    def runner(command: list[str]) -> object:
        nonlocal triggered
        repair_calls.append(command)
        if not triggered:
            triggered = True
            preloader.release()
        if command[1] == "query":
            return _service_status(1)
        if command[1] == "qc":
            return _service_config(str(old_driver))
        return _sc_result(0, "[SC] DeleteService SUCCESS")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )

    assert repairer.repair(current_driver) == "repaired_restart_required"
    assert cleanup_calls == []
    assert preloader.state == "preloaded"
    assert preloader.phase == "complete"
    assert (
        preloader._service_ownership,
        preloader._expected_driver_path,
    ) == ownership_before
    assert sum(command[1] == "delete" for command in repair_calls) == 1


def test_release_blocks_cross_instance_repair_and_cleans_outer_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited")
    current_driver, old_driver = _current_and_old_driver_paths(tmp_path / "repair")
    preloader_calls: list[list[str]] = []
    nested_results: list[str] = []
    repair_calls: list[list[str]] = []
    repairer = _repairer(
        runner=_successful_repair_runner(repair_calls, old_driver),
        inventory_reader=lambda _path: _trusted_identity(old_driver),
    )
    triggered = False

    def runner(command: list[str]) -> object:
        nonlocal triggered
        preloader_calls.append(command)
        if not triggered:
            triggered = True
            nested_results.append(repairer.repair(current_driver))
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(driver))
        if command in (
            ["sc.exe", "stop", SERVICE_NAME],
            ["sc.exe", "delete", SERVICE_NAME],
        ):
            return _sc_result(0)
        raise AssertionError(command)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )
    preloader.state = "preloaded"
    preloader.phase = "complete"
    preloader._claim_service_ownership("created_this_run", driver)

    preloader.release()

    assert nested_results == ["failed_repair_busy"]
    assert repair_calls == []
    assert sum(command[1] == "stop" for command in preloader_calls) == 1
    assert sum(command[1] == "delete" for command in preloader_calls) == 1
    assert not any(command[1] in {"create", "config"} for command in preloader_calls)
    assert preloader.state == "preloaded"
    assert preloader.phase == "cleanup"
    assert preloader._service_ownership is None


def test_release_same_instance_reentry_cleans_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited")
    calls: list[list[str]] = []
    triggered = False
    preloader: wss_monitor.WinDivertCompatibilityPreloader

    def runner(command: list[str]) -> object:
        nonlocal triggered
        calls.append(command)
        if not triggered:
            triggered = True
            preloader.release()
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(driver))
        if command in (
            ["sc.exe", "stop", SERVICE_NAME],
            ["sc.exe", "delete", SERVICE_NAME],
        ):
            return _sc_result(0)
        raise AssertionError(command)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )
    preloader.state = "preloaded"
    preloader.phase = "complete"
    preloader._claim_service_ownership("created_this_run", driver)

    preloader.release()

    assert sum(command[1] == "stop" for command in calls) == 1
    assert sum(command[1] == "delete" for command in calls) == 1
    assert preloader.state == "preloaded"
    assert preloader.phase == "cleanup"
    assert preloader._service_ownership is None


@pytest.mark.parametrize("same_instance", (False, True))
def test_prepare_runner_reentry_creates_and_starts_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_instance: bool,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    nested_results: list[str] = []
    triggered = False
    create_calls = 0
    preloader: wss_monitor.WinDivertCompatibilityPreloader
    nested: wss_monitor.WinDivertCompatibilityPreloader

    def runner(command: list[str]) -> object:
        nonlocal triggered, create_calls
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", SERVICE_NAME]:
            if create_calls == 0:
                return _sc_result(1060, "[SC] OpenService FAILED 1060")
            return _sc_result(0, "STATE : 4 RUNNING")
        if command[1] == "create":
            if not triggered:
                triggered = True
                nested_results.append(nested.prepare(driver))
            create_calls += 1
            return _sc_result(0)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(driver.resolve()))
        if command == ["sc.exe", "start", SERVICE_NAME]:
            return _sc_result(0)
        raise AssertionError(command)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(True),
    )
    nested = (
        preloader
        if same_instance
        else wss_monitor.WinDivertCompatibilityPreloader(
            runner=runner,
            lock_factory=lambda: _lock(True),
        )
    )

    assert preloader.prepare(driver) == "preloaded"
    assert nested_results == ["failed_service_conflict"]
    assert sum(command[1] == "create" for command in calls) == 1
    assert sum(command[1] == "start" for command in calls) == 1
    assert not any(command[1] in {"stop", "delete", "config"} for command in calls)
    assert preloader.state == "preloaded"
    assert preloader.phase == "complete"
    assert preloader._service_ownership == "created_this_run"


@pytest.mark.parametrize("failure_type", (RuntimeError, KeyboardInterrupt, SystemExit))
def test_lifecycle_guard_releases_after_runner_exception_or_baseexception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[BaseException],
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver, _old_driver = _current_and_old_driver_paths(tmp_path)
    first_call = True

    def runner(_command: list[str]) -> SimpleNamespace:
        nonlocal first_call
        if first_call:
            first_call = False
            raise failure_type("PRIVATE_CANARY")
        return _sc_result(1060, "[SC] OpenService FAILED 1060")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    if issubclass(failure_type, Exception):
        assert repairer.repair(current_driver) == "failed_unclassified"
    else:
        with pytest.raises(failure_type):
            repairer.repair(current_driver)

    assert repairer.repair(current_driver) == "not_present"
    assert repairer.state == "not_present"
    assert repairer.phase == "complete"


@pytest.mark.parametrize("failure_type", (KeyboardInterrupt, SystemExit))
def test_lifecycle_guard_releases_after_result_getter_baseexception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[BaseException],
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver, _old_driver = _current_and_old_driver_paths(tmp_path)

    class RaisingResult:
        @property
        def returncode(self) -> int:
            raise failure_type("PRIVATE_CANARY")

        stdout = ""
        stderr = ""

    first_call = True

    def runner(_command: list[str]) -> object:
        nonlocal first_call
        if first_call:
            first_call = False
            return RaisingResult()
        return _sc_result(1060, "[SC] OpenService FAILED 1060")

    repairer = _repairer(
        runner=runner,
        inventory_reader=lambda _path: pytest.fail("inventory must not be read"),
    )

    with pytest.raises(failure_type):
        repairer.repair(current_driver)

    assert repairer.repair(current_driver) == "not_present"
    assert repairer.state == "not_present"
    assert repairer.phase == "complete"


def test_process_guard_allows_other_threads_to_reach_named_mutex_boundary() -> None:
    entries: list[bool] = []

    def enter_from_other_thread() -> None:
        with wss_monitor._non_reentrant_windivert_service_lifecycle_entry() as entered:
            entries.append(entered)

    with wss_monitor._non_reentrant_windivert_service_lifecycle_entry() as entered:
        assert entered is True
        worker = threading.Thread(target=enter_from_other_thread)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert entries == [True]
