"""
config.py — every studio-specific / version-specific string this tool needs.

Two categories of constant live here, and they fail very differently:

  1. Paths and ShotGrid field names — wrong values fail loud and immediately
     (a lookup returns nothing, a template raises).
  2. DaVinci Resolve UI strings (ACES IDT names, colorScienceMode values) —
     wrong values fail QUIET. `MediaPoolItem.SetClipProperty()` and
     `Project.SetSetting()` both just return False on an unrecognised value;
     Resolve does not raise, and the clip is silently left on whatever
     colorspace it had before. Every value in CAMERA_ACES_IDT below MUST be
     verified against the live dropdown in Resolve's Color page (Clip
     Attributes / Camera Metadata palette -> RAW/ACES tab) on the exact
     Resolve version and ACES preset this show uses before relying on it —
     see resolve_bridge/README.md "Verify the ACES IDT strings" for the
     step-by-step. This mirrors the existing show pipe's own philosophy
     (../scripts/qt_bake_oiio.py's OCIO_REC709_DISPLAY/VIEW comments): ship a
     documented best guess, but say loudly that it is a guess until checked.
"""

import os

# ---------------------------------------------------------------------------
# Shared with the existing QT Watcher show pipe (../scripts/qt_bake_oiio.py)
# ---------------------------------------------------------------------------
# Kept here too (rather than only importing) so this package still has
# sane defaults if ../scripts ever isn't importable (e.g. run from a laptop
# that only has this folder checked out) - color_pipeline.py prefers the
# live values from qt_bake_oiio when it can import it, exactly like
# ../drop_app/preview.py already does.
SHOTS_ROOT = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots"
SHOW_LUT_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/_globals/LUT/"
    "260629/s3LUT/ARRILogC4_SEV_S3_V3_digital_p1s_R709.cube"
)

# ---------------------------------------------------------------------------
# ShotGrid / Flow connection
# ---------------------------------------------------------------------------
# Prefer sgtk (Toolkit) bootstrap against the pipeline config, exactly like
# ../scripts/qt_watcher.py, so plate paths resolve through the same
# templates. Set SG_TOOLKIT_CONFIG_PATH to override; falls back to raw
# shotgun_api3 with a script key (SG_SITE / SG_SCRIPT_NAME / SG_SCRIPT_KEY)
# for use away from a Toolkit-configured machine.
TOOLKIT_CONFIG_PATH = os.environ.get(
    "SG_TOOLKIT_CONFIG_PATH",
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/current",
)
SG_SITE = os.environ.get("SG_SITE", "")
SG_SCRIPT_NAME = os.environ.get("SG_SCRIPT_NAME", "")
SG_SCRIPT_KEY = os.environ.get("SG_SCRIPT_KEY", "")

# ---------------------------------------------------------------------------
# Camera family detection
# ---------------------------------------------------------------------------
# Keyword match against whatever free-text camera field/metadata is
# available (ShotGrid Shot.sg_camera, EXR "Make"/"cameraMake" header, plate
# filename). First match wins - order matters only in that ARRI and RED
# strings never collide, so it doesn't actually matter here, but keep this
# list ordered by how often each shows up on this show if that changes.
CAMERA_FAMILY_KEYWORDS = {
    "arri": ("arri", "alexa", "amira"),
    "red": ("red", "dragon", "monstro", "helium", "gemini", "komodo", "v-raptor", "vraptor"),
}

# Camera family assumed when nothing on the Shot/plate identifies one.
# Loud by design (see resolve_color_plan()) - never silently guess without
# saying so in the log.
DEFAULT_CAMERA_FAMILY = os.environ.get("RESOLVE_DEFAULT_CAMERA_FAMILY", "arri")

# ---------------------------------------------------------------------------
# ACES IDT selection in DaVinci Resolve — VERIFY BEFORE TRUSTING (see above)
# ---------------------------------------------------------------------------
# Values are what gets passed to:
#   mediaPoolItem.SetClipProperty("Input Color Space", <value>)
# with the project already switched into an ACES colorScienceMode (see
# RESOLVE_COLOR_SCIENCE_MODE below). Resolve's ACES Transform dropdown lists
# combined gamut+curve names (unlike the raw-gamut-only strings you get in
# Resolve Color Management mode, e.g. plain "REDWideGamutRGB") - these are
# the combined forms.
CAMERA_ACES_IDT = {
    "arri": os.environ.get("RESOLVE_ACES_IDT_ARRI", "ARRI LogC4"),
    "red": os.environ.get("RESOLVE_ACES_IDT_RED", "REDWideGamutRGB/Log3G10"),
}

