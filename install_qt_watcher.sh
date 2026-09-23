#!/usr/bin/env bash
#
# install_qt_watcher.sh — provision the QT review watcher on a macOS host.
#
# Takes a bare Mac to a running com.buffalovfx.qtwatcher LaunchAgent:
# checks every prerequisite, offers to install what's missing, generates the
# LaunchAgent plist with correct (post-relocation, local-log) paths, then
# bootstraps and verifies it.
#
# Safe to re-run. Every step is idempotent and skips work already done, so
# this doubles as a health check on a working machine.
#
#   ./install_qt_watcher.sh              # check, prompt before each install
#   ./install_qt_watcher.sh --yes        # assume yes (unattended)
#   ./install_qt_watcher.sh --check-only # report only, change nothing
#   ./install_qt_watcher.sh --help
#
# Repo: russelling/BUF_Mac_watcher
#
set -uo pipefail

# ---------------------------------------------------------------------------
# Configuration — edit these if the storage layout changes.
# Each may also be overridden from the environment, e.g.
#   CONFIG_ROOT=/some/other/path ./install_qt_watcher.sh
# ---------------------------------------------------------------------------
LABEL="${LABEL:-com.buffalovfx.qtwatcher}"
STORAGE_ROOT="${STORAGE_ROOT:-/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx}"

# Pipeline configuration ROOT — the folder CONTAINING config/ and install/.
# NOTE: must NOT end in /config. Toolkit appends config/core/... itself.
CONFIG_ROOT="${CONFIG_ROOT:-$STORAGE_ROOT/repo/pipeline/config/flow/current}"

# Where qt_watcher.py lives (BUF_Mac_watcher checkout). Auto-detected if unset.
WATCHER_SCRIPT="${WATCHER_SCRIPT:-}"

# This installer lives in the BUF_Mac_watcher checkout, so scripts/ is its
# sibling. Resolved once here because two steps need it: step 4 reads the
# pinned ffmpeg path out of scripts/qt_bake_oiio.py, and step 7 locates
# scripts/qt_watcher.py.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"

# Flow Production Tracking Desktop bundled interpreter.
FLOW_PYTHON="${FLOW_PYTHON:-/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3}"

# Logs MUST be local. launchd's spawn context lacks the login session's SMB
# auth, so stdio redirects onto the share fail with EX_CONFIG (exit 78) and
# the job never starts — with nothing written anywhere to tell you why.
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/buffalovfx}"

PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
GUI_DOMAIN="gui/$(id -u)"

# ---------------------------------------------------------------------------
ASSUME_YES=0
CHECK_ONLY=0
FAILURES=0
INSTALLED=()
SKIPPED=()

for arg in "$@"; do
  case "$arg" in
    --yes|-y)     ASSUME_YES=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    --help|-h)
      # Print the header comment block, stopping at the first non-comment line.
      awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"
      exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  B=$(tput bold 2>/dev/null || true);  N=$(tput sgr0 2>/dev/null || true)
  R=$(tput setaf 1 2>/dev/null || true); G=$(tput setaf 2 2>/dev/null || true)
  Y=$(tput setaf 3 2>/dev/null || true); C=$(tput setaf 6 2>/dev/null || true)
else
  B=""; N=""; R=""; G=""; Y=""; C=""
fi

hdr()  { printf "\n%s=== %s ===%s\n" "$B" "$*" "$N"; }
ok()   { printf "  %s[ OK ]%s %s\n"   "$G" "$N" "$*"; }
warn() { printf "  %s[WARN]%s %s\n"   "$Y" "$N" "$*"; }
bad()  { printf "  %s[FAIL]%s %s\n"   "$R" "$N" "$*"; FAILURES=$((FAILURES+1)); }
info() { printf "  %s->%s   %s\n"     "$C" "$N" "$*"; }

