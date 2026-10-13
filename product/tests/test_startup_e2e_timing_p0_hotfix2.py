from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "native" / "bidking_trust_bootstrap" / "src" / "main.rs"
KERNEL = ROOT / "native" / "bidking_kernel" / "src" / "lib.rs"
TIMING = ROOT / "native" / "bidking_kernel" / "src" / "startup_timing.rs"
LIFECYCLE = ROOT / "native" / "bidking_kernel" / "src" / "client_lifecycle.rs"
NAMESPACE = ROOT / "scripts" / "local_integrity_namespace_guard.py"
STARTUP = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
OVERLAY = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
DIAGNOSTICS = ROOT / "scripts" / "collect_diagnostics.ps1"

EVENTS = {
    "root_entry",
    "preload",
    "python_spawn",
    "kernel_import",
    "kernel_manifest",
    "kernel_coverage",
    "kernel_namespace",
    "acl_inventory",
    "acl_journal",
    "acl_apply",
    "acl_attest",
    "integrity_recheck",
    "lifecycle",
    "powershell_spawn",
    "powershell_launch",
    "powershell_generation",
    "powershell_component",
    "powershell_certificate",
    "powershell_dependency",
    "powershell_process_identity",
    "powershell_monitor",
    "powershell_overlay",
    "tk_map",
    "tk_after_idle",
    "startup_guard",
    "startup_terminal",
    "acl_restore",
    "root_exit",
}
RESULTS = {"begin", "ok", "failed", "skipped", "unavailable"}
TOPOLOGIES = {
    "unknown",
    "cold_start",
    "current_current",
    "current_absent",
    "current_stale",
    "current_unsafe",
    "rapid_reentry",
    "diagnostics",
    "repair",
}
PAYLOAD_FIELDS = {
    "schema",
    "schema_version",
    "run_id",
    "event",
    "result",
    "topology",
    "elapsed_ms",
}


def _quoted_values(source: str, anchor: str) -> set[str]:
    start = source.index(anchor)
    block = source[start : source.index("];", start) + 2]
    return set(re.findall(r'"([a-z0-9_]+)"', block))


def _rust_struct_fields(source: str, anchor: str) -> set[str]:
    start = source.index(anchor)
    block = source[start : source.index("}\n", start)]
    return set(re.findall(r"^\s*([a-z][a-z0-9_]*):", block, flags=re.MULTILINE))


def test_fixed_schema_allowlists_match_across_native_powershell_and_diagnostics() -> None:
    native = TIMING.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    startup = STARTUP.read_text(encoding="utf-8-sig")
    diagnostics = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    assert _quoted_values(native, "pub(crate) const EVENTS") == EVENTS
    assert _quoted_values(native, "pub(crate) const RESULTS") == RESULTS
    assert _quoted_values(native, "pub(crate) const TOPOLOGIES") == TOPOLOGIES
    for value in EVENTS | RESULTS | TOPOLOGIES:
        assert f'"{value}"' in bootstrap
        assert f'"{value}"' in startup
        assert f'"{value}"' in diagnostics


def test_every_writer_payload_has_only_the_seven_fixed_privacy_safe_fields() -> None:
    native = TIMING.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    startup = STARTUP.read_text(encoding="utf-8-sig")
    overlay = OVERLAY.read_text(encoding="utf-8")
    assert _rust_struct_fields(native, "struct TimingEvent<'a>") == PAYLOAD_FIELDS
    assert _rust_struct_fields(bootstrap, "struct TimingEvent<'a>") == PAYLOAD_FIELDS

    ps_start = startup.index("$Payload = [ordered]@{")
    ps_block = startup[ps_start : startup.index("} | ConvertTo-Json -Compress", ps_start)]
    assert set(re.findall(r"^\s*([a-z][a-z0-9_]*)\s*=", ps_block, re.MULTILINE)) == PAYLOAD_FIELDS

    overlay_tree = ast.parse(overlay, filename=str(OVERLAY))
    payload_keys: set[str] | None = None
    for node in ast.walk(overlay_tree):
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "payload" for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Dict):
            payload_keys = {
                key.value for key in node.value.keys if isinstance(key, ast.Constant)
            }
            break
    assert payload_keys == PAYLOAD_FIELDS


