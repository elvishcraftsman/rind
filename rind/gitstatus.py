"""Read-only git status for the current folder (never modifies the repository)."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

UNMERGED = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


@dataclass
class GitInfo:
    branch: str
    root: Path
    status: dict[Path, str] = field(default_factory=dict)  # absolute real path -> two-letter code

    def code_for(self, real_dir: str, name: str, is_dir: bool) -> str:
        p = Path(real_dir) / name
        if not is_dir:
            return self.status.get(p, "")
        direct = self.status.get(p)
        if direct:
            return direct
        best = ""
        for q, c in self.status.items():
            if p in q.parents:
                if c != "??":
                    return "M"
                best = "??"
        return best


def _run(base: list[str], args: list[str], timeout: float) -> Optional[bytes]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        r = subprocess.run(base + args, capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def parse_status(data: bytes, root: Path) -> tuple[str, dict[Path, str]]:
    branch = ""
    status: dict[Path, str] = {}
    entries = data.decode("utf-8", errors="replace").split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if not e:
            continue
        if e.startswith("## "):
            b = e[3:]
            if b.startswith("No commits yet on "):
                b = b[len("No commits yet on "):]
            elif b.startswith("HEAD (no branch)"):
                b = "detached HEAD"
            branch = b.split("...")[0].split(" [")[0]
            continue
        xy, rel = e[:2], e[3:]
        if "R" in xy or "C" in xy:
            i += 1  # the next entry is the rename's original path
        if xy in UNMERGED:
            xy = "UU"
        status[root / rel.rstrip("/")] = xy
    return branch, status


def git_status(path: Path, timeout: float = 3.0) -> Optional[GitInfo]:
    """Status of the repository containing `path`, or None (no git, no repo, too slow)."""
    if not shutil.which("git"):
        return None
    base = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(path)]
    top = _run(base, ["rev-parse", "--show-toplevel"], timeout)
    if not top:
        return None
    root = Path(os.path.realpath(top.decode(errors="replace").strip()))
    data = _run(base, ["status", "--porcelain=v1", "-b", "-z", "--untracked-files=normal"], timeout)
    if data is None:
        return None
    branch, status = parse_status(data, root)
    return GitInfo(branch or "?", root, status)
