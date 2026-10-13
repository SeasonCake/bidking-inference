from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
POWERSHELL = shutil.which("powershell.exe")
MARKER_VALUE = b"compatibility_guidance_v1"


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _guidance_functions() -> str:
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    start = source.index("function Get-CompatibilityGuidancePrompt")
    end = source.index("# --- 系统兼容性预检", start)
    return source[start:end]


def _run_powershell(body: str) -> subprocess.CompletedProcess[str]:
    if not POWERSHELL:
        pytest.skip("Windows PowerShell is unavailable")
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$ErrorActionPreference='Stop';"
        + _guidance_functions()
        + body
    )
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _json_result(body: str) -> dict:
    proc = _run_powershell(body)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"missing JSON output; stderr={proc.stderr}"
    return json.loads(lines[-1])


def _marker_path_override(marker: Path) -> str:
    return f"""
function Get-CompatibilityGuidanceMarkerPath {{
  return {_ps_quote(marker)}
}}
"""


def test_first_use_guidance_absent_ok_writes_exact_utf8_marker(tmp_path: Path) -> None:
    marker = tmp_path / "profile" / "compatibility_guidance_v1.seen"
    result = _json_result(
        _marker_path_override(marker)
        + """
$script:showCalls=0
function Invoke-CompatibilityGuidanceMessageBox {
  $script:showCalls += 1
  return "OK"
}
$confirmed = Confirm-FirstCompatibilityGuidance
$bytes = if (Test-Path -LiteralPath """
        + _ps_quote(marker)
        + """) {
  [System.IO.File]::ReadAllBytes("""
        + _ps_quote(marker)
        + """)
} else { [byte[]]@() }
[pscustomobject]@{
  confirmed=[bool]$confirmed
  show_calls=$script:showCalls
  marker_b64=[Convert]::ToBase64String($bytes)
} | ConvertTo-Json -Compress
"""
    )
    assert result == {
        "confirmed": True,
        "show_calls": 1,
        "marker_b64": base64.b64encode(MARKER_VALUE).decode("ascii"),
    }


def test_first_use_guidance_valid_marker_skips_prompt(tmp_path: Path) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    marker.write_bytes(MARKER_VALUE)
    result = _json_result(
        _marker_path_override(marker)
        + """
$script:showCalls=0
function Invoke-CompatibilityGuidanceMessageBox {
  $script:showCalls += 1
  throw "prompt must not run"
}
$confirmed = Confirm-FirstCompatibilityGuidance
[pscustomobject]@{
  confirmed=[bool]$confirmed
  show_calls=$script:showCalls
} | ConvertTo-Json -Compress
"""
    )
    assert result == {"confirmed": True, "show_calls": 0}


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"garbled",
        b"\xef\xbb\xbfcompatibility_guidance_v1",
        b"compatibility_guidance_v1\n",
    ],
)
def test_first_use_guidance_invalid_marker_is_unseen_and_cancelled(
    tmp_path: Path,
    payload: bytes,
) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    marker.write_bytes(payload)
    result = _json_result(
        _marker_path_override(marker)
        + """
$script:showCalls=0
function Invoke-CompatibilityGuidanceMessageBox {
  $script:showCalls += 1
  return "Cancel"
}
$confirmed = Confirm-FirstCompatibilityGuidance
[pscustomobject]@{
  confirmed=[bool]$confirmed
  show_calls=$script:showCalls
} | ConvertTo-Json -Compress
"""
    )
    assert result == {"confirmed": False, "show_calls": 1}
    assert marker.read_bytes() == payload


def test_first_use_guidance_marker_read_error_is_unseen(tmp_path: Path) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    marker.write_bytes(MARKER_VALUE)
    result = _json_result(
        _marker_path_override(marker)
        + """
function Read-CompatibilityGuidanceMarkerBytes {
  throw "PRIVATE_READ_ERROR"
}
function Invoke-CompatibilityGuidanceMessageBox { return "Cancel" }
$confirmed = Confirm-FirstCompatibilityGuidance
[pscustomobject]@{confirmed=[bool]$confirmed} | ConvertTo-Json -Compress
"""
    )
    assert result == {"confirmed": False}


def test_first_use_guidance_directory_marker_is_unseen(tmp_path: Path) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    marker.mkdir()
    result = _json_result(
        _marker_path_override(marker)
        + """
function Invoke-CompatibilityGuidanceMessageBox { return "Cancel" }
$confirmed = Confirm-FirstCompatibilityGuidance
[pscustomobject]@{confirmed=[bool]$confirmed} | ConvertTo-Json -Compress
"""
    )
    assert result == {"confirmed": False}
    assert marker.is_dir()


