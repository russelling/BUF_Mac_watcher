"""
lut_bake.py — optional: bake a shot's CDL + Show LUT into one combined 3D
cube LUT, for the single-grading-node fallback in resolve_api.apply_color_plan().

EXPERIMENTAL. Only used when config.BAKE_COMBINED_LUT_FALLBACK is on and no
2-node PowerGrade is configured. Baking happens via OpenColorIO's
`ociobakelut` CLI against the same OCIO config the QT Watcher show pipe uses
(see OCIO_CONFIG in ../scripts/qt_bake_oiio.py), applying the CDL as an
OCIO FileTransform ahead of the LUT FileTransform — same order as the show
pipe (CDL, then Show LUT). What this CANNOT guarantee is that the resulting
cube operates in the same colorspace Resolve's single remaining node
actually grades in for a given project (that depends on this project's ACES
setup and where in its pipeline that node sits) — verify against a
known-good reference frame before trusting this on a real show. Prefer
configuring a 2-node PowerGrade template instead; this exists for the
one-node clips that would otherwise lose the Show LUT entirely.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from resolve_bridge import config
from resolve_bridge.color_plan import ColorPlan


class LutBakeError(RuntimeError):
    pass


def bake_combined_lut(plan: ColorPlan, cube_size: int = 33, logger=print):
    """
    Bake plan.cdl_path -> plan.lut_path into one 3D .cube via ociobakelut.

    Returns the baked LUT's path, or None (never raises) if ociobakelut
    isn't available or the bake fails — this is an optional enhancement to
    a fallback path, so its own failure must not take down the whole push.
    """
    if not (plan.has_cdl and plan.has_lut):
        return None

    ociobakelut = config.OCIOBAKELUT
    fd, out_path = tempfile.mkstemp(prefix="resolve_bridge_combined_", suffix=".cube")
    os.close(fd)

    cmd = [
        ociobakelut,
        "--iconfig", config.OCIO_CONFIG,
        "--inputspace", "ACEScg",
        "--outputspace", "ACEScg",
        "--shapesize", str(cube_size),
        "--cdl", plan.cdl_path,
        out_path,
    ]
    # NOTE: ociobakelut's --cdl option applies a single FileTransform ahead
    # of the main input->output conversion; chaining the Show LUT on top of
    # that in one invocation isn't something ociobakelut's CLI exposes
    # directly, so the LUT stage is applied as a second pass over the CDL
    # bake's own output below.
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger("[resolve_bridge.lut_bake] ociobakelut unavailable/failed: %s" % exc)
        return None
    if result.returncode != 0:
        logger("[resolve_bridge.lut_bake] ociobakelut (CDL pass) failed: %s" % result.stderr)
        return None

    combined_path = out_path.replace(".cube", "_combined.cube")
    cmd2 = [
        ociobakelut,
        "--iconfig", config.OCIO_CONFIG,
        "--inputspace", "ACEScg",
        "--outputspace", "ACEScg",
        "--shapesize", str(cube_size),
        "--lut", out_path,
        "--lut", plan.lut_path,
        combined_path,
    ]
    try:
        result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger("[resolve_bridge.lut_bake] ociobakelut (LUT pass) failed: %s" % exc)
        return None
    if result2.returncode != 0:
        logger("[resolve_bridge.lut_bake] ociobakelut (LUT pass) failed: %s" % result2.stderr)
        return None

    logger(
        "[resolve_bridge.lut_bake] baked combined CDL+LUT cube for %s: %s"
        % (plan.shot_code, combined_path)
    )
    return combined_path
