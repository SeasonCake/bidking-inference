from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bidking_wss_capture_core import (  # noqa: E402
    FRAME_STREAM_PREFIX,
    FLOW_STREAM_PREFIX,
    WssFrameSanitizer,
)
from build_wss_addon_bootstrap import (  # noqa: E402
    load_operational_contract,
    render_protected_addon,
    smoke_mitmdump_addon,
)
from build_release_runtime_component import build_component  # noqa: E402
from run_wss_live_monitor import (  # noqa: E402
    KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    REDIRECTOR_MISSING_CONFIRM_POLLS,
    REDIRECTOR_STARTUP_GRACE_SECONDS,
    RUNTIME_COMPATIBILITY_FAILED_STATE,
    WinDivertCompatibilityPreloader,
    WssHelperDiagnostics,
    WssIdleLifecycleGuard,
    WssRedirectorLiveness,
    _helper_error_details,
    _write_wss_helper_status,
    assign_windows_kill_on_close_job,
    build_mitmdump_command,
    classify_mitmdump_stderr_line,
    close_windows_job_handle,
    frame_record_to_row,
    inspect_runtime_compatibility,
    parse_frame_record,
    parse_flow_record,
    resolve_mitmdump,
)
import run_wss_live_monitor as wss_monitor  # noqa: E402
import local_compat_launcher  # noqa: E402
from verify_release_asset_inventory import inventory  # noqa: E402


def _frame(direction: str, message_id: int, body: bytes = b"") -> bytes:
    minimum = 12 if direction == "SEND" else 16
    raw = bytearray(minimum + len(body))
    raw[:4] = len(raw).to_bytes(4, "big")
    offset = 8 if direction == "SEND" else 12
    raw[offset : offset + 4] = message_id.to_bytes(4, "big")
    raw[minimum:] = body
    return bytes(raw)


def _record(direction: str, message_id: int, raw: bytes) -> str:
    return FRAME_STREAM_PREFIX + json.dumps(
        {
            "captured_at_ms": 1,
            "flow_id": "test-flow",
            "direct": direction,
            "message_id": message_id,
            "data_length": len(raw),
            "data_b64": base64.b64encode(raw).decode("ascii"),
        },
        separators=(",", ":"),
    )


def test_sanitizer_reassembles_split_and_concatenated_rev_frames() -> None:
    sanitizer = WssFrameSanitizer()
    first = _frame("REV", 0x0021, b"state")
    second = _frame("REV", 0x0077, b"status")

    assert sanitizer.feed(flow_id="f", direction="REV", payload=first[:7]) == ()
    emitted = sanitizer.feed(
        flow_id="f",
        direction="REV",
        payload=first[7:] + second,
    )

    assert [(item.message_id, item.raw) for item in emitted] == [
        (0x0021, first),
        (0x0077, second),
    ]
    assert sanitizer.pending_bytes() == 0


def test_sanitizer_allows_only_auction_client_frames() -> None:
    sanitizer = WssFrameSanitizer()
    login = _frame("SEND", 0x0002, b"private-login-payload")
    bid = _frame("SEND", 0x0022, b"bid")
    action = _frame("SEND", 0x0026, b"action")
    unknown = _frame("SEND", 0x019D, b"private-lobby-payload")

    emitted = sanitizer.feed(
        flow_id="f",
        direction="SEND",
        payload=login + bid + action + unknown,
    )

    assert [(item.message_id, item.raw) for item in emitted] == [
        (0x0022, bid),
        (0x0026, action),
    ]
    assert sanitizer.discarded["send_not_allowlisted"] == 2
    assert b"private-login-payload" not in b"".join(item.raw for item in emitted)
    assert b"private-lobby-payload" not in b"".join(item.raw for item in emitted)


def test_sanitizer_invalid_prefix_fails_closed_without_byte_scan() -> None:
    sanitizer = WssFrameSanitizer()
    coincidental = _frame("SEND", 0x0022, b"must-not-escape")

    emitted = sanitizer.feed(
        flow_id="f",
        direction="SEND",
        payload=b"\x00\x00\x00\x01" + coincidental,
    )

    assert emitted == ()
    assert sanitizer.pending_bytes() == 0
    assert sanitizer.malformed["send_invalid_length"] == 1


def test_parent_revalidates_addon_record_and_normalizes_protocol() -> None:
    raw = _frame("REV", 0x002D, b"settlement")
    parsed = parse_frame_record(_record("REV", 0x002D, raw))
    assert parsed is not None

    row = frame_record_to_row(parsed, sort_id=7, process_name="BidKing.exe")
    assert row["SortID"] == 7
    assert row["Direct"] == "REV"
    assert row["Protocol"] == "Tcp"
    assert row["Source"] == "WssLocal"
    assert base64.b64decode(row["Data"]) == raw


def test_parent_rejects_non_allowlisted_or_mismatched_records() -> None:
    private = _frame("SEND", 0x0002, b"private")
    assert parse_frame_record(_record("SEND", 0x0002, private)) is None

    bid = _frame("SEND", 0x0022, b"bid")
    mismatched = _record("SEND", 0x0026, bid)
    assert parse_frame_record(mismatched) is None


def test_parent_tracks_only_valid_flow_open_and_close_events() -> None:
    assert parse_flow_record(
        FLOW_STREAM_PREFIX + '{"event":"open","flow_id":"flow-1"}'
    ) == ("open", "flow-1")
    assert parse_flow_record(
        FLOW_STREAM_PREFIX + '{"event":"close","flow_id":"flow-1"}'
    ) == ("close", "flow-1")
    assert (
        parse_flow_record(FLOW_STREAM_PREFIX + '{"event":"login","flow_id":"flow-1"}')
        is None
    )
    assert (
        parse_flow_record(FLOW_STREAM_PREFIX + '{"event":"open","flow_id":""}') is None
    )


def test_mitmdump_command_is_process_local_and_never_writes_flow_file() -> None:
    command = build_mitmdump_command(
        mitmdump_path=Path("mitmdump.exe"),
        addon_path=Path("addon.py"),
        confdir=Path("conf"),
        process_name="BidKing.exe",
    )

    assert "local:BidKing.exe" in command
    assert "-w" not in command
    assert "--save-stream-file" not in command


def test_fixed_packaged_mitmdump_is_the_only_public_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    packaged = tmp_path / "vendor" / "mitmproxy" / "mitmdump.exe"
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(b"fixed")
    monkeypatch.setattr(wss_monitor, "ROOT", tmp_path)

    assert resolve_mitmdump() == packaged.resolve()


@pytest.mark.parametrize("fallback_source", ["program_files", "path", "environment"])
def test_public_resolution_rejects_global_mitmdump_fallbacks(
    tmp_path: Path, monkeypatch, fallback_source: str
) -> None:
    program_files_runtime = (
        tmp_path / "program-files" / "mitmproxy" / "bin" / "mitmdump.exe"
    )
    path_runtime = tmp_path / "path-bin" / "mitmdump.exe"
    environment_runtime = tmp_path / "environment" / "mitmdump.exe"
    global_runtime = {
        "program_files": program_files_runtime,
        "path": path_runtime,
        "environment": environment_runtime,
    }[fallback_source]
    global_runtime.parent.mkdir(parents=True)
    global_runtime.write_bytes(b"not-packaged")
    monkeypatch.setattr(wss_monitor, "ROOT", tmp_path)
    if fallback_source == "program_files":
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "program-files"))
    elif fallback_source == "path":
        monkeypatch.setenv("PATH", str(path_runtime.parent))
    else:
        monkeypatch.setenv("BIDKING_MITMDUMP_PATH", str(environment_runtime))

    with pytest.raises(FileNotFoundError, match="fixed mitmdump runtime unavailable"):
        resolve_mitmdump()


def test_engineering_runtime_requires_explicit_source_checkout_gate(
    tmp_path: Path, monkeypatch
) -> None:
    engineering = tmp_path / "scripts" / "pkg_assets" / "mitmproxy" / "mitmdump.exe"
    engineering.parent.mkdir(parents=True)
    engineering.write_bytes(b"pinned-engineering-runtime")
    (tmp_path / ".git").write_text("gitdir: test", encoding="utf-8")
    monkeypatch.setattr(wss_monitor, "ROOT", tmp_path)

    with pytest.raises(FileNotFoundError):
        resolve_mitmdump(str(engineering))
    assert (
        resolve_mitmdump(str(engineering), allow_engineering_runtime=True)
        == engineering.resolve()
    )


def test_prepare_ca_classifies_confdir_write_denial(
    tmp_path: Path,
    monkeypatch,
) -> None:
    confdir = tmp_path / "denied"
    original_mkdir = Path.mkdir

    def denied_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if path == confdir:
            raise PermissionError("private path")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied_mkdir)
    with pytest.raises(wss_monitor.WssCaPrepareError) as caught:
        wss_monitor.prepare_ca(
            mitmdump_path=tmp_path / "mitmdump.exe",
            confdir=confdir,
        )
    assert caught.value.classification == "component_write_denied"
    assert "private path" not in str(caught.value)


def test_prepare_ca_classifies_runtime_execution_denial(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def denied_popen(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("private runtime path")

    monkeypatch.setattr(wss_monitor.subprocess, "Popen", denied_popen)
    with pytest.raises(wss_monitor.WssCaPrepareError) as caught:
        wss_monitor.prepare_ca(
            mitmdump_path=tmp_path / "mitmdump.exe",
            confdir=tmp_path / "conf",
        )
    assert caught.value.classification == "component_execution_blocked"
    assert "private runtime path" not in str(caught.value)


@pytest.mark.parametrize(
    "classification",
    ["component_write_denied", "component_execution_blocked"],
)
def test_prepare_ca_main_emits_only_fixed_failure_sentinel(
    tmp_path: Path,
    monkeypatch,
    capsys,
    classification: str,
) -> None:
    mitmdump = tmp_path / "mitmdump.exe"
    mitmdump.write_bytes(b"fixture")
    monkeypatch.setattr(
        wss_monitor,
        "resolve_mitmdump",
        lambda *_args, **_kwargs: mitmdump,
    )

    def fail_prepare(**_kwargs: object) -> None:
        raise wss_monitor.WssCaPrepareError(classification)

    monkeypatch.setattr(wss_monitor, "prepare_ca", fail_prepare)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_wss_live_monitor.py",
            "--prepare-ca",
            "--mitmdump",
            str(mitmdump),
            "--confdir",
            str(tmp_path / "conf"),
        ],
    )

    assert wss_monitor.main() == 2
    captured = capsys.readouterr()
    assert f"BIDKING_WSS_CA_FAILURE={classification}" in captured.err
    assert "private" not in captured.err


def test_powershell_ca_prepare_consumes_fixed_failure_classification() -> None:
    launcher = (SCRIPTS / "start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8-sig"
    )
    block = launcher[
        launcher.index('$CaFailureClass = "unknown"') : launcher.index(
            "$TemporaryTrustRuntimeIdentity = ("
        )
    ]
    assert '"BIDKING_WSS_CA_FAILURE="' in block
    assert '"component_write_denied", "component_execution_blocked"' in block
    assert "Write-BidKingStartupFailure $CaFailureClass" in block
    assert "Show-BidKingInternetCafeFailure $CaFailureClass" in block
    assert 'Write-BidKingStartupFailure "unknown"' not in block


