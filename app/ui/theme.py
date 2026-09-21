"""Visual theme: palette, stylesheet and SVG helpers.

The look is lifted from analytics_dashboard — a white sidebar, a light-grey
card grid, drop shadows, rounded corners and a single blue accent — but the
QSvgPixmap recolor trick and shadow helper are generalised here so every
widget in the app can share them.

Light, dark and a pure-black "AMOLED" theme (BLACK -- black window, dark-grey
surfaces and a single bright accent, after the Telegram themes in
_ref_themes/) are just colour dicts (LIGHT / DARK / BLACK), any of which can
be re-accented (ACCENT_PRESETS / set_appearance) and re-sized (ZOOM_LEVELS /
fs). `COLORS` is the *active* one — kept as a single mutable dict object (rather than rebound)
so every module that did `from .theme import COLORS` sees a switch without
re-importing; callers just need to rebuild their widgets afterwards (see
MainWindow._switch_theme, which mirrors the existing language-switch
rebuild).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QTableWidget

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
SVGS = ASSETS / "svgs"

# ------------------------------------------------------------ colour maths
def _rgb(hex_color: str) -> tuple[float, float, float]:
    c = QColor(hex_color)
    return c.redF(), c.greenF(), c.blueF()


def _mix(a: str, b: str, t: float) -> str:
    """`a` blended toward `b` by `t` (0 = a, 1 = b), as "#RRGGBB"."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return QColor.fromRgbF(ra + (rb - ra) * t, ga + (gb - ga) * t,
                           ba + (bb - ba) * t).name().upper()


def _luminance(hex_color: str) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _readable_on(accent: str, bg: str, dark_bg: bool, minimum: float = 3.0) -> str:
    """`accent`, nudged lighter (dark bg) or darker (light bg) just far
    enough to keep `minimum`:1 contrast against `bg` -- a neon lime or
    yellow is a fine accent on black but unreadable on white, and a deep
    crimson the reverse."""
    color = QColor(accent)
    for _ in range(40):
        if _contrast(color.name(), bg) >= minimum:
            break
        h, s, l, a = color.getHslF()
        l = min(1.0, l + 0.03) if dark_bg else max(0.0, l - 0.03)
        color.setHslF(max(h, 0.0), s, l, a)
    return color.name().upper()


def _accent_tokens(base: dict, accent: str, dark: bool) -> dict:
    """Every accent-derived palette entry for `accent` on `base`'s surfaces
    -- see set_theme. `on_accent` is the text colour that stays
    readable *on* the accent (primary buttons), which flips to near-black
    for a light accent like yellow."""
    acc = _readable_on(accent, base["card"], dark)
    disabled = _mix(base["card"], acc, 0.38 if dark else 0.45)
    return {
        "accent": acc,
        "accent_soft": _mix(base["card"], acc, 0.16 if dark else 0.10),
        "accent_track": _mix(base["card"], acc, 0.10 if dark else 0.07),
        "accent_hover": _mix(acc, "#FFFFFF" if dark else "#000000", 0.18 if dark else 0.15),
        "accent_disabled": disabled,
        "accent_disabled_text": (_mix(disabled, base["text"], 0.45) if dark
                                 else _mix(disabled, "#FFFFFF", 0.7)),
        "on_accent": ("#FFFFFF" if _contrast(acc, "#FFFFFF") >= _contrast(acc, "#0B0B0B")
                      else "#0B0B0B"),
        "hour": acc,
        "activity": acc,
        "bar_from": _mix(acc, "#FFFFFF", 0.15),
        "bar_to": acc,
    }


# ------------------------------------------------------------------ palette
LIGHT = {
    "bg": "#F4F6FA",
    "card": "#FFFFFF",
    "card_border": "transparent",
    "accent": "#1B59F8",
    "accent_soft": "#E9F0FF",
    "accent_track": "#EEF3FF",
    "accent_hover": "#1449D6",
    "accent_disabled": "#A9C1F7",
    "accent_disabled_text": "#EEF3FF",
    "on_accent": "#FFFFFF",
    "text": "#12203A",
    "muted": "#6B7480",
    "faint": "#9AA3AF",
    "line": "#E7EBF1",
    "scrollbar": "#CBD3DE",
    "good": "#22C55E",
    "warn": "#F59E0B",
    "hot": "#F04438",
    "win": "#F2C230",
    "hour": "#1B59F8",
    "weekday": "#7C4DFF",
    "posts": "#06B6D4",
    "activity": "#1B59F8",
    "bar_from": "#3B7BFF",
    "bar_to": "#1B59F8",
    "shadow": (20, 32, 58),
}

