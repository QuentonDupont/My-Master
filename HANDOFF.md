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

## Standing instructions from the board owner

- Keep developer-facing text direct and precise. They should not have to read
  through a ticket to find the ask.
- Never delete a ticket — recommend "Close as Won't Do" instead (invariant 9).
- Every outbound message is approved by a human first.

## The review page

https://claude.ai/artifact/UBNzgPpL7W16vZVh4a8m2k — proposals,
close recommendations and board priorities. Seeded with
`python -m agents.jira_leader.mobile export` and the Artifact db tools.
