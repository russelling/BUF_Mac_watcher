"""
staging.py — detect dropped media, copy into pipeline paths, write watcher flags.

Used by review_drop_app.py. Relies on Toolkit templates from the Flow
pipeline config (same bootstrap as qt_watcher.py).
"""
from __future__ import annotations

import getpass
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

MOVIE_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}
# Scene-linear / HDR stills — full ACEScg → show LUT color pipe.
LINEAR_IMAGE_EXTS = {".exr", ".hdr"}
# Display-referred / standard stills — baked with skip_color (no ACEScg grade).
DISPLAY_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp",
    ".dpx", ".cin", ".gif", ".webp", ".psd", ".iff", ".sxr",
}
IMAGE_EXTS = LINEAR_IMAGE_EXTS | DISPLAY_IMAGE_EXTS
# Basic 3D asset delivery formats routed to the ingest drop folder.
MODEL_3D_EXTS = {
    ".obj", ".fbx", ".glb", ".gltf", ".ply", ".stl", ".abc",
    ".usd", ".usdc", ".usda", ".usdz", ".max", ".blend",
}

SEQUENCE_RE = re.compile(
    r"^(?P<head>.*?)(?P<frame>\d{3,8})(?P<tail>\.[^.]+)$",
    re.IGNORECASE,
)


def classify_paths(paths: list[str]) -> dict:
    """
    Inspect dropped paths. Returns:
      media_type: 'exr_sequence' | 'exr_single' | 'image_sequence' |
                  'image_single' | 'movie' | 'model_3d' | 'mixed' | 'unknown'
      files: sorted list of Path
      frame_first / frame_last / pattern (for stills)
      movie_path (for movie)
      skip_color (True for movies + display-referred stills)
    """
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not child.name.startswith("."):
                    files.append(child)
        elif path.is_file():
            files.append(path)

    files = [
        f for f in files
        if f.suffix.lower() in IMAGE_EXTS | MOVIE_EXTS | MODEL_3D_EXTS
    ]
    if not files:
        return {"media_type": "unknown", "files": []}

    movies = [f for f in files if f.suffix.lower() in MOVIE_EXTS]
    images = [f for f in files if f.suffix.lower() in IMAGE_EXTS]
    models = [f for f in files if f.suffix.lower() in MODEL_3D_EXTS]

    if models and not movies and not images:
        return {
            "media_type": "model_3d",
            "files": models,
        }

    if movies and not images and not models:
        return {
            "media_type": "movie",
            "files": movies,
            "movie_path": str(movies[0]),
            "frame_first": 1001,
            "frame_last": 1001,
            "skip_color": True,
        }

    if images and not movies and not models:
        # One extension per drop — mixed PNG+JPG etc. is ambiguous for naming.
        exts = {f.suffix.lower() for f in images}
        if len(exts) > 1:
            return {"media_type": "mixed", "files": files}

        ext = next(iter(exts))
        pattern, first, last = _detect_image_sequence(images)
        is_linear = ext in LINEAR_IMAGE_EXTS
        if first == last and len(images) == 1:
            media = "exr_single" if is_linear else "image_single"
        else:
            media = "exr_sequence" if is_linear else "image_sequence"
        return {
            "media_type": media,
            "files": images,
            "exr_path_pattern": pattern,
            "frame_first": first,
            "frame_last": last,
            "skip_color": not is_linear,
            "image_ext": ext,
        }

    return {"media_type": "mixed", "files": files}


def _detect_image_sequence(images: list[Path]) -> tuple[str, int, int]:
    """Return (%04d pattern, first, last) for a list of still image paths."""
    parsed = []
    for f in images:
        m = SEQUENCE_RE.match(f.name)
        if not m:
            # Single unnumbered file
            return str(f), 1, 1
        frame = int(m.group("frame"))
        width = len(m.group("frame"))
        head = m.group("head")
        tail = m.group("tail")
        parsed.append((f.parent, head, width, frame, tail))

    # Group by directory + head + width + tail
    key = (parsed[0][0], parsed[0][1], parsed[0][2], parsed[0][4])
    frames = sorted(p[3] for p in parsed if (p[0], p[1], p[2], p[4]) == key)
    parent, head, width, _, tail = parsed[0]
    token = "%0{}d".format(width)
    pattern = str(parent / (head + token + tail))
    return pattern, frames[0], frames[-1]


def next_version(existing_dir: Path, stem_prefix: str) -> int:
    """Scan a directory for *_v### and return next version number."""
    if not existing_dir.exists():
        return 1
    versions = []
    for p in existing_dir.iterdir():
        m = re.search(r"_v(\d{3})", p.name)
        if m and stem_prefix in p.name:
            versions.append(int(m.group(1)))
    return (max(versions) + 1) if versions else 1


