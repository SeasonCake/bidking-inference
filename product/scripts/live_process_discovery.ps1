# WMI/CIM-aware process discovery for the public live-capture launcher.
#
# Normal path: keep command-line matching through Win32_Process.
# Degraded path: if CIM is unavailable or times out, trust only the launcher's
# own PID state files after verifying the PID still belongs to a live Python
# process.  This module never starts, enables, or repairs Winmgmt.

$script:BidKingCimProcessEnumerationAvailable = $true
$script:BidKingCimFallbackRecorded = $false
$script:BidKingOverlayAbsenceQualified = $false
$script:BidKingProcessInventory = $null
$script:BidKingProcessInventoryGeneration = 0
$script:BidKingProcessInventoryQueryCount = 0
$script:BidKingProcessInventoryCacheHitCount = 0

function Reset-BidKingProcessDiscoveryState {
  $script:BidKingCimProcessEnumerationAvailable = $true
  $script:BidKingCimFallbackRecorded = $false
  $script:BidKingOverlayAbsenceQualified = $false
  $script:BidKingProcessInventory = $null
  $script:BidKingProcessInventoryGeneration = 0
  $script:BidKingProcessInventoryQueryCount = 0
  $script:BidKingProcessInventoryCacheHitCount = 0
}

function Write-BidKingStartupStatus {
  param(
    [string]$StatusPath,
    [string]$Event
  )
  if (-not $StatusPath) { return }
  try {
    $parent = Split-Path -Parent $StatusPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $line = "{0}  {1}" -f (Get-Date).ToString("s"), $Event
    Add-Content -LiteralPath $StatusPath -Value $line -Encoding utf8
  } catch {
    # Startup logging is best-effort and must never become a new hard dependency.
  }
}

function Enable-BidKingPidFallback {
  param(
    [string]$StatusPath,
    [System.Management.Automation.ErrorRecord]$Failure
  )
  $script:BidKingCimProcessEnumerationAvailable = $false
  $script:BidKingOverlayAbsenceQualified = $false
  if ($script:BidKingProcessInventory) {
    $script:BidKingProcessInventory.Result = "query_failed"
    $script:BidKingProcessInventory.Rows = @()
  }
  if (-not $script:BidKingCimFallbackRecorded) {
    $kind = if ($Failure -and $Failure.Exception) {
      $Failure.Exception.GetType().Name
    } else {
      "unknown"
    }
    Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
      "cim_process_enumeration_unavailable_fallback reason_type={0}" -f $kind
    )
    $script:BidKingCimFallbackRecorded = $true
  }
}

function Invalidate-BidKingProcessInventory {
  param([string]$Reason = "unknown", [string]$StatusPath = "")
  $AllowedReasons = @(
    "refresh", "process_start", "process_stop", "restart", "poll", "dialog",
    "stale_cleanup", "unknown"
  )
  if ($AllowedReasons -cnotcontains $Reason) { $Reason = "unknown" }
  $script:BidKingProcessInventory = $null
  $script:BidKingProcessInventoryGeneration += 1
  $script:BidKingOverlayAbsenceQualified = $false
  Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
    "process_inventory invalidation_reason={0}" -f $Reason
  )
}

function Get-BidKingProcessInventory {
  param(
    [string]$PhaseToken = "",
    [string]$StatusPath = "",
    [switch]$Refresh
  )
  # Only explicit, short-lived launch phases share provider results. Legacy
  # callers keep their fresh-query/fallback behavior and never populate a cache.
  $UsePhase = $PhaseToken -cmatch '^[a-z_]{1,32}$'
  if (-not $UsePhase -and $script:BidKingProcessInventory) {
    # An uncached observer starts a separate read boundary. Do not let a later
    # phased observer reuse rows that predate this newer provider observation.
    Invalidate-BidKingProcessInventory -Reason "refresh"
  }
  if ($Refresh) {
    Invalidate-BidKingProcessInventory -Reason "refresh" -StatusPath $StatusPath
  }
  $Cached = $script:BidKingProcessInventory
  $Now = [Diagnostics.Stopwatch]::GetTimestamp()
  if (
    $UsePhase -and $Cached -and
    $Cached.PhaseToken -ceq $PhaseToken -and
    $Cached.Generation -eq $script:BidKingProcessInventoryGeneration
  ) {
    $AgeMs = 1000.0 * ($Now - $Cached.CapturedTick) / [Diagnostics.Stopwatch]::Frequency
    # A failed query is reused only inside its phase so three consumers cannot
    # each spend the three-second timeout. Explicit refresh can retry the host.
    if ($Cached.Result -ne "ok" -or ($AgeMs -ge 0 -and $AgeMs -le 500)) {
      $script:BidKingProcessInventoryCacheHitCount += 1
      Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
        "process_inventory phase={0} result={1} cache_hit=true query_count={2} cache_hit_count={3}" -f
          $PhaseToken, $Cached.Result, $script:BidKingProcessInventoryQueryCount,
          $script:BidKingProcessInventoryCacheHitCount
      )
      return $Cached
    }
  }
  if (-not $Refresh -and -not $script:BidKingCimProcessEnumerationAvailable) {
    return [pscustomobject]@{ Result = "unavailable"; Rows = @() }
  }
  $Timer = [Diagnostics.Stopwatch]::StartNew()
  $script:BidKingProcessInventoryQueryCount += 1
  $Rows = @()
  $Result = "ok"
  try {
    $Rows = @(Get-CimInstance -ClassName Win32_Process -OperationTimeoutSec 3 -ErrorAction Stop)
    $script:BidKingCimProcessEnumerationAvailable = $true
  } catch {
    Enable-BidKingPidFallback -StatusPath $StatusPath -Failure $_
    $Result = "query_failed"
  }
  $Timer.Stop()
  $Inventory = [pscustomobject]@{
    PhaseToken = $PhaseToken
    Generation = $script:BidKingProcessInventoryGeneration
    CapturedTick = [Diagnostics.Stopwatch]::GetTimestamp()
    Result = $Result
    Rows = $Rows
  }
  if ($UsePhase) {
    $script:BidKingProcessInventory = $Inventory
    Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
      "process_inventory phase={0} result={1} cache_hit=false query_count={2} elapsed_ms={3}" -f
        $PhaseToken, $Result, $script:BidKingProcessInventoryQueryCount, $Timer.ElapsedMilliseconds
    )
  }
  return $Inventory
}

