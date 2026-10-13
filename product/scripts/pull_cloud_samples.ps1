param(
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Server,
    [string]$RemoteRepo = "/srv/bidking",
    [string]$Stamp = (Get-Date -Format "yyyyMMdd_HHmmss")
)

$ErrorActionPreference = "Stop"
if ($Stamp -notmatch "^[0-9]{8}_[0-9]{6}$") {
    throw "Stamp must use yyyyMMdd_HHmmss, got: $Stamp"
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LocalDir = Join-Path $RepoRoot "data\collected"
$LocalFile = Join-Path $LocalDir "samples_cloud_$Stamp.jsonl.gz"
$PartialFile = Join-Path $LocalDir "samples_cloud_$Stamp.partial.jsonl.gz"
if ((Test-Path -LiteralPath $LocalFile) -or (Test-Path -LiteralPath $PartialFile)) {
    throw "Refusing to overwrite an existing timestamp artifact: $LocalFile"
}

New-Item -ItemType Directory -Path $LocalDir -Force | Out-Null
$RemoteFile = "/home/ubuntu/samples_cloud_$Stamp.jsonl.gz"
$RemoteScript = @'
set -eu
umask 077
cd '{0}'
test -s export_key.txt
test ! -e '{1}'
key=$(cat export_key.txt)
curl -fsS -H "X-Api-Key: $key" -H "Accept-Encoding: gzip" -o '{1}' http://127.0.0.1:18900/export
test -s '{1}'
gzip -t '{1}'
line_count=$(gzip -cd '{1}' | wc -l)
test "$line_count" -ge 49623
echo "remote_lines=$line_count"
sha256sum '{1}'
'@ -f $RemoteRepo, $RemoteFile
$RemoteScriptBase64 = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($RemoteScript)
)
# Windows PowerShell/PowerShell 7 may reassemble a native program's multi-line
# argument and strip the quotes needed by curl -H. Send an ASCII-only wrapper;
# the remote bash script itself is decoded byte-for-byte before execution.
$RemoteCommand = "printf %s $RemoteScriptBase64 | base64 -d | bash"

Write-Host "Creating a read-only collector export on $Server ..."
Write-Host "SSH may prompt for the server password; the password is not stored by this script."
$SshOptions = @(
    "-o", "PreferredAuthentications=publickey,password",
    "-o", "NumberOfPasswordPrompts=1",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=4"
)
& ssh @SshOptions $Server $RemoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "Remote export failed with exit code $LASTEXITCODE"
}

Write-Host "Downloading to a non-overwriting partial path ..."
& scp @SshOptions "${Server}:$RemoteFile" $PartialFile
if ($LASTEXITCODE -ne 0) {
    throw "SCP failed with exit code $LASTEXITCODE; inspect any partial file before retrying"
}

& python (Join-Path $PSScriptRoot "census_cloud_sample_fields.py") $PartialFile --heroes Raven Gabriela --strict --min-rows 49623
if ($LASTEXITCODE -ne 0) {
    throw "Downloaded file failed gzip/JSONL census; partial file preserved: $PartialFile"
}
Move-Item -LiteralPath $PartialFile -Destination $LocalFile
$Hash = & python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest().upper())" $LocalFile
if ($LASTEXITCODE -ne 0) {
    throw "SHA-256 calculation failed for: $LocalFile"
}
Write-Host "Saved without overwriting the 0713 baseline: $LocalFile"
Write-Host "SHA-256: $Hash"
Write-Host "Remote gzip was intentionally retained for audit/retry: $RemoteFile"
