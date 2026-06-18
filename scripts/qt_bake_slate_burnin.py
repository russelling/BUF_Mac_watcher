"""
qt_bake_slate_burnin.py

Run via: nuke -t qt_bake_slate_burnin.py <flag_json_path> <output_path_1> [<output_path_2> ...]

Handles both shot renders and asset turntable renders.

Reads an EXR sequence (raw ACEScg) described by the render-complete flag JSON,
applies the full output color chain:

    ACEScg -> LogC4 -> CDL (per-shot, if available) -> Show LUT -> Rec.709

Builds:
  - A slate frame (prepended at first_frame - 1) with context-aware fields:

      Shot context:
          Show logo, Episode / Scene / Shot, Step, Version, Artist, Date,
          Frame Range, Start TC, Submitted For, Description

      Asset context:
          Show logo, Asset Type / Asset, Step, Version, Artist, Date,
          Submitted For, Description

  - Burn-ins on every frame:

      Shot:
          upper left   : "In House - {artist}"
          upper right  : date
          lower left   : "{shot}_{step}_v{version}"
          bottom center: frame counter (white = edit range, yellow = handles)
          bottom right : timecode

      Asset (turntable):
          upper left   : "{asset_type} - {asset_name}"
          upper right  : date
          lower left   : "{asset}_{step}_v{version}"
          bottom center: frame counter (always white)
          bottom right : timecode

Writes ProRes QT to every output path supplied on the command line.

FLAG SCHEMA (2026-06-17): consumes the render-complete flag written by
render_complete_callback.py. Key fields used here:
    frame_first, frame_last   - actual rendered frame numbers (ints)
    start_timecode            - embedded source TC at frame_first, "HH:MM:SS:FF"
                                or null if the render carried no embedded TC
    exr_path_pattern          - macOS-resolved EXR pattern with %04d
    cut_in, cut_out           - edit range for handle colouring (may be null)
    shot_code, episode, scene, step, version, artist, date,
    submitted_for, description
"""

import json
import os
import sys

import nuke


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SHOW_LUT_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/color/luts/"
    "ARRILogC4_SEV_S3_V3_digital_R709.cube"
)
LOGO_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/GLOBALS/logo/teardrop.png"
)

OCIO_ACES_WORKING = "ACES - ACEScg"
OCIO_LOGC4        = "Input - ARRI - Curve - LogC4 - EI800"

FPS = 24


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def p(path):
    """Normalise path separators for Nuke."""
    return path.replace("\\", "/")


def is_shot_context(data):
    """Return True if flag JSON is from a shot render."""
    return data.get("type", "shot") != "asset_turntable"


def get_frame_range(data):
    """
    Return (first, last) as ints from the flag.

    The current flag schema stores these as two scalar fields,
    frame_first / frame_last (the ACTUAL rendered frame numbers). Older
    flags used a 'frame_range' [first, last] list - support both so a
    stale flag doesn't crash the bake.
    """
    if data.get("frame_first") is not None and data.get("frame_last") is not None:
        return int(data["frame_first"]), int(data["frame_last"])
    fr = data.get("frame_range")
    if fr and len(fr) >= 2:
        return int(fr[0]), int(fr[1])
    # Last-resort fallback - should not happen for a valid render flag.
    nuke.tprint("[qt_bake] WARNING: no frame range in flag; defaulting to 1-1")
    return 1, 1


# ---------------------------------------------------------------------------
# Color bake
# ---------------------------------------------------------------------------

def build_color_bake(read_node, data):
    """ACEScg -> LogC4 -> CDL (optional) -> Show LUT -> Rec.709"""

    cs1 = nuke.createNode("OCIOColorSpace", inpanel=False)
    cs1.setInput(0, read_node)
    cs1["in_colorspace"].setValue(OCIO_ACES_WORKING)
    cs1["out_colorspace"].setValue(OCIO_LOGC4)
    cs1["label"].setValue("ACEScg -> LogC4")

    last = cs1

    # CDL: shots only — look for a per-shot .cc file
    if is_shot_context(data):
        shot    = data.get("shot_code", "")
        episode = str(data.get("episode", ""))
        scene   = str(data.get("scene", ""))
        cdl_path = os.path.join(
            "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots",
            episode, scene, shot, "plates", "%s.cc" % shot,
        )
        if os.path.exists(cdl_path):
            cdl = nuke.createNode("OCIOFileTransform", inpanel=False)
            cdl.setInput(0, last)
            cdl["file"].setValue(p(cdl_path))
            cdl["direction"].setValue("forward")
            cdl["interpolation"].setValue("linear")
            cdl["label"].setValue("Shot CDL")
            last = cdl

    # Show LUT
    if os.path.exists(SHOW_LUT_PATH):
        lut = nuke.createNode("OCIOFileTransform", inpanel=False)
        lut.setInput(0, last)
        lut["file"].setValue(p(SHOW_LUT_PATH))
        lut["direction"].setValue("forward")
        lut["interpolation"].setValue("tetrahedral")
        lut["label"].setValue("Show LUT -> Rec.709")
        last = lut

    return last


