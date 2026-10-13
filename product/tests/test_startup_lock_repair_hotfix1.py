from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import psutil
import pytest


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = ROOT / "scripts" / "live_process_discovery.ps1"
WSS_ENVIRONMENT_HELPER = ROOT / "scripts" / "wss_environment_preflight.ps1"
REPAIR = ROOT / "scripts" / "startup_repair.ps1"
REPAIR_BAT = ROOT / "scripts" / "startup_repair.bat"
STARTUP = ROOT / "scripts" / "start_live_npcap_overlay.ps1"
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
CONTRACT = ROOT / "scripts" / "release_zip_contract.json"
CURRENT_GENERATION = "a" * 64


def _powershell_hosts() -> list[Path]:
    candidates = [
        Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
        Path(r"C:\Program Files\PowerShell\7\pwsh.exe"),
    ]
    return [candidate for candidate in candidates if candidate.is_file()]


def _powershell() -> Path:
    hosts = _powershell_hosts()
    if not hosts:
        pytest.skip("PowerShell unavailable")
    return hosts[0]


def _ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(
    body: str, *, timeout: int = 45, host: Path | None = None
) -> dict[str, object]:
    completed = subprocess.run(
        [
            str(host or _powershell()),
            "-NoProfile",
            "-NonInteractive",
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
        timeout=timeout,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _probe_epoch_token(
    tmp_path: Path, epoch_token: str, *, host: Path
) -> dict[str, object]:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    package_root = tmp_path / "current"
    monitor = package_root / "scripts" / "local_compat_monitor.py"
    overlay = package_root / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    lock = log_dir / "monitor.lock"
    overlay_state = log_dir / "ahmad_overlay.pid"
    lock.write_text(
        json.dumps(
            _lock_payload(
                pid=2_147_483_646,
                started_at=1.25,
                monitor=monitor,
                log_dir=log_dir,
                generation=CURRENT_GENERATION,
            ),
            separators=(",", ":"),
        ).replace("1.25", epoch_token),
        encoding="utf-8",
    )
    overlay_state.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pid": 2_147_483_646,
                "started_at": 1.25,
                "kind": "ahmad_overlay",
                "overlay_path": str(overlay),
                "package_generation_identity": CURRENT_GENERATION,
            },
            separators=(",", ":"),
        ).replace("1.25", epoch_token),
        encoding="utf-8",
    )
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(log_dir)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
$lockIdentity=Get-BidKingMonitorLockIdentity -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(monitor)} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(monitor)} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
$owner=[pscustomobject]@{{PackageRoot={_ps_quote(package_root)};Generation='{CURRENT_GENERATION}'}}
$overlayIdentity=Get-BidKingRepairOverlayOwnership -MonitorOwnership $owner
$overlayRemoval=Remove-BidKingMonitorLockIfFingerprint -StatePath {_ps_quote(overlay_state)} -Identity $overlayIdentity
[pscustomobject]@{{lock_state=$lockIdentity.State;cleanup=$cleanup.State;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)});overlay_state=$overlayIdentity.State;overlay_removal=$overlayRemoval;overlay_exists=(Test-Path -LiteralPath {_ps_quote(overlay_state)})}}|ConvertTo-Json -Compress
"""
    return _run_powershell(body, host=host)


def _probe_overlay_integer_tokens(
    tmp_path: Path,
    *,
    host: Path,
    schema_token: str = "2",
    pid_token: str = "2147483646",
    epoch_token: str = "1.25",
) -> dict[str, object]:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    package_root = tmp_path / "current"
    overlay = package_root / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    overlay_state = log_dir / "ahmad_overlay.pid"
    overlay_state.write_text(
        (
            '{"schema_version":'
            + schema_token
            + ',"pid":'
            + pid_token
            + ',"started_at":'
            + epoch_token
            + ',"kind":"ahmad_overlay","overlay_path":'
            + json.dumps(str(overlay))
            + ',"package_generation_identity":'
            + json.dumps(CURRENT_GENERATION)
            + "}"
        ),
        encoding="utf-8",
    )
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(log_dir)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
$script:epochImpl=${{function:Get-BidKingValidatedEpoch}}
$script:epochCalls=0
function Get-BidKingValidatedEpoch {{
  param([object]$Value)
  $script:epochCalls += 1
  return & $script:epochImpl -Value $Value
}}
$owner=[pscustomobject]@{{PackageRoot={_ps_quote(package_root)};Generation='{CURRENT_GENERATION}'}}
$identity=Get-BidKingRepairOverlayOwnership -MonitorOwnership $owner
$removal=Remove-BidKingMonitorLockIfFingerprint -StatePath {_ps_quote(overlay_state)} -Identity $identity
[pscustomobject]@{{state=$identity.State;process_id=$identity.ProcessId;epoch_calls=$script:epochCalls;removal=$removal;exists=(Test-Path -LiteralPath {_ps_quote(overlay_state)})}}|ConvertTo-Json -Compress
"""
    return _run_powershell(body, host=host)


def _lock_payload(
    *,
    pid: int,
    started_at: float,
    monitor: Path,
    log_dir: Path,
    generation: str,
) -> dict[str, object]:
    return {
        "pid": pid,
        "process_started_at": started_at,
        "monitor_path": str(monitor),
        "log_dir": str(log_dir),
        "package_generation_identity": generation,
    }


def test_dead_cross_directory_lock_is_stale_and_atomically_reclaimed(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "shared" / "logs" / "live"
    log_dir.mkdir(parents=True)
    lock = log_dir / "monitor.lock"
    lock.write_text(
        json.dumps(
            _lock_payload(
                pid=2_147_483_646,
                started_at=1.0,
                monitor=tmp_path / "deleted-old" / "scripts" / "local_compat_monitor.py",
                log_dir=log_dir,
                generation="b" * 64,
            ),
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    body = f"""
. {_ps_quote(DISCOVERY)}
$script:stopCalls=0
function Stop-Process {{ [CmdletBinding()]param([int]$Id,[switch]$Force);$script:stopCalls+=1 }}
$identity=Get-BidKingMonitorLockIdentity -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{state=$identity.State;reason=$identity.Reason;succeeded=$cleanup.Succeeded;cleanup_state=$cleanup.State;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)});stop_calls=$script:stopCalls}} | ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "state": "stale",
        "reason": "pid_absent",
        "succeeded": True,
        "cleanup_state": "reclaimed",
        "lock_exists": False,
        "stop_calls": 0,
    }


@pytest.mark.parametrize("case", ["query_denied", "pid_reuse"])
def test_unknown_process_query_or_pid_reuse_preserves_lock(
    tmp_path: Path,
    case: str,
) -> None:
    process = psutil.Process()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    started_at = process.create_time() + (10_000 if case == "pid_reuse" else 0)
    lock.write_text(
        json.dumps(
            _lock_payload(
                pid=process.pid,
                started_at=started_at,
                monitor=tmp_path / "scripts" / "local_compat_monitor.py",
                log_dir=log_dir,
                generation=CURRENT_GENERATION,
            )
        ),
        encoding="utf-8",
    )
    override = ""
    if case == "query_denied":
        override = """
function Get-Process {
  [CmdletBinding()]param([int]$Id,[string]$Name)
  throw [System.UnauthorizedAccessException]::new('synthetic-denied')
}
"""
    body = f"""
. {_ps_quote(DISCOVERY)}
{override}
$identity=Get-BidKingMonitorLockIdentity -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{state=$identity.State;reason=$identity.Reason;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)})}} | ConvertTo-Json -Compress
"""
    result = _run_powershell(body)
    assert result["state"] == "unknown"
    assert result["reason"] == (
        "process_query_unknown" if case == "query_denied" else "pid_epoch_mismatch"
    )
    assert result["lock_exists"] is True


def test_dead_lock_fingerprint_change_aborts_without_delete(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    payload = _lock_payload(
        pid=2_147_483_646,
        started_at=1.0,
        monitor=tmp_path / "old" / "scripts" / "local_compat_monitor.py",
        log_dir=log_dir,
        generation="b" * 64,
    )
    lock.write_text(json.dumps(payload), encoding="utf-8")
    replacement = json.dumps({**payload, "nonce": "replacement"}).replace("'", "''")
    body = f"""
