param(
  [string]$LogDir = "data\logs\live",
  [string]$ProcessName = "BidKing.exe",
  [string]$PythonPath = "C:\Python313\python.exe",
  [int[]]$ServerPort = @(10000),
  [int]$NTrials = 500,
  [int]$RoiTrials = 0,
  [int]$FullShadowTrials = 20,
  [int]$FastNTrials = 10,
  [ValidateSet("v3_practical", "v2")]
  [string]$FormalMode = "v3_practical",
  [double]$DebounceSeconds = 1.0,
  [double]$MinInferenceIntervalSeconds = 2.0,
  [switch]$BroadSniff,
  [switch]$PortOnly,
  [switch]$IncludeLoopback,
  [switch]$ExcludeLoopback,
  [switch]$EnableDebugShadows,
  [switch]$KeepMonitorOnOverlayClose,
  [switch]$NoOverlay,
  [switch]$NoAutoElevate,
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Monitor = Join-Path $Repo "scripts\run_windivert_live_monitor.py"
$Overlay = Join-Path $Repo "scripts\run_live_overlay.py"
$LogPath = Join-Path $Repo $LogDir
$LockPath = Join-Path $LogPath "monitor.lock"
$MonitorOut = Join-Path $LogPath "monitor.stdout.log"
$MonitorErr = Join-Path $LogPath "monitor.stderr.log"
$OverlayOut = Join-Path $LogPath "overlay.stdout.log"
$OverlayErr = Join-Path $LogPath "overlay.stderr.log"
$OverlayPidPath = Join-Path $LogPath "overlay.pid"

# Default: port-filter capture (low CPU). Use -BroadSniff for VPN/TUN/proxy diagnosis.
$UseBroadSniff = [bool]$BroadSniff
if ($PortOnly) {
  $UseBroadSniff = $false
}
$UseLoopback = [bool]$IncludeLoopback -and -not $ExcludeLoopback

$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltinRole]::Administrator
)

function Get-CurrentPowerShellPath {
  try {
    $Current = Get-Process -Id $PID -ErrorAction Stop
    if ($Current.Path) {
      return $Current.Path
    }
  } catch {
  }
  $Pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($Pwsh) {
    return $Pwsh.Source
  }
  $PowerShell = Get-Command powershell -ErrorAction SilentlyContinue
  if ($PowerShell) {
    return $PowerShell.Source
  }
  throw "PowerShell executable not found for elevation"
}

if (-not $IsAdmin -and -not $NoAutoElevate) {
  $ElevatedArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $PSCommandPath,
    "-LogDir", $LogDir,
    "-ProcessName", $ProcessName,
    "-NTrials", "$NTrials",
    "-RoiTrials", "$RoiTrials",
    "-FullShadowTrials", "$FullShadowTrials",
    "-FastNTrials", "$FastNTrials",
    "-FormalMode", "$FormalMode",
    "-DebounceSeconds", "$DebounceSeconds",
    "-MinInferenceIntervalSeconds", "$MinInferenceIntervalSeconds",
    "-NoAutoElevate"
  )
  if ($PythonPath) {
    $ElevatedArgs += @("-PythonPath", $PythonPath)
  }
  foreach ($PortValue in $ServerPort) {
    $ElevatedArgs += @("-ServerPort", "$PortValue")
  }
  if ($UseBroadSniff) {
    $ElevatedArgs += "-BroadSniff"
  }
  if ($UseLoopback) {
    $ElevatedArgs += "-IncludeLoopback"
  }
  if ($EnableDebugShadows) {
    $ElevatedArgs += "-EnableDebugShadows"
  }
  if ($KeepMonitorOnOverlayClose) {
    $ElevatedArgs += "-KeepMonitorOnOverlayClose"
  }
  if ($NoOverlay) {
    $ElevatedArgs += "-NoOverlay"
  }
  if ($Restart) {
    $ElevatedArgs += "-Restart"
  }
  $PowerShellPath = Get-CurrentPowerShellPath
  Start-Process -FilePath $PowerShellPath -Verb RunAs -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList $ElevatedArgs
  Write-Host "WinDivert requires Administrator. Relaunched an elevated hidden PowerShell for live monitor." -ForegroundColor Yellow
  return
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

