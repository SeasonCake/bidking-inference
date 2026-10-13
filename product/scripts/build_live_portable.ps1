# Build bidking-live-vX-portable.zip — 当前 Npcap 悬浮窗 live 系统的便携版(内嵌 Python，玩家解压点击即跑)。
# 不是老的 streamlit 网页版(那是 build_release_portable.ps1)。结构镜像仓库 + runtime/python(内嵌)，
# 复用 scripts/start_live_npcap_overlay.ps1 的提权+monitor+overlay 流程(指向内嵌 Python)。
# 运行时依赖(实测): psutil + pydantic + pydantic_core + numpy；0.3.0 另捆绑
# mitmdump 的 process-local WSS 运行时。Npcap 保留为显式回退。
[CmdletBinding()]
param(
    [string]$Version,  # 漏传则从源码常量 HERO_REF_VERSION 取(见下), 绝不静默打成旧 0.2.0(#5)
    [string]$PythonVersion = "3.13.15",
    [string]$BuildPython = "C:\Python31315\python.exe",  # 独立 no-install build layout + 固定依赖
    [switch]$Encrypt,  # 引擎 Cython 编译成 .pyd(中等加密)
    [string]$MasterKeyFile,  # 出厂主密钥(base64)文件路径; 提供则加密上传配置进群发包(无明文 .local.ps1)
    [string]$IntegritySigningKeyFile,  # 离线 Ed25519 32-byte 私钥；只读签名，不进入 Stage/receipt
    [string]$ReleaseCapture2536Path,  # 作者真实实战 raw；仅核 exact bytes/SHA，不进入包或receipt路径
    [string]$ReleaseCapture2408Path,  # 作者真实实战 raw；仅核 exact bytes/SHA，不进入包或receipt路径
    [string]$ReleaseCapture2409Path,  # hotfix2 实战基线 raw；仅核 exact bytes/SHA，不进入包或receipt路径
    [string]$ReleaseCapture4501Path,  # hotfix2 暗拍实战 raw；仅核 exact bytes/SHA，不进入包或receipt路径
    [string]$ReleaseCapture4538Path,  # current activity 实战 raw；仅核 exact bytes/SHA，不进入包或receipt路径
    [switch]$SplitCompatibilityRuntime,
    [ValidateSet("standard", "network-diagnostic")]
    [string]$ProductProfile = "standard",
    [switch]$InspectOnly,
    [switch]$PreflightOnly,
    [string[]]$RuntimeComponentMirrorBase = @(
        "https://downloads.bidking.example.invalid/components"
    )
)
$ErrorActionPreference = "Stop"
if ($InspectOnly -and $PreflightOnly) {
    throw "-InspectOnly 与 -PreflightOnly 不能同时使用。"
}

function Initialize-TrustedFileHashCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$PowerShellHome
    )

    $moduleDirectory = [System.IO.Path]::Combine(
        $PowerShellHome,
        "Modules",
        "Microsoft.PowerShell.Utility"
    )
    $moduleManifest = [System.IO.Path]::Combine(
        $moduleDirectory,
        "Microsoft.PowerShell.Utility.psd1"
    )
    $moduleScript = [System.IO.Path]::Combine(
        $moduleDirectory,
        "Microsoft.PowerShell.Utility.psm1"
    )
    if (-not [System.IO.File]::Exists($moduleManifest)) {
        throw "[build] trusted_file_hash_module_missing"
    }

    try {
        Microsoft.PowerShell.Core\Import-Module `
            -Name $moduleManifest `
            -Force `
            -Scope Local `
            -ErrorAction Stop
    } catch {
        throw "[build] trusted_file_hash_import_failed"
    }

    try {
        $commands = @(
            Microsoft.PowerShell.Core\Get-Command `
                -Name "Microsoft.PowerShell.Utility\Get-FileHash" `
                -All `
                -ErrorAction Stop
        )
    } catch {
        throw "[build] trusted_file_hash_command_missing"
    }
    if ($commands.Count -ne 1) {
        throw "[build] trusted_file_hash_command_untrusted"
    }

    $command = $commands[0]
    $trusted = $false
    try {
        $manifestFullPath = [System.IO.Path]::GetFullPath($moduleManifest)
        $scriptFullPath = [System.IO.Path]::GetFullPath($moduleScript)
        $commandModulePath = [System.IO.Path]::GetFullPath(
            [string]$command.Module.Path
        )
        if (
            $command.ModuleName -ceq "Microsoft.PowerShell.Utility" -and
            $command.CommandType -eq [System.Management.Automation.CommandTypes]::Cmdlet
        ) {
            $trusted = [string]::Equals(
                $commandModulePath,
                $manifestFullPath,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        } elseif (
            $command.ModuleName -ceq "Microsoft.PowerShell.Utility" -and
            $command.CommandType -eq [System.Management.Automation.CommandTypes]::Function
        ) {
            $commandScriptPath = [System.IO.Path]::GetFullPath(
                [string]$command.ScriptBlock.File
            )
            $trusted = (
                [string]::Equals(
                    $commandModulePath,
                    $scriptFullPath,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -and
                [string]::Equals(
                    $commandScriptPath,
                    $scriptFullPath,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            )
        }
    } catch {
        $trusted = $false
    }
    if (-not $trusted) {
        throw "[build] trusted_file_hash_command_untrusted"
    }
    return $command
}

function ConvertTo-NativeDiagnosticSmokeProcessArgument([string]$Value) {
    if (
        [string]::IsNullOrEmpty($Value) -or
        $Value.Contains('"') -or
        $Value.EndsWith("\", [System.StringComparison]::Ordinal)
    ) {
        throw "native diagnostic smoke process 参数无效。"
    }
    return '"' + $Value + '"'
}

function Invoke-NativeDiagnosticSmokeReceiptLease {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$RunnerPath,
        [Parameter(Mandatory = $true)][string]$ReceiptPath
    )

    $result = [ordered]@{
        Status = "bind_failed"
        BoundIdentity = ""
    }
    $process = $null
    $processStarted = $false
    $commandSent = $false
    try {
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $PythonPath
        $startInfo.Arguments = @(
            "-E", "-s", "-B",
            (ConvertTo-NativeDiagnosticSmokeProcessArgument $RunnerPath),
            "hold",
            (ConvertTo-NativeDiagnosticSmokeProcessArgument $ReceiptPath)
        ) -join " "
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            return [pscustomobject]$result
        }
        $processStarted = $true
        $process.StandardInput.NewLine = "`n"

        $boundTask = $process.StandardOutput.ReadLineAsync()
        if (-not $boundTask.Wait(15000)) {
            return [pscustomobject]$result
        }
        $boundLine = [string]$boundTask.Result
        $boundPattern = '\A\{"bytes":(?<bytes>[1-9][0-9]{0,2}),"file_index":(?<file_index>[1-9][0-9]{0,19}),"sha256":"[0-9a-f]{64}","status":"bound","volume_serial_number":[0-9]{1,10}\}\z'
        $boundMatch = [regex]::Match($boundLine, $boundPattern)
        if (-not $boundMatch.Success -or
            [uint32]$boundMatch.Groups["bytes"].Value -gt 256) {
            return [pscustomobject]$result
        }
        try {
            [void][uint64]::Parse(
                $boundMatch.Groups["file_index"].Value,
                [System.Globalization.CultureInfo]::InvariantCulture
            )
        } catch {
            return [pscustomobject]$result
        }
        $result.BoundIdentity = $boundLine

        $process.StandardInput.WriteLine("delete")
        $process.StandardInput.Flush()
        $commandSent = $true
        $finalTask = $process.StandardOutput.ReadLineAsync()
        if (-not $finalTask.Wait(15000)) {
            $result.Status = "cleanup_conflict"
            return [pscustomobject]$result
        }
        $finalLine = [string]$finalTask.Result
        if (-not $process.WaitForExit(15000)) {
            $result.Status = "cleanup_conflict"
            return [pscustomobject]$result
        }
        if (
            $finalLine -ceq '{"status":"deleted"}' -and
            $process.ExitCode -eq 0 -and
            -not (Test-Path -LiteralPath $ReceiptPath)
        ) {
            $result.Status = "deleted"
        } else {
            $result.Status = "cleanup_conflict"
        }
        return [pscustomobject]$result
    } catch {
        if ($result.BoundIdentity) {
            $result.Status = "cleanup_conflict"
        }
        return [pscustomobject]$result
    } finally {
        if ($null -ne $process) {
            $processExited = $false
            if ($processStarted) {
                try {
                    $processExited = $process.HasExited
                } catch {
                    $processExited = $false
                }
            }
            if ($processStarted -and -not $processExited) {
                if (-not $commandSent) {
                    try {
                        $process.StandardInput.WriteLine("abort")
                        $process.StandardInput.Flush()
                    } catch {
                        # Closing a task-owned holder still retains the receipt.
                    }
                }
                try {
                    [void]$process.StandardInput.Close()
                    if (-not $process.WaitForExit(5000)) {
                        $process.Kill()
                        [void]$process.WaitForExit(5000)
                    }
                    $processExited = $process.HasExited
                } catch {
                    # Killing only this task-owned holder releases its read handle;
                    # it never authorizes receipt deletion.
                }
            }
            if ($processExited) {
                try {
                    [void]$process.StandardError.ReadToEnd()
                } catch {
                    # Error bytes are intentionally discarded and never enter a gate.
                }
            }
            $process.Dispose()
        }
    }
}

$TrustedFileHashCommand = Initialize-TrustedFileHashCommand `
    -PowerShellHome $PSHOME
# FILE_HASH_TRUST_BOOTSTRAP_END

function Copy-BuildStageMutableLeaf {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$StageRoot,
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath,
        [Parameter(Mandatory = $true)][string]$ExpectedSHA256,
        [int64]$ExpectedBytes = -1
    )

    if ($ExpectedSHA256 -cnotmatch '\A[0-9a-f]{64}\z' -or $ExpectedBytes -lt -1) {
        throw "[build] stage_mutable_copy_expected_identity_invalid"
    }
    $windowsAbsolutePathPattern = '\A(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
    foreach ($pathValue in @($StageRoot, $SourcePath, $DestinationPath)) {
        if ([string]::IsNullOrWhiteSpace($pathValue) -or
            -not [System.Text.RegularExpressions.Regex]::IsMatch(
                $pathValue,
                $windowsAbsolutePathPattern,
                [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
            ) -or
            [System.Text.RegularExpressions.Regex]::IsMatch(
                $pathValue,
                '(?:\A|[\\/])\.{1,2}(?:[\\/]|$)',
                [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
            )) {
            throw "[build] stage_mutable_copy_path_invalid"
        }
    }
    try {
        $stageFullPath = [System.IO.Path]::GetFullPath($StageRoot).TrimEnd("\", "/")
        $sourceFullPath = [System.IO.Path]::GetFullPath($SourcePath)
        $destinationFullPath = [System.IO.Path]::GetFullPath($DestinationPath)
        $stagePathRoot = [System.IO.Path]::GetPathRoot($stageFullPath).TrimEnd("\", "/")
    } catch {
        throw "[build] stage_mutable_copy_path_invalid"
    }
    if ([string]::IsNullOrWhiteSpace($stageFullPath) -or
        [string]::Equals(
            $stageFullPath,
            $stagePathRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "[build] stage_mutable_copy_path_invalid"
    }
    $stagePrefix = $stageFullPath + [System.IO.Path]::DirectorySeparatorChar
    if (-not $destinationFullPath.StartsWith(
            $stagePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        [string]::Equals(
            $sourceFullPath,
            $destinationFullPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "[build] stage_mutable_copy_out_of_scope"
    }

    try {
        $stageAttributes = [System.IO.File]::GetAttributes($stageFullPath)
        $sourceAttributes = [System.IO.File]::GetAttributes($sourceFullPath)
    } catch {
        throw "[build] stage_mutable_copy_input_invalid"
    }
    if (($stageAttributes -band [System.IO.FileAttributes]::Directory) -eq 0 -or
        ($stageAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not [System.IO.File]::Exists($sourceFullPath) -or
        ($sourceAttributes -band [System.IO.FileAttributes]::Directory) -ne 0 -or
        ($sourceAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "[build] stage_mutable_copy_input_invalid"
    }

    $destinationParent = [System.IO.Path]::GetDirectoryName($destinationFullPath)
    if ([string]::IsNullOrWhiteSpace($destinationParent)) {
        throw "[build] stage_mutable_copy_path_invalid"
    }
    $currentDirectory = [System.IO.DirectoryInfo]::new($destinationParent)
    while ($null -ne $currentDirectory) {
        try {
            $currentAttributes = [System.IO.File]::GetAttributes(
                $currentDirectory.FullName
            )
        } catch {
            throw "[build] stage_mutable_copy_destination_invalid"
        }
        if (($currentAttributes -band [System.IO.FileAttributes]::Directory) -eq 0 -or
            ($currentAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "[build] stage_mutable_copy_destination_invalid"
        }
        if ([string]::Equals(
                $currentDirectory.FullName.TrimEnd("\", "/"),
                $stageFullPath,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            break
        }
        $currentDirectory = $currentDirectory.Parent
    }
    if ($null -eq $currentDirectory -or [System.IO.File]::Exists($destinationFullPath) -or
        [System.IO.Directory]::Exists($destinationFullPath)) {
        throw "[build] stage_mutable_copy_destination_invalid"
    }

    $sourceStream = $null
    $destinationStream = $null
    try {
        $sourceStream = [System.IO.FileStream]::new(
            $sourceFullPath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read,
            1048576,
            [System.IO.FileOptions]::SequentialScan
        )
        # STAGE_MUTABLE_SOURCE_HANDLE_BOUND
        $boundSourceAttributes = [System.IO.File]::GetAttributes($sourceFullPath)
        if (($boundSourceAttributes -band [System.IO.FileAttributes]::Directory) -ne 0 -or
            ($boundSourceAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $boundSourceAttributes -ne $sourceAttributes) {
            throw "[build] stage_mutable_copy_input_invalid"
        }
        $sourceLength = [int64]$sourceStream.Length
        if ($ExpectedBytes -ge 0 -and $sourceLength -ne $ExpectedBytes) {
            throw "[build] stage_mutable_copy_source_identity_mismatch"
        }
        $sourceHasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $sourceHashBytes = $sourceHasher.ComputeHash($sourceStream)
        } finally {
            $sourceHasher.Dispose()
        }
        $sourceSHA256 = (
            [System.BitConverter]::ToString($sourceHashBytes).Replace("-", "").ToLowerInvariant()
        )
        if ($sourceSHA256 -cne $ExpectedSHA256 -or
            [int64]$sourceStream.Length -ne $sourceLength) {
            throw "[build] stage_mutable_copy_source_identity_mismatch"
        }
        $sourceStream.Position = 0

        $destinationStream = [System.IO.FileStream]::new(
            $destinationFullPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None,
            1048576,
            [System.IO.FileOptions]::SequentialScan
        )
        # STAGE_MUTABLE_DESTINATION_HANDLE_BOUND
        $sourceStream.CopyTo($destinationStream, 1048576)
        $destinationStream.Flush($true)
        if ([int64]$sourceStream.Position -ne $sourceLength -or
            [int64]$destinationStream.Length -ne $sourceLength) {
            throw "[build] stage_mutable_copy_length_mismatch"
        }
        $destinationStream.Position = 0
        $destinationHasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $destinationHashBytes = $destinationHasher.ComputeHash($destinationStream)
        } finally {
            $destinationHasher.Dispose()
        }
        $destinationSHA256 = (
            [System.BitConverter]::ToString($destinationHashBytes).Replace("-", "").ToLowerInvariant()
        )
        $destinationAttributes = [System.IO.File]::GetAttributes($destinationFullPath)
        if ($destinationSHA256 -cne $ExpectedSHA256 -or
            [int64]$destinationStream.Length -ne $sourceLength -or
            ($destinationAttributes -band [System.IO.FileAttributes]::Directory) -ne 0 -or
            ($destinationAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($destinationAttributes -band [System.IO.FileAttributes]::ReadOnly) -ne 0 -or
            [System.IO.File]::GetAttributes($sourceFullPath) -ne $sourceAttributes) {
            throw "[build] stage_mutable_copy_final_identity_mismatch"
        }
    } catch {
        if ($_.Exception.Message.StartsWith(
                "[build]",
                [System.StringComparison]::Ordinal
            )) {
            throw
        }
        throw "[build] stage_mutable_copy_failed"
    } finally {
        if ($null -ne $destinationStream) {
            $destinationStream.Dispose()
        }
        if ($null -ne $sourceStream) {
            $sourceStream.Dispose()
        }
    }
}
# BUILD_STAGE_MUTABLE_COPY_HELPER_END

function Get-ValidatedMutableStageMoatMembers {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object]$ReleaseContract,
        [Parameter(Mandatory = $true)][object[]]$PackageDataFiles,
        [Parameter(Mandatory = $true)][string]$StaticDataMember,
        [Parameter(Mandatory = $true)][string]$PlaintextPackageMember
    )

    $structureError = "[build] release_member_protection_invalid"
    $setError = "[build] mutable_stage_moat_contract_invalid"
    if ($null -eq $ReleaseContract -or
        $ReleaseContract.GetType() -ne [System.Management.Automation.PSCustomObject]) {
        throw $structureError
    }
    $protectionProperty = $ReleaseContract.PSObject.Properties["member_protection"]
    if ($null -eq $protectionProperty -or $null -eq $protectionProperty.Value -or
        $protectionProperty.Value.GetType() -ne
            [System.Management.Automation.PSCustomObject]) {
        throw $structureError
    }

    $allowedModes = @("bkenc1", "bkenc2-gzip", "plaintext")
    $mutableMembers = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($memberProperty in @($protectionProperty.Value.PSObject.Properties)) {
        $rule = $memberProperty.Value
        if ($null -eq $rule -or
            $rule.GetType() -ne [System.Management.Automation.PSCustomObject]) {
            throw $structureError
        }
        $ruleProperties = @($rule.PSObject.Properties)
        $ruleNames = @($ruleProperties | ForEach-Object { [string]$_.Name })
        if ($ruleProperties.Count -ne 2 -or
            -not ($ruleNames -ccontains "mode") -or
            -not ($ruleNames -ccontains "required") -or
            $rule.mode -isnot [string] -or
            $allowedModes -cnotcontains $rule.mode -or
            $rule.required -isnot [bool]) {
            throw $structureError
        }
        if ($rule.mode -ceq "bkenc2-gzip" -and $rule.required) {
            if (-not $mutableMembers.Add([string]$memberProperty.Name)) {
                throw $structureError
            }
        }
    }

    $packageMembers = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    $expectedMutableMembers = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($packageMember in $PackageDataFiles) {
        if ($packageMember -isnot [string] -or
            [string]::IsNullOrEmpty($packageMember) -or
            -not $packageMembers.Add($packageMember)) {
            throw $setError
        }
        if ($packageMember -cne $PlaintextPackageMember) {
            [void]$expectedMutableMembers.Add($packageMember)
        }
    }
    if (-not $packageMembers.Contains($PlaintextPackageMember) -or
        $packageMembers.Contains($StaticDataMember) -or
        -not $expectedMutableMembers.Add($StaticDataMember)) {
        throw $setError
    }

    $plaintextProperty = $protectionProperty.Value.PSObject.Properties[
        $PlaintextPackageMember
    ]
    $staticProperty = $protectionProperty.Value.PSObject.Properties[$StaticDataMember]
    if ($null -eq $plaintextProperty -or
        $plaintextProperty.Value.mode -cne "plaintext" -or
        $plaintextProperty.Value.required -isnot [bool] -or
        -not $plaintextProperty.Value.required -or
        $null -eq $staticProperty -or
        $staticProperty.Value.mode -cne "bkenc2-gzip" -or
        $staticProperty.Value.required -isnot [bool] -or
        -not $staticProperty.Value.required -or
        $mutableMembers.Count -ne 21 -or
        -not $mutableMembers.SetEquals($expectedMutableMembers)) {
        throw $setError
    }
    return @($mutableMembers | Sort-Object)
}
# BUILD_STAGE_MUTABLE_CONTRACT_VALIDATOR_END

function ConvertTo-ControlledWindowsAbsolutePath {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$PathValue
    )
    $invalid = (
        [string]::IsNullOrWhiteSpace($PathValue) -or
        -not [string]::Equals(
            $PathValue,
            $PathValue.Trim(),
            [System.StringComparison]::Ordinal
        )
    )
    if (-not $invalid) {
        foreach ($character in $PathValue.ToCharArray()) {
            if ([int][char]$character -lt 0x20) {
                $invalid = $true
                break
            }
        }
    }
    if (-not $invalid -and $PathValue.IndexOfAny(
            [char[]]@('*', '?', '"', '<', '>', '|', '=')
        ) -ge 0) {
        $invalid = $true
    }
    $driveRooted = (-not $invalid) -and [System.Text.RegularExpressions.Regex]::IsMatch(
        $PathValue,
        '\A[A-Za-z]:[\\/](?:[^\\/]+[\\/])*[^\\/]*\z',
        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    $uncRooted = (-not $invalid) -and [System.Text.RegularExpressions.Regex]::IsMatch(
        $PathValue,
        '\A(?:\\\\|//)(?![?.][\\/])[^\\/]+[\\/][^\\/]+' +
            '(?:[\\/][^\\/]+)*[\\/]?\z',
        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (-not $driveRooted -and -not $uncRooted) {
        $invalid = $true
    }
    if (-not $invalid) {
        $tail = if ($driveRooted) { $PathValue.Substring(2) } else { $PathValue }
        if ($tail.Contains(':')) {
            $invalid = $true
        }
    }
    if (-not $invalid) {
        foreach ($segment in [System.Text.RegularExpressions.Regex]::Split(
                $PathValue,
                '[\\/]+'
            )) {
            $reservedDevice = [System.Text.RegularExpressions.Regex]::IsMatch(
                $segment,
                '\A(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\z',
                [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor
                    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
            )
            if ($segment -ceq '.' -or $segment -ceq '..' -or $reservedDevice -or
                ($segment.Length -gt 0 -and
                    $segment.TrimEnd([char[]]@(' ', '.')).Length -ne $segment.Length)) {
                $invalid = $true
                break
            }
        }
    }
    if ($invalid) {
        throw "Rust 路径映射源不是受控 Windows 绝对路径，拒绝构建。"
    }
    try {
        $fullPath = [System.IO.Path]::GetFullPath($PathValue)
        $root = [System.IO.Path]::GetPathRoot($fullPath)
    } catch {
        throw "Rust 路径映射源无法规范化，拒绝构建。"
    }
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw "Rust 路径映射源缺 Windows 根，拒绝构建。"
    }
    $trimChars = [char[]]@('\', '/')
    $trimmed = $fullPath.TrimEnd($trimChars)
    $trimmedRoot = $root.TrimEnd($trimChars)
    if ([string]::Equals(
            $trimmed,
            $trimmedRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        return $root
    }
    return $trimmed
}

function Get-ControlledRustPathMappings {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )
    $repository = ConvertTo-ControlledWindowsAbsolutePath $RepositoryRoot
    $profileRoot = ConvertTo-ControlledWindowsAbsolutePath (
        [Environment]::GetEnvironmentVariable("USERPROFILE")
    )
    $cargoHomeValue = [Environment]::GetEnvironmentVariable("CARGO_HOME")
    if ($null -eq $cargoHomeValue -or $cargoHomeValue.Length -eq 0) {
        $cargoHomeValue = [System.IO.Path]::Combine($profileRoot, ".cargo")
    }
    $cargoHome = ConvertTo-ControlledWindowsAbsolutePath $cargoHomeValue
    $rustupHomeValue = [Environment]::GetEnvironmentVariable("RUSTUP_HOME")
    if ($null -eq $rustupHomeValue -or $rustupHomeValue.Length -eq 0) {
        $rustupHomeValue = [System.IO.Path]::Combine($profileRoot, ".rustup")
    }
    $rustupHome = ConvertTo-ControlledWindowsAbsolutePath $rustupHomeValue
    $cargoRegistry = ConvertTo-ControlledWindowsAbsolutePath (
        [System.IO.Path]::Combine($cargoHome, "registry", "src")
    )
    $actualTempRoot = ConvertTo-ControlledWindowsAbsolutePath (
        [System.IO.Path]::GetTempPath()
    )
    $declaredTempRoots = @()
    foreach ($tempVariable in @("TEMP", "TMP")) {
        $tempValue = [Environment]::GetEnvironmentVariable($tempVariable)
        if ($null -eq $tempValue -or $tempValue.Length -eq 0) {
            continue
        }
        $declaredTempRoots += ConvertTo-ControlledWindowsAbsolutePath $tempValue
    }
    if ($declaredTempRoots.Count -gt 0) {
        $actualDeclared = $false
        foreach ($declaredTempRoot in $declaredTempRoots) {
            if ([string]::Equals(
                    $actualTempRoot,
                    $declaredTempRoot,
                    [System.StringComparison]::OrdinalIgnoreCase
                )) {
                $actualDeclared = $true
                break
            }
        }
        if (-not $actualDeclared) {
            throw "实际临时目录与 TEMP/TMP 声明不一致，拒绝构建。"
        }
    }
    $rawMappings = @(
        [PSCustomObject]@{ Source = $repository; PublicPrefix = "." },
        [PSCustomObject]@{
            Source = $cargoRegistry
            PublicPrefix = "<cargo-registry>"
        },
        [PSCustomObject]@{ Source = $cargoHome; PublicPrefix = "<cargo-home>" },
        [PSCustomObject]@{ Source = $rustupHome; PublicPrefix = "<rust-toolchain>" }
    )
    foreach ($tempRoot in @($actualTempRoot) + @($declaredTempRoots)) {
        $rawMappings += [PSCustomObject]@{
            Source = $tempRoot
            PublicPrefix = "<build-temp>"
        }
    }
    $rawMappings += [PSCustomObject]@{
        Source = $profileRoot
        PublicPrefix = "<build-user>"
    }
    $seen = [System.Collections.Generic.Dictionary[string, string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $mappings = @()
    foreach ($mapping in $rawMappings) {
        $source = [string]$mapping.Source
        $publicPrefix = [string]$mapping.PublicPrefix
        $existingPrefix = $null
        if ($seen.TryGetValue($source, [ref]$existingPrefix)) {
            if ($existingPrefix -cne $publicPrefix) {
                throw "Rust 路径映射来源分类冲突，拒绝构建。"
            }
            continue
        }
        $seen.Add($source, $publicPrefix)
        $mappings += [PSCustomObject]@{
            Source = $source
            PublicPrefix = $publicPrefix
        }
    }
    return @($mappings)
}

function Restore-ControlledCargoEncodedRustFlags {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$WasPresent,
        [Parameter(Mandatory = $false)]
        [AllowNull()]
        [string]$PreviousValue
    )
    if ($WasPresent) {
        if ($null -eq $PreviousValue) {
            throw "CARGO_ENCODED_RUSTFLAGS 恢复状态不一致，拒绝继续。"
        }
        [Environment]::SetEnvironmentVariable(
            "CARGO_ENCODED_RUSTFLAGS",
            $PreviousValue,
            [EnvironmentVariableTarget]::Process
        )
    } else {
        Remove-Item Env:\CARGO_ENCODED_RUSTFLAGS -ErrorAction SilentlyContinue
    }
}

function Invoke-NativeArtifactPrivacyGate {
    param(
        [Parameter(Mandatory = $true)][string]$ArtifactPath,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$ForbiddenPaths
    )
    if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
        throw "$Label 隐私门缺构建产物。"
    }
    $privacyArgs = @("--audit-private-build-artifact", $ArtifactPath)
    foreach ($forbiddenPath in $ForbiddenPaths) {
        $privacyArgs += @("--forbid-path", $forbiddenPath)
    }
    $previousPrivacyEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $privacyOutput = (& $BuildPython -X utf8 -I -B $zipAuditor @privacyArgs 2>&1 | Out-String)
        $privacyExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPrivacyEap
    }
    if ($privacyExit -ne 0) {
        throw "$Label 含私有构建路径，拒绝进入 Stage。扫描结果: $privacyOutput"
    }
    Write-Host "  + $Label 私有构建路径扫描通过" -ForegroundColor Green
}

function Get-ClientNativeModuleArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$ModuleName,
        [Parameter(Mandatory = $true)][string[]]$AllowedAbiTags
    )
    $expectedNames = @(
        $AllowedAbiTags | ForEach-Object { "$ModuleName.$_.pyd" }
    )
    if ($expectedNames.Count -eq 0) {
        throw "native_module_abi_contract_invalid"
    }
    $matches = @(
        Get-ChildItem -LiteralPath $Directory -Filter "$ModuleName*.pyd" -File -ErrorAction SilentlyContinue |
            Where-Object { $expectedNames -ccontains $_.Name }
    )
    if ($matches.Count -ne 1) {
        throw "native_module_artifact_count_invalid"
    }
    $untrusted = @(
        Get-ChildItem -LiteralPath $Directory -Filter "$ModuleName*.pyd" -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name.StartsWith(
                    "$ModuleName.",
                    [System.StringComparison]::Ordinal
                ) -and
                $expectedNames -cnotcontains $_.Name
            }
    )
    if ($untrusted.Count -gt 0) {
        throw "native_module_artifact_untrusted"
    }
    return $matches[0]
}

$ProductProfile = $ProductProfile.ToLowerInvariant()
$Repo = Split-Path -Parent $PSScriptRoot
$PublicDiagnosticProfile = "portable"
$IsNetworkDiagnosticCohort = ($ProductProfile -ceq "network-diagnostic")
$CommercialSurfaceMode = if ($IsNetworkDiagnosticCohort) {
    "disabled-network-diagnostic-cohort"
} else {
    "standard"
}
# 版本单一真源 = hero_ref\tools\hero_ref_version.py 的 HERO_REF_VERSION。漏传时采用源码值；
# 显式传入不同值也 fail-closed，避免 manifest/包名覆盖源码版本并破坏现场归因。
$verFile = Join-Path $Repo "hero_ref\tools\hero_ref_version.py"
$vm = Select-String -Path $verFile -Pattern 'HERO_REF_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $vm) { throw "无法从 $verFile 读取 HERO_REF_VERSION，拒绝构建。" }
$SourceVersion = $vm.Matches[0].Groups[1].Value
if (-not $Version) {
    $Version = $SourceVersion
    Write-Host "  -Version 未指定 -> 采用源码常量 HERO_REF_VERSION=$Version" -ForegroundColor Yellow
} elseif ($Version -cne $SourceVersion) {
    throw "显式 -Version=$Version 与源码 HERO_REF_VERSION=$SourceVersion 不一致，拒绝构建。请先建立明确版本边界。"
}
# 0.3.0 公版仍在受支持范围，其版本提示只认识 base / rN / hotfixN。若这里放行
# hotfixN-rN 等新展示语法，旧客户端虽然成功拿到 /version，却会把 latest 判为畸形并
# 静默隐藏“有新版”。提高最低支持版本前，候选版本必须保持最老受支持客户端可解析。
$OldestSupportedClientVersionPattern = '\A[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(?:-(?:[rR]|[hH][oO][tT][fF][iI][xX])[0-9]{1,9})?\z'
$OldestSupportedClientVersionIsValid = [System.Text.RegularExpressions.Regex]::IsMatch(
    $Version,
    $OldestSupportedClientVersionPattern,
    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
)
if (-not $OldestSupportedClientVersionIsValid) {
    throw "Version=$Version 无法由最老受支持客户端解析，拒绝构建。请使用 base/rN/hotfixN，或先建立最低支持版本迁移边界。"
}

# 主 runtime source 必须来自独立、精确冻结的 CPython + wheel 基线。manifest 同时绑定
# 官方 embed 下载、build interpreter、cp313 ABI 与安装依赖；不得从 PATH 或 C:\Python313
# 静默回退。这里只读核验，不安装、不修改 PATH。
$RuntimePythonSourceManifestPath = Join-Path $Repo "scripts\runtime_python_source_manifest.json"
try {
    $RuntimePythonSource = Get-Content -LiteralPath $RuntimePythonSourceManifestPath `
        -Raw -Encoding utf8 | ConvertFrom-Json
} catch {
    throw "runtime Python source manifest 不可读取或格式无效，拒绝构建。"
}
if (
    $RuntimePythonSource.schema -cne "bidking_runtime_python_source_v1" -or
    $RuntimePythonSource.manifest_version -is [bool] -or
    [int64]$RuntimePythonSource.manifest_version -ne 1 -or
    -not $RuntimePythonSource.python -or
    -not $RuntimePythonSource.python.embed -or
    -not $RuntimePythonSource.python.build_layout -or
    -not $RuntimePythonSource.runtime_dependencies -or
    -not $RuntimePythonSource.build_tools -or
    -not $RuntimePythonSource.layout_tools
) {
    throw "runtime Python source manifest schema/边界不完整，拒绝构建。"
}
$RuntimePythonSourceManifestSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $RuntimePythonSourceManifestPath -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($PythonVersion -cne [string]$RuntimePythonSource.python.version) {
    throw "PythonVersion=$PythonVersion 与冻结 runtime source 不一致，拒绝构建。"
}
if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    throw "独立 build Python 不存在: $BuildPython"
}
$BuildPythonSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $BuildPython -Algorithm SHA256
).Hash.ToLowerInvariant()
if (
    $BuildPythonSHA256 -cne
        [string]$RuntimePythonSource.python.build_layout.python_exe_sha256
) {
    throw "build Python executable 与冻结 runtime source 不一致，拒绝构建。"
}
$BuildPythonIdentityOutput = @(
    & $BuildPython -E -s -c "import json,sys,sysconfig; print(json.dumps({'version':'.'.join(map(str,sys.version_info[:3])),'cache_tag':sys.implementation.cache_tag,'platform':sysconfig.get_platform()},sort_keys=True,separators=(',',':')))" 2>&1
)
if ($LASTEXITCODE -ne 0 -or $BuildPythonIdentityOutput.Count -ne 1) {
    throw "独立 build Python 身份探针失败，拒绝构建。"
}
try {
    $BuildPythonIdentity = $BuildPythonIdentityOutput[0] | ConvertFrom-Json
} catch {
    throw "独立 build Python 身份输出无效，拒绝构建。"
}
if (
    $BuildPythonIdentity.version -cne [string]$RuntimePythonSource.python.version -or
    $BuildPythonIdentity.cache_tag -cne [string]$RuntimePythonSource.python.cache_tag -or
    $BuildPythonIdentity.platform -cne [string]$RuntimePythonSource.python.platform
) {
    throw "独立 build Python version/cache_tag/platform 与冻结 source 不一致，拒绝构建。"
}
$BuildPythonDistributionOutput = @(
    & $BuildPython -E -s -c "import importlib.metadata as m,json,re; c=lambda s:re.sub(r'[-_.]+','-',s).lower(); d={}; [(d.setdefault(c(x.metadata['Name']),[]).append(x.version)) for x in m.distributions() if x.metadata.get('Name')]; print(json.dumps(d,sort_keys=True,separators=(',',':')))" 2>&1
)
if ($LASTEXITCODE -ne 0 -or $BuildPythonDistributionOutput.Count -ne 1) {
    throw "独立 build Python distribution inventory 探针失败，拒绝构建。"
}
try {
    $BuildPythonDistributions = $BuildPythonDistributionOutput[0] | ConvertFrom-Json
} catch {
    throw "独立 build Python distribution inventory 输出无效，拒绝构建。"
}
$ExpectedBuildDistributions = @{}
foreach ($property in $RuntimePythonSource.runtime_dependencies.PSObject.Properties) {
    $ExpectedBuildDistributions[[string]$property.Name] = [string]$property.Value
}
foreach ($property in $RuntimePythonSource.build_tools.PSObject.Properties) {
    $ExpectedBuildDistributions[[string]$property.Name] = [string]$property.Value
}
foreach ($property in $RuntimePythonSource.layout_tools.PSObject.Properties) {
    $ExpectedBuildDistributions[[string]$property.Name] = [string]$property.Value
}
foreach ($name in $ExpectedBuildDistributions.Keys) {
    $installed = @($BuildPythonDistributions.PSObject.Properties[$name].Value)
    if ($installed.Count -ne 1 -or $installed[0] -cne $ExpectedBuildDistributions[$name]) {
        throw "独立 build Python distribution 与冻结 source 不一致: $name"
    }
}

