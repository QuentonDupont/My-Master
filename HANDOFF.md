# Handoff — 16 Oct 2026: PDP voucher iOS tickets (connector writes)

Created by hand at the board owner's explicit request, through the Atlassian
connector (not `execute_proposal`, so not in the ledger). Verified by reading back.
All assigned to Hitesh (hitesh.b, iOS), epic PRDT-1976, Department Technology,
labels `ios` + `pdp-voucher`. PRDT requires Department and Epic Link on create.

| PRDT | Item | Status |
|---|---|---|
| 11912 | remove the dash from the sticky view | In progress |
| 11913 | remove the space above the Pomelo tile (sticky view) | To Do |
| 11914 | voucher code after title in Best Price Card (min spend) | To Do |
| 11915 | grey background matching Free Delivery | Ready To Release |
| 11916 | replace (i) icon with > arrow | Ready To Release |
| 11917 | details sheet: code after the title | Ready To Release |
| 11918 | details sheet: reduce cell height | Ready To Release |
| 11919 | details sheet: remove top-right close button | Ready To Release |
| 11920 | details sheet: collapsed by default | Ready To Release |

"Done" was mapped to Ready To Release (PRDT has no "Done"; Live would claim it
shipped). Move to Live once released. Same day: PESD1 Waiting Support sweep found
all 11 already triaged by the farm; the Crashlytics clones for 11344/11345 and the
11323 comment are still unapproved.

# Handoff — 12 Oct 2026: flows refactor (branch `refactor/agentic-flows`)

Everything recurring is now single-purpose agents chained into flows, each
with a trigger (CLAUDE.md > Architecture). On the branch, not yet live:

* **Live machine still runs the old jobs** (`board-tick`, `corpus-refresh`,
  `daily_report`). To switch: merge, then `./tools/install_flows.sh` (retires
  them, installs `com.pomelo.flows`). `./tools/install_flows.sh rollback`
  restores them; their scripts still work against the refactored code.
* **LIVE since 12 Oct 2026** — `com.pomelo.flows` installed, old jobs retired.
* **Crashlytics fixed route merged into the triage flow** (12 Oct 2026, the
  board owner reconfirming on PESD1-11335): `fixed_route_gate` in
  `flows/triage.py`, config in `config/boards.yml` `fixed_routes`. Every
  `[Crashlytics]` alert clones verbatim to Rahul Ravindra under PRDT-11608,
  before duplicate detection runs — closing the gap that read PESD1-11337 as
  a duplicate of 11298. Housekeeping still owed: the PRDT-11609..11785 series
  is still PROPOSED/To Do and routine to approve.
