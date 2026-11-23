#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/elvishcraftsman/rind"
REPO_NAME="rind"
TEMP_DIR=""

cleanup() {
  if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
    rm -rf "$TEMP_DIR"
  fi
}
trap cleanup EXIT

echo "==> Preparing installation for $REPO_NAME..."

# Ensure git is installed to retrieve the repository
if ! command -v git &>/dev/null; then
  echo "--> git is required. Attempting installation via package manager..."
  if command -v apt-get &>/dev/null; then
    sudo apt-get update && sudo apt-get install -y git
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y git
  elif command -v pacman &>/dev/null; then
    sudo pacman -Sy --noconfirm git
  elif command -v zypper &>/dev/null; then
    sudo zypper install -y git
  else
    echo "Error: git is not installed and no supported package manager was found." >&2
    exit 1
  fi
fi

TEMP_DIR=$(mktemp -d)
echo "--> Cloning $REPO_URL..."
git clone "$REPO_URL.git" "$TEMP_DIR/$REPO_NAME"

# ==============================================================================
# 1. OPTION A: pipx or uv (recommended)
# ==============================================================================
if command -v uv &>/dev/null; then
  echo "--> 'uv' detected. Attempting Option A (uv tool install)..."
  if uv tool install --directory "$TEMP_DIR/$REPO_NAME" '.[images]'; then
    echo "==> Successfully installed $REPO_NAME via uv!"
    exit 0
  elif uv tool install --directory "$TEMP_DIR/$REPO_NAME" .; then
    echo "==> Successfully installed $REPO_NAME via uv (without image support)!"
    exit 0
  else
    echo "--> uv tool install failed. Trying next method..."
  fi
fi

if command -v pipx &>/dev/null; then
  echo "--> 'pipx' detected. Attempting Option A (pipx install)..."
  if pipx install "$TEMP_DIR/$REPO_NAME[images]"; then
    echo "==> Successfully installed $REPO_NAME via pipx!"
    exit 0
  elif pipx install "$TEMP_DIR/$REPO_NAME"; then
    echo "==> Successfully installed $REPO_NAME via pipx (without image support)!"
    exit 0
  else
    echo "--> pipx install failed. Trying next method..."
  fi
fi

# ==============================================================================
# 2. OPTION B: Plain virtual environment (system Python)
# ==============================================================================
if command -v python3 &>/dev/null; then
  echo "--> Python 3 detected. Attempting Option B (venv in ~/.local/share)..."
  
  VENV_DIR="$HOME/.local/share/rind-venv"
  BIN_DIR="$HOME/.local/bin"
  
  if python3 -m venv "$VENV_DIR" 2>/dev/null; then
    if "$VENV_DIR/bin/pip" install "$TEMP_DIR/$REPO_NAME[images]" 2>/dev/null || \
       "$VENV_DIR/bin/pip" install "$TEMP_DIR/$REPO_NAME" 2>/dev/null; then
      mkdir -p "$BIN_DIR"
      ln -sf "$VENV_DIR/bin/rind" "$BIN_DIR/rind"
      
      echo "==> Successfully installed $REPO_NAME to $BIN_DIR/rind!"
      if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        echo "Note: Make sure $BIN_DIR is added to your PATH."
      fi
      exit 0
    fi
  fi
  echo "--> Option B failed or python3-venv is missing. Moving to package manager installation..."
fi

# ==============================================================================
# 3. System Package Manager Setup (Pre-requisite for Options C & B Fallbacks)
# ==============================================================================
echo "--> Installing Python 3 and build dependencies via system package manager..."

if command -v apt-get &>/dev/null; then
  sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
elif command -v dnf &>/dev/null; then
  sudo dnf install -y python3 python3-pip
elif command -v pacman &>/dev/null; then
  sudo pacman -Sy --noconfirm python python-pip
elif command -v zypper &>/dev/null; then
  sudo zypper install -y python3 python3-pip
else
  echo "Error: No supported package manager (apt, dnf, pacman, zypper) found." >&2
  exit 1
fi

# ==============================================================================
# 3. OPTION C: Run from folder without installing
# ==============================================================================
echo "--> Attempting Option C (setting up local folder environment)..."

TARGET_DIR="$HOME/rind"
mkdir -p "$TARGET_DIR"
cp -r "$TEMP_DIR/$REPO_NAME/"* "$TARGET_DIR/"

if (cd "$TARGET_DIR" && python3 -m venv .venv && .venv/bin/pip install textual); then
  echo "==> Option C setup complete!"
  echo "To run $REPO_NAME, execute:"
  echo "  cd $TARGET_DIR && .venv/bin/python -m rind"
  exit 0
fi

echo "--> Option C failed to configure. Proceeding to fourth option (Option B fallback)..."

# ==============================================================================
# 4. OPTION B FALLBACK: Full Option B post package manager installation
# ==============================================================================
echo "--> Executing Option B fallback using freshly installed package manager Python..."

VENV_DIR="$HOME/.local/share/rind-venv"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$BIN_DIR"
python3 -m venv "$VENV_DIR"

if "$VENV_DIR/bin/pip" install "$TEMP_DIR/$REPO_NAME[images]"; then
  ln -sf "$VENV_DIR/bin/rind" "$BIN_DIR/rind"
  echo "==> Successfully installed $REPO_NAME with image support to $BIN_DIR/rind!"
elif "$VENV_DIR/bin/pip" install "$TEMP_DIR/$REPO_NAME"; then
  ln -sf "$VENV_DIR/bin/rind" "$BIN_DIR/rind"
  echo "==> Successfully installed $REPO_NAME (without image support) to $BIN_DIR/rind!"
else
  echo "Error: All installation options (A, B, C, and B-fallback) failed." >&2
  exit 1
fi

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo "Note: Make sure $BIN_DIR is added to your PATH."
fi