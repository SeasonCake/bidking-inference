param(
  [switch]$DryRun
)

# Build-only source for the native sealed startup-repair lifecycle mode.
# The public ZIP contains only 诊断工具\启动修复.bat; this plaintext payload is
# verified, sealed into bidking_kernel, and opened only behind BidKingLive.exe's
# full local-integrity namespace lease.

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"

function ConvertFrom-BidKingExtendedPath([string]$Path) {
  if (-not $Path) { return $Path }
  if ($Path.StartsWith('\\?\UNC\', [System.StringComparison]::OrdinalIgnoreCase)) {
    return '\\' + $Path.Substring(8)
  }
  if ($Path.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) {
    return $Path.Substring(4)
  }
  return $Path
}

$Repo = ConvertFrom-BidKingExtendedPath ([string]$env:BIDKING_PACKAGE_ROOT)
$PythonPath = ConvertFrom-BidKingExtendedPath ([string]$env:BIDKING_EMBED_PYTHON)
$LogPath = ConvertFrom-BidKingExtendedPath ([string]$env:BIDKING_EMBED_LOG_DIR)
$RootExecutable = ConvertFrom-BidKingExtendedPath ([string]$env:BIDKING_EMBED_ROOT_EXE)
$DryRun = $DryRun -or ($env:BIDKING_EMBED_REPAIR_DRY_RUN -eq "1")
$ProcessDiscovery = Join-Path $Repo "scripts\live_process_discovery.ps1"
$GenerationHelper = Join-Path $Repo "scripts\package_generation_identity.py"
$InternetCafeHelper = Join-Path $Repo "scripts\wss_environment_preflight.ps1"
$MonitorPath = Join-Path $Repo "scripts\local_compat_monitor.py"
$OverlayPath = Join-Path $Repo "hero_ref\tools\ahmad_tk_overlay.py"
$MonitorLockPath = Join-Path $LogPath "monitor.lock"
$OverlayStatePath = Join-Path $LogPath "ahmad_overlay.pid"
$StartupSessionPath = Join-Path $LogPath "startup_session.json"
$MonitorLogSessionPath = Join-Path $LogPath "monitor_log_session.json"
$WssHelperStatusPath = Join-Path $LogPath "wss_helper_status.json"
$GameProcessName = "BidKing"
$TrustCleanupRequested = $env:BIDKING_REPAIR_TRUST_CLEANUP -eq "1"

function Show-BidKingRepairMessage {
  param(
    [string]$Text,
    [string]$Title = "BidKing Live · 启动修复",
    [string]$Buttons = "OK",
    [string]$Icon = "Information"
  )
  Add-Type -AssemblyName System.Windows.Forms
  return [System.Windows.Forms.MessageBox]::Show(
    $Text,
    $Title,
    $Buttons,
    $Icon
  )
}

function Confirm-BidKingRepairApply {
  param([string]$Summary)
  $choice = Show-BidKingRepairMessage `
    ($Summary + "`n`n是否执行启动修复？选择「否」或关闭窗口将保持零修改。") `
    "BidKing Live · 确认启动修复" `
    "YesNo" `
    "Warning"
  return $choice -eq [System.Windows.Forms.DialogResult]::Yes
}

function Invoke-BidKingRepairElevation {
  param([bool]$RepairDryRun)
  $arguments = @("--repair")
  if ($RepairDryRun) { $arguments += "--dry-run" }
  $elevated = Start-Process -FilePath $RootExecutable `
    -Verb RunAs `
    -WindowStyle Hidden `
    -WorkingDirectory $Repo `
    -ArgumentList $arguments `
    -Wait `
    -PassThru
  return [int]$elevated.ExitCode
}

function Test-BidKingRepairInputContract {
  return (
    $Repo -and
    $PythonPath -and
    $LogPath -and
    $RootExecutable -and
    (Test-Path -LiteralPath $Repo -PathType Container) -and
    (Test-Path -LiteralPath $PythonPath -PathType Leaf) -and
    (Test-Path -LiteralPath $RootExecutable -PathType Leaf) -and
    (Test-Path -LiteralPath $GenerationHelper -PathType Leaf) -and
    (Test-Path -LiteralPath $InternetCafeHelper -PathType Leaf)
  )
}

function Write-BidKingRepairReceipt {
  param(
    [string]$Classification,
    [string]$Action,
    [bool]$Relieved,
    [string]$NextStep,
    [string]$LockReason = "not_applicable"
  )
  $allowedClassifications = @(
    "no_obstacle", "dead_lock", "current_package_running",
    "verified_old_package_running", "game_active", "foreign_or_unknown",
    "identity_changed", "cleanup_failed", "repaired", "repair_unavailable",
    "wss_same_path_mixed_generation", "wss_stale_product_service_conflict",
    "wss_stale_product_service_repaired_restart_required",
    "driver_load_denied", "component_execution_blocked"
  )
  $allowedActions = @(
    "none", "preserved", "dead_lock_removed", "dead_lock_quarantined",
    "current_package_stopped", "old_package_stopped"
  )
  $allowedNextSteps = @(
    "start_current_package", "close_game_and_retry", "close_old_hero_ref_and_retry",
    "send_diagnostics_privately", "retry_repair",
    "extract_official_zip_to_new_final_directory",
    "restart_windows_then_start_current_package",
    "ask_cafe_admin_to_allow_verified_driver",
    "ask_cafe_admin_to_allow_verified_component"
  )
  $allowedLockReasons = @(
    "not_applicable", "lock_absent", "lock_invalid", "lock_nonregular", "lock_query_unknown",
    "pid_absent", "pid_reused",
    "process_query_unknown", "process_name_mismatch", "pid_epoch_unknown",
    "pid_epoch_mismatch", "context_mismatch", "generation_missing",
    "generation_mismatch", "current"
  )
  if (
    $Classification -notin $allowedClassifications -or
    $Action -notin $allowedActions -or
    $NextStep -notin $allowedNextSteps -or
    $LockReason -notin $allowedLockReasons
  ) {
    throw "repair receipt enum invalid"
  }
  $documents = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::MyDocuments
  )
  if (-not $documents) { throw "documents unavailable" }
  $exports = Join-Path $documents "BidKingLive\exports"
  New-Item -ItemType Directory -Path $exports -Force | Out-Null
  $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss-fff")
  $receiptPath = Join-Path $exports "BidKingStartupRepair-$stamp.txt"
  $body = @(
    "BidKing 启动修复回执",
    "schema_version: 1",
    "recorded_at: $((Get-Date).ToString('o'))",
    "classification: $Classification",
    "action: $Action",
    "lock_reason: $LockReason",
    "obstacle_relieved: $($Relieved.ToString().ToLowerInvariant())",
    "next_step: $NextStep",
    "privacy: fixed-enums-only"
  ) -join [Environment]::NewLine
  [System.IO.File]::WriteAllText(
    $receiptPath,
    $body + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
  )
}

