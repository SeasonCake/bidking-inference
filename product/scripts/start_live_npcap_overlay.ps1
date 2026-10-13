param(
  [string]$PythonPath = "C:\Python313\python.exe",
  [string]$LogDir = "data\logs\live",
  [string]$ProcessName = "BidKing.exe",
  [int[]]$ServerPort = @(10000),
  [ValidateSet("engineering", "portable", "public-safe", "stable")]
  [string]$DiagnosticProfile = "portable",
  [switch]$KeepMonitorOnClose,
  [switch]$NoOverlay,
  [switch]$NoAutoElevate,
  [switch]$ManagedLaunch,
  [switch]$Restart,
  [switch]$NoTaskbar,
  [ValidateSet("auto", "wss", "npcap")]
  [string]$CaptureMode = "auto"
)

# One-click: start the live monitor + the new (ahmad_tk) overlay together.
# 0.3.0 defaults to process-local WSS capture because the official 2026-07-23
# client moved the unchanged application frames behind TLS.  The old Npcap path
# remains an explicit rollback arm (BIDKING_DISABLE_WSS_CAPTURE=1).

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"

$script:BidKingLaunchState = @{
  Succeeded = $false
  FailureCode = "unknown"
  MessageKey = "startup_failed"
  PreserveChildren = $false
  Monitor = $null
  Overlay = $null
  MonitorState = "not_created"
  OverlayState = "preserved_unknown"
}

function New-BidKingOwnedStartupProcess($Process, [string]$Kind, [string]$EntryPath) {
  # Only the object returned by this invocation's Start-Process enters this set.
  # Reused PID files/discovery results never manufacture creation ownership.
  $Record = @{
    Process = $Process; Kind = $Kind; EntryPath = $EntryPath
    PackageRoot = $Repo; Generation = $ExpectedPackageGenerationIdentity
    LaunchId = $LaunchId; ProcessId = 0; StartedAtTicks = [long]0; Handle = $null
  }
  try {
    $Record.ProcessId = [int]$Process.Id
    $Record.Handle = $Process.SafeHandle
    $Record.StartedAtTicks = $Process.StartTime.ToUniversalTime().Ticks
  } catch {}
  return $Record
}

function Get-BidKingOwnedStartupProcessState($Record) {
  if (-not $Record) { return "not_created" }
  try {
    $ExpectedEntry = if ($Record.Kind -ceq "monitor") { $Monitor } else { $Overlay }
    if (
      $Record.Kind -cnotin @("monitor", "overlay") -or
      -not $Record.LaunchId -or $Record.LaunchId -cne $LaunchId -or
      $Record.Generation -cnotmatch '^[0-9a-f]{64}$' -or
      $Record.Generation -cne $ExpectedPackageGenerationIdentity -or
      $Record.PackageRoot -cne $Repo -or $Record.EntryPath -cne $ExpectedEntry -or
      $Record.ProcessId -le 0 -or $Record.StartedAtTicks -le 0 -or
      -not $Record.Handle -or $Record.Handle.IsClosed -or $Record.Handle.IsInvalid
    ) { return "preserved_unknown" }
    # The retained Process/SafeHandle is the original kernel process object, not
    # a later Get-Process result for a potentially recycled numeric PID.
    $Record.Process.Refresh()
    if ($Record.Process.HasExited) { return "exited" }
    if (
      [int]$Record.Process.Id -ne $Record.ProcessId -or
      $Record.Process.StartTime.ToUniversalTime().Ticks -ne $Record.StartedAtTicks
    ) { return "preserved_changed" }
    return "alive"
  } catch { return "preserved_unknown" }
}

function Get-BidKingFailureCleanupGameState {
  try {
    $Name = [IO.Path]::GetFileNameWithoutExtension($ProcessName)
    if (-not $Name) { return "unknown" }
    $Processes = @(Get-Process -Name $Name -ErrorAction Stop)
    if ($Processes.Count -gt 0) { return "active" }
    return "unknown"
  } catch {
    if ([string]$_.FullyQualifiedErrorId -like "NoProcessFoundForGivenName,*") {
      return "absent"
    }
    return "unknown"
  }
}

function Stop-BidKingOwnedStartupProcess($Record) {
  $State = Get-BidKingOwnedStartupProcessState $Record
  if ($State -ne "alive") { return $State }
  if ($script:BidKingLaunchState.PreserveChildren) { return "preserved_unknown" }
  if ($UseWssCapture -and (Get-BidKingFailureCleanupGameState) -ne "absent") {
    return "preserved_game"
  }
  try {
    # Re-prove both identities immediately before each process operation.
    $State = Get-BidKingOwnedStartupProcessState $Record
    if ($State -ne "alive") { return $State }
    if ($UseWssCapture -and (Get-BidKingFailureCleanupGameState) -ne "absent") {
      return "preserved_game"
    }
    $State = Get-BidKingOwnedStartupProcessState $Record
    if ($State -ne "alive") { return $State }
    if (Get-Command Invalidate-BidKingProcessInventory -ErrorAction SilentlyContinue) {
      Invalidate-BidKingProcessInventory -Reason "process_stop"
    }
    $null = $Record.Process.CloseMainWindow()
    if ($Record.Process.WaitForExit(2000)) { return "reclaimed" }
    $State = Get-BidKingOwnedStartupProcessState $Record
    if ($State -ne "alive") { return $State }
    if ($UseWssCapture -and (Get-BidKingFailureCleanupGameState) -ne "absent") {
      return "preserved_game"
    }
    $State = Get-BidKingOwnedStartupProcessState $Record
    if ($State -ne "alive") { return $State }
    # Never use Stop-Process by name/PID or terminate a parent process tree.
    $Record.Process.Kill()
    if ($Record.Process.WaitForExit(5000)) { return "reclaimed" }
    return "cleanup_failed"
  } catch { return "cleanup_failed" } finally {
    if (Get-Command Invalidate-BidKingProcessInventory -ErrorAction SilentlyContinue) {
      Invalidate-BidKingProcessInventory -Reason "process_stop"
    }
  }
}

function Complete-BidKingStartupAttempt {
  $State = $script:BidKingLaunchState
  if (-not $State.Succeeded) {
    # A missing owned record is not evidence that no existing UI depends on the
    # monitor.  Require the existing discovery/creation path to prove absence,
    # or the retained original process object to prove exit.
    $OverlayDependency = if ($State.Overlay) {
      Get-BidKingOwnedStartupProcessState $State.Overlay
    } else { [string]$State.OverlayState }
    if ($OverlayDependency -cnotin @("not_created", "exited")) {
      $State.PreserveChildren = $true
    }
    # Reverse creation order.  Do not delete a lock whose owner has not exited;
    # the existing exact dead-lock path may reclaim it on a later launch.
    foreach ($Kind in @("Overlay", "Monitor")) {
      if ($Kind -ceq "Monitor" -and $State.OverlayState -cnotin @("not_created", "exited")) {
        $State.PreserveChildren = $true
      }
      if ($State[$Kind]) {
        $State[$Kind + "State"] = Stop-BidKingOwnedStartupProcess $State[$Kind]
      }
    }
    $null = Write-BidKingStartupTiming "startup_terminal" "failed"
  }
  if ($ManagedLaunch) {
    try {
      $RunId = [string]$env:BIDKING_STARTUP_TIMING_RUN_ID
      $Directory = [string]$env:BIDKING_STARTUP_TIMING_DIR
      if ($RunId -cmatch '^[0-9a-f]{32}$' -and [IO.Path]::IsPathRooted($Directory)) {
        $Receipt = [ordered]@{
          schema = "bidking_startup_cleanup_v1"; run_id = $RunId
          phase = "powershell"; succeeded = [bool]$State.Succeeded
          failure_code = [string]$State.FailureCode; message_key = [string]$State.MessageKey
          monitor = [string]$State.MonitorState; overlay = [string]$State.OverlayState
        } | ConvertTo-Json -Compress
        if ([Text.Encoding]::UTF8.GetByteCount($Receipt) -le 4096) {
          $Path = Join-Path $Directory ($RunId + "--cleanup-powershell.json")
          [IO.File]::WriteAllText($Path, $Receipt, [Text.UTF8Encoding]::new($false))
        }
      }
    } catch {} # Observability never grants kill/restore authority or changes exit.
  }
  foreach ($Kind in @("Overlay", "Monitor")) {
    if ($State[$Kind]) {
      try { $State[$Kind].Process.Dispose() } catch {}
    }
  }
}