function ConvertTo-BidKingCanonicalPath {
  param([string]$Path)
  if (-not $Path) { return $null }
  try {
    $candidate = $Path.Trim().Trim('"')
    if (-not $candidate) { return $null }
    return (
      [System.IO.Path]::GetFullPath($candidate).
        Replace("/", "\").
        TrimEnd([char[]]"\/")
    )
  } catch {
    return $null
  }
}

function Test-BidKingSamePath {
  param(
    [string]$Left,
    [string]$Right
  )
  $leftCanonical = ConvertTo-BidKingCanonicalPath $Left
  $rightCanonical = ConvertTo-BidKingCanonicalPath $Right
  return (
    $null -ne $leftCanonical -and
    $null -ne $rightCanonical -and
    [string]::Equals(
      $leftCanonical,
      $rightCanonical,
      [System.StringComparison]::OrdinalIgnoreCase
    )
  )
}

function Split-BidKingCommandLine {
  param([string]$CommandLine)
  if (-not $CommandLine) { return @() }

  $arguments = New-Object System.Collections.Generic.List[string]
  $current = New-Object System.Text.StringBuilder
  $insideQuotes = $false
  foreach ($character in $CommandLine.ToCharArray()) {
    if ($character -eq '"') {
      $insideQuotes = -not $insideQuotes
      continue
    }
    if ([char]::IsWhiteSpace($character) -and -not $insideQuotes) {
      if ($current.Length -gt 0) {
        $arguments.Add($current.ToString())
        $null = $current.Clear()
      }
      continue
    }
    $null = $current.Append($character)
  }
  if ($current.Length -gt 0) {
    $arguments.Add($current.ToString())
  }
  return $arguments.ToArray()
}

function Get-BidKingPythonEntrypointIndex {
  param([string[]]$Arguments)
  if (-not $Arguments -or $Arguments.Count -lt 2) { return -1 }

  try {
    $pythonName = [System.IO.Path]::GetFileName($Arguments[0])
  } catch {
    return -1
  }
  if ($pythonName -notmatch '^python(?:w)?(?:[0-9.]*)?\.exe$') {
    return -1
  }

  $flagOnly = @("-B", "-E", "-I", "-O", "-OO", "-s", "-S", "-u")
  $flagWithValue = @("-W", "-X")
  $index = 1
  while ($index -lt $Arguments.Count) {
    $argument = [string]$Arguments[$index]
    if ($flagOnly -contains $argument) {
      $index += 1
      continue
    }
    if ($flagWithValue -contains $argument) {
      if ($index + 1 -ge $Arguments.Count) { return -1 }
      $index += 2
      continue
    }
    if ($argument.StartsWith("-")) {
      # -c/-m and unknown interpreter switches do not have a script entrypoint
      # that this launcher can safely adopt.
      return -1
    }
    return $index
  }
  return -1
}

function Get-BidKingPythonEntrypointArgument {
  param([string[]]$Arguments)
  $index = Get-BidKingPythonEntrypointIndex -Arguments $Arguments
  if ($index -lt 0) { return $null }
  return $Arguments[$index]
}

function Get-BidKingMonitorCommandGenerationState {
  param(
    [string]$CommandLine,
    [string]$ExpectedMonitorPath,
    [string]$ExpectedLogDir,
    [string]$ExpectedPackageGenerationIdentity = ""
  )
  if (
    -not $ExpectedMonitorPath -or
    -not $ExpectedLogDir -or
    $ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$'
  ) { return "unknown" }

  try {
    $arguments = @(Split-BidKingCommandLine $CommandLine)
  } catch {
    return "unknown"
  }
  $entrypointIndex = Get-BidKingPythonEntrypointIndex -Arguments $arguments
  if ($entrypointIndex -lt 0) { return "foreign" }
  $entrypoint = $arguments[$entrypointIndex]
  $monitorMatches = Test-BidKingSamePath $entrypoint $ExpectedMonitorPath

  $logDirValues = New-Object System.Collections.Generic.List[string]
  $generationValues = New-Object System.Collections.Generic.List[string]
  for ($index = $entrypointIndex + 1; $index -lt $arguments.Count; $index += 1) {
    if ([string]::Equals(
      $arguments[$index],
      "--log-dir",
      [System.StringComparison]::Ordinal
    )) {
      if (
        $index + 1 -ge $arguments.Count -or
        ([string]$arguments[$index + 1]).StartsWith("-")
      ) { return "unknown" }
      $logDirValues.Add([string]$arguments[$index + 1])
      $index += 1
      continue
    }
    if ([string]::Equals(
      $arguments[$index],
      "--package-generation-identity",
      [System.StringComparison]::Ordinal
    )) {
      if (
        $index + 1 -ge $arguments.Count -or
        ([string]$arguments[$index + 1]).StartsWith("-")
      ) { return "unknown" }
      $generationValues.Add([string]$arguments[$index + 1])
      $index += 1
    }
  }
  if ($logDirValues.Count -ne 1) {
    if ($monitorMatches) { return "unknown" }
    return "foreign"
  }
  $logDirMatches = Test-BidKingSamePath $logDirValues[0] $ExpectedLogDir
  if (-not $monitorMatches -or -not $logDirMatches) {
    if ($monitorMatches -or $logDirMatches) { return "foreign_context" }
    return "foreign"
  }
  if ($generationValues.Count -eq 0) { return "stale_package" }
  if ($generationValues.Count -ne 1) { return "unknown" }
  if ($generationValues[0] -cnotmatch '^[0-9a-f]{64}$') { return "unknown" }
  if ([string]::Equals(
    $generationValues[0],
    $ExpectedPackageGenerationIdentity,
    [System.StringComparison]::Ordinal
  )) { return "current" }
  return "stale_package"
}

function Test-BidKingMonitorCommandIdentity {
  param(
    [string]$CommandLine,
    [string]$ExpectedMonitorPath,
    [string]$ExpectedLogDir,
    [string]$ExpectedPackageGenerationIdentity = ""
  )
  $state = Get-BidKingMonitorCommandGenerationState `
    -CommandLine $CommandLine `
    -ExpectedMonitorPath $ExpectedMonitorPath `
    -ExpectedLogDir $ExpectedLogDir `
    -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
  return $state -eq "current"
}

function Move-BidKingJsonWhitespace {
  param(
    [string]$Text,
    [ref]$Offset
  )
  while (
    $Offset.Value -lt $Text.Length -and
    [char]::IsWhiteSpace($Text[$Offset.Value])
  ) {
    $Offset.Value += 1
  }
}

function Read-BidKingJsonStringToken {
  param(
    [string]$Text,
    [ref]$Offset
  )
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
    if ([int]$character -lt 0x20) {
      throw "json string contains a control character"
    }
    if ([int]$character -eq 0x5c) {
      $Offset.Value += 1
      if ($Offset.Value -ge $Text.Length) {
        throw "json string escape is incomplete"
      }
      $escape = $Text[$Offset.Value]
      if ($escape -eq 'u') {
        if ($Offset.Value + 4 -ge $Text.Length) {
          throw "json unicode escape is incomplete"
        }
        $digits = $Text.Substring($Offset.Value + 1, 4)
        if ($digits -cnotmatch '^[0-9a-fA-F]{4}$') {
          throw "json unicode escape is invalid"
        }
        $Offset.Value += 5
        continue
      }
      if ('"\/bfnrt'.IndexOf($escape) -lt 0) {
        throw "json string escape is invalid"
      }
    }
    $Offset.Value += 1
  }
  throw "json string is unterminated"
}

function Read-BidKingJsonValueWithUniqueKeys {
  param(
    [string]$Text,
    [ref]$Offset
  )
  Move-BidKingJsonWhitespace -Text $Text -Offset $Offset
  if ($Offset.Value -ge $Text.Length) { throw "json value expected" }
  $character = $Text[$Offset.Value]
  if ($character -eq '{') {
    Read-BidKingJsonObjectWithUniqueKeys -Text $Text -Offset $Offset
    return
  }
  if ($character -eq '[') {
    Read-BidKingJsonArrayWithUniqueKeys -Text $Text -Offset $Offset
    return
  }
  if ($character -eq '"') {
    $null = Read-BidKingJsonStringToken -Text $Text -Offset $Offset
    return
  }
  $start = $Offset.Value
  while ($Offset.Value -lt $Text.Length) {
    $character = $Text[$Offset.Value]
    if (
      [char]::IsWhiteSpace($character) -or
      $character -in @(',', ']', '}')
    ) { break }
    $Offset.Value += 1
  }
  if ($Offset.Value -eq $start) { throw "json primitive expected" }
  $primitive = $Text.Substring($start, $Offset.Value - $start)
  if (-not [System.Text.RegularExpressions.Regex]::IsMatch(
    $primitive,
    '^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)$',
    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
  )) {
    throw "json primitive is not RFC 8259 compliant"
  }
  $null = $primitive | ConvertFrom-Json -ErrorAction Stop
}

function Read-BidKingJsonArrayWithUniqueKeys {
  param(
    [string]$Text,
    [ref]$Offset
  )
  if ($Text[$Offset.Value] -ne '[') { throw "json array expected" }
  $Offset.Value += 1
  Move-BidKingJsonWhitespace -Text $Text -Offset $Offset
  if ($Offset.Value -lt $Text.Length -and $Text[$Offset.Value] -eq ']') {
    $Offset.Value += 1
    return
  }
  while ($true) {
    Read-BidKingJsonValueWithUniqueKeys -Text $Text -Offset $Offset
    Move-BidKingJsonWhitespace -Text $Text -Offset $Offset
    if ($Offset.Value -ge $Text.Length) { throw "json array is unterminated" }
    if ($Text[$Offset.Value] -eq ']') {
      $Offset.Value += 1
      return
    }
    if ($Text[$Offset.Value] -ne ',') { throw "json array delimiter expected" }
    $Offset.Value += 1
  }
}

function Read-BidKingJsonObjectWithUniqueKeys {
  param(
    [string]$Text,
    [ref]$Offset
  )
  if ($Text[$Offset.Value] -ne '{') { throw "json object expected" }
  $Offset.Value += 1
  $seen = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::Ordinal
  )
  Move-BidKingJsonWhitespace -Text $Text -Offset $Offset
  if ($Offset.Value -lt $Text.Length -and $Text[$Offset.Value] -eq '}') {
    $Offset.Value += 1
    return
  }
  while ($true) {
    $token = Read-BidKingJsonStringToken -Text $Text -Offset $Offset
    $key = $token | ConvertFrom-Json -ErrorAction Stop
    if ($key -isnot [string] -or -not $seen.Add([string]$key)) {
      throw "json object key is duplicated"
    }
    Move-BidKingJsonWhitespace -Text $Text -Offset $Offset
    if ($Offset.Value -ge $Text.Length -or $Text[$Offset.Value] -ne ':') {
      throw "json object colon expected"
    }
    $Offset.Value += 1
    Read-BidKingJsonValueWithUniqueKeys -Text $Text -Offset $Offset
    Move-BidKingJsonWhitespace -Text $Text -Offset $Offset
    if ($Offset.Value -ge $Text.Length) { throw "json object is unterminated" }
    if ($Text[$Offset.Value] -eq '}') {
      $Offset.Value += 1
      return
    }
    if ($Text[$Offset.Value] -ne ',') { throw "json object delimiter expected" }
    $Offset.Value += 1
    Move-BidKingJsonWhitespace -Text $Text -Offset $Offset
  }
}

