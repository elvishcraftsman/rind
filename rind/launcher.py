"""Starting other programs (system opener, helper windows, file managers) and saying why they failed.

Everything here runs the program detached, waits a moment, and reports an exit code or the
program's own error text, so a failed "Open" is never silent. Inside a Flatpak, Toolbox or
Distrobox container the host's tools are used when the container has none of its own.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Optional

# "open" is deliberately absent: on many Linux systems it is openvt, not a file opener.
OPENERS = (["xdg-open"], ["gio", "open"], ["kde-open"], ["exo-open"], ["gnome-open"])


def container_kind() -> str:
    """'flatpak', 'distrobox', 'toolbox', 'container' or '' when running on the host."""
    if os.path.exists("/.flatpak-info"):
        return "flatpak"
    if os.environ.get("DISTROBOX_ENTER_PATH") or os.environ.get("DISTROBOX_HOST_HOME"):
        return "distrobox"
    if os.path.exists("/run/.toolboxenv"):
        return "toolbox"
    if os.path.exists("/run/.containerenv"):
        return "container"
    return ""


def host_prefix() -> list[str]:
    """Words that make a command run on the host, or [] when not in a container (or no way out)."""
    kind = container_kind()
    if not kind:
        return []
    if kind == "distrobox" and shutil.which("distrobox-host-exec"):
        return ["distrobox-host-exec"]
    if shutil.which("flatpak-spawn"):
        return ["flatpak-spawn", "--host"]
    return []


def wrap(argv: list[str]) -> list[str]:
    """Run on the host when this container doesn't have the program."""
    pre = host_prefix()
    return pre + argv if pre and not shutil.which(argv[0]) else argv


def system_open_candidates(target) -> list[list[str]]:
    """Commands to try, best first, to open a file or folder with the desktop's default program."""
    t = str(target)
    out: list[list[str]] = []
    pre = host_prefix()
    if pre:
        out += [pre + ["xdg-open", t], pre + ["gio", "open", t]]
    out += [base + [t] for base in OPENERS if shutil.which(base[0])]
    return out


def launch(candidates: list[list[str]], done: Callable[[bool, str], None], wait: float = 1.5) -> None:
    """Try each command in turn on a background thread.

    A command that is still running after `wait` seconds counts as started. One that exits 0
    counts as done. One that fails moves on to the next candidate. done(ok, message) is called
    once, from the thread; when nothing worked the message is the first (best) command's error."""
    def work() -> None:
        errors: list[str] = []
        for cmd in candidates:
            try:
                err = tempfile.TemporaryFile()
                p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=err, start_new_session=True)
            except OSError as e:
                errors.append(f"{cmd[0]}: {e.strerror or e}")
                continue
            try:
                rc = p.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                done(True, "")
                err.close()
                p.wait()  # reap it when it finishes
                return
            err.seek(0)
            text = err.read().decode(errors="replace").strip().splitlines()
            err.close()
            if rc == 0:
                done(True, "")
                return
            errors.append(f"{cmd[0]} exited with {rc}" + (f": {text[-1][:200]}" if text else ""))
        msg = errors[0] if errors else "no program to run"
        if len(errors) > 1:
            msg += f" ({len(errors) - 1} other way{'s' if len(errors) > 2 else ''} also failed)"
        done(False, msg)

    threading.Thread(target=work, daemon=True).start()


def tool(*names: str) -> Optional[str]:
    """First of these programs that is installed here (or that the host provides from a container)."""
    for n in names:
        if shutil.which(n):
            return n
    return None


def diagnose() -> list[str]:
    """Lines for 'rind --doctor'."""
    kind = container_kind()
    lines = [f"Container:        {kind or 'none (running on the host)'}"]
    if kind:
        lines.append(f"Host commands:    {' '.join(host_prefix()) or 'NOT available (install flatpak-spawn)'}")
    lines += [f"Desktop:          {os.environ.get('XDG_CURRENT_DESKTOP') or '(unset)'}",
              f"Display:          WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY') or '-'}  "
              f"DISPLAY={os.environ.get('DISPLAY') or '-'}",
              f"D-Bus session:    {'yes' if os.environ.get('DBUS_SESSION_BUS_ADDRESS') else 'NO (openers need it)'}"]
    found = [" ".join(c[:-1]) for c in system_open_candidates("/")]
    lines.append("Openers tried:    " + (", ".join(found) if found else "NONE found: install xdg-utils"))
    for label, names in (("Clipboard tool", ("wl-copy", "xclip")),
                         ("Drag helper", ("ripdrag", "dragon-drop", "xdragon", "dragon")),
                         ("Default opener", ("xdg-mime",))):
        lines.append(f"{label + ':':<18}{tool(*names) or 'not installed'}")
    return lines
