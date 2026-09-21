"""Reusable card widgets and the sidebar nav button.

Everything here is a thin QFrame/QPushButton styled by theme.build_qss() plus
a soft drop shadow, so the dashboard reads as one system the way the
analytics_dashboard cards do.
"""
from __future__ import annotations

from PySide6.QtCore import QUrl, QEvent, Qt
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget,
)

from ..mentions import tg_deep_link
from ..scoring import GAUGE_MAX
from .charts import GaugeDial, Sparkline
from .theme import COLORS, add_shadow, fs, sp, svg_pixmap, zoom_extra


def open_external_link(url: str) -> None:
    """Every link this app opens externally (a post, a channel, a name's
    own Telegram/web link) should go through this rather than calling
    QDesktopServices.openUrl directly, so a t.me link opens straight in
    the Telegram app (see app.mentions.tg_deep_link) instead of a browser
    tab, app-wide -- not just in whichever view happened to be fixed
    first."""
    QDesktopServices.openUrl(QUrl(tg_deep_link(url)))

POST_CARD_WIDTH = 195    # 130 * 1.5
POST_CARD_HEIGHT = 191   # 225 * 0.85 (see PostCard)
POST_CARD_THUMB_HEIGHT = 63   # 74 * 0.85
POST_CARD_GAUGE_SIZE = 49     # 58 * 0.85
POST_CARD_PLACEHOLDER_PIXEL_SIZE = 52   # 🏞️/▶️/⚪️ font-size on a card with no cached thumb
POST_CARD_TEXT_PIXEL_SIZE = 10
POST_CARD_TEXT_LINES = 2
POST_CARD_TEXT_WIDTH = POST_CARD_WIDTH - 20   # card's own left+right content margins


def post_card_height() -> int:
    """POST_CARD_HEIGHT, plus room for the card's text growing at a Large
    zoom (see theme.zoom_extra) -- its width stays put, its height is what
    the name/caption lines have to grow into."""
    return POST_CARD_HEIGHT + zoom_extra(6)

# media_type (see channel_stat.py) -> placeholder icon shown until a real
# thumbnail is fetched; "" is both a genuine text-only post and an older
# checkpoint that predates this field — either way, 📖 is the reasonable
# default rather than leaving the card's thumb area blank.
POST_CARD_PLACEHOLDERS = {
    "photo": "🏞️", "video": "▶️", "video_note": "⚪️",
    "audio": "🎙️", "file": "💾", "": "📖",
}
_MEDIA_COUNT_ORDER = ["photo", "video", "video_note", "audio", "file"]


def format_media_counts(media_counts: dict) -> str:
    """"×7🏞️" for a 7-photo album, "×2🏞️ ×2▶️" for a mixed one, "×1▶️"
    for a single video — shown even at ×1 because a real cached thumbnail
    is just a static frame either way, so it's the only on-card indicator
    of which media type it actually is (a placeholder emoji alone already
    makes that obvious, but a real thumbnail doesn't)."""
    parts = [f"×{media_counts[mt]}{POST_CARD_PLACEHOLDERS[mt]}"
             for mt in _MEDIA_COUNT_ORDER if media_counts.get(mt, 0) >= 1]
    return " ".join(parts)


def elide_to_lines(text: str, width: int, max_lines: int, pixel_size: int) -> str:
    """Word-wrap `text` to at most `max_lines` lines that fit `width` px at
    `pixel_size`, appending "…" if it had to cut content short — QLabel's
    own elideMode only elides a *single* line, it can't do this after N
    wrapped lines, so this measures and breaks the text by hand.

    Builds a fresh QFont for measurement (rather than trusting a live
    widget's .font()) because this app's global QSS sets font-size in px,
    which makes an unpolished widget's QFont report no usable point/pixel
    size yet — same issue worked through for the Content Quality Index
    metrics line."""
    text = " ".join(text.split())
    if not text:
        return ""
    font = QFont()
    font.setPixelSize(fs(pixel_size))
    fm = QFontMetrics(font)

    words = text.split(" ")
    lines: list[str] = []
    i = 0
    while i < len(words) and len(lines) < max_lines:
        line = words[i]
        i += 1
        while i < len(words):
            trial = f"{line} {words[i]}"
            if fm.horizontalAdvance(trial) > width:
                break
            line = trial
            i += 1
        lines.append(line)

    if i < len(words):  # ran out of lines before running out of words
        last = lines[-1] if lines else ""
        while last and fm.horizontalAdvance(last + "…") > width:
            last = last[:-1].rstrip()
        lines[-1] = f"{last}…" if last else "…"
    return "\n".join(lines)


