#!/bin/bash
# Launch Buffalo Review Drop (standalone Mac app).
# Uses Shotgun/Flow Desktop's bundled Python for PySide6 + sgtk.

APP_DIR="/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/alts/BUF_Mac_watcher/drop_app"
SG_PYTHON="/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3"
CONFIG_CORE="/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/current/install/core/python"

if [ ! -x "$SG_PYTHON" ]; then
  echo "ERROR: Shotgun/Flow Desktop Python not found at:"
  echo "  $SG_PYTHON"
  echo "Install/launch ShotGrid Desktop once, then retry."
  read -r _
  exit 1
fi

export PYTHONPATH="$CONFIG_CORE${PYTHONPATH:+:$PYTHONPATH}"
cd "$APP_DIR" || exit 1
exec "$SG_PYTHON" "$APP_DIR/review_drop_app.py"