def test_root_context_uses_monotonic_base_bounded_atomic_events_and_rotation() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert "GetTickCount64" in source
    assert "BIDKING_STARTUP_TIMING_RUN_ID" in source
    assert "BIDKING_STARTUP_TIMING_BASE_TICK_MS" in source
    assert "TIMING_RUNS_RETAINED: usize = 8" in source
    assert ".create_new(true)" in source
    assert "drop(stream);" in source
    assert "fs::rename(&temporary, &final_path)" in source
    for event in ("root_entry", "preload", "python_spawn", "kernel_import", "root_exit"):
        assert f'"{event}"' in source
    assert "def timing_event(event, result):" in source
    assert "Observability is never a safety or availability input" in source
    assert 'timing_topology = "rapid_reentry"' in source
    assert 'timing_event("startup_terminal", "skipped")' in source


def test_winps_writer_uses_same_monotonic_clock_low_word_with_wrap_handling() -> None:
    source = STARTUP.read_text(encoding="utf-8-sig")
    assert "[Environment]::TickCount64" not in source
    assert "[Environment]::TickCount" in source
    assert "$TickModulus = 4294967296" in source
    assert "$BaseLow = $BaseTick % $TickModulus" in source
    assert "$TickModulus - $BaseLow + $CurrentLow" in source


def test_integrity_acl_lifecycle_and_powershell_stages_are_observability_only() -> None:
    kernel = KERNEL.read_text(encoding="utf-8")
    lifecycle = LIFECYCLE.read_text(encoding="utf-8")
    namespace = NAMESPACE.read_text(encoding="utf-8")
    startup = STARTUP.read_text(encoding="utf-8-sig")
    for event in ("kernel_manifest", "kernel_coverage", "kernel_namespace", "integrity_recheck"):
        assert f'"{event}"' in kernel
    for event in ("lifecycle", "powershell_spawn"):
        assert f'"{event}"' in lifecycle
    for event in ("acl_inventory", "acl_journal", "acl_apply", "acl_attest", "acl_restore"):
        assert f'"{event}"' in namespace
    assert "except BaseException:" in namespace
    assert "Observability must never change a security decision" in namespace
    for event in (
        "powershell_generation",
        "powershell_component",
        "powershell_certificate",
        "powershell_dependency",
        "powershell_process_identity",
        "powershell_monitor",
        "powershell_overlay",
        "startup_guard",
        "startup_terminal",
    ):
        assert f'Write-BidKingStartupTiming "{event}"' in startup
    assert '$null = Write-BidKingStartupTiming' in startup


def test_close_writers_preserve_the_first_exact_nonunknown_topology() -> None:
    native = TIMING.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    startup = STARTUP.read_text(encoding="utf-8-sig")
    for source in (native, bootstrap):
        assert "resolve_persisted_topology" in source
        assert "serde_json::to_vec(&stored).ok()? != encoded" in source
        assert "json_files > 1280" in source
        assert 'stored.topology == "unknown"' in source
    assert (
        '(event == "lifecycle" && result != "begin") || event == "acl_restore"'
        in native
    )
    assert (
        '(event == "python_spawn" && result != "begin") || event == "root_exit"'
        in bootstrap
    )
    fast = startup[
        startup.index("startup_reuse_ready") : startup.index(
            "# --- 0.3.0 WSS cold-start preflight ---"
        )
    ]
    assert fast.index(
        'Write-BidKingStartupTiming "powershell_launch" "ok" "current_current"'
    ) < fast.index(
        'Write-BidKingStartupTiming "startup_terminal" "ok" "current_current"'
    )


def test_tk_ready_has_one_map_to_after_idle_chain_and_python_parses() -> None:
    source = OVERLAY.read_text(encoding="utf-8")
    ast.parse(source, filename=str(OVERLAY))
    assert 'root.bind("<Map>", _record_root_map, add="+")' in source
    assert '_write_startup_timing_event("tk_map", "ok")' in source
    assert '_write_startup_timing_event("tk_after_idle", "begin")' in source
    assert "root.after_idle(_record_tk_after_idle)" in source
    assert '_write_startup_timing_event("tk_after_idle", "ok")' in source
    assert 'timing_ready = {"map": False, "idle": False}' in source