# ask "prompt" -> 0 yes / 1 no. Honours --yes and --check-only.
ask() {
  if [ "$CHECK_ONLY" -eq 1 ]; then SKIPPED+=("$1"); return 1; fi
  if [ "$ASSUME_YES" -eq 1 ]; then return 0; fi
  printf "  %s?%s    %s [y/N] " "$C" "$N" "$1"
  read -r reply </dev/tty || return 1
  case "$reply" in [yY]*) return 0 ;; *) SKIPPED+=("$1"); return 1 ;; esac
}

die() { printf "\n%sAborted:%s %s\n" "$R" "$N" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
hdr "Environment"

[ "$(uname -s)" = "Darwin" ] || die "This installer is macOS-only."
[ "$(id -u)" -ne 0 ] || die \
  "Do not run this as root. This is a per-user LaunchAgent; the root/system
   domain does not carry the login session's SMB authentication, which is
   exactly what the watcher needs to reach the share."

ok "macOS $(sw_vers -productVersion) ($(uname -m)), user $(id -un), uid $(id -u)"
[ "$CHECK_ONLY" -eq 1 ] && warn "--check-only: nothing will be installed or changed"

# ---------------------------------------------------------------------------
hdr "1. Storage mount"

if [ -d "$STORAGE_ROOT" ]; then
  ok "Share mounted: $STORAGE_ROOT"
else
  bad "Share NOT mounted: $STORAGE_ROOT"
  info "Start LucidLink (or mount the SMB share) and re-run. Every check"
  info "below depends on this, so stopping here."
  exit 1
fi

# ---------------------------------------------------------------------------
hdr "2. Homebrew"

BREW=""
for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$candidate" ] && { BREW="$candidate"; break; }
done
[ -z "$BREW" ] && BREW="$(command -v brew 2>/dev/null || true)"

if [ -n "$BREW" ]; then
  ok "Homebrew: $BREW"
else
  bad "Homebrew not installed (not preinstalled on macOS)"
  if ask "Install Homebrew now? (https://brew.sh)"; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
      || die "Homebrew install failed."
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      [ -x "$candidate" ] && { BREW="$candidate"; break; }
    done
    [ -n "$BREW" ] || die "Homebrew installed but 'brew' not found on the expected paths."
    INSTALLED+=("Homebrew")
    ok "Homebrew: $BREW"
  else
    info "Skipping. oiiotool and ffmpeg cannot be installed without it."
  fi
fi

# ---------------------------------------------------------------------------
hdr "3. OpenImageIO (oiiotool)"

OIIOTOOL=""
for candidate in /opt/homebrew/bin/oiiotool /usr/local/bin/oiiotool; do
  [ -x "$candidate" ] && { OIIOTOOL="$candidate"; break; }
done
[ -z "$OIIOTOOL" ] && OIIOTOOL="$(command -v oiiotool 2>/dev/null || true)"

if [ -n "$OIIOTOOL" ]; then
  ok "oiiotool: $OIIOTOOL ($("$OIIOTOOL" --version 2>/dev/null | head -1))"
elif [ -n "$BREW" ]; then
  bad "oiiotool not found — required for the ACEScg colour pipeline and slate"
  if ask "brew install openimageio ?"; then
    "$BREW" install openimageio || die "openimageio install failed."
    OIIOTOOL="$(command -v oiiotool 2>/dev/null || echo /opt/homebrew/bin/oiiotool)"
    INSTALLED+=("openimageio")
    ok "oiiotool: $OIIOTOOL"
  fi
else
  bad "oiiotool not found and Homebrew unavailable"
fi

# ---------------------------------------------------------------------------
hdr "4. ffmpeg (needs drawtext/freetype)"

# The acceptance test is drawtext support, NOT which formula is installed.
# The core ffmpeg formula has historically lacked it; the slate and per-frame
# burn-ins fail at runtime without it while the watcher still looks healthy.
has_drawtext() {
  [ -n "${1:-}" ] && [ -x "$1" ] && \
    "$1" -hide_banner -filters 2>/dev/null | grep -q '[[:space:]]drawtext[[:space:]]'
}

find_ffmpeg_with_drawtext() {
  local c
  for c in "${FFMPEG_PINNED:-}" \
           /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
           /opt/homebrew/bin/ffmpeg \
           /usr/local/bin/ffmpeg \
           "$(command -v ffmpeg 2>/dev/null || true)"; do
    if has_drawtext "$c"; then printf '%s' "$c"; return 0; fi
  done
  return 1
}

# The bake does NOT use whatever ffmpeg is on PATH: qt_bake_oiio.py hard-codes
# an absolute path in its FFMPEG constant. Read that constant from the sibling
# script so this check can never drift from the binary the bake actually runs.
# (watcher_launch.txt reads it the same way, for the same reason.)
FFMPEG_PINNED=""
if [ -n "${SELF_DIR:-}" ] && [ -f "$SELF_DIR/scripts/qt_bake_oiio.py" ]; then
  FFMPEG_PINNED="$(sed -n 's/^FFMPEG = "\(.*\)"/\1/p' \
    "$SELF_DIR/scripts/qt_bake_oiio.py" | head -1)"
fi

# The requirement is the pinned path, not "some ffmpeg with drawtext".
if [ -n "$FFMPEG_PINNED" ] && has_drawtext "$FFMPEG_PINNED"; then
  FFMPEG="$FFMPEG_PINNED"
  ok "ffmpeg with drawtext: $FFMPEG (pinned in qt_bake_oiio.py)"
else
  FFMPEG="$(find_ffmpeg_with_drawtext || true)"

  if [ -z "$FFMPEG_PINNED" ] && [ -n "$FFMPEG" ]; then
    # qt_bake_oiio.py was not beside this installer, so the pin could not be
    # read. Fall back to the old, weaker PATH test rather than pass silently.
    warn "Could not read the FFMPEG pin from scripts/qt_bake_oiio.py"
    info "Falling back to a PATH test. Confirm the bake's FFMPEG constant"
    info "points at a drawtext-capable build before trusting this."
    ok "ffmpeg with drawtext: $FFMPEG"
  else
    if [ -n "$FFMPEG_PINNED" ]; then
      bad "qt_bake_oiio.py pins $FFMPEG_PINNED — missing, or no drawtext"
    elif [ -n "$(command -v ffmpeg 2>/dev/null || true)" ]; then
      bad "ffmpeg found ($(command -v ffmpeg)) but it has NO drawtext filter"
      info "Slate and burn-ins will fail at bake time. A 'full' build is needed."
    else
      bad "ffmpeg not found"
    fi

    if [ -n "$FFMPEG" ] && [ "$FFMPEG" != "$FFMPEG_PINNED" ]; then
      info "A drawtext-capable ffmpeg does exist at:"
      info "  $FFMPEG"
      info "but the bake hard-codes the pinned path, so it will never run it."
    fi

    info "This studio uses the 'ffmpeg-full' formula. It installs alongside"
    info "any core ffmpeg, at /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg."
    info "The homebrew-ffmpeg tap is NOT a substitute: its formula is also"
    info "named 'ffmpeg', so brew refuses it whenever core ffmpeg is present."

    if [ -n "$BREW" ] && ask "Install ffmpeg-full with Homebrew now?"; then
      "$BREW" install ffmpeg-full || \
        warn "brew install ffmpeg-full failed — see watcher_launch.txt."
      if [ -n "$FFMPEG_PINNED" ] && has_drawtext "$FFMPEG_PINNED"; then
        FFMPEG="$FFMPEG_PINNED"
      else
        FFMPEG="$(find_ffmpeg_with_drawtext || true)"
      fi
      if [ -n "$FFMPEG" ] && { [ -z "$FFMPEG_PINNED" ] || [ "$FFMPEG" = "$FFMPEG_PINNED" ]; }; then
        INSTALLED+=("ffmpeg-full (drawtext-capable)")
        # The bad() above incremented FAILURES; a capable build is now in
        # place, so retract it. Without this the run ends "1 check failed"
        # and refuses to install the LaunchAgent, two lines after OK.
        FAILURES=$((FAILURES > 0 ? FAILURES - 1 : 0))
        ok "ffmpeg with drawtext: $FFMPEG"
      else
        bad "Still no drawtext-capable ffmpeg at the pinned path."
        info "If ffmpeg-full landed somewhere else, point the FFMPEG constant"
        info "in scripts/qt_bake_oiio.py at it. Installed versions:"
        ls -1 /opt/homebrew/Cellar/ffmpeg-full/ 2>/dev/null | sed 's/^/       /'
      fi
    fi
  fi
fi

# ---------------------------------------------------------------------------
hdr "5. Flow Production Tracking Desktop"

if [ -x "$FLOW_PYTHON" ]; then
  ok "Bundled Python: $FLOW_PYTHON ($("$FLOW_PYTHON" --version 2>&1))"
else
  bad "Flow Desktop interpreter not found at: $FLOW_PYTHON"
  info "This one cannot be automated — it's a GUI installer requiring sign-in."
  info "Download from your own site so the version matches:"
  info "  https://buffalovfx.shotgrid.autodesk.com  ->  Apps menu  ->  Desktop App"
  info "Install it, sign in to the buffalovfx site once, then re-run this script."
  info "(If it landed elsewhere, re-run with FLOW_PYTHON=/path/to/python3)"
fi

# ---------------------------------------------------------------------------
hdr "6. Pipeline configuration + sgtk import"

PC_YML="$CONFIG_ROOT/config/core/pipeline_configuration.yml"
TK_CORE_PY="$CONFIG_ROOT/install/core/python"

if [ -f "$PC_YML" ]; then
  ok "Pipeline config: $PC_YML"
else
  bad "Pipeline config metadata not found: $PC_YML"
  info "CONFIG_ROOT must be the pipeline configuration ROOT — the folder"
  info "containing config/ and install/ — NOT the config/ folder itself."
fi

if [ -d "$TK_CORE_PY" ]; then
  ok "tk-core: $TK_CORE_PY"
else
  bad "tk-core not found: $TK_CORE_PY"
fi

# qt_watcher.py authenticates via sgtk.sgtk_from_path() against the pipeline
# config itself (no separate API script), so this import is the real gate.
if [ -x "$FLOW_PYTHON" ] && [ -d "$TK_CORE_PY" ]; then
  if PYTHONPATH="$TK_CORE_PY" "$FLOW_PYTHON" \
       -c "import sgtk, shotgun_api3" >/dev/null 2>&1; then
    ok "sgtk + shotgun_api3 import cleanly under $("$FLOW_PYTHON" --version 2>&1)"
  else
    bad "sgtk/shotgun_api3 FAILED to import — the watcher cannot run"
    info "Full error:"
    PYTHONPATH="$TK_CORE_PY" "$FLOW_PYTHON" -c "import sgtk, shotgun_api3" 2>&1 | sed 's/^/       /'
    info "If this is a Python version incompatibility, point the plist at"
    info "another interpreter instead (e.g. brew install python@3.11) —"
    info "sgtk arrives via PYTHONPATH, so it need not be the bundled one."
  fi
else
  warn "Skipping sgtk import test (interpreter or tk-core missing above)"
fi

# ---------------------------------------------------------------------------
hdr "7. Watcher script"

if [ -z "$WATCHER_SCRIPT" ]; then
  # This installer lives in the BUF_Mac_watcher checkout, so the watcher is
  # its sibling: <repo>/scripts/qt_watcher.py. Deriving it from here is
  # correct by construction and survives any relocation of the repo.
  #
  # The previous `find "$STORAGE_ROOT" -maxdepth 5` could not reach it at all:
  # post-relocation the script sits EIGHT levels below the storage root, so
  # the installer reported "qt_watcher.py not found" while that very file was
  # running in front of it (Mac-Studio, 2026-09-22). Raising the depth would
  # also mean walking shots/ and assets/ over SMB, which is slow enough to
  # look like a hang.
  # SELF_DIR is resolved once at the top of this script.
  if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/scripts/qt_watcher.py" ]; then
    WATCHER_SCRIPT="$SELF_DIR/scripts/qt_watcher.py"
  else
    # Fallback, scoped to repo/ so it cannot wander into the render trees.
    WATCHER_SCRIPT="$(find "$STORAGE_ROOT/repo" -maxdepth 8 -name qt_watcher.py -type f 2>/dev/null | head -1)"
  fi
fi

if [ -n "$WATCHER_SCRIPT" ] && [ -f "$WATCHER_SCRIPT" ]; then
  ok "qt_watcher.py: $WATCHER_SCRIPT"
  WATCHER_DIR="$(dirname "$WATCHER_SCRIPT")"
else
  bad "qt_watcher.py not found under $STORAGE_ROOT"
  info "Clone/deploy the BUF_Mac_watcher repo, or re-run with"
  info "  WATCHER_SCRIPT=/path/to/qt_watcher.py"
  WATCHER_DIR=""
fi

# Stale-path guard: the config moved out of buffalo_flow_config in 2026-08.
if [ -n "$WATCHER_DIR" ] && grep -rl "buffalo_flow_config" "$WATCHER_DIR" >/dev/null 2>&1; then
  warn "Files under $WATCHER_DIR still reference 'buffalo_flow_config' (pre-relocation path):"
  grep -rl "buffalo_flow_config" "$WATCHER_DIR" 2>/dev/null | sed 's/^/       /'
  info "The plist this script generates uses the correct paths, but check"
  info "whether any of the above need updating in the BUF_Mac_watcher repo."
fi

# ---------------------------------------------------------------------------
hdr "8. Duplicate-watcher check"

info "Only ONE host may run this watcher. Two watchers polling the same"
info "trees race on the same .render_complete_*.json flags and produce"
info "double-encoded QTs and duplicate Versions in Flow."

# Look, rather than just advise. On 2026-09-22 a nine-day-old watcher running
# PRE-PATCH code was still polling while two more were started the same
# afternoon; nothing surfaced that until the log was read by hand. A process
# started before a code change is running the OLD code, whatever is on disk.
RUNNING_WATCHERS="$(pgrep -f "qt_watcher.py" 2>/dev/null || true)"
if [ -n "$RUNNING_WATCHERS" ]; then
  warn "Watcher process(es) already running on THIS machine:"
  # shellcheck disable=SC2086
  ps -o pid,user,start,command -p $RUNNING_WATCHERS 2>/dev/null | sed 's/^/       /'
  info "Check the START time: anything predating your last code change is"
  info "running the old code from memory. Stop it before bootstrapping, or"
  info "you will have two watchers on one tree."
else
  ok "No qt_watcher process currently running on this machine"
fi
info "On any OTHER host that still runs it:"
info "  launchctl bootout gui/\$(id -u)/$LABEL"

# ---------------------------------------------------------------------------
hdr "9. LaunchAgent"

if [ "$FAILURES" -gt 0 ]; then
  warn "$FAILURES prerequisite check(s) failed — not installing the LaunchAgent."
  info "Fix the items above and re-run. Installing now would produce a job"
  info "that starts and then fails silently."
else
  mkdir -p "$LOG_DIR" "$PLIST_DIR"
  ok "Log directory (local): $LOG_DIR"

  # --- Local launcher -------------------------------------------------------
  # launchd's spawn context lacks the login session's SMB authentication, so
  # it cannot reach the share BEFORE the job starts: a ProgramArguments path
  # on the share fails as [Errno 2], and a WorkingDirectory on the share fails
  # as EX_CONFIG (78). Same root cause as the stdio paths above, which is why
  # those are local too.
  #
  # So launchd is pointed at a launcher on the LOCAL disk, which it can always
  # find. The launcher waits for the share, then execs the real watcher.
  LAUNCHER_DIR="$HOME/Library/Application Support/BuffaloVFX"
  LAUNCHER="$LAUNCHER_DIR/run_qt_watcher.sh"
  mkdir -p "$LAUNCHER_DIR"

  cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/bin/bash
# Generated by install_qt_watcher.sh -- do not edit here, edit the installer.
#
# Local on purpose: launchd can always reach this file, and it waits for the
# share rather than dying the instant the mount is not yet visible.

PY="$FLOW_PYTHON"
SCRIPT="$WATCHER_SCRIPT"
DIAG="$LOG_DIR/launcher.log"
WAIT_SECONDS=600
INTERVAL=10

echo "=== \$(date) launcher starting (uid \$(id -u)) ===" >> "\$DIAG" 2>&1

elapsed=0
while [ ! -r "\$SCRIPT" ]; do
  if [ "\$elapsed" -ge "\$WAIT_SECONDS" ]; then
    {
      echo "[\$(date)] share unreachable after \${WAIT_SECONDS}s: \$SCRIPT"
      echo "  /Volumes:"
      ls -la /Volumes/ 2>&1
      echo "  matching mounts:"
      /sbin/mount 2>&1 | grep -i -e lucid -e atv || echo "    (none)"
      echo "  Exiting non-zero so launchd retries."
    } >> "\$DIAG" 2>&1
    exit 1
  fi
  sleep "\$INTERVAL"
  elapsed=\$((elapsed + INTERVAL))
done

echo "[\$(date)] share reachable after \${elapsed}s -- starting watcher" >> "\$DIAG"
exec "\$PY" "\$SCRIPT"
LAUNCHER_EOF

  chmod +x "$LAUNCHER"
  ok "Local launcher: $LAUNCHER"

  NEW_PLIST="$(mktemp -t qtwatcher_plist)"
  # NOTE: plists do NOT expand '~' — every path here is absolute.
  cat > "$NEW_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <!-- The LOCAL launcher, never the script on the share: launchd cannot
         resolve a share path at spawn (see the launcher generation above). -->
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$LAUNCHER</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <!-- Local paths on purpose: launchd cannot create stdio redirects on the
         SMB share (EX_CONFIG / exit 78) because its spawn context lacks the
         login session's share authentication. -->
    <key>StandardOutPath</key>
    <string>$LOG_DIR/qt_watcher.log</string>

    <key>StandardErrorPath</key>
    <string>$LOG_DIR/qt_watcher_error.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$TK_CORE_PY</string>
    </dict>

    <!-- Local: a WorkingDirectory on the share is one more thing launchd
         must reach before the job starts, and it fails as EX_CONFIG (78). -->
    <key>WorkingDirectory</key>
    <string>$HOME</string>
</dict>
</plist>
PLIST

  if ! plutil -lint "$NEW_PLIST" >/dev/null 2>&1; then
    rm -f "$NEW_PLIST"; die "Generated plist failed plutil -lint (this is a bug in the script)."
  fi
  ok "Generated plist passes plutil -lint"

  if [ -f "$PLIST_PATH" ] && diff -q "$NEW_PLIST" "$PLIST_PATH" >/dev/null 2>&1; then
    ok "Installed plist already up to date: $PLIST_PATH"
    rm -f "$NEW_PLIST"
  else
    if [ -f "$PLIST_PATH" ]; then
      info "Existing plist differs:"
      diff "$PLIST_PATH" "$NEW_PLIST" | sed 's/^/       /' || true
    fi
    if ask "Write plist to $PLIST_PATH ?"; then
      # Copy LOCAL, never bootstrap off the share — launchd refuses a plist
      # on a network volume with a bare 'Input/output error'.
      cp "$NEW_PLIST" "$PLIST_PATH"
      chmod 644 "$PLIST_PATH"
      INSTALLED+=("LaunchAgent plist")
      ok "Wrote $PLIST_PATH"
    fi
    rm -f "$NEW_PLIST"
  fi

  if [ -f "$PLIST_PATH" ] && [ "$CHECK_ONLY" -eq 0 ]; then
    if ask "Bootstrap (restart) the watcher now?"; then
      # bootout+bootstrap, not unload/load: unload/load can resume corrupted
      # state left by earlier failed attempts.
      launchctl bootout "$GUI_DOMAIN/$LABEL" >/dev/null 2>&1 || true
      if launchctl bootstrap "$GUI_DOMAIN" "$PLIST_PATH" 2>/dev/null; then
        ok "Bootstrapped into $GUI_DOMAIN"
      else
        bad "Bootstrap failed. launchctl reports nearly everything as EIO;"
        info "check: plist readable, on local disk, label not already loaded."
      fi
      launchctl kickstart -k "$GUI_DOMAIN/$LABEL" >/dev/null 2>&1 || true
      sleep 2
    fi
  fi
fi

# ---------------------------------------------------------------------------
hdr "10. Status"

if launchctl print "$GUI_DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl print "$GUI_DOMAIN/$LABEL" \
    | grep -E "^[[:space:]]*(state|pid|runs|last exit code) " \
    | sed 's/^[[:space:]]*/  /'
  if launchctl print "$GUI_DOMAIN/$LABEL" 2>/dev/null | grep -q "state = running"; then
    ok "Watcher is running"
  else
    warn "Registered but not running — check $LOG_DIR/qt_watcher_error.log"
    warn "'last exit code = 78: EX_CONFIG' means a stdio path is unwritable."
  fi
else
  warn "Service not registered in $GUI_DOMAIN"
fi

# ---------------------------------------------------------------------------
hdr "11. Monitor app on the Desktop"

# The status panel talks to launchctl in THIS user's gui/<uid> domain and tails
# THIS user's log, so it only ever reports on the machine and account it was
# launched from. Building it here is the one place that is guaranteed to be the
# watcher host.
#
# Offered, never forced, and never fatal: the watcher does not need the panel,
# so a failure warns instead of calling bad(). A missing Desktop icon must not
# be the reason a LaunchAgent is refused.
MONITOR_BUILDER="${SELF_DIR:-}/watcher_monitor/make_monitor_app.sh"
MONITOR_APP="$HOME/Desktop/QT Watcher Monitor.app"

build_monitor_app() {
  local out
  if out="$(bash "$MONITOR_BUILDER" 2>&1)"; then
    return 0
  fi
  warn "Could not build the Desktop app. The watcher itself is unaffected."
  printf '%s\n' "$out" | sed 's/^/        /'
  return 1
}

if [ ! -f "$MONITOR_BUILDER" ]; then
  warn "Monitor builder not found: $MONITOR_BUILDER"
  info "Skipping the Desktop app. Nothing else is affected."
elif [ -d "$MONITOR_APP" ]; then
  ok "Monitor app already on the Desktop"
  if ask "Rebuild it (picks up a new icon or launcher)?"; then
    if build_monitor_app; then
      INSTALLED+=("QT Watcher Monitor.app (rebuilt)")
      ok "Rebuilt $MONITOR_APP"
    fi
  fi
elif ask "Put a QT Watcher Monitor icon on the Desktop?"; then
  if build_monitor_app; then
    INSTALLED+=("QT Watcher Monitor.app")
    ok "Built $MONITOR_APP"
  fi
fi

hdr "Summary"
if [ ${#INSTALLED[@]} -gt 0 ]; then
  printf "  Installed/updated:\n"; printf "    - %s\n" "${INSTALLED[@]}"
fi
if [ ${#SKIPPED[@]} -gt 0 ]; then
  printf "  Skipped:\n"; printf "    - %s\n" "${SKIPPED[@]}"
fi
if [ "$FAILURES" -gt 0 ]; then
  printf "\n  %s%d check(s) failed.%s Re-run after resolving them.\n" "$R" "$FAILURES" "$N"
  exit 1
fi
printf "\n  %sAll checks passed.%s\n" "$G" "$N"
printf "  Logs: %s/qt_watcher.log\n" "$LOG_DIR"
printf "  Status: launchctl print %s/%s\n\n" "$GUI_DOMAIN" "$LABEL"