# ---------------------------------------------------------------------------
# Timecode
# ---------------------------------------------------------------------------

def build_timecode(parent, data, first_frame):
    """
    Attach source timecode to the stream so the burn-in can display it.

    Priority:
      1. start_timecode from the flag (authoritative - captured at submission
         from the EXR's embedded TC). Anchored so that this TC corresponds to
         frame_first, then counts forward.
      2. If start_timecode is null/empty, fall back to whatever embedded TC
         the EXRs themselves carry (input/timecode), read straight through.
         May be empty if the render had no embedded TC.

    Returns the node whose 'input/timecode' metadata the burn-in should read.
    """
    start_tc = data.get("start_timecode")

    tc = nuke.createNode("AddTimeCode", inpanel=False)
    tc.setInput(0, parent)
    tc["fps"].setValue(FPS)

    if start_tc:
        # Assign start_tc to the first rendered frame and count forward.
        tc["startcode"].setValue(str(start_tc))
        # 'useFrame' + 'frame' makes startcode correspond to that frame number
        # rather than to frame 1; this keeps TC correct for offset renders
        # (e.g. frames numbered 1001+).
        if tc.knob("useFrame"):
            tc["useFrame"].setValue(True)
        if tc.knob("frame"):
            tc["frame"].setValue(int(first_frame))
        tc["label"].setValue("Source TC (from flag)")
    else:
        # No flag TC: pass through embedded EXR timecode if present. Setting
        # the node to NOT regenerate leaves any existing input/timecode intact.
        if tc.knob("useFrame"):
            tc["useFrame"].setValue(False)
        tc["label"].setValue("Source TC (embedded / none)")

    return tc


# ---------------------------------------------------------------------------
# Burn-ins
# ---------------------------------------------------------------------------

def build_burnins(parent, data, first_frame):
    is_shot = is_shot_context(data)

    step    = data.get("step", "")
    version = data.get("version", 1)
    date    = str(data.get("date", ""))[:10]

    if is_shot:
        artist     = data.get("artist", "")
        upper_left = "In House - %s" % artist
        lower_left = "%s_%s_v%03d" % (data.get("shot_code", ""), step, version)
        cut_in     = data.get("cut_in")
        cut_out    = data.get("cut_out")
    else:
        asset_name  = data.get("entity_name", "")
        asset_type  = data.get("asset_type", "Asset")
        upper_left  = "%s - %s" % (asset_type, asset_name)
        lower_left  = "%s_%s_v%03d" % (asset_name, step, version)
        cut_in = cut_out = None

    last = parent

    def text_node(parent, msg, xj, yj, size=24):
        n = nuke.createNode("Text2", inpanel=False)
        n.setInput(0, parent)
        n["message"].setValue(msg)
        n["xjustify"].setValue(xj)
        n["yjustify"].setValue(yj)
        n["size"].setValue(size)
        n["color"].setValue((1, 1, 1, 1))
        return n

    last = text_node(last, upper_left, "left",  "top")
    last = text_node(last, date,       "right", "top")
    last = text_node(last, lower_left, "left",  "bottom")

    # Source timecode onto the stream (from flag start_timecode, or embedded).
    last = build_timecode(last, data, first_frame)

    # Bottom right: timecode display (reads the TC metadata set above)
    tc_text = nuke.createNode("Text2", inpanel=False)
    tc_text.setInput(0, last)
    tc_text["message"].setValue("[metadata input/timecode]")
    tc_text["xjustify"].setValue("right")
    tc_text["yjustify"].setValue("bottom")
    tc_text["size"].setValue(24)
    tc_text["color"].setValue((1, 1, 1, 1))
    last = tc_text

    # Bottom center: frame counter
    counter = nuke.createNode("Text2", inpanel=False)
    counter.setInput(0, last)
    counter["message"].setValue("[frame]")
    counter["xjustify"].setValue("center")
    counter["yjustify"].setValue("bottom")
    counter["size"].setValue(28)

    if cut_in is not None and cut_out is not None:
        # White inside edit range, yellow for handles
        in_range = "(frame>=%d && frame<=%d) ? 1 : 0" % (int(cut_in), int(cut_out))
        counter["color"].setExpression(in_range, "r")
        counter["color"].setExpression(in_range, "g")
        counter["color"].setExpression(in_range, "b")
        counter["color"].setExpression("1", "a")
    else:
        # No cut range (e.g. shot without sg_cut_in/out, or asset turntable):
        # plain white counter, no handle colouring.
        counter["color"].setValue((1, 1, 1, 1))

    return counter


