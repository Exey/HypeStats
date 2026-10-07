"""Folder Search view: type words, get every stored post containing all of
them, from one folder or all of them, as wide cards laid out in 2-3 columns
with the post's *full* text (matches highlighted) on the left and its
thumbnail, Quality gauge and counts on the right.

The header (folder, search box, sort, limit, Fetch media) stays put; the
results scroll underneath. Searching is live (debounced) over the posts
already stored in the checkpoints — see app.post_search for exactly what that
covers, which is not a channel's full history. Cards are built once per
search; the Fetch media button (same job as High-Quality Posts') only
re-reads the cached thumbnails afterwards.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from ..config import Config
from ..folders import FolderStore
from ..media_cache import thumbnail_path
from ..post_search import (
    DEFAULT_LIMIT, DEFAULT_SORT, LIMITS, MIN_QUERY_CHARS, SORT_KEYS, highlight_html,
    search_posts,
)
from ..scoring import GAUGE_MAX, post_gauge_value, score_tooltip
from ..store import ChannelStore
from ..tools.media_fetch import run_thumbnail_cache
from ..worker import ToolWorker
from .charts import GaugeDial
from .dashboard_view import build_post_link, fmt_int
from .theme import COLORS, fs, zoom_extra
from .widgets import (
    POST_CARD_GAUGE_SIZE, POST_CARD_PLACEHOLDERS, Card, format_media_counts,
    open_external_link,
)

_ALL_FOLDERS = "__all__"
_SEARCH_DELAY_MS = 600     # quiet time after the last keystroke before searching
_BUILD_CHUNK = 8           # cards built per event-loop turn, so typing never waits on them
_PAGE_MARGIN_LEFT = 34
_PAGE_MARGIN_RIGHT = 40
_GRID_GAP = 14
_CARD_MIN_WIDTH = 360      # Auto columns = as many of these as fit, 1-3
_MAX_COLS = 4              # the Columns combo goes up to this
_FORCED_MIN_WIDTH = 300    # a column count picked by hand still has to fit this
_COMPACT_MIN_WIDTH = 270   # ...or this, for the compact (4-column) card
_COMPACT_FROM_COLS = 4     # at this many columns the cards switch to a compact side column
_SIDE_WIDTH = 190          # the card's right-hand media/stats column
_THUMB_HEIGHT = 130
_SIDE_COMPACT_WIDTH = 130
_THUMB_COMPACT_HEIGHT = 96
COLUMN_CHOICES = (0, 1, 2, 3, 4)   # 0 = Auto
_CARD_HEIGHT = 290         # every card is exactly this tall (a longer post scrolls inside)
_MEDIA_LOG_WIDTH = 260
_MEDIA_LOG_PIXEL_SIZE = 12   # matches QLabel#hint's font-size in theme.py


def _channel_label(ch: dict) -> str:
    username = ch.get("username") or ""
    if username:
        return f"@{username}"
    return ch.get("title") or ch.get("channel") or ch.get("key", "?")


def _channel_ref(ch: dict) -> str:
    """Identifier to resolve/link a channel — same fallback order as the
    other views (see content_quality_view._channel_ref)."""
    return ch.get("channel") or ch.get("username") or ch.get("key", "")


def _channel_avg_views(data: dict) -> float:
    stats = data.get("stats", {}) or {}
    return float(stats.get("avg_views_recent") or stats.get("avg_views", 0) or 0)


def _fmt_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime(
            "%Y-%m-%d %H:%M")
    except ValueError:
        return iso


class _TextArea(QTextBrowser):
    """Read-only rich-text area filling the card's left side — transparent, so
    it sits on the card like plain text; a post longer than the card scrolls
    inside it, so every card keeps one height."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; padding: 0px; }")
        self.document().setDocumentMargin(2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


class _ClickLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SearchResultCard(Card):
    """One result: header (channel · date · followers · open link), the full
    highlighted text on the left, thumbnail + gauge + counts on the right."""

    def __init__(self, tr, entry: dict, combined, thumb: QPixmap | None) -> None:
        super().__init__(shadow=False)   # hundreds of cards: a border is enough
        self.setFixedHeight(_CARD_HEIGHT + zoom_extra(14))   # one height for every card
        ch, row = entry["channel"], entry["row"]
        link = build_post_link(_channel_ref(ch), row.get("id", 0))
        label = _channel_label(ch)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        name = QLabel(label)
        name.setStyleSheet("font-weight: 700;")
        # A long channel name clips rather than widening the card — the
        # header's width must never set a minimum wider than a column.
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setToolTip(label)
        head.addWidget(name, 1)   # takes the room the (fixed-width) meta leaves
        # Date only (no time): the header's width is what sets a card's
        # minimum, and 2-3 columns have to fit side by side.
        meta = [_fmt_date(row.get("date", ""))[:10], f"{fmt_int(ch.get('members', 0))} 👥"]
        if row.get("repost"):
            meta.append(tr("fsearch_repost"))
        meta_lbl = QLabel(" · ".join(m for m in meta if m))
        meta_lbl.setObjectName("hint")
        head.addWidget(meta_lbl)
        # The default link blue is unreadable on dark themes (and a QLabel's
        # palette Link colour is overridden by the app stylesheet), so colour
        # the text inside the anchor instead.
        open_lbl = QLabel(f'<a href="{link}" style="text-decoration:none;">'
                          f'<span style="color:{COLORS["accent"]};">{tr("fsearch_open")}</span></a>')
        open_lbl.setTextFormat(Qt.TextFormat.RichText)
        open_lbl.linkActivated.connect(open_external_link)
        head.addWidget(open_lbl)   # top-right corner of the card
        lay.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)
        text = _TextArea()
        text.setHtml(highlight_html(_post_text(row), combined))
        body.addWidget(text, 1)

        self._compact = False
        self._thumb_src = thumb if thumb is not None and not thumb.isNull() else None
        side = QVBoxLayout()
        side.setSpacing(6)
        thumb_lbl = self._thumb_lbl = _ClickLabel()
        thumb_lbl.setFixedSize(_SIDE_WIDTH, _THUMB_HEIGHT)
        thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        thumb_lbl.setStyleSheet(f"font-size: {fs(44)}px;")
        if thumb is not None and not thumb.isNull():
            thumb_lbl.setPixmap(thumb)
        else:
            thumb_lbl.setText(POST_CARD_PLACEHOLDERS.get(row.get("media_type") or "", ""))
        thumb_lbl.clicked.connect(lambda: open_external_link(link))
        side.addWidget(thumb_lbl)
        media = format_media_counts(row.get("media_counts") or {})
        media_lbl = QLabel(media)
        media_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        media_lbl.setObjectName("hint")
        media_lbl.setVisible(bool(media))
        side.addWidget(media_lbl)

        bottom = self._bottom = QHBoxLayout()
        bottom.setSpacing(8)
        gauge_value = post_gauge_value(entry["raw_score"])
        gauge = self._gauge = GaugeDial(0, GAUGE_MAX)
        gauge.setFixedSize(POST_CARD_GAUGE_SIZE, POST_CARD_GAUGE_SIZE)
        gauge.setValue(max(0, min(GAUGE_MAX, round(gauge_value))))
        gauge.setToolTip(score_tooltip(tr, label, row, ch.get("avg_views", 0),
                                       entry["raw_score"], gauge_value, fmt_int))
        bottom.addWidget(gauge)
        counts = self._counts = QLabel(f"{fmt_int(row.get('views', 0))}👁️ {fmt_int(row.get('comments', 0))}💬\n"
                        f"{fmt_int(row.get('reactions', 0))}❤️ {fmt_int(row.get('forwards', 0))}🔄")
        bottom.addWidget(counts, 1)
        side.addLayout(bottom)
        side.addStretch(1)
        side_holder = self._side_holder = QWidget()
        side_holder.setFixedWidth(_SIDE_WIDTH)
        side_holder.setLayout(side)
        body.addWidget(side_holder, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(body)

    def set_compact(self, on: bool) -> None:
        """A narrower side column (smaller thumbnail, gauge above the counts)
        for the 4-column layout, where a full-width one would squeeze the
        text to nothing."""
        if on == self._compact:
            return
        self._compact = on
        width, height = ((_SIDE_COMPACT_WIDTH, _THUMB_COMPACT_HEIGHT) if on
                         else (_SIDE_WIDTH, _THUMB_HEIGHT))
        self._side_holder.setFixedWidth(width)
        self._thumb_lbl.setFixedSize(width, height)
        if self._thumb_src is not None:
            self._thumb_lbl.setPixmap(self._thumb_src.scaled(
                width, height, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self._bottom.setDirection(QBoxLayout.Direction.TopToBottom if on
                                  else QBoxLayout.Direction.LeftToRight)
        self._bottom.setAlignment(self._gauge, Qt.AlignmentFlag.AlignHCenter if on
                                  else Qt.AlignmentFlag(0))
        self._counts.setAlignment(Qt.AlignmentFlag.AlignHCenter if on
                                  else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def _post_text(row: dict) -> str:
    return row.get("full_text") or row.get("text") or ""


class FolderSearchView(QWidget):
    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                 cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self.cfg = cfg
        self._dirty = True
        # key -> (fetched_at, channel info incl. its stored rows); only
        # re-read from disk when a checkpoint's fetched_at changes.
        self._index: dict[str, tuple[str, dict]] = {}
        self._entries: list[dict] = []
        self._combined = None
        self._cards: list[QWidget] = []
        self._cols = 0
        self._build_gen = 0          # bumped to abandon a card build in progress
        self._build_next = 0         # index into _entries of the next card to build
        self._shown_sig = None       # what the cards on screen were built from
        self._media_worker: ToolWorker | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_SEARCH_DELAY_MS)
        self._timer.timeout.connect(self._run_search)
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QVBoxLayout()
        header.setContentsMargins(_PAGE_MARGIN_LEFT, 28, _PAGE_MARGIN_RIGHT, 10)
        header.setSpacing(10)
        title_row = QHBoxLayout()
        self.title_lbl = QLabel()
        self.title_lbl.setObjectName("pageTitle")
        title_row.addWidget(self.title_lbl)
        title_row.addStretch(1)
        self.media_log_lbl = QLabel("")
        self.media_log_lbl.setObjectName("hint")
        self.media_log_lbl.setFixedWidth(_MEDIA_LOG_WIDTH)
        title_row.addWidget(self.media_log_lbl)
        self.fetch_media_btn = QPushButton()
        self.fetch_media_btn.clicked.connect(self._on_fetch_media_clicked)
        title_row.addWidget(self.fetch_media_btn)
        header.addLayout(title_row)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.folder_combo = QComboBox()
        self.folder_combo.setMinimumWidth(190)
        self.folder_combo.currentIndexChanged.connect(lambda _i: self._schedule(0))
        controls.addWidget(self.folder_combo)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _t: self._schedule())
        self.search_edit.returnPressed.connect(lambda: self._schedule(0))
        controls.addWidget(self.search_edit, 1)
        self.sort_combo = QComboBox()
        for key in SORT_KEYS:
            self.sort_combo.addItem("", key)
        self.sort_combo.setCurrentIndex(self.sort_combo.findData(DEFAULT_SORT))
        self.sort_combo.currentIndexChanged.connect(lambda _i: self._schedule(0))
        controls.addWidget(self.sort_combo)
        self.limit_combo = QComboBox()
        for n in LIMITS:
            self.limit_combo.addItem("", n)
        self.limit_combo.setCurrentIndex(self.limit_combo.findData(DEFAULT_LIMIT))
        self.limit_combo.currentIndexChanged.connect(lambda _i: self._schedule(0))
        controls.addWidget(self.limit_combo)
        self.cols_combo = QComboBox()
        for n in COLUMN_CHOICES:
            self.cols_combo.addItem("", n)
        self.cols_combo.currentIndexChanged.connect(lambda _i: self._relayout_grid(force=True))
        controls.addWidget(self.cols_combo)
        header.addLayout(controls)

        self.status_lbl = QLabel()
        self.status_lbl.setObjectName("hint")
        self.status_lbl.setWordWrap(True)
        header.addWidget(self.status_lbl)
        outer.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        page = QVBoxLayout(holder)
        page.setContentsMargins(_PAGE_MARGIN_LEFT, 4, _PAGE_MARGIN_RIGHT, 24)
        page.setSpacing(0)
        self.grid_holder = QWidget()
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(_GRID_GAP)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        page.addWidget(self.grid_holder)
        page.addStretch(1)
        self.scroll.setWidget(holder)
        outer.addWidget(self.scroll, 1)
        self._retranslate_static()

    def _retranslate_static(self) -> None:
        self.title_lbl.setText(self.tr_("fsearch_title"))
        self.search_edit.setPlaceholderText(self.tr_("fsearch_placeholder"))
        for i in range(self.sort_combo.count()):
            self.sort_combo.setItemText(i, self.tr_("fsearch_sort_" + self.sort_combo.itemData(i)))
        self.sort_combo.setToolTip(self.tr_("fsearch_sort_hint"))
        for i in range(self.limit_combo.count()):
            self.limit_combo.setItemText(i, self.tr_("fsearch_limit_n", n=self.limit_combo.itemData(i)))
        self.limit_combo.setToolTip(self.tr_("fsearch_limit_hint"))
        for i in range(self.cols_combo.count()):
            n = self.cols_combo.itemData(i)
            self.cols_combo.setItemText(
                i, self.tr_("fsearch_cols_auto") if n == 0 else self.tr_("fsearch_cols_n", n=n))
        self.cols_combo.setToolTip(self.tr_("fsearch_cols_hint"))
        self.fetch_media_btn.setToolTip(self.tr_("cqi_fetch_media_hint"))
        if self._media_worker is None:
            self.fetch_media_btn.setText(self.tr_("cqi_fetch_media"))
        if self.folder_combo.count():
            self.folder_combo.setItemText(0, self.tr_("fsearch_all_folders"))

    def retranslate(self) -> None:
        self._retranslate_static()
        self._shown_sig = None   # card captions are translated text: rebuild them
        self._run_search()

    # ---------------------------------------------------------------- data
    def refresh(self) -> None:
        """Channels / folders changed elsewhere (or the view is about to be
        shown): reload lazily — now if visible, else on the next show."""
        self._dirty = True
        if self.isVisible():
            self._reload()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self._dirty:
            self._reload()
        self.search_edit.setFocus()

    def _reload(self) -> None:
        self._dirty = False
        self._shown_sig = None   # stored numbers may have changed: rebuild the cards
        live: set[str] = set()
        for summary in self.channel_store.list():
            key = summary["key"]
            live.add(key)
            fetched = summary.get("fetched_at", "")
            cached = self._index.get(key)
            if cached is None or cached[0] != fetched:
                data = self.channel_store.load(key)
                if not data:
                    continue
                data.setdefault("key", key)
                info = {
                    "key": key,
                    "username": data.get("username", ""),
                    "title": data.get("title", ""),
                    "channel": data.get("channel", ""),
                    "members": int((data.get("info") or {}).get("members", 0) or 0),
                    "avg_views": _channel_avg_views(data),
                    "rows": data.get("rows") or [],
                }
                cached = (fetched, info)
                self._index[key] = cached
        for key in [k for k in self._index if k not in live]:
            del self._index[key]

        current = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem(self.tr_("fsearch_all_folders"), _ALL_FOLDERS)
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        idx = self.folder_combo.findData(current) if current else 0
        self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.folder_combo.blockSignals(False)
        self._run_search()

    def _scoped_channels(self) -> list[dict]:
        folder_id = self.folder_combo.currentData()
        infos = [info for _fetched, info in self._index.values()]
        if folder_id in (None, _ALL_FOLDERS):
            return infos
        return [i for i in infos if self.folder_store.folder_for_channel(i["key"]) == folder_id]

    # -------------------------------------------------------------- search
    def _schedule(self, delay: int = _SEARCH_DELAY_MS) -> None:
        """(Re)start the quiet-time countdown — every keystroke pushes the
        search back, so nothing runs while you're still typing — and drop any
        card build still going from the previous search, which is about to be
        replaced anyway."""
        self._build_gen += 1
        self._timer.start(delay)

    def _run_search(self) -> None:
        self._timer.stop()
        channels = self._scoped_channels()
        query = self.search_edit.text().strip()
        total, entries, combined = search_posts(
            channels, query, self.sort_combo.currentData() or DEFAULT_SORT,
            int(self.limit_combo.currentData() or DEFAULT_LIMIT))
        self._entries, self._combined = entries, combined

        if not self._index:
            self.status_lbl.setText(self.tr_("fsearch_no_channels"))
        elif not query:
            self.status_lbl.setText(self.tr_("fsearch_prompt"))
        elif len(query) < MIN_QUERY_CHARS:
            self.status_lbl.setText(self.tr_("fsearch_too_short", n=MIN_QUERY_CHARS))
        elif not total:
            self.status_lbl.setText(self.tr_("fsearch_no_results", query=query))
        else:
            self.status_lbl.setText(self.tr_(
                "fsearch_status", found=fmt_int(total), shown=fmt_int(len(entries)),
                posts=fmt_int(sum(len(c["rows"]) for c in channels)),
                channels=fmt_int(len(channels))))
        # The same results (e.g. a trailing space was typed) need no rebuild.
        sig = (tuple((e["channel"]["key"], e["row"].get("id")) for e in entries),
               combined.pattern if combined else "")
        if sig == self._shown_sig and len(self._cards) == len(entries):
            return
        self._populate()

    def _clear_cards(self) -> None:
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i)
        for card in self._cards:
            card.hide()
            card.deleteLater()
        self._cards = []

    def _populate(self) -> None:
        """(Re)build the result cards from `_entries` — also after a media
        fetch, to pick up the newly cached thumbnails. Cards are built a few
        at a time from the event loop (see _build_chunk) so the window stays
        responsive while a big result list appears."""
        self._build_gen += 1
        self._clear_cards()
        self._shown_sig = (tuple((e["channel"]["key"], e["row"].get("id")) for e in self._entries),
                           self._combined.pattern if self._combined else "")
        self._apply_columns(self._column_count())
        self._build_next = 0
        QTimer.singleShot(0, lambda gen=self._build_gen: self._build_chunk(gen))

    def _build_chunk(self, gen: int) -> None:
        if gen != self._build_gen:
            return   # a newer search replaced this one
        end = min(len(self._entries), self._build_next + _BUILD_CHUNK)
        for i in range(self._build_next, end):
            entry = self._entries[i]
            ch, row = entry["channel"], entry["row"]
            thumb = None
            path = thumbnail_path(_channel_ref(ch), row.get("id", 0))
            if path.exists():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    thumb = pix.scaled(_SIDE_WIDTH, _THUMB_HEIGHT,
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
            card = SearchResultCard(self.tr_, entry, self._combined, thumb)
            card.set_compact(self._cols >= _COMPACT_FROM_COLS)
            self._cards.append(card)
            self.grid.addWidget(card, i // self._cols, i % self._cols, Qt.AlignmentFlag.AlignTop)
        self._build_next = end
        if end < len(self._entries):
            QTimer.singleShot(0, lambda: self._build_chunk(gen))

    def _column_count(self) -> int:
        """Auto: as many as fit at _CARD_MIN_WIDTH, 1-3. A number picked in
        the Columns combo is honoured only as far as the cards still fit the
        window (down to _FORCED_MIN_WIDTH, or _COMPACT_MIN_WIDTH for 4) — a
        narrower window gets fewer columns rather than clipped cards."""
        avail = self.scroll.viewport().width() - _PAGE_MARGIN_LEFT - _PAGE_MARGIN_RIGHT
        choice = int(self.cols_combo.currentData() or 0)
        if not choice:
            return max(1, min(3, (avail + _GRID_GAP) // (_CARD_MIN_WIDTH + _GRID_GAP)))
        floor = _COMPACT_MIN_WIDTH if choice >= _COMPACT_FROM_COLS else _FORCED_MIN_WIDTH
        return max(1, min(choice, (avail + _GRID_GAP) // (floor + _GRID_GAP)))

    def _apply_columns(self, cols: int) -> None:
        self._cols = cols
        for c in range(_MAX_COLS + 1):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)   # equal-width columns
        for card in self._cards:
            card.set_compact(cols >= _COMPACT_FROM_COLS)

    def _relayout_grid(self, force: bool = False) -> None:
        if not self._cards:
            self._cols = 0
            return
        cols = self._column_count()
        if cols == self._cols and not force:
            return
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i)
        self._apply_columns(cols)
        for i, card in enumerate(self._cards):
            self.grid.addWidget(card, i // cols, i % cols, Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout_grid()

    # --------------------------------------------------------- fetch media
    def _on_fetch_media_clicked(self) -> None:
        """Same on-demand thumbnail download as High-Quality Posts (see
        app.tools.media_fetch), for the cards currently shown."""
        if not self._entries or self._media_worker is not None:
            return
        to_fetch = [e for e in self._entries
                    if not thumbnail_path(_channel_ref(e["channel"]), e["row"].get("id", 0)).exists()]
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
                  "ids": e["row"].get("ids") or [e["row"].get("id", 0)]} for e in to_fetch]
        self.fetch_media_btn.setEnabled(False)
        self.fetch_media_btn.setText(self.tr_("cqi_fetch_media_running"))
        self._set_media_log("")
        self._media_worker = ToolWorker(run_thumbnail_cache, {"posts": posts}, conn,
                                        parent=self, alt_conn=self.cfg.alt_conn(1))
        self._media_worker.sig_log.connect(self._set_media_log)
        self._media_worker.sig_ask.connect(self._on_media_ask)
        self._media_worker.sig_done.connect(self._on_fetch_media_done)
        self._media_worker.start()

    def _set_media_log(self, msg: str) -> None:
        msg = msg.strip()
        font = QFont()
        font.setPixelSize(fs(_MEDIA_LOG_PIXEL_SIZE))
        elided = QFontMetrics(font).elidedText(
            msg, Qt.TextElideMode.ElideRight, _MEDIA_LOG_WIDTH)
        self.media_log_lbl.setText(elided)
        self.media_log_lbl.setToolTip(msg)

    def _on_media_ask(self, _kind: str, _prompt: str) -> None:
        # Relies on the session already authorized from Config, like the
        # other Fetch media buttons.
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("cqi_fetch_media_login_required"))
        if self._media_worker is not None:
            self._media_worker.request_cancel()

    def _on_fetch_media_done(self, ok: bool, msg: str) -> None:
        self.fetch_media_btn.setEnabled(True)
        self.fetch_media_btn.setText(self.tr_("cqi_fetch_media"))
        self._media_worker = None
        if ok:
            self._populate()   # pick up newly cached thumbnails
        elif msg and msg != "Login cancelled":
            QMessageBox.warning(self, self.tr_("app_title"), msg)
