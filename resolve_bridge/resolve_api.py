"""
resolve_api.py — thin wrapper around DaVinci Resolve's scripting API:
connect, build a bin + timeline for a Playlist, import media, and apply a
ColorPlan (ACES IDT, Shot CDL, Show LUT) to each clip.

Requires DaVinci Resolve STUDIO (the scripting API does not exist in the
free edition) running locally with "External scripting using" enabled in
Preferences > System > General, and Resolve's Python modules on
sys.path — see resolve_bridge/README.md for the exact environment
variables. Nothing in this module runs at import time, so it's safe to
import in tests without Resolve installed; connect() is where a missing
Resolve would fail.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

from resolve_bridge import config
from resolve_bridge.color_plan import ColorPlan


class ResolveConnectionError(RuntimeError):
    pass


class GradeApplyError(RuntimeError):
    pass


def _platform_key() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def connect_resolve(logger=print):
    """
    Import DaVinciResolveScript and connect to a running Resolve instance.

    Path resolution order for both the API folder and the fusionscript
    library: RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB env vars (Blackmagic's
    own documented override), then the per-platform default install
    location in config.py. Raises ResolveConnectionError with the checked
    paths on any failure — never returns None.
    """
    platform_key = _platform_key()
    api_path = _expand(
        os.environ.get("RESOLVE_SCRIPT_API", config.RESOLVE_SCRIPT_API_DEFAULTS[platform_key])
    )
    lib_path = _expand(
        os.environ.get("RESOLVE_SCRIPT_LIB", config.RESOLVE_SCRIPT_LIB_DEFAULTS[platform_key])
    )

    modules_path = os.path.join(api_path, "Modules")
    if modules_path not in sys.path:
        sys.path.append(modules_path)
    os.environ.setdefault("RESOLVE_SCRIPT_API", api_path)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib_path)

    try:
        import DaVinciResolveScript as dvr_script
    except ImportError as exc:
        raise ResolveConnectionError(
            "could not import DaVinciResolveScript from %s (%s). Confirm "
            "DaVinci Resolve STUDIO is installed and RESOLVE_SCRIPT_API / "
            "RESOLVE_SCRIPT_LIB point at it — see resolve_bridge/README.md."
            % (modules_path, exc)
        )

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        raise ResolveConnectionError(
            "DaVinciResolveScript imported but scriptapp('Resolve') returned "
            "None — is Resolve running, and is 'External scripting using' "
            "set to Local/Network in Preferences > System > General?"
        )
    logger("[resolve_bridge.resolve_api] connected to DaVinci Resolve")
    return resolve


def ensure_project(resolve, project_name: str, logger=print):
    """Open project_name if it exists in the current database, else create
    it. Returns the Project object with project_name now the current one."""
    pm = resolve.GetProjectManager()
    project = pm.LoadProject(project_name)
    if project:
        logger("[resolve_bridge.resolve_api] opened existing project '%s'" % project_name)
        return project
    project = pm.CreateProject(project_name)
    if not project:
        raise ResolveConnectionError("could not open or create project '%s'" % project_name)
    logger("[resolve_bridge.resolve_api] created project '%s'" % project_name)
    return project


def configure_aces_color_management(project, logger=print) -> bool:
    """
    Switch the project into ACES color management per config.py.

    colorScienceMode MUST be set before anything else — Resolve applies
    color-management project settings in the order they're set, and
    changing the science mode after other color settings silently discards
    them (matches the "critical: set first" note upstream tooling has
    hit — see resolve_bridge/README.md). Returns True only if every setting
    in the sequence reported success; logs each one either way so a partial
    failure (e.g. an ACES version this Resolve build doesn't have) is
    visible rather than silently leaving the project half-configured.
    """
    settings = [
        ("colorScienceMode", config.RESOLVE_COLOR_SCIENCE_MODE),
        ("acesOutputTransform", config.RESOLVE_ACES_OUTPUT_TRANSFORM),
    ]
    all_ok = True
    for key, value in settings:
        ok = bool(project.SetSetting(key, value))
        logger(
            "[resolve_bridge.resolve_api] %s SetSetting(%s, %s) = %s"
            % ("OK " if ok else "FAIL", key, value, ok)
        )
        all_ok = all_ok and ok
        # Give Resolve's UI/settings machinery a beat between the science
        # mode switch and anything that depends on it being live.
        if key == "colorScienceMode":
            time.sleep(0.1)
    return all_ok


def ensure_bin(media_pool, name: str, logger=print):
    """Get-or-create a top-level Media Pool bin named `name`."""
    root = media_pool.GetRootFolder()
    for sub in root.GetSubFolderList():
        if sub.GetName() == name:
            logger("[resolve_bridge.resolve_api] reusing bin '%s'" % name)
            media_pool.SetCurrentFolder(sub)
            return sub
    bin_folder = media_pool.AddSubFolder(root, name)
    logger("[resolve_bridge.resolve_api] created bin '%s'" % name)
    media_pool.SetCurrentFolder(bin_folder)
    return bin_folder


def import_media(media_pool, path: str, logger=print):
    """
    Import one clip (single file or a %04d/#### sequence pattern Resolve's
    ImportMedia understands) into the current Media Pool folder.

    Returns the MediaPoolItem, or raises GradeApplyError — an import that
    silently fails would otherwise leave a gap in the timeline with no clip
    to grade and no obvious cause.
    """
    items = media_pool.ImportMedia([path])
    if not items:
        raise GradeApplyError("ImportMedia returned nothing for %s" % path)
    logger("[resolve_bridge.resolve_api] imported %s" % path)
    return items[0]


def _node_target(timeline_item):
    """
    Return whatever object actually carries SetLUT/SetCDL/GetNumNodes on
    this Resolve build.

    Some documented API versions expose these directly on TimelineItem;
    others expose a separate node-graph object via GetNodeGraph(). Prefer
    the node-graph object when present since it's the more specific,
    forward-looking shape; fall back to the TimelineItem itself so this
    keeps working on Resolve builds that only have the flat API.
    """
    graph_getter = getattr(timeline_item, "GetNodeGraph", None)
    if callable(graph_getter):
        graph = graph_getter()
        if graph is not None:
            return graph
    return timeline_item


def tag_input_color_space(media_pool_item, aces_idt: str, logger=print) -> bool:
    """Set the clip's ACES IDT via the one clip property Resolve exposes for
    it. See config.py CAMERA_ACES_IDT for the "verify against the live
    dropdown" caveat — a False here almost always means the string didn't
    match Resolve's dropdown exactly, not a connection problem."""
    ok = bool(media_pool_item.SetClipProperty("Input Color Space", aces_idt))
    logger(
        "[resolve_bridge.resolve_api] %s SetClipProperty('Input Color Space', %s) = %s"
        % ("OK " if ok else "FAIL", aces_idt, ok)
    )
    return ok


@dataclass
class GradeResult:
    shot_code: str
    input_color_space_ok: bool = False
    cdl_ok: Optional[bool] = None
    lut_ok: Optional[bool] = None
    node_count: int = 0
    strategy: str = ""
    warnings: List[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def apply_color_plan(
    timeline_item,
    media_pool_item,
    plan: ColorPlan,
    powergrade_name: str = "",
    bake_combined_lut_fallback: bool = False,
    logger=print,
) -> GradeResult:
    """
    Apply plan.aces_idt / plan.cdl_values / plan.lut_path to one clip.

    Node-graph strategy (see config.py POWERGRADE_TEMPLATE_PATH):
      - powergrade_name given: apply it first via ApplyGradeFromDRX, then
        target node 1 for the CDL and node 2 for the Show LUT. This is the
        only strategy that keeps both stages independently visible/editable
        in Resolve afterwards.
      - no powergrade and >= 2 nodes already on the clip (e.g. a template
        project default): same two-node targeting, no ApplyGradeFromDRX
        needed.
      - exactly 1 node available: CDL goes on it (grading always wins over
        display-only fallbacks); the Show LUT is dropped UNLESS
        bake_combined_lut_fallback resolves a combined LUT for this plan
        (see resolve_bridge/lut_bake.py) — logged as a warning either way,
        never silently.
    """
    result = GradeResult(shot_code=plan.shot_code)

    result.input_color_space_ok = tag_input_color_space(
        media_pool_item, plan.aces_idt, logger=logger
    )

    if powergrade_name:
        applied = bool(timeline_item.ApplyGradeFromDRX(powergrade_name, 0))
        logger(
            "[resolve_bridge.resolve_api] %s ApplyGradeFromDRX(%s)"
            % ("OK " if applied else "FAIL", powergrade_name)
        )
        if not applied:
            result.warnings.append(
                "PowerGrade '%s' failed to apply — falling back to whatever "
                "nodes this clip already has" % powergrade_name
            )

    node_target = _node_target(timeline_item)
    node_count = node_target.GetNumNodes() if hasattr(node_target, "GetNumNodes") else 1
    result.node_count = node_count

    if node_count >= 2:
        result.strategy = "two-node (CDL -> node 1, Show LUT -> node 2)"
        if plan.has_cdl:
            result.cdl_ok = bool(node_target.SetCDL(plan.cdl_values.to_resolve_cdl_map(1)))
        else:
            result.warnings.append("no CDL for %s — node 1 left as-is" % plan.shot_code)
        if plan.has_lut:
            result.lut_ok = bool(node_target.SetLUT(2, plan.lut_path))
        else:
            result.warnings.append("no Show LUT for %s — node 2 left as-is" % plan.shot_code)
    else:
        result.strategy = "single-node fallback"
        combined_lut = None
        if bake_combined_lut_fallback and plan.has_cdl and plan.has_lut:
            from resolve_bridge.lut_bake import bake_combined_lut  # local import: optional OCIO dep
            combined_lut = bake_combined_lut(plan)
        if combined_lut:
            result.lut_ok = bool(node_target.SetLUT(1, combined_lut))
            result.strategy = "single-node baked CDL+LUT combined cube"
        elif plan.has_cdl:
            result.cdl_ok = bool(node_target.SetCDL(plan.cdl_values.to_resolve_cdl_map(1)))
            if plan.has_lut:
                result.warnings.append(
                    "only 1 grading node available on %s — CDL applied, Show "
                    "LUT dropped. Configure config.POWERGRADE_TEMPLATE_PATH "
                    "for a 2-node stack." % plan.shot_code
                )
        elif plan.has_lut:
            result.lut_ok = bool(node_target.SetLUT(1, plan.lut_path))
        else:
            result.warnings.append("no CDL and no LUT for %s" % plan.shot_code)

    for warning in result.warnings:
        logger("[resolve_bridge.resolve_api] WARNING: %s" % warning)
    return result


def build_playlist_timeline(
    project,
    media_pool,
    timeline_name: str,
    clip_media_pool_items: list,
    logger=print,
):
    """Create a new timeline named timeline_name and append clips to it, in
    the order given (the Playlist's own order — callers must not re-sort)."""
    timeline = media_pool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        raise GradeApplyError("CreateEmptyTimeline('%s') failed" % timeline_name)
    project.SetCurrentTimeline(timeline)
    appended = media_pool.AppendToTimeline(clip_media_pool_items)
    if not appended:
        raise GradeApplyError(
            "AppendToTimeline failed for %d clip(s) on timeline '%s'"
            % (len(clip_media_pool_items), timeline_name)
        )
    logger(
        "[resolve_bridge.resolve_api] timeline '%s': appended %d clip(s)"
        % (timeline_name, len(appended))
    )
    return timeline, appended
