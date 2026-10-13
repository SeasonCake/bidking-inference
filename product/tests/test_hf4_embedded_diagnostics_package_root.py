from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COLLECT = ROOT / "scripts" / "collect_diagnostics.ps1"
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
KNOWN_FOLDER_BEGIN = "# BIDKING_TEST_KNOWN_FOLDER_BEGIN"
KNOWN_FOLDER_END = "# BIDKING_TEST_KNOWN_FOLDER_END"


def _ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _instrument_known_folders(
    source: str,
    *,
    local_appdata: Path,
    documents: Path,
) -> str:
    assert source.count(KNOWN_FOLDER_BEGIN) == 1
    assert source.count(KNOWN_FOLDER_END) == 1
    injected = "\n".join(
        (
            KNOWN_FOLDER_BEGIN,
            "function Get-BidKingKnownFolderPath($kind) {",
            "  if ([string]$kind -ceq 'LocalApplicationData') { "
            f"return {_ps_quote(local_appdata)} }}",
            "  if ([string]$kind -ceq 'Documents') { "
            f"return {_ps_quote(documents)} }}",
            "  throw 'unknown test Known Folder kind'",
            "}",
            KNOWN_FOLDER_END,
        )
    )
    start = source.index(KNOWN_FOLDER_BEGIN)
    finish = source.index(KNOWN_FOLDER_END, start) + len(KNOWN_FOLDER_END)
    return source[:start] + injected + source[finish:]


def _prepare_package(
    tmp_path: Path,
    *,
    name: str = "package with spaces",
) -> tuple[Path, Path, Path, Path]:
    package_root = tmp_path / name
    scripts = package_root / "scripts"
    scripts.mkdir(parents=True)
    local_appdata = tmp_path / f"{name}-local-appdata"
    documents = tmp_path / f"{name}-documents"
    local_appdata.mkdir()
    documents.mkdir()
    source = _instrument_known_folders(
        COLLECT.read_text(encoding="utf-8-sig"),
        local_appdata=local_appdata,
        documents=documents,
    )
    script_path = scripts / "collect_diagnostics.ps1"
    script_path.write_text(source, encoding="utf-8-sig")
    (package_root / "BUILD_MANIFEST.txt").write_text(
        "Version: 0.3.2-hotfix4\n",
        encoding="utf-8",
    )
    return package_root, script_path, local_appdata, documents


