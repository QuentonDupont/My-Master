# SETUP.md: run the Pomelo Support Triage System anywhere

This is the one file to read before moving or re-creating the system. It says what
the system **is**, what it **connects to**, how it **must behave**, and how to
**set it up on a new machine**. `./setup.sh` does the mechanical parts.

> Written 1 Oct 2026 from the repo as it stands on branch `worktree-remove-panel`
> (based on `wip/daily-report-health-mailer`). `CLAUDE.md` is the specification;
> this file is the operating manual. If they ever disagree, `CLAUDE.md` wins.
> **No secrets are in this file or in git.** Credentials live only in `.env`.

---

## 1. Quick start (10 minutes, any macOS or Linux box)

```bash
git clone <your remote> My-Master && cd My-Master
git checkout <the branch you are deploying>     # see §10 for which one

./setup.sh                  # checks Python + SQLite FTS5, creates .env, runs the 289 tests
$EDITOR .env                # JIRA_EMAIL + JIRA_API_TOKEN at minimum
./setup.sh                  # re-run: now also checks Jira + runs the read-only preflight
./setup.sh --build-corpus   # first time only: 18 months of PESD1/PRDT + Confluence → search index
make brief                  # what is waiting for you
```

That gets you a working, **read-only** system. It still cannot write to Jira, Slack
or email until you pass `--execute` to a specific command (§4). To make it run on its
own: `./setup.sh --schedules all --yes` (§8), on **one machine only** (§9).

Try it with no credentials at all: `./setup.sh --demo --offline` seeds six sample
tickets and shows every outcome (duplicate, needs-code, answerable, two escalations).
The demo wipes `corpus/ledger.db`, so the script refuses to run it where one exists.

**Requirements:** Python 3.9+ (3.11+ recommended; it runs on 3.9 and 3.13), standard
library only, and an SQLite build with FTS5 (python.org, Homebrew and Debian/Ubuntu
all have it). No pip install is needed. `anthropic` is optional (§3).

---

## 2. What it is

A semi-autonomous **triage assistant for a Technical PM at Pomelo Fashion**. It watches
the **PESD1** Jira board (support intake), researches each new ticket, and writes a
**proposal**. A human approves, edits or rejects every proposal. On approval it
comments on PESD1 and, when code work is needed, clones the ticket into **PRDT**
(development) and assigns a developer.

```
PESD1 "Waiting Support"
   │  (every 5 min)
   ▼
Jira Leader ── ledger gate ── up to 5 ephemeral workers ──► proposal
   │            (SQLite)        read → sanity → never-touch → duplicate →
   │                            Historian evidence → requester → classify → draft
   ▼
approval batch (review/) ── human: approve / edit / reject ──► corrections.jsonl
   ▼                                                              │
core/execute.py  (the ONLY Jira write path)                        ▼
 comment → clone to PRDT → link → assign → transition       Chief of Staff clusters
                                                             corrections into rule
Slack Leader: @mentions → reply proposals (human posts)      proposals; a human adds
Marketing & Onsite: Apollo content drafts, staged inactive   them to knowledge/rules.md
```

Four standing agents plus one team (workers are ephemeral, one per unit of work):

| Agent | Job | Entry points |
|---|---|---|
| **Historian** | Search index over past tickets, Confluence, GitHub; duplicate detection; assignee ranking; SOP drafting | `agents/historian`, `corpus/` |
| **Jira Leader** | Poll the board, run workers, assemble approval batches, hand approved work to execution | `agents/jira_leader` |
| **Chief of Staff** | Morning brief, 09:00 emailed daily report, health checks, board ranking, rule proposals, cost and error review | `agents/chief_of_staff` |
| **Slack Leader** | Turn Slack @mentions into reply proposals; poll channels | `agents/slack_leader` |
| **Marketing & Onsite** | Draft Apollo content changes (banners, menus, navigation); reports to the board owner directly | `agents/marketing_onsite` |

---

## 3. What it connects to

Everything below is reached over HTTPS from the machine that runs the system. Nothing
connects *in* except the optional MCP connector.

