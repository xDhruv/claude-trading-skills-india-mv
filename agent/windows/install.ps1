# Installs the NSE desk agent on Windows as a scheduled task that starts when you log in and restarts if it stops.
# Safe to re-run. No administrator rights needed. Usage (PowerShell, from the repo folder):
#   powershell -ExecutionPolicy Bypass -File agent\windows\install.ps1              install or update
#   powershell -ExecutionPolicy Bypass -File agent\windows\install.ps1 -Uninstall   stop and remove
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"

$TaskName = "NSE Desk Agent"
$AgentDir = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $PSScriptRoot "run.ps1"
$Log = Join-Path $AgentDir "agent.log"

function Stop-Existing {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    # the launcher and agent are separate processes; stop the ones that belong to this repo
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -like "*windows\run.ps1*" -or $_.CommandLine -like "*nse_agent*run*") -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Uninstall) {
    Stop-Existing
    Write-Host "Removed '$TaskName'. Your .env, logs and state are untouched."
    exit 0
}

# 1. Python 3.10+ (the agent uses only the standard library, nothing to pip install)
$Python = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
    $exe = $cand[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $extra = @($cand | Select-Object -Skip 1)
        $path = & $exe @extra -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path "$path")) { $Python = "$path".Trim(); break }
    } catch { }
}
if (-not $Python) {
    throw "Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), open a NEW PowerShell window and run this again."
}
Write-Host "Using $(& $Python --version) at $Python"

# 2. Settings file. Stop the first time so you can fill it in.
$envFile = Join-Path $AgentDir ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $AgentDir ".env.example") $envFile
    Write-Host ""
    Write-Host "Created $envFile. Notepad will open: set SMTP_USER, SMTP_APP_PASSWORD, EMAIL_TO and CAPITAL"
    Write-Host "(and the Angel One lines if you have them). Remove the # at the start of any line you fill in."
    Write-Host "Save, close Notepad, then run this script again."
    Start-Process notepad.exe $envFile
    exit 0
}

# 3. Checks before installing
Push-Location $AgentDir
try {
    Write-Host "Running the self-check..."
    & $Python -m nse_agent check
    if ($LASTEXITCODE -ne 0) { throw "The check failed: fix the items above, then run this script again." }
    Write-Host "Sending a test email..."
    & $Python -m nse_agent test-email
} finally { Pop-Location }

# 4. Scheduled task: runs at logon for this user, hidden, restarts on failure, no time limit, runs on battery
Stop-Existing
$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Python "{1}"' -f $RunScript, $Python
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs -WorkingDirectory $AgentDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "NSE desk agent: market analysis and Gmail alerts" | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5

$state = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host ""
Write-Host "Installed. Task state: $state"
Write-Host ""
Write-Host "Next steps"
Write-Host "  Watch the log     Get-Content `"$Log`" -Wait -Tail 20"
Write-Host "  Status            Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Restart           Stop-ScheduledTask -TaskName '$TaskName'; Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Stop and remove   powershell -ExecutionPolicy Bypass -File agent\windows\install.ps1 -Uninstall"
Write-Host ""
Write-Host "Keep the PC awake and plugged in from 08:45 to 15:45 IST (Settings, System, Power: set sleep to Never"
Write-Host "while plugged in). The agent stops Windows from idle-sleeping, but closing a laptop lid still sleeps it."
Write-Host "Details: docs/nse-agent-windows.md"
