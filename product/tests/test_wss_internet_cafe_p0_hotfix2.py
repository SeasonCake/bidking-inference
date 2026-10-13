from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "wss_environment_preflight.ps1"
HELPER_BAT = ROOT / "scripts" / "wss_environment_preflight.bat"
STARTUP = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
REPAIR = ROOT / "scripts" / "startup_repair.ps1"
REMOVE_CA = ROOT / "scripts" / "remove_wss_capture_ca.ps1"
COMPONENT = ROOT / "scripts" / "runtime_component.py"
DIAGNOSTICS = ROOT / "scripts" / "collect_diagnostics.ps1"
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
CONTRACT = ROOT / "scripts" / "release_zip_contract.json"
MANUAL = ROOT / "scripts" / "pkg_assets" / "使用说明.txt"
SID = "S-1-5-21-1000-1000-1000-1001"
GENERATION = "a" * 64
RUNTIME_IDENTITY = "b" * 64
COMPONENT_IDENTITY = "c" * 64
MONITOR_EPOCH = "11111111-2222-4333-8444-555555555555"
FOREIGN_MONITOR_EPOCH = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
MONITOR_PID = 4321
MONITOR_STARTED_AT_MS = 1_800_000_000_123


def _sidecar_payload(
    *,
    launch_id: str = MONITOR_EPOCH,
    state: str = "failed_access_denied",
    preload_phase: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 4,
        "owner_kind": "bidking_wss_monitor",
        "owner_sid": SID,
        "package_generation_identity": GENERATION,
        "runtime_identity": RUNTIME_IDENTITY,
        "component_identity": COMPONENT_IDENTITY,
        "monitor_epoch": launch_id,
        "monitor_started_at_unix_ms": MONITOR_STARTED_AT_MS,
        "ts_unix_ms": MONITOR_STARTED_AT_MS + 1,
        "phase": "helper_start_failed",
        "monitor_pid": MONITOR_PID,
        "helper_pid": 0,
        "launch_id": launch_id,
        "redirector_state": "unknown",
        "windivert_state": "unknown",
        "windivert_preload_state": state,
    }
    if preload_phase is not None:
        payload["windivert_preload_phase"] = preload_phase
    return payload


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell_hosts() -> list[str]:
    hosts: list[str] = []
    for name in ("powershell.exe", "pwsh.exe"):
        resolved = shutil.which(name)
        if resolved and resolved not in hosts:
            hosts.append(resolved)
    return hosts


def _run_ps(source: str, *, host: str | None = None) -> subprocess.CompletedProcess[str]:
    executable = host or shutil.which("powershell.exe")
    if not executable:
        pytest.skip("Windows PowerShell is unavailable")
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )


def _powershell_function_text(source: str, name: str) -> str:
    match = re.search(rf"(?m)^function\s+{re.escape(name)}\b[^{{]*\{{", source)
    assert match is not None
    brace = source.find("{", match.start())
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "`":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise AssertionError(f"unterminated PowerShell function: {name}")


def _fake_adapter(
    *,
    current_sid: str = SID,
    interactive_sid: str = SID,
    profile_state: str = "persistent",
    package_state: str = "supported",
    component_state: str = "available",
    store_state: str = "available",
    enable_lua: str = "enabled",
    is_admin: bool = True,
    game_state: str = "absent",
    monitor_state: str = "absent",
) -> str:
    return f"""
$script:certificates = @()
$script:adds = 0
$script:removes = 0
$script:protected = 0
$adapter = [pscustomobject]@{{
  GetCurrentSid = {{ {_ps_quote(current_sid)} }}
  GetInteractiveSid = {{ {_ps_quote(interactive_sid)} }}
  GetIsAdmin = {{ ${str(is_admin).lower()} }}
  GetEnableLuaState = {{ {_ps_quote(enable_lua)} }}
  GetProfileState = {{ param($sid) {_ps_quote(profile_state)} }}
  GetPackageRootState = {{ param($path) {_ps_quote(package_state)} }}
  GetComponentState = {{ param($path) {_ps_quote(component_state)} }}
  ProbeStore = {{ {_ps_quote(store_state)} }}
  ListCertificates = {{
    param($thumbprint)
    @($script:certificates | Where-Object {{ $_.thumbprint -ceq $thumbprint }})
  }}
  AddCertificate = {{
    param($raw)
    $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
      [Convert]::FromBase64String($raw)
    )
    try {{
      $script:certificates += [pscustomobject]@{{
        thumbprint = $cert.Thumbprint.ToUpperInvariant()
        raw_b64 = $raw
      }}
      $script:adds += 1
    }} finally {{ $cert.Dispose() }}
  }}
  RemoveCertificate = {{
    param($thumbprint, $raw)
    $matches = @($script:certificates | Where-Object {{
      $_.thumbprint -ceq $thumbprint -and $_.raw_b64 -ceq $raw
    }})
    if ($matches.Count -ne 1) {{ throw 'identity changed' }}
    $script:certificates = @($script:certificates | Where-Object {{
      -not ($_.thumbprint -ceq $thumbprint -and $_.raw_b64 -ceq $raw)
    }})
    $script:removes += 1
  }}
  GetGameState = {{ param($name) {_ps_quote(game_state)} }}
  GetMonitorState = {{ param($processId, $startedAt) {_ps_quote(monitor_state)} }}
  ProtectLeaseRoot = {{
    param($path)
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    $script:protected += 1
  }}
}}
"""


