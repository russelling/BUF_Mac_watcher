"""
reflag_dialog.py

"RE-RUN QT" dialog for the watcher monitor: pick a shot, a task and one of
its rendered versions, and write a fresh .render_complete_*.json flag so
qt_watcher bakes and uploads it again on its next poll.

The flag is built from scratch out of ShotGrid + what is actually on disk,
not by reviving an old .processed_ flag - an old flag can carry a stale
frame range, description or path from before a re-render, and reviving it
would quietly bake the wrong thing.

All the non-Qt work lives in flag_builder.py so it can be tested headless.
Everything here is layout, wiring, and telling the user what will happen
before it happens.
"""

import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

import flag_builder as fb

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "drop_app")
)
import theme  # noqa: E402

# A shot list that long is easier to type into than to scroll.
FILTER_HINT = "type to filter…"


class ReflagDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Re-run QT")
        self.setModal(True)
        self.resize(660, 560)
        self.setStyleSheet(theme.APP_CSS)

        self.tk = None
        self.project = None
        self.shots = []          # every shot, unfiltered
        self.tasks = []
        self.renders = []
        self.fields = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(0)

        title = QLabel("RE-RUN QT")
        title.setFont(QFont("", 15, QFont.Bold))
        title.setStyleSheet("%s letter-spacing: 4px;" % theme.BRAND_CSS)
        outer.addWidget(title)

        blurb = QLabel(
            "Writes a render-complete flag. The watcher picks it up on its "
            "next poll and re-bakes the QT."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet(theme.SUBTITLE_CSS)
        outer.addWidget(blurb)
        outer.addSpacing(18)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(FILTER_HINT)
        self.filter_edit.textChanged.connect(self._apply_filter)

        self.shot_combo = QComboBox()
        self.task_combo = QComboBox()
        self.render_combo = QComboBox()
        self.submitted_combo = QComboBox()
        self.artist_edit = QLineEdit(fb.DEFAULT_ARTIST)
        self.description_edit = QPlainTextEdit()
        self.description_edit.setFixedHeight(64)
        self.description_edit.setPlaceholderText("Re-run from QT Watcher monitor.")

        for w in (self.shot_combo, self.task_combo, self.render_combo,
                  self.submitted_combo):
            w.setMinimumHeight(28)

        self.shot_combo.currentIndexChanged.connect(self._on_shot_changed)
        self.task_combo.currentIndexChanged.connect(self._on_task_changed)
        self.render_combo.currentIndexChanged.connect(self._update_preview)

        form.addRow(self._key("FILTER"), self.filter_edit)
        form.addRow(self._key("SHOT"), self.shot_combo)
        form.addRow(self._key("TASK"), self.task_combo)
        form.addRow(self._key("RENDER"), self.render_combo)
        form.addRow(self._key("SUBMITTED FOR"), self.submitted_combo)
        form.addRow(self._key("ARTIST"), self.artist_edit)
        form.addRow(self._key("DESCRIPTION"), self.description_edit)
        outer.addLayout(form)

        outer.addSpacing(16)

        # Everything the write will do, in words, before the button is armed.
        self.preview = QLabel("Connecting to Flow…")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setStyleSheet(
            "QLabel {"
            "  background: %s;"
            "  color: %s;"
            "  border: 1px solid %s;"
            "  border-radius: %s;"
            "  padding: 10px;"
            "  font-size: 11px;"
            "}" % (theme.SURFACE_RAISED, theme.INK_MUTED,
                   theme.STRUCTURE_LINE, theme.RADIUS_TIGHT)
        )
        outer.addWidget(self.preview, 1)

        outer.addSpacing(16)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.cancel_btn = QPushButton("CANCEL")
        self.write_btn = QPushButton("WRITE FLAG")
        self.cancel_btn.setStyleSheet(theme.GHOST_BUTTON_CSS)
        self.write_btn.setStyleSheet(theme.PRIMARY_BUTTON_CSS)
        for b in (self.cancel_btn, self.write_btn):
            b.setMinimumHeight(34)
            b.setMinimumWidth(120)
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        self.cancel_btn.clicked.connect(self.reject)
        self.write_btn.clicked.connect(self.on_write)
        self.write_btn.setEnabled(False)
        outer.addLayout(row)

        # Bootstrapping sgtk takes a few seconds; let the window paint first
        # so it doesn't look like a hang.
        QTimer.singleShot(0, self._connect)

    def _key(self, text):
        lab = QLabel(text)
        lab.setStyleSheet(
            "color: %s; font-size: 10px; letter-spacing: 2px;" % theme.INK_FAINT
        )
        return lab

    # -- Loading -------------------------------------------------------------

    def _connect(self):
        """Bootstrap Toolkit and fill the shot list."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.tk = fb.get_tk()
            self.shots = fb.list_shots(self.tk)
            self.project = fb.project_info(self.tk)
            options = fb.submitted_for_options(self.tk)
        except Exception as exc:
            self._fail(
                "Could not connect to Flow:\n\n%s: %s\n\nConfig root:\n%s"
                % (type(exc).__name__, exc, fb.CONFIG_ROOT)
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.submitted_combo.addItems(options)
        if not self.shots:
            self._fail("No shots found in this project.")
            return
        self._apply_filter("")

    def _fail(self, message):
        self.preview.setText(message)
        self.write_btn.setEnabled(False)

    # -- Cascade -------------------------------------------------------------

    def _apply_filter(self, text):
        """Rebuild the shot combo from the filter, keeping the selection if it survives."""
        if not self.shots:
            return
        needle = (text or "").strip().lower()
        matches = [s for s in self.shots
                   if not needle or needle in (s.get("code") or "").lower()]
        keep = self.current_shot()

        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        for s in matches:
            self.shot_combo.addItem(s.get("code") or "(unnamed)", s)
        if keep:
            idx = next((i for i, s in enumerate(matches)
                        if s["id"] == keep["id"]), -1)
            if idx >= 0:
                self.shot_combo.setCurrentIndex(idx)
        self.shot_combo.blockSignals(False)

        if not matches:
            self.task_combo.clear()
            self.render_combo.clear()
            self._fail("No shot matches “%s”." % text)
            return
        self._on_shot_changed()

    def current_shot(self):
        return self.shot_combo.currentData()

    def current_task(self):
        return self.task_combo.currentData()

    def current_render(self):
        return self.render_combo.currentData()

    def _on_shot_changed(self, *_):
        shot = self.current_shot()
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        self.tasks = []
        if shot:
            try:
                self.tasks = fb.tasks_for_shot(self.tk, shot["id"])
            except Exception as exc:
                self._fail("Could not read tasks: %s" % exc)
        for t in self.tasks:
            label = t.get("content") or t.get("step_name") or "Task %s" % t["id"]
            if t.get("step"):
                label = "%s  ·  %s" % (label, t["step"])
            self.task_combo.addItem(label, t)
        self.task_combo.blockSignals(False)
        self._on_task_changed()

    def _on_task_changed(self, *_):
        """Resolve context fields for the task, then list what it has rendered."""
        self.render_combo.blockSignals(True)
        self.render_combo.clear()
        self.renders = []
        self.fields = {}
        self.render_combo.blockSignals(False)

        task = self.current_task()
        if not task:
            self._fail("This shot has no tasks.")
            return

        try:
            self.fields = fb.context_fields(self.tk, task["id"])
        except Exception as exc:
            self._fail(
                "Toolkit could not resolve this task's folders:\n\n%s\n\n"
                "That usually means folders were never registered for it. "
                "Run:  tank Task %s folders" % (exc, task["id"])
            )
            return

        missing = [k for k in ("Episode", "Scene", "Shot", "Step")
                   if not self.fields.get(k)]
        if missing:
            self._fail(
                "Missing template fields for this task: %s\n\n"
                "The path cache has no folders registered for it. Run:\n"
                "  tank Task %s folders" % (", ".join(missing), task["id"])
            )
            return

        try:
            self.renders = fb.find_renders(self.tk, self.fields)
        except Exception as exc:
            self._fail("Could not scan renders: %s" % exc)
            return

        if not self.renders:
            self._fail(
                "No rendered EXRs on disk for %s / %s.\n\nThe QT is baked from "
                "the EXRs, so there is nothing to re-run until this task has "
                "rendered." % (self.fields.get("Shot"), self.fields.get("Step"))
            )
            return

        self.render_combo.blockSignals(True)
        for r in self.renders:
            self.render_combo.addItem(fb.describe_render(r), r)
        self.render_combo.blockSignals(False)
        self.render_combo.setCurrentIndex(0)   # newest version
        self._update_preview()

    # -- Preview -------------------------------------------------------------

    def _flag_path(self, render):
        return fb.flag_path_for(self.tk, self.fields, render)

    def _update_preview(self, *_):
        render = self.current_render()
        if not render:
            return
        try:
            flag_path = self._flag_path(render)
        except Exception as exc:
            self._fail("Could not resolve the flag path: %s" % exc)
            return

        lines = ["Flag:  %s" % flag_path,
                 "EXRs:  %s" % render["exr_path_pattern"],
                 "Frames:  %d–%d  (%d on disk)"
                 % (render["frame_first"], render["frame_last"],
                    render["frame_count"])]

        expected = render["frame_last"] - render["frame_first"] + 1
        if render["frame_count"] != expected:
            lines.append(
                "WARNING: %d of %d frames are on disk. The bake will encode "
                "only what exists." % (render["frame_count"], expected)
            )
        if os.path.exists(flag_path):
            lines.append(
                "A flag is already sitting here unprocessed — writing will "
                "replace it."
            )
        elif fb.existing_processed_flag(flag_path):
            lines.append("This version was baked before; it will be baked again "
                         "and a new Version created in Flow.")

        self.preview.setText("\n\n".join(lines))
        self.write_btn.setEnabled(True)

    # -- Write ---------------------------------------------------------------

    def on_write(self):
        shot, task, render = self.current_shot(), self.current_task(), self.current_render()
        if not (shot and task and render):
            return

        artist = self.artist_edit.text().strip() or fb.DEFAULT_ARTIST
        cut_in, cut_out = fb.cut_range(self.tk, shot["id"])
        data = fb.build_flag_data(
            shot=shot,
            task=task,
            step=self.fields.get("Step"),
            render=render,
            submitted_for=self.submitted_combo.currentText(),
            description=self.description_edit.toPlainText().strip(),
            episode=self.fields.get("Episode"),
            scene=self.fields.get("Scene"),
            project=self.project,
            artist=artist,
            user_id=fb.human_user_id(self.tk, artist) or fb.current_user_id(self.tk),
            cut_in=cut_in,
            cut_out=cut_out,
        )

        problems = fb.validate_flag(data)
        if problems:
            QMessageBox.warning(self, "Flag is incomplete",
                                "\n".join("• %s" % p for p in problems))
            return

        flag_path = self._flag_path(render)
        overwrite = False
        if os.path.exists(flag_path):
            answer = QMessageBox.question(
                self, "Replace existing flag",
                "A flag for this version is already waiting to be processed:"
                "\n\n%s\n\nReplace it?" % flag_path,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            overwrite = True

        ok, message = fb.write_flag(flag_path, data, overwrite=overwrite)
        if not ok:
            QMessageBox.warning(self, "Could not write flag", message)
            return

        QMessageBox.information(
            self, "Flag written",
            "%s v%03d is queued.\n\nThe watcher picks it up within 30 seconds; "
            "watch the log for the bake.\n\n%s"
            % (shot.get("code"), render["version"], flag_path),
        )
        self.accept()
