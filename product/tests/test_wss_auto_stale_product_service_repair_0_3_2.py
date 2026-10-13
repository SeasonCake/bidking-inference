from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_wss_live_monitor as wss_monitor  # noqa: E402


SERVICE_NAME = "BidKingWinDivertR1"
PUBLISHED_STALE_MANIFEST_SHA256S = (
    "5f68750eea71ac89c3a9b38c0b399efec8a29abf9ace330013f8ed9dddba0581",
    "e32bfe529b1b8099b10990ec02d86c2bf572e2c37f097070ef79d3613fe9c1c8",
    "942842957e1e6fe289bab29531c7216055f961d1b527fd05ffe716c9d364dd56",
    "e5de65c97d0198ec38b759328f0e63ea7d89da9f8661a0d7d2be6f43bbc58f06",
    "54ad1dee92ce37b6ee86ba8b1c8e8f1b96f2174db5d48f536cc352ac6c07475b",
    "c37429bdccde205151bd8316631a85756d86a9084ab6bda5817c28d9ff188e9c",
    "52e3e6446aedaefcb680464efe09664c2604e8ecd76a8f0bf13d847d59d085e1",
    "e7ff0e81969f67c79fc05dbc44c5a6299d2ea865d65af4f08026d633dac1448c",
    "b839666a67e5ac84a094edad510718c3590e6dc83c3d59f9291c522b02231ce7",
    "f8180da40eb43ad8270af58b1c2d532e6209bcd80e56bca3bbb6918fdb0afe90",
    "8bac0b68a8a9508014116ac1956e5da1cdd766618f30f252921d6f57d9fc3409",
    "f7b9f8459d17ea886754a2126c9b94ab95b47ff28343cfca0caedd536f9d0146",
    "7ba99e43e1a5e831535022e2d12f25c9d711f629ef0225f5b459da59b3593e49",
    "020dba82050619e20710681acd5382827b9d9657af848605bbd965c3646f5d0b",
)


def _sc_result(returncode: int, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _service_status(state: int, service_name: str = SERVICE_NAME):
    return _sc_result(
        0,
        "\n".join(
            (
                f"SERVICE_NAME: {service_name}",
                "        TYPE               : 1  KERNEL_DRIVER",
                f"        STATE              : {state}  STATE",
            )
        ),
    )


def _service_config(driver_path: str):
    return _sc_result(
        0,
        "\n".join(
            (
                "[SC] QueryServiceConfig SUCCESS",
                "        TYPE               : 1  KERNEL_DRIVER",
                "        START_TYPE         : 3  DEMAND_START",
                "        ERROR_CONTROL      : 1  NORMAL",
                f"        BINARY_PATH_NAME   : {driver_path}",
                "        LOAD_ORDER_GROUP   :",
                "        TAG                : 0",
                "        DISPLAY_NAME       : BidKing WinDivert Compatibility",
                "        DEPENDENCIES       :",
                "        SERVICE_START_NAME :",
            )
        ),
    )


def _trusted_identity(
    path: Path,
    epoch: int = 1,
    *,
    manifest_sha256: str | None = None,
):
    return wss_monitor._StaleBidKingDriverIdentity(
        normalized_path=wss_monitor._normalize_windows_driver_path(str(path)),
        driver_file_identity=(11, epoch, 94144, 123456789),
        driver_sha256=wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
        signer_thumbprint=wss_monitor.KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT,
        package_manifest_sha256=(
            manifest_sha256
            or wss_monitor.KNOWN_OLD_BIDKING_PACKAGE_MANIFEST_SHA256
        ),
        manifest_file_identity=(22, epoch, 2048, 987654321),
    )


@contextmanager
def _lock(acquired: bool = True):
    yield acquired


def _repairer(*, runner, inventory_reader, service_name: str = SERVICE_NAME):
    return wss_monitor.WinDivertStaleProductServiceRepair(
        runner=runner,
        service_name=service_name,
        elevation_checker=lambda: 1,
        lock_factory=lambda: _lock(),
        inventory_reader=inventory_reader,
    )


def _conflicted_preloader(*, runner=None):
    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner or (lambda command: pytest.fail(str(command))),
        lock_factory=lambda: _lock(),
    )
    preloader.state = "failed_service_conflict"
    preloader.phase = "verify_product"
    return preloader


