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

# Branding assets. The dark teardrop sits on the light Lumon field;
# the inverted cut is reserved for the slate bake's black backgrounds.
LOGO_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/"
    "shots/_globals/logo/teardrop.png"
)
BRAND_FONT_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/_globals/"
    "fonts/SEV_LUMON LOGO_FONT/Manifold Extended CF v4.0 OTF/"
    "ManifoldExtendedCF-Medium.otf"
)

# NOTE: the old literal Qt-stylesheet constants that used to live here
# (PRIMARY_BUTTON_STYLE / SECONDARY_BUTTON_STYLE / DANGER_BUTTON_STYLE) have
# been retired in favor of theme.py's CSS constants (theme.PRIMARY_BUTTON_CSS
# etc.), which is now the single styling system for this app. See theme.py.

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

from PySide6.QtCore import Qt, QSize, QTimer
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
    QCompleter,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QCheckBox,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QButtonGroup,
)

import sgtk
import preview
import staging
import theme


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


PLACEHOLDER = "— select —"
NO_EPISODE = "  (no episode)"
NO_SEQUENCE = "  (no sequence)"


class SearchableComboBox(QComboBox):
    """
    Editable combo: arrow shows every item, typing filters by contains-match.

    Qt's editable combo applies the completer filter to the arrow popup too.
    With current text "— select —" (or any code MatchContains doesn't hit),
    that filter empties the list — so the dropdown looks bare until the
    artist starts typing. Detach the completer for the arrow popup; keep it
    for keyboard entry.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMaxVisibleItems(24)
        self._completer = QCompleter(self)
        self._completer.setModel(self.model())
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self.setCompleter(self._completer)

    def showPopup(self):
        self._completer.setModel(self.model())
        self.setCompleter(None)
        super().showPopup()

    def hidePopup(self):
        super().hidePopup()
        self._completer.setModel(self.model())
        self.setCompleter(self._completer)


def _searchable_combo() -> QComboBox:
    return SearchableComboBox()


def entity_code(text: str) -> str:
    """Drop the '(no sequence)' style annotation from a combo label."""
    code = (text or "").split("  (")[0].strip()
    return "" if code == PLACEHOLDER else code


def _link_label(value) -> str:
    if isinstance(value, dict):
        return str(value.get("code") or value.get("id") or value)
    return str(value)


class DropZone(QLabel):
    IDLE = (
        "Drop media here — or click to browse\n"
        "Images · EXR · MOV   |   3D: OBJ / FBX / GLB / PLY"
    )

    def __init__(self, on_drop, on_double_click=None, on_browse=None, parent=None):
        super().__init__(parent)
        self._on_drop = on_drop
        self._on_double_click = on_double_click
        self._on_browse = on_browse
        self._ignore_browse = False
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(96)
        self.setWordWrap(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(theme.DROP_ZONE_IDLE)
        self.setText(self.IDLE)
        self.setToolTip(
            "Click to browse for files. Drop files here too. "
            "Double-click to preview the loaded media."
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            self.setStyleSheet(theme.DROP_ZONE_ACTIVE)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(theme.DROP_ZONE_IDLE)

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet(theme.DROP_ZONE_IDLE)
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            self._on_drop(paths)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() != Qt.LeftButton or not self._on_browse:
            return
        # Delay so a double-click can cancel the browse and open Preview.
        QTimer.singleShot(220, self._emit_browse)

    def mouseDoubleClickEvent(self, event):
        self._ignore_browse = True
        if self._on_double_click:
            self._on_double_click()

    def _emit_browse(self):
        if self._ignore_browse:
            self._ignore_browse = False
            return
        if self._on_browse:
            self._on_browse()


class ReviewDropWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Buffalo Review Drop")
        self.setFixedSize(QSize(660, 700))

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
        # Color-pipe stages shared with Preview chips and written into the
        # QT Watcher flag so deselection carries through to the bake.
        self._color_pipe = {
            "log_convert": True,
            "cdl": True,
            "show_lut": True,
        }
        self._schema_fields = {}
        self._seq_episode_field = None
        self._shot_sequence_field = None

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        self.setStyleSheet(theme.APP_CSS)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addLayout(self._build_header())

        self.drop = DropZone(
            self.on_paths_dropped,
            self.open_preview,
            self.browse_for_media,
        )
        layout.addWidget(self.drop)

        media_row = QHBoxLayout()
        media_row.setSpacing(8)
        self.media_info = QLabel("No media loaded.")
        self.media_info.setWordWrap(True)
        self.media_info.setStyleSheet(theme.MEDIA_INFO_CSS)
        self.btn_remove_loaded = QPushButton("Remove Loaded")
        self.btn_remove_loaded.setEnabled(False)
        self.btn_remove_loaded.setCursor(Qt.PointingHandCursor)
        # theme.py has no DANGER_BUTTON_CSS; GHOST_BUTTON_CSS (the same
        # low-emphasis style used for "Open 3D Ingest Folder") is the closest
        # semantic match for a secondary, non-primary action.
        self.btn_remove_loaded.setStyleSheet(theme.GHOST_BUTTON_CSS)
        self.btn_remove_loaded.setToolTip(
            "Remove the current drop from this app. Source files are not deleted."
        )
        self.btn_remove_loaded.clicked.connect(self.clear_loaded_media)
        media_row.addWidget(self.media_info, 1)
        media_row.addWidget(self.btn_remove_loaded)
        layout.addLayout(media_row)

        # Entity type — compact tab strip (only as wide as Shot | Asset)
        tab_host = QWidget()
        tab_host.setObjectName("EntityTabRow")
        tab_host.setStyleSheet(theme.ENTITY_TAB_ROW_CSS)
        tab_host.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        type_row = QHBoxLayout(tab_host)
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(0)
        self.radio_shot = QPushButton("Shot")
        self.radio_shot.setObjectName("EntityTabShot")
        self.radio_asset = QPushButton("Asset")
        self.radio_asset.setObjectName("EntityTabAsset")
        for tab in (self.radio_shot, self.radio_asset):
            tab.setCheckable(True)
            tab.setStyleSheet(theme.ENTITY_TAB_CSS)
            tab.setCursor(Qt.PointingHandCursor)
            tab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.radio_shot.setChecked(True)
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        self.type_group.addButton(self.radio_shot)
        self.type_group.addButton(self.radio_asset)
        type_row.addWidget(self.radio_shot)
        type_row.addWidget(self.radio_asset)
        tab_bar = QHBoxLayout()
        tab_bar.setContentsMargins(0, 0, 0, 0)
        tab_bar.addWidget(tab_host)
        tab_bar.addStretch(1)
        layout.addLayout(tab_bar)
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
        self.cmb_episode = _searchable_combo()
        self.cmb_sequence = _searchable_combo()
        self.cmb_shot = _searchable_combo()
        self.cmb_episode.currentIndexChanged.connect(self._refresh_sequences)
        self.cmb_sequence.currentIndexChanged.connect(self._refresh_shots)
        self.cmb_shot.currentIndexChanged.connect(self._autofill_version)
        self.cmb_shot.lineEdit().editingFinished.connect(self._autofill_version)
        shot_form.addRow("Episode", self.cmb_episode)
        shot_form.addRow("Sequence", self.cmb_sequence)
        shot_form.addRow("Shot", self.cmb_shot)
        left_col.addWidget(self.shot_box)

        self.asset_box = QGroupBox("Asset context")
        asset_form = QFormLayout(self.asset_box)
        asset_form.setContentsMargins(8, 8, 8, 8)
        asset_form.setSpacing(6)
        self.cmb_asset_type = QComboBox()
        for code in staging.ASSET_TYPE_CODES:
            self.cmb_asset_type.addItem(
                "%s  ·  %s" % (code, staging.ASSET_TYPE_LABELS[code]),
                code,
            )
        self.cmb_asset = _searchable_combo()
        self.cmb_asset.setToolTip(
            "Script / Flow Asset name, e.g. set_mdr or veh_marks_volvo"
        )
        self.txt_asset_real = QLineEdit()
        self.txt_asset_real.setPlaceholderText("e.g. wf_stage_02 / sedan_960")
        self.txt_asset_real.setToolTip("Real-world location or asset name")
        self.cmb_asset_variant = QComboBox()
        self.cmb_asset_variant.setEditable(True)
        self.cmb_asset_variant.setInsertPolicy(QComboBox.NoInsert)
        self.cmb_asset_variant.setToolTip(
            "Variant under the real name, e.g. base, previz, pre_crash"
        )
        self._reload_variant_choices()
        self.cmb_asset_type.currentIndexChanged.connect(self._on_asset_type_changed)
        self.cmb_asset.currentIndexChanged.connect(self._autofill_version)
        self.cmb_asset.lineEdit().editingFinished.connect(self._autofill_version)
        self.txt_asset_real.textChanged.connect(self._autofill_version)
        self.cmb_asset_variant.currentTextChanged.connect(self._autofill_version)
        asset_form.addRow("Type", self.cmb_asset_type)
        asset_form.addRow("Script name", self.cmb_asset)
        asset_form.addRow("Real name", self.txt_asset_real)
        asset_form.addRow("Variant", self.cmb_asset_variant)
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
        self.txt_version.setToolTip(
            "Next free number for {Shot|Asset}_{Step}_v### in Flow. "
            "Updates when you change Shot / Asset / Step."
        )
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
        self.cmb_step.currentTextChanged.connect(self._autofill_version)
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
        self.lbl_record_hint.setStyleSheet(theme.HINT_CSS)
        record_row.addWidget(self.chk_flow_record)
        record_row.addWidget(self.lbl_record_hint, 1)
        layout.addLayout(record_row)

        self.status = QTextEdit()
        self.status.setReadOnly(True)
        self.status.setFixedHeight(84)
        self.status.setStyleSheet(theme.STATUS_CSS)
        layout.addWidget(self.status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_send = QPushButton("Send to QT Watcher")
        self.btn_send.clicked.connect(self.send_to_watcher)
        self.btn_send.setEnabled(False)
        self.btn_send.setMinimumHeight(34)
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_preview = QPushButton("Preview…")
        self.btn_preview.clicked.connect(self.open_preview)
        self.btn_preview.setEnabled(False)
        self.btn_preview.setMinimumHeight(34)
        self.btn_preview.setCursor(Qt.PointingHandCursor)
        self.btn_preview.setToolTip(
            "Check the loaded media for visibility and color before sending."
        )
        self.btn_ingest = QPushButton("Open 3D Ingest Folder")
        self.btn_ingest.clicked.connect(self.open_ingest)
        self.btn_ingest.setMinimumHeight(34)
        self.btn_ingest.setCursor(Qt.PointingHandCursor)
        btn_row.addWidget(self.btn_preview, 1)
        btn_row.addWidget(self.btn_send, 3)
        btn_row.addWidget(self.btn_ingest, 2)
        self.btn_preview.setStyleSheet(theme.SECONDARY_BUTTON_CSS)
        self.btn_send.setStyleSheet(theme.PRIMARY_BUTTON_CSS)
        self.btn_ingest.setStyleSheet(theme.GHOST_BUTTON_CSS)
        layout.addLayout(btn_row)

        self._update_modes()

        self._log("Bootstrapping Flow…")
        try:
            self.tk, self.sg = get_sgtk()
            self._log("Connected to Flow.")
            self._load_submitted_for()
            self._load_users()
            self._load_shot_context()
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
        brand.setStyleSheet(theme.BRAND_CSS)
        subtitle = QLabel("OBJECTS SUBMITTABLE")
        subtitle.setStyleSheet(theme.SUBTITLE_CSS)
        text_col.addWidget(brand)
        text_col.addWidget(subtitle)
        header.addLayout(text_col)
        header.addStretch()
        return header

    def _log(self, msg: str):
        self.status.append(msg)

    def clear_loaded_media(self, checked=False, log_action=True):
        """
        Remove the pending drop from the UI without touching source files.

        This is the user-facing entry point (wired to the "Remove Loaded"
        button); it delegates the actual "no media loaded" UI reset to
        _reset_media() so that path and the internal after-send reset share
        one implementation, then adds the Remove-Loaded-specific logging.
        """
        had_media = bool(self.media)
        self._reset_media()
        if had_media and log_action:
            self._log("Removed loaded media (source files unchanged).")

    def _on_type_changed(self):
        is_shot = self.radio_shot.isChecked()
        self.shot_box.setVisible(is_shot)
        self.asset_box.setVisible(not is_shot)
        self.cmb_step.blockSignals(True)
        self.cmb_step.clear()
        for s in (SHOT_STEPS if is_shot else ASSET_STEPS):
            self.cmb_step.addItem(s)
        self.cmb_step.blockSignals(False)
        self._update_modes()
        self._autofill_version()

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
        # Step / Version / Submitted by / Submitted for / Notes stay editable
        # for references and are recorded in the reference sidecar. Only the
        # slate is bake-only, so it has nothing to act on for references or
        # 3D ingest drops (record_optional covers both).
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

    def _project_filter(self):
        return [["project", "is", {"type": "Project", "id": PROJECT_ID}]]

    def _has_field(self, entity_type, field):
        return bool(field) and self._schema_field(entity_type, [field]) == field

    def _schema_field(self, entity_type, candidates):
        """First of `candidates` this Flow site actually has on the entity."""
        key = (entity_type, tuple(candidates))
        if key in self._schema_fields:
            return self._schema_fields[key]
        try:
            schema = self.sg.schema_field_read(entity_type)
        except Exception as exc:
            self._log("WARNING: could not read %s schema: %s" % (entity_type, exc))
            schema = {}
        field = next((c for c in candidates if c in schema), None)
        if schema and not field:
            self._log(
                "WARNING: %s has none of %s — links will not be filtered."
                % (entity_type, ", ".join(candidates))
            )
        self._schema_fields[key] = field
        return field

    def _find_all(self, entity_type, fields):
        """Every record of a type on this project, ordered by code."""
        try:
            return self.sg.find(
                entity_type,
                self._project_filter(),
                [f for f in fields if f],
                order=[{"field_name": "code", "direction": "asc"}],
            )
        except Exception as exc:
            self._log("WARNING: could not load %ss: %s" % (entity_type, exc))
            return []

    def _load_shot_context(self):
        """
        Pull the whole project once and filter the combos in memory.

        Querying Sequence-by-Episode and Shot-by-Sequence hid anything whose
        link was unset, so a real shot could be impossible to pick. Everything
        is loaded here instead; the cascade narrows the lists but never drops
        a record.
        """
        if not self.sg:
            return
        self._seq_episode_field = self._schema_field(
            "Sequence", ["episode", "sg_episode"]
        )
        self._shot_sequence_field = self._schema_field(
            "Shot", ["sg_sequence", "sequence"]
        )
        self._episodes = self._find_all("Episode", ["code"])
        self._sequences = self._find_all(
            "Sequence", ["code", self._seq_episode_field]
        )
        self._shots = self._find_all("Shot", ["code", self._shot_sequence_field])
        self._log(
            "Loaded %d episode(s), %d sequence(s), %d shot(s)."
            % (len(self._episodes), len(self._sequences), len(self._shots))
        )

        self.cmb_episode.blockSignals(True)
        self.cmb_episode.clear()
        self.cmb_episode.addItem(PLACEHOLDER, None)
        for ep in self._episodes:
            self.cmb_episode.addItem(ep["code"], ep)
        self.cmb_episode.blockSignals(False)
        self._refresh_sequences()

    def _refresh_sequences(self, *_args):
        """Sequences under the chosen Episode, or all of them if none is."""
        if not self.sg:
            return
        episode = self.cmb_episode.currentData()
        self.cmb_sequence.blockSignals(True)
        self.cmb_sequence.clear()
        self.cmb_sequence.addItem(PLACEHOLDER, None)
        for seq in self._sequences:
            linked = seq.get(self._seq_episode_field) if self._seq_episode_field else None
            if episode and linked and linked["id"] != episode["id"]:
                continue
            self.cmb_sequence.addItem(
                seq["code"] + ("" if linked else NO_EPISODE), seq
            )
        self.cmb_sequence.blockSignals(False)
        self._refresh_shots()

    def _refresh_shots(self, *_args):
        """Shots under the chosen Sequence, or all of them if none is."""
        if not self.sg:
            return
        sequence = self.cmb_sequence.currentData()
        self.cmb_shot.blockSignals(True)
        self.cmb_shot.clear()
        self.cmb_shot.addItem(PLACEHOLDER, None)
        for shot in self._shots:
            linked = (
                shot.get(self._shot_sequence_field)
                if self._shot_sequence_field
                else None
            )
            if sequence and linked and linked["id"] != sequence["id"]:
                continue
            self.cmb_shot.addItem(
                shot["code"] + ("" if linked else NO_SEQUENCE), shot
            )
        self.cmb_shot.blockSignals(False)
        self._autofill_version()

    def _selected_asset_type(self) -> str:
        """Short type code (chr|prp|env|veh) from the Type combo."""
        data = self.cmb_asset_type.currentData()
        if data:
            return str(data)
        return staging.normalize_asset_type(self.cmb_asset_type.currentText())

    def _reload_variant_choices(self):
        current = ""
        if hasattr(self, "cmb_asset_variant"):
            current = (self.cmb_asset_variant.currentText() or "").strip()
        self.cmb_asset_variant.blockSignals(True)
        self.cmb_asset_variant.clear()
        for variant in staging.ASSET_VARIANT_DEFAULTS:
            self.cmb_asset_variant.addItem(variant)
        if current:
            index = self.cmb_asset_variant.findText(current)
            if index >= 0:
                self.cmb_asset_variant.setCurrentIndex(index)
            else:
                self.cmb_asset_variant.setEditText(current)
        else:
            self.cmb_asset_variant.setCurrentIndex(0)
        self.cmb_asset_variant.blockSignals(False)

    def _on_asset_type_changed(self, *_args):
        self._reload_variant_choices()
        self._load_assets()

    def _asset_hierarchy_from_ui(self) -> dict:
        """Normalize type / script / real / variant from the Asset form."""
        return staging.asset_hierarchy(
            self._selected_asset_type(),
            entity_code(self.cmb_asset.currentText()) or "",
            self.txt_asset_real.text(),
            self.cmb_asset_variant.currentText() or "base",
        )

    def _load_assets(self, *_args):
        if not self.sg:
            return
        if not self._assets:
            self._assets = self._find_all("Asset", ["code", "sg_asset_type"])
            self._log("Loaded %d asset(s)." % len(self._assets))
        asset_type = self._selected_asset_type()
        self.cmb_asset.blockSignals(True)
        self.cmb_asset.clear()
        self.cmb_asset.addItem(PLACEHOLDER, None)
        for asset in self._assets:
            if not staging.asset_type_matches(
                asset_type, asset.get("sg_asset_type") or ""
            ):
                continue
            self.cmb_asset.addItem(asset["code"], asset)
        self.cmb_asset.blockSignals(False)
        self._autofill_version()

    def browse_for_media(self):
        """File picker for the drop zone click."""
        exts = sorted(
            staging.IMAGE_EXTS | staging.MOVIE_EXTS | staging.MODEL_3D_EXTS
        )
        patterns = " ".join("*%s" % ext for ext in exts)
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose review media",
            "",
            "Review media (%s);;All files (*)" % patterns,
        )
        if paths:
            self.on_paths_dropped(paths)

    def _version_entity_code(self):
        """Shot or Asset code currently selected / typed, or None."""
        if self.radio_shot.isChecked():
            combo = self.cmb_shot
            pool = self._shots
        else:
            combo = self.cmb_asset
            pool = self._assets
        chosen = combo.currentData()
        if chosen and chosen.get("code"):
            return chosen["code"]
        typed = entity_code(combo.currentText())
        if not typed:
            return None
        for item in pool:
            if (item.get("code") or "").lower() == typed.lower():
                return item["code"]
        return typed

    def _autofill_version(self, *_args, minimum=None):
        """Set Version to the next free {entity}_{step}_v### in Flow."""
        if not self.sg:
            return
        code = self._version_entity_code()
        step = self.cmb_step.currentText().strip()
        if not code or not step:
            return
        entity_type = "Shot" if self.radio_shot.isChecked() else "Asset"
        real_name = ""
        variant = ""
        if entity_type == "Asset":
            real_name = self.txt_asset_real.text().strip()
            variant = self.cmb_asset_variant.currentText().strip()
            if not real_name or not variant:
                return
        try:
            number = staging.next_version_from_flow(
                self.sg,
                PROJECT_ID,
                entity_type,
                code,
                step,
                real_name=real_name,
                variant=variant,
            )
        except Exception as exc:
            self._log("Could not read next version from Flow: %s" % exc)
            return
        if minimum is not None:
            try:
                number = max(number, int(minimum))
            except (TypeError, ValueError):
                pass
        self.txt_version.setText(str(number))

    def on_paths_dropped(self, paths):
        self.media = staging.classify_paths(paths)
        mt = self.media.get("media_type")
        if mt == "unknown":
            # Nothing usable was dropped at all — fully clear so there's
            # nothing left to "remove".
            self.media = None
            self.media_info.setText(
                "Unsupported drop — need an image (PNG/JPG/TIFF/EXR…), "
                "MOV, or a 3D file (OBJ, FBX, GLB, PLY, USD…)."
            )
            self.btn_remove_loaded.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_preview.setEnabled(False)
            self.fields_box.setVisible(True)
            self._update_modes()
            self._refresh_preview()
            return
        if mt == "mixed":
            # A real (if unusable) drop — keep self.media set so Remove
            # Loaded has something to clear.
            self.media_info.setText("Mixed media types — drop one type only.")
            self.btn_remove_loaded.setEnabled(True)
            self.btn_send.setEnabled(False)
            self.btn_preview.setEnabled(False)
            self.fields_box.setVisible(True)
            self._update_modes()
            self._refresh_preview()
            return

        self.btn_remove_loaded.setEnabled(True)

        if mt == "model_3d":
            names = ", ".join(f.name for f in self.media["files"][:3])
            more = "" if len(self.media["files"]) <= 3 else " …"
            info = (
                "3D asset delivery (%d file%s): %s%s\n"
                "→ routes to ingest as "
                "{type}/{script_name}/{real_name}/{variant}."
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
            ep, seq, shot = self._shot_selection()
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
        hierarchy = self._asset_hierarchy_from_ui()
        return {
            "entity_type": "Asset",
            "entity": self._asset_selection(),
            "asset_type": hierarchy["asset_type"],
            "script_name": hierarchy["script_name"],
            "real_name": hierarchy["real_name"],
            "variant": hierarchy["variant"],
            "step": self.cmb_step.currentText().strip() or "turntable",
            "version": int(self.txt_version.text().strip() or "1"),
            "submitted_for": self.cmb_submitted.currentText().strip(),
            "description": self.txt_description.text().strip(),
            "project_id": PROJECT_ID,
            "task_id": None,
            **submitted_by,
        }

    def _resolve_entity(self, combo, entity_type, pool, fields, links, label):
        """
        Turn what's in a context combo into a real Flow entity.

        Picked from the list, matched against what was loaded, looked up live
        in Flow (the lists go stale the moment someone adds a shot), and only
        then offered as something to create — with the links the schema
        actually has, so an override can't invent fields Flow doesn't carry.
        """
        code = entity_code(combo.currentText())
        if not code:
            return None
        chosen = combo.currentData()
        if chosen and (chosen.get("code") or "").lower() == code.lower():
            return chosen
        for entity in pool:
            if (entity.get("code") or "").lower() == code.lower():
                return entity

        query_fields = [f for f in fields if f]
        try:
            found = self.sg.find_one(
                entity_type,
                self._project_filter() + [["code", "is", code]],
                query_fields,
            )
        except Exception as exc:
            raise ValueError("Could not look up %s '%s': %s" % (label, code, exc))
        if found:
            pool.append(found)
            self._log("Resolved %s '%s' from Flow." % (label, code))
            return found

        created = self._create_entity(entity_type, code, links, query_fields, label)
        # Kept in the pool rather than pushed into the combos: a repopulate
        # here would wipe the codes still being typed into the row below.
        pool.append(created)
        return created

    def _create_entity(self, entity_type, code, links, fields, label):
        """Create a missing context entity, once the artist confirms it."""
        schema_links = {}
        for field, value in (links or {}).items():
            if not field or not value:
                continue
            if not self._has_field(entity_type, field):
                self._log(
                    "WARNING: %s has no field '%s' on this site — not set."
                    % (entity_type, field)
                )
                continue
            schema_links[field] = value
        detail = "\n".join(
            "  %s: %s" % (field, _link_label(value))
            for field, value in schema_links.items()
        )
        answer = QMessageBox.question(
            self,
            "Create %s in Flow?" % label,
            "No %s named '%s' exists on this project.\n\n"
            "Create it in Flow now?%s"
            % (label, code, ("\n\n" + detail) if detail else ""),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            raise ValueError(
                "%s '%s' does not exist in Flow — pick one from the list or "
                "let it be created." % (label, code)
            )
        data = {
            "project": {"type": "Project", "id": PROJECT_ID},
            "code": code,
        }
        data.update(schema_links)
        created = self.sg.create(entity_type, data)
        created.setdefault("code", code)
        self._log("Created %s '%s' in Flow (id %s)." % (label, code, created["id"]))
        return created

    def _shot_selection(self):
        """Resolve Episode → Sequence → Shot, creating any that are missing."""
        episode = self._resolve_entity(
            self.cmb_episode, "Episode", self._episodes, ["code"], {}, "Episode"
        )
        if not episode:
            raise ValueError("Select or type an Episode.")
        sequence = self._resolve_entity(
            self.cmb_sequence,
            "Sequence",
            self._sequences,
            ["code", self._seq_episode_field],
            {self._seq_episode_field: episode},
            "Sequence",
        )
        if not sequence:
            raise ValueError("Select or type a Sequence.")
        shot = self._resolve_entity(
            self.cmb_shot,
            "Shot",
            self._shots,
            ["code", self._shot_sequence_field],
            {self._shot_sequence_field: sequence},
            "Shot",
        )
        if not shot:
            raise ValueError("Select or type a Shot.")
        return episode, sequence, shot

    def _asset_selection(self):
        asset = self._resolve_entity(
            self.cmb_asset,
            "Asset",
            self._assets,
            ["code", "sg_asset_type"],
            {"sg_asset_type": self._selected_asset_type()},
            "Asset",
        )
        if not asset:
            raise ValueError("Select or type a script name (Flow Asset).")
        return asset

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
            ep, seq, shot = self._shot_selection()
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
            hierarchy = self._asset_hierarchy_from_ui()
            context = {
                "entity_type": "Asset",
                "entity": self._asset_selection(),
                "asset_type": hierarchy["asset_type"],
                "script_name": hierarchy["script_name"],
                "real_name": hierarchy["real_name"],
                "variant": hierarchy["variant"],
                "step": "reference",
                "version": 1,
                "project_id": PROJECT_ID,
            }
        if self.chk_flow_record.isChecked():
            context.update(self._record_fields())
        return context

    def _gather_ingest_context(self):
        """Context for a Flow record on a 3D delivery (Asset only)."""
        hierarchy = self._asset_hierarchy_from_ui()
        asset = self.cmb_asset.currentData()
        if not asset:
            raise ValueError(
                "Select a script name (Flow Asset) to create the Flow "
                "record, or untick Create Flow record to copy the files only."
            )
        return {
            "entity_type": "Asset",
            "entity": asset,
            "asset_type": hierarchy["asset_type"],
            "script_name": hierarchy["script_name"],
            "real_name": hierarchy["real_name"],
            "variant": hierarchy["variant"],
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
        """Return to the empty-drop UI state; source files are untouched."""
        self.btn_remove_loaded.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self.media = None
        self.drop.setText(self.drop.IDLE)
        self.media_info.setText("No media loaded.")
        self._reset_picture_adjustments()
        if self._preview_window is not None:
            self._preview_window.close()
        # _update_modes() derives button text and fields_box visibility from
        # self.media / self.radio_shot / self.cmb_delivery_type state, so it
        # takes care of both without needing them set explicitly here.
        self._update_modes()

    def _reset_picture_adjustments(self):
        """Return exposure / gamma / channel / color-pipe chips to defaults."""
        self._color_pipe = {
            "log_convert": True,
            "cdl": True,
            "show_lut": True,
        }
        if self._preview_window is not None:
            self._preview_window.reset_adjustments()

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

    def open_preview(self):
        if not self.media:
            return
        missing = _missing_tools_for_media(self.media)
        if missing:
            detail = "\n".join("  • %s" % item for item in missing)
            answer = QMessageBox.warning(
                self,
                "Preview tools required",
                "This media needs the following on this Mac to preview:\n\n"
                "%s\n\n"
                "Install with:\n\n"
                "  brew install openimageio ffmpeg\n\n"
                "Send / reference / 3D ingest do not need these tools.\n\n"
                "Open Preview anyway?"
                % detail,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        if self._preview_window is None:
            self._preview_window = preview.PreviewWindow(self)
            self._preview_window.pipe_changed.connect(self._on_preview_pipe_changed)
        self._preview_window.set_pipe_options(self._color_pipe)
        self._preview_window.set_media(self.media, self._shot_cdl_path())
        self._preview_window.show()
        self._preview_window.raise_()
        self._preview_window.activateWindow()

    def _on_preview_pipe_changed(self, options: dict):
        self._color_pipe = {
            "log_convert": bool(options.get("log_convert", True)),
            "cdl": bool(options.get("cdl", True)),
            "show_lut": bool(options.get("show_lut", True)),
        }

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
            previous = self._begin_busy("Copying to 3D Ingest…")
            try:
                # Validate the record context before copying anything so a
                # missing Asset can't leave files in ingest with no record.
                context = self._gather_ingest_context() if wants_record else None
                hierarchy = (
                    {
                        "asset_type": context["asset_type"],
                        "script_name": context["script_name"],
                        "real_name": context["real_name"],
                        "variant": context["variant"],
                    }
                    if context
                    else self._asset_hierarchy_from_ui()
                )
                names = [f.name for f in self.media["files"]]
                dest = staging.stage_asset_ingest(
                    INGEST_FOLDER,
                    self.media,
                    hierarchy=hierarchy,
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
                self.tk,
                self.sg,
                self.media,
                context,
                include_slate,
                color_pipe=self._color_pipe,
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
            # Deliberately not a full clear_loaded_media() here: the media
            # stays loaded (Preview still works) because the watcher hasn't
            # created the Flow Version yet — bump past what we just queued
            # so a second Send doesn't reuse the same number.
            try:
                used = int(self.txt_version.text().strip() or "1")
            except ValueError:
                used = 1
            self._autofill_version(minimum=used + 1)
            self._reset_picture_adjustments()
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
            "Drop 3D deliveries under:\n\n"
            "  {type}/{script_name}/{real_name}/{variant}/\n\n"
            "Types: chr · prp · env · veh\n"
            "Example: env/set_mdr/wf_stage_02/base/\n\n"
            "The ingest watcher on the Mac Studio processes them automatically.",
        )


def _missing_tools_for_media(media: dict) -> list:
    """
    Tools this media needs for Preview that are not on this Mac.

    Launch and Send never require these — only Preview of EXR / DPX / MOV
    (and other non-Qt stills). PNG / JPG / TIFF need nothing external.
    """
    if not media:
        return []
    media_type = media.get("media_type")
    if media_type == "model_3d":
        return []

    needs_oiio = False
    needs_ffmpeg = media_type == "movie"

    if media_type in ("exr_single", "exr_sequence"):
        needs_oiio = True
    else:
        for path in media.get("files") or []:
            ext = Path(path).suffix.lower()
            if ext in preview.LINEAR_EXTS or ext in {".dpx", ".cin", ".psd", ".iff"}:
                needs_oiio = True
            elif ext not in preview.QT_READABLE_EXTS and media_type != "movie":
                needs_oiio = True

    # EXR can use ffmpeg as a fallback when oiiotool is absent.
    has_oiio = bool(preview.find_tool(preview.OIIOTOOL, "oiiotool"))
    has_ffmpeg = bool(preview.find_tool(preview.FFMPEG, "ffmpeg"))

    missing = []
    if needs_ffmpeg and not has_ffmpeg:
        missing.append("ffmpeg (brew install ffmpeg)")
    if needs_oiio and not (has_oiio or has_ffmpeg):
        missing.append("oiiotool or ffmpeg (brew install openimageio ffmpeg)")
    elif needs_oiio and not has_oiio and has_ffmpeg:
        # ffmpeg alone can show an approximate EXR — not a hard blocker.
        pass
    return missing


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Buffalo Review Drop")
    win = ReviewDropWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
