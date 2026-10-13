# Shared Python selection for BidKing lab scripts.
# Default: C:\Python313\python.exe (override with -PythonPath or $env:BIDKING_PYTHON).

$script:BidKingDefaultPython313 = "C:\Python313\python.exe"

function Test-BidKingPython {
  param(
    [string]$Candidate,
    [switch]$RequirePacket
  )
  if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
    return $false
  }
  if ($RequirePacket) {
    & $Candidate -c "import pydivert, psutil" *> $null
    return $LASTEXITCODE -eq 0
  }
  & $Candidate -c "import sys; sys.exit(0)" *> $null
  return $LASTEXITCODE -eq 0
}

function Resolve-BidKingPython {
  param(
    [string]$ExplicitPython = "",
    [switch]$RequirePacket
  )

  $Candidates = New-Object System.Collections.Generic.List[string]
  if ($ExplicitPython) {
    $Candidates.Add($ExplicitPython)
  }
  if ($env:BIDKING_PYTHON) {
    $Candidates.Add($env:BIDKING_PYTHON)
  }
  $Candidates.Add($script:BidKingDefaultPython313)

  $PyCommand = Get-Command py -ErrorAction SilentlyContinue
  if ($PyCommand) {
    $Py313 = & $PyCommand.Source -3.13 -c "import sys; print(sys.executable)" 2>$null
    if ($Py313 -and (Test-Path -LiteralPath $Py313)) {
      $Candidates.Add($Py313.Trim())
    }
  }

  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($PythonCommand) {
    $Candidates.Add($PythonCommand.Source)
  }

  $Seen = @{}
  foreach ($Candidate in $Candidates) {
    if (-not $Candidate -or $Seen.ContainsKey($Candidate)) {
      continue
    }
    $Seen[$Candidate] = $true
    if (Test-BidKingPython -Candidate $Candidate -RequirePacket:$RequirePacket) {
      return $Candidate
    }
  }

  if ($ExplicitPython -and (Test-Path -LiteralPath $ExplicitPython)) {
    return $ExplicitPython
  }
  if (Test-Path -LiteralPath $script:BidKingDefaultPython313) {
    return $script:BidKingDefaultPython313
  }
  if ($PythonCommand) {
    return $PythonCommand.Source
  }
  throw "Python not found. Install Python 3.13 or set `$env:BIDKING_PYTHON."
}

function Resolve-BidKingPythonw {
  param([string]$PythonExe)
  $PythonwCandidate = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
  if (Test-Path -LiteralPath $PythonwCandidate) {
    return $PythonwCandidate
  }
  return $PythonExe
}