function Assert-BidKingJsonObjectKeysUnique {
  param([string]$Json)
  $offset = 0
  Move-BidKingJsonWhitespace -Text $Json -Offset ([ref]$offset)
  if ($offset -ge $Json.Length -or $Json[$offset] -ne '{') {
    throw "json root object expected"
  }
  Read-BidKingJsonObjectWithUniqueKeys -Text $Json -Offset ([ref]$offset)
  Move-BidKingJsonWhitespace -Text $Json -Offset ([ref]$offset)
  if ($offset -ne $Json.Length) { throw "json trailing data is invalid" }
}

function Get-BidKingValidatedEpoch {
  param([object]$Value)
  $invalid = [pscustomobject]@{
    Valid = $false
    Seconds = $null
    UnixMilliseconds = $null
    UtcDateTime = $null
  }
  if ($null -eq $Value) { return $invalid }
  $valueType = $Value.GetType()
  if ($valueType -ne [double] -and $valueType -ne [decimal]) { return $invalid }
  try {
    $seconds = [double]$Value
    if (
      [double]::IsNaN($seconds) -or
      [double]::IsInfinity($seconds) -or
      $seconds -le 0
    ) { return $invalid }
    $millisecondsValue = $seconds * 1000.0
    if (
      [double]::IsNaN($millisecondsValue) -or
      [double]::IsInfinity($millisecondsValue)
    ) { return $invalid }
    $roundedMilliseconds = [Math]::Round($millisecondsValue)
    $minimumMilliseconds = [DateTimeOffset]::MinValue.ToUnixTimeMilliseconds()
    $maximumMilliseconds = [DateTimeOffset]::MaxValue.ToUnixTimeMilliseconds()
    if (
      $roundedMilliseconds -lt $minimumMilliseconds -or
      $roundedMilliseconds -gt $maximumMilliseconds
    ) { return $invalid }
    $unixMilliseconds = [long]$roundedMilliseconds
    $utcDateTime = [DateTimeOffset]::FromUnixTimeMilliseconds(
      $unixMilliseconds
    ).UtcDateTime
    return [pscustomobject]@{
      Valid = $true
      Seconds = $seconds
      UnixMilliseconds = $unixMilliseconds
      UtcDateTime = $utcDateTime
    }
  } catch {
    return $invalid
  }
}

function Get-BidKingMonitorLockPathState {
  param([string]$StatePath)
  try {
    # Test-Path/Exists false also covers non-leaves and query failures.  Only
    # the filesystem's explicit not-found result proves absence.
    $attributes = [System.IO.File]::GetAttributes($StatePath)
    if (($attributes -band (
      [System.IO.FileAttributes]::Directory -bor [System.IO.FileAttributes]::ReparsePoint
    )) -ne 0) { return "nonregular" }
    return "file"
  } catch {
    $failure = $_.Exception.GetBaseException()
    if (
      $failure -is [System.IO.FileNotFoundException] -or
      $failure -is [System.IO.DirectoryNotFoundException]
    ) { return "absent" }
    return "unknown"
  }
}

function Get-BidKingMonitorLockIdentity {
  param(
    [string]$StatePath,
    [string]$ExpectedMonitorPath,
    [string]$ExpectedLogDir,
    [string]$ExpectedPackageGenerationIdentity = ""
  )

  $result = [ordered]@{
    State = "absent"
    Reason = "lock_absent"
    ProcessId = $null
    ProcessStartedAt = $null
    ActualProcessStartedAt = $null
    MonitorPath = $null
    LogDir = $null
    ProcessName = $null
    RawFingerprint = $null
    PackageGenerationState = "unknown"
    PackageGenerationIdentity = $null
  }
  $pathState = Get-BidKingMonitorLockPathState -StatePath $StatePath
  if ($pathState -eq "absent") {
    return [pscustomobject]$result
  }
  $result.State = "unknown"
  $result.Reason = "lock_invalid"
  if ($pathState -ne "file") {
    $result.Reason = if ($pathState -eq "nonregular") { "lock_nonregular" } else { "lock_query_unknown" }
    return [pscustomobject]$result
  }

  try {
    $rawBytes = [System.IO.File]::ReadAllBytes($StatePath)
    if ($rawBytes.Length -le 0 -or $rawBytes.Length -gt 65536) {
      return [pscustomobject]$result
    }
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
      $hashBytes = $sha256.ComputeHash($rawBytes)
      $result.RawFingerprint = (
        [System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()
      )
    } finally {
      $sha256.Dispose()
    }
    $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $raw = ($strictUtf8.GetString($rawBytes)).Trim()
    if (-not $raw -or -not $raw.StartsWith("{")) {
      return [pscustomobject]$result
    }
    Assert-BidKingJsonObjectKeysUnique -Json $raw
    $payload = $raw | ConvertFrom-Json -ErrorAction Stop
    $required = @("pid", "process_started_at", "monitor_path", "log_dir")
    foreach ($field in $required) {
      if ($payload.PSObject.Properties.Name -notcontains $field) {
        return [pscustomobject]$result
      }
    }

    if (
      ($payload.pid -isnot [int] -and $payload.pid -isnot [long]) -or
      $payload.monitor_path -isnot [string] -or
      $payload.log_dir -isnot [string]
    ) {
      return [pscustomobject]$result
    }
    $processIdLong = [long]$payload.pid
    if ($processIdLong -le 0 -or $processIdLong -gt [int]::MaxValue) {
      return [pscustomobject]$result
    }
    $epoch = Get-BidKingValidatedEpoch -Value $payload.process_started_at
    if (-not $epoch.Valid) { return [pscustomobject]$result }
    $processId = [int]$processIdLong
    $processStartedAt = [double]$epoch.Seconds
    $monitorPath = [string]$payload.monitor_path
    $logDir = [string]$payload.log_dir
    $result.ProcessId = $processId
    $result.ProcessStartedAt = $processStartedAt
    $result.MonitorPath = $monitorPath
    $result.LogDir = $logDir
    # Liveness and the creation epoch are identity prerequisites.  They must be
    # resolved before package-relative paths: a dead lock from a different old
    # extraction is stale state, not a live foreign process.
    try {
      $process = Get-Process -Id $processId -ErrorAction Stop
    } catch {
      $fullyQualifiedErrorId = [string]$_.FullyQualifiedErrorId
      if ($fullyQualifiedErrorId.StartsWith(
        "NoProcessFoundForGivenId,",
        [System.StringComparison]::Ordinal
      )) {
        $result.State = "stale"
        $result.Reason = "pid_absent"
      } else {
        $result.Reason = "process_query_unknown"
      }
      return [pscustomobject]$result
    }
    $result.ProcessName = [string]$process.ProcessName
    try {
      $expected = $epoch.UtcDateTime
      $actual = $process.StartTime.ToUniversalTime()
      $result.ActualProcessStartedAt = ([DateTimeOffset]$actual).ToUnixTimeMilliseconds() / 1000.0
    } catch {
      $result.Reason = "pid_epoch_unknown"
      return [pscustomobject]$result
    }
    $epochDelta = ($actual - $expected).TotalSeconds
    if ($epochDelta -lt -2.0) {
      # Earlier creation is inconsistent evidence, not proof of PID reuse.
      $result.Reason = "pid_epoch_mismatch"
      return [pscustomobject]$result
    }
    if ($epochDelta -gt 2.0) {
      # Only a demonstrably later lifetime proves the recorded owner is gone.
      # Do not adopt/stop the new process, whatever its executable name/context.
      if ($payload.PSObject.Properties.Name -contains "package_generation_identity") {
        if (
          $payload.package_generation_identity -isnot [string] -or
          $payload.package_generation_identity -cnotmatch '^[0-9a-f]{64}$'
        ) { return [pscustomobject]$result }
      }
      $result.State = "stale"
      $result.Reason = "pid_reused"
      return [pscustomobject]$result
    }
    if ($process.ProcessName -notlike "python*") {
      $result.State = "foreign"
      $result.Reason = "process_name_mismatch"
      return [pscustomobject]$result
    }

    # Parse generation before context comparison so a cross-directory repair
    # owner can later prove an old package from the already validated lock.
    # Missing generation remains non-actionable; malformed generation is unknown.
    if ($ExpectedPackageGenerationIdentity) {
      if ($ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$') {
        return [pscustomobject]$result
      }
      if ($payload.PSObject.Properties.Name -contains "package_generation_identity") {
        if ($payload.package_generation_identity -isnot [string]) {
          return [pscustomobject]$result
        }
        $parsedGeneration = [string]$payload.package_generation_identity
        if ($parsedGeneration -cnotmatch '^[0-9a-f]{64}$') {
          return [pscustomobject]$result
        }
        $result.PackageGenerationIdentity = $parsedGeneration
      }
    }

    if (
      -not (Test-BidKingSamePath $monitorPath $ExpectedMonitorPath) -or
      -not (Test-BidKingSamePath $logDir $ExpectedLogDir)
    ) {
      $result.State = "foreign"
      $result.Reason = "context_mismatch"
      return [pscustomobject]$result
    }

    if ($ExpectedPackageGenerationIdentity) {
      if ($ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$') {
        return [pscustomobject]$result
      }
      if (
        $payload.PSObject.Properties.Name -notcontains "package_generation_identity"
      ) {
        $result.State = "stale_package"
        $result.Reason = "generation_missing"
        $result.PackageGenerationState = "missing"
        return [pscustomobject]$result
      }
      $actualGeneration = [string]$result.PackageGenerationIdentity
      if (-not [string]::Equals(
        $actualGeneration,
        $ExpectedPackageGenerationIdentity,
        [System.StringComparison]::Ordinal
      )) {
        $result.State = "stale_package"
        $result.Reason = "generation_mismatch"
        $result.PackageGenerationState = "mismatch"
        return [pscustomobject]$result
      }
      $result.PackageGenerationState = "current"
    }

    $result.State = "current"
    $result.Reason = "current"
    return [pscustomobject]$result
  } catch {
    return [pscustomobject]$result
  }
}

