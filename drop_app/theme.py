"""
theme.py — Severance / Lumon color psychology for the Review Drop UI.

Palette taken from the MDR floor still: fluorescent white walls, sage carpet,
forest partitions, Mark's navy suit. Applied as 60 / 30 / 10:

  60%  field — white and pale cool gray (the walls and light)
  30%  structure — sage and olive (the carpet and partitions)
  10%  emphasis — dark navy (the suit) reserved for the primary action
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Tokens — left-to-right from the reference swatch strip
# ---------------------------------------------------------------------------

FOREST = "#1A2A22"       # 1 deep forest green
OLIVE = "#4A5C40"        # 2 muted olive
SAGE = "#8B9A7C"         # 3 carpet sage
WHITE = "#F7F7F5"        # 4 pure white
PALE_GRAY = "#E6E7EA"    # 5 pale cool gray
MID_GRAY = "#9A9DA4"     # 6 medium cool gray
SLATE = "#5C6572"        # 7 slate blue-gray
NAVY = "#1A2330"         # 8 dark navy / charcoal
MIDNIGHT = "#0B0E14"     # 9 midnight black

# 60% — field
BG = PALE_GRAY
BG_DEEP = "#D9DBE0"
SURFACE = WHITE
SURFACE_RAISED = "#FCFCFB"

# 30% — structure
STRUCTURE = OLIVE
STRUCTURE_MID = FOREST
STRUCTURE_SOFT = "#D8DFD2"   # sage washed into the field
STRUCTURE_LINE = SAGE
INK = NAVY
INK_MUTED = SLATE
INK_FAINT = MID_GRAY
INFO = SLATE

# 10% — emphasis
ACCENT = NAVY
ACCENT_HOVER = MIDNIGHT
ACCENT_SOFT = "#C5C9D0"      # navy diluted for disabled

RADIUS = "10px"
RADIUS_TIGHT = "6px"

# ---------------------------------------------------------------------------
# Stylesheets
# ---------------------------------------------------------------------------

APP_CSS = f"""
QMainWindow, QDialog {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 {SURFACE_RAISED},
        stop:0.45 {WHITE},
        stop:1 {PALE_GRAY}
    );
    color: {INK};
}}
QWidget {{
    color: {INK};
    font-size: 12px;
}}
QGroupBox {{
    background: {SURFACE};
    border: 1px solid {STRUCTURE_LINE};
    border-radius: {RADIUS};
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    color: {STRUCTURE};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {STRUCTURE};
}}
QLabel {{
    background: transparent;
    color: {INK};
}}
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {{
    background: {SURFACE_RAISED};
    color: {INK};
    border: 1px solid {STRUCTURE_LINE};
    border-radius: {RADIUS_TIGHT};
    padding: 4px 8px;
    selection-background-color: {STRUCTURE_SOFT};
    selection-color: {INK};
}}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
    border: 1px solid {STRUCTURE};
}}
QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled {{
    background: {PALE_GRAY};
    color: {INK_FAINT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE_RAISED};
    color: {INK};
    border: 1px solid {STRUCTURE_LINE};
    selection-background-color: {STRUCTURE_SOFT};
    selection-color: {INK};
}}
QRadioButton, QCheckBox {{
    spacing: 8px;
    color: {INK};
    background: transparent;
}}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 14px;
    height: 14px;
}}
QRadioButton::indicator {{
    border: 1px solid {STRUCTURE};
    border-radius: 8px;
    background: {SURFACE_RAISED};
}}
QRadioButton::indicator:checked {{
    background: {STRUCTURE};
    border: 1px solid {STRUCTURE};
}}
QCheckBox::indicator {{
    border: 1px solid {STRUCTURE};
    border-radius: 3px;
    background: {SURFACE_RAISED};
}}
QCheckBox::indicator:checked {{
    background: {STRUCTURE};
    border: 1px solid {STRUCTURE};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {STRUCTURE_LINE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {STRUCTURE};
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}}
QScrollArea {{
    background: {MIDNIGHT};
    border: 1px solid {STRUCTURE_LINE};
    border-radius: {RADIUS_TIGHT};
}}
QToolTip {{
    background: {NAVY};
    color: {WHITE};
    border: 1px solid {STRUCTURE};
    padding: 4px 8px;
}}
"""

# Preview dialog only — no QLabel/QWidget background rules. APP_CSS blanks
# image painting on Shotgun Desktop's macOS Qt; keep this narrow.
PREVIEW_DIALOG_CSS = f"""
QDialog {{
    background: {WHITE};
    color: {INK};
}}
QCheckBox, QRadioButton {{
    color: {INK};
    spacing: 8px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {STRUCTURE_LINE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {STRUCTURE};
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}}
QComboBox {{
    background: {SURFACE_RAISED};
    color: {INK};
    border: 1px solid {STRUCTURE_LINE};
    border-radius: {RADIUS_TIGHT};
    padding: 4px 8px;
}}
"""

DROP_ZONE_IDLE = f"""
QLabel {{
    border: 2px dashed {STRUCTURE_LINE};
    border-radius: {RADIUS};
    background: {STRUCTURE_SOFT};
    color: {STRUCTURE};
    padding: 10px;
    font-size: 12px;
}}
"""

DROP_ZONE_ACTIVE = f"""
QLabel {{
    border: 2px solid {STRUCTURE};
    border-radius: {RADIUS};
    background: {SURFACE_RAISED};
    color: {STRUCTURE};
    padding: 10px;
    font-size: 12px;
}}
"""

# 10% — the one button that advances the pipeline (navy suit).
PRIMARY_BUTTON_CSS = f"""
QPushButton {{
    background: {ACCENT};
    color: {WHITE};
    border: 1px solid {ACCENT};
    border-radius: {RADIUS};
    padding: 8px 16px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
QPushButton:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton:pressed {{ background: {MIDNIGHT}; }}
QPushButton:disabled {{
    background: {ACCENT_SOFT};
    color: {SLATE};
    border-color: {ACCENT_SOFT};
}}
"""

# 30% — supporting actions (forest / olive).
SECONDARY_BUTTON_CSS = f"""
QPushButton {{
    background: {FOREST};
    color: {WHITE};
    border: 1px solid {FOREST};
    border-radius: {RADIUS};
    padding: 8px 14px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {OLIVE}; border-color: {OLIVE}; }}
QPushButton:pressed {{ background: {MIDNIGHT}; }}
QPushButton:disabled {{
    background: {STRUCTURE_SOFT};
    color: {INK_FAINT};
    border-color: {STRUCTURE_LINE};
}}
QPushButton:checked {{
    background: {OLIVE};
    border-color: {FOREST};
}}
"""

GHOST_BUTTON_CSS = f"""
QPushButton {{
    background: {SURFACE_RAISED};
    color: {STRUCTURE};
    border: 1px solid {STRUCTURE_LINE};
    border-radius: {RADIUS};
    padding: 6px 12px;
}}
QPushButton:hover {{
    background: {STRUCTURE_SOFT};
    border-color: {STRUCTURE};
}}
QPushButton:pressed {{ background: {PALE_GRAY}; }}
QPushButton:disabled {{
    color: {INK_FAINT};
    border-color: {STRUCTURE_LINE};
}}
QPushButton:checked {{
    background: {STRUCTURE_SOFT};
    border-color: {STRUCTURE};
    color: {STRUCTURE};
}}
"""

# Toggle chips for color-pipe stages (checkable). Selected = stage on.
CHIP_CSS = f"""
QPushButton {{
    background: {SURFACE_RAISED};
    color: {INK_MUTED};
    border: 1px solid {STRUCTURE_LINE};
    border-radius: {RADIUS_TIGHT};
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {STRUCTURE_SOFT};
    border-color: {STRUCTURE};
    color: {STRUCTURE};
}}
QPushButton:checked {{
    background: {STRUCTURE};
    border-color: {FOREST};
    color: {WHITE};
}}
QPushButton:checked:hover {{
    background: {OLIVE};
    border-color: {FOREST};
    color: {WHITE};
}}
QPushButton:disabled {{
    background: {PALE_GRAY};
    color: {INK_FAINT};
    border-color: {STRUCTURE_LINE};
}}
"""

# Shot / Asset entity switch — tab strip, one selected.
ENTITY_TAB_CSS = f"""
QPushButton {{
    background: transparent;
    color: {STRUCTURE};
    border: none;
    border-radius: 0px;
    padding: 8px 22px;
    font-size: 12px;
    font-weight: 600;
    min-width: 88px;
}}
QPushButton:hover {{
    background: {SURFACE_RAISED};
    color: {FOREST};
}}
QPushButton:checked {{
    background: {FOREST};
    color: {WHITE};
}}
QPushButton:checked:hover {{
    background: {OLIVE};
    color: {WHITE};
}}
QPushButton#EntityTabShot {{
    border-top-left-radius: {RADIUS_TIGHT};
    border-bottom-left-radius: {RADIUS_TIGHT};
}}
QPushButton#EntityTabAsset {{
    border-top-right-radius: {RADIUS_TIGHT};
    border-bottom-right-radius: {RADIUS_TIGHT};
}}
"""

ENTITY_TAB_ROW_CSS = f"""
QWidget#EntityTabRow {{
    background: {STRUCTURE_SOFT};
    border: 1px solid {STRUCTURE_LINE};
    border-radius: {RADIUS_TIGHT};
}}
"""

STATUS_CSS = f"""
QTextEdit {{
    background: {MIDNIGHT};
    color: {SAGE};
    font-size: 10px;
    border: 1px solid {FOREST};
    border-radius: {RADIUS_TIGHT};
    padding: 4px;
}}
"""

BRAND_CSS = f"color: {NAVY};"
SUBTITLE_CSS = f"color: {SLATE}; font-size: 11px; letter-spacing: 1px;"
MEDIA_INFO_CSS = f"color: {SLATE}; font-size: 11px;"
HINT_CSS = f"color: {SLATE}; font-size: 10px;"
LABEL_CSS = f"color: {SLATE}; font-size: 10px;"
VALUE_CSS = f"color: {OLIVE}; font-size: 11px;"
PREVIEW_CANVAS_CSS = f"background: {MIDNIGHT};"
PREVIEW_SCROLL_CSS = (
    f"QScrollArea {{ background: {MIDNIGHT}; border: 1px solid {STRUCTURE_LINE}; "
    f"border-radius: {RADIUS_TIGHT}; }}"
)