def _test_certificate(path: Path, *, common_name: str = "mitmproxy") -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    raw = certificate.public_bytes(serialization.Encoding.DER)
    path.write_bytes(raw)
    return raw


def test_product_ca_identity_rejects_wrong_subject_before_store_mutation(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "foreign-ca.cer"
    _test_certificate(certificate, common_name="Foreign Test Root")
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter()}
$result = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(tmp_path / 'leases')} -Apply
[pscustomobject]@{{ succeeded=$result.Succeeded;classification=$result.Classification;adds=$script:adds;removes=$script:removes }} | ConvertTo-Json -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "succeeded": False,
        "classification": "foreign_cert_or_state",
        "adds": 0,
        "removes": 0,
    }


def test_normal_start_and_exit_do_not_auto_remove_exact_product_ca() -> None:
    startup = STARTUP.read_text(encoding="utf-8-sig")
    automatic_lifecycle = startup[
        startup.index("# --- 0.3.0 WSS cold-start preflight ---") :
    ]
    assert "Invoke-BidKingTemporaryTrustRecovery" not in automatic_lifecycle
    assert "Start-BidKingTemporaryTrustCleanupWatcher" not in automatic_lifecycle
    assert "\u4fdd\u7559\u5728\u5f53\u524d Windows \u7528\u6237\u7684\u4fe1\u4efb\u533a" in automatic_lifecycle
    assert "\u9ad8\u7ea7\u5378\u8f7d\u4e0e\u6e05\u7406" in automatic_lifecycle


@pytest.mark.parametrize(
    ("classification", "next_step"),
    (
        (
            "wss_same_path_mixed_generation",
            "extract_official_zip_to_new_final_directory",
        ),
        (
            "wss_stale_product_service_conflict",
            "send_diagnostics_privately",
        ),
        (
            "wss_stale_product_service_repaired_restart_required",
            "restart_windows_then_start_current_package",
        ),
    ),
)
def test_startup_repair_reuses_exact_current_launch_wss_decision(
    tmp_path: Path,
    classification: str,
    next_step: str,
) -> None:
    (tmp_path / "startup_session.json").write_text(
        json.dumps({"launch_id": MONITOR_EPOCH, "started_at": 1_800_000_000.0}),
        encoding="utf-8",
    )
    (tmp_path / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": MONITOR_EPOCH,
                "monitor_pid": MONITOR_PID,
                "monitor_started_at": MONITOR_STARTED_AT_MS / 1000.0,
            }
        ),
        encoding="utf-8",
    )
    source = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(tmp_path)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(Path(sys.executable))}
. {_ps_quote(REPAIR)}
function Get-BidKingWssHelperFailureClassification {{ return '{classification}' }}
$trust=[pscustomobject]@{{PackageGenerationIdentity='{GENERATION}';RuntimeIdentity='{RUNTIME_IDENTITY}';ComponentIdentity='{COMPONENT_IDENTITY}'}}
$result=Get-BidKingCurrentLaunchWssDecision -CurrentGeneration '{GENERATION}' -TrustIdentities $trust
$result | ConvertTo-Json -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload == {
        "Classification": classification,
        "Action": "none",
        "Relieved": False,
        "NextStep": next_step,
    }


def test_startup_repair_rejects_wrong_launch_sidecar_without_classification(
    tmp_path: Path,
) -> None:
    (tmp_path / "startup_session.json").write_text(
        json.dumps({"launch_id": MONITOR_EPOCH, "started_at": 1_800_000_000.0}),
        encoding="utf-8",
    )
    (tmp_path / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": FOREIGN_MONITOR_EPOCH,
                "monitor_pid": MONITOR_PID,
                "monitor_started_at": MONITOR_STARTED_AT_MS / 1000.0,
            }
        ),
        encoding="utf-8",
    )
    source = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(tmp_path)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(Path(sys.executable))}
. {_ps_quote(REPAIR)}
function Get-BidKingWssHelperFailureClassification {{ throw 'must not classify stale launch' }}
$trust=[pscustomobject]@{{PackageGenerationIdentity='{GENERATION}';RuntimeIdentity='{RUNTIME_IDENTITY}';ComponentIdentity='{COMPONENT_IDENTITY}'}}
$result=Get-BidKingCurrentLaunchWssDecision -CurrentGeneration '{GENERATION}' -TrustIdentities $trust
[Console]::Out.WriteLine(($null -eq $result).ToString().ToLowerInvariant())
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "true"


def test_fixed_classifications_and_zero_mutation_public_boundary_are_explicit() -> None:
    helper = HELPER.read_text(encoding="utf-8-sig")
    startup = STARTUP.read_text(encoding="utf-8-sig")
    expected = {
        "cert_store_write_denied",
        "policy_locked",
        "profile_ephemeral",
        "uac_disabled",
        "component_write_denied",
        "restore_disk_suspected",
        "interactive_user_mismatch",
        "component_execution_blocked",
        "driver_load_denied",
        "package_root_unsupported",
        "foreign_cert_or_state",
        "user_cancelled",
        "unknown",
    }
    for classification in expected:
        assert f'"{classification}"' in helper
        assert f'"{classification}"' in startup
    assert "internet_cafe_realtime_unsupported" in helper
    assert "Direct invocation is always dry-run" in helper
    assert 'ValidateSet("preflight", "watch")' in helper
    assert "Import-Certificate" not in startup