def _assert_no_mutation(calls: list[list[str]]) -> None:
    assert not any(
        len(command) > 1
        and command[1] in {"create", "start", "stop", "delete", "config"}
        for command in calls
    )


def test_auto_repair_kill_switch_is_default_on_with_explicit_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_name = wss_monitor.DISABLE_WINDIVERT_AUTO_STALE_REPAIR_ENV
    monkeypatch.delenv(env_name, raising=False)
    assert not wss_monitor._env_switch_enabled(env_name)
    monkeypatch.setenv(env_name, "1")
    assert wss_monitor._env_switch_enabled(env_name)


def test_published_stale_manifest_allowlist_is_exact() -> None:
    assert wss_monitor.KNOWN_PUBLISHED_BIDKING_PACKAGE_MANIFEST_SHA256S == (
        frozenset(
            (
                wss_monitor.KNOWN_OLD_BIDKING_PACKAGE_MANIFEST_SHA256,
                *PUBLISHED_STALE_MANIFEST_SHA256S,
            )
        )
    )


def test_auto_repair_deletes_exact_product_once_reads_back_1060_and_requires_reboot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    deleted = False

    def runner(command: list[str]):
        nonlocal deleted
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _service_status(1, "WinDivert")
        if command == ["sc.exe", "query", SERVICE_NAME]:
            if deleted:
                return _sc_result(1060, "[SC] OpenService FAILED 1060")
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        if command == ["sc.exe", "delete", SERVICE_NAME]:
            deleted = True
            return _sc_result(0, "[SC] DeleteService SUCCESS")
        raise AssertionError(command)

    preloader = wss_monitor.WinDivertCompatibilityPreloader(
        runner=runner,
        lock_factory=lambda: _lock(),
    )
    assert preloader.prepare(current_driver) == "failed_service_conflict"
    assert preloader.phase == "verify_product"

    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=lambda _path: _trusted_identity(old_driver),
        ),
    )

    assert outcome == wss_monitor._WinDivertAutoRepairOutcome(
        preload_state="repaired_restart_required",
        repair_state="not_present",
        repair_phase="complete",
    )
    assert calls.count(["sc.exe", "delete", SERVICE_NAME]) == 1
    delete_index = calls.index(["sc.exe", "delete", SERVICE_NAME])
    assert calls[delete_index + 1] == ["sc.exe", "query", SERVICE_NAME]
    assert not any(command[1] in {"create", "start"} for command in calls)
    assert not any(
        command[1] in {"create", "start", "stop", "delete", "config"}
        and command[-1] == "WinDivert"
        for command in calls
        if len(command) > 1
    )


@pytest.mark.parametrize("manifest_sha256", PUBLISHED_STALE_MANIFEST_SHA256S)
def test_auto_repair_accepts_each_published_product_manifest_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_sha256: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    deleted = False

    def runner(command: list[str]):
        nonlocal deleted
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            if deleted:
                return _sc_result(1060, "[SC] OpenService FAILED 1060")
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        if command == ["sc.exe", "delete", SERVICE_NAME]:
            deleted = True
            return _sc_result(0, "[SC] DeleteService SUCCESS")
        raise AssertionError(command)

    preloader = _conflicted_preloader(runner=runner)
    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=lambda _path: _trusted_identity(
                old_driver,
                manifest_sha256=manifest_sha256,
            ),
        ),
    )

    assert outcome == wss_monitor._WinDivertAutoRepairOutcome(
        preload_state="repaired_restart_required",
        repair_state="not_present",
        repair_phase="complete",
    )
    assert calls.count(["sc.exe", "delete", SERVICE_NAME]) == 1


@pytest.mark.parametrize(
    "invalid_identity",
    ("manifest", "driver", "signer", "path"),
)
def test_auto_repair_rejects_unpublished_or_foreign_identity_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_identity: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []

    def runner(command: list[str]):
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        raise AssertionError(command)

    identity = _trusted_identity(
        old_driver,
        manifest_sha256=PUBLISHED_STALE_MANIFEST_SHA256S[-1],
    )
    replacements = {
        "manifest": {"package_manifest_sha256": "0" * 64},
        "driver": {"driver_sha256": "0" * 64},
        "signer": {"signer_thumbprint": "0" * 40},
        "path": {
            "normalized_path": wss_monitor._normalize_windows_driver_path(
                str(tmp_path / "foreign" / "WinDivert64.sys")
            )
        },
    }
    identity = replace(identity, **replacements[invalid_identity])

    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        _conflicted_preloader(runner=runner),
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=lambda _path: identity,
        ),
    )

    assert outcome == wss_monitor._WinDivertAutoRepairOutcome(
        preload_state="failed_service_conflict",
        repair_state="failed_service_conflict",
        repair_phase="verify_product",
    )
    _assert_no_mutation(calls)


