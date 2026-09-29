"""Test sandbox: every test gets its own ledger, corpus, proposal store and logs."""
from __future__ import annotations

import contextlib
import json
import pathlib
import tempfile

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


@contextlib.contextmanager
def fixture_boards():
    """Point config at the fixture instance, not the live Pomelo one.

    Tuning config/boards.yml or config/repos.yml to the real Jira must never
    break the suite.
    """
    from core import config

    boards = config.load_yaml(FIXTURES / "boards.yml")
    repos = config.load_yaml(FIXTURES / "repos.yml")
    saved_boards, saved_repos = config.boards, config.repos
    config.boards = lambda: boards
    config.repos = lambda: repos
    try:
        yield boards
    finally:
        config.boards, config.repos = saved_boards, saved_repos


@contextlib.contextmanager
def sandbox():
    from core import config, proposals

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        saved = {
            "LEDGER_DB": config.LEDGER_DB, "CORPUS_DB": config.CORPUS_DB,
            "REVIEW_DIR": config.REVIEW_DIR, "LOG_DIR": config.LOG_DIR,
            "CORRECTIONS": config.CORRECTIONS, "KNOWLEDGE_DIR": config.KNOWLEDGE_DIR,
            "RULES_MD": config.RULES_MD, "STORE": proposals.STORE,
        }
        config.LEDGER_DB = root / "ledger.db"
        config.CORPUS_DB = root / "corpus.db"
        config.REVIEW_DIR = root / "review"
        config.LOG_DIR = root / "logs"
        config.KNOWLEDGE_DIR = root / "knowledge"
        config.CORRECTIONS = root / "knowledge" / "corrections.jsonl"
        # RULES_MD = KNOWLEDGE_DIR / "rules.md" is computed once at import time
        # in core/config.py, so reassigning KNOWLEDGE_DIR above does NOT move
        # it — it silently keeps pointing at the real repo's rules.md unless
        # redirected here too. Found live: a sandboxed test reading approved
        # rules picked up the real 2 approved rules from the actual repo.
        config.RULES_MD = root / "knowledge" / "rules.md"
        proposals.STORE = root / "review" / "proposals"
        for d in (config.REVIEW_DIR, config.LOG_DIR, config.KNOWLEDGE_DIR,
                  proposals.STORE):
            d.mkdir(parents=True, exist_ok=True)
        try:
            with fixture_boards():
                yield root
        finally:
            for key, value in saved.items():
                if key == "STORE":
                    proposals.STORE = value
                else:
                    setattr(config, key, value)


def load_fixture(name: str) -> list[dict]:
    return [json.loads(line) for line in
            (FIXTURES / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def issue(key: str, name: str = "inbox.jsonl") -> dict:
    return next(i for i in load_fixture(name) if i["key"] == key)


def build_corpus() -> None:
    """Index the fixture history into the sandboxed corpus db."""
    from corpus.index import Corpus, doc_from_jira, doc_from_markdown

    docs = [doc_from_jira(i) for i in load_fixture("pesd1.jsonl")]
    docs += [doc_from_jira(i) for i in load_fixture("prdt.jsonl")]
    sop_dir = FIXTURES.parent.parent / "knowledge" / "sops"
    docs += [doc_from_markdown(p) for p in sorted(sop_dir.glob("*.md"))]
    with Corpus() as corpus:
        corpus.add_many(docs)
        corpus.rebuild_term_stats()
