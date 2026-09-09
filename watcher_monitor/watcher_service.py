"""
watcher_service.py

launchctl wrapper for the qt_watcher LaunchAgent. Deliberately free of any Qt
import so the parsing and command construction can be unit tested without a
display.

Every launchctl call is bounded by a timeout: launchctl can block when the
launchd domain is wedged, and a hung call must not freeze the GUI.
"""

import os
import re
import subprocess

LABEL = "com.buffalovfx.qtwatcher"
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)
LOG_PATH = os.path.expanduser("~/Library/Logs/buffalovfx/qt_watcher.log")

# launchctl is normally instant; anything slower means a wedged domain.
LAUNCHCTL_TIMEOUT = 15


def gui_domain():
    """launchd domain for the current user, e.g. 'gui/503'."""
    return "gui/%d" % os.getuid()


def service_target():
    """Fully qualified service target, e.g. 'gui/503/com.buffalovfx.qtwatcher'."""
    return "%s/%s" % (gui_domain(), LABEL)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_print_output(text):
    """
    Pull the interesting fields out of `launchctl print <target>`.

    Returns a dict with: loaded, state, pid, runs, last_exit.

    NOTE: `launchctl print` emits several `state = ...` lines - the job's own
    state first, then `state = active` for each nested endpoint. Only the
    FIRST is the job, so this takes that one rather than the last.
    """
    result = {
        "loaded": False,
        "state": None,
        "pid": None,
        "runs": None,
        "last_exit": None,
    }
    if not text:
        return result

    # "Could not find service ... in domain for user gui: 503"
    if "Could not find service" in text or "No such process" in text:
        return result

    result["loaded"] = True

    # Capture the whole value: states like "not running" contain a space.
    m = re.search(r"^\s*state\s*=\s*(.+?)\s*$", text, re.MULTILINE)
    if m:
        result["state"] = m.group(1)

    m = re.search(r"^\s*pid\s*=\s*(\d+)", text, re.MULTILINE)
    if m:
        result["pid"] = int(m.group(1))

    m = re.search(r"^\s*runs\s*=\s*(\d+)", text, re.MULTILINE)
    if m:
        result["runs"] = int(m.group(1))

    m = re.search(r"^\s*last exit code\s*=\s*(.+?)\s*$", text, re.MULTILINE)
    if m:
        result["last_exit"] = m.group(1)

    return result


def summarize(status):
    """One-line human summary plus a state key for colouring the dot."""
    if not status["loaded"]:
        return "not loaded", "stopped"
    if status["state"] == "running":
        pid = status["pid"]
        return ("running (pid %s)" % pid) if pid else "running", "running"
    # Loaded but not running: registered and waiting, or it died.
    return "loaded, %s" % (status["state"] or "unknown"), "warn"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _run(cmd):
    """Run a command, returning (ok, combined_output). Never raises."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return False, "timed out after %ds: %s" % (LAUNCHCTL_TIMEOUT, " ".join(cmd))
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out.strip()


def status():
    """Current service status as a dict (see parse_print_output)."""
    _, out = _run(["launchctl", "print", service_target()])
    return parse_print_output(out)


def start():
    """bootstrap the LaunchAgent into the GUI domain."""
    if not os.path.exists(PLIST_PATH):
        return False, ("plist not found: %s\nRun install_qt_watcher.sh first."
                       % PLIST_PATH)
    return _run(["launchctl", "bootstrap", gui_domain(), PLIST_PATH])


def stop():
    """bootout the LaunchAgent. 'No such process' means already stopped."""
    ok, out = _run(["launchctl", "bootout", service_target()])
    if not ok and "No such process" in out:
        return True, "already stopped"
    return ok, out


def restart():
    """
    Full bootout + bootstrap rather than `kickstart -k`.

    kickstart only restarts the process; it does not re-read the plist, and it
    does not clear a wedged job. bootout+bootstrap is what actually recovered
    this service during the Aug 2026 incident, so that is what this does.
    """
    stop()
    return start()


def read_log_tail(max_lines=200):
    """Last N lines of the watcher log, or a message explaining its absence."""
    if not os.path.exists(LOG_PATH):
        return "No log yet at %s\n(The watcher writes it on its first poll.)" % LOG_PATH
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        return "Could not read %s: %s" % (LOG_PATH, exc)
    return "".join(lines[-max_lines:])
