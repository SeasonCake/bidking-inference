# Quick post-game checks after WinDivert live sessions.
param(
  [string]$LogDir = "data\logs\live",
  [double]$SinceHours = 72.0
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Repo
. (Join-Path $Repo "scripts\resolve_python.ps1")
$Python = Resolve-BidKingPython
$LogPath = Join-Path $Repo $LogDir

Write-Host "== post_game_live ==" -ForegroundColor Cyan
Write-Host ""

& (Join-Path $Repo "scripts\live_status.ps1") -LogDir $LogDir
Write-Host ""

Write-Host "-- capture_source_status.json --" -ForegroundColor DarkCyan
Get-Content (Join-Path $LogPath "capture_source_status.json")
Write-Host ""

Write-Host "-- archive_live_raw --" -ForegroundColor DarkCyan
& $Python (Join-Path $Repo "scripts\archive_live_raw.py")
Write-Host ""

Write-Host "-- summarize_size_bucket_live --" -ForegroundColor DarkCyan
& $Python (Join-Path $Repo "scripts\summarize_size_bucket_live.py")
Write-Host ""

Write-Host "-- summarize_live_windivert_brief --" -ForegroundColor DarkCyan
& $Python (Join-Path $Repo "scripts\summarize_live_windivert_brief.py") --since-hours $SinceHours
Write-Host ""

Write-Host "-- summarize_live_model_eval --brief --since-hours $SinceHours --" -ForegroundColor DarkCyan
& $Python (Join-Path $Repo "scripts\summarize_live_model_eval.py") --brief --since-hours $SinceHours
