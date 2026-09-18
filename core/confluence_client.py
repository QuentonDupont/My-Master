"""Read-only Confluence client.

CLAUDE.md makes Confluence and the SOP library the Historian's highest-weight
source, above raw ticket history. This reads pages so they can be indexed; there
is no write method here and none is to be added — the SOP flow ends with a human
publishing the page, not the system.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from typing import Iterator

from core import config, log
from core.jira_client import _Base

LOG = log.get("confluence")

#: Confluence storage format is XHTML with macros. Keep the text, drop the markup.
SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
BLANKS_RE = re.compile(r"\n{3,}")
#: block-level tags become line breaks so lists and headings stay readable
BLOCK_RE = re.compile(r"</(p|div|li|tr|h[1-6]|td|th|blockquote)>|<br\s*/?>", re.I)


def to_text(storage: str) -> str:
    """Storage-format XHTML to readable plain text."""
    text = SCRIPT_RE.sub(" ", storage or "")
    text = CDATA_RE.sub(r"\1", text)
    text = BLOCK_RE.sub("\n", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return BLANKS_RE.sub("\n\n", text).strip()


class ConfluenceReadClient(_Base):
    """Read-only. No POST, PUT or DELETE anywhere in this class."""

    def spaces(self, keys: list[str] | None = None, limit: int = 100) -> list[dict]:
        params: dict = {"limit": limit}
        if keys:
            params["keys"] = ",".join(keys)
        return self._request("GET", "/wiki/api/v2/spaces", params=params).get(
            "results", [])

    def space(self, key: str) -> dict | None:
        found = self.spaces(keys=[key], limit=5)
        return found[0] if found else None

    def _paged(self, path: str, params: dict) -> Iterator[dict]:
        page = self._request("GET", path, params=params)
        while True:
            yield from page.get("results", [])
            nxt = (page.get("_links") or {}).get("next")
            if not nxt:
                return
            # `next` is a path with its own cursor; the site prefix is already
            # in base_url.
            page = self._request("GET", nxt.replace("/wiki", "/wiki", 1))

    def pages(self, space_id: str, limit: int | None = None) -> list[dict]:
        out = []
        for page in self._paged(f"/wiki/api/v2/spaces/{space_id}/pages",
                                {"limit": 100, "status": "current"}):
            out.append(page)
            if limit and len(out) >= limit:
                break
        LOG.info("confluence.pages", space_id=space_id, count=len(out))
        return out

    def page_body(self, page_id: str) -> dict:
        return self._request("GET", f"/wiki/api/v2/pages/{page_id}",
                             params={"body-format": "storage"})

    def page_url(self, page: dict) -> str:
        webui = ((page.get("_links") or {}).get("webui")
                 or f"/wiki/spaces/_/pages/{page.get('id')}")
        return f"{self.base_url}/wiki{webui}" if not webui.startswith("/wiki") \
            else f"{self.base_url}{webui}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.confluence_client")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("spaces")
    p_p = sub.add_parser("pages")
    p_p.add_argument("space")
    p_p.add_argument("--limit", type=int, default=30)
    p_b = sub.add_parser("page")
    p_b.add_argument("page_id")
    args = ap.parse_args(argv)

    client = ConfluenceReadClient()
    if args.cmd == "spaces":
        for s in client.spaces():
            print(f"{s['key']:<12} {s['id']:<12} {s['name']}")
    elif args.cmd == "pages":
        space = client.space(args.space)
        if not space:
            print(f"no space {args.space}")
            return 2
        for page in client.pages(space["id"], limit=args.limit):
            print(f"{page['id']:<12} {page['title']}")
    else:
        page = client.page_body(args.page_id)
        body = ((page.get("body") or {}).get("storage") or {}).get("value", "")
        print(json.dumps({"title": page.get("title"),
                          "text": to_text(body)[:2000]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
