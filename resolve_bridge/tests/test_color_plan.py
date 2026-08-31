import types

import pytest

from resolve_bridge import color_plan, config
from resolve_bridge.color_plan import ShotContext, resolve_color_plan


@pytest.fixture(autouse=True)
def no_qt_bake_module(monkeypatch):
    """Default every test to the "qt_bake_oiio not importable" path unless a
    test explicitly injects a fake module — keeps these tests independent of
    whether ../scripts happens to be checked out next to this package."""
    monkeypatch.setattr(color_plan, "_get_qt_bake_module", lambda: None)
    yield


def make_shot(tmp_path, shot_code="301_001_0050", episode="301", sequence="001", **overrides):
    defaults = dict(shot_code=shot_code, episode=episode, sequence=sequence)
    defaults.update(overrides)
    return ShotContext(**defaults)


def test_resolve_color_plan_full_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SHOTS_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "SHOW_LUT_PATH", str(tmp_path / "show.cube"))
    (tmp_path / "show.cube").write_text("LUT")

    plates = tmp_path / "301" / "001" / "301_001_0050" / "plates"
    plates.mkdir(parents=True)
    (plates / "301_001_0050_BG01_v01.cc").write_text(
        "<ColorCorrection><SOPNode><Slope>1 1 1</Slope><Offset>0 0 0</Offset>"
        "<Power>1 1 1</Power></SOPNode><SATNode><Saturation>1</Saturation>"
        "</SATNode></ColorCorrection>"
    )
    (plates / "301_001_0050.cube").write_text("LUT")

    shot = make_shot(tmp_path, camera_field="ARRI Alexa 35")
    plan = resolve_color_plan(shot)

    assert plan.camera_family == "arri"
    assert plan.aces_idt == config.CAMERA_ACES_IDT["arri"]
    assert plan.cdl_path.endswith("301_001_0050_BG01_v01.cc")
    assert plan.has_cdl
    assert plan.lut_path == str(plates / "301_001_0050.cube")
    assert plan.has_lut
    assert plan.warnings == []


def test_resolve_color_plan_falls_back_to_show_lut(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SHOTS_ROOT", str(tmp_path))
    show_lut = tmp_path / "show.cube"
    show_lut.write_text("LUT")
    monkeypatch.setattr(config, "SHOW_LUT_PATH", str(show_lut))

    shot = make_shot(tmp_path, camera_field="RED Komodo")
    plan = resolve_color_plan(shot)

    assert plan.camera_family == "red"
    assert plan.lut_path == str(show_lut)
    assert plan.cdl_path is None
    assert any("no per-shot CDL" in w for w in plan.warnings)


def test_resolve_color_plan_no_lut_anywhere_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SHOTS_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "SHOW_LUT_PATH", str(tmp_path / "nope.cube"))

    shot = make_shot(tmp_path, camera_field="ARRI")
    plan = resolve_color_plan(shot)

    assert plan.lut_path is None
    assert any("no LUT found" in w for w in plan.warnings)


def test_resolve_color_plan_unknown_camera_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CAMERA_FAMILY", "phantom")
    shot = make_shot(tmp_path, camera_field="", plate_path="")
    with pytest.raises(Exception):
        resolve_color_plan(shot)


def test_resolve_color_plan_bad_cdl_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SHOTS_ROOT", str(tmp_path))
    plates = tmp_path / "301" / "001" / "301_001_0050" / "plates"
    plates.mkdir(parents=True)
    (plates / "301_001_0050_BG01_v01.cc").write_text("not xml")

    shot = make_shot(tmp_path, camera_field="ARRI")
    with pytest.raises(ValueError):
        resolve_color_plan(shot)


def test_resolve_color_plan_uses_qt_bake_oiio_when_available(tmp_path, monkeypatch):
    show_lut = tmp_path / "show_from_qt_bake.cube"
    show_lut.write_text("LUT")

    fake_module = types.SimpleNamespace(
        SHOW_LUT_PATH=str(show_lut),
        resolve_lut_path=lambda data: None,
    )
    monkeypatch.setattr(color_plan, "_get_qt_bake_module", lambda: fake_module)

    shot = make_shot(tmp_path, camera_field="ARRI")
    plan = resolve_color_plan(shot)

    assert plan.lut_path == str(show_lut)