DARK = {
    "bg": "#10141F",
    "card": "#1A2032",
    "card_border": "#2A3142",
    "accent": "#4C8DFF",
    "accent_soft": "#1E2A47",
    "accent_track": "#212C46",
    "accent_hover": "#6FA3FF",
    "accent_disabled": "#2C3B5E",
    "accent_disabled_text": "#7C88A3",
    "on_accent": "#FFFFFF",
    "text": "#E7ECF5",
    "muted": "#97A2B8",
    "faint": "#6B7690",
    "line": "#2A3142",
    "scrollbar": "#3A4256",
    "good": "#34D399",
    "warn": "#FBBF24",
    "hot": "#F87171",
    "win": "#F2C230",
    "hour": "#4C8DFF",
    "weekday": "#9D7BFF",
    "posts": "#22D3EE",
    "activity": "#4C8DFF",
    "bar_from": "#5B93FF",
    "bar_to": "#3B6FE0",
    "shadow": (0, 0, 0),
}

# Pure-black "AMOLED" theme, after the Telegram themes in _ref_themes/
# (Amoled Black / the ABTheme series): a #000 window, dark-grey surfaces
# (their #232323 / #373737 bubble greys) and one bright accent -- Pixel Blue
# here, swappable through ACCENT_PRESETS like Telegram's own accent picker.
BLACK = {
    "bg": "#000000",
    "card": "#141414",
    "card_border": "#262626",
    "text": "#F2F2F2",
    "muted": "#A8A8A8",
    "faint": "#767676",
    "line": "#262626",
    "scrollbar": "#3A3A3A",
    "good": "#34D399",
    "warn": "#FBBF24",
    "hot": "#F87171",
    "win": "#F2C230",
    "weekday": "#B28DFF",
    "posts": "#22D3EE",
    "shadow": (0, 0, 0),
}
BLACK.update(_accent_tokens(BLACK, "#5B97F6", dark=True))

# Accent choices, lifted from the reference Telegram themes' accent colours
# (CRIMSON / CYAN / FIRE / LIME / ORANGE / PHLOX / PIXEL BLUE / Quite Red /
# YELLOW). "" = the active theme's own default. Each is adjusted per theme
# for readability (see _readable_on), so a neon one still works on light.
ACCENT_PRESETS = [
    ("#5B97F6", "Pixel Blue"), ("#00D7D7", "Cyan"), ("#00D700", "Lime"),
    ("#E0E505", "Yellow"), ("#FF8C00", "Orange"), ("#FF3B30", "Fire"),
    ("#DC123C", "Crimson"), ("#E0415B", "Quite Red"), ("#DF00FF", "Phlox"),
]

# ---------------------------------------------------------------- zoom
# Interface zoom: (regular delta, title delta) in font px on top of the
# stylesheet's own sizes. Anything at/above TITLE_MIN_SIZE is a "big title"
# (page titles, stat values, the brand) and moves less than body text.
ZOOM_LEVELS = ("small", "standard", "large")
_ZOOM_DELTAS = {"small": (-2, -3), "standard": (0, 0), "large": (2, 1)}
TITLE_MIN_SIZE = 18

# Fixed 16-swatch palette offered when picking a folder color. Same set in
# both themes — these are saturated enough to read on light or dark card
# backgrounds.
FOLDER_COLORS = [
    "#EF4444", "#F97316", "#F59E0B", "#EAB308",
    "#84CC16", "#22C55E", "#10B981", "#14B8A6",
    "#06B6D4", "#0EA5E9", "#3B82F6", "#6366F1",
    "#8B5CF6", "#A855F7", "#D946EF", "#EC4899",
]

# The currently active palette, mutated in place by set_theme() — see the
# module docstring for why this stays one dict object rather than a rebound
# name.
COLORS = dict(LIGHT)
_mode = "light"  # resolved 'light' | 'dark' | 'black', kept for queries
_accent = ""     # "" = the theme's own accent, else "#RRGGBB"
_zoom = "standard"


def resolve_system_dark() -> bool:
    """Best-effort read of the OS appearance (Qt 6.5+); False if unavailable."""
    try:
        hints = QGuiApplication.styleHints()
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def resolve_mode(pref: str) -> str:
    """pref: 'light' | 'dark' | 'black' | 'system' -> resolved
    'light'/'dark'/'black' ('system' only ever follows light/dark)."""
    if pref in ("light", "dark", "black"):
        return pref
    return "dark" if resolve_system_dark() else "light"


def current_mode() -> str:
    return _mode


def default_accent() -> str:
    """The active theme's own accent colour (what the "Default" choice in
    Settings means), whatever custom accent is currently applied."""
    return {"dark": DARK, "black": BLACK}.get(_mode, LIGHT)["accent"]