function Initialize-BidKingMonitorLockAtomicDelete {
  if ("BidKingMonitorLockAtomicDelete" -as [type]) { return }
  Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class BidKingMonitorLockAtomicDelete
{
    private const uint DeleteAccess = 0x00010000;
    private const uint GenericRead = 0x80000000;
    private const uint FileShareRead = 0x00000001;
    private const uint OpenExisting = 3;
    private const uint FileAttributeNormal = 0x00000080;
    private const int FileDispositionInfo = 4;

    [StructLayout(LayoutKind.Sequential)]
    private struct FileDisposition
    {
        [MarshalAs(UnmanagedType.U1)]
        public bool DeleteFile;
    }

    [DllImport(
        "kernel32.dll",
        CharSet = CharSet.Unicode,
        SetLastError = true
    )]
    private static extern SafeFileHandle CreateFileW(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetFileInformationByHandle(
        SafeFileHandle fileHandle,
        int fileInformationClass,
        ref FileDisposition fileInformation,
        uint bufferSize
    );

    public static SafeFileHandle OpenReadDelete(string path)
    {
        SafeFileHandle handle = CreateFileW(
            path,
            GenericRead | DeleteAccess,
            FileShareRead,
            IntPtr.Zero,
            OpenExisting,
            FileAttributeNormal,
            IntPtr.Zero
        );
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error);
        }
        return handle;
    }

    public static void MarkDeleteOnClose(SafeFileHandle handle)
    {
        FileDisposition disposition = new FileDisposition { DeleteFile = true };
        if (!SetFileInformationByHandle(
            handle,
            FileDispositionInfo,
            ref disposition,
            (uint)Marshal.SizeOf(typeof(FileDisposition))
        ))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }
}
'@ -ErrorAction Stop
}

function Set-BidKingMonitorLockDeleteOnClose {
  param([Microsoft.Win32.SafeHandles.SafeFileHandle]$SafeHandle)
  [BidKingMonitorLockAtomicDelete]::MarkDeleteOnClose($SafeHandle)
}

function Test-BidKingRecycledMonitorProcessUnchanged {
  param([object]$Identity)
  if ($Identity.Reason -ne "pid_reused") { return $true }
  try {
    $process = Get-Process -Id ([int]$Identity.ProcessId) -ErrorAction Stop
    $actual = ([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds() / 1000.0
    return (
      $actual -eq [double]$Identity.ActualProcessStartedAt -and
      [string]$process.ProcessName -ceq [string]$Identity.ProcessName
    )
  } catch { return $false }
}

function Remove-BidKingMonitorLockIfFingerprint {
  param(
    [string]$StatePath,
    [object]$Identity
  )
  if (
    -not $Identity -or
    $Identity.State -notin @("current", "stale", "stale_package") -or
    -not $Identity.RawFingerprint
  ) {
    return "unknown"
  }
  $pathState = Get-BidKingMonitorLockPathState -StatePath $StatePath
  if ($pathState -eq "absent") { return "absent" }
  if ($pathState -ne "file") { return "unknown" }

  $stream = $null
  $safeHandle = $null
  $deletePending = $false
  try {
    Initialize-BidKingMonitorLockAtomicDelete
    if ($Identity.Reason -eq "pid_reused") {
      Initialize-BidKingDeadMonitorLockParentLease
      $safeHandle = [BidKingDeadLockParentDirectoryLease]::OpenFile($StatePath)
      $fileIdentity = [BidKingDeadLockParentDirectoryLease]::Read($safeHandle)
      if (($fileIdentity.FileAttributes -band (
        [System.IO.FileAttributes]::ReparsePoint -bor [System.IO.FileAttributes]::Directory
      )) -ne 0) {
        $safeHandle.Dispose()
        return "changed"
      }
    } else {
      $safeHandle = [BidKingMonitorLockAtomicDelete]::OpenReadDelete($StatePath)
    }
    $stream = New-Object System.IO.FileStream(
      $safeHandle,
      [System.IO.FileAccess]::Read
    )
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
      $hashBytes = $sha256.ComputeHash($stream)
      $fingerprint = (
        [System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()
      )
    } finally {
      $sha256.Dispose()
    }
    if (-not [string]::Equals(
      $fingerprint,
      [string]$Identity.RawFingerprint,
      [System.StringComparison]::Ordinal
    )) {
      return "changed"
    }
    # The fingerprint-bound leaf is now held against writes/replacement.  A
    # process lifetime change since classification must abort before mutation.
    if (-not (Test-BidKingRecycledMonitorProcessUnchanged -Identity $Identity)) {
      return "changed"
    }
    Set-BidKingMonitorLockDeleteOnClose -SafeHandle $stream.SafeFileHandle
    $deletePending = $true
  } catch {
    return "unknown"
  } finally {
    if ($stream) { $stream.Dispose() }
    elseif ($safeHandle) { $safeHandle.Dispose() }
  }

  if ($deletePending -and (Get-BidKingMonitorLockPathState -StatePath $StatePath) -eq "absent") {
    return "deleted"
  }
  return "unknown"
}

function Initialize-BidKingDeadMonitorLockParentLease {
  if ("BidKingDeadLockParentDirectoryLease" -as [type]) { return }
  Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public sealed class BidKingFileObjectIdentity
{
    public uint FileAttributes { get; set; }
    public uint VolumeSerialNumber { get; set; }
    public ulong FileIndex { get; set; }
}

public static class BidKingDeadLockParentDirectoryLease
{
    private const uint FileListDirectory = 0x00000001;
    private const uint FileReadAttributes = 0x00000080;
    private const uint DeleteAccess = 0x00010000;
    private const uint GenericRead = 0x80000000;
    private const uint FileShareRead = 0x00000001;
    private const uint FileShareWrite = 0x00000002;
    private const uint FileShareDelete = 0x00000004;
    private const uint OpenExisting = 3;
    private const uint FileFlagBackupSemantics = 0x02000000;
    private const uint FileFlagOpenReparsePoint = 0x00200000;
    private const int FileRenameInfo = 3;

    [StructLayout(LayoutKind.Sequential)]
    private struct FileTime
    {
        public uint LowDateTime;
        public uint HighDateTime;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ByHandleFileInformation
    {
        public uint FileAttributes;
        public FileTime CreationTime;
        public FileTime LastAccessTime;
        public FileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport(
        "kernel32.dll",
        CharSet = CharSet.Unicode,
        SetLastError = true
    )]
    private static extern SafeFileHandle CreateFileW(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle fileHandle,
        out ByHandleFileInformation fileInformation
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetFileInformationByHandle(
        SafeFileHandle fileHandle,
        int fileInformationClass,
        IntPtr fileInformation,
        uint bufferSize
    );

    public static SafeFileHandle Open(string path)
    {
        // Deliberately omit FILE_SHARE_DELETE.  While this lease is open,
        // Windows cannot rename/delete and replace this directory object.
        SafeFileHandle handle = CreateFileW(
            path,
            FileListDirectory | FileReadAttributes,
            FileShareRead | FileShareWrite,
            IntPtr.Zero,
            OpenExisting,
            FileFlagBackupSemantics | FileFlagOpenReparsePoint,
            IntPtr.Zero
        );
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error);
        }
        return handle;
    }

    public static SafeFileHandle OpenFile(string path)
    {
        // Freeze the source leaf at the rename linearization point.  A second
        // process cannot write, rename, delete, or replace it while this
        // handle remains open; readers are still allowed.
        SafeFileHandle handle = CreateFileW(
            path,
            GenericRead | FileReadAttributes | DeleteAccess,
            FileShareRead,
            IntPtr.Zero,
            OpenExisting,
            FileFlagOpenReparsePoint,
            IntPtr.Zero
        );
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error);
        }
        return handle;
    }

    public static SafeFileHandle OpenFileReadEvidence(string path)
    {
        // This readback is compatible with the still-open source rename
        // handle: it requests only read access and shares the source handle's
        // DELETE access, without granting a writer or another renamer entry.
        SafeFileHandle handle = CreateFileW(
            path,
            GenericRead | FileReadAttributes,
            FileShareRead | FileShareDelete,
            IntPtr.Zero,
            OpenExisting,
            FileFlagOpenReparsePoint,
            IntPtr.Zero
        );
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            throw new Win32Exception(error);
        }
        return handle;
    }

    public static BidKingFileObjectIdentity Read(SafeFileHandle handle)
    {
        ByHandleFileInformation information;
        if (!GetFileInformationByHandle(handle, out information))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        return new BidKingFileObjectIdentity
        {
            FileAttributes = information.FileAttributes,
            VolumeSerialNumber = information.VolumeSerialNumber,
            FileIndex = ((ulong)information.FileIndexHigh << 32) |
                        information.FileIndexLow
        };
    }

    public static void RenameAbsoluteNoReplace(
        SafeFileHandle source,
        string destinationPath
    )
    {
        if (String.IsNullOrWhiteSpace(destinationPath))
        {
            throw new ArgumentException("destination path is required");
        }
        byte[] nameBytes = System.Text.Encoding.Unicode.GetBytes(destinationPath);
        int rootOffset = IntPtr.Size;
        int lengthOffset = IntPtr.Size * 2;
        int nameOffset = lengthOffset + sizeof(uint);
        int bufferSize = checked(nameOffset + nameBytes.Length + sizeof(char));
        IntPtr buffer = Marshal.AllocHGlobal(bufferSize);
        try
        {
            for (int index = 0; index < bufferSize; index++)
            {
                Marshal.WriteByte(buffer, index, 0);
            }
            // ReplaceIfExists = FALSE.  The caller holds and rechecks the exact
            // destination parent object across this handle-bound rename.
            Marshal.WriteByte(buffer, 0, 0);
            Marshal.WriteIntPtr(buffer, rootOffset, IntPtr.Zero);
            Marshal.WriteInt32(buffer, lengthOffset, nameBytes.Length);
            Marshal.Copy(nameBytes, 0, IntPtr.Add(buffer, nameOffset), nameBytes.Length);
            if (!SetFileInformationByHandle(
                source,
                FileRenameInfo,
                buffer,
                (uint)bufferSize
            ))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }
}
'@ -ErrorAction Stop
}

