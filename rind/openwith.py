"""'Open with': installed applications for a file, from freedesktop .desktop files."""
from __future__ import annotations

import configparser
import mimetypes
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config

REMEMBER_FILE = config.STATE_DIR / "openwith.json"


@dataclass
class DesktopApp:
    id: str
    name: str
    exec: str
    terminal: bool = False
    mimes: set[str] = field(default_factory=set)


def data_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [home] + [Path(d) for d in dirs.split(":") if d]


def parse_desktop(path: Path) -> Optional[DesktopApp]:
    cp = configparser.RawConfigParser(strict=False, interpolation=None, delimiters=("=",))
    cp.optionxform = str  # keys are case-sensitive
    try:
        cp.read(path, encoding="utf-8")
        e = cp["Desktop Entry"]
    except (configparser.Error, KeyError, OSError, UnicodeDecodeError):
        return None
    if e.get("Type", "Application") != "Application" or not e.get("Exec") or not e.get("Name"):
        return None
    if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true":
        return None
    try_exec = e.get("TryExec")
    if try_exec and not shutil.which(try_exec):
        return None
    mimes = {m for m in e.get("MimeType", "").split(";") if m}
    return DesktopApp(path.name, e["Name"], e["Exec"], e.get("Terminal", "").lower() == "true", mimes)


def installed_apps() -> dict[str, DesktopApp]:
    """All launchable applications; earlier data dirs (the user's own) win."""
    apps: dict[str, DesktopApp] = {}
    for d in data_dirs():
        try:
            names = sorted(os.listdir(d / "applications"))
        except OSError:
            continue
        for n in names:
            if n.endswith(".desktop") and n not in apps:
                app = parse_desktop(d / "applications" / n)
                if app:
                    apps[n] = app
    return apps


def _mimeapps() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(default, added) associations from mimeapps.list files."""
    defaults: dict[str, list[str]] = {}
    added: dict[str, list[str]] = {}
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for f in [cfg / "mimeapps.list"] + [d / "applications" / "mimeapps.list" for d in data_dirs()]:
        cp = configparser.RawConfigParser(strict=False, interpolation=None, delimiters=("=",))
        cp.optionxform = str
        try:
            cp.read(f, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
        for section, target in (("Default Applications", defaults), ("Added Associations", added)):
            if cp.has_section(section):
                for mime, val in cp.items(section):
                    target.setdefault(mime, []).extend(a for a in val.split(";") if a)
    return defaults, added


def mime_of(path: Path) -> str:
    if shutil.which("xdg-mime"):
        try:
            r = subprocess.run(["xdg-mime", "query", "filetype", str(path)], capture_output=True,
                               text=True, timeout=2)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _handles(app: DesktopApp, mime: str) -> bool:
    if mime in app.mimes:
        return True
    if mime.startswith("text/") and "text/plain" in app.mimes:
        return True
    return any(m.endswith("/*") and mime.startswith(m[:-1]) for m in app.mimes)


def apps_for(mime: str, apps: Optional[dict[str, DesktopApp]] = None) -> list[DesktopApp]:
    """Applications able to open this type: the user's defaults first, then the rest by name."""
    apps = installed_apps() if apps is None else apps
    defaults, added = _mimeapps()
    order = defaults.get(mime, []) + added.get(mime, [])
    found = {i: a for i, a in apps.items() if _handles(a, mime)}
    first = [apps[i] for i in dict.fromkeys(order) if i in apps]
    rest = sorted((a for a in found.values() if a not in first), key=lambda a: a.name.casefold())
    return first + rest


def build_exec(app: DesktopApp, path: Path) -> list[str]:
    """Turn a .desktop Exec line into argv for opening one file."""
    out: list[str] = []
    used = False
    for tok in shlex.split(app.exec):
        if tok in ("%f", "%F", "%u", "%U"):
            out.append(str(path))
            used = True
        elif tok == "%i":
            if out and out[-1] == "--icon":
                out.pop()
        elif tok in ("%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"):
            continue
        else:
            t = tok.replace("%%", "\0")
            for code in ("%f", "%F", "%u", "%U"):
                if code in t:
                    t = t.replace(code, str(path))
                    used = True
            out.append(t.replace("\0", "%"))
    if not used:
        out.append(str(path))
    return out


def resolve(spec: dict, path: Path) -> Optional[tuple[list[str], bool]]:
    """(argv, needs_terminal) for a remembered/chosen spec, or None if it no longer applies.
    spec is {"desktop": id} or {"cmd": "command with {} for the file"}."""
    if "desktop" in spec:
        app = installed_apps().get(spec["desktop"])
        return (build_exec(app, path), app.terminal) if app else None
    if "cmd" in spec:
        parts = shlex.split(str(spec["cmd"]))
        if not parts:
            return None
        if any("{}" in p for p in parts):
            parts = [p.replace("{}", str(path)) for p in parts]
        else:
            parts.append(str(path))
        return parts, bool(spec.get("terminal", False))
    return None


def recall(ext: str) -> Optional[dict]:
    spec = config.read_json(REMEMBER_FILE, {}).get(ext.lower().lstrip("."))
    return spec if isinstance(spec, dict) else None


def remember(ext: str, spec: dict) -> None:
    data = config.read_json(REMEMBER_FILE, {})
    data[ext.lower().lstrip(".")] = spec
    config.write_json(REMEMBER_FILE, data)
