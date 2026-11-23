"""Build the content of the preview pane for a file or folder."""
from __future__ import annotations

import grp
import mimetypes
import os
import pwd
import stat
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

from rich.console import Group, RenderableType
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text

from .fileops import human, natural_key, trash_origin

MAX_BYTES = 64 * 1024
MAX_LINES = 300
MAX_LIST = 200
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tif", ".tiff"}
TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")

# Replace control characters so file contents can never drive the terminal.
_CTRL = {i: "·" for i in list(range(0, 9)) + list(range(11, 32)) + list(range(127, 160))}
_CTRL[13] = None


def sanitize(s: str) -> str:
    return s.translate(_CTRL)


def _meta(path: Path, lst: os.stat_result, st: os.stat_result) -> Text:
    t = Text()

    def row(label: str, value: str) -> None:
        t.append(f"{label:<9}", style="dim")
        t.append(sanitize(value) + "\n")

    if stat.S_ISDIR(st.st_mode):
        row("Type", "Folder")
    else:
        row("Type", mimetypes.guess_type(path.name)[0] or "unknown")
        row("Size", f"{human(st.st_size)} ({st.st_size:,} bytes)")
    row("Modified", datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    row("Mode", f"{stat.filemode(st.st_mode)}  {owner}:{group}")
    if stat.S_ISLNK(lst.st_mode):
        row("Link to", os.readlink(path))
    orig = trash_origin(path)
    if orig is not None:
        row("Was in", str(orig.parent))
    t.rstrip()
    return t


def _folder(path: Path) -> RenderableType:
    try:
        with os.scandir(path) as it:
            items = sorted(((e.name, e.is_dir()) for e in it),
                           key=lambda x: (not x[1], natural_key(x[0])))
    except OSError as e:
        return Text(f"Cannot read folder: {e.strerror}", style="red")
    out = Text(f"{len(items)} items\n", style="dim")
    for name, is_dir in items[:MAX_LIST]:
        out.append(sanitize(name) + ("/" if is_dir else "") + "\n", style="bold blue" if is_dir else "")
    if len(items) > MAX_LIST:
        out.append(f"… {len(items) - MAX_LIST} more", style="dim")
    out.rstrip()
    return out


def _archive(path: Path) -> RenderableType | None:
    names: list[str] = []
    more = False
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                all_names = z.namelist()
            names, more = all_names[:MAX_LIST], len(all_names) > MAX_LIST
            header = f"Zip archive, {len(all_names)} entries\n"
        elif path.name.lower().endswith(TAR_SUFFIXES) and path.stat().st_size < (1 << 30):
            with tarfile.open(path) as tf:
                for i, m in enumerate(tf):
                    if i >= MAX_LIST:
                        more = True
                        break
                    names.append(m.name)
            header = "Tar archive\n"
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    out = Text(header, style="dim")
    for n in names:
        out.append(sanitize(n) + "\n")
    if more:
        out.append(f"… more entries", style="dim")
    out.rstrip()
    return out


def _image(path: Path, cols: int, rows: int) -> RenderableType:
    try:
        from PIL import Image
    except ImportError:
        return Text("Install Pillow for image previews:\n  pip install 'rind[images]'", style="dim")
    try:
        with Image.open(path) as im:
            w, h = im.size
            mw, mh = max(8, cols - 2), max(8, rows)
            try:
                im.draft("RGB", (mw, mh * 2))
            except Exception:  # noqa: BLE001
                pass
            im = im.convert("RGBA")
        im.thumbnail((mw, mh * 2))
        from PIL import Image as _I
        im = _I.alpha_composite(_I.new("RGBA", im.size, (0, 0, 0, 255)), im)
    except Exception as e:  # noqa: BLE001
        return Text(f"Cannot decode image: {e}", style="red")
    px = im.load()
    W, H = im.size
    lines = [Text(f"{w}×{h} pixels", style="dim")]
    for y in range(0, H, 2):
        line = Text(no_wrap=True)
        for x in range(W):
            r1, g1, b1, _ = px[x, y]
            r2, g2, b2, _ = px[x, y + 1] if y + 1 < H else (0, 0, 0, 255)
            line.append("▀", style=Style(color=f"rgb({r1},{g1},{b1})", bgcolor=f"rgb({r2},{g2},{b2})"))
        lines.append(line)
    return Group(*lines)


def _hexdump(data: bytes, cols: int) -> Text:
    per = 8 if cols < 70 else 16
    out = []
    for i in range(0, len(data), per):
        chunk = data[i:i + per]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"{i:08x}  {hexs:<{per * 3 - 1}}  {asc}")
    return Text("\n".join(out))


def _file_body(path: Path, st: os.stat_result, cols: int, rows: int) -> RenderableType:
    if path.suffix.lower() in IMAGE_EXTS:
        return _image(path, cols, rows)
    arch = _archive(path)
    if arch is not None:
        return arch
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES)
    except OSError as e:
        return Text(f"Cannot read file: {e.strerror}", style="red")
    if not data:
        return Text("(empty file)", style="dim")
    if b"\0" in data[:8192]:
        return Group(Text("Binary file, first bytes:", style="dim"), _hexdump(data[:256], cols))
    lines = data.decode("utf-8", errors="replace").splitlines()
    clipped = len(lines) > MAX_LINES or st.st_size > len(data)
    code = sanitize("\n".join(lines[:MAX_LINES]))
    try:
        body: RenderableType = Syntax(code, Syntax.guess_lexer(str(path), code=code), theme="ansi_dark",
                                      line_numbers=False, word_wrap=False, background_color="default")
    except Exception:  # noqa: BLE001
        body = Text(code)
    if clipped:
        return Group(body, Text("… preview truncated", style="dim"))
    return body


def build_preview(path: Path, cols: int, rows: int) -> RenderableType:
    try:
        lst = os.lstat(path)
    except OSError as e:
        return Text(f"Cannot read: {e.strerror}", style="red")
    parts: list[RenderableType] = [Text(sanitize(path.name), style="bold underline")]
    try:
        st = os.stat(path)
    except OSError:
        parts.append(Text(f"Broken link → {os.readlink(path)}", style="red"))
        return Group(*parts)
    parts.append(_meta(path, lst, st))
    parts.append(Text(""))
    if stat.S_ISDIR(st.st_mode):
        parts.append(_folder(path))
    elif stat.S_ISREG(st.st_mode):
        parts.append(_file_body(path, st, cols, rows))
    else:
        parts.append(Text("Special file (device, socket or pipe)", style="dim"))
    return Group(*parts)
