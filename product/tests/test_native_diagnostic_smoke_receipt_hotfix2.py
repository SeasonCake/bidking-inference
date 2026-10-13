from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_native_diagnostic_smoke.py"
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
GENERATION = "a" * 64
SCHEMA = "bidking_native_diagnostic_smoke_receipt_v1"
CANONICAL_OK = (
    b'{"reason":"ok","schema":"bidking_native_diagnostic_smoke_receipt_v1",'
    b'"status":0}\n'
)
OLD_STDOUT_PATTERN = re.compile(
    rb"(?m)^BIDKING_NATIVE_DIAGNOSTIC_REASON=(?P<reason>[a-z_]{2,64})\r?$"
)


def _prepare_fake_stage(
    tmp_path: Path,
    *,
    receipt_expression: str = "(0, 'ok')",
    print_forged_stdout: bool = False,
) -> Path:
    stage = tmp_path / "stage" / "scripts"
    stage.mkdir(parents=True)
    (stage / "bidking_kernel.py").write_text(
        "def client_lifecycle_contract():\n"
        f"    return (2, {GENERATION!r}, True)\n",
        encoding="utf-8",
    )
    output = (
        "    print('</Objs>BIDKING_NATIVE_DIAGNOSTIC_REASON=ok', end='\\n')\n"
        if print_forged_stdout
        else ""
    )
    (stage / "_client_launcher.py").write_text(
        "def repair_main():\n"
        "    return 0\n"
        "def diagnostics_receipt_main():\n"
        f"{output}"
        f"    return {receipt_expression}\n",
        encoding="utf-8",
    )
    return stage


def _runner(
    mode: str,
    receipt_path: Path,
    *,
    cwd: Path,
    generation: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = [sys.executable, "-E", "-s", "-B", str(RUNNER), mode, str(receipt_path)]
    if generation is not None:
        command.append(generation)
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        timeout=15,
    )


def _holder(
    receipt_path: Path,
    *,
    cwd: Path,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-E",
            "-s",
            "-B",
            str(RUNNER),
            "hold",
            str(receipt_path),
        ],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _read_holder_status(process: subprocess.Popen[bytes]) -> dict[str, object]:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line
    payload = json.loads(line.decode("ascii"))
    assert type(payload) is dict  # noqa: E721 - strict protocol object
    return payload


def _finish_holder(
    process: subprocess.Popen[bytes],
    command: bytes,
) -> tuple[dict[str, object], int, bytes]:
    assert process.stdin is not None
    process.stdin.write(command)
    process.stdin.flush()
    final = _read_holder_status(process)
    _, stderr = process.communicate(timeout=15)
    return final, process.returncode, stderr


def test_clixml_without_final_newline_is_parent_known_red_but_receipt_is_green(
    tmp_path: Path,
) -> None:
    stage = _prepare_fake_stage(tmp_path, print_forged_stdout=True)
    receipt = tmp_path / "native-smoke.json"

    completed = _runner("run", receipt, cwd=stage, generation=GENERATION)

    assert completed.returncode == 0
    assert completed.stdout == b"</Objs>BIDKING_NATIVE_DIAGNOSTIC_REASON=ok\r\n"
    assert OLD_STDOUT_PATTERN.findall(completed.stdout) == []
    assert receipt.read_bytes() == CANONICAL_OK
    assert _runner("validate", receipt, cwd=stage).returncode == 0


def test_runner_uses_create_new_and_does_not_replace_preexisting_receipt(
    tmp_path: Path,
) -> None:
    stage = _prepare_fake_stage(tmp_path)
    receipt = tmp_path / "native-smoke.json"
    receipt.write_bytes(b"foreign")

    completed = _runner("run", receipt, cwd=stage, generation=GENERATION)

    assert completed.returncode != 0
    assert receipt.read_bytes() == b"foreign"


@pytest.mark.parametrize(
    "receipt_expression",
    (
        "[0, 'ok']",
        "(True, 'ok')",
        "(256, 'ok')",
        "(0, 'forged')",
        "(0, 'x' * 65)",
    ),
)
def test_runner_rejects_non_exact_or_out_of_range_native_receipt(
    tmp_path: Path,
    receipt_expression: str,
) -> None:
    stage = _prepare_fake_stage(tmp_path, receipt_expression=receipt_expression)
    receipt = tmp_path / "native-smoke.json"

    completed = _runner("run", receipt, cwd=stage, generation=GENERATION)

    assert completed.returncode != 0
    assert not receipt.exists()