function Open-BidKingDeadMonitorLockParentLease {
  param(
    [string]$StatePath,
    [string]$ExpectedLogDir
  )
  $stateCanonical = ConvertTo-BidKingCanonicalPath $StatePath
  $expectedParent = ConvertTo-BidKingCanonicalPath $ExpectedLogDir
  if (-not $stateCanonical -or -not $expectedParent) {
    throw "dead lock parent path invalid"
  }
  $parent = ConvertTo-BidKingCanonicalPath (
    [System.IO.Path]::GetDirectoryName($stateCanonical)
  )
  if (-not (Test-BidKingSamePath $parent $expectedParent)) {
    throw "dead lock parent path mismatch"
  }

  Initialize-BidKingDeadMonitorLockParentLease
  $handle = [BidKingDeadLockParentDirectoryLease]::Open($parent)
  try {
    $identity = [BidKingDeadLockParentDirectoryLease]::Read($handle)
    if (
      (($identity.FileAttributes -band [System.IO.FileAttributes]::Directory) -eq 0) -or
      (($identity.FileAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    ) {
      throw "dead lock parent is not a regular directory"
    }
    return [pscustomobject]@{
      ParentPath = $parent
      SafeHandle = $handle
      FileAttributes = [uint32]$identity.FileAttributes
      VolumeSerialNumber = [uint32]$identity.VolumeSerialNumber
      FileIndex = [uint64]$identity.FileIndex
    }
  } catch {
    $handle.Dispose()
    throw
  }
}

function Test-BidKingDeadMonitorLockParentLease {
  param(
    [object]$Lease,
    [string]$StatePath,
    [string]$ExpectedLogDir
  )
  if (-not $Lease -or -not $Lease.SafeHandle) { return $false }
  try {
    if ($Lease.SafeHandle.IsInvalid -or $Lease.SafeHandle.IsClosed) {
      return $false
    }
    $stateCanonical = ConvertTo-BidKingCanonicalPath $StatePath
    $expectedParent = ConvertTo-BidKingCanonicalPath $ExpectedLogDir
    if (-not $stateCanonical -or -not $expectedParent) { return $false }
    $parent = ConvertTo-BidKingCanonicalPath (
      [System.IO.Path]::GetDirectoryName($stateCanonical)
    )
    if (
      -not (Test-BidKingSamePath $parent $expectedParent) -or
      -not (Test-BidKingSamePath $parent ([string]$Lease.ParentPath))
    ) {
      return $false
    }

    $heldIdentity = [BidKingDeadLockParentDirectoryLease]::Read(
      $Lease.SafeHandle
    )
    if (
      (($heldIdentity.FileAttributes -band [System.IO.FileAttributes]::Directory) -eq 0) -or
      (($heldIdentity.FileAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -or
      [uint32]$heldIdentity.VolumeSerialNumber -ne [uint32]$Lease.VolumeSerialNumber -or
      [uint64]$heldIdentity.FileIndex -ne [uint64]$Lease.FileIndex
    ) {
      return $false
    }

    $probe = [BidKingDeadLockParentDirectoryLease]::Open($parent)
    try {
      $pathIdentity = [BidKingDeadLockParentDirectoryLease]::Read($probe)
      return (
        (($pathIdentity.FileAttributes -band [System.IO.FileAttributes]::Directory) -ne 0) -and
        (($pathIdentity.FileAttributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) -and
        [uint32]$pathIdentity.VolumeSerialNumber -eq [uint32]$Lease.VolumeSerialNumber -and
        [uint64]$pathIdentity.FileIndex -eq [uint64]$Lease.FileIndex
      )
    } finally {
      $probe.Dispose()
    }
  } catch {
    return $false
  }
}

function Close-BidKingDeadMonitorLockParentLease {
  param([object]$Lease)
  if ($Lease -and $Lease.SafeHandle) {
    $Lease.SafeHandle.Dispose()
  }
}

function Open-BidKingDeadMonitorLockSourceLease {
  param(
    [string]$StatePath,
    [object]$ParentLease,
    [string]$ExpectedFingerprint,
    [switch]$ReadbackOnly
  )
  if (-not (Test-BidKingDeadMonitorLockParentLease `
    -Lease $ParentLease `
    -StatePath $StatePath `
    -ExpectedLogDir ([string]$ParentLease.ParentPath))) {
    throw "dead lock parent lease changed"
  }
  $stateCanonical = ConvertTo-BidKingCanonicalPath $StatePath
  if (-not $stateCanonical) { throw "dead lock source path invalid" }
  $handle = if ($ReadbackOnly) {
    [BidKingDeadLockParentDirectoryLease]::OpenFileReadEvidence($stateCanonical)
  } else {
    [BidKingDeadLockParentDirectoryLease]::OpenFile($stateCanonical)
  }
  $stream = $null
  try {
    $identity = [BidKingDeadLockParentDirectoryLease]::Read($handle)
    if (
      (($identity.FileAttributes -band [System.IO.FileAttributes]::Directory) -ne 0) -or
      (($identity.FileAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -or
      [uint32]$identity.VolumeSerialNumber -ne [uint32]$ParentLease.VolumeSerialNumber
    ) {
      throw "dead lock source is not a regular file in leased volume"
    }
    $stream = New-Object System.IO.FileStream(
      $handle,
      [System.IO.FileAccess]::Read
    )
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
      $hashBytes = $sha256.ComputeHash($stream)
      $fingerprint = (
        [System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()
      )
    } finally {
      $sha256.Dispose()
    }
    if (-not [string]::Equals(
      $fingerprint,
      $ExpectedFingerprint,
      [System.StringComparison]::Ordinal
    )) {
      throw "dead lock source fingerprint changed"
    }
    return [pscustomobject]@{
      SourcePath = $stateCanonical
      Stream = $stream
      FileAttributes = [uint32]$identity.FileAttributes
      VolumeSerialNumber = [uint32]$identity.VolumeSerialNumber
      FileIndex = [uint64]$identity.FileIndex
      Bytes = [long]$stream.Length
      Fingerprint = $fingerprint
    }
  } catch {
    if ($stream) {
      $stream.Dispose()
    } else {
      $handle.Dispose()
    }
    throw
  }
}

function Close-BidKingDeadMonitorLockSourceLease {
  param([object]$Lease)
  if ($Lease -and $Lease.Stream) {
    $Lease.Stream.Dispose()
  }
}

function Get-BidKingDeadMonitorLockFileEvidence {
  param(
    [string]$LiteralPath,
    [string]$ExpectedParent,
    [string]$ExpectedFingerprint
  )
  $result = [ordered]@{
    State = "invalid"
    FullPath = $null
    Parent = $null
    Bytes = $null
    Fingerprint = $null
  }
  try {
    $fullPath = ConvertTo-BidKingCanonicalPath $LiteralPath
    $expectedParentCanonical = ConvertTo-BidKingCanonicalPath $ExpectedParent
    if (-not $fullPath -or -not $expectedParentCanonical) {
      return [pscustomobject]$result
    }
    $parent = ConvertTo-BidKingCanonicalPath ([System.IO.Path]::GetDirectoryName($fullPath))
    if (-not (Test-BidKingSamePath $parent $expectedParentCanonical)) {
      return [pscustomobject]$result
    }
    $pathState = Get-BidKingMonitorLockPathState -StatePath $fullPath
    if ($pathState -ne "file") {
      $result.State = if ($pathState -eq "absent") { "absent" } else { "invalid" }
      return [pscustomobject]$result
    }
    $item = Get-Item -LiteralPath $fullPath -Force -ErrorAction Stop
    if (
      $item.PSIsContainer -or
      (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -or
      -not (Test-BidKingSamePath ([string]$item.FullName) $fullPath) -or
      -not (Test-BidKingSamePath ([string]$item.DirectoryName) $expectedParentCanonical)
    ) {
      return [pscustomobject]$result
    }
    $rawBytes = [System.IO.File]::ReadAllBytes($fullPath)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
      $hashBytes = $sha256.ComputeHash($rawBytes)
      $fingerprint = (
        [System.BitConverter]::ToString($hashBytes).Replace("-", "").ToLowerInvariant()
      )
    } finally {
      $sha256.Dispose()
    }
    $result.FullPath = $fullPath
    $result.Parent = $parent
    $result.Bytes = [long]$rawBytes.Length
    $result.Fingerprint = $fingerprint
    if (-not [string]::Equals(
      $fingerprint,
      $ExpectedFingerprint,
      [System.StringComparison]::Ordinal
    )) {
      $result.State = "changed"
      return [pscustomobject]$result
    }
    $result.State = "exact"
    return [pscustomobject]$result
  } catch {
    return [pscustomobject]$result
  }
}

function New-BidKingDeadMonitorLockQuarantinePath {
  param([string]$StatePath)
  $parent = [System.IO.Path]::GetDirectoryName(
    (ConvertTo-BidKingCanonicalPath $StatePath)
  )
  $leaf = [System.IO.Path]::GetFileName($StatePath)
  $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
  $nonce = [Guid]::NewGuid().ToString("N")
  return [System.IO.Path]::Combine(
    $parent,
    ("{0}.quarantine.{1}.{2}" -f $leaf, $stamp, $nonce)
  )
}

function Move-BidKingDeadMonitorLockSameDirectory {
  param(
    [string]$SourcePath,
    [string]$DestinationPath,
    [object]$ParentLease,
    [object]$SourceLease
  )
  $sourceCanonical = ConvertTo-BidKingCanonicalPath $SourcePath
  $destinationCanonical = ConvertTo-BidKingCanonicalPath $DestinationPath
  if (
    -not $sourceCanonical -or
    -not $destinationCanonical -or
    -not (Test-BidKingSamePath (
      [System.IO.Path]::GetDirectoryName($sourceCanonical)
    ) ([string]$ParentLease.ParentPath)) -or
    -not (Test-BidKingSamePath (
      [System.IO.Path]::GetDirectoryName($destinationCanonical)
    ) ([string]$ParentLease.ParentPath)) -or
    -not [string]::Equals(
      $sourceCanonical,
      [string]$SourceLease.SourcePath,
      [System.StringComparison]::OrdinalIgnoreCase
    )
  ) {
    throw "dead lock rename identity mismatch"
  }
  [BidKingDeadLockParentDirectoryLease]::RenameAbsoluteNoReplace(
    $SourceLease.Stream.SafeFileHandle,
    $destinationCanonical
  )
}

function Move-BidKingDeadMonitorLockToQuarantineIfFingerprint {
  param(
    [string]$StatePath,
    [object]$Identity,
    [object]$ParentLease
  )
  if (
    -not $Identity -or
    $Identity.State -ne "stale" -or
    $Identity.RawFingerprint -cnotmatch '^[0-9a-f]{64}$'
  ) {
    return [pscustomobject]@{ State = "quarantine_failed" }
  }
  $sourcePath = ConvertTo-BidKingCanonicalPath $StatePath
  if (-not $sourcePath) {
    return [pscustomobject]@{ State = "quarantine_failed" }
  }
  $parent = ConvertTo-BidKingCanonicalPath (
    [System.IO.Path]::GetDirectoryName($sourcePath)
  )
  if (-not $parent) {
    return [pscustomobject]@{ State = "quarantine_failed" }
  }
  if (-not (Test-BidKingDeadMonitorLockParentLease `
    -Lease $ParentLease `
    -StatePath $sourcePath `
    -ExpectedLogDir $parent)) {
    return [pscustomobject]@{ State = "changed" }
  }
  $before = Get-BidKingDeadMonitorLockFileEvidence `
    -LiteralPath $sourcePath `
    -ExpectedParent $parent `
    -ExpectedFingerprint ([string]$Identity.RawFingerprint)
  if ($before.State -eq "changed") {
    return [pscustomobject]@{ State = "changed" }
  }
  if ($before.State -ne "exact") {
    return [pscustomobject]@{ State = "quarantine_failed" }
  }

  $sourceLease = $null
  $destinationLease = $null
  $quarantinePath = $null
  try {
    try {
      $sourceLease = Open-BidKingDeadMonitorLockSourceLease `
        -StatePath $sourcePath `
        -ParentLease $ParentLease `
        -ExpectedFingerprint ([string]$Identity.RawFingerprint)
      if (
        [long]$sourceLease.Bytes -ne [long]$before.Bytes -or
        -not [string]::Equals(
          [string]$sourceLease.Fingerprint,
          [string]$before.Fingerprint,
          [System.StringComparison]::Ordinal
        )
      ) {
        return [pscustomobject]@{ State = "changed" }
      }
      $quarantinePath = ConvertTo-BidKingCanonicalPath (
        (New-BidKingDeadMonitorLockQuarantinePath -StatePath $sourcePath)
      )
      if (
        -not $quarantinePath -or
        -not (Test-BidKingSamePath (
          [System.IO.Path]::GetDirectoryName($quarantinePath)
        ) $parent) -or
        (Test-Path -LiteralPath $quarantinePath) -or
        -not (Test-BidKingDeadMonitorLockParentLease `
          -Lease $ParentLease `
          -StatePath $sourcePath `
          -ExpectedLogDir $parent)
      ) {
        return [pscustomobject]@{ State = "quarantine_failed" }
      }
      if (-not (Test-BidKingRecycledMonitorProcessUnchanged -Identity $Identity)) {
        return [pscustomobject]@{ State = "changed" }
      }
      Move-BidKingDeadMonitorLockSameDirectory `
        -SourcePath $sourcePath `
        -DestinationPath $quarantinePath `
        -ParentLease $ParentLease `
        -SourceLease $sourceLease
    } catch {
      return [pscustomobject]@{ State = "quarantine_failed" }
    }

    try {
      $destinationLease = Open-BidKingDeadMonitorLockSourceLease `
        -StatePath $quarantinePath `
        -ParentLease $ParentLease `
        -ExpectedFingerprint ([string]$Identity.RawFingerprint) `
        -ReadbackOnly
    } catch {
      return [pscustomobject]@{ State = "quarantine_failed" }
    }
    $sourceAbsent = (Get-BidKingMonitorLockPathState -StatePath $sourcePath) -eq "absent"
    if (
      $sourceAbsent -and
      $destinationLease -and
      (Test-BidKingDeadMonitorLockParentLease `
        -Lease $ParentLease `
        -StatePath $sourcePath `
        -ExpectedLogDir $parent) -and
      [long]$destinationLease.Bytes -eq [long]$sourceLease.Bytes -and
      [string]::Equals(
        [string]$destinationLease.Fingerprint,
        [string]$sourceLease.Fingerprint,
        [System.StringComparison]::Ordinal
      ) -and
      [uint32]$destinationLease.VolumeSerialNumber -eq [uint32]$sourceLease.VolumeSerialNumber -and
      [uint64]$destinationLease.FileIndex -eq [uint64]$sourceLease.FileIndex
    ) {
      return [pscustomobject]@{ State = "quarantined" }
    }
    # A post-move red identity cannot be restored safely.  Preserve the bounded
    # sibling as evidence and report failure rather than guessing ownership.
    return [pscustomobject]@{ State = "quarantine_failed" }
  } finally {
    Close-BidKingDeadMonitorLockSourceLease -Lease $destinationLease
    Close-BidKingDeadMonitorLockSourceLease -Lease $sourceLease
  }
}

function Test-BidKingDeadMonitorLockIdentityExact {
  param(
    [object]$Candidate,
    [object]$Frozen
  )
  return (
    $Candidate -and
    $Frozen -and
    $Candidate.State -eq "stale" -and
    $Candidate.Reason -ceq $Frozen.Reason -and
    $Candidate.ActualProcessStartedAt -eq $Frozen.ActualProcessStartedAt -and
    $Candidate.ProcessName -ceq $Frozen.ProcessName -and
    [int]$Candidate.ProcessId -eq [int]$Frozen.ProcessId -and
    [double]$Candidate.ProcessStartedAt -eq [double]$Frozen.ProcessStartedAt -and
    [string]::Equals(
      [string]$Candidate.RawFingerprint,
      [string]$Frozen.RawFingerprint,
      [System.StringComparison]::Ordinal
    ) -and
    [string]::Equals(
      [string]$Candidate.MonitorPath,
      [string]$Frozen.MonitorPath,
      [System.StringComparison]::Ordinal
    ) -and
    [string]::Equals(
      [string]$Candidate.LogDir,
      [string]$Frozen.LogDir,
      [System.StringComparison]::Ordinal
    )
  )
}

function Invoke-BidKingDeadMonitorLockCleanup {
  param(
    [string]$StatePath,
    [string]$ExpectedMonitorPath,
    [string]$ExpectedLogDir,
    [string]$ExpectedPackageGenerationIdentity = "",
    [object]$ExpectedIdentity = $null
  )
  $stateCanonical = ConvertTo-BidKingCanonicalPath $StatePath
  $expectedParent = ConvertTo-BidKingCanonicalPath $ExpectedLogDir
  if (-not $stateCanonical -or -not $expectedParent) {
    return [pscustomobject]@{ Succeeded = $false; State = "quarantine_failed"; Mode = "preserved" }
  }
  $stateParent = ConvertTo-BidKingCanonicalPath (
    [System.IO.Path]::GetDirectoryName($stateCanonical)
  )
  if (-not (Test-BidKingSamePath $stateParent $expectedParent)) {
    return [pscustomobject]@{ Succeeded = $false; State = "quarantine_failed"; Mode = "preserved" }
  }
  $pathState = Get-BidKingMonitorLockPathState -StatePath $stateCanonical
  if ($pathState -eq "absent") {
    return [pscustomobject]@{ Succeeded = $true; State = "absent"; Mode = "absent" }
  }
  if ($pathState -ne "file") {
    $state = if ($pathState -eq "nonregular") { "changed" } else { "unknown" }
    return [pscustomobject]@{ Succeeded = $false; State = $state; Mode = "preserved" }
  }

  $parentLease = $null
  try {
    try {
      $parentLease = Open-BidKingDeadMonitorLockParentLease `
        -StatePath $stateCanonical `
        -ExpectedLogDir $expectedParent
    } catch {
      return [pscustomobject]@{ Succeeded = $false; State = "quarantine_failed"; Mode = "preserved" }
    }
    if (-not (Test-BidKingDeadMonitorLockParentLease `
      -Lease $parentLease `
      -StatePath $stateCanonical `
      -ExpectedLogDir $expectedParent)) {
      return [pscustomobject]@{ Succeeded = $false; State = "changed"; Mode = "preserved" }
    }

    $initial = Get-BidKingMonitorLockIdentity `
      -StatePath $stateCanonical `
      -ExpectedMonitorPath $ExpectedMonitorPath `
      -ExpectedLogDir $ExpectedLogDir `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
    if ($initial.State -eq "absent") {
      if ((Get-BidKingMonitorLockPathState -StatePath $stateCanonical) -ne "absent") {
        return [pscustomobject]@{ Succeeded = $false; State = "changed"; Mode = "preserved" }
      }
      return [pscustomobject]@{ Succeeded = $true; State = "absent"; Mode = "absent" }
    }
    if ($initial.State -ne "stale") {
      return [pscustomobject]@{ Succeeded = $false; State = [string]$initial.State; Mode = "preserved" }
    }
    if ($ExpectedIdentity -and -not (
      Test-BidKingDeadMonitorLockIdentityExact `
        -Candidate $initial `
        -Frozen $ExpectedIdentity
    )) {
      return [pscustomobject]@{ Succeeded = $false; State = "changed"; Mode = "preserved" }
    }

    # Re-prove the dead PID and exact raw lock while the parent directory object
    # remains leased, immediately before opening the delete-bound file handle.
    $current = Get-BidKingMonitorLockIdentity `
      -StatePath $stateCanonical `
      -ExpectedMonitorPath $ExpectedMonitorPath `
      -ExpectedLogDir $ExpectedLogDir `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
    if (-not (
      Test-BidKingDeadMonitorLockIdentityExact `
        -Candidate $current `
        -Frozen $initial
    )) {
      return [pscustomobject]@{ Succeeded = $false; State = "changed"; Mode = "preserved" }
    }

    try {
      $removal = Remove-BidKingMonitorLockIfFingerprint `
        -StatePath $stateCanonical `
        -Identity $current
    } catch {
      return [pscustomobject]@{ Succeeded = $false; State = "quarantine_failed"; Mode = "preserved" }
    }
    if ($removal -eq "unknown") {
      # Atomic delete gave no linearizable answer.  Re-prove dead PID/epoch and
      # raw lock a third time before the dedicated quarantine fallback.
      $postUnknown = Get-BidKingMonitorLockIdentity `
        -StatePath $stateCanonical `
        -ExpectedMonitorPath $ExpectedMonitorPath `
        -ExpectedLogDir $ExpectedLogDir `
        -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
      if (
        -not (Test-BidKingDeadMonitorLockIdentityExact `
          -Candidate $postUnknown `
          -Frozen $current) -or
        -not (Test-BidKingDeadMonitorLockParentLease `
          -Lease $parentLease `
          -StatePath $stateCanonical `
          -ExpectedLogDir $expectedParent)
      ) {
        if (
          (Test-Path -LiteralPath $stateCanonical) -and
          -not (Test-Path -LiteralPath $stateCanonical -PathType Leaf)
        ) {
          return [pscustomobject]@{ Succeeded = $false; State = "quarantine_failed"; Mode = "preserved" }
        }
        return [pscustomobject]@{ Succeeded = $false; State = "changed"; Mode = "preserved" }
      }
      $quarantine = Move-BidKingDeadMonitorLockToQuarantineIfFingerprint `
        -StatePath $stateCanonical `
        -Identity $postUnknown `
        -ParentLease $parentLease
      if ($quarantine.State -eq "quarantined") {
        return [pscustomobject]@{
          Succeeded = $true
          State = "quarantined"
          Mode = "quarantined"
        }
      }
      return [pscustomobject]@{
        Succeeded = $false
        State = [string]$quarantine.State
        Mode = "preserved"
      }
    }
    if ($removal -notin @("absent", "deleted")) {
      return [pscustomobject]@{ Succeeded = $false; State = [string]$removal; Mode = "preserved" }
    }
    $post = Get-BidKingMonitorLockIdentity `
      -StatePath $stateCanonical `
      -ExpectedMonitorPath $ExpectedMonitorPath `
      -ExpectedLogDir $ExpectedLogDir `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
    if (
      $post.State -ne "absent" -or
      (Get-BidKingMonitorLockPathState -StatePath $stateCanonical) -ne "absent" -or
      -not (Test-BidKingDeadMonitorLockParentLease `
        -Lease $parentLease `
        -StatePath $stateCanonical `
        -ExpectedLogDir $expectedParent)
    ) {
      return [pscustomobject]@{ Succeeded = $false; State = "changed"; Mode = "preserved" }
    }
    return [pscustomobject]@{
      Succeeded = $true
      State = "reclaimed"
      Mode = [string]$removal
    }
  } finally {
    Close-BidKingDeadMonitorLockParentLease -Lease $parentLease
  }
}

function Get-BidKingProcessFromStateFile {
  param(
    [string]$StatePath,
    [string]$CommandMarker,
    [string]$DiscoverySource,
    [string]$StatusPath,
    [string]$ExpectedMonitorPath = "",
    [string]$ExpectedLogDir = "",
    [string]$ExpectedPackageGenerationIdentity = ""
  )
  if (-not (Test-Path -LiteralPath $StatePath)) { return }

  if ($ExpectedMonitorPath -or $ExpectedLogDir) {
    $identity = Get-BidKingMonitorLockIdentity `
      -StatePath $StatePath `
      -ExpectedMonitorPath $ExpectedMonitorPath `
      -ExpectedLogDir $ExpectedLogDir `
      -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
    if ($identity.State -eq "current") {
      [pscustomobject]@{
        ProcessId = [int]$identity.ProcessId
        Name = "$($identity.ProcessName).exe"
        CommandLine = $CommandMarker
        DiscoverySource = $DiscoverySource
        LockIdentity = "current"
      }
      return
    }
    if ($identity.State -eq "stale") {
      $cleanup = Invoke-BidKingDeadMonitorLockCleanup `
        -StatePath $StatePath `
        -ExpectedMonitorPath $ExpectedMonitorPath `
        -ExpectedLogDir $ExpectedLogDir `
        -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity `
        -ExpectedIdentity $identity
      if ($cleanup.Succeeded -and $cleanup.State -in @("reclaimed", "quarantined")) {
        Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
          "stale_process_state_reclaimed source={0} mode={1}" -f `
            $DiscoverySource, $cleanup.Mode
        )
      } elseif (-not $cleanup.Succeeded) {
        Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
          "monitor_lock_identity_unverified source={0} state={1}" -f `
            $DiscoverySource, $cleanup.State
        )
      }
      return
    }
    if ($identity.State -ne "absent") {
      Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
        "monitor_lock_identity_unverified source={0} state={1}" -f `
          $DiscoverySource, $identity.State
      )
    }
    return
  }

  try {
    $raw = (Get-Content -LiteralPath $StatePath -Raw -ErrorAction Stop).Trim()
    if (-not $raw) { throw "empty process state" }

    $processId = $null
    $startedAt = $null
    $payload = $null
    if ($raw.StartsWith("{")) {
      $payload = $raw | ConvertFrom-Json -ErrorAction Stop
      $processId = [int]$payload.pid
      if ($payload.PSObject.Properties.Name -contains "started_at") {
        $startedAt = [double]$payload.started_at
      }
    } else {
      $processId = [int]$raw
    }
    if (-not $processId -or $processId -le 0) { throw "invalid process id" }

    $process = Get-Process -Id $processId -ErrorAction Stop
    if ($process.ProcessName -notlike "python*") {
      throw "pid no longer belongs to Python"
    }

    # monitor.lock records the Python process creation epoch. Reject a recycled
    # Python PID when the observed start time is materially different.
    if ($startedAt -and $startedAt -gt 0) {
      $startTimeMatches = $true
      try {
        $expected = [DateTimeOffset]::FromUnixTimeMilliseconds(
          [long][Math]::Round($startedAt * 1000.0)
        ).UtcDateTime
        $actual = $process.StartTime.ToUniversalTime()
        $startTimeMatches = [Math]::Abs(($actual - $expected).TotalSeconds) -le 120.0
      } catch {
        # We are already elevated, but unusual protected-process policy can still
        # hide StartTime. A verified live Python PID is safer than double-launching.
        Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
          "pid_start_time_check_unavailable_assumed_live source={0}" -f $DiscoverySource
        )
      }
      if (-not $startTimeMatches) {
        throw "pid start time does not match state file"
      }
    }

    [pscustomobject]@{
      ProcessId = [int]$processId
      Name = "$($process.ProcessName).exe"
      CommandLine = $CommandMarker
      DiscoverySource = $DiscoverySource
    }
  } catch {
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
      "stale_process_state_reclaimed source={0}" -f $DiscoverySource
    )
  }
}

