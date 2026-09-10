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
    QApplication, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

import flag_builder as fb

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "drop_app")
)
import theme  # noqa: E402

# A shot list that long is easier to type into than to scroll.
FILTER_HINT = "type to filter…"

# Colour-source lamps, in the same muted palette the monitor's status dot uses:
#   ok      the shot's own file will be applied
#   show    no per-shot LUT; the global show LUT stands in (normal, but say so)
#   none    nothing found - the QT will be ungraded / off-look
#   unknown the lookup could not run
LAMP = {
    "ok":      theme.OLIVE,
    "warn":    "#8A7A3D",
    "show":    "#8A7A3D",
    "none":    "#7A3B34",
    "unknown": theme.INK_FAINT,
}
LAMP_WORD = {
    "ok": "FOUND",
    "warn": "CHECK",
    "show": "SHOW LUT",
    "none": "NOT FOUND",
    "unknown": "UNKNOWN",
}


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
        self.colour = None

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

        # -- Colour sources --------------------------------------------------
        # Answered by qt_bake_oiio's own lookups, so what is shown here is
        # literally what the bake will pick up - see flag_builder.color_sources.
        colour = QGridLayout()
        colour.setHorizontalSpacing(10)
        colour.setVerticalSpacing(4)
        colour.setColumnStretch(2, 1)

        def lamp_row(row, key):
            dot = QLabel("●")
            dot.setFont(QFont("", 13))
            dot.setFixedWidth(16)
            dot.setAlignment(Qt.AlignCenter)
            name = QLabel(key)
            name.setStyleSheet(
                "color: %s; font-size: 10px; letter-spacing: 2px;" % theme.INK_FAINT
            )
            name.setFixedWidth(44)
            detail = QLabel("—")
            detail.setWordWrap(True)
            detail.setStyleSheet("color: %s; font-size: 11px;" % theme.INK)
            colour.addWidget(dot, row, 0)
            colour.addWidget(name, row, 1)
            colour.addWidget(detail, row, 2)
            return dot, detail

        self.cdl_dot, self.cdl_detail = lamp_row(0, "CDL")
        self.lut_dot, self.lut_detail = lamp_row(1, "LUT")
        outer.addLayout(colour)

        outer.addSpacing(14)

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
        self._clear_lamps()

    # -- Colour lamps --------------------------------------------------------

    def _clear_lamps(self):
        self.colour = None
        for dot, detail in ((self.cdl_dot, self.cdl_detail),
                            (self.lut_dot, self.lut_detail)):
            dot.setStyleSheet("color: %s;" % theme.INK_FAINT)
            detail.setText("—")

    def _refresh_lamps(self):
        """
        Ask the bake what it will pick up for this shot.

        Per shot, not per version - the CDL and LUT live in the shot's plates/
        folder - so this runs when the context changes rather than on every
        version click.
        """
        self.colour = fb.color_sources(fb.color_probe_data(self.fields))
        for key, dot, detail in (("cdl", self.cdl_dot, self.cdl_detail),
                                 ("lut", self.lut_dot, self.lut_detail)):
            entry = self.colour[key]
            dot.setStyleSheet("color: %s;" % LAMP[entry["status"]])
            detail.setText("%s   %s" % (LAMP_WORD[entry["status"]],
                                        entry["detail"]))
            dot.setToolTip(entry["path"] or entry["detail"])
            detail.setToolTip(entry["path"] or entry["detail"])

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

        self._refresh_lamps()

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

        # A missing CDL is not an error - plenty of shots have none - but it
        # is the difference between a graded and an ungraded QT, so it is
        # said in words here as well as shown on the lamp.
        colour = getattr(self, "colour", None)
        if colour:
            if colour["cdl"]["status"] == "none":
                lines.append("No CDL for this shot — the QT will bake UNGRADED.")
            if colour["lut"]["status"] == "none":
                lines.append(
                    "No LUT at all — the bake falls back to a generic display "
                    "transform and the look will not match the show."
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

        colour_note = ""
        if self.colour:
            colour_note = "\n\nCDL: %s\nLUT: %s" % (
                self.cdl_detail.text(), self.lut_detail.text())

        QMessageBox.information(
            self, "Flag written",
            "%s v%03d is queued.\n\nThe watcher picks it up within 30 seconds; "
            "watch the log for the bake.\n\n%s%s"
            % (shot.get("code"), render["version"], flag_path, colour_note),
        )
        self.accept()
