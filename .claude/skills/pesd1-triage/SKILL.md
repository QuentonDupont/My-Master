---
name: pesd1-triage
description: Use when working the PESD1/PRDT Jira triage farm in this repo — sweeping the board, researching how to fix a PESD1 ticket, writing fix details onto a ticket or PRDT clone, checking a Slack @channel mention, drafting a requester reply, or deciding whether something can be resolved without a developer. Read CLAUDE.md and HANDOFF.md first; this skill is the accumulated operating lessons on top of them, not a replacement.
---

# PESD1 triage — operating lessons

This file exists because corrections and mistakes should compound into something
that does not have to be re-discovered next session. Read it, and add to it —
see "Adding to this file" at the bottom.

## The flow for every PESD1 ticket: research the fix, then write it down

A comment that only routes a ticket ("we have raised a dev ticket") is not
enough. The board owner's standing instruction (23 Sep 2026): every ticket we
touch carries the **fix**: what to do, where, who does it, and how to check
it worked. Work out the fix from what the organisation already knows before
drafting anything. Run this after the gates below pass.

1. **Match the ticket type in `playbooks.md`** (next to this file). A matching
   recipe is the starting point. Still check its precedents are current.
2. **Historian: Jira precedent.** Find 3–5 resolved PESD1 tickets of the same
   type (JQL `text ~` / `summary ~`, status Live) and read **every comment**,
   not the summary: who fixed it, the NetSuite/Henry/Apollo record they
   linked, and the words "Completed", "synced", "deleted". Most operational
   tickets are resolved in comments by Ops tech, Quenton, Wallop or Vishal,
   and the images carry the rest. Say when the only evidence is an image.
3. **PRDT dev handover.** Look up the linked PRDT clone and any PRDT ticket
   for the same component. Developers' comments there (Unni for Henry,
   Vishal for NetSuite, Wallop for Apollo) are the dev handover: root cause,
   the table, field or script, and whether the issue will come back. There
   is no single handover document; the 2022–23 "Handover" pages in PM are
   historical.
4. **Confluence SOPs.** Search OP (operations procedures), PSD, PM ("TechOps
   SOP", "Henry Revamp project"), NEON (inventory sync) and MUL. A written
   procedure beats inferring one from how a ticket happened to be closed.
5. **Slack.** Search the record numbers (RMA, TO, IR, PO, style). Fixes are
   often agreed in a thread and never written back to Jira.
6. **Platform check-up, read-only, where a connection exists.** Confirm the
   current state before promising a fix: is the RMA still unsynced, is the
   bin still empty. See "Platform connections" in `playbooks.md` for what is
   reachable from which session. Never write to Apollo or Henry
   (invariant 2). If nothing is reachable, say "not verified" on the
   ticket. Don't imply that it was.
7. **Write the fix down, in two places:**
   - **PESD1 comment (requester-facing):** what will be done, by whom, and
     what they will see when it is done. Plain language. Do not say it is
     done until someone has confirmed it.
   - **Fix brief (for whoever does the work):** a comment on the PRDT
     clone. With no clone, a comment on the PESD1 ticket, **which the
     requester can see**: this site has no JSM internal notes
     (`jsmCommentType: internalNote` falls back to a public comment,
     learned 23 Sep 2026). Write it so a requester reading it is fine, and
     say it is requester-visible when asking for approval. Include:
     **Fix** (numbered steps, with record paths/links) · **Who** ·
     **Verify** (the exact check that proves it) · **Root cause** (evidence
     vs inference, labelled) · **Precedents** (keys with who fixed them and
     when) · **Recurs?** (and the PRDT ticket that would stop it).
8. **Feed the playbook.** New ticket type, or a better recipe than the one on
   file → update `playbooks.md` in the same session and commit it. Wrong
   recipe (a human corrected it) → fix the entry and note why. This is how
   the next session starts from knowledge instead of re-investigating.

All writes in step 7 still go through the human's approval first
(invariant 1). The internal note is a write like any other.

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

## Before drafting a comment: read the whole thread

- **Check whether the work is already done.** PESD1-11281 had both deletions
  confirmed in comments by Vishal and Wallop, and a later comment still asked
  the requester for identifiers that were in the description. Read every
  comment and the description fields (Steps to reproduce, Expected Outcome)
  before asking a requester for anything.
- **Writes made through the Atlassian connector skip the ledger.** Until the
  connector is wired into `execute_proposal`, record each approved connector
  write (comment ids, clone keys) in HANDOFF.md so there is a record to undo.

## Adding to this file

When a mistake or a correction in a session is the kind that would recur in a
*different* session (not something the personal cross-session memory already
covers), add a short bullet here rather than only in chat. Keep entries
imperative and dated only when the date matters to the lesson (e.g. "missing
until 2026-09-21" tells you the gap could still be there for anything raised
before that).