function Get-BidKingLiveProcesses {
  param(
    [string]$MonitorLockPath,
    [string]$StatusPath,
    [string]$ExpectedMonitorPath,
    [string]$ExpectedLogDir,
    [string]$ExpectedPackageGenerationIdentity = "",
    [string]$InventoryPhase = "",
    [switch]$Refresh
  )
  if (
    -not $ExpectedMonitorPath -or
    -not $ExpectedLogDir -or
    $ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$'
  ) {
    Write-BidKingStartupStatus -StatusPath $StatusPath -Event (
      "monitor_identity_contract_missing"
    )
    return
  }
  $Inventory = Get-BidKingProcessInventory -PhaseToken $InventoryPhase -StatusPath $StatusPath -Refresh:$Refresh
  if ($Inventory.Result -ceq "ok") {
    try {
      $Inventory.Rows |
        Where-Object {
          $_.Name -like "python*" -and
          (Test-BidKingMonitorCommandIdentity `
            -CommandLine $_.CommandLine `
            -ExpectedMonitorPath $ExpectedMonitorPath `
            -ExpectedLogDir $ExpectedLogDir `
            -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
          )
        }
      return
    } catch {
      Enable-BidKingPidFallback -StatusPath $StatusPath -Failure $_
    }
  }

  $fallbackArgs = @{
    StatePath = $MonitorLockPath
    CommandMarker = "verified_current_monitor"
    DiscoverySource = "monitor.lock"
    StatusPath = $StatusPath
    ExpectedMonitorPath = $ExpectedMonitorPath
    ExpectedLogDir = $ExpectedLogDir
    ExpectedPackageGenerationIdentity = $ExpectedPackageGenerationIdentity
  }
  Get-BidKingProcessFromStateFile @fallbackArgs
}

