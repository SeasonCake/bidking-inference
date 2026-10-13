# One-time helper: put C:\Python313 ahead of Anaconda/other Pythons in the USER Path.
# Re-open PowerShell after running so new terminals pick up the change.

$ErrorActionPreference = "Stop"

$PythonRoot = "C:\Python313"
$ScriptsRoot = Join-Path $PythonRoot "Scripts"

if (-not (Test-Path -LiteralPath (Join-Path $PythonRoot "python.exe"))) {
  Write-Error "Expected $PythonRoot\python.exe but it was not found."
}

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Parts = @()
if ($UserPath) {
  $Parts = $UserPath -split ";" | Where-Object { $_ -and $_.Trim() -ne "" }
}

$Filtered = New-Object System.Collections.Generic.List[string]
foreach ($Part in $Parts) {
  $Normalized = $Part.TrimEnd("\")
  if ($Normalized -ieq $PythonRoot -or $Normalized -ieq $ScriptsRoot) {
    continue
  }
  $Filtered.Add($Part)
}

$NewParts = @($PythonRoot, $ScriptsRoot) + $Filtered.ToArray()
$NewPath = ($NewParts | Select-Object -Unique) -join ";"
[Environment]::SetEnvironmentVariable("Path", $NewPath, "User")

# Refresh current session too.
$Env:Path = $NewPath + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")

Write-Host "User PATH updated: Python 3.13 is first." -ForegroundColor Green
Write-Host "  $PythonRoot"
Write-Host "  $ScriptsRoot"
Write-Host ""
Write-Host "Verify in a NEW PowerShell window:" -ForegroundColor Yellow
Write-Host "  python -c `"import sys; print(sys.executable)`""
Write-Host "  where.exe python"
Write-Host ""
Write-Host "Install project deps once:" -ForegroundColor Yellow
Write-Host "  cd $((Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)))"
Write-Host '  python -m pip install -e ".[packet]"'
