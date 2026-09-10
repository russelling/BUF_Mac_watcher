#!/bin/bash
# Launch the QT Watcher monitor panel.
#
# Uses Flow/Shotgun Desktop's bundled Python for PySide6, the same
# interpreter drop_app uses - nothing extra to install.
#
# Double-click in Finder, or run from a terminal.

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SG_PYTHON="/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3"

pause_exit() {
  echo ""
  echo "Press Return to close..."
  read -r _
  exit 1
}

if [ ! -x "$SG_PYTHON" ]; then
  echo "ERROR: Flow/Shotgun Desktop Python not found at:"
  echo "  $SG_PYTHON"
  echo "Install and launch Flow Desktop once, then retry."
  pause_exit
fi

# The service controls and log tail need nothing but launchctl. The RE-RUN QT
# button needs Toolkit, which arrives on PYTHONPATH exactly the way
# install_qt_watcher.sh gives it to the watcher itself - same interpreter, same
# tk-core, so the panel and the daemon can never disagree about the config.
#
# Missing tk-core is not fatal: the panel still starts, and only that one
# button reports the problem.
STORAGE_ROOT="${STORAGE_ROOT:-/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx}"
CONFIG_ROOT="${CONFIG_ROOT:-$STORAGE_ROOT/repo/pipeline/config/flow/current}"
TK_CORE_PY="$CONFIG_ROOT/install/core/python"

if [ -d "$TK_CORE_PY" ]; then
  export PYTHONPATH="$TK_CORE_PY${PYTHONPATH:+:$PYTHONPATH}"
else
  echo "NOTE: tk-core not found at $TK_CORE_PY"
  echo "      The panel will run, but RE-RUN QT will be unavailable."
fi

exec "$SG_PYTHON" "$APP_DIR/watcher_monitor.py"
