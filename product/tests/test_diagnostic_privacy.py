from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from bidking_lab.live import fatbeans
from bidking_lab.secure.diagnostic_redaction import (
    collect_player_aliases,
    redact_document,
    redact_payload,
    redact_text,
)


ROOT = Path(__file__).resolve().parents[1]


def _diagnostic_recheck_sequence(
    at: int,
    *,
    recovered_at: int | None,
) -> dict[str, object]:
    return {
        "started_at": at,
        "last_failure_at": at,
        "recovered_at": recovered_at,
        "attempt_count": 1,
        "attempts": [
            {
                "at": at,
                "endpoint": "endpoint_1",
                "stage": "tls",
                "failure_class": "tls_connection_reset",
            }
        ],
    }


def _write_diagnostic_recheck_history(
    path: Path,
    *,
    now: int,
    sequences: list[dict[str, object]],
) -> None:
    path.write_text(
        json.dumps({"schema": 1, "updated_at": now, "sequences": sequences}),
        encoding="utf-8",
    )


def _settlement_frame_with_private_body() -> fatbeans.FatbeansFrame:
    private_body = b"\x0a\x0ePRIVATE_PLAYER"
    header = bytearray(16)
    header[12:16] = (0x002D).to_bytes(4, "big")
    return fatbeans.FatbeansFrame(
        index=0,
        direction="REV",
        sort_id=7,
        capture_time="2026-07-20 00:00:00.000",
        raw=bytes(header) + private_body,
    )


def _instrument_collection_known_folders(
    script: str,
    *,
    local_appdata: Path,
    documents: Path,
) -> str:
    begin = "# BIDKING_TEST_KNOWN_FOLDER_BEGIN"
    end = "# BIDKING_TEST_KNOWN_FOLDER_END"
    assert script.count(begin) == 1 and script.count(end) == 1

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
    start = script.index(begin)
    finish = script.index(end, start) + len(end)
    return script[:start] + injected + script[finish:]


