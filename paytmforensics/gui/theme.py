"""Modern dark theme: color palette + global QSS stylesheet.

Custom QSS (no external theme dependency) for full control and a professional, friendly
look inspired by modern forensic suites (dashboard + sidebar + clean tables).
"""
from __future__ import annotations

DARK = {
    "bg_app":        "#0d1117",
    "bg_sidebar":    "#0b0f15",
    "bg_panel":      "#161b22",
    "bg_card":       "#1b222c",
    "bg_card_hover": "#222c39",
    "bg_input":      "#11161d",
    "row_alt":       "#1b222c",
    "border":        "#2a313c",
    "border_soft":   "#1f2630",
    "accent":        "#2f81f7",
    "accent_cyan":   "#00b9f5",
    "text":          "#e6edf3",
    "text_muted":    "#8b949e",
    "text_dim":      "#6e7681",
    "green":         "#3fb950",
    "red":           "#f85149",
    "amber":         "#d29922",
    "purple":        "#a371f7",
    "chip":          "#21262d",
}

LIGHT = {
    "bg_app":        "#eef1f5",
    "bg_sidebar":    "#ffffff",
    "bg_panel":      "#ffffff",
    "bg_card":       "#ffffff",
    "bg_card_hover": "#eef2f7",
    "bg_input":      "#ffffff",
    "row_alt":       "#f4f7fa",
    "border":        "#d0d7de",
    "border_soft":   "#e6eaef",
    "accent":        "#2f81f7",
    "accent_cyan":   "#0a9bd6",
    "text":          "#1f2328",
    "text_muted":    "#57606a",
    "text_dim":      "#8b949e",
    "green":         "#1a7f37",
    "red":           "#cf222e",
    "amber":         "#9a6700",
    "purple":        "#8250df",
    "chip":          "#eef2f7",
}

# C is a *living* palette dict; switch it in place so existing imports stay valid.
C = dict(DARK)
_CURRENT = "dark"


def set_theme(name: str) -> None:
    global _CURRENT
    pal = LIGHT if name == "light" else DARK
    C.clear(); C.update(pal)
    _CURRENT = "light" if name == "light" else "dark"


def current_theme() -> str:
    return _CURRENT


