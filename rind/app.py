"""The rind Textual application."""
from __future__ import annotations

import asyncio
import grp
import os
import pwd
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, ListItem, ListView, Static

from . import __version__, archives, config, devices, fileops, gitstatus, openwith, places, updater
from .fileops import Cancelled, Job, Resolver, human, natural_key
from .preview import build_preview
from .screens import (ConfirmScreen, ConflictScreen, InfoScreen, JumpScreen, MenuScreen, OpenWithScreen,
                      ProgressScreen, PromptScreen, PropertiesScreen, SearchScreen)
from .search import match_name
from .watch import DirWatcher

SORTS = (("name", "Name"), ("size", "Size"), ("modified", "Modified"), ("type", "Type"))
COLUMNS = (("size", "Size"), ("modified", "Modified"), ("type", "Type"),
           ("perms", "Permissions"), ("owner", "Owner"), ("group", "Group"))


MENUS = ("File", "Edit", "View", "Go", "Tools", "Help")
KEY_NAMES = {"full_stop": ".", "slash": "/", "colon": ":", "backspace": "Backspace", "delete": "Delete",
             "enter": "Enter", "space": "Space", "escape": "Esc", "menu": "Menu"}


def pretty_key(key: str) -> str:
    if key in KEY_NAMES:
        return KEY_NAMES[key]
    parts = key.split("+")
    last = parts[-1]
    if len(last) == 1 and last.isupper():
        parts[-1] = last
        parts.insert(len(parts) - 1, "Shift")
    return "+".join(x if len(x) == 1 and x.isupper() else x.capitalize() for x in parts)


def git_style(code: str) -> str:
    if not code:
        return ""
    if code == "??":
        return "magenta"
    if code == "UU":
        return "bold red"
    return "green" if code[0] != " " and code[1] == " " else "yellow"


def build_command(spec, path: Path):
    """Turn a config value (string or {cmd, terminal}) into (argv, terminal)."""
    if isinstance(spec, dict):
        cmd, terminal = str(spec.get("cmd", "")), bool(spec.get("terminal", False))
    else:
        cmd, terminal = str(spec), False
    parts = shlex.split(cmd)
    if not parts:
        return None
    if any("{}" in p for p in parts):
        parts = [p.replace("{}", str(path)) for p in parts]
    else:
        parts.append(str(path))
    return parts, terminal


def xdg_places() -> list[tuple[str, Path]]:
    home = Path.home()
    names = {"DESKTOP": "Desktop", "DOCUMENTS": "Documents", "DOWNLOAD": "Downloads",
             "MUSIC": "Music", "PICTURES": "Pictures", "VIDEOS": "Videos"}
    found: dict[str, Path] = {}
    f = config.CONFIG_DIR.parent / "user-dirs.dirs"
    try:
        for line in f.read_text().splitlines():
            line = line.strip()
            if line.startswith("XDG_") and "_DIR=" in line:
                key, _, val = line.partition("=")
                key = key[4:-4]
                val = val.strip().strip('"').replace("$HOME", str(home))
                if key in names:
                    found[key] = Path(val)
    except OSError:
        pass
    out = []
    for key, label in names.items():
        p = found.get(key, home / label)
        if p.is_dir() and p != home:
            out.append((label, p))
    return out


@dataclass
class ArchiveCtx:
    archive: Path  # the archive file
    root: Path     # folder holding its extracted contents (read-only to the user)


@dataclass
class TabState:
    cwd: Path
    back: list
    forward: list
    marked: set
    filter_text: str = ""
    cursor: Optional[str] = None


@dataclass
class DragState:
    paths: list
    from_archive: bool = False


@dataclass
class UndoEntry:
    label: str
    run: Callable[[], None]


# ---------------------------------------------------------------- widgets

class Btn(Button, can_focus=False):
    """Toolbar button that never steals keyboard focus from the file list."""


class PathInput(Input):
    BINDINGS = [
        Binding("escape", "reset", "Reset", show=False),
        Binding("tab", "complete", "Complete", show=False),
    ]

    def action_reset(self) -> None:
        self.value = self.app.display_path(self.app.cwd)  # type: ignore[attr-defined]
        self.app.query_one(FileTable).focus()

    def action_complete(self) -> None:
        self.app.complete_path(self)  # type: ignore[attr-defined]


class FilterInput(Input):
    BINDINGS = [
        Binding("escape", "app.close_filter", show=False),
        Binding("down", "app.focus_table", show=False),
    ]


class MenuLabel(Static):
    """A clickable title in the menu bar."""

    def __init__(self, label: str, index: int) -> None:
        super().__init__(f" {label} ", classes="menulabel")
        self.index = index

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.app.open_menubar(self.index)  # type: ignore[attr-defined]


class PlaceItem(ListItem):
    def __init__(self, label: str, path: Optional[Path], **kw) -> None:
        super().__init__(Label(Text(label)), **kw)
        self.path = path

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            event.prevent_default()
            event.stop()
            self.app.sidebar_menu(self, event.screen_x, event.screen_y)  # type: ignore[attr-defined]


class DeviceItem(PlaceItem):
    def __init__(self, device: "devices.Device") -> None:
        super().__init__(device.title, device.mountpoint)
        self.device = device


class FileTable(DataTable):
    BINDINGS = [
        Binding("enter", "app.open_cursor", "Open", show=False),
        Binding("backspace,alt+up", "app.go_up", "Up", show=False),
        Binding("alt+left", "app.go_back", "Back", show=False),
        Binding("x,ctrl+x", "app.cut_files", "Cut", show=False),
        Binding("c,ctrl+c", "app.copy_files", "Copy", show=False),
        Binding("v,ctrl+v", "app.paste_files", "Paste", show=False),
        Binding("f2,r", "app.rename_item", "Rename", show=False),
        Binding("delete,d", "app.delete_items", "Delete", show=False),
        Binding("n,ctrl+n", "app.new_folder", "New folder", show=False),
        Binding("z,ctrl+z", "app.undo", "Undo", show=False),
        Binding("R", "app.restore_items", "Restore", show=False),
        Binding("j", "app.jump", "Jump", show=False),
        Binding("colon", "app.run_command", "Run command", show=False),
        Binding("t", "app.shell_here", "Shell here", show=False),
        Binding("i", "app.calc_sizes", "Folder sizes", show=False),
        Binding("ctrl+r", "app.rename_in_editor", "Rename in editor", show=False),
        Binding("N", "app.new_file", "New file", show=False),
        Binding("ctrl+d", "app.duplicate", "Duplicate", show=False),
        Binding("e", "app.extract", "Extract archive", show=False),
        Binding("C", "app.compress", "Compress", show=False),
        Binding("alt+right", "app.go_forward", "Forward", show=False),
        Binding("o", "app.open_with", "Open with…", show=False),
        Binding("P", "app.properties", "Properties", show=False),
        Binding("ctrl+t", "app.new_tab", "New tab", show=False),
        Binding("ctrl+w", "app.close_current_tab", "Close tab", show=False),
        Binding("ctrl+pageup", "app.prev_tab", "Previous tab", show=False),
        Binding("ctrl+pagedown", "app.next_tab", "Next tab", show=False),
        *[Binding(f"alt+{n}", f"app.goto_tab({n - 1})", "Go to tab 1-9", show=False) for n in range(1, 10)],
        Binding("y", "app.copy_to_tab", "Copy to other tab", show=False),
        Binding("Y", "app.move_to_tab", "Move to other tab", show=False),
        Binding("U", "app.unmount", "Unmount a device", show=False),
        Binding("slash", "app.open_filter", "Filter", show=False),
        Binding("ctrl+f", "app.find", "Find", show=False),
        Binding("p", "app.toggle_preview", "Preview", show=False),
        Binding("full_stop,ctrl+h", "app.toggle_hidden", "Hidden", show=False),
        Binding("space", "app.toggle_mark", "Mark", show=False),
        Binding("ctrl+a", "app.select_all", "Select all", show=False),
        Binding("s", "app.sort_menu", "Sort", show=False),
        Binding("m,menu,shift+f10", "app.key_context_menu", "Menu", show=False),
        Binding("u", "app.check_updates", "Update", show=False),
        Binding("f5", "app.reload_dir", "Reload", show=False),
        Binding("escape", "app.clear_marks", "Clear", show=False),
        Binding("q", "app.quit", "Quit", show=False),
        Binding("Q", "app.quit_no_cd", "Quit without cd", show=False),
    ]

    _drag: Optional[tuple] = None  # (row, x, y) of a left-button press that may become a drag

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._drag = None
        if event.button != 1 or event.ctrl or event.shift:
            return
        try:
            row = event.style.meta.get("row")
        except Exception:  # noqa: BLE001
            row = None
        if row is not None and row >= 0:
            self._drag = (row, event.screen_x, event.screen_y)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        app = self.app
        if getattr(app, "drag_state", None) is not None:
            app.drag_hover(event.screen_x, event.screen_y)  # type: ignore[attr-defined]
            return
        d = self._drag
        if d and event.button == 1 and (event.screen_x, event.screen_y) != (d[1], d[2]):
            self._drag = None
            if app.begin_drag(d[0]):  # type: ignore[attr-defined]
                self.capture_mouse()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self._drag = None
        app = self.app
        if getattr(app, "drag_state", None) is not None:
            self.release_mouse()
            app.finish_drag(event.screen_x, event.screen_y, event.ctrl)  # type: ignore[attr-defined]

    def on_click(self, event: events.Click) -> None:
        try:
            row = event.style.meta.get("row")
        except Exception:  # noqa: BLE001
            row = None
        app = self.app
        if event.button == 3:
            event.prevent_default()
            app.right_click(row, event.screen_x, event.screen_y)  # type: ignore[attr-defined]
            return
        if row is None or row < 0:
            return
        if event.ctrl:
            app.toggle_mark_row(row)  # type: ignore[attr-defined]
            event.prevent_default()
        elif event.shift:
            app.mark_range(self.cursor_row, row)  # type: ignore[attr-defined]
            event.prevent_default()
        elif event.chain >= 2:
            app.open_row(row)  # type: ignore[attr-defined]


# ---------------------------------------------------------------- app

