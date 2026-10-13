# Start overlay only when WinDivert monitor is already running.
param(
  [string]$LogDir = "data\logs\live"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $Repo "scripts\resolve_python.ps1")
$Python = Resolve-BidKingPython
$PythonWindowed = Resolve-BidKingPythonw -PythonExe $Python
$Overlay = Join-Path $Repo "scripts\run_live_overlay.py"
$LogPath = Join-Path $Repo $LogDir
$Snapshot = Join-Path $LogPath "latest_snapshot.json"

$OverlayProcesses = @(
  Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -like "*run_live_overlay.py*" -and
    $_.CommandLine -like "*latest_snapshot.json*"
  }
)
if ($OverlayProcesses) {
  Write-Host "Overlay already running (PID $($OverlayProcesses[0].ProcessId))." -ForegroundColor Yellow
  exit 0
}

$MonitorProcesses = @(
  Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -like "*run_windivert_live_monitor.py*"
  }
)
if (-not $MonitorProcesses) {
  Write-Host "No WinDivert monitor found. Run start_live_windivert_overlay.ps1 -Restart first." -ForegroundColor Red
  exit 1
}

$OverlayArgs = @(
  $Overlay,
  "--snapshot", $Snapshot
)
# Do not redirect pythonw stdout/stderr (can break Tk on Windows).
$StartedOverlay = Start-Process -FilePath $PythonWindowed -WorkingDirectory $Repo -ArgumentList $OverlayArgs -PassThru
$OverlayPidPath = Join-Path $LogPath "overlay.pid"
if ($StartedOverlay -and $StartedOverlay.Id) {
  Set-Content -Path $OverlayPidPath -Value "$($StartedOverlay.Id)" -Encoding ascii
  Write-Host "Overlay started (PID $($StartedOverlay.Id))." -ForegroundColor Green
}
Write-Host "Snapshot: $Snapshot"
