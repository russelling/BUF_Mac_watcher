"""
external_render.py

Bring a render made OUTSIDE the normal pipeline - an EXR sequence or a
QuickTime - into a shot, and into today's submission package.

Two routes, chosen per file by the user in external_render_dialog.py:

  VIA WATCHER   EXR sequences always; QTs when the user wants a slate and
                burn-ins added. The media is copied into the shot's
                render/work/{Shot}_{vendor}_{Step}_v###/ folder (the Toolkit
                template, so it sits exactly where a Nuke render would), and a
                .render_complete_*.json flag is written LAST. qt_watcher then
                bakes it into today's _staging package and writes the CSV row,
                exactly as for any other render. Nothing here encodes a QT -
                rule #1 for qt_watcher.

  AS-IS         QTs only, when they already carry their own slate/burn-ins.
                The QT is copied into the same shot folder for the record, then
                filed straight into today's package (prores/, h264/, dnx/) and
                recorded in the package CSV, using the same buf calls
                qt_watcher.process_flag() uses: buf.sg.resolve_output_paths,
                buf.deliver.companions, buf.manifest.record. No slate, no
                burn-ins, no colour. A non-ProRes source is transcoded to
                ProRes 422 HQ - a straight codec change, like buf.deliver's DNx.

The flag shape comes from flag_builder.build_flag_data(), so it cannot drift
from what RE-RUN QT writes; this module only adds the keys the bake already
documents for external media: color_pipe, source_colorspace, include_slate,
skip_color, movie_path (see qt_bake_oiio.bake_sequence()).

Qt-free on purpose, like flag_builder.py.
"""

import datetime
import getpass
import json
import os
import re
import shutil
import subprocess
import sys

import flag_builder as fb

MOVIE_EXTS = (".mov", ".mp4", ".m4v")
EXR_EXT = ".exr"

# frame number = the LAST run of digits before .exr, with a . or _ before it.
EXR_FRAME_RE = re.compile(r"^(?P<head>.*?[._])(?P<frame>\d+)(?P<tail>\.exr)$",
                          re.IGNORECASE)

# Every QT the watcher delivers is this size; an as-is QT that is not is
# delivered anyway, but the dialog says so.
DELIVERY_SIZE = (1920, 1080)

# Source colourspaces offered for EXRs: (label, flag value). None writes no
# key, so the bake applies its own shot default (aces_interchange = AP0) and
# logs that it did.
SOURCE_SPACES = (
    ("ACES2065-1 / AP0  (shot default)", None),
    ("ACEScg / AP1", "ACEScg"),
)

SIDE_CAR = "external_source.json"
SOURCE_TAG = "watcher_monitor_external"

BUF_PACKAGE_ROOT = os.path.join(fb.STORAGE_ROOT, "repo/pipeline/buffalo-pipeline")


class ExternalError(Exception):
    """Something the user has to fix; the message is shown as-is."""


# ---------------------------------------------------------------------------
# Detecting what was picked
# ---------------------------------------------------------------------------

def detect_source(path):
    """
    Describe a picked file.

    An EXR is taken as one frame of a sequence: every sibling with the same
    head/padding/tail is part of it. A movie is probed with ffprobe.

    Returns a dict:
        kind          "exr" | "movie"
        path          the picked file
        files         {frame: path} (exr)  /  [path] (movie)
        frame_first, frame_last, frame_count
        missing       frames absent inside first..last (exr)
        probe         ffprobe facts (movie): codec, width, height, timecode
    Raises ExternalError for anything unusable.
    """
    if not path or not os.path.isfile(path):
        raise ExternalError("Not a file:\n%s" % path)
    ext = os.path.splitext(path)[1].lower()

    if ext == EXR_EXT:
        return _detect_exr(path)
    if ext in MOVIE_EXTS:
        probe = probe_movie(path)
        count = probe.get("frames") or 0
        if count < 1:
            raise ExternalError("Could not count the frames in:\n%s" % path)
        return {
            "kind": "movie", "path": path, "files": [path],
            "frame_first": 1, "frame_last": count, "frame_count": count,
            "missing": [], "probe": probe,
        }
    raise ExternalError("Pick an EXR frame or a %s file." %
                        " / ".join(MOVIE_EXTS))


