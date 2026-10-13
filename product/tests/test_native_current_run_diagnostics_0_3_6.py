"""Actual embedded BOOTSTRAP and PS formatter controls; no product launch."""
from __future__ import annotations

import json
import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
from test_startup_failure_cleanup_0_3_5 import _run_bootstrap  # noqa: E402
from test_startup_e2e_timing_p0_hotfix2 import _diagnostics_success, _timing_row  # noqa: E402

DIAGNOSTICS = ROOT / "scripts/collect_diagnostics.ps1"
RUN = "a" * 32


def _namespace_failure_rows():
    events = [
        ("root_entry", "begin"), ("preload", "begin"), ("preload", "ok"),
        ("kernel_import", "begin"), ("python_spawn", "begin"), ("kernel_import", "ok"),
        ("kernel_manifest", "begin"), ("kernel_manifest", "ok"),
        ("kernel_coverage", "begin"), ("kernel_coverage", "ok"),
        ("kernel_namespace", "begin"), ("kernel_namespace", "ok"),
        ("python_spawn", "ok"), ("root_exit", "failed"),
    ]
    return [_timing_row(event, result, "unknown", elapsed, RUN)
            for elapsed, (event, result) in enumerate(events)]


def _write_timing(directory: Path, rows=None, *, modified_offset=0):
    directory.mkdir(exist_ok=True)
    modified = time.time() + modified_offset
    for row in rows or _namespace_failure_rows():
        path = directory / f"{row['run_id']}--{row['event']}--{row['result']}.json"
        path.write_text(json.dumps({"schema": "bidking_startup_timing_v1",
                                    "schema_version": 1, **row}, separators=(",", ":")), encoding="utf-8")
        os.utime(path, (modified, modified))


def _projection():
    return {
        "schema": "bidking_startup_cleanup_v1", "run_id": RUN, "phase": "bootstrap",
        "acl_restore": "unknown", "handle_release": "released", "primary_error": True,
        "primary_exit_code": None, "powershell_attempted": False,
        "acl_restore_failed_count": None, "acl_restore_attempted_count": None,
        "primary_exception_preserved": True, "monitor": "not_created", "overlay": "not_created",
        "message_key": "startup_failed", "failure_projection_version": 1,
        "primary_failure_stage": "namespace_lease", "primary_exception_class": "RuntimeError",
        "primary_reason_code": "namespace_guard_elevation_required", "primary_errno": None,
        "primary_winerror": None,
    }


def _write_projection(directory: Path, payload=None, *, raw=None):
    path = directory / (RUN + "--cleanup-bootstrap.json")
    path.write_bytes(raw if raw is not None else json.dumps(payload or _projection(), separators=(",", ":")).encode("ascii"))


def _format(directory: Path, *, extra=""):
    shell = shutil.which("powershell.exe")
    if not shell:
        pytest.skip("Windows PowerShell formatter unavailable")
    source = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    start = source.index("function Add-BidKingStartupTimingSummary")
    end = source.index("function Add-CompatibilityRecoveryGuidance", start)
    quoted = "'" + str(directory).replace("'", "''") + "'"
    command = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$ErrorActionPreference='Stop';\n"
               + source[start:end]
               + "\n$script:ProbeLines=[Collections.Generic.List[string]]::new()\n"
               + "function Add-Line($value){$script:ProbeLines.Add([string]$value)}\n"
               + f"Add-BidKingStartupTimingSummary {quoted}\n" + extra
               + "@($script:ProbeLines)|ConvertTo-Json -Compress -Depth 5\n")
    # Keep the actual formatter intact beyond Windows' command-line length
    # limit. Stdin is a transport change; no AST substitute or policy change.
    completed = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", "-"],
                               input=command + "\n", capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=20, check=False)
    assert completed.returncode == 0, completed.stderr
    lines = json.loads(completed.stdout.strip().splitlines()[-1])
    return lines if isinstance(lines, list) else [lines]


def test_actual_bootstrap_preserves_current_namespace_reason(monkeypatch, tmp_path):
    primary = RuntimeError("namespace_guard_elevation_required")
    caught, events, record = _run_bootstrap(monkeypatch, tmp_path, begin_error=primary)
    assert caught is primary and events == ["verify", "begin", "release"]
    assert record["primary_failure_stage"] == "namespace_lease"
    assert record["primary_exception_class"] == "RuntimeError"
    assert record["primary_reason_code"] == "namespace_guard_elevation_required"


def test_formatter_projects_only_same_current_run_fixed_fields(tmp_path):
    directory = tmp_path / "timing"
    _write_timing(directory)
    _write_projection(directory)
    lines = _format(directory)
    projected = [line for line in lines if "[native_current_run]" in line]
    assert len(projected) == 1
    assert "stage=namespace_lease" in projected[0]
    assert "reason_code=namespace_guard_elevation_required" in projected[0]


