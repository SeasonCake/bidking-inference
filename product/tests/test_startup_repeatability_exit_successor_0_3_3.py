from __future__ import annotations

import base64
from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import subprocess
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
BLOCKED_SOURCE = "67ff0562a7dda6184392501d608b7961c86a7b48"


class _Lease:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _Kernel:
    def __init__(
        self,
        *,
        verify: tuple[bool, str] = (True, "ok"),
        recheck: tuple[bool, str] = (True, "ok"),
        release: tuple[bool, str] = (True, "ok"),
    ) -> None:
        self.verify_result = verify
        self.recheck_result = recheck
        self.release_result = release
        self.release_calls = 0

    def verify_local_integrity(self, _root: str) -> tuple[bool, str, int, str]:
        ok, reason = self.verify_result
        return ok, reason, 0, "successor-manifest"

    def recheck_local_integrity(self, _root: str) -> tuple[bool, str]:
        return self.recheck_result

    def release_local_integrity(self) -> tuple[bool, str]:
        self.release_calls += 1
        return self.release_result


class _Guard:
    def __init__(self, lease: _Lease) -> None:
        self.lease = lease

    def begin_namespace_lease(self, _root: Path, *, timing_event=None) -> _Lease:
        return self.lease


def _extract_public_python(source: str) -> tuple[str, str]:
    bootstrap_marker = '$integrityBootstrap = @"\n'
    bootstrap_start = source.index(bootstrap_marker) + len(bootstrap_marker)
    bootstrap_end = source.index('\n"@\n$runnerCode = ', bootstrap_start)
    runner_marker = '$runnerCode = $integrityBootstrap + "`n" + @"\n'
    runner_start = source.index(runner_marker, bootstrap_end) + len(runner_marker)
    runner_end = source.index('\n"@\n$pythonBootstrapEntry', runner_start)
    bootstrap = source[bootstrap_start:bootstrap_end]
    return bootstrap[bootstrap.index("_kernel = None") :], source[
        runner_start:runner_end
    ]


def _run_bootstrap(
    tmp_path: Path,
    kernel: _Kernel,
    *,
    source: str,
) -> tuple[int | None, str, dict[str, object], _Lease]:
    bootstrap, _runner = _extract_public_python(source)
    candidate = tmp_path / "src" / "bidking_kernel.successor.pyd"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"successor fixture")
    lease = _Lease()

    def load_exact(name: str, _path: object) -> object:
        if name == "bidking_kernel":
            return kernel
        if name == "_bidking_local_integrity_namespace_guard":
            return _Guard(lease)
        raise AssertionError(name)

    scope: dict[str, object] = {
        "pathlib": __import__("pathlib"),
        "sys": sys,
        "_load_exact": load_exact,
    }
    previous_argv = sys.argv
    stdout = io.StringIO()
    exit_code: int | None = None
    try:
        sys.argv = ["successor-bootstrap", str(tmp_path)]
        with redirect_stdout(stdout):
            try:
                exec(compile(bootstrap, "<public-integrity-bootstrap>", "exec"), scope)
            except SystemExit as exc:
                exit_code = int(exc.code)
    finally:
        sys.argv = previous_argv
    return exit_code, stdout.getvalue(), scope, lease


