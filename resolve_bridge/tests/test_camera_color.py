import pytest

from resolve_bridge import camera_color, config


def test_detect_from_text_arri():
    assert camera_color.detect_from_text("ARRI Alexa 35") == "arri"


def test_detect_from_text_red():
    assert camera_color.detect_from_text("RED V-Raptor") == "red"


def test_detect_from_text_no_match():
    assert camera_color.detect_from_text("Sony Venice") is None
    assert camera_color.detect_from_text("") is None


def test_detect_camera_family_prefers_sg_field(monkeypatch):
    family = camera_color.detect_camera_family(
        shot_camera_field="Arri Alexa Mini LF",
        plate_path="/shots/301/001/301_001_0050/plates/RED_A001_C002.exr",
    )
    assert family == "arri"


def test_detect_camera_family_falls_back_to_plate_path():
    family = camera_color.detect_camera_family(
        shot_camera_field="",
        plate_path="/shots/301/001/301_001_0050/plates/A001_C002_red_dragon.exr",
    )
    assert family == "red"


def test_detect_camera_family_falls_back_to_exr_metadata(monkeypatch):
    monkeypatch.setattr(
        camera_color, "detect_from_exr_metadata", lambda path, oiiotool="oiiotool": "red"
    )
    family = camera_color.detect_camera_family(
        shot_camera_field="",
        plate_path="",
        exr_metadata_path="/shots/301/001/301_001_0050/plates/0050.1001.exr",
    )
    assert family == "red"


def test_detect_camera_family_default_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CAMERA_FAMILY", "arri")
    family = camera_color.detect_camera_family(
        shot_camera_field="Sony Venice", plate_path="", exr_metadata_path="",
    )
    assert family == "arri"


def test_detect_camera_family_unmapped_default_raises(monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CAMERA_FAMILY", "phantom")
    with pytest.raises(camera_color.UnknownCameraFamilyError):
        camera_color.detect_camera_family(shot_camera_field="", plate_path="")


def test_aces_idt_for_family_known():
    assert camera_color.aces_idt_for_family("arri") == config.CAMERA_ACES_IDT["arri"]


def test_aces_idt_for_family_unknown_raises():
    with pytest.raises(camera_color.UnknownCameraFamilyError):
        camera_color.aces_idt_for_family("phantom")


def test_detect_from_exr_metadata_missing_file_returns_none():
    assert camera_color.detect_from_exr_metadata("/does/not/exist.exr") is None


def test_detect_from_exr_metadata_missing_tool_returns_none(tmp_path):
    exr = tmp_path / "frame.exr"
    exr.write_bytes(b"not a real exr")
    assert camera_color.detect_from_exr_metadata(str(exr), oiiotool="/no/such/oiiotool") is None
