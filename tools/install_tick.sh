#!/bin/bash
# Install the board tick as a launchd agent: sweep, read labels, mirror status,
# every five minutes.
#
#   ./tools/install_tick.sh          install and start
#   ./tools/install_tick.sh remove   stop and uninstall
#
# What this does unattended, and what it does not:
#
#   does      write proposals, record your triage-approved / triage-rejected
#             labels, and move a PESD1 parent to match its PRDT clone
#   does NOT  post a comment, create a clone, reply in Slack, or close anything
#
# Executing a proposal stays a command you run. Nothing here reaches a requester.
set -euo pipefail

LABEL="com.pomelo.board-tick"
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

if ! grep -qE '^JIRA_API_TOKEN=.+' "$ROOT/.env" 2>/dev/null; then
  echo "JIRA_API_TOKEN is not set in .env — the tick would fail every 5 minutes."
  exit 1
fi

echo "checking Jira access…"
( cd "$ROOT" && /usr/bin/python3 -m core.jira_client whoami >/dev/null \
  && echo "  ok" ) || { echo "  cannot reach Jira; not installing"; exit 1; }

echo "running one pass before handing it to launchd…"
if ! "$ROOT/tools/board_tick.sh" >/dev/null 2>&1; then
  echo "  the pass failed — fix that before scheduling it"
  exit 1
fi
echo "  ok"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"

# launchd caches by label; bootout first so a re-install takes effect.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
cp "$SRC" "$DEST"
launchctl bootstrap "$DOMAIN" "$DEST"
launchctl enable "$DOMAIN/$LABEL"

echo
echo "installed $LABEL — every 5 minutes"
echo "  status : launchctl print $DOMAIN/$LABEL | head -20"
echo "  output : tail -f $ROOT/logs/board_tick.out"
echo "  stop   : ./tools/install_tick.sh remove"
echo
echo "label a PESD1 ticket 'triage-approved' or 'triage-rejected' from anywhere;"
echo "it is picked up within five minutes. Executing stays manual:"
echo "  python3 -m agents.jira_leader.batch execute --execute"
