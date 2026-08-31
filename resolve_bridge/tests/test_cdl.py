import os

import pytest

from resolve_bridge import cdl

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parse_single_color_correction():
    path = os.path.join(FIXTURES, "301_001_0050_BG01_v02.cc")
    values = cdl.parse_cdl(path)

    assert values.slope == (1.05, 1.0, 0.95)
    assert values.offset == (0.01, 0.0, -0.01)
    assert values.power == (1.0, 1.02, 1.0)
    assert values.saturation == 1.1
    assert not values.is_identity()


def test_parse_ccc_defaults_to_first_entry():
    path = os.path.join(FIXTURES, "301_001_0050_BG01_v01.ccc")
    values = cdl.parse_cdl(path)

    assert values.cc_id == "wide"
    assert values.is_identity()


def test_parse_ccc_selects_by_id():
    path = os.path.join(FIXTURES, "301_001_0050_BG01_v01.ccc")
    values = cdl.parse_cdl(path, cc_id="close")

    assert values.cc_id == "close"
    assert values.slope == (0.9, 0.95, 1.0)
    assert values.saturation == 0.9


def test_parse_ccc_unknown_id_raises():
    path = os.path.join(FIXTURES, "301_001_0050_BG01_v01.ccc")
    with pytest.raises(ValueError):
        cdl.parse_cdl(path, cc_id="does-not-exist")


def test_parse_invalid_xml_raises():
    path = os.path.join(FIXTURES, "broken.cc")
    with pytest.raises(ValueError):
        cdl.parse_cdl(path)


def test_to_resolve_cdl_map_shape():
    values = cdl.CDLValues(
        slope=(1.05, 1.0, 0.95),
        offset=(0.01, 0.0, -0.01),
        power=(1.0, 1.02, 1.0),
        saturation=1.1,
    )
    cdl_map = values.to_resolve_cdl_map(node_index=1)

    assert cdl_map["NodeIndex"] == "1"
    assert cdl_map["Slope"] == "1.05 1 0.95"
    assert cdl_map["Offset"] == "0.01 0 -0.01"
    assert cdl_map["Power"] == "1 1.02 1"
    assert cdl_map["Saturation"] == "1.1"
    assert set(cdl_map) == {"NodeIndex", "Slope", "Offset", "Power", "Saturation"}


def test_find_shot_cdl_picks_highest_version(tmp_path):
    plates = tmp_path / "plates"
    plates.mkdir()
    (plates / "301_001_0050_BG01_v01.cc").write_text("<ColorCorrection></ColorCorrection>")
    (plates / "301_001_0050_BG01_v03.cc").write_text("<ColorCorrection></ColorCorrection>")
    (plates / "301_001_0050_BG02_v02.ccc").write_text("<ColorCorrectionCollection></ColorCorrectionCollection>")

    found = cdl.find_shot_cdl(str(plates), "301_001_0050")
    assert found == str(plates / "301_001_0050_BG01_v03.cc")


def test_find_shot_cdl_prefers_cc_over_ccc_at_same_version(tmp_path):
    plates = tmp_path / "plates"
    plates.mkdir()
    (plates / "301_001_0050_BGA_v02.cc").write_text("<ColorCorrection></ColorCorrection>")
    (plates / "301_001_0050_BGB_v02.ccc").write_text("<ColorCorrectionCollection></ColorCorrectionCollection>")

    found = cdl.find_shot_cdl(str(plates), "301_001_0050")
    assert found.endswith(".cc")


def test_find_shot_cdl_legacy_fallback(tmp_path):
    plates = tmp_path / "plates"
    plates.mkdir()
    (plates / "301_001_0050.cc").write_text("<ColorCorrection></ColorCorrection>")

    found = cdl.find_shot_cdl(str(plates), "301_001_0050")
    assert found == str(plates / "301_001_0050.cc")


def test_find_shot_cdl_missing_returns_none(tmp_path):
    assert cdl.find_shot_cdl(str(tmp_path / "nope"), "301_001_0050") is None
    assert cdl.find_shot_cdl(None, "301_001_0050") is None
    assert cdl.find_shot_cdl(str(tmp_path), "") is None
