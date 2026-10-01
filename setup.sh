#!/usr/bin/env bash
# Pomelo Support Triage System: portable setup. Run it anywhere, as often as you like.
#
#   ./setup.sh                      check + prepare this machine (safe, local only)
#   ./setup.sh --demo               also run the offline demo (refuses if a live ledger exists)
#   ./setup.sh --build-corpus       pull 18 months of PESD1/PRDT + Confluence, build the index (read-only on Jira)
#   ./setup.sh --schedules all --yes            install the scheduled jobs (launchd on macOS, cron on Linux)
#   ./setup.sh --schedules board,corpus --yes   only some of them
#   ./setup.sh --remove-schedules   remove what --schedules installed
#   ./setup.sh --help
#
# What it does NOT do: it never writes to Jira, Slack, Confluence or email. It
# never prints a secret. It never overwrites an existing .env, ledger, or corpus.
# Everything the system writes outward still needs an explicit --execute later.
#
# Read SETUP.md first. The system's rules live in CLAUDE.md.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

DEMO=0; BUILD=0; SCHEDULES=""; REMOVE=0; YES=0; SKIP_TESTS=0; OFFLINE=0; FORCE_DEMO=0
while [ $# -gt 0 ]; do
  case "$1" in
    --demo) DEMO=1 ;;
    --force-demo) DEMO=1; FORCE_DEMO=1 ;;
    --build-corpus) BUILD=1 ;;
    --schedules) SCHEDULES="${2:-}"; shift ;;
    --remove-schedules) REMOVE=1 ;;
    --yes|-y) YES=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    --offline) OFFLINE=1 ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1  (try --help)"; exit 2 ;;
  esac
  shift
done

FAILS=0; WARNS=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; WARNS=$((WARNS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILS=$((FAILS+1)); }
step() { printf '\n== %s\n' "$*"; }

# ---------------------------------------------------------------- 1. python
step "1/7 Python"
PY="${PY:-$(command -v python3 || true)}"
if [ -z "$PY" ]; then
  bad "python3 not found. Install Python 3.9 or newer (3.11+ recommended)."
  exit 1
fi
PYV="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
if "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
  ok "python $PYV ($PY)"
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
    || warn "CLAUDE.md asks for 3.11+; it is known to run on 3.9 and 3.13, so continuing"
else
  bad "python $PYV is too old; need 3.9+"; exit 1
fi
if "$PY" -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)')" 2>/dev/null; then
  ok "sqlite FTS5 available (the Historian's search index needs it)"
else
  bad "this Python's sqlite3 has no FTS5. Use a Python build with FTS5 (python.org, Homebrew, or Debian/Ubuntu python3)."
fi
export PY

# ---------------------------------------------------------------- 2. layout
step "2/7 Folders"
mkdir -p logs review corpus/raw knowledge
for d in logs review; do [ -f "$d/.gitkeep" ] || : > "$d/.gitkeep"; done
ok "logs/ review/ corpus/raw/ ready"

# ---------------------------------------------------------------- 3. .env
step "3/7 Configuration (.env)"
if [ ! -f .env ]; then
  cp config/.env.example .env && chmod 600 .env
  ok "created .env from config/.env.example (mode 600). Fill it in; it is gitignored"
else
  ok ".env already exists, left untouched"
  chmod 600 .env 2>/dev/null || true
fi
# MCP_AUTH_TOKEN: generate once if blank (the connector refuses to start without it).
if ! grep -qE '^MCP_AUTH_TOKEN=.+' .env; then
  TOKEN="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  if grep -qE '^MCP_AUTH_TOKEN=' .env; then
    "$PY" - "$TOKEN" <<'PYEOF'
import re, sys
p = ".env"; s = open(p).read()
s = re.sub(r"(?m)^MCP_AUTH_TOKEN=.*$", "MCP_AUTH_TOKEN=" + sys.argv[1], s, count=1)
open(p, "w").write(s)
PYEOF
  else
    printf '\nMCP_AUTH_TOKEN=%s\n' "$TOKEN" >> .env
  fi
  ok "generated MCP_AUTH_TOKEN (not shown)"
fi
# Report which keys are filled in, by NAME only. Values are never printed.
have() { grep -E "^$1=.+" .env | grep -qvE "=you@pomelofashion\.com$"; }
for k in JIRA_EMAIL JIRA_API_TOKEN; do
  have "$k" && ok "$k set" || warn "$k empty (required for anything live)"
done
for k in SLACK_USER_TOKEN SLACK_BOT_TOKEN ANTHROPIC_API_KEY GITHUB_TOKEN \
         GMAIL_OAUTH_REFRESH_TOKEN REPORT_EMAIL_APP_PASSWORD REPORT_EMAIL_FROM; do
  have "$k" && ok "$k set" || printf '  --    %s empty (optional)\n' "$k"
