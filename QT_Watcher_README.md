# QT Watcher — Launch Instructions

Source: https://github.com/russelling/BUF_Mac_watcher
("Render monitor and qt renderer for exr files.")

QT Watcher runs as a **macOS LaunchAgent** (`com.buffalovfx.qtwatcher`) that
executes `scripts/qt_watcher.py` using the Python 3.11 interpreter bundled
with the Shotgun/Flow desktop app.

## Everyday launch (agent already installed)

Once the LaunchAgent has been installed (see one-time setup below), you have
two ways to start it:

**Option A — double-click launcher**

Double-click `~/Desktop/start_qt_watcher.command`. This runs:

```bash
launchctl load ~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist
```

and prints `QT Watcher started.`

**Option B — Terminal**

```bash
launchctl load ~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist
```

Because the plist has `RunAtLoad` and `KeepAlive` set to `true`, the watcher
also starts automatically on login/reboot and relaunches itself if it
crashes — you generally only need to run the load command after it's been
unloaded (e.g. after `launchctl unload`, or a fresh machine setup).

## Checking it's running

```bash
launchctl list | grep qtwatcher
```

Logs:

- Output: `/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config/logs/qt_watcher.log`
- Errors: `/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config/logs/qt_watcher_error.log`

## Stopping it

```bash
launchctl unload ~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist
```

## One-time setup (new machine, or reinstalling)

1. Make sure the shared volume is mounted (`atv-post-lucid3`) and the repo
   exists at:
   `/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config_alts/BUF_Mac_watcher`
2. In Terminal, run the installer script (it has no shebang, so invoke it
   with `bash`, not `./`):

   ```bash
   cd "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config_alts/BUF_Mac_watcher"
   bash watcher_launch.txt
   ```

   This will:
   - create the shared logs folder
   - write `~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist`
   - `launchctl load` it
   - confirm with `launchctl list | grep qtwatcher`
3. Run the desktop-launcher generator once (same directory):

   ```bash
   bash launch.command
   ```

   This creates `~/Desktop/start_qt_watcher.command` (Option A above).

## What the LaunchAgent runs

```
/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3.11 \
  /Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config_alts/BUF_Mac_watcher/scripts/qt_watcher.py
```

Working directory: `.../BUF_Mac_watcher/scripts`

Related scripts in the same folder: `qt_bake_oiio.py`, `qt_bake_slate_burnin.py`.
