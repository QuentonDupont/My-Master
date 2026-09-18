"""Historian — the retrieval and SOP-drafting layer.

Standing agent, no workers: everything here is a query against the corpus index.
It never writes to Jira and never decides anything; it hands evidence to the
Jira Leader's workers, who put it in a proposal for the human.

    python -m agents.historian.retrieval similar "stock not syncing after import"
    python -m agents.historian.retrieval duplicates PESD1-11274
    python -m agents.historian.retrieval assignee "stock sync broken" --labels stock-sync
    python -m agents.historian.retrieval sop PESD1-11274
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field

from core import config, log
from corpus.index import Corpus, keywords

LOG = log.get("historian")

#: a duplicate has to be this similar before we will even propose the link.
DUPLICATE_THRESHOLD = 0.62
#: below this, a hit is noise — citing it would make the proposal look researched
#: when it is not.
MIN_RELEVANCE = 0.25
#: closed/resolved-ish statuses. Anything not here counts as open.
RESOLVED_HINTS = ("done", "closed", "resolved", "complete", "cancelled", "declined")


@dataclass
class Retrieval:
    """Everything the Historian found for one ticket."""
    duplicates: list[dict] = field(default_factory=list)
    similar_resolved: list[dict] = field(default_factory=list)
    sops: list[dict] = field(default_factory=list)
    github: list[dict] = field(default_factory=list)
    components: list[str] = field(default_factory=list)

    def evidence(self, limit: int = 8) -> list[dict]:
        """Shaped for `Proposal.evidence` — every item says why it is here."""
        out: list[dict] = []
        for doc in self.sops:
            out.append({"type": "sop", "ref": doc["ref"],
                        "why": f"approved SOP, {doc['similarity']:.0%} match on the symptom"})
        for doc in self.similar_resolved:
            who = doc.get("assignee") or "unknown"
            res = doc.get("resolution") or doc.get("status") or "resolved"
            out.append({"type": "jira", "ref": doc["ref"],
                        "why": f"same symptom, {res.lower()} by {who} "
                               f"({doc['similarity']:.0%} match)"})
        for doc in self.github:
            out.append({"type": "github", "ref": doc["ref"],
                        "why": f"code change referencing {doc['title'][:60]}"})
        return out[:limit]


class Historian:
    def __init__(self, corpus: Corpus | None = None) -> None:
        self.corpus = corpus or Corpus()
        self._own = corpus is None

    def close(self) -> None:
        if self._own:
            self.corpus.close()

    def __enter__(self) -> "Historian":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- primitives --------------------------------------------------------
    @staticmethod
    def _is_resolved(doc: dict) -> bool:
        if doc.get("resolution"):
            return True
        status = (doc.get("status") or "").lower()
        return any(h in status for h in RESOLVED_HINTS)

    def duplicates(self, text: str, *, exclude_ref: str | None = None,
                   limit: int = 5, threshold: float = DUPLICATE_THRESHOLD) -> list[dict]:
        """Similar tickets that are still OPEN on the intake board."""
        open_statuses = tuple(config.boards()["intake"]["open_statuses"])
        hits = self.corpus.search(
            text, limit=limit * 3, source_types=("jira",),
            project=config.intake_project(), statuses=open_statuses,
            exclude_refs=(exclude_ref,) if exclude_ref else (),
        )
        return [h for h in hits if h.get("similarity", 0) >= threshold][:limit]

    def similar_resolved(self, text: str, *, exclude_ref: str | None = None,
                         limit: int = 5) -> list[dict]:
        hits = self.corpus.search(text, limit=limit * 4, source_types=("jira",),
                                  exclude_refs=(exclude_ref,) if exclude_ref else ())
        return [h for h in hits
                if self._is_resolved(h) and h.get("similarity", 0) >= MIN_RELEVANCE
                ][:limit]

    def sops(self, text: str, limit: int = 3) -> list[dict]:
        hits = self.corpus.search(text, limit=limit, source_types=("sop", "confluence"))
        return [h for h in hits if h.get("similarity", 0) >= MIN_RELEVANCE]

    def github_for(self, refs: list[str], limit: int = 4) -> list[dict]:
        if not refs:
            return []
        hits = self.corpus.search(" ".join(refs), limit=limit, source_types=("github",))
        return [h for h in hits if h.get("similarity", 0) >= MIN_RELEVANCE]

    def components_for(self, text: str) -> list[str]:
        """Map free text onto the component vocabulary in config/repos.yml."""
        blob = (text or "").lower()
        scored = []
        for component, words in (config.repos().get("component_keywords") or {}).items():
            hits = sum(1 for w in words if w in blob)
            if hits:
                scored.append((hits, component))
        scored.sort(reverse=True)
        return [c for _, c in scored]

    # -- the one call a worker makes ---------------------------------------
    def research(self, ticket_key: str, summary: str, description: str) -> Retrieval:
        text = f"{summary}\n\n{description}"
        dupes = self.duplicates(text, exclude_ref=ticket_key)
        similar = self.similar_resolved(text, exclude_ref=ticket_key)
        sops = self.sops(text)
        gh = self.github_for([d["ref"] for d in similar])
        comps = self.components_for(text)
        LOG.info("historian.research", ticket=ticket_key, duplicates=len(dupes),
                 similar=len(similar), sops=len(sops), github=len(gh), components=comps)
        return Retrieval(duplicates=dupes, similar_resolved=similar, sops=sops,
                         github=gh, components=comps)

    # -- assignee ranking --------------------------------------------------
    def assignee_candidates(self, text: str, *, labels: list[str] | None = None,
                            components: list[str] | None = None,
                            limit: int = 3, window: int = 10) -> list[dict]:
        """Rank PRDT developers by recent closed work on this component.

        Returns a ranked shortlist — never a single guess (proposal schema).
        """
        comps = list(components or []) or self.components_for(text)
        comps += [l for l in (labels or []) if l not in comps]
        dev = config.dev_project()
        rows = self.corpus.conn.execute(
            """SELECT assignee, components, labels, title, updated, resolution
               FROM documents
               WHERE source_type = 'jira' AND project = ? AND assignee IS NOT NULL
                     AND assignee != '' AND (resolution IS NOT NULL OR lower(status) IN
                         ('done','closed','resolved'))
               ORDER BY updated DESC LIMIT 400""",
            (dev,),
        ).fetchall()

        def matches(row) -> str | None:
            haystack = f"{row['components']} {row['labels']} {row['title']}".lower()
            for comp in comps:
                if comp and comp.lower() in haystack:
                    return comp
            return None

        matched = [(r, c) for r in rows if (c := matches(r))]
        pool = matched[:max(window * 4, 40)] if matched else list(rows)[:window * 4]
        counts: dict[str, int] = {}
        why: dict[str, str] = {}
        for row, comp in (matched or [(r, None) for r in rows[:window * 4]]):
            name = row["assignee"]
            counts[name] = counts.get(name, 0) + 1
            why.setdefault(name, comp or "recent PRDT work")

        total = len(pool) or 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

        # Backfill so the human always gets a shortlist, never a single name.
        if len(ranked) < limit:
            overall: dict[str, int] = {}
            for row in rows[: window * 4]:
                overall[row["assignee"]] = overall.get(row["assignee"], 0) + 1
            for name, count in sorted(overall.items(), key=lambda kv: (-kv[1], kv[0])):
                if len(ranked) >= limit:
                    break
                if name in counts:
                    continue
                counts[name] = count
                why[name] = "recent PRDT work"
                ranked.append((name, count))

        out = []
        for name, count in ranked:
            comp = why[name]
            reason = (f"closed {count} of the last {total} {dev} tickets with "
                      f"component={comp}" if comp != "recent PRDT work"
                      else f"closed {count} of the last {len(rows[: window * 4])} "
                           f"{dev} tickets overall (no {dev} history on this component)")
            out.append({"assignee": name, "count": count, "reason": reason,
                        "component": comp})
        LOG.info("historian.assignees", candidates=[o["assignee"] for o in out],
                 components=comps)
        return out

    # -- SOP drafting ------------------------------------------------------
    def draft_sop(self, ticket_key: str, summary: str, description: str,
                  resolution: str = "") -> str:
        """Draft a Confluence-ready SOP page. The human approves before it lands."""
        research = self.research(ticket_key, summary, description)
        comps = research.components or ["uncategorised"]
        terms = keywords(f"{summary} {description}", 8)
        lines = [
            f"# SOP: {summary.strip()}",
            "",
            f"*Drafted from {ticket_key}. Not yet approved — a human must review and "
            f"publish this to Confluence.*",
            "",
            "## Symptom",
            "",
            (description or summary).strip()[:1200],
            "",
            "## Scope",
            "",
            f"- Component(s): {', '.join(comps)}",
            f"- Signals: {', '.join(terms)}",
            "",
            "## Resolution",
            "",
            resolution.strip() or "_TODO: paste the resolution that actually worked._",
            "",
            "## Precedent",
            "",
        ]
        if research.similar_resolved:
            for doc in research.similar_resolved:
                lines.append(f"- {doc['ref']} — {doc['title'][:90]} "
                             f"({doc.get('resolution') or doc.get('status')}, "
                             f"{doc.get('assignee') or 'unassigned'})")
        else:
            lines.append("- No precedent found — this is a novel resolution.")
        lines += ["", "## Next time", "",
                  "1. Confirm the symptom matches the Scope above.",
                  "2. Apply the Resolution.",
                  "3. If it does not resolve, escalate with the component owner."]
        return "\n".join(lines)


def _text_of(ticket_key: str, corpus: Corpus) -> tuple[str, str]:
    doc = corpus.by_ref(ticket_key)
    if not doc:
        raise SystemExit(f"{ticket_key} is not in the corpus — run corpus.export/index")
    return doc["title"], doc["body"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.historian.retrieval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_s = sub.add_parser("similar")
    p_s.add_argument("text")
    p_d = sub.add_parser("duplicates")
    p_d.add_argument("ticket_or_text")
    p_a = sub.add_parser("assignee")
    p_a.add_argument("text")
    p_a.add_argument("--labels", nargs="*", default=[])
    p_r = sub.add_parser("research")
    p_r.add_argument("ticket")
    p_o = sub.add_parser("sop")
    p_o.add_argument("ticket")
    p_o.add_argument("--resolution", default="")
    args = ap.parse_args(argv)

    with Historian() as hist:
        if args.cmd == "similar":
            for doc in hist.similar_resolved(args.text):
                print(f"{doc['similarity']:.3f}  {doc['ref']:<14} {doc['title'][:70]}")
        elif args.cmd == "duplicates":
            arg = args.ticket_or_text
            if re.match(r"^[A-Z]+-\d+$", arg):
                title, body = _text_of(arg, hist.corpus)
                hits = hist.duplicates(f"{title}\n{body}", exclude_ref=arg)
            else:
                hits = hist.duplicates(arg)
            for doc in hits:
                print(f"{doc['similarity']:.3f}  {doc['ref']:<14} {doc['status']:<22} "
                      f"{doc['title'][:60]}")
        elif args.cmd == "assignee":
            print(json.dumps(hist.assignee_candidates(args.text, labels=args.labels),
                             indent=2))
        elif args.cmd == "research":
            title, body = _text_of(args.ticket, hist.corpus)
            res = hist.research(args.ticket, title, body)
            print(json.dumps({"components": res.components,
                              "duplicates": [d["ref"] for d in res.duplicates],
                              "similar": [d["ref"] for d in res.similar_resolved],
                              "sops": [d["ref"] for d in res.sops],
                              "evidence": res.evidence()}, indent=2))
        else:
            title, body = _text_of(args.ticket, hist.corpus)
            print(hist.draft_sop(args.ticket, title, body, args.resolution))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
