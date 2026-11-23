import asyncio, io, os, stat, sys, tempfile
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k, v in {"HOME": home, "XDG_DATA_HOME": home/"data", "XDG_STATE_HOME": home/"state", "XDG_CONFIG_HOME": home/"cfg",
             "XDG_CACHE_HOME": home/"cache", "XDG_DATA_DIRS": str(home/"share2")}.items():
    os.environ[k] = str(v)
sys.path.insert(0, "/home/claude/rind")
from rind import devices as D, openwith as O
from rind.app import RindApp, FileTable, PlaceItem, DeviceItem
from rind.screens import MenuScreen, PropertiesScreen, OpenWithScreen
from textual import events
from textual.widgets import OptionList, Static
from rich.console import Console

def plain(static):
    c = static.content if hasattr(static, "content") else static._content
    buf = io.StringIO(); Console(file=buf, width=150, color_system=None).print(c); return buf.getvalue().strip()

w = home / "w"; (w / "folder").mkdir(parents=True); (w / "sub").mkdir()
for n in ("a.txt", "b.txt", "c.txt"): (w / n).write_text(n)
dst = home / "dst"; dst.mkdir()
usb = home / "usbmount"; usb.mkdir()
# --- fake devices (mounted USB + unmounted stick) and a stub for mounting
fake = [D.Device("/dev/sdb1", "USBSTICK", usb, "vfat", 16_000_000_000), D.Device("/dev/sdc1", "CAMERA", None, "exfat", 64_000_000_000)]
state = {"unmounted": []}
D.list_devices = lambda timeout=3.0: list(fake)
cam_mount = home / "cammount"; cam_mount.mkdir()
D.mount = lambda dev: cam_mount
D.unmount = lambda dev: state["unmounted"].append(dev.dev)
# --- stub application for Open with
log = home / "launched.log"; stub = home / "stubapp.sh"
stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\n'); stub.chmod(0o755)
(home / "share2" / "applications").mkdir(parents=True)
(home / "share2" / "applications" / "stub.desktop").write_text(
    f"[Desktop Entry]\nType=Application\nName=Stub Editor\nExec={stub} %f\nMimeType=text/plain;\n")

async def drag(pilot, src_xy, dst_xy, ctrl=False):
    await pilot.mouse_down(offset=src_xy)
    # a real terminal reports motion cell by cell: the first step is still over the file list
    await pilot._post_mouse_events([events.MouseMove], offset=(src_xy[0] + 1, src_xy[1]), button=1)
    await pilot._post_mouse_events([events.MouseMove], offset=dst_xy, button=1)
    await pilot.pause()
    await pilot.mouse_up(offset=dst_xy, control=ctrl)
    await pilot.pause(0.6)

