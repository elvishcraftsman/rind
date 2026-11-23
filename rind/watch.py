"""Watch the current folder for changes using inotify (Linux, no dependencies)."""
from __future__ import annotations

import os
from typing import Callable

IN_MODIFY, IN_ATTRIB = 0x2, 0x4
IN_MOVED_FROM, IN_MOVED_TO = 0x40, 0x80
IN_CREATE, IN_DELETE = 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF = 0x400, 0x800
MASK = (IN_MODIFY | IN_ATTRIB | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
        | IN_DELETE_SELF | IN_MOVE_SELF)


class DirWatcher:
    """Calls `callback` (in the event loop) whenever the watched folder changes."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._cb = callback
        self._fd: int | None = None
        self._wd = -1
        self._libc = None
        self._loop = None
        self.active = False  # True while the current folder is being watched

    def start(self, loop) -> None:
        try:
            import ctypes
            import ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
            libc.inotify_init1.argtypes = [ctypes.c_int]
            libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
            fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        except (OSError, AttributeError, ImportError):
            return
        if fd < 0:
            return
        self._libc, self._fd, self._loop = libc, fd, loop
        loop.add_reader(fd, self._ready)

    def watch(self, path) -> None:
        self.active = False
        if self._fd is None:
            return
        if self._wd >= 0:
            self._libc.inotify_rm_watch(self._fd, self._wd)
            self._wd = -1
        wd = self._libc.inotify_add_watch(self._fd, os.fsencode(path), MASK)
        if wd >= 0:
            self._wd = wd
            self.active = True

    def _ready(self) -> None:
        try:
            while os.read(self._fd, 65536):
                pass
        except OSError:
            pass
        self._cb()

    def stop(self) -> None:
        if self._fd is not None:
            try:
                self._loop.remove_reader(self._fd)
                os.close(self._fd)
            except (OSError, ValueError):
                pass
            self._fd = None
        self.active = False
