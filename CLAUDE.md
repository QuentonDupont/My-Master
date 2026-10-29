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

   **One exception, added 20 Sep 2026 by the board owner: status mirroring**
   (`core/status_mirror.py`). When a PRDT clone moves, its PESD1 parent is moved
   to match, so the requester sees progress on the ticket they are watching.
   There is nothing to propose — the decision was made when the clone was
   approved, and this is bookkeeping after it. The exception is exactly this
   wide and no wider:
   - transitions only, never a comment, clone, link or assignment
   - the PESD1 parent only, never the clone, and only to the status implied by
     its PRDT clone: the one recorded against it in the ledger, or (widened
     27 Oct 2026 by the board owner: "make the status changes according to what
     the developer has updated on their PRDT tickets … this should be an
     automated process") one linked to an open PESD1 ticket in Jira as a clone
     (Cloners link). A parent with several clones moves only when they all
     imply the same status; otherwise it is reported
   - forward along the declared order only; a parent is never moved backwards, and a
     Blocked parent is a person's hold — only a clone that has started work (In progress or
     later) lifts it, never a clone still in To Do (29 Oct 2026)
   - never into a closing status — invariant 9 still holds, so a clone closed as
     Won't Do is reported for a human, not mirrored
   - a transition the workflow does not offer is reported, never forced
   Everything else still goes through `execute_proposal`.
2. **No writes to Apollo or Henry. Ever.** Read-only integrations only, and only
   after they are explicitly added in a later phase.

   **One exception, added 21 Sep 2026 by the board owner: the Marketing &
   Onsite team** (`agents/marketing_onsite/`) sets up content in Apollo for web
   and app. Henry remains untouched, permanently and with no exception. The
   Apollo exception is exactly this wide and no wider:
   - content surfaces only — the ones declared in
     `agents/marketing_onsite/surfaces.py`. A surface not in that table is not
     touched, and the code raises rather than guessing.
   - never orders, customers, refunds, credit, pricing or stock. Those stay
     never-touch under invariant 3 whoever is asking.
   - **every change is staged inactive.** The board owner's words, 21 Sep 2026:
     *"all changes need to inactive, if not possible then please continue with
     the change."* So each surface declares how a change is parked — a
     visibility flag, a future-dated window, or every market unticked. Where a
     surface can do none of those, the change still proceeds, and the plan is
     marked `stages_live` and reported to him first, before anything else.
   - a worker drafts; it does not apply. Applying a plan to production is a
     separate human-gated step.
   - the team reports to the board owner directly, not through the Jira Leader
     or the Chief of Staff.
   **A third standing team, added 24 Sep 2026 by the board owner: the Digital
   Twin** (`agents/digital_twin/`) — his personal Chief of Staff, built from the
   staff starter file in `agents/digital_twin/spec/`. It is **read and draft
   only**: it reads the sources he has allowed in `config/twin.yml` (pasted
   snapshots, the triage ledger, Slack, Gmail, Calendar, Drive), keeps his Work
   Profile and Task List under `twin/` (gitignored), writes morning and
   end-of-day updates, and drafts messages in his voice. It never sends: the
   package has no write client and `tests/test_digital_twin.py` asserts it
   imports none; `core/google_client.py` has no method that writes. He sends a
   draft himself, from the app, and the twin records the proof. It reports to
   him directly. Turning a source on in config is not the same as it working —
   only a passed `lead test <source>` marks it "working" on the profile.

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

Many single-purpose agents, chained into flows, each flow started by a trigger.
No standing "department" agent owns more than one action. (Until 12 Oct 2026
this was four standing agents — Jira Leader, Historian, Chief of Staff, Slack
Leader — each owning many actions and chained by shell scripts.)

- **Agent** (`core/flow.py`) — one action, a function `run(ctx)`, and the one
  side-effect class it may have: `read`, `local` (ledger, review/, knowledge/,
  logs/), `jira_write` or `email`.
- **Flow** — an ordered chain of agents, or a `ForEach` fanning a sub-flow over
  items (the per-ticket triage, 5 at a time). Each step either stops the flow on
  failure or records it and continues. An agent ends a flow early, successfully,
  with `ctx.stop(reason)` — the gates do this.
- **Trigger** — `Every(seconds)`, `Daily(at, weekdays)`, `On(event)`, `Human()`.
- **Events** — every ledger state change writes `ticket.<state>` to the
  `events` table in the same transaction; `content_task.added` is emitted by
  Marketing & Onsite intake. A flow's event cursor starts at the journal's end.