def fs(size: float) -> int:
    """`size` (a font size in the units the stylesheet uses) at the current
    zoom -- every hardcoded font size in the app goes through this so the
    Settings zoom reaches all of them, not just the global stylesheet."""
    regular, title = _ZOOM_DELTAS.get(_zoom, (0, 0))
    return max(6, round(size + (title if size >= TITLE_MIN_SIZE else regular)))


# Row/padding density per zoom level: Small also tightens row heights and
# vertical padding (a 12 px font in a 46 px sidebar row looks lost), Large
# keeps the spacing it has and lets the bigger text fill it.
_DENSITY = {"small": 0.7, "standard": 1.0, "large": 1.0}


def sp(px: float) -> int:
    """`px` of row height / vertical padding at the current zoom (see
    _DENSITY) -- what the sidebar rows, table rows, buttons and inputs
    use so Small zoom shrinks the *rows*, not just the text in them."""
    return round(px * _DENSITY.get(_zoom, 1.0))


# A table's row height is max(its header's default section size, the item's
# own height) -- so the QSS padding scaling above alone can't shrink a row
# below Qt's stock 30 px. Every QTableWidget here is built in Python, so
# wrap its constructor once to give Small zoom a tighter default row.
_table_init = QTableWidget.__init__


def _dense_table_init(self, *args, **kwargs) -> None:
    _table_init(self, *args, **kwargs)
    if _zoom == "small":
        self.verticalHeader().setDefaultSectionSize(sp(30))


QTableWidget.__init__ = _dense_table_init


def zoom_extra(step: float) -> int:
    """`step` px of extra room per positive font-size step of the current
    zoom (0 at Standard/Small) -- for fixed-size boxes that text now grows
    inside."""
    return round(step * max(0, _ZOOM_DELTAS.get(_zoom, (0, 0))[0]))


def set_theme(pref: str, accent: str = "", zoom: str = "standard") -> str:
    """Resolve `pref` and update COLORS in place (recolouring its accent
    entries if `accent` is a "#RRGGBB"), and set the zoom `fs()` reads.
    Returns the resolved mode."""
    global _mode, _accent, _zoom
    _mode = resolve_mode(pref)
    _accent = accent if QColor(accent).isValid() and accent else ""
    _zoom = zoom if zoom in ZOOM_LEVELS else "standard"
    base = {"dark": DARK, "black": BLACK}.get(_mode, LIGHT)
    COLORS.clear()
    COLORS.update(base)
    if _accent:
        COLORS.update(_accent_tokens(base, _accent, dark=_mode != "light"))
    return _mode


def apply_theme(app, pref: str, accent: str = "", zoom: str = "standard") -> str:
    """set_theme() + push the resulting QSS onto the running QApplication."""
    mode = set_theme(pref, accent, zoom)
    app.setStyleSheet(build_qss())
    return mode


def add_shadow(widget, blur: int = 24, dy: int = 8, alpha: int = 28) -> None:
    r, g, b = COLORS["shadow"]
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(dy)
    effect.setColor(QColor(r, g, b, alpha))
    widget.setGraphicsEffect(effect)


def svg_pixmap(name: str, color: str | QColor = "#4c4c4c",
               size: int | None = None) -> QPixmap:
    """Load an SVG from assets/svgs and tint it a flat colour (SourceIn)."""
    path = SVGS / (name if name.endswith(".svg") else f"{name}.svg")
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return pixmap
    if size:
        pixmap = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(color))
    painter.end()
    return tinted


def svg_icon(name: str, color: str | QColor = "#4c4c4c", size: int = 24) -> QIcon:
    return QIcon(svg_pixmap(name, color, size))