if (-not (Test-Path -LiteralPath $ProcessDiscovery)) {
  throw "repair discovery input missing"
}
. $ProcessDiscovery
. $InternetCafeHelper

function Get-BidKingRepairTrustIdentities([string]$CurrentGeneration) {
  $runtime = Join-Path $Repo "vendor\mitmproxy\mitmdump.exe"
  $descriptor = Join-Path $Repo "data\processed\local_compat_component.json"
  if (-not (Test-Path -LiteralPath $runtime -PathType Leaf) -and
      (Test-Path -LiteralPath $descriptor -PathType Leaf)) {
    try {
      $component = Get-Content -LiteralPath $descriptor -Raw -Encoding utf8 | ConvertFrom-Json
      $identity = "{0}-{1}" -f (
        [string]$component.version
      ), ([string]$component.inventory.inventory_sha256).Substring(0, 16).ToLowerInvariant()
      $runtime = Join-Path (
        Join-Path (
          Join-Path (
            Join-Path $env:LOCALAPPDATA "BidKingLive\components"
          ) ([string]$component.component_id)
        ) $identity
      ) "runtime\mitmdump.exe"
    } catch { return $null }
  }
  if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) { return $null }
  try {
    $runtimeIdentity = (Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash.ToLowerInvariant()
    $componentIdentity = if (Test-Path -LiteralPath $descriptor -PathType Leaf) {
      (Get-FileHash -LiteralPath $descriptor -Algorithm SHA256).Hash.ToLowerInvariant()
    } else { $runtimeIdentity }
    if (
      -not (Test-BidKingSha256 $CurrentGeneration) -or
      -not (Test-BidKingSha256 $runtimeIdentity) -or
      -not (Test-BidKingSha256 $componentIdentity)
    ) { return $null }
    return [pscustomobject]@{
      PackageGenerationIdentity = $CurrentGeneration
      RuntimeIdentity = $runtimeIdentity
      ComponentIdentity = $componentIdentity
    }
  } catch { return $null }
}

function Read-BidKingRepairJsonObject([string]$Path) {
  try {
    $raw = [IO.File]::ReadAllBytes($Path)
    if ($raw.Length -le 0 -or $raw.Length -gt 16384) { return $null }
    $text = [Text.UTF8Encoding]::new($false, $true).GetString($raw)
    Assert-BidKingJsonObjectKeysUnique -Json $text
    return $text | ConvertFrom-Json -ErrorAction Stop
  } catch { return $null }
}

function Get-BidKingCurrentLaunchWssDecision {
  param(
    [string]$CurrentGeneration,
    [object]$TrustIdentities
  )
  if (
    -not $TrustIdentities -or
    [string]$TrustIdentities.PackageGenerationIdentity -cne $CurrentGeneration
  ) { return $null }
  $startup = Read-BidKingRepairJsonObject $StartupSessionPath
  $monitor = Read-BidKingRepairJsonObject $MonitorLogSessionPath
  if (-not $startup -or -not $monitor) { return $null }
  $launchId = [string]$startup.launch_id
  if (
    -not (Test-BidKingGuidText $launchId) -or
    [string]$monitor.launch_id -cne $launchId -or
    -not (Test-BidKingExactJsonInteger $monitor.monitor_pid 1 ([int]::MaxValue))
  ) { return $null }
  $startedAt = $monitor.monitor_started_at
  if ($null -eq $startedAt -or $startedAt -is [bool]) { return $null }
  $startedAtType = $startedAt.GetType()
  if ($startedAtType -notin @([int], [long], [double], [decimal])) { return $null }
  $startedAtSeconds = [double]$startedAt
  if (
    $startedAtSeconds -le 0 -or
    [double]::IsNaN($startedAtSeconds) -or
    [double]::IsInfinity($startedAtSeconds)
  ) { return $null }
  $startedAtMs = [long][Math]::Round($startedAtSeconds * 1000.0)
  if ($startedAtMs -le 0) { return $null }
  try {
    $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  } catch { return $null }
  $classification = Get-BidKingWssHelperFailureClassification `
    -StatusPath $WssHelperStatusPath `
    -ExpectedLaunchId $launchId `
    -ExpectedPackageGenerationIdentity $CurrentGeneration `
    -ExpectedRuntimeIdentity ([string]$TrustIdentities.RuntimeIdentity) `
    -ExpectedComponentIdentity ([string]$TrustIdentities.ComponentIdentity) `
    -ExpectedOwnerSid $ownerSid `
    -ExpectedMonitorProcessId ([int]$monitor.monitor_pid) `
    -ExpectedMonitorStartedAtMs $startedAtMs
  $nextStep = switch ($classification) {
    "wss_same_path_mixed_generation" { "extract_official_zip_to_new_final_directory" }
    "wss_stale_product_service_conflict" { "send_diagnostics_privately" }
    "wss_stale_product_service_repaired_restart_required" { "restart_windows_then_start_current_package" }
    "driver_load_denied" { "ask_cafe_admin_to_allow_verified_driver" }
    "component_execution_blocked" { "ask_cafe_admin_to_allow_verified_component" }
    default { "" }
  }
  if (-not $nextStep) { return $null }
  return [pscustomobject]@{
    Classification = $classification
    Action = "none"
    Relieved = $false
    NextStep = $nextStep
  }
}