def test_first_use_guidance_add_type_failure_is_cancel_without_marker(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    proc = _run_powershell(
        _marker_path_override(marker)
        + """
function Add-Type { throw "PRIVATE_ADD_TYPE_ERROR" }
$confirmed = Confirm-FirstCompatibilityGuidance
[pscustomobject]@{
  confirmed=[bool]$confirmed
  marker_exists=Test-Path -LiteralPath """
        + _ps_quote(marker)
        + """
} | ConvertTo-Json -Compress
"""
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "PRIVATE_ADD_TYPE_ERROR" not in proc.stdout
    assert "PRIVATE_ADD_TYPE_ERROR" not in proc.stderr
    result = json.loads([line for line in proc.stdout.splitlines() if line.strip()][-1])
    assert result == {"confirmed": False, "marker_exists": False}


def test_first_use_guidance_message_box_failure_is_cancel_without_marker(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    proc = _run_powershell(
        _marker_path_override(marker)
        + """
function Invoke-CompatibilityGuidanceMessageBox {
  throw "PRIVATE_MESSAGEBOX_ERROR"
}
$confirmed = Confirm-FirstCompatibilityGuidance
[pscustomobject]@{
  confirmed=[bool]$confirmed
  marker_exists=Test-Path -LiteralPath """
        + _ps_quote(marker)
        + """
} | ConvertTo-Json -Compress
"""
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "PRIVATE_MESSAGEBOX_ERROR" not in proc.stdout
    assert "PRIVATE_MESSAGEBOX_ERROR" not in proc.stderr
    result = json.loads([line for line in proc.stdout.splitlines() if line.strip()][-1])
    assert result == {"confirmed": False, "marker_exists": False}


def test_first_use_guidance_marker_write_failure_continues_and_reprompts(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    result = _json_result(
        _marker_path_override(marker)
        + """
$script:showCalls=0
function Invoke-CompatibilityGuidanceMessageBox {
  $script:showCalls += 1
  return "OK"
}
function Write-CompatibilityGuidanceMarker {
  throw "PRIVATE_WRITE_ERROR"
}
$first = Confirm-FirstCompatibilityGuidance
$second = Confirm-FirstCompatibilityGuidance
[pscustomobject]@{
  first=[bool]$first
  second=[bool]$second
  show_calls=$script:showCalls
  marker_exists=Test-Path -LiteralPath """
        + _ps_quote(marker)
        + """
} | ConvertTo-Json -Compress
"""
    )
    assert result == {
        "first": True,
        "second": True,
        "show_calls": 2,
        "marker_exists": False,
    }


def test_first_use_guidance_cancel_guard_exits_three(tmp_path: Path) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    proc = _run_powershell(
        _marker_path_override(marker)
        + """
function Invoke-CompatibilityGuidanceMessageBox { return "Cancel" }
if (-not (Confirm-FirstCompatibilityGuidance)) { exit 3 }
exit 0
"""
    )
    assert proc.returncode == 3
    assert not marker.exists()


@pytest.mark.parametrize("failure", ["add_type", "message_box"])
def test_first_use_guidance_prompt_failure_guard_exits_three(
    tmp_path: Path,
    failure: str,
) -> None:
    marker = tmp_path / "compatibility_guidance_v1.seen"
    failure_override = (
        'function Add-Type { throw "PRIVATE_ADD_TYPE_ERROR" }'
        if failure == "add_type"
        else (
            "function Invoke-CompatibilityGuidanceMessageBox "
            '{ throw "PRIVATE_MESSAGEBOX_ERROR" }'
        )
    )
    proc = _run_powershell(
        _marker_path_override(marker)
        + failure_override
        + """
if (-not (Confirm-FirstCompatibilityGuidance)) { exit 3 }
exit 0
"""
    )
    assert proc.returncode == 3
    assert "PRIVATE_" not in proc.stdout
    assert "PRIVATE_" not in proc.stderr
    assert not marker.exists()


def test_first_use_guidance_confirmation_precedes_product_side_effects() -> None:
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    confirmation = source.index(
        "if ($UseWssCapture -and -not (Confirm-FirstCompatibilityGuidance))"
    )
    assert confirmation < source.index(
        "New-Item -ItemType Directory -Path $LogPath -Force"
    )
    assert confirmation < source.index("$LaunchStartedAt =")
    assert confirmation < source.index("$ComponentConsent =")
    assert confirmation < source.index("Start-Process -FilePath $MonitorPythonPath")