def test_auto_repair_promotes_same_path_mixed_generation_to_helper_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "\u542b\u4e2d\u6587 \u7684\u5f53\u524d\u76ee\u5f55" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]):
        calls.append(command)
        if command[1] == "query":
            return _service_status(1)
        if command[1] == "qc":
            return _service_config(str(current_driver))
        raise AssertionError(command)

    preloader = _conflicted_preloader(runner=runner)
    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=lambda _path: pytest.fail(
                "same-path identity must never enter frozen-old deletion"
            ),
        ),
    )

    assert outcome == wss_monitor._WinDivertAutoRepairOutcome(
        preload_state="failed_same_path_mixed_generation",
        repair_state="failed_same_path_mixed_generation",
        repair_phase="verify_product",
    )
    assert preloader.state == "failed_same_path_mixed_generation"
    assert preloader.phase == "verify_product"
    _assert_no_mutation(calls)


@pytest.mark.parametrize(
    ("enabled", "helper_attached", "accepted_frames", "active_session_id"),
    (
        (False, False, 0, ""),
        (True, True, 0, ""),
        (True, False, 1, ""),
        (True, False, 0, "active-session"),
    ),
)
def test_auto_repair_requires_default_gate_and_empty_current_launch(
    tmp_path: Path,
    enabled: bool,
    helper_attached: bool,
    accepted_frames: int,
    active_session_id: str,
) -> None:
    current_driver = tmp_path / "WinDivert64.sys"
    current_driver.write_bytes(b"current")
    preloader = _conflicted_preloader()

    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=enabled,
        helper_attached=helper_attached,
        accepted_frames=accepted_frames,
        active_session_id=active_session_id,
        repairer_factory=lambda: pytest.fail("repair must not be constructed"),
    )

    assert outcome.repair_state == "not_started"
    assert preloader.state == "failed_service_conflict"


@pytest.mark.parametrize("unsafe_kind", ("running", "access_denied", "timeout", "malformed"))
def test_auto_repair_keeps_unsafe_or_unknown_service_zero_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_kind: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]):
        calls.append(command)
        if unsafe_kind == "timeout":
            raise subprocess.TimeoutExpired(command, 10)
        if unsafe_kind == "access_denied":
            return _sc_result(5, "[SC] OpenService FAILED 5")
        if unsafe_kind == "malformed":
            return _sc_result(0, "SERVICE_NAME: BidKingWinDivertR1")
        return _service_status(4)

    preloader = _conflicted_preloader(runner=runner)
    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=lambda _path: pytest.fail("inventory must not run"),
        ),
    )

    assert outcome.preload_state == "failed_service_conflict"
    _assert_no_mutation(calls)


def test_auto_repair_rejects_legacy_service_name_without_scm_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "WinDivert64.sys"
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []
    preloader = _conflicted_preloader()

    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=lambda command: calls.append(command),
            inventory_reader=lambda _path: pytest.fail("inventory must not run"),
            service_name="WinDivert",
        ),
    )

    assert outcome.repair_state == "failed_service_conflict"
    assert calls == []


def test_auto_repair_toctou_change_never_deletes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    reads = 0

    def runner(command: list[str]):
        nonlocal reads
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            reads += 1
            return _service_config(str(old_driver))
        raise AssertionError(command)

    def inventory_reader(_path: str):
        return _trusted_identity(old_driver, epoch=reads)

    preloader = _conflicted_preloader(runner=runner)
    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=inventory_reader,
        ),
    )

    assert outcome.repair_state == "failed_service_conflict"
    _assert_no_mutation(calls)


