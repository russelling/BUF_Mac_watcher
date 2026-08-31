# shotgrid_resolve_bridge

Pushes a ShotGrid (Autodesk Flow Production Tracking) **Playlist** into
DaVinci **Resolve Studio** as a graded timeline: one clip per Playlist
entry, in Playlist order, using the **highest-resolution media actually on
disk** for that shot, with a per-shot color pipeline applied automatically:

```
camera-original plate
    -> ACES (per-camera IDT: ARRI LogC4/Wide Gamut 4, or RED Log3G10/
       REDWideGamutRGB — detected automatically per shot)
    -> Shot CDL (.cc / .ccc)
    -> Show LUT (per-shot override, else a show-wide fallback)
```

The Python package lives in [`resolve_bridge/`](resolve_bridge/); see
[`resolve_bridge/README.md`](resolve_bridge/README.md) for the full module
map. This top-level README covers setup and day-to-day usage.

`resolve_bridge` can optionally reuse an existing OpenColorIO-based show
pipe's per-shot LUT resolution logic (a `resolve_lut_path(data)` /
`SHOW_LUT_PATH` pair, e.g. from a Nuke/OIIO QuickTime bake script) if one is
importable next to it — see `resolve_bridge/qt_bake_import.py`. This is
entirely optional: without one, everything falls back to the defaults and
lookups built into this package (`resolve_bridge/config.py`,
`resolve_bridge/color_plan.py`).

## Before you run this against a real project

This package ships two very different kinds of assumption, and only one of
them is safe to trust without checking:

- **Paths, ShotGrid field names, filesystem layout**
  (`resolve_bridge/config.py`, `resolve_bridge/sg_playlist.py`,
  `resolve_bridge/media_resolver.py`) — these fail loud. A wrong ShotGrid
  field name raises, a missing path returns nothing and gets logged.
- **DaVinci Resolve UI strings** — `colorScienceMode`, the ACES IDT names in
  `config.CAMERA_ACES_IDT`, `acesOutputTransform` — these fail **quiet**.
  `Project.SetSetting()` and `MediaPoolItem.SetClipProperty()` both just
  return `False` on a string Resolve doesn't recognise; nothing raises, and
  the clip is silently left on whatever colorspace it already had.

**Verify the ACES IDT strings** before trusting a real push:

1. In Resolve, open (or create) a project and switch **Project Settings →
   Color Management → Color science** to the ACES mode your show uses
   (`config.RESOLVE_COLOR_SCIENCE_MODE`, default `acescct`).
2. Import one ARRI plate and one RED plate manually into the Media Pool.
3. Right-click each clip → **Clip Attributes → RAW/ACES tab** (or the ACES
   Transform dropdown, wording varies slightly by Resolve version) and note
   the *exact* string for "ARRI LogC4" / RED's Log3G10+REDWideGamutRGB
   combined transform.
4. Either confirm they match `config.CAMERA_ACES_IDT`, or override with the
   `RESOLVE_ACES_IDT_ARRI` / `RESOLVE_ACES_IDT_RED` environment variables.
5. Re-check after any Resolve upgrade — Resolve's ACES dropdown strings and
   available `colorScienceMode` values have changed between major versions
   before.

Every `SetSetting` / `SetClipProperty` call in this package logs its own
`OK`/`FAIL` and the value it tried, specifically so a silent failure here
is never actually silent in the log.

## Setup

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. ShotGrid connection

Either works; `resolve_bridge.sg_playlist.connect()` tries sgtk first, then
falls back:

- **Toolkit bootstrap** (reuses path templates from your pipeline config):
  set `SG_TOOLKIT_CONFIG_PATH` to your Toolkit pipeline config root. Needs
  `sgtk` importable (e.g. the Python bundled with the Flow/ShotGrid Desktop
  app).
- **Script key** (works from any machine, no Toolkit config needed): create
  an API Script under ShotGrid **Site Preferences → API Scripts**, then set
  `SG_SITE`, `SG_SCRIPT_NAME`, `SG_SCRIPT_KEY`. Needs `shotgun-api3` (already
  in `requirements.txt`).

### 3. DaVinci Resolve scripting

Requires **DaVinci Resolve Studio** (the scripting API does not exist in
the free edition), running locally, with **Preferences → System → General
→ External scripting using** set to `Local` (or `Network`).

