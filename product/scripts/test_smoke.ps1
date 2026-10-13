param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $Repo "scripts\resolve_python.ps1")
$Python = Resolve-BidKingPython

Push-Location -LiteralPath $Repo
try {
    & $Python -m pytest -q -m "not slow" @PytestArgs
    $PytestExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $PytestExitCode