function Get-LockProcessIdValue {
  param([string]$Path)
  if (-not (Test-Path $Path)) {
    return $null
  }
  try {
    $Payload = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($Payload.pid) {
      return [int]$Payload.pid
    }
  } catch {
  }
  return $null
}

function Test-ProcessIdRunning {
  param($ProcessId)
  if (-not $ProcessId) {
    return $false
  }
  try {
    $null = Get-Process -Id ([int]$ProcessId) -ErrorAction Stop
    return $true
  } catch {
    return $false
  }
}

. (Join-Path $Repo "scripts\resolve_python.ps1")
$Python = Resolve-BidKingPython -ExplicitPython $PythonPath -RequirePacket
$PythonWindowed = Resolve-BidKingPythonw -PythonExe $Python
$HasPacketDeps = Test-BidKingPython -Candidate $Python -RequirePacket

$MonitorArgs = @(
  $Monitor,
  "--log-dir", $LogPath,
  "--process-name", $ProcessName,
  "--n-trials", "$NTrials",
  "--roi-trials", "$RoiTrials",
  "--full-shadow-trials", "$FullShadowTrials",
  "--fast-n-trials", "$FastNTrials",
  "--formal-mode", "$FormalMode",
  "--debounce-seconds", "$DebounceSeconds",
  "--min-inference-interval-seconds", "$MinInferenceIntervalSeconds"
)
foreach ($PortValue in $ServerPort) {
  $MonitorArgs += @("--server-port", "$PortValue")
}
if ($UseBroadSniff) {
  $MonitorArgs += "--broad"
}
if ($UseLoopback) {
  $MonitorArgs += "--include-loopback"
}
if (-not $EnableDebugShadows) {
  $MonitorArgs += "--skip-debug-shadows"
}

New-Item -ItemType Directory -Path $LogPath -Force | Out-Null