`resolve_api.connect_resolve()` looks for `DaVinciResolveScript` using, in
order: the `RESOLVE_SCRIPT_API` / `RESOLVE_SCRIPT_LIB` environment variables
(Blackmagic's own documented override), then the per-platform default
install path in `resolve_bridge/config.py` (macOS: `/Library/Application
Support/Blackmagic Design/DaVinci Resolve/...`; Windows:
`%PROGRAMDATA%\Blackmagic Design\...`; Linux: `/opt/resolve/...`).

### 4. Show/pipeline paths

Update `resolve_bridge/config.py`'s `SHOTS_ROOT`, `SHOW_LUT_PATH`,
`CAMERA_ACES_IDT`, and `TOOLKIT_CONFIG_PATH` defaults for your show/site —
or override any of them with the matching environment variable listed in
that file, without editing the code.

### 5. Optional: a 2-node PowerGrade for CDL + Show LUT

Resolve's scripting API can **address** existing color nodes
(`SetCDL`/`SetLUT` by index) but cannot **add** them. Without a template, a
freshly-imported clip usually has exactly one node, and applying both a CDL
and a LUT stage to the same single node isn't possible — this tool applies
the CDL and drops the LUT in that case (logged as a warning, never silent —
see `resolve_api.apply_color_plan`).

To get a clean, independently-editable **[CDL node] → [Show LUT node]**
stack per clip instead:

1. In Resolve's Color page, on any clip, add a second serial node (both
   left empty/passthrough).
2. Right-click the node graph → **Grabs → Save Grade As PowerGrade...**, and
   give it a name (e.g. `cdl_lut_stack`).
3. Point this tool at it: `--powergrade cdl_lut_stack`, or set
   `RESOLVE_CDL_LUT_POWERGRADE` in `config.py`.

`push_playlist_to_resolve.py` applies this PowerGrade to every clip via
`ApplyGradeFromDRX` before targeting node 1 (CDL) and node 2 (Show LUT).

### 6. Optional/experimental: combined-LUT fallback

`--bake-combined-lut-fallback` (or `RESOLVE_BAKE_COMBINED_LUT_FALLBACK=1`)
bakes the CDL + Show LUT into one 3D cube via OpenColorIO's `ociobakelut`
(`brew install opencolorio`, or any OCIO install that ships the CLI) and
pushes that as a single `SetLUT` call when only one grading node is
available, instead of dropping the Show LUT entirely. This is a genuine
fallback, not a replacement for the PowerGrade approach above — the domain
the combined bake operates in may not match the domain Resolve's single
remaining node actually grades in for a given project. **Verify against a
known-good reference frame before trusting it on a real show.** See
`resolve_bridge/lut_bake.py` for the exact caveat.

## Usage

Run as a module from the repository root, so the `resolve_bridge` package
import resolves:

```bash
# Sanity-check a Playlist before touching Resolve: resolves media + the full
# color plan for every shot and prints it. Never connects to Resolve.
python3 -m resolve_bridge.push_playlist_to_resolve dailies_2026_08_31 --dry-run

# The real push: builds/reuses a Resolve project + bin named after the
# Playlist, creates a timeline in Playlist order, imports the
# highest-resolution media for each shot, and grades each clip.
python3 -m resolve_bridge.push_playlist_to_resolve dailies_2026_08_31 \
    --resolve-project "Dailies" \
    --powergrade cdl_lut_stack
```

`<playlist>` accepts either a numeric ShotGrid Playlist id or its exact
`code`.

## What "highest resolution media available" means here

`media_resolver.find_highest_res_media()` checks, per shot, in this order:

1. **`{shots}/{episode}/{sequence}/{shot}/plates/`** — the camera-original
   plate sequence. This is almost always the actual highest resolution, and
   is preferred by default even before measuring pixels.
2. **`Version.sg_path_to_frames`** — a rendered/comp EXR sequence, when
   there's no (or no matching) plate.
3. **`Version.sg_path_to_movie`**, then **`sg_uploaded_movie`** — the review
   QuickTime/MP4, as an absolute last resort. Review encodes are usually
   downscaled for delivery, so this must never be preferred just because
   ShotGrid's Playlist UI shows it.

When more than one of these resolves to a real, readable file, the actual
pixel resolution (via `oiiotool --info`) decides — a comp render can
legitimately be higher-res than a plate, and this never guesses when it can
measure.

## Module map

See [`resolve_bridge/README.md`](resolve_bridge/README.md) for the full
per-file breakdown (`config.py`, `cdl.py`, `camera_color.py`,
`color_plan.py`, `sg_playlist.py`, `media_resolver.py`, `resolve_api.py`,
`lut_bake.py`, `push_playlist_to_resolve.py`).

## Running the tests

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
