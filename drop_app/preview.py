"""
preview.py — color pipeline preview for the Review Drop app.

Opens a window that renders one frame of the dropped media through the same
oiiotool stages the Mac Studio bake uses (color_pipeline.py), with a checkbox
per stage so an artist can see what the QT will look like — and omit any part
of the pipe — before submitting.

The stage map the dialog returns is written into the render-complete flag as
"color_stages", so the watcher bakes exactly what was approved here.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = APP_DIR.parent / "scripts"
if SCRIPTS_DIR.is_dir() and str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import color_pipeline
from color_pipeline import (
    PIPELINE_STAGES,
    SHOW_LUT_PATH,
    build_frame_command,
    default_stages,
    describe_stages,
    normalize_stages,
)

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
)

# Media the preview can render. Movies are re-wrapped by the bake without any
# color processing, so there is no pipe to preview for them.
PREVIEWABLE_TYPES = {
    "exr_single", "exr_sequence", "image_single", "image_sequence",
}

VIEW_MIN_WIDTH = 720
VIEW_MIN_HEIGHT = 405

STAGE_BY_KEY = {stage.key: stage for stage in PIPELINE_STAGES}

# Stand-in for the output path while the command is being hashed for the cache.
_OUTPUT_PLACEHOLDER = "@preview_out@"


def can_preview(media) -> bool:
    return bool(media) and media.get("media_type") in PREVIEWABLE_TYPES


class _RenderTask(QThread):
    """Run one oiiotool invocation off the UI thread."""

    done = Signal(str, str)  # (rendered path, error message)

    def __init__(self, cmd, dst_path, parent=None):
        super().__init__(parent)
        self.cmd = list(cmd)
        self.dst_path = dst_path

    def run(self):
        try:
            result = subprocess.run(self.cmd, capture_output=True, text=True)
        except Exception as exc:
            self.done.emit("", str(exc))
            return
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            self.done.emit("", message or "oiiotool exited with %d" % result.returncode)
            return
        self.done.emit(self.dst_path, "")


class ColorPreviewDialog(QDialog):
    """
    Frame viewer + per-stage switches.

    media        : the dict from staging.classify_paths()
    cdl_path     : per-shot .cc for the selected context, or None
    stages       : stage map to open with (falls back to the source defaults)
    context_note : short description of the delivery shown in the header
    """

    def __init__(self, media, cdl_path=None, stages=None, context_note="",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Color Pipeline Preview")
        self.setMinimumSize(880, 720)
        self.setStyleSheet("QWidget { background: #1e1e1e; color: #ddd; }")

        self.media = media
        self.cdl_path = cdl_path
        self.stages = normalize_stages(stages, media.get("skip_color"))

        self._oiiotool = color_pipeline.find_oiiotool()
        self._tmpdir = tempfile.mkdtemp(prefix="drop_preview_")
        self._task = None
        self._queued = None
        self._current_pixmap = None

        self._frames = self._frame_numbers()
        self._src_w, self._src_h, self._par = self._probe_source()
        self._desqueeze_to = color_pipeline.desqueeze_size(
            self._src_w, self._src_h, self._par
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        header = QLabel(context_note or "Preview")
        header.setWordWrap(True)
        header.setStyleSheet("color: #9ecbff; font-size: 11px;")
        layout.addWidget(header)

        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumSize(VIEW_MIN_WIDTH, VIEW_MIN_HEIGHT)
        # Without this the pixmap's size hint becomes the layout's floor and
        # the window can no longer be made smaller once a frame is shown.
        self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.view.setStyleSheet(
            "QLabel { background: #000; border: 1px solid #333; color: #777; }"
        )
        self.view.setText("Rendering…")
        layout.addWidget(self.view, 1)

        layout.addLayout(self._build_frame_row())
        layout.addWidget(self._build_stage_box())

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: #8a8a8a; font-size: 10px;")
        layout.addWidget(self.summary)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #c98a8a; font-size: 10px;")
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.btn_reset = QPushButton("Reset to defaults")
        self.btn_reset.clicked.connect(self._reset_stages)
        buttons.addButton(self.btn_reset, QDialogButtonBox.ResetRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        apply_btn = buttons.addButton("Use these settings", QDialogButtonBox.AcceptRole)
        apply_btn.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_stage_widgets()
        if self._oiiotool:
            self._request_render()
        else:
            self.view.setText(
                "oiiotool not found on this machine.\n\n"
                "The frame can't be rendered here, but the stage switches below\n"
                "still apply to the bake on the Mac Studio.\n\n"
                "Install it with:  brew install openimageio"
            )

    # ── Construction helpers ────────────────────────────────────────────────

    def _build_frame_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.frame_label = QLabel("")
        self.frame_label.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        self.slider = QSlider(Qt.Horizontal)
        # The window's flat dark palette makes a stock groove invisible.
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #3a3a3a; "
            "border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 12px; margin: -5px 0; "
            "background: #9ecbff; border-radius: 3px; }"
        )
        self.slider.setMinimum(0)
        self.slider.setMaximum(max(0, len(self._frames) - 1))
        self.slider.setValue(0)
        self.slider.setTracking(False)  # render on release, not every pixel
        self.slider.valueChanged.connect(self._on_frame_changed)
        single = len(self._frames) <= 1
        self.slider.setVisible(not single)
        row.addWidget(QLabel("Frame"))
        row.addWidget(self.slider, 1)
        row.addWidget(self.frame_label)
        self._update_frame_label()
        return row

    def _build_stage_box(self) -> QGroupBox:
        box = QGroupBox("Pipeline stages")
        outer = QHBoxLayout(box)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(18)

        checks = QVBoxLayout()
        checks.setSpacing(4)
        self.stage_checks = {}
        for stage in PIPELINE_STAGES:
            check = QCheckBox(stage.label)
            check.setToolTip(stage.description)
            check.setChecked(bool(self.stages.get(stage.key)))
            check.toggled.connect(self._on_stage_toggled)
            checks.addWidget(check)
            self.stage_checks[stage.key] = check

        self.chk_bypass = QCheckBox("Bypass everything (show source)")
        self.chk_bypass.setToolTip(
            "Temporary A/B only — this is not saved with the submission."
        )
        self.chk_bypass.toggled.connect(lambda _: self._request_render())
        checks.addSpacing(6)
        checks.addWidget(self.chk_bypass)
        checks.addStretch()
        outer.addLayout(checks, 1)

        info = QLabel(self._source_notes())
        info.setWordWrap(True)
        info.setAlignment(Qt.AlignTop)
        info.setStyleSheet("color: #8a8a8a; font-size: 10px;")
        outer.addWidget(info, 1)
        return box

    def _source_notes(self) -> str:
        if self._src_w and self._src_h:
            res = "%d x %d" % (self._src_w, self._src_h)
        else:
            res = "unknown"
        if self._desqueeze_to:
            squeeze = "PAR %.4f \u2192 de-squeeze to %d x %d" % (
                self._par, self._desqueeze_to[0], self._desqueeze_to[1]
            )
        else:
            squeeze = "PAR %.4f (square pixels)" % (self._par or 1.0)

        if not self.cdl_path:
            cdl = "CDL: not applicable for this delivery"
        elif os.path.exists(self.cdl_path):
            cdl = "CDL: %s" % os.path.basename(self.cdl_path)
        else:
            cdl = "CDL: none at %s" % self.cdl_path

        lut = (
            "Show LUT: %s" % os.path.basename(SHOW_LUT_PATH)
            if os.path.exists(SHOW_LUT_PATH)
            else "Show LUT: NOT FOUND — the display transform is used instead"
        )
        return "Source: %s\n%s\n%s\n%s" % (res, squeeze, cdl, lut)

    # ── Source inspection ───────────────────────────────────────────────────

    def _frame_numbers(self):
        """Frame numbers available for scrubbing, in order."""
        media_type = self.media.get("media_type")
        if media_type in ("exr_sequence", "image_sequence"):
            first = int(self.media.get("frame_first", 1))
            last = int(self.media.get("frame_last", first))
            if last >= first:
                return list(range(first, last + 1))
        return [int(self.media.get("frame_first", 1))]

    def _source_path(self, index=0) -> str:
        """
        Path of the frame at slider position `index`.

        Sequences are addressed through the detected %04d pattern; the sorted
        file list is the fallback so an irregular numbering still previews.
        """
        pattern = self.media.get("exr_path_pattern")
        files = sorted(self.media.get("files", []), key=lambda p: p.name)
        if pattern and len(self._frames) > 1:
            candidate = color_pipeline.frame_path(pattern, self._frames[index])
            if candidate and os.path.exists(candidate):
                return candidate
        if not files:
            return ""
        return str(files[min(index, len(files) - 1)])

    def _probe_source(self):
        if not self._oiiotool:
            return (None, None, 1.0)
        src = self._source_path(0)
        if not src or not os.path.exists(src):
            return (None, None, 1.0)
        width, height = color_pipeline.read_resolution(src, self._oiiotool)
        par = color_pipeline.read_pixel_aspect(src, self._oiiotool)
        return (width, height, par)

    # ── Stage state ─────────────────────────────────────────────────────────

    def _on_stage_toggled(self, _checked):
        for key, check in self.stage_checks.items():
            self.stages[key] = check.isChecked()
        self._sync_stage_widgets()
        self._request_render()

    def _reset_stages(self):
        self.stages = default_stages(self.media.get("skip_color"))
        for key, check in self.stage_checks.items():
            check.blockSignals(True)
            check.setChecked(bool(self.stages.get(key)))
            check.blockSignals(False)
        self._sync_stage_widgets()
        self._request_render()

    def _sync_stage_widgets(self):
        """Grey out stages that can have no effect, and refresh the summary."""
        lut_active = bool(
            self.stages.get("show_lut") and os.path.exists(SHOW_LUT_PATH)
        )
        cdl_usable = bool(self.cdl_path) and os.path.exists(self.cdl_path or "")

        self._set_stage_state(
            "display_transform",
            not lut_active,
            "overridden by the show LUT",
        )
        self._set_stage_state(
            "cdl",
            cdl_usable,
            "no .cc for this shot" if self.cdl_path else "not used for assets",
        )
        self._set_stage_state(
            "desqueeze",
            self._desqueeze_to is not None,
            "square pixels",
        )

        self.summary.setText("Bake will run \u2014 %s" % describe_stages(self.stages))

    def _set_stage_state(self, key, active, inert_reason):
        """
        Disable a switch that can't change anything and say so in its label —
        a greyed checkbox alone doesn't explain why it's greyed.
        """
        check = self.stage_checks.get(key)
        if check is None:
            return
        stage = STAGE_BY_KEY[key]
        check.setEnabled(active)
        check.setText(stage.label if active else "%s  (%s)" % (stage.label, inert_reason))
        check.setToolTip(stage.description if active else inert_reason)

    def selected_stages(self) -> dict:
        """The stage map to store on the submission (bypass is view-only)."""
        return dict(self.stages)

    # ── Rendering ───────────────────────────────────────────────────────────

    def _on_frame_changed(self, _value):
        self._update_frame_label()
        self._request_render()

    def _update_frame_label(self):
        index = self.slider.value() if hasattr(self, "slider") else 0
        frame = self._frames[min(index, len(self._frames) - 1)]
        if len(self._frames) > 1:
            self.frame_label.setText(
                "%d  (%d\u2013%d)" % (frame, self._frames[0], self._frames[-1])
            )
        else:
            self.frame_label.setText(str(frame))

    def _effective_stages(self) -> dict:
        if self.chk_bypass.isChecked():
            return {stage.key: False for stage in PIPELINE_STAGES}
        return self.stages

    def _request_render(self):
        if not self._oiiotool:
            return
        index = self.slider.value() if hasattr(self, "slider") else 0
        src = self._source_path(index)
        if not src or not os.path.exists(src):
            self.status.setText("Frame not found on disk: %s" % src)
            return

        stages = self._effective_stages()
        cmd = build_frame_command(
            src,
            _OUTPUT_PLACEHOLDER,
            stages,
            cdl_path=self.cdl_path,
            desqueeze_to=self._desqueeze_to,
            fit_to=(color_pipeline.DELIVERY_WIDTH, color_pipeline.DELIVERY_HEIGHT),
            oiiotool=self._oiiotool,
            force_uint8=True,
        )
        # The command is the cache key, so flipping a stage back and forth
        # re-shows an already rendered frame instead of re-running oiiotool.
        key = hashlib.md5(("\x00".join(cmd)).encode("utf-8")).hexdigest()
        dst = os.path.join(self._tmpdir, "%s.png" % key)
        cmd[cmd.index(_OUTPUT_PLACEHOLDER)] = dst

        if os.path.exists(dst):
            self._show_image(dst)
            return

        if self._task is not None and self._task.isRunning():
            self._queued = (cmd, dst)
            return
        self._start_task(cmd, dst)

    def _start_task(self, cmd, dst):
        self.status.setText("")
        self.view.setText("Rendering…")
        self._task = _RenderTask(cmd, dst, self)
        self._task.done.connect(self._on_render_done)
        self._task.start()

    def _on_render_done(self, path, error):
        if error:
            self.view.setText("Preview failed.")
            self.status.setText(error.splitlines()[-1] if error else "")
        elif path:
            self._show_image(path)

        queued = self._queued
        self._queued = None
        if queued:
            self._start_task(*queued)

    def _show_image(self, path):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.view.setText("Could not load the rendered frame.")
            return
        self._current_pixmap = pixmap
        self._rescale_view()

    def _rescale_view(self):
        if self._current_pixmap is None:
            return
        self.view.setPixmap(
            self._current_pixmap.scaled(
                self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    # ── Qt plumbing ─────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_view()

    def _teardown(self):
        """Let any in-flight oiiotool finish before its temp dir disappears."""
        self._queued = None
        if self._task is not None and self._task.isRunning():
            self._task.wait(5000)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def closeEvent(self, event):
        self._teardown()
        super().closeEvent(event)

    def done(self, result):
        self._teardown()
        super().done(result)