def test_wss_dot_source_preserves_valid_package_generation_contract() -> None:
    startup = STARTUP.read_text(encoding="utf-8-sig")
    dot_source = startup.index(". $InternetCafeHelper `")
    first_monitor_query = startup.index("Get-BidKingLiveProcesses", dot_source)
    branch = startup[dot_source:first_monitor_query]
    assert "-ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity" in branch
    assert "$ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$'" in branch
    assert "Cert:\\CurrentUser\\Root" not in startup
    assert startup.index("startup_reuse_ready") < startup.index(
        "Invoke-BidKingInternetCafePreflight"
    )
    assert startup.index("Invoke-BidKingInternetCafePreflight") < startup.index(
        "$MitmdumpPath = Resolve-WssMitmdumpPath"
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    (
        ({}, "ready"),
        ({"is_admin": False}, "ready"),
        ({"enable_lua": "disabled"}, "ready"),
        ({"interactive_sid": "S-1-5-21-9"}, "interactive_user_mismatch"),
        ({"profile_state": "temporary"}, "profile_ephemeral"),
        ({"profile_state": "mandatory"}, "profile_ephemeral"),
        ({"package_state": "unsupported"}, "package_root_unsupported"),
        ({"component_state": "write_denied"}, "component_write_denied"),
        ({"component_state": "execution_blocked"}, "component_execution_blocked"),
        ({"component_state": "restore_disk_suspected"}, "restore_disk_suspected"),
        ({"store_state": "write_denied"}, "cert_store_write_denied"),
        ({"store_state": "policy_locked"}, "policy_locked"),
        ({"store_state": "unknown"}, "unknown"),
    ),
)
def test_fake_preflight_classifies_without_store_or_filesystem_mutation(
    overrides: dict[str, object], expected: str
) -> None:
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter(**overrides)}
$result = Invoke-BidKingInternetCafePreflight `
  -Adapter $adapter -PackageRoot 'C:\\Cafe Package' -ComponentPath 'C:\\Cache'
[pscustomobject]@{{
  result = $result
  adds = $script:adds
  removes = $script:removes
  protected = $script:protected
}} | ConvertTo-Json -Depth 5 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["result"]["classification"] == expected
    assert payload["result"]["mutation_performed"] is False
    assert payload["adds"] == payload["removes"] == payload["protected"] == 0
    if expected == "ready":
        assert payload["result"]["result"] == "supported"
    else:
        assert payload["result"]["result"] == "internet_cafe_realtime_unsupported"


def test_fake_temporary_trust_is_exactly_inserted_read_back_and_removed(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "网吧 证书.cer"
    _test_certificate(certificate)
    lease_root = tmp_path / "lease root"
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter()}
$enabled = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(lease_root)} `
  -Apply
$adapter.GetMonitorState = {{ param($processId, $startedAt) 'alive' }}
$bound = Bind-BidKingTemporaryTrustMonitor `
  -Adapter $adapter -LeasePath $enabled.LeasePath `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -MonitorProcessId 4321 -MonitorStartedAtMs 1000 `
  -Apply
$adapter.GetMonitorState = {{ param($processId, $startedAt) 'absent' }}
$lease = Read-BidKingPrivateLease $enabled.LeasePath
$cleaned = Invoke-BidKingTemporaryTrustCleanup `
  -Adapter $adapter -LeasePath $enabled.LeasePath `
  -ExpectedPackageGenerationIdentity '{GENERATION}' `
  -ExpectedRuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ExpectedComponentIdentity '{COMPONENT_IDENTITY}' `
  -ExpectedMonitorEpoch '{MONITOR_EPOCH}' `
  -MonitorProcessId 4321 -MonitorStartedAtMs 1000 `
  -GameProcessName 'BidKing' `
  -Apply
[pscustomobject]@{{
  enabled = $enabled
  bound = $bound
  lease_state = $lease.Payload.state
  lease_inserted = $lease.Payload.inserted_by_this_run
  cleaned = $cleaned
  adds = $script:adds
  removes = $script:removes
  remaining = @($script:certificates).Count
  lease_exists = Test-Path -LiteralPath $enabled.LeasePath
}} | ConvertTo-Json -Depth 5 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["enabled"]["Succeeded"] is True
    assert payload["enabled"]["Inserted"] is True
    assert payload["bound"] is True
    assert payload["lease_state"] == "active"
    assert payload["lease_inserted"] is True
    assert payload["cleaned"] == {"State": "removed", "Removed": True}
    assert payload["adds"] == payload["removes"] == 1
    assert payload["remaining"] == 0
    assert payload["lease_exists"] is False


def test_all_trust_mutation_entrypoints_require_apply_and_reject_dry_run(
    tmp_path: Path,
) -> None:
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter()}
$writeRejected = $false
try {{
  Write-BidKingPrivateLease {_ps_quote(tmp_path / 'blocked.json')} ([ordered]@{{}}) $adapter -DryRun
}} catch {{ $writeRejected = $true }}
$enable = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(tmp_path / 'missing.cer')} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -DryRun
$bind = Bind-BidKingTemporaryTrustMonitor `
  -Adapter $adapter -LeasePath {_ps_quote(tmp_path / 'missing.json')} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -MonitorProcessId {MONITOR_PID} `
  -MonitorStartedAtMs {MONITOR_STARTED_AT_MS} `
  -DryRun
$cleanup = Invoke-BidKingTemporaryTrustCleanup `
  -Adapter $adapter -LeasePath {_ps_quote(tmp_path / 'missing.json')} `
  -DryRun
$recovery = Invoke-BidKingTemporaryTrustRecovery `
  -Adapter $adapter -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -DryRun
