#!/usr/bin/env bash
#
# make_monitor_app.sh — build a double-clickable "QT Watcher Monitor.app".
#
# The monitor panel already has launch_watcher_monitor.command beside it, but a
# .command opens a Terminal window, carries no icon, and looks like a script.
# This wraps it in an application bundle with the studio icon, so the watcher
# host has a normal Desktop icon.
#
#   ./make_monitor_app.sh                 # build onto ~/Desktop
#   ./make_monitor_app.sh --dest ~/Applications
#   ./make_monitor_app.sh --remove        # delete the bundle again
#
# Run it ON THE WATCHER HOST, as the account the LaunchAgent belongs to. The
# panel drives launchctl in that user's gui/<uid> domain and tails that user's
# ~/Library/Logs/buffalovfx/qt_watcher.log, so it only reports on the machine
# and the account it runs under. The log path is baked in at build time for
# that reason.
#
# The bundle contains NO copy of the panel. It runs
# watcher_monitor/launch_watcher_monitor.command from this checkout, so the app
# never goes stale: pull the repo and the icon launches the new code.
#
# WHY osacompile AND NOT A HAND-ROLLED BUNDLE
#   The first version put a #!/bin/bash script in Contents/MacOS and pointed
#   CFBundleExecutable at it. Finder refused to launch it:
#
#       _LSOpenURLsWithCompletionHandler() failed with error -10669
#
#   A shell script has no Mach-O header, so LaunchServices has no architecture
#   or platform to check. macOS answers that refusal by offering a system
#   update, which then cannot reach Apple -- so the only thing the user sees is
#   a "Unable to check for updates" alert with no mention of our app at all.
#   Running the same launcher from Terminal worked throughout, because Terminal
#   never goes through LaunchServices. Verified on M4MM02, 2026-09-22.
#
#   osacompile's applet is Apple's own signed Mach-O, so the bundle launches.
#   This script therefore only replaces the applet's ICON and leaves its
#   Info.plist exactly as osacompile wrote it -- that combination is what was
#   tested working, and editing a signed bundle's plist is a good way to get
#   back to where we started.
#
# Safe to re-run: the bundle is rebuilt from scratch each time.
#
# Repo: russelling/BUF_Mac_watcher
#
set -uo pipefail

APP_NAME="QT Watcher Monitor"

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$SELF_DIR/launch_watcher_monitor.command"
TEMPLATE="$SELF_DIR/monitor_app.applescript.tmpl"
SG_PYTHON="/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3"
LOG_DIR="$HOME/Library/Logs/buffalovfx"

# The monitor has its own mark so it is distinguishable in the Dock from the
# watcher daemon's icon. Falls back to the daemon's if it is ever missing.
ICON_SRC="$SELF_DIR/../icons/watcher_monitor_icon.icns"
[ -f "$ICON_SRC" ] || ICON_SRC="$SELF_DIR/../icons/qt_watcher_icon.icns"

DEST="$HOME/Desktop"
REMOVE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dest)   DEST="${2:?--dest needs a directory}"; shift 2 ;;
    --remove) REMOVE=1; shift ;;
    --help|-h)
      awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"
      exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || { echo "macOS only." >&2; exit 1; }

APP="$DEST/$APP_NAME.app"

if [ "$REMOVE" -eq 1 ]; then
  if [ -d "$APP" ]; then
    rm -rf "$APP" && echo "Removed $APP"
  else
    echo "Nothing at $APP"
  fi
  exit 0
fi

command -v osacompile >/dev/null || {
  echo "osacompile not found — it ships with macOS; something is wrong." >&2
  exit 1
}
[ -f "$LAUNCHER" ] || { echo "Launcher not found: $LAUNCHER" >&2; exit 1; }
[ -f "$TEMPLATE" ] || { echo "Template not found: $TEMPLATE" >&2; exit 1; }
[ -d "$DEST" ]     || { echo "Destination not a directory: $DEST" >&2; exit 1; }

# The launcher is not executable in a fresh clone on some checkouts.
[ -x "$LAUNCHER" ] || chmod +x "$LAUNCHER" 2>/dev/null || true

# --- source ------------------------------------------------------------------
# Paths are substituted rather than interpolated by the shell so the template
# stays a readable, compilable AppleScript file on its own.
# A directory, so the source keeps its .applescript extension -- osacompile
# picks its input language from that.
WORK="$(mktemp -d -t monitorapp.XXXXXX 2>/dev/null || mktemp -d)" || {
  echo "Could not create a temporary directory." >&2; exit 1; }
trap 'rm -rf "$WORK"' EXIT
SRC="$WORK/monitor_app.applescript"

python3 - "$TEMPLATE" "$SRC" "$LAUNCHER" "$SG_PYTHON" "$LOG_DIR" <<'PY'
import sys
tmpl, out, launcher, sg_python, log_dir = sys.argv[1:6]
text = open(tmpl, encoding="utf-8").read()
for token, value in (("@@LAUNCHER@@", launcher),
                     ("@@SG_PYTHON@@", sg_python),
                     ("@@LOG_DIR@@", log_dir)):
    if token not in text:
        sys.exit("template is missing %s" % token)
    if '"' in value:
        sys.exit("refusing to substitute a path containing a quote: %s" % value)
    text = text.replace(token, value)
open(out, "w", encoding="utf-8").write(text)
PY
[ $? -eq 0 ] || { echo "Could not prepare the AppleScript source." >&2; exit 1; }

# --- build -------------------------------------------------------------------
rm -rf "$APP"
if ! osacompile -o "$APP" "$SRC"; then
  echo "osacompile failed — the AppleScript did not compile." >&2
  rm -rf "$APP"
  exit 1
fi

# --- icon --------------------------------------------------------------------
# osacompile's Info.plist points CFBundleIconFile at "applet", so replacing
# that file is the whole job. Nothing else in the bundle is touched.
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APP/Contents/Resources/applet.icns"
else
  echo "NOTE: icon not found at $ICON_SRC — the app keeps the generic one."
fi

# Nudge Finder/LaunchServices so the icon appears without a logout.
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP" >/dev/null 2>&1 || true

echo "Built:    $APP"
echo "Launches: $LAUNCHER"
echo "Log:      $LOG_DIR/monitor_app.log"
[ -f "$ICON_SRC" ] && echo "Icon:     $ICON_SRC"
