"""
camera_color.py — which ACES IDT a shot needs (ARRI LogC4/Wide Gamut 4 vs
RED Log3G10/REDWideGamutRGB), and how that decision gets made.

Detection is priority-ordered and every branch logs which one fired and why
- silently guessing the wrong camera family is a wrong grade on the whole
shot, not a cosmetic bug, so this never picks a default without saying so.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from resolve_bridge import config


class UnknownCameraFamilyError(ValueError):
    pass


def _match_keyword(text: str) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()
    for family, keywords in config.CAMERA_FAMILY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in lowered:
                return family
    return None


def detect_from_text(*candidates: str) -> Optional[str]:
    """First keyword match across any number of free-text fields, in order."""
    for candidate in candidates:
        family = _match_keyword(candidate or "")
        if family:
            return family
    return None


def detect_from_exr_metadata(exr_path: str, oiiotool: str = "oiiotool") -> Optional[str]:
    """
    Read camera make/model out of an EXR header via `oiiotool --info -v`.

    Returns None (never raises) on anything short of a clean match -
    missing tool, unreadable file, no camera metadata present - callers fall
    through to the next detection strategy rather than failing the whole
    lookup over an optional signal.
    """
    if not exr_path or not os.path.exists(exr_path):
        return None
    try:
        result = subprocess.run(
            [oiiotool, "--info", "-v", exr_path],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    relevant_keys = ("make", "model", "cameramake", "cameramodel", "camera")
    for line in result.stdout.splitlines():
        lowered = line.strip().lower()
        if ":" not in lowered:
            continue
        key, _, value = lowered.partition(":")
        key = key.strip()
        if any(rk in key for rk in relevant_keys):
            family = _match_keyword(value)
            if family:
                return family
    return None


def detect_camera_family(
    shot_camera_field: str = "",
    plate_path: str = "",
    exr_metadata_path: str = "",
    oiiotool: str = "oiiotool",
    default: Optional[str] = None,
) -> str:
    """
    Resolve which ACES IDT family a shot uses.

    Priority:
      1. shot_camera_field — explicit value from ShotGrid (e.g.
         Shot.sg_camera / Shot.sg_camera_manufacturer), keyword-matched.
      2. plate_path — the plate filename/path itself, in case the vendor or
         camera roll naming carries it (e.g. ".../A001_C002_..._RED_...").
      3. exr_metadata_path — embedded EXR "Make"/"Model" header, read via
         oiiotool. Only fires when the caller has a concrete frame path on
         disk AND oiiotool available; both are optional so this still works
         on a machine with no mounted volume (SG-field/filename detection
         only).
      4. default (falls back to config.DEFAULT_CAMERA_FAMILY) — loud, not
         silent: always logged so a wrong default doesn't quietly bake.

    Raises UnknownCameraFamilyError if the resolved family (from any source,
    including the default) isn't a family this tool knows an ACES IDT for -
    better to stop than push a clip with an unmapped colorspace.
    """
    default = default if default is not None else config.DEFAULT_CAMERA_FAMILY

    family = detect_from_text(shot_camera_field)
    if family:
        print(
            "[resolve_bridge.camera_color] camera family '%s' from ShotGrid "
            "field %r" % (family, shot_camera_field)
        )
    else:
        family = detect_from_text(plate_path)
        if family:
            print(
                "[resolve_bridge.camera_color] camera family '%s' from plate "
                "path %r" % (family, plate_path)
            )

    if not family and exr_metadata_path:
        family = detect_from_exr_metadata(exr_metadata_path, oiiotool=oiiotool)
        if family:
            print(
                "[resolve_bridge.camera_color] camera family '%s' from EXR "
                "metadata %r" % (family, exr_metadata_path)
            )

    if not family:
        family = default
        print(
            "[resolve_bridge.camera_color] WARNING: could not determine "
            "camera family from ShotGrid field, plate path, or EXR metadata "
            "— falling back to default '%s'. Verify this is correct; a wrong "
            "camera family means the wrong ACES IDT for the whole shot."
            % family
        )

    if family not in config.CAMERA_ACES_IDT:
        raise UnknownCameraFamilyError(
            "camera family '%s' has no ACES IDT mapping in "
            "config.CAMERA_ACES_IDT (%s)"
            % (family, sorted(config.CAMERA_ACES_IDT))
        )
    return family


def aces_idt_for_family(family: str) -> str:
    """Resolve 'Input Color Space' string for a camera family. See config.py
    for the verify-against-the-live-dropdown caveat before trusting this."""
    try:
        return config.CAMERA_ACES_IDT[family]
    except KeyError:
        raise UnknownCameraFamilyError(
            "camera family '%s' has no ACES IDT mapping in "
            "config.CAMERA_ACES_IDT (%s)" % (family, sorted(config.CAMERA_ACES_IDT))
        )
