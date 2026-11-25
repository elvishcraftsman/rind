"""Modal screens: popup menu, prompts, conflict dialog, progress, recursive search."""
from __future__ import annotations

import mimetypes
import os
import pwd
import grp
import stat
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList, ProgressBar, Select, Static
from textual.widgets.option_list import Option

from .fileops import Job, human, is_real_dir, path_size
from .search import find_files
from . import places


class MenuScreen(ModalScreen[Optional[str]]):
    """Popup menu at screen position (x, y). items: (id, label[, disabled]) or None."""

    BINDINGS = [Binding("escape", "close", show=False),
                Binding("left", "nav('__left__')", show=False),
                Binding("right", "nav('__right__')", show=False)]

    def __init__(self, items: list, x: int, y: int, bar: Optional[list] = None) -> None:
        """bar: [(x0, x1), ...] column ranges of the menu bar labels, when opened from it."""
        super().__init__()
        self.items, self.x, self.y, self.bar = items, x, y, bar

    def action_nav(self, which: str) -> None:
        if self.bar is not None:
            self.dismiss(which)

    def compose(self) -> ComposeResult:
        opts = []
        for it in self.items:
            if it is None:
                opts.append(None)
            else:
                opts.append(Option(Text(it[1]), id=it[0], disabled=bool(len(it) > 2 and it[2])))
        yield OptionList(*opts, id="ctxmenu")

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        width = max((len(it[1]) for it in self.items if it), default=8) + 6
        height = len(self.items) + 2
        sw, sh = self.app.size
        width, height = min(width, sw), min(height, sh)
        menu.styles.width = width
        menu.styles.height = height
        menu.styles.offset = (max(0, min(self.x, sw - width)), max(0, min(self.y, sh - height)))
        menu.focus()
        try:
            menu.action_first()
        except Exception:  # noqa: BLE001
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def on_click(self, event: events.Click) -> None:
        try:
            widget, _ = self.get_widget_at(event.screen_x, event.screen_y)
        except Exception:  # noqa: BLE001
            widget = None
        if widget is not self.query_one(OptionList):
            if self.bar and event.screen_y == 0:
                for i, (x0, x1) in enumerate(self.bar):
                    if x0 <= event.screen_x < x1:
                        self.dismiss(f"__bar:{i}")
                        return
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class PromptScreen(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, title: str, value: str = "") -> None:
        super().__init__()
        self.title_text, self.value = title, value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(Text(self.title_text))
            yield Input(value=self.value, id="prompt-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self.query_one(Input).value if event.button.id == "ok" else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "yes", show=False), Binding("n,escape", "no", show=False)]

    def __init__(self, message: str, yes_label: str = "Yes") -> None:
        super().__init__()
        self.message, self.yes_label = message, yes_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(Text(self.message))
            with Horizontal(id="dialog-buttons"):
                yield Button(self.yes_label, variant="error", id="yes")
                yield Button("Cancel", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


def _describe(p: Path) -> str:
    try:
        st = os.lstat(p)
    except OSError:
        return "(unavailable)"
    when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    kind = "folder" if is_real_dir(p) else human(st.st_size)
    return f"{kind}, modified {when}"


class ConflictScreen(ModalScreen[Optional[tuple]]):
    """Asks what to do when the destination exists. Returns (action, apply_to_all)."""

    BINDINGS = [Binding("r", "choose('replace')", show=False), Binding("s", "choose('skip')", show=False),
                Binding("k", "choose('both')", show=False), Binding("escape", "choose('cancel')", show=False)]

    def __init__(self, src: Path, dst: Path) -> None:
        super().__init__()
        self.src, self.dst = src, dst

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(Text(f"“{self.dst.name}” already exists here."))
            yield Label(Text(f"Existing:  {_describe(self.dst)}"))
            yield Label(Text(f"Incoming:  {_describe(self.src)}"))
            yield Checkbox("Apply to all remaining conflicts", id="all")
            with Horizontal(id="dialog-buttons"):
                yield Button("Replace", variant="warning", id="replace")
                yield Button("Skip", id="skip")
                yield Button("Keep both", variant="primary", id="both")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_choose(str(event.button.id))

    def action_choose(self, action: str) -> None:
        self.dismiss((action, self.query_one(Checkbox).value))


class ProgressScreen(ModalScreen[None]):
    """Shows a running job's progress; polls the Job object, so no cross-thread calls."""

    def __init__(self, title: str, job: Job) -> None:
        super().__init__()
        self.title_text, self.job = title, job

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(Text(self.title_text), id="ptitle")
            yield Label("", id="pcur")
            yield ProgressBar(total=None, show_eta=False, id="pbar")
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._tick()
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        j = self.job
        self.query_one(ProgressBar).update(total=j.total or None, progress=min(j.done, j.total) if j.total else 0)
        self.query_one("#pcur", Label).update(Text(j.current))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.job.cancel()
        self.query_one("#ptitle", Label).update("Cancelling…")


class SearchScreen(ModalScreen[Optional[Path]]):
    """Search file and folder names in the current folder and all subfolders."""

    BINDINGS = [Binding("escape", "close", show=False), Binding("down", "focus_results", show=False)]

    def __init__(self, root: Path, show_hidden: bool) -> None:
        super().__init__()
        self.root, self.show_hidden = root, show_hidden
        self.results: list[Path] = []
        self._token = 0
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="search"):
            yield Label(Text(f"Search in {self.root} and subfolders"))
            yield Input(placeholder="Part of a name; several words must all match", id="q")
            yield OptionList(id="results")
            yield Label("", id="count")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self._timer is not None:
            self._timer.stop()
        query = event.value
        self._token += 1
        token = self._token
        if not query.strip():
            self._show(token, [], False)
            return
        self.query_one("#count", Label).update("Searching…")
        self._timer = self.set_timer(0.2, lambda: self._start(token, query))

    def _start(self, token: int, query: str) -> None:
        def work() -> None:
            res, trunc = find_files(self.root, query, self.show_hidden, lambda: token != self._token)
            if token == self._token:
                try:
                    self.app.call_from_thread(self._show, token, res, trunc)
                except Exception:  # noqa: BLE001
                    pass

        self.run_worker(work, thread=True, group="find", exclusive=True)

    def _show(self, token: int, res: list[Path], truncated: bool) -> None:
        if token != self._token:
            return
        self.results = res
        ol = self.query_one(OptionList)
        ol.clear_options()
        ol.add_options([Option(Text(str(p.relative_to(self.root)) + ("/" if is_real_dir(p) else "")),
                               id=str(i)) for i, p in enumerate(res)])
        n = len(res)
        self.query_one("#count", Label).update(
            "" if not self.query_one(Input).value.strip()
            else f"{n}{'+' if truncated else ''} result{'s' if n != 1 else ''}"
            + ("  (Enter opens the first, ↓ to browse)" if n else ""))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self.results:
            self.dismiss(self.results[0])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.results[int(event.option.id)])

    def action_focus_results(self) -> None:
        if self.results:
            self.query_one(OptionList).focus()
            self.query_one(OptionList).action_first()

    def action_close(self) -> None:
        self.dismiss(None)