# Project-level ACES setup. colorScienceMode enables ACES color management;
# acesVersion / acesInputTransform (project-default IDT before per-clip
# overrides) / acesOutputTransform names below are last-verified against
# Resolve Studio 19/20's Project Settings > Color Management panel - RECHECK
# after any Resolve upgrade, same as the ffmpeg/oiiotool version checks in
# ../WATCHER_PREREQUISITES.md.
RESOLVE_COLOR_SCIENCE_MODE = os.environ.get("RESOLVE_COLOR_SCIENCE_MODE", "acescct")
RESOLVE_ACES_OUTPUT_TRANSFORM = os.environ.get(
    "RESOLVE_ACES_OUTPUT_TRANSFORM", "Rec.709 (ACES)"
)

# ---------------------------------------------------------------------------
# Node-graph grading strategy
# ---------------------------------------------------------------------------
# Resolve's scripting API cannot add/remove/reorder color nodes
# (TimelineItem.SetCDL / SetLUT only ADDRESS existing nodes by index). To get
# a clean [CDL node] -> [Show LUT node] stack per clip, apply a 2-node
# PowerGrade .drx FIRST (both nodes empty/passthrough), then target node 1
# for the CDL and node 2 for the LUT. Build that .drx once in Resolve's
# Color page (two empty serial nodes, right-click -> Grabs -> Save Grade As
# PowerGrade) and point this at it. Leave unset to fall back to whatever
# node count the clip already has - see resolve_bridge/resolve_api.py
# apply_color_plan() for exactly what happens at 1 node vs 2+.
POWERGRADE_TEMPLATE_PATH = os.environ.get("RESOLVE_CDL_LUT_POWERGRADE", "")

# When True and only a single grading node is available (no PowerGrade
# template configured), bake the per-shot CDL + Show LUT into one combined
# 3D LUT (via OpenColorIO's ociobakelut, see resolve_bridge/lut_bake.py) and
# push that as a single SetLUT call instead of dropping the Show LUT
# entirely. Off by default: baking happens in whatever color domain the CDL
# file was authored in, which may not match the domain Resolve's single
# remaining node actually operates in for this project - verify the result
# against a known-good frame before trusting it on a real show.
BAKE_COMBINED_LUT_FALLBACK = os.environ.get(
    "RESOLVE_BAKE_COMBINED_LUT_FALLBACK", ""
).lower() in ("1", "true", "yes")

OCIOBAKELUT = os.environ.get("OCIOBAKELUT_PATH", "ociobakelut")
OCIO_CONFIG = os.environ.get("RESOLVE_OCIO_CONFIG", "ocio://studio-config-latest")

# ---------------------------------------------------------------------------
# Resolve scripting bootstrap (per Blackmagic's own README.txt)
# ---------------------------------------------------------------------------
RESOLVE_SCRIPT_API_DEFAULTS = {
    "darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/",
    "win32": r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\\",
    "linux": "/opt/resolve/Developer/Scripting/",
}
RESOLVE_SCRIPT_LIB_DEFAULTS = {
    "darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Libraries/Fusion/fusionscript.so",
    "win32": r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Libraries\Fusion\fusionscript.dll",
    "linux": "/opt/resolve/libs/Fusion/fusionscript.so",
}

# ---------------------------------------------------------------------------
# Media resolution: what counts as "highest resolution available"
# ---------------------------------------------------------------------------
# Priority order when several candidates exist for the same shot. Plates
# (camera-original resolution) always outrank anything Flow/ShotGrid has a
# review encode of (the QT Watcher review movie is deliberately downscaled
# to 1920x1080 — see DELIVERY_WIDTH/HEIGHT in ../scripts/qt_bake_oiio.py —
# so it must never be preferred just because it's the field ShotGrid shows
# in the Playlist UI).
MEDIA_PRIORITY = ("plates", "sg_path_to_frames", "sg_path_to_movie", "sg_uploaded_movie")

PLATE_IMAGE_EXTENSIONS = (".exr", ".dpx", ".tif", ".tiff")
