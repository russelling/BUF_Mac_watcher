"""
theme.py — Severance / Lumon color psychology for the Review Drop UI.

60 / 30 / 10, as the show uses it:

  60%  institutional field — cool off-white with a green cast
  30%  structure — Lumon teal for panels, secondary chrome, labels
  10%  emphasis — Severance red reserved for the primary action

The palette is deliberately not dark-mode default chrome. Lumon's floors
are bright, the carpet is green, and the only red in the building means
something. The same hierarchy applies here: neutrals hold the space,
teal organises it, red is the one thing that asks to be pressed.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

# 60% — field
BG = "#E8E6DF"
BG_DEEP = "#DEDCD4"
SURFACE = "#F4F3EE"
SURFACE_RAISED = "#FBFAF6"

# 30% — structure (Lumon teal / corporate green)
STRUCTURE = "#1F5C52"
STRUCTURE_MID = "#2E7A6C"
STRUCTURE_SOFT = "#D5E3DE"
STRUCTURE_LINE = "#A8C0B8"
INK = "#1A2421"
INK_MUTED = "#5A675F"
INK_FAINT = "#8A948C"
INFO = "#1E4D6B"          # Lumon blue for status / media metadata

# 10% — emphasis (Severance red)
ACCENT = "#B42318"
ACCENT_HOVER = "#8F1C13"
ACCENT_SOFT = "#F3D6D2"

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
        stop:0.55 {BG},
        stop:1 {BG_DEEP}
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
    border: 1px solid {STRUCTURE_MID};
}}
QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled {{
    background: {BG_DEEP};
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
    background: {INK};
    border: 1px solid {STRUCTURE_LINE};
    border-radius: {RADIUS_TIGHT};
}}
QToolTip {{
    background: {INK};
    color: {SURFACE_RAISED};
    border: 1px solid {STRUCTURE};
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

# 10% — the one button that advances the pipeline.
PRIMARY_BUTTON_CSS = f"""
QPushButton {{
    background: {ACCENT};
    color: {SURFACE_RAISED};
    border: 1px solid {ACCENT};
    border-radius: {RADIUS};
    padding: 8px 16px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
QPushButton:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton:pressed {{ background: #6E150F; }}
QPushButton:disabled {{
    background: {ACCENT_SOFT};
    color: #9A7A76;
    border-color: {ACCENT_SOFT};
}}
"""

# 30% — supporting actions.
SECONDARY_BUTTON_CSS = f"""
QPushButton {{
    background: {STRUCTURE};
    color: {SURFACE_RAISED};
    border: 1px solid {STRUCTURE};
    border-radius: {RADIUS};
    padding: 8px 14px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {STRUCTURE_MID}; border-color: {STRUCTURE_MID}; }}
QPushButton:pressed {{ background: #17463E; }}
QPushButton:disabled {{
    background: {STRUCTURE_SOFT};
    color: {INK_FAINT};
    border-color: {STRUCTURE_LINE};
}}
QPushButton:checked {{
    background: {STRUCTURE_MID};
    border-color: {STRUCTURE};
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
QPushButton:pressed {{ background: {BG_DEEP}; }}
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

STATUS_CSS = f"""
QTextEdit {{
    background: {INK};
    color: {STRUCTURE_SOFT};
    font-size: 10px;
    border: 1px solid {STRUCTURE};
    border-radius: {RADIUS_TIGHT};
    padding: 4px;
}}
"""

BRAND_CSS = f"color: {INK};"
SUBTITLE_CSS = f"color: {INK_MUTED}; font-size: 11px; letter-spacing: 1px;"
MEDIA_INFO_CSS = f"color: {INFO}; font-size: 11px;"
HINT_CSS = f"color: {INK_MUTED}; font-size: 10px;"
LABEL_CSS = f"color: {INK_MUTED}; font-size: 10px;"
VALUE_CSS = f"color: {INFO}; font-size: 11px;"
PREVIEW_CANVAS_CSS = f"background: {INK};"
PREVIEW_SCROLL_CSS = (
    f"QScrollArea {{ background: {INK}; border: 1px solid {STRUCTURE_LINE}; "
    f"border-radius: {RADIUS_TIGHT}; }}"
)