def stage_and_flag(
    tk: Any,
    sg: Any,
    media: dict,
    context: dict,
    include_slate: bool,
) -> Path:
    """
    Copy media into the pipeline render/work area and write a
    .render_complete_*.json flag for qt_watcher.

    context keys:
      entity_type: Shot | Asset
      entity: SG entity dict (id, code, ...)
      episode, sequence (shot)
      asset_type (asset)
      step, version, submitted_for, description
      user_id, artist (optional — Flow HumanUser for Version.user)
      project_id, task_id (optional)
    """
    project_id = context["project_id"]
    step = context["step"]
    version = int(context["version"])
    artist = context.get("artist") or getpass.getuser()
    today = datetime.now().strftime("%Y-%m-%d")

    is_shot = context["entity_type"] == "Shot"
    skip_color = bool(media.get("skip_color")) or media["media_type"] == "movie"

    if is_shot:
        episode = context["episode"]
        sequence = context["sequence"]
        shot_code = context["entity"]["code"]
        fields = {
            "Episode": episode,
            "Sequence": sequence,
            "Shot": shot_code,
            "Step": step,
            "version": version,
        }
        # Ensure folders exist
        try:
            tk.create_filesystem_structure("Shot", context["entity"]["id"])
        except Exception:
            pass

        # Derive shot root from a known movie template (absolute path).
        sample_mov = tk.templates["ep_nuke_shot_render_movie"].apply_fields(fields)
        shot_root = Path(sample_mov).parent.parent  # .../{Shot}
        leaf = "%s_%s_v%03d" % (shot_code, step, version)
        dest_dir = shot_root / "render" / "work" / leaf
        dest_dir.mkdir(parents=True, exist_ok=True)
        _archive_originals(media, shot_root / "source" / "review_drop" / leaf)

        if media["media_type"] == "movie":
            src = Path(media["movie_path"])
            dest = dest_dir / ("%s%s" % (leaf, src.suffix.lower()))
            shutil.copy2(src, dest)
            movie_path = str(dest)
            exr_pattern = ""
            frame_first = media.get("frame_first", 1001)
            frame_last = media.get("frame_last", 1001)
        else:
            movie_path = None
            frame_first = media["frame_first"]
            frame_last = media["frame_last"]
            exr_pattern = _copy_frames(media, dest_dir, leaf)

        flag_name = ".render_complete_%s.json" % leaf
        flag_path = dest_dir / flag_name

        flag_data = {
            "type": "shot",
            "project_id": project_id,
            "entity_type": "Shot",
            "entity_id": context["entity"]["id"],
            "shot_code": shot_code,
            "episode": episode,
            "sequence": sequence,
            "scene": sequence,  # legacy bake scripts
            "step": step,
            "version": version,
            "output": None,
            "artist": artist,
            "user_id": context.get("user_id"),
            "date": today,
            "frame_first": frame_first,
            "frame_last": frame_last,
            "start_timecode": context.get("start_timecode"),
            "exr_path_pattern": exr_pattern,
            "movie_path": movie_path,
            "skip_color": skip_color,
            "include_slate": include_slate,
            "submitted_for": context.get("submitted_for") or "Internal Review",
            "description": context.get("description") or "Review Drop",
            "task_id": context.get("task_id"),
            "source": "review_drop_app",
        }
    else:
        asset_code = context["entity"]["code"]
        asset_type = context["asset_type"]
        fields = {
            "Asset": asset_code,
            "sg_asset_type": asset_type,
            "version": version,
        }
        try:
            tk.create_filesystem_structure("Asset", context["entity"]["id"])
        except Exception:
            pass

        leaf = "%s_%s_v%03d" % (asset_code, step, version)
        # Turntable step uses the canonical turntable render folder so the
        # existing watcher/movie templates resolve correctly.
        if step == "turntable" and tk.templates.get("unreal_asset_turntable_render"):
            sample = tk.templates["unreal_asset_turntable_render"].apply_fields(fields)
            dest_dir = Path(sample).parent
        else:
            dest_dir = (
                Path(tk.templates["asset_root"].apply_fields(fields))
                / "render"
                / "work"
                / leaf
            )
        dest_dir.mkdir(parents=True, exist_ok=True)

        if media["media_type"] == "movie":
            src = Path(media["movie_path"])
            dest = dest_dir / ("%s%s" % (leaf, src.suffix.lower()))
            shutil.copy2(src, dest)
            movie_path = str(dest)
            exr_pattern = ""
            frame_first = media.get("frame_first", 1)
            frame_last = media.get("frame_last", 1)
        else:
            movie_path = None
            frame_first = media["frame_first"]
            frame_last = media["frame_last"]
            exr_pattern = _copy_frames(media, dest_dir, leaf)

        flag_name = ".render_complete_%s.json" % leaf
        flag_path = dest_dir / flag_name

        flag_data = {
            "type": "asset_turntable",
            "project_id": project_id,
            "entity_type": "Asset",
            "entity_id": context["entity"]["id"],
            "entity_name": asset_code,
            "asset_type": asset_type,
            "step": step,
            "version": version,
            "artist": artist,
            "user_id": context.get("user_id"),
            "date": today,
            "frame_first": frame_first,
            "frame_last": frame_last,
            "start_timecode": context.get("start_timecode"),
            "exr_path_pattern": exr_pattern,
            "movie_path": movie_path,
            "skip_color": skip_color,
            "include_slate": include_slate,
            "submitted_for": context.get("submitted_for") or "Internal Review",
            "description": context.get("description") or "Review Drop",
            "task_id": context.get("task_id"),
            "source": "review_drop_app",
        }

    with open(flag_path, "w") as f:
        json.dump(flag_data, f, indent=2)

    return flag_path