- **Scheduler** (`core/scheduler.py`) — launchd runs `python -m flows tick`
  every minute (`com.pomelo.flows`). Each due flow starts in its own process
  under a per-flow lock; every run is recorded in `flow_runs`; a failing
  scheduled flow raises one macOS alert until it succeeds again.

The runtime enforces invariant 1 when a flow is defined, not when it runs:
only `execute_proposal` and `mirror_parent_status` may declare `jira_write`, and
a flow containing `execute_proposal` cannot be given any trigger but `Human()`.
Scheduled runs are dry-run unless the flow is `live_when_scheduled` (decisions,
mirror, daily-report); manual runs are dry-run unless `--execute`.

| Flow | Trigger | Agents |
|---|---|---|
| intake | every 5m | choose_analyst → poll_intake → reclaim_stale_claims → claim_tickets → triage ×5 → summarise_intake |
| triage (sub-flow) | per claimed ticket | read_ticket → sanity_gate → never_touch_gate → fixed_route_gate → research → resolve_requester → duplicate_gate → classify → assess_risk → plan_clone → draft_proposal |
| decisions | every 5m, on `ticket.proposed` | read_decision_labels → apply_decisions → report_decisions |
| execute | **human confirm only** | select_approved → execute_proposal (each) → summarise_execution |
| mirror | every 5m, on `ticket.executed` | find_drift → mirror_parent_status → report_mirror |
| research | every 15m | open_research → fetch_open_work → plan_tickets → build_outbox → write_review → flag_failed_tickets |
| corpus | every 2h | export_pesd1 → export_prdt → build_index |
| confluence | Mon 07:00 | export_confluence → build_index |
| content | on `content_task.added`, hourly | claim_new_tasks → draft_plan ×3 → summarise_content |
| escalation | human (an answer) | check_escalation → rewrite_proposal → record_correction → requeue_ticket |
| daily-report | 09:00 | check_health → build_brief → assemble_report → render_report → save_report → send_report (only if Gmail is configured) |
| rules | Mon 08:30 | cluster_corrections → write_rule_proposals |
| housekeeping | hourly | rotate_logs → prune_run_history |
| retriage | human (a rule changed) | load_for_retriage → claim_for_retriage → run_triage → record_retriage_correction |

`python -m flows list | show <flow> | run <flow> [--execute] [--confirm] | tick |
status | runs`. The old module CLIs (`queue run`, `labels poll`, `batch execute`,
`reentry answer`, `lead run`, `work_agent`) still work: each is a thin entry
point that runs its flow, so there is one code path per action. The packages
under `agents/` are libraries the agents call; they orchestrate nothing.

Adding an action: write one agent (one effect, one docstring line), put it in
the flow it belongs to, and give a new flow a trigger. A flow without a trigger
is a sub-flow. Never widen an agent to do a second thing — add a second agent.

---

## Repo layout

```
/config          .env.example, boards.yml, never_touch.yml, repos.yml
/corpus          export + index scripts, SQLite FTS db (gitignored)
/flows           one module per flow: its agents and its Flow (+ the CLI)
/agents          libraries the flow agents call — no orchestration
  /historian       retrieval + SOP drafting
  /jira_leader     gates, analysis, clone brief, labels, batch, re-entry, research
  /chief_of_staff  brief, health, rules, daily report
  /marketing_onsite content tasks, surfaces, SOPs, change-plan drafting
  /digital_twin    personal Chief of Staff — read and draft only, reports to owner
/core
  flow.py        agents, flows, triggers — the runtime and its guardrails
  scheduler.py   the tick: what is due, locks, run history, alerts
  events.py      the event journal (written by ledger.py, read by flows)
  ledger.py      SQLite state machine, the ONLY place ledger state changes
  proposals.py   proposal schema, validation, serialisation
  execute.py     execute_proposal() and undo() — the only Jira write path
  status_mirror.py the status-mirroring exception
  jira_client.py thin API wrapper, read and write split into two classes
/knowledge
  rules.md       human-approved routing and handling rules — injected into workers
  corrections.jsonl  append-only log of human edits to proposals
/review          generated approval batches (gitignored)
/twin            the Digital Twin's records: profile, tasks, drafts, cursors (gitignored)
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

Each step is its own agent in the triage flow (`flows/triage.py`).

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
