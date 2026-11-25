import asyncio, io, os, sys, tarfile, tempfile, zipfile
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k, v in {"HOME": home, "XDG_DATA_HOME": home/"data", "XDG_STATE_HOME": home/"state",
             "XDG_CONFIG_HOME": home/"cfg", "XDG_CACHE_HOME": home/"cache"}.items():
    os.environ[k] = str(v)
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))
from rind.app import RindApp, FileTable, MenuLabel
from rind.screens import MenuScreen, PromptScreen, InfoScreen
from textual.widgets import OptionList
from rich.console import Console

def plain(static):
    c = static.content if hasattr(static, "content") else static._content
    buf = io.StringIO(); Console(file=buf, width=140, color_system=None).print(c); return buf.getvalue().strip()

w = home / "w"; (w / "sub" / "deeper").mkdir(parents=True)
(w / "one.txt").write_text("1"); (w / "two.txt").write_text("2")
with zipfile.ZipFile(w / "bundle.zip", "w") as z:
    z.writestr("proj/readme.md", "# hi"); z.writestr("proj/src/main.py", "print(1)")

def labels(app):
    return [o.prompt.plain if hasattr(o.prompt, "plain") else str(o.prompt) for o in app.screen.query_one(OptionList).options if o is not None]

async def main():
    app = RindApp(str(w))
    names = lambda: [p.name for p, _ in app.entries]
    async with app.run_test(size=(150, 45)) as pilot:
        await pilot.pause()
        # ---------- menu bar
        bar = list(app.query(MenuLabel)); assert [str(b.render()).strip() for b in bar] == ["File", "Edit", "View", "Go", "Tools", "Help"]
        await pilot.click(bar[0]); await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        ls = labels(app); assert ls[0].startswith("New folder…") and ls[0].rstrip().endswith("n"), ls[:3]
        await pilot.press("right"); await pilot.pause()
        assert labels(app)[0].startswith("Undo"), labels(app)[:2]           # Edit
        await pilot.click(bar[2]); await pilot.pause()                       # click View while a menu is open
        assert any("Preview pane" in l for l in labels(app)) and not any("Undo" in l for l in labels(app))
        await pilot.press("escape"); await pilot.pause()
        assert not isinstance(app.screen, MenuScreen)
        await pilot.press("alt+m"); await pilot.pause()
        assert isinstance(app.screen, MenuScreen) and labels(app)[0].startswith("New folder")
        await pilot.press("enter"); await pilot.pause()                      # first item: New folder
        assert isinstance(app.screen, PromptScreen); await pilot.press("escape"); await pilot.pause()
        app.open_menubar(1); await pilot.pause()                             # Edit: Paste disabled with empty clipboard
        paste = [o for o in app.screen.query_one(OptionList).options if o is not None and o.id == "paste"][0]
        assert paste.disabled; await pilot.press("escape"); await pilot.pause()
        app.open_menubar(5); await pilot.pause()
        await pilot.press("enter"); await pilot.pause()                      # Help > Keyboard shortcuts
        assert isinstance(app.screen, InfoScreen) and "Extract archive" in app.screen.body and "Alt+Right" in app.screen.body
        await pilot.press("escape"); await pilot.pause()
        print("menu bar ok")
        # ---------- new file, duplicate
        await pilot.press("N"); await pilot.pause()
        app.screen.query_one("#prompt-input").value = "notes.txt"; await pilot.press("enter"); await pilot.pause()
        assert (w / "notes.txt").exists() and "notes.txt" in names()
        await pilot.press("z"); await pilot.pause()
        assert not (w / "notes.txt").exists()
        app.query_one(FileTable).move_cursor(row=names().index("one.txt")); await pilot.pause()
        await pilot.press("ctrl+d"); await pilot.pause(0.6)
        assert (w / "one (copy).txt").read_text() == "1"
        print("new file + duplicate ok")
        # ---------- forward history
        app.navigate(w / "sub"); await pilot.pause(); app.navigate(w / "sub" / "deeper"); await pilot.pause()
        await pilot.press("alt+left"); await pilot.pause(); assert app.cwd == w / "sub"
        await pilot.press("alt+right"); await pilot.pause(); assert app.cwd == w / "sub" / "deeper"
        app.navigate(w / "sub"); await pilot.pause(); assert app.forward == []
        print("forward history ok")
        # ---------- breadcrumbs
        app.navigate(w / "sub" / "deeper"); await pilot.pause()
        assert plain(app.query_one("#crumbs")) == "~ › w › sub › deeper", plain(app.query_one("#crumbs"))
        await pilot.click("#crumbs", offset=(2 + 1 + 3, 0)); await pilot.pause()      # "w"
        assert app.cwd == w, app.cwd
        print("breadcrumbs ok")
        # ---------- archives: browse read-only
        app.query_one(FileTable).move_cursor(row=names().index("bundle.zip")); await pilot.pause()
        await pilot.press("enter"); await pilot.pause(1.0)
        assert app.in_archive and names() == ["proj"], (app.cwd, names())
        shown = app.query_one("#path").value
        assert shown == str(w / "bundle.zip") + "!/", shown
        assert "ARCHIVE" in plain(app.query_one("#status"))
        await pilot.press("enter"); await pilot.pause(0.3)                              # into proj
        assert app.query_one("#path").value.endswith("bundle.zip!/proj") and "bundle.zip › proj" in plain(app.query_one("#crumbs"))
        app.query_one(FileTable).move_cursor(row=names().index("readme.md")); await pilot.pause()
        await pilot.press("d"); await pilot.pause(); assert "readme.md" in names()           # delete refused
        await pilot.press("x"); await pilot.pause(); assert app.clip is None                 # cut refused
        await pilot.press("c"); await pilot.pause(); assert app.clip and app.clip[0] == "copy"
        await pilot.press("backspace"); await pilot.pause(); await pilot.press("backspace"); await pilot.pause()
        assert app.cwd == w and not app.in_archive and names()[app.query_one(FileTable).cursor_row] == "bundle.zip"
        await pilot.press("v"); await pilot.pause(0.6)
        assert (w / "readme.md").read_text() == "# hi"
        print("archive browse ok")
        # path bar understands the virtual path
        app.action_focus_path(); await pilot.pause()
        inp = app.query_one("#path"); inp.value = str(w / "bundle.zip") + "!/proj/src"; await pilot.press("enter"); await pilot.pause()
        assert app.in_archive and names() == ["main.py"], (app.cwd, names())
        app.navigate(w); await pilot.pause()
        # ---------- extract + undo
        app.query_one(FileTable).move_cursor(row=names().index("bundle.zip")); await pilot.pause()
        await pilot.press("e"); await pilot.pause(1.0)
        assert (w / "proj" / "src" / "main.py").read_text() == "print(1)", names()
        await pilot.press("z"); await pilot.pause(0.5)
        assert not (w / "proj").exists()
        print("extract + undo ok")
        # ---------- compress
        app.marked.clear(); app.refresh_listing()
        app.query_one(FileTable).move_cursor(row=names().index("one.txt")); await pilot.pause()
        await pilot.press("space", "space"); await pilot.pause()
        assert len(app.marked) == 2
        await pilot.press("C"); await pilot.pause()
        assert isinstance(app.screen, PromptScreen) and app.screen.query_one("#prompt-input").value == "archive.zip"
        app.screen.query_one("#prompt-input").value = "pack.tar.gz"; await pilot.press("enter"); await pilot.pause(1.0)
        assert (w / "pack.tar.gz").exists() and "pack.tar.gz" in names()
        with tarfile.open(w / "pack.tar.gz") as t: names_in = sorted(t.getnames())
        assert names_in == ["one.txt", "readme.md"], names_in; print("packed:", names_in)
        # bad name rejected
        await pilot.press("C"); await pilot.pause()
        app.screen.query_one("#prompt-input").value = "pack.rar"; await pilot.press("enter"); await pilot.pause()
        assert not (w / "pack.rar").exists()
        print("compress ok")
    print("cache cleaned on exit:", not (home / "cache" / "rind" / "archives").exists() or not any((home / "cache" / "rind" / "archives").iterdir()))
asyncio.run(main())
