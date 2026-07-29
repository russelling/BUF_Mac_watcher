"""
color_pipeline.py

Single definition of the QT bake color/geometry pipeline, shared by the
Mac Studio bake (qt_bake_oiio.py) and the Review Drop app's preview window
(drop_app/preview.py) so a preview always matches what the watcher renders.

The pipeline is expressed as an ordered list of STAGES, each of which can be
switched off individually:

    input_transform    ACEScg -> ARRI LogC4          (OCIO colorconvert)
    cdl                per-shot .cc grade            (OCIO file transform)
    show_lut           show .cube LogC4 -> Rec.709   (OCIO file transform)
    display_transform  Rec.709 display + view        (used when show_lut is off)
    desqueeze          anamorphic de-squeeze         (resize by PAR)
    fit                letterbox to delivery size    (fit + pad)

The Review Drop app writes the resulting on/off map into the render-complete
flag as "color_stages"; qt_bake_oiio.py reads it back. Flags without that key
keep the historical behaviour: every stage on, except for skip_color sources
(movies and display-referred stills) where the four color stages are off.
"""

import os
import subprocess

# ---------------------------------------------------------------------------
# Configuration (canonical — qt_bake_oiio.py re-exports these)
# ---------------------------------------------------------------------------

SHOTS_ROOT = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots"

SHOW_LUT_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/_globals/LUT/"
    "260629/s3LUT/ARRILogC4_SEV_S3_V3_digital_p1s_R709.cube"
)

OIIOTOOL = "/opt/homebrew/bin/oiiotool"

# Point at the ffmpeg-full formula, NOT /opt/homebrew/bin/ffmpeg. The regular
# Homebrew 'ffmpeg' formula is built WITHOUT freetype, so it lacks the
# 'drawtext' filter the slate and burn-ins require.
FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"

OCIO_CONFIG = "ocio://studio-config-latest"

# Use the config's SPACE-FREE aliases, not the display names. oiiotool
# tokenizes positional colorspace arguments on whitespace, so a name like
# "ARRI LogC4" is misread as two arguments ("ARRI" + "LogC4").
#
# NOTE on the LogC4 step: arri_logc4 is the FULL "ARRI LogC4" colorspace
# (LogC4 curve + ARRI Wide Gamut 4 primaries), a deliberate choice that
# differs from the Nuke setup's curve-only "Input - ARRI - Curve - LogC4".
# If the show .cube LUT expects curve-on-AP1, revisit this.
OCIO_ACESCG = "ACEScg"        # alias of "ACEScg" (no space anyway)
OCIO_LOGC4 = "arri_logc4"     # alias of "ARRI LogC4"

# Fallback when the show LUT is missing or its stage is off: the Studio config
# expresses Rec.709 output as a display + view. These contain spaces and the
# config provides no space-free aliases, so they are passed as separate
# trailing --ociodisplay arguments.
OCIO_REC709_DISPLAY = "Gamma 2.2 Rec.709 - Display"
OCIO_REC709_VIEW = "ACES 2.0 - SDR 100 nits (Rec.709)"

# Fixed final delivery size for ALL QTs. Every output is letterboxed/
# pillarboxed to exactly this, regardless of source resolution or squeeze.
DELIVERY_WIDTH = 1920
DELIVERY_HEIGHT = 1080


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

class Stage(object):
    """One switchable step of the bake pipeline."""

    def __init__(self, key, label, description, is_color):
        self.key = key
        self.label = label
        self.description = description
        self.is_color = is_color


PIPELINE_STAGES = [
    Stage(
        "input_transform",
        "ACEScg \u2192 LogC4",
        "Scene-linear ACEScg into ARRI LogC4. Turn off when the source is "
        "already log or display-referred.",
        True,
    ),
    Stage(
        "cdl",
        "Shot CDL (.cc)",
        "Per-shot creative grade from plates/{shot}.cc. Silently inert when "
        "no .cc exists for the shot.",
        True,
    ),
    Stage(
        "show_lut",
        "Show LUT \u2192 Rec.709",
        "The show .cube (LogC4 \u2192 Rec.709). Turn off to see the ungraded "
        "display transform instead.",
        True,
    ),
    Stage(
        "display_transform",
        "Rec.709 display transform",
        "Fallback LogC4 \u2192 Rec.709 display/view, applied only when the show "
        "LUT is off or missing. Off with the LUT also off delivers flat log.",
        True,
    ),
    Stage(
        "desqueeze",
        "Anamorphic de-squeeze",
        "Divide height by the source pixel aspect ratio. No-op on square "
        "pixels.",
        False,
    ),
    Stage(
        "fit",
        "Letterbox to %dx%d" % (DELIVERY_WIDTH, DELIVERY_HEIGHT),
        "Fit and pad every frame to the fixed delivery size. Off keeps the "
        "source resolution.",
        False,
    ),
]

