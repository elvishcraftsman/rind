"""File operations engine: trash (freedesktop spec), copy/move with conflict
handling, progress and cancellation. No UI code lives here."""
from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

CHUNK = 1024 * 1024


# ---------------------------------------------------------------- helpers

def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{int(size)}B" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return str(n)


def natural_key(s: str) -> list:
    """Case-insensitive natural order: file2 < file10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.casefold())]


def is_real_dir(p) -> bool:
    return os.path.isdir(p) and not os.path.islink(p)


def unique_dest(dest: Path, is_dir: bool) -> Path:
    if not os.path.lexists(dest):
        return dest
    stem, suffix = (dest.name, "") if is_dir else (dest.stem, dest.suffix)
    n = 1
    while True:
        tag = "copy" if n == 1 else f"copy {n}"
        cand = dest.with_name(f"{stem} ({tag}){suffix}")
        if not os.path.lexists(cand):
            return cand
        n += 1


def remove_path(p: Path) -> None:
    if is_real_dir(p):
        shutil.rmtree(p)
    else:
        os.unlink(p)


def path_size(p: Path, job: "Optional[Job]" = None) -> int:
    try:
        st = os.lstat(p)
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size if stat.S_ISREG(st.st_mode) else 0
    total = 0
    for dp, _dns, fns in os.walk(p, followlinks=False):
        if job:
            job.check()
        for fn in fns:
            try:
                s = os.lstat(os.path.join(dp, fn))
            except OSError:
                continue
            if stat.S_ISREG(s.st_mode):
                total += s.st_size
    return total


# ---------------------------------------------------------------- trash

def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def home_trash() -> Path:
    return data_home() / "Trash"


def home_trash_files() -> Path:
    return home_trash() / "files"


@dataclass
class TrashRecord:
    orig: Path
    trash: Path
    name: str

    @property
    def stored(self) -> Path:
        return self.trash / "files" / self.name

    @property
    def info(self) -> Path:
        return self.trash / "info" / f"{self.name}.trashinfo"


def _topdir(path: Path) -> Path:
    p = Path(os.path.abspath(path))
    while not os.path.ismount(p) and p != p.parent:
        p = p.parent
    return p


def _trash_dir_for(path: Path) -> Path:
    ht = home_trash()
    try:
        for sub in ("files", "info"):
            (ht / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.lstat(path).st_dev == os.stat(ht).st_dev:
            return ht
    except OSError:
        pass
    return _topdir(path.parent) / f".Trash-{os.getuid()}"


def trash_item(path) -> TrashRecord:
    """Move path to the trash (freedesktop.org Trash spec). Raises OSError."""
    path = Path(os.path.abspath(path))
    if not os.path.lexists(path):
        raise FileNotFoundError(errno.ENOENT, "no longer exists", str(path))
    if os.path.ismount(path):
        raise OSError(errno.EBUSY, "is a mount point", str(path))
    td = _trash_dir_for(path)
    files, info = td / "files", td / "info"
    for d in (files, info):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    stored_path = str(path) if td == home_trash() else os.path.relpath(path, td.parent)
    n = 1
    while True:
        name = path.name if n == 1 else f"{path.name}.{n}"
        if not os.path.lexists(files / name):
            try:
                fd = os.open(info / f"{name}.trashinfo", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                pass
        n += 1
    rec = TrashRecord(path, td, name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (
                urllib.parse.quote(stored_path, safe="/"),
                datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))
        os.rename(path, rec.stored)
    except BaseException:
        try:
            os.unlink(rec.info)
        except OSError:
            pass
        raise
    return rec


def trash_origin(stored: Path) -> Optional[Path]:
    """Original location of an item inside a trash 'files' folder."""
    if stored.parent.name != "files":
        return None
    td = stored.parent.parent
    try:
        for line in (td / "info" / f"{stored.name}.trashinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("Path="):
                p = Path(urllib.parse.unquote(line[5:]))
                return p if p.is_absolute() else td.parent / p
    except OSError:
        pass
    return None


def restore_from_trash(stored: Path, dest: Optional[Path] = None) -> Path:
    orig = dest or trash_origin(stored)
    if orig is None:
        raise OSError(errno.ENOENT, "original location unknown", str(stored))
    orig.parent.mkdir(parents=True, exist_ok=True)
    target = unique_dest(orig, is_real_dir(stored)) if os.path.lexists(orig) else orig
    shutil.move(str(stored), str(target))
    try:
        os.unlink(stored.parent.parent / "info" / f"{stored.name}.trashinfo")
    except OSError:
        pass
    return target


def restore_record(rec: TrashRecord) -> Path:
    return restore_from_trash(rec.stored, rec.orig)


def empty_trash() -> int:
    n = 0
    for sub in ("files", "info"):
        d = home_trash() / sub
        if not d.is_dir():
            continue
        for e in list(os.scandir(d)):
            try:
                remove_path(Path(e.path))
                n += sub == "files"
            except OSError:
                pass
    return n


# ---------------------------------------------------------------- jobs

class Cancelled(Exception):
    pass


class Job:
    """Progress + cancellation state shared between a worker thread and the UI."""

    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self.total = 0
        self.done = 0
        self.current = ""

    def cancel(self) -> None:
        self.cancel_event.set()

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def advance(self, n: int) -> None:
        self.done += n


class Resolver:
    """Decides what happens when a destination already exists."""

    def __init__(self, ask: Optional[Callable[[Path, Path], tuple[str, bool]]] = None,
                 sticky: Optional[str] = None) -> None:
        self.ask = ask
        self.sticky = sticky

    def decide(self, src: Path, dst: Path) -> str:
        if self.sticky:
            return self.sticky
        if self.ask is None:
            return "skip"
        action, apply_all = self.ask(src, dst)
        if action == "cancel":
            raise Cancelled()
        if apply_all:
            self.sticky = action
        return action


@dataclass
class OpResult:
    done: list[tuple[Path, Path]] = field(default_factory=list)  # (source, final destination)
    skipped: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    replaced: list[TrashRecord] = field(default_factory=list)
    irreversible: bool = False
    cancelled: bool = False


def _err(name: str, e: OSError) -> str:
    return f"{name}: {e.strerror or e}"


def _clear_for_replace(dst: Path, res: OpResult) -> None:
    try:
        res.replaced.append(trash_item(dst))
    except OSError:
        remove_path(dst)
        res.irreversible = True


def _copy_file(src: Path, dst: Path, job: Job) -> None:
    tmp = dst.with_name(f".rind-{os.getpid()}-{threading.get_ident()}.part")
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                job.check()
                chunk = fi.read(CHUNK)
                if not chunk:
                    break
                fo.write(chunk)
                job.advance(len(chunk))
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def copy_path(src: Path, dst: Path, job: Job, resolver: Resolver, res: OpResult) -> Optional[Path]:
    job.check()
    job.current = src.name
    mode = os.lstat(src).st_mode
    is_dir = stat.S_ISDIR(mode)
    merge = False
    if os.path.lexists(dst):
        if is_dir and is_real_dir(dst):
            merge = True
        else:
            action = resolver.decide(src, dst)
            if action == "skip":
                res.skipped.append(src)
                job.advance(path_size(src))
                return None
            if action == "both":
                dst = unique_dest(dst, is_dir)
            else:
                _clear_for_replace(dst, res)
    if stat.S_ISLNK(mode):
        os.symlink(os.readlink(src), dst)
        return dst
    if is_dir:
        if not merge:
            os.mkdir(dst)
        with os.scandir(src) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            try:
                copy_path(Path(e.path), dst / e.name, job, resolver, res)
            except OSError as err:
                res.errors.append(_err(e.name, err))
        if not merge:
            try:
                shutil.copystat(src, dst)
            except OSError:
                pass
        return dst
    if stat.S_ISREG(mode):
        _copy_file(src, dst, job)
        return dst
    res.errors.append(f"{src.name}: not a regular file, skipped")
    return None


def move_path(src: Path, dst: Path, job: Job, resolver: Resolver, res: OpResult,
              size: Optional[int] = None) -> Optional[Path]:
    job.check()
    job.current = src.name
    is_dir = stat.S_ISDIR(os.lstat(src).st_mode)
    if os.path.lexists(dst):
        if is_dir and is_real_dir(dst):
            with os.scandir(src) as it:
                entries = sorted(it, key=lambda e: e.name)
            for e in entries:
                try:
                    move_path(Path(e.path), dst / e.name, job, resolver, res)
                except OSError as err:
                    res.errors.append(_err(e.name, err))
            try:
                os.rmdir(src)
            except OSError:
                pass
            return dst
        action = resolver.decide(src, dst)
        if action == "skip":
            res.skipped.append(src)
            job.advance(size if size is not None else path_size(src))
            return None
        if action == "both":
            dst = unique_dest(dst, is_dir)
        else:
            _clear_for_replace(dst, res)
    sz = size if size is not None else path_size(src)
    try:
        os.rename(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        errs, skp = len(res.errors), len(res.skipped)
        out = copy_path(src, dst, job, resolver, res)
        if out is not None and len(res.errors) == errs and len(res.skipped) == skp:
            remove_path(src)
        return out
    job.advance(sz)
    return dst


def _run(pairs, job, resolver, fn, sizes=None) -> OpResult:
    res = OpResult()
    for src, dst in pairs:
        existed = os.path.lexists(dst)
        try:
            if sizes is not None:
                final = fn(src, dst, job, resolver, res, sizes.get(src))
            else:
                final = fn(src, dst, job, resolver, res)
            if final is not None:
                res.done.append((src, final))
        except Cancelled:
            res.cancelled = True
            if fn is copy_path and not existed and os.path.lexists(dst):
                try:
                    remove_path(dst)
                except OSError:
                    pass
            break
        except OSError as e:
            res.errors.append(_err(src.name, e))
    return res


def run_copy(pairs: list[tuple[Path, Path]], job: Job, resolver: Resolver) -> OpResult:
    try:
        job.total = sum(path_size(s, job) for s, _ in pairs)
    except Cancelled:
        return OpResult(cancelled=True)
    return _run(pairs, job, resolver, copy_path)


def run_move(pairs: list[tuple[Path, Path]], job: Job, resolver: Resolver) -> OpResult:
    try:
        sizes = {s: path_size(s, job) for s, _ in pairs}
    except Cancelled:
        return OpResult(cancelled=True)
    job.total = sum(sizes.values())
    return _run(pairs, job, resolver, move_path, sizes)


def run_delete(paths: list[Path], job: Job) -> OpResult:
    res = OpResult()
    for p in paths:
        try:
            job.check()
            job.current = p.name
            remove_path(p)
            res.done.append((p, p))
        except Cancelled:
            res.cancelled = True
            break
        except OSError as e:
            res.errors.append(_err(p.name, e))
    return res


# ---------------------------------------------------------------- bulk rename

def plan_renames(pairs: list[tuple[Path, str]]) -> list[tuple[Path, Path]]:
    """Validate (path, new_name) pairs; return the (old, new) changes. Raises ValueError."""
    olds = {p for p, _ in pairs}
    changes: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for p, name in pairs:
        if name == p.name:
            continue
        if not name or name in (".", "..") or "/" in name or "\0" in name or "\n" in name:
            raise ValueError(f"invalid name {name!r}")
        new = p.with_name(name)
        if new in seen:
            raise ValueError(f"two items would be called '{name}'")
        seen.add(new)
        if os.path.lexists(new) and new not in olds:
            raise ValueError(f"'{name}' already exists")
        changes.append((p, new))
    return changes


def apply_renames(changes: list[tuple[Path, Path]]) -> None:
    """Rename old->new for all pairs. Swaps and chains work (two-phase via temporary
    names). If anything fails, everything already done is rolled back."""
    olds = {o for o, _ in changes}
    two_phase = any(n in olds for _, n in changes)
    done: list[tuple[Path, Path]] = []  # (current, original) steps performed, for rollback
    try:
        if not two_phase:
            for old, new in changes:
                os.rename(old, new)
                done.append((new, old))
            return
        temps = []
        for i, (old, _new) in enumerate(changes):
            tmp = old.with_name(f".rind-rename-{os.getpid()}-{i}")
            os.rename(old, tmp)
            done.append((tmp, old))
            temps.append(tmp)
        for tmp, (_old, new) in zip(temps, changes):
            os.rename(tmp, new)
            done[done.index((tmp, _old))] = (new, _old)
    except OSError:
        for cur, orig in reversed(done):
            try:
                os.rename(cur, orig)
            except OSError:
                pass
        raise


_COUNTER = re.compile(r"\{n(?::0?(\d+))?\}")


def pattern_names(paths: list[Path], spec: str) -> list[str]:
    """Apply 'find=>replace' (regex; \\1 backrefs; {n} / {n:03} running counter) to names."""
    if "=>" not in spec:
        raise ValueError("use the form  find=>replace")
    find, repl = spec.split("=>", 1)
    try:
        rx = re.compile(find)
    except re.error as e:
        raise ValueError(f"bad pattern: {e}") from None
    repl = re.sub(r"\\(\d)", r"\\g<\1>", repl)
    out = []
    for i, p in enumerate(paths, 1):
        r = _COUNTER.sub(lambda m: str(i).zfill(int(m.group(1) or 0)), repl)
        try:
            out.append(rx.sub(r, p.name))
        except (re.error, IndexError) as e:
            raise ValueError(f"bad replacement: {e}") from None
    return out


# ---------------------------------------------------------------- permissions

def apply_mode(path: Path, mode: int, recursive: bool = False) -> list[tuple[Path, int]]:
    """chmod path to `mode`. With recursive=True the contents change too: folders get `mode`,
    files get `mode` without execute bits unless they were already executable. Symlinks are
    left alone. Returns [(path, old_mode)] for undo; on failure everything is rolled back."""
    changed: list[tuple[Path, int]] = []

    def one(p: Path, new: int) -> None:
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode):
            return
        old = stat.S_IMODE(st.st_mode)
        if old != new:
            os.chmod(p, new)
            changed.append((p, old))

    try:
        if recursive and is_real_dir(path):
            for dp, dns, fns in os.walk(path, topdown=False, followlinks=False):
                for n in fns:  # children first, so a folder never locks us out of its contents
                    f = Path(dp) / n
                    try:
                        st = os.lstat(f)
                    except OSError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        was_exec = bool(stat.S_IMODE(st.st_mode) & 0o111)
                        one(f, (mode & 0o777) if was_exec else mode & 0o666)
                for n in dns:
                    one(Path(dp) / n, mode & 0o777)
        one(path, mode)
    except OSError:
        for p, old in reversed(changed):
            try:
                os.chmod(p, old)
            except OSError:
                pass
        raise
    return changed
