"""
qt_bake_oiio.py

License-free QT bake using OpenImageIO (oiiotool) + FFmpeg.
Handles both shot renders and asset turntable renders.

Usage:
    python3 qt_bake_oiio.py <flag_json_path> <output_path_1> [<output_path_2> ...]

Color pipeline (identical to Nuke bake):
    ACEScg -> LogC4 -> CDL (if present) -> Show LUT -> Rec.709

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
"""

import datetime
import json
import math
import os
import subprocess
import sys
import tempfile

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

OIIOTOOL = "/opt/homebrew/bin/oiiotool"

# Point directly at the ffmpeg-full keg, NOT /opt/homebrew/bin/ffmpeg. The
# regular Homebrew 'ffmpeg' formula is built WITHOUT freetype, so it lacks the
# 'drawtext' filter the slate and burn-ins require. 'ffmpeg-full' includes it.
# Using the keg path avoids depending on which ffmpeg the PATH symlink happens
# to resolve to (a future 'brew' op could flip it and silently break the
# watcher). If ffmpeg-full is upgraded, update this version-pinned path.
FFMPEG = "/opt/homebrew/Cellar/ffmpeg-full/8.1.2/bin/ffmpeg"

OCIO_CONFIG = "ocio://studio-config-latest"

# Use the config's SPACE-FREE aliases, not the display names. oiiotool
# tokenizes positional colorspace arguments on whitespace, so a name like
# "ARRI LogC4" is misread as two arguments ("ARRI" + "LogC4"). The aliases
# below (from the config inventory) avoid that entirely.
#
# NOTE on the LogC4 step: arri_logc4 is the FULL "ARRI LogC4" colorspace
# (LogC4 curve + ARRI Wide Gamut 4 primaries), a deliberate choice that
# differs from the Nuke setup's curve-only "Input - ARRI - Curve - LogC4".
# If the show .cube LUT expects curve-on-AP1, revisit this.
OCIO_ACESCG = "ACEScg"        # alias of "ACEScg" (no space anyway)
OCIO_LOGC4  = "arri_logc4"    # alias of "ARRI LogC4"

# Fallback when SHOW_LUT_PATH is missing: the Studio config expresses Rec.709
# output as a display + view. These contain spaces and the config provides no
# space-free aliases for them. They are passed as separate --ociodisplay
# trailing arguments, which SHOULD be fine - but this path is UNTESTED (it
# only fires when the show LUT is missing). If it errors on the spaces like
# --colorconvert did, the alternative is to apply the display via a different
# mechanism. Test by temporarily renaming the show LUT.
OCIO_REC709_DISPLAY = "Gamma 2.2 Rec.709 - Display"
OCIO_REC709_VIEW    = "ACES 2.0 - SDR 100 nits (Rec.709)"

FPS = 24

# Fixed final delivery size for ALL QTs. Every output is letterboxed/
# pillarboxed to exactly this, regardless of source resolution or squeeze.
DELIVERY_WIDTH = 1920
DELIVERY_HEIGHT = 1080


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


def frame_path(pattern, frame_num):
    """
    Expand a frame pattern to an actual filename. Supports both #### and
    %04d style tokens (the flag's exr_path_pattern uses %04d).
    """
    if "####" in pattern:
        return pattern.replace("####", "%04d" % frame_num)
    if "%04d" in pattern:
        return pattern % frame_num
    # No recognised token - return as-is (single file).
    return pattern


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


def read_pixel_aspect(exr_path):
    """
    Read the PixelAspectRatio from an EXR's metadata via oiiotool --info -v.

    Returns a float (e.g. 2.0 for a 2:1 anamorphic squeeze), or 1.0 if the
    attribute is absent or unreadable (i.e. treat as non-anamorphic).
    """
    try:
        result = subprocess.run(
            [OIIOTOOL, "--info", "-v", exr_path],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            # Line format: "    PixelAspectRatio: 2" (may be "2", "2.0", etc.)
            if "pixelaspectratio" in line.lower():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    try:
                        par = float(parts[1].strip())
                        if par > 0:
                            return par
                    except ValueError:
                        pass
    except Exception as e:
        print("[qt_bake_oiio] WARNING: could not read PixelAspectRatio: %s" % e)
    return 1.0


def read_resolution(exr_path):
    """
    Read the pixel width/height of an EXR via oiiotool --info.

    Returns (width, height) ints, or (None, None) if unreadable.
    """
    try:
        result = subprocess.run(
            [OIIOTOOL, "--info", exr_path],
            capture_output=True, text=True
        )
        # Typical: "<path> :  1920 x 1080, 4 channel, half openexr"
        import re
        m = re.search(r"(\d+)\s*x\s*(\d+)", result.stdout)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception as e:
        print("[qt_bake_oiio] WARNING: could not read resolution: %s" % e)
    return (None, None)


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
# Color bake: single EXR frame -> baked PNG (for slate) or baked EXR (frames)
# ---------------------------------------------------------------------------

def bake_frame(src_exr, dst_png, cdl_path=None, use_show_lut=True,
               desqueeze_to=None, fit_to=None):
    """
    Apply full color pipeline to a single EXR frame using oiiotool:
        ACEScg -> LogC4 -> CDL (optional) -> Show LUT -> Rec.709

    cdl_path     : path to a per-shot .cc to apply, or None to skip.
    use_show_lut : if True (and the LUT file exists), apply the show LUT for
                   the final LogC4->Rec.709 step. If False, fall back to a
                   generic LogC4->Rec.709 colorspace conversion.
    desqueeze_to : (width, height) to resize the frame to FIRST, for
                   anamorphic de-squeeze. None means no de-squeeze.
    fit_to       : (width, height) final delivery size. The (de-squeezed)
                   image is letterboxed/pillarboxed to fit EXACTLY this box,
                   preserving aspect with black bars. None means no fit.

    Output is an 8-bit PNG suitable for ffmpeg input.
    """
    # The OCIO config must be set ONCE, up front, via the top-level
    # --colorconfig flag. It is NOT a valid modifier on --colorconvert or
    # --ociofiletransform (those only accept key=/value=/unpremult=/etc).
    cmd = [OIIOTOOL, "--colorconfig", OCIO_CONFIG, src_exr]

    # Step 1: ACEScg -> LogC4 via OCIO
    cmd += [
        "--colorconvert",
        OCIO_ACESCG,
        OCIO_LOGC4,
    ]

    # Step 2: CDL if present (caller has already decided and logged).
    if cdl_path and os.path.exists(cdl_path):
        cmd += ["--ociofiletransform", cdl_path]

    # Step 3: Show LUT -> Rec.709, or fallback display transform.
    if use_show_lut and os.path.exists(SHOW_LUT_PATH):
        cmd += ["--ociofiletransform", SHOW_LUT_PATH]
    else:
        cmd += [
            "--ociodisplay:from=%s" % OCIO_LOGC4,
            OCIO_REC709_DISPLAY,
            OCIO_REC709_VIEW,
        ]

    # Step 4: anamorphic de-squeeze (deliberately change aspect, so resize to
    # the exact de-squeezed pixel size). Lanczos3 is a sharp resample filter.
    if desqueeze_to is not None:
        dw, dh = desqueeze_to
        cmd += ["--resize:filter=lanczos3", "%dx%d" % (dw, dh)]

    # Step 5: fit/letterbox to the fixed delivery size. pad=1 forces the
    # output to be EXACTLY fit_to with black bars, preserving aspect.
    if fit_to is not None:
        fw, fh = fit_to
        cmd += ["--fit:filter=lanczos3:pad=1", "%dx%d" % (fw, fh)]

    # Clamp, convert to 8-bit, output PNG.
    # NOTE: --clamp takes min=/max= as colon-appended MODIFIERS, not
    # positional args. "--clamp 0 1" makes oiiotool treat 0 and 1 as input
    # filenames (-> "File does not exist: 0").
    cmd += ["--clamp:min=0:max=1", "--ch", "R,G,B", "-o", dst_png]

    run(cmd, label="Color bake: %s" % os.path.basename(src_exr))


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

def build_slate_png(data, dst_path, first, last, start_tc, width, height):
    """
    Render a slate frame as a PNG using FFmpeg's lavfi source + drawtext.

    width/height are the OUTPUT dimensions - must match the (possibly
    de-squeezed) image frames so concat/append doesn't mismatch.
    """
    is_shot = is_shot_context(data)

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
        "Frame Range: %s - %s" % (first, last),
        "Start TC: %s" % (start_tc if start_tc else "n/a"),
        "Submitted For: %s" % data.get("submitted_for", ""),
        "Description: %s" % data.get("description", ""),
    ]

    font_size = 36
    line_height = 52
    margin_x = 80
    start_y = 280  # below logo area

    text_filters = []
    for i, line in enumerate(lines):
        y = start_y + i * line_height
        escaped = str(line).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
        text_filters.append(
            "drawtext=text='%s':x=%d:y=%d:fontsize=%d:fontcolor=white"
            % (escaped, margin_x, y, font_size)
        )

    if os.path.exists(LOGO_PATH):
        # First generate black bg + text, then overlay logo
        black_with_text = dst_path + ".notlogo.png"
        cmd = [
            FFMPEG, "-y",
            "-f", "lavfi",
            "-i", "color=black:size=%dx%d:rate=1" % (width, height),
            "-vframes", "1",
            "-vf", ",".join(text_filters),
            black_with_text,
        ]
        run(cmd, label="Slate text layer")

        # oiiotool --over requires BOTH images to have an alpha channel. The
        # ffmpeg-generated background is RGB only, so add an opaque alpha to
        # it first (--ch R,G,B,A=1.0). The logo PNG carries its own alpha.
        # --invert flips the logo's RGB (chbegin=0,chend=3 by default leaves
        # alpha intact), so a light teardrop becomes dark and vice versa.
        cmd = [
            OIIOTOOL,
            black_with_text,
            "--ch", "R,G,B,A=1.0",
            LOGO_PATH,
            "--invert",
            "--over",
            "-o", dst_path,
        ]
        run(cmd, label="Slate logo overlay")
        os.remove(black_with_text)
    else:
        cmd = [
            FFMPEG, "-y",
            "-f", "lavfi",
            "-i", "color=black:size=%dx%d:rate=1" % (width, height),
            "-vframes", "1",
            "-vf", ",".join(text_filters),
            dst_path,
        ]
        run(cmd, label="Slate frame")


# ---------------------------------------------------------------------------
# Main bake pipeline
# ---------------------------------------------------------------------------

def bake_sequence(data, output_paths):
    """
    Full bake pipeline:
      1. Bake each EXR frame to a temp PNG with color transform
      2. Build slate PNG
      3. Concatenate slate + baked frames via FFmpeg
      4. Add burn-ins
      5. Encode to ProRes QT for each output path
    """
    exr_pattern = data.get("exr_path") or data.get("exr_path_pattern", "")
    first, last = get_frame_range(data)

    # Resolve the source start timecode (flag -> EXR -> frame-derived).
    start_tc, tc_source = resolve_start_timecode(data, exr_pattern, first)
    print("[qt_bake_oiio] Start TC: %s (source: %s)" % (start_tc, tc_source))

    # CDL path (shots only — assets skip CDL). CDL is an optional creative
    # grade: if absent, skip it but log so it's visible that the QT is
    # ungraded.
    cdl_path = None
    if is_shot_context(data):
        shot = data.get("shot_code", "")
        episode = str(data.get("episode", ""))
        scene = str(data.get("scene", ""))
        cdl_guess = os.path.join(
            "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots",
            episode, scene, shot, "plates", "%s.cc" % shot,
        )
        if os.path.exists(cdl_guess):
            cdl_path = cdl_guess
            print("[qt_bake_oiio] CDL: applying %s" % cdl_guess)
        else:
            print("[qt_bake_oiio] CDL: none found at %s — baking UNGRADED" % cdl_guess)
    else:
        print("[qt_bake_oiio] CDL: skipped (asset turntable)")

    # Show LUT presence: decided once. If missing, fall back to a generic
    # LogC4->Rec.709 conversion (viewable, roughly correct) rather than
    # shipping flat log. Log it loudly since the look will differ from final.
    use_show_lut = os.path.exists(SHOW_LUT_PATH)
    if use_show_lut:
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
    desqueeze_to = None
    if src_w is None or src_h is None:
        print("[qt_bake_oiio] Resolution: could not read; no de-squeeze applied")
    elif par and abs(par - 1.0) > 1e-3:
        new_h = int(round(src_h / par))
        print(
            "[qt_bake_oiio] Anamorphic: PAR=%.4f, de-squeezing %dx%d -> %dx%d "
            "(height/PAR, Lanczos3)" % (par, src_w, src_h, src_w, new_h)
        )
        desqueeze_to = (src_w, new_h)
    else:
        print("[qt_bake_oiio] PAR=1.0 (square pixels); no de-squeeze")

    # All QTs deliver at this fixed size, letterboxed.
    fit_to = (DELIVERY_WIDTH, DELIVERY_HEIGHT)
    out_w, out_h = DELIVERY_WIDTH, DELIVERY_HEIGHT
    print("[qt_bake_oiio] Delivery: letterboxing to %dx%d" % (out_w, out_h))

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
                src, dst, cdl_path=cdl_path, use_show_lut=use_show_lut,
                desqueeze_to=desqueeze_to, fit_to=fit_to,
            )
            baked_frames.append(dst)

        if not baked_frames:
            raise RuntimeError("No frames were baked — check EXR path: %s" % exr_pattern)

        # ── 2. Build slate ────────────────────────────────────────────────────
        slate_path = os.path.join(tmpdir, "slate.png")
        build_slate_png(data, slate_path, first, last, start_tc, out_w, out_h)

        # ── 3. Build frame list for FFmpeg concat ─────────────────────────────
        # Slate = 1 frame (output index 0), then baked frames follow.
        concat_list = os.path.join(tmpdir, "frames.txt")
        with open(concat_list, "w") as f:
            f.write("file '%s'\n" % slate_path)
            f.write("duration %f\n" % (1.0 / FPS))
            for baked in baked_frames:
                f.write("file '%s'\n" % baked)
                f.write("duration %f\n" % (1.0 / FPS))
            # FFmpeg concat demuxer needs last file listed twice
            if baked_frames:
                f.write("file '%s'\n" % baked_frames[-1])

        # Burn-in frame offset: output index 0 is the slate, so the first
        # image frame (output index 1) must read the real source 'first'.
        # eif uses n (0-based). At n=1 we want 'first', so offset = first - 1.
        burnin_offset = first - 1

        # Burn-in timecode start: the slate occupies output frame 0, and
        # drawtext starts counting timecode from output frame 0. So set the
        # burn-in start TC one frame BEFORE the source start, so that the
        # first image frame (output index 1) reads exactly start_tc.
        start_tc_frames = tc_to_frames(start_tc)
        if start_tc_frames is not None:
            burnin_start_tc = frames_to_tc(start_tc_frames - 1)
        else:
            # Unparseable TC - fall back to the raw value (burn-in still shows
            # something rather than crashing).
            burnin_start_tc = start_tc

        burnin_filters = build_drawtext_filters(data, burnin_offset, burnin_start_tc)

        # Embedded SMPTE timecode track: same one-frame-back offset so the
        # QT's TC track aligns with the first image frame, not the slate.
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