def apply_palette(app) -> None:
    """Set the application QPalette so selection/highlight colors are reliable across
    styles (QSS alone doesn't always colour QTableView selections)."""
    from PySide6.QtGui import QPalette, QColor
    p = QPalette()
    p.setColor(QPalette.Window, QColor(C["bg_app"]))
    p.setColor(QPalette.WindowText, QColor(C["text"]))
    p.setColor(QPalette.Base, QColor(C["bg_panel"]))
    p.setColor(QPalette.AlternateBase, QColor(C["row_alt"]))
    p.setColor(QPalette.Text, QColor(C["text"]))
    p.setColor(QPalette.Button, QColor(C["bg_card"]))
    p.setColor(QPalette.ButtonText, QColor(C["text"]))
    p.setColor(QPalette.Highlight, QColor(C["accent"]))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, QColor(C["bg_card"]))
    p.setColor(QPalette.ToolTipText, QColor(C["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(C["text_dim"]))
    p.setColor(QPalette.Link, QColor(C["accent"]))
    app.setPalette(p)

# per-domain accent colors + glyphs for the sidebar / cards
DOMAIN_STYLE = {
    "dashboard":   ("◉", "#2f81f7"),
    "map":         ("◍", "#f0883e"),
    "entity":      ("☺", "#a371f7"),
    "transaction": ("₹", "#3fb950"),
    "message":     ("✉", "#00b9f5"),
    "person":      ("⚑", "#8b949e"),
    "location":    ("◈", "#f0883e"),
    "timeline":    ("⧖", "#2f81f7"),
    "job":         ("⚙", "#8b949e"),
    "consent":     ("✓", "#3fb950"),
    "notification":("▣", "#d29922"),
    "search":      ("⚲", "#8b949e"),
    "config":      ("≡", "#6e7681"),
    "diagnostic":  ("⚠", "#d29922"),
    "encrypted":   ("⚿", "#f85149"),
    "pref":        ("☰", "#6e7681"),
    "carved":      ("✂", "#f85149"),
    "cookie":      ("●", "#f0883e"),
    "webstorage":  ("■", "#f0883e"),
    "capability":  ("◆", "#a371f7"),
    "webcache":    ("⚇", "#f0883e"),
    "appstate":    ("▤", "#3fb950"),
    "crash":       ("⚠", "#f85149"),
}

# sidebar grouping
NAV_GROUPS = [
    ("OVERVIEW", ["dashboard"]),
    ("FINANCIAL", ["transaction", "entity", "carved"]),
    ("COMMUNICATIONS", ["message", "person"]),
    ("DEVICE & ACTIVITY", ["map", "location", "timeline", "job", "notification", "search"]),
    ("SHOPPING & WEB", ["appstate", "webcache", "cookie", "webstorage"]),
    ("SYSTEM & PRIVACY", ["capability", "consent", "config", "diagnostic", "pref",
                          "crash", "encrypted"]),
]


def qss() -> str:
    c = C
    return f"""
* {{ font-family: 'Segoe UI', 'Inter', Arial; font-size: 13px; color: {c['text']}; }}
QMainWindow, QWidget#root {{ background: {c['bg_app']}; }}

/* ---------- Sidebar ---------- */
QWidget#sidebar {{ background: {c['bg_sidebar']}; border-right: 1px solid {c['border_soft']}; }}
QLabel#brand {{ font-size: 17px; font-weight: 700; color: {c['text']}; padding: 16px 18px 2px 18px; }}
QLabel#brandSub {{ font-size: 11px; color: {c['text_dim']}; padding: 0 18px 12px 18px; }}
QLabel#navGroup {{ color: {c['text_dim']}; font-size: 10px; font-weight: 700;
    letter-spacing: 1px; padding: 14px 18px 4px 18px; }}
QListWidget#nav {{ background: transparent; border: none; outline: 0; padding: 4px 8px; }}
QListWidget#nav::item {{ color: {c['text_muted']}; padding: 9px 12px; border-radius: 8px; margin: 1px 4px; }}
QListWidget#nav::item:hover {{ background: {c['bg_card']}; color: {c['text']}; }}
QListWidget#nav::item:selected {{ background: {c['accent']}; color: white; font-weight: 600; }}

/* ---------- Top bar ---------- */
QWidget#topbar {{ background: {c['bg_panel']}; border-bottom: 1px solid {c['border_soft']}; }}
QLabel#pageTitle {{ font-size: 19px; font-weight: 700; }}
QLabel#caseBadge {{ background: {c['chip']}; color: {c['text_muted']};
    border: 1px solid {c['border']}; border-radius: 12px; padding: 4px 12px; font-size: 11px; }}

/* ---------- Cards ---------- */
QFrame#card {{ background: {c['bg_card']}; border: 1px solid {c['border_soft']};
    border-radius: 14px; }}
QFrame#statCard {{ background: {c['bg_card']}; border: 1px solid {c['border_soft']};
    border-radius: 14px; }}
QFrame#statCard:hover {{ background: {c['bg_card_hover']}; border: 1px solid {c['border']}; }}
QLabel#statValue {{ font-size: 26px; font-weight: 800; }}
QLabel#statLabel {{ color: {c['text_muted']}; font-size: 12px; }}
QLabel#statIcon {{ font-size: 20px; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 700; padding-bottom: 2px; }}
QLabel#kvKey {{ color: {c['text_muted']}; font-size: 12px; }}
QLabel#kvVal {{ color: {c['text']}; font-size: 13px; font-weight: 600; }}

/* ---------- Inputs ---------- */
QLineEdit, QComboBox {{ background: {c['bg_input']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 7px 10px; selection-background-color: {c['accent']}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['bg_panel']}; border: 1px solid {c['border']};
    selection-background-color: {c['accent']}; outline: 0; }}

/* ---------- Buttons ---------- */
QPushButton {{ background: {c['bg_card']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 7px 14px; color: {c['text']}; }}
QPushButton:hover {{ background: {c['bg_card_hover']}; border: 1px solid {c['accent']}; }}
QPushButton:pressed {{ background: {c['accent']}; color: white; }}
QPushButton#primary {{ background: {c['accent']}; border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #4593ff; }}

/* ---------- Tables ---------- */
QTableView {{ background: {c['bg_panel']}; alternate-background-color: {c['row_alt']};
    gridline-color: transparent; border: 1px solid {c['border_soft']}; border-radius: 12px;
    selection-background-color: {c['accent']}; selection-color: #ffffff; }}
QTableView::item {{ padding: 6px 8px; border: none; }}
/* explicit item-level rule: reliably colours the selected row in both themes */
QTableView::item:selected {{ background: {c['accent']}; color: #ffffff; }}
QTableView::item:selected:!active {{ background: {c['accent']}; color: #ffffff; }}
QHeaderView::section {{ background: {c['chip']}; color: {c['text_muted']};
    padding: 8px 10px; border: none; border-bottom: 2px solid {c['border']};
    border-right: 1px solid {c['border_soft']}; font-weight: 600; font-size: 11px; }}
QHeaderView::section:hover {{ color: {c['text']}; }}
QTableCornerButton::section {{ background: {c['chip']}; border: none; }}

/* ---------- Detail panel ---------- */
QTextEdit#detail {{ background: {c['bg_input']}; border: 1px solid {c['border_soft']};
    border-radius: 12px; font-family: 'Cascadia Code','Consolas',monospace; font-size: 12px;
    padding: 10px; }}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c['text_dim']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}

/* ---------- Misc ---------- */
QMenuBar {{ background: {c['bg_panel']}; border-bottom: 1px solid {c['border_soft']}; }}
QMenuBar::item:selected {{ background: {c['bg_card']}; }}
QMenu {{ background: {c['bg_panel']}; border: 1px solid {c['border']}; }}
QMenu::item:selected {{ background: {c['accent']}; }}
QStatusBar {{ background: {c['bg_sidebar']}; color: {c['text_muted']}; border-top: 1px solid {c['border_soft']}; }}
QLabel#chip {{ background: {c['chip']}; border: 1px solid {c['border']}; border-radius: 10px;
    padding: 3px 10px; color: {c['text_muted']}; font-size: 11px; }}
QSplitter::handle {{ background: {c['border_soft']}; }}
QScrollArea {{ border: none; background: transparent; }}
"""