| System | How | Credential (`.env`) | Direction | Needed for |
|---|---|---|---|---|
| **Jira Cloud** (`pomelofashion.atlassian.net`) | REST v2 + Agile, `core/jira_client.py` | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | Read everywhere; write **only** via `core/execute.py` and status mirroring. Hard allowlist: PESD1 and PRDT only | **Required** for anything live |
| **Confluence** (same site, `/wiki/api/v2`) | `core/confluence_client.py`, same login | same Jira token | **Read only**, no write path exists | Historian's best evidence (SOPs weigh more than ticket history). Spaces in `config/confluence.yml` |
| **Slack** | Web API, `core/slack_client.py` | `SLACK_USER_TOKEN` (xoxp-, preferred, reads as you) or `SLACK_BOT_TOKEN` (xoxb-). **xapp- tokens cannot read or post** | Read always; post only via `core.slack_execute --execute` after approval | Slack Leader + poller. Optional |
| **GitHub** (`api.github.com`, org `pomelofashion`) | commit/PR search by ticket key | `GITHUB_TOKEN` | Read only | Links a ticket to the change that fixed it. Optional |
| **Anthropic API** | `agents/jira_leader/analysis.py` | `ANTHROPIC_API_KEY`, `ANALYST_MODEL` | Outbound prompts | Optional. **Unset = workers use a deterministic keyword heuristic.** The biggest single quality lever |
| **Gmail SMTP** | `core/mailer.py`; OAuth2 via `oauth2.googleapis.com` (`tools/gmail_oauth_setup.py`) or app password | `GMAIL_OAUTH_*` or `REPORT_EMAIL_APP_PASSWORD`, `REPORT_EMAIL_FROM/TO` | Sends the daily report only | 09:00 daily status email. Optional |
| **Requester intake sheet** | A **CSV export**, no Google API | `REQUESTER_SHEET_CSV` (default `corpus/raw/intake_sheet.csv`) | Read | Matching a ticket to its requester. Optional |
| **Claude app (phone)** | MCP server, `tools/mcp_server.py`, JSON-RPC over HTTP | `MCP_AUTH_TOKEN` (server refuses to start without it) | Inbound. Needs a **public URL** (Anthropic calls it from their servers): Tailscale Funnel or a Cloudflare tunnel | Approving from the phone. Optional |
| **claude.ai review page** | `tools/review_app.html` published as a private Artifact | none in the repo | Records approve/reject, has no Jira credentials | Optional phone review UI. **Lives on claude.ai, not in git** |

**Deliberately not connected:** Apollo and Henry. There is **no client for either** in
this repo. Marketing & Onsite only *drafts* plans for Apollo surfaces; a human applies
them. NetSuite, Superset and customer data are not touched. MCP tools: `board_status`,
`list_proposals`, `show_proposal`, `approve`, `reject`, `execute` (dry-run unless
`confirm=true`), `escalations`, `answer_escalated`, `status_drift`.

**Jira specifics that bite on a fresh instance** (all in `config/boards.yml`; run
`python3 -m core.preflight` to check each): intake status `Waiting Support`, resolved
statuses `Live` and `Closed - Won't Do`, link type `Cloners`, clone type `Story`, PRDT
requires `customfield_10199 = Technology` and an Epic Link (`customfield_10008`),
default epic `PRDT-11563`, tracked boards 381 (PESD1) and 347 (Tech Ops). Atlassian
removed the old `/search` endpoint (HTTP 410); the client uses `/search/jql`.

---

## 4. Behavior contract: how it must behave

These are **invariants**, not preferences. They are enforced in code and tests. A new
operator, human or AI, must not weaken them without the board owner's explicit,
dated approval written into `CLAUDE.md`.

1. **A human approves everything that goes out.** No Jira write without an approved
   proposal. The one exception (20 Sep 2026): **status mirroring**. When a PRDT clone
   moves, its PESD1 parent is moved to match, transitions only, forward only, never
   into a closing status, and every move is recorded so it can be undone.
2. **`--dry-run` is the default everywhere.** Writing needs an explicit `--execute`.
3. **Never-touch categories** escalate to the human with **no comment and no clone**,
   whatever the confidence: refunds (including store credit), payments, pricing,
   customer PII, stock adjustments, account access. Keywords live in
   `config/never_touch.yml`. Also treat anything that is a money/PII/access matter *in
   spirit* as never-touch even if no keyword fires, and fix the keyword gap.
4. **Apollo and Henry are read-only, permanently.** One narrow exception for the
   Marketing & Onsite team (21 Sep 2026): content surfaces only, never orders,
   customers, refunds, pricing or stock; **every change staged inactive** (or
   scheduled/market-unticked); drafting is separate from applying, which is a human
   step. A surface not in `agents/marketing_onsite/surfaces.py` is not touched.
5. **Never delete a ticket.** Recommend "Close as Won't Do" for a human to action.
6. **Never re-process** a ticket already in the ledger unless its content changed *and*
   it is back to "Waiting Support"; never re-execute an `EXECUTED` proposal.