def test_service_disappearing_before_delete_is_race_not_same_launch_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "WinDivert64.sys"
    current_driver.write_bytes(b"current")
    calls: list[list[str]] = []

    def runner(command: list[str]):
        calls.append(command)
        return _sc_result(1060, "[SC] OpenService FAILED 1060")

    preloader = _conflicted_preloader(runner=runner)
    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=lambda _path: pytest.fail("inventory must not run"),
        ),
    )

    assert outcome.preload_state == "failed_service_conflict"
    assert outcome.repair_state == "not_present"
    assert calls == [["sc.exe", "query", SERVICE_NAME]]
    _assert_no_mutation(calls)


def test_readback_timeout_after_delete_stops_launch_for_reboot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    deleted = False

    def runner(command: list[str]):
        nonlocal deleted
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            if deleted:
                raise subprocess.TimeoutExpired(command, 10)
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        if command == ["sc.exe", "delete", SERVICE_NAME]:
            deleted = True
            return _sc_result(0, "[SC] DeleteService SUCCESS")
        raise AssertionError(command)

    preloader = _conflicted_preloader(runner=runner)
    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        preloader,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=lambda _path: _trusted_identity(old_driver),
        ),
    )

    assert outcome.preload_state == "repaired_restart_required"
    assert outcome.repair_state == "failed_unclassified"
    assert calls.count(["sc.exe", "delete", SERVICE_NAME]) == 1


def test_nested_auto_repair_is_busy_and_outer_delete_remains_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    deleted = False
    nested_outcomes: list[wss_monitor._WinDivertAutoRepairOutcome] = []

    def repairer_factory():
        return _repairer(
            runner=runner,
            inventory_reader=lambda _path: _trusted_identity(old_driver),
        )

    def runner(command: list[str]):
        nonlocal deleted
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            if deleted:
                nested_outcomes.append(
                    wss_monitor._attempt_automatic_stale_product_service_repair(
                        _conflicted_preloader(runner=runner),
                        current_driver,
                        enabled=True,
                        helper_attached=False,
                        accepted_frames=0,
                        active_session_id="",
                        repairer_factory=repairer_factory,
                    )
                )
                return _sc_result(1060, "[SC] OpenService FAILED 1060")
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        if command == ["sc.exe", "delete", SERVICE_NAME]:
            deleted = True
            return _sc_result(0, "[SC] DeleteService SUCCESS")
        raise AssertionError(command)

    outer = wss_monitor._attempt_automatic_stale_product_service_repair(
        _conflicted_preloader(runner=runner),
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=repairer_factory,
    )

    assert outer.preload_state == "repaired_restart_required"
    assert nested_outcomes == [
        wss_monitor._WinDivertAutoRepairOutcome(
            preload_state="failed_service_conflict",
            repair_state="failed_repair_busy",
            repair_phase="not_started",
        )
    ]
    assert calls.count(["sc.exe", "delete", SERVICE_NAME]) == 1


def test_delete_pending_requires_reboot_and_next_launch_does_not_repair_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    current_driver = tmp_path / "current" / "WinDivert64.sys"
    current_driver.parent.mkdir()
    current_driver.write_bytes(b"current")
    old_driver = tmp_path / "old" / "WinDivert64.sys"
    calls: list[list[str]] = []
    delete_pending = False

    def runner(command: list[str]):
        nonlocal delete_pending
        calls.append(command)
        if command == ["sc.exe", "query", SERVICE_NAME]:
            if delete_pending:
                return _sc_result(1072, "[SC] OpenService FAILED 1072")
            return _service_status(1)
        if command == ["sc.exe", "qc", SERVICE_NAME]:
            return _service_config(str(old_driver))
        if command == ["sc.exe", "delete", SERVICE_NAME]:
            delete_pending = True
            return _sc_result(0, "[SC] DeleteService SUCCESS")
        raise AssertionError(command)

    first = _conflicted_preloader(runner=runner)
    outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        first,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: _repairer(
            runner=runner,
            inventory_reader=lambda _path: _trusted_identity(old_driver),
        ),
    )
    assert outcome.preload_state == "repaired_restart_required"
    assert outcome.repair_state == "failed_marked_for_delete"
    assert calls.count(["sc.exe", "delete", SERVICE_NAME]) == 1

    # Simulate the post-reboot second launch: the legacy service is absent, so
    # preload is already safe and the automatic repair path must stay dormant.
    second_calls: list[list[str]] = []
    second = wss_monitor.WinDivertCompatibilityPreloader(
        runner=lambda command: (
            second_calls.append(command)
            or _sc_result(1060, "[SC] OpenService FAILED 1060")
        ),
        lock_factory=lambda: _lock(),
    )
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: wss_monitor.KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    assert second.prepare(current_driver) == "not_needed"
    second_outcome = wss_monitor._attempt_automatic_stale_product_service_repair(
        second,
        current_driver,
        enabled=True,
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
        repairer_factory=lambda: pytest.fail("second launch must not repair"),
    )
    assert second_outcome.repair_state == "not_started"
    assert second_calls == [["sc.exe", "query", "WinDivert"]]


