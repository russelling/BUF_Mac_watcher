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
BREW_FORMULAE="openimageio ffmpeg"

pause_exit() {
  echo ""
  echo "Press Return to close…"
  read -r _
  exit 1
}

if [ ! -x "$SG_PYTHON" ]; then
  echo "ERROR: Shotgun/Flow Desktop Python not found at:"
  echo "  $SG_PYTHON"
  echo "Install/launch ShotGrid Desktop once, then retry."
  pause_exit
fi

# Finder / Shotgun launches strip Homebrew from PATH. Preview needs
# oiiotool + ffmpeg for EXR; put the usual prefixes back before exec.
BREW=""
if [ -x /opt/homebrew/bin/brew ]; then
  BREW=/opt/homebrew/bin/brew
elif [ -x /usr/local/bin/brew ]; then
  BREW=/usr/local/bin/brew
fi
if [ -n "$BREW" ]; then
  eval "$("$BREW" shellenv)"
fi
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/ffmpeg-full/bin:/usr/local/bin:${PATH}"

resolve_tool() {
  # $1 = command name; prints absolute path or empty.
  local name="$1"
  local path=""
  path="$(command -v "$name" 2>/dev/null || true)"
  if [ -n "$path" ] && [ -x "$path" ]; then
    echo "$path"
    return 0
  fi
  for candidate in \
    "/opt/homebrew/bin/$name" \
    "/opt/homebrew/opt/ffmpeg-full/bin/$name" \
    "/opt/homebrew/opt/ffmpeg/bin/$name" \
    "/opt/homebrew/opt/openimageio/bin/$name" \
    "/usr/local/bin/$name" \
    "/usr/bin/$name"
  do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

OIIOTOOL_BIN="$(resolve_tool oiiotool || true)"
FFMPEG_BIN="$(resolve_tool ffmpeg || true)"

if [ -z "$OIIOTOOL_BIN" ] || [ -z "$FFMPEG_BIN" ]; then
  echo "ERROR: Buffalo Review Drop prerequisites are missing on this Mac."
  echo ""
  echo "  Required for EXR / DPX / movie preview:"
  echo "    oiiotool  — $( [ -n "$OIIOTOOL_BIN" ] && echo "$OIIOTOOL_BIN" || echo "NOT FOUND" )"
  echo "    ffmpeg    — $( [ -n "$FFMPEG_BIN" ] && echo "$FFMPEG_BIN" || echo "NOT FOUND" )"
  echo ""
  echo "Install with Homebrew, then relaunch:"
  echo ""
  echo "  brew install $BREW_FORMULAE"
  echo ""
  if [ -z "$BREW" ]; then
    echo "Homebrew itself was not found. Install it from https://brew.sh first."
    pause_exit
  fi
  echo -n "Install now with brew? [y/N] "
  read -r ANSWER
  case "$ANSWER" in
    y|Y|yes|YES)
      echo "[review_drop] brew install $BREW_FORMULAE"
      if ! "$BREW" install $BREW_FORMULAE; then
        echo "ERROR: brew install failed. Fix the brew error above, then relaunch."
        pause_exit
      fi
      # Refresh PATH after install.
      eval "$("$BREW" shellenv)"
      export PATH="/opt/homebrew/bin:/opt/homebrew/opt/ffmpeg-full/bin:/usr/local/bin:${PATH}"
      OIIOTOOL_BIN="$(resolve_tool oiiotool || true)"
      FFMPEG_BIN="$(resolve_tool ffmpeg || true)"
      if [ -z "$OIIOTOOL_BIN" ] || [ -z "$FFMPEG_BIN" ]; then
        echo "ERROR: tools still not on PATH after brew install."
        echo "  oiiotool=$( [ -n "$OIIOTOOL_BIN" ] && echo "$OIIOTOOL_BIN" || echo NOT FOUND )"
        echo "  ffmpeg=$( [ -n "$FFMPEG_BIN" ] && echo "$FFMPEG_BIN" || echo NOT FOUND )"
        pause_exit
      fi
      ;;
    *)
      pause_exit
      ;;
  esac
fi

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
echo "[review_drop] oiiotool=$OIIOTOOL_BIN"
echo "[review_drop] ffmpeg=$FFMPEG_BIN"

exec "$SG_PYTHON" "$APP_DIR/review_drop_app.py"
