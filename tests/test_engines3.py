import json, os, stat, sys, tempfile
from pathlib import Path
home = Path(tempfile.mkdtemp())
for k in ("HOME","XDG_STATE_HOME","XDG_CONFIG_HOME"): os.environ[k] = str(home)
dd = home / "share"; os.environ["XDG_DATA_HOME"] = str(dd); os.environ["XDG_DATA_DIRS"] = str(home / "share2")
sys.path.insert(0, "/home/claude/rind")
from rind import openwith as O, devices as D, fileops as F

# ---------------- open with
(dd / "applications").mkdir(parents=True); (home / "share2" / "applications").mkdir(parents=True)
def desktop(d, name, **kw):
    f = d / "applications" / name
    f.write_text("[Desktop Entry]\n" + "\n".join(f"{k}={v}" for k, v in kw.items()) + "\n")
desktop(dd, "editor.desktop", Type="Application", Name="Fancy Editor", Exec="fancy %F", MimeType="text/plain;text/markdown;")
desktop(home / "share2", "viewer.desktop", Type="Application", Name="Alpha Viewer", Exec="alpha --icon %i --open %u", MimeType="text/plain;")
desktop(home / "share2", "vim.desktop", Type="Application", Name="Vim", Exec="vim %f", Terminal="true", MimeType="text/plain;")
desktop(home / "share2", "hidden.desktop", Type="Application", Name="Hidden", Exec="h %f", MimeType="text/plain;", NoDisplay="true")
desktop(home / "share2", "gone.desktop", Type="Application", Name="Gone", Exec="g %f", MimeType="text/plain;", TryExec="definitely-not-installed")
desktop(home / "share2", "img.desktop", Type="Application", Name="Image Thing", Exec="img %f", MimeType="image/*;")
desktop(home / "share2", "link.desktop", Type="Link", Name="Not an app", Exec="x")
apps = O.installed_apps(); assert sorted(apps) == ["editor.desktop", "img.desktop", "viewer.desktop", "vim.desktop"], sorted(apps)
assert [a.name for a in O.apps_for("text/plain")] == ["Alpha Viewer", "Fancy Editor", "Vim"], [a.name for a in O.apps_for("text/plain")]
assert [a.name for a in O.apps_for("text/x-python")] == ["Alpha Viewer", "Fancy Editor", "Vim"]     # text/* falls back to text/plain
assert [a.name for a in O.apps_for("image/png")] == ["Image Thing"]
(home / "mimeapps.list").write_text("[Default Applications]\ntext/plain=vim.desktop;\n[Added Associations]\ntext/plain=editor.desktop;\n")
assert [a.name for a in O.apps_for("text/plain")] == ["Vim", "Fancy Editor", "Alpha Viewer"], [a.name for a in O.apps_for("text/plain")]
f = Path("/tmp/my file.txt")
assert O.build_exec(apps["editor.desktop"], f) == ["fancy", str(f)]
assert O.build_exec(apps["viewer.desktop"], f) == ["alpha", "--open", str(f)]                   # %i and --icon dropped
assert O.build_exec(O.DesktopApp("x", "x", "tool --flag 100%% %f", False), f) == ["tool", "--flag", "100%", str(f)]
assert O.build_exec(O.DesktopApp("x", "x", "tool", False), f) == ["tool", str(f)]               # no field code: file appended
assert O.resolve({"desktop": "vim.desktop"}, f) == (["vim", str(f)], True)
assert O.resolve({"cmd": "mpv --fs {}"}, f) == (["mpv", "--fs", str(f)], False)
assert O.resolve({"desktop": "uninstalled.desktop"}, f) is None
O.remember(".TXT", {"desktop": "vim.desktop"}); assert O.recall("txt") == {"desktop": "vim.desktop"} and O.recall("md") is None
print("open with ok")

