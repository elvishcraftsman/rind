"""Visit history ('frecency') used by the Frequent list and the jump dialog."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from . import config


def load_visits() -> dict[str, dict]:
    """Normalise visits.json: values are {"n": count, "t": last-visit time}.
    Older versions stored a bare count."""
    out: dict[str, dict] = {}
    for k, v in config.read_json(config.VISITS_FILE, {}).items():
        if isinstance(v, int):
            out[k] = {"n": v, "t": 0.0}
        elif isinstance(v, dict) and "n" in v:
            try:
                out[k] = {"n": int(v["n"]), "t": float(v.get("t", 0))}
            except (TypeError, ValueError):
                pass
    return out


def record(visits: dict[str, dict], path: Path) -> None:
    e = visits.setdefault(str(path), {"n": 0, "t": 0.0})
    e["n"] += 1
    e["t"] = time.time()
    config.write_json(config.VISITS_FILE, visits)


def frecency(entry: dict, now: Optional[float] = None) -> float:
    """Visit count weighted by how recently the folder was used."""
    now = time.time() if now is None else now
    t = entry.get("t", 0.0)
    age = now - t if t else float("inf")
    mult = 4 if age < 3600 else 2 if age < 86400 else 0.5 if age < 7 * 86400 else 0.25
    return entry.get("n", 0) * mult


def rank_jump(visits: dict[str, dict], query: str, limit: int = 30,
              exists: Callable[[str], bool] = os.path.isdir) -> list[Path]:
    """Folders whose path contains every word of query, best frecency first."""
    terms = query.casefold().split()
    scored = []
    for k, e in visits.items():
        low = k.casefold()
        if all(t in low for t in terms):
            s = frecency(e)
            if terms and terms[-1] in Path(k).name.casefold():
                s *= 2
            scored.append((s, k))
    scored.sort(reverse=True)
    out: list[Path] = []
    for _s, k in scored:
        if exists(k):
            out.append(Path(k))
            if len(out) >= limit:
                break
    return out


def top_places(visits: dict[str, dict], exclude: set[Path], n: int = 5) -> list[Path]:
    return [p for p in rank_jump(visits, "", limit=n + len(exclude) + 5) if p not in exclude][:n]


def tilde(p: Path) -> str:
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s
