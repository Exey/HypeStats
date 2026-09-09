"""Folders & Tags view (nav_folder_stat): the Folders and Tags management
cards up top — built by app.ui.config_view (which owns all the folder/tag
logic plus the worker the "Refresh comments" action needs) and mounted here
by MainWindow via mount_taxonomy_cards() — then, below them, per-period
(monthly/seasonal/half-year/rolling-year) totals and a composite Rating for
one folder, exportable as Markdown.

The cross-channel **reposts** table that used to sit here now lives at the
bottom of app.ui.mutual_pr_view instead (all the ad-swap signals on one
screen). Everything in the period table —
views/shares/reactions/viral-share and the "most viewed post" — comes from
each checkpoint's `distributions.monthly`, which the fetcher fills from
*every* scanned post (not a fresh Telegram fetch), so it's accurate
regardless of top-N, as long as the channel was fetched after that field
was added (older checkpoints report zero/blank there until refetched).

The month/season picker buttons are deliberately built from *every* tracked
channel, not just the ones in the currently selected folder — so the grid of
available periods stays put as you switch folders; only the numbers below it
change.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..errors import friendly_os_error
from ..folders import FolderStore
from ..periods import (
    YEAR_WINDOW_OPTIONS, period_key_label as _period_key_label, year_window_cutoff,
)
from ..rating import VIRAL_SHARE_DISPLAY_CAP, score_entries
from ..scoring import post_gauge_value, post_score_raw
from ..store import ChannelStore
from .dashboard_view import build_post_link, fmt_int
from .widgets import SectionCard, hline, open_external_link

MONTHS_FULL = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _parse_date(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _channel_uid(ch: dict) -> str:
    username = ch.get("username") or ""
    return f"@{username}" if username else (ch.get("channel") or ch.get("key", ""))


def _channel_title(ch: dict) -> str:
    return ch.get("title") or ch.get("channel") or ch.get("key", "")


def _channel_avg_views(ch: dict) -> float:
    """The reference the per-post quality gauge compares a post's views
    against (app.scoring's viral-excess term) — the channel's *recent*
    average where the checkpoint carries one, so a channel that has simply
    grown doesn't score every recent post as a breakout; the lifetime
    average otherwise (older checkpoints, or too little recent history)."""
    stats = ch.get("stats", {})
    return float(stats.get("avg_views_recent") or stats.get("avg_views", 0) or 0)


def _last_ended_half_year_key() -> tuple:
    """(year, half) for the most recent calendar half-year that has fully
    ended as of today — e.g. August 2026 is in H2 2026, which hasn't ended
    yet, so this returns (2026, 1); January-June of any year instead falls
    back to H2 of the *previous* year."""
    now = datetime.now()
    return (now.year - 1, 2) if now.month <= 6 else (now.year, 1)


class FolderStatView(QWidget):
    # period_table column index -> sortable (Most viewed post isn't).
    _PERIOD_SORTABLE_COLS = {0, 1, 2, 3, 4, 6, 7, 8}

    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                 parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self._channels: list[dict] = []
        self._periods: dict[tuple, dict] = {}
        self._period_mode = "halfyear"
        self._selected_period_key: tuple | None = None
        self._period_btns: dict[tuple, QPushButton] = {}
        self._year_window_key = "all"
        self._year_btns: dict[str, QPushButton] = {}
        self._period_entries: list[dict] = []
        self._period_sort_col = 7   # Rating
        self._period_sort_desc = True
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The whole page scrolls as one unit — the period table used to be
        # the one thing that scrolled internally (fixed to whatever space
        # was left after the header/pickers), which meant a big folder/
        # period got squeezed into a tiny visible slice. Now the table
        # grows to its full row count (see _sync_period_table_height) and
        # this scroll area is what pans to the rest of it.
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.page_scroll)

        page_holder = QWidget()
        page = QVBoxLayout(page_holder)
        page.setContentsMargins(34, 28, 40, 24)
        page.setSpacing(16)

        header = QVBoxLayout()
        header.setSpacing(2)
        self.title_lbl = QLabel(self.tr_("nav_folder_stat"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(self.tr_("folder_stat_sub"))
        self.sub_lbl.setObjectName("pageSub")
        header.addWidget(self.sub_lbl)
        page.addLayout(header)

        # Folder / Tag management cards — built by ConfigView, mounted here
        # by MainWindow via mount_taxonomy_cards().
        self._taxonomy_lay = QVBoxLayout()
        self._taxonomy_lay.setContentsMargins(0, 0, 0, 0)
        self._taxonomy_lay.setSpacing(16)
        page.addLayout(self._taxonomy_lay)

        pick_row = QHBoxLayout()
        self.pick_lbl = QLabel(self.tr_("folder_stat_pick_folder"))
        pick_row.addWidget(self.pick_lbl)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        pick_row.addWidget(self.folder_combo, 1)
        page.addLayout(pick_row)
        page.addWidget(hline())

        self.no_folders_lbl = QLabel(self.tr_("folder_stat_no_folders"))
        self.no_folders_lbl.setObjectName("navEmpty")
        self.no_folders_lbl.setWordWrap(True)
        page.addWidget(self.no_folders_lbl)

        self.empty_channels_lbl = QLabel(self.tr_("folder_stat_empty_channels"))
        self.empty_channels_lbl.setObjectName("navEmpty")
        self.empty_channels_lbl.setWordWrap(True)
        page.addWidget(self.empty_channels_lbl)

        self.content = QWidget()
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 6, 0, 0)
        body.setSpacing(20)
        # content's stretch is wildly disproportionate (100 vs. the trailing
        # spacer's 1) on purpose: with two equal-stretch=1 items, Qt splits
        # leftover space between them roughly evenly, which used to leave
        # the cards stopping well short of the window's bottom edge when
        # there's little enough data that the page doesn't need to scroll.
        # The 100:1 ratio makes content claim essentially all of it while
        # keeping the spacer's stretch nonzero — needed for the *hidden*
        # case below.
        page.addWidget(self.content, 100)
        # A trailing addStretch — not just content's own stretch — because
        # when content is hidden (no folders/no channels yet, see
        # _reload_channels) Qt orphans a hidden widget's stretch instead of
        # honoring it, and spreads the leftover height across the other,
        # visible items instead; this always-present spacer item is what
        # actually keeps the empty-state message pinned to the top.
        page.addStretch(1)

        body.addWidget(self._period_card(), 1)

        self.page_scroll.setWidget(page_holder)

    def mount_taxonomy_cards(self, folders_card, tags_card, mentions_export_card=None) -> None:
        """Place ConfigView's Folders and Tags cards at the top of this view
        (they own their own logic; this view just hosts them) — and, right
        below that row, the Mentions export card (see
        ConfigView._mentions_export_card), its own row rather than
        squeezed into the first one since it's a different kind of thing
        (a batch export, not folder/tag management)."""
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addWidget(folders_card, 1)
        row.addWidget(tags_card, 1)
        self._taxonomy_lay.addLayout(row)
        if mentions_export_card is not None:
            self._taxonomy_lay.addWidget(mentions_export_card)

    def _period_card(self) -> SectionCard:
        card = SectionCard(self.tr_("folder_stat_period_title"))
        self.period_card_ref = card

        self.period_hint_lbl = QLabel(self.tr_("folder_stat_period_hint"))
        self.period_hint_lbl.setObjectName("hint")
        self.period_hint_lbl.setWordWrap(True)

        self.mode_season_btn = QPushButton(self.tr_("period_mode_season"))
        self.mode_season_btn.setObjectName("ghost")
        self.mode_season_btn.setCheckable(True)
        self.mode_halfyear_btn = QPushButton(self.tr_("period_mode_halfyear"))
        self.mode_halfyear_btn.setObjectName("ghost")
        self.mode_halfyear_btn.setCheckable(True)
        self.mode_month_btn = QPushButton(self.tr_("period_mode_month"))
        self.mode_month_btn.setObjectName("ghost")
        self.mode_month_btn.setCheckable(True)
        self.mode_year_btn = QPushButton(self.tr_("period_mode_year"))
        self.mode_year_btn.setObjectName("ghost")
        self.mode_year_btn.setCheckable(True)
        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.setExclusive(True)
        self._mode_btn_group.addButton(self.mode_season_btn)
        self._mode_btn_group.addButton(self.mode_halfyear_btn)
        self._mode_btn_group.addButton(self.mode_month_btn)
        self._mode_btn_group.addButton(self.mode_year_btn)

        self.period_md_btn = QPushButton(self.tr_("save_md_button"))
        self.period_md_btn.clicked.connect(self._save_md)

        self.picker_container = QWidget()
        self.picker_lay = QVBoxLayout(self.picker_container)
        self.picker_lay.setContentsMargins(0, 0, 0, 0)
        self.picker_lay.setSpacing(8)

        self.period_empty_lbl = QLabel(self.tr_("folder_stat_period_empty"))
        self.period_empty_lbl.setObjectName("hint")

        self.period_table = QTableWidget(0, 9)
        self.period_table.setHorizontalHeaderLabels([
            self.tr_("col_channel_title"), self.tr_("col_username_id"), self.tr_("col_views"),
            self.tr_("col_shares"), self.tr_("col_reactions"), self.tr_("col_most_viewed"),
            self.tr_("col_viral_share"), self.tr_("col_rating"), self.tr_("col_post_quality")])
        self.period_table.verticalHeader().setVisible(False)
        self.period_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.period_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        period_header = self.period_table.horizontalHeader()
        period_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        period_header.setSectionsClickable(True)
        period_header.sectionClicked.connect(self._on_period_header_clicked)
        # Title and Username/ID commonly overflow Qt's default column width —
        # give them 33% more room than that default up front.
        for col in (0, 1):
            self.period_table.setColumnWidth(col, int(self.period_table.columnWidth(col) * 1.33))
        self.period_table.cellDoubleClicked.connect(self._open_period_post)
        self.period_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # No internal scrollbar: the table grows to fit every row (see
        # _sync_period_table_height) and the page-level scroll area handles
        # anything taller than the viewport, instead of the table scrolling
        # on its own within a fixed slice of the page.
        self.period_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Every widget the toggle handler touches now exists, so it's safe to
        # wire the signal and set the default mode (which fires it once).
        self.mode_season_btn.toggled.connect(lambda checked: self._on_mode_toggled("season", checked))
        self.mode_halfyear_btn.toggled.connect(
            lambda checked: self._on_mode_toggled("halfyear", checked))
        self.mode_month_btn.toggled.connect(lambda checked: self._on_mode_toggled("month", checked))
        self.mode_year_btn.toggled.connect(lambda checked: self._on_mode_toggled("year", checked))
        self.mode_halfyear_btn.setChecked(True)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_halfyear_btn)
        mode_row.addWidget(self.mode_season_btn)
        mode_row.addWidget(self.mode_month_btn)
        mode_row.addWidget(self.mode_year_btn)
        mode_row.addStretch()
        mode_row.addWidget(self.period_md_btn)

        card.body.addWidget(self.period_hint_lbl)
        card.body.addLayout(mode_row)
        card.body.addWidget(self.picker_container)
        card.body.addWidget(self.period_empty_lbl)
        card.body.addWidget(self.period_table, 1)
        return card

    # --------------------------------------------------------------- data
    def refresh(self) -> None:
        """Reload folders + this folder's channels from disk. Call whenever
        the view is shown, or folders/channels changed elsewhere."""
        current_id = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        self.folder_combo.blockSignals(False)
        if self.folder_combo.count():
            idx = self.folder_combo.findData(current_id) if current_id else -1
            self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._reload_channels()

    def _on_folder_changed(self, _index: int) -> None:
        self._reload_channels()

    def _reload_channels(self) -> None:
        folder_id = self.folder_combo.currentData()
        self._channels = []
        if folder_id:
            keys = [k for k, fid in self.folder_store.assignments.items() if fid == folder_id]
            for key in keys:
                data = self.channel_store.load(key)
                if data:
                    data.setdefault("key", key)
                    self._channels.append(data)

        has_folders = self.folder_combo.count() > 0
        has_channels = bool(self._channels)
        self.no_folders_lbl.setVisible(not has_folders)
        self.pick_lbl.setVisible(has_folders)
        self.folder_combo.setVisible(has_folders)
        self.empty_channels_lbl.setVisible(has_folders and not has_channels)
        self.content.setVisible(has_channels)

        self._rebuild_period_picker()

    # ------------------------------------------------------------- periods
    def _collect_periods(self, mode: str) -> dict[tuple, dict]:
        """Per-period totals for the *currently selected folder's* channels.

        Views/shares/reactions/viral-share and the "most viewed post" come
        from each checkpoint's `distributions.monthly`, which the fetcher
        fills from *every* scanned post, not just the stored top-N sample
        in `rows` (checkpoints fetched before these fields existed report
        zero/blank here until refetched). Post Quality is the exception —
        app.scoring's gauge score needs per-post reactions/forwards/comments/
        views, which only `rows` carries, so it's computed over whatever of
        that top-N pool falls in each bucket instead. The *displayed*
        "Post Quality" column (`quality_display`) is the standard 0-1000
        gauge — same number the dashboard and High-Quality Posts grid show
        — averaged over the pool. The Rating consumes a *separate* take
        (`quality`): median not mean (the pool skews toward a channel's best
        posts) and `viral_excess=False` (the Rating already scores reach via
        its views + virality terms). Keeping the two apart means tuning the
        Rating never turns the readable column into single digits.

        Each entry also carries `posts` (the period's post count) — the
        Rating divides views and engagement by it, so a channel that posts
        3× as often doesn't out-rank one with better posts (see app.rating).

        Reposts (posts forwarded in from another channel — see
        app.tools.channel_stat._is_repost) are excluded throughout: the
        monthly `*_own` totals drop them, and the quality loop skips
        `rows` entries flagged `repost`. A channel re-sharing a bigger
        channel's viral post shouldn't inherit its views into this view's
        totals or its Rating. Each entry also carries `repost_share` (the
        percent of the period's posts that were reposts), which
        app.rating.score_entries turns into an extra smooth cut on the
        composite score once it passes 20%. Checkpoints fetched before the
        `repost` flag existed have no `*_own` totals, report repost_share 0,
        and fall back to counting every post, unchanged, until refetched.
        """
        periods: dict[tuple, dict] = {}
        for ch in self._channels:
            avg_views = _channel_avg_views(ch)
            buckets: dict[tuple, dict] = {}
            for m in ch.get("distributions", {}).get("monthly") or []:
                count = int(m.get("count", 0) or 0)
                if not count:
                    continue
                # Own posts only (reposts excluded); pre-`repost` checkpoints
                # have no `*_own` keys, so fall back to the all-posts totals.
                own_count = int(m.get("count_own", count) or 0)
                try:
                    year, month = (int(x) for x in m.get("label", "").split("-"))
                except ValueError:
                    continue
                if mode == "year":
                    # Everything within the selected rolling window collapses
                    # into a single bucket — month-granularity, since that's
                    # all `distributions.monthly` can offer.
                    cutoff = year_window_cutoff(self._year_window_days())
                    if cutoff is not None and (year, month) < (cutoff.year, cutoff.month):
                        continue
                    key, label = ("year", self._year_window_key), self._year_window_label()
                else:
                    key, label = _period_key_label(year, month, mode)
                b = buckets.setdefault(key, {
                    "label": label, "count": 0, "count_total": 0, "views": 0,
                    "shares": 0, "reactions": 0, "viral_count": 0, "top_row": None,
                })
                b["count"] += own_count
                b["count_total"] += count
                b["views"] += int(m.get("views_own", m.get("views", 0)) or 0)
                b["shares"] += int(m.get("shares_own", m.get("shares", 0)) or 0)
                b["reactions"] += int(m.get("reactions_own", m.get("reactions", 0)) or 0)
                b["viral_count"] += int(m.get("viral_count_own", m.get("viral_count", 0)) or 0)
                top = m.get("top")
                if top and (b["top_row"] is None
                           or int(top.get("views", 0) or 0) > int(b["top_row"].get("views", 0) or 0)):
                    b["top_row"] = top

            # Two takes on the same 0-1000 gauge (app.scoring):
            #  q_rating  — viral_excess=False, median: fed to app.rating, which
            #              already scores reach (views term) + virality on
            #              their own, and whose `rows` pool skews to a
            #              channel's best posts (so median, not mean).
            #  q_display — the standard per-post gauge (viral_excess on, mean),
            #              the same number the dashboard / High-Quality Posts
            #              grid show; this is the readable "Post Quality"
            #              column, kept separate so tuning the Rating never
            #              makes the displayed figure unrecognisable.
            q_rating: dict[tuple, list[float]] = {}
            q_display: dict[tuple, list[float]] = {}
            for r in ch.get("rows", []) or []:
                # Reposts aren't this channel's content — see module note.
                if r.get("repost"):
                    continue
                dt = _parse_date(r.get("date", ""))
                if dt is None:
                    continue
                if mode == "year":
                    cutoff = year_window_cutoff(self._year_window_days())
                    if cutoff is not None and dt < cutoff:
                        continue
                    key = ("year", self._year_window_key)
                else:
                    key, _label = _period_key_label(dt.year, dt.month, mode)
                q_rating.setdefault(key, []).append(
                    post_gauge_value(post_score_raw(r, avg_views, viral_excess=False)))
                q_display.setdefault(key, []).append(
                    post_gauge_value(post_score_raw(r, avg_views)))

            for key, b in buckets.items():
                bucket = periods.setdefault(key, {"label": b["label"], "entries": []})
                rscores = q_rating.get(key)
                dscores = q_display.get(key)
                bucket["entries"].append({
                    "channel": ch, "views": b["views"], "shares": b["shares"],
                    "reactions": b["reactions"], "top_row": b["top_row"],
                    # posts in the period — app.rating divides views/engagement
                    # by this so a prolific channel can't win on volume alone.
                    "posts": b["count"],
                    "viral_share": min((b["viral_count"] / b["count"] * 100) if b["count"] else 0,
                                       VIRAL_SHARE_DISPLAY_CAP),
                    # % of the period's posts that were reposts — feeds
                    # app.rating.repost_share_penalty.
                    "repost_share": ((b["count_total"] - b["count"]) / b["count_total"] * 100)
                                    if b["count_total"] else 0,
                    "quality": median(rscores) if rscores else 0,
                    "quality_display": (sum(dscores) / len(dscores)) if dscores else 0,
                })
        for bucket in periods.values():
            score_entries(bucket["entries"])
        return periods

    def _collect_all_period_keys(self, mode: str) -> list[tuple[tuple, str]]:
        """Every period key/label across *all* tracked channels, regardless of
        folder — this is what drives the picker buttons, so the grid of
        available periods doesn't change when you switch folders."""
        keys: dict[tuple, str] = {}
        for summary in self.channel_store.list():
            data = self.channel_store.load(summary["key"])
            if not data:
                continue
            for m in data.get("distributions", {}).get("monthly") or []:
                if not int(m.get("count", 0) or 0):
                    continue
                try:
                    year, month = (int(x) for x in m.get("label", "").split("-"))
                except ValueError:
                    continue
                key, label = _period_key_label(year, month, mode)
                keys[key] = label
        return sorted(keys.items(), key=lambda kv: kv[0], reverse=True)

    # ------------------------------------------------------- period picker
    @staticmethod
    def _clear_layout(layout) -> None:
        # hide() first — takeAt() only unmanages a widget from the layout,
        # it doesn't hide it, and deleteLater() doesn't actually destroy it
        # until the next event-loop pass, so a rapid double-rebuild (the
        # constructor's initial setChecked(True) followed by main_window's
        # first refresh(), both before the event loop is re-entered) can
        # leave the old buttons visibly stacked under the new ones.
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                FolderStatView._clear_layout(sub)

    def _on_mode_toggled(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        self._period_mode = mode
        self._selected_period_key = None
        self._rebuild_period_picker()

    def _make_period_button(self, text: str, key: tuple) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("ghost")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._period_btn_group.addButton(btn)
        btn.toggled.connect(lambda checked, k=key: self._on_period_button_toggled(k, checked))
        self._period_btns[key] = btn
        return btn

    def _build_season_picker(self, keys_labels: list[tuple[tuple, str]]) -> None:
        row_widget = QWidget()
        row_lay = QHBoxLayout(row_widget)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(6)
        for key, label in keys_labels:
            row_lay.addWidget(self._make_period_button(label, key))
        row_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(row_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(48)
        self.picker_lay.addWidget(scroll)

    def _build_month_picker(self, keys_labels: list[tuple[tuple, str]]) -> None:
        years = sorted({key[0] for key, _ in keys_labels}, reverse=True)

        grid_widget = QWidget()
        grid_lay = QVBoxLayout(grid_widget)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        grid_lay.setSpacing(6)
        for year in years:
            year_row = QHBoxLayout()
            year_row.setSpacing(6)
            year_lbl = QLabel(f"{year}:")
            year_lbl.setMinimumWidth(52)
            year_lbl.setStyleSheet("font-weight: 700;")
            year_row.addWidget(year_lbl)
            for m in range(1, 13):
                year_row.addWidget(self._make_period_button(MONTHS_FULL[m], (year, m)))
            year_row.addStretch()
            grid_lay.addLayout(year_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFixedHeight(min(220, 46 * max(len(years), 1) + 10))
        self.picker_lay.addWidget(scroll)

    def _year_window_days(self) -> int | None:
        for key, _label_key, days in YEAR_WINDOW_OPTIONS:
            if key == self._year_window_key:
                return days
        return None

    def _year_window_label(self) -> str:
        for key, label_key, _days in YEAR_WINDOW_OPTIONS:
            if key == self._year_window_key:
                return self.tr_(label_key)
        return ""

    def _build_year_picker(self) -> None:
        row_widget = QWidget()
        row_lay = QHBoxLayout(row_widget)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(6)
        self._year_btn_group = QButtonGroup(self)
        self._year_btn_group.setExclusive(True)
        self._year_btns = {}
        for key, label_key, _days in YEAR_WINDOW_OPTIONS:
            btn = QPushButton(self.tr_(label_key))
            btn.setObjectName("ghost")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._year_btn_group.addButton(btn)
            btn.toggled.connect(lambda checked, k=key: self._on_year_window_toggled(k, checked))
            self._year_btns[key] = btn
            row_lay.addWidget(btn)
        row_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(row_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(48)
        self.picker_lay.addWidget(scroll)
        self._year_btns[self._year_window_key].setChecked(True)

    def _on_year_window_toggled(self, key: str, checked: bool) -> None:
        if not checked:
            return
        self._year_window_key = key
        self._selected_period_key = ("year", key)
        self._rebuild_selected_period_table()

    def _rebuild_period_picker(self) -> None:
        self._clear_layout(self.picker_lay)
        self._period_btn_group = QButtonGroup(self)
        self._period_btn_group.setExclusive(True)
        self._period_btns = {}
        self.picker_container.setVisible(True)

        if self._period_mode == "year":
            self._build_year_picker()  # its own setChecked(True) triggers the rebuild
            return

        keys_labels = self._collect_all_period_keys(self._period_mode)
        if self._period_mode in ("season", "halfyear"):
            # Both are a flat, single-scroll row of buttons (one per
            # period key) — the season picker's builder works unchanged
            # for half-years too, unlike the month picker's per-year grid.
            self._build_season_picker(keys_labels)
        else:
            self._build_month_picker(keys_labels)

        if keys_labels:
            if self._selected_period_key in self._period_btns:
                target = self._selected_period_key
            elif self._period_mode == "halfyear" and _last_ended_half_year_key() in self._period_btns:
                # Default to the last half-year that's actually *over* (e.g.
                # August 2026 defaults to 2026 H1, not the still-in-progress
                # H2) rather than just whichever bucket happens to be
                # newest — a channel with a stray early post in the current,
                # unfinished half would otherwise make that partial half the
                # default.
                target = _last_ended_half_year_key()
            else:
                target = keys_labels[0][0]
            self._period_btns[target].setChecked(True)
        else:
            self._selected_period_key = None
            self._rebuild_selected_period_table()

    def _on_period_button_toggled(self, key: tuple, checked: bool) -> None:
        if not checked:
            return
        self._selected_period_key = key
        self._rebuild_selected_period_table()

    def _rebuild_selected_period_table(self) -> None:
        self._periods = self._collect_periods(self._period_mode)
        bucket = self._periods.get(self._selected_period_key) if self._selected_period_key else None
        self._period_entries = list(bucket["entries"]) if bucket else []
        self._render_period_table()

    def _period_sort_value(self, entry: dict, col: int):
        ch = entry["channel"]
        if col == 0:
            return _channel_title(ch).lower()
        if col == 1:
            return _channel_uid(ch).lower()
        if col == 2:
            return entry["views"]
        if col == 3:
            return entry["shares"]
        if col == 4:
            return entry["reactions"]
        if col == 6:
            return entry["viral_share"]
        if col == 7:
            return entry["score"]
        if col == 8:
            return entry["quality_display"]
        return 0

    def _on_period_header_clicked(self, col: int) -> None:
        if col not in self._PERIOD_SORTABLE_COLS:
            return
        if col == self._period_sort_col:
            self._period_sort_desc = not self._period_sort_desc
        else:
            self._period_sort_col, self._period_sort_desc = col, True
        self._render_period_table()

    def _render_period_table(self) -> None:
        entries = sorted(self._period_entries,
                         key=lambda e: self._period_sort_value(e, self._period_sort_col),
                         reverse=self._period_sort_desc)

        self.period_empty_lbl.setVisible(not entries)
        self.period_table.setVisible(bool(entries))
        self.period_table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            ch = entry["channel"]
            self.period_table.setItem(i, 0, QTableWidgetItem(_channel_title(ch)))
            self.period_table.setItem(i, 1, QTableWidgetItem(_channel_uid(ch)))
            self.period_table.setItem(i, 2, QTableWidgetItem(fmt_int(entry["views"])))
            self.period_table.setItem(i, 3, QTableWidgetItem(fmt_int(entry["shares"])))
            self.period_table.setItem(i, 4, QTableWidgetItem(fmt_int(entry["reactions"])))

            top_row = entry["top_row"]
            if top_row is None:
                post_item = QTableWidgetItem("—")
            else:
                # Full text, not chopped to a fixed character count — the
                # column stretches to fill whatever space is available, so a
                # fixed cutoff was cutting text short of what the cell could
                # actually show; Qt already elides whatever doesn't fit.
                snippet = (top_row.get("text") or f"#{top_row.get('id')}").strip()
                link = build_post_link(ch.get("channel") or ch.get("username") or "",
                                       top_row.get("id", 0))
                post_item = QTableWidgetItem(snippet or f"#{top_row.get('id')}")
                post_item.setToolTip(f"{snippet}\n{link}" if snippet else link)
                post_item.setData(Qt.ItemDataRole.UserRole, link)
            self.period_table.setItem(i, 5, post_item)

            self.period_table.setItem(i, 6, QTableWidgetItem(f"{entry['viral_share']:.4f}%"))
            self.period_table.setItem(i, 7, QTableWidgetItem(f"{entry['score']:.3f}"))
            self.period_table.setItem(i, 8, QTableWidgetItem(str(round(entry["quality_display"]))))

        order = (Qt.SortOrder.DescendingOrder if self._period_sort_desc
                 else Qt.SortOrder.AscendingOrder)
        self.period_table.horizontalHeader().setSortIndicatorShown(True)
        self.period_table.horizontalHeader().setSortIndicator(self._period_sort_col, order)
        self._sync_period_table_height()

    def _sync_period_table_height(self) -> None:
        """Grow period_table to fit every row with no internal scrollbar
        (see its setVerticalScrollBarPolicy in _period_card) — height still
        only a *minimum*, so QSizePolicy.Expanding can inflate it further
        to fill the page when there's little enough data that the page
        doesn't need to scroll (see _build_ui's 100:1 stretch)."""
        total = self.period_table.horizontalHeader().height()
        for r in range(self.period_table.rowCount()):
            total += self.period_table.rowHeight(r)
        total += 2 * self.period_table.frameWidth()
        self.period_table.setMinimumHeight(total)

    def _open_period_post(self, row: int, _col: int) -> None:
        item = self.period_table.item(row, 5)
        link = item.data(Qt.ItemDataRole.UserRole) if item else None
        if link:
            open_external_link(link)

    # ------------------------------------------------------------- export
    def _build_md(self) -> str:
        headers = [self.tr_("col_username_id"), self.tr_("col_views"), self.tr_("col_shares"),
                   self.tr_("col_reactions"), self.tr_("col_most_viewed"),
                   self.tr_("col_viral_share"), self.tr_("col_rating"),
                   self.tr_("col_post_quality")]
        lines = [f"# {self.folder_combo.currentText()}", ""]
        for key in sorted(self._periods):
            bucket = self._periods[key]
            lines.append(f"## {bucket['label']}")
            lines.append("")
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for entry in sorted(bucket["entries"], key=lambda e: e["viral_share"], reverse=True):
                ch = entry["channel"]
                top_row = entry["top_row"]
                if top_row is None:
                    post_md = "—"
                else:
                    text = (top_row.get("full_text") or top_row.get("text")
                           or f"#{top_row.get('id')}")
                    text = (text.replace("|", "\\|").replace("\n", " ").strip()
                           or f"#{top_row.get('id')}")
                    link = build_post_link(ch.get("channel") or ch.get("username") or "",
                                           top_row.get("id", 0))
                    post_md = f"[{text}]({link})"
                lines.append(
                    f"| {_channel_uid(ch)} | {entry['views']} | {entry['shares']} | "
                    f"{entry['reactions']} | {post_md} | {entry['viral_share']:.4f}% | "
                    f"{entry['score']:.3f} | {round(entry['quality_display'])} |")
            lines.append("")
        return "\n".join(lines).rstrip("\n") + "\n"

    def _save_md(self) -> None:
        if not self._periods:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("folder_stat_period_empty"))
            return
        default = f"{self.folder_combo.currentText() or 'folder'}_stats.md"
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("save_md_button"), default, "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_md())
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("md_saved", path=path))

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_folder_stat"))
        self.sub_lbl.setText(self.tr_("folder_stat_sub"))
        self.pick_lbl.setText(self.tr_("folder_stat_pick_folder"))
        self.no_folders_lbl.setText(self.tr_("folder_stat_no_folders"))
        self.empty_channels_lbl.setText(self.tr_("folder_stat_empty_channels"))

        self.period_card_ref.title_lbl.setText(self.tr_("folder_stat_period_title"))
        self.period_hint_lbl.setText(self.tr_("folder_stat_period_hint"))
        self.mode_season_btn.setText(self.tr_("period_mode_season"))
        self.mode_halfyear_btn.setText(self.tr_("period_mode_halfyear"))
        self.mode_month_btn.setText(self.tr_("period_mode_month"))
        self.mode_year_btn.setText(self.tr_("period_mode_year"))
        for key, btn in self._year_btns.items():
            label_key = next(lk for k, lk, _d in YEAR_WINDOW_OPTIONS if k == key)
            btn.setText(self.tr_(label_key))
        self.period_md_btn.setText(self.tr_("save_md_button"))
        self.period_empty_lbl.setText(self.tr_("folder_stat_period_empty"))
        self.period_table.setHorizontalHeaderLabels([
            self.tr_("col_channel_title"), self.tr_("col_username_id"), self.tr_("col_views"),
            self.tr_("col_shares"), self.tr_("col_reactions"), self.tr_("col_most_viewed"),
            self.tr_("col_viral_share"), self.tr_("col_rating"), self.tr_("col_post_quality")])
