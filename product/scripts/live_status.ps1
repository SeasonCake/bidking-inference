param(
  [string]$LogDir = "data\logs\live",
  [double]$StaleSeconds = 30.0,
  [double]$SlowSeconds = 15.0,
  [switch]$Json,
  [switch]$Strict
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $Repo "scripts\resolve_python.ps1")
$Python = Resolve-BidKingPython
$StatusScript = Join-Path $Repo "scripts\live_status.py"
$LogPath = Join-Path $Repo $LogDir

$Arguments = @(
  $StatusScript,
  "--log-dir", $LogPath,
  "--stale-seconds", "$StaleSeconds",
  "--slow-seconds", "$SlowSeconds"
)
if ($Json) {
  $Arguments += @("--format", "json")
}
if ($Strict) {
  $Arguments += "--strict"
}

& $Python @Arguments
exit $LASTEXITCODE
