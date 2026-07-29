"""
qt_bake_oiio.py

License-free QT bake using OpenImageIO (oiiotool) + FFmpeg.
Handles both shot renders and asset turntable renders.

Usage:
    python3 qt_bake_oiio.py <flag_json_path> <output_path_1> [<output_path_2> ...]

Color pipeline (identical to Nuke bake):
    ACEScg -> LogC4 -> CDL (if present) -> Show LUT -> Rec.709

Each stage can be switched off per submission via the flag's "color_stages"
map (written by the Review Drop app's preview window). The stage list and the
oiiotool argument construction live in color_pipeline.py, shared with that
preview so what an artist approves is what gets baked here.

Slate frame:
    - Black background
    - Show logo (top-left)
    - Context-aware text block:
        Shot:  Episode / Scene / Shot / Step / Version / Artist / Date /
               Frame Range / Start TC / Submitted For / Description
        Asset: Asset Type / Asset / Step / Version / Artist / Date /
               Submitted For / Description

Burn-ins on every frame:
    Upper left:    "In House - {artist}"  (shots)
                   "{asset_type} - {asset}"  (assets)
    Upper right:   date
    Lower left:    "{shot}_{step}_v{version}"  or  "{asset}_{step}_v{version}"
    Bottom center: source frame number
    Bottom right:  source timecode (HH:MM:SS:FF), anchored to start_timecode

Requires:
    brew install ffmpeg openimageio

FLAG SCHEMA (2026-06-17): consumes the render-complete flag written by
render_complete_callback.py. Key fields used here:
    frame_first, frame_last   - actual rendered frame numbers (ints)
    start_timecode            - source TC at frame_first, "HH:MM:SS:FF", or
                                null if the render carried no embedded TC
    exr_path_pattern          - macOS-resolved EXR pattern (with %04d or ####)
    cut_in, cut_out           - edit range (currently informational here)
    shot_code, episode, scene, step, version, artist, date,
    submitted_for, description
    color_stages              - optional {stage_key: bool} map; missing keys
                                fall back to the defaults for the source type
"""

import datetime
import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The color/geometry pipeline itself lives in color_pipeline.py so the Review
# Drop app's preview window renders through exactly the same stage list and
# oiiotool arguments as this bake. The OCIO colorspace names, the show LUT
# path and the delivery size are defined there too.
import color_pipeline
from color_pipeline import (
    DELIVERY_HEIGHT,
    DELIVERY_WIDTH,
    FFMPEG,
    OCIO_REC709_DISPLAY,
    OCIO_REC709_VIEW,
    OIIOTOOL,
    SHOW_LUT_PATH,
    build_frame_command,
    default_stages,
    describe_stages,
    frame_path,
    normalize_stages,
    read_pixel_aspect,
    read_resolution,
    resolve_cdl_path,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOGO_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/_globals/logo/teardrop_blk1.png"
)

FPS = 24

# ---------------------------------------------------------------------------
# Slate title + thumbnails + bottom strip
# ---------------------------------------------------------------------------

SLATE_TITLE = "BUFFALO S3"

# drawtext needs an actual font FILE, not a family name. This points at the
# stock macOS Arial Bold (present by default in Fonts/Supplemental on every
# Mac, no extra license needed) for a bold geometric-grotesk look in the
# same spirit as modern minimal show titles. VERIFY this path exists on the
# Mac Studio (`ls "/System/Library/Fonts/Supplemental/Arial Bold.ttf"`); if
# missing, point this at any bold sans .ttf/.otf already licensed for the
# show instead.
TITLE_FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
TITLE_FONT_SIZE = 110
TITLE_X = 80
TITLE_Y = 50

# Logo: top right, with a fixed margin from both edges. Uses ffmpeg overlay's
# main_w/overlay_w expressions so it's correctly right-aligned regardless of
# the logo PNG's actual pixel dimensions - no need to hardcode its size here.
LOGO_MARGIN = 60

# Three thumbnails (first / mid / last rendered frame), full color baked
# through the same ACEScg->LogC4->CDL->Show LUT->Rec.709 chain as the main
# QT, laid out as one even row beneath the title/metadata text block and
# above the bottom gradient strip. Equal size, no rotation, no overlap.
SBS_MARGIN_X = 80
SBS_GAP = 40
SBS_Y = 700
SBS_HEIGHT = 250
SBS_WIDTH = (DELIVERY_WIDTH - 2 * SBS_MARGIN_X - 2 * SBS_GAP) // 3
SBS_SIZE = (SBS_WIDTH, SBS_HEIGHT)