$watcher = Start-BidKingTemporaryTrustCleanupWatcher `
  -HelperPath {_ps_quote(HELPER)} -LeasePath {_ps_quote(tmp_path / 'missing.json')} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -MonitorProcessId {MONITOR_PID} `
  -MonitorStartedAtMs {MONITOR_STARTED_AT_MS} `
  -DryRun
[pscustomobject]@{{
  write_rejected=$writeRejected
  enable=$enable
  bind=$bind
  cleanup=$cleanup
  recovery=$recovery
  watcher_is_null=($null -eq $watcher)
  adds=$script:adds
  removes=$script:removes
  protected=$script:protected
}} | ConvertTo-Json -Depth 5 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["write_rejected"] is True
    assert payload["enable"]["Classification"] == "dry_run"
    assert payload["bind"] is False
    assert payload["cleanup"] == {"State": "dry_run", "Removed": False}
    assert payload["recovery"] == {"State": "dry_run", "Removed": 0, "Preserved": 0}
    assert payload["watcher_is_null"] is True
    assert payload["adds"] == payload["removes"] == payload["protected"] == 0


def test_sealed_repair_cleanup_dry_run_returns_before_recovery() -> None:
    repair = REPAIR.read_text(encoding="utf-8-sig")
    function = _powershell_function_text(repair, "Invoke-BidKingRequestedTrustCleanup")
    source = f"""
$script:snapshotCalls = 0
$script:recoveryCalls = 0
function Get-BidKingRepairSnapshot {{ $script:snapshotCalls += 1; throw 'must not run' }}
function Invoke-BidKingTemporaryTrustRecovery {{ $script:recoveryCalls += 1; throw 'must not run' }}
{function}
$result = Invoke-BidKingRequestedTrustCleanup -CurrentGeneration '{GENERATION}' -DryRun
[pscustomobject]@{{ result=$result; snapshot_calls=$script:snapshotCalls; recovery_calls=$script:recoveryCalls }} |
  ConvertTo-Json -Depth 4 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["result"] == {"State": "dry_run", "Removed": 0, "Preserved": 0}
    assert payload["snapshot_calls"] == payload["recovery_calls"] == 0
    startup = _powershell_function_text(repair, "Invoke-BidKingStartupRepair")
    branch = startup[startup.index("if ($TrustCleanupRequested)") :]
    assert branch.index("if ($DryRun)") < branch.index("Invoke-BidKingRequestedTrustCleanup")


def test_preexisting_exact_certificate_is_reused_without_lease_or_delete(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "ca.cer"
    raw = _test_certificate(certificate)
    raw_b64 = base64.b64encode(raw).decode("ascii")
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter()}
$cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
  [Convert]::FromBase64String('{raw_b64}')
)
try {{
  $script:certificates = @([pscustomobject]@{{
    thumbprint = $cert.Thumbprint.ToUpperInvariant()
    raw_b64 = '{raw_b64}'
  }})
}} finally {{ $cert.Dispose() }}
$enabled = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -Apply
[pscustomobject]@{{ enabled=$enabled; adds=$script:adds; removes=$script:removes }} |
  ConvertTo-Json -Depth 4 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["enabled"]["Succeeded"] is True
    assert payload["enabled"]["Inserted"] is False
    assert payload["enabled"]["LeasePath"] == ""
    assert payload["adds"] == payload["removes"] == 0


def test_pem_and_der_snapshots_share_store_canonical_identity_and_reuse(
    tmp_path: Path,
) -> None:
    der_path = tmp_path / "ca.der"
    der = _test_certificate(der_path)
    pem_path = tmp_path / "ca.pem"
    pem_path.write_bytes(
        x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM)
    )
    der_b64 = base64.b64encode(der).decode("ascii")
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter()}
$der = Get-BidKingCertificateSnapshot {_ps_quote(der_path)}
$pem = Get-BidKingCertificateSnapshot {_ps_quote(pem_path)}
$script:certificates = @([pscustomobject]@{{thumbprint=$der.thumbprint;raw_b64='{der_b64}'}})
$enabled = Enable-BidKingTemporaryCurrentUserTrust -Adapter $adapter -CertificatePath {_ps_quote(pem_path)} -PackageGenerationIdentity '{GENERATION}' -RuntimeIdentity '{RUNTIME_IDENTITY}' -ComponentIdentity '{COMPONENT_IDENTITY}' -MonitorEpoch '{MONITOR_EPOCH}' -LeaseRoot {_ps_quote(tmp_path / 'leases')} -Apply
[pscustomobject]@{{ same_thumb=($der.thumbprint -ceq $pem.thumbprint);same_raw=($der.raw_b64 -ceq $pem.raw_b64);same_sha=($der.raw_sha256 -ceq $pem.raw_sha256);ready=$enabled.Succeeded;adds=$script:adds;removes=$script:removes }}|ConvertTo-Json -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "same_thumb": True, "same_raw": True, "same_sha": True,
        "ready": True, "adds": 0, "removes": 0,
    }


def test_foreign_same_thumbprint_state_is_preserved_without_add_or_delete(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "ca.cer"
    raw = _test_certificate(certificate)
    raw_b64 = base64.b64encode(raw).decode("ascii")
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter()}
$cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
  [Convert]::FromBase64String('{raw_b64}')
)
try {{
  $script:certificates = @([pscustomobject]@{{
    thumbprint = $cert.Thumbprint.ToUpperInvariant()
    raw_b64 = [Convert]::ToBase64String([byte[]](1,2,3,4))
  }})
}} finally {{ $cert.Dispose() }}
$enabled = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -Apply
[pscustomobject]@{{ enabled=$enabled; adds=$script:adds; removes=$script:removes }} |
  ConvertTo-Json -Depth 4 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["enabled"]["Succeeded"] is False
    assert payload["enabled"]["Classification"] == "foreign_cert_or_state"
    assert payload["adds"] == payload["removes"] == 0


@pytest.mark.parametrize(
    ("game_state", "monitor_state", "expected"),
    (
        ("alive", "absent", "game_active"),
        ("unknown", "absent", "query_denied"),
        ("absent", "alive", "monitor_active"),
        ("absent", "foreign", "identity_changed"),
    ),
)
def test_cleanup_preserves_exact_cert_when_runtime_ownership_is_not_safe(
    tmp_path: Path, game_state: str, monitor_state: str, expected: str
) -> None:
    certificate = tmp_path / "ca.cer"
    _test_certificate(certificate)
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter(game_state=game_state, monitor_state=monitor_state)}
$enabled = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -Apply
$lease = Read-BidKingPrivateLease $enabled.LeasePath
$bound = [ordered]@{{}}
foreach ($property in $lease.Payload.PSObject.Properties) {{ $bound[$property.Name] = $property.Value }}
$bound.monitor_process_id = 4321
$bound.monitor_started_at_unix_ms = 1000
$null = Write-BidKingPrivateLease $enabled.LeasePath $bound $adapter -Apply
$cleaned = Invoke-BidKingTemporaryTrustCleanup `
  -Adapter $adapter -LeasePath $enabled.LeasePath `
  -ExpectedPackageGenerationIdentity '{GENERATION}' `
  -ExpectedRuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ExpectedComponentIdentity '{COMPONENT_IDENTITY}' `
  -ExpectedMonitorEpoch '{MONITOR_EPOCH}' `
  -MonitorProcessId 4321 -MonitorStartedAtMs 1000 -GameProcessName 'BidKing' `
  -Apply
[pscustomobject]@{{ cleaned=$cleaned; removes=$script:removes; remaining=@($script:certificates).Count; lease_exists=(Test-Path $enabled.LeasePath) }} |
  ConvertTo-Json -Depth 4 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["cleaned"]["State"] == expected
    assert payload["cleaned"]["Removed"] is False
    assert payload["removes"] == 0
    assert payload["remaining"] == 1
    assert payload["lease_exists"] is True


def test_exact_product_ca_and_journal_are_reused_after_cancelled_or_abnormal_exit(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "mitmproxy-ca.cer"
    _test_certificate(certificate)
    lease_root = tmp_path / "leases"
    second_epoch = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter(is_admin=False)}
$first = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(lease_root)} -Apply
# Simulate either cancelling an older native delete prompt or an abnormal exit:
# the exact product CA and its private journal remain for the next launch.
$second = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{second_epoch}' `
  -LeaseRoot {_ps_quote(lease_root)} -Apply