@pytest.mark.skipif(os.name != "nt", reason="PowerShell launcher contract")
def test_powershell_runtime_resolution_accepts_fixed_and_rejects_global_fallbacks(
    tmp_path: Path,
) -> None:
    helper = tmp_path / "resolve-runtime.ps1"
    helper.write_text(
        """
param([string]$Launcher, [string]$PackageRoot)
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  $Launcher, [ref]$tokens, [ref]$errors
)
if ($errors.Count) { exit 90 }
$functionAst = $ast.Find(
  {
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq "Resolve-WssMitmdumpPath"
  },
  $true
)
if ($null -eq $functionAst) { exit 91 }
. ([ScriptBlock]::Create($functionAst.Extent.Text))
$resolved = Resolve-WssMitmdumpPath -PackageRoot $PackageRoot -AllowEngineeringRuntime $false
if (-not $resolved) {
  Write-Output "FIXED_RUNTIME_MISSING"
  exit 2
}
Write-Output $resolved
""".strip(),
        encoding="utf-8",
    )
    global_runtime = tmp_path / "program-files" / "mitmproxy" / "bin" / "mitmdump.exe"
    global_runtime.parent.mkdir(parents=True)
    global_runtime.write_bytes(b"not-packaged")
    path_runtime = tmp_path / "path-bin" / "mitmdump.exe"
    path_runtime.parent.mkdir()
    path_runtime.write_bytes(b"not-packaged")
    env = dict(os.environ)
    env["ProgramFiles"] = str(tmp_path / "program-files")
    env["PATH"] = str(path_runtime.parent)
    env["BIDKING_MITMDUMP_PATH"] = str(path_runtime)

    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper),
        str(SCRIPTS / "start_live_npcap_overlay.ps1"),
        str(tmp_path / "package"),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout.strip() == "FIXED_RUNTIME_MISSING"
    assert str(global_runtime) not in completed.stdout
    assert str(path_runtime) not in completed.stdout

    packaged = tmp_path / "package" / "vendor" / "mitmproxy" / "mitmdump.exe"
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(b"fixed")
    accepted = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )

    assert accepted.returncode == 0
    assert accepted.stdout.strip() == str(packaged.resolve())


@pytest.mark.skipif(os.name != "nt", reason="PowerShell launcher contract")
def test_powershell_runtime_resolution_installs_verified_component_cache(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    scripts = package / "scripts"
    processed = package / "data" / "processed"
    scripts.mkdir(parents=True)
    processed.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "runtime_component.py", scripts / "runtime_component.py")
    shutil.copy2(
        SCRIPTS / "local_compat_component.py",
        scripts / "local_compat_component.py",
    )

    source = tmp_path / "runtime-source"
    source.mkdir()
    (source / "mitmdump.exe").write_bytes(b"fixed-component-runtime")
    actual = inventory(source)
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_id": "fixture-runtime",
                "version": "1.0",
                "inventory": {
                    "file_count": actual["file_count"],
                    "total_bytes": actual["total_bytes"],
                    "inventory_sha256": actual["inventory_sha256"],
                },
                "required_files": {
                    "mitmdump.exe": actual["files"]["mitmdump.exe"],
                },
            }
        ),
        encoding="utf-8",
    )
    component = build_component(
        source_root=source,
        asset_manifest_path=manifest,
        output_dir=tmp_path / "component-dist",
        descriptor_path=processed / "local_compat_component.json",
        mirror_bases=["https://downloads.example.test/components"],
    )
    descriptor_payload = json.loads(
        (processed / "local_compat_component.json").read_text(encoding="utf-8")
    )
    descriptor_payload["mirrors"] = []
    (processed / "local_compat_component.json").write_text(
        json.dumps(descriptor_payload),
        encoding="utf-8",
    )

    helper = tmp_path / "resolve-component-runtime.ps1"
    helper.write_text(
        """
param([string]$Launcher, [string]$PackageRoot, [string]$PythonPath)
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  $Launcher, [ref]$tokens, [ref]$errors
)
if ($errors.Count) { exit 90 }
$functionAst = $ast.Find(
  {
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq "Resolve-WssMitmdumpPath"
  },
  $true
)
if ($null -eq $functionAst) { exit 91 }
. ([ScriptBlock]::Create($functionAst.Extent.Text))
$resolved = Resolve-WssMitmdumpPath `
  -PackageRoot $PackageRoot `
  -AllowEngineeringRuntime $false `
  -PythonPath $PythonPath
if (-not $resolved) { exit 2 }
Write-Output $resolved
""".strip(),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(tmp_path / "local-app-data")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            str(SCRIPTS / "start_live_npcap_overlay.ps1"),
            str(package),
            sys.executable,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )

    # The launcher intentionally has no arbitrary local-archive override. Seed the
    # immutable cache through the same installer API, then prove PowerShell accepts
    # only the verified content-addressed result.
    assert completed.returncode == 2
    from runtime_component import ensure_component

    payload = ensure_component(
        processed / "local_compat_component.json",
        cache_root=Path(env["LOCALAPPDATA"]) / "BidKingLive" / "components",
        local_archive=Path(str(component["archive_path"])),
    )
    accepted = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            str(SCRIPTS / "start_live_npcap_overlay.ps1"),
            str(package),
            sys.executable,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.strip() == str((payload / "mitmdump.exe").resolve())


@pytest.mark.skipif(os.name != "nt", reason="Windows MOTW component copy contract")
def test_powershell_runtime_resolution_copies_motw_bundled_runtime_without_ads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = tmp_path / "package"
    scripts = package / "scripts"
    runtime = package / "vendor" / "mitmproxy"
    redirector = runtime / "_internal" / "mitmproxy_windows" / "windows-redirector.exe"
    driver = runtime / "_internal" / "mitmproxy_windows" / "WinDivert64.sys"
    redirector.parent.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (runtime / "mitmdump.exe").write_bytes(b"MITMDUMP")
    redirector.write_bytes(b"REDIRECTOR")
    driver.write_bytes(b"WINDIVERT")
    (runtime / "LICENSE.txt").write_bytes(b"LICENSE")
    for member in (runtime / "mitmdump.exe", redirector, driver):
        Path(f"{member}:Zone.Identifier").write_bytes(
            b"[ZoneTransfer]\r\nZoneId=3\r\n"
        )
    shutil.copy2(SCRIPTS / "runtime_component.py", scripts / "runtime_component.py")
    shutil.copy2(
        SCRIPTS / "local_compat_component.py",
        scripts / "local_compat_component.py",
    )
    actual = inventory(runtime)
    required = {
        relative: actual["files"][relative]
        for relative in (
            "LICENSE.txt",
            "mitmdump.exe",
            "_internal/mitmproxy_windows/windows-redirector.exe",
            "_internal/mitmproxy_windows/WinDivert64.sys",
        )
    }
    (scripts / "mitmproxy_runtime_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_id": "mitmproxy-windows-runtime",
                "version": "fixture-1",
                "architecture": "windows-x86_64",
                "source": {},
                "inventory": {
                    key: actual[key]
                    for key in ("file_count", "total_bytes", "inventory_sha256")
                },
                "required_files": required,
                "forbidden_globs": [],
            }
        ),
        encoding="utf-8",
    )
    helper = tmp_path / "resolve-bundled-runtime.ps1"
    helper.write_text(
        """
param([string]$Launcher, [string]$PackageRoot, [string]$PythonPath)
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  $Launcher, [ref]$tokens, [ref]$errors
)
if ($errors.Count) { exit 90 }
$functionAst = $ast.Find(
  {
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq "Resolve-WssMitmdumpPath"
  },
  $true
)
if ($null -eq $functionAst) { exit 91 }
. ([ScriptBlock]::Create($functionAst.Extent.Text))
$resolved = Resolve-WssMitmdumpPath `
  -PackageRoot $PackageRoot `
  -AllowEngineeringRuntime $false `
  -PythonPath $PythonPath
if (-not $resolved) { exit 2 }
Write-Output $resolved
""".strip(),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(tmp_path / "local-app-data")

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            str(SCRIPTS / "start_live_npcap_overlay.ps1"),
            str(package),
            sys.executable,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    resolved = Path(completed.stdout.strip()).resolve()
    assert resolved != (runtime / "mitmdump.exe").resolve()
    assert resolved.is_relative_to(Path(env["LOCALAPPDATA"]).resolve())
    payload = resolved.parent
    assert inventory(payload)["inventory_sha256"] == actual["inventory_sha256"]
    for relative in required:
        cached = payload.joinpath(*Path(relative).parts)
        assert not os.path.exists(f"{cached}:Zone.Identifier")
    monkeypatch.setattr(wss_monitor, "ROOT", package)
    monkeypatch.setenv("LOCALAPPDATA", env["LOCALAPPDATA"])
    assert resolve_mitmdump(str(resolved)) == resolved


def test_bundled_runtime_copy_fails_closed_on_wrong_inventory(tmp_path: Path) -> None:
    from runtime_component import ComponentError, ensure_bundled_runtime_copy

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "mitmdump.exe").write_bytes(b"MITMDUMP")
    actual = inventory(runtime)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_id": "mitmproxy-windows-runtime",
                "version": "fixture-1",
                "inventory": {
                    "file_count": actual["file_count"],
                    "total_bytes": actual["total_bytes"],
                    "inventory_sha256": "0" * 64,
                },
                "required_files": {
                    "mitmdump.exe": actual["files"]["mitmdump.exe"]
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ComponentError, match="inventory mismatch"):
        ensure_bundled_runtime_copy(
            runtime,
            manifest,
            cache_root=tmp_path / "cache",
        )


def _write_bundled_runtime_fixture(
    root: Path,
) -> tuple[Path, Path, dict[str, object]]:
    runtime = root / "runtime"
    helper = runtime / "_internal" / "helper.exe"
    helper.parent.mkdir(parents=True)
    (runtime / "mitmdump.exe").write_bytes(b"MITMDUMP")
    helper.write_bytes(b"HELPER")
    actual = inventory(runtime)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_id": "mitmproxy-windows-runtime",
                "version": "fixture-1",
                "inventory": {
                    key: actual[key]
                    for key in ("file_count", "total_bytes", "inventory_sha256")
                },
                "required_files": {
                    relative: actual["files"][relative]
                    for relative in ("mitmdump.exe", "_internal/helper.exe")
                },
            }
        ),
        encoding="utf-8",
    )
    return runtime, manifest, actual


def _write_package_bundled_runtime_fixture(
    root: Path,
) -> tuple[Path, Path, dict[str, object]]:
    runtime, temporary_manifest, actual = _write_bundled_runtime_fixture(root)
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    manifest = scripts / "mitmproxy_runtime_manifest.json"
    temporary_manifest.replace(manifest)
    packaged_runtime = root / "vendor" / "mitmproxy"
    packaged_runtime.parent.mkdir(parents=True)
    runtime.replace(packaged_runtime)
    return packaged_runtime, manifest, actual


def test_python_runtime_resolution_accepts_only_verified_bundled_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from runtime_component import ensure_bundled_runtime_copy

    package = tmp_path / "package"
    runtime, manifest, _actual = _write_package_bundled_runtime_fixture(package)
    local_app_data = tmp_path / "local-app-data"
    cache_root = local_app_data / "BidKingLive" / "components"
    payload = ensure_bundled_runtime_copy(
        runtime,
        manifest,
        cache_root=cache_root,
    )
    monkeypatch.setattr(wss_monitor, "ROOT", package)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert resolve_mitmdump(str(payload / "mitmdump.exe")) == (
        payload / "mitmdump.exe"
    )


@pytest.mark.parametrize("tamper", ["ready", "payload"])
def test_python_runtime_resolution_rejects_verified_cache_tamper(
    tmp_path: Path,
    monkeypatch,
    tamper: str,
) -> None:
    from runtime_component import ensure_bundled_runtime_copy

    package = tmp_path / "package"
    runtime, manifest, _actual = _write_package_bundled_runtime_fixture(package)
    local_app_data = tmp_path / "local-app-data"
    cache_root = local_app_data / "BidKingLive" / "components"
    payload = ensure_bundled_runtime_copy(
        runtime,
        manifest,
        cache_root=cache_root,
    )
    if tamper == "ready":
        (payload.parent / "ready.json").write_text("{}", encoding="utf-8")
    else:
        (payload / "mitmdump.exe").write_bytes(b"REPLACED")
    monkeypatch.setattr(wss_monitor, "ROOT", package)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    with pytest.raises(FileNotFoundError, match="fixed mitmdump runtime unavailable"):
        resolve_mitmdump(str(payload / "mitmdump.exe"))


