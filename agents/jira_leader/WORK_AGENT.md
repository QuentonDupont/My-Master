# PESD1 work agent

`make work-agent` reads board 381 tickets in Waiting Support, Blocked and Waiting for customer. It reads all comments and linked PRDT comments, searches the existing corpus, and prepares a review at `review/work_agent/index.html`.

The existing five-minute board tick runs the work agent after the intake sweep, approval-label check and status mirror. Unchanged issue/sheet/rule/corpus inputs reuse cached research. Errors are recorded per ticket. The research pass does not modify the execution ledger.

## Intake source

Export the authorized Google Sheet tab as CSV to `corpus/raw/pesd1_requesters.csv`. This is the configured live source; the four-row sample `intake_sheet.csv` is not live data. The source sheet is https://docs.google.com/spreadsheets/d/16wkf3rnERCxEV_ri-9bf4euIWjViVcoFq3LJ2yrgRSw/edit?gid=1275977271.

Requester resolution checks the configured Jira field first, then exact ticket-key rows, then name + time + request similarity. Email comes from the email column; conflicting exact or fuzzy matches remain unknown. Exact joins must use a ticket/Jira column, never a ticket mentioned casually in the request body. Inspect matching with `python3 -m core.requester PESD1-123 --sheet corpus/raw/pesd1_requesters.csv`.

`customfield_10304` was verified as Jira's text field named Requester Email. New ordinary triage proposals can include an email update only when the field is empty and the sheet match is exact. The update executes through `execute_proposal()` after proposal approval. It checks for concurrent changes before posting anything. Execution and undo are journalled; undo does not overwrite a later email edit. Existing proposals and already-executed tickets are not silently rewritten.

## Fix plans and communication

Each ticket JSON records the requirement, current status, linked development comments, evidence, candidate procedure, investigation/verification steps, requester match and blockers. A candidate procedure is not a confirmed diagnosis or an executed fix. With no documented evidence the agent asks for investigation instead of inventing an answer.

Documented SQL fenced in retrieved SOPs is quoted with its source and marked REVIEW_REQUIRED. No SQL is executed. Confirm schema, identifiers, permissions and impact before using it. The report also includes a scoped Jira query.

`review/work_agent/outbox.json` groups requester updates by verified email, with ticket links. These are local drafts, not Slack attached drafts. During a Codex session, look up each exact email in Slack, verify one active human match, and call `slack_send_message_draft` with that user's ID. Preserve existing drafts. Record any successful attached draft separately; never use `slack_send_message` for this workflow. There is no standalone supported Slack draft API wired to the scheduled job.

Sensitive categories remain escalated without comments, email updates, queries or Slack drafts. Operational production changes, code deployment, and outbound messages are not part of the research loop. Existing CLAUDE.md approval and system boundaries remain in force.

## Analyst readiness

`--analyst auto` uses the existing Claude analyst when configured, otherwise the deterministic heuristic. The summary exposes the actual analyst and missing sheet. An explicit `--analyst claude` fails if unavailable. Heuristic outputs are research aids, not autonomous diagnoses; advanced reasoning needs the configured LLM service or a Codex review session. Configure secrets only in the existing ignored `.env`, never in source or reports.

## Review artifacts

- `index.html`: local review of all planned tickets.
- `tickets/PESD1-*.json`: evidence and per-ticket fix plan.
- `outbox.json`: requester messages ready for exact Slack lookup.
- `summary.json`: counts, analyst, errors and blockers.

Reports are local and created with restricted file permissions. Their source snapshots can contain internal ticket content and work email addresses.
