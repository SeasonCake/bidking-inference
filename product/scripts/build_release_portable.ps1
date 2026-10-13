# Build bidking-lab-v1.0.0-portable.zip — embedded Python, no pip/venv for players.
param(
    [string]$Version = "1.0.0",
    [string]$PythonVersion = "3.13.15",
    [string]$SitePackagesSource = ""
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Repo "dist"
$Cache = Join-Path $Dist ".cache"
$Name = "bidking-lab-v$Version-portable"
$Stage = Join-Path $Dist $Name
$ZipPath = Join-Path $Dist "$Name.zip"
$RuntimePy = Join-Path $Stage "runtime\python"

$RequiredTables = @("BidMap.txt", "Drop.txt", "Item.txt")

Write-Host "==> BidKing Lab PORTABLE packager v$Version" -ForegroundColor Cyan
Write-Host "    Python embed: $PythonVersion amd64"

foreach ($t in $RequiredTables) {
    $p = Join-Path $Repo "data\raw\tables\$t"
    if (-not (Test-Path $p)) {
        Write-Host "ERROR: Missing $p — run scripts\copy_game_tables.ps1 first" -ForegroundColor Red
        exit 1
    }
}

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
# Do not delete existing zip here — may be locked; overwrite via .part at end.
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
New-Item -ItemType Directory -Path $Cache -Force | Out-Null

function Copy-TreeClean {
    param([string]$Rel)
    $src = Join-Path $Repo $Rel
    $dst = Join-Path $Stage $Rel
    if (-not (Test-Path $src)) { return }
    New-Item -ItemType Directory -Path (Split-Path $dst -Parent) -Force | Out-Null
    robocopy $src $dst /E /XD __pycache__ .pytest_cache .mypy_cache .ruff_cache /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $Rel" }
    Write-Host "  + $Rel"
}

Write-Host ""
Write-Host "Staging app files..." -ForegroundColor Cyan
Copy-TreeClean "app"
Copy-TreeClean "src"

$procDst = Join-Path $Stage "data\processed"
New-Item -ItemType Directory -Path $procDst -Force | Out-Null
Get-ChildItem (Join-Path $Repo "data\processed\*.json") | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $procDst $_.Name)
    Write-Host "  + data/processed/$($_.Name)"
}

$rawDst = Join-Path $Stage "data\raw\tables"
New-Item -ItemType Directory -Path $rawDst -Force | Out-Null
foreach ($t in $RequiredTables) {
    Copy-Item (Join-Path $Repo "data\raw\tables\$t") (Join-Path $rawDst $t)
    Write-Host "  + data/raw/tables/$t"
}
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
$assetsDst = Join-Path $docsDst "assets"
New-Item -ItemType Directory -Path $assetsDst -Force | Out-Null
foreach ($img in @("01-inputs.png", "02-bidding.png")) {
    $imgSrc = Join-Path $Repo "docs\assets\$img"
    if (Test-Path $imgSrc) { Copy-Item $imgSrc (Join-Path $assetsDst $img) }
}
Write-Host "  + docs/"

foreach ($f in @("LICENSE", "README.zh-CN.md")) {
    Copy-Item (Join-Path $Repo $f) (Join-Path $Stage $f)
    Write-Host "  + $f"
}
Copy-Item (Join-Path $Repo "requirements-release.txt") (Join-Path $Stage "requirements-release.txt")
$reqFile = Join-Path $Stage "requirements-release.txt"

Write-Host ""
Write-Host "Setting up embedded Python..." -ForegroundColor Cyan
$EmbedZip = Join-Path $Cache "python-$PythonVersion-embed-amd64.zip"
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $EmbedZip)) {
    Write-Host "  Downloading $EmbedUrl"
    Invoke-WebRequest -Uri $EmbedUrl -OutFile $EmbedZip -UseBasicParsing
}
New-Item -ItemType Directory -Path $RuntimePy -Force | Out-Null
Expand-Archive -Path $EmbedZip -DestinationPath $RuntimePy -Force

$sitePackages = Join-Path $RuntimePy "Lib\site-packages"
New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null

$pthFile = Get-ChildItem $RuntimePy -Filter "python*._pth" | Select-Object -First 1
$zipLine = (Get-Content $pthFile.FullName | Where-Object { $_ -match '\.zip$' } | Select-Object -First 1)
if (-not $zipLine) { $zipLine = "python313.zip" }
@(
    $zipLine
    "."
    "Lib\site-packages"
    "import site"
) | Set-Content -Path $pthFile.FullName -Encoding ascii
Write-Host "  + runtime/python ($PythonVersion embed)"

