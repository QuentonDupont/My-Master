"""An MCP server, so the Claude app can work the board from your phone.

    make mcp                       # 127.0.0.1:8766
    python3 -m tools.mcp_server --host 0.0.0.0 --port 8766

Model Context Protocol is JSON-RPC 2.0 over HTTP, so this is written against the
protocol directly rather than pulling in the SDK — the README's promise that the
system runs on the standard library alone still holds.

Why a connector and not another screen
--------------------------------------
On a phone, asking "anything waiting?" beats tapping through cards, and a
rejection typed as a sentence is worth more than a button: it lands in
corrections.jsonl as the reason, which is what the Chief of Staff clusters into
rules. A button can only ever say "no".

Every tool is a thin wrapper over a function that already exists and is already
tested. Nothing here reimplements triage, approval or execution, so this stays
correct as those change — and when the state moves to Postgres one day, this
layer does not move with it.

The line is unchanged
---------------------
`approve` and `reject` record decisions. `execute` is the only tool that writes
to Jira, it defaults to a dry run, and it requires confirm=true to do
anything. Nothing reaches a requester by accident.

Authentication
--------------
`MCP_AUTH_TOKEN` in .env, checked as a bearer token on every request. This is
the minimum that makes a public URL safe, and a public URL is required: Anthropic
calls the connector from their servers, so a private network will not do.
Without the token set the server refuses to start rather than listening openly.
"""
from __future__ import annotations

import argparse
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import config, log

LOG = log.get("mcp")

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "pomelo-triage"
SERVER_VERSION = "1.0.0"


# --------------------------------------------------------------------------
# tools — each one wraps something that already exists
# --------------------------------------------------------------------------

def _board_status() -> dict:
    from core import ledger as ledger_mod

    with ledger_mod.Ledger() as led:
        return {"jira": led.stats()}


def _list_proposals(state: str = "PROPOSED") -> list[dict]:
    from core import ledger as ledger_mod, proposals

    out = []
    with ledger_mod.Ledger() as led:
        for row in led.by_state(state):
            pid = row.get("proposal_id")
            if not pid:
                continue
            try:
                p = proposals.load(pid)
            except FileNotFoundError:
                continue
            out.append({"proposal_id": pid, "ticket": p.ticket,
                        "classification": p.classification,
                        "confidence": p.confidence,
                        "requirement": p.requirement_restated,
                        "flags": p.flags,
                        "clones_to": (p.clone or {}).get("target_project")
                        if isinstance(p.clone, dict) else None})
    return out


def _show(proposal_id: str) -> dict:
    from core import proposals

    p = proposals.load(proposal_id)
    data = p.to_dict()
    data["evidence"] = [e for e in data.get("evidence") or []][:6]
    return data


def _decide(proposal_id: str, decision: str, note: str = "") -> dict:
    from agents.jira_leader import mobile as jira_mobile

    if decision == "reject" and not note.strip():
        return {"error": "a rejection needs a reason — it is what the system "
                         "learns from. Say why and I will record it."}
    return jira_mobile.apply([{"proposal_id": proposal_id,
                               "decision": decision, "note": note}])


def _execute(confirm: bool = False) -> dict:
    from agents.jira_leader import batch

    results = batch.execute_approved(execute=bool(confirm))
    return {"dry_run": not confirm, "results": results,
            "note": "" if confirm else
                    "dry run — nothing was written. Call again with "
                    "confirm=true to post."}


def _escalations() -> list[dict]:
    from agents.jira_leader import reentry

    return reentry.escalated()


def _answer_escalated(proposal_id: str, answer: str) -> dict:
    from agents.jira_leader import reentry

    try:
        return reentry.answer(proposal_id, answer,
                              reason="answered from the Claude app")
    except reentry.ReentryRefused as exc:
        return {"error": str(exc)}


def _status_drift() -> list[dict]:
    from core import status_mirror
    from core.jira_client import JiraReadClient

    return status_mirror.drift(JiraReadClient())


