# Shared alert + heartbeat helpers for the scheduled tools/*.sh jobs.
#
# Source this, don't run it:  . "$ROOT/tools/notify_on_failure.sh"
# ($ROOT must already be set — every caller computes it before sourcing.)
#
# alert_once <marker> <title> <message>
#   Fires a macOS notification via `osascript` — that's the mechanism, and the
#   only one available: PushNotification is a Claude Code session tool, and a
#   headless launchd job has no session to call it from. Fires once per marker,
#   then stays silent on every later call until clear_alert() runs for the same
#   marker — without that, a 5-minute cadence would renotify every 5 minutes
#   for as long as something stays broken.
#
# clear_alert <marker>
#   Removes the marker so the next failure alerts again. Call on success.
#
# heartbeat <name>
#   Writes now() to logs/.heartbeat_<name>. Lets a health check report "last
#   successful tick: Xm ago" — distinguishing "stopped running entirely" (no
#   heartbeat update) from "running but every step fails" (alert_once instead).

MARKER_DIR="$ROOT/logs/.markers"
mkdir -p "$MARKER_DIR"

alert_once() {
  local marker="$1" title="$2" message="$3"
  local marker_file="$MARKER_DIR/$marker"
  if [ -f "$marker_file" ]; then
    return 0
  fi
  touch "$marker_file"
  osascript -e "display notification \"$message\" with title \"$title\" sound name \"Basso\"" \
    >/dev/null 2>&1 || true
}

clear_alert() {
  rm -f "$MARKER_DIR/$1"
}

heartbeat() {
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$ROOT/logs/.heartbeat_$1"
}
