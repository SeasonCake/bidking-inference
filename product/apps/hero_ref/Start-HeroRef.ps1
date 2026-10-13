param(
    [string]$PythonPath = "",
    [string]$ProcessName = "BidKing.exe",
    [int[]]$ServerPort = @(10000),
    [ValidateSet("engineering", "portable", "public-safe", "stable", "public_safe")]
    [string]$DiagnosticProfile = "portable",
    [switch]$BroadSniff,
    [switch]$IncludeLoopback,
    [switch]$KeepMonitorOnClose,
    [switch]$ShowTaskbar,
    [switch]$NoRestart,
    [switch]$NoAutoElevate
)

$ErrorActionPreference = "Stop"

$AppRoot = $PSScriptRoot
$ScriptsDir = Join-Path $AppRoot "scripts"
$LogDir = Join-Path $AppRoot "data\logs\live"
$LockPath = Join-Path $LogDir "monitor.lock"
$OverlayPidPath = Join-Path $LogDir "ahmad_overlay.pid"
$SnapshotPath = Join-Path $LogDir "latest_snapshot.json"
$HeroExe = Join-Path $AppRoot "BidKingHeroRef\BidKingHeroRef.exe"
$MonitorExe = Join-Path $AppRoot "BidKingHeroMonitor\BidKingHeroMonitor.exe"
$MonitorStart = Join-Path $ScriptsDir "start_live_windivert_overlay.ps1"
$ResolvePython = Join-Path $ScriptsDir "resolve_python.ps1"

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

function Get-MonitorLockPayload {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-PacketPython {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    & $Path -c "import pydivert, psutil" *> $null
    return $LASTEXITCODE -eq 0
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

function Stop-MonitorFromLock {
    param([string]$Path)
    $LockPayload = Get-MonitorLockPayload -Path $Path
    if ($LockPayload -and $LockPayload.pid) {
        try {
            Stop-Process -Id ([int]$LockPayload.pid) -Force -ErrorAction SilentlyContinue
        } catch {
        }
    }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    }
}

function Stop-PackagedMonitorProcesses {
    param([string]$ExePath)
    try {
        $FullExePath = [System.IO.Path]::GetFullPath($ExePath)
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.ExecutablePath -and
                ([System.IO.Path]::GetFullPath($_.ExecutablePath)).Equals($FullExePath, [System.StringComparison]::OrdinalIgnoreCase)
            } |
            ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
    } catch {
    }
}

if (-not (Test-Path -LiteralPath $HeroExe)) {
    throw "Hero Ref UI exe not found: $HeroExe"
}
$HasPackagedMonitor = Test-Path -LiteralPath $MonitorExe
if (-not $HasPackagedMonitor -and -not (Test-Path -LiteralPath $MonitorStart)) {
    throw "Monitor starter not found: $MonitorStart"
}
if (-not $HasPackagedMonitor -and -not (Test-Path -LiteralPath $ResolvePython)) {
    throw "Python resolver not found: $ResolvePython"
}

$RequiredTables = @("BidMap.txt", "Drop.txt", "Item.txt")
$TablesDir = Join-Path $AppRoot "data\raw\tables"
$MissingTables = @(
    foreach ($Name in $RequiredTables) {
        $Path = Join-Path $TablesDir $Name
        if (-not (Test-Path -LiteralPath $Path)) {
            $Name
        }
    }
)
if ($MissingTables.Count -gt 0) {
    Write-Host "缺少本地游戏表: $($MissingTables -join ', ')" -ForegroundColor Red
    Write-Host "full 完整包通常自带 data\raw\tables；群友发的 full 包请直接 Start-HeroRef.bat，不要先点「导入本机游戏表」。" -ForegroundColor Yellow
    Write-Host "仅 public-safe 公开包需先运行 Import-LocalTables.bat（或 导入本机游戏表.bat）。" -ForegroundColor Yellow
    Write-Host "若确认是 public-safe 包，从本机 BidKing 复制表到 data\raw\tables；Steam 示例: ...\steamapps\common\BidKing" -ForegroundColor Yellow
    Write-Host "请勿公开传播本地游戏表。" -ForegroundColor Yellow
    exit 1
}

$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltinRole]::Administrator
)

