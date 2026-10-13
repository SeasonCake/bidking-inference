from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
DIAGNOSTICS = ROOT / "scripts" / "collect_diagnostics.ps1"


def _extract_ps_function(source: str, name: str) -> str:
    match = re.search(rf"(?m)^function {re.escape(name)}\b", source)
    assert match is not None
    start = match.start()
    brace_at = source.find("{", match.end())
    assert brace_at >= 0
    depth = 0
    for index in range(brace_at, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated PowerShell function: {name}")


def _powershell() -> str:
    executable = shutil.which("powershell.exe")
    if not executable:
        pytest.skip("Windows PowerShell unavailable")
    return executable


def _run_collection_script(package_root: Path) -> str:
    scripts = package_root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    copied = scripts / "collect_diagnostics.ps1"
    local_appdata = package_root / "local-appdata"
    local_appdata.mkdir(parents=True, exist_ok=True)
    documents = package_root.parent / f"{package_root.name}-documents"
    documents.mkdir(parents=True, exist_ok=True)
    source = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    begin = "# BIDKING_TEST_KNOWN_FOLDER_BEGIN"
    end = "# BIDKING_TEST_KNOWN_FOLDER_END"
    assert source.count(begin) == 1 and source.count(end) == 1

    def _ps(value: Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    injected = "\n".join(
        (
            begin,
            "function Get-BidKingKnownFolderPath($kind) {",
            f"  if ([string]$kind -ceq 'LocalApplicationData') {{ return {_ps(local_appdata)} }}",
            f"  if ([string]$kind -ceq 'Documents') {{ return {_ps(documents)} }}",
            "  throw 'unknown test Known Folder kind'",
            "}",
            end,
        )
    )
    start = source.index(begin)
    finish = source.index(end, start) + len(end)
    copied.write_text(source[:start] + injected + source[finish:], encoding="utf-8-sig")
    copied_arg = str(copied).replace("'", "''")
    command = (
        "$env:BIDKING_DIAG_NOEXPLORER='1';"
        "$env:BIDKING_DIAG_NONINTERACTIVE='1';"
        "$env:BIDKING_DIAG_SKIP_NETWORK='1';"
        f"& '{copied_arg}'"
    )
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    reports = list((documents / "BidKingLive" / "exports").glob("BidKing诊断_*.txt"))
    assert len(reports) == 1
    assert not list(package_root.glob("BidKing诊断_*.txt"))
    return reports[0].read_text(encoding="utf-8-sig")


def test_launcher_records_overlay_launch_identity_and_fixed_exit_classes() -> None:
    script = LAUNCHER.read_text(encoding="utf-8-sig")

    for marker in (
        '$OverlayLogSession = Join-Path $LogPath "overlay_log_session.json"',
        "launch_id = $LaunchId",
        "overlay_started_at = $OverlayStartedAt",
        "overlay_pid = $OverlayProcessId",
        "stderr_file = [System.IO.Path]::GetFileName($OverlayErr)",
        'runtime_status_file = "hero_ref_ui_runtime_status.json"',
        "$OverlayLogState.exit_code = [int]$ExitCode",
        "overlay_guard launch_id={1}",
    ):
        assert marker in script

    classify = _extract_ps_function(script, "Get-OverlayExitClass")
    probe = (
        classify
        + "\n@("
        + "\n  (Get-OverlayExitClass 'alive' 'alive' $null),"
        + "\n  (Get-OverlayExitClass 'alive' 'dead' 0),"
        + "\n  (Get-OverlayExitClass 'alive' 'dead' 1),"
        + "\n  (Get-OverlayExitClass 'dead' 'dead' 0),"
        + "\n  (Get-OverlayExitClass 'unknown' 'alive' $null)"
        + "\n) | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1]) == [
        "running",
        "overlay_exited_cleanly",
        "overlay_exception",
        "monitor_exit",
        "process_query_unknown",
    ]


def test_launcher_fatal_guidance_points_to_unified_support_export_not_raw_logs() -> None:
    script = LAUNCHER.read_text(encoding="utf-8-sig")

    assert "文档\\BidKingLive\\exports" in script
    assert "不要发送原始日志" in script
    assert "把 data\\logs\\live\\monitor.stderr.log" not in script
    assert "$MonTail" not in script
    assert "$OvTail" not in script


def test_public_diagnostics_projects_current_overlay_evidence_without_raw_content(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    started_at = time.time() - 5
    (log_dir / "startup_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": started_at,
                "launcher_elevation_state": "not_elevated",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": started_at,
                "monitor_pid": 1234,
                "reused_monitor": False,
                "stdout_file": "monitor.stdout.log",
                "stderr_file": "monitor.stderr.log",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "overlay_log_session.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "launch_id": launch_id,
                "started_at": started_at,
                "overlay_started_at": started_at + 1,
                "overlay_pid": 5678,
                "guard_state": "dead",
                "exit_class": "monitor_exit",
                "exit_code": 0,
                "stderr_file": "overlay.stderr.log",
                "runtime_status_file": "hero_ref_ui_runtime_status.json",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "monitor.stdout.log").write_text("", encoding="utf-8")
    (log_dir / "monitor.stderr.log").write_text("", encoding="utf-8")
    private_path = r"C:\Users\example\PRIVATE_OVERLAY_PATH"
    (log_dir / "overlay.stderr.log").write_text(
        f"Traceback (most recent call last):\n  File {private_path}\n"
        "token=PRIVATE_OVERLAY_TOKEN\n",
        encoding="utf-8",
    )
    (log_dir / "hero_ref_ui_runtime_status.json").write_text(
        json.dumps(
            {
                "logged_at": time.time(),
                "event": "watched_monitor_exit",
                "snapshot_path": private_path,
                "error": "PRIVATE_OVERLAY_EXCEPTION",
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert (
        "overlay_launch: guard=dead exit_class=monitor_exit exit_code=0 "
        "stderr_class=python_traceback runtime_event=watched_monitor_exit"
        in report
    )
    for canary in (
        "PrivateUser",
        "PRIVATE_OVERLAY_PATH",
        "PRIVATE_OVERLAY_TOKEN",
        "PRIVATE_OVERLAY_EXCEPTION",
    ):
        assert canary not in report


def test_public_diagnostics_rejects_unattributed_or_malformed_overlay_state(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    started_at = time.time() - 5
    (log_dir / "startup_session.json").write_text(
        json.dumps(
            {
                "launch_id": "11111111-2222-4333-8444-555555555555",
                "started_at": started_at,
                "launcher_elevation_state": "unknown",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "overlay_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                "started_at": started_at,
                "guard_state": "PRIVATE_GUARD",
                "exit_class": "PRIVATE_EXIT_CLASS",
                "stderr_file": "overlay.stderr.log",
                "runtime_status_file": "hero_ref_ui_runtime_status.json",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "overlay.stderr.log").write_text(
        "PRIVATE_HISTORICAL_OVERLAY_ERROR",
        encoding="utf-8",
    )
    (log_dir / "hero_ref_ui_runtime_status.json").write_text(
        json.dumps({"event": "PRIVATE_RUNTIME_EVENT"}),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert "overlay_launch: historical_or_unattributed（不作为本次启动证据）" in report
    for canary in (
        "PRIVATE_GUARD",
        "PRIVATE_EXIT_CLASS",
        "PRIVATE_HISTORICAL_OVERLAY_ERROR",
        "PRIVATE_RUNTIME_EVENT",
    ):
        assert canary not in report


@pytest.mark.parametrize(
    ("stderr_file", "runtime_status_file"),
    [
        ("hero_ref_ui_runtime_status.json", "overlay.stderr.log"),
        ("overlay.stderr.log", "overlay.stderr.log"),
    ],
)
def test_public_diagnostics_rejects_swapped_or_duplicate_overlay_roles(
    tmp_path: Path,
    stderr_file: str,
    runtime_status_file: str,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    started_at = time.time() - 5
    (log_dir / "startup_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": started_at,
                "launcher_elevation_state": "not_elevated",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "overlay_log_session.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "launch_id": launch_id,
                "started_at": started_at,
                "overlay_started_at": started_at + 1,
                "overlay_pid": 5678,
                "guard_state": "alive",
                "exit_class": "running",
                "exit_code": None,
                "stderr_file": stderr_file,
                "runtime_status_file": runtime_status_file,
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "overlay.stderr.log").write_text(
        "PRIVATE_WRONG_ROLE_OVERLAY_ERROR",
        encoding="utf-8",
    )
    (log_dir / "hero_ref_ui_runtime_status.json").write_text(
        json.dumps(
            {
                "logged_at": time.time(),
                "event": "overlay_started",
                "error": "PRIVATE_WRONG_ROLE_RUNTIME_ERROR",
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert (
        "overlay_launch: guard=alive exit_class=running exit_code=0 "
        "stderr_class=unavailable runtime_event=unavailable"
        in report
    )
    assert "PRIVATE_WRONG_ROLE_OVERLAY_ERROR" not in report
    assert "PRIVATE_WRONG_ROLE_RUNTIME_ERROR" not in report