STAGE_KEYS = [s.key for s in PIPELINE_STAGES]
COLOR_STAGE_KEYS = [s.key for s in PIPELINE_STAGES if s.is_color]


def default_stages(skip_color=False):
    """
    Stage map matching the pipeline's historical behaviour.

    skip_color sources (movies, display-referred stills) get the color stages
    off; geometry stages are always on by default.
    """
    color_on = not bool(skip_color)
    stages = {}
    for stage in PIPELINE_STAGES:
        stages[stage.key] = color_on if stage.is_color else True
    return stages


def normalize_stages(value, skip_color=False):
    """
    Coerce a (possibly partial, possibly None) stage map into a full one.

    Unknown keys are dropped and missing keys fall back to the default for
    this source type, so an old flag or a hand-edited one still bakes.
    """
    stages = default_stages(skip_color)
    if isinstance(value, dict):
        for key in STAGE_KEYS:
            if key in value:
                stages[key] = bool(value[key])
    return stages


def any_color_stage(stages):
    """True when at least one color stage is on (i.e. run the OCIO path)."""
    return any(stages.get(key) for key in COLOR_STAGE_KEYS)


def describe_stages(stages):
    """One-line 'A + B (C off)' summary for logs and the app's status pane."""
    on = [s.label for s in PIPELINE_STAGES if stages.get(s.key)]
    off = [s.label for s in PIPELINE_STAGES if not stages.get(s.key)]
    if not off:
        return "full pipe: %s" % " + ".join(on)
    if not on:
        return "all stages off (raw source)"
    return "%s (off: %s)" % (" + ".join(on), ", ".join(off))


def resolve_cdl_path(episode, sequence, shot_code, shots_root=None):
    """
    Expected per-shot CDL location: shots/{ep}/{seq}/{shot}/plates/{shot}.cc

    Returns the path whether or not it exists — callers report the miss so an
    ungraded bake is visible rather than silent.
    """
    if not shot_code:
        return None
    return os.path.join(
        shots_root or SHOTS_ROOT,
        str(episode or ""),
        str(sequence or ""),
        str(shot_code),
        "plates",
        "%s.cc" % shot_code,
    )


# ---------------------------------------------------------------------------
# oiiotool command construction
# ---------------------------------------------------------------------------

def build_stage_args(
    stages,
    cdl_path=None,
    desqueeze_to=None,
    fit_to=None,
    resize_to=None,
    show_lut_path=None,
):
    """
    Build the oiiotool arguments for the enabled stages, in pipeline order.

    cdl_path     : per-shot .cc, or None. Applied only if it exists on disk.
    desqueeze_to : (w, h) de-squeezed pixel size, or None for square pixels.
    fit_to       : (w, h) delivery size to letterbox into, or None.
    resize_to    : (w, h) unconditional final resize (slate thumbnails), applied
                   after fit and never gated by a stage toggle.
    """
    if show_lut_path is None:
        show_lut_path = SHOW_LUT_PATH
    args = []

    if stages.get("input_transform"):
        args += ["--colorconvert", OCIO_ACESCG, OCIO_LOGC4]

    if stages.get("cdl") and cdl_path and os.path.exists(cdl_path):
        args += ["--ociofiletransform", cdl_path]

    if stages.get("show_lut") and show_lut_path and os.path.exists(show_lut_path):
        args += ["--ociofiletransform", show_lut_path]
    elif stages.get("display_transform"):
        args += [
            "--ociodisplay:from=%s" % OCIO_LOGC4,
            OCIO_REC709_DISPLAY,
            OCIO_REC709_VIEW,
        ]

    if stages.get("desqueeze") and desqueeze_to is not None:
        args += ["--resize:filter=lanczos3", "%dx%d" % desqueeze_to]

    if stages.get("fit") and fit_to is not None:
        args += ["--fit:filter=lanczos3:pad=1", "%dx%d" % fit_to]

    if resize_to is not None:
        args += ["--resize:filter=lanczos3", "%dx%d" % resize_to]

    return args


