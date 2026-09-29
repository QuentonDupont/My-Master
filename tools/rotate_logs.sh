#!/bin/bash
# Cap the size of the append-only logs, truncating each oversized one IN PLACE
# to keep its most recent tail.
#
#   ./tools/rotate_logs.sh
#   MAX_BYTES=2000000 KEEP_BYTES=500000 ./tools/rotate_logs.sh
#
# Truncate-in-place, not rotate-and-rename: core/log.py opens each *.jsonl file
# fresh per write (core/log.py:67, `with self.path.open("a", ...)`), and
# launchd opens StandardOutPath/StandardErrorPath fresh per invocation, not
# held open across runs — so there is no long-lived file handle anywhere that
# a rename would silently orphan. `cat tmp > f` rewrites f's own inode in
# place rather than swapping it, matching that.
#
# A byte-boundary truncation can cut the first kept JSONL line in half; every
# reader of these files (agents/chief_of_staff/brief.py's _read_log) already
# skips a line it can't json.loads, so that first partial line is silently
# dropped rather than breaking anything.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX_BYTES="${MAX_BYTES:-5000000}"
KEEP_BYTES="${KEEP_BYTES:-1000000}"

shopt -s nullglob
for f in "$ROOT"/logs/*.jsonl "$ROOT"/logs/*.out "$ROOT"/logs/*.err; do
  size=$(wc -c < "$f" | tr -d ' ')
  if [ "$size" -gt "$MAX_BYTES" ]; then
    tmp="$f.rotate.tmp"
    tail -c "$KEEP_BYTES" "$f" > "$tmp"
    cat "$tmp" > "$f"
    rm -f "$tmp"
    new_size=$(wc -c < "$f" | tr -d ' ')
    echo "truncated $(basename "$f"): ${size} -> ${new_size} bytes"
  fi
done
