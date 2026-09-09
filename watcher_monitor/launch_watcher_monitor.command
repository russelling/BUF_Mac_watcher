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

# No sgtk needed - this panel only talks to launchctl and reads the log.
exec "$SG_PYTHON" "$APP_DIR/watcher_monitor.py"