function Get-BidKingMonitorGenerationConflicts {
  param(
    [string]$StatusPath,
    [string]$ExpectedMonitorPath,
    [string]$ExpectedLogDir,
    [string]$ExpectedPackageGenerationIdentity = "",
    [string]$InventoryPhase = "",
    [switch]$Refresh
  )
  if (
    -not $ExpectedMonitorPath -or
    -not $ExpectedLogDir -or
    $ExpectedPackageGenerationIdentity -cnotmatch '^[0-9a-f]{64}$'
  ) { return }
  $Inventory = Get-BidKingProcessInventory -PhaseToken $InventoryPhase -StatusPath $StatusPath -Refresh:$Refresh
  if ($Inventory.Result -ceq "ok") {
    try {
      foreach ($process in @($Inventory.Rows)) {
        if ($process.Name -notlike "python*") { continue }
        $state = Get-BidKingMonitorCommandGenerationState `
          -CommandLine $process.CommandLine `
          -ExpectedMonitorPath $ExpectedMonitorPath `
          -ExpectedLogDir $ExpectedLogDir `
          -ExpectedPackageGenerationIdentity $ExpectedPackageGenerationIdentity
        if ($state -notin @("stale_package", "unknown", "foreign_context")) {
          continue
        }
        [pscustomobject]@{
          ProcessId = [int]$process.ProcessId
          Name = [string]$process.Name
          CommandLine = "unverified_monitor_generation"
          GenerationState = $state
          DiscoverySource = "Win32_Process"
        }
      }
    } catch {
      Enable-BidKingPidFallback -StatusPath $StatusPath -Failure $_
    }
  }
}