function Write-BidKingStartupTiming {
  param(
    [Parameter(Mandatory = $true)][string]$Event,
    [Parameter(Mandatory = $true)][string]$Result,
    [string]$Topology = ""
  )
  $Events = @(
    "root_entry", "preload", "python_spawn", "kernel_import",
    "kernel_manifest", "kernel_coverage", "kernel_namespace", "acl_inventory",
    "acl_journal", "acl_apply", "acl_attest", "integrity_recheck", "lifecycle",
    "powershell_spawn", "powershell_launch", "powershell_generation",
    "powershell_component", "powershell_certificate", "powershell_dependency",
    "powershell_process_identity", "powershell_monitor", "powershell_overlay",
    "tk_map", "tk_after_idle", "startup_guard", "startup_terminal",
    "acl_restore", "root_exit"
  )
  $Results = @("begin", "ok", "failed", "skipped", "unavailable")
  $Topologies = @(
    "unknown", "cold_start", "current_current", "current_absent",
    "current_stale", "current_unsafe", "rapid_reentry", "diagnostics", "repair"
  )
  $Temporary = $null
  try {
    if ($Events -cnotcontains $Event -or $Results -cnotcontains $Result) { return $false }
    if (-not $Topology) { $Topology = [string]$env:BIDKING_STARTUP_TIMING_TOPOLOGY }
    if ($Topologies -cnotcontains $Topology) { $Topology = "unknown" }
    $RunId = [string]$env:BIDKING_STARTUP_TIMING_RUN_ID
    $Directory = [string]$env:BIDKING_STARTUP_TIMING_DIR
    [long]$BaseTick = 0
    if (
      $RunId -cnotmatch '^[0-9a-f]{32}$' -or
      -not [long]::TryParse(
        [string]$env:BIDKING_STARTUP_TIMING_BASE_TICK_MS,
        [Globalization.NumberStyles]::None,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$BaseTick
      ) -or
      -not [IO.Path]::IsPathRooted($Directory)
    ) { return $false }
    # Windows PowerShell 5.1 does not expose Environment.TickCount64 on every
    # supported .NET Framework build.  Its signed 32-bit TickCount is the low
    # word of the same monotonic system clock, so modulo subtraction remains
    # exact inside the seven-day receipt lifetime, including one wrap.
    [long]$TickModulus = 4294967296
    [long]$BaseLow = $BaseTick % $TickModulus
    [long]$CurrentLow = [Environment]::TickCount
    if ($CurrentLow -lt 0) { $CurrentLow += $TickModulus }
    [long]$Elapsed = if ($CurrentLow -ge $BaseLow) {
      $CurrentLow - $BaseLow
    } else {
      $TickModulus - $BaseLow + $CurrentLow
    }
    if ($Elapsed -gt 604800000) { return $false }
    [IO.Directory]::CreateDirectory($Directory) | Out-Null
    $Payload = [ordered]@{
      schema = "bidking_startup_timing_v1"
      schema_version = 1
      run_id = $RunId
      event = $Event
      result = $Result
      topology = $Topology
      elapsed_ms = $Elapsed
    } | ConvertTo-Json -Compress
    if ([Text.Encoding]::UTF8.GetByteCount($Payload) -gt 1024) { return $false }
    $Final = Join-Path $Directory ("{0}--{1}--{2}.json" -f $RunId, $Event, $Result)
    $Temporary = Join-Path $Directory (
      ".{0}--{1}--{2}--{3}.tmp" -f $RunId, $Event, $Result, [guid]::NewGuid().ToString("N")
    )
    [IO.File]::WriteAllText($Temporary, $Payload, [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($Temporary, $Final)
    return $true
  } catch {
    if ($Temporary -and (Test-Path -LiteralPath $Temporary -PathType Leaf)) {
      Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
    }
    return $false
  }
}

$null = Write-BidKingStartupTiming "powershell_launch" "begin"

function ConvertFrom-WindowsExtendedPath([string]$Path) {
  if (-not $Path) { return $Path }
  if ($Path.StartsWith('\\?\UNC\', [System.StringComparison]::OrdinalIgnoreCase)) {
    return '\\' + $Path.Substring(8)
  }
  if ($Path.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) {
    return $Path.Substring(4)
  }
  return $Path
}
$Repo = if ($env:BIDKING_PACKAGE_ROOT) {
  [System.IO.Path]::GetFullPath($env:BIDKING_PACKAGE_ROOT)
} elseif ($MyInvocation.MyCommand.Path) {
  Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
} else {
  [System.IO.Path]::GetFullPath([System.IO.Directory]::GetCurrentDirectory())
}
$Repo = ConvertFrom-WindowsExtendedPath $Repo
$PythonPath = ConvertFrom-WindowsExtendedPath $PythonPath
$LogDir = ConvertFrom-WindowsExtendedPath $LogDir
$LabRoot = Join-Path $Repo "hero_ref"
$NpcapMonitor = Join-Path $Repo "scripts\run_npcap_live_monitor.py"
$PackagedLocalMonitor = Join-Path $Repo "scripts\local_compat_monitor.py"
$PackagedLocalAddon = Join-Path $Repo "scripts\local_compat_addon.py"
$WssMonitor = if (Test-Path -LiteralPath $PackagedLocalMonitor -PathType Leaf) {
  $PackagedLocalMonitor
} else {
  Join-Path $Repo "scripts\run_wss_live_monitor.py"
}
$WssAddon = if (Test-Path -LiteralPath $PackagedLocalAddon -PathType Leaf) {
  $PackagedLocalAddon
} else {
  Join-Path $Repo "scripts\mitm_bidking_wss_addon.py"
}
$RuntimeComponentBootstrap = Join-Path $Repo "scripts\local_compat_component.py"
$RuntimeComponentDescriptor = Join-Path $Repo "data\processed\local_compat_component.json"
$InternetCafeHelper = Join-Path $Repo "scripts\wss_environment_preflight.ps1"
$UseWssCapture = (
  $CaptureMode -eq "wss" -or
  ($CaptureMode -eq "auto" -and $env:BIDKING_DISABLE_WSS_CAPTURE -ne "1")
)
$Monitor = if ($UseWssCapture) { $WssMonitor } else { $NpcapMonitor }
$Overlay = Join-Path $LabRoot "tools\ahmad_tk_overlay.py"
$LogPath = if ([System.IO.Path]::IsPathRooted($LogDir)) {
  [System.IO.Path]::GetFullPath($LogDir)
} else {
  Join-Path $Repo $LogDir
}
$LockPath = Join-Path $LogPath "monitor.lock"
$Snapshot = Join-Path $LogPath "latest_snapshot.json"
$MonitorOut = Join-Path $LogPath "monitor.stdout.log"
$MonitorErr = Join-Path $LogPath "monitor.stderr.log"
$OverlayPidPath = Join-Path $LogPath "ahmad_overlay.pid"
$LifecyclePolicyPath = Join-Path $LogPath "lifecycle_policy.json"
$OverlayErr = Join-Path $LogPath "overlay.stderr.log"
$StartupStatus = Join-Path $LogPath "startup_status.log"
$StartupSession = Join-Path $LogPath "startup_session.json"
$MonitorLogSession = Join-Path $LogPath "monitor_log_session.json"
$OverlayLogSession = Join-Path $LogPath "overlay_log_session.json"
$NetworkConnectionStatus = Join-Path $LogPath "network_connection_status.json"
$WssHelperStatus = Join-Path $LogPath "wss_helper_status.json"
$ProcessDiscovery = Join-Path $Repo "scripts\live_process_discovery.ps1"
$PackageIdentityHelper = Join-Path $Repo "scripts\package_generation_identity.py"
$AllowEngineeringRuntime = (
  $DiagnosticProfile -eq "engineering" -and
  -not $env:BIDKING_PACKAGE_ROOT -and
  (Test-Path -LiteralPath (Join-Path $Repo ".git"))
)

# Quote a path before it goes into a Start-Process -ArgumentList ARRAY. The array form joins elements
# with spaces but does NOT quote them, so a path with a space ("C:\bid king\...") splits into broken
# argv and the child silently fails (verified: an unquoted spaced/中文 path mis-splits Python sys.argv).
# Wrapping each path element in double quotes preserves spaces AND Chinese intact -> works from any
# folder/drive/desktop, no more "请用无空格英文路径" reminder. Drive/desktop already worked ($Repo is
# absolute via $MyInvocation); this closes the space/中文 gap.
function Q([string]$p) { return '"' + $p + '"' }

function Resolve-BidKingWindowedPythonPath {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [switch]$AllowConsoleFallback
  )
  $RuntimeParent = Split-Path -Parent $PythonPath
  if (-not $RuntimeParent) { return $null }
  $WindowedPython = Join-Path $RuntimeParent "pythonw.exe"
  if (Test-Path -LiteralPath $WindowedPython -PathType Leaf) {
    return $WindowedPython
  }
  if (
    $AllowConsoleFallback -and
    (Test-Path -LiteralPath $PythonPath -PathType Leaf)
  ) {
    return $PythonPath
  }
  return $null
}

function Resolve-WssMitmdumpPath(
  [string]$PackageRoot,
  [bool]$AllowEngineeringRuntime,
  [string]$PythonPath = ""
) {
  $script:BidKingLastComponentFailure = "unknown"
  $PackagedRuntime = Join-Path $PackageRoot "vendor\mitmproxy\mitmdump.exe"
  $PackagedRuntimeRoot = Split-Path -Parent $PackagedRuntime
  $PackagedRuntimeNeedsCleanCopy = $false
  if (Test-Path -LiteralPath $PackagedRuntime -PathType Leaf) {
    foreach ($RelativeComponent in @(
      "mitmdump.exe",
      "_internal\mitmproxy_windows\windows-redirector.exe",
      "_internal\mitmproxy_windows\WinDivert64.sys"
    )) {
      $ComponentMember = Join-Path $PackagedRuntimeRoot $RelativeComponent
      if (
        (Test-Path -LiteralPath $ComponentMember -PathType Leaf) -and
        $null -ne (
          Get-Item -LiteralPath $ComponentMember `
            -Stream Zone.Identifier -ErrorAction SilentlyContinue
        )
      ) {
        $PackagedRuntimeNeedsCleanCopy = $true
        break
      }
    }
    if (-not $PackagedRuntimeNeedsCleanCopy) {
      $script:BidKingLastComponentFailure = "ready"
      return [System.IO.Path]::GetFullPath($PackagedRuntime)
    }
  }
  $ComponentBootstrap = Join-Path $PackageRoot "scripts\local_compat_component.py"
  $ComponentDescriptor = Join-Path $PackageRoot "data\processed\local_compat_component.json"
  $BundledRuntimeManifest = Join-Path $PackageRoot "scripts\mitmproxy_runtime_manifest.json"
  $ComponentArguments = @()
  if ($PackagedRuntimeNeedsCleanCopy) {
    $ComponentArguments = @(
      "--bundled-root", $PackagedRuntimeRoot,
      "--bundled-manifest", $BundledRuntimeManifest,
      "--cache-root", (Join-Path $env:LOCALAPPDATA "BidKingLive\components")
    )
  } elseif (Test-Path -LiteralPath $ComponentDescriptor -PathType Leaf) {
    $ComponentArguments = @("--descriptor", $ComponentDescriptor)
  }
  if (
    $PythonPath -and
    (Test-Path -LiteralPath $PythonPath -PathType Leaf) -and
    (Test-Path -LiteralPath $ComponentBootstrap -PathType Leaf) -and
    $ComponentArguments.Count -gt 0 -and
    (-not $PackagedRuntimeNeedsCleanCopy -or (
      Test-Path -LiteralPath $BundledRuntimeManifest -PathType Leaf
    ))
  ) {
    $PreviousEap = $ErrorActionPreference
    try {
      $ErrorActionPreference = "Continue"
      $ComponentOutput = (
        & $PythonPath -E -B $ComponentBootstrap @ComponentArguments 2>&1 |
          Out-String
      )
      $ComponentExit = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $PreviousEap
    }
    if ($ComponentExit -eq 0) {
      $ReadyPrefix = "BIDKING_COMPONENT_READY="
      $ReadyLine = @(
        $ComponentOutput -split "[`r`n]+" |
          Where-Object { $_.StartsWith($ReadyPrefix, [System.StringComparison]::Ordinal) }
      ) | Select-Object -Last 1
      if ($ReadyLine) {
        $RuntimeRoot = $ReadyLine.Substring($ReadyPrefix.Length)
        $ResolvedRuntime = Join-Path $RuntimeRoot "mitmdump.exe"
        $CacheRoot = [System.IO.Path]::GetFullPath(
          (Join-Path $env:LOCALAPPDATA "BidKingLive\components")
        ).TrimEnd("\") + "\"
        $ResolvedRuntimeFull = [System.IO.Path]::GetFullPath($ResolvedRuntime)
        if (
          $ResolvedRuntimeFull.StartsWith(
            $CacheRoot,
            [System.StringComparison]::OrdinalIgnoreCase
          ) -and
          (Test-Path -LiteralPath $ResolvedRuntimeFull -PathType Leaf)
        ) {
          $script:BidKingLastComponentFailure = "ready"
          return $ResolvedRuntimeFull
        }
      }
    } else {
      $FailurePrefix = "BIDKING_COMPONENT_FAILURE="
      $FailureLine = @(
        $ComponentOutput -split "[`r`n]+" |
          Where-Object { $_.StartsWith($FailurePrefix, [System.StringComparison]::Ordinal) }
      ) | Select-Object -Last 1
      if ($FailureLine) {
        $FailureClass = $FailureLine.Substring($FailurePrefix.Length)
        if ($FailureClass -in @("component_write_denied", "component_execution_blocked")) {
          $script:BidKingLastComponentFailure = $FailureClass
        }
      }
    }
  }
  if ($AllowEngineeringRuntime) {
    $EngineeringRuntime = Join-Path $PackageRoot "scripts\pkg_assets\mitmproxy\mitmdump.exe"
    if (Test-Path -LiteralPath $EngineeringRuntime -PathType Leaf) {
      $script:BidKingLastComponentFailure = "ready"
      return [System.IO.Path]::GetFullPath($EngineeringRuntime)
    }
  }
  return $null
}

function Get-RuntimeComponentInstallPrompt([string]$DescriptorPath) {
  if (-not (Test-Path -LiteralPath $DescriptorPath -PathType Leaf)) {
    return $null
  }
  try {
    $Descriptor = Get-Content -LiteralPath $DescriptorPath -Raw -Encoding utf8 |
      ConvertFrom-Json
    $InventorySha = [string]$Descriptor.inventory.inventory_sha256
    $Identity = "{0}-{1}" -f (
      [string]$Descriptor.version
    ), $InventorySha.Substring(0, 16).ToLowerInvariant()
    $ComponentRoot = Join-Path (
      Join-Path (
        Join-Path $env:LOCALAPPDATA "BidKingLive\components"
      ) ([string]$Descriptor.component_id)
    ) $Identity
    if (Test-Path -LiteralPath (Join-Path $ComponentRoot "ready.json") -PathType Leaf) {
      return $null
    }
    $ArchiveMiB = [Math]::Ceiling(([double]$Descriptor.archive.bytes) / 1MB)
    return "首次使用需要下载约 $ArchiveMiB MB 的本地兼容组件。下载完成后会自动校验并缓存，后续版本无需重复下载。`n`n点击【确定】继续；点击【取消】退出。"
  } catch {
    return "首次使用需要下载并校验本地兼容组件。下载完成后会自动缓存，后续版本无需重复下载。`n`n点击【确定】继续；点击【取消】退出。"
  }
}

function Get-CompatibilityGuidancePrompt {
  return @"
首次使用或遇到“能进游戏但不计算”时，请按顺序操作：

1. 完整解压程序包，不要在 ZIP 内直接运行。
2. 将整个文件夹（解压后的 Hero Ref/BidKingLive）加入火绒或 360 的信任区/白名单。
3. 完整退出火绒、360、游戏加速器；仅关闭主窗口可能仍有后台防护或服务。
4. 先启动 Hero Ref，等待悬浮窗出现，再启动游戏。
5. 仍不计算时按同一顺序重启，再收集脱敏诊断私发；不要反复更换 token。

不要卸载或永久关闭安全软件。测试后可恢复防护；若恢复后再次失效，请保持安全设置并等待兼容更新。
Hero Ref 不会自动修改安全软件、Windows 安全功能、代理、路由或 DNS。

已完成上述准备请点【确定】；尚未完成请点【取消】退出。
"@
}

function Get-CompatibilityGuidanceMarkerPath {
  $LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
  if (-not $LocalAppData) {
    return $null
  }
  return (Join-Path $LocalAppData "BidKingLive\compatibility_guidance_v1.seen")
}

function Read-CompatibilityGuidanceMarkerBytes([string]$MarkerPath) {
  return [System.IO.File]::ReadAllBytes($MarkerPath)
}

function Test-CompatibilityGuidanceMarker([string]$MarkerPath) {
  if (-not $MarkerPath) {
    return $false
  }
  try {
    if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) {
      return $false
    }
    $MarkerItem = Get-Item -LiteralPath $MarkerPath -Force -ErrorAction Stop
    if (
      ($MarkerItem.Attributes -band [System.IO.FileAttributes]::Directory) -ne 0 -or
      ($MarkerItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
      return $false
    }
    [byte[]]$Actual = Read-CompatibilityGuidanceMarkerBytes $MarkerPath
    [byte[]]$Expected = [System.Text.Encoding]::UTF8.GetBytes(
      "compatibility_guidance_v1"
    )
    if ($Actual.Length -ne $Expected.Length) {
      return $false
    }
    for ($Index = 0; $Index -lt $Expected.Length; $Index += 1) {
      if ($Actual[$Index] -ne $Expected[$Index]) {
        return $false
      }
    }
    return $true
  } catch {
    return $false
  }
}

function Invoke-CompatibilityGuidanceMessageBox {
  return [System.Windows.Forms.MessageBox]::Show(
    (Get-CompatibilityGuidancePrompt),
    "BidKing Live · 首次兼容设置",
    "OKCancel",
    "Information"
  )
}

function Show-FirstCompatibilityGuidancePrompt {
  Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
  return (Invoke-CompatibilityGuidanceMessageBox)
}

function Write-CompatibilityGuidanceMarker([string]$MarkerPath) {
  $MarkerDir = Split-Path -Parent $MarkerPath
  New-Item -ItemType Directory -Path $MarkerDir -Force | Out-Null
  [System.IO.File]::WriteAllBytes(
    $MarkerPath,
    [System.Text.Encoding]::UTF8.GetBytes("compatibility_guidance_v1")
  )
}

function Confirm-FirstCompatibilityGuidance {
  $Marker = Get-CompatibilityGuidanceMarkerPath
  if (Test-CompatibilityGuidanceMarker $Marker) {
    return $true
  }
  try {
    $Result = Show-FirstCompatibilityGuidancePrompt
    if ([string]$Result -cne "OK") {
      return $false
    }
  } catch {
    Write-Host "无法显示首次兼容设置确认，本次启动已取消。" -ForegroundColor Red
    return $false
  }
  if ($Marker) {
    try {
      Write-CompatibilityGuidanceMarker $Marker
    } catch {
      # The user confirmed the steps, so this launch may continue. Since no valid
      # marker was persisted, the next launch will ask again instead of failing open.
    }
  }
  return $true
}

try {
# --- 系统兼容性预检(老机器友好提示, 而非静默闪退 / 误报成 Npcap 问题) ---
# 内嵌 Python 3.13 需 Windows 10+(已弃 Win7/8/8.1); 包仅 64 位。低版本 / 32 位 / runtime 缺失时
# python.exe 根本起不来 → monitor 秒退 → 会被下面"抓包未就绪"兜底误报, 把用户引去反复重装 Npcap。
# 故在任何启动 / 提权之前就显式判 OS / 位数 / python 存在性, 给出精确原因。控制台被壳隐藏, 用 MessageBox。
function Show-FatalBox([string]$Msg, [string]$Title) {
  if ($ManagedLaunch) {
    # Only fixed message categories cross the supervisor boundary.  Dynamic
    # paths, native error bodies and credentials never become receipt/UI input.
    $script:BidKingLaunchState.MessageKey = switch ($Title) {
      "BidKing Live · 系统版本过低" { "unsupported_system" }
      "BidKing Live · 需 64 位系统" { "unsupported_system" }
      "BidKing Live · 运行时缺失" { "runtime_missing" }
      "BidKing Live · 无控制台运行时缺失" { "runtime_missing" }
      "BidKing Live · 启动组件缺失" { "component_missing" }
      "BidKing Live · 安装包不完整" { "component_missing" }
      "BidKing Live · 运行环境自检未通过" { "runtime_probe_failed" }
      "BidKing Live · 请先关闭游戏" { "close_game_first" }
      "BidKing Live · 启动锁已变化" { "lock_changed" }
      "BidKing Live · 启动锁清理未完成" { "lock_cleanup_failed" }
      default { "startup_failed" }
    }
    return
  }
  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    [System.Windows.Forms.MessageBox]::Show($Msg, $Title, "OK", "Error") | Out-Null
  } catch { Write-Host "$Title`n$Msg" -ForegroundColor Red } finally {
    if (Get-Command Invalidate-BidKingProcessInventory -ErrorAction SilentlyContinue) {
      Invalidate-BidKingProcessInventory -Reason "dialog"
    }
  }
}
$OsVer = [Environment]::OSVersion.Version
if ($OsVer.Major -lt 10) {
  Show-FatalBox "BidKing Live 需要 Windows 10 或更高版本(64 位)。`n`n当前系统版本过低: 内嵌的 Python 3.13 已不再支持 Windows 7 / 8 / 8.1。`n请在 Windows 10 / 11(64 位)上运行。" "BidKing Live · 系统版本过低"
  exit 1
}
if (-not [Environment]::Is64BitOperatingSystem) {
  Show-FatalBox "BidKing Live 仅支持 64 位 Windows。`n`n检测到当前是 32 位系统, 无法运行。" "BidKing Live · 需 64 位系统"
  exit 1
}
if (-not (Test-Path $PythonPath)) {
  Show-FatalBox "未找到运行时 Python:`n$PythonPath`n`n通常是压缩包没有完整解压, 或 runtime 目录被杀毒软件删除了。`n请:`n  1) 重新【完整解压】整个压缩包(不要在压缩包里直接双击运行);`n  2) 把本程序所在文件夹加入杀软白名单后重试。" "BidKing Live · 运行时缺失"
  exit 1
}
$MonitorPythonPath = Resolve-BidKingWindowedPythonPath `
  -PythonPath $PythonPath `
  -AllowConsoleFallback:$AllowEngineeringRuntime
if (-not $MonitorPythonPath) {
  Show-FatalBox "未找到无控制台运行时 Python:`n$(Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe')`n`n当前正式程序包不完整，继续启动会显示黑色控制台窗口。请重新完整解压当前版本安装包，把程序文件夹加入杀毒软件白名单后重试。" "BidKing Live · 无控制台运行时缺失"
  exit 1
}

$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltinRole]::Administrator
)

function Get-CurrentPowerShellPath {
  try {
    $Current = Get-Process -Id $PID -ErrorAction Stop
    if ($Current.Path) { return $Current.Path }
  } catch {}
  $Pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($Pwsh) { return $Pwsh.Source }
  $PowerShell = Get-Command powershell -ErrorAction SilentlyContinue
  if ($PowerShell) { return $PowerShell.Source }
  throw "PowerShell executable not found for elevation"
}

if (-not $IsAdmin -and -not $NoAutoElevate) {
  $ElevatedArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Q $PSCommandPath),
    "-PythonPath", (Q $PythonPath), "-LogDir", (Q $LogDir), "-ProcessName", $ProcessName,
    "-DiagnosticProfile", $DiagnosticProfile, "-CaptureMode", $CaptureMode, "-NoAutoElevate"
  )
  foreach ($p in $ServerPort) { $ElevatedArgs += @("-ServerPort", "$p") }
  if ($KeepMonitorOnClose) { $ElevatedArgs += "-KeepMonitorOnClose" }
  if ($NoOverlay) { $ElevatedArgs += "-NoOverlay" }
  if ($Restart) { $ElevatedArgs += "-Restart" }
  if ($NoTaskbar) { $ElevatedArgs += "-NoTaskbar" }  # propagate through the elevated relaunch
  $PowerShellPath = Get-CurrentPowerShellPath
  Start-Process -FilePath $PowerShellPath -Verb RunAs -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList $ElevatedArgs
  Write-Host "已请求管理员授权，请按提示继续。" -ForegroundColor Yellow
  return
}

if ($UseWssCapture -and -not (Confirm-FirstCompatibilityGuidance)) {
  exit 3
}

New-Item -ItemType Directory -Path $LogPath -Force | Out-Null
$LaunchStartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$LaunchStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$LaunchId = [guid]::NewGuid().ToString()
if ($UseWssCapture) {
  $LifecyclePolicyTmp = "$LifecyclePolicyPath.tmp-$PID"
  $LifecyclePolicy = @{
    schema_version = 1
    auto_stop_when_idle = (-not $NoOverlay -and -not $KeepMonitorOnClose)
    launch_id = $LaunchId
    updated_at = $LaunchStartedAt
  } | ConvertTo-Json -Compress
  [System.IO.File]::WriteAllText(
    $LifecyclePolicyTmp,
    $LifecyclePolicy,
    (New-Object System.Text.UTF8Encoding($false))
  )
  Move-Item -LiteralPath $LifecyclePolicyTmp -Destination $LifecyclePolicyPath -Force
}
$LaunchState = @{
  launch_id = $LaunchId
  started_at = $LaunchStartedAt
  diagnostic_profile = $DiagnosticProfile
  restart = [bool]$Restart
  launcher_pid = [int]$PID
  launcher_elevation_state = if ($IsAdmin) { "elevated" } else { "not_elevated" }
} | ConvertTo-Json -Compress
$LaunchStateTmp = "$StartupSession.tmp-$PID"
[System.IO.File]::WriteAllText($LaunchStateTmp, $LaunchState, (New-Object System.Text.UTF8Encoding($false)))
Move-Item -LiteralPath $LaunchStateTmp -Destination $StartupSession -Force
Add-Content -Path $StartupStatus -Value (
  "{0} launch_begin launch_id={1} started_at={2} profile={3} restart={4}" -f `
    (Get-Date -Format o), $LaunchId, $LaunchStartedAt, $DiagnosticProfile, [bool]$Restart
) -Encoding utf8
if (-not (Test-Path -LiteralPath $ProcessDiscovery)) {
  $missingMessage = "启动组件缺失:{0}{1}{0}{0}请重新完整解压当前版本安装包后重试。" -f [Environment]::NewLine, $ProcessDiscovery
  Show-FatalBox $missingMessage "BidKing Live · 启动组件缺失"
  exit 1
}
. $ProcessDiscovery
Reset-BidKingProcessDiscoveryState

function Get-BidKingCurrentOverlayIdentity {
  param(
    [string]$StatePath,
    [string]$ExpectedOverlayPath,
    [string]$ExpectedPackageGenerationIdentity
  )
  $Result = [ordered]@{
    State = "absent"
    ProcessId = $null
    ProcessStartedAt = $null
  }
  if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
    return [pscustomobject]$Result
  }
  $Result.State = "unknown"
  try {
    [byte[]]$RawBytes = [System.IO.File]::ReadAllBytes($StatePath)
    if ($RawBytes.Length -le 0 -or $RawBytes.Length -gt 4096) {
      return [pscustomobject]$Result
    }
    $StrictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $Raw = $StrictUtf8.GetString($RawBytes).Trim()
    Assert-BidKingJsonObjectKeysUnique -Json $Raw
    $Payload = $Raw | ConvertFrom-Json -ErrorAction Stop
    foreach ($Field in @(
      "schema_version", "pid", "started_at", "kind", "overlay_path",
      "package_generation_identity"
    )) {
      if ($Payload.PSObject.Properties.Name -notcontains $Field) {
        return [pscustomobject]$Result
      }
    }
    $ProcessId = [int]$Payload.pid
    $ProcessStartedAt = [double]$Payload.started_at
    $Result.ProcessId = $ProcessId
    $Result.ProcessStartedAt = $ProcessStartedAt
    if (
      [int]$Payload.schema_version -ne 2 -or
      $ProcessId -le 0 -or
      $ProcessStartedAt -le 0 -or
      [string]$Payload.kind -cne "ahmad_overlay" -or
      -not (Test-BidKingSamePath ([string]$Payload.overlay_path) $ExpectedOverlayPath) -or
      $ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$' -or
      -not [string]::Equals(
        [string]$Payload.package_generation_identity,
        $ExpectedPackageGenerationIdentity,
        [System.StringComparison]::Ordinal
      )
    ) {
      $Result.State = "foreign"
      return [pscustomobject]$Result
    }
    try {
      $Process = Get-Process -Id $ProcessId -ErrorAction Stop
    } catch {
      $FullyQualifiedErrorId = [string]$_.FullyQualifiedErrorId
      if ($FullyQualifiedErrorId.StartsWith(
        "NoProcessFoundForGivenId,",
        [System.StringComparison]::Ordinal
      )) {
        $Result.State = "stale"
      }
      return [pscustomobject]$Result
    }
    if ($Process.ProcessName -notlike "python*") {
      $Result.State = "foreign"
      return [pscustomobject]$Result
    }
    try {
      $ExpectedStartedAt = [DateTimeOffset]::FromUnixTimeMilliseconds(
        [long][Math]::Round($ProcessStartedAt * 1000.0)
      ).UtcDateTime
      $ActualStartedAt = $Process.StartTime.ToUniversalTime()
    } catch {
      return [pscustomobject]$Result
    }
    if ([Math]::Abs(($ActualStartedAt - $ExpectedStartedAt).TotalSeconds) -gt 2.0) {
      $Result.State = "foreign"
      return [pscustomobject]$Result
    }
    $Result.State = "current"
    return [pscustomobject]$Result
  } catch {
    return [pscustomobject]$Result
  }
}

function Write-BidKingStartupFailure([string]$Code, [string]$Detail = "") {
  $null = Write-BidKingStartupTiming "startup_terminal" "failed"
  $Allowed = @(
    "package_generation_identity_component_missing",
    "package_generation_identity_unavailable",
    "monitor_foreign_lock",
    "monitor_identity_unknown",
    "monitor_old_package_game_active",
    "monitor_old_package_changed",
    "monitor_old_package_stop_failed",
    "monitor_old_package_lock_changed",
    "monitor_old_package_conflict",
    "monitor_foreign_process",
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
    "wss_same_path_mixed_generation",
    "wss_stale_product_service_conflict",
    "wss_stale_product_service_repaired_restart_required",
    "user_cancelled",
    "overlay_spawn_failed",
    "overlay_exception",
    "overlay_exited_cleanly",
    "monitor_exit",
    "process_query_unknown",
    "unknown"
  )
  if ($Allowed -cnotcontains $Code) {
    throw "invalid startup failure classification"
  }
  $ExitCode = switch ($Code) {
    "monitor_foreign_lock" { 180 }
    "monitor_identity_unknown" { 181 }
    "monitor_old_package_game_active" { 182 }
    "monitor_old_package_changed" { 183 }
    "monitor_old_package_stop_failed" { 184 }
    "monitor_old_package_lock_changed" { 185 }
    "monitor_old_package_conflict" { 186 }
    "monitor_foreign_process" { 187 }
    "package_generation_identity_component_missing" { 188 }
    "package_generation_identity_unavailable" { 189 }
    "cert_store_write_denied" { 190 }
    "policy_locked" { 191 }
    "profile_ephemeral" { 192 }
    "uac_disabled" { 193 }
    "component_write_denied" { 194 }
    "restore_disk_suspected" { 195 }
    "interactive_user_mismatch" { 196 }
    "component_execution_blocked" { 197 }
    "driver_load_denied" { 198 }
    "package_root_unsupported" { 199 }
    "foreign_cert_or_state" { 200 }
    "user_cancelled" { 201 }
    "unknown" { 202 }
    "wss_same_path_mixed_generation" { 203 }
    "wss_stale_product_service_conflict" { 204 }
    "wss_stale_product_service_repaired_restart_required" { 205 }
    "overlay_spawn_failed" { 206 }
    "overlay_exception" { 207 }
    "overlay_exited_cleanly" { 208 }
    "monitor_exit" { 209 }
    "process_query_unknown" { 210 }
  }
  $script:BidKingLaunchState.FailureCode = $Code
  $Event = "startup_failure code={0}" -f $Code
  if ($Detail) { $Event += " detail={0}" -f $Detail }
  Write-BidKingStartupStatus -StatusPath $StartupStatus -Event $Event
  [Console]::Out.WriteLine("BIDKING_STARTUP_FAILURE={0}" -f $Code)
  return [int]$ExitCode
}

function Show-BidKingInternetCafeFailure([string]$Classification) {
  $Body = switch ($Classification) {
    "cert_store_write_denied" { "当前用户的证书信任区拒绝写入。请联系网吧管理员允许 CurrentUser 证书信任，或在支持的个人电脑上使用。" }
    "policy_locked" { "网吧策略锁定了本地实时读取所需设置。该环境当前不支持实时读取；更换 token、重解压或更换抓包驱动不会解决。" }
    "profile_ephemeral" { "当前 Windows 用户配置是临时或强制配置，兼容状态无法安全持久化。请联系网吧管理员提供可持久用户配置。" }
    "interactive_user_mismatch" { "当前管理员身份与桌面登录用户不一致。为避免把证书写入另一位用户，本次已停止。请使用同一桌面用户完成管理员确认。" }
    "component_write_denied" { "网吧策略拒绝写入本地兼容组件缓存。请联系管理员允许当前用户的 BidKingLive 组件目录。" }
    "component_execution_blocked" { "网吧策略阻止了已验证的本地兼容组件执行。请联系管理员为当前版本组件放行。" }
    "driver_load_denied" { "网吧策略拒绝加载本地实时读取所需驱动。该环境当前不支持实时读取；Npcap 或离线模式不能替代 WSS 解密。" }
    "package_root_unsupported" { "程序当前位于不受支持的网络、链接或虚拟目录。请完整解压到本机普通文件夹后重试。" }
    "restore_disk_suspected" { "检测到可能被还原或重定向的存储位置，无法安全保存兼容状态。请联系网吧管理员提供可持久目录。" }
    "foreign_cert_or_state" { "发现无法证明由本次运行创建的证书或兼容状态。为避免删除或覆盖其他证书，本次已保留现场。" }
    "user_cancelled" { "你已取消本次临时兼容授权。未添加新的证书信任，也未继续启动。" }
    default { "无法安全确认当前网吧环境是否支持本地实时读取。本次未继续修改系统；请运行独立兼容检查和收集诊断，并将结果私发支持人员。" }
  }
  Show-FatalBox (
    "网吧环境兼容检查未通过。`n`n$Body`n`n固定分类：$Classification`n结果：internet_cafe_realtime_unsupported`n`n可运行「诊断工具\网吧环境兼容检查.bat」进行零修改复核。"
  ) "BidKing Live · 网吧环境兼容检查"
}

