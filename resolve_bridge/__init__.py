"""
resolve_bridge — pushes ShotGrid (Flow Production Tracking) Playlists into
DaVinci Resolve as a graded timeline.

Pipeline, per shot:

    highest-res camera-original plate on disk
        -> ACES (per-camera IDT: ARRI LogC4/Wide Gamut 4 or RED Log3G10/
           REDWideGamutRGB, selected automatically from shot/camera metadata)
        -> Shot CDL (.cc / .ccc, same {shot}_{layer}_v{version} convention
           as ../scripts/qt_bake_oiio.py)
        -> Show LUT, per-shot override with a global fallback (reuses
           ../scripts/qt_bake_oiio.resolve_lut_path so this never drifts
           from what the QT Watcher already bakes)

See resolve_bridge/README.md before running anything against a real site or
a real Resolve project.
"""