$pyExe = Join-Path $RuntimePy "python.exe"
$getPip = Join-Path $Cache "get-pip.py"
if (-not (Test-Path $getPip)) {
    Write-Host "  Downloading get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
}

Write-Host "  Bootstrapping pip..."
$pipOk = $false
try {
    & $pyExe $getPip --no-warn-script-location 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & $pyExe -m pip install --upgrade pip 2>&1 | Out-Null
        Write-Host "  Installing runtime deps via pip..."
        & $pyExe -m pip install -r $reqFile 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $pipOk = $true }
    }
} catch {
    $pipOk = $false
}

if (-not $pipOk) {
    Write-Host "  pip install failed (network/SSL?) — copying site-packages from local Python" -ForegroundColor Yellow
    if (-not $SitePackagesSource) {
        $buildPyCmd = $null
        foreach ($cmd in @("py -3.13", "py -3.12", "python")) {
            try {
                $ok = Invoke-Expression "$cmd -c `"import sys; exit(0 if sys.version_info>=(3,10) else 1)`"" 2>$null
                if ($LASTEXITCODE -eq 0) { $buildPyCmd = $cmd; break }
            } catch { continue }
        }
        if (-not $buildPyCmd) { throw "Need Python 3.10+ on PATH to copy site-packages" }
        $SitePackagesSource = (Invoke-Expression "$buildPyCmd -c `"import site; print(site.getusersitepackages())`"").Trim()
    }
    if (-not (Test-Path $SitePackagesSource)) {
        throw "No site-packages at $SitePackagesSource. Pass -SitePackagesSource or install deps with Python 3.13"
    }
    Write-Host "  Source: $SitePackagesSource"
    robocopy $SitePackagesSource $sitePackages /E /XD __pycache__ /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy site-packages failed" }
}

Write-Host "  Verifying imports..."
& $pyExe -c "import streamlit, numpy, pydantic; print('ok', streamlit.__version__)"
if ($LASTEXITCODE -ne 0) { throw "import verification failed" }

$batContent = @'
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title BidKing Lab
echo.
echo  BidKing Lab — 正在启动...
echo  浏览器将打开 http://localhost:8501
echo  关闭本窗口即停止服务。
echo.
"%~dp0runtime\python\python.exe" -m streamlit run "%~dp0app\streamlit_app.py" --server.headless false
if errorlevel 1 (
    echo.
    echo 启动失败。请确认已完整解压，且未被杀毒软件拦截 runtime 目录。
    pause
)
'@
$batPath = Join-Path $Stage "启动.bat"
$utf8Bom = New-Object System.Text.UTF8Encoding $true
[System.IO.File]::WriteAllText($batPath, $batContent, $utf8Bom)
Copy-Item $batPath (Join-Path $Stage "start.bat") -Force
Write-Host "  + 启动.bat (+ start.bat alias)"

Copy-Item (Join-Path $Repo "RELEASE_PORTABLE.zh-CN.md") (Join-Path $Stage "RELEASE_PORTABLE.zh-CN.md")
Write-Host "  + RELEASE_PORTABLE.zh-CN.md"

Write-Host ""
Write-Host "Creating zip (this may take a while)..." -ForegroundColor Cyan
$ZipTmp = "$ZipPath.tmp.zip"
if (Test-Path $ZipTmp) { Remove-Item -Force $ZipTmp -ErrorAction SilentlyContinue }
Compress-Archive -Path $Stage -DestinationPath $ZipTmp -Force
try {
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath -ErrorAction Stop }
    Move-Item -Force $ZipTmp $ZipPath
} catch {
    $ZipPath = Join-Path $Dist "$Name-built.zip"
    Write-Host "  Note: could not overwrite locked $Name.zip — writing $Name-built.zip" -ForegroundColor Yellow
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue }
    Move-Item -Force $ZipTmp $ZipPath
}

$sizeMb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
Write-Host ""
Write-Host "Done: $ZipPath ($sizeMb MB)" -ForegroundColor Green
Write-Host "Upload to GitHub Release as bidking-lab-v$Version-portable.zip" -ForegroundColor Yellow
