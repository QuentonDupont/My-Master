# Pomelo Support Triage System

A semi-autonomous triage assistant for the PESD1 support board. It researches
incoming tickets and produces **proposals**. A human approves every one. On
approval it comments on PESD1 and, when code work is needed, clones the ticket
into PRDT and assigns a developer.

The system never acts unattended. `--dry-run` is the default everywhere; writing
requires an explicit `--execute`.

See [CLAUDE.md](CLAUDE.md) for the invariants — they are the specification, not
documentation of it.

---

## Build status

Build order is Historian → Jira Leader → Chief of Staff → Slack Leader.

| Component | State |
|---|---|
| Historian (`agents/historian`, `corpus/`) | built — retrieval, duplicates, assignee ranking, SOP drafting |
| Jira Leader (`agents/jira_leader`) | built — queue, 5 concurrent workers, batch assembly, execution hand-off |
| Chief of Staff (`agents/chief_of_staff`) | built — morning brief, rule proposals, cost, error review |
| Slack Leader (`agents/slack_leader`) | built — mentions: 3 concurrent thread workers, reply proposals, single write path with undo. Needs `SLACK_BOT_TOKEN` to run live |
| Digital Twin (`agents/digital_twin`) | built — personal Chief of Staff: Work Profile, Task List, morning/EOD updates from allowed sources, drafts in the owner's voice, live-alert pilot. Read and draft only; never sends |

Apollo and Henry have no integration at all, by design.

---

## Try it offline (no Jira token, no network)

```bash
make demo
```

That seeds the corpus from `tests/fixtures/`, triages six sample tickets, writes
an approval batch into `review/`, and prints the morning brief. What it produces:

| Ticket | Outcome | Why |
|---|---|---|
| PESD1-11274 | `DUPLICATE` | matches the open PESD1-11270 |
| PESD1-11275 | `NEEDS_CODE` | checkout bug → clone to Nadia Rahman |
| PESD1-11276 | `ANSWERABLE` | answered from a resolved ticket |
| PESD1-11277 | `ESCALATED` | refund — never-touch, no comment, no clone |
| PESD1-11278 | `ESCALATED` | sanity gate: the requirement cannot be stated |
| PESD1-11279 | `NEEDS_CODE` | catalog bug → clone to Pim Wattana |

Then walk the approval loop:

```bash
$EDITOR review/batch_*.md                        # read
$EDITOR review/batch_*.json                      # set "decision", edit any field
python3 -m agents.jira_leader.batch apply review/batch_<ts>.json
python3 -m agents.jira_leader.batch execute      # dry run — prints the plan
python3 -m agents.jira_leader.batch execute --execute   # the only way to write
```

Every field you change is appended to `knowledge/corrections.jsonl`, which is
what the Chief of Staff clusters into rule proposals.

---

## Live setup

```bash
cp config/.env.example .env      # gitignored; fill in JIRA_EMAIL + JIRA_API_TOKEN
python3 -m core.jira_client whoami
python3 -m core.jira_client fields --grep email       # find requester_email_field
python3 -m core.jira_client issue-types --project PRDT  # confirm the clone type
python3 -m core.jira_client transitions PESD1-<key>     # confirm the status names
```

Put the custom field id in `config/boards.yml` (`intake.requester_email_field`),
and set `development.issue_type` to whatever PRDT actually uses for this work.
If PESD1 carries the requester email, the Google Sheet join is unnecessary.

Build the corpus, then run:

```bash
python3 -m corpus.export jira --project PESD1 --months 18
python3 -m corpus.export jira --project PRDT  --months 18
python3 -m corpus.index build

python3 -m agents.jira_leader.queue run     # triage what is Waiting for Support
python3 -m agents.jira_leader.batch assemble
```

The requester intake sheet is read as a CSV export (`REQUESTER_SHEET_CSV`);
there is no Google API integration in v1.

### Optional: the Claude analyst

Workers classify and draft with a deterministic heuristic by default — no
network, no cost. To use Claude instead:

```bash
pip install -r requirements-optional.txt
# set ANTHROPIC_API_KEY in .env
python3 -m agents.jira_leader.queue run --analyst claude
```

Evidence from the Historian and every approved rule in `knowledge/rules.md` go
into its context. Any failure falls back to the heuristic and flags the proposal
`analyst_fallback` — a worker always returns a proposal.

---

## Slack mentions

```bash
make slack                      # offline, against tests/fixtures/mentions.json
python3 -m agents.slack_leader.leader run --events events.json   # live
python3 -m core.slack_execute run s_0003            # dry run
python3 -m core.slack_execute run s_0003 --execute  # post, after approval
python3 -m core.slack_execute undo <channel>:<ts> --execute
```

A mention becomes a reply *proposal*. Nothing posts without approval and an
explicit `--execute`, because a Slack message can be deleted but not unread.
Never-touch subjects get total silence — no reply, no acknowledgement.

Needs the bot token (`xoxb-`), not the app-level token (`xapp-`): Slack answers
`not_allowed_token_type` to the latter for every read and every post.

## The Digital Twin

A personal Chief of Staff for the board owner, separate from the triage
brief. Built from the staff starter file in `agents/digital_twin/spec/`.

