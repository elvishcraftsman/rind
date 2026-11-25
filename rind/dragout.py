"""Getting files out of rind and into other programs.

A program in a terminal can't start a real drag-and-drop to another window, so there are three
workarounds: put the files on the system clipboard as a file list (Ctrl+V in a file manager
pastes them), open a small draggable window with ripdrag or dragon, or open the folder in the
file manager. This module also reads what a terminal pastes when you drop files onto it.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urlparse

from . import launcher

CLIPBOARD_MIMES = {"gnome": "x-special/gnome-copied-files", "uri-list": "text/uri-list"}
HELPERS = ("ripdrag", "dragon-drop", "xdragon", "dragon")


# ----------------------------------------------------------------- files dropped onto the terminal
def parse_dropped(text: str) -> list[Path]:
    """Existing absolute paths in text pasted by a terminal when files are dropped on it.

    Handles file:// URIs, one per line, and shell-quoted or backslash-escaped paths, as written
    by GNOME Terminal/Console, Konsole, kitty, WezTerm, Alacritty and others."""
    text = text.strip().strip("\x00")
    if not text:
        return []
    tokens: list[str] = []
    if "file://" in text:
        for line in text.replace("\r", "\n").split("\n"):
            line = line.strip().strip("'\"")
            if line.startswith("file://"):
                u = urlparse(line)
                if u.netloc in ("", "localhost"):
                    tokens.append(unquote(u.path))
    else:
        if os.path.lexists(os.path.expanduser(text)):
            tokens = [text]
        else:
            try:
                tokens = shlex.split(text)
            except ValueError:
                tokens = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: list[Path] = []
    for t in tokens:
        p = Path(os.path.expanduser(t))
        if p.is_absolute() and os.path.lexists(p) and p not in out:
            out.append(p)
    return out


# ----------------------------------------------------------------- 1. system clipboard
def file_uri(p) -> str:
    return "file://" + quote(os.fspath(p), safe="/")


def clipboard_format(setting: str = "auto") -> str:
    """'gnome' (x-special/gnome-copied-files: GNOME Files, Nemo, Caja, Thunar, PCManFM) or
    'uri-list' (text/uri-list: Dolphin and most Qt file managers)."""
    s = (setting or "auto").strip().lower()
    if s in CLIPBOARD_MIMES:
        return s
    desktop = set(os.environ.get("XDG_CURRENT_DESKTOP", "").lower().replace(";", ":").split(":"))
    return "uri-list" if desktop & {"kde", "plasma", "lxqt"} else "gnome"


def clipboard_payload(paths: list, fmt: str, cut: bool = False) -> tuple[str, bytes]:
    uris = [file_uri(p) for p in paths]
    if fmt == "uri-list":
        return CLIPBOARD_MIMES[fmt], ("\r\n".join(uris) + "\r\n").encode()
    return CLIPBOARD_MIMES["gnome"], (("cut" if cut else "copy") + "\n" + "\n".join(uris)).encode()


def copy_to_clipboard(paths: list, setting: str = "auto", cut: bool = False) -> tuple[bool, str]:
    """Put the files on the system clipboard as a file list. Needs wl-clipboard or xclip."""
    mime, data = clipboard_payload(paths, clipboard_format(setting), cut)
    wayland, x11 = bool(os.environ.get("WAYLAND_DISPLAY")), bool(os.environ.get("DISPLAY"))
    if wayland or not x11:
        cmds = [["wl-copy", "--type", mime]] + [["xclip", "-selection", "clipboard", "-t", mime, "-i"]]
    else:
        cmds = [["xclip", "-selection", "clipboard", "-t", mime, "-i"], ["wl-copy", "--type", mime]]
    for cmd in cmds:
        if not launcher.tool(cmd[0]) and not launcher.host_prefix():
            continue
        try:
            r = subprocess.run(launcher.wrap(cmd), input=data, timeout=5, check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"{cmd[0]} failed: {getattr(e, 'strerror', None) or e}"
        if r.returncode == 0:
            n = len(paths)
            return True, f"{n} item{'s' if n != 1 else ''} on the clipboard: paste with Ctrl+V in your file manager"
        err = r.stderr.decode(errors="replace").strip().splitlines()
        if r.returncode == 127 or not err:
            continue
        return False, f"{cmd[0]}: {err[-1][:150]}"
    return False, "Needs wl-clipboard (Wayland) or xclip (X11) to copy files to the clipboard"


# ----------------------------------------------------------------- 2. helper window
def find_helper(custom: str = "") -> Optional[list[str]]:
    """The helper command as argv (without flags or files), or None."""
    if custom.strip():
        try:
            return shlex.split(custom)
        except ValueError:
            return None
    name = launcher.tool(*HELPERS)
    return [name] if name else None


def helper_argv(paths: list, custom: str = "", args: Optional[str] = None) -> Optional[list[str]]:
    base = find_helper(custom)
    if base is None:
        return None
    if args is None:
        flags = ["--and-exit"] + (["--all"] if len(paths) > 1 else [])
    else:
        flags = shlex.split(args)
    return launcher.wrap(base + flags + [os.fspath(p) for p in paths])


# ----------------------------------------------------------------- 3. file manager
def file_manager_candidates(folder, selection: list, custom: str = "", reveal: bool = True) -> list[list[str]]:
    """Commands that show the folder in the file manager. With a selection, ask the file manager
    to highlight those items first (org.freedesktop.FileManager1), then fall back to the folder."""
    folder_s = os.fspath(folder)
    if custom.strip():
        try:
            parts = shlex.split(custom)
        except ValueError:
            return []
        parts = [x.replace("{}", folder_s) for x in parts] if any("{}" in x for x in parts) else parts + [folder_s]
        return [launcher.wrap(parts)]
    out: list[list[str]] = []
    if reveal and selection and (launcher.tool("gdbus") or launcher.host_prefix()):
        uris = "[" + ", ".join("'" + file_uri(p) + "'" for p in selection) + "]"
        out.append(launcher.wrap(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                                  "--object-path", "/org/freedesktop/FileManager1",
                                  "--method", "org.freedesktop.FileManager1.ShowItems", uris, ""]))
    return out + launcher.system_open_candidates(folder_s)
