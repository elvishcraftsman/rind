"""Command-line entry point."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__

SHELL_INIT = {
    "bash": r'''rind() {
  local f d rc
  f="$(mktemp "${TMPDIR:-/tmp}/rind-cwd.XXXXXX")" || return
  command rind --cwd-file "$f" "$@"; rc=$?
  if [ -s "$f" ]; then
    d="$(cat -- "$f")"
    [ -d "$d" ] && [ "$d" != "$PWD" ] && builtin cd -- "$d"
  fi
  rm -f -- "$f"
  return $rc
}''',
    "fish": '''function rind
    set -l f (mktemp "$TMPDIR/rind-cwd.XXXXXX" 2>/dev/null; or mktemp /tmp/rind-cwd.XXXXXX)
    command rind --cwd-file $f $argv
    set -l rc $status
    if test -s $f
        set -l d (cat $f)
        if test -d "$d"; and test "$d" != "$PWD"
            builtin cd -- $d
        end
    end
    rm -f $f
    return $rc
end''',
}
SHELL_INIT["zsh"] = SHELL_INIT["bash"]


def run_update(source: str, check_only: bool, yes: bool) -> int:
    from . import updater

    if not source:
        print("No update source is configured. Add this to ~/.config/rind/config.toml:\n\n"
              "  [update]\n  source = \"github:owner/repo\"\n", file=sys.stderr)
        return 2
    try:
        rel = updater.latest_release(source)
    except updater.UpdateError as e:
        print(f"rind: update check failed: {e}", file=sys.stderr)
        return 1
    updater.record_check(rel.version)
    if not updater.is_newer(rel.version, __version__):
        print(f"rind {__version__} is up to date.")
        return 0
    print(f"rind {rel.version} is available (installed: {__version__}).")
    if rel.notes:
        print("\n" + "\n".join(rel.notes.splitlines()[:12]) + "\n")
    if check_only:
        print("Run 'rind --update' to install it.")
        return 0
    print(f"Source: {source}\nInstall type: {updater.install_kind()}")
    if not yes and input("Install it now? [y/N] ").strip().lower() not in ("y", "yes"):
        return 0
    try:
        updater.apply(rel)
    except updater.UpdateError as e:
        print(f"rind: update failed: {e}", file=sys.stderr)
        return 1
    print(f"Updated to {rel.version}. Restart rind to use it.")
    return 0


def print_keys(cfg: dict) -> None:
    from . import keys
    from .app import FileTable, RindApp

    infos = list(FileTable.BINDINGS) + list(RindApp.BINDINGS)
    raw = cfg.get("keys", {}) if isinstance(cfg.get("keys"), dict) else {}
    overrides, errors = keys.configure(raw, {b.id for b in infos})
    for e in errors:
        print(f"rind: {e}", file=sys.stderr)
    print(f"{'ACTION':<24}{'KEYS':<28}DESCRIPTION")
    for ident, (desc, ks) in keys.effective(infos, overrides).items():
        mark = "*" if ident in overrides else " "
        print(f"{ident:<23}{mark}{', '.join(ks) or '(off)':<28}{desc}")
    if overrides:
        print("\n* changed in [keys] of your config.toml")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="rind", description="A mouse-friendly terminal file manager.")
    ap.add_argument("folder", nargs="?", help="folder to open (default: current folder)")
    ap.add_argument("-V", "--version", action="version", version=f"rind {__version__}")
    ap.add_argument("--cwd-file", metavar="FILE",
                    help="on exit, write the last folder to FILE (used by the shell integration)")
    ap.add_argument("--init", choices=sorted(SHELL_INIT), metavar="SHELL",
                    help="print shell integration so quitting rind cd's your shell (bash, zsh, fish)")
    ap.add_argument("--update", action="store_true", help="check for a newer version and install it")
    ap.add_argument("--check", action="store_true", help="with --update: only check, don't install")
    ap.add_argument("-y", "--yes", action="store_true", help="with --update: don't ask for confirmation")
    ap.add_argument("--update-source", metavar="SRC", help="override the update source for this run")
    ap.add_argument("--keys", action="store_true",
                    help="list every shortcut with its action name (for [keys] in config.toml)")
    ap.add_argument("--doctor", action="store_true",
                    help="show what rind found for opening files, the clipboard and drag helpers")
    args = ap.parse_args(argv)

    if args.init:
        print(SHELL_INIT[args.init])
        return

    from . import config, updater

    if args.doctor:
        from . import launcher
        print("\n".join(launcher.diagnose()))
        return

    cfg, cfg_error = config.load_config()
    if args.keys:
        print_keys(cfg)
        return
    ucfg = cfg.get("update", {}) if isinstance(cfg.get("update"), dict) else {}
    source = updater.normalize_source(args.update_source or str(ucfg.get("source") or "") or updater.DEFAULT_SOURCE)

    if args.update or args.check:
        sys.exit(run_update(source, args.check or False, args.yes))

    start = os.path.expanduser(args.folder) if args.folder else os.getcwd()
    if not os.path.isdir(start):
        sys.exit(f"rind: not a folder: {start}")

    from .app import RindApp

    app = RindApp(start, cfg=cfg, cfg_error=cfg_error, update_source=source)
    app.run()
    if args.cwd_file and not app.skip_cwd:
        try:
            Path(args.cwd_file).write_text(str(app.cwd))
        except OSError:
            pass