def test_python_runtime_resolution_rejects_arbitrary_file_inside_cache_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = tmp_path / "package"
    _runtime, _manifest, _actual = _write_package_bundled_runtime_fixture(package)
    local_app_data = tmp_path / "local-app-data"
    arbitrary = (
        local_app_data / "BidKingLive" / "components" / "other" / "mitmdump.exe"
    )
    arbitrary.parent.mkdir(parents=True)
    arbitrary.write_bytes(b"ARBITRARY")
    monkeypatch.setattr(wss_monitor, "ROOT", package)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    with pytest.raises(FileNotFoundError, match="fixed mitmdump runtime unavailable"):
        resolve_mitmdump(str(arbitrary))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_bundled_runtime_copy_rejects_required_helper_cache_junction(
    tmp_path: Path,
) -> None:
    from runtime_component import ComponentError, ensure_bundled_runtime_copy

    runtime, manifest, actual = _write_bundled_runtime_fixture(tmp_path / "source")
    cache_root = tmp_path / "cache"
    payload = (
        cache_root
        / "mitmproxy-bundled"
        / f"fixture-1-{actual['inventory_sha256'][:16]}"
        / "payload"
    )
    payload.mkdir(parents=True)
    (payload / "mitmdump.exe").write_bytes(b"MITMDUMP")
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / "helper.exe").write_bytes(b"HELPER")
    junction = payload / "_internal"
    linked = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(ambient)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert linked.returncode == 0, linked.stderr

    try:
        with pytest.raises(ComponentError, match="cache topology is unsafe"):
            ensure_bundled_runtime_copy(
                runtime,
                manifest,
                cache_root=cache_root,
            )
        assert junction.exists()
        assert (junction / "helper.exe").resolve() == (ambient / "helper.exe").resolve()
        assert (ambient / "helper.exe").read_bytes() == b"HELPER"
    finally:
        os.rmdir(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_bundled_runtime_copy_rejects_empty_cache_junction(tmp_path: Path) -> None:
    from runtime_component import ComponentError, ensure_bundled_runtime_copy

    runtime, manifest, actual = _write_bundled_runtime_fixture(tmp_path / "source")
    cache_root = tmp_path / "cache"
    payload = (
        cache_root
        / "mitmproxy-bundled"
        / f"fixture-1-{actual['inventory_sha256'][:16]}"
        / "payload"
    )
    shutil.copytree(runtime, payload)
    ambient = tmp_path / "empty-ambient"
    ambient.mkdir()
    junction = payload / "unused"
    linked = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(ambient)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert linked.returncode == 0, linked.stderr

    try:
        with pytest.raises(ComponentError, match="cache topology is unsafe"):
            ensure_bundled_runtime_copy(
                runtime,
                manifest,
                cache_root=cache_root,
            )
        assert junction.exists()
        assert list(ambient.iterdir()) == []
    finally:
        os.rmdir(junction)


def test_bundled_runtime_copy_rejects_required_helper_cache_hardlink(
    tmp_path: Path,
) -> None:
    from runtime_component import ComponentError, ensure_bundled_runtime_copy

    runtime, manifest, actual = _write_bundled_runtime_fixture(tmp_path / "source")
    cache_root = tmp_path / "cache"
    payload = (
        cache_root
        / "mitmproxy-bundled"
        / f"fixture-1-{actual['inventory_sha256'][:16]}"
        / "payload"
    )
    helper = payload / "_internal" / "helper.exe"
    helper.parent.mkdir(parents=True)
    (payload / "mitmdump.exe").write_bytes(b"MITMDUMP")
    ambient = tmp_path / "ambient-helper.exe"
    ambient.write_bytes(b"HELPER")
    os.link(ambient, helper)

    with pytest.raises(ComponentError, match="cache topology is unsafe"):
        ensure_bundled_runtime_copy(
            runtime,
            manifest,
            cache_root=cache_root,
        )
    assert ambient.read_bytes() == b"HELPER"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_bundled_runtime_copy_rejects_source_root_junction(tmp_path: Path) -> None:
    from runtime_component import ComponentError, ensure_bundled_runtime_copy

    runtime, manifest, _actual = _write_bundled_runtime_fixture(tmp_path / "source")
    source_link = tmp_path / "runtime-link"
    linked = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(source_link), str(runtime)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert linked.returncode == 0, linked.stderr

    try:
        with pytest.raises(ComponentError, match="component path is unsafe"):
            ensure_bundled_runtime_copy(
                source_link,
                manifest,
                cache_root=tmp_path / "cache",
            )
        assert (runtime / "mitmdump.exe").read_bytes() == b"MITMDUMP"
    finally:
        os.rmdir(source_link)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_bundled_runtime_copy_rejects_cache_root_junction(tmp_path: Path) -> None:
    from runtime_component import ComponentError, ensure_bundled_runtime_copy

    runtime, manifest, _actual = _write_bundled_runtime_fixture(tmp_path / "source")
    ambient = tmp_path / "ambient-cache"
    ambient.mkdir()
    cache_root = tmp_path / "cache-link"
    linked = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(cache_root), str(ambient)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert linked.returncode == 0, linked.stderr

    try:
        with pytest.raises(ComponentError, match="component path is unsafe"):
            ensure_bundled_runtime_copy(
                runtime,
                manifest,
                cache_root=cache_root,
            )
        assert list(ambient.iterdir()) == []
    finally:
        os.rmdir(cache_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_bundled_runtime_copy_rejects_cache_parent_junction(tmp_path: Path) -> None:
    from runtime_component import ComponentError, ensure_bundled_runtime_copy

    runtime, manifest, _actual = _write_bundled_runtime_fixture(tmp_path / "source")
    ambient = tmp_path / "ambient-parent"
    ambient.mkdir()
    parent_link = tmp_path / "cache-parent-link"
    linked = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(parent_link), str(ambient)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert linked.returncode == 0, linked.stderr

    try:
        with pytest.raises(ComponentError, match="component path is unsafe"):
            ensure_bundled_runtime_copy(
                runtime,
                manifest,
                cache_root=parent_link / "nested-cache",
            )
        assert list(ambient.iterdir()) == []
    finally:
        os.rmdir(parent_link)


@pytest.mark.skipif(os.name != "nt", reason="Windows job object contract")
def test_windows_job_handle_kills_child_when_closed() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    job = None
    try:
        job = assign_windows_kill_on_close_job(child)
        assert job is not None
        assert child.poll() is None
        close_windows_job_handle(job)
        job = None
        child.wait(timeout=5.0)
        assert child.poll() is not None
    finally:
        if job is not None:
            close_windows_job_handle(job)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3.0)


def test_public_launcher_enforces_cold_start_and_has_npcap_rollback() -> None:
    launcher = (SCRIPTS / "start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '$env:BIDKING_DISABLE_WSS_CAPTURE -ne "1"' in launcher
    assert '$OverlayArgs += "--protect-monitor-while-game-running"' in launcher
    assert "if ($KeepMonitorOnClose)" in launcher
    assert '$OverlayArgs += "--keep-monitor-on-close"' in launcher
    assert "if (-not $KeepMonitorOnClose)" in launcher
    assert '"--stop-pid-on-exit", "$MonitorPid"' in launcher
    assert '"--cleanup-lock-on-exit", (Q $LockPath)' in launcher
    assert "$KeepMonitorAliveOnClose" not in launcher
    assert "Get-Process -Name $GameProcessBaseName" in launcher
    assert "$ExistingWssMonitorProcs = @(" in launcher
    assert "-ExpectedMonitorPath $WssMonitor" in launcher
    assert "-ExpectedLogDir $LogPath" in launcher
    assert "-ExpectedMonitorPath $Monitor" in launcher
    assert '$_.CommandLine -like "*run_wss_live_monitor.py*"' not in launcher
    assert '$_.CommandLine -like "*local_compat_monitor.py*"' not in launcher
    assert "$PackagedLocalMonitor" in launcher
    assert "$PackagedLocalAddon" in launcher
    assert (
        "if ($GameProcessRunning -and ($Restart -or -not $ExistingWssMonitorProcs))"
        in launcher
    )
    assert "完全退出 BidKing 游戏" in launcher
    assert "等悬浮窗出现" in launcher
    assert "再从 Steam 启动游戏" in launcher
    assert "为了正确读取本局信息" in launcher
    assert "计算器不会自动关闭游戏" in launcher
    assert "SetGamePreset / GameUseItem" not in launcher
    assert "Resolve-WssMitmdumpPath" in launcher
    assert 'Join-Path $PackageRoot "vendor\\mitmproxy\\mitmdump.exe"' in launcher
    assert '$DiagnosticProfile -eq "engineering"' in launcher
    assert 'Join-Path $Repo ".git"' in launcher
    assert "--allow-engineering-runtime" in launcher
    assert "$env:ProgramFiles" not in launcher
    assert "BIDKING_MITMDUMP_PATH" not in launcher
    assert '--mitmdump", (Q $MitmdumpPath)' in launcher
    assert '--addon", (Q $WssAddon)' in launcher
    assert "Enable-BidKingTemporaryCurrentUserTrust" in launcher
    assert "Import-Certificate" not in launcher
    assert "mitmproxy-ca.pem" not in launcher
    assert "mitmproxy-ca.p12" not in launcher
    assert '$env:PYTHONIOENCODING = "utf-8"' in launcher
    assert "function Write-MonitorLogSessionState" in launcher
    assert "reused_monitor = $ReusedMonitor" in launcher
    assert "monitor_started_at = $MonitorStartedAt" in launcher
    assert "Write-MonitorLogSessionState $MonitorPid $false" in launcher
    assert "Write-MonitorLogSessionState $MonitorPid $true" in launcher
    assert "monitor_reused launch_id={1} monitor_pid={2}" in launcher
    assert "auto_stop_when_idle = (-not $NoOverlay -and -not $KeepMonitorOnClose)" in launcher
    assert '"--lifecycle-overlay-state", (Q $OverlayPidPath)' in launcher
    assert '"--lifecycle-policy-state", (Q $LifecyclePolicyPath)' in launcher
    assert '"--lifecycle-launch-id", $LaunchId' in launcher
    assert "游戏退出后后台读取会自动结束" in launcher


def test_lifecycle_status_owner_is_created_only_after_single_instance_lock() -> None:
    source = (SCRIPTS / "run_wss_live_monitor.py").read_text(encoding="utf-8")
    main_source = source[source.index("def main() -> int:") :]

    lifecycle_index = main_source.index("lifecycle_guard = WssIdleLifecycleGuard(")
    assert main_source.index(
        "package_generation_identity = _validated_package_generation_identity("
    ) < lifecycle_index
    assert main_source.index(
        "package_generation_identity=package_generation_identity,"
    ) < lifecycle_index


def _lifecycle_guard(
    tmp_path: Path,
    *,
    game_states: list[bool | None],
    ui_states: list[bool | None],
    policy_states: list[bool | None] | None = None,
    enabled: bool = True,
) -> WssIdleLifecycleGuard:
    def next_game(_process_name: str) -> bool | None:
        return game_states.pop(0)

    def next_ui(_state_path: Path) -> bool | None:
        return ui_states.pop(0)

    def next_policy(_state_path: Path) -> bool | None:
        return policy_states.pop(0) if policy_states is not None else True

    guard = WssIdleLifecycleGuard(
        process_name="BidKing.exe",
        overlay_state_path=tmp_path / "ahmad_overlay.pid",
        policy_state_path=tmp_path / "lifecycle_policy.json",
        status_path=tmp_path / "lifecycle_status.json",
        launch_id="launch-test",
        enabled=enabled,
        startup_grace_seconds=0.0,
        drain_grace_seconds=2.0,
        exit_confirm_polls=2,
        poll_seconds=0.0,
        game_state_reader=next_game,
        ui_state_reader=next_ui,
        policy_state_reader=next_policy,
    )
    guard.started_monotonic = 0.0
    return guard


def test_lifecycle_process_scan_is_tristate_and_fail_safe() -> None:
    running = [
        SimpleNamespace(info={"name": "steam.exe"}),
        SimpleNamespace(info={"name": "BIDKING.EXE"}),
    ]
    absent = [SimpleNamespace(info={"name": "steam.exe"})]

    class Unreadable:
        @property
        def info(self):
            raise PermissionError("protected")

    assert (
        wss_monitor._named_process_running_state(  # noqa: SLF001
            "BidKing.exe",
            process_iter=lambda _attrs: running,
        )
        is True
    )
    assert (
        wss_monitor._named_process_running_state(  # noqa: SLF001
            "BidKing.exe",
            process_iter=lambda _attrs: absent,
        )
        is False
    )
    assert (
        wss_monitor._named_process_running_state(  # noqa: SLF001
            "BidKing.exe",
            process_iter=lambda _attrs: [Unreadable()],
        )
        is None
    )


def test_game_process_evidence_is_pid_and_creation_time_bound() -> None:
    candidates = [
        SimpleNamespace(
            info={"pid": 41, "name": "BIDKING.EXE", "create_time": 1000.0}
        ),
        SimpleNamespace(
            info={"pid": 42, "name": "BidKing.exe", "create_time": 1001.0}
        ),
        SimpleNamespace(
            info={"pid": 7, "name": "steam.exe", "create_time": 900.0}
        ),
    ]

    assert wss_monitor._game_process_evidence(  # noqa: SLF001
        "BidKing.exe",
        process_iter=lambda _attrs: candidates,
    ) == (True, 42, 1001.0)
    assert wss_monitor._game_process_evidence(  # noqa: SLF001
        "BidKing.exe",
        process_iter=lambda _attrs: [],
    ) == (False, None, None)


def test_lifecycle_policy_is_typed_and_fail_closed(tmp_path: Path) -> None:
    policy_path = tmp_path / "lifecycle_policy.json"
    assert wss_monitor._lifecycle_policy_enabled(policy_path) is None  # noqa: SLF001
    policy_path.write_text('{"auto_stop_when_idle":true}', encoding="utf-8")
    assert wss_monitor._lifecycle_policy_enabled(policy_path) is True  # noqa: SLF001
    policy_path.write_text('{"auto_stop_when_idle":false}', encoding="utf-8")
    assert wss_monitor._lifecycle_policy_enabled(policy_path) is False  # noqa: SLF001
    policy_path.write_text('{"auto_stop_when_idle":1}', encoding="utf-8")
    assert wss_monitor._lifecycle_policy_enabled(policy_path) is None  # noqa: SLF001


def test_lifecycle_overlay_pid_state_rejects_stale_or_corrupt_identity(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "ahmad_overlay.pid"
    state_path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": time.time()}),
        encoding="utf-8",
    )
    assert wss_monitor._pid_state_file_running_state(state_path) is True  # noqa: SLF001

    state_path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": 1}),
        encoding="utf-8",
    )
    assert wss_monitor._pid_state_file_running_state(state_path) is False  # noqa: SLF001
    state_path.write_text("{broken", encoding="utf-8")
    assert wss_monitor._pid_state_file_running_state(state_path) is None  # noqa: SLF001
    state_path.unlink()
    assert wss_monitor._pid_state_file_running_state(state_path) is False  # noqa: SLF001


def test_lifecycle_keeps_capture_while_game_active_then_stops_after_exit(
    tmp_path: Path,
) -> None:
    guard = _lifecycle_guard(
        tmp_path,
        game_states=[True, True, False, False, False, False],
        ui_states=[False, False, False, False, False, False],
    )

    assert guard.tick(0.0) is False
    assert guard.phase == "game_active"
    assert guard.tick(1.0) is False
    # 2026-09-13 selected group exit supersedes the prior UI-detach drain.
    assert guard.tick(2.0) is True
    assert guard.tick(3.0) is True
    assert guard.phase == "stop_requested"
    assert guard.tick(4.0) is True
    assert guard.tick(5.0) is True
    assert guard.phase == "stop_requested"
    assert guard.stop_reason == "bound_game_exited"

    status = json.loads((tmp_path / "lifecycle_status.json").read_text("utf-8"))
    assert status["launch_id"] == "launch-test"
    assert status["phase"] == "stop_requested"
    assert status["game_state"] == "exited"
    assert status["ui_state"] == "exited"


def test_lifecycle_unknown_state_never_stops_and_cancels_drain(
    tmp_path: Path,
) -> None:
    guard = _lifecycle_guard(
        tmp_path,
        game_states=[False, False, None, False, False, False],
        ui_states=[False, False, False, False, True, False],
    )

    assert guard.tick(0.0) is False
    assert guard.tick(1.0) is False
    assert guard.phase == "draining"
    assert guard.tick(2.0) is False
    assert guard.phase == "game_unknown"
    assert guard.drain_deadline_monotonic is None
    assert guard.tick(3.0) is False
    assert guard.tick(4.0) is False
    assert guard.phase == "ready"
    assert guard.drain_deadline_monotonic is None
    assert guard.tick(10.0) is False


def test_lifecycle_reused_monitor_honors_latest_keepalive_policy(
    tmp_path: Path,
) -> None:
    guard = _lifecycle_guard(
        tmp_path,
        game_states=[False, False],
        ui_states=[False, False],
        policy_states=[True, True, False, None],
    )

    assert guard.tick(0.0) is False
    assert guard.tick(1.0) is False
    assert guard.phase == "draining"
    assert guard.tick(2.0) is False
    assert guard.phase == "policy_disabled"
    assert guard.drain_deadline_monotonic is None
    assert guard.tick(10.0) is False
    assert guard.phase == "policy_unknown"


def test_lifecycle_rollback_switch_disables_all_process_probes(
    tmp_path: Path,
) -> None:
    guard = _lifecycle_guard(
        tmp_path,
        game_states=[],
        ui_states=[],
        enabled=False,
    )

    assert guard.tick(100.0) is False
    assert guard.phase == "disabled"
    status = json.loads((tmp_path / "lifecycle_status.json").read_text("utf-8"))
    assert status["auto_stop_enabled"] is False


def test_monitor_log_session_state_preserves_reused_monitor_lineage(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell unavailable")
    launcher = (SCRIPTS / "start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8-sig"
    )
    match = re.search(
        r"function Write-MonitorLogSessionState\(.*?^\}",
        launcher,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    session = str(tmp_path / "monitor_log_session.json").replace("'", "''")
    status = str(tmp_path / "startup_status.log").replace("'", "''")
    first_launch = "11111111-2222-4333-8444-555555555555"
    second_launch = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    probe = (
        match.group(0)
        + f"\n$MonitorLogSession = '{session}'"
        + f"\n$StartupStatus = '{status}'"
        + "\n$MonitorOut = 'monitor.stdout.log'"
        + "\n$MonitorErr = 'monitor.stderr.log'"
        + f"\n$LaunchId = '{first_launch}'"
        + "\n$LaunchStartedAt = 1000"
        + "\nWrite-MonitorLogSessionState 1234 $false"
        + "\nWrite-Output (Get-Content -LiteralPath $MonitorLogSession -Raw -Encoding UTF8)"
        + f"\n$LaunchId = '{second_launch}'"
        + "\n$LaunchStartedAt = 2000"
        + "\nWrite-MonitorLogSessionState 1234 $true"
        + "\nWrite-Output (Get-Content -LiteralPath $MonitorLogSession -Raw -Encoding UTF8)"
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
    states = [
        json.loads(line) for line in completed.stdout.splitlines() if line.strip()
    ]
    assert len(states) == 2
    first, reused = states
    assert first["launch_id"] == first_launch
    assert first["started_at"] == 1000
    assert first["monitor_started_at"] == 1000
    assert first["monitor_pid"] == 1234
    assert first["reused_monitor"] is False
    assert "previous_launch_id" not in first
    assert reused["launch_id"] == second_launch
    assert reused["started_at"] == 2000
    assert reused["monitor_started_at"] == 1000
    assert reused["monitor_pid"] == 1234
    assert reused["reused_monitor"] is True
    assert reused["previous_launch_id"] == first_launch


def test_compiled_launcher_native_lifecycle_argument_forwarding(monkeypatch) -> None:
    generation = "a" * 64
    calls: list[dict[str, object]] = []

    class FakeNativeLifecycle:
        @staticmethod
        def client_lifecycle_contract():
            return (2, generation, True)

        @staticmethod
        def run_client_lifecycle(**kwargs):
            calls.append(kwargs)
            return 0

    monkeypatch.setattr(local_compat_launcher, "_LIFECYCLE_GENERATION", generation)
    monkeypatch.setitem(sys.modules, "bidking_kernel", FakeNativeLifecycle())

    assert (
        local_compat_launcher.main(
            [
                "--python-path",
                sys.executable,
                "--diagnostic-profile",
                "public-safe",
                "--restart",
                "--no-taskbar",
            ]
        )
        == 0
    )
    assert calls == [
        {
            "abi": 2,
            "generation": generation,
            "caller": "_client_launcher",
            "mode": "launch",
            "package_root": str(ROOT),
            "python_path": str(Path(sys.executable).resolve()),
            "diagnostic_profile": "public-safe",
            "restart": True,
            "no_taskbar": True,
            "repair_dry_run": False,
        }
    ]
    assert local_compat_launcher.diagnostics_main() == 0
    assert calls[1] == {
        "abi": 2,
        "generation": generation,
        "caller": "_client_launcher",
        "mode": "diagnostics",
        "package_root": str(ROOT),
        "python_path": "",
        "diagnostic_profile": "portable",
        "restart": False,
        "no_taskbar": False,
        "repair_dry_run": False,
    }
    assert local_compat_launcher.repair_main(["--dry-run"]) == 0
    assert calls[2] == {
        "abi": 2,
        "generation": generation,
        "caller": "_client_launcher",
        "mode": "repair",
        "package_root": str(ROOT),
        "python_path": str(Path(sys.executable).resolve()),
        "diagnostic_profile": "portable",
        "restart": False,
        "no_taskbar": False,
        "repair_dry_run": True,
    }


def test_compiled_launcher_has_no_generic_script_transport() -> None:
    assert not hasattr(local_compat_launcher, "_run_powershell_source")
    assert not hasattr(local_compat_launcher, "_decode_payload")
    assert not hasattr(local_compat_launcher, "_POWERSHELL_PAYLOAD_B64")
    assert not hasattr(local_compat_launcher, "_DIAGNOSTICS_PAYLOAD_B64")
    assert not hasattr(local_compat_launcher, "_REPAIR_PAYLOAD_B64")


def test_cross_runtime_addon_bootstrap_encrypts_source_into_native_loader() -> None:
    core_source = (SCRIPTS / "bidking_wss_capture_core.py").read_text(encoding="utf-8")
    addon_source = (SCRIPTS / "mitm_bidking_wss_addon.py").read_text(encoding="utf-8")

    bootstrap, native_source = render_protected_addon(
        core_source=core_source,
        addon_source=addon_source,
        content_key=bytes(range(32)),
        key_share_a=bytes(reversed(range(32))),
        nonces=(b"\x01" * 12, b"\x02" * 12),
    )

    for protected_text in (bootstrap, native_source):
        assert "release.bid-king.cn" not in protected_text
        assert "class BidKingWssAddon" not in protected_text
        assert "ALLOWED_SEND_MESSAGE_IDS" not in protected_text
    assert "base64" not in bootstrap
    assert "gzip" not in bootstrap
    assert "_local_compat_addon_loader" in bootstrap
    assert "load_addons" in bootstrap
    assert "source(" not in bootstrap
    assert "BCryptDecrypt" in native_source
    assert "PyBytes_FromString" not in native_source
    assert '{"source",' not in native_source
    assert core_source not in native_source
    assert addon_source not in native_source


@pytest.mark.skipif(
    os.name != "nt"
    or not (SCRIPTS / "pkg_assets" / "mitmproxy" / "mitmdump.exe").is_file(),
    reason="pinned Windows mitmproxy runtime unavailable",
)
def test_pinned_mitmproxy_embedded_python_loads_generated_addon(
    tmp_path: Path,
) -> None:
    addon_path = tmp_path / "scripts" / "local_compat_addon.py"
    addon_path.parent.mkdir(parents=True)
    bootstrap, native_source = render_protected_addon(
        core_source=(SCRIPTS / "bidking_wss_capture_core.py").read_text(
            encoding="utf-8"
        ),
        addon_source=(SCRIPTS / "mitm_bidking_wss_addon.py").read_text(
            encoding="utf-8"
        ),
    )
    addon_path.write_text(bootstrap, encoding="utf-8")
    native_path = addon_path.parent / "_local_compat_addon_loader.c"
    native_path.write_text(native_source, encoding="utf-8")
    setup_path = addon_path.parent / "_setup_protected_addon.py"
    setup_path.write_text(
        "\n".join(
            (
                "from setuptools import Extension, setup",
                "from Cython.Build import cythonize",
                "extensions = [Extension(",
                "    '_local_compat_addon_loader',",
                "    ['_local_compat_addon_loader.c'],",
                "    define_macros=[('Py_LIMITED_API', '0x030D0000')],",
                "    py_limited_api=True,",
                "    libraries=['bcrypt'],",
                ")]",
                "setup(ext_modules=cythonize(extensions, quiet=True))",
            )
        ),
        encoding="utf-8",
    )
    build = subprocess.run(
        [sys.executable, "-B", str(setup_path), "build_ext", "--inplace"],
        cwd=addon_path.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert build.returncode == 0, f"{build.stdout}\n{build.stderr}"
    protected_modules = list(addon_path.parent.glob("_local_compat_addon_loader*.pyd"))
    assert len(protected_modules) == 1
    assert protected_modules[0].name == "_local_compat_addon_loader.pyd"
    protected_binary = protected_modules[0].read_bytes()
    assert b"release.bid-king.cn" not in protected_binary
    assert b"class BidKingWssAddon" not in protected_binary
    assert b"ALLOWED_SEND_MESSAGE_IDS" not in protected_binary
    contract_path = SCRIPTS / "client_protection_manifest.json"
    contract = load_operational_contract(contract_path)
    package_generation = "a" * 64
    child_env = dict(os.environ)
    child_env.update(
        {
            "BIDKING_WSS_OPERATIONAL_ABI": contract.abi_version,
            "BIDKING_WSS_PACKAGE_ROOT": str(tmp_path.resolve()),
            "BIDKING_WSS_PACKAGE_GENERATION": package_generation,
            "BIDKING_WSS_OPERATIONAL_GENERATION": contract.generation_sha256,
            "BIDKING_WSS_RUNTIME_ROLE": "main",
        }
    )
    client_load = subprocess.run(
        [
            sys.executable,
            "-E",
            "-s",
            "-c",
            (
                "import local_compat_addon as addon; "
                "assert len(addon.addons) == 1; "
                "assert addon.addons[0].sanitizer.pending_bytes() == 0; "
                "import _local_compat_addon_loader as loader; "
                "assert not hasattr(loader, 'source'); "
                "assert callable(loader.load_addons)"
            ),
        ],
        cwd=addon_path.parent,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert client_load.returncode == 0, f"{client_load.stdout}\n{client_load.stderr}"

    ok, reason = smoke_mitmdump_addon(
        mitmdump_path=SCRIPTS / "pkg_assets" / "mitmproxy" / "mitmdump.exe",
        addon_path=addon_path,
        confdir=tmp_path / "conf",
        timeout=3.0,
        contract_path=contract_path,
        package_root=tmp_path,
        package_generation=package_generation,
    )

    assert ok, reason
    assert reason == "ready"


@pytest.mark.skipif(
    os.name != "nt"
    or not (SCRIPTS / "pkg_assets" / "mitmproxy" / "mitmdump.exe").is_file(),
    reason="pinned Windows mitmproxy runtime unavailable",
)
def test_pinned_mitmproxy_smoke_rejects_broken_addon(tmp_path: Path) -> None:
    addon_path = tmp_path / "scripts" / "local_compat_addon.py"
    addon_path.parent.mkdir(parents=True)
    addon_path.write_text(
        "from _missing_cross_runtime_native_module import addons\n",
        encoding="utf-8",
    )

    ok, reason = smoke_mitmdump_addon(
        mitmdump_path=SCRIPTS / "pkg_assets" / "mitmproxy" / "mitmdump.exe",
        addon_path=addon_path,
        confdir=tmp_path / "conf",
        timeout=3.0,
    )

    assert not ok
    assert reason == "addon_import_failed"


def test_monitor_fails_loudly_when_capture_helper_exits_early() -> None:
    source = (SCRIPTS / "run_wss_live_monitor.py").read_text(encoding="utf-8")

    assert "helper_exited_unexpectedly = False" in source
    assert source.count("helper_exited_unexpectedly = True") == 2
    assert "wss_capture_helper_stopped_unexpectedly" in source
    assert "code={child.returncode} reason={error_code}" in source
    assert 'phase="exited_unexpectedly"' in source
    assert "write_capture_status(" in source


def test_monitor_polls_packaged_redirector_without_mutating_network_or_security() -> None:
    source = (SCRIPTS / "run_wss_live_monitor.py").read_text(encoding="utf-8")

    assert 'REDIRECTOR_PROCESS_NAME = "windows-redirector.exe"' in source
    assert "_named_process_running_state(" in source
    assert "redirector_liveness.observe(" in source
    assert '_REDIRECTOR_MISSING_STABLE_ERROR = "wss_redirector_missing_stable"' in source
    for forbidden in (
        "netsh winhttp set",
        "Set-NetRoute",
        "Remove-NetRoute",
        "Set-DnsClientServerAddress",
        "Disable-NetAdapter",
        "Set-MpPreference",
        "Disable-WindowsOptionalFeature",
    ):
        assert forbidden not in source


def _sc_result(
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _sc_driver_config(
    driver_path: str,
    *,
    service_type: int = 1,
    start_type: int = 3,
    error_control: int = 1,
    dependencies: tuple[str, ...] = (),
    include_binary_path: bool = True,
) -> SimpleNamespace:
    lines = [
        "[SC] QueryServiceConfig SUCCESS",
        f"        TYPE               : {service_type}  KERNEL_DRIVER",
        f"        START_TYPE         : {start_type}  DEMAND_START",
        f"        ERROR_CONTROL      : {error_control}  NORMAL",
    ]
    if include_binary_path:
        lines.append(f"        BINARY_PATH_NAME   : {driver_path}")
    if dependencies:
        lines.append(f"        DEPENDENCIES       : {dependencies[0]}")
        lines.extend(f"                             {item}" for item in dependencies[1:])
    else:
        lines.append("        DEPENDENCIES       :")
    lines.append("        SERVICE_START_NAME :")
    return _sc_result(0, "\n".join(lines))


def _assert_no_service_mutation(calls: list[list[str]]) -> None:
    assert not any(
        len(command) > 1
        and command[1] in {"create", "start", "stop", "delete", "config"}
        for command in calls
    )


@pytest.mark.parametrize(
    "configured_path",
    (
        "{exact}",
        '"{exact}"',
        "{upper}",
        "{forward_slash}",
        "\\??\\{exact}",
        "\\\\?\\{exact}",
        "\\DosDevices\\{exact}",
    ),
)
def test_windivert_preloader_accepts_only_normalized_exact_running_service_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_path: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    exact = str(driver.resolve())
    actual = configured_path.format(
        exact=exact,
        upper=exact.upper(),
        forward_slash=exact.replace("\\", "/"),
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            return _sc_result(0, "STATE : 4 RUNNING")
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            return _sc_driver_config(actual)
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    assert preloader.prepare(driver) == "already_preloaded"
    assert ["sc.exe", "qc", "BidKingWinDivertR1"] in calls
    assert ["sc.exe", "create", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "start", "BidKingWinDivertR1"] not in calls

    preloader.release()
    assert calls.count(["sc.exe", "stop", "BidKingWinDivertR1"]) == 1
    assert calls.count(["sc.exe", "delete", "BidKingWinDivertR1"]) == 1

    preloader.release()
    assert calls.count(["sc.exe", "stop", "BidKingWinDivertR1"]) == 1
    assert calls.count(["sc.exe", "delete", "BidKingWinDivertR1"]) == 1


def test_windivert_preloader_reuses_only_exact_stopped_product_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    product_queries = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal product_queries
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            product_queries += 1
            return _sc_result(
                0,
                "STATE : 1 STOPPED" if product_queries == 1 else "STATE : 4 RUNNING",
            )
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            return _sc_driver_config(str(driver.resolve()))
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    assert preloader.prepare(driver) == "preloaded"
    assert ["sc.exe", "qc", "BidKingWinDivertR1"] in calls
    assert ["sc.exe", "start", "BidKingWinDivertR1"] in calls
    assert ["sc.exe", "create", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "delete", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "stop", "WinDivert"] not in calls
    assert ["sc.exe", "delete", "WinDivert"] not in calls


@pytest.mark.parametrize(
    ("product_state", "conflict_kind"),
    (
        (4, "path"),
        (1, "path"),
        (4, "service_type"),
        (4, "start_type"),
        (4, "missing_path"),
        (4, "relative_path"),
        (4, "query_failed"),
    ),
)
def test_windivert_preloader_rejects_unproven_existing_product_service_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    product_state: int,
    conflict_kind: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            return _sc_result(0, f"STATE : {product_state}")
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            if conflict_kind == "path":
                return _sc_driver_config(str(driver.resolve()) + ".near-miss")
            if conflict_kind == "service_type":
                return _sc_driver_config(
                    str(driver.resolve()),
                    service_type=2,
                )
            if conflict_kind == "start_type":
                return _sc_driver_config(
                    str(driver.resolve()),
                    start_type=2,
                )
            if conflict_kind == "missing_path":
                return _sc_driver_config(
                    str(driver.resolve()),
                    include_binary_path=False,
                )
            if conflict_kind == "relative_path":
                return _sc_driver_config(driver.name)
            return _sc_result(
                5,
                "",
                "[SC] QueryServiceConfig FAILED 5 PRIVATE_TOKEN",
            )
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    expected_state = (
        "failed_access_denied"
        if conflict_kind == "query_failed"
        else "failed_service_conflict"
    )
    assert preloader.prepare(driver) == expected_state
    assert preloader.phase == "verify_product"
    preloader.release()

    _assert_no_service_mutation(calls)
    assert "PRIVATE_TOKEN" not in repr(preloader)
    assert ["sc.exe", "stop", "WinDivert"] not in calls
    assert ["sc.exe", "delete", "WinDivert"] not in calls


def test_windivert_preloader_release_revalidates_identity_before_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    config_queries = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal config_queries
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            return _sc_result(0, "STATE : 4 RUNNING")
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            config_queries += 1
            path = (
                str(driver.resolve())
                if config_queries == 1
                else str(driver.resolve()) + ".replaced"
            )
            return _sc_driver_config(path)
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    assert preloader.prepare(driver) == "already_preloaded"
    preloader.release()

    assert ["sc.exe", "stop", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "delete", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "stop", "WinDivert"] not in calls
    assert ["sc.exe", "delete", "WinDivert"] not in calls


def test_windivert_preloader_bypasses_stopped_legacy_service_without_mutating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            if calls.count(command) == 1:
                return _sc_result(1060, "[SC] OpenService FAILED 1060")
            return _sc_result(0, "STATE : 4 RUNNING")
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            return _sc_driver_config(str(driver.resolve()))
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    assert preloader.prepare(driver) == "preloaded"
    assert [
        "sc.exe",
        "create",
        "BidKingWinDivertR1",
        "type=",
        "kernel",
        "start=",
        "demand",
        "error=",
        "normal",
        "binPath=",
        str(driver.resolve()),
    ] in calls
    assert ["sc.exe", "start", "BidKingWinDivertR1"] in calls
    assert ["sc.exe", "stop", "WinDivert"] not in calls
    assert ["sc.exe", "delete", "WinDivert"] not in calls

    preloader.release()

    assert ["sc.exe", "stop", "BidKingWinDivertR1"] in calls
    assert ["sc.exe", "delete", "BidKingWinDivertR1"] in calls


@pytest.mark.parametrize(
    ("legacy_result", "expected"),
    [
        (_sc_result(1060, "[SC] OpenService FAILED 1060"), "not_needed"),
        (_sc_result(0, "STATE : 4 RUNNING"), "not_needed"),
    ],
)
def test_windivert_preloader_is_noop_without_a_stopped_legacy_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_result: SimpleNamespace,
    expected: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return legacy_result

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    assert preloader.prepare(driver) == expected
    assert calls == [["sc.exe", "query", "WinDivert"]]


def test_windivert_preloader_rejects_untrusted_driver_without_service_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"unexpected-driver")
    calls: list[list[str]] = []
    preloader = WinDivertCompatibilityPreloader(
        runner=lambda command: calls.append(command)
    )

    assert preloader.prepare(driver) == "failed_untrusted_driver"
    assert calls == []


def test_windivert_preloader_reuses_and_cleans_only_exact_stopped_product_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    product_queries = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal product_queries
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            product_queries += 1
            return _sc_result(
                0,
                "STATE : 1 STOPPED" if product_queries == 1 else "STATE : 4 RUNNING",
            )
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            return _sc_driver_config(str(driver.resolve()))
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    assert preloader.prepare(driver) == "preloaded"
    assert ["sc.exe", "start", "BidKingWinDivertR1"] in calls
    assert ["sc.exe", "create", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "stop", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "delete", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "stop", "WinDivert"] not in calls
    assert ["sc.exe", "delete", "WinDivert"] not in calls

    preloader.release()

    assert ["sc.exe", "stop", "BidKingWinDivertR1"] in calls
    assert ["sc.exe", "delete", "BidKingWinDivertR1"] in calls


def test_windivert_preloader_classifies_start_failure_without_retaining_private_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            return _sc_result(1060, "[SC] OpenService FAILED 1060")
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            return _sc_driver_config(str(driver.resolve()))
        if command == ["sc.exe", "start", "BidKingWinDivertR1"]:
            return _sc_result(
                5,
                "",
                "[SC] StartService FAILED 5 token=PRIVATE_TOKEN path=C:\\Users\\example",
            )
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    assert preloader.prepare(driver) == "failed_access_denied"
    assert "PRIVATE_TOKEN" not in repr(preloader)
    assert "C:\\Users\\example" not in repr(preloader)
    assert ["sc.exe", "delete", "BidKingWinDivertR1"] in calls
    assert ["sc.exe", "delete", "WinDivert"] not in calls


@pytest.mark.parametrize(
    ("failure_command", "expected_phase"),
    (
        (("sc.exe", "query", "WinDivert"), "query_legacy"),
        (("sc.exe", "query", "BidKingWinDivertR1"), "query_product"),
        (("sc.exe", "create", "BidKingWinDivertR1"), "create_product"),
        (("sc.exe", "qc", "BidKingWinDivertR1"), "verify_product"),
        (("sc.exe", "start", "BidKingWinDivertR1"), "start_product"),
        (("sc.exe", "query_running", "BidKingWinDivertR1"), "query_running"),
    ),
)
def test_windivert_preloader_preserves_access_denied_failure_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_command: tuple[str, ...],
    expected_phase: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    product_queries = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal product_queries
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            product_queries += 1
        command_key = tuple(command[: len(failure_command)])
        should_fail = command_key == failure_command
        if expected_phase == "query_product":
            should_fail = (
                command == ["sc.exe", "query", "BidKingWinDivertR1"]
                and product_queries == 1
            )
        elif expected_phase == "query_running":
            should_fail = (
                command == ["sc.exe", "query", "BidKingWinDivertR1"]
                and product_queries > 1
            )
        if should_fail:
            return _sc_result(
                5,
                "",
                "[SC] operation FAILED 5 token=PRIVATE_TOKEN path=C:\\Users\\example",
            )
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            return _sc_result(1060, "[SC] OpenService FAILED 1060")
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            return _sc_driver_config(str(driver.resolve()))
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    assert preloader.prepare(driver) == "failed_access_denied"
    assert preloader.phase == expected_phase
    assert "PRIVATE_TOKEN" not in repr(preloader)
    assert "private" not in repr(preloader).lower()


def test_windivert_preloader_reports_cleanup_access_denied_without_touching_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")
    driver = tmp_path / "WinDivert64.sys"
    driver.write_bytes(b"audited-driver")
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    calls: list[list[str]] = []
    product_queries = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal product_queries
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            product_queries += 1
            return _sc_result(
                1060 if product_queries == 1 else 0,
                (
                    "[SC] OpenService FAILED 1060"
                    if product_queries == 1
                    else "STATE : 4 RUNNING"
                ),
            )
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            return _sc_driver_config(str(driver.resolve()))
        if command == ["sc.exe", "stop", "BidKingWinDivertR1"]:
            return _sc_result(5, "", "[SC] ControlService FAILED 5 PRIVATE_TOKEN")
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)
    assert preloader.prepare(driver) == "preloaded"

    preloader.release()

    assert preloader.state == "failed_access_denied"
    assert preloader.phase == "cleanup"
    assert ["sc.exe", "delete", "BidKingWinDivertR1"] not in calls
    assert ["sc.exe", "stop", "WinDivert"] not in calls
    assert ["sc.exe", "delete", "WinDivert"] not in calls


@pytest.mark.parametrize(
    (
        "prepare_access_denied",
        "cleanup_access_denied",
        "expected_preload_state",
        "expected_preload_phase",
    ),
    (
        (False, True, "failed_access_denied", "cleanup"),
        (False, False, "preloaded", "cleanup"),
        (True, False, "failed_access_denied", "query_legacy"),
    ),
)
def test_main_helper_popen_failure_persists_fixed_final_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prepare_access_denied: bool,
    cleanup_access_denied: bool,
    expected_preload_state: str,
    expected_preload_phase: str,
) -> None:
    log_dir = tmp_path / "logs"
    confdir = tmp_path / "conf"
    driver = tmp_path / "WinDivert64.sys"
    mitmdump = tmp_path / "mitmdump.exe"
    driver.write_bytes(b"audited-driver")
    mitmdump.write_bytes(b"helper")
    calls: list[list[str]] = []
    events: list[str] = []
    product_queries = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal product_queries
        calls.append(command)
        if command == ["sc.exe", "query", "WinDivert"]:
            if prepare_access_denied:
                return _sc_result(
                    5,
                    "",
                    "[SC] OpenService FAILED 5 PRIVATE_TOKEN C:\\Users\\example",
                )
            return _sc_result(0, "STATE : 1 STOPPED")
        if command == ["sc.exe", "query", "BidKingWinDivertR1"]:
            product_queries += 1
            return _sc_result(
                1060 if product_queries == 1 else 0,
                (
                    "[SC] OpenService FAILED 1060"
                    if product_queries == 1
                    else "STATE : 4 RUNNING"
                ),
            )
        if command == ["sc.exe", "qc", "BidKingWinDivertR1"]:
            return _sc_driver_config(str(driver.resolve()))
        if command == ["sc.exe", "stop", "BidKingWinDivertR1"]:
            events.append("cleanup")
            if cleanup_access_denied:
                return _sc_result(
                    5,
                    "",
                    "[SC] ControlService FAILED 5 PRIVATE_TOKEN C:\\Users\\example",
                )
            return _sc_result(0)
        return _sc_result(0)

    preloader = WinDivertCompatibilityPreloader(runner=runner)

    class FakeMonitor:
        def start(self) -> None:
            events.append("monitor.start")

        def stop(self) -> None:
            events.append("monitor.stop")

    fake_monitor = FakeMonitor()
    original_atomic_write = wss_monitor._atomic_write_json

    def record_atomic_write(path: Path, payload: dict[str, object]) -> None:
        events.append("sidecar.write")
        original_atomic_write(path, payload)

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
        lambda **_kwargs: fake_monitor,
    )
    monkeypatch.setattr(
        wss_monitor,
        "_start_version_and_activation_threads",
        lambda _log_dir: None,
    )
    monkeypatch.setattr(
        wss_monitor,
        "build_mitmdump_command",
        lambda **_kwargs: ["mitmdump.exe", "--mode", "local:BidKing.exe"],
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
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )
    monkeypatch.setattr(
        wss_monitor,
        "_windows_elevation_state",
        lambda: "elevated",
    )
    monkeypatch.setattr(wss_monitor, "_atomic_write_json", record_atomic_write)

    def fail_popen(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("PRIVATE_TOKEN C:\\Users\\example\\mitmdump.exe")

    monkeypatch.setattr(wss_monitor.subprocess, "Popen", fail_popen)

    assert wss_monitor.main() == 5

    payload = json.loads(
        (log_dir / "wss_helper_status.json").read_text(encoding="utf-8")
    )
    assert payload == {
        "schema_version": 4,
        "owner_kind": "bidking_wss_monitor",
        "owner_sid": "",
        "package_generation_identity": "",
        "runtime_identity": "",
        "component_identity": "",
        "monitor_epoch": "",
        "monitor_started_at_unix_ms": 0,
        "ts_unix_ms": payload["ts_unix_ms"],
        "phase": "helper_start_failed",
        "monitor_pid": os.getpid(),
        "helper_pid": 0,
        "launch_id": "",
        "redirector_state": "unknown",
        "windivert_state": "unknown",
        "error_code": "wss_capture_helper_start_failed",
        "windivert_preload_state": expected_preload_state,
        "windivert_preload_phase": expected_preload_phase,
        "monitor_elevation_state": "elevated",
    }
    if prepare_access_denied:
        assert "cleanup" not in events
    else:
        assert events.index("cleanup") < events.index("sidecar.write")
    assert events.index("monitor.stop") < events.index("sidecar.write")
    assert (
        ["sc.exe", "delete", "BidKingWinDivertR1"] in calls
    ) is (not prepare_access_denied and not cleanup_access_denied)
    assert ["sc.exe", "stop", "WinDivert"] not in calls
    assert ["sc.exe", "delete", "WinDivert"] not in calls
    captured = capsys.readouterr()
    assert "wss_capture_helper_start_failed" in captured.err
    for canary in ("PRIVATE_TOKEN", "private", "mitmdump.exe"):
        assert canary not in captured.err
        assert canary not in json.dumps(payload)


@pytest.mark.parametrize(
    ("checker_result", "expected"),
    ((1, "elevated"), (0, "not_elevated")),
)
def test_windows_elevation_state_is_fixed_and_privacy_safe(
    monkeypatch: pytest.MonkeyPatch,
    checker_result: int,
    expected: str,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")

    assert (
        wss_monitor._windows_elevation_state(lambda: checker_result)
        == expected
    )


def test_windows_elevation_state_fails_closed_on_probe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wss_monitor.os, "name", "nt")

    def fail() -> int:
        raise OSError("PRIVATE_TOKEN C:\\Users\\example")

    assert wss_monitor._windows_elevation_state(fail) == "unknown"


def test_monitor_preloads_before_helper_and_releases_after_helper_shutdown() -> None:
    source = (SCRIPTS / "run_wss_live_monitor.py").read_text(encoding="utf-8")

    prepare_at = source.index("windivert_preloader.prepare(")
    child_at = source.index("child = subprocess.Popen(")
    teardown_at = source.index("cleanup_result = _shutdown_capture_group(", child_at)
    import inspect
    teardown = inspect.getsource(wss_monitor._shutdown_capture_group)
    assert prepare_at < child_at < teardown_at
    assert teardown.index("child.terminate()") < teardown.index("preloader.release(deadline=deadline)")


def test_launcher_and_monitor_record_fixed_elevation_identity() -> None:
    launcher = (SCRIPTS / "start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8"
    )
    monitor = (SCRIPTS / "run_wss_live_monitor.py").read_text(encoding="utf-8")

    assert "launcher_elevation_state" in launcher
    assert "_windows_elevation_state(" in monitor
    assert "monitor_elevation_state=monitor_elevation_state" in monitor


def test_monitor_binds_recovery_evidence_to_current_game_process_epoch() -> None:
    source = (SCRIPTS / "run_wss_live_monitor.py").read_text(encoding="utf-8")
    epoch = wss_monitor.GameCaptureLivenessEpoch()
    epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=1000.0,
    )
    epoch.open_flow("flow-A")
    epoch.record_raw_frame("flow-A")
    epoch.record_accepted_frame("session-A")

    assert not epoch.observe_process(
        game_running=None,
        game_pid=None,
        game_started_at=None,
    )
    assert not epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=None,
    )
    assert epoch.capture_status()["raw_packets"] == 0
    assert epoch.capture_status()["accepted_frames"] == 0
    assert epoch.capture_status()["active_session_id"] == ""
    assert epoch.observe_process(
        game_running=True,
        game_pid=42,
        game_started_at=2000.0,
    )
    assert epoch.capture_status()["raw_packets"] == 0
    assert epoch.capture_status()["accepted_frames"] == 0
    assert epoch.capture_status()["active_session_id"] == ""
    assert "game_capture_epoch = GameCaptureLivenessEpoch()" in source
    assert "capture_status=epoch_capture_status" in source
    assert "game_capture_epoch.record_raw_frame(flow_id)" in source
    assert "game_capture_epoch.record_accepted_frame(" in source
    assert "frame_gate.reset_stream_buffers(clear_session=True)" in source


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "Failed to start the interception process as administrator.",
            "wss_redirector_elevation_denied",
        ),
        (
            "Failed to start the executable: access denied",
            "wss_redirector_start_failed",
        ),
        ("WinDivert Error: private details", "wss_windivert_start_failed"),
        ("Error opening WinDivert handle", "wss_windivert_start_failed"),
        ("Error loading script private.py", "wss_addon_start_failed"),
        ("Addon error: PRIVATE_REQUEST", "wss_addon_start_failed"),
        ("No module named PRIVATE_MODULE", "wss_addon_start_failed"),
        (
            "Error logged during startup, exiting...",
            "wss_capture_helper_start_failed",
        ),
        (
            "Task failed: redirect daemon exited prematurely. disabled backtrace",
            "wss_redirect_daemon_premature_exit",
        ),
        ("request private.example token=PRIVATE_TOKEN", None),
    ],
)
def test_mitmdump_stderr_classifier_only_returns_fixed_codes(
    line: str,
    expected: str | None,
) -> None:
    assert classify_mitmdump_stderr_line(line) == expected