* **Daily report goes to a file, not email** (board owner's choice, 12 Oct):
  `review/daily_reports/<date>.md`. `send_report` skips while no Gmail
  credentials are set; `tools/gmail_oauth_setup.py` turns emailing back on.
* **New flow: `retriage`** (12 Oct 2026) — re-run triage on one named ticket
  by hand, because a rule changed, not because the ticket did. Built after
  three one-off scripts corrected stored proposals that day (Crashlytics x2,
  six return tickets) with no proper path to do it. Human-triggered only:
  `make retriage TICKET=PESD1-x REASON="..."`. Needed one new ledger edge,
  `PROPOSED -> CLAIMED` (core/ledger.py), alongside the three that already
  existed for the same reason (REJECTED/ESCALATED/DUPLICATE -> CLAIMED).
* **New mechanism: `assignee_overrides`** (12 Oct 2026), distinct from
  `fixed_routes` — a known-correct developer for a ticket family, without
  skipping research or the drafted comment the way Crashlytics does. One
  entry live: new-NCG-item requests always go to Vishal Gaikwad. Checked a
  Superset/data-engineering route too (requests like PESD1-11332) and did
  **not** build one — `text ~ "Superset"` pulls in RMA-sync tickets and
  others with nothing to do with data engineering, and only one real
  precedent (11332) exists so far. Not enough signal to write a safe rule;
  said so rather than forcing one.
* **The retriage flow's first live use immediately found a second instance
  of the duplicate-detection bug it was meant to fix only for Crashlytics.**
  Retriaging PESD1-11333 with the new NCG assignee override live-flagged it
  as a duplicate of PESD1-11334 — a different NCG code, matched purely on
  near-identical templated wording. Both tickets were corrected directly
  (same NCG routing, not a duplicate). This is now fixed generally, not
  per-ticket-family: `Historian.duplicates()` drops a candidate whose
  record number, SKU code or crash signature differs from the query's
  (`distinguishing_ids`, `agents/historian/retrieval.py`), so wording alone
  can no longer outrank a named, differing record. SKILL.md's gates section
  has the general lesson for the next formulaic ticket type this misses.
* New behaviour: `rules` now actually runs weekly (Mon 08:30, proposals only);
  `confluence` refreshes weekly (Mon 07:00, read-only); research runs every
  15m instead of every 5m; mirror moves are journalled in `execution_log`.
* Merged with `main` (Digital Twin). The Twin reads 6 Slack channels, so on
  12 Oct the board owner chose to keep `core/slack_client.py` as a **read-only**
  client for it: no write client, an allowlist of read methods, and
  `tests/test_slack_read_only.py` guarding both. The Slack Leader stays removed.
* `make demo` claims nothing, and did before this branch: it reads the live
  `config/boards.yml` (open status "Waiting Support") against fixtures marked
  "Waiting for Support". The tests use fixture boards and are unaffected.

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

Fix briefs (researched from precedent, PRDT dev comments, Confluence and Slack,
now in `.claude/skills/pesd1-triage/playbooks.md`) were posted as comments:
PESD1-11283 (283803), PESD1-11284 (283804), PRDT-11596 (283805),
PRDT-11597 (283806). The two PESD1 ones were meant as internal notes, but JSM
comment types are not enabled here, so they are **requester-visible**.

Security: the Superset MCP bearer token has been pasted in plain text in
several Slack DMs. It should be rotated and kept in Vault only.

Left for the human (never-touch, no comment): **11276** (pricing), **11289**
(perks credit + customer email — "perks credit" added to never_touch.yml),
**11279** (set 17 RMAs to Return Received — treated as refund-adjacent; owner
to confirm whether receiving a return triggers the refund).

Owner chose To Do for 11281 even though the work is complete; it can move to
Live once the requester confirms.

## Open, waiting on the board owner

1. **PRDT-11559 → "Close as Won't Do"** — it duplicates PRDT-11539 / PRDT-11536.
   Recorded in `review/recommendations.jsonl` and shown on the review page.
   The system never closes a ticket itself (invariant 9).
2. **BUSK board 377** — asked for, refused: outside `allowed_projects`. Proposed
   fix is a readable-vs-writable split so BUSK can be tracked but never written
   to. Needs the owner's yes before CLAUDE.md's scope rule changes.
3. **Confluence spaces** — PM is indexed (329 pages). PSD (Pomelo Service Desk),
   DO, PRA and POMQ are listed as candidates in `config/confluence.yml`.
4. **`ANTHROPIC_API_KEY`** — unset, so every worker runs the heuristic analyst.
   This is the biggest single quality lever left.

## Digital Twin — 24 Sep

The board owner uploaded the staff "digital twin" starter (v3) and asked what
we had. The triage Chief of Staff covers only the ticket side, so
`agents/digital_twin/` was built as a separate, read-and-draft-only team that
reports to him directly (same footing as `marketing_onsite`). Spec copied to
`agents/digital_twin/spec/`.

Built and tested offline: Work Profile + Task List (`twin/`, gitignored),
source adapters (pasted snapshots, triage ledger, Slack, Gmail, Calendar,
Drive — the last four off until enabled and tested), per-source cursors,
morning/EOD updates, meeting prep, drafts with sent-proof, live-alert pilot
with the bridge intake queue. `core/google_client.py` is read-only.

Waiting on the owner:
1. Run `make twin-setup` and answer the profile questions (or skip them).
2. Decide which Slack channels and Drive files go in `config/twin.yml` —
   empty lists read nothing, on purpose.
3. Google: an OAuth client with the three read-only scopes, values in `.env`
   (`GOOGLE_*`, see `.env.example`). Until then Gmail/Calendar/Drive stay
   "not enabled" and the pasted-snapshot route works.
4. Whether alerts should run at all (`alerts resume`), and on which sources.

Not built: the push bridge itself (Socket Mode / Pub/Sub / Drive watch) — the
queue it would feed exists (`bridge.py`). A model-backed drafter and signal
classifier would slot into `drafts.compose` and `items.signals`; both are
keyword heuristics today.

## Standing instructions from the board owner

- Keep developer-facing text direct and precise. They should not have to read
  through a ticket to find the ask.
- Never delete a ticket — recommend "Close as Won't Do" instead (invariant 9).
- Every outbound message is approved by a human first.

## The review page

https://claude.ai/artifact/UBNzgPpL7W16vZVh4a8m2k — proposals,
close recommendations and board priorities. Seeded with
`python -m agents.jira_leader.mobile export` and the Artifact db tools.

## Connector writes outside the ledger (undo record)

- **16 Oct 2026, PESD1-11347**, approved in chat by the board owner ("approved, use the api label epic"):
  created PRDT-11802 (Story, Critical, assignee Daniel Goncalves, label `api`,
  epic PRDT-10842, Department = Technology); fix-brief comment 284556 on
  PRDT-11802; requester comment 284557 on PESD1-11347; Cloners link
  PRDT-11802 clones PESD1-11347; PESD1-11347 moved to To Do via transition
  11 "Submit to Dev". To undo: close PRDT-11802 as Won't Do (never delete),
  move PESD1-11347 back to Waiting Support, edit comment 284557.

- **16 Oct 2026, Pomelo Intelligence Hub (Henry Partner Portal)**, approved in chat ("yes to all please create"):
  created BUSK-2851 (Epic, feature, assignee Quenton) with the mockup V6.2 HTML
  attached; PRDT-11804 (Epic, container, relates to BUSK-2851 because PRDT
  requires an Epic Link on every Story and cannot take a team-managed BUSK epic);
  29 Stories PRDT-11805, PRDT-11810 to PRDT-11837 under PRDT-11804; 64 Sub-tasks
  PRDT-11806 to PRDT-11809 and PRDT-11838 to PRDT-11897. Unni on Phase 1 and 2,
  Quenton on [PM] decision sub-tasks, Phase 3 / cross-cutting / needs-spec
  unassigned. To undo: close all as Won't Do (never delete), newest first.
