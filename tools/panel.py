"""The review page, served from this machine instead of a published artifact.

    make panel                      # http://localhost:8765
    python3 -m tools.panel --port 8765 --host 0.0.0.0

`tools/review_app.html` was written against `claude.use("db")`. Its whole use of
that API is two calls — `collection(name).onSnapshot(cb)` and
`doc(path).update(fields)` — so this serves the page unchanged with a small shim
injected ahead of it that implements those two against local HTTP. The UI is not
copied, forked or edited; if the page changes, this keeps working.

Where the data comes from
-------------------------
`agents.jira_leader.mobile.export` and `agents.slack_leader.mobile.export`
already write exactly the documents the page expects. This runs them on each
load and serves the result, so the panel can never drift from what the phone
flow sees.

Decisions go back through `mobile.apply`, which is the same path the batch file
uses: it records every edit in `knowledge/corrections.jsonl` and moves the
ledger. The panel has no Jira credentials and no execution path — approving here
marks a proposal APPROVED, exactly as editing the JSON by hand would.
Executing stays a separate, deliberate command.

Binding
-------
Defaults to 127.0.0.1. `--host 0.0.0.0` serves it to your phone on the same
wifi; it has no authentication, so only do that on a network you trust.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import config, log

LOG = log.get("panel")

PAGE = Path(__file__).resolve().parent / "review_app.html"

#: implements the two db calls review_app.html makes, against this server
SHIM = """
<script>
window.claude = window.claude || {};
window.claude.use = async function (name) {
  if (name !== "db") return null;
  const load = async () => (await fetch("/api/collections")).json();
  const subs = [];
  let cache = {};
  const push = async () => {
    try { cache = await load(); } catch (e) { return; }
    for (const [coll, fn] of subs) {
      const docs = (cache[coll] || []).map(d => ({
        id: d.id, exists: true, data: () => d.data,
        metadata: {fromCache: false, hasPendingWrites: false},
      }));
      fn({docs, size: docs.length, empty: !docs.length,
          docChanges: () => [], metadata: {fromCache: false}});
    }
  };
  setInterval(push, 4000);
  setTimeout(push, 0);
  return {
    collection: (coll) => ({
      onSnapshot: (next, _err) => { subs.push([coll, next]); push(); return () => {}; },
    }),
    doc: (path) => ({
      update: async (fields) => {
        const res = await fetch("/api/doc", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path, fields}),
        });
        if (!res.ok) throw Object.assign(new Error("write failed"),
                                         {code: "unavailable"});
        await push();
      },
    }),
  };
};
</script>
"""


def _documents() -> dict:
    """Every collection the page reads, freshly exported."""
    from agents.jira_leader import mobile as jira_mobile
    from agents.slack_leader import mobile as slack_mobile

    out: dict[str, list] = {"proposals": [], "slack_proposals": [],
                            "recommendations": [], "board_items": []}
    tmp = Path(tempfile.mkdtemp(prefix="panel-"))
    try:
        try:
            jira_mobile.export(tmp)
        except Exception as exc:
            LOG.warn("panel.jira_export_failed", error=str(exc)[:160])
        try:
            slack_mobile.export(tmp / "slack")
        except Exception as exc:
            LOG.warn("panel.slack_export_failed", error=str(exc)[:160])

        def collect(folder: Path, key: str) -> None:
            if not folder.is_dir():
                return
            for path in sorted(folder.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                out[key].append({"id": path.stem, "data": data})

        collect(tmp, "proposals")
        collect(tmp / "slack", "slack_proposals")
        collect(tmp / "recommendations", "recommendations")
        collect(tmp / "boards", "board_items")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _decide(path: str, fields: dict) -> dict:
    """Route one edit from the page to the store it belongs to."""
    collection, _, doc_id = path.partition("/")
    decision = {"proposal_id": doc_id,
                "decision": fields.get("decision", "pending"),
                "note": fields.get("note", ""),
                "edited_reply": fields.get("edited_reply", ""),
                "assignee_override": fields.get("assignee_override")}

    if collection == "proposals":
        from agents.jira_leader import mobile as jira_mobile
        return jira_mobile.apply([decision])
    if collection == "slack_proposals":
        from agents.slack_leader import mobile as slack_mobile
        return slack_mobile.apply([decision])
    if collection == "recommendations":
        from core import recommendations
        state = fields.get("state", "open")
        recommendations.resolve(doc_id, state)
        return {"recommendation": doc_id, "state": state}
    raise ValueError(f"nothing writes to {collection!r}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):     # quiet; we have our own logger
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            html = PAGE.read_text(encoding="utf-8")
            self._send(200, (SHIM + html).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if self.path == "/api/collections":
            self._send(200, json.dumps(_documents()).encode("utf-8"),
                       "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/api/doc":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = _decide(payload["path"], payload.get("fields") or {})
        except Exception as exc:
            LOG.warn("panel.write_failed", error=str(exc)[:160])
            self._send(400, json.dumps({"error": str(exc)}).encode(),
                       "application/json")
            return
        LOG.info("panel.decided", path=payload["path"])
        self._send(200, json.dumps(result).encode("utf-8"), "application/json")


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    where = "localhost" if host == "127.0.0.1" else host
    print(f"review panel on http://{where}:{port}  (ctrl-c to stop)")
    if host != "127.0.0.1":
        print("  reachable from your network — there is no authentication")
    print(f"  decisions land in {config.REVIEW_DIR} and "
          f"{config.CORRECTIONS.name}; nothing is written to Jira from here")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m tools.panel")
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 to reach it from your phone on the same wifi")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
