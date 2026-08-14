# Prerequisites — install these BEFORE the LaunchAgent

> **Read this first.** Every dependency below must be installed and verified
> *before* you copy or bootstrap `com.buffalovfx.qtwatcher.plist`. `launchctl`
> reports almost every failure as `Load failed: 5: Input/output error` or
> `Bootstrap failed: 5: Input/output error`, which tells you nothing about
> which piece is missing. Installing in this order turns a multi-hour
> debugging session into a checklist.
>
> Verified on a fresh Mac (M4 Mac Studio, macOS 26, 2026-08-13).

---

## 0. Storage mount

The LucidLink / SMB share must be mounted before anything else, or every
path check below silently returns nothing.

```bash
ls /Volumes/
ls -d /Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx
```

## 1. Homebrew

Not preinstalled on macOS. Provides `oiiotool` and `ffmpeg`.

- Site: <https://brew.sh>

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

On Apple Silicon this installs to `/opt/homebrew`. Follow the post-install
instructions it prints to add `/opt/homebrew/bin` to your PATH (the watcher's
plist uses absolute paths, so PATH only matters for your interactive testing).

```bash
which brew          # expect /opt/homebrew/bin/brew
```

## 2. OpenImageIO (`oiiotool`)

Does the ACEScg → LogC4 → CDL → Show LUT → Rec.709 colour pipeline and builds
the slate thumbnails.

```bash
brew install openimageio
oiiotool --version           # expect /opt/homebrew/bin/oiiotool to exist
```

## 3. ffmpeg — the **full** build, not core

Encodes ProRes 422 HQ and draws the slate text and per-frame burn-ins.

> **The core `ffmpeg` formula is not sufficient** — it lacks the
> `drawtext`/freetype support the slate and burn-ins require. See
> `watcher_launch.txt` in this repo for the exact formula/tap this studio
> uses (expected install path `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`).

Acceptance test — this is the one that matters, regardless of which formula
you installed:

```bash
ffmpeg -filters | grep drawtext
```

If `drawtext` is not listed, the bake **will** fail at runtime with the
watcher otherwise looking perfectly healthy in `launchctl`.

## 4. Flow Production Tracking Desktop app

Supplies the bundled Python used to run `qt_watcher.py`, and the
authenticated session it needs — `qt_watcher.py` authenticates via
`sgtk.sgtk_from_path()` against the pipeline configuration itself (no
separate API script/credentials), so Desktop must be installed *and signed
in at least once* as the user the LaunchAgent runs as.

**Download from your own site**, so the installer matches the site version:
<https://buffalovfx.shotgrid.autodesk.com> → Apps menu → Desktop App.
Autodesk also documents current/older download locations
[here](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Location-to-download-Flow-Production-Tracking-Desktop-App-from-the-community-forum.html).

After installing, sign in to the `buffalovfx` site once in the GUI, then
verify the interpreter:

```bash
ls -l  /Applications/Shotgun.app/Contents/Resources/Python3/bin/python3
/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3 --version
```

> **Version note (2026-08):** current Desktop bundles **Python 3.13**
> (earlier notes in this repo said 3.11). Confirmed working against the
> project config's pinned tk-core **v0.23.8** — no interpreter workaround
> needed. Re-run the import test in step 5 after any Desktop upgrade.

## 5. Combined verification — run this before touching the plist

```bash
CONFIG_ROOT=/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/current

# config reachable at the location install_location.yml declares
ls -d "$CONFIG_ROOT/config/core/pipeline_configuration.yml"

# sgtk + shotgun_api3 import under the bundled interpreter
PYTHONPATH="$CONFIG_ROOT/install/core/python" \
/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3 \
  -c "import sgtk, shotgun_api3; print('sgtk OK', sgtk.__file__)"

# bake tools
oiiotool --version
ffmpeg -filters | grep drawtext
```

All four must pass. Only then install the LaunchAgent.

---

## Symptom → cause, when you skip a step

| Symptom | Actual cause |
|---|---|
| `Load failed: 5: Input/output error` | Plist file missing, unreadable, **or being loaded directly off the network share** — launchd will not accept a plist from `/Volumes/...`. Copy it local first. |
| `Bootstrap failed: 5: Input/output error` | Same. Most often the plist simply isn't at the path you gave. |
| `Could not find service "com.buffalovfx.qtwatcher"` | Nothing ever registered — the load/bootstrap above failed. Not a separate problem. |
| Job shows `runs = 1`, `state = not running`, `last exit code = 78: EX_CONFIG` | `StandardOutPath`/`StandardErrorPath` point at the SMB share. launchd's spawn context lacks the session's SMB auth and can't create the redirect targets. Point them at a **local** path. |
| Watcher "runs" but no QTs ever appear | Missing `oiiotool`/`drawtext`, unreachable `SHOTS_ROOT`/`ASSETS_ROOT`, or sgtk failing to bootstrap. Check the log, not `launchctl`. |

## launchd rules learned the hard way

- **Never bootstrap a plist from the share.** Copy to
  `~/Library/LaunchAgents/` (local disk) first, `chmod 644`.
- **Plists do not expand `~`.** Use the full `/Users/<user>/...` path in
  `StandardOutPath`, `StandardErrorPath`, and anywhere else.
- **Don't run it as root**, despite what launchctl's error text suggests.
  This is a per-user LaunchAgent; the root/system domain does not carry the
  login session's SMB authentication, which is exactly what breaks access to
  the share. Always `gui/$(id -u)`.
- **Prefer `bootout` + `bootstrap` + `kickstart` over `unload`/`load`**,
  especially after failed attempts — `unload`/`load` can resume corrupted
  state.
- **`launchctl print gui/$(id -u)/<label>` beats `launchctl list`** — it
  shows `runs`, `last exit code`, and the full resolved
  ProgramArguments/paths/environment.
- **Validate before bootstrapping:** `plutil -lint <plist>`.

## Path relocation warning

The config moved from `buffalo_vfx/buffalo_flow_config` to
`buffalo_vfx/repo/pipeline/config/flow/current` (2026-08). Any plist,
script, or `PYTHONPATH` still referencing the old location will fail. Grep
before deploying:

```bash
grep -rn "buffalo_flow_config" .
```

## Before going live: one watcher, not two

If the old Mac Studio is still running `com.buffalovfx.qtwatcher`, boot it
out before starting this one. Two watchers polling the same
`SHOTS_ROOT`/`ASSETS_ROOT` will race on the same `.render_complete_*.json`
flags — double-encoding and creating duplicate Versions in Flow.

```bash
# on the OLD machine
launchctl bootout gui/$(id -u)/com.buffalovfx.qtwatcher
```
