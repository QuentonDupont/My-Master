"""The saved query library — the board owner's Queries repo, used as templates.

Each template is one `.sql` file under `<QUERIES_REPO>/catalog/`, headed by comment lines:

    -- id: return_by_id
    -- title: A return, its lines and history
    -- db: spanky                 (spanky | henry)
    -- kind: read                 (read: run by the investigator | write: shown to a human)
    -- params: rma_ids:ints       (name:type, comma-separated; types int, ints, str)
    -- tags: returns, rma
    SELECT ... WHERE id_order_return IN ({{rma_ids}});

`read` templates are the evidence the investigator runs; `write` templates are fixes it
renders into the packet for a person to run — never executed here. Values are rendered
by type: ints are checked to be integers, strings are quoted and escaped, so a value
lifted from a ticket can never change the shape of the statement.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from core import config

_HEADER = re.compile(r"^--\s*(\w+)\s*:\s*(.*?)\s*$")
_PARAM = re.compile(r"\{\{\s*(\w+)\s*\}\}")
TYPES = ("int", "ints", "str")


def repo_path() -> pathlib.Path:
    return pathlib.Path(config.env("QUERIES_REPO", str(pathlib.Path.home() / "Queries"))).expanduser()


class RenderError(ValueError):
    pass


@dataclass
class Template:
    id: str
    title: str
    db: str
    kind: str
    params: dict[str, str]
    sql: str
    tags: list[str] = field(default_factory=list)
    path: str = ""

    def render(self, **values) -> str:
        missing = [p for p in self.params if p not in values]
        if missing:
            raise RenderError(f"{self.id}: missing {', '.join(missing)}")

        def sub(m):
            name = m.group(1)
            if name not in self.params:
                raise RenderError(f"{self.id}: {{{{{name}}}}} is not declared in params")
            return _literal(self.params[name], values[name], f"{self.id}.{name}")

        return _PARAM.sub(sub, self.sql).strip()


def _literal(kind: str, value, where: str) -> str:
    if kind == "int":
        return str(_int(value, where))
    if kind == "ints":
        vals = [_int(v, where) for v in (value if isinstance(value, (list, tuple, set)) else [value])]
        if not vals:
            raise RenderError(f"{where}: empty list")
        return ", ".join(str(v) for v in vals)
    if kind == "str":
        return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"
    raise RenderError(f"{where}: unknown type {kind}")


def _int(value, where: str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value).strip()):
        raise RenderError(f"{where}: {value!r} is not an integer")
    return int(str(value).strip())


def parse(text: str, path: str = "") -> Template:
    meta, body = {}, []
    for line in text.splitlines():
        m = _HEADER.match(line) if not body else None
        if m:
            meta[m.group(1).lower()] = m.group(2)
        elif line.strip() or body:
            body.append(line)
    for key in ("id", "db", "kind"):
        if not meta.get(key):
            raise RenderError(f"{path or 'template'}: missing '-- {key}:' header")
    if meta["kind"] not in ("read", "write"):
        raise RenderError(f"{meta['id']}: kind must be read or write")
    params = {}
    for part in filter(None, (p.strip() for p in meta.get("params", "").split(","))):
        name, _, kind = part.partition(":")
        kind = kind.strip() or "int"
        if kind not in TYPES:
            raise RenderError(f"{meta['id']}: param {name} has unknown type {kind}")
        params[name.strip()] = kind
    return Template(id=meta["id"], title=meta.get("title", meta["id"]), db=meta["db"],
                    kind=meta["kind"], params=params, sql="\n".join(body),
                    tags=[t.strip() for t in meta.get("tags", "").split(",") if t.strip()],
                    path=path)


def load(root: pathlib.Path | None = None) -> dict[str, Template]:
    root = (root or repo_path()) / "catalog"
    out: dict[str, Template] = {}
    if not root.is_dir():
        return out
    for f in sorted(root.glob("*.sql")):
        t = parse(f.read_text(encoding="utf-8"), str(f))
        if t.id in out:
            raise RenderError(f"duplicate template id {t.id} ({f} and {out[t.id].path})")
        out[t.id] = t
    return out
