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
import subprocess
import sys
import time

import sgtk


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config"
SHOTS_ROOT  = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots"
ASSETS_ROOT = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/assets"

NUKE_EXECUTABLE  = "/Applications/Nuke17.0v1/Nuke17.0.app/Contents/MacOS/Nuke17.0"
PYTHON3          = "/opt/homebrew/bin/python3"

SCRIPTS_DIR      = os.path.dirname(os.path.abspath(__file__))
NUKE_BAKE_SCRIPT = os.path.join(SCRIPTS_DIR, "qt_bake_slate_burnin.py")
OIIO_BAKE_SCRIPT = os.path.join(SCRIPTS_DIR, "qt_bake_oiio.py")

POLL_INTERVAL_SECONDS = 30

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

def find_flags():
    """
    Find all .render_complete_*.json flag files under shots/ and assets/.
    Uses recursive os.walk so depth doesn't matter.
    """
    flags = []
    for root_dir in [SHOTS_ROOT, ASSETS_ROOT]:
        if not os.path.exists(root_dir):
            continue
        for root, _dirs, files in os.walk(root_dir):
            for fname in files:
                if fname.startswith(".render_complete_") and fname.endswith(".json"):
                    flags.append(os.path.join(root, fname))
    return flags


# ---------------------------------------------------------------------------
# Output path resolution
# ---------------------------------------------------------------------------

def resolve_shot_output_paths(tk, data):
    """Resolve output paths for a shot render flag."""
    now = datetime.datetime.now()
    fields = {
        "Episode":     data["episode"],
        "Scene":       data["scene"],
        "Shot":        data["shot_code"],
        "Step":        data["step"],
        "version":     data["version"],
        "output":      data.get("output", data.get("nuke_output", "main")),
        "YYYY":        now.year,
        "MM":          now.month,
        "DD":          now.day,
    }
    shot_movie_path      = tk.templates["ep_nuke_shot_render_movie"].apply_fields(fields)
    editorial_movie_path = tk.templates["editorial_to_editorial_movie"].apply_fields(fields)
    return shot_movie_path, editorial_movie_path


def resolve_asset_output_paths(tk, data):
    """Resolve output paths for an asset turntable flag."""
    now = datetime.datetime.now()
    asset_name = data["entity_name"]
    asset_type = data.get("asset_type", "Asset")
    version    = data["version"]

    fields = {
        "Asset":        asset_name,
        "sg_asset_type": asset_type,
        "version":      version,
    }
    asset_movie_path = tk.templates["unreal_asset_turntable_movie"].apply_fields(fields)

    # Also drop a copy to the dated editorial folder
    editorial_fields = {
        "YYYY": now.year,
        "MM":   now.month,
        "DD":   now.day,
        # Reuse editorial template if it exists, otherwise build path manually
    }
    editorial_dir = os.path.join(
        "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/editorial",
        "to_editorial",
        "%04d_%02d_%02d" % (now.year, now.month, now.day),
    )
    editorial_movie_path = os.path.join(
        editorial_dir,
        "%s_turntable_v%03d.mov" % (asset_name, version),
    )
    return asset_movie_path, editorial_movie_path


# ---------------------------------------------------------------------------
# Bake invocation
# ---------------------------------------------------------------------------

def run_nuke_bake(flag_path, output_paths):
    """Invoke Nuke batch bake for shot renders."""
    cmd = [NUKE_EXECUTABLE, "-t", NUKE_BAKE_SCRIPT, flag_path] + list(output_paths)
    log("Running Nuke bake: %s" % " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        log(result.stdout)
    if result.returncode != 0:
        log("ERROR in Nuke bake: %s" % result.stderr)
        return False
    return True


def run_oiio_bake(flag_path, output_paths):
    """Invoke OIIO+FFmpeg bake for asset turntable renders."""
    cmd = [PYTHON3, OIIO_BAKE_SCRIPT, flag_path] + list(output_paths)
    log("Running OIIO bake: %s" % " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        log(result.stdout)
    if result.returncode != 0:
        log("ERROR in OIIO bake: %s" % result.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# ShotGrid upload
# ---------------------------------------------------------------------------

def upload_version(sg, data, movie_path):
    """Create a Version in ShotGrid and upload the QT."""
    is_asset = data.get("type") == "asset_turntable"

    if is_asset:
        entity_name = data.get("entity_name", "")
        step        = data.get("step", "")
        version_num = data.get("version", 1)
        version_code = "%s_%s_turntable_v%03d" % (entity_name, step, version_num)
    else:
        version_code = "%s_%s_v%03d" % (
            data.get("shot_code", ""),
            data.get("step", ""),
            data.get("version", 1),
        )

    version_data = {
        "project":           {"type": "Project", "id": data["project_id"]},
        "code":              version_code,
        "entity":            {"type": data.get("entity_type", "Shot"),
                              "id":   data["entity_id"]},
        "description":       data.get("description", ""),
        "sg_submitted_for":  data.get("submitted_for", ""),
        "sg_path_to_movie":  movie_path,
    }

    if data.get("task_id"):
        version_data["sg_task"] = {"type": "Task", "id": data["task_id"]}

    # Remove None / empty values
    version_data = {k: v for k, v in version_data.items() if v not in (None, "")}

    try:
        version = sg.create("Version", version_data)
        sg.upload(
            "Version",
            version["id"],
            movie_path,
            field_name="sg_uploaded_movie",
        )
        log("Uploaded Version %s (%s)" % (version["id"], version_code))
        return version
    except Exception as exc:
        log("ERROR uploading version: %s" % exc)
        return None


# ---------------------------------------------------------------------------
# Flag cleanup
# ---------------------------------------------------------------------------

def mark_processed(flag_path):
    if PROCESSED_ACTION == "delete":
        os.remove(flag_path)
    else:
        new_path = flag_path.replace(".render_complete_", ".processed_")
        os.rename(flag_path, new_path)


# ---------------------------------------------------------------------------
# Per-flag processing
# ---------------------------------------------------------------------------

def process_flag(tk, sg, flag_path):
    log("Found flag: %s" % flag_path)

    with open(flag_path, "r") as f:
        data = json.load(f)

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
        return

    output_paths = [primary_path, editorial_path]

    # ── Run bake ─────────────────────────────────────────────────────────────
    if is_asset:
        success = run_oiio_bake(flag_path, output_paths)
    else:
        success = run_nuke_bake(flag_path, output_paths)

    if not success:
        log("Bake failed for: %s — flag left in place for retry" % flag_path)
        return

    # ── Upload to ShotGrid ───────────────────────────────────────────────────
    if os.path.exists(primary_path):
        upload_version(sg, data, primary_path)
    else:
        log("WARNING: expected output not found: %s" % primary_path)

    # ── Mark processed ───────────────────────────────────────────────────────
    mark_processed(flag_path)
    log("Processed: %s" % flag_path)


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

    while True:
        try:
            flags = find_flags()
            if flags:
                log("Found %d flag(s)" % len(flags))
            for flag_path in flags:
                try:
                    process_flag(tk, sg, flag_path)
                except Exception as exc:
                    log("ERROR processing %s: %s" % (flag_path, exc))
        except Exception as exc:
            log("ERROR in main loop: %s" % exc)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

