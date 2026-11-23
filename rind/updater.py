"""Self-update: check a release source and install the new version.

Sources (config [update] source, or --update-source):
  github:owner/repo   the repo's latest published release (tarball of its tag)
  https://.../latest.json   a manifest: {"version": "1.2.3", "url": "...", "sha256": "..."}
"""
from __future__ import annotations

import hashlib
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

# Set this to your repository (e.g. "github:youruser/rind") to enable updates by default.
DEFAULT_SOURCE = ""


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


def latest_release(source: str) -> Release:
    source = source.strip()
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
        url = f"https://github.com/{repo}/archive/refs/tags/{urllib.parse.quote(tag)}.tar.gz"
        return Release(tag.lstrip("vV"), url, None, str(data.get("body") or "")[:2000])
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
        if kind == "pipx":
            cmd = ["pipx", "install", "--force", str(f)]
        elif kind == "uv":
            cmd = ["uv", "tool", "install", "--force", str(f)]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", str(f)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise UpdateError("installer failed:\n" + (r.stderr or r.stdout).strip()[-1200:])
    finally:
        shutil.rmtree(f.parent, ignore_errors=True)


def record_check(latest: str) -> None:
    write_json(UPDATE_FILE, {"checked": time.time(), "latest": latest})


def due(interval_hours: float) -> bool:
    last = read_json(UPDATE_FILE, {}).get("checked", 0)
    return time.time() - float(last) > interval_hours * 3600