class RindApp(App):
    TITLE = "rind"
    BINDINGS = [Binding("ctrl+l", "focus_path", "Edit path", show=False),
                Binding("f10,f9,alt+m", "menu_bar", "Menu bar", show=False)]
    CSS = """
    #menubar { height: 1; background: $panel; }
    .menulabel { width: auto; height: 1; }
    .menulabel:hover { background: $primary; color: $text; }
    #tabbar { height: 1; display: none; background: $panel; padding: 0 1; }
    #owlist { height: auto; max-height: 12; margin-top: 1; }
    #owcmd { margin-top: 1; }
    #props { width: 76; max-width: 95%; height: auto; max-height: 92%; padding: 1 2; background: $panel; border: thick $primary; }
    #props-info { height: auto; max-height: 12; margin-bottom: 1; }
    .permrow { height: 1; }
    .permrow Checkbox { height: 1; border: none; padding: 0 1; }
    OpenWithScreen, PropertiesScreen { align: center middle; }
    #crumbs { height: 1; padding: 0 2; color: $text-muted; }
    #info { width: 78; max-width: 95%; height: 85%; padding: 1 2; background: $panel; border: thick $primary; }
    #info VerticalScroll { height: 1fr; margin-top: 1; }
    InfoScreen { align: center middle; }
    #topbar { height: 3; padding: 1 1 0 1; }
    #toolbar { height: 2; padding: 0 1; }
    Btn { height: 1; min-width: 0; border: none; padding: 0 1; margin-right: 1; }
    PathInput { width: 1fr; height: 1; border: none; padding: 0 1; margin-right: 1; background: $boost; }
    PathInput:focus { border: none; }
    FilterInput { height: 1; border: none; padding: 0 1; background: $boost; display: none; }
    FilterInput:focus { border: none; }
    #body { height: 1fr; }
    #sidebar { width: 26; border-right: solid $primary-background; }
    ListItem.section { color: $text-muted; text-style: bold; }
    #main { width: 1fr; }
    FileTable { width: 1fr; height: 1fr; }
    #preview { width: 45%; display: none; border-left: solid $primary-background; padding: 0 1;
               overflow-x: auto; }
    #status { dock: bottom; height: 1; padding: 0 1; color: $text-muted; }
    PromptScreen, ConfirmScreen, ConflictScreen, ProgressScreen { align: center middle; }
    #dialog { width: 70; max-width: 95%; height: auto; padding: 1 2; background: $panel; border: thick $primary; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    #dialog-buttons Button { margin-left: 1; min-width: 8; }
    SearchScreen { align: center top; }
    #search { width: 80%; max-width: 110; height: 80%; margin-top: 2; padding: 1 2;
              background: $panel; border: thick $primary; }
    #search OptionList { height: 1fr; margin-top: 1; }
    #ctxmenu { border: round $primary; background: $panel; padding: 0; }
    """

    def __init__(self, start: str, cfg: Optional[dict] = None, cfg_error: Optional[str] = None,
                 update_source: str = "") -> None:
        super().__init__()
        self.start = start
        self.cwd = Path(os.getcwd())
        self.back: list[Path] = []
        self.entries: list[tuple[Path, bool]] = []
        self.marked: set[Path] = set()
        self.clip: Optional[tuple[str, list[Path]]] = None
        self.undo_stack: list[UndoEntry] = []
        self.skip_cwd = False
        self.filter_text = ""
        self.in_trash = False
        self.preview_on = False
        self.job_state: Optional[dict] = None
        self.config = cfg if cfg is not None else {}
        self.config_error = cfg_error
        self.update_source = update_source
        self.visits = places.load_visits()
        self.trash_files = fileops.home_trash_files()

        def section(name: str) -> dict:
            v = self.config.get(name, {})
            return v if isinstance(v, dict) else {}

        view, beh, upd = section("view"), section("behavior"), section("update")
        self.sort_by = view.get("sort", "name") if view.get("sort") in dict(SORTS) else "name"
        self.sort_reverse = bool(view.get("reverse", False))
        self.dirs_first = bool(view.get("dirs_first", True))
        self.show_hidden = bool(view.get("show_hidden", False))
        self.start_preview = bool(view.get("preview", False))
        self.confirm_trash = bool(beh.get("confirm_trash", False))
        self.show_git = bool(view.get("git", True))
        cols = view.get("columns", ["size", "modified", "type"])
        self.columns = [c for c in (cols if isinstance(cols, list) else []) if c in dict(COLUMNS)] \
            or ["size", "modified", "type"]
        self.terminal_cmd = str(beh.get("terminal") or "")
        self.update_check = bool(upd.get("check", True))
        try:
            self.update_interval = float(upd.get("interval_hours", 24))
        except (TypeError, ValueError):
            self.update_interval = 24.0

        open_cfg = section("open")
        self.default_opener = open_cfg.get("default")
        self.openers: dict[str, object] = {}
        for key, val in open_cfg.items():
            if key == "default":
                continue
            for ext in str(key).split(","):
                self.openers[ext.strip().lower().lstrip(".")] = val

        self.places: list[tuple[str, Path]] = [("Home", Path.home()), ("Root  /", Path("/"))]
        self.places += xdg_places()
        for extra in self.config.get("places", []) or []:
            p = Path(str(extra)).expanduser()
            self.places.append((p.name or str(p), p))
        self.places.append(("Trash", self.trash_files))
        shown = {p for _, p in self.places}
        self.frequent = [(p.name or str(p), p) for p in places.top_places(self.visits, shown, 5)]

        self.watcher = DirWatcher(self._schedule_refresh)
        self._refresh_timer = None
        self._first_event = 0.0
        self._pv_timer = None
        self._pv_token = 0
        self._poll_sig: Optional[int] = None
        self.git: Optional[gitstatus.GitInfo] = None
        self._git_timer = None
        self._git_token = 0
        self.dir_sizes: dict[Path, tuple[int, int]] = {}  # folder -> (bytes, folder mtime_ns)
        self.last_cmd = ""
        self.archive_ctxs: list[ArchiveCtx] = []
        self.tabs: list[TabState] = []
        self.tab_index = 0
        self.drag_state: Optional[DragState] = None
        self.devices: list[devices.Device] = []
        self._dev_sig: Optional[tuple] = None
        self.forward: list[Path] = []
        self._crumbs: list[tuple[str, Path]] = []
        try:
            self.max_extract = int(float(beh.get("max_extract_gb", 10)) * (1 << 30))
        except (TypeError, ValueError):
            self.max_extract = archives.MAX_BYTES
        self._owners: dict[int, str] = {}
        self._groups: dict[int, str] = {}

    # ----- layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="menubar"):
            for i, name in enumerate(MENUS):
                yield MenuLabel(name, i)
        with Horizontal(id="topbar"):
            yield Btn("←", id="back")
            yield Btn("→", id="forward")
            yield Btn("↑", id="up")
            yield Btn("↻", id="reload")
            yield PathInput(id="path", placeholder="Type a path, Tab to complete, Enter to go")
            yield Btn("Copy path", id="copypath")
        with HorizontalScroll(id="toolbar"):
            for bid, label in (("cut", "Cut"), ("copy", "Copy"), ("paste", "Paste"),
                               ("rename", "Rename"), ("delete", "Delete"), ("mkdir", "New"),
                               ("undo", "Undo"), ("filter", "Filter"), ("find", "Find"),
                               ("preview", "Preview"), ("sort", "Sort")):
                yield Btn(label, id=bid)
        yield Static("", id="tabbar")
        yield Static("", id="crumbs")
        with Horizontal(id="body"):
            yield ListView(*self.sidebar_items(), initial_index=1, id="sidebar")
            with Vertical(id="main"):
                yield FilterInput(id="filter", placeholder="Filter this folder…  (Esc clears)")
                yield FileTable(cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="preview"):
                yield Static("", id="preview-body")
        yield Static("", id="status")

    def sidebar_items(self) -> list:
        items: list = [ListItem(Label("Places"), classes="section", disabled=True)]
        items += [PlaceItem(label, p) for label, p in self.places]
        if self.frequent:
            items.append(ListItem(Label("Frequent"), classes="section", disabled=True))
            items += [PlaceItem(label, p) for label, p in self.frequent]
        if self.devices:
            items.append(ListItem(Label("Devices"), classes="section", disabled=True))
            items += [DeviceItem(d) for d in self.devices]
        return items

    async def refresh_sidebar(self) -> None:
        lv = self.query_one("#sidebar", ListView)
        await lv.clear()
        await lv.extend(self.sidebar_items())

    def on_mount(self) -> None:
        self.tabs = [TabState(self.cwd, self.back, self.forward, set())]
        self.set_interval(5.0, self.poll_devices)
        self.poll_devices()
        archives.clean_cache(86400)
        self.watcher.start(asyncio.get_running_loop())
        self.set_interval(2.0, self._poll)
        self.navigate(self.start, push=False)
        self.query_one(FileTable).focus()
        if self.start_preview:
            self.action_toggle_preview()
        if self.config_error:
            self.notify(self.config_error, severity="warning")
        if self.update_source and self.update_check and updater.due(self.update_interval):
            self._check_updates(manual=False)

    def on_unmount(self) -> None:
        self.watcher.stop()
        archives.clean_cache()

    # ----- navigation
    def navigate(self, target, push: bool = True, select: Optional[str] = None) -> None:
        p = Path(os.path.abspath(os.path.expanduser(str(target))))
        if p == Path(os.path.abspath(self.trash_files)):
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        if not p.is_dir():
            self.notify(f"Not a folder: {p}", severity="error")
            return
        if not os.access(p, os.R_OK | os.X_OK):
            self.notify(f"Permission denied: {p}", severity="error")
            return
        if push and p != self.cwd:
            self.back.append(self.cwd)
            self.forward.clear()
            if self.ctx_for(p) is None:
                self.record_visit(p)
        self.cwd = p
        self.in_trash = os.path.realpath(p) == os.path.realpath(self.trash_files)
        self.marked.clear()
        self.entries = []
        self._reset_filter()
        self.watcher.watch(p)
        self.query_one(PathInput).value = self.display_path(p)
        self.update_crumbs()
        self.update_tabs()
        self.git = None
        self.refresh_listing(select_name=select, top=select is None)
        self.schedule_git()
        self.query_one(FileTable).focus()

    def record_visit(self, p: Path) -> None:
        places.record(self.visits, p)

    def sort_key(self, row):
        name, path, is_dir, _link, st = row
        if self.sort_by == "size":
            if is_dir:
                primary = self.dir_sizes.get(path, (0, 0))[0]
            else:
                primary = 0 if st is None else st.st_size
        elif self.sort_by == "modified":
            primary = 0 if st is None else st.st_mtime
        elif self.sort_by == "type":
            primary = "" if is_dir else path.suffix.casefold()
        else:
            primary = 0
        return (primary, natural_key(name))

    def refresh_listing(self, select_name: Optional[str] = None, top: bool = False) -> None:
        table = self.query_one(FileTable)
        prev = table.cursor_row
        prev_name = self.entries[prev][0].name if 0 <= prev < len(self.entries) else None
        rows = []
        try:
            with os.scandir(self.cwd) as it:
                for e in it:
                    if not self.show_hidden and e.name.startswith("."):
                        continue
                    if self.filter_text and not match_name(self.filter_text, e.name):
                        continue
                    try:
                        is_dir = e.is_dir()
                    except OSError:
                        is_dir = False
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        st = None
                    rows.append((e.name, Path(e.path), is_dir, e.is_symlink(), st))
        except OSError as err:
            self.notify(f"Cannot read folder: {err.strerror}", severity="error")
        if self.dirs_first:
            dirs = sorted((r for r in rows if r[2]), key=self.sort_key, reverse=self.sort_reverse)
            files = sorted((r for r in rows if not r[2]), key=self.sort_key, reverse=self.sort_reverse)
            rows = dirs + files
        else:
            rows.sort(key=self.sort_key, reverse=self.sort_reverse)
        self.entries = [(r[1], r[2]) for r in rows]

        arrow = " ▼" if self.sort_reverse else " ▲"
        real_dir = os.path.realpath(self.cwd)
        git = self.git if (self.show_git and self.git is not None) else None
        table.clear(columns=True)
        table.add_column("Name" + (arrow if self.sort_by == "name" else ""), key="name")
        if git:
            table.add_column("Git", key="git")
        for key in self.columns:
            label = dict(COLUMNS)[key]
            table.add_column(label + (arrow if key == self.sort_by else ""), key=key)
        for name, path, is_dir, is_link, st in rows:
            marked = path in self.marked
            style = "bold yellow" if marked else ("bold blue" if is_dir else ("cyan" if is_link else ""))
            label = Text(("● " if marked else "  ") + name + ("/" if is_dir else ""), style=style)
            if is_link:
                try:
                    label.append("  → " + os.readlink(path), style="dim")
                except OSError:
                    pass
            cells = [label]
            if git:
                code = git.code_for(real_dir, name, is_dir)
                cells.append(Text(code, style=git_style(code)))
            for key in self.columns:
                cells.append(self.cell_for(key, path, is_dir, st))
            table.add_row(*cells)
        if rows:
            idx = 0 if top else min(prev, len(rows) - 1)
            target = select_name or (None if top else prev_name)
            if target:
                idx = next((i for i, (p, _) in enumerate(self.entries) if p.name == target), idx)
            table.move_cursor(row=idx, animate=False)
        try:
            self._poll_sig = os.stat(self.cwd).st_mtime_ns
        except OSError:
            self._poll_sig = None
        self.update_status()
        self.schedule_preview()

    def update_status(self) -> None:
        parts = [f"{len(self.entries)} items"]
        if self.in_trash:
            parts.insert(0, "TRASH")
        if self.in_archive:
            parts.insert(0, "ARCHIVE, read-only (copy files out)")
        if self.filter_text:
            parts.append(f"filter: {self.filter_text}")
        if self.marked:
            parts.append(f"{len(self.marked)} marked")
        if self.clip:
            parts.append(f"{len(self.clip[1])} {'cut' if self.clip[0] == 'cut' else 'copied'}")
        parts.append(f"sort: {self.sort_by} {'▼' if self.sort_reverse else '▲'}")
        if self.show_hidden:
            parts.append("hidden shown")
        if self.show_git and self.git is not None:
            n = len(self.git.status)
            parts.append(f"git: {self.git.branch}" + (f" ({n} changed)" if n else ""))
        if self.undo_stack:
            parts.append("z undoes: " + self.undo_stack[-1].label)
        self.query_one("#status", Static).update(Text("  ·  ".join(parts)))

    # ----- automatic refresh
    def _schedule_refresh(self) -> None:
        now = time.monotonic()
        if self._refresh_timer is None:
            self._first_event = now
        else:
            self._refresh_timer.stop()
        delay = 0.3 if now - self._first_event < 1.5 else 0.01
        self._refresh_timer = self.set_timer(delay, self._refresh_from_disk)

    def _refresh_from_disk(self) -> None:
        self._refresh_timer = None
        if not self.cwd.is_dir():
            p = self.cwd
            while not p.is_dir() and p != p.parent:
                p = p.parent
            self.notify(f"{self.cwd.name} no longer exists", severity="warning")
            self.navigate(p, push=False)
            return
        self.marked = {m for m in self.marked if os.path.lexists(m)}
        self.refresh_listing()
        self.schedule_git()

    def _poll(self) -> None:
        """Fallback when inotify is unavailable: notice folder-level changes."""
        if self.watcher.active:
            return
        try:
            sig = os.stat(self.cwd).st_mtime_ns
        except OSError:
            sig = None
        if sig != self._poll_sig:
            self._schedule_refresh()

    # ----- filter
    def _reset_filter(self) -> None:
        self.filter_text = ""
        inp = self.query_one(FilterInput)
        inp.value = ""
        inp.display = False

    def action_open_filter(self) -> None:
        inp = self.query_one(FilterInput)
        inp.display = True
        inp.focus()

    def action_close_filter(self) -> None:
        self._reset_filter()
        self.refresh_listing(top=True)
        self.query_one(FileTable).focus()

    def action_focus_table(self) -> None:
        self.query_one(FileTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter" and event.value != self.filter_text:
            self.filter_text = event.value
            self.refresh_listing(top=True)

    # ----- recursive search
    def action_find(self) -> None:
        def done(p: Optional[Path]) -> None:
            if p is None:
                return
            if p.is_dir():
                self.navigate(p)
            else:
                self.navigate(p.parent, select=p.name)

        self.push_screen(SearchScreen(self.cwd, self.show_hidden), done)

    # ----- preview
    def action_toggle_preview(self) -> None:
        self.preview_on = not self.preview_on
        self.query_one("#preview").display = self.preview_on
        if self.preview_on:
            self.update_preview_now()

    def schedule_preview(self) -> None:
        if not self.preview_on:
            return
        if self._pv_timer is not None:
            self._pv_timer.stop()
        self._pv_timer = self.set_timer(0.12, self.update_preview_now)

    def update_preview_now(self) -> None:
        self._pv_timer = None
        row = self.query_one(FileTable).cursor_row
        body = self.query_one("#preview-body", Static)
        if not (0 <= row < len(self.entries)):
            body.update("")
            return
        path = self.entries[row][0]
        self._pv_token += 1
        token = self._pv_token
        cols = max(20, int(self.size.width * 0.45) - 4)
        rows = max(10, self.size.height - 8)

        def work() -> None:
            try:
                result = build_preview(path, cols, rows)
            except Exception as e:  # noqa: BLE001
                result = Text(f"Preview failed: {e}", style="red")
            try:
                self.call_from_thread(self._set_preview, token, result)
            except Exception:  # noqa: BLE001
                pass

        self.run_worker(work, thread=True, group="preview", exclusive=True)

    def _set_preview(self, token: int, result) -> None:
        if token == self._pv_token and self.preview_on:
            self.query_one("#preview-body", Static).update(result)
            self.query_one("#preview").scroll_home(animate=False)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.schedule_preview()

    # ----- selection
    def targets(self) -> list[Path]:
        if self.marked:
            return sorted((p for p in self.marked if os.path.lexists(p)), key=lambda p: natural_key(p.name))
        row = self.query_one(FileTable).cursor_row
        return [self.entries[row][0]] if 0 <= row < len(self.entries) else []

    def toggle_mark_row(self, row: int) -> None:
        if 0 <= row < len(self.entries):
            self.marked.symmetric_difference_update({self.entries[row][0]})
            self.refresh_listing()

    def mark_range(self, a: int, b: int) -> None:
        for i in range(min(a, b), max(a, b) + 1):
            if 0 <= i < len(self.entries):
                self.marked.add(self.entries[i][0])
        self.refresh_listing()

    def action_toggle_mark(self) -> None:
        table = self.query_one(FileTable)
        self.toggle_mark_row(table.cursor_row)
        table.action_cursor_down()

    def action_select_all(self) -> None:
        self.marked = {p for p, _ in self.entries}
        self.refresh_listing()

    def action_clear_marks(self) -> None:
        if self.drag_state is not None:
            self.cancel_drag()
            return
        self.marked.clear()
        if self.filter_text or self.query_one(FilterInput).display:
            self.action_close_filter()
        else:
            self.refresh_listing()

    # ----- opening
    def open_row(self, row: int) -> None:
        if 0 <= row < len(self.entries):
            path, is_dir = self.entries[row]
            self.navigate(path) if is_dir else self.open_file(path)

    def action_open_cursor(self) -> None:
        self.open_row(self.query_one(FileTable).cursor_row)

    def open_file(self, path: Path) -> None:
        ext = path.suffix.lower().lstrip(".")
        spec = self.openers.get(ext)
        if archives.archive_kind(path) and spec is None:
            self.open_archive(path)
            return
        cmd: Optional[list] = None
        terminal = False
        if spec is None:
            remembered = openwith.recall(ext) if ext else None
            resolved = openwith.resolve(remembered, path) if remembered else None
            if resolved:
                cmd, terminal = resolved
            else:
                spec = self.default_opener
        if cmd is None:
            if spec is None:
                for base in (["xdg-open"], ["gio", "open"], ["open"]):
                    if shutil.which(base[0]):
                        cmd = base + [str(path)]
                        break
                if cmd is None:
                    self.notify("No opener found. Install xdg-utils or set [open] in config.", severity="error")
                    return
            else:
                built = build_command(spec, path)
                if not built:
                    self.notify(f"Bad opener config for .{path.suffix.lstrip('.')}", severity="error")
                    return
                cmd, terminal = built
        self._launch(cmd, terminal)

    def _launch(self, cmd: list, terminal: bool) -> None:
        try:
            if terminal:
                with self.suspend():
                    subprocess.run(cmd)
            else:
                subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            self.notify(f"Could not run {cmd[0]}: {e.strerror}", severity="error")

    # ----- navigation actions
    def action_go_up(self) -> None:
        ctx = self.ctx_for(self.cwd)
        if ctx is not None and self.cwd == ctx.root:
            self.navigate(ctx.archive.parent, select=ctx.archive.name)
            return
        if self.cwd.parent != self.cwd:
            self.navigate(self.cwd.parent, select=self.cwd.name)

    def action_go_back(self) -> None:
        if self.back:
            self.forward.append(self.cwd)
            self.navigate(self.back.pop(), push=False)

    def action_go_forward(self) -> None:
        if self.forward:
            self.back.append(self.cwd)
            self.navigate(self.forward.pop(), push=False)

    def action_reload_dir(self) -> None:
        self.refresh_listing()
        self.schedule_git()

    def action_toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.refresh_listing()

    def action_focus_path(self) -> None:
        inp = self.query_one(PathInput)
        inp.focus()
        if hasattr(inp, "select_all"):
            inp.select_all()

    def action_quit_no_cd(self) -> None:
        self.skip_cwd = True
        self.exit()

    # ----- tab completion
    def complete_path(self, inp: Input) -> None:
        if self.in_archive:
            self.bell()
            return
        text = inp.value
        if text == "~":
            inp.value = "~/"
            inp.cursor_position = len(inp.value)
            return
        head, sep, prefix = text.rpartition("/")
        base_display = head + sep
        base_dir = Path(os.path.expanduser(base_display)) if base_display else self.cwd
        if not base_dir.is_absolute():
            base_dir = self.cwd / base_dir
        try:
            with os.scandir(base_dir) as it:
                cands = []
                for e in it:
                    if not e.name.startswith(prefix):
                        continue
                    if e.name.startswith(".") and not prefix.startswith("."):
                        continue
                    try:
                        isd = e.is_dir()
                    except OSError:
                        isd = False
                    cands.append(e.name + ("/" if isd else ""))
        except OSError:
            self.bell()
            return
        cands.sort(key=natural_key)
        if not cands:
            self.bell()
            return
        common = os.path.commonprefix(cands)
        if len(cands) == 1 or len(common) > len(prefix):
            inp.value = base_display + common
            inp.cursor_position = len(inp.value)
            if len(cands) == 1:
                return
        shown = cands[:15]
        items: list = [(c, c) for c in shown]
        if len(cands) > len(shown):
            items.append(None)
            items.append(("__more__", f"… {len(cands) - len(shown)} more (keep typing)", True))

        def chosen(choice: Optional[str]) -> None:
            if choice and choice != "__more__":
                inp.value = base_display + choice
            inp.focus()
            inp.cursor_position = len(inp.value)

        r = inp.region
        self.push_screen(MenuScreen(items, r.x + 1, r.y + 1), chosen)

    # ----- context menus
    def right_click(self, row: Optional[int], x: int, y: int) -> None:
        table = self.query_one(FileTable)
        if row is not None and 0 <= row < len(self.entries):
            table.move_cursor(row=row, animate=False)
            if self.entries[row][0] not in self.marked and self.marked:
                self.marked.clear()
                self.refresh_listing()
            self.show_context_menu(x, y, True)
        else:
            self.show_context_menu(x, y, False)

    def action_key_context_menu(self) -> None:
        table = self.query_one(FileTable)
        r = table.region
        y = max(r.y + 1, r.y + 2 + table.cursor_row - int(table.scroll_y))
        self.show_context_menu(r.x + 4, y, bool(self.entries))

    def show_context_menu(self, x: int, y: int, on_item: bool) -> None:
        paste = ("paste", f"Paste ({len(self.clip[1])})" if self.clip else "Paste",
                 self.clip is None or self.in_trash)
        undo = ("undo", "Undo: " + self.undo_stack[-1].label if self.undo_stack else "Undo",
                not self.undo_stack)
        items: list = []
        if on_item:
            n = len(self.targets())
            items += [("open", "Open", n != 1), ("openwith", "Open with…", n != 1), None,
                      ("cut", "Cut"), ("copy", "Copy"), paste, None]
            if self.in_trash:
                items += [("restore", "Restore"), ("delete", "Delete permanently…"), None]
            else:
                items += [("rename", "Rename…" if n == 1 else "Rename marked… (pattern)"),
                          ("editorrename", "Rename in editor…"), ("duplicate", "Duplicate"),
                          ("delete", "Move to trash…"), ("copypath", "Copy path"),
                          ("compress", "Compress…"), ("calcsize", "Calculate size")]
                if any(archives.archive_kind(p) for p in self.targets()):
                    items.append(("extract", "Extract here"))
                if n == 1 and fileops.is_real_dir(self.targets()[0]):
                    items.append(("opentab", "Open in new tab"))
                items += [("props", "Properties…"), None]
        else:
            items += [paste, None]
        if self.in_trash:
            items += [("emptytrash", "Empty trash…")]
        else:
            items += [("mkdir", "New folder…"), ("newfile", "New file…")]
        items += [undo, None, ("runcmd", "Run command…"), ("shell", "Open shell here"),
                  ("jump", "Jump to frequent folder…"), ("find", "Find in subfolders…"),
                  ("filter", "Filter this folder…"), None, ("viewmenu", "View  ▸")]
        self.push_screen(MenuScreen(items, x, y), lambda c: self.run_menu_choice(c, x, y))

    def show_view_menu(self, x: int, y: int) -> None:
        items: list = [("sortmenu", "Sort by  ▸"), None]
        items += [(f"col:{k}", ("✓ " if k in self.columns else "  ") + label + " column")
                  for k, label in COLUMNS]
        items += [("col:git", ("✓ " if self.show_git else "  ") + "Git status column"), None,
                  ("hidden", ("✓ " if self.show_hidden else "  ") + "Show hidden files"),
                  ("preview", ("✓ " if self.preview_on else "  ") + "Preview pane"),
                  ("reload", "Reload")]
        self.push_screen(MenuScreen(items, x, y), lambda c: self.run_menu_choice(c, x, y))

    def run_menu_choice(self, choice: Optional[str], x: int, y: int) -> None:
        if not choice:
            return
        if choice.startswith("sort:"):
            self.sort_by = choice[5:]
            self.refresh_listing()
        elif choice == "reverse":
            self.sort_reverse = not self.sort_reverse
            self.refresh_listing()
        elif choice == "dirsfirst":
            self.dirs_first = not self.dirs_first
            self.refresh_listing()
        elif choice == "sortmenu":
            self.show_sort_menu(x, y)
        elif choice == "viewmenu":
            self.show_view_menu(x, y)
        elif choice.startswith("col:"):
            self.toggle_column(choice[4:])
            self.show_view_menu(x, y)
        elif choice == "open":
            t = self.targets()
            if t:
                self.navigate(t[0]) if t[0].is_dir() else self.open_file(t[0])
        elif choice == "copypath":
            self.copy_text("\n".join(str(p) for p in self.targets()))
            self.notify("Path copied")
        else:
            {"cut": self.action_cut_files, "copy": self.action_copy_files,
             "paste": self.action_paste_files, "rename": self.action_rename_item,
             "delete": self.action_delete_items, "mkdir": self.action_new_folder,
             "hidden": self.action_toggle_hidden, "reload": self.action_reload_dir,
             "undo": self.action_undo, "restore": self.action_restore_items,
             "emptytrash": self.action_empty_trash, "find": self.action_find,
             "filter": self.action_open_filter, "preview": self.action_toggle_preview,
             "runcmd": self.action_run_command, "shell": self.action_shell_here,
             "jump": self.action_jump, "calcsize": self.action_calc_sizes,
             "editorrename": self.action_rename_in_editor,
             "newfile": self.action_new_file, "duplicate": self.action_duplicate,
             "extract": self.action_extract, "compress": self.action_compress,
             "back": self.action_go_back, "forward": self.action_go_forward, "up": self.action_go_up,
             "gohome": lambda: self.navigate(Path.home()), "goroot": lambda: self.navigate("/"),
             "gotrash": lambda: self.navigate(self.trash_files), "gopath": self.action_focus_path,
             "selectall": self.action_select_all, "clearmarks": self.action_clear_marks,
             "quit": self.exit, "quitnocd": self.action_quit_no_cd, "help": self.action_help,
             "about": self.action_about, "update": self.action_check_updates,
             "openwith": self.action_open_with, "props": self.action_properties,
             "newtab": self.action_new_tab, "closetab": self.action_close_current_tab,
             "opentab": self.action_open_in_tab, "unmount": self.action_unmount,
             "copytab": self.action_copy_to_tab, "movetab": self.action_move_to_tab,
             "nexttab": self.action_next_tab, "prevtab": self.action_prev_tab}[choice]()

    def show_sort_menu(self, x: int, y: int) -> None:
        items: list = [(f"sort:{k}", ("● " if k == self.sort_by else "  ") + label) for k, label in SORTS]
        items += [None,
                  ("reverse", ("✓ " if self.sort_reverse else "  ") + "Reverse order"),
                  ("dirsfirst", ("✓ " if self.dirs_first else "  ") + "Folders first")]
        self.push_screen(MenuScreen(items, x, y), lambda c: self.run_menu_choice(c, x, y))

    def action_sort_menu(self) -> None:
        r = self.query_one(FileTable).region
        self.show_sort_menu(r.x + 4, r.y + 1)

    # ----- clipboard
    def copy_text(self, text: str) -> None:
        try:
            self.copy_to_clipboard(text)  # OSC 52 (works over SSH in most terminals)
        except Exception:  # noqa: BLE001
            pass
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if shutil.which(cmd[0]):
                try:
                    subprocess.run(cmd, input=text.encode(), timeout=2, check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break
                except Exception:  # noqa: BLE001
                    pass

    def action_copy_path(self) -> None:
        self.copy_text(self.display_path(self.cwd))
        self.notify("Path copied")

    def action_cut_files(self) -> None:
        if self._ro():
            return
        t = self.targets()
        if t:
            self.clip = ("cut", t)
            self.update_status()

    def action_copy_files(self) -> None:
        t = self.targets()
        if t:
            self.clip = ("copy", t)
            self.update_status()

    # ----- background jobs with progress
    def run_job(self, title: str, fn: Callable[[Job], object], done: Callable[[object], None]) -> None:
        if self.job_state is not None:
            self.notify("Another operation is still running", severity="warning")
            return
        job = Job()
        state = {"screen": None, "finished": False, "asking": False, "job": job}
        self.job_state = state

        def show() -> None:
            if state["finished"] or state["screen"] is not None or state["asking"]:
                return
            state["screen"] = ProgressScreen(title, job)
            self.push_screen(state["screen"])

        state["show"] = show
        self.set_timer(0.4, show)

        def finish(result: object) -> None:
            state["finished"] = True
            self.job_state = None
            if state["screen"] is not None:
                state["screen"].dismiss(None)
            done(result)

        def work() -> None:
            try:
                result: object = fn(job)
            except Exception as e:  # noqa: BLE001
                result = e
            try:
                self.call_from_thread(finish, result)
            except Exception:  # noqa: BLE001
                pass

        self.run_worker(work, thread=True, group="jobs")

    def _ask_conflict(self, src: Path, dst: Path) -> tuple[str, bool]:
        """Called from the worker thread: show the conflict dialog and wait."""
        state = self.job_state or {}
        ev = threading.Event()
        box = [("cancel", False)]

        def cb(r) -> None:
            if r:
                box[0] = r
            ev.set()

        state["asking"] = True
        self.call_from_thread(self.push_screen, ConflictScreen(src, dst), cb)
        while not ev.wait(0.2):
            if not self.is_running:
                break
        state["asking"] = False
        if "show" in state:
            self.call_from_thread(self.set_timer, 0.4, state["show"])
        return box[0]

    # ----- paste / transfers
    def action_paste_files(self) -> None:
        if self._ro():
            return
        if self.in_trash:
            self.notify("Can't paste into the trash. Use Delete to trash items.", severity="warning")
            return
        if not self.clip:
            self.notify("Nothing to paste", severity="warning")
            return
        mode, paths = self.clip
        self._start_transfer(mode, paths, self.cwd, clear_clip=True)

    def _start_transfer(self, mode: str, paths: list, dest_dir: Path, clear_clip: bool = False) -> None:
        """Copy or move paths into dest_dir as a background job (conflicts, progress, undo)."""
        if mode == "cut":
            mode = "move"
        pairs: list[tuple[Path, Path]] = []
        for src in paths:
            if not os.path.lexists(src):
                self.notify(f"{src.name}: no longer exists", severity="error")
                continue
            is_dir = fileops.is_real_dir(src)
            if is_dir and (dest_dir == src or src in dest_dir.parents):
                self.notify(f"{src.name}: can't put a folder inside itself", severity="error")
                continue
            if src.parent == dest_dir:
                if mode == "move":
                    continue
                dst = fileops.unique_dest(dest_dir / src.name, is_dir)
            else:
                dst = dest_dir / src.name
            pairs.append((src, dst))
        if not pairs:
            return

        def work(job: Job):
            resolver = Resolver(self._ask_conflict)
            return (fileops.run_copy if mode == "copy" else fileops.run_move)(pairs, job, resolver)

        self.run_job("Copying" if mode == "copy" else "Moving", work,
                     lambda res: self._finish_transfer("cut" if mode == "move" else "copy", res, clear_clip))

    def _finish_transfer(self, mode: str, res, clear_clip: bool = True) -> None:
        if isinstance(res, Exception):
            self.notify(f"Operation failed: {res}", severity="error")
            self.refresh_listing()
            return
        verb = "Copied" if mode == "copy" else "Moved"
        n = len(res.done)
        msg = f"{verb} {n} item{'s' if n != 1 else ''}"
        if res.skipped:
            msg += f", skipped {len(res.skipped)}"
        if res.cancelled:
            msg += " (cancelled)"
        self.notify(msg, severity="warning" if (res.cancelled or res.skipped) else "information")
        for e in res.errors[:3]:
            self.notify(e, severity="error")
        if res.done:
            self.push_undo(f"{verb.lower()} {n} item{'s' if n != 1 else ''}",
                           lambda: self._undo_transfer(mode, res))
        if clear_clip and mode == "cut" and not res.cancelled and not res.errors:
            self.clip = None
        self.marked.clear()
        self.refresh_listing(select_name=res.done[-1][1].name if res.done else None)

    # ----- undo
    def push_undo(self, label: str, run: Callable[[], None]) -> None:
        self.undo_stack.append(UndoEntry(label, run))
        del self.undo_stack[:-50]
        self.update_status()

    def action_undo(self) -> None:
        if not self.undo_stack:
            self.notify("Nothing to undo", severity="warning")
            return
        entry = self.undo_stack.pop()
        self.notify(f"Undoing: {entry.label}")
        entry.run()
        self.update_status()

    def _restore_records(self, records: list, errs: list[str]) -> None:
        for rec in reversed(records):
            try:
                fileops.restore_record(rec)
            except OSError as e:
                errs.append(f"{rec.orig.name}: {e.strerror or e}")

    def _after_undo(self, errs: list[str]) -> None:
        for e in errs[:3]:
            self.notify(e, severity="error")
        self.marked = {m for m in self.marked if os.path.lexists(m)}
        self.refresh_listing()

    def _undo_transfer(self, mode: str, res) -> None:
        replaced = list(res.replaced)
        if mode == "copy":
            errs: list[str] = []
            for _src, final in res.done:
                try:
                    fileops.trash_item(final)
                except OSError as e:
                    errs.append(f"{final.name}: {e.strerror or e}")
            self._restore_records(replaced, errs)
            self._after_undo(errs)
            return
        back = [(final, src) for src, final in reversed(res.done)]

        def work(job: Job):
            return fileops.run_move(back, job, Resolver(None))

        def done(r) -> None:
            if isinstance(r, Exception):
                self._after_undo([str(r)])
                return
            errs = list(r.errors) + [f"{p.name}: original name is in use" for p in r.skipped]
            self._restore_records(replaced, errs)
            self._after_undo(errs)

        self.run_job("Undoing move", work, done)

    # ----- rename / new folder
    def action_rename_item(self) -> None:
        if self._ro():
            return
        if self.in_trash:
            self.notify("Can't rename inside the trash", severity="warning")
            return
        t = self.targets()
        if len(t) > 1:
            self._rename_pattern(t)
            return
        if len(t) != 1:
            self.notify("Select an item to rename", severity="warning")
            return
        old = t[0]

        def done(new: Optional[str]) -> None:
            if not new or new == old.name:
                return
            if "/" in new or new in (".", ".."):
                self.notify("Invalid name", severity="error")
                return
            dest = old.with_name(new)
            if os.path.lexists(dest):
                self.notify(f"'{new}' already exists", severity="error")
                return
            try:
                old.rename(dest)
            except OSError as e:
                self.notify(f"Rename failed: {e.strerror}", severity="error")
                return

            def undo() -> None:
                if os.path.lexists(old) or not os.path.lexists(dest):
                    self.notify("Can't undo rename: names changed since", severity="error")
                else:
                    try:
                        os.rename(dest, old)
                    except OSError as e:
                        self.notify(f"Undo failed: {e.strerror}", severity="error")
                self.refresh_listing(select_name=old.name)

            self.push_undo(f"rename of '{old.name}'", undo)
            self.marked.discard(old)
            self.refresh_listing(select_name=new)

        self.push_screen(PromptScreen(f"Rename '{old.name}' to:", old.name), done)

    def action_new_folder(self) -> None:
        if self._ro():
            return
        if self.in_trash:
            self.notify("Can't create folders inside the trash", severity="warning")
            return

        def done(name: Optional[str]) -> None:
            if not name:
                return
            if "/" in name:
                self.notify("Invalid name", severity="error")
                return
            target = self.cwd / name
            try:
                target.mkdir()
            except OSError as e:
                self.notify(f"Could not create folder: {e.strerror}", severity="error")
                return

            def undo() -> None:
                try:
                    os.rmdir(target)
                except OSError:
                    self.notify("Can't undo: the folder is no longer empty", severity="error")
                self.refresh_listing()

            self.push_undo(f"new folder '{name}'", undo)
            self.refresh_listing(select_name=name)

        self.push_screen(PromptScreen("New folder name:"), done)

    # ----- delete / trash
    def action_delete_items(self) -> None:
        if self._ro():
            return
        t = self.targets()
        if not t:
            return
        what = f"'{t[0].name}'" if len(t) == 1 else f"{len(t)} items"
        if self.in_trash:
            self._confirm_permanent(t, f"Permanently delete {what}? This cannot be undone.")
        elif self.confirm_trash:
            self.push_screen(ConfirmScreen(f"Move {what} to the trash?", "Trash"),
                             lambda ok: ok and self._do_trash(t))
        else:
            self._do_trash(t)

    def _do_trash(self, paths: list[Path]) -> None:
        records, failed = [], []
        for p in paths:
            try:
                records.append(fileops.trash_item(p))
            except OSError as e:
                failed.append((p, e.strerror or str(e)))
        self.marked.clear()
        if records:
            n = len(records)
            label = f"'{records[0].orig.name}' moved to trash" if n == 1 else f"{n} items moved to trash"
            self.push_undo(label, lambda: self._undo_trash(records))
        self.refresh_listing()
        if failed:
            names = ", ".join(p.name for p, _ in failed[:3])
            self.push_screen(
                ConfirmScreen(f"Couldn't move {names} to the trash ({failed[0][1]}). Delete permanently?",
                              "Delete"),
                lambda ok: ok and self._permanent_delete([p for p, _ in failed]))

    def _undo_trash(self, records: list) -> None:
        errs: list[str] = []
        self._restore_records(records, errs)
        self._after_undo(errs)

    def _confirm_permanent(self, paths: list[Path], message: str) -> None:
        self.push_screen(ConfirmScreen(message, "Delete"), lambda ok: ok and self._permanent_delete(paths))

    def _permanent_delete(self, paths: list[Path]) -> None:
        def done(res) -> None:
            if isinstance(res, Exception):
                self.notify(f"Delete failed: {res}", severity="error")
            else:
                for e in res.errors[:3]:
                    self.notify(e, severity="error")
                self.notify(f"Deleted {len(res.done)} item{'s' if len(res.done) != 1 else ''}")
            self.marked.clear()
            self.refresh_listing()

        self.run_job("Deleting", lambda job: fileops.run_delete(paths, job), done)

    def action_restore_items(self) -> None:
        if not self.in_trash:
            self.notify("Restore works inside the trash (sidebar → Trash)", severity="warning")
            return
        restored, errs = 0, []
        for p in self.targets():
            try:
                fileops.restore_from_trash(p)
                restored += 1
            except OSError as e:
                errs.append(f"{p.name}: {e.strerror or e}")
        self.marked.clear()
        if restored:
            self.notify(f"Restored {restored} item{'s' if restored != 1 else ''}")
        for e in errs[:3]:
            self.notify(e, severity="error")
        self.refresh_listing()

    def action_empty_trash(self) -> None:
        def go(ok: Optional[bool]) -> None:
            if ok:
                def done(res) -> None:
                    self.notify(f"Emptied trash ({res} items)" if isinstance(res, int) else f"Failed: {res}")
                    self.refresh_listing()

                self.run_job("Emptying trash", lambda job: fileops.empty_trash(), done)

        self.push_screen(ConfirmScreen("Permanently delete everything in the trash?", "Empty trash"), go)

    # ----- columns, git, folder sizes
    def cell_for(self, key: str, path: Path, is_dir: bool, st):
        if st is None:
            return ""
        if key == "size":
            if is_dir:
                cached = self.dir_sizes.get(path)
                if cached and cached[1] == st.st_mtime_ns:
                    return Text(human(cached[0]), justify="right", style="italic dim")
                return Text("")
            return Text(human(st.st_size), justify="right")
        if key == "modified":
            return datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        if key == "type":
            return "Folder" if is_dir else (path.suffix.lstrip(".").upper() or "File")
        if key == "perms":
            return stat.filemode(st.st_mode)
        if key == "owner":
            if st.st_uid not in self._owners:
                try:
                    self._owners[st.st_uid] = pwd.getpwuid(st.st_uid).pw_name
                except KeyError:
                    self._owners[st.st_uid] = str(st.st_uid)
            return self._owners[st.st_uid]
        if st.st_gid not in self._groups:
            try:
                self._groups[st.st_gid] = grp.getgrgid(st.st_gid).gr_name
            except KeyError:
                self._groups[st.st_gid] = str(st.st_gid)
        return self._groups[st.st_gid]

    def schedule_git(self) -> None:
        if not self.show_git:
            return
        if self._git_timer is not None:
            self._git_timer.stop()
        self._git_timer = self.set_timer(0.15, self._start_git)

    def _start_git(self) -> None:
        self._git_timer = None
        self._git_token += 1
        token, cwd = self._git_token, self.cwd

        def work() -> None:
            info = gitstatus.git_status(cwd)
            try:
                self.call_from_thread(self._set_git, token, cwd, info)
            except Exception:  # noqa: BLE001
                pass

        self.run_worker(work, thread=True, group="git", exclusive=True)

    def _set_git(self, token: int, cwd: Path, info) -> None:
        if token != self._git_token or cwd != self.cwd:
            return
        old = self.git
        changed = (old is None) != (info is None) or (
            info is not None and old is not None
            and (info.branch != old.branch or info.status != old.status))
        self.git = info
        if changed:
            self.refresh_listing()

    def action_calc_sizes(self) -> None:
        t = self.targets()
        if not t:
            return

        def work(job: Job):
            sizes = {}
            for p in t:
                job.current = p.name
                job.check()
                sizes[p] = fileops.path_size(p, job)
            return sizes

        def done(res) -> None:
            if isinstance(res, Exception):
                return  # cancelled
            total = 0
            for p, sz in res.items():
                total += sz
                try:
                    mt = os.lstat(p).st_mtime_ns
                except OSError:
                    continue
                if fileops.is_real_dir(p):
                    self.dir_sizes[p] = (sz, mt)
            self.notify(f"{human(total)} in {len(res)} item{'s' if len(res) != 1 else ''}")
            self.refresh_listing()

        self.run_job("Calculating size", work, done)

    # ----- jump, shell, run command
    def action_jump(self) -> None:
        def done(p: Optional[Path]) -> None:
            if p is not None:
                self.navigate(p)

        self.push_screen(JumpScreen(self.visits), done)

    def _terminal_session(self, argv, shell: bool, banner: str, pause: bool) -> None:
        env = dict(os.environ, RIND_LEVEL=str(int(os.environ.get("RIND_LEVEL", "0")) + 1))
        try:
            with self.suspend():
                print(banner, flush=True)
                rc = subprocess.run(argv, shell=shell, cwd=self.cwd, env=env).returncode
                if pause:
                    print(f"\n[exit status {rc}]  Press Enter to return to rind", flush=True)
                    try:
                        input()
                    except (EOFError, KeyboardInterrupt):
                        pass
        except Exception as e:  # noqa: BLE001
            self.notify(f"Couldn't run it here: {e}", severity="error")
        self.refresh_listing()
        self.schedule_git()

    def action_run_command(self) -> None:
        def done(cmd: Optional[str]) -> None:
            if not cmd or not cmd.strip():
                return
            self.last_cmd = cmd
            names = " ".join(shlex.quote(p.name) for p in self.targets())
            full = cmd.replace("{}", names)
            self._terminal_session(full, True, f"$ {full}\n", True)

        n = len(self.targets())
        self.push_screen(PromptScreen(
            f"Run in this folder. {{}} stands for the {n} selected item{'s' if n != 1 else ''}:",
            self.last_cmd), done)

    def action_shell_here(self) -> None:
        if self.terminal_cmd:
            parts = shlex.split(self.terminal_cmd)
            parts = [x.replace("{}", str(self.cwd)) for x in parts]
            try:
                subprocess.Popen(parts, cwd=self.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                self.notify(f"Could not start terminal: {e.strerror}", severity="error")
            return
        shell = os.environ.get("SHELL") or "/bin/sh"
        self._terminal_session([shell], False, f"rind: shell in {self.cwd}\nType 'exit' to return to rind.\n", False)

    # ----- bulk rename
    def _rename_pattern(self, t: list[Path]) -> None:
        def done(spec: Optional[str]) -> None:
            if not spec:
                return
            try:
                names = fileops.pattern_names(t, spec)
            except ValueError as e:
                self.notify(f"Rename cancelled: {e}", severity="error")
                return
            self._confirm_renames(list(zip(t, names)))

        self.push_screen(PromptScreen(
            f"Rename {len(t)} items.  find=>replace  (regex, \\1 groups, {{n}} / {{n:03}} counter)", ""), done)

    def action_rename_in_editor(self) -> None:
        if self._ro():
            return
        if self.in_trash:
            self.notify("Can't rename inside the trash", severity="warning")
            return
        t = self.targets()
        if not t:
            return
        if any("\n" in p.name for p in t):
            self.notify("A name contains a newline; use the pattern rename instead", severity="error")
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or next(
            (e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
        if not editor:
            self.notify("Set $EDITOR to rename in an editor", severity="error")
            return
        fd, tmp = tempfile.mkstemp(prefix="rind-rename-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(p.name for p in t) + "\n")
        try:
            with self.suspend():
                subprocess.run(shlex.split(editor) + [tmp])
            text = Path(tmp).read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self.notify(f"Editor failed: {e}", severity="error")
            return
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if len(lines) != len(t):
            self.notify(f"Line count changed ({len(lines)} instead of {len(t)}); nothing renamed",
                        severity="error")
            return
        self._confirm_renames(list(zip(t, lines)))

    def _confirm_renames(self, pairs: list[tuple[Path, str]]) -> None:
        try:
            changes = fileops.plan_renames(pairs)
        except ValueError as e:
            self.notify(f"Rename cancelled: {e}", severity="error")
            return
        if not changes:
            self.notify("No names changed")
            return
        lines = [f"{o.name}  →  {n.name}" for o, n in changes[:8]]
        if len(changes) > 8:
            lines.append(f"… and {len(changes) - 8} more")
        self.push_screen(
            ConfirmScreen(f"Rename {len(changes)} item{'s' if len(changes) != 1 else ''}?\n\n" + "\n".join(lines),
                          "Rename"),
            lambda ok: ok and self._do_renames(changes))

    def _do_renames(self, changes: list[tuple[Path, Path]]) -> None:
        try:
            fileops.apply_renames(changes)
        except OSError as e:
            self.notify(f"Rename failed, nothing was changed: {e.strerror or e}", severity="error")
            self.refresh_listing()
            return

        def undo() -> None:
            try:
                fileops.apply_renames(fileops.plan_renames([(n, o.name) for o, n in changes]))
            except (ValueError, OSError) as e:
                self.notify(f"Can't undo rename: {e}", severity="error")
            self.refresh_listing()

        self.push_undo(f"rename of {len(changes)} item{'s' if len(changes) != 1 else ''}", undo)
        self.marked.clear()
        self.refresh_listing(select_name=changes[-1][1].name)

    # ----- archives, breadcrumbs, virtual paths
    def ctx_for(self, p: Path) -> Optional[ArchiveCtx]:
        best = None
        for c in self.archive_ctxs:
            if p == c.root or c.root in p.parents:
                if best is None or len(c.root.parts) > len(best.root.parts):
                    best = c
        return best

    @property
    def in_archive(self) -> bool:
        return self.ctx_for(self.cwd) is not None

    def _ro(self) -> bool:
        if self.in_archive:
            self.notify("Archives are read-only. Copy files out to a normal folder.", severity="warning")
            return True
        return False

    def display_path(self, p: Path) -> str:
        ctx = self.ctx_for(p)
        if ctx is None:
            return str(p)
        rel = p.relative_to(ctx.root).as_posix()
        return self.display_path(ctx.archive) + "!/" + ("" if rel == "." else rel)

    def map_virtual(self, text: str) -> Optional[Path]:
        """Turn 'archive.zip!/inner/path' typed in the path bar back into a real path."""
        best: Optional[tuple[int, Path]] = None
        for c in self.archive_ctxs:
            base = self.display_path(c.root)
            if text.startswith(base) and (best is None or len(base) > best[0]):
                best = (len(base), Path(os.path.abspath(c.root / text[len(base):])))
        if best is None:
            return None
        return best[1] if self.ctx_for(best[1]) is not None else None

    def _chain(self, p: Path) -> list[tuple[str, Path]]:
        ctx = self.ctx_for(p)
        if ctx is None:
            home = Path.home()
            try:
                parts, cur, out = p.relative_to(home).parts, home, [("~", home)]
            except ValueError:
                parts, cur, out = p.parts[1:], Path("/"), [("/", Path("/"))]
            for part in parts:
                cur = cur / part
                out.append((part, cur))
            return out
        out = self._chain(ctx.archive.parent) + [(ctx.archive.name, ctx.root)]
        cur = ctx.root
        for part in p.relative_to(ctx.root).parts:
            cur = cur / part
            out.append((part, cur))
        return out

    def update_crumbs(self) -> None:
        chain = self._chain(self.cwd)
        self._crumbs = chain
        sep = " › "
        width = max(20, self.size.width - 6)

        def total(i: int) -> int:
            return sum(len(label) for label, _ in chain[i:]) + len(sep) * (len(chain) - i - 1) + (4 if i else 0)

        start = 0
        while start < len(chain) - 1 and total(start) > width:
            start += 1
        t = Text(no_wrap=True, overflow="ellipsis")
        if start:
            t.append("… › ", style="dim")
        for i in range(start, len(chain)):
            if i > start:
                t.append(sep, style="dim")
            t.append(chain[i][0], style=Style(bold=(i == len(chain) - 1), meta={"@click": f"app.crumb({i})"}))
        self.query_one("#crumbs", Static).update(t)

    def action_crumb(self, i: int) -> None:
        if 0 <= i < len(self._crumbs):
            self.navigate(self._crumbs[i][1])

    def on_resize(self, event: events.Resize) -> None:
        try:
            self.update_crumbs()
        except Exception:  # noqa: BLE001  (can fire before the widgets exist)
            pass

    def open_archive(self, path: Path) -> None:
        def work(job: Job):
            return archives.open_cache(path, job, self.max_extract)

        def done(res) -> None:
            if isinstance(res, Exception):
                if not isinstance(res, Cancelled):
                    self.notify(f"Can't open {path.name}: {res}", severity="error")
                return
            if not any(c.root == res for c in self.archive_ctxs):
                self.archive_ctxs.append(ArchiveCtx(path, res))
            self.navigate(res)

        self.run_job(f"Opening {path.name}", work, done)

    def action_extract(self) -> None:
        if self._ro():
            return
        t = [p for p in self.targets() if archives.archive_kind(p) and p.is_file()]
        if not t:
            self.notify("Select an archive (.zip, .tar.gz, .tar.xz, ...) to extract", severity="warning")
            return
        parent = self.cwd

        def work(job: Job):
            made, skipped, errors = [], [], []
            for a in t:
                job.current, job.done = a.name, 0
                try:
                    final, sk = archives.extract_here(a, parent, job, self.max_extract)
                    made.append(final)
                    skipped += sk
                except Cancelled:
                    raise
                except (archives.ArchiveError, OSError) as e:
                    errors.append(f"{a.name}: {e}")
            return made, skipped, errors

        def done(res) -> None:
            if isinstance(res, Exception):
                self.notify("Extraction cancelled" if isinstance(res, Cancelled) else f"Extract failed: {res}",
                            severity="warning" if isinstance(res, Cancelled) else "error")
                self.refresh_listing()
                return
            made, skipped, errors = res
            for e in errors[:3]:
                self.notify(e, severity="error")
            if skipped:
                self.notify(f"Skipped {len(skipped)} unsafe entr{'ies' if len(skipped) != 1 else 'y'}, "
                            f"e.g. {skipped[0]}", severity="warning", timeout=8)
            if made:
                n = len(made)
                self.notify(f"Extracted {n} archive{'s' if n != 1 else ''}")

                def undo() -> None:
                    errs: list[str] = []
                    for m in made:
                        try:
                            fileops.trash_item(m)
                        except OSError as e:
                            errs.append(f"{m.name}: {e.strerror or e}")
                    self._after_undo(errs)

                self.push_undo(f"extract of '{t[0].name}'" if n == 1 else f"extract of {n} archives", undo)
            self.marked.clear()
            self.refresh_listing(select_name=made[-1].name if made else None)

        self.run_job("Extracting", work, done)

    def action_compress(self) -> None:
        if self._ro():
            return
        t = self.targets()
        if not t:
            return
        default = f"{t[0].name}.zip" if len(t) == 1 else "archive.zip"

        def named(name: Optional[str]) -> None:
            if not name:
                return
            name = name.strip()
            if "/" in name or archives.archive_kind(name) is None:
                self.notify("Name must end in .zip, .tar, .tar.gz, .tar.xz, .tar.bz2, .tgz, .txz or .tbz2",
                            severity="error")
                return
            dest = self.cwd / name

            def fin(res) -> None:
                if isinstance(res, Exception):
                    self.notify("Cancelled" if isinstance(res, Cancelled) else f"Compress failed: {res}",
                                severity="warning" if isinstance(res, Cancelled) else "error")
                    self.refresh_listing()
                    return
                self.notify(f"Created {res.name}")

                def undo() -> None:
                    errs: list[str] = []
                    try:
                        fileops.trash_item(res)
                    except OSError as e:
                        errs.append(f"{res.name}: {e.strerror or e}")
                    self._after_undo(errs)

                self.push_undo(f"archive '{res.name}'", undo)
                self.marked.clear()
                self.refresh_listing(select_name=res.name)

            self.run_job("Compressing", lambda job: archives.compress(t, dest, job), fin)

        self.push_screen(PromptScreen("Create archive (.zip, .tar.gz, .tar.xz, .tar.bz2 or .tar):", default), named)

    # ----- new file, duplicate
    def action_new_file(self) -> None:
        if self._ro():
            return
        if self.in_trash:
            self.notify("Can't create files inside the trash", severity="warning")
            return

        def done(name: Optional[str]) -> None:
            if not name:
                return
            if "/" in name:
                self.notify("Invalid name", severity="error")
                return
            target = self.cwd / name
            try:
                with open(target, "x"):
                    pass
            except FileExistsError:
                self.notify(f"'{name}' already exists", severity="error")
                return
            except OSError as e:
                self.notify(f"Could not create file: {e.strerror}", severity="error")
                return

            def undo() -> None:
                try:
                    if os.path.getsize(target) == 0:
                        os.unlink(target)
                    else:
                        self.notify("Not undone: the new file now has content", severity="error")
                except OSError:
                    pass
                self.refresh_listing()

            self.push_undo(f"new file '{name}'", undo)
            self.refresh_listing(select_name=name)

        self.push_screen(PromptScreen("New file name:"), done)

    def action_duplicate(self) -> None:
        if self._ro():
            return
        if self.in_trash:
            self.notify("Can't duplicate inside the trash", severity="warning")
            return
        t = self.targets()
        if not t:
            return
        pairs = [(p, fileops.unique_dest(p, fileops.is_real_dir(p))) for p in t]
        self.run_job("Duplicating", lambda job: fileops.run_copy(pairs, job, Resolver(None)),
                     lambda res: self._finish_transfer("copy", res))

    # ----- menu bar, help, about
    def action_menu_bar(self) -> None:
        if len(self.screen_stack) > 1:
            return
        self.open_menubar(0)

    def open_menubar(self, index: int) -> None:
        labels = list(self.query(MenuLabel))
        if not labels:
            return
        r = labels[index].region
        regions = [(lab.region.x, lab.region.x + lab.region.width) for lab in labels]
        items = self.menu_items(MENUS[index])
        self.push_screen(MenuScreen(items, r.x, r.y + 1, bar=regions),
                         lambda c: self.menubar_choice(c, index, r.x, r.y + 1))

    def menubar_choice(self, choice: Optional[str], index: int, x: int, y: int) -> None:
        if not choice:
            return
        if choice in ("__left__", "__right__"):
            self.open_menubar((index + (1 if choice == "__right__" else -1)) % len(MENUS))
        elif choice.startswith("__bar:"):
            target = int(choice[6:])
            if target != index:
                self.open_menubar(target)
        elif choice.startswith("col:"):
            self.toggle_column(choice[4:])
            self.open_menubar(index)
        else:
            self.run_menu_choice(choice, x, y)

    def toggle_column(self, key: str) -> None:
        if key == "git":
            self.show_git = not self.show_git
            if self.show_git:
                self.schedule_git()
        else:
            self.columns = [k for k, _ in COLUMNS if (k in self.columns) != (k == key)]
        self.refresh_listing()

    def menu_items(self, name: str) -> list:
        has = bool(self.targets())
        ro, tr = self.in_archive, self.in_trash
        no_paste = self.clip is None or tr or ro
        check = lambda on, label: ("✓ " if on else "   ") + label  # noqa: E731
        rows: list = []
        if name == "File":
            rows = [("mkdir", "New folder…", "n", ro or tr), ("newfile", "New file…", "N", ro or tr), None,
                    ("open", "Open", "Enter", not has), ("openwith", "Open with…", "o", not has),
                    ("opentab", "Open folder in new tab", "", not has),
                    ("newtab", "New tab", "Ctrl+T"), ("closetab", "Close tab", "Ctrl+W", len(self.tabs) < 2),
                    ("shell", "Open shell here", "t"), None,
                    ("extract", "Extract here", "e", ro or not has), ("compress", "Compress…", "C", ro or not has),
                    None, ("calcsize", "Calculate size", "i", not has),
                    ("copypath", "Copy path", "", not has), ("props", "Properties…", "P", not has), None,
                    ("unmount", "Unmount device…", "U", not any(d.mounted for d in self.devices)), None,
                    ("quit", "Quit", "q"), ("quitnocd", "Quit without changing folder", "Q")]
        elif name == "Edit":
            rows = [("undo", "Undo: " + self.undo_stack[-1].label if self.undo_stack else "Undo", "z",
                     not self.undo_stack), None,
                    ("cut", "Cut", "x", not has or ro), ("copy", "Copy", "c", not has),
                    ("paste", "Paste" + (f" ({len(self.clip[1])})" if self.clip else ""), "v", no_paste),
                    ("duplicate", "Duplicate", "Ctrl+D", not has or ro or tr),
                    ("copytab", "Copy to other tab", "y", not has or len(self.tabs) < 2),
                    ("movetab", "Move to other tab", "Y", not has or ro or len(self.tabs) < 2), None]
            if tr:
                rows += [("restore", "Restore", "R", not has), ("delete", "Delete permanently…", "d", not has),
                         ("emptytrash", "Empty trash…", "")]
            else:
                rows += [("rename", "Rename…", "F2", not has or ro),
                         ("editorrename", "Rename in editor…", "Ctrl+R", not has or ro),
                         ("delete", "Move to trash", "d", not has or ro)]
            rows += [None, ("selectall", "Select all", "Ctrl+A"), ("clearmarks", "Clear selection", "Esc")]
        elif name == "View":
            rows = [("preview", check(self.preview_on, "Preview pane"), "p"),
                    ("hidden", check(self.show_hidden, "Show hidden files"), "."),
                    ("col:git", check(self.show_git, "Git status column"), ""), None,
                    ("sortmenu", "Sort by  ▸", "s"), None]
            rows += [(f"col:{k}", check(k in self.columns, f"{label} column"), "") for k, label in COLUMNS]
            rows += [None, ("filter", "Filter this folder…", "/"), ("reload", "Reload", "F5")]
        elif name == "Go":
            rows = [("back", "Back", "Alt+Left", not self.back), ("forward", "Forward", "Alt+Right", not self.forward),
                    ("up", "Up", "Backspace"), None,
                    ("nexttab", "Next tab", "Ctrl+PgDn", len(self.tabs) < 2),
                    ("prevtab", "Previous tab", "Ctrl+PgUp", len(self.tabs) < 2), None,
                    ("gohome", "Home", ""), ("goroot", "/  (root)", ""), ("gotrash", "Trash", ""), None,
                    ("jump", "Jump to frequent folder…", "j"), ("find", "Find in subfolders…", "Ctrl+F"),
                    ("gopath", "Edit path", "Ctrl+L")]
        elif name == "Tools":
            rows = [("runcmd", "Run command…", ":"), ("shell", "Open shell here", "t"), None,
                    ("update", "Check for updates…", "u")]
        else:
            rows = [("help", "Keyboard shortcuts", ""), ("about", "About rind", "")]
        w = max(len(r[1]) for r in rows if r)
        out: list = []
        for r in rows:
            if r is None:
                out.append(None)
                continue
            label = r[1].ljust(w + 3) + r[2] if r[2] else r[1]
            out.append((r[0], label, bool(len(r) > 3 and r[3])))
        return out

    def action_help(self) -> None:
        seen, lines = set(), []
        for b in FileTable.BINDINGS:
            if b.description in seen:
                continue
            seen.add(b.description)
            keys = " / ".join(pretty_key(k) for k in b.key.split(","))
            lines.append(f"{keys:<26}{b.description}")
        lines += [f"{'Ctrl+L':<26}Edit path (Tab completes)", f"{'F10 / F9 / Alt+M':<26}Menu bar (←/→ switch menus)", "",
                  "Mouse: click selects, double-click opens, right-click for the context menu,",
                  "Ctrl+click toggles a mark, Shift+click marks a range, click a column header to sort,",
                  "click a folder name in the path line above the list to jump up to it."]
        self.push_screen(InfoScreen("Keyboard shortcuts", "\n".join(lines)))

    def action_about(self) -> None:
        src = self.update_source or "(none set)"
        self.push_screen(InfoScreen("About rind", (
            f"rind {__version__}\nA mouse-friendly terminal file manager.\n\n"
            f"Config:         {config.CONFIG_FILE}\nUpdate source:  {src}\n"
            f"Install type:   {updater.install_kind()}")))

    # ----- tabs
    def tab_name(self, i: int) -> str:
        cwd = self.cwd if i == self.tab_index else self.tabs[i].cwd
        return cwd.name or "/"

    def update_tabs(self) -> None:
        try:
            bar = self.query_one("#tabbar", Static)
        except Exception:  # noqa: BLE001  (before the widgets exist)
            return
        bar.display = len(self.tabs) > 1
        if len(self.tabs) <= 1:
            return
        t = Text(no_wrap=True, overflow="ellipsis")
        for i in range(len(self.tabs)):
            active = i == self.tab_index
            t.append(f" {i + 1} {self.tab_name(i)[:18]} ",
                     style=Style(bold=active, reverse=active, meta={"@click": f"app.goto_tab({i})"}))
            t.append("× ", style=Style(dim=not active, meta={"@click": f"app.close_tab({i})"}))
            t.append(" ")
        t.append(" + ", style=Style(bold=True, meta={"@click": "app.new_tab"}))
        bar.update(t)

    def _save_tab(self) -> None:
        ts = self.tabs[self.tab_index]
        ts.cwd, ts.back, ts.forward = self.cwd, self.back, self.forward
        ts.marked, ts.filter_text = set(self.marked), self.filter_text
        row = self.query_one(FileTable).cursor_row
        ts.cursor = self.entries[row][0].name if 0 <= row < len(self.entries) else None

    def _activate_tab(self, i: int) -> None:
        self.tab_index = i
        ts = self.tabs[i]
        self.back, self.forward = ts.back, ts.forward
        target = ts.cwd
        while not target.is_dir() and target != target.parent:
            target = target.parent
        self.navigate(target, push=False, select=ts.cursor)
        if ts.marked:
            self.marked = {m for m in ts.marked if os.path.lexists(m)}
            self.refresh_listing(select_name=ts.cursor)
        if ts.filter_text:
            inp = self.query_one(FilterInput)
            inp.display = True
            inp.value = ts.filter_text
        self.update_tabs()

    def new_tab(self, path: Optional[Path] = None) -> None:
        self._save_tab()
        cursor = self.tabs[self.tab_index].cursor if path is None else None
        ts = TabState(path or self.cwd, [], [], set(), "", cursor)
        self.tabs.insert(self.tab_index + 1, ts)
        self._activate_tab(self.tab_index + 1)

    def action_new_tab(self) -> None:
        self.new_tab()

    def action_close_tab(self, i: int) -> None:
        if len(self.tabs) == 1:
            self.notify("This is the only tab", severity="warning")
            return
        if not 0 <= i < len(self.tabs):
            return
        del self.tabs[i]
        if i < self.tab_index:
            self.tab_index -= 1
            self.update_tabs()
        elif i == self.tab_index:
            self._activate_tab(min(i, len(self.tabs) - 1))
        else:
            self.update_tabs()

    def action_close_current_tab(self) -> None:
        self.action_close_tab(self.tab_index)

    def action_goto_tab(self, i: int) -> None:
        if 0 <= i < len(self.tabs) and i != self.tab_index:
            self._save_tab()
            self._activate_tab(i)

    def action_next_tab(self) -> None:
        self.action_goto_tab((self.tab_index + 1) % len(self.tabs))

    def action_prev_tab(self) -> None:
        self.action_goto_tab((self.tab_index - 1) % len(self.tabs))

    def action_open_in_tab(self) -> None:
        t = self.targets()
        self.new_tab(t[0] if len(t) == 1 and fileops.is_real_dir(t[0]) else None)

    def _to_tab(self, mode: str) -> None:
        others = [i for i in range(len(self.tabs)) if i != self.tab_index]
        if not others:
            self.notify("Open a second tab first (Ctrl+T)", severity="warning")
            return
        t = self.targets()
        if not t:
            return
        if mode == "cut" and self._ro():
            return

        def go(i: int) -> None:
            dest = self.tabs[i].cwd
            if self.ctx_for(dest) is not None or os.path.realpath(dest) == os.path.realpath(self.trash_files):
                self.notify("That tab can't receive files (archive or trash)", severity="warning")
                return
            self._start_transfer(mode, t, dest)

        if len(others) == 1:
            go(others[0])
            return
        items = [(f"tab:{i}", f"{i + 1}  {self.tab_name(i)}") for i in others]
        r = self.query_one(FileTable).region
        self.push_screen(MenuScreen(items, r.x + 4, r.y + 2), lambda c: c and go(int(c[4:])))

    def action_copy_to_tab(self) -> None:
        self._to_tab("copy")

    def action_move_to_tab(self) -> None:
        self._to_tab("cut")

    # ----- drag and drop (within rind)
    def _label_for(self, paths: list) -> str:
        return f"'{paths[0].name}'" if len(paths) == 1 else f"{len(paths)} items"

    def begin_drag(self, row: int) -> bool:
        if self.in_trash:
            self.notify("Use Restore (R) to take items out of the trash", severity="warning")
            return False
        if not 0 <= row < len(self.entries):
            return False
        p = self.entries[row][0]
        paths = self.targets() if p in self.marked else [p]
        if not paths:
            return False
        self.drag_state = DragState(paths, self.in_archive)
        self._drag_status("")
        return True

    def _drag_status(self, target: str) -> None:
        st = self.drag_state
        if st is None:
            return
        tail = f"→ {target}   (release to {'copy' if st.from_archive else 'move'}, Ctrl = copy)" if target \
            else "drop on a folder, sidebar place, tab or path segment  (Ctrl = copy, Esc cancels)"
        self.query_one("#status", Static).update(Text(f"Dragging {self._label_for(st.paths)}  {tail}"))

    def drop_target(self, x: int, y: int) -> tuple[Optional[Path], str]:
        """Destination folder under a screen position, and a short name for it."""
        try:
            widget, _ = self.screen.get_widget_at(x, y)
            meta = self.screen.get_style_at(x, y).meta
        except Exception:  # noqa: BLE001
            return None, ""
        dragged = set(self.drag_state.paths) if self.drag_state else set()
        for w in widget.ancestors_with_self:
            if isinstance(w, PlaceItem):
                return (w.path, w.path.name or "/") if w.path is not None else (None, "")
            if isinstance(w, FileTable):
                row = meta.get("row")
                if row is not None and 0 <= row < len(self.entries) and self.entries[row][1]:
                    d = self.entries[row][0]
                    return (None, "") if d in dragged else (d, d.name)
                return None, ""
        click = str(meta.get("@click", ""))
        try:
            if click.startswith("app.crumb(") and widget.id == "crumbs":
                d = self._crumbs[int(click[10:-1])][1]
                return d, d.name or "/"
            if click.startswith("app.goto_tab("):
                i = int(click[13:-1])
                return (self.tabs[i].cwd, f"tab {i + 1}") if i != self.tab_index else (None, "")
        except (ValueError, IndexError):
            pass
        return None, ""

    def drag_hover(self, x: int, y: int) -> None:
        self._drag_status(self.drop_target(x, y)[1])

    def cancel_drag(self) -> None:
        if self.drag_state is not None:
            self.drag_state = None
            self.query_one(FileTable).release_mouse()
            self.update_status()

    def finish_drag(self, x: int, y: int, ctrl: bool) -> None:
        st, self.drag_state = self.drag_state, None
        self.update_status()
        if st is None:
            return
        dest, _ = self.drop_target(x, y)
        if dest is None:
            return
        if self.ctx_for(dest) is not None:
            self.notify("Archives are read-only", severity="warning")
        elif os.path.realpath(dest) == os.path.realpath(self.trash_files):
            if st.from_archive:
                self.notify("Items inside an archive can't be trashed", severity="warning")
            else:
                self._do_trash(st.paths)
        else:
            self._start_transfer("copy" if (ctrl or st.from_archive) else "move", st.paths, dest)

    # ----- devices
    def poll_devices(self) -> None:
        def work() -> None:
            devs = devices.list_devices()
            try:
                self.call_from_thread(self._set_devices, devs)
            except Exception:  # noqa: BLE001
                pass

        self.run_worker(work, thread=True, group="devices", exclusive=True)

    async def _set_devices(self, devs: list) -> None:
        sig = tuple((d.dev, str(d.mountpoint), d.label) for d in devs)
        if sig == self._dev_sig:
            return
        self._dev_sig, self.devices = sig, devs
        await self.refresh_sidebar()

    def activate_device(self, d) -> None:
        if d.mountpoint is not None and d.mountpoint.is_dir():
            self.navigate(d.mountpoint)
            return

        def done(res) -> None:
            if isinstance(res, Exception):
                self.notify(f"Can't mount {d.label}: {res}", severity="error")
                return
            self.poll_devices()
            self.navigate(res)

        self.run_job(f"Mounting {d.label}", lambda job: devices.mount(d), done)

    def unmount_device(self, d) -> None:
        if d.mountpoint is not None and (self.cwd == d.mountpoint or d.mountpoint in self.cwd.parents):
            self.navigate(Path.home())

        def done(res) -> None:
            if isinstance(res, Exception):
                self.notify(f"Can't unmount {d.label}: {res}", severity="error")
            else:
                self.notify(f"Unmounted {d.label}")
            self.poll_devices()

        self.run_job(f"Unmounting {d.label}", lambda job: devices.unmount(d), done)

    def action_unmount(self) -> None:
        mounted = [d for d in self.devices if d.mounted]
        if not mounted:
            self.notify("No removable or network devices are mounted", severity="warning")
        elif len(mounted) == 1:
            self.unmount_device(mounted[0])
        else:
            self.push_screen(MenuScreen([(f"dev:{i}", d.title) for i, d in enumerate(mounted)], 2, 6),
                             lambda c: c and self.unmount_device(mounted[int(c[4:])]))

    def sidebar_menu(self, item, x: int, y: int) -> None:
        items: list = []
        if isinstance(item, DeviceItem):
            d = item.device
            items.append(("sb_open", "Open" if d.mounted else "Mount and open"))
            if d.mounted:
                items += [("sb_tab", "Open in new tab"), None, ("sb_unmount", "Unmount")]
        elif item.path is not None:
            items += [("sb_open", "Open"), ("sb_tab", "Open in new tab")]
        if not items:
            return

        def chosen(c: Optional[str]) -> None:
            if c == "sb_open":
                self.activate_device(item.device) if isinstance(item, DeviceItem) else self.navigate(item.path)
            elif c == "sb_tab" and item.path is not None:
                self.new_tab(item.path)
            elif c == "sb_unmount":
                self.unmount_device(item.device)

        self.push_screen(MenuScreen(items, x, y), chosen)

    # ----- properties, open with
    def action_properties(self) -> None:
        t = self.targets()
        if not t:
            return

        def done(res) -> None:
            if not res or res[0] != "apply" or self._ro():
                return
            _, mode, rec = res
            path = t[0]

            def fin(r) -> None:
                if isinstance(r, Exception):
                    self.notify(f"Couldn't change permissions: {getattr(r, 'strerror', None) or r}",
                                severity="error")
                    self.refresh_listing()
                    return
                changed = r
                if not changed:
                    self.notify("Nothing needed changing")
                    return
                self.notify(f"Permissions changed on {len(changed)} item{'s' if len(changed) != 1 else ''}")

                def undo() -> None:
                    errs: list[str] = []
                    for p, old in reversed(changed):
                        try:
                            os.chmod(p, old)
                        except OSError as e:
                            errs.append(f"{p.name}: {e.strerror}")
                    self._after_undo(errs)

                self.push_undo(f"permissions of '{path.name}'", undo)
                self.refresh_listing()

            self.run_job("Changing permissions", lambda job: fileops.apply_mode(path, mode, rec), fin)

        self.push_screen(PropertiesScreen(t), done)

    def action_open_with(self) -> None:
        t = self.targets()
        if not t:
            return
        path = t[0]
        if fileops.is_real_dir(path):
            self.notify("Open with works on files; use Enter to enter a folder", severity="warning")
            return
        apps = openwith.apps_for(openwith.mime_of(path))

        def done(res) -> None:
            if not res:
                return
            kind, value, remember = res
            if kind == "default":
                for base in (["xdg-open"], ["gio", "open"], ["open"]):
                    if shutil.which(base[0]):
                        self._launch(base + [str(path)], False)
                        return
                self.notify("No system opener found (install xdg-utils)", severity="error")
                return
            spec = {"desktop": value} if kind == "desktop" else {"cmd": value}
            resolved = openwith.resolve(spec, path)
            if not resolved:
                self.notify("That application is no longer installed", severity="error")
                return
            ext = path.suffix.lower().lstrip(".")
            if remember and ext:
                openwith.remember(ext, spec)
                self.notify(f"Will use it for .{ext} files (unless your config says otherwise)")
            self._launch(*resolved)

        self.push_screen(OpenWithScreen(path, apps), done)

    # ----- updates
    def action_check_updates(self) -> None:
        self._check_updates(manual=True)

    def _check_updates(self, manual: bool) -> None:
        if not self.update_source:
            if manual:
                self.notify("No update source set. Add [update] source to the config (see README).",
                            severity="warning")
            return
        if manual:
            self.notify("Checking for updates…")

        def work() -> None:
            rel, err = None, ""
            try:
                rel = updater.latest_release(self.update_source)
                updater.record_check(rel.version)
            except updater.UpdateError as e:
                err = str(e)
            try:
                self.call_from_thread(self._update_result, rel, err, manual)
            except Exception:  # noqa: BLE001
                pass

        self.run_worker(work, thread=True, group="update")

    def _update_result(self, rel, err: str, manual: bool) -> None:
        if rel is None:
            if manual:
                self.notify(f"Update check failed: {err}", severity="error")
            return
        if not updater.is_newer(rel.version, __version__):
            if manual:
                self.notify(f"rind {__version__} is up to date")
            return
        if not manual:
            self.notify(f"rind {rel.version} is available. Press u to update.", timeout=10)
            return
        self.push_screen(
            ConfirmScreen(f"rind {rel.version} is available (you have {__version__}). Install it now?",
                          "Update"),
            lambda ok: ok and self._apply_update(rel))

    def _apply_update(self, rel) -> None:
        self.notify("Installing update…", timeout=6)

        def work() -> None:
            try:
                updater.apply(rel)
                msg, sev = f"Updated to {rel.version}. Restart rind to use it.", "information"
            except updater.UpdateError as e:
                msg, sev = f"Update failed: {e}", "error"
            try:
                self.call_from_thread(self.notify, msg, severity=sev, timeout=12)
            except Exception:  # noqa: BLE001
                pass

        self.run_worker(work, thread=True, group="update")

    # ----- events
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, DeviceItem):
            self.activate_device(event.item.device)
        elif isinstance(event.item, PlaceItem) and event.item.path is not None:
            self.navigate(event.item.path)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        key = event.column_key.value
        if key not in dict(SORTS):
            return
        if key == self.sort_by:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_by, self.sort_reverse = str(key), False
        self.refresh_listing()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            self.query_one(FileTable).focus()
            return
        if event.input.id != "path":
            return
        text = os.path.expandvars(event.value.strip())
        if not text:
            return
        p = self.map_virtual(text)
        if p is None:
            p = Path(text).expanduser()
            if not p.is_absolute():
                p = self.cwd / p
            p = Path(os.path.abspath(p))
        if p.is_dir():
            self.navigate(p)
        elif os.path.lexists(p):
            self.navigate(p.parent, select=p.name)
        else:
            self.notify(f"No such path: {p}", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not isinstance(event.button, Btn):
            return
        if event.button.id == "sort":
            r = event.button.region
            self.show_sort_menu(r.x, r.y + 1)
            return
        {
            "back": self.action_go_back, "forward": self.action_go_forward, "up": self.action_go_up, "reload": self.action_reload_dir,
            "copypath": self.action_copy_path, "cut": self.action_cut_files,
            "copy": self.action_copy_files, "paste": self.action_paste_files,
            "rename": self.action_rename_item, "delete": self.action_delete_items,
            "mkdir": self.action_new_folder, "undo": self.action_undo,
            "filter": self.action_open_filter, "find": self.action_find,
            "preview": self.action_toggle_preview,
        }[event.button.id or ""]()