function Show-BidKingWssServiceFailure([string]$Classification) {
  $Body = switch ($Classification) {
    "wss_same_path_mixed_generation" {
      "新旧包可能在同一绝对目录混装。当前服务与本包 driver 路径相同，但代际身份无法完整证明；本次不会停止、删除或重配服务。`n`n请完全退出 Hero Ref 和游戏，把官方 ZIP 完整解压到新的最终目录；不要先删旧目录，也不要在首次启动后移动或改名新目录。"
    }
    "wss_stale_product_service_repaired_restart_required" {
      "已对完整验证的旧版产品服务提交精确删除，但旧 driver 或句柄未证明卸载。请先重启 Windows，再从当前最终目录启动；重启前不要继续启动游戏。"
    }
    default {
      "产品兼容服务身份冲突，且未达到安全删除条件。本次不会停止、删除或重配服务。请完全退出后运行收集诊断并私发支持人员；不要处理名为 WinDivert 的其他服务。"
    }
  }
  Show-FatalBox (
    "$Body`n`n固定分类：$Classification"
  ) "BidKing Live · 本地兼容服务"
}

if (-not (Test-Path -LiteralPath $PackageIdentityHelper -PathType Leaf)) {
  $StartupFailureExit = Write-BidKingStartupFailure "package_generation_identity_component_missing"
  Show-FatalBox "程序包代际身份组件缺失。`n`n请重新完整解压当前版本后再启动；不要覆盖旧目录中的零散文件。" "BidKing Live · 安装包不完整"
  exit $StartupFailureExit
}
$null = Write-BidKingStartupTiming "powershell_generation" "begin"
$PreviousIdentityEap = $ErrorActionPreference
try {
  $ErrorActionPreference = "Continue"
  $PackageIdentityOutput = (
    & $PythonPath -E -B $PackageIdentityHelper `
      --package-root $Repo `
      --monitor $Monitor `
      --runtime $MonitorPythonPath 2>&1 |
      Out-String
  )
  $PackageIdentityExit = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $PreviousIdentityEap
}
$PackageIdentityPrefix = "BIDKING_PACKAGE_GENERATION_IDENTITY="
$PackageIdentityLines = @(
  $PackageIdentityOutput -split "[`r`n]+" |
    Where-Object {
      $_.StartsWith($PackageIdentityPrefix, [System.StringComparison]::Ordinal)
    }
)
$ExpectedPackageGenerationIdentity = if ($PackageIdentityLines.Count -eq 1) {
  $PackageIdentityLines[0].Substring($PackageIdentityPrefix.Length)
} else {
  ""
}
if (
  $PackageIdentityExit -ne 0 -or
  $ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$'
) {
  $null = Write-BidKingStartupTiming "powershell_generation" "failed"
  Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
    "monitor_package_generation_unavailable reason=fixed_identity_error"
  )
  $StartupFailureExit = Write-BidKingStartupFailure "package_generation_identity_unavailable"
  Show-FatalBox "无法验证当前程序包的完整代际身份。`n`n为避免复用旧版本后台进程，本次已安全退出。请重新完整解压当前版本到独立目录后重试。" "BidKing Live · 无法验证程序包"
  exit $StartupFailureExit
}
$null = Write-BidKingStartupTiming "powershell_generation" "ok"

# A dead lock is recoverable regardless of which old extraction wrote its
# package-relative paths.  Re-prove the absent PID, delete only the same
# handle-bound lock bytes, and require an absent readback before startup may
# continue.  Live foreign/unknown owners never enter this path.
$null = Write-BidKingStartupTiming "powershell_process_identity" "begin"
$EarlyMonitorIdentity = Get-BidKingMonitorLockIdentity `
  -StatePath $LockPath `
  -ExpectedMonitorPath $Monitor `
  -ExpectedLogDir $LogPath `
  -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
if ($EarlyMonitorIdentity.State -eq "stale") {
  $DeadLockCleanup = Invoke-BidKingDeadMonitorLockCleanup `
    -StatePath $LockPath `
    -ExpectedMonitorPath $Monitor `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
    -ExpectedIdentity $EarlyMonitorIdentity
  if ($DeadLockCleanup.Succeeded -and (Get-BidKingMonitorLockPathState -StatePath $LockPath) -ne "absent") {
    $DeadLockCleanup.Succeeded = $false
    $DeadLockCleanup.State = "changed"
  }
  if (-not $DeadLockCleanup.Succeeded) {
    Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
      "monitor_dead_lock_reclaim action=preserved state={0} reason={1} obstacle_relieved=false" -f $DeadLockCleanup.State, $EarlyMonitorIdentity.Reason
    )
    $StartupFailureExit = Write-BidKingStartupFailure `
      "monitor_identity_unknown" $DeadLockCleanup.State
    if ($DeadLockCleanup.State -eq "changed") {
      Show-FatalBox "启动锁在清理前发生变化，本次已安全停止。`n`n请完全退出所有 Hero Ref 后重试；仍失败时运行「诊断工具\启动修复.bat」。" "BidKing Live · 启动锁已变化"
    } else {
      Show-FatalBox "无法安全删除或隔离已退出后台留下的启动锁，本次已安全停止。`n`n请完全退出所有 Hero Ref 后运行「诊断工具\启动修复.bat」；仍失败时收集诊断并私发支持人员。" "BidKing Live · 启动锁清理未完成"
    }
    exit $StartupFailureExit
  }
  Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
    ("monitor_dead_lock_reclaim action=reclaimed mode={0}" -f $DeadLockCleanup.Mode) +
    (" reason={0} obstacle_relieved=true" -f $EarlyMonitorIdentity.Reason)
  )
}

# A normal second click is an idempotent request.  Prove both long-lived
# processes from their package-bound state before the WSS component, certificate,
# dependency and four-second cold-start work.  Unknown/foreign state never enters
# this fast path and remains subject to the existing fail-closed handling below.
if (-not $Restart -and -not $NoOverlay) {
  $FastMonitorIdentity = Get-BidKingMonitorLockIdentity `
    -StatePath $LockPath `
    -ExpectedMonitorPath $Monitor `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  $FastOverlayIdentity = Get-BidKingCurrentOverlayIdentity `
    -StatePath $OverlayPidPath `
    -ExpectedOverlayPath $Overlay `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  if ($FastOverlayIdentity.State -eq "current") {
    $script:BidKingLaunchState.OverlayState = "reused_preserved"
  }
  $TimingTopology = if (
    $FastMonitorIdentity.State -eq "current" -and
    $FastOverlayIdentity.State -eq "current"
  ) {
    "current_current"
  } elseif (
    $FastMonitorIdentity.State -eq "current" -and
    $FastOverlayIdentity.State -eq "absent"
  ) {
    "current_absent"
  } elseif (
    $FastMonitorIdentity.State -eq "current" -and
    $FastOverlayIdentity.State -eq "stale"
  ) {
    "current_stale"
  } elseif (
    $FastMonitorIdentity.State -in @("foreign", "unknown") -or
    $FastOverlayIdentity.State -in @("foreign", "unknown")
  ) {
    "current_unsafe"
  } else {
    "cold_start"
  }
  $env:BIDKING_STARTUP_TIMING_TOPOLOGY = $TimingTopology
  $null = Write-BidKingStartupTiming "powershell_process_identity" "ok" $TimingTopology
  if (
    $FastMonitorIdentity.State -eq "current" -and
    $FastOverlayIdentity.State -eq "current"
  ) {
    Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
      "startup_reuse_ready monitor_state=current overlay_state=current elapsed_ms={0}" -f `
        [int]$LaunchStopwatch.ElapsedMilliseconds
    )
    $null = Write-BidKingStartupTiming "powershell_launch" "ok" "current_current"
    $null = Write-BidKingStartupTiming "startup_terminal" "ok" "current_current"
    $script:BidKingLaunchState.Succeeded = $true
    $script:BidKingLaunchState.MonitorState = "reused_preserved"
    $script:BidKingLaunchState.OverlayState = "reused_preserved"
    exit 0
  }
} else {
  $env:BIDKING_STARTUP_TIMING_TOPOLOGY = "cold_start"
  $null = Write-BidKingStartupTiming "powershell_process_identity" "ok" "cold_start"
}

