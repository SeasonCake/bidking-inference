# This public tool never derives deletion authority from a confdir thumbprint.
# It delegates to the root executable, which verifies the whole package and then
# opens the native-sealed repair payload.  That payload removes only an exact
# product CA with its private journal after a second SID/cert/runtime/monitor/game check.

$ErrorActionPreference = "Stop"
$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DiagnosticsDir = Split-Path -Parent $ToolDir
$PackageRoot = Split-Path -Parent $DiagnosticsDir
$RootExecutable = Join-Path $PackageRoot "BidKingLive.exe"
if (-not (Test-Path -LiteralPath $RootExecutable -PathType Leaf)) {
  Write-Error "BidKingLive.exe 不在诊断工具的上一级包根目录；请重新完整解压当前安装包。"
  exit 2
}

$previous = $env:BIDKING_REPAIR_TRUST_CLEANUP
try {
  $env:BIDKING_REPAIR_TRUST_CLEANUP = "1"
  $process = Start-Process -FilePath $RootExecutable `
    -ArgumentList "--repair" `
    -WorkingDirectory $PackageRoot `
    -Wait `
    -PassThru
  exit [int]$process.ExitCode
} finally {
  if ($null -eq $previous) {
    Remove-Item Env:\BIDKING_REPAIR_TRUST_CLEANUP -ErrorAction SilentlyContinue
  } else {
    $env:BIDKING_REPAIR_TRUST_CLEANUP = $previous
  }
}
