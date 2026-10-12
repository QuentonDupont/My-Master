#!/bin/bash
# Switch scheduling over to the flow scheduler, or back.
#
#   ./tools/install_flows.sh            install com.pomelo.flows, retire the old jobs
#   ./tools/install_flows.sh rollback   remove com.pomelo.flows, restore the old jobs
#   ./tools/install_flows.sh remove     remove com.pomelo.flows only
#
# com.pomelo.flows runs `python -m flows tick` every minute and replaces:
#
#   com.pomelo.board-tick       -> intake, decisions, mirror, research flows
#   com.pomelo.corpus-refresh   -> corpus flow (and a new weekly confluence flow)
#   com.pomelo.daily_report     -> daily-report flow (plus a new weekly rules flow)
#
# com.pomelo.mcp (the phone connector) is a server, not a schedule; it is left alone.
#
# What runs unattended is unchanged: proposals, recorded label decisions, the
# status mirror, research drafts, the corpus, the daily email. Executing a
# proposal stays a human confirm (`python -m flows run execute --confirm --execute`).
set -euo pipefail

LABEL="com.pomelo.flows"
OLD="com.pomelo.board-tick com.pomelo.corpus-refresh com.pomelo.daily_report"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

unload() { launchctl bootout "$DOMAIN/$1" 2>/dev/null || true; rm -f "$AGENTS/$1.plist"; }
load() {
  # Explicit returns: `set -e` does not apply inside a function called from `if`.
  sed -e "s#/Users/quenton-d/My-Master#$ROOT#g" "$ROOT/tools/$1.plist" > "$AGENTS/$1.plist" || return 1
  launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$AGENTS/$1.plist" || return 1
  launchctl enable "$DOMAIN/$1" || return 1
}

case "${1:-install}" in
  remove)
    unload "$LABEL"; echo "removed $LABEL"; exit 0 ;;
  rollback)
    unload "$LABEL"
    failed=0
    for j in $OLD; do
      [ -f "$ROOT/tools/$j.plist" ] || continue
      if load "$j"; then echo "restored $j"; else echo "FAILED to restore $j"; failed=1; fi
    done
    [ "$failed" -eq 0 ] && echo "rolled back to the per-job schedules" \
      || echo "rollback incomplete: check launchctl list | grep pomelo"
    exit "$failed" ;;
  install) ;;
  *) echo "usage: $0 [install|rollback|remove]"; exit 2 ;;
esac

case "$ROOT" in "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
  echo "launchd cannot read a repo under Desktop/Documents/Downloads (macOS TCC)."; exit 1 ;;
esac

if ! grep -qE '^JIRA_API_TOKEN=["'"'"']?[^"'"'"'[:space:]]' "$ROOT/.env" 2>/dev/null; then
  echo "JIRA_API_TOKEN is not set in .env — every Jira flow would fail."; exit 1
fi

echo "checking the flows load…"
( cd "$ROOT" && /usr/bin/python3 -m flows list >/dev/null ) || { echo "  flows do not import; not installing"; exit 1; }
echo "  ok"

echo "checking Jira access…"
( cd "$ROOT" && /usr/bin/python3 -m core.jira_client whoami >/dev/null && echo "  ok" ) \
  || { echo "  cannot reach Jira; not installing"; exit 1; }

mkdir -p "$AGENTS" "$ROOT/logs/flows"
# New job first: if it cannot load, the old jobs keep running and nothing is
# left unscheduled. A minute of overlap is harmless — ledger claims are atomic
# and every flow holds its own lock.
if ! load "$LABEL"; then
  echo "could not load $LABEL; the old jobs are untouched"; exit 1
fi
for j in $OLD; do unload "$j"; done
echo "retired: $OLD"

echo
echo "installed $LABEL — a tick every minute"
echo "  flows   : python3 -m flows list"
echo "  status  : python3 -m flows status"
echo "  history : python3 -m flows runs [flow]"
echo "  output  : tail -f $ROOT/logs/flows/<flow>.out"
echo "  undo    : ./tools/install_flows.sh rollback"
