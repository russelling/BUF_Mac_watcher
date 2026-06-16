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
               Frame Range / Submitted For / Description
        Asset: Asset Type / Asset / Step / Version / Artist / Date /
               Submitted For / Description

Burn-ins on every frame:
    Upper left:    "In House - {artist}"  (shots)
                   "{asset_type} - {asset}"  (assets)
    Upper right:   date
    Lower left:    "{shot}_{step}_v{version}"  or  "{asset}_{step}_v{version}"
    Bottom center: frame number
    Bottom right:  timecode (HH:MM:SS:FF)

Requires:
    brew install ffmpeg openimageio
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
FFMPEG = "/opt/homebrew/bin/ffmpeg"

OCIO_CONFIG = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/color/aces_1.2/config.ocio"
)

FPS = 24
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080


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
    """Expand a #### pattern to an actual frame filename."""
    return pattern.replace("####", "%04d" % frame_num)


def frames_from_pattern(pattern, first, last):
    """Return list of existing frame paths."""
    return [frame_path(pattern, f) for f in range(first, last + 1)]


def timecode(frame, fps=FPS):
    """Convert frame number to HH:MM:SS:FF timecode string."""
    total_seconds = frame // fps
    ff = frame % fps
    hh = total_seconds // 3600
    mm = (total_seconds % 3600) // 60
    ss = total_seconds % 60
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
                    # Validate it looks like HH:MM:SS:FF
                    tc_parts = tc.replace(";", ":").split(":")
                    if len(tc_parts) == 4 and all(p.isdigit() for p in tc_parts):
                        return ":".join(tc_parts)
    except Exception as e:
        print("[qt_bake_oiio] WARNING: could not read EXR timecode: %s" % e)
    return None


def is_shot_context(data):
    """Return True if this flag JSON is from a shot render, False for asset."""
    return data.get("type", "shot") != "asset_turntable"


# ---------------------------------------------------------------------------
# Color bake: single EXR frame -> baked PNG (for slate) or baked EXR (frames)
# ---------------------------------------------------------------------------

def bake_frame(src_exr, dst_png, cdl_path=None):
    """
    Apply full color pipeline to a single EXR frame using oiiotool:
        ACEScg -> LogC4 -> CDL (optional) -> Show LUT -> Rec.709

    Output is an 8-bit PNG suitable for ffmpeg input.
    """
    cmd = [OIIOTOOL, src_exr]

    # Step 1: ACEScg -> LogC4 via OCIO
    cmd += [
        "--colorconvert:config=%s" % OCIO_CONFIG,
        "ACES - ACEScg",
        "Input - ARRI - Curve - LogC4 - EI800",
    ]

    # Step 2: CDL if present
    if cdl_path and os.path.exists(cdl_path):
        cmd += ["--ociofiletransform:config=%s" % OCIO_CONFIG, cdl_path]

    # Step 3: Show LUT -> Rec.709
    if os.path.exists(SHOW_LUT_PATH):
        cmd += ["--ociofiletransform:config=%s" % OCIO_CONFIG, SHOW_LUT_PATH]

    # Clamp, convert to 8-bit, output PNG
    cmd += ["--clamp", "0", "1", "--ch", "R,G,B", "-o", dst_png]

    run(cmd, label="Color bake: %s" % os.path.basename(src_exr))


# ---------------------------------------------------------------------------
# Burn-ins: overlay text onto a baked PNG using FFmpeg drawtext
# ---------------------------------------------------------------------------

