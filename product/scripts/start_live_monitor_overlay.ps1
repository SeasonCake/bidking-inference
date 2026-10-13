param(
  [string]$WatchDir = "",
  [string]$LogDir = "data\logs\live",
  [int]$NTrials = 500,
  [int]$RoiTrials = 250,
  [double]$StableSeconds = 1.0,
  [double]$ProcessDelaySeconds = 1.5,
  [switch]$ProcessExisting,
  [switch]$ReprocessExisting,
  [switch]$KeepMonitorOnOverlayClose,
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $WatchDir) {
  $WatchDir = Join-Path $Repo "data\samples\fatbeans"
}
. (Join-Path $Repo "scripts\resolve_python.ps1")
$Python = Resolve-BidKingPython
$PythonWindowed = Resolve-BidKingPythonw -PythonExe $Python
$Monitor = Join-Path $Repo "scripts\run_fatbeans_live_monitor.py"
$Overlay = Join-Path $Repo "scripts\run_live_overlay.py"
$LogPath = Join-Path $Repo $LogDir
$LockPath = Join-Path $LogPath "monitor.lock"
$MonitorOut = Join-Path $LogPath "monitor.stdout.log"
$MonitorErr = Join-Path $LogPath "monitor.stderr.log"
$MonitorArgs = @(
  $Monitor,
  "--watch-dir", $WatchDir,
  "--log-dir", $LogPath,
  "--n-trials", "$NTrials",
  "--roi-trials", "$RoiTrials",
  "--stable-seconds", "$StableSeconds",
  "--process-delay-seconds", "$ProcessDelaySeconds"
)
if ($ReprocessExisting) {
  $MonitorArgs += "--reprocess-existing-once"
}
if (-not $ProcessExisting -and -not $ReprocessExisting) {
  $MonitorArgs += "--ignore-existing"
}

function Get-ProcessIdValue {
  param($Process)
  if (-not $Process) {
    return $null
  }
  if ($Process.PSObject.Properties.Name -contains "ProcessId" -and $Process.ProcessId) {
    return [int]$Process.ProcessId
  }
  if ($Process.PSObject.Properties.Name -contains "Id" -and $Process.Id) {
    return [int]$Process.Id
  }
  return $null
}

New-Item -ItemType Directory -Path $LogPath -Force | Out-Null

$MonitorProcesses = @(
  Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -like "*run_fatbeans_live_monitor.py*" -and
    $_.CommandLine -like "*$LogPath*"
  }
)
$OverlayProcesses = @(
  Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -like "*run_live_overlay.py*" -and
    $_.CommandLine -like "*latest_snapshot.json*"
  }
)

if ($Restart) {
  foreach ($Process in @($MonitorProcesses + $OverlayProcesses)) {
    Stop-Process -Id $Process.ProcessId -Force
  }
  $MonitorProcesses = @()
  $OverlayProcesses = @()
  if (Test-Path $LockPath) {
    Remove-Item -LiteralPath $LockPath -Force
  }
}

if ((Test-Path $LockPath) -and -not $MonitorProcesses) {
  Remove-Item -LiteralPath $LockPath -Force
}

$StartedMonitor = $null
if (-not $MonitorProcesses) {
  $StartedMonitor = Start-Process -FilePath $Python -WorkingDirectory $Repo -WindowStyle Hidden -PassThru -ArgumentList @(
    $MonitorArgs
  ) -RedirectStandardOutput $MonitorOut -RedirectStandardError $MonitorErr
} else {
  $StartedMonitor = $MonitorProcesses[0]
}
$StartedMonitorPid = Get-ProcessIdValue $StartedMonitor

if (-not $OverlayProcesses) {
  $OverlayArgs = @(
    $Overlay,
    "--snapshot", (Join-Path $LogPath "latest_snapshot.json")
  )
  if (-not $KeepMonitorOnOverlayClose -and $StartedMonitorPid) {
    $OverlayArgs += @(
      "--stop-pid-on-exit", "$StartedMonitorPid",
      "--cleanup-lock-on-exit", $LockPath
    )
  }
  Start-Process -FilePath $PythonWindowed -WorkingDirectory $Repo -ArgumentList $OverlayArgs
}

Write-Host "BidKing live monitor started." -ForegroundColor Green
Write-Host "WatchDir: $WatchDir"
Write-Host "LogDir:   $LogPath"
Write-Host "Delay:    $ProcessDelaySeconds sec between processed files"
if ($ReprocessExisting) {
  Write-Host "Replay:   existing files will be reprocessed once"
}
if ($MonitorProcesses) {
  Write-Host "Monitor:  already running (PID $($MonitorProcesses[0].ProcessId))"
}
if ($OverlayProcesses) {
  Write-Host "Overlay:  already running (PID $($OverlayProcesses[0].ProcessId))"
}
if (-not $KeepMonitorOnOverlayClose -and $StartedMonitorPid) {
  Write-Host "Lifecycle: closing overlay will stop monitor PID $StartedMonitorPid"
}