# 成品源码溯源：只检查会进入包或直接参与构建的 Git 路径，忽略共享工作树中与成品无关的
# 报告/临时文件。加密候选必须严格对应一个 commit；否则 BUILD_MANIFEST 即使写了 HEAD，
# 实际 .pyd 仍可能混入另一个窗口尚未提交的源码，事后无法复现或审计。
$SourceCommit = ((& git -C $Repo rev-parse HEAD 2>&1) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $SourceCommit) {
    throw "无法读取 Git HEAD，拒绝生成不可溯源的候选包。"
}
$SourceBranch = ((& git -C $Repo rev-parse --abbrev-ref HEAD 2>&1) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $SourceBranch) {
    throw "无法读取 Git 分支，拒绝生成不可溯源的候选包。"
}
$packageInputPaths = @(
    ".gitattributes", "LICENSE", "activation_service_endpoints.json", "activation_http_e2ee.json",
    "hero_ref/src", "hero_ref/tools", "hero_ref/assets",
    "src/bidking_lab",
    "scripts/build_live_portable.ps1", "scripts/check_package_data_integrity.py",
    "scripts/check_hotfix3_real_drop_pool_quote_runtime.py",
    "scripts/audit_release_zip.py", "scripts/client_protection_manifest.json",
    "scripts/native_release_hardening.py", "scripts/release_native_binary_contract.json",
    "scripts/cython", "scripts/release_zip_contract.json",
    "scripts/runtime_python_source_manifest.json",
    "scripts/runtime_python_inventory_manifest.json",
    "scripts/package_data_manifest.json",
    "scripts/package_data_fingerprints.json",
    "scripts/verify_release_asset_inventory.py",
    "scripts/mitmproxy_runtime_manifest.json", "scripts/pet_companion_assets_manifest.json",
    "scripts/encrypt_upload_config.py", "scripts/verify_release_master_key.py", "scripts/encrypt_moat_files.py",
    "scripts/run_npcap_live_monitor.py", "scripts/run_wss_live_monitor.py",
    "scripts/mitm_bidking_wss_addon.py", "scripts/bidking_wss_capture_core.py",
    "scripts/build_wss_addon_bootstrap.py", "scripts/prepare_client_lifecycle_payload.py",
    "scripts/build_release_runtime_component.py", "scripts/runtime_component.py",
    "scripts/build_local_integrity_manifest.py", "scripts/local_integrity_contract.json",
    "scripts/local_compat_component.py",
    "scripts/_local_compat_core.py", "scripts/local_compat_launcher.py",
    "scripts/run_fatbeans_webhook_monitor.py", "scripts/package_generation_identity.py",
    "scripts/run_windivert_live_monitor.py", "scripts/start_live_npcap_overlay.ps1",
    "scripts/live_process_discovery.ps1", "scripts/startup_repair.ps1",
    "scripts/startup_repair.bat", "scripts/remove_wss_capture_ca.ps1",
    "scripts/wss_environment_preflight.ps1", "scripts/wss_environment_preflight.bat",
    "scripts/live_status.ps1", "scripts/stop_live_monitor.ps1", "scripts/collect_diagnostics.ps1",
    "scripts/collect_diagnostics.bat", "scripts/activation_transport_diagnostic.py",
    "scripts/activation_transport_diagnostic.bat",
    "scripts/match_records_html.py", "scripts/build_match_records.py", "scripts/pkg_assets",
    "data/processed", "data/raw/tables",
    "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs",
    "native/bidking_kernel", "native/bidking_trust_bootstrap"
)
$PackageInputStatus = @(& git --no-optional-locks -C $Repo status --porcelain=v1 --untracked-files=all -- @packageInputPaths)
if ($LASTEXITCODE -ne 0) {
    throw "无法核对候选包输入树状态，拒绝继续打包。"
}
$PackageInputsDirty = ($PackageInputStatus.Count -gt 0)
if ($Encrypt -and $PackageInputsDirty -and -not $InspectOnly) {
    throw "加密候选的包输入树存在 $($PackageInputStatus.Count) 项未提交修改，无法保证成品对应 SourceCommit=$SourceCommit。请由所属窗口提交后重试；不得清理或覆盖共享工作树。"
}
$ActivationServiceEndpointsPath = Join-Path $Repo "activation_service_endpoints.json"
if (-not (Test-Path -LiteralPath $ActivationServiceEndpointsPath -PathType Leaf)) {
    throw "缺少受控激活入口配置，拒绝构建。"
}
$ActivationEndpointProbe = @'
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo / "src"))
from bidking_lab.live.version_check import load_activation_service_endpoint_config

