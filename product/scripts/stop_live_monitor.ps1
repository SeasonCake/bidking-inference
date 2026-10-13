param(
  [string]$LogDir = "data\logs\live"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogPath = Join-Path $Repo $LogDir
$LockPath = Join-Path $LogPath "monitor.lock"

$LockPid = $null
if (Test-Path $LockPath) {
  try {
    $Lock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
    if ($Lock.pid) {
      $LockPid = [int]$Lock.pid
    }
  } catch {
    $LockPid = $null
  }
}

$MonitorProcesses = @(
  Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    (
      $_.CommandLine -like "*run_fatbeans_live_monitor.py*" -or
      $_.CommandLine -like "*run_fatbeans_webhook_monitor.py*" -or
      $_.CommandLine -like "*run_windivert_live_monitor.py*"
    )
  }
)
$OverlayProcesses = @(
  Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -like "*run_live_overlay.py*"
  }
)

$ProcessIds = New-Object System.Collections.Generic.HashSet[int]
foreach ($Process in @($MonitorProcesses + $OverlayProcesses)) {
  $null = $ProcessIds.Add([int]$Process.ProcessId)
}
if ($LockPid) {
  $LockProcess = Get-Process -Id $LockPid -ErrorAction SilentlyContinue
  if ($LockProcess -and $LockProcess.ProcessName -like "python*") {
    $null = $ProcessIds.Add($LockPid)
  }
}

foreach ($ProcessId in $ProcessIds) {
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

if (Test-Path $LockPath) {
  Remove-Item -LiteralPath $LockPath -Force
}

Write-Host "Stopped BidKing live monitor processes: $($ProcessIds.Count)" -ForegroundColor Green
Write-Host "Stopped BidKing overlay processes: $($OverlayProcesses.Count)" -ForegroundColor Green
Write-Host "LogDir: $LogPath"
