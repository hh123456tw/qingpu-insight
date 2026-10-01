# Daily 591 refresh: list API (headless, logged-in profile) -> history panel -> radar.
# Registered as a Windows scheduled task by scripts/register-daily-listings.ps1.
# Every step stops politely on a login wall or verification page; the log says why.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not $env:QINGPU_591_PROFILE_DIR) { $env:QINGPU_591_PROFILE_DIR = "instance/chrome-591" }

$logDir = Join-Path $root "logs/daily-listings"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
$exe = Join-Path $root ".venv/Scripts/qingpu-data.exe"

function Invoke-Step([string]$name, [string[]]$arguments) {
    "== $name $(Get-Date -Format o)" | Out-File -FilePath $log -Append -Encoding utf8
    & $exe @arguments *>&1 |
        Where-Object { $_ -notmatch "chromedriver|GetHandleVerifier|KERNEL32|ntdll|No symbol" } |
        Out-File -FilePath $log -Append -Encoding utf8
    "== $name exit $LASTEXITCODE" | Out-File -FilePath $log -Append -Encoding utf8
    return $LASTEXITCODE
}

$update = Invoke-Step "listing-update" @("listing-update", "--types", "sale")
if ($update -ne 0) {
    # No fresh complete batch: skip the radar instead of ranking yesterday's list again.
    exit $update
}
Invoke-Step "listing-history" @("listing-history") | Out-Null
$radar = Invoke-Step "listing-radar" @("listing-radar", "--max-listings", "60")

# Keep 60 days of logs.
Get-ChildItem $logDir -Filter *.log |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-60) } |
    Remove-Item -Force
exit $radar