def test_helper_diagnostics_prioritizes_redirector_error_without_retaining_stderr() -> None:
    diagnostics = WssHelperDiagnostics()
    diagnostics.observe_line(
        "Addon error: token=PRIVATE_TOKEN endpoint=private.example.test"
    )
    diagnostics.observe_line(
        "Failed to start the interception process as administrator."
    )

    assert diagnostics.primary_error_code() == "wss_redirector_elevation_denied"
    assert diagnostics.redirector_state() == "elevation_denied"
    assert diagnostics.windivert_state() == "unknown"
    assert "PRIVATE_TOKEN" not in repr(diagnostics)
    assert "private.example.test" not in repr(diagnostics)

    code, message, hint = _helper_error_details(diagnostics)
    assert code == "wss_redirector_elevation_denied"
    assert "Windows" in message
    assert "下载来源标记" in hint


def test_redirector_liveness_requires_grace_and_consecutive_complete_absence() -> None:
    observer = WssRedirectorLiveness(
        grace_seconds=REDIRECTOR_STARTUP_GRACE_SECONDS,
        missing_confirm_polls=REDIRECTOR_MISSING_CONFIRM_POLLS,
    )

    assert (
        observer.observe(
            now=100.0,
            game_running=True,
            redirector_running=False,
            has_current_wss_health=False,
        )
        == "starting"
    )
    assert (
        observer.observe(
            now=100.0 + REDIRECTOR_STARTUP_GRACE_SECONDS + 0.1,
            game_running=True,
            redirector_running=False,
            has_current_wss_health=False,
        )
        == "starting"
    )
    for index in range(1, REDIRECTOR_MISSING_CONFIRM_POLLS):
        expected = (
            "missing_stable"
            if index == REDIRECTOR_MISSING_CONFIRM_POLLS - 1
            else "starting"
        )
        assert (
            observer.observe(
                now=101.0 + REDIRECTOR_STARTUP_GRACE_SECONDS + index,
                game_running=True,
                redirector_running=False,
                has_current_wss_health=False,
            )
            == expected
        )