[pscustomobject]@{{
  first_succeeded=$first.Succeeded
  first_inserted=$first.Inserted
  second_succeeded=$second.Succeeded
  second_inserted=$second.Inserted
  adds=$script:adds
  removes=$script:removes
  lease_count=@(Get-ChildItem -LiteralPath {_ps_quote(lease_root)} -Filter 'lease-*.json').Count
}} | ConvertTo-Json -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "first_succeeded": True,
        "first_inserted": True,
        "second_succeeded": True,
        "second_inserted": False,
        "adds": 1,
        "removes": 0,
        "lease_count": 1,
    }


def test_sid_or_cert_identity_change_is_preserved_without_mutation(tmp_path: Path) -> None:
    certificate = tmp_path / "ca.cer"
    _test_certificate(certificate)
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter()}
$enabled = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -Apply
$adapter.GetInteractiveSid = {{ 'S-1-5-21-9999' }}
$sidChanged = Invoke-BidKingTemporaryTrustCleanup `
  -Adapter $adapter -LeasePath $enabled.LeasePath `
  -ExpectedPackageGenerationIdentity '{GENERATION}' `
  -Apply
$adapter.GetInteractiveSid = {{ '{SID}' }}
$script:certificates[0].raw_b64 = [Convert]::ToBase64String([byte[]](1,2,3,4))
$certChanged = Invoke-BidKingTemporaryTrustCleanup `
  -Adapter $adapter -LeasePath $enabled.LeasePath `
  -ExpectedPackageGenerationIdentity '{GENERATION}' `
  -Apply
[pscustomobject]@{{ sid=$sidChanged; cert=$certChanged; removes=$script:removes }} |
  ConvertTo-Json -Depth 4 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["sid"]["State"] == "identity_changed"
    assert payload["cert"]["State"] == "foreign_or_unknown"
    assert payload["removes"] == 0


