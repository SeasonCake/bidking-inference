param(
  [ValidateSet("preflight", "watch")]
  [string]$Mode = "preflight",
  [switch]$DryRun,
  [switch]$Apply,
  [string]$PackageRoot = "",
  [string]$ComponentPath = "",
  [string]$LeasePath = "",
  [string]$ExpectedPackageGenerationIdentity = "",
  [string]$ExpectedRuntimeIdentity = "",
  [string]$ExpectedComponentIdentity = "",
  [string]$ExpectedMonitorEpoch = "",
  [int]$MonitorProcessId = 0,
  [long]$MonitorStartedAtMs = 0,
  [string]$GameProcessName = "BidKing"
)

# Public, read-only entry plus functions consumed only after BidKingLive's local-
# integrity gate.  Direct invocation is always dry-run.  Certificate mutation is
# possible only through the explicit function calls used by the sealed launcher or
# repair payload; this script has no command-line install/remove mode.

$ErrorActionPreference = "Stop"
$script:BidKingInternetCafeClassifications = @(
  "ready",
  "cert_store_write_denied",
  "policy_locked",
  "profile_ephemeral",
  "uac_disabled",
  "component_write_denied",
  "restore_disk_suspected",
  "interactive_user_mismatch",
  "component_execution_blocked",
  "driver_load_denied",
  "package_root_unsupported",
  "foreign_cert_or_state",
  "user_cancelled",
  "unknown"
)
$script:BidKingMaximumUnixTimeMilliseconds = [long]253402300799999

function Test-BidKingInternetCafeClassification([string]$Value) {
  return $script:BidKingInternetCafeClassifications -ccontains $Value
}

function Get-BidKingSha256Hex([byte[]]$Bytes) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Get-BidKingTextSha256([string]$Text) {
  return Get-BidKingSha256Hex ([System.Text.UTF8Encoding]::new($false).GetBytes($Text))
}

function Test-BidKingSha256([string]$Value) {
  return [bool]($Value -cmatch '^[0-9a-f]{64}$')
}

function Test-BidKingGuidText([string]$Value) {
  $parsed = [guid]::Empty
  return [guid]::TryParseExact($Value, "D", [ref]$parsed)
}

function Test-BidKingExactJsonInteger([object]$Value, [long]$Minimum, [long]$Maximum) {
  if ($null -eq $Value -or $Value -is [bool]) { return $false }
  $type = $Value.GetType()
  if ($type -ne [int] -and $type -ne [long]) { return $false }
  return $Value -ge $Minimum -and $Value -le $Maximum
}

function Move-BidKingInternetCafeJsonWhitespace {
  param([string]$Text, [ref]$Offset)
  while (
    $Offset.Value -lt $Text.Length -and
    " `t`r`n".IndexOf($Text[$Offset.Value]) -ge 0
  ) { $Offset.Value += 1 }
}

function Read-BidKingInternetCafeJsonStringToken {
  param([string]$Text, [ref]$Offset)
  if ($Offset.Value -ge $Text.Length -or $Text[$Offset.Value] -ne '"') {
    throw "json string expected"
  }
  $start = $Offset.Value
  $Offset.Value += 1
  while ($Offset.Value -lt $Text.Length) {
    $character = $Text[$Offset.Value]
    if ($character -eq '"') {
      $Offset.Value += 1
      return $Text.Substring($start, $Offset.Value - $start)
    }
    if ([int]$character -lt 0x20) { throw "json control character rejected" }
    if ([int]$character -eq 0x5c) {
      $Offset.Value += 1
      if ($Offset.Value -ge $Text.Length) { throw "json escape incomplete" }
      $escape = $Text[$Offset.Value]
      if ($escape -eq 'u') {
        if ($Offset.Value + 4 -ge $Text.Length) { throw "json unicode escape incomplete" }
        $digits = $Text.Substring($Offset.Value + 1, 4)
        if ($digits -cnotmatch '^[0-9a-fA-F]{4}$') { throw "json unicode escape invalid" }
        $Offset.Value += 5
        continue
      }
      if ('"\/bfnrt'.IndexOf($escape) -lt 0) { throw "json escape invalid" }
    }
    $Offset.Value += 1
  }
  throw "json string unterminated"
}

function Read-BidKingInternetCafeJsonValue {
  param([string]$Text, [ref]$Offset)
  Move-BidKingInternetCafeJsonWhitespace $Text $Offset
  if ($Offset.Value -ge $Text.Length) { throw "json value expected" }
  $character = $Text[$Offset.Value]
  if ($character -eq '{') {
    Read-BidKingInternetCafeJsonObject $Text $Offset
    return
  }
  if ($character -eq '[') {
    Read-BidKingInternetCafeJsonArray $Text $Offset
    return
  }
  if ($character -eq '"') {
    $null = Read-BidKingInternetCafeJsonStringToken $Text $Offset
    return
  }
  $start = $Offset.Value
  while ($Offset.Value -lt $Text.Length) {
    $character = $Text[$Offset.Value]
    if (" `t`r`n".IndexOf($character) -ge 0 -or $character -in @(',', ']', '}')) { break }
    $Offset.Value += 1
  }
  if ($Offset.Value -eq $start) { throw "json primitive expected" }
  $primitive = $Text.Substring($start, $Offset.Value - $start)
  if (-not [regex]::IsMatch(
    $primitive,
    '^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)$',
    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
  )) { throw "json primitive rejected" }
  $decoded = $primitive | ConvertFrom-Json -ErrorAction Stop
  if ($null -ne $decoded -and $decoded -isnot [bool]) {
    $decodedType = $decoded.GetType()
    if ($decodedType -notin @([int], [long], [double], [decimal])) {
      throw "json numeric type rejected"
    }
    if ($decodedType -eq [double] -and (
      [double]::IsNaN($decoded) -or [double]::IsInfinity($decoded)
    )) { throw "json non-finite number rejected" }
  }
}

function Read-BidKingInternetCafeJsonArray {
  param([string]$Text, [ref]$Offset)
  if ($Text[$Offset.Value] -ne '[') { throw "json array expected" }
  $Offset.Value += 1
  Move-BidKingInternetCafeJsonWhitespace $Text $Offset
  if ($Offset.Value -lt $Text.Length -and $Text[$Offset.Value] -eq ']') {
    $Offset.Value += 1
    return
  }
  while ($true) {
    Read-BidKingInternetCafeJsonValue $Text $Offset
    Move-BidKingInternetCafeJsonWhitespace $Text $Offset
    if ($Offset.Value -ge $Text.Length) { throw "json array unterminated" }
    if ($Text[$Offset.Value] -eq ']') { $Offset.Value += 1; return }
    if ($Text[$Offset.Value] -ne ',') { throw "json array delimiter expected" }
    $Offset.Value += 1
  }
}

