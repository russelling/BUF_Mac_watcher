"""
qt_watcher.py

Polling daemon intended to run on the Mac Studio via launchd.

  1. Walks the shots/ and assets/ trees looking for .render_complete_*.json
     flag files written by:
       - render_complete_callback.py  (Nuke shot renders)
       - publish_turntable_unreal.py  (Unreal asset turntable renders)

  2. For each flag found, routes to the appropriate bake tool:

       Shot renders    ->  Nuke batch  (qt_bake_slate_burnin.py)
                           Outputs: shot review folder + dated editorial drop

       Asset turntable ->  OIIO + FFmpeg  (qt_bake_oiio.py)
                           Outputs: asset review folder + dated editorial drop

  3. Uploads the resulting QT to ShotGrid as a Version linked to the
     appropriate entity (Shot or Asset).

  4. Renames the flag to .processed_*.json so it isn't reprocessed.

Run as a launchd service — see com.buffalovfx.qtwatcher.plist
"""

import datetime
import glob
import json
import os
import signal
import subprocess
import sys
import time

import sgtk


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/current"
SHOTS_ROOT  = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots"
ASSETS_ROOT = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/assets"

NUKE_EXECUTABLE  = "/Applications/Nuke17.0v1/Nuke17.0.app/Contents/MacOS/Nuke17.0"
PYTHON3          = "/opt/homebrew/bin/python3"

SCRIPTS_DIR      = os.path.dirname(os.path.abspath(__file__))
NUKE_BAKE_SCRIPT = os.path.join(SCRIPTS_DIR, "qt_bake_slate_burnin.py")
OIIO_BAKE_SCRIPT = os.path.join(SCRIPTS_DIR, "qt_bake_oiio.py")

POLL_INTERVAL_SECONDS = 30

# Hard ceiling on a single bake. Without this the watcher blocks FOREVER on a
# stalled oiiotool/ffmpeg read against the SMB share - the process stays alive
# and launchd reports it running, but polling has silently stopped. That is
# the failure mode behind the Aug 2026 wedged-watcher incident. Generous
# enough for a long EXR sequence; anything past it is a hang, not slow work.
BAKE_TIMEOUT_SECONDS = 2 * 60 * 60      # 2 hours
# Grace period between SIGTERM and SIGKILL when tearing down a timed-out bake.
BAKE_KILL_GRACE_SECONDS = 10

# A flag whose bake or upload keeps failing is retried on every poll. After
# this many CONSECUTIVE failures it is quarantined (renamed to .failed_*.json)
# so one bad flag can't re-spawn a full bake every 30s forever. Counts are
# in-memory only: a watcher restart gives every flag a clean slate, which is
# the desired behaviour after fixing whatever broke.
MAX_FLAG_FAILURES = 3

# Log a liveness line every N polls even when nothing happens, so an idle
# watcher is distinguishable from a dead one. 120 * 30s = once an hour.
HEARTBEAT_EVERY_N_POLLS = 120

# "delete" or "rename" — what to do with flag files after processing
PROCESSED_ACTION = "rename"

LOG_FILE = os.path.join(SCRIPTS_DIR, "logs", "qt_watcher.log")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (timestamp, msg)
    print(line)
    try:
        log_dir = os.path.dirname(LOG_FILE)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# SGTK bootstrap
# ---------------------------------------------------------------------------

def get_sgtk():
    """Bootstrap an sgtk instance + shotgun connection for this config."""
    tk = sgtk.sgtk_from_path(CONFIG_PATH)
    sg = tk.shotgun
    return tk, sg


# ---------------------------------------------------------------------------
# Flag discovery
# ---------------------------------------------------------------------------

# Last known reachability of each watched root, so an unmounted share is
# logged on the transition rather than every 30 seconds forever.
_root_reachable = {}


