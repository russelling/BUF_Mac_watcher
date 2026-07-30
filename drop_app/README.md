# Buffalo Review Drop

Standalone Mac app for sending **external** review media (images / EXR / MOV)
into the existing QT Watcher pipeline, plus a shortcut to the 3D asset ingest
drop folder.

Branded square window (BUFFALO VFX, Manifold Extended Medium) with a
compact two-column layout.

## What it does

### Review media (images / EXR / MOV)

1. **Drag & drop** an image sequence, single still, EXR, or MOV/MP4
2. Pick **Shot** (Episode → Sequence → Shot) or **Asset** from Flow
3. Set Step / Version / Submitted by / Submitted for / Notes
4. Optional **Include slate** (especially useful for single frames)
5. Copies media into the pipeline render area and writes a
   `.render_complete_*.json` flag
6. The Mac Studio **QT Watcher** bakes burn-ins / slate / upload on its next poll

Accepted stills: `exr, hdr, png, jpg, jpeg, tif, tiff, tga, bmp, dpx, cin,
gif, webp, psd, iff, sxr`. Drop one extension at a time (no mixed PNG+JPG).

### Reference media and original sources

Choose **Reference** as the Delivery type to copy stills or a QT straight
into the selected Shot's (or Asset's) `reference/` folder, skipping the bake.
**Naming override** is optional: it replaces the basename while preserving
the source extension and sequence frame numbers. Unchanged originals are
also retained under `reference/source/`.

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

### 3D asset deliveries (OBJ / FBX / GLB / PLY / USD / ABC …)

Drop a 3D file directly on the window. The app switches to **Asset** mode;
pick the **Asset Type** and click **Send to 3D Ingest** — the file is copied
into the ingest watch folder under that type, where the ingest watcher
converts and turntables it. Tick **Create Flow record** to also log the
delivery against a Flow Asset. (The **Open 3D Ingest Folder** button still
opens the drop folder in Finder for manual/bulk deliveries.)

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

Drop into `Character` / `Prop` / `Environment` / `Vehicle` / `FX` as before.

## Requirements

- ShotGrid / Flow Desktop installed (bundled Python + PySide6)
- Network volume mounted (`atv-post-lucid3`)
- QT Watcher running on the Mac Studio (`com.buffalovfx.qtwatcher`)

## Launch

Double-click (or run in Terminal):

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
| `launch_review_drop.command` | Launcher |

Bake support for `include_slate` / `skip_color` / `movie_path` lives in
`../scripts/qt_bake_oiio.py`.
