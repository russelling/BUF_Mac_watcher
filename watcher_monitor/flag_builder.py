"""
flag_builder.py

Builds a .render_complete_*.json flag for a shot render that has already been
rendered to disk, so qt_watcher will bake and upload it again.

The flag shape is copied from FlowTrackingConfig's
hooks/render_complete_callback.py - that hook is the authoritative writer and
this must not drift from it. Note project_id and entity_id are read with
bracket access in qt_watcher.upload_version(), so a missing one raises rather
than degrading; they are required, not optional.

Paths are resolved through the Toolkit TEMPLATES rather than being assembled
by hand, so this follows the config instead of duplicating its layout.

Qt-free on purpose: the pure helpers below are unit tested without a display,
and the sgtk-backed ones are thin enough to eyeball.
"""

import datetime
import json
import os

# Overridable so this matches install_qt_watcher.sh, which takes the same two
# variables - one place to point everything at a different config.
STORAGE_ROOT = os.environ.get(
    "STORAGE_ROOT",
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx",
)
CONFIG_ROOT = os.environ.get(
    "CONFIG_ROOT",
    os.path.join(STORAGE_ROOT, "repo/pipeline/config/flow/current"),
)

RENDER_TEMPLATE = "ep_nuke_shot_render_work"
FLAG_TEMPLATE = "ep_nuke_shot_render_flag"

FALLBACK_SUBMITTED_FOR = ["Internal Review", "Supervisor", "Editorial", "Client"]

# In-house is the norm; vendor work overrides it per render, which is exactly
# what {vendor_code} in the render path already records.
DEFAULT_ARTIST = "INH"
DEFAULT_VENDOR = "INH"


# ---------------------------------------------------------------------------
# Pure helpers (no sgtk, no Qt - covered by tests)
# ---------------------------------------------------------------------------

def exr_pattern_from_path(path, seq):
    """
    Turn one rendered EXR path into the %04d pattern the bake expects.

    Deliberately derived by substituting the frame number found in THIS path
    rather than re-applying the template with a FORMAT: spec - the round trip
    through the template is one more place for an optional token to change the
    result, and the file on disk is ground truth.

    Replaces the LAST occurrence, since a shot code can itself contain digits.
    Returns None if the frame number is not in the name.
    """
    if not path or seq is None:
        return None
    token = ".%04d." % int(seq)
    idx = path.rfind(token)
    if idx == -1:
        return None
    return path[:idx] + ".%04d." + path[idx + len(token):]


def group_renders(entries):
    """
    Collapse per-frame EXR records into one entry per rendered version.

    entries: iterable of (path, fields) as returned by Toolkit.
    Returns a list of dicts, newest version first:
        version, output, vendor_code, frame_first, frame_last,
        frame_count, exr_path_pattern
    """
    groups = {}
    for path, fields in entries:
        version = fields.get("version")
        if version is None:
            continue
        # NOTE: the template key is written {nuke.output} in templates.yml but
        # carries `alias: output`, and Toolkit names fields by the ALIAS - so
        # get_fields()/apply_fields() speak "output", never "nuke.output".
        key = (version, fields.get("output"), fields.get("vendor_code"))
        seq = fields.get("SEQ")
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            continue
        g = groups.setdefault(key, {
            "version": version,
            "output": fields.get("output"),
            "vendor_code": fields.get("vendor_code"),
            "frames": [],
            "sample_path": path,
            "sample_seq": seq,
        })
        g["frames"].append(seq)
        if seq < g["sample_seq"]:
            g["sample_path"], g["sample_seq"] = path, seq

    out = []
    for g in groups.values():
        frames = sorted(g["frames"])
        out.append({
            "version": g["version"],
            "output": g["output"],
            "vendor_code": g["vendor_code"],
            "frame_first": frames[0],
            "frame_last": frames[-1],
            "frame_count": len(frames),
            "exr_path_pattern": exr_pattern_from_path(g["sample_path"],
                                                      g["sample_seq"]),
        })
    out.sort(key=lambda r: (r["version"], r["output"] or ""), reverse=True)
    return out


def describe_render(render):
    """One-line label for a render, for the picker."""
    bits = ["v%03d" % render["version"]]
    if render.get("output"):
        bits.append(render["output"])
    if render.get("vendor_code"):
        bits.append(render["vendor_code"])
    span = "%d-%d" % (render["frame_first"], render["frame_last"])
    expected = render["frame_last"] - render["frame_first"] + 1
    gap = "" if render["frame_count"] == expected else \
          "  (!) %d of %d frames" % (render["frame_count"], expected)
    return "%s   %s   %d frames%s" % ("  ".join(bits), span,
                                      render["frame_count"], gap)