TOOLS: dict[str, dict] = {
    "board_status": {
        "fn": lambda a: _board_status(),
        "description": "Counts per ledger state for the Jira board. Start here "
                       "to see whether anything needs you.",
        "schema": {"type": "object", "properties": {}},
    },
    "list_proposals": {
        "fn": lambda a: _list_proposals(a.get("state", "PROPOSED")),
        "description": "Proposals in a given ledger state, PROPOSED by default "
                       "— those are the ones waiting on a decision.",
        "schema": {"type": "object", "properties": {
            "state": {"type": "string",
                      "description": "PROPOSED, APPROVED, ESCALATED, EXECUTED"}}},
    },
    "show_proposal": {
        "fn": lambda a: _show(a["proposal_id"]),
        "description": "Everything about one proposal: the restated "
                       "requirement, the drafted comment, its evidence and any "
                       "clone it would create.",
        "schema": {"type": "object",
                   "properties": {"proposal_id": {"type": "string"}},
                   "required": ["proposal_id"]},
    },
    "approve": {
        "fn": lambda a: _decide(a["proposal_id"], "approve", a.get("note", "")),
        "description": "Mark a proposal approved. This does NOT post anything — "
                       "run execute afterwards.",
        "schema": {"type": "object",
                   "properties": {"proposal_id": {"type": "string"},
                                  "note": {"type": "string"}},
                   "required": ["proposal_id"]},
    },
    "reject": {
        "fn": lambda a: _decide(a["proposal_id"], "reject", a.get("note", "")),
        "description": "Reject a proposal. A reason is required and is recorded "
                       "in corrections.jsonl, which is what the system learns "
                       "from.",
        "schema": {"type": "object",
                   "properties": {"proposal_id": {"type": "string"},
                                  "note": {"type": "string",
                                           "description": "why — required"}},
                   "required": ["proposal_id", "note"]},
    },
    "execute": {
        "fn": lambda a: _execute(bool(a.get("confirm"))),
        "description": "Post everything approved: comments, PRDT clones, "
                       "transitions. THIS REACHES REAL PEOPLE. Defaults to a dry "
                       "run; pass confirm=true to write for real.",
        "schema": {"type": "object", "properties": {
            "confirm": {"type": "boolean",
                        "description": "true to actually write to Jira"}}},
    },
    "escalations": {
        "fn": lambda a: _escalations(),
        "description": "Tickets the system handed to a human, and whether each "
                       "may be answered through the system or must be done in "
                       "Jira by hand.",
        "schema": {"type": "object", "properties": {}},
    },
    "answer_escalated": {
        "fn": lambda a: _answer_escalated(a["proposal_id"], a["answer"]),
        "description": "Supply the answer to an escalated ticket in your own "
                       "words. It becomes a reviewable proposal.",
        "schema": {"type": "object",
                   "properties": {"proposal_id": {"type": "string"},
                                  "answer": {"type": "string"}},
                   "required": ["proposal_id", "answer"]},
    },
    "status_drift": {
        "fn": lambda a: _status_drift(),
        "description": "PESD1 parents whose PRDT clone has moved on, and what "
                       "the mirror would do about each.",
        "schema": {"type": "object", "properties": {}},
    },
}


# --------------------------------------------------------------------------
# JSON-RPC
# --------------------------------------------------------------------------

def handle(message: dict) -> dict | None:
    """One JSON-RPC request. None means notification — no reply."""
    method = message.get("method")
    mid = message.get("id")
    params = message.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, msg):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": [
            {"name": name, "description": spec["description"],
             "inputSchema": spec["schema"]}
            for name, spec in TOOLS.items()]})

    if method == "tools/call":
        name = params.get("name")
        spec = TOOLS.get(name)
        if not spec:
            return err(-32602, f"no such tool: {name}")
        try:
            result = spec["fn"](params.get("arguments") or {})
        except Exception as exc:
            LOG.warn("mcp.tool_failed", tool=name, error=str(exc)[:200])
            return ok({"content": [{"type": "text", "text": f"failed: {exc}"}],
                       "isError": True})
        LOG.info("mcp.tool", tool=name)
        return ok({"content": [
            {"type": "text", "text": json.dumps(result, indent=2, default=str)}]})

    return err(-32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype="application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        session = self.headers.get("Mcp-Session-Id")
        if session:
            self.send_header("Mcp-Session-Id", session)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        offered = header[7:] if header.startswith("Bearer ") else ""
        return bool(self.token) and secrets.compare_digest(offered, self.token)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, b'{"ok":true}')
            return
        # Streamable HTTP: a client opens the stream with GET before it posts
        # anything. Answering 404 here meant the handshake never started, so no
        # tool call ever arrived — the server looked fine to curl and dead to a
        # real client.
        if not self._authorised():
            LOG.warn("mcp.unauthorised", path=self.path, method="GET")
            self._send(401, b'{"error":"unauthorised"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                # A comment frame is a valid SSE keepalive and carries no data,
                # so nothing is sent that a client has to understand.
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                time.sleep(15)
        except (BrokenPipeError, ConnectionResetError):
            LOG.info("mcp.stream_closed")

    def do_DELETE(self) -> None:
        """Clients end a session with DELETE; there is no per-session state."""
        self._send(200, b'{"ok":true}')

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:
        if not self._authorised():
            LOG.warn("mcp.unauthorised", path=self.path)
            self._send(401, b'{"error":"unauthorised"}')
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, b'{"jsonrpc":"2.0","error":{"code":-32700,'
                            b'"message":"parse error"}}')
            return

        batch = payload if isinstance(payload, list) else [payload]
        replies = [r for r in (handle(m) for m in batch) if r is not None]
        if not replies:
            self._send(202, b"")
            return
        body = replies if isinstance(payload, list) else replies[0]
        self._send(200, json.dumps(body, default=str).encode("utf-8"))


def serve(host: str = "127.0.0.1", port: int = 8766) -> int:
    token = config.env("MCP_AUTH_TOKEN")
    if not token:
        print("MCP_AUTH_TOKEN is not set in .env — refusing to start.")
        print("These tools approve work that gets posted under your name, so an")
        print("unauthenticated endpoint is not an option. Generate one with:")
        print("  python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"")
        return 2

    Handler.token = token
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"mcp server on http://{host}:{port}  ({len(TOOLS)} tools)")
    print("  bearer auth on; execute defaults to a dry run")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m tools.mcp_server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args(argv)
    return serve(args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
