"""
qt_bake_import.py — best-effort import of a sibling ../scripts/qt_bake_oiio.py
(the existing QT Watcher show pipe), when this package happens to be checked
out next to it.

Reuses its exact per-shot LUT resolution (resolve_lut_path / shot_plates_dir
/ SHOW_LUT_PATH) so the LUT this tool pushes into Resolve can never drift
from what the QT Watcher already bakes into review QuickTimes for the same
shot — one lookup, two consumers, when both are available together.

resolve_bridge is also shipped as its own standalone repository, where there
is no ../scripts to find — every caller (color_plan.py, media_resolver.py)
already falls back to its own defaults in that case, exactly like
../drop_app/preview.py falls back to its own defaults when the watcher
scripts aren't alongside it either. get_module() returning None is an
expected, handled outcome, not an error.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

# Override hook for tests: set to a list of candidate directories to search
# instead of the real sibling ../scripts lookup, so tests don't depend on
# whether this package happens to be vendored inside the monorepo it was
# originally built alongside.
SCRIPTS_DIR_CANDIDATES: Optional[List[Path]] = None

_qt_bake_oiio = None
_import_error: Optional[Exception] = None


def _candidate_scripts_dirs() -> List[Path]:
    if SCRIPTS_DIR_CANDIDATES is not None:
        return list(SCRIPTS_DIR_CANDIDATES)
    return [Path(__file__).resolve().parent.parent / "scripts"]


def reset_cache():
    """Clear the cached import result — for tests that change
    SCRIPTS_DIR_CANDIDATES between cases."""
    global _qt_bake_oiio, _import_error
    _qt_bake_oiio = None
    _import_error = None


def _load():
    global _qt_bake_oiio, _import_error
    if _qt_bake_oiio is not None or _import_error is not None:
        return

    scripts_dir = next((d for d in _candidate_scripts_dirs() if d.is_dir()), None)
    if scripts_dir is None:
        _import_error = ImportError(
            "no scripts/ directory with qt_bake_oiio.py found (checked: %s)"
            % [str(d) for d in _candidate_scripts_dirs()]
        )
        return

    sys.path.insert(0, str(scripts_dir))
    # Drop any previously-cached 'qt_bake_oiio' module first: plain `import`
    # is a no-op against sys.modules on a second call, which would otherwise
    # silently keep serving whichever scripts_dir won the FIRST successful
    # load in this process (real concern for reset_cache() callers/tests
    # pointed at more than one candidate directory in a session).
    sys.modules.pop("qt_bake_oiio", None)
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
