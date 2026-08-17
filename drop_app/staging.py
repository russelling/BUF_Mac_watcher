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

# Asset location schema:
#   {type}/{script_name}/{real_name}/{variant}
#   e.g. assets/env/set_mdr/wf_stage_02/base
ASSET_TYPE_CODES = ("chr", "prp", "env", "veh")
ASSET_TYPE_LABELS = {
    "chr": "Character",
    "prp": "Prop",
    "env": "Environment",
    "veh": "Vehicle",
}
# Accept legacy ShotGrid values and short codes when filtering.
ASSET_TYPE_ALIASES = {
    "chr": {"chr", "character"},
    "prp": {"prp", "prop"},
    "env": {"env", "environment"},
    "veh": {"veh", "vehicle"},
}
ASSET_VARIANT_DEFAULTS = (
    "base",
    "previz",
    "pre_crash",
    "post_crash",
    "pod",
)
# Media that can be filed straight into a reference folder (no bake).
REFERENCE_MEDIA_TYPES = {
    "exr_single", "exr_sequence", "image_single", "image_sequence", "movie",
}
# Formats Flow can transcode into a viewable / thumbnail on upload.
UPLOADABLE_EXTS = MOVIE_EXTS | {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".webp", ".bmp",
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


def normalize_asset_type(value: str) -> str:
    """Return short type code (chr|prp|env|veh) or empty string."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    for code, aliases in ASSET_TYPE_ALIASES.items():
        if raw == code or raw in aliases:
            return code
    return ""


def asset_type_matches(selected_code: str, asset_sg_type: str) -> bool:
    """True if a Flow Asset's sg_asset_type matches the selected short code."""
    selected = normalize_asset_type(selected_code)
    actual = normalize_asset_type(asset_sg_type)
    if not selected:
        return True
    if not actual:
        return True  # untyped assets stay visible
    return selected == actual


def sanitize_path_token(value: str, label: str) -> str:
    """Filesystem-safe token for type/script/real/variant path segments."""
    token = re.sub(r"[^A-Za-z0-9_\-]+", "_", (value or "").strip()).strip("_")
    if not token:
        raise ValueError("Enter a %s." % label)
    return token


def asset_hierarchy(
    asset_type: str,
    script_name: str,
    real_name: str,
    variant: str,
) -> dict:
    """
    Normalize the asset location schema:

      {type}/{script_name}/{real_name}/{variant}
    """
    type_code = normalize_asset_type(asset_type)
    if type_code not in ASSET_TYPE_CODES:
        raise ValueError("Select an asset type (chr / prp / env / veh).")
    return {
        "asset_type": type_code,
        "script_name": sanitize_path_token(script_name, "script name"),
        "real_name": sanitize_path_token(real_name, "real-world name"),
        "variant": sanitize_path_token(variant, "variant"),
    }


def asset_relpath(hierarchy: dict) -> Path:
    """Relative path type/script_name/real_name/variant."""
    return Path(
        hierarchy["asset_type"],
        hierarchy["script_name"],
        hierarchy["real_name"],
        hierarchy["variant"],
    )


def toolkit_asset_fields(context: dict, version: int | None = None) -> dict:
    """Fields for Toolkit templates under the new asset schema."""
    hierarchy = asset_hierarchy(
        context.get("asset_type") or "",
        context.get("script_name") or (context.get("entity") or {}).get("code") or "",
        context.get("real_name") or "",
        context.get("variant") or "base",
    )
    fields = {
        "Asset": hierarchy["script_name"],
        "sg_asset_type": hierarchy["asset_type"],
        "script_name": hierarchy["script_name"],
        "real_name": hierarchy["real_name"],
        "variant": hierarchy["variant"],
        "version": int(version if version is not None else context.get("version") or 1),
    }
    return fields


def asset_publish_stem(hierarchy: dict, step: str = "") -> str:
    """Stem used in Version codes and publish leaves before `_v###`."""
    parts = [
        hierarchy["script_name"],
        hierarchy["real_name"],
        hierarchy["variant"],
    ]
    step = (step or "").strip()
    if step and step not in {"reference", "ingest"}:
        parts.append(step)
    return "_".join(parts)


def version_name_prefixes(
    entity_code: str,
    step: str,
    entity_type: str,
    real_name: str = "",
    variant: str = "",
) -> list:
    """
    Version code stems before `_v###`.

    Shot:   {Shot}_{Step}_v001
    Asset:  {script}_{real}_{variant}_{step}_v001
            {script}_{real}_{variant}_turntable_v001
    """
    code = (entity_code or "").strip()
    step = (step or "").strip()
    if not code:
        return []
    if entity_type == "Asset":
        if not (real_name and variant):
            return []
        try:
            hierarchy = {
                "script_name": sanitize_path_token(code, "script name"),
                "real_name": sanitize_path_token(real_name, "real-world name"),
                "variant": sanitize_path_token(variant, "variant"),
            }
        except ValueError:
            return []
        base = asset_publish_stem(hierarchy, "")
        prefixes = [base]
        if step and step not in {"reference", "ingest"}:
            with_step = asset_publish_stem(hierarchy, step)
            if with_step not in prefixes:
                prefixes.append(with_step)
        # Alternate bake naming used when the step itself isn't "turntable".
        if step != "turntable":
            turntable = "%s_turntable" % base
            if turntable not in prefixes:
                prefixes.append(turntable)
        return prefixes
    if not step:
        return []
    return ["%s_%s" % (code, step)]


def next_version_from_flow(
    sg: Any,
    project_id: int,
    entity_type: str,
    entity_code: str,
    step: str,
    real_name: str = "",
    variant: str = "",
) -> int:
    """
    Next free version number for the watcher naming convention on this project.

    Looks at Version.code values in Flow (ShotGrid) and returns
    max(existing) + 1 (or 1 if none).
    """
    prefixes = version_name_prefixes(
        entity_code, step, entity_type, real_name=real_name, variant=variant
    )
    if not prefixes or sg is None:
        return 1

    numbers = []
    for prefix in prefixes:
        needle = "%s_v" % prefix
        try:
            rows = sg.find(
                "Version",
                [
                    ["project", "is", {"type": "Project", "id": int(project_id)}],
                    ["code", "starts_with", needle],
                ],
                ["code"],
            )
        except Exception:
            rows = []
        pattern = re.compile(
            r"^%s_v(\d+)$" % re.escape(prefix),
            re.IGNORECASE,
        )
        for row in rows:
            match = pattern.match(row.get("code") or "")
            if match:
                numbers.append(int(match.group(1)))
    return (max(numbers) + 1) if numbers else 1


def stage_and_flag(
    tk: Any,
    sg: Any,
    media: dict,
    context: dict,
    include_slate: bool,
    color_pipe: dict | None = None,
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
    # Preview color-pipe chips → bake stages. Defaults keep the full show look.
    pipe = color_pipe or {}
    color_pipe_flag = {
        "log_convert": bool(pipe.get("log_convert", True)),
        "cdl": bool(pipe.get("cdl", True)),
        "show_lut": bool(pipe.get("show_lut", True)),
    }

    if is_shot:
        episode = context["episode"]
        sequence = context["sequence"]
        shot_code = context["entity"]["code"]
        # No Review Drop UI control for this yet - always in-house until one
        # is added. context.get() future-proofs against that landing later.
        vendor_code = context.get("vendor_code") or "INH"
        fields = {
            "Episode": episode,
            "Sequence": sequence,
            "Shot": shot_code,
            "vendor_code": vendor_code,
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
        leaf = "%s_%s_%s_v%03d" % (shot_code, vendor_code, step, version)
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
            "vendor_code": vendor_code,
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
            "color_pipe": color_pipe_flag,
            "submitted_for": context.get("submitted_for") or "Internal Review",
            "description": context.get("description") or "Review Drop",
            "task_id": context.get("task_id"),
            "source": "review_drop_app",
        }
    else:
        hierarchy = asset_hierarchy(
            context.get("asset_type") or "",
            context.get("script_name")
            or (context.get("entity") or {}).get("code")
            or "",
            context.get("real_name") or "",
            context.get("variant") or "base",
        )
        fields = toolkit_asset_fields(context, version)
        try:
            tk.create_filesystem_structure("Asset", context["entity"]["id"])
        except Exception:
            pass

        stem = asset_publish_stem(hierarchy, step)
        leaf = "%s_v%03d" % (stem, version)
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
            "entity_name": hierarchy["script_name"],
            "asset_type": hierarchy["asset_type"],
            "script_name": hierarchy["script_name"],
            "real_name": hierarchy["real_name"],
            "variant": hierarchy["variant"],
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
            "color_pipe": color_pipe_flag,
            "submitted_for": context.get("submitted_for") or "Internal Review",
            "description": context.get("description") or "Review Drop",
            "task_id": context.get("task_id"),
            "source": "review_drop_app",
        }

    with open(flag_path, "w") as f:
        json.dump(flag_data, f, indent=2)

    return flag_path


def reference_root(tk: Any, context: dict) -> Path:
    """Resolve the reference folder for the Shot or Asset in context."""
    if context.get("entity_type") == "Shot":
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
        return Path(sample_mov).parent.parent / "reference"

    fields = toolkit_asset_fields(context)
    try:
        tk.create_filesystem_structure("Asset", context["entity"]["id"])
    except Exception:
        pass
    return Path(tk.templates["asset_root"].apply_fields(fields)) / "reference"


def stage_reference(
    tk: Any,
    media: dict,
    context: dict,
    name_override: str = "",
) -> list[Path]:
    """
    Copy stills or a QT into the selected Shot's / Asset's reference folder.

    References never write a render-complete flag, so nothing is baked. A Flow
    record is only created when the caller also runs create_flow_record(). The
    optional override replaces the source basename while preserving extension
    and sequence frame numbers; without an override the delivered filename is
    kept as-is. Originals are archived unchanged, and the Step / Version /
    Submitted by / Submitted for / Notes fields are recorded in a sidecar
    JSON so the reference stays traceable without a Version entity.
    """
    if context.get("entity_type") not in {"Shot", "Asset"}:
        raise ValueError("Reference media must be associated with a Shot or Asset.")
    if media.get("media_type") not in REFERENCE_MEDIA_TYPES:
        raise ValueError("Reference mode accepts still images and QT movies only.")

    # reference_root() resolves the Shot-or-Asset reference folder generically
    # (branch feature); step/version are still needed below for the reference
    # sidecar JSON (main feature).
    step = (context.get("step") or "temp").strip() or "temp"
    version = int(context.get("version") or 1)
    reference_dir = reference_root(tk, context)
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

    _write_reference_sidecar(reference_dir, copied, media, context, step, version)
    return copied


def _write_reference_sidecar(
    reference_dir: Path,
    copied: list[Path],
    media: dict,
    context: dict,
    step: str,
    version: int,
) -> Path:
    """Record reference provenance alongside the copied files."""
    stem = copied[0].stem if copied else "reference"
    sidecar = reference_dir / ("%s.reference.json" % stem)
    entity_type = context.get("entity_type") or "Shot"
    payload = {
        "type": "shot_reference" if entity_type == "Shot" else "asset_reference",
        "project_id": context.get("project_id"),
        "entity_type": entity_type,
        "entity_id": context["entity"]["id"],
    }
    if entity_type == "Shot":
        # Shot references keep the original key names (episode/sequence,
        # shot_code) for compatibility with anything already reading these.
        payload.update({
            "shot_code": context["entity"]["code"],
            "episode": context.get("episode"),
            "sequence": context.get("sequence"),
        })
    else:
        payload.update({
            "asset_code": context["entity"]["code"],
            "asset_type": context.get("asset_type"),
            "script_name": context.get("script_name"),
            "real_name": context.get("real_name"),
            "variant": context.get("variant"),
        })
    payload.update({
        "step": step,
        "version": version,
        "artist": context.get("artist") or getpass.getuser(),
        "user_id": context.get("user_id"),
        "submitted_for": context.get("submitted_for") or "",
        "description": context.get("description") or "",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "media_type": media.get("media_type"),
        "originals": [f.name for f in media["files"]],
        "references": [f.name for f in copied],
        "source": "review_drop_app",
    })
    with open(sidecar, "w") as f:
        json.dump(payload, f, indent=2)
    return sidecar


def stage_asset_ingest(
    ingest_root: str,
    media: dict,
    asset_type: str = "",
    script_name: str = "",
    real_name: str = "",
    variant: str = "",
    hierarchy: dict | None = None,
) -> Path:
    """
    Copy dropped 3D asset files into the ingest watch folder under:

      {ingest_root}/{type}/{script_name}/{real_name}/{variant}/

    No flag is written here — the ingest pipeline handles conversion/turntable.
    Returns the destination folder.
    """
    hierarchy = hierarchy or asset_hierarchy(
        asset_type, script_name, real_name, variant or "base"
    )
    dest_dir = Path(ingest_root) / asset_relpath(hierarchy)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in media["files"]:
        shutil.copy2(src, dest_dir / src.name)
    return dest_dir


def create_flow_record(
    sg: Any,
    media: dict,
    context: dict,
    paths: list,
) -> dict:
    """
    Create a Flow Production Tracking Version for media that skips the bake:
    reference stills, reference QTs, and 3D asset deliveries.

    `paths` are the files as they now live on the server (the copies made by
    stage_reference / stage_asset_ingest), so the record points at pipeline
    paths rather than at whatever the artist dragged in.

    Returns {"version": <Flow entity>, "url": str, "warnings": [str, ...]}.
    """
    if sg is None:
        raise ValueError("No Flow connection available.")
    entity = context.get("entity")
    entity_type = context.get("entity_type") or "Shot"
    if not entity:
        raise ValueError(
            "Select a %s to attach the Flow record to." % entity_type.lower()
        )
    files = [Path(p) for p in paths]
    if not files:
        raise ValueError("Nothing was copied, so there is nothing to record.")

    warnings: list[str] = []
    media_type = media.get("media_type")
    primary = files[0]

    data = {
        "project": {"type": "Project", "id": context["project_id"]},
        "code": _unique_version_code(sg, context, _record_code(context, files)),
        "entity": {"type": entity_type, "id": entity["id"]},
        "description": context.get("description")
        or "Delivered via Review Drop (%s)." % _record_kind(media_type),
    }
    statuses = _valid_list_values(sg, "Version", "sg_status_list")
    if statuses is None or "rev" in statuses:
        data["sg_status_list"] = "rev"
    if media_type == "movie":
        data["sg_path_to_movie"] = str(primary)
    elif media_type == "model_3d":
        data["sg_path_to_geometry"] = str(primary.parent)
    else:
        data["sg_path_to_frames"] = _frames_path(media, files)
        if len(files) > 1:
            data["sg_first_frame"] = int(media.get("frame_first") or 1)
            data["sg_last_frame"] = int(media.get("frame_last") or len(files))
            data["frame_count"] = len(files)

    submitted_for = context.get("submitted_for")
    if submitted_for:
        valid = _valid_list_values(sg, "Version", "sg_submitted_for")
        if valid is None or submitted_for in valid:
            data["sg_submitted_for"] = submitted_for
        else:
            warnings.append(
                "'%s' is not a configured Submitted for option — left unset."
                % submitted_for
            )
    if context.get("user_id"):
        data["user"] = {"type": "HumanUser", "id": int(context["user_id"])}
    if context.get("task_id"):
        data["sg_task"] = {"type": "Task", "id": context["task_id"]}

    data = {k: v for k, v in data.items() if v not in (None, "")}
    data, dropped = _drop_unsupported_fields(sg, "Version", data)
    if dropped:
        warnings.append(
            "Flow has no Version field(s) %s on this site — skipped."
            % ", ".join(sorted(dropped))
        )

    version = sg.create("Version", data)

    # 3D geometry has no Flow viewable, and a texture sitting next to it is
    # not a stand-in for one, so those records stay path-only.
    upload = None if media_type == "model_3d" else _uploadable_source(files)
    if upload is None:
        warnings.append(
            "%s can't be transcoded by Flow — the record links to the file "
            "path only." % (
                "3D geometry"
                if media_type == "model_3d"
                else primary.suffix.upper().lstrip(".")
            )
        )
    else:
        if len(files) > 1:
            warnings.append(
                "Sequence uploaded as its first frame; the record links to "
                "the full range."
            )
        try:
            sg.upload_thumbnail("Version", version["id"], str(upload))
        except Exception as exc:
            warnings.append("Thumbnail upload failed: %s" % exc)
        try:
            sg.upload(
                "Version",
                version["id"],
                str(upload),
                field_name="sg_uploaded_movie",
            )
        except Exception as exc:
            warnings.append("Media upload failed: %s" % exc)

    return {
        "version": version,
        "url": _entity_url(sg, "Version", version["id"]),
        "warnings": warnings,
    }


def _record_kind(media_type: Optional[str]) -> str:
    if media_type == "movie":
        return "QT"
    if media_type == "model_3d":
        return "3D asset"
    return "image"


def _record_code(context: dict, files: list[Path]) -> str:
    """Build a Version code from the entity plus the delivered filename."""
    first = files[0]
    match = SEQUENCE_RE.match(first.name)
    stem = match.group("head") if match else first.stem
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_")
    entity_code = (context.get("entity") or {}).get("code") or ""
    if not stem:
        stem = "reference"
    if entity_code and not stem.lower().startswith(entity_code.lower()):
        return "%s_%s" % (entity_code, stem)
    return stem


def _unique_version_code(sg: Any, context: dict, base: str) -> str:
    """Append _v### until the code is free on this project."""
    try:
        existing = sg.find(
            "Version",
            [
                ["project", "is", {"type": "Project", "id": context["project_id"]}],
                ["code", "starts_with", base],
            ],
            ["code"],
        )
    except Exception:
        return base
    taken = {v.get("code") for v in existing}
    if base not in taken:
        return base
    number = 2
    while "%s_v%03d" % (base, number) in taken:
        number += 1
    return "%s_v%03d" % (base, number)


def _frames_path(media: dict, files: list[Path]) -> str:
    """Frame pattern for the copied stills, or the single file's path."""
    if len(files) == 1:
        return str(files[0])
    match = SEQUENCE_RE.match(files[0].name)
    if not match:
        return str(files[0])
    token = "%0{}d".format(len(match.group("frame")))
    return str(files[0].parent / (match.group("head") + token + match.group("tail")))


def _uploadable_source(files: list[Path]) -> Optional[Path]:
    """First delivered file Flow can transcode, for thumbnail + viewable."""
    for f in files:
        if f.suffix.lower() in UPLOADABLE_EXTS:
            return f
    return None


def _valid_list_values(sg: Any, entity_type: str, field: str) -> Optional[set]:
    try:
        schema = sg.schema_field_read(entity_type, field)
        props = schema.get(field, {}).get("properties", {})
        valid = props.get("valid_values", {}).get("value")
        if valid:
            return set(valid)
    except Exception:
        pass
    return None


def _drop_unsupported_fields(
    sg: Any,
    entity_type: str,
    data: dict,
) -> tuple[dict, set]:
    """Strip fields this Flow site doesn't have so create() can't fail on them."""
    try:
        schema = sg.schema_field_read(entity_type)
    except Exception:
        return data, set()
    if not schema:
        return data, set()
    supported = set(schema)
    dropped = {k for k in data if k not in supported}
    return {k: v for k, v in data.items() if k in supported}, dropped


def _entity_url(sg: Any, entity_type: str, entity_id: int) -> str:
    base = (getattr(sg, "base_url", "") or "").rstrip("/")
    if not base:
        return ""
    return "%s/detail/%s/%d" % (base, entity_type, entity_id)


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
