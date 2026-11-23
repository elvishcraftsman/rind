"""Paths, config loading and small JSON state files."""
from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore


def _xdg(env: str, default: Path) -> Path:
    return Path(os.environ.get(env) or default)


HOME = Path.home()
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", HOME / ".config") / "rind"
STATE_DIR = _xdg("XDG_STATE_HOME", HOME / ".local" / "state") / "rind"
CONFIG_FILE = CONFIG_DIR / "config.toml"
VISITS_FILE = STATE_DIR / "visits.json"
UPDATE_FILE = STATE_DIR / "update.json"


def load_config() -> tuple[dict, str | None]:
    if not CONFIG_FILE.exists():
        return {}, None
    if tomllib is None:
        return {}, "Config ignored: install 'tomli' (Python < 3.11)"
    try:
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f), None
    except Exception as e:  # noqa: BLE001
        return {}, f"Config error: {e}"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except OSError:
        pass