def stage_shot_reference(
    tk: Any,
    media: dict,
    context: dict,
    name_override: str = "",
) -> list[Path]:
    """
    Copy still images into the selected shot's shared reference folder.

    References do not create a render-complete flag or a Flow Version. The
    optional override replaces the source basename while preserving extension
    and sequence frame numbers. Originals are also archived unchanged.
    """
    if context.get("entity_type") != "Shot":
        raise ValueError("Reference images must be associated with a Shot.")
    if media.get("media_type") not in {
        "exr_single", "exr_sequence", "image_single", "image_sequence",
    }:
        raise ValueError("Reference mode accepts still images only.")

    fields = {
        "Episode": context["episode"],
        "Sequence": context["sequence"],
        "Shot": context["entity"]["code"],
        "Step": context.get("step") or "temp",
        "version": int(context.get("version") or 1),
    }
    try:
        tk.create_filesystem_structure("Shot", context["entity"]["id"])
    except Exception:
        pass

    sample_mov = tk.templates["ep_nuke_shot_render_movie"].apply_fields(fields)
    shot_root = Path(sample_mov).parent.parent
    reference_dir = shot_root / "reference"
    source_dir = reference_dir / "source"
    reference_dir.mkdir(parents=True, exist_ok=True)
    _archive_originals(media, source_dir)

    override = _validate_override(name_override)
    copied = []
    files = sorted(media["files"], key=lambda p: p.name)
    for index, src in enumerate(files, start=1):
        dest_name = _reference_name(src, override, index, len(files))
        dest = reference_dir / dest_name
        if dest.exists():
            raise FileExistsError(
                "Reference already exists; choose another naming override:\n%s" % dest
            )
        shutil.copy2(src, dest)
        copied.append(dest)
    return copied


def stage_asset_ingest(
    ingest_root: str,
    asset_type: str,
    media: dict,
) -> Path:
    """
    Copy dropped 3D asset files into the ingest watch folder under the
    chosen asset-type subfolder (e.g. .../assets_incoming/Prop/).

    The type folder name IS the sg_asset_type for the ingest watcher, so no
    flag is written here — the ingest pipeline handles conversion/turntable.
    Returns the destination folder.
    """
    if not asset_type:
        raise ValueError("Select an Asset Type for a 3D delivery.")
    dest_dir = Path(ingest_root) / asset_type
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in media["files"]:
        shutil.copy2(src, dest_dir / src.name)
    return dest_dir


def _archive_originals(media: dict, dest_dir: Path) -> None:
    """Copy source media unchanged, preserving the delivered filenames."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in media["files"]:
        dest = dest_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)


def _validate_override(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("Naming override must be a filename, not a path.")
    return value


def _reference_name(src: Path, override: str, index: int, count: int) -> str:
    if not override:
        return src.name
    base = override
    if base.lower().endswith(src.suffix.lower()):
        base = base[: -len(src.suffix)]
    match = SEQUENCE_RE.match(src.name)
    if match:
        return "%s.%s%s" % (base, match.group("frame"), src.suffix.lower())
    if count > 1:
        return "%s_%03d%s" % (base, index, src.suffix.lower())
    return "%s%s" % (base, src.suffix.lower())


def _copy_frames(media: dict, dest_dir: Path, leaf: str) -> str:
    """Copy stills into dest_dir as leaf.%04d.<ext> and return the pattern."""
    files = sorted(media["files"], key=lambda p: p.name)
    first = media["frame_first"]
    ext = (media.get("image_ext") or files[0].suffix).lower()
    src_frames = []
    for f in files:
        m = SEQUENCE_RE.match(f.name)
        src_frames.append(int(m.group("frame")) if m else first)

    pattern = str(dest_dir / ("%s.%%04d%s" % (leaf, ext)))
    for src, frame in zip(files, src_frames):
        dest = dest_dir / ("%s.%04d%s" % (leaf, frame, ext))
        shutil.copy2(src, dest)
    return pattern
