#!/bin/bash
# Install the corpus refresh as a launchd agent: every 2 hours, catch any
# PESD1/PRDT ticket updated in the last 3 days and reindex.
#
#   ./tools/install_corpus_refresh.sh          install and start
#   ./tools/install_corpus_refresh.sh remove   stop and uninstall
#
# Read-only against Jira. It never writes to a ticket, never posts a comment;
# it only refreshes what the Historian's retrieval reads from.
set -euo pipefail

LABEL="com.pomelo.corpus-refresh"
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
  echo "JIRA_API_TOKEN is not set in .env — the refresh would fail every run."
  exit 1
fi

echo "checking Jira access…"
( cd "$ROOT" && /usr/bin/python3 -m core.jira_client whoami >/dev/null \
  && echo "  ok" ) || { echo "  cannot reach Jira; not installing"; exit 1; }

echo "running one pass before handing it to launchd…"
if ! "$ROOT/tools/corpus_refresh.sh" >/dev/null 2>&1; then
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
echo "installed $LABEL — every 2 hours"
echo "  status : launchctl print $DOMAIN/$LABEL | head -20"
echo "  output : tail -f $ROOT/logs/corpus_refresh.out"
echo "  stop   : ./tools/install_corpus_refresh.sh remove"