def _detect_exr(path):
    folder, name = os.path.split(path)
    m = EXR_FRAME_RE.match(name)
    if not m:
        # A single unnumbered EXR is a one-frame render.
        return {"kind": "exr", "path": path, "files": {1: path},
                "frame_first": 1, "frame_last": 1, "frame_count": 1,
                "missing": [], "probe": {}}
    head, pad, tail = m.group("head"), len(m.group("frame")), m.group("tail")
    files = {}
    for other in os.listdir(folder):
        if other.startswith("."):
            continue
        o = EXR_FRAME_RE.match(other)
        if (o and o.group("head") == head and len(o.group("frame")) == pad
                and o.group("tail").lower() == tail.lower()):
            files[int(o.group("frame"))] = os.path.join(folder, other)
    frames = sorted(files)
    first, last = frames[0], frames[-1]
    missing = [f for f in range(first, last + 1) if f not in files]
    return {"kind": "exr", "path": path, "files": files,
            "frame_first": first, "frame_last": last,
            "frame_count": len(frames), "missing": missing, "probe": {}}


def describe_source(src):
    """One line for the dialog."""
    if src["kind"] == "movie":
        p = src["probe"]
        return "QT  %s   %s  %sx%s   %d frames%s" % (
            os.path.basename(src["path"]), p.get("codec") or "?",
            p.get("width") or "?", p.get("height") or "?",
            src["frame_count"],
            ("   TC %s" % p["timecode"]) if p.get("timecode") else "")
    gap = ("   (!) %d missing" % len(src["missing"])) if src["missing"] else ""
    return "EXR  %s   %d-%d   %d frames%s" % (
        os.path.basename(src["path"]), src["frame_first"], src["frame_last"],
        src["frame_count"], gap)


# ---------------------------------------------------------------------------
# External tools
# ---------------------------------------------------------------------------

def _tool(name):
    """ffmpeg/ffprobe: buf's resolution first (same binaries as the watcher)."""
    try:
        from buf import settings
        return settings.tool(name)
    except Exception:
        pass
    for cand in ("/opt/homebrew/opt/ffmpeg-full/bin/" + name,
                 "/opt/homebrew/bin/" + name, shutil.which(name)):
        if cand and os.path.exists(cand):
            return cand
    raise ExternalError("%s not found on this machine (brew install "
                        "ffmpeg-full)." % name)


def probe_movie(path):
    """codec, width, height, frames, fps, timecode for a movie. Raises ExternalError."""
    import_buf()
    cmd = [_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
           "-count_packets",
           "-show_entries",
           "stream=codec_name,width,height,r_frame_rate,nb_read_packets"
           ":stream_tags=timecode:format_tags=timecode",
           "-of", "json", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        data = json.loads(out.stdout or "{}")
    except Exception as exc:
        raise ExternalError("ffprobe failed on %s:\n%s" % (path, exc))
    streams = data.get("streams") or []
    if not streams:
        raise ExternalError("No video stream in:\n%s" % path)
    s = streams[0]
    tc = ((s.get("tags") or {}).get("timecode")
          or ((data.get("format") or {}).get("tags") or {}).get("timecode"))
    try:
        frames = int(s.get("nb_read_packets") or 0)
    except ValueError:
        frames = 0
    return {"codec": s.get("codec_name"), "width": s.get("width"),
            "height": s.get("height"), "fps": s.get("r_frame_rate"),
            "frames": frames, "timecode": tc}


def import_buf():
    """Put the buf package on sys.path the way qt_watcher.py does."""
    if BUF_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, BUF_PACKAGE_ROOT)
    os.environ.setdefault("BUF_SHOW", "lucid_s3")


# ---------------------------------------------------------------------------
# Versions and destinations
# ---------------------------------------------------------------------------

def next_free_version(tk, fields):
    """
    One above the highest version on disk for this shot + step.

    Counts EXR renders (Toolkit's own scan) AND every version FOLDER in
    render/work by name, any vendor code - a folder holding only an external
    QT has no EXRs, so the template scan alone would offer its number again.
    """
    versions = [0]
    try:
        versions += [r["version"] for r in fb.find_renders(tk, fields)]
    except Exception:
        pass
    try:
        work = os.path.dirname(leaf_dir(tk, target_fields(fields, 1, "INH")))
        rx = re.compile(r"^%s_[A-Za-z0-9]+_%s(?:_.+)?_v(\d{3,})$"
                        % (re.escape(fields["Shot"]), re.escape(fields["Step"])))
        for name in os.listdir(work):
            m = rx.match(name)
            if m:
                versions.append(int(m.group(1)))
    except (OSError, KeyError, ExternalError):
        pass
    return max(versions) + 1


def target_fields(fields, version, vendor_code):
    f = dict(fields)
    f["version"] = int(version)
    f["vendor_code"] = vendor_code
    f.pop("output", None)          # external renders have no Nuke output name
    return f