class JumpScreen(ModalScreen[Optional[Path]]):
    """Jump to a folder you use often (ranked by how often and how recently)."""

    BINDINGS = [Binding("escape", "close", show=False), Binding("down", "focus_results", show=False)]

    def __init__(self, visits: dict) -> None:
        super().__init__()
        self.visits = visits
        self.results: list[Path] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search"):
            yield Label("Jump to a frequently used folder")
            yield Input(placeholder="Part of a path; several words must all match", id="q")
            yield OptionList(id="results")
            yield Label("", id="count")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._refresh("")

    def _refresh(self, query: str) -> None:
        self.results = places.rank_jump(self.visits, query)
        ol = self.query_one(OptionList)
        ol.clear_options()
        ol.add_options([Option(Text(places.tilde(p)), id=str(i)) for i, p in enumerate(self.results)])
        self.query_one("#count", Label).update(
            "" if self.results else "No matching folders yet. Folders you visit are remembered.")

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self.results:
            self.dismiss(self.results[0])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.results[int(event.option.id)])

    def action_focus_results(self) -> None:
        if self.results:
            self.query_one(OptionList).focus()
            self.query_one(OptionList).action_first()

    def action_close(self) -> None:
        self.dismiss(None)


class InfoScreen(ModalScreen[None]):
    """A scrollable read-only text dialog (help, about)."""

    BINDINGS = [Binding("escape,enter,q", "close", show=False)]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(id="info"):
            yield Label(Text(self.title_text, style="bold"))
            with VerticalScroll():
                yield Static(Text(self.body))
            with Horizontal(id="dialog-buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class OpenWithScreen(ModalScreen[Optional[tuple]]):
    """Choose an application for a file. Returns (kind, value, remember):
    ("desktop", app id), ("cmd", command text) or ("default", None)."""

    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(self, path: Path, apps: list) -> None:
        super().__init__()
        self.path, self.apps = path, apps

    def compose(self) -> ComposeResult:
        ext = self.path.suffix.lstrip(".")
        with Vertical(id="dialog"):
            yield Label(Text(f"Open “{self.path.name}” with:"))
            opts = [Option(Text(a.name + ("  (runs in terminal)" if a.terminal else "")), id=f"app:{a.id}")
                    for a in self.apps]
            opts.append(Option(Text("Default application (system)"), id="default"))
            yield OptionList(*opts, id="owlist")
            yield Input(placeholder="…or type a command ({} = the file), then Enter", id="owcmd")
            if ext:
                yield Checkbox(f"Always use this for .{ext} files", id="remember")
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        try:
            self.query_one(OptionList).action_first()
        except Exception:  # noqa: BLE001
            pass

    def _remember(self) -> bool:
        boxes = self.query("#remember")
        return bool(boxes and boxes.first(Checkbox).value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        oid = str(event.option.id)
        if oid == "default":
            self.dismiss(("default", None, False))
        else:
            self.dismiss(("desktop", oid[4:], self._remember()))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value.strip():
            self.dismiss(("cmd", event.value.strip(), self._remember()))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


_BITS = {"ur": 0o400, "uw": 0o200, "ux": 0o100, "gr": 0o040, "gw": 0o020, "gx": 0o010,
         "or": 0o004, "ow": 0o002, "ox": 0o001}


class PropertiesScreen(ModalScreen[Optional[tuple]]):
    """Details for the selection, and (for one item) editable permissions.
    Returns ("apply", mode, recursive) or None."""

    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self.paths = paths
        self.job = Job()
        self.size_text: Optional[str] = None
        self.st = None
        if len(paths) == 1:
            try:
                self.st = os.stat(paths[0])
            except OSError:
                self.st = None
        self.editable = self.st is not None and not os.path.islink(paths[0]) and (
            self.st.st_uid == os.getuid() or os.getuid() == 0)
        self.owner_name = ""
        self.group_options: list[tuple[str, int]] = []
        if self.st is not None:
            try:
                self.owner_name = pwd.getpwuid(self.st.st_uid).pw_name
            except KeyError:
                self.owner_name = str(self.st.st_uid)
            gids = {g.gr_gid for g in grp.getgrall()} if os.getuid() == 0 else \
                {os.getgid(), self.st.st_gid, *os.getgroups()}
            for gid in sorted(gids):
                try:
                    name = grp.getgrgid(gid).gr_name
                except KeyError:
                    name = str(gid)
                self.group_options.append((name, gid))
            self.group_options.sort(key=lambda o: o[0].casefold())

    # ----- text
    def info(self) -> Text:
        t = Text()

        def row(label: str, value: str) -> None:
            t.append(f"{label:<10}", style="dim")
            t.append(value + "\n")

        def when(ts: float) -> str:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

        size = self.size_text if self.size_text is not None else "calculating…"
        if len(self.paths) != 1:
            dirs = sum(1 for p in self.paths if is_real_dir(p))
            row("Items", f"{len(self.paths)}  ({dirs} folder{'s' if dirs != 1 else ''}, "
                         f"{len(self.paths) - dirs} other)")
            row("Location", str(self.paths[0].parent))
            row("Total size", size)
        else:
            p = self.paths[0]
            try:
                lst = os.lstat(p)
            except OSError as e:
                t.append(f"Cannot read: {e.strerror}", style="red")
                return t
            st = self.st or lst
            kind = ("Symbolic link" if stat.S_ISLNK(lst.st_mode) else "Folder" if stat.S_ISDIR(st.st_mode)
                    else mimetypes.guess_type(p.name)[0] or "File")
            row("Name", p.name)
            row("Location", str(p.parent))
            row("Type", kind)
            row("Size", size)
            if stat.S_ISLNK(lst.st_mode):
                row("Link to", os.readlink(p))
            row("Modified", when(st.st_mtime))
            row("Accessed", when(st.st_atime))
            row("Changed", when(st.st_ctime))
            try:
                owner = pwd.getpwuid(st.st_uid).pw_name
            except KeyError:
                owner = str(st.st_uid)
            try:
                group = grp.getgrgid(st.st_gid).gr_name
            except KeyError:
                group = str(st.st_gid)
            row("Owner", f"{owner}  (group {group})")
        t.rstrip()
        return t

    def _mode(self) -> int:
        m = 0
        for k, bit in _BITS.items():
            if self.query_one(f"#p-{k}", Checkbox).value:
                m |= bit
        return m

    def _update_mode_label(self) -> None:
        m = self._mode()
        special = stat.S_IMODE(self.st.st_mode) & 0o7000 if self.st else 0
        full = (self.st.st_mode & ~0o7777 if self.st else 0) | m | special
        self.query_one("#octal", Label).update(Text(f"Mode  {m | special:04o}   {stat.filemode(full)}"))

    # ----- layout
    def compose(self) -> ComposeResult:
        title = self.paths[0].name if len(self.paths) == 1 else f"{len(self.paths)} items"
        with Vertical(id="props"):
            yield Label(Text("Properties: " + title, style="bold"))
            with VerticalScroll(id="props-info"):
                yield Static(self.info(), id="pinfo")
            if self.st is not None and len(self.paths) == 1:
                yield Label(Text("Permissions" + ("" if self.editable else "  (only the owner can change these)"),
                                 style="bold"))
                mode = stat.S_IMODE(self.st.st_mode)
                for who, label in (("u", "Owner"), ("g", "Group"), ("o", "Others")):
                    with Horizontal(classes="permrow"):
                        yield Label(f"{label:<8}")
                        for bit, name in (("r", "read"), ("w", "write"), ("x", "execute")):
                            yield Checkbox(name, value=bool(mode & _BITS[who + bit]), id=f"p-{who}{bit}",
                                           disabled=not self.editable)
                yield Label("", id="octal")
                yield Label(Text("Ownership" + ("" if self.editable else "  (read-only)"), style="bold"))
                with Horizontal(classes="permrow"):
                    yield Label(f"{'Owner':<8}")
                    yield Input(value=self.owner_name, id="p-owner", disabled=os.getuid() != 0)
                    yield Label("  Group ")
                    yield Select(self.group_options, value=self.st.st_gid, allow_blank=False, id="p-group",
                                 disabled=not self.editable)
                yield Label("", id="perror")
                if is_real_dir(self.paths[0]):
                    yield Checkbox("Apply to everything inside (files keep their execute state)", id="rec",
                                   disabled=not self.editable)
            with Horizontal(id="dialog-buttons"):
                if self.editable and len(self.paths) == 1:
                    yield Button("Apply", variant="primary", id="apply")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        if self.st is not None and len(self.paths) == 1:
            self._update_mode_label()
        if any(is_real_dir(p) for p in self.paths) or len(self.paths) > 1:
            self.run_worker(self._calc, thread=True, group="props-size")
        else:
            self.size_text = self._fmt_size(sum(path_size(p) for p in self.paths))
            self.query_one("#pinfo", Static).update(self.info())

    @staticmethod
    def _fmt_size(n: int) -> str:
        return f"{human(n)}  ({n:,} bytes)"

    def _calc(self) -> None:
        try:
            total = sum(path_size(p, self.job) for p in self.paths)
        except Exception:  # noqa: BLE001  (cancelled)
            return
        try:
            self.app.call_from_thread(self._set_size, total)
        except Exception:  # noqa: BLE001
            pass

    def _set_size(self, total: int) -> None:
        self.size_text = self._fmt_size(total)
        try:
            self.query_one("#pinfo", Static).update(self.info())
        except Exception:  # noqa: BLE001
            pass

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        if str(event.checkbox.id).startswith("p-"):
            self._update_mode_label()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.job.cancel()
        if event.button.id == "apply":
            special = stat.S_IMODE(self.st.st_mode) & 0o7000
            rec = bool(self.query("#rec") and self.query_one("#rec", Checkbox).value)
            new = self._mode() | special
            uid: Optional[int] = None
            gid: Optional[int] = None
            owner = self.query_one("#p-owner", Input).value.strip()
            if owner and owner != self.owner_name:
                try:
                    uid = int(owner) if owner.isdigit() else pwd.getpwnam(owner).pw_uid
                except KeyError:
                    self.job = Job()
                    self.query_one("#perror", Label).update(Text(f"No such user: {owner}", style="red"))
                    return
            sel = self.query_one("#p-group", Select).value
            if isinstance(sel, int) and sel != self.st.st_gid:
                gid = sel
            if new == stat.S_IMODE(self.st.st_mode) and uid is None and gid is None and not rec:
                self.dismiss(None)
            else:
                self.dismiss(("apply", new, rec, uid, gid))
        else:
            self.dismiss(None)

    def action_close(self) -> None:
        self.job.cancel()
        self.dismiss(None)
