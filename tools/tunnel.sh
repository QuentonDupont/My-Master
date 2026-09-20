#!/bin/bash
# Put the MCP server on a public URL, so the Claude app can reach it.
#
#   ./tools/tunnel.sh          quick tunnel: a fresh trycloudflare.com URL
#   ./tools/tunnel.sh named    a stable URL on your own Cloudflare domain
#
# Anthropic call a connector from their servers, so it has to be publicly
# reachable — a private network like Tailscale will not do. What makes that safe
# is MCP_AUTH_TOKEN: every request needs it as a bearer token, and the server
# refuses to start without one.
#
# Quick vs named
# --------------
# A quick tunnel needs no Cloudflare account and no domain, but its URL changes
# every restart, so the connector has to be re-pointed each time. Fine for
# trying it; annoying to live with.
#
# A named tunnel keeps one URL. It needs a Cloudflare account with a domain, and
# `cloudflared tunnel login` opens a browser for you to authorise — that part is
# yours, not something this script can do for you.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8766}"
NAME="${NAME:-pomelo-triage}"
MODE="${1:-quick}"

command -v cloudflared >/dev/null || {
  echo "cloudflared is not installed:  brew install cloudflared"; exit 1; }

grep -qE '^MCP_AUTH_TOKEN=.+' "$ROOT/.env" 2>/dev/null || {
  echo "MCP_AUTH_TOKEN is not set in .env."
  echo "The tunnel would expose the server to the internet with no auth."
  echo "  python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
  exit 1; }

if ! curl -fsS -o /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null; then
  echo "nothing answering on 127.0.0.1:$PORT — start it first:"
  echo "  make mcp"
  exit 1
fi
echo "mcp server is up on $PORT"

if [ "$MODE" = "named" ]; then
  if ! cloudflared tunnel list >/dev/null 2>&1; then
    echo
    echo "Not logged in to Cloudflare. Run this yourself — it opens a browser:"
    echo "  cloudflared tunnel login"
    echo "then re-run:  ./tools/tunnel.sh named"
    exit 1
  fi
  cloudflared tunnel list | grep -q " $NAME " || cloudflared tunnel create "$NAME"
  echo "routing: set a CNAME for the hostname you want to $NAME.cfargotunnel.com"
  echo "  cloudflared tunnel route dns $NAME triage.yourdomain.com"
  exec cloudflared tunnel run --url "http://127.0.0.1:$PORT" "$NAME"
fi

echo "starting a quick tunnel — the URL changes every restart"
echo "the https://...trycloudflare.com line below is what goes in the Claude app"
echo
exec cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate
