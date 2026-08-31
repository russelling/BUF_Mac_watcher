"""
qt_bake_import.py — best-effort import of ../scripts/qt_bake_oiio.py.

Reuses its exact per-shot LUT resolution (resolve_lut_path / shot_plates_dir
/ SHOW_LUT_PATH) so the LUT this tool pushes into Resolve can never drift
from what the QT Watcher already bakes into review QuickTimes for the same
shot — one lookup, two consumers. Same technique ../drop_app/preview.py uses
for the same reason: import when ../scripts is reachable, otherwise fall
back to the defaults in resolve_bridge/config.py so this package still
imports (with reduced accuracy) from a bare checkout that doesn't have
../scripts alongside it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_qt_bake_oiio = None
_import_error: Optional[Exception] = None


def _load():
    global _qt_bake_oiio, _import_error
    if _qt_bake_oiio is not None or _import_error is not None:
        return

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if not scripts_dir.is_dir():
        _import_error = ImportError("no ../scripts directory next to resolve_bridge/")
        return

    sys.path.insert(0, str(scripts_dir))
    try:
        import qt_bake_oiio as module
        _qt_bake_oiio = module
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch in tests
        _import_error = exc
    finally:
        sys.path.remove(str(scripts_dir))


def get_module():
    """Return the imported qt_bake_oiio module, or None if unavailable."""
    _load()
    return _qt_bake_oiio


def get_import_error() -> Optional[Exception]:
    _load()
    return _import_error
