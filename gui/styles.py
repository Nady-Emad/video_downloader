"""Dark and light themes (QSS) and shared colours. Spacing follows a 4/8/12/16 px rhythm."""
from __future__ import annotations

BG = "#12141a"
SURFACE = "#1a1d25"
SURFACE_ALT = "#232733"
SURFACE_HOVER = "#2b3040"
BORDER = "#2d3342"
TEXT = "#e8eaf0"
MUTED = "#98a1b3"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6b9eff"
SUCCESS = "#3ecf8e"
WARNING = "#f5a623"
ERROR = "#ff6161"
DEBUG = "#7d8799"
INFO = "#6cb6ff"

LOG_COLORS = {"INFO": INFO, "SUCCESS": SUCCESS, "WARNING": WARNING, "ERROR": ERROR, "DEBUG": DEBUG}

STATUS_COLORS = {
    "Pending": MUTED,
    "Extracting Information": INFO,
    "Downloading": ACCENT,
    "Completed": SUCCESS,
    "Failed": ERROR,
    "Cancelled": WARNING,
    "Paused": WARNING,
    "Interrupted": ERROR,
}


PALETTES = {
    "dark": dict(BG=BG, SURFACE=SURFACE, SURFACE_ALT=SURFACE_ALT, SURFACE_HOVER=SURFACE_HOVER, BORDER=BORDER,
                 TEXT=TEXT, MUTED=MUTED, ACCENT=ACCENT, ACCENT_HOVER=ACCENT_HOVER, SUCCESS=SUCCESS, WARNING=WARNING,
                 ERROR=ERROR, DEBUG=DEBUG, INFO=INFO, ALT_ROW="#1e222b", CONSOLE_BG="#0e1015", SELECTION="#2b3a5a",
                 SCROLL="#343a49", SCROLL_HOVER="#434a5d", DANGER_HOVER="#3a2226", PRIMARY_OFF="#2a3a5c",
                 PRIMARY_OFF_TEXT="#8794b0", PRESSED="#1f232d", SIDEBAR="#0f1117", SIDEBAR_HOVER="#1a1d25"),
    "light": dict(BG="#f4f6fa", SURFACE="#ffffff", SURFACE_ALT="#eef1f6", SURFACE_HOVER="#e3e8f1", BORDER="#d5dbe6",
                  TEXT="#1c2230", MUTED="#5d6779", ACCENT="#2f6fe4", ACCENT_HOVER="#4a85f0", SUCCESS="#1f9d6b",
                  WARNING="#b8730a", ERROR="#d64545", DEBUG="#7d8799", INFO="#2f6fe4", ALT_ROW="#f7f9fc",
                  CONSOLE_BG="#ffffff", SELECTION="#d6e4ff", SCROLL="#c3cbd9", SCROLL_HOVER="#a9b3c6",
                  DANGER_HOVER="#fdeaea", PRIMARY_OFF="#b9cdf5", PRIMARY_OFF_TEXT="#ffffff", PRESSED="#dde3ee",
                  SIDEBAR="#ffffff", SIDEBAR_HOVER="#eef1f6"),
}