# ---------------------------------------------------------------------------
# Slate
# ---------------------------------------------------------------------------

def build_slate(data, first_frame, last_frame):
    is_shot = is_shot_context(data)

    bg = nuke.createNode("Constant", inpanel=False)
    bg["color"].setValue((0, 0, 0, 1))
    last = bg

    if os.path.exists(LOGO_PATH):
        logo_read = nuke.createNode("Read", inpanel=False)
        logo_read["file"].setValue(p(LOGO_PATH))
        merge = nuke.createNode("Merge2", inpanel=False)
        merge.setInput(0, last)
        merge.setInput(1, logo_read)
        merge["operation"].setValue("over")
        last = merge

    if is_shot:
        context_line = "%s / %s / %s" % (
            data.get("episode", ""),
            data.get("scene", ""),
            data.get("shot_code", ""),
        )
    else:
        context_line = "%s / %s" % (
            data.get("asset_type", "Asset"),
            data.get("entity_name", ""),
        )

    start_tc = data.get("start_timecode")
    tc_line = "Start TC: %s" % (start_tc if start_tc else "n/a")

    lines = [
        context_line,
        "Step: %s   Version: v%03d" % (data.get("step", ""), data.get("version", 1)),
        "Artist: %s" % data.get("artist", ""),
        "Date: %s" % str(data.get("date", ""))[:10],
        "Frame Range: %s - %s" % (first_frame, last_frame),
        tc_line,
        "Submitted For: %s" % data.get("submitted_for", ""),
        "Description: %s" % data.get("description", ""),
    ]

    slate_text = nuke.createNode("Text2", inpanel=False)
    slate_text.setInput(0, last)
    slate_text["message"].setValue("\n".join(lines))
    slate_text["xjustify"].setValue("left")
    slate_text["yjustify"].setValue("center")
    slate_text["size"].setValue(36)
    slate_text["color"].setValue((1, 1, 1, 1))

    return slate_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    flag_path    = sys.argv[1]
    output_paths = sys.argv[2:]

    with open(flag_path, "r") as f:
        data = json.load(f)

    # EXR path: support both key names (current schema uses exr_path_pattern).
    exr_pattern = data.get("exr_path") or data.get("exr_path_pattern", "")
    first, last_frame = get_frame_range(data)

    # ── Main read ─────────────────────────────────────────────────────────────
    read = nuke.createNode("Read", inpanel=False)
    read["file"].setValue(p(exr_pattern))
    read["raw"].setValue(True)
    read["colorspace"].setValue("raw")
    read["first"].setValue(first)
    read["last"].setValue(last_frame)
    read["origfirst"].setValue(first)
    read["origlast"].setValue(last_frame)

    # ── Color bake ────────────────────────────────────────────────────────────
    baked  = build_color_bake(read, data)
    burned = build_burnins(baked, data, first)

    # ── Slate ─────────────────────────────────────────────────────────────────
    slate = build_slate(data, first, last_frame)

    slate_offset = nuke.createNode("TimeOffset", inpanel=False)
    slate_offset.setInput(0, slate)
    slate_offset["time_offset"].setValue((first - 1) - 1)

    append = nuke.createNode("AppendClip", inpanel=False)
    append.setInput(0, slate_offset)
    append.setInput(1, burned)

    root = nuke.root()
    root["first_frame"].setValue(first - 1)
    root["last_frame"].setValue(last_frame)
    root["fps"].setValue(FPS)

    # ── Write outputs ─────────────────────────────────────────────────────────
    for out_path in output_paths:
        out_dir = os.path.dirname(out_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir)

        write = nuke.createNode("Write", inpanel=False)
        write.setInput(0, append)
        write["file"].setValue(p(out_path))
        write["file_type"].setValue("mov")
        if write.knob("mov64_codec"):
            write["mov64_codec"].setValue("appr")  # Apple ProRes
        write["colorspace"].setValue("Output - Rec.709")
        write["raw"].setValue(False)

        nuke.execute(write, first - 1, last_frame)
        nuke.delete(write)

    print("[qt_bake] Done. Wrote %d output(s)." % len(output_paths))


if __name__ == "__main__":
    main()
