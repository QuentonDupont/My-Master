#!/bin/bash
# One pass of the board, for launchd to run on an interval.
#
#   ./tools/board_tick.sh          run it once, here
#
# Three steps, none of which writes anything outward to a requester:
#
#   1. queue run                    poll Waiting Support, write proposals
#   2. labels poll --apply          record triage-approved / triage-rejected
#   3. status_mirror apply          move PESD1 parents to match their PRDT clone
#
# Step 3 is the one sanctioned Jira write (CLAUDE.md invariant 1, status
# mirroring). Steps 1 and 2 touch nothing but the local ledger and proposal
# store. Executing a proposal — the comment, the clone, the reply that reaches
# a person — is deliberately NOT here and stays a command you run.
#
# Each step is allowed to fail without stopping the others: a Jira outage during
# the sweep should not stop your labels being recorded.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
. "$ROOT/tools/notify_on_failure.sh"

PY=/usr/bin/python3
stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

echo "=== board tick $(stamp) ==="

failed=0

echo "--- 1/3 sweep"
if "$PY" -m agents.jira_leader.queue run 2>&1 | tail -20; then
  clear_alert board_tick_sweep
else
  echo "sweep failed"
  failed=1
  alert_once board_tick_sweep "My-Master: sweep failing" \
    "queue run has been failing — check logs/board_tick.err"
fi

echo "--- 2/3 labels"
if "$PY" -m agents.jira_leader.labels poll --apply 2>&1 | tail -20; then
  clear_alert board_tick_labels
else
  echo "labels failed"
  failed=1
  alert_once board_tick_labels "My-Master: labels failing" \
    "labels poll has been failing — check logs/board_tick.err"
fi

echo "--- 3/3 mirror"
if "$PY" -m core.status_mirror apply --execute 2>&1 | tail -20; then
  clear_alert board_tick_mirror
else
  echo "mirror failed"
  failed=1
  alert_once board_tick_mirror "My-Master: status mirror failing" \
    "status_mirror apply has been failing — check logs/board_tick.err"
fi

if [ "$failed" -eq 0 ]; then
  heartbeat board_tick
fi

# Housekeeping: a no-op unless a log has actually crossed the size threshold,
# so this never delays or blocks the sweep above it. Never allowed to affect
# $failed — log rotation failing is not a reason to skip the next tick.
"$ROOT/tools/rotate_logs.sh" 2>&1 || echo "log rotation failed (non-fatal)"

echo "=== done $(stamp) ==="
