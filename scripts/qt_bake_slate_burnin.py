"""
qt_bake_slate_burnin.py

Run via: nuke -t qt_bake_slate_burnin.py <flag_json_path> <output_path_1> [<output_path_2> ...]

Reads an EXR sequence (raw ACEScg) described by the render-complete flag JSON,
applies the full output color chain:

    ACEScg -> LogC4 -> CDL (per-shot) -> Show LUT -> Rec.709

Builds:
  - A clean slate frame (prepended) with:
      show logo, Episode / Scene / Shot, Artist, Date, Description
      (Step / Version / Frame range / Submitted-for are also included)
  - Burn-ins on every frame:
      upper left  : "In House - {artist}"
      upper right : date
      lower left  : "{shot}_{step}_v{version}"
      bottom center: frame counter, color-coded:
                        white  = within cut in/out (edit range)
                        yellow = handle frame (outside cut in/out)
      bottom right: timecode

Writes the result to every output path supplied on the command line
(typically the shot's review folder + the dated to_editorial folder).
"""

import sys
import json
import os

import nuke


SHOW_LUT_PATH = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/color/luts/ARRILogC4_SEV_S3_V3_digital_R709.cube"
LOGO_PATH = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/GLOBALS/logo/teardrop.png"

OCIO_ACES_WORKING = "ACES - ACEScg"
OCIO_LOGC4 = "Input - ARRI - Curve - LogC4 - EI800"

FPS = 24  # adjust if show framerate differs


def p(path):
    return path.replace("\\", "/")


def build_color_bake(read_node):
    """ACEScg -> LogC4 -> CDL -> Show LUT (-> Rec.709)"""

    cs1 = nuke.createNode("OCIOColorSpace", inpanel=False)
    cs1.setInput(0, read_node)
    cs1["in_colorspace"].setValue(OCIO_ACES_WORKING)
    cs1["out_colorspace"].setValue(OCIO_LOGC4)
    cs1["label"].setValue("ACEScg -> LogC4")

    cdl_path = None
    if read_node.knob("cdl_path"):
        cdl_path = read_node["cdl_path"].value()

    last = cs1
    if cdl_path and os.path.exists(cdl_path):
        cdl = nuke.createNode("OCIOFileTransform", inpanel=False)
        cdl.setInput(0, last)
        cdl["file"].setValue(p(cdl_path))
        cdl["direction"].setValue("forward")
        cdl["interpolation"].setValue("linear")
        cdl["label"].setValue("Shot CDL")
        last = cdl

    if os.path.exists(SHOW_LUT_PATH):
        lut = nuke.createNode("OCIOFileTransform", inpanel=False)
        lut.setInput(0, last)
        lut["file"].setValue(p(SHOW_LUT_PATH))
        lut["direction"].setValue("forward")
        lut["interpolation"].setValue("tetrahedral")
        lut["label"].setValue("Show LUT -> Rec.709")
        last = lut

    return last


def add_text_node(parent, message, xjustify, yjustify, x_offset=20, y_offset=20,
                   size=28, color_expr=None, font_color=(1, 1, 1, 1)):
    text = nuke.createNode("Text2", inpanel=False)
    text.setInput(0, parent)
    text["message"].setValue(message)
    text["xjustify"].setValue(xjustify)
    text["yjustify"].setValue(yjustify)
    text["size"].setValue(size)

    # Position offsets via translate
    tx = x_offset if xjustify == "left" else (-x_offset if xjustify == "right" else 0)
    ty = y_offset if yjustify == "bottom" else (-y_offset if yjustify == "top" else 0)
    text["box"].setValue([tx, ty, tx, ty])  # nudge via box if needed; translate below preferred

    if color_expr:
        # color_expr is a dict of channel -> expression string
        for chan, expr in color_expr.items():
            try:
                text["color"].setExpression(expr, chan)
            except Exception:
                pass
    else:
        text["color"].setValue(font_color)

    return text


def build_burnins(parent, data):
    shot = data["shot_code"]
    step = data["step"]
    version = data["version"]
    artist = data["artist"]
    date_str = data["date"]
    cut_in = data.get("cut_in")
    cut_out = data.get("cut_out")

    last = parent

    # Upper left: In House - {artist}
    last = add_text_node(
        last, "In House - %s" % artist,
        xjustify="left", yjustify="top", x_offset=40, y_offset=40, size=24,
    )

    # Upper right: date
    last = add_text_node(
        last, date_str,
        xjustify="right", yjustify="top", x_offset=40, y_offset=40, size=24,
    )

    # Lower left: shot_step_vXXX
    last = add_text_node(
        last, "%s_%s_v%03d" % (shot, step, version),
        xjustify="left", yjustify="bottom", x_offset=40, y_offset=40, size=24,
    )

    # Bottom right: timecode
    tc_node = nuke.createNode("AddTimeCode", inpanel=False)
    tc_node.setInput(0, last)
    tc_node["fps"].setValue(FPS)
    tc_node["startcode"].setValue("00:00:00:00")
    last = tc_node

    last = add_text_node(
        last, "[metadata input/timecode]",
        xjustify="right", yjustify="bottom", x_offset=40, y_offset=40, size=24,
    )

    # Bottom center: frame counter, color-coded for handles
    counter = nuke.createNode("Text2", inpanel=False)
    counter.setInput(0, last)
    counter["message"].setValue("[frame]")
    counter["xjustify"].setValue("center")
    counter["yjustify"].setValue("bottom")
    counter["size"].setValue(28)

    if cut_in is not None and cut_out is not None:
        # White inside cut range, yellow for handle frames
        in_range_expr = "(frame>=%d && frame<=%d) ? 1 : 0" % (cut_in, cut_out)
        # r,g = expr; b = 0 when handle (yellow), 1 when in range (white)
        counter["color"].setExpression(in_range_expr, "r")
        counter["color"].setExpression(in_range_expr, "g")
        counter["color"].setExpression(in_range_expr, "b")
        counter["color"].setExpression("1", "a")
    else:
        counter["color"].setValue((1, 1, 1, 1))

    return counter