def build_flag_data(shot, task, step, render, submitted_for, description,
                    episode, scene, project=None, artist=DEFAULT_ARTIST,
                    user_id=None, cut_in=None, cut_out=None):
    """
    Assemble the flag payload.

    Key-for-key the shape render_complete_callback.py writes, plus
    "type": "shot" (which qt_watcher already defaults to) and "user_id"
    (which upload_version() already honours). Nothing beyond that: a key no
    consumer reads is a key that will drift.

    start_timecode is left None on purpose - qt_bake_oiio.resolve_start_timecode()
    reads the real timecode out of the first EXR when the flag carries none,
    which beats anything that could be guessed here.
    """
    return {
        "type": "shot",
        "project_id": (project or {}).get("id"),
        "project_name": (project or {}).get("name"),
        "entity_type": "Shot",
        "entity_id": shot["id"],
        "shot_code": shot["code"],
        "episode": episode,
        "scene": scene,
        "step": step,
        "vendor_code": render.get("vendor_code") or DEFAULT_VENDOR,
        "version": render["version"],
        "output": render.get("output"),
        "artist": artist or DEFAULT_ARTIST,
        "user_id": user_id,
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "frame_first": render["frame_first"],
        "frame_last": render["frame_last"],
        "start_timecode": None,
        "cut_in": cut_in,
        "cut_out": cut_out,
        "exr_path_pattern": render["exr_path_pattern"],
        "submitted_for": submitted_for or None,
        "description": description or "Re-run from QT Watcher monitor.",
        "task_id": (task or {}).get("id"),
    }


def validate_flag(data):
    """Return a list of problems that would break the bake. Empty == good."""
    problems = []
    for key in ("project_id", "entity_id", "shot_code", "step", "version"):
        if data.get(key) in (None, ""):
            problems.append("missing %s" % key)
    if not data.get("exr_path_pattern"):
        problems.append("missing exr_path_pattern")
    if data.get("frame_first") is None or data.get("frame_last") is None:
        problems.append("missing frame range")
    elif data["frame_first"] > data["frame_last"]:
        problems.append("frame_first is after frame_last")
    return problems


# ---------------------------------------------------------------------------
# Colour sources (CDL + LUT), answered by the bake itself
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts"
)


def _bake_module():
    """
    Import qt_bake_oiio.

    The point of this whole section is that the CDL/LUT indicator asks the
    BAKE where it will look, rather than reimplementing the lookup here. A
    second copy of that logic would drift the first time the naming
    convention moves (it already has once, to versioned CDLs) and would then
    show a green light for a file the bake never picks up.

    qt_bake_oiio imports nothing but the standard library and does no work at
    import time, so this is cheap and safe.
    """
    import sys
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    import qt_bake_oiio
    return qt_bake_oiio


def color_probe_data(fields, shot_code=None):
    """Minimal flag-shaped dict for the lookups - they read only these keys."""
    return {
        "type": "shot",
        "shot_code": shot_code or fields.get("Shot"),
        "episode": fields.get("Episode"),
        "scene": fields.get("Scene"),
        "sequence": fields.get("Sequence"),
    }