def render_pattern(tk, fields, frame):
    """The %04d EXR pattern the template gives for this version."""
    path = tk.templates[fb.RENDER_TEMPLATE].apply_fields(
        dict(fields, SEQ=int(frame)))
    pattern = fb.exr_pattern_from_path(path, int(frame))
    if not pattern:
        raise ExternalError("Could not derive a frame pattern from %s" % path)
    return pattern


def leaf_dir(tk, fields):
    """render/work/{Shot}_{vendor}_{Step}_v###/ for these fields."""
    return os.path.dirname(render_pattern(tk, fields, 1001))


def leaf_name(tk, fields):
    return os.path.basename(leaf_dir(tk, fields))


def check_destination(folder):
    """Refuse to mix an external render into a version that already exists."""
    if os.path.isdir(folder) and any(not n.startswith(".")
                                     for n in os.listdir(folder)):
        raise ExternalError(
            "That version already has files on disk:\n%s\n\nPick a higher "
            "version number." % folder)


def renumber(src, first_frame):
    """[(source_frame, delivered_frame)] with the first frame moved to first_frame."""
    if src["kind"] != "exr":
        return []
    offset = int(first_frame) - src["frame_first"]
    return [(f, f + offset) for f in sorted(src["files"])]


# ---------------------------------------------------------------------------
# Copying
# ---------------------------------------------------------------------------

def _copy(src, dst):
    """copy2 to a temp name then rename, so nothing ever sees half a file."""
    tmp = dst + ".partial"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def copy_exrs(src, pattern, first_frame, progress=None):
    """Copy (and renumber) the frames into the shot. Returns delivered (first, last)."""
    folder = os.path.dirname(pattern)
    check_destination(folder)
    os.makedirs(folder, exist_ok=True)
    pairs = renumber(src, first_frame)
    for i, (sf, df) in enumerate(pairs):
        _copy(src["files"][sf], pattern % df)
        if progress and (i % 10 == 0 or i == len(pairs) - 1):
            progress("Copied %d / %d frames" % (i + 1, len(pairs)))
    return pairs[0][1], pairs[-1][1]


def copy_movie(src, folder, leaf):
    """Copy the QT into the shot's version folder. Returns the copy's path."""
    check_destination(folder)
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, leaf + os.path.splitext(src["path"])[1].lower())
    _copy(src["path"], dst)
    return dst


