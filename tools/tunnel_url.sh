#!/bin/bash
# The connector URL. Permanent, via Tailscale Funnel.
#
# Funnel is a Tailscale service, not a process of ours, so there is nothing to
# keep alive and the hostname never changes — unlike the cloudflared quick
# tunnel this replaced, which minted a new URL on every restart.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="$(tailscale status --json 2>/dev/null \
  | python3 -c "import json,sys; print('https://'+(json.load(sys.stdin).get('Self') or {}).get('DNSName','').rstrip('.'))" 2>/dev/null)"

[ -n "${URL:-}" ] && [ "$URL" != "https://" ] || {
  echo "Tailscale is not up:  tailscale up"; exit 1; }

# Captured, not piped: `grep -q` exits on first match, the writer takes SIGPIPE
# and dies 141, and pipefail then reports a successful match as a failure.
SERVE="$(tailscale serve status 2>/dev/null)"
case "$SERVE" in
  *"Funnel on"*) ;;
  *) echo "$URL"
     echo "  Funnel is OFF — turn it back on with:  tailscale funnel --bg 8766"
     exit 1;;
esac

if curl -fsS -o /dev/null --max-time 20 "$URL/health" 2>/dev/null; then
  STATE="reachable"
else
  STATE="not answering — is com.pomelo.mcp running?"
fi

echo "$URL"
echo "  $STATE"
echo
echo "Claude app -> Settings -> Connectors -> custom connector"
echo "  URL     $URL"
echo "  Header  Authorization: Bearer <MCP_AUTH_TOKEN from .env>"
echo
echo "  grep MCP_AUTH_TOKEN $ROOT/.env"
