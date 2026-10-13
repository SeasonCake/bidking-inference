from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
CHECKER = ROOT / "scripts" / "check_hotfix3_real_drop_pool_quote_runtime.py"
PYTHON = Path(r"C:\Python31315\python.exe")
POWERSHELL_HOSTS = (
    Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    Path(r"C:\Program Files\PowerShell\7\pwsh.exe"),
)


def _function(source: str, name: str, next_name: str | None) -> str:
    start = source.index(f"function {name}")
    if next_name is None:
        end = source.index("\nif (", start)
    else:
        end = source.index(f"function {next_name}", start)
    return source[start:end].rstrip()


def _load_checker():
    spec = importlib.util.spec_from_file_location("capture_holder_checker_test", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _holder_functions(*, timeout_ms: int = 15_000) -> str:
    source = BUILD.read_text(encoding="utf-8-sig")
    functions = "\n\n".join(
        (
            _function(
                source,
                "ConvertTo-ReleaseCaptureProcessArgument",
                "Start-ReleaseCaptureLeaseHolder",
            ),
            _function(
                source,
                "Start-ReleaseCaptureLeaseHolder",
                "Stop-ReleaseCaptureLeaseHolder",
            ),
            _function(source, "Stop-ReleaseCaptureLeaseHolder", None),
        )
    )
    if timeout_ms != 15_000:
        functions = functions.replace("15000", str(timeout_ms))
    return functions


def _write_synthetic_holder(
    tmp_path: Path,
    captures: list[Path],
    contracts: list[dict[str, object]],
) -> Path:
    helper = tmp_path / "synthetic_holder.py"
    helper.write_text(
        textwrap.dedent(
            f"""
            from __future__ import annotations

            import argparse
            from contextlib import ExitStack
            import importlib.util
            import json
            import os
            from pathlib import Path
            import sys
            import time

            spec = importlib.util.spec_from_file_location("release_checker", {str(CHECKER)!r})
            assert spec and spec.loader
            checker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(checker)
            checker._CAPTURE_CONTRACTS = tuple({contracts!r})

            parser = argparse.ArgumentParser()
            parser.add_argument("--hold-captures", action="store_true")
            parser.add_argument("--capture-2536", required=True, type=Path)
            parser.add_argument("--capture-2408", required=True, type=Path)
            parser.add_argument("--capture-2409", required=True, type=Path)
            parser.add_argument("--capture-4501", required=True, type=Path)
            parser.add_argument("--capture-4538", required=True, type=Path)
            args = parser.parse_args()
            paths = (
                args.capture_2536,
                args.capture_2408,
                args.capture_2409,
                args.capture_4501,
                args.capture_4538,
            )
            mode = os.environ.get("BIDKING_TEST_CAPTURE_HOLDER_MODE", "normal")

            def main() -> int:
                try:
                    with ExitStack() as stack:
                        leases = tuple(
                            stack.enter_context(checker._StableCaptureLease.open(path, contract))
                            for path, contract in zip(paths, checker._CAPTURE_CONTRACTS, strict=True)
                        )
                        receipt = {{
                            "schema": checker.CAPTURE_IDENTITY_RECEIPT_SCHEMA,
                            "receipt_version": 1,
                            "status": "pass",
                            "captures": [lease.identity_receipt() for lease in leases],
                        }}
                        print(json.dumps(receipt, separators=(",", ":"), sort_keys=True), flush=True)
                        if not checker._release_capture_holder_requested(sys.stdin.buffer):
                            return 1
                        if mode == "timeout":
                            time.sleep(3)
                            return 1
                        if mode == "child_error":
                            raise RuntimeError("support_safe_child_error")
                        if mode == "identity_drift":
                            os.link(paths[0], paths[0].with_suffix(".hardlink"))
                        for lease in leases:
                            lease.assert_unchanged()
                        if mode == "malformed_ack":
                            print('{{"schema":"wrong","status":"released","path":"forbidden"}}', flush=True)
                            return 0
                        print(
                            json.dumps(
                                {{
                                    "schema": checker.CAPTURE_IDENTITY_RECEIPT_SCHEMA,
                                    "status": "released",
                                }},
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            flush=True,
                        )
                    return 0
                except Exception as exc:
                    print(
                        json.dumps(
                            {{
                                "schema": "bidking_release_availability_receipt_v1",
                                "status": "fail",
                                "error": type(exc).__name__,
                            }},
                            separators=(",", ":"),
                        ),
                        flush=True,
                    )
                    return 1

            raise SystemExit(main())
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return helper


def _run_holder(
    host: Path,
    tmp_path: Path,
    *,
    mode: str,
) -> subprocess.CompletedProcess[str]:
    capture_dir = tmp_path / f"captures {host.stem} {mode}"
    capture_dir.mkdir()
    captures: list[Path] = []
    contracts: list[dict[str, object]] = []
    for index in range(5):
        capture = capture_dir / f"capture {index}.json"
        raw = json.dumps([{"index": index}], separators=(",", ":")).encode("ascii")
        capture.write_bytes(raw)
        captures.append(capture)
        contracts.append(
            {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "prefix_rows": 1,
            }
        )
    helper = _write_synthetic_holder(tmp_path, captures, contracts)
    expected_json = json.dumps(contracts, separators=(",", ":"))
    quoted_paths = ",".join(f"'{path}'" for path in captures)
    functions = _holder_functions(timeout_ms=500 if mode == "timeout" else 15_000)
    script = textwrap.dedent(
        f"""
        $ErrorActionPreference = "Stop"
        $BuildPython = '{PYTHON}'
        $ReleaseAvailabilityCheckerPath = '{helper}'
        $ReleaseCaptureResolvedPaths = @({quoted_paths})
        $parsedExpectedCaptures = '{expected_json}' | ConvertFrom-Json
        $ExpectedReleaseCaptures = @(
            $parsedExpectedCaptures | ForEach-Object {{ $_ }}
        )
        $script:ReleaseCaptureLeaseProcess = $null
        {functions}
        $lease = Start-ReleaseCaptureLeaseHolder
        $script:ReleaseCaptureLeaseProcess = $lease.Process
        $blocked = 0
        foreach ($capture in $ReleaseCaptureResolvedPaths) {{
            try {{
                $stream = [System.IO.File]::Open(
                    $capture,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::ReadWrite
                )
                $stream.Dispose()
            }} catch [System.IO.IOException] {{
                $blocked++
            }}
        }}
        $result = "pass"
        try {{
            Stop-ReleaseCaptureLeaseHolder
        }} catch {{
            $result = "fail"
        }}
        $writable = 0
        foreach ($capture in $ReleaseCaptureResolvedPaths) {{
            try {{
                $stream = [System.IO.File]::Open(
                    $capture,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::ReadWrite
                )
                $stream.Dispose()
                $writable++
            }} catch {{}}
        }}
        [ordered]@{{
            result = $result
            held_write_blocked = $blocked
            post_release_writable = $writable
            receipt_count = @($lease.PublicReceipts).Count
            process_reference_cleared = ($null -eq $script:ReleaseCaptureLeaseProcess)
        }} | ConvertTo-Json -Compress
        """
    ).strip()
    encoded = subprocess.run(
        [
            str(host),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes([Console]::In.ReadToEnd()))",
        ],
        input=script,
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    ).stdout.strip()
    env = os.environ.copy()
    env["BIDKING_TEST_CAPTURE_HOLDER_MODE"] = mode
    return subprocess.run(
        [
            str(host),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=25,
        env=env,
    )


def _receipt(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    assert lines, (completed.returncode, completed.stdout, completed.stderr)
    return json.loads(lines[-1])


def _run_teardown_contract(
    host: Path,
    *,
    mode: str,
) -> subprocess.CompletedProcess[str]:
    functions = _holder_functions()
    script = textwrap.dedent(
        f"""
        $ErrorActionPreference = "Stop"
        {functions}
        $events = [System.Collections.ArrayList]::new()
        $process = [PSCustomObject]@{{
            HasExited = $false
            Disposed = $false
            Mode = '{mode}'
            Events = $events
        }}
        $process | Add-Member -MemberType ScriptMethod -Name Kill -Value {{
            [void]$this.Events.Add("kill")
            if ($this.Mode -eq "kill_failure") {{ throw "synthetic_kill_failure" }}
        }}
        $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {{
            param([int]$Milliseconds)
            [void]$this.Events.Add("wait")
            if ($this.Mode -eq "wait_timeout") {{ return $false }}
            if ($this.Mode -eq "stays_alive") {{ return $true }}
            Start-Sleep -Milliseconds ([Math]::Min(40, $Milliseconds))
            $this.HasExited = $true
            return $true
        }}
        $process | Add-Member -MemberType ScriptMethod -Name Dispose -Value {{
            [void]$this.Events.Add("dispose")
            $this.Disposed = $true
        }}
        $result = "pass"
        try {{
            Complete-ReleaseCaptureLeaseProcessTeardown `
                -Process $process `
                -TimeoutMilliseconds 100
        }} catch {{
            $result = "fail"
        }}
        [ordered]@{{
            result = $result
            has_exited = [bool]$process.HasExited
            disposed = [bool]$process.Disposed
            events = @($events)
        }} | ConvertTo-Json -Compress
        """
    ).strip()
    encoded = subprocess.run(
        [
            str(host),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes([Console]::In.ReadToEnd()))",
        ],
        input=script,
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    ).stdout.strip()
    return subprocess.run(
        [
            str(host),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def test_build_holder_uses_strict_cross_host_control_and_canonical_final_ack() -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    start = _function(
        source,
        "Start-ReleaseCaptureLeaseHolder",
        "Stop-ReleaseCaptureLeaseHolder",
    )
    stop = _function(source, "Stop-ReleaseCaptureLeaseHolder", None)
    teardown = _function(
        source,
        "Complete-ReleaseCaptureLeaseProcessTeardown",
        "Stop-ReleaseCaptureLeaseHolder",
    )

    checker = CHECKER.read_text(encoding="utf-8")
    assert "StandardInputEncoding" not in start
    assert '$process.StandardInput.WriteLine("release")' in stop
    assert "$process.StandardInput.Close()" in stop
    assert "_RELEASE_CAPTURE_HOLDER_CONTROL_FRAMES" in checker
    assert 'b"\\xef\\xbb\\xbfrelease\\r\\n"' in checker
    assert "_release_capture_holder_requested(sys.stdin.buffer)" in checker
    assert (
        '$expectedAck = '
        "'{\"schema\":\"bidking_release_capture_identity_receipt_v1\",\"status\":\"released\"}'"
    ) in stop
    assert "ConvertFrom-Json" not in stop
    assert "$Process.Kill()" in teardown
    assert "$Process.WaitForExit($TimeoutMilliseconds)" in teardown
    assert teardown.rindex("$Process.HasExited") < teardown.index("$Process.Dispose()")


def test_release_control_frames_accept_only_supported_exact_bytes() -> None:
    checker = _load_checker()

    for raw in (
        b"release\n",
        b"release\r\n",
        b"\xef\xbb\xbfrelease\n",
        b"\xef\xbb\xbfrelease\r\n",
    ):
        assert checker._release_capture_holder_requested(io.BytesIO(raw))  # noqa: SLF001
    for raw in (
        b"",
        b"release",
        b" release\n",
        b"release\nextra\n",
        b"\xef\xbb\xbf\xef\xbb\xbfrelease\r\n",
        "release\r\n".encode("utf-16"),
    ):
        assert not checker._release_capture_holder_requested(  # noqa: SLF001
            io.BytesIO(raw)
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell process contract")
@pytest.mark.parametrize("host", POWERSHELL_HOSTS, ids=("winps51", "absolute_pwsh7"))
def test_five_capture_holder_releases_with_final_ack_on_both_hosts(
    host: Path,
    tmp_path: Path,
) -> None:
    assert host.is_file()
    completed = _run_holder(host, tmp_path, mode="normal")
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert _receipt(completed) == {
        "result": "pass",
        "held_write_blocked": 5,
        "post_release_writable": 5,
        "receipt_count": 5,
        "process_reference_cleared": True,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell process contract")
@pytest.mark.parametrize("host", POWERSHELL_HOSTS, ids=("winps51", "absolute_pwsh7"))
@pytest.mark.parametrize(
    "mode",
    ("child_error", "malformed_ack", "identity_drift"),
)
def test_holder_final_ack_fails_closed_on_both_hosts(
    host: Path,
    tmp_path: Path,
    mode: str,
) -> None:
    assert host.is_file()
    completed = _run_holder(host, tmp_path, mode=mode)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    receipt = _receipt(completed)
    assert receipt["result"] == "fail"
    assert receipt["held_write_blocked"] == 5
    assert receipt["post_release_writable"] == 5
    assert receipt["receipt_count"] == 5
    assert receipt["process_reference_cleared"] is True
    combined = completed.stdout + completed.stderr
    assert "forbidden" not in combined.casefold()
    assert str(tmp_path).casefold() not in combined.casefold()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell process contract")
@pytest.mark.parametrize("host", POWERSHELL_HOSTS, ids=("winps51", "absolute_pwsh7"))
def test_timeout_teardown_repeatedly_returns_only_after_all_five_handles_release(
    host: Path,
    tmp_path: Path,
) -> None:
    assert host.is_file()
    for attempt in range(6):
        attempt_root = tmp_path / f"attempt-{attempt}"
        attempt_root.mkdir()
        completed = _run_holder(host, attempt_root, mode="timeout")
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        receipt = _receipt(completed)
        assert receipt == {
            "result": "fail",
            "held_write_blocked": 5,
            "post_release_writable": 5,
            "receipt_count": 5,
            "process_reference_cleared": True,
        }


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell process contract")
@pytest.mark.parametrize("host", POWERSHELL_HOSTS, ids=("winps51", "absolute_pwsh7"))
@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        (
            "delayed_exit",
            {
                "result": "pass",
                "has_exited": True,
                "disposed": True,
                "events": ["kill", "wait", "dispose"],
            },
        ),
        (
            "kill_failure",
            {
                "result": "fail",
                "has_exited": False,
                "disposed": False,
                "events": ["kill"],
            },
        ),
        (
            "wait_timeout",
            {
                "result": "fail",
                "has_exited": False,
                "disposed": False,
                "events": ["kill", "wait"],
            },
        ),
        (
            "stays_alive",
            {
                "result": "fail",
                "has_exited": False,
                "disposed": False,
                "events": ["kill", "wait"],
            },
        ),
    ),
)
def test_teardown_wait_contract_fails_closed_on_both_hosts(
    host: Path,
    mode: str,
    expected: dict[str, object],
) -> None:
    assert host.is_file()
    completed = _run_teardown_contract(host, mode=mode)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert _receipt(completed) == expected