def test_pending_crash_journal_is_recoverable_only_with_exact_identities(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "ca.cer"
    _test_certificate(certificate)
    source = f"""
. {_ps_quote(HELPER)}
{_fake_adapter()}
$enabled = Enable-BidKingTemporaryCurrentUserTrust `
  -Adapter $adapter -CertificatePath {_ps_quote(certificate)} `
  -PackageGenerationIdentity '{GENERATION}' `
  -RuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ComponentIdentity '{COMPONENT_IDENTITY}' `
  -MonitorEpoch '{MONITOR_EPOCH}' `
  -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -Apply
$lease = Read-BidKingPrivateLease $enabled.LeasePath
$payload = [ordered]@{{}}
foreach ($property in $lease.Payload.PSObject.Properties) {{ $payload[$property.Name] = $property.Value }}
$payload.state = 'pending'
$null = Write-BidKingPrivateLease $enabled.LeasePath $payload $adapter -Apply
$wrong = Invoke-BidKingTemporaryTrustRecovery `
  -Adapter $adapter -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -ExpectedPackageGenerationIdentity '{'d' * 64}' `
  -ExpectedRuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ExpectedComponentIdentity '{COMPONENT_IDENTITY}' `
  -Apply
$right = Invoke-BidKingTemporaryTrustRecovery `
  -Adapter $adapter -LeaseRoot {_ps_quote(tmp_path / 'leases')} `
  -ExpectedPackageGenerationIdentity '{GENERATION}' `
  -ExpectedRuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ExpectedComponentIdentity '{COMPONENT_IDENTITY}' `
  -Apply
[pscustomobject]@{{ wrong=$wrong; right=$right; removes=$script:removes }} |
  ConvertTo-Json -Depth 4 -Compress
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["wrong"]["State"] == "preserved"
    assert payload["right"]["State"] == "cleaned"
    assert payload["removes"] == 1


@pytest.mark.parametrize(
    ("raw_json", "expected"),
    (
        ('{"a":1}', True),
        ('{"schema_version":2,"sc\\u0068ema_version":2}', False),
        ('{"schema_version":2,"SCHEMA_VERSION":2}', False),
        ('{"é":1,"e\\u0301":2}', False),
        ('{"a":"\\q"}', False),
        ('{"a":NaN}', False),
        ('{"a":Infinity}', False),
        ('{"a":-Infinity}', False),
        ('{"a":1e999}', False),
        ('prefix{"a":1}', False),
        ('{"a":1}trailing', False),
    ),
)
def test_strict_json_gate_rejects_aliases_invalid_numbers_and_extra_data(
    raw_json: str, expected: bool
) -> None:
    encoded = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")
    source = f"""
. {_ps_quote(HELPER)}
$json = [Text.UTF8Encoding]::new($false, $true).GetString([Convert]::FromBase64String('{encoded}'))
[Console]::Out.WriteLine((Test-BidKingFlatJsonKeysUnique $json).ToString().ToLowerInvariant())
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == str(expected).lower()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", True),
        ("schema_version", 2.0),
        ("schema_version", "2"),
        ("schema_version", None),
        ("monitor_process_id", True),
        ("monitor_process_id", 4321.5),
        ("monitor_process_id", "4321"),
        ("monitor_process_id", None),
        ("monitor_started_at_unix_ms", True),
        ("monitor_started_at_unix_ms", 1000.5),
        ("monitor_started_at_unix_ms", "1000"),
        ("monitor_started_at_unix_ms", None),
        ("created_at_unix_ms", True),
        ("created_at_unix_ms", 1000.5),
        ("created_at_unix_ms", "1000"),
        ("created_at_unix_ms", None),
    ),
)
def test_private_lease_rejects_non_exact_integer_types(
    tmp_path: Path, field: str, value: object
) -> None:
    certificate = tmp_path / "ca.cer"
    raw = _test_certificate(certificate)
    cert = x509.load_der_x509_certificate(raw)
    payload: dict[str, object] = {
        "schema_version": 2,
        "state": "active",
        "current_sid": SID,
        "interactive_sid": SID,
        "thumbprint": cert.fingerprint(hashes.SHA1()).hex().upper(),  # noqa: S303
        "certificate_raw_b64": base64.b64encode(raw).decode("ascii"),
        "certificate_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "package_generation_identity": GENERATION,
        "runtime_identity": RUNTIME_IDENTITY,
        "component_identity": COMPONENT_IDENTITY,
        "lease_epoch": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "monitor_epoch": MONITOR_EPOCH,
        "monitor_process_id": MONITOR_PID,
        "monitor_started_at_unix_ms": MONITOR_STARTED_AT_MS,
        "inserted_by_this_run": True,
        "created_at_unix_ms": MONITOR_STARTED_AT_MS - 1000,
    }
    payload[field] = value
    lease = tmp_path / "lease.json"
    lease.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    source = f"""
. {_ps_quote(HELPER)}
$value = Read-BidKingPrivateLease {_ps_quote(lease)}
[Console]::Out.WriteLine(($null -eq $value).ToString().ToLowerInvariant())
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "true"


@pytest.mark.parametrize(
    "payload_bytes",
    (b"\xff\xfe{}", b"{" + b" " * 65536 + b"}"),
    ids=("invalid-utf8", "oversize-65k"),
)
def test_private_lease_rejects_invalid_utf8_and_oversize(
    tmp_path: Path, payload_bytes: bytes
) -> None:
    lease = tmp_path / "lease.json"
    lease.write_bytes(payload_bytes)
    source = f"""
