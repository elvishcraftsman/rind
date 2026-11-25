import asyncio, os, stat, sys, tempfile
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k, v in {"HOME": home, "XDG_DATA_HOME": home/"data", "XDG_STATE_HOME": home/"state", "XDG_CONFIG_HOME": home/"cfg",
             "XDG_CACHE_HOME": home/"cache"}.items():
    os.environ[k] = str(v)
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))
from rind.app import RindApp, FileTable
from rind.screens import PropertiesScreen, PromptScreen
from textual.widgets import Select

w = home / "w"; (w / "sub").mkdir(parents=True)
for n in ("a.txt", "b.txt", "c.txt"): (w / n).write_text(n)
dst = home / "dst"; dst.mkdir()

async def main():
    app = RindApp(str(w))
    names = lambda: [p.name for p, _ in app.entries]
    async with app.run_test(size=(150, 45)) as pilot:
        await pilot.pause(0.5)
        t = app.query_one(FileTable)
        sel = lambda n: (t.move_cursor(row=names().index(n)), None)
        # ---- redo: trash
        sel("a.txt"); await pilot.pause(); await pilot.press("d"); await pilot.pause()
        assert "a.txt" not in names()
        await pilot.press("z"); await pilot.pause(); assert "a.txt" in names()
        await pilot.press("Z"); await pilot.pause(0.3); assert "a.txt" not in names() and not app.redo_stack
        await pilot.press("z"); await pilot.pause(); assert "a.txt" in names()
        print("redo trash ok")
        # ---- redo chain: two renames, undo both, redo both in order
        sel("a.txt"); await pilot.pause(); await pilot.press("f2"); await pilot.pause()
        app.screen.query_one("#prompt-input").value = "a1.txt"; await pilot.press("enter"); await pilot.pause()
        await pilot.press("f2"); await pilot.pause()
        app.screen.query_one("#prompt-input").value = "a2.txt"; await pilot.press("enter"); await pilot.pause()
        assert "a2.txt" in names()
        await pilot.press("z", "z"); await pilot.pause(); assert "a.txt" in names() and len(app.redo_stack) == 2
        await pilot.press("Z"); await pilot.pause(); assert "a1.txt" in names() and len(app.redo_stack) == 1, (names(), len(app.redo_stack))
        await pilot.press("ctrl+y"); await pilot.pause(); assert "a2.txt" in names() and not app.redo_stack
        await pilot.press("z", "z"); await pilot.pause()
        # a brand-new action clears the redo chain
        await pilot.press("N"); await pilot.pause()
        app.screen.query_one("#prompt-input").value = "fresh.txt"; await pilot.press("enter"); await pilot.pause()
        assert app.redo_stack == [], "a new action must clear redo"
        await pilot.press("z"); await pilot.pause(); assert not (w / "fresh.txt").exists()
        await pilot.press("Z"); await pilot.pause(); assert (w / "fresh.txt").exists()          # redo of new file
        await pilot.press("z"); await pilot.pause()
        print("redo chain ok")
        # ---- redo: new folder, move (job), bulk rename, permissions
        await pilot.press("n"); await pilot.pause()
        app.screen.query_one("#prompt-input").value = "newdir"; await pilot.press("enter"); await pilot.pause()
        await pilot.press("z"); await pilot.pause(); assert not (w / "newdir").exists()
        await pilot.press("Z"); await pilot.pause(); assert (w / "newdir").is_dir()
        await pilot.press("z"); await pilot.pause()
        sel("b.txt"); await pilot.pause(); await pilot.press("x"); app.navigate(dst); await pilot.pause(); await pilot.press("v"); await pilot.pause(0.8)
        assert (dst / "b.txt").exists()
        await pilot.press("z"); await pilot.pause(0.8); assert (w / "b.txt").exists() and not (dst / "b.txt").exists()
        await pilot.press("Z"); await pilot.pause(1.0); assert (dst / "b.txt").exists() and not (w / "b.txt").exists(), "redo of a move"
        await pilot.press("z"); await pilot.pause(0.8)
        app.navigate(w); await pilot.pause()
        sel("b.txt"); await pilot.pause(); os.chmod(w / "b.txt", 0o644)
        app._apply_props(w / "b.txt", 0o600, False, None, None); await pilot.pause(0.6)
        assert stat.S_IMODE(os.stat(w / "b.txt").st_mode) == 0o600
        await pilot.press("z"); await pilot.pause(0.4); assert stat.S_IMODE(os.stat(w / "b.txt").st_mode) == 0o644
        await pilot.press("Z"); await pilot.pause(0.8); assert stat.S_IMODE(os.stat(w / "b.txt").st_mode) == 0o600, "redo of chmod"
        await pilot.press("z"); await pilot.pause(0.4)
        print("redo new folder / move / permissions ok")
        # ---- extract breaks the redo chain (it has no redo): older redo entries are dropped
        sel("c.txt"); await pilot.pause(); await pilot.press("d"); await pilot.pause()
        assert app.redo_stack == [] and len(app.undo_stack) >= 1
        await pilot.press("z"); await pilot.pause(); assert len(app.redo_stack) == 1
        app.push_undo("fake extract", lambda: None)                      # an entry without redo...
        app.redo_stack = list(app.redo_stack); await pilot.press("z"); await pilot.pause()
        assert app.redo_stack == [], "undoing an entry with no redo must clear the chain"
        print("redo chain break ok")
        # ---- ownership: the group Select is present, and _apply_props passes uid/gid through to lchown
        calls = []
        real = os.lchown
        os.lchown = lambda p, u, g, *a, **k: calls.append((Path(p).name, u, g))
        app._apply_props(w / "a.txt", stat.S_IMODE(os.stat(w / "a.txt").st_mode), False, 4242, 4343); await pilot.pause(0.6)
        assert calls == [("a.txt", 4242, 4343)], calls
        await pilot.press("z"); await pilot.pause(0.4)
        assert calls[-1] == ("a.txt", os.getuid(), os.getgid()), calls
        os.lchown = real
        sel("a.txt"); await pilot.pause(); await pilot.press("P"); await pilot.pause(0.6)
        assert isinstance(app.screen, PropertiesScreen)
        grp_sel = app.screen.query_one("#p-group", Select)
        assert grp_sel.value == os.stat(w / "a.txt").st_gid and len(app.screen.group_options) >= 1
        owner_in = app.screen.query_one("#p-owner")
        assert owner_in.disabled == (os.getuid() != 0)
        if os.getuid() == 0:                                     # root may name an owner: unknown names are rejected in the dialog
            owner_in.value = "no-such-user-xyz"; await pilot.click("#apply"); await pilot.pause()
            assert isinstance(app.screen, PropertiesScreen) and "No such user" in str(app.screen.query_one("#perror").render())
        await pilot.press("escape"); await pilot.pause()
        print("ownership ok (running as uid %d)" % os.getuid())
asyncio.run(main())