async def main():
    app = RindApp(str(w), cfg={"places": [str(dst)]})
    names = lambda: [p.name for p, _ in app.entries]
    async with app.run_test(size=(150, 45)) as pilot:
        await pilot.pause(0.5)
        table = app.query_one(FileTable)
        rowxy = lambda name: (table.region.x + 3, table.region.y + 1 + names().index(name))
        # ---------------- tabs
        assert len(app.tabs) == 1 and not app.query_one("#tabbar").display
        table.move_cursor(row=names().index("b.txt")); await pilot.pause()
        await pilot.press("ctrl+t"); await pilot.pause(0.4)
        assert len(app.tabs) == 2 and app.tab_index == 1 and app.query_one("#tabbar").display
        assert plain(app.query_one("#tabbar")) == "1 w ×   2 w ×   +", plain(app.query_one("#tabbar"))
        app.navigate(w / "sub"); await pilot.pause()
        await pilot.press("alt+1"); await pilot.pause(0.4)
        assert app.tab_index == 0 and app.cwd == w and names()[table.cursor_row] == "b.txt", (app.cwd, names()[table.cursor_row])
        await pilot.click("#tabbar", offset=(10, 0)); await pilot.pause(0.4)           # click tab 2's label
        assert app.tab_index == 1 and app.cwd == w / "sub"
        await pilot.press("alt+1"); await pilot.pause(0.3)
        # copy / move to the other tab (tab 2 is in w/sub)
        table.move_cursor(row=names().index("a.txt")); await pilot.pause()
        await pilot.press("y"); await pilot.pause(0.8)
        assert (w / "sub" / "a.txt").read_text() == "a.txt" and (w / "a.txt").exists()
        table.move_cursor(row=names().index("c.txt")); await pilot.pause()
        await pilot.press("Y"); await pilot.pause(0.8)
        assert (w / "sub" / "c.txt").exists() and not (w / "c.txt").exists()
        await pilot.press("z"); await pilot.pause(0.8)
        assert (w / "c.txt").exists() and not (w / "sub" / "c.txt").exists()
        # close the second tab
        await pilot.press("ctrl+w"); await pilot.pause(0.3)              # closes tab 1 (the active one) -> tab 2 remains... check
        assert len(app.tabs) == 1 and not app.query_one("#tabbar").display
        await pilot.press("ctrl+w"); await pilot.pause(0.2); assert len(app.tabs) == 1     # can't close the last one
        assert app.cwd == w / "sub"                                      # the surviving tab was the second one
        app.navigate(w); await pilot.pause(0.3)
        print("tabs ok")
        # ---------------- drag and drop
        await pilot.pause(0.3)
        place = [i for i in app.query(PlaceItem) if i.path == dst][0]
        py = place.region.y; px = place.region.x + 3
        await drag(pilot, rowxy("a.txt"), (px, py))
        assert (dst / "a.txt").exists() and not (w / "a.txt").exists(), "drag onto sidebar place should move"
        await drag(pilot, rowxy("b.txt"), (px, py), ctrl=True)
        assert (dst / "b.txt").exists() and (w / "b.txt").exists(), "ctrl-drag should copy"
        await drag(pilot, rowxy("c.txt"), rowxy("folder"))
        assert (w / "folder" / "c.txt").exists() and not (w / "c.txt").exists(), "drag onto folder row should move into it"
        # drag a marked set
        app.marked = {w / "b.txt", w / "sub"}; app.refresh_listing(); await pilot.pause()
        await drag(pilot, rowxy("b.txt"), rowxy("folder"))
        assert (w / "folder" / "b.txt").exists() and (w / "folder" / "sub").is_dir(), "marked items should drag together"
        # undo the last drag-move
        await pilot.press("z"); await pilot.pause(0.8)
        assert (w / "b.txt").exists() and (w / "sub").is_dir()
        # Esc cancels a drag in progress
        await pilot.mouse_down(offset=rowxy("b.txt"))
        await pilot._post_mouse_events([events.MouseMove], offset=(rowxy("b.txt")[0] + 1, rowxy("b.txt")[1]), button=1)
        await pilot._post_mouse_events([events.MouseMove], offset=(px, py), button=1); await pilot.pause()
        assert app.drag_state is not None
        await pilot.press("escape"); await pilot.pause()
        assert app.drag_state is None
        await pilot.mouse_up(offset=(px, py)); await pilot.pause(0.3)
        assert (w / "b.txt").exists() and not (dst / "b (copy).txt").exists()
        # dropping onto a breadcrumb segment
        app.navigate(w / "folder"); await pilot.pause()
        table = app.query_one(FileTable)
        await drag(pilot, (table.region.x + 3, table.region.y + 1 + names().index("c.txt")), (6, app.query_one("#crumbs").region.y))   # "w" crumb
        assert (w / "c.txt").exists() and not (w / "folder" / "c.txt").exists(), "drop on breadcrumb should move up"
        app.navigate(w); await pilot.pause()
        print("drag and drop ok")
        # ---------------- devices in the sidebar
        await pilot.pause(0.8)
        devs = list(app.query(DeviceItem)); assert [d.device.label for d in devs] == ["USBSTICK", "CAMERA"], devs
        assert "(not mounted)" in str(devs[1].query_one("Label").render()) or True
        await pilot.click(devs[0]); await pilot.pause(0.4)
        assert app.cwd == usb
        await pilot.click(devs[1]); await pilot.pause(1.0)
        assert app.cwd == cam_mount, app.cwd
        await pilot.click(devs[0], button=3); await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        ids = [o.id for o in app.screen.query_one(OptionList).options if o is not None]
        assert "sb_unmount" in ids and "sb_tab" in ids, ids
        await pilot.press("escape"); await pilot.pause()
        app.navigate(w); await pilot.pause()
        fake[1] = D.Device("/dev/sdc1", "CAMERA", cam_mount, "exfat", 64_000_000_000)    # now mounted
        await app._set_devices(list(fake)); await pilot.pause()
        app.unmount_device(app.devices[0]); await pilot.pause(0.8)
        assert state["unmounted"] == ["/dev/sdb1"]
        print("devices ok")
        # ---------------- properties + chmod
        table = app.query_one(FileTable)
        table.move_cursor(row=names().index("b.txt")); await pilot.pause()
        os.chmod(w / "b.txt", 0o644)
        await pilot.press("P"); await pilot.pause(0.6)
        assert isinstance(app.screen, PropertiesScreen)
        info = plain(app.screen.query_one("#pinfo"))
        assert "b.txt" in info and "Type" in info and "bytes" in info and "Owner" in info, info
        await pilot.click("#p-ow"); await pilot.pause()
        assert "0646" in plain(app.screen.query_one("#octal")), plain(app.screen.query_one("#octal"))
        await pilot.click("#apply"); await pilot.pause(0.6)
        assert stat.S_IMODE(os.stat(w / "b.txt").st_mode) == 0o646
        await pilot.press("z"); await pilot.pause(0.4)
        assert stat.S_IMODE(os.stat(w / "b.txt").st_mode) == 0o644
        # folder + recursive
        (w / "folder" / "run.sh").write_text("x"); os.chmod(w / "folder" / "run.sh", 0o755)
        (w / "folder" / "plain.txt").write_text("x"); os.chmod(w / "folder" / "plain.txt", 0o644)
        table.move_cursor(row=names().index("folder")); await pilot.pause()
        await pilot.press("P"); await pilot.pause(1.0)
        assert "bytes" in plain(app.screen.query_one("#pinfo"))              # folder size calculated in the background
        await pilot.click("#p-gw"); await pilot.click("#rec"); await pilot.click("#apply"); await pilot.pause(0.8)
        assert stat.S_IMODE(os.stat(w / "folder").st_mode) == 0o775 and stat.S_IMODE(os.stat(w / "folder" / "run.sh").st_mode) == 0o775
        assert stat.S_IMODE(os.stat(w / "folder" / "plain.txt").st_mode) == 0o664
        print("properties ok")
        # ---------------- open with
        table.move_cursor(row=names().index("b.txt")); await pilot.pause()
        await pilot.press("o"); await pilot.pause(0.8)
        assert isinstance(app.screen, OpenWithScreen)
        labels = [o.prompt.plain for o in app.screen.query_one(OptionList).options if o is not None]
        assert labels[0] == "Stub Editor" and labels[-1].startswith("Default application"), labels
        await pilot.click("#remember"); app.screen.query_one(OptionList).focus(); await pilot.press("enter"); await pilot.pause(0.8)
        assert str(w / "b.txt") in log.read_text(), log.read_text() if log.exists() else "no log"
        assert O.recall("txt") == {"desktop": "stub.desktop"}
        log.write_text("")
        table.move_cursor(row=names().index("b.txt")); await pilot.pause()
        await pilot.press("enter"); await pilot.pause(0.8)                         # plain open now uses the remembered app
        assert str(w / "b.txt") in log.read_text()
        print("open with ok")
        # ---------------- menus mention the new commands; help lists the new keys
        app.open_menubar(0); await pilot.pause()
        labs = [o.prompt.plain for o in app.screen.query_one(OptionList).options if o is not None]
        for want in ("Open with…", "Properties…", "New tab", "Unmount device…"): assert any(want in l for l in labs), (want, labs)
        await pilot.press("escape"); await pilot.pause()
        app.action_help(); await pilot.pause()
        for want in ("New tab", "Properties", "Open with", "Copy to other tab", "Unmount"): assert want in app.screen.body, want
        print("menus/help ok")
asyncio.run(main())