def test_helper_sidecar_allows_only_fixed_reboot_state(tmp_path: Path) -> None:
    wss_monitor._write_wss_helper_status(
        tmp_path,
        phase="helper_start_failed",
        helper_pid=0,
        launch_id="launch-current",
        diagnostics=wss_monitor.WssHelperDiagnostics(),
        windivert_preload_state="repaired_restart_required",
        windivert_preload_phase="complete",
        monitor_elevation_state="elevated",
    )

    payload = json.loads(
        (tmp_path / wss_monitor.WSS_HELPER_STATUS_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert payload["windivert_preload_state"] == "repaired_restart_required"
    assert payload["windivert_preload_phase"] == "complete"
    assert "path" not in json.dumps(payload).casefold()


def test_main_stops_current_launch_before_redirector_when_reboot_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_dir = tmp_path / "logs"
    confdir = tmp_path / "conf"
    driver = tmp_path / "WinDivert64.sys"
    mitmdump = tmp_path / "mitmdump.exe"
    driver.write_bytes(b"driver")
    mitmdump.write_bytes(b"helper")
    events: list[str] = []

    class FakeMonitor:
        def start(self) -> None:
            events.append("monitor.start")

        def stop(self) -> None:
            events.append("monitor.stop")

    preloader = _conflicted_preloader()

    def attempt(preloader_arg, _driver_path, **_kwargs):
        assert preloader_arg is preloader
        preloader.state = "repaired_restart_required"
        preloader.phase = "complete"
        return wss_monitor._WinDivertAutoRepairOutcome(
            preload_state="repaired_restart_required",
            repair_state="not_present",
            repair_phase="complete",
        )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_wss_live_monitor.py",
            "--log-dir",
            str(log_dir),
            "--confdir",
            str(confdir),
            "--no-lock",
            "--allow-running-game",
        ],
    )
    monkeypatch.setattr(wss_monitor, "resolve_mitmdump", lambda *_a, **_k: mitmdump)
    monkeypatch.setattr(wss_monitor, "ca_is_trusted_for_current_user", lambda _p: True)
    monkeypatch.setattr(wss_monitor, "_game_process_ids", lambda _name: [])
    monkeypatch.setattr(wss_monitor, "load_monitor_tables", lambda: object())
    monkeypatch.setattr(
        wss_monitor,
        "FatbeansWebhookMonitor",
        lambda **_kwargs: FakeMonitor(),
    )
    monkeypatch.setattr(
        wss_monitor,
        "_start_version_and_activation_threads",
        lambda _log_dir: None,
    )
    monkeypatch.setattr(
        wss_monitor,
        "build_mitmdump_command",
        lambda **_kwargs: ["mitmdump.exe"],
    )
    monkeypatch.setattr(
        wss_monitor,
        "WinDivertCompatibilityPreloader",
        lambda: preloader,
    )
    monkeypatch.setattr(
        wss_monitor,
        "_bundled_windivert_driver",
        lambda _path: driver,
    )
    monkeypatch.setattr(
        wss_monitor,
        "_attempt_automatic_stale_product_service_repair",
        attempt,
    )
    monkeypatch.setattr(wss_monitor, "_windows_elevation_state", lambda: "elevated")
    monkeypatch.setattr(
        wss_monitor.subprocess,
        "Popen",
        lambda *_a, **_k: pytest.fail("redirector helper must not start"),
    )

    assert wss_monitor.main() == 6
    assert events == ["monitor.start", "monitor.stop"]
    payload = json.loads(
        (log_dir / wss_monitor.WSS_HELPER_STATUS_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert payload["helper_pid"] == 0
    assert payload["windivert_preload_state"] == "repaired_restart_required"
    captured = capsys.readouterr()
    assert "restart Windows" in captured.err
    assert "new ASCII-only directory" in captured.err