. {_ps_quote(DISCOVERY)}
$script:calls=0
$script:impl=${{function:Get-BidKingMonitorLockIdentity}}
function Get-BidKingMonitorLockIdentity {{
  param([string]$StatePath,[string]$ExpectedMonitorPath,[string]$ExpectedLogDir,[string]$ExpectedPackageGenerationIdentity)
  $script:calls+=1
  $result=& $script:impl -StatePath $StatePath -ExpectedMonitorPath $ExpectedMonitorPath -ExpectedLogDir $ExpectedLogDir -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  if($script:calls -eq 1){{[IO.File]::WriteAllText($StatePath,'{replacement}',[Text.UTF8Encoding]::new($false))}}
  return $result
}}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{succeeded=$cleanup.Succeeded;state=$cleanup.State;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)});replacement=([IO.File]::ReadAllText({_ps_quote(lock)})).Contains('replacement')}} | ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "succeeded": False,
        "state": "changed",
        "lock_exists": True,
        "replacement": True,
    }


def test_stable_dead_lock_atomic_unknown_quarantines_exact_bytes_and_is_idempotent(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "中文 logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    raw = json.dumps(
        _lock_payload(
            pid=2_147_483_646,
            started_at=1.0,
            monitor=tmp_path / "旧 包" / "scripts" / "local_compat_monitor.py",
            log_dir=log_dir,
            generation="b" * 64,
        ),
        separators=(",", ":"),
    ).encode()
    lock.write_bytes(raw)
    expected_base64 = base64.b64encode(raw).decode()
    body = f"""
. {_ps_quote(DISCOVERY)}
function Remove-BidKingMonitorLockIfFingerprint {{return 'unknown'}}
$first=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
$quarantines=@(Get-ChildItem -LiteralPath {_ps_quote(log_dir)} -Filter 'monitor.lock.quarantine.*' -File)
$second=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
$quarantineExact=$false
if($quarantines.Count -eq 1){{$quarantineExact=([Convert]::ToBase64String([IO.File]::ReadAllBytes($quarantines[0].FullName)) -ceq '{expected_base64}')}}
[pscustomobject]@{{first_succeeded=$first.Succeeded;first_state=$first.State;second_succeeded=$second.Succeeded;second_state=$second.State;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)});quarantine_count=$quarantines.Count;quarantine_exact=$quarantineExact}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "first_succeeded": True,
        "first_state": "quarantined",
        "second_succeeded": True,
        "second_state": "absent",
        "lock_exists": False,
        "quarantine_count": 1,
        "quarantine_exact": True,
    }


@pytest.mark.parametrize("post_unknown_state", ["current", "unknown"])
def test_post_atomic_unknown_reproves_dead_pid_and_epoch_before_quarantine(
    tmp_path: Path,
    post_unknown_state: str,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    raw = json.dumps(
        _lock_payload(
            pid=2_147_483_646,
            started_at=1.0,
            monitor=tmp_path / "old" / "scripts" / "local_compat_monitor.py",
            log_dir=log_dir,
            generation="b" * 64,
        ),
        separators=(",", ":"),
    ).encode()
    lock.write_bytes(raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    body = f"""
. {_ps_quote(DISCOVERY)}
$script:identityCalls=0;$script:moveCalls=0;$script:afterUnknown=$false
function Get-BidKingMonitorLockIdentity {{
  $script:identityCalls+=1
  if($script:afterUnknown){{return [pscustomobject]@{{State='{post_unknown_state}';Reason='synthetic_post_unknown_transition';ProcessId=2147483646;ProcessStartedAt=2.0;RawFingerprint='{fingerprint}';MonitorPath='changed';LogDir='changed'}}}}
  return [pscustomobject]@{{State='stale';Reason='pid_absent';ProcessId=2147483646;ProcessStartedAt=1.0;RawFingerprint='{fingerprint}';MonitorPath='old';LogDir={_ps_quote(log_dir)}}}
}}
function Remove-BidKingMonitorLockIfFingerprint {{$script:afterUnknown=$true;return 'unknown'}}
function Move-BidKingDeadMonitorLockSameDirectory {{param([string]$SourcePath,[string]$DestinationPath);$script:moveCalls+=1;[IO.File]::Move($SourcePath,$DestinationPath)}}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{succeeded=$cleanup.Succeeded;state=$cleanup.State;identity_calls=$script:identityCalls;move_calls=$script:moveCalls;source_exists=(Test-Path -LiteralPath {_ps_quote(lock)});quarantine_count=@(Get-ChildItem -LiteralPath {_ps_quote(log_dir)} -Filter 'monitor.lock.quarantine.*').Count}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "succeeded": False,
        "state": "changed",
        "identity_calls": 3,
        "move_calls": 0,
        "source_exists": True,
        "quarantine_count": 0,
    }


