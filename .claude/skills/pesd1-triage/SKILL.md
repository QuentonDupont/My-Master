---
name: pesd1-triage
description: Use when working the PESD1/PRDT Jira triage farm in this repo — sweeping the board, checking a Slack @channel mention, drafting a requester reply, or deciding whether something can be resolved without a developer. Read CLAUDE.md and HANDOFF.md first; this skill is the accumulated operating lessons on top of them, not a replacement.
---

# PESD1 triage — operating lessons

This file exists because corrections and mistakes should compound into something
that does not have to be re-discovered next session. Read it, and add to it —
see "Adding to this file" at the bottom.

## Before touching Jira: the gates, in order

1. **Never-touch is about the money/PII/access, not the exact keyword.**
   `config/never_touch.yml` is a keyword list, and keyword lists have gaps —
   "store credit" was missing until 2026-09-21 even though it is plainly a
   customer monetary balance. When a ticket is *in spirit* a refund, payment,
   pricing, PII, stock, or access matter, treat it as never-touch even if no
   keyword fires, and go fix the keyword gap in the same pass.
2. **Henry and Apollo are read-only, permanently** (CLAUDE.md invariant 2). A
   ticket that needs an operational fix *inside* Henry or Apollo (unlock a
   picking list, correct a price label) gets a Jira comment saying a human is
   picking it up — never a promise that the system will do it, and never an
   attempt to do it.
3. **Ticket creation has no sanctioned automated path.** `execute_proposal()`
   comments on, clones from, links, assigns and transitions an *existing*
   PESD1 ticket — it does not create one. Raising a brand-new PESD1 or PRDT
   ticket is a deliberate, human-authorized exception each time (like the
   status-mirror exception already written into invariant 1), run by hand
   with `JiraWriteClient(execute=True)`, never folded into the autonomous
   loop. Say this plainly before doing it, and only do it once the human has
   actually said to.

## Slack

- **`slack_send_message_draft` creates a draft. `slack_send_message` sends.**
  Confirm which one is being called before calling it — the failure mode
  (posting live instead of drafting) is publicly visible and cannot be
  un-sent; there is no edit/delete on the connector.
- **A ticket key mentioned in a Slack message identifies the requester.**
  When a message names `PESD1-\d+` or `PRDT-\d+`, pull that issue's own
  comments (and its linked clone's comments) before drafting a reply. Often
  the work and the answer already exist in Jira and just never made it back
  to the thread that's asking about it — the gap is feedback, not effort.
- **`@channel` messages with no ticket key are unlogged asks.** Investigate
  whether it's something the system can actually resolve (root-cause it in
  the code, same as any PESD1 ticket would get) before assuming it needs a
  human. If it turns out to be never-touch (see above), say so and stop
  there rather than drafting a reply that implies a resolution.

## The learning loop is designed to be run, not just written to

`knowledge/corrections.jsonl` accumulates on every edit or rejection, but
nothing acts on it until someone runs
`python -m agents.chief_of_staff.rules --write`, which clusters it into
`review/rule_proposals_*.md` — still nothing-active until the human pastes
approved ones into `knowledge/rules.md`. This sat un-run for days at least
once (68 corrections, zero approved rules). Run it periodically as part of a
board sweep, surface the output, and be explicit that nothing in it is live
until approved — don't let it silently pile up again.

## Adding to this file

When a mistake or a correction in a session is the kind that would recur in a
*different* session (not something the personal cross-session memory already
covers), add a short bullet here rather than only in chat. Keep entries
imperative and dated only when the date matters to the lesson (e.g. "missing
until 2026-09-21" tells you the gap could still be there for anything raised
before that).