config = load_activation_service_endpoint_config(Path(sys.argv[2]))
print(json.dumps({
    "generation": config.generation,
    "endpoint_3_enabled": config.endpoint_3_enabled,
    "raw_sha256": config.raw_sha256,
}, sort_keys=True, separators=(",", ":")))
'@
$PythonSourceProbeLauncher = "import base64,sys;exec(base64.b64decode(sys.argv.pop(1)))"
$ActivationEndpointProbeBase64 = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($ActivationEndpointProbe)
)
$ActivationEndpointProbeOutput = @(
    & $BuildPython -E -B -c $PythonSourceProbeLauncher `
        $ActivationEndpointProbeBase64 $Repo $ActivationServiceEndpointsPath 2>&1
)
if ($LASTEXITCODE -ne 0 -or $ActivationEndpointProbeOutput.Count -ne 1) {
    throw "受控激活入口配置未通过严格解析，拒绝构建。"
}
try {
    $ActivationEndpointIdentity = $ActivationEndpointProbeOutput[0] | ConvertFrom-Json
} catch {
    throw "受控激活入口配置身份输出无效，拒绝构建。"
}
$ActivationServiceEndpointsSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $ActivationServiceEndpointsPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$ActivationServiceEndpointsGeneration = [int64]$ActivationEndpointIdentity.generation
$ActivationServiceEndpoint3Enabled = [bool]$ActivationEndpointIdentity.endpoint_3_enabled
if (
    $ActivationEndpointIdentity.raw_sha256 -cne $ActivationServiceEndpointsSHA256 -or
    $ActivationServiceEndpointsGeneration -le 0
) {
    throw "受控激活入口配置身份不一致，拒绝构建。"
}
$ActivationHttpE2EEPath = Join-Path $Repo "activation_http_e2ee.json"
if (-not (Test-Path -LiteralPath $ActivationHttpE2EEPath -PathType Leaf)) {
    throw "缺少受控 HTTP 密文激活配置，拒绝构建。"
}
$ActivationHttpE2EEProbe = @'
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo / "src"))
from bidking_lab.secure.activation_http_e2ee import load_config

config = load_config(Path(sys.argv[2]))
print(json.dumps({
    "enabled": config.enabled,
    "key_id": config.key_id,
    "raw_sha256": config.raw_sha256,
}, sort_keys=True, separators=(",", ":")))
'@
$ActivationHttpE2EEProbeBase64 = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($ActivationHttpE2EEProbe)
)
$ActivationHttpE2EEProbeOutput = @(
    & $BuildPython -E -B -c $PythonSourceProbeLauncher `
        $ActivationHttpE2EEProbeBase64 $Repo $ActivationHttpE2EEPath 2>&1
)
if ($LASTEXITCODE -ne 0 -or $ActivationHttpE2EEProbeOutput.Count -ne 1) {
    throw "受控 HTTP 密文激活配置未通过严格解析，拒绝构建。"
}
try {
    $ActivationHttpE2EEIdentity = $ActivationHttpE2EEProbeOutput[0] | ConvertFrom-Json
} catch {
    throw "受控 HTTP 密文激活配置身份输出无效，拒绝构建。"
}
$ActivationHttpE2EEConfigSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $ActivationHttpE2EEPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$ActivationHttpE2EEEnabled = [bool]$ActivationHttpE2EEIdentity.enabled
$ActivationHttpE2EEKeyID = if ($ActivationHttpE2EEEnabled) {
    [string]$ActivationHttpE2EEIdentity.key_id
} else {
    "disabled"
}
if (
    $ActivationHttpE2EEIdentity.raw_sha256 -cne $ActivationHttpE2EEConfigSHA256 -or
    ($ActivationHttpE2EEEnabled -and -not $ActivationHttpE2EEKeyID)
) {
    throw "受控 HTTP 密文激活配置身份不一致，拒绝构建。"
}
$BuildScriptSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $PSCommandPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$ClientProtectionManifestPath = Join-Path $Repo "scripts\client_protection_manifest.json"
try {
    $ClientProtection = Get-Content -LiteralPath $ClientProtectionManifestPath -Raw -Encoding utf8 |
        ConvertFrom-Json
} catch {
    throw "客户端保护清单不可读取或格式无效，拒绝构建。"
}
if (
    $ClientProtection.schema -cne "bidking_client_protection_v2" -or
    $ClientProtection.manifest_version -is [bool] -or
    [int64]$ClientProtection.manifest_version -le 0
) {
    throw "客户端保护清单 schema 不受支持，拒绝构建。"
}
if (
    @($ClientProtection.native_abi_tags).Count -eq 0 -or
    @($ClientProtection.source_roots).Count -eq 0 -or
    -not ($ClientProtection.PSObject.Properties.Name -ccontains "build_only_source_authorities") -or
    $ClientProtection.build_only_source_authorities -is [string] -or
    $ClientProtection.build_only_source_authorities -is [bool] -or
    @($ClientProtection.build_only_source_authorities).Count -ne 3 -or
    @($ClientProtection.plaintext_allowlist).Count -eq 0 -or
    @($ClientProtection.packaged_plaintext_python_allowlist).Count -eq 0 -or
    -not $ClientProtection.critical_native -or
    @($ClientProtection.critical_native.engine_modules).Count -eq 0 -or
    @($ClientProtection.critical_native.tool_modules).Count -eq 0 -or
    @($ClientProtection.critical_native.package_modules).Count -eq 0 -or
    @($ClientProtection.critical_native.package_trees).Count -eq 0 -or
    -not $ClientProtection.critical_native.overlay.source -or
    -not $ClientProtection.critical_native.overlay.native_module -or
    $ClientProtection.cython_surface.compiler_directives.embedsignature -ne $false -or
    $ClientProtection.cython_surface.compiler_directives.emit_code_comments -ne $false -or
    @($ClientProtection.cython_surface.binding_false_groups).Count -ne 1 -or
    @($ClientProtection.cython_surface.build_only_augmentations).Count -ne 1
) {
    throw "客户端保护清单缺少必要保护边界，拒绝构建。"
}
$BuildOnlySourceModules = @()
$BuildOnlySourceAuthoritySet = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($buildOnlySourceAuthority in @($ClientProtection.build_only_source_authorities)) {
    if (
        $buildOnlySourceAuthority -isnot [string] -or
        [string]::IsNullOrEmpty($buildOnlySourceAuthority) -or
        $buildOnlySourceAuthority -cne $buildOnlySourceAuthority.Trim() -or
        $buildOnlySourceAuthority.Contains("\") -or
        $buildOnlySourceAuthority.StartsWith("/") -or
        $buildOnlySourceAuthority.Contains(":") -or
        -not $buildOnlySourceAuthority.EndsWith(
            ".py", [System.StringComparison]::Ordinal
        ) -or
        @($buildOnlySourceAuthority.Split("/") | Where-Object {
            [string]::IsNullOrEmpty($_) -or $_ -in @(".", "..")
        }).Count -ne 0 -or
        -not $BuildOnlySourceAuthoritySet.Add($buildOnlySourceAuthority)
    ) {
        throw "客户端保护清单 build-only source authority 无效，拒绝构建。"
    }
    $BuildOnlySourceModules += $buildOnlySourceAuthority.Replace("/", "\")
}
$WssOperationalContract = $ClientProtection.wss_operational_abi
if (
    -not $WssOperationalContract -or
    $WssOperationalContract.schema -cne "bidking_wss_operational_abi_v1" -or
    $WssOperationalContract.contract_version -is [bool] -or
    [int64]$WssOperationalContract.contract_version -ne 1 -or
    $WssOperationalContract.abi_version -cne "bidking_wss_addon_operational_v1" -or
    $WssOperationalContract.native_module -cne "_local_compat_addon_loader" -or
    $WssOperationalContract.entrypoint -cne "load_addons" -or
    $WssOperationalContract.source_export -ne $false -or
    $WssOperationalContract.runtime_roles.main -cne "cpython-313" -or
    $WssOperationalContract.runtime_roles.mitmdump -cne "cpython-314" -or
    @($WssOperationalContract.source_members).Count -ne 2 -or
    [string]$WssOperationalContract.bootstrap_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$WssOperationalContract.generation_sha256 -notmatch '^[0-9a-f]{64}$'
) {
    throw "WSS operational ABI 合同不完整，拒绝构建。"
}
$WssOperationalABI = [string]$WssOperationalContract.abi_version
$WssOperationalGeneration = [string]$WssOperationalContract.generation_sha256
$ClientProtectionManifestSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $ClientProtectionManifestPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$NativeBinaryContractPath = Join-Path $Repo "scripts\release_native_binary_contract.json"
$NativeHardeningTool = Join-Path $Repo "scripts\native_release_hardening.py"
try {
    $NativeBinaryContract = Get-Content -LiteralPath $NativeBinaryContractPath -Raw -Encoding utf8 |
        ConvertFrom-Json
} catch {
    throw "native binary 发布合同不可读取或格式无效，拒绝构建。"
}
if (
    $NativeBinaryContract.schema -cne "bidking_release_native_binary_contract_v2" -or
    [int64]$NativeBinaryContract.contract_version -ne 2 -or
    [int64]$NativeBinaryContract.protected_cython_module_count -ne 112 -or
    [int64]$NativeBinaryContract.additional_native_binary_count -ne 7 -or
    -not (Test-Path -LiteralPath $NativeHardeningTool -PathType Leaf)
) {
    throw "native binary 发布合同边界不受支持，拒绝构建。"
}
$NativeBinaryContractSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $NativeBinaryContractPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$ReleaseZipContractPath = Join-Path $Repo "scripts\release_zip_contract.json"
$ReleaseAvailabilityCheckerPath = Join-Path $Repo "scripts\check_hotfix3_real_drop_pool_quote_runtime.py"
$ReleaseAvailabilityFailureReceiptPublisherPath = Join-Path $Repo "scripts\publish_release_availability_failure_receipt.py"
try {
    $ReleaseZipContract = Get-Content -LiteralPath $ReleaseZipContractPath -Raw -Encoding utf8 |
        ConvertFrom-Json
} catch {
    throw "正式 ZIP 正向合同不可读取或格式无效，拒绝构建。"
}
if (
    $ReleaseZipContract.schema -cne "bidking_release_zip_contract_v1" -or
    $ReleaseZipContract.contract_version -is [bool] -or
    [int64]$ReleaseZipContract.contract_version -le 0 -or
    $ReleaseZipContract.bkenc_authentication.envelope_aad -cne
        "bidking_bkenc_member_path_by_format_v1" -or
    [int64]$ReleaseZipContract.contract_version -lt 16 -or
    $ReleaseZipContract.native_binary.contract_source -cne
        "scripts/release_native_binary_contract.json" -or
    $ReleaseZipContract.native_binary.distribution -cne "unsigned" -or
    $ReleaseZipContract.native_binary.receipt_member -cne
        "integrity/native_binary_receipt.json" -or
    $ReleaseZipContract.native_binary.receipt_schema -cne
        "bidking_native_binary_receipt_v2" -or
    -not $ReleaseZipContract.fixed_manifest_values.RuntimeDependencyInventorySHA256 -or
    -not $ReleaseZipContract.fixed_manifest_values.RuntimePythonSourceManifestSHA256 -or
    -not $ReleaseZipContract.fixed_manifest_values.RuntimeInventoryApprovalSHA256 -or
    -not $ReleaseZipContract.fixed_manifest_values.StaticDataSourceSHA256 -or
    $ReleaseZipContract.release_availability.receipt_schema -cne
        "bidking_release_availability_receipt_v1" -or
    [int64]$ReleaseZipContract.release_availability.receipt_version -ne 1 -or
    [int64]$ReleaseZipContract.release_availability.current_authority.map_id -ne 2409 -or
    $ReleaseZipContract.launcher_wrappers.target -cne "BidKingLive.exe" -or
    $ReleaseZipContract.launcher_wrappers.encoding -cne "ascii" -or
    $ReleaseZipContract.launcher_wrappers.line_endings -cne "crlf" -or
    -not (Test-Path -LiteralPath $ReleaseAvailabilityCheckerPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $ReleaseAvailabilityFailureReceiptPublisherPath -PathType Leaf)
) {
    throw "正式 ZIP 正向合同 schema/固定身份不完整，拒绝构建。"
}
$ReleaseAvailabilityFailureReceiptPublisherSHA256 = (
    & $TrustedFileHashCommand `
        -LiteralPath $ReleaseAvailabilityFailureReceiptPublisherPath `
        -Algorithm SHA256
).Hash.ToLowerInvariant()
$ExpectedReleaseCaptures = @($ReleaseZipContract.release_availability.captures)
if ($ExpectedReleaseCaptures.Count -ne 5) {
    throw "release availability capture 合同数量无效，拒绝构建。"
}
$ReleaseCaptureResolvedPaths = @()
$ReleaseCaptureReceipts = @()
$ReleaseCaptureIdentityReceiptJson = $null
$ReleaseCaptureLeaseProcess = $null
if ($Encrypt -and -not $InspectOnly) {
    $ReleaseCaptureInputs = @(
        [PSCustomObject]@{ Label = "release_capture_2536"; Value = $ReleaseCapture2536Path },
        [PSCustomObject]@{ Label = "release_capture_2408"; Value = $ReleaseCapture2408Path },
        [PSCustomObject]@{ Label = "release_capture_2409"; Value = $ReleaseCapture2409Path },
        [PSCustomObject]@{ Label = "release_capture_4501"; Value = $ReleaseCapture4501Path },
        [PSCustomObject]@{ Label = "release_capture_4538"; Value = $ReleaseCapture4538Path }
    )
    for ($captureIndex = 0; $captureIndex -lt $ReleaseCaptureInputs.Count; $captureIndex++) {
        $captureInput = $ReleaseCaptureInputs[$captureIndex]
        if (-not $captureInput.Value) {
            throw "$($captureInput.Label): capture_identity_mismatch"
        }
        try {
            $captureFullPath = ConvertTo-ControlledWindowsAbsolutePath `
                -PathValue ([string]$captureInput.Value)
        } catch {
            throw "$($captureInput.Label): capture_identity_mismatch"
        }
        $ReleaseCaptureResolvedPaths += $captureFullPath
    }
}

function ConvertTo-ReleaseCaptureProcessArgument([string]$Value) {
    if (
        [string]::IsNullOrEmpty($Value) -or
        $Value.Contains('"') -or
        $Value.EndsWith("\", [System.StringComparison]::Ordinal)
    ) {
        throw "release capture holder 参数无效。"
    }
    return '"' + $Value + '"'
}

function Start-ReleaseCaptureLeaseHolder {
    if ($ReleaseCaptureResolvedPaths.Count -ne 5) {
        throw "release capture stable identity 输入不完整。"
    }
    $arguments = @(
        "-I", "-B",
        (ConvertTo-ReleaseCaptureProcessArgument $ReleaseAvailabilityCheckerPath),
        "--hold-captures",
        "--capture-2536", (ConvertTo-ReleaseCaptureProcessArgument $ReleaseCaptureResolvedPaths[0]),
        "--capture-2408", (ConvertTo-ReleaseCaptureProcessArgument $ReleaseCaptureResolvedPaths[1]),
        "--capture-2409", (ConvertTo-ReleaseCaptureProcessArgument $ReleaseCaptureResolvedPaths[2]),
        "--capture-4501", (ConvertTo-ReleaseCaptureProcessArgument $ReleaseCaptureResolvedPaths[3]),
        "--capture-4538", (ConvertTo-ReleaseCaptureProcessArgument $ReleaseCaptureResolvedPaths[4])
    )
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $BuildPython
    $startInfo.Arguments = $arguments -join " "
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $receiptTask = $process.StandardOutput.ReadLineAsync()
    if (-not $receiptTask.Wait(15000)) {
        $process.Kill()
        throw "release capture stable identity holder 启动超时。"
    }
    $receiptLine = [string]$receiptTask.Result
    try {
        $receipt = $receiptLine | ConvertFrom-Json
    } catch {
        if (-not $process.HasExited) { $process.Kill() }
        throw "release capture stable identity holder receipt 无效。"
    }
    $captures = @($receipt.captures)
    if (
        $receipt.schema -cne "bidking_release_capture_identity_receipt_v1" -or
        $receipt.receipt_version -is [bool] -or
        [int64]$receipt.receipt_version -ne 1 -or
        $receipt.status -cne "pass" -or
        $captures.Count -ne 5 -or
        $process.HasExited
    ) {
        if (-not $process.HasExited) { $process.Kill() }
        throw "release capture stable identity holder 拒绝输入。"
    }
    $publicReceipts = @()
    for ($captureIndex = 0; $captureIndex -lt 5; $captureIndex++) {
        $capture = $captures[$captureIndex]
        $expected = $ExpectedReleaseCaptures[$captureIndex]
        $fieldNames = @($capture.PSObject.Properties.Name)
        if (
            $fieldNames.Count -ne 6 -or
            @($fieldNames | Where-Object { $_ -notin @(
                "bytes", "sha256", "volume_serial_number", "file_index",
                "number_of_links", "verdict"
            ) }).Count -ne 0 -or
            $capture.verdict -cne "pass" -or
            [int64]$capture.bytes -ne [int64]$expected.bytes -or
            [string]$capture.sha256 -cne [string]$expected.sha256 -or
            [int64]$capture.volume_serial_number -lt 0 -or
            [int64]$capture.file_index -le 0 -or
            [int64]$capture.number_of_links -ne 1
        ) {
            if (-not $process.HasExited) { $process.Kill() }
            throw "release capture stable identity receipt 不一致。"
        }
        $publicReceipts += [ordered]@{
            bytes = [int64]$capture.bytes
            sha256 = [string]$capture.sha256
            verdict = "pass"
        }
    }
    return [PSCustomObject]@{
        Process = $process
        IdentityReceiptJson = ($receipt | ConvertTo-Json -Depth 5 -Compress)
        PublicReceipts = @($publicReceipts)
    }
}

function Complete-ReleaseCaptureLeaseProcessTeardown(
    [object]$Process,
    [int]$TimeoutMilliseconds = 15000
) {
    if ($null -eq $Process) {
        throw "release capture stable identity holder 终止输入无效。"
    }
    if (-not $Process.HasExited) {
        try {
            $Process.Kill()
        } catch {
            throw "release capture stable identity holder 终止失败。"
        }
        if (
            -not $Process.WaitForExit($TimeoutMilliseconds) -or
            -not $Process.HasExited
        ) {
            throw "release capture stable identity holder 终止超时。"
        }
    }
    if (-not $Process.HasExited) {
        throw "release capture stable identity holder 终止状态无效。"
    }
    $Process.Dispose()
}

function Stop-ReleaseCaptureLeaseHolder {
    if ($null -eq $script:ReleaseCaptureLeaseProcess) { return }
    $process = $script:ReleaseCaptureLeaseProcess
    $script:ReleaseCaptureLeaseProcess = $null
    try {
        if ($process.HasExited) {
            throw "release capture stable identity holder 提前退出。"
        }
        $process.StandardInput.WriteLine("release")
        $process.StandardInput.Flush()
        $process.StandardInput.Close()
        $ackTask = $process.StandardOutput.ReadLineAsync()
        if (-not $ackTask.Wait(15000) -or -not $process.WaitForExit(15000)) {
            throw "release capture stable identity holder 释放失败。"
        }
        $ackLine = [string]$ackTask.Result
        $expectedAck = '{"schema":"bidking_release_capture_identity_receipt_v1","status":"released"}'
        if ($process.ExitCode -ne 0 -or $ackLine -cne $expectedAck) {
            throw "release capture stable identity holder 最终复核失败。"
        }
    } finally {
        try {
            Complete-ReleaseCaptureLeaseProcessTeardown -Process $process
        } catch {
            try {
                if (-not $process.HasExited) {
                    $script:ReleaseCaptureLeaseProcess = $process
                }
            } catch {
                $script:ReleaseCaptureLeaseProcess = $process
            }
            throw
        }
    }
}
if (
    [string]$ReleaseZipContract.fixed_manifest_values.WssOperationalABI -cne
        $WssOperationalABI -or
    [string]$ReleaseZipContract.fixed_manifest_values.WssOperationalGeneration -cne
        $WssOperationalGeneration -or
    [string]$ReleaseZipContract.fixed_manifest_values.WssAddonBootstrapSHA256 -cne
        [string]$WssOperationalContract.bootstrap_sha256
) {
    throw "WSS operational ABI 与 release contract 不一致，拒绝构建。"
}
$ReleaseZipContractSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $ReleaseZipContractPath -Algorithm SHA256
).Hash.ToLowerInvariant()
if (
    [string]$ReleaseZipContract.fixed_manifest_values.RuntimePythonSourceManifestSHA256 -cne
        $RuntimePythonSourceManifestSHA256
) {
    throw "runtime Python source manifest 身份与 release contract 不一致，拒绝构建。"
}
$ReleaseRuntimeDependencies = @($ReleaseZipContract.runtime_dependencies.PSObject.Properties)
$SourceRuntimeDependencies = @($RuntimePythonSource.runtime_dependencies.PSObject.Properties)
if ($ReleaseRuntimeDependencies.Count -ne $SourceRuntimeDependencies.Count) {
    throw "runtime dependency source 与 release contract 数量不一致，拒绝构建。"
}
foreach ($property in $SourceRuntimeDependencies) {
    $releaseProperty = $ReleaseZipContract.runtime_dependencies.PSObject.Properties[
        [string]$property.Name
    ]
    if ($null -eq $releaseProperty -or [string]$releaseProperty.Value -cne [string]$property.Value) {
        throw "runtime dependency source 与 release contract 不一致: $($property.Name)"
    }
}
$RuntimeDependencyInventorySHA256 = [string](
    $ReleaseZipContract.fixed_manifest_values.RuntimeDependencyInventorySHA256
)
$RuntimeInventoryApprovalPath = Join-Path $Repo "scripts\runtime_python_inventory_manifest.json"
try {
    $RuntimeInventoryApproval = Get-Content -LiteralPath $RuntimeInventoryApprovalPath `
        -Raw -Encoding utf8 | ConvertFrom-Json
    $RuntimeInventoryApprovalCanonical = [ordered]@{
        approval_version = $RuntimeInventoryApproval.approval_version
        directory_count = $RuntimeInventoryApproval.directory_count
        file_count = $RuntimeInventoryApproval.file_count
        inventory_sha256 = $RuntimeInventoryApproval.inventory_sha256
        prefix = $RuntimeInventoryApproval.prefix
        schema = $RuntimeInventoryApproval.schema
        total_bytes = $RuntimeInventoryApproval.total_bytes
    } | ConvertTo-Json -Compress
    $runtimeApprovalHasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $RuntimeInventoryApprovalSHA256 = [System.BitConverter]::ToString(
            $runtimeApprovalHasher.ComputeHash(
                [System.Text.Encoding]::UTF8.GetBytes(
                    $RuntimeInventoryApprovalCanonical
                )
            )
        ).Replace("-", "").ToLowerInvariant()
    } finally {
        $runtimeApprovalHasher.Dispose()
    }
} catch {
    throw "runtime/python 源码受审库存不可读取或格式无效，拒绝构建。"
}
$ExpectedRuntimeInventoryApprovalSHA256 = [string](
    $ReleaseZipContract.fixed_manifest_values.RuntimeInventoryApprovalSHA256
)
if ($RuntimeInventoryApprovalSHA256 -cne $ExpectedRuntimeInventoryApprovalSHA256) {
    throw "runtime/python 源码受审库存身份与 release contract 不一致，拒绝构建。"
}
$ExpectedStaticDataSourceSHA256 = [string](
    $ReleaseZipContract.fixed_manifest_values.StaticDataSourceSHA256
)
$StaticDataSourceRelativePath = "external_references\AuctionAnalyzer4.13.3\_decompiled\MapBidCalculator\MapBidCalculator\Models\StaticData.cs"
$StaticDataSourcePath = Join-Path $Repo $StaticDataSourceRelativePath
if (-not (Test-Path -LiteralPath $StaticDataSourcePath -PathType Leaf)) {
    throw "缺引擎静态先验输入，拒绝构建。"
}
$StaticDataSourceSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $StaticDataSourcePath -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($StaticDataSourceSHA256 -cne $ExpectedStaticDataSourceSHA256) {
    throw "StaticData.cs 与受审 release ZIP 合同身份不一致，拒绝构建。"
}
$LauncherSHA256 = "Pending"

$Dist = Join-Path $Repo "dist"
$Cache = Join-Path $Dist ".cache"
$NativeDiagnosticSmokeRunner = Join-Path $Repo "scripts\run_native_diagnostic_smoke.py"
$ProductProfileSuffix = if ($IsNetworkDiagnosticCohort) { "-network-diagnostic" } else { "" }
$BaseName = if ($Encrypt) {
    "bidking-live-v$Version-encrypted$ProductProfileSuffix"
} else {
    "bidking-live-v$Version-portable$ProductProfileSuffix"
}
$Name = if ($SplitCompatibilityRuntime) { "$BaseName-online" } else { $BaseName }
$Stage = Join-Path $Dist $Name
$Zip = Join-Path $Dist "$Name.zip"
$RuntimePy = Join-Path $Stage "runtime\python"
$RuntimeInventoryReceiptPath = "$Zip.runtime-receipt.json"
$RuntimeInventoryReceiptSHA256 = "Pending"
$BkencAuthenticationReceiptPath = "$Zip.bkenc-auth-receipt.json"
$BkencAuthenticationReceiptSHA256 = "Pending"
$BuildAuditReceiptPath = "$Zip.build-audit-receipt.json"
$NativeBinaryAuditReceiptPath = "$Zip.native-binary-receipt.json"
$NativeBinaryReceiptPath = Join-Path $Stage "integrity\native_binary_receipt.json"
$NativeBinaryDistribution = if ($Encrypt) { "unsigned" } else { "NotRequired" }
$NativeBinaryReceiptSHA256 = if ($Encrypt) { "Pending" } else { "NotRequired" }
$NativeBinaryVerified = $false
$NativeBinaryCount = 0
$RuntimeComponentArchive = $null
$RuntimeComponentArchiveName = "None"
$RuntimeComponentArchiveBytes = 0
$RuntimeComponentArchiveSHA256 = "None"
$RuntimeComponentDescriptorSHA256 = "None"
$RuntimeComponentMode = if ($SplitCompatibilityRuntime) { "component" } else { "bundled" }

# 出厂主密钥 K: -Encrypt + -MasterKeyFile 时【早设 env】, 贯穿整个 -Encrypt 流程共用:
#   ① Rust 内核编译期内嵌 K(build.rs 读此 env) ② 核心 moat 文件加密(5c) ③ 上传配置加密(step4)。
# 三者用同一把 K = 运行时内核内嵌的 K = 服务端下发的 master_key。末尾恢复(env 进程局部, 不外泄)。
$origGlobalDataKey = $env:BIDKING_DATA_KEY
$ValidatedReleaseDataKey = $null
$ReleaseKeyCacheVerified = $false
$UploadConfigRoundTripVerified = $false
$GeneratedUploadConfigSHA256 = $null
$origIntegrityPublicKey = $env:BIDKING_INTEGRITY_PUBLIC_KEY_B64
$LocalIntegrityPublicKeyB64 = $null
$LocalIntegrityPublicKeySHA256 = if ($Encrypt) { "Pending" } else { "NotRequired" }
$LocalIntegrityManifestSHA256 = if ($Encrypt) { "Pending" } else { "NotRequired" }
$LocalIntegrityEntryCount = 0
$LocalIntegrityVerified = $false

# 🔴 已退役主密钥的【指纹黑名单】(0.2.7 起必须用 master_key_v2)。
# 为什么需要它: -MasterKeyFile 指哪个文件就读哪个, 脚本【分不出新旧】。桌面部署夹里两个文件
# 只差一个 "_v2", 手一滑就打成旧 key —— 而后果是【服务端给 0.2.7 客户端发新 key、包却是旧 key 加密的
# ⇒ 用户解不开数据 ⇒ 全员失活】, 而且要等群友装了才会发现。这是整个发布里最贵的一次手滑。
# 存的是【已退役 key 的 sha256 前缀】: 不可逆, 且它只作黑名单用 —— 新 key 的内容和哈希都【绝不进仓库】。
$RetiredKeyFingerprints = @{
    "f634c1df20164549" = "0.2.6 及之前的旧主密钥(master_key.txt)"
}
function Get-KeyFingerprint([string]$key) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($key))
    return -join ($bytes | ForEach-Object { $_.ToString("x2") }) | ForEach-Object { $_.Substring(0, 16) }
}

if ($Encrypt -and -not $InspectOnly -and -not $MasterKeyFile) {
    throw "-Encrypt 公版候选必须显式传 -MasterKeyFile；拒绝使用不可溯源的预设 env:BIDKING_DATA_KEY。"
}
if ($Encrypt -and -not $InspectOnly -and $MasterKeyFile) {
    if (Test-Path -LiteralPath $MasterKeyFile -PathType Leaf) {
        $ValidatedReleaseDataKey = (Get-Content -LiteralPath $MasterKeyFile -Raw) -replace '\s', ''
        if ($ValidatedReleaseDataKey) {
            $fp = Get-KeyFingerprint $ValidatedReleaseDataKey
            if ($RetiredKeyFingerprints.ContainsKey($fp)) {
                $ValidatedReleaseDataKey = $null
                throw @"
🔴 拒绝出包: -MasterKeyFile 传的是【已退役的主密钥】!
   0.2.7 起服务端只给 >=0.2.7 的客户端下发【新 key】。用旧 key 打出来的包
   【解不开服务端下发的数据】=> 装了就是失活, 而且要等群友装完才会发现。
   请重新选择当前受控发布密钥文件。
"@
            }
            Write-Host "  出厂主密钥已完成只读格式检查；构建阶段尚未写入进程环境" -ForegroundColor Green
        }
    } else {
        throw "-MasterKeyFile 不是可读普通文件；-Encrypt 拒绝继续。"
    }
}
# 安全红线(审查#6): -Encrypt 必须最终持有非空主密钥(来自 -MasterKeyFile 或预设 env:BIDKING_DATA_KEY)。
# 否则 moat 加密(5c)被跳过 + 明文 ingest key(sample_upload.local.ps1)拷进【自称 -encrypted】的包
# = 明文密钥泄露 + 作者误以为已加密。line ~200 的 fail-loud 只盖"已设 key 但加密失败", 盖不住"从没设 key"。
if ($Encrypt -and -not $InspectOnly -and -not $ValidatedReleaseDataKey) {
    throw "-Encrypt 但主密钥未设: 未传 -MasterKeyFile(或其内容为空)且 env:BIDKING_DATA_KEY 也未设。拒绝出包 — 否则会产出明文 moat + 明文 ingest key 却命名 -encrypted。请传 -MasterKeyFile <master_key.txt> 再打。"
}
$LocalIntegrityBuilder = Join-Path $Repo "scripts\build_local_integrity_manifest.py"
$LocalIntegrityContractPath = Join-Path $Repo "scripts\local_integrity_contract.json"
if (-not (Test-Path -LiteralPath $LocalIntegrityBuilder -PathType Leaf) -or
    -not (Test-Path -LiteralPath $LocalIntegrityContractPath -PathType Leaf)) {
    throw "缺本地完整性签名器或合同，拒绝构建。"
}
if ($Encrypt -and -not $InspectOnly -and -not $IntegritySigningKeyFile) {
    throw "-Encrypt 公版候选必须显式传 -IntegritySigningKeyFile；不会生成、查找或回退离线 Ed25519 私钥。"
}
if ($Encrypt -and -not $InspectOnly) {
    if (-not (Test-Path -LiteralPath $IntegritySigningKeyFile -PathType Leaf)) {
        throw "-IntegritySigningKeyFile 不是可读普通文件；-Encrypt 拒绝继续。"
    }
    $previousIntegrityEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $integrityPublicKeyOut = (
            & $BuildPython -I -B $LocalIntegrityBuilder public-key `
                --signing-key $IntegritySigningKeyFile 2>&1 |
                Out-String
        )
        $integrityPublicKeyExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousIntegrityEap
    }
    if ($integrityPublicKeyExit -ne 0) {
        throw "离线 Ed25519 签名输入无效，拒绝构建: $integrityPublicKeyOut"
    }
    try {
        $integrityPublicKeyReceipt = $integrityPublicKeyOut | ConvertFrom-Json
        $LocalIntegrityPublicKeyB64 = [string]$integrityPublicKeyReceipt.public_key_b64
        $LocalIntegrityPublicKeySHA256 = [string]$integrityPublicKeyReceipt.public_key_sha256
    } catch {
        throw "离线 Ed25519 公钥回执无效，拒绝构建。"
    }
    if ($LocalIntegrityPublicKeyB64 -cnotmatch '^[A-Za-z0-9+/]{43}=$' -or
        $LocalIntegrityPublicKeySHA256 -cnotmatch '^[0-9a-f]{64}$') {
        throw "离线 Ed25519 公钥身份无效，拒绝构建。"
    }
    Write-Host "  本地完整性 Ed25519 公钥身份已冻结（私钥不进入进程环境或 Stage）" -ForegroundColor Green
}
$uploadLocal = Join-Path $Repo "scripts\sample_upload.local.ps1"
if ($Encrypt -and -not (Test-Path $uploadLocal)) {
    throw "-Encrypt 群发包缺 scripts/sample_upload.local.ps1 构建输入，无法生成 upload_config.enc。公版默认上传，拒绝产出 EncryptedUploadConfig=False 的伪完成包。"
}

Write-Host "==> BidKing LIVE portable packager v$Version" -ForegroundColor Cyan

# --- 0. 前置检查 ---
if (-not (Test-Path $BuildPython)) { throw "源 Python 不存在: $BuildPython" }
if ($Encrypt -and -not $InspectOnly) {
    foreach ($ambientNativeOverride in @(
            "CL", "_CL_", "LINK", "_LINK_", "RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS",
            "CARGO_ENCODED_RUSTFLAGS"
        )) {
        if ($null -ne [Environment]::GetEnvironmentVariable($ambientNativeOverride)) {
            throw "检测到未受控 native 编译环境覆盖 $ambientNativeOverride，拒绝构建。"
        }
    }
    if ($LocalIntegrityPublicKeyB64 -cnotmatch '^[A-Za-z0-9+/]{43}=$' -or
        $LocalIntegrityPublicKeySHA256 -cnotmatch '^[0-9a-f]{64}$') {
        throw "native trust bootstrap 未复用完整性 A 派生的 Ed25519 公钥身份，拒绝构建。"
    }
}

# 第三方 WSS runtime 与 Pet 素材都属于固定 package input。其 payload 可以是
# gitignored 本地目录，但 provenance/inventory manifest 与 verifier 必须由 Git 管理；
# 构建不得自动 snapshot 或接受开发机全局安装作为替代。
$releaseAssetVerifier = Join-Path $Repo "scripts\verify_release_asset_inventory.py"
$mitmproxyManifestPath = Join-Path $Repo "scripts\mitmproxy_runtime_manifest.json"
$petAssetsManifestPath = Join-Path $Repo "scripts\pet_companion_assets_manifest.json"
$wssAddonBootstrapBuilder = Join-Path $Repo "scripts\build_wss_addon_bootstrap.py"
$runtimeComponentBuilder = Join-Path $Repo "scripts\build_release_runtime_component.py"
$runtimeComponentSource = Join-Path $Repo "scripts\runtime_component.py"
$runtimeComponentBootstrap = Join-Path $Repo "scripts\local_compat_component.py"
$mitmproxyAsset = Join-Path $Repo "scripts\pkg_assets\mitmproxy"
$petAssetsSource = Join-Path $Repo "hero_ref\assets\pet_companion"
foreach ($requiredReleaseAssetInput in @(
        $releaseAssetVerifier,
        $mitmproxyManifestPath,
        $petAssetsManifestPath,
        $wssAddonBootstrapBuilder,
        $runtimeComponentBuilder,
        $runtimeComponentSource,
        $runtimeComponentBootstrap,
        $mitmproxyAsset,
        $petAssetsSource
    )) {
    if (-not (Test-Path -LiteralPath $requiredReleaseAssetInput)) {
        throw "缺正式 release asset 输入: $requiredReleaseAssetInput"
    }
}
function Invoke-ReleaseAssetGate([string]$root, [string]$manifest, [string]$label) {
    $previousEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $result = (& $BuildPython -B $releaseAssetVerifier --root $root --manifest $manifest --json 2>&1 | Out-String)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEap
    }
    if ($exitCode -ne 0) {
        throw "$label inventory 门失败，拒绝构建: $result"
    }
    try {
        $parsed = $result | ConvertFrom-Json
    } catch {
        throw "$label inventory 门未返回有效 JSON，拒绝构建。"
    }
    Write-Host "  + $label inventory: $($parsed.file_count) files / $($parsed.total_bytes) bytes" -ForegroundColor Green
    return $parsed
}
$mitmproxyIdentity = Invoke-ReleaseAssetGate $mitmproxyAsset $mitmproxyManifestPath "mitmproxy runtime"
$petAssetsIdentity = Invoke-ReleaseAssetGate $petAssetsSource $petAssetsManifestPath "Pet assets"
try {
    $mitmproxyManifest = Get-Content -LiteralPath $mitmproxyManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
} catch {
    throw "无法读取 mitmproxy runtime provenance manifest。"
}
$MitmproxyVersion = [string]$mitmproxyManifest.version
$MitmproxySourceURL = [string]$mitmproxyManifest.source.url
$MitmproxyInstallerSHA256 = [string]$mitmproxyManifest.source.installer_sha256
$MitmproxyInventorySHA256 = [string]$mitmproxyIdentity.inventory_sha256
$PetAssetsInventorySHA256 = [string]$petAssetsIdentity.inventory_sha256

# gitignored 数据不会出现在 PackageInputsDirty 中。必须在清理 Stage、复制 data 或生成任何
# 加密产物之前，对人工确认过的发布基线执行只读 --verify；缺基线、内容变化或源码树混入
# BKENC1/BKENC2 密文均由检测器非零退出并中止构建。构建流程绝不自动建立/刷新基线。
$integrityChecker = Join-Path $Repo "scripts\check_package_data_integrity.py"
if (-not (Test-Path $integrityChecker)) {
    throw "缺打包数据完整性检测器: $integrityChecker"
}
$origPythonIOEncoding = $env:PYTHONIOENCODING
$prevEAP = $ErrorActionPreference
try {
    $env:PYTHONIOENCODING = "utf-8"
    $ErrorActionPreference = "Continue"
    $integrityLog = (& $BuildPython -B $integrityChecker --verify 2>&1 | Out-String)
    $integrityExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
    $env:PYTHONIOENCODING = $origPythonIOEncoding
}
if ($integrityExit -ne 0) {
    throw "打包数据完整性门失败，拒绝复制或加密 data。不得在构建中自动 --snapshot：`n$integrityLog"
}
Write-Host $integrityLog.Trim() -ForegroundColor Green
$packageDataManifestPath = Join-Path $Repo "scripts\package_data_manifest.json"
$packageDataFingerprintPath = Join-Path $Repo "scripts\package_data_fingerprints.json"
if (-not (Test-Path -LiteralPath $packageDataManifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $packageDataFingerprintPath -PathType Leaf)) {
    throw "缺 tracked 数据门输入，拒绝继续构建。"
}
$PackageDataManifestSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $packageDataManifestPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$PackageDataFingerprintSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $packageDataFingerprintPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$PackageDataFingerprintMember = "scripts/package_data_fingerprints.json"
$PackageDataFingerprintFixedProperty = (
    $ReleaseZipContract.fixed_member_sha256.PSObject.Properties[
        $PackageDataFingerprintMember
    ]
)
if (
    $null -eq $PackageDataFingerprintFixedProperty -or
    [string]$PackageDataFingerprintFixedProperty.Value -notmatch '^[0-9a-f]{64}$' -or
    [string]$PackageDataFingerprintFixedProperty.Value -cne $PackageDataFingerprintSHA256
) {
    throw "package_data_fingerprints.json 与受审 release ZIP 合同身份不一致，拒绝构建。"
}
try {
    $PackageDataFingerprintDocument = Get-Content `
        -LiteralPath $packageDataFingerprintPath `
        -Raw `
        -Encoding utf8 |
        ConvertFrom-Json
    $PackageDataBaselineID = $PackageDataFingerprintDocument.baseline_id
} catch {
    throw "无法读取 tracked 数据基线标识，拒绝继续构建。"
}
if ($PackageDataBaselineID -isnot [string] -or -not $PackageDataBaselineID.Trim()) {
    throw "tracked 数据基线缺非空 baseline_id，拒绝继续构建。"
}

# One source mapping for both cheap preflight and the later exact-copy consumers.
# Expected bytes remain independently pinned in the reviewed release ZIP contract.
function Get-VerifiedReleaseDiagnosticInputs([string]$RepositoryRoot, $Contract) {
    $inputs = [ordered]@{
        "诊断工具/收集诊断.bat" = Join-Path $RepositoryRoot "scripts\collect_diagnostics.bat"
        "诊断工具/启动修复.bat" = Join-Path $RepositoryRoot "scripts\startup_repair.bat"
        "诊断工具/激活网络诊断.bat" = Join-Path $RepositoryRoot "scripts\activation_transport_diagnostic.bat"
        "诊断工具/activation_transport_diagnostic.py" = Join-Path $RepositoryRoot "scripts\activation_transport_diagnostic.py"
    }
    foreach ($member in $inputs.Keys) {
        $pin = @($Contract.fixed_member_sha256.PSObject.Properties | Where-Object { $_.Name -ceq $member })
        if ($pin.Count -ne 1 -or $pin[0].Value -isnot [string] -or
            $pin[0].Value -cnotmatch '^[0-9a-f]{64}$' -or
            @($Contract.required_members.common) -cnotcontains $member) {
            throw "release_fixed_copy_contract_invalid: $member"
        }
        $sourcePath = $inputs[$member]
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            throw "release_fixed_copy_missing: $member"
        }
        $actualSHA256 = (
            & $TrustedFileHashCommand -LiteralPath $sourcePath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($actualSHA256 -cne $pin[0].Value) {
            throw "release_fixed_copy_hash_mismatch: $member"
        }
    }
    return $inputs
}
$ReleaseDiagnosticInputs = Get-VerifiedReleaseDiagnosticInputs -RepositoryRoot $Repo -Contract $ReleaseZipContract

# 复制集合与冻结集合必须来自同一 tracked manifest。检测器机器可读输出是 PowerShell
# 唯一的数据复制入口；不得在此另维护表名/JSON 名称，也不得退回整目录复制。
$prevEAP = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $packageDataJson = (& $BuildPython -B $integrityChecker --list-json 2>&1 | Out-String)
    $packageDataListExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
if ($packageDataListExit -ne 0) {
    throw "无法读取统一发包数据清单，拒绝继续: $packageDataJson"
}
try {
    # Windows PowerShell 5.1 keeps a JSON array as one nested Object[] when
    # ConvertFrom-Json is wrapped directly in @(...).  Split the assignment so
    # @($parsed) enumerates the actual string entries like PowerShell 7 does.
    $packageDataParsed = $packageDataJson | ConvertFrom-Json
    $packageDataFiles = @($packageDataParsed)
} catch {
    throw "统一发包数据清单不是有效 JSON，拒绝继续: $packageDataJson"
}
if ($packageDataFiles.Count -eq 0) {
    throw "统一发包数据清单为空，拒绝继续。"
}
foreach ($rel in $packageDataFiles) {
    if ($rel -isnot [string] -or -not $rel) {
        throw "统一发包数据清单包含非字符串或空路径，拒绝继续。"
    }
}
$StaticDataMoatMember = $StaticDataSourceRelativePath.Replace("\", "/")
$MutableStageMoatMembers = @(
    Get-ValidatedMutableStageMoatMembers `
        -ReleaseContract $ReleaseZipContract `
        -PackageDataFiles $packageDataFiles `
        -StaticDataMember $StaticDataMoatMember `
        -PlaintextPackageMember "data/processed/heroes.json"
)
$MutableStageExpectedIdentities = [System.Collections.Generic.Dictionary[string, object]]::new(
    [System.StringComparer]::Ordinal
)
if ($null -eq $PackageDataFingerprintDocument.files -or
    $PackageDataFingerprintDocument.files.GetType() -ne
        [System.Management.Automation.PSCustomObject]) {
    throw "[build] stage_mutable_copy_expected_identity_invalid"
}
foreach ($mutableMember in $MutableStageMoatMembers) {
    if ($mutableMember -ceq $StaticDataMoatMember) {
        continue
    }
    $identityProperty = $PackageDataFingerprintDocument.files.PSObject.Properties[
        $mutableMember
    ]
    if ($null -eq $identityProperty -or $null -eq $identityProperty.Value -or
        $identityProperty.Value.GetType() -ne
            [System.Management.Automation.PSCustomObject]) {
        throw "[build] stage_mutable_copy_expected_identity_invalid"
    }
    $identityProperties = @($identityProperty.Value.PSObject.Properties)
    $identityNames = @($identityProperties | ForEach-Object { [string]$_.Name })
    $identitySizeType = if ($null -eq $identityProperty.Value.size) {
        $null
    } else {
        $identityProperty.Value.size.GetType()
    }
    if ($identityProperties.Count -ne 2 -or
        -not ($identityNames -ccontains "sha256") -or
        -not ($identityNames -ccontains "size") -or
        $identityProperty.Value.sha256 -isnot [string] -or
        [string]$identityProperty.Value.sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
        ($identitySizeType -ne [int32] -and $identitySizeType -ne [int64]) -or
        [int64]$identityProperty.Value.size -lt 0) {
        throw "[build] stage_mutable_copy_expected_identity_invalid"
    }
    $MutableStageExpectedIdentities.Add(
        $mutableMember,
        [pscustomobject]@{
            Bytes = [int64]$identityProperty.Value.size
            SHA256 = [string]$identityProperty.Value.sha256
        }
    )
}
if ($MutableStageExpectedIdentities.Count -ne 20) {
    throw "[build] stage_mutable_copy_expected_identity_invalid"
}

# 事故门(0.2.7): 只验证“能加密”无法发现拿错仍有效的主密钥。公版出包前必须证明所选文件
# 等于本机近期从激活服务取得的设备绑定缓存；全程不打印密钥或活动密钥指纹，也不发网络请求。
if ($Encrypt -and -not $InspectOnly) {
    $keyVerifier = Join-Path $Repo "scripts\verify_release_master_key.py"
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $verifyLog = (& $BuildPython -X utf8 -E -B $keyVerifier --key-file $MasterKeyFile --max-cache-age-hours 6 2>&1 | Out-String)
    $verifyExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($verifyExit -ne 0) {
        throw "候选主密钥未通过近期激活缓存核对，拒绝出包: $verifyLog"
    }
    $ReleaseKeyCacheVerified = $true
    Write-Host $verifyLog.Trim() -ForegroundColor Green
}

function Write-BuildPreflightReceipt([string]$Mode, [bool]$ReadyForBuild, [string]$KeyCheck) {
    $receipt = [ordered]@{
        schema = "bidking_build_preflight_receipt_v1"
        receipt_version = 1
        mode = $Mode
        ready_for_build = $ReadyForBuild
        package_version = "v$Version"
        source_commit = $SourceCommit
        source_branch = $SourceBranch
        encrypted = [bool]$Encrypt
        split_compatibility_runtime = [bool]$SplitCompatibilityRuntime
        product_profile = $ProductProfile
        package_inputs_dirty = [bool]$PackageInputsDirty
        package_input_status_count = [int]$PackageInputStatus.Count
        package_data_baseline_id = [string]$PackageDataBaselineID
        package_data_manifest_sha256 = [string]$PackageDataManifestSHA256
        package_data_fingerprint_sha256 = [string]$PackageDataFingerprintSHA256
        release_contract_sha256 = [string]$ReleaseZipContractSHA256
        runtime_python_source_manifest_sha256 = [string]$RuntimePythonSourceManifestSHA256
        runtime_approval_sha256 = [string]$RuntimeInventoryApprovalSHA256
        native_binary_contract_sha256 = [string]$NativeBinaryContractSHA256
        native_binary_distribution = [string]$NativeBinaryDistribution
        static_data_source_sha256 = [string]$StaticDataSourceSHA256
        activation_endpoints_sha256 = [string]$ActivationServiceEndpointsSHA256
        activation_endpoints_generation = [int64]$ActivationServiceEndpointsGeneration
        activation_http_e2ee_sha256 = [string]$ActivationHttpE2EEConfigSHA256
        mitmproxy_inventory_sha256 = [string]$MitmproxyInventorySHA256
        pet_assets_inventory_sha256 = [string]$PetAssetsInventorySHA256
        release_availability = if ($Encrypt) {
            [ordered]@{
                schema = "bidking_release_availability_preflight_v1"
                captures = @($ReleaseCaptureReceipts)
                verdict = if ($ReleaseCaptureReceipts.Count -eq 5) { "pass" } else { "fail" }
            }
        } else { $null }
        release_key_check = $KeyCheck
        local_integrity_key_check = if ($Encrypt) {
            if ($LocalIntegrityPublicKeyB64) { "verified" } else { "not_checked" }
        } else { "not_required" }
        local_integrity_public_key_sha256 = [string]$LocalIntegrityPublicKeySHA256
        artifact_name = [System.IO.Path]::GetFileName($Zip)
        stage_exists_before = [bool](Test-Path -LiteralPath $Stage)
        zip_exists_before = [bool](Test-Path -LiteralPath $Zip)
    }
    Write-Output (
        "BUILD_PREFLIGHT_RECEIPT=" +
        ($receipt | ConvertTo-Json -Depth 5 -Compress)
    )
}

if ($InspectOnly) {
    Write-BuildPreflightReceipt "inspect_only" $false "not_run"
    $ValidatedReleaseDataKey = $null
    return
}
if ($Encrypt) {
    $releaseCaptureLease = Start-ReleaseCaptureLeaseHolder
    $ReleaseCaptureLeaseProcess = $releaseCaptureLease.Process
    $ReleaseCaptureIdentityReceiptJson = [string](
        $releaseCaptureLease.IdentityReceiptJson
    )
    $ReleaseCaptureReceipts = @($releaseCaptureLease.PublicReceipts)
}
try {
    if ($PreflightOnly) {
        $PreflightKeyCheck = if ($Encrypt) { "verified" } else { "not_required" }
        Stop-ReleaseCaptureLeaseHolder
        Write-BuildPreflightReceipt "preflight_only" $true $PreflightKeyCheck
        $ValidatedReleaseDataKey = $null
        return
    }
$zipAuditor = Join-Path $Repo "scripts\audit_release_zip.py"
if (-not (Test-Path -LiteralPath $zipAuditor -PathType Leaf)) {
    throw "缺成品 ZIP 审计器，拒绝构建。"
}
$ControlledRustPathMappings = @()
$ControlledRustRemapFlags = @()
$NativePrivacyForbiddenPaths = @()
if ($Encrypt) {
    $ControlledRustPathMappings = @(
        Get-ControlledRustPathMappings -RepositoryRoot $Repo
    )
    $ControlledRustRemapFlags = @(
        $ControlledRustPathMappings |
            ForEach-Object { "--remap-path-prefix=$($_.Source)=$($_.PublicPrefix)" }
    )
    $NativePrivacyForbiddenPaths = @(
        $ControlledRustPathMappings | ForEach-Object { [string]$_.Source }
    )
}
if ($Encrypt) {
    $env:BIDKING_DATA_KEY = $ValidatedReleaseDataKey
    $env:BIDKING_INTEGRITY_PUBLIC_KEY_B64 = $LocalIntegrityPublicKeyB64
}

# --- 1. 清理 + 目录 ---
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
if (Test-Path $RuntimeInventoryReceiptPath) {
    Remove-Item -LiteralPath $RuntimeInventoryReceiptPath -Force
}
if (Test-Path $BkencAuthenticationReceiptPath) {
    Remove-Item -LiteralPath $BkencAuthenticationReceiptPath -Force
}
if (Test-Path $BuildAuditReceiptPath) {
    Remove-Item -LiteralPath $BuildAuditReceiptPath -Force
}
if (Test-Path $NativeBinaryAuditReceiptPath) {
    Remove-Item -LiteralPath $NativeBinaryAuditReceiptPath -Force
}
New-Item -ItemType Directory -Force -Path $Stage, $Cache, $RuntimePy | Out-Null

# --- 2. 内嵌 Python(下载并缓存) ---
$embedZip = Join-Path $Cache ([string]$RuntimePythonSource.python.embed.filename)
if (-not (Test-Path $embedZip)) {
    $url = [string]$RuntimePythonSource.python.embed.url
    Write-Host "  下载内嵌 Python: $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $embedZip
}
$embedIdentity = Get-Item -LiteralPath $embedZip
$embedSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $embedZip -Algorithm SHA256
).Hash.ToLowerInvariant()
if (
    [int64]$embedIdentity.Length -ne [int64]$RuntimePythonSource.python.embed.bytes -or
    $embedSHA256 -cne [string]$RuntimePythonSource.python.embed.sha256
) {
    throw "内嵌 Python archive 与冻结 runtime source 不一致，拒绝构建。"
}
Expand-Archive -Path $embedZip -DestinationPath $RuntimePy -Force
$embeddedPython = Join-Path $RuntimePy "python.exe"
$embeddedPythonSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $embeddedPython -Algorithm SHA256
).Hash.ToLowerInvariant()
if (
    $embeddedPythonSHA256 -cne
        [string]$RuntimePythonSource.python.embed.python_exe_sha256
) {
    throw "内嵌 Python executable 与冻结 runtime source 不一致，拒绝构建。"
}
# 配置 ._pth: 显式列出受控路径，但绝不执行 import site。包内依赖仍从固定
# Lib/site-packages 解析；sitecustomize/usercustomize/.pth 等启动前可写钩子保持关闭。
$pthFile = Get-ChildItem $RuntimePy -Filter "python*._pth" | Select-Object -First 1
@(
    "python313.zip",
    ".",
    "Lib\site-packages",
    "..\..\hero_ref\src",
    "..\..\hero_ref\tools",
    "..\..\src",
    "..\..\scripts"
) | Set-Content -Path $pthFile.FullName -Encoding ascii
if (Select-String -LiteralPath $pthFile.FullName -Pattern '^\s*import\s+site\s*$' -Quiet) {
    throw "内嵌 Python ._pth 重新启用了 import site，拒绝构建。"
}
Write-Host "  + 内嵌 Python + ._pth"

# --- 3. site-packages: 从源 Python 拷运行时依赖(+ 它们的依赖) ---
# 依赖可能装在系统 site 或用户级 site(pip install --user)。必须搜索全部 site 目录,
# 只看 getsitepackages()[0] 会静默拷空 -> 打包版缺 numpy -> 玩家干净机器崩
# ModuleNotFoundError(2026-06-20 群友实测根因)。末尾有隔离导入验证兜底。
$siteDirsJson = & $BuildPython -c "import site,json; d=list(site.getsitepackages()); d.append(site.getusersitepackages()); print(json.dumps(d))"
$siteDirs = $siteDirsJson | ConvertFrom-Json
$dstSite = Join-Path $RuntimePy "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $dstSite | Out-Null
# cryptography/cffi/pycparser = 激活+数据加密(secure/data_crypt 的 AES-GCM)运行时依赖。0.2.2 起激活
# 【真强制】, 缺 cryptography → client_activate 导入失败 → 激活恒降级 → 产品锁死, 故【必须打进】。
# _cffi_backend 是【顶层 .pyd】(非包目录), 需下面 "$pk*.pyd" 分支才拷得到。
$pkgs = @("psutil", "pydantic", "pydantic_core", "numpy", "certifi", "PIL", "pillow", "typing_extensions", "annotated_types", "typing_inspection",
          "cryptography", "cffi", "_cffi_backend", "pycparser")  # _cffi_backend = 顶层 .pyd, cryptography 的 rust openssl 绑定运行时要它
foreach ($pk in $pkgs) {
    foreach ($siteDir in $siteDirs) {
        if (-not (Test-Path $siteDir)) { continue }
        foreach ($cand in @($pk, "$pk.py")) {
            $src = Join-Path $siteDir $cand
            if (Test-Path $src) {
                if ((Get-Item $src).PSIsContainer) {
                    robocopy $src (Join-Path $dstSite $cand) /E /XD __pycache__ /NFL /NDL /NJH /NJS /NC /NS | Out-Null
                } else { Copy-Item $src (Join-Path $dstSite $cand) -Force }
            }
        }
        # 顶层扩展模块 .pyd(如 _cffi_backend.cp313-win_amd64.pyd): 不是包目录, 上面 dir/py 分支拷不到。
        Get-ChildItem $siteDir -Filter "$pk*.pyd" -File -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $dstSite $_.Name) -Force
        }
        # 同级 .libs 目录(numpy.libs 等捆绑 DLL, 缺了 numpy import 会失败)
        $libsDir = Join-Path $siteDir "$pk.libs"
        if (Test-Path $libsDir) { robocopy $libsDir (Join-Path $dstSite "$pk.libs") /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null }
        # dist-info(版本元数据，pydantic 等运行需要)
        Get-ChildItem $siteDir -Filter "$pk-*.dist-info" -ErrorAction SilentlyContinue | ForEach-Object {
            robocopy $_.FullName (Join-Path $dstSite $_.Name) /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
        }
    }
}
$bundledCaFile = Join-Path $dstSite "certifi\cacert.pem"
if (-not (Test-Path $bundledCaFile) -or (Get-Item $bundledCaFile).Length -lt 10000) {
    throw "群发包缺少有效 certifi CA bundle，HTTPS 激活/上传/版本检查会依赖玩家机器证书环境，拒绝出包。"
}
$BundledCABundle = $true
Write-Host "  + site-packages (psutil/pydantic/numpy/Pillow/certifi CA)"

# --- 3b. tkinter + Tcl/Tk(embed 默认不含; overlay 是 Tkinter 需要) ---
$devRoot = Split-Path $BuildPython
foreach ($d in @("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll")) {
    $src = Join-Path $devRoot "DLLs\$d"
    if (Test-Path $src) { Copy-Item $src (Join-Path $RuntimePy $d) -Force }
}
robocopy (Join-Path $devRoot "Lib/tkinter") (Join-Path $dstSite "tkinter") /E /XD __pycache__ /NFL /NDL /NJH /NJS /NC /NS | Out-Null
$tclDst = Join-Path $RuntimePy "tcl"
New-Item -ItemType Directory -Force -Path $tclDst | Out-Null
foreach ($t in @("tcl8.6", "tk8.6", "tcl8")) {
    $src = Join-Path $devRoot "tcl/$t"
    if (Test-Path $src) { robocopy $src (Join-Path $tclDst $t) /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null }
}
Write-Host "  + tkinter + Tcl/Tk（路径由最小 launcher 显式注入，不使用 sitecustomize）"
if (Get-ChildItem -LiteralPath $RuntimePy -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("sitecustomize.py", "usercustomize.py") } |
    Select-Object -First 1) {
    throw "内嵌 Python runtime 含自动启动 customizer，拒绝构建。"
}

# --- 4. 源码(镜像仓库结构，live 流程需要的部分) ---
function Copy-Tree($rel, [string[]]$xf = @(), [string[]]$xdExtra = @()) {
    $src = Join-Path $Repo $rel; $dst = Join-Path $Stage $rel
    if (-not (Test-Path $src)) { return }
    $xd = @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache") + $xdExtra
    robocopy $src $dst /E /XD $xd /XF $xf /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy 失败: $rel" }
}
Copy-Tree "hero_ref\src"
Copy-Tree "hero_ref\tools"
# hero_ref/assets 同时存放运行时图标/付款码和历史 closeout 截图。公版只复制运行时
# 实际读取的资产；购买资料在 step 6 从源码资产单独复制到“购买与续费”。禁止整目录
# 复制，避免把带英雄、场次和复核画面的历史截图当作用户样本带入群发包。
$heroAssetsDst = Join-Path $Stage "hero_ref\assets"
New-Item -ItemType Directory -Force -Path $heroAssetsDst | Out-Null
$runtimeHeroAssets = @("bidking_cat.ico")
if (-not $IsNetworkDiagnosticCohort) {
    $runtimeHeroAssets += "support_qr.png", "shop_qr.png"
}
foreach ($asset in $runtimeHeroAssets) {
    $assetSrc = Join-Path $Repo "hero_ref\assets\$asset"
    if (-not (Test-Path -LiteralPath $assetSrc -PathType Leaf)) {
        throw "缺少运行时 Hero Ref 资产: hero_ref/assets/$asset"
    }
    Copy-Item -LiteralPath $assetSrc -Destination (Join-Path $heroAssetsDst $asset) -Force
}
$petAssetsDst = Join-Path $heroAssetsDst "pet_companion"
robocopy $petAssetsSource $petAssetsDst /E /XD __pycache__ /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "复制 Pet 运行时素材失败。"
}
$stagedPetIdentity = Invoke-ReleaseAssetGate $petAssetsDst $petAssetsManifestPath "staged Pet assets"
if ($stagedPetIdentity.inventory_sha256 -cne $PetAssetsInventorySHA256) {
    throw "staged Pet 素材与已验证源码 inventory 不一致，拒绝构建。"
}
Copy-Tree "src\bidking_lab"
foreach ($buildOnlySourceModule in $BuildOnlySourceModules) {
    $sourceBuildOnlyModule = Join-Path $Repo $buildOnlySourceModule
    $stagedBuildOnlyModule = Join-Path $Stage $buildOnlySourceModule
    if (-not (Test-Path -LiteralPath $sourceBuildOnlyModule -PathType Leaf) -or
        -not (Test-Path -LiteralPath $stagedBuildOnlyModule -PathType Leaf)) {
        throw "build-only 模块输入或精确 Stage 成员缺失，拒绝构建。"
    }
    Remove-Item -LiteralPath $stagedBuildOnlyModule -Force
    if (Test-Path -LiteralPath $stagedBuildOnlyModule) {
        throw "build-only 模块未从 Stage 精确移除，拒绝构建。"
    }
}
Write-Host "  - 仅构建期 update606 合同模块不进入 Stage"
$activationEndpointsDst = Join-Path $Stage "activation_service_endpoints.json"
Copy-Item -LiteralPath $ActivationServiceEndpointsPath -Destination $activationEndpointsDst -Force
$StagedActivationServiceEndpointsSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $activationEndpointsDst -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($StagedActivationServiceEndpointsSHA256 -cne $ActivationServiceEndpointsSHA256) {
    throw "staged 激活入口配置与受控源码字节不一致，拒绝构建。"
}
Write-Host "  + activation_service_endpoints.json (generation=$ActivationServiceEndpointsGeneration, endpoint_3=$ActivationServiceEndpoint3Enabled)"
$activationHttpE2EEDst = Join-Path $Stage "activation_http_e2ee.json"
Copy-Item -LiteralPath $ActivationHttpE2EEPath -Destination $activationHttpE2EEDst -Force
$StagedActivationHttpE2EESHA256 = (
    & $TrustedFileHashCommand -LiteralPath $activationHttpE2EEDst -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($StagedActivationHttpE2EESHA256 -cne $ActivationHttpE2EEConfigSHA256) {
    throw "staged HTTP 密文激活配置与受控源码字节不一致，拒绝构建。"
}
Write-Host "  + activation_http_e2ee.json (enabled=$ActivationHttpE2EEEnabled, key_id=$ActivationHttpE2EEKeyID)"
# scripts: 只 live 流程要的(monitor + 启动 + 依赖)，不拷整个 scripts(80+ 校准脚本)
$scriptsDst = Join-Path $Stage "scripts"
New-Item -ItemType Directory -Force -Path $scriptsDst | Out-Null
foreach ($s in @(
        "run_npcap_live_monitor.py", "run_wss_live_monitor.py",
        "mitm_bidking_wss_addon.py", "bidking_wss_capture_core.py",
        "_local_compat_core.py", "local_compat_launcher.py",
        "runtime_component.py", "local_compat_component.py",
        "mitmproxy_runtime_manifest.json",
        "local_integrity_namespace_guard.py",
        "run_fatbeans_webhook_monitor.py", "package_generation_identity.py",
        "run_windivert_live_monitor.py",
        "start_live_npcap_overlay.ps1", "live_process_discovery.ps1",
        "remove_wss_capture_ca.ps1", "wss_environment_preflight.ps1",
        "collect_diagnostics.ps1",
        "match_records_html.py", "build_match_records.py"
    )) {
    $p = Join-Path $Repo "scripts\$s"
    if (Test-Path $p) { Copy-Item $p (Join-Path $scriptsDst $s) -Force }
}
$mitmproxyManifestDst = Join-Path $scriptsDst "mitmproxy_runtime_manifest.json"
if (-not (Test-Path -LiteralPath $mitmproxyManifestDst -PathType Leaf)) {
    throw "Stage 缺 bundled WSS runtime manifest，拒绝构建。"
}
$sourceMitmproxyManifestSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $mitmproxyManifestPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$stagedMitmproxyManifestSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $mitmproxyManifestDst -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($stagedMitmproxyManifestSHA256 -cne $sourceMitmproxyManifestSHA256) {
    throw "staged mitmproxy_runtime_manifest.json 与受控源码字节不一致，拒绝构建。"
}
Write-Host "  + scripts/mitmproxy_runtime_manifest.json (fixed runtime identity)"
$packageDataFingerprintDst = Join-Path $scriptsDst "package_data_fingerprints.json"
Copy-Item -LiteralPath $packageDataFingerprintPath -Destination $packageDataFingerprintDst -Force
$StagedPackageDataFingerprintSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $packageDataFingerprintDst -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($StagedPackageDataFingerprintSHA256 -cne $PackageDataFingerprintSHA256) {
    throw "staged package_data_fingerprints.json 与受控源码字节不一致，拒绝构建。"
}
Write-Host "  + scripts/package_data_fingerprints.json (fixed identity)"
$processDiscoveryDst = Join-Path $scriptsDst "live_process_discovery.ps1"
if (-not (Test-Path -LiteralPath $processDiscoveryDst)) {
    throw "缺 WMI/CIM 降级进程发现组件: scripts/live_process_discovery.ps1，拒绝出包。"
}
# 公版主 EXE 通过预编译壳调用此脚本且不显式传诊断档位，因此 staged 脚本的默认值
# 就是公版真实行为。若它漂回 engineering，会持续落盘精确三档报价；构建必须 fail-closed。
$publicLauncherScript = Join-Path $scriptsDst "start_live_npcap_overlay.ps1"
$publicProfileDeclaration = '[string]$DiagnosticProfile = "' + $PublicDiagnosticProfile + '"'
if (-not (Select-String -LiteralPath $publicLauncherScript -SimpleMatch $publicProfileDeclaration -Quiet)) {
    throw "公版启动脚本默认诊断档位不是 $PublicDiagnosticProfile，拒绝出包。"
}
Write-Host "  + 公版诊断档位: $PublicDiagnosticProfile (不持续写入精确报价摘要)"
# 上传配置: 群发加密包必须内置 upload_config.enc; 非加密作者包可用明文 .local.ps1。
# -Encrypt 已在上方硬性要求主密钥。.local.ps1 是构建期输入，必须加密成
# data/processed/upload_config.enc，然后绝不拷贝明文。
#   不拷明文(群发版形态: 群友激活后 activation.data_key() 解密 -> HMAC 上传; 提取包拿不到 key)。
# 非 -Encrypt 才允许拷明文 .local.ps1，作为作者本机开发包。
$EncUploadConfig = $false
$dataProcessedDst = Join-Path $Stage "data\processed"
$encOut = Join-Path $dataProcessedDst "upload_config.enc"
$origDataKey = $env:BIDKING_DATA_KEY
try {
    if ($env:BIDKING_DATA_KEY -and (Test-Path $uploadLocal)) {
        $encHelper = Join-Path $Repo "scripts\encrypt_upload_config.py"
        $prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $encLog = (& $BuildPython -E $encHelper --local $uploadLocal --out $encOut 2>&1 | Out-String)
        $encExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($encExit -eq 0 -and (Test-Path $encOut) -and (Get-Item $encOut).Length -gt 0) {
            $EncUploadConfig = $true
            # helper 已完成解密回读；先绑定生成物哈希，等全部 staged copy 结束后再确认成品仍是同一文件。
            # 不能在这里提前把 manifest 门标绿：后续 data/processed copy 曾用仓库旧密文覆盖生成物。
            $GeneratedUploadConfigSHA256 = (
                & $TrustedFileHashCommand -LiteralPath $encOut -Algorithm SHA256
            ).Hash
            Write-Host "  + data/processed/upload_config.enc (群发版加密上传配置, 无明文 key)" -ForegroundColor Green
        } else {
            # #5 fail-loud: 已设主密钥(群发意图)却加密失败 -> 【绝不回退明文 .local.ps1】(含 ingest API key,
            # 打进 -encrypted 包 = 明文密钥泄露, 且用户误以为已加密)。硬失败拒绝出包。
            throw "加密上传配置失败(已传 -MasterKeyFile 为群发意图), 拒绝回退明文密钥进包。修好 encrypt_upload_config 再打: $encLog"
        }
    } elseif (Test-Path $uploadLocal) {
        Copy-Item $uploadLocal (Join-Path $scriptsDst "sample_upload.local.ps1") -Force
        Write-Host "  + scripts/sample_upload.local.ps1 (本机上传配置, 仅作者包; 群发版请传 -MasterKeyFile)" -ForegroundColor Yellow
    }
} finally {
    $env:BIDKING_DATA_KEY = $origDataKey
}
if ($Encrypt -and -not $EncUploadConfig) {
    throw "-Encrypt 群发包未生成非空 upload_config.enc，拒绝继续打包。"
}
Write-Host "  + 源码(hero_ref/src,tools,assets; src/bidking_lab; scripts/live)"

# --- 4b. 引擎 Cython 加密(-Encrypt): 声明的引擎模块 -> .pyd, 删 .py ---
if ($Encrypt) {
    Write-Host "  加密: Cython 编译引擎 -> .pyd ..."
    $engSrc = Join-Path $Stage "hero_ref/src"
    # moat 引擎模块 -> .pyd。反 AI 逆向: 见下 cythonize 的 compiler_directives(去 docstring/源注释)。
    # 【.pyd 扩展计划(L1, 需在打包时 test-compile 后逐个启用, 别盲加致打包静默失败)】候选:
    #   value/catalog 锁逻辑已在 ahmad_ref_engine 内(已覆盖); 待扩 = monitor / panel_server /
    #   sample_upload(含上传/激活逻辑)。这些在包结构里(相对 import), Cython 扁平编译需先验证
    #   能 import + 不破相对依赖, 故暂不默认启用; 扩时加进本数组并跑一次完整打包冒烟。
    $mods = @($ClientProtection.critical_native.engine_modules)
    # 扁平临时目录编译(engSrc 有 __init__.py 会被当包, .pyd 嵌到 src/ 子目录), 再拷回
    $cytTmp = Join-Path $Dist ".cyt_build"
    if (Test-Path $cytTmp) { Remove-Item $cytTmp -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $cytTmp | Out-Null
    foreach ($m in $mods) { Copy-Item (Join-Path $engSrc "$m.py") (Join-Path $cytTmp "$m.py") -Force }
    $engineAugmentation = Join-Path $Repo "scripts\cython\ahmad_ref_engine.pxd"
    if (-not (Test-Path -LiteralPath $engineAugmentation -PathType Leaf)) {
        throw "缺 Cython build-only internalization 声明，拒绝编译引擎。"
    }
    Copy-Item -LiteralPath $engineAugmentation `
        -Destination (Join-Path $cytTmp "ahmad_ref_engine.pxd") -Force
    $setupPy = Join-Path $cytTmp "_cyt_setup.py"
    @(
        "import sys",
        "from setuptools import Extension, setup",
        "from Cython.Build import cythonize",
        "mods = sys.argv[1:]",
        "sys.argv = [sys.argv[0], 'build_ext', '--inplace']",
        # 反 AI/反逆向 L1(已 A/B 实测): Cython 全局 Options.docstrings=False 去掉编进 .pyd 的算法讲解
        # docstring(AI 逆向最大抓手; 实测编出的 .pyd 里 grep 不到 docstring); compiler_directive embedsignature=False
        # 不嵌调用签名。注意: docstrings 是 Cython 【全局 Options】不是 compiler_directive(写进 directives 会
        # ValueError 让 build 挂); emit_code_comments 作为 compiler directive 关闭生成 C 中的源码注释。
        "import Cython.Compiler.Options as _cyopt",
        "_cyopt.docstrings = False",
        "compile_flags = ['/O2', '/GL', '/guard:cf']",
        "link_flags = ['/LTCG', '/OPT:REF', '/OPT:ICF', '/GUARD:CF', '/DYNAMICBASE', '/NXCOMPAT', '/HIGHENTROPYVA']",
        "exts = [Extension(m, [m + '.py'], extra_compile_args=compile_flags, extra_link_args=link_flags) for m in mods]",
        "setup(ext_modules=cythonize(exts, language_level=3, quiet=True, compiler_directives={'embedsignature': False, 'emit_code_comments': False}))"
    ) | Set-Content -Path $setupPy -Encoding ascii
    Push-Location $cytTmp
    & $BuildPython "_cyt_setup.py" @mods
    $ok = ($LASTEXITCODE -eq 0)
    Pop-Location
    if (-not $ok) { throw "Cython compile failed" }
    foreach ($m in $mods) {
        $pyd = Get-ClientNativeModuleArtifact -Directory $cytTmp -ModuleName $m `
            -AllowedAbiTags @($ClientProtection.native_abi_tags)
        Copy-Item $pyd.FullName (Join-Path $engSrc $pyd.Name) -Force
        Remove-Item (Join-Path $engSrc "$m.py") -Force
    }
    Remove-Item $cytTmp -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host ("  + 引擎已加密为 .pyd (" + ($mods -join " / ") + ")")
}

# --- 4b-1. 高价值产品工具 Cython 加密(-Encrypt): UI/详情/网络/产品策略 -> .pyd ---
# Overlay 的启动文件名是 launcher/process discovery 的稳定合同，因此完整实现编译到
# `_protected_ahmad_tk_overlay.pyd`，原路径只留下一个极小、可审计的启动壳。
if ($Encrypt) {
    Write-Host "  加密: Cython 编译高价值产品工具与 Overlay -> .pyd ..."
    $toolSrc = Join-Path $Stage "hero_ref\tools"
    $toolMods = @($ClientProtection.critical_native.tool_modules)
    $overlaySourceName = Split-Path ([string]$ClientProtection.critical_native.overlay.source) -Leaf
    $overlayNativeModule = [string]$ClientProtection.critical_native.overlay.native_module
    $toolSetup = Join-Path $toolSrc "_cyt_tool_setup.py"
    @(
        "import sys",
        "from setuptools import Extension, setup",
        "from Cython.Build import cythonize",
        "import Cython.Compiler.Options as _cyopt",
        "_cyopt.docstrings = False",
        "_cyopt.error_on_unknown_names = False",
        "mods = sys.argv[1:]",
        "sys.argv = [sys.argv[0], 'build_ext', '--inplace']",
        "compile_flags = ['/O2', '/GL', '/guard:cf']",
        "link_flags = ['/LTCG', '/OPT:REF', '/OPT:ICF', '/GUARD:CF', '/DYNAMICBASE', '/NXCOMPAT', '/HIGHENTROPYVA']",
        "exts = [Extension('$overlayNativeModule', ['$overlaySourceName'], extra_compile_args=compile_flags, extra_link_args=link_flags)]",
        "exts.extend(Extension(m, [m + '.py'], extra_compile_args=compile_flags, extra_link_args=link_flags) for m in mods)",
        "setup(ext_modules=cythonize(exts, language_level=3, quiet=True, compiler_directives={'embedsignature': False, 'emit_code_comments': False}))"
    ) | Set-Content -Path $toolSetup -Encoding ascii
    Push-Location $toolSrc
    & $BuildPython "_cyt_tool_setup.py" @toolMods
    $toolOk = ($LASTEXITCODE -eq 0)
    Pop-Location
    if (-not $toolOk) { throw "高价值产品工具 Cython compile failed" }

    $overlayPyd = Get-ClientNativeModuleArtifact -Directory $toolSrc `
        -ModuleName $overlayNativeModule `
        -AllowedAbiTags @($ClientProtection.native_abi_tags)
    Remove-Item (Join-Path $toolSrc $overlaySourceName) -Force
    Remove-Item (Join-Path $toolSrc ($overlaySourceName -replace "\.py$", ".c")) -Force -ErrorAction SilentlyContinue
    @(
        "import multiprocessing",
        "from $overlayNativeModule import main",
        "",
        "if __name__ == `"__main__`":",
        "    multiprocessing.freeze_support()",
        "    raise SystemExit(main())"
    ) | Set-Content -Path (Join-Path $toolSrc $overlaySourceName) -Encoding ascii

    foreach ($m in $toolMods) {
        $pyd = Get-ClientNativeModuleArtifact -Directory $toolSrc -ModuleName $m `
            -AllowedAbiTags @($ClientProtection.native_abi_tags)
        Remove-Item (Join-Path $toolSrc "$m.py") -Force
        Remove-Item (Join-Path $toolSrc "$m.c") -Force -ErrorAction SilentlyContinue
    }
    foreach ($excluded in @($ClientProtection.encrypted_stage_excludes)) {
        $excludedPath = Join-Path $Stage ([string]$excluded)
        if (Test-Path -LiteralPath $excludedPath -PathType Leaf) {
            Remove-Item -LiteralPath $excludedPath -Force
        }
    }
    Remove-Item $toolSetup -Force -ErrorAction SilentlyContinue
    $toolBuildDir = Join-Path $toolSrc "build"
    if (Test-Path $toolBuildDir) { Remove-Item $toolBuildDir -Recurse -Force }
    Get-ChildItem $toolSrc -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  + 高价值工具已原生化；Overlay 原路径仅保留最小启动壳"
}

# --- 4b-2. 包内模块 Cython 加密(-Encrypt): secure 层(激活编排)+ live moat 模块 -> .pyd ---
# 与 4b 区别: 这些在【包内】(src/bidking_lab/**)有相对导入(from .x import y), 必须【原地编译】
# (build_ext --inplace 把 .pyd 放回包内, 相对导入运行时正常), 不能像 4b 用 flat-temp(会破相对导入)。
# 覆盖面经 scripts/verify_pyd_compile.py 逐模块体检通过(编译 + 完整包路径 import 全绿)才纳入。
# inference 曾因 Cython 局部类型严检在运行时失败；本保护边界只在完整 41 模块
# 编译/import 以及下方两份真实 monitor fixture 都通过后启用。导入成功不能替代 6c 的
# build_monitor_artifact 产品路径，任何运行时类型冲突都会 fail-closed 拒绝出包。
if ($Encrypt) {
    Write-Host "  加密: Cython 编译包内模块(secure + live)-> .pyd ..."
    $pkgSrc = Join-Path $Stage "src"
    $pkgMods = @($ClientProtection.critical_native.package_modules)
    foreach ($tree in @($ClientProtection.critical_native.package_trees)) {
        $treeRoot = Join-Path $pkgSrc ([string]$tree.path)
        if (-not (Test-Path -LiteralPath $treeRoot -PathType Container)) {
            throw "客户端保护清单要求的包内模块树不存在，拒绝构建。"
        }
        $treeModules = @($tree.protected_modules)
        if ($treeModules.Count -eq 0) {
            throw "包内高价值模块树未声明精确模块集合，拒绝构建。"
        }
        foreach ($relative in $treeModules) {
            $normalizedTree = ([string]$tree.path).Trim("/").Replace("\", "/")
            $normalizedRelative = ([string]$relative).Replace("\", "/")
            if (
                -not $normalizedRelative.StartsWith(
                    "$normalizedTree/",
                    [System.StringComparison]::Ordinal
                ) -or
                -not $normalizedRelative.EndsWith(
                    ".py",
                    [System.StringComparison]::Ordinal
                ) -or
                -not (Test-Path -LiteralPath (Join-Path $pkgSrc $normalizedRelative) -PathType Leaf)
            ) {
                throw "包内高价值模块树的精确模块声明无效，拒绝构建。"
            }
        }
        $pkgMods += $treeModules
    }
    $pkgMods = @($pkgMods | Sort-Object -Unique)
    $pkgSetup = Join-Path $pkgSrc "_cyt_pkg_setup.py"
    @(
        "import sys",
        "from setuptools import Extension, setup",
        "from Cython.Build import cythonize",
        "import Cython.Compiler.Options as _cyopt",
        "_cyopt.docstrings = False",   # 同 4b: 去 docstring(反 AI 逆向 L1)
        "mods = sys.argv[1:]",
        "sys.argv = [sys.argv[0], 'build_ext', '--inplace']",
        "compile_flags = ['/O2', '/GL', '/guard:cf']",
        "link_flags = ['/LTCG', '/OPT:REF', '/OPT:ICF', '/GUARD:CF', '/DYNAMICBASE', '/NXCOMPAT', '/HIGHENTROPYVA']",
        "exts = [Extension(m[:-3].replace('/', '.'), [m], extra_compile_args=compile_flags, extra_link_args=link_flags) for m in mods]",
        "setup(ext_modules=cythonize(exts, language_level=3, quiet=True, compiler_directives={'embedsignature': False, 'emit_code_comments': False}))"
    ) | Set-Content -Path $pkgSetup -Encoding ascii
    Push-Location $pkgSrc
    & $BuildPython "_cyt_pkg_setup.py" @pkgMods
    $pok = ($LASTEXITCODE -eq 0)
    Pop-Location
    if (-not $pok) { throw "包内模块 Cython compile failed" }
    foreach ($rel in $pkgMods) {
        $relPyd = $rel -replace "\.py$", ""
        $stem = Split-Path $relPyd -Leaf
        $dir = Join-Path $pkgSrc (Split-Path $relPyd -Parent)
        $pyd = Get-ClientNativeModuleArtifact -Directory $dir -ModuleName $stem `
            -AllowedAbiTags @($ClientProtection.native_abi_tags)
        Remove-Item (Join-Path $pkgSrc $rel) -Force                       # 删 .py(只留 .pyd)
        Remove-Item (Join-Path $pkgSrc ($rel -replace "\.py$", ".c")) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $pkgSetup -Force -ErrorAction SilentlyContinue
    # build_ext --inplace 仍会留下 src/build 下的 obj/lib/重复 pyd；它们不是运行时输入，
    # 进入群发 ZIP 只会增加体积并暴露无用的编译中间物。
    $pkgBuildDir = Join-Path $pkgSrc "build"
    if (Test-Path $pkgBuildDir) { Remove-Item $pkgBuildDir -Recurse -Force }
    if (Test-Path $pkgBuildDir) { throw "Cython 中间目录清理失败: $pkgBuildDir" }
    Get-ChildItem $pkgSrc -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  + 包内高价值模块已加密为 .pyd (secure/live/runtime/inference)"
}

# --- 4b-3. 产品本地兼容模块(-Encrypt): 编译为泛化 .pyd，仅保留最小启动壳 ---
# mitmdump 的 -s 入口必须是 Python 文件，因此 addon/monitor 各保留一个最小 bootstrap；
# 产品 bridge/core/monitor 实现本身不得以可直接阅读 .py 进入群发包。
if ($Encrypt) {
    Write-Host "  加密: 编译本地兼容模块并生成最小启动壳 ..."
    $wssSrc = Join-Path $Stage "scripts"
    $launcherSourcePath = Join-Path $wssSrc "local_compat_launcher.py"
    $launcherTemplate = Get-Content -LiteralPath $launcherSourcePath -Raw -Encoding utf8
    $lifecycleGenerationMarker = "__BIDKING_NATIVE_LIFECYCLE_GENERATION__"
    if (-not $launcherTemplate.Contains($lifecycleGenerationMarker) -or
        $launcherTemplate.Contains("_POWERSHELL_PAYLOAD_B64") -or
        $launcherTemplate.Contains("_run_powershell_source") -or
        $launcherTemplate.Contains("_decode_payload")) {
        throw "本地兼容 launcher 未收敛到最小 native lifecycle ABI。"
    }
    $diagnosticsScriptPath = Join-Path $wssSrc "collect_diagnostics.ps1"
    $repairScriptPath = Join-Path $Repo "scripts\startup_repair.ps1"
    $lifecyclePreparer = Join-Path $Repo "scripts\prepare_client_lifecycle_payload.py"
    $lifecycleBuildDir = Join-Path $Dist ".client_lifecycle"
    if (Test-Path -LiteralPath $lifecycleBuildDir) {
        Remove-Item -LiteralPath $lifecycleBuildDir -Recurse -Force
    }
    & $BuildPython -B $lifecyclePreparer `
        --startup $publicLauncherScript `
        --process-discovery $processDiscoveryDst `
        --diagnostics $diagnosticsScriptPath `
        --repair $repairScriptPath `
        --output-dir $lifecycleBuildDir
    if ($LASTEXITCODE -ne 0) {
        throw "native lifecycle 构建输入准备失败。"
    }
    $lifecycleReceiptPath = Join-Path $lifecycleBuildDir "receipt.json"
    $lifecycleReceipt = Get-Content -LiteralPath $lifecycleReceiptPath -Raw -Encoding ascii | ConvertFrom-Json
    $lifecycleGeneration = [string]$lifecycleReceipt.generation
    if ([int]$lifecycleReceipt.schema_version -ne 1 -or
        [int]$lifecycleReceipt.abi -ne 2 -or
        $lifecycleGeneration -cnotmatch '^[0-9a-f]{64}$') {
        throw "native lifecycle receipt 合同无效。"
    }
    $lifecycleLauncherPayload = Join-Path $lifecycleBuildDir "launcher.payload"
    $lifecycleDiagnosticsPayload = Join-Path $lifecycleBuildDir "diagnostics.payload"
    $lifecycleRepairPayload = Join-Path $lifecycleBuildDir "repair.payload"
    foreach ($payloadSpec in @(
            [pscustomobject]@{
                Path = $lifecycleLauncherPayload
                Receipt = $lifecycleReceipt.launcher
            },
            [pscustomobject]@{
                Path = $lifecycleDiagnosticsPayload
                Receipt = $lifecycleReceipt.diagnostics
            },
            [pscustomobject]@{
                Path = $lifecycleRepairPayload
                Receipt = $lifecycleReceipt.repair
            }
        )) {
        $payloadPath = [string]$payloadSpec.Path
        $payloadReceipt = $payloadSpec.Receipt
        $payloadInfo = Get-Item -LiteralPath $payloadPath
        $payloadHash = (& $TrustedFileHashCommand -LiteralPath $payloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($payloadInfo.Length -ne [long]$payloadReceipt.bytes -or
            $payloadHash -cne [string]$payloadReceipt.sha256) {
            throw "native lifecycle payload receipt 与字节不一致。"
        }
    }
    $launcherGenerated = $launcherTemplate.Replace(
        $lifecycleGenerationMarker,
        $lifecycleGeneration
    )
    [System.IO.File]::WriteAllText(
        $launcherSourcePath,
        $launcherGenerated,
        [System.Text.UTF8Encoding]::new($false)
    )
    # 固定 mitmproxy 12.2.3 自带 Python 3.14，不能加载普通客户端
    # Python 3.13 扩展。构建期把 addon/core 加密进 Windows stable-ABI 扩展，
    # 公包只保留最小 Python 壳；这同时避免旧版 gzip/base64 负载被静态还原。
    $addonBootstrapPath = Join-Path $wssSrc "local_compat_addon.py"
    $addonNativeSourcePath = Join-Path $wssSrc "_local_compat_addon_loader.c"
    & $BuildPython -B $wssAddonBootstrapBuilder render `
        --core (Join-Path $wssSrc "bidking_wss_capture_core.py") `
        --addon (Join-Path $wssSrc "mitm_bidking_wss_addon.py") `
        --output $addonBootstrapPath `
        --native-c-output $addonNativeSourcePath `
        --contract $ClientProtectionManifestPath
    if ($LASTEXITCODE -ne 0 -or
        -not (Test-Path -LiteralPath $addonBootstrapPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $addonNativeSourcePath -PathType Leaf)) {
        throw "WSS addon 跨运行时保护模块生成失败。"
    }
    $wssSetup = Join-Path $wssSrc "_cyt_local_compat_setup.py"
    @(
        "import sys",
        "from setuptools import Extension, setup",
        "from Cython.Build import cythonize",
        "import Cython.Compiler.Options as _cyopt",
        "_cyopt.docstrings = False",
        "compile_flags = ['/O2', '/GL', '/guard:cf']",
        "link_flags = ['/LTCG', '/OPT:REF', '/OPT:ICF', '/GUARD:CF', '/DYNAMICBASE', '/NXCOMPAT', '/HIGHENTROPYVA']",
        "extensions = [",
        "    Extension('_local_compat_core', ['bidking_wss_capture_core.py'], extra_compile_args=compile_flags, extra_link_args=link_flags),",
        "    Extension('_local_compat_monitor', ['run_wss_live_monitor.py'], extra_compile_args=compile_flags, extra_link_args=link_flags),",
        "    Extension('_client_launcher', ['local_compat_launcher.py'], extra_compile_args=compile_flags, extra_link_args=link_flags),",
        "    Extension('_runtime_component', ['runtime_component.py'], extra_compile_args=compile_flags, extra_link_args=link_flags),",
        "    Extension('_local_compat_addon_loader', ['_local_compat_addon_loader.c'], define_macros=[('Py_LIMITED_API', '0x030D0000')], py_limited_api=True, libraries=['bcrypt'], extra_compile_args=compile_flags, extra_link_args=link_flags),",
        "]",
        "sys.argv = [sys.argv[0], 'build_ext', '--inplace']",
        "setup(ext_modules=cythonize(extensions, language_level=3, quiet=True, compiler_directives={'embedsignature': False, 'emit_code_comments': False, 'binding': False}))"
    ) | Set-Content -LiteralPath $wssSetup -Encoding ascii
    Push-Location $wssSrc
    & $BuildPython "_cyt_local_compat_setup.py"
    $wssCompileOk = ($LASTEXITCODE -eq 0)
    Pop-Location
    if (-not $wssCompileOk) {
        throw "本地兼容模块 Cython compile failed"
    }
    foreach ($compiledName in @(
            "_local_compat_core",
            "_local_compat_monitor",
            "_client_launcher",
            "_runtime_component",
            "_local_compat_addon_loader"
        )) {
        if (-not (Get-ChildItem -LiteralPath $wssSrc -Filter "$compiledName*.pyd" -File | Select-Object -First 1)) {
            throw "本地兼容模块缺编译产物: $compiledName"
        }
    }
    $protectedAddonModule = @(
        Get-ChildItem -LiteralPath $wssSrc -Filter "_local_compat_addon_loader*.pyd" -File
    )
    if (
        $protectedAddonModule.Count -ne 1 -or
        $protectedAddonModule[0].Name -cne "_local_compat_addon_loader.pyd"
    ) {
        throw "WSS operational native loader identity is ambiguous."
    }
    $WssAddonBootstrapSHA256 = (
        & $TrustedFileHashCommand -LiteralPath $addonBootstrapPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $WssAddonLoaderSHA256 = (
        & $TrustedFileHashCommand -LiteralPath $protectedAddonModule[0].FullName -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($WssAddonBootstrapSHA256 -cne [string]$WssOperationalContract.bootstrap_sha256) {
        throw "WSS operational bootstrap identity mismatch."
    }
    $launcherSmokePython = Join-Path $RuntimePy "python.exe"
    if (-not (Test-Path -LiteralPath $launcherSmokePython -PathType Leaf)) {
        throw "缺内嵌 Python，无法实跑编译启动器。"
    }
    Push-Location $wssSrc
    try {
        & $launcherSmokePython -E -s -c "import _client_launcher as m; assert callable(m.main) and callable(m.diagnostics_main) and callable(m.diagnostics_receipt_main) and callable(m.repair_main); assert not any(hasattr(m, n) for n in ('_decode_payload', '_run_powershell_source', '_POWERSHELL_PAYLOAD_B64', '_DIAGNOSTICS_PAYLOAD_B64', '_REPAIR_PAYLOAD_B64'))"
        $launcherAbiSmokeOk = ($LASTEXITCODE -eq 0)
    } finally {
        Pop-Location
    }
    if (-not $launcherAbiSmokeOk) {
        throw "编译启动器未收敛到最小 native lifecycle ABI。"
    }
    Write-Host "  + 编译启动器最小 native lifecycle ABI 导入门通过"
    Write-Host "  + 编译启动器大脚本与公版诊断入口实跑通过"
    $previousWssOperationalEnvironment = [ordered]@{
        BIDKING_WSS_OPERATIONAL_ABI = $env:BIDKING_WSS_OPERATIONAL_ABI
        BIDKING_WSS_PACKAGE_ROOT = $env:BIDKING_WSS_PACKAGE_ROOT
        BIDKING_WSS_PACKAGE_GENERATION = $env:BIDKING_WSS_PACKAGE_GENERATION
        BIDKING_WSS_OPERATIONAL_GENERATION = $env:BIDKING_WSS_OPERATIONAL_GENERATION
        BIDKING_WSS_RUNTIME_ROLE = $env:BIDKING_WSS_RUNTIME_ROLE
    }
    try {
        $env:BIDKING_WSS_OPERATIONAL_ABI = $WssOperationalABI
        $env:BIDKING_WSS_PACKAGE_ROOT = $Stage
        $env:BIDKING_WSS_PACKAGE_GENERATION = $ClientProtectionManifestSHA256
        $env:BIDKING_WSS_OPERATIONAL_GENERATION = $WssOperationalGeneration
        $env:BIDKING_WSS_RUNTIME_ROLE = "main"
        Push-Location $wssSrc
        try {
            & $launcherSmokePython -E -s -c (
                "import local_compat_addon as a; " +
                "assert type(a.addons) is list and len(a.addons) == 1; " +
                "assert not hasattr(__import__('_local_compat_addon_loader'), 'source')"
            )
            $wssMainRuntimeSmokeOk = ($LASTEXITCODE -eq 0)
        } finally {
            Pop-Location
        }
    } finally {
        foreach ($entry in $previousWssOperationalEnvironment.GetEnumerator()) {
            if ($null -eq $entry.Value) {
                Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
            } else {
                Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value
            }
        }
    }
    if (-not $wssMainRuntimeSmokeOk) {
        throw "主 runtime 无法通过WSS operational ABI加载addon，拒绝生成候选。"
    }
    Write-Host "  + 主 runtime WSS operational ABI smoke通过（无source export）"
    foreach ($plainName in @(
            "run_wss_live_monitor.py",
            "mitm_bidking_wss_addon.py",
            "bidking_wss_capture_core.py",
            "_local_compat_core.py",
            "local_compat_launcher.py",
            "runtime_component.py"
        )) {
        Remove-Item -LiteralPath (Join-Path $wssSrc $plainName) -Force
    }
    Get-ChildItem -LiteralPath $wssSrc -Filter "*.c" -File |
        Where-Object {
            $_.BaseName -in @(
                "run_wss_live_monitor",
                "mitm_bidking_wss_addon",
                "bidking_wss_capture_core",
                "local_compat_launcher",
                "runtime_component",
                "_local_compat_addon_loader"
            )
        } |
        Remove-Item -Force
    Remove-Item -LiteralPath $wssSetup -Force
    $wssBuildDir = Join-Path $wssSrc "build"
    if (Test-Path -LiteralPath $wssBuildDir) {
        Remove-Item -LiteralPath $wssBuildDir -Recurse -Force
    }
    $monitorBootstrap = @'
from _local_compat_monitor import main
if __name__ == "__main__":
    raise SystemExit(main())
'@
    [System.IO.File]::WriteAllText(
        (Join-Path $wssSrc "local_compat_monitor.py"),
        $monitorBootstrap,
        [System.Text.UTF8Encoding]::new($false)
    )
    $publicLauncherShell = @'
param(
  [string]$PythonPath = "",
  [ValidateSet("engineering", "portable", "public-safe", "stable")]
  [string]$DiagnosticProfile = "__PUBLIC_PROFILE__",
  [switch]$Restart,
  [switch]$NoTaskbar
)
$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $PythonPath) {
  $PythonPath = Join-Path $Repo "runtime\python\python.exe"
}
function Show-LaunchError([string]$Message) {
  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    [System.Windows.Forms.MessageBox]::Show(
      $Message,
      "BidKing Live · 启动失败",
      "OK",
      "Error"
    ) | Out-Null
  } catch {
    Write-Host $Message -ForegroundColor Red
  }
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
  Show-LaunchError "运行组件缺失。请重新完整解压安装包后再试。"
  exit 1
}
$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltinRole]::Administrator
)
if (-not $IsAdmin) {
  $args = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"'),
    "-PythonPath", ('"' + $PythonPath + '"'),
    "-DiagnosticProfile", $DiagnosticProfile
  )
  if ($Restart) { $args += "-Restart" }
  if ($NoTaskbar) { $args += "-NoTaskbar" }
  Start-Process -FilePath "powershell.exe" -Verb RunAs -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList $args
  exit 0
}
$RuntimeRoot = Split-Path -Parent $PythonPath
$env:TCL_LIBRARY = Join-Path $RuntimeRoot "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $RuntimeRoot "tcl\tk8.6"
$integrityBootstrap = @"
import importlib.util
import pathlib
import sys
_MODULE_MISSING = object()
def _load_exact(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("bootstrap_exact_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name, _MODULE_MISSING)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is _MODULE_MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module
_kernel = None
_namespace_lease = None
_INTEGRITY_FAILURE_EXITS = {
    "integrity_member_path_invalid": 101,
    "integrity_reparse_or_symlink_rejected": 102,
    "integrity_file_open_failed": 103,
    "integrity_stream_namespace_invalid": 104,
    "integrity_stream_inventory_unavailable": 105,
    "integrity_stream_read_failed": 106,
    "integrity_stream_identity_changed": 107,
    "integrity_file_identity_unavailable": 108,
    "integrity_file_missing": 109,
    "integrity_file_type_invalid": 110,
    "integrity_named_stream_rejected": 111,
    "integrity_directory_missing": 112,
    "integrity_directory_type_invalid": 113,
    "integrity_directory_open_failed": 114,
    "integrity_directory_identity_changed": 115,
    "integrity_directory_identity_unavailable": 116,
    "integrity_file_read_failed": 117,
    "integrity_file_identity_changed": 118,
    "integrity_coverage_invalid": 119,
    "integrity_coverage_unreadable": 120,
    "integrity_namespace_unreadable": 121,
    "integrity_namespace_invalid": 122,
    "integrity_member_identity_collision": 123,
    "integrity_signature_encoding_invalid": 124,
    "integrity_public_key_missing": 125,
    "integrity_signature_invalid": 126,
    "integrity_public_key_invalid": 127,
    "integrity_manifest_invalid": 128,
    "integrity_file_size_mismatch": 129,
    "integrity_file_hash_mismatch": 130,
    "integrity_coverage_mismatch": 131,
    "integrity_handle_retention_failed": 132,
    "integrity_namespace_retention_failed": 133,
    "integrity_namespace_not_verified": 134,
    "integrity_namespace_root_changed": 135,
    "integrity_namespace_changed": 136,
    "integrity_stream_namespace_changed": 137,
    "integrity_handle_release_failed": 138,
}
def _fail_with_safe_reason(code):
    exit_code = _INTEGRITY_FAILURE_EXITS.get(code) if type(code) is str else None
    if exit_code is not None:
        print("BIDKING_STARTUP_FAILURE=" + code, flush=True)
        raise SystemExit(exit_code)
    raise RuntimeError(code)
try:
    _root = pathlib.Path(sys.argv.pop(1)).resolve(strict=True)
    _candidates = tuple((_root / "src").glob("bidking_kernel*.pyd"))
    if len(_candidates) != 1 or not _candidates[0].is_file() or _candidates[0].is_symlink():
        raise SystemExit(6)
    _kernel = _load_exact("bidking_kernel", _candidates[0])
    _ok, _code, _count, _manifest_id = _kernel.verify_local_integrity(str(_root))
    print("BIDKING_LOCAL_INTEGRITY=%s:%d:%s" % (_code, _count, _manifest_id))
    if not _ok:
        _fail_with_safe_reason(_code)
    _guard_path = _root / "scripts" / "local_integrity_namespace_guard.py"
    _guard = _load_exact(
        "_bidking_local_integrity_namespace_guard", _guard_path
    )
    _namespace_lease = _guard.begin_namespace_lease(_root)
    _namespace_ok, _namespace_code = _kernel.recheck_local_integrity(str(_root))
    if not _namespace_ok:
        print("BIDKING_LOCAL_INTEGRITY=%s" % _namespace_code)
        _fail_with_safe_reason(_namespace_code)
except BaseException as _bootstrap_error:
    try:
        if _namespace_lease is not None:
            _namespace_lease.close()
    except BaseException:
        pass
    finally:
        if _kernel is not None:
            try:
                _kernel.release_local_integrity()
            except BaseException:
                pass
    if (
        type(_bootstrap_error) is SystemExit
        and type(_bootstrap_error.code) is int
        and 101 <= _bootstrap_error.code <= 138
    ):
        raise
    if not isinstance(_bootstrap_error, SystemExit):
        print("BIDKING_LOCAL_INTEGRITY=bootstrap_failed")
    raise SystemExit(6)
"@
$runnerCode = $integrityBootstrap + "`n" + @"
try:
    from _client_launcher import main
    _managed_exit_code = main()
finally:
    try:
        _namespace_lease.close()
    finally:
        _released, _release_code = _kernel.release_local_integrity()
        if not _released:
            _fail_with_safe_reason(_release_code)
raise SystemExit(_managed_exit_code)
"@
$pythonBootstrapEntry = "import base64,sys;exec(compile(base64.b64decode(sys.argv.pop(1),validate=True),'<bidking-public-bootstrap>','exec'))"
$runnerPayload = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($runnerCode))
$runnerArgs = @(
  "-E", "-s", "-S", "-B", "-c",
  $pythonBootstrapEntry,
  $runnerPayload,
  $Repo,
  "--python-path", $PythonPath,
  "--diagnostic-profile", $DiagnosticProfile
)
if ($Restart) { $runnerArgs += "--restart" }
if ($NoTaskbar) { $runnerArgs += "--no-taskbar" }
& $PythonPath @runnerArgs
$runnerExit = $LASTEXITCODE
if ($runnerExit -ge 101 -and $runnerExit -le 138) {
  Write-Host "local_integrity_failed" -ForegroundColor Red
  $integrityMessage = switch ($runnerExit) {
    111 { "程序文件带有 Windows 下载标记。请先对官方 ZIP 执行「解除锁定」，再解压到新的空目录。" }
    131 { "程序包在运行后产生了额外文件，或目录混入了其他文件。请删除当前解压目录，并从官方 ZIP 重新完整解压；不要覆盖旧目录。" }
    default { "程序文件完整性验证失败。请删除当前目录，重新完整解压官方安装包后再试。" }
  }
  Show-LaunchError ($integrityMessage + "`n`n错误代码：" + $runnerExit)
}
exit $runnerExit
'@.Replace("__PUBLIC_PROFILE__", $PublicDiagnosticProfile)
    [System.IO.File]::WriteAllText(
        $publicLauncherScript,
        $publicLauncherShell,
        [System.Text.UTF8Encoding]::new($true)
    )
$publicDiagnosticsShell = @'
$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonPath = Join-Path $Repo "runtime\python\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
  Write-Host "运行组件缺失。请重新完整解压安装包后再试。" -ForegroundColor Red
  exit 1
}
$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltinRole]::Administrator
)
if (-not $IsAdmin) {
  Start-Process -FilePath "powershell.exe" -Verb RunAs -WindowStyle Hidden -WorkingDirectory $Repo -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"')
  )
  exit 0
}
$RuntimeRoot = Split-Path -Parent $PythonPath
$env:TCL_LIBRARY = Join-Path $RuntimeRoot "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $RuntimeRoot "tcl\tk8.6"
$integrityBootstrap = @"
__BIDKING_INTEGRITY_BOOTSTRAP__
"@
$diagnosticsCode = $integrityBootstrap + "`n" + @"
try:
    from _client_launcher import diagnostics_main
    _managed_exit_code = diagnostics_main()
