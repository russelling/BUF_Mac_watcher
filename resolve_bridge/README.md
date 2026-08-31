# resolve_bridge (package reference)

See the [repository README](../README.md) for setup, the ACES IDT
verification steps, PowerGrade creation, and usage. This file is the
per-module reference for the `resolve_bridge` package itself.

## Pipeline

```
camera-original plate
    -> ACES (per-camera IDT: ARRI LogC4/Wide Gamut 4, or RED Log3G10/
       REDWideGamutRGB — detected automatically per shot)
    -> Shot CDL (.cc / .ccc)
    -> Show LUT (per-shot override, else a show-wide fallback)
```

`color_plan.py` optionally reuses a `resolve_lut_path(data)` /
`SHOW_LUT_PATH` pair from an external show pipe module if one is
importable (see `qt_bake_import.py`) — set
`qt_bake_import.SCRIPTS_DIR_CANDIDATES` to point it at one. Without that,
per-shot LUT resolution falls back to the defaults in `config.py`.

## Before you run this against a real project

This package ships two very different kinds of assumption, and only one of
them is safe to trust without checking:

- **Paths, ShotGrid field names, filesystem layout** (`config.py`,
  `sg_playlist.py`, `media_resolver.py`) — these fail loud. A wrong
  ShotGrid field name raises, a missing path returns nothing and gets
  logged.
- **DaVinci Resolve UI strings** — `colorScienceMode`, the ACES IDT names
  in `config.CAMERA_ACES_IDT`, `acesOutputTransform` — these fail **quiet**.
  `Project.SetSetting()` and `MediaPoolItem.SetClipProperty()` both just
  return `False` on a string Resolve doesn't recognise; nothing raises, and
  the clip is silently left on whatever colorspace it already had.

See the repository README's "Verify the ACES IDT strings" section before
trusting a real push. Every `SetSetting` / `SetClipProperty` call in this
package logs its own `OK`/`FAIL` and the value it tried, specifically so a
silent failure here is never actually silent in the log.

## Module map

| File | Role |
|------|------|
| `config.py` | Every studio-specific / Resolve-version-specific string, in one place |
| `cdl.py` | ASC CDL (`.cc`/`.ccc`) parsing + per-shot lookup |
| `camera_color.py` | ARRI vs RED detection, ACES IDT lookup |
| `color_plan.py` | Ties camera + CDL + Show LUT into one `ColorPlan` per shot |
| `qt_bake_import.py` | Best-effort import of an external show pipe module for shared per-shot LUT resolution (optional) |
| `sg_playlist.py` | ShotGrid connection + Playlist → ordered Versions/Shots |
| `media_resolver.py` | Highest-resolution media lookup per shot |
| `resolve_api.py` | DaVinci Resolve scripting: project/bin/timeline, `SetClipProperty`/`SetCDL`/`SetLUT` |
| `lut_bake.py` | Optional CDL+LUT combined-cube bake for the single-node fallback |
| `push_playlist_to_resolve.py` | CLI entry point tying all of the above together |
| `tests/` | Unit tests for everything above that doesn't require a live ShotGrid site or a running Resolve (i.e. everything except `resolve_api.connect_resolve()` and `sg_playlist.connect()` themselves) |

## Node-graph grading strategy (`resolve_api.apply_color_plan`)

Resolve's scripting API can **address** existing color nodes
(`SetCDL`/`SetLUT` by index) but cannot **add** them:

- If a PowerGrade template is configured (`--powergrade` /
  `config.POWERGRADE_TEMPLATE_PATH`), or the clip already has 2+ nodes: CDL
  goes on node 1, Show LUT on node 2 — both independently visible/editable
  in Resolve afterwards.
- With exactly 1 node and no template: the CDL wins (grading beats a
  display-only LUT) and the Show LUT is dropped, logged as a warning —
  unless `--bake-combined-lut-fallback` resolves a combined cube for this
  shot (`lut_bake.py`, EXPERIMENTAL, see its module docstring for the
  domain-matching caveat).

## Running the tests

From the repository root:

```bash
pip install -r requirements.txt
python3 -m pytest resolve_bridge/tests -q
```

The test suite never touches a real ShotGrid site or a real Resolve
instance — ShotGrid and Resolve are both faked at their narrow call
surfaces (`shotgun_api3.Shotgun`'s `find`/`find_one`, and the handful of
Resolve objects `resolve_api.py` calls methods on). `connect_resolve()` and
`sg_playlist.connect()` are intentionally left untested beyond "raises a
clear error with no credentials" — actually connecting can only be verified
against the real services.
