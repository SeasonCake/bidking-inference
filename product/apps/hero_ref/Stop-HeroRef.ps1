$ErrorActionPreference = "SilentlyContinue"

$AppRoot = $PSScriptRoot
$LogDir = Join-Path $AppRoot "data\logs\live"
$OverlayPidPath = Join-Path $LogDir "ahmad_overlay.pid"
$LockPath = Join-Path $LogDir "monitor.lock"

if (Test-Path -LiteralPath $OverlayPidPath) {
    try {
        $OverlayPid = [int]((Get-Content -LiteralPath $OverlayPidPath -Raw).Trim())
        Stop-Process -Id $OverlayPid -Force
    } catch {
    }
    Remove-Item -LiteralPath $OverlayPidPath -Force
}

if (Test-Path -LiteralPath $LockPath) {
    try {
        $LockPayload = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
        if ($LockPayload.pid) {
            Stop-Process -Id ([int]$LockPayload.pid) -Force
        }
    } catch {
    }
    Remove-Item -LiteralPath $LockPath -Force
}

# WinDivert 驱动由本包 monitor 加载；强杀后不会自动卸载，会一直锁住
# BidKingHeroMonitor\_internal\...\WinDivert64.sys，导致整个文件夹无法删除。
# 仅当驱动确实来自本包路径时停止并删除该服务，释放文件。
try {
    Start-Sleep -Milliseconds 800
    $RootPrefix = ([System.IO.Path]::GetFullPath($AppRoot)).ToLower()
    $ScConfig = & sc.exe qc WinDivert 2>$null
    $BinaryLine = $ScConfig | Where-Object { $_ -match "BINARY_PATH_NAME" }
    if ($BinaryLine -and ($BinaryLine.ToLower() -like "*$RootPrefix*")) {
        & sc.exe stop WinDivert 2>$null | Out-Null
        & sc.exe delete WinDivert 2>$null | Out-Null
        Write-Host "WinDivert 驱动已卸载（来自本包），现在可以删除文件夹。"
    }
} catch {
}

Write-Host "Hero Ref UI and monitor stop requested."
