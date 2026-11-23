"""Mounted and mountable devices for the sidebar (disks, USB sticks, network mounts)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .fileops import human

NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "fuse.rclone", "davfs", "fuse.davfs"}
IGNORE_FS = {"swap", "LVM2_member", "crypto_LUKS", "linux_raid_member"}
USER_MOUNT_ROOTS = ("/media/", "/run/media/", "/mnt/")


class DeviceError(Exception):
    pass


@dataclass(frozen=True)
class Device:
    dev: str
    label: str
    mountpoint: Optional[Path]
    fstype: str = ""
    size: int = 0
    kind: str = "disk"  # "disk" or "network"

    @property
    def mounted(self) -> bool:
        return self.mountpoint is not None

    @property
    def title(self) -> str:
        t = self.label
        if self.size:
            t += f"  {human(self.size)}"
        return t if self.mounted else t + "  (not mounted)"


def _truthy(v) -> bool:
    return v in (True, 1, "1", "true")


def parse_lsblk(text: str) -> list[Device]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out: list[Device] = []

    def walk(nodes) -> None:
        for n in nodes or []:
            fs, mp = n.get("fstype"), n.get("mountpoint")
            if fs and fs not in IGNORE_FS and n.get("path"):
                removable = _truthy(n.get("rm")) or _truthy(n.get("hotplug"))
                user_mounted = bool(mp) and str(mp).startswith(USER_MOUNT_ROOTS)
                if n.get("type") != "loop" or user_mounted:
                    if removable or user_mounted:
                        try:
                            size = int(n.get("size") or 0)
                        except (TypeError, ValueError):
                            size = 0
                        out.append(Device(n["path"], n.get("label") or Path(n["path"]).name,
                                          Path(mp) if mp else None, fs, size))
            walk(n.get("children"))

    walk(data.get("blockdevices"))
    return out


def parse_mounts(text: str) -> list[Device]:
    out: list[Device] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] not in NETWORK_FS:
            continue
        unesc = lambda s: re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)  # noqa: E731
        mp = Path(unesc(parts[1]))
        out.append(Device(unesc(parts[0]), mp.name or unesc(parts[0]), mp, parts[2], 0, "network"))
    return out


def list_devices(timeout: float = 3.0) -> list[Device]:
    out: list[Device] = []
    if shutil.which("lsblk"):
        try:
            r = subprocess.run(["lsblk", "-J", "-b", "-o", "PATH,LABEL,FSTYPE,SIZE,MOUNTPOINT,RM,HOTPLUG,TYPE"],
                               capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                out += parse_lsblk(r.stdout)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        out += parse_mounts(Path("/proc/self/mounts").read_text())
    except OSError:
        pass
    return sorted(out, key=lambda d: (d.kind, d.label.casefold()))


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise DeviceError(f"{cmd[0]} timed out") from None
    except OSError as e:
        raise DeviceError(f"{cmd[0]}: {e.strerror}") from None


def mount(dev: Device) -> Path:
    """Mount a block device the way a desktop does (no root needed). Returns the mount point."""
    if shutil.which("udisksctl"):
        r = _run(["udisksctl", "mount", "-b", dev.dev, "--no-user-interaction"])
        if r.returncode == 0:
            m = re.search(r" at (.+?)\.?\s*$", r.stdout.strip())
            if m:
                return Path(m.group(1))
        err = (r.stderr or r.stdout).strip()
    elif shutil.which("gio"):
        r = _run(["gio", "mount", "-d", dev.dev])
        if r.returncode == 0:
            for d in list_devices():
                if d.dev == dev.dev and d.mountpoint:
                    return d.mountpoint
        err = (r.stderr or r.stdout).strip()
    else:
        err = "install udisks2 (udisksctl) to mount devices"
    raise DeviceError(err or "mount failed")


def unmount(dev: Device) -> None:
    attempts: list[list[str]] = []
    if dev.kind == "disk" and shutil.which("udisksctl"):
        attempts.append(["udisksctl", "unmount", "-b", dev.dev, "--no-user-interaction"])
    mp = str(dev.mountpoint)
    for tool, args in (("gio", ["gio", "mount", "-u", mp]), ("fusermount3", ["fusermount3", "-u", mp]),
                       ("fusermount", ["fusermount", "-u", mp]), ("umount", ["umount", mp])):
        if shutil.which(tool):
            attempts.append(args)
    if not attempts:
        raise DeviceError("no unmount tool found (install udisks2)")
    err = ""
    for cmd in attempts:
        r = _run(cmd)
        if r.returncode == 0:
            return
        err = (r.stderr or r.stdout).strip() or err
    raise DeviceError(err or "unmount failed")