def color_sources(data):
    """
    What the bake will actually pick up for this shot.

    Returns {"plates_dir", "cdl": {...}, "lut": {...}, "log"} where each of
    cdl/lut is {"status", "path", "detail"} and status is one of:

        "ok"      a per-shot file was found and will be applied
        "warn"    found, but the bake had to choose between candidates
        "show"    (LUT only) no per-shot LUT; the global show LUT applies
        "none"    nothing found - CDL: bakes UNGRADED;
                                  LUT: generic display transform, off-look
        "unknown" the lookup could not run (bake module missing, etc.)

    Never raises: an indicator that throws is worse than one that says it
    does not know.
    """
    import contextlib
    import io

    result = {
        "plates_dir": None,
        "cdl": {"status": "unknown", "path": None, "detail": ""},
        "lut": {"status": "unknown", "path": None, "detail": ""},
        "log": "",
    }

    try:
        bake = _bake_module()
    except Exception as exc:
        detail = "could not import qt_bake_oiio: %s" % exc
        result["cdl"]["detail"] = detail
        result["lut"]["detail"] = detail
        return result

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            plates_dir = bake.shot_plates_dir(data)
            cdl_path = bake.find_shot_cdl(plates_dir, data.get("shot_code", ""))
            lut_path = bake.resolve_lut_path(data)
            show_lut = bake.SHOW_LUT_PATH
            show_lut_exists = os.path.exists(show_lut)
    except Exception as exc:
        detail = "lookup failed: %s" % exc
        result["cdl"]["detail"] = detail
        result["lut"]["detail"] = detail
        result["log"] = buf.getvalue()
        return result

    result["plates_dir"] = plates_dir
    result["log"] = buf.getvalue().strip()

    if not plates_dir:
        missing = "no plates folder resolves from this context"
    elif not os.path.isdir(plates_dir):
        missing = "no plates folder at %s" % plates_dir
    else:
        missing = "nothing in %s" % plates_dir

    # The bake logs when it had to CHOOSE between several candidates - more
    # than one versioned CDL, more than one LUT in plates/. It still bakes,
    # so this is not a failure, but "it found something" is the wrong thing
    # to show when what it found was a guess. Surface the ambiguity.
    log_lines = result["log"].splitlines()
    cdl_ambiguous = any("CDL: multiple candidates" in ln for ln in log_lines)
    lut_ambiguous = any("LUT: WARNING" in ln for ln in log_lines)

    if cdl_path:
        result["cdl"] = {
            "status": "warn" if cdl_ambiguous else "ok",
            "path": cdl_path,
            "detail": os.path.basename(cdl_path) + (
                " — several CDLs in plates/, this is the highest version"
                if cdl_ambiguous else ""),
        }
    else:
        result["cdl"] = {"status": "none", "path": None,
                         "detail": "%s — bakes UNGRADED" % missing}

    if lut_path and lut_ambiguous:
        result["lut"] = {
            "status": "warn", "path": lut_path,
            "detail": "%s — several LUTs in plates/, this one wins"
                      % os.path.basename(lut_path),
        }
    elif lut_path:
        result["lut"] = {"status": "ok", "path": lut_path,
                         "detail": "%s (per-shot)" % os.path.basename(lut_path)}
    elif show_lut_exists:
        result["lut"] = {"status": "show", "path": show_lut,
                         "detail": "%s (show LUT)" % os.path.basename(show_lut)}
    else:
        result["lut"] = {
            "status": "none", "path": None,
            "detail": "no per-shot LUT and the show LUT is missing — generic "
                      "display transform, look will differ",
        }

    return result


# ---------------------------------------------------------------------------
# sgtk-backed
# ---------------------------------------------------------------------------

def get_tk():
    """Bootstrap sgtk against the live pipeline config."""
    import sgtk
    return sgtk.sgtk_from_path(CONFIG_ROOT)


def project_info(tk):
    """{'id', 'name'} for the config's project - the display name, not the
    disk name, since that is what the flag's project_name records."""
    pid = tk.pipeline_configuration.get_project_id()
    name = None
    try:
        proj = tk.shotgun.find_one("Project", [["id", "is", pid]], ["name"])
        if proj:
            name = proj.get("name")
    except Exception:
        pass
    return {"id": pid, "name": name}


def list_shots(tk):
    """Shots in the project, with the fields needed to resolve folders."""
    sg = tk.shotgun
    project_id = tk.pipeline_configuration.get_project_id()
    shots = sg.find(
        "Shot",
        [["project", "is", {"type": "Project", "id": project_id}]],
        ["id", "code", "sg_scene", "sg_sequence"],
    )
    return sorted(shots, key=lambda s: s.get("code") or "")


def tasks_for_shot(tk, shot_id):
    """Tasks on a shot, each with its step short_name (the {Step} token)."""
    sg = tk.shotgun
    tasks = sg.find("Task", [["entity", "is", {"type": "Shot", "id": shot_id}]],
                    ["id", "content", "step"])
    out = []
    for t in tasks:
        step = t.get("step") or {}
        short = None
        if step.get("id"):
            full = sg.find_one("Step", [["id", "is", step["id"]]],
                               ["short_name", "code"])
            if full:
                short = full.get("short_name") or full.get("code")
        out.append({
            "id": t["id"],
            "content": t.get("content"),
            "step_name": step.get("name"),
            "step": short,
        })
    return out


def context_fields(tk, task_id, template_name=RENDER_TEMPLATE):
    """
    Template fields for a Task, straight from Toolkit's own resolution.

    This is the same call Workfiles2 makes, and it leans on the path cache -
    if folders were never registered for the Task, Step (and sometimes more)
    comes back missing. Use resolve_fields() rather than this directly.
    """
    ctx = tk.context_from_entity("Task", task_id)
    return ctx.as_template_fields(tk.templates[template_name])


def fields_from_shot_code(code):
    """
    Episode and Scene inferred from a shot code, e.g.

        301_001_0010  ->  Episode "301", Scene "301_001"

    matching shots/{Episode}/{Scene}/{Shot} on disk. Last resort only - used
    when Toolkit's own resolution came back without them.
    """
    parts = (code or "").split("_")
    if len(parts) < 3:
        return {}
    return {"Episode": parts[0], "Scene": "%s_%s" % (parts[0], parts[1])}


