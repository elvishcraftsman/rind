"""Archives: safe extraction (zip, tar, tar.gz/bz2/xz), creation, and a browse cache.

Extraction never trusts the archive: absolute paths, '..' components, links that
resolve outside the destination, device files and oversized/exploding archives are
refused or skipped.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from .fileops import Cancelled, Job, human, is_real_dir, path_size, unique_dest

TAR_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar")
SUFFIXES = TAR_SUFFIXES + (".zip",)
MAX_BYTES = 10 << 30      # refuse to expand beyond this (zip-bomb guard)
MAX_FILES = 500_000
CHUNK = 1 << 20


class ArchiveError(Exception):
    pass


@dataclass
class ExtractResult:
    files: int = 0
    skipped: list[str] = field(default_factory=list)


def archive_kind(path) -> Optional[str]:
    """'zip', 'tar' or None, judged by file name."""
    n = str(path).lower()
    if n.endswith(".zip"):
        return "zip"
    if n.endswith(TAR_SUFFIXES):
        return "tar"
    return None


def archive_stem(path) -> str:
    name = Path(path).name
    low = name.lower()
    for suf in SUFFIXES:
        if low.endswith(suf):
            return name[: -len(suf)] or name
    return name


# ---------------------------------------------------------------- extraction

def _safe_rel(name: str) -> Optional[PurePosixPath]:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts:
        return None
    parts = [x for x in p.parts if x not in (".", "")]
    return PurePosixPath(*parts) if parts else None


class _Extractor:
    def __init__(self, dest: Path, job: Job, limit: int) -> None:
        self.dest = dest
        self.dest_real = os.path.realpath(dest)
        self.job, self.limit = job, limit
        self.written = 0
        self.res = ExtractResult()
        self.has_links = False

    def target(self, rel: PurePosixPath) -> Optional[Path]:
        t = self.dest / rel
        if self.has_links:  # symlinks exist: make sure we can't be steered outside
            real = os.path.realpath(t.parent)
            if real != self.dest_real and not real.startswith(self.dest_real + os.sep):
                return None
        return t

    def skip(self, name: str, why: str) -> None:
        self.res.skipped.append(f"{name} ({why})")

    def add_dir(self, rel: PurePosixPath, name: str) -> None:
        t = self.target(rel)
        if t is None:
            return self.skip(name, "unsafe path")
        t.mkdir(parents=True, exist_ok=True)

    def add_link(self, rel: PurePosixPath, name: str, link: str) -> None:
        t = self.target(rel)
        if t is None or not link or os.path.isabs(link):
            return self.skip(name, "unsafe link")
        resolved = os.path.realpath(os.path.join(os.path.dirname(t), link))
        if resolved != self.dest_real and not resolved.startswith(self.dest_real + os.sep):
            return self.skip(name, "link leaves the folder")
        t.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(t):
            os.unlink(t)
        os.symlink(link, t)
        self.has_links = True

    def add_file(self, rel: PurePosixPath, name: str, src, mode: int, mtime: Optional[float],
                 progress=None) -> None:
        t = self.target(rel)
        if t is None:
            return self.skip(name, "unsafe path")
        t.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(t) and (os.path.islink(t) or os.path.isdir(t)):
            return self.skip(name, "name clash")
        with open(t, "wb") as out:
            while True:
                self.job.check()
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                self.written += len(chunk)
                if self.written > self.limit:
                    raise ArchiveError(f"archive expands beyond the {human(self.limit)} safety limit")
                out.write(chunk)
                if progress:
                    progress()
                else:
                    self.job.advance(len(chunk))
        os.chmod(t, (mode & 0o777) | 0o600)
        if mtime:
            try:
                os.utime(t, (mtime, mtime))
            except OSError:
                pass
        self.res.files += 1


def _extract_zip(archive: Path, ex: _Extractor) -> None:
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise ArchiveError("archive has too many entries")
        total = sum(i.file_size for i in infos)
        if total > ex.limit:
            raise ArchiveError(f"archive would expand to {human(total)}, over the "
                               f"{human(ex.limit)} safety limit")
        ex.job.total = total
        for info in infos:
            ex.job.check()
            rel = _safe_rel(info.filename)
            if rel is None:
                ex.skip(info.filename, "unsafe path")
                continue
            mode = info.external_attr >> 16
            if info.is_dir():
                ex.add_dir(rel, info.filename)
            elif stat.S_ISLNK(mode):
                ex.add_link(rel, info.filename, z.read(info).decode("utf-8", errors="replace"))
            elif stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                ex.skip(info.filename, "special file")
            else:
                try:
                    ts = time.mktime(info.date_time + (0, 0, -1))
                except (OverflowError, ValueError):
                    ts = None
                with z.open(info) as src:
                    ex.add_file(rel, info.filename, src, mode or 0o644, ts)


def _extract_tar(archive: Path, ex: _Extractor) -> None:
    ex.job.total = archive.stat().st_size
    with open(archive, "rb") as raw, tarfile.open(fileobj=raw, mode="r|*") as tf:
        count = 0
        for m in tf:
            ex.job.check()
            ex.job.done = min(raw.tell(), ex.job.total)
            count += 1
            if count > MAX_FILES:
                raise ArchiveError("archive has too many entries")
            rel = _safe_rel(m.name)
            if rel is None:
                ex.skip(m.name, "unsafe path")
            elif m.isdir():
                ex.add_dir(rel, m.name)
            elif m.issym():
                ex.add_link(rel, m.name, m.linkname)
            elif m.isreg():
                src = tf.extractfile(m)
                if src is not None:
                    ex.add_file(rel, m.name, src, m.mode, m.mtime,
                                progress=lambda: setattr(ex.job, "done", min(raw.tell(), ex.job.total)))
            else:
                ex.skip(m.name, "hard link or special file")


def extract_archive(archive: Path, dest: Path, job: Job, limit: int = MAX_BYTES) -> ExtractResult:
    """Safely extract archive into dest (created if needed)."""
    kind = archive_kind(archive)
    if kind is None:
        raise ArchiveError("not a supported archive type")
    dest.mkdir(parents=True, exist_ok=True)
    ex = _Extractor(dest, job, limit)
    try:
        (_extract_zip if kind == "zip" else _extract_tar)(archive, ex)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, NotImplementedError, RuntimeError) as e:
        raise ArchiveError(f"can't read archive: {e}") from None
    return ex.res


def extract_here(archive: Path, parent: Path, job: Job, limit: int = MAX_BYTES) -> tuple[Path, list[str]]:
    """Extract next to the archive into a new folder named after it. If the archive has a
    single top-level entry, that entry is placed directly (no doubled folder)."""
    tmp = Path(tempfile.mkdtemp(prefix=".rind-extract-", dir=parent))
    try:
        skipped = extract_archive(archive, tmp, job, limit).skipped
        entries = list(os.scandir(tmp))
        if len(entries) == 1:
            src = Path(entries[0].path)
            final = unique_dest(parent / entries[0].name, is_real_dir(src))
            os.rename(src, final)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o777 & ~umask)
            final = unique_dest(parent / archive_stem(archive), True)
            os.rename(tmp, final)
            tmp = None  # type: ignore[assignment]
        return final, skipped
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- browse cache

def cache_root() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "rind" / "archives"


def open_cache(archive: Path, job: Job, limit: int = MAX_BYTES) -> Path:
    """Extract archive into the browse cache (once) and return the folder to browse."""
    st = os.stat(archive)
    key = hashlib.sha1(f"{os.path.realpath(archive)}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]
    base = cache_root() / key
    content = base / "content"
    if (base / ".complete").exists():
        return content
    shutil.rmtree(base, ignore_errors=True)
    try:
        extract_archive(archive, content, job, limit)
        (base / ".complete").touch()
    except BaseException:
        shutil.rmtree(base, ignore_errors=True)
        raise
    return content


def clean_cache(max_age: Optional[float] = None) -> None:
    root = cache_root()
    if not root.is_dir():
        return
    for e in os.scandir(root):
        try:
            if max_age is None or time.time() - e.stat().st_mtime > max_age:
                shutil.rmtree(e.path, ignore_errors=True)
        except OSError:
            pass


# ---------------------------------------------------------------- creation

class _Reader:
    """File wrapper that reports progress and honours cancellation while being archived."""

    def __init__(self, f, job: Job) -> None:
        self.f, self.job = f, job

    def read(self, n: int = -1) -> bytes:
        self.job.check()
        b = self.f.read(n)
        self.job.advance(len(b))
        return b


def _walk(paths: list[Path]):
    """Yield (path, arcname) for every item to store, parents before children."""
    for top in paths:
        yield top, top.name
        if is_real_dir(top):
            for dp, dns, fns in os.walk(top, followlinks=False):
                for n in sorted(dns) + sorted(fns):
                    p = Path(dp) / n
                    yield p, str(PurePosixPath(top.name) / p.relative_to(top).as_posix())


def compress(paths: list[Path], dest: Path, job: Job) -> Path:
    """Create an archive (type from dest's name) containing paths. Returns the final path."""
    kind = archive_kind(dest)
    if kind is None:
        raise ArchiveError("unknown archive type")
    job.total = sum(path_size(p, job) for p in paths)
    final = unique_dest(dest, False)
    tmp = final.with_name(f".rind-pack-{os.getpid()}.part")
    skip = {os.path.realpath(tmp), os.path.realpath(final)}
    try:
        items = [(p, a) for p, a in _walk(paths) if os.path.realpath(p) not in skip]
        if kind == "zip":
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for p, arc in items:
                    job.check()
                    job.current = p.name
                    st = os.lstat(p)
                    if stat.S_ISLNK(st.st_mode):
                        zi = zipfile.ZipInfo(arc, time.localtime(st.st_mtime)[:6])
                        zi.create_system = 3
                        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
                        zf.writestr(zi, os.readlink(p))
                    elif stat.S_ISDIR(st.st_mode):
                        zf.writestr(zipfile.ZipInfo.from_file(p, arc), b"")
                    elif stat.S_ISREG(st.st_mode):
                        zi = zipfile.ZipInfo.from_file(p, arc)
                        zi.compress_type = zipfile.ZIP_DEFLATED
                        with open(p, "rb") as f, zf.open(zi, "w", force_zip64=st.st_size > (1 << 31)) as out:
                            while True:
                                job.check()
                                chunk = f.read(CHUNK)
                                if not chunk:
                                    break
                                out.write(chunk)
                                job.advance(len(chunk))
        else:
            low = final.name.lower()
            mode = ("w:gz" if low.endswith((".tar.gz", ".tgz")) else
                    "w:bz2" if low.endswith((".tar.bz2", ".tbz2")) else
                    "w:xz" if low.endswith((".tar.xz", ".txz")) else "w")
            with tarfile.open(tmp, mode) as tf:
                for p, arc in items:
                    job.check()
                    job.current = p.name
                    ti = tf.gettarinfo(str(p), arc)
                    if ti.isreg():
                        with open(p, "rb") as f:
                            tf.addfile(ti, _Reader(f, job))
                    elif ti.isdir() or ti.issym():
                        tf.addfile(ti)
        os.replace(tmp, final)
        return final
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
