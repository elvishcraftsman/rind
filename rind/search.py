"""Name matching (for the filter box) and recursive file search."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

from .fileops import natural_key

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__"}


def match_name(query: str, name: str) -> bool:
    """All space-separated terms must appear in the name (case-insensitive).

    A single term of 3+ characters also matches as a fuzzy subsequence.
    """
    q = query.strip().casefold()
    if not q:
        return True
    hay = name.casefold()
    terms = q.split()
    if all(t in hay for t in terms):
        return True
    if len(terms) == 1 and len(terms[0]) >= 3:
        it = iter(hay)
        return all(c in it for c in terms[0])
    return False


def _rank(results: list[Path], root: Path, query: str) -> list[Path]:
    q0 = query.strip().casefold().split()[0] if query.strip() else ""

    def key(p: Path):
        name = p.name.casefold()
        tier = 0 if name.startswith(q0) else (1 if q0 in name else 2)
        rel = p.relative_to(root)
        return (tier, len(rel.parts), natural_key(str(rel)))

    return sorted(results, key=key)


def find_files(root: Path, query: str, show_hidden: bool, cancelled: Callable[[], bool],
               limit: int = 300, budget: float = 5.0) -> tuple[list[Path], bool]:
    """Walk root for names matching query. Returns (results, truncated)."""
    if not query.strip():
        return [], False
    results: list[Path] = []
    start = time.monotonic()
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if cancelled():
            return [], False
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and (show_hidden or not d.startswith("."))]
        for name in dirnames + filenames:
            if not show_hidden and name.startswith("."):
                continue
            if match_name(query, name):
                results.append(Path(dirpath) / name)
                if len(results) >= limit:
                    return _rank(results, root, query), True
        if time.monotonic() - start > budget:
            truncated = True
            break
    return _rank(results, root, query), truncated