def find_flags():
    """
    Find all .render_complete_*.json flag files under shots/ and assets/.
    Uses recursive os.walk so depth doesn't matter.

    Returns (flags, scan_errors). scan_errors is a list of human-readable
    strings describing anything that made this scan INCOMPLETE - an
    unreachable root, or a directory os.walk could not read.

    Both of those used to fail silently, which is why flag pickup looked
    intermittent: os.walk's default onerror=None DISCARDS errors from the
    underlying scandir(), so a transient SMB failure would drop an entire
    subtree from the scan with no exception and nothing in the log. A caller
    that gets a non-empty scan_errors must NOT treat "no flags found" as
    "there is nothing to do".
    """
    flags = []
    scan_errors = []

    def _on_walk_error(exc):
        # Raised for the directory os.walk failed to list. Recorded rather
        # than swallowed so an incomplete scan is visible.
        scan_errors.append("could not list %s: %s" % (getattr(exc, "filename", "?"), exc))

    for root_dir in [SHOTS_ROOT, ASSETS_ROOT]:
        reachable = os.path.isdir(root_dir)
        if _root_reachable.get(root_dir) != reachable:
            if reachable:
                log("Root now reachable: %s" % root_dir)
            else:
                log(
                    "WARNING: root NOT reachable, skipping it this poll (is the "
                    "share mounted?): %s" % root_dir
                )
            _root_reachable[root_dir] = reachable

        if not reachable:
            scan_errors.append("root not reachable: %s" % root_dir)
            continue

        for root, _dirs, files in os.walk(root_dir, onerror=_on_walk_error):
            for fname in files:
                if fname.startswith(".render_complete_") and fname.endswith(".json"):
                    flags.append(os.path.join(root, fname))

    return flags, scan_errors


# ---------------------------------------------------------------------------
# Output path resolution
# ---------------------------------------------------------------------------

def resolve_shot_output_paths(tk, data):
    """Resolve output paths for a shot render flag.

    The 'output' token is intentionally omitted: the EXR renders use the
    clean {Shot}_{Step}_v{version} convention (output resolves to null), and
    the movie templates either have no output key (ep_nuke_shot_render_movie)
    or an optional [_{nuke.output}] segment (editorial) that should drop when
    output is absent. So we do NOT inject an output/nuke.output field here.
    """
    now = datetime.datetime.now()
    # Prefer Sequence (Episode→Sequence→Shot); fall back to legacy scene.
    mid = data.get("sequence") or data.get("scene")
    fields = {
        "Episode":     data["episode"],
        "Sequence":    mid,
        "Scene":       mid,  # legacy keys if any old templates remain
        "Shot":        data["shot_code"],
        "vendor_code": data.get("vendor_code") or "INH",
        "Step":        data["step"],
        "version":     data["version"],
        "YYYY":        now.year,
        "MM":          now.month,
        "DD":          now.day,
    }
    # Only carry an output token through if the flag actually has a non-null
    # one (it normally does not). The editorial template's key is the dotted
    # "nuke.output"; ep_nuke_shot_render_movie has no output key at all.
    if data.get("output"):
        fields["nuke.output"] = data["output"]

    shot_movie_path      = tk.templates["ep_nuke_shot_render_movie"].apply_fields(fields)
    editorial_movie_path = tk.templates["editorial_to_editorial_movie"].apply_fields(fields)
    return shot_movie_path, editorial_movie_path


def _asset_hierarchy_fields(data):
    """
    Toolkit / path fields for the asset location schema:

      {type}/{script_name}/{real_name}/{variant}
    """
    script_name = (
        data.get("script_name")
        or data.get("entity_name")
        or data.get("Asset")
        or ""
    )
    return {
        "Asset": script_name,
        "sg_asset_type": data.get("asset_type") or data.get("sg_asset_type") or "",
        "script_name": script_name,
        "real_name": data.get("real_name") or "base",
        "variant": data.get("variant") or "base",
        "version": int(data.get("version") or 1),
    }


def _asset_publish_stem(data, step=None):
    """Version / movie stem: {script}_{real}_{variant}[_{step}]."""
    fields = _asset_hierarchy_fields(data)
    parts = [fields["script_name"], fields["real_name"], fields["variant"]]
    use_step = step if step is not None else data.get("step", "")
    use_step = (use_step or "").strip()
    if use_step and use_step not in {"reference", "ingest"}:
        parts.append(use_step)
    return "_".join(p for p in parts if p)