function Read-BidKingInternetCafeJsonObject {
  param([string]$Text, [ref]$Offset)
  if ($Text[$Offset.Value] -ne '{') { throw "json object expected" }
  $Offset.Value += 1
  $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  Move-BidKingInternetCafeJsonWhitespace $Text $Offset
  if ($Offset.Value -lt $Text.Length -and $Text[$Offset.Value] -eq '}') {
    $Offset.Value += 1
    return
  }
  while ($true) {
    $token = Read-BidKingInternetCafeJsonStringToken $Text $Offset
    $key = $token | ConvertFrom-Json -ErrorAction Stop
    if ($key -isnot [string]) { throw "json object key rejected" }
    $normalizedKey = $key.Normalize([Text.NormalizationForm]::FormC)
    if (-not $seen.Add($normalizedKey)) { throw "json object key duplicated" }
    Move-BidKingInternetCafeJsonWhitespace $Text $Offset
    if ($Offset.Value -ge $Text.Length -or $Text[$Offset.Value] -ne ':') {
      throw "json object colon expected"
    }
    $Offset.Value += 1
    Read-BidKingInternetCafeJsonValue $Text $Offset
    Move-BidKingInternetCafeJsonWhitespace $Text $Offset
    if ($Offset.Value -ge $Text.Length) { throw "json object unterminated" }
    if ($Text[$Offset.Value] -eq '}') { $Offset.Value += 1; return }
    if ($Text[$Offset.Value] -ne ',') { throw "json object delimiter expected" }
    $Offset.Value += 1
    Move-BidKingInternetCafeJsonWhitespace $Text $Offset
  }
}

function Assert-BidKingInternetCafeJsonObject([string]$Json) {
  $offset = 0
  Move-BidKingInternetCafeJsonWhitespace $Json ([ref]$offset)
  if ($offset -ge $Json.Length -or $Json[$offset] -ne '{') { throw "json root rejected" }
  Read-BidKingInternetCafeJsonObject $Json ([ref]$offset)
  Move-BidKingInternetCafeJsonWhitespace $Json ([ref]$offset)
  if ($offset -ne $Json.Length) { throw "json trailing data rejected" }
}

function Get-BidKingInternetCafeResult(
  [string]$Classification,
  [string]$Phase,
  [string]$StoreAccess = "not_checked",
  [string]$ProfileState = "unknown",
  [string]$ComponentState = "not_checked",
  [string]$DriverState = "not_checked",
  [bool]$IsAdmin = $false,
  [string]$EnableLuaState = "unknown"
) {
  if (-not (Test-BidKingInternetCafeClassification $Classification)) {
    throw "invalid internet-cafe classification"
  }
  $Result = if ($Classification -eq "ready") {
    "supported"
  } elseif ($Classification -eq "user_cancelled") {
    "cancelled"
  } else {
    "internet_cafe_realtime_unsupported"
  }
  $NextStep = switch ($Classification) {
    "ready" { "continue_startup" }
    "user_cancelled" { "retry_and_confirm" }
    "profile_ephemeral" { "ask_cafe_admin_for_persistent_profile" }
    "interactive_user_mismatch" { "sign_in_with_same_interactive_admin" }
    "component_write_denied" { "ask_cafe_admin_to_allow_component_cache" }
    "component_execution_blocked" { "ask_cafe_admin_to_allow_verified_component" }
    "driver_load_denied" { "ask_cafe_admin_to_allow_verified_driver" }
    "package_root_unsupported" { "use_local_plain_directory" }
    "restore_disk_suspected" { "ask_cafe_admin_for_persistent_storage" }
    default { "send_diagnostics_privately" }
  }
  return [pscustomobject][ordered]@{
    schema_version = 1
    phase = $Phase
    classification = $Classification
    result = $Result
    next_step = $NextStep
    mutation_performed = $false
    store_access = $StoreAccess
    profile_state = $ProfileState
    component_state = $ComponentState
    driver_state = $DriverState
    is_admin = $IsAdmin
    enable_lua_state = $EnableLuaState
    privacy = "fixed-enums-only"
  }
}

