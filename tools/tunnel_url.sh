#!/bin/bash
# The connector URL currently in force.
#
# A quick tunnel gets a new hostname every time cloudflared restarts, so this
# reads the newest one out of the log rather than remembering a stale one. Run
# it whenever the Claude app stops reaching the connector.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/logs/tunnel.out"

[ -f "$LOG" ] || { echo "no tunnel log yet — is com.pomelo.tunnel running?"; exit 1; }

URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | tail -1)"
[ -n "$URL" ] || { echo "no URL in $LOG yet; give cloudflared a few seconds"; exit 1; }

if curl -fsS -o /dev/null -w '' "$URL/health" 2>/dev/null; then
  STATE="reachable"
else
  STATE="NOT reachable — the tunnel may have restarted onto a new URL"
fi

echo "$URL"
echo "  $STATE"
echo
echo "Claude app -> Settings -> Connectors -> custom connector"
echo "  URL     $URL"
echo "  Header  Authorization: Bearer <MCP_AUTH_TOKEN from .env>"
echo
echo "  grep MCP_AUTH_TOKEN $ROOT/.env"