function Invoke-BidKingRequestedTrustCleanup {
  param(
    [string]$CurrentGeneration,
    [switch]$Apply,
    [switch]$DryRun
  )
  if ($DryRun -or -not $Apply) {
    return [pscustomobject]@{ State = "dry_run"; Removed = 0; Preserved = 0 }
  }
  $snapshot = Get-BidKingRepairSnapshot -CurrentGeneration $CurrentGeneration
  if ($snapshot.Classification -ne "no_obstacle") {
    return [pscustomobject]@{
      State = $(if ($snapshot.Classification -eq "game_active") { "game_active" } else { "preserved" })
      Removed = 0
      Preserved = 1
    }
  }
  $adapter = New-BidKingInternetCafeProductionAdapter
  # The current package has already passed its full integrity/generation gate.
  # Explicit advanced uninstall may therefore clean an older product journal,
  # but Read-BidKingPrivateLease still requires the exact self-signed product CA
  # identity, protected journal, current SID, no game/monitor, and two store reads.
  return Invoke-BidKingTemporaryTrustRecovery `
    -Adapter $adapter `
    -GameProcessName $GameProcessName `
    -Apply
}

function Get-BidKingCurrentGeneration {
  $output = (& $PythonPath -E -B $GenerationHelper `
    --package-root $Repo `
    --monitor $MonitorPath `
    --runtime (Join-Path (Split-Path -Parent $PythonPath) "pythonw.exe") 2>&1 |
    Out-String)
  $prefix = "BIDKING_PACKAGE_GENERATION_IDENTITY="
  $lines = @(
    $output -split "[`r`n]+" |
      Where-Object { $_.StartsWith($prefix, [System.StringComparison]::Ordinal) }
  )
  if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) { return "" }
  $generation = $lines[0].Substring($prefix.Length)
  if ($generation -cnotmatch '^[0-9a-f]{64}$') { return "" }
  return $generation
}

