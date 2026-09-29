#!/bin/bash
# Keep the Historian's corpus from going stale, for launchd to run on an
# interval.
#
#   ./tools/corpus_refresh.sh          run it once, here
#
# The corpus was built once and then never touched again while the live
# system kept running: board_tick.sh's every-5-minute sweep was drafting every
# proposal — duplicate detection, "similar resolved ticket" evidence, assignee
# ranking — against a snapshot that was days out of date.
#
# Two steps, both read-only against Jira:
#
#   1. corpus.export jira --updated-since-days N --project {PESD1,PRDT}
#        Filters on `updated`, not `created` — a ticket opened months ago but
#        resolved yesterday has an old `created` date forever, so a
#        created-date filter (including a full re-run of the original
#        --months pull) never re-fetches it and its resolution note goes
#        permanently stale. This MERGES into the existing raw file instead of
#        overwriting it, so a short window never truncates the rest of the
#        history away.
#   2. corpus.index build
#        Rebuilds the SQLite index from the (merged, still-complete) raw
#        files. Local only, no network — cheap even on every run.
#
# Confluence is not refreshed here: it changes far more slowly than tickets,
# and stays on its existing manual/periodic pull.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
. "$ROOT/tools/notify_on_failure.sh"

PY=/usr/bin/python3
stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# Generous overlap against the refresh interval (2h by default, see the
# .plist) so one missed or slow run never opens a real gap in coverage.
UPDATED_SINCE_DAYS="${UPDATED_SINCE_DAYS:-3}"

echo "=== corpus refresh $(stamp) ==="

failed=0

echo "--- 1/2 export (updated in the last ${UPDATED_SINCE_DAYS}d, merged)"
export_ok=1
for project in PESD1 PRDT; do
  if ! "$PY" -m corpus.export jira --project "$project" \
        --updated-since-days "$UPDATED_SINCE_DAYS" 2>&1 | tail -10; then
    echo "export failed for $project"
    export_ok=0
  fi
done
if [ "$export_ok" -eq 1 ]; then
  clear_alert corpus_refresh_export
else
  failed=1
  alert_once corpus_refresh_export "My-Master: corpus export failing" \
    "corpus.export has been failing — check logs/corpus_refresh.err"
fi

echo "--- 2/2 reindex"
if "$PY" -m corpus.index build 2>&1 | tail -10; then
  clear_alert corpus_refresh_index
else
  echo "reindex failed"
  failed=1
  alert_once corpus_refresh_index "My-Master: corpus reindex failing" \
    "corpus.index build has been failing — check logs/corpus_refresh.err"
fi

if [ "$failed" -eq 0 ]; then
  heartbeat corpus_refresh
fi

echo "=== done $(stamp) ==="