if (-not $IsAdmin -and -not $NoAutoElevate) {
    $ElevatedArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-ProcessName", $ProcessName,
        "-DiagnosticProfile", $DiagnosticProfile,
        "-NoAutoElevate"
    )
    if ($PythonPath) {
        $ElevatedArgs += @("-PythonPath", $PythonPath)
    }
    foreach ($PortValue in $ServerPort) {
        $ElevatedArgs += @("-ServerPort", "$PortValue")
    }
    if ($BroadSniff) {
        $ElevatedArgs += "-BroadSniff"
    }
    if ($IncludeLoopback) {
        $ElevatedArgs += "-IncludeLoopback"
    }
    if ($KeepMonitorOnClose) {
        $ElevatedArgs += "-KeepMonitorOnClose"
    }
    if ($ShowTaskbar) {
        $ElevatedArgs += "-ShowTaskbar"
    }
    if ($NoRestart) {
        $ElevatedArgs += "-NoRestart"
    }
    Start-Process -FilePath (Get-CurrentPowerShellPath) -Verb RunAs -WindowStyle Hidden -WorkingDirectory $AppRoot -ArgumentList $ElevatedArgs
    Write-Host "WinDivert 需要管理员权限。已弹出 UAC，请点击「是」，随后会静默启动 Hero Ref。" -ForegroundColor Yellow
    return
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