7. **Never infer a requester email from a name alone.** Order: Jira field → intake
   sheet key join → name + time-window + text similarity (all three, medium
   confidence) → otherwise "Requester: unknown (manual lookup needed)".
8. **No credentials in the repo or in logs.** Secrets come from `.env` via
   `core.config.env()`, which feeds a redactor.
9. **Every write has its undo** written in the same commit (`core.execute undo`).
10. **Never post to Slack without approval.** A message can be deleted but not unread.
    Never-touch subjects get total silence: no reply, no acknowledgement.
11. **Scope:** only the PESD1 and PRDT projects. Any other key or JQL raises `ScopeError`.

**Standing instructions from the board owner** (from `HANDOFF.md`): keep
developer-facing text direct, so a developer never has to read the whole ticket to
find the ask; never delete tickets; every outbound message is approved first. Move the
PESD1 parent to *To Do* alongside the comment and PRDT clone.

**Worker loop** (per ticket): read → *sanity gate* (can the requirement be stated in
one sentence? no → escalate) → *never-touch gate* → *duplicate check* (strong match →
propose a link, never clone) → Historian retrieval → requester lookup → classify
`ANSWERABLE` or `NEEDS_CODE` → draft. Writes nothing to Jira. Always give a ranked
shortlist of assignees, never a single guess. **Default to `ANSWERABLE`** unless a
developer change is genuinely required (approved rule, 2026-09-21).

**Learning loop:** models do not learn from being corrected, so persistence is built.
Every human edit/rejection is appended to `knowledge/corrections.jsonl`. Weekly, run
`make rules` to cluster them into `review/rule_proposals_*.md`. **Nothing there is
live until a human pastes it into `knowledge/rules.md`**, which is injected verbatim
into every worker. Don't let this pile up unrun.

**Ledger states:** `NEW → CLAIMED → PROPOSED → APPROVED|CORRECTED|REJECTED → EXECUTED`,
plus `ESCALATED`, `DUPLICATE`, `ROLLED_BACK`. Execution order:
comment → clone → link → assign → transition; a mid-sequence failure rolls back that
item only.

**Lessons that have already cost time** (`.claude/skills/pesd1-triage`): Jira comments
through the MCP tool are *markdown*, so `{code}` markup renders as text and markdown
eats `*` (`COUNT(*)` posts as `COUNT(_)`); use a fenced block and read the comment back.
`slack_send_message_draft` drafts, `slack_send_message` sends. A ticket key mentioned
in Slack identifies the requester, so pull that ticket's own comments before replying.

---

## 5. Context an operator (or an AI assistant) needs

* **Boards.** PESD1 = what staff ask for; PRDT = what developers do. A PRDT ticket is a
  *clone* of a PESD1 ticket (link type `Cloners`); priority is **carried** from PESD1.
* **Components → routing.** Ticket text maps to components (henry, netsuite, apollo, api,
  web, ios, android, checkout, pims, neon, perks, order) via
  `config/repos.yml`; this drives clone labels and developer ranking. Henry =
  warehouse/factory/POs; Apollo = storefront/merchandising; NetSuite = accounting.
* **Evidence weight.** Confluence SOPs outrank ticket history. Approved novel
  resolutions should become Confluence pages (published by a human).
* **Corpus.** `corpus/raw/*.jsonl` (exports) → `corpus/corpus.db` (SQLite FTS5). The
  corpus-refresh job merges tickets *updated* in the last 3 days every 2 hours.
  Confluence is refreshed manually. Ledger and corpus are **local SQLite**; there is
  no server database.
* **If you work in Claude Code here:** `CLAUDE.md` and `.claude/skills/` load
  automatically. Skills: `pesd1-triage` (board sweeps, Slack mentions, requester
  replies) and `apollo-category-nav` (Apollo Category Navigation lessons: the DB
  `bar_type` names lie, saves publish live, verify in the database). Read `CLAUDE.md`
  and `HANDOFF.md` first, and **ask before widening any invariant**.
* **No GUI.** The local review panel (`tools/panel.py`) was removed on 29 Sep 2026 at
  the board owner's request. Review happens in `review/batch_*.md|json`, via the MCP
  connector, or on the claude.ai page. Don't rebuild a panel.

---

## 6. Command cheat sheet

