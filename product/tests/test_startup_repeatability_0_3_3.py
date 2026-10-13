from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
STARTUP = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
COLLECT_DIAGNOSTICS = ROOT / "scripts" / "collect_diagnostics.ps1"
ACTIVATION_DIAGNOSTIC = ROOT / "scripts" / "activation_transport_diagnostic.bat"
TRUST_BOOTSTRAP = ROOT / "native" / "bidking_trust_bootstrap" / "src" / "main.rs"
CLIENT_LIFECYCLE = ROOT / "native" / "bidking_kernel" / "src" / "client_lifecycle.rs"


def test_bytecode_checker_distinguishes_default_from_double_defense(
    tmp_path: Path,
) -> None:
    module = tmp_path / "packaged_probe.py"
    module.write_text("VALUE = 7\n", encoding="utf-8")

    default = subprocess.run(
        [sys.executable, "-c", "import packaged_probe; assert packaged_probe.VALUE == 7"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert default.returncode == 0, default.stderr
    caches = list(tmp_path.rglob("*.pyc"))
    assert caches, "positive control must prove that this fixture can write bytecode"
    for cache in caches:
        cache.unlink()
    for directory in sorted(
        (path for path in tmp_path.rglob("__pycache__") if path.is_dir()),
        reverse=True,
    ):
        directory.rmdir()

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    protected = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import packaged_probe; assert packaged_probe.VALUE == 7",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert protected.returncode == 0, protected.stderr
    assert list(tmp_path.rglob("*.pyc")) == []
    assert list(tmp_path.rglob("__pycache__")) == []


def test_all_packaged_python_entrypoints_have_cli_and_environment_defense() -> None:
    startup = STARTUP.read_text(encoding="utf-8-sig")
    build = BUILD.read_text(encoding="utf-8-sig")
    collect = COLLECT_DIAGNOSTICS.read_text(encoding="utf-8-sig")
    activation = ACTIVATION_DIAGNOSTIC.read_text(encoding="utf-8-sig")
    trust = TRUST_BOOTSTRAP.read_text(encoding="utf-8")
    lifecycle = CLIENT_LIFECYCLE.read_text(encoding="utf-8")

    assert '$env:PYTHONDONTWRITEBYTECODE = "1"' in startup
    assert "& $PythonPath -E -B $ComponentBootstrap" in startup
    assert "& $PythonPath -E -B $PackageIdentityHelper" in startup
    assert "& $PythonPath -E -B $WssMonitor --prepare-ca" in startup
    assert "& $PythonPath -E -B -c" in startup
    assert '$MonitorArgs = @(\n    "-B",' in startup
    assert '$OverlayArgs = @(\n    "-B",' in startup

    assert '$env:PYTHONDONTWRITEBYTECODE = "1"' in collect
    assert "& $py -B --version" in collect
    assert 'set "PYTHONDONTWRITEBYTECODE=1"' in activation
    assert '"%PYTHON%" -B "%SCRIPT%"' in activation

    # The generated public launcher/diagnostics shells are package members too.
    assert build.count('$env:PYTHONDONTWRITEBYTECODE = "1"') >= 2
    assert trust.count('.env("PYTHONDONTWRITEBYTECODE", "1")') >= 1
    assert lifecycle.count('.env("PYTHONDONTWRITEBYTECODE", "1")') >= 1

    assert '"startup_failure.log"' in collect
    assert "bidking_public_startup_failure_v1" in collect
    assert "[最近 native 启动失败；可能属于历史启动]" in collect


def test_native_bootstrap_propagates_allowlisted_integrity_reason() -> None:
    trust = TRUST_BOOTSTRAP.read_text(encoding="utf-8")
    build = BUILD.read_text(encoding="utf-8-sig")

    assert "BIDKING_STARTUP_FAILURE=" in trust
    assert "product_failure_from_exit" in trust
    assert '131 => Some("integrity_coverage_mismatch")' in trust
    assert '111 => Some("integrity_named_stream_rejected")' in trust
    assert '"integrity_coverage_mismatch"' in trust
    assert '"integrity_named_stream_rejected"' in trust
    assert "client_exit_{code}" in trust  # non-integrity exits remain classified
    assert "程序包在运行后产生了额外文件" in trust
    assert "程序文件的附加数据格式不受支持" in trust

    # The alternate packaged PowerShell entrypoint must preserve the same exact
    # machine-readable reason instead of collapsing it to a generic exit 6.
    assert "BIDKING_STARTUP_FAILURE=" in build
    assert "_INTEGRITY_FAILURE_EXITS" in build
    assert "_fail_with_safe_reason(_code)" in build
    assert "_fail_with_safe_reason(_namespace_code)" in build


def test_native_and_python_integrity_reason_exit_maps_are_exactly_aligned() -> None:
    trust = TRUST_BOOTSTRAP.read_text(encoding="utf-8")
    build = BUILD.read_text(encoding="utf-8-sig")

    rust_pairs = {
        reason: int(code)
        for code, reason in re.findall(
            r'(1[0-3][0-9]) => Some\("(integrity_[a-z_]+)"\)', trust
        )
    }
    native_python_pairs = {
        reason: int(code)
        for reason, code in re.findall(
            r'"(integrity_[a-z_]+)": (1[0-3][0-9]),', trust
        )
    }
    public_python_pairs = {
        reason: int(code)
        for reason, code in re.findall(
            r'"(integrity_[a-z_]+)": (1[0-3][0-9]),', build
        )
    }
    assert len(rust_pairs) == 38
    assert native_python_pairs == rust_pairs
    assert public_python_pairs == rust_pairs
    assert rust_pairs["integrity_named_stream_rejected"] == 111
    assert rust_pairs["integrity_coverage_mismatch"] == 131


def test_monitor_conflicts_have_stable_safe_classes_and_recovery_boundary() -> None:
    startup = STARTUP.read_text(encoding="utf-8-sig")
    trust = TRUST_BOOTSTRAP.read_text(encoding="utf-8")

    for classification in (
        "monitor_foreign_lock",
        "monitor_identity_unknown",
        "monitor_old_package_game_active",
        "monitor_old_package_changed",
        "monitor_old_package_stop_failed",
        "monitor_foreign_process",
    ):
        assert classification in startup
        assert classification in trust

    assert "BIDKING_STARTUP_FAILURE=" in startup
    assert "不会终止身份不明的进程" in startup
    assert "完全退出游戏和所有旧版 Hero Ref" in startup
    # Unknown/foreign identities must still return before the only stale-package
    # cleanup path that is permitted to call Stop-Process.
    foreign_guard = startup.index(
        '$PreStartIdentity.State -in @("foreign", "unknown")'
    )
    stale_cleanup = startup.index("$StaleCleanup = Invoke-BidKingStalePackageCleanup")
    assert foreign_guard < stale_cleanup


def test_monitor_conflict_classifier_returns_stable_exit_without_process_action(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise AssertionError("Windows PowerShell is required")
    startup = STARTUP.read_text(encoding="utf-8-sig")
    match = re.search(
        r"function Write-BidKingStartupFailure\([^\n]+\) \{.*?^\}",
        startup,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    assert "Stop-Process" not in match.group(0)
    status = tmp_path / "startup_status.log"
    body = (
        "function Write-BidKingStartupStatus {\n"
        "  param([string]$StatusPath,[string]$Event)\n"
        "  Add-Content -LiteralPath $StatusPath -Value $Event -Encoding utf8\n"
        "}\n"
        + match.group(0)
        + "\n$StartupStatus='"
        + str(status).replace("'", "''")
        + "'\n"
        + "$codes=@(\n"
        + "  'monitor_foreign_lock','monitor_identity_unknown',\n"
        + "  'monitor_old_package_game_active','monitor_old_package_changed',\n"
        + "  'monitor_old_package_stop_failed','monitor_old_package_lock_changed',\n"
        + "  'monitor_old_package_conflict','monitor_foreign_process'\n"
        + ")\n"
        + "$rows=@($codes|ForEach-Object{\n"
        + "  [pscustomobject]@{code=$_;exit=(Write-BidKingStartupFailure $_ 'probe')}\n"
        + "})\n"
        + "$rows|ConvertTo-Json -Compress\n"
    )
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            body,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    rows = json.loads([line for line in completed.stdout.splitlines() if line][-1])
    assert {row["code"]: row["exit"] for row in rows} == {
        "monitor_foreign_lock": 180,
        "monitor_identity_unknown": 181,
        "monitor_old_package_game_active": 182,
        "monitor_old_package_changed": 183,
        "monitor_old_package_stop_failed": 184,
        "monitor_old_package_lock_changed": 185,
        "monitor_old_package_conflict": 186,
        "monitor_foreign_process": 187,
    }
    status_text = status.read_text(encoding="utf-8-sig")
    assert status_text.count("startup_failure code=") == 8
    assert str(tmp_path) not in status_text


def _package_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    import hashlib

    snapshot: dict[str, tuple[int, str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        snapshot[path.relative_to(root).as_posix()] = (
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )
    return snapshot


def _verify_formal_package(python: Path, root: Path) -> tuple[bool, str]:
    code = (
        "import importlib.util,pathlib,sys;"
        "r=pathlib.Path(sys.argv[1]);"
        "p=tuple((r/'src').glob('bidking_kernel*.pyd'));"
        "assert len(p)==1;"
        "s=importlib.util.spec_from_file_location('bidking_kernel',p[0]);"
        "m=importlib.util.module_from_spec(s);sys.modules['bidking_kernel']=m;"
        "s.loader.exec_module(m);"
        "ok,reason,count,manifest=m.verify_local_integrity(str(r));"
        "print(('1' if ok else '0')+'|'+reason+'|'+str(count)+'|'+manifest)"
    )
    completed = subprocess.run(
        [str(python), "-E", "-s", "-S", "-B", "-c", code, str(root)],
        cwd=root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    marker = completed.stdout.strip().splitlines()[-1]
    ok, reason, _count, _manifest = marker.split("|", 3)
    return ok == "1", reason


def test_formal_package_two_startup_import_cycles_are_immutable_and_pyc_tamper_fails(
    tmp_path: Path,
) -> None:
    archive_value = os.environ.get("BIDKING_FORMAL_PACKAGE_ZIP", "")
    if not archive_value:
        import pytest

        pytest.skip("set BIDKING_FORMAL_PACKAGE_ZIP for the formal package-shaped gate")
    archive = Path(archive_value)
    assert archive.is_file()
    root = tmp_path / "formal-package"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(root)

    python = root / "runtime" / "python" / "python.exe"
    pythonw = root / "runtime" / "python" / "pythonw.exe"
    monitor = root / "scripts" / "local_compat_monitor.py"
    overlay = root / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    identity = root / "scripts" / "package_generation_identity.py"
    assert all(path.is_file() for path in (python, pythonw, monitor, overlay, identity))
    assert list(root.rglob("*.pyc")) == []
    assert list(root.rglob("__pycache__")) == []
    clean_ok, clean_reason = _verify_formal_package(python, root)
    assert (clean_ok, clean_reason) == (True, "ok")
    baseline = _package_snapshot(root)

    env = {**os.environ, "BIDKING_PACKAGE_ROOT": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    commands = (
        [
            str(python),
            "-E",
            "-B",
            "-c",
            "import numpy,psutil,pydantic;"
            "from cryptography.hazmat.primitives.ciphers.aead import AESGCM;"
            "print('PROBE_OK')",
        ],
        [
            str(python),
            "-E",
            "-B",
            str(identity),
            "--package-root",
            str(root),
            "--monitor",
            str(monitor),
            "--runtime",
            str(pythonw),
        ],
        [str(python), "-B", str(monitor), "--help"],
        [str(python), "-B", str(overlay), "--help"],
    )
    for _cycle in ("first_start_close", "second_start_close"):
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            assert completed.returncode == 0, completed.stderr[-2000:]
        assert _package_snapshot(root) == baseline
        assert list(root.rglob("*.pyc")) == []
        assert list(root.rglob("__pycache__")) == []

    after_ok, after_reason = _verify_formal_package(python, root)
    assert (after_ok, after_reason) == (True, "ok")

    cache = root / "scripts" / "__pycache__"
    cache.mkdir()
    (cache / "startup_repeatability_probe.pyc").write_bytes(b"real-extra-bytecode")
    tampered_ok, tampered_reason = _verify_formal_package(python, root)
    assert (tampered_ok, tampered_reason) == (
        False,
        "integrity_coverage_mismatch",
    )
