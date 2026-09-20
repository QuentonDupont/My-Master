.PHONY: help test demo demo-reset queue batch brief rules clean poll poll-fixtures poll-install mirror mirror-apply panel labels tick tick-install mcp connector-install connector-url

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

test:  ## run the test suite (stdlib unittest, no network)
	python3 -m unittest discover -s tests -t . -v

demo: demo-reset  ## full offline run: corpus -> triage -> review batch -> brief
	./tools/seed_demo.sh
	python3 -m agents.jira_leader.queue run \
		--file tests/fixtures/inbox.jsonl \
		--sheet tests/fixtures/intake_sheet.csv --analyst heuristic
	python3 -m agents.jira_leader.batch assemble
	python3 -m agents.chief_of_staff.brief brief

demo-reset:  ## wipe local state (ledger, proposals, batches, logs)
	rm -f corpus/ledger.db corpus/ledger.db-wal corpus/ledger.db-shm
	rm -rf review/proposals review/slack_proposals review/batch_* review/brief_*
	rm -rf review/rule_proposals_* review/mobile
	rm -f logs/*.jsonl

queue:  ## poll PESD1 and triage (live; needs .env)
	python3 -m agents.jira_leader.queue run

batch:  ## assemble the approval batch
	python3 -m agents.jira_leader.batch assemble

slack:  ## triage Slack mentions (offline fixtures)
	python3 -m agents.slack_leader.leader run --file tests/fixtures/mentions.json

poll:  ## one poller sweep (live; needs SLACK_BOT_TOKEN)
	python3 -m agents.slack_leader.poller once

poll-fixtures:  ## one poller sweep against fixture history, cursor untouched
	python3 -m agents.slack_leader.poller once \
		--history tests/fixtures/slack_history.json \
		--lookback 1000000000 --no-commit

poll-install:  ## install the launchd poller (refuses without an xoxb- token)
	./tools/install_poller.sh

connector-install:  ## keep the MCP server running across reboots
	./tools/install_connector.sh

connector-url:  ## the permanent connector URL, and whether it answers
	./tools/tunnel_url.sh

mcp:  ## MCP server for the Claude app connector (needs MCP_AUTH_TOKEN)
	python3 -m tools.mcp_server --host $(or $(HOST),127.0.0.1)

panel:  ## review page served from this machine (add HOST=0.0.0.0 for your phone)
	python3 -m tools.panel --host $(or $(HOST),127.0.0.1)

tick:  ## one board pass: sweep, read labels, mirror status
	./tools/board_tick.sh

tick-install:  ## run that pass every 5 minutes via launchd
	./tools/install_tick.sh

labels:  ## read triage-approved / triage-rejected labels off the board
	python3 -m agents.jira_leader.labels poll $(APPLY)

mirror:  ## report PESD1 parents whose PRDT clone has moved on
	python3 -m core.status_mirror report

mirror-apply:  ## move those parents to match (add EXEC=--execute to write)
	python3 -m core.status_mirror apply $(EXEC)

brief:  ## morning brief
	python3 -m agents.chief_of_staff.brief brief

rules:  ## propose rules from knowledge/corrections.jsonl
	python3 -m agents.chief_of_staff.rules --write

clean: demo-reset  ## demo-reset plus the corpus index
	rm -f corpus/corpus.db corpus/corpus.db-wal corpus/corpus.db-shm
	rm -rf corpus/raw
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
