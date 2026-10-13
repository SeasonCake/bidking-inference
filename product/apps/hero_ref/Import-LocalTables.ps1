param(
    [string]$GameRoot = "",
    [string]$AppRoot = $PSScriptRoot,
    [switch]$NoPrompt
)

$ErrorActionPreference = "Stop"

$RequiredTables = @("BidMap.txt", "Drop.txt", "Item.txt")
$OptionalTables = @(
    "Hero.txt",
    "BattleItem.txt",
    "Activity.txt",
    "Map.txt",
    "RankMap.txt",
    "Item_Type.txt",
    "Constant.txt",
    "Cabinet.txt",
    "Condition.txt",
    "ItemRestock.txt",
    "LevelUp.txt"
)
$RootFiles = @("filelist.txt", "fileVersion", "fileDiff.txt")

function Find-TableSource {
    param([string]$Root)
    if (-not $Root) {
        return $null
    }
    $FullRoot = [System.IO.Path]::GetFullPath($Root)
    $Candidates = @(
        $FullRoot,
        (Join-Path $FullRoot "Tables"),
        (Join-Path $FullRoot "BidKing_Data\StreamingAssets"),
        (Join-Path $FullRoot "BidKing_Data\StreamingAssets\Tables")
    )
    foreach ($Candidate in $Candidates) {
        if (-not (Test-Path -LiteralPath $Candidate)) {
            continue
        }
        $Missing = @(
            foreach ($Name in $RequiredTables) {
                if (-not (Test-Path -LiteralPath (Join-Path $Candidate $Name))) {
                    $Name
                }
            }
        )
        if ($Missing.Count -eq 0) {
            return [System.IO.Path]::GetFullPath($Candidate)
        }
    }
    return $null
}

function Find-StreamingAssetsRoot {
    param(
        [string]$GameRoot,
        [string]$TablesSource
    )
    $Candidates = @()
    if ($GameRoot) {
        $FullRoot = [System.IO.Path]::GetFullPath($GameRoot)
        $Candidates += $FullRoot
        $Candidates += (Join-Path $FullRoot "BidKing_Data\StreamingAssets")
    }
    if ($TablesSource) {
        $Parent = Split-Path -Parent $TablesSource
        if ($Parent) {
            $Candidates += $Parent
        }
    }
    foreach ($Candidate in $Candidates | Select-Object -Unique) {
        if (Test-Path -LiteralPath (Join-Path $Candidate "filelist.txt")) {
            return [System.IO.Path]::GetFullPath($Candidate)
        }
    }
    return $null
}

function Get-DefaultBidKingBrowseRoot {
    $Candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Steam\steamapps\common\BidKing"),
        (Join-Path $env:ProgramFiles "Steam\steamapps\common\BidKing"),
        (Join-Path ${env:ProgramFiles(x86)} "Steam\steamapps\common"),
        (Join-Path $env:ProgramFiles "Steam\steamapps\common")
    )
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            return $Candidate
        }
    }
    return $null
}

function Select-GameRoot {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $Dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $Dialog.Description = @"
仅 public-safe 公开包需要此步骤。
full 完整包请直接运行 Start-HeroRef.bat，无需导入。
请选择 BidKing 游戏目录、StreamingAssets 目录，或 Tables 目录。
必须包含 BidMap.txt、Drop.txt、Item.txt。
Steam 示例: ...\steamapps\common\BidKing
"@
        $Dialog.ShowNewFolderButton = $false
        $DefaultRoot = Get-DefaultBidKingBrowseRoot
        if ($DefaultRoot) {
            $Dialog.SelectedPath = $DefaultRoot
        }
        if ($Dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            return $Dialog.SelectedPath
        }
    } catch {
    }
    return Read-Host "请输入 BidKing 游戏目录、StreamingAssets 目录，或 Tables 目录（Steam 示例: ...\steamapps\common\BidKing）"
}

if (-not (Test-Path -LiteralPath $AppRoot)) {
    throw "AppRoot not found: $AppRoot"
}

$TablesDest = Join-Path $AppRoot "data\raw\tables"
$ExistingMissingTables = @(
    foreach ($Name in $RequiredTables) {
        if (-not (Test-Path -LiteralPath (Join-Path $TablesDest $Name))) {
            $Name
        }
    }
)
if ($ExistingMissingTables.Count -eq 0 -and -not $NoPrompt -and -not $GameRoot) {
    Write-Host "检测到包内已有游戏表（full 完整包）。" -ForegroundColor Green
    Write-Host "无需导入，请直接运行 Start-HeroRef.bat。" -ForegroundColor Yellow
    Write-Host "「导入本机游戏表」仅用于 public-safe 公开包。" -ForegroundColor Yellow
    $Continue = Read-Host "仍要重新导入请输入 y，直接按 Enter 退出"
    if ($Continue -ne "y") {
        exit 0
    }
}

if (-not $GameRoot) {
    if ($NoPrompt) {
        throw "GameRoot is required when -NoPrompt is used."
    }
    $GameRoot = Select-GameRoot
}

$TablesSource = Find-TableSource -Root $GameRoot
if (-not $TablesSource) {
    Write-Host "没有找到必要表文件。" -ForegroundColor Red
    Write-Host "请在 Steam 中：右键 BidKing → 管理 → 浏览本地文件，进入含 BidKing.exe 的目录后重试。" -ForegroundColor Yellow
    Write-Host "也可直接选择以下任一目录：" -ForegroundColor Yellow
    Write-Host "  1. BidKing 游戏根目录，例如 ...\steamapps\common\BidKing"
    Write-Host "  2. BidKing_Data\StreamingAssets"
    Write-Host "  3. BidKing_Data\StreamingAssets\Tables"
    Write-Host ""
    Write-Host "必须包含：$($RequiredTables -join ', ')" -ForegroundColor Yellow
    exit 1
}

$RawDest = Join-Path $AppRoot "data\raw"
New-Item -ItemType Directory -Path $TablesDest -Force | Out-Null
New-Item -ItemType Directory -Path $RawDest -Force | Out-Null

Write-Host "表来源: $TablesSource" -ForegroundColor Cyan
Write-Host "导入到: $TablesDest" -ForegroundColor Cyan
Write-Host ""

$Copied = 0
foreach ($Name in ($RequiredTables + $OptionalTables)) {
    $Source = Join-Path $TablesSource $Name
    if (Test-Path -LiteralPath $Source) {
        Copy-Item -LiteralPath $Source -Destination (Join-Path $TablesDest $Name) -Force
        Write-Host "OK $Name"
        $Copied += 1
    } elseif ($RequiredTables -contains $Name) {
        throw "Required table disappeared while copying: $Source"
    }
}

$StreamingAssetsRoot = Find-StreamingAssetsRoot -GameRoot $GameRoot -TablesSource $TablesSource
if ($StreamingAssetsRoot) {
    foreach ($Name in $RootFiles) {
        $Source = Join-Path $StreamingAssetsRoot $Name
        if (Test-Path -LiteralPath $Source) {
            Copy-Item -LiteralPath $Source -Destination (Join-Path $RawDest $Name) -Force
            Write-Host "OK $Name"
            $Copied += 1
        }
    }
}

Write-Host ""
Write-Host "导入完成，共复制 $Copied 个文件。" -ForegroundColor Green
Write-Host "现在可以运行：Start-HeroRef.bat" -ForegroundColor Green
Write-Host "需要任务栏窗口时运行：Start-HeroRef-Taskbar.bat" -ForegroundColor Green