function New-BidKingInternetCafeProductionAdapter {
  $GetCurrentSid = {
    try {
      return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    } catch { return "unknown" }
  }
  $GetInteractiveSid = {
    try {
      $row = Get-CimInstance -ClassName Win32_ComputerSystem -OperationTimeoutSec 3 -ErrorAction Stop
      $name = [string]$row.UserName
      if (-not $name) { return "unknown" }
      return ([Security.Principal.NTAccount]::new($name)).Translate(
        [Security.Principal.SecurityIdentifier]
      ).Value
    } catch { return "unknown" }
  }
  $GetIsAdmin = {
    try {
      return ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator
      )
    } catch { return $false }
  }
  $GetEnableLuaState = {
    try {
      $value = Get-ItemPropertyValue `
        -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" `
        -Name EnableLUA -ErrorAction Stop
      if ([int]$value -eq 0) { return "disabled" }
      if ([int]$value -eq 1) { return "enabled" }
      return "unknown"
    } catch { return "unknown" }
  }
  $GetProfileState = {
    param([string]$Sid)
    try {
      $escaped = $Sid.Replace("'", "''")
      $rows = @(Get-CimInstance -ClassName Win32_UserProfile `
        -Filter ("SID = '{0}'" -f $escaped) -OperationTimeoutSec 3 -ErrorAction Stop)
      if ($rows.Count -ne 1) { return "unknown" }
      $status = [int]$rows[0].Status
      if (($status -band 1) -ne 0) { return "temporary" }
      if (($status -band 4) -ne 0) { return "mandatory" }
      return "persistent"
    } catch { return "unknown" }
  }
  $GetPackageRootState = {
    param([string]$Path)
    try {
      if (-not $Path -or -not [IO.Path]::IsPathRooted($Path) -or $Path.StartsWith("\\")) {
        return "unsupported"
      }
      $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
      if (-not $item.PSIsContainer) { return "unsupported" }
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        return "unsupported"
      }
      return "supported"
    } catch { return "unknown" }
  }
  $GetComponentState = {
    param([string]$Path)
    try {
      if (-not $Path -or -not [IO.Path]::IsPathRooted($Path)) { return "unknown" }
      $current = Split-Path -Parent $Path
      while ($current -and -not (Test-Path -LiteralPath $current)) {
        $next = Split-Path -Parent $current
        if ($next -eq $current) { break }
        $current = $next
      }
      if (-not $current) { return "unknown" }
      $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        return "restore_disk_suspected"
      }
      if (($item.Attributes -band [IO.FileAttributes]::ReadOnly) -ne 0) {
        return "write_denied"
      }
      return "available"
    } catch [System.UnauthorizedAccessException] { return "write_denied" }
    catch [System.Security.SecurityException] { return "policy_locked" }
    catch { return "unknown" }
  }
  $ProbeStore = {
    $store = $null
    try {
      $store = [Security.Cryptography.X509Certificates.X509Store]::new(
        [Security.Cryptography.X509Certificates.StoreName]::Root,
        [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
      )
      $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
      return "available"
    } catch [System.UnauthorizedAccessException] { return "write_denied" }
    catch [System.Security.SecurityException] { return "policy_locked" }
    catch { return "unknown" }
    finally { if ($store) { $store.Dispose() } }
  }
  $ListCertificates = {
    param([string]$Thumbprint)
    $store = $null
    try {
      $store = [Security.Cryptography.X509Certificates.X509Store]::new(
        [Security.Cryptography.X509Certificates.StoreName]::Root,
        [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
      )
      $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
      return @($store.Certificates | Where-Object {
        [string]::Equals($_.Thumbprint, $Thumbprint, [StringComparison]::OrdinalIgnoreCase)
      } | ForEach-Object {
        [pscustomobject]@{
          thumbprint = $_.Thumbprint.ToUpperInvariant()
          raw_b64 = [Convert]::ToBase64String($_.RawData)
        }
      })
    } finally { if ($store) { $store.Dispose() } }
  }
  $AddCertificate = {
    param([string]$RawBase64)
    $store = $null
    $certificate = $null
    try {
      $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
        [Convert]::FromBase64String($RawBase64)
      )
      $store = [Security.Cryptography.X509Certificates.X509Store]::new(
        [Security.Cryptography.X509Certificates.StoreName]::Root,
        [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
      )
      $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
      $store.Add($certificate)
    } finally {
      if ($store) { $store.Dispose() }
      if ($certificate) { $certificate.Dispose() }
    }
  }
  $RemoveCertificate = {
    param([string]$Thumbprint, [string]$RawBase64)
    $store = $null
    try {
      $store = [Security.Cryptography.X509Certificates.X509Store]::new(
        [Security.Cryptography.X509Certificates.StoreName]::Root,
        [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
      )
      $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
      $matches = @($store.Certificates | Where-Object {
        [string]::Equals($_.Thumbprint, $Thumbprint, [StringComparison]::OrdinalIgnoreCase) -and
        [Convert]::ToBase64String($_.RawData) -ceq $RawBase64
      })
      if ($matches.Count -ne 1) { throw "certificate identity changed" }
      $store.Remove($matches[0])
    } finally { if ($store) { $store.Dispose() } }
  }
  $GetGameState = {
    param([string]$ProcessName)
    try {
      $rows = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
      if ($rows.Count -gt 0) { return "alive" }
      return "absent"
    } catch { return "unknown" }
  }
  $GetMonitorState = {
    param([int]$ProcessId, [long]$StartedAtMs)
    if ($ProcessId -le 0) { return "absent" }
    try {
      $process = Get-Process -Id $ProcessId -ErrorAction Stop
      $actualMs = ([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()
      if ($actualMs -ne $StartedAtMs) { return "foreign" }
      return "alive"
    } catch [Microsoft.PowerShell.Commands.ProcessCommandException] { return "absent" }
    catch { return "unknown" }
  }
  $ProtectLeaseRoot = {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
      New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
    $admins = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    $system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($admins, $system)) {
      $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
          [Security.AccessControl.InheritanceFlags]::ObjectInherit,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
      )
      $security.AddAccessRule($rule)
    }
    [IO.Directory]::SetAccessControl($Path, $security)
    $verified = [IO.Directory]::GetAccessControl($Path)
    if (-not $verified.AreAccessRulesProtected) { throw "lease ACL not protected" }
  }
  return [pscustomobject]@{
    GetCurrentSid = $GetCurrentSid
    GetInteractiveSid = $GetInteractiveSid
    GetIsAdmin = $GetIsAdmin
    GetEnableLuaState = $GetEnableLuaState
    GetProfileState = $GetProfileState
    GetPackageRootState = $GetPackageRootState
    GetComponentState = $GetComponentState
    ProbeStore = $ProbeStore
    ListCertificates = $ListCertificates
    AddCertificate = $AddCertificate
    RemoveCertificate = $RemoveCertificate
    GetGameState = $GetGameState
    GetMonitorState = $GetMonitorState
    ProtectLeaseRoot = $ProtectLeaseRoot
  }
}

function Invoke-BidKingInternetCafePreflight {
  param(
    [Parameter(Mandatory = $true)]$Adapter,
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [Parameter(Mandatory = $true)][string]$ComponentPath
  )
  $currentSid = & $Adapter.GetCurrentSid
  $interactiveSid = & $Adapter.GetInteractiveSid
  $isAdmin = [bool](& $Adapter.GetIsAdmin)
  $enableLuaState = [string](& $Adapter.GetEnableLuaState)
  $profileState = if ($currentSid -ne "unknown") {
    [string](& $Adapter.GetProfileState $currentSid)
  } else { "unknown" }
  $packageRootState = [string](& $Adapter.GetPackageRootState $PackageRoot)
  $componentState = [string](& $Adapter.GetComponentState $ComponentPath)
  $storeAccess = [string](& $Adapter.ProbeStore)

  $classification = "ready"
  if ($currentSid -eq "unknown" -or $interactiveSid -eq "unknown") {
    $classification = "unknown"
  } elseif ($currentSid -cne $interactiveSid) {
    $classification = "interactive_user_mismatch"
  } elseif ($packageRootState -eq "unsupported") {
    $classification = "package_root_unsupported"
  } elseif ($packageRootState -eq "unknown") {
    $classification = "unknown"
  } elseif ($profileState -in @("temporary", "mandatory")) {
    $classification = "profile_ephemeral"
  } elseif ($profileState -eq "unknown") {
    $classification = "unknown"
  } elseif ($componentState -eq "write_denied") {
    $classification = "component_write_denied"
  } elseif ($componentState -eq "execution_blocked") {
    $classification = "component_execution_blocked"
  } elseif ($componentState -eq "restore_disk_suspected") {
    $classification = "restore_disk_suspected"
  } elseif ($componentState -in @("unknown", "policy_locked")) {
    $classification = $(if ($componentState -eq "policy_locked") { "policy_locked" } else { "unknown" })
  } elseif ($storeAccess -eq "write_denied") {
    $classification = "cert_store_write_denied"
  } elseif ($storeAccess -eq "policy_locked") {
    $classification = "policy_locked"
  } elseif ($storeAccess -ne "available") {
    $classification = "unknown"
  }
  # EnableLUA=0 is evidence, not an automatic failure.  An interactive-SID mismatch
  # or a concrete store/driver denial above remains decisive.
  return Get-BidKingInternetCafeResult `
    $classification "preflight" $storeAccess $profileState $componentState `
    "not_checked" $isAdmin $enableLuaState
}

function Write-BidKingInternetCafeReceipt {
  param([Parameter(Mandatory = $true)]$Result)
  if (-not (Test-BidKingInternetCafeClassification ([string]$Result.classification))) {
    throw "invalid receipt classification"
  }
  $documents = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)
  if (-not $documents) { throw "documents unavailable" }
  $exports = Join-Path $documents "BidKingLive\exports"
  New-Item -ItemType Directory -Path $exports -Force | Out-Null
  $path = Join-Path $exports ("BidKingInternetCafe-{0}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss-fff"))
  $safe = [ordered]@{
    schema_version = 1
    recorded_at = (Get-Date).ToUniversalTime().ToString("o")
    phase = [string]$Result.phase
    classification = [string]$Result.classification
    result = [string]$Result.result
    next_step = [string]$Result.next_step
    mutation_performed = [bool]$Result.mutation_performed
    store_access = [string]$Result.store_access
    profile_state = [string]$Result.profile_state
    component_state = [string]$Result.component_state
    driver_state = [string]$Result.driver_state
    is_admin = [bool]$Result.is_admin
    enable_lua_state = [string]$Result.enable_lua_state
    privacy = "fixed-enums-only"
  }
  [IO.File]::WriteAllText(
    $path,
    ($safe | ConvertTo-Json -Compress),
    [Text.UTF8Encoding]::new($false)
  )
  return $path
}

function Get-BidKingProductCaIdentity([byte[]]$CanonicalDer) {
  $certificate = $null
  try {
    if (-not $CanonicalDer -or $CanonicalDer.Length -le 0) { return $null }
    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
      $CanonicalDer
    )
    $subjectSimple = $certificate.GetNameInfo(
      [Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
      $false
    )
    $issuerSimple = $certificate.GetNameInfo(
      [Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
      $true
    )
    $basicConstraints = @(
      $certificate.Extensions | Where-Object {
        $_ -is [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]
      }
    )
    if (
      $subjectSimple -cne "mitmproxy" -or
      $issuerSimple -cne "mitmproxy" -or
      [string]$certificate.Subject -cne [string]$certificate.Issuer -or
      $basicConstraints.Count -ne 1 -or
      -not [bool]$basicConstraints[0].CertificateAuthority
    ) { return $null }
    return [pscustomobject]@{
      thumbprint = $certificate.Thumbprint.ToUpperInvariant()
      subject = [string]$certificate.Subject
      issuer = [string]$certificate.Issuer
      raw_b64 = [Convert]::ToBase64String([byte[]]$certificate.RawData)
      raw_sha256 = Get-BidKingSha256Hex ([byte[]]$certificate.RawData)
    }
  } catch { return $null }
  finally { if ($certificate) { $certificate.Dispose() } }
}

function Get-BidKingCertificateSnapshot([string]$CertificatePath) {
  $stream = [IO.File]::Open(
    $CertificatePath,
    [IO.FileMode]::Open,
    [IO.FileAccess]::Read,
    [IO.FileShare]::Read
  )
  try {
    if ($stream.Length -le 0 -or $stream.Length -gt 1048576) {
      throw "certificate size invalid"
    }
    $raw = [byte[]]::new([int]$stream.Length)
    $offset = 0
    while ($offset -lt $raw.Length) {
      $read = $stream.Read($raw, $offset, $raw.Length - $offset)
      if ($read -le 0) { throw "certificate read incomplete" }
      $offset += $read
    }
    if ($stream.Length -ne $raw.Length) { throw "certificate identity changed" }
    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($raw)
    try {
      # Store enumeration exposes canonical DER RawData.  The certificate file may
      # be either PEM or DER, so lease/store identity must bind that same DER form.
      $identity = Get-BidKingProductCaIdentity ([byte[]]$certificate.RawData)
      if (-not $identity) { throw "certificate is not the exact product CA identity" }
      return $identity
    } finally { $certificate.Dispose() }
  } finally { $stream.Dispose() }
}

function Get-BidKingTemporaryTrustLeaseRoot {
  $programData = [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonApplicationData)
  if (-not $programData) { throw "program data unavailable" }
  return Join-Path $programData "BidKing\wss-trust"
}

function Write-BidKingPrivateLease {
  param(
    [string]$Path,
    [System.Collections.IDictionary]$Payload,
    $Adapter,
    [switch]$Apply,
    [switch]$DryRun
  )
  if ($DryRun -or -not $Apply) { throw "lease mutation authorization missing" }
  $root = Split-Path -Parent $Path
  & $Adapter.ProtectLeaseRoot $root
  $tmp = Join-Path $root (".lease-{0}.tmp" -f [guid]::NewGuid().ToString("N"))
  try {
    [IO.File]::WriteAllText($tmp, ($Payload | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
  } finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
  }
  return Get-BidKingSha256Hex ([IO.File]::ReadAllBytes($Path))
}

function Test-BidKingFlatJsonKeysUnique([string]$Json) {
  try {
    # The strict token parser decodes key tokens with ConvertFrom-Json, so \u
    # aliases, NFC-equivalent spelling and case aliases share one identity.
    Assert-BidKingInternetCafeJsonObject $Json
    return $true
  } catch { return $false }
}

function Read-BidKingPrivateLease([string]$Path) {
  try {
    $raw = [IO.File]::ReadAllBytes($Path)
    if ($raw.Length -le 0 -or $raw.Length -gt 65536) { return $null }
    $text = [Text.UTF8Encoding]::new($false, $true).GetString($raw)
    if (-not (Test-BidKingFlatJsonKeysUnique $text)) { return $null }
    $payload = $text | ConvertFrom-Json -ErrorAction Stop
    $required = @(
      "schema_version", "state", "current_sid", "interactive_sid", "thumbprint",
      "certificate_raw_b64", "certificate_raw_sha256", "package_generation_identity",
      "runtime_identity", "component_identity", "lease_epoch", "monitor_epoch",
      "monitor_process_id", "monitor_started_at_unix_ms", "inserted_by_this_run",
      "created_at_unix_ms"
    )
    if (@($payload.PSObject.Properties.Name).Count -ne $required.Count) { return $null }
    foreach ($field in $required) {
      if ($payload.PSObject.Properties.Name -cnotcontains $field) { return $null }
    }
    if (
      ($payload.schema_version -isnot [int] -and $payload.schema_version -isnot [long]) -or
      $payload.schema_version -ne 2 -or
      $payload.state -isnot [string] -or $payload.state -cnotin @("pending", "active") -or
      $payload.current_sid -isnot [string] -or $payload.current_sid -cnotmatch '^S-1-' -or
      $payload.interactive_sid -isnot [string] -or
      $payload.current_sid -cne $payload.interactive_sid -or
      $payload.thumbprint -isnot [string] -or
      $payload.thumbprint -cnotmatch '^[0-9A-F]{40,128}$' -or
      $payload.certificate_raw_b64 -isnot [string] -or
      $payload.certificate_raw_sha256 -isnot [string] -or
      -not (Test-BidKingSha256 $payload.certificate_raw_sha256) -or
      $payload.package_generation_identity -isnot [string] -or
      -not (Test-BidKingSha256 $payload.package_generation_identity) -or
      $payload.runtime_identity -isnot [string] -or
      -not (Test-BidKingSha256 $payload.runtime_identity) -or
      $payload.component_identity -isnot [string] -or
      -not (Test-BidKingSha256 $payload.component_identity) -or
      $payload.lease_epoch -isnot [string] -or
      -not (Test-BidKingGuidText $payload.lease_epoch) -or
      $payload.monitor_epoch -isnot [string] -or
      -not (Test-BidKingGuidText $payload.monitor_epoch) -or
      -not (Test-BidKingExactJsonInteger $payload.monitor_process_id 0 ([int]::MaxValue)) -or
      -not (Test-BidKingExactJsonInteger $payload.monitor_started_at_unix_ms 0 $script:BidKingMaximumUnixTimeMilliseconds) -or
      $payload.inserted_by_this_run -isnot [bool] -or
      -not (Test-BidKingExactJsonInteger $payload.created_at_unix_ms 1 $script:BidKingMaximumUnixTimeMilliseconds) -or
      (($payload.monitor_process_id -eq 0) -xor ($payload.monitor_started_at_unix_ms -eq 0))
    ) { return $null }
    $certRaw = [Convert]::FromBase64String($payload.certificate_raw_b64)
    $productCa = Get-BidKingProductCaIdentity $certRaw
    if (
      -not $productCa -or
      [string]$productCa.thumbprint -cne [string]$payload.thumbprint -or
      [Convert]::ToBase64String($certRaw) -cne $payload.certificate_raw_b64 -or
      (Get-BidKingSha256Hex $certRaw) -cne $payload.certificate_raw_sha256
    ) { return $null }
    return [pscustomobject]@{
      Payload = $payload
      RawBytes = $raw
      FileSha256 = Get-BidKingSha256Hex $raw
    }
  } catch { return $null }
}

function Enable-BidKingTemporaryCurrentUserTrust {
  param(
    [Parameter(Mandatory = $true)]$Adapter,
    [Parameter(Mandatory = $true)][string]$CertificatePath,
    [Parameter(Mandatory = $true)][string]$PackageGenerationIdentity,
    [Parameter(Mandatory = $true)][string]$RuntimeIdentity,
    [Parameter(Mandatory = $true)][string]$ComponentIdentity,
    [Parameter(Mandatory = $true)][string]$MonitorEpoch,
    [string]$LeaseRoot = "",
    [switch]$Apply,
    [switch]$DryRun
  )
  if ($DryRun -or -not $Apply) {
    return [pscustomobject]@{ Succeeded = $false; Classification = "dry_run"; Inserted = $false; LeasePath = "" }
  }
  if (
    -not (Test-BidKingSha256 $PackageGenerationIdentity) -or
    -not (Test-BidKingSha256 $RuntimeIdentity) -or
    -not (Test-BidKingSha256 $ComponentIdentity) -or
    -not (Test-BidKingGuidText $MonitorEpoch)
  ) { throw "temporary trust identity invalid" }
  $currentSid = [string](& $Adapter.GetCurrentSid)
  $interactiveSid = [string](& $Adapter.GetInteractiveSid)
  if ($currentSid -eq "unknown" -or $currentSid -cne $interactiveSid) {
    return [pscustomobject]@{ Succeeded = $false; Classification = "interactive_user_mismatch"; Inserted = $false; LeasePath = "" }
  }
  try { $snapshot = Get-BidKingCertificateSnapshot $CertificatePath }
  catch {
    return [pscustomobject]@{ Succeeded = $false; Classification = "foreign_cert_or_state"; Inserted = $false; LeasePath = "" }
  }
  try { $matches = @(& $Adapter.ListCertificates $snapshot.thumbprint) }
  catch [System.UnauthorizedAccessException] {
    return [pscustomobject]@{ Succeeded = $false; Classification = "cert_store_write_denied"; Inserted = $false; LeasePath = "" }
  } catch [System.Security.SecurityException] {
    return [pscustomobject]@{ Succeeded = $false; Classification = "policy_locked"; Inserted = $false; LeasePath = "" }
  } catch {
    return [pscustomobject]@{ Succeeded = $false; Classification = "unknown"; Inserted = $false; LeasePath = "" }
  }
  $exact = @($matches | Where-Object { [string]$_.raw_b64 -ceq $snapshot.raw_b64 })
  if ($matches.Count -gt 0 -and ($matches.Count -ne 1 -or $exact.Count -ne 1)) {
    return [pscustomobject]@{ Succeeded = $false; Classification = "foreign_cert_or_state"; Inserted = $false; LeasePath = "" }
  }
  if ($exact.Count -eq 1) {
    return [pscustomobject]@{ Succeeded = $true; Classification = "ready"; Inserted = $false; LeasePath = "" }
  }

  if (-not $LeaseRoot) { $LeaseRoot = Get-BidKingTemporaryTrustLeaseRoot }
  $leaseEpoch = [guid]::NewGuid().ToString("D")
  $leasePath = Join-Path $LeaseRoot ("lease-{0}.json" -f $leaseEpoch)
  $payload = [ordered]@{
    schema_version = 2
    state = "pending"
    current_sid = $currentSid
    interactive_sid = $interactiveSid
    thumbprint = $snapshot.thumbprint
    certificate_raw_b64 = $snapshot.raw_b64
    certificate_raw_sha256 = $snapshot.raw_sha256
    package_generation_identity = $PackageGenerationIdentity
    runtime_identity = $RuntimeIdentity
    component_identity = $ComponentIdentity
    lease_epoch = $leaseEpoch
    monitor_epoch = $MonitorEpoch
    monitor_process_id = 0
    monitor_started_at_unix_ms = 0
    inserted_by_this_run = $true
    created_at_unix_ms = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  }
  $null = Write-BidKingPrivateLease $leasePath $payload $Adapter -Apply
  try {
    & $Adapter.AddCertificate $snapshot.raw_b64
    $readback = @(& $Adapter.ListCertificates $snapshot.thumbprint)
    $exactReadback = @($readback | Where-Object { [string]$_.raw_b64 -ceq $snapshot.raw_b64 })
    if ($readback.Count -ne 1 -or $exactReadback.Count -ne 1) {
      throw "certificate readback mismatch"
    }
    $payload.state = "active"
    $null = Write-BidKingPrivateLease $leasePath $payload $Adapter -Apply
    return [pscustomobject]@{ Succeeded = $true; Classification = "ready"; Inserted = $true; LeasePath = $leasePath; LeaseEpoch = $leaseEpoch }
  } catch [System.UnauthorizedAccessException] {
    return [pscustomobject]@{ Succeeded = $false; Classification = "cert_store_write_denied"; Inserted = $true; LeasePath = $leasePath }
  } catch [System.Security.SecurityException] {
    return [pscustomobject]@{ Succeeded = $false; Classification = "policy_locked"; Inserted = $true; LeasePath = $leasePath }
  } catch {
    return [pscustomobject]@{ Succeeded = $false; Classification = "unknown"; Inserted = $true; LeasePath = $leasePath }
  }
}

function Bind-BidKingTemporaryTrustMonitor {
  param(
    [Parameter(Mandatory = $true)]$Adapter,
    [Parameter(Mandatory = $true)][string]$LeasePath,
    [Parameter(Mandatory = $true)][string]$PackageGenerationIdentity,
    [Parameter(Mandatory = $true)][string]$RuntimeIdentity,
    [Parameter(Mandatory = $true)][string]$ComponentIdentity,
    [Parameter(Mandatory = $true)][string]$MonitorEpoch,
    [Parameter(Mandatory = $true)][int]$MonitorProcessId,
    [Parameter(Mandatory = $true)][long]$MonitorStartedAtMs,
    [switch]$Apply,
    [switch]$DryRun
  )
  if ($DryRun -or -not $Apply) { return $false }
  $lease = Read-BidKingPrivateLease $LeasePath
  if (-not $lease -or $MonitorProcessId -le 0 -or $MonitorStartedAtMs -le 0) { return $false }
  $payload = $lease.Payload
  if (
    [string]$payload.state -cne "active" -or
    -not [bool]$payload.inserted_by_this_run -or
    [string]$payload.current_sid -cne [string](& $Adapter.GetCurrentSid) -or
    [string]$payload.interactive_sid -cne [string](& $Adapter.GetInteractiveSid) -or
    [string]$payload.package_generation_identity -cne $PackageGenerationIdentity -or
    [string]$payload.runtime_identity -cne $RuntimeIdentity -or
    [string]$payload.component_identity -cne $ComponentIdentity -or
    [string]$payload.monitor_epoch -cne $MonitorEpoch -or
    [string](& $Adapter.GetMonitorState $MonitorProcessId $MonitorStartedAtMs) -cne "alive"
  ) { return $false }
  $fresh = Read-BidKingPrivateLease $LeasePath
  if (-not $fresh -or $fresh.FileSha256 -cne $lease.FileSha256) { return $false }
  $bound = [ordered]@{}
  foreach ($property in $payload.PSObject.Properties) {
    $bound[$property.Name] = $property.Value
  }
  $bound.monitor_process_id = $MonitorProcessId
  $bound.monitor_started_at_unix_ms = $MonitorStartedAtMs
  $null = Write-BidKingPrivateLease $LeasePath $bound $Adapter -Apply
  $verified = Read-BidKingPrivateLease $LeasePath
  return (
    $verified -and
    $verified.Payload.monitor_process_id -eq $MonitorProcessId -and
    $verified.Payload.monitor_started_at_unix_ms -eq $MonitorStartedAtMs
  )
}

function Invoke-BidKingTemporaryTrustCleanup {
  param(
    [Parameter(Mandatory = $true)]$Adapter,
    [Parameter(Mandatory = $true)][string]$LeasePath,
    [string]$ExpectedPackageGenerationIdentity = "",
    [string]$ExpectedRuntimeIdentity = "",
    [string]$ExpectedComponentIdentity = "",
    [string]$ExpectedMonitorEpoch = "",
    [int]$MonitorProcessId = 0,
    [long]$MonitorStartedAtMs = 0,
    [string]$GameProcessName = "BidKing",
    [switch]$Apply,
    [switch]$DryRun
  )
  if ($DryRun -or -not $Apply) {
    return [pscustomobject]@{ State = "dry_run"; Removed = $false }
  }
  $lease = Read-BidKingPrivateLease $LeasePath
  if (-not $lease) { return [pscustomobject]@{ State = "foreign_or_unknown"; Removed = $false } }
  $payload = $lease.Payload
  $boundProcessId = $payload.monitor_process_id
  $boundStartedAtMs = $payload.monitor_started_at_unix_ms
  if ($MonitorProcessId -gt 0 -and (
      $MonitorProcessId -ne $boundProcessId -or
      $MonitorStartedAtMs -ne $boundStartedAtMs
  )) { return [pscustomobject]@{ State = "identity_changed"; Removed = $false } }
  if ($MonitorProcessId -le 0) {
    $MonitorProcessId = $boundProcessId
    $MonitorStartedAtMs = $boundStartedAtMs
  }
  $currentSid = [string](& $Adapter.GetCurrentSid)
  $interactiveSid = [string](& $Adapter.GetInteractiveSid)
  if (
    $currentSid -cne [string]$payload.current_sid -or
    $interactiveSid -cne [string]$payload.interactive_sid -or
    ($ExpectedPackageGenerationIdentity -and $ExpectedPackageGenerationIdentity -cne [string]$payload.package_generation_identity) -or
    ($ExpectedRuntimeIdentity -and $ExpectedRuntimeIdentity -cne [string]$payload.runtime_identity) -or
    ($ExpectedComponentIdentity -and $ExpectedComponentIdentity -cne [string]$payload.component_identity) -or
    ($ExpectedMonitorEpoch -and $ExpectedMonitorEpoch -cne [string]$payload.monitor_epoch)
  ) { return [pscustomobject]@{ State = "identity_changed"; Removed = $false } }
  $gameState = [string](& $Adapter.GetGameState $GameProcessName)
  if ($gameState -ne "absent") {
    return [pscustomobject]@{ State = $(if ($gameState -eq "alive") { "game_active" } else { "query_denied" }); Removed = $false }
  }
  $monitorState = [string](& $Adapter.GetMonitorState $MonitorProcessId $MonitorStartedAtMs)
  if ($monitorState -ne "absent") {
    return [pscustomobject]@{ State = $(if ($monitorState -eq "alive") { "monitor_active" } else { "identity_changed" }); Removed = $false }
  }
  try { $matches = @(& $Adapter.ListCertificates ([string]$payload.thumbprint)) }
  catch { return [pscustomobject]@{ State = "query_denied"; Removed = $false } }
  $exact = @($matches | Where-Object { [string]$_.raw_b64 -ceq [string]$payload.certificate_raw_b64 })
  if ($matches.Count -eq 0) {
    Remove-Item -LiteralPath $LeasePath -Force
    return [pscustomobject]@{ State = "already_absent"; Removed = $false }
  }
  if ($matches.Count -ne 1 -or $exact.Count -ne 1 -or -not [bool]$payload.inserted_by_this_run) {
    return [pscustomobject]@{ State = "foreign_or_unknown"; Removed = $false }
  }
  $freshLease = Read-BidKingPrivateLease $LeasePath
  if (-not $freshLease -or $freshLease.FileSha256 -cne $lease.FileSha256) {
    return [pscustomobject]@{ State = "identity_changed"; Removed = $false }
  }
  try {
    $freshMatches = @(& $Adapter.ListCertificates ([string]$payload.thumbprint))
    if (
      $freshMatches.Count -ne 1 -or
      [string]$freshMatches[0].raw_b64 -cne [string]$payload.certificate_raw_b64
    ) { return [pscustomobject]@{ State = "identity_changed"; Removed = $false } }
    & $Adapter.RemoveCertificate ([string]$payload.thumbprint) ([string]$payload.certificate_raw_b64)
    $post = @(& $Adapter.ListCertificates ([string]$payload.thumbprint))
    if ($post.Count -ne 0) { return [pscustomobject]@{ State = "cleanup_failed"; Removed = $false } }
    Remove-Item -LiteralPath $LeasePath -Force
    return [pscustomobject]@{ State = "removed"; Removed = $true }
  } catch { return [pscustomobject]@{ State = "query_denied"; Removed = $false } }
}

function Invoke-BidKingTemporaryTrustRecovery {
  param(
    [Parameter(Mandatory = $true)]$Adapter,
    [string]$LeaseRoot = "",
    [string]$ExpectedPackageGenerationIdentity = "",
    [string]$ExpectedRuntimeIdentity = "",
    [string]$ExpectedComponentIdentity = "",
    [string]$GameProcessName = "BidKing",
    [switch]$Apply,
    [switch]$DryRun
  )
  if ($DryRun -or -not $Apply) {
    return [pscustomobject]@{ State = "dry_run"; Removed = 0; Preserved = 0 }
  }
  if (-not $LeaseRoot) { $LeaseRoot = Get-BidKingTemporaryTrustLeaseRoot }
  try {
    if (-not (Test-Path -LiteralPath $LeaseRoot -PathType Container)) {
      return [pscustomobject]@{ State = "absent"; Removed = 0; Preserved = 0 }
    }
    $leaseFiles = @(
      Get-ChildItem -LiteralPath $LeaseRoot -Filter "lease-*.json" -File -Force -ErrorAction Stop
    )
  } catch {
    return [pscustomobject]@{ State = "preserved"; Removed = 0; Preserved = 1 }
  }
  $removed = 0
  $preserved = 0
  foreach ($file in $leaseFiles) {
    $result = Invoke-BidKingTemporaryTrustCleanup `
      -Adapter $Adapter -LeasePath $file.FullName `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
      -ExpectedRuntimeIdentity $ExpectedRuntimeIdentity `
      -ExpectedComponentIdentity $ExpectedComponentIdentity `
      -GameProcessName $GameProcessName `
      -Apply
    if ($result.State -in @("removed", "already_absent")) { $removed += 1 }
    else { $preserved += 1 }
  }
  $state = if ($preserved -gt 0) { "preserved" } elseif ($removed -gt 0) { "cleaned" } else { "absent" }
  return [pscustomobject]@{ State = $state; Removed = $removed; Preserved = $preserved }
}

function Start-BidKingTemporaryTrustCleanupWatcher {
  param(
    [Parameter(Mandatory = $true)][string]$HelperPath,
    [Parameter(Mandatory = $true)][string]$LeasePath,
    [Parameter(Mandatory = $true)][string]$PackageGenerationIdentity,
    [Parameter(Mandatory = $true)][string]$RuntimeIdentity,
    [Parameter(Mandatory = $true)][string]$ComponentIdentity,
    [Parameter(Mandatory = $true)][string]$MonitorEpoch,
    [Parameter(Mandatory = $true)][int]$MonitorProcessId,
    [Parameter(Mandatory = $true)][long]$MonitorStartedAtMs,
    [string]$GameProcessName = "BidKing",
    [switch]$Apply,
    [switch]$DryRun
  )
  if ($DryRun -or -not $Apply) { return $null }
  $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
  $arguments = @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", ('"' + $HelperPath + '"'), "-Mode", "watch",
    "-LeasePath", ('"' + $LeasePath + '"'),
    "-ExpectedPackageGenerationIdentity", $PackageGenerationIdentity,
    "-ExpectedRuntimeIdentity", $RuntimeIdentity,
    "-ExpectedComponentIdentity", $ComponentIdentity,
    "-ExpectedMonitorEpoch", $MonitorEpoch,
    "-MonitorProcessId", "$MonitorProcessId",
    "-MonitorStartedAtMs", "$MonitorStartedAtMs",
    "-Apply",
    "-GameProcessName", $GameProcessName
  )
  return Start-Process -FilePath $powershell -ArgumentList $arguments -WindowStyle Hidden -PassThru
}

