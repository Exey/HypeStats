"""Mentions view (nav_mentions): up to MAX_MENTIONS_COMPARE channels' post
texts side by side, so mentions of the same person (a model, a photographer)
across different channels can be spotted and reconciled into one canonical
identity. No page title/header of its own — the sidebar's Mentions button
already says what this is, and the view trades that for more height to work
in (see _build_ui).

Two tiers, one scrollable page:

- Per-channel columns — a period filter (shared across columns, so all four
  are read on the same footing), the channel's post texts in scope as a
  sortable table (date / media type / text, extracted person names
  highlighted inline — see _populate_texts_table), and a staging table of
  the names found by NER, a known-name dictionary scan, and a caption
  pattern scan together (see _rebuild_column, and
  app.mentions.extract_person_names/find_known_names_in_text/
  extract_person_names_by_pattern): whether each is already in mentions.md
  (and if not, a way to link it there), and the post ids it came from
  (each independently clickable, hover shows the cached thumbnail if one's
  been fetched — see _post_id_chips).
- The mentions.md table itself (app.mentions.MentionsStore) — id / names /
  unclear links, directly editable. Autosaves on leaving the view (hideEvent)
  and via an explicit Save button that's only enabled while there are
  unsaved edits.
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlparse

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QMenu, QMessageBox, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from ...errors import friendly_os_error
from ...mentions import (
    MentionsStore, NameExceptions, apply_no_link_penalty, cache_channel_mentions,
    canonical_link_key, classify_channel_links, extract_all_names_per_post,
    extraction_available, is_telegram_link, name_link_matches, name_tg_links,
    names_match, normalize_links, resolve_telegram_link, tg_identity_key,
)
from ...media_cache import thumbnail_path
from ...periods import period_key_label
from ...store import ChannelStore
from ..dashboard_view import build_post_link
from ..theme import COLORS, fs
from ..widgets import StatCard, hline, open_external_link

# media_type -> the label _format_media_type shows, in display order; a
# "Circle" (video_note) never carries a ×N — Telegram round videos can't be
# grouped into an album, so it's always exactly one.
_MEDIA_TYPE_LABELS = [
    ("photo", "Photos"), ("video", "Video"), ("video_note", "Circle"),
    ("audio", "Audio"), ("file", "File"),
]

# Fewer columns than Compare/Compare Charts (MAX_COMPARE=8, see compare_view)
# — four channels' full post texts side by side is already a lot to read.
MAX_MENTIONS_COMPARE = 4
_ALL_TIME = ("all", None)  # period combo sentinel

# The mentions.md table's "names" column is kept at this fraction of the
# table's width (see _resize_mentions_table_columns) — Qt's QHeaderView has
# no native percentage resize mode, only fixed pixels or "stretch to fill
# whatever's left", so this is recomputed by hand on every resize.
_MENTIONS_NAMES_COL_FRACTION = 0.45

# Floor for the mentions.md table's height (~2x names_table's own 260,
# since mentions.md is the persistent canonical store, more prominent than
# the per-column staging tables) — see _fit_mentions_table_height, which
# grows it past this to fit all of its own rows instead of ever scrolling
# internally, so the page's own scrollbar is the only one a viewer has to
# fight with.
_MENTIONS_TABLE_MIN_HEIGHT = 520

# The 6 small stat cards replacing the old "Summary" table (see
# _build_column/_update_stats_table) — key, card-title i18n key, in
# on-screen order (3-wide grid, so this fills 2 rows). "Force link" isn't
# one of these -- see _links_title_text, which folds that back into the
# texts table's own title instead of giving it a 7th card.
_STAT_CARD_SPECS = [
    ("fairness", "mentions_card_fairness"),
    ("fair", "mentions_card_fair"),
    ("fake", "mentions_card_fake"),
    ("no_link", "mentions_card_no_link"),
    ("unique", "mentions_card_unique"),
    ("balance", "mentions_card_tg_web"),
]
_STAT_CARD_COLUMNS = 3
# compare_view.py's own compact-card shrink, reused here for the same
# reason: narrow, side-by-side columns, not one full-width dashboard.
_STAT_CARD_HEIGHT = round(132 * 0.6 * 0.9 * 0.9)


def _parse_date(iso: str):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _link_post_id(channel_text: str, post_id: int) -> str:
    return build_post_link(channel_text, post_id)


def _short_date(iso: str) -> str:
    """"2026-09-01T00:00:00" -> "26-09-01" -- the texts table's Date column
    is narrow enough that the century is dead weight."""
    return (iso or "")[2:10]


def _thumb_tooltip(link: str, fallback: str) -> str:
    """Rich-text tooltip embedding the cached thumbnail for `link`'s post, if
    one's been fetched (High-Quality Posts / dashboard "Fetch media") —
    otherwise just `fallback` as plain text. `link` is a t.me/<channel>/<id>
    (or t.me/c/<id>/<msg>) URL, same shape build_post_link produces."""
    m = re.search(r"t\.me/(c/\d+|[^/]+)/(\d+)$", link)
    if not m:
        return fallback
    channel, post_id = m.group(1), int(m.group(2))
    path = thumbnail_path(channel, post_id)
    if not path.exists():
        return fallback
    return f'<img src="{path}" width="180"><br>{fallback}'


def _format_media_type(post: dict) -> str:
    """"Photos x9", "Video x1", "Circle", "Photos x2, Video x1" for a mixed
    album, or "Text" for a post with no media — from the per-type tally in
    `media_counts` (see channel_stat.py), not just the anchor `media_type`,
    so a mixed album shows every type it actually carries."""
    counts = post.get("media_counts") or {}
    parts = []
    for mt, label in _MEDIA_TYPE_LABELS:
        n = int(counts.get(mt, 0) or 0)
        if not n:
            continue
        parts.append(label if mt == "video_note" else f"{label} x{n}")
    return ", ".join(parts) if parts else "Text"


def _tg_username_from_url(url: str) -> str | None:
    """"@gotomargosha" from "https://t.me/gotomargosha" — None if `url`
    isn't a plain t.me/<username> link: a post link (path has a second
    segment, the message id) or a join/invite link carry no reusable
    identity of their own."""
    if not is_telegram_link(url):
        return None
    path = urlparse(url).path.strip("/")
    if not path or "/" in path or path.startswith(("joinchat", "+", "c")):
        return None
    return f"@{path}"




class MentionsView(QWidget):

    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.mentions_store = MentionsStore()
        self.name_exceptions = NameExceptions()
        self.channel_store = ChannelStore()
        self._channels: list[dict] = []   # up to MAX_MENTIONS_COMPARE checkpoint dicts
        self._period_options: list[tuple] = []   # [(key, label), ...], _ALL_TIME first
        self._selected_period = _ALL_TIME
        self._loading_table = False   # guard against itemChanged during a programmatic rebuild
        # {numeric Telegram channel_id: ChannelStore.list() summary} -- lets
        # a repost's bare fwd_from channel_id (see
        # channel_stat._repost_source) resolve to a name without any live
        # Telegram API call, by matching it against this app's own tracked
        # channels. Refreshed once per load() (see _repost_source_html).
        self._channel_id_lookup: dict[int, dict] = {}
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    def _open_external_link(self, url: str) -> None:
        """Every link this view opens externally (a post, a name's own
        Telegram/web link, the Summary "Force link") goes through here as a
        bound-method slot (so linkActivated etc. can connect straight to
        it), rather than calling QDesktopServices.openUrl directly — see
        widgets.open_external_link, the same app-wide helper every other
        view uses, for why (a t.me link opens straight in the Telegram
        app, not a browser tab)."""
        open_external_link(url)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.table and event.type() == QEvent.Type.Resize:
            # event.size() (this resize's own new size), not a live query
            # like self.table.viewport().width() -- the viewport hasn't
            # necessarily been resized yet at the point this fires (it's a
            # separate child widget QAbstractScrollArea updates on its own),
            # so a live read here lags one resize behind.
            self._resize_mentions_table_columns(event.size().width())
        return super().eventFilter(obj, event)

    def _resize_mentions_table_columns(self, total_width: int | None = None) -> None:
        """Keeps "names" at _MENTIONS_NAMES_COL_FRACTION of self.table's
        width — called on every resize (see eventFilter) since Qt has no
        built-in percentage column-resize mode."""
        total = self.table.viewport().width() if total_width is None else total_width
        if total <= 0:   # not laid out yet (e.g. the QTimer.singleShot(0, ...)
            return        # in _build_ui firing before the first real geometry)
        self.table.setColumnWidth(1, int(total * _MENTIONS_NAMES_COL_FRACTION))

    def _fit_mentions_table_height(self) -> None:
        """Grows self.table to exactly fit its header plus every current
        row (floor: _MENTIONS_TABLE_MIN_HEIGHT) — paired with its vertical
        scrollbar being off (see _build_ui), so it never needs to scroll
        internally; call after anything that can change row count or row
        heights (resizeRowsToContents first, if a row just changed size)."""
        rows_h = sum(self.table.rowHeight(i) for i in range(self.table.rowCount()))
        header_h = self.table.horizontalHeader().height()
        frame = 2 * self.table.frameWidth()
        self.table.setFixedHeight(max(_MENTIONS_TABLE_MIN_HEIGHT, header_h + rows_h + frame + 4))

    # -------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QWidget()
        page = QVBoxLayout(body)
        # No page title/subtitle here (the sidebar button already says what
        # this is) and a tighter top margin than other views — trading that
        # header for more height to actually work in, since four columns of
        # post tables need all the room they can get.
        page.setContentsMargins(34, 14, 40, 24)
        page.setSpacing(12)
        scroll.setWidget(body)

        self.no_extraction_lbl = QLabel(self.tr_("mentions_no_extraction"))
        self.no_extraction_lbl.setObjectName("hint")
        self.no_extraction_lbl.setWordWrap(True)
        self.no_extraction_lbl.setVisible(not extraction_available())
        page.addWidget(self.no_extraction_lbl)

        period_row = QHBoxLayout()
        self.period_lbl = QLabel(self.tr_("mentions_period_label"))
        period_row.addWidget(self.period_lbl)
        self.period_combo = QComboBox()
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        period_row.addWidget(self.period_combo)
        period_row.addSpacing(16)

        # Which pairs of the loaded channels talk about the same people —
        # exact-surface-form overlap between their name_hits (see
        # _refresh_similar_mentions), a quick "these two cover the same
        # beat" signal before reading four columns of text. Same row as the
        # period picker; hidden below 2 loaded channels, since there's no
        # pair to compare yet.
        # "Similar MD" (left of the summary) exports the same overlap as a
        # Markdown table with links to the posts — see _build_similar_md.
        self.similar_md_btn = QPushButton(self.tr_("mentions_similar_md_btn"))
        self.similar_md_btn.setToolTip(self.tr_("mentions_similar_md_hint"))
        self.similar_md_btn.clicked.connect(self._on_similar_md_clicked)
        self.similar_md_btn.setVisible(False)
        period_row.addWidget(self.similar_md_btn)
        self.similar_mentions_lbl = QLabel("")
        self.similar_mentions_lbl.setObjectName("sectionTitle")
        self.similar_mentions_lbl.setVisible(False)
        period_row.addWidget(self.similar_mentions_lbl)

        period_row.addStretch()
        self.reload_btn = QPushButton(self.tr_("mentions_reload_btn"))
        self.reload_btn.setToolTip(self.tr_("mentions_reload_hint"))
        self.reload_btn.clicked.connect(self._on_reload_clicked)
        period_row.addWidget(self.reload_btn)
        page.addLayout(period_row)

        self.empty_lbl = QLabel(self.tr_("mentions_empty"))
        self.empty_lbl.setObjectName("hint")
        page.addWidget(self.empty_lbl)

        self._columns_row = QHBoxLayout()
        self._columns_row.setSpacing(14)
        page.addLayout(self._columns_row)
        self._columns: list[dict] = []
        for _ in range(MAX_MENTIONS_COMPARE):
            self._columns.append(self._build_column())

        page.addWidget(hline())

        table_header = QHBoxLayout()
        self.table_title = QLabel(self.tr_("mentions_table_title"))
        self.table_title.setObjectName("sectionTitle")
        table_header.addWidget(self.table_title)
        self.locate_btn = QPushButton(self.tr_("mentions_locate_btn"))
        self.locate_btn.setToolTip(self.tr_("mentions_locate_hint"))
        self.locate_btn.setStyleSheet("padding: 1px 8px;")
        self.locate_btn.clicked.connect(self._on_locate_clicked)
        table_header.addWidget(self.locate_btn)
        table_header.addStretch(1)
        self.add_row_btn = QPushButton(self.tr_("mentions_add_row_btn"))
        self.add_row_btn.clicked.connect(self._on_add_row_clicked)
        table_header.addWidget(self.add_row_btn)
        self.save_btn = QPushButton(self.tr_("mentions_save_btn"))
        self.save_btn.clicked.connect(self._on_save_clicked)
        table_header.addWidget(self.save_btn)
        page.addLayout(table_header)

        # "names" is kept at _MENTIONS_NAMES_COL_FRACTION of the table's
        # width by hand (see eventFilter/_resize_mentions_table_columns);
        # column 2 ("unclear links") is Stretch, soaking up whatever's left
        # — id/delete stay a fixed, readable size. Word-wrap plus
        # resizeRowsToContents (see _rebuild_table) means a long
        # comma-separated names/links cell grows the row instead of
        # clipping.
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_id"), self.tr_("mentions_col_names"),
            self.tr_("mentions_col_links"), ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setWordWrap(True)
        self.table.setColumnWidth(0, 240)   # 20% wider than the original 200px
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(3, 32)
        # No internal scrollbar -- the table grows to fit every row instead
        # (see _fit_mentions_table_height), so the page's own QScrollArea is
        # the only scrollbar a viewer ever has to reckon with here.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.installEventFilter(self)
        QTimer.singleShot(0, self._resize_mentions_table_columns)
        page.addWidget(self.table)
        page.addStretch(1)

        self._refresh_period_combo()
        self._rebuild_table()
        self._sync_save_btn()

    def _build_column(self) -> dict:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setSpacing(8)

        # Channel name *and* its available post range share this one line
        # (see _rebuild_column) — no separate "Parse whole history" control;
        # this just reports what's already in the stored checkpoint.
        name_lbl = QLabel("—")
        name_lbl.setObjectName("sectionTitle")
        name_lbl.setWordWrap(True)
        lay.addWidget(name_lbl)

        # Transient status line for _classify_full_history's "computing"
        # notice -- blank/hidden the rest of the time, since NER only ever
        # runs there on a handful of short link-anchor strings and usually
        # finishes before a viewer would even notice, except the model's
        # first load in a session.
        classify_status_lbl = QLabel("")
        classify_status_lbl.setObjectName("hint")
        classify_status_lbl.setVisible(False)
        lay.addWidget(classify_status_lbl)

        # 6 small stat cards, replacing the old "Summary" table -- first
        # block under the channel name, above the post texts/names found
        # detail below. See _STAT_CARD_SPECS for what each shows and
        # _update_stats_table for where the numbers come from. "Force
        # link" isn't one of these; it folds back into links_title below
        # instead (see _links_title_text/_most_repeat_link) rather than
        # taking a 7th card of its own.
        cards_grid = QGridLayout()
        cards_grid.setHorizontalSpacing(8)
        cards_grid.setVerticalSpacing(8)
        stat_cards: dict[str, StatCard] = {}
        for i, (key, title_key) in enumerate(_STAT_CARD_SPECS):
            card = StatCard(self.tr_(title_key))
            card.setMinimumHeight(_STAT_CARD_HEIGHT)
            card.set_compact(True)
            cards_grid.addWidget(card, i // _STAT_CARD_COLUMNS, i % _STAT_CARD_COLUMNS)
            stat_cards[key] = card
        for c in range(_STAT_CARD_COLUMNS):
            cards_grid.setColumnStretch(c, 1)
        lay.addLayout(cards_grid)

        # The two popups that used to be spanning table rows are now just
        # ordinary buttons side by side, plus Mentions report (see
        # _open_mentions_report) between them -- every name collected in
        # col["name_hits"] (all three extraction passes together, not just
        # the link-bearing ones Link report covers), one row each, with
        # whichever link (if any) it was found credited to.
        buttons_row = QHBoxLayout()
        unresolved_btn = QPushButton()
        buttons_row.addWidget(unresolved_btn)
        mentions_report_btn = QPushButton()
        buttons_row.addWidget(mentions_report_btn)
        report_btn = QPushButton()
        buttons_row.addWidget(report_btn)
        lay.addLayout(buttons_row)

        # Mirrors names_title below (posts/links summary instead of
        # names/mentions.md), sitting above its own table the same way —
        # rich text since "Most repeat" ends in an actual clickable link.
        links_title = QLabel(self._links_title_text(0, 0, 0, 0, None))
        links_title.setObjectName("hint")
        links_title.setTextFormat(Qt.TextFormat.RichText)
        links_title.setWordWrap(True)
        links_title.linkActivated.connect(self._open_external_link)
        lay.addWidget(links_title)

        # A real, titled "ID" column instead of the default 1/2/3 row-number
        # strip (which showed nothing useful) — the vertical header is
        # switched off so it can't show both at once.
        texts_table = QTableWidget(0, 4)
        texts_table.setObjectName("mentionsColumnTable")
        texts_table.verticalHeader().setVisible(False)
        texts_table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_post_id"), self.tr_("mentions_col_date"),
            self.tr_("mentions_col_type"), self.tr_("mentions_col_text")])
        texts_table.horizontalHeader().setStretchLastSection(True)
        texts_table.horizontalHeader().setSortIndicatorShown(True)
        texts_table.setEditTriggers(texts_table.EditTrigger.NoEditTriggers)
        texts_table.setColumnWidth(0, 64)
        texts_table.setColumnWidth(1, 92)
        texts_table.setColumnWidth(2, 90)
        texts_table.setWordWrap(True)
        texts_table.setMinimumHeight(312)  # 240 + 30%
        lay.addWidget(texts_table)

        names_title = QLabel(self._names_title_text(0, 0, {}))
        names_title.setObjectName("hint")
        lay.addWidget(names_title)

        # Name gets real room for a full Cyrillic ФИО — double Qt's 100px
        # default column width, which is all "Found"/"Posts" left it before.
        names_table = QTableWidget(0, 3)
        names_table.setObjectName("mentionsColumnTable")
        names_table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_name"), self.tr_("mentions_col_found"),
            self.tr_("mentions_col_posts")])
        names_table.horizontalHeader().setStretchLastSection(True)
        names_table.setEditTriggers(names_table.EditTrigger.NoEditTriggers)
        names_table.setColumnWidth(0, 200)
        names_table.setMinimumHeight(260)
        lay.addWidget(names_table)

        self._columns_row.addWidget(holder, 1)
        col = {"holder": holder, "name": name_lbl, "texts_table": texts_table,
              "names_table": names_table, "names_title": names_title,
              "links_title": links_title, "classify_status": classify_status_lbl,
              "stat_cards": stat_cards, "unresolved_btn": unresolved_btn,
              "report_btn": report_btn, "mentions_report_btn": mentions_report_btn,
              "fairness": {}, "link_classes": None, "channel": None,
              "posts": [], "name_hits": {},          # cached between re-sorts
              "texts_sort_col": 1, "texts_sort_desc": True}   # Date, newest first
        texts_table.horizontalHeader().sectionClicked.connect(
            lambda section, c=col: self._on_texts_header_clicked(c, section))
        texts_table.cellDoubleClicked.connect(
            lambda row, _col, c=col: self._on_texts_row_double_clicked(c, row))
        report_btn.clicked.connect(lambda _=False, c=col: self._open_link_report(c))
        unresolved_btn.clicked.connect(lambda _=False, c=col: self._open_unresolved_fair_links(c))
        mentions_report_btn.clicked.connect(lambda _=False, c=col: self._open_mentions_report(c))
        return col

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.no_extraction_lbl.setText(self.tr_("mentions_no_extraction"))
        self.period_lbl.setText(self.tr_("mentions_period_label"))
        self.reload_btn.setText(self.tr_("mentions_reload_btn"))
        self.reload_btn.setToolTip(self.tr_("mentions_reload_hint"))
        self.similar_md_btn.setText(self.tr_("mentions_similar_md_btn"))
        self.similar_md_btn.setToolTip(self.tr_("mentions_similar_md_hint"))
        self.empty_lbl.setText(self.tr_("mentions_empty"))
        self.table_title.setText(self.tr_("mentions_table_title"))
        self.locate_btn.setText(self.tr_("mentions_locate_btn"))
        self.locate_btn.setToolTip(self.tr_("mentions_locate_hint"))
        self.add_row_btn.setText(self.tr_("mentions_add_row_btn"))
        self.save_btn.setText(self.tr_("mentions_save_btn"))
        self.table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_id"), self.tr_("mentions_col_names"),
            self.tr_("mentions_col_links"), ""])
        for col in self._columns:
            col["texts_table"].setHorizontalHeaderLabels([
                self.tr_("mentions_col_post_id"), self.tr_("mentions_col_date"),
                self.tr_("mentions_col_type"), self.tr_("mentions_col_text")])
            col["names_table"].setHorizontalHeaderLabels([
                self.tr_("mentions_col_name"), self.tr_("mentions_col_found"),
                self.tr_("mentions_col_posts")])
            total_posts = self._total_posts_in_scope(col["channel"]) if col["channel"] else 0
            col["names_title"].setText(
                self._names_title_text(len(col["posts"]), total_posts, col["name_hits"]))
            tg_count, web_count, _most_url, _most_count = self._link_stats(col["posts"])
            col["links_title"].setText(self._links_title_text(
                len(col["posts"]), total_posts, tg_count, web_count,
                self._most_repeat_link(col)))
            for key, title_key in _STAT_CARD_SPECS:
                col["stat_cards"][key].title_lbl.setText(self.tr_(title_key))
            self._update_stats_table(col)
        self._refresh_similar_mentions()
        self._rebuild_table()

    # -------------------------------------------------------------- load
    def load(self, datas: list[dict]) -> None:
        """datas: up to MAX_MENTIONS_COMPARE checkpoint dicts, from the
        sidebar's Mentions multi-select (SidePanel.compare_mentions_selected).

        MentionsStore was only ever loaded once, at MentionsView construction
        (app startup) — so a channel opened here could show found/not-found
        status against a stale in-memory snapshot even though mentions.md on
        disk has since moved on (e.g. edited earlier in the very same run,
        or by hand outside the app). Re-reading it on every open fixes that;
        skipped while there are unsaved edits so reopening/reselecting
        channels mid-edit can't silently discard them. NameExceptions has no
        such unsaved state to protect (every add() persists immediately), so
        it's always reloaded here — picks up a hand-edited name_exceptions.txt
        the same way.
        """
        self.name_exceptions.load()
        if not self.mentions_store.dirty:
            self.mentions_store.load()
            self._rebuild_table()
            self._sync_save_btn()
        self._channel_id_lookup = {
            c["channel_id"]: c for c in self.channel_store.list() if c.get("channel_id")}
        self._channels = datas[:MAX_MENTIONS_COMPARE]
        self.empty_lbl.setVisible(not self._channels)
        self._refresh_period_combo()
        for i, col in enumerate(self._columns):
            if i < len(self._channels):
                col["holder"].setVisible(True)
                col["channel"] = self._channels[i]
                self._rebuild_column(col)
            else:
                col["holder"].setVisible(False)
                col["channel"] = None
        # _rebuild_column already refreshes this, but only as of *its own*
        # column — if the selection just shrank, the columns that got
        # cleared above (the `else` branch) never trigger a follow-up call,
        # so the last _rebuild_column's refresh would still be counting
        # them. One more pass here, now that every column's `channel` for
        # this load() is final, is what actually gets it right.
        self._refresh_similar_mentions()

    def _refresh_period_combo(self) -> None:
        """"All time" plus every season any loaded channel has posts in,
        newest first — one shared filter (not four independent ones) so all
        selected channels are read over the same window."""
        current = self.period_combo.currentData()
        keys: dict[tuple, str] = {}
        for ch in self._channels:
            for m in ch.get("distributions", {}).get("monthly") or []:
                try:
                    year, month = (int(x) for x in m.get("label", "").split("-"))
                except ValueError:
                    continue
                key, label = period_key_label(year, month, "season")
                keys[key] = label
        self._period_options = [_ALL_TIME] + [
            (k, v) for k, v in sorted(keys.items(), key=lambda kv: kv[0], reverse=True)]
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem(self.tr_("mentions_period_all"), _ALL_TIME)
        for key, label in self._period_options[1:]:
            self.period_combo.addItem(label, key)
        idx = self.period_combo.findData(current) if current else 0
        self.period_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.period_combo.blockSignals(False)
        self._selected_period = self.period_combo.currentData() or _ALL_TIME

    def _on_period_changed(self, _index: int) -> None:
        self._selected_period = self.period_combo.currentData() or _ALL_TIME
        for col in self._columns:
            if col["channel"] is not None:
                self._rebuild_column(col)
        self._refresh_similar_mentions()

    # ------------------------------------------------------------ columns
    def _posts_in_scope(self, ch: dict) -> list[dict]:
        # Unlike Rating/Quality scoring, reposts aren't excluded here — a
        # forwarded post's own caption can still mention someone, and its
        # forward-source is itself useful context (see _repost_source_html).
        rows = list(ch.get("rows", []) or [])
        if self._selected_period == _ALL_TIME:
            return rows
        out = []
        for r in rows:
            dt = _parse_date(r.get("date", ""))
            if dt is None:
                continue
            if period_key_label(dt.year, dt.month, "season")[0] == self._selected_period:
                out.append(r)
        return out

    def _rebuild_column(self, col: dict) -> None:
        ch = col["channel"]
        col["name"].setText(self._column_title(ch))

        # Extraction happens once per (channel, period) — re-sorting the
        # texts table (see _on_texts_header_clicked) reuses this instead of
        # re-running NER over every post again. The actual 3-pass pipeline
        # (NER, the known-name dictionary scan, the caption-pattern scan)
        # lives in app.mentions.extract_all_names_per_post, shared with the
        # headless batch export (see tools.mentions_export) so both run
        # identically instead of two copies of this loop drifting apart.
        posts = self._posts_in_scope(ch)
        all_names = extract_all_names_per_post(posts, self.mentions_store, self.name_exceptions)
        by_post: list[tuple[dict, list[str]]] = list(zip(posts, all_names))
        name_hits: dict[str, list[int]] = {}
        name_latest_ts: dict[str, int] = {}
        # A name backed by an actual Telegram link (see app.mentions.name_tg_links) is
        # higher-confidence than one from NER/dictionary-scan text alone —
        # tracked across every post in scope, not just one, so whichever
        # post first carries the link is what the green Link… button (see
        # _found_indicator) and its @username suggestion end up using.
        name_tg_link: dict[str, str] = {}
        for post, names in by_post:
            ts = int(post.get("ts", 0))
            for extracted in names:
                name_hits.setdefault(extracted, []).append(int(post.get("id", 0)))
                if ts > name_latest_ts.get(extracted, -1):
                    name_latest_ts[extracted] = ts
            for name, url in name_tg_links(names, normalize_links(post.get("links"))).items():
                name_tg_link.setdefault(name, url)
        col["posts"] = by_post
        col["name_hits"] = name_hits
        # Instant first paint from whatever was last cached to disk (see
        # _show_cached_stats), immediately overwritten below once the real,
        # period-scoped classification finishes -- cheap and always safe to
        # call, so no reason to gate it on anything.
        self._show_cached_stats(col, ch)
        # Before _populate_texts_table: it colors a post's linked names by
        # col["link_classes"] (see _highlight_names_with_links), which this
        # call is what (re)computes -- running it after would color every
        # post one rebuild stale.
        self._update_stats_table(col)

        self._populate_texts_table(col)

        channel_text = ch.get("channel") or ch.get("username") or ""
        table = col["names_table"]
        table.setRowCount(len(name_hits))
        # Newest-mention-first, matching the texts table's default order —
        # ranked by each name's own most recent hit, not insertion order.
        ordered = sorted(name_hits.items(), key=lambda kv: name_latest_ts[kv[0]], reverse=True)
        for i, (extracted, post_ids) in enumerate(ordered):
            table.setItem(i, 0, QTableWidgetItem(extracted))
            table.setCellWidget(i, 1, self._found_indicator(extracted, name_tg_link.get(extracted)))
            table.setCellWidget(i, 2, self._post_id_chips(channel_text, post_ids))
        table.resizeRowsToContents()
        total_posts = self._total_posts_in_scope(ch)
        col["names_title"].setText(self._names_title_text(len(posts), total_posts, name_hits))
        tg_count, web_count, _most_url, _most_count = self._link_stats(by_post)
        col["links_title"].setText(self._links_title_text(
            len(posts), total_posts, tg_count, web_count, self._most_repeat_link(col)))

        self._refresh_similar_mentions()

    def _update_stats_table(self, col: dict) -> None:
        """Refreshes col["stat_cards"]/the Unresolved/Link report/Mentions
        report buttons from the full-history link classification (see
        _classify_full_history) whenever the checkpoint has `all_links`,
        falling back to the pool-scoped _fairness_stats for one fetched
        before that field existed — called after every _rebuild_column,
        and from retranslate() when only the display language changed.
        Mentions report's own count is just col["name_hits"]'s size — it
        isn't classification-scoped like the other two, since it lists
        every collected name, link or not (see _open_mentions_report).

        Fairness gets app.mentions.apply_no_link_penalty's halving rule on
        top (more uncredited "No Link" mentions than "Fair" ones reads as
        much less fair in practice) — and, at All Time scope with a real
        (not pool-fallback) classification, is cached to the checkpoint's
        own `mentions_cache` (see _cache_fairness) so the Dashboard's Ethics
        card, a folder's MD export, and this same view's own instant
        first-paint (see _show_cached_stats) can show/reuse it without a
        fresh classification having run yet."""
        ch = col.get("channel")
        classes = self._classify_full_history(col) if ch else None
        col["link_classes"] = classes
        cards = col["stat_cards"]
        if classes is not None:
            fair = sum(1 for c in classes.values() if c["status"] == "fair")
            # Counted by distinct *name*, not by link -- unlike "fair"
            # (where the link itself is the identity, so one link is
            # always exactly one person), the same repeat web link often
            # credits many different people across posts (see
            # classify_channel_links), and each of those is its own fake
            # mention, not one shared between them.
            fake = sum(len(c.get("names") or []) for c in classes.values()
                      if c["status"] == "fake")
            unresolved_count = sum(1 for c in classes.values() if c["status"] == "unresolved")
            report_count = fair + fake + unresolved_count
            total_ff = fair + fake
            fairness_pct = round(fair / total_ff * 100) if total_ff else None
        else:
            stats = self._fairness_stats(col["posts"])
            col["fairness"] = stats
            fair, fake = stats["fair"], stats["fake"]
            report_count = len(stats["link_counts"])
            unresolved_count = len(stats["unresolved"])
            fairness_pct = stats["fairness_pct"]
        no_link_count = self._no_link_mention_count(col)
        fairness_pct = apply_no_link_penalty(fairness_pct, no_link_count, fair)
        none_text = self.tr_("mentions_stats_none")
        cards["fairness"].set_value(f"{fairness_pct}%" if fairness_pct is not None else none_text)
        cards["fair"].set_value(str(fair))
        cards["fake"].set_value(str(fake))
        cards["no_link"].set_value(str(no_link_count))
        full = self._link_balance_stats_full(ch) if ch else None
        total, tg, web = full if full is not None else self._link_balance_stats(col["posts"])
        cards["unique"].set_value(str(total))
        tg_pct = round(tg / total * 100) if total else None
        web_pct = round(web / total * 100) if total else None
        cards["balance"].set_value(
            self.tr_("mentions_stats_balance_value", tg=tg_pct, web=web_pct)
            if total else none_text)
        if classes is not None and ch is not None and self._selected_period == _ALL_TIME:
            self._cache_fairness(ch, {
                "fairness_pct": fairness_pct, "fair": fair, "fake": fake,
                "no_link": no_link_count, "unique": total,
                "tg_pct": tg_pct, "web_pct": web_pct,
            })
        col["report_btn"].setText(self.tr_("mentions_stats_report_row", count=report_count))
        col["unresolved_btn"].setText(
            self.tr_("mentions_stats_unresolved_row", count=unresolved_count))
        col["mentions_report_btn"].setText(
            self.tr_("mentions_report_row", count=len(col.get("name_hits") or {})))

    def _cache_fairness(self, ch: dict, cache: dict) -> None:
        """Persists `ch`'s own Summary card values back to its checkpoint's
        `mentions_cache` (see app.mentions.cache_channel_mentions), stamped
        with a fresh `calculated_at`, so the Dashboard's Ethics card, a
        folder's MD export (tools.mentions_export.run_fairness_calculate),
        and this same view's own next cold open (_show_cached_stats) can
        show/reuse it without a fresh classification having run yet — see
        _update_stats_table for when this is (and isn't) called. A no-op if
        `fairness_pct` hasn't actually changed, so switching between
        columns/periods doesn't hit disk every time (the other, cheaper
        fields moving on their own isn't worth a write without it)."""
        old = ch.get("mentions_cache") or {}
        if old.get("fairness_pct") == cache.get("fairness_pct"):
            return
        cache_channel_mentions(ch, cache)
        self.channel_store.save(ch)

    def _show_cached_stats(self, col: dict, ch: dict) -> None:
        """Paints col["stat_cards"] from ch["mentions_cache"] (see
        app.mentions.cache_channel_mentions), if there is one, before the
        real (and, for a large channel, noticeably slower) classification
        in _update_stats_table has had a chance to run — called first thing
        in _rebuild_column so opening/switching to a column shows *some*
        Summary immediately instead of a blank one, then gets silently
        overwritten moments later with the accurate, period-scoped values.
        Left untouched (not blanked) if there's no cache yet — the cards
        just keep whatever they last showed until _update_stats_table
        fills them in for real."""
        cache = ch.get("mentions_cache")
        if not cache:
            return
        cards = col["stat_cards"]
        none_text = self.tr_("mentions_stats_none")
        fairness_pct = cache.get("fairness_pct")
        cards["fairness"].set_value(f"{fairness_pct}%" if fairness_pct is not None else none_text)
        cards["fair"].set_value(str(cache.get("fair", 0)))
        cards["fake"].set_value(str(cache.get("fake", 0)))
        cards["no_link"].set_value(str(cache.get("no_link", 0)))
        total = cache.get("unique") or 0
        cards["unique"].set_value(str(total))
        tg_pct, web_pct = cache.get("tg_pct"), cache.get("web_pct")
        cards["balance"].set_value(
            self.tr_("mentions_stats_balance_value", tg=tg_pct, web=web_pct)
            if total and tg_pct is not None and web_pct is not None else none_text)

    def _no_link_mention_count(self, col: dict) -> int:
        """How many of col["name_hits"]'s distinct extracted names never
        once appeared as a link's own anchor text (see app.mentions.name_link_matches)
        across any of their posts — a bare narrative mention ("Вчера с ней
        снимали...") with no hyperlink backing it at all, the one mention
        shape classify_channel_links' link-based fair/fake/unresolved/promo
        can't see (it only ever looks at links, full history or not) —
        backs the Summary "No link mentions" row, right after Fake."""
        linked_names: set[str] = set()
        for post, names in col["posts"]:
            links = normalize_links(post.get("links"))
            for name, _link in name_link_matches(names, links):
                linked_names.add(name)
        return sum(1 for name in col["name_hits"] if name not in linked_names)

    def _all_links_in_scope(self, ch: dict) -> list[dict] | None:
        """`ch["all_links"]` (see channel_stat.py) filtered to the selected
        period the same way _posts_in_scope filters the checkpoint's `rows`
        pool — None if this checkpoint predates `all_links` entirely, so
        callers can fall back to whatever pool-scoped equivalent they had
        before it existed."""
        entries = ch.get("all_links")
        if entries is None:
            return None
        if self._selected_period == _ALL_TIME:
            return entries
        out = []
        for entry in entries:
            dt = _parse_date(entry.get("date", ""))
            if dt is not None and period_key_label(
                    dt.year, dt.month, "season")[0] == self._selected_period:
                out.append(entry)
        return out

    def _classify_full_history(self, col: dict) -> dict[str, dict] | None:
        """col["channel"]'s full-history link classification (see
        app.mentions.classify_channel_links) — None for a checkpoint
        fetched before `all_links` existed, so the caller can fall back to
        the pool-scoped _fairness_stats. Flashes col["classify_status"] to
        say classification is running: NER only ever runs there on a
        handful of short link-anchor strings (see classify_channel_links),
        but the *first* call in a session can still take a moment loading
        the model, and this view otherwise never shows anything for that
        wait. Passes this channel's own tg_identity_key (from its own
        checkpoint "link") through as `own_channel_key`, so a bare
        self-link (this channel plugging its own root, no post -- the
        exact same shape as crediting a person by linking to their
        channel) is excluded rather than misread as a mention."""
        ch = col["channel"]
        entries = self._all_links_in_scope(ch)
        if entries is None:
            return None
        status_lbl = col["classify_status"]
        status_lbl.setText(self.tr_("mentions_stats_classifying"))
        status_lbl.setVisible(True)
        QApplication.processEvents()
        own_channel_key = tg_identity_key(ch.get("link") or "")
        try:
            return classify_channel_links(
                entries, self.mentions_store, self.name_exceptions, own_channel_key)
        finally:
            status_lbl.setVisible(False)

    def _most_repeated_link_full(self, ch: dict) -> tuple[str, int] | None:
        """The single most-repeated url across ch's full scanned history
        (see _all_links_in_scope) — backs the Summary "Force link" row
        (see _most_repeat_link), unscoped to just name-anchored links the
        way the old pool-scoped Force link was. Grouped case-insensitively
        for a Telegram link (see canonical_link_key — t.me/geekography and
        t.me/Geekography are the same channel), displayed as whichever
        exact casing showed up most often under that grouping."""
        entries = self._all_links_in_scope(ch) or []
        counts: dict[str, int] = {}
        url_counts: dict[str, dict[str, int]] = {}
        for entry in entries:
            for link in entry.get("links") or []:
                url = link.get("url")
                if not url:
                    continue
                key = canonical_link_key(url)
                counts[key] = counts.get(key, 0) + 1
                uc = url_counts.setdefault(key, {})
                uc[url] = uc.get(url, 0) + 1
        if not counts:
            return None
        best_key, count = max(counts.items(), key=lambda kv: kv[1])
        display_url = max(url_counts[best_key].items(), key=lambda kv: kv[1])[0]
        return display_url, count

    def _most_repeat_link(self, col: dict) -> tuple[str, int] | None:
        """(url, count) for the Summary "Force link" row —
        _most_repeated_link_full when the checkpoint has `all_links`; else
        whatever _link_stats finds as the pool's own most-repeated link."""
        ch = col.get("channel")
        full = self._most_repeated_link_full(ch) if ch else None
        if full is not None:
            return full
        _tg, _web, most_url, most_count = self._link_stats(col["posts"])
        return (most_url, most_count) if most_url else None

    def _link_balance_stats_full(self, ch: dict) -> tuple[int, int, int] | None:
        """Same (total, tg, web) shape as _link_balance_stats, but computed
        from `ch["all_links"]` — every post channel_stat.py's iter_messages
        scan visited, not just the checkpoint's scored top-N `rows` pool —
        so "All unique links" matches a true full-history count instead of
        undercounting to whatever fraction of history happens to be in the
        pool. None for a checkpoint fetched before `all_links` existed, so
        the caller can fall back to the pool-only _link_balance_stats.
        Grouped case-insensitively for a Telegram link (see
        canonical_link_key) so t.me/geekography and t.me/Geekography count
        as one link, not two."""
        entries = self._all_links_in_scope(ch)
        if entries is None:
            return None
        seen: dict[str, bool] = {}
        for entry in entries:
            for link in entry.get("links") or []:
                url = link.get("url")
                if not url:
                    continue
                key = canonical_link_key(url)
                if key not in seen:
                    seen[key] = is_telegram_link(url)
        total = len(seen)
        tg = sum(1 for is_tg in seen.values() if is_tg)
        return total, tg, total - tg

    def _link_balance_stats(self, posts: list[tuple[dict, list[str]]]) -> tuple[int, int, int]:
        """(total unique links, unique Telegram links, unique web links)
        across every post *in the checkpoint's pool* — distinct urls, unlike
        _link_stats' tg_count/web_count (which count every occurrence, and
        feed the title above the texts table instead). Fallback for a
        checkpoint that predates `all_links` (see _link_balance_stats_full,
        which is preferred whenever it's present) — unscoped to just the
        name-anchored links _fairness_stats looks at."""
        seen: dict[str, bool] = {}
        for post, _names in posts:
            for link in normalize_links(post.get("links")):
                url = link["url"]
                if url not in seen:
                    seen[url] = is_telegram_link(url)
        total = len(seen)
        tg = sum(1 for is_tg in seen.values() if is_tg)
        return total, tg, total - tg

    def _open_mentions_report(self, col: dict) -> None:
        """The button between Unresolved fair links and Link report —
        every name collected in col["name_hits"] (all three extraction
        passes together: NER, the known-name dictionary scan, and the
        caption-pattern scan — see _rebuild_column), one row each, with
        whichever link (if any) name_link_matches found it credited to
        across its posts in scope, or "No link" if none of them did. Link
        report only ever covers link-bearing mentions (its whole shape is
        "one row per link"); this is the wider, name-first view Link
        report can't give — including every "No link mentions" card entry,
        not just the fair/fake/unresolved/promo ones.

        Sortable the same way as Link report; clicking a colored Name cell
        opens the first post that name was found in (its own post_ids,
        already tracked in col["name_hits"]); Source does the same for one
        post, or opens a pick-list of all of them for a name found in
        several (see _open_link_report_sources — the exact same helper
        Link report's own Source button uses); Open opens its link, for a
        row that has one."""
        name_hits = col.get("name_hits") or {}
        ch = col.get("channel") or {}
        ch_name = ch.get("title") or ch.get("channel") or "—"
        channel_text = ch.get("channel") or ch.get("username") or ""
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr_("mentions_report_title", channel=ch_name))
        dlg.resize(760, 420)
        lay = QVBoxLayout(dlg)
        if not name_hits:
            lay.addWidget(QLabel(self.tr_("mentions_stats_report_empty")))
            dlg.exec()
            return

        # name -> the first link any of its posts credited it to, if any.
        name_link: dict[str, str | None] = dict.fromkeys(name_hits)
        for post, names in col.get("posts") or []:
            links = normalize_links(post.get("links"))
            for name, link in name_link_matches(names, links):
                if name_link.get(name) is None:
                    name_link[name] = link["url"]

        table = QTableWidget(len(name_hits), 5)
        table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_name"), self.tr_("mentions_stats_report_col_count"),
            self.tr_("mentions_stats_report_col_url"), "", ""])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 220)  # real room for a full Cyrillic ФИО
        table.setColumnWidth(1, 60)
        table.setColumnWidth(3, 70)
        table.setColumnWidth(4, 70)
        table.setEditTriggers(table.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(False)
        no_link_text = self.tr_("mentions_report_no_link")
        for i, name in enumerate(name_hits):
            post_ids = name_hits[name]
            name_item = QTableWidgetItem(name)
            if post_ids:
                name_item.setData(
                    Qt.ItemDataRole.UserRole, _link_post_id(channel_text, post_ids[0]))
                name_item.setForeground(QColor(COLORS["accent"]))
                name_item.setToolTip(self.tr_("mentions_stats_report_name_hint"))
            table.setItem(i, 0, name_item)
            count_item = QTableWidgetItem()
            count_item.setData(Qt.ItemDataRole.DisplayRole, len(post_ids))
            table.setItem(i, 1, count_item)
            url = name_link.get(name)
            table.setItem(i, 2, QTableWidgetItem(url or no_link_text))
            if url:
                open_btn = QPushButton(self.tr_("mentions_open_link_btn"))
                open_btn.setStyleSheet("padding: 1px 8px;")
                open_btn.clicked.connect(lambda _=False, u=url: self._open_external_link(u))
                table.setCellWidget(i, 3, open_btn)
            source_btn = QPushButton(self.tr_("mentions_source_btn"))
            source_btn.setStyleSheet("padding: 1px 8px;")
            source_btn.setEnabled(bool(post_ids))
            source_btn.clicked.connect(
                lambda _=False, ids=post_ids, ct=channel_text:
                    self._open_link_report_sources(ids, ct))
            table.setCellWidget(i, 4, source_btn)
        table.setSortingEnabled(True)
        table.sortItems(1, Qt.SortOrder.DescendingOrder)
        table.cellClicked.connect(
            lambda row, column, t=table: self._on_report_name_clicked(t, row, column, 0))
        lay.addWidget(table)
        dlg.exec()

    def _open_link_report(self, col: dict) -> None:
        """Row 5's popup — every link classify_channel_links found in this
        column's full scanned history (see _classify_full_history),
        most-repeated first, as a Status/Name/Count/Link table plus
        trailing Link…/Open/Source buttons: the Status column is what
        actually lets a person audit the classifier's calls (a "Promo"
        link that's really a person's page, say), not just read the raw
        list. A "fake" link expands into one row per distinct name it was
        ever captioned with (see classify_channel_links' "names") — the
        same repeat plug link crediting a different person each time is
        exactly the case that made a single link-per-row table hide how
        many people it actually stood for.

        Four separate ways to act on a row, none overloading another:
        clicking its Name cell (colored, when it's clickable) opens the
        first original post that name/link came from; Source does the same
        for a row with just one source post, or opens a small pick-list of
        all of them if that exact name/link combination came from several
        (see classify_channel_links' "post_ids", and
        _open_link_report_sources); Link… runs the exact same
        confirm/correct + pick-or-create-row flow as every other Link…
        button in this view (_on_link_clicked), for resolving a
        "fake"/"unresolved" row into mentions.md right from here instead of
        hunting it back down in the texts table above; Open opens the
        link's own target (see _open_external_link — straight into
        Telegram for a t.me one, same as everywhere else in this view).

        Sortable by clicking any column header (Qt's own setSortingEnabled,
        not the manual sort-and-repaint _on_texts_header_clicked uses for
        the per-post texts table — this popup is built fresh every time
        it's opened, so there's nothing to preserve sort state for between
        openings). Falls back to _fairness_stats' pool-scoped,
        name-anchored-only link_counts as a plain copy-pasteable dump —
        its pre-existing shape — for a checkpoint that predates
        `all_links`."""
        classes = col.get("link_classes")
        ch = col.get("channel") or {}
        ch_name = ch.get("title") or ch.get("channel") or "—"
        channel_text = ch.get("channel") or ch.get("username") or ""
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr_("mentions_stats_report_title", channel=ch_name))
        dlg.resize(850, 420)
        lay = QVBoxLayout(dlg)
        if classes is not None:
            rows = []
            for c in classes.values():
                url = c["url"]
                post_ids = c.get("post_ids") or {}
                if c["status"] == "fake":
                    for name in c.get("names") or [c["text"]]:
                        rows.append((c["status"], name, c["count"], url, post_ids.get(name) or []))
                else:
                    rows.append((c["status"], c["text"], c["count"], url,
                                post_ids.get(c["text"]) or []))
            rows.sort(key=lambda r: r[2], reverse=True)
            if not rows:
                lay.addWidget(QLabel(self.tr_("mentions_stats_report_empty")))
            else:
                table = QTableWidget(len(rows), 7)
                table.setHorizontalHeaderLabels([
                    self.tr_("mentions_stats_report_col_status"),
                    self.tr_("mentions_col_name"),
                    self.tr_("mentions_stats_report_col_count"),
                    self.tr_("mentions_stats_report_col_url"), "", "", ""])
                table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
                table.setColumnWidth(0, 90)
                table.setColumnWidth(1, 140)
                table.setColumnWidth(2, 60)
                table.setColumnWidth(4, 70)
                table.setColumnWidth(5, 70)
                table.setColumnWidth(6, 70)
                table.setEditTriggers(table.EditTrigger.NoEditTriggers)
                # Sorting off while populating -- Qt would otherwise re-sort
                # after every single setItem call, each one against the
                # partially-filled table.
                table.setSortingEnabled(False)
                for i, (status, name, count, url, post_ids_list) in enumerate(rows):
                    table.setItem(i, 0, QTableWidgetItem(self.tr_(f"mentions_link_status_{status}")))
                    name_item = QTableWidgetItem(name)
                    if post_ids_list:
                        link = _link_post_id(channel_text, post_ids_list[0])
                        name_item.setData(Qt.ItemDataRole.UserRole, link)
                        name_item.setForeground(QColor(COLORS["accent"]))
                        name_item.setToolTip(self.tr_("mentions_stats_report_name_hint"))
                    table.setItem(i, 1, name_item)
                    count_item = QTableWidgetItem()
                    count_item.setData(Qt.ItemDataRole.DisplayRole, count)  # numeric sort, not "10" < "2"
                    table.setItem(i, 2, count_item)
                    table.setItem(i, 3, QTableWidgetItem(url))
                    # Same confirm/correct + pick-or-create-row flow as every
                    # other Link… button in this view (_on_link_clicked) --
                    # a Telegram link's own username still seeds the "+ New
                    # id…" suggestion (see _link_name_new), a web link's
                    # doesn't (there's no account to derive one from).
                    tg_url = url if is_telegram_link(url) else None
                    link_btn = QPushButton(self.tr_("mentions_link_btn"))
                    link_btn.setStyleSheet("padding: 1px 8px;")
                    link_btn.clicked.connect(
                        lambda _=False, t=table, n=name, u=tg_url, b=link_btn:
                            self._on_link_report_link_clicked(t, n, u, b))
                    table.setCellWidget(i, self._LINK_BTN_COL, link_btn)
                    open_btn = QPushButton(self.tr_("mentions_open_link_btn"))
                    open_btn.setStyleSheet("padding: 1px 8px;")
                    open_btn.clicked.connect(lambda _=False, u=url: self._open_external_link(u))
                    table.setCellWidget(i, self._LINK_BTN_COL + 1, open_btn)
                    source_btn = QPushButton(self.tr_("mentions_source_btn"))
                    source_btn.setStyleSheet("padding: 1px 8px;")
                    source_btn.setEnabled(bool(post_ids_list))
                    source_btn.clicked.connect(
                        lambda _=False, ids=post_ids_list, ct=channel_text:
                            self._open_link_report_sources(ids, ct))
                    table.setCellWidget(i, self._LINK_BTN_COL + 2, source_btn)
                table.setSortingEnabled(True)
                table.sortItems(2, Qt.SortOrder.DescendingOrder)  # most-repeated first, as before
                table.cellClicked.connect(
                    lambda row, column, t=table: self._on_report_name_clicked(t, row, column, 1))
                lay.addWidget(table)
        else:
            counts = (col.get("fairness") or {}).get("link_counts") or {}
            text = QTextEdit()
            text.setReadOnly(True)
            if counts:
                ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
                text.setPlainText("\n".join(f"{count}: {url}" for url, count in ordered))
            else:
                text.setPlainText(self.tr_("mentions_stats_report_empty"))
            lay.addWidget(text)
        dlg.exec()

    def _on_report_name_clicked(self, table: QTableWidget, row: int, column: int,
                                name_column: int) -> None:
        """The Name column of _open_link_report's or _open_mentions_report's
        table, clicked — opens the original post that name/anchor text was
        found under, if there was one to find (a row with none just isn't
        colored/clickable in the first place — see each popup's own
        construction). Any other column does nothing; `name_column` is
        which one counts as Name here (the two tables don't share a
        layout)."""
        if column != name_column:
            return
        item = table.item(row, column)
        link = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if link:
            self._open_external_link(link)

    def _open_link_report_sources(self, post_ids: list[int], channel_text: str) -> None:
        """A _open_link_report row's Source button, clicked — opens the
        one post a name/link came from directly if there's only one (same
        as clicking its Name cell would), or a small popup listing every
        one of them if there's more (reusing _post_id_chips' rich-text
        chip list — each independently clickable, hover shows the cached
        thumbnail, exactly like the Names Found table's own post-id
        column), since a single link-per-row/name-per-row can still have
        come from several different posts."""
        if not post_ids:
            return
        if len(post_ids) == 1:
            self._open_external_link(_link_post_id(channel_text, post_ids[0]))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr_("mentions_stats_report_sources_title"))
        dlg.resize(360, 160)
        lay = QVBoxLayout(dlg)
        lay.addWidget(self._post_id_chips(channel_text, post_ids))
        lay.addStretch(1)
        dlg.exec()

    _LINK_BTN_COL = 4  # _open_link_report's table -- see _on_link_report_link_clicked

    def _on_link_report_link_clicked(self, table: QTableWidget, name: str,
                                     tg_url: str | None, anchor: QPushButton) -> None:
        """A _open_link_report row's own Link… button, clicked — runs the
        usual _on_link_clicked flow, then paints that row's text green
        right in the still-open popup if it succeeded, and relabels its
        Status cell "Fair" (it now would be, if _classify_full_history ran
        again — which it won't until the column itself rebuilds). This
        popup isn't torn down and rebuilt on every mentions.md edit the way
        the rest of the view is, so without this a row you just resolved
        would look exactly as unresolved as it did before, until you closed
        and reopened the popup.

        `anchor`'s row is looked up fresh by identity (not a captured index)
        because clicking any column header re-sorts the table — a plain
        index taken when the button was built could point at a different
        row by the time this runs."""
        if not self._on_link_clicked(name, anchor, tg_url):
            return
        row = next((r for r in range(table.rowCount())
                   if table.cellWidget(r, self._LINK_BTN_COL) is anchor), -1)
        if row < 0:
            return
        for c in range(self._LINK_BTN_COL):
            item = table.item(row, c)
            if item is not None:
                item.setForeground(QColor("#22C55E"))
        status_item = table.item(row, 0)
        if status_item is not None:
            status_item.setText(self.tr_("mentions_link_status_fair"))

    def _open_unresolved_fair_links(self, col: dict) -> None:
        """Row 6's popup — every Telegram link not yet in mentions.md (see
        _classify_full_history's "unresolved" status, or _fairness_stats'
        "unresolved" as a fallback for a checkpoint that predates
        `all_links`) — exactly what turns into a "fair" mention once
        linked. Each row's own Link… button runs the same confirm/attach
        flow as the per-post Link… button (_on_link_clicked), so these can
        be resolved right here instead of hunting the name back down in
        the texts table above."""
        classes = col.get("link_classes")
        if classes is not None:
            unresolved = [(c["text"], c["url"]) for c in classes.values()
                         if c["status"] == "unresolved"]
        else:
            unresolved = (col.get("fairness") or {}).get("unresolved") or []
        ch = col.get("channel") or {}
        ch_name = ch.get("title") or ch.get("channel") or "—"
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr_("mentions_stats_unresolved_title", channel=ch_name))
        dlg.resize(560, 360)
        lay = QVBoxLayout(dlg)
        if not unresolved:
            lay.addWidget(QLabel(self.tr_("mentions_stats_unresolved_empty")))
        else:
            table = QTableWidget(len(unresolved), 3)
            table.setHorizontalHeaderLabels([
                self.tr_("mentions_col_name"), self.tr_("mentions_stats_unresolved_col_link"), ""])
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.setColumnWidth(0, 160)
            table.setEditTriggers(table.EditTrigger.NoEditTriggers)
            for i, (name, url) in enumerate(unresolved):
                table.setItem(i, 0, QTableWidgetItem(name))
                table.setItem(i, 1, QTableWidgetItem(url))
                btn = QPushButton(self.tr_("mentions_link_btn"))
                btn.clicked.connect(
                    lambda _=False, n=name, b=btn, u=url: self._on_link_clicked(n, b, u))
                table.setCellWidget(i, 2, btn)
            lay.addWidget(table)
        dlg.exec()

    def _names_title_text(self, posts_count: int, total_posts: int, name_hits: dict) -> str:
        linked = sum(1 for n in name_hits if self.mentions_store.find_row(n) is not None)
        return self.tr_("mentions_names_title_counts", posts=posts_count,
                        total_posts=total_posts, total=len(name_hits), linked=linked)

    def _total_posts_in_scope(self, ch: dict) -> int:
        """True count of posts published in the current period — not just
        `_posts_in_scope`'s count, which is capped at whatever's in the
        stored top-N sample (see channel_stat.py's module docstring: the
        pool is a few dozen posts even for a channel with thousands). Built
        from distributions.monthly's per-month totals instead, which cover
        every scanned post, not just the pool. Falls back to
        stats.total_posts for "All time" if a checkpoint predates monthly
        distributions."""
        monthly = ch.get("distributions", {}).get("monthly") or []
        if self._selected_period == _ALL_TIME:
            if monthly:
                return sum(int(m.get("count", 0) or 0) for m in monthly)
            return int(ch.get("stats", {}).get("total_posts", 0) or 0)
        total = 0
        for m in monthly:
            try:
                year, month = (int(x) for x in m.get("label", "").split("-"))
            except ValueError:
                continue
            if period_key_label(year, month, "season")[0] == self._selected_period:
                total += int(m.get("count", 0) or 0)
        return total

    def _link_stats(self, posts: list[tuple[dict, list[str]]]
                    ) -> tuple[int, int, str | None, int]:
        """(telegram_link_count, web_link_count, most_repeated_url,
        most_repeated_count) across every post's links (see
        channel_stat._extract_links / app.mentions.normalize_links) in
        this column's current scope. Counts are of link *occurrences* — the
        same url repeated across several posts counts each time, which is
        exactly what "Most repeat" is looking for — not distinct urls.
        "Telegram" is a t.me/telegram.me/telegram.org host; anything else
        counts as "Web"."""
        tg_count = web_count = 0
        counts: dict[str, int] = {}
        for post, _names in posts:
            for link in normalize_links(post.get("links")):
                url = link["url"]
                counts[url] = counts.get(url, 0) + 1
                host = urlparse(url).netloc.lower()
                if host in ("t.me", "telegram.me", "telegram.org") or host.endswith(".t.me"):
                    tg_count += 1
                else:
                    web_count += 1
        if not counts:
            return tg_count, web_count, None, 0
        most_url, most_count = max(counts.items(), key=lambda kv: kv[1])
        return tg_count, web_count, most_url, most_count

    def _fairness_stats(self, by_post: list[tuple[dict, list[str]]]) -> dict:
        """Aggregates the "fair" vs "fake" mention signal across every post
        in scope — pool-scoped fallback for a checkpoint that predates
        `all_links` (see _classify_full_history, preferred whenever it's
        available — this applies the exact same rule, just over the
        checkpoint's pool instead of its full scanned history):

        - "fair": a name whose own link anchor text is a *Telegram* link
          that resolves (see app.mentions.resolve_telegram_link) to a
          mentions.md row already — e.g. "Марго" hyperlinked to
          t.me/gotomargosha, where either that link's own username matches
          some row's id, or "Марго" itself names a row MentionsStore.find_row
          already recognizes.
        - "fake": a name whose own link anchor text points to a
          non-Telegram (web) resource instead — a "mention" that isn't
          actually a Telegram identity at all.

        Also returns: "fairness_pct" (fair / (fair + fake) * 100, rounded,
        or None if there's no fair-or-fake data to divide); "link_counts"
        (every name-anchored link's occurrence count, for the "link
        report" popup's fallback rendering — see _open_link_report); and
        "unresolved" ((name, url) pairs, one per distinct url) for
        Telegram links that *would* count as fair once added to
        mentions.md but aren't yet — see the "unknown fair links" popup.
        (The single most-repeated link itself is _most_repeat_link's job
        now, not this method's — see that docstring for why.)"""
        fair = fake = 0
        link_counts: dict[str, int] = {}
        unresolved: dict[str, str] = {}  # url -> a name it was seen anchoring
        for post, names in by_post:
            links = normalize_links(post.get("links"))
            for name, link in name_link_matches(names, links):
                url = link["url"]
                link_counts[url] = link_counts.get(url, 0) + 1
                if is_telegram_link(url):
                    row = resolve_telegram_link(url, [name], self.mentions_store)
                    if row is not None:
                        fair += 1
                    else:
                        unresolved.setdefault(url, name)
                else:
                    fake += 1
        total = fair + fake
        return {
            "fair": fair,
            "fake": fake,
            "fairness_pct": round(fair / total * 100) if total else None,
            "link_counts": link_counts,
            "unresolved": [(name, url) for url, name in unresolved.items()],
        }

    def _links_title_text(self, posts_count: int, total_posts: int,
                          tg_count: int, web_count: int, most: tuple[str, int] | None) -> str:
        """Rich-text companion to _names_title_text, sitting above the
        texts table instead of the names one — "Most repeat" is an actual
        clickable link (see _build_column's linkActivated wiring), shown
        without its scheme (bare "t.me/…" instead of "https://t.me/…") to
        keep it compact. `most` is _most_repeat_link's result — the same
        full-history-aware value the old "Summary" section's "Force link"
        row showed, now surfaced only here (that section no longer exists,
        see _STAT_CARD_SPECS)."""
        prefix = self.tr_("mentions_links_title_counts", posts=posts_count,
                          total_posts=total_posts, tg=tg_count, web=web_count)
        if most is None:
            return prefix + self.tr_("mentions_links_title_none")
        url, count = most
        display = html.escape(url.split("://", 1)[-1])
        href = html.escape(url, quote=True)
        return (prefix + self.tr_("mentions_links_title_most", count=count)
               + f' <a href="{href}">{display}</a>')

    def _refresh_similar_mentions(self) -> None:
        """"1↔2: 4, 2↔3: 2, 1↔4: 1" — exact-surface-form name overlap between
        every pair of currently loaded channels (1-based column position, not
        channel name, to keep this to one line — see the tooltip for the
        legend), most-overlapping pair first. A quick "these two cover the
        same people" signal before reading four columns of text. Hidden
        below 2 loaded channels, since there's no pair to compare yet."""
        loaded = [(i, col) for i, col in enumerate(self._columns) if col["channel"] is not None]
        self.similar_md_btn.setVisible(len(loaded) >= 2)
        if len(loaded) < 2:
            self.similar_mentions_lbl.setVisible(False)
            return
        pairs = []
        for x in range(len(loaded)):
            for y in range(x + 1, len(loaded)):
                i, a = loaded[x]
                j, b = loaded[y]
                shared = set(a["name_hits"]) & set(b["name_hits"])
                pairs.append((i + 1, j + 1, len(shared)))
        pairs.sort(key=lambda t: t[2], reverse=True)
        text = ", ".join(f"{a}↔{b}: {c}" for a, b, c in pairs)
        self.similar_mentions_lbl.setText(f'{self.tr_("mentions_similar_title")}: {text}')
        self.similar_mentions_lbl.setToolTip("  ·  ".join(
            f"{i + 1} = {col['channel'].get('title') or col['channel'].get('channel') or '—'}"
            for i, col in loaded))
        self.similar_mentions_lbl.setVisible(True)

    def _build_similar_md(self) -> str:
        """Markdown table of the people two or more loaded channels both
        mention: `| name variants | channel 1 | channel 2 | ... |`, one row
        per person, each channel cell listing every post of that channel
        that names them as a bare t.me/<channel>/<id> link. "Name variants" are every surface form found
        across the loaded columns (names_match — exact or a Russian case
        variant, e.g. "Алиса"/"Алисой"), grouped into one row. Names only
        one channel uses aren't "similar" and are left out. "" if nothing
        overlaps. Most widely shared (then most-mentioned) first."""
        loaded = [col for col in self._columns if col["channel"] is not None]
        if len(loaded) < 2:
            return ""
        # (column index, name) nodes, one per distinct casefolded surface form
        nodes: list[tuple[int, str]] = []
        for ci, col in enumerate(loaded):
            seen: set[str] = set()
            for name in col["name_hits"]:
                if name.casefold() not in seen:
                    seen.add(name.casefold())
                    nodes.append((ci, name))
        parent = list(range(len(nodes)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # Only cross-column pairs need comparing: a same-column variant
        # joins a group through whatever it matches in another column.
        for x in range(len(nodes)):
            for y in range(x + 1, len(nodes)):
                if nodes[x][0] != nodes[y][0] and names_match(nodes[x][1], nodes[y][1]):
                    parent[find(x)] = find(y)
        groups: dict[int, list[int]] = {}
        for x in range(len(nodes)):
            groups.setdefault(find(x), []).append(x)

        rows: list[tuple[int, int, list[str], list[list[int]]]] = []
        for members in groups.values():
            if len({nodes[x][0] for x in members}) < 2:
                continue
            variants = sorted({nodes[x][1] for x in members}, key=str.casefold)
            per_col: list[list[int]] = []
            for ci, col in enumerate(loaded):
                ids: set[int] = set()
                for x in members:
                    if nodes[x][0] == ci:
                        ids.update(col["name_hits"][nodes[x][1]])
                # a case variant spelled identically (casefold) elsewhere in
                # this column is the same hit list already; nothing to add
                per_col.append(sorted(ids))
            rows.append((sum(1 for ids in per_col if ids), sum(len(i) for i in per_col),
                         variants, per_col))
        if not rows:
            return ""
        rows.sort(key=lambda r: (-r[0], -r[1], r[2][0].casefold()))

        def esc(text: str) -> str:
            return text.replace("|", "\\|")

        def header(col: dict) -> str:
            ch = col["channel"]
            return esc(ch.get("title") or ch.get("channel") or ch.get("username") or "—")

        lines = ["| " + " | ".join([self.tr_("mentions_similar_md_col_names")]
                                    + [header(c) for c in loaded]) + " |",
                 "| " + " | ".join(["---"] * (len(loaded) + 1)) + " |"]
        for _n, _t, variants, per_col in rows:
            cells = [esc(", ".join(variants))]
            for col, ids in zip(loaded, per_col):
                ch = col["channel"]
                channel_text = ch.get("channel") or ch.get("username") or ""
                cells.append(", ".join(
                    _link_post_id(channel_text, i).split("://", 1)[-1] for i in ids))
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines) + "\n"

    def _on_similar_md_clicked(self) -> None:
        md = self._build_similar_md()
        if not md:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("mentions_similar_md_empty"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("mentions_similar_md_btn"), "similar_mentions.md",
            "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"), self.tr_("md_saved", path=path))

    @staticmethod
    def _column_title(ch: dict) -> str:
        """"Channel Name (posts 2019-08 — 2026-09)" — the full scanned range
        from `stats` (not just the period in view, and not just the stored
        top-N `rows` sample), so the column header itself answers "how much
        history do I actually have for this channel"."""
        name = ch.get("title") or ch.get("channel") or "—"
        stats = ch.get("stats", {})
        first, last = (stats.get("first_post_date") or "")[:7], (stats.get("last_post_date") or "")[:7]
        if not (first and last):
            return name
        return f"{name}  (posts {first} — {last})"

    def _populate_texts_table(self, col: dict) -> None:
        """Renders col["posts"] (already extracted, see _rebuild_column)
        into col["texts_table"], sorted by whichever column header was last
        clicked (col["texts_sort_col"]/"texts_sort_desc" — see
        _on_texts_header_clicked)."""
        sort_col, desc = col["texts_sort_col"], col["texts_sort_desc"]
        key = {
            0: lambda pn: pn[0].get("id", 0),
            1: lambda pn: pn[0].get("ts", 0),
            2: lambda pn: _format_media_type(pn[0]),
            3: lambda pn: (pn[0].get("full_text") or pn[0].get("text") or "").casefold(),
        }[sort_col]
        rows = sorted(col["posts"], key=key, reverse=desc)

        table = col["texts_table"]
        table.horizontalHeader().setSortIndicator(
            sort_col, Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder)
        table.setRowCount(len(rows))
        for i, (post, names) in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(str(post.get("id", ""))))
            table.setItem(i, 1, QTableWidgetItem(_short_date(post.get("date") or "")))
            table.setCellWidget(i, 2, self._type_cell(post, names))
            text = (post.get("full_text") or post.get("text") or "").strip()
            links = normalize_links(post.get("links"))
            text_lbl = QLabel(self._repost_source_html(post)
                              + self._highlight_names_with_links(
                                  text, names, links, col.get("link_classes")))
            text_lbl.setTextFormat(Qt.TextFormat.RichText)
            text_lbl.setWordWrap(True)
            text_lbl.setStyleSheet(f"font-size: {fs(12)}px;")
            text_lbl.linkActivated.connect(self._open_external_link)
            table.setCellWidget(i, 3, text_lbl)
        table.resizeRowsToContents()

    def _highlight_names_with_links(self, text: str, names: list[str], links: list[dict],
                                    link_classes: dict[str, dict] | None = None) -> str:
        """`text`, HTML-escaped, with every occurrence of each of `names`
        (exact surface form, as app.mentions.extract_person_names found it)
        wrapped in a bold accent span — so it's obvious at a glance which
        words the extractor picked out of the sentence around them. A name
        that's *also* this post's own link anchor text (see
        channel_stat._extract_links — a "text link" whose real target
        would otherwise be invisible, e.g. a model's name hyperlinked to
        her own channel) becomes a clickable link to that target instead
        of a plain span, colored by `link_classes` (col["link_classes"],
        see _classify_full_history) when it's available: muted for a
        "promo" link (the channel's own recurring plug, e.g. a repeated
        Boosty page — confidently not a mention of a person even if this
        one post's own NER pass happened to tag an overlapping span),
        green for "fair" (already in mentions.md, see
        app.mentions.resolve_telegram_link), the usual accent otherwise —
        falling back to that same green/accent split by a bare
        resolve_telegram_link lookup when `link_classes` is None (a
        checkpoint fetched before `all_links`/full-history classification
        existed)."""
        link_classes = link_classes or {}
        out = html.escape(text)
        for name in sorted(set(names), key=len, reverse=True):  # longest first, so a
            needle = html.escape(name)                          # short name inside a
            if not needle:                                      # longer one doesn't
                continue                                         # split its span
            link = next((lk for lk in links if names_match(name, lk["text"])), None)
            if link is not None:
                status = (link_classes.get(canonical_link_key(link["url"])) or {}).get("status")
                if status == "promo":
                    color = COLORS["muted"]
                elif status == "fair" or (
                        status is None and is_telegram_link(link["url"])
                        and resolve_telegram_link(link["url"], [name], self.mentions_store) is not None):
                    color = "#22C55E"
                else:
                    color = "#F97316"
                url = html.escape(link["url"], quote=True)
                replacement = (f'<a href="{url}" style="color:{color}; '
                              f'font-weight:bold; text-decoration:none;">{needle}</a>')
            else:
                replacement = f'<b style="color:#F97316;">{needle}</b>'
            out = re.sub(re.escape(needle), replacement, out)
        return out

    def _repost_source_html(self, post: dict) -> str:
        """"Forwarded from <name>" (or just "Forwarded" if no name can be
        resolved), prepended before a repost's own text — green if that
        source is already in mentions.md (the same substring-aware
        MentionsStore.find_row used for the Names Found indicator),
        otherwise muted. "" for a post that isn't a repost at all.

        `post["repost_from_id"]` (see channel_stat._repost_source) is a bare
        numeric Telegram channel id with no name attached — resolved here
        against this app's own tracked channels (self._channel_id_lookup,
        refreshed in load()) rather than any live API call, so an untracked
        source just falls back to its byline (`repost_from_author`, if the
        origin channel signed the post) or the unnamed default."""
        if not post.get("repost"):
            return ""
        info = self._channel_id_lookup.get(post.get("repost_from_id"))
        name = (info and (info.get("title") or info.get("channel"))) or post.get("repost_from_author") or ""
        label = self.tr_("mentions_forwarded_from", name=name) if name else self.tr_("mentions_forwarded")
        found = bool(name) and self.mentions_store.find_row(name) is not None
        color = "#22C55E" if found else COLORS["muted"]
        return f'<b style="color:{color};">{html.escape(label)}</b><br>'

    def _type_cell(self, post: dict, names: list[str]) -> QWidget:
        """Media-type text plus a second line with either a compact Link…
        button, if this post has any extracted names, or (nothing was
        highlighted — extraction found no name at all here) a Mark…
        button instead — both reuse the exact same confirm/correct +
        pick-target flow as the "Names found" section's own Link button
        (see _on_link_clicked and _on_post_names_menu), just without having
        to scroll down to it. Mark… starts that flow with an empty name
        (_confirm_name_text's dialog just shows a blank field instead of
        prefilling one to correct) — it's for the case NER/dictionary-scan
        missed a real mention entirely, not for correcting one they did
        catch."""
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        type_lbl = QLabel(_format_media_type(post))
        type_lbl.setStyleSheet(f"font-size: {fs(12)}px;")
        lay.addWidget(type_lbl)
        if names:
            tg_links = name_tg_links(names, normalize_links(post.get("links")))
            btn = QPushButton(self.tr_("mentions_link_btn"))
            style = f"padding: 0px 6px; font-size: {fs(12)}px;"
            if len(names) == 1:
                name = names[0]
                tg_url = tg_links.get(name)
                if tg_url:
                    style += " color: #22C55E; font-weight: bold;"
                    btn.setToolTip(self.tr_("mentions_link_tg_hint"))
                btn.setStyleSheet(style)
                btn.clicked.connect(
                    lambda _=False, n=name, b=btn, u=tg_url: self._on_link_clicked(n, b, u))
            else:
                if tg_links:
                    style += " color: #22C55E; font-weight: bold;"
                btn.setStyleSheet(style)
                btn.clicked.connect(
                    lambda _=False, ns=names, b=btn, tl=tg_links: self._on_post_names_menu(ns, b, tl))
            lay.addWidget(btn)
        else:
            btn = QPushButton(self.tr_("mentions_mark_btn"))
            btn.setStyleSheet(f"padding: 0px 6px; font-size: {fs(12)}px;")
            btn.setToolTip(self.tr_("mentions_mark_hint"))
            btn.clicked.connect(lambda _=False, b=btn: self._on_link_clicked("", b))
            lay.addWidget(btn)
        return holder

    def _on_post_names_menu(self, names: list[str], anchor: QPushButton,
                            tg_links: dict[str, str] | None = None) -> None:
        """A post can carry more than one extracted name — let the user
        pick which one the Link… button (see _type_cell) should attach."""
        tg_links = tg_links or {}
        menu = QMenu(self)
        for n in names:
            act = menu.addAction(n)
            act.triggered.connect(
                lambda _=False, nm=n, b=anchor, u=tg_links.get(n): self._on_link_clicked(nm, b, u))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _on_texts_header_clicked(self, col: dict, section: int) -> None:
        if col["texts_sort_col"] == section:
            col["texts_sort_desc"] = not col["texts_sort_desc"]
        else:
            col["texts_sort_col"] = section
            col["texts_sort_desc"] = False
        self._populate_texts_table(col)

    def _on_texts_row_double_clicked(self, col: dict, row: int) -> None:
        """Opens that row's post in Telegram — the ID column (see
        _populate_texts_table) already carries the post id regardless of
        the table's current sort order, so it's read straight off the
        rendered cell rather than re-deriving it from col["posts"]."""
        ch = col["channel"]
        item = col["texts_table"].item(row, 0)
        if ch is None or item is None or not item.text():
            return
        channel_text = ch.get("channel") or ch.get("username") or ""
        link = _link_post_id(channel_text, int(item.text()))
        self._open_external_link(link)

    # A pill-like "button" rendered as plain rich text, not a real
    # QPushButton -- see _found_indicator's docstring for why.
    _FOUND_PILL_STYLE = ("padding:1px 6px; border:1px solid #666; "
                        "border-radius:3px; text-decoration:none;")

    def _found_indicator(self, name: str, tg_url: str | None = None) -> QWidget:
        """A green "✓ id" QLabel if `name` is already in mentions.md;
        otherwise a single word-wrapped rich-text QLabel with two
        button-styled links, Link…/Ignore. A real QPushButton pair inside a
        custom flow-layout container was tried here first (see git history)
        so both could sit side by side and wrap onto a second line when the
        column's too narrow — but Qt's own row-height fitting sized that
        custom container wildly wrong in both directions (way too tall, then,
        once patched, still clipped) no matter how the fix was tuned. A
        plain QLabel word-wraps and reports its height correctly on its own
        — the exact same mechanism already used, without any such bug, by
        the post texts table's own wrapped Text column — so it's used here
        too instead of continuing to fight Qt's custom-layout row sizing.
        Which link was clicked is encoded in its href and dispatched by
        _on_found_link_activated; linkHovered swaps the tooltip to match,
        since a QLabel has only one tooltip for the whole widget, not one
        per link."""
        row = self.mentions_store.find_row(name)
        if row is not None:
            lbl = QLabel(f"✓ {row['id']}")
            lbl.setStyleSheet(f"color: #22C55E; font-size: {fs(12)}px;")
            return lbl
        link_style = self._FOUND_PILL_STYLE
        if tg_url:
            # Backed by an actual Telegram link (see app.mentions.name_tg_links), not
            # just NER/dictionary-scan text -- worth calling out.
            link_style += " color:#22C55E; font-weight:bold;"
        html_text = (
            f'<a href="link" style="{link_style}">'
            f'{html.escape(self.tr_("mentions_link_btn"))}</a> '
            f'<a href="ignore" style="{self._FOUND_PILL_STYLE}">'
            f'{html.escape(self.tr_("mentions_ignore_btn"))}</a>')
        lbl = QLabel(html_text)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size: {fs(12)}px;")
        lbl.linkActivated.connect(
            lambda href, n=name, w=lbl, u=tg_url: self._on_found_link_activated(href, n, w, u))
        lbl.linkHovered.connect(
            lambda href, w=lbl, tg=bool(tg_url): self._on_found_link_hovered(href, w, tg))
        return lbl

    def _on_found_link_hovered(self, href: str, label: QLabel, has_tg_hint: bool) -> None:
        if href == "ignore":
            label.setToolTip(self.tr_("mentions_ignore_hint"))
        elif href == "link" and has_tg_hint:
            label.setToolTip(self.tr_("mentions_link_tg_hint"))
        else:
            label.setToolTip("")

    def _on_found_link_activated(self, href: str, name: str, anchor: QWidget,
                                 tg_url: str | None) -> None:
        if href == "ignore":
            self._on_ignore_clicked(name)
        else:
            self._on_link_clicked(name, anchor, tg_url)

    def _on_ignore_clicked(self, name: str) -> None:
        """"This isn't a name" — the opposite of Link…, for a bare
        extraction that's plainly wrong (e.g. "Мастер-класс"). Added to
        NameExceptions (declension-aware, so this one entry covers every
        grammatical form — see app.mentions), then every loaded column is
        re-extracted so it disappears from this session immediately, not
        just future ones."""
        self.name_exceptions.add(name)
        for col in self._columns:
            if col["channel"] is not None:
                self._rebuild_column(col)

    def _post_id_chips(self, channel_text: str, post_ids: list[int]) -> QWidget:
        """Every post id as its own clickable link inside one word-wrapped
        rich-text QLabel — see _found_indicator's docstring for why this
        isn't a custom flow-layout container of individual chip widgets
        anymore. linkHovered swaps in the right thumbnail preview for
        whichever id is currently under the cursor, since a QLabel only has
        one tooltip for the whole widget, not one per link."""
        html_text = " ".join(
            f'<a href="{_link_post_id(channel_text, pid)}">#{pid}</a>' for pid in post_ids)
        lbl = QLabel(html_text)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size: {fs(12)}px;")
        lbl.linkActivated.connect(self._open_external_link)
        lbl.linkHovered.connect(
            lambda url, w=lbl: w.setToolTip(_thumb_tooltip(url, url) if url else ""))
        return lbl

    def _confirm_name_text(self, name: str) -> str | None:
        """NER models occasionally tag a span that isn't actually a person
        (a capitalized post-title word misread as PER, a name's extraction
        cut off mid-phrase, etc.) — this is the one point before that text
        is written into mentions.md where it can be corrected, or the link
        abandoned outright instead of poisoning the store. None if cancelled
        or left blank."""
        text, ok = QInputDialog.getText(
            self, self.tr_("mentions_confirm_name_title"),
            self.tr_("mentions_confirm_name_prompt"), text=name)
        text = text.strip()
        if not ok or not text:
            return None
        return text

    def _on_link_clicked(self, name: str, anchor: QWidget, tg_url: str | None = None) -> bool:
        """Returns whether `name` (or its confirmed/corrected form) ended up
        linked into mentions.md by the time this returns — checked via
        MentionsStore.find_row rather than threading a success flag through
        the popup menu's triggered signals, since by the time menu.exec()
        returns, whichever action it fired (if any) has already run to
        completion. Used by _on_link_report_link_clicked to know whether to
        paint that row green; every other caller just ignores it, same as
        before this had a return value at all."""
        corrected = self._confirm_name_text(name)
        if corrected is None:
            return False
        menu = QMenu(self)
        for row in self.mentions_store.rows:
            act = menu.addAction(row["id"])
            act.triggered.connect(lambda _=False, r=row, n=corrected: self._link_name_to_row(n, r))
        if self.mentions_store.rows:
            menu.addSeparator()
        new_act = menu.addAction(self.tr_("mentions_link_new"))
        new_act.triggered.connect(
            lambda _=False, n=corrected, u=tg_url: self._link_name_new(n, u))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        return self.mentions_store.find_row(corrected) is not None

    def _link_name_to_row(self, name: str, row: dict) -> None:
        self.mentions_store.attach_name(row, name)
        self._after_mentions_edit()

    def _link_name_new(self, name: str, tg_url: str | None = None) -> None:
        # A Telegram link's own username is a much better id suggestion
        # than the name text itself (see app.mentions.name_tg_links) — "Марго" isn't a
        # usable id, but "@gotomargosha" (from https://t.me/gotomargosha) is.
        suggested_id = (_tg_username_from_url(tg_url) if tg_url else None) or name
        id_, ok = QInputDialog.getText(
            self, self.tr_("mentions_link_new"), self.tr_("mentions_new_id_prompt"),
            text=suggested_id)
        id_ = id_.strip()
        if not ok or not id_:
            return
        self.mentions_store.add_row(id_, names=[name])   # name is already attached
        self._after_mentions_edit()

    def _after_mentions_edit(self) -> None:
        self._rebuild_table()
        self._sync_save_btn()
        for col in self._columns:
            if col["channel"] is not None:
                self._rebuild_column(col)
        self._refresh_similar_mentions()

    # -------------------------------------------------------- master table
    def _rebuild_table(self) -> None:
        self._loading_table = True
        try:
            self.table.setRowCount(len(self.mentions_store.rows))
            for i, row in enumerate(self.mentions_store.rows):
                self.table.setItem(i, 0, QTableWidgetItem(row.get("id", "")))
                self.table.setItem(i, 1, QTableWidgetItem(", ".join(row.get("names") or [])))
                self.table.setItem(i, 2, QTableWidgetItem(", ".join(row.get("links") or [])))
                del_btn = QPushButton("🗑")
                del_btn.setStyleSheet("padding: 1px 4px;")
                del_btn.setToolTip(self.tr_("mentions_delete_row_hint"))
                del_btn.clicked.connect(lambda _=False, r=row: self._on_delete_row_clicked(r))
                self.table.setCellWidget(i, 3, del_btn)
            self.table.resizeRowsToContents()
            self._fit_mentions_table_height()
        finally:
            self._loading_table = False

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_table:
            return
        row_idx, col_idx = item.row(), item.column()
        if row_idx >= len(self.mentions_store.rows):
            return
        row = self.mentions_store.rows[row_idx]
        text = item.text().strip()
        if col_idx == 0:
            if row.get("id", "") != text:
                row["id"] = text
                self.mentions_store.dirty = True
        elif col_idx == 1:
            names = [n.strip() for n in text.split(",") if n.strip()]
            if row.get("names") != names:
                row["names"] = names
                self.mentions_store.dirty = True
        elif col_idx == 2:
            links = [link.strip() for link in text.split(",") if link.strip()]
            if row.get("links") != links:
                row["links"] = links
                self.mentions_store.dirty = True
        self.table.resizeRowsToContents()
        self._fit_mentions_table_height()
        self._sync_save_btn()

    def _on_add_row_clicked(self) -> None:
        self.mentions_store.add_row("")
        self._rebuild_table()
        self._sync_save_btn()
        self.table.setCurrentCell(len(self.mentions_store.rows) - 1, 0)
        self.table.editItem(self.table.item(len(self.mentions_store.rows) - 1, 0))

    def _on_locate_clicked(self) -> None:
        """Reveal mentions.md in the OS file manager, selected where that's
        supported (macOS/Windows) rather than just opening its folder."""
        path = self.mentions_store.path
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        elif sys.platform.startswith("win"):
            subprocess.run(["explorer", f"/select,{path}"], check=False)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _on_reload_clicked(self) -> None:
        """Explicit re-read of mentions.md from disk — the same reload
        `load()` already does automatically on opening channels (see its
        docstring), but on demand, e.g. after hand-editing the file while
        this view is already open. Confirms first if there are unsaved
        edits, since a reload discards them."""
        if self.mentions_store.dirty:
            reply = QMessageBox.question(
                self, self.tr_("app_title"), self.tr_("mentions_reload_confirm"))
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.mentions_store.load()
        self._after_mentions_edit()

    def _on_delete_row_clicked(self, row: dict) -> None:
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("mentions_delete_row_confirm", id=row.get("id", "")))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.mentions_store.remove_row(row)
        self._after_mentions_edit()

    def _sync_save_btn(self) -> None:
        self.save_btn.setEnabled(self.mentions_store.dirty)

    def _on_save_clicked(self) -> None:
        self.mentions_store.save()
        self._sync_save_btn()

    # ------------------------------------------------------------- events
    def hideEvent(self, event) -> None:
        # Autosave leaving the view — see MentionsStore.save (no-op if clean).
        self.mentions_store.save()
        self._sync_save_btn()
        super().hideEvent(event)

__all__ = ["MAX_MENTIONS_COMPARE", "MentionsView"]
