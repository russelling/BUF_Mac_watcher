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

# Branding assets
LOGO_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/"
    "shots/_globals/logo/teardrop.png"
)
BRAND_FONT_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/_globals/"
    "fonts/SEV_LUMON LOGO_FONT/Manifold Extended CF v4.0 OTF/"
    "ManifoldExtendedCF-Medium.otf"
)

PRIMARY_BUTTON_STYLE = """
QPushButton {
    background: #2d6cb4;
    color: #ffffff;
    border: 1px solid #3f82cf;
    border-radius: 10px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover:enabled { background: #3782d6; border-color: #6aa9e8; }
QPushButton:pressed:enabled {
    background: #17456f;
    border-color: #9ccbff;
    padding-top: 10px;
    padding-bottom: 6px;
}
QPushButton:disabled { background: #262626; color: #5c5c5c; border-color: #383838; }
"""

SECONDARY_BUTTON_STYLE = """
QPushButton {
    background: #333333;
    color: #e6e6e6;
    border: 1px solid #4a4a4a;
    border-radius: 10px;
    padding: 8px 14px;
    font-size: 13px;
}
QPushButton:hover:enabled { background: #3e3e3e; border-color: #6a6a6a; }
QPushButton:pressed:enabled {
    background: #1c1c1c;
    border-color: #9ccbff;
    padding-top: 10px;
    padding-bottom: 6px;
}
QPushButton:disabled { background: #262626; color: #5c5c5c; border-color: #383838; }
"""