function Get-BidKingWssHelperFailureClassification {
  param(
    [string]$StatusPath,
    [string]$ExpectedLaunchId,
    [string]$ExpectedPackageGenerationIdentity,
    [string]$ExpectedRuntimeIdentity,
    [string]$ExpectedComponentIdentity,
    [string]$ExpectedOwnerSid,
    [int]$ExpectedMonitorProcessId,
    [long]$ExpectedMonitorStartedAtMs
  )
  try {
    if (
      -not (Test-BidKingGuidText $ExpectedLaunchId) -or
      -not (Test-BidKingSha256 $ExpectedPackageGenerationIdentity) -or
      -not (Test-BidKingSha256 $ExpectedRuntimeIdentity) -or
      -not (Test-BidKingSha256 $ExpectedComponentIdentity) -or
      $ExpectedOwnerSid -cnotmatch '^S-1-(?:[0-9]+-){1,14}[0-9]+$' -or
      $ExpectedMonitorProcessId -le 0 -or
      $ExpectedMonitorStartedAtMs -le 0
    ) { return "unknown" }
    $raw = [IO.File]::ReadAllBytes($StatusPath)
    if ($raw.Length -le 0 -or $raw.Length -gt 16384) { return "unknown" }
    $text = [Text.UTF8Encoding]::new($false, $true).GetString($raw)
    if (-not (Test-BidKingFlatJsonKeysUnique $text)) { return "unknown" }
    $payload = $text | ConvertFrom-Json -ErrorAction Stop
    $required = @(
      "schema_version", "owner_kind", "owner_sid", "package_generation_identity",
      "runtime_identity", "component_identity", "monitor_epoch",
      "monitor_started_at_unix_ms", "ts_unix_ms", "phase", "monitor_pid",
      "helper_pid", "launch_id", "redirector_state", "windivert_state"
    )
    foreach ($field in $required) {
      if ($payload.PSObject.Properties.Name -cnotcontains $field) { return "unknown" }
    }
    if (
      -not (Test-BidKingExactJsonInteger $payload.schema_version 4 4) -or
      $payload.owner_kind -isnot [string] -or
      $payload.owner_kind -cne "bidking_wss_monitor" -or
      $payload.owner_sid -isnot [string] -or
      $payload.owner_sid -cne $ExpectedOwnerSid -or
      $payload.package_generation_identity -isnot [string] -or
      $payload.package_generation_identity -cne $ExpectedPackageGenerationIdentity -or
      $payload.runtime_identity -isnot [string] -or
      $payload.runtime_identity -cne $ExpectedRuntimeIdentity -or
      $payload.component_identity -isnot [string] -or
      $payload.component_identity -cne $ExpectedComponentIdentity -or
      $payload.monitor_epoch -isnot [string] -or
      $payload.monitor_epoch -cne $ExpectedLaunchId -or
      $payload.launch_id -isnot [string] -or
      $payload.launch_id -cne $ExpectedLaunchId -or
      -not (Test-BidKingExactJsonInteger $payload.monitor_pid 1 ([int]::MaxValue)) -or
      $payload.monitor_pid -ne $ExpectedMonitorProcessId -or
      -not (Test-BidKingExactJsonInteger $payload.helper_pid 0 ([int]::MaxValue)) -or
      -not (Test-BidKingExactJsonInteger $payload.monitor_started_at_unix_ms 1 $script:BidKingMaximumUnixTimeMilliseconds) -or
      $payload.monitor_started_at_unix_ms -ne $ExpectedMonitorStartedAtMs -or
      -not (Test-BidKingExactJsonInteger $payload.ts_unix_ms 1 $script:BidKingMaximumUnixTimeMilliseconds) -or
      $payload.phase -isnot [string] -or
      $payload.redirector_state -isnot [string] -or
      $payload.windivert_state -isnot [string]
    ) { return "unknown" }
    if ($payload.windivert_preload_state -isnot [string]) { return "unknown" }
    if ($payload.windivert_preload_state -ceq "failed_access_denied") {
      return "driver_load_denied"
    }
    if ($payload.windivert_preload_state -ceq "failed_untrusted_driver") {
      return "component_execution_blocked"
    }
    $preloadPhase = if (
      $payload.PSObject.Properties.Name -ccontains "windivert_preload_phase" -and
      $payload.windivert_preload_phase -is [string]
    ) { [string]$payload.windivert_preload_phase } else { "" }
    if (
      $payload.windivert_preload_state -ceq "failed_same_path_mixed_generation" -and
      $preloadPhase -ceq "verify_product"
    ) { return "wss_same_path_mixed_generation" }
    if (
      $payload.windivert_preload_state -ceq "failed_service_conflict" -and
      $preloadPhase -ceq "verify_product"
    ) { return "wss_stale_product_service_conflict" }
    if (
      $payload.windivert_preload_state -ceq "repaired_restart_required" -and
      $preloadPhase -ceq "complete"
    ) { return "wss_stale_product_service_repaired_restart_required" }
    return "unknown"
  } catch { return "unknown" }
}

