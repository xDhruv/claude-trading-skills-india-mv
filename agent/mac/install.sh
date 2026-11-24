#!/usr/bin/env bash
# Installs the NSE desk agent as a launchd job on macOS: starts at login, restarts if it dies, keeps the Mac
# awake while it runs. Safe to re-run (it replaces the job). Usage:
#   bash agent/mac/install.sh              install or update
#   bash agent/mac/install.sh --uninstall  stop and remove the job
set -euo pipefail

LABEL="com.xdhruv.nse-agent"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$HERE/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$AGENT_DIR/agent.log"
DOMAIN="gui/$(id -u)"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this installer is for macOS"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  say "Removed $LABEL. Your .env, logs and state are untouched."
  say "If you set a wake schedule, remove it with: sudo pmset repeat cancel"
  exit 0
fi

# 1. Python 3.10+ (the agent uses only the standard library, nothing to pip install)
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3.13 python3.12 python3.11 python3.10 python3; do
  p="$(command -v "$c" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$p"; break; fi
done
[ -n "$PY" ] || die "Python 3.10 or newer not found. Install it with: brew install python"
say "Using $("$PY" --version) at $PY"

# 2. Settings file. Stop the first time so you can fill it in.
if [ ! -f "$AGENT_DIR/.env" ]; then
  cp "$AGENT_DIR/.env.example" "$AGENT_DIR/.env"
  chmod 600 "$AGENT_DIR/.env"
  say ""
  say "Created $AGENT_DIR/.env. Open it, set SMTP_USER, SMTP_APP_PASSWORD, EMAIL_TO, CAPITAL and the"
  say "Angel One block if you have it, then run this script again."
  exit 0
fi
chmod 600 "$AGENT_DIR/.env"

# 3. Sanity checks before the job is installed
cd "$AGENT_DIR"
say "Running the self-check..."
"$PY" -m nse_agent check || die "the check failed: fix the items above, then run this script again"
say "Sending a test email..."
"$PY" -m nse_agent test-email || say "(test email not sent: check SMTP_USER, SMTP_APP_PASSWORD and EMAIL_TO in .env)"

# 4. launchd job. caffeinate -i stops idle sleep while the agent runs (it does not stop lid-closed sleep).
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$PY</string>
    <string>-m</string>
    <string>nse_agent</string>
    <string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$AGENT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TZ</key><string>Asia/Kolkata</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null || die "generated plist is invalid: $PLIST"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl kickstart -k "$DOMAIN/$LABEL"
sleep 3

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  say ""
  say "Installed and running: $LABEL"
else
  die "launchd did not start the job. See $LOG"
fi

cat <<EOF

Next steps
  Watch the log        tail -f "$LOG"
  Status               launchctl print $DOMAIN/$LABEL | head -20
  Stop and remove      bash "$HERE/install.sh" --uninstall
  Try the F&O desk now $PY -m nse_agent fno NIFTY      (run from $AGENT_DIR)

Keep the Mac awake and plugged in during market hours (09:00 to 15:45 IST), lid open. To wake it for the
pre-open brief (it can only wake from sleep, not from shut down):
  sudo pmset repeat wakeorpoweron MTWRF 08:40:00
Cancel it with: sudo pmset repeat cancel
Details: docs/nse-agent-mac.md
EOF