def build_qss(theme: str = "dark") -> str:
    """Complete application stylesheet for 'dark' or 'light'."""
    p = PALETTES.get(theme, PALETTES["dark"])
    BG, SURFACE, SURFACE_ALT, SURFACE_HOVER = p["BG"], p["SURFACE"], p["SURFACE_ALT"], p["SURFACE_HOVER"]
    BORDER, TEXT, MUTED, ACCENT, ACCENT_HOVER = p["BORDER"], p["TEXT"], p["MUTED"], p["ACCENT"], p["ACCENT_HOVER"]
    SUCCESS, WARNING, ERROR, DEBUG, INFO = p["SUCCESS"], p["WARNING"], p["ERROR"], p["DEBUG"], p["INFO"]
    ALT_ROW, CONSOLE_BG, SELECTION, SCROLL, SCROLL_HOVER = p["ALT_ROW"], p["CONSOLE_BG"], p["SELECTION"], p["SCROLL"], p["SCROLL_HOVER"]
    DANGER_HOVER, PRIMARY_OFF, PRIMARY_OFF_TEXT, PRESSED = p["DANGER_HOVER"], p["PRIMARY_OFF"], p["PRIMARY_OFF_TEXT"], p["PRESSED"]
    SIDEBAR, SIDEBAR_HOVER = p["SIDEBAR"], p["SIDEBAR_HOVER"]
    return f"""
* {{ font-family: "Segoe UI", "Inter", "Noto Sans", "Helvetica Neue", Arial, sans-serif; font-size: 13px; }}
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog, QWizard {{ background-color: {BG}; }}
QLabel {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---- typography */
QLabel#title {{ font-size: 20px; font-weight: 700; }}
QLabel#subtitle, QLabel#muted {{ color: {MUTED}; }}
QLabel#cardTitle {{ color: {MUTED}; font-size: 11px; font-weight: 700; }}
QLabel#statusValue {{ font-weight: 600; }}
QLabel#previewTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#emptyTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#emptyHint {{ color: {MUTED}; }}
QLabel#thumb {{ background: transparent; color: {MUTED}; }}
QLabel#metaChip {{ color: {MUTED}; }}

/* ---- cards */
QFrame#card {{ background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QGroupBox {{
    background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    margin-top: 14px; padding: 14px 12px 10px 12px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {MUTED}; }}

/* ---- inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background-color: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {ACCENT}; selection-color: white;
}}
QLineEdit, QComboBox, QSpinBox {{ min-height: 22px; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {DEBUG}; background-color: {SURFACE}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_ALT}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    outline: none; padding: 4px;
}}

/* ---- buttons */
QPushButton {{
    background-color: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 16px; min-height: 20px; font-weight: 500;
}}
QPushButton:hover {{ background-color: {SURFACE_HOVER}; }}
QPushButton:pressed {{ background-color: {PRESSED}; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton:disabled {{ color: {DEBUG}; background-color: {SURFACE}; border-color: {SURFACE_ALT}; }}
QPushButton#primary {{ background-color: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; padding: 9px 22px; }}
QPushButton#primary:hover {{ background-color: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background-color: {PRIMARY_OFF}; border-color: {PRIMARY_OFF}; color: {PRIMARY_OFF_TEXT}; }}
QPushButton#danger {{ background-color: transparent; border-color: {ERROR}; color: {ERROR}; }}
QPushButton#danger:hover {{ background-color: {DANGER_HOVER}; }}
QPushButton#danger:disabled {{ border-color: {BORDER}; color: {DEBUG}; }}
QPushButton#ghost {{ background-color: transparent; border-color: transparent; color: {MUTED}; }}
QPushButton#ghost:hover {{ background-color: {SURFACE_ALT}; color: {TEXT}; }}

/* ---- tabs */
QTabWidget::pane {{ border: none; top: 0px; background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 10px 20px; margin-right: 4px;
    border: none; border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

/* ---- tables */
QTableWidget {{
    background-color: {SURFACE}; alternate-background-color: {ALT_ROW}; border: 1px solid {BORDER};
    border-radius: 10px; gridline-color: transparent; selection-background-color: {SELECTION};
    selection-color: {TEXT}; outline: none;
}}
QHeaderView::section {{
    background-color: {SURFACE_ALT}; color: {MUTED}; border: none; border-right: 1px solid {BORDER};
    padding: 8px 10px; font-weight: 600;
}}
QTableWidget::item {{ padding: 4px 8px; }}

/* ---- lists */
QListWidget {{
    background-color: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 10px; outline: none; padding: 4px;
}}
QListWidget::item {{ border-radius: 8px; padding: 4px 6px; margin: 1px 0; }}
QListWidget::item:hover {{ background-color: {SURFACE_HOVER}; }}
QListWidget::item:disabled {{ color: {DEBUG}; }}
QListWidget::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER}; border-radius: 4px; background: {SURFACE}; }}
QListWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* ---- progress */
QProgressBar {{
    background-color: {SURFACE_ALT}; border: none; border-radius: 6px; min-height: 12px; max-height: 12px;
    text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 6px; }}

/* ---- console */
QPlainTextEdit#console {{
    background-color: {CONSOLE_BG}; border: 1px solid {BORDER}; border-radius: 8px;
    font-family: "Cascadia Mono", "JetBrains Mono", "DejaVu Sans Mono", Consolas, monospace; font-size: 12px;
}}

/* ---- misc */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 6px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 6px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER}; border-radius: 4px; background: {SURFACE_ALT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QMenu {{ background-color: {SURFACE_ALT}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {ACCENT}; }}
QToolTip {{ background-color: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QSplitter::handle {{ background-color: transparent; height: 6px; }}
QStatusBar {{ background-color: {SURFACE}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 0 8px; }}
QMessageBox {{ background-color: {SURFACE}; }}

/* ---- sidebar */
QFrame#sidebar {{ background-color: {SIDEBAR}; border: none; border-right: 1px solid {BORDER}; border-radius: 0; }}
QLabel#brand {{ font-size: 16px; font-weight: 700; }}
QLabel#version {{ color: {MUTED}; font-size: 11px; }}
QPushButton#nav {{
    background: transparent; border: none; border-radius: 8px; padding: 10px 14px; text-align: left;
    color: {MUTED}; font-weight: 600; min-height: 22px;
}}
QPushButton#nav:hover {{ background-color: {SIDEBAR_HOVER}; color: {TEXT}; }}
QPushButton#nav:checked {{ background-color: {SELECTION}; color: {TEXT}; }}
QLabel#statValue {{ font-size: 22px; font-weight: 700; }}
QLabel#statLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; }}
QLabel#pill {{ border-radius: 9px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}
QLabel#urlHint {{ color: {MUTED}; }}
QPlainTextEdit#urlEdit {{ font-size: 15px; padding: 10px 12px; border-radius: 10px; }}
QWizard QLabel {{ background: transparent; }}
"""


DARK_QSS = build_qss("dark")          # kept for compatibility
