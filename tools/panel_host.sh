#!/bin/bash
# Start the review panel on the tailnet address, whatever it is today.
#
# launchd cannot compute an argument, and the panel must not bind 0.0.0.0: it
# has no authentication, so it should be reachable from your own devices and
# nowhere else — not from café wifi, not from a shared office network. This
# resolves the Tailscale address at launch and binds only to that.
#
# If Tailscale is down there is no safe address to bind, so this waits rather
# than falling back to something wider. KeepAlive means launchd retries.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

TS=/usr/local/bin/tailscale
[ -x "$TS" ] || TS="$(command -v tailscale || true)"

for attempt in $(seq 1 30); do
  IP="$("$TS" ip -4 2>/dev/null | head -1)"
  case "$IP" in
    100.*) break ;;
  esac
  echo "waiting for tailscale (attempt $attempt)"
  sleep 4
  IP=""
done

if [ -z "${IP:-}" ]; then
  echo "tailscale has no address after 2 minutes; not binding anything"
  exit 1
fi

echo "binding the panel to $IP:8765"
exec /usr/bin/python3 -m tools.panel --host "$IP" --port 8765
