"""
color_plan.py — resolve the full per-shot pipeline:

    ACES (camera IDT) -> Shot CDL (.cc/.ccc) -> Show LUT (per-shot, else
    show-wide fallback)

into one ColorPlan the Resolve bridge can apply without knowing anything
about how any of the three stages were found on disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from resolve_bridge import camera_color, cdl as cdl_module, config
from resolve_bridge.qt_bake_import import get_module as _get_qt_bake_module


@dataclass
class ShotContext:
    """Everything color_plan needs to know about one shot/version."""

    shot_code: str
    episode: str = ""
    sequence: str = ""
    camera_field: str = ""          # e.g. ShotGrid Shot.sg_camera, free text
    plate_path: str = ""            # representative frame, for camera/EXR
                                     # metadata detection and LUT/CDL lookup
    oiiotool: str = "oiiotool"


@dataclass
class ColorPlan:
    shot_code: str
    camera_family: str
    aces_idt: str
    cdl_path: Optional[str] = None
    cdl_values: Optional[cdl_module.CDLValues] = None
    lut_path: Optional[str] = None
    warnings: list = field(default_factory=list)

    @property
    def has_cdl(self) -> bool:
        return bool(self.cdl_values)

    @property
    def has_lut(self) -> bool:
        # Existence is already verified once, at resolution time, by
        # resolve_color_plan() / qt_bake_oiio.resolve_lut_path() - re-check
        # here only that a path was actually resolved, not the filesystem
        # again, so callers (e.g. resolve_api.apply_color_plan) can be
        # exercised against a ColorPlan built directly in a test without
        # needing a real file on disk.
        return bool(self.lut_path)


def _shot_plates_dir(shot: ShotContext) -> Optional[str]:
    if not (shot.shot_code and shot.episode and (shot.sequence)):
        return None
    return os.path.join(
        config.SHOTS_ROOT, str(shot.episode), str(shot.sequence), shot.shot_code,
        "plates",
    )


def _resolve_show_lut(shot: ShotContext) -> tuple:
    """
    Delegate to qt_bake_oiio.resolve_lut_path() when it's importable, so the
    Show LUT this tool picks can never disagree with what the QT Watcher
    bakes for the same shot. Returns (lut_path_or_None, note_str).
    """
    qt_bake = _get_qt_bake_module()
    if qt_bake is not None:
        data = {
            "type": "shot",
            "shot_code": shot.shot_code,
            "episode": shot.episode,
            "sequence": shot.sequence,
        }
        lut_path = qt_bake.resolve_lut_path(data)
        if lut_path is None and os.path.exists(qt_bake.SHOW_LUT_PATH):
            return qt_bake.SHOW_LUT_PATH, "show LUT fallback (qt_bake_oiio)"
        if lut_path:
            return lut_path, "per-shot LUT (qt_bake_oiio)"
        return None, "no LUT found by qt_bake_oiio and no show LUT on disk"

    # ../scripts not importable — do the equivalent lookup by hand using the
    # defaults in config.py, so this package still works away from a full
    # pipeline checkout, at the cost of not sharing qt_bake_oiio's exact
    # extension-priority tie-break logic for ambiguous plates/ folders.
    plates_dir = _shot_plates_dir(shot)
    if plates_dir and os.path.isdir(plates_dir):
        for ext in (".cube", ".lut"):
            candidate = os.path.join(plates_dir, "%s%s" % (shot.shot_code, ext))
            if os.path.exists(candidate):
                return candidate, "per-shot LUT (fallback lookup, qt_bake_oiio not importable)"
    if os.path.exists(config.SHOW_LUT_PATH):
        return config.SHOW_LUT_PATH, "show LUT fallback (fallback lookup, qt_bake_oiio not importable)"
    return None, "no LUT found (fallback lookup, qt_bake_oiio not importable)"


def resolve_color_plan(shot: ShotContext) -> ColorPlan:
    """Build the full ColorPlan for one shot. Never raises for a missing
    CDL/LUT (those are optional stages, logged as warnings) — only raises
    for an unmapped camera family or an unparsable CDL file, both of which
    mean "this would grade wrong", not "this stage is simply absent"."""
    warnings = []

    camera_family = camera_color.detect_camera_family(
        shot_camera_field=shot.camera_field,
        plate_path=shot.plate_path,
        exr_metadata_path=shot.plate_path,
        oiiotool=shot.oiiotool,
    )
    aces_idt = camera_color.aces_idt_for_family(camera_family)

    plates_dir = _shot_plates_dir(shot)
    cdl_path = cdl_module.find_shot_cdl(plates_dir, shot.shot_code)
    cdl_values = None
    if cdl_path:
        try:
            cdl_values = cdl_module.parse_cdl(cdl_path)
            print("[resolve_bridge.color_plan] %s: CDL %s" % (shot.shot_code, cdl_path))
        except ValueError as exc:
            raise ValueError(
                "%s: found CDL %s but could not parse it: %s"
                % (shot.shot_code, cdl_path, exc)
            )
    else:
        msg = "%s: no per-shot CDL found under %s — baking UNGRADED" % (
            shot.shot_code, plates_dir,
        )
        print("[resolve_bridge.color_plan] %s" % msg)
        warnings.append(msg)

    lut_path, lut_note = _resolve_show_lut(shot)
    print("[resolve_bridge.color_plan] %s: %s (%s)" % (shot.shot_code, lut_path, lut_note))
    if not lut_path:
        warnings.append("%s: %s" % (shot.shot_code, lut_note))

    return ColorPlan(
        shot_code=shot.shot_code,
        camera_family=camera_family,
        aces_idt=aces_idt,
        cdl_path=cdl_path,
        cdl_values=cdl_values,
        lut_path=lut_path,
        warnings=warnings,
    )