function Invoke-BidKingInternetCafeWatchMode {
  if ($DryRun -or -not $Apply) { return 0 }
  $adapter = New-BidKingInternetCafeProductionAdapter
  $monitorState = [string](& $adapter.GetMonitorState $MonitorProcessId $MonitorStartedAtMs)
  if ($monitorState -eq "alive") {
    try {
      $process = Get-Process -Id $MonitorProcessId -ErrorAction Stop
      $process.WaitForExit()
    } catch { return 0 }
  } elseif ($monitorState -ne "absent") { return 0 }
  $null = Invoke-BidKingTemporaryTrustCleanup `
    -Adapter $adapter -LeasePath $LeasePath `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
    -ExpectedRuntimeIdentity $ExpectedRuntimeIdentity `
    -ExpectedComponentIdentity $ExpectedComponentIdentity `
    -ExpectedMonitorEpoch $ExpectedMonitorEpoch `
    -MonitorProcessId $MonitorProcessId -MonitorStartedAtMs $MonitorStartedAtMs `
    -GameProcessName $GameProcessName `
    -Apply
  return 0
}

if ($MyInvocation.InvocationName -ne '.') {
  if ($Mode -eq "watch") {
    exit (Invoke-BidKingInternetCafeWatchMode)
  }
  $adapter = New-BidKingInternetCafeProductionAdapter
  $result = Invoke-BidKingInternetCafePreflight `
    -Adapter $adapter -PackageRoot $PackageRoot -ComponentPath $ComponentPath
  [Console]::Out.WriteLine(($result | ConvertTo-Json -Compress))
  exit $(if ($result.classification -eq "ready") { 0 } else { 2 })
}