finally:
    try:
        _namespace_lease.close()
    finally:
        _released, _release_code = _kernel.release_local_integrity()
        if not _released:
            _fail_with_safe_reason(_release_code)
raise SystemExit(_managed_exit_code)
"@
$pythonBootstrapEntry = "import base64,sys;exec(compile(base64.b64decode(sys.argv.pop(1),validate=True),'<bidking-public-bootstrap>','exec'))"
$diagnosticsPayload = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($diagnosticsCode))
& $PythonPath -E -s -S -B -c $pythonBootstrapEntry $diagnosticsPayload $Repo
$diagnosticsExit = $LASTEXITCODE
if ($diagnosticsExit -ge 101 -and $diagnosticsExit -le 138) {
  Write-Host "local_integrity_failed" -ForegroundColor Red
  exit $diagnosticsExit
}
if ($diagnosticsExit -ne 0) {
  Write-Host "local_integrity_failed" -ForegroundColor Red
  exit 6
}
exit 0
'@.Replace("__BIDKING_INTEGRITY_BOOTSTRAP__", $integrityBootstrap)
    [System.IO.File]::WriteAllText(
        $diagnosticsScriptPath,
        $publicDiagnosticsShell,
        [System.Text.UTF8Encoding]::new($true)
    )
    $windowsPowerShell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf)) {
        throw "缺 Windows PowerShell 5.1，无法验证群发 PowerShell 壳兼容性。"
    }
    $parseCommand = @'