# Bottom strip: a row of grayscale steps (black -> white) over a row of
# saturated color bars (white, yellow, cyan, green, magenta, red, blue) -
# standard technical reference bars, full delivery width, sitting at the
# very bottom edge of the slate.
STRIP_HEIGHT = 120                 # total height, split evenly between rows
STRIP_ROW_HEIGHT = STRIP_HEIGHT // 2
GRAYSCALE_STEPS = 12
COLOR_BARS = [
    "white", "yellow", "cyan", "green", "magenta", "red", "blue",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd, label=""):
    """Run a shell command, print output, raise on failure."""
    print("[qt_bake_oiio] %s" % (label or " ".join(cmd)))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print("[qt_bake_oiio] ERROR: %s" % result.stderr)
        raise RuntimeError("Command failed: %s" % " ".join(cmd))
    return result


def get_frame_range(data):
    """
    Return (first, last) as ints from the flag.

    Current schema uses scalar frame_first / frame_last (the ACTUAL rendered
    frame numbers). Older flags used a 'frame_range' [first, last] list -
    support both so a stale flag doesn't silently bake a 1-frame movie.
    """
    if data.get("frame_first") is not None and data.get("frame_last") is not None:
        return int(data["frame_first"]), int(data["frame_last"])
    fr = data.get("frame_range")
    if fr and len(fr) >= 2:
        return int(fr[0]), int(fr[1])
    print("[qt_bake_oiio] WARNING: no frame range in flag; defaulting to 1-1")
    return 1, 1


def timecode_from_frame(frame, fps=FPS):
    """Convert an absolute frame number to HH:MM:SS:FF (last-resort fallback)."""
    total_seconds = frame // fps
    ff = frame % fps
    hh = total_seconds // 3600
    mm = (total_seconds % 3600) // 60
    ss = total_seconds % 60
    return "%02d:%02d:%02d:%02d" % (hh, mm, ss, ff)


def tc_to_frames(tc, fps=FPS):
    """Parse 'HH:MM:SS:FF' (or ';' drop separator) to a total frame count."""
    parts = tc.replace(";", ":").split(":")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return None
    hh, mm, ss, ff = (int(x) for x in parts)
    return hh * 3600 * fps + mm * 60 * fps + ss * fps + ff