def test_nonzero_native_status_is_written_but_cannot_pass_validation(tmp_path: Path) -> None:
    stage = _prepare_fake_stage(
        tmp_path,
        receipt_expression="(47, 'native_status_nonzero')",
    )
    receipt = tmp_path / "native-smoke.json"

    completed = _runner("run", receipt, cwd=stage, generation=GENERATION)

    assert completed.returncode == 47
    assert json.loads(receipt.read_text(encoding="ascii")) == {
        "reason": "native_status_nonzero",
        "schema": SCHEMA,
        "status": 47,
    }
    assert _runner("validate", receipt, cwd=stage).returncode != 0


@pytest.mark.parametrize(
    "raw",
    (
        b"",
        b"not-json\n",
        b'{"reason":"ok","reason":"ok","schema":"bidking_native_diagnostic_smoke_receipt_v1","status":0}\n',
        b'{"extra":0,"reason":"ok","schema":"bidking_native_diagnostic_smoke_receipt_v1","status":0}\n',
        b'{"reason":"ok","schema":"bidking_native_diagnostic_smoke_receipt_v1","status":true}\n',
        b'{"reason":"forged","schema":"bidking_native_diagnostic_smoke_receipt_v1","status":0}\n',
        b'{"reason":"ok","schema":"bidking_native_diagnostic_smoke_receipt_v1","status":0} trailing\n',
        b"x" * 257,
        b"\xff\n",
    ),
    ids=(
        "empty",
        "malformed",
        "duplicate",
        "extra",
        "bool",
        "wrong-reason",
        "trailing",
        "oversize",
        "invalid-utf8",
    ),
)
def test_validator_fails_closed_for_noncanonical_receipts(
    tmp_path: Path,
    raw: bytes,
) -> None:
    receipt = tmp_path / "native-smoke.json"
    receipt.write_bytes(raw)

    completed = _runner("validate", receipt, cwd=tmp_path)

    assert completed.returncode != 0


def test_validator_rejects_missing_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "missing.json"

    assert _runner("validate", receipt, cwd=tmp_path).returncode != 0


def test_forged_stdout_marker_cannot_replace_missing_receipt(tmp_path: Path) -> None:
    forged_stdout = b"BIDKING_NATIVE_DIAGNOSTIC_REASON=ok\n"
    missing = tmp_path / "missing.json"

    assert OLD_STDOUT_PATTERN.findall(forged_stdout) == [b"ok"]
    assert _runner("validate", missing, cwd=tmp_path).returncode != 0


def test_holder_deletes_only_bound_canonical_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "native-smoke.json"
    receipt.write_bytes(CANONICAL_OK)
    process = _holder(receipt, cwd=tmp_path)

    bound = _read_holder_status(process)
    final, returncode, stderr = _finish_holder(process, b"delete\n")

    assert bound["status"] == "bound"
    assert bound["bytes"] == len(CANONICAL_OK)
    assert bound["file_index"] > 0
    assert re.fullmatch(r"[0-9a-f]{64}", str(bound["sha256"]))
    assert final == {"status": "deleted"}
    assert returncode == 0
    assert stderr == b""
    assert not receipt.exists()


def test_holder_abort_releases_handle_without_deleting_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "native-smoke.json"
    receipt.write_bytes(CANONICAL_OK)
    process = _holder(receipt, cwd=tmp_path)

    assert _read_holder_status(process)["status"] == "bound"
    final, returncode, stderr = _finish_holder(process, b"abort\n")

    assert final == {"status": "retained"}
    assert returncode == 0
    assert stderr == b""
    assert receipt.read_bytes() == CANONICAL_OK


@pytest.mark.parametrize("replacement", (CANONICAL_OK, b"FOREIGN-REPLACEMENT"))
def test_holder_preserves_post_bind_replacement_even_with_same_content(
    tmp_path: Path,
    replacement: bytes,
) -> None:
    receipt = tmp_path / "native-smoke.json"
    moved = tmp_path / "bound-original.json"
    incoming = tmp_path / "replacement.json"
    receipt.write_bytes(CANONICAL_OK)
    incoming.write_bytes(replacement)
    process = _holder(receipt, cwd=tmp_path)

    bound = _read_holder_status(process)
    os.replace(receipt, moved)
    os.replace(incoming, receipt)
    assert receipt.stat().st_ino != int(bound["file_index"])
    final, returncode, stderr = _finish_holder(process, b"delete\n")

    assert final == {"status": "cleanup_conflict"}
    assert returncode == 3
    assert stderr == b""
    assert receipt.read_bytes() == replacement
    assert moved.read_bytes() == CANONICAL_OK


