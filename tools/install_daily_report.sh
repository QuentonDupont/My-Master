#!/bin/bash
# Install (or remove) the 9am daily status email.
#
#   ./tools/install_daily_report.sh          install and load it
#   ./tools/install_daily_report.sh remove   unload and remove it
#   ./tools/install_daily_report.sh test     run it right now, --send, off-schedule
#
# Needs REPORT_EMAIL_FROM and REPORT_EMAIL_APP_PASSWORD in .env — see
# core/mailer.py for how to generate a Gmail App Password. Without them the
# job runs, prints the report to its log, and fails only the send step.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL=com.pomelo.daily_report
DOMAIN="gui/$(id -u)"

if [ "${1:-install}" = "remove" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
  echo "removed $LABEL"
  exit 0
fi

if [ "${1:-}" = "test" ]; then
  cd "$ROOT"
  PYTHONPATH="$ROOT" python3 -m agents.chief_of_staff.daily_report run --send
  exit 0
fi

grep -qE '^REPORT_EMAIL_FROM=.+' "$ROOT/.env" 2>/dev/null || {
  echo "REPORT_EMAIL_FROM is not set in .env -- the job will run daily but"
  echo "every send will fail until it is. See core/mailer.py for setup."
}

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
cp "$ROOT/tools/$LABEL.plist" "$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootstrap "$DOMAIN" "$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl enable "$DOMAIN/$LABEL"

echo "installed $LABEL -- fires daily at 09:00 (local time), only while this Mac is on"
echo "run it right now:  ./tools/install_daily_report.sh test"
echo "remove it:         ./tools/install_daily_report.sh remove"