```bash
make test                 # 289 tests, offline
make brief                # morning brief: waiting, escalations, cost, errors
make queue                # triage Waiting Support            (writes proposals only)
make batch                # assemble the approval batch       → review/batch_*.md|json
make rules                # weekly: rule proposals from corrections.jsonl
make mirror               # report PESD1 parents whose PRDT clone moved on
make mcp                  # MCP server on 127.0.0.1:8766 (needs MCP_AUTH_TOKEN)

# Approve and execute (the only writes)
python3 -m agents.jira_leader.batch apply review/batch_<ts>.json
python3 -m agents.jira_leader.batch execute              # dry run: prints the plan
python3 -m agents.jira_leader.batch execute --execute    # the only way to write Jira
python3 -m core.execute undo PESD1-123 --execute         # roll one back

# Look around
python3 -m core.preflight          # every live assumption, PASS/WARN/FAIL (read-only)
python3 -m core.jira_client whoami|fields --grep email|transitions PESD1-123
python3 -m core.ledger stats
python3 -m core.recommendations list    # tickets for YOU to close; the system never closes

# Slack and Marketing & Onsite
python3 -m agents.slack_leader.poller once|state|reset
python3 -m core.slack_execute run s_0003 [--execute]
python3 -m agents.marketing_onsite.lead add "pull the banner off HK" --surface web_hero --markets TH
python3 -m agents.marketing_onsite.lead run | report
python3 -m agents.chief_of_staff.daily_report run [--send]
```

---

## 7. Configuration files

| File | What | Edit when |
|---|---|---|
| `.env` | Secrets and runtime. **Gitignored.** Template: `config/.env.example` | Always, once |
| `config/boards.yml` | Projects, statuses, link type, required Jira fields, epics, tracked boards, allowlist | The Jira workflow changes |
| `config/never_touch.yml` | Never-touch keywords/regexes | A money/PII/access ticket slips through |
| `config/repos.yml` | GitHub repos + ticket-text → component keywords | Routing is wrong |
| `config/confluence.yml` | Which Confluence spaces are indexed, with limits | You want more procedures searched |
| `knowledge/rules.md` | Human-approved rules injected into every worker | Weekly, after `make rules` |
| `knowledge/corrections.jsonl` | Append-only edit log (tracked in git) | Never by hand |
| `knowledge/sops/` | Local SOP drafts | An approved resolution deserves a page |

---

## 8. Scheduled jobs

`./setup.sh --schedules all --yes` installs these (launchd on macOS, cron on Linux;
it renders the paths for the new location and refuses a job whose credentials are
missing). Nothing here posts, comments, clones or emails except the daily report.

| Job | Every | Does | Writes outward? | Needs |
|---|---|---|---|---|
| `board` (`tools/board_tick.sh`) | 5 min | sweep → record `triage-approved/rejected` labels → mirror PESD1 status | **Only** the sanctioned status mirror | Jira |
| `corpus` (`tools/corpus_refresh.sh`) | 2 h | merge recently updated tickets, rebuild index | No | Jira |
| `poller` (`agents.slack_leader.poller once`) | 3 min | find @mentions → proposals | No | Slack token |
| `report` (`agents.chief_of_staff.daily_report run --send`) | 09:00 local | status email | **Emails the board owner** | Gmail creds |
| `mcp` (`tools.mcp_server`, always on) | n/a | phone connector | Only via approved `execute` | `MCP_AUTH_TOKEN` + public URL |

Failures raise a one-time macOS notification (`osascript`; silent on Linux) and write
`logs/.heartbeat_*`; `agents.chief_of_staff.health` reads those. Logs are capped by
`tools/rotate_logs.sh`. **Linux:** the daily report time is the machine's local time
(Bangkok at Pomelo); run `mcp` under systemd or supervisor:
`python3 -m tools.mcp_server --host 127.0.0.1 --port 8766` with `WorkingDirectory` at the
repo and `Restart=always`. **macOS:** launchd cannot read scripts under `~/Desktop`,
`~/Documents` or `~/Downloads` (TCC); install the repo elsewhere, e.g.
`~/Library/Application Support/My-Master`. `./setup.sh` checks this.

**Public URL for the connector.** The old setup used Tailscale Funnel
(`tailscale funnel --bg 8766`, `tools/tunnel_url.sh` prints the URL) because it never
changes; `tools/tunnel.sh` does a Cloudflare quick tunnel instead (URL changes on every
restart). Either way `MCP_AUTH_TOKEN` is what makes a public URL safe.

---

## 9. Moving it (read this before you switch machines)

### One writer
**Run the scheduled jobs on exactly one machine.** The ledger and the Slack cursor are
local files. A second machine with an empty ledger sees every "Waiting Support"
ticket as *new*, re-proposes them, and its status mirror may move tickets the first
machine already handled. Stop the old jobs first (`./setup.sh --remove-schedules` on
the old machine), *then* start the new ones.

