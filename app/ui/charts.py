"""Native, QPainter-drawn charts — no matplotlib, no QtCharts.

Widgets in here:

* BarChart      — the dashboard-style bar chart: a faint full-height "track"
                  behind every column, a rounded gradient value bar on top,
                  light y-gridlines with labels, and (thinned) x labels.
* Sparkline     — a small trend line with a soft gradient fill, for stat cards.
* MultiLineChart — several count-valued trend lines sharing one x-axis.
* GaugeDial     — a read-only circular gauge (QDial subclass) colored red to
                  green by value; used for the Content Quality Index cards.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QDial, QSizePolicy, QToolTip, QWidget

from .theme import COLORS, fs


def _nice_ceiling(value: float) -> int:
    """Round an axis maximum up to a friendly 1/2/5 × 10ⁿ number."""
    if value <= 0:
        return 1
    import math
    exp = math.floor(math.log10(value))
    base = 10 ** exp
    for mult in (1, 2, 2.5, 5, 10):
        if value <= mult * base:
            return int(mult * base) if mult * base >= 1 else 1
    return int(10 * base)


class BarChart(QWidget):
    PAD_L, PAD_R, PAD_T, PAD_B = 44, 8, 14, 26

    def __init__(self, values=None, labels=None, accent: str | None = None,
                 max_labels: int = 14, value_fmt=str, tooltips=None,
                 parent=None) -> None:
        super().__init__(parent)
        self._values = list(values or [])
        self._labels = list(labels or [])
        self._tooltips = list(tooltips or [])
        self._accent = accent or COLORS["accent"]
        self._max_labels = max_labels
        self._value_fmt = value_fmt
        self._empty_text = "No data"
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)

    def set_data(self, values, labels=None, accent: str | None = None,
                 empty_text: str | None = None, tooltips=None) -> None:
        self._values = list(values or [])
        if labels is not None:
            self._labels = list(labels)
        if tooltips is not None:
            self._tooltips = list(tooltips)
        elif labels is not None:
            self._tooltips = []
        if accent:
            self._accent = accent
        if empty_text is not None:
            self._empty_text = empty_text
        self.update()

    def _plot_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        return QRectF(self.PAD_L, self.PAD_T,
                      w - self.PAD_L - self.PAD_R, h - self.PAD_T - self.PAD_B)

    # ------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pad_l = self.PAD_L
        plot = self._plot_rect()

        if not self._values or max(self._values) <= 0:
            p.setPen(QColor(COLORS["faint"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return

        top = _nice_ceiling(max(self._values))

        # y gridlines + labels
        p.setFont(QFont(self.font().family(), fs(9)))
        grid_pen = QPen(QColor(COLORS["line"]))
        grid_pen.setWidthF(1.0)
        steps = 4
        for i in range(steps + 1):
            frac = i / steps
            y = plot.bottom() - frac * plot.height()
            p.setPen(grid_pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(COLORS["faint"]))
            p.drawText(QRectF(0, y - 8, pad_l - 8, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self._short_num(round(top * frac)))

        n = len(self._values)
        slot = plot.width() / n
        bar_w = min(34.0, slot * 0.6)
        radius = min(7.0, bar_w / 2)
        max_val = max(self._values)
        label_every = max(1, round(n / self._max_labels))

        for i, val in enumerate(self._values):
            cx = plot.left() + slot * (i + 0.5)
            x = cx - bar_w / 2
            # track
            track = QRectF(x, plot.top(), bar_w, plot.height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLORS["accent_track"]))
            p.drawRoundedRect(track, radius, radius)
            # value bar
            bh = plot.height() * (val / top) if top else 0
            if bh > 0:
                bar = QRectF(x, plot.bottom() - bh, bar_w, bh)
                grad = QLinearGradient(bar.topLeft(), bar.bottomLeft())
                hot = val == max_val
                bar_gradient = (COLORS["bar_from"], COLORS["bar_to"])
                c0, c1 = (bar_gradient if not hot else (self._accent, self._accent))
                grad.setColorAt(0, QColor(self._accent if hot else c0))
                grad.setColorAt(1, QColor(c1))
                p.setBrush(QBrush(grad))
                p.drawRoundedRect(bar, radius, radius)

            if i % label_every == 0 and i < len(self._labels):
                p.setPen(QColor(COLORS["faint"]))
                p.setFont(QFont(self.font().family(), fs(9)))
                p.drawText(QRectF(cx - slot / 2, plot.bottom() + 4, slot, 18),
                           Qt.AlignmentFlag.AlignCenter, str(self._labels[i]))
        p.end()

    # ---------------------------------------------------------- tooltip
    def mouseMoveEvent(self, event) -> None:
        idx = self._bar_at(event.position() if hasattr(event, "position")
                           else event.pos())
        if idx is None:
            QToolTip.hideText()
        else:
            pos = (event.globalPosition().toPoint() if hasattr(event, "globalPosition")
                   else event.globalPos())
            QToolTip.showText(pos, self._tooltip_text(idx), self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

    def _bar_at(self, pos) -> int | None:
        if not self._values:
            return None
        plot = self._plot_rect()
        x, y = pos.x(), pos.y()
        if x < plot.left() or x > plot.right() or y < plot.top() or y > plot.bottom():
            return None
        n = len(self._values)
        slot = plot.width() / n
        return min(n - 1, max(0, int((x - plot.left()) // slot))) if slot else None

    def _tooltip_text(self, idx: int) -> str:
        if idx < len(self._tooltips) and self._tooltips[idx]:
            return self._tooltips[idx]
        label = self._labels[idx] if idx < len(self._labels) else ""
        value = self._value_fmt(self._values[idx])
        return f"{label}: {value}" if label else str(value)

    @staticmethod
    def _short_num(v: float) -> str:
        v = float(v)
        if v >= 1_000_000:
            return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
        if v >= 1_000:
            return f"{v / 1_000:.1f}k".replace(".0k", "k")
        return str(int(v))


class Sparkline(QWidget):
    def __init__(self, values=None, accent: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._values = list(values or [])
        self._accent = accent or COLORS["accent"]
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, values, accent: str | None = None) -> None:
        self._values = list(values or [])
        if accent:
            self._accent = accent
        self.update()

    def paintEvent(self, _event) -> None:
        vals = self._values
        if len(vals) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad = 2.0
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        n = len(vals)

        def pt(i: int) -> QPointF:
            x = pad + (w - 2 * pad) * (i / (n - 1))
            y = (h - pad) - (h - 2 * pad) * ((vals[i] - lo) / span)
            return QPointF(x, y)

        line = QPainterPath(pt(0))
        for i in range(1, n):
            line.lineTo(pt(i))

        fill = QPainterPath(QPointF(pad, h - pad))
        fill.lineTo(pt(0))
        for i in range(1, n):
            fill.lineTo(pt(i))
        fill.lineTo(QPointF(w - pad, h - pad))
        fill.closeSubpath()

        grad = QLinearGradient(0, 0, 0, h)
        col = QColor(self._accent)
        grad.setColorAt(0, QColor(col.red(), col.green(), col.blue(), 60))
        grad.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
        p.fillPath(fill, QBrush(grad))

        pen = QPen(col)
        pen.setWidthF(2.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(line)
        p.end()


class MultiLineChart(QWidget):
    """Several count-valued trend lines over one shared x-axis, e.g.
    views/reactions/shares over time.

    Two scaling modes, since "fair" means different things depending on
    what the series represent:

    * shared_scale=True — one y-axis (with real numeric gridlines) for every
      series. Use this when the series are the *same metric* for different
      subjects (e.g. views for several channels) — a shared axis is what
      makes them comparable at a glance.
    * shared_scale=False (default) — each series is positioned against its
      *own* max, with no shared numeric axis (gridlines are unlabeled) but
      every point labeled with its real count. Use this when the series are
      *different metrics* that legitimately live on different scales (e.g.
      views vs. reactions vs. shares for one channel) — a shared axis would
      flatten the smaller ones into an unreadable line hugging zero.
    """
    PAD_R, PAD_B = 14, 26

    def __init__(self, series=None, labels=None, max_labels: int = 999,
                 value_fmt=None, shared_scale: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._series = series or []   # [{"label", "color", "values": [...]}]
        self._labels = list(labels or [])
        self._max_labels = max_labels
        self._value_fmt = value_fmt or self._short_num
        self._shared_scale = shared_scale
        self._empty_text = "No data"
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)

    def set_data(self, series, labels=None, empty_text: str | None = None,
                shared_scale: bool | None = None) -> None:
        self._series = series or []
        if labels is not None:
            self._labels = list(labels)
        if empty_text is not None:
            self._empty_text = empty_text
        if shared_scale is not None:
            self._shared_scale = shared_scale
        self.update()

    @property
    def _pad_l(self) -> float:
        return 46 if self._shared_scale else 14   # room for numeric axis labels

    @property
    def _pad_t(self) -> float:
        return 28 if self._shared_scale else 14   # headroom for the topmost point's label

    def _plot_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        pad_l, pad_t = self._pad_l, self._pad_t
        return QRectF(pad_l, pad_t, w - pad_l - self.PAD_R, h - pad_t - self.PAD_B)

    def _all_values(self) -> list[float]:
        vals: list[float] = []
        for s in self._series:
            vals.extend(s.get("values") or [])
        return vals

    # ------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot_rect()

        n = len(self._labels)
        vals = self._all_values()
        if n < 2 or not vals or max(vals) <= 0:
            p.setPen(QColor(COLORS["faint"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return

        # Reserve headroom above each line's own peak so its value label
        # never gets clipped by the top edge (shared_scale reserves the same
        # headroom via a taller PAD_T instead, so the axis numbers stay
        # exact round values).
        top_pad_frac = 0.14
        shared_top = _nice_ceiling(max(vals)) if self._shared_scale else None

        grid_pen = QPen(QColor(COLORS["line"]))
        grid_pen.setWidthF(1.0)
        for i in range(5):
            frac = i / 4
            y = plot.bottom() - frac * plot.height()
            p.setPen(grid_pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            if shared_top is not None:
                p.setPen(QColor(COLORS["faint"]))
                p.setFont(QFont(self.font().family(), fs(9)))
                p.drawText(QRectF(0, y - 8, self._pad_l - 8, 16),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           self._value_fmt(round(shared_top * frac)))

        def x_at(i: int) -> float:
            return plot.left() + (plot.width() * i / (n - 1)) if n > 1 else plot.center().x()

        label_every = max(1, round(n / self._max_labels))
        for i, lab in enumerate(self._labels):
            if i % label_every:
                continue
            p.setPen(QColor(COLORS["faint"]))
            p.setFont(QFont(self.font().family(), fs(9)))
            p.drawText(QRectF(x_at(i) - 34, plot.bottom() + 4, 68, 18),
                       Qt.AlignmentFlag.AlignCenter, str(lab))

        label_font = QFont(self.font().family(), fs(8))
        for s in self._series:
            values = s.get("values") or []
            if len(values) != n:
                continue
            series_top = shared_top if shared_top is not None else (max(values) or 1) * (1 + top_pad_frac)

            def y_at(v: float, _top=series_top) -> float:
                return plot.bottom() - plot.height() * (v / _top) if _top else plot.bottom()

            color = QColor(s.get("color") or COLORS["accent"])
            pts = [QPointF(x_at(i), y_at(v)) for i, v in enumerate(values)]

            path = QPainterPath(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            pen = QPen(color)
            pen.setWidthF(2.2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

            p.setBrush(QBrush(color))
            p.setFont(label_font)
            for i, pt in enumerate(pts):
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(pt, 2.6, 2.6)
                v = values[i]
                if v <= 0:
                    continue
                p.setPen(color)
                p.drawText(QRectF(pt.x() - 26, pt.y() - 17, 52, 14),
                           Qt.AlignmentFlag.AlignCenter, self._value_fmt(v))
        p.end()

    # ---------------------------------------------------------- tooltip
    def mouseMoveEvent(self, event) -> None:
        idx = self._index_at(event.position() if hasattr(event, "position")
                             else event.pos())
        if idx is None:
            QToolTip.hideText()
        else:
            pos = (event.globalPosition().toPoint() if hasattr(event, "globalPosition")
                   else event.globalPos())
            QToolTip.showText(pos, self._tooltip_text(idx), self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

    def _index_at(self, pos) -> int | None:
        n = len(self._labels)
        if n < 2:
            return None
        plot = self._plot_rect()
        x = pos.x()
        if x < plot.left() - 12 or x > plot.right() + 12:
            return None
        frac = (x - plot.left()) / plot.width() if plot.width() else 0
        return min(n - 1, max(0, round(frac * (n - 1))))

    def _tooltip_text(self, idx: int) -> str:
        lines = []
        if idx < len(self._labels):
            lines.append(str(self._labels[idx]))
        for s in self._series:
            values = s.get("values") or []
            if idx < len(values):
                lines.append(f"{s.get('label', '')}: {self._value_fmt(values[idx])}")
        return "\n".join(lines)

    @staticmethod
    def _short_num(v) -> str:
        v = float(v)
        if v >= 1_000_000:
            return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
        if v >= 1_000:
            return f"{v / 1_000:.1f}k".replace(".0k", "k")
        return str(int(v))


class GaugeDial(QDial):
    """A read-only circular gauge: a 270° arc, colored red (low) through
    amber to green (high) by value, with the numeric value centered inside.

    QDial's own QSS support is unreliable for this kind of colored-arc look
    across platforms (macOS in particular tends to ignore it), so this
    overrides paintEvent entirely and hand-draws the arc with QPainter —
    the same approach the other chart widgets in this module use. It's
    still a real QDial (as opposed to a plain QWidget) so setRange/setValue
    keep working as expected; mouse/wheel/key events are swallowed so it
    can't be dragged out of sync with the score it's displaying.
    """
    ARC_SPAN = 270          # degrees the gauge sweeps
    ARC_START = 225         # degrees, Qt convention: 0 = 3 o'clock, CCW+

    def __init__(self, minimum: int = 0, maximum: int = 1000, parent=None) -> None:
        super().__init__(parent)
        self.setRange(minimum, maximum)
        self.setNotchesVisible(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------- display-only
    def mousePressEvent(self, event) -> None:
        event.ignore()

    def mouseMoveEvent(self, event) -> None:
        event.ignore()

    def wheelEvent(self, event) -> None:
        event.ignore()

    def keyPressEvent(self, event) -> None:
        event.ignore()

    # ------------------------------------------------------------- color
    @staticmethod
    def _color_for(frac: float) -> QColor:
        """Red -> amber -> green as frac goes 0 -> 1."""
        frac = max(0.0, min(1.0, frac))
        stops = [QColor(COLORS["hot"]), QColor(COLORS["warn"]), QColor(COLORS["good"])]
        seg = frac * (len(stops) - 1)
        i = min(int(seg), len(stops) - 2)
        t = seg - i
        c1, c2 = stops[i], stops[i + 1]
        return QColor(
            int(c1.red() + (c2.red() - c1.red()) * t),
            int(c1.green() + (c2.green() - c1.green()) * t),
            int(c1.blue() + (c2.blue() - c1.blue()) * t),
        )

    # ------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        pen_w = max(4.0, side * 0.09)
        pad = pen_w / 2 + 3
        rect = QRectF((self.width() - side) / 2 + pad, (self.height() - side) / 2 + pad,
                      side - 2 * pad, side - 2 * pad)

        span = self.maximum() - self.minimum()
        frac = (self.value() - self.minimum()) / span if span else 0.0

        track_pen = QPen(QColor(COLORS["accent_track"]))
        track_pen.setWidthF(pen_w)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track_pen)
        p.drawArc(rect, self.ARC_START * 16, -self.ARC_SPAN * 16)

        if frac > 0:
            val_pen = QPen(self._color_for(frac))
            val_pen.setWidthF(pen_w)
            val_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(val_pen)
            p.drawArc(rect, self.ARC_START * 16, -int(self.ARC_SPAN * 16 * frac))

        p.setPen(QColor(COLORS["text"]))
        f = QFont(self.font().family(), fs(max(9, int(side * 0.16))), QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(int(round(self.value()))))
        p.end()
