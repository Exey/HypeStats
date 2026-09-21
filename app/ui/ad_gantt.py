"""Ad-campaign timeline: one row per ad placement on a shared day axis, a
block's width being how long the placement runs (24h … a month). Native
QPainter, like app.ui.charts — no third-party chart library.

A block is coloured by its channel's prime-era state (see
app.ad_campaign.prime_profile) and labelled with the channel and price;
when the block is too narrow to hold the label (a 24h ad on a month-long
axis), the label sits beside it instead. Hovering shows the caller's
tooltip, clicking emits `block_clicked` so the view can offer a replacement
channel — see app.ui.ad_campaign_view.
"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from .theme import COLORS, fs

# prime-era state -> COLORS key
STATE_COLOR_KEYS = {
    "prime": "good", "warm": "accent", "steady": "muted",
    "cooling": "warn", "unknown": "scrollbar",
}

_PAD_L, _PAD_R, _PAD_B = 14, 14, 12
_AXIS_H = 44
_ROW_H, _ROW_GAP = 30, 6
_MIN_BLOCK_W = 8
_TICK_STEPS = (1, 2, 7, 14, 30, 60, 90, 180, 365)
_LABEL_GAP = 8   # min space between two axis labels


def state_color(state: str) -> QColor:
    return QColor(COLORS[STATE_COLOR_KEYS.get(state, "scrollbar")])


class AdGanttChart(QWidget):
    block_clicked = Signal(int, QPoint)   # block index, global click position

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._blocks: list[dict] = []
        self._start = date.today()
        self._days = 1
        self._label_fn = lambda b: b.get("label", "")
        self._price_fn = lambda b: str(b.get("price", ""))
        self._tooltip_fn = lambda b: ""
        self._day_fn = lambda d: d.strftime("%d %b")
        self._weekday_fn = lambda d: d.strftime("%a")
        self._empty_text = ""
        self._rects: list[QRectF] = []
        self._hover: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(120)

    # -------------------------------------------------------------- data
    def set_callbacks(self, *, label, price, tooltip, day, weekday) -> None:
        """Formatting hooks, so this widget stays free of i18n: `label(block)`
        and `price(block)` build the block text, `tooltip(block)` the hover
        text, `day(date)` / `weekday(date)` the axis labels."""
        self._label_fn, self._price_fn, self._tooltip_fn = label, price, tooltip
        self._day_fn, self._weekday_fn = day, weekday

    def set_plan(self, blocks: list[dict], window_start: date, window_days: int,
                 empty_text: str = "") -> None:
        self._blocks = list(blocks)
        self._start = window_start
        self._days = max(1, window_days)
        self._empty_text = empty_text
        self._hover = None
        rows = max(1, len(self._blocks))
        self.setFixedHeight(_AXIS_H + rows * (_ROW_H + _ROW_GAP) + _PAD_B)
        self.update()

    # ---------------------------------------------------------- geometry
    def _plot(self) -> tuple[float, float]:
        left = _PAD_L
        return left, max(1.0, self.width() - _PAD_R - left)

    def _row_top(self, index: int) -> float:
        return _AXIS_H + index * (_ROW_H + _ROW_GAP)

    def _block_rect(self, index: int) -> QRectF:
        left, width = self._plot()
        day_w = width / self._days
        b = self._blocks[index]
        off = (b["start"] - self._start).days
        x = left + off * day_w
        w = max(_MIN_BLOCK_W, b["days"] * day_w - 2)
        return QRectF(x + 1, self._row_top(index), w, _ROW_H)

    def _block_at(self, pos) -> int | None:
        for i, rect in enumerate(self._rects):
            if rect.contains(pos):
                return i
        return None

    # ------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, width = self._plot()
        day_w = width / self._days
        font = QFont(self.font())
        font.setPixelSize(fs(11))
        p.setFont(font)
        fm = QFontMetrics(font)
        height = self.height()

        if not self._blocks:
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(QRectF(0, 0, self.width(), height),
                       Qt.AlignmentFlag.AlignCenter, self._empty_text)
            self._rects = []
            return

        # Weekend shading + day gridlines.
        for d in range(self._days):
            day = self._start + timedelta(d)
            x = left + d * day_w
            if day.weekday() >= 5:
                shade = QColor(COLORS["accent_track"])
                p.fillRect(QRectF(x, _AXIS_H - 6, day_w, height - _AXIS_H + 6 - _PAD_B + 6), shade)
        p.setPen(QPen(QColor(COLORS["line"]), 1))
        for d in range(self._days + 1):
            x = left + d * day_w
            p.drawLine(QPointF(x, _AXIS_H - 6), QPointF(x, height - _PAD_B + 6))

        # Axis: a label every `step` days — the smallest step whose labels
        # don't run into each other at this width, font and language.
        label_w = max(fm.horizontalAdvance(self._day_fn(self._start + timedelta(d)))
                      for d in range(min(self._days, 31)))
        step = next((s for s in _TICK_STEPS if day_w * s >= label_w + _LABEL_GAP),
                    _TICK_STEPS[-1])
        for d in range(0, self._days, step):
            day = self._start + timedelta(d)
            cx = left + (d + (0.5 if step == 1 else 0)) * day_w
            top = self._weekday_fn(day) if step == 1 else ""
            bottom = self._day_fn(day)
            p.setPen(QColor(COLORS["faint"] if day.weekday() >= 5 and step == 1
                            else COLORS["muted"]))
            for text, y in ((top, 6), (bottom, 22)):
                if not text:
                    continue
                tw = fm.horizontalAdvance(text)
                x = cx - tw / 2 if step == 1 else cx
                p.drawText(QPointF(max(2.0, min(x, self.width() - tw - 2)), y + fm.ascent()), text)

        # Blocks.
        self._rects = []
        for i, b in enumerate(self._blocks):
            rect = self._block_rect(i)
            self._rects.append(rect)
            color = state_color(b["prime"]["state"])
            fill = QColor(color)
            fill.setAlphaF(1.0 if i == self._hover else 0.86)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(rect, 6, 6)
            if i == self._hover:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(COLORS["text"]), 1.5))
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

            text = f"{self._label_fn(b)} · {self._price_fn(b)}"
            inner_w = rect.width() - 12
            text_w = fm.horizontalAdvance(text)
            baseline = rect.top() + (rect.height() + fm.ascent() - fm.descent()) / 2
            if text_w <= inner_w:
                p.setPen(QColor("#FFFFFF") if fill.lightness() < 150 else QColor("#12203A"))
                p.drawText(QPointF(rect.left() + 6, baseline), text)
            else:
                # Beside the block: right of it if it fits, else left of it.
                p.setPen(QColor(COLORS["text"]))
                if rect.right() + 8 + text_w <= self.width() - _PAD_R:
                    p.drawText(QPointF(rect.right() + 8, baseline), text)
                else:
                    p.drawText(QPointF(max(2.0, rect.left() - 8 - text_w), baseline), text)

    # ------------------------------------------------------------ events
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        pos = event.position() if hasattr(event, "position") else event.pos()
        idx = self._block_at(pos)
        if idx != self._hover:
            self._hover = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx is not None
                           else Qt.CursorShape.ArrowCursor)
            self.update()
        if idx is None:
            QToolTip.hideText()
        else:
            gpos = (event.globalPosition().toPoint() if hasattr(event, "globalPosition")
                    else event.globalPos())
            QToolTip.showText(gpos, self._tooltip_fn(self._blocks[idx]), self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        QToolTip.hideText()
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position() if hasattr(event, "position") else event.pos()
            idx = self._block_at(pos)
            if idx is not None:
                gpos = (event.globalPosition().toPoint() if hasattr(event, "globalPosition")
                        else event.globalPos())
                QToolTip.hideText()
                self.block_clicked.emit(idx, gpos)
                return
        super().mousePressEvent(event)
