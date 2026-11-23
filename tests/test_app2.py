import asyncio, contextlib, io, os, stat, subprocess, sys, tempfile, builtins
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k, v in {"HOME": home, "XDG_DATA_HOME": home/"data", "XDG_STATE_HOME": home/"state", "XDG_CONFIG_HOME": home/"cfg"}.items():
    os.environ[k] = str(v)
sys.path.insert(0, "/home/claude/rind")
from rind.app import RindApp, FileTable
from rind.screens import JumpScreen, PromptScreen, ConfirmScreen, MenuScreen
from textual.widgets import OptionList

w = home / "w"; (w / "big").mkdir(parents=True)
(w / "big" / "f1").write_bytes(b"x" * 3000); (w / "big" / "f2").write_bytes(b"y" * 2000)
for n in ("IMG_001.jpg", "IMG_002.jpg", "IMG_003.jpg"): (w / n).write_text(n)
(w / "link").symlink_to("IMG_001.jpg")
repo = home / "repo"; repo.mkdir()
def git(*a): subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
git("init", "-q", "-b", "main"); (repo / "a.txt").write_text("1"); git("add", "."); git("commit", "-qm", "i")
(repo / "a.txt").write_text("2"); (repo / "new.txt").write_text("n")
stub = home / "termstub.sh"; stub.write_text("#!/bin/sh\npwd > %s/term-cwd.txt\n" % home); stub.chmod(0o755)
editor = home / "ed.sh"; editor.write_text("#!/bin/sh\nsed -i 's/IMG_/Photo_/' \"$1\"\n"); editor.chmod(0o755)

def header_labels(app):
    return [str(c.label) for c in app.query_one(FileTable).columns.values()]

async def main():
    app = RindApp(str(w), cfg={"behavior": {"terminal": f"{stub} {{}}"}})
    app.suspend = contextlib.nullcontext
    names = lambda: [p.name for p, _ in app.entries]
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        print("columns:", header_labels(app))
        row = names().index("link")
        cell = str(app.query_one(FileTable).get_row_at(row)[0])
        assert "→ IMG_001.jpg" in cell, cell
        # --- columns: toggle permissions + owner via the view menu helper
        app.run_menu_choice("col:perms", 5, 5); await pilot.pause(); await pilot.press("escape"); await pilot.pause()
        assert "Permissions" in header_labels(app)
        perms = str(app.query_one(FileTable).get_row_at(0)[app.columns.index("perms") + 1])
        assert perms.startswith("drwx"), perms
        app.run_menu_choice("col:perms", 5, 5); await pilot.pause(); await pilot.press("escape"); await pilot.pause()
        assert "Permissions" not in header_labels(app)
        print("columns ok")
        # --- folder sizes
        app.query_one(FileTable).move_cursor(row=names().index("big")); await pilot.pause()
        await pilot.press("i"); await pilot.pause(0.8)
        assert app.dir_sizes[w / "big"][0] == 5000, app.dir_sizes
        sz = str(app.query_one(FileTable).get_row_at(names().index("big"))[1])
        assert "4.9K" in sz, sz
        print("folder size ok:", sz)
        # --- bulk pattern rename + undo
        app.query_one(FileTable).move_cursor(row=names().index("IMG_001.jpg")); await pilot.pause()
        await pilot.press("space", "space", "space"); await pilot.pause()
        assert len(app.marked) == 3, app.marked
        await pilot.press("f2"); await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        app.screen.query_one("#prompt-input").value = r"IMG_(\d+)=>Holiday_{n:02}_\1"; await pilot.press("enter"); await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen); await pilot.press("y"); await pilot.pause(0.3)
        assert "Holiday_01_001.jpg" in names() and "Holiday_03_003.jpg" in names() and "IMG_001.jpg" not in names(), names()
        await pilot.press("z"); await pilot.pause(0.3)
        assert "IMG_001.jpg" in names() and "Holiday_01_001.jpg" not in names()
        print("pattern rename + undo ok")
        # --- editor rename
        app.marked.clear(); app.refresh_listing()
        app.query_one(FileTable).move_cursor(row=names().index("IMG_001.jpg")); await pilot.pause()
        await pilot.press("space", "space"); await pilot.pause()
        os.environ["EDITOR"] = str(editor)
        await pilot.press("ctrl+r"); await pilot.pause(0.5)
        assert isinstance(app.screen, ConfirmScreen), type(app.screen).__name__
        await pilot.press("y"); await pilot.pause(0.3)
        assert "Photo_001.jpg" in names() and "Photo_002.jpg" in names() and "IMG_003.jpg" in names(), names()
        await pilot.press("z"); await pilot.pause(0.3)
        assert "IMG_001.jpg" in names()
        print("editor rename ok")
        # --- run command (suspend patched out, input() patched)
        builtins.input = lambda *a: ""
        app.marked.clear(); app.refresh_listing()
        app.query_one(FileTable).move_cursor(row=names().index("IMG_002.jpg")); await pilot.pause()
        await pilot.press("colon"); await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        app.screen.query_one("#prompt-input").value = "echo {} > ran.txt"; await pilot.press("enter"); await pilot.pause(0.6)
        assert (w / "ran.txt").read_text().strip() == "IMG_002.jpg", (w / "ran.txt").read_text()
        assert "ran.txt" in names()
        print("run command ok")
        # --- shell here with configured terminal
        await pilot.press("t"); await pilot.pause(0.6)
        assert (home / "term-cwd.txt").read_text().strip() == os.path.realpath(w)
        print("terminal here ok")
        # --- jump
        app.navigate(home / "data" if (home / "data").is_dir() else home); await pilot.pause()
        app.navigate(repo); await pilot.pause(); app.navigate(w); await pilot.pause()
        await pilot.press("j"); await pilot.pause()
        assert isinstance(app.screen, JumpScreen)
        await pilot.press(*"repo"); await pilot.pause()
        assert [p.name for p in app.screen.results] == ["repo"], app.screen.results
        await pilot.press("enter"); await pilot.pause()
        assert app.cwd == repo
        print("jump ok")
        # --- git column in the repo
        await pilot.pause(1.0)
        labels = header_labels(app); assert "Git" in labels, labels
        gi = {names()[i]: str(app.query_one(FileTable).get_row_at(i)[1]) for i in range(len(names()))}
        assert gi["a.txt"].strip() == "M" and gi["new.txt"] == "??", gi
        status = str(app.query_one("#status").render())
        assert "git: main" in status and "2 changed" in status, status
        print("git column ok:", gi)
        app.navigate(w); await pilot.pause(0.6)
        assert "Git" not in header_labels(app) and "git:" not in str(app.query_one("#status").render())
        print("git column hidden outside repo ok")
asyncio.run(main())