$tokens = $null
$errors = $null
[Management.Automation.Language.Parser]::ParseFile(
    $env:BIDKING_PS_PARSE_TARGET,
    [ref]$tokens,
    [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }
    exit 1
}
'@
    $previousParseTarget = $env:BIDKING_PS_PARSE_TARGET
    try {
        $powerShellParseInputs = @(
            $publicLauncherScript,
            $diagnosticsScriptPath,
            (Join-Path $scriptsDst "wss_environment_preflight.ps1"),
            $repairScriptPath
        )
        foreach ($publicPowerShellShell in $powerShellParseInputs) {
            $shellBytes = [System.IO.File]::ReadAllBytes($publicPowerShellShell)
            $hasUtf8Bom = (
                $shellBytes.Length -ge 3 -and
                $shellBytes[0] -eq 0xEF -and
                $shellBytes[1] -eq 0xBB -and
                $shellBytes[2] -eq 0xBF
            )
            $hasNonAscii = @($shellBytes | Where-Object { [int]$_ -gt 127 }).Count -gt 0
            if ($hasNonAscii -and -not $hasUtf8Bom) {
                throw "含非ASCII文本的群发 PowerShell 壳缺 UTF-8 BOM，Windows PowerShell 5.1 可能误解中文内容。"
            }
            $env:BIDKING_PS_PARSE_TARGET = $publicPowerShellShell
            $parseOut = (& $windowsPowerShell -NoProfile -NonInteractive -Command $parseCommand 2>&1 | Out-String)
            if ($LASTEXITCODE -ne 0) {
                throw "群发 PowerShell 壳无法通过 Windows PowerShell 5.1 解析: $parseOut"
            }
        }
    } finally {
        if ($null -eq $previousParseTarget) {
            Remove-Item Env:\BIDKING_PS_PARSE_TARGET -ErrorAction SilentlyContinue
        } else {
            $env:BIDKING_PS_PARSE_TARGET = $previousParseTarget
        }
    }
    Write-Host "  + 群发启动/诊断/网吧预检/密封修复载荷通过 Windows PowerShell 5.1 解析门"
    Remove-Item -LiteralPath $processDiscoveryDst -Force
    Write-Host "  + 本地兼容实现已编译为 .pyd，群发包只保留泛化启动与诊断壳"
}

# --- 4c. Rust 安全内核(-Encrypt): native/bidking_kernel -> bidking_kernel.pyd, 放进包 src/(顶层可 import) ---
# 内核 = L4 设备指纹/激活校验/key派生 + client lifecycle helper。包内 src/ 已在 ._pth path(见步骤2),
# 故 .pyd 放 $Stage/src/ 即 `import bidking_kernel` 可解析(rust_provider.load_kernel 用顶层 import)。
# ABI: 编译用 $BuildPython(CPython3.13)= 包内嵌 python 同为 3.13 -> cp313 .pyd 通用。
if ($Encrypt) {
    Write-Host "  加密: maturin build Rust 激活内核 -> .pyd ..."
    $kernelDir = Join-Path $Repo "native\bidking_kernel"
    if (-not (Test-Path (Join-Path $kernelDir "Cargo.toml"))) { throw "no native/bidking_kernel/Cargo.toml" }
    # Rust 的 include!(OUT_DIR/...) 即使 release strip 后也可能把构建机用户目录嵌进 .pyd。
    # 用 Cargo 的分隔编码参数同时重映射仓库和用户目录；ZIP 负向扫描会验证没有漏网。
    $origEncodedRustFlags = [Environment]::GetEnvironmentVariable(
        "CARGO_ENCODED_RUSTFLAGS"
    )
    $origEncodedRustFlagsPresent = ($null -ne $origEncodedRustFlags)
    $rustFlagSeparator = [char]0x1f
    $releaseRustFlags = @($ControlledRustRemapFlags) + @(
        "-Ccontrol-flow-guard=checks",
        "-Clink-arg=/GUARD:CF",
        "-Clink-arg=/DYNAMICBASE",
        "-Clink-arg=/NXCOMPAT",
        "-Clink-arg=/HIGHENTROPYVA",
        "-Clink-arg=/OPT:REF",
        "-Clink-arg=/OPT:ICF"
    )
    $encodedReleaseRustFlags = $releaseRustFlags -join $rustFlagSeparator
    $origLifecycleLauncherFile = $env:BIDKING_LIFECYCLE_LAUNCHER_FILE
    $origLifecycleDiagnosticsFile = $env:BIDKING_LIFECYCLE_DIAGNOSTICS_FILE
    $origLifecycleRepairFile = $env:BIDKING_LIFECYCLE_REPAIR_FILE
    $origLifecycleGeneration = $env:BIDKING_LIFECYCLE_GENERATION
    # maturin 把 banner("Found CPython...")写到 stderr, EAP=Stop 会把它当 NativeCommandError 抛;
    # 临时放宽 EAP, 用 $LASTEXITCODE 兜底真失败(下方 $kok + throw)。同隔离验证步的既有模式。
    $prevEAP = $ErrorActionPreference
    $kernelLocationPushed = $false
    try {
        [Environment]::SetEnvironmentVariable(
            "CARGO_ENCODED_RUSTFLAGS",
            $encodedReleaseRustFlags,
            [EnvironmentVariableTarget]::Process
        )
        $env:BIDKING_LIFECYCLE_LAUNCHER_FILE = $lifecycleLauncherPayload
        $env:BIDKING_LIFECYCLE_DIAGNOSTICS_FILE = $lifecycleDiagnosticsPayload
        $env:BIDKING_LIFECYCLE_REPAIR_FILE = $lifecycleRepairPayload
        $env:BIDKING_LIFECYCLE_GENERATION = $lifecycleGeneration
        Push-Location $kernelDir
        $kernelLocationPushed = $true
        $ErrorActionPreference = "Continue"
        & $BuildPython -m maturin build --release -i $BuildPython 2>&1 | Out-Null
        $kok = ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prevEAP
        Restore-ControlledCargoEncodedRustFlags `
            -WasPresent $origEncodedRustFlagsPresent `
            -PreviousValue $origEncodedRustFlags
        if ($null -eq $origLifecycleLauncherFile) {
            Remove-Item Env:\BIDKING_LIFECYCLE_LAUNCHER_FILE -ErrorAction SilentlyContinue
        } else {
            $env:BIDKING_LIFECYCLE_LAUNCHER_FILE = $origLifecycleLauncherFile
        }
        if ($null -eq $origLifecycleDiagnosticsFile) {
            Remove-Item Env:\BIDKING_LIFECYCLE_DIAGNOSTICS_FILE -ErrorAction SilentlyContinue
        } else {
            $env:BIDKING_LIFECYCLE_DIAGNOSTICS_FILE = $origLifecycleDiagnosticsFile
        }
        if ($null -eq $origLifecycleRepairFile) {
            Remove-Item Env:\BIDKING_LIFECYCLE_REPAIR_FILE -ErrorAction SilentlyContinue
        } else {
            $env:BIDKING_LIFECYCLE_REPAIR_FILE = $origLifecycleRepairFile
        }
        if ($null -eq $origLifecycleGeneration) {
            Remove-Item Env:\BIDKING_LIFECYCLE_GENERATION -ErrorAction SilentlyContinue
        } else {
            $env:BIDKING_LIFECYCLE_GENERATION = $origLifecycleGeneration
        }
        if ($kernelLocationPushed) {
            Pop-Location
        }
        if (Test-Path -LiteralPath $lifecycleBuildDir) {
            Remove-Item -LiteralPath $lifecycleBuildDir -Recurse -Force
        }
    }
    if (-not $kok) { throw "maturin build (Rust kernel) failed" }
    $whl = Get-ChildItem (Join-Path $kernelDir "target\wheels") -Filter "bidking_kernel-*.whl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $whl) { throw "no bidking_kernel wheel produced" }
    $kExtract = Join-Path $Dist ".kernel_whl"
    if (Test-Path $kExtract) { Remove-Item $kExtract -Recurse -Force }
    & $BuildPython -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" $whl.FullName $kExtract
    $kpyd = Get-ChildItem $kExtract -Recurse -Filter "bidking_kernel*.pyd" | Select-Object -First 1
    if (-not $kpyd) { throw "no bidking_kernel .pyd in wheel" }
    Invoke-NativeArtifactPrivacyGate `
        -ArtifactPath $kpyd.FullName `
        -Label "Rust kernel" `
        -ForbiddenPaths $NativePrivacyForbiddenPaths
    Copy-Item $kpyd.FullName (Join-Path $Stage "src\$($kpyd.Name)") -Force
    Remove-Item $kExtract -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  + Rust 激活内核与 client lifecycle helper 已编入包 (src/$($kpyd.Name))"

}

# --- 5. 数据(显式 allowlist；复制集合 = checker 冻结集合) ---
# Hero.txt 是当前运行时英雄元数据输入，必须冻结并复制。Skill.txt 当前没有运行时读取路径且
# 源文件不存在，因此明确不随包；历史 activity/reconstructed 先验也不进入 Stage。
# upload_config.enc 已在 step 4 使用本次发布主密钥生成，永远不从源码树复制。
foreach ($rel in $packageDataFiles) {
    $relWindows = $rel.Replace("/", "\")
    $src = Join-Path $Repo $relWindows
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
        throw "统一发包数据清单中的必须文件缺失: $rel"
    }
    $dst = Join-Path $Stage $relWindows
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    if ($MutableStageMoatMembers -ccontains $rel) {
        $expectedIdentity = $MutableStageExpectedIdentities[$rel]
        if ($null -eq $expectedIdentity) {
            throw "[build] stage_mutable_copy_expected_identity_invalid"
        }
        Copy-BuildStageMutableLeaf `
            -StageRoot $Stage `
            -SourcePath $src `
            -DestinationPath $dst `
            -ExpectedSHA256 ([string]$expectedIdentity.SHA256) `
            -ExpectedBytes ([int64]$expectedIdentity.Bytes)
    } else {
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
}