function Get-BidKingProcessCommandRecord {
  param([int]$ProcessId)
  try {
    $rows = @(
      Get-CimInstance -ClassName Win32_Process `
        -Filter ("ProcessId = {0}" -f $ProcessId) `
        -OperationTimeoutSec 3 `
        -ErrorAction Stop
    )
    if ($rows.Count -ne 1 -or [int]$rows[0].ProcessId -ne $ProcessId) {
      return $null
    }
    if (
      $rows[0].CommandLine -isnot [string] -or
      $rows[0].ExecutablePath -isnot [string]
    ) {
      return $null
    }
    return [pscustomobject]@{
      CommandLine = [string]$rows[0].CommandLine
      ExecutablePath = [string]$rows[0].ExecutablePath
    }
  } catch {
    return $null
  }
}

function Resolve-BidKingPackageRootFromEntrypoint {
  param([string]$Entrypoint)
  $canonical = ConvertTo-BidKingCanonicalPath $Entrypoint
  if (-not $canonical) { return $null }
  $scripts = Split-Path -Parent $canonical
  if (
    -not $scripts -or
    -not [string]::Equals(
      (Split-Path -Leaf $scripts),
      "scripts",
      [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    (Split-Path -Leaf $canonical) -notin @(
      "local_compat_monitor.py", "run_wss_live_monitor.py",
      "run_npcap_live_monitor.py"
    )
  ) {
    return $null
  }
  return ConvertTo-BidKingCanonicalPath (Split-Path -Parent $scripts)
}

function Get-BidKingVerifiedPackageGeneration {
  param(
    [string]$PackageRoot,
    [string]$Entrypoint,
    [string]$RuntimePath
  )
  $oldHelper = Join-Path $PackageRoot "scripts\package_generation_identity.py"
  if (-not (Test-Path -LiteralPath $oldHelper -PathType Leaf)) { return "" }
  $output = (& $PythonPath -E -B $GenerationHelper `
    --package-root $PackageRoot `
    --monitor $Entrypoint `
    --runtime $RuntimePath `
    --identity-helper $oldHelper 2>&1 | Out-String)
  $prefix = "BIDKING_PACKAGE_GENERATION_IDENTITY="
  $lines = @(
    $output -split "[`r`n]+" |
      Where-Object { $_.StartsWith($prefix, [System.StringComparison]::Ordinal) }
  )
  if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) { return "" }
  $generation = $lines[0].Substring($prefix.Length)
  if ($generation -cnotmatch '^[0-9a-f]{64}$') { return "" }
  return $generation
}

function Get-BidKingRepairMonitorOwnership {
  param(
    [object]$InitialIdentity,
    [string]$CurrentGeneration
  )
  if ($InitialIdentity.State -eq "current") {
    $candidate = $InitialIdentity
  } elseif (
    $InitialIdentity.State -eq "foreign" -and
    $InitialIdentity.Reason -eq "context_mismatch" -and
    [string]$InitialIdentity.MonitorPath -and
    [string]$InitialIdentity.LogDir
  ) {
    $candidateGeneration = [string]$InitialIdentity.PackageGenerationIdentity
    if ($candidateGeneration -cnotmatch '^[0-9a-f]{64}$') { return $null }
    $candidate = Get-BidKingMonitorLockIdentity `
      -StatePath $MonitorLockPath `
      -ExpectedMonitorPath ([string]$InitialIdentity.MonitorPath) `
      -ExpectedLogDir ([string]$InitialIdentity.LogDir) `
      -ExpectedPackageGenerationIdentity $candidateGeneration
    if ($candidate.State -ne "current") { return $null }
  } else {
    return $null
  }

  if (-not (Test-BidKingSamePath ([string]$candidate.LogDir) $LogPath)) {
    return $null
  }
  $record = Get-BidKingProcessCommandRecord -ProcessId ([int]$candidate.ProcessId)
  if (-not $record) { return $null }
  $arguments = @(Split-BidKingCommandLine $record.CommandLine)
  $entrypointIndex = Get-BidKingPythonEntrypointIndex -Arguments $arguments
  if ($entrypointIndex -lt 0) { return $null }
  $entrypoint = ConvertTo-BidKingCanonicalPath $arguments[$entrypointIndex]
  $runtimePath = ConvertTo-BidKingCanonicalPath $arguments[0]
  if (
    -not $entrypoint -or
    -not $runtimePath -or
    -not (Test-BidKingSamePath $entrypoint ([string]$candidate.MonitorPath)) -or
    -not (Test-BidKingSamePath $runtimePath $record.ExecutablePath)
  ) {
    return $null
  }
  $packageRoot = Resolve-BidKingPackageRootFromEntrypoint $entrypoint
  if (-not $packageRoot) { return $null }
  $expectedRuntime = Join-Path $packageRoot "runtime\python\pythonw.exe"
  if (-not (Test-BidKingSamePath $runtimePath $expectedRuntime)) { return $null }
  $generation = [string]$candidate.PackageGenerationState
  $lockGeneration = [string]$candidate.PackageGenerationIdentity
  if (
    $generation -ne "current" -or
    (Get-BidKingMonitorCommandGenerationState `
      -CommandLine $record.CommandLine `
      -ExpectedMonitorPath $entrypoint `
      -ExpectedLogDir $LogPath `
      -ExpectedPackageGenerationIdentity $lockGeneration) -ne "current"
  ) {
    return $null
  }
  $recomputed = Get-BidKingVerifiedPackageGeneration `
    -PackageRoot $packageRoot `
    -Entrypoint $entrypoint `
    -RuntimePath $runtimePath
  if (-not $recomputed -or $recomputed -cne $lockGeneration) { return $null }
  $kind = if (
    (Test-BidKingSamePath $packageRoot $Repo) -and
    $lockGeneration -ceq $CurrentGeneration
  ) { "current" } elseif (
    -not (Test-BidKingSamePath $packageRoot $Repo)
  ) { "old" } else { "" }
  if (-not $kind) { return $null }
  return [pscustomobject]@{
    Kind = $kind
    ProcessId = [int]$candidate.ProcessId
    ProcessStartedAt = [double]$candidate.ProcessStartedAt
    RawFingerprint = [string]$candidate.RawFingerprint
    PackageRoot = $packageRoot
    Generation = $lockGeneration
  }
}

function Get-BidKingRepairOverlayOwnership {
  param(
    [object]$MonitorOwnership
  )
  $result = [ordered]@{
    State = "absent"
    ProcessId = $null
    ProcessStartedAt = $null
    RawFingerprint = $null
    OverlayPath = $null
    PackageGenerationIdentity = $null
  }
  if (-not (Test-Path -LiteralPath $OverlayStatePath -PathType Leaf)) {
    return [pscustomobject]$result
  }
  $result.State = "unknown"
  try {
    $raw = [System.IO.File]::ReadAllBytes($OverlayStatePath)
    if ($raw.Length -le 0 -or $raw.Length -gt 65536) { return [pscustomobject]$result }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
      $result.RawFingerprint = [BitConverter]::ToString(
        $sha.ComputeHash($raw)
      ).Replace("-", "").ToLowerInvariant()
    } finally { $sha.Dispose() }
    $text = [System.Text.UTF8Encoding]::new($false, $true).GetString($raw).Trim()
    Assert-BidKingJsonObjectKeysUnique -Json $text
    $payload = $text | ConvertFrom-Json -ErrorAction Stop
    foreach ($field in @(
      "schema_version", "pid", "started_at", "kind", "overlay_path",
      "package_generation_identity"
    )) {
      if ($payload.PSObject.Properties.Name -notcontains $field) {
        return [pscustomobject]$result
      }
    }
    if ($null -eq $payload.schema_version -or $null -eq $payload.pid) {
      return [pscustomobject]$result
    }
    $schemaVersionType = $payload.schema_version.GetType()
    $processIdType = $payload.pid.GetType()
    if (
      ($schemaVersionType -ne [int] -and $schemaVersionType -ne [long]) -or
      ($processIdType -ne [int] -and $processIdType -ne [long]) -or
      $payload.kind -isnot [string] -or
      $payload.overlay_path -isnot [string] -or
      $payload.package_generation_identity -isnot [string] -or
      [string]$payload.kind -cne "ahmad_overlay"
    ) { return [pscustomobject]$result }
    $schemaVersionLong = [long]$payload.schema_version
    $processIdLong = [long]$payload.pid
    if (
      $schemaVersionLong -ne 2 -or
      $processIdLong -le 0 -or
      $processIdLong -gt [int]::MaxValue
    ) { return [pscustomobject]$result }
    $epoch = Get-BidKingValidatedEpoch -Value $payload.started_at
    if (-not $epoch.Valid) { return [pscustomobject]$result }
    $processId = [int]$processIdLong
    $result.ProcessId = $processId
    $result.ProcessStartedAt = [double]$epoch.Seconds
    $result.OverlayPath = [string]$payload.overlay_path
    $result.PackageGenerationIdentity = [string]$payload.package_generation_identity
    $expectedOverlay = Join-Path $MonitorOwnership.PackageRoot "hero_ref\tools\ahmad_tk_overlay.py"
    if (
      -not (Test-BidKingSamePath ([string]$payload.overlay_path) $expectedOverlay) -or
      [string]$payload.package_generation_identity -cne $MonitorOwnership.Generation
    ) { return [pscustomobject]$result }
    try {
      $process = Get-Process -Id $processId -ErrorAction Stop
    } catch {
      if ([string]$_.FullyQualifiedErrorId -like "NoProcessFoundForGivenId,*") {
        $result.State = "stale"
      }
      return [pscustomobject]$result
    }
    if ($process.ProcessName -notlike "python*") { return [pscustomobject]$result }
    try {
      $expected = $epoch.UtcDateTime
      if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $expected).TotalSeconds) -gt 2.0) {
        return [pscustomobject]$result
      }
    } catch { return [pscustomobject]$result }
    $record = Get-BidKingProcessCommandRecord -ProcessId $processId
    if (-not $record) { return [pscustomobject]$result }
    $arguments = @(Split-BidKingCommandLine $record.CommandLine)
    $entrypointIndex = Get-BidKingPythonEntrypointIndex -Arguments $arguments
    if (
      $entrypointIndex -lt 0 -or
      -not (Test-BidKingSamePath $arguments[$entrypointIndex] $expectedOverlay) -or
      -not (Test-BidKingSamePath $arguments[0] (Join-Path $MonitorOwnership.PackageRoot "runtime\python\pythonw.exe")) -or
      -not (Test-BidKingSamePath $arguments[0] $record.ExecutablePath)
    ) { return [pscustomobject]$result }
    $result.State = "current"
    return [pscustomobject]$result
  } catch {
    return [pscustomobject]$result
  }
}

