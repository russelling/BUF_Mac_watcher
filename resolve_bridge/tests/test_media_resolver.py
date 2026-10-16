import pytest

from resolve_bridge import media_resolver
from resolve_bridge.sg_playlist import PlaylistEntry


def make_entry(**overrides):
    defaults = dict(
        sort_order=1.0,
        version_id=1,
        version_code="301_001_0050_comp_v001",
        description="",
        sg_path_to_movie=None,
        sg_path_to_frames=None,
        sg_uploaded_movie=None,
        frame_first=None,
        frame_last=None,
        shot_id=1,
        shot_code="301_001_0050",
        episode="301",
        sequence="001",
        camera_field="",
    )
    defaults.update(overrides)
    return PlaylistEntry(**defaults)


def test_find_shot_plates_single_frame(tmp_path):
    plates = tmp_path / "301" / "001" / "301_001_0050" / "plates"
    plates.mkdir(parents=True)
    (plates / "301_001_0050.exr").write_bytes(b"fake")

    candidate = media_resolver.find_shot_plates(str(tmp_path), "301", "001", "301_001_0050")
    assert candidate is not None
    assert candidate.kind == "plates"
    assert not candidate.is_sequence
    assert candidate.path.endswith("301_001_0050.exr")


def test_find_shot_plates_sequence(tmp_path):
    plates = tmp_path / "301" / "001" / "301_001_0050" / "plates"
    plates.mkdir(parents=True)
    for frame in (1001, 1002, 1003):
        (plates / ("301_001_0050.%04d.exr" % frame)).write_bytes(b"fake")

    candidate = media_resolver.find_shot_plates(str(tmp_path), "301", "001", "301_001_0050")
    assert candidate.is_sequence
    assert candidate.frame_first == 1001
    assert candidate.frame_last == 1003
    assert "%04d" in candidate.path
    assert candidate.representative_frame == str(plates / "301_001_0050.1001.exr")


def test_find_shot_plates_ignores_cdl_and_lut_sidecars(tmp_path):
    plates = tmp_path / "301" / "001" / "301_001_0050" / "plates"
    plates.mkdir(parents=True)
    (plates / "301_001_0050.exr").write_bytes(b"fake")
    (plates / "301_001_0050_BG01_v01.cc").write_text("<ColorCorrection></ColorCorrection>")
    (plates / "301_001_0050.cube").write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")

    candidate = media_resolver.find_shot_plates(str(tmp_path), "301", "001", "301_001_0050")
    assert candidate.path.endswith(".exr")


def test_find_shot_plates_no_plates_dir_returns_none(tmp_path):
    assert media_resolver.find_shot_plates(str(tmp_path), "301", "001", "301_001_0050") is None


def test_find_shot_plates_picks_largest_group(tmp_path):
    plates = tmp_path / "301" / "001" / "301_001_0050" / "plates"
    plates.mkdir(parents=True)
    for frame in (1001, 1002):
        (plates / ("takeA.%04d.exr" % frame)).write_bytes(b"fake")
    for frame in (1001, 1002, 1003, 1004):
        (plates / ("takeB.%04d.exr" % frame)).write_bytes(b"fake")

    candidate = media_resolver.find_shot_plates(str(tmp_path), "301", "001", "301_001_0050")
    assert "takeB" in candidate.path


def test_candidate_from_sg_path_to_frames():
    entry = make_entry(sg_path_to_frames="/renders/301_001_0050.%04d.exr", frame_first=1001, frame_last=1010)
    candidate = media_resolver.candidate_from_sg_path_to_frames(entry)
    assert candidate.kind == "sg_path_to_frames"
    assert candidate.is_sequence
    assert candidate.frame_first == 1001


def test_candidate_from_sg_path_to_frames_none_when_absent():
    entry = make_entry()
    assert media_resolver.candidate_from_sg_path_to_frames(entry) is None


def test_candidate_from_sg_movie_prefers_path_to_movie():
    entry = make_entry(
        sg_path_to_movie="/review/301_001_0050.mov",
        sg_uploaded_movie={"url": "https://example.com/foo.mov"},
    )
    candidate = media_resolver.candidate_from_sg_movie(entry)
    assert candidate.kind == "sg_path_to_movie"
    assert candidate.path == "/review/301_001_0050.mov"


def test_candidate_from_sg_movie_falls_back_to_uploaded():
    entry = make_entry(sg_uploaded_movie={"url": "https://example.com/foo.mov"})
    candidate = media_resolver.candidate_from_sg_movie(entry)
    assert candidate.kind == "sg_uploaded_movie"


def test_find_highest_res_media_prefers_plates_over_movie(tmp_path, monkeypatch):
    plates = tmp_path / "301" / "001" / "301_001_0050" / "plates"
    plates.mkdir(parents=True)
    (plates / "301_001_0050.exr").write_bytes(b"fake")

    entry = make_entry(sg_path_to_movie="/review/301_001_0050.mov")
    candidate = media_resolver.find_highest_res_media(
        entry, shots_root=str(tmp_path), measure_resolution=False,
    )
    assert candidate.kind == "plates"


def test_find_highest_res_media_measures_resolution_when_ambiguous(tmp_path, monkeypatch):
    plates = tmp_path / "301" / "001" / "301_001_0050" / "plates"
    plates.mkdir(parents=True)
    (plates / "301_001_0050.exr").write_bytes(b"fake")

    entry = make_entry(sg_path_to_frames="/renders/301_001_0050.%04d.exr", frame_first=1, frame_last=1)

    def fake_read_resolution(path, oiiotool="oiiotool"):
        if path == str(plates / "301_001_0050.exr"):
            return 2048, 858
        return 4096, 2160

    monkeypatch.setattr(media_resolver, "_read_resolution", fake_read_resolution)
    candidate = media_resolver.find_highest_res_media(entry, shots_root=str(tmp_path))
    assert candidate.kind == "sg_path_to_frames"
    assert candidate.width == 4096


def test_find_highest_res_media_raises_when_nothing_found(tmp_path):
    entry = make_entry()
    with pytest.raises(media_resolver.NoMediaFoundError):
        media_resolver.find_highest_res_media(entry, shots_root=str(tmp_path), measure_resolution=False)