# --- 0.3.0 WSS cold-start preflight ---
# A connection created before this local reader starts cannot be adopted safely.
# Fail before starting anything and give the user the required order. The one safe
# exception is reopening only the overlay while a positively identified WSS monitor
# is already alive. A requested restart must still fail before it can kill that
# monitor and break the game's active connection.
$MitmdumpPath = $null
$WssConfDir = $null
$InternetCafeAdapter = $null
$TemporaryTrustLeasePath = ""
$TemporaryTrustInserted = $false
$TemporaryTrustConsentGranted = $false
$TemporaryTrustCertificatePath = ""
$TemporaryTrustRuntimeIdentity = ""
$TemporaryTrustComponentIdentity = ""
$TemporaryTrustOwnerSid = ""
if ($UseWssCapture) {
  $null = Write-BidKingStartupTiming "powershell_certificate" "begin"
  if (-not (Test-Path -LiteralPath $InternetCafeHelper -PathType Leaf)) {
    $StartupFailureExit = Write-BidKingStartupFailure "package_generation_identity_component_missing"
    Show-FatalBox "网吧环境兼容检查组件缺失。`n`n请重新完整解压当前版本后重试。" "BidKing Live · 安装包不完整"
    exit $StartupFailureExit
  }
  . $InternetCafeHelper `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  if ($ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$') {
    $StartupFailureExit = Write-BidKingStartupFailure "package_generation_identity_unavailable"
    exit $StartupFailureExit
  }
  $InternetCafeAdapter = New-BidKingInternetCafeProductionAdapter
  $TemporaryTrustOwnerSid = [string](& $InternetCafeAdapter.GetCurrentSid)
  $ComponentCacheTarget = Join-Path $env:LOCALAPPDATA "BidKingLive\components"
  $InternetCafePreflight = Invoke-BidKingInternetCafePreflight `
    -Adapter $InternetCafeAdapter `
    -PackageRoot $Repo `
    -ComponentPath $ComponentCacheTarget
  try { $null = Write-BidKingInternetCafeReceipt $InternetCafePreflight } catch {}
  if ($InternetCafePreflight.classification -ne "ready") {
    $StartupFailureExit = Write-BidKingStartupFailure `
      ([string]$InternetCafePreflight.classification)
    Show-BidKingInternetCafeFailure ([string]$InternetCafePreflight.classification)
    exit $StartupFailureExit
  }

  $GameProcessBaseName = [System.IO.Path]::GetFileNameWithoutExtension($ProcessName)
  $ExistingWssMonitorProcs = @(
    Get-BidKingLiveProcesses `
      -MonitorLockPath $LockPath `
      -ExpectedMonitorPath $WssMonitor `
      -ExpectedLogDir $LogPath `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
      -StatusPath $StartupStatus
  )
  $GameProcessRunning = $null -ne (
    Get-Process -Name $GameProcessBaseName -ErrorAction SilentlyContinue |
      Select-Object -First 1
  )
  if ($GameProcessRunning -and ($Restart -or -not $ExistingWssMonitorProcs)) {
    Show-FatalBox "为了正确读取本局信息，计算器需要在游戏连接前启动。`n`n请按顺序操作：`n  1) 完全退出 BidKing 游戏；`n  2) 打开 BidKingLive / Hero Ref；`n  3) 等悬浮窗出现；`n  4) 再从 Steam 启动游戏。`n`n计算器不会自动关闭游戏。" "BidKing Live · 请先关闭游戏"
    exit 4
  }

  $PackagedRuntime = Join-Path $Repo "vendor\mitmproxy\mitmdump.exe"
  if (
    -not (Test-Path -LiteralPath $PackagedRuntime -PathType Leaf) -and
    (Test-Path -LiteralPath $RuntimeComponentBootstrap -PathType Leaf) -and
    (Test-Path -LiteralPath $RuntimeComponentDescriptor -PathType Leaf)
  ) {
    $InstallPrompt = Get-RuntimeComponentInstallPrompt $RuntimeComponentDescriptor
    if ($InstallPrompt) {
      Add-Type -AssemblyName System.Windows.Forms
      $ComponentConsent = [System.Windows.Forms.MessageBox]::Show(
        $InstallPrompt,
        "BidKing Live · 首次兼容设置",
        "OKCancel",
        "Information"
      )
      Invalidate-BidKingProcessInventory -Reason "dialog" -StatusPath $StartupStatus
      if ($ComponentConsent -ne [System.Windows.Forms.DialogResult]::OK) {
        $Cancelled = Get-BidKingInternetCafeResult "user_cancelled" "component_consent"
        try { $null = Write-BidKingInternetCafeReceipt $Cancelled } catch {}
        $StartupFailureExit = Write-BidKingStartupFailure "user_cancelled"
        Show-BidKingInternetCafeFailure "user_cancelled"
        exit $StartupFailureExit
      }
    }
  }

  $null = Write-BidKingStartupTiming "powershell_component" "begin"
  $MitmdumpPath = Resolve-WssMitmdumpPath `
    -PackageRoot $Repo `
    -AllowEngineeringRuntime $AllowEngineeringRuntime `
    -PythonPath $PythonPath
  if (-not $MitmdumpPath) {
    $null = Write-BidKingStartupTiming "powershell_component" "failed"
    $ComponentFailure = if ($script:BidKingLastComponentFailure -in @(
      "component_write_denied", "component_execution_blocked"
    )) { [string]$script:BidKingLastComponentFailure } else { "unknown" }
    $FailureResult = Get-BidKingInternetCafeResult $ComponentFailure "component_prepare"
    try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
    $StartupFailureExit = Write-BidKingStartupFailure $ComponentFailure
    Show-BidKingInternetCafeFailure $ComponentFailure
    exit $StartupFailureExit
  }
  $null = Write-BidKingStartupTiming "powershell_component" "ok"
  if (-not (Test-Path -LiteralPath $WssAddon)) {
    Show-FatalBox "新版游戏兼容组件不完整。`n`n请重新完整解压当前版本安装包，把程序文件夹加入杀毒软件白名单后重试。" "BidKing Live · 启动组件缺失"
    exit 2
  }

  $WssConfDir = Join-Path $env:LOCALAPPDATA "BidKingLive\mitmproxy"
  try {
    New-Item -ItemType Directory -Path $WssConfDir -Force | Out-Null
  } catch {
    $FailureResult = Get-BidKingInternetCafeResult "component_write_denied" "certificate_prepare"
    try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
    $StartupFailureExit = Write-BidKingStartupFailure "component_write_denied"
    Show-BidKingInternetCafeFailure "component_write_denied"
    exit $StartupFailureExit
  }
  $WssCaPath = Join-Path $WssConfDir "mitmproxy-ca-cert.cer"
  if (-not (Test-Path -LiteralPath $WssCaPath)) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $PrepareOut = (& $PythonPath -E -B $WssMonitor --prepare-ca --mitmdump $MitmdumpPath --confdir $WssConfDir 2>&1 | Out-String)
    $PrepareExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($PrepareExit -ne 0 -or -not (Test-Path -LiteralPath $WssCaPath)) {
      $CaFailureClass = "unknown"
      $CaFailurePrefix = "BIDKING_WSS_CA_FAILURE="
      $CaFailureLine = @(
        $PrepareOut -split "[`r`n]+" |
          Where-Object { $_.StartsWith($CaFailurePrefix, [System.StringComparison]::Ordinal) }
      ) | Select-Object -Last 1
      if ($CaFailureLine) {
        $CaFailureCandidate = $CaFailureLine.Substring($CaFailurePrefix.Length)
        if ($CaFailureCandidate -in @(
          "component_write_denied", "component_execution_blocked"
        )) {
          $CaFailureClass = $CaFailureCandidate
        }
      }
      $FailureResult = Get-BidKingInternetCafeResult $CaFailureClass "certificate_prepare"
      try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
      $StartupFailureExit = Write-BidKingStartupFailure $CaFailureClass
      Show-BidKingInternetCafeFailure $CaFailureClass
      exit $StartupFailureExit
    }
  }

  $TemporaryTrustRuntimeIdentity = (
    Get-FileHash -LiteralPath $MitmdumpPath -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  $TemporaryTrustComponentIdentity = if (
    Test-Path -LiteralPath $RuntimeComponentDescriptor -PathType Leaf
  ) {
    (Get-FileHash -LiteralPath $RuntimeComponentDescriptor -Algorithm SHA256).Hash.ToLowerInvariant()
  } else { $TemporaryTrustRuntimeIdentity }
  # Product trust persists for this Windows user after one explicit product
  # explanation.  Normal, cancelled and abnormal exits reuse the exact CA on
  # the next launch; only the separated advanced uninstall/cleanup entry may
  # remove it.

  try {
    $WssCertificateSnapshot = Get-BidKingCertificateSnapshot $WssCaPath
    $WssStoreMatches = @(
      & $InternetCafeAdapter.ListCertificates $WssCertificateSnapshot.thumbprint
    )
    $WssExactMatches = @($WssStoreMatches | Where-Object {
      [string]$_.raw_b64 -ceq [string]$WssCertificateSnapshot.raw_b64
    })
    if (
      $WssStoreMatches.Count -gt 0 -and
      ($WssStoreMatches.Count -ne 1 -or $WssExactMatches.Count -ne 1)
    ) {
      throw [InvalidOperationException]::new("foreign certificate state")
    }
    $WssCaTrusted = $WssExactMatches.Count -eq 1
  } catch {
    $FailureResult = Get-BidKingInternetCafeResult "foreign_cert_or_state" "certificate_query"
    try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
    $StartupFailureExit = Write-BidKingStartupFailure "foreign_cert_or_state"
    Show-BidKingInternetCafeFailure "foreign_cert_or_state"
    exit $StartupFailureExit
  }

  if (-not $WssCaTrusted) {
    Add-Type -AssemblyName System.Windows.Forms
    $Consent = [System.Windows.Forms.MessageBox]::Show(
      "新版游戏连接方式发生变化。为了继续读取本机对局中的公开信息，Hero Ref 需要将一张【本机生成】的兼容证书添加到【当前 Windows 用户】的系统信任区。`n`n该设置仅用于本机对局数据兼容；不会记录或上传登录账号、激活 token，证书和私钥也不会随安装包分发或上传。确认后，该产品证书会保留在当前 Windows 用户的信任区，后续正常启停直接复用，不会每次退出都要求删除。`n`n点击【确定】添加信任并继续；点击【取消】不会添加证书信任，并退出。需要撤销时，请使用「诊断工具\高级卸载与清理\卸载本地兼容证书（高级清理）.bat」。",
      "BidKing Live · 首次兼容设置", "OKCancel", "Warning")
    Invalidate-BidKingProcessInventory -Reason "dialog" -StatusPath $StartupStatus
    if ($Consent -ne [System.Windows.Forms.DialogResult]::OK) {
      $Cancelled = Get-BidKingInternetCafeResult "user_cancelled" "certificate_consent"
      try { $null = Write-BidKingInternetCafeReceipt $Cancelled } catch {}
      $StartupFailureExit = Write-BidKingStartupFailure "user_cancelled"
      Show-BidKingInternetCafeFailure "user_cancelled"
      exit $StartupFailureExit
    }
    $TemporaryTrustCertificatePath = $WssCaPath
    $TemporaryTrustConsentGranted = $true
  }
  $null = Write-BidKingStartupTiming "powershell_certificate" "ok"
} else {
  $null = Write-BidKingStartupTiming "powershell_component" "skipped"
  $null = Write-BidKingStartupTiming "powershell_certificate" "skipped"
}