def frames_to_tc(total_frames, fps=FPS):
    """Inverse of tc_to_frames: total frame count -> 'HH:MM:SS:FF'."""
    total_frames = max(0, int(total_frames))
    ff = total_frames % fps
    ss = (total_frames // fps) % 60
    mm = (total_frames // fps // 60) % 60
    hh = total_frames // fps // 3600
    return "%02d:%02d:%02d:%02d" % (hh, mm, ss, ff)


def extract_exr_timecode(exr_path):
    """
    Read the SMPTE timecode embedded in an EXR file's metadata using oiiotool.
    Unreal's Movie Render Queue writes this as 'smpte:TimeCode' or 'timecode'.

    Returns a timecode string "HH:MM:SS:FF" or None if not found.
    """
    try:
        result = subprocess.run(
            [OIIOTOOL, "--info", "-v", exr_path],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            line_lower = line.lower()
            if "timecode" in line_lower or "smpte" in line_lower:
                # Line format: "    smpte:TimeCode: 00:00:00:00"
                # or           "    timecode: 00:00:00:00"
                parts = line.split(":", 1)
                if len(parts) == 2:
                    tc = parts[1].strip()
                    tc_parts = tc.replace(";", ":").split(":")
                    if len(tc_parts) == 4 and all(p.isdigit() for p in tc_parts):
                        return ":".join(tc_parts)
    except Exception as e:
        print("[qt_bake_oiio] WARNING: could not read EXR timecode: %s" % e)
    return None


def resolve_start_timecode(data, exr_pattern, first):
    """
    Determine the source start timecode, in priority order:

      1. start_timecode from the flag (authoritative - captured at submission
         from the EXR's embedded TC, which is what the artist saw).
      2. Read it directly from the first EXR's metadata (fallback if the flag
         didn't carry one).
      3. Derive from the frame number (last resort - keeps the burn-in
         populated even when no real TC exists anywhere).

    Returns (tc_string, source_label) for logging/slate clarity.
    """
    flag_tc = data.get("start_timecode")
    if flag_tc:
        return str(flag_tc), "flag"

    first_exr = frame_path(exr_pattern, first)
    if os.path.exists(first_exr):
        exr_tc = extract_exr_timecode(first_exr)
        if exr_tc:
            return exr_tc, "exr"

    return timecode_from_frame(first), "frame-derived"


def is_shot_context(data):
    """Return True if this flag JSON is from a shot render, False for asset."""
    return data.get("type", "shot") != "asset_turntable"


# ---------------------------------------------------------------------------
# Color bake: single source frame -> baked PNG (delivery frame or slate thumb)
# ---------------------------------------------------------------------------

def bake_frame(src_exr, dst_png, stages, cdl_path=None, desqueeze_to=None,
               fit_to=None):
    """
    Run one frame through the enabled pipeline stages using oiiotool:
        ACEScg -> LogC4 -> CDL -> Show LUT -> Rec.709 -> de-squeeze -> fit

    stages       : stage on/off map (see color_pipeline.PIPELINE_STAGES). With
                   every color stage off this degrades to a plain
                   resize/convert of the source pixels.
    cdl_path     : path to a per-shot .cc to apply, or None to skip.
    desqueeze_to : (width, height) to resize the frame to FIRST, for
                   anamorphic de-squeeze. None means no de-squeeze.
    fit_to       : (width, height) final delivery size. The (de-squeezed)
                   image is letterboxed/pillarboxed to fit EXACTLY this box,
                   preserving aspect with black bars. None means no fit.

    Output is a PNG suitable for ffmpeg input.
    """
    cmd = build_frame_command(
        src_exr, dst_png, stages,
        cdl_path=cdl_path, desqueeze_to=desqueeze_to, fit_to=fit_to,
    )
    run(cmd, label="Frame bake: %s" % os.path.basename(src_exr))


def bake_thumbnail(src_exr, dst_png, stages, cdl_path, desqueeze_to, size):
    """
    Same stages as bake_frame, but for a small slate collage print rather
    than a delivery frame: resizes directly to the exact (w, h) box with NO
    letterbox padding, since the collage prints sit on the slate's own
    black/white border treatment rather than needing to preserve source
    aspect exactly. A small amount of aspect distortion at thumbnail size is
    an acceptable tradeoff for filling the print cleanly.
    """
    thumb_stages = dict(stages)
    thumb_stages["fit"] = False
    cmd = build_frame_command(
        src_exr, dst_png, thumb_stages,
        cdl_path=cdl_path, desqueeze_to=desqueeze_to, resize_to=size,
    )
    run(cmd, label="Thumbnail bake: %s" % os.path.basename(src_exr))


# ---------------------------------------------------------------------------
# Burn-ins: overlay text onto frames using FFmpeg drawtext
# ---------------------------------------------------------------------------

def build_drawtext_filters(data, frame_offset, start_tc):
    """
    Build FFmpeg drawtext filter chain for burn-ins.

    frame_offset : added to ffmpeg's 0-based output frame index so the
                   bottom-center counter shows the real source frame number.
    start_tc     : source start timecode (HH:MM:SS:FF) for the bottom-right
                   burn-in. Already offset by the caller to account for the
                   prepended slate, so it reads correctly on the first image
                   frame.
    """
    is_shot = is_shot_context(data)

    if is_shot:
        upper_left = "In House - %s" % data.get("artist", "")
        lower_left = "%s_%s_v%03d" % (
            data.get("shot_code", ""),
            data.get("step", ""),
            data.get("version", 1),
        )
    else:
        upper_left = "%s - %s" % (
            data.get("asset_type", "Asset"),
            data.get("entity_name", ""),
        )
        lower_left = "%s_%s_v%03d" % (
            data.get("entity_name", ""),
            data.get("step", ""),
            data.get("version", 1),
        )

    upper_right = str(data.get("date", ""))[:10]  # YYYY-MM-DD only
    font_size = 28
    margin = 40

    # Escape for FFmpeg filter syntax (colons and quotes).
    def esc(s):
        return str(s).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")

    filters = []

    # Upper left
    filters.append(
        "drawtext=text='%s':x=%d:y=%d:fontsize=%d:fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=4"
        % (esc(upper_left), margin, margin, font_size)
    )

    # Upper right
    filters.append(
        "drawtext=text='%s':x=w-%d-tw:y=%d:fontsize=%d:fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=4"
        % (esc(upper_right), margin, margin, font_size)
    )

    # Lower left
    filters.append(
        "drawtext=text='%s':x=%d:y=h-%d-th:fontsize=%d:fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=4"
        % (esc(lower_left), margin, margin, font_size)
    )

    # Bottom center: source frame number
    # n = ffmpeg's 0-based output frame index; add frame_offset to recover the
    # real source frame number (slate is at index 0, first image frame -> first).
    filters.append(
        "drawtext=text='%%{eif\\:n+%d\\:d\\:4}':x=(w-tw)/2:y=h-%d-th:fontsize=%d"
        ":fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=4"
        % (frame_offset, margin, font_size)
    )

    # Bottom right: SOURCE timecode, anchored to start_tc and auto-incremented
    # by drawtext per output frame. This shows editorial-accurate source TC,
    # NOT elapsed playback time. A space 'text' is required alongside the
    # timecode option on many ffmpeg builds.
    tc_esc = start_tc.replace(":", r"\:")
    filters.append(
        "drawtext=timecode='%s':timecode_rate=%d:text=' ':x=w-%d-tw:y=h-%d-th"
        ":fontsize=%d:fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=4"
        % (tc_esc, FPS, margin, margin, font_size)
    )

    return ",".join(filters)


# ---------------------------------------------------------------------------
# Slate frame builder
# ---------------------------------------------------------------------------

def _esc_drawtext(s):
    """Escape a string for safe use inside an FFmpeg drawtext text= value."""
    return str(s).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def build_slate_png(data, dst_path, first, last, start_tc, width, height,
                     tmpdir, exr_pattern, cdl_path, stages, desqueeze_to):
    """
    Render a slate frame as a PNG: title + metadata text block over a black
    background, a row of three equal-size frame thumbnails (first/mid/last)
    below it, the show logo top right, and a grayscale + color bar reference
    strip along the bottom. The three thumbnails are run through the same
    pipeline stages as the deliverable frames, so they match the graded look
    of the shot rather than showing raw/log plates.

    width/height are the OUTPUT dimensions - must match the (possibly
    de-squeezed) image frames so concat/append doesn't mismatch.

    tmpdir, exr_pattern, cdl_path, stages, desqueeze_to are passed through
    from bake_sequence() so the thumbnails inherit the exact same color
    decisions (CDL presence, enabled stages, de-squeeze) already resolved for
    the main bake - no re-deriving them here.
    """
    is_shot = is_shot_context(data)

    if is_shot:
        context_line = "%s / %s / %s" % (
            data.get("episode", ""),
            data.get("sequence") or data.get("scene", ""),
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
        "Frame Range: %s - %s" % (first, last),
        "Start TC: %s" % (start_tc if start_tc else "n/a"),
        "Submitted For: %s" % data.get("submitted_for", ""),
        "Description: %s" % data.get("description", ""),
    ]

    font_size = 36
    line_height = 52
    margin_x = 80
    # Pushed down from the old start_y=280 to clear the new title block
    # (TITLE_Y + TITLE_FONT_SIZE + breathing room).
    start_y = TITLE_Y + TITLE_FONT_SIZE + 60

    text_filters = []
    for i, line in enumerate(lines):
        y = start_y + i * line_height
        text_filters.append(
            "drawtext=text='%s':x=%d:y=%d:fontsize=%d:fontcolor=white"
            % (_esc_drawtext(line), margin_x, y, font_size)
        )

    # Title, using the explicit font file so it reads as a bold geometric
    # sans regardless of which default font fontconfig would otherwise pick.
    title_filter = (
        "drawtext=text='%s':fontfile='%s':x=%d:y=%d:fontsize=%d:fontcolor=white"
        % (_esc_drawtext(SLATE_TITLE), TITLE_FONT_PATH, TITLE_X, TITLE_Y, TITLE_FONT_SIZE)
    )

    # ── Bake the three thumbnails (first / mid / last), full color ─────────
    mid = (first + last) // 2

    def _nearest_existing(frame_num):
        """If the exact frame is missing on disk, nudge inward until one
        exists, so a single dropped frame doesn't take out the whole slate."""
        if os.path.exists(frame_path(exr_pattern, frame_num)):
            return frame_num
        for delta in range(1, (last - first) + 1):
            for candidate in (frame_num - delta, frame_num + delta):
                if first <= candidate <= last and os.path.exists(frame_path(exr_pattern, candidate)):
                    return candidate
        return None

    frame_first_actual = _nearest_existing(first)
    frame_mid_actual = _nearest_existing(mid)
    frame_last_actual = _nearest_existing(last)

    thumb1_png = os.path.join(tmpdir, "slate_thumb_1.png")
    thumb2_png = os.path.join(tmpdir, "slate_thumb_2.png")
    thumb3_png = os.path.join(tmpdir, "slate_thumb_3.png")

    have_thumbs = all(
        f is not None for f in (frame_first_actual, frame_mid_actual, frame_last_actual)
    )

    if have_thumbs:
        for frame_num, thumb_png in (
            (frame_first_actual, thumb1_png),
            (frame_mid_actual, thumb2_png),
            (frame_last_actual, thumb3_png),
        ):
            bake_thumbnail(
                frame_path(exr_pattern, frame_num), thumb_png,
                stages, cdl_path, desqueeze_to, SBS_SIZE,
            )
    else:
        print(
            "[qt_bake_oiio] WARNING: could not find all of first/mid/last "
            "frames on disk for the slate thumbnail row - skipping "
            "thumbnails, slate will show title/text/strip only."
        )

    # ── Composite: bg -> [3 side-by-side thumbnails] -> logo -> strip -> text ──
    inputs = [
        "-f", "lavfi", "-i", "color=black:size=%dx%d:rate=1" % (width, height),
    ]
    next_input_idx = 1  # input 0 is the lavfi black background
    filter_parts = []
    cur_label = "0:v"

    if have_thumbs:
        thumb_idx_1, thumb_idx_2, thumb_idx_3 = next_input_idx, next_input_idx + 1, next_input_idx + 2
        inputs += ["-i", thumb1_png, "-i", thumb2_png, "-i", thumb3_png]
        next_input_idx += 3

        x1 = SBS_MARGIN_X
        x2 = SBS_MARGIN_X + SBS_WIDTH + SBS_GAP
        x3 = SBS_MARGIN_X + 2 * (SBS_WIDTH + SBS_GAP)

        filter_parts.append("[0:v][%d:v]overlay=%d:%d[bg1]" % (thumb_idx_1, x1, SBS_Y))
        filter_parts.append("[bg1][%d:v]overlay=%d:%d[bg2]" % (thumb_idx_2, x2, SBS_Y))
        filter_parts.append("[bg2][%d:v]overlay=%d:%d[bg3]" % (thumb_idx_3, x3, SBS_Y))
        cur_label = "bg3"

    have_logo = os.path.exists(LOGO_PATH)
    if have_logo:
        logo_idx = next_input_idx
        inputs += ["-i", LOGO_PATH]
        next_input_idx += 1
        # Top right: ffmpeg's main_w/overlay_w expressions right-align the
        # logo regardless of its native pixel size, so LOGO_MARGIN alone
        # controls the gap from both edges.
        filter_parts.append(
            "[%s][%d:v]overlay=main_w-overlay_w-%d:%d[bg4]"
            % (cur_label, logo_idx, LOGO_MARGIN, LOGO_MARGIN)
        )
        cur_label = "bg4"

    # ── Bottom reference strip: grayscale row over a saturated color-bar row ──
    strip_filters = []
    strip_y_top = height - STRIP_HEIGHT
    strip_y_bottom = height - STRIP_ROW_HEIGHT
    gray_step_w = width / float(GRAYSCALE_STEPS)
    for i in range(GRAYSCALE_STEPS):
        # Evenly spaced black -> white steps, last step forced to pure white
        # so the ramp actually reaches 0xFFFFFF rather than stopping short.
        level = int(round(255 * i / (GRAYSCALE_STEPS - 1)))
        hexcol = "%02x%02x%02x" % (level, level, level)
        seg_x = int(round(i * gray_step_w))
        seg_w = int(round((i + 1) * gray_step_w)) - seg_x
        strip_filters.append(
            "drawbox=x=%d:y=%d:w=%d:h=%d:color=0x%s:t=fill"
            % (seg_x, strip_y_top, seg_w, STRIP_ROW_HEIGHT, hexcol)
        )
    bar_step_w = width / float(len(COLOR_BARS))
    for i, color in enumerate(COLOR_BARS):
        seg_x = int(round(i * bar_step_w))
        seg_w = int(round((i + 1) * bar_step_w)) - seg_x
        strip_filters.append(
            "drawbox=x=%d:y=%d:w=%d:h=%d:color=%s:t=fill"
            % (seg_x, strip_y_bottom, seg_w, STRIP_ROW_HEIGHT, color)
        )

    all_overlay_filters = strip_filters + [title_filter] + text_filters
    if filter_parts:
        # Chain the strip + drawtext onto the last labeled node, output [out].
        filter_parts[-1] += ";[%s]%s[out]" % (cur_label, ",".join(all_overlay_filters))
        filter_complex = ";".join(filter_parts)
    else:
        # No thumbnails, no logo: strip + drawtext chain straight on the bg.
        filter_complex = "[0:v]%s[out]" % ",".join(all_overlay_filters)

    cmd = [FFMPEG, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-vframes", "1", dst_path,
    ]
    run(cmd, label="Slate frame (title + thumbnails + strip + text)")


# ---------------------------------------------------------------------------
# Main bake pipeline
# ---------------------------------------------------------------------------

def bake_sequence(data, output_paths):
    """
    Full bake pipeline:
      1. Bake each EXR frame to a temp PNG with color transform
         (or, for skip_color movie sources, pass frames/movie through)
      2. Optionally build slate PNG
      3. Concatenate slate + frames via FFmpeg
      4. Add burn-ins
      5. Encode to ProRes QT for each output path

    Flag extras (optional, used by the Review Drop app):
      include_slate  - bool, default True
      skip_color     - bool, default False (MOV/QT sources)
      movie_path     - path to an existing .mov/.mp4 when skip_color is True
      color_stages   - per-stage on/off map; see color_pipeline.py. Absent
                       means "the defaults for this skip_color value", i.e.
                       exactly the pre-preview-window behaviour.
    """
    include_slate = data.get("include_slate", True)
    skip_color = data.get("skip_color", False)
    movie_path = data.get("movie_path")
    stages = normalize_stages(data.get("color_stages"), skip_color)

    if movie_path:
        bake_movie_passthrough(data, output_paths, movie_path, include_slate)
        return

    exr_pattern = data.get("exr_path") or data.get("exr_path_pattern", "")
    first, last = get_frame_range(data)

    # Resolve the source start timecode (flag -> EXR -> frame-derived).
    start_tc, tc_source = resolve_start_timecode(data, exr_pattern, first)
    print("[qt_bake_oiio] Start TC: %s (source: %s)" % (start_tc, tc_source))
    print("[qt_bake_oiio] include_slate=%s skip_color=%s" % (include_slate, skip_color))
    print("[qt_bake_oiio] Pipeline: %s" % describe_stages(stages))

    # CDL path (shots only — assets skip CDL). CDL is an optional creative
    # grade: if absent, skip it but log so it's visible that the QT is
    # ungraded.
    cdl_path = None
    if is_shot_context(data) and stages.get("cdl"):
        # Prefer Sequence (Episode→Sequence→Shot); fall back to legacy scene.
        cdl_guess = resolve_cdl_path(
            data.get("episode", ""),
            data.get("sequence") or data.get("scene") or "",
            data.get("shot_code", ""),
        )
        if cdl_guess and os.path.exists(cdl_guess):
            cdl_path = cdl_guess
            print("[qt_bake_oiio] CDL: applying %s" % cdl_guess)
        else:
            print("[qt_bake_oiio] CDL: none found at %s — baking UNGRADED" % cdl_guess)
    elif not stages.get("cdl"):
        print("[qt_bake_oiio] CDL: skipped (stage switched off)")
    else:
        print("[qt_bake_oiio] CDL: skipped (asset turntable)")

    # Show LUT presence. If the stage is on but the file is missing, the
    # pipeline falls back to a generic LogC4->Rec.709 conversion (viewable,
    # roughly correct) rather than shipping flat log — log it loudly since the
    # look will differ from final.
    if not stages.get("show_lut"):
        print("[qt_bake_oiio] Show LUT: skipped (stage switched off)")
    elif os.path.exists(SHOW_LUT_PATH):
        print("[qt_bake_oiio] Show LUT: applying %s" % SHOW_LUT_PATH)
    else:
        print(
            "[qt_bake_oiio] Show LUT: NOT FOUND at %s — falling back to display "
            "transform '%s' / '%s'. Look will differ from final show look."
            % (SHOW_LUT_PATH, OCIO_REC709_DISPLAY, OCIO_REC709_VIEW)
        )

    # Anamorphic de-squeeze + fixed delivery size. Read the pixel aspect
    # ratio and resolution ONCE from the first frame (a sequence shares one
    # PAR). If PAR != 1.0, de-squeeze by reducing height (new_height =
    # height / PAR), keeping width. Then EVERY frame is letterboxed to the
    # fixed DELIVERY_WIDTH x DELIVERY_HEIGHT, so all QTs are 1920x1080
    # regardless of source resolution. The slate is always delivery size too.
    first_exr = frame_path(exr_pattern, first)
    src_w, src_h = read_resolution(first_exr)
    par = read_pixel_aspect(first_exr)
    desqueeze_to = color_pipeline.desqueeze_size(src_w, src_h, par)
    if src_w is None or src_h is None:
        print("[qt_bake_oiio] Resolution: could not read; no de-squeeze applied")
    elif desqueeze_to is not None:
        print(
            "[qt_bake_oiio] Anamorphic: PAR=%.4f, de-squeezing %dx%d -> %dx%d "
            "(height/PAR, Lanczos3)" % (par, src_w, src_h, desqueeze_to[0], desqueeze_to[1])
        )
    else:
        print("[qt_bake_oiio] PAR=1.0 (square pixels); no de-squeeze")

    # All QTs deliver at this fixed size, letterboxed — unless the fit stage
    # was switched off, in which case frames keep their (de-squeezed) source
    # size and the slate has to match it or the concat mismatches.
    fit_to = (DELIVERY_WIDTH, DELIVERY_HEIGHT)
    if stages.get("fit"):
        out_w, out_h = DELIVERY_WIDTH, DELIVERY_HEIGHT
        print("[qt_bake_oiio] Delivery: letterboxing to %dx%d" % (out_w, out_h))
    else:
        out_w, out_h = desqueeze_to or (src_w, src_h)
        if not out_w or not out_h:
            out_w, out_h = DELIVERY_WIDTH, DELIVERY_HEIGHT
            print(
                "[qt_bake_oiio] Delivery: fit stage off but source size is "
                "unreadable; slate falls back to %dx%d" % (out_w, out_h)
            )
        else:
            print(
                "[qt_bake_oiio] Delivery: fit stage off — keeping source size "
                "%dx%d" % (out_w, out_h)
            )
        if out_w % 2 or out_h % 2:
            print(
                "[qt_bake_oiio] WARNING: %dx%d has an odd dimension; ProRes "
                "encoding may reject it. Re-enable the letterbox stage if the "
                "encode fails." % (out_w, out_h)
            )

    with tempfile.TemporaryDirectory(prefix="qt_bake_") as tmpdir:
        print("[qt_bake_oiio] Working in temp dir: %s" % tmpdir)

        # ── 1. Bake each frame ────────────────────────────────────────────────
        baked_frames = []
        for frame_num in range(first, last + 1):
            src = frame_path(exr_pattern, frame_num)
            if not os.path.exists(src):
                print("[qt_bake_oiio] WARNING: missing frame %s" % src)
                continue
            dst = os.path.join(tmpdir, "frame_%04d.png" % frame_num)
            bake_frame(
                src, dst, stages, cdl_path=cdl_path,
                desqueeze_to=desqueeze_to, fit_to=fit_to,
            )
            baked_frames.append(dst)

        if not baked_frames:
            raise RuntimeError("No frames were baked — check EXR path: %s" % exr_pattern)

        # ── 2. Build slate (optional) ─────────────────────────────────────────
        slate_path = None
        if include_slate:
            slate_path = os.path.join(tmpdir, "slate.png")
            build_slate_png(
                data, slate_path, first, last, start_tc, out_w, out_h,
                tmpdir=tmpdir, exr_pattern=exr_pattern, cdl_path=cdl_path,
                stages=stages, desqueeze_to=desqueeze_to,
            )

        # ── 3. Build frame list for FFmpeg concat ─────────────────────────────
        concat_list = os.path.join(tmpdir, "frames.txt")
        with open(concat_list, "w") as f:
            if slate_path:
                f.write("file '%s'\n" % slate_path)
                f.write("duration %f\n" % (1.0 / FPS))
            for baked in baked_frames:
                f.write("file '%s'\n" % baked)
                f.write("duration %f\n" % (1.0 / FPS))
            if baked_frames:
                f.write("file '%s'\n" % baked_frames[-1])

        if include_slate:
            # Slate at output index 0; first image frame at index 1.
            burnin_offset = first - 1
            start_tc_frames = tc_to_frames(start_tc)
            if start_tc_frames is not None:
                burnin_start_tc = frames_to_tc(start_tc_frames - 1)
            else:
                burnin_start_tc = start_tc
        else:
            burnin_offset = first
            burnin_start_tc = start_tc

        burnin_filters = build_drawtext_filters(data, burnin_offset, burnin_start_tc)
        embed_tc = burnin_start_tc

        # ── 4+5. Encode to ProRes QT with burn-ins and TC track ───────────────
        for out_path in output_paths:
            out_dir = os.path.dirname(out_path)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir)

            cmd = [
                FFMPEG, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list,
                "-vf", burnin_filters,
                "-c:v", "prores_ks",
                "-profile:v", "3",        # ProRes 422 HQ
                "-vendor", "apl0",
                "-pix_fmt", "yuv422p10le",
                "-r", str(FPS),
            ]

            if embed_tc:
                cmd += ["-timecode", embed_tc]

            cmd.append(out_path)

            run(cmd, label="Encode QT: %s" % os.path.basename(out_path))
            print("[qt_bake_oiio] Written: %s" % out_path)


def bake_movie_passthrough(data, output_paths, movie_path, include_slate):
    """Re-wrap an existing MOV/MP4 with burn-ins (and optional slate). No OCIO."""
    if not os.path.exists(movie_path):
        raise RuntimeError("movie_path not found: %s" % movie_path)

    print("[qt_bake_oiio] Movie passthrough (skip_color): %s" % movie_path)
    print("[qt_bake_oiio] include_slate=%s" % include_slate)

    first, last = get_frame_range(data)
    start_tc = data.get("start_timecode") or "00:00:00:00"

    fit = "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (
        DELIVERY_WIDTH, DELIVERY_HEIGHT, DELIVERY_WIDTH, DELIVERY_HEIGHT
    )

    with tempfile.TemporaryDirectory(prefix="qt_bake_mov_") as tmpdir:
        slate_path = None
        if include_slate:
            slate_path = os.path.join(tmpdir, "slate.png")
            # Minimal slate without EXR thumbnails
            build_slate_png(
                data, slate_path, first, last, start_tc,
                DELIVERY_WIDTH, DELIVERY_HEIGHT,
                tmpdir=tmpdir, exr_pattern="", cdl_path=None,
                stages=default_stages(skip_color=True), desqueeze_to=None,
            )

        if include_slate:
            burnin_offset = first - 1
            start_tc_frames = tc_to_frames(start_tc)
            burnin_start_tc = (
                frames_to_tc(start_tc_frames - 1)
                if start_tc_frames is not None else start_tc
            )
        else:
            burnin_offset = first
            burnin_start_tc = start_tc

        burnin_filters = build_drawtext_filters(data, burnin_offset, burnin_start_tc)
        vf = "%s,%s" % (fit, burnin_filters)

        for out_path in output_paths:
            out_dir = os.path.dirname(out_path)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir)

            if slate_path:
                # slate (1 frame) + movie
                cmd = [
                    FFMPEG, "-y",
                    "-loop", "1", "-t", str(1.0 / FPS), "-i", slate_path,
                    "-i", movie_path,
                    "-filter_complex",
                    "[0:v]%s[slate];[1:v]%s[mov];[slate][mov]concat=n=2:v=1:a=0[out]"
                    % (fit, vf),
                    "-map", "[out]",
                    "-c:v", "prores_ks",
                    "-profile:v", "3",
                    "-vendor", "apl0",
                    "-pix_fmt", "yuv422p10le",
                    "-r", str(FPS),
                    "-timecode", burnin_start_tc,
                    out_path,
                ]
            else:
                cmd = [
                    FFMPEG, "-y",
                    "-i", movie_path,
                    "-vf", vf,
                    "-c:v", "prores_ks",
                    "-profile:v", "3",
                    "-vendor", "apl0",
                    "-pix_fmt", "yuv422p10le",
                    "-r", str(FPS),
                    "-timecode", burnin_start_tc,
                    out_path,
                ]

            run(cmd, label="Encode QT (movie passthrough): %s" % os.path.basename(out_path))
            print("[qt_bake_oiio] Written: %s" % out_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 qt_bake_oiio.py <flag_json> <output_path> [<output_path2> ...]")
        sys.exit(1)

    flag_path = sys.argv[1]
    output_paths = sys.argv[2:]

    print("[qt_bake_oiio] Reading flag: %s" % flag_path)
    with open(flag_path, "r") as f:
        data = json.load(f)

    bake_sequence(data, output_paths)
    print("[qt_bake_oiio] Done. Wrote %d output(s)." % len(output_paths))


if __name__ == "__main__":
    main()
