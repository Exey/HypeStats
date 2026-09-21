"""Side-by-side comparison of 2-8 fetched channels.

Reuses the same StatCard tiles as the single-channel dashboard, laid out as
one column per channel so the numbers line up for an easy diff. Selection
happens from the sidebar's compare mode (see SidePanel.compare_requested).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QStackedLayout, QVBoxLayout, QWidget,
)

from ...errors import friendly_os_error
from ..dashboard_view import fmt_int, short_num
from ..theme import COLORS, fs
from ..widgets import StatCard, hline

# (card key, i18n key) — order here is display order top-to-bottom.
_METRICS = [
    ("members", "stat_members"),
    ("avg_views", "stat_avg_views"),
    ("err_pct", "stat_err_pct"),
    ("max_views", "cmp_max_views"),
    ("views_last_year", "cmp_view_repost_year"),
    ("posts_per_day", "cmp_posts_per_day"),
    ("reposts_per_post", "stat_reposts_per_post"),
    ("erv_pct", "cmp_erv_pct"),
    ("max_reposts", "cmp_max_reposts"),
    ("avg_reactions", "cmp_avg_reactions"),
    ("virality_index", "cmp_virality_index"),
    ("viral_post_share", "cmp_viral_share"),
]

# card key -> i18n key for an explanatory tooltip (only the two least
# self-explanatory cards need one).
_TOOLTIPS = {
    "virality_index": "cmp_virality_index_tip",
    "viral_post_share": "cmp_viral_share_tip",
    "erv_pct": "cmp_erv_pct_tip",
}

_CARD_HEIGHT = round(132 * 0.6 * 0.9 * 0.9)  # -40%, then -10%, then another -10%
MAX_COMPARE = 8
LAST_FULL_YEAR = datetime.now().year - 1


class CompareView(QWidget):
    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    def _metric_title(self, key: str, title_key: str) -> str:
        title = self.tr_(title_key)
        if key == "err_pct":
            title = f"{title} ({self.tr_('stat_err_pct_sub')})"
        elif key == "views_last_year":
            title = self.tr_(title_key, year=LAST_FULL_YEAR)
        return title

    def retranslate(self) -> None:
        self.md_btn.setText(self.tr_("save_md_button"))
        self.md_btn.setToolTip(self.tr_("nav_compare_md_hint"))
        for key, title_key in _METRICS:
            title = self._metric_title(key, title_key)
            tip = self.tr_(_TOOLTIPS[key]) if key in _TOOLTIPS else ""
            for col in self._columns:
                col["cards"][key].title_lbl.setText(title)
                col["cards"][key].setToolTip(tip)
        for col in self._columns:
            col["section_lbl"].setText(self.tr_("compare_content_quality"))

    def _build_ui(self) -> None:
        # Root is a StackAll QStackedLayout — same overlay technique as the
        # per-column crown emoji below — so the "Save MD" button can float
        # over the top-right corner without becoming part of (or disturbing)
        # the real content layout at all.
        root_stack = QStackedLayout(self)
        root_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(34, 16, 40, 24)
        outer.setSpacing(16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body = QWidget()
        columns = QHBoxLayout(body)
        columns.setContentsMargins(0, 6, 8, 0)
        columns.setSpacing(9)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._columns: list[dict] = []
        for _ in range(MAX_COMPARE):
            col_lay = QVBoxLayout()
            col_lay.setSpacing(14)

            # Crown floats centered over the header as a true overlay (both
            # widgets share the same rect) instead of sitting inline next to
            # the name text.
            header_widget = QWidget()
            header_stack = QStackedLayout(header_widget)
            header_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

            name_block = QWidget()
            name_block_lay = QVBoxLayout(name_block)
            name_block_lay.setContentsMargins(0, 16, 0, 0)  # headroom for the crown
            name_block_lay.setSpacing(2)
            name_lbl = QLabel("—")
            name_lbl.setObjectName("sectionTitle")
            name_block_lay.addWidget(name_lbl)
            username_lbl = QLabel("")
            username_lbl.setObjectName("hint")
            username_lbl.setVisible(False)
            name_block_lay.addWidget(username_lbl)
            header_stack.addWidget(name_block)

            crown_lbl = QLabel("👑")
            crown_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            crown_lbl.setStyleSheet(f"font-size: {fs(20)}px;")
            crown_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            crown_lbl.setVisible(False)
            header_stack.addWidget(crown_lbl)

            col_lay.addWidget(header_widget)
            cards = {}
            section_lbl = QLabel(self.tr_("compare_content_quality"))
            section_lbl.setObjectName("smallCapsTitle")
            section_lbl.setStyleSheet(
                f"color: {COLORS['faint']}; font-size: {fs(11)}px; font-weight: 700; "
                f"letter-spacing: 1.5px; padding-top: 4px;")
            for key, title_key in _METRICS:
                title = self._metric_title(key, title_key)
                card = StatCard(title)
                card.setMinimumHeight(_CARD_HEIGHT)
                card.set_compact(True)
                if key in _TOOLTIPS:
                    card.setToolTip(self.tr_(_TOOLTIPS[key]))
                cards[key] = card
                col_lay.addWidget(card)
                if key == "posts_per_day":
                    col_lay.addWidget(hline())
                    col_lay.addWidget(section_lbl)
            col_lay.addStretch()
            holder = QWidget()
            holder.setLayout(col_lay)
            holder.setMinimumWidth(200)
            columns.addWidget(holder, 1)
            self._columns.append({"holder": holder, "name": name_lbl,
                                  "crown": crown_lbl, "username": username_lbl,
                                  "cards": cards, "section_lbl": section_lbl})

        root_stack.addWidget(content)

        # Overlay layer: WA_TransparentForMouseEvents on the *container*
        # (not the button — a child widget still gets its own clicks
        # regardless of its parent's transparency) so clicks/scrolling on
        # the real content underneath keep working everywhere outside the
        # button's own small rect.
        overlay = QWidget()
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay_lay = QVBoxLayout(overlay)
        overlay_lay.setContentsMargins(0, 12, 20, 0)
        overlay_lay.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.md_btn = QPushButton(self.tr_("save_md_button"))
        self.md_btn.setToolTip(self.tr_("nav_compare_md_hint"))
        self.md_btn.clicked.connect(self.save_markdown)
        overlay_lay.addWidget(self.md_btn)
        root_stack.addWidget(overlay)

    def load(self, datas: list[dict]) -> None:
        datas = datas[:MAX_COMPARE]
        raw: list[dict] = []
        names: list[str] = []
        for i, col in enumerate(self._columns):
            if i >= len(datas):
                col["holder"].setVisible(False)
                continue
            col["holder"].setVisible(True)
            data = datas[i]
            info = data.get("info", {})
            stats = data.get("stats", {})
            members = info.get("members", 0) or 0
            avg_views = stats.get("avg_views", 0) or 0
            avg_reposts = stats.get("avg_reposts", 0) or 0
            avg_reactions = stats.get("avg_reactions", 0) or 0
            # Older checkpoints predate this stat entirely (None) — fall back
            # to the overall average rather than showing a false 0%.
            avg_views_settled = stats.get("avg_views_settled")
            if avg_views_settled is None:
                avg_views_settled = avg_views
            views_ly = stats.get("last_year_views", 0) or 0
            max_views = stats.get("max_views", 0) or 0
            vals = {
                "members": members,
                "avg_views": avg_views,
                "max_views": max_views,
                "posts_per_day": stats.get("avg_posts_per_day", 0) or 0,
                "avg_reactions": avg_reactions,
                "max_reposts": stats.get("max_reposts", 0) or 0,
                "reposts_per_post": avg_reposts,
                "erv_pct": ((avg_reactions + avg_reposts) / avg_views * 100) if avg_views else 0,
                "err_pct": (avg_views_settled / members * 100) if members else 0,
                "views_last_year": views_ly,
                "virality_index": (max_views / avg_views) if avg_views else 0,
                "viral_post_share": stats.get("viral_post_share", 0) or 0,
            }
            raw.append(vals)
            name = data.get("title") or data.get("channel") or "—"
            names.append(name)
            col["name"].setText(name)
            username = data.get("username") or ""
            if username:
                subtitle = f"@{username}"
            else:
                chan_id = info.get("id") or 0
                subtitle = f"-100{chan_id}" if chan_id else ""
            col["username"].setText(subtitle)
            col["username"].setVisible(bool(subtitle))
            col["cards"]["members"].set_value(fmt_int(vals["members"]) if vals["members"] else "—")
            col["cards"]["avg_views"].set_value(fmt_int(round(vals["avg_views"])))
            col["cards"]["max_views"].set_value(fmt_int(vals["max_views"]))
            col["cards"]["posts_per_day"].set_value(str(vals["posts_per_day"]))
            col["cards"]["avg_reactions"].set_value(fmt_int(round(vals["avg_reactions"])))
            col["cards"]["max_reposts"].set_value(fmt_int(vals["max_reposts"]))
            col["cards"]["reposts_per_post"].set_value(fmt_int(round(vals["reposts_per_post"])))
            col["cards"]["erv_pct"].set_value(f"{vals['erv_pct']:.1f}%")
            col["cards"]["err_pct"].set_value(f"{vals['err_pct']:.1f}%")
            col["cards"]["views_last_year"].set_value(
                short_num(views_ly, 2) if views_ly else "—")
            col["cards"]["virality_index"].set_value(f"{vals['virality_index']:.2f}×")
            col["cards"]["viral_post_share"].set_value(f"{vals['viral_post_share']:.1f}%")

        shown = self._columns[:len(raw)]
        win_counts = [0] * len(shown)
        for key, _ in _METRICS:
            values = [v[key] for v in raw]
            top = max(values) if values else None
            winner = values.index(top) if top is not None and top > 0 and values.count(top) == 1 else None
            # Every column tied at the minimum gets painted, not just one —
            # e.g. if two channels both sit at 0.0%, both get marked.
            bottom = min(values) if len(values) > 1 else None
            for i, col in enumerate(shown):
                is_win = i == winner
                col["cards"][key].set_highlighted(is_win)
                col["cards"][key].set_lowest(bottom is not None and values[i] == bottom)
                if is_win:
                    win_counts[i] += 1

        # Every column tied for the most won metrics gets a crown — could be
        # more than one if two channels are equally dominant.
        best = max(win_counts) if win_counts else 0
        for i, col in enumerate(shown):
            col["name"].setText(names[i])
            col["crown"].setVisible(best > 0 and win_counts[i] == best)

    # ------------------------------------------------------------- export
    def build_markdown(self) -> str:
        shown = [col for col in self._columns if col["holder"].isVisible()]
        if not shown:
            return ""
        headers = [self.tr_("compare_md_metric")] + [col["name"].text() for col in shown]
        lines = ["| " + " | ".join(headers) + " |",
                 "| " + " | ".join(["---"] * len(headers)) + " |"]
        for key, _ in _METRICS:
            row = [shown[0]["cards"][key].title_lbl.text()]
            row += [col["cards"][key].value_lbl.text() for col in shown]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
        return "\n".join(lines) + "\n"

    def save_markdown(self) -> None:
        md = self.build_markdown()
        if not md:
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("report_empty"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("save_md_button"), "compare.md", "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"), self.tr_("md_saved", path=path))