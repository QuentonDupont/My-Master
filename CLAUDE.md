# CLAUDE.md — Pomelo Support Triage System

This file is loaded automatically at the start of every Claude Code session in this
repo. Read it fully before doing anything. If a request in a session conflicts with
the invariants below, stop and ask.

**Read `HANDOFF.md` too** — this file is the specification, that one is the state
of play: what is live in Jira, what is waiting on the board owner, and what was
agreed next.

---

## What this system is

A semi-autonomous triage assistant for a Technical PM at Pomelo Fashion. It watches
the PESD1 Jira board, researches incoming support tickets, and produces **proposals**
for the human to approve. On approval it comments on PESD1 and, when code work is
needed, clones the ticket into PRDT and assigns a developer.

The human is the approver for everything. The system never acts unattended in v1.

**Boards in scope:** `PESD1` (support intake) and `PRDT` (development) only.
Do not query, write to, or reason about any other Jira project.

---

## Invariants — never violate these

1. **No write to Jira without an approved proposal.** All writes go through
   `execute_proposal(proposal_id)`. There is no other write path. Do not add one,
   do not call the Jira write API directly from a worker, do not "just this once".
2. **No writes to Apollo or Henry. Ever.** Read-only integrations only, and only
   after they are explicitly added in a later phase. Right now: no integration at all.
3. **Never-touch categories** are escalated to the human with no comment and no
   clone, regardless of confidence: refunds, payments, pricing, customer PII,
   stock adjustments, account access/permissions.
4. **Never re-process a ticket** whose key is in the ledger unless its
   `content_hash` changed AND its status is back to "Waiting for Support".
5. **Never re-execute** a proposal whose ledger state is `EXECUTED`.
6. **Never infer a requester email from a name alone.** See requester rules below.
7. **No credentials in the repo.** Everything from `.env`, which is gitignored.
   Never log a token, never print one, never paste one into a comment or ticket.
8. **Every write is reversible.** If you add a write action, you add its undo in the
   same commit.
9. **Never delete a ticket.** Not a PESD1 ticket, not a PRDT clone, not one the
   system created itself and not one created by mistake. A clone that should not
   exist is unassigned, unlinked, and recorded as a **"Close as Won't Do"**
   recommendation for the human to action in Jira. `undo()` reverts everything
   else; closing is a human decision.

---

## Architecture

Four standing agents. Workers are ephemeral — spawned per unit of work, terminated on
completion. Concurrency, not specialisation.

| Agent | Standing | Workers | Owns |
|---|---|---|---|
| Jira Leader | 1 | 5 concurrent, one per ticket | Queue, ledger, batch assembly, execution |
| Historian | 1 | — (index + query layer) | Retrieval, SOP drafting |
| Slack Leader | 1 | 3 concurrent, one per thread | Mentions, status replies, requester updates |
| Chief of Staff | 1 | — | Morning brief, rule proposals, cost, error review |

Build order is Historian → Jira Leader → Chief of Staff → Slack Leader.
Do not start a later component before the earlier one meets its acceptance criteria.

---

## Repo layout

```
/config          .env.example, boards.yml, never_touch.yml, repos.yml
/corpus          export + index scripts, SQLite FTS db (gitignored)
/agents
  /historian     retrieval + SOP drafting
  /jira_leader   queue, workers, batch assembly
  /chief_of_staff
  /slack_leader  (phase 5 — do not create earlier)
/core
  ledger.py      SQLite state machine, the ONLY place ledger state changes
  proposals.py   proposal schema, validation, serialisation
  execute.py     execute_proposal() and undo() — the only Jira write path
  jira_client.py thin API wrapper, read and write split into two classes
/knowledge
  rules.md       human-approved routing and handling rules — injected into workers
  corrections.jsonl  append-only log of human edits to proposals
/review          generated approval batches (gitignored)
/tests
```

---

## Ledger schema (SQLite, `core/ledger.py`)

```sql
CREATE TABLE ledger (
  ticket_key      TEXT PRIMARY KEY,
  state           TEXT NOT NULL,   -- see state machine
  first_seen      TEXT NOT NULL,
  last_processed  TEXT NOT NULL,
  content_hash    TEXT NOT NULL,   -- sha256(summary + description + comment_count)
  proposal_id     TEXT,
  clone_key       TEXT,            -- PRDT key, null until executed
  comment_id      TEXT,            -- null until executed
  attempts        INTEGER DEFAULT 0,
  last_error      TEXT
);
```

