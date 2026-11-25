#!/usr/bin/env bash
# Install (or upgrade) rind. Works from a clone of the repo or straight from the web:
#
#   curl -fsSL https://raw.githubusercontent.com/elvishcraftsman/rind/main/install.sh | bash
#
# Environment:  RIND_REF=<tag|branch>  install that instead of the newest release (default) or main
#               RIND_NO_IMAGES=1       skip Pillow (image previews)
# Options:      --uninstall            remove rind (and its venv, if the script made one)
set -euo pipefail

REPO="elvishcraftsman/rind"
REPO_URL="https://github.com/$REPO"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/rind/venv"
OLD_VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/rind-venv"   # where earlier versions of this script put it

say()  { printf '==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

TMP=""
trap '[ -n "$TMP" ] && rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------- uninstall
if [ "${1:-}" = "--uninstall" ]; then
  have pipx && pipx uninstall rind 2>/dev/null && say "Removed the pipx install" || true
  have uv   && uv tool uninstall rind 2>/dev/null && say "Removed the uv install" || true
  for d in "$VENV_DIR" "$OLD_VENV_DIR"; do
    [ -d "$d" ] && rm -rf "$d" && say "Removed $d"
  done
  if [ -L "$BIN_DIR/rind" ]; then
    case "$(readlink "$BIN_DIR/rind")" in *rind*venv*) rm -f "$BIN_DIR/rind"; say "Removed $BIN_DIR/rind";; esac
  fi
  note "Your settings in ~/.config/rind are left alone."
  exit 0
fi

# ---------------------------------------------------------------- Python >= 3.10
find_python() {
  local c v
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if have "$c"; then
      v="$("$c" -c 'import sys; print(sys.version_info >= (3, 10))' 2>/dev/null || true)"
      [ "$v" = "True" ] && { echo "$c"; return 0; }
    fi
  done
  return 1
}

PYTHON=""
if ! have uv; then          # uv fetches its own Python when needed; everything else needs one here
  if ! PYTHON="$(find_python)"; then
    if [ -e /run/ostree-booted ]; then
      die "Python 3.10 or newer was not found. On an immutable system run this inside a toolbox/distrobox, or install uv (https://docs.astral.sh/uv/)."
    fi
    die "Python 3.10 or newer is required (found: $(python3 --version 2>&1 || echo none)). Install it with your package manager (apt install python3 python3-venv, dnf install python3, pacman -S python, zypper install python3) and run this again."
  fi
fi

# ---------------------------------------------------------------- get the source
SRC=""
SELF_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/pyproject.toml" ] && grep -q '^name = "rind"' "$SELF_DIR/pyproject.toml"; then
  SRC="$SELF_DIR"
  say "Installing from this folder ($SRC)"
else
  TMP="$(mktemp -d)"
  REF="${RIND_REF:-}"
  if [ -z "$REF" ] && have git; then
    # newest v1.2.3-style tag, if the project has published any
    REF="$(git ls-remote --tags --refs --sort=-v:refname "$REPO_URL.git" 'v*' 2>/dev/null | head -n1 | sed 's#.*refs/tags/##' || true)"
  fi
  REF="${REF:-main}"
  say "Fetching rind ($REF)"
  if have git; then
    git clone --quiet --depth 1 --branch "$REF" "$REPO_URL.git" "$TMP/rind" \
      || die "could not clone $REPO_URL (ref: $REF). Check your connection, or set RIND_REF."
    SRC="$TMP/rind"
  elif have curl || have wget; then
    mkdir -p "$TMP/rind"
    URL="https://codeload.github.com/$REPO/tar.gz/$REF"
    if have curl; then curl -fsSL "$URL" | tar -xz -C "$TMP/rind" --strip-components=1
    else wget -qO- "$URL" | tar -xz -C "$TMP/rind" --strip-components=1; fi \
      || die "could not download $URL"
    SRC="$TMP/rind"
  else
    die "need git, curl or wget to download rind"
  fi
fi

done_msg() {
  say "rind is installed: $*"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) note "Add $BIN_DIR to your PATH so the 'rind' command is found:"
       note "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc" ;;
  esac
  note "Run:  rind        Update later:  rind --update        Check your setup:  rind --doctor"
  note "To make quitting rind cd your shell:  eval \"\$(rind --init bash)\"  (see the README)"
}

IMAGES=1; [ -n "${RIND_NO_IMAGES:-}" ] && IMAGES=0

# ---------------------------------------------------------------- 1. uv
if have uv; then
  say "Installing with uv"
  if { [ $IMAGES = 1 ] && uv tool install --force --reinstall --with pillow "$SRC"; } || uv tool install --force --reinstall "$SRC"; then
    done_msg "uv tool"; exit 0
  fi
  note "uv could not install it; trying the next method"
fi

# ---------------------------------------------------------------- 2. pipx
if have pipx; then
  say "Installing with pipx"
  if pipx install --force "$SRC"; then
    [ $IMAGES = 1 ] && { pipx inject rind pillow >/dev/null 2>&1 || note "Pillow could not be added; image previews are off"; }
    done_msg "pipx"; exit 0
  fi
  note "pipx could not install it; trying the next method"
fi

# ---------------------------------------------------------------- 3. a private virtual environment
[ -n "$PYTHON" ] || PYTHON="$(find_python)" || die "Python 3.10 or newer is required"
say "Installing into a private virtual environment ($VENV_DIR)"
rm -rf "$VENV_DIR"; [ -d "$OLD_VENV_DIR" ] && rm -rf "$OLD_VENV_DIR"
mkdir -p "$(dirname "$VENV_DIR")" "$BIN_DIR"
"$PYTHON" -m venv "$VENV_DIR" || { rm -rf "$VENV_DIR"; die "could not create a virtual environment. On Debian/Ubuntu: sudo apt install python3-venv"; }
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if ! "$VENV_DIR/bin/python" -m pip install "$SRC"; then
  rm -rf "$VENV_DIR"; die "pip could not install rind (see the messages above)"
fi
[ $IMAGES = 1 ] && { "$VENV_DIR/bin/python" -m pip install --quiet pillow >/dev/null 2>&1 || note "Pillow could not be added; image previews are off"; }
ln -sf "$VENV_DIR/bin/rind" "$BIN_DIR/rind"
done_msg "$BIN_DIR/rind"
