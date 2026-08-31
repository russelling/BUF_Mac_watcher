"""
sg_playlist.py — ShotGrid (Flow Production Tracking) connection and Playlist
retrieval, in Playlist order.

connect() prefers sgtk (Toolkit) bootstrap against the pipeline config,
same as ../scripts/qt_watcher.py, so anything downstream that wants path
templates gets them; it falls back to a raw shotgun_api3.Shotgun script-key
connection for use away from a Toolkit-configured machine. Either way the
rest of this module only ever touches the plain `sg` connection (a
shotgun_api3.Shotgun-shaped object) — nothing else here depends on sgtk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from resolve_bridge import config

VERSION_FIELDS = [
    "code",
    "description",
    "entity",
    "sg_path_to_movie",
    "sg_path_to_frames",
    "sg_uploaded_movie",
    "sg_first_frame",
    "sg_last_frame",
    "frame_count",
]

SHOT_FIELDS = [
    "code",
    "sg_episode",
    "episode",
    "sg_sequence",
    "sequence",
    "sg_camera",
    "sg_camera_manufacturer",
]


class PlaylistNotFoundError(LookupError):
    pass


@dataclass
class PlaylistEntry:
    """One Playlist row, in Playlist order, with its Shot resolved."""

    sort_order: float
    version_id: int
    version_code: str
    description: str
    sg_path_to_movie: Optional[str]
    sg_path_to_frames: Optional[str]
    sg_uploaded_movie: Optional[dict]
    frame_first: Optional[int]
    frame_last: Optional[int]
    shot_id: Optional[int]
    shot_code: str = ""
    episode: str = ""
    sequence: str = ""
    camera_field: str = ""
    raw_version: dict = field(default_factory=dict)
    raw_shot: dict = field(default_factory=dict)


def connect(logger=print):
    """
    Return a shotgun_api3.Shotgun-compatible connection.

    Raises RuntimeError with a clear, actionable message if neither the
    Toolkit config nor script-key env vars are usable — never returns None,
    so every caller can assume a working `sg` object or an exception, not a
    third "silently broken" state.
    """
    try:
        import sgtk  # noqa: F401 - import error means "not on this machine"
        tk = sgtk.sgtk_from_path(config.TOOLKIT_CONFIG_PATH)
        logger("[resolve_bridge.sg_playlist] connected via sgtk: %s" % config.TOOLKIT_CONFIG_PATH)
        return tk.shotgun
    except Exception as exc:
        logger(
            "[resolve_bridge.sg_playlist] sgtk bootstrap unavailable (%s) — "
            "falling back to a script-key connection" % exc
        )

    if not (config.SG_SITE and config.SG_SCRIPT_NAME and config.SG_SCRIPT_KEY):
        raise RuntimeError(
            "No ShotGrid connection available: sgtk bootstrap failed and "
            "SG_SITE / SG_SCRIPT_NAME / SG_SCRIPT_KEY are not all set. Set "
            "those three environment variables to a ShotGrid API script "
            "key (Site Preferences > API Scripts), or run this on a machine "
            "with the Toolkit pipeline config reachable at "
            "SG_TOOLKIT_CONFIG_PATH."
        )

    import shotgun_api3

    sg = shotgun_api3.Shotgun(
        config.SG_SITE, script_name=config.SG_SCRIPT_NAME, api_key=config.SG_SCRIPT_KEY,
    )
    logger("[resolve_bridge.sg_playlist] connected via script key: %s" % config.SG_SITE)
    return sg


def _find_playlist(sg: Any, playlist_ref) -> dict:
    """Look up a Playlist by numeric id, or by exact code/name."""
    if isinstance(playlist_ref, int) or (
        isinstance(playlist_ref, str) and playlist_ref.isdigit()
    ):
        playlist = sg.find_one("Playlist", [["id", "is", int(playlist_ref)]], ["code", "project"])
        if playlist:
            return playlist

    playlist = sg.find_one(
        "Playlist", [["code", "is", str(playlist_ref)]], ["code", "project"]
    )
    if playlist:
        return playlist

    raise PlaylistNotFoundError(
        "no Playlist found matching %r (tried id and exact code match)" % (playlist_ref,)
    )


def fetch_playlist(sg: Any, playlist_ref, logger=print) -> list:
    """
    Return this Playlist's Versions as a list of PlaylistEntry, in the exact
    order they're sorted in ShotGrid's Playlist UI.

    Uses PlaylistVersionConnection.sg_sort_order rather than
    Playlist.versions (a plain multi-entity link field, unordered) — this is
    the field ShotGrid itself uses to persist drag-and-drop reordering in
    the Playlist page, so it's the only reliable ordering source.
    """
    playlist = _find_playlist(sg, playlist_ref)
    logger(
        "[resolve_bridge.sg_playlist] Playlist %s (id=%d)"
        % (playlist.get("code"), playlist["id"])
    )

    connections = sg.find(
        "PlaylistVersionConnection",
        [["playlist", "is", {"type": "Playlist", "id": playlist["id"]}]],
        ["sg_sort_order", "version"],
        order=[{"field_name": "sg_sort_order", "direction": "asc"}],
    )
    if not connections:
        logger("[resolve_bridge.sg_playlist] Playlist has no versions")
        return []

    version_ids = [c["version"]["id"] for c in connections if c.get("version")]
    versions = sg.find("Version", [["id", "in", version_ids]], VERSION_FIELDS)
    versions_by_id = {v["id"]: v for v in versions}

    shot_ids = sorted({
        v["entity"]["id"] for v in versions
        if v.get("entity") and v["entity"]["type"] == "Shot"
    })
    shots_by_id = {}
    if shot_ids:
        shots = sg.find("Shot", [["id", "in", shot_ids]], SHOT_FIELDS)
        shots_by_id = {s["id"]: s for s in shots}

    entries = []
    for connection in connections:
        version_ref = connection.get("version")
        if not version_ref:
            continue
        version = versions_by_id.get(version_ref["id"])
        if not version:
            continue

        shot = None
        entity = version.get("entity")
        if entity and entity.get("type") == "Shot":
            shot = shots_by_id.get(entity["id"])

        entries.append(
            PlaylistEntry(
                sort_order=connection.get("sg_sort_order") or 0,
                version_id=version["id"],
                version_code=version.get("code") or "",
                description=version.get("description") or "",
                sg_path_to_movie=version.get("sg_path_to_movie"),
                sg_path_to_frames=version.get("sg_path_to_frames"),
                sg_uploaded_movie=version.get("sg_uploaded_movie"),
                frame_first=version.get("sg_first_frame"),
                frame_last=version.get("sg_last_frame"),
                shot_id=shot["id"] if shot else None,
                shot_code=(shot or {}).get("code") or "",
                episode=(shot or {}).get("sg_episode") or (shot or {}).get("episode") or "",
                sequence=(shot or {}).get("sg_sequence") or (shot or {}).get("sequence") or "",
                camera_field=(shot or {}).get("sg_camera")
                or (shot or {}).get("sg_camera_manufacturer") or "",
                raw_version=version,
                raw_shot=shot or {},
            )
        )

    logger("[resolve_bridge.sg_playlist] %d version(s) in order" % len(entries))
    return entries
