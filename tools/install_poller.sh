#!/bin/bash
# Install the Slack poller as a launchd agent. Run it AFTER SLACK_BOT_TOKEN is in
# .env — the poller exits 2 with a clear message otherwise, every 3 minutes.
#
#   ./tools/install_poller.sh          install and start
#   ./tools/install_poller.sh remove   stop and uninstall
#
# Nothing here posts to Slack. The poller reads mentions and writes proposals;
# replies are still approved by a human.
set -euo pipefail

LABEL="com.pomelo.slack-poller"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/tools/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [ "${1:-install}" = "remove" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$DEST"
  echo "removed $LABEL"
  exit 0
fi

if grep -qE '^SLACK_USER_TOKEN=xoxp-' "$ROOT/.env" 2>/dev/null; then
  echo "reading as you (SLACK_USER_TOKEN) — your channels and DMs are in scope"
elif grep -qE '^SLACK_BOT_TOKEN=xoxb-' "$ROOT/.env" 2>/dev/null; then
  echo "reading as the app (SLACK_BOT_TOKEN) — invited channels only, no DMs,"
  echo "and only mentions of the bot itself will be seen."
else
  echo "No usable Slack read token in .env."
  echo "  SLACK_USER_TOKEN=xoxp-...  reads as you (what you asked for), or"
  echo "  SLACK_BOT_TOKEN=xoxb-...   reads as the app"
  echo "See HANDOFF.md. Installing the job anyway would fail every 3 minutes."
  exit 1
fi

# Confirm the token actually works before handing the job to launchd.
echo "checking Slack access…"
( cd "$ROOT" && /usr/bin/python3 -m core.slack_client whoami )

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"

# launchd caches plists by label; bootout first so a re-install takes effect.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
cp "$SRC" "$DEST"
launchctl bootstrap "$DOMAIN" "$DEST"
launchctl enable "$DOMAIN/$LABEL"

echo
echo "installed $LABEL — sweeping every 3 minutes"
echo "  status : launchctl print $DOMAIN/$LABEL | head -20"
echo "  logs   : tail -f $ROOT/logs/slack_poller.jsonl"
echo "  cursor : python3 -m agents.slack_leader.poller state"
echo "  stop   : ./tools/install_poller.sh remove"
