"""Ad Campaign view: "I want N followers for B money by date D" -> a plan of
paid ad placements across the channels this app tracks.

Top to bottom: one row of inputs (target followers, period, budget +
currency, price per follower, optionally *your* channel); a tile strip with
what the plan costs and is expected to bring; if your channel is tracked, a
card with its followers now -> after the plan beside its five best posts
by Quality (the ones to repost into partner channels or lift media from);
the ad timeline — a Gantt with one block per placement, its width the
placement's period; and, below it, the recommendations for actually
reaching the target. Click a block to swap in another channel, or to mark the
channel as not selling ads (it is replaced and excluded from later plans).

All the maths — the ad-list price formula, prime-era detection, the planner —
lives in app.ad_campaign, which documents it; this module only gathers the
inputs, keeps the manual edits, and renders. Every input change re-plans from
scratch (live, debounced); a manual replace/remove edits the current plan in
place until the next input change. Excluded channels persist until reset.
"""
from __future__ import annotations

import html
from datetime import date, timedelta

from PySide6.QtCore import QDate, QLocale, QPoint, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from .. import ad_campaign as ac
from ..folders import FolderStore
from ..media_cache import thumbnail_path
from ..scoring import score_tooltip
from ..store import ChannelStore
from ..tools.media_fetch import run_thumbnail_cache
from ..worker import ToolWorker
from .ad_gantt import STATE_COLOR_KEYS, AdGanttChart
from .dashboard_view import ChannelReportDialog, build_post_link, fmt_int
from .theme import COLORS, fs
from .widgets import (
    POST_CARD_PLACEHOLDERS, POST_CARD_TEXT_LINES, POST_CARD_TEXT_PIXEL_SIZE,
    POST_CARD_TEXT_WIDTH, POST_CARD_THUMB_HEIGHT, POST_CARD_WIDTH, PostCard,
    SectionCard, StatCard, elide_to_lines, hline, open_external_link,
)

_MAX_TARGET = 100_000_000
_MAX_BUDGET = 2_000_000_000
_MAX_UNTIL_DAYS = 365
_DEFAULT_UNTIL_DAYS = 21
_DEFAULT_CURRENCY = "₽"
_REPLAN_DELAY_MS = 250
_TOP_POSTS = 5
_POST_GAP = 14
_OWN_CARD_MIN_WIDTH = 210
_OWN_CARD_MAX_WIDTH = 340
_OWN_CARD_MARGINS = 40   # StatCard's own left+right content margins, with slack
_OWN_VALUE_PX = 26       # QLabel#statValue's size (theme.py), before zoom
_OWN_VALUE_MIN_PX = 12
_MAX_ALTERNATIVES = 12
_STATE_ICONS = {"prime": "🔥", "warm": "📈", "steady": "➖", "cooling": "❄️", "unknown": "❔"}
_MEDIA_TIP_TYPES = ("photo", "video", "video_note")


