from pathlib import Path

import pytest

from resolve_bridge import qt_bake_import


@pytest.fixture(autouse=True)
def reset_between_tests():
    """Every test controls its own SCRIPTS_DIR_CANDIDATES and cache state,
    regardless of whether a real ../scripts/qt_bake_oiio.py happens to be
    checked out alongside this package in whatever repo it's vendored into."""
    yield
    qt_bake_import.SCRIPTS_DIR_CANDIDATES = None
    qt_bake_import.reset_cache()


def test_get_module_none_when_no_scripts_dir_found(tmp_path):
    qt_bake_import.SCRIPTS_DIR_CANDIDATES = [tmp_path / "does_not_exist"]
    qt_bake_import.reset_cache()

    assert qt_bake_import.get_module() is None
    assert qt_bake_import.get_import_error() is not None


def test_get_module_loads_from_a_candidate_directory(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "qt_bake_oiio.py").write_text(
        "SHOW_LUT_PATH = '/fake/show.cube'\n"
        "def resolve_lut_path(data):\n"
        "    return None\n"
    )
    qt_bake_import.SCRIPTS_DIR_CANDIDATES = [scripts_dir]
    qt_bake_import.reset_cache()

    module = qt_bake_import.get_module()

    assert module is not None
    assert module.SHOW_LUT_PATH == "/fake/show.cube"
    assert module.resolve_lut_path({}) is None
    assert qt_bake_import.get_import_error() is None


def test_get_module_is_cached_until_reset(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "qt_bake_oiio.py").write_text("SHOW_LUT_PATH = '/fake/show.cube'\n")
    qt_bake_import.SCRIPTS_DIR_CANDIDATES = [scripts_dir]
    qt_bake_import.reset_cache()

    first = qt_bake_import.get_module()
    second = qt_bake_import.get_module()
    assert first is second


def test_default_candidate_is_sibling_scripts_dir():
    qt_bake_import.reset_cache()
    candidates = qt_bake_import._candidate_scripts_dirs()
    assert candidates == [Path(qt_bake_import.__file__).resolve().parent.parent / "scripts"]