function Get-BidKingGameState {
  try {
    $processes = @(Get-Process -Name $GameProcessName -ErrorAction Stop)
    if ($processes.Count -gt 0) { return "alive" }
    return "unknown"
  } catch {
    if ([string]$_.FullyQualifiedErrorId -like "NoProcessFoundForGivenName,*") {
      return "absent"
    }
    return "unknown"
  }
}

function Get-BidKingProcessEpochState {
  param([int]$ProcessId, [double]$StartedAt)
  try {
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
  } catch {
    if ([string]$_.FullyQualifiedErrorId -like "NoProcessFoundForGivenId,*") {
      return [pscustomobject]@{ State = "absent"; Process = $null }
    }
    return [pscustomobject]@{ State = "unknown"; Process = $null }
  }
  try {
    $expected = [DateTimeOffset]::FromUnixTimeMilliseconds(
      [long][Math]::Round($StartedAt * 1000.0)
    ).UtcDateTime
    if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $expected).TotalSeconds) -gt 2.0) {
      return [pscustomobject]@{ State = "mismatch"; Process = $null }
    }
    return [pscustomobject]@{ State = "alive"; Process = $process }
  } catch {
    return [pscustomobject]@{ State = "unknown"; Process = $null }
  }
}

function Stop-BidKingVerifiedProcess {
  param([int]$ProcessId, [double]$StartedAt)
  $probe = Get-BidKingProcessEpochState $ProcessId $StartedAt
  if ($probe.State -eq "absent") { return $true }
  if ($probe.State -ne "alive") { return $false }
  $null = $probe.Process.CloseMainWindow()
  $deadline = (Get-Date).AddSeconds(2)
  while ((Get-Date) -lt $deadline) {
    $probe = Get-BidKingProcessEpochState $ProcessId $StartedAt
    if ($probe.State -eq "absent") { return $true }
    if ($probe.State -ne "alive") { return $false }
    Start-Sleep -Milliseconds 100
  }
  $probe = Get-BidKingProcessEpochState $ProcessId $StartedAt
  if ($probe.State -eq "absent") { return $true }
  if ($probe.State -ne "alive") { return $false }
  Stop-Process -Id $ProcessId -Force -ErrorAction Stop
  $deadline = (Get-Date).AddSeconds(8)
  while ((Get-Date) -lt $deadline) {
    $probe = Get-BidKingProcessEpochState $ProcessId $StartedAt
    if ($probe.State -eq "absent") { return $true }
    if ($probe.State -ne "alive") { return $false }
    Start-Sleep -Milliseconds 100
  }
  $probe = Get-BidKingProcessEpochState $ProcessId $StartedAt
  return $probe.State -eq "absent"
}

function Get-BidKingRepairSnapshot {
  param([string]$CurrentGeneration)
  $identity = Get-BidKingMonitorLockIdentity `
    -StatePath $MonitorLockPath `
    -ExpectedMonitorPath $MonitorPath `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $CurrentGeneration
  if ($identity.State -eq "absent") {
    return [pscustomobject]@{ Classification = "no_obstacle"; Identity = $identity; Monitor = $null; Overlay = $null }
  }
  if ($identity.State -eq "stale") {
    return [pscustomobject]@{ Classification = "dead_lock"; Identity = $identity; Monitor = $null; Overlay = $null }
  }
  $monitor = Get-BidKingRepairMonitorOwnership `
    -InitialIdentity $identity `
    -CurrentGeneration $CurrentGeneration
  if (-not $monitor) {
    return [pscustomobject]@{ Classification = "foreign_or_unknown"; Identity = $identity; Monitor = $null; Overlay = $null }
  }
  $overlay = Get-BidKingRepairOverlayOwnership -MonitorOwnership $monitor
  if ($overlay.State -eq "unknown") {
    return [pscustomobject]@{ Classification = "foreign_or_unknown"; Identity = $identity; Monitor = $monitor; Overlay = $overlay }
  }
  $gameState = Get-BidKingGameState
  if ($gameState -eq "alive") {
    return [pscustomobject]@{ Classification = "game_active"; Identity = $identity; Monitor = $monitor; Overlay = $overlay }
  }
  if ($gameState -ne "absent") {
    return [pscustomobject]@{ Classification = "foreign_or_unknown"; Identity = $identity; Monitor = $monitor; Overlay = $overlay }
  }
  $classification = if ($monitor.Kind -eq "current") {
    "current_package_running"
  } else {
    "verified_old_package_running"
  }
  return [pscustomobject]@{ Classification = $classification; Identity = $identity; Monitor = $monitor; Overlay = $overlay }
}

function Test-BidKingRepairSnapshotStable {
  param([object]$Left, [object]$Right)
  if ($Left.Classification -cne $Right.Classification) { return $false }
  if ($Left.Classification -eq "dead_lock") {
    return (
      $Left.Identity.Reason -ceq $Right.Identity.Reason -and
      $Left.Identity.ActualProcessStartedAt -eq $Right.Identity.ActualProcessStartedAt -and
      $Left.Identity.ProcessName -ceq $Right.Identity.ProcessName -and
      [string]$Left.Identity.RawFingerprint -ceq [string]$Right.Identity.RawFingerprint -and
      [int]$Left.Identity.ProcessId -eq [int]$Right.Identity.ProcessId -and
      [double]$Left.Identity.ProcessStartedAt -eq [double]$Right.Identity.ProcessStartedAt
    )
  }
  if ($Left.Monitor) {
    if (
      [int]$Left.Monitor.ProcessId -ne [int]$Right.Monitor.ProcessId -or
      [double]$Left.Monitor.ProcessStartedAt -ne [double]$Right.Monitor.ProcessStartedAt -or
      [string]$Left.Monitor.RawFingerprint -cne [string]$Right.Monitor.RawFingerprint
    ) { return $false }
  }
  if ([bool]$Left.Overlay -ne [bool]$Right.Overlay) { return $false }
  if ($Left.Overlay) {
    if (
      [string]$Left.Overlay.State -cne [string]$Right.Overlay.State -or
      [int]$Left.Overlay.ProcessId -ne [int]$Right.Overlay.ProcessId -or
      [double]$Left.Overlay.ProcessStartedAt -ne [double]$Right.Overlay.ProcessStartedAt -or
      [string]$Left.Overlay.RawFingerprint -cne [string]$Right.Overlay.RawFingerprint -or
      [string]$Left.Overlay.OverlayPath -cne [string]$Right.Overlay.OverlayPath -or
      [string]$Left.Overlay.PackageGenerationIdentity -cne `
        [string]$Right.Overlay.PackageGenerationIdentity
    ) { return $false }
  }
  return $true
}

