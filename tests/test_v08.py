"""Drag-in (terminal paste), drag-out workarounds, configurable keys, opener errors, updater."""
import asyncio, os, stat, sys, tempfile
from pathlib import Path

home = Path(tempfile.mkdtemp())
for k, v in {"HOME": home, "XDG_DATA_HOME": home / "data", "XDG_STATE_HOME": home / "state",
             "XDG_CONFIG_HOME": home / "cfg"}.items():
    os.environ[k] = str(v)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from textual import events  # noqa: E402

from rind import dragout, keys, launcher, updater  # noqa: E402
from rind.app import FileTable, RindApp  # noqa: E402
from rind.screens import MenuScreen  # noqa: E402

w = home / "w"
w.mkdir()
(w / "a.txt").write_text("alpha")
(w / "my file.txt").write_text("spaced")
dst = home / "dst"
dst.mkdir()


# ---------------------------------------------------------------- parsing what a terminal pastes
def test_parse_dropped():
    a, s = w / "a.txt", w / "my file.txt"
    assert dragout.parse_dropped(str(a)) == [a]
    assert dragout.parse_dropped(f"'{a}' '{s}'") == [a, s]                       # VTE, WezTerm
    assert dragout.parse_dropped(f"{a} {str(s).replace(' ', chr(92) + ' ')}") == [a, s]  # backslash escapes
    assert dragout.parse_dropped(f"file://{a}\r\nfile://{str(s).replace(' ', '%20')}\r\n") == [a, s]
    assert dragout.parse_dropped(str(s)) == [s]                                  # unquoted, with a space
    assert dragout.parse_dropped("hello world") == []
    assert dragout.parse_dropped("'unbalanced") == []


def test_clipboard_payload():
    a = w / "my file.txt"
    mime, data = dragout.clipboard_payload([a], "gnome")
    assert mime == "x-special/gnome-copied-files"
    assert data == f"copy\nfile://{w}/my%20file.txt".encode()
    assert dragout.clipboard_payload([a], "gnome", cut=True)[1].startswith(b"cut\n")
    mime, data = dragout.clipboard_payload([a, w / "a.txt"], "uri-list")
    assert mime == "text/uri-list" and data.endswith(b"a.txt\r\n") and data.count(b"\r\n") == 2
    os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
    assert dragout.clipboard_format("auto") == "uri-list"
    os.environ["XDG_CURRENT_DESKTOP"] = "GNOME"
    assert dragout.clipboard_format("auto") == "gnome" and dragout.clipboard_format("uri-list") == "uri-list"


def test_helper_and_file_manager(tmp=None):
    bin_dir = Path(tempfile.mkdtemp())
    old = os.environ["PATH"]
    os.environ["PATH"] = str(bin_dir)
    try:
        assert dragout.find_helper() is None and dragout.helper_argv([w / "a.txt"]) is None
        for n in ("dragon", "ripdrag"):
            (bin_dir / n).write_text("#!/bin/sh\n")
            (bin_dir / n).chmod(0o755)
        assert dragout.find_helper() == ["ripdrag"]                               # ripdrag preferred
        assert dragout.helper_argv([w / "a.txt"]) == ["ripdrag", "--and-exit", str(w / "a.txt")]
        two = dragout.helper_argv([w / "a.txt", w / "my file.txt"])
        assert two[:3] == ["ripdrag", "--and-exit", "--all"] and len(two) == 5
        assert dragout.helper_argv([w / "a.txt"], "dragon -t", "") == ["dragon", "-t", str(w / "a.txt")]
        assert dragout.file_manager_candidates(w, [], "nautilus {}") == [["nautilus", str(w)]]
        assert dragout.file_manager_candidates(w, [], "thunar") == [["thunar", str(w)]]
    finally:
        os.environ["PATH"] = old


def test_keys_normalize_and_configure():
    assert keys.normalize_key("Ctrl+X") == "ctrl+x" and keys.normalize_key("Shift+z") == "Z"
    assert keys.normalize_key("/") == "slash" and keys.normalize_key("PgDn") == "pagedown"
    ids = {b.id for b in FileTable.BINDINGS}
    assert {"cut_files", "goto_tab_1", "goto_tab_9", "drag_out", "copy_to_system", "drag_helper",
            "show_in_file_manager", "open_cursor"} <= ids
    ov, err = keys.configure({"cut_files": "ctrl+k", "copy-files": ["c", "Alt+C"], "delete_items": "",
                              "nope": "x", "undo": 5}, ids)
    assert ov == {"cut_files": ["ctrl+k"], "copy_files": ["c", "alt+c"], "delete_items": []}
    assert len(err) == 2 and "nope" in err[0]


# ---------------------------------------------------------------- app level
def run(coro):
    return asyncio.run(coro)