def test_redirector_liveness_recovers_and_never_uses_unknown_scan_as_absence() -> None:
    observer = WssRedirectorLiveness(grace_seconds=1.0, missing_confirm_polls=2)

    assert (
        observer.observe(
            now=10.0,
            game_running=True,
            redirector_running=False,
            has_current_wss_health=False,
        )
        == "starting"
    )
    assert (
        observer.observe(
            now=12.0,
            game_running=True,
            redirector_running=None,
            has_current_wss_health=False,
        )
        == "unknown"
    )
    assert (
        observer.observe(
            now=14.0,
            game_running=True,
            redirector_running=True,
            has_current_wss_health=False,
        )
        == "running"
    )
    assert (
        observer.observe(
            now=16.0,
            game_running=True,
            redirector_running=False,
            has_current_wss_health=True,
        )
        == "unknown"
    )


def test_stable_missing_redirector_sidecar_is_fixed_and_privacy_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics = WssHelperDiagnostics()
    diagnostics.observe_line(
        "unclassified token=PRIVATE_TOKEN path=C:\\Users\\example"
    )
    monkeypatch.setattr(wss_monitor.time, "time_ns", lambda: 1_234_500_000_000)

    _write_wss_helper_status(
        tmp_path,
        phase="helper_running",
        helper_pid=4321,
        launch_id="11111111-2222-4333-8444-555555555555",
        diagnostics=diagnostics,
        error_code="wss_redirector_missing_stable",
        redirector_observation_state="missing_stable",
        package_generation_identity="a" * 64,
        runtime_identity="b" * 64,
        component_identity="c" * 64,
        owner_sid="S-1-5-21-1000-1000-1000-1001",
        monitor_started_at_unix_ms=1_234_000,
    )

    raw = (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    assert json.loads(raw) == {
        "schema_version": 4,
        "owner_kind": "bidking_wss_monitor",
        "owner_sid": "S-1-5-21-1000-1000-1000-1001",
        "package_generation_identity": "a" * 64,
        "runtime_identity": "b" * 64,
        "component_identity": "c" * 64,
        "monitor_epoch": "11111111-2222-4333-8444-555555555555",
        "monitor_started_at_unix_ms": 1_234_000,
        "ts_unix_ms": 1_234_500,
        "phase": "helper_running",
        "monitor_pid": os.getpid(),
        "helper_pid": 4321,
        "launch_id": "11111111-2222-4333-8444-555555555555",
        "redirector_state": "missing_stable",
        "windivert_state": "unknown",
        "error_code": "wss_redirector_missing_stable",
    }
    assert "PRIVATE_TOKEN" not in raw
    assert "private-user" not in raw


def test_helper_sidecar_rejects_unlisted_error_and_redirector_values(
    tmp_path: Path,
) -> None:
    _write_wss_helper_status(
        tmp_path,
        phase="helper_running",
        helper_pid=4321,
        launch_id="current-launch",
        diagnostics=WssHelperDiagnostics(),
        error_code="PRIVATE_TOKEN",
        redirector_observation_state="missing_stable:PRIVATE_PATH",
    )

    raw = (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["redirector_state"] == "unknown"
    assert "error_code" not in payload
    assert "PRIVATE" not in raw


def test_helper_sidecar_uses_fixed_phase_when_input_is_not_whitelisted(
    tmp_path: Path,
) -> None:
    _write_wss_helper_status(
        tmp_path,
        phase="helper_start_failed:PRIVATE_TOKEN:C:\\Users\\example",
        helper_pid=0,
        launch_id="current-launch",
        diagnostics=WssHelperDiagnostics(),
        error_code="wss_capture_helper_start_failed",
    )

    raw = (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["phase"] == "unknown"
    assert payload["helper_pid"] == 0
    assert payload["error_code"] == "wss_capture_helper_start_failed"
    assert "PRIVATE" not in raw
    assert "private" not in raw.lower()


def test_helper_sidecar_whitelists_windivert_preload_state(
    tmp_path: Path,
) -> None:
    _write_wss_helper_status(
        tmp_path,
        phase="helper_running",
        helper_pid=4321,
        launch_id="current-launch",
        diagnostics=WssHelperDiagnostics(),
        windivert_preload_state="preloaded:PRIVATE_TOKEN",
    )

    raw = (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "windivert_preload_state" not in payload
    assert "PRIVATE" not in raw

    _write_wss_helper_status(
        tmp_path,
        phase="helper_running",
        helper_pid=4321,
        launch_id="current-launch",
        diagnostics=WssHelperDiagnostics(),
        windivert_preload_state="preloaded",
    )

    payload = json.loads(
        (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    )
    assert payload["windivert_preload_state"] == "preloaded"


def test_helper_sidecar_whitelists_preload_phase_and_monitor_elevation(
    tmp_path: Path,
) -> None:
    _write_wss_helper_status(
        tmp_path,
        phase="helper_running",
        helper_pid=4321,
        launch_id="current-launch",
        diagnostics=WssHelperDiagnostics(),
        windivert_preload_state="failed_access_denied",
        windivert_preload_phase="start_product",
        monitor_elevation_state="elevated",
    )

    payload = json.loads(
        (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    )
    assert payload["windivert_preload_phase"] == "start_product"
    assert payload["monitor_elevation_state"] == "elevated"

    _write_wss_helper_status(
        tmp_path,
        phase="helper_running",
        helper_pid=4321,
        launch_id="current-launch",
        diagnostics=WssHelperDiagnostics(),
        windivert_preload_state="failed_access_denied",
        windivert_preload_phase="start_product:PRIVATE_TOKEN",
        monitor_elevation_state="elevated:C:\\Users\\example",
    )

    raw = (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "windivert_preload_phase" not in payload
    assert "monitor_elevation_state" not in payload
    assert "PRIVATE" not in raw
    assert "private" not in raw.lower()


def test_helper_status_sidecar_contains_only_safe_structured_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics = WssHelperDiagnostics()
    diagnostics.observe_line(
        "Failed to start the interception process as administrator. "
        "token=PRIVATE_TOKEN endpoint=private.example.test"
    )
    monkeypatch.setattr(wss_monitor.time, "time_ns", lambda: 1_234_500_000_000)

    _write_wss_helper_status(
        tmp_path,
        phase="exited_unexpectedly",
        helper_pid=4321,
        launch_id="11111111-2222-4333-8444-555555555555",
        diagnostics=diagnostics,
        helper_exit_code=1,
        error_code="wss_redirector_elevation_denied",
        package_generation_identity="a" * 64,
        runtime_identity="b" * 64,
        component_identity="c" * 64,
        owner_sid="S-1-5-21-1000-1000-1000-1001",
        monitor_started_at_unix_ms=1_234_000,
    )

    raw = (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload == {
        "schema_version": 4,
        "owner_kind": "bidking_wss_monitor",
        "owner_sid": "S-1-5-21-1000-1000-1000-1001",
        "package_generation_identity": "a" * 64,
        "runtime_identity": "b" * 64,
        "component_identity": "c" * 64,
        "monitor_epoch": "11111111-2222-4333-8444-555555555555",
        "monitor_started_at_unix_ms": 1_234_000,
        "ts_unix_ms": 1_234_500,
        "phase": "exited_unexpectedly",
        "monitor_pid": os.getpid(),
        "helper_pid": 4321,
        "launch_id": "11111111-2222-4333-8444-555555555555",
        "redirector_state": "elevation_denied",
        "windivert_state": "unknown",
        "helper_exit_code": 1,
        "error_code": "wss_redirector_elevation_denied",
    }
    assert "PRIVATE_TOKEN" not in raw
    assert "private.example.test" not in raw


def test_known_driver_plus_premature_exit_is_runtime_compatibility_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mitmdump_path = tmp_path / "mitmdump.exe"
    diagnostics = WssHelperDiagnostics()
    diagnostics.observe_line(
        "Task failed: redirect daemon exited prematurely. "
        "disabled backtrace token=PRIVATE_TOKEN"
    )
    monkeypatch.setattr(
        wss_monitor,
        "_sha256_file",
        lambda _path: KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    )

    runtime_compatibility = inspect_runtime_compatibility(
        diagnostics,
        mitmdump_path=mitmdump_path,
    )
    code, message, hint = _helper_error_details(
        diagnostics,
        runtime_compatibility=runtime_compatibility,
    )

    assert runtime_compatibility == {
        "runtime_compatibility_state": RUNTIME_COMPATIBILITY_FAILED_STATE,
        "windivert_driver_sha256": KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    }
    assert code == "wss_runtime_compatibility_failed"
    assert "Windows 安全策略" in message
    assert "保持系统安全功能开启" in hint
    assert "PRIVATE_TOKEN" not in repr(diagnostics)


def test_premature_exit_with_other_driver_stays_generic_redirector_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics = WssHelperDiagnostics()
    diagnostics.observe_line("Task failed: redirect daemon exited prematurely.")
    monkeypatch.setattr(wss_monitor, "_sha256_file", lambda _path: "0" * 64)

    runtime_compatibility = inspect_runtime_compatibility(
        diagnostics,
        mitmdump_path=tmp_path / "mitmdump.exe",
    )
    code, _message, _hint = _helper_error_details(
        diagnostics,
        runtime_compatibility=runtime_compatibility,
    )

    assert runtime_compatibility == {}
    assert code == "wss_redirector_start_failed"


def test_runtime_compatibility_sidecar_contains_only_fixed_safe_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics = WssHelperDiagnostics()
    diagnostics.observe_line(
        "Task failed: redirect daemon exited prematurely. "
        "token=PRIVATE_TOKEN endpoint=private.example.test"
    )
    runtime_compatibility = {
        "runtime_compatibility_state": RUNTIME_COMPATIBILITY_FAILED_STATE,
        "windivert_driver_sha256": KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
        "unexpected_private_field": "PRIVATE_TOKEN",
        "exception": r"C:\Users\example\redirector failed",
        "wss_body": "private.example.test",
    }
    monkeypatch.setattr(wss_monitor.time, "time_ns", lambda: 1_234_500_000_000)

    _write_wss_helper_status(
        tmp_path,
        phase="exited_unexpectedly",
        helper_pid=4321,
        launch_id="11111111-2222-4333-8444-555555555555",
        diagnostics=diagnostics,
        helper_exit_code=1,
        error_code="wss_runtime_compatibility_failed",
        runtime_compatibility=runtime_compatibility,
        package_generation_identity="a" * 64,
        runtime_identity="b" * 64,
        component_identity="c" * 64,
        owner_sid="S-1-5-21-1000-1000-1000-1001",
        monitor_started_at_unix_ms=1_234_000,
    )

    raw = (tmp_path / "wss_helper_status.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload == {
        "schema_version": 4,
        "owner_kind": "bidking_wss_monitor",
        "owner_sid": "S-1-5-21-1000-1000-1000-1001",
        "package_generation_identity": "a" * 64,
        "runtime_identity": "b" * 64,
        "component_identity": "c" * 64,
        "monitor_epoch": "11111111-2222-4333-8444-555555555555",
        "monitor_started_at_unix_ms": 1_234_000,
        "ts_unix_ms": 1_234_500,
        "phase": "exited_unexpectedly",
        "monitor_pid": os.getpid(),
        "helper_pid": 4321,
        "launch_id": "11111111-2222-4333-8444-555555555555",
        "redirector_state": "start_failed",
        "windivert_state": "known_historical_blocked_driver",
        "helper_exit_code": 1,
        "error_code": "wss_runtime_compatibility_failed",
        "runtime_compatibility_state": RUNTIME_COMPATIBILITY_FAILED_STATE,
        "windivert_driver_sha256": KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    }
    assert "PRIVATE_TOKEN" not in raw
    assert "private.example.test" not in raw
    assert "private-user" not in raw
    assert "unexpected_private_field" not in raw
    assert "exception" not in raw
    assert "wss_body" not in raw


def test_fixed_runtime_manifest_matches_classified_historical_driver() -> None:
    manifest = json.loads(
        (SCRIPTS / "mitmproxy_runtime_manifest.json").read_text(encoding="utf-8")
    )
    driver = manifest["required_files"][
        "_internal/mitmproxy_windows/WinDivert64.sys"
    ]

    assert manifest["version"] == "12.2.3"
    assert manifest["architecture"] == "windows-x86_64"
    assert driver == {
        "bytes": 94144,
        "sha256": KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256,
    }


def test_portable_build_bundles_runtime_but_never_a_shared_ca() -> None:
    build = (SCRIPTS / "build_live_portable.ps1").read_text(encoding="utf-8-sig")
    remove_ca = (SCRIPTS / "remove_wss_capture_ca.ps1").read_text(encoding="utf-8-sig")

    for name in (
        "run_wss_live_monitor.py",
        "mitm_bidking_wss_addon.py",
        "bidking_wss_capture_core.py",
        "_local_compat_core.py",
        "local_compat_launcher.py",
        "remove_wss_capture_ca.ps1",
    ):
        assert name in build
    assert "scripts/pkg_assets/mitmproxy" in build
    assert "mitmproxy_runtime_manifest.json" in build
    assert '"mitmproxy_runtime_manifest.json",' in build
    assert (
        '$mitmproxyManifestDst = Join-Path $scriptsDst "mitmproxy_runtime_manifest.json"'
        in build
    )
    assert "Stage 缺 bundled WSS runtime manifest" in build
    assert "staged mitmproxy_runtime_manifest.json 与受控源码字节不一致" in build
    assert "verify_release_asset_inventory.py" in build
    assert "native lifecycle 合同或公版诊断入口实跑失败" in build
    assert "_launcher_candidate_smoke.py" not in build
    assert '$env:BIDKING_DIAG_NONINTERACTIVE = "1"' in build
    assert "vendor\\mitmproxy" in build
    assert "LICENSE.txt" in build
    assert "windows-redirector.exe" in build
    assert "WinDivert64.sys" in build
    assert "mitmproxy-ca.pem" not in build
    assert "mitmproxy-ca.p12" not in build
    assert "MitmproxyInstallerSHA256:" in build
    assert "MitmproxyInventorySHA256:" in build
    assert "[switch]$SplitCompatibilityRuntime" in build
    assert '"$BaseName-online"' in build
    assert "文件名不会与本精简包冲突" in build
    assert "CompatibilityRuntimeMode:" in build
    assert "CompatibilityRuntimeArchiveSHA256:" in build
    assert "build_release_runtime_component.py" in build
    assert "local_compat_component.py" in build
    assert "_runtime_component" in build
    assert "薄主包已移除 vendor/mitmproxy" in build
    assert "发布时必须同时上传组件 ZIP" in build
    assert "staged mitmproxy 无法加载跨运行时 addon" in build
    assert '$extractedMitmproxyManifest = Join-Path (' in build
    assert "解压成品缺 bundled WSS runtime manifest" in build
    assert "解压成品 bounded MotW" in build
    assert "--bundled-manifest $extractedMitmproxyManifest" in build
    bounded_motw_block = build[
        build.index("    $boundedMotwHelper = Join-Path $extractedMitmproxy \"mitmdump.exe\"") : build.index(
            "$extractedMitmproxyIdentity = Invoke-ReleaseAssetGate"
        )
    ]
    assert "$boundedMotwPythonLauncher" in bounded_motw_block
    assert "base64.b64decode(sys.argv.pop(1))" in bounded_motw_block
    assert 'payload = b"[ZoneTransfer]\\r\\nZoneId=3\\r\\n"' in bounded_motw_block
    assert "BIDKING_EXTRACTED_MOTW_WRITE_OK" in bounded_motw_block
    assert "BIDKING_EXTRACTED_MOTW_PROBE_OK" in bounded_motw_block
    assert "$boundedMotwWriterExit -ne 0" in bounded_motw_block
    assert "$boundedMotwProbeExit -ne 0" in bounded_motw_block
    assert "$boundedMotwHelperBytes" in bounded_motw_block
    assert "$boundedMotwHelperSHA256" in bounded_motw_block
    assert "$boundedMotwCacheStream" in bounded_motw_block
    assert "$mitmproxyManifestPath" not in bounded_motw_block
    assert "[System.IO.File]::WriteAllBytes" not in bounded_motw_block
    assert "[System.IO.File]::Exists" not in bounded_motw_block
    assert "[System.IO.File]::ReadAllBytes" not in bounded_motw_block
    assert "bundled MotW runtime default-stream-only copy 通过" in build
    assert (
        ') $extractedMitmproxyManifest "extracted/runtime-component mitmproxy runtime"'
        in build
    )
    assert "& $BuildPython -B $wssAddonBootstrapBuilder @mitmproxyAddonSmokeArgs" in build
    assert '"--contract"' in build
    assert '"--package-generation"' in build
    assert "--options 2>&1" not in build
    assert "plaintext_wss_module" in (SCRIPTS / "audit_release_zip.py").read_text(
        encoding="utf-8-sig"
    )
    assert "build_wss_addon_bootstrap.py" in build
    assert "真实 addon load 已验证" in build
    assert '--mode",\n        "regular' in (
        SCRIPTS / "build_wss_addon_bootstrap.py"
    ).read_text(encoding="utf-8")
    for compiled_name in (
        "_local_compat_core",
        "_local_compat_monitor",
        "_client_launcher",
    ):
        assert compiled_name in build
    assert 'Filter "_local_compat_addon*.pyd"' in build
    for bootstrap_name in (
        "local_compat_monitor.py",
        "local_compat_addon.py",
    ):
        assert bootstrap_name in build
    assert "群发包残留产品本地兼容明文模块" in build
    assert "群发启动壳残留内部实现标识" in build
    assert "群发诊断壳残留内部实现标识" in build
    assert build.count("[System.Text.UTF8Encoding]::new($true)") >= 2
    assert "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in build
    assert "BIDKING_PS_PARSE_TARGET" in build
    assert "Parser]::ParseFile" in build
    assert "群发启动/诊断/网吧预检/密封修复载荷通过 Windows PowerShell 5.1 解析门" in build
    assert "live_process_discovery.ps1" in build
    assert "Remove-Item -LiteralPath $processDiscoveryDst -Force" in build
    assert '$advancedCleanupDir = Join-Path $diagnosticsDir "高级卸载与清理"' in build
    assert 'Join-Path $advancedCleanupDir "卸载本地兼容证书（高级清理）.bat"' in build
    remove_bat_writer = build[
        build.index("$removeWssBat = @'") : build.index(
            'Write-Host "  + 诊断工具/高级卸载与清理/卸载本地兼容证书（高级清理）.bat"'
        )
    ]
    assert "UTF8Encoding]::new($false)" in remove_bat_writer
    assert "UTF8Encoding($true)" not in remove_bat_writer
    assert 'TrimEnd("`r", "`n") + "`r`n"' in remove_bat_writer
    assert "移除WSS本地证书.bat" not in build
    assert "BIDKING_REPAIR_TRUST_CLEANUP" in remove_ca
    assert 'ArgumentList "--repair"' in remove_ca
    assert "Remove-Item -LiteralPath $StorePath" not in remove_ca
    assert "BidKing Live WSS 本地证书" not in remove_ca


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe BAT encoding contract")
def test_generated_remove_ca_bat_is_no_bom_and_cmd_parseable(
    tmp_path: Path,
) -> None:
    build = (SCRIPTS / "build_live_portable.ps1").read_text(encoding="utf-8-sig")
    matched = re.search(r"\$removeWssBat = @'\r?\n(.*?)\r?\n'@", build, re.DOTALL)
    assert matched is not None
    rendered = matched.group(1).replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n"
    bat = tmp_path / "remove-ca.bat"
    bat.write_bytes(rendered.encode("utf-8"))
    assert not bat.read_bytes().startswith(b"\xef\xbb\xbf")
    assert bat.read_bytes().startswith(b"@echo off\r\n")

    stub = tmp_path / "powershell.cmd"
    stub.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii", newline="")
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(bat)],
        input="\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert "锘" not in completed.stdout


def test_manual_documents_required_order_privacy_and_uninstall() -> None:
    manual = (SCRIPTS / "pkg_assets" / "使用说明.txt").read_text(encoding="utf-8-sig")

    assert "Steam 可以保持开启，但 BidKing.exe 必须完全退出" in manual
    assert "再从 Steam 启动游戏" in manual
    assert "刷新当前界面显示" in manual
    assert "不会重启后台读取或影响当前对局" in manual
    assert "游戏运行中关闭悬浮窗只会关闭界面" in manual
    assert "再次启动计算器可恢复界面" in manual
    assert "游戏退出后后台读取会自动结束" in manual
    assert "自动结束" in manual
    assert "本机对局数据兼容" in manual
    assert "不会记录或上传登录账号、激活 token" in manual
    assert "release.bid-king.cn" not in manual
    assert "0x22 / 0x26" not in manual
    assert "SetGamePreset" not in manual
    assert "GameUseItem" not in manual
    assert "高级卸载与清理\\卸载本地兼容证书（高级清理）.bat" in manual
    assert "移除WSS本地证书.bat" not in manual