def build_qss() -> str:
    c = COLORS
    return f"""
    QWidget {{
        color: {c['text']};
        font-size: {fs(14)}px;
    }}
    QWidget#root {{ background: {c['bg']}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollArea {{ border: none; }}

    /* ---------------- sidebar ---------------- */
    QFrame#sidebar {{
        background: {c['card']};
        border: none;
    }}
    QLabel#brand {{
        font-size: {fs(19)}px; font-weight: 800; color: {c['text']};
        padding: 4px 6px;
    }}
    QLabel#brandDot {{ color: {c['accent']}; }}
    QLabel#sectionLabel {{
        color: {c['faint']}; font-size: {fs(11)}px; font-weight: 700;
        letter-spacing: 1px; padding: {sp(4)}px 8px;
    }}
    QPushButton#navBtn {{
        text-align: left; border: none; border-radius: 12px;
        padding: {sp(10)}px 12px; font-size: {fs(14)}px; font-weight: 600;
        color: {c['muted']}; background: transparent;
    }}
    QPushButton#navBtn:hover {{ background: {c['bg']}; }}
    QPushButton#navBtn:checked {{
        background: {c['accent_soft']}; color: {c['accent']}; font-weight: 700;
    }}
    QLabel#navEmpty {{ color: {c['faint']}; font-size: {fs(12)}px; padding: {sp(6)}px 10px; }}
    QLabel#navMeta {{ color: {c['faint']}; font-size: {fs(11)}px; font-weight: 700; }}

    /* ---------------- cards ---------------- */
    QFrame#card {{
        background: {c['card']};
        border-radius: 18px;
        border: 1px solid {c['card_border']};
    }}
    QLabel#cardTitle {{ color: {c['muted']}; font-size: {fs(13)}px; font-weight: 600; }}
    QLabel#statValue {{ color: {c['text']}; font-size: {fs(26)}px; font-weight: 800; }}
    QLabel#statSub {{ color: {c['faint']}; font-size: {fs(12)}px; }}

    QLabel#pageTitle {{ font-size: {fs(24)}px; font-weight: 800; color: {c['text']}; }}
    QLabel#pageSub {{ color: {c['muted']}; font-size: {fs(13)}px; }}
    QLabel#sectionTitle {{ font-size: {fs(15)}px; font-weight: 700; color: {c['text']}; }}
    QLabel#hint {{ color: {c['muted']}; font-size: {fs(12)}px; }}
    QLabel#status {{ color: {c['muted']}; font-size: {fs(12)}px; }}

    /* ---------------- inputs ---------------- */
    QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {{
        background: {c['card']}; border: 1px solid {c['line']};
        border-radius: 10px; padding: {sp(7)}px 10px; selection-background-color: {c['accent_soft']};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {c['card']}; border: 1px solid {c['line']};
        selection-background-color: {c['accent_soft']}; selection-color: {c['accent']};
        outline: none;
    }}

    /* ---------------- buttons ---------------- */
    QPushButton {{
        background: {c['card']}; border: 1px solid {c['line']};
        border-radius: 10px; padding: {sp(8)}px 14px; font-weight: 600; color: {c['text']};
    }}
    QPushButton:hover {{ background: {c['bg']}; }}
    QPushButton:disabled {{ color: {c['faint']}; }}
    QPushButton#primary {{
        background: {c['accent']}; color: {c['on_accent']}; border: none; font-weight: 700;
    }}
    QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
    QPushButton#primary:disabled {{
        background: {c['accent_disabled']}; color: {c['accent_disabled_text']};
    }}
    QPushButton#ghost {{ border: none; background: transparent; color: {c['muted']}; }}
    QPushButton#ghost:hover {{ color: {c['accent']}; }}
    QPushButton#ghost:checked {{
        color: {c['accent']}; background: {c['accent_soft']}; border-radius: 8px;
    }}

    /* ---------------- table ---------------- */
    QTableWidget {{
        background: {c['card']}; border: none; gridline-color: transparent;
        selection-background-color: {c['accent_soft']}; selection-color: {c['text']};
    }}
    QHeaderView::section {{
        background: {c['card']}; color: {c['muted']}; border: none;
        border-bottom: 1px solid {c['line']}; padding: {sp(8)}px 6px;
        font-weight: 700; font-size: {fs(12)}px;
    }}
    QTableWidget::item {{ padding: {sp(6)}px; border-bottom: 1px solid {c['line']}; }}

    /* Mentions view's per-column "Summary" stats table -- deliberately as
       quiet as the "hint"-styled Posts/Names Found lines above it, not a
       full-size data table, since it's a plain key/value recap. */
    QTableWidget#statsTable {{ font-size: {fs(12)}px; }}
    QTableWidget#statsTable::item {{
        padding: {sp(3)}px 6px; border-bottom: none; color: {c['muted']};
    }}

    /* Mentions view's per-column post-texts/names-found tables -- same
       small size as the stats table above (and the "hint"-styled
       Posts/Names Found lines each sits under), just keeping their own row
       divider since, unlike Summary, these are genuine sortable data
       tables, not a quiet key/value recap. */
    QTableWidget#mentionsColumnTable {{ font-size: {fs(12)}px; }}
    QTableWidget#mentionsColumnTable::item {{ padding: {sp(4)}px 6px; }}

    QProgressBar {{
        background: {c['bg']}; border: none; border-radius: 6px;
        height: 8px; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}

    QPlainTextEdit {{
        background: {c['bg']}; border: 1px solid {c['line']}; border-radius: 10px;
        color: {c['muted']}; font-family: "SF Mono","Menlo",monospace; font-size: {fs(12)}px;
    }}
    QGroupBox {{
        border: 1px solid {c['line']}; border-radius: 12px; margin-top: 10px;
        font-weight: 700; padding-top: 6px;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {c['muted']}; }}

    QMenuBar {{ background: {c['card']}; }}
    QMenuBar::item:selected {{ background: {c['accent_soft']}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['scrollbar']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