def resolve_fields(tk, shot, task, template_name=RENDER_TEMPLATE):
    """
    Template fields for this shot + task, path cache or no path cache.

    Toolkit's own resolution is tried first and trusted where it answers.
    What it will NOT answer is {Step}: step folders carry
    `create_with_parent: false`, so `tank Shot <id> folders` never creates
    them and nothing registers them until someone runs
    `tank Task <id> folders`. Step is a property of the Task regardless, and
    we already looked the Step entity up - so take it from there instead of
    demanding the cache be perfect.

    That matters because the renders themselves are on disk either way (Nuke
    wrote them), and paths_from_template() globs the disk rather than reading
    the cache. Nothing here needs the cache to be correct; it only needs the
    right field values.
    """
    fields = {}
    try:
        fields = dict(context_fields(tk, task["id"], template_name))
    except Exception:
        fields = {}

    if task.get("step"):
        fields["Step"] = task["step"]
    if shot.get("code"):
        fields.setdefault("Shot", shot["code"])
    for key, value in fields_from_shot_code(shot.get("code")).items():
        if not fields.get(key):
            fields[key] = value
    return fields


def find_renders(tk, fields):
    """Rendered EXR versions on disk for these context fields, newest first."""
    tmpl = tk.templates[RENDER_TEMPLATE]
    paths = tk.paths_from_template(
        tmpl, fields,
        skip_keys=["SEQ", "version", "vendor_code", "output"],
    )
    entries = []
    for p in paths:
        try:
            entries.append((p, tmpl.get_fields(p)))
        except Exception:
            continue
    return group_renders(entries)


def flag_path_for(tk, fields, render):
    """Where the flag must be written, per the ep_nuke_shot_render_flag template."""
    tmpl = tk.templates[FLAG_TEMPLATE]
    f = dict(fields)
    f["version"] = render["version"]
    if render.get("vendor_code"):
        f["vendor_code"] = render["vendor_code"]
    if render.get("output"):
        f["output"] = render["output"]
    return tmpl.apply_fields(f)


def submitted_for_options(tk):
    """Valid values for Version.sg_submitted_for, or a sane fallback."""
    try:
        schema = tk.shotgun.schema_field_read("Version", "sg_submitted_for")
        props = schema.get("sg_submitted_for", {}).get("properties", {})
        vals = props.get("valid_values", {}).get("value")
        if vals:
            return list(vals)
    except Exception:
        pass
    return list(FALLBACK_SUBMITTED_FOR)


def current_user_id(tk):
    """HumanUser id for Version.user, or None."""
    try:
        import sgtk
        user = sgtk.util.get_current_user(tk)
        return user.get("id") if user else None
    except Exception:
        return None


def human_user_id(tk, name):
    """
    HumanUser id for an artist name, or None.

    Tried against login, name and firstname so "INH" resolves whichever way
    that account was created. None is a fine answer: upload_version() simply
    leaves Version.user unset, which is better than pointing it at the wrong
    person.
    """
    if not name:
        return None
    for field in ("login", "name", "firstname"):
        try:
            hit = tk.shotgun.find_one("HumanUser", [[field, "is", name]], ["id"])
        except Exception:
            return None
        if hit:
            return hit["id"]
    return None


def cut_range(tk, shot_id):
    """(cut_in, cut_out) from the Shot, or (None, None)."""
    try:
        shot = tk.shotgun.find_one("Shot", [["id", "is", shot_id]],
                                   ["sg_cut_in", "sg_cut_out"])
    except Exception:
        return None, None
    if not shot:
        return None, None
    return shot.get("sg_cut_in"), shot.get("sg_cut_out")


def write_flag(path, data, overwrite=False):
    """
    Write the flag. Returns (ok, message).

    Written to a temp name and renamed into place: qt_watcher polls every 30s
    and would otherwise be able to read a half-written file. The watcher now
    tolerates that, but an atomic rename means it never has to.
    """
    if os.path.exists(path) and not overwrite:
        return False, "A flag already exists:\n%s" % path
    folder = os.path.dirname(path)
    try:
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        return False, "Could not write flag:\n%s\n\n%s" % (path, exc)
    return True, path


def existing_processed_flag(flag_path):
    """The .processed_ twin of a flag path, if the render was baked before."""
    folder, base = os.path.split(flag_path)
    if not base.startswith(".render_complete_"):
        return None
    processed = os.path.join(
        folder, base.replace(".render_complete_", ".processed_", 1))
    return processed if os.path.exists(processed) else None
