import pytest

from resolve_bridge import sg_playlist


class FakeShotgun:
    """Minimal shotgun_api3.Shotgun stand-in covering only what
    sg_playlist.py calls: find_one() and find()."""

    def __init__(self, playlists, connections, versions, shots):
        self.playlists = playlists
        self.connections = connections
        self.versions = versions
        self.shots = shots

    def find_one(self, entity_type, filters, fields=None):
        rows = self.find(entity_type, filters, fields)
        return rows[0] if rows else None

    def find(self, entity_type, filters, fields=None, order=None):
        rows = {
            "Playlist": self.playlists,
            "PlaylistVersionConnection": self.connections,
            "Version": self.versions,
            "Shot": self.shots,
        }[entity_type]

        def matches(row):
            for f in filters:
                field_name, op, value = f
                if op == "is":
                    if field_name in ("playlist", "version"):
                        if not row.get(field_name) or row[field_name]["id"] != value["id"]:
                            return False
                    elif field_name == "id":
                        if row.get("id") != value:
                            return False
                    elif row.get(field_name) != value:
                        return False
                elif op == "in":
                    if row.get(field_name) not in value and row.get("id") not in value:
                        return False
            return True

        result = [r for r in rows if matches(r)]
        if order:
            for spec in reversed(order):
                reverse = spec.get("direction") == "desc"
                result.sort(key=lambda r: r.get(spec["field_name"]) or 0, reverse=reverse)
        return result


@pytest.fixture
def fake_sg():
    playlists = [{"id": 500, "code": "dailies_2026_08_31", "type": "Playlist"}]
    shots = [
        {"id": 10, "code": "301_001_0050", "sg_episode": "301", "sg_sequence": "001",
         "sg_camera": "ARRI Alexa 35", "type": "Shot"},
        {"id": 11, "code": "301_001_0060", "sg_episode": "301", "sg_sequence": "001",
         "sg_camera": "RED Komodo", "type": "Shot"},
    ]
    versions = [
        {"id": 100, "code": "301_001_0050_comp_v001", "description": "first",
         "entity": {"type": "Shot", "id": 10}, "sg_path_to_movie": "/review/50.mov",
         "sg_path_to_frames": None, "sg_uploaded_movie": None,
         "sg_first_frame": 1001, "sg_last_frame": 1050, "frame_count": 50, "type": "Version"},
        {"id": 101, "code": "301_001_0060_comp_v001", "description": "second",
         "entity": {"type": "Shot", "id": 11}, "sg_path_to_movie": "/review/60.mov",
         "sg_path_to_frames": None, "sg_uploaded_movie": None,
         "sg_first_frame": 1001, "sg_last_frame": 1040, "frame_count": 40, "type": "Version"},
    ]
    connections = [
        {"id": 1, "playlist": {"type": "Playlist", "id": 500}, "version": {"type": "Version", "id": 101},
         "sg_sort_order": 2, "type": "PlaylistVersionConnection"},
        {"id": 2, "playlist": {"type": "Playlist", "id": 500}, "version": {"type": "Version", "id": 100},
         "sg_sort_order": 1, "type": "PlaylistVersionConnection"},
    ]
    return FakeShotgun(playlists, connections, versions, shots)


def test_fetch_playlist_orders_by_sort_order(fake_sg):
    entries = sg_playlist.fetch_playlist(fake_sg, 500)
    assert [e.version_id for e in entries] == [100, 101]


def test_fetch_playlist_resolves_shot_fields(fake_sg):
    entries = sg_playlist.fetch_playlist(fake_sg, 500)
    first = entries[0]
    assert first.shot_code == "301_001_0050"
    assert first.episode == "301"
    assert first.sequence == "001"
    assert first.camera_field == "ARRI Alexa 35"


def test_fetch_playlist_by_code(fake_sg):
    entries = sg_playlist.fetch_playlist(fake_sg, "dailies_2026_08_31")
    assert len(entries) == 2


def test_fetch_playlist_not_found_raises(fake_sg):
    with pytest.raises(sg_playlist.PlaylistNotFoundError):
        sg_playlist.fetch_playlist(fake_sg, "no_such_playlist")


def test_fetch_playlist_empty_returns_empty_list(fake_sg):
    fake_sg.connections = []
    entries = sg_playlist.fetch_playlist(fake_sg, 500)
    assert entries == []


def test_connect_raises_without_credentials(monkeypatch):
    import resolve_bridge.config as config

    monkeypatch.setattr(config, "SG_SITE", "")
    monkeypatch.setattr(config, "SG_SCRIPT_NAME", "")
    monkeypatch.setattr(config, "SG_SCRIPT_KEY", "")
    monkeypatch.setattr(config, "TOOLKIT_CONFIG_PATH", "/no/such/toolkit/config")

    with pytest.raises(RuntimeError):
        sg_playlist.connect(logger=lambda *a, **k: None)