# 运行环境自检(提权后跑一次): 用内嵌 python 导入关键依赖, 兜住"python 起不来 / 缺 VC++ 运行库 /
# 依赖被杀软删"——这些会让 monitor 秒退, 否则被"抓包未就绪"误报, 引去反复重装 Npcap。失败给精确原因。
# EAP=Stop 下 python 写 stderr 会被当致命错误, 自检期间临时切 Continue(同 build 脚本隔离验证)。
# 探针【只用 -E, 不用 -s】: 必须与下面 monitor/overlay 的【实际启动环境一致】。-s 会排除用户级
# site-packages(%APPDATA%\Roaming\Python), 但实际运行不带 -s → 源码 dev 跑(系统 Python·依赖常装在
# 用户级 site)时探针假失败(误报"缺 VC++/numpy")。打包版内嵌 Python 无用户 site → 去 -s 对其为 no-op,
# 仍能抓"内嵌 site 缺依赖"。-s 是从 build 隔离验证误抄进运行时探针的, build 冒烟另行用 -E -s 不受影响。
$null = Write-BidKingStartupTiming "powershell_dependency" "begin"
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$ProbeOut = (& $PythonPath -E -B -c "import numpy, psutil, pydantic; from cryptography.hazmat.primitives.ciphers.aead import AESGCM; print('PROBE_OK')" 2>&1 | Out-String)
$ProbeExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($ProbeExit -ne 0 -or $ProbeOut -notmatch "PROBE_OK") {
  $null = Write-BidKingStartupTiming "powershell_dependency" "failed"
  Show-FatalBox ("程序运行环境自检未通过, 无法启动。`n`n最可能原因: 缺少「Microsoft Visual C++ 2015-2022 可再发行组件 (x64)」, 或系统过旧 / runtime 被杀软删除。`n`n请按顺序处理:`n  1) 安装 VC++ 运行库: https://aka.ms/vs/17/release/vc_redist.x64.exe`n  2) 确认系统为 Windows 10 / 11 (64 位);`n  3) 把本程序文件夹加入杀软白名单后重新【完整解压】。`n`n技术信息(发作者时附上):`n" + $ProbeOut.Trim()) "BidKing Live · 运行环境自检未通过"
  exit 1
}
$null = Write-BidKingStartupTiming "powershell_dependency" "ok"

# 样本上传配置(本地私有, 不入 git): 若存在 scripts\sample_upload.local.ps1 就加载它,
# 由它 set $env:BIDKING_SAMPLE_UPLOAD_URL / _KEY -> monitor(下面 Start-Process 起的)继承环境变量
# 后结算自动上传到收集服务器。密钥只放这个 gitignore 文件里, 不硬编码进受控源码。
# (打包/分发时这里改为内置+加密, 见 TODO; 当前仅本地实战测试用明文。)
$UploadCfg = Join-Path $Repo "scripts\sample_upload.local.ps1"
if (Test-Path $UploadCfg) { . $UploadCfg }
# 上传状态【仅调试显示】: .local.ps1 里置 BIDKING_SAMPLE_UPLOAD_DEBUG=1 才写状态文件/打印。
# 发布版用内置+加密的静默机制(见 TODO), 不设此调试开关 -> 不显示任何上传痕迹。
# 调试模式下【无论 ON/OFF 都写一行 + 原因】, 这样"普通启动传不了"(配置没加载/env 跨 UAC 提权丢失)
# 也能一眼看出, 而非啥都不写=失败不可见。提权后窗口隐藏, 用状态文件确认:
#   Get-Content data\logs\live\upload_status.txt
if ($env:BIDKING_SAMPLE_UPLOAD_DEBUG -eq "1") {
  $UpStatus = if ($env:BIDKING_SAMPLE_UPLOAD_URL) { "ON  -> $($env:BIDKING_SAMPLE_UPLOAD_URL)" }
              elseif (-not (Test-Path $UploadCfg)) { "OFF -> 缺少 scripts\sample_upload.local.ps1(配置文件不存在)" }
              else { "OFF -> .local.ps1 已加载但未设 BIDKING_SAMPLE_UPLOAD_URL" }
  Write-Host "Sample upload: $UpStatus" -ForegroundColor Cyan
  "$((Get-Date).ToString('s'))  $UpStatus" | Set-Content -Path (Join-Path $LogPath "upload_status.txt") -Encoding utf8
}

# Npcap 首次安装: 免费版安装包不支持 /S 静默(静默仅 Npcap OEM 付费版支持), 所以拉起图形安装界面,
# 弹中文说明框引导玩家点几下完成(仅首次, 之后永久跳过)。仅打包版带 vendor/npcap-installer.exe;
# 开发机已装 Npcap 会跳过整段。PS 控制台被壳隐藏, 故用 MessageBox 而非 Write-Host 与玩家沟通。
$NpcapOk = $true
if (-not $UseWssCapture) {
  $NpcapOk = ($null -ne (Get-Service npcap -ErrorAction SilentlyContinue)) -or (Test-Path "$env:SystemRoot/System32/Npcap/wpcap.dll")
}
if (-not $UseWssCapture -and -not $NpcapOk) {
  Add-Type -AssemblyName System.Windows.Forms
  $NpcapInstaller = Join-Path $Repo "vendor/npcap-installer.exe"
  if (Test-Path $NpcapInstaller) {
    [System.Windows.Forms.MessageBox]::Show(
      "首次使用需要安装一次抓包驱动 Npcap（之后永久不用再装）。`n`n点【确定】后会弹出 Npcap 安装窗口，请依次点击：`n  I Agree（同意）→ Install（安装）→ Next（下一步）→ Finish（完成）`n`n保持默认选项即可，约 30 秒。安装完悬浮窗会自动出现。",
      "BidKing Live · 首次安装抓包驱动", "OK", "Information") | Out-Null
    Invalidate-BidKingProcessInventory -Reason "process_start" -StatusPath $StartupStatus
    try {
      Start-Process -FilePath $NpcapInstaller -Wait
    } finally {
      Invalidate-BidKingProcessInventory -Reason "process_start" -StatusPath $StartupStatus
    }
    Start-Sleep -Seconds 2
    $NpcapOk = ($null -ne (Get-Service npcap -ErrorAction SilentlyContinue)) -or (Test-Path "$env:SystemRoot/System32/Npcap/wpcap.dll")
    if (-not $NpcapOk) {
      if ($ManagedLaunch) {
        $script:BidKingLaunchState.MessageKey = "npcap_install_incomplete"
        $StartupFailureExit = Write-BidKingStartupFailure "unknown" "npcap_install_incomplete"
        exit $StartupFailureExit
      }
      [System.Windows.Forms.MessageBox]::Show(
        "未检测到 Npcap 安装完成。`n如果悬浮窗没有数据，请手动运行包内 vendor\npcap-installer.exe 装好后重新打开。",
        "BidKing Live", "OK", "Warning") | Out-Null
    }
  } else {
    if ($ManagedLaunch) {
      $script:BidKingLaunchState.MessageKey = "npcap_installer_missing"
      $StartupFailureExit = Write-BidKingStartupFailure "unknown" "npcap_installer_missing"
      exit $StartupFailureExit
    }
    [System.Windows.Forms.MessageBox]::Show(
      "未找到抓包驱动安装包。`n请从 https://npcap.com 下载安装 Npcap 后再运行。",
      "BidKing Live", "OK", "Warning") | Out-Null
  }
}

function Get-LiveProcesses {
  param([string]$InventoryPhase = "", [switch]$Refresh)
  Get-BidKingLiveProcesses `
    -MonitorLockPath $LockPath `
    -ExpectedMonitorPath $Monitor `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
    -StatusPath $StartupStatus `
    -InventoryPhase $InventoryPhase -Refresh:$Refresh
}
function Get-OverlayProcesses {
  param([string]$InventoryPhase = "", [switch]$Refresh)
  Get-BidKingOverlayProcesses -OverlayPidPath $OverlayPidPath -StatusPath $StartupStatus `
    -InventoryPhase $InventoryPhase -Refresh:$Refresh
}

function Assert-BidKingMonitorGenerationConflictAbsent {
  param([string]$InventoryPhase, [switch]$Refresh)
  $GenerationConflicts = @(
    Get-BidKingMonitorGenerationConflicts `
      -StatusPath $StartupStatus `
      -ExpectedMonitorPath $Monitor `
      -ExpectedLogDir $LogPath `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
      -InventoryPhase $InventoryPhase -Refresh:$Refresh
  )
  if ($GenerationConflicts.Count -gt 0) {
    $ConflictStates = @($GenerationConflicts | ForEach-Object { [string]$_.GenerationState })
    $ConflictCode = if ($ConflictStates -contains "foreign_context") {
      "monitor_foreign_process"
    } elseif ($ConflictStates -contains "unknown") {
      "monitor_identity_unknown"
    } else {
      "monitor_old_package_conflict"
    }
    Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
      "monitor_package_generation_unverified action=blocked_cim_conflict class={0}" -f $ConflictCode
    )
    $StartupFailureExit = Write-BidKingStartupFailure $ConflictCode
    Show-FatalBox "检测到无法通过当前程序包代际校验的后台读取进程。`n`n为避免同时运行两个读取进程或误关其他程序，本次已安全退出，并且不会终止身份不明的进程。请完全退出游戏后运行「诊断工具\启动修复.bat」；身份仍无法证明时工具会保留现场并生成脱敏回执。`n`n分类：$ConflictCode" "BidKing Live · 后台进程代际冲突"
    exit $StartupFailureExit
  }
}

function Write-MonitorLogSessionState([int]$MonitorProcessId, [bool]$ReusedMonitor) {
  $MonitorLogStateTmp = $null
  try {
    $MonitorStartedAt = $LaunchStartedAt
    $PreviousLaunchId = $null
    if ($ReusedMonitor -and (Test-Path -LiteralPath $MonitorLogSession)) {
      try {
        $PreviousMonitorLogState = Get-Content -LiteralPath $MonitorLogSession -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([int]$PreviousMonitorLogState.monitor_pid -eq $MonitorProcessId) {
          $PreviousLaunchId = [string]$PreviousMonitorLogState.launch_id
          if ($PreviousMonitorLogState.PSObject.Properties.Name -contains "monitor_started_at") {
            $MonitorStartedAt = [double]$PreviousMonitorLogState.monitor_started_at
          } elseif ([double]$PreviousMonitorLogState.started_at -gt 0) {
            $MonitorStartedAt = [double]$PreviousMonitorLogState.started_at
          }
        }
      } catch {}
    }
    $MonitorLogState = @{
      launch_id = $LaunchId
      started_at = $LaunchStartedAt
      monitor_pid = $MonitorProcessId
      monitor_started_at = $MonitorStartedAt
      reused_monitor = $ReusedMonitor
      stdout_file = [System.IO.Path]::GetFileName($MonitorOut)
      stderr_file = [System.IO.Path]::GetFileName($MonitorErr)
    }
    if ($PreviousLaunchId) { $MonitorLogState.previous_launch_id = $PreviousLaunchId }
    $MonitorLogStateJson = $MonitorLogState | ConvertTo-Json -Compress
    $MonitorLogStateTmp = "$MonitorLogSession.tmp-$PID"
    [System.IO.File]::WriteAllText($MonitorLogStateTmp, $MonitorLogStateJson, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $MonitorLogStateTmp -Destination $MonitorLogSession -Force
  } catch {
    if ($MonitorLogStateTmp -and (Test-Path -LiteralPath $MonitorLogStateTmp)) {
      Remove-Item -LiteralPath $MonitorLogStateTmp -Force -ErrorAction SilentlyContinue
    }
    Add-Content -Path $StartupStatus -Value (
      "{0} monitor_log_session_write_failed launch_id={1} reason_type={2}" -f `
        (Get-Date -Format o), $LaunchId, $_.Exception.GetType().Name
    ) -Encoding utf8
  }
}

