"""
resolve_bridge — pushes ShotGrid (Flow Production Tracking) Playlists into
DaVinci Resolve as a graded timeline.

Pipeline, per shot:

    highest-res camera-original plate on disk
        -> ACES (per-camera IDT: ARRI LogC4/Wide Gamut 4 or RED Log3G10/
           REDWideGamutRGB, selected automatically from shot/camera metadata)
        -> Shot CDL (.cc / .ccc, {shot}_{layer}_v{version} naming convention)
        -> Show LUT, per-shot override with a global fallback (see
           qt_bake_import.py to share this lookup with an existing
           OIIO/OCIO show pipe, if one is available)

See the repository README before running anything against a real site or a
real Resolve project.
"""