# ---------------- devices
lsblk = json.dumps({"blockdevices": [
  {"path": "/dev/sda", "label": None, "fstype": None, "size": 500107862016, "mountpoint": None, "rm": False, "hotplug": False, "type": "disk",
   "children": [{"path": "/dev/sda1", "label": "ROOT", "fstype": "ext4", "size": 4e11, "mountpoint": "/", "rm": False, "hotplug": False, "type": "part"},
                {"path": "/dev/sda2", "label": None, "fstype": "swap", "size": 1e9, "mountpoint": "[SWAP]", "rm": False, "hotplug": False, "type": "part"}]},
  {"path": "/dev/sdb", "fstype": None, "size": 16e9, "mountpoint": None, "rm": "1", "hotplug": "1", "type": "disk",
   "children": [{"path": "/dev/sdb1", "label": "USBSTICK", "fstype": "vfat", "size": "15999000000", "mountpoint": None, "rm": "1", "hotplug": "1", "type": "part"}]},
  {"path": "/dev/sdc1", "label": "Backup", "fstype": "ext4", "size": 2e12, "mountpoint": "/run/media/me/Backup", "rm": False, "hotplug": False, "type": "part"},
  {"path": "/dev/sdd1", "label": "DATA", "fstype": "ext4", "size": 1e12, "mountpoint": "/mnt/data", "rm": False, "hotplug": False, "type": "part"},
  {"path": "/dev/loop3", "fstype": "squashfs", "size": 1e8, "mountpoint": "/snap/core/1", "rm": False, "hotplug": False, "type": "loop"},
  {"path": "/dev/sde1", "fstype": "crypto_LUKS", "size": 1e9, "mountpoint": None, "rm": True, "hotplug": True, "type": "part"}]})
devs = D.parse_lsblk(lsblk)
assert [(d.dev, d.mounted) for d in devs] == [("/dev/sdb1", False), ("/dev/sdc1", True), ("/dev/sdd1", True)], devs
assert devs[0].label == "USBSTICK" and devs[0].size == 15999000000 and devs[1].mountpoint == Path("/run/media/me/Backup")
assert "(not mounted)" in devs[0].title and "(not mounted)" not in devs[1].title
assert D.parse_lsblk("garbage") == []
mounts = "proc /proc proc rw 0 0\nnas:/export/my\\040files /mnt/nas\\040share nfs4 rw 0 0\nuser@host:/ /home/me/remote fuse.sshfs rw 0 0\n/dev/sda1 / ext4 rw 0 0\n"
nets = D.parse_mounts(mounts)
assert [(d.dev, str(d.mountpoint), d.kind) for d in nets] == [("nas:/export/my files", "/mnt/nas share", "network"), ("user@host:/", "/home/me/remote", "network")], nets
print("devices parse ok")
# mount/unmount through a stub udisksctl
bin_ = home / "bin"; bin_.mkdir(); log = home / "udisks.log"
(bin_ / "udisksctl").write_text(f'#!/bin/sh\necho "$@" >> {log}\ncase "$1" in mount) echo "Mounted $3 at /run/media/me/USBSTICK.";; unmount) echo "Unmounted $3.";; esac\n')
(bin_ / "udisksctl").chmod(0o755)
os.environ["PATH"] = f"{bin_}:{os.environ['PATH']}"
assert D.mount(devs[0]) == Path("/run/media/me/USBSTICK")
D.unmount(devs[1]); assert "mount -b /dev/sdb1" in log.read_text() and "unmount -b /dev/sdc1" in log.read_text()
(bin_ / "udisksctl").write_text('#!/bin/sh\necho "Error mounting: not authorized" >&2; exit 1\n')
try: D.mount(devs[0]); raise SystemExit("expected failure")
except D.DeviceError as e: print("mount error ->", e)
print("mount/unmount ok")

# ---------------- chmod
t = home / "tree"; (t / "sub").mkdir(parents=True)
(t / "a.txt").write_text("a"); (t / "run.sh").write_text("#!/bin/sh"); os.chmod(t / "run.sh", 0o755); (t / "sub" / "b.txt").write_text("b")
(t / "ln").symlink_to("a.txt")
ch = F.apply_mode(t / "a.txt", 0o600); assert stat.S_IMODE(os.stat(t / "a.txt").st_mode) == 0o600 and ch == [(t / "a.txt", 0o644)]
ch = F.apply_mode(t, 0o750, recursive=True)
m = lambda p: stat.S_IMODE(os.stat(p).st_mode)
assert m(t) == 0o750 and m(t / "sub") == 0o750 and m(t / "a.txt") == 0o640 and m(t / "run.sh") == 0o750 and m(t / "sub" / "b.txt") == 0o640, [oct(m(p)) for p in (t, t/"sub", t/"a.txt", t/"run.sh")]
for p, old in reversed(ch): os.chmod(p, old)                      # undo restores everything
assert m(t) == 0o755 and m(t / "run.sh") == 0o755 and m(t / "sub" / "b.txt") == 0o644
real = os.chmod; n = [0]
def flaky(p, mode, *a, **k):
    n[0] += 1
    if n[0] == 3: raise PermissionError(1, "denied")
    return real(p, mode, *a, **k)
os.chmod = flaky
try: F.apply_mode(t, 0o700, recursive=True); raise SystemExit("expected failure")
except OSError: pass
os.chmod = real
assert m(t) == 0o755 and m(t / "a.txt") == 0o600 and m(t / "sub") == 0o755, "rollback failed"
print("chmod ok")
