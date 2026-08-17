# Buffalo Review Drop

Standalone Mac app for sending **external** review media (images / EXR / MOV)
into the existing QT Watcher pipeline, plus a shortcut to the 3D asset ingest
drop folder.

Branded square window (BUFFALO VFX, Manifold Extended Medium) with a
compact two-column layout. The chrome follows a Lumon / Severance
60-30-10 palette drawn from the MDR floor: white and pale gray field,
sage / olive structure, navy reserved for the primary send button.
Bottom row order is Preview → Send → Open 3D Ingest.

## What it does

### Review media (images / EXR / MOV)

1. **Drag & drop** or **click** the drop zone to browse for an image
   sequence, single still, EXR, MOV/MP4, or 3D file
   - **Remove Loaded** clears a pending drop before submission; it never
     deletes the original files from disk.
2. Pick **Shot** (Episode → Sequence → Shot) or **Asset** hierarchy from Flow
3. Set Step / Submitted by / Submitted for / Notes — **Version** autofills
   to the next free Shot `{Shot}_{Step}_v###` or Asset
   `{script}_{real}_{variant}_{step}_v###` in Flow
4. Optional **Include slate** (especially useful for single frames)
5. Copies media into the pipeline render area and writes a
   `.render_complete_*.json` flag
6. The Mac Studio **QT Watcher** bakes burn-ins / slate / upload on its next poll

Accepted stills: `exr, hdr, png, jpg, jpeg, tif, tiff, tga, bmp, dpx, cin,
gif, webp, psd, iff, sxr`. Drop one extension at a time (no mixed PNG+JPG).

### Picking context

Episode / Sequence / Shot (and Asset) are loaded whole from Flow at startup —
the status log reports the counts — and filtered in the app. Picking an
Episode narrows the Sequence list, but nothing is ever hidden: a Sequence
with no Episode link, or a Shot with no Sequence link, still appears and is
labelled `(no episode)` / `(no sequence)`, and clearing a level back to
`— select —` shows everything again. The link fields are read from your
Flow schema (`episode` or `sg_episode`, `sg_sequence` or `sequence`) rather
than assumed.

All four fields are type-ahead searchable and accept a code that isn't in
the list. On submit the typed code is matched against what was loaded, then
looked up live in Flow (so a shot created since launch is found), and only
if it exists nowhere are you asked whether to create it. Created entities
carry the project and the links above them — Sequence → Episode, Shot →
Sequence, Asset → `sg_asset_type` (short code) — and any field your site
doesn't have is skipped with a warning rather than sent.

### Reference media and original sources

Choose **Reference** as the Delivery type to copy stills or a QT straight
into the selected Shot's (or Asset's) `reference/` folder, skipping the bake.
**Naming override** is optional: it replaces the basename while preserving
the source extension and sequence frame numbers. Unchanged originals are
also retained under `reference/source/`.

### Preview

**Preview…** (or double-click the drop zone) opens a viewer on whatever is
loaded, so visibility and color can be checked before anything is sent.

Scene-linear stills are decoded through the same chain the QT Watcher bakes
with — ACEScg → LogC4 → CDL → show LUT → Rec.709 — so the preview is the
delivered look, CDL included when the selected Shot has a `{Shot}_BG1.cdl` in `plates/`.
Display-referred stills and movies are shown as delivered, matching the
watcher's `skip_color` path.

For EXR / HDR, Preview exposes color-pipe **chips** to turn each stage off
(**ACEScg → LogC4**, **CDL**, **Show LUT**) so a crushed or blown look can be
isolated to a single step. Turning them all off clamps raw ACEScg for
inspection. Those chip settings are written into the watcher flag as
`color_pipe`, so the QT bake matches what you previewed. The pipeline label
always reflects what is on, and marks anything that is no longer the delivered
look as `approx`.

Every frame is labelled with the pipeline and the tool that decoded it. When
the show LUT can't be found (volume not mounted) or oiiotool isn't installed
and ffmpeg has to stand in, the label says **approx** and the header warns
**NOT the delivered look** — an approximate preview is still worth having,
but it must not be mistaken for the real grade.

| Control | Use |
|---------|-----|
| Frame slider / ◀ ▶ | Scrub a sequence, or step a movie frame at a time |
| Channel | RGB, single channels, alpha, or luma — for mattes and edges |
| Exposure / Gamma | Viewer-only stops and gamma, for checking into darks |
| Fit / − / + | Fit to window, or zoom to inspect at pixel level |
| Hover | Pixel coordinates and RGBA values under the cursor |
| Open Externally | Hand the file to the system viewer (QuickTime, Preview) |

Transparent areas are composited over a checkerboard, decoding runs off the
UI thread so scrubbing stays responsive, and 3D drops say so rather than
showing an empty frame. Preview needs `oiiotool` for EXR/DPX and `ffmpeg`
for movies on this Mac (`brew install openimageio ffmpeg`) — only when
you open Preview, not to launch or Send. The bake machine's install is
not used. PNG/JPG/TIFF need neither. If both are missing on macOS,
Preview falls back to a Quick Look thumbnail and labels it `approx`.

### Flow record

**Create Flow record** makes a Version in Flow Production Tracking for
deliveries the QT Watcher never sees — a reference image, a reference QT, or
a 3D asset drop. The record links to the selected Shot or Asset, points at
the copied pipeline path, and carries Submitted by / Submitted for / Notes,
so those fields stay editable whenever the box is ticked.

| Delivery | Record |
|----------|--------|
| Version | Always — the QT Watcher creates it after the bake (option locked on) |
| Reference image / QT | Optional — image or QT is uploaded as the thumbnail and viewable |
| 3D asset drop | Optional — Version on the chosen Asset pointing at the ingest copy |

