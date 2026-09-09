"""
watcher_monitor.py

Small status panel for the qt_watcher LaunchAgent: shows whether it is
running and lets you start, restart or stop it, with a live tail of the log.

Runs under Flow Production Tracking Desktop's bundled Python (PySide6), the
same interpreter drop_app uses - launch via launch_watcher_monitor.command.

All launchctl work lives in watcher_service.py so it can be tested without a
display.
"""

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

import watcher_service as svc

POLL_MS = 3000          # status refresh; launchctl is cheap
LOG_LINES = 200

DOT = {
    "running": "#33CC33",
    "warn":    "#E6C229",
    "stopped": "#D93025",
}


class MonitorWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QT Watcher")
        self.resize(720, 520)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # -- Status row ------------------------------------------------------
        status_row = QHBoxLayout()
        self.dot = QLabel("*")
        self.dot.setFont(QFont("", 22))
        self.status_label = QLabel("checking...")
        self.status_label.setFont(QFont("", 15, QFont.Bold))
        status_row.addWidget(self.dot)
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("color: #888;")
        layout.addWidget(self.detail_label)

        # -- Buttons ---------------------------------------------------------
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.restart_btn = QPushButton("Restart")
        self.stop_btn = QPushButton("Stop")
        self.start_btn.clicked.connect(self.on_start)
        self.restart_btn.clicked.connect(self.on_restart)
        self.stop_btn.clicked.connect(self.on_stop)
        for b in (self.start_btn, self.restart_btn, self.stop_btn):
            b.setMinimumHeight(32)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # -- Log tail --------------------------------------------------------
        log_header = QLabel("Log  -  %s" % svc.LOG_PATH)
        log_header.setStyleSheet("color: #888;")
        layout.addWidget(log_header)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Menlo", 11))
        self.log_view.setStyleSheet(
            "background: #1e1e1e; color: #ddd; border: 1px solid #333;"
        )
        layout.addWidget(self.log_view, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.refresh()

    # -- Refresh -------------------------------------------------------------

    def refresh(self):
        st = svc.status()
        text, key = svc.summarize(st)

        self.dot.setStyleSheet("color: %s;" % DOT.get(key, "#888"))
        self.status_label.setText(text)

        bits = []
        if st["runs"] is not None:
            bits.append("runs: %s" % st["runs"])
        if st["last_exit"] is not None:
            bits.append("last exit: %s" % st["last_exit"])
        self.detail_label.setText("   ".join(bits) if bits else " ")

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
