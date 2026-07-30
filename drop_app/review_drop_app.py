#!/usr/bin/env python3
"""
Review Drop — standalone Mac app for sending external EXR / MOV to the
QT Watcher pipeline, plus a shortcut into the 3D asset ingest drop folder.

Requires Shotgun/Flow Desktop's bundled Python (PySide6 + sgtk via PYTHONPATH).
Launch with launch_review_drop.command.
"""
from __future__ import annotations

import os
import sys
import subprocess
import getpass
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap paths before importing sgtk / staging
# ---------------------------------------------------------------------------

CONFIG_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/"
    "repo/pipeline/config/flow/current"
)
INGEST_FOLDER = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/"
    "_staging/assets_incoming"
)
PROJECT_ID = 1343

# Branding assets. The inverted teardrop is the variant cut for dark
# backgrounds, which is what this window's chrome is.
LOGO_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/"
    "shots/_globals/logo/teardrop_blk1.png"
)
BRAND_FONT_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/_globals/"
    "fonts/SEV_LUMON LOGO_FONT/Manifold Extended CF v4.0 OTF/"
    "ManifoldExtendedCF-Medium.otf"
)

SHOT_STEPS = ["temp", "comp", "light", "fx", "anim", "editorial", "deliverable"]
ASSET_STEPS = ["turntable", "model", "lookdev", "rig", "fx"]
DEFAULT_SUBMITTED = ["Internal Review", "Supervisor", "Editorial", "Client"]

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

os.environ.setdefault(
    "PYTHONPATH",
    os.path.join(CONFIG_PATH, "install", "core", "python"),
)
_core_py = os.path.join(CONFIG_PATH, "install", "core", "python")
if _core_py not in sys.path:
    sys.path.insert(0, _core_py)

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import (
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QCheckBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QButtonGroup,
)

import sgtk
import preview
import staging


def get_sgtk():
    tk = sgtk.sgtk_from_path(CONFIG_PATH)
    return tk, tk.shotgun


def load_brand_font(default_size: int = 22) -> QFont:
    """Load Manifold Extended Medium; fall back to a bold system font."""
    family = None
    if os.path.exists(BRAND_FONT_PATH):
        fid = QFontDatabase.addApplicationFont(BRAND_FONT_PATH)
        fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
        if fams:
            family = fams[0]
    font = QFont(family) if family else QFont()
    font.setPixelSize(default_size)
    if not family:
        font.setBold(True)
    font.setLetterSpacing(QFont.PercentageSpacing, 108)
    return font


class DropZone(QLabel):
    IDLE = ("Drop media here\n"
            "Images · EXR · MOV   |   3D: OBJ / FBX / GLB / PLY")

    def __init__(self, on_drop, on_double_click=None, parent=None):
        super().__init__(parent)
        self._on_drop = on_drop
        self._on_double_click = on_double_click
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(84)
        self.setWordWrap(True)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #666; border-radius: 8px; "
            "background: #2a2a2a; color: #bbb; padding: 8px; font-size: 12px; }"
        )
        self.setText(self.IDLE)
        self.setToolTip("Double-click to preview the loaded media.")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            self._on_drop(paths)

    def mouseDoubleClickEvent(self, event):
        if self._on_double_click:
            self._on_double_click()


class ReviewDropWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Buffalo Review Drop")
        self.setFixedSize(QSize(640, 640))

        self.tk = None
        self.sg = None
        self.media = None
        self._episodes = []
        self._sequences = []
        self._shots = []
        self._assets = []
        self._users = []
        self._record_pref = False
        self._preview_window = None

        root = QWidget()
        root.setStyleSheet("QWidget { background: #1e1e1e; color: #ddd; }")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addLayout(self._build_header())

        self.drop = DropZone(self.on_paths_dropped, self.open_preview)
        layout.addWidget(self.drop)

        self.media_info = QLabel("No media loaded.")
        self.media_info.setWordWrap(True)
        self.media_info.setStyleSheet("color: #9ecbff; font-size: 11px;")
        layout.addWidget(self.media_info)

        # Entity type
        type_row = QHBoxLayout()
        type_row.setSpacing(12)
        self.radio_shot = QRadioButton("Shot")
        self.radio_asset = QRadioButton("Asset")
        self.radio_shot.setChecked(True)
        self.type_group = QButtonGroup(self)
        self.type_group.addButton(self.radio_shot)
        self.type_group.addButton(self.radio_asset)
        type_row.addWidget(self.radio_shot)
        type_row.addWidget(self.radio_asset)
        type_row.addStretch()
        layout.addLayout(type_row)
        self.radio_shot.toggled.connect(self._on_type_changed)

        # ── Two-column body: context (left) + delivery fields (right) ──────────
        body = QHBoxLayout()
        body.setSpacing(12)

        # Left column: Shot context stacked over Asset context (one visible)
        left_col = QVBoxLayout()
        left_col.setSpacing(6)

        self.shot_box = QGroupBox("Shot context")
        shot_form = QFormLayout(self.shot_box)
        shot_form.setContentsMargins(8, 8, 8, 8)
        shot_form.setSpacing(6)
        self.cmb_episode = QComboBox()
        self.cmb_sequence = QComboBox()
        self.cmb_shot = QComboBox()
        self.cmb_episode.currentIndexChanged.connect(self._load_sequences)
        self.cmb_sequence.currentIndexChanged.connect(self._load_shots)
        shot_form.addRow("Episode", self.cmb_episode)
        shot_form.addRow("Sequence", self.cmb_sequence)
        shot_form.addRow("Shot", self.cmb_shot)
        left_col.addWidget(self.shot_box)

        self.asset_box = QGroupBox("Asset context")
        asset_form = QFormLayout(self.asset_box)
        asset_form.setContentsMargins(8, 8, 8, 8)
        asset_form.setSpacing(6)
        self.cmb_asset_type = QComboBox()
        self.cmb_asset = QComboBox()
        self.cmb_asset_type.currentIndexChanged.connect(self._load_assets)
        for t in ["Character", "Prop", "Environment", "Vehicle", "FX"]:
            self.cmb_asset_type.addItem(t)
        asset_form.addRow("Type", self.cmb_asset_type)
        asset_form.addRow("Asset", self.cmb_asset)
        left_col.addWidget(self.asset_box)
        self.asset_box.hide()
        left_col.addStretch()
        body.addLayout(left_col, 1)

        # Right column: delivery / version fields
        self.fields_box = QGroupBox("Delivery")
        form = QFormLayout(self.fields_box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)
        self.cmb_step = QComboBox()
        self.cmb_step.setEditable(True)
        for s in SHOT_STEPS:
            self.cmb_step.addItem(s)
        self.cmb_delivery_type = QComboBox()
        self.cmb_delivery_type.addItems(["Version", "Reference"])
        self.cmb_delivery_type.currentIndexChanged.connect(self._update_modes)
        self.txt_name_override = QLineEdit()
        self.txt_name_override.setPlaceholderText("Optional reference filename")
        self.txt_name_override.setEnabled(False)
        self.txt_version = QLineEdit("1")
        self.cmb_submitted_by = QComboBox()
        self.cmb_submitted = QComboBox()
        self.cmb_submitted.setEditable(True)
        for s in DEFAULT_SUBMITTED:
            self.cmb_submitted.addItem(s)
        self.txt_description = QLineEdit()
        self.chk_slate = QCheckBox("Include slate")
        self.chk_slate.setChecked(True)
        form.addRow("Delivery type", self.cmb_delivery_type)
        form.addRow("Naming override", self.txt_name_override)
        form.addRow("Step", self.cmb_step)
        form.addRow("Version", self.txt_version)
        form.addRow("Submitted by", self.cmb_submitted_by)
        form.addRow("Submitted for", self.cmb_submitted)
        form.addRow("Notes", self.txt_description)
        form.addRow("", self.chk_slate)
        body.addWidget(self.fields_box, 1)

        layout.addLayout(body)

        # Flow record option — applies to deliveries the QT Watcher never sees.
        record_row = QHBoxLayout()
        record_row.setSpacing(8)
        self.chk_flow_record = QCheckBox("Create Flow record")
        self.chk_flow_record.setToolTip(
            "Create a Version in Flow Production Tracking linked to the "
            "selected Shot or Asset, with the media uploaded for review."
        )
        self.chk_flow_record.toggled.connect(self._on_record_toggled)
        self.lbl_record_hint = QLabel("")
        self.lbl_record_hint.setWordWrap(True)
        self.lbl_record_hint.setStyleSheet("color: #7a7a7a; font-size: 10px;")
        record_row.addWidget(self.chk_flow_record)
        record_row.addWidget(self.lbl_record_hint, 1)
        layout.addLayout(record_row)

        self.status = QTextEdit()
        self.status.setReadOnly(True)
        self.status.setFixedHeight(84)
        self.status.setStyleSheet(
            "QTextEdit { background: #141414; color: #9a9a9a; "
            "font-size: 10px; border: 1px solid #333; }"
        )
        layout.addWidget(self.status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_send = QPushButton("Send to QT Watcher")
        self.btn_send.clicked.connect(self.send_to_watcher)
        self.btn_send.setEnabled(False)
        self.btn_send.setMinimumHeight(34)
        self.btn_preview = QPushButton("Preview…")
        self.btn_preview.clicked.connect(self.open_preview)
        self.btn_preview.setEnabled(False)
        self.btn_preview.setMinimumHeight(34)
        self.btn_preview.setToolTip(
            "Check the loaded media for visibility and color before sending."
        )
        self.btn_ingest = QPushButton("Open 3D Ingest Folder")
        self.btn_ingest.clicked.connect(self.open_ingest)
        self.btn_ingest.setMinimumHeight(34)
        btn_row.addWidget(self.btn_send, 3)
        btn_row.addWidget(self.btn_preview, 1)
        btn_row.addWidget(self.btn_ingest, 2)
        layout.addLayout(btn_row)

        self._update_modes()

        self._log("Bootstrapping Flow…")
        try:
            self.tk, self.sg = get_sgtk()
            self._log("Connected to Flow.")
            self._load_submitted_for()
            self._load_users()
            self._load_episodes()
            self._load_assets()
        except Exception as exc:
            self._log("ERROR: could not connect to Flow: %s" % exc)
            QMessageBox.critical(
                self,
                "Flow connection failed",
                "Could not bootstrap Toolkit from:\n%s\n\n%s" % (CONFIG_PATH, exc),
            )

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)

        logo = QLabel()
        if os.path.exists(LOGO_PATH):
            pix = QPixmap(LOGO_PATH)
            if not pix.isNull():
                logo.setPixmap(
                    pix.scaledToHeight(
                        46, Qt.SmoothTransformation
                    )
                )
        logo.setFixedWidth(52)
        logo.setAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
        header.addWidget(logo)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        brand = QLabel("BUFFALO VFX")
        brand.setFont(load_brand_font(24))
        brand.setStyleSheet("color: #ffffff;")
        subtitle = QLabel("Review Drop")
        subtitle.setStyleSheet("color: #7a7a7a; font-size: 11px;")
        text_col.addWidget(brand)
        text_col.addWidget(subtitle)
        header.addLayout(text_col)
        header.addStretch()
        return header

    def _log(self, msg: str):
        self.status.append(msg)

    def _on_type_changed(self):
        is_shot = self.radio_shot.isChecked()
        self.shot_box.setVisible(is_shot)
        self.asset_box.setVisible(not is_shot)
        self.cmb_step.clear()
        for s in (SHOT_STEPS if is_shot else ASSET_STEPS):
            self.cmb_step.addItem(s)
        self._update_modes()

    def _is_reference_mode(self):
        return self.cmb_delivery_type.currentText() == "Reference"

    def _is_model_mode(self):
        return bool(self.media) and self.media.get("media_type") == "model_3d"

    def _on_record_toggled(self, checked: bool):
        if self.chk_flow_record.isEnabled():
            self._record_pref = checked
        self._update_modes()

    def _update_modes(self, *_args):
        """Sync field states, button text, and the Flow record hint."""
        is_model = self._is_model_mode()
        is_reference = self._is_reference_mode() and not is_model
        is_shot = self.radio_shot.isChecked()

        # A Version delivery always ends up in Flow — the QT Watcher creates
        # that record after the bake — so the option is only ours to make for
        # references and 3D ingest drops.
        record_optional = is_reference or is_model
        wants_record = self._record_pref if record_optional else True
        self.chk_flow_record.blockSignals(True)
        self.chk_flow_record.setChecked(wants_record)
        self.chk_flow_record.setEnabled(record_optional)
        self.chk_flow_record.blockSignals(False)

        self.cmb_delivery_type.setEnabled(not is_model)
        self.fields_box.setVisible(not is_model or wants_record)
        self.txt_name_override.setEnabled(is_reference)
        for widget in (self.cmb_step, self.txt_version, self.chk_slate):
            widget.setEnabled(not record_optional)
        # These fields populate the Flow record, so they stay live whenever
        # one is being created.
        for widget in (
            self.cmb_submitted_by,
            self.cmb_submitted,
            self.txt_description,
        ):
            widget.setEnabled(not record_optional or wants_record)

        if is_model:
            self.btn_send.setText("Send to 3D Ingest")
        elif is_reference:
            self.btn_send.setText(
                "Copy Reference to %s" % ("Shot" if is_shot else "Asset")
            )
        else:
            self.btn_send.setText("Send to QT Watcher")
        self.lbl_record_hint.setText(self._record_hint(is_model, is_reference))

    def _record_hint(self, is_model: bool, is_reference: bool) -> str:
        wants_record = self.chk_flow_record.isChecked()
        entity = "Shot" if self.radio_shot.isChecked() else "Asset"
        if is_model:
            if wants_record:
                return (
                    "Version created on the selected Asset, linked to the "
                    "ingest copy."
                )
            return "Files land in the ingest folder only — nothing in Flow yet."
        if is_reference:
            if wants_record:
                return (
                    "Version created on the selected %s with the reference "
                    "uploaded for review." % entity
                )
            return "Files land in the reference folder only — nothing in Flow."
        return "QT Watcher creates the Flow Version after the bake."

    def _load_submitted_for(self):
        if not self.sg:
            return
        try:
            schema = self.sg.schema_field_read("Version", "sg_submitted_for")
            props = schema.get("sg_submitted_for", {}).get("properties", {})
            valid = props.get("valid_values", {}).get("value") or []
            if valid:
                self.cmb_submitted.clear()
                for v in valid:
                    self.cmb_submitted.addItem(v)
        except Exception:
            pass

    def _load_users(self):
        """Populate Submitted by from active Flow users on this project."""
        if not self.sg:
            return
        self.cmb_submitted_by.clear()
        self.cmb_submitted_by.addItem("— select —", None)
        try:
            self._users = self.sg.find(
                "HumanUser",
                [
                    ["projects", "is", {"type": "Project", "id": PROJECT_ID}],
                    ["sg_status_list", "is_not", "dis"],
                ],
                ["name", "login", "firstname", "lastname"],
                order=[{"field_name": "name", "direction": "asc"}],
            )
        except Exception as exc:
            self._log("WARNING: could not load Flow users: %s" % exc)
            self._users = []
            return

        local_login = getpass.getuser().lower()
        default_idx = 0
        for i, user in enumerate(self._users, start=1):
            label = user.get("name") or user.get("login") or "User %s" % user["id"]
            login = (user.get("login") or "").strip()
            if login:
                label = "%s (%s)" % (label, login)
            self.cmb_submitted_by.addItem(label, user)
            if login and login.lower() == local_login:
                default_idx = i
        if default_idx:
            self.cmb_submitted_by.setCurrentIndex(default_idx)
        self._log("Loaded %d Flow user(s)." % len(self._users))

    def _load_episodes(self):
        if not self.sg:
            return
        self.cmb_episode.blockSignals(True)
        self.cmb_episode.clear()
        self._episodes = self.sg.find(
            "Episode",
            [["project", "is", {"type": "Project", "id": PROJECT_ID}]],
            ["code"],
            order=[{"field_name": "code", "direction": "asc"}],
        )
        self.cmb_episode.addItem("— select —", None)
        for ep in self._episodes:
            self.cmb_episode.addItem(ep["code"], ep)
        self.cmb_episode.blockSignals(False)

    def _load_sequences(self):
        if not self.sg:
            return
        self.cmb_sequence.blockSignals(True)
        self.cmb_sequence.clear()
        self.cmb_shot.clear()
        ep = self.cmb_episode.currentData()
        if not ep:
            self.cmb_sequence.blockSignals(False)
            return
        self._sequences = self.sg.find(
            "Sequence",
            [
                ["project", "is", {"type": "Project", "id": PROJECT_ID}],
                ["episode", "is", ep],
            ],
            ["code", "episode"],
            order=[{"field_name": "code", "direction": "asc"}],
        )
        self.cmb_sequence.addItem("— select —", None)
        for seq in self._sequences:
            self.cmb_sequence.addItem(seq["code"], seq)
        self.cmb_sequence.blockSignals(False)

    def _load_shots(self):
        if not self.sg:
            return
        self.cmb_shot.clear()
        seq = self.cmb_sequence.currentData()
        if not seq:
            return
        self._shots = self.sg.find(
            "Shot",
            [
                ["project", "is", {"type": "Project", "id": PROJECT_ID}],
                ["sg_sequence", "is", seq],
            ],
            ["code", "sg_sequence"],
            order=[{"field_name": "code", "direction": "asc"}],
        )
        self.cmb_shot.addItem("— select —", None)
        for sh in self._shots:
            self.cmb_shot.addItem(sh["code"], sh)

    def _load_assets(self):
        if not self.sg:
            return
        self.cmb_asset.clear()
        asset_type = self.cmb_asset_type.currentText()
        self._assets = self.sg.find(
            "Asset",
            [
                ["project", "is", {"type": "Project", "id": PROJECT_ID}],
                ["sg_asset_type", "is", asset_type],
            ],
            ["code", "sg_asset_type"],
            order=[{"field_name": "code", "direction": "asc"}],
        )
        self.cmb_asset.addItem("— select —", None)
        for a in self._assets:
            self.cmb_asset.addItem(a["code"], a)

    def on_paths_dropped(self, paths):
        self.media = staging.classify_paths(paths)
        mt = self.media.get("media_type")
        if mt in ("unknown", "mixed"):
            self.media_info.setText(
                "Mixed media types — drop one type only."
                if mt == "mixed"
                else "Unsupported drop — need an image (PNG/JPG/TIFF/EXR…), "
                "MOV, or a 3D file (OBJ, FBX, GLB, PLY, USD…)."
            )
            self.media = None
            self.btn_send.setEnabled(False)
            self.btn_preview.setEnabled(False)
            self._update_modes()
            self._refresh_preview()
            return

        if mt == "model_3d":
            names = ", ".join(f.name for f in self.media["files"][:3])
            more = "" if len(self.media["files"]) <= 3 else " …"
            info = (
                "3D asset delivery (%d file%s): %s%s\n"
                "→ routes to the ingest watch folder by Asset Type."
                % (
                    len(self.media["files"]),
                    "" if len(self.media["files"]) == 1 else "s",
                    names,
                    more,
                )
            )
            # Force Asset mode; 3D isn't review media.
            self.radio_asset.setChecked(True)
            self.cmb_delivery_type.setCurrentIndex(0)
            self.media_info.setText(info)
            self.btn_send.setEnabled(True)
            self.btn_preview.setEnabled(True)
            self._update_modes()
            self._refresh_preview()
            self._log("Loaded 3D asset delivery")
            return

        if mt == "movie":
            info = "Movie: %s (color bake skipped)" % self.media["movie_path"]
        elif mt in ("exr_single", "image_single"):
            color_note = (
                "full color pipe"
                if mt == "exr_single"
                else "display-referred — color bake skipped"
            )
            info = (
                "Still: %s\nFrames %s–%s (%s)"
                % (
                    self.media["files"][0].name,
                    self.media["frame_first"],
                    self.media["frame_last"],
                    color_note,
                )
            )
        else:
            color_note = (
                "full color pipe"
                if mt == "exr_sequence"
                else "display-referred — color bake skipped"
            )
            info = (
                "Image sequence: %s\nFrames %s–%s (%d files, %s)"
                % (
                    self.media.get("exr_path_pattern"),
                    self.media["frame_first"],
                    self.media["frame_last"],
                    len(self.media["files"]),
                    color_note,
                )
            )
            self.chk_slate.setChecked(True)

        self.media_info.setText(info)
        self._update_modes()
        self.btn_send.setEnabled(True)
        self.btn_preview.setEnabled(True)
        self._refresh_preview()
        self._log("Loaded %s" % mt)

    def _submitted_by_fields(self):
        user = self.cmb_submitted_by.currentData()
        if not user:
            raise ValueError("Select Submitted by (a Flow user).")
        display = (
            user.get("name")
            or " ".join(
                p for p in (user.get("firstname"), user.get("lastname")) if p
            ).strip()
            or user.get("login")
            or "user_%s" % user["id"]
        )
        return {
            "user_id": user["id"],
            "artist": display,
            "artist_login": user.get("login") or "",
        }

    def _gather_context(self):
        submitted_by = self._submitted_by_fields()
        if self.radio_shot.isChecked():
            ep = self.cmb_episode.currentData()
            seq = self.cmb_sequence.currentData()
            shot = self.cmb_shot.currentData()
            if not (ep and seq and shot):
                raise ValueError("Select Episode, Sequence, and Shot.")
            return {
                "entity_type": "Shot",
                "entity": shot,
                "episode": ep["code"],
                "sequence": seq["code"],
                "step": self.cmb_step.currentText().strip(),
                "version": int(self.txt_version.text().strip() or "1"),
                "submitted_for": self.cmb_submitted.currentText().strip(),
                "description": self.txt_description.text().strip(),
                "project_id": PROJECT_ID,
                "task_id": None,
                **submitted_by,
            }
        asset = self.cmb_asset.currentData()
        if not asset:
            raise ValueError("Select an Asset.")
        return {
            "entity_type": "Asset",
            "entity": asset,
            "asset_type": self.cmb_asset_type.currentText(),
            "step": self.cmb_step.currentText().strip() or "turntable",
            "version": int(self.txt_version.text().strip() or "1"),
            "submitted_for": self.cmb_submitted.currentText().strip(),
            "description": self.txt_description.text().strip(),
            "project_id": PROJECT_ID,
            "task_id": None,
            **submitted_by,
        }

    def _record_fields(self):
        """Flow record metadata — only gathered when a record is requested."""
        return {
            "submitted_for": self.cmb_submitted.currentText().strip(),
            "description": self.txt_description.text().strip(),
            "task_id": None,
            **self._submitted_by_fields(),
        }

    def _gather_reference_context(self):
        if self.radio_shot.isChecked():
            ep = self.cmb_episode.currentData()
            seq = self.cmb_sequence.currentData()
            shot = self.cmb_shot.currentData()
            if not (ep and seq and shot):
                raise ValueError("Select Episode, Sequence, and Shot.")
            context = {
                "entity_type": "Shot",
                "entity": shot,
                "episode": ep["code"],
                "sequence": seq["code"],
                "step": "temp",
                "version": 1,
                "project_id": PROJECT_ID,
            }
        else:
            asset = self.cmb_asset.currentData()
            if not asset:
                raise ValueError("Select an Asset.")
            context = {
                "entity_type": "Asset",
                "entity": asset,
                "asset_type": self.cmb_asset_type.currentText(),
                "step": "reference",
                "version": 1,
                "project_id": PROJECT_ID,
            }
        if self.chk_flow_record.isChecked():
            context.update(self._record_fields())
        return context

    def _gather_ingest_context(self):
        """Context for a Flow record on a 3D delivery (Asset only)."""
        asset = self.cmb_asset.currentData()
        if not asset:
            raise ValueError(
                "Select an Asset to create the Flow record, or untick "
                "Create Flow record to copy the files only."
            )
        return {
            "entity_type": "Asset",
            "entity": asset,
            "asset_type": self.cmb_asset_type.currentText(),
            "step": "ingest",
            "version": 1,
            "project_id": PROJECT_ID,
            **self._record_fields(),
        }

    def _create_flow_record(self, context, paths) -> str:
        """Create the Flow Version and return a summary for the dialog."""
        record = staging.create_flow_record(self.sg, self.media, context, paths)
        version = record["version"]
        summary = "Flow record: %s (Version %s)" % (
            version.get("code", ""),
            version["id"],
        )
        if record.get("url"):
            summary += "\n%s" % record["url"]
        for warning in record.get("warnings", []):
            summary += "\n• %s" % warning
        self._log(summary)
        return summary

    def _record_or_warn(self, context, paths) -> str:
        """Record creation never undoes a successful copy — report instead."""
        try:
            return self._create_flow_record(context, paths)
        except Exception as exc:
            self._log("ERROR creating Flow record: %s" % exc)
            return "Files were copied, but the Flow record failed:\n%s" % exc

    def _reset_media(self):
        self.btn_send.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self.media = None
        self.drop.setText(self.drop.IDLE)
        self.media_info.setText("No media loaded.")
        if self._preview_window is not None:
            self._preview_window.close()
        self._update_modes()

    def _shot_cdl_path(self):
        """CDL the bake would apply to the current shot, so the preview matches."""
        if not self.radio_shot.isChecked():
            return None
        ep = self.cmb_episode.currentData()
        seq = self.cmb_sequence.currentData()
        shot = self.cmb_shot.currentData()
        if not (ep and seq and shot):
            return None
        return preview.shot_cdl_path(ep["code"], seq["code"], shot["code"])

    def open_preview(self):
        if not self.media:
            return
        if self._preview_window is None:
            self._preview_window = preview.PreviewWindow(self)
        self._preview_window.set_media(self.media, self._shot_cdl_path())
        self._preview_window.show()
        self._preview_window.raise_()
        self._preview_window.activateWindow()

    def _refresh_preview(self):
        window = self._preview_window
        if window is not None and window.isVisible():
            window.set_media(self.media, self._shot_cdl_path())

    def send_to_watcher(self):
        if not self.media or not self.tk:
            return

        wants_record = self.chk_flow_record.isChecked()

        # 3D asset deliveries go straight to the ingest watch folder.
        if self.media.get("media_type") == "model_3d":
            try:
                # Validate the record context before copying anything so a
                # missing Asset can't leave files in ingest with no record.
                context = self._gather_ingest_context() if wants_record else None
                asset_type = self.cmb_asset_type.currentText()
                names = [f.name for f in self.media["files"]]
                dest = staging.stage_asset_ingest(
                    INGEST_FOLDER, asset_type, self.media
                )
                self._log("Copied 3D delivery to:\n%s" % dest)
                message = (
                    "Copied %d file(s) into:\n%s\n\n"
                    "The ingest watcher will convert + turntable them."
                    % (len(names), dest)
                )
                if context:
                    message += "\n\n%s" % self._record_or_warn(
                        context, [dest / n for n in names]
                    )
                QMessageBox.information(self, "Sent to 3D Ingest", message)
                self._reset_media()
            except Exception as exc:
                self._log("ERROR: %s" % exc)
                QMessageBox.critical(self, "Ingest failed", str(exc))
            return

        if self._is_reference_mode():
            try:
                context = self._gather_reference_context()
                copied = staging.stage_reference(
                    self.tk,
                    self.media,
                    context,
                    self.txt_name_override.text(),
                )
                self._log("Copied reference media:\n%s" % copied[0].parent)
                message = (
                    "Copied %d file(s) into the %s reference folder.\n\n%s"
                    % (len(copied), context["entity_type"], copied[0].parent)
                )
                message += "\n\n%s" % (
                    self._record_or_warn(context, copied)
                    if wants_record
                    else "No Flow record was created."
                )
                QMessageBox.information(self, "Reference copied", message)
                self._reset_media()
                self.txt_name_override.clear()
            except Exception as exc:
                self._log("ERROR: %s" % exc)
                QMessageBox.critical(self, "Reference copy failed", str(exc))
            return

        try:
            context = self._gather_context()
            include_slate = self.chk_slate.isChecked()
            # Single-frame slate is optional; sequences default on but still respect checkbox
            flag_path = staging.stage_and_flag(
                self.tk, self.sg, self.media, context, include_slate
            )
            self._log("Staged + flag written:\n%s" % flag_path)
            QMessageBox.information(
                self,
                "Queued for QT Watcher",
                "Files staged and flag written.\n\n"
                "The Mac Studio QT Watcher will pick this up on its next poll "
                "(~30s).\n\nFlag:\n%s" % flag_path,
            )
        except Exception as exc:
            self._log("ERROR: %s" % exc)
            QMessageBox.critical(self, "Send failed", str(exc))

    def open_ingest(self):
        path = INGEST_FOLDER
        if not os.path.isdir(path):
            QMessageBox.warning(
                self,
                "Ingest folder missing",
                "Expected:\n%s\n\nCreate it or update INGEST_FOLDER." % path,
            )
            return
        subprocess.Popen(["open", path])
        self._log("Opened ingest folder: %s" % path)
        QMessageBox.information(
            self,
            "3D Asset Ingest",
            "Drop 3D deliveries into a type folder:\n\n"
            "  Character / Prop / Environment / Vehicle / FX\n\n"
            "The ingest watcher on the Mac Studio processes them automatically.",
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Buffalo Review Drop")
    win = ReviewDropWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
