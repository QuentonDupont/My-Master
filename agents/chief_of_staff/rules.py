"""Weekly rule proposals, clustered from knowledge/corrections.jsonl.

The models do not learn from being corrected, so the Chief of Staff reads what
the human actually changed and proposes rules. It writes a PROPOSAL file into
review/ — never knowledge/rules.md. Only the human adds a rule.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
from collections import Counter, defaultdict

from core import config, corrections, log, proposals

LOG = log.get("chief_of_staff")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"

#: how many times a pattern must appear before it is worth proposing as a rule.
MIN_SUPPORT = 2


def _ticket_context(proposal_id: str) -> dict:
    try:
        p = proposals.load(proposal_id)
    except FileNotFoundError:
        return {}
    return {"ticket": p.ticket, "classification": p.classification,
            "labels": (p.clone.labels if p.clone else [])}


def _phrase_changes(entries: list[dict]) -> list[tuple[str, str, int]]:
    """Fragments the human consistently removes from, or adds to, comments."""
    removed: Counter = Counter()
    added: Counter = Counter()
    for e in entries:
        was, became = str(e.get("was") or ""), str(e.get("became") or "")
        matcher = difflib.SequenceMatcher(None, was, became)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("delete", "replace"):
                frag = was[i1:i2].strip(" .,\n")
                if len(frag) >= 8:
                    removed[frag] += 1
            if tag in ("insert", "replace"):
                frag = became[j1:j2].strip(" .,\n")
                if len(frag) >= 8:
                    added[frag] += 1
    out = []
    for frag, count in removed.most_common(5):
        replacement = added.most_common(1)[0][0] if added else ""
        out.append((frag, replacement, count))
    return out


def cluster(entries: list[dict] | None = None, min_support: int = MIN_SUPPORT) -> list[dict]:
    entries = corrections.load() if entries is None else entries
    by_field: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_field[e["field"]].append(e)

    proposals_out: list[dict] = []

    # 1. assignee overrides -> routing rules
    routing: Counter = Counter()
    routing_context: dict[tuple[str, str], list[str]] = defaultdict(list)
    for e in by_field.get("clone.assignee", []):
        pair = (str(e.get("was")), str(e.get("became")))
        routing[pair] += 1
        ctx = _ticket_context(e["proposal_id"])
        routing_context[pair].append(
            f"{ctx.get('ticket', e['proposal_id'])}"
            + (f" ({', '.join(ctx['labels'])})" if ctx.get("labels") else ""))
    for (was, became), count in routing.items():
        if count >= min_support:
            proposals_out.append({
                "kind": "routing",
                "support": count,
                "rule": f"Route these to **{became}**, not {was}.",
                "evidence": routing_context[(was, became)],
                "why": f"the human reassigned {was} -> {became} "
                       f"{_plural(count, 'time')}",
            })

    # 2. comment phrasing
    phrases = _phrase_changes(by_field.get("proposed_comment", []))
    for frag, replacement, count in phrases:
        if count >= min_support:
            rule = f"Do not write “{frag}” in a PESD1 comment."
            if replacement:
                rule += f" Prefer “{replacement}”."
            proposals_out.append({"kind": "phrasing", "support": count, "rule": rule,
                                  "evidence": [], "why": "edited out of comments repeatedly"})

    # 3. classification flips
    flips = Counter((str(e.get("was")), str(e.get("became")))
                    for e in by_field.get("classification", []))
    for (was, became), count in flips.items():
        if count >= min_support:
            proposals_out.append({
                "kind": "classification", "support": count,
                "rule": f"Tickets like these are **{became}**, not {was}.",
                "evidence": [e["proposal_id"] for e in by_field["classification"]],
                "why": f"reclassified {was} -> {became} "
                       f"{_plural(count, 'time')}"})

    # 4. rejection themes
    reject_terms: Counter = Counter()
    for e in by_field.get("__rejected__", []):
        for word in re.findall(r"[a-z]{4,}", str(e.get("reason") or "").lower()):
            if word not in ("this", "that", "with", "from", "just", "because"):
                reject_terms[word] += 1
    themes = [w for w, c in reject_terms.most_common(4) if c >= min_support]
    if themes:
        proposals_out.append({
            "kind": "rejection-theme", "support": max(reject_terms.values()),
            "rule": f"Check for: {', '.join(themes)} — recurring reason for rejection.",
            "evidence": [e["proposal_id"] for e in by_field["__rejected__"]],
            "why": "clustered from rejection reasons"})

    # 5. anything else that keeps being edited
    for field, group in by_field.items():
        if field in ("clone.assignee", "proposed_comment", "classification",
                     "__rejected__"):
            continue
        if len(group) >= min_support:
            proposals_out.append({
                "kind": "field-churn", "support": len(group),
                "rule": f"`{field}` is edited often — the worker's default for it is "
                        f"probably wrong.",
                "evidence": [e["proposal_id"] for e in group][:6],
                "why": f"{_plural(len(group), 'edit')} to the same field"})

    proposals_out.sort(key=lambda r: -r["support"])
    LOG.info("rules.clustered", proposals=len(proposals_out), corrections=len(entries))
    return proposals_out


def render(rule_proposals: list[dict]) -> str:
    ts = dt.datetime.now().strftime("%Y-%m-%d")
    out = [f"# Proposed rules — {ts}", "",
           "Generated from `knowledge/corrections.jsonl`. **Nothing here is active.**",
           "Paste the ones you agree with into `knowledge/rules.md`; they are then",
           "injected into every worker's context.", ""]
    if not rule_proposals:
        out += ["No pattern has enough support yet "
                f"(threshold: {MIN_SUPPORT} occurrences)."]
        return "\n".join(out)
    for i, rule in enumerate(rule_proposals, 1):
        out += [f"## {i}. {rule['rule']}", "",
                f"- Kind: `{rule['kind']}` · Support: {rule['support']}",
                f"- Why: {rule['why']}"]
        if rule["evidence"]:
            out.append(f"- Seen on: {', '.join(str(e) for e in rule['evidence'][:6])}")
        out.append("")
    return "\n".join(out)


def write_proposal_file(rule_proposals: list[dict] | None = None) -> str:
    rule_proposals = cluster() if rule_proposals is None else rule_proposals
    config.REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REVIEW_DIR / f"rule_proposals_{dt.datetime.now():%Y-%m-%d_%H%M}.md"
    path.write_text(render(rule_proposals), encoding="utf-8")
    return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.chief_of_staff.rules")
    ap.add_argument("--min-support", type=int, default=MIN_SUPPORT)
    ap.add_argument("--write", action="store_true", help="write the file into review/")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    clustered = cluster(min_support=args.min_support)
    if args.json:
        print(json.dumps(clustered, indent=2))
    elif args.write:
        print(write_proposal_file(clustered))
    else:
        print(render(clustered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