. {_ps_quote(HELPER)}
[Console]::Out.WriteLine(($null -eq (Read-BidKingPrivateLease {_ps_quote(lease)})).ToString().ToLowerInvariant())
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "true"


@pytest.mark.parametrize(
    ("launch_id", "state", "preload_phase", "expected"),
    (
        (MONITOR_EPOCH, "failed_access_denied", None, "driver_load_denied"),
        (MONITOR_EPOCH, "failed_untrusted_driver", None, "component_execution_blocked"),
        (
            MONITOR_EPOCH,
            "failed_same_path_mixed_generation",
            "verify_product",
            "wss_same_path_mixed_generation",
        ),
        (
            MONITOR_EPOCH,
            "failed_service_conflict",
            "verify_product",
            "wss_stale_product_service_conflict",
        ),
        (
            MONITOR_EPOCH,
            "repaired_restart_required",
            "complete",
            "wss_stale_product_service_repaired_restart_required",
        ),
        (FOREIGN_MONITOR_EPOCH, "failed_access_denied", None, "unknown"),
        (MONITOR_EPOCH, "preloaded", None, "unknown"),
    ),
)
def test_fake_driver_sidecar_maps_only_exact_current_fixed_states(
    tmp_path: Path,
    launch_id: str,
    state: str,
    preload_phase: str | None,
    expected: str,
) -> None:
    sidecar = tmp_path / "wss_helper_status.json"
    sidecar.write_text(
        json.dumps(
            _sidecar_payload(
                launch_id=launch_id,
                state=state,
                preload_phase=preload_phase,
            )
        ),
        encoding="utf-8",
    )
    source = f"""
. {_ps_quote(HELPER)}
Get-BidKingWssHelperFailureClassification `
  -StatusPath {_ps_quote(sidecar)} `
  -ExpectedLaunchId '{MONITOR_EPOCH}' `
  -ExpectedPackageGenerationIdentity '{GENERATION}' `
  -ExpectedRuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ExpectedComponentIdentity '{COMPONENT_IDENTITY}' `
  -ExpectedOwnerSid '{SID}' `
  -ExpectedMonitorProcessId {MONITOR_PID} `
  -ExpectedMonitorStartedAtMs {MONITOR_STARTED_AT_MS}
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == expected


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", True),
        ("schema_version", 4.0),
        ("schema_version", "4"),
        ("schema_version", None),
        ("monitor_pid", True),
        ("monitor_pid", 4321.5),
        ("monitor_pid", "4321"),
        ("monitor_started_at_unix_ms", True),
        ("monitor_started_at_unix_ms", 1.5),
        ("monitor_started_at_unix_ms", "1800000000123"),
        ("ts_unix_ms", 1.5),
        ("package_generation_identity", "d" * 64),
        ("runtime_identity", "d" * 64),
        ("component_identity", "d" * 64),
        ("owner_sid", "S-1-5-21-9999"),
        ("monitor_epoch", FOREIGN_MONITOR_EPOCH),
    ),
)
def test_driver_sidecar_rejects_type_identity_and_aba_tamper(
    tmp_path: Path, field: str, value: object
) -> None:
    payload = _sidecar_payload()
    payload[field] = value
    sidecar = tmp_path / "wss_helper_status.json"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    source = f"""
