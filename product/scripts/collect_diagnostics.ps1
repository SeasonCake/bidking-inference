# BidKing 一键诊断收集器 —— 群友遇到问题时双击运行, 生成一个 BidKing诊断_<时间>.txt 发作者排查。
# 设计: 自包含(只用 Windows 自带 + 包内文件)、每项探测独立 try/catch(单项失败不影响整体)、
#       绝不打印上传密钥(只读 URL 做连通性测试)。覆盖最常见的发包翻车原因:
#       WSS 冷启动/证书/组件、Npcap 回退、加速器、路径、内嵌 Python 和权限。
#
# 用法: 双击「诊断工具\收集诊断.bat」, 或右键本文件→用 PowerShell 运行。

# Tests execute an instrumented temporary copy whose only change is the marked Known Folder
# resolver.  The shipped script always uses the implementation below and accepts no path override.
$ErrorActionPreference = "Continue"
$env:PYTHONDONTWRITEBYTECODE = "1"
function Write-BidKingDiagnosticProgress([int]$step, [string]$label) {
  if ($step -lt 1 -or $step -gt 7) { throw "invalid diagnostic progress step" }
  Write-Host ("[进度 {0}/7] {1}" -f $step, $label) -ForegroundColor Cyan
}
function Write-BidKingDiagnosticFailure {
  Write-Host "[失败] 诊断收集未完成；请保留本窗口提示并联系支持人员。" -ForegroundColor Red
}
trap {
  Write-BidKingDiagnosticFailure
  exit 1
}
Write-BidKingDiagnosticProgress 1 "开始收集诊断"
# BIDKING_TEST_KNOWN_FOLDER_BEGIN
function Get-BidKingKnownFolderPath($kind) {
  $folder = switch -CaseSensitive ([string]$kind) {
    "LocalApplicationData" { "LocalApplicationData"; break }
    "Documents" { "MyDocuments"; break }
    default { throw "unknown Known Folder kind" }
  }
  $value = [Environment]::GetFolderPath($folder)
  if (-not $value -or -not [System.IO.Path]::IsPathRooted($value)) {
    throw "$kind Known Folder unavailable"
  }
  return [System.IO.Path]::GetFullPath($value)
}
# BIDKING_TEST_KNOWN_FOLDER_END
function ConvertFrom-BidKingWindowsExtendedPath($path) {
  $value = [string]$path
  if ($value.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
    return '\\' + $value.Substring(8)
  }
  if ($value.StartsWith('\\?\', [StringComparison]::OrdinalIgnoreCase)) {
    return $value.Substring(4)
  }
  return $value
}
function Assert-BidKingPlainDirectory($path, $label) {
  if (-not $path -or -not [System.IO.Path]::IsPathRooted([string]$path)) {
    throw "$label is not absolute"
  }
  if (-not (Test-Path -LiteralPath $path -PathType Container)) {
    throw "$label is unavailable"
  }
  $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$label is a reparse point"
  }
  return $item.FullName
}
function Assert-BidKingPlainSingleLinkFile($path, $label) {
  $path = ConvertFrom-BidKingWindowsExtendedPath $path
  if (-not $path -or -not [System.IO.Path]::IsPathRooted([string]$path)) {
    throw "$label is not absolute"
  }
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "$label is unavailable"
  }
  $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$label is a reparse point"
  }
  if ([string]$item.LinkType -ceq "HardLink") {
    throw "$label has multiple links"
  }
  return $item.FullName
}
function Test-BidKingSamePath($left, $right) {
  $leftFull = [System.IO.Path]::GetFullPath(
    (ConvertFrom-BidKingWindowsExtendedPath $left)
  ).TrimEnd('\', '/')
  $rightFull = [System.IO.Path]::GetFullPath(
    (ConvertFrom-BidKingWindowsExtendedPath $right)
  ).TrimEnd('\', '/')
  return [string]::Equals($leftFull, $rightFull, [StringComparison]::OrdinalIgnoreCase)
}
function Test-BidKingContainedPath($path, $root) {
  $candidate = [System.IO.Path]::GetFullPath([string]$path)
  $boundary = [System.IO.Path]::GetFullPath([string]$root).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
  return $candidate.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)
}
function Assert-BidKingExistingChildChain($root, $parts, $label) {
  $current = Assert-BidKingPlainDirectory $root $label
  foreach ($part in @($parts)) {
    $current = Join-Path $current $part
    if (-not (Test-BidKingContainedPath $current $root)) { throw "$label escaped root" }
    # A missing component means the active log tree does not exist yet, not that this
    # shorter prefix becomes the log authority.  Keep calculating the fixed terminal
    # path so a monitor that creates the tree later is still read only from
    # LocalAppData\BidKingLive\logs\live.  Every component that already exists remains
    # fail-closed for files and reparse points.
    if (Test-Path -LiteralPath $current) {
      $current = Assert-BidKingPlainDirectory $current $label
    }
  }
  return [System.IO.Path]::GetFullPath($current)
}
function New-BidKingPlainChildDirectory($root, $name, $label) {
  $candidate = Join-Path $root $name
  if (-not (Test-BidKingContainedPath $candidate $root)) { throw "$label escaped root" }
  if (-not (Test-Path -LiteralPath $candidate)) {
    $null = New-Item -ItemType Directory -Path $candidate -ErrorAction Stop
  }
  return Assert-BidKingPlainDirectory $candidate $label
}
function Resolve-BidKingDiagnosticPackageRoot($invocationPath, $scriptRoot, $environmentRoot) {
  $invocationPath = [string]$invocationPath
  $scriptRoot = [string]$scriptRoot
  $environmentRoot = [string]$environmentRoot

  if ($invocationPath) {
    # A normal -File/direct invocation is authoritative only for the exact
    # package-root\scripts\collect_diagnostics.ps1 leaf that PowerShell opened.
    $scriptFile = Assert-BidKingPlainSingleLinkFile $invocationPath "diagnostic script"
    $actualScriptRoot = Assert-BidKingPlainDirectory `
      (Split-Path -Parent $scriptFile) "diagnostic script directory"
    if (-not $scriptRoot) { throw "diagnostic script root is unavailable" }
    $declaredScriptRoot = Assert-BidKingPlainDirectory `
      (ConvertFrom-BidKingWindowsExtendedPath $scriptRoot) "diagnostic script root"
    if (-not (Test-BidKingSamePath $actualScriptRoot $declaredScriptRoot)) {
      throw "diagnostic script root mismatch"
    }
    $packageRoot = Assert-BidKingPlainDirectory `
      (Split-Path -Parent $actualScriptRoot) "package root"
    $expectedScript = Join-Path $packageRoot "scripts\collect_diagnostics.ps1"
    if (-not (Test-BidKingContainedPath $scriptFile $packageRoot) -or
        -not (Test-BidKingSamePath $scriptFile $expectedScript)) {
      throw "diagnostic script is outside package root"
    }
    if ($environmentRoot) {
      $declaredPackageRoot = Assert-BidKingPlainDirectory `
        (ConvertFrom-BidKingWindowsExtendedPath $environmentRoot) "package root environment"
      if (-not (Test-BidKingSamePath $packageRoot $declaredPackageRoot)) {
        throw "package root environment mismatch"
      }
    }
    return $packageRoot
  }

  # The native lifecycle deliberately executes the verified diagnostics payload
  # as an in-memory ScriptBlock, so neither MyInvocation.Path nor PSScriptRoot is
  # available.  In that mode the native ABI supplies the canonical package root
  # and starts PowerShell in the same directory.  Bind both identities and a
  # single-link package manifest; never infer a root from cwd alone.
  if ($scriptRoot) { throw "in-memory diagnostic script root mismatch" }
  if (-not $environmentRoot) { throw "package root environment is unavailable" }
  $packageRoot = Assert-BidKingPlainDirectory `
    (ConvertFrom-BidKingWindowsExtendedPath $environmentRoot) "package root environment"
  $location = Get-Location
  if (-not $location.Provider -or $location.Provider.Name -cne "FileSystem" -or
      -not (Test-BidKingSamePath $packageRoot $location.Path)) {
    throw "package root environment is not the lifecycle working directory"
  }
  $manifest = Assert-BidKingPlainSingleLinkFile `
    (Join-Path $packageRoot "BUILD_MANIFEST.txt") "package manifest"
  if (-not (Test-BidKingContainedPath $manifest $packageRoot)) {
    throw "package manifest escaped package root"
  }
  return $packageRoot
}

$Repo = Resolve-BidKingDiagnosticPackageRoot `
  $MyInvocation.MyCommand.Path $PSScriptRoot $env:BIDKING_PACKAGE_ROOT
$LocalAppDataRoot = Assert-BidKingPlainDirectory `
  (Get-BidKingKnownFolderPath "LocalApplicationData") "LocalAppData Known Folder"
$LogPath = Assert-BidKingExistingChildChain `
  $LocalAppDataRoot @("BidKingLive", "logs", "live") "live log path"
if (-not (Test-BidKingContainedPath $LogPath $LocalAppDataRoot)) {
  throw "live log path escaped LocalAppData"
}

# 输出文件与悬浮窗“导出”统一到系统 Known Folder 文档\BidKingLive\exports。
# 不向包根、LocalAppData、桌面或 cwd 回退；Known Folder 不可用/不安全时 fail closed。
$DocumentsRoot = Assert-BidKingPlainDirectory `
  (Get-BidKingKnownFolderPath "Documents") "Documents Known Folder"
$ProductDocuments = New-BidKingPlainChildDirectory $DocumentsRoot "BidKingLive" "diagnostic output"
$ExportDir = New-BidKingPlainChildDirectory $ProductDocuments "exports" "diagnostic output"
if (-not (Test-BidKingContainedPath $ExportDir $DocumentsRoot)) {
  throw "diagnostic output escaped Documents"
}
$stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
$outFile = $null
foreach ($suffix in 1..999) {
  $tail = if ($suffix -eq 1) { "" } else { "-$suffix" }
  $candidate = Join-Path $ExportDir "BidKing诊断_$stamp$tail.txt"
  if (-not (Test-Path -LiteralPath $candidate)) {
    $outFile = $candidate
    break
  }
}
if (-not $outFile) { throw "no unique diagnostic output name available" }