def test_drop_copy_and_move():
    async def main():
        t = home / "t1"
        t.mkdir()
        app = RindApp(str(t))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.post_message(events.Paste(f"'{w / 'a.txt'}' '{w / 'my file.txt'}'"))
            await pilot.pause()
            assert isinstance(app.screen, MenuScreen), app.screen
            labels = [str(o.prompt) for o in app.screen.query_one("OptionList").options if o]
            assert labels[0].startswith("Copy 2 items") and labels[1].startswith("Move 2 items")
            await pilot.press("enter")                       # first choice: copy
            await pilot.pause(1.0)
            assert sorted(p.name for p in t.iterdir()) == ["a.txt", "my file.txt"]
            assert (w / "a.txt").exists()
            # not a path: nothing happens
            app.post_message(events.Paste("just some words"))
            await pilot.pause()
            assert not isinstance(app.screen, MenuScreen)
    run(main())


def test_drop_move_config():
    async def main():
        src = home / "src"; src.mkdir(); (src / "m.txt").write_text("m")
        t = home / "t2"; t.mkdir()
        app = RindApp(str(t), cfg={"drop": {"action": "move"}})
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.post_message(events.Paste(str(src / "m.txt")))
            await pilot.pause(1.0)
            assert (t / "m.txt").exists() and not (src / "m.txt").exists()
    run(main())


def test_custom_keys():
    async def main():
        t = home / "t3"; t.mkdir(); (t / "x.txt").write_text("x"); (t / "y.txt").write_text("y")
        app = RindApp(str(t), cfg={"keys": {"cut_files": "ctrl+k", "copy_files": "x", "go_up": ""}})
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")                           # was cut, now copy
            assert app.clip and app.clip[0] == "copy"
            app.clip = None
            await pilot.press("ctrl+k")
            assert app.clip and app.clip[0] == "cut"
            app.clip = None
            await pilot.press("c")                           # no longer bound to copy
            assert app.clip is None
            await pilot.press("backspace")                   # go_up switched off
            assert app.cwd == t
            await pilot.press("alt+up")                      # alt+up was go_up's too: off as well
            assert app.cwd == t
            assert app.key_hint("cut", "x") == "ctrl+k".replace("ctrl+k", "Ctrl+K")
            assert app.key_hint("up", "Backspace") == ""
        app2 = RindApp(str(t), cfg={"keys": {"bogus": "q"}})
        async with app2.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app2.key_errors
    run(main())


def test_drag_out_menu_and_errors():
    async def main():
        t = home / "t4"; t.mkdir(); (t / "z.txt").write_text("z")
        bin_dir = Path(tempfile.mkdtemp())
        old = os.environ["PATH"]
        os.environ["PATH"] = str(bin_dir)
        try:
            app = RindApp(str(t))
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.press("g")
                await pilot.pause()
                assert isinstance(app.screen, MenuScreen)
                texts = [str(o.prompt) for o in app.screen.query_one("OptionList").options if o]
                assert any("system clipboard" in x for x in texts) and any("install ripdrag" in x for x in texts)
                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("alt+d")                   # no helper installed: say so
                await pilot.pause()
                assert any("No drag helper" in n.message for n in app._notifications)
        finally:
            os.environ["PATH"] = old
    run(main())


def test_open_reports_failures_and_succeeds():
    async def main():
        t = home / "t5"; t.mkdir(); (t / "o.txt").write_text("o")
        bin_dir = Path(tempfile.mkdtemp())
        log = bin_dir / "log"
        op = bin_dir / "xdg-open"
        old = os.environ["PATH"]
        os.environ["PATH"] = f"{bin_dir}:{old}"
        try:
            op.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n"); op.chmod(0o755)
            app = RindApp(str(t))
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.press("enter"); await pilot.pause(0.6)
                assert log.read_text().strip() == str(t / "o.txt")
                op.write_text("#!/bin/sh\necho 'no handler for this type' >&2\nexit 3\n")
                await pilot.press("enter"); await pilot.pause(2.5)
                msgs = " ".join(n.message for n in app._notifications)
                assert "Could not open o.txt" in msgs and "xdg-open exited with 3: no handler for this type" in msgs, msgs
        finally:
            os.environ["PATH"] = old
    run(main())


def test_launcher_tries_next_candidate():
    res = []
    launcher.launch([["/nonexistent/prog"], ["sh", "-c", "exit 2"], ["true"]], lambda ok, m: res.append((ok, m)))
    for _ in range(50):
        if res: break
        import time; time.sleep(0.1)
    assert res == [(True, "")]
    res.clear()
    launcher.launch([["sh", "-c", "echo boom >&2; exit 4"]], lambda ok, m: res.append((ok, m)))
    for _ in range(50):
        if res: break
        import time; time.sleep(0.1)
    assert res and res[0][0] is False and "exited with 4" in res[0][1] and "boom" in res[0][1]


def test_updater_default_source_and_wheel_asset():
    assert updater.DEFAULT_SOURCE == "github:elvishcraftsman/rind"
    data = {"assets": [{"name": "rind-0.9.0-py3-none-any.whl",
                        "browser_download_url": "https://example.invalid/rind-0.9.0-py3-none-any.whl"},
                       {"name": "rind-0.9.0.tar.gz", "browser_download_url": "https://example.invalid/x.tar.gz"}]}
    rel = updater._asset_release(data, "v0.9.0", "notes")
    assert rel.version == "0.9.0" and rel.url.endswith(".whl") and rel.sha256 is None
    assert updater._asset_release({"assets": []}, "v1", "") is None
    assert updater.is_newer("0.10.0", "0.9.0")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