EXR / HDR and 3D files can't be transcoded by Flow, so those records link to
the path only and the app says so in the confirmation. A 3D record needs an
Asset selected; without one the drop is refused rather than copied with no
record. If a record fails after the files land, the copy stands and the error
is reported instead of being rolled back.

For normal Version deliveries, the untouched original files are archived
under the selected Shot at:

`source/review_drop/{Shot}_{Step}_v###/`

### Asset location schema

Asset review media, turntables, references, and 3D ingest all use the same
hierarchy:

```
{type}/{script_name}/{real_name}/{variant}
```

| Segment | Meaning | Examples |
|---------|---------|----------|
| `type` | Short asset class | `chr` · `prp` · `env` · `veh` |
| `script_name` | Flow Asset.code / script name | `set_mdr` · `veh_marks_volvo` |
| `real_name` | Real-world location or asset | `wf_stage_02` · `sedan_960` |
| `variant` | Look / state under that real name | `base` · `previz` · `pre_crash` · `post_crash` · `pod` |

Pipeline paths resolve under Toolkit as:

```
…/assets/{sg_asset_type}/{Asset}/{real_name}/{variant}/…
```

e.g. `…/assets/env/set_mdr/wf_stage_02/base/`. Flow still stores the Asset as
`script_name` (`Asset.code`); `sg_asset_type` should be the short code
(`env`, `veh`, …). Legacy labels (`Environment`, `Vehicle`, …) still match
when filtering in the drop app.

Version / publish stems follow:

`{script_name}_{real_name}_{variant}[_{step}]_v###`

Update the ShotGrid Toolkit templates on the volume (`asset_root`,
`unreal_asset_turntable_render`, `unreal_asset_turntable_movie`, and any
related paths) to include `{real_name}` and `{variant}` under `{Asset}`.

### 3D asset deliveries (OBJ / FBX / GLB / PLY / USD / ABC …)

Drop a 3D file directly on the window. The app switches to **Asset** mode;
fill **Type / Script name / Real name / Variant** and click **Send to 3D
Ingest** — the file is copied into:

`_staging/assets_incoming/{type}/{script_name}/{real_name}/{variant}/`

Tick **Create Flow record** to also log the delivery against the Flow Asset
(script name). (The **Open 3D Ingest Folder** button still opens the drop
folder in Finder for manual/bulk deliveries.)

Recognized 3D formats: `obj, fbx, glb, gltf, ply, stl, abc, usd, usdc, usda,
usdz, max, blend`.

### Color rules

| Source | Color pipe |
|--------|------------|
| EXR / HDR | Full ACEScg → LogC4 → CDL → Show LUT → Rec.709 |
| PNG / JPG / TIFF / TGA / DPX / … | Skip color — burn-in (+ optional slate) only |
| MOV / MP4 | Skip color — burn-in (+ optional slate) only |

### 3D ingest shortcut

**Open 3D Asset Ingest Folder** reveals:

`/Volumes/.../buffalo_vfx/_staging/assets_incoming/`

Drop under `{type}/{script_name}/{real_name}/{variant}/`
(e.g. `env/set_mdr/wf_stage_02/base/`).

## Requirements

| Prerequisite | When |
|--------------|------|
| ShotGrid / Flow Desktop | Always (bundled Python + PySide6 + `sgtk`) |
| Network volume `atv-post-lucid3` | Always (shots, LUTs, staging) |
| QT Watcher on the Mac Studio | After Send (`com.buffalovfx.qtwatcher`) |
| Homebrew `openimageio` / `ffmpeg` | **Only when Preview is used** on EXR / DPX / MOV |

Send, reference copy, and 3D ingest do not need `oiiotool` or `ffmpeg`.
PNG / JPG / TIFF preview uses Qt only. Previewing EXR / DPX / MOV on this
Mac needs:

```bash
brew install openimageio ffmpeg
```

(The bake machine’s install is not used.) If those tools are missing when
you click **Preview…**, the app tells you and offers the brew command.

## Launch

Update the volume checkout onto the working branch first (tracked `.DS_Store`
files used to block `git checkout` — they are untracked now):

```bash
cd "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/alts/BUF_Mac_watcher"
git fetch origin
git checkout cursor/flow-record-option-1ade
git pull --ff-only origin cursor/flow-record-option-1ade
```

If checkout still complains about `.DS_Store`:

```bash
git rm -f --cached .DS_Store scripts/.DS_Store 2>/dev/null
git checkout -f cursor/flow-record-option-1ade
git pull --ff-only origin cursor/flow-record-option-1ade
```

Then double-click (or run in Terminal) — the launcher prints the branch and
commit so you can confirm you are not on a stale tree:

```bash
bash "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/alts/BUF_Mac_watcher/drop_app/launch_review_drop.command"
```

Optional desktop alias:

```bash
ln -sf \
  "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/alts/BUF_Mac_watcher/drop_app/launch_review_drop.command" \
  ~/Desktop/Buffalo_Review_Drop.command
chmod +x ~/Desktop/Buffalo_Review_Drop.command
```

## Files

| File | Role |
|------|------|
| `review_drop_app.py` | PySide6 UI |
| `staging.py` | Media detect, copy, flag write, Flow record |
| `preview.py` | Preview window, frame decoding, show-LUT color pipe |
| `theme.py` | Lumon 60-30-10 color tokens and stylesheets |
| `launch_review_drop.command` | Launcher |

Bake support for `include_slate` / `skip_color` / `movie_path` lives in
`../scripts/qt_bake_oiio.py`.
