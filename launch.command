cat << 'EOF' > ~/Desktop/start_qt_watcher.command
#!/bin/bash
launchctl load ~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist
echo "QT Watcher started."
EOF

chmod +x ~/Desktop/start_qt_watcher.command