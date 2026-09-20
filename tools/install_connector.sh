#!/bin/bash
# Keep the MCP server and its tunnel running across reboots.
#
#   ./tools/install_connector.sh          install and start both
#   ./tools/install_connector.sh remove   stop and uninstall both
#
# Two jobs, because they fail differently and you want to restart one without
# the other:
#
#   com.pomelo.mcp      the MCP server, bound to 127.0.0.1 only
#   com.pomelo.panel    the review page, bound to the tailnet address only
#
# What puts it on the internet is Tailscale Funnel, which is a Tailscale service
# rather than a process of ours — nothing to supervise, and the hostname never
# changes. This replaced a cloudflared quick tunnel that minted a new URL on
# every restart.
#
#   tailscale funnel --bg 8766     on
#   tailscale funnel --https=443 off
#
# Turning Funnel off is the kill switch: it takes the connector off the internet
# while leaving the server up for the panel and local use.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="gui/$(id -u)"
JOBS=(com.pomelo.mcp com.pomelo.panel)

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

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"

# Anything already running by hand would hold the port.
pkill -f "tools.mcp_server" 2>/dev/null || true
pkill -f "tools.panel" 2>/dev/null || true
sleep 1

for label in "${JOBS[@]}"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  cp "$ROOT/tools/$label.plist" "$HOME/Library/LaunchAgents/$label.plist"
  launchctl bootstrap "$DOMAIN" "$HOME/Library/LaunchAgents/$label.plist"
  launchctl enable "$DOMAIN/$label"
  echo "installed $label"
done


sleep 2
echo
"$ROOT/tools/tunnel_url.sh" || true
echo
echo "  panel  : http://$(tailscale ip -4 2>/dev/null | head -1):8765  (tailnet only)"
echo "  stop everything : ./tools/install_connector.sh remove"
echo "  off the internet, server still up : tailscale funnel --https=443 off"
