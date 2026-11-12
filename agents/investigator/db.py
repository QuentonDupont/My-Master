"""Read-only queries on spanky (Apollo) and henry through the `mysql` client.

Standard library only: the `mysql` command-line client does the talking, the password
travels in MYSQL_PWD (never on the command line, never logged). Three guards, any one
of which is enough on its own:

  1. the login is the read-only `lambda-read` user;
  2. every statement must be a SELECT / WITH / SHOW / DESCRIBE / EXPLAIN — anything
     else (and INTO OUTFILE, FOR UPDATE, LOCK, a second statement smuggled in) is
     refused before the client starts;
  3. the session is opened READ ONLY, so the server refuses writes too.

Connection settings (.env): INVESTIGATE_DB_HOST (127.0.0.1, i.e. the bastion tunnel),
INVESTIGATE_DB_PORT (3306), INVESTIGATE_DB_USER, INVESTIGATE_DB_PASSWORD, and
INVESTIGATE_DB_LABEL — "production" or "pre-production" — printed on every packet,
because pre-production is a stale copy and must never be reported as the live state.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess

from core import config, log

LOG = log.get("investigator.db")

_ALLOWED = ("select", "with", "show", "describe", "desc", "explain")
_FORBIDDEN = re.compile(r"\b(into\s+(out|dump)file|for\s+update|lock\s+in\s+share\s+mode|"
                        r"insert|update|delete|replace(?!\s*\()|alter|drop|create|truncate|grant|"
                        r"revoke|rename|load\s+data|call|handler|lock\s+tables|set)\b", re.I)
DATABASES = ("spanky", "henry")
MAX_ROWS = 500


class NotReadOnly(ValueError):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


def _strip(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"(--|#)[^\n]*", " ", sql)
    return re.sub(r"'(?:[^'\\]|\\.|'')*'", "''", sql)  # literals can't trip the keyword check


def check_read_only(sql: str) -> str:
    """The statement, if it is one read-only statement; NotReadOnly otherwise."""
    bare = _strip(sql).strip().rstrip(";").strip()
    if not bare:
        raise NotReadOnly("empty statement")
    if ";" in bare:
        raise NotReadOnly("one statement per query")
    first = bare.split(None, 1)[0].lower()
    if first not in _ALLOWED:
        raise NotReadOnly(f"{first.upper()} is not a read")
    hit = _FORBIDDEN.search(bare)
    if hit:
        raise NotReadOnly(f"'{hit.group(0)}' is not allowed in a read")
    return sql.strip().rstrip(";")


def settings() -> dict:
    return {"host": config.env("INVESTIGATE_DB_HOST", "127.0.0.1"),
            "port": int(config.env("INVESTIGATE_DB_PORT", "3306")),
            "user": config.env("INVESTIGATE_DB_USER", ""),
            "password": config.env("INVESTIGATE_DB_PASSWORD", ""),
            "label": config.env("INVESTIGATE_DB_LABEL", "unknown")}


def available() -> tuple[bool, str]:
    s = settings()
    if not shutil.which("mysql"):
        return False, "the mysql client is not installed"
    if not s["user"]:
        return False, "INVESTIGATE_DB_USER is not set in .env"
    try:
        with socket.create_connection((s["host"], s["port"]), timeout=3):
            pass
    except OSError as exc:
        return False, f"nothing listening on {s['host']}:{s['port']} ({exc.strerror or exc}) — open the bastion tunnel"
    return True, s["label"]


def query(db: str, sql: str, *, timeout: int = 60) -> list[dict]:
    """Rows as dicts (all values strings, NULL as None). Read-only or refused."""
    if db not in DATABASES:
        raise NotReadOnly(f"unknown database {db}")
    stmt = check_read_only(sql)
    ok, why = available()
    if not ok:
        raise DatabaseUnavailable(why)
    s = settings()
    wrapped = f"SET SESSION TRANSACTION READ ONLY; SELECT * FROM ({stmt}) q LIMIT {MAX_ROWS}" \
        if stmt.split(None, 1)[0].lower() in ("select", "with") else \
        f"SET SESSION TRANSACTION READ ONLY; {stmt}"
    env = dict(os.environ, MYSQL_PWD=s["password"])
    proc = subprocess.run(
        ["mysql", "-h", s["host"], "-P", str(s["port"]), "-u", s["user"], "--batch", "--raw",
         "--connect-timeout=10", db, "-e", wrapped],
        capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"exit {proc.returncode}"
        LOG.warn("db.query_failed", db=db, error=err)
        raise DatabaseUnavailable(err)
    return parse_batch(proc.stdout)


def parse_batch(out: str) -> list[dict]:
    lines = [ln for ln in out.split("\n") if ln != ""]
    if not lines:
        return []
    head = lines[0].split("\t")
    rows = []
    for ln in lines[1:]:
        cells = ln.split("\t")
        rows.append({h: (None if c == "NULL" else c) for h, c in zip(head, cells)})
    return rows
