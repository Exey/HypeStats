"""Mutual PR (ad-swap) view: every tracked channel in one sortable table —
followers, an estimated ad-post follower-gain forecast at five horizons
(24h highlighted, since it's the number most ad-swap decisions hinge on),
a "Repeated after Month" column estimating a second/reminder post placed
a month after the first (app.scoring_pr.repeated_post_forecast), and the
least-crowded weekdays to post one, each with a rate showing how much
better that day scores than an average day for the channel. Meant to help
decide which channels are actually worth trading ad posts with.

Recent content quality ("Interest" in app.scoring_pr) isn't shown as its
own column, but still feeds the forecast/best-days math under the hood —
see channel_interest's weight in follow_conversion_rate and best_days.

All figures beyond Followers are estimates built from app.scoring_pr, which
documents exactly which parts are measured (this app's own quality formula)
versus assumed heuristics (the view-accumulation curve and the ad
conversion rate) — see that module's docstring. mutual_pr_hint surfaces the
same caveat in the UI itself, since this table is meant to inform deals with
other people's channels, not just describe your own.

Above the main table sits a **Channel links** card: which tracked channels
already link to which others, and how often — app.cross_mentions' whole-base
scan of every channel's own stored `all_links` (any t.me link a post's
caption carries, not just Telegram's own "public forward" stat), matched
against ChannelStore's own roster of tracked channels rather than
mentions.md's roster of people. Cached (app.cross_mentions.
cache_cross_mentions) so the table paints instantly from whatever was last
calculated; a Calculate/Recalculate button reruns it — cheap enough (pure
link matching, no NER, no Telegram calls) to run synchronously on click
rather than through a background worker. Who already promotes a channel is
exactly the signal a Mutual PR decision should see first, hence its
position above the forecast table.

Below the main table sits one more card: **MPR Pairs** — the top channel
pairs ranked by app.scoring_pr.rank_mutual_pr_pairs (size/engagement/timing/
niche compatibility; the math lives there, not here — niche is tag-first,
with a shared folder counting for much less, so cross-folder same-tag pairs
surface), whose "best days" column shows each side's own best days plus a ★
for the days that suit both at once (mutual_best_days). Both this and
Channel links are scoped to whatever the folder filter is showing.

The Markdown export (_build_md) keeps the main forecast table byte-for-byte
and *appends* just the MPR Pairs table ("## Пары ВП") — no Channel links
table, no blurb.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..activity import channel_activity_trend
from ..cross_mentions import (
    cache_cross_mentions, compute_cross_channel_mentions, load_cross_mentions_cache,
    rank_targets,
)
from ..errors import friendly_os_error
from ..folders import FolderStore
from ..mentions import MentionsStore
from ..rating import activity_trend_penalty
from ..scoring_pr import (
    ad_forecast, ad_forecast_range, best_days, channel_interest,
    rank_mutual_pr_pairs, repeated_post_forecast,
)
from ..store import ChannelStore
from .dashboard_view import fmt_int, short_num
from .theme import COLORS
from .widgets import SectionCard, hline, open_external_link

# Channel links table columns (see _links_card/_rebuild_links_table).
_LINKS_COL_CHANNEL = 0
_LINKS_COL_BY = 1
_LINKS_COL_COUNT = 2
_LINKS_COL_TOP = 3

# Forecast columns between 24h and Best Days, in table order.
_FORECAST_COLS = ["48h", "72h", "week", "month"]
_WD_KEYS = ["wd_mon", "wd_tue", "wd_wed", "wd_thu", "wd_fri", "wd_sat", "wd_sun"]
# weekday index 0=Mon..6=Sun -> emoji, grouped Mon-Tue / Wed-Thu / Fri / Sat-Sun.
_WD_EMOJI = ["🚀", "🚀", "⚖️", "⚖️", "🎉", "☀️", "☀️"]
_TITLE_MAX_CHARS = 24
_TITLE_COL_WIDTH = 170
_FOLLOWERS_COL = 0
_TITLE_COL = 1
_COL_24H = 2   # tinted — see _render_table
_FORECAST_START_COL = 3   # 48h onward, see _FORECAST_COLS
_TINTED_COL = _COL_24H
_BEST_DAYS_COL = 7
_BEST_DAYS_COL_WIDTH = 228   # 20% wider than the original 190, on request
_COL_REPEATED = 8   # last column, after Best Days, on request
_REPEATED_COL_WIDTH = 160
_FORECAST_COL_WIDTH = 120   # 20% wider than Qt's 100px default, on request
_FORECAST_TABLE_COLS = (_COL_24H, 3, 4, 5, 6)

# col index -> sortable (Best days isn't a single scalar).
_SORTABLE_COLS = {0, 1, 2, 3, 4, 5, 6, 8}

# MPR Pairs card column layout (index: №=0, Channel A=1, Channel B=2,
# Rating=3, Best days=4, Forecast=5). Channel A/B stay Stretch — widening
# the two fixed columns below is what shrinks them ~20%, on request.
_PAIRS_RATING_COL_WIDTH = 62
_PAIRS_DAYS_COL_WIDTH = 260   # +30%, and holds the new "★both · A · B" text
_PAIRS_FORECAST_COL_WIDTH = 156  # +30% over the old ~120


def _channel_label(ch: dict) -> str:
    username = ch.get("username") or ""
    if username:
        return f"@{username}"
    return ch.get("title") or ch.get("channel") or ch.get("key", "?")


def _truncate(text: str, limit: int = _TITLE_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_rate_pct(rate: float) -> str:
    return f"{round((rate - 1) * 100):+d}%"


class MutualPrView(QWidget):
    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                tag_store=None, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self.tag_store = tag_store
        self._entries: list[dict] = []
        self._rendered_entries: list[dict] = []
        self._channel_by_key: dict[str, dict] = {}   # see _reload_entries
        self._sort_col = _TINTED_COL   # 24h forecast — see _render_table
        self._sort_desc = True
        self._links_sort_col = _LINKS_COL_COUNT
        self._links_sort_desc = True
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.page_scroll)

        page_holder = QWidget()
        page = QVBoxLayout(page_holder)
        page.setContentsMargins(34, 28, 40, 24)
        page.setSpacing(16)

        # A plain top row (title/subtitle column + the button, both real
        # siblings in one layout) rather than a floating overlay — a
        # StackAll QStackedLayout overlay (CompareView's "Save MD"
        # technique) turned out to visually paint on top but not actually
        # receive clicks: verified with a real simulated click, not just a
        # screenshot, and CompareView's own Save MD button has the exact
        # same latent bug. This inline layout has no competing Z-order to
        # get wrong.
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header = QVBoxLayout()
        header.setSpacing(2)
        self.title_lbl = QLabel(self.tr_("nav_mutual_pr"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(self.tr_("mutual_pr_sub"))
        self.sub_lbl.setObjectName("pageSub")
        header.addWidget(self.sub_lbl)
        header_row.addLayout(header, 1)
        self.md_btn = QPushButton(self.tr_("save_md_button"))
        self.md_btn.clicked.connect(self._save_md)
        header_row.addWidget(self.md_btn, 0, Qt.AlignmentFlag.AlignTop)
        page.addLayout(header_row)

        self.hint_lbl = QLabel(self.tr_("mutual_pr_hint"))
        self.hint_lbl.setObjectName("hint")
        self.hint_lbl.setWordWrap(True)
        page.addWidget(self.hint_lbl)

        pick_row = QHBoxLayout()
        self.pick_lbl = QLabel(self.tr_("mutual_pr_pick_folder"))
        pick_row.addWidget(self.pick_lbl)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        pick_row.addWidget(self.folder_combo, 1)
        page.addLayout(pick_row)
        page.addWidget(hline())

        self.empty_lbl = QLabel(self.tr_("mutual_pr_empty"))
        self.empty_lbl.setObjectName("navEmpty")
        self.empty_lbl.setWordWrap(True)
        page.addWidget(self.empty_lbl)

        page.addWidget(self._links_card())
        page.addWidget(self._table_card(), 100)
        page.addWidget(self._pairs_card())
        page.addStretch(1)

        self.page_scroll.setWidget(page_holder)

    def _table_card(self) -> SectionCard:
        card = SectionCard(self.tr_("nav_mutual_pr"))
        self.table_card_ref = card

        self.table = QTableWidget(0, 9)
        self._set_headers()
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        # Fixed rather than Stretch — a Stretch title column claimed all the
        # leftover width once Interest/2-Weeks were dropped, crowding out
        # the other columns; a title beyond _TITLE_MAX_CHARS is elided with
        # a tooltip instead (see _render_table).
        header.setSectionResizeMode(_TITLE_COL, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(_TITLE_COL, _TITLE_COL_WIDTH)
        self.table.setColumnWidth(_BEST_DAYS_COL, _BEST_DAYS_COL_WIDTH)
        self.table.setColumnWidth(_COL_REPEATED, _REPEATED_COL_WIDTH)
        for col in _FORECAST_TABLE_COLS:
            self.table.setColumnWidth(col, _FORECAST_COL_WIDTH)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # No internal scrollbar: the table grows to fit every row (see
        # _sync_table_height) and the page-level scroll area handles the
        # rest, same as FolderStatView's period table.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        card.body.addWidget(self.table, 1)
        return card

    def _set_headers(self) -> None:
        self.table.setHorizontalHeaderLabels([
            self.tr_("col_followers"), self.tr_("col_channel_title"),
            self.tr_("col_forecast_24h"), self.tr_("col_forecast_48h"),
            self.tr_("col_forecast_72h"), self.tr_("col_forecast_week"),
            self.tr_("col_forecast_month"), self.tr_("col_best_days"),
            self.tr_("col_repeated_after_month")])

    def _links_card(self) -> SectionCard:
        """Which tracked channels already link to which others — see the
        module docstring and app.cross_mentions. A Calculate/Recalculate
        button (_on_calculate_links_clicked) runs the whole-base scan;
        _rebuild_links_table paints from whatever's cached without
        recomputing anything, so opening this view is instant."""
        card = SectionCard(self.tr_("mutual_pr_links_title"))
        self.links_card_ref = card

        self.links_hint_lbl = QLabel(self.tr_("mutual_pr_links_hint"))
        self.links_hint_lbl.setObjectName("hint")
        self.links_hint_lbl.setWordWrap(True)
        card.body.addWidget(self.links_hint_lbl)

        calc_row = QHBoxLayout()
        self.links_calc_btn = QPushButton(self.tr_("mutual_pr_links_calc_btn"))
        self.links_calc_btn.clicked.connect(self._on_calculate_links_clicked)
        calc_row.addWidget(self.links_calc_btn, 0)
        self.links_calculated_lbl = QLabel("")
        self.links_calculated_lbl.setObjectName("hint")
        calc_row.addWidget(self.links_calculated_lbl, 1)
        card.body.addLayout(calc_row)

        self.links_empty_lbl = QLabel(self.tr_("mutual_pr_links_empty"))
        self.links_empty_lbl.setObjectName("hint")
        card.body.addWidget(self.links_empty_lbl)

        self.links_table = QTableWidget(0, 4)
        self._set_links_headers()
        self.links_table.verticalHeader().setVisible(False)
        self.links_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.links_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        links_header = self.links_table.horizontalHeader()
        links_header.setSectionResizeMode(_LINKS_COL_CHANNEL, QHeaderView.ResizeMode.Stretch)
        links_header.setSectionResizeMode(_LINKS_COL_TOP, QHeaderView.ResizeMode.Stretch)
        links_header.setSectionsClickable(True)
        links_header.sectionClicked.connect(self._on_links_header_clicked)
        self.links_table.cellDoubleClicked.connect(self._open_links_cell)
        self.links_table.setMaximumHeight(280)
        card.body.addWidget(self.links_table)
        return card

    def _set_links_headers(self) -> None:
        self.links_table.setHorizontalHeaderLabels([
            self.tr_("mutual_pr_links_col_channel"), self.tr_("mutual_pr_links_col_by"),
            self.tr_("mutual_pr_links_col_count"), self.tr_("mutual_pr_links_col_top_source")])

    def _pairs_card(self) -> SectionCard:
        """Top channel *pairs* ranked for an ad swap — see
        app.scoring_pr.rank_mutual_pr_pairs. Ranked over exactly the
        channels the folder filter is currently showing. Double-click a
        channel cell to open it."""
        card = SectionCard(self.tr_("mutual_pr_partners_title"))
        self.pairs_card_ref = card

        self.pairs_hint_lbl = QLabel(self.tr_("mutual_pr_partners_hint"))
        self.pairs_hint_lbl.setObjectName("hint")
        self.pairs_hint_lbl.setWordWrap(True)
        card.body.addWidget(self.pairs_hint_lbl)

        self.pairs_empty_lbl = QLabel(self.tr_("mutual_pr_partners_empty"))
        self.pairs_empty_lbl.setObjectName("hint")
        card.body.addWidget(self.pairs_empty_lbl)

        self.pairs_table = QTableWidget(0, 6)
        self._set_pairs_headers()
        self.pairs_table.verticalHeader().setVisible(False)
        self.pairs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pairs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        pairs_header = self.pairs_table.horizontalHeader()
        pairs_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Channel A
        pairs_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Channel B
        self.pairs_table.setColumnWidth(0, 40)
        self.pairs_table.setColumnWidth(3, _PAIRS_RATING_COL_WIDTH)
        self.pairs_table.setColumnWidth(4, _PAIRS_DAYS_COL_WIDTH)
        self.pairs_table.setColumnWidth(5, _PAIRS_FORECAST_COL_WIDTH)
        self.pairs_table.cellDoubleClicked.connect(self._open_pair_channel)
        self.pairs_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        card.body.addWidget(self.pairs_table)
        return card

    def _set_pairs_headers(self) -> None:
        self.pairs_table.setHorizontalHeaderLabels([
            "№", self.tr_("mutual_pr_partners_col_a"),
            self.tr_("mutual_pr_partners_col_b"), self.tr_("col_rating"),
            self.tr_("mutual_pr_partners_col_days"),
            self.tr_("mutual_pr_partners_col_forecast")])

    # --------------------------------------------------------------- data
    def refresh(self) -> None:
        """Reload folders + recompute every channel's Mutual PR figures from
        disk. Call whenever the view is shown, or folders/channels changed
        elsewhere."""
        current_id = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem(self.tr_("mutual_pr_all_channels"), None)
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        self.folder_combo.blockSignals(False)
        idx = self.folder_combo.findData(current_id) if current_id else 0
        self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._reload_entries()

    def _on_folder_changed(self, _index: int) -> None:
        self._render_table()

    def _reload_entries(self) -> None:
        """Compute once per channel — header-click sorting only re-sorts
        this cached list, never recomputes it (see _render_table)."""
        self._entries = []
        self._channel_by_key = {}
        for summary in self.channel_store.list():
            data = self.channel_store.load(summary["key"])
            if not data:
                continue
            data.setdefault("key", summary["key"])
            self._channel_by_key[data["key"]] = data
            stats = data.get("stats", {}) or {}
            avg_views = float(stats.get("avg_views", 0) or 0)
            avg_views_settled = float(stats.get("avg_views_settled", 0) or 0)
            avg_posts_per_day = float(stats.get("avg_posts_per_day", 0) or 0)
            total_posts = int(stats.get("total_posts", 0) or 0)
            followers = int(data.get("info", {}).get("members", 0) or 0)
            viral_post_share = float(stats.get("viral_post_share", 0) or 0)
            weekday_counts = data.get("distributions", {}).get("weekday") or [0] * 7
            rows = data.get("rows", []) or []
            interest = channel_interest(rows, avg_views)
            # A channel that's slowing/stalling/abandoned (see
            # app.activity.channel_activity_trend) still forecasts off its
            # old avg_views_settled otherwise — exactly what put a winding-
            # down channel at the top of this table's default 24h sort.
            trend = channel_activity_trend((data.get("distributions") or {}).get("monthly"))
            forecast = ad_forecast(avg_views_settled, interest, avg_posts_per_day,
                                   total_posts, followers, viral_post_share, rows,
                                   activity_trend=trend)
            tag = (self.tag_store.tag_for_channel(data["key"])
                   if self.tag_store is not None else None)
            self._entries.append({
                "channel": data,
                "folder_id": self.folder_store.folder_for_channel(data["key"]),
                "tag": tag,
                "followers": followers,
                "activity_trend": trend,
                "forecast": forecast,
                "forecast_range": ad_forecast_range(forecast),
                "repeated": repeated_post_forecast(forecast["24h"], avg_posts_per_day),
                "best_days": best_days(weekday_counts, interest),
                # Full 7-day ranking — feeds mutual_best_days (the ★ days in
                # the MPR Pairs "best days" column); the top-2 above still
                # drives the day-overlap score component.
                "best_days_full": best_days(weekday_counts, interest, top_n=7),
            })
        self._render_table()

    def _visible_entries(self) -> list[dict]:
        folder_id = self.folder_combo.currentData()
        if folder_id is None:
            return self._entries
        return [e for e in self._entries if e["folder_id"] == folder_id]

    def _pair_channels(self) -> list[dict]:
        """The currently-visible entries reshaped for
        app.scoring_pr.rank_mutual_pr_pairs, carrying the display fields
        (label/link/followers) through so the ranked result can render
        without a second lookup."""
        out = []
        for e in self._visible_entries():
            ch = e["channel"]
            out.append({
                "key": ch.get("key"),
                "label": _channel_label(ch),
                "link": ch.get("link") or "",
                "followers": e["followers"],
                "forecast": e["forecast"],
                "best_days": e["best_days"],
                "best_days_full": e["best_days_full"],
                "folder_id": e["folder_id"],
                "tag": e["tag"],
            })
        return out

    def _sort_value(self, entry: dict, col: int):
        if col == _FOLLOWERS_COL:
            return entry["followers"]
        if col == _TITLE_COL:
            return _channel_label(entry["channel"]).lower()
        if col == _COL_24H:
            return entry["forecast"]["24h"]
        if col == _COL_REPEATED:
            return entry["repeated"]
        if _FORECAST_START_COL <= col < _BEST_DAYS_COL:
            return entry["forecast"][_FORECAST_COLS[col - _FORECAST_START_COL]]
        return 0

    def _on_header_clicked(self, col: int) -> None:
        if col not in _SORTABLE_COLS:
            return
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, True
        self._render_table()

    def _range_tooltip(self, low: float, high: float) -> str:
        return self.tr_("mutual_pr_range_tooltip", low=fmt_int(round(low)),
                        high=fmt_int(round(high)))

    def _render_table(self) -> None:
        entries = sorted(self._visible_entries(),
                         key=lambda e: self._sort_value(e, self._sort_col),
                         reverse=self._sort_desc)

        self._rendered_entries = entries   # see _build_md — export mirrors the current view
        self.empty_lbl.setVisible(not entries)
        self.table_card_ref.setVisible(bool(entries))
        self.table.setRowCount(len(entries))
        tint = QColor(COLORS["accent_soft"])
        for i, entry in enumerate(entries):
            ch = entry["channel"]
            label = _channel_label(ch)
            trend = entry.get("activity_trend")
            # Why the forecast below might read lower than the channel's
            # raw reach would suggest — see ad_forecast's own
            # `activity_trend` argument.
            if trend and trend.get("verdict") != "active":
                pct = round(activity_trend_penalty(trend) * 100)
                title = f"{_truncate(label)} ⚠"
                tooltip = (label + "\n" + self.tr_(
                    "mutual_pr_activity_tooltip",
                    verdict=self.tr_(f"activity_verdict_{trend['verdict']}"), pct=pct))
            else:
                title, tooltip = _truncate(label), label
            title_item = QTableWidgetItem(title)
            title_item.setToolTip(tooltip)
            self.table.setItem(i, _TITLE_COL, title_item)
            self.table.setItem(i, _FOLLOWERS_COL, QTableWidgetItem(fmt_int(entry["followers"])))

            rng = entry["forecast_range"]
            item_24h = QTableWidgetItem(fmt_int(round(entry["forecast"]["24h"])))
            item_24h.setBackground(tint)
            item_24h.setToolTip(self._range_tooltip(*rng["24h"]))
            self.table.setItem(i, _COL_24H, item_24h)
            self.table.setItem(i, _COL_REPEATED,
                               QTableWidgetItem(fmt_int(round(entry["repeated"]))))
            for j, horizon in enumerate(_FORECAST_COLS):
                item = QTableWidgetItem(fmt_int(round(entry["forecast"][horizon])))
                item.setToolTip(self._range_tooltip(*rng[horizon]))
                self.table.setItem(i, _FORECAST_START_COL + j, item)

            days_label = " ".join(f"{_WD_EMOJI[d]}{self.tr_(_WD_KEYS[d])}({_fmt_rate_pct(r)})"
                                  for d, r in entry["best_days"])
            self.table.setItem(i, _BEST_DAYS_COL, QTableWidgetItem(days_label))

        order = Qt.SortOrder.DescendingOrder if self._sort_desc else Qt.SortOrder.AscendingOrder
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(self._sort_col, order)
        self._sync_table_height()
        self._rebuild_links_table()
        self._rebuild_pairs_table()

    # -------------------------------------------------------------- links
    def _visible_channels(self) -> list[dict]:
        return [e["channel"] for e in self._visible_entries()]

    @staticmethod
    def _parse_iso(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            return None

    def _channel_label_for_key(self, key: str) -> str:
        ch = self._channel_by_key.get(key)
        return _channel_label(ch) if ch else key

    def _links_sort_value(self, target: dict, col: int):
        if col == _LINKS_COL_CHANNEL:
            return self._channel_label_for_key(target["key"]).lower()
        if col == _LINKS_COL_BY:
            return len(target["sources"])
        if col == _LINKS_COL_TOP:
            src = target["sources"][0] if target["sources"] else None
            return self._channel_label_for_key(src["key"]).lower() if src else ""
        return target["count"]   # _LINKS_COL_COUNT, and the default

    def _on_links_header_clicked(self, col: int) -> None:
        if col == self._links_sort_col:
            self._links_sort_desc = not self._links_sort_desc
        else:
            self._links_sort_col, self._links_sort_desc = col, True
        self._rebuild_links_table()

    def _on_calculate_links_clicked(self) -> None:
        """Runs app.cross_mentions.compute_cross_channel_mentions for the
        *whole* tracked base right now, synchronously — pure link matching
        over already-fetched data, no NER, no Telegram calls, fast enough
        (well under a second on this app's own ~200-channel test set) that
        a background ToolWorker would only add latency, not save any.
        Caches the result (app.cross_mentions.cache_cross_mentions) so the
        next time this view opens, the table paints from that instead of
        rescanning the base again."""
        self.links_calc_btn.setEnabled(False)
        self.links_calc_btn.setText(self.tr_("mutual_pr_links_calculating"))
        QApplication.processEvents()
        try:
            edges = compute_cross_channel_mentions(self.channel_store, MentionsStore())
            cache_cross_mentions(edges)
        finally:
            self.links_calc_btn.setEnabled(True)
        self._rebuild_links_table()

    def _rebuild_links_table(self) -> None:
        """Paints from whatever app.cross_mentions.load_cross_mentions_cache
        returns — never recomputes (see _on_calculate_links_clicked for the
        only thing that does). Target channels are ranked by
        app.cross_mentions.rank_targets ("which channel is mentioned more"
        is a property of the target, summed across every source), then
        filtered to the folder currently selected above and re-sorted by
        whichever column was last clicked."""
        cache = load_cross_mentions_cache()
        has_cache = cache is not None
        self.links_calc_btn.setText(self.tr_(
            "mutual_pr_links_recalc_btn" if has_cache else "mutual_pr_links_calc_btn"))
        if has_cache:
            dt = self._parse_iso(cache.get("calculated_at", ""))
            when = f"{dt:%Y-%m-%d %H:%M}" if dt else (cache.get("calculated_at") or "")
            self.links_calculated_lbl.setText(
                self.tr_("mutual_pr_links_calculated_at", when=when))
        else:
            self.links_calculated_lbl.setText("")

        targets = rank_targets(cache["edges"]) if has_cache and cache.get("edges") else []
        if self.folder_combo.currentData() is not None:
            visible_keys = {ch["key"] for ch in self._visible_channels()}
            targets = [t for t in targets if t["key"] in visible_keys]
        targets = sorted(targets, key=lambda t: self._links_sort_value(t, self._links_sort_col),
                         reverse=self._links_sort_desc)

        self.links_empty_lbl.setVisible(not targets)
        self.links_table.setVisible(bool(targets))
        self.links_table.setRowCount(len(targets))
        for i, target in enumerate(targets):
            ch = self._channel_by_key.get(target["key"])
            chan_item = QTableWidgetItem(self._channel_label_for_key(target["key"]))
            if ch and ch.get("link"):
                chan_item.setData(Qt.ItemDataRole.UserRole, ch["link"])
                chan_item.setToolTip(ch["link"])
            self.links_table.setItem(i, _LINKS_COL_CHANNEL, chan_item)
            self.links_table.setItem(i, _LINKS_COL_BY, QTableWidgetItem(str(len(target["sources"]))))
            self.links_table.setItem(i, _LINKS_COL_COUNT, QTableWidgetItem(fmt_int(target["count"])))

            sources = target["sources"]
            top = sources[0] if sources else None
            top_ch = self._channel_by_key.get(top["key"]) if top else None
            top_item = QTableWidgetItem(
                f"{self._channel_label_for_key(top['key'])} ({top['count']})" if top else "—")
            if len(sources) > 1:
                others = ", ".join(f"{self._channel_label_for_key(s['key'])} ({s['count']})"
                                   for s in sources[1:6])
                top_item.setToolTip(self.tr_("mutual_pr_links_also", names=others))
            elif top_ch and top_ch.get("link"):
                top_item.setToolTip(top_ch["link"])
            if top_ch and top_ch.get("link"):
                top_item.setData(Qt.ItemDataRole.UserRole, top_ch["link"])
            self.links_table.setItem(i, _LINKS_COL_TOP, top_item)

        order = Qt.SortOrder.DescendingOrder if self._links_sort_desc else Qt.SortOrder.AscendingOrder
        self.links_table.horizontalHeader().setSortIndicatorShown(True)
        self.links_table.horizontalHeader().setSortIndicator(self._links_sort_col, order)

    def _open_links_cell(self, row: int, col: int) -> None:
        if col not in (_LINKS_COL_CHANNEL, _LINKS_COL_TOP):
            return
        item = self.links_table.item(row, col)
        link = item.data(Qt.ItemDataRole.UserRole) if item else None
        if link:
            open_external_link(link)

    # -------------------------------------------------------------- pairs
    def _linked_pair_keys(self) -> set[frozenset]:
        """Every channel-pair the Channel links card's cache already
        connects (either direction — see app.cross_mentions), as
        {frozenset({key_a, key_b}), ...}: a pair a real posted link already
        connects doesn't need MPR Pairs to suggest it. Empty (no
        exclusions) until Channel links has actually been calculated once."""
        cache = load_cross_mentions_cache()
        edges = cache.get("edges") if cache else None
        return {frozenset((e["source"], e["target"])) for e in edges or []}

    def _ranked_pairs(self) -> list[dict]:
        return rank_mutual_pr_pairs(self._pair_channels(),
                                    exclude_keys=self._linked_pair_keys())

    def _wd_list(self, days: list[int]) -> str:
        return ", ".join(self.tr_(_WD_KEYS[d]) for d in days) or "—"

    def _pair_days_label(self, pair: dict) -> str:
        """Both channels' best posting days, plus (prefixed with ★) the days
        that work well for *both* at once — see
        app.scoring_pr.mutual_best_days."""
        text = f"A: {self._wd_list(pair['days_a'])} · B: {self._wd_list(pair['days_b'])}"
        if pair.get("mutual_days"):
            text = f"★ {self._wd_list(pair['mutual_days'])} · {text}"
        return text

    @staticmethod
    def _pair_forecast_ab(pair: dict) -> str:
        a24 = round((pair["a"].get("forecast") or {}).get("24h", 0))
        b24 = round((pair["b"].get("forecast") or {}).get("24h", 0))
        return f"+{fmt_int(a24)} / +{fmt_int(b24)}"

    def _rebuild_pairs_table(self) -> None:
        pairs = self._ranked_pairs()
        self.pairs_empty_lbl.setVisible(not pairs)
        self.pairs_table.setVisible(bool(pairs))
        self.pairs_table.setRowCount(len(pairs))
        for i, pair in enumerate(pairs):
            a, b = pair["a"], pair["b"]
            self.pairs_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            for col, ch in ((1, a), (2, b)):
                cell = QTableWidgetItem(f"{ch['label']} ({short_num(ch['followers'])})")
                if ch.get("link"):
                    cell.setData(Qt.ItemDataRole.UserRole, ch["link"])
                    cell.setToolTip(ch["link"])
                self.pairs_table.setItem(i, col, cell)
            self.pairs_table.setItem(i, 3, QTableWidgetItem(f"{pair['score']:.2f}"))
            self.pairs_table.setItem(i, 4, QTableWidgetItem(self._pair_days_label(pair)))
            self.pairs_table.setItem(i, 5, QTableWidgetItem(self._pair_forecast_ab(pair)))
        self._sync_pairs_table_height()

    def _open_pair_channel(self, row: int, col: int) -> None:
        if col not in (1, 2):
            return
        item = self.pairs_table.item(row, col)
        link = item.data(Qt.ItemDataRole.UserRole) if item else None
        if link:
            open_external_link(link)

    def _sync_pairs_table_height(self) -> None:
        total = self.pairs_table.horizontalHeader().height()
        for r in range(self.pairs_table.rowCount()):
            total += self.pairs_table.rowHeight(r)
        total += 2 * self.pairs_table.frameWidth()
        self.pairs_table.setMinimumHeight(total)

    def _sync_table_height(self) -> None:
        total = self.table.horizontalHeader().height()
        for r in range(self.table.rowCount()):
            total += self.table.rowHeight(r)
        total += 2 * self.table.frameWidth()
        self.table.setMinimumHeight(total)

    # ------------------------------------------------------------- export
    def _build_md(self) -> str:
        """Mirrors whatever's currently on screen — same folder filter and
        sort order as _render_table left in self._rendered_entries."""
        entries = self._rendered_entries
        if not entries:
            return ""
        headers = [self.tr_("col_followers"), self.tr_("col_channel_title"),
                  self.tr_("col_forecast_24h"), self.tr_("col_forecast_48h"),
                  self.tr_("col_forecast_72h"), self.tr_("col_forecast_week"),
                  self.tr_("col_forecast_month"), self.tr_("col_best_days"),
                  self.tr_("col_repeated_after_month"), self.tr_("folder_export_col_folder")]
        lines = ["| " + " | ".join(headers) + " |",
                 "| " + " | ".join(["---"] * len(headers)) + " |"]
        for entry in entries:
            days_label = " ".join(f"{_WD_EMOJI[d]}{self.tr_(_WD_KEYS[d])}({_fmt_rate_pct(r)})"
                                  for d, r in entry["best_days"])
            folder = self.folder_store.get_folder(entry["folder_id"]) if entry["folder_id"] else None
            row = [
                fmt_int(entry["followers"]), _channel_label(entry["channel"]),
                fmt_int(round(entry["forecast"]["24h"])),
                fmt_int(round(entry["forecast"]["48h"])),
                fmt_int(round(entry["forecast"]["72h"])),
                fmt_int(round(entry["forecast"]["week"])),
                fmt_int(round(entry["forecast"]["month"])),
                days_label,
                fmt_int(round(entry["repeated"])),
                folder["name"] if folder else self.tr_("folder_none"),
            ]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
        md = "\n".join(lines) + "\n"

        # Append — never replace — the pairs section at the bottom, so the
        # main forecast table above stays byte-for-byte what it was.
        pairs_md = self._pairs_md()
        if pairs_md:
            md += "\n" + pairs_md
        return md

    def _pairs_md(self) -> str:
        """The top mutual-PR partner pairs (see app.scoring_pr.
        rank_mutual_pr_pairs) as a bare Markdown table — same rows as the
        on-screen MPR Pairs card, no heading blurb."""
        pairs = self._ranked_pairs()
        if not pairs:
            return ""

        def _label(ch: dict) -> str:
            name = f"[{ch['label']}]({ch['link']})" if ch.get("link") else ch["label"]
            return f"{name} ({short_num(ch['followers'])})"

        headers = ["№", self.tr_("mutual_pr_partners_col_a"),
                   self.tr_("mutual_pr_partners_col_b"), self.tr_("col_rating"),
                   self.tr_("mutual_pr_partners_col_days"),
                   self.tr_("mutual_pr_partners_col_forecast")]
        lines = [f"## {self.tr_('mutual_pr_partners_title')}", "",
                 "| " + " | ".join(headers) + " |",
                 "| " + " | ".join(["---"] * len(headers)) + " |"]
        for i, pair in enumerate(pairs, 1):
            row = [str(i), _label(pair["a"]), _label(pair["b"]),
                   f"{pair['score']:.2f}", self._pair_days_label(pair),
                   self._pair_forecast_ab(pair)]
            lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
        return "\n".join(lines) + "\n"

    def _save_md(self) -> None:
        md = self._build_md()
        if not md:
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("report_empty"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("save_md_button"), "mutual_pr.md", "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("md_saved", path=path))

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_mutual_pr"))
        self.sub_lbl.setText(self.tr_("mutual_pr_sub"))
        self.hint_lbl.setText(self.tr_("mutual_pr_hint"))
        self.pick_lbl.setText(self.tr_("mutual_pr_pick_folder"))
        self.empty_lbl.setText(self.tr_("mutual_pr_empty"))
        self.md_btn.setText(self.tr_("save_md_button"))
        self.table_card_ref.title_lbl.setText(self.tr_("nav_mutual_pr"))
        self.links_card_ref.title_lbl.setText(self.tr_("mutual_pr_links_title"))
        self.links_hint_lbl.setText(self.tr_("mutual_pr_links_hint"))
        self.links_empty_lbl.setText(self.tr_("mutual_pr_links_empty"))
        self._set_links_headers()
        self.pairs_card_ref.title_lbl.setText(self.tr_("mutual_pr_partners_title"))
        self.pairs_hint_lbl.setText(self.tr_("mutual_pr_partners_hint"))
        self.pairs_empty_lbl.setText(self.tr_("mutual_pr_partners_empty"))
        self._set_pairs_headers()
        current_id = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem(self.tr_("mutual_pr_all_channels"), None)
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        self.folder_combo.blockSignals(False)
        idx = self.folder_combo.findData(current_id) if current_id is not None else 0
        self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._set_headers()
        self._render_table()