def resolve_asset_output_paths(tk, data):
    """Resolve output paths for an asset turntable flag."""
    now = datetime.datetime.now()
    version = data["version"]
    fields = _asset_hierarchy_fields(data)
    asset_movie_path = tk.templates["unreal_asset_turntable_movie"].apply_fields(fields)

    # Also drop a copy to the dated editorial folder
    editorial_dir = os.path.join(
        "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/io/editorial",
        "to_editorial",
        "%04d_%02d_%02d" % (now.year, now.month, now.day),
    )
    editorial_movie_path = os.path.join(
        editorial_dir,
        "%s_v%03d.mov" % (_asset_publish_stem(data), version),
    )
    return asset_movie_path, editorial_movie_path


# ---------------------------------------------------------------------------
# Bake invocation
# ---------------------------------------------------------------------------

def _run_bake(cmd, label):
    """
    Run a bake command with a hard timeout, returning True on success.

    Deliberately NOT subprocess.run(..., timeout=): the bake script spawns
    oiiotool/ffmpeg as its own children, and run()'s timeout only kills the
    DIRECT child. A stalled ffmpeg would be orphaned and keep holding the
    share open. Instead the bake gets its own process group
    (start_new_session=True) and the whole group is torn down on timeout -
    SIGTERM, a short grace period, then SIGKILL.
    """
    log("Running %s: %s" % (label, " ".join(cmd)))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except Exception as exc:
        log("ERROR launching %s: %s" % (label, exc))
        return False

    def _kill_group(sig):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError) as exc:
            log("WARNING: could not signal %s process group: %s" % (label, exc))

    try:
        stdout, stderr = proc.communicate(timeout=BAKE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log(
            "ERROR: %s exceeded %ds and appears hung — killing it. (Left "
            "unbounded this is what silently stops the watcher polling.)"
            % (label, BAKE_TIMEOUT_SECONDS)
        )
        _kill_group(signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=BAKE_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            log("%s ignored SIGTERM; sending SIGKILL" % label)
            _kill_group(signal.SIGKILL)
            try:
                stdout, stderr = proc.communicate(timeout=BAKE_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                # Unreapable (uninterruptible I/O wait on the share). Give up
                # on this bake but keep the watcher polling.
                log("WARNING: %s did not die even after SIGKILL; abandoning it" % label)
                return False
        return False

    if stdout:
        log(stdout)
    if proc.returncode != 0:
        log("ERROR in %s (exit %s): %s" % (label, proc.returncode, stderr))
        return False
    return True


def run_nuke_bake(flag_path, output_paths):
    """Invoke Nuke batch bake for shot renders."""
    cmd = [NUKE_EXECUTABLE, "-t", NUKE_BAKE_SCRIPT, flag_path] + list(output_paths)
    return _run_bake(cmd, "Nuke bake")


def run_oiio_bake(flag_path, output_paths):
    """Invoke OIIO+FFmpeg bake for asset turntable renders."""
    cmd = [PYTHON3, OIIO_BAKE_SCRIPT, flag_path] + list(output_paths)
    return _run_bake(cmd, "OIIO bake")


# ---------------------------------------------------------------------------
# ShotGrid upload
# ---------------------------------------------------------------------------

def _valid_list_values(sg, entity_type, field_name):
    """
    Return the set of valid values for a ShotGrid list field, or None if it
    can't be determined. Used to avoid setting a list field to a value that
    isn't a configured option (which errors or is silently dropped).
    """
    try:
        schema = sg.schema_field_read(entity_type, field_name)
        props = schema.get(field_name, {}).get("properties", {})
        valid = props.get("valid_values", {}).get("value")
        if valid:
            return set(valid)
    except Exception as exc:
        log("WARNING: could not read schema for %s.%s: %s"
            % (entity_type, field_name, exc))
    return None


def upload_version(sg, data, movie_path):
    """Create a Version in ShotGrid and upload the QT."""
    is_asset = data.get("type") == "asset_turntable"

    if is_asset:
        version_num = data.get("version", 1)
        version_code = "%s_v%03d" % (_asset_publish_stem(data), version_num)
    else:
        version_code = "%s_%s_%s_v%03d" % (
            data.get("shot_code", ""),
            data.get("vendor_code") or "INH",
            data.get("step", ""),
            data.get("version", 1),
        )

    version_data = {
        "project":           {"type": "Project", "id": data["project_id"]},
        "code":              version_code,
        "entity":            {"type": data.get("entity_type", "Shot"),
                              "id":   data["entity_id"]},
        "description":       data.get("description", ""),
        "sg_path_to_movie":  movie_path,
    }

    # sg_submitted_for is a LIST field: only set it if the flag's value is one
    # of the field's configured options. Setting an unconfigured value errors
    # or is silently dropped, so validate first and warn rather than fail.
    submitted_for = data.get("submitted_for")
    if submitted_for:
        valid = _valid_list_values(sg, "Version", "sg_submitted_for")
        if valid is None or submitted_for in valid:
            version_data["sg_submitted_for"] = submitted_for
        else:
            log("WARNING: submitted_for '%s' is not a valid sg_submitted_for "
                "option %s — leaving field unset" % (submitted_for, sorted(valid)))

    if data.get("task_id"):
        version_data["sg_task"] = {"type": "Task", "id": data["task_id"]}

    # Version.user = Artist / "submitted by" HumanUser from the drop app.
    user_id = data.get("user_id")
    if user_id:
        version_data["user"] = {"type": "HumanUser", "id": int(user_id)}

    # Remove None / empty values
    version_data = {k: v for k, v in version_data.items() if v not in (None, "")}

    try:
        version = sg.create("Version", version_data)
    except Exception as exc:
        log("ERROR creating Version %s: %s" % (version_code, exc))
        return None

    # Create succeeded but the movie upload can still fail. If it does, the
    # Version exists with no media attached - and since the caller now leaves
    # the flag in place to retry, a later poll would create a SECOND, equally
    # empty Version. Delete the orphan so retrying is idempotent.
    try:
        sg.upload(
            "Version",
            version["id"],
            movie_path,
            field_name="sg_uploaded_movie",
        )
    except Exception as exc:
        log("ERROR uploading movie to Version %s (%s): %s"
            % (version["id"], version_code, exc))
        try:
            sg.delete("Version", version["id"])
            log("Removed orphaned Version %s so the retry won't duplicate it"
                % version["id"])
        except Exception as del_exc:
            log("WARNING: could not remove orphaned Version %s: %s — a retry "
                "will create a duplicate; clean this up by hand"
                % (version["id"], del_exc))
        return None

    log("Uploaded Version %s (%s)" % (version["id"], version_code))
    return version


# ---------------------------------------------------------------------------
# Flag cleanup
# ---------------------------------------------------------------------------

def _rename_flag(flag_path, new_prefix):
    """Swap a flag's '.render_complete_' prefix for another, in place.

    Renames only the BASENAME so a directory that happens to contain
    '.render_complete_' can't be corrupted by a path-wide replace.
    """
    d = os.path.dirname(flag_path)
    base = os.path.basename(flag_path)
    new_base = base.replace(".render_complete_", new_prefix, 1)
    os.rename(flag_path, os.path.join(d, new_base))
    return os.path.join(d, new_base)


def mark_processed(flag_path):
    if PROCESSED_ACTION == "delete":
        os.remove(flag_path)
    else:
        _rename_flag(flag_path, ".processed_")


def quarantine_flag(flag_path):
    """Park a repeatedly-failing flag as .failed_*.json.

    Stops one broken flag from re-spawning a full bake every poll forever.
    Renaming (rather than deleting) keeps the payload for diagnosis, and
    renaming it back to .render_complete_* is all it takes to requeue once
    the underlying problem is fixed.
    """
    try:
        failed_path = _rename_flag(flag_path, ".failed_")
        log("QUARANTINED after %d consecutive failures: %s — rename it back to "
            ".render_complete_*.json to retry once the cause is fixed."
            % (MAX_FLAG_FAILURES, failed_path))
    except OSError as exc:
        log("WARNING: could not quarantine %s: %s" % (flag_path, exc))


# ---------------------------------------------------------------------------
# Per-flag processing
# ---------------------------------------------------------------------------

def process_flag(tk, sg, flag_path):
    """Bake + upload one flag.

    Returns True if the flag was fully handled (baked, uploaded and marked
    processed) and False if it should be retried on a later poll. Raising is
    also fine - the caller counts that as a failure too.
    """
    log("Found flag: %s" % flag_path)

    try:
        with open(flag_path, "r") as f:
            data = json.load(f)
    except ValueError as exc:
        # Almost always a half-written flag: the Nuke-side writer streams JSON
        # straight into the final watched filename, so a poll can catch it
        # mid-write. Harmless in itself - retry next poll, by which point the
        # write has landed. Persistently malformed flags hit the failure cap
        # and get quarantined.
        log("Flag not readable as JSON yet (likely still being written): %s (%s)"
            % (flag_path, exc))
        return False

    flag_type = data.get("type", "shot")
    is_asset  = flag_type == "asset_turntable"

    # ── Resolve output paths ─────────────────────────────────────────────────
    try:
        if is_asset:
            primary_path, editorial_path = resolve_asset_output_paths(tk, data)
        else:
            primary_path, editorial_path = resolve_shot_output_paths(tk, data)
    except Exception as exc:
        log("ERROR resolving output paths: %s" % exc)
        return False

    output_paths = [primary_path, editorial_path]

    # ── Run bake ─────────────────────────────────────────────────────────────
    # NOTE: both shots and asset turntables route through the license-free
    # OIIO+FFmpeg bake to avoid requiring an extra Nuke render license on
    # this headless watcher machine. run_nuke_bake() is kept below but is
    # currently unused.
    success = run_oiio_bake(flag_path, output_paths)

    if not success:
        log("Bake failed for: %s — flag left in place for retry" % flag_path)
        return False

    # ── Upload to ShotGrid ───────────────────────────────────────────────────
    # Everything below must succeed before the flag is consumed. Previously
    # the flag was marked processed regardless, so a failed upload left a QT
    # on disk, no Version in ShotGrid, and a .processed_ flag claiming it was
    # done - a silent partial failure with nothing left to retry from.
    if not os.path.exists(primary_path):
        log("ERROR: bake reported success but the expected output is missing: "
            "%s — flag left in place for retry" % primary_path)
        return False

    if upload_version(sg, data, primary_path) is None:
        log("ERROR: ShotGrid upload failed for %s — flag left in place for retry"
            % flag_path)
        return False

    # ── Mark processed ───────────────────────────────────────────────────────
    mark_processed(flag_path)
    log("Processed: %s" % flag_path)
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    log("qt_watcher starting. Watching shots/ and assets/ every %ds" % POLL_INTERVAL_SECONDS)

    try:
        tk, sg = get_sgtk()
    except Exception as exc:
        log("FATAL: could not bootstrap sgtk: %s" % exc)
        sys.exit(1)

    # flag path -> consecutive failure count (in-memory; see MAX_FLAG_FAILURES)
    failure_counts = {}
    poll_count = 0
    processed_since_heartbeat = 0

    while True:
        poll_count += 1
        try:
            flags, scan_errors = find_flags()

            # An incomplete scan is NOT the same as "nothing to do" - say so,
            # otherwise a share that has gone away looks exactly like an idle
            # watcher and flag pickup just appears intermittent.
            for err in scan_errors:
                log("WARNING: incomplete scan — %s" % err)

            if flags:
                log("Found %d flag(s)" % len(flags))

            for flag_path in flags:
                try:
                    handled = process_flag(tk, sg, flag_path)
                except Exception as exc:
                    log("ERROR processing %s: %s" % (flag_path, exc))
                    handled = False

                if handled:
                    failure_counts.pop(flag_path, None)
                    processed_since_heartbeat += 1
                    continue

                # Left in place for retry - but not indefinitely.
                failures = failure_counts.get(flag_path, 0) + 1
                failure_counts[flag_path] = failures
                log("Flag not completed (attempt %d of %d): %s"
                    % (failures, MAX_FLAG_FAILURES, flag_path))
                if failures >= MAX_FLAG_FAILURES:
                    quarantine_flag(flag_path)
                    failure_counts.pop(flag_path, None)

            # Drop counters for flags that are no longer present, so the cap
            # only ever applies to CONSECUTIVE failures of a live flag.
            still_present = set(flags)
            for gone in [p for p in failure_counts if p not in still_present]:
                failure_counts.pop(gone, None)

        except Exception as exc:
            log("ERROR in main loop: %s" % exc)

        if poll_count % HEARTBEAT_EVERY_N_POLLS == 0:
            log("Heartbeat: %d polls, %d flag(s) processed since last heartbeat, "
                "%d flag(s) awaiting retry"
                % (HEARTBEAT_EVERY_N_POLLS, processed_since_heartbeat,
                   len(failure_counts)))
            processed_since_heartbeat = 0

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()