done

# ---------------------------------------------------------------- 4. tests
step "4/7 Tests (offline, no network, no credentials)"
if [ "$SKIP_TESTS" -eq 1 ]; then
  warn "skipped (--skip-tests)"
else
  OUT="$("$PY" -m unittest discover -s tests -t . 2>&1)"; RC=$?
  SUMMARY="$(printf '%s\n' "$OUT" | grep -E '^(Ran|OK|FAILED)' | tr '\n' ' ')"
  if [ "$RC" -eq 0 ]; then ok "$SUMMARY"; else bad "$SUMMARY"; printf '%s\n' "$OUT" | tail -25; fi
fi

# ---------------------------------------------------------------- 5. demo
step "5/7 Offline demo"
if [ "$DEMO" -eq 0 ]; then
  echo "  --    skipped (add --demo to run it)"
elif [ -f corpus/ledger.db ] && [ "$FORCE_DEMO" -eq 0 ]; then
  warn "corpus/ledger.db exists. The demo WIPES it, so I did not run it. Use --force-demo only on a throwaway copy"
else
  if make demo CONFIRM=1 >/dev/null 2>&1; then ok "demo ran: batch written to review/, morning brief printed by 'make brief'"
  else bad "make demo failed. Run 'make demo' to see why"; fi
fi

# ---------------------------------------------------------------- 6. live checks
step "6/7 Live connections (read-only)"
if [ "$OFFLINE" -eq 1 ]; then
  echo "  --    skipped (--offline)"
elif have JIRA_API_TOKEN && have JIRA_EMAIL; then
  if "$PY" -m core.jira_client whoami >/dev/null 2>&1; then ok "Jira: authenticated"
  else bad "Jira: whoami failed. Check JIRA_EMAIL / JIRA_API_TOKEN / JIRA_BASE_URL"; fi
  PF="$(mktemp)"
  if "$PY" -m core.preflight >"$PF" 2>&1; then ok "preflight: every live assumption PASS (boards, statuses, fields, permissions)"
  else warn "preflight reported FAIL/WARN. See below"; tail -25 "$PF"; fi
  rm -f "$PF"
  if have SLACK_USER_TOKEN || have SLACK_BOT_TOKEN; then
    "$PY" -m core.slack_client whoami >/dev/null 2>&1 && ok "Slack: authenticated" || warn "Slack: whoami failed (xapp- tokens cannot read or post; use xoxp- or xoxb-)"
  fi
else
  echo "  --    no Jira credentials yet: skipped. Fill JIRA_EMAIL and JIRA_API_TOKEN in .env, then re-run"
fi

# ---------------------------------------------------------------- corpus (optional)
if [ "$BUILD" -eq 1 ]; then
  step "Corpus build (read-only against Jira and Confluence)"
  if ! { have JIRA_API_TOKEN && have JIRA_EMAIL; }; then
    bad "needs JIRA_EMAIL and JIRA_API_TOKEN in .env"
  else
    "$PY" -m corpus.export jira --project PESD1 --months 18 && \
    "$PY" -m corpus.export jira --project PRDT  --months 18 && \
    "$PY" -m corpus.export confluence && \
    { have GITHUB_TOKEN && "$PY" -m corpus.export github || echo "  --    github skipped (no GITHUB_TOKEN)"; } && \
    "$PY" -m corpus.index build && ok "corpus built: corpus/corpus.db" || bad "corpus build failed; see output above"
  fi
fi

# ---------------------------------------------------------------- 7. schedules
step "7/7 Scheduled jobs"
OS="$(uname -s)"
ALL="board corpus report poller mcp"
[ "$SCHEDULES" = "all" ] && SCHEDULES="${ALL// /,}"
label_for() { case "$1" in board) echo com.pomelo.board-tick;; corpus) echo com.pomelo.corpus-refresh;; report) echo com.pomelo.daily_report;; poller) echo com.pomelo.slack-poller;; mcp) echo com.pomelo.mcp;; esac; }

needs_ok() {  # the same guards the install_*.sh scripts apply
  case "$1" in
    board|corpus) have JIRA_API_TOKEN || { warn "$1: JIRA_API_TOKEN missing, not installing"; return 1; } ;;
    report) { have GMAIL_OAUTH_REFRESH_TOKEN || have REPORT_EMAIL_APP_PASSWORD; } && have REPORT_EMAIL_FROM || { warn "report: email credentials missing, not installing"; return 1; } ;;
    poller) { have SLACK_USER_TOKEN || have SLACK_BOT_TOKEN; } || { warn "poller: no Slack read token, not installing"; return 1; } ;;
    mcp) have MCP_AUTH_TOKEN || { warn "mcp: MCP_AUTH_TOKEN missing"; return 1; } ;;
  esac
}

