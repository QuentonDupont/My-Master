#!/bin/bash
# Keep the MCP server and its tunnel running across reboots.
#
#   ./tools/install_connector.sh          install and start both
#   ./tools/install_connector.sh remove   stop and uninstall both
#
# Two jobs, because they fail differently and you want to restart one without
# the other:
#
#   com.pomelo.mcp      the server, bound to 127.0.0.1 only
#   com.pomelo.tunnel   cloudflared, which is what puts it on the internet
#
# Stopping the tunnel alone takes the connector off the internet while leaving
# the server running for the panel and local use — that is the kill switch.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="gui/$(id -u)"
JOBS=(com.pomelo.mcp com.pomelo.tunnel)

if [ "${1:-install}" = "remove" ]; then
  for label in "${JOBS[@]}"; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$label.plist"
    echo "removed $label"
  done
  exit 0
fi

grep -qE '^MCP_AUTH_TOKEN=.+' "$ROOT/.env" 2>/dev/null || {
  echo "MCP_AUTH_TOKEN is not set in .env."
  echo "The tunnel would expose the server to the internet with no auth."
  exit 1; }

command -v cloudflared >/dev/null || {
  echo "cloudflared is not installed:  brew install cloudflared"; exit 1; }

CF="$(command -v cloudflared)"
if ! grep -q "$CF" "$ROOT/tools/com.pomelo.tunnel.plist"; then
  echo "note: the plist points at a different cloudflared than $CF"
  echo "      edit tools/com.pomelo.tunnel.plist if the tunnel fails to start"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"

# Anything already running by hand would hold the port.
pkill -f "tools.mcp_server" 2>/dev/null || true
pkill -f "cloudflared tunnel --url http://127.0.0.1:8766" 2>/dev/null || true
sleep 1

for label in "${JOBS[@]}"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  cp "$ROOT/tools/$label.plist" "$HOME/Library/LaunchAgents/$label.plist"
  launchctl bootstrap "$DOMAIN" "$HOME/Library/LaunchAgents/$label.plist"
  launchctl enable "$DOMAIN/$label"
  echo "installed $label"
done

echo
echo "waiting for the tunnel to pick a URL…"
for _ in $(seq 1 20); do
  sleep 1
  grep -qoE 'https://[a-z0-9-]+\.trycloudflare\.com' "$ROOT/logs/tunnel.out" 2>/dev/null && break
done

echo
"$ROOT/tools/tunnel_url.sh" || true
echo
echo "  stop everything : ./tools/install_connector.sh remove"
echo "  off the internet, server still up : launchctl bootout $DOMAIN/com.pomelo.tunnel"