def build_drawtext_filters(data, frame_offset=0):
    """
    Build FFmpeg drawtext filter chain for burn-ins.
    frame_offset accounts for slate prepended at frame 0.
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

    upper_right = data.get("date", "")[:10]  # YYYY-MM-DD only
    font_size = 28
    margin = 40

    # Escape colons for FFmpeg filter syntax
    def esc(s):
        return str(s).replace(":", r"\:").replace("'", r"\'")

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

    # Bottom center: frame number
    # n = current frame number in ffmpeg (0-based), add frame_offset to get
    # the actual frame number from the source sequence
    filters.append(
        "drawtext=text='%%{eif\\:n+%d\\:d\\:4}':x=(w-tw)/2:y=h-%d-th:fontsize=%d"
        ":fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=4"
        % (frame_offset, margin, font_size)
    )

    # Bottom right: timecode expressed as frame-derived text
    filters.append(
        "drawtext=text='%%{pts\\:hms}':x=w-%d-tw:y=h-%d-th:fontsize=%d"
        ":fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=4"
        % (margin, margin, font_size)
    )

    return ",".join(filters)


# ---------------------------------------------------------------------------
# Slate frame builder
# ---------------------------------------------------------------------------

def build_slate_png(data, dst_path):
    """
    Render a slate frame as a PNG using FFmpeg's lavfi source + drawtext.
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
        "Date: %s" % data.get("date", "")[:10],
        "Frame Range: %s - %s" % (
            data.get("frame_range", [1, 1])[0],
            data.get("frame_range", [1, 1])[1],
        ),
        "Submitted For: %s" % data.get("submitted_for", ""),
        "Description: %s" % data.get("description", ""),
    ]

    font_size = 36
    line_height = 52
    margin_x = 80
    start_y = 280  # below logo area

    # Build drawtext filter chain for slate lines
    text_filters = []
    for i, line in enumerate(lines):
        y = start_y + i * line_height
        escaped = line.replace(":", r"\:").replace("'", r"\'")
        text_filters.append(
            "drawtext=text='%s':x=%d:y=%d:fontsize=%d:fontcolor=white"
            % (escaped, margin_x, y, font_size)
        )

    # If logo exists, overlay it
    if os.path.exists(LOGO_PATH):
        # First generate black bg + text, then overlay logo
        black_with_text = dst_path + ".notlogo.png"
        cmd = [
            FFMPEG, "-y",
            "-f", "lavfi",
            "-i", "color=black:size=%dx%d:rate=1" % (FRAME_WIDTH, FRAME_HEIGHT),
            "-vframes", "1",
            "-vf", ",".join(text_filters),
            black_with_text,
        ]
        run(cmd, label="Slate text layer")

        # Overlay logo top-left with oiiotool
        cmd = [
            OIIOTOOL,
            black_with_text,
            LOGO_PATH,
            "--over",
            "-o", dst_path,
        ]
        run(cmd, label="Slate logo overlay")
        os.remove(black_with_text)
    else:
        cmd = [
            FFMPEG, "-y",
            "-f", "lavfi",
            "-i", "color=black:size=%dx%d:rate=1" % (FRAME_WIDTH, FRAME_HEIGHT),
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
    frame_range = data.get("frame_range", [1, 1])
    first, last = int(frame_range[0]), int(frame_range[1])

    # CDL path (shots only — assets skip CDL)
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
            bake_frame(src, dst, cdl_path=cdl_path)
            baked_frames.append(dst)

        if not baked_frames:
            raise RuntimeError("No frames were baked — check EXR path: %s" % exr_pattern)

        # ── 2. Build slate ────────────────────────────────────────────────────
        slate_path = os.path.join(tmpdir, "slate.png")
        build_slate_png(data, slate_path)

        # ── 3. Build frame list for FFmpeg concat ─────────────────────────────
        # Slate = 1 frame (frame 0), then baked frames start at 1
        concat_list = os.path.join(tmpdir, "frames.txt")
        with open(concat_list, "w") as f:
            # Slate held for 1 frame
            f.write("file '%s'\n" % slate_path)
            f.write("duration %f\n" % (1.0 / FPS))
            for baked in baked_frames:
                f.write("file '%s'\n" % baked)
                f.write("duration %f\n" % (1.0 / FPS))
            # FFmpeg concat demuxer needs last file listed twice
            if baked_frames:
                f.write("file '%s'\n" % baked_frames[-1])

        # Frame offset for burn-ins: slate is frame 0, first source frame = 1
        burnin_filters = build_drawtext_filters(data, frame_offset=first - 1)

        # ── Extract SMPTE timecode from first EXR frame ───────────────────────
        first_exr = frame_path(exr_pattern, first)
        smpte_tc = None
        if os.path.exists(first_exr):
            smpte_tc = extract_exr_timecode(first_exr)
            if smpte_tc:
                print("[qt_bake_oiio] EXR timecode found: %s" % smpte_tc)
            else:
                # Derive from frame number if EXR doesn't carry TC metadata
                smpte_tc = timecode(first)
                print("[qt_bake_oiio] No EXR timecode metadata — deriving from frame: %s" % smpte_tc)

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

            # Embed SMPTE timecode track — slate occupies frame (first-1),
            # so offset TC back by one frame so it reads correctly on frame 1
            if smpte_tc:
                # Subtract one frame from TC to account for prepended slate
                tc_parts = smpte_tc.replace(";", ":").split(":")
                hh, mm, ss, ff = [int(x) for x in tc_parts]
                total_frames = hh * 3600 * FPS + mm * 60 * FPS + ss * FPS + ff
                total_frames = max(0, total_frames - 1)
                ff2  = total_frames % FPS
                ss2  = (total_frames // FPS) % 60
                mm2  = (total_frames // FPS // 60) % 60
                hh2  = total_frames // FPS // 3600
                slate_tc = "%02d:%02d:%02d:%02d" % (hh2, mm2, ss2, ff2)
                cmd += ["-timecode", slate_tc]

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