def _git_file(commit: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def test_blocked_source_known_fail_collapses_verify_reason(tmp_path: Path) -> None:
    source = _git_file(BLOCKED_SOURCE, "scripts/build_live_portable.ps1")
    kernel = _Kernel(verify=(False, "integrity_coverage_mismatch"))
    exit_code, stdout, _scope, _lease = _run_bootstrap(
        tmp_path, kernel, source=source
    )
    assert "BIDKING_STARTUP_FAILURE=integrity_coverage_mismatch" in stdout
    assert exit_code == 6


def test_successor_preserves_verify_and_recheck_reasons_after_cleanup(
    tmp_path: Path,
) -> None:
    source = BUILD.read_text(encoding="utf-8-sig")

    verify_kernel = _Kernel(verify=(False, "integrity_coverage_mismatch"))
    verify_code, verify_stdout, _scope, verify_lease = _run_bootstrap(
        tmp_path / "verify", verify_kernel, source=source
    )
    assert "BIDKING_STARTUP_FAILURE=integrity_coverage_mismatch" in verify_stdout
    assert verify_code == 131
    assert verify_kernel.release_calls == 1
    assert verify_lease.close_calls == 0

    recheck_kernel = _Kernel(recheck=(False, "integrity_stream_namespace_changed"))
    recheck_code, recheck_stdout, _scope, recheck_lease = _run_bootstrap(
        tmp_path / "recheck", recheck_kernel, source=source
    )
    assert "BIDKING_STARTUP_FAILURE=integrity_stream_namespace_changed" in recheck_stdout
    assert recheck_code == 137
    assert recheck_kernel.release_calls == 1
    assert recheck_lease.close_calls == 1


def test_successor_keeps_unknown_failures_closed_at_six(tmp_path: Path) -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    kernel = _Kernel(verify=(False, "integrity_unrecognized_reason"))
    exit_code, stdout, _scope, _lease = _run_bootstrap(
        tmp_path, kernel, source=source
    )
    assert "BIDKING_STARTUP_FAILURE=" not in stdout
    assert "BIDKING_LOCAL_INTEGRITY=bootstrap_failed" in stdout
    assert exit_code == 6


def test_successor_runner_preserves_release_reason(tmp_path: Path) -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    kernel = _Kernel(release=(False, "integrity_handle_release_failed"))
    initial_code, _stdout, scope, lease = _run_bootstrap(
        tmp_path, kernel, source=source
    )
    assert initial_code is None
    _bootstrap, runner = _extract_public_python(source)
    launcher = types.ModuleType("_client_launcher")
    launcher.main = lambda: 0  # type: ignore[attr-defined]
    previous = sys.modules.get("_client_launcher")
    sys.modules["_client_launcher"] = launcher
    release_code: int | None = None
    try:
        try:
            exec(compile(runner, "<public-runner>", "exec"), scope)
        except SystemExit as exc:
            release_code = int(exc.code)
    finally:
        if previous is None:
            sys.modules.pop("_client_launcher", None)
        else:
            sys.modules["_client_launcher"] = previous
    assert release_code == 138
    assert lease.close_calls == 1


def _extract_public_shell(source: str, name: str, end_marker: str) -> str:
    marker = f"${name} = @'\n"
    start = source.index(marker) + len(marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _powershell() -> str:
    path = shutil.which("powershell.exe") or shutil.which("powershell")
    assert path is not None
    return path


def _parse_with_winps(source: str) -> subprocess.CompletedProcess[str]:
    payload = base64.b64encode(source.encode("utf-8")).decode("ascii")
    probe = (
        "$src=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
        + payload
        + "'));$tokens=$null;$errors=$null;"
        + "[Management.Automation.Language.Parser]::ParseInput($src,[ref]$tokens,[ref]$errors)|Out-Null;"
        + "if($errors.Count){$errors|ForEach-Object{[Console]::Error.WriteLine($_.Message)};exit 1};exit 0"
    )
    encoded = base64.b64encode(probe.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_successor_generated_public_shells_are_winps_parseable() -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    launcher = _extract_public_shell(
        source,
        "publicLauncherShell",
        '\n\'@.Replace("__PUBLIC_PROFILE__", $PublicDiagnosticProfile)',
    ).replace("__PUBLIC_PROFILE__", "public-safe")
    diagnostics = _extract_public_shell(
        source,
        "publicDiagnosticsShell",
        '\n\'@.Replace("__BIDKING_INTEGRITY_BOOTSTRAP__", $integrityBootstrap)',
    )
    for shell in (launcher, diagnostics):
        completed = _parse_with_winps(shell)
        assert completed.returncode == 0, completed.stderr


def _run_public_tail(
    tmp_path: Path,
    *,
    child_exit: int,
    tail: str,
    prefix: str,
) -> subprocess.CompletedProcess[str]:
    fake_python = tmp_path / "python.cmd"
    fake_python.parent.mkdir(parents=True, exist_ok=True)
    fake_python.write_text(
        f"@echo off\r\nexit /b {child_exit}\r\n", encoding="ascii"
    )
    body = (
        "$PythonPath='"
        + str(fake_python).replace("'", "''")
        + "';"
        + prefix
        + "\n"
        + tail
    )
    encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_successor_public_diagnostics_preserves_integrity_and_closes_unknown(
    tmp_path: Path,
) -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    shell = _extract_public_shell(
        source,
        "publicDiagnosticsShell",
        '\n\'@.Replace("__BIDKING_INTEGRITY_BOOTSTRAP__", $integrityBootstrap)',
    )
    tail = shell[shell.index("& $PythonPath -E -s -S -B -c") :]
    prefix = "$pythonBootstrapEntry='successor';$diagnosticsPayload='successor';$Repo='successor';"
    for child_exit in (101, 131, 137, 138):
        completed = _run_public_tail(
            tmp_path / str(child_exit),
            child_exit=child_exit,
            tail=tail,
            prefix=prefix,
        )
        assert completed.returncode == child_exit
        assert "local_integrity_failed" in completed.stdout
    unknown = _run_public_tail(
        tmp_path / "unknown", child_exit=7, tail=tail, prefix=prefix
    )
    assert unknown.returncode == 6
    clean = _run_public_tail(
        tmp_path / "clean", child_exit=0, tail=tail, prefix=prefix
    )
    assert clean.returncode == 0


def test_successor_public_launcher_preserves_integrity_exit(tmp_path: Path) -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    shell = _extract_public_shell(
        source,
        "publicLauncherShell",
        '\n\'@.Replace("__PUBLIC_PROFILE__", $PublicDiagnosticProfile)',
    )
    tail = shell[shell.index("& $PythonPath @runnerArgs") :]
    prefix = (
        "$runnerArgs=@();"
        + "function Show-LaunchError([string]$Message){[Console]::Out.WriteLine('SHOW_LAUNCH_ERROR')}"
    )
    for child_exit in (111, 131, 137, 138):
        completed = _run_public_tail(
            tmp_path / str(child_exit),
            child_exit=child_exit,
            tail=tail,
            prefix=prefix,
        )
        assert completed.returncode == child_exit
        assert "local_integrity_failed" in completed.stdout
        assert "SHOW_LAUNCH_ERROR" in completed.stdout
