"""Self-update: check a release source and install the new version.

Sources (config [update] source, or --update-source):
  github:owner/repo   the repo's latest published release (also https://github.com/owner/repo);
                      the release's .whl file is used when it has one, else the tag's tarball
  https://.../latest.json   a manifest: {"version": "1.2.3", "url": "...", "sha256": "..."}
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__
from .config import UPDATE_FILE, read_json, write_json

DEFAULT_SOURCE = "github:elvishcraftsman/rind"


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    url: str
    sha256: Optional[str] = None
    notes: str = ""


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _check_url(url: str) -> None:
    p = urllib.parse.urlparse(url)
    if p.scheme == "https" or (p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1")):
        return
    raise UpdateError("update URLs must use https://")


def _get(url: str, limit: int) -> bytes:
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": f"rind/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"HTTP {e.code} from {urllib.parse.urlparse(url).netloc}") from None
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"network error: {getattr(e, 'reason', e)}") from None
    if len(data) > limit:
        raise UpdateError("response too large")
    return data


def normalize_source(source: str) -> str:
    """https://github.com/owner/repo(.git)(/...) -> github:owner/repo; anything else unchanged."""
    source = source.strip()
    m = re.fullmatch(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?(?:/(?:releases|tree|blob).*)?", source)
    return f"github:{m.group(1)}/{m.group(2)}" if m else source


def _asset_release(data: dict, tag: str, notes: str) -> Optional[Release]:
    """A release asset named rind-*.whl (preferred over the source tarball), with its checksum
    from a SHA256SUMS asset when the release has one."""
    assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
    wheel = next((a for a in assets if str(a.get("name", "")).startswith("rind-")
                  and str(a.get("name", "")).endswith(".whl") and a.get("browser_download_url")), None)
    if wheel is None:
        return None
    sha = None
    sums = next((a for a in assets if a.get("name") == "SHA256SUMS" and a.get("browser_download_url")), None)
    if sums is not None:
        try:
            for line in _get(str(sums["browser_download_url"]), 1 << 16).decode().splitlines():
                digest, _, name = line.strip().partition(" ")
                if name.strip().lstrip("*") == wheel["name"] and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    sha = digest
        except (UpdateError, UnicodeDecodeError):
            pass
    return Release(tag.lstrip("vV"), str(wheel["browser_download_url"]), sha, notes)


def latest_release(source: str) -> Release:
    source = normalize_source(source)
    if source.startswith("github:"):
        repo = source[7:].strip("/")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise UpdateError("github source must look like github:owner/repo")
        try:
            data = json.loads(_get(f"https://api.github.com/repos/{repo}/releases/latest", 1 << 20))
        except UpdateError as e:
            raise UpdateError(f"{e} (has {repo} published a release?)") from None
        except ValueError:
            raise UpdateError("unexpected reply from GitHub") from None
        tag = str(data.get("tag_name") or "")
        if not tag:
            raise UpdateError("latest release has no tag")
        notes = str(data.get("body") or "")[:2000]
        rel = _asset_release(data, tag, notes)
        if rel is not None:
            return rel
        url = f"https://github.com/{repo}/archive/refs/tags/{urllib.parse.quote(tag)}.tar.gz"
        return Release(tag.lstrip("vV"), url, None, notes)
    try:
        data = json.loads(_get(source, 1 << 20))
        return Release(str(data["version"]), urllib.parse.urljoin(source, str(data["url"])),
                       data.get("sha256"), str(data.get("notes") or "")[:2000])
    except (ValueError, KeyError, TypeError):
        raise UpdateError("update manifest must contain \"version\" and \"url\"") from None


def install_kind() -> str:
    """How rind was installed: pipx, uv (tool), pip (any other environment) or source."""
    here = str(Path(__file__).resolve())
    prefix = Path(sys.prefix)
    if (prefix / "pipx_metadata.json").exists() or "/pipx/venvs/" in here:
        return "pipx"
    if (prefix / "uv-receipt.toml").exists() or "/uv/tools/" in here:
        return "uv"
    if "site-packages" in here or "dist-packages" in here:
        return "pip"
    return "source"


def _download(rel: Release) -> Path:
    data = _get(rel.url, 50 << 20)
    if rel.sha256 and hashlib.sha256(data).hexdigest() != rel.sha256.lower():
        raise UpdateError("checksum mismatch, refusing to install")
    name = urllib.parse.urlparse(rel.url).path.rsplit("/", 1)[-1] or "rind.tar.gz"
    d = Path(tempfile.mkdtemp(prefix="rind-update-"))
    f = d / name
    f.write_bytes(data)
    return f


def apply(rel: Release) -> None:
    kind = install_kind()
    if kind == "source":
        raise UpdateError("running from a source folder; update it with git pull or reinstall")
    tool = {"pipx": "pipx", "uv": "uv"}.get(kind)
    if tool and not shutil.which(tool):
        raise UpdateError(f"{tool} not found on PATH")
    f = _download(rel)
    try:
        images = importlib.util.find_spec("PIL") is not None  # keep image preview if it was installed
        if kind == "pipx":
            cmd = ["pipx", "install", "--force", str(f)]
        elif kind == "uv":
            cmd = ["uv", "tool", "install", "--force"] + (["--with", "pillow"] if images else []) + [str(f)]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", str(f)] + (["pillow"] if images else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise UpdateError("installer failed:\n" + (r.stderr or r.stdout).strip()[-1200:])
        if kind == "pipx" and images:
            subprocess.run(["pipx", "inject", "rind", "pillow"], capture_output=True, text=True)
    finally:
        shutil.rmtree(f.parent, ignore_errors=True)


def record_check(latest: str) -> None:
    write_json(UPDATE_FILE, {"checked": time.time(), "latest": latest})


def due(interval_hours: float) -> bool:
    last = read_json(UPDATE_FILE, {}).get("checked", 0)
    return time.time() - float(last) > interval_hours * 3600