function Get-BidKingOverlayProcesses {
  param(
    [string]$OverlayPidPath,
    [string]$StatusPath,
    [string]$InventoryPhase = "",
    [switch]$Refresh
  )
  $script:BidKingOverlayAbsenceQualified = $false
  $Inventory = Get-BidKingProcessInventory -PhaseToken $InventoryPhase -StatusPath $StatusPath -Refresh:$Refresh
  if ($Inventory.Result -ceq "ok") {
    try {
      $absence = @{ Qualified = $true }
      $Inventory.Rows |
        Where-Object {
          $candidate = $_.Name -like "python*"
          $matches = $candidate -and $_.CommandLine -like "*ahmad_tk_overlay.py*"
          if ($matches -or ($candidate -and [string]::IsNullOrWhiteSpace($_.CommandLine))) {
            $absence.Qualified = $false
          }
          $matches
        }
      # A usable CIM query can still omit a candidate's command line.  Keep
      # availability separate from proof that this query found no possible UI.
      $script:BidKingOverlayAbsenceQualified = $absence.Qualified
      return
    } catch {
      Enable-BidKingPidFallback -StatusPath $StatusPath -Failure $_
    }
  }

  $fallbackArgs = @{
    StatePath = $OverlayPidPath
    CommandMarker = "ahmad_tk_overlay.py"
    DiscoverySource = "ahmad_overlay.pid"
    StatusPath = $StatusPath
  }
  Get-BidKingProcessFromStateFile @fallbackArgs
}