$MonitorProcesses = @(
  Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    (
      $_.CommandLine -like "*run_windivert_live_monitor.py*" -or
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
  $OverlayPidFromFile = $null
  if (Test-Path $OverlayPidPath) {
    try {
      $OverlayPidFromFile = [int]((Get-Content -LiteralPath $OverlayPidPath -Raw).Trim())
    } catch {
    }
  }
  if (
    $OverlayPidFromFile -and
    -not ($OverlayProcesses | Where-Object { $_.ProcessId -eq $OverlayPidFromFile }) -and
    (Test-ProcessIdRunning -ProcessId $OverlayPidFromFile)
  ) {
    try {
      $OverlayProcessFromFile = Get-CimInstance Win32_Process -Filter "ProcessId=$OverlayPidFromFile" -ErrorAction Stop
      if ($OverlayProcessFromFile.Name -like "python*") {
        Stop-Process -Id $OverlayPidFromFile -Force -ErrorAction SilentlyContinue
      }
    } catch {
    }
  }
  $LockPid = Get-LockProcessIdValue -Path $LockPath
  if ($LockPid -and (Test-ProcessIdRunning -ProcessId $LockPid)) {
    Stop-Process -Id $LockPid -Force -ErrorAction SilentlyContinue
  }
  $MonitorProcesses = @()
  $OverlayProcesses = @()
  if (Test-Path $LockPath) {
    Remove-Item -LiteralPath $LockPath -Force
  }
  if (Test-Path $OverlayPidPath) {
    Remove-Item -LiteralPath $OverlayPidPath -Force
  }
}

$ExistingMonitorPidFromLock = $null
if (-not $MonitorProcesses) {
  $LockPid = Get-LockProcessIdValue -Path $LockPath
  if ($LockPid -and (Test-ProcessIdRunning -ProcessId $LockPid)) {
    $ExistingMonitorPidFromLock = $LockPid
  }
}

if ((Test-Path $LockPath) -and -not $MonitorProcesses -and -not $ExistingMonitorPidFromLock) {
  Remove-Item -LiteralPath $LockPath -Force
}

$StartedMonitor = $null
$StartedOverlay = $null
if (-not $MonitorProcesses -and -not $ExistingMonitorPidFromLock) {
  "" | Set-Content -Path $MonitorOut -Encoding utf8
  "" | Set-Content -Path $MonitorErr -Encoding utf8
  $MonitorStartParams = @{
    FilePath = $Python
    WorkingDirectory = $Repo
    WindowStyle = "Hidden"
    PassThru = $true
    ArgumentList = $MonitorArgs
    RedirectStandardOutput = $MonitorOut
    RedirectStandardError = $MonitorErr
  }
  if (-not $IsAdmin) {
    $MonitorStartParams["Verb"] = "RunAs"
  }
  $StartedMonitor = Start-Process @MonitorStartParams
} elseif ($MonitorProcesses) {
  $StartedMonitor = $MonitorProcesses[0]
}
$StartedMonitorPid = Get-ProcessIdValue $StartedMonitor
if (-not $StartedMonitorPid -and $ExistingMonitorPidFromLock) {
  $StartedMonitorPid = $ExistingMonitorPidFromLock
}

function Get-MonitorStderrTail {
  param([string]$StderrLog)
  if (-not (Test-Path $StderrLog)) {
    return ""
  }
  return (Get-Content $StderrLog -Tail 40 -ErrorAction SilentlyContinue | Out-String)
}

function Test-MonitorStartupHealthy {
  param(
    $Process,
    [string]$StderrLog,
    [double]$WaitSeconds = 2.5
  )
  Start-Sleep -Seconds $WaitSeconds
  $StderrText = Get-MonitorStderrTail -StderrLog $StderrLog
  if ($StderrText -match "elevated PowerShell/admin") {
    return @{
      Ok = $false
      Reason = "windivert_requires_admin"
      Stderr = $StderrText
    }
  }
  if ($StderrText -match "\[error\]") {
    return @{
      Ok = $false
      Reason = "monitor_reported_error"
      Stderr = $StderrText
    }
  }
  $PidValue = Get-ProcessIdValue $Process
  if (-not $PidValue) {
    return @{
      Ok = $false
      Reason = "monitor_process_exited"
      Stderr = $StderrText
    }
  }
  try {
    $null = Get-Process -Id $PidValue -ErrorAction Stop
  } catch {
    return @{
      Ok = $false
      Reason = "monitor_process_exited"
      Stderr = $StderrText
    }
  }
  return @{ Ok = $true; Reason = "ok"; Pid = $PidValue; Stderr = $StderrText }
}

$MonitorHealth = @{ Ok = $true }
if ($StartedMonitor -and -not $MonitorProcesses) {
  $MonitorHealth = Test-MonitorStartupHealthy -Process $StartedMonitor -StderrLog $MonitorErr
  if (-not $MonitorHealth.Ok) {
    $DeadPid = Get-ProcessIdValue $StartedMonitor
    if ($DeadPid) {
      Stop-Process -Id $DeadPid -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $LockPath) {
      Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
    Write-Host ""
    Write-Host "Monitor failed to stay running." -ForegroundColor Red
    if ($MonitorHealth.Reason -eq "windivert_requires_admin") {
      Write-Host "WinDivert still sees a non-elevated Python process." -ForegroundColor Red
      Write-Host "Even in Admin PowerShell, the hidden monitor child may need a fresh UAC grant." -ForegroundColor Yellow
      Write-Host "Try in THIS admin window with the SAME Python as monitor:" -ForegroundColor Yellow
      Write-Host "  & `"$Python`" scripts\diagnose_windivert.py" -ForegroundColor Yellow
      Write-Host "If diagnose shows elevated=False, close all terminals and open a new" -ForegroundColor Yellow
      Write-Host "PowerShell via right-click -> Run as administrator, then -Restart again." -ForegroundColor Yellow
    } else {
      Write-Host "Check: $MonitorErr" -ForegroundColor Yellow
    }
    if ($MonitorHealth.Stderr) {
      Write-Host ""
      Write-Host "monitor.stderr.log (tail):" -ForegroundColor DarkYellow
      Write-Host $MonitorHealth.Stderr
    }
    Write-Host "Overlay was not started because the monitor exited immediately." -ForegroundColor Yellow
    exit 1
  }
  $StartedMonitorPid = $MonitorHealth.Pid
}

if (-not $NoOverlay -and -not $OverlayProcesses) {
  $OverlayArgs = @(
    $Overlay,
    "--snapshot", (Join-Path $LogPath "latest_snapshot.json")
  )
  if ($StartedMonitorPid -and -not $KeepMonitorOnOverlayClose) {
    $OverlayArgs += @(
      "--exit-when-pid-exits", "$StartedMonitorPid"
    )
  }
  if (-not $KeepMonitorOnOverlayClose -and $StartedMonitorPid) {
    $OverlayArgs += @(
      "--stop-pid-on-exit", "$StartedMonitorPid",
      "--cleanup-lock-on-exit", $LockPath
    )
  }
  # Do not redirect pythonw stdout/stderr: it can prevent the Tk window from starting.
  $StartedOverlay = Start-Process -FilePath $PythonWindowed -WorkingDirectory $Repo -ArgumentList $OverlayArgs -PassThru
  if ($StartedOverlay -and $StartedOverlay.Id) {
    Set-Content -Path $OverlayPidPath -Value "$($StartedOverlay.Id)" -Encoding ascii
  }
}

Write-Host "BidKing WinDivert live monitor started." -ForegroundColor Green
Write-Host "LogDir:     $LogPath"
Write-Host "Python:     $Python  (monitor uses this interpreter, not plain 'python' on PATH)"
$DriverPath = & $Python -c "import pydivert; from pathlib import Path; print((Path(pydivert.__file__).parent/'windivert_dll'/'WinDivert64.sys').resolve())" 2>$null
if ($DriverPath) {
  Write-Host "WinDivert:  $DriverPath"
  if ($DriverPath -match 'anaconda3') {
    Write-Host "Warning: WinDivert driver is from Anaconda. Pass -PythonPath C:\Python313\python.exe" -ForegroundColor Yellow
  }
}
Write-Host "Verify:     & `"$Python`" scripts\diagnose_windivert.py"
Write-Host "Process:    $ProcessName"
Write-Host "Mode:       $(if ($UseBroadSniff) { 'broad-sniff + process-match' } else { 'port-filter (default)' })"
Write-Host "Loopback:   $(if ($UseLoopback) { 'included (-IncludeLoopback)' } else { 'excluded (use -IncludeLoopback for VPN/UU)' })"
Write-Host "ServerPort: $($ServerPort -join ',')"
Write-Host "Formal:    $FormalMode (set -FormalMode v2 to roll back live bids)"
Write-Host "Inference:  live-fast $FastNTrials trials every >=${MinInferenceIntervalSeconds}s; full $NTrials trials on stop (roi=$RoiTrials, shadow=$FullShadowTrials)"
Write-Host "DebugShadow:$(if ($EnableDebugShadows) { 'enabled' } else { 'skipped (low-impact default)' })"
Write-Host "Python:     $Python"
if (-not $IsAdmin) {
  Write-Host "Warning: WinDivert usually requires an elevated/admin PowerShell." -ForegroundColor Yellow
}
if (-not $HasPacketDeps) {
  Write-Host "Warning: selected Python cannot import pydivert/psutil." -ForegroundColor Yellow
  Write-Host "Install with: `"$Python`" -m pip install -e `"$Repo[packet]`""
}
Write-Host "If monitor.stderr.log says pydivert is missing, run: `"$Python`" -m pip install pydivert"
if ($MonitorProcesses) {
  Write-Host "Monitor:    already running (PID $($MonitorProcesses[0].ProcessId))"
} elseif ($ExistingMonitorPidFromLock) {
  Write-Host "Monitor:    already running from lock (PID $ExistingMonitorPidFromLock)"
}
if ($OverlayProcesses) {
  Write-Host "Overlay:    already running (PID $($OverlayProcesses[0].ProcessId))"
} elseif ($StartedOverlay -and $StartedOverlay.Id) {
  Write-Host "Overlay:    started (PID $($StartedOverlay.Id))"
} elseif ($NoOverlay) {
  Write-Host "Overlay:    skipped (-NoOverlay)"
}
if (-not $NoOverlay -and -not $KeepMonitorOnOverlayClose -and $StartedMonitorPid) {
  Write-Host "Lifecycle:  closing overlay will stop monitor PID $StartedMonitorPid"
}
if ($UseBroadSniff) {
  Write-Host "Tip: drop -BroadSniff for lower CPU when the game uses port 10000 directly." -ForegroundColor DarkYellow
}