def test_post_atomic_unknown_parent_replacement_fails_before_move(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old_parent = tmp_path / "logs.original"
    lock = log_dir / "monitor.lock"
    raw = json.dumps(
        _lock_payload(
            pid=2_147_483_646,
            started_at=1.0,
            monitor=tmp_path / "old" / "scripts" / "local_compat_monitor.py",
            log_dir=log_dir,
            generation="b" * 64,
        ),
        separators=(",", ":"),
    ).encode()
    lock.write_bytes(raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    body = f"""
. {_ps_quote(DISCOVERY)}
$script:parentReplaced=$false;$script:identityCalls=0;$script:moveCalls=0
function Open-BidKingDeadMonitorLockParentLease {{return [pscustomobject]@{{ParentPath={_ps_quote(log_dir)}}}}}
function Close-BidKingDeadMonitorLockParentLease {{}}
function Test-BidKingDeadMonitorLockParentLease {{return (-not $script:parentReplaced)}}
function Get-BidKingMonitorLockIdentity {{$script:identityCalls+=1;return [pscustomobject]@{{State='stale';Reason='pid_absent';ProcessId=2147483646;ProcessStartedAt=1.0;RawFingerprint='{fingerprint}';MonitorPath='old';LogDir={_ps_quote(log_dir)}}}}}
function Remove-BidKingMonitorLockIfFingerprint {{
  [IO.Directory]::Move({_ps_quote(log_dir)},{_ps_quote(old_parent)})
  [IO.Directory]::CreateDirectory({_ps_quote(log_dir)})|Out-Null
  [IO.File]::WriteAllBytes({_ps_quote(lock)},[IO.File]::ReadAllBytes({_ps_quote(old_parent / 'monitor.lock')}))
  $script:parentReplaced=$true
  return 'unknown'
}}
function Move-BidKingDeadMonitorLockSameDirectory {{param([string]$SourcePath,[string]$DestinationPath);$script:moveCalls+=1;[IO.File]::Move($SourcePath,$DestinationPath)}}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{succeeded=$cleanup.Succeeded;state=$cleanup.State;identity_calls=$script:identityCalls;move_calls=$script:moveCalls;replacement_source=(Test-Path -LiteralPath {_ps_quote(lock)});old_source=(Test-Path -LiteralPath {_ps_quote(old_parent / 'monitor.lock')});quarantine_count=@(Get-ChildItem -LiteralPath {_ps_quote(log_dir)} -Filter 'monitor.lock.quarantine.*').Count}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "succeeded": False,
        "state": "changed",
        "identity_calls": 3,
        "move_calls": 0,
        "replacement_source": True,
        "old_source": True,
        "quarantine_count": 0,
    }


def test_post_atomic_unknown_source_directory_transition_is_cleanup_failure(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    raw = json.dumps(
        _lock_payload(
            pid=2_147_483_646,
            started_at=1.0,
            monitor=tmp_path / "old" / "scripts" / "local_compat_monitor.py",
            log_dir=log_dir,
            generation="b" * 64,
        ),
        separators=(",", ":"),
    ).encode()
    lock.write_bytes(raw)
    body = f"""
. {_ps_quote(DISCOVERY)}
$script:moveCalls=0
function Remove-BidKingMonitorLockIfFingerprint {{
  [IO.File]::Delete({_ps_quote(lock)})
  [IO.Directory]::CreateDirectory({_ps_quote(lock)})|Out-Null
  return 'unknown'
}}
function Move-BidKingDeadMonitorLockSameDirectory {{$script:moveCalls+=1}}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{succeeded=$cleanup.Succeeded;state=$cleanup.State;move_calls=$script:moveCalls;source_is_directory=(Test-Path -LiteralPath {_ps_quote(lock)} -PathType Container);quarantine_count=@(Get-ChildItem -LiteralPath {_ps_quote(log_dir)} -Filter 'monitor.lock.quarantine.*').Count}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "succeeded": False,
        "state": "quarantine_failed",
        "move_calls": 0,
        "source_is_directory": True,
        "quarantine_count": 0,
    }


def test_parent_directory_lease_blocks_rename_and_keeps_object_identity(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    renamed = tmp_path / "logs.renamed"
    body = f"""
. {_ps_quote(DISCOVERY)}
$lease=Open-BidKingDeadMonitorLockParentLease -StatePath {_ps_quote(log_dir / 'monitor.lock')} -ExpectedLogDir {_ps_quote(log_dir)}
$before=Test-BidKingDeadMonitorLockParentLease -Lease $lease -StatePath {_ps_quote(log_dir / 'monitor.lock')} -ExpectedLogDir {_ps_quote(log_dir)}
$blocked=$false
try{{[IO.Directory]::Move({_ps_quote(log_dir)},{_ps_quote(renamed)})}}catch{{$blocked=$true}}
$after=Test-BidKingDeadMonitorLockParentLease -Lease $lease -StatePath {_ps_quote(log_dir / 'monitor.lock')} -ExpectedLogDir {_ps_quote(log_dir)}
Close-BidKingDeadMonitorLockParentLease -Lease $lease
[pscustomobject]@{{before=$before;rename_blocked=$blocked;after=$after;original_exists=(Test-Path -LiteralPath {_ps_quote(log_dir)});renamed_exists=(Test-Path -LiteralPath {_ps_quote(renamed)})}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "before": True,
        "rename_blocked": True,
        "after": True,
        "original_exists": True,
        "renamed_exists": False,
    }


def test_source_file_lease_blocks_leaf_swap_before_handle_bound_rename(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    moved = log_dir / "monitor.lock.replaced"
    raw = b'{"stable":"dead-lock"}'
    lock.write_bytes(raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    body = f"""
. {_ps_quote(DISCOVERY)}
$parentLease=Open-BidKingDeadMonitorLockParentLease -StatePath {_ps_quote(lock)} -ExpectedLogDir {_ps_quote(log_dir)}
$sourceLease=Open-BidKingDeadMonitorLockSourceLease -StatePath {_ps_quote(lock)} -ParentLease $parentLease -ExpectedFingerprint '{fingerprint}'
$blocked=$false
try{{[IO.File]::Move({_ps_quote(lock)},{_ps_quote(moved)})}}catch{{$blocked=$true}}
$heldExact=([string]$sourceLease.Fingerprint -ceq '{fingerprint}')
Close-BidKingDeadMonitorLockSourceLease -Lease $sourceLease
Close-BidKingDeadMonitorLockParentLease -Lease $parentLease
[pscustomobject]@{{rename_blocked=$blocked;held_exact=$heldExact;source_exists=(Test-Path -LiteralPath {_ps_quote(lock)});moved_exists=(Test-Path -LiteralPath {_ps_quote(moved)})}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "rename_blocked": True,
        "held_exact": True,
        "source_exists": True,
        "moved_exists": False,
    }


def test_handle_bound_quarantine_rename_never_replaces_existing_leaf(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    collision = log_dir / "monitor.lock.quarantine.collision"
    source_raw = b'{"stable":"dead-lock"}'
    collision_raw = b"existing-evidence"
    lock.write_bytes(source_raw)
    collision.write_bytes(collision_raw)
    fingerprint = hashlib.sha256(source_raw).hexdigest()
    body = f"""
. {_ps_quote(DISCOVERY)}
$parentLease=Open-BidKingDeadMonitorLockParentLease -StatePath {_ps_quote(lock)} -ExpectedLogDir {_ps_quote(log_dir)}
$sourceLease=Open-BidKingDeadMonitorLockSourceLease -StatePath {_ps_quote(lock)} -ParentLease $parentLease -ExpectedFingerprint '{fingerprint}'
$blocked=$false
try{{Move-BidKingDeadMonitorLockSameDirectory -SourcePath {_ps_quote(lock)} -DestinationPath {_ps_quote(collision)} -ParentLease $parentLease -SourceLease $sourceLease}}catch{{$blocked=$true}}
Close-BidKingDeadMonitorLockSourceLease -Lease $sourceLease
Close-BidKingDeadMonitorLockParentLease -Lease $parentLease
[pscustomobject]@{{collision_blocked=$blocked;source_exact=([Convert]::ToBase64String([IO.File]::ReadAllBytes({_ps_quote(lock)})));collision_exact=([Convert]::ToBase64String([IO.File]::ReadAllBytes({_ps_quote(collision)})))}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "collision_blocked": True,
        "source_exact": base64.b64encode(source_raw).decode(),
        "collision_exact": base64.b64encode(collision_raw).decode(),
    }


@pytest.mark.parametrize(
    ("case", "expected_state"),
    [
        ("changed", "changed"),
        ("reparse", "quarantine_failed"),
        ("collision", "quarantine_failed"),
        ("rename_failed", "quarantine_failed"),
    ],
)
def test_dead_lock_quarantine_red_gates_preserve_original(
    tmp_path: Path,
    case: str,
    expected_state: str,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    raw = json.dumps(
        _lock_payload(
            pid=2_147_483_646,
            started_at=1.0,
            monitor=tmp_path / "old" / "scripts" / "local_compat_monitor.py",
            log_dir=log_dir,
            generation="b" * 64,
        ),
        separators=(",", ":"),
    )
    lock.write_text(raw, encoding="utf-8")
    collision = log_dir / "monitor.lock.quarantine.collision"
    collision.write_text("existing-evidence", encoding="utf-8")
    override = ""
    if case == "changed":
        override = """
function Get-BidKingDeadMonitorLockFileEvidence {
  return [pscustomobject]@{State='changed'}
}
"""
    elif case == "reparse":
        override = """
function Get-BidKingDeadMonitorLockFileEvidence {
  return [pscustomobject]@{State='invalid'}
}
"""
    elif case == "collision":
        override = f"""
function New-BidKingDeadMonitorLockQuarantinePath {{return {_ps_quote(collision)}}}
"""
    else:
        override = """
function Move-BidKingDeadMonitorLockSameDirectory {
  throw [System.UnauthorizedAccessException]::new('synthetic-denied')
}
"""
    body = f"""
. {_ps_quote(DISCOVERY)}
function Remove-BidKingMonitorLockIfFingerprint {{return 'unknown'}}
{override}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{succeeded=$cleanup.Succeeded;state=$cleanup.State;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)});original_exact=([IO.File]::ReadAllText({_ps_quote(lock)}) -ceq {_ps_quote(raw)});collision_exact=([IO.File]::ReadAllText({_ps_quote(collision)}) -ceq 'existing-evidence')}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "succeeded": False,
        "state": expected_state,
        "lock_exists": True,
        "original_exact": True,
        "collision_exact": True,
    }


