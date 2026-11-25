import asyncio, io, os, sys, tempfile
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k, v in {"HOME": home, "XDG_DATA_HOME": home/"data", "XDG_STATE_HOME": home/"state", "XDG_CONFIG_HOME": home/"cfg",
             "XDG_CACHE_HOME": home/"cache"}.items():
    os.environ[k] = str(v)
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))
from rind.app import RindApp, FileTable
from textual import events
from textual.widgets import OptionList

w = home / "w"; (w / "sub1").mkdir(parents=True); (w / "sub2").mkdir()
for n in ("a.txt", "b.txt", "c.txt"): (w / n).write_text(n)
(w / "sub2" / "inside.txt").write_text("i")

def rows(table): return [str(table.get_row_at(i)[0]).strip(" ●").rstrip("/") for i in range(table.row_count)]

async def main():
    app = RindApp(str(w))
    async with app.run_test(size=(170, 45)) as pilot:
        await pilot.pause(0.5)
        names = lambda: [p.name for p, _ in app.entries]
        # --------------- Tab outside split view keeps its normal job
        assert not app.split and not app.table_for(1).display
        await pilot.press("tab"); await pilot.pause()
        assert app.active_side == 0 and not app.split
        app.table.focus(); await pilot.pause()
        # --------------- toggle on
        await pilot.press("f4"); await pilot.pause(0.6)
        assert app.split and len(app.tabs) == 2 and app.table_for(1).display and app.active_side == 0
        assert rows(app.table_for(0)) == rows(app.table_for(1)) == ["sub1", "sub2", "a.txt", "b.txt", "c.txt"], (rows(app.table_for(0)), rows(app.table_for(1)))
        print("split on ok")
        # --------------- navigate in the right pane with real mouse clicks; the left pane stays put
        right = app.table_for(1)
        await pilot.click(right, offset=(3, 1 + 1), times=2); await pilot.pause(0.6)           # double-click "sub2" in the right pane
        assert app.active_side == 1 and app.cwd == w / "sub2", (app.active_side, app.cwd)
        assert rows(app.table_for(1)) == ["inside.txt"] and rows(app.table_for(0)) == ["sub1", "sub2", "a.txt", "b.txt", "c.txt"]
        assert app.table is app.table_for(1)
        # --------------- Tab switches panes
        await pilot.press("tab"); await pilot.pause(0.5)
        assert app.active_side == 0 and app.cwd == w and app.focused is app.table_for(0), (app.active_side, app.cwd)
        await pilot.press("tab"); await pilot.pause(0.5)
        assert app.active_side == 1 and app.cwd == w / "sub2"
        await pilot.click(app.table_for(0), offset=(3, 1)); await pilot.pause(0.5)              # clicking a pane activates it
        assert app.active_side == 0 and app.cwd == w
        print("pane navigation ok")
        # --------------- marks survive a round trip between panes
        app.table.move_cursor(row=names().index("b.txt")); await pilot.pause()
        await pilot.press("space"); await pilot.pause()
        assert w / "b.txt" in app.marked
        await pilot.press("tab"); await pilot.press("tab"); await pilot.pause(0.6)
        assert app.active_side == 0 and w / "b.txt" in app.marked and "● b.txt" in [str(app.table.get_row_at(i)[0]).strip() for i in range(app.table.row_count)]
        app.marked.clear(); app.refresh_listing(); await pilot.pause()
        # --------------- copy / move to the other pane; the inactive pane refreshes by itself
        app.table.move_cursor(row=names().index("a.txt")); await pilot.pause()
        await pilot.press("y"); await pilot.pause(0.9)
        assert (w / "sub2" / "a.txt").read_text() == "a.txt" and (w / "a.txt").exists()
        assert "a.txt" in rows(app.table_for(1)), rows(app.table_for(1))
        app.table.move_cursor(row=names().index("c.txt")); await pilot.pause()
        await pilot.press("Y"); await pilot.pause(0.9)
        assert (w / "sub2" / "c.txt").exists() and not (w / "c.txt").exists() and "c.txt" in rows(app.table_for(1)) and "c.txt" not in rows(app.table_for(0))
        await pilot.press("z"); await pilot.pause(0.9)
        assert (w / "c.txt").exists() and "c.txt" in rows(app.table_for(0)) and "c.txt" not in rows(app.table_for(1))
        print("copy/move between panes ok")
        # --------------- drag a file from the left pane onto the right pane's list (empty space or a file row)
        t0, t1 = app.table_for(0), app.table_for(1)
        src = (t0.region.x + 3, t0.region.y + 1 + names().index("b.txt"))
        dst_xy = (t1.region.x + 3, t1.region.y + 1 + 3)
        await pilot.mouse_down(offset=src)
        await pilot._post_mouse_events([events.MouseMove], offset=(src[0] + 1, src[1]), button=1)
        await pilot._post_mouse_events([events.MouseMove], offset=dst_xy, button=1); await pilot.pause()
        await pilot.mouse_up(offset=dst_xy); await pilot.pause(0.9)
        assert (w / "sub2" / "b.txt").exists() and not (w / "b.txt").exists(), "drag onto the other pane should move there"
        assert "b.txt" in rows(app.table_for(1))
        await pilot.press("z"); await pilot.pause(0.9)
        assert (w / "b.txt").exists()
        print("drag between panes ok")
        # --------------- a change on disk in the inactive pane's folder shows up without touching it
        (w / "sub2" / "appeared.txt").write_text("!")
        await pilot.pause(2.6)
        assert "appeared.txt" in rows(app.table_for(1)), rows(app.table_for(1))
        # --------------- sorting applies to both panes
        app.sort_by, app.sort_reverse = "name", True; app.refresh_listing(); await pilot.pause()
        assert rows(app.table_for(1))[0] != "a.txt" or len(rows(app.table_for(1))) == 1
        app.sort_by, app.sort_reverse = "name", False; app.refresh_listing(); await pilot.pause()
        # --------------- menus
        app.open_menubar(2); await pilot.pause()
        labs = [o.prompt.plain for o in app.screen.query_one(OptionList).options if o is not None]
        assert any(l.startswith("✓ Split view") for l in labs), labs
        await pilot.press("escape"); await pilot.pause()
        app.open_menubar(1); await pilot.pause()
        labs = [o.prompt.plain for o in app.screen.query_one(OptionList).options if o is not None]
        assert any("Copy to other pane" in l for l in labs), labs
        await pilot.press("escape"); await pilot.pause()
        # --------------- closing the other pane's tab leaves split view; closing split keeps the tab
        other_idx = app.tabs.index(app.pane_tabs[1 - app.active_side])
        app.action_close_tab(other_idx); await pilot.pause(0.4)
        assert not app.split and len(app.tabs) == 1 and not app.table_for(1).display
        await pilot.press("f4"); await pilot.pause(0.4)
        assert app.split and len(app.tabs) == 2
        await pilot.press("tab"); await pilot.pause(0.4)                      # active = right pane
        await pilot.press("f4"); await pilot.pause(0.5)                        # leave split while the right pane is active
        assert not app.split and app.active_side == 0 and app.table is app.table_for(0) and len(app.tabs) == 2
        assert rows(app.table_for(0)) and app.cwd.is_dir()
        print("leaving split ok; tab for the other pane is kept:", len(app.tabs), "tabs")
        # --------------- three tabs in split view: switching tabs replaces the active pane's tab
        await pilot.press("f4"); await pilot.pause(0.4)
        app.new_tab(w / "sub1"); await pilot.pause(0.6)
        assert app.split and app.cwd == w / "sub1" and len(app.tabs) == 3
        assert all(t in app.tabs for t in app.pane_tabs) and app.pane_tabs[0] is not app.pane_tabs[1]
        app.action_goto_tab(0); await pilot.pause(0.5)
        assert app.pane_tabs[app.active_side] is app.tabs[0] and app.pane_tabs[0] is not app.pane_tabs[1]
        print("tabs inside split ok")
asyncio.run(main())