### What is in git, and what is not
In git: all code, `config/*.yml`, `knowledge/` (rules, corrections, SOPs), `CLAUDE.md`,
`HANDOFF.md`, `.claude/skills/`, `tools/*.plist`, tests. **Not in git (gitignored),
so you must carry it or rebuild it:**

| State | Where | Move? |
|---|---|---|
| Secrets | `.env` | **Re-issue the tokens** on the new host if you can; otherwise copy over a secure channel. Never email it |
| **Ledger** (what was proposed/executed, parent↔clone pairs) | `corpus/ledger.db` (+ `-wal`, `-shm`) | **Yes, essential.** Copy a consistent snapshot: `sqlite3 corpus/ledger.db ".backup 'ledger-copy.db'"` and use that file |
| Pending proposals, batches, decisions, recommendations, Slack cursor | `review/` (`proposals/`, `decisions.json`, `recommendations.jsonl`, `slack_poll_cursor.json`, `content_tasks/`, `content_plans/`) | Yes |
| Execution history and timing | `logs/*.jsonl` (`execute.jsonl`, `corrections.jsonl`, …) | Yes if you want the brief's history |
| Search index | `corpus/corpus.db`, `corpus/raw/` | **No, rebuildable**: `./setup.sh --build-corpus` (about 25 MB, takes a while) |
| Intake sheet CSV | `corpus/raw/intake_sheet.csv` (`REQUESTER_SHEET_CSV`) | Re-export from the sheet |

### Steps
1. On the **old** machine: `./setup.sh --remove-schedules`; wait a minute; snapshot the ledger as above.
2. On the **new** machine: clone the repo, check out the branch, `./setup.sh`.
3. Fill `.env`. Copy `corpus/ledger.db` and `review/` into place **before** the first run.
4. `./setup.sh` again (live checks), then `./setup.sh --build-corpus`.
5. `python3 -m core.ledger stats` should match the old machine's counts.
6. `make brief`, then one manual `make queue` and `make batch`. Confirm nothing already-handled reappears.
7. `./setup.sh --schedules all --yes`.
8. Slack/phone: re-point the MCP connector at the new URL; `python3 -m agents.slack_leader.poller state` to confirm the cursor moved over.

### Not portable by themselves
* The live launchd jobs on the old Mac point at `/Users/quenton-d/My-Master`; the repo's
  `tools/*.plist` still say so by design. `setup.sh` rewrites them for the new path.
  `tools/install_*.sh` copy the plist **unmodified**, so use `setup.sh --schedules` instead.
* `tools/install_connector.sh` expects Tailscale; skip it elsewhere.

---

## 10. State of play and open items

From `HANDOFF.md` (18 Sep) plus this week's changes. Verify before relying on them.

* **Live in Jira (18 Sep):** five PESD1 tickets triaged → PRDT-11559/60/64/65/66, epics
  PRDT-10732 and PRDT-11563. PESD1-10660 and 11276 were deliberately left alone.
* **Waiting on the board owner:** a Slack token for the poller (`xoxp-`/`xoxb-`; the
  `xapp-` ones cannot read); **PRDT-11559 → "Close as Won't Do"** (duplicate of 11539/11536);
  BUSK board 377 was refused as out of scope (a readable-vs-writable split needs a "yes"
  and a `CLAUDE.md` change); more Confluence spaces (DO, PRA, POMQ…); and
  **`ANTHROPIC_API_KEY`** (unset, so workers run the heuristic).
* **Branches:** `wip/daily-report-health-mailer` carries the daily report, health checks,
  Gmail mailer and ops scripts (work in progress); `worktree-remove-panel` is that plus the
  panel removal, the portability fixes, `setup.sh` and this file. Deploy whichever the
  board owner names. `main` has the MCP server, Slack poller, status mirror and Marketing &
  Onsite, but **not** the daily report, mailer or health checks, and it still contains the
  removed panel.
* **Known stale text:** `README.md` says "75 tests" (it is 289) and lists a layout that
  predates the daily report and Marketing & Onsite.

---

## 11. Verify a fresh install

```bash
./setup.sh                              # 0 failures; 289 tests OK
python3 -m core.jira_client whoami      # your account
python3 -m core.preflight               # all PASS (WARN is a note, FAIL is a stop)
python3 -m core.ledger stats            # matches the old machine after a migration
make brief                              # renders with no errors
python3 -m agents.jira_leader.batch execute      # DRY RUN prints a plan, writes nothing
```

If anything surprises you, stop and read `logs/*.jsonl` (structured, secrets redacted)
before running anything with `--execute`.
