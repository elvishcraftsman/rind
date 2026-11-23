import asyncio, os, sys, tempfile, time
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k, v in {"HOME": home, "XDG_DATA_HOME": home/"data", "XDG_STATE_HOME": home/"state", "XDG_CONFIG_HOME": home/"cfg"}.items():
    os.environ[k] = str(v)
sys.path.insert(0, "/home/claude/rind")
from rind.app import RindApp, FileTable, FilterInput
from rind.screens import MenuScreen, ConflictScreen, SearchScreen, ConfirmScreen, PromptScreen
from textual.widgets import OptionList, Static
import io
from rich.console import Console
def plain(static):
    c = static.content if hasattr(static, "content") else static._content
    buf = io.StringIO(); Console(file=buf, width=120, color_system=None).print(c); return buf.getvalue()

w = home / "w"; (w / "sub").mkdir(parents=True)
(w / "a.txt").write_text("alpha\n"); (w / "b.md").write_text("# beta\n"); (w / "sub" / "deep_report.txt").write_text("r")
(w / "pic.bin").write_bytes(bytes(range(256)))
dst = home / "dst"; dst.mkdir(); (dst / "a.txt").write_text("OLD")

async def main():
    app = RindApp(str(w))
    names = lambda: [p.name for p, _ in app.entries]
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        print("start", names(), "inotify active:", app.watcher.active)
        # 1. auto-refresh
        (w / "new.txt").write_text("n"); await pilot.pause(1.0)
        assert "new.txt" in names(), names()
        (w / "new.txt").unlink(); await pilot.pause(1.0)
        assert "new.txt" not in names()
        print("auto-refresh ok")
        # 2. filter
        await pilot.press("slash"); await pilot.press(*"txt"); await pilot.pause()
        assert names() == ["a.txt"], names()
        await pilot.press("enter"); assert app.focused.__class__.__name__ == "FileTable"
        await pilot.press("escape"); await pilot.pause()
        assert "b.md" in names() and app.filter_text == ""
        print("filter ok")
        # 3. preview
        await pilot.press("p"); await pilot.pause(0.6)
        txt = plain(app.query_one("#preview-body", Static))
        assert "sub" in txt or "Folder" in txt, txt[:200]
        await pilot.press("down"); await pilot.pause(0.6)           # a.txt
        txt = plain(app.query_one("#preview-body", Static))
        assert "alpha" in txt and "Size" in txt, txt[:300]
        for _ in range(3): await pilot.press("down")
        await pilot.pause(0.6)                                       # pic.bin
        txt = plain(app.query_one("#preview-body", Static))
        assert "Binary" in txt and "00000000" in txt, txt[:300]
        await pilot.press("p"); print("preview ok")
        # 4. search
        await pilot.press("ctrl+f"); await pilot.pause()
        assert isinstance(app.screen, SearchScreen)
        await pilot.press(*"report"); await pilot.pause(1.0)
        assert [p.name for p in app.screen.results] == ["deep_report.txt"]
        await pilot.press("enter"); await pilot.pause()
        assert app.cwd == w / "sub" and names() == ["deep_report.txt"]
        await pilot.press("backspace"); await pilot.pause()
        print("search ok")
        # 5. trash + undo
        app.query_one(FileTable).move_cursor(row=names().index("a.txt")); await pilot.pause()
        await pilot.press("d"); await pilot.pause()
        assert "a.txt" not in names() and (home/"data"/"Trash"/"files"/"a.txt").exists()
        await pilot.press("z"); await pilot.pause()
        assert (w / "a.txt").read_text() == "alpha\n" and "a.txt" in names()
        print("trash+undo ok")
        # 6. trash view + restore
        app.query_one(FileTable).move_cursor(row=names().index("a.txt")); await pilot.pause()
        await pilot.press("d"); await pilot.pause()
        app.navigate(app.trash_files); await pilot.pause()
        assert app.in_trash and names() == ["a.txt"]
        await pilot.press("R"); await pilot.pause()
        assert (w / "a.txt").exists() and names() == []
        app.navigate(w); await pilot.pause()
        print("trash view/restore ok")
        # 7. copy with conflict -> Replace + undo restores old file
        app.query_one(FileTable).move_cursor(row=names().index("a.txt")); await pilot.pause()
        await pilot.press("c"); app.navigate(dst); await pilot.pause()
        await pilot.press("v"); await pilot.pause(0.6)
        assert isinstance(app.screen, ConflictScreen), type(app.screen).__name__
        await pilot.press("r"); await pilot.pause(0.8)
        assert (dst / "a.txt").read_text() == "alpha\n", (dst / "a.txt").read_text()
        await pilot.press("z"); await pilot.pause(0.5)
        assert (dst / "a.txt").read_text() == "OLD", (dst / "a.txt").read_text()
        print("conflict replace + undo ok")
        # 8. conflict keep-both with apply-all
        await pilot.press("v"); await pilot.pause(0.6)
        assert isinstance(app.screen, ConflictScreen)
        await pilot.click("#all"); await pilot.press("k"); await pilot.pause(0.8)
        assert (dst / "a (copy).txt").exists() and (dst / "a.txt").read_text() == "OLD"
        print("keep both ok")
        # 9. rename + undo
        app.query_one(FileTable).move_cursor(row=names().index("a (copy).txt")); await pilot.pause()
        await pilot.press("f2"); await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        app.screen.query_one("#prompt-input").value = ""; await pilot.press(*"zzz.txt", "enter"); await pilot.pause()
        assert (dst / "zzz.txt").exists(); await pilot.press("z"); await pilot.pause()
        assert (dst / "a (copy).txt").exists()
        print("rename undo ok")
        # 10. move + undo
        app.query_one(FileTable).move_cursor(row=names().index("a (copy).txt")); await pilot.pause()
        await pilot.press("x"); app.navigate(w); await pilot.pause(); await pilot.press("v"); await pilot.pause(0.8)
        assert (w / "a (copy).txt").exists() and not (dst / "a (copy).txt").exists()
        await pilot.press("z"); await pilot.pause(0.8)
        assert (dst / "a (copy).txt").exists() and not (w / "a (copy).txt").exists()
        print("move undo ok")
        # 11. context menu shows trash/undo labels; quit-without-cd
        await pilot.click(FileTable, offset=(5, 2), button=3); await pilot.pause()
        assert isinstance(app.screen, MenuScreen); await pilot.press("escape")
        await pilot.press("Q"); await pilot.pause()
    print("skip_cwd:", app.skip_cwd, "cwd:", app.cwd)
asyncio.run(main())