# Stage 数据集合反向对账：除本次生成的 upload_config.enc 外，任何漏复制或额外文件都
# fail-closed。这样即使未来有人误加整目录复制，也不能绕过统一清单。
$stagePrefix = $Stage.TrimEnd("\") + "\"
$actualPackageDataFiles = @()
foreach ($dataRootRel in @("data\processed", "data\raw\tables")) {
    $dataRoot = Join-Path $Stage $dataRootRel
    if (Test-Path -LiteralPath $dataRoot) {
        foreach ($file in Get-ChildItem -LiteralPath $dataRoot -File -Recurse) {
            $rel = $file.FullName.Substring($stagePrefix.Length).Replace("\", "/")
            if ($rel -ne "data/processed/upload_config.enc") {
                $actualPackageDataFiles += $rel
            }
        }
    }
}
$packageDataDiff = @(Compare-Object `
    -ReferenceObject @($packageDataFiles | Sort-Object) `
    -DifferenceObject @($actualPackageDataFiles | Sort-Object))
if ($packageDataDiff.Count -gt 0) {
    $diffText = ($packageDataDiff | ForEach-Object { "$($_.SideIndicator) $($_.InputObject)" }) -join "; "
    throw "最终 Stage 数据集合与统一冻结清单不一致，拒绝出包: $diffText"
}
Write-Host "  + 数据($($packageDataFiles.Count) 项统一 allowlist，Stage 反向对账通过)"

# --- 5a. 引擎静态先验 StaticData.cs(地图掉落权重/价格先验; 缺它引擎红估虚高~3x) ---
# ahmad_ref_engine.STATIC_DATA = ROOT/external_references/.../Models/StaticData.cs(ROOT=包根),
# 必须镜像同一相对路径, 否则 load_reference_static_data 返回空字典 -> 幻影红过估(起手200w bug)。
$staticRel = $StaticDataSourceRelativePath
$staticSrc = Join-Path $Repo $staticRel
if (-not (Test-Path $staticSrc)) { throw "缺 $staticRel — 引擎静态先验, 不能打包" }
$StaticDataSourceSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $staticSrc -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($StaticDataSourceSHA256 -cne $ExpectedStaticDataSourceSHA256) {
    throw "StaticData.cs 与受审 release ZIP 合同身份不一致，拒绝构建。"
}
$staticDst = Join-Path $Stage $staticRel
New-Item -ItemType Directory -Force -Path (Split-Path $staticDst) | Out-Null
if (-not ($MutableStageMoatMembers -ccontains $StaticDataMoatMember)) {
    throw "StaticData.cs 不在受审 BKENC2 Stage 写入集合中，拒绝构建。"
}
Copy-BuildStageMutableLeaf `
    -StageRoot $Stage `
    -SourcePath $staticSrc `
    -DestinationPath $staticDst `
    -ExpectedSHA256 $ExpectedStaticDataSourceSHA256
Write-Host "  + 引擎静态先验(StaticData.cs)"

# --- 5c. 压缩加密核心 moat 文件(-Encrypt + 主密钥): BKENC2-gzip, 运行时内核 K 解密 ---
# 只加密【已接 data_crypt 读取点】的核心文件(掉率先验/游戏表/目录/StaticData); 运行时
# load_json/load_text 兼容 BKENC1/BKENC2；静态 moat 走 BKENC2 解密解压。次要运行时文件
# 读取点未接，暂不加密(明文)。
if ($Encrypt -and $env:BIDKING_DATA_KEY) {
    Write-Host "  加密: 核心 moat 文件(掉率先验/游戏表/目录/StaticData)..."
    $encMoat = Join-Path $Repo "scripts\encrypt_moat_files.py"
    $prevEAP2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $mlog = (
        & $BuildPython -E $encMoat `
            --stage $Stage `
            --release-contract $ReleaseZipContractPath 2>&1 |
            Out-String
    )
    $mexit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP2
    Write-Host $mlog
    if ($mexit -ne 0) { throw "moat 文件加密失败(见上): 检查 encrypt_moat_files.py / 路径" }
    Write-Host "  + 核心 moat 文件已压缩加密(BKENC2-gzip, 内核 K 解密)" -ForegroundColor Green
} elseif ($Encrypt) {
    Write-Host "  ⚠ 未提供 -MasterKeyFile: 核心 moat 文件保持【明文】(作者本机测试包形态)" -ForegroundColor Yellow
}

# --- 5b. WSS/Npcap 抓包运行时 -> vendor/ ---
$mitmdumpAsset = Join-Path $mitmproxyAsset "mitmdump.exe"
$mitmproxyInternalAsset = Join-Path $mitmproxyAsset "_internal"
$mitmproxyLicenseAsset = Join-Path $mitmproxyAsset "LICENSE.txt"
if (-not (Test-Path -LiteralPath $mitmdumpAsset -PathType Leaf) -or
    -not (Test-Path -LiteralPath $mitmproxyInternalAsset -PathType Container) -or
    -not (Test-Path -LiteralPath $mitmproxyLicenseAsset -PathType Leaf)) {
    throw "缺 WSS 本地兼容运行时 scripts/pkg_assets/mitmproxy/{mitmdump.exe,_internal,LICENSE.txt}，拒绝出包。"
}
$mitmproxyDst = Join-Path $Stage "vendor\mitmproxy"
New-Item -ItemType Directory -Force -Path $mitmproxyDst | Out-Null
Copy-Item -LiteralPath $mitmdumpAsset -Destination (Join-Path $mitmproxyDst "mitmdump.exe") -Force
Copy-Item -LiteralPath $mitmproxyLicenseAsset -Destination (Join-Path $mitmproxyDst "LICENSE.txt") -Force
robocopy $mitmproxyInternalAsset (Join-Path $mitmproxyDst "_internal") /E /XD __pycache__ /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "复制 mitmproxy WSS 运行时失败。" }
$requiredMitmFiles = @(
    "mitmdump.exe",
    "LICENSE.txt",
    "_internal\mitmproxy_rs\mitmproxy_rs.pyd",
    "_internal\mitmproxy_windows\windows-redirector.exe",
    "_internal\mitmproxy_windows\WinDivert64.sys"
)
foreach ($relativeMitmFile in $requiredMitmFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $mitmproxyDst $relativeMitmFile) -PathType Leaf)) {
        throw "WSS 本地兼容运行时缺文件: vendor/mitmproxy/$relativeMitmFile"
    }
}
$stagedMitmproxyIdentity = Invoke-ReleaseAssetGate $mitmproxyDst $mitmproxyManifestPath "staged mitmproxy runtime"
if ($stagedMitmproxyIdentity.inventory_sha256 -cne $MitmproxyInventorySHA256) {
    throw "staged mitmproxy runtime 与已验证 package input inventory 不一致，拒绝构建。"
}
$mitmproxyAddonSmokeConf = Join-Path $Stage "_build_verify_mitmproxy_conf"
$stagedWssAddon = if ($Encrypt) {
    Join-Path $scriptsDst "local_compat_addon.py"
} else {
    Join-Path $scriptsDst "mitm_bidking_wss_addon.py"
}
$previousEapMitmproxy = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $mitmproxyAddonSmokeArgs = @(
        "smoke",
        "--mitmdump", (Join-Path $mitmproxyDst "mitmdump.exe"),
        "--addon", $stagedWssAddon,
        "--confdir", $mitmproxyAddonSmokeConf,
        "--timeout", "3.0"
    )
    if ($Encrypt) {
        $mitmproxyAddonSmokeArgs += @(
            "--contract", $ClientProtectionManifestPath,
            "--package-root", $Stage,
            "--package-generation", $ClientProtectionManifestSHA256
        )
    }
    $mitmproxyAddonSmokeOutput = (
        & $BuildPython -B $wssAddonBootstrapBuilder @mitmproxyAddonSmokeArgs 2>&1 |
            Out-String
    )
    $mitmproxyAddonSmokeExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousEapMitmproxy
    if (Test-Path -LiteralPath $mitmproxyAddonSmokeConf) {
        Remove-Item -LiteralPath $mitmproxyAddonSmokeConf -Recurse -Force
    }
}
if ($mitmproxyAddonSmokeExit -ne 0) {
    throw "staged mitmproxy 无法加载跨运行时 addon，拒绝构建: $mitmproxyAddonSmokeOutput"
}
$MitmproxyBundled = -not $SplitCompatibilityRuntime
if ($SplitCompatibilityRuntime) {
    $runtimeComponentDescriptor = Join-Path $Stage "data\processed\local_compat_component.json"
    $componentBuildArgs = @(
        $runtimeComponentBuilder,
        "--source-root", $mitmproxyDst,
        "--asset-manifest", $mitmproxyManifestPath,
        "--output-dir", $Dist,
        "--descriptor-out", $runtimeComponentDescriptor
    )
    foreach ($mirrorBase in $RuntimeComponentMirrorBase) {
        $componentBuildArgs += @("--mirror-base", $mirrorBase)
    }
    $previousEapComponent = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $componentBuildOutput = (
            & $BuildPython -B @componentBuildArgs 2>&1 |
                Out-String
        )
        $componentBuildExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEapComponent
    }
    if ($componentBuildExit -ne 0) {
        throw "固定兼容运行时组件构建失败: $componentBuildOutput"
    }
    try {
        $componentBuildResult = $componentBuildOutput | ConvertFrom-Json
    } catch {
        throw "固定兼容运行时组件构建未返回有效 JSON。"
    }
    $RuntimeComponentArchive = [System.IO.Path]::GetFullPath(
        [string]$componentBuildResult.archive_path
    )
    $distFull = [System.IO.Path]::GetFullPath($Dist).TrimEnd("\") + "\"
    if (-not $RuntimeComponentArchive.StartsWith(
            $distFull,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not (Test-Path -LiteralPath $RuntimeComponentArchive -PathType Leaf)) {
        throw "固定兼容运行时组件成品路径越界或不存在。"
    }
    $RuntimeComponentArchiveName = Split-Path -Leaf $RuntimeComponentArchive
    $RuntimeComponentArchiveBytes = [long]$componentBuildResult.archive_bytes
    $RuntimeComponentArchiveSHA256 = [string]$componentBuildResult.archive_sha256
    $RuntimeComponentDescriptorSHA256 = (
        & $TrustedFileHashCommand -LiteralPath $runtimeComponentDescriptor -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ((Get-Item -LiteralPath $RuntimeComponentArchive).Length -ne
        $RuntimeComponentArchiveBytes -or
        (& $TrustedFileHashCommand -LiteralPath $RuntimeComponentArchive -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        $RuntimeComponentArchiveSHA256) {
        throw "固定兼容运行时组件成品身份与描述符不一致。"
    }
    Write-Host (
        "  + 固定兼容运行时组件: {0} ({1:N1} MB，完整 inventory + archive SHA 已验证)" -f
        $RuntimeComponentArchiveName,
        ($RuntimeComponentArchiveBytes / 1MB)
    )
} else {
    Write-Host "  + vendor/mitmproxy (v$MitmproxyVersion，provenance/inventory/真实 addon load 已验证)"
}

# Npcap remains the explicit BIDKING_DISABLE_WSS_CAPTURE=1 rollback arm for
# machines that already have it.  The WSS-default public package intentionally
# does not bundle the unused driver installer.
Write-Host "  - Npcap 安装器不随包分发（WSS 默认路线不依赖；已安装机器仍可显式回退）"

# --- 6. 启动器(BidKingLive.exe + .bat入子文件夹) + 使用说明 ---
$assets = Join-Path $Repo "scripts/pkg_assets"
$stagedLauncher = Join-Path $Stage "BidKingLive.exe"
if ($Encrypt) {
    # 加密候选不再信任可替换的 Python/PowerShell 壳。native bootstrap 先以
    # Ed25519 manifest 锁住 preload roots，之后才启动 Python；一方 native 按正式
    # unsigned distribution 合同发布，不声明 publisher/SmartScreen 身份。
    $bootstrapDir = Join-Path $Repo "native\bidking_trust_bootstrap"
    $bootstrapManifest = Join-Path $bootstrapDir "Cargo.toml"
    if (-not (Test-Path -LiteralPath $bootstrapManifest -PathType Leaf)) {
        throw "缺 native pre-Python trust bootstrap source，拒绝构建。"
    }
    $cargoCommand = Get-Command cargo.exe -CommandType Application -ErrorAction Stop
    $originalBootstrapRustFlags = [Environment]::GetEnvironmentVariable(
        "CARGO_ENCODED_RUSTFLAGS"
    )
    $originalBootstrapRustFlagsPresent = ($null -ne $originalBootstrapRustFlags)
    $originalBootstrapPublicKey = $env:BIDKING_INTEGRITY_PUBLIC_KEY_B64
    $rustFlagSeparator = [char]0x1f
    $bootstrapRustFlags = @($ControlledRustRemapFlags) + @(
        "-Ccontrol-flow-guard=checks",
        "-Clink-arg=/GUARD:CF",
        "-Clink-arg=/DYNAMICBASE",
        "-Clink-arg=/NXCOMPAT",
        "-Clink-arg=/HIGHENTROPYVA",
        "-Clink-arg=/OPT:REF",
        "-Clink-arg=/OPT:ICF"
    )
    try {
        [Environment]::SetEnvironmentVariable(
            "CARGO_ENCODED_RUSTFLAGS",
            ($bootstrapRustFlags -join $rustFlagSeparator),
            [EnvironmentVariableTarget]::Process
        )
        $env:BIDKING_INTEGRITY_PUBLIC_KEY_B64 = $LocalIntegrityPublicKeyB64
        $previousBootstrapEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $cargoCommand.Source build --locked --release --manifest-path $bootstrapManifest
        $bootstrapBuildExit = $LASTEXITCODE
        $ErrorActionPreference = $previousBootstrapEap
    } finally {
        Restore-ControlledCargoEncodedRustFlags `
            -WasPresent $originalBootstrapRustFlagsPresent `
            -PreviousValue $originalBootstrapRustFlags
        $env:BIDKING_INTEGRITY_PUBLIC_KEY_B64 = $originalBootstrapPublicKey
    }
    $bootstrapExe = Join-Path $bootstrapDir "target\release\bidking_trust_bootstrap.exe"
    if ($bootstrapBuildExit -ne 0 -or
        -not (Test-Path -LiteralPath $bootstrapExe -PathType Leaf)) {
        throw "native pre-Python trust bootstrap build failed。"
    }
    Invoke-NativeArtifactPrivacyGate `
        -ArtifactPath $bootstrapExe `
        -Label "native trust bootstrap" `
        -ForbiddenPaths $NativePrivacyForbiddenPaths
    Copy-Item -LiteralPath $bootstrapExe -Destination $stagedLauncher -Force
    Write-Host "  + BidKingLive.exe (pre-Python native trust bootstrap，unsigned distribution)"
} else {
    $exeAsset = Join-Path $assets "BidKingLive.exe"
    if (-not (Test-Path -LiteralPath $exeAsset -PathType Leaf)) {
        throw "缺 BidKingLive.exe 正式启动器，拒绝继续打包。"
    }
    Copy-Item -LiteralPath $exeAsset -Destination $stagedLauncher -Force
    Write-Host "  + BidKingLive.exe (非加密开发包固定资产)"
}
if ($Encrypt) {
    Invoke-NativeArtifactPrivacyGate `
        -ArtifactPath $stagedLauncher `
        -Label "BidKingLive launcher" `
        -ForbiddenPaths $NativePrivacyForbiddenPaths
}
$LauncherSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $stagedLauncher -Algorithm SHA256
).Hash.ToLowerInvariant()
$manualAsset = Join-Path $assets "使用说明.txt"
if (-not (Test-Path -LiteralPath $manualAsset -PathType Leaf)) {
    throw "缺使用说明.txt 正式资产，拒绝继续打包。"
} else {
    $manualDst = Join-Path $Stage "使用说明.txt"
    Copy-Item $manualAsset $manualDst -Force
    if ($IsNetworkDiagnosticCohort) {
        $productProfileTool = Join-Path $Repo "hero_ref\tools\product_profile.py"
        & $BuildPython -E -s $productProfileTool `
            --profile $ProductProfile `
            --manual-source $manualAsset `
            --manual-output $manualDst
        if ($LASTEXITCODE -ne 0) {
            throw "网络诊断内测包使用说明生成失败。"
        }
        $diagnosticManual = Get-Content -LiteralPath $manualDst -Raw -Encoding utf8
        if (
            $diagnosticManual -notmatch "网络诊断内测包" -or
            $diagnosticManual -match "90/365 天有效期内可用" -or
            $diagnosticManual -match "¥11\.11|¥28\.88|¥68\.88|¥19\.99|¥39\.99|¥89\.99"
        ) {
            throw "网络诊断内测包使用说明未正确隔离公开商业合同。"
        }
        Write-Host "  + 使用说明.txt (网络诊断内测边界已隔离)" -ForegroundColor Yellow
    }
    if ($SplitCompatibilityRuntime) {
        Add-Content -LiteralPath $manualDst -Encoding utf8 -Value @"

【精简包首次兼容设置】
首次启动会提示下载约 $([Math]::Ceiling($RuntimeComponentArchiveBytes / 1MB)) MB 的本地兼容组件。组件会先完整校验，再缓存在本机；后续版本继续使用同一固定组件时无需重复下载。若当前网络无法完成下载，请改用完整离线包。
"@
    }
}
$publicStatsAsset = Join-Path $assets "公共大数据平台.html"
if (-not (Test-Path $publicStatsAsset)) {
    throw "缺公共大数据平台入口资产: $publicStatsAsset"
}
Copy-Item $publicStatsAsset (Join-Path $Stage "公共大数据平台.html") -Force
Write-Host "  + 公共大数据平台.html (免费/付费用户均可访问)"
$licenseAsset = Join-Path $Repo "LICENSE"
if (-not (Test-Path $licenseAsset)) {
    throw "缺 LICENSE：公版包必须携带专有软件许可协议，拒绝继续打包。"
}
Copy-Item $licenseAsset (Join-Path $Stage "LICENSE.txt") -Force
Write-Host "  + LICENSE.txt (专有软件许可协议)"
# 用户双击入口集中到「诊断工具」；脚本本体仍在 scripts，诊断结果固定写入系统文档目录。
$diagnosticsDir = Join-Path $Stage "诊断工具"
New-Item -ItemType Directory -Force -Path $diagnosticsDir | Out-Null
$diagBat = $ReleaseDiagnosticInputs["诊断工具/收集诊断.bat"]
if (-not (Test-Path -LiteralPath $diagBat -PathType Leaf)) {
    throw "缺收集诊断 BAT 资产，拒绝继续打包。"
}
Copy-Item -LiteralPath $diagBat -Destination (Join-Path $diagnosticsDir "收集诊断.bat") -Force
Write-Host "  + 诊断工具/收集诊断.bat (报告写入 Windows 文档/BidKingLive/exports)"
$repairBat = $ReleaseDiagnosticInputs["诊断工具/启动修复.bat"]
if (-not (Test-Path -LiteralPath $repairBat -PathType Leaf)) {
    throw "缺启动修复 BAT 资产，拒绝继续打包。"
}
Copy-Item -LiteralPath $repairBat -Destination (Join-Path $diagnosticsDir "启动修复.bat") -Force
Write-Host "  + 诊断工具/启动修复.bat (root完整性门 + native密封repair载荷)"
$internetCafeCheckBat = Join-Path $Repo "scripts\wss_environment_preflight.bat"
if (-not (Test-Path -LiteralPath $internetCafeCheckBat -PathType Leaf)) {
    throw "缺网吧环境兼容检查 BAT 资产，拒绝继续打包。"
}
$internetCafeCheckText = [IO.File]::ReadAllText($internetCafeCheckBat, [Text.Encoding]::ASCII)
if ($internetCafeCheckText.ToCharArray() | Where-Object { [int]$_ -gt 127 }) {
    throw "网吧环境兼容检查 BAT 必须保持纯 ASCII。"
}
$internetCafeCheckText = ($internetCafeCheckText -replace "`r?`n", "`r`n").TrimEnd("`r", "`n") + "`r`n"
[IO.File]::WriteAllText(
    (Join-Path $diagnosticsDir "网吧环境兼容检查.bat"),
    $internetCafeCheckText,
    [Text.Encoding]::ASCII
)
Write-Host "  + 诊断工具/网吧环境兼容检查.bat (零系统修改的固定分类预检)"
$transportDiagPy = $ReleaseDiagnosticInputs["诊断工具/activation_transport_diagnostic.py"]
$transportDiagBat = $ReleaseDiagnosticInputs["诊断工具/激活网络诊断.bat"]
if (-not (Test-Path $transportDiagPy) -or -not (Test-Path $transportDiagBat)) {
    throw "缺激活网络诊断资产，拒绝继续打包。"
}
Copy-Item $transportDiagPy (Join-Path $diagnosticsDir "activation_transport_diagnostic.py") -Force
Copy-Item $transportDiagBat (Join-Path $diagnosticsDir "激活网络诊断.bat") -Force
Write-Host "  + 诊断工具/激活网络诊断.bat (无凭据 HTTPS 对照)"
$removeWssCa = Join-Path $Repo "scripts\remove_wss_capture_ca.ps1"
if (-not (Test-Path -LiteralPath $removeWssCa -PathType Leaf)) {
    throw "缺 WSS 证书移除脚本，拒绝出包。"
}
$advancedCleanupDir = Join-Path $diagnosticsDir "高级卸载与清理"
New-Item -ItemType Directory -Force -Path $advancedCleanupDir | Out-Null
$removeWssCaDestination = Join-Path $advancedCleanupDir "remove_wss_capture_ca.ps1"
$removeWssCaText = Get-Content -LiteralPath $removeWssCa -Raw -Encoding utf8
[IO.File]::WriteAllText(
    $removeWssCaDestination,
    $removeWssCaText,
    [Text.UTF8Encoding]::new($true)
)
$removeParseHost = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $removeParseHost -PathType Leaf)) {
    throw "缺 Windows PowerShell 5.1，无法验证证书移除入口。"
}
$removeParseCommand = @'
$tokens = $null
$errors = $null
[Management.Automation.Language.Parser]::ParseFile(
    $env:BIDKING_PS_PARSE_TARGET,
    [ref]$tokens,
    [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) { exit 1 }
'@
$previousRemoveParseTarget = $env:BIDKING_PS_PARSE_TARGET
try {
    $env:BIDKING_PS_PARSE_TARGET = $removeWssCaDestination
    & $removeParseHost -NoProfile -NonInteractive -Command $removeParseCommand
    if ($LASTEXITCODE -ne 0) {
        throw "证书移除入口无法通过 Windows PowerShell 5.1 AST 解析门。"
    }
} finally {
    if ($null -eq $previousRemoveParseTarget) {
        Remove-Item Env:\BIDKING_PS_PARSE_TARGET -ErrorAction SilentlyContinue
    } else {
        $env:BIDKING_PS_PARSE_TARGET = $previousRemoveParseTarget
    }
}
$removeWssBat = @'
@echo off
chcp 65001 >nul
title BidKing Live - 卸载本地兼容证书（高级清理）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0remove_wss_capture_ca.ps1"
echo.
pause
'@
[System.IO.File]::WriteAllText(
    (Join-Path $advancedCleanupDir "卸载本地兼容证书（高级清理）.bat"),
    (($removeWssBat -replace "`r?`n", "`r`n").TrimEnd("`r", "`n") + "`r`n"),
    [System.Text.UTF8Encoding]::new($false)
)
[System.IO.File]::WriteAllText(
    (Join-Path $advancedCleanupDir "说明.txt"),
    (
        "这里是高级卸载与清理动作，不是诊断。正常使用无需运行。" +
        " 只有在你明确要撤销 BidKing 本地 WSS 兼容证书时，才运行" +
        "《卸载本地兼容证书（高级清理）.bat》；工具只处理 exact 产品 CA 与私有 journal。" +
        " 清理成功后，下次启动需要重新完成一次产品兼容初始化。"
    ),
    [Text.UTF8Encoding]::new($true)
)
Write-Host "  + 诊断工具/高级卸载与清理/卸载本地兼容证书（高级清理）.bat"
if ($Encrypt) {
    # 所有一方 exe/pyd 已冻结；做 PE hardening/inventory/unsigned identity receipt。
    # 本阶段不接收证书、native signing policy 或 signtool，且明确拒绝嵌入式
    # Authenticode，随后 A 的 Ed25519 full-tree manifest 锁住这些 exact bytes。
    $nativeReceiptDir = Split-Path -Parent $NativeBinaryReceiptPath
    New-Item -ItemType Directory -Force -Path $nativeReceiptDir | Out-Null
    $previousNativeInspectEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $nativeInspectOutput = (
            & $BuildPython -I -B $NativeHardeningTool inspect-stage `
                --stage $Stage `
                --contract $NativeBinaryContractPath `
                --protection-manifest $ClientProtectionManifestPath `
                --receipt $NativeBinaryReceiptPath 2>&1 |
                Out-String
        )
        $nativeInspectExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousNativeInspectEap
    }
    if ($nativeInspectExit -ne 0 -or
        -not (Test-Path -LiteralPath $NativeBinaryReceiptPath -PathType Leaf)) {
        throw "一方 native binary hardening/unsigned identity门失败，拒绝写 BUILD_MANIFEST: $nativeInspectOutput"
    }
    try {
        $NativeBinaryReceipt = Get-Content -LiteralPath $NativeBinaryReceiptPath `
            -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        throw "native binary receipt 不可读取，拒绝构建。"
    }
    $NativeBinaryCount = [int]$NativeBinaryReceipt.member_count
    if ($NativeBinaryReceipt.schema -cne "bidking_native_binary_receipt_v2" -or
        $NativeBinaryReceipt.distribution -cne "unsigned" -or
        $NativeBinaryCount -ne 119) {
        throw "native binary receipt 数量或schema不一致，拒绝构建。"
    }
    $NativeBinaryReceiptSHA256 = (
        & $TrustedFileHashCommand -LiteralPath $NativeBinaryReceiptPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    Copy-Item -LiteralPath $NativeBinaryReceiptPath `
        -Destination $NativeBinaryAuditReceiptPath -Force
    $LauncherSHA256 = (
        & $TrustedFileHashCommand -LiteralPath $stagedLauncher -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $NativeBinaryVerified = $true
    Write-Host "  + 119个unsigned一方native binary已完成PE/CFG/identity冻结" -ForegroundColor Green
}
if ($Encrypt) {
    if (-not $GeneratedUploadConfigSHA256 -or -not (Test-Path $encOut)) {
        throw "最终 staged 包缺少本次构建生成的 upload_config.enc，拒绝写 BUILD_MANIFEST。"
    }
    $FinalUploadConfigSHA256 = (
        & $TrustedFileHashCommand -LiteralPath $encOut -Algorithm SHA256
    ).Hash
    if ($FinalUploadConfigSHA256 -ne $GeneratedUploadConfigSHA256) {
        throw "最终 staged upload_config.enc 已被后续复制覆盖，拒绝出包。"
    }
    $UploadConfigRoundTripVerified = $true
    Write-Host "  + 最终 upload_config.enc 与本次已回读生成物一致" -ForegroundColor Green
}
# BUILD_MANIFEST: 版本权威源。hero_ref_version.resolve_*() 优先读它 → 显示/上报版本 = 本次构建版本,
# 不再依赖"常量恰好等于 -Version"(否则版本检测会显示错版本号)。
function Write-CandidateBuildManifest {
    @(
        "PackageVersion: v$Version",
        "SourceCommit: $SourceCommit",
        "SourceBranch: $SourceBranch",
        "PackageInputsDirty: $PackageInputsDirty",
        "BuildScriptSHA256: $BuildScriptSHA256",
        "ReleaseZipContractSHA256: $ReleaseZipContractSHA256",
        "RuntimePythonSourceManifestSHA256: $RuntimePythonSourceManifestSHA256",
        "ClientProtectionSchema: $($ClientProtection.schema)",
        "ClientProtectionManifestSHA256: $ClientProtectionManifestSHA256",
        "NativeBinaryContractSHA256: $NativeBinaryContractSHA256",
        "NativeBinaryDistribution: $NativeBinaryDistribution",
        "NativeBinaryReceiptSHA256: $NativeBinaryReceiptSHA256",
        "NativeBinaryVerified: $NativeBinaryVerified",
        "NativeBinaryCount: $NativeBinaryCount",
        "WssOperationalABI: $WssOperationalABI",
        "WssOperationalGeneration: $WssOperationalGeneration",
        "WssAddonBootstrapSHA256: $WssAddonBootstrapSHA256",
        "WssAddonLoaderSHA256: $WssAddonLoaderSHA256",
        "PackageDataManifestSHA256: $PackageDataManifestSHA256",
        "PackageDataFingerprintSHA256: $PackageDataFingerprintSHA256",
        "PackageDataBaselineID: $PackageDataBaselineID",
        "RuntimeDependencyInventorySHA256: $RuntimeDependencyInventorySHA256",
        "RuntimeInventoryApprovalSHA256: $RuntimeInventoryApprovalSHA256",
        "RuntimeInventoryReceiptSHA256: $RuntimeInventoryReceiptSHA256",
        "BkencAuthenticationReceiptSHA256: $BkencAuthenticationReceiptSHA256",
        "LauncherSHA256: $LauncherSHA256",
        "StaticDataSourceSHA256: $StaticDataSourceSHA256",
        "ActivationServiceEndpointsSHA256: $ActivationServiceEndpointsSHA256",
        "ActivationServiceEndpointsGeneration: $ActivationServiceEndpointsGeneration",
        "ActivationServiceEndpoint3Enabled: $ActivationServiceEndpoint3Enabled",
        "ActivationHttpE2EEConfigSHA256: $ActivationHttpE2EEConfigSHA256",
        "ActivationHttpE2EEEnabled: $ActivationHttpE2EEEnabled",
        "ActivationHttpE2EEKeyID: $ActivationHttpE2EEKeyID",
        "LocalIntegrityPublicKeySHA256: $LocalIntegrityPublicKeySHA256",
        "LocalIntegrityManifestSHA256: $LocalIntegrityManifestSHA256",
        "LocalIntegrityEntryCount: $LocalIntegrityEntryCount",
        "LocalIntegrityVerified: $LocalIntegrityVerified",
        "Encrypted: $([bool]$Encrypt)",
        "EncryptedUploadConfig: $EncUploadConfig",
        "ReleaseKeyCacheVerified: $ReleaseKeyCacheVerified",
        "UploadConfigRoundTripVerified: $UploadConfigRoundTripVerified",
        "BundledCABundle: $BundledCABundle",
        "WssCaptureDefault: True",
        "MitmproxyBundled: $MitmproxyBundled",
        "CompatibilityRuntimeMode: $RuntimeComponentMode",
        "CompatibilityRuntimeArchive: $RuntimeComponentArchiveName",
        "CompatibilityRuntimeArchiveBytes: $RuntimeComponentArchiveBytes",
        "CompatibilityRuntimeArchiveSHA256: $RuntimeComponentArchiveSHA256",
        "CompatibilityRuntimeDescriptorSHA256: $RuntimeComponentDescriptorSHA256",
        "MitmproxyVersion: $MitmproxyVersion",
        "MitmproxySourceURL: $MitmproxySourceURL",
        "MitmproxyInstallerSHA256: $MitmproxyInstallerSHA256",
        "MitmproxyInventorySHA256: $MitmproxyInventorySHA256",
        "PetAssetsInventorySHA256: $PetAssetsInventorySHA256",
        "DiagnosticProfile: $PublicDiagnosticProfile",
        "ProductProfile: $ProductProfile",
        "CommercialSurfaceMode: $CommercialSurfaceMode",
        "PythonVersion: $PythonVersion",
        "BuildTime: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))"
    ) | Set-Content -Path (Join-Path $Stage "BUILD_MANIFEST.txt") -Encoding utf8
}
Write-CandidateBuildManifest
Write-Host "  + BUILD_MANIFEST.txt (PackageVersion v$Version, SourceCommit $SourceCommit)"
if (-not $Encrypt) {
    # standard portable 包不含高保护 .pyd；用实际 Stage 的公开启动链重新计算代际，
    # 防止 ZIP 在玩家解压后才因错误要求不存在的 native 成员而安全退出。
    $portableGenerationHelper = Join-Path $scriptsDst "package_generation_identity.py"
    $portableGenerationMonitor = Join-Path $scriptsDst "run_wss_live_monitor.py"
    $portableGenerationPython = Join-Path $RuntimePy "python.exe"
    $portableGenerationRuntime = Join-Path $RuntimePy "pythonw.exe"
    foreach ($portableGenerationInput in @(
            $stagedLauncher,
            $portableGenerationHelper,
            $portableGenerationMonitor,
            $portableGenerationPython,
            $portableGenerationRuntime,
            (Join-Path $scriptsDst "start_live_npcap_overlay.ps1"),
            (Join-Path $scriptsDst "mitm_bidking_wss_addon.py"),
            (Join-Path $scriptsDst "_local_compat_core.py")
        )) {
        if (-not (Test-Path -LiteralPath $portableGenerationInput -PathType Leaf)) {
            throw "portable package generation preflight 缺公开启动链成员。"
        }
    }
    $previousPortableGenerationEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $portableGenerationOut = (
            & $portableGenerationPython -E -B $portableGenerationHelper `
                --package-root $Stage `
                --monitor $portableGenerationMonitor `
                --runtime $portableGenerationRuntime `
                --identity-helper $portableGenerationHelper 2>&1 |
                Out-String
        )
        $portableGenerationExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPortableGenerationEap
    }
    if ($portableGenerationExit -ne 0 -or
        -not [System.Text.RegularExpressions.Regex]::IsMatch(
            $portableGenerationOut,
            '\ABIDKING_PACKAGE_GENERATION_IDENTITY=[0-9a-f]{64}(?:\r\n)?\z',
            [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
        )) {
        throw "portable package generation preflight 失败，拒绝形成会在启动前退出的候选包。"
    }
    Write-Host "  + portable package generation helper 实包预检通过"
}
if ($Encrypt) {
    # 内存 diagnostics package-root 合同要求正式 BUILD_MANIFEST 已存在。该门仍在
    # root EXE/BAT 成功门、Ed25519 full-tree 签名与 canonical ZIP 之前执行；不得
    # 为构建期 smoke 写入临时、简化或虚假的 manifest。
    $diagnosticReportsBefore = @(
        Get-ChildItem -LiteralPath $Stage -File -Filter "BidKing诊断_*.txt" -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName }
    )
    $origDiagSkipNetwork = $env:BIDKING_DIAG_SKIP_NETWORK
    $origDiagNoExplorer = $env:BIDKING_DIAG_NOEXPLORER
    $origDiagNonInteractive = $env:BIDKING_DIAG_NONINTERACTIVE
    $launcherNativeSmokeExit = 1
    $launcherNativeSmokeCleanupStatus = "not_run"
    $launcherNativeSmokeReceiptPath = Join-Path $Dist (
        ".native-diagnostic-smoke-" + [guid]::NewGuid().ToString("N") + ".json"
    )
    if (-not (Test-Path -LiteralPath $NativeDiagnosticSmokeRunner -PathType Leaf)) {
        throw "native diagnostic smoke runner 缺失，拒绝生成候选。"
    }
    if (Test-Path -LiteralPath $launcherNativeSmokeReceiptPath) {
        throw "native diagnostic smoke receipt 路径已存在，拒绝生成候选。"
    }
    $launcherNativeSmokeOk = $false
    Push-Location $wssSrc
    try {
            $env:BIDKING_DIAG_SKIP_NETWORK = "1"
            $env:BIDKING_DIAG_NOEXPLORER = "1"
            $env:BIDKING_DIAG_NONINTERACTIVE = "1"
            $nativeSmokeEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & $launcherSmokePython -E -s -B $NativeDiagnosticSmokeRunner `
                    run $launcherNativeSmokeReceiptPath $lifecycleGeneration *> $null
                $launcherNativeSmokeExit = $LASTEXITCODE
                if ($launcherNativeSmokeExit -eq 0) {
                    $nativeSmokeLease = Invoke-NativeDiagnosticSmokeReceiptLease `
                        -PythonPath $launcherSmokePython `
                        -RunnerPath $NativeDiagnosticSmokeRunner `
                        -ReceiptPath $launcherNativeSmokeReceiptPath
                    $launcherNativeSmokeCleanupStatus = [string]$nativeSmokeLease.Status
                }
            } finally {
                $ErrorActionPreference = $nativeSmokeEap
            }
        } finally {
            if ($null -eq $origDiagSkipNetwork) {
                Remove-Item Env:\BIDKING_DIAG_SKIP_NETWORK -ErrorAction SilentlyContinue
            } else {
                $env:BIDKING_DIAG_SKIP_NETWORK = $origDiagSkipNetwork
            }
            if ($null -eq $origDiagNoExplorer) {
                Remove-Item Env:\BIDKING_DIAG_NOEXPLORER -ErrorAction SilentlyContinue
            } else {
                $env:BIDKING_DIAG_NOEXPLORER = $origDiagNoExplorer
            }
            if ($null -eq $origDiagNonInteractive) {
                Remove-Item Env:\BIDKING_DIAG_NONINTERACTIVE -ErrorAction SilentlyContinue
            } else {
                $env:BIDKING_DIAG_NONINTERACTIVE = $origDiagNonInteractive
            }
            Pop-Location
    }
    $launcherNativeSmokeOk = (
        $launcherNativeSmokeExit -eq 0 -and
        $launcherNativeSmokeCleanupStatus -ceq "deleted"
    )
    $diagnosticReportsAfter = @(
        Get-ChildItem -LiteralPath $Stage -File -Filter "BidKing诊断_*.txt" -ErrorAction SilentlyContinue
    )
    foreach ($diagnosticReport in $diagnosticReportsAfter) {
        if ($diagnosticReport.FullName -notin $diagnosticReportsBefore) {
            Remove-Item -LiteralPath $diagnosticReport.FullName -Force
        }
    }
    if (-not $launcherNativeSmokeOk) {
        if ($launcherNativeSmokeExit -eq 0 -and
            $launcherNativeSmokeCleanupStatus -ceq "cleanup_conflict") {
            throw "native diagnostic smoke receipt cleanup_conflict，拒绝生成候选。"
        }
        throw "native lifecycle 合同或公版诊断入口实跑失败，拒绝生成候选。"
    }
    Write-Host "  + native lifecycle 合同与公版诊断入口实跑通过"
}
$utf8Bom = New-Object System.Text.UTF8Encoding $true
if ($IsNetworkDiagnosticCohort) {
    # 定向诊断候选不携带付款码或购买说明；只生成可审计、无价格/新权益承诺的边界文件。
    $cohortDir = Join-Path $Stage "网络诊断内测说明"
    $cohortNote = Join-Path $cohortDir "内测边界.txt"
    New-Item -ItemType Directory -Force -Path $cohortDir | Out-Null
    $productProfileTool = Join-Path $Repo "hero_ref\tools\product_profile.py"
    & $BuildPython -E -s $productProfileTool `
        --profile $ProductProfile `
        --manual-source $manualAsset `
        --manual-output $manualDst `
        --cohort-note-output $cohortNote
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $cohortNote -PathType Leaf)) {
        throw "网络诊断内测边界文件生成失败。"
    }
    if (Test-Path -LiteralPath (Join-Path $Stage "购买与续费")) {
        throw "网络诊断内测包不得包含购买与续费目录。"
    }
    Write-Host "  + 网络诊断内测说明/内测边界.txt (无付款码/购买入口)" -ForegroundColor Yellow
} else {
    # 标准包提供店铺入口并保留人工扫码购买；缺任一必需购买资产即拒绝出包。
    $purchaseDir = Join-Path $Stage "购买与续费"
    New-Item -ItemType Directory -Force -Path $purchaseDir | Out-Null
    $shopQrAsset = Join-Path $Repo "hero_ref/assets/shop_qr.png"
    $shopHtmlAsset = Join-Path $Repo "scripts/pkg_assets/打开店铺.html"
    if (-not (Test-Path -LiteralPath $shopQrAsset -PathType Leaf)) { throw "缺店铺二维码资产: $shopQrAsset" }
    if (-not (Test-Path -LiteralPath $shopHtmlAsset -PathType Leaf)) { throw "缺店铺入口页面: $shopHtmlAsset" }
    Copy-Item -LiteralPath $shopQrAsset -Destination (Join-Path $purchaseDir "店铺二维码.png") -Force
    Copy-Item -LiteralPath $shopHtmlAsset -Destination (Join-Path $purchaseDir "打开店铺.html") -Force
    $alipayQrAsset = Join-Path $Repo "hero_ref/assets/support_qr.png"
    $wechatQrAsset = Join-Path $Repo "hero_ref/assets/support_qr_wechat.jpg"
    if (-not (Test-Path $alipayQrAsset)) { throw "缺支付宝付款码资产: $alipayQrAsset" }
    if (-not (Test-Path $wechatQrAsset)) { throw "缺微信付款码资产: $wechatQrAsset" }
    Copy-Item $alipayQrAsset (Join-Path $purchaseDir "支付宝付款码.png") -Force
    Copy-Item $wechatQrAsset (Join-Path $purchaseDir "微信付款码.jpg") -Force
    Write-Host "  + 购买与续费/支付宝付款码.png + 微信付款码.jpg"
    $catN = 0
    foreach ($cat in @("cat_mascot.jpg", "cat_mascot2.jpg")) {
        $catSrc = Join-Path $Repo "hero_ref/assets/$cat"
        if (Test-Path $catSrc) { $catN++; Copy-Item $catSrc (Join-Path $purchaseDir "猫猫$catN.jpg") -Force }
    }
    $purchaseText = @'
BidKing Live 店铺与人工购买 / 续费说明
================================

一、店铺入口

双击同目录「打开店铺.html」，或扫描「店铺二维码.png」进入店铺：
https://www.kufaka.com/shop/GGTO42L9
店铺二维码用于打开店铺，不是支付宝或微信付款码。
商品、发放及续费方式请以店铺实际商品说明为准；不要把下方人工续费规则直接套用于店铺新卡。

二、人工扫码购买 / 续费（保留原有方式）

当前采用人工核对发码：扫码付款不会自动生成 token。

1. 先选择档位：
   绑定设备：30 天 ¥15.88 ｜ 90 天 ¥35.88 ｜ 365 天 ¥88.88
   可换绑：30 天 ¥24.88 ｜ 90 天 ¥48.88 ｜ 365 天 ¥118.88（换设备时重新绑定）
   漫游卡最多换绑 8 次；首次绑定不计次，同卡续签/重签不重置次数；不追溯补计历史换绑。
   一次性购买，不自动扣款；续费、换档或衔接剩余时间，请先联系作者核对。
   店铺新卡不承诺自动叠加已有时间；既有人工续费承诺按原约定处理。

2. 选择支付方式：
   支付宝扫描同目录「支付宝付款码.png」
   微信扫描同目录「微信付款码.jpg」

3. 付款后把「付款截图 + 想要的档位」发给作者；人工核对后会回复 token。
   点悬浮窗「点此输入/购买 token」→ 粘贴 token → 点「激活」，无需重启。

联系：GitHub Issues ｜ project discussion
请勿在群里公开完整 token；金额或档位不确定时，请先联系作者再付款。

--------------------------------

BidKing Live 会按原有五位专项英雄——艾莎、艾哈迈德、加布里埃拉、伊森、拉文——的不同技能与揭示方式，
实时合并件数、格数、均价、技能/道具和小地图证据，给出保守 / 参考 / 激进三档报价。
玛丽亚、伊万另有专属技能证据与显示；其余已支持英雄仍按现有通用/轮次合同处理，
不代表所有英雄均已完成同等级专项校准或实战验证。
主面板、详情、迷你卡与 hover 共用同一结果，hover 可查看候选、概率和证据来源。

本工具为付费激活版，标准新设备试用 12 小时；具体发放与剩余时间以悬浮窗显示的
服务器权威额度为准。

所有档位均可正常使用计算器。
猫猫：免费 10 分钟 ｜ 30 天 12 小时 ｜ 90/365 天有效期内可用。
30 天猫猫只在显示时扣减配额，不是每天 12 小时；30 天不含「我的记录」及完整私有大数据。
90/365 天档在订阅有效期内可用「我的记录」。
90 天不含完整私有大数据。
365 天档和历史永久版另含完整私有大数据和高级个性化。
会员按自然时间计时；90/365 天若先用本卡登录会员数据页，也会开始计时。
音效、动画与互动成长将逐步扩充。
历史永久版保留既有权益，但永久档已不再新售。
以上均为限期订阅，365 天为公版最高档。

公版对局结算默认上传裁剪后的数据，不上传原始抓包、网络 IP 或明文 token。
公共大数据：https://api.bidking.example.invalid/public
（「猫猫1/2.jpg」是作者家的猫、卖个萌。）
'@
    [System.IO.File]::WriteAllText((Join-Path $purchaseDir "扫码购买说明.txt"), $purchaseText, $utf8Bom)
    Write-Host "  + 购买与续费/扫码购买说明.txt + 猫猫1/2.jpg"
}
$subDir = Join-Path $Stage "其他启动方式"
New-Item -ItemType Directory -Force -Path $subDir | Out-Null
$bat = (
    "@echo off`r`n" +
    "setlocal`r`n" +
    'if not exist "%~dp0..\BidKingLive.exe" exit /b 1' + "`r`n" +
    'if exist "%~dp0..\BidKingLive.exe\*" exit /b 1' + "`r`n" +
    'start "" "%~dp0..\BidKingLive.exe"' + "`r`n" +
    "exit /b %errorlevel%`r`n"
)
$batBytes = [System.Text.Encoding]::ASCII.GetBytes($bat)
$batSHA256 = [System.BitConverter]::ToString(
    [System.Security.Cryptography.SHA256]::Create().ComputeHash($batBytes)
).Replace("-", "").ToLowerInvariant()
if ($batSHA256 -cne [string]$ReleaseZipContract.launcher_wrappers.sha256) {
    throw "BAT 薄包装字节与 release contract 不一致，拒绝构建。"
}
foreach ($batName in @("启动.bat", "start.bat")) {
    [System.IO.File]::WriteAllBytes((Join-Path $subDir $batName), $batBytes)
}
[System.IO.File]::WriteAllText(
    (Join-Path $subDir "说明.txt"),
    (
        "这两个 .bat 只异步启动根目录 BidKingLive.exe，产品入口和 UAC authority 与直接双击根 EXE 相同；它们在启动请求发出后立即关闭命令窗口，不等待产品退出，也不承诺返回产品退出码。取消 UAC 时产品不会启动；需要最直接的启动/错误反馈时请双击根目录 BidKingLive.exe。首次使用请完整解压，把整个文件夹加入火绒/360信任区/白名单；能进游戏但不计算时，完整退出火绒、360、游戏加速器（仅关闭主窗口可能仍有后台）。Steam 可以保持开启，但 BidKing.exe 必须完全退出；先启动 Hero Ref，等悬浮窗出现，再启动游戏；已经登录、位于大厅或对局时不要中途打开或重启计算器，否则本局可能无法正常读取。不要卸载或永久关闭安全软件；仍失败请收集诊断私发，不要反复更换 token。顶部 ⟳ 只刷新当前界面，不会重启后台读取；游戏运行中关闭悬浮窗不会中断当前连接，游戏退出后后台读取会自动结束。" +
        $(if ($SplitCompatibilityRuntime) {
            " 精简包首次启动还会下载、校验并缓存本地兼容组件；下载失败时请检查网络/磁盘，或改用完整离线包。"
        } else { "" })
    ),
    $utf8Bom
)
Write-Host "  + BidKingLive.exe + 使用说明.txt + 公共大数据平台.html + 其他启动方式/(.bat)"

# --- 6b. 隔离导入验证(模拟玩家干净机器: -E 忽略环境变量, -s 屏蔽用户级 site) ---
# 这是杜绝"依赖静默拷空"的兜底: 在开发机上内嵌 python 会从用户级 site 偷到依赖而
# 假装正常, 唯有 -E -s 隔离后才暴露包里到底缺没缺。导入整条 monitor 依赖链, 任一缺失即 throw。
$verifyPy = Join-Path $RuntimePy "python.exe"
$localIntegrityGateCode = @'
import importlib.util
import pathlib
import sys
_MODULE_MISSING = object()
def _load_exact(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("bootstrap_exact_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name, _MODULE_MISSING)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is _MODULE_MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module
try:
    _root = pathlib.Path(sys.argv.pop(1)).resolve(strict=True)
    _candidates = tuple((_root / "src").glob("bidking_kernel*.pyd"))
    if len(_candidates) != 1 or not _candidates[0].is_file() or _candidates[0].is_symlink():
        raise SystemExit(6)
    _kernel = _load_exact("bidking_kernel", _candidates[0])
    _ok, _code, _count, _manifest_id = _kernel.verify_local_integrity(str(_root))
    print("BIDKING_LOCAL_INTEGRITY=%s:%d:%s" % (_code, _count, _manifest_id))
    if not _ok:
        raise SystemExit(6)
except SystemExit:
    raise
except BaseException:
    print("BIDKING_LOCAL_INTEGRITY=bootstrap_failed")
    raise SystemExit(6)
'@
function Invoke-LocalIntegrityGate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Label,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$PythonPath,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$PackageRoot
    )
    $previousIntegrityGateEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $integrityGateOut = (
            & $PythonPath -E -s -S -B -c `
                $localIntegrityGateEntry $localIntegrityGatePayload $PackageRoot 2>&1 |
                Out-String
        )
        $integrityGateExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousIntegrityGateEap
    }
    $integrityGateMarkerPattern = (
        '\ABIDKING_LOCAL_INTEGRITY=ok:' +
        '[1-9][0-9]*:[0-9a-f]{16}(?:\r\n)?\z'
    )
    if ($integrityGateExit -ne 0 -or
        -not [System.Text.RegularExpressions.Regex]::IsMatch(
            $integrityGateOut,
            $integrityGateMarkerPattern,
            [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
        )) {
        throw "$Label 本地完整性 native gate 失败: $integrityGateOut"
    }
    Write-Host "  + $Label 本地完整性 native gate 通过" -ForegroundColor Green
}
$localIntegrityGateBytes = [System.Text.Encoding]::UTF8.GetBytes(
    $localIntegrityGateCode
)
$localIntegrityGatePayload = [Convert]::ToBase64String($localIntegrityGateBytes)
$localIntegrityGateHasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $localIntegrityGatePayloadSHA256 = (
        [System.BitConverter]::ToString(
            $localIntegrityGateHasher.ComputeHash($localIntegrityGateBytes)
        )
    ).Replace("-", "").ToLowerInvariant()
} finally {
    $localIntegrityGateHasher.Dispose()
}
$localIntegrityGateEntry = "import base64,hashlib,sys;(len(sys.argv)==3 and sys.argv[0]=='-c' and bool(sys.argv[1]) and bool(sys.argv[2])) or sys.exit(6);_raw=base64.b64decode(sys.argv[1],validate=True);(_raw and hashlib.sha256(_raw).hexdigest()=='$localIntegrityGatePayloadSHA256') or sys.exit(6);sys.argv.pop(1);exec(compile(_raw,'<bidking-local-integrity-gate>','exec'))"
# 含激活链(0.2.2 真强制): cryptography→data_crypt→client_activate→enforce 任一缺依赖即包内激活恒失败
# → 产品锁死, 必须在打包验证就暴露(此前漏检 cryptography 即因验证没导入激活链)。
$verifyCode = "import certifi, numpy, psutil, pydantic, pydantic_core, ssl, tkinter; from PIL import Image; from pathlib import Path; from cryptography.hazmat.primitives.ciphers.aead import AESGCM; from bidking_lab.http_tls import verified_ssl_context; from bidking_lab.live.capture import NpcapBackend, build_bpf_filter; from bidking_lab.live.tcp_reassembly import TcpSeqReassembler; from bidking_lab.secure import client_activate, enforce; from pet_companion import pet_assets_available; assert Path(certifi.where()).is_file(); assert pet_assets_available(); assert TcpSeqReassembler().feed(('a', 1, 'b', 2), 0, b'x') == b'x'; ctx=verified_ssl_context(); assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname; assert client_activate._result_from_cache(None, device='package-smoke', ingest_url=None, now=0) is None; print('IMPORT_OK')"
if ($Encrypt) {
    $verifyCode = "import _client_launcher, _local_compat_core, _local_compat_monitor, _runtime_component, _protected_ahmad_tk_overlay, ahmad_live_panel_server, detail_composition_view, hero_ref_live_schedule, network_connection_status, pet_access, pet_companion, product_profile, replay_vs_truth; import bidking_lab.inference.v2, bidking_lab.inference.v3.posterior, bidking_lab.live.fatbeans, bidking_lab.runtime.snapshot; assert all(str(m.__file__).lower().endswith('.pyd') for m in (_protected_ahmad_tk_overlay, ahmad_live_panel_server, detail_composition_view, hero_ref_live_schedule, network_connection_status, pet_access, pet_companion, product_profile, replay_vs_truth, bidking_lab.inference.v2, bidking_lab.inference.v3.posterior, bidking_lab.live.fatbeans, bidking_lab.runtime.snapshot)); assert _protected_ahmad_tk_overlay._flag_color('risk').startswith('#') and _protected_ahmad_tk_overlay._flag_color('watch').startswith('#'); " + $verifyCode
}
# EAP=Stop 会把 python 写 stderr 当致命错误(在判断前就抛), 验证期间临时切 Continue。
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$verifyOut = (& $verifyPy -E -s -B -c $verifyCode 2>&1 | Out-String)
$verifyExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($verifyExit -ne 0 -or $verifyOut -notmatch "IMPORT_OK") {
    Write-Host $verifyOut -ForegroundColor Red
    throw "打包验证失败: 内嵌 python 隔离导入依赖链出错(包内缺依赖, 玩家干净机器会崩)。"
}
Write-Host "  + 隔离导入验证通过 (numpy/psutil/pydantic/tkinter + bidking_lab 依赖链)"

# --- 6c. synthetic ABI smoke + 真实 release availability gate ---
# 【防复发闸】0.2.3-hotfix3 的 inference/.pyd 因 Cython `局部: dict=defaultdict` 严检【每帧炸】
# build_monitor_artifact → 群友无估价/卡上一局/结算异常, 根因是打包只有【导入】验证(6b 不执行函数体)。
# 本 gate 用【包内 .pyd + 包内数据(BIDKING_PROJECT_ROOT=$Stage)】真跑仓库脱敏样本, 运行时报错即拒绝出包。
# 样本(已去标识, 在仓库)。用 -s(包内 site)但不用 -E(需 env)。多 fixture 覆盖【老+新结算格式】:
#   5604 = 56xx 老结算帧(f3 顶层合并路径); 2508 = 24/25xx 新版图结算帧尾部 desync 恢复(3a63e60)+ 沉船族。
# 当前76b7ca/112物理图模型已让这两个原pool complete/sampleable；这只是历史fixture
# 兼容ABI回放，不是新代实战或官方Drop/服务器开放证据。不得做synthetic rebind。
# 另用 actual unauthorized root 1066 / leaf 1106013 保留独立 fail-closed 负控。
$gateFixtures = @(
    [PSCustomObject]@{
        Path = (Join-Path $Repo "tests\fixtures\live\settlement_5604_f3_split_npcap_live.jsonl")
        MapId = 5604; OriginalPoolId = 2704; ExpectedStatus = "sampleable"
        ExpectedSupportStatus = ""; SupportUnresolvedProbabilityPpm = 0
        PackageGeneration = "76b7ca-2026-10-08-723"; ModelScopeCount = 112
        Hero = "ahmed"; InventoryCount = 33; SettlementIncomplete = $false
    },
    [PSCustomObject]@{
        Path = (Join-Path $Repo "tests\fixtures\live\settlement_2508_tail_desync_npcap_live.jsonl")
        MapId = 2508; OriginalPoolId = 2508; ExpectedStatus = "sampleable"
        ExpectedSupportStatus = ""; SupportUnresolvedProbabilityPpm = 0
        PackageGeneration = "76b7ca-2026-10-08-723"; ModelScopeCount = 112
        Hero = "ahmed"; InventoryCount = 47; SettlementIncomplete = $true
    }
)
function New-RuntimeFixtureGateCode {
    param(
        [Parameter(Mandatory = $true)] [object]$GateSpec,
        [Parameter(Mandatory = $true)] [string]$GateFixture
    )
    $requiredProperties = @(
        "MapId",
        "OriginalPoolId",
        "ExpectedStatus",
        "ExpectedSupportStatus",
        "SupportUnresolvedProbabilityPpm",
        "PackageGeneration", "ModelScopeCount", "Hero", "InventoryCount", "SettlementIncomplete"
    )
    $properties = @{}
    foreach ($propertyName in $requiredProperties) {
        $property = $GateSpec.PSObject.Properties[$propertyName]
        if ($null -eq $property) {
            throw "运行时 gate fixture 当前权威合同缺失: $propertyName"
        }
        $properties[$propertyName] = $property.Value
    }
    if (
        $properties["MapId"] -isnot [int] -or
        $properties["OriginalPoolId"] -isnot [int] -or
        $properties["ExpectedStatus"] -isnot [string] -or
        $properties["ExpectedSupportStatus"] -isnot [string] -or
        $properties["SupportUnresolvedProbabilityPpm"] -isnot [int] -or
        $properties["PackageGeneration"] -isnot [string] -or
        $properties["ModelScopeCount"] -isnot [int] -or
        $properties["Hero"] -isnot [string] -or
        $properties["InventoryCount"] -isnot [int] -or
        $properties["SettlementIncomplete"] -isnot [bool]
    ) {
        throw "运行时 gate fixture 当前权威合同类型无效"
    }
    $approvedGateSpecs = @{
        5604 = [PSCustomObject]@{
            OriginalPoolId = 2704
            ExpectedStatus = "sampleable"
            ExpectedSupportStatus = ""
            SupportUnresolvedProbabilityPpm = 0
            PackageGeneration = "76b7ca-2026-10-08-723"; ModelScopeCount = 112
            Hero = "ahmed"; InventoryCount = 33; SettlementIncomplete = $false
        }
        2508 = [PSCustomObject]@{
            OriginalPoolId = 2508
            ExpectedStatus = "sampleable"
            ExpectedSupportStatus = ""; SupportUnresolvedProbabilityPpm = 0
            PackageGeneration = "76b7ca-2026-10-08-723"; ModelScopeCount = 112
            Hero = "ahmed"; InventoryCount = 47; SettlementIncomplete = $true
        }
    }
    $mapId = [int]$properties["MapId"]
    if (-not $approvedGateSpecs.ContainsKey($mapId)) {
        throw "运行时 gate fixture 当前权威状态未批准: $mapId"
    }
    $approvedSpec = $approvedGateSpecs[$mapId]
    if (
        [int]$properties["OriginalPoolId"] -ne [int]$approvedSpec.OriginalPoolId -or
        [string]$properties["ExpectedStatus"] -cne [string]$approvedSpec.ExpectedStatus -or
        [string]$properties["ExpectedSupportStatus"] -cne [string]$approvedSpec.ExpectedSupportStatus -or
        [int]$properties["SupportUnresolvedProbabilityPpm"] -ne [int]$approvedSpec.SupportUnresolvedProbabilityPpm -or
        [string]$properties["PackageGeneration"] -cne [string]$approvedSpec.PackageGeneration -or
        [int]$properties["ModelScopeCount"] -ne [int]$approvedSpec.ModelScopeCount -or
        [string]$properties["Hero"] -cne [string]$approvedSpec.Hero -or
        [int]$properties["InventoryCount"] -ne [int]$approvedSpec.InventoryCount -or
        [bool]$properties["SettlementIncomplete"] -ne [bool]$approvedSpec.SettlementIncomplete
    ) {
        throw "运行时 gate fixture 当前权威合同未批准: $mapId"
    }
    return @"
import hashlib, json, os, tempfile
from dataclasses import replace
from pathlib import Path
import ahmad_live_panel_server
from bidking_lab.live.fatbeans import parse_fatbeans_capture_payload
from bidking_lab.live.monitor import load_monitor_tables, build_monitor_artifact_from_payload
from bidking_lab.simulation.basic_mc import DropPoolResolutionError, flatten_pool
raw = open(r'$GateFixture', encoding='utf-8', errors='replace').read()
try:
    json.loads(raw); payload = raw
except Exception:
    payload = '[' + ','.join(l for l in raw.splitlines() if l.strip()) + ']'
expected_map_id = $mapId
expected_original_pool_id = $([int]$approvedSpec.OriginalPoolId)
expected_status = '$([string]$approvedSpec.ExpectedStatus)'
expected_support_status = '$([string]$approvedSpec.ExpectedSupportStatus)'
expected_support_unresolved_probability_ppm = $([int]$approvedSpec.SupportUnresolvedProbabilityPpm)
expected_generation = '$([string]$approvedSpec.PackageGeneration)'
expected_scope_count = $([int]$approvedSpec.ModelScopeCount)
expected_hero = '$($approvedSpec.Hero)'
expected_inventory_count = $($approvedSpec.InventoryCount)
expected_settlement_incomplete = $([bool]$approvedSpec.SettlementIncomplete)
def require_current_fixture_authority(tables, map_id, pool_id, generation, scope_count):
    assert generation == '76b7ca-2026-10-08-723' and type(scope_count) is int and scope_count == 112
    receipt = tables.rankmap_distribution_receipt
    assert tables.rankmap_distribution_status == 'ok' and isinstance(receipt, dict)
    assert receipt.get('status') == 'current_rankmap_algorithm_prior'
    assert receipt.get('package_generation') == generation
    assert receipt.get('authority_mode') == 'rankmap_drop_model_algorithm_prior'
    assert type(receipt.get('scope_map_count')) is int and receipt['scope_map_count'] == scope_count
    assert receipt.get('scope') == 'physical_map_conditioned_ordinary_and_ranked'
    assert receipt.get('model_distribution_authority') is True
    assert receipt.get('official_distribution_authority') is False
    assert receipt.get('runtime_open_claim') is False
    assert receipt.get('drop_or_dropmap_textasset') is False
    assert receipt.get('claims_item_level_drop_graph') is False
    assert receipt.get('drop_or_dropmap_absence_is_not_a_blocker') is True
    assert tables.drops.package_generation == generation
    assert tables.drops.authority_mode == 'rankmap_drop_model_algorithm_prior'
    ids = tables.drops.bound_map_ids
    assert len(ids) == scope_count and all(type(mid) is int for mid in ids)
    assert hashlib.sha256(json.dumps(sorted(ids), separators=(',', ':')).encode('ascii')).hexdigest() == '78bab49717c2a3884ea49e8452cc779544ce4ee8bad5dce49e513e5bbfb4c99d'
    assert map_id in ids and tables.maps[map_id].drop_pool_id == pool_id
    flattened = flatten_pool(pool_id, tables.drops, tables.items, map_id=map_id)
    assert flattened.item_ids and flattened.resolution_status == 'complete'
    assert flattened.resolution_policy == 'rankmap_drop_model_algorithm_prior'
    assert flattened.resolution_generation == generation
# These are frozen historical captures.  Do not let the developer machine's
# currently installed game generation replace their packaged-table identity.
tables = replace(
    load_monitor_tables(),
    warehouse_live_identity_check=False,
)
require_current_fixture_authority(tables, expected_map_id, expected_original_pool_id, expected_generation, expected_scope_count)
assert expected_status == 'sampleable' and expected_support_status == '' and expected_support_unresolved_probability_ppm == 0
parsed = parse_fatbeans_capture_payload(payload)
settled = next(state for state in reversed(parsed.states) if state.message_id == 0x002D)
fixture_map_id = settled.map_id
if fixture_map_id != expected_map_id:
    raise AssertionError('runtime fixture map identity mismatch')
assert tables.maps[expected_map_id].drop_pool_id == expected_original_pool_id
assert len(settled.inventory_items) == expected_inventory_count
if expected_map_id == 2508:
    recovered_ids = {item.item_id for item in settled.inventory_items}
    assert {1086008, 1096009}.issubset(recovered_ids)
art = build_monitor_artifact_from_payload(
    payload, file='gate-current-authority', tables=tables, n_trials=40, roi_trials=20,
)
assert art.get('map_id') == expected_map_id
assert art.get('model_map_id') == expected_map_id
assert art.get('map_alias_mode') == ''
assert art.get('hero') == expected_hero
assert art.get('phase') == 'settled'
assert art.get('inventory_count') == expected_inventory_count
assert art.get('settlement_incomplete') is expected_settlement_incomplete
assert isinstance(art.get('final_decision_value'), int) and art['final_decision_value'] > 0
support = art.get('drop_pool_support')
assert support is None
try:
    flatten_pool(1066, tables.drops, tables.items)
except DropPoolResolutionError as exc:
    assert exc.reason == 'unknown_item'
    assert exc.pool_id == 1066
    assert exc.item_id == 1106013
else:
    raise AssertionError('unauthorized root and leaf did not fail closed')
with tempfile.TemporaryDirectory(prefix='bidking-runtime-gate-') as temp_dir:
    snapshot_path = Path(temp_dir) / 'latest_snapshot.json'
    snapshot_path.write_text(json.dumps(art), encoding='utf-8')
    summary = ahmad_live_panel_server.summarize_snapshot(art, snapshot_path=snapshot_path)
assert summary.get('drop_pool_support') == support
assert summary.get('diagnostics', {}).get('drop_pool_support') == support
print('CURRENT_AUTHORITY_ABI_SMOKE_OK' if isinstance(summary, dict) and summary else 'CURRENT_AUTHORITY_ABI_SMOKE_EMPTY')
"@
}
$prevEAP2 = $ErrorActionPreference; $ErrorActionPreference = "Continue"
foreach ($gateSpec in $gateFixtures) {
    $gateFixture = [string]$gateSpec.Path
    if (-not (Test-Path -LiteralPath $gateFixture -PathType Leaf)) {
        $ErrorActionPreference = $prevEAP2
        throw "运行时 gate 样本缺失: $gateFixture"
    }
    $gateCode = New-RuntimeFixtureGateCode -GateSpec $gateSpec -GateFixture $gateFixture
    $env:BIDKING_PROJECT_ROOT = $Stage
    $gateOut = (& $verifyPy -s -B -c $gateCode 2>&1 | Out-String)
    $gateExit = $LASTEXITCODE
    Remove-Item Env:\BIDKING_PROJECT_ROOT -ErrorAction SilentlyContinue
    if ($gateExit -ne 0 -or $gateOut -notmatch "CURRENT_AUTHORITY_ABI_SMOKE_OK") {
        Write-Host $gateOut -ForegroundColor Red
        $ErrorActionPreference = $prevEAP2
        throw "current-authority ABI smoke 失败 ($([System.IO.Path]::GetFileName($gateFixture))): .pyd 真跑 build_monitor_artifact 出错。"
    }
    Write-Host "  + current-authority ABI smoke 通过: $([System.IO.Path]::GetFileName($gateFixture))（仅ABI，不代表发布可用）"
}

# Aisha 2408 R4 (2026-08-06) exposed a Cython-only closure type mismatch:
# source Python produced quotes, but the encrypted engine raised
# ``source_notes expected tuple, got list`` once the multi-count q5 guard ran.
# The settlement fixtures above do not enter that bidding-only branch, so execute
# this small frozen pre-settlement state against the already-built Stage .pyd.
$aishaPydGateFixture = Join-Path $Repo "tests\fixtures\live\aisha_2408_multicount_q5_pyd_gate.json"
if (-not (Test-Path -LiteralPath $aishaPydGateFixture -PathType Leaf)) {
    $ErrorActionPreference = $prevEAP2
    throw "Aisha .pyd runtime gate fixture missing: $aishaPydGateFixture"
}
$aishaPydGateCode = @"
import json
from pathlib import Path
import ahmad_live_panel_server as panel
from ahmad_ref_engine import run_reference_engine
snapshot = json.loads(Path(r'$aishaPydGateFixture').read_text(encoding='utf-8'))
prepared = panel._aisha_prepare_pass_snapshot(
    snapshot,
    pass_kind=panel.AISHA_ENGINE_PASS_ITEM,
    round_no=4,
)
result = run_reference_engine(
    prepared,
    max_combos=panel._aisha_pass_max_combos(4, prepared),
)
valid = (
    result.status in {'ok', 'count_prior'}
    and result.combo_count > 0
    and 0 < result.conservative <= result.balanced <= result.aggressive
)
print('AISHA_PYD_GATE_OK' if valid else 'AISHA_PYD_GATE_EMPTY')
"@
$env:BIDKING_PROJECT_ROOT = $Stage
$aishaPydGateOut = (& $verifyPy -s -B -c $aishaPydGateCode 2>&1 | Out-String)
$aishaPydGateExit = $LASTEXITCODE
Remove-Item Env:\BIDKING_PROJECT_ROOT -ErrorAction SilentlyContinue
if ($aishaPydGateExit -ne 0 -or $aishaPydGateOut -notmatch "AISHA_PYD_GATE_OK") {
    Write-Host $aishaPydGateOut -ForegroundColor Red
    $ErrorActionPreference = $prevEAP2
    throw "Aisha .pyd runtime gate failed: encrypted engine cannot produce an R4 quote."
}
Write-Host "  + Aisha .pyd runtime gate 通过: 2408 R4 multicount-q5 branch"

# Manual input deliberately has no numeric round: the UI publishes ``round=手动``
# and the engine normalizes it to None.  Cython treats a plain ``round_no: int``
# helper parameter as a native boundary and would reject the value before the
# helper's non-R5 no-op guard can run.  Exercise the real Stage .pyd with the
# frozen product-shaped manual snapshot so source-only success cannot ship again.
$aishaManualPydGateFixture = Join-Path $Repo "tests\fixtures\live\aisha_manual_round_none_pyd_gate.json"
if (-not (Test-Path -LiteralPath $aishaManualPydGateFixture -PathType Leaf)) {
    $ErrorActionPreference = $prevEAP2
    throw "Aisha manual .pyd runtime gate fixture missing: $aishaManualPydGateFixture"
}
$aishaManualPydGateCode = @"
import json
from pathlib import Path
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine
snapshot = json.loads(Path(r'$aishaManualPydGateFixture').read_text(encoding='utf-8'))
result = run_reference_engine(
    prepare_reference_engine_snapshot(snapshot),
    max_combos=2500,
)
valid = (
    result.status in {'ok', 'count_prior'}
    and result.combo_count > 0
    and 0 < result.conservative <= result.balanced <= result.aggressive
)
print('AISHA_MANUAL_PYD_GATE_OK' if valid else 'AISHA_MANUAL_PYD_GATE_EMPTY')
"@
$env:BIDKING_PROJECT_ROOT = $Stage
$aishaManualPydGateOut = (& $verifyPy -s -B -c $aishaManualPydGateCode 2>&1 | Out-String)
$aishaManualPydGateExit = $LASTEXITCODE
Remove-Item Env:\BIDKING_PROJECT_ROOT -ErrorAction SilentlyContinue
if ($aishaManualPydGateExit -ne 0 -or $aishaManualPydGateOut -notmatch "AISHA_MANUAL_PYD_GATE_OK") {
    Write-Host $aishaManualPydGateOut -ForegroundColor Red
    $ErrorActionPreference = $prevEAP2
    throw "Aisha manual .pyd runtime gate failed: encrypted engine rejected round=None or produced no quote."
}
Write-Host "  + Aisha manual .pyd runtime gate 通过: product manual round=None"
$ErrorActionPreference = $prevEAP2

function Save-ReleaseAvailabilityFailureReceipt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object]$GateExit,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$GateOutput,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$OutputPath
    )

    if ($GateExit.GetType() -ne [int] -or $GateExit -le 0 -or $GateExit -gt 255) {
        throw "release availability checker failure receipt invalid ($Label)。"
    }
    $safeTypes = @(
        "AssertionError", "AttributeError", "FileNotFoundError", "ImportError",
        "KeyError", "ModuleNotFoundError", "OSError", "PermissionError",
        "RuntimeError", "TypeError", "ValueError"
    )
    $line = $GateOutput
    if ($line.EndsWith("`r`n", [System.StringComparison]::Ordinal)) {
        $line = $line.Substring(0, $line.Length - 2)
    } elseif ($line.EndsWith("`n", [System.StringComparison]::Ordinal)) {
        $line = $line.Substring(0, $line.Length - 1)
    }
    if ($line.Contains("`r") -or $line.Contains("`n")) {
        throw "release availability checker failure receipt invalid ($Label)。"
    }
    $match = [regex]::Match(
        $line,
        '\A\{"schema":"bidking_release_availability_receipt_v1","status":"fail","error":"(?<type>[A-Za-z][A-Za-z0-9_]{0,63})"\}\z'
    )
    if (-not $match.Success) {
        throw "release availability checker failure receipt invalid ($Label)。"
    }
    $errorType = [string]$match.Groups["type"].Value
    if ($safeTypes -cnotcontains $errorType) {
        throw "release availability checker failure receipt invalid ($Label)。"
    }

    $payload = [System.Text.Encoding]::ASCII.GetBytes($line + "`n")
    if ($payload.Length -le 0 -or $payload.Length -gt 256) {
        throw "release availability checker failure receipt invalid ($Label)。"
    }
    if (
        (Test-Path -LiteralPath $OutputPath) -or
        -not (Test-Path `
            -LiteralPath $ReleaseAvailabilityFailureReceiptPublisherPath `
            -PathType Leaf) -or
        (& $TrustedFileHashCommand `
            -LiteralPath $ReleaseAvailabilityFailureReceiptPublisherPath `
            -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            $ReleaseAvailabilityFailureReceiptPublisherSHA256
    ) {
        throw "release availability checker failure receipt preexists ($Label)。"
    }
    $expectedSHA256 = $null
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $expectedSHA256 = [System.BitConverter]::ToString(
            $sha.ComputeHash($payload)
        ).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }

    $previousReceiptEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $publisherOutput = (
            & $BuildPython -I -B `
                $ReleaseAvailabilityFailureReceiptPublisherPath `
                --output $OutputPath `
                --error-type $errorType 2>&1 |
                Out-String
        )
        $publisherExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousReceiptEap
    }
    if ($publisherExit -ne 0) {
        throw "release availability checker failure receipt write_failed ($Label)。"
    }
    $publisherLine = [string]$publisherOutput
    if ($publisherLine.EndsWith("`r`n", [System.StringComparison]::Ordinal)) {
        $publisherLine = $publisherLine.Substring(0, $publisherLine.Length - 2)
    } elseif ($publisherLine.EndsWith("`n", [System.StringComparison]::Ordinal)) {
        $publisherLine = $publisherLine.Substring(0, $publisherLine.Length - 1)
    }
    if ($publisherLine.Contains("`r") -or $publisherLine.Contains("`n")) {
        throw "release availability checker failure receipt readback_failed ($Label)。"
    }
    $publicationMatch = [regex]::Match(
        $publisherLine,
        '\A\{"attributes":(?<attributes>[0-9]{1,10}),"bytes":(?<bytes>[0-9]{1,3}),"creation_time":(?<creation>[0-9]{1,20}),"file_index":(?<fileindex>[0-9]{1,20}),"last_write_time":(?<lastwrite>[0-9]{1,20}),"number_of_links":1,"schema":"bidking_release_availability_failure_publication_v1","sha256":"(?<sha>[0-9a-f]{64})","status":"published","volume_serial_number":(?<volume>[0-9]{1,10})\}\z'
    )
    if (-not $publicationMatch.Success) {
        throw "release availability checker failure receipt readback_failed ($Label)。"
    }
    try {
        $receiptAttributes = [Convert]::ToUInt32(
            $publicationMatch.Groups["attributes"].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $receiptBytes = [Convert]::ToInt32(
            $publicationMatch.Groups["bytes"].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $receiptCreationTime = [Convert]::ToInt64(
            $publicationMatch.Groups["creation"].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $receiptFileIndex = [Convert]::ToUInt64(
            $publicationMatch.Groups["fileindex"].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $receiptLastWriteTime = [Convert]::ToInt64(
            $publicationMatch.Groups["lastwrite"].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $receiptVolume = [Convert]::ToUInt32(
            $publicationMatch.Groups["volume"].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
    } catch {
        throw "release availability checker failure receipt readback_failed ($Label)。"
    }
    $receiptSHA256 = [string]$publicationMatch.Groups["sha"].Value
    if (
        $receiptAttributes -eq 0 -or
        ($receiptAttributes -band 0x10) -ne 0 -or
        ($receiptAttributes -band 0x400) -ne 0 -or
        $receiptBytes -ne $payload.Length -or
        $receiptCreationTime -le 0 -or
        $receiptFileIndex -le 0 -or
        $receiptLastWriteTime -le 0 -or
        $receiptVolume -le 0 -or
        $receiptSHA256 -cne $expectedSHA256
    ) {
        throw "release availability checker failure receipt readback_failed ($Label)。"
    }
    return [pscustomobject]@{
        Attributes = [uint32]$receiptAttributes
        Bytes = [int64]$receiptBytes
        CreationTime = [int64]$receiptCreationTime
        ErrorType = $errorType
        FileIndex = [uint64]$receiptFileIndex
        FileName = [System.IO.Path]::GetFileName($OutputPath)
        LastWriteTime = [int64]$receiptLastWriteTime
        NumberOfLinks = [int64]1
        Path = $OutputPath
        SHA256 = $receiptSHA256
        VolumeSerialNumber = [uint32]$receiptVolume
    }
}

function Invoke-ReleaseAvailabilityGate(
    [string]$PackageRoot,
    [string]$PackagePython,
    [string]$Label
) {
    if (
        $ReleaseCaptureResolvedPaths.Count -ne 5 -or
        [string]::IsNullOrEmpty($ReleaseCaptureIdentityReceiptJson) -or
        $null -eq $ReleaseCaptureLeaseProcess -or
        $ReleaseCaptureLeaseProcess.HasExited -or
        -not (Test-Path -LiteralPath $PackagePython -PathType Leaf)
    ) {
        throw "release availability gate 输入不完整 ($Label)。"
    }
    $previousGateEap = $ErrorActionPreference
    $previousGateProjectRoot = $env:BIDKING_PROJECT_ROOT
    $previousGateCaptureIdentity = $env:BIDKING_RELEASE_CAPTURE_IDENTITY_RECEIPT
    try {
        $ErrorActionPreference = "Continue"
        $env:BIDKING_PROJECT_ROOT = $PackageRoot
        $env:BIDKING_RELEASE_CAPTURE_IDENTITY_RECEIPT = (
            $ReleaseCaptureIdentityReceiptJson
        )
        $gateOutput = (
            & $PackagePython -I -B $ReleaseAvailabilityCheckerPath `
                --package-root $PackageRoot `
                --capture-2536 $ReleaseCaptureResolvedPaths[0] `
                --capture-2408 $ReleaseCaptureResolvedPaths[1] `
                --capture-2409 $ReleaseCaptureResolvedPaths[2] `
                --capture-4501 $ReleaseCaptureResolvedPaths[3] `
                --capture-4538 $ReleaseCaptureResolvedPaths[4] 2>&1 |
                Out-String
        )
        $gateExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousGateEap
        if ($null -eq $previousGateProjectRoot) {
            Remove-Item Env:\BIDKING_PROJECT_ROOT -ErrorAction SilentlyContinue
        } else {
            $env:BIDKING_PROJECT_ROOT = $previousGateProjectRoot
        }
        if ($null -eq $previousGateCaptureIdentity) {
            Remove-Item Env:\BIDKING_RELEASE_CAPTURE_IDENTITY_RECEIPT `
                -ErrorAction SilentlyContinue
        } else {
            $env:BIDKING_RELEASE_CAPTURE_IDENTITY_RECEIPT = (
                $previousGateCaptureIdentity
            )
        }
    }
    if ($gateExit -ne 0) {
        $failureLabel = switch ($Label) {
            "Stage" { "stage" }
            "canonical ZIP" { "canonical-zip" }
            default {
                throw "release availability checker failure label invalid。"
            }
        }
        $failureReceiptPath = "$Zip.release-availability-$failureLabel-failure.json"
        $failureReceipt = Save-ReleaseAvailabilityFailureReceipt `
            -GateExit $gateExit `
            -GateOutput ([string]$gateOutput) `
            -Label $Label `
            -OutputPath $failureReceiptPath
        throw (
            "release availability checker failed ($Label): " +
            "checker_exit=$gateExit; checker_error=$($failureReceipt.ErrorType); " +
            "failure_receipt=$($failureReceipt.FileName); " +
            "failure_receipt_sha256=$($failureReceipt.SHA256)"
        )
    }
    try {
        $gateReceipt = $gateOutput | ConvertFrom-Json
    } catch {
        throw "release availability checker 未返回单一有效 JSON ($Label)。"
    }
    if (
        $gateReceipt.schema -cne "bidking_release_availability_receipt_v1" -or
        $gateReceipt.receipt_version -is [bool] -or
        [int64]$gateReceipt.receipt_version -ne 1 -or
        $gateReceipt.status -cne "pass" -or
        $gateReceipt.package.verdict -cne "pass" -or
        [string]$gateReceipt.package.identity_sha256 -notmatch '^[0-9a-f]{64}$'
    ) {
        throw "release availability receipt 顶层合同失败 ($Label)。"
    }
    $actualCaptures = @($gateReceipt.captures)
    if ($actualCaptures.Count -ne 5) {
        throw "release availability capture receipt 数量无效 ($Label)。"
    }
    for ($captureIndex = 0; $captureIndex -lt 5; $captureIndex++) {
        $actualCapture = $actualCaptures[$captureIndex]
        $expectedCapture = $ExpectedReleaseCaptures[$captureIndex]
        if (
            $actualCapture.verdict -cne "pass" -or
            [int64]$actualCapture.bytes -ne [int64]$expectedCapture.bytes -or
            [string]$actualCapture.sha256 -cne [string]$expectedCapture.sha256
        ) {
            throw "release availability capture receipt 身份不一致 ($Label)。"
        }
    }
    $expectedCensus = $ReleaseZipContract.release_availability.policy_census
    if ([int64]$gateReceipt.policy_census.policy_roots -ne [int64]$expectedCensus.policy_roots) {
        throw "release availability census root 数量不一致 ($Label)。"
    }
    foreach ($layer in @("direct_root", "source_map")) {
        foreach ($field in @(
            "complete", "degraded", "sampleable", "missing", "empty", "unsupported"
        )) {
            if (
                [int64]$gateReceipt.policy_census.$layer.$field -ne
                    [int64]$expectedCensus.$layer.$field
            ) {
                throw "release availability census 不一致 ($Label/$layer/$field)。"
            }
        }
        if (
            [string]$gateReceipt.policy_census.$layer.unsupported_ids_sha256 -cne
                [string]$expectedCensus.$layer.unsupported_ids_sha256
        ) {
            throw "release availability census identity 不一致 ($Label/$layer)。"
        }
    }
    $expectedRoutes = $ReleaseZipContract.release_availability.route_scope
    foreach ($field in @(
        "changed_route_count", "new_only_count", "legacy_only_count",
        "forbidden_count", "current_activity_alias_sampleable_count",
        "ranked_production_unsupported_count"
    )) {
        if ([int64]$gateReceipt.route_scope.$field -ne [int64]$expectedRoutes.$field) {
            throw "release availability route scope 不一致 ($Label/$field)。"
        }
    }
    if (
        $gateReceipt.policy_census.verdict -cne "pass" -or
        $gateReceipt.route_scope.verdict -cne "pass" -or
        $gateReceipt.chains.accepted_scheduled_snapshot -cne "pass" -or
        $gateReceipt.chains.summary_api_ui -cne "pass" -or
        $gateReceipt.chains.current_authority_2409 -cne "pass"
    ) {
        throw "release availability 产品链 verdict 失败 ($Label)。"
    }
    Write-Host "  + RELEASE_AVAILABILITY_OK ($Label)" -ForegroundColor Green
    return $gateReceipt
}

$ReleaseAvailabilityStageReceipt = $null
if ($Encrypt) {
    $ReleaseAvailabilityStageReceipt = Invoke-ReleaseAvailabilityGate `
        -PackageRoot $Stage `
        -PackagePython $verifyPy `
        -Label "Stage"
}

# --- 6d. 最终字节码清理门（必须在所有 Stage 运行时烟测之后、Ed25519 签名前）---
# 运行时烟测会重新生成项目与第三方依赖的 __pycache__。第三方 .pyc 的 co_filename
# 会固化构建机绝对路径，且运行时可以直接从同包 .py 源文件导入，因此最终公版必须
# 删除这些可再生缓存；标准库仍由 python313.zip 提供，不受此清理影响。
$projectStageRoots = @("hero_ref", "src", "scripts")
foreach ($projectRel in $projectStageRoots) {
    $projectRoot = Join-Path $Stage $projectRel
    if (-not (Test-Path -LiteralPath $projectRoot -PathType Container)) { continue }
    $cacheDirs = @(Get-ChildItem -LiteralPath $projectRoot -Recurse -Directory -Filter "__pycache__" -Force)
    foreach ($cacheDir in ($cacheDirs | Sort-Object { $_.FullName.Length } -Descending)) {
        Remove-Item -LiteralPath $cacheDir.FullName -Recurse -Force
    }
    $bytecodeFiles = @(Get-ChildItem -LiteralPath $projectRoot -Recurse -File -Force |
        Where-Object { $_.Extension -in @(".pyc", ".pyo") })
    foreach ($bytecodeFile in $bytecodeFiles) {
        Remove-Item -LiteralPath $bytecodeFile.FullName -Force
    }
}
$remainingProjectBytecode = @()
foreach ($projectRel in $projectStageRoots) {
    $projectRoot = Join-Path $Stage $projectRel
    if (-not (Test-Path -LiteralPath $projectRoot -PathType Container)) { continue }
    $remainingProjectBytecode += @(Get-ChildItem -LiteralPath $projectRoot -Recurse -Force |
        Where-Object {
            ($_.PSIsContainer -and $_.Name -eq "__pycache__") -or
            (-not $_.PSIsContainer -and $_.Extension -in @(".pyc", ".pyo"))
        })
}
if ($remainingProjectBytecode.Count -gt 0) {
    throw "最终项目字节码清理失败，仍有 $($remainingProjectBytecode.Count) 个 pycache/pyc/pyo，拒绝压缩。"
}
$runtimeCacheDirs = @(Get-ChildItem -LiteralPath $RuntimePy -Recurse -Directory -Filter "__pycache__" -Force)
foreach ($cacheDir in ($runtimeCacheDirs | Sort-Object { $_.FullName.Length } -Descending)) {
    Remove-Item -LiteralPath $cacheDir.FullName -Recurse -Force
}
$runtimeBytecodeFiles = @(Get-ChildItem -LiteralPath $RuntimePy -Recurse -File -Force |
    Where-Object { $_.Extension -in @(".pyc", ".pyo") })
foreach ($bytecodeFile in $runtimeBytecodeFiles) {
    Remove-Item -LiteralPath $bytecodeFile.FullName -Force
}
$remainingRuntimeBytecode = @(Get-ChildItem -LiteralPath $RuntimePy -Recurse -Force |
    Where-Object {
        ($_.PSIsContainer -and $_.Name -eq "__pycache__") -or
        (-not $_.PSIsContainer -and $_.Extension -in @(".pyc", ".pyo"))
    })
if ($remainingRuntimeBytecode.Count -gt 0) {
    throw "最终运行时字节码清理失败，仍有 $($remainingRuntimeBytecode.Count) 个 pycache/pyc/pyo，拒绝压缩。"
}
foreach ($buildOnlySourceModule in $BuildOnlySourceModules) {
    if (Test-Path -LiteralPath (Join-Path $Stage $buildOnlySourceModule)) {
        throw "最终 Stage 残留 build-only 模块，拒绝压缩。"
    }
}
Write-Host "  + 最终项目与 runtime/python 可再生字节码清理通过"

# runtime/python 的批准身份来自仓库内受审固定资产，并由 release contract 固定
# SHA。当前 build 只能逐项计算 Stage 的 path/size/SHA 后与该身份比较，绝不能
# 从 Stage 生成或重绑批准集合。
Write-Host "  + runtime/python 源码受审库存已由 release contract 固定"

# 群发包技术暴露门：实现模块必须只剩编译扩展；两个可读入口只允许最小 bootstrap。
if ($Encrypt) {
    $forbiddenPlainLocalModules = @(
        "run_wss_live_monitor.py",
        "mitm_bidking_wss_addon.py",
        "bidking_wss_capture_core.py",
        "_local_compat_core.py",
        "local_compat_launcher.py",
        "runtime_component.py",
        "live_process_discovery.ps1"
    )
    foreach ($forbiddenPlainLocalModule in $forbiddenPlainLocalModules) {
        if (Test-Path -LiteralPath (Join-Path $scriptsDst $forbiddenPlainLocalModule) -PathType Leaf) {
            throw "群发包残留产品本地兼容明文模块: scripts/$forbiddenPlainLocalModule"
        }
    }
    $monitorBootstrapPath = Join-Path $scriptsDst "local_compat_monitor.py"
    $addonBootstrapPath = Join-Path $scriptsDst "local_compat_addon.py"
    $componentBootstrapPath = Join-Path $scriptsDst "local_compat_component.py"
    foreach ($bootstrapPath in @(
            $monitorBootstrapPath,
            $addonBootstrapPath,
            $componentBootstrapPath
        )) {
        if (-not (Test-Path -LiteralPath $bootstrapPath -PathType Leaf)) {
            throw "群发包缺本地兼容启动壳: $bootstrapPath"
        }
    }
    if ((Get-Item -LiteralPath $monitorBootstrapPath).Length -gt 256) {
        throw "monitor 启动壳超出最小暴露上限。"
    }
    $componentBootstrapText = Get-Content -LiteralPath $componentBootstrapPath -Raw -Encoding utf8
    if ((Get-Item -LiteralPath $componentBootstrapPath).Length -gt 512 -or
        -not $componentBootstrapText.Contains("_runtime_component")) {
        throw "固定兼容运行时组件启动壳包含明文实现或尺寸异常。"
    }
    $addonBootstrapText = Get-Content -LiteralPath $addonBootstrapPath -Raw -Encoding utf8
    if ((Get-Item -LiteralPath $addonBootstrapPath).Length -gt 2048 -or
        $addonBootstrapText.Contains("release.bid-king.cn") -or
        $addonBootstrapText.Contains("class BidKingWssAddon") -or
        $addonBootstrapText.Contains("ALLOWED_SEND_MESSAGE_IDS") -or
        $addonBootstrapText.Contains("base64") -or
        $addonBootstrapText.Contains("gzip") -or
        -not $addonBootstrapText.Contains("_local_compat_addon_loader")) {
        throw "addon 跨运行时壳包含明文实现或尺寸异常。"
    }
    foreach ($requiredNativeModule in @(
            "_local_compat_core",
            "_local_compat_monitor",
            "_client_launcher",
            "_runtime_component"
        )) {
        $nativeMatches = @(Get-ChildItem -LiteralPath $scriptsDst -Filter "$requiredNativeModule*.pyd" -File)
        if ($nativeMatches.Count -ne 1) {
            throw "本地兼容编译模块数量异常: $requiredNativeModule=$($nativeMatches.Count)"
        }
    }
    $clientLauncherNative = @(
        Get-ChildItem -LiteralPath $scriptsDst -Filter "_client_launcher*.pyd" -File
    )
    $nativeKernelModules = @(
        Get-ChildItem -LiteralPath (Join-Path $Stage "src") -Filter "bidking_kernel*.pyd" -File
    )
    if ($clientLauncherNative.Count -ne 1 -or $nativeKernelModules.Count -ne 1) {
        throw "client lifecycle native 模块数量异常。"
    }
    $clientLauncherNativeText = [System.Text.Encoding]::ASCII.GetString(
        [System.IO.File]::ReadAllBytes($clientLauncherNative[0].FullName)
    )
    foreach ($forbiddenLifecycleSymbol in @(
            "_POWERSHELL_PAYLOAD_B64",
            "_DIAGNOSTICS_PAYLOAD_B64",
            "_REPAIR_PAYLOAD_B64",
            "_decode_payload",
            "_run_powershell_source"
        )) {
        if ($clientLauncherNativeText.Contains($forbiddenLifecycleSymbol)) {
            throw "client launcher 残留可逆 lifecycle payload 接口。"
        }
    }
    $nativeKernelText = [System.Text.Encoding]::ASCII.GetString(
        [System.IO.File]::ReadAllBytes($nativeKernelModules[0].FullName)
    )
    foreach ($requiredLifecycleSymbol in @(
            "client_lifecycle_contract",
            "run_client_lifecycle",
            "client_lifecycle_request_rejected",
            "client_lifecycle_payload_invalid"
        )) {
        if (-not $nativeKernelText.Contains($requiredLifecycleSymbol)) {
            throw "client lifecycle native helper 合同不完整。"
        }
    }
    if (Test-Path -LiteralPath (Join-Path $scriptsDst "startup_repair.ps1")) {
        throw "群发包不得包含明文 startup repair payload。"
    }
    $protectedAddonModules = @(
        Get-ChildItem -LiteralPath $scriptsDst -Filter "_local_compat_addon_loader*.pyd" -File
    )
    if ($protectedAddonModules.Count -ne 1 -or
        $protectedAddonModules[0].Name -cne "_local_compat_addon_loader.pyd") {
        throw "群发包缺 Windows stable-ABI addon 保护模块。"
    }
    $incompatibleAddonModules = @(
        Get-ChildItem -LiteralPath $scriptsDst -Filter "_local_compat_addon*.pyd" -File |
            Where-Object { $_.FullName -cne $protectedAddonModules[0].FullName }
    )
    if ($incompatibleAddonModules.Count -ne 0) {
        throw "群发包残留与固定 mitmproxy ABI 不兼容的 addon pyd。"
    }
    $localCompatIntermediates = @(
        Get-ChildItem -LiteralPath $scriptsDst -File -Recurse |
            Where-Object {
                $_.Extension -in @(".c", ".obj", ".lib", ".exp") -and
                $_.Name -like "*local_compat*"
            }
    )
    if ($localCompatIntermediates.Count -gt 0) {
        throw "群发包残留本地兼容编译中间文件，拒绝压缩。"
    }
    $publicShellText = Get-Content -LiteralPath $publicLauncherScript -Raw -Encoding utf8
    foreach ($forbiddenPublicShellTerm in @(
            "release.bid-king.cn",
            "run_wss_live_monitor",
            "mitm_bidking_wss_addon",
            "bidking_wss_capture_core",
            "BIDKING_DISABLE_WSS_CAPTURE",
            "ServerPort",
            "Mitmdump",
            "NpcapMonitor",
            "WssAddon"
        )) {
        if ($publicShellText.Contains($forbiddenPublicShellTerm)) {
            throw "群发启动壳残留内部实现标识，拒绝压缩。"
        }
    }
    $publicDiagnosticsText = Get-Content -LiteralPath (
        Join-Path $scriptsDst "collect_diagnostics.ps1"
    ) -Raw -Encoding utf8
    foreach ($forbiddenPublicDiagnosticsTerm in @(
            "release.bid-king.cn",
            "run_wss_live_monitor",
            "mitm_bidking_wss_addon",
            "bidking_wss_capture_core",
            "capture_source_status",
            "ServerPort",
            "mitmdump",
            "WinDivert",
            "Npcap",
            "0x2D"
        )) {
        if ($publicDiagnosticsText.Contains($forbiddenPublicDiagnosticsTerm)) {
            throw "群发诊断壳残留内部实现标识，拒绝压缩。"
        }
    }
    Write-Host "  + 本地兼容技术暴露门通过（4 个客户端 .pyd + 1 个 abi3 addon 保护模块 + 最小 addon/monitor/component/PowerShell 壳）"
}

# --- 7. 打包 zip ---
if ($SplitCompatibilityRuntime) {
    $vendorFull = [System.IO.Path]::GetFullPath((Join-Path $Stage "vendor\mitmproxy"))
    $stageFull = [System.IO.Path]::GetFullPath($Stage).TrimEnd("\") + "\"
    if (-not $vendorFull.StartsWith(
            $stageFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "固定兼容运行时拆分目录越界，拒绝删除。"
    }
    if (-not (Test-Path -LiteralPath $RuntimeComponentArchive -PathType Leaf)) {
        throw "固定兼容运行时组件成品不存在，拒绝生成薄主包。"
    }
    if (Test-Path -LiteralPath $vendorFull -PathType Container) {
        Remove-Item -LiteralPath $vendorFull -Recurse -Force
    }
    if (Test-Path -LiteralPath $vendorFull) {
        throw "固定兼容运行时未从薄主包 Stage 中移除。"
    }
    Write-Host "  + 薄主包已移除 vendor/mitmproxy；首次启动按描述符校验下载并跨版本缓存"
}

# Ed25519 full-tree manifest 必须只绑定最终 Stage。所有会删除或改写 Stage 叶文件的
# runtime smoke / 可再生字节码 cleanup / 可选薄包裁剪均已在上方完成；从这里到
# canonical ZIP 只允许读取 manifest-covered 成员（BUILD_MANIFEST 是合同外元数据）。
if ($Encrypt) {
    $previousLocalIntegritySignEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $localIntegritySignOut = (
            & $BuildPython -I -B $LocalIntegrityBuilder sign `
                --stage $Stage `
                --contract $LocalIntegrityContractPath `
                --protection-manifest $ClientProtectionManifestPath `
                --signing-key $IntegritySigningKeyFile `
                --release-id "v$Version`:$SourceCommit" 2>&1 |
                Out-String
        )
        $localIntegritySignExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousLocalIntegritySignEap
    }
    if ($localIntegritySignExit -ne 0) {
        throw "离线 Ed25519 本地完整性 manifest 生成失败: $localIntegritySignOut"
    }
    try {
        $localIntegrityReceipt = $localIntegritySignOut | ConvertFrom-Json
        $LocalIntegrityManifestSHA256 = [string]$localIntegrityReceipt.manifest_sha256
        $LocalIntegrityEntryCount = [int]$localIntegrityReceipt.entry_count
    } catch {
        throw "本地完整性 manifest 回执无效，拒绝构建。"
    }
    if ($LocalIntegrityManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or
        $LocalIntegrityEntryCount -le 0 -or
        [string]$localIntegrityReceipt.public_key_sha256 -cne $LocalIntegrityPublicKeySHA256) {
        throw "本地完整性 manifest 身份与内嵌公钥不一致，拒绝构建。"
    }
    Invoke-LocalIntegrityGate `
        -Label "最终Stage签名后" `
        -PythonPath $verifyPy `
        -PackageRoot $Stage
    $LocalIntegrityVerified = $true
    Write-CandidateBuildManifest
}
$previousEapRuntimeReceipt = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $runtimeReceiptOut = (
        & $BuildPython -X utf8 -I -B $zipAuditor `
            --write-runtime-receipt `
                $RuntimePy `
                $RuntimeInventoryReceiptPath `
                $RuntimeInventoryApprovalPath 2>&1 |
            Out-String
    )
    $runtimeReceiptExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousEapRuntimeReceipt
}
if ($runtimeReceiptExit -ne 0 -or
    -not (Test-Path -LiteralPath $RuntimeInventoryReceiptPath -PathType Leaf)) {
    Write-Host $runtimeReceiptOut -ForegroundColor Red
    throw "最终 Stage runtime/python canonical receipt 生成失败，拒绝压缩。"
}
$RuntimeInventoryReceiptSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $RuntimeInventoryReceiptPath -Algorithm SHA256
).Hash.ToLowerInvariant()
Write-CandidateBuildManifest
Write-Host $runtimeReceiptOut.Trim() -ForegroundColor Green
Write-Host "  + BUILD_MANIFEST 已绑定外部 runtime receipt SHA256"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
$previousEapCanonicalZip = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $canonicalZipOut = (
        & $BuildPython -X utf8 -I -B $zipAuditor `
            --write-canonical-zip $Stage $Zip 2>&1 |
            Out-String
    )
    $canonicalZipExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousEapCanonicalZip
}
if ($canonicalZipExit -ne 0 -or -not (Test-Path -LiteralPath $Zip -PathType Leaf)) {
    Write-Host $canonicalZipOut -ForegroundColor Red
    throw "canonical POSIX 成品 ZIP 生成失败，拒绝形成候选。"
}
Write-Host $canonicalZipOut.Trim() -ForegroundColor Green
if ($Encrypt) {
    $previousEapBkencReceipt = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $bkencReceiptOut = (
            & $BuildPython -X utf8 -E -B $zipAuditor `
                --write-bkenc-receipt `
                    $Zip `
                    $BkencAuthenticationReceiptPath `
                    $ReleaseZipContractPath `
                    $MasterKeyFile 2>&1 |
                Out-String
        )
        $bkencReceiptExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEapBkencReceipt
    }
    if (
        $bkencReceiptExit -ne 0 -or
        -not (
            Test-Path -LiteralPath $BkencAuthenticationReceiptPath -PathType Leaf
        )
    ) {
        Write-Host $bkencReceiptOut -ForegroundColor Red
        throw "BKENC 逐成员真实 AEAD receipt 生成失败，拒绝形成候选。"
    }
    $BkencAuthenticationReceiptSHA256 = (
        & $TrustedFileHashCommand `
            -LiteralPath $BkencAuthenticationReceiptPath `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    Write-CandidateBuildManifest

    # Receipt hash进入BUILD_MANIFEST后，必须重新生成最终canonical ZIP。BKENC
    # 成员本身未变化；最终审计还会核对其压缩字节与receipt，防止重打包替换。
    $previousEapFinalCanonicalZip = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $finalCanonicalZipOut = (
            & $BuildPython -X utf8 -I -B $zipAuditor `
                --write-canonical-zip $Stage $Zip 2>&1 |
                Out-String
        )
        $finalCanonicalZipExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEapFinalCanonicalZip
    }
    if (
        $finalCanonicalZipExit -ne 0 -or
        -not (Test-Path -LiteralPath $Zip -PathType Leaf)
    ) {
        Write-Host $finalCanonicalZipOut -ForegroundColor Red
        throw "绑定BKENC receipt后的canonical ZIP重建失败，拒绝形成候选。"
    }
    Write-Host $bkencReceiptOut.Trim() -ForegroundColor Green
    Write-Host $finalCanonicalZipOut.Trim() -ForegroundColor Green
}

$ReleaseAvailabilityBuildReceipt = $null
if ($Encrypt) {
    # 产品可用性门必须再次消费 canonical ZIP 的真实解包字节，不能由 Stage
    # success 或 5604/2508 synthetic ABI rebind 冒充成品可用性。
    $availabilityCachePrefix = [System.IO.Path]::GetFullPath($Cache).TrimEnd("\") + "\"
    $availabilityExtractedRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $Cache "$Name-release-availability-extracted")
    )
    if (-not $availabilityExtractedRoot.StartsWith(
            $availabilityCachePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "release availability 解包目录越界。"
    }
    if (Test-Path -LiteralPath $availabilityExtractedRoot) {
        Remove-Item -LiteralPath $availabilityExtractedRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $availabilityExtractedRoot | Out-Null
    Expand-Archive `
        -LiteralPath $Zip `
        -DestinationPath $availabilityExtractedRoot `
        -Force
    $availabilityExtractedPython = Join-Path (
        $availabilityExtractedRoot
    ) "runtime\python\python.exe"
    $ReleaseAvailabilityZipReceipt = Invoke-ReleaseAvailabilityGate `
        -PackageRoot $availabilityExtractedRoot `
        -PackagePython $availabilityExtractedPython `
        -Label "canonical ZIP"
    if (
        [string]$ReleaseAvailabilityStageReceipt.package.identity_sha256 -cne
            [string]$ReleaseAvailabilityZipReceipt.package.identity_sha256
    ) {
        throw "Stage 与 canonical ZIP release availability package identity 不一致。"
    }
    foreach ($field in @("policy_census", "route_scope", "chains", "captures")) {
        $stageValue = $ReleaseAvailabilityStageReceipt.$field |
            ConvertTo-Json -Depth 8 -Compress
        $zipValue = $ReleaseAvailabilityZipReceipt.$field |
            ConvertTo-Json -Depth 8 -Compress
        if ($stageValue -cne $zipValue) {
            throw "Stage 与 canonical ZIP release availability receipt 不一致 ($field)。"
        }
    }
    $ReleaseAvailabilityBuildReceipt = [ordered]@{
        schema = "bidking_release_availability_receipt_v1"
        receipt_version = 1
        status = "pass"
        captures = @($ReleaseAvailabilityZipReceipt.captures)
        stage = [ordered]@{
            identity_sha256 = [string]$ReleaseAvailabilityStageReceipt.package.identity_sha256
            verdict = "pass"
        }
        extracted_zip = [ordered]@{
            identity_sha256 = [string]$ReleaseAvailabilityZipReceipt.package.identity_sha256
            verdict = "pass"
        }
        policy_census = $ReleaseAvailabilityZipReceipt.policy_census
        route_scope = $ReleaseAvailabilityZipReceipt.route_scope
        chains = $ReleaseAvailabilityZipReceipt.chains
    }
    Remove-Item -LiteralPath $availabilityExtractedRoot -Recurse -Force
}

# --- 7a. 冻结构建审计 authority receipt；成品审计只消费该 receipt ---
function Get-BuildAuditSourceIdentity([string]$RelativePath, [string]$FullPath) {
    return [ordered]@{
        path = $RelativePath.Replace("\", "/")
        sha256 = (
            & $TrustedFileHashCommand -LiteralPath $FullPath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
    }
}
$ExpectedBuildManifestFields = [ordered]@{
    PackageVersion = "v$Version"
    SourceCommit = [string]$SourceCommit
    SourceBranch = [string]$SourceBranch
    PackageInputsDirty = [string]$PackageInputsDirty
    BuildScriptSHA256 = [string]$BuildScriptSHA256
    ClientProtectionManifestSHA256 = [string]$ClientProtectionManifestSHA256
    NativeBinaryContractSHA256 = [string]$NativeBinaryContractSHA256
    NativeBinaryDistribution = [string]$NativeBinaryDistribution
    NativeBinaryReceiptSHA256 = [string]$NativeBinaryReceiptSHA256
    NativeBinaryVerified = [string][bool]$NativeBinaryVerified
    NativeBinaryCount = [string]$NativeBinaryCount
    WssOperationalABI = [string]$WssOperationalABI
    WssOperationalGeneration = [string]$WssOperationalGeneration
    WssAddonBootstrapSHA256 = [string]$WssAddonBootstrapSHA256
    WssAddonLoaderSHA256 = [string]$WssAddonLoaderSHA256
    PackageDataManifestSHA256 = [string]$PackageDataManifestSHA256
    PackageDataFingerprintSHA256 = [string]$PackageDataFingerprintSHA256
    PackageDataBaselineID = [string]$PackageDataBaselineID
    RuntimeInventoryReceiptSHA256 = [string]$RuntimeInventoryReceiptSHA256
    BkencAuthenticationReceiptSHA256 = [string]$BkencAuthenticationReceiptSHA256
    RuntimeInventoryApprovalSHA256 = [string]$RuntimeInventoryApprovalSHA256
    LocalIntegrityPublicKeySHA256 = [string]$LocalIntegrityPublicKeySHA256
    LocalIntegrityManifestSHA256 = [string]$LocalIntegrityManifestSHA256
    LocalIntegrityEntryCount = [string]$LocalIntegrityEntryCount
    LocalIntegrityVerified = [string][bool]$LocalIntegrityVerified
    PetAssetsInventorySHA256 = [string]$PetAssetsInventorySHA256
    DiagnosticProfile = [string]$PublicDiagnosticProfile
    ProductProfile = [string]$ProductProfile
    CommercialSurfaceMode = [string]$CommercialSurfaceMode
    MitmproxyBundled = [string]$MitmproxyBundled
    CompatibilityRuntimeMode = [string]$RuntimeComponentMode
    CompatibilityRuntimeArchive = [string]$RuntimeComponentArchiveName
    CompatibilityRuntimeArchiveBytes = [string]$RuntimeComponentArchiveBytes
    CompatibilityRuntimeArchiveSHA256 = [string]$RuntimeComponentArchiveSHA256
    CompatibilityRuntimeDescriptorSHA256 = [string]$RuntimeComponentDescriptorSHA256
    MitmproxyVersion = [string]$MitmproxyVersion
    MitmproxySourceURL = [string]$MitmproxySourceURL
    MitmproxyInstallerSHA256 = [string]$MitmproxyInstallerSHA256
    MitmproxyInventorySHA256 = [string]$MitmproxyInventorySHA256
    Encrypted = [string][bool]$Encrypt
    EncryptedUploadConfig = [string][bool]$EncUploadConfig
    ReleaseKeyCacheVerified = [string][bool]$ReleaseKeyCacheVerified
    UploadConfigRoundTripVerified = [string][bool]$UploadConfigRoundTripVerified
    BundledCABundle = [string][bool]$BundledCABundle
}
if (-not $Encrypt) {
    # Portable builds do not generate the encrypted addon bootstrap/loader pair.
    # A blank value is not a valid strict build-receipt expected field.
    $ExpectedBuildManifestFields.Remove("WssAddonBootstrapSHA256")
    $ExpectedBuildManifestFields.Remove("WssAddonLoaderSHA256")
}
$TrustedAssetReceipts = @()
if (-not $SplitCompatibilityRuntime) {
    $TrustedAssetReceipts = @(
        [ordered]@{
            prefix = "vendor/mitmproxy"
            path = "scripts/mitmproxy_runtime_manifest.json"
            sha256 = (
                & $TrustedFileHashCommand -LiteralPath $mitmproxyManifestPath -Algorithm SHA256
            ).Hash.ToLowerInvariant()
        }
    )
}
$ForbiddenPathSources = @("source_root")
if ($env:USERPROFILE) {
    $ForbiddenPathSources += "current_user_profile"
}
$ZipIdentity = Get-Item -LiteralPath $Zip
$BuildAuditReceipt = [ordered]@{
    schema = "bidking_release_build_audit_receipt_v5"
    receipt_version = 5
    zip_name = $ZipIdentity.Name
    zip_bytes = [int64]$ZipIdentity.Length
    zip_sha256 = (
        & $TrustedFileHashCommand -LiteralPath $Zip -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    source_commit = [string]$SourceCommit
    source_branch = [string]$SourceBranch
    product_profile = [string]$ProductProfile
    encrypted = [bool]$Encrypt
    protection_manifest = if ($Encrypt) {
        Get-BuildAuditSourceIdentity `
            "scripts/client_protection_manifest.json" $ClientProtectionManifestPath
    } else { $null }
    release_contract = if ($Encrypt) {
        Get-BuildAuditSourceIdentity `
            "scripts/release_zip_contract.json" $ReleaseZipContractPath
    } else { $null }
    local_integrity_contract = if ($Encrypt) {
        Get-BuildAuditSourceIdentity `
            "scripts/local_integrity_contract.json" $LocalIntegrityContractPath
    } else { $null }
    local_integrity = if ($Encrypt) {
        [ordered]@{
            manifest_name = "integrity/local_integrity_manifest.json"
            manifest_sha256 = [string]$LocalIntegrityManifestSHA256
            signature_name = "integrity/local_integrity_manifest.sig"
            public_key_b64 = [string]$LocalIntegrityPublicKeyB64
            public_key_sha256 = [string]$LocalIntegrityPublicKeySHA256
            entry_count = [int]$LocalIntegrityEntryCount
            native_gate_verified = [bool]$LocalIntegrityVerified
        }
    } else { $null }
    native_binary_contract = if ($Encrypt) {
        Get-BuildAuditSourceIdentity `
            "scripts/release_native_binary_contract.json" $NativeBinaryContractPath
    } else { $null }
    native_binary_receipt = if ($Encrypt) {
        [ordered]@{
            name = [System.IO.Path]::GetFileName($NativeBinaryAuditReceiptPath)
            sha256 = [string]$NativeBinaryReceiptSHA256
        }
    } else { $null }
    runtime_receipt = [ordered]@{
        name = [System.IO.Path]::GetFileName($RuntimeInventoryReceiptPath)
        sha256 = [string]$RuntimeInventoryReceiptSHA256
    }
    runtime_approval = Get-BuildAuditSourceIdentity `
        "scripts/runtime_python_inventory_manifest.json" $RuntimeInventoryApprovalPath
    bkenc_receipt = if ($Encrypt) {
        [ordered]@{
            name = [System.IO.Path]::GetFileName($BkencAuthenticationReceiptPath)
            sha256 = [string]$BkencAuthenticationReceiptSHA256
        }
    } else { $null }
    release_availability = if ($Encrypt) {
        $ReleaseAvailabilityBuildReceipt
    } else { $null }
    expected_manifest_fields = $ExpectedBuildManifestFields
    trusted_assets = $TrustedAssetReceipts
    forbidden_path_sources = $ForbiddenPathSources
}
$BuildAuditReceiptJson = $BuildAuditReceipt | ConvertTo-Json -Depth 8
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $BuildAuditReceiptPath,
    $BuildAuditReceiptJson,
    $Utf8NoBom
)
$BuildAuditReceiptSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $BuildAuditReceiptPath -Algorithm SHA256
).Hash.ToLowerInvariant()
Write-Host "  + build audit receipt: $BuildAuditReceiptSHA256" -ForegroundColor Green

$zipAuditArgs = @(
    $zipAuditor,
    "--build-receipt", $BuildAuditReceiptPath,
    "--source-root", $Repo
)
if ($Encrypt) {
    $zipAuditArgs += @("--bkenc-key-file", $MasterKeyFile)
}
$prevEAP3 = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$zipAuditOut = (& $BuildPython -X utf8 -E -B @zipAuditArgs 2>&1 | Out-String)
$zipAuditExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP3
if ($zipAuditExit -ne 0) {
    Write-Host $zipAuditOut -ForegroundColor Red
    throw "成品 ZIP 隐私/字节码负向扫描失败，拒绝形成候选包。"
}
Write-Host $zipAuditOut.Trim() -ForegroundColor Green
if ($SplitCompatibilityRuntime) {
    $componentZipAuditArgs = @(
        $zipAuditor,
        $RuntimeComponentArchive,
        "--forbid-path", $Repo,
        "--trusted-asset", "runtime", $mitmproxyManifestPath
    )
    if ($env:USERPROFILE) {
        $componentZipAuditArgs += @("--forbid-path", $env:USERPROFILE)
    }
    $previousEapComponentAudit = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $componentZipAuditOut = (
            & $BuildPython -X utf8 -E @componentZipAuditArgs 2>&1 |
                Out-String
        )
        $componentZipAuditExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEapComponentAudit
    }
    if ($componentZipAuditExit -ne 0) {
        Write-Host $componentZipAuditOut -ForegroundColor Red
        throw "固定兼容运行时组件 ZIP 隐私/字节码负向扫描失败。"
    }
    Write-Host $componentZipAuditOut.Trim() -ForegroundColor Green
}

# --- 7b. 解压后成品烟测 ---
$zipSmokeRoot = Join-Path $Cache "$Name-extracted-smoke"
$cacheFull = [System.IO.Path]::GetFullPath($Cache).TrimEnd("\") + "\"
$zipSmokeFull = [System.IO.Path]::GetFullPath($zipSmokeRoot)
if (-not $zipSmokeFull.StartsWith($cacheFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "解压烟测目录越界: $zipSmokeFull"
}
if (Test-Path -LiteralPath $zipSmokeFull) {
    Remove-Item -LiteralPath $zipSmokeFull -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $zipSmokeFull | Out-Null
Expand-Archive -LiteralPath $Zip -DestinationPath $zipSmokeFull -Force
$extractedPetAssets = Join-Path $zipSmokeFull "hero_ref\assets\pet_companion"
$extractedPetIdentity = Invoke-ReleaseAssetGate (
    $extractedPetAssets
) $petAssetsManifestPath "extracted ZIP Pet assets"
if ($extractedPetIdentity.inventory_sha256 -cne $PetAssetsInventorySHA256) {
    throw "解压后 Pet 素材 inventory 与 package input 不一致，拒绝形成候选包。"
}
$extractedVerifyPy = Join-Path $zipSmokeFull "runtime\python\python.exe"
if (-not (Test-Path -LiteralPath $extractedVerifyPy -PathType Leaf)) {
    throw "解压成品缺内嵌 Python，拒绝形成候选包。"
}
if ($Encrypt) {
    Invoke-LocalIntegrityGate `
        -Label "解压成品" `
        -PythonPath $extractedVerifyPy `
        -PackageRoot $zipSmokeFull
}
$extractedMitmproxy = Join-Path $zipSmokeFull "vendor\mitmproxy"
$extractedMitmproxyManifest = Join-Path (
    $zipSmokeFull
) "scripts\mitmproxy_runtime_manifest.json"
if (-not (Test-Path -LiteralPath $extractedMitmproxyManifest -PathType Leaf)) {
    throw "解压成品缺 bundled WSS runtime manifest，拒绝形成候选包。"
}
$extractedMitmproxyManifestSHA256 = (
    & $TrustedFileHashCommand -LiteralPath $extractedMitmproxyManifest -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($extractedMitmproxyManifestSHA256 -cne $stagedMitmproxyManifestSHA256) {
    throw "解压成品 bundled WSS runtime manifest 与 Stage 字节不一致。"
}
if ($SplitCompatibilityRuntime) {
    if (Test-Path -LiteralPath $extractedMitmproxy) {
        throw "薄主包仍包含 vendor/mitmproxy，拒绝形成候选包。"
    }
    $extractedComponentDescriptor = Join-Path (
        $zipSmokeFull
    ) "data\processed\local_compat_component.json"
    $extractedComponentBootstrap = Join-Path (
        $zipSmokeFull
    ) "scripts\local_compat_component.py"
    if (-not (Test-Path -LiteralPath $extractedComponentDescriptor -PathType Leaf) -or
        -not (Test-Path -LiteralPath $extractedComponentBootstrap -PathType Leaf)) {
        throw "薄主包缺固定兼容运行时组件描述符或启动壳。"
    }
    $componentSmokeCache = Join-Path $Cache "$Name-component-smoke"
    $componentSmokeFull = [System.IO.Path]::GetFullPath($componentSmokeCache)
    if (-not $componentSmokeFull.StartsWith(
            $cacheFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "固定兼容运行时组件烟测缓存越界。"
    }
    if (Test-Path -LiteralPath $componentSmokeFull) {
        Remove-Item -LiteralPath $componentSmokeFull -Recurse -Force
    }
    $previousEapComponentSmoke = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $componentSmokeOutput = (
            & $extractedVerifyPy -I -B $extractedComponentBootstrap `
                --descriptor $extractedComponentDescriptor `
                --cache-root $componentSmokeFull `
                --local-archive $RuntimeComponentArchive `
                --json 2>&1 |
                Out-String
        )
        $componentSmokeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEapComponentSmoke
    }
    if ($componentSmokeExit -ne 0) {
        throw "薄主包固定兼容运行时组件安装烟测失败: $componentSmokeOutput"
    }
    try {
        $componentSmokeResult = $componentSmokeOutput | ConvertFrom-Json
    } catch {
        throw "薄主包固定兼容运行时组件烟测未返回有效 JSON。"
    }
    $extractedMitmproxy = [System.IO.Path]::GetFullPath(
        [string]$componentSmokeResult.runtime_root
    )
    $componentSmokePrefix = $componentSmokeFull.TrimEnd("\") + "\"
    if (-not $extractedMitmproxy.StartsWith(
            $componentSmokePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "薄主包固定兼容运行时组件烟测返回路径越界。"
    }
} else {
    # 公版 ZIP 带 MotW 时，bounded Zone.Identifier 会被完整性内核接受；启动器随后必须
    # 用包内 manifest 把 bundled WSS runtime 复制为仅默认流的 content-addressed cache。
    # 该路径必须在候选形成前真实执行，不能只用 source-side manifest 验证 vendor inventory。
    $boundedMotwHelper = Join-Path $extractedMitmproxy "mitmdump.exe"
    $boundedMotwStream = $boundedMotwHelper + ":Zone.Identifier"
    $boundedMotwHelperBytes = (Get-Item -LiteralPath $boundedMotwHelper).Length
    $boundedMotwHelperSHA256 = (
        & $TrustedFileHashCommand -LiteralPath $boundedMotwHelper -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    # Windows PowerShell 5.1/.NET Framework cannot use File.* APIs for ADS paths.
    # Keep the write and both stream probes inside the extracted, package-bound Python.
    $boundedMotwPythonLauncher = "import base64,sys;exec(base64.b64decode(sys.argv.pop(1)))"
    $boundedMotwWriterCode = @'
import sys

payload = b"[ZoneTransfer]\r\nZoneId=3\r\n"
with open(sys.argv[1], "wb") as stream:
    stream.write(payload)
with open(sys.argv[1], "rb") as stream:
    if stream.read() != payload:
        raise RuntimeError("bounded MotW stream bytes differ")
print("BIDKING_EXTRACTED_MOTW_WRITE_OK")
'@
    $boundedMotwWriterPayload = [Convert]::ToBase64String(
        [System.Text.Encoding]::UTF8.GetBytes($boundedMotwWriterCode)
    )
    $previousEapBoundedMotwWriter = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $boundedMotwWriterOutput = (
            & $extractedVerifyPy -I -B -c $boundedMotwPythonLauncher `
                $boundedMotwWriterPayload $boundedMotwStream 2>&1 |
                Out-String
        ).Trim()
        $boundedMotwWriterExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEapBoundedMotwWriter
    }
    if ($boundedMotwWriterExit -ne 0 -or
        $boundedMotwWriterOutput -cne "BIDKING_EXTRACTED_MOTW_WRITE_OK") {
        throw "解压成品 bounded MotW 写入或回读失败: $boundedMotwWriterOutput"
    }
    if ((Get-Item -LiteralPath $boundedMotwHelper).Length -ne $boundedMotwHelperBytes -or
        ((& $TrustedFileHashCommand -LiteralPath $boundedMotwHelper -Algorithm SHA256).Hash.ToLowerInvariant() `
            -cne $boundedMotwHelperSHA256)) {
        throw "解压成品 bounded MotW 写入改变了默认流。"
    }
    Invoke-LocalIntegrityGate `
        -Label "解压成品 bounded MotW" `
        -PythonPath $extractedVerifyPy `
        -PackageRoot $zipSmokeFull
    $bundledMotwSmokeCache = Join-Path $Cache "$Name-bundled-motw-smoke"
    $bundledMotwSmokeFull = [System.IO.Path]::GetFullPath($bundledMotwSmokeCache)
    if (-not $bundledMotwSmokeFull.StartsWith(
            $cacheFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "bundled MotW runtime copy 烟测缓存越界。"
    }
    if (Test-Path -LiteralPath $bundledMotwSmokeFull) {
        Remove-Item -LiteralPath $bundledMotwSmokeFull -Recurse -Force
    }
    $extractedComponentBootstrap = Join-Path (
        $zipSmokeFull
    ) "scripts\local_compat_component.py"
    $previousEapBundledMotwSmoke = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $bundledMotwSmokeOutput = (
            & $extractedVerifyPy -I -B $extractedComponentBootstrap `
                --bundled-root $extractedMitmproxy `
                --bundled-manifest $extractedMitmproxyManifest `
                --cache-root $bundledMotwSmokeFull `
                --json 2>&1 |
                Out-String
        )
        $bundledMotwSmokeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEapBundledMotwSmoke
    }
    if ($bundledMotwSmokeExit -ne 0) {
        throw "解压成品 bundled MotW runtime copy 烟测失败: $bundledMotwSmokeOutput"
    }
    try {
        $bundledMotwSmokeResult = $bundledMotwSmokeOutput | ConvertFrom-Json
    } catch {
        throw "解压成品 bundled MotW runtime copy 烟测未返回有效 JSON。"
    }
    $extractedMitmproxy = [System.IO.Path]::GetFullPath(
        [string]$bundledMotwSmokeResult.runtime_root
    )
    $bundledMotwSmokePrefix = $bundledMotwSmokeFull.TrimEnd("\") + "\"
    if (-not $extractedMitmproxy.StartsWith(
            $bundledMotwSmokePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "bundled MotW runtime copy 烟测返回路径越界。"
    }
    if ((Get-Item -LiteralPath $boundedMotwHelper).Length -ne $boundedMotwHelperBytes -or
        ((& $TrustedFileHashCommand -LiteralPath $boundedMotwHelper -Algorithm SHA256).Hash.ToLowerInvariant() `
            -cne $boundedMotwHelperSHA256)) {
        throw "bundled MotW runtime copy 改变了来源默认流。"
    }
    $boundedMotwCacheStream = (Join-Path $extractedMitmproxy "mitmdump.exe") + ":Zone.Identifier"
    $boundedMotwProbeCode = @'
import sys

payload = b"[ZoneTransfer]\r\nZoneId=3\r\n"
with open(sys.argv[1], "rb") as stream:
    if stream.read() != payload:
        raise RuntimeError("source bounded MotW stream bytes differ")
try:
    with open(sys.argv[2], "rb"):
        raise RuntimeError("cache retained Zone.Identifier")
except FileNotFoundError:
    pass
print("BIDKING_EXTRACTED_MOTW_PROBE_OK")
'@
    $boundedMotwProbePayload = [Convert]::ToBase64String(
        [System.Text.Encoding]::UTF8.GetBytes($boundedMotwProbeCode)
    )
    $previousEapBoundedMotwProbe = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $boundedMotwProbeOutput = (
            & $extractedVerifyPy -I -B -c $boundedMotwPythonLauncher `
                $boundedMotwProbePayload $boundedMotwStream $boundedMotwCacheStream 2>&1 |
                Out-String
        ).Trim()
        $boundedMotwProbeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEapBoundedMotwProbe
    }
    if ($boundedMotwProbeExit -ne 0 -or
        $boundedMotwProbeOutput -cne "BIDKING_EXTRACTED_MOTW_PROBE_OK") {
        throw "bundled MotW runtime copy 未保留来源标记或未移除缓存标记: $boundedMotwProbeOutput"
    }
    Write-Host "  + 解压成品 bundled MotW runtime default-stream-only copy 通过"
}
$extractedMitmproxyIdentity = Invoke-ReleaseAssetGate (
    $extractedMitmproxy
) $extractedMitmproxyManifest "extracted/runtime-component mitmproxy runtime"
if ($extractedMitmproxyIdentity.inventory_sha256 -cne $MitmproxyInventorySHA256) {
    throw "解压或组件缓存中的 mitmproxy runtime inventory 与 package input 不一致。"
}
$prevEAP4 = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$extractedImportOut = (& $extractedVerifyPy -I -B -c $verifyCode 2>&1 | Out-String)
$extractedImportExit = $LASTEXITCODE
if ($extractedImportExit -ne 0 -or $extractedImportOut -notmatch "IMPORT_OK") {
    $ErrorActionPreference = $prevEAP4
    Write-Host $extractedImportOut -ForegroundColor Red
    throw "解压后隔离导入烟测失败，最终清理或 ZIP 内容破坏了启动依赖。"
}
foreach ($gateSpec in $gateFixtures) {
    $gateFixture = [string]$gateSpec.Path
    if (-not (Test-Path -LiteralPath $gateFixture -PathType Leaf)) {
        $ErrorActionPreference = $prevEAP4
        throw "解压后运行时 gate 样本缺失: $gateFixture"
    }
    $gateCode = New-RuntimeFixtureGateCode -GateSpec $gateSpec -GateFixture $gateFixture
    $env:BIDKING_PROJECT_ROOT = $zipSmokeFull
    $extractedGateOut = (& $extractedVerifyPy -I -B -c $gateCode 2>&1 | Out-String)
    $extractedGateExit = $LASTEXITCODE
    Remove-Item Env:\BIDKING_PROJECT_ROOT -ErrorAction SilentlyContinue
    if ($extractedGateExit -ne 0 -or $extractedGateOut -notmatch "CURRENT_AUTHORITY_ABI_SMOKE_OK") {
        $ErrorActionPreference = $prevEAP4
        Write-Host $extractedGateOut -ForegroundColor Red
        throw "解压后 current-authority ABI smoke 失败 ($([System.IO.Path]::GetFileName($gateFixture)))，拒绝形成候选包。"
    }
}
$ErrorActionPreference = $prevEAP4
Remove-Item -LiteralPath $zipSmokeFull -Recurse -Force
if ($SplitCompatibilityRuntime -and (Test-Path -LiteralPath $componentSmokeFull)) {
    Remove-Item -LiteralPath $componentSmokeFull -Recurse -Force
}
Write-Host "  + 解压后隔离导入与运行时推理烟测通过"

Stop-ReleaseCaptureLeaseHolder
$sizeMB = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
Write-Host ""
$env:BIDKING_DATA_KEY = $origGlobalDataKey   # 恢复(不外泄主密钥到后续进程/调用)
$env:BIDKING_INTEGRITY_PUBLIC_KEY_B64 = $origIntegrityPublicKey
Write-Host "==> 完成: $Zip ($sizeMB MB)" -ForegroundColor Green
if ($SplitCompatibilityRuntime) {
    $componentSizeMB = [math]::Round($RuntimeComponentArchiveBytes / 1MB, 1)
    Write-Host "    固定兼容组件: $RuntimeComponentArchive ($componentSizeMB MB)"
    Write-Host "    发布时必须同时上传组件 ZIP 到描述符中的 HTTPS 镜像地址；不得只发布薄主包。"
    Write-Host "    完整离线包可在不传 -SplitCompatibilityRuntime 时另行构建，文件名不会与本精简包冲突。"
}
Write-Host "    解压后先完全退出游戏，再双击 BidKingLive.exe。"
Write-Host "    首次启动将引导完成本机兼容设置；完成后再从 Steam 启动游戏。"
} finally {
    Stop-ReleaseCaptureLeaseHolder
}