State machine:

```
NEW -> CLAIMED -> PROPOSED -> APPROVED|CORRECTED|REJECTED -> EXECUTED
                      |
                      +-> ESCALATED     (waiting on human input)
                      +-> DUPLICATE     (linked to existing, no further action)
                                        ROLLED_BACK (after undo)
```

---

## Proposal schema (`core/proposals.py`)

```json
{
  "proposal_id": "p_0142",
  "ticket": "PESD1-11274",
  "ticket_url": "https://pomelofashion.atlassian.net/browse/PESD1-11274",
  "requirement_restated": "one sentence — what the user is actually asking for",
  "requester": { "email": "...", "source": "sheet|jira_field|unknown",
                 "confidence": "high|medium|unknown", "matched_row": {} },
  "classification": "ANSWERABLE|NEEDS_CODE|ESCALATE|DUPLICATE",
  "confidence": 0.82,
  "proposed_comment": "text that will be posted to PESD1 on approval",
  "evidence": [
    { "type": "jira|confluence|github|sop", "ref": "PESD1-10233",
      "why": "same stock-sync symptom, resolved by <name>" }
  ],
  "clone": {
    "target_project": "PRDT",
    "summary": "...",
    "description": "Ask / Now / Wanted, a facts table, Start here (links to\n                     the procedure and the precedent), Possibly already covered,\n                     the original request quoted verbatim, Unknowns",
    "assignee": "dev.name",
    "assignee_reason": "closed 7 of last 10 PRDT tickets with component=stock-sync",
    "assignee_alternates": ["dev.two", "dev.three"],
    "labels": [], "priority": "Medium", "link_type": "is cloned by"
  },
  "pesd1_transition": "In Development",
  "flags": []
}
```

`clone` is null when `classification` is not `NEEDS_CODE`.
`assignee_alternates` is required — always give the human a ranked shortlist, never a
single guess.

---

## Requester resolution rules

In order:

1. If the requester email exists on the PESD1 ticket (custom field) — use it,
   `source: jira_field`, `confidence: high`. **Check this first; it may make the
   Google Sheet join unnecessary.**
2. If the intake sheet has a column containing the Jira ticket key — exact join,
   `source: sheet`, `confidence: high`.
3. Otherwise: match on requester name + submission timestamp within a window of
   ticket creation + text similarity of the request body. All three must agree →
   `confidence: medium`, and `matched_row` must be populated so the human can eyeball it.
4. Anything weaker → `Requester: unknown (manual lookup needed)` and a flag on the
   proposal. Never guess.

---

## Worker loop (per ticket)

1. Read summary, description, comments, attachments, reporter.
2. **Sanity gate** — can I state the requirement in one sentence? No → `ESCALATED`, stop.
3. **Never-touch gate** — matches `config/never_touch.yml`? → `ESCALATED` + flag, stop.
4. **Duplicate check** — Historian: similar OPEN PESD1 tickets. Strong match →
   propose link-as-duplicate, `DUPLICATE`, stop. Never clone a duplicate.
5. **Retrieve** — Historian: 5 most similar resolved tickets, matching Confluence/SOP
   pages, GitHub commits/PRs referencing those keys.
6. **Requester lookup** — rules above.
7. **Classify** — `ANSWERABLE` or `NEEDS_CODE`.
8. **Draft proposal object** and return it. Write nothing to Jira.

## Execution order (on approval, `core/execute.py`)

post PESD1 comment → create PRDT clone → link the two → assign → transition PESD1.
Log each step. On failure mid-sequence, roll back that item only and mark `last_error`.

---

## The learning loop

The models do not learn from being corrected. Persistence is built, not assumed.

- `knowledge/corrections.jsonl` — appended on every human edit or rejection:
  `{ "proposal_id", "field", "was", "became", "reason", "ts" }`
- `knowledge/rules.md` — human-approved rules, injected into every worker's context.
  Only the human approves additions. Chief of Staff proposes them weekly by clustering
  `corrections.jsonl`.
- SOP library — approved novel resolutions become Confluence pages. Highest-weight
  source for the Historian, above raw ticket history.

---

## Code conventions

- Python 3.11+, standard library first. Add a dependency only when it removes real work.
- SQLite for ledger and corpus index. No server database.
- Every module runnable standalone from the CLI for testing.
- Structured logging to `logs/` as JSONL. No secrets in logs.
- Dry-run flag on anything that writes. `--dry-run` is the default; writing requires
  an explicit `--execute`.
