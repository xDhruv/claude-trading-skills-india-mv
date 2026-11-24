# Run the NSE desk on a Windows PC, automatically

Same agent and same emails as the Mac setup ([nse-agent-mac.md](nse-agent-mac.md) explains the day, the
alerts and the limits; read it once). This page is only the Windows installation. Cost: zero.

## One-time setup

1. **Install Python 3.10 or newer** from <https://www.python.org/downloads/>. On the first installer screen,
   tick **Add python.exe to PATH**. Then open a *new* PowerShell window and check `python --version`.
2. **Install Git** from <https://git-scm.com/download/win> if `git --version` fails.
3. **Get the code:**
   ```powershell
   cd $HOME
   git clone https://github.com/xDhruv/claude-trading-skills-india-mv.git
   cd claude-trading-skills-india-mv
   ```
4. **Create a Gmail app password** (needs 2-Step Verification): <https://myaccount.google.com/apppasswords>.
5. **Run the installer:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File agent\windows\install.ps1
   ```
   The first run creates `agent\.env` and opens it in Notepad. Fill in `SMTP_USER`, `SMTP_APP_PASSWORD`,
   `EMAIL_TO`, `CAPITAL` (and the `ANGEL_*` lines if you use Angel One). Remove the `#` at the start of every
   line you fill in. Save, close, and run the same command again.
6. The second run checks your settings, sends a test email, creates a scheduled task called
   **NSE Desk Agent** (starts when you log in, restarts if it stops) and starts it.

## Everyday use

| Want to | Run (PowerShell) |
|---|---|
| Watch the log | `Get-Content agent\agent.log -Wait -Tail 20` |
| Check it is running | `Get-ScheduledTask -TaskName "NSE Desk Agent"` |
| Restart (after editing `.env` or `git pull`) | `Stop-ScheduledTask -TaskName "NSE Desk Agent"; Start-ScheduledTask -TaskName "NSE Desk Agent"` |
| Try the F&O desk by hand | `cd agent; python -m nse_agent fno NIFTY` |
| Remove it | `powershell -ExecutionPolicy Bypass -File agent\windows\install.ps1 -Uninstall` |

## Keeping the PC awake

The agent asks Windows not to idle-sleep while it runs, but it cannot work while the PC is asleep or off.
Keep the PC on and plugged in from 08:45 to 15:45 IST. On a laptop, closing the lid usually sleeps it: change
that in Control Panel, Power Options, "Choose what closing the lid does". Missed ticks are caught up
afterwards without re-sending emails already sent.

## Notes

- It runs only while you are logged in (the task starts at logon). Lock the screen instead of signing out.
- Run the agent on **one** machine at a time (Mac, Windows, GitHub Actions or a VM), or you get duplicate
  emails.
- If the installer says scripts are disabled, the `-ExecutionPolicy Bypass` part of the command fixes it.
  If Python is not found, reopen PowerShell after installing it.