def _probe_collection_log_child_chain(
    root: Path,
    *,
    powershell_executable: str,
    parts: tuple[str, ...] = ("BidKingLive", "logs", "live"),
) -> subprocess.CompletedProcess[str]:
    def _ps(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    functions = (
        "Assert-BidKingPlainDirectory",
        "Test-BidKingContainedPath",
        "Assert-BidKingExistingChildChain",
    )
    function_names = ",".join(_ps(name) for name in functions)
    part_values = ",".join(_ps(part) for part in parts)
    script_path = ROOT / "scripts" / "collect_diagnostics.ps1"
    command = (
        "$tokens=$null;$errors=$null;"
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile({_ps(script_path)},"
        "[ref]$tokens,[ref]$errors);"
        "if($errors.Count){throw ($errors|Out-String)};"
        f"foreach($name in @({function_names})){{"
        "$fn=$ast.Find({param($node)"
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq $name},$true);"
        "if(-not $fn){throw ('missing function '+$name)};"
        "Invoke-Expression $fn.Extent.Text};"
        f"$root={_ps(root)};"
        f"$actual=Assert-BidKingExistingChildChain $root @({part_values}) 'live log path';"
        "[pscustomobject]@{actual=$actual;contained=(Test-BidKingContainedPath $actual $root)}"
        "|ConvertTo-Json -Compress"
    )
    return subprocess.run(
        [
            str(Path(powershell_executable).resolve()),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )


def _collection_log_probe_payload(
    completed: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    assert completed.returncode == 0, completed.stderr
    json_lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    assert len(json_lines) == 1, completed.stdout
    payload = json.loads(json_lines[0])
    assert isinstance(payload, dict)
    return payload


def _run_collection_script(
    package_root: Path,
    *,
    local_appdata: Path | None = None,
    powershell_executable: str | None = None,
) -> str:
    completed, report_path = _run_collection_process(
        package_root,
        local_appdata=local_appdata,
        powershell_executable=powershell_executable,
    )
    assert completed.returncode == 0, completed.stderr
    return report_path.read_text(encoding="utf-8-sig")


def _run_collection_process(
    package_root: Path,
    *,
    local_appdata: Path | None = None,
    powershell_executable: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    powershell = powershell_executable or shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell unavailable")
    scripts = package_root / "scripts"
    scripts.mkdir(exist_ok=True)
    copied = scripts / "collect_diagnostics.ps1"
    local_appdata = local_appdata or (package_root / "local-appdata")
    local_appdata.mkdir(parents=True, exist_ok=True)
    documents = package_root.parent / f"{package_root.name}-documents"
    documents.mkdir(parents=True, exist_ok=True)
    source = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")
    copied.write_text(
        _instrument_collection_known_folders(
            source,
            local_appdata=local_appdata,
            documents=documents,
        ),
        encoding="utf-8-sig",
    )
    copied_arg = str(copied).replace("'", "''")
    command = (
        "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false);"
        "$OutputEncoding=[Console]::OutputEncoding;"
        "$env:BIDKING_DIAG_NOEXPLORER='1';"
        "$env:BIDKING_DIAG_NONINTERACTIVE='1';"
        "$env:BIDKING_DIAG_SKIP_NETWORK='1';"
        f"& '{copied_arg}'"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    reports = list((documents / "BidKingLive" / "exports").glob("BidKing诊断_*.txt"))
    assert len(reports) == 1
    assert not list(package_root.glob("BidKing诊断_*.txt"))
    return completed, reports[0]


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_collect_diagnostics_emits_fixed_privacy_safe_progress_in_real_shells(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    package_root = tmp_path / f"package {shell_name}"
    package_root.mkdir()

    completed, report_path = _run_collection_process(
        package_root,
        powershell_executable=str(Path(shell).resolve()),
    )

    assert completed.returncode == 0, completed.stderr
    expected = [f"[进度 {index}/7]" for index in range(1, 8)]
    positions = [completed.stdout.index(marker) for marker in expected]
    assert positions == sorted(positions)
    assert completed.stdout.count("[进度 ") == 7
    assert "[完成] 诊断报告已生成" in completed.stdout
    assert "[失败] 诊断收集未完成" not in completed.stdout
    assert str(report_path) in completed.stdout


def test_collect_diagnostics_bat_reports_start_documents_and_preserves_success_code(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    cmd = shutil.which("cmd.exe")
    if not powershell or not cmd:
        pytest.skip("Windows command hosts unavailable")
    package_root = tmp_path / "package with spaces"
    tool_dir = package_root / "诊断工具"
    scripts = package_root / "scripts"
    tool_dir.mkdir(parents=True)
    scripts.mkdir()
    local_appdata = tmp_path / "local-appdata"
    documents = tmp_path / "documents"
    local_appdata.mkdir()
    documents.mkdir()
    (tool_dir / "收集诊断.bat").write_bytes(
        (ROOT / "scripts" / "collect_diagnostics.bat").read_bytes()
    )
    source = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8-sig"
    )
    (scripts / "collect_diagnostics.ps1").write_text(
        _instrument_collection_known_folders(
            source,
            local_appdata=local_appdata,
            documents=documents,
        ),
        encoding="utf-8-sig",
    )
    env = {
        **dict(os.environ),
        "BIDKING_DIAG_NOEXPLORER": "1",
        "BIDKING_DIAG_SKIP_NETWORK": "1",
    }

    completed = subprocess.run(
        [cmd, "/d", "/c", str(tool_dir / "收集诊断.bat")],
        input="\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "[START] BidKing diagnostics" in completed.stdout
    assert completed.stdout.index("[START]") < completed.stdout.index("[进度 1/7]")
    assert "Windows Documents\\BidKingLive\\exports" in completed.stdout
    assert "package root" not in completed.stdout
    assert completed.stdout.count("Press any key to continue") == 1
    assert len(list((documents / "BidKingLive" / "exports").glob("BidKing诊断_*.txt"))) == 1


def test_collect_diagnostics_bat_missing_script_fails_after_visible_start(
    tmp_path: Path,
) -> None:
    cmd = shutil.which("cmd.exe")
    if not cmd:
        pytest.skip("cmd.exe unavailable")
    tool_dir = tmp_path / "package" / "诊断工具"
    tool_dir.mkdir(parents=True)
    bat = tool_dir / "收集诊断.bat"
    bat.write_bytes((ROOT / "scripts" / "collect_diagnostics.bat").read_bytes())

    completed = subprocess.run(
        [cmd, "/d", "/c", str(bat)],
        input="\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )

    assert completed.returncode == 1
    assert "[START] BidKing diagnostics" in completed.stdout
    assert "[ERROR] collect_diagnostics.ps1 not found." in completed.stdout
    assert completed.stdout.count("Press any key to continue") == 1


def test_collect_diagnostics_bat_propagates_powershell_failure_code(
    tmp_path: Path,
) -> None:
    cmd = shutil.which("cmd.exe")
    if not cmd:
        pytest.skip("cmd.exe unavailable")
    tool_dir = tmp_path / "package" / "诊断工具"
    tool_dir.mkdir(parents=True)
    bat = tool_dir / "收集诊断.bat"
    bat.write_bytes((ROOT / "scripts" / "collect_diagnostics.bat").read_bytes())
    (tool_dir / "collect_diagnostics.ps1").write_text("exit 7\n", encoding="utf-8")

    completed = subprocess.run(
        [cmd, "/d", "/c", str(bat)],
        input="\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )

    assert completed.returncode == 7
    assert "[START] BidKing diagnostics" in completed.stdout
    assert "Failed with code 7." in completed.stdout
    assert completed.stdout.count("Press any key to continue") == 1


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_collect_diagnostics_log_chain_keeps_exact_terminal_at_every_missing_depth(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    parts = ("BidKingLive", "logs", "live")
    for present_depth in range(len(parts) + 1):
        root = tmp_path / f"重定向 LocalAppData {present_depth}"
        root.mkdir()
        current = root
        for part in parts[:present_depth]:
            current /= part
            current.mkdir()
        payload = _collection_log_probe_payload(
            _probe_collection_log_child_chain(
                root,
                powershell_executable=shell,
            )
        )
        expected = root.joinpath(*parts).resolve()
        assert Path(str(payload["actual"])).resolve() == expected
        assert payload["contained"] is True


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_collect_diagnostics_log_chain_stays_exact_when_tree_appears_after_resolution(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    root = tmp_path / "race LocalAppData"
    root.mkdir()
    payload = _collection_log_probe_payload(
        _probe_collection_log_child_chain(root, powershell_executable=shell)
    )
    resolved = Path(str(payload["actual"]))
    exact_live = root / "BidKingLive" / "logs" / "live"
    exact_live.mkdir(parents=True)
    marker = exact_live / "capture_source_status.json"
    marker.write_text('{"source":"race-exact-live"}', encoding="utf-8")

    assert resolved.resolve() == exact_live.resolve()
    assert (resolved / marker.name).read_text(encoding="utf-8") == marker.read_text(
        encoding="utf-8"
    )
    assert not (root / marker.name).exists()
    assert not (root / "BidKingLive" / marker.name).exists()
    assert not (root / "BidKingLive" / "logs" / marker.name).exists()


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_collect_diagnostics_log_chain_rejects_non_directory_reparse_and_outside(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    parts = ("BidKingLive", "logs", "live")

    for invalid_depth in range(len(parts)):
        root = tmp_path / f"file-root-{invalid_depth}"
        root.mkdir()
        current = root
        for part in parts[:invalid_depth]:
            current /= part
            current.mkdir()
        (current / parts[invalid_depth]).write_text("not-a-directory", encoding="utf-8")
        completed = _probe_collection_log_child_chain(root, powershell_executable=shell)
        assert completed.returncode != 0

    for invalid_depth in range(-1, len(parts)):
        reparse_parent = tmp_path / f"reparse-parent-{invalid_depth}"
        reparse_parent.mkdir()
        reparse_target = tmp_path / f"foreign-prefix-target-{invalid_depth}"
        reparse_target.mkdir()
        if invalid_depth < 0:
            reparse_root = reparse_parent / "LocalAppData"
            junction = reparse_root
        else:
            reparse_root = reparse_parent / "LocalAppData"
            reparse_root.mkdir()
            current = reparse_root
            for part in parts[:invalid_depth]:
                current /= part
                current.mkdir()
            junction = current / parts[invalid_depth]
        junction_result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(reparse_target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        assert junction_result.returncode == 0, junction_result.stderr
        assert junction.exists()
        completed = _probe_collection_log_child_chain(
            reparse_root,
            powershell_executable=shell,
        )
        assert completed.returncode != 0

    outside_root = tmp_path / "outside-root"
    outside_root.mkdir()
    prefix_sibling = tmp_path / "outside-root-foreign"
    prefix_sibling.mkdir()
    completed = _probe_collection_log_child_chain(
        outside_root,
        powershell_executable=shell,
        parts=("..", prefix_sibling.name, "live"),
    )
    assert completed.returncode != 0


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_collect_diagnostics_reads_external_live_logs_and_writes_documents_exports(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    package_root = tmp_path / "package"
    package_root.mkdir()
    local_appdata = tmp_path / "external-local-appdata"
    live = local_appdata / "BidKingLive" / "logs" / "live"
    live.mkdir(parents=True)
    (live / "capture_source_status.json").write_text(
        json.dumps(
            {
                "source": "wss_local",
                "active_session_id": "support-safe",
                "accepted_frames": 17,
                "ignored_frames": 2,
                "dropped_bytes": 0,
            }
        ),
        encoding="utf-8",
    )
    stale_package_live = package_root / "data" / "logs" / "live"
    stale_package_live.mkdir(parents=True)
    (stale_package_live / "capture_source_status.json").write_text(
        '{"source":"wrong-package-log","accepted_frames":99991}',
        encoding="utf-8",
    )

    report = _run_collection_script(
        package_root,
        local_appdata=local_appdata,
        powershell_executable=str(Path(shell).resolve()),
    )

    assert "source=wss_local" in report
    assert "accepted_frames=17" in report
    assert "wrong-package-log" not in report
    assert "99991" not in report
    documents = tmp_path / "package-documents" / "BidKingLive" / "exports"
    assert len(list(documents.glob("BidKing诊断_*.txt"))) == 1
    assert not list(package_root.glob("BidKing诊断_*.txt"))


def test_collect_diagnostics_path_authorities_have_no_env_cwd_or_desktop_fallback() -> None:
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert 'Get-BidKingKnownFolderPath "LocalApplicationData"' in script
    assert 'Get-BidKingKnownFolderPath "Documents"' in script
    assert '@("BidKingLive", "logs", "live")' in script
    assert '"BidKingLive" "diagnostic output"' in script
    assert '"exports" "diagnostic output"' in script
    assert script.count("$env:BIDKING_PACKAGE_ROOT") == 1
    assert "Resolve-BidKingDiagnosticPackageRoot" in script
    assert '"BUILD_MANIFEST.txt"' in script
    assert 'GetFolderPath("Desktop")' not in script
    assert 'Join-Path $Repo "data\\logs\\live"' not in script
    assert "回退桌面" not in script


def test_current_troubleshooting_docs_use_live_and_export_path_authorities() -> None:
    troubleshooting_docs = (
        ROOT / "docs" / "TROUBLESHOOTING_group_friends.zh-CN.md",
        ROOT / "docs" / "TROUBLESHOOTING_packaged.zh-CN.md",
    )

    for path in troubleshooting_docs:
        text = path.read_text(encoding="utf-8")
        normalized = text.replace("/", "\\")
        assert "data\\logs\\live" not in normalized.casefold(), path
        assert "文档\\BidKingLive\\exports" in normalized, path

    group_friends = troubleshooting_docs[0].read_text(encoding="utf-8")
    assert "%LOCALAPPDATA%\\BidKingLive\\logs\\live" in group_friends


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_collect_diagnostics_missing_documents_known_folder_fails_without_fallback(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    package_root = tmp_path / "package"
    scripts = package_root / "scripts"
    scripts.mkdir(parents=True)
    local_appdata = tmp_path / "local-appdata"
    local_appdata.mkdir()
    missing_documents = tmp_path / "missing-documents"
    source = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8-sig"
    )
    copied = scripts / "collect_diagnostics.ps1"
    copied.write_text(
        _instrument_collection_known_folders(
            source,
            local_appdata=local_appdata,
            documents=missing_documents,
        ),
        encoding="utf-8-sig",
    )
    copied_arg = str(copied).replace("'", "''")
    completed = subprocess.run(
        [
            str(Path(shell).resolve()),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false);"
                "$OutputEncoding=[Console]::OutputEncoding;"
                "$env:BIDKING_DIAG_NOEXPLORER='1';"
                "$env:BIDKING_DIAG_NONINTERACTIVE='1';"
                "$env:BIDKING_DIAG_SKIP_NETWORK='1';"
                f"& '{copied_arg}'"
            ),
        ],
        cwd=package_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )

    assert completed.returncode != 0
    assert "[进度 1/7]" in completed.stdout
    assert "[失败] 诊断收集未完成" in completed.stdout
    assert "[完成]" not in completed.stdout
    assert str(missing_documents) not in completed.stdout + completed.stderr
    assert not missing_documents.exists()
    assert not list(package_root.glob("BidKing诊断_*.txt"))
    assert not (local_appdata / "BidKingLive" / "exports").exists()


def test_collect_diagnostics_keeps_only_safe_recent_activation_rechecks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    local_appdata = tmp_path / "local-appdata"
    history_root = local_appdata / "bidking"
    history_root.mkdir(parents=True)
    now = int(time.time())
    safe_history = {
        "schema": 1,
        "updated_at": now,
        "sequences": [
            {
                "started_at": now - 4,
                "last_failure_at": now - 2,
                "recovered_at": now - 1,
                "attempt_count": 2,
                "attempts": [
                    {
                        "at": now - 4,
                        "endpoint": "endpoint_1",
                        "stage": "tls",
                        "failure_class": "tls_connection_reset",
                    },
                    {
                        "at": now - 2,
                        "endpoint": "endpoint_2",
                        "stage": "tcp",
                        "failure_class": "connect_timeout",
                    },
                ],
            }
        ],
    }
    (history_root / "activation_recheck_history_42.json").write_text(
        json.dumps(safe_history),
        encoding="utf-8",
    )
    (history_root / "activation_recheck_history_43.json").write_text(
        json.dumps(
            {
                **safe_history,
                "private_token": "PRIVATE_TOKEN",
                "url": "https://private.example/198.51.100.91",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("BIDKING_PACKAGE_ROOT", str(package_root))

    report = _run_collection_script(package_root, local_appdata=local_appdata)

    assert "最近在线复核摘要: 1 段" in report
    assert "endpoint=endpoint_1 stage=tls failure_class=tls_connection_reset" in report
    assert "endpoint=endpoint_2 stage=tcp failure_class=connect_timeout" in report
    for forbidden in (
        "PRIVATE_TOKEN",
        "private.example",
        "198.51.100.91",
        "activation_recheck_history_43",
    ):
        assert forbidden not in report


def test_collect_diagnostics_rejects_recheck_stage_failure_mismatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    local_appdata = tmp_path / "local-appdata"
    history_root = local_appdata / "bidking"
    history_root.mkdir(parents=True)
    now = int(time.time())
    damaged_history = {
        "schema": 1,
        "updated_at": now,
        "sequences": [
            {
                "started_at": now - 2,
                "last_failure_at": now - 1,
                "recovered_at": None,
                "attempt_count": 1,
                "attempts": [
                    {
                        "at": now - 1,
                        "endpoint": "endpoint_1",
                        "stage": "tls",
                        "failure_class": "connect_timeout",
                    }
                ],
            }
        ],
    }
    (history_root / "activation_recheck_history_42.json").write_text(
        json.dumps(damaged_history),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("BIDKING_PACKAGE_ROOT", str(package_root))

    report = _run_collection_script(package_root, local_appdata=local_appdata)

    assert "最近在线复核摘要: 无" in report
    assert "failure_class=connect_timeout" not in report


def test_collect_diagnostics_rejects_impossible_recheck_timeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    local_appdata = tmp_path / "local-appdata"
    history_root = local_appdata / "bidking"
    history_root.mkdir(parents=True)
    now = int(time.time())
    damaged_history = {
        "schema": 1,
        "updated_at": now,
        "sequences": [
            {
                "started_at": now - 10,
                "last_failure_at": now - 5,
                "recovered_at": now - 4,
                "attempt_count": 1,
                "attempts": [
                    {
                        "at": now - 1,
                        "endpoint": "endpoint_1",
                        "stage": "tls",
                        "failure_class": "tls_connection_reset",
                    }
                ],
            }
        ],
    }
    (history_root / "activation_recheck_history_42.json").write_text(
        json.dumps(damaged_history),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("BIDKING_PACKAGE_ROOT", str(package_root))

    report = _run_collection_script(package_root, local_appdata=local_appdata)

    assert "最近在线复核摘要: 无" in report
    assert "failure_class=tls_connection_reset" not in report


def test_collect_diagnostics_accepts_valid_truncated_recheck_timeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    local_appdata = tmp_path / "local-appdata"
    history_root = local_appdata / "bidking"
    history_root.mkdir(parents=True)
    now = int(time.time())
    attempts = [
        {
            "at": now - 12 + index,
            "endpoint": "endpoint_1",
            "stage": "tls",
            "failure_class": "tls_connection_reset",
        }
        for index in range(12)
    ]
    safe_history = {
        "schema": 1,
        "updated_at": now,
        "sequences": [
            {
                "started_at": now - 20,
                "last_failure_at": now - 1,
                "recovered_at": now,
                "attempt_count": 13,
                "attempts": attempts,
            }
        ],
    }
    (history_root / "activation_recheck_history_42.json").write_text(
        json.dumps(safe_history),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("BIDKING_PACKAGE_ROOT", str(package_root))

    report = _run_collection_script(package_root, local_appdata=local_appdata)

    assert "最近在线复核摘要: 1 段" in report
    assert "attempt_count=13" in report
    assert report.count("failure_class=tls_connection_reset") == 12


def test_collect_diagnostics_rejects_two_pending_sequences_in_one_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    local_appdata = tmp_path / "local-appdata"
    history_root = local_appdata / "bidking"
    history_root.mkdir(parents=True)
    now = int(time.time())
    _write_diagnostic_recheck_history(
        history_root / "activation_recheck_history_42.json",
        now=now,
        sequences=[
            _diagnostic_recheck_sequence(now - 20, recovered_at=None),
            _diagnostic_recheck_sequence(now - 10, recovered_at=None),
        ],
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("BIDKING_PACKAGE_ROOT", str(package_root))

    report = _run_collection_script(package_root, local_appdata=local_appdata)

    assert "最近在线复核摘要: 无" in report
    assert "failure_class=tls_connection_reset" not in report


def test_collect_diagnostics_rejects_sequence_start_before_previous_recovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    local_appdata = tmp_path / "local-appdata"
    history_root = local_appdata / "bidking"
    history_root.mkdir(parents=True)
    now = int(time.time())
    _write_diagnostic_recheck_history(
        history_root / "activation_recheck_history_42.json",
        now=now,
        sequences=[
            _diagnostic_recheck_sequence(now - 20, recovered_at=now - 10),
            _diagnostic_recheck_sequence(now - 15, recovered_at=None),
        ],
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("BIDKING_PACKAGE_ROOT", str(package_root))

    report = _run_collection_script(package_root, local_appdata=local_appdata)

    assert "最近在线复核摘要: 无" in report
    assert "failure_class=tls_connection_reset" not in report


def test_collect_diagnostics_keeps_causal_file_and_pending_from_another_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    local_appdata = tmp_path / "local-appdata"
    history_root = local_appdata / "bidking"
    history_root.mkdir(parents=True)
    now = int(time.time())
    _write_diagnostic_recheck_history(
        history_root / "activation_recheck_history_42.json",
        now=now,
        sequences=[
            _diagnostic_recheck_sequence(now - 30, recovered_at=now - 20),
            _diagnostic_recheck_sequence(now - 10, recovered_at=None),
        ],
    )
    _write_diagnostic_recheck_history(
        history_root / "activation_recheck_history_43.json",
        now=now,
        sequences=[
            _diagnostic_recheck_sequence(now - 10, recovered_at=None)
        ],
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("BIDKING_PACKAGE_ROOT", str(package_root))

    report = _run_collection_script(package_root, local_appdata=local_appdata)

    assert "最近在线复核摘要: 3 段" in report
    assert report.count("recovered_at=pending") == 2
    assert report.count("failure_class=tls_connection_reset") == 3


def test_python_redactor_has_positive_and_negative_controls() -> None:
    raw = (
        "token=v1|buyer|100|9999999999|r." + "b" * 64
        + " Bearer PRIVATE_BEARER 198.51.100.25 AA:BB:CC:DD:EE:FF "
        + r"C:\Users\example\BidKing private@example.com"
    )
    redacted = redact_text(raw)

    for canary in (
        "PRIVATE_BEARER",
        "198.51.100.25",
        "AA:BB:CC:DD:EE:FF",
        "PrivateUser",
        "private@example.com",
    ):
        assert canary not in redacted
    assert "BidKing" in redacted
    assert "<redacted>" in redacted


def test_structured_redactor_preserves_shape_but_removes_identity_and_paths() -> None:
    payload = {
        "status": "ok",
        "player": "PRIVATE_PLAYER",
        "device_id": "PRIVATE_DEVICE",
        "Data": "PRIVATE_PACKET_DATA",
        "snapshot_path": r"C:\Users\example\latest_snapshot.json",
        "nested": [{"uid_hash": "PRIVATE_UID", "hero": "ahmed"}],
    }
    redacted = redact_payload(payload)

    assert redacted["status"] == "ok"
    assert redacted["player"] == "<redacted>"
    assert redacted["device_id"] == "<redacted>"
    assert redacted["Data"] == "<redacted>"
    assert redacted["snapshot_path"] == "<local-path>/latest_snapshot.json"
    assert redacted["nested"][0] == {"uid_hash": "<redacted>", "hero": "ahmed"}


def test_player_dynamic_keys_and_embedded_names_share_package_aliases() -> None:
    payload = {
        "self_name": "SELF_NAME_CANARY",
        "local_player_id": "LOCAL_PLAYER_ID_CANARY",
        "player_ranks": {
            "RANK_NAME_CANARY": {"player_id": "RANK_ID_CANARY", "rank": 2},
        },
        "latest_bids": {
            "LATEST_BID_NAME_CANARY": 1234,
        },
        "note": "RANK_NAME_CANARY 与 LATEST_BID_NAME_CANARY 同时出价",
    }
    aliases = collect_player_aliases(payload)
    redacted = redact_payload(payload, player_aliases=aliases)

    assert redacted["self_name"] == "<redacted>"
    assert redacted["local_player_id"] == "<redacted>"
    assert set(redacted["player_ranks"]) == {aliases["RANK_NAME_CANARY"]}
    assert set(redacted["latest_bids"]) == {aliases["LATEST_BID_NAME_CANARY"]}
    assert redacted["player_ranks"][aliases["RANK_NAME_CANARY"]]["player_id"] == "<redacted>"
    for canary in (
        "SELF_NAME_CANARY",
        "LOCAL_PLAYER_ID_CANARY",
        "RANK_NAME_CANARY",
        "RANK_ID_CANARY",
        "LATEST_BID_NAME_CANARY",
    ):
        assert canary not in str(redacted)


def test_player_rank_record_id_and_short_names_are_redacted_in_context() -> None:
    payload = {
        "id": "UNRELATED_MAP_ID_MUST_SURVIVE",
        "self_name": "甲",
        "player_id": "TOP_LEVEL_PLAYER_ID_CANARY",
        "player_ranks": {
            "甲": {"p": 1, "t": 2, "id": "ONE_CHAR_STABLE_ID_CANARY"},
            "乙丙": {"p": 3, "t": 4, "id": "TWO_CHAR_STABLE_ID_CANARY"},
            "三字玩家": {"p": 5, "t": 6, "id": "STABLE_ID_CANARY"},
        },
        "latest_bids": {"甲": 100, "乙丙": 200, "三字玩家": 300},
        "note": (
            "甲、乙丙和三字玩家都出价；STABLE_ID_CANARY 也是玩家稳定 ID；"
            "地图 id 不属于玩家身份。"
        ),
    }
    aliases = collect_player_aliases(payload)
    redacted = redact_payload(payload, player_aliases=aliases)
    rendered = json.dumps(redacted, ensure_ascii=False)

    assert redacted["id"] == "UNRELATED_MAP_ID_MUST_SURVIVE"
    for canary in (
        "甲",
        "乙丙",
        "三字玩家",
        "TOP_LEVEL_PLAYER_ID_CANARY",
        "ONE_CHAR_STABLE_ID_CANARY",
        "TWO_CHAR_STABLE_ID_CANARY",
        "STABLE_ID_CANARY",
    ):
        assert canary not in rendered


def test_jsonl_redactor_does_not_fall_back_to_raw_for_malformed_lines() -> None:
    text = '{"token":"PRIVATE_TOKEN","status":"ok"}\napi_key=PRIVATE_KEY\n'
    redacted = redact_document(text, suffix=".jsonl")

    assert "PRIVATE_TOKEN" not in redacted
    assert "PRIVATE_KEY" not in redacted
    assert '"status": "ok"' in redacted


def test_collect_diagnostics_powershell_redactor_positive_control() -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell unavailable")
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")
    match = re.search(
        r"function Protect-DiagnosticText\(\$value\) \{.*?^\}",
        script,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    probe = (
        match.group(0)
        + "\nProtect-DiagnosticText 'token=PRIVATE_TOKEN 203.0.113.7 C:\\Users\\example\\BidKing'"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PRIVATE_TOKEN" not in completed.stdout
    assert "203.0.113.7" not in completed.stdout
    assert "PrivateUser" not in completed.stdout
    assert "BidKing" in completed.stdout


def test_live_settlement_logging_is_safe_by_default_and_raw_only_on_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(fatbeans, "LOG_SETTLEMENT_FRAMES", False)
    monkeypatch.setattr(fatbeans, "LOG_RAW_SETTLEMENT_FRAMES", False)
    monkeypatch.delenv(fatbeans.RAW_SETTLEMENT_FORENSICS_ENV, raising=False)

    assert fatbeans.configure_live_settlement_diagnostics() is False
    fatbeans._log_settlement_frame(
        _settlement_frame_with_private_body(),
        b"PRIVATE_PAYLOAD",
        f4_items=16,
        merged_items=33,
    )
    default_output = capsys.readouterr().out
    assert "[settle] sort=7 body=16B payload=15B f4_items=16 merged_items=33 f3_extra=17" in default_output
    assert "[settle-raw]" not in default_output
    assert "UFJJVkFURV9QTEFZRVI=" not in default_output

    monkeypatch.setenv(fatbeans.RAW_SETTLEMENT_FORENSICS_ENV, "true")
    assert fatbeans.configure_live_settlement_diagnostics() is False
    monkeypatch.setenv(fatbeans.RAW_SETTLEMENT_FORENSICS_ENV, "0")
    assert fatbeans.configure_live_settlement_diagnostics() is False
    monkeypatch.setenv(fatbeans.RAW_SETTLEMENT_FORENSICS_ENV, "1")
    assert fatbeans.configure_live_settlement_diagnostics() is True
    fatbeans._log_settlement_frame(
        _settlement_frame_with_private_body(),
        b"PRIVATE_PAYLOAD",
        f4_items=16,
        merged_items=33,
    )
    raw_output = capsys.readouterr().out
    assert "[settle-raw]" in raw_output
    assert "bodyb64=Cg5QUklWQVRFX1BMQVlFUg==" in raw_output


def test_portable_monitors_use_the_shared_raw_forensics_gate() -> None:
    for relative in (
        "scripts/run_npcap_live_monitor.py",
        "scripts/run_wss_live_monitor.py",
        "scripts/run_windivert_live_monitor.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "configure_live_settlement_diagnostics()" in source
        assert "BIDKING_ENABLE_RAW_SETTLEMENT_FORENSICS=1" in source
        assert "LOG_RAW_SETTLEMENT_FRAMES = True" not in source

    portable_build = (ROOT / "scripts/build_live_portable.ps1").read_text(encoding="utf-8-sig")
    assert "BIDKING_ENABLE_RAW_SETTLEMENT_FORENSICS" not in portable_build


def test_collect_diagnostics_redactor_drops_raw_lines_and_opaque_payload_fields() -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell unavailable")
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")
    match = re.search(
        r"function Protect-DiagnosticText\(\$value\) \{.*?^\}",
        script,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    probe = (
        match.group(0)
        + "\nProtect-DiagnosticText '[settle-raw] bodyb64=UFJJVkFURV9QTEFZRVI= payload=PRIVATE_PAYLOAD'"
        + "\nProtect-DiagnosticText '[status] frame_base64=PRIVATE_FRAME Data=PRIVATE_DATA payload=PRIVATE_OTHER'"
        + "\nProtect-DiagnosticText '[settle] body=8123B payload=7999B f4_items=16 merged_items=33'"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for canary in (
        "UFJJVkFURV9QTEFZRVI=",
        "PRIVATE_PAYLOAD",
        "PRIVATE_FRAME",
        "PRIVATE_DATA",
        "PRIVATE_OTHER",
    ):
        assert canary not in completed.stdout
    assert "<raw-forensics-line-omitted>" in completed.stdout
    assert "body=8123B payload=7999B f4_items=16 merged_items=33" in completed.stdout


def test_collect_diagnostics_redactor_consumes_complete_quoted_values_and_only_keeps_lengths() -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell unavailable")
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")
    match = re.search(
        r"function Protect-DiagnosticText\(\$value\) \{.*?^\}",
        script,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    probes = (
        '[status] Data="PRIVATE DATA WITH SPACES" payload="PRIVATE PAYLOAD WITH SPACES"',
        '[status] "Data":"PRIVATE,DATA;TAIL" "payload":"PRIVATE,PAYLOAD;TAIL"',
        '[status] bodyb64=PRIVATE_BODY_TOKEN frame_base64=PRIVATE_FRAME_TOKEN',
        '[status] bodyb64="PRIVATE BODY WITH SPACES" frame_base64="PRIVATE FRAME WITH SPACES"',
        '[status] "bodyb64":"PRIVATE,BODY;TAIL" "frame_base64":"PRIVATE,FRAME;TAIL"',
        '[settle] payload=7999B',
        '[settle] payload="7999B"',
        '[settle] "payload":"7999B"',
    )
    calls = "".join(
        "\nProtect-DiagnosticText '" + probe.replace("'", "''") + "'"
        for probe in probes
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", match.group(0) + calls],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for canary in (
        "PRIVATE DATA WITH SPACES",
        "PRIVATE PAYLOAD WITH SPACES",
        "PRIVATE,DATA;TAIL",
        "PRIVATE,PAYLOAD;TAIL",
        "PRIVATE_BODY_TOKEN",
        "PRIVATE_FRAME_TOKEN",
        "PRIVATE BODY WITH SPACES",
        "PRIVATE FRAME WITH SPACES",
        "PRIVATE,BODY;TAIL",
        "PRIVATE,FRAME;TAIL",
        "DATA WITH SPACES",
        "PAYLOAD WITH SPACES",
        "DATA;TAIL",
        "PAYLOAD;TAIL",
        "BODY;TAIL",
        "FRAME;TAIL",
    ):
        assert canary not in completed.stdout
    assert "[settle] payload=7999B" in completed.stdout
    assert '[settle] payload="7999B"' in completed.stdout
    assert '[settle] "payload":"7999B"' in completed.stdout


def test_collect_diagnostics_redactor_rejects_truncated_or_illegal_quoted_boundaries() -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell unavailable")
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")
    match = re.search(
        r"function Protect-DiagnosticText\(\$value\) \{.*?^\}",
        script,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    probes = (
        'payload="7999B"ADJACENT_PRIVATE_TAIL',
        'payload="TRUNCATED PAYLOAD TAIL',
        'Data="TRUNCATED DATA TAIL',
        'bodyb64="TRUNCATED BODY TAIL',
        'frame_base64="TRUNCATED FRAME TAIL',
        'payload=7999b',
        'payload="7999B"',
        'payload="7999B" next=ok',
        'payload="7999B",next=ok',
        'payload="7999B";next=ok',
        '{"payload":"7999B"}',
        '{"payload":"7999B","status":"ok"}',
    )
    calls = "".join(
        "\nProtect-DiagnosticText '" + probe.replace("'", "''") + "'"
        for probe in probes
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", match.group(0) + calls],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for canary in (
        "ADJACENT_PRIVATE_TAIL",
        "TRUNCATED",
        "PAYLOAD TAIL",
        "DATA TAIL",
        "BODY TAIL",
        "FRAME TAIL",
        "7999b",
    ):
        assert canary not in completed.stdout
    for safe_line in probes[6:]:
        assert safe_line in completed.stdout


def test_collect_diagnostics_only_attributes_logs_from_current_launch(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell unavailable")
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")
    match = re.search(
        r"function Test-DiagnosticFileForLaunch\(\s*"
        r"\$path,\s*\$launchStartedAt,\s*\$launchId,\s*\$logSessionState,\s*"
        r"\$declaredProperty\s*\) \{.*?^\}",
        script,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    old_log = str(tmp_path / "old.log").replace("'", "''")
    touched_log = str(tmp_path / "touched.log").replace("'", "''")
    current_log = str(tmp_path / "current.log").replace("'", "''")
    launch_id = "11111111-2222-4333-8444-555555555555"
    other_launch_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    probe = (
        match.group(0)
        + f"\n$old = '{old_log}'"
        + f"\n$touched = '{touched_log}'"
        + f"\n$current = '{current_log}'"
        + "\n'old' | Set-Content -LiteralPath $old"
        + "\n'old content, only touched later' | Set-Content -LiteralPath $touched"
        + "\n'current' | Set-Content -LiteralPath $current"
        + "\n(Get-Item -LiteralPath $old).LastWriteTimeUtc = [datetime]::UtcNow.AddHours(-1)"
        + "\n(Get-Item -LiteralPath $touched).LastWriteTimeUtc = [datetime]::UtcNow"
        + "\n$launch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - 5"
        + f"\n$launchId = '{launch_id}'"
        + "\n$currentMarker = [pscustomobject]@{ launch_id=$launchId; started_at=$launch; "
          "stdout_file='current.log'; stderr_file='current.err.log' }"
        + f"\n$oldMarker = [pscustomobject]@{{ launch_id='{other_launch_id}'; started_at=($launch-3600); "
          "stdout_file='touched.log'; stderr_file='old.err.log' }"
        + "\nWrite-Output ((Test-DiagnosticFileForLaunch $old $launch $launchId $currentMarker 'stdout_file').ToString())"
        + "\nWrite-Output ((Test-DiagnosticFileForLaunch $touched $launch $launchId $oldMarker 'stdout_file').ToString())"
        + "\nWrite-Output ((Test-DiagnosticFileForLaunch $current $launch $launchId $oldMarker 'stdout_file').ToString())"
        + "\nWrite-Output ((Test-DiagnosticFileForLaunch $current $launch $launchId $null 'stdout_file').ToString())"
        + "\nWrite-Output ((Test-DiagnosticFileForLaunch $current $launch $launchId $currentMarker 'stdout_file').ToString())"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[-5:] == [
        "False",  # 一小时前旧日志
        "False",  # 旧内容只 touch，sidecar 仍属于旧 launch
        "False",  # sidecar launch ID 不匹配
        "False",  # sidecar 缺失时 fail-safe
        "True",   # 当前 launch ID + 当前 sidecar + 当前日志
    ]


def test_collect_diagnostics_applies_launch_scope_to_every_monitor_log_consumer() -> None:
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "monitor_log_session.json" in script
    assert script.count("Test-CurrentMonitorLog") >= 8
    assert "历史辅助信息，不作为本次启动证据" in script
    assert "本次启动错误关键词命中" in script
    assert "本次启动结算采集摘要" in script
    assert "本次启动抓包状态行" in script
    assert "helperBelongsToCurrentLaunch" in script
    assert "不作为当前错误证据" in script


def test_collect_diagnostics_reports_safe_wss_helper_classification_and_zone(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    started_at = time.time() - 5
    (log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": started_at}),
        encoding="utf-8",
    )
    (log_dir / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": started_at,
                "monitor_pid": 1234,
                "stdout_file": "monitor.stdout.log",
                "stderr_file": "monitor.stderr.log",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    (log_dir / "wss_helper_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "exited_unexpectedly",
                "launch_id": launch_id,
                "helper_exit_code": 1,
                "redirector_state": "elevation_denied",
                "windivert_state": "unknown",
                "error_code": "wss_redirector_elevation_denied",
                "raw_stderr": (
                    "token=PRIVATE_HELPER_TOKEN endpoint=198.51.100.9 "
                    "player=PRIVATE_HELPER_PLAYER"
                ),
            }
        ),
        encoding="utf-8",
    )
    redirector = (
        tmp_path
        / "vendor"
        / "mitmproxy"
        / "_internal"
        / "mitmproxy_windows"
        / "windows-redirector.exe"
    )
    redirector.parent.mkdir(parents=True)
    redirector.write_bytes(b"test redirector")
    zone_stream = Path(f"{redirector}:Zone.Identifier")
    try:
        zone_stream.write_text(
            "[ZoneTransfer]\n"
            "ZoneId=3\n"
            "ReferrerUrl=https://private.example.test/download?token=PRIVATE_REFERRER\n",
            encoding="utf-8",
        )
    except OSError as exc:
        pytest.skip(f"NTFS alternate data streams unavailable: {exc}")

    report = _run_collection_script(tmp_path)

    assert (
        "helper: phase=exited_unexpectedly exit_code=1 "
        "redirector=elevation_denied windivert=unknown"
    ) in report
    assert "code=wss_redirector_elevation_denied" in report
    assert "redirector_download_zone=3" in report
    for canary in (
        "PRIVATE_HELPER_TOKEN",
        "PRIVATE_HELPER_PLAYER",
        "198.51.100.9",
        "PRIVATE_REFERRER",
        "private.example.test",
        "ReferrerUrl",
    ):
        assert canary not in report


def test_collect_diagnostics_reports_only_fixed_internet_cafe_receipt_fields(
    tmp_path: Path,
) -> None:
    documents = tmp_path.parent / f"{tmp_path.name}-documents"
    exports = documents / "BidKingLive" / "exports"
    exports.mkdir(parents=True)
    receipt = {
        "schema_version": 1,
        "recorded_at": "2026-08-20T00:00:00Z",
        "phase": "preflight",
        "classification": "driver_load_denied",
        "result": "internet_cafe_realtime_unsupported",
        "next_step": "ask_cafe_admin_to_allow_verified_driver",
        "mutation_performed": False,
        "store_access": "available",
        "profile_state": "persistent",
        "component_state": "available",
        "driver_state": "write_denied",
        "is_admin": True,
        "enable_lua_state": "enabled",
        "privacy": "fixed-enums-only",
        "message": "PRIVATE_EXCEPTION_MESSAGE",
        "path": "C:\\Users\\example\\certificate.cer",
        "certificate_raw_b64": "PRIVATE_CERTIFICATE_BODY",
    }
    (exports / "BidKingInternetCafe-20260820-000000-000.json").write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert (
        "网吧环境兼容: classification=driver_load_denied "
        "result=internet_cafe_realtime_unsupported "
        "next_step=ask_cafe_admin_to_allow_verified_driver"
    ) in report
    for canary in (
        "PRIVATE_EXCEPTION_MESSAGE",
        "PRIVATE_USER",
        "PRIVATE_CERTIFICATE_BODY",
        "certificate.cer",
    ):
        assert canary not in report


def test_collect_diagnostics_rejects_stale_wss_helper_sidecar(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    current_launch_id = "11111111-2222-4333-8444-555555555555"
    (log_dir / "startup_session.json").write_text(
        json.dumps(
            {
                "launch_id": current_launch_id,
                "started_at": time.time() - 5,
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    (log_dir / "wss_helper_status.json").write_text(
        json.dumps(
            {
                "phase": "exited_unexpectedly",
                "launch_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                "helper_exit_code": 1,
                "redirector_state": "elevation_denied",
                "windivert_state": "unknown",
                "error_code": "wss_redirector_elevation_denied",
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert "不作为当前错误证据" in report
    assert "code=wss_redirector_elevation_denied" not in report


def test_collect_diagnostics_reports_known_runtime_compatibility_without_raw_stderr(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    (log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": time.time() - 5}),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    driver_sha256 = (
        "8da085332782708d8767bcace5327a6ec"
        "7283c17cfb85e40b03cd2323a90ddc2"
    )
    (log_dir / "wss_helper_status.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "phase": "exited_unexpectedly",
                "launch_id": launch_id,
                "helper_exit_code": 1,
                "redirector_state": "start_failed",
                "windivert_state": "known_historical_blocked_driver",
                "error_code": "wss_runtime_compatibility_failed",
                "runtime_compatibility_state": "failed_known_historical_driver",
                "windivert_driver_sha256": driver_sha256,
                "raw_stderr": (
                    "Task failed: redirect daemon exited prematurely. "
                    "token=PRIVATE_HELPER_TOKEN endpoint=private.example.test"
                ),
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert (
        "helper: phase=exited_unexpectedly exit_code=1 "
        "redirector=start_failed windivert=known_historical_blocked_driver"
    ) in report
    assert "code=wss_runtime_compatibility_failed" in report
    assert (
        "runtime_compatibility=failed_known_historical_driver "
        f"driver_sha256={driver_sha256}"
    ) in report
    assert "保持易受攻击驱动阻止列表和内存完整性开启" in report
    assert "PRIVATE_HELPER_TOKEN" not in report
    assert "private.example.test" not in report
    assert "redirect daemon exited prematurely" not in report


def test_collect_diagnostics_reports_stable_missing_redirector_without_guessing_cause(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    (log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": time.time() - 30}),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    (log_dir / "wss_helper_status.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "phase": "helper_running",
                "launch_id": launch_id,
                "redirector_state": "missing_stable",
                "windivert_state": "unknown",
                "error_code": "wss_redirector_missing_stable",
                "unexpected_private_field": "PRIVATE_TOKEN",
                "command_line": r"C:\Users\example\redirector.exe --secret",
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert (
        "helper: phase=helper_running exit_code=running "
        "redirector=missing_stable windivert=unknown"
    ) in report
    assert "code=wss_redirector_missing_stable" in report
    assert "启动宽限和连续复核后仍未接管" in report
    assert "本分类不锁定唯一根因" in report
    for canary in ("PRIVATE_TOKEN", "private-user", "command_line", "--secret"):
        assert canary not in report


@pytest.mark.parametrize(
    "preload_state",
    ("preloaded", "repaired_restart_required"),
)
def test_collect_diagnostics_reports_only_whitelisted_windivert_preload_state(
    tmp_path: Path,
    preload_state: str,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    (log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": time.time() - 5}),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    helper_path = log_dir / "wss_helper_status.json"
    helper_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "phase": "helper_running",
                "launch_id": launch_id,
                "redirector_state": "starting",
                "windivert_state": "unknown",
                "windivert_preload_state": preload_state,
                "windivert_preload_phase": "complete",
                "monitor_elevation_state": "elevated",
                "unexpected_private_field": "PRIVATE_TOKEN",
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert (
        f"WinDivert 隔离预加载: state={preload_state} phase=complete "
        "monitor_elevation=elevated"
    ) in report
    assert "PRIVATE_TOKEN" not in report

    invalid_root = tmp_path / "invalid"
    invalid_log_dir = invalid_root / "local-appdata" / "BidKingLive" / "logs" / "live"
    invalid_log_dir.mkdir(parents=True)
    (invalid_log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": time.time() - 5}),
        encoding="utf-8",
    )
    (invalid_log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    (invalid_log_dir / "wss_helper_status.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "phase": "helper_running",
                "launch_id": launch_id,
                "redirector_state": "starting",
                "windivert_state": "unknown",
                "windivert_preload_state": "preloaded:PRIVATE_TOKEN",
                "windivert_preload_phase": "start_product:PRIVATE_TOKEN",
                "monitor_elevation_state": "elevated:C:\\Users\\example",
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(invalid_root)

    assert "WinDivert 隔离预加载:" not in report
    assert "PRIVATE_TOKEN" not in report


def test_collect_diagnostics_reports_helper_start_cleanup_without_private_fields(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    (log_dir / "startup_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": time.time() - 5,
                "launcher_elevation_state": "elevated",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    (log_dir / "wss_helper_status.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "phase": "helper_start_failed",
                "helper_pid": 0,
                "launch_id": launch_id,
                "redirector_state": "unknown",
                "windivert_state": "unknown",
                "error_code": "wss_capture_helper_start_failed",
                "windivert_preload_state": "failed_access_denied",
                "windivert_preload_phase": "cleanup",
                "monitor_elevation_state": "elevated",
                "exception": "PRIVATE_TOKEN",
                "command_line": r"C:\Users\example\mitmdump.exe --secret",
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert (
        "helper: phase=helper_start_failed exit_code=not_started "
        "redirector=unknown windivert=unknown"
    ) in report
    assert (
        "WinDivert 隔离预加载: state=failed_access_denied phase=cleanup "
        "monitor_elevation=elevated"
    ) in report
    assert "已提升的本地监控在 cleanup 阶段被 SCM 或系统策略拒绝" in report
    assert "code=wss_capture_helper_start_failed" in report
    for canary in (
        "PRIVATE_TOKEN",
        "private",
        "command_line",
        "--secret",
        "mitmdump.exe",
    ):
        assert canary not in report


@pytest.mark.parametrize(
    ("preload_phase", "monitor_elevation", "expected"),
    (
        (
            "create_product",
            "elevated",
            "已提升的本地监控在 create_product 阶段被 SCM 或系统策略拒绝",
        ),
        (
            "start_product",
            "elevated",
            "已提升的本地监控在 start_product 阶段被 SCM 或系统策略拒绝",
        ),
        (
            "start_product",
            "not_elevated",
            "本地监控未处于管理员上下文",
        ),
    ),
)
def test_collect_diagnostics_distinguishes_access_denied_phase_and_elevation(
    tmp_path: Path,
    preload_phase: str,
    monitor_elevation: str,
    expected: str,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    (log_dir / "startup_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": time.time() - 5,
                "launcher_elevation_state": "elevated",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    (log_dir / "wss_helper_status.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "phase": "helper_running",
                "launch_id": launch_id,
                "redirector_state": "missing_stable",
                "windivert_state": "unknown",
                "windivert_preload_state": "failed_access_denied",
                "windivert_preload_phase": preload_phase,
                "monitor_elevation_state": monitor_elevation,
                "unexpected_private_field": "PRIVATE_TOKEN",
                "security_process": r"C:\Users\example\security.exe",
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert expected in report
    assert "不锁定具体安全软件或系统策略" in report
    for canary in ("PRIVATE_TOKEN", "private", "security_process"):
        assert canary not in report


def test_collect_diagnostics_reports_privacy_safe_three_layer_status(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    started_at = time.time() - 5
    (log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": started_at}),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps({"source": "wss_local"}),
        encoding="utf-8",
    )
    (log_dir / "network_connection_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "launch_id": launch_id,
                "game_connection_state": "failed",
                "wss_intercept_state": "ready",
                "capture_decode_state": "idle",
                "game_pid": 2468,
                "game_started_at": started_at + 1,
                "player_log_window_started_at": started_at + 1,
                "evidence": {
                    "player_log_error_counts": {
                        "websocket_unable_to_connect": 2,
                    },
                    "raw_player_log": (
                        "PRIVATE_PLAYER PRIVATE_TOKEN 198.51.100.10"
                    ),
                },
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert "game_connection_state=failed" in report
    assert "wss_intercept_state=ready" in report
    assert "capture_decode_state=idle" in report
    assert "websocket_unable_to_connect=2" in report
    assert "game_pid=2468" in report
    for canary in ("PRIVATE_PLAYER", "PRIVATE_TOKEN", "198.51.100.10"):
        assert canary not in report


def test_collect_diagnostics_declares_required_network_context_without_raw_addresses() -> None:
    script = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8-sig"
    )

    for marker in (
        "clash_mode=",
        "tun_state=",
        "fake_ip_198_18=",
        "WinINET=",
        "WinHTTP=",
        "active_default_route_count=",
        "fixed_endpoint_tcp=skipped",
        "TCP success 仅证明 L4 建连",
    ):
        assert marker in script
    assert "完整 Player.log" not in script
    assert "Get-Content" not in "\n".join(
        line for line in script.splitlines() if "Player.log" in line
    )


def test_collect_diagnostics_attributes_matching_launch_end_to_end(tmp_path: Path) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    started_at = time.time() - 5
    (log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": started_at}),
        encoding="utf-8",
    )
    (log_dir / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": started_at,
                "monitor_pid": 1234,
                "stdout_file": "monitor.stdout.log",
                "stderr_file": "monitor.stderr.log",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "startup_status.log").write_text(
        f"2026-07-20T00:00:00 launch_begin launch_id={launch_id}\n",
        encoding="utf-8",
    )
    (log_dir / "monitor.stdout.log").write_text(
        "[settle] CURRENT_SETTLE_CANARY\n[status] CURRENT_STATUS_CANARY\n",
        encoding="utf-8",
    )
    (log_dir / "monitor.stderr.log").write_text(
        "Traceback CURRENT_ERROR_CANARY\n",
        encoding="utf-8",
    )
    (log_dir / "lifecycle_status.json").write_text(
        json.dumps(
            {
                "phase": "draining",
                "game_state": "exited",
                "ui_state": "exited",
                "policy_state": "enabled",
                "stop_reason": None,
            }
        ),
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert str(tmp_path).lower() not in report.lower()
    assert "包根目录: <package-root>" in report
    assert "monitor.stdout.log (本次启动证据" in report
    assert "本次启动错误关键词命中" in report
    assert "本次启动结算采集摘要" in report
    assert "本次启动抓包状态行" in report
    assert "startup_status.log (本次 launch ID" in report
    assert (
        "WSS 生命周期: phase=draining game=exited ui=exited policy=enabled"
        in report
    )


def test_collect_diagnostics_labels_reused_monitor_as_continuous_log(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    previous_launch_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    started_at = time.time() - 5
    (log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": started_at}),
        encoding="utf-8",
    )
    (log_dir / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": started_at,
                "monitor_pid": 1234,
                "monitor_started_at": started_at - 120,
                "reused_monitor": True,
                "previous_launch_id": previous_launch_id,
                "stdout_file": "monitor.stdout.log",
                "stderr_file": "monitor.stderr.log",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "startup_status.log").write_text(
        f"2026-07-24T20:18:05 launch_begin launch_id={launch_id}\n"
        f"2026-07-24T20:18:05 monitor_reused launch_id={launch_id} monitor_pid=1234\n",
        encoding="utf-8",
    )
    (log_dir / "monitor.stdout.log").write_text(
        "[settle] REUSED_SETTLE_CANARY\n[status] REUSED_STATUS_CANARY\n",
        encoding="utf-8",
    )
    (log_dir / "monitor.stderr.log").write_text(
        "Traceback REUSED_ERROR_CANARY\n",
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert str(tmp_path).lower() not in report.lower()
    assert "本次启动复用已有 monitor，连续日志已在本次 launch 后更新" in report
    assert "本次启动复用 monitor 的连续日志；尾部可能含复用前记录" in report
    assert "复用 monitor 错误关键词（可能含复用前记录）" in report
    assert "复用 monitor [settle] 行（可能含复用前记录）" in report
    assert "复用 monitor 抓包状态行（连续日志，可能含复用前记录）" in report
    assert "本次启动尚未创建 monitor 日志" not in report


def test_collect_diagnostics_report_rejects_raw_payload_but_keeps_safe_capture_stats(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    launch_id = "11111111-2222-4333-8444-555555555555"
    started_at = time.time() - 5
    (log_dir / "startup_session.json").write_text(
        json.dumps({"launch_id": launch_id, "started_at": started_at}),
        encoding="utf-8",
    )
    (log_dir / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": launch_id,
                "started_at": started_at,
                "monitor_pid": 1234,
                "stdout_file": "monitor.stdout.log",
                "stderr_file": "monitor.stderr.log",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "monitor.stdout.log").write_text(
        "[settle-raw] 203.0.113.7 bodyb64=UFJJVkFURV9QTEFZRVI= "
        "payload=PRIVATE_PAYLOAD player_name=PRIVATE_PLAYER\n"
        "[status] Data=\"PRIVATE DATA WITH SPACES\" "
        "payload=\"PRIVATE PAYLOAD WITH SPACES\"\n"
        "[status] \"Data\":\"PRIVATE,DATA;TAIL\" "
        "\"payload\":\"PRIVATE,PAYLOAD;TAIL\"\n"
        "[status] bodyb64=PRIVATE_BODY_TOKEN frame_base64=PRIVATE_FRAME_TOKEN\n"
        "[status] \"bodyb64\":\"PRIVATE,BODY;TAIL\" "
        "\"frame_base64\":\"PRIVATE,FRAME;TAIL\" token=PRIVATE_TOKEN\n"
        "[status] payload=\"7999B\"ADJACENT_PRIVATE_TAIL\n"
        "[status] payload=\"TRUNCATED PAYLOAD TAIL\n"
        "[status] Data=\"TRUNCATED DATA TAIL\n"
        "[status] bodyb64=\"TRUNCATED BODY TAIL\n"
        "[status] frame_base64=\"TRUNCATED FRAME TAIL\n"
        "[status] payload=\"7999B\" next=ok\n"
        "[status] {\"payload\":\"7999B\",\"status\":\"ok\"}\n"
        "[settle] sort=7 body=8123B payload=7999B f4_items=16 "
        "merged_items=33 f3_extra=17\n"
        "[seq-jump] #1 REV flow=0123456789ab "
        "jump=128B seq=4000 (wire_gaps=1)\n",
        encoding="utf-8",
    )
    (log_dir / "monitor.stderr.log").write_text("", encoding="utf-8")

    report = _run_collection_script(tmp_path)

    for canary in (
        "203.0.113.7",
        "198.51.100.9",
        "UFJJVkFURV9QTEFZRVI=",
        "PRIVATE_PAYLOAD",
        "PRIVATE_PLAYER",
        "PRIVATE DATA WITH SPACES",
        "PRIVATE PAYLOAD WITH SPACES",
        "PRIVATE,DATA;TAIL",
        "PRIVATE,PAYLOAD;TAIL",
        "PRIVATE_BODY_TOKEN",
        "PRIVATE_FRAME_TOKEN",
        "PRIVATE,BODY;TAIL",
        "PRIVATE,FRAME;TAIL",
        "DATA WITH SPACES",
        "PAYLOAD WITH SPACES",
        "DATA;TAIL",
        "PAYLOAD;TAIL",
        "BODY;TAIL",
        "FRAME;TAIL",
        "ADJACENT_PRIVATE_TAIL",
        "TRUNCATED",
        "PAYLOAD TAIL",
        "DATA TAIL",
        "BODY TAIL",
        "FRAME TAIL",
        "PRIVATE_TOKEN",
    ):
        assert canary not in report
    assert "<raw-forensics-line-omitted>" in report
    assert 'payload="7999B" next=ok' in report
    assert '{"payload":"7999B","status":"ok"}' in report
    assert "body=8123B payload=7999B f4_items=16 merged_items=33 f3_extra=17" in report
    assert "flow=0123456789ab jump=128B seq=4000 (wire_gaps=1)" in report


@pytest.mark.parametrize("startup_contents", [None, "{malformed-json"])
def test_collect_diagnostics_fails_safe_without_valid_startup_session(
    tmp_path: Path,
    startup_contents: str | None,
) -> None:
    log_dir = tmp_path / "local-appdata" / "BidKingLive" / "logs" / "live"
    log_dir.mkdir(parents=True)
    if startup_contents is not None:
        (log_dir / "startup_session.json").write_text(startup_contents, encoding="utf-8")
    (log_dir / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                "started_at": time.time() - 5,
                "stdout_file": "monitor.stdout.log",
                "stderr_file": "monitor.stderr.log",
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "monitor.stdout.log").write_text(
        "[settle] HISTORICAL_SETTLE_CANARY\n[status] HISTORICAL_STATUS_CANARY\n",
        encoding="utf-8",
    )
    (log_dir / "monitor.stderr.log").write_text(
        "Traceback HISTORICAL_ERROR_CANARY\n",
        encoding="utf-8",
    )

    report = _run_collection_script(tmp_path)

    assert "monitor.stdout.log（历史辅助信息，不作为本次启动证据" in report
    assert "历史错误关键词命中（历史辅助信息，不作为本次启动证据）" in report
    assert "历史 [settle] 行（历史辅助信息，不作为本次启动证据）" in report
    assert "本次启动错误关键词命中" not in report
