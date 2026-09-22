"""The work item this team passes around, and where it is stored.

A content task is whatever Quenton hands over — a file of copy, a folder of
images, or a line of Slack asking for a banner pulled from HK. It arrives with
almost no structure, so `ContentTask` keeps it deliberately loose and records
what is missing rather than inventing it. `blocking_gaps()` is what stops a
worker drafting a change out of assumptions.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core import config
from agents.marketing_onsite import surfaces

STORE = config.REVIEW_DIR / "content_tasks"

NEW = "NEW"
CLAIMED = "CLAIMED"
DRAFTED = "DRAFTED"
NEEDS_INPUT = "NEEDS_INPUT"
APPROVED = "APPROVED"
APPLIED = "APPLIED"
REJECTED = "REJECTED"
STATES = (NEW, CLAIMED, DRAFTED, NEEDS_INPUT, APPROVED, APPLIED, REJECTED)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".docx", ".xlsx", ".json"}


@dataclass
class ContentTask:
    task_id: str
    #: Quenton's words, kept verbatim. Never paraphrased into the record.
    request: str
    surface: str = ""
    #: Markets to apply to. Empty means "not stated" — which is a gap, not "all".
    markets: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    copy_text: str = ""
    link: str = ""
    state: str = NEW
    worker: str = ""
    created: str = ""
    notes: str = ""
    #: Set by the worker once it has read the surface's SOPs.
    sops_read: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    # -- validation --------------------------------------------------------
    def blocking_gaps(self) -> list[str]:
        """What must be answered before a change can be drafted.

        Deliberately strict about markets: an unstated market list is the
        single most expensive mistake on this board — it is how a TH-only
        offer ends up on HK, which is exactly the ticket that started this
        team. So "not stated" never silently becomes "everywhere".
        """
        gaps: list[str] = []
        if not self.request.strip():
            gaps.append("the request itself is empty")
        if not self.surface:
            gaps.append("no Apollo surface named — say which module this lands in")
        else:
            try:
                surfaces.resolve(self.surface)
            except surfaces.UnknownSurface as exc:
                gaps.append(str(exc))
        if not self.markets:
            gaps.append("no markets named — which shops does this apply to? "
                        "(never assumed to be all of them)")
        else:
            unknown = [m for m in self.markets
                       if m.upper() not in surfaces.MARKETS]
            if unknown:
                gaps.append(f"markets not recognised: {', '.join(unknown)}; "
                            f"known are {', '.join(surfaces.MARKETS)}")
        missing = [a for a in self.assets if not Path(a).exists()]
        if missing:
            gaps.append(f"asset file(s) not found: {', '.join(missing)}")
        return gaps

    @property
    def ready(self) -> bool:
        return not self.blocking_gaps()


def new_id() -> str:
    return "c_" + uuid.uuid4().hex[:8]


def _store() -> Path:
    STORE.mkdir(parents=True, exist_ok=True)
    return STORE


def save(task: ContentTask) -> Path:
    path = _store() / f"{task.task_id}.json"
    path.write_text(task.to_json(), encoding="utf-8")
    return path


def load(task_id: str) -> ContentTask:
    path = _store() / f"{task_id}.json"
    return ContentTask(**json.loads(path.read_text(encoding="utf-8")))


def all_tasks() -> list[ContentTask]:
    return [ContentTask(**json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(_store().glob("c_*.json"))]


def in_state(*states: str) -> list[ContentTask]:
    return [t for t in all_tasks() if t.state in states]


# -- intake ---------------------------------------------------------------

_MARKET_RE = re.compile(
    r"\b(" + "|".join(surfaces.MARKETS) + r")\b(?!\w)", re.I)


def sniff_markets(text: str) -> list[str]:
    """Markets named in free text. A hint for the lead to confirm, never a
    decision — `blocking_gaps` still demands they be set explicitly."""
    return sorted({m.upper() for m in _MARKET_RE.findall(text or "")})


def from_request(request: str, *, surface: str = "", markets=None,
                 assets=None, copy_text: str = "", link: str = "",
                 created: str = "") -> ContentTask:
    """Build a task from whatever Quenton sent.

    A dropped folder becomes assets; a dropped .txt/.md becomes copy. Nothing
    here decides a market or a surface — those are asked for, because guessing
    them is what puts the wrong banner in the wrong country.
    """
    resolved: list[str] = []
    for raw in (assets or []):
        p = Path(raw).expanduser()
        if p.is_dir():
            resolved += [str(f) for f in sorted(p.iterdir())
                         if f.suffix.lower() in IMAGE_SUFFIXES]
        else:
            resolved.append(str(p))

    text = copy_text
    if not text:
        for raw in list(resolved):
            p = Path(raw)
            if p.suffix.lower() in {".txt", ".md"} and p.exists():
                text = p.read_text(encoding="utf-8", errors="replace")
                resolved.remove(raw)
                break

    return ContentTask(
        task_id=new_id(),
        request=request,
        surface=surface,
        markets=[m.upper() for m in (markets or [])],
        assets=resolved,
        copy_text=text,
        link=link,
        created=created,
    )
