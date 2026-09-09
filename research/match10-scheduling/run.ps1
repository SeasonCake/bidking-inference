# Companion runner: docs/research/PROVENANCE.json (entry: match10-harnesses).
# Windows PowerShell 5.1 / PowerShell 7; no SDK installation or global configuration.
[CmdletBinding()]
param(
    [string] $Compiler = '',
    [string] $OutputDirectory = '',
    [ValidateSet('all', 'limiter', 'scheduler', 'failure-control', 'timeout-control')]
    [string] $Scenario = 'all',
    [ValidateRange(1, 60)] [int] $CompileTimeoutSeconds = 20,
    [ValidateRange(1, 60)] [int] $RunTimeoutSeconds = 20
)

$ErrorActionPreference = 'Stop'
$taskExit = 2
$taskReceipt = [ordered]@{ schema = 1; scenario = $Scenario; compile = $null; run = $null }
$taskRunRoot = $null

function Quote-WindowsArgument([string] $Value) {
    # Windows command-line quoting, not a shell command.
    return '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Get-SourceSha256([string] $Path) {
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    $taskStream = $null
    try {
        $taskStream = [IO.File]::OpenRead($Path)
        return ([BitConverter]::ToString($taskHasher.ComputeHash($taskStream))).Replace('-', '').ToLowerInvariant()
    }
    finally { if ($taskStream) { $taskStream.Dispose() }; $taskHasher.Dispose() }
}

