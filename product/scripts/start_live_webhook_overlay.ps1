param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8765,
  [string]$WebhookPath = "/fatbeans",
  [string]$LogDir = "data\logs\live",
  [string]$ProcessName = "BidKing.exe",
  [int[]]$ServerPort = @(10000),
  [int]$NTrials = 500,
  [int]$RoiTrials = 0,
  [int]$FastNTrials = 10,
  [double]$DebounceSeconds = 1.0,
  [double]$MinInferenceIntervalSeconds = 2.0,
  [switch]$EnableDebugShadows,
  [string]$FatbeansPath = "C:\Users\example\Desktop\FatbeansCreaterV1.0.3\FatbeansCreater.exe",
  [switch]$StartFatbeans,
  [switch]$KeepMonitorOnOverlayClose,
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $Repo "scripts\resolve_python.ps1")
$Python = Resolve-BidKingPython
$PythonWindowed = Resolve-BidKingPythonw -PythonExe $Python
$Monitor = Join-Path $Repo "scripts\run_fatbeans_webhook_monitor.py"
$Overlay = Join-Path $Repo "scripts\run_live_overlay.py"
$LogPath = Join-Path $Repo $LogDir
$LockPath = Join-Path $LogPath "monitor.lock"
$MonitorOut = Join-Path $LogPath "monitor.stdout.log"
$MonitorErr = Join-Path $LogPath "monitor.stderr.log"

$MonitorArgs = @(
  $Monitor,
  "--host", $HostName,
  "--port", "$Port",
  "--path", $WebhookPath,
  "--log-dir", $LogPath,
  "--process-name", $ProcessName,
  "--n-trials", "$NTrials",
  "--roi-trials", "$RoiTrials",
  "--fast-n-trials", "$FastNTrials",
  "--debounce-seconds", "$DebounceSeconds",
  "--min-inference-interval-seconds", "$MinInferenceIntervalSeconds"
)
if (-not $EnableDebugShadows) {
  $MonitorArgs += "--skip-debug-shadows"
}
foreach ($PortValue in $ServerPort) {
  $MonitorArgs += @("--server-port", "$PortValue")
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
    (
      $_.CommandLine -like "*run_fatbeans_webhook_monitor.py*" -or
      $_.CommandLine -like "*run_fatbeans_live_monitor.py*"
    ) -and
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

if ($StartFatbeans) {
  $FatbeansRunning = @(
    Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "FatbeansCreater.exe" }
  )
  if (-not $FatbeansRunning) {
    if (-not (Test-Path $FatbeansPath)) {
      throw "FatbeansCreater.exe not found: $FatbeansPath"
    }
    Start-Process -FilePath $FatbeansPath -WorkingDirectory (Split-Path -Parent $FatbeansPath)
  }
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

$WebhookUrl = "http://$HostName`:$Port$WebhookPath"
Write-Host "BidKing Fatbeans WebHook monitor started." -ForegroundColor Green
Write-Host "WebHook URL: $WebhookUrl"
Write-Host "LogDir:      $LogPath"
Write-Host "Process:     $ProcessName"
Write-Host "ServerPort:  $($ServerPort -join ',')"
Write-Host "Note: configure Fatbeans WebHook to POST to the URL above, then start capture with BidKing.exe filter."
if ($MonitorProcesses) {
  Write-Host "Monitor:     already running (PID $($MonitorProcesses[0].ProcessId))"
}
if ($OverlayProcesses) {
  Write-Host "Overlay:     already running (PID $($OverlayProcesses[0].ProcessId))"
}
if (-not $KeepMonitorOnOverlayClose -and $StartedMonitorPid) {
  Write-Host "Lifecycle:   closing overlay will stop monitor PID $StartedMonitorPid"
}
