"""High-Quality Posts view: for one folder and period, a grid of individual
*posts* (not channels) ranked by content quality — proportional engagement
relative to that post's own views, not raw view count. Click a card to
open the post on Telegram.

The scoring formula itself (ERV%, the reaction/comment/forward/viral-excess
weighting, the K-tuned gauge) lives in app.scoring, not here — it's shared
with the per-channel Dashboard's Quality trend line and recent-posts row
(app.ui.dashboard_view). Likewise the actual post-card widget (thumbnail,
placeholder icon, gauge, counts) is `PostCard` in app.ui.widgets, reused by
both views for the same reason: neither view imports UI code from the
other, so there's no risk of a circular import between them.

Posts come from each channel's checkpoint `rows` — the stored top-N sample
(see folder_stat_view's module docstring), not a fresh fetch — filtered to
the selected period (Seasonal/Monthly/Year, same picker as Folder Stats,
backed by app.periods), scored and sorted best-first, then balanced across
channels and capped at MAX_POSTS_SHOWN in _channel_limited_entries() (after
the per-channel limits, not before — see that method's docstring) to keep
the grid manageable, and varied, for a big folder/period.

Thumbnails are opt-in: nothing in this app downloads post media by default
(checkpoints only ever store text/counts), so the header's "Fetch media"
button runs a small on-demand background job (app.tools.media_fetch) that
downloads just the *smallest* thumbnail (photo, video, or round/circle
video note) for the posts currently on screen into a local cache
(app.media_cache) — a voice message, audio file or other document never
gets a real thumbnail fetched at all, just its static placeholder below,
since none of those have a meaningful visual preview. A card without a
cached thumbnail shows a placeholder icon by media type instead (🏞️
photo, ▶️ video, ⚪️ circle message, 🎙️ voice/audio, 💾 other file, 📖
text-only post). Both `media_type` and `comments` are new per-row
checkpoint fields (see channel_stat.py) — posts from a checkpoint fetched
before they existed show the 📖 placeholder (same as a genuine text post)
and 0 comments until refetched.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from statistics import median

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..config import Config
from ..errors import friendly_os_error
from ..folders import FolderStore
from ..media_cache import thumbnail_path
from ..periods import YEAR_WINDOW_OPTIONS, period_key_label, year_window_cutoff
from ..scoring import post_gauge_value, post_score_raw, score_tooltip
from ..store import ChannelStore
from ..tools.media_fetch import run_thumbnail_cache
from ..worker import ToolWorker
from .dashboard_view import ChannelReportDialog, build_post_link, fmt_int
from .widgets import (
    PostCard, POST_CARD_HEIGHT as CARD_HEIGHT, POST_CARD_PLACEHOLDERS as _MEDIA_PLACEHOLDERS,
    POST_CARD_TEXT_LINES as _TEXT_LINES, POST_CARD_TEXT_PIXEL_SIZE as _TEXT_PIXEL_SIZE,
    POST_CARD_TEXT_WIDTH as _TEXT_WIDTH, POST_CARD_THUMB_HEIGHT as _THUMB_HEIGHT,
    POST_CARD_WIDTH as CARD_WIDTH, elide_to_lines as _elide_to_lines, format_media_counts,
)

_GRID_SPACING = 14
_PAGE_MARGIN_LEFT = 34
_PAGE_MARGIN_RIGHT = 40
MAX_POSTS_SHOWN = 80   # keeps a big folder/period from being an unbounded grid
TOP_AUTHORS_SHOWN = 5   # "Best N authors" summary at the top of the Tg Links export
FOLDER_HITMAKERS_SHOWN = 10   # "Top 10 {folder} hitmakers" per folder in the MD export
_EXPORT_MD_TEXT_LEN = 160
_TG_LINKS_SNIPPET_LEN = 12
MONTHS_FULL = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

_MEDIA_LOG_WIDTH = 260
_MEDIA_LOG_PIXEL_SIZE = 12   # matches QLabel#hint's font-size in theme.py


def _parse_date(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _channel_label(ch: dict) -> str:
    username = ch.get("username") or ""
    if username:
        return f"@{username}"
    return ch.get("title") or ch.get("channel") or ch.get("key", "?")


def _channel_ref(ch: dict) -> str:
    """Identifier to resolve/link this channel — same fallback order
    build_post_link and the media-fetch tool use."""
    return ch.get("channel") or ch.get("username") or ch.get("key", "")


def _channel_avg_views(ch: dict) -> float:
    """The reference a post's views are compared against for the quality
    gauge's viral-excess term (app.scoring): the channel's *recent* average
    (`stats.avg_views_recent`) so a channel that has merely grown doesn't
    score every recent post as a breakout, falling back to the lifetime
    `stats.avg_views` for older checkpoints / thin recent history, and 0 if
    the checkpoint predates both."""
    stats = ch.get("stats", {})
    return float(stats.get("avg_views_recent") or stats.get("avg_views", 0) or 0)


def _channel_members(ch: dict) -> int:
    return int(ch.get("info", {}).get("members", 0) or 0)


# Per-channel post cap for cross-channel balance on a big Top-N (see
# _channel_limited_entries) — smaller channels get a tighter cap so a
# handful of very engaged small channels can't fill most of a big
# Top 50+/100+/etc. slate. None = no cap (7000+ followers).
_FOLLOWERS_CAP_BREAKPOINTS = [(2000, 5), (3000, 6), (4000, 7), (5000, 8), (7000, 9)]


def _followers_cap(members: int) -> int | None:
    for threshold, cap in _FOLLOWERS_CAP_BREAKPOINTS:
        if members < threshold:
            return cap
    return None


# Also gated on Top 50+ (see _channel_limited_entries): at Top 50-200, a
# post this weak on *both* fronts at once doesn't belong in a big ranked
# slate, but either one alone is enough to keep it (a high-view post with 0
# shares is still fine, and vice versa). Top 250 gets its own pair *and* its
# own, stricter rule (see _quality_floor): both floors must hold, not just
# one — that much bigger a slate pulls in a much longer tail, and a real
# share count is what actually proves a post wasn't just a view-count
# fluke, so a view count alone no longer excuses a weak share count there.
_TOP50_MIN_VIEWS = 4000
_TOP50_MIN_SHARES = 11
_TOP250_MIN_VIEWS = 3000
_TOP250_MIN_SHARES = 30


def _quality_floor(max_posts: int) -> tuple[int, int, bool] | None:
    """(min_views, min_shares, require_both) for the selected Top-N tier,
    biggest matching tier first — None below Top 50, where natural
    competition for slots already keeps a weak post from surfacing at all.
    `require_both` tells _channel_limited_entries how to judge a post
    against the pair: False (Top 50-200) drops it only if it's under *both*
    floors at once; True (Top 250) drops it if it's under *either* one —
    see the constants above."""
    if max_posts >= 250:
        return _TOP250_MIN_VIEWS, _TOP250_MIN_SHARES, True
    if max_posts >= 50:
        return _TOP50_MIN_VIEWS, _TOP50_MIN_SHARES, False
    return None

# One large channel (uncapped by _followers_cap) can still run away with a
# big Top-N — e.g. 43 of its posts vs ~3 for the typical channel. When the
# per-channel-limit combo is set to "Rein in dominant channel"
# (_ANOMALY_SENTINEL), any channel holding both more than _ANOMALY_CAP slots
# *and* at least _ANOMALY_FACTOR× the median channel's slot count is capped
# to _ANOMALY_CAP and the slate refilled, so the freed slots spread down the
# ranked list. Re-checked a few times in case the redistribution then makes
# another channel run away. Only on Top 50+ (top50_rules).
_ANOMALY_FACTOR = 3.0
_ANOMALY_CAP = 11
_ANOMALY_SENTINEL = -1   # tg_links_limit_combo data value for that option


class ContentQualityView(QWidget):
    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                 cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self.cfg = cfg
        self._channels: list[dict] = []
        self._post_entries: list[dict] = []
        self._cards: list[PostCard] = []
        self._cols = 0
        self._period_mode = "season"
        self._selected_period_key: tuple | None = None
        self._period_btns: dict[tuple, QPushButton] = {}
        self._year_window_key = "all"
        self._year_btns: dict[str, QPushButton] = {}
        self._media_worker: ToolWorker | None = None
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The whole page — header, pickers and the card grid — scrolls as
        # one unit (rather than a fixed header above a separately-scrolling
        # grid), so the header/pickers scroll out of the way too instead of
        # permanently eating vertical space.
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        outer.addWidget(self.page_scroll)

        page = QVBoxLayout(body)
        page.setContentsMargins(_PAGE_MARGIN_LEFT, 28, _PAGE_MARGIN_RIGHT, 24)
        # Explicit per-pair spacing below (not a single page.setSpacing())
        # so the picker-to-grid gap can be tighter than the rest, now that
        # there's no divider line sitting between them to justify the same
        # 16px used elsewhere on the page.
        page.setSpacing(0)

        header = QHBoxLayout()
        self.title_lbl = QLabel(self.tr_("nav_content_quality"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        header.addStretch(1)
        # Last message from the running (or just-finished) media fetch —
        # mainly so a FloodWait cooldown ("FloodWait: sleeping 30s…", see
        # app.tools.common.retry) is visible instead of the button just
        # looking stuck.
        self.media_log_lbl = QLabel("")
        self.media_log_lbl.setObjectName("hint")
        self.media_log_lbl.setFixedWidth(_MEDIA_LOG_WIDTH)
        header.addWidget(self.media_log_lbl)
        self.fetch_media_btn = QPushButton(self.tr_("cqi_fetch_media"))
        self.fetch_media_btn.setToolTip(self.tr_("cqi_fetch_media_hint"))
        self.fetch_media_btn.clicked.connect(self._on_fetch_media_clicked)
        header.addWidget(self.fetch_media_btn)
        self.tg_links_btn = QPushButton(self.tr_("cqi_tg_links"))
        self.tg_links_btn.setToolTip(self.tr_("cqi_tg_links_hint"))
        self.tg_links_btn.clicked.connect(self._on_tg_links_clicked)
        header.addWidget(self.tg_links_btn)
        # A richer export than Tg Links: one row per post with its cached
        # thumbnail embedded as a base64 data: URI — see
        # _build_export_md_table. Saves straight to a file (like every
        # other "MD" button in the app) instead of the Tg Links button's
        # copy-from-a-dialog flow, since a table full of embedded images is
        # far too large to usefully preview in a plain-text dialog.
        self.export_md_btn = QPushButton(self.tr_("cqi_export_md_btn"))
        self.export_md_btn.setToolTip(self.tr_("cqi_export_md_hint"))
        self.export_md_btn.clicked.connect(self._on_export_md_clicked)
        header.addWidget(self.export_md_btn)
        # How many top posts to keep overall — the same cap the grid, the
        # per-channel limit and the Tg Links list all share (see
        # _channel_limited_entries).
        self.max_posts_combo = QComboBox()
        self.max_posts_combo.setToolTip(self.tr_("cqi_max_posts_hint"))
        for n in (25, 50, 60, 80, 100, 150, 200, 250):
            self.max_posts_combo.addItem(self.tr_("cqi_max_posts_n", n=n), n)
        self.max_posts_combo.setCurrentIndex(self.max_posts_combo.findData(MAX_POSTS_SHOWN))
        header.addWidget(self.max_posts_combo)
        # Caps how many posts from the same channel can appear — both in
        # the grid below and in the generated Tg Links list, so one
        # prolific channel can't fill up either one. Connected to
        # _on_filter_changed further down, once the grid it needs
        # to rebuild actually exists.
        self.tg_links_limit_combo = QComboBox()
        self.tg_links_limit_combo.setToolTip(self.tr_("cqi_tg_links_limit_hint"))
        self.tg_links_limit_combo.addItem(self.tr_("cqi_tg_links_limit_none"), 0)
        # No fixed cap, but reins in a runaway channel — see
        # _channel_limited_entries's anomaly-cap block.
        self.tg_links_limit_combo.addItem(self.tr_("cqi_tg_links_limit_anomaly"),
                                          _ANOMALY_SENTINEL)
        for n in (7, 6, 5, 4, 3, 2):
            self.tg_links_limit_combo.addItem(self.tr_("cqi_tg_links_limit_n", n=n), n)
        header.addWidget(self.tg_links_limit_combo)
        # Excludes posts from channels below this follower count from the
        # ranking entirely — a tiny channel's best post can still have a
        # sky-high ERV% just from a small, highly-engaged audience, which
        # otherwise crowds out posts from bigger channels worth featuring.
        self.min_followers_combo = QComboBox()
        self.min_followers_combo.setToolTip(self.tr_("cqi_min_followers_hint"))
        self.min_followers_combo.addItem(self.tr_("cqi_min_followers_none"), 0)
        for n in (100, 300, 500, 1000, 1500, 2000):
            self.min_followers_combo.addItem(self.tr_("cqi_min_followers_n", n=n), n)
        header.addWidget(self.min_followers_combo)
        # Off by default — a text-only post still has a real ERV% score and
        # may well be one of the best-ranked ones; this just lets you focus
        # on media when that's what you're after (e.g. before a "Fetch
        # media" pass, or when hunting for repost-worthy visuals).
        self.hide_non_media_chk = QCheckBox(self.tr_("cqi_hide_non_media"))
        self.hide_non_media_chk.setToolTip(self.tr_("cqi_hide_non_media_hint"))
        header.addWidget(self.hide_non_media_chk)
        page.addLayout(header)
        page.addSpacing(16)

        # One row: period mode on the left, folder picker on the right —
        # they're unrelated controls but both narrow, so sharing a row
        # keeps the header more compact than stacking each on its own line.
        self.mode_halfyear_btn = QPushButton(self.tr_("period_mode_halfyear"))
        self.mode_halfyear_btn.setObjectName("ghost")
        self.mode_halfyear_btn.setCheckable(True)
        self.mode_season_btn = QPushButton(self.tr_("period_mode_season"))
        self.mode_season_btn.setObjectName("ghost")
        self.mode_season_btn.setCheckable(True)
        self.mode_month_btn = QPushButton(self.tr_("period_mode_month"))
        self.mode_month_btn.setObjectName("ghost")
        self.mode_month_btn.setCheckable(True)
        self.mode_year_btn = QPushButton(self.tr_("period_mode_year"))
        self.mode_year_btn.setObjectName("ghost")
        self.mode_year_btn.setCheckable(True)
        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.setExclusive(True)
        self._mode_btn_group.addButton(self.mode_halfyear_btn)
        self._mode_btn_group.addButton(self.mode_season_btn)
        self._mode_btn_group.addButton(self.mode_month_btn)
        self._mode_btn_group.addButton(self.mode_year_btn)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_halfyear_btn)
        mode_row.addWidget(self.mode_season_btn)
        mode_row.addWidget(self.mode_month_btn)
        mode_row.addWidget(self.mode_year_btn)
        mode_row.addStretch(1)
        self.pick_lbl = QLabel(self.tr_("folder_stat_pick_folder"))
        mode_row.addWidget(self.pick_lbl)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        self.folder_combo.setMinimumWidth(220)
        mode_row.addWidget(self.folder_combo)
        page.addLayout(mode_row)
        page.addSpacing(16)

        self.picker_container = QWidget()
        self.picker_lay = QVBoxLayout(self.picker_container)
        self.picker_lay.setContentsMargins(0, 0, 0, 0)
        self.picker_lay.setSpacing(8)
        page.addWidget(self.picker_container)
        page.addSpacing(6)

        self.empty_channels_lbl = QLabel(self.tr_("folder_stat_empty_channels"))
        self.empty_channels_lbl.setObjectName("navEmpty")
        self.empty_channels_lbl.setWordWrap(True)
        page.addWidget(self.empty_channels_lbl)

        self.empty_posts_lbl = QLabel(self.tr_("cqi_empty_posts"))
        self.empty_posts_lbl.setObjectName("navEmpty")
        self.empty_posts_lbl.setWordWrap(True)
        page.addWidget(self.empty_posts_lbl)

        self.grid_holder = QWidget()
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setSpacing(_GRID_SPACING)
        self.grid.setContentsMargins(0, 6, 8, 6)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        page.addWidget(self.grid_holder, 1)
        # A trailing addStretch — not just grid_holder's own stretch=1 —
        # because when grid_holder is hidden (no folders/no channels/no
        # posts yet, see _reload_channels/_render_grid) Qt orphans a hidden
        # widget's stretch instead of honoring it, and spreads the leftover
        # height across the other, visible items instead; this
        # always-present spacer item is what actually keeps the empty-state
        # message pinned to the top (see the same fix in FolderStatView).
        page.addStretch(1)

        self.page_scroll.setWidget(body)

        # Widgets above exist now, so it's safe to wire signals and set the
        # default mode (which fires the toggle once).
        self.mode_halfyear_btn.toggled.connect(lambda c: self._on_mode_toggled("halfyear", c))
        self.mode_season_btn.toggled.connect(lambda c: self._on_mode_toggled("season", c))
        self.mode_month_btn.toggled.connect(lambda c: self._on_mode_toggled("month", c))
        self.mode_year_btn.toggled.connect(lambda c: self._on_mode_toggled("year", c))
        self.mode_season_btn.setChecked(True)
        self.tg_links_limit_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.hide_non_media_chk.toggled.connect(lambda _c: self._rebuild_cards())
        self.min_followers_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.max_posts_combo.currentIndexChanged.connect(self._on_filter_changed)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout_grid()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # main_window.py calls refresh() *before* stack.setCurrentWidget(),
        # so the very first grid layout can be computed while this view
        # isn't the visible stack page yet and its viewport hasn't settled
        # to its final width — re-check the instant it's actually shown,
        # regardless of what its width looked like when refresh() ran.
        super().showEvent(event)
        self._relayout_grid()

    # --------------------------------------------------------------- data
    _ALL_FOLDERS = "__all__"

    def refresh(self) -> None:
        """Reload folders + this folder's channels from disk. Call whenever
        the view is shown, or folders/channels changed elsewhere."""
        current_id = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem(self.tr_("cqi_all_folders"), self._ALL_FOLDERS)
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        idx = self.folder_combo.findData(current_id) if current_id else -1
        self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.folder_combo.blockSignals(False)
        self._reload_channels()

    def _on_folder_changed(self, _index: int) -> None:
        self._reload_channels()

    def _reload_channels(self) -> None:
        folder_id = self.folder_combo.currentData()
        self._channels = []
        if folder_id == self._ALL_FOLDERS:
            keys = [s["key"] for s in self.channel_store.list()]
        elif folder_id:
            keys = [k for k, fid in self.folder_store.assignments.items() if fid == folder_id]
        else:
            keys = []
        for key in keys:
            data = self.channel_store.load(key)
            if data:
                data.setdefault("key", key)
                self._channels.append(data)

        # The combo always has at least "All folders", so there's no
        # "create a folder first" empty state to show anymore — only
        # whether the selected scope (a folder, or all of them) actually
        # has any channels in it.
        self.empty_channels_lbl.setVisible(not self._channels)

        self._rebuild_period_picker()

    # ------------------------------------------------------- period picker
    @staticmethod
    def _clear_layout(layout) -> None:
        # hide() first — takeAt() only unmanages a widget from the layout,
        # it doesn't hide it, and deleteLater() doesn't actually destroy it
        # until the next event-loop pass. The constructor's initial
        # setChecked(True) and the first refresh() from main_window both
        # rebuild this picker before either yields back to the event loop,
        # so without hide() the old buttons stay visibly stacked under the
        # new ones for a frame.
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                ContentQualityView._clear_layout(sub)

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
        if isinstance(self._year_window_key, int):
            return str(self._year_window_key)
        for key, label_key, _days in YEAR_WINDOW_OPTIONS:
            if key == self._year_window_key:
                return self.tr_(label_key)
        return ""

    def _collect_calendar_years(self) -> list[int]:
        """Every calendar year with at least one post, across *all* tracked
        channels regardless of folder — same stability rule as
        _collect_all_period_keys, newest first."""
        years: set[int] = set()
        for summary in self.channel_store.list():
            data = self.channel_store.load(summary["key"])
            if not data:
                continue
            for m in data.get("distributions", {}).get("monthly") or []:
                if not int(m.get("count", 0) or 0):
                    continue
                try:
                    years.add(int(m.get("label", "").split("-")[0]))
                except (ValueError, IndexError):
                    continue
        return sorted(years, reverse=True)

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
        # Calendar years (1 Jan - 31 Dec), newest first — distinct from the
        # rolling windows above: those are relative to today, these are a
        # fixed calendar year regardless of when you're looking. Int keys
        # (vs. the windows' string keys) is what lets _year_window_label/
        # _collect_posts tell the two kinds apart.
        for year in self._collect_calendar_years():
            btn = QPushButton(str(year))
            btn.setObjectName("ghost")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._year_btn_group.addButton(btn)
            btn.toggled.connect(lambda checked, k=year: self._on_year_window_toggled(k, checked))
            self._year_btns[year] = btn
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

    def _on_year_window_toggled(self, key: str | int, checked: bool) -> None:
        if not checked:
            return
        self._year_window_key = key
        self._selected_period_key = ("year", key)
        self._rebuild_posts()

    def _collect_all_period_keys(self, mode: str) -> list[tuple[tuple, str]]:
        """Every period key/label across *all* tracked channels, regardless
        of folder — matches Folder Stats: the picker buttons stay put as
        you switch folders, only the posts below change."""
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
                key, label = period_key_label(year, month, mode)
                keys[key] = label
        return sorted(keys.items(), key=lambda kv: kv[0], reverse=True)

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
            target = (self._selected_period_key if self._selected_period_key in self._period_btns
                      else keys_labels[0][0])
            self._period_btns[target].setChecked(True)
        else:
            self._selected_period_key = None
            self._rebuild_posts()

    def _on_period_button_toggled(self, key: tuple, checked: bool) -> None:
        if not checked:
            return
        self._selected_period_key = key
        self._rebuild_posts()

    # ----------------------------------------------------------- posts
    def _collect_posts(self) -> list[dict]:
        """Every stored row across the selected folder's channels that
        falls in the selected period, each scored — sorted best-first.

        Deliberately NOT capped at MAX_POSTS_SHOWN here: that cap is applied
        in _channel_limited_entries() instead, *after* the per-channel
        limit — capping here first would let a restrictive per-channel
        limit (e.g. "2 per ch") starve the grid down to far fewer than
        MAX_POSTS_SHOWN cards just because the best 60-ish posts overall
        happened to cluster in a handful of channels, when there are
        plenty more further down this full ranked list to fill the rest
        of the quota from."""
        mode = self._period_mode
        target_key = self._selected_period_key
        cutoff = None
        upper = None
        if mode == "year":
            if isinstance(self._year_window_key, int):
                # A specific calendar year (1 Jan - 31 Dec), not a rolling
                # window from today — needs an upper bound too, unlike the
                # windows below which always run through the present.
                cutoff = datetime(self._year_window_key, 1, 1, tzinfo=timezone.utc)
                upper = datetime(self._year_window_key + 1, 1, 1, tzinfo=timezone.utc)
            else:
                cutoff = year_window_cutoff(self._year_window_days())
        posts: list[dict] = []
        for ch in self._channels:
            for row in ch.get("rows", []) or []:
                # Forwarded in from another channel — its engagement isn't
                # this channel's content (app.tools.channel_stat._is_repost).
                if row.get("repost"):
                    continue
                if mode == "year":
                    dt = _parse_date(row.get("date", ""))
                    if dt is None or (cutoff is not None and dt < cutoff):
                        continue
                    if upper is not None and dt >= upper:
                        continue
                else:
                    dt = _parse_date(row.get("date", ""))
                    if dt is None:
                        continue
                    key, _label = period_key_label(dt.year, dt.month, mode)
                    if key != target_key:
                        continue
                posts.append({"channel": ch, "row": row,
                             "raw_score": post_score_raw(row, _channel_avg_views(ch))})
        posts.sort(key=lambda p: p["raw_score"], reverse=True)
        return posts

    def _rebuild_posts(self) -> None:
        self._post_entries = self._collect_posts() if self._channels else []
        self._rebuild_cards()

    def _channel_limited_entries(self) -> list[dict]:
        """`self._post_entries` (already best-first, unbounded — see
        _collect_posts), with non-media posts and posts from channels below
        the minimum-followers threshold dropped first (see hide_non_media_chk/
        min_followers_combo). Once the max-posts combo is at Top 50 or
        above, two more balance rules kick in (see `top50_rules` below):
        a per-channel cap scaled by follower count (_followers_cap) — on
        top of, and whichever is stricter than, the Tg Links limit combo's
        own per-channel cap (0 = no cap); and dropping any post that's weak
        on *both* views and shares at once (neither alone disqualifies it),
        against a floor that gets stricter at Top 250 (see _quality_floor).
        With the per-channel-limit combo set to "Rein in dominant channel"
        a third joins them: an anomaly cap on a single channel running away
        with the slate (_ANOMALY_FACTOR / _ANOMALY_CAP). Finally capped to at
        most the max-posts combo's current value overall (default MAX_POSTS_SHOWN) —
        shared by the grid and the generated Tg Links/MD exports so all three
        always agree on which posts are "in scope". Applying the per-channel
        cap before the overall cap means a restrictive limit still fills up
        to the overall cap by reaching further down the ranked list across
        other channels, the same as when no limit is set at all."""
        limit_data = self.tg_links_limit_combo.currentData() or 0
        anomaly_cap_on = limit_data == _ANOMALY_SENTINEL
        limit = limit_data if isinstance(limit_data, int) and limit_data > 0 else 0
        hide_non_media = self.hide_non_media_chk.isChecked()
        min_followers = int(self.min_followers_combo.currentData() or 0)
        max_posts = int(self.max_posts_combo.currentData() or MAX_POSTS_SHOWN)
        # Below Top 50, natural competition for slots already keeps any one
        # channel from dominating and weak posts from surfacing at all —
        # these extra rules only matter once there's enough room for them
        # to slip through.
        top50_rules = max_posts >= 50
        quality_floor = _quality_floor(max_posts)

        def _fill(anomaly_caps: dict[str, int]) -> tuple[list[dict], dict[str, int]]:
            seen: dict[str, int] = {}
            out: list[dict] = []
            for entry in self._post_entries:
                if len(out) >= max_posts:
                    break
                if hide_non_media and not (entry["row"].get("media_type") or ""):
                    continue
                members = _channel_members(entry["channel"])
                if min_followers and members < min_followers:
                    continue
                if quality_floor:
                    min_views, min_shares, require_both = quality_floor
                    views = int(entry["row"].get("views", 0) or 0)
                    shares = int(entry["row"].get("forwards", 0) or 0)
                    below_views, below_shares = views < min_views, shares < min_shares
                    fails = (below_views or below_shares) if require_both else (below_views and below_shares)
                    if fails:
                        continue
                key = _channel_ref(entry["channel"])
                effective_limit = limit or None
                if top50_rules:
                    fcap = _followers_cap(members)
                    if fcap is not None:
                        effective_limit = (fcap if effective_limit is None
                                           else min(effective_limit, fcap))
                acap = anomaly_caps.get(key)
                if acap is not None:
                    effective_limit = (acap if effective_limit is None
                                       else min(effective_limit, acap))
                if effective_limit is not None and seen.get(key, 0) >= effective_limit:
                    continue
                seen[key] = seen.get(key, 0) + 1
                out.append(entry)
            return out, seen

        out, seen = _fill({})
        # A channel dwarfing the slate (> _ANOMALY_CAP slots and ≥
        # _ANOMALY_FACTOR× the median channel) → cap it and refill; re-check
        # in case the freed slots then let another channel run away. Bounded
        # so a degenerate folder can't loop. Opt-in (anomaly_cap_chk).
        caps: dict[str, int] = {}
        if anomaly_cap_on and top50_rules and len(seen) >= 3:
            for _ in range(5):
                mid = median(seen.values())
                runaway = {k: _ANOMALY_CAP for k, n in seen.items()
                           if k not in caps and n > _ANOMALY_CAP
                           and n >= _ANOMALY_FACTOR * max(mid, 1)}
                if not runaway:
                    break
                caps.update(runaway)
                out, seen = _fill(caps)
        return out

    def _on_filter_changed(self, _index: int) -> None:
        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        # See ContentQualityView's earlier channel-card version of this
        # bug: takeAt() unmanages a widget from the layout but doesn't hide
        # it, and deleteLater() doesn't destroy it until the next event
        # loop pass — hide immediately so a rapid double-rebuild can't
        # briefly show stale cards on top of fresh ones.
        for i in reversed(range(self.grid.count())):
            item = self.grid.takeAt(i)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._cards = []

        has_channels = bool(self._channels)
        has_posts = bool(self._post_entries)
        self.empty_posts_lbl.setVisible(has_channels and not has_posts)
        self.grid_holder.setVisible(has_posts)

        for entry in self._channel_limited_entries():
            ch = entry["channel"]
            row = entry["row"]
            card = PostCard()

            label = _channel_label(ch)
            text = _elide_to_lines(row.get("text") or "", _TEXT_WIDTH, _TEXT_LINES,
                                   _TEXT_PIXEL_SIZE)
            channel_ref = _channel_ref(ch)
            link = build_post_link(channel_ref, row.get("id", 0))

            thumb_path = thumbnail_path(channel_ref, row.get("id", 0))
            thumb = None
            if thumb_path.exists():
                pix = QPixmap(str(thumb_path))
                if not pix.isNull():
                    thumb = pix.scaled(
                        CARD_WIDTH - 20, _THUMB_HEIGHT, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
            placeholder = _MEDIA_PLACEHOLDERS.get(row.get("media_type") or "", "")

            views = int(row.get("views", 0) or 0)
            reactions = int(row.get("reactions", 0) or 0)
            forwards = int(row.get("forwards", 0) or 0)
            comments = int(row.get("comments", 0) or 0)
            counts_text = (f"{fmt_int(views)}👁️ {fmt_int(comments)}💬\n"
                          f"{fmt_int(reactions)}❤️ {fmt_int(forwards)}🔄")
            gauge_value = post_gauge_value(entry["raw_score"])
            tooltip = score_tooltip(self.tr_, label, row, _channel_avg_views(ch),
                                    entry["raw_score"], gauge_value, fmt_int)

            card.set_data(label, thumb, placeholder, text, gauge_value, counts_text,
                         link, tooltip, media_counts=row.get("media_counts"))
            self._cards.append(card)

        self._cols = 0  # force _relayout_grid to actually place the new cards
        self._relayout_grid()

    def _relayout_grid(self) -> None:
        if not self._cards:
            return
        avail = max(self.page_scroll.viewport().width()
                    - _PAGE_MARGIN_LEFT - _PAGE_MARGIN_RIGHT, CARD_WIDTH)
        cols = max(1, (avail + _GRID_SPACING) // (CARD_WIDTH + _GRID_SPACING))
        if cols == self._cols:
            return
        self._cols = cols
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i)
        for i, card in enumerate(self._cards):
            self.grid.addWidget(card, i // cols, i % cols)
        rows = -(-len(self._cards) // cols)  # ceil division
        for r in range(256):
            self.grid.setRowStretch(r, 0)
        for c in range(256):
            self.grid.setColumnStretch(c, 0)
        self.grid.setRowStretch(rows, 1)
        self.grid.setColumnStretch(cols, 1)

    # ------------------------------------------------------- fetch media
    def _on_fetch_media_clicked(self) -> None:
        entries = self._channel_limited_entries()
        if not entries or self._media_worker is not None:
            return
        # Only posts that don't already have a cached thumbnail (see
        # media_cache.thumbnail_path) — media_fetch.run_thumbnail_cache
        # would skip re-downloading them anyway, but filtering here too
        # means the "Fetching N post(s)" progress/log is accurate and no
        # get_messages call is spent on posts nothing is going to be
        # downloaded for.
        to_fetch = [e for e in entries if not thumbnail_path(
            _channel_ref(e["channel"]), e["row"].get("id", 0)).exists()]
        if not to_fetch:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("cqi_fetch_media_all_cached"))
            return
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        if not conn["api_id"] or not conn["api_hash"]:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("cqi_fetch_media_need_login"))
            return

        posts = [{"channel": _channel_ref(e["channel"]), "id": e["row"].get("id", 0),
                 "ids": e["row"].get("ids") or [e["row"].get("id", 0)]}
                 for e in to_fetch]
        self.fetch_media_btn.setEnabled(False)
        self.fetch_media_btn.setText(self.tr_("cqi_fetch_media_running"))
        self._set_media_log("")
        self._media_worker = ToolWorker(run_thumbnail_cache, {"posts": posts}, conn, parent=self)
        self._media_worker.sig_log.connect(self._set_media_log)
        self._media_worker.sig_ask.connect(self._on_media_ask)
        self._media_worker.sig_done.connect(self._on_fetch_media_done)
        self._media_worker.start()

    def _set_media_log(self, msg: str) -> None:
        # Measured with a fresh QFont at the QSS pixel size, not the
        # label's own .fontMetrics() — an unpolished widget's QFont doesn't
        # yet reflect the global QSS font-size rule (see _elide_to_lines).
        msg = msg.strip()
        font = QFont()
        font.setPixelSize(_MEDIA_LOG_PIXEL_SIZE)
        elided = QFontMetrics(font).elidedText(
            msg, Qt.TextElideMode.ElideRight, _MEDIA_LOG_WIDTH)
        self.media_log_lbl.setText(elided)
        self.media_log_lbl.setToolTip(msg)

    def _on_media_ask(self, _kind: str, _prompt: str) -> None:
        # This button assumes the session already used for regular channel
        # fetches is still authorized — if Telegram unexpectedly needs a
        # fresh login here, send the user to Config rather than building a
        # second inline login flow just for this.
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("cqi_fetch_media_login_required"))
        if self._media_worker is not None:
            self._media_worker.request_cancel()

    def _on_fetch_media_done(self, ok: bool, msg: str) -> None:
        self.fetch_media_btn.setEnabled(True)
        self.fetch_media_btn.setText(self.tr_("cqi_fetch_media"))
        self._media_worker = None
        if ok:
            self._rebuild_cards()  # pick up newly cached thumbnails
        elif msg and msg != "Login cancelled":
            QMessageBox.warning(self, self.tr_("app_title"), msg)

    # ------------------------------------------------------------ tg links
    def _current_period_label(self) -> str:
        if self._period_mode == "year":
            return self._year_window_label()
        btn = self._period_btn_group.checkedButton()
        return btn.text() if btn else ""

    def _build_tg_links_text(self) -> str:
        header = self.tr_("cqi_tg_links_header", folder=self.folder_combo.currentText(),
                          period=self._current_period_label())
        entries = self._channel_limited_entries()
        lines = [header, ""]

        # "Best N authors": channels ranked by how many of the posts below
        # are theirs — same `entries` the post list uses, so the two
        # sections always agree on what's "in scope" for this export.
        counts: dict[str, int] = {}
        channel_by_ref: dict[str, dict] = {}
        for entry in entries:
            ref = _channel_ref(entry["channel"])
            counts[ref] = counts.get(ref, 0) + 1
            channel_by_ref.setdefault(ref, entry["channel"])
        top_authors = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:TOP_AUTHORS_SHOWN]
        if top_authors:
            lines.append(self.tr_("cqi_tg_links_top_authors_title", n=len(top_authors)))
            for i, (ref, count) in enumerate(top_authors, 1):
                label = _channel_label(channel_by_ref[ref])
                lines.append(f"{i}. {count} {label}")
            lines.append("")

        for i, entry in enumerate(entries, 1):
            row = entry["row"]
            score = round(post_gauge_value(entry["raw_score"]))
            text = " ".join((row.get("text") or "").split())
            snippet = (text[:_TG_LINKS_SNIPPET_LEN]
                      + ("…" if len(text) > _TG_LINKS_SNIPPET_LEN else ""))
            link = build_post_link(_channel_ref(entry["channel"]), row.get("id", 0))
            link = link.removeprefix("https://")
            lines.append(f"{i}. {score} 📊 {snippet} {link}")
        return "\n".join(lines)

    def _on_tg_links_clicked(self) -> None:
        if not self._post_entries:
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("cqi_empty_posts"))
            return
        ChannelReportDialog(self, self.i18n, self._build_tg_links_text(),
                            title=self.tr_("cqi_tg_links_dialog_title")).exec()

    def _build_hitmakers_section(self, entries: list[dict]) -> list[str]:
        """"Top 10 {folder} hitmakers" per folder — channels ranked by how
        many of `entries` are theirs, one section per folder that actually
        has posts in scope (folder list order, "No folder" last), so a
        folder with only a couple of contributing channels still gets a
        section, just a shorter one. Uses the same `entries` the table
        itself lists, so both always agree on what's "in scope"."""
        folder_name = {f["id"]: f["name"] for f in self.folder_store.list_folders()}
        counts_by_folder: dict[str | None, dict[str, int]] = {}
        channel_by_ref: dict[str, dict] = {}
        for entry in entries:
            ch = entry["channel"]
            ref = _channel_ref(ch)
            fid = self.folder_store.folder_for_channel(ch.get("key", ""))
            counts_by_folder.setdefault(fid, {})
            counts_by_folder[fid][ref] = counts_by_folder[fid].get(ref, 0) + 1
            channel_by_ref.setdefault(ref, ch)

        folder_order = [f["id"] for f in self.folder_store.list_folders()] + [None]
        lines: list[str] = []
        for fid in folder_order:
            counts = counts_by_folder.get(fid)
            if not counts:
                continue
            name = folder_name.get(fid, self.tr_("folder_none"))
            top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:FOLDER_HITMAKERS_SHOWN]
            lines.append(self.tr_("cqi_md_hitmakers_title", n=FOLDER_HITMAKERS_SHOWN, folder=name))
            for i, (ref, count) in enumerate(top, 1):
                label = _channel_label(channel_by_ref[ref]).replace("|", "")
                lines.append(f"{i}. {count} {label}")
            lines.append("")
        return lines

    def _build_export_md_table(self) -> str:
        """"Top 10 {folder} hitmakers" per folder (see
        _build_hitmakers_section), then one row per post (same
        `_channel_limited_entries()` scope as the grid/Tg Links/hitmakers
        above), with its cached thumbnail embedded directly as a base64
        `data:image/jpeg` URI — self-contained, unlike a plain link, so the
        table still shows real previews with nothing else to fetch. A post
        with no cached thumbnail (see the "Fetch media" button) just gets
        an em dash instead of a broken image reference."""
        entries = self._channel_limited_entries()
        lines = self._build_hitmakers_section(entries)

        headers = [self.tr_("cqi_md_col_channel"), self.tr_("cqi_md_col_score"),
                  self.tr_("cqi_md_col_thumbnail"), self.tr_("cqi_md_col_media"),
                  self.tr_("cqi_md_col_text"), self.tr_("col_views"), self.tr_("col_shares"),
                  self.tr_("cqi_md_col_link")]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        for entry in entries:
            ch = entry["channel"]
            row = entry["row"]
            channel_ref = _channel_ref(ch)
            label = _channel_label(ch).replace("|", "")
            score = round(post_gauge_value(entry["raw_score"]))
            link = build_post_link(channel_ref, row.get("id", 0))

            thumb_path = thumbnail_path(channel_ref, row.get("id", 0))
            thumb_md = "—"
            if thumb_path.exists():
                try:
                    b64 = base64.b64encode(thumb_path.read_bytes()).decode("ascii")
                    thumb_md = f"![](data:image/jpeg;base64,{b64})"
                except OSError:
                    pass

            media = format_media_counts(row.get("media_counts") or {})
            if not media:
                media = _MEDIA_PLACEHOLDERS.get(row.get("media_type") or "", "")

            text = " ".join((row.get("text") or "").split()).replace("|", "")
            if len(text) > _EXPORT_MD_TEXT_LEN:
                text = text[:_EXPORT_MD_TEXT_LEN] + "…"

            views = fmt_int(int(row.get("views", 0) or 0))
            shares = fmt_int(int(row.get("forwards", 0) or 0))
            lines.append(f"| {label} | {score} | {thumb_md} | {media} | {text} | "
                        f"{views} | {shares} | {link} |")
        return "\n".join(lines) + "\n"

    def _on_export_md_clicked(self) -> None:
        if not self._post_entries:
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("cqi_empty_posts"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("cqi_export_md_btn"), "high_quality_posts.md", "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_export_md_table())
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"), self.tr_("md_saved", path=path))

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_content_quality"))
        self.fetch_media_btn.setToolTip(self.tr_("cqi_fetch_media_hint"))
        if self._media_worker is None:
            self.fetch_media_btn.setText(self.tr_("cqi_fetch_media"))
        self.tg_links_btn.setText(self.tr_("cqi_tg_links"))
        self.tg_links_btn.setToolTip(self.tr_("cqi_tg_links_hint"))
        self.export_md_btn.setText(self.tr_("cqi_export_md_btn"))
        self.export_md_btn.setToolTip(self.tr_("cqi_export_md_hint"))
        if self.folder_combo.count():
            self.folder_combo.setItemText(0, self.tr_("cqi_all_folders"))
        self.max_posts_combo.setToolTip(self.tr_("cqi_max_posts_hint"))
        for i in range(self.max_posts_combo.count()):
            n = self.max_posts_combo.itemData(i)
            self.max_posts_combo.setItemText(i, self.tr_("cqi_max_posts_n", n=n))
        self.tg_links_limit_combo.setToolTip(self.tr_("cqi_tg_links_limit_hint"))
        self.tg_links_limit_combo.setItemText(0, self.tr_("cqi_tg_links_limit_none"))
        for i in range(1, self.tg_links_limit_combo.count()):
            n = self.tg_links_limit_combo.itemData(i)
            if n == _ANOMALY_SENTINEL:
                self.tg_links_limit_combo.setItemText(i, self.tr_("cqi_tg_links_limit_anomaly"))
            else:
                self.tg_links_limit_combo.setItemText(i, self.tr_("cqi_tg_links_limit_n", n=n))
        self.min_followers_combo.setToolTip(self.tr_("cqi_min_followers_hint"))
        self.min_followers_combo.setItemText(0, self.tr_("cqi_min_followers_none"))
        for i in range(1, self.min_followers_combo.count()):
            n = self.min_followers_combo.itemData(i)
            self.min_followers_combo.setItemText(i, self.tr_("cqi_min_followers_n", n=n))
        self.hide_non_media_chk.setText(self.tr_("cqi_hide_non_media"))
        self.hide_non_media_chk.setToolTip(self.tr_("cqi_hide_non_media_hint"))
        self.pick_lbl.setText(self.tr_("folder_stat_pick_folder"))
        self.empty_channels_lbl.setText(self.tr_("folder_stat_empty_channels"))
        self.empty_posts_lbl.setText(self.tr_("cqi_empty_posts"))
        self.mode_halfyear_btn.setText(self.tr_("period_mode_halfyear"))
        self.mode_season_btn.setText(self.tr_("period_mode_season"))
        self.mode_month_btn.setText(self.tr_("period_mode_month"))
        self.mode_year_btn.setText(self.tr_("period_mode_year"))
        for key, btn in self._year_btns.items():
            if isinstance(key, int):
                continue  # calendar-year buttons are just numbers, nothing to translate
            label_key = next(lk for k, lk, _d in YEAR_WINDOW_OPTIONS if k == key)
            btn.setText(self.tr_(label_key))
        # Card tooltips embed translated text (see _score_tooltip) — rebuild
        # so they don't stay stuck in whatever language was active when the
        # cards were last built.
        self._rebuild_cards()
