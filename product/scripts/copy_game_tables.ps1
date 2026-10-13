<#
.SYNOPSIS
  Copy key BidKing StreamingAssets text tables into data/raw/tables for local parsing.

  Game assets stay out of git (.gitignore). Re-run after game updates.

.PARAMETER GameRoot
  Path to .../BidKing (folder containing BidKing_Data). Override or set BIDKING_GAME_ROOT.
#>
param(
  [string] $GameRoot = $env:BIDKING_GAME_ROOT
)

$ErrorActionPreference = "Stop"

if (-not $GameRoot) {
  $GameRoot = "C:\Games\Steam\steamapps\common\BidKing"
}
$sa = Join-Path $GameRoot "BidKing_Data\StreamingAssets"
if (-not (Test-Path $sa)) {
  Write-Error "StreamingAssets not found: $sa — set BIDKING_GAME_ROOT to your BidKing install."
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path (Join-Path $repo "pyproject.toml"))) {
  Write-Error "Could not find pyproject.toml above scripts\; cwd repo root?"
}
$dst = Join-Path $repo "data\raw\tables"
New-Item -ItemType Directory -Path $dst -Force | Out-Null

$files = @(
  "filelist.txt", "fileVersion", "fileDiff.txt",
  "Tables\Activity.txt", "Tables\Map.txt", "Tables\RankMap.txt",
  "Tables\Drop.txt", "Tables\BidMap.txt", "Tables\Item.txt", "Tables\Hero.txt",
  "Tables\Language.txt",
  "Tables\Item_Type.txt", "Tables\Constant.txt", "Tables\Cabinet.txt",
  "Tables\Condition.txt", "Tables\BattleItem.txt", "Tables\ItemRestock.txt",
  "Tables\LevelUp.txt"
)

foreach ($f in $files) {
  $src = Join-Path $sa $f
  if (-not (Test-Path $src)) {
    Write-Warning "Skip missing: $f"
    continue
  }
  $leaf = Split-Path $f -Leaf
  Copy-Item -LiteralPath $src -Destination (Join-Path $dst $leaf) -Force
  Write-Host "OK $leaf"
}

$rootRaw = Join-Path $repo "data\raw"
Copy-Item (Join-Path $sa "filelist.txt") (Join-Path $rootRaw "filelist.txt") -Force
Copy-Item (Join-Path $sa "fileVersion") (Join-Path $rootRaw "fileVersion") -Force
Write-Host "`nDone -> $dst"
