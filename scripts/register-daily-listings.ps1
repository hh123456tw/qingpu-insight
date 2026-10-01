# Register (or replace) the daily 591 refresh as a scheduled task for the current user.
#   pwsh -File scripts/register-daily-listings.ps1            # 09:30 every day
#   pwsh -File scripts/register-daily-listings.ps1 -At 07:00
#   pwsh -File scripts/register-daily-listings.ps1 -Remove
param(
    [string]$At = "09:30",
    [switch]$Remove
)

$taskName = "qingpu-insight-daily-listings"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    "Removed $taskName"
    exit 0
}

$script = Join-Path $PSScriptRoot "daily-listings.ps1"
$shell = (Get-Command pwsh -ErrorAction SilentlyContinue)?.Source
if (-not $shell) { $shell = (Get-Command powershell).Source }

$action = New-ScheduledTaskAction -Execute $shell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# Run as soon as possible after a missed start (laptop asleep at 09:30); never twice at once.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "qingpu-insight: daily 591 list API, history panel and radar" `
    -Force | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