function Invoke-BoundedProcess {
    param([string] $Executable, [string[]] $Arguments, [int] $TimeoutSeconds, [string] $WorkingDirectory)
    $taskInfo = New-Object System.Diagnostics.ProcessStartInfo
    $taskInfo.FileName = $Executable
    $taskInfo.Arguments = (($Arguments | ForEach-Object { Quote-WindowsArgument $_ }) -join ' ')
    $taskInfo.WorkingDirectory = $WorkingDirectory
    $taskInfo.UseShellExecute = $false
    $taskInfo.CreateNoWindow = $true
    $taskInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $taskInfo.RedirectStandardOutput = $true
    $taskInfo.RedirectStandardError = $true
    $taskInfo.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $taskInfo.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $taskProcess = New-Object System.Diagnostics.Process
    $taskProcess.StartInfo = $taskInfo
    $taskWatch = [System.Diagnostics.Stopwatch]::StartNew()
    $taskStarted = $false
    try {
        if (-not $taskProcess.Start()) { throw 'Process start failed.' }
        $taskStarted = $true
        $taskPid = $taskProcess.Id
        $taskOut = $taskProcess.StandardOutput.ReadToEndAsync()
        $taskErr = $taskProcess.StandardError.ReadToEndAsync()
        $taskTimedOut = -not $taskProcess.WaitForExit($TimeoutSeconds * 1000)
        if ($taskTimedOut) {
            if (-not $taskProcess.HasExited) { $taskProcess.Kill() }
            if (-not $taskProcess.WaitForExit(5000)) { throw 'Timed-out process did not exit after termination.' }
        }
        $taskOutputTasks = [System.Threading.Tasks.Task[]]@($taskOut, $taskErr)
        if (-not [System.Threading.Tasks.Task]::WaitAll($taskOutputTasks, 5000)) {
            throw 'Process output collection exceeded its deadline.'
        }
        $taskWatch.Stop()
        return [pscustomobject]@{
            executable = $Executable; arguments = $Arguments; pid = $taskPid
            timeout_seconds = $TimeoutSeconds; timed_out = $taskTimedOut
            exit_code = $(if ($taskTimedOut) { 124 } else { $taskProcess.ExitCode })
            process_exit_code = $taskProcess.ExitCode; exited = $taskProcess.HasExited
            elapsed_ms = [math]::Round($taskWatch.Elapsed.TotalMilliseconds, 3)
            stdout = $taskOut.Result; stderr = $taskErr.Result
        }
    }
    finally {
        if ($taskStarted -and -not $taskProcess.HasExited) {
            $taskProcess.Kill()
            [void] $taskProcess.WaitForExit(5000)
        }
        $taskProcess.Dispose()
    }
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This runner requires Windows and an installed .NET Framework C# compiler.'
    }
    $taskSourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
    $taskRepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    if (-not $Compiler) {
        $Compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    }
    $Compiler = [IO.Path]::GetFullPath($Compiler)
    if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
        throw 'C# compiler not found. Supply -Compiler with an installed csc.exe path.'
    }
    if (-not $OutputDirectory) {
        $OutputDirectory = Join-Path $taskRepositoryRoot 'outputs/match10-scheduling'
    }
    $OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
    $taskSourcePrefix = $taskSourceRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if ($OutputDirectory.Equals($taskSourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $OutputDirectory.StartsWith($taskSourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'OutputDirectory must be outside the research source directory.'
    }
    $taskRunName = 'run-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $taskRunRoot = Join-Path $OutputDirectory $taskRunName
    [void] [IO.Directory]::CreateDirectory($taskRunRoot)
    $taskSources = @('ReadinessTraceLimiter.cs', 'ManagedInstallScheduler.cs', 'Harness.cs') |
        ForEach-Object { Join-Path $taskSourceRoot $_ }
    foreach ($taskSource in $taskSources) {
        if (-not (Test-Path -LiteralPath $taskSource -PathType Leaf)) { throw 'A required source file is missing.' }
    }
    $taskExe = Join-Path $taskRunRoot 'SchedulingHarness.exe'
    $taskReceipt.compiler = @{ path = $Compiler; file_version = (Get-Item -LiteralPath $Compiler).VersionInfo.FileVersion }
    $taskReceipt.sources = @($taskSources | ForEach-Object {
        @{ name = [IO.Path]::GetFileName($_); sha256 = (Get-SourceSha256 $_) }
    })
    $taskCompileArgs = @('/nologo', '/noconfig', '/target:exe', '/platform:anycpu', '/langversion:5',
        '/optimize+', '/debug-', '/utf8output', '/reference:System.dll', '/reference:System.Core.dll', ('/out:' + $taskExe)) + $taskSources
    $taskBuild = Invoke-BoundedProcess -Executable $Compiler -Arguments $taskCompileArgs -TimeoutSeconds $CompileTimeoutSeconds -WorkingDirectory $taskSourceRoot
    $taskReceipt.compile = $taskBuild
    [IO.File]::WriteAllText((Join-Path $taskRunRoot 'compile.stdout.txt'), $taskBuild.stdout, (New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText((Join-Path $taskRunRoot 'compile.stderr.txt'), $taskBuild.stderr, (New-Object Text.UTF8Encoding($false)))
    Write-Output ('PHASE compile exit=' + $taskBuild.exit_code + ' timed_out=' + $taskBuild.timed_out)
    if ($taskBuild.stdout) { Write-Output $taskBuild.stdout.TrimEnd() }
    if ($taskBuild.stderr) { [Console]::Error.WriteLine($taskBuild.stderr.TrimEnd()) }
    $taskExit = [int] $taskBuild.exit_code
    if ($taskExit -eq 0) {
        if (-not (Test-Path -LiteralPath $taskExe -PathType Leaf)) { throw 'Compiler returned success without the expected executable.' }
        $taskRun = Invoke-BoundedProcess -Executable $taskExe -Arguments @($Scenario) -TimeoutSeconds $RunTimeoutSeconds -WorkingDirectory $taskRunRoot
        $taskReceipt.run = $taskRun
        [IO.File]::WriteAllText((Join-Path $taskRunRoot 'run.stdout.txt'), $taskRun.stdout, (New-Object Text.UTF8Encoding($false)))
        [IO.File]::WriteAllText((Join-Path $taskRunRoot 'run.stderr.txt'), $taskRun.stderr, (New-Object Text.UTF8Encoding($false)))
        if ($taskRun.stdout) { Write-Output $taskRun.stdout.TrimEnd() }
        if ($taskRun.stderr) { [Console]::Error.WriteLine($taskRun.stderr.TrimEnd()) }
        Write-Output ('PHASE run exit=' + $taskRun.exit_code + ' timed_out=' + $taskRun.timed_out + ' exited=' + $taskRun.exited)
        $taskExit = [int] $taskRun.exit_code
    }
}
catch {
    $taskReceipt.error = $_.Exception.Message
    [Console]::Error.WriteLine('RUNNER_ERROR ' + $_.Exception.Message)
    $taskExit = 2
}
finally {
    if ($taskRunRoot) {
        $taskReceipt.exit_code = $taskExit
        $taskReceipt.finished_at_utc = [DateTime]::UtcNow.ToString('o')
        [IO.File]::WriteAllText((Join-Path $taskRunRoot 'RUN.json'), ($taskReceipt | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
        Write-Output ('RECEIPT ' + (Join-Path $taskRunRoot 'RUN.json'))
    }
}
exit $taskExit
