# Keeps the NSE desk agent running: stops Windows from idle-sleeping while it runs, and restarts it if it exits.
# Started by the scheduled task that install.ps1 creates. You normally never run this by hand.
param([Parameter(Mandatory = $true)][string]$Python)

$agentDir = Split-Path -Parent $PSScriptRoot
$log = Join-Path $agentDir "agent.log"
Set-Location $agentDir
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: no idle sleep while this window of PowerShell is alive (lid-close still sleeps)
Add-Type -Namespace Win -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);'
[void][Win.Power]::SetThreadExecutionState([uint32]2147483649)

while ($true) {
    Add-Content -Path $log -Value ("{0} launcher: starting agent" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -Encoding utf8
    # cmd does the redirection so the log stays plain UTF-8 text and is appended to
    $line = '"{0}" -m nse_agent run >> "{1}" 2>&1' -f $Python, $log
    Start-Process -FilePath "cmd.exe" -ArgumentList "/d /s /c `"$line`"" -Wait -WindowStyle Hidden
    Start-Sleep -Seconds 30
}