```bash
make twin-setup        # Work Profile, one question at a time — skip any
python3 -m agents.digital_twin.lead test pasted    # prove a source, record it
make twin-morning      # what changed, what needs me, what is next
make twin-eod
make twin-export       # "save my work profile and task list" -> twin/exports/
make twin-export-system  # the bot system's own profile, for a chat outside the repo
```

Two exports, two files: the Work Profile is the person, the system profile is
the ecosystem (spec, state of play, commands, sources, which credentials are
set — by name only). Upload both to a Claude Project and the twin works from
anywhere, no container or session to keep alive.

Sources live in `config/twin.yml` and are off until you enable them; enabled
is not the same as working — only a passed test marks a source "working" on
the profile's source list. When an app cannot connect, drop a JSON snapshot in
`twin/inbox/` (see `tests/fixtures/twin_snapshot.json` for the shape) and the
update reads that instead, saying it is a snapshot.

What it will not do: send. Drafts (`agents.digital_twin.drafts`) are yours to
send from the app; then `drafts sent <id> --proof <link>` records it and closes
the linked task. The Google client is read-only by construction and the test
suite asserts the package imports no write client.

Live alerts (`agents.digital_twin.alerts`) are a poll-based pilot, paused by
default, budgeted per day, quiet on routine items. A push bridge (Slack Socket
Mode, Gmail Pub/Sub, Drive watch) is not built; `bridge.py` is the durable
queue one would feed, and `alerts test-event` proves that path end to end.

## Reviewing from your phone

`tools/review_app.html` is published as a private page on claude.ai. It shows the
same batch, one card per proposal, and records approve / reject / reassign.

```bash
python3 -m agents.jira_leader.mobile export --out review/mobile   # seed documents
# Claude writes them into the page's store, and reads your decisions back
python3 -m agents.jira_leader.mobile apply review/decisions.json
python3 -m agents.jira_leader.batch execute --execute
```

The page has no Jira credentials and no execution path — it records decisions and
nothing else. Reassigning on the phone is logged to `knowledge/corrections.jsonl`
exactly like an edit made in the batch file, so the learning loop sees it.

---

## Daily loop

```bash
make brief      # what is waiting for you, escalations, cost, errors
make queue      # triage new tickets
make batch      # assemble the approval batch
make rules      # weekly: propose rules from the corrections log
```

Recommendations to action in Jira yourself (the system never deletes a ticket):

```bash
python3 -m core.recommendations list
```

Rollback, if something lands wrong:

```bash
python3 -m core.execute undo PESD1-11279            # dry run
python3 -m core.execute undo PESD1-11279 --execute
```

---

## How a ticket moves

```
queue (Jira Leader)
  └─ ledger gate: new, or content changed AND back to "Waiting for Support"
     └─ claim → worker (ephemeral, 5 concurrent)
        1 read → 2 sanity gate → 3 never-touch gate → 4 duplicate check
        → 5 Historian retrieval → 6 requester → 7 classify → 8 draft proposal
           ├─ ESCALATED   no comment, no clone — a human reads it
           ├─ DUPLICATE   propose the link, never clone
           └─ PROPOSED    into the next approval batch
                 └─ human: approve / edit / reject   → corrections.jsonl
                       └─ core.execute (the only write path)
                          comment → clone → link → assign → transition
```

Ledger states: `NEW → CLAIMED → PROPOSED → APPROVED|CORRECTED|REJECTED →
EXECUTED`, plus `ESCALATED`, `DUPLICATE`, `ROLLED_BACK`.

---

## Layout

```
config/     boards.yml, never_touch.yml, repos.yml, .env.example
core/       ledger.py proposals.py execute.py jira_client.py
            config.py log.py requester.py corrections.py miniyaml.py
corpus/     export.py index.py (+ corpus.db, ledger.db — gitignored)
agents/     historian/ jira_leader/ chief_of_staff/ slack_leader/ marketing_onsite/ digital_twin/
twin/       the Digital Twin's records — profile, tasks, drafts, cursors (gitignored)
knowledge/  rules.md, corrections.jsonl, sops/
review/     generated batches, briefs, rule proposals (gitignored)
tests/      75 tests, stdlib unittest, no network
tools/      fixture generator, demo seeder
```

Every module runs standalone: `python3 -m core.ledger stats`,
`python3 -m agents.historian.retrieval duplicates PESD1-11274`,
`python3 -m agents.jira_leader.worker PESD1-11275`, and so on.

---

## Where the safety actually lives

- `core/execute.py` is the only module that writes to Jira, and it refuses
  anything that is not an `APPROVED`/`CORRECTED` proposal in the ledger.
- `core/jira_client.py` splits read and write into two classes; the write class
  is inert unless constructed with `execute=True`, and every write method has an
  undo next to it.
- Any Jira key or JQL outside PESD1/PRDT raises `ScopeError`.
- Secrets come from `.env` only and are redacted from every log line.
- `tests/test_scope.py` asserts the worker module never imports a write client.

## Tests

```bash
make test
```

No network, no Jira token, no API key. Execution is covered against a fake Jira
writer, including mid-sequence failure and full undo.
