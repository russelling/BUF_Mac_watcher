"""
media_resolver.py — find the highest-resolution media actually on disk for a
Playlist entry, instead of just handing Resolve whatever ShotGrid's Playlist
UI happens to show.

The review QuickTime a Version points at (`sg_uploaded_movie` /
`sg_path_to_movie`) is ALWAYS 1920x1080 for this show — see
DELIVERY_WIDTH/HEIGHT in ../scripts/qt_bake_oiio.py — so it is the last
resort, not the default. Camera-original plates outrank everything.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from resolve_bridge import config
from resolve_bridge.qt_bake_import import get_module as _get_qt_bake_module

FRAME_TOKEN_RE = re.compile(r"(\d{3,8})(?=\.[^.]+$)")


class NoMediaFoundError(LookupError):
    pass


@dataclass
class MediaCandidate:
    kind: str                       # "plates" | "sg_path_to_frames" | "sg_path_to_movie" | "sg_uploaded_movie"
    path: str                       # single file, or a %04d frame pattern
    is_sequence: bool
    frame_first: Optional[int] = None
    frame_last: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def resolution_area(self) -> int:
        if self.width and self.height:
            return self.width * self.height
        return 0

    @property
    def representative_frame(self) -> str:
        """One real file path for metadata probing (camera/EXR detection,
        resolution reads) — the pattern itself is not a file that exists."""
        if not self.is_sequence:
            return self.path
        if self.frame_first is None:
            return self.path
        if "%" in self.path:
            return self.path % self.frame_first
        return self.path


def _sequence_pattern_and_range(files: list) -> tuple:
    """Given sorted plate filenames sharing a directory, return
    (%0Nd pattern, first, last), or (None, None, None) if they don't look
    like a numbered sequence."""
    parsed = []
    for f in files:
        m = FRAME_TOKEN_RE.search(os.path.basename(f))
        if not m:
            return None, None, None
        parsed.append((int(m.group(1)), len(m.group(1)), f))

    parsed.sort()
    first = parsed[0][0]
    last = parsed[-1][0]
    width = parsed[0][1]
    sample = parsed[0][2]
    head = sample[: sample.rindex(str(parsed[0][0]).zfill(width))]
    tail = sample[len(head) + width:]
    pattern = "%s%%0%dd%s" % (head, width, tail)
    return pattern, first, last


def find_shot_plates(shots_root: str, episode: str, sequence: str, shot_code: str) -> Optional[MediaCandidate]:
    """
    Scan {shots_root}/{episode}/{sequence}/{shot}/plates for the camera
    original image sequence (highest-res source available for this shot).

    Ignores CDL (.cc/.ccc) and LUT (.cube/.lut) sidecars that also live in
    plates/ (see ../scripts/qt_bake_oiio.py resolve_lut_path /
    find_shot_cdl) — only PLATE_IMAGE_EXTENSIONS count as picture. Returns
    None (not an error) when there's no plates/ folder or nothing image-like
    in it; a missing camera-original plate is common for shots still in
    animation/layout and callers should fall through to a rendered/review
    candidate instead.
    """
    if not (episode and sequence and shot_code):
        return None
    plates_dir = os.path.join(shots_root, str(episode), str(sequence), shot_code, "plates")
    if not os.path.isdir(plates_dir):
        return None

    candidates = []
    for ext in config.PLATE_IMAGE_EXTENSIONS:
        candidates.extend(glob.glob(os.path.join(plates_dir, "*%s" % ext)))
    if not candidates:
        return None

    # A plates/ folder can hold more than one sequence (different takes,
    # different elements) — group by the non-numeric part of the filename
    # and use the largest group, logged, same "log every candidate and pick
    # one" convention as qt_bake_oiio.resolve_lut_path.
    groups: dict = {}
    for path in sorted(candidates):
        m = FRAME_TOKEN_RE.search(os.path.basename(path))
        key = os.path.basename(path)[: m.start()] if m else os.path.basename(path)
        groups.setdefault(key, []).append(path)

    if len(groups) > 1:
        print(
            "[resolve_bridge.media_resolver] %s: %d plate groups in %s "
            "(%s) — using the largest: %s"
            % (
                shot_code, len(groups), plates_dir,
                {k: len(v) for k, v in groups.items()},
                max(groups, key=lambda k: len(groups[k])),
            )
        )
    chosen_key = max(groups, key=lambda k: len(groups[k]))
    files = groups[chosen_key]

    if len(files) == 1:
        return MediaCandidate(kind="plates", path=files[0], is_sequence=False)

    pattern, first, last = _sequence_pattern_and_range(files)
    if pattern is None:
        return MediaCandidate(kind="plates", path=files[0], is_sequence=False)
    return MediaCandidate(
        kind="plates", path=pattern, is_sequence=True, frame_first=first, frame_last=last,
    )


def candidate_from_sg_path_to_frames(entry) -> Optional[MediaCandidate]:
    if not entry.sg_path_to_frames:
        return None
    return MediaCandidate(
        kind="sg_path_to_frames",
        path=entry.sg_path_to_frames,
        is_sequence="%" in entry.sg_path_to_frames or "#" in entry.sg_path_to_frames,
        frame_first=entry.frame_first,
        frame_last=entry.frame_last,
    )


def candidate_from_sg_movie(entry) -> Optional[MediaCandidate]:
    path = entry.sg_path_to_movie or (entry.sg_uploaded_movie or {}).get("url")
    if not path:
        return None
    kind = "sg_path_to_movie" if entry.sg_path_to_movie else "sg_uploaded_movie"
    return MediaCandidate(kind=kind, path=path, is_sequence=False)


def _read_resolution(path: str, oiiotool: str = "oiiotool") -> tuple:
    """Best-effort (width, height) via oiiotool; (None, None) if unreadable
    or oiiotool isn't available — resolution measurement is an optimisation
    (choosing between two real candidates), never a requirement."""
    qt_bake = _get_qt_bake_module()
    if qt_bake is not None:
        return qt_bake.read_resolution(path)
    if not path or not os.path.exists(path):
        return None, None
    try:
        result = subprocess.run(
            [oiiotool, "--info", path], capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    m = re.search(r"(\d+)\s*x\s*(\d+)", result.stdout)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def find_highest_res_media(
    entry,
    shots_root: str = config.SHOTS_ROOT,
    oiiotool: str = "oiiotool",
    measure_resolution: bool = True,
    logger=print,
) -> MediaCandidate:
    """
    Pick the best media candidate for one Playlist entry.

    Tries, in config.MEDIA_PRIORITY order, to build a candidate; the first
    KIND of candidate found is generally authoritative (plates always beat
    a review movie), but when measure_resolution is True and more than one
    candidate resolves to a real file, the actual highest pixel resolution
    wins regardless of kind — a comp render published as sg_path_to_frames
    can legitimately be higher-res than a proxy plate, and this should never
    guess when it can just measure.

    Raises NoMediaFoundError if nothing at all is found — pushing a Playlist
    entry with no media is a bug to surface immediately, not a shot to
    silently skip.
    """
    finders = {
        "plates": lambda: find_shot_plates(
            shots_root, entry.episode, entry.sequence, entry.shot_code
        ),
        "sg_path_to_frames": lambda: candidate_from_sg_path_to_frames(entry),
        "sg_path_to_movie": lambda: candidate_from_sg_movie(entry),
        "sg_uploaded_movie": lambda: candidate_from_sg_movie(entry),
    }

    found = []
    for kind in config.MEDIA_PRIORITY:
        candidate = finders[kind]()
        if candidate is not None:
            found.append(candidate)

    if not found:
        raise NoMediaFoundError(
            "no media found for %s (version %s) — checked plates/, "
            "sg_path_to_frames, sg_path_to_movie, sg_uploaded_movie"
            % (entry.shot_code or "?", entry.version_code)
        )

    if measure_resolution:
        for candidate in found:
            if candidate.width and candidate.height:
                continue
            frame = candidate.representative_frame
            w, h = _read_resolution(frame, oiiotool=oiiotool)
            candidate.width, candidate.height = w, h

    measured = [c for c in found if c.resolution_area]
    if len(measured) > 1:
        best = max(measured, key=lambda c: c.resolution_area)
    else:
        best = found[0]

    logger(
        "[resolve_bridge.media_resolver] %s: chose %s (%s) %sx%s from %s"
        % (
            entry.shot_code or entry.version_code, best.kind, best.path,
            best.width or "?", best.height or "?",
            [c.kind for c in found],
        )
    )
    return best