. {_ps_quote(HELPER)}
Get-BidKingWssHelperFailureClassification `
  -StatusPath {_ps_quote(sidecar)} `
  -ExpectedLaunchId '{MONITOR_EPOCH}' `
  -ExpectedPackageGenerationIdentity '{GENERATION}' `
  -ExpectedRuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ExpectedComponentIdentity '{COMPONENT_IDENTITY}' `
  -ExpectedOwnerSid '{SID}' `
  -ExpectedMonitorProcessId {MONITOR_PID} `
  -ExpectedMonitorStartedAtMs {MONITOR_STARTED_AT_MS}
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "unknown"


@pytest.mark.parametrize(
    "mutation",
    ("escaped_duplicate", "case_duplicate", "invalid_utf8", "oversize"),
)
def test_driver_sidecar_strict_decode_rejects_duplicate_and_invalid_bytes(
    tmp_path: Path, mutation: str
) -> None:
    raw = json.dumps(_sidecar_payload(), separators=(",", ":")).encode("utf-8")
    if mutation == "escaped_duplicate":
        raw = raw.replace(b'"schema_version":4', b'"schema_version":4,"sc\\u0068ema_version":4')
    elif mutation == "case_duplicate":
        raw = raw.replace(b'"schema_version":4', b'"schema_version":4,"SCHEMA_VERSION":4')
    elif mutation == "invalid_utf8":
        raw = b"\xff" + raw
    else:
        raw += b" " * 16384
    sidecar = tmp_path / "wss_helper_status.json"
    sidecar.write_bytes(raw)
    source = f"""
. {_ps_quote(HELPER)}
Get-BidKingWssHelperFailureClassification `
  -StatusPath {_ps_quote(sidecar)} `
  -ExpectedLaunchId '{MONITOR_EPOCH}' `
  -ExpectedPackageGenerationIdentity '{GENERATION}' `
  -ExpectedRuntimeIdentity '{RUNTIME_IDENTITY}' `
  -ExpectedComponentIdentity '{COMPONENT_IDENTITY}' `
  -ExpectedOwnerSid '{SID}' `
  -ExpectedMonitorProcessId {MONITOR_PID} `
  -ExpectedMonitorStartedAtMs {MONITOR_STARTED_AT_MS}
"""
    completed = _run_ps(source)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "unknown"


def test_packaging_repair_diagnostics_and_privacy_contract_are_wired() -> None:
    build = BUILD.read_text(encoding="utf-8-sig")
    repair = REPAIR.read_text(encoding="utf-8-sig")
    remover = REMOVE_CA.read_text(encoding="utf-8-sig")
    diagnostics = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    manual = MANUAL.read_text(encoding="utf-8-sig")
    assert contract["contract_version"] == 25
    for member in (
        "scripts/wss_environment_preflight.ps1",
        "诊断工具/网吧环境兼容检查.bat",
        "诊断工具/高级卸载与清理/卸载本地兼容证书（高级清理）.bat",
        "诊断工具/高级卸载与清理/remove_wss_capture_ca.ps1",
        "诊断工具/高级卸载与清理/说明.txt",
    ):
        assert member in contract["required_members"]["common"]
    common = set(contract["required_members"]["common"])
    assert "诊断工具/移除本地兼容证书.bat" not in common
    assert "诊断工具/remove_wss_capture_ca.ps1" not in common
    assert "wss_environment_preflight.ps1" in build
    assert 'Join-Path $diagnosticsDir "网吧环境兼容检查.bat"' in build
    assert '$internetCafeCheckText -replace "`r?`n", "`r`n"' in build
    assert 'Join-Path $scriptsDst "wss_environment_preflight.ps1"' in build
    assert "$repairScriptPath" in build
    assert "证书移除入口无法通过 Windows PowerShell 5.1 AST 解析门" in build
    assert "BIDKING_REPAIR_TRUST_CLEANUP" in repair
    assert "Invoke-BidKingTemporaryTrustRecovery" in repair
    assert "BIDKING_REPAIR_TRUST_CLEANUP" in remover
    assert 'ArgumentList "--repair"' in remover
    assert "Cert:\\CurrentUser\\Root" not in remover
    assert "certificate_raw_b64" not in diagnostics
    assert "BidKingInternetCafe-*.json" in diagnostics
    assert "privacy = \"fixed-enums-only\"" in HELPER.read_text(encoding="utf-8-sig")
    assert "网吧环境兼容检查.bat" in manual
    assert "internet_cafe_realtime_unsupported" in manual


def test_startup_repair_overlay_and_full_diagnostic_share_service_decisions() -> None:
    repair = REPAIR.read_text(encoding="utf-8-sig")
    diagnostics = DIAGNOSTICS.read_text(encoding="utf-8-sig")
    overlay = (ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py").read_text(
        encoding="utf-8-sig"
    )
    decisions = {
        "wss_same_path_mixed_generation": (
            "extract_official_zip_to_new_final_directory",
            "完全退出后迁移新目录",
        ),
        "wss_stale_product_service_conflict": (
            "send_diagnostics_privately",
            "完全退出后修复并重启",
        ),
        "wss_stale_product_service_repaired_restart_required": (
            "restart_windows_then_start_current_package",
            "重启后先开 Hero Ref",
        ),
    }
    for classification, (next_step, next_hint) in decisions.items():
        assert classification in repair
        assert classification in diagnostics
        assert classification in overlay
        assert next_step in repair
        assert next_step in diagnostics
        assert next_hint in overlay
    assert (
        '-Action $wssDecision.Action `\n        -Relieved:$false'
        in repair.replace("\r\n", "\n")
    )
    assert '-Relieved:($snapshot.Classification -eq "no_obstacle")' not in repair


def test_component_failure_marker_is_fixed_and_does_not_include_exception_text() -> None:
    source = COMPONENT.read_text(encoding="utf-8")
    assert 'FAILURE_SENTINEL = "BIDKING_COMPONENT_FAILURE="' in source
    assert "_fixed_failure_class(exc)" in source
    marker_line = next(
        line for line in source.splitlines() if "FAILURE_SENTINEL}{_fixed_failure_class" in line
    )
    assert "exc}" not in marker_line


@pytest.mark.parametrize("host", _powershell_hosts())
def test_new_and_changed_powershell_parse_on_available_hosts(host: str) -> None:
    paths = (HELPER, STARTUP, REPAIR, REMOVE_CA, DIAGNOSTICS, BUILD)
    source = "\n".join(
        f"$tokens=$null;$errors=$null;[Management.Automation.Language.Parser]::ParseFile({_ps_quote(path)},[ref]$tokens,[ref]$errors)|Out-Null;"
        f"if($errors.Count -gt 0){{Write-Error ({_ps_quote(path.name)} + ':' + $errors[0].Message);exit 2}}"
        for path in paths
    )
    completed = _run_ps(source, host=host)
    assert completed.returncode == 0, completed.stderr


def test_read_only_bat_is_ascii_and_has_no_mutating_command() -> None:
    raw = HELPER_BAT.read_bytes()
    assert raw.isascii()
    text = raw.decode("ascii")
    assert "-DryRun" in text
    for forbidden in (
        "Import-Certificate",
        "Remove-Item",
        "reg add",
        "sc.exe create",
        "sc.exe delete",
        "Start-Process",
    ):
        assert forbidden not in text