$sb = New-Object System.Text.StringBuilder
function Protect-DiagnosticText($value) {
  $text = [string]$value
  # Raw settlement forensics contain an opaque application body. Never attempt
  # field-level guessing: omit the complete line from support diagnostics.
  if ($text -match '(?i)\[settle-raw\]') { return '<raw-forensics-line-omitted>' }
  $opaquePrefix = '(?i)(?<prefix>(?<![A-Za-z0-9_])"?(?:bodyb64|frame_base64|Data)"?\s*[:=]\s*)'
  $payloadPrefix = '(?i)(?<prefix>(?<![A-Za-z0-9_])"?payload"?\s*[:=]\s*)'
  $quotedBody = '"(?:\\.|[^"\\])*"'
  $validQuotedBoundary = '(?=$|[\s,;}\]])'
  # A quoted field with no closing quote, or a closing quote followed by an
  # ordinary character, is syntactically unsafe. Drop the rest of that line so
  # neither a truncated tail nor a safe-prefix bypass can survive.
  $invalidQuotedBoundary = $quotedBody + '(?=[^\s,;}\]])[^\r\n]*$'
  $unterminatedQuoted = '"(?:\\.|[^"\\])*$'
  $dropUnsafeQuoted = [Text.RegularExpressions.MatchEvaluator]{
    param($match)
    return $match.Groups['prefix'].Value + '<redacted>'
  }
  foreach ($prefixPattern in @($opaquePrefix, $payloadPrefix)) {
    $text = [regex]::Replace($text, $prefixPattern + $invalidQuotedBoundary, $dropUnsafeQuoted)
    $text = [regex]::Replace($text, $prefixPattern + $unterminatedQuoted, $dropUnsafeQuoted)
  }
  # Valid opaque quoted values are always removed. Unquoted values are limited
  # to one token and may never fall back from an opening quote.
  $opaqueQuotedPattern = $opaquePrefix + '(?<value>' + $quotedBody + ')' + $validQuotedBoundary
  $text = [regex]::Replace($text, $opaqueQuotedPattern, [Text.RegularExpressions.MatchEvaluator]{
    param($match)
    return $match.Groups['prefix'].Value + '<redacted>'
  })
  $opaqueUnquotedPattern = $opaquePrefix + '(?<value>(?!")[^\s,;}\]]+)'
  $text = [regex]::Replace($text, $opaqueUnquotedPattern, [Text.RegularExpressions.MatchEvaluator]{
    param($match)
    return $match.Groups['prefix'].Value + '<redacted>'
  })
  # Payload is safe only when its complete semantic value is exactly digits+B.
  # Everything else is opaque application data and must be fully removed.
  $payloadEvaluator = [Text.RegularExpressions.MatchEvaluator]{
    param($match)
    $rawValue = $match.Groups['value'].Value
    $semanticValue = $rawValue
    if ($rawValue.Length -ge 2 -and $rawValue[0] -eq '"' -and
        $rawValue[$rawValue.Length - 1] -eq '"') {
      $semanticValue = $rawValue.Substring(1, $rawValue.Length - 2)
    }
    if ($semanticValue -cmatch '^\d+B$') { return $match.Value }
    return $match.Groups['prefix'].Value + '<redacted>'
  }
  $payloadQuotedPattern = $payloadPrefix + '(?<value>' + $quotedBody + ')' + $validQuotedBoundary
  $text = [regex]::Replace($text, $payloadQuotedPattern, $payloadEvaluator)
  $payloadUnquotedPattern = $payloadPrefix + '(?<value>(?!")[^\s,;}\]]+)'
  $text = [regex]::Replace($text, $payloadUnquotedPattern, $payloadEvaluator)
  $text = [regex]::Replace($text, '(?i)\bv\d+\|[^\r\n\s]*\.[0-9a-f]{16,}\b', '<activation-token>')
  $text = [regex]::Replace($text, '(?i)\bBearer\s+[^\s,;]+', 'Bearer <redacted>')
  $text = [regex]::Replace($text, '(?i)\b(token|authorization|cookie|password|passwd|secret|api[_-]?key|master[_-]?key|upload[_-]?key|ingest[_-]?key|device[_-]?id|machine[_-]?guid|uid(?:_hash)?|user(?:name|_id)?|player(?:_name)?|winner|bidder|email|phone|qq)(\s*[:=]\s*)([^\s,;]+)', '$1$2<redacted>')
  $text = [regex]::Replace($text, '(?i)([?&](?:token|key|secret|password|auth|authorization)=)[^&#\s]+', '$1<redacted>')
  # The support report needs path-health facts, not the user's absolute install
  # or profile roots. Replace the known local roots before the generic user
  # placeholder so package paths under Desktop do not retain private segments.
  $privateRoots = @(
    [pscustomobject]@{ Value = $Repo; Replacement = '<package-root>' },
    [pscustomobject]@{ Value = $LocalAppDataRoot; Replacement = '<local-appdata>' },
    [pscustomobject]@{ Value = $DocumentsRoot; Replacement = '<documents>' },
    [pscustomobject]@{ Value = $env:APPDATA; Replacement = '<appdata>' },
    [pscustomobject]@{ Value = $env:USERPROFILE; Replacement = '<user-profile>' }
  ) | Where-Object { $_.Value } | Sort-Object { ([string]$_.Value).Length } -Descending
  foreach ($privateRoot in $privateRoots) {
    $rootText = ([string]$privateRoot.Value).TrimEnd('\', '/')
    if ($rootText) {
      $text = [regex]::Replace(
        $text,
        [regex]::Escape($rootText) + '(?=$|[\\/])',
        [string]$privateRoot.Replacement,
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
      )
    }
  }
  $text = [regex]::Replace($text, '(?i)\b[A-Z]:\\Users\\[^\\/\s]+', 'C:\Users\<user>')
  $text = [regex]::Replace($text, '(?i)(?:/home|/Users)/[^/\s]+', '/home/<user>')
  $text = [regex]::Replace($text, '(?i)\\Device\\NPF_\{[0-9a-f-]{36}\}', '<npcap-device>')
  $text = [regex]::Replace($text, '(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '<guid>')
  $text = [regex]::Replace($text, '(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b', '<mac>')
  $text = [regex]::Replace($text, '\b(?:\d{1,3}\.){3}\d{1,3}\b', '<ip>')
  $text = [regex]::Replace($text, '(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', '<email>')
  return $text
}
function Add-Line($t) { [void]$sb.AppendLine((Protect-DiagnosticText $t)) }
function Add-Sec($t)  { Add-Line ""; Add-Line ("=" * 60); Add-Line "  $t"; Add-Line ("=" * 60) }

function Add-BidKingStartupTimingSummary {
  param([string]$Directory, [switch]$CaptureOnly)
  $script:BidKingNativeStartupSummaryLines = [Collections.Generic.List[string]]::new()
  $script:BidKingCurrentNativeStartup = $null
  function Write-BidKingStartupSummaryLine([string]$Value) {
    $script:BidKingNativeStartupSummaryLines.Add($Value)
    if (-not $CaptureOnly) { Add-Line $Value }
  }
  $ReadNativeProjection = {
    param($Run)
    try {
      $Path = Join-Path $Directory ($Run.RunId + "--cleanup-bootstrap.json")
      $File = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
      if ($File.PSIsContainer -or ($File.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
          $File.Length -le 0 -or $File.Length -gt 4096) { return $null }
      # Limit allocation/read even if the file grows after the initial stat.
      [byte[]]$Buffer = [byte[]]::new(4097)
      $Stream = [IO.File]::OpenRead($Path)
      try {
        $Read = 0
        while ($Read -lt $Buffer.Length) {
          $Count = $Stream.Read($Buffer, $Read, $Buffer.Length - $Read)
          if ($Count -eq 0) { break }
          $Read += $Count
        }
      } finally { $Stream.Dispose() }
      if ($Read -le 0 -or $Read -gt 4096) { return $null }
      [byte[]]$Raw = $Buffer[0..($Read - 1)]
      $Text = [Text.UTF8Encoding]::new($false, $true).GetString($Raw)
      # Exact producer shape: duplicate/extra keys, coercions, arbitrary strings,
      # or the old observation without its fixed projection are not current evidence.
      $Shape = '\A\{"schema":"bidking_startup_cleanup_v1","run_id":"(?<run>[0-9a-f]{32})","phase":"bootstrap","acl_restore":"[a-z_]+","handle_release":"[a-z_]+","primary_error":(?:true|false),"primary_exit_code":(?:null|0|[1-9][0-9]*),"powershell_attempted":(?:true|false),"acl_restore_failed_count":(?:null|0|[1-9][0-9]*),"acl_restore_attempted_count":(?:null|0|[1-9][0-9]*),"primary_exception_preserved":(?:true|false),"monitor":"[a-z_]+","overlay":"[a-z_]+","message_key":"[a-z_]+","failure_projection_version":1,"primary_failure_stage":"[a-z_]+","primary_exception_class":"[A-Za-z]+","primary_reason_code":"[a-z_]+","primary_errno":(?:null|0|[1-9][0-9]*),"primary_winerror":(?:null|0|[1-9][0-9]*)\}\z'
      $Match = [regex]::Match($Text, $Shape, [Text.RegularExpressions.RegexOptions]::CultureInvariant)
      if (-not $Match.Success -or $Match.Groups["run"].Value -cne $Run.RunId) { return $null }
      $Value = $Text | ConvertFrom-Json -ErrorAction Stop
      $Stages = @("none", "unknown", "kernel_import", "integrity_verify", "namespace_guard_import", "namespace_lease", "integrity_recheck", "launcher_import", "managed_launch")
      $Classes = @("none", "unknown", "RuntimeError", "PermissionError", "FileNotFoundError", "FileExistsError", "NotADirectoryError", "IsADirectoryError", "TimeoutError", "OSError", "ModuleNotFoundError", "ImportError", "ValueError", "TypeError", "SystemExit", "NamespaceGuardError", "NamespaceRestoreError")
      $Reasons = @(
        "none", "unknown", "bootstrap_exact_loader_unavailable", "managed_exit_nonzero",
        "integrity_member_path_invalid", "integrity_reparse_or_symlink_rejected", "integrity_file_open_failed",
        "integrity_stream_namespace_invalid", "integrity_stream_inventory_unavailable", "integrity_stream_read_failed",
        "integrity_stream_identity_changed", "integrity_file_identity_unavailable", "integrity_file_missing",
        "integrity_file_type_invalid", "integrity_named_stream_rejected", "integrity_directory_missing",
        "integrity_directory_type_invalid", "integrity_directory_open_failed", "integrity_directory_identity_changed",
        "integrity_directory_identity_unavailable", "integrity_file_read_failed", "integrity_file_identity_changed",
        "integrity_coverage_invalid", "integrity_coverage_unreadable", "integrity_namespace_unreadable",
        "integrity_namespace_invalid", "integrity_member_identity_collision", "integrity_signature_encoding_invalid",
        "integrity_public_key_missing", "integrity_signature_invalid", "integrity_public_key_invalid",
        "integrity_manifest_invalid", "integrity_file_size_mismatch", "integrity_file_hash_mismatch",
        "integrity_coverage_mismatch", "integrity_handle_retention_failed", "integrity_namespace_retention_failed",
        "integrity_namespace_not_verified", "integrity_namespace_root_changed", "integrity_namespace_changed",
        "integrity_stream_namespace_changed", "integrity_handle_release_failed",
        "namespace_guard_windows_only", "namespace_directory_unreadable", "namespace_reparse_rejected",
        "namespace_directory_type_invalid", "namespace_file_type_invalid", "namespace_owner_unreadable",
        "namespace_security_read_failed", "namespace_security_policy_invalid", "namespace_security_apply_failed",
        "namespace_security_apply_unverified", "namespace_root_identity_unavailable", "namespace_named_stream_rejected",
        "namespace_stream_inventory_unavailable", "namespace_stream_inventory_invalid", "namespace_recovery_path_invalid",
        "namespace_recovery_invalid", "namespace_restore_verification_failed", "namespace_recovery_acl_invalid",
        "namespace_write_denial_unproven", "namespace_stream_write_denial_unproven", "namespace_lock_attestation_failed",
        "namespace_file_inventory_empty", "namespace_recovery_cleanup_failed", "namespace_guard_elevation_required",
        "namespace_recovery_root_missing", "namespace_restore_incomplete", "client_lifecycle_unavailable",
        "client_lifecycle_request_rejected", "client_lifecycle_busy", "client_lifecycle_payload_invalid",
        "client_lifecycle_start_failed", "native_module_missing", "native_module_load_failed", "native_symbol_missing",
        "native_access_denied", "native_exception", "native_status_invalid", "native_status_nonzero"
      )
      $Children = @("not_created", "exited", "reclaimed", "alive", "reused_preserved", "preserved_unknown", "preserved_changed", "preserved_game", "cleanup_failed", "unknown")
      $Messages = @("startup_failed", "unsupported_system", "runtime_missing", "component_missing", "runtime_probe_failed", "close_game_first", "lock_changed", "lock_cleanup_failed", "npcap_install_incomplete", "npcap_installer_missing")
      if ($Stages -cnotcontains $Value.primary_failure_stage -or $Classes -cnotcontains $Value.primary_exception_class -or
          $Reasons -cnotcontains $Value.primary_reason_code -or $Children -cnotcontains $Value.monitor -or $Children -cnotcontains $Value.overlay -or
          $Messages -cnotcontains $Value.message_key -or
          @("not_acquired", "restored", "failed", "unknown") -cnotcontains $Value.acl_restore -or
          @("released", "failed", "unknown") -cnotcontains $Value.handle_release) { return $null }
      foreach ($Field in @("primary_errno", "primary_winerror", "primary_exit_code", "acl_restore_failed_count", "acl_restore_attempted_count")) {
        if ($null -ne $Value.$Field) {
          $Limit = if ($Field -ceq "primary_exit_code") { 255 } elseif ($Field -like "acl_*") { 100000 } else { 2147483647 }
          if (($Value.$Field -isnot [int] -and $Value.$Field -isnot [long]) -or $Value.$Field -lt 0 -or $Value.$Field -gt $Limit) { return $null }
        }
      }
      $Prerequisite = switch -CaseSensitive ($Value.primary_failure_stage) {
        "kernel_import" { "kernel_import" }
        "integrity_verify" { "kernel_import" }
        "namespace_guard_import" { "kernel_namespace" }
        "namespace_lease" { "kernel_namespace" }
        "integrity_recheck" { "kernel_namespace" }
        "launcher_import" { "integrity_recheck" }
        "managed_launch" { "integrity_recheck" }
        default { $null }
      }
      if ($Prerequisite) {
        $RequiredResult = if ($Value.primary_failure_stage -ceq "kernel_import") { "begin" } else { "ok" }
        if (@($Run.Rows | Where-Object { $_.Event -ceq $Prerequisite -and $_.Result -ceq $RequiredResult }).Count -ne 1) { return $null }
      }
      if ($Value.primary_exception_preserved -and $Value.primary_exception_class -ceq "none") { return $null }
      if (-not $Value.primary_exception_preserved -and $Value.primary_exception_class -cne "none") { return $null }
      if ($Value.primary_exception_preserved -and -not $Value.primary_error) { return $null }
      if ($Value.primary_failure_stage -ceq "managed_launch" -and -not $Value.powershell_attempted) { return $null }
      if ($Value.primary_failure_stage -notin @("managed_launch", "none", "unknown") -and $Value.powershell_attempted) { return $null }
      return $Value
    } catch { return $null }
  }
  $Events = @(
    "root_entry", "preload", "python_spawn", "kernel_import",
    "kernel_manifest", "kernel_coverage", "kernel_namespace", "acl_inventory",
    "acl_journal", "acl_apply", "acl_attest", "integrity_recheck", "lifecycle",
    "powershell_spawn", "powershell_launch", "powershell_generation",
    "powershell_component", "powershell_certificate", "powershell_dependency",
    "powershell_process_identity", "powershell_monitor", "powershell_overlay",
    "tk_map", "tk_after_idle", "startup_guard", "startup_terminal",
    "acl_restore", "root_exit"
  )
  $Results = @("begin", "ok", "failed", "skipped", "unavailable")
  $Topologies = @(
    "unknown", "cold_start", "current_current", "current_absent",
    "current_stale", "current_unsafe", "rapid_reentry", "diagnostics", "repair"
  )
  $TerminalResults = @("ok", "failed", "skipped", "unavailable")
  $SingletonSkippedEvents = @(
    "powershell_component", "powershell_certificate", "powershell_overlay",
    "startup_guard"
  )
  $ProgressEvents = @(
    "root_entry", "preload", "kernel_import", "python_spawn",
    "kernel_manifest", "kernel_coverage", "kernel_namespace", "acl_inventory",
    "acl_journal", "acl_apply", "acl_attest", "integrity_recheck", "lifecycle",
    "powershell_spawn", "powershell_launch", "powershell_generation",
    "powershell_process_identity", "powershell_certificate", "powershell_component",
    "powershell_dependency", "powershell_monitor", "powershell_overlay", "tk_map",
    "tk_after_idle", "startup_guard", "startup_terminal"
  )
  try {
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
      Write-BidKingStartupSummaryLine "  [startup_timing] result=timing_unavailable reason=invalid"
      return
    }
    $Files = @(Get-ChildItem -LiteralPath $Directory -Filter "*.json" -File -Force -ErrorAction Stop)
    # Eight retained runs x 28 fixed events x five fixed results is at most
    # 1,120 writer-owned JSON files.  Keep a small bounded allowance while
    # rejecting an unbounded or foreign input set.
    if ($Files.Count -le 0 -or $Files.Count -gt 1280) {
      Write-BidKingStartupSummaryLine "  [startup_timing] result=timing_unavailable reason=invalid"
      return
    }
    $Candidates = @{}
    foreach ($File in $Files) {
      $NameMatch = [regex]::Match(
        $File.Name,
        '\A(?<run>[0-9a-f]{32})--(?<event>[a-z_]+)--(?<result>[a-z_]+)\.json\z',
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
      )
      if (-not $NameMatch.Success) {
        continue
      }
      $NameRunId = $NameMatch.Groups["run"].Value
      if (-not $Candidates.ContainsKey($NameRunId)) {
        $Candidates[$NameRunId] = [pscustomobject]@{
          RunId = $NameRunId
          Rows = [Collections.Generic.List[object]]::new()
          Invalid = $false
          Modified = [DateTime]::MinValue
        }
      }
      $Candidate = $Candidates[$NameRunId]
      if ($File.LastWriteTimeUtc -gt $Candidate.Modified) {
        $Candidate.Modified = $File.LastWriteTimeUtc
      }
      try {
        [byte[]]$Raw = [IO.File]::ReadAllBytes($File.FullName)
        if ($Raw.Length -le 0 -or $Raw.Length -gt 1024) { throw "invalid" }
        $Text = [Text.UTF8Encoding]::new($false, $true).GetString($Raw)
        $Match = [regex]::Match(
          $Text,
          '\A\{"schema":"bidking_startup_timing_v1","schema_version":1,"run_id":"(?<run>[0-9a-f]{32})","event":"(?<event>[a-z_]+)","result":"(?<result>[a-z_]+)","topology":"(?<topology>[a-z_]+)","elapsed_ms":(?<elapsed>0|[1-9][0-9]*)\}\z',
          [Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
        if (-not $Match.Success) { throw "invalid" }
        $RunId = $Match.Groups["run"].Value
        $Event = $Match.Groups["event"].Value
        $Result = $Match.Groups["result"].Value
        $Topology = $Match.Groups["topology"].Value
        [long]$Elapsed = 0
        if (
          $RunId -cne $NameRunId -or
          $Event -cne $NameMatch.Groups["event"].Value -or
          $Result -cne $NameMatch.Groups["result"].Value -or
          $Events -cnotcontains $Event -or
          $Results -cnotcontains $Result -or
          $Topologies -cnotcontains $Topology -or
          -not [long]::TryParse(
            $Match.Groups["elapsed"].Value,
            [Globalization.NumberStyles]::None,
            [Globalization.CultureInfo]::InvariantCulture,
            [ref]$Elapsed
          ) -or
          $Elapsed -lt 0 -or $Elapsed -gt 604800000 -or
          $File.Name -cne ("{0}--{1}--{2}.json" -f $RunId, $Event, $Result)
        ) { throw "invalid" }
        $Phase = if ($Result -ceq "begin") { "begin" } else { "terminal" }
        $Candidate.Rows.Add([pscustomobject]@{
          RunId = $RunId
          Event = $Event
          Result = $Result
          Topology = $Topology
          Elapsed = $Elapsed
          Modified = $File.LastWriteTimeUtc
          Phase = $Phase
          Key = ("{0}:{1}" -f $Event, $Phase)
        })
      } catch {
        $Candidate.Invalid = $true
      }
    }

    $ValidateRun = {
      param($Candidate)
      $Invalid = {
        [pscustomobject]@{
          Valid = $false
          RunId = $Candidate.RunId
          Complete = $false
          Outcome = "invalid"
          Prefix = "invalid"
          Started = $Candidate.Modified
          Rows = @()
        }
      }
      if ($Candidate.Invalid -or $Candidate.Rows.Count -le 0) { return & $Invalid }
      $Rows = @($Candidate.Rows)
      $PhaseRows = @{}
      foreach ($Row in $Rows) {
        if ($PhaseRows.ContainsKey($Row.Key)) { return & $Invalid }
        $PhaseRows[$Row.Key] = $Row
      }
      foreach ($Stage in @($Rows | Group-Object Event)) {
        $Begins = @($Stage.Group | Where-Object { $_.Result -ceq "begin" })
        $Terminals = @($Stage.Group | Where-Object { $TerminalResults -ccontains $_.Result })
        if ($Begins.Count -gt 1 -or $Terminals.Count -gt 1) { return & $Invalid }
        if ($Begins.Count -eq 1 -and $Terminals.Count -eq 1 -and
            $Terminals[0].Elapsed -lt $Begins[0].Elapsed) { return & $Invalid }
        if ($Begins.Count -eq 0 -and $Terminals.Count -eq 1) {
          $AllowedSingleton = (
            ($SingletonSkippedEvents -ccontains $Stage.Name -and $Terminals[0].Result -ceq "skipped") -or
            $Stage.Name -ceq "startup_terminal" -or
            $Stage.Name -ceq "root_exit"
          )
          if (-not $AllowedSingleton) { return & $Invalid }
        }
      }
      $RootEntries = @($Rows | Where-Object { $_.Event -ceq "root_entry" })
      $RootExits = @($Rows | Where-Object { $_.Event -ceq "root_exit" })
      if ($RootEntries.Count -ne 1 -or $RootEntries[0].Result -cne "begin" -or
          $RootExits.Count -ne 1 -or
          $RootExits[0].Result -notin @("ok", "failed")) { return & $Invalid }

      $Prerequisites = @{
        preload = @("root_entry:begin", "begin")
        kernel_import = @("preload:terminal", "ok")
        python_spawn = @("kernel_import:begin", "begin")
        kernel_manifest = @("kernel_import:terminal", "ok")
        kernel_coverage = @("kernel_manifest:terminal", "ok")
        kernel_namespace = @("kernel_coverage:terminal", "ok")
        acl_inventory = @("kernel_namespace:terminal", "ok")
        acl_journal = @("acl_inventory:terminal", "ok")
        acl_apply = @("acl_journal:terminal", "ok")
        acl_attest = @("acl_apply:terminal", "ok")
        integrity_recheck = @("acl_attest:terminal", "ok")
        lifecycle = @("integrity_recheck:terminal", "ok")
        powershell_spawn = @("lifecycle:begin", "begin")
        powershell_launch = @("powershell_spawn:terminal", "ok")
        powershell_generation = @("powershell_launch:begin", "begin")
        powershell_process_identity = @("powershell_generation:terminal", "ok")
        powershell_certificate = @("powershell_process_identity:terminal", "ok")
        powershell_component = @("powershell_process_identity:terminal", "ok")
        powershell_dependency = @("powershell_certificate:terminal", "ok", "skipped")
        powershell_monitor = @("powershell_dependency:terminal", "ok")
        powershell_overlay = @("powershell_monitor:terminal", "ok")
        tk_map = @("powershell_overlay:begin", "begin")
        tk_after_idle = @("tk_map:terminal", "ok")
        startup_guard = @("powershell_overlay:terminal", "ok", "skipped")
        startup_terminal = @("lifecycle:begin", "begin")
        acl_restore = @("acl_attest:terminal", "ok")
        root_exit = @("root_entry:begin", "begin")
      }
      foreach ($EventName in @($Rows.Event | Sort-Object -Unique)) {
        if (-not $Prerequisites.ContainsKey($EventName)) { continue }
        $Requirement = $Prerequisites[$EventName]
        $RequiredKey = $Requirement[0]
        if (-not $PhaseRows.ContainsKey($RequiredKey) -or
            $Requirement[1..($Requirement.Count - 1)] -cnotcontains $PhaseRows[$RequiredKey].Result) {
          return & $Invalid
        }
      }

      $NonUnknownTopologies = @(
        $Rows.Topology | Where-Object { $_ -cne "unknown" } | Sort-Object -Unique
      )
      if ($NonUnknownTopologies.Count -gt 1) { return & $Invalid }
      $Topology = if ($NonUnknownTopologies.Count -eq 1) {
        $NonUnknownTopologies[0]
      } else {
        "unknown"
      }

      $Adjacency = @{}
      $AddEdge = {
        param([string]$From, [string]$To)
        if ($From -ceq $To) { return }
        if (-not $Adjacency.ContainsKey($From)) {
          $Adjacency[$From] = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        }
        [void]$Adjacency[$From].Add($To)
      }
      foreach ($EventName in $Events) { & $AddEdge "$EventName`:begin" "$EventName`:terminal" }
      $Edges = @(
        "root_entry:begin>preload:begin",
        "preload:terminal>kernel_import:begin",
        "kernel_import:begin>python_spawn:begin",
        "python_spawn:begin>kernel_import:terminal",
        "kernel_import:terminal>kernel_manifest:begin",
        "kernel_manifest:terminal>kernel_coverage:begin",
        "kernel_coverage:terminal>kernel_namespace:begin",
        "kernel_namespace:terminal>acl_inventory:begin",
        "acl_inventory:terminal>acl_journal:begin",
        "acl_journal:terminal>acl_apply:begin",
        "acl_apply:terminal>acl_attest:begin",
        "acl_attest:terminal>integrity_recheck:begin",
        "integrity_recheck:terminal>lifecycle:begin",
        "lifecycle:begin>powershell_spawn:begin",
        "powershell_spawn:terminal>powershell_launch:begin",
        "powershell_launch:begin>powershell_generation:begin",
        "powershell_generation:terminal>powershell_process_identity:begin",
        "powershell_process_identity:terminal>powershell_certificate:begin",
        "powershell_process_identity:terminal>powershell_component:terminal",
        "powershell_certificate:begin>powershell_component:begin",
        "powershell_component:terminal>powershell_certificate:terminal",
        "powershell_certificate:terminal>powershell_dependency:begin",
        "powershell_dependency:terminal>powershell_monitor:begin",
        "powershell_monitor:terminal>powershell_overlay:begin",
        "powershell_monitor:terminal>powershell_overlay:terminal",
        "powershell_overlay:begin>tk_map:begin",
        "tk_map:terminal>tk_after_idle:begin",
        "powershell_overlay:terminal>startup_guard:begin",
        "powershell_overlay:terminal>startup_guard:terminal",
        "startup_guard:terminal>powershell_launch:terminal",
        "powershell_process_identity:terminal>powershell_launch:terminal",
        "powershell_launch:terminal>startup_terminal:terminal",
        "powershell_spawn:terminal>lifecycle:terminal",
        "lifecycle:terminal>acl_restore:begin",
        "acl_restore:terminal>python_spawn:terminal",
        "python_spawn:terminal>root_exit:terminal"
      )
      foreach ($Edge in $Edges) {
        $Separator = $Edge.IndexOf(">", [StringComparison]::Ordinal)
        if ($Separator -le 0 -or $Separator -ge ($Edge.Length - 1)) { return & $Invalid }
        & $AddEdge $Edge.Substring(0, $Separator) $Edge.Substring($Separator + 1)
      }
      if ($Topology -ceq "rapid_reentry") {
        & $AddEdge "lifecycle:terminal" "startup_terminal:terminal"
        & $AddEdge "startup_terminal:terminal" "acl_restore:begin"
      } else {
        & $AddEdge "startup_terminal:terminal" "lifecycle:terminal"
      }
      foreach ($Key in @($PhaseRows.Keys)) {
        if ($Key -cne "root_entry:begin") { & $AddEdge "root_entry:begin" $Key }
        if ($Key -cne "root_exit:terminal") { & $AddEdge $Key "root_exit:terminal" }
      }
      $CanReach = {
        param([string]$From, [string]$To)
        if ($From -ceq $To) { return $true }
        $Seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        $Stack = [Collections.Generic.Stack[string]]::new()
        $Stack.Push($From)
        while ($Stack.Count -gt 0) {
          $CurrentKey = $Stack.Pop()
          if (-not $Seen.Add($CurrentKey)) { continue }
          if (-not $Adjacency.ContainsKey($CurrentKey)) { continue }
          foreach ($NextKey in $Adjacency[$CurrentKey]) {
            if ($NextKey -ceq $To) { return $true }
            $Stack.Push($NextKey)
          }
        }
        return $false
      }
      for ($Left = 0; $Left -lt $Rows.Count; $Left++) {
        for ($Right = $Left + 1; $Right -lt $Rows.Count; $Right++) {
          $A = $Rows[$Left]
          $B = $Rows[$Right]
          $AB = & $CanReach $A.Key $B.Key
          $BA = & $CanReach $B.Key $A.Key
          if (($AB -and $BA) -or
              ($AB -and $A.Elapsed -gt $B.Elapsed) -or
              ($BA -and $B.Elapsed -gt $A.Elapsed) -or
              ($A.Elapsed -eq $B.Elapsed -and -not $AB -and -not $BA)) {
            return & $Invalid
          }
        }
      }
      if ($Topology -cne "unknown") {
        $FirstTopologyRows = @($Rows | Where-Object { $_.Topology -cne "unknown" })
        foreach ($UnknownRow in @($Rows | Where-Object { $_.Topology -ceq "unknown" })) {
          foreach ($KnownRow in $FirstTopologyRows) {
            if ($UnknownRow.Elapsed -gt $KnownRow.Elapsed -or
                ($UnknownRow.Elapsed -eq $KnownRow.Elapsed -and
                 -not (& $CanReach $UnknownRow.Key $KnownRow.Key))) {
              return & $Invalid
            }
          }
        }
      }

      $SortedRows = [Collections.Generic.List[object]]::new()
      foreach ($ElapsedGroup in @($Rows | Group-Object Elapsed | Sort-Object { [long]$_.Name })) {
        $Remaining = [Collections.Generic.List[object]]::new()
        foreach ($Row in $ElapsedGroup.Group) { $Remaining.Add($Row) }
        while ($Remaining.Count -gt 0) {
          $Ready = @(
            $Remaining | Where-Object {
              $CandidateRow = $_
              -not @($Remaining | Where-Object {
                $_.Key -cne $CandidateRow.Key -and (& $CanReach $_.Key $CandidateRow.Key)
              })
            } | Sort-Object Key | Select-Object -First 1
          )
          if (-not $Ready) { return & $Invalid }
          $SortedRows.Add($Ready[0])
          [void]$Remaining.Remove($Ready[0])
        }
      }

      $Started = $RootEntries[0].Modified
      if ($RootExits[0].Result -ceq "failed") {
        $Prefix = "root_entry"
        foreach ($EventName in $ProgressEvents) {
          if ($Rows.Event -ccontains $EventName) { $Prefix = $EventName }
        }
        return [pscustomobject]@{
          Valid = $true
          RunId = $Candidate.RunId
          Complete = $true
          Outcome = "failed"
          Prefix = $Prefix
          Started = $Started
          Rows = @($SortedRows)
        }
      }

      $RequirePairOk = {
        param([string]$EventName)
        $BeginKey = "$EventName`:begin"
        $TerminalKey = "$EventName`:terminal"
        return (
          $PhaseRows.ContainsKey($BeginKey) -and
          $PhaseRows.ContainsKey($TerminalKey) -and
          $PhaseRows[$TerminalKey].Result -ceq "ok"
        )
      }
      $MandatoryPairs = @(
        "preload", "kernel_import", "python_spawn", "kernel_manifest",
        "kernel_coverage", "kernel_namespace", "acl_inventory", "acl_journal",
        "acl_apply", "acl_attest", "integrity_recheck", "acl_restore"
      )
      foreach ($EventName in $MandatoryPairs) {
        if (-not (& $RequirePairOk $EventName)) { return & $Invalid }
      }
      if ($Topology -ceq "rapid_reentry") {
        if (
          -not $PhaseRows.ContainsKey("lifecycle:begin") -or
          -not $PhaseRows.ContainsKey("lifecycle:terminal") -or
          $PhaseRows["lifecycle:terminal"].Result -cne "failed" -or
          -not $PhaseRows.ContainsKey("startup_terminal:terminal") -or
          $PhaseRows["startup_terminal:terminal"].Result -cne "skipped" -or
          $PhaseRows.ContainsKey("powershell_spawn:begin")
        ) { return & $Invalid }
      } else {
        if (-not (& $RequirePairOk "lifecycle") -or
            -not (& $RequirePairOk "powershell_spawn")) { return & $Invalid }
      }
      if ($Topology -in @("diagnostics", "repair")) {
        if ($PhaseRows.ContainsKey("powershell_launch:begin")) { return & $Invalid }
      } elseif ($Topology -ne "rapid_reentry") {
        if ($Topology -ceq "unknown" -or
            -not (& $RequirePairOk "powershell_launch") -or
            -not (& $RequirePairOk "powershell_generation") -or
            -not (& $RequirePairOk "powershell_process_identity") -or
            -not $PhaseRows.ContainsKey("startup_terminal:terminal") -or
            $PhaseRows["startup_terminal:terminal"].Result -cne "ok") {
          return & $Invalid
        }
        if ($Topology -cne "current_current") {
          foreach ($OptionalEvent in @("powershell_component", "powershell_certificate")) {
            $BeginKey = "$OptionalEvent`:begin"
            $TerminalKey = "$OptionalEvent`:terminal"
            if (-not $PhaseRows.ContainsKey($TerminalKey) -or
                (($PhaseRows.ContainsKey($BeginKey) -and $PhaseRows[$TerminalKey].Result -cne "ok") -or
                 (-not $PhaseRows.ContainsKey($BeginKey) -and $PhaseRows[$TerminalKey].Result -cne "skipped"))) {
              return & $Invalid
            }
          }
          if (-not (& $RequirePairOk "powershell_dependency") -or
              -not (& $RequirePairOk "powershell_monitor")) { return & $Invalid }
          foreach ($OptionalEvent in @("powershell_overlay", "startup_guard")) {
            $BeginKey = "$OptionalEvent`:begin"
            $TerminalKey = "$OptionalEvent`:terminal"
            if (-not $PhaseRows.ContainsKey($TerminalKey) -or
                (($PhaseRows.ContainsKey($BeginKey) -and $PhaseRows[$TerminalKey].Result -cne "ok") -or
                 (-not $PhaseRows.ContainsKey($BeginKey) -and $PhaseRows[$TerminalKey].Result -cne "skipped"))) {
              return & $Invalid
            }
          }
          if ($PhaseRows["powershell_overlay:terminal"].Result -ceq "ok" -and
              (-not (& $RequirePairOk "tk_map") -or -not (& $RequirePairOk "tk_after_idle"))) {
            return & $Invalid
          }
        }
      }
      foreach ($Row in $Rows) {
        if ($Row.Result -in @("failed", "unavailable")) {
          if (-not ($Topology -ceq "rapid_reentry" -and
                    $Row.Event -ceq "lifecycle" -and $Row.Result -ceq "failed")) {
            return & $Invalid
          }
        }
      }
      return [pscustomobject]@{
        Valid = $true
        RunId = $Candidate.RunId
        Complete = $true
        Outcome = "ok"
        Prefix = "complete"
        Started = $Started
        Rows = @($SortedRows)
      }
    }

    $Evaluated = @(
      foreach ($Candidate in $Candidates.Values) { & $ValidateRun $Candidate }
    )
    $Latest = @(
      $Evaluated |
        Where-Object { $_.Valid -and $_.Complete } |
        Sort-Object Started -Descending |
        Select-Object -First 1
    )
    if (-not $Latest) {
      Write-BidKingStartupSummaryLine "  [startup_timing] result=timing_unavailable reason=invalid"
      return
    }
    if ($Latest.Outcome -ceq "failed") {
      Write-BidKingStartupSummaryLine (
        "  [startup_timing] schema=bidking_startup_timing_v1 result=failed prefix={0} events={1}" -f `
          $Latest.Prefix, $Latest.Rows.Count
      )
    } else {
      Write-BidKingStartupSummaryLine (
        "  [startup_timing] schema=bidking_startup_timing_v1 result=ok events={0}" -f `
          $Latest.Rows.Count
      )
    }
    foreach ($Row in $Latest.Rows) {
      Write-BidKingStartupSummaryLine (
        "    event={0} result={1} topology={2} elapsed_ms={3}" -f `
          $Row.Event, $Row.Result, $Row.Topology, $Row.Elapsed
      )
    }
    # A completed diagnostics/repair run cannot replace the last actual launch's
    # failure. Select that launch independently, then bind its exact cleanup ID.
    $Launch = @(
      $Evaluated | Where-Object {
        $_.Valid -and $_.Complete -and
        @($_.Rows | Where-Object { $_.Topology -in @("diagnostics", "repair") }).Count -eq 0
      } | Sort-Object Started -Descending | Select-Object -First 1
    )
    if ($Launch) {
      $Projection = & $ReadNativeProjection $Launch[0]
      if ($Projection) {
        $script:BidKingCurrentNativeStartup = [pscustomobject]@{
          RunId = $Launch[0].RunId; Started = $Launch[0].Started; Outcome = $Launch[0].Outcome
          ValidProjection = $true; PowershellAttempted = $Projection.powershell_attempted
        }
        $Errno = if ($null -eq $Projection.primary_errno) { "none" } else { [string]$Projection.primary_errno }
        $Winerror = if ($null -eq $Projection.primary_winerror) { "none" } else { [string]$Projection.primary_winerror }
        Write-BidKingStartupSummaryLine (
          "  [native_current_run] result={0} stage={1} exception_class={2} reason_code={3} errno={4} winerror={5} powershell_attempted={6}" -f
            $Launch[0].Outcome, $Projection.primary_failure_stage, $Projection.primary_exception_class,
            $Projection.primary_reason_code, $Errno, $Winerror, ([string]$Projection.powershell_attempted).ToLowerInvariant()
        )
        Write-BidKingStartupSummaryLine (
          "  [native_current_cleanup] acl_restore={0} handle_release={1} monitor={2} overlay={3}" -f
            $Projection.acl_restore, $Projection.handle_release, $Projection.monitor, $Projection.overlay
        )
      } elseif (Test-Path -LiteralPath (Join-Path $Directory ($Launch[0].RunId + "--cleanup-bootstrap.json")) -PathType Leaf) {
        Write-BidKingStartupSummaryLine "  [native_current_run] result=unavailable reason=invalid_projection"
      }
    }
  } catch {
    Write-BidKingStartupSummaryLine "  [startup_timing] result=timing_unavailable reason=invalid"
  }
}

function Test-BidKingNativeSupersedesStartupSession {
  param([double]$LaunchStartedAt, $NativeEvidence = $script:BidKingCurrentNativeStartup)
  try {
    if ($LaunchStartedAt -le 0 -or -not $NativeEvidence -or
        $NativeEvidence.ValidProjection -isnot [bool] -or -not $NativeEvidence.ValidProjection -or
        $NativeEvidence.Outcome -cne "failed") { return $false }
    $NativeStarted = ([DateTimeOffset]$NativeEvidence.Started).ToUnixTimeMilliseconds() / 1000.0
    return $NativeStarted -gt ($LaunchStartedAt + 2.0)
  } catch { return $false }
}

function Add-CompatibilityRecoveryGuidance {
  Add-Line "  [恢复顺序] 1) 完整解压程序包，不要在 ZIP 内直接运行。"
  Add-Line "  [恢复顺序] 2) 将整个文件夹加入火绒或 360 的信任区/白名单。"
  Add-Line "  [恢复顺序] 3) 完整退出火绒、360、游戏加速器；仅关闭主窗口可能仍有后台防护或服务。"
  Add-Line "  [恢复顺序] 4) 先启动 Hero Ref，等待悬浮窗出现，再启动游戏。"
  Add-Line "  [恢复顺序] 5) 仍不计算时按同一顺序重启并收集脱敏诊断私发；不要反复更换 token。"
  Add-Line "  [安全边界] 不要卸载或永久关闭安全软件；测试后可恢复防护。若恢复后再次失效，请保持安全设置并等待兼容更新。"
  Add-Line "  [安全边界] Hero Ref 不会自动修改安全软件、Windows 安全功能、代理、路由或 DNS。"
}
function Try-Do($label, $block) {   # 单项探测的统一包装: 失败只记一行, 不中断
  try { & $block }
  catch { Add-Line "  [!] $label 探测失败: $($_.Exception.Message)" }
}
function Get-DiagnosticZoneId($path) {
  if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { return "missing" }
  try {
    $stream = Get-Item -LiteralPath $path -Stream Zone.Identifier -ErrorAction SilentlyContinue
    if (-not $stream) { return "none" }
    $zoneText = Get-Content -LiteralPath $path -Stream Zone.Identifier -Raw -ErrorAction Stop
    $zoneMatch = [regex]::Match([string]$zoneText, '(?im)^\s*ZoneId\s*=\s*(\d+)\s*$')
    if ($zoneMatch.Success) { return $zoneMatch.Groups[1].Value }
    return "present"
  } catch {
    return "unavailable"
  }
}
function Test-DiagnosticFileForLaunch(
  $path,
  $launchStartedAt,
  $launchId,
  $logSessionState,
  $declaredProperty
) {
  if (-not $launchStartedAt -or -not $launchId -or -not $logSessionState -or
      -not (Test-Path -LiteralPath $path)) { return $false }
  try {
    $knownProperties = @("stdout_file", "stderr_file", "runtime_status_file")
    if ($knownProperties -cnotcontains [string]$declaredProperty -or
        $logSessionState.PSObject.Properties.Name -cnotcontains [string]$declaredProperty) {
      return $false
    }
    $markerLaunchId = [string]$logSessionState.launch_id
    $markerStartedAt = [double]$logSessionState.started_at
    if (-not $markerLaunchId -or $markerLaunchId -cne [string]$launchId) { return $false }
    if ($markerStartedAt -le 0 -or
        [Math]::Abs($markerStartedAt - [double]$launchStartedAt) -gt 2.0) { return $false }
    $fileName = [System.IO.Path]::GetFileName([string]$path)
    $declaredFiles = @()
    foreach ($propertyName in $knownProperties) {
      if ($logSessionState.PSObject.Properties.Name -ccontains $propertyName) {
        $declaredValue = [string]$logSessionState.$propertyName
        if ($declaredValue) {
          if ([System.IO.Path]::GetFileName($declaredValue) -cne $declaredValue) {
            return $false
          }
          $declaredFiles += $declaredValue
        }
      }
    }
    if (@($declaredFiles | Group-Object | Where-Object Count -gt 1).Count -gt 0) {
      return $false
    }
    $expectedFile = [string]$logSessionState.$declaredProperty
    if (-not $expectedFile -or $expectedFile -cne $fileName) { return $false }
    $modifiedAt = [DateTimeOffset](Get-Item -LiteralPath $path -ErrorAction Stop).LastWriteTimeUtc
    return $modifiedAt.ToUnixTimeSeconds() -ge ([double]$launchStartedAt - 2.0)
  } catch { return $false }
}

Add-BidKingStartupTimingSummary (Join-Path $LogPath "startup-timing-v1") -CaptureOnly
$CurrentNativeSupersedesLaunch = $false
$CurrentLaunchStartedAt = $null
$CurrentLaunchId = $null
$CurrentLauncherElevationState = "unknown"
$CurrentMonitorLogSession = $null
$CurrentMonitorWasReused = $false
$CurrentOverlayLogSession = $null
$StartupSession = Join-Path $LogPath "startup_session.json"
$MonitorLogSession = Join-Path $LogPath "monitor_log_session.json"
$OverlayLogSession = Join-Path $LogPath "overlay_log_session.json"
try {
  if (Test-Path -LiteralPath $StartupSession) {
    $LaunchState = Get-Content -LiteralPath $StartupSession -Raw -Encoding UTF8 | ConvertFrom-Json
    $launchIdText = [string]$LaunchState.launch_id
    if ($launchIdText -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' -and
        [double]$LaunchState.started_at -gt 0) {
      $CurrentLaunchId = $LaunchState.launch_id
      $CurrentLaunchStartedAt = [double]$LaunchState.started_at
      $launcherElevation = [string]$LaunchState.launcher_elevation_state
      if (@("elevated", "not_elevated", "unknown") -contains $launcherElevation) {
        $CurrentLauncherElevationState = $launcherElevation
      }
    }
  }
} catch {
  $CurrentLaunchStartedAt = $null
  $CurrentLaunchId = $null
  $CurrentLauncherElevationState = "unknown"
}
try {
  if (Test-Path -LiteralPath $MonitorLogSession) {
    $CurrentMonitorLogSession = Get-Content -LiteralPath $MonitorLogSession -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($CurrentLaunchId -and
        [string]$CurrentMonitorLogSession.launch_id -ceq [string]$CurrentLaunchId -and
        $CurrentMonitorLogSession.PSObject.Properties.Name -contains "reused_monitor") {
      $CurrentMonitorWasReused = [bool]$CurrentMonitorLogSession.reused_monitor
    }
  }
} catch {
  $CurrentMonitorLogSession = $null
  $CurrentMonitorWasReused = $false
}
try {
  if (Test-Path -LiteralPath $OverlayLogSession) {
    $CandidateOverlayLogSession = (
      Get-Content -LiteralPath $OverlayLogSession -Raw -Encoding UTF8 |
        ConvertFrom-Json
    )
    $overlayLaunchId = [string]$CandidateOverlayLogSession.launch_id
    $overlayStartedAt = [double]$CandidateOverlayLogSession.started_at
    if ($CurrentLaunchId -and
        $overlayLaunchId -ceq [string]$CurrentLaunchId -and
        $overlayStartedAt -gt 0 -and
        [Math]::Abs($overlayStartedAt - [double]$CurrentLaunchStartedAt) -le 2.0) {
      $CurrentOverlayLogSession = $CandidateOverlayLogSession
    }
  }
} catch {
  $CurrentOverlayLogSession = $null
}
$CurrentNativeSupersedesLaunch = Test-BidKingNativeSupersedesStartupSession ([double]$CurrentLaunchStartedAt)
if ($CurrentNativeSupersedesLaunch) {
  # The exact newer failed native run supersedes an older PowerShell session;
  # clearing attribution never terminates or adopts any background process.
  $CurrentLaunchStartedAt = $null; $CurrentLaunchId = $null
  $CurrentMonitorLogSession = $null; $CurrentMonitorWasReused = $false
  $CurrentOverlayLogSession = $null; $CurrentLauncherElevationState = "unknown"
}
function Test-CurrentMonitorLog($path) {
  $fileName = [System.IO.Path]::GetFileName([string]$path)
  $declaredProperty = switch -CaseSensitive ($fileName) {
    "monitor.stdout.log" { "stdout_file"; break }
    "monitor.stderr.log" { "stderr_file"; break }
    default { $null }
  }
  if (-not $declaredProperty) { return $false }
  return Test-DiagnosticFileForLaunch `
    $path $CurrentLaunchStartedAt $CurrentLaunchId $CurrentMonitorLogSession $declaredProperty
}
function Test-CurrentOverlayFile($path) {
  $fileName = [System.IO.Path]::GetFileName([string]$path)
  $declaredProperty = switch -CaseSensitive ($fileName) {
    "overlay.stderr.log" { "stderr_file"; break }
    "hero_ref_ui_runtime_status.json" { "runtime_status_file"; break }
    default { $null }
  }
  if (-not $declaredProperty) { return $false }
  return Test-DiagnosticFileForLaunch `
    $path $CurrentLaunchStartedAt $CurrentLaunchId $CurrentOverlayLogSession $declaredProperty
}
function Get-OverlayStderrClass($path) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return "missing" }
  try {
    $text = [string](Get-Content -LiteralPath $path -Raw -Encoding UTF8 -ErrorAction Stop)
    if (-not $text.Trim()) { return "empty" }
    if ($text -match 'Traceback \(most recent call last\)') { return "python_traceback" }
    if ($text -match 'Fatal Python error') { return "fatal_python" }
    if ($text -match 'Unhandled exception') { return "unhandled_exception" }
    return "other_nonempty"
  } catch {
    return "unavailable"
  }
}
function Get-SafeUiRuntimeEvent($path) {
  if (-not (Test-CurrentOverlayFile $path)) { return "unavailable" }
  try {
    $state = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $event = [string]$state.event
    $allowed = @(
      "interface_refreshed",
      "game_launch_failed",
      "game_launch_requested",
      "summary_pending_cache_hit",
      "summary_worker_timeout_recovered",
      "summary_worker_error",
      "summary_applied",
      "bid_only_refresh",
      "summary_cache_hit",
      "summary_worker_started",
      "waiting_for_snapshot",
      "idle_no_change",
      "watched_monitor_exit",
      "gated_needs_activation"
    )
    if ($allowed -contains $event) { return $event }
    return "unknown"
  } catch {
    return "unavailable"
  }
}

$script:DiagnosticCimAvailable = $true
$script:DiagnosticCimFailureRecorded = $false
function Get-DiagnosticCimInstance($className, $filter) {
  if (-not $script:DiagnosticCimAvailable) { return $null }
  try {
    if ($filter) {
      return Get-CimInstance -ClassName $className -Filter $filter -OperationTimeoutSec 3 -ErrorAction Stop
    }
    return Get-CimInstance -ClassName $className -OperationTimeoutSec 3 -ErrorAction Stop
  } catch {
    $script:DiagnosticCimAvailable = $false
    if (-not $script:DiagnosticCimFailureRecorded) {
      $kind = $_.Exception.GetType().Name
      Add-Line "  [!] wmi_cim_unavailable: reason_type=$kind; diagnostics_continue=true"
      $script:DiagnosticCimFailureRecorded = $true
    }
    return $null
  }
}

Add-Line "BidKing 诊断报告"
Add-Line "生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Line "(本文件已默认脱敏，但仍含系统/网卡/软件环境；仅私发给支持人员，不要发群或公开上传。)"

# ---------- 1. 基本环境 + 路径体检 ----------
Write-BidKingDiagnosticProgress 2 "检查系统与路径"
Add-Sec "1. 基本环境 / 路径体检"
Try-Do "系统信息" {
  Add-Line "  PowerShell: $($PSVersionTable.PSVersion)"
  $wmi = Get-Service -Name Winmgmt -ErrorAction SilentlyContinue
  if ($wmi) { Add-Line "  WMI 服务: Status=$($wmi.Status) StartType=$($wmi.StartType) (仅观察, 产品不会修改)" }
  else { Add-Line "  WMI 服务: 未找到或不可读取 (仅观察, 产品不会修改)" }
  $os = Get-DiagnosticCimInstance "Win32_OperatingSystem" $null
  if ($os) {
    Add-Line "  系统: $($os.Caption) Build $($os.BuildNumber) ($($os.OSArchitecture))"
  } else {
    Add-Line "  系统详细信息: WMI/CIM 不可用, 已降级；其余诊断继续。"
  }
}
Try-Do "管理员权限" {
  $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
  Add-Line "  当前以管理员运行: $admin  (注: 正常启动会自动提权, 这里 False 不一定是问题)"
}
Try-Do "包路径体检" {
  Add-Line "  包根目录: $Repo"
  $hasCJK   = ($Repo -match '[一-鿿]')
  $hasSpace = ($Repo -match ' ')
  $onCloud  = ($Repo -match 'OneDrive|Dropbox|GoogleDrive|坚果云|百度网盘')
  $drive    = (Split-Path -Qualifier $Repo) 2>$null
  $disk = Get-DiagnosticCimInstance "Win32_LogicalDisk" "DeviceID='$drive'"
  $driveType = if ($disk) { $disk.DriveType } else { $null }
  # DriveType: 2=可移动(U盘) 3=本地 4=网络
  Add-Line "  含中文路径: $hasCJK   含空格: $hasSpace   云同步盘: $onCloud"
  Add-Line "  所在盘: $drive  类型: $(switch($driveType){2{'可移动/U盘 ⚠'}3{'本地硬盘'}4{'网络盘 ⚠'}default{'未知'}})"
  if ($hasCJK)   { Add-Line "  [次级提示] 路径含中文仅是通用兼容上下文；存在明确 WSS/service 分类时不得把它当根因。若需建立新旧代目录边界，可完整解压到新的最终目录。" }
  if ($onCloud)  { Add-Line "  [提示] 在云同步目录 → 文件可能被占用/同步冲突, 建议移到非同步目录。" }
  if ($driveType -in 2,4) { Add-Line "  [提示] 在 U盘/网络盘 → 速度慢且驱动可能装不上, 建议放本地硬盘。" }
}
Try-Do "产品版本" {
  $manifest = Join-Path $Repo "BUILD_MANIFEST.txt"
  if (Test-Path $manifest) {
    $m = (Select-String -Path $manifest -Pattern 'PackageVersion:\s*v?(.+)' | Select-Object -First 1)
    Add-Line "  产品版本(BUILD_MANIFEST): $($m.Matches.Groups[1].Value.Trim())"
    $commit = (Select-String -Path $manifest -Pattern 'SourceCommit:\s*(\S+)' | Select-Object -First 1)
    if ($commit) { Add-Line "  源码提交: $($commit.Matches.Groups[1].Value.Trim())" }
    else { Add-Line "  源码提交: BUILD_MANIFEST 未记录 ⚠" }
    $dirty = (Select-String -Path $manifest -Pattern 'PackageInputsDirty:\s*(\S+)' | Select-Object -First 1)
    if ($dirty) { Add-Line "  打包输入存在未提交修改: $($dirty.Matches.Groups[1].Value.Trim())" }
    else { Add-Line "  打包输入状态: BUILD_MANIFEST 未记录 ⚠" }
    $encrypted = (Select-String -Path $manifest -Pattern 'Encrypted:\s*(\S+)' | Select-Object -First 1)
    $isEncrypted = $encrypted -and ($encrypted.Matches.Groups[1].Value.Trim() -eq 'True')
    if ($isEncrypted) {
      $keyGate = (Select-String -Path $manifest -Pattern 'ReleaseKeyCacheVerified:\s*(\S+)' | Select-Object -First 1)
      $cfgGate = (Select-String -Path $manifest -Pattern 'UploadConfigRoundTripVerified:\s*(\S+)' | Select-Object -First 1)
      $keyOK = $keyGate -and ($keyGate.Matches.Groups[1].Value.Trim() -eq 'True')
      $cfgOK = $cfgGate -and ($cfgGate.Matches.Groups[1].Value.Trim() -eq 'True')
      Add-Line "  发布主密钥匹配近期激活缓存: $keyOK$(if(-not $keyOK){' ⚠'})"
      Add-Line "  加密上传配置解密回环: $cfgOK$(if(-not $cfgOK){' ⚠'})"
    }
  } else {
    $vf = Join-Path $Repo "hero_ref\tools\hero_ref_version.py"
    if (Test-Path $vf) {
      $v = (Select-String -Path $vf -Pattern 'HERO_REF_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1)
      Add-Line "  产品版本(源码常量): $($v.Matches.Groups[1].Value)  (无 BUILD_MANIFEST, 可能是源码/工程版)"
    } else { Add-Line "  产品版本: 未找到版本文件 ⚠(包可能不完整)" }
  }
}

Try-Do "最近激活在线复核摘要" {
  $HistoryRoot = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "bidking"
  } else {
    Join-Path ([Environment]::GetFolderPath("UserProfile")) ".bidking"
  }
  $AllowedEndpoints = @("endpoint_1", "endpoint_2", "endpoint_3", "endpoint_4")
  $AllowedStages = @("dns", "tcp", "tls", "unknown")
  $AllowedFailures = @(
    "dns_lookup_failed", "connect_timeout", "connect_refused",
    "tls_connection_reset", "tls_handshake_failed",
    "transport_os_error", "transport_unknown"
  )
  $FailureStages = @{
    dns_lookup_failed = "dns"
    connect_timeout = "tcp"
    connect_refused = "tcp"
    tls_connection_reset = "tls"
    tls_handshake_failed = "tls"
    transport_os_error = "tcp"
    transport_unknown = "unknown"
  }
  $ExpectedRootFields = @("schema", "updated_at", "sequences")
  $ExpectedSequenceFields = @(
    "started_at", "last_failure_at", "recovered_at", "attempt_count", "attempts"
  )
  $ExpectedAttemptFields = @("at", "endpoint", "stage", "failure_class")
  $NowEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  $EarliestEpoch = [Math]::Max(0, $NowEpoch - 86400)

  function Test-ExactJsonObjectFields($Value, [string[]]$Expected) {
    if (
      $null -eq $Value -or
      $Value.GetType().FullName -cne "System.Management.Automation.PSCustomObject"
    ) { return $false }
    $Properties = @($Value.PSObject.Properties)
    if ($Properties.Count -ne $Expected.Count) { return $false }
    foreach ($Property in $Properties) {
      if ($Property.Name.GetType() -ne [string] -or -not ($Expected -ccontains $Property.Name)) {
        return $false
      }
    }
    return $true
  }
  function Get-SafeDiagnosticInteger($Value, [long]$Minimum, [long]$Maximum) {
    if ($null -eq $Value) { return $null }
    $ValueType = $Value.GetType()
    if ($ValueType -ne [int] -and $ValueType -ne [long]) { return $null }
    $Number = [long]$Value
    if ($Number -lt $Minimum -or $Number -gt $Maximum) { return $null }
    return $Number
  }

  $SafeSequences = New-Object System.Collections.Generic.List[object]
  if (Test-Path -LiteralPath $HistoryRoot -PathType Container) {
    $HistoryFiles = @(
      Get-ChildItem -LiteralPath $HistoryRoot -Filter "activation_recheck_history_*.json" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -le 65536 } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 16
    )
    foreach ($HistoryFile in $HistoryFiles) {
      try {
        $Payload = Get-Content -LiteralPath $HistoryFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not (Test-ExactJsonObjectFields $Payload $ExpectedRootFields)) { continue }
        $Schema = Get-SafeDiagnosticInteger $Payload.schema 1 1
        $UpdatedAt = Get-SafeDiagnosticInteger $Payload.updated_at 0 253402300799
        if ($Schema -ne 1 -or $null -eq $UpdatedAt -or $Payload.sequences -isnot [System.Array]) { continue }
        $RawSequences = @($Payload.sequences)
        if ($RawSequences.Count -gt 4) { continue }
        $FileSequences = New-Object System.Collections.Generic.List[object]
        $FileValid = $true
        $HasPreviousSequence = $false
        $PreviousRecoveredAt = $null
        foreach ($Sequence in $RawSequences) {
          if (-not (Test-ExactJsonObjectFields $Sequence $ExpectedSequenceFields)) {
            $FileValid = $false; break
          }
          $StartedAt = Get-SafeDiagnosticInteger $Sequence.started_at 0 253402300799
          $LastFailureAt = Get-SafeDiagnosticInteger $Sequence.last_failure_at 0 253402300799
          $AttemptCount = Get-SafeDiagnosticInteger $Sequence.attempt_count 1 64
          $RecoveredAt = $null
          if ($null -ne $Sequence.recovered_at) {
            $RecoveredAt = Get-SafeDiagnosticInteger $Sequence.recovered_at 0 253402300799
            if ($null -eq $RecoveredAt) { $FileValid = $false; break }
          }
          if (
            $null -eq $StartedAt -or $null -eq $LastFailureAt -or
            $LastFailureAt -lt $StartedAt -or
            $LastFailureAt -lt $EarliestEpoch -or $LastFailureAt -gt ($NowEpoch + 300) -or
            ($null -ne $RecoveredAt -and $RecoveredAt -lt $LastFailureAt) -or
            ($HasPreviousSequence -and (
              $null -eq $PreviousRecoveredAt -or $StartedAt -lt $PreviousRecoveredAt
            )) -or
            $null -eq $AttemptCount -or $Sequence.attempts -isnot [System.Array]
          ) { $FileValid = $false; break }
          $RawAttempts = @($Sequence.attempts)
          if ($RawAttempts.Count -lt 1 -or $RawAttempts.Count -gt 12 -or $AttemptCount -lt $RawAttempts.Count) {
            $FileValid = $false; break
          }
          $SafeAttempts = New-Object System.Collections.Generic.List[object]
          $PreviousAttemptAt = $null
          foreach ($Attempt in $RawAttempts) {
            if (-not (Test-ExactJsonObjectFields $Attempt $ExpectedAttemptFields)) {
              $FileValid = $false; break
            }
            $AttemptAt = Get-SafeDiagnosticInteger $Attempt.at 0 253402300799
            if (
              $null -eq $AttemptAt -or $Attempt.endpoint.GetType() -ne [string] -or
              -not ($AllowedEndpoints -ccontains $Attempt.endpoint) -or
              $Attempt.stage.GetType() -ne [string] -or -not ($AllowedStages -ccontains $Attempt.stage) -or
              $Attempt.failure_class.GetType() -ne [string] -or
              -not ($AllowedFailures -ccontains $Attempt.failure_class) -or
              $FailureStages[[string]$Attempt.failure_class] -cne [string]$Attempt.stage -or
              $AttemptAt -lt $StartedAt -or $AttemptAt -gt $LastFailureAt -or
              ($null -ne $PreviousAttemptAt -and $AttemptAt -lt $PreviousAttemptAt)
            ) { $FileValid = $false; break }
            [void]$SafeAttempts.Add([pscustomobject]@{
              at = $AttemptAt
              endpoint = [string]$Attempt.endpoint
              stage = [string]$Attempt.stage
              failure_class = [string]$Attempt.failure_class
            })
            $PreviousAttemptAt = $AttemptAt
          }
          if (-not $FileValid) { break }
          if (
            $null -eq $PreviousAttemptAt -or $PreviousAttemptAt -ne $LastFailureAt -or
            ($AttemptCount -eq $RawAttempts.Count -and $SafeAttempts[0].at -ne $StartedAt) -or
            ($AttemptCount -gt $RawAttempts.Count -and $RawAttempts.Count -ne 12)
          ) { $FileValid = $false; break }
          [void]$FileSequences.Add([pscustomobject]@{
            started_at = $StartedAt
            last_failure_at = $LastFailureAt
            recovered_at = $RecoveredAt
            attempt_count = $AttemptCount
            attempts = @($SafeAttempts.ToArray())
          })
          $HasPreviousSequence = $true
          $PreviousRecoveredAt = $RecoveredAt
        }
        if ($FileValid) {
          foreach ($Sequence in $FileSequences) { [void]$SafeSequences.Add($Sequence) }
        }
      } catch {
        # History is optional support evidence. Never print exception text/path.
      }
    }
  }
  $RecentSequences = @($SafeSequences | Sort-Object last_failure_at -Descending | Select-Object -First 4)
  if ($RecentSequences.Count -eq 0) {
    Add-Line "  最近在线复核摘要: 无"
  } else {
    Add-Line "  最近在线复核摘要: $($RecentSequences.Count) 段（最多4段/24小时）"
    foreach ($Sequence in $RecentSequences) {
      $RecoveredText = if ($null -eq $Sequence.recovered_at) { "pending" } else { [string]$Sequence.recovered_at }
      Add-Line "    recheck started_at=$($Sequence.started_at) last_failure_at=$($Sequence.last_failure_at) attempt_count=$($Sequence.attempt_count) recovered_at=$RecoveredText"
      foreach ($Attempt in $Sequence.attempts) {
        Add-Line "      attempt at=$($Attempt.at) endpoint=$($Attempt.endpoint) stage=$($Attempt.stage) failure_class=$($Attempt.failure_class)"
      }
    }
  }
}

# ---------- 2. 依赖 / 本地兼容 ----------
Write-BidKingDiagnosticProgress 3 "检查本地兼容组件"
$CaptureSourceForDiag = ""
try {
  $CaptureStatusForDiag = Join-Path $LogPath "capture_source_status.json"
  if (Test-Path -LiteralPath $CaptureStatusForDiag) {
    $CaptureStateForDiag = (
      Get-Content -LiteralPath $CaptureStatusForDiag -Raw -Encoding UTF8 |
        ConvertFrom-Json
    )
    $CaptureSourceForDiag = [string]$CaptureStateForDiag.source
  }
} catch {}
$WssManifestDefault = $false
try {
  $ManifestForDiag = Join-Path $Repo "BUILD_MANIFEST.txt"
  if (Test-Path -LiteralPath $ManifestForDiag) {
    $WssManifestDefault = Select-String -LiteralPath $ManifestForDiag -Pattern '^WssCaptureDefault:\s*True\s*$' -Quiet
  }
} catch {}
$WssExpected = $WssManifestDefault -or $CaptureSourceForDiag -eq "wss_local"

Add-Sec "2. 依赖 / 本地兼容(官方新版优先检查 WSS 冷启动、组件和证书)"
Try-Do "WSS 本地兼容" {
  $bundledMitmdump = Join-Path $Repo "vendor\mitmproxy\mitmdump.exe"
  $mitmdump = $bundledMitmdump
  $componentDescriptor = Join-Path $Repo "data\processed\local_compat_component.json"
  $componentCached = $false
  $componentRoot = $null
  if (Test-Path -LiteralPath $componentDescriptor -PathType Leaf) {
    try {
      $component = Get-Content -LiteralPath $componentDescriptor -Raw -Encoding utf8 |
        ConvertFrom-Json
      $inventoryPrefix = ([string]$component.inventory.inventory_sha256).Substring(0, 16).ToLowerInvariant()
      $componentIdentity = "{0}-{1}" -f ([string]$component.version), $inventoryPrefix
      $componentRoot = Join-Path (
        Join-Path (
          Join-Path $env:LOCALAPPDATA "BidKingLive\components"
        ) ([string]$component.component_id)
      ) $componentIdentity
      $componentCached = (
        (Test-Path -LiteralPath (Join-Path $componentRoot "ready.json") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $componentRoot "runtime\mitmdump.exe") -PathType Leaf)
      )
    } catch {}
  }
  $runtimeAvailable = (
    (Test-Path -LiteralPath $mitmdump -PathType Leaf) -or
    $componentCached
  )
  $runtimeRoot = Join-Path $Repo "vendor\mitmproxy"
  if (-not (Test-Path -LiteralPath $mitmdump -PathType Leaf) -and $componentCached) {
    $runtimeRoot = Join-Path $componentRoot "runtime"
    $mitmdump = Join-Path $runtimeRoot "mitmdump.exe"
  }
  $addon = Join-Path $Repo "scripts\local_compat_addon.py"
  if (-not (Test-Path -LiteralPath $addon -PathType Leaf)) {
    $addon = Join-Path $Repo "scripts\mitm_bidking_wss_addon.py"
  }
  $ca = Join-Path $env:LOCALAPPDATA "BidKingLive\mitmproxy\mitmproxy-ca-cert.cer"
  $trusted = $false
  if (Test-Path -LiteralPath $ca -PathType Leaf) {
    try {
      $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($ca)
      $trusted = Test-Path -LiteralPath "Cert:\CurrentUser\Root\$($cert.Thumbprint)"
    } catch {}
  }
  Add-Line "  WSS 默认路线: $WssExpected  当前 source=$CaptureSourceForDiag"
  Add-Line "  WSS 运行时: bundled=$(Test-Path -LiteralPath $bundledMitmdump)  component_cached=$componentCached  安全桥=$(Test-Path -LiteralPath $addon)"
  Add-Line "  本机独立 CA: exists=$(Test-Path -LiteralPath $ca)  current-user trusted=$trusted"
  $gameRunning = [bool](Get-Process -Name "BidKing" -ErrorAction SilentlyContinue)
  Add-Line "  诊断时 BidKing.exe 正在运行: $gameRunning  (WSS 必须先开 Hero Ref、后开游戏)"
  $redirector = Join-Path $runtimeRoot "_internal\mitmproxy_windows\windows-redirector.exe"
  $winDivertDriver = Join-Path $runtimeRoot "_internal\mitmproxy_windows\WinDivert64.sys"
  foreach ($componentProbe in @(
      [pscustomobject]@{ Label = "redirector"; Path = $redirector },
      [pscustomobject]@{ Label = "WinDivert driver"; Path = $winDivertDriver }
    )) {
    $exists = Test-Path -LiteralPath $componentProbe.Path -PathType Leaf
    if ($exists) {
      $item = Get-Item -LiteralPath $componentProbe.Path
      $signatureStatus = "unavailable"
      try {
        $signature = Get-AuthenticodeSignature -LiteralPath $componentProbe.Path -ErrorAction Stop
        $signatureStatus = [string]$signature.Status
      } catch {}
      $zoneId = Get-DiagnosticZoneId $componentProbe.Path
      Add-Line "  $($componentProbe.Label): exists=True bytes=$($item.Length) signature=$signatureStatus zone_id=$zoneId"
    } else {
      Add-Line "  $($componentProbe.Label): exists=False"
    }
  }
  $mitmdumpCount = @(Get-Process -Name "mitmdump" -ErrorAction SilentlyContinue).Count
  $redirectorCount = @(Get-Process -Name "windows-redirector" -ErrorAction SilentlyContinue).Count
  Add-Line "  WSS 进程: mitmdump=$mitmdumpCount redirector=$redirectorCount"
  $winDivertDrivers = @(
    Get-DiagnosticCimInstance "Win32_SystemDriver" $null |
      Where-Object { ([string]$_.Name + [string]$_.DisplayName) -match '(?i)WinDivert' }
  )
  if ($winDivertDrivers) {
    $driverStates = $winDivertDrivers | ForEach-Object {
      "{0}:{1}/{2}" -f $_.Name, $_.State, $_.StartMode
    }
    Add-Line "  WinDivert 系统驱动: $($driverStates -join ' | ')"
  } else {
    Add-Line "  WinDivert 系统驱动: 未发现活动或已注册实例"
  }
  $appinfo = Get-Service -Name "Appinfo" -ErrorAction SilentlyContinue
  $uacPolicy = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -ErrorAction SilentlyContinue
  Add-Line "  Windows 提权链: Appinfo=$($appinfo.Status)/$($appinfo.StartType) EnableLUA=$($uacPolicy.EnableLUA) ValidateAdminCodeSignatures=$($uacPolicy.ValidateAdminCodeSignatures)"
  if ((Get-DiagnosticZoneId $redirector) -eq "3") {
    Add-Line "  [风险] redirector_download_zone=3；该互联网来源标记在部分 Windows 策略下会阻止管理员启动。"
  }
  if ($WssExpected -and (-not $runtimeAvailable -or -not $trusted)) {
    if (Test-Path -LiteralPath $componentDescriptor -PathType Leaf) {
      Add-Line "  [解决] 检查网络和磁盘空间后，先完全关闭游戏，再打开 Hero Ref 并按提示完成首次兼容设置；也可改用完整离线包。"
    } else {
      Add-Line "  [解决] 重新完整解压当前版本安装包；先完全关闭游戏，再打开 Hero Ref 并按提示初始化 WSS。"
    }
  }
}
Try-Do "WSS helper 分类状态" {
  $helperStatusPath = Join-Path $LogPath "wss_helper_status.json"
  if (Test-Path -LiteralPath $helperStatusPath -PathType Leaf) {
    $helperStatus = Get-Content -LiteralPath $helperStatusPath -Raw -Encoding UTF8 |
      ConvertFrom-Json
    $helperLaunchId = [string]$helperStatus.launch_id
    $helperBelongsToCurrentLaunch = (
      $CurrentLaunchId -and
      $helperLaunchId -and
      $helperLaunchId -ceq [string]$CurrentLaunchId
    )
    if (-not $helperBelongsToCurrentLaunch) {
      Add-Line "  helper 分类状态: 历史 sidecar 或无法绑定本次 launch，不作为当前错误证据。"
      return
    }
    $allowedPhases = @(
      "helper_running", "helper_start_failed", "exited_unexpectedly",
      "stopped", "monitor_guard_failed"
    )
    $allowedRedirectorStates = @(
      "unknown", "starting", "running", "missing_stable",
      "elevation_denied", "start_failed"
    )
    $allowedWinDivertStates = @(
      "unknown", "start_failed", "known_historical_blocked_driver"
    )
    $allowedPreloadStates = @(
      "not_applicable", "not_needed", "preloaded", "already_preloaded",
      "repaired_restart_required",
      "failed_missing_driver", "failed_untrusted_driver",
      "failed_access_denied", "failed_invalid_image_hash",
      "failed_driver_blocked", "failed_same_path_mixed_generation",
      "failed_service_conflict", "failed_unclassified"
    )
    $allowedPreloadPhases = @(
      "not_started", "validate_driver", "query_legacy", "query_product",
      "create_product", "verify_product", "start_product", "query_running",
      "cleanup", "complete"
    )
    $allowedElevationStates = @(
      "elevated", "not_elevated", "unknown", "not_applicable"
    )
    $allowedErrorCodes = @(
      "wss_redirector_elevation_denied",
      "wss_windivert_start_failed",
      "wss_redirector_start_failed",
      "wss_addon_start_failed",
      "wss_capture_helper_start_failed",
      "wss_capture_helper_exit_unclassified",
      "wss_runtime_compatibility_failed",
      "wss_redirector_missing_stable",
      "wss_child_lifecycle_guard_failed"
    )
    $phase = [string]$helperStatus.phase
    $redirectorState = [string]$helperStatus.redirector_state
    $winDivertState = [string]$helperStatus.windivert_state
    $preloadState = [string]$helperStatus.windivert_preload_state
    $preloadPhase = [string]$helperStatus.windivert_preload_phase
    $monitorElevationState = [string]$helperStatus.monitor_elevation_state
    $errorCode = [string]$helperStatus.error_code
    if ($allowedPhases -notcontains $phase) { $phase = "unknown" }
    if ($allowedRedirectorStates -notcontains $redirectorState) { $redirectorState = "unknown" }
    if ($allowedWinDivertStates -notcontains $winDivertState) { $winDivertState = "unknown" }
    if ($allowedPreloadStates -notcontains $preloadState) { $preloadState = "" }
    if ($allowedPreloadPhases -notcontains $preloadPhase) { $preloadPhase = "" }
    if ($allowedElevationStates -notcontains $monitorElevationState) {
      $monitorElevationState = ""
    }
    if ($allowedErrorCodes -notcontains $errorCode) { $errorCode = "" }
    $exitCode = if ($helperStatus.PSObject.Properties.Name -contains "helper_exit_code") {
      try { [int]$helperStatus.helper_exit_code } catch { "unknown" }
    } elseif ($phase -eq "helper_start_failed") {
      "not_started"
    } else {
      "running"
    }
    Add-Line "  helper: phase=$phase exit_code=$exitCode redirector=$redirectorState windivert=$winDivertState"
    if ($preloadState) {
      $preloadDetails = "state=$preloadState"
      if ($preloadPhase) { $preloadDetails += " phase=$preloadPhase" }
      if ($monitorElevationState) {
        $preloadDetails += " monitor_elevation=$monitorElevationState"
      }
      Add-Line "  WinDivert 隔离预加载: $preloadDetails"
      if (
        $preloadState -eq "failed_same_path_mixed_generation" -and
        $preloadPhase -eq "verify_product"
      ) {
        Add-Line "  [明确根因] classification=wss_same_path_mixed_generation；新旧包可能在同一绝对目录混装，本次安全拒绝自动修改服务。"
        Add-Line "  [下一步] next_step=extract_official_zip_to_new_final_directory；完全退出后把官方 ZIP 完整解压到新的最终目录，不要先删旧目录。"
        Add-Line "  [边界] 不停止、删除或重配服务；中文/空格路径、安全软件与备用端点仅作次级上下文，不覆盖本分类。"
      } elseif (
        $preloadState -eq "failed_service_conflict" -and
        $preloadPhase -eq "verify_product"
      ) {
        Add-Line "  [明确根因] classification=wss_stale_product_service_conflict；产品服务身份冲突且未达到安全删除条件。"
        Add-Line "  [下一步] next_step=send_diagnostics_privately；完全退出后保留现场并把本诊断私发支持人员。"
      } elseif (
        $preloadState -eq "repaired_restart_required" -and
        $preloadPhase -eq "complete"
      ) {
        Add-Line "  [明确根因] classification=wss_stale_product_service_repaired_restart_required；精确删除已提交但旧 driver/句柄未证明卸载。"
        Add-Line "  [下一步] next_step=restart_windows_then_start_current_package；重启前不继续启动链。"
      }
      if ($preloadState -eq "failed_access_denied") {
        if ($monitorElevationState -eq "elevated") {
          Add-Line "  [明确分类] 已提升的本地监控在 $preloadPhase 阶段被 SCM 或系统策略拒绝；不锁定具体安全软件或系统策略。"
        } elseif ($monitorElevationState -eq "not_elevated") {
          Add-Line "  [明确分类] 本地监控未处于管理员上下文，在 $preloadPhase 阶段被拒绝；不锁定具体安全软件或系统策略。"
        } else {
          Add-Line "  [明确分类] 本地兼容预加载在 $preloadPhase 阶段被拒绝，提升身份未知；不锁定具体安全软件或系统策略。"
        }
        if (
          $CurrentLauncherElevationState -ne "unknown" -and
          $monitorElevationState -and
          $CurrentLauncherElevationState -cne $monitorElevationState
        ) {
          Add-Line "  [提示] launcher_elevation=$CurrentLauncherElevationState 与 monitor_elevation=$monitorElevationState 不一致，按身份未决处理。"
        }
        if ($errorCode -ne "wss_redirector_missing_stable") {
          Add-CompatibilityRecoveryGuidance
        }
      }
    }
    if ($errorCode) {
      Add-Line "  [明确 WSS 错误] code=$errorCode"
      switch ($errorCode) {
        "wss_redirector_elevation_denied" {
          Add-Line "  [建议] Windows 拒绝提升本地兼容组件；核对官方包完整性和 redirector_download_zone。"
        }
        "wss_windivert_start_failed" {
          Add-Line "  [建议] WinDivert 启动失败；结合系统驱动状态排查，不以安全软件清单单独判根因。"
        }
        "wss_runtime_compatibility_failed" {
          $compatibilityState = [string]$helperStatus.runtime_compatibility_state
          $driverSha256 = ([string]$helperStatus.windivert_driver_sha256).ToLowerInvariant()
          if (
            $compatibilityState -ceq "failed_known_historical_driver" -and
            $driverSha256 -cmatch '^[0-9a-f]{64}$'
          ) {
            Add-Line "  [明确分类] runtime_compatibility=$compatibilityState driver_sha256=$driverSha256"
          }
          Add-Line "  [建议] 本次 launch 的本地兼容组件未通过 Windows 安全策略；请保持易受攻击驱动阻止列表和内存完整性开启，并把诊断私发支持人员。"
        }
        "wss_redirector_missing_stable" {
          Add-Line "  [明确分类] helper 仍在运行，但 redirector 在启动宽限和连续复核后仍未接管。"
          Add-Line "  [建议] 本分类不锁定唯一根因；请按下面顺序恢复。"
          Add-CompatibilityRecoveryGuidance
        }
        default {
          Add-Line "  [建议] 本地兼容组件启动失败；请把本诊断私发支持人员。"
        }
      }
    }
  } elseif ($WssExpected) {
    Add-Line "  helper 分类状态: 无(旧版本、monitor 未启动或尚未生成 sidecar)。"
  }
}
Try-Do "网吧环境固定分类" {
  $allowedClassifications = @(
    "ready", "cert_store_write_denied", "policy_locked", "profile_ephemeral",
    "uac_disabled", "component_write_denied", "restore_disk_suspected",
    "interactive_user_mismatch", "component_execution_blocked",
    "driver_load_denied", "package_root_unsupported", "foreign_cert_or_state",
    "user_cancelled", "unknown"
  )
  $allowedResults = @("supported", "cancelled", "internet_cafe_realtime_unsupported")
  $receipt = @(Get-ChildItem -LiteralPath $ExportDir `
    -Filter "BidKingInternetCafe-*.json" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1)
  if ($receipt.Count -eq 0) {
    Add-Line "  网吧环境兼容回执: 无；可运行「诊断工具\网吧环境兼容检查.bat」做零修改检查。"
  } else {
    try {
      $raw = [IO.File]::ReadAllBytes($receipt[0].FullName)
      if ($raw.Length -le 0 -or $raw.Length -gt 16384) { throw "unsafe" }
      $payload = [Text.UTF8Encoding]::new($false, $true).GetString($raw) |
        ConvertFrom-Json -ErrorAction Stop
      $classification = [string]$payload.classification
      $result = [string]$payload.result
      $nextStep = [string]$payload.next_step
      if (
        [int]$payload.schema_version -ne 1 -or
        $classification -notin $allowedClassifications -or
        $result -notin $allowedResults -or
        $nextStep -cnotmatch '^[a-z_]{3,64}$'
      ) { throw "unsafe" }
      Add-Line "  网吧环境兼容: classification=$classification result=$result next_step=$nextStep"
    } catch {
      Add-Line "  网吧环境兼容回执: 无法安全验证，未读取正文。"
    }
  }
  $leaseRoot = Join-Path ([Environment]::GetFolderPath(
    [Environment+SpecialFolder]::CommonApplicationData
  )) "BidKing\wss-trust"
  try {
    $leaseCount = if (Test-Path -LiteralPath $leaseRoot -PathType Container) {
      @(Get-ChildItem -LiteralPath $leaseRoot -Filter "lease-*.json" -File -Force).Count
    } else { 0 }
    Add-Line "  产品 CA 私有 journal: state=$(if ($leaseCount -gt 0) { 'present' } else { 'absent' }) count=$leaseCount"
  } catch {
    Add-Line "  产品 CA 私有 journal: state=unavailable"
  }
}
Try-Do "Npcap" {
  $svc = Get-Service -Name npcap -ErrorAction SilentlyContinue
  $dll = Test-Path "$env:SystemRoot\System32\Npcap\wpcap.dll"
  $dll2 = Test-Path "$env:SystemRoot\System32\wpcap.dll"
  if ($svc) { Add-Line "  Npcap 服务(旧路线回退): 已安装, 状态=$($svc.Status)" }
  elseif ($WssExpected) { Add-Line "  Npcap 服务: 未找到(当前 WSS 默认路线不依赖 Npcap)。" }
  else      { Add-Line "  Npcap 服务: 未找到 ⚠ → Npcap 回退路线无法启动。" }
  Add-Line "  wpcap.dll: System32\Npcap=$dll  System32=$dll2"
  if (-not $svc -and -not $WssExpected) { Add-Line "  [解决] 当前发布包不内置 Npcap 安装器；仅在显式启用旧路线回退时，请从 Npcap 官方渠道自行安装，重启电脑后再开。" }
}
Try-Do "内嵌 Python" {
  $cands = @("runtime\python\python.exe","python\python.exe","runtime\python.exe") | ForEach-Object { Join-Path $Repo $_ }
  $py = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($py) {
    $ver = & $py -B --version 2>&1
    Add-Line "  内嵌 Python: $py  ($ver)"
  } else { Add-Line "  内嵌 Python: 未找到(便携版应自带) ⚠ → 包可能没解压全, 建议重新完整解压。" }
}

# ---------- 3. 网络环境上下文（明确初始化错误优先于软件清单） ----------
Write-BidKingDiagnosticProgress 4 "检查网络环境"
Add-Sec "3. 网络环境(网卡/虚拟网卡/代理/安全软件上下文)"
Try-Do "明确抓包初始化错误(优先判据)" {
  $cs = Join-Path $LogPath "capture_source_status.json"
  $foundOpenError = $false
  if (Test-Path $cs) {
    $captureStatus = Get-Content $cs -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($captureStatus.error_code -eq 'npcap_device_open_failed') {
      $foundOpenError = $true
      Add-Line "  [明确错误] 网卡抓包初始化失败(npcap_device_open_failed)。"
      if ($captureStatus.error_hint) { Add-Line "  [建议] $($captureStatus.error_hint)" }
    }
  }
  if (-not $foundOpenError) {
    $captureLogs = @(
      (Join-Path $LogPath "monitor.stdout.log"),
      (Join-Path $LogPath "monitor.stderr.log")
    ) | Where-Object { Test-Path $_ }
    if ($captureLogs) {
      $legacyHit = Select-String -Path $captureLogs -Pattern 'pcap_open_live.*(promiscuous|hardware filter)' -ErrorAction SilentlyContinue | Select-Object -Last 1
      if ($legacyHit) {
        $foundOpenError = $true
        Add-Line "  [明确错误] 日志显示网卡混杂模式初始化失败；新版会尝试普通抓包兼容模式。"
      }
    }
  }
  if (-not $foundOpenError -and $CaptureSourceForDiag -eq "wss_local") {
    Add-Line "  当前使用 WSS process-local 路线，不以 Npcap 网卡打开状态判根因。"
  } elseif (-not $foundOpenError) {
    Add-Line "  未发现明确的 Npcap 设备打开错误；以下网卡和软件清单仅作排查上下文。"
  }
}
Try-Do "三层连接状态" {
  $networkStatusPath = Join-Path $LogPath "network_connection_status.json"
  if (-not (Test-Path -LiteralPath $networkStatusPath -PathType Leaf)) {
    Add-Line "  三层状态: 无(旧版本、monitor 尚未启动或 sidecar 尚未生成)。"
    return
  }
  $networkStatus = Get-Content -LiteralPath $networkStatusPath -Raw -Encoding UTF8 |
    ConvertFrom-Json
  $networkLaunchId = [string]$networkStatus.launch_id
  if ($CurrentLaunchId -and $networkLaunchId -and
      $networkLaunchId -cne [string]$CurrentLaunchId) {
    Add-Line "  三层状态: 历史 sidecar，不作为本次连接证据。"
    return
  }
  $allowedGameStates = @("not_running", "connecting", "connected", "failed", "unknown")
  $allowedWssStates = @("not_started", "ready", "attached", "failed", "unknown")
  $allowedDecodeStates = @("idle", "receiving", "unrecognized", "recognized")
  $gameConnectionState = [string]$networkStatus.game_connection_state
  $wssInterceptState = [string]$networkStatus.wss_intercept_state
  $captureDecodeState = [string]$networkStatus.capture_decode_state
  if ($allowedGameStates -notcontains $gameConnectionState) { $gameConnectionState = "unknown" }
  if ($allowedWssStates -notcontains $wssInterceptState) { $wssInterceptState = "unknown" }
  if ($allowedDecodeStates -notcontains $captureDecodeState) { $captureDecodeState = "idle" }
  Add-Line "  game_connection_state=$gameConnectionState"
  Add-Line "  wss_intercept_state=$wssInterceptState"
  Add-Line "  capture_decode_state=$captureDecodeState"
  $gamePid = try { [int]$networkStatus.game_pid } catch { 0 }
  $gameStartedAt = try { [double]$networkStatus.game_started_at } catch { 0 }
  $windowStartedAt = try { [double]$networkStatus.player_log_window_started_at } catch { 0 }
  if ($gamePid -gt 0) {
    Add-Line "  game_pid=$gamePid create_time=$gameStartedAt player_log_window_start=$windowStartedAt"
  }
  $allowedPlayerLogErrors = @("websocket_unable_to_connect")
  $errorParts = @()
  if ($networkStatus.evidence -and $networkStatus.evidence.player_log_error_counts) {
    foreach ($errorName in $allowedPlayerLogErrors) {
      $property = $networkStatus.evidence.player_log_error_counts.PSObject.Properties[$errorName]
      if ($property) {
        $errorCount = try { [Math]::Max(0, [int]$property.Value) } catch { 0 }
        if ($errorCount -gt 0) { $errorParts += "$errorName=$errorCount" }
      }
    }
  }
  Add-Line "  Player.log 固定错误分类(仅本次进程时间窗、无原文): $(if($errorParts){$errorParts -join ' | '}else{'none'})"
}
Try-Do "代理模式/TUN/fake-IP/默认路由" {
  $clashProcesses = @(
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.ProcessName -match '(?i)clash|mihomo' }
  )
  $tunAdapters = @(
    Get-NetAdapter -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Status -eq "Up" -and
        ([string]$_.Name + " " + [string]$_.InterfaceDescription) -match
          '(?i)TUN|Wintun|Clash|Mihomo'
      }
  )
  $tunState = if ($tunAdapters.Count -gt 0) { "up" } else { "down" }

  $winInetMode = "unknown"
  try {
    $internetSettings = Get-ItemProperty `
      "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" `
      -ErrorAction Stop
    $winInetMode = if ([int]$internetSettings.ProxyEnable -eq 1) { "proxy" } else { "direct" }
  } catch {}

  $winHttpMode = "unknown"
  try {
    $winHttpText = (& netsh winhttp show proxy 2>&1 | Out-String)
    if ($winHttpText -match '(?i)Direct access|直接访问') {
      $winHttpMode = "direct"
    } elseif ($winHttpText -match '(?i)Proxy Server|代理服务器') {
      $winHttpMode = "proxy"
    }
  } catch {}

  $fakeIpState = "unknown"
  if ($env:BIDKING_DIAG_SKIP_NETWORK -ne "1") {
    try {
      $resolved = @(
        Resolve-DnsName -Name "release.bid-king.cn" -Type A `
          -DnsOnly -QuickTimeout -ErrorAction Stop
      )
      $fakeIpHit = @(
        $resolved | Where-Object {
          [string]$_.IPAddress -match '^198\.(18|19)\.'
        }
      ).Count -gt 0
      $fakeIpState = if ($fakeIpHit) { "hit" } else { "not_hit" }
    } catch {
      $fakeIpState = "unknown"
    }
  }

  $clashMode = "unknown"
  if ($clashProcesses.Count -eq 0 -and $tunState -eq "down" -and
      $winInetMode -eq "direct" -and $fakeIpState -eq "not_hit") {
    $clashMode = "off_native"
  }
  # A running Clash/TUN stack does not expose whether the current rule selected
  # DIRECT or a proxy node through these privacy-safe Windows facts. Keep it
  # unknown instead of guessing on_direct/on_proxy.
  Add-Line "  clash_mode=$clashMode  (off_native/on_direct/on_proxy/unknown)"
  Add-Line "  tun_state=$tunState  fake_ip_198_18=$fakeIpState"
  Add-Line "  WinINET=$winInetMode  WinHTTP=$winHttpMode"

  $defaultRoutes = @(
    Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue |
      Where-Object {
        $_.DestinationPrefix -eq "0.0.0.0/0" -and
        $_.State -ne "Invalid"
      } |
      Sort-Object RouteMetric, InterfaceMetric
  )
  Add-Line "  active_default_route_count=$($defaultRoutes.Count)"
  foreach ($route in $defaultRoutes) {
    $adapter = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue
    $description = [string]$adapter.InterfaceDescription
    $interfaceType = if ($description -match '(?i)Remote NDIS|手机|USB.*Ethernet') {
      "remote_ndis"
    } elseif ($description -match '(?i)Wi-?Fi|Wireless|WLAN|802\.11') {
      "wlan"
    } elseif ($description -match '(?i)TUN|Wintun|Clash|Mihomo|VPN|Virtual') {
      "virtual_tun"
    } elseif ($description -match '(?i)Ethernet|Realtek|Intel') {
      "ethernet"
    } else {
      "unknown"
    }
    Add-Line "    route interface_type=$interfaceType metric=$($route.RouteMetric)"
  }
  if ($defaultRoutes.Count -gt 1) {
    Add-Line "  [提示] 多出口风险：存在多个活动默认路由；本诊断不猜测游戏实际出口。"
  }
}
Try-Do "固定游戏端点 TCP 分类" {
  if ($env:BIDKING_DIAG_SKIP_NETWORK -eq "1") {
    Add-Line "  fixed_endpoint_tcp=skipped"
    return
  }
  function Test-DiagnosticTcpEndpoint($hostName, $port, $timeoutMs) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
      $task = $client.ConnectAsync([string]$hostName, [int]$port)
      if (-not $task.Wait([int]$timeoutMs)) { return "timeout" }
      $task.GetAwaiter().GetResult()
      return "success"
    } catch [System.Net.Sockets.SocketException] {
      switch ([string]$_.Exception.SocketErrorCode) {
        "ConnectionReset" { return "reset" }
        "ConnectionRefused" { return "refused" }
        "TimedOut" { return "timeout" }
        "NetworkUnreachable" { return "unreachable" }
        "HostUnreachable" { return "unreachable" }
        default { return "socket_error" }
      }
    } catch {
      return "error"
    } finally {
      $client.Dispose()
    }
  }
  $fixedEndpoints = @("8.133.187.124", "8.133.200.182")
  $tcpResults = @()
  for ($i = 0; $i -lt $fixedEndpoints.Count; $i++) {
    $classification = Test-DiagnosticTcpEndpoint $fixedEndpoints[$i] 443 2500
    $tcpResults += "endpoint_$($i + 1)=$classification"
  }
  Add-Line "  $($tcpResults -join ' | ')"
  Add-Line "  注: TCP success 仅证明 L4 建连，不证明 DNS/TLS/SNI/HTTP Upgrade/鉴权或游戏 WebSocket 成功。"
}
Try-Do "网络适配器" {
  Add-Line "  -- 网卡列表(用于判断实际流量路径，不单独作为根因证据) --"
  Get-NetAdapter -ErrorAction SilentlyContinue | Sort-Object Status | ForEach-Object {
    $virt = ($_.InterfaceDescription -match 'TAP|TUN|VPN|Virtual|WireGuard|Wintun|加速|UU|Clash|Mihomo|Oray|Pgy|蒲公英|ZeroTier|Hamachi|Radmin|Killer|Loopback|netcat')
    $flag = if ($_.Status -eq 'Up' -and $virt) { "  ⚠虚拟/加速网卡(在用!)" } elseif ($virt) { "  (虚拟, 未启用)" } else { "" }
    Add-Line ("    [{0,-4}] {1}  | {2}{3}" -f $_.Status, $_.Name, $_.InterfaceDescription, $flag)
  }
}
Try-Do "加速器/代理/组网/安全软件(运行状态与安装清单分开记录)" {
  $pat = 'uu|uuwz|xunyou|迅游|qqstv|clash|mihomo|letsvpn|v2ray|xray|trojan|wireguard|netch|surge|proxifier|shadowsock|' +
         'killer|smartbyte|rivet|oray|pgy|蒲公英|花生壳|zerotier|hamachi|radmin|tailscale|frp|组网|异地组网|局域网|' +
         'huorong|火绒|360安全|360杀毒|qqpcmgr|电脑管家|kaspersky|卡巴|norton|诺顿|mcafee|eset|bitdefender|avast|bdagent'
  $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match $pat } | Select-Object -Expand ProcessName -Unique
  if ($procs) { Add-Line "  [运行进程] $($procs -join ', ')  (正在运行，可能影响流量路径，但不是单独根因证据)" }
  $svcs = Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Running' -and ($_.Name + $_.DisplayName) -match $pat } | Select-Object -Expand DisplayName -Unique
  if ($svcs) { Add-Line "  [运行服务] $($svcs -join ', ')  (正在运行，需结合网卡/过滤驱动和抓包日志判断)" }
  $apps = @()
  foreach ($rk in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*') {
    Get-ItemProperty $rk -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match $pat } | ForEach-Object { $apps += $_.DisplayName }
  }
  if ($apps) { Add-Line "  [仅已安装] $(($apps | Sort-Object -Unique) -join ' | ')  (仅安装不证明正在拦截，也不据此建议卸载)" }
  if (-not ($procs -or $svcs -or $apps)) { Add-Line "  未检测到常见加速器/组网/安全软件。" }
  elseif ($procs -or $svcs) { Add-Line "  [提示] 运行中的网络软件是可能原因之一；应结合明确 pcap_open_live 错误、实际 NDIS 过滤和流量计数判断。" }
}
Try-Do "NDIS过滤驱动 + 网卡RSC(入站抓不到的深层原因)" {
  Add-Line "  -- 非微软的 NDIS 过滤驱动(除 Npcap 外的第三方 = 可疑拦截入站) --"
  # 不用 -IncludeHidden: 第三方过滤只绑在真实网卡上, 遍历隐藏 WAN Miniport 会拖慢 30s+。
  $tp = New-Object System.Collections.ArrayList
  Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object {
    $n = $_.Name
    Get-NetAdapterBinding -Name $n -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -and $_.ComponentID -notlike 'ms_*' -and $_.ComponentID -ne 'INSECURE_NPCAP' } | ForEach-Object {
      [void]$tp.Add(1); Add-Line ("    网卡[{0}] {1} ({2})  ⚠第三方过滤" -f $n, $_.DisplayName, $_.ComponentID)
    }
  }
  if ($tp.Count -eq 0) { Add-Line "    (除 Npcap 外无第三方过滤驱动)" }
  Add-Line "  -- 活动网卡接收侧 RSC(接收合并会把入站小包并成超大包 → Npcap 拆不回帧) --"
  Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -notmatch 'Loopback' } | ForEach-Object {
    $rsc = Get-NetAdapterRsc -Name $_.Name -ErrorAction SilentlyContinue
    if ($rsc) {
      $hint = if ($rsc.IPv4Enabled) { "  ⚠可尝试(管理员): Disable-NetAdapterRsc -Name *" } else { "" }
      Add-Line ("    [{0}] RSC IPv4={1} IPv6={2}{3}" -f $_.Name, $rsc.IPv4Enabled, $rsc.IPv6Enabled, $hint)
    }
  }
}
Try-Do "系统代理" {
  $k = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
  $en = (Get-ItemProperty -Path $k -Name ProxyEnable -ErrorAction SilentlyContinue).ProxyEnable
  if ($en -eq 1) { Add-Line "  系统代理: 开启(地址已省略；仅记录模式，不据此判根因)。" }
  else           { Add-Line "  系统代理: 关闭(正常)。" }
}

# ---------- 4. 与服务器连通性(只读 URL, 不读/不打印密钥) ----------
Write-BidKingDiagnosticProgress 5 "检查服务连通性"
Add-Sec "4. 服务器连通性(上传 / 版本)"
if ($env:BIDKING_DIAG_SKIP_NETWORK -eq "1") {
  Add-Line "  [自动化] 已跳过服务器网络探测。"
} else {
  Try-Do "连通性" {
    $cfg = Join-Path $Repo "scripts\sample_upload.local.ps1"
    $encCfg = Join-Path $Repo "data\processed\upload_config.enc"
    $base = $null
    if (Test-Path $encCfg) {
      $encSize = (Get-Item $encCfg).Length
      if ($encSize -le 0) { Add-Line "  [!] 加密上传配置 upload_config.enc 为空，包不完整。"; return }
      Add-Line "  加密上传配置 data\processed\upload_config.enc: 存在 ($encSize bytes，群发版正常，不解密/不显示密钥)。"
      # 不在诊断文本中解密上传配置；连通性改查客户端默认 TLS 服务。
      $base = "https://api.bidking.example.invalid"
    } elseif (Test-Path $cfg) {
      $txt = Get-Content $cfg -Raw -ErrorAction SilentlyContinue
      $mu = [regex]::Match($txt, 'BIDKING_SAMPLE_UPLOAD_URL\s*=\s*"([^"]+)"')
      if (-not $mu.Success) { Add-Line "  明文上传配置存在但未设 URL。"; return }
      $ingest = $mu.Groups[1].Value
      $base = $ingest -replace '/[^/]*$',''
      Add-Line "  明文上传地址(host): $([regex]::Replace($ingest,':\d+/.*$',':<port>/<path>'))  (密钥不显示)"
    } else {
      Add-Line "  [!] 未找到 data\processed\upload_config.enc 或 scripts\sample_upload.local.ps1：上传配置缺失，公版包不应如此。"
      return
    }
    foreach ($ep in @("version","healthz")) {
      try {
        $r = Invoke-WebRequest -Uri "$base/$ep" -TimeoutSec 5 -UseBasicParsing
        Add-Line "  GET /$ep -> HTTP $($r.StatusCode)  $(if($ep -eq 'version'){$r.Content})"
      } catch {
        $code = $null; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
        if ($code) { Add-Line "  GET /$ep -> HTTP $code  (连上了服务器, 但该端点缺/出错: 多半是服务器版本旧, 等部署新版即可)" }
        else       { Add-Line "  GET /$ep -> 连接失败: $($_.Exception.Message)  ⚠(连不上服务器: 防火墙/断网/服务器没开)" }
      }
    }
  }
}

# ---------- 5. 运行日志(错误真相在这里) ----------
Write-BidKingDiagnosticProgress 6 "汇总本机活动日志"
Add-Sec "5. 运行日志"
foreach ($NativeLine in $script:BidKingNativeStartupSummaryLines) { Add-Line $NativeLine }
if ($CurrentNativeSupersedesLaunch) {
  Add-Line "  [当前 native 启动] 较新的失败 run 已验证；下面旧 PowerShell/monitor 日志仅作历史辅助，不作为本次启动证据。"
}
Try-Do "启动阶段判定" {
  $nativeFailure = Join-Path (Split-Path -Parent $LogPath) "startup_failure.log"
  if (Test-Path -LiteralPath $nativeFailure -PathType Leaf) {
    $nativeFailureBytes = [System.IO.File]::ReadAllBytes($nativeFailure)
    if ($nativeFailureBytes.Length -le 1024) {
      $nativeFailureText = [System.Text.Encoding]::UTF8.GetString($nativeFailureBytes)
      $nativeFailureMatch = [regex]::Match(
        $nativeFailureText,
        '\Aschema=bidking_public_startup_failure_v1\r?\ncode=([a-z0-9_]+)\r?\n\z',
        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
      )
      if ($nativeFailureMatch.Success) {
        $nativeFailureCode = $nativeFailureMatch.Groups[1].Value
        if ($nativeFailureCode.StartsWith("integrity_", [StringComparison]::Ordinal) -or
            $nativeFailureCode.StartsWith("monitor_", [StringComparison]::Ordinal) -or
            $nativeFailureCode.StartsWith("package_generation_", [StringComparison]::Ordinal) -or
            $nativeFailureCode -cmatch '^client_exit_[0-9]+$') {
          Add-Line "  [最近 native 启动失败；可能属于历史启动] code=$nativeFailureCode"
        }
      }
    }
  }
  $startupStatus = Join-Path $LogPath "startup_status.log"
  if (Test-Path $startupStatus) {
    $startupLines = @(Get-Content $startupStatus -ErrorAction SilentlyContinue)
    $launchLineIndex = -1
    if ($CurrentLaunchId) {
      $launchNeedle = "launch_begin launch_id=$CurrentLaunchId"
      for ($i = $startupLines.Count - 1; $i -ge 0; $i--) {
        if ([string]$startupLines[$i] -like "*$launchNeedle*") { $launchLineIndex = $i; break }
      }
    }
    if ($launchLineIndex -ge 0) {
      Add-Line "  -- startup_status.log (本次 launch ID，尾 20 行) --"
      $currentStartupLines = @($startupLines[$launchLineIndex..($startupLines.Count - 1)])
      $currentStartupLines | Select-Object -Last 20 | ForEach-Object { Add-Line "    $_" }
    } else {
      Add-Line "  -- startup_status.log（历史辅助信息，不作为本次启动证据；尾 20 行）--"
      $startupLines | Select-Object -Last 20 | ForEach-Object { Add-Line "    $_" }
    }
  } else {
    Add-Line "  startup_status.log: 无(未记录 CIM 降级或陈旧 PID 回收)。"
  }
  $monitorOut = Join-Path $LogPath "monitor.stdout.log"
  $monitorErr = Join-Path $LogPath "monitor.stderr.log"
  $hasAnyMonitorLog = (Test-Path -LiteralPath $monitorOut) -or (Test-Path -LiteralPath $monitorErr)
  $hasCurrentMonitorLog = (Test-CurrentMonitorLog $monitorOut) -or
                          (Test-CurrentMonitorLog $monitorErr)
  if (-not $CurrentLaunchStartedAt) {
    Add-Line "  [启动阶段] 未找到可验证的本次 launch ID/启动时间；历史 monitor 日志不作为本次启动证据。"
  } elseif (-not $hasCurrentMonitorLog) {
    $historyHint = if ($hasAnyMonitorLog) { "（检测到历史日志，已排除本次归因）" } else { "" }
    if ($CurrentMonitorWasReused) {
      Add-Line "  [启动阶段] 本次启动复用已有 monitor，但连续日志未在本次 launch 后更新$historyHint；应检查 monitor 是否卡住或已经失活。"
    } else {
      Add-Line "  [启动阶段] 本次启动尚未创建 monitor 日志$historyHint；应优先检查启动器/提权/runtime/CIM 降级记录。"
    }
  } else {
    if ($CurrentMonitorWasReused) {
      Add-Line "  [启动阶段] 本次启动复用已有 monitor，连续日志已在本次 launch 后更新；日志尾部可能包含复用前记录。"
    } else {
      Add-Line "  [启动阶段] 本次启动已进入 monitor 日志创建阶段；若窗口秒退，请查看下面的 monitor/overlay 日志。"
    }
  }
}
Try-Do "上传状态" {
  $us = Join-Path $LogPath "upload_status.txt"
  if (Test-Path $us) { Add-Line "  upload_status.txt: $(Get-Content $us -Raw)" } else { Add-Line "  upload_status.txt: 无(未开调试或未上传过)。" }
}
function Tail-Log($name, $n) {
  $p = Join-Path $LogPath $name
  Add-Line ""
  if (Test-CurrentMonitorLog $p) {
    if ($CurrentMonitorWasReused) {
      Add-Line "  -- $name（本次启动复用 monitor 的连续日志；尾部可能含复用前记录；尾 $n 行）--"
    } else {
      Add-Line "  -- $name (本次启动证据，尾 $n 行) --"
    }
  } elseif (Test-Path $p) {
    Add-Line "  -- $name（历史辅助信息，不作为本次启动证据；尾 $n 行）--"
  } else {
    Add-Line "  -- $name (尾 $n 行) --"
  }
  if (Test-Path $p) { Get-Content $p -Tail $n -ErrorAction SilentlyContinue | ForEach-Object { Add-Line "    $_" } }
  else { Add-Line "    (无此日志, 可能从没成功启动过)" }
}
Try-Do "stdout 尾部" { Tail-Log "monitor.stdout.log" 50 }
Try-Do "stderr 尾部" { Tail-Log "monitor.stderr.log" 80 }
Try-Do "悬浮窗启动状态" {
  $overlayErr = Join-Path $LogPath "overlay.stderr.log"
  $runtimeStatus = Join-Path $LogPath "hero_ref_ui_runtime_status.json"
  if ($CurrentOverlayLogSession) {
    $guardState = [string]$CurrentOverlayLogSession.guard_state
    if ($guardState -notin @("started", "alive", "dead", "unknown")) {
      $guardState = "unknown"
    }
    $exitClass = [string]$CurrentOverlayLogSession.exit_class
    if ($exitClass -notin @(
      "running",
      "overlay_exited_cleanly",
      "overlay_exception",
      "monitor_exit",
      "process_query_unknown"
    )) {
      $exitClass = "process_query_unknown"
    }
    $exitCode = "none"
    if ($CurrentOverlayLogSession.PSObject.Properties.Name -contains "exit_code") {
      try { $exitCode = [string][int]$CurrentOverlayLogSession.exit_code } catch { $exitCode = "invalid" }
    }
    $stderrClass = if (Test-CurrentOverlayFile $overlayErr) {
      Get-OverlayStderrClass $overlayErr
    } else {
      "unavailable"
    }
    $runtimeEvent = Get-SafeUiRuntimeEvent $runtimeStatus
    Add-Line (
      "  overlay_launch: guard=$guardState exit_class=$exitClass exit_code=$exitCode " +
      "stderr_class=$stderrClass runtime_event=$runtimeEvent"
    )
  } elseif ((Test-Path -LiteralPath $overlayErr) -or (Test-Path -LiteralPath $runtimeStatus)) {
    Add-Line "  overlay_launch: historical_or_unattributed（不作为本次启动证据）"
  } else {
    Add-Line "  overlay_launch: unavailable"
  }
}
Try-Do "错误关键词扫描" {
  $errf = Join-Path $LogPath "monitor.stderr.log"
  if (Test-Path $errf) {
    $hits = Select-String -Path $errf -Pattern 'Traceback|Error|Exception|Permission|Npcap|Access is denied|拒绝访问' -ErrorAction SilentlyContinue | Select-Object -Last 15
    if ($hits) {
      Add-Line ""
      if (Test-CurrentMonitorLog $errf) {
        if ($CurrentMonitorWasReused) {
          Add-Line "  -- 复用 monitor 错误关键词（可能含复用前记录）--"
        } else {
          Add-Line "  -- 本次启动错误关键词命中 --"
        }
      } else {
        Add-Line "  -- 历史错误关键词命中（历史辅助信息，不作为本次启动证据）--"
      }
      $hits | ForEach-Object { Add-Line "    L$($_.LineNumber): $($_.Line.Trim())" }
    }
  }
}

# ---------- 6. 抓包健康度(诊断丢包/结算件数缺失的关键, 5606 类问题看这里) ----------
Add-Sec "6. 抓包健康度(capture_source_status.json + 结算帧件数日志)"
Try-Do "capture_source_status" {
  $cs = Join-Path $LogPath "capture_source_status.json"
  if (Test-Path $cs) {
    $j = Get-Content $cs -Raw -Encoding UTF8 | ConvertFrom-Json
    Add-Line "  source=$($j.source)  active_session=$($j.active_session_id)"
    Add-Line "  accepted_frames=$($j.accepted_frames)  ignored_frames=$($j.ignored_frames)  dropped_bytes=$($j.dropped_bytes)"
    if ($j.error_code) {
      Add-Line "  [明确抓包错误] code=$($j.error_code)"
      if ($j.error_message) { Add-Line "  message=$($j.error_message)" }
      if ($j.error_hint) { Add-Line "  hint=$($j.error_hint)" }
    }
    if ($j.capture_devices) {
      Add-Line "  -- 抓包设备状态 --"
      foreach ($device in $j.capture_devices) {
        Add-Line ("    {0} | state={1} mode={2} fallback_attempted={3} error={4}" -f $device.description, $device.state, $device.capture_mode, $device.non_promisc_fallback_attempted, $device.error_code)
      }
    }
    if ($j.PSObject.Properties.Name -contains 'dropped_bytes_delta') { Add-Line "  dropped_bytes_delta(最近一个状态周期)=$($j.dropped_bytes_delta)" }
    if ($j.PSObject.Properties.Name -contains 'kernel_drop')         { Add-Line "  kernel_drop=$($j.kernel_drop)  kernel_recv=$($j.kernel_recv)  (内核缓冲丢包, >0 即用户态看不见的丢包)" }
    if ($j.PSObject.Properties.Name -contains 'queue_full_drops')    { Add-Line "  queue_full_drops=$($j.queue_full_drops)  (用户态队列满丢包, >0 即消费跟不上)" }
    if ($j.ignored_reasons) { Add-Line "  ignored_reasons: $($j.ignored_reasons | ConvertTo-Json -Compress)" }
    if ($j.dropped_bytes -gt 0 -or ($j.PSObject.Properties.Name -contains 'kernel_drop' -and $j.kernel_drop -gt 0)) {
      if ($j.source -eq "wss_local") {
        Add-Line "  [提示] WSS 应用帧边界出现丢弃；请确认按冷启动顺序进入，并把本诊断私发作者。"
      } else {
        Add-Line "  [提示] 出现丢包 → 可能导致结算帧截断、件数偏少、估值/结算偏低。建议结合网卡/驱动状态，检查 kernel/queue/ifdrop，必要时调高 BIDKING_NPCAP_BUFFER_MB 并减少高 CPU 占用。"
      }
    }
  } else { Add-Line "  capture_source_status.json: 无(抓包从未成功启动过, 或还没收到任何包)。" }
}
Try-Do "WSS 生命周期状态" {
  $lifecyclePath = Join-Path $LogPath "lifecycle_status.json"
  if (Test-Path -LiteralPath $lifecyclePath) {
    $lifecycle = Get-Content -LiteralPath $lifecyclePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $allowedPhases = @(
      "disabled", "policy_disabled", "policy_unknown", "ready",
      "game_active", "game_unknown", "ui_unknown", "draining", "stopped"
    )
    $allowedProcessStates = @("running", "exited", "unknown")
    $allowedPolicyStates = @("enabled", "disabled", "unknown")
    $phase = [string]$lifecycle.phase
    $gameState = [string]$lifecycle.game_state
    $uiState = [string]$lifecycle.ui_state
    $policyState = [string]$lifecycle.policy_state
    if ($allowedPhases -notcontains $phase) { $phase = "unknown" }
    if ($allowedProcessStates -notcontains $gameState) { $gameState = "unknown" }
    if ($allowedProcessStates -notcontains $uiState) { $uiState = "unknown" }
    if ($allowedPolicyStates -notcontains $policyState) { $policyState = "unknown" }
    Add-Line "  WSS 生命周期: phase=$phase game=$gameState ui=$uiState policy=$policyState"
    if ([string]$lifecycle.stop_reason -eq "ui_detached_game_exited") {
      Add-Line "  最近安全结束原因: 悬浮窗已关闭且游戏已退出"
    }
  } elseif ($WssExpected) {
    Add-Line "  WSS 生命周期状态: 无(旧版本、未启动成功或当前关闭了自动生命周期回收)。"
  }
}
Try-Do "结算帧件数日志" {
  $so = Join-Path $LogPath "monitor.stdout.log"
  if (Test-Path $so) {
    $settle = Select-String -Path $so -Pattern '^\[settle\]' -ErrorAction SilentlyContinue | Select-Object -Last 12
    if ($settle) {
      Add-Line ""
      if (Test-CurrentMonitorLog $so) {
        if ($CurrentMonitorWasReused) {
          Add-Line "  -- 复用 monitor [settle] 行（可能含复用前记录）--"
        } else {
          Add-Line "  -- 本次启动结算采集摘要 --"
        }
      } else {
        Add-Line "  -- 历史 [settle] 行（历史辅助信息，不作为本次启动证据）--"
      }
      $settle | ForEach-Object { Add-Line "    L$($_.LineNumber): $($_.Line.Trim())" }
    } elseif (Test-CurrentMonitorLog $so) {
      Add-Line "  本次启动结算采集摘要: 无(本会话尚未完成结算，或当前日志不包含该摘要)。"
    } else {
      Add-Line "  历史 monitor.stdout.log 中无 [settle] 行；不作为本次启动证据。"
    }
    $st = Select-String -Path $so -Pattern '^\[(npcap|wss|status)\]' -ErrorAction SilentlyContinue | Select-Object -Last 3
    if ($st) {
      Add-Line ""
      $statusLabel = if (Test-CurrentMonitorLog $so) {
        if ($CurrentMonitorWasReused) {
          "复用 monitor 抓包状态行（连续日志，可能含复用前记录）"
        } else {
          "本次启动抓包状态行"
        }
      } else {
        "历史抓包状态行（历史辅助信息，不作为本次启动证据）"
      }
      Add-Line "  -- $statusLabel --"
      $st | ForEach-Object { Add-Line "    L$($_.LineNumber): $($_.Line.Trim())" }
    }
  } else { Add-Line "  monitor.stdout.log: 无(抓包没成功启动过)。" }
}

# ---------- 写出 ----------
$stream = $null
$writer = $null
try {
  # CreateNew is the final no-overwrite gate.  If another process won this name after the
  # candidate scan, allocate the next suffix rather than replacing its report.
  foreach ($suffix in 1..999) {
    $tail = if ($suffix -eq 1) { "" } else { "-$suffix" }
    $candidate = Join-Path $ExportDir "BidKing诊断_$stamp$tail.txt"
    try {
      $stream = New-Object System.IO.FileStream(
        $candidate,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
      )
      $outFile = $candidate
      break
    } catch [System.IO.IOException] {
      $stream = $null
    }
  }
  if (-not $stream) { throw "no unique diagnostic output name available" }
  $writer = New-Object System.IO.StreamWriter(
    $stream,
    (New-Object System.Text.UTF8Encoding($true))
  )
  $writer.Write($sb.ToString())
  $writer.Flush()
  $stream.Flush($true)
} catch {
  if ($writer) { $writer.Dispose() }
  elseif ($stream) { $stream.Dispose() }
  throw
} finally {
  if ($writer) { $writer.Dispose() }
  elseif ($stream) { $stream.Dispose() }
}
Write-BidKingDiagnosticProgress 7 "安全写入诊断报告"
Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "[完成] 诊断报告已生成: $outFile" -ForegroundColor Green
Write-Host " 请把这个文件【仅私发给支持人员】排查，不要发群或公开上传。" -ForegroundColor Green
Write-Host " (报告包含系统、网卡与软件环境信息)" -ForegroundColor DarkGray
Write-Host "==================================================" -ForegroundColor Cyan
# 顺手在资源管理器里选中它(让用户立刻看到文件在哪)。自动化/测试可置 BIDKING_DIAG_NOEXPLORER=1 跳过。
if (-not $env:BIDKING_DIAG_NOEXPLORER) {
  try { Start-Process explorer.exe "/select,`"$outFile`"" } catch {}
}
# 仅在直接运行 PS(右键→用 PowerShell 运行, 未经 .bat)时等待按键;
# 经 收集诊断.bat 时 .bat 会设 BIDKING_DIAG_NONINTERACTIVE=1 跳过本段, 由 .bat 末尾统一 pause(避免双重等待)。
if (-not $env:BIDKING_DIAG_NONINTERACTIVE) {
  Write-Host "按任意键关闭..."
  try { $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") } catch { Start-Sleep -Seconds 5 }
}
# The native lifecycle treats the child process status as a strict product contract.
# Close the successful path explicitly so ambient native-command status cannot turn a
# fully written 7/7 report into an indistinguishable lifecycle failure.
exit 0