if [ "$REMOVE" -eq 1 ]; then
  for j in $ALL; do
    L="$(label_for "$j")"
    if [ "$OS" = "Darwin" ]; then
      launchctl bootout "gui/$(id -u)/$L" 2>/dev/null; rm -f "$HOME/Library/LaunchAgents/$L.plist"
    fi
  done
  if [ "$OS" != "Darwin" ] && command -v crontab >/dev/null; then
    crontab -l 2>/dev/null | sed '/# BEGIN pomelo-triage/,/# END pomelo-triage/d' | crontab -
  fi
  ok "scheduled jobs removed"
elif [ -z "$SCHEDULES" ]; then
  echo "  --    none installed. When ready:  ./setup.sh --schedules all --yes"
  echo "        IMPORTANT: run the scheduled jobs on ONE machine only. See SETUP.md, 'One writer'."
elif [ "$YES" -ne 1 ]; then
  warn "scheduling changes the machine, so add --yes to confirm:  ./setup.sh --schedules $SCHEDULES --yes"
  echo "        Reminder: only ONE machine may run board/corpus/poller at a time."
else
  PYABS="$(command -v "$PY")"
  if [ "$OS" = "Darwin" ]; then
    case "$ROOT" in "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
      bad "launchd cannot read scripts under Desktop/Documents/Downloads (macOS TCC). Move the repo, e.g. to ~/Library/Application Support/My-Master"; SCHEDULES="" ;;
    esac
    mkdir -p "$HOME/Library/LaunchAgents"
    for j in ${SCHEDULES//,/ }; do
      L="$(label_for "$j")"; [ -n "$L" ] || { bad "unknown job '$j' (use: $ALL)"; continue; }
      needs_ok "$j" || continue
      sed -e "s#/Users/quenton-d/My-Master#$ROOT#g" -e "s#/usr/bin/python3#$PYABS#g" \
          "tools/$L.plist" > "$HOME/Library/LaunchAgents/$L.plist"
      launchctl bootout "gui/$(id -u)/$L" 2>/dev/null
      if launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$L.plist" && launchctl enable "gui/$(id -u)/$L"; then ok "installed $L"
      else bad "could not load $L"; fi
    done
  elif command -v crontab >/dev/null; then
    LINES=""
    for j in ${SCHEDULES//,/ }; do
      needs_ok "$j" || continue
      case "$j" in
        board)  LINES+="*/5 * * * * cd '$ROOT' && PY='$PYABS' ./tools/board_tick.sh >> logs/board_tick.out 2>> logs/board_tick.err"$'\n' ;;
        corpus) LINES+="0 */2 * * * cd '$ROOT' && PY='$PYABS' ./tools/corpus_refresh.sh >> logs/corpus_refresh.out 2>> logs/corpus_refresh.err"$'\n' ;;
        report) LINES+="0 9 * * * cd '$ROOT' && '$PYABS' -m agents.chief_of_staff.daily_report run --send >> logs/daily_report.out 2>> logs/daily_report.err"$'\n' ;;
        poller) LINES+="*/3 * * * * cd '$ROOT' && '$PYABS' -m agents.slack_leader.poller once >> logs/poller.out 2>> logs/poller.err"$'\n' ;;
        mcp)    warn "mcp is a long-running server, not a cron job. Run it under systemd/supervisor: see SETUP.md" ;;
        *) bad "unknown job '$j'" ;;
      esac
    done
    if [ -n "$LINES" ]; then
      { crontab -l 2>/dev/null | sed '/# BEGIN pomelo-triage/,/# END pomelo-triage/d'
        echo "# BEGIN pomelo-triage"; printf '%s' "$LINES"; echo "# END pomelo-triage"; } | crontab - \
        && ok "cron entries installed (crontab -l to review). Times are this machine's local time" \
        || bad "could not write crontab"
    fi
  else
    bad "no launchd or crontab here. Use your scheduler to run the commands in SETUP.md > 'Scheduled jobs'"
  fi
fi

# ---------------------------------------------------------------- summary
printf '\n== Summary: %s failure(s), %s warning(s)\n' "$FAILS" "$WARNS"
if [ "$FAILS" -eq 0 ]; then
  cat <<EOF

Next:
  1. Fill in .env (JIRA_EMAIL + JIRA_API_TOKEN at minimum), then re-run ./setup.sh
  2. ./setup.sh --build-corpus          (first time only; then the corpus-refresh job keeps it fresh)
  3. make brief                         what is waiting for you
  4. python3 -m agents.jira_leader.queue run     triage (writes proposals only, never Jira)
  5. ./setup.sh --schedules all --yes   when you want it to run on its own (ONE machine only)

Moving from another machine? Read SETUP.md > 'Moving it'. Copy corpus/ledger.db or you lose the history of what was executed.
EOF
fi
exit "$FAILS"