function Write-OverlayLogSessionState(
  [int]$OverlayProcessId,
  [double]$OverlayStartedAt,
  [string]$GuardState,
  [string]$ExitClass,
  $ExitCode
) {
  $OverlayLogStateTmp = $null
  try {
    $SafeGuardState = if ($GuardState -in @("started", "alive", "dead", "unknown")) {
      $GuardState
    } else {
      "unknown"
    }
    $SafeExitClass = if ($ExitClass -in @(
      "running",
      "overlay_exited_cleanly",
      "overlay_exception",
      "monitor_exit",
      "process_query_unknown"
    )) {
      $ExitClass
    } else {
      "process_query_unknown"
    }
    $OverlayLogState = @{
      schema_version = 1
      launch_id = $LaunchId
      started_at = $LaunchStartedAt
      overlay_started_at = $OverlayStartedAt
      overlay_pid = $OverlayProcessId
      guard_state = $SafeGuardState
      exit_class = $SafeExitClass
      stderr_file = [System.IO.Path]::GetFileName($OverlayErr)
      runtime_status_file = "hero_ref_ui_runtime_status.json"
    }
    if ($null -ne $ExitCode) {
      $OverlayLogState.exit_code = [int]$ExitCode
    }
    $OverlayLogStateJson = $OverlayLogState | ConvertTo-Json -Compress
    $OverlayLogStateTmp = "$OverlayLogSession.tmp-$PID"
    [System.IO.File]::WriteAllText(
      $OverlayLogStateTmp,
      $OverlayLogStateJson,
      (New-Object System.Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $OverlayLogStateTmp -Destination $OverlayLogSession -Force
  } catch {
    if ($OverlayLogStateTmp -and (Test-Path -LiteralPath $OverlayLogStateTmp)) {
      Remove-Item -LiteralPath $OverlayLogStateTmp -Force -ErrorAction SilentlyContinue
    }
    Add-Content -Path $StartupStatus -Value (
      "{0} overlay_log_session_write_failed launch_id={1} reason_type={2}" -f `
        (Get-Date -Format o), $LaunchId, $_.Exception.GetType().Name
    ) -Encoding utf8
  }
}

function Get-StartedProcessProbeState($Process) {
  if (-not $Process) {
    return [pscustomobject]@{ State = "unknown"; ExitCode = $null }
  }
  try {
    $Process.Refresh()
    if ([bool]$Process.HasExited) {
      return [pscustomobject]@{ State = "dead"; ExitCode = [int]$Process.ExitCode }
    }
    return [pscustomobject]@{ State = "alive"; ExitCode = $null }
  } catch {
    return [pscustomobject]@{ State = "unknown"; ExitCode = $null }
  }
}

function Get-PidProbeState([int]$ProcessId) {
  if ($ProcessId -le 0) {
    return [pscustomobject]@{ State = "dead"; ExitCode = $null }
  }
  try {
    $Process = [System.Diagnostics.Process]::GetProcessById($ProcessId)
    $Process.Refresh()
    if ([bool]$Process.HasExited) {
      return [pscustomobject]@{ State = "dead"; ExitCode = [int]$Process.ExitCode }
    }
    return [pscustomobject]@{ State = "alive"; ExitCode = $null }
  } catch [System.ArgumentException] {
    return [pscustomobject]@{ State = "dead"; ExitCode = $null }
  } catch {
    return [pscustomobject]@{ State = "unknown"; ExitCode = $null }
  }
}

function Get-OverlayExitClass([string]$MonitorState, [string]$OverlayState, $OverlayExitCode) {
  if ($MonitorState -eq "unknown" -or $OverlayState -eq "unknown") {
    return "process_query_unknown"
  }
  if ($OverlayState -eq "dead") {
    if ($null -ne $OverlayExitCode -and [int]$OverlayExitCode -ne 0) {
      return "overlay_exception"
    }
    if ($MonitorState -eq "dead") {
      return "monitor_exit"
    }
    return "overlay_exited_cleanly"
  }
  if ($MonitorState -eq "dead") {
    return "monitor_exit"
  }
  return "running"
}

function Get-BidKingMonitorReadinessState(
  [string]$StatePath,
  [string]$ExpectedLaunchId
) {
  if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
    return "not_ready"
  }
  try {
    [byte[]]$RawBytes = [System.IO.File]::ReadAllBytes($StatePath)
    if ($RawBytes.Length -le 0 -or $RawBytes.Length -gt 16384) {
      return "unknown"
    }
    $StrictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $Raw = $StrictUtf8.GetString($RawBytes).Trim()
    Assert-BidKingJsonObjectKeysUnique -Json $Raw
    $Payload = $Raw | ConvertFrom-Json -ErrorAction Stop
    if (
      [int]$Payload.schema_version -ne 1 -or
      [string]$Payload.launch_id -cne $ExpectedLaunchId
    ) {
      return "not_ready"
    }
    if ([string]$Payload.wss_intercept_state -ceq "ready") {
      return "ready"
    }
    return "not_ready"
  } catch {
    return "unknown"
  }
}

function Get-BidKingStartupGuardDecision(
  [string]$MonitorState,
  [string]$OverlayState,
  $OverlayExitCode,
  [string]$ReadinessState,
  [bool]$DeadlineReached
) {
  $ExitClass = Get-OverlayExitClass $MonitorState $OverlayState $OverlayExitCode
  if ($ExitClass -ne "running") {
    return $ExitClass
  }
  if ($ReadinessState -eq "ready") {
    return "ready"
  }
  if ($DeadlineReached) {
    return "not_ready"
  }
  return "wait"
}

function Invoke-BidKingRestartCleanup() {
  $LockIdentity = Get-BidKingMonitorLockIdentity `
    -StatePath $LockPath `
    -ExpectedMonitorPath $Monitor `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  if (
    $LockIdentity.State -in @("foreign", "unknown") -or
    ($LockIdentity.State -eq "stale" -and $LockIdentity.Reason -eq "pid_reused")
  ) {
    # Dead-lock recovery is handled by the identity-bound startup gates.  A
    # recycled PID must never authorize this separate process-stopping path.
    Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
      ("monitor_restart_lock_preserved state={0}" -f $LockIdentity.State) +
      (" reason={0} obstacle_relieved=false" -f $LockIdentity.Reason)
    )
    return [pscustomobject]@{
      Succeeded = $false
      LockState = $LockIdentity.State
      KilledMonitorPids = @()
    }
  }

  $KilledMonitorPids = @()
  $InitialMonitorProcesses = @(Get-LiveProcesses -InventoryPhase "restart_initial" -Refresh:$script:BidKingCimProcessEnumerationAvailable)
  $InitialOverlayProcesses = @(Get-OverlayProcesses -InventoryPhase "restart_initial")
  foreach ($process in $InitialMonitorProcesses) {
    $KilledMonitorPids += [int]$process.ProcessId
    Invalidate-BidKingProcessInventory -Reason "process_stop" -StatusPath $StartupStatus
    try { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue } finally {
      Invalidate-BidKingProcessInventory -Reason "process_stop" -StatusPath $StartupStatus
    }
  }
  foreach ($process in $InitialOverlayProcesses) {
    Invalidate-BidKingProcessInventory -Reason "process_stop" -StatusPath $StartupStatus
    try { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue } finally {
      Invalidate-BidKingProcessInventory -Reason "process_stop" -StatusPath $StartupStatus
    }
  }

  $killDeadline = (Get-Date).AddSeconds(8)
  while ((@(Get-LiveProcesses -InventoryPhase "restart_wait" -Refresh:$script:BidKingCimProcessEnumerationAvailable).Count -gt 0) -and (Get-Date) -lt $killDeadline) {
    Start-Sleep -Milliseconds 250
  }
  foreach ($process in @(Get-LiveProcesses -InventoryPhase "restart_final" -Refresh:$script:BidKingCimProcessEnumerationAvailable)) {
    $KilledMonitorPids += [int]$process.ProcessId
    Invalidate-BidKingProcessInventory -Reason "process_stop" -StatusPath $StartupStatus
    try { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue } finally {
      Invalidate-BidKingProcessInventory -Reason "process_stop" -StatusPath $StartupStatus
    }
  }
  $KilledMonitorPids = @($KilledMonitorPids | Select-Object -Unique)
  $RemainingMonitorProcesses = @(Get-LiveProcesses -InventoryPhase "restart_final" -Refresh:$script:BidKingCimProcessEnumerationAvailable)
  if ($RemainingMonitorProcesses.Count -gt 0) {
    Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
      "monitor_restart_lock_preserved state={0}" -f $LockIdentity.State
    )
    return [pscustomobject]@{
      Succeeded = $false
      LockState = $LockIdentity.State
      KilledMonitorPids = $KilledMonitorPids
    }
  }

  if ($LockIdentity.State -eq "current") {
    $PostStopIdentity = Get-BidKingMonitorLockIdentity `
      -StatePath $LockPath `
      -ExpectedMonitorPath $Monitor `
      -ExpectedLogDir $LogPath `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
    if ($PostStopIdentity.State -eq "absent") {
      $LockRemovalState = "absent"
    } elseif ($PostStopIdentity.State -eq "stale") {
      $LockRemovalState = Remove-BidKingMonitorLockIfFingerprint `
        -StatePath $LockPath `
        -Identity $LockIdentity
    } else {
      $LockRemovalState = "unknown"
    }
    if (
      $PostStopIdentity.State -notin @("absent", "stale") -or
      ($PostStopIdentity.State -eq "stale" -and $LockRemovalState -ne "deleted")
    ) {
      Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
        "monitor_restart_lock_preserved state=current"
      )
      return [pscustomobject]@{
        Succeeded = $false
        LockState = "current"
        KilledMonitorPids = $KilledMonitorPids
      }
    }
  }
  if (Test-Path $OverlayPidPath) {
    Remove-Item -LiteralPath $OverlayPidPath -Force -ErrorAction SilentlyContinue
  }

  return [pscustomobject]@{
    Succeeded = $true
    LockState = $LockIdentity.State
    KilledMonitorPids = $KilledMonitorPids
  }
}

function Test-BidKingGameRunning {
  $GameProcessBaseName = [System.IO.Path]::GetFileNameWithoutExtension($ProcessName)
  return $null -ne (
    Get-Process -Name $GameProcessBaseName -ErrorAction SilentlyContinue |
      Select-Object -First 1
  )
}

function Invoke-BidKingStalePackageCleanup {
  $InitialIdentity = Get-BidKingMonitorLockIdentity `
    -StatePath $LockPath `
    -ExpectedMonitorPath $Monitor `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  if ($InitialIdentity.State -ne "stale_package") {
    return [pscustomobject]@{
      Succeeded = ($InitialIdentity.State -in @("absent", "stale", "current"))
      State = $InitialIdentity.State
    }
  }
  if (Test-BidKingGameRunning) {
    Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
      "monitor_package_generation_stale action=blocked_game_active"
    )
    return [pscustomobject]@{ Succeeded = $false; State = "game_active" }
  }

  # Re-read immediately before stopping.  PID, creation epoch, entrypoint/log
  # paths, package state, and the lock-file identity must all still match.
  $CurrentIdentity = Get-BidKingMonitorLockIdentity `
    -StatePath $LockPath `
    -ExpectedMonitorPath $Monitor `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  if (
    $CurrentIdentity.State -ne "stale_package" -or
    $CurrentIdentity.ProcessId -ne $InitialIdentity.ProcessId -or
    $CurrentIdentity.ProcessStartedAt -ne $InitialIdentity.ProcessStartedAt -or
    -not [string]::Equals(
      [string]$CurrentIdentity.RawFingerprint,
      [string]$InitialIdentity.RawFingerprint,
      [System.StringComparison]::Ordinal
    )
  ) {
    return [pscustomobject]@{ Succeeded = $false; State = "changed" }
  }

  Invalidate-BidKingProcessInventory -Reason "stale_cleanup" -StatusPath $StartupStatus
  try { Stop-Process -Id ([int]$CurrentIdentity.ProcessId) -Force -ErrorAction SilentlyContinue } finally {
    Invalidate-BidKingProcessInventory -Reason "stale_cleanup" -StatusPath $StartupStatus
  }
  $StopDeadline = (Get-Date).AddSeconds(8)
  while ((Get-Process -Id ([int]$CurrentIdentity.ProcessId) -ErrorAction SilentlyContinue) -and
         (Get-Date) -lt $StopDeadline) {
    Start-Sleep -Milliseconds 250
  }
  if (Get-Process -Id ([int]$CurrentIdentity.ProcessId) -ErrorAction SilentlyContinue) {
    return [pscustomobject]@{ Succeeded = $false; State = "stop_failed" }
  }
  $RemovalState = Remove-BidKingMonitorLockIfFingerprint `
    -StatePath $LockPath `
    -Identity $InitialIdentity
  if ($RemovalState -notin @("absent", "deleted")) {
    return [pscustomobject]@{ Succeeded = $false; State = "lock_changed" }
  }
  Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
    "monitor_package_generation_stale action=replaced_safely"
  )
  return [pscustomobject]@{ Succeeded = $true; State = "replaced" }
}

$PreStartIdentity = Get-BidKingMonitorLockIdentity `
  -StatePath $LockPath `
  -ExpectedMonitorPath $Monitor `
  -ExpectedLogDir $LogPath `
  -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