function Test-BidKingRepairOverlayPostStopStable {
  param([object]$Initial, [object]$Fresh)
  if (-not $Initial -or -not $Fresh) { return $false }
  if ($Initial.State -eq "absent") { return $Fresh.State -eq "absent" }
  if ($Initial.State -eq "current" -and $Fresh.State -eq "absent") { return $true }
  if ($Initial.State -notin @("current", "stale") -or $Fresh.State -ne "stale") {
    return $false
  }
  return (
    [int]$Initial.ProcessId -eq [int]$Fresh.ProcessId -and
    [double]$Initial.ProcessStartedAt -eq [double]$Fresh.ProcessStartedAt -and
    [string]$Initial.RawFingerprint -ceq [string]$Fresh.RawFingerprint -and
    [string]$Initial.OverlayPath -ceq [string]$Fresh.OverlayPath -and
    [string]$Initial.PackageGenerationIdentity -ceq `
      [string]$Fresh.PackageGenerationIdentity
  )
}

function Invoke-BidKingRepairApply {
  param([object]$Initial, [string]$CurrentGeneration)
  $current = Get-BidKingRepairSnapshot -CurrentGeneration $CurrentGeneration
  if (-not (Test-BidKingRepairSnapshotStable $Initial $current)) {
    return [pscustomobject]@{ Classification = "identity_changed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
  }
  if ($current.Classification -eq "dead_lock") {
    $cleanup = Invoke-BidKingDeadMonitorLockCleanup `
      -StatePath $MonitorLockPath `
      -ExpectedMonitorPath $MonitorPath `
      -ExpectedLogDir $LogPath `
      -ExpectedPackageGenerationIdentity $CurrentGeneration `
      -ExpectedIdentity $current.Identity
    if (-not $cleanup.Succeeded) {
      $classification = if ($cleanup.State -eq "changed") {
        "identity_changed"
      } else {
        "cleanup_failed"
      }
      return [pscustomobject]@{ Classification = $classification; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
    }
    if ((Get-BidKingMonitorLockPathState -StatePath $MonitorLockPath) -ne "absent") {
      return [pscustomobject]@{ Classification = "identity_changed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
    }
    $action = if ($cleanup.State -eq "quarantined") {
      "dead_lock_quarantined"
    } else {
      "dead_lock_removed"
    }
    return [pscustomobject]@{ Classification = "repaired"; Action = $action; Relieved = $true; NextStep = "start_current_package" }
  }
  if ($current.Classification -notin @("current_package_running", "verified_old_package_running")) {
    return [pscustomobject]@{ Classification = $current.Classification; Action = "preserved"; Relieved = $false; NextStep = $(if ($current.Classification -eq "game_active") { "close_game_and_retry" } else { "send_diagnostics_privately" }) }
  }
  $gameState = Get-BidKingGameState
  if ($gameState -eq "alive") {
    return [pscustomobject]@{ Classification = "game_active"; Action = "preserved"; Relieved = $false; NextStep = "close_game_and_retry" }
  }
  if ($gameState -ne "absent") {
    return [pscustomobject]@{ Classification = "foreign_or_unknown"; Action = "preserved"; Relieved = $false; NextStep = "send_diagnostics_privately" }
  }
  if ($current.Overlay.State -eq "current") {
    if (-not (Stop-BidKingVerifiedProcess `
      -ProcessId ([int]$current.Overlay.ProcessId) `
      -StartedAt ([double]$current.Overlay.ProcessStartedAt))) {
      return [pscustomobject]@{ Classification = "cleanup_failed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
    }
  }
  $overlayPostStop = Get-BidKingRepairOverlayOwnership `
    -MonitorOwnership $current.Monitor
  if (-not (Test-BidKingRepairOverlayPostStopStable `
    -Initial $current.Overlay `
    -Fresh $overlayPostStop)) {
    return [pscustomobject]@{ Classification = "identity_changed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
  }
  $gameState = Get-BidKingGameState
  if ($gameState -eq "alive") {
    return [pscustomobject]@{ Classification = "game_active"; Action = "preserved"; Relieved = $false; NextStep = "close_game_and_retry" }
  }
  if ($gameState -ne "absent") {
    return [pscustomobject]@{ Classification = "foreign_or_unknown"; Action = "preserved"; Relieved = $false; NextStep = "send_diagnostics_privately" }
  }
  if (-not (Stop-BidKingVerifiedProcess `
    -ProcessId ([int]$current.Monitor.ProcessId) `
    -StartedAt ([double]$current.Monitor.ProcessStartedAt))) {
    return [pscustomobject]@{ Classification = "cleanup_failed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
  }
  $lockPost = Get-BidKingMonitorLockIdentity `
    -StatePath $MonitorLockPath `
    -ExpectedMonitorPath ([string]$current.Identity.MonitorPath) `
    -ExpectedLogDir ([string]$current.Identity.LogDir) `
    -ExpectedPackageGenerationIdentity ([string]$current.Monitor.Generation)
  if ($lockPost.State -eq "stale") {
    if (-not [string]::Equals(
      [string]$lockPost.RawFingerprint,
      [string]$current.Monitor.RawFingerprint,
      [System.StringComparison]::Ordinal
    )) {
      return [pscustomobject]@{ Classification = "identity_changed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
    }
    $removed = Remove-BidKingMonitorLockIfFingerprint `
      -StatePath $MonitorLockPath `
      -Identity $lockPost
    if ($removed -notin @("absent", "deleted")) {
      return [pscustomobject]@{ Classification = "cleanup_failed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
    }
  } elseif ($lockPost.State -ne "absent") {
    return [pscustomobject]@{ Classification = "identity_changed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
  }
  $overlayPreDelete = Get-BidKingRepairOverlayOwnership `
    -MonitorOwnership $current.Monitor
  if (-not (Test-BidKingRepairOverlayPostStopStable `
    -Initial $overlayPostStop `
    -Fresh $overlayPreDelete)) {
    return [pscustomobject]@{ Classification = "identity_changed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
  }
  if ($overlayPreDelete.State -eq "stale") {
    $overlayRemoval = Remove-BidKingMonitorLockIfFingerprint `
      -StatePath $OverlayStatePath `
      -Identity $overlayPreDelete
    if ($overlayRemoval -notin @("absent", "deleted")) {
      return [pscustomobject]@{ Classification = "cleanup_failed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
    }
    $overlayPostDelete = Get-BidKingRepairOverlayOwnership `
      -MonitorOwnership $current.Monitor
    if ($overlayPostDelete.State -ne "absent") {
      return [pscustomobject]@{ Classification = "identity_changed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
    }
  }
  $post = Get-BidKingRepairSnapshot -CurrentGeneration $CurrentGeneration
  if ($post.Classification -ne "no_obstacle") {
    return [pscustomobject]@{ Classification = "cleanup_failed"; Action = "preserved"; Relieved = $false; NextStep = "retry_repair" }
  }
  $action = if ($current.Monitor.Kind -eq "current") {
    "current_package_stopped"
  } else {
    "old_package_stopped"
  }
  return [pscustomobject]@{ Classification = "repaired"; Action = $action; Relieved = $true; NextStep = "start_current_package" }
}

function Invoke-BidKingStartupRepair {
  try {
    if (-not (Test-BidKingRepairInputContract)) {
      throw "repair input unavailable"
    }
    if ($TrustCleanupRequested -and $DryRun) {
      $trustReceipt = Get-BidKingInternetCafeResult "ready" "trust_cleanup_dry_run"
      $trustReceipt.mutation_performed = $false
      try { $null = Write-BidKingInternetCafeReceipt $trustReceipt } catch {}
      $null = Show-BidKingRepairMessage (
        "已完成本地兼容证书清理预检；DryRun 不会恢复、清理或删除证书与状态。" +
        "`n`n如需支持，请运行「收集诊断.bat」并私发脱敏报告。"
      ) "BidKing Live · 本地兼容证书"
      return 0
    }
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
      [Security.Principal.WindowsBuiltinRole]::Administrator
    )
    if (-not $isAdmin) {
      return Invoke-BidKingRepairElevation -RepairDryRun ([bool]$DryRun)
    }
    $generation = Get-BidKingCurrentGeneration
    if (-not $generation) { throw "generation unavailable" }
    if ($TrustCleanupRequested) {
      if ($DryRun) { return 0 }
      $trustResult = Invoke-BidKingRequestedTrustCleanup `
        -CurrentGeneration $generation `
        -Apply
      $trustClassification = switch ($trustResult.State) {
        { $_ -in @("cleaned", "absent") } { "ready"; break }
        "game_active" { "foreign_cert_or_state"; break }
        "identity_unavailable" { "unknown"; break }
        default { "foreign_cert_or_state" }
      }
      $trustReceipt = Get-BidKingInternetCafeResult $trustClassification "trust_cleanup"
      $trustReceipt.mutation_performed = [int]$trustResult.Removed -gt 0
      try { $null = Write-BidKingInternetCafeReceipt $trustReceipt } catch {}
      $trustText = switch ($trustResult.State) {
        "cleaned" { "exact 产品兼容证书已安全移除；下次启动需要重新完成一次产品兼容初始化。" }
        "absent" { "未发现由产品私有 journal 登记的 exact 兼容证书，无需移除。" }
        "game_active" { "游戏仍在运行。为避免中断当前连接，本次未移除证书信任。" }
        default { "证书或运行身份无法完整复核，本次已保留现场，不会删除其他证书。" }
      }
      $null = Show-BidKingRepairMessage (
        $trustText + "`n`n如需支持，请运行「收集诊断.bat」并私发脱敏报告。"
      ) "BidKing Live · 本地兼容证书"
      return $(if ($trustResult.State -in @("cleaned", "absent")) { 0 } else { 1 })
    }
    $trustIdentities = Get-BidKingRepairTrustIdentities $generation
    $wssDecision = Get-BidKingCurrentLaunchWssDecision `
      -CurrentGeneration $generation `
      -TrustIdentities $trustIdentities
    if ($wssDecision) {
      Write-BidKingRepairReceipt `
        -Classification $wssDecision.Classification `
        -Action $wssDecision.Action `
        -Relieved:$false `
        -NextStep $wssDecision.NextStep
      $wssSummary = switch ($wssDecision.Classification) {
        "wss_same_path_mixed_generation" {
          "当前产品服务与本包 driver 指向同一绝对目录，但同次启动无法证明该目录只含当前代文件。为避免误删，本次不会停止、删除或重配任何服务。`n`n请完全退出 Hero Ref 和游戏，把官方 ZIP 完整解压到一个新的最终目录；先不要删除旧目录，也不要在首次启动后移动或改名新目录。然后从新目录启动，按提示完成旧服务的精确修复。"
        }
        "wss_stale_product_service_conflict" {
          "同次启动确认产品兼容服务身份冲突，但未达到安全删除条件。本次不会停止、删除或重配任何服务。请保留现场并运行收集诊断，私发给支持人员；不要处理名为 WinDivert 的其他服务。"
        }
        "wss_stale_product_service_repaired_restart_required" {
          "同次启动已对完整验证的旧版产品服务提交精确删除。旧 driver 或句柄仍可能存在，请先重启 Windows，再从当前最终目录启动；重启前本次不会继续启动链。"
        }
        "driver_load_denied" {
          "同次启动在加载已验证 driver 时被权限或系统策略拒绝。本次不会更改服务、证书、代理、路由或安全软件设置；请让网吧管理员允许已验证 driver 后重试。"
        }
        default {
          "同次启动的已验证本地兼容组件被系统策略阻止。本次不会更改服务、证书、代理、路由或安全软件设置；请让网吧管理员允许已验证组件后重试。"
        }
      }
      $null = Show-BidKingRepairMessage (
        $wssSummary + "`n`n已生成脱敏回执：Windows 文档\BidKingLive\exports。"
      )
      return 0
    }
    $snapshot = Get-BidKingRepairSnapshot -CurrentGeneration $generation
    $nextStep = switch ($snapshot.Classification) {
      "no_obstacle" { "start_current_package" }
      "game_active" { "close_game_and_retry" }
      "foreign_or_unknown" { "send_diagnostics_privately" }
      default { "close_old_hero_ref_and_retry" }
    }
    Write-BidKingRepairReceipt `
      -Classification $snapshot.Classification `
      -Action "none" `
      -Relieved:$false `
      -NextStep $nextStep `
      -LockReason $snapshot.Identity.Reason
    $summary = switch ($snapshot.Classification) {
      "no_obstacle" { "未发现会阻断启动的后台锁或进程。可以直接启动当前版本。" }
      "dead_lock" { "发现已退出后台留下的启动锁。可安全删除该单个锁文件，不会终止进程。" }
      "current_package_running" { "发现当前版本完整验证的后台读取进程。修复会先尝试正常退出，必要时再安全停止。" }
      "verified_old_package_running" { "发现来自旧目录、且完整验证属于 BidKing 的后台读取进程。修复会先尝试正常退出，必要时再安全停止。" }
      "game_active" { "游戏仍在运行。为避免中断本局，本次不会清理或停止任何进程。" }
      default { "后台身份无法完整证明。本次不会清理锁，也不会终止任何进程。" }
    }
    if ($DryRun -or $snapshot.Classification -in @("no_obstacle", "game_active", "foreign_or_unknown")) {
      $summary += "`n`n已生成脱敏回执：Windows 文档\BidKingLive\exports。"
      $null = Show-BidKingRepairMessage $summary
      return $(if ($DryRun -or $snapshot.Classification -eq "no_obstacle") { 0 } else { 1 })
    }
    if (-not (Confirm-BidKingRepairApply -Summary $summary)) {
      Write-BidKingRepairReceipt `
        -Classification $snapshot.Classification `
        -Action "preserved" `
        -Relieved:$false `
        -NextStep "close_old_hero_ref_and_retry" `
        -LockReason $snapshot.Identity.Reason
      return 0
    }
    $result = Invoke-BidKingRepairApply `
      -Initial $snapshot `
      -CurrentGeneration $generation
    Write-BidKingRepairReceipt `
      -Classification $result.Classification `
      -Action $result.Action `
      -Relieved $result.Relieved `
      -NextStep $result.NextStep `
      -LockReason $snapshot.Identity.Reason
    $resultText = switch ($result.Classification) {
      "repaired" {
        "启动障碍已安全解除。请关闭此提示后，再启动当前版本。"
      }
      "identity_changed" {
        "启动锁在修复期间已发生变化，本次已安全停止。请完全退出所有 Hero Ref 后重试。"
      }
      "cleanup_failed" {
        "未能安全删除或隔离启动锁，本次已安全停止。请重启 Windows 后再试。"
      }
      default {
        "未能在身份保持稳定的前提下解除障碍。本次已停止，不会继续清理或终止其他进程。"
      }
    }
    $null = Show-BidKingRepairMessage (
      $resultText + "`n`n已生成脱敏回执：Windows 文档\BidKingLive\exports。"
    )
    return $(if ($result.Relieved) { 0 } else { 1 })
  } catch {
    try {
      Write-BidKingRepairReceipt `
        -Classification "repair_unavailable" `
        -Action "preserved" `
        -Relieved:$false `
        -NextStep "send_diagnostics_privately"
    } catch {}
    $null = Show-BidKingRepairMessage `
      "启动修复未完成，且未执行清理。请运行「收集诊断.bat」，将脱敏报告私发给支持人员。" `
      "BidKing Live · 启动修复未完成" `
      "OK" `
      "Error"
    return 1
  }
}

if ($MyInvocation.InvocationName -ne '.') {
  exit (Invoke-BidKingStartupRepair)
}