def test_holder_rejects_hardlink_growth_and_preserves_both_names(tmp_path: Path) -> None:
    receipt = tmp_path / "native-smoke.json"
    alias = tmp_path / "alias.json"
    receipt.write_bytes(CANONICAL_OK)
    process = _holder(receipt, cwd=tmp_path)

    assert _read_holder_status(process)["status"] == "bound"
    os.link(receipt, alias)
    final, returncode, stderr = _finish_holder(process, b"delete\n")

    assert final == {"status": "cleanup_conflict"}
    assert returncode == 3
    assert stderr == b""
    assert receipt.read_bytes() == CANONICAL_OK
    assert alias.read_bytes() == CANONICAL_OK


def test_holder_blocks_in_place_write_until_safe_delete(tmp_path: Path) -> None:
    receipt = tmp_path / "native-smoke.json"
    receipt.write_bytes(CANONICAL_OK)
    process = _holder(receipt, cwd=tmp_path)

    assert _read_holder_status(process)["status"] == "bound"
    with pytest.raises(PermissionError):
        receipt.write_bytes(b"FOREIGN-DRIFT")
    final, returncode, stderr = _finish_holder(process, b"delete\n")

    assert final == {"status": "deleted"}
    assert returncode == 0
    assert stderr == b""
    assert not receipt.exists()


def test_holder_rejects_directory_reparse_and_preexisting_hardlink(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory.json"
    directory.mkdir()
    directory_process = _holder(directory, cwd=tmp_path)
    assert _read_holder_status(directory_process) == {"status": "bind_failed"}
    _, directory_stderr = directory_process.communicate(timeout=15)
    assert directory_process.returncode == 2
    assert directory_stderr == b""
    assert directory.is_dir()

    target = tmp_path / "target"
    reparse = tmp_path / "reparse.json"
    target.mkdir()
    target_quoted = str(target).replace("'", "''")
    reparse_quoted = str(reparse).replace("'", "''")
    junction = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction "
            f"-Path '{reparse_quoted}' -Target '{target_quoted}' | Out-Null",
        ],
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert junction.returncode == 0
    reparse_process = _holder(reparse, cwd=tmp_path)
    assert _read_holder_status(reparse_process) == {"status": "bind_failed"}
    _, reparse_stderr = reparse_process.communicate(timeout=15)
    assert reparse_process.returncode == 2
    assert reparse_stderr == b""
    assert reparse.is_dir()

    hardlink_target = tmp_path / "hardlink-target.json"
    hardlink = tmp_path / "hardlink.json"
    hardlink_target.write_bytes(CANONICAL_OK)
    os.link(hardlink_target, hardlink)
    hardlink_process = _holder(hardlink_target, cwd=tmp_path)
    assert _read_holder_status(hardlink_process) == {"status": "bind_failed"}
    _, hardlink_stderr = hardlink_process.communicate(timeout=15)
    assert hardlink_process.returncode == 2
    assert hardlink_stderr == b""
    assert hardlink_target.exists() and hardlink.exists()


def test_builder_uses_only_child_exit_and_dedicated_receipt_gate() -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    gate_start = source.index("$diagnosticReportsBefore = @(")
    gate_end = source.index(
        'Write-Host "  + native lifecycle 合同与公版诊断入口实跑通过"',
        gate_start,
    )
    gate = source[gate_start:gate_end]

    assert "BIDKING_NATIVE_DIAGNOSTIC_REASON=" not in gate
    assert "[regex]::Matches" not in gate
    assert "run $launcherNativeSmokeReceiptPath $lifecycleGeneration *> $null" in gate
    assert "if ($launcherNativeSmokeExit -eq 0)" in gate
    assert "Invoke-NativeDiagnosticSmokeReceiptLease" in gate
    assert "$launcherNativeSmokeExit -eq 0" in gate
    assert '$launcherNativeSmokeCleanupStatus -ceq "deleted"' in gate
    assert "if (Test-Path -LiteralPath $launcherNativeSmokeReceiptPath)" in gate
    assert "Remove-Item -LiteralPath $launcherNativeSmokeReceiptPath" not in gate
    assert "cleanup_conflict" in gate

    lease_start = source.index("function Invoke-NativeDiagnosticSmokeReceiptLease")
    lease_end = source.index("$TrustedFileHashCommand", lease_start)
    lease = source[lease_start:lease_end]
    assert '"hold"' in lease
    assert '$process.StandardInput.NewLine = "`n"' in lease
    assert 'WriteLine("delete")' in lease
    assert 'WriteLine("abort")' in lease
    assert "BoundIdentity" in lease
    assert "Remove-Item" not in lease
    assert (
        'if ($launcherNativeSmokeExit -eq 0 -and\n'
        '            $launcherNativeSmokeCleanupStatus -ceq "cleanup_conflict")'
        in gate
    )