def folder_icon(color: str) -> QIcon:
    """Small filled dot used to represent a folder's color in menus/buttons."""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, 12, 12)
    painter.end()
    return QIcon(pixmap)


class Card(QFrame):
    """White rounded panel with a soft shadow."""

    def __init__(self, parent=None, shadow: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        if shadow:
            add_shadow(self)


class PostCard(Card):
    """A single post: title, thumbnail (if cached), a 2-line text preview,
    and a gauge + absolute counts row. The whole card opens the post on
    click — every child is mouse-transparent so nothing (least of all the
    otherwise-interactive-looking GaugeDial) can swallow that click.

    Shared by the High-Quality Posts view (one card per post, across many
    channels — see app.ui.content_quality_view) and the per-channel
    Dashboard's "recent posts" row (see app.ui.dashboard_view) — kept here
    rather than in either view so neither has to import UI code from the
    other to reuse it. Scoring itself lives in app.scoring for the same
    reason."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(POST_CARD_WIDTH, post_card_height())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 7)
        lay.setSpacing(5)

        self.name_lbl = QLabel("—")
        self.name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_lbl.setWordWrap(False)
        self.name_lbl.setObjectName("hint")
        self.name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.name_lbl)

        # Placeholder emoji (🏞️/▶️/⚪️) when there's no cached thumbnail yet —
        # setStyleSheet, not setFont(): theme.py's global `QWidget
        # {font-size: 14px}` rule outranks a plain setFont() once this
        # widget is polished/shown (see the equivalent problem worked
        # through for the Content Quality Index cards' metrics line).
        self.thumb_lbl = QLabel()
        self.thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_lbl.setFixedHeight(POST_CARD_THUMB_HEIGHT)
        self.thumb_lbl.setStyleSheet(f"font-size: {fs(POST_CARD_PLACEHOLDER_PIXEL_SIZE)}px;")
        self.thumb_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.thumb_lbl)

        # Album media-count badge (e.g. "×7🏞️", "×2🏞️ ×2▶️") — a child
        # layout on thumb_lbl itself rather than a sibling, so it overlays
        # the thumbnail/placeholder instead of taking its own row; QLabel's
        # own pixmap/text paint independently of any child widgets placed
        # on it via a layout, so this works whether the card is showing a
        # real cached thumbnail or a placeholder emoji.
        overlay_lay = QHBoxLayout(self.thumb_lbl)
        overlay_lay.setContentsMargins(0, 0, 6, 0)
        overlay_lay.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.media_overlay_lbl = QLabel()
        self.media_overlay_lbl.setStyleSheet(
            "background: rgba(0, 0, 0, 150); color: white; border-radius: 4px; "
            f"padding: 1px 5px; font-size: {fs(10)}px; font-weight: 600;")
        self.media_overlay_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.media_overlay_lbl.setVisible(False)
        overlay_lay.addWidget(self.media_overlay_lbl)

        # Text is pre-wrapped to exactly 2 lines with a trailing "…" by
        # elide_to_lines (QLabel can't natively elide after N *wrapped*
        # lines, only a single line) — wordWrap off since the line breaks
        # are already embedded in the string.
        self.text_lbl = QLabel("")
        self.text_lbl.setWordWrap(False)
        self.text_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_lbl.setObjectName("hint")
        self.text_lbl.setStyleSheet(f"font-size: {fs(POST_CARD_TEXT_PIXEL_SIZE)}px;")
        self.text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.text_lbl)

        lay.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.gauge = GaugeDial(0, GAUGE_MAX)
        self.gauge.setFixedSize(POST_CARD_GAUGE_SIZE, POST_CARD_GAUGE_SIZE)
        self.gauge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        bottom.addWidget(self.gauge)

        self.counts_lbl = QLabel("")
        self.counts_lbl.setWordWrap(True)
        self.counts_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        bottom.addWidget(self.counts_lbl, 1)
        lay.addLayout(bottom)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._link:
            open_external_link(self._link)
        super().mousePressEvent(event)

    def set_data(self, label: str, thumb: QPixmap | None, placeholder: str, text: str,
                gauge_value: float, counts_text: str, link: str, tooltip: str,
                media_counts: dict | None = None) -> None:
        self.name_lbl.setText(label)
        if thumb is not None and not thumb.isNull():
            self.thumb_lbl.setPixmap(thumb)
        elif placeholder:
            self.thumb_lbl.setText(placeholder)
        else:
            self.thumb_lbl.clear()
        overlay_text = format_media_counts(media_counts or {})
        self.media_overlay_lbl.setText(overlay_text)
        self.media_overlay_lbl.setVisible(bool(overlay_text))
        self.text_lbl.setText(text)
        self.gauge.setValue(max(0, min(GAUGE_MAX, round(gauge_value))))
        self.counts_lbl.setText(counts_text)
        self._link = link
        self.setToolTip(tooltip)


class StatCard(Card):
    """Small KPI tile: title, big value, optional sub-line and sparkline."""

    def __init__(self, title: str, value: str = "—", sub: str = "",
                 spark=None, accent: str | None = None, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)

        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("cardTitle")
        lay.addWidget(self.title_lbl)

        self.value_lbl = QLabel(value)
        self.value_lbl.setObjectName("statValue")
        lay.addWidget(self.value_lbl)

        self.sub_lbl = QLabel(sub)
        self.sub_lbl.setObjectName("statSub")
        self.sub_lbl.setVisible(bool(sub))
        lay.addWidget(self.sub_lbl)

        self.spark = Sparkline(spark or [], accent=accent or COLORS["accent"])
        self.spark.setVisible(bool(spark))
        lay.addWidget(self.spark)

        # Hidden until set_action() gives it something to do — e.g. the
        # Dashboard's Ethics card, whose value isn't always cached yet
        # (see app.ui.dashboard_view._update_ethics_card): "Calculate"
        # lives right here instead of a separate control elsewhere on the
        # page, since it's specific to this one card's own value.
        self.action_btn = QPushButton()
        self.action_btn.setStyleSheet(f"padding: 2px 8px; font-size: {fs(11)}px;")
        self.action_btn.setVisible(False)
        lay.addWidget(self.action_btn)

        lay.addStretch()
        self.setMinimumHeight(132)

        self._compact = False
        self._winner = False
        self._lowest = False

    def set_value(self, value: str, sub: str = "", spark=None) -> None:
        self.value_lbl.setText(value)
        self.sub_lbl.setText(sub)
        self.sub_lbl.setVisible(bool(sub))
        if spark is not None:
            self.spark.set_data(spark)
            self.spark.setVisible(bool(spark))

    def set_action(self, text: str, on_click) -> None:
        """Shows action_btn with `text`, calling `on_click` (no args) when
        clicked — replaces whatever action was connected before, so this
        is safe to call again (e.g. to relabel it "Calculating…" and
        disable it for the duration, see DashboardView._on_calculate_ethics)."""
        self.action_btn.setText(text)
        try:
            self.action_btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass  # nothing connected yet
        self.action_btn.clicked.connect(on_click)
        self.action_btn.setVisible(True)

    def clear_action(self) -> None:
        self.action_btn.setVisible(False)

    def set_compact(self, on: bool = True) -> None:
        """Tighter padding + smaller type — used by compare mode, where each
        card is much shorter than the dashboard's full-size tiles."""
        self._compact = on
        if on:
            self.layout().setContentsMargins(14, 6, 14, 6)
            self.layout().setSpacing(2)
        else:
            self.layout().setContentsMargins(18, 16, 18, 16)
            self.layout().setSpacing(6)
        self._apply_label_style()

    def _apply_label_style(self) -> None:
        # title_lbl/value_lbl each need at most one local stylesheet, so
        # compact sizing and the lowest-value text color are combined here
        # rather than in set_compact()/set_lowest() directly (setStyleSheet
        # replaces the whole local sheet, it doesn't merge across calls).
        title = f"font-size: {fs(11)}px;" if self._compact else ""
        value = f"font-size: {fs(18)}px;" if self._compact else ""
        if self._lowest:
            title += " color: #D9B8DE;"
            value += " color: #FFFFFF;"
        self.title_lbl.setStyleSheet(title)
        self.value_lbl.setStyleSheet(value)

    def set_highlighted(self, on: bool) -> None:
        """Gold border — used by compare mode to mark the winning metric."""
        self._winner = on
        self._apply_card_style()

    def set_lowest(self, on: bool) -> None:
        """Dark fill — used by compare mode to mark the worst metric."""
        self._lowest = on
        self._apply_label_style()
        self._apply_card_style()

    def _apply_card_style(self) -> None:
        rules = []
        if self._lowest:
            rules.append("background: #241D34;")
        if self._winner:
            rules.append(f"border: 2px solid {COLORS['win']};")
        self.setStyleSheet(f"QFrame#card {{ {' '.join(rules)} }}" if rules else "")


class ChartCard(Card):
    """A titled card that hosts a chart (or any content widget). `title_row`
    is exposed so callers can addWidget() extra controls (e.g. a checkbox)
    to the right of the title — it already ends in a stretch, so anything
    added lands flush right."""

    def __init__(self, title: str, content: QWidget, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        self.title_row = QHBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("sectionTitle")
        self.title_row.addWidget(self.title_lbl)
        self.title_row.addStretch(1)
        lay.addLayout(self.title_row)
        lay.addWidget(content, 1)
        self.content = content

    def set_title(self, title: str) -> None:
        self.title_lbl.setText(title)


class SectionCard(Card):
    """A titled card with a vertical body layout callers fill in. `title_row`
    is exposed so callers can addWidget() extra controls (e.g. a checkbox)
    to the right of the title — it already ends in a stretch, so anything
    added lands flush right."""

    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(12)
        self.title_row = QHBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("sectionTitle")
        self.title_row.addWidget(self.title_lbl)
        self.title_row.addStretch(1)
        self.body.addLayout(self.title_row)


class NavButton(QPushButton):
    """Sidebar entry: recolorable SVG icon + label, checkable & exclusive.

    icon_name=None skips the SVG icon column entirely — for entries whose
    label already carries its own emoji (e.g. "📁 Folder Stats") and don't
    want a second, redundant icon on top of it.

    compact=True is for a half-width pill sharing a row with another one: a
    short, centred label with tighter margins and no meta column (an empty
    meta label would still cost a layout-spacing gap).
    """

    def __init__(self, icon_name: str | None, text: str, parent=None,
                 compact: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("navBtn")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name
        self._badge_text: str | None = None
        self._badge_color: str | None = None
        self._badge_tooltip: str | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(*((6, 0, 6, 0) if compact else (12, 0, 12, 0)))
        lay.setSpacing(0 if compact else 12)
        self._icon = QLabel()
        self._icon.setFixedSize(22, 22)
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if icon_name:
            lay.addWidget(self._icon)
        else:
            self._icon.setVisible(False)
        self._label = QLabel(text)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if compact:
            self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._label, 1)

        self._meta = QLabel()
        self._meta.setObjectName("navMeta")
        self._meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._meta.setVisible(not compact)
        lay.addWidget(self._meta)

        self.setMinimumHeight(sp(46))
        self.toggled.connect(self._sync_icon)
        self._sync_icon(False)

    def _sync_icon(self, checked: bool) -> None:
        if not self._icon_name:
            return
        if self._badge_text:
            # A folder/tag abbreviation takes over the icon slot entirely —
            # plain colored text, no SVG underneath it.
            self._icon.setPixmap(QPixmap())
            self._icon.setText(self._badge_text)
            self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._icon.setStyleSheet(
                f"color: {self._badge_color}; font-size: {fs(9)}px; font-weight: 800;")
            return
        self._icon.setText("")
        self._icon.setStyleSheet("")
        color = COLORS["accent"] if checked else COLORS["muted"]
        self._icon.setPixmap(svg_pixmap(self._icon_name, color, 20))

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def set_meta(self, text: str) -> None:
        self._meta.setText(text)

    def set_badge(self, text: str, color: str, tooltip: str = "") -> None:
        """2-3 letter colored abbreviation replacing the plain SVG icon —
        used by the sidebar channel list for a folder/tag abbreviation (see
        SidePanel.refresh_badges) once a channel has one assigned."""
        self._badge_text = text
        self._badge_color = color
        self._badge_tooltip = tooltip
        self._sync_icon(self.isChecked())

    def clear_badge(self) -> None:
        self._badge_text = None
        self._badge_tooltip = None
        self._sync_icon(self.isChecked())

    def event(self, e) -> bool:
        # Hovering the icon shows what it's an abbreviation of, distinct
        # from the button's own tooltip (channel/title) covering the rest.
        if (e.type() == QEvent.Type.ToolTip and self._badge_tooltip
                and self._icon.geometry().contains(e.pos())):
            QToolTip.showText(e.globalPos(), self._badge_tooltip, self)
            return True
        return super().event(e)


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{COLORS['line']}; border:none;")
    return line
