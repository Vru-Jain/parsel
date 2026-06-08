"""
ui/theme.py
-----------
A single, restrained visual language for the whole app so it feels intentional
rather than stock-Windows. One accent colour, calm neutrals, generous spacing,
consistent 8px radii. Applied once at the QApplication level.

Also holds the plain-language onboarding/help copy, written for a novice
data-entry operator (no jargon).
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# ---- palette (kept tiny on purpose) --------------------------------------- #
# Parseltongue green: the brand accent for Parsel.
ACCENT = "#1f8a5b"
ACCENT_DK = "#15663f"
INK = "#1f2937"      # primary text
MUTED = "#6b7280"    # secondary text
LINE = "#e6e8ec"     # hairline borders
BG = "#f6f7f9"       # window background
CARD = "#ffffff"     # raised surfaces


def light_palette() -> QPalette:
    """A fixed light palette so the app looks identical whether Windows is in
    light or dark mode. Without this, unstyled widgets (tabs, combos, tables)
    inherit the OS dark palette and render dark-on-dark."""
    p = QPalette()
    p.setColor(QPalette.Window, QColor(BG))
    p.setColor(QPalette.WindowText, QColor(INK))
    p.setColor(QPalette.Base, QColor(CARD))
    p.setColor(QPalette.AlternateBase, QColor("#f0f2f5"))
    p.setColor(QPalette.Text, QColor(INK))
    p.setColor(QPalette.Button, QColor(CARD))
    p.setColor(QPalette.ButtonText, QColor(INK))
    p.setColor(QPalette.ToolTipBase, QColor(INK))
    p.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    p.setColor(QPalette.Highlight, QColor(ACCENT))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.PlaceholderText, QColor(MUTED))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#aab0b8"))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#aab0b8"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#aab0b8"))
    return p

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
    color: {INK};
}}
QMainWindow, QDialog {{ background: {BG}; }}

QLabel#h1 {{ font-size: 19px; font-weight: 700; color: {INK}; }}
QLabel#sub {{ font-size: 12px; color: {MUTED}; }}
QLabel#caption {{ font-size: 11px; color: {MUTED}; }}
QLabel#sectionLabel {{ font-size: 11px; font-weight: 600; color: {MUTED};
                       text-transform: uppercase; letter-spacing: 1px; }}

/* default (secondary) buttons: quiet, outlined */
QPushButton {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 7px 14px;
    color: {INK};
}}
QPushButton:hover {{ border-color: #c9ced6; background: #fbfcfd; }}
QPushButton:pressed {{ background: #f0f2f5; }}
QPushButton:disabled {{ color: #aab0b8; background: #f4f5f7; border-color: {LINE}; }}

/* the one primary action */
QPushButton#primaryButton {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    border-radius: 9px;
    color: white;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background: {ACCENT_DK}; border-color: {ACCENT_DK}; }}
QPushButton#primaryButton:pressed {{ background: #0f4f31; border-color: #0f4f31; }}
QPushButton#primaryButton:disabled {{ background: #a9d3bd; border-color: #a9d3bd; color: #eef6f0; }}

/* success-tinted post-run buttons */
QPushButton#openButton {{
    background: #ecf6ef; border: 1px solid #bfe0cb; color: #1c7c3c; font-weight: 600;
}}
QPushButton#openButton:hover {{ background: #e1f1e7; }}
QPushButton#openButton:pressed {{ background: #d3e9db; }}

QLineEdit {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 8px;
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QListWidget, QPlainTextEdit {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 8px;
    padding: 4px;
}}

QProgressBar {{
    background: #eceef1; border: none; border-radius: 7px;
    height: 14px; text-align: center; color: {INK}; font-size: 11px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 7px; }}

QToolTip {{
    background: {INK}; color: white; border: none; padding: 6px 8px; border-radius: 6px;
}}
QTextBrowser {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; }}

/* --- Settings dialog widgets --- */
QTabWidget::pane {{
    border: 1px solid {LINE}; border-radius: 8px; top: -1px; background: {CARD};
}}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 7px 16px; border: none;
    border-bottom: 2px solid transparent; margin-right: 2px;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {INK}; }}

QComboBox {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 7px;
    padding: 5px 8px; color: {INK}; min-height: 20px;
}}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {CARD}; color: {INK}; border: 1px solid {LINE};
    selection-background-color: {ACCENT}; selection-color: white;
}}

/* Clean number field. Steppers hidden (they render as broken stubs under a
   QSS theme without arrow images); typing, keyboard up/down, and the scroll
   wheel all still adjust the value. */
QSpinBox, QDoubleSpinBox {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 7px;
    padding: 5px 8px; color: {INK}; min-height: 22px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 0; height: 0; border: none;
}}

QTableWidget {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 8px;
    gridline-color: {LINE}; color: {INK};
}}
QHeaderView::section {{
    background: #f0f2f5; color: {MUTED}; padding: 6px; border: none;
    border-bottom: 1px solid {LINE}; font-weight: 600;
}}
QTableWidget::item:selected {{ background: #d8eee2; color: {INK}; }}

QCheckBox {{ color: {INK}; spacing: 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #cfd4dc; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #b7bdc7; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #cfd4dc; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


# ---- onboarding copy ------------------------------------------------------- #
# the three steps shown as a strip across the top
STEPS = [
    ("1", "Add a manual PDF", "Drag it in; pick pages if you like"),
    ("2", "Process & Preview", "See the table before you save it"),
    ("3", "Save Excel", "Happy with it? One click to save"),
]

HELP_HTML = f"""
<div style="font-family:Segoe UI;">
  <h2 style="color:{ACCENT}; margin-bottom:2px;">How to use this app</h2>
  <p style="color:{MUTED}; margin-top:0;">
    It turns a spare-parts <b>manual PDF</b> into a tidy <b>Excel sheet</b> for you —
    so you don't have to type every part by hand. Everything runs on this computer;
    nothing is uploaded anywhere.
  </p>

  <h3>The 3 steps</h3>
  <ol>
    <li><b>Add a manual PDF</b> — drag the file onto the box, or click <i>Add Files</i>.
        Optionally type which <b>Pages</b> hold the parts tables (e.g. <i>1-5, 12, 20-30</i>)
        to go faster and skip diagram/instruction pages.</li>
    <li><b>Press “Process &amp; Preview”</b> — watch the bar; you can keep working,
        it won't freeze. A <b>preview window</b> then shows the exact table.</li>
    <li><b>Review, then click “Save Excel”</b> in the preview — nothing is written
        to disk until you do. Wrong columns? Close, fix in Settings ▸ Mappings, re-process.</li>
  </ol>

  <h3>What the Excel contains</h3>
  <p>One row per spare part, in the standard column order your team uses:</p>
  <ul>
    <li><b>Part Name</b> — the part's description (already in proper case).</li>
    <li><b>Spare Group</b> — the drawing/section heading the part belongs to.</li>
    <li><b>DrawingNo</b> — the plate number from the manual.</li>
    <li><b>DrawingPosNo</b> — the item / position number on that plate.</li>
    <li>Other columns (Material, Text, etc.) are filled when the manual provides them.</li>
  </ul>

  <h3>Manual word → Excel column</h3>
  <table cellpadding="4" style="border-collapse:collapse;">
    <tr><td><b>“Plate” / “Dwg. No.”</b></td><td>→ DrawingNo</td></tr>
    <tr><td><b>“Item No.” / “No.” / “Pos.”</b></td><td>→ DrawingPosNo</td></tr>
    <tr><td><b>“Designation” / “Description”</b></td><td>→ Part Name</td></tr>
    <tr><td><b>“Part No.” / “Article No.”</b></td><td>→ Spare Part No</td></tr>
    <tr><td><b>Section heading / Title</b></td><td>→ Spare Group</td></tr>
  </table>

  <h3>If something looks off</h3>
  <ul>
    <li><b>“Scanned / image-only”</b> message → the PDF is a picture, not text.
        It can still be read if OCR is installed; otherwise ask your supervisor.</li>
    <li><b>“Open in Excel” error</b> → close the Excel file first, then Process again.</li>
    <li><b>Only need part of a big manual?</b> Type page numbers in the <i>Pages</i> boxes
        (leave blank for the whole file).</li>
    <li><b>An “Unmapped columns” note</b> → a column name was unfamiliar; its data is kept on a
        separate “Unmapped (review)” sheet so nothing is lost.</li>
  </ul>

  <p style="color:{MUTED};">Always give the finished sheet a quick read before handing it on —
  the app does the typing, you do the final check.</p>
</div>
"""
