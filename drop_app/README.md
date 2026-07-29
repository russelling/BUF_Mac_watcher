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
5. Optional **Preview color…** to check the color pipe and switch off any
   stage you don't want (see below)
6. Copies media into the pipeline render area and writes a
   `.render_complete_*.json` flag
7. The Mac Studio **QT Watcher** bakes burn-ins / slate / upload on its next poll

Accepted stills: `exr, hdr, png, jpg, jpeg, tif, tiff, tga, bmp, dpx, cin,
gif, webp, psd, iff, sxr`. Drop one extension at a time (no mixed PNG+JPG).

### Reference images and original sources

Choose **Reference image** as the Delivery type to copy stills directly into
the selected Shot's `reference/` folder without creating a Flow Version.
**Naming override** is optional: it replaces the basename while preserving
the source extension and sequence frame numbers. Unchanged originals are
also retained under `reference/source/`.

For normal Version deliveries, the untouched original files are archived
under the selected Shot at:

`source/review_drop/{Shot}_{Step}_v###/`

### 3D asset deliveries (OBJ / FBX / GLB / PLY / USD / ABC …)

Drop a 3D file directly on the window. The app switches to **Asset** mode;
pick the **Asset Type** and click **Send to 3D Ingest** — the file is copied
into the ingest watch folder under that type, where the ingest watcher
converts and turntables it. (The **Open 3D Ingest Folder** button still opens
the drop folder in Finder for manual/bulk deliveries.)

Recognized 3D formats: `obj, fbx, glb, gltf, ply, stl, abc, usd, usdc, usda,
usdz, max, blend`.

### Color rules

| Source | Default color pipe |
|--------|--------------------|
| EXR / HDR | Full ACEScg → LogC4 → CDL → Show LUT → Rec.709 |
| PNG / JPG / TIFF / TGA / DPX / … | Skip color — burn-in (+ optional slate) only |
| MOV / MP4 | Skip color — burn-in (+ optional slate) only |

### Color pipeline preview

**Preview color…** opens a viewer that renders the dropped media through the
same oiiotool stages the Mac Studio bake uses, with a switch per stage:

| Stage | What it does |
|-------|--------------|
| ACEScg → LogC4 | Scene-linear input transform |
| Shot CDL (.cc) | Per-shot grade from `{shot}/plates/{shot}.cc` |
| Show LUT → Rec.709 | The show `.cube` |
| Rec.709 display transform | Fallback used only when the show LUT is off or missing |
| Anamorphic de-squeeze | Divides height by the source pixel aspect ratio |
| Letterbox to 1920×1080 | Fit + pad to the fixed delivery size |

Scrub the sequence with the slider, and use **Bypass everything** to A/B
against the untouched source (that switch is view-only and isn't submitted).
Stages that can't change anything — no `.cc` for the shot, square pixels, the
display transform while the show LUT is on — are disabled and say why.

**Use these settings** stores the map on the submission; it rides along in the
flag as `color_stages` and the watcher bakes exactly that, so the QT matches
what you approved. Without a preview the flag carries no `color_stages` and the
bake uses the defaults in the table above. The button carries a dot
(**Preview color •**) while a non-default pipe is set, and a new drop resets it.

The defaults follow the source: an EXR opens with the full pipe, a PNG or DPX
opens with the color stages off. Switch them on for a log-encoded DPX, or turn
the show LUT off to check an ungraded pass.

The preview shells out to `oiiotool`, so it needs OpenImageIO locally
(`brew install openimageio`). Without it the window still edits the stage
switches — it just can't draw the frame. Movies, 3D deliveries, and reference
copies never reach the color pipe, so the button stays off for them (hover for
the reason).

### 3D ingest shortcut

**Open 3D Asset Ingest Folder** reveals:

`/Volumes/.../buffalo_vfx/_staging/assets_incoming/`

Drop into `Character` / `Prop` / `Environment` / `Vehicle` / `FX` as before.

## Requirements

- ShotGrid / Flow Desktop installed (bundled Python + PySide6)
- Network volume mounted (`atv-post-lucid3`)
- QT Watcher running on the Mac Studio (`com.buffalovfx.qtwatcher`)
- `brew install openimageio` — only for the color preview; everything else
  works without it

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
| `staging.py` | Media detect, copy, flag write |
| `preview.py` | Color pipeline preview window |
| `launch_review_drop.command` | Launcher |

Bake support for `include_slate` / `skip_color` / `movie_path` /
`color_stages` lives in `../scripts/qt_bake_oiio.py`. The stage list and the
oiiotool arguments themselves are in `../scripts/color_pipeline.py`, shared by
the bake and the preview so the two can't drift apart.
