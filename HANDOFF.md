# Handoff — 18 Sep 2026

Read this with CLAUDE.md. It is the state of play, not the spec.

## Live in Jira

Five PESD1 tickets were triaged, approved by the board owner on the phone, and
executed. Verified by reading them back:

| PESD1 | PRDT | Assignee | Epic | Priority |
|---|---|---|---|---|
| 11271 NS locations | PRDT-11559 | Vishal Gaikwad | PRDT-10732 | High |
| 11274 unit cost | PRDT-11560 | Vishal Gaikwad | PRDT-10732 | Critical |
| 11275 IR number | PRDT-11564 | Vishal Gaikwad | PRDT-11563 | High |
| 11272 Buyers on Henry | PRDT-11565 | Unni Purushothaman | PRDT-11563 | High |
| 11278 cancel from PO | PRDT-11566 | Unni Purushothaman | PRDT-11563 | Critical |

`PRDT-11563 "PESD1 Support Escalations"` was created as the default epic.

Two PESD1 tickets were deliberately not touched: **10660** (says nothing) and
**11276** (pricing — never-touch).

### 23 Sep sweep — via the claude.ai Atlassian connector

Approved by the board owner in session, executed through the connector (not
`execute_proposal`, so none of this is in the local ledger — this table is the
record). Verified by reading back.

| PESD1 | Action | PRDT | Assignee |
|---|---|---|---|
| 11283 IR 3168727 bin | comment (283798) → To Do | — | manual NetSuite fix |
| 11284 RMA1424188 sync | comment (283799) → To Do | — | Wallop to re-push |
| 11281 duplicate RMAs | comment (283800) → To Do | — | already done 18 Sep |
| 11285 franchise PO cost | comment (283801) → To Do | PRDT-11596 (High) | Unni Purushothaman |
| 11287 MS133049 sizes | comment (283802) → To Do | PRDT-11597 (Critical) | Unni Purushothaman |

Both clones are under epic PRDT-11563, linked with Cloners.

Left for the human (never-touch, no comment): **11276** (pricing), **11289**
(perks credit + customer email — "perks credit" added to never_touch.yml),
**11279** (set 17 RMAs to Return Received — treated as refund-adjacent; owner
to confirm whether receiving a return triggers the refund).

Owner chose To Do for 11281 even though the work is complete; it can move to
Live once the requester confirms.

## Open, waiting on the board owner

1. **`SLACK_BOT_TOKEN` (xoxb-)** — the one thing preventing the Slack Leader from
   running unattended. https://api.slack.com/apps/A0BA4UVKA9M/oauth → Bot Token
   Scopes (`app_mentions:read`, `channels:history`, `groups:history`,
   `chat:write`, `users:read`, `users:read.email`) → Install to Workspace.
   The `xapp-` tokens already in .env cannot read or post.
2. **PRDT-11559 → "Close as Won't Do"** — it duplicates PRDT-11539 / PRDT-11536.
   Recorded in `review/recommendations.jsonl` and shown on the review page.
   The system never closes a ticket itself (invariant 9).
3. **BUSK board 377** — asked for, refused: outside `allowed_projects`. Proposed
   fix is a readable-vs-writable split so BUSK can be tracked but never written
   to. Needs the owner's yes before CLAUDE.md's scope rule changes.
4. **Confluence spaces** — PM is indexed (329 pages). PSD (Pomelo Service Desk),
   DO, PRA and POMQ are listed as candidates in `config/confluence.yml`.
5. **`ANTHROPIC_API_KEY`** — unset, so every worker runs the heuristic analyst.
   This is the biggest single quality lever left.

## Slack, next session

The claude.ai Slack connector is authenticated; its tools load on a fresh start.

- **Connector mode is built**: drafts are made locally, approved on the phone,
  and posted by the connector under the approver's own identity —
  `core.slack_execute.record_external_reply` records who posted and how.
  `python -m agents.slack_leader.mobile ready` lists what to send.
- The board owner reported an unanswered mention. Nothing is scheduled and
  nothing subscribes to Slack events: the leader is a command, not a daemon.
  Making it react needs the bot token plus either a launchd poller or a Socket
  Mode listener. (launchd cannot read scripts under ~/Desktop on this machine —
  install under ~/Library/Application Support.)
- **Requester updates** are the agreed next feature, after mentions. Undecided:
  whether an update goes to the requester in a DM or back to the channel.

## Standing instructions from the board owner

- Keep developer-facing text direct and precise. They should not have to read
  through a ticket to find the ask.
- Never delete a ticket — recommend "Close as Won't Do" instead (invariant 9).
- Every outbound message, Jira or Slack, is approved by a human first.

## The review page

https://claude.ai/artifact/UBNzgPpL7W16vZVh4a8m2k — proposals, Slack replies,
close recommendations and board priorities. Seeded with
`python -m agents.jira_leader.mobile export` and the Artifact db tools.
