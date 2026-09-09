"""
watcher_monitor.py

Status panel for the qt_watcher LaunchAgent: shows whether it is running and
lets you start, restart or stop it, with a live tail of the log.

Styled from drop_app/theme.py rather than a local palette, so the two apps
stay in lockstep - change the theme once and both follow.

Runs under Flow Production Tracking Desktop's bundled Python (PySide6), the
same interpreter drop_app uses - launch via launch_watcher_monitor.command.

All launchctl work lives in watcher_service.py so it can be tested without a
display.
"""

import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

import watcher_service as svc

# Shared Lumon palette lives with the Review Drop app.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "drop_app")
)
import theme  # noqa: E402

POLL_MS = 3000          # status refresh; launchctl is cheap
LOG_LINES = 200

# Status colours, kept inside the MDR palette rather than signal-bright RGB:
# a muted olive, brass and oxblood read as instrument lamps on the pale field.
DOT = {
    "running": theme.OLIVE,
    "warn":    "#8A7A3D",
    "stopped": "#7A3B34",
}
STATE_WORD = {
    "running": "RUNNING",
    "warn":    "ATTENTION",
    "stopped": "STOPPED",
}

# Brand mark. The dark teardrop reads correctly on the pale Lumon field.
# Tried in order because the variants differ in readability on the share -
# see core/templates.yml's show_logo note. Missing art is not an error: the
# masthead simply renders without it, exactly as drop_app does.
LOGO_DIR = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/_globals/logo"
)
LOGO_CANDIDATES = ("teardrop.png", "teardrop_blk1.png", "teardrop_blk.png")
LOGO_HEIGHT = 54


def find_logo():
    """First readable teardrop variant, or None."""
    for name in LOGO_CANDIDATES:
        path = os.path.join(LOGO_DIR, name)
        if os.path.isfile(path) and os.access(path, os.R_OK):
            return path
    return None


RULE_CSS = "background: %s;" % theme.STRUCTURE_LINE
READOUT_KEY_CSS = (
    "color: %s; font-size: 10px; letter-spacing: 2px;" % theme.INK_FAINT
)
READOUT_VAL_CSS = "color: %s; font-size: 12px;" % theme.INK


def _rule():
    """A one-pixel sage divider - the partition line."""
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(RULE_CSS)
    return line


class MonitorWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QT Watcher")
        self.resize(760, 580)
        self.setStyleSheet(theme.APP_CSS)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(34, 28, 34, 28)
        outer.setSpacing(0)

        # -- Masthead --------------------------------------------------------
        # Wordmark left, teardrop right: the mark closes the header band
        # rather than competing with the brand for the same corner.
        mast = QHBoxLayout()
        mast.setSpacing(16)

        mast_text = QVBoxLayout()
        mast_text.setSpacing(0)

        brand = QLabel("QT WATCHER")
        brand.setFont(QFont("", 20, QFont.Bold))
        brand.setStyleSheet("%s letter-spacing: 5px;" % theme.BRAND_CSS)
        mast_text.addWidget(brand)

        subtitle = QLabel("BUFFALO VFX  ·  RENDER MONITORING")
        subtitle.setStyleSheet(theme.SUBTITLE_CSS)
        mast_text.addWidget(subtitle)

        mast.addLayout(mast_text)
        mast.addStretch(1)

        logo = QLabel()
        logo.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        logo_path = find_logo()
        if logo_path:
            pix = QPixmap(logo_path)
            if not pix.isNull():
                logo.setPixmap(
                    pix.scaledToHeight(LOGO_HEIGHT, Qt.SmoothTransformation)
                )
        mast.addWidget(logo)

        outer.addLayout(mast)

        outer.addSpacing(18)
        outer.addWidget(_rule())
        outer.addSpacing(22)

        # -- Status ----------------------------------------------------------
        status_row = QHBoxLayout()
        status_row.setSpacing(14)

        self.dot = QLabel("●")
        self.dot.setFont(QFont("", 26))
        self.dot.setFixedWidth(28)
        self.dot.setAlignment(Qt.AlignCenter)
        status_row.addWidget(self.dot)

        stack = QVBoxLayout()
        stack.setSpacing(2)
        self.status_label = QLabel("CHECKING")
        self.status_label.setFont(QFont("", 17, QFont.Bold))
        self.status_label.setStyleSheet(
            "color: %s; letter-spacing: 3px;" % theme.INK
        )
        self.pid_label = QLabel(" ")
        self.pid_label.setStyleSheet(theme.HINT_CSS)
        stack.addWidget(self.status_label)
        stack.addWidget(self.pid_label)
        status_row.addLayout(stack)
        status_row.addStretch(1)
        outer.addLayout(status_row)

        outer.addSpacing(20)

        # -- Readout grid ----------------------------------------------------
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(2, 1)

        def readout(row, key):
            k = QLabel(key)
            k.setStyleSheet(READOUT_KEY_CSS)
            v = QLabel("—")
            v.setStyleSheet(READOUT_VAL_CSS)
            grid.addWidget(k, row, 0, Qt.AlignRight)
            grid.addWidget(v, row, 1, Qt.AlignLeft)
            return v

        self.runs_value = readout(0, "RUNS")
        self.exit_value = readout(1, "LAST EXIT")
        outer.addLayout(grid)

        outer.addSpacing(22)
        outer.addWidget(_rule())
        outer.addSpacing(18)

        # -- Controls --------------------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.start_btn = QPushButton("START")
        self.restart_btn = QPushButton("RESTART")
        self.stop_btn = QPushButton("STOP")

        self.start_btn.setStyleSheet(theme.PRIMARY_BUTTON_CSS)
        self.restart_btn.setStyleSheet(theme.SECONDARY_BUTTON_CSS)
        self.stop_btn.setStyleSheet(theme.GHOST_BUTTON_CSS)

        self.start_btn.clicked.connect(self.on_start)
        self.restart_btn.clicked.connect(self.on_restart)
        self.stop_btn.clicked.connect(self.on_stop)

        for b in (self.start_btn, self.restart_btn, self.stop_btn):
            b.setMinimumHeight(34)
            b.setMinimumWidth(112)
            b.setCursor(Qt.PointingHandCursor)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        outer.addSpacing(24)

        # -- Log -------------------------------------------------------------
        log_label = QLabel("LOG")
        log_label.setStyleSheet(READOUT_KEY_CSS)
        outer.addWidget(log_label)

        log_path = QLabel(svc.LOG_PATH)
        log_path.setStyleSheet(theme.HINT_CSS)
        outer.addWidget(log_path)
        outer.addSpacing(6)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Menlo", 10))
        self.log_view.setStyleSheet(
            "QPlainTextEdit {"
            "  background: %s;"
            "  color: %s;"
            "  border: 1px solid %s;"
            "  border-radius: %s;"
            "  padding: 10px;"
            "}" % (theme.SURFACE_RAISED, theme.INK_MUTED,
                   theme.STRUCTURE_LINE, theme.RADIUS_TIGHT)
        )
        outer.addWidget(self.log_view, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.refresh()

    # -- Refresh -------------------------------------------------------------

    def refresh(self):
        st = svc.status()
        text, key = svc.summarize(st)

        self.dot.setStyleSheet("color: %s;" % DOT.get(key, theme.INK_FAINT))
        self.status_label.setText(STATE_WORD.get(key, text.upper()))

        if st["pid"]:
            self.pid_label.setText("PID %s" % st["pid"])
        elif st["loaded"]:
            self.pid_label.setText(text)
        else:
            self.pid_label.setText("service not registered")

        self.runs_value.setText(
            "—" if st["runs"] is None else str(st["runs"])
        )
        self.exit_value.setText(st["last_exit"] or "—")

        self.start_btn.setEnabled(not st["loaded"])
        self.stop_btn.setEnabled(st["loaded"])
        self.restart_btn.setEnabled(True)

        self._update_log()

    def _update_log(self):
        """Repaint the tail, preserving the scroll position unless pinned."""
        bar = self.log_view.verticalScrollBar()
        pinned = bar.value() >= bar.maximum() - 4
        text = svc.read_log_tail(LOG_LINES)
        if text != self.log_view.toPlainText():
            pos = bar.value()
            self.log_view.setPlainText(text)
            if pinned:
                self.log_view.moveCursor(QTextCursor.End)
            else:
                bar.setValue(min(pos, bar.maximum()))

    # -- Actions -------------------------------------------------------------

    def _act(self, fn, label):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, out = fn()
        finally:
            QApplication.restoreOverrideCursor()
        if not ok:
            QMessageBox.warning(self, "%s failed" % label, out or "(no output)")
        self.refresh()

    def on_start(self):
        self._act(svc.start, "Start")

    def on_restart(self):
        self._act(svc.restart, "Restart")

    def on_stop(self):
        self._act(svc.stop, "Stop")


def main():
    app = QApplication(sys.argv)
    win = MonitorWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