def test_diagnostics_accepts_only_canonical_fixed_schema_and_never_copies_raw() -> None:
    source = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    assert "Add-BidKingStartupTimingSummary" in source
    assert "$Files.Count -gt 1280" in source
    assert "$Raw.Length -gt 1024" in source
    assert "UTF8Encoding]::new($false, $true)" in source
    assert '"schema":"bidking_startup_timing_v1"' in source
    assert '"elapsed_ms":(?<elapsed>0|[1-9][0-9]*)' in source
    assert "$ValidateRun = {" in source
    assert "$Prerequisites = @{" in source
    assert "$Adjacency = @{}" in source
    assert '$RootEntries.Count -ne 1' in source
    assert '$RootExits[0].Result -notin @("ok", "failed")' in source
    assert '$NonUnknownTopologies.Count -gt 1' in source
    assert 'Where-Object { $_.Valid -and $_.Complete }' in source
    assert "result=failed prefix={0} events={1}" in source
    assert "result=timing_unavailable reason=invalid" in source
    timing_section = source[
        source.index("function Add-BidKingStartupTimingSummary") :
        source.index("function Add-CompatibilityRecoveryGuidance")
    ]
    for forbidden in (
        "command_line",
        "certificate_raw",
        "package_generation_identity",
        "username",
        "exception.message",
        "token=",
        "pid=",
    ):
        assert forbidden not in timing_section.casefold()


def _timing_row(
    event: str,
    result: str,
    topology: str,
    elapsed: int,
    run_id: str = "a" * 32,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "event": event,
        "result": result,
        "topology": topology,
        "elapsed_ms": elapsed,
    }


def _append_pair(
    rows: list[dict[str, object]], event: str, topology: str, elapsed: int
) -> int:
    rows.append(_timing_row(event, "begin", topology, elapsed))
    rows.append(_timing_row(event, "ok", topology, elapsed + 1))
    return elapsed + 2


def _diagnostics_success(run_id: str = "a" * 32) -> list[dict[str, object]]:
    rows = [
        _timing_row("root_entry", "begin", "unknown", 0, run_id),
        _timing_row("preload", "begin", "unknown", 1, run_id),
        _timing_row("preload", "ok", "unknown", 2, run_id),
        _timing_row("kernel_import", "begin", "unknown", 3, run_id),
        _timing_row("python_spawn", "begin", "unknown", 4, run_id),
        _timing_row("kernel_import", "ok", "diagnostics", 5, run_id),
    ]
    elapsed = 6
    for event in (
        "kernel_manifest",
        "kernel_coverage",
        "kernel_namespace",
        "acl_inventory",
        "acl_journal",
        "acl_apply",
        "acl_attest",
        "integrity_recheck",
    ):
        rows.extend(
            (
                _timing_row(event, "begin", "diagnostics", elapsed, run_id),
                _timing_row(event, "ok", "diagnostics", elapsed + 1, run_id),
            )
        )
        elapsed += 2
    rows.extend(
        (
            _timing_row("lifecycle", "begin", "diagnostics", elapsed, run_id),
            _timing_row(
                "powershell_spawn", "begin", "diagnostics", elapsed + 1, run_id
            ),
            _timing_row("powershell_spawn", "ok", "diagnostics", elapsed + 2, run_id),
            _timing_row("lifecycle", "ok", "diagnostics", elapsed + 3, run_id),
            _timing_row("acl_restore", "begin", "diagnostics", elapsed + 4, run_id),
            _timing_row("acl_restore", "ok", "diagnostics", elapsed + 5, run_id),
            _timing_row("python_spawn", "ok", "diagnostics", elapsed + 6, run_id),
            _timing_row("root_exit", "ok", "diagnostics", elapsed + 7, run_id),
        )
    )
    return rows


def _current_success() -> list[dict[str, object]]:
    rows = [
        _timing_row("root_entry", "begin", "unknown", 0),
        _timing_row("preload", "begin", "unknown", 1),
        _timing_row("preload", "ok", "unknown", 2),
        _timing_row("kernel_import", "begin", "unknown", 3),
        _timing_row("python_spawn", "begin", "unknown", 4),
        _timing_row("kernel_import", "ok", "unknown", 5),
    ]
    elapsed = 6
    for event in (
        "kernel_manifest",
        "kernel_coverage",
        "kernel_namespace",
        "acl_inventory",
        "acl_journal",
        "acl_apply",
        "acl_attest",
        "integrity_recheck",
    ):
        elapsed = _append_pair(rows, event, "unknown", elapsed)
    rows.extend(
        (
            _timing_row("lifecycle", "begin", "unknown", elapsed),
            _timing_row("powershell_spawn", "begin", "unknown", elapsed + 1),
            _timing_row("powershell_spawn", "ok", "unknown", elapsed + 2),
            _timing_row("powershell_launch", "begin", "unknown", elapsed + 3),
            _timing_row("powershell_generation", "begin", "unknown", elapsed + 4),
            _timing_row("powershell_generation", "ok", "unknown", elapsed + 5),
            _timing_row("powershell_process_identity", "begin", "unknown", elapsed + 6),
            _timing_row(
                "powershell_process_identity", "ok", "current_current", elapsed + 7
            ),
            _timing_row("powershell_launch", "ok", "current_current", elapsed + 8),
            _timing_row("startup_terminal", "ok", "current_current", elapsed + 9),
            _timing_row("lifecycle", "ok", "current_current", elapsed + 10),
            _timing_row("acl_restore", "begin", "current_current", elapsed + 11),
            _timing_row("acl_restore", "ok", "current_current", elapsed + 12),
            _timing_row("python_spawn", "ok", "current_current", elapsed + 13),
            _timing_row("root_exit", "ok", "current_current", elapsed + 14),
        )
    )
    return rows


def _cold_success(*, ambiguous_ready_terminals: bool = False) -> list[dict[str, object]]:
    rows = []
    for row in _current_success():
        if int(row["elapsed_ms"]) > 29:
            continue
        copied = dict(row)
        if copied["topology"] != "unknown":
            copied["topology"] = "cold_start"
        rows.append(copied)
    rows.extend(
        (
            _timing_row("powershell_certificate", "begin", "cold_start", 30),
            _timing_row("powershell_component", "skipped", "cold_start", 31),
            _timing_row("powershell_certificate", "ok", "cold_start", 32),
            _timing_row("powershell_dependency", "begin", "cold_start", 33),
            _timing_row("powershell_dependency", "ok", "cold_start", 34),
            _timing_row("powershell_monitor", "begin", "cold_start", 35),
            _timing_row("powershell_monitor", "ok", "cold_start", 36),
            _timing_row("powershell_overlay", "begin", "cold_start", 37),
            _timing_row("tk_map", "begin", "cold_start", 38),
            _timing_row("tk_map", "ok", "cold_start", 39),
            _timing_row("tk_after_idle", "begin", "cold_start", 40),
            _timing_row("powershell_overlay", "ok", "cold_start", 41),
            _timing_row("startup_guard", "begin", "cold_start", 42),
            _timing_row("tk_after_idle", "ok", "cold_start", 43),
            _timing_row(
                "startup_guard",
                "ok",
                "cold_start",
                43 if ambiguous_ready_terminals else 44,
            ),
        )
    )
    elapsed = 44 if ambiguous_ready_terminals else 45
    rows.extend(
        (
            _timing_row("powershell_launch", "ok", "cold_start", elapsed),
            _timing_row("startup_terminal", "ok", "cold_start", elapsed + 1),
            _timing_row("lifecycle", "ok", "cold_start", elapsed + 2),
            _timing_row("acl_restore", "begin", "cold_start", elapsed + 3),
            _timing_row("acl_restore", "ok", "cold_start", elapsed + 4),
            _timing_row("python_spawn", "ok", "cold_start", elapsed + 5),
            _timing_row("root_exit", "ok", "cold_start", elapsed + 6),
        )
    )
    return rows