if ($PreStartIdentity.State -eq "stale") {
  $PreStartDeadCleanup = Invoke-BidKingDeadMonitorLockCleanup `
    -StatePath $LockPath `
    -ExpectedMonitorPath $Monitor `
    -ExpectedLogDir $LogPath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
    -ExpectedIdentity $PreStartIdentity
  if ($PreStartDeadCleanup.Succeeded) {
    $PreStartDeadReason = $PreStartIdentity.Reason
    $PreStartIdentity = Get-BidKingMonitorLockIdentity `
      -StatePath $LockPath `
      -ExpectedMonitorPath $Monitor `
      -ExpectedLogDir $LogPath `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
    if ($PreStartIdentity.State -eq "absent" -and (Get-BidKingMonitorLockPathState -StatePath $LockPath) -eq "absent") {
      Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
        ("monitor_dead_lock_reclaim action=reclaimed mode={0} phase=pre_start" -f `
          $PreStartDeadCleanup.Mode) +
        (" reason={0} obstacle_relieved=true" -f $PreStartDeadReason)
      )
    } else {
      $PreStartDeadCleanup.Succeeded = $false
      $PreStartDeadCleanup.State = "changed"
    }
  }
  if (-not $PreStartDeadCleanup.Succeeded -or $PreStartIdentity.State -ne "absent") {
    Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
      "monitor_dead_lock_reclaim action=preserved state={0} phase=pre_start reason={1} obstacle_relieved=false" -f `
        $PreStartDeadCleanup.State, $PreStartIdentity.Reason
    )
    $StartupFailureExit = Write-BidKingStartupFailure `
      "monitor_identity_unknown" $PreStartDeadCleanup.State
    if ($PreStartDeadCleanup.State -eq "changed") {
      Show-FatalBox "启动锁在清理前发生变化，本次已安全停止。`n`n请完全退出所有 Hero Ref 后运行「诊断工具\启动修复.bat」再重试。" "BidKing Live · 启动锁已变化"
    } else {
      Show-FatalBox "无法安全删除或隔离已退出后台留下的启动锁，本次已安全停止。`n`n请完全退出所有 Hero Ref 后运行「诊断工具\启动修复.bat」；仍失败时收集诊断并私发支持人员。" "BidKing Live · 启动锁清理未完成"
    }
    exit $StartupFailureExit
  }
}
if ($PreStartIdentity.State -in @("foreign", "unknown")) {
  $ConflictCode = if ($PreStartIdentity.State -eq "foreign") {
    "monitor_foreign_lock"
  } else {
    "monitor_identity_unknown"
  }
  Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
    "monitor_package_generation_unverified action=preserved class={0} reason={1} obstacle_relieved=false" -f $ConflictCode, $PreStartIdentity.Reason
  )
  $StartupFailureExit = Write-BidKingStartupFailure $ConflictCode $PreStartIdentity.State
  Show-FatalBox "检测到身份不完整或不属于当前程序包的后台读取进程。`n`n为避免误关其他程序，本次未复用，并且不会终止身份不明的进程。请完全退出游戏后运行「诊断工具\启动修复.bat」；工具只会处理完整证明属于 BidKing 的旧后台或已退出进程留下的锁。`n`n分类：$ConflictCode" "BidKing Live · 后台进程身份不明"
  exit $StartupFailureExit
}
if ($PreStartIdentity.State -eq "stale_package") {
  $StaleCleanup = Invoke-BidKingStalePackageCleanup
  if (-not $StaleCleanup.Succeeded) {
    $ConflictCode = switch ($StaleCleanup.State) {
      "game_active" { "monitor_old_package_game_active" }
      "changed" { "monitor_old_package_changed" }
      "stop_failed" { "monitor_old_package_stop_failed" }
      "lock_changed" { "monitor_old_package_lock_changed" }
      default { "monitor_old_package_conflict" }
    }
    $SafeMessage = if ($StaleCleanup.State -eq "game_active") {
      "检测到游戏仍在运行，且后台读取进程属于旧程序包。`n`n本次不会中途关闭读取进程。请先完全退出游戏和旧版 Hero Ref，再从当前完整解压目录启动。"
    } else {
      "检测到旧程序包后台进程，但其身份在处理期间发生变化或无法安全停止。`n`n本次未继续启动。请完全退出游戏和所有旧版 Hero Ref 后重试。"
    }
    $StartupFailureExit = Write-BidKingStartupFailure $ConflictCode $StaleCleanup.State
    $SafeMessage += "`n`n分类：$ConflictCode；完全退出游戏后可运行「诊断工具\启动修复.bat」。"
    Show-FatalBox $SafeMessage "BidKing Live · 请先结束旧版本"
    exit $StartupFailureExit
  }
}
if ($Restart -or ($UseWssCapture -and $TemporaryTrustConsentGranted)) {
  Assert-BidKingMonitorGenerationConflictAbsent -InventoryPhase "pre_start"
}

# -Restart 期间被我们 kill 过的 monitor pid, 供下面"是否已在运行"检查排除(见 189 行注释)。
$RestartKilledMonitorPids = @()
if ($Restart) {
  Invalidate-BidKingProcessInventory -Reason "restart" -StatusPath $StartupStatus
  try { $RestartResult = Invoke-BidKingRestartCleanup } finally {
    Invalidate-BidKingProcessInventory -Reason "restart" -StatusPath $StartupStatus
  }
  $RestartKilledMonitorPids = @($RestartResult.KilledMonitorPids)
  if (-not $RestartResult.Succeeded) {
    $ConflictCode = if ($RestartResult.LockState -eq "foreign") {
      "monitor_foreign_lock"
    } elseif ($RestartResult.LockState -eq "unknown") {
      "monitor_identity_unknown"
    } else {
      "monitor_old_package_conflict"
    }
    $StartupFailureExit = Write-BidKingStartupFailure $ConflictCode $RestartResult.LockState
    Show-FatalBox "检测到不属于当前程序包、身份不完整或已变化的 monitor 锁。`n`n为避免关闭其他目录中的读取进程，本次重启已安全停止。请完全退出游戏和所有旧版 Hero Ref，再从当前完整解压目录重新启动；仍失败时请收集脱敏诊断并私发作者。" "BidKing Live · 无法安全重启"
    exit $StartupFailureExit
  }
}

