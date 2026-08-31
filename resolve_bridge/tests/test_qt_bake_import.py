from resolve_bridge import qt_bake_import


def test_get_module_returns_real_module_when_scripts_dir_present():
    qt_bake_import._qt_bake_oiio = None
    qt_bake_import._import_error = None

    module = qt_bake_import.get_module()

    assert module is not None
    assert hasattr(module, "resolve_lut_path")
    assert hasattr(module, "SHOW_LUT_PATH")


def test_get_module_is_cached():
    first = qt_bake_import.get_module()
    second = qt_bake_import.get_module()
    assert first is second
