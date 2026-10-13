# Build bidking-lab-v1.0.0.zip for GitHub Release (one-stop player package).
param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Repo "dist"
$Name = "bidking-lab-v$Version"
$Stage = Join-Path $Dist $Name
$ZipPath = Join-Path $Dist "$Name.zip"

$RequiredTables = @(
    "BidMap.txt",
    "Drop.txt",
    "Item.txt"
)

Write-Host "==> BidKing Lab release packager v$Version" -ForegroundColor Cyan
Write-Host "    Repo: $Repo"

foreach ($t in $RequiredTables) {
    $p = Join-Path $Repo "data\raw\tables\$t"
    if (-not (Test-Path $p)) {
        Write-Host ""
        Write-Host "ERROR: Missing $p" -ForegroundColor Red
        Write-Host "Copy game Tables first, e.g.:" -ForegroundColor Yellow
        Write-Host '  $env:BIDKING_GAME_ROOT = "C:\path\to\steamapps\common\BidKing"'
        Write-Host "  .\scripts\copy_game_tables.ps1"
        exit 1
    }
}

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null

function Copy-Tree {
    param([string]$Rel)
    $src = Join-Path $Repo $Rel
    $dst = Join-Path $Stage $Rel
    if (-not (Test-Path $src)) {
        Write-Host "  skip (missing): $Rel" -ForegroundColor DarkGray
        return
    }
    $parent = Split-Path $dst -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -Recurse -Force $src $dst
    Write-Host "  + $Rel"
}

Write-Host ""
Write-Host "Staging files..." -ForegroundColor Cyan

Copy-Tree "app"
Copy-Tree "src"

# Processed JSON (runtime + OCR map fixes)
$procDst = Join-Path $Stage "data\processed"
New-Item -ItemType Directory -Path $procDst -Force | Out-Null
Get-ChildItem (Join-Path $Repo "data\processed\*.json") | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $procDst $_.Name)
    Write-Host "  + data/processed/$($_.Name)"
}

# Raw tables (Streamlit MC loader — not in git, bundled at release build time)
$rawDst = Join-Path $Stage "data\raw\tables"
New-Item -ItemType Directory -Path $rawDst -Force | Out-Null
foreach ($t in $RequiredTables) {
    Copy-Item (Join-Path $Repo "data\raw\tables\$t") (Join-Path $rawDst $t)
    Write-Host "  + data/raw/tables/$t"
}
# Optional extras if present (Hero/BattleItem for future tabs / OCR)
foreach ($opt in @("Hero.txt", "BattleItem.txt")) {
    $optSrc = Join-Path $Repo "data\raw\tables\$opt"
    if (Test-Path $optSrc) {
        Copy-Item $optSrc (Join-Path $rawDst $opt)
        Write-Host "  + data/raw/tables/$opt"
    }
}

$docsDst = Join-Path $Stage "docs"
New-Item -ItemType Directory -Path $docsDst -Force | Out-Null
Copy-Item (Join-Path $Repo "docs\INSTRUCTIONS.zh-CN.md") (Join-Path $docsDst "INSTRUCTIONS.zh-CN.md")
Write-Host "  + docs/INSTRUCTIONS.zh-CN.md"
$assetsDst = Join-Path $Stage "docs\assets"
New-Item -ItemType Directory -Path $assetsDst -Force | Out-Null
foreach ($img in @("01-inputs.png", "02-bidding.png")) {
    $imgSrc = Join-Path $Repo "docs\assets\$img"
    if (Test-Path $imgSrc) {
        Copy-Item $imgSrc (Join-Path $assetsDst $img)
        Write-Host "  + docs/assets/$img"
    }
}

$RootFiles = @(
    "LICENSE",
    "README.zh-CN.md",
    "RELEASE_QUICKSTART.zh-CN.md",
    "requirements-release.txt",
    "start_ui.ps1",
    "pyproject.toml"
)
foreach ($f in $RootFiles) {
    Copy-Item (Join-Path $Repo $f) (Join-Path $Stage $f)
    Write-Host "  + $f"
}

Write-Host ""
Write-Host "Creating zip..." -ForegroundColor Cyan
Compress-Archive -Path $Stage -DestinationPath $ZipPath -Force

$sizeMb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
Write-Host ""
Write-Host "Done: $ZipPath ($sizeMb MB)" -ForegroundColor Green
Write-Host "Upload this file to GitHub Release v$Version -> Attach binaries" -ForegroundColor Yellow