Write-Host "== BidKing Hero Ref ==" -ForegroundColor Cyan
Write-Host "App:     $AppRoot"
Write-Host "Diagnostic: $DiagnosticProfile"
if ($HasPackagedMonitor) {
    Write-Host "Monitor: $MonitorExe"
    Write-Host "Mode:    packaged WinDivert monitor exe + Hero Ref UI"
} else {
    . $ResolvePython
    $Python = Resolve-BidKingPython -ExplicitPython $PythonPath -RequirePacket
    if (-not (Test-PacketPython -Path $Python)) {
        Write-Host "Python packet dependencies are missing for: $Python" -ForegroundColor Red
        Write-Host "Install once:" -ForegroundColor Yellow
        Write-Host "  `"$Python`" -m pip install pydivert psutil" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Python:  $Python"
    Write-Host "Mode:    Python WinDivert monitor fallback + Hero Ref UI"
}
Write-Host ""

if ($HasPackagedMonitor) {
    if (-not $NoRestart) {
        Stop-MonitorFromLock -Path $LockPath
        Stop-PackagedMonitorProcesses -ExePath $MonitorExe
    }
    $LockPayload = Get-MonitorLockPayload -Path $LockPath
    if ($NoRestart -and $LockPayload -and $LockPayload.pid -and (Test-ProcessIdRunning -ProcessId $LockPayload.pid)) {
        Write-Host "Monitor: reuse existing PID $($LockPayload.pid)"
    } else {
        $env:BIDKING_PROJECT_ROOT = $AppRoot
        $MonitorOut = Join-Path $LogDir "monitor.stdout.log"
        $MonitorErr = Join-Path $LogDir "monitor.stderr.log"
        $MonitorArgs = @(
            "--log-dir", $LogDir,
            "--tables-dir", $TablesDir,
            "--process-name", $ProcessName,
            "--n-trials", "500",
            "--roi-trials", "0",
            "--full-shadow-trials", "20",
            "--fast-n-trials", "10",
            "--formal-mode", "v3_practical",
            "--debounce-seconds", "1.0",
            "--min-inference-interval-seconds", "2.0",
            "--skip-debug-shadows"
        )
        foreach ($PortValue in $ServerPort) {
            $MonitorArgs += @("--server-port", "$PortValue")
        }
        if ($BroadSniff) {
            $MonitorArgs += "--broad"
        }
        if ($IncludeLoopback) {
            $MonitorArgs += "--include-loopback"
        }
        $StartedMonitor = Start-Process -FilePath $MonitorExe -WorkingDirectory $AppRoot -ArgumentList $MonitorArgs -RedirectStandardOutput $MonitorOut -RedirectStandardError $MonitorErr -WindowStyle Hidden -PassThru
        Write-Host "Monitor: started packaged exe (PID $($StartedMonitor.Id))"
    }
} else {
    $MonitorParams = @{
        LogDir = "data\logs\live"
        ProcessName = $ProcessName
        PythonPath = $Python
        ServerPort = $ServerPort
        NTrials = 500
        RoiTrials = 0
        FullShadowTrials = 20
        FastNTrials = 10
        FormalMode = "v3_practical"
        NoOverlay = $true
        NoAutoElevate = $true
    }
    if (-not $NoRestart) {
        $MonitorParams["Restart"] = $true
    }
    if ($BroadSniff) {
        $MonitorParams["BroadSniff"] = $true
    } else {
        $MonitorParams["PortOnly"] = $true
    }
    if ($IncludeLoopback) {
        $MonitorParams["IncludeLoopback"] = $true
    }
    if ($KeepMonitorOnClose) {
        $MonitorParams["KeepMonitorOnOverlayClose"] = $true
    }
    & $MonitorStart @MonitorParams
    if ($LASTEXITCODE) {
        exit $LASTEXITCODE
    }
}

$MonitorPid = $null
$MonitorLaunchFailed = $false
$Deadline = (Get-Date).AddSeconds(5)
while (-not $MonitorPid -and (Get-Date) -lt $Deadline) {
    $LockPayload = Get-MonitorLockPayload -Path $LockPath
    if ($LockPayload -and $LockPayload.pid) {
        $MonitorPid = [int]$LockPayload.pid
        break
    }
    Start-Sleep -Milliseconds 250
}
if ($HasPackagedMonitor -and -not $MonitorPid -and $StartedMonitor -and $StartedMonitor.HasExited) {
    Write-Host "Monitor exited before lock was created. See:" -ForegroundColor Red
    Write-Host "  $MonitorErr" -ForegroundColor Yellow
    $MonitorLaunchFailed = $true
}
if ($HasPackagedMonitor -and $StartedMonitor -and $MonitorPid) {
    Start-Sleep -Milliseconds 900
    try {
        $StartedMonitor.Refresh()
    } catch {
    }
    if ($StartedMonitor.HasExited) {
        Write-Host "Monitor exited immediately after startup. Hero Ref UI will stay open for diagnosis." -ForegroundColor Yellow
        Write-Host "See:" -ForegroundColor Yellow
        Write-Host "  $MonitorErr" -ForegroundColor Yellow
        $MonitorLaunchFailed = $true
        $MonitorPid = $null
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
}

$HeroArgs = @(
    "--snapshot", $SnapshotPath,
    "--load-existing",
    "--diagnostic-profile", $DiagnosticProfile,
    "--ui-prefs"
)
if ($ShowTaskbar) {
    $HeroArgs += "--show-taskbar"
}
if ($KeepMonitorOnClose) {
    $HeroArgs += "--keep-monitor-on-close"
}
if ($MonitorPid -and -not $KeepMonitorOnClose -and -not $MonitorLaunchFailed) {
    $HeroArgs += @(
        "--stop-pid-on-exit", "$MonitorPid",
        "--exit-when-pid-exits", "$MonitorPid",
        "--cleanup-lock-on-exit", $LockPath
    )
    Write-Host "Lifecycle: closing Hero Ref will stop monitor PID $MonitorPid"
} elseif ($KeepMonitorOnClose) {
    Write-Host "Lifecycle: Hero Ref will not stop monitor (-KeepMonitorOnClose)"
} else {
    Write-Host "Lifecycle: monitor lock not found; Hero Ref cannot stop monitor automatically." -ForegroundColor Yellow
}

$Hero = Start-Process -FilePath $HeroExe -WorkingDirectory $AppRoot -ArgumentList $HeroArgs -PassThru
if ($Hero -and $Hero.Id) {
    Set-Content -Path $OverlayPidPath -Value "$($Hero.Id)" -Encoding ascii
    Write-Host "Hero Ref: started (PID $($Hero.Id))" -ForegroundColor Green
}

if ($MonitorLaunchFailed) {
    Write-Host ""
    Write-Host "后台 monitor 未成功启动，Hero Ref 会一直显示「等待 monitor 状态」。" -ForegroundColor Yellow
    Write-Host "请查看: $MonitorErr" -ForegroundColor Yellow
    Write-Host "常见原因：安全软件拦截 WinDivert；或未以管理员运行。详见 火绒拦截说明.txt" -ForegroundColor Yellow
    Write-Host ""
    # 提权窗口为隐藏静默启动，失败时用弹窗提示，避免错误信息被吞掉。
    $FailMsg = "后台 monitor 未成功启动，Hero Ref 会一直显示「等待 monitor 状态」。`n`n请查看:`n$MonitorErr`n`n常见原因：安全软件拦截 WinDivert；或未以管理员运行。`n详见 火绒拦截说明.txt"
    try {
        (New-Object -ComObject WScript.Shell).Popup($FailMsg, 0, "BidKing Hero Ref 启动提示", 48) | Out-Null
    } catch {
    }
} elseif ($Hero -and $Hero.Id) {
    Write-Host ""
    Write-Host "Hero Ref 已启动。关闭 Hero Ref 小窗会同时停止后台 monitor。" -ForegroundColor Green
}