DANGER_BUTTON_STYLE = """
QPushButton {
    background: #382628;
    color: #e6b9bd;
    border: 1px solid #5b383c;
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 11px;
}
QPushButton:hover:enabled { background: #573237; color: #ffffff; border-color: #885159; }
QPushButton:pressed:enabled { background: #241719; border-color: #d17b85; }
QPushButton:disabled { background: #252525; color: #555555; border-color: #343434; }
"""

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

    def __init__(self, on_drop, parent=None):
        super().__init__(parent)
        self._on_drop = on_drop
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(84)
        self.setWordWrap(True)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #666; border-radius: 8px; "
            "background: #2a2a2a; color: #bbb; padding: 8px; font-size: 12px; }"
        )
        self.setText(self.IDLE)

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

        root = QWidget()
        root.setStyleSheet("QWidget { background: #1e1e1e; color: #ddd; }")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addLayout(self._build_header())

        self.drop = DropZone(self.on_paths_dropped)
        layout.addWidget(self.drop)

        media_row = QHBoxLayout()
        media_row.setSpacing(8)
        self.media_info = QLabel("No media loaded.")
        self.media_info.setWordWrap(True)
        self.media_info.setStyleSheet("color: #9ecbff; font-size: 11px;")
        self.btn_remove_loaded = QPushButton("Remove Loaded")
        self.btn_remove_loaded.setEnabled(False)
        self.btn_remove_loaded.setCursor(Qt.PointingHandCursor)
        self.btn_remove_loaded.setStyleSheet(DANGER_BUTTON_STYLE)
        self.btn_remove_loaded.setToolTip(
            "Remove the current drop from this app. Source files are not deleted."
        )
        self.btn_remove_loaded.clicked.connect(self.clear_loaded_media)
        media_row.addWidget(self.media_info, 1)
        media_row.addWidget(self.btn_remove_loaded)
        layout.addLayout(media_row)

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
        self.cmb_delivery_type.addItems(["Version", "Reference image"])
        self.cmb_delivery_type.currentIndexChanged.connect(
            self._on_delivery_type_changed
        )
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

        self.status = QTextEdit()
        self.status.setReadOnly(True)
        self.status.setFixedHeight(84)
        self.status.setStyleSheet(
            "QTextEdit { background: #141414; color: #9a9a9a; "
            "font-size: 10px; border: 1px solid #333; }"
        )
        layout.addWidget(self.status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_send = QPushButton("Send to QT Watcher")
        self.btn_send.clicked.connect(self.send_to_watcher)
        self.btn_send.setEnabled(False)
        self.btn_send.setMinimumHeight(40)
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.btn_ingest = QPushButton("Open 3D Ingest Folder")
        self.btn_ingest.clicked.connect(self.open_ingest)
        self.btn_ingest.setMinimumHeight(40)
        self.btn_ingest.setCursor(Qt.PointingHandCursor)
        self.btn_ingest.setStyleSheet(SECONDARY_BUTTON_STYLE)
        btn_row.addWidget(self.btn_send, 2)
        btn_row.addWidget(self.btn_ingest, 1)
        layout.addLayout(btn_row)

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
        subtitle = QLabel("Objects Submittable")
        subtitle.setStyleSheet("color: #7a7a7a; font-size: 11px;")
        text_col.addWidget(brand)
        text_col.addWidget(subtitle)
        header.addLayout(text_col)
        header.addStretch()
        return header

    def _log(self, msg: str):
        self.status.append(msg)

    def clear_loaded_media(self, checked=False, log_action=True):
        """Remove the pending drop from the UI without touching source files."""
        had_media = bool(self.media)
        self.media = None
        self.drop.setText(self.drop.IDLE)
        self.media_info.setText("No media loaded.")
        self.btn_remove_loaded.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.btn_send.setText(
            "Copy Reference to Shot"
            if self._is_reference_mode()
            else "Send to QT Watcher"
        )
        self.fields_box.setVisible(True)
        self._on_delivery_type_changed()
        if had_media and log_action:
            self._log("Removed loaded media (source files unchanged).")

    def _on_type_changed(self):
        is_shot = self.radio_shot.isChecked()
        if not is_shot and self._is_reference_mode():
            self.cmb_delivery_type.setCurrentIndex(0)
        self.shot_box.setVisible(is_shot)
        self.asset_box.setVisible(not is_shot)
        # 3D deliveries are asset-only; keep review fields for media.
        is_model = bool(self.media) and self.media.get("media_type") == "model_3d"
        self.fields_box.setVisible(not is_model)
        self.cmb_step.clear()
        for s in (SHOT_STEPS if is_shot else ASSET_STEPS):
            self.cmb_step.addItem(s)

    def _is_reference_mode(self):
        return self.cmb_delivery_type.currentText() == "Reference image"

    def _on_delivery_type_changed(self):
        is_reference = self._is_reference_mode()
        if is_reference:
            self.radio_shot.setChecked(True)
        self.txt_name_override.setEnabled(is_reference)
        # Step / Version / Submitted by / Submitted for / Notes stay editable
        # for references and are recorded in the reference sidecar. Only the
        # slate is bake-only, so it has nothing to act on here.
        self.chk_slate.setEnabled(not is_reference)
        if self.media and self.media.get("media_type") != "model_3d":
            self.btn_send.setText(
                "Copy Reference to Shot" if is_reference else "Send to QT Watcher"
            )

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
        if mt == "unknown":
            self.media = None
            self.media_info.setText(
                "Unsupported drop — need an image (PNG/JPG/TIFF/EXR…), "
                "MOV, or a 3D file (OBJ, FBX, GLB, PLY, USD…)."
            )
            self.btn_remove_loaded.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.fields_box.setVisible(True)
            return
        if mt == "mixed":
            self.media_info.setText("Mixed media types — drop one type only.")
            self.btn_remove_loaded.setEnabled(True)
            self.btn_send.setEnabled(False)
            self.fields_box.setVisible(True)
            return

        self.btn_remove_loaded.setEnabled(True)

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
            self.fields_box.setVisible(False)
            self.btn_send.setText("Send to 3D Ingest")
            self.media_info.setText(info)
            self.btn_send.setEnabled(True)
            self._log("Loaded 3D asset delivery")
            return

        # Any non-3D drop uses the review/QT-watcher path.
        self.fields_box.setVisible(True)
        if self._is_reference_mode() and mt not in {
            "exr_single", "exr_sequence", "image_single", "image_sequence",
        }:
            self.media_info.setText(
                "Reference mode accepts still images only; choose Version for movies."
            )
            self.btn_send.setEnabled(False)
            return
        self.btn_send.setText(
            "Copy Reference to Shot"
            if self._is_reference_mode()
            else "Send to QT Watcher"
        )

        if mt == "movie":
            info = "Movie: %s (color bake skipped)" % self.media["movie_path"]
            self.chk_slate.setEnabled(True)
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
            self.chk_slate.setEnabled(True)
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
            self.chk_slate.setEnabled(True)
            self.chk_slate.setChecked(True)

        self.media_info.setText(info)
        self._on_delivery_type_changed()
        self.btn_send.setEnabled(True)
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

    def _gather_reference_context(self):
        if not self.radio_shot.isChecked():
            raise ValueError("Reference images must be associated with a Shot.")
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
            "step": self.cmb_step.currentText().strip() or "temp",
            "version": int(self.txt_version.text().strip() or "1"),
            "submitted_for": self.cmb_submitted.currentText().strip(),
            "description": self.txt_description.text().strip(),
            "project_id": PROJECT_ID,
            **self._submitted_by_fields(),
        }

    def _begin_busy(self, label: str) -> str:
        """Show immediate click feedback while a copy runs on the UI thread."""
        previous = self.btn_send.text()
        self.btn_send.setEnabled(False)
        self.btn_send.setText(label)
        self._log(label)
        QApplication.processEvents()
        return previous

    def _end_busy(self, previous: str, succeeded: bool):
        self.btn_send.setText(previous)
        self.btn_send.setEnabled(not succeeded and bool(self.media))

    def send_to_watcher(self):
        if not self.media or not self.tk:
            return

        # 3D asset deliveries go straight to the ingest watch folder.
        if self.media.get("media_type") == "model_3d":
            previous = self._begin_busy("Copying to 3D Ingest…")
            try:
                asset_type = self.cmb_asset_type.currentText()
                dest = staging.stage_asset_ingest(
                    INGEST_FOLDER, asset_type, self.media
                )
                self._log("Copied 3D delivery to:\n%s" % dest)
                QMessageBox.information(
                    self,
                    "Sent to 3D Ingest",
                    "Copied %d file(s) into:\n%s\n\n"
                    "The ingest watcher will convert + turntable them."
                    % (len(self.media["files"]), dest),
                )
                self.clear_loaded_media(log_action=False)
            except Exception as exc:
                self._log("ERROR: %s" % exc)
                self._end_busy(previous, False)
                QMessageBox.critical(self, "Ingest failed", str(exc))
            return

        if self._is_reference_mode():
            previous = self._begin_busy("Copying reference…")
            try:
                context = self._gather_reference_context()
                copied = staging.stage_shot_reference(
                    self.tk,
                    self.media,
                    context,
                    self.txt_name_override.text(),
                )
                self._log("Copied reference image(s):\n%s" % copied[0].parent)
                QMessageBox.information(
                    self,
                    "Reference copied",
                    "Copied %d reference image(s) into the Shot reference folder.\n\n"
                    "No Flow Version was created.\n\n%s"
                    % (len(copied), copied[0].parent),
                )
                self.txt_name_override.clear()
                self.clear_loaded_media(log_action=False)
            except Exception as exc:
                self._log("ERROR: %s" % exc)
                self._end_busy(previous, False)
                QMessageBox.critical(self, "Reference copy failed", str(exc))
            return

        previous = self._begin_busy("Staging for QT Watcher…")
        try:
            context = self._gather_context()
            include_slate = self.chk_slate.isChecked()
            # Single-frame slate is optional; sequences default on but still respect checkbox
            flag_path = staging.stage_and_flag(
                self.tk, self.sg, self.media, context, include_slate
            )
            self._log("Staged + flag written:\n%s" % flag_path)
            self._end_busy(previous, True)
            QMessageBox.information(
                self,
                "Queued for QT Watcher",
                "Files staged and flag written.\n\n"
                "The Mac Studio QT Watcher will pick this up on its next poll "
                "(~30s).\n\nFlag:\n%s" % flag_path,
            )
            self.clear_loaded_media(log_action=False)
        except Exception as exc:
            self._log("ERROR: %s" % exc)
            self._end_busy(previous, False)
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