def _boot(monkeypatch, tmp_path, *, primary=None, at="namespace_lease", close_error=False,
          release_error=False, exit_code=0, registered_class=None):
    from types import SimpleNamespace
    source = (ROOT / "native/bidking_trust_bootstrap/src/main.rs").read_text(encoding="utf-8")
    embedded = re.search(r'const BOOTSTRAP: &str = r#"(.*?)"#;', source, re.S).group(1)
    tree = ast.parse(embedded)
    tree.body = [node for node in tree.body if not isinstance(node, ast.FunctionDef) or node.name != "load_exact"]
    events = []

    def act(stage):
        events.append(stage)
        if primary is not None and at == stage:
            raise primary

    def verify(_root):
        act("integrity_verify")
        return True, "ok", 1, "fixture"

    def recheck(_root):
        act("integrity_recheck")
        return True, "ok"

    def close():
        events.append("close")
        if close_error:
            raise RuntimeError("PRIVATE_RESTORE_TEXT")

    def begin(*_a, **_k):
        act("namespace_lease")
        return SimpleNamespace(close=close)

    def launch(*_a):
        act("managed_launch")
        return exit_code

    def release():
        events.append("release")
        if release_error:
            raise RuntimeError("PRIVATE_RELEASE_TEXT")
        return True, "ok"

    modules = {
        "bidking_kernel": SimpleNamespace(verify_local_integrity=verify, recheck_local_integrity=recheck,
                                            release_local_integrity=release, startup_timing_event=lambda *_a: None),
        "_bidking_local_integrity_namespace_guard": SimpleNamespace(begin_namespace_lease=begin,
                                                                    NamespaceGuardError=registered_class),
        "_client_launcher": SimpleNamespace(main=launch),
    }

    def loader(name, _path):
        stage = {"bidking_kernel": "kernel_import", "_bidking_local_integrity_namespace_guard": "namespace_guard_import",
                 "_client_launcher": "launcher_import"}[name]
        act(stage)
        return modules[name]

    monkeypatch.setenv("BIDKING_STARTUP_TIMING_RUN_ID", RUN)
    monkeypatch.setenv("BIDKING_STARTUP_TIMING_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["fixture", "root", "kernel", "launcher", "launch"])
    caught = None
    try:
        exec(compile(tree, "actual-bootstrap", "exec"), {"load_exact": loader})
    except BaseException as error:
        caught = error
    record = json.loads((tmp_path / (RUN + "--cleanup-bootstrap.json")).read_text(encoding="utf-8"))
    return caught, events, record


@pytest.mark.parametrize("stage", ["kernel_import", "integrity_verify", "namespace_guard_import",
                                    "namespace_lease", "integrity_recheck", "launcher_import", "managed_launch"])
def test_actual_primary_stage_and_unknown_text_are_preserved_safely(monkeypatch, tmp_path, stage):
    primary = RuntimeError("PRIVATE_ORIGINAL_TEXT C:/private/file token=SECRET")
    caught, events, record = _boot(monkeypatch, tmp_path, primary=primary, at=stage)
    assert caught is primary
    assert record["primary_failure_stage"] == stage
    assert record["primary_exception_class"] == "RuntimeError"
    assert record["primary_reason_code"] == "unknown"
    assert "PRIVATE_" not in json.dumps(record) and "C:/private" not in json.dumps(record)
    assert record["primary_exception_preserved"] is True
    if stage == "kernel_import":
        assert "release" not in events and record["handle_release"] == "unknown"
    else:
        assert events[-1] == "release"


def test_builtin_integer_error_codes_and_independent_cleanup(monkeypatch, tmp_path):
    primary = PermissionError(13, "PRIVATE_ORIGINAL_TEXT", "C:/private/file")
    primary.winerror = 5
    caught, events, record = _boot(monkeypatch, tmp_path, primary=primary, at="managed_launch",
                                 close_error=True, release_error=True)
    assert caught is primary and events[-2:] == ["close", "release"]
    assert record["primary_errno"] == 13 and record["primary_winerror"] == 5
    assert record["primary_exception_class"] == "PermissionError"
    assert record["acl_restore"] == "failed" and record["handle_release"] == "failed"
    assert "PRIVATE_" not in json.dumps(record)


@pytest.mark.parametrize("value", [True, -1, 2147483648, "5"])
def test_integer_projection_never_coerces_or_exceeds_range(monkeypatch, tmp_path, value):
    primary = OSError("PRIVATE_ORIGINAL_TEXT")
    primary.errno = value
    primary.winerror = value
    caught, _, record = _boot(monkeypatch, tmp_path, primary=primary)
    assert caught is primary
    assert record["primary_errno"] is None and record["primary_winerror"] is None


def test_registered_guard_uses_fixed_class_name_without_raw_typename(monkeypatch, tmp_path):
    class PRIVATE_CLASS_NAME(RuntimeError):
        pass
    primary = PRIVATE_CLASS_NAME("namespace_recovery_root_missing")
    caught, _, record = _boot(monkeypatch, tmp_path, primary=primary, registered_class=PRIVATE_CLASS_NAME)
    assert caught is primary
    assert record["primary_exception_class"] == "NamespaceGuardError"
    assert record["primary_reason_code"] == "namespace_recovery_root_missing"
    assert "PRIVATE_CLASS_NAME" not in json.dumps(record)


def test_unknown_exception_class_does_not_inspect_magic_fields(monkeypatch, tmp_path):
    class PRIVATE_CLASS_NAME(RuntimeError):
        def __getattribute__(self, name):
            if name in {"args", "errno", "winerror"}:
                raise AssertionError("must not inspect unknown class")
            return super().__getattribute__(name)
    primary = PRIVATE_CLASS_NAME("namespace_guard_elevation_required")
    caught, _, record = _boot(monkeypatch, tmp_path, primary=primary)
    assert caught is primary
    assert record["primary_exception_class"] == record["primary_reason_code"] == "unknown"
    assert "PRIVATE_CLASS_NAME" not in json.dumps(record)


@pytest.mark.parametrize("kind", ["normal", "rapid", "managed_nonzero", "cleanup_only"])
def test_success_rapid_nonzero_and_cleanup_remain_distinct(monkeypatch, tmp_path, kind):
    primary = RuntimeError("client_lifecycle_busy") if kind == "rapid" else None
    caught, _, record = _boot(monkeypatch, tmp_path, primary=primary, at="managed_launch",
                             exit_code=1 if kind == "managed_nonzero" else 0,
                             close_error=kind == "cleanup_only")
    expected = 1 if kind == "managed_nonzero" else 211 if kind == "cleanup_only" else 0
    assert isinstance(caught, SystemExit) and caught.code == expected
    assert record["primary_exception_class"] == "none"
    assert record["primary_reason_code"] == ("managed_exit_nonzero" if kind == "managed_nonzero" else "none")
    assert record["primary_exception_preserved"] is False
    assert record["acl_restore"] == ("failed" if kind == "cleanup_only" else "restored")


@pytest.mark.parametrize("mutation", ["wrong_run", "duplicate", "invalid_utf8", "oversize", "extra_key",
                                      "private_class", "private_reason", "bool_errno", "out_of_range",
                                      "coerced_winerror", "wrong_stage", "attempt_inconsistent"])
def test_formatter_rejects_damaged_or_wrong_run_projection(tmp_path, mutation):
    directory = tmp_path / "timing"
    _write_timing(directory)
    payload = _projection()
    if mutation == "wrong_run":
        payload["run_id"] = "b" * 32
    elif mutation == "private_class":
        payload["primary_exception_class"] = "PRIVATECLASS"
    elif mutation == "private_reason":
        payload["primary_reason_code"] = "private_reason"
    elif mutation == "bool_errno":
        payload["primary_errno"] = True
    elif mutation == "out_of_range":
        payload["primary_winerror"] = 2147483648
    elif mutation == "coerced_winerror":
        payload["primary_winerror"] = "5"
    elif mutation == "extra_key":
        payload["private"] = "PRIVATE_RAW_TEXT"
    elif mutation == "wrong_stage":
        payload["primary_failure_stage"] = "launcher_import"
    elif mutation == "attempt_inconsistent":
        payload["powershell_attempted"] = True
    raw = json.dumps(payload, separators=(",", ":")).encode("ascii")
    if mutation == "duplicate":
        raw = raw.replace(b'"failure_projection_version":1', b'"failure_projection_version":1,"failure_projection_version":1')
    elif mutation == "invalid_utf8":
        raw = b"\xff" + raw
    elif mutation == "oversize":
        raw += b" " * 4096
    _write_projection(directory, raw=raw)
    lines = _format(directory)
    assert any("result=unavailable reason=invalid_projection" in line for line in lines)
    assert not any("stage=namespace_lease" in line for line in lines)
    assert not any("PRIVATE" in line or "private_reason" in line for line in lines)


def test_later_diagnostics_run_does_not_hide_launch_failure(tmp_path):
    directory = tmp_path / "timing"
    _write_timing(directory)
    _write_projection(directory)
    _write_timing(directory, _diagnostics_success("b" * 32), modified_offset=10)
    lines = _format(directory)
    assert any("[native_current_run] result=failed stage=namespace_lease" in line for line in lines)


def test_projection_supersedes_old_session_but_not_newer_or_wrong_run(tmp_path):
    directory = tmp_path / "timing"
    _write_timing(directory)
    _write_projection(directory)
    extra = '''
$now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
Add-Line ('old_superseded='+(Test-BidKingNativeSupersedesStartupSession ($now-86400)).ToString().ToLowerInvariant())
Add-Line ('new_superseded='+(Test-BidKingNativeSupersedesStartupSession ($now+10)).ToString().ToLowerInvariant())
'''
    lines = _format(directory, extra=extra)
    assert "old_superseded=true" in lines and "new_superseded=false" in lines
    payload = _projection()
    payload["run_id"] = "b" * 32
    _write_projection(directory, payload)
    lines = _format(directory, extra=extra)
    assert "old_superseded=false" in lines


@pytest.mark.parametrize("wrong_run", [False, True])
def test_actual_session_attribution_clears_only_newer_valid_native_failure(tmp_path, wrong_run):
    log_dir = tmp_path / "live"
    log_dir.mkdir()
    directory = log_dir / "startup-timing-v1"
    _write_timing(directory)
    payload = _projection()
    if wrong_run:
        payload["run_id"] = "b" * 32
    _write_projection(directory, payload)
    launch = "10000000-0000-4000-8000-000000000001"
    started = int(time.time()) - 86400
    (log_dir / "startup_session.json").write_text(json.dumps({"launch_id": launch, "started_at": started}), encoding="utf-8")
    (log_dir / "monitor_log_session.json").write_text(json.dumps({
        "launch_id": launch, "started_at": started, "monitor_pid": 123,
        "reused_monitor": False, "stdout_file": "monitor.stdout.log", "stderr_file": "monitor.stderr.log",
    }), encoding="utf-8")
    (log_dir / "monitor.stdout.log").write_text("SAFE_HISTORY", encoding="utf-8")
    source = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    validator = re.search(r"(?ms)^function Test-DiagnosticFileForLaunch\b.*?^\}", source).group(0)
    start = source.index('Add-BidKingStartupTimingSummary (Join-Path $LogPath "startup-timing-v1") -CaptureOnly')
    end = source.index("function Get-OverlayStderrClass", start)
    quoted = "'" + str(log_dir).replace("'", "''") + "'"
    extra = (validator + f"\n$LogPath={quoted}\n" + source[start:end] + '''
Add-Line ('superseded='+$CurrentNativeSupersedesLaunch.ToString().ToLowerInvariant())
Add-Line ('monitor_current='+(Test-CurrentMonitorLog (Join-Path $LogPath 'monitor.stdout.log')).ToString().ToLowerInvariant())
''')
    lines = _format(directory, extra=extra)
    assert f"superseded={str(not wrong_run).lower()}" in lines
    assert f"monitor_current={str(wrong_run).lower()}" in lines


def test_damaged_latest_launch_never_falls_back_to_older_projection(tmp_path):
    directory = tmp_path / "timing"
    _write_timing(directory)
    _write_projection(directory)
    newer = [{**row, "run_id": "b" * 32} for row in _namespace_failure_rows()]
    _write_timing(directory, newer, modified_offset=10)
    # Latest filename B with older run A in its body is not a valid current binding.
    (directory / ("b" * 32 + "--cleanup-bootstrap.json")).write_text(
        json.dumps(_projection(), separators=(",", ":")), encoding="utf-8")
    lines = _format(directory)
    assert any("result=unavailable reason=invalid_projection" in line for line in lines)
    assert not any("stage=namespace_lease" in line for line in lines)


def test_known_reason_allowlist_matches_formatter(monkeypatch, tmp_path):
    source = (ROOT / "native/bidking_trust_bootstrap/src/main.rs").read_text(encoding="utf-8")
    embedded = re.search(r'const BOOTSTRAP: &str = r#"(.*?)"#;', source, re.S).group(1)
    tree = ast.parse(embedded)
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id in {"INTEGRITY_FAILURE_EXITS", "SAFE_PRIMARY_REASONS"}
                           for target in node.targets)]
    namespace = {}
    exec(compile(ast.Module(body=assignments, type_ignores=[]), "actual-reason-allowlist", "exec"), namespace)
    formatter = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    reason_list = formatter[formatter.index("$Reasons = @("):formatter.index("$Children = @(")]
    for reason in namespace["SAFE_PRIMARY_REASONS"]:
        assert f'"{reason}"' in reason_list


def test_native_sidecar_reader_is_bounded_before_decoding():
    source = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    reader = source[source.index("$ReadNativeProjection = {"):source.index("  $Events = @(")]
    assert "[byte[]]::new(4097)" in reader
    assert "$Stream.Read($Buffer, $Read, $Buffer.Length - $Read)" in reader
    assert "finally { $Stream.Dispose() }" in reader
    assert reader.index("$Read -gt 4096") < reader.index(".GetString($Raw)")
    assert "ReadAllBytes" not in reader