# Install a new CurrentUser trust only after every non-mutating startup/process
# gate has passed.  The helper rechecks exact SID/cert/runtime identities here,
# closing the consent-to-write window.  A preexisting exact cert is only reused.
if ($UseWssCapture -and $TemporaryTrustConsentGranted) {
  $TrustResult = Enable-BidKingTemporaryCurrentUserTrust `
    -Adapter $InternetCafeAdapter `
    -CertificatePath $TemporaryTrustCertificatePath `
    -PackageGenerationIdentity $ExpectedPackageGenerationIdentity `
    -RuntimeIdentity $TemporaryTrustRuntimeIdentity `
    -ComponentIdentity $TemporaryTrustComponentIdentity `
    -MonitorEpoch $LaunchId `
    -Apply
  if (-not $TrustResult.Succeeded) {
    $FailureClass = if (Test-BidKingInternetCafeClassification $TrustResult.Classification) {
      [string]$TrustResult.Classification
    } else { "unknown" }
    $FailureResult = Get-BidKingInternetCafeResult $FailureClass "certificate_apply"
    try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
    $StartupFailureExit = Write-BidKingStartupFailure $FailureClass
    Show-BidKingInternetCafeFailure $FailureClass
    exit $StartupFailureExit
  }
  $TemporaryTrustLeasePath = [string]$TrustResult.LeasePath
  $TemporaryTrustInserted = [bool]$TrustResult.Inserted
}

# --- start monitor (if not already running) ---
# 排除 -Restart 刚 kill 过的 pid: Stop-Process -Force 异步, 卡死 monitor 可能撑过 8s 界限仍【将死】地
# 出现在 Get-LiveProcesses 里 → 若不排除会被误判"已在运行"→ 不起新 monitor → 复现"重启=UI关了没重开"
# 残留缺口(A2)。既然它已被我们 kill, 就不能当"还在正常跑的"来阻止起新 monitor。非 -Restart 路径此列表为空=no-op。
$null = Write-BidKingStartupTiming "powershell_monitor" "begin"
# Fresh final creation gate; both conflict and current-monitor consumers share
# this exact provider result. A failed host keeps the original PID fallback.
Assert-BidKingMonitorGenerationConflictAbsent -InventoryPhase "monitor_pre_spawn" `
  -Refresh:$script:BidKingCimProcessEnumerationAvailable
$MonitorProcs = @(Get-LiveProcesses -InventoryPhase "monitor_pre_spawn" | Where-Object { $RestartKilledMonitorPids -notcontains $_.ProcessId })
$MonitorPid = $null
$MonitorStartedAt = 0.0
$MonitorStartedAtMs = [long]0
$Started = $null
if (-not $MonitorProcs) {
  $MonitorArgs = @(
    "-B",
    (Q $Monitor),
    "--log-dir", (Q $LogPath),
    "--process-name", $ProcessName,
    "--package-generation-identity", $ExpectedPackageGenerationIdentity
  )
  if ($UseWssCapture) {
    $MonitorArgs += @(
      "--mitmdump", (Q $MitmdumpPath),
      "--addon", (Q $WssAddon),
      "--confdir", (Q $WssConfDir)
    )
    $MonitorArgs += @(
      "--lifecycle-overlay-state", (Q $OverlayPidPath),
      "--lifecycle-policy-state", (Q $LifecyclePolicyPath),
      "--lifecycle-launch-id", $LaunchId,
      "--internet-cafe-runtime-identity", $TemporaryTrustRuntimeIdentity,
      "--internet-cafe-component-identity", $TemporaryTrustComponentIdentity,
      "--internet-cafe-owner-sid", $TemporaryTrustOwnerSid
    )
    if ($AllowEngineeringRuntime) {
      $MonitorArgs += "--allow-engineering-runtime"
    }
  } else {
    foreach ($p in $ServerPort) { $MonitorArgs += @("--server-port", "$p") }
  }
  "" | Set-Content -Path $MonitorOut -Encoding utf8
  "" | Set-Content -Path $MonitorErr -Encoding utf8
  Invalidate-BidKingProcessInventory -Reason "process_start" -StatusPath $StartupStatus
  try {
    $Started = Start-Process -FilePath $MonitorPythonPath -WorkingDirectory $Repo -WindowStyle Hidden -PassThru `
      -ArgumentList $MonitorArgs -RedirectStandardOutput $MonitorOut -RedirectStandardError $MonitorErr
    $script:BidKingLaunchState.Monitor = New-BidKingOwnedStartupProcess $Started "monitor" $Monitor
    $script:BidKingLaunchState.MonitorState = "alive"
  } catch {
    # A monitor spawn failure is not an uninstall request.  Preserve the exact
    # product CA and private journal for the next launch or advanced cleanup.
    $FailureResult = Get-BidKingInternetCafeResult "unknown" "monitor_spawn"
    try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
    $StartupFailureExit = Write-BidKingStartupFailure "unknown"
    Show-BidKingInternetCafeFailure "unknown"
    exit $StartupFailureExit
  } finally {
    Invalidate-BidKingProcessInventory -Reason "process_start" -StatusPath $StartupStatus
  }
  $MonitorPid = $Started.Id
  try {
    $MonitorStartedAtMs = ([DateTimeOffset]$Started.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()
    $MonitorStartedAt = $MonitorStartedAtMs / 1000.0
  } catch {
    $MonitorStartedAt = 0.0
    $MonitorStartedAtMs = [long]0
  }
  # Bind these fixed-name log files to this exact launcher session.  mtime alone is not
  # evidence: an old log can be touched after startup.  The collector requires this
  # independently written sidecar plus the matching startup_session launch_id before it
  # labels stdout/stderr/settlement lines as evidence from the current launch.
  Write-MonitorLogSessionState $MonitorPid $false
  # wait for the monitor lock so the overlay can wire its lifecycle to the real pid
  $Deadline = (Get-Date).AddSeconds(6)
  while (-not (Test-Path $LockPath) -and (Get-Date) -lt $Deadline) { Start-Sleep -Milliseconds 250 }
  Start-Sleep -Milliseconds 500
  try { $null = Get-Process -Id $MonitorPid -ErrorAction Stop } catch {
    $null = Write-BidKingStartupTiming "powershell_monitor" "failed"
    # Preserve product trust across failed/abnormal launches; no automatic CA
    # removal is part of startup failure handling.
    $MonitorFailureClass = if ($UseWssCapture) {
      Get-BidKingWssHelperFailureClassification `
        -StatusPath $WssHelperStatus `
        -ExpectedLaunchId $LaunchId `
        -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
        -ExpectedRuntimeIdentity $TemporaryTrustRuntimeIdentity `
        -ExpectedComponentIdentity $TemporaryTrustComponentIdentity `
        -ExpectedOwnerSid $TemporaryTrustOwnerSid `
        -ExpectedMonitorProcessId $MonitorPid `
        -ExpectedMonitorStartedAtMs $MonitorStartedAtMs
    } else { "unknown" }
    if ($MonitorFailureClass -in @(
      "wss_same_path_mixed_generation",
      "wss_stale_product_service_conflict",
      "wss_stale_product_service_repaired_restart_required"
    )) {
      $StartupFailureExit = Write-BidKingStartupFailure $MonitorFailureClass
      Show-BidKingWssServiceFailure $MonitorFailureClass
      exit $StartupFailureExit
    }
    if ($MonitorFailureClass -in @("driver_load_denied", "component_execution_blocked")) {
      $FailureResult = Get-BidKingInternetCafeResult $MonitorFailureClass "monitor_start"
      try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
      $StartupFailureExit = Write-BidKingStartupFailure $MonitorFailureClass
      Show-BidKingInternetCafeFailure $MonitorFailureClass
      exit $StartupFailureExit
    }
    Write-Host "Monitor exited immediately. Check $MonitorErr" -ForegroundColor Red
    Get-Content $MonitorErr -Tail 30 -ErrorAction SilentlyContinue | Write-Host
    exit 1
  }
} else {
  $script:BidKingLaunchState.MonitorState = "reused_preserved"
  $MonitorPid = $MonitorProcs[0].ProcessId
  try {
    $MonitorProcess = Get-Process -Id ([int]$MonitorPid) -ErrorAction Stop
    $MonitorStartedAtMs = ([DateTimeOffset]$MonitorProcess.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()
    $MonitorStartedAt = $MonitorStartedAtMs / 1000.0
  } catch {
    $MonitorStartedAt = 0.0
    $MonitorStartedAtMs = [long]0
  }
  Write-MonitorLogSessionState $MonitorPid $true
  Add-Content -Path $StartupStatus -Value (
    "{0} monitor_reused launch_id={1} monitor_pid={2}" -f `
      (Get-Date -Format o), $LaunchId, $MonitorPid
  ) -Encoding utf8
  Write-Host "Monitor: already running (PID $MonitorPid)"
}
$null = Write-BidKingStartupTiming "powershell_monitor" $(if ($MonitorPid) { "ok" } else { "failed" })

if ($TemporaryTrustInserted -and $TemporaryTrustLeasePath) {
  try {
    if ($MonitorStartedAtMs -le 0) { throw "monitor epoch unavailable" }
    $TrustMonitorBound = Bind-BidKingTemporaryTrustMonitor `
      -Adapter $InternetCafeAdapter `
      -LeasePath $TemporaryTrustLeasePath `
      -PackageGenerationIdentity $ExpectedPackageGenerationIdentity `
      -RuntimeIdentity $TemporaryTrustRuntimeIdentity `
      -ComponentIdentity $TemporaryTrustComponentIdentity `
      -MonitorEpoch $LaunchId `
      -MonitorProcessId $MonitorPid `
      -MonitorStartedAtMs $MonitorStartedAtMs `
      -Apply
    if (-not $TrustMonitorBound) { throw "temporary trust monitor bind failed" }
    # No exit watcher: normal, cancelled and abnormal exits retain the exact
    # product CA.  This prevents a native root-store delete prompt loop.
  } catch {
    # The common finally owns this invocation's monitor and must first retain
    # any unknown/existing overlay dependency and the normal game protection.
    $FailureResult = Get-BidKingInternetCafeResult "unknown" "certificate_monitor_bind"
    try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
    $StartupFailureExit = Write-BidKingStartupFailure "unknown"
    Show-BidKingInternetCafeFailure "unknown"
    exit $StartupFailureExit
  }
}

# --- start overlay (the new ahmad_tk UI) ---
$null = Write-BidKingStartupTiming "powershell_overlay" $(if ($NoOverlay) { "skipped" } else { "begin" })
$StartedOverlay = $null
$OverlayPid = $null
$script:BidKingLaunchState.OverlayState = "preserved_unknown"
$OverlayProcs = if ($NoOverlay) { @() } else {
  @(Get-OverlayProcesses -InventoryPhase "overlay_pre_spawn" -Refresh:$script:BidKingCimProcessEnumerationAvailable)
}
if ($OverlayProcs) {
  $script:BidKingLaunchState.OverlayState = "reused_preserved"
} elseif (-not $NoOverlay -and $script:BidKingCimProcessEnumerationAvailable -and $script:BidKingOverlayAbsenceQualified) {
  # Only this provider's qualified empty result proves absence.  Unidentified
  # Python rows, fallback, an old observation, or NoOverlay do not prove it.
  $script:BidKingLaunchState.OverlayState = "not_created"
}
if (-not $NoOverlay -and -not $OverlayProcs) {
  # --ui-prefs: 打包版启动器启用 UI 偏好持久化 —— 配色主题/窗口位置大小/展开态存到
  # data/logs/live/hero_ref_ui_prefs.json, 下次启动自动恢复(群友选的配色不用每次重选)。
  # 设计意图见 ahmad_tk_overlay --ui-prefs 的 help("Packaged Start-HeroRef enables this; dev scripts leave it off");
  # 本 ps1 = 打包/一键启动路径, 此前漏接这个 flag -> 群友配色一直不记忆。(装机内持久; 换新包会重置=可接受)
  $OverlayArgs = @(
    "-B",
    (Q $Overlay), "--snapshot", (Q $Snapshot),
    "--diagnostic-profile", $DiagnosticProfile, "--ui-prefs"
  )
  if (-not $NoTaskbar) { $OverlayArgs += "--show-taskbar" }  # default ON (borderless + taskbar); -NoTaskbar opts out
  if ($MonitorPid) {
    $OverlayArgs += @("--exit-when-pid-exits", "$MonitorPid")
    if ($UseWssCapture) {
      $OverlayArgs += "--protect-monitor-while-game-running"
    }
    if ($KeepMonitorOnClose) {
      $OverlayArgs += "--keep-monitor-on-close"
    }
    # Keep the normal cleanup targets wired for both capture paths. The WSS overlay
    # dynamically suppresses that cleanup only while the game may still be active;
    # after the game exits an ordinary close must still reclaim the monitor.
    if (-not $KeepMonitorOnClose) {
      $OverlayArgs += @(
        "--stop-pid-on-exit", "$MonitorPid",
        "--cleanup-lock-on-exit", (Q $LockPath)
      )
    }
  }
  $PythonWindowed = Resolve-BidKingWindowedPythonPath `
    -PythonPath $PythonPath `
    -AllowConsoleFallback
  # 重定向 overlay stderr: pythonw 无控制台, 硬 import 崩(如杀软删了依赖)本会静默闪退、无任何痕迹。
  # 落盘 stderr 让下面的存活守护能 tail 出真因, 也便于"收集诊断.bat"回传。
  "" | Set-Content -Path $OverlayErr -Encoding utf8
  # Registration can fail after process creation; until the returned object is
  # bound, absence is no longer established by the pre-spawn enumeration.
  $script:BidKingLaunchState.OverlayState = "preserved_unknown"
  Invalidate-BidKingProcessInventory -Reason "process_start" -StatusPath $StartupStatus
  try {
    $StartedOverlay = Start-Process -FilePath $PythonWindowed -WorkingDirectory $Repo -ArgumentList $OverlayArgs -PassThru -RedirectStandardError $OverlayErr
    $script:BidKingLaunchState.Overlay = New-BidKingOwnedStartupProcess $StartedOverlay "overlay" $Overlay
    $script:BidKingLaunchState.OverlayState = "alive"
  } catch {
    $StartupFailureExit = Write-BidKingStartupFailure "overlay_spawn_failed"
    Show-FatalBox "悬浮窗未能启动。请保留诊断，关闭游戏后重试。" "BidKing Live · 悬浮窗启动失败"
    exit $StartupFailureExit
  } finally {
    Invalidate-BidKingProcessInventory -Reason "process_start" -StatusPath $StartupStatus
  }
  if ($StartedOverlay -and $StartedOverlay.Id) {
    $OverlayStartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $overlayState = @{
      schema_version = 2
      pid = [int]$StartedOverlay.Id
      started_at = $OverlayStartedAt
      kind = "ahmad_overlay"
      launch_id = $LaunchId
      overlay_path = $Overlay
      package_generation_identity = $ExpectedPackageGenerationIdentity
    } | ConvertTo-Json -Compress
    $OverlayPidTmp = "$OverlayPidPath.tmp-$PID"
    [System.IO.File]::WriteAllText(
      $OverlayPidTmp,
      $overlayState,
      (New-Object System.Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $OverlayPidTmp -Destination $OverlayPidPath -Force
    $OverlayPid = [int]$StartedOverlay.Id
    Write-OverlayLogSessionState `
      ([int]$StartedOverlay.Id) $OverlayStartedAt "started" "running" $null
    Add-Content -Path $StartupStatus -Value (
      "{0} overlay_started launch_id={1}" -f (Get-Date -Format o), $LaunchId
    ) -Encoding utf8
  }
} elseif (-not $NoOverlay -and $OverlayProcs) {
  $OverlayPid = [int]$OverlayProcs[0].ProcessId
  Add-Content -Path $StartupStatus -Value (
    "{0} overlay_reused launch_id={1} overlay_pid={2}" -f `
      (Get-Date -Format o), $LaunchId, $OverlayPid
  ) -Encoding utf8
}
if (-not $NoOverlay) {
  $null = Write-BidKingStartupTiming "powershell_overlay" $(
    if ($OverlayPid) { "ok" } else { "failed" }
  )
}

# Capture-readiness guard: the overlay is wired to --exit-when-pid-exits the monitor.
# If Npcap/capture isn't ready the monitor dies a few seconds in (after enumerating
# devices) and the overlay would just silently vanish ("页面出来又消失"). Wait for that
# window to pass, then surface a clear reason via MessageBox instead of a silent close.
# ⚠️ -Restart 时【跳过】本守卫: 重启是用户主动恢复动作,-Restart 刚强杀了旧 monitor,旧的长命
# monitor(握着 Npcap 句柄)释放慢 → 这 4s 窗口里 $MonitorPid 可能瞬时查不到 → 误弹"抓包未就绪"
# (作者/群友实测: 第一次重启必弹、第二次不弹, 打包/源码都会)。而重启其实成功(UI+新 monitor 都起来了)。
# 守卫本意是【首次启动】的 Npcap 未就绪检测, 不该管重启churn; 真失败用户再点一次重启即可, 且 overlay
# 自带的 monitor 退出说明框(_notify_watched_monitor_exit)会兜住重启后 monitor 真死的情况。
if ($MonitorPid -and -not $NoOverlay -and -not $Restart) {
  $null = Write-BidKingStartupTiming "startup_guard" "begin"
  $GuardDeadline = (Get-Date).AddSeconds(4)
  $ProbeDelayMs = 100
  do {
    $MonitorProbe = Get-PidProbeState ([int]$MonitorPid)
    $OverlayProbe = if ($StartedOverlay) {
      Get-StartedProcessProbeState $StartedOverlay
    } elseif ($OverlayPid) {
      Get-PidProbeState ([int]$OverlayPid)
    } else {
      [pscustomobject]@{ State = "dead"; ExitCode = $null }
    }
    $ReadinessState = if ($UseWssCapture) {
      Get-BidKingMonitorReadinessState $NetworkConnectionStatus $LaunchId
    } else {
      "not_ready"
    }
    $DeadlineReached = (Get-Date) -ge $GuardDeadline
    $GuardDecision = Get-BidKingStartupGuardDecision `
      $MonitorProbe.State $OverlayProbe.State $OverlayProbe.ExitCode `
      $ReadinessState $DeadlineReached
    if ($GuardDecision -eq "wait") {
      $RemainingMs = [Math]::Max(
        1,
        [int][Math]::Ceiling(($GuardDeadline - (Get-Date)).TotalMilliseconds)
      )
      Start-Sleep -Milliseconds ([Math]::Min($ProbeDelayMs, $RemainingMs))
      $ProbeDelayMs = [Math]::Min(1000, $ProbeDelayMs * 2)
    }
  } while ($GuardDecision -eq "wait")
  $null = Write-BidKingStartupTiming "startup_guard" $(
    if ($GuardDecision -in @("ready", "not_ready")) { "ok" } else { "failed" }
  )
  $OverlayExitClass = if ($GuardDecision -in @("ready", "not_ready")) {
    "running"
  } else {
    $GuardDecision
  }
  if ($StartedOverlay -and $StartedOverlay.Id) {
    Write-OverlayLogSessionState `
      ([int]$StartedOverlay.Id) $OverlayStartedAt `
      $OverlayProbe.State $OverlayExitClass $OverlayProbe.ExitCode
  }
  Add-Content -Path $StartupStatus -Value (
    "{0} overlay_guard launch_id={1} monitor_state={2} overlay_state={3} readiness={4} decision={5} exit_class={6} exit_code={7} elapsed_ms={8}" -f `
      (Get-Date -Format o), $LaunchId, $MonitorProbe.State, $OverlayProbe.State,
      $ReadinessState, $GuardDecision, $OverlayExitClass,
      $(if ($null -eq $OverlayProbe.ExitCode) { "none" } else { [int]$OverlayProbe.ExitCode }),
      [int]$LaunchStopwatch.ElapsedMilliseconds
  ) -Encoding utf8
  if ($GuardDecision -eq "not_ready") {
    Write-BidKingStartupStatus -StatusPath $StartupStatus -Event (
      "startup_guard_not_ready monitor_state={0} overlay_state={1} elapsed_ms={2}" -f `
        $MonitorProbe.State, $OverlayProbe.State, [int]$LaunchStopwatch.ElapsedMilliseconds
    )
  }
  # Only pop a failure box if a process died or its state is unsafe to decide.
  if ($OverlayExitClass -ne "running") {
    $StartupFailureExit = Write-BidKingStartupFailure $OverlayExitClass
    if ($OverlayExitClass -eq "process_query_unknown") {
      $script:BidKingLaunchState.PreserveChildren = $true
    }
    # 分类只消费进程状态与退出码，不把异常正文或本机绝对路径带进弹窗/sidecar。
    if ($OverlayExitClass -eq "process_query_unknown") {
      Show-FatalBox -Msg (
        "无法确认悬浮窗或后台读取进程状态。`n`n请先查看悬浮窗是否已正常出现；如未出现，不要反复重启或更换 token，请运行「诊断工具\收集诊断.bat」并把诊断私发作者。"
      ) -Title "BidKing Live · 进程状态未知"
    } elseif ($OverlayExitClass -eq "overlay_exception") {
      # Case B: 悬浮窗自身崩溃(overlay.stderr 有 traceback, 或 monitor 仍在而 overlay 没了) —— 不是 Npcap
      Show-FatalBox -Msg (
        "悬浮窗启动后退出。`n`n请按顺序处理：`n  1) 把程序文件夹加入杀毒软件白名单；`n  2) 重新完整解压安装包后重试；`n  3) 仍然失败时运行「诊断工具\收集诊断.bat」，把生成的诊断私发作者。"
      ) -Title "BidKing Live · 悬浮窗启动失败"
    } elseif ($OverlayExitClass -eq "overlay_exited_cleanly") {
      Show-FatalBox -Msg (
        "悬浮窗启动后已正常退出，但后台读取仍在运行。`n`n请运行「诊断工具\收集诊断.bat」并把诊断私发作者；无需重装、重启电脑或更换 token。"
      ) -Title "BidKing Live · 悬浮窗已退出"
    } elseif ($OverlayExitClass -eq "monitor_exit" -and $UseWssCapture) {
      $MonitorFailureClass = Get-BidKingWssHelperFailureClassification `
        -StatusPath $WssHelperStatus `
        -ExpectedLaunchId $LaunchId `
        -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
        -ExpectedRuntimeIdentity $TemporaryTrustRuntimeIdentity `
        -ExpectedComponentIdentity $TemporaryTrustComponentIdentity `
        -ExpectedOwnerSid $TemporaryTrustOwnerSid `
        -ExpectedMonitorProcessId $MonitorPid `
        -ExpectedMonitorStartedAtMs $MonitorStartedAtMs
      if ($MonitorFailureClass -in @("driver_load_denied", "component_execution_blocked")) {
        $FailureResult = Get-BidKingInternetCafeResult $MonitorFailureClass "monitor_guard"
        try { $null = Write-BidKingInternetCafeReceipt $FailureResult } catch {}
        Show-BidKingInternetCafeFailure $MonitorFailureClass
      } elseif ($MonitorFailureClass -in @(
        "wss_same_path_mixed_generation",
        "wss_stale_product_service_conflict",
        "wss_stale_product_service_repaired_restart_required"
      )) {
        Show-BidKingWssServiceFailure $MonitorFailureClass
      } else {
        Show-FatalBox -Msg (
          "本地数据读取组件启动后退出，悬浮窗已自动关闭。`n`n请按顺序处理：`n  1) 完全退出游戏；`n  2) 重新启动 Hero Ref，等悬浮窗出现；`n  3) 再从 Steam 启动游戏；`n  4) 仍然失败时运行「诊断工具\网吧环境兼容检查.bat」和「收集诊断.bat」。`n`n更换 token、抓包驱动或解压路径不能绕过网吧策略。"
        ) -Title "BidKing Live · 数据读取未就绪"
      }
    } elseif ($OverlayExitClass -eq "monitor_exit") {
      # Case A: monitor 自己退出且 overlay 无崩溃迹象 = 真·抓包未就绪(Npcap)
      Show-FatalBox -Msg (
        "抓包监控启动后退出，悬浮窗已自动关闭。`n`n最常见原因：抓包驱动 Npcap 未安装或未就绪。请按顺序排查：`n  1) 运行包内 vendor\npcap-installer.exe 安装 Npcap（保持默认、一路 Next）；`n  2) 若刚装好仍不行，请【重启电脑】后再打开（驱动需重启才生效）；`n  3) 重启后重新打开 BidKingLive.exe。`n`n仍不行时请运行「诊断工具\收集诊断.bat」（悬浮窗可用时也可点「导出」）；生成文件会在「文档\BidKingLive\exports」中打开，请仅私发支持人员，不要发送原始日志。"
      ) -Title "BidKing Live · 抓包未就绪"
    }
    exit $StartupFailureExit
  }
} else {
  $null = Write-BidKingStartupTiming "startup_guard" "skipped"
}

$script:BidKingLaunchState.Succeeded = $true
$null = Write-BidKingStartupTiming "powershell_launch" "ok"
$null = Write-BidKingStartupTiming "startup_terminal" "ok"
Write-Host "BidKing Live 已启动。" -ForegroundColor Green
if ($UseWssCapture) {
  Write-Host "下一步：等悬浮窗出现后，再启动游戏。"
} else {
  Write-Host "兼容模式已启动，请等待悬浮窗。"
}
if ($StartedOverlay -and $StartedOverlay.Id) {
  if ($UseWssCapture) {
    Write-Host "关闭悬浮窗不会中断正在进行的对局；游戏退出后后台读取会自动结束。"
  } else {
    Write-Host "关闭悬浮窗会同时结束后台读取。"
  }
} elseif ($NoOverlay) {
  Write-Host "已按要求仅启动后台读取。"
}
} catch {
  $script:BidKingLaunchState.Succeeded = $false
  $null = Write-BidKingStartupTiming "powershell_launch" "failed"
  throw
} finally {
  Complete-BidKingStartupAttempt
}