def build_slate(data):
    """Builds a standalone slate frame: logo + text fields on separate lines."""

    bg = nuke.createNode("Constant", inpanel=False)
    bg["color"].setValue((0, 0, 0, 1))
    bg["label"].setValue("Slate BG")

    # Match format to plate format - assumes Read1 (main bake read) already
    # established root format; Constant inherits project format by default.

    last = bg

    if os.path.exists(LOGO_PATH):
        logo_read = nuke.createNode("Read", inpanel=False)
        logo_read["file"].setValue(p(LOGO_PATH))
        logo_read["label"].setValue("Show Logo")

        logo_merge = nuke.createNode("Merge2", inpanel=False)
        logo_merge.setInput(0, last)
        logo_merge.setInput(1, logo_read)
        logo_merge["operation"].setValue("over")
        last = logo_merge

    lines = [
        "%s / %s / %s" % (data.get("episode"), data.get("scene"), data.get("shot_code")),
        "Step: %s   Version: v%03d" % (data.get("step"), data.get("version")),
        "Artist: %s" % data.get("artist"),
        "Date: %s" % data.get("date"),
        "Frame Range: %s - %s" % (data.get("frame_first"), data.get("frame_last")),
        "Submitted For: %s" % data.get("submitted_for"),
        "Description: %s" % data.get("description"),
    ]

    text_block = "\n".join(lines)

    slate_text = nuke.createNode("Text2", inpanel=False)
    slate_text.setInput(0, last)
    slate_text["message"].setValue(text_block)
    slate_text["xjustify"].setValue("left")
    slate_text["yjustify"].setValue("center")
    slate_text["box"].setValue([60, 60, 60, 60])
    slate_text["size"].setValue(36)
    slate_text["color"].setValue((1, 1, 1, 1))

    return slate_text


def main():
    flag_path = sys.argv[1]
    output_paths = sys.argv[2:]

    with open(flag_path, "r") as f:
        data = json.load(f)

    exr_pattern = data["exr_path_pattern"]
    first = data["frame_first"]
    last = data["frame_last"]

    # --- Main read ---
    read = nuke.createNode("Read", inpanel=False)
    read["file"].setValue(p(exr_pattern))
    read["raw"].setValue(True)
    read["colorspace"].setValue("raw")
    read["first"].setValue(first)
    read["last"].setValue(last)
    read["origfirst"].setValue(first)
    read["origlast"].setValue(last)

    # Optional per-shot CDL path - convention: same dir as plates, {shot}.cc
    shot = data["shot_code"]
    cdl_guess = os.path.join(
        "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots",
        str(data.get("episode")), str(data.get("scene")), shot,
        "plates", "%s.cc" % shot,
    )
    read.addKnob(nuke.String_Knob("cdl_path", "cdl_path", cdl_guess))

    baked = build_color_bake(read)
    burned = build_burnins(baked, data)

    slate = build_slate(data)

    # --- Append slate as frame (first - 1) ---
    slate_offset = nuke.createNode("TimeOffset", inpanel=False)
    slate_offset.setInput(0, slate)
    slate_offset["time_offset"].setValue((first - 1) - 1)  # slate's source frame -> first-1

    append = nuke.createNode("AppendClip", inpanel=False)
    append.setInput(0, slate_offset)
    append.setInput(1, burned)

    root = nuke.root()
    root["first_frame"].setValue(first - 1)
    root["last_frame"].setValue(last)
    root["fps"].setValue(FPS)

    # --- Write to each output path ---
    for out_path in output_paths:
        out_dir = os.path.dirname(out_path)
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)

        write = nuke.createNode("Write", inpanel=False)
        write.setInput(0, append)
        write["file"].setValue(p(out_path))
        write["file_type"].setValue("mov")
        if write.knob("mov64_codec"):
            write["mov64_codec"].setValue("appr")  # Apple ProRes
        write["colorspace"].setValue("Output - Rec.709")
        write["raw"].setValue(False)

        nuke.execute(write, first - 1, last)

        # remove write node before next iteration to avoid duplicate renders
        nuke.delete(write)

    print("[qt_bake] Done. Wrote %d output(s)." % len(output_paths))


if __name__ == "__main__":
    main()