def _write_timing_case(
    root: Path,
    name: str,
    rows: list[dict[str, object]],
    *,
    modified_offset: float = 0.0,
) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    modified = time.time() + modified_offset
    for row in rows:
        payload = {
            "schema": "bidking_startup_timing_v1",
            "schema_version": 1,
            **row,
        }
        path = directory / (
            f"{row['run_id']}--{row['event']}--{row['result']}.json"
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.utime(path, (modified, modified))


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_strict_aggregation_rejects_invalid_runs_and_keeps_previous_valid(
    tmp_path: Path, shell_name: str
) -> None:
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} unavailable")
    ambiguous = _cold_success(ambiguous_ready_terminals=True)
    cases: dict[str, list[dict[str, object]]] = {
        "missing_root_entry": [_timing_row("root_exit", "ok", "cold_start", 1)],
        "incomplete_ok": [
            _timing_row("root_entry", "begin", "unknown", 0),
            _timing_row("root_exit", "ok", "unknown", 1),
        ],
        "unknown_event": [
            _timing_row("root_entry", "begin", "unknown", 0),
            _timing_row("unknown_event", "begin", "unknown", 1),
            _timing_row("root_exit", "failed", "unknown", 2),
        ],
        "failed_exit_prefix": [
            _timing_row("root_entry", "begin", "unknown", 0),
            _timing_row("preload", "begin", "unknown", 1),
            _timing_row("preload", "failed", "unknown", 2),
            _timing_row("root_exit", "failed", "unknown", 3),
        ],
        "duplicate_terminal": [
            _timing_row("root_entry", "begin", "unknown", 0),
            _timing_row("preload", "begin", "unknown", 1),
            _timing_row("preload", "ok", "unknown", 2),
            _timing_row("preload", "failed", "unknown", 3),
            _timing_row("root_exit", "failed", "unknown", 4),
        ],
        "terminal_before_begin": [
            _timing_row("root_entry", "begin", "unknown", 0),
            _timing_row("preload", "ok", "unknown", 5),
            _timing_row("preload", "begin", "unknown", 10),
            _timing_row("root_exit", "failed", "unknown", 20),
        ],
        "reverse_dag": [
            _timing_row("root_entry", "begin", "unknown", 0),
            _timing_row("preload", "begin", "unknown", 1),
            _timing_row("preload", "ok", "unknown", 2),
            _timing_row("kernel_import", "begin", "unknown", 5),
            _timing_row("python_spawn", "begin", "unknown", 4),
            _timing_row("root_exit", "failed", "unknown", 10),
        ],
        "mixed_topology": [
            _timing_row("root_entry", "begin", "unknown", 0),
            _timing_row("preload", "begin", "repair", 1),
            _timing_row("preload", "failed", "repair", 2),
            _timing_row("root_exit", "failed", "cold_start", 3),
        ],
        "trailing_unknown": [
            _timing_row("root_entry", "begin", "unknown", 0),
            _timing_row("preload", "begin", "cold_start", 1),
            _timing_row("preload", "failed", "cold_start", 2),
            _timing_row("root_exit", "failed", "unknown", 3),
        ],
        "ambiguous_same_elapsed": ambiguous,
        "diagnostics_success": _diagnostics_success(),
        "current_current_success": _current_success(),
    }
    for name, rows in cases.items():
        _write_timing_case(tmp_path, name, rows)
    fallback = "invalid_latest_previous_valid"
    _write_timing_case(tmp_path, fallback, _diagnostics_success(), modified_offset=-120)
    _write_timing_case(
        tmp_path,
        fallback,
        [_timing_row("root_exit", "ok", "cold_start", 1, "b" * 32)],
    )

    source = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    start = source.index("function Add-BidKingStartupTimingSummary")
    end = source.index("function Add-CompatibilityRecoveryGuidance", start)
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        source[start:end]
        + "\n$root=$args[0]\n$results=[ordered]@{}\n"
        + "foreach($case in @(Get-ChildItem -LiteralPath $root -Directory | "
        + "Sort-Object Name)){\n"
        + "  $script:ProbeLines=[Collections.Generic.List[string]]::new()\n"
        + "  function Add-Line($value){$script:ProbeLines.Add([string]$value)}\n"
        + "  Add-BidKingStartupTimingSummary $case.FullName\n"
        + "  $results[$case.Name]=@($script:ProbeLines)\n"
        + "}\n$results|ConvertTo-Json -Compress -Depth 5\n",
        encoding="utf-8-sig",
    )
    completed = subprocess.run(
        [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(probe),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout.strip().splitlines()[-1])

    def lines(name: str) -> list[str]:
        value = rendered[name]
        return value if isinstance(value, list) else [value]

    invalid = "  [startup_timing] result=timing_unavailable reason=invalid"
    for name in (
        "missing_root_entry",
        "incomplete_ok",
        "unknown_event",
        "duplicate_terminal",
        "terminal_before_begin",
        "reverse_dag",
        "mixed_topology",
        "trailing_unknown",
        "ambiguous_same_elapsed",
    ):
        assert lines(name) == [invalid]
    assert lines("failed_exit_prefix")[0].endswith(
        "result=failed prefix=preload events=4"
    )
    assert "result=ok events=30" in lines("diagnostics_success")[0]
    assert "result=ok events=37" in lines("current_current_success")[0]
    assert "result=ok events=30" in lines(fallback)[0]
    assert all("bbbbbbbb" not in line for line in lines(fallback))