def build_frame_command(
    src_path,
    dst_path,
    stages,
    cdl_path=None,
    desqueeze_to=None,
    fit_to=None,
    resize_to=None,
    oiiotool=None,
    force_uint8=False,
):
    """
    Full oiiotool command for one frame.

    The OCIO config must be set ONCE, up front, via the top-level
    --colorconfig flag; it is NOT a valid modifier on --colorconvert or
    --ociofiletransform. With every color stage off there is nothing for OCIO
    to do, so the config is left out entirely and the frame is a straight
    resize/convert.
    """
    cmd = [oiiotool or OIIOTOOL]
    if any_color_stage(stages):
        cmd += ["--colorconfig", OCIO_CONFIG]
    cmd.append(src_path)
    cmd += build_stage_args(
        stages,
        cdl_path=cdl_path,
        desqueeze_to=desqueeze_to,
        fit_to=fit_to,
        resize_to=resize_to,
    )
    # --clamp takes min=/max= as colon-appended MODIFIERS, not positional args:
    # "--clamp 0 1" makes oiiotool read 0 and 1 as input filenames.
    if any_color_stage(stages):
        cmd += ["--clamp:min=0:max=1"]
    cmd += ["--ch", "R,G,B"]
    if force_uint8 or not any_color_stage(stages):
        cmd += ["-d", "uint8"]
    cmd += ["-o", dst_path]
    return cmd


# ---------------------------------------------------------------------------
# Source inspection
# ---------------------------------------------------------------------------

def frame_path(pattern, frame_num):
    """
    Expand a frame pattern to an actual filename. Supports both #### and
    %04d style tokens (the flag's exr_path_pattern uses %04d).
    """
    if not pattern:
        return ""
    if "####" in pattern:
        return pattern.replace("####", "%04d" % frame_num)
    if "%04d" in pattern:
        return pattern % frame_num
    return pattern


def _info_lines(path, oiiotool=None, verbose=True):
    cmd = [oiiotool or OIIOTOOL, "--info"]
    if verbose:
        cmd.append("-v")
    cmd.append(path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.splitlines()


def read_pixel_aspect(path, oiiotool=None):
    """
    PixelAspectRatio from image metadata (e.g. 2.0 for a 2:1 squeeze).

    Returns 1.0 when absent or unreadable, i.e. treat as non-anamorphic.
    """
    try:
        for line in _info_lines(path, oiiotool):
            if "pixelaspectratio" in line.lower():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    try:
                        par = float(parts[1].strip())
                        if par > 0:
                            return par
                    except ValueError:
                        pass
    except Exception as exc:
        print("[color_pipeline] WARNING: could not read PixelAspectRatio: %s" % exc)
    return 1.0


def read_resolution(path, oiiotool=None):
    """Pixel (width, height) of an image, or (None, None) if unreadable."""
    try:
        import re

        for line in _info_lines(path, oiiotool, verbose=False):
            # Typical: "<path> :  1920 x 1080, 4 channel, half openexr"
            m = re.search(r"(\d+)\s*x\s*(\d+)", line)
            if m:
                return int(m.group(1)), int(m.group(2))
    except Exception as exc:
        print("[color_pipeline] WARNING: could not read resolution: %s" % exc)
    return (None, None)


def desqueeze_size(src_w, src_h, par):
    """
    De-squeezed pixel size for an anamorphic source, or None when the source
    is square-pixel / unmeasurable. Height is divided by PAR, width is kept.
    """
    if not src_w or not src_h or not par:
        return None
    if abs(par - 1.0) <= 1e-3:
        return None
    return (int(src_w), int(round(src_h / par)))


def find_executable(configured, names):
    """
    Locate a tool: the configured absolute path first, then PATH, then the
    usual Homebrew prefixes. Returns None when nothing is usable.

    The bake host is a known Mac Studio, but the drop app runs on artist
    workstations where Homebrew may live elsewhere (or not at all).
    """
    import shutil

    candidates = [configured]
    for name in names:
        candidates.append(shutil.which(name))
        candidates.append("/opt/homebrew/bin/%s" % name)
        candidates.append("/usr/local/bin/%s" % name)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def find_oiiotool():
    return find_executable(OIIOTOOL, ["oiiotool"])


def find_ffmpeg():
    return find_executable(FFMPEG, ["ffmpeg"])