def _run_collect(
    shell: str,
    *,
    package_root: Path,
    script_path: Path,
    mode: str,
    environment_root: str | Path | None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if mode == "file":
        invocation = f"& {_ps_quote(script_path)}"
    elif mode == "memory":
        invocation = (
            f"$source=[IO.File]::ReadAllText({_ps_quote(script_path)});"
            "$entry=[ScriptBlock]::Create($source);"
            "& $entry"
        )
    else:  # pragma: no cover - test helper contract
        raise AssertionError(mode)

    command = (
        "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);"
        "$OutputEncoding=[Console]::OutputEncoding;"
        + invocation
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key.casefold() != "bidking_package_root"
    }
    env.update(
        {
            "BIDKING_DIAG_NOEXPLORER": "1",
            "BIDKING_DIAG_NONINTERACTIVE": "1",
            "BIDKING_DIAG_SKIP_NETWORK": "1",
        }
    )
    if environment_root is not None:
        env["BIDKING_PACKAGE_ROOT"] = str(environment_root)
    return subprocess.run(
        [
            str(Path(shell).resolve()),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=cwd or package_root.parent,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _assert_failed_before_output(
    completed: subprocess.CompletedProcess[str],
    documents: Path,
) -> None:
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert completed.stdout.count("[进度 1/7]") == 1
    assert "[进度 2/7]" not in completed.stdout
    assert completed.stdout.count("[失败] 诊断收集未完成") == 1
    assert completed.stderr == ""
    for private_detail in (
        "package root environment",
        "BUILD_MANIFEST",
        "diagnostic script",
    ):
        assert private_detail not in completed.stdout
    assert not (documents / "BidKingLive").exists()


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
@pytest.mark.parametrize("mode", ("file", "memory"))
def test_collect_diagnostics_file_and_in_memory_package_root_positive_controls(
    tmp_path: Path,
    shell_name: str,
    mode: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    package_root, script_path, _local_appdata, documents = _prepare_package(tmp_path)
    environment_root = package_root
    cwd = package_root if mode == "memory" else package_root.parent

    completed = _run_collect(
        shell,
        package_root=package_root,
        script_path=script_path,
        mode=mode,
        environment_root=environment_root,
        cwd=cwd,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    expected = [f"[进度 {step}/7]" for step in range(1, 8)]
    assert [completed.stdout.index(marker) for marker in expected] == sorted(
        completed.stdout.index(marker) for marker in expected
    )
    assert completed.stdout.count("[进度 ") == 7
    assert "[完成] 诊断报告已安全生成" in completed.stdout
    assert "[失败] 诊断收集未完成" not in completed.stdout
    reports = list((documents / "BidKingLive" / "exports").glob("BidKing诊断_*.txt"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8-sig")
    assert str(package_root) not in report
    assert "<package-root>" in report
    assert not list(package_root.glob("BidKing诊断_*.txt"))


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
@pytest.mark.parametrize("invalid_kind", ("missing", "empty", "relative", "foreign"))
def test_in_memory_diagnostics_reject_missing_malformed_and_foreign_environment_root(
    tmp_path: Path,
    shell_name: str,
    invalid_kind: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    package_root, script_path, _local_appdata, documents = _prepare_package(tmp_path)
    if invalid_kind == "missing":
        environment_root: str | Path | None = None
    elif invalid_kind == "empty":
        environment_root = ""
    elif invalid_kind == "relative":
        environment_root = "relative-package"
    else:
        foreign_root = tmp_path / "package with spaces-foreign"
        foreign_root.mkdir()
        (foreign_root / "BUILD_MANIFEST.txt").write_text("foreign\n", encoding="utf-8")
        environment_root = foreign_root

    completed = _run_collect(
        shell,
        package_root=package_root,
        script_path=script_path,
        mode="memory",
        environment_root=environment_root,
        cwd=package_root,
    )

    _assert_failed_before_output(completed, documents)


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_in_memory_diagnostics_reject_reparse_package_root(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    cmd = shutil.which("cmd.exe")
    if not shell or not cmd:
        pytest.skip("Windows command hosts unavailable")
    package_root, script_path, _local_appdata, documents = _prepare_package(tmp_path)
    junction = tmp_path / "package-junction"
    linked = subprocess.run(
        [cmd, "/d", "/c", "mklink", "/J", str(junction), str(package_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    assert linked.returncode == 0, linked.stdout + linked.stderr

    completed = _run_collect(
        shell,
        package_root=package_root,
        script_path=script_path,
        mode="memory",
        environment_root=junction,
        cwd=package_root,
    )

    _assert_failed_before_output(completed, documents)


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_in_memory_diagnostics_reject_hardlinked_package_manifest(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    package_root, script_path, _local_appdata, documents = _prepare_package(tmp_path)
    manifest = package_root / "BUILD_MANIFEST.txt"
    os.link(manifest, package_root / "BUILD_MANIFEST.shadow")

    completed = _run_collect(
        shell,
        package_root=package_root,
        script_path=script_path,
        mode="memory",
        environment_root=package_root,
        cwd=package_root,
    )

    _assert_failed_before_output(completed, documents)


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_file_diagnostics_reject_foreign_environment_and_hardlinked_script(
    tmp_path: Path,
    shell_name: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    package_root, script_path, _local_appdata, documents = _prepare_package(tmp_path)
    foreign_root = tmp_path / "foreign-package"
    foreign_root.mkdir()

    foreign = _run_collect(
        shell,
        package_root=package_root,
        script_path=script_path,
        mode="file",
        environment_root=foreign_root,
    )
    _assert_failed_before_output(foreign, documents)

    misplaced_script = package_root / "collect_diagnostics.ps1"
    shutil.copyfile(script_path, misplaced_script)
    misplaced = _run_collect(
        shell,
        package_root=package_root,
        script_path=misplaced_script,
        mode="file",
        environment_root=None,
    )
    _assert_failed_before_output(misplaced, documents)

    os.link(script_path, package_root / "collect_diagnostics.shadow.ps1")
    hardlinked = _run_collect(
        shell,
        package_root=package_root,
        script_path=script_path,
        mode="file",
        environment_root=None,
    )
    _assert_failed_before_output(hardlinked, documents)


def test_diagnostic_package_root_locator_has_no_cwd_or_package_log_fallback() -> None:
    source = COLLECT.read_text(encoding="utf-8-sig")

    assert source.count("$env:BIDKING_PACKAGE_ROOT") == 1
    assert "Resolve-BidKingDiagnosticPackageRoot" in source
    assert 'Provider.Name -cne "FileSystem"' in source
    assert '"BUILD_MANIFEST.txt"' in source
    assert 'Join-Path $Repo "data\\logs\\live"' not in source
    assert 'GetFolderPath("Desktop")' not in source
    assert "[System.IO.Directory]::GetCurrentDirectory()" not in source


def test_native_diagnostics_gate_runs_after_manifest_before_candidate_success_gates() -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    diagnostic_command = "run $launcherNativeSmokeReceiptPath $lifecycleGeneration"
    manifest_ready = (
        'Write-CandidateBuildManifest\n'
        'Write-Host "  + BUILD_MANIFEST.txt '
    )
    root_exe_success = (
        'Write-Host "  + BidKingLive.exe + 使用说明.txt + 公共大数据平台.html '
        '+ 其他启动方式/(.bat)"'
    )
    local_integrity_sign = "$LocalIntegrityBuilder sign"
    canonical_zip = "--write-canonical-zip $Stage $Zip"

    assert source.count(diagnostic_command) == 1
    assert source.count("function Write-CandidateBuildManifest") == 1
    assert sum(
        line.strip() == "Write-CandidateBuildManifest" for line in source.splitlines()
    ) == 4
    assert (
        source.index(manifest_ready)
        < source.index(diagnostic_command)
        < source.index(root_exe_success)
        < source.index(local_integrity_sign)
        < source.index(canonical_zip)
    )

    diagnostic_gate_end = (
        source.index(
            'Write-Host "  + native lifecycle 合同与公版诊断入口实跑通过"',
            source.index(diagnostic_command),
        )
        + len('Write-Host "  + native lifecycle 合同与公版诊断入口实跑通过"')
    )
    diagnostic_gate = source[
        source.rindex("if ($Encrypt) {", 0, source.index(diagnostic_command)) :
        diagnostic_gate_end
    ]
    assert "Set-Content" not in diagnostic_gate
    assert "WriteAllText" not in diagnostic_gate
    assert 'Join-Path $Stage "BUILD_MANIFEST.txt"' not in diagnostic_gate
    for cleanup_contract in (
        "Remove-Item Env:\\BIDKING_DIAG_SKIP_NETWORK",
        "Remove-Item Env:\\BIDKING_DIAG_NOEXPLORER",
        "Remove-Item Env:\\BIDKING_DIAG_NONINTERACTIVE",
        "Pop-Location",
        "Remove-Item -LiteralPath $diagnosticReport.FullName -Force",
        "if (-not $launcherNativeSmokeOk)",
    ):
        assert cleanup_contract in diagnostic_gate

    assert "BIDKING_NATIVE_DIAGNOSTIC_REASON=" not in diagnostic_gate
    assert "$launcherNativeSmokeExit -eq 0" in diagnostic_gate
    assert '$launcherNativeSmokeCleanupStatus -ceq "deleted"' in diagnostic_gate
    assert "Invoke-NativeDiagnosticSmokeReceiptLease" in diagnostic_gate
    assert "Remove-Item -LiteralPath $launcherNativeSmokeReceiptPath" not in diagnostic_gate


def test_embedded_diagnostics_has_explicit_success_exit_after_safe_report() -> None:
    source = COLLECT.read_text(encoding="utf-8-sig")

    assert "trap {" in source and "exit 1" in source
    assert source.rstrip().endswith("exit 0")


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
@pytest.mark.parametrize("invalid_manifest", ("missing", "directory"))
def test_in_memory_build_gate_requires_regular_final_manifest_on_both_hosts(
    tmp_path: Path,
    shell_name: str,
    invalid_manifest: str,
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    package_root, script_path, _local_appdata, documents = _prepare_package(tmp_path)
    manifest = package_root / "BUILD_MANIFEST.txt"
    manifest.unlink()
    if invalid_manifest == "directory":
        manifest.mkdir()

    completed = _run_collect(
        shell,
        package_root=package_root,
        script_path=script_path,
        mode="memory",
        environment_root=package_root,
        cwd=package_root,
    )

    _assert_failed_before_output(completed, documents)