def test_real_file_reparse_dead_lock_is_rejected_or_capability_skipped(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    target = log_dir / "monitor.target"
    lock = log_dir / "monitor.lock"
    raw = json.dumps(
        _lock_payload(
            pid=2_147_483_646,
            started_at=1.0,
            monitor=tmp_path / "old" / "scripts" / "local_compat_monitor.py",
            log_dir=log_dir,
            generation="b" * 64,
        ),
        separators=(",", ":"),
    ).encode()
    target.write_bytes(raw)
    try:
        os.symlink(target, lock)
    except OSError as exc:
        pytest.skip(f"Windows file symlink capability unavailable: {exc}")
    body = f"""
. {_ps_quote(DISCOVERY)}
$script:moveCalls=0
function Remove-BidKingMonitorLockIfFingerprint {{return 'unknown'}}
function Move-BidKingDeadMonitorLockSameDirectory {{param([string]$SourcePath,[string]$DestinationPath);$script:moveCalls+=1;[IO.File]::Move($SourcePath,$DestinationPath)}}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(tmp_path / 'current' / 'scripts' / 'local_compat_monitor.py')} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{succeeded=$cleanup.Succeeded;state=$cleanup.State;move_calls=$script:moveCalls;source_exists=(Test-Path -LiteralPath {_ps_quote(lock)});target_exact=([Convert]::ToBase64String([IO.File]::ReadAllBytes({_ps_quote(target)})) -ceq '{base64.b64encode(raw).decode()}')}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "succeeded": False,
        "state": "quarantine_failed",
        "move_calls": 0,
        "source_exists": True,
        "target_exact": True,
    }


def test_generic_atomic_delete_never_invokes_dead_lock_quarantine() -> None:
    body = f"""
. {_ps_quote(DISCOVERY)}
$script:moveCalls=0
function Move-BidKingDeadMonitorLockToQuarantineIfFingerprint {{$script:moveCalls+=1;return [pscustomobject]@{{State='quarantined'}}}}
$identity=[pscustomobject]@{{State='current';RawFingerprint='{'a' * 64}'}}
$result=Remove-BidKingMonitorLockIfFingerprint -StatePath {_ps_quote(ROOT / 'absent-monitor.lock')} -Identity $identity
[pscustomobject]@{{result=$result;move_calls=$script:moveCalls}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {"result": "absent", "move_calls": 0}


def test_live_current_lock_never_enters_dead_lock_quarantine(tmp_path: Path) -> None:
    process = psutil.Process()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monitor = tmp_path / "current" / "scripts" / "local_compat_monitor.py"
    lock = log_dir / "monitor.lock"
    lock.write_text(
        json.dumps(
            _lock_payload(
                pid=process.pid,
                started_at=process.create_time(),
                monitor=monitor,
                log_dir=log_dir,
                generation=CURRENT_GENERATION,
            )
        ),
        encoding="utf-8",
    )
    body = f"""
. {_ps_quote(DISCOVERY)}
$script:moveCalls=0
function Move-BidKingDeadMonitorLockToQuarantineIfFingerprint {{$script:moveCalls+=1;return [pscustomobject]@{{State='quarantined'}}}}
$cleanup=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(monitor)} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[pscustomobject]@{{succeeded=$cleanup.Succeeded;state=$cleanup.State;move_calls=$script:moveCalls;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)})}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "succeeded": False,
        "state": "current",
        "move_calls": 0,
        "lock_exists": True,
    }


@pytest.mark.parametrize(
    ("cleanup_state", "succeeded", "classification", "action", "relieved"),
    [
        ("quarantined", True, "repaired", "dead_lock_quarantined", True),
        ("changed", False, "identity_changed", "preserved", False),
        ("unknown", False, "cleanup_failed", "preserved", False),
        ("quarantine_failed", False, "cleanup_failed", "preserved", False),
    ],
)
def test_startup_repair_reports_dead_lock_quarantine_and_distinct_failures(
    cleanup_state: str,
    succeeded: bool,
    classification: str,
    action: str,
    relieved: bool,
) -> None:
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(ROOT)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
$snapshot=[pscustomobject]@{{Classification='dead_lock';Identity=[pscustomobject]@{{RawFingerprint='same';ProcessId=1;ProcessStartedAt=1.0}};Monitor=$null;Overlay=$null}}
function Get-BidKingRepairSnapshot {{return $snapshot}}
function Invoke-BidKingDeadMonitorLockCleanup {{return [pscustomobject]@{{Succeeded=${str(succeeded).lower()};State='{cleanup_state}';Mode='{cleanup_state}'}}}}
$result=Invoke-BidKingRepairApply -Initial $snapshot -CurrentGeneration '{CURRENT_GENERATION}'
[pscustomobject]@{{classification=$result.Classification;action=$result.Action;relieved=$result.Relieved;next_step=$result.NextStep}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "classification": classification,
        "action": action,
        "relieved": relieved,
        "next_step": "start_current_package" if relieved else "retry_repair",
    }


def _write_packaged_generation_fixture(root: Path) -> tuple[Path, Path, Path]:
    scripts = root / "scripts"
    runtime_dir = root / "runtime" / "python"
    scripts.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    monitor = scripts / "local_compat_monitor.py"
    runtime = runtime_dir / "pythonw.exe"
    helper = scripts / "package_generation_identity.py"
    monitor.write_text("# old monitor\n", encoding="utf-8")
    runtime.write_bytes(b"synthetic-old-runtime")
    shutil.copy2(ROOT / "scripts" / "package_generation_identity.py", helper)
    (scripts / "start_live_npcap_overlay.ps1").write_text(
        "# old launcher\n", encoding="utf-8"
    )
    (scripts / "_client_launcher.cp313-win_amd64.pyd").write_bytes(b"launcher")
    (scripts / "_local_compat_monitor.cp313-win_amd64.pyd").write_bytes(b"monitor")
    (scripts / "_local_compat_addon_loader.pyd").write_bytes(b"addon")
    (scripts / "local_compat_addon.py").write_text("# addon\n", encoding="utf-8")
    (root / "BUILD_MANIFEST.txt").write_text(
        "PackageVersion: 0.3.3\n"
        f"SourceCommit: {'1' * 40}\n"
        "EncryptedUploadConfig: False\n"
        "ProductProfile: standard\n"
        "Encrypted: True\n"
        "NativeBinaryDistribution: unsigned\n",
        encoding="utf-8",
    )
    return monitor, runtime, helper


@pytest.mark.parametrize(
    ("game_active", "same_generation", "package_deleted"),
    [(False, False, False), (False, True, False), (True, True, False), (False, False, True)],
    ids=[
        "old-generation",
        "relocated-current-generation",
        "game-active",
        "old-directory-deleted",
    ],
)
def test_verified_old_package_apply_stops_only_controlled_child_and_respects_game(
    tmp_path: Path,
    game_active: bool,
    same_generation: bool,
    package_deleted: bool,
) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from package_generation_identity import compute_package_generation_identity
    finally:
        sys.path.pop(0)

    old_root = tmp_path / "旧 包"
    monitor, runtime, helper = _write_packaged_generation_fixture(old_root)
    generation = compute_package_generation_identity(
        package_root=old_root,
        monitor_path=monitor,
        runtime_path=runtime,
        identity_helper_path=helper,
    ).digest
    log_dir = tmp_path / "shared logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    process = psutil.Process(child.pid)
    lock.write_text(
        json.dumps(
            _lock_payload(
                pid=child.pid,
                started_at=process.create_time(),
                monitor=monitor,
                log_dir=log_dir,
                generation=generation,
            )
        ),
        encoding="utf-8",
    )
    command_line = (
        f'"{runtime}" "{monitor}" --log-dir "{log_dir}" '
        f"--package-generation-identity {generation}"
    )
    current_generation = generation if same_generation else CURRENT_GENERATION
    if package_deleted:
        shutil.rmtree(old_root)
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(log_dir)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
function Get-CimInstance {{
  [CmdletBinding()]param([string]$ClassName,[string]$Filter,[int]$OperationTimeoutSec)
  return [pscustomobject]@{{ProcessId={child.pid};CommandLine={_ps_quote(command_line)};ExecutablePath={_ps_quote(runtime)}}}
}}
function Get-BidKingGameState {{ return '{"alive" if game_active else "absent"}' }}
$snapshot=Get-BidKingRepairSnapshot -CurrentGeneration '{current_generation}'
$result=Invoke-BidKingRepairApply -Initial $snapshot -CurrentGeneration '{current_generation}'
[pscustomobject]@{{before=$snapshot.Classification;classification=$result.Classification;action=$result.Action;relieved=$result.Relieved;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)});child_alive=[bool](Get-Process -Id {child.pid} -ErrorAction SilentlyContinue)}} | ConvertTo-Json -Compress
"""
    try:
        result = _run_powershell(body, timeout=60)
        expected_before = (
            "foreign_or_unknown"
            if package_deleted
            else ("game_active" if game_active else "verified_old_package_running")
        )
        assert result["before"] == expected_before
        if package_deleted:
            assert result == {
                "before": "foreign_or_unknown",
                "classification": "foreign_or_unknown",
                "action": "preserved",
                "relieved": False,
                "lock_exists": True,
                "child_alive": True,
            }
            assert child.poll() is None
        elif game_active:
            assert result == {
                "before": "game_active",
                "classification": "game_active",
                "action": "preserved",
                "relieved": False,
                "lock_exists": True,
                "child_alive": True,
            }
            assert child.poll() is None
        else:
            assert result == {
                "before": "verified_old_package_running",
                "classification": "repaired",
                "action": "old_package_stopped",
                "relieved": True,
                "lock_exists": False,
                "child_alive": False,
            }
            child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_process_query_unknown_after_normal_close_never_forces_stop() -> None:
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(ROOT)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
$fake=[pscustomobject]@{{}}
$fake | Add-Member -MemberType ScriptMethod -Name CloseMainWindow -Value {{ return $true }}
$script:probeCalls=0
function Get-BidKingProcessEpochState {{
  param([int]$ProcessId,[double]$StartedAt)
  $script:probeCalls+=1
  if($script:probeCalls -eq 1){{return [pscustomobject]@{{State='alive';Process=$fake}}}}
  return [pscustomobject]@{{State='unknown';Process=$null}}
}}
$script:stopCalls=0
function Stop-Process {{[CmdletBinding()]param([int]$Id,[switch]$Force);$script:stopCalls+=1}}
$result=Stop-BidKingVerifiedProcess -ProcessId 4242 -StartedAt 1.0
[pscustomobject]@{{result=$result;stop_calls=$script:stopCalls;probe_calls=$script:probeCalls}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "result": False,
        "stop_calls": 0,
        "probe_calls": 2,
    }


def test_game_query_denied_is_unknown_not_absent() -> None:
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(ROOT)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
function Get-Process {{[CmdletBinding()]param([string]$Name,[int]$Id);throw [UnauthorizedAccessException]::new('denied')}}
[pscustomobject]@{{state=(Get-BidKingGameState)}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {"state": "unknown"}


def test_true_foreign_python_is_preserved_without_stop(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    process = psutil.Process(child.pid)
    foreign_monitor = tmp_path / "foreign" / "scripts" / "other_monitor.py"
    lock.write_text(
        json.dumps(
            _lock_payload(
                pid=child.pid,
                started_at=process.create_time(),
                monitor=foreign_monitor,
                log_dir=log_dir,
                generation="e" * 64,
            )
        ),
        encoding="utf-8",
    )
    command_line = (
        f'"{sys.executable}" "{foreign_monitor}" --log-dir "{log_dir}" '
        f"--package-generation-identity {'e' * 64}"
    )
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(log_dir)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
function Get-CimInstance {{[CmdletBinding()]param([string]$ClassName,[string]$Filter,[int]$OperationTimeoutSec);return [pscustomobject]@{{ProcessId={child.pid};CommandLine={_ps_quote(command_line)};ExecutablePath={_ps_quote(Path(sys.executable))}}}}}
function Get-BidKingGameState {{return 'absent'}}
$script:stopCalls=0
function Stop-Process {{[CmdletBinding()]param([int]$Id,[switch]$Force);$script:stopCalls+=1}}
$snapshot=Get-BidKingRepairSnapshot -CurrentGeneration '{CURRENT_GENERATION}'
$result=Invoke-BidKingRepairApply -Initial $snapshot -CurrentGeneration '{CURRENT_GENERATION}'
[pscustomobject]@{{before=$snapshot.Classification;classification=$result.Classification;action=$result.Action;stop_calls=$script:stopCalls;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)})}}|ConvertTo-Json -Compress
"""
    try:
        assert _run_powershell(body) == {
            "before": "foreign_or_unknown",
            "classification": "foreign_or_unknown",
            "action": "preserved",
            "stop_calls": 0,
            "lock_exists": True,
        }
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_user_no_and_elevation_cancel_are_zero_mutation() -> None:
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(ROOT)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
Add-Type -AssemblyName System.Windows.Forms
$script:applyCalls=0
$script:stopCalls=0
function Show-BidKingRepairMessage {{ return [System.Windows.Forms.DialogResult]::No }}
function Invoke-BidKingRepairApply {{$script:applyCalls+=1}}
$confirmed=Confirm-BidKingRepairApply -Summary 'fixed-safe-summary'
function Start-Process {{ throw [System.ComponentModel.Win32Exception]::new('cancelled') }}
function Stop-Process {{$script:stopCalls+=1}}
$elevation='unexpected'
try {{$null=Invoke-BidKingRepairElevation -RepairDryRun $false}} catch {{$elevation='cancelled'}}
[pscustomobject]@{{confirmed=$confirmed;apply_calls=$script:applyCalls;stop_calls=$script:stopCalls;elevation=$elevation}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "confirmed": False,
        "apply_calls": 0,
        "stop_calls": 0,
        "elevation": "cancelled",
    }


def _current_monitor_overlay_fixture(
    root: Path,
    log_dir: Path,
    *,
    overlay_generation_override: str | None = None,
    overlay_epoch_delta: float = 0.0,
) -> tuple[str, subprocess.Popen[bytes], subprocess.Popen[bytes], Path, Path, Path]:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from package_generation_identity import compute_package_generation_identity
    finally:
        sys.path.pop(0)
    monitor, runtime, helper = _write_packaged_generation_fixture(root)
    shutil.copy2(DISCOVERY, root / "scripts" / "live_process_discovery.ps1")
    shutil.copy2(
        WSS_ENVIRONMENT_HELPER,
        root / "scripts" / "wss_environment_preflight.ps1",
    )
    overlay = root / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("# overlay\n", encoding="utf-8")
    generation = compute_package_generation_identity(
        package_root=root,
        monitor_path=monitor,
        runtime_path=runtime,
        identity_helper_path=helper,
    ).digest
    monitor_child = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"]
    )
    overlay_child = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"]
    )
    monitor_process = psutil.Process(monitor_child.pid)
    overlay_process = psutil.Process(overlay_child.pid)
    lock = log_dir / "monitor.lock"
    overlay_state = log_dir / "ahmad_overlay.pid"
    lock.write_text(
        json.dumps(
            _lock_payload(
                pid=monitor_child.pid,
                started_at=monitor_process.create_time(),
                monitor=monitor,
                log_dir=log_dir,
                generation=generation,
            )
        ),
        encoding="utf-8",
    )
    overlay_state.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pid": overlay_child.pid,
                "started_at": overlay_process.create_time() + overlay_epoch_delta,
                "kind": "ahmad_overlay",
                "overlay_path": str(overlay),
                "package_generation_identity": (
                    overlay_generation_override or generation
                ),
            }
        ),
        encoding="utf-8",
    )
    return generation, monitor_child, overlay_child, monitor, runtime, overlay


def test_exact_current_monitor_and_overlay_stop_in_order_and_clear_literal_states(
    tmp_path: Path,
) -> None:
    current_root = tmp_path / "当前 包"
    log_dir = tmp_path / "shared logs"
    log_dir.mkdir()
    generation, monitor_child, overlay_child, monitor, runtime, overlay = (
        _current_monitor_overlay_fixture(current_root, log_dir)
    )
    monitor_command = (
        f'"{runtime}" "{monitor}" --log-dir "{log_dir}" '
        f"--package-generation-identity {generation}"
    )
    overlay_command = f'"{runtime}" "{overlay}" --snapshot "{log_dir / "latest_snapshot.json"}"'
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(current_root)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(log_dir)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(current_root / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
function Get-CimInstance {{
  [CmdletBinding()]param([string]$ClassName,[string]$Filter,[int]$OperationTimeoutSec)
  if($Filter -eq 'ProcessId = {monitor_child.pid}'){{return [pscustomobject]@{{ProcessId={monitor_child.pid};CommandLine={_ps_quote(monitor_command)};ExecutablePath={_ps_quote(runtime)}}}}}
  if($Filter -eq 'ProcessId = {overlay_child.pid}'){{return [pscustomobject]@{{ProcessId={overlay_child.pid};CommandLine={_ps_quote(overlay_command)};ExecutablePath={_ps_quote(runtime)}}}}}
  throw 'unexpected pid'
}}
function Get-BidKingGameState {{ return 'absent' }}
$script:stopOrder=@()
function Stop-Process {{
  [CmdletBinding()]param([int]$Id,[switch]$Force)
  $script:stopOrder += $Id
  Microsoft.PowerShell.Management\\Stop-Process -Id $Id -Force -ErrorAction Stop
}}
$snapshot=Get-BidKingRepairSnapshot -CurrentGeneration '{generation}'
$result=Invoke-BidKingRepairApply -Initial $snapshot -CurrentGeneration '{generation}'
[pscustomobject]@{{before=$snapshot.Classification;classification=$result.Classification;action=$result.Action;relieved=$result.Relieved;lock_exists=(Test-Path -LiteralPath {_ps_quote(log_dir / 'monitor.lock')});overlay_state_exists=(Test-Path -LiteralPath {_ps_quote(log_dir / 'ahmad_overlay.pid')});stop_order=@($script:stopOrder)}}|ConvertTo-Json -Compress
"""
    try:
        result = _run_powershell(body, timeout=60)
        assert result == {
            "before": "current_package_running",
            "classification": "repaired",
            "action": "current_package_stopped",
            "relieved": True,
            "lock_exists": False,
            "overlay_state_exists": False,
            "stop_order": [overlay_child.pid, monitor_child.pid],
        }
        monitor_child.wait(timeout=5)
        overlay_child.wait(timeout=5)
    finally:
        for child in (monitor_child, overlay_child):
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


@pytest.mark.parametrize("fault", ["generation", "epoch"])
def test_overlay_identity_mismatch_blocks_all_monitor_and_overlay_mutation(
    tmp_path: Path,
    fault: str,
) -> None:
    current_root = tmp_path / "current"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    generation, monitor_child, overlay_child, monitor, runtime, overlay = (
        _current_monitor_overlay_fixture(
            current_root,
            log_dir,
            overlay_generation_override=("f" * 64 if fault == "generation" else None),
            overlay_epoch_delta=(10_000.0 if fault == "epoch" else 0.0),
        )
    )
    monitor_command = (
        f'"{runtime}" "{monitor}" --log-dir "{log_dir}" '
        f"--package-generation-identity {generation}"
    )
    overlay_command = f'"{runtime}" "{overlay}" --snapshot x'
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(current_root)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(log_dir)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(current_root / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
function Get-CimInstance {{
  [CmdletBinding()]param([string]$ClassName,[string]$Filter,[int]$OperationTimeoutSec)
  if($Filter -eq 'ProcessId = {monitor_child.pid}'){{return [pscustomobject]@{{ProcessId={monitor_child.pid};CommandLine={_ps_quote(monitor_command)};ExecutablePath={_ps_quote(runtime)}}}}}
  return [pscustomobject]@{{ProcessId={overlay_child.pid};CommandLine={_ps_quote(overlay_command)};ExecutablePath={_ps_quote(runtime)}}}
}}
function Get-BidKingGameState {{ return 'absent' }}
$script:stopCalls=0
function Stop-Process {{[CmdletBinding()]param([int]$Id,[switch]$Force);$script:stopCalls+=1}}
$snapshot=Get-BidKingRepairSnapshot -CurrentGeneration '{generation}'
$result=Invoke-BidKingRepairApply -Initial $snapshot -CurrentGeneration '{generation}'
[pscustomobject]@{{before=$snapshot.Classification;classification=$result.Classification;action=$result.Action;stop_calls=$script:stopCalls;lock_exists=(Test-Path -LiteralPath {_ps_quote(log_dir / 'monitor.lock')});overlay_exists=(Test-Path -LiteralPath {_ps_quote(log_dir / 'ahmad_overlay.pid')})}}|ConvertTo-Json -Compress
"""
    try:
        assert _run_powershell(body, timeout=60) == {
            "before": "foreign_or_unknown",
            "classification": "foreign_or_unknown",
            "action": "preserved",
            "stop_calls": 0,
            "lock_exists": True,
            "overlay_exists": True,
        }
        assert monitor_child.poll() is None
        assert overlay_child.poll() is None
    finally:
        for child in (monitor_child, overlay_child):
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


def test_fallback_new_dead_lock_reproves_pid_absence_before_delete(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lock = log_dir / "monitor.lock"
    expected_monitor = tmp_path / "current" / "scripts" / "local_compat_monitor.py"
    payload = _lock_payload(
        pid=2_147_483_646,
        started_at=1.25,
        monitor=tmp_path / "old" / "scripts" / "local_compat_monitor.py",
        log_dir=log_dir,
        generation="b" * 64,
    )
    body = f"""
. {_ps_quote(DISCOVERY)}
$early=Invoke-BidKingDeadMonitorLockCleanup -StatePath {_ps_quote(lock)} -ExpectedMonitorPath {_ps_quote(expected_monitor)} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}'
[IO.File]::WriteAllText({_ps_quote(lock)}, {_ps_quote(json.dumps(payload))}, [Text.UTF8Encoding]::new($false))
$script:getCalls=0
function Get-Process {{
  [CmdletBinding()]param([int]$Id,[string]$Name)
  $script:getCalls += 1
  if($script:getCalls -eq 1) {{
    $record=[Management.Automation.ErrorRecord]::new(
      [ArgumentException]::new('controlled-absent'),
      'NoProcessFoundForGivenId,Microsoft.PowerShell.Commands.GetProcessCommand',
      [Management.Automation.ErrorCategory]::ObjectNotFound,
      $Id
    )
    throw $record
  }}
  return [pscustomobject]@{{ProcessName='python';StartTime=[DateTime]::UtcNow}}
}}
$null=@(Get-BidKingProcessFromStateFile -StatePath {_ps_quote(lock)} -CommandMarker 'controlled' -DiscoverySource 'controlled' -StatusPath {_ps_quote(log_dir / 'startup.status')} -ExpectedMonitorPath {_ps_quote(expected_monitor)} -ExpectedLogDir {_ps_quote(log_dir)} -ExpectedPackageGenerationIdentity '{CURRENT_GENERATION}')
[pscustomobject]@{{early=$early.State;get_calls=$script:getCalls;lock_exists=(Test-Path -LiteralPath {_ps_quote(lock)})}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "early": "absent",
        "get_calls": 2,
        "lock_exists": True,
    }


def test_dead_foreign_overlay_identity_is_unknown_and_preserved(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    overlay_state = log_dir / "ahmad_overlay.pid"
    overlay_state.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pid": 2_147_483_646,
                "started_at": 1.0,
                "kind": "ahmad_overlay",
                "overlay_path": str(tmp_path / "foreign" / "overlay.py"),
                "package_generation_identity": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(log_dir)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
$monitor=[pscustomobject]@{{PackageRoot={_ps_quote(tmp_path / 'current')};Generation='{CURRENT_GENERATION}'}}
$identity=Get-BidKingRepairOverlayOwnership -MonitorOwnership $monitor
[pscustomobject]@{{state=$identity.State;exists=(Test-Path -LiteralPath {_ps_quote(overlay_state)})}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {"state": "unknown", "exists": True}


def test_overlay_snapshot_stability_is_symmetric_for_all_states() -> None:
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(ROOT)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
function New-Overlay([string]$State,[int]$ProcessIdentifier,[double]$Epoch,[string]$Fingerprint) {{
  return [pscustomobject]@{{State=$State;ProcessId=$ProcessIdentifier;ProcessStartedAt=$Epoch;RawFingerprint=$Fingerprint;OverlayPath='owned-overlay';PackageGenerationIdentity='{CURRENT_GENERATION}'}}
}}
$monitor=[pscustomobject]@{{ProcessId=1;ProcessStartedAt=1.0;RawFingerprint='monitor'}}
function New-Snapshot([object]$Overlay) {{return [pscustomobject]@{{Classification='current_package_running';Identity=$null;Monitor=$monitor;Overlay=$Overlay}}}}
$absent=New-Overlay 'absent' 0 0.0 ''
$stale=New-Overlay 'stale' 2 2.0 'stale'
$current=New-Overlay 'current' 3 3.0 'current'
$replacement=New-Overlay 'stale' 4 4.0 'replacement'
[pscustomobject]@{{
  absent_same=(Test-BidKingRepairSnapshotStable (New-Snapshot $absent) (New-Snapshot $absent))
  absent_to_current=(Test-BidKingRepairSnapshotStable (New-Snapshot $absent) (New-Snapshot $current))
  stale_same=(Test-BidKingRepairSnapshotStable (New-Snapshot $stale) (New-Snapshot $stale))
  stale_replaced=(Test-BidKingRepairSnapshotStable (New-Snapshot $stale) (New-Snapshot $replacement))
  stale_to_current=(Test-BidKingRepairSnapshotStable (New-Snapshot $stale) (New-Snapshot $current))
  current_same=(Test-BidKingRepairSnapshotStable (New-Snapshot $current) (New-Snapshot $current))
  current_to_absent=(Test-BidKingRepairSnapshotStable (New-Snapshot $current) (New-Snapshot $absent))
}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "absent_same": True,
        "absent_to_current": False,
        "stale_same": True,
        "stale_replaced": False,
        "stale_to_current": False,
        "current_same": True,
        "current_to_absent": False,
    }


def test_apply_deletes_only_fresh_exact_owned_stale_overlay() -> None:
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(ROOT)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
$monitor=[pscustomobject]@{{Kind='current';ProcessId=111;ProcessStartedAt=1.0;RawFingerprint='monitor';Generation='{CURRENT_GENERATION}';PackageRoot={_ps_quote(ROOT)}}}
$overlay=[pscustomobject]@{{State='current';ProcessId=222;ProcessStartedAt=2.0;RawFingerprint='overlay';OverlayPath='owned-overlay';PackageGenerationIdentity='{CURRENT_GENERATION}'}}
$stale=[pscustomobject]@{{State='stale';ProcessId=222;ProcessStartedAt=2.0;RawFingerprint='overlay';OverlayPath='owned-overlay';PackageGenerationIdentity='{CURRENT_GENERATION}'}}
$absent=[pscustomobject]@{{State='absent';ProcessId=$null;ProcessStartedAt=$null;RawFingerprint=$null;OverlayPath=$null;PackageGenerationIdentity=$null}}
$identity=[pscustomobject]@{{MonitorPath='monitor';LogDir='logs'}}
$snapshot=[pscustomobject]@{{Classification='current_package_running';Identity=$identity;Monitor=$monitor;Overlay=$overlay}}
$script:snapshotCalls=0
function Get-BidKingRepairSnapshot {{
  param([string]$CurrentGeneration)
  $script:snapshotCalls += 1
  if($script:snapshotCalls -eq 1){{return $snapshot}}
  return [pscustomobject]@{{Classification='no_obstacle';Identity=$null;Monitor=$null;Overlay=$null}}
}}
function Get-BidKingGameState {{return 'absent'}}
function Stop-BidKingVerifiedProcess {{return $true}}
function Get-BidKingMonitorLockIdentity {{return [pscustomobject]@{{State='absent'}}}}
$script:overlayCalls=0
function Get-BidKingRepairOverlayOwnership {{
  param([object]$MonitorOwnership)
  $script:overlayCalls += 1
  if($script:overlayCalls -le 2){{return $stale}}
  return $absent
}}
$script:removeStates=@()
function Remove-BidKingMonitorLockIfFingerprint {{
  param([string]$StatePath,[object]$Identity)
  $script:removeStates += [string]$Identity.State
  return 'deleted'
}}
$result=Invoke-BidKingRepairApply -Initial $snapshot -CurrentGeneration '{CURRENT_GENERATION}'
[pscustomobject]@{{classification=$result.Classification;action=$result.Action;overlay_calls=$script:overlayCalls;remove_states=@($script:removeStates)}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "classification": "repaired",
        "action": "current_package_stopped",
        "overlay_calls": 3,
        "remove_states": ["stale"],
    }


@pytest.mark.parametrize("transition", ["unknown", "replacement", "current"])
def test_apply_preserves_overlay_when_fresh_stale_identity_changes(
    transition: str,
) -> None:
    fresh_state = "stale" if transition == "replacement" else transition
    fresh_pid = 333 if transition == "replacement" else 222
    fresh_fingerprint = "replacement" if transition == "replacement" else "overlay"
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(ROOT)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
$monitor=[pscustomobject]@{{Kind='current';ProcessId=111;ProcessStartedAt=1.0;RawFingerprint='monitor';Generation='{CURRENT_GENERATION}';PackageRoot={_ps_quote(ROOT)}}}
$overlay=[pscustomobject]@{{State='current';ProcessId=222;ProcessStartedAt=2.0;RawFingerprint='overlay';OverlayPath='owned-overlay';PackageGenerationIdentity='{CURRENT_GENERATION}'}}
$stale=[pscustomobject]@{{State='stale';ProcessId=222;ProcessStartedAt=2.0;RawFingerprint='overlay';OverlayPath='owned-overlay';PackageGenerationIdentity='{CURRENT_GENERATION}'}}
$changed=[pscustomobject]@{{State='{fresh_state}';ProcessId={fresh_pid};ProcessStartedAt=2.0;RawFingerprint='{fresh_fingerprint}';OverlayPath='owned-overlay';PackageGenerationIdentity='{CURRENT_GENERATION}'}}
$identity=[pscustomobject]@{{MonitorPath='monitor';LogDir='logs'}}
$snapshot=[pscustomobject]@{{Classification='current_package_running';Identity=$identity;Monitor=$monitor;Overlay=$overlay}}
function Get-BidKingRepairSnapshot {{return $snapshot}}
function Get-BidKingGameState {{return 'absent'}}
$script:stopCalls=0
function Stop-BidKingVerifiedProcess {{$script:stopCalls+=1;return $true}}
$script:overlayCalls=0
function Get-BidKingRepairOverlayOwnership {{
  param([object]$MonitorOwnership)
  $script:overlayCalls += 1
  if($script:overlayCalls -eq 1){{return $stale}}
  return $changed
}}
$script:removeCalls=0
function Remove-BidKingMonitorLockIfFingerprint {{$script:removeCalls+=1;return 'deleted'}}
function Get-BidKingMonitorLockIdentity {{return [pscustomobject]@{{State='absent'}}}}
$result=Invoke-BidKingRepairApply -Initial $snapshot -CurrentGeneration '{CURRENT_GENERATION}'
[pscustomobject]@{{classification=$result.Classification;stop_calls=$script:stopCalls;remove_calls=$script:removeCalls}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "classification": "identity_changed",
        "stop_calls": 2,
        "remove_calls": 0,
    }


def test_new_current_overlay_after_confirmation_blocks_before_any_stop() -> None:
    body = f"""
$env:BIDKING_PACKAGE_ROOT={_ps_quote(ROOT)}
$env:BIDKING_EMBED_PYTHON={_ps_quote(Path(sys.executable))}
$env:BIDKING_EMBED_LOG_DIR={_ps_quote(ROOT)}
$env:BIDKING_EMBED_ROOT_EXE={_ps_quote(ROOT / 'BidKingLive.exe')}
. {_ps_quote(REPAIR)}
$monitor=[pscustomobject]@{{Kind='current';ProcessId=111;ProcessStartedAt=1.0;RawFingerprint='monitor';Generation='{CURRENT_GENERATION}';PackageRoot={_ps_quote(ROOT)}}}
$absent=[pscustomobject]@{{State='absent';ProcessId=$null;ProcessStartedAt=$null;RawFingerprint=$null;OverlayPath=$null;PackageGenerationIdentity=$null}}
$current=[pscustomobject]@{{State='current';ProcessId=222;ProcessStartedAt=2.0;RawFingerprint='new';OverlayPath='owned-overlay';PackageGenerationIdentity='{CURRENT_GENERATION}'}}
$identity=[pscustomobject]@{{MonitorPath='monitor';LogDir='logs'}}
$initial=[pscustomobject]@{{Classification='current_package_running';Identity=$identity;Monitor=$monitor;Overlay=$absent}}
$fresh=[pscustomobject]@{{Classification='current_package_running';Identity=$identity;Monitor=$monitor;Overlay=$current}}
function Get-BidKingRepairSnapshot {{return $fresh}}
$script:stopCalls=0
function Stop-BidKingVerifiedProcess {{$script:stopCalls+=1;return $true}}
$result=Invoke-BidKingRepairApply -Initial $initial -CurrentGeneration '{CURRENT_GENERATION}'
[pscustomobject]@{{classification=$result.Classification;stop_calls=$script:stopCalls}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body) == {
        "classification": "identity_changed",
        "stop_calls": 0,
    }


@pytest.mark.parametrize("host", _powershell_hosts(), ids=lambda path: path.stem)
@pytest.mark.parametrize(
    "epoch_token",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "1e999",
        "253402300800.0",
        "-1.25",
        "0.0",
        "true",
        "1",
        '"1.25"',
    ],
)
def test_invalid_epoch_tokens_are_unknown_and_never_deleted(
    tmp_path: Path, host: Path, epoch_token: str
) -> None:
    assert _probe_epoch_token(tmp_path, epoch_token, host=host) == {
        "lock_state": "unknown",
        "cleanup": "unknown",
        "lock_exists": True,
        "overlay_state": "unknown",
        "overlay_removal": "unknown",
        "overlay_exists": True,
    }


@pytest.mark.parametrize("host", _powershell_hosts(), ids=lambda path: path.stem)
@pytest.mark.parametrize("epoch_token", ["1.25", "1.25e0"])
def test_finite_decimal_and_double_epochs_keep_stale_cleanup_contract(
    tmp_path: Path, host: Path, epoch_token: str
) -> None:
    assert _probe_epoch_token(tmp_path, epoch_token, host=host) == {
        "lock_state": "stale",
        "cleanup": "reclaimed",
        "lock_exists": False,
        "overlay_state": "stale",
        "overlay_removal": "deleted",
        "overlay_exists": False,
    }


@pytest.mark.parametrize("host", _powershell_hosts(), ids=lambda path: path.stem)
@pytest.mark.parametrize("epoch_token", ["1.25", "1.25e0"])
def test_overlay_ordinary_integer_writer_reaches_epoch_validator_and_deletes_stale(
    tmp_path: Path, host: Path, epoch_token: str
) -> None:
    assert _probe_overlay_integer_tokens(
        tmp_path, host=host, epoch_token=epoch_token
    ) == {
        "state": "stale",
        "process_id": 2_147_483_646,
        "epoch_calls": 1,
        "removal": "deleted",
        "exists": False,
    }


@pytest.mark.parametrize("host", _powershell_hosts(), ids=lambda path: path.stem)
@pytest.mark.parametrize(
    ("field", "token"),
    [
        ("schema", "true"),
        ("schema", "2.0"),
        ("schema", '"2"'),
        ("schema", "null"),
        ("schema", "3"),
        ("pid", "true"),
        ("pid", "2147483646.0"),
        ("pid", '"2147483646"'),
        ("pid", "null"),
        ("pid", "0"),
        ("pid", "-1"),
        ("pid", "2147483648"),
    ],
)
def test_overlay_rejects_noninteger_or_out_of_range_schema_and_pid_before_epoch(
    tmp_path: Path, host: Path, field: str, token: str
) -> None:
    kwargs = {f"{field}_token": token}
    assert _probe_overlay_integer_tokens(tmp_path, host=host, **kwargs) == {
        "state": "unknown",
        "process_id": None,
        "epoch_calls": 0,
        "removal": "unknown",
        "exists": True,
    }


@pytest.mark.parametrize("host", _powershell_hosts(), ids=lambda path: path.stem)
def test_shared_epoch_validator_accepts_only_exact_finite_numeric_types(
    host: Path,
) -> None:
    body = f"""
. {_ps_quote(DISCOVERY)}
[pscustomobject]@{{
  decimal=(Get-BidKingValidatedEpoch -Value ([decimal]1.25)).Valid
  double=(Get-BidKingValidatedEpoch -Value ([double]1.25)).Valid
  nan=(Get-BidKingValidatedEpoch -Value ([double]::NaN)).Valid
  positive_infinity=(Get-BidKingValidatedEpoch -Value ([double]::PositiveInfinity)).Valid
  negative_infinity=(Get-BidKingValidatedEpoch -Value ([double]::NegativeInfinity)).Valid
  boolean=(Get-BidKingValidatedEpoch -Value $true).Valid
  integer=(Get-BidKingValidatedEpoch -Value ([int]1)).Valid
  string=(Get-BidKingValidatedEpoch -Value '1.25').Valid
  beyond_datetime=(Get-BidKingValidatedEpoch -Value ([double]253402300800.0)).Valid
}}|ConvertTo-Json -Compress
"""
    assert _run_powershell(body, host=host) == {
        "decimal": True,
        "double": True,
        "nan": False,
        "positive_infinity": False,
        "negative_infinity": False,
        "boolean": False,
        "integer": False,
        "string": False,
        "beyond_datetime": False,
    }


@pytest.mark.parametrize("host", _powershell_hosts(), ids=lambda path: path.stem)
def test_repair_and_launcher_parse_in_real_powershell(host: Path) -> None:
    body = """
$failed=@()
foreach($path in $env:BIDKING_PARSE_TARGETS -split ';'){
  $tokens=$null;$errors=$null
  [Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)|Out-Null
  if($errors.Count -ne 0){$failed+=$path}
}
if($failed.Count -ne 0){exit 1}
"""
    env = os.environ.copy()
    env["BIDKING_PARSE_TARGETS"] = ";".join(
        str(path) for path in (DISCOVERY, REPAIR, STARTUP, BUILD)
    )
    completed = subprocess.run(
        [str(host), "-NoProfile", "-NonInteractive", "-Command", body],
        env=env,
        check=False,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


def test_repair_entry_is_integrity_gated_sealed_and_package_discoverable() -> None:
    repair = REPAIR.read_text(encoding="utf-8-sig")
    bat_bytes = REPAIR_BAT.read_bytes()
    bat = bat_bytes.decode("ascii")
    build = BUILD.read_text(encoding="utf-8-sig")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    trust = (
        ROOT / "native" / "bidking_trust_bootstrap" / "src" / "main.rs"
    ).read_text(encoding="utf-8")
    lifecycle = (
        ROOT / "native" / "bidking_kernel" / "src" / "client_lifecycle.rs"
    ).read_text(encoding="utf-8")

    assert not bat_bytes.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in bat_bytes
    assert b"\n" not in bat_bytes.replace(b"\r\n", b"")
    assert 'for %%I in ("%TOOL_DIR%..")' in bat
    assert 'start "" /wait "%ROOT_EXE%" --repair %*' in bat
    assert "startup_repair.ps1" not in bat
    assert "verify_local_integrity(root)" in trust
    assert 'elif mode == "repair"' in trust
    assert "launcher.repair_main(forwarded)" in trust
    assert "CLIENT_LIFECYCLE_REPAIR_SEALED" in lifecycle
    assert 'Self::Launch | Self::Repair => "launch"' in lifecycle
    assert "BIDKING_LIFECYCLE_REPAIR_FILE" in build
    assert '--repair $repairScriptPath' in build
    assert 'Join-Path $diagnosticsDir "启动修复.bat"' in build
    assert 'Join-Path $scriptsDst "startup_repair.ps1"' in build
    assert "群发包不得包含明文 startup repair payload" in build
    member = "诊断工具/启动修复.bat"
    assert contract["contract_version"] == 25
    assert member in contract["required_members"]["common"]
    assert contract["fixed_member_sha256"][member] == hashlib.sha256(
        bat_bytes
    ).hexdigest()
    for forbidden in (
        "Get-Process -Name python",
        "Get-Process -Name pythonw",
        "Remove-Item -Recurse",
        "token",
        "certificate",
    ):
        assert forbidden not in repair
    assert "DialogResult]::Yes" in repair
    assert 'return [pscustomobject]@{ Classification = "foreign_or_unknown"' in repair
    assert "Invoke-BidKingTemporaryTrustRecovery" in repair
    assert "$TrustCleanupRequested" in repair


def test_normal_launch_reclaims_dead_lock_before_fast_reuse() -> None:
    startup = STARTUP.read_text(encoding="utf-8-sig")
    repair = REPAIR.read_text(encoding="utf-8-sig")
    cleanup = startup.index("Invoke-BidKingDeadMonitorLockCleanup")
    fast_reuse = startup.index("$FastMonitorIdentity")
    assert cleanup < fast_reuse
    assert startup.count("Invoke-BidKingDeadMonitorLockCleanup") == 2
    assert "phase=pre_start" in startup
    assert "monitor_dead_lock_reclaim action=reclaimed" in startup
    assert 'mode={0}" -f $DeadLockCleanup.Mode' in startup
    assert 'mode={0} phase=pre_start" -f' in startup
    assert "$PreStartDeadCleanup.Mode" in startup
    assert "无法安全删除或隔离" in startup
    assert "启动锁在清理前发生变化" in startup
    assert "启动锁发生变化" not in startup
    assert "诊断工具\\启动修复.bat" in startup
    assert '"dead_lock_quarantined"' in repair
    assert "未能安全删除或隔离启动锁" in repair
    assert "启动锁在修复期间已发生变化" in repair
