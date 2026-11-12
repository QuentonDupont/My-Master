"""Where the code for an issue lives, in the local clones of the Pomelo repos.

Greps (read-only) for the terms a playbook names — table names, status constants,
controller names — and returns file:line hits, so the packet can say *which code*
produces the behaviour, not only which data is wrong.

Clones (.env): INVESTIGATE_CODE_ROOTS, a comma-separated list of `name=path`.
Default: the clones on the board owner's machine.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

from core import config

DEFAULT_ROOTS = {
    "apollo": "~/pomelo/apollo",
    "api": "~/pomelo/api",
    "www": "~/pomelo/www",
    "henry": "~/work/henry",
}
_SKIP = ("node_modules", "vendor", ".git", ".claude", "worktrees", "cache", "dist", "build", "tests", "test",
         "migrations", "seeds", "factories")
_EXT = ("*.php", "*.js", "*.ts", "*.tpl", "*.py", "*.sql")
MAX_HITS = 8


def roots() -> dict[str, pathlib.Path]:
    raw = config.env("INVESTIGATE_CODE_ROOTS", "")
    pairs = dict(p.split("=", 1) for p in raw.split(",") if "=" in p) if raw else DEFAULT_ROOTS
    return {name.strip(): pathlib.Path(path.strip()).expanduser() for name, path in pairs.items()}


def search(term: str, repos: list[str] | None = None, *, max_hits: int = MAX_HITS) -> list[str]:
    """`repo:path:line: text` hits for a fixed-string term, newest repos first."""
    tool = shutil.which("grep")
    if not tool:
        return []
    hits: list[str] = []
    for name, root in roots().items():
        if repos and name not in repos:
            continue
        if not root.is_dir():
            continue
        cmd = [tool, "-rnIF", "--max-count=2"]
        cmd += [f"--include={e}" for e in _EXT]
        cmd += [f"--exclude-dir={d}" for d in _SKIP]
        cmd += ["--", term, str(root)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        except subprocess.TimeoutExpired:
            continue
        for line in out.splitlines():
            path, _, rest = line.partition(":")
            rel = pathlib.Path(path).relative_to(root) if path.startswith(str(root)) else path
            hits.append(f"{name}:{rel}:{rest[:180]}")
            if len(hits) >= max_hits:
                return hits
    return hits
