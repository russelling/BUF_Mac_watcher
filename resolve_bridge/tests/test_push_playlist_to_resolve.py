import pytest

from resolve_bridge import color_plan, config, push_playlist_to_resolve, sg_playlist
from resolve_bridge.sg_playlist import PlaylistEntry


def make_entry(**overrides):
    defaults = dict(
        sort_order=1.0, version_id=1, version_code="301_001_0050_comp_v001",
        description="", sg_path_to_movie="/review/50.mov", sg_path_to_frames=None,
        sg_uploaded_movie=None, frame_first=None, frame_last=None, shot_id=1,
        shot_code="301_001_0050", episode="301", sequence="001", camera_field="ARRI Alexa 35",
    )
    defaults.update(overrides)
    return PlaylistEntry(**defaults)


@pytest.fixture(autouse=True)
def no_qt_bake_module(monkeypatch):
    monkeypatch.setattr(color_plan, "_get_qt_bake_module", lambda: None)


def test_dry_run_prints_plan_and_does_not_touch_resolve(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "SHOTS_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "SHOW_LUT_PATH", str(tmp_path / "show.cube"))
    (tmp_path / "show.cube").write_text("LUT")

    entries = [make_entry()]
    monkeypatch.setattr(sg_playlist, "connect", lambda *a, **k: object())
    monkeypatch.setattr(sg_playlist, "fetch_playlist", lambda sg, ref, **k: entries)

    def fail_if_called(*a, **k):
        raise AssertionError("dry-run must not connect to Resolve")

    monkeypatch.setattr(push_playlist_to_resolve.resolve_api, "connect_resolve", fail_if_called)

    args = push_playlist_to_resolve.parse_args(["dailies_2026_08_31", "--dry-run"])
    exit_code = push_playlist_to_resolve.run(args)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "301_001_0050" in out
    assert "ARRI LogC4" in out or config.CAMERA_ACES_IDT["arri"] in out


def test_run_reports_error_when_no_media_found(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SHOTS_ROOT", str(tmp_path))
    entries = [make_entry(sg_path_to_movie=None)]
    monkeypatch.setattr(sg_playlist, "connect", lambda *a, **k: object())
    monkeypatch.setattr(sg_playlist, "fetch_playlist", lambda sg, ref, **k: entries)

    args = push_playlist_to_resolve.parse_args(["dailies_2026_08_31", "--dry-run"])
    exit_code = push_playlist_to_resolve.run(args)

    assert exit_code == 1


def test_run_pushes_to_resolve_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SHOTS_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "SHOW_LUT_PATH", str(tmp_path / "show.cube"))
    (tmp_path / "show.cube").write_text("LUT")

    entries = [make_entry()]
    monkeypatch.setattr(sg_playlist, "connect", lambda *a, **k: object())
    monkeypatch.setattr(sg_playlist, "fetch_playlist", lambda sg, ref, **k: entries)

    calls = {"imported": [], "graded": []}

    class FakeMediaPoolItem:
        def SetClipProperty(self, name, value):
            return True

    class FakeTimelineItem:
        def GetNodeGraph(self):
            return None

        def SetCDL(self, cdl_map):
            calls["graded"].append(("cdl", cdl_map))
            return True

        def SetLUT(self, idx, path):
            calls["graded"].append(("lut", idx, path))
            return True

    class FakeMediaPool:
        def ImportMedia(self, paths):
            calls["imported"].append(paths[0])
            return [FakeMediaPoolItem()]

        def GetRootFolder(self):
            class Root:
                def GetSubFolderList(self):
                    return []

            return Root()

        def SetCurrentFolder(self, folder):
            pass

        def AddSubFolder(self, root, name):
            return object()

        def CreateEmptyTimeline(self, name):
            return object()

        def AppendToTimeline(self, items):
            return [FakeTimelineItem() for _ in items]

    class FakeProject:
        def GetMediaPool(self):
            return FakeMediaPool()

        def SetSetting(self, key, value):
            return True

        def SetCurrentTimeline(self, timeline):
            pass

    monkeypatch.setattr(
        push_playlist_to_resolve.resolve_api, "connect_resolve", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        push_playlist_to_resolve.resolve_api, "ensure_project", lambda *a, **k: FakeProject()
    )

    args = push_playlist_to_resolve.parse_args(["dailies_2026_08_31"])
    exit_code = push_playlist_to_resolve.run(args)

    assert exit_code == 0
    assert calls["imported"] == ["/review/50.mov"]
    kinds = [c[0] for c in calls["graded"]]
    assert kinds == ["lut"], "no CDL on disk for this shot — only the Show LUT should be applied"
    assert calls["graded"][0][1:] == (1, str(tmp_path / "show.cube"))
