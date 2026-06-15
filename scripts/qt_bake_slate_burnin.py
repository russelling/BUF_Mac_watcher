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
          Frame Range, Submitted For, Description

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
# Burn-ins
# ---------------------------------------------------------------------------

def build_burnins(parent, data):
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

    def text_node(parent, msg, xj, yj, xo=40, yo=40, size=24):
        n = nuke.createNode("Text2", inpanel=False)
        n.setInput(0, parent)
        n["message"].setValue(msg)
        n["xjustify"].setValue(xj)
        n["yjustify"].setValue(yj)
        n["size"].setValue(size)
        n["color"].setValue((1, 1, 1, 1))
        return n

    last = text_node(last, upper_left,  "left",   "top")
    last = text_node(last, date,         "right",  "top")
    last = text_node(last, lower_left,  "left",   "bottom")

    # Bottom right: timecode
    tc = nuke.createNode("AddTimeCode", inpanel=False)
    tc.setInput(0, last)
    tc["fps"].setValue(FPS)
    tc["startcode"].setValue("00:00:00:00")
    last = tc

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
        in_range = "(frame>=%d && frame<=%d) ? 1 : 0" % (cut_in, cut_out)
        counter["color"].setExpression(in_range, "r")
        counter["color"].setExpression(in_range, "g")
        counter["color"].setExpression(in_range, "b")
        counter["color"].setExpression("1", "a")
    else:
        counter["color"].setValue((1, 1, 1, 1))

    return counter


# ---------------------------------------------------------------------------
# Slate
# ---------------------------------------------------------------------------

def build_slate(data):
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

    frame_range = data.get("frame_range", [1, 1])

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

    lines = [
        context_line,
        "Step: %s   Version: v%03d" % (data.get("step", ""), data.get("version", 1)),
        "Artist: %s" % data.get("artist", ""),
        "Date: %s" % str(data.get("date", ""))[:10],
        "Frame Range: %s - %s" % (frame_range[0], frame_range[1]),
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

    # Support both key names for EXR path and frame range
    exr_pattern = data.get("exr_path") or data.get("exr_path_pattern", "")
    frame_range = data.get("frame_range", [1, 1])
    first       = int(frame_range[0])
    last_frame  = int(frame_range[1])

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
    burned = build_burnins(baked, data)

    # ── Slate ─────────────────────────────────────────────────────────────────
    slate = build_slate(data)

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
