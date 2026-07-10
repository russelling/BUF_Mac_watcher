cat << 'EOF' > ~/Desktop/start_qt_watcher.command
#!/bin/bash
launchctl load ~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist
echo "QT Watcher started."
EOF

chmod +x ~/Desktop/start_qt_watcher.command

# ---------------------------------------------------------------------------
# Apply the custom icon (stored in this repo as qt_watcher_icon.icns).
# Icons live in a file's extended attributes, so regenerating the .command
# above wipes any previously applied icon - reapply it every time.
# ---------------------------------------------------------------------------
ICON="/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config_alts/BUF_Mac_watcher/qt_watcher_icon.icns"

# Make brew visible even if the shell profile isn't set up yet
if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

if [ -e "$ICON" ]; then
    if ! command -v fileicon >/dev/null 2>&1 && command -v brew >/dev/null 2>&1; then
        brew install fileicon
    fi
    if command -v fileicon >/dev/null 2>&1; then
        fileicon set ~/Desktop/start_qt_watcher.command "$ICON" && echo "Icon applied."
    else
        echo "NOTE: fileicon not available - icon skipped. To apply manually:"
        echo "      brew install fileicon"
        echo "      fileicon set ~/Desktop/start_qt_watcher.command \"$ICON\""
    fi
else
    echo "NOTE: icon not found at $ICON - skipped."
fi
