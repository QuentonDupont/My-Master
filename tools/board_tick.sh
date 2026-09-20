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

PY=/usr/bin/python3
stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

echo "=== board tick $(stamp) ==="

echo "--- 1/3 sweep"
"$PY" -m agents.jira_leader.queue run 2>&1 | tail -20 || echo "sweep failed"

echo "--- 2/3 labels"
"$PY" -m agents.jira_leader.labels poll --apply 2>&1 | tail -20 || echo "labels failed"

echo "--- 3/3 mirror"
"$PY" -m core.status_mirror apply --execute 2>&1 | tail -20 || echo "mirror failed"

echo "=== done $(stamp) ==="
