#!/bin/bash
# Launch Buffalo Review Drop (standalone Mac app).
# Uses Shotgun/Flow Desktop's bundled Python for PySide6 + sgtk.
#
# Always runs the checkout that contains this script (so a git pull of the
# feature branch is what you actually launch).

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SG_PYTHON="/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3"
VOLUME_ROOT="/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow"
CONFIG_CORE="${VOLUME_ROOT}/current/install/core/python"

if [ ! -x "$SG_PYTHON" ]; then
  echo "ERROR: Shotgun/Flow Desktop Python not found at:"
  echo "  $SG_PYTHON"
  echo "Install/launch ShotGrid Desktop once, then retry."
  read -r _
  exit 1
fi

# Finder / Shotgun launches strip Homebrew from PATH. Preview needs
# oiiotool + ffmpeg for EXR; put the usual prefixes back before exec.
if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/ffmpeg-full/bin:/usr/local/bin:${PATH}"

export PYTHONPATH="$CONFIG_CORE${PYTHONPATH:+:$PYTHONPATH}"
cd "$APP_DIR" || exit 1

# Make it obvious which checkout / commit is running (black preview was often
# just a stale tree that never got onto the feature branch).
if command -v git >/dev/null 2>&1; then
  REPO_ROOT="$(cd "$APP_DIR/.." && pwd)"
  BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
  echo "[review_drop] $APP_DIR"
  echo "[review_drop] branch=$BRANCH commit=$COMMIT"
fi
echo "[review_drop] oiiotool=$(command -v oiiotool 2>/dev/null || echo NOT FOUND)"
echo "[review_drop] ffmpeg=$(command -v ffmpeg 2>/dev/null || echo NOT FOUND)"

exec "$SG_PYTHON" "$APP_DIR/review_drop_app.py"
