"""
external_render_dialog.py

"EXTERNAL RENDER" dialog for the watcher monitor: take an EXR sequence or a
QT that was made outside the pipeline, put it in a shot, and get it into
today's _staging submission package with its CSV row.

  EXR  -> copied into the shot's render/work version folder, then a flag.
          The user is asked which colour stages to apply (or ignore); the
          watcher bakes it into the package and writes the CSV row.
  QT   -> copied into the same folder, then the user MUST answer one
          question: add a slate + burn-ins (watcher re-wraps it, colour
          untouched) or deliver as-is (straight into the package + CSV now).

Shot / task pickers and the CDL/LUT/FRAME lamps are the same ones RE-RUN QT
uses (flag_builder), so both dialogs ask the bake the same questions. All the
non-Qt work lives in external_render.py.
"""

import os
import sys

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QIntValidator
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

import flag_builder as fb
import external_render as er

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "drop_app")
)
import theme  # noqa: E402

from reflag_dialog import LAMP, LAMP_WORD, FILTER_HINT  # noqa: E402

KEY_CSS = "color: %s; font-size: 10px; letter-spacing: 2px;" % theme.INK_FAINT
DETAIL_CSS = "color: %s; font-size: 11px;" % theme.INK


class _Worker(QThread):
    """Runs the copy / delivery off the UI thread; a share copy can take minutes."""
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except er.ExternalError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                    # noqa: BLE001
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))


class ExternalRenderDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("External render")
        self.setModal(True)
        self.resize(700, 780)
        self.setStyleSheet(theme.APP_CSS)

        self.tk = None
        self.project = None
        self.shots = []
        self.tasks = []
        self.fields = {}
        self.src = None
        self.colour = None
        self.framing = None
        self.worker = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(0)

        title = QLabel("EXTERNAL RENDER")
        title.setFont(QFont("", 15, QFont.Bold))
        title.setStyleSheet("%s letter-spacing: 4px;" % theme.BRAND_CSS)
        outer.addWidget(title)
        blurb = QLabel(
            "Bring an EXR sequence or QT made outside the pipeline into a shot "
            "and today's _staging submission package, with its CSV row."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet(theme.SUBTITLE_CSS)
        outer.addWidget(blurb)
        outer.addSpacing(16)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # -- Source ------------------------------------------------------------
        src_row = QHBoxLayout()
        self.src_label = QLabel("nothing chosen")
        self.src_label.setWordWrap(True)
        self.src_label.setStyleSheet(DETAIL_CSS)
        choose = QPushButton("CHOOSE…")
        choose.setStyleSheet(theme.SECONDARY_BUTTON_CSS)
        choose.setCursor(Qt.PointingHandCursor)
        choose.clicked.connect(self.on_choose)
        src_row.addWidget(self.src_label, 1)
        src_row.addWidget(choose)
        form.addRow(self._key("SOURCE"), src_row)

        # -- Context -----------------------------------------------------------
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(FILTER_HINT)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.shot_combo = QComboBox()
        self.task_combo = QComboBox()
        for w in (self.shot_combo, self.task_combo):
            w.setMinimumHeight(28)
        self.shot_combo.currentIndexChanged.connect(self._on_shot_changed)
        self.task_combo.currentIndexChanged.connect(self._on_task_changed)
        form.addRow(self._key("FILTER"), self.filter_edit)
        form.addRow(self._key("SHOT"), self.shot_combo)
        form.addRow(self._key("TASK"), self.task_combo)

        ver_row = QHBoxLayout()
        ver_row.setSpacing(10)
        self.version_spin = QSpinBox()
        self.version_spin.setRange(1, 999)
        self.version_spin.setPrefix("v")
        self.version_spin.setMinimumHeight(28)
        self.version_spin.valueChanged.connect(self._update_preview)
        self.vendor_edit = QLineEdit(fb.DEFAULT_VENDOR)
        self.vendor_edit.setFixedWidth(70)
        self.vendor_edit.setToolTip("Vendor code in the file and Version name "
                                    "(INH for in-house).")
        self.vendor_edit.textChanged.connect(self._on_vendor_changed)
        self.first_edit = QLineEdit("1001")
        self.first_edit.setValidator(QIntValidator(0, 9999999, self))
        self.first_edit.setFixedWidth(80)
        self.first_edit.setAlignment(Qt.AlignCenter)
        self.first_edit.textChanged.connect(self._update_preview)
        ver_row.addWidget(self.version_spin)
        ver_row.addSpacing(14)
        ver_row.addWidget(self._key("VENDOR"))
        ver_row.addWidget(self.vendor_edit)
        ver_row.addSpacing(14)
        ver_row.addWidget(self._key("FIRST FRAME"))
        ver_row.addWidget(self.first_edit)
        ver_row.addStretch(1)
        form.addRow(self._key("VERSION"), ver_row)

        self.submitted_combo = QComboBox()
        self.submitted_combo.setMinimumHeight(28)
        self.artist_edit = QLineEdit(fb.DEFAULT_ARTIST)
        self.description_edit = QPlainTextEdit()
        self.description_edit.setFixedHeight(56)
        self.description_edit.setPlaceholderText("External render.")
        form.addRow(self._key("SUBMITTED FOR"), self.submitted_combo)
        form.addRow(self._key("ARTIST"), self.artist_edit)
        form.addRow(self._key("DESCRIPTION"), self.description_edit)
        outer.addLayout(form)
        outer.addSpacing(14)

        # -- EXR colour questions ---------------------------------------------
        self.exr_box = QWidget()
        g = QGridLayout(self.exr_box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(6)
        g.setColumnStretch(3, 1)
        head = QLabel("COLOUR PIPE  ·  untick a stage to ignore it")
        head.setStyleSheet(KEY_CSS)
        g.addWidget(head, 0, 0, 1, 4)

        self.log_check = QCheckBox("Convert → LogC4")
        self.cdl_check = QCheckBox("Shot CDL")
        self.lut_check = QCheckBox("LUT → Rec.709")
        self.source_combo = QComboBox()
        for label, value in er.SOURCE_SPACES:
            self.source_combo.addItem(label, value)
        self.source_combo.setToolTip(
            "What colourspace the EXRs are in. Shot renders from Nuke are AP0; "
            "CG out of other packages is often ACEScg.")

        def lamp():
            d = QLabel("●")
            d.setFont(QFont("", 13))
            d.setFixedWidth(16)
            d.setAlignment(Qt.AlignCenter)
            t = QLabel("—")
            t.setWordWrap(True)
            t.setStyleSheet(DETAIL_CSS)
            return d, t

        self.cdl_dot, self.cdl_detail = lamp()
        self.lut_dot, self.lut_detail = lamp()
        self.frame_dot, self.frame_detail = lamp()

        g.addWidget(self.log_check, 1, 0, 1, 2)
        g.addWidget(self._key("SOURCE"), 1, 2)
        g.addWidget(self.source_combo, 1, 3)
        g.addWidget(self.cdl_check, 2, 0, 1, 2)
        g.addWidget(self.cdl_dot, 2, 2)
        g.addWidget(self.cdl_detail, 2, 3)
        g.addWidget(self.lut_check, 3, 0, 1, 2)
        g.addWidget(self.lut_dot, 3, 2)
        g.addWidget(self.lut_detail, 3, 3)

        self.framing_combo = QComboBox()
        for label, value in fb.FRAMING_CHOICES:
            self.framing_combo.addItem(label, value)
        self.framing_combo.currentIndexChanged.connect(self._refresh_framing)
        g.addWidget(self._key("FRAMING"), 4, 0)
        g.addWidget(self.framing_combo, 4, 1)
        g.addWidget(self.frame_dot, 4, 2)
        g.addWidget(self.frame_detail, 4, 3)
        hint = QLabel("Untick everything for display-referred EXRs (already "
                      "Rec.709). Untick only Convert for EXRs already in LogC4.")
        hint.setWordWrap(True)
        hint.setStyleSheet(theme.HINT_CSS)
        g.addWidget(hint, 5, 0, 1, 4)
        for c in (self.log_check, self.cdl_check, self.lut_check):
            c.setChecked(True)
            c.toggled.connect(self._update_preview)
        self.log_check.toggled.connect(self.source_combo.setEnabled)
        self.source_combo.currentIndexChanged.connect(self._update_preview)
        outer.addWidget(self.exr_box)

        # -- QT question -------------------------------------------------------
        self.mov_box = QWidget()
        v = QVBoxLayout(self.mov_box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        qhead = QLabel("THIS QT  ·  does it already have its own slate / burn-ins?")
        qhead.setStyleSheet(KEY_CSS)
        v.addWidget(qhead)
        self.rewrap_radio = QRadioButton(
            "No - add our slate + burn-ins (watcher re-wraps it; colour untouched)")
        self.asis_radio = QRadioButton(
            "Yes - deliver as-is (straight into today's package + CSV now)")
        self.mov_group = QButtonGroup(self)
        self.mov_group.setExclusive(True)
        for r in (self.rewrap_radio, self.asis_radio):
            self.mov_group.addButton(r)
            r.toggled.connect(self._update_preview)
            v.addWidget(r)
        self.slate_check = QCheckBox("Include slate")
        self.slate_check.setChecked(True)
        self.slate_check.toggled.connect(self._update_preview)
        self.rewrap_radio.toggled.connect(self.slate_check.setEnabled)
        v.addWidget(self.slate_check)
        mhint = QLabel("A QT is display-referred, so no LogC4 / CDL / LUT is "
                       "applied either way.")
        mhint.setStyleSheet(theme.HINT_CSS)
        v.addWidget(mhint)
        outer.addWidget(self.mov_box)

        outer.addSpacing(12)
        self.preview = QLabel("Connecting to Flow…")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setStyleSheet(
            "QLabel { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: %s; padding: 10px; font-size: 11px; }"
            % (theme.SURFACE_RAISED, theme.INK_MUTED, theme.STRUCTURE_LINE,
               theme.RADIUS_TIGHT))
        outer.addWidget(self.preview, 1)
        outer.addSpacing(14)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton("CANCEL")
        self.send_btn = QPushButton("SEND")
        self.cancel_btn.setStyleSheet(theme.GHOST_BUTTON_CSS)
        self.send_btn.setStyleSheet(theme.PRIMARY_BUTTON_CSS)
        for b in (self.cancel_btn, self.send_btn):
            b.setMinimumHeight(34)
            b.setMinimumWidth(120)
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        self.cancel_btn.clicked.connect(self.reject)
        self.send_btn.clicked.connect(self.on_send)
        self.send_btn.setEnabled(False)
        outer.addLayout(row)

        self.exr_box.hide()
        self.mov_box.hide()
        QTimer.singleShot(0, self._connect)

    def _key(self, text):
        lab = QLabel(text)
        lab.setStyleSheet(KEY_CSS)
        return lab

    # -- Loading -------------------------------------------------------------

    def _connect(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.tk = fb.get_tk()
            self.shots = fb.list_shots(self.tk)
            self.project = fb.project_info(self.tk)
            options = fb.submitted_for_options(self.tk)
        except Exception as exc:
            self._fail("Could not connect to Flow:\n\n%s: %s\n\nConfig root:\n%s"
                       % (type(exc).__name__, exc, fb.CONFIG_ROOT))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.submitted_combo.addItems(options)
        self._apply_filter("")

    def _fail(self, message):
        self.preview.setText(message)
        self.send_btn.setEnabled(False)

    # -- Source --------------------------------------------------------------

    def on_choose(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an EXR frame or a QT",
            os.path.dirname(self.src["path"]) if self.src else "",
            "Renders (*.exr *.mov *.mp4 *.m4v);;All files (*)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.src = er.detect_source(path)
        except er.ExternalError as exc:
            self.src = None
            QMessageBox.warning(self, "Can't use that file", str(exc))
        finally:
            QApplication.restoreOverrideCursor()
        self._on_source_changed()

    def _on_source_changed(self):
        src = self.src
        self.exr_box.setVisible(bool(src) and src["kind"] == "exr")
        self.mov_box.setVisible(bool(src) and src["kind"] == "movie")
        if not src:
            self.src_label.setText("nothing chosen")
            self._update_preview()
            return
        self.src_label.setText(er.describe_source(src))
        self.src_label.setToolTip(src["path"])
        # Keep the source's own numbering unless it starts below 1001.
        # Editorial counts from 1001 and the delivered TC is frame-derived, so
        # a source numbered from 0 or 1 defaults to being renumbered.
        first = src["frame_first"] if src["kind"] == "exr" else 1001
        self.first_edit.setText(str(max(first, 1001)))
        if src["kind"] == "movie":
            # Force the question: no answer is pre-selected.
            self.mov_group.setExclusive(False)
            self.rewrap_radio.setChecked(False)
            self.asis_radio.setChecked(False)
            self.mov_group.setExclusive(True)
        if not self.description_edit.toPlainText().strip():
            self.description_edit.setPlainText(
                "External render: %s" % os.path.basename(src["path"]))
        self._refresh_framing()
        self._update_preview()

    # -- Shot / task cascade (as RE-RUN QT) -----------------------------------

    def _apply_filter(self, text):
        if not self.shots:
            return
        needle = (text or "").strip().lower()
        matches = [s for s in self.shots
                   if not needle or needle in (s.get("code") or "").lower()]
        keep = self.shot_combo.currentData()
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
            self._fail("No shot matches “%s”." % text)
            return
        self._on_shot_changed()

    def _on_shot_changed(self, *_):
        shot = self.shot_combo.currentData()
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
        self.fields = {}
        shot, task = self.shot_combo.currentData(), self.task_combo.currentData()
        if not task:
            self._fail("This shot has no tasks.")
            return
        try:
            fields = fb.resolve_fields(self.tk, shot, task)
        except Exception as exc:
            self._fail("Could not resolve this task: %s" % exc)
            return
        # The path cache on this show can carry pre-renumber Episode/Scene
        # values (see reflag_dialog). The shot code is the authority on disk.
        fields.update(fb.fields_from_shot_code(shot.get("code")))
        missing = [k for k in ("Episode", "Scene", "Shot", "Step")
                   if not fields.get(k)]
        if missing:
            self._fail("Cannot resolve template fields for this task: %s"
                       % ", ".join(missing))
            return
        self.fields = fields
        self.version_spin.blockSignals(True)
        self.version_spin.setValue(er.next_free_version(self.tk, fields))
        self.version_spin.blockSignals(False)
        self._refresh_lamps()
        self._refresh_framing()
        self._update_preview()

    def _on_vendor_changed(self, text):
        up = text.upper()
        if up != text:
            self.vendor_edit.setText(up)
            return
        self._update_preview()

    # -- Lamps (asked of the bake itself, via flag_builder) -------------------

    def _refresh_lamps(self):
        if not self.fields:
            return
        self.colour = fb.color_sources(fb.color_probe_data(self.fields))
        for key, dot, detail in (("cdl", self.cdl_dot, self.cdl_detail),
                                 ("lut", self.lut_dot, self.lut_detail)):
            entry = self.colour[key]
            dot.setStyleSheet("color: %s;" % LAMP[entry["status"]])
            detail.setText("%s   %s" % (LAMP_WORD[entry["status"]],
                                        entry["detail"]))
            detail.setToolTip(entry["path"] or entry["detail"])

    def _refresh_framing(self, *_):
        if not (self.fields and self.src and self.src["kind"] == "exr"):
            return
        # The bake's frame_path() returns a token-less pattern as-is, so the
        # picked frame itself is probed - before anything is copied.
        probe_render = {"exr_path_pattern": self.src["path"],
                        "frame_first": self.src["frame_first"]}
        self.framing = fb.framing_source(fb.color_probe_data(self.fields),
                                         probe_render,
                                         self.framing_combo.currentData())
        status = self.framing["status"]
        self.frame_dot.setStyleSheet("color: %s;" % LAMP[status])
        self.frame_detail.setText(self.framing["detail"])
        self.frame_detail.setToolTip("\n".join(self.framing["notes"])
                                     or self.framing["detail"])
        self._update_preview()

    # -- State ---------------------------------------------------------------

    def _vendor(self):
        return (self.vendor_edit.text().strip() or fb.DEFAULT_VENDOR).upper()

    def _first_frame(self):
        try:
            return int(self.first_edit.text())
        except ValueError:
            return None

    def _color_pipe(self):
        return er.color_pipe_dict(self.log_check.isChecked(),
                                  self.cdl_check.isChecked(),
                                  self.lut_check.isChecked())

    def _source_space(self):
        return self.source_combo.currentData() if self.log_check.isChecked() \
            else None

    def _as_is(self):
        return self.asis_radio.isChecked()

    def _tfields(self):
        return er.target_fields(self.fields, self.version_spin.value(),
                                self._vendor())

    def _update_preview(self, *_):
        if not self.tk:
            return
        lines, ready = [], True
        src = self.src
        if not src:
            lines.append("Choose an EXR frame (its sequence is found for you) "
                         "or a QT.")
            ready = False
        if not self.fields:
            ready = False
        first = self._first_frame()
        if first is None:
            lines.append("First frame must be a whole number.")
            ready = False

        if src and self.fields and first is not None:
            try:
                folder = er.leaf_dir(self.tk, self._tfields())
            except Exception as exc:
                self._fail("Could not resolve the render path: %s" % exc)
                return
            last = first + (src["frame_last"] - src["frame_first"]
                            if src["kind"] == "exr" else src["frame_count"] - 1)
            lines.append("Copy to:  %s" % folder)
            if src["kind"] == "exr" and first != src["frame_first"]:
                lines.append("Frames renumbered %d-%d -> %d-%d"
                             % (src["frame_first"], src["frame_last"], first,
                                last))
            else:
                lines.append("Frames:  %d-%d" % (first, last))
            if src.get("missing"):
                lines.append("Note: %d frames missing in the source range; "
                             "the bake skips them." % len(src["missing"]))
            if os.path.isdir(folder) and os.listdir(folder):
                lines.append("v%03d already exists on disk - pick a higher "
                             "version." % self.version_spin.value())
                ready = False

            if src["kind"] == "exr":
                lines.extend(er.describe_colour(
                    "exr", self._color_pipe(), self._source_space()))
                if self.colour and self.cdl_check.isChecked() and \
                        self.colour["cdl"]["status"] == "none":
                    lines.append("CDL is ticked but this shot has none - "
                                 "the QT bakes ungraded.")
                lines.append("Then:  the watcher bakes it into the package "
                             "below and writes the CSV row.")
                lines.append("Package:  %s" % er.current_package_name())
            else:
                if not (self.rewrap_radio.isChecked() or self._as_is()):
                    lines.append("Answer the QT question above.")
                    ready = False
                elif self._as_is():
                    lines.extend(er.describe_colour("movie", as_is=True))
                    p = src["probe"]
                    if (p.get("width"), p.get("height")) != er.DELIVERY_SIZE:
                        lines.append("Note: %sx%s, not 1920x1080 - delivered "
                                     "at its own size." % (p.get("width"),
                                                          p.get("height")))
                    if (p.get("codec") or "").lower() != "prores":
                        lines.append("Note: %s source - transcoded to ProRes "
                                     "422 HQ for the package." % p.get("codec"))
                    lines.append("Then:  filed into the package below (prores / "
                                 "h264 / dnx) and the CSV row written now. "
                                 "Its own timecode is kept.")
                    lines.append("Package:  %s" % er.current_package_name())
                else:
                    lines.extend(er.describe_colour("movie"))
                    lines.append("Then:  the watcher re-wraps it into the "
                                 "package below and writes the CSV row.")
                    lines.append("Package:  %s" % er.current_package_name())

        self.preview.setText("\n\n".join(lines))
        self.send_btn.setEnabled(ready and self.worker is None)

    # -- Send ----------------------------------------------------------------

    def _snapshot(self):
        """Every widget value the send needs, read ON the UI thread."""
        is_movie = self.src["kind"] == "movie"
        return {
            "shot": self.shot_combo.currentData(),
            "task": self.task_combo.currentData(),
            "artist": self.artist_edit.text().strip() or fb.DEFAULT_ARTIST,
            "version": self.version_spin.value(),
            "vendor": self._vendor(),
            "submitted_for": self.submitted_combo.currentText(),
            "description": self.description_edit.toPlainText().strip()
            or "External render.",
            "color_pipe": None if is_movie else self._color_pipe(),
            "source_space": None if is_movie else self._source_space(),
            "include_slate": self.slate_check.isChecked() if is_movie else True,
            "anamorphic": None if is_movie else self.framing_combo.currentData(),
        }

    def _flag_data(self, ui, exr_pattern=None, movie_path=None,
                   frame_first=None, frame_last=None):
        """Safe off the UI thread: reads only `ui`, the dialog's fixed state."""
        is_movie = self.src["kind"] == "movie"
        cut_in, cut_out = fb.cut_range(self.tk, ui["shot"]["id"])
        return er.build_flag(
            shot=ui["shot"], task=ui["task"], fields=self.fields,
            project=self.project, version=ui["version"],
            vendor_code=ui["vendor"], frame_first=frame_first,
            frame_last=frame_last, artist=ui["artist"],
            user_id=fb.human_user_id(self.tk, ui["artist"])
            or fb.current_user_id(self.tk),
            submitted_for=ui["submitted_for"], description=ui["description"],
            cut_in=cut_in, cut_out=cut_out, kind=self.src["kind"],
            exr_pattern=exr_pattern, movie_path=movie_path,
            color_pipe=ui["color_pipe"], source_space=ui["source_space"],
            include_slate=ui["include_slate"], anamorphic=ui["anamorphic"],
            start_timecode=er.frame_timecode(frame_first) if is_movie else None,
        )

    def on_send(self):
        src = self.src
        if not (src and self.fields):
            return
        tfields = self._tfields()
        first = self._first_frame()
        ui = self._snapshot()
        is_movie = src["kind"] == "movie"
        as_is = is_movie and self._as_is()
        summary = (er.describe_colour("movie", as_is=as_is) if is_movie else
                   er.describe_colour("exr", self._color_pipe(),
                                      self._source_space()))
        answer = QMessageBox.question(
            self, "Send external render",
            "%s v%03d  (%s)\n\n%s\n\nSend it?"
            % (ui["shot"].get("code"), ui["version"], ui["vendor"],
               "\n".join(summary)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return

        choices = {"as_is": as_is,
                   "color_pipe": None if is_movie else self._color_pipe(),
                   "source_colorspace": None if is_movie else self._source_space(),
                   "first_frame": first}

        def job(progress):
            folder = er.leaf_dir(self.tk, tfields)
            if is_movie:
                progress("Copying the QT into the shot…")
                copy = er.copy_movie(src, folder, er.leaf_name(self.tk, tfields))
                last = first + src["frame_count"] - 1
                data = self._flag_data(ui, movie_path=copy, frame_first=first,
                                       frame_last=last)
            else:
                pattern = er.render_pattern(self.tk, tfields, first)
                f0, f1 = er.copy_exrs(src, pattern, first, progress)
                data = self._flag_data(ui, exr_pattern=pattern, frame_first=f0,
                                       frame_last=f1)
            er.write_sidecar(folder, src, choices)
            problems = er.validate(data)
            if problems:
                raise er.ExternalError("Flag is incomplete:\n" + "\n".join(
                    "• %s" % p for p in problems))
            if as_is:
                result = er.deliver_as_is(data, copy, src["probe"],
                                          self.tk.shotgun, progress=progress)
                return ("as_is", result)
            flag_path = fb.flag_path_for(self.tk, tfields,
                                         {"version": tfields["version"],
                                          "vendor_code": tfields["vendor_code"]})
            ok, msg = fb.write_flag(flag_path, data)
            if not ok:
                raise er.ExternalError(msg)
            return ("flag", flag_path)

        self.send_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.worker = _Worker(job, self)
        self.worker.progress.connect(self.preview.setText)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_failed(self, message):
        self.worker = None
        self.cancel_btn.setEnabled(True)
        QMessageBox.warning(self, "External render failed", message)
        self._update_preview()

    def _on_done(self, result):
        self.worker = None
        self.cancel_btn.setEnabled(True)
        kind, info = result
        if kind == "flag":
            QMessageBox.information(
                self, "Queued",
                "Copied and flagged. The watcher picks it up within 30 seconds, "
                "bakes it into today's package and writes the CSV row - watch "
                "the log.\n\n%s" % info)
        else:
            d = info["delivery"]
            problems = ("\n\nProblems:\n" + "\n".join("• %s" % p for p in
                                                       d.problems)) if d.problems else ""
            QMessageBox.information(
                self, "Delivered as-is",
                "%s\n\nCSV: %s\nVendor Code: %s%s"
                % (info["prores"], info["csv"], info["artist_note"], problems))
        self.accept()