class AdCampaignView(QWidget):
    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                 tag_store=None, cfg=None, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self.tag_store = tag_store
        self.cfg = cfg

        self._dirty = True                  # needs _reload() on next show
        self._summaries: list[dict] = []    # ChannelStore.list()
        self._cache: dict[str, tuple[str, dict | None]] = {}   # key -> (fetched_at, candidate)
        self._candidates: list[dict] = []
        self._excluded: set[str] = set()
        self._own: dict | None = None       # {"key", "tag", "folder_id", "followers"}
        self._own_data: dict | None = None
        self._blocks: list[dict] = []
        self._window_start = date.today()
        self._window_days = ac.PERIOD_TWO_WEEKS
        self._post_widgets: list[QWidget] = []
        self._post_cols = 0
        self._own_entries: list[dict] = []   # best posts currently shown
        self._media_worker: ToolWorker | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_REPLAN_DELAY_MS)
        self._timer.timeout.connect(self._replan)

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

        holder = QWidget()
        page = QVBoxLayout(holder)
        page.setContentsMargins(34, 28, 40, 24)
        page.setSpacing(16)

        header = QVBoxLayout()
        header.setSpacing(2)
        self.title_lbl = QLabel()
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel()
        self.sub_lbl.setObjectName("pageSub")
        self.sub_lbl.setWordWrap(True)
        header.addWidget(self.sub_lbl)
        page.addLayout(header)

        self.hint_lbl = QLabel()
        self.hint_lbl.setObjectName("hint")
        self.hint_lbl.setWordWrap(True)
        page.addWidget(self.hint_lbl)

        page.addWidget(self._inputs_row())
        page.addWidget(hline())

        self.empty_lbl = QLabel()
        self.empty_lbl.setObjectName("navEmpty")
        self.empty_lbl.setWordWrap(True)
        page.addWidget(self.empty_lbl)

        page.addLayout(self._tiles_row())
        page.addWidget(self._posts_card())
        page.addWidget(self._gantt_card())
        page.addWidget(self._recs_card())
        page.addStretch(1)
        self.page_scroll.setWidget(holder)
        self._retranslate_static()

    def _inputs_row(self) -> QWidget:
        """The row the whole view starts from — see the module docstring. A
        grid (labels / inputs / extras) rather than stacked columns, so a
        label that wraps in a long translation or at Large zoom can't push
        its input lower than its neighbours'."""
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(4, 1)

        self.target_lbl = QLabel()
        self.target_spin = QSpinBox()
        self.target_spin.setRange(1, _MAX_TARGET)
        self.target_spin.setValue(ac.DEFAULT_TARGET_FOLLOWERS)
        self.target_spin.setGroupSeparatorShown(True)
        self.target_spin.setFixedWidth(104)

        self.period_lbl = QLabel()
        self.period_combo = QComboBox()
        for _ in range(3):
            self.period_combo.addItem("")
        self.period_combo.setItemData(0, ac.PERIOD_TWO_WEEKS)
        self.period_combo.setItemData(1, ac.PERIOD_ONE_MONTH)
        self.period_combo.setItemData(2, 0)   # until a chosen date
        self.period_combo.setFixedWidth(124)
        self.until_edit = QDateEdit()
        self.until_edit.setCalendarPopup(True)
        self.until_edit.setDisplayFormat("dd.MM.yyyy")
        today = QDate.currentDate()
        self.until_edit.setMinimumDate(today)
        self.until_edit.setMaximumDate(today.addDays(_MAX_UNTIL_DAYS - 1))
        self.until_edit.setDate(today.addDays(_DEFAULT_UNTIL_DAYS))
        self.until_edit.setFixedWidth(124)
        self.until_edit.setVisible(False)

        self.budget_lbl = QLabel()
        self.budget_spin = QSpinBox()
        self.budget_spin.setRange(1, _MAX_BUDGET)
        self.budget_spin.setValue(ac.DEFAULT_BUDGET)
        self.budget_spin.setGroupSeparatorShown(True)
        self.budget_spin.setFixedWidth(112)
        self.currency_edit = QLineEdit(_DEFAULT_CURRENCY)
        self.currency_edit.setMaxLength(5)
        self.currency_edit.setFixedWidth(52)
        self.currency_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        budget_row = QHBoxLayout()
        budget_row.setSpacing(6)
        budget_row.addWidget(self.budget_spin)
        budget_row.addWidget(self.currency_edit)

        self.price_lbl = QLabel()
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setDecimals(2)
        self.price_spin.setRange(0.01, 1_000_000)
        self.price_spin.setValue(ac.DEFAULT_PRICE_PER_FOLLOWER)
        self.price_spin.setFixedWidth(96)

        self.channel_lbl = QLabel()
        self.channel_edit = QLineEdit()
        self.channel_edit.setMinimumWidth(150)
        self.own_status_lbl = QLabel()
        self.own_status_lbl.setObjectName("hint")
        self.own_status_lbl.setWordWrap(True)

        bottom = Qt.AlignmentFlag.AlignBottom
        for col, lbl in enumerate((self.target_lbl, self.period_lbl, self.budget_lbl,
                                   self.price_lbl, self.channel_lbl)):
            lbl.setWordWrap(True)   # a long translation must not widen the whole page
            grid.addWidget(lbl, 0, col, bottom)
        grid.addWidget(self.target_spin, 1, 0)
        grid.addWidget(self.period_combo, 1, 1)
        grid.addLayout(budget_row, 1, 2)
        grid.addWidget(self.price_spin, 1, 3)
        grid.addWidget(self.channel_edit, 1, 4)
        grid.addWidget(self.until_edit, 2, 1)
        grid.addWidget(self.own_status_lbl, 2, 4)

        self.target_spin.valueChanged.connect(self._schedule)
        self.budget_spin.valueChanged.connect(self._schedule)
        self.price_spin.valueChanged.connect(self._schedule)
        self.currency_edit.textChanged.connect(self._schedule)
        self.until_edit.dateChanged.connect(self._schedule)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        self.channel_edit.textChanged.connect(self._on_channel_changed)
        return box

    def _tiles_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        self.tile_spend = StatCard("")
        self.tile_expected = StatCard("")
        self.tile_cpf = StatCard("")
        self.tile_slots = StatCard("")
        for tile in (self.tile_spend, self.tile_expected, self.tile_cpf, self.tile_slots):
            tile.setMinimumHeight(108)
            # Unwrapped, four tiles' sub-lines set the page's minimum width —
            # wider than the window at Large zoom or in a longer language.
            tile.title_lbl.setWordWrap(True)
            tile.sub_lbl.setWordWrap(True)
            row.addWidget(tile, 1)
        return row

    def _posts_card(self) -> SectionCard:
        self.posts_card = SectionCard()
        self.posts_hint_lbl = QLabel()
        self.posts_hint_lbl.setObjectName("hint")
        self.posts_hint_lbl.setWordWrap(True)
        self.posts_card.body.addWidget(self.posts_hint_lbl)
        # [your-channel stats card | grid of post cards that wraps to the width]
        self.posts_row = QHBoxLayout()
        self.posts_row.setSpacing(_POST_GAP)
        self.own_card = StatCard("")
        self.own_card.setFixedWidth(_OWN_CARD_MIN_WIDTH)   # resized to its number, see _fit_own_value
        self.own_card.title_lbl.setWordWrap(True)
        self.own_card.sub_lbl.setWordWrap(True)
        self.posts_row.addWidget(self.own_card, 0, Qt.AlignmentFlag.AlignTop)
        self.posts_holder = QWidget()
        self.posts_grid = QGridLayout(self.posts_holder)
        self.posts_grid.setContentsMargins(0, 0, 0, 0)
        self.posts_grid.setSpacing(_POST_GAP)
        self.posts_row.addWidget(self.posts_holder, 1)
        self.posts_card.body.addLayout(self.posts_row)
        self.fetch_media_btn = QPushButton()
        self.fetch_media_btn.setObjectName("ghost")
        self.fetch_media_btn.clicked.connect(self._on_fetch_media_clicked)
        self.posts_card.title_row.addWidget(self.fetch_media_btn)
        self.posts_card.setVisible(False)
        return self.posts_card

    def _recs_card(self) -> SectionCard:
        self.recs_card = SectionCard()
        self.recs_lay = QVBoxLayout()
        self.recs_lay.setSpacing(8)
        self.recs_card.body.addLayout(self.recs_lay)
        return self.recs_card

    def _gantt_card(self) -> SectionCard:
        self.gantt_card = SectionCard()
        self.reset_btn = QPushButton()
        self.reset_btn.setObjectName("ghost")
        self.reset_btn.setVisible(False)
        self.reset_btn.clicked.connect(self._reset_excluded)
        self.gantt_card.title_row.addWidget(self.reset_btn)
        self.text_btn = QPushButton()
        self.text_btn.setObjectName("ghost")
        self.text_btn.clicked.connect(self._show_text)
        self.gantt_card.title_row.addWidget(self.text_btn)

        self.gantt_hint_lbl = QLabel()
        self.gantt_hint_lbl.setObjectName("hint")
        self.gantt_hint_lbl.setWordWrap(True)
        self.gantt_card.body.addWidget(self.gantt_hint_lbl)
        self.legend_lbl = QLabel()
        self.legend_lbl.setObjectName("hint")
        self.gantt_card.body.addWidget(self.legend_lbl)

        self.gantt = AdGanttChart()
        self.gantt.block_clicked.connect(self._on_block_clicked)
        self.gantt_card.body.addWidget(self.gantt)
        return self.gantt_card

    # ----------------------------------------------------------- language
    def _retranslate_static(self) -> None:
        tr = self.tr_
        self.title_lbl.setText(tr("ad_title"))
        self.sub_lbl.setText(tr("ad_sub"))
        self.hint_lbl.setText(tr("ad_hint"))
        self.empty_lbl.setText(tr("ad_empty"))
        self.target_lbl.setText(tr("ad_field_target"))
        self.period_lbl.setText(tr("ad_field_period"))
        for i, key in enumerate(("ad_period_2w", "ad_period_1m", "ad_period_until")):
            self.period_combo.setItemText(i, tr(key))
        self.budget_lbl.setText(tr("ad_field_budget"))
        self.currency_edit.setToolTip(tr("ad_field_currency"))
        self.price_lbl.setText(tr("ad_field_price"))
        self.channel_lbl.setText(tr("ad_field_channel"))
        self.channel_edit.setPlaceholderText(tr("ad_channel_placeholder"))
        self.tile_spend.title_lbl.setText(tr("ad_tile_spend"))
        self.tile_expected.title_lbl.setText(tr("ad_tile_expected"))
        self.tile_cpf.title_lbl.setText(tr("ad_tile_cpf"))
        self.tile_slots.title_lbl.setText(tr("ad_tile_slots"))
        self.posts_card.title_lbl.setText(tr("ad_posts_title"))
        self.own_card.title_lbl.setText(tr("ad_own_card_title"))
        self.fetch_media_btn.setToolTip(tr("cqi_fetch_media_hint"))
        if self._media_worker is None:
            self.fetch_media_btn.setText(tr("cqi_fetch_media"))
        self.posts_hint_lbl.setText(tr("ad_posts_hint", n=_TOP_POSTS))
        self.recs_card.title_lbl.setText(tr("ad_recs_title"))
        self.gantt_card.title_lbl.setText(tr("ad_gantt_title"))
        self.gantt_hint_lbl.setText(tr("ad_gantt_hint"))
        self.text_btn.setText(tr("ad_text_btn"))
        self.legend_lbl.setText(self._legend_html())
        self.gantt.set_callbacks(
            label=lambda b: b["label"], price=lambda b: self._money(b["price"]),
            tooltip=self._block_tooltip, day=self._fmt_day, weekday=self._fmt_weekday)

    def retranslate(self) -> None:
        self._retranslate_static()
        self._update_own()
        self._render()

    def _legend_html(self) -> str:
        parts = [f'<span style="color:{COLORS[STATE_COLOR_KEYS[s]]}">●</span> '
                 f'{self.tr_("ad_state_" + s)}' for s in ac.STATES]
        return f"{self.tr_('ad_legend_title')} " + " &nbsp;&nbsp; ".join(parts)

    # ------------------------------------------------------------ formats
    def _locale(self) -> QLocale:
        return QLocale(self.i18n.lang)

    def _fmt_day(self, d: date) -> str:
        # Some locales abbreviate months with a trailing dot ("окт."), which
        # would double up with a sentence's own full stop.
        return self._locale().toString(QDate(d.year, d.month, d.day), "d MMM").rstrip(".")

    def _fmt_weekday(self, d: date) -> str:
        return self._locale().dayName(d.isoweekday(), QLocale.FormatType.ShortFormat)

    def _currency(self) -> str:
        return self.currency_edit.text().strip()

    def _money(self, value: float) -> str:
        if abs(value - round(value)) < 0.005:
            num = fmt_int(round(value))
        else:
            num = f"{value:,.2f}".replace(",", " ")
        return f"{num} {self._currency()}".strip()

    def _state_name(self, state: str) -> str:
        return self.tr_("ad_state_" + state)

    # --------------------------------------------------------------- data
    def refresh(self) -> None:
        """Channels, folders or tags changed elsewhere (or the view is about
        to be shown): reload lazily — right now if visible, else on the next
        show, so a startup full of sidebar rebuilds doesn't pay for it."""
        self._dirty = True
        if self.isVisible():
            self._reload()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self._dirty:
            self._reload()

    def _reload(self) -> None:
        self._dirty = False
        self._summaries = self.channel_store.list()
        live: set[str] = set()
        candidates = []
        for summary in self._summaries:
            key = summary["key"]
            live.add(key)
            fetched = summary.get("fetched_at", "")
            cached = self._cache.get(key)
            if cached is None or cached[0] != fetched:
                data = self.channel_store.load(key)
                cand = None
                if data:
                    data.setdefault("key", key)
                    cand = ac.candidate_from_checkpoint(data)
                cached = (fetched, cand)
                self._cache[key] = cached
            if cached[1] is not None:
                candidates.append(self._with_folder_fields(cached[1]))
        for key in [k for k in self._cache if k not in live]:
            del self._cache[key]
        self._excluded &= live
        self._candidates = candidates
        self.empty_lbl.setVisible(not candidates)
        self._update_own()
        self._replan()

    def _with_folder_fields(self, cand: dict) -> dict:
        """A copy of the cached candidate with the fields that depend on
        folders/tags, which can change without a refetch."""
        out = dict(cand)
        folder_id = self.folder_store.folder_for_channel(cand["key"])
        folder = self.folder_store.get_folder(folder_id) if folder_id else None
        out["folder_id"] = folder_id
        out["is_model"] = bool(
            folder and folder["name"].strip().lower() == ac.MODELS_FOLDER)
        out["tag"] = (self.tag_store.tag_for_channel(cand["key"])
                      if self.tag_store is not None else None)
        return out

    # ---------------------------------------------------------- own channel
    def _on_channel_changed(self, _text: str = "") -> None:
        self._update_own()
        self._schedule()

    def _update_own(self) -> None:
        """Match the typed channel against the tracked ones and rebuild the
        best-posts row. The planner keeps the matched channel out of the
        candidates and prefers ones in its niche."""
        query = self.channel_edit.text().strip()
        summary = ac.find_own_channel(query, self._summaries) if query else None
        self._own = None
        self._own_data = None
        if summary:
            key = summary["key"]
            self._own_data = self.channel_store.load(key)
            if self._own_data:
                self._own_data.setdefault("key", key)
                cand = self._with_folder_fields({"key": key})
                self._own = {"key": key, "tag": cand["tag"],
                             "folder_id": cand["folder_id"],
                             "followers": int(summary.get("members", 0) or 0)}
            self.own_status_lbl.setText(self.tr_(
                "ad_own_matched", title=ac.channel_label(summary),
                followers=fmt_int(summary.get("members", 0) or 0)))
        else:
            self.own_status_lbl.setText(self.tr_("ad_own_unmatched") if query else "")
        self._render_posts()

    def _clear_posts(self) -> None:
        for w in self._post_widgets:
            w.hide()
            w.deleteLater()
        self._post_widgets = []

    def _render_posts(self) -> None:
        self._clear_posts()
        data = self._own_data
        self.posts_card.setVisible(data is not None)
        if data is None:
            return
        entries = ac.best_posts(data, _TOP_POSTS)
        self._own_entries = entries
        if not entries:
            empty = QLabel(self.tr_("ad_posts_empty"))
            empty.setObjectName("hint")
            empty.setWordWrap(True)
            self.posts_grid.addWidget(empty, 0, 0)
            self._post_widgets.append(empty)
            return
        ref = ac.channel_ref(data)
        label = ac.channel_label(data)
        avg_views = ac.channel_avg_views(data)
        for n, entry in enumerate(entries, 1):
            row = entry["row"]
            card = PostCard()
            thumb = None
            path = thumbnail_path(ref, row.get("id", 0))
            if path.exists():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    thumb = pix.scaled(POST_CARD_WIDTH - 20, POST_CARD_THUMB_HEIGHT,
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
            counts = (f"{fmt_int(row.get('views', 0))}👁️ {fmt_int(row.get('comments', 0))}💬\n"
                      f"{fmt_int(row.get('reactions', 0))}❤️ {fmt_int(row.get('forwards', 0))}🔄")
            card.set_data(
                label, thumb, POST_CARD_PLACEHOLDERS.get(row.get("media_type") or "", ""),
                elide_to_lines(row.get("text") or "", POST_CARD_TEXT_WIDTH,
                               POST_CARD_TEXT_LINES, POST_CARD_TEXT_PIXEL_SIZE),
                entry["gauge"], counts, build_post_link(ref, row.get("id", 0)),
                score_tooltip(self.tr_, label, row, avg_views, entry["raw_score"],
                              entry["gauge"], fmt_int),
                media_counts=row.get("media_counts"))
            tip_key = ("ad_post_tip_media" if (row.get("media_type") or "") in _MEDIA_TIP_TYPES
                       else "ad_post_tip_text")
            url = build_post_link(ref, row.get("id", 0))
            link_lbl = QLabel(f'<a href="{html.escape(url)}">'
                              f'{html.escape(url.split("://", 1)[-1])}</a>')
            link_lbl.setTextFormat(Qt.TextFormat.RichText)
            link_lbl.linkActivated.connect(open_external_link)
            link_lbl.setFixedWidth(POST_CARD_WIDTH)
            caption = QLabel(self.tr_("ad_post_rank", n=n, gauge=round(entry["gauge"]))
                             + "\n" + self.tr_(tip_key))
            caption.setObjectName("hint")
            caption.setWordWrap(True)
            caption.setFixedWidth(POST_CARD_WIDTH)
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(card)
            col.addWidget(link_lbl)
            col.addWidget(caption)
            col.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(col)
            self._post_widgets.append(wrap)
        self._relayout_posts(force=True)

    def _on_fetch_media_clicked(self) -> None:
        """Download thumbnails for the shown posts — same job the
        High-Quality Posts view's Fetch media runs."""
        if self._own_data is None or self._media_worker is not None or self.cfg is None:
            return
        ref = ac.channel_ref(self._own_data)
        posts = [{"channel": ref, "id": e["row"].get("id", 0),
                  "ids": e["row"].get("ids") or [e["row"].get("id", 0)]}
                 for e in self._own_entries
                 if not thumbnail_path(ref, e["row"].get("id", 0)).exists()]
        if not posts:
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
        self.fetch_media_btn.setEnabled(False)
        self.fetch_media_btn.setText(self.tr_("cqi_fetch_media_running"))
        self._media_worker = ToolWorker(run_thumbnail_cache, {"posts": posts}, conn, parent=self,
                                        alt_conn=self.cfg.alt_conn(1))
        self._media_worker.sig_ask.connect(self._on_media_ask)
        self._media_worker.sig_done.connect(self._on_fetch_media_done)
        self._media_worker.start()

    def _on_media_ask(self, _kind: str, _prompt: str) -> None:
        # Needs the already-authorized session; a fresh login belongs on Config.
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("cqi_fetch_media_login_required"))
        if self._media_worker is not None:
            self._media_worker.request_cancel()

    def _on_fetch_media_done(self, ok: bool, msg: str) -> None:
        self.fetch_media_btn.setEnabled(True)
        self.fetch_media_btn.setText(self.tr_("cqi_fetch_media"))
        self._media_worker = None
        if ok:
            self._render_posts()   # pick up the newly cached thumbnails
        elif msg and msg != "Login cancelled":
            QMessageBox.warning(self, self.tr_("app_title"), msg)

    def _relayout_posts(self, force: bool = False) -> None:
        """Flow the post cards into as many columns as the row has room for."""
        cards = [w for w in self._post_widgets if not isinstance(w, QLabel)]
        if not cards:
            return
        avail = self.posts_holder.width() or 3 * (POST_CARD_WIDTH + _POST_GAP)
        cols = max(1, (avail + _POST_GAP) // (POST_CARD_WIDTH + _POST_GAP))
        if cols == self._post_cols and not force:
            return
        self._post_cols = cols
        for i in reversed(range(self.posts_grid.count())):
            self.posts_grid.takeAt(i)
        for i, w in enumerate(cards):
            self.posts_grid.addWidget(w, i // cols, i % cols, Qt.AlignmentFlag.AlignTop)
        self.posts_grid.setColumnStretch(cols, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout_posts()

    # --------------------------------------------------------------- plan
    def _on_period_changed(self, _index: int = 0) -> None:
        self.until_edit.setVisible(self.period_combo.currentData() == 0)
        self._schedule()

    def _schedule(self, *_args) -> None:
        self._timer.start()

    def _period_days(self) -> int:
        days = self.period_combo.currentData()
        if days:
            return int(days)
        today = date.today()
        until = self.until_edit.date().toPython()
        return max(1, (until - today).days + 1)

    def _replan(self) -> None:
        self._window_start = date.today()
        self._window_days = self._period_days()
        self._blocks = ac.plan_campaign(
            self._candidates, target=self.target_spin.value(),
            budget=float(self.budget_spin.value()),
            price_per_follower=self.price_spin.value(),
            window_start=self._window_start, window_days=self._window_days,
            own=self._own, excluded=self._excluded)
        self._render()

    # ------------------------------------------------------------- render
    def _render(self) -> None:
        self._render_own_card()
        self._render_tiles()
        self._render_recs()
        self._render_gantt()

    def _render_own_card(self) -> None:
        """Current followers -> current + what the plan expects, beside the target."""
        if self._own is None:
            return
        current = self._own["followers"]
        target = self.target_spin.value()
        expected = ac.plan_totals(self._blocks, target, float(self.budget_spin.value()),
                                  self.price_spin.value())["expected"]
        value = f"{fmt_int(current)} → {fmt_int(current + round(expected))}"
        self.own_card.set_value(
            value, self.tr_("ad_own_card_sub", target=fmt_int(target),
                            expected=fmt_int(round(expected))))
        self._fit_own_value(value)

    def _fit_own_value(self, text: str) -> None:
        """Widen the card to fit `text` at the normal size, up to its maximum
        width; past that, shrink the font instead so the number is never cut
        off (a 50 000 → 51 000 channel used to lose its second number)."""
        base = fs(_OWN_VALUE_PX)
        font = QFont()
        font.setPixelSize(base)
        font.setWeight(QFont.Weight.ExtraBold)
        need = QFontMetrics(font).horizontalAdvance(text) + _OWN_CARD_MARGINS
        width = max(_OWN_CARD_MIN_WIDTH, min(_OWN_CARD_MAX_WIDTH, need))
        self.own_card.setFixedWidth(width)
        px = base
        if need > width:
            px = max(_OWN_VALUE_MIN_PX, int(base * (width - _OWN_CARD_MARGINS)
                                           / (need - _OWN_CARD_MARGINS)))
        self.own_card.value_lbl.setStyleSheet(f"font-size: {px}px;")
        QTimer.singleShot(0, self._relayout_posts)   # the posts grid's width changed

    def _render_tiles(self) -> None:
        tr = self.tr_
        target = self.target_spin.value()
        budget = float(self.budget_spin.value())
        ppf = self.price_spin.value()
        t = ac.plan_totals(self._blocks, target, budget, ppf)
        self.tile_spend.set_value(self._money(t["spend"]),
                                  tr("ad_tile_spend_sub", budget=self._money(budget)))
        self.tile_expected.set_value(
            f"≈ {fmt_int(round(t['expected']))}",
            tr("ad_tile_expected_sub", pct=round(t["target_pct"]), target=fmt_int(target)))
        cpf = t["cost_per_follower"]
        self.tile_cpf.set_value(self._money(round(cpf, 2)) if cpf else "—",
                                tr("ad_tile_cpf_sub", price=self._money(ppf)))
        self.tile_slots.set_value(str(t["slots"]),
                                  tr("ad_tile_slots_sub", prime=t["prime_slots"]))

    def _render_recs(self) -> None:
        while self.recs_lay.count():
            item = self.recs_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        recs = ac.recommendations(
            self._blocks, self._candidates, target=self.target_spin.value(),
            budget=float(self.budget_spin.value()),
            price_per_follower=self.price_spin.value(), window_days=self._window_days,
            own=self._own, own_followers=(self._own or {}).get("followers", 0),
            excluded_count=len(self._excluded))
        texts = [self._rec_text(r) for r in recs]
        if self._own is None:
            texts.append(self.tr_("ad_rec_own_missing"))
        for text in texts:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.recs_lay.addWidget(lbl)

    def _rec_text(self, r: dict) -> str:
        tr, money = self.tr_, self._money
        kind = r["kind"]
        if kind == "cost_check":
            return tr(f"ad_rec_{kind}", price=money(r["price"]), target=fmt_int(r["target"]),
                      cost=money(r["cost"]), budget=money(r["budget"]),
                      affordable=fmt_int(round(r["affordable"])))
        if kind == "pacing":
            return tr("ad_rec_pacing", per_day=fmt_int(max(1, round(r["per_day"]))),
                      days=r["days"])
        if kind == "target_met":
            return tr("ad_rec_target_met", expected=fmt_int(round(r["expected"])),
                      safety=round(r["safety_pct"]), spend=money(r["spend"]),
                      reserve=money(r["reserve"]))
        if kind == "target_short":
            return tr("ad_rec_target_short", expected=fmt_int(round(r["expected"])),
                      pct=round(r["pct"]), need_budget=money(round(r["need_budget"])),
                      extra_budget=money(round(r["extra_budget"])),
                      cpf=money(round(r["cpf"], 2)))
        if kind == "prime_top":
            names = ", ".join(
                tr("ad_rec_prime_item_since" if t["since"] else "ad_rec_prime_item",
                   label=t["label"], gain=t["gain"], since=t["since"])
                for t in r["top"])
            return tr("ad_rec_prime_top", count=r["count"], total=r["total"], names=names)
        if kind == "no_prime":
            return tr("ad_rec_no_prime", total=r["total"])
        if kind == "timing":
            return tr("ad_rec_timing", first=self._fmt_day(r["first"]),
                      first_label=r["first_label"], last_end=self._fmt_day(r["last_end"]))
        if kind == "excluded":
            return tr("ad_rec_excluded", count=r["count"])
        if kind == "swap":
            return tr("ad_rec_swap", names=r["names"])
        return tr(f"ad_rec_{kind}")   # no_slots

    def _render_gantt(self) -> None:
        self.gantt.set_plan(self._blocks, self._window_start, self._window_days,
                            self.tr_("ad_gantt_empty"))
        n = len(self._excluded)
        self.reset_btn.setVisible(n > 0)
        if n:
            self.reset_btn.setText(self.tr_("ad_excluded_reset", count=n))

    def _block_tooltip(self, b: dict) -> str:
        tr = self.tr_
        prime = b["prime"]
        end = b["start"] + timedelta(b["days"] - 1)
        lines = [
            tr("ad_tip_head", label=b["label"], followers=fmt_int(b["followers"])),
            tr("ad_tip_slot", period=tr("ad_period_" + b["horizon"]),
               start=self._fmt_day(b["start"]), end=self._fmt_day(end)),
            tr("ad_tip_price", price=self._money(b["price"]),
               base=fmt_int(round(b["base_forecast"])), ppf=self._money(self.price_spin.value()),
               markup=tr("ad_tip_markup") if b["is_model"] else ""),
            tr("ad_tip_expected", expected=fmt_int(round(b["expected"])),
               state=self._state_name(prime["state"]), score=round(prime["score"])),
        ]
        if prime["reach_gain"] is None:
            lines.append(tr("ad_tip_pulse_unknown"))
        else:
            era = (tr("ad_tip_era", since=prime["era_since"], months=prime["era_months"])
                   if prime["era_since"] else "")
            lines.append(tr("ad_tip_pulse", recent=ac.PRIME_RECENT_MONTHS,
                            window=ac.PRIME_WINDOW_MONTHS,
                            reach=f"{(prime['reach_gain'] - 1) * 100:+.0f}%",
                            eng=f"{(prime['engagement_gain'] - 1) * 100:+.0f}%", era=era))
        return "\n".join(lines)

    def _plan_text(self) -> str:
        """The current plan as a plain numbered list, to paste elsewhere."""
        tr = self.tr_
        target = self.target_spin.value()
        lines = [tr("ad_text_title")]
        if self._own is not None:
            current = self._own["followers"]
            name = ac.channel_label(self._own_data or {})
            lines.append(tr("ad_text_own", channel=name, current=fmt_int(current),
                            target=fmt_int(current + target)))
        else:
            lines.append(tr("ad_text_no_own", target=fmt_int(target)))
        for n, b in enumerate(self._blocks, 1):
            end = b["start"] + timedelta(b["days"] - 1)
            lines.append(f"{n}. {b['label']} {self._fmt_day(b['start'])} - "
                         f"{self._fmt_day(end)} +{fmt_int(round(b['expected']))}")
        return "\n".join(lines)

    def _show_text(self) -> None:
        ChannelReportDialog(self, self.i18n, self._plan_text(),
                            self.tr_("ad_text_title")).exec()

    # ------------------------------------------------------ manual edits
    def _on_block_clicked(self, index: int, pos: QPoint) -> None:
        if not 0 <= index < len(self._blocks):
            return
        block = self._blocks[index]
        alts = ac.alternatives(
            self._candidates, self._blocks, index,
            budget=float(self.budget_spin.value()),
            price_per_follower=self.price_spin.value(), own=self._own,
            excluded=self._excluded, limit=_MAX_ALTERNATIVES)

        menu = QMenu(self)
        head = menu.addAction(self.tr_("ad_menu_replace", label=block["label"]))
        head.setEnabled(False)
        if alts:
            for alt in alts:
                text = self.tr_(
                    "ad_menu_alt", label=alt["label"], price=self._money(alt["price"]),
                    expected=fmt_int(round(alt["expected"])),
                    state=f"{_STATE_ICONS[alt['prime']['state']]} {round(alt['prime']['score'])}")
                act = menu.addAction(text)
                act.triggered.connect(lambda _=False, a=alt: self._replace(index, a))
        else:
            menu.addAction(self.tr_("ad_menu_none")).setEnabled(False)
        menu.addSeparator()
        no_ads = menu.addAction(self.tr_("ad_menu_no_ads"))
        no_ads.triggered.connect(
            lambda _=False: self._exclude_and_replace(index, alts[0] if alts else None))
        remove = menu.addAction(self.tr_("ad_menu_remove"))
        remove.triggered.connect(lambda _=False: self._remove(index))
        menu.exec(pos)

    def _replace(self, index: int, block: dict) -> None:
        self._blocks[index] = block
        self._blocks.sort(key=lambda b: (b["start"], -b["price"], b["label"]))
        self._render()

    def _exclude_and_replace(self, index: int, block: dict | None) -> None:
        self._excluded.add(self._blocks[index]["key"])
        if block is not None:
            self._replace(index, block)
        else:
            self._remove(index)

    def _remove(self, index: int) -> None:
        del self._blocks[index]
        self._render()

    def _reset_excluded(self) -> None:
        self._excluded.clear()
        self._replan()
