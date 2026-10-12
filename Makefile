.PHONY: help test flows flow flows-status flows-runs flows-tick flows-install flows-rollback execute retriage demo demo-reset queue batch brief rules clean mirror mirror-apply labels tick tick-install corpus-refresh corpus-refresh-install mcp connector-install connector-url twin twin-setup twin-morning twin-eod twin-export twin-alerts

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

test:  ## run the test suite (stdlib unittest, no network)
	python3 -m unittest discover -s tests -t . -v

demo:  ## full offline run: wipes local demo state, then corpus -> triage -> review batch -> brief
	@$(MAKE) demo-reset CONFIRM=1
	./tools/seed_demo.sh
	python3 -m agents.jira_leader.queue run \
		--file tests/fixtures/inbox.jsonl \
		--sheet tests/fixtures/intake_sheet.csv --analyst heuristic
	python3 -m agents.jira_leader.batch assemble
	python3 -m agents.chief_of_staff.brief brief

# demo-reset wipes corpus/ledger.db — the SAME file board_tick.sh writes to
# every 5 minutes on the live system. It no longer touches logs/*.jsonl: those
# are now operational history (execution_log, corrections timing), not demo
# fixtures, and a demo run should not be able to erase them. Requires
# CONFIRM=1 so it can't be triggered by a bare `make demo-reset` typo once
# there is a live ledger worth losing.
demo-reset:  ## wipe local state (ledger, proposals, batches) — needs CONFIRM=1
	@if [ "$(CONFIRM)" != "1" ]; then \
		echo "demo-reset deletes corpus/ledger.db — the live board state if"; \
		echo "this system is running against real Jira, not just a demo."; \
		echo "Re-run as: make demo-reset CONFIRM=1"; \
		exit 1; \
	fi
	rm -f corpus/ledger.db corpus/ledger.db-wal corpus/ledger.db-shm
	rm -rf review/proposals review/batch_* review/brief_*
	rm -rf review/rule_proposals_* review/mobile

queue:  ## poll PESD1 and triage (live; needs .env)
	python3 -m agents.jira_leader.queue run

batch:  ## assemble the approval batch
	python3 -m agents.jira_leader.batch assemble

work-agent:  ## research open intake, prepare fix plans and requester draft outbox
	python3 -m agents.jira_leader.work_agent

connector-install:  ## keep the MCP server running across reboots
	./tools/install_connector.sh

connector-url:  ## the permanent connector URL, and whether it answers
	./tools/tunnel_url.sh

mcp:  ## MCP server for the Claude app connector (needs MCP_AUTH_TOKEN)
	python3 -m tools.mcp_server --host $(or $(HOST),127.0.0.1)

flows:  ## every flow: its trigger and its chain of agents
	python3 -m flows list

flow:  ## run one flow now, dry-run (FLOW=intake; add EXEC=--execute to write)
	python3 -m flows run $(FLOW) $(EXEC)

flows-status:  ## last run, lock and pending events per flow
	python3 -m flows status

flows-runs:  ## recent run history (FLOW=name to filter)
	python3 -m flows runs $(FLOW)

flows-tick:  ## start every due flow once, here (what launchd runs each minute)
	python3 -m flows tick

flows-install:  ## schedule the flows (one launchd job) and retire the old jobs
	./tools/install_flows.sh

flows-rollback:  ## back to the old per-job launchd schedules
	./tools/install_flows.sh rollback

execute:  ## carry out approved proposals: dry run; EXEC=--execute to write to Jira
	python3 -m flows run execute --confirm $(EXEC)

retriage:  ## re-run triage on one ticket because a rule changed (TICKET=PESD1-x REASON="...")
	python3 -m flows run retriage --confirm --data '{"ticket": "$(TICKET)", "reason": "$(REASON)"}'

tick:  ## legacy: one board pass via the old shell script (rollback only)
	./tools/board_tick.sh

tick-install:  ## legacy: the old 5-minute launchd job (use flows-install)
	./tools/install_tick.sh

corpus-refresh:  ## catch tickets updated recently and reindex (read-only)
	python3 -m flows run corpus

corpus-refresh-install:  ## legacy: the old 2-hour launchd job (use flows-install)
	./tools/install_corpus_refresh.sh

labels:  ## read triage-approved / triage-rejected labels off the board
	python3 -m agents.jira_leader.labels poll $(APPLY)

mirror:  ## report PESD1 parents whose PRDT clone has moved on
	python3 -m core.status_mirror report

mirror-apply:  ## move those parents to match (add EXEC=--execute to write)
	python3 -m core.status_mirror apply $(EXEC)

brief:  ## morning brief
	python3 -m agents.chief_of_staff.brief brief

rules:  ## propose rules from knowledge/corrections.jsonl
	python3 -m flows run rules

twin:  ## digital twin: sources, records, drafts, alerts at a glance
	python3 -m agents.digital_twin.lead status

twin-setup:  ## work profile, one question at a time
	python3 -m agents.digital_twin.lead setup

twin-morning:  ## morning update from the sources you have allowed
	python3 -m agents.digital_twin.lead morning

twin-eod:  ## end-of-day update
	python3 -m agents.digital_twin.lead eod

twin-export:  ## "save my work profile and task list" -> one dated file
	python3 -m agents.digital_twin.lead export

twin-alerts:  ## one pass of the live-alert pilot (paused unless resumed)
	python3 -m agents.digital_twin.alerts once

clean: demo-reset  ## demo-reset plus the corpus index
	rm -f corpus/corpus.db corpus/corpus.db-wal corpus/corpus.db-shm
	rm -rf corpus/raw
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
