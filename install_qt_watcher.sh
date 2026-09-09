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
find_ffmpeg_with_drawtext() {
  local c
  for c in /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
           /opt/homebrew/bin/ffmpeg \
           /usr/local/bin/ffmpeg \
           "$(command -v ffmpeg 2>/dev/null || true)"; do
    if [ -z "$c" ] || [ ! -x "$c" ]; then continue; fi
    if "$c" -hide_banner -filters 2>/dev/null | grep -q '[[:space:]]drawtext[[:space:]]'; then
      printf '%s' "$c"; return 0
    fi
  done
  return 1
}

FFMPEG="$(find_ffmpeg_with_drawtext || true)"

if [ -n "$FFMPEG" ]; then
  ok "ffmpeg with drawtext: $FFMPEG"
else
  ANY_FFMPEG="$(command -v ffmpeg 2>/dev/null || true)"
  if [ -n "$ANY_FFMPEG" ]; then
    bad "ffmpeg found ($ANY_FFMPEG) but it has NO drawtext filter"
    info "Slate and burn-ins will fail at bake time. A 'full' build is needed."
  else
    bad "ffmpeg not found"
  fi
  if [ -n "$BREW" ] && ask "Try the homebrew-ffmpeg tap (full-featured build)?"; then
    "$BREW" tap homebrew-ffmpeg/ffmpeg 2>/dev/null || true
    "$BREW" install homebrew-ffmpeg/ffmpeg/ffmpeg || \
      warn "Tap install failed — see watcher_launch.txt for the formula this studio uses."
    FFMPEG="$(find_ffmpeg_with_drawtext || true)"
    if [ -n "$FFMPEG" ]; then
      INSTALLED+=("ffmpeg (drawtext-capable)")
      ok "ffmpeg with drawtext: $FFMPEG"
    else
      bad "Still no drawtext-capable ffmpeg. Consult watcher_launch.txt."
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
  WATCHER_SCRIPT="$(find "$STORAGE_ROOT" -maxdepth 5 -name qt_watcher.py -type f 2>/dev/null | head -1)"
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
info "If the old Mac Studio still runs it, on THAT machine run:"
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

  NEW_PLIST="$(mktemp -t qtwatcher_plist)"
  # NOTE: plists do NOT expand '~' — every path here is absolute.
  cat > "$NEW_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$FLOW_PYTHON</string>
        <string>$WATCHER_SCRIPT</string>
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

    <key>WorkingDirectory</key>
    <string>$WATCHER_DIR</string>
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
