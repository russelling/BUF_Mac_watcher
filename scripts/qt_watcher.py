"""
qt_watcher.py

Polling daemon intended to run on the Mac Studio via launchd.

  1. Walks the shots/ tree looking for `.render_complete_*.json` flag files
     (written by render_complete_callback.py on artist machines).

  2. For each flag found:
       - Resolves output paths:
           a) shot's review folder (ep_nuke_shot_render_movie)
           b) dated editorial drop (editorial_to_editorial_movie)
       - Invokes `nuke -t qt_bake_slate_burnin.py <flag> <out_a> <out_b>`
       - Uploads the resulting QT to ShotGrid as a Version, using
         `description` and `submitted_for` from the flag data
       - Renames the flag file to `.processed_*.json` so it isn't
         reprocessed (or deletes it - see PROCESSED_ACTION)

Run as a launchd service - see com.buffalovfx.qtwatcher.plist
"""

import os
import json
import glob
import time
import datetime
import subprocess

import sgtk
from sgtk import authentication


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config"
SHOTS_ROOT = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots"
NUKE_EXECUTABLE = "/Applications/Nuke17.0v1/Nuke17.0.app/Contents/MacOS/Nuke17.0"
BAKE_SCRIPT = os.path.join(os.path.dirname(__file__), "qt_bake_slate_burnin.py")

POLL_INTERVAL_SECONDS = 30

# "delete" or "rename" - what to do with flag files after processing
PROCESSED_ACTION = "rename"


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
    pattern = os.path.join(SHOTS_ROOT, "*", "*", "*", "*", "*", "*",
                            ".render_complete_*.json")
    # Broad glob - depth may vary, so also try a recursive walk fallback
    flags = glob.glob(pattern)
    if not flags:
        flags = []
        for root, _dirs, files in os.walk(SHOTS_ROOT):
            for f in files:
                if f.startswith(".render_complete_") and f.endswith(".json"):
                    flags.append(os.path.join(root, f))
    return flags


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_output_paths(tk, data):
    fields = {
        "Episode": data["episode"],
        "Scene": data["scene"],
        "Shot": data["shot_code"],
        "Step": data["step"],
        "version": data["version"],
        "nuke.output": data["nuke_output"],
    }

    now = datetime.datetime.now()
    editorial_fields = dict(fields)
    editorial_fields["YYYY"] = now.year
    editorial_fields["MM"] = now.month
    editorial_fields["DD"] = now.day

    shot_movie_template = tk.templates["ep_nuke_shot_render_movie"]
    editorial_movie_template = tk.templates["editorial_to_editorial_movie"]

    shot_movie_path = shot_movie_template.apply_fields(fields)
    editorial_movie_path = editorial_movie_template.apply_fields(editorial_fields)

    return shot_movie_path, editorial_movie_path


# ---------------------------------------------------------------------------
# Nuke bake invocation
# ---------------------------------------------------------------------------

def run_bake(flag_path, output_paths):
    cmd = [NUKE_EXECUTABLE, "-t", BAKE_SCRIPT, flag_path] + list(output_paths)
    print("[qt_watcher] Running: %s" % " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print("[qt_watcher] ERROR running bake script:")
        print(result.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# ShotGrid upload
# ---------------------------------------------------------------------------

def upload_version(sg, data, movie_path):
    version_data = {
        "project": {"type": "Project", "id": data["project_id"]},
        "code": "%s_%s_v%03d" % (data["shot_code"], data["step"], data["version"]),
        "entity": {"type": data["entity_type"], "id": data["entity_id"]},
        "sg_task": {"type": "Task", "id": data["task_id"]} if data.get("task_id") else None,
        "description": data.get("description"),
        "sg_submitted_for": data.get("submitted_for"),
        "sg_path_to_movie": movie_path,
        "created_by": None,  # left to default (script user)
    }
    # Remove None values
    version_data = {k: v for k, v in version_data.items() if v is not None}

    try:
        version = sg.create("Version", version_data)
        sg.upload(
            "Version",
            version["id"],
            movie_path,
            field_name="sg_uploaded_movie",
        )
        print("[qt_watcher] Uploaded Version %s for %s" % (version["id"], data["shot_code"]))
        return version
    except Exception as exc:
        print("[qt_watcher] ERROR uploading version: %s" % exc)
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
# Main loop
# ---------------------------------------------------------------------------

def process_flag(tk, sg, flag_path):
    print("[qt_watcher] Found flag: %s" % flag_path)

    with open(flag_path, "r") as f:
        data = json.load(f)

    try:
        shot_movie_path, editorial_movie_path = resolve_output_paths(tk, data)
    except Exception as exc:
        print("[qt_watcher] ERROR resolving output paths: %s" % exc)
        return

    output_paths = [shot_movie_path, editorial_movie_path]

    if not run_bake(flag_path, output_paths):
        return

    # Upload the shot-folder copy as the review version
    if os.path.exists(shot_movie_path):
        upload_version(sg, data, shot_movie_path)
    else:
        print("[qt_watcher] WARNING: expected output not found: %s" % shot_movie_path)

    mark_processed(flag_path)


def main():
    tk, sg = get_sgtk()
    print("[qt_watcher] Started. Watching %s every %ds" % (SHOTS_ROOT, POLL_INTERVAL_SECONDS))

    while True:
        for flag_path in find_flags():
            try:
                process_flag(tk, sg, flag_path)
            except Exception as exc:
                print("[qt_watcher] ERROR processing %s: %s" % (flag_path, exc))
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