def write_sidecar(folder, src, choices):
    """Provenance beside the copied media: where it came from, who, what was chosen."""
    data = {
        "source_path": src["path"],
        "source_kind": src["kind"],
        "source_frames": [src["frame_first"], src["frame_last"]],
        "copied_by": getpass.getuser(),
        "copied_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "tool": SOURCE_TAG,
        "choices": choices,
    }
    with open(os.path.join(folder, SIDE_CAR), "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# The flag
# ---------------------------------------------------------------------------

def color_pipe_dict(log_convert, cdl, show_lut):
    return {"log_convert": bool(log_convert), "cdl": bool(cdl),
            "show_lut": bool(show_lut)}


def describe_colour(kind, color_pipe=None, source_space=None, as_is=False):
    """Plain-words list of what the QT will and will NOT get, for the confirm."""
    if kind == "movie":
        if as_is:
            return ["Delivered as-is: no slate, no burn-ins, no colour."]
        return ["Colour pipe: none - a QT is already display-referred.",
                "Slate / burn-ins: added by the watcher."]
    names = (("log_convert", "Convert %s -> LogC4" % (source_space or "AP0")),
             ("cdl", "Shot CDL"), ("show_lut", "LUT -> Rec.709"))
    on = [label for key, label in names if color_pipe.get(key)]
    off = [label for key, label in names if not color_pipe.get(key)]
    lines = ["Applied:  %s" % (", ".join(on) or "nothing")]
    if off:
        lines.append("IGNORED:  %s" % ", ".join(off))
    if not on:
        lines.append("All stages off: the EXR pixels are clamped to 0-1 and "
                     "encoded as they are (right for display-referred EXRs).")
    elif not color_pipe.get("log_convert") and color_pipe.get("show_lut"):
        lines.append("The LUT expects LogC4 input - only right if these EXRs "
                     "are already LogC4.")
    return lines


def build_flag(*, shot, task, fields, project, version, vendor_code,
               frame_first, frame_last, artist, user_id, submitted_for,
               description, cut_in, cut_out, kind, exr_pattern=None,
               movie_path=None, color_pipe=None, source_space=None,
               include_slate=True, anamorphic=None, start_timecode=None):
    """flag_builder's payload, plus the documented external-media keys."""
    render = {"version": int(version), "vendor_code": vendor_code,
              "output": None, "frame_first": frame_first,
              "frame_last": frame_last, "exr_path_pattern": exr_pattern or ""}
    data = fb.build_flag_data(
        shot=shot, task=task, step=fields.get("Step"), render=render,
        submitted_for=submitted_for, description=description,
        episode=fields.get("Episode"), scene=fields.get("Scene"),
        project=project, artist=artist, user_id=user_id,
        cut_in=cut_in, cut_out=cut_out, anamorphic=anamorphic)
    data["include_slate"] = bool(include_slate)
    data["source"] = SOURCE_TAG
    if kind == "movie":
        data["movie_path"] = movie_path
        data["skip_color"] = True
        # The passthrough bake does not derive TC from the frame number the
        # way the EXR bake does - it falls back to 00:00:00:00 - so state it.
        data["start_timecode"] = start_timecode
    else:
        data["color_pipe"] = dict(color_pipe)
        if source_space:
            data["source_colorspace"] = source_space
    return data


def validate(data):
    """fb.validate_flag(), except a movie flag carries movie_path, not EXRs."""
    problems = [p for p in fb.validate_flag(data)
                if not (data.get("movie_path") and p == "missing exr_path_pattern")]
    if data.get("movie_path") and not os.path.exists(data["movie_path"]):
        problems.append("movie_path does not exist")
    return problems


def frame_timecode(frame):
    """Frame-derived TC, from the bake's own helper (frame 1001 -> 00:00:41:17)."""
    return fb._bake_module().frames_to_tc(int(frame))


# ---------------------------------------------------------------------------
# As-is delivery (QTs that already carry their own slate / burn-ins)
# ---------------------------------------------------------------------------

def _is_prores(probe):
    return (probe.get("codec") or "").lower() == "prores"


def to_prores(src_path, dst, timecode=None):
    """Straight ProRes 422 HQ transcode - no scaling, no colour."""
    cmd = [_tool("ffmpeg"), "-y", "-i", src_path, "-c:v", "prores_ks",
           "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-an"]
    if timecode:
        cmd += ["-timecode", timecode]
    cmd.append(dst)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(dst):
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        raise ExternalError("ProRes transcode failed:\n%s" % tail)


def deliver_as_is(data, movie_copy, probe, sg, *, replace=False, progress=None):
    """
    File an as-is QT into today's package and record it in the CSV.

    Mirrors qt_watcher.process_flag() after its bake step, so a package
    holds the same three folders and the same CSV row either way.
    Returns {"prores", "csv", "delivery", "artist_note"}.
    """
    import_buf()
    from buf import flags as buf_flags
    from buf import sg as buf_sg
    from buf import manifest as buf_manifest
    from buf import deliver as buf_deliver
    from buf import submission as buf_submission

    say = progress or (lambda _m: None)
    payload = buf_flags.parse(data, validate_now=False)
    prores = buf_sg.resolve_output_paths(payload)[0]
    if os.path.exists(prores) and not replace:
        raise ExternalError("Already in today's package:\n%s" % prores)
    os.makedirs(os.path.dirname(prores), exist_ok=True)

    tmp = os.path.splitext(prores)[0] + ".partial.mov"
    if _is_prores(probe):
        say("Copying ProRes into the package…")
        shutil.copy2(movie_copy, tmp)
    else:
        say("Transcoding %s to ProRes 422 HQ…" % (probe.get("codec") or "source"))
        to_prores(movie_copy, tmp, timecode=probe.get("timecode"))
    os.replace(tmp, prores)

    # The h264 is made by the bake's own proxy writer, so its settings stay
    # in one place; buf.deliver then files it into h264/ and builds the DNx.
    say("Writing the h264 proxy…")
    try:
        fb._bake_module().write_review_proxy(
            data, prores, embed_tc=probe.get("timecode"))
    except Exception as exc:
        say("WARNING: h264 proxy failed: %s" % exc)
    say("Building the DNx…")
    delivery = buf_deliver.companions(payload, prores)

    try:
        artist, how = buf_sg.artist_name(sg, payload)
    except Exception as exc:
        artist, how = None, "lookup raised: %s" % exc
    csv_path = buf_manifest.record(payload, prores, artist=artist)

    # Same release rule as the watcher (only acts when buf builds dotted).
    package_dir = os.path.dirname(os.path.dirname(prores))
    if os.path.basename(package_dir).startswith(buf_submission.BUILDING_PREFIX):
        buf_submission.publish(package_dir)

    return {"prores": prores, "csv": str(csv_path), "delivery": delivery,
            "artist_note": artist or ("INH (%s)" % how)}


def current_package_name():
    """Today's open package, for the preview. Never raises."""
    try:
        import_buf()
        from buf import submission
        return submission.describe()
    except Exception as exc:
        return "(could not resolve: %s)" % exc
