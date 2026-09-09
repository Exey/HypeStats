"""Config screen: Telegram credentials + the "fetch a channel" card.

Combines tg-super-admin's Config tab (profiles, connection fields, QR / check
login, .env import-export) with a compact fetch panel that drives the
channel_stat tool in a background worker and emits the finished payload.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..config import CONN_FIELDS, config_dir
from ..errors import friendly_os_error
from ..folders import FolderStore
from ..periods import period_key_label
from ..rating import VIRAL_SHARE_DISPLAY_CAP, score_entries
from ..scoring import post_gauge_value, post_score_raw
from ..store import ChannelStore
from ..tags import TagStore
from ..tools.channel_stat import run_channel_stat
from ..tools.comments_refresh import run_comments_refresh
from ..tools.lean_refresh import run_lean_refresh
from ..tools.mentions_export import run_link_report_export, run_mentions_report_export
from ..tools.mentions_refresh import run_mentions_refresh
from ..worker import CheckLoginWorker, ToolWorker
from .dashboard_view import fmt_int
from .folder_dialog import FolderManagerDialog
from .qr_login_dialog import QrLoginDialog
from .widgets import Card, SectionCard

PERIOD_KEYS = ["2y", "3y", "all"]
_THEME_PREFS = ["system", "light", "dark"]


def _channel_display_name(ch: dict) -> str:
    username = ch.get("username") or ""
    return f"@{username}" if username else (ch.get("title") or ch.get("key", "?"))


class ConfigView(QWidget):
    channel_fetched = Signal(dict)   # full channel_stat payload
    folders_changed = Signal()
    tags_changed = Signal()
    checkpoints_changed = Signal()   # a folder's checkpoints were updated in place
    worker_started = Signal()        # a background job began — bring this screen up
    theme_change_requested = Signal(str)  # "system" | "light" | "dark"

    def __init__(self, cfg, i18n, folder_store: FolderStore, tag_store: TagStore,
                channel_store: ChannelStore, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.i18n = i18n
        self.folder_store = folder_store
        self.tag_store = tag_store
        self.channel_store = channel_store
        self.worker: ToolWorker | None = None
        self._export_out_path: str | None = None  # set by a Mentions-export
                                                    # button click, read back
                                                    # in _finish_mentions_export
        self._build_ui()
        self._load_fields()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 28, 40, 24)
        outer.setSpacing(18)

        header = QVBoxLayout()
        header.setSpacing(2)
        self.title_lbl = QLabel(self.tr_("nav_config"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(self.tr_("app_title"))
        self.sub_lbl.setObjectName("pageSub")
        header.addWidget(self.sub_lbl)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(18)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        root.addWidget(self._connection_card())
        root.addWidget(self._fetch_card())
        root.addWidget(self._instructions_card())
        root.addWidget(self._lean_refresh_card())
        root.addStretch()

        # Built here (this view owns all the folder/tag logic + the worker the
        # comments-refresh needs) but mounted at the top of the Folders & Tags
        # view — see MainWindow._build_ui / FolderStatView.mount_taxonomy_cards.
        # Built before _folders_card(): its own refresh_folders_list() call
        # at the end touches every folder-scoped combo, including this
        # card's — it has to exist first.
        self.mentions_export_card = self._mentions_export_card()
        self.folders_card = self._folders_card()
        self.tags_card = self._tags_card()

        # Every button that must be greyed out while a background worker runs
        # (the fetch/comments/lean jobs all share self.worker).
        self._busy_btns = [self.fetch_btn, self.refresh_mentions_btn, self.refresh_comments_btn,
                           self.lean_oldest_btn, self.lean_1mo_btn, self.lean_3mo_btn,
                           self.lean_selected_btn, self.lean_selected_2y_btn,
                           self.lean_selected_all_btn, self.refetch_mentions_btn,
                           self.export_link_report_btn, self.export_mentions_report_btn]

    def _connection_card(self) -> Card:
        card = SectionCard("Telegram")

        prow = QHBoxLayout()
        self.profile_lbl = QLabel(self.tr_("profile"))
        prow.addWidget(self.profile_lbl)
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(sorted(self.cfg.profiles))
        self.profile_combo.setCurrentText(self.cfg.current_profile)
        self.profile_combo.currentTextChanged.connect(self._switch_profile)
        prow.addWidget(self.profile_combo, 1)
        self.new_profile_btn = QPushButton(self.tr_("new_profile"))
        self.new_profile_btn.clicked.connect(self._new_profile)
        prow.addWidget(self.new_profile_btn)
        self.del_profile_btn = QPushButton(self.tr_("delete_profile"))
        self.del_profile_btn.clicked.connect(self._delete_profile)
        prow.addWidget(self.del_profile_btn)
        card.body.addLayout(prow)

        self.conn_form = QFormLayout()
        self.conn_form.setSpacing(10)
        self.edits: dict[str, QLineEdit] = {}
        for key in CONN_FIELDS:
            edit = QLineEdit()
            if key == "API_HASH":
                edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
            self.edits[key] = edit
            self.conn_form.addRow(self.tr_(f"field_{key}"), edit)
        card.body.addLayout(self.conn_form)

        brow = QHBoxLayout()
        self.save_btn = QPushButton(self.tr_("save"))
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        brow.addWidget(self.save_btn)
        self.qr_btn = QPushButton(self.tr_("qr_login_button"))
        self.qr_btn.clicked.connect(self._qr_login)
        brow.addWidget(self.qr_btn)
        self.check_login_btn = QPushButton(self.tr_("check_login_button"))
        self.check_login_btn.clicked.connect(self._check_login)
        brow.addWidget(self.check_login_btn)
        brow.addStretch()
        card.body.addLayout(brow)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        card.body.addWidget(self.status)

        theme_row = QHBoxLayout()
        self.theme_lbl = QLabel(self.tr_("menu_theme"))
        theme_row.addWidget(self.theme_lbl)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(
            [self.tr_(f"theme_{p}") for p in _THEME_PREFS])
        self.sync_theme_combo()
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        theme_row.addWidget(self.theme_combo, 1)
        card.body.addLayout(theme_row)

        loc_row = QHBoxLayout()
        self.loc_lbl = QLabel(self.tr_("config_location", path=str(self.cfg.path)))
        self.loc_lbl.setObjectName("hint")
        self.loc_lbl.setWordWrap(True)
        loc_row.addWidget(self.loc_lbl, 1)
        self.open_folder_btn = QPushButton(self.tr_("open_config_folder"))
        self.open_folder_btn.setObjectName("ghost")
        self.open_folder_btn.clicked.connect(self._open_config_folder)
        loc_row.addWidget(self.open_folder_btn)
        card.body.addLayout(loc_row)
        return card

    def _open_config_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir())))

    def sync_theme_combo(self) -> None:
        pref = self.cfg.theme if self.cfg.theme in _THEME_PREFS else "system"
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(_THEME_PREFS.index(pref))
        self.theme_combo.blockSignals(False)

    def _on_theme_combo_changed(self, index: int) -> None:
        if 0 <= index < len(_THEME_PREFS):
            self.theme_change_requested.emit(_THEME_PREFS[index])

    def _fetch_card(self) -> Card:
        card = SectionCard(self.tr_("fetch_title"))
        self.fetch_card_ref = card

        self.fetch_help_lbl = QLabel(self.tr_("fetch_help"))
        self.fetch_help_lbl.setObjectName("hint")
        self.fetch_help_lbl.setWordWrap(True)
        card.body.addWidget(self.fetch_help_lbl)

        self.channel_lbl = QLabel(self.tr_("fetch_channel"))
        card.body.addWidget(self.channel_lbl)
        self.channel_edit = QLineEdit(self.cfg.get("CHANNEL_ID"))
        self.channel_edit.setPlaceholderText(self.tr_("fetch_channel_placeholder"))
        self.channel_edit.setClearButtonEnabled(True)
        card.body.addWidget(self.channel_edit)

        self.fetch_form = QFormLayout()
        self.fetch_form.setSpacing(10)
        self.period_combo = QComboBox()
        self.period_combo.addItems([self.tr_(f"period_{k}") for k in PERIOD_KEYS])
        self.period_combo.setCurrentIndex(0)  # 2 years
        self.fetch_form.addRow(self.tr_("fetch_period"), self.period_combo)
        card.body.addLayout(self.fetch_form)

        self.public_check = QCheckBox(self.tr_("fetch_public"))
        card.body.addWidget(self.public_check)

        brow = QHBoxLayout()
        self.fetch_btn = QPushButton(self.tr_("fetch_button"))
        self.fetch_btn.setObjectName("primary")
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        brow.addWidget(self.fetch_btn)
        self.stop_btn = QPushButton(self.tr_("stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        brow.addWidget(self.stop_btn)
        brow.addStretch()
        card.body.addLayout(brow)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        card.body.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_view.setFixedHeight(120)
        card.body.addWidget(self.log_view)
        return card

    def _instructions_card(self) -> QGroupBox:
        box = QGroupBox(self.tr_("instructions_title"))
        self.instructions_box = box
        box.setCheckable(True)
        box.setChecked(False)
        lay = QVBoxLayout(box)
        self.instructions_info = QLabel(self.tr_("instructions_text"))
        self.instructions_info.setWordWrap(True)
        self.instructions_info.setOpenExternalLinks(True)
        self.instructions_info.setTextFormat(Qt.TextFormat.RichText)
        self.instructions_info.setVisible(False)
        lay.addWidget(self.instructions_info)
        box.toggled.connect(self.instructions_info.setVisible)
        return box

    def _folders_card(self) -> Card:
        card = SectionCard(self.tr_("folder_section_title"))
        self.folders_card_ref = card

        self.folders_help_lbl = QLabel(self.tr_("folder_manage_help"))
        self.folders_help_lbl.setObjectName("hint")
        self.folders_help_lbl.setWordWrap(True)
        card.body.addWidget(self.folders_help_lbl)

        self.folders_list_lbl = QLabel()
        self.folders_list_lbl.setObjectName("hint")
        self.folders_list_lbl.setWordWrap(True)
        self.folders_list_lbl.setTextFormat(Qt.TextFormat.RichText)
        card.body.addWidget(self.folders_list_lbl)

        row = QHBoxLayout()
        self.folders_manage_btn = QPushButton(self.tr_("folder_manage"))
        self.folders_manage_btn.clicked.connect(self._open_folder_manager)
        row.addWidget(self.folders_manage_btn)
        row.addStretch()
        card.body.addLayout(row)

        export_row = QHBoxLayout()
        self.folders_export_md_btn = QPushButton(self.tr_("folder_export_md_btn"))
        self.folders_export_md_btn.setToolTip(self.tr_("folder_export_md_hint"))
        self.folders_export_md_btn.clicked.connect(self._on_export_folders_md)
        export_row.addWidget(self.folders_export_md_btn)
        self.folders_export_extra_chk = QCheckBox(self.tr_("folder_export_extra_cols"))
        self.folders_export_extra_chk.setToolTip(self.tr_("folder_export_extra_cols_hint"))
        self.folders_export_extra_chk.toggled.connect(
            lambda on: self.folders_export_period_combo.setEnabled(on))
        export_row.addWidget(self.folders_export_extra_chk)
        export_row.addStretch()
        # Which specific period Rating/Views/Viral share are computed over
        # — every Half-Year bucket across all tracked channels (newest
        # first), then every Season bucket the same way, then a trailing
        # "All time" entry (see _collect_export_periods/
        # _channel_period_metrics). No "Monthly" entries on purpose: a
        # single calendar month is too noisy a window for a per-channel
        # export meant to compare many channels at a glance.
        self.folders_export_period_combo = QComboBox()
        self.folders_export_period_combo.setToolTip(self.tr_("folder_export_period_hint"))
        self.folders_export_period_combo.setEnabled(self.folders_export_extra_chk.isChecked())
        self.refresh_export_periods()
        export_row.addWidget(self.folders_export_period_combo)
        card.body.addLayout(export_row)

        # Bulk move: every tracked channel into one folder at once, instead
        # of assigning them one by one from the sidebar's right-click menu —
        # handy right after creating a folder for a batch of channels
        # that were all fetched before any folders existed.
        assign_all_row = QHBoxLayout()
        self.assign_all_lbl = QLabel(self.tr_("folder_assign_all_label"))
        assign_all_row.addWidget(self.assign_all_lbl)
        self.assign_all_combo = QComboBox()
        assign_all_row.addWidget(self.assign_all_combo, 1)
        self.assign_all_btn = QPushButton(self.tr_("folder_assign_all_btn"))
        self.assign_all_btn.setToolTip(self.tr_("folder_assign_all_hint"))
        self.assign_all_btn.clicked.connect(self._on_assign_all_clicked)
        assign_all_row.addWidget(self.assign_all_btn)
        card.body.addLayout(assign_all_row)

        # Keep the contents packed at the top — when this card is narrower
        # (and so taller) than the Tags card beside it, or vice-versa, the
        # shorter one shouldn't spread its rows to fill the leftover height.
        card.body.addStretch(1)

        self.refresh_folders_list()
        return card

    def _tags_card(self) -> Card:
        card = SectionCard(self.tr_("tag_section_title"))
        self.tags_card_ref = card

        self.tags_help_lbl = QLabel(self.tr_("tag_manage_help"))
        self.tags_help_lbl.setObjectName("hint")
        self.tags_help_lbl.setWordWrap(True)
        card.body.addWidget(self.tags_help_lbl)

        self.tags_list_lbl = QLabel()
        self.tags_list_lbl.setObjectName("hint")
        self.tags_list_lbl.setWordWrap(True)
        self.tags_list_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.tags_list_lbl.setStyleSheet("font-size: 14px;")
        card.body.addWidget(self.tags_list_lbl)

        row = QHBoxLayout()
        self.tags_load_btn = QPushButton(self.tr_("tag_load_md_btn"))
        self.tags_load_btn.setToolTip(self.tr_("tag_load_md_hint"))
        self.tags_load_btn.clicked.connect(self._on_load_tags_md)
        row.addWidget(self.tags_load_btn)
        row.addStretch()
        card.body.addLayout(row)
        card.body.addStretch(1)   # keep contents top-aligned — see _folders_card

        self.refresh_tags_list()
        return card

    def _mentions_export_card(self) -> Card:
        """Batch export of the Mentions view's own Link report / Mentions
        report (see app.ui.compare.mentions_view._open_link_report/
        _open_mentions_report) for a whole folder at once — one combined
        Markdown file, sections separated by channel title (see
        tools.mentions_export). Pure local computation over already-stored
        checkpoints, no live Telegram calls, but still routed through the
        same ToolWorker as every other batch action here (Mentions report
        runs full name extraction, real work for a large folder) — see
        _on_export_link_report_clicked/_on_export_mentions_report_clicked."""
        card = SectionCard(self.tr_("mentions_export_title"))
        self.mentions_export_card_ref = card

        self.mentions_export_help_lbl = QLabel(self.tr_("mentions_export_help"))
        self.mentions_export_help_lbl.setObjectName("hint")
        self.mentions_export_help_lbl.setWordWrap(True)
        card.body.addWidget(self.mentions_export_help_lbl)

        folder_row = QHBoxLayout()
        self.mentions_export_folder_lbl = QLabel(self.tr_("mentions_export_folder_label"))
        folder_row.addWidget(self.mentions_export_folder_lbl)
        self.mentions_export_folder_combo = QComboBox()
        folder_row.addWidget(self.mentions_export_folder_combo, 1)
        card.body.addLayout(folder_row)

        btn_row = QHBoxLayout()
        self.export_link_report_btn = QPushButton(self.tr_("mentions_export_link_btn"))
        self.export_link_report_btn.setToolTip(self.tr_("mentions_export_link_hint"))
        self.export_link_report_btn.clicked.connect(self._on_export_link_report_clicked)
        btn_row.addWidget(self.export_link_report_btn)
        self.export_mentions_report_btn = QPushButton(self.tr_("mentions_export_mentions_btn"))
        self.export_mentions_report_btn.setToolTip(self.tr_("mentions_export_mentions_hint"))
        self.export_mentions_report_btn.clicked.connect(self._on_export_mentions_report_clicked)
        btn_row.addWidget(self.export_mentions_report_btn)
        card.body.addLayout(btn_row)

        self.mentions_export_empty_lbl = QLabel(self.tr_("folder_list_empty"))
        self.mentions_export_empty_lbl.setObjectName("hint")
        card.body.addWidget(self.mentions_export_empty_lbl)

        card.body.addStretch(1)   # keep contents top-aligned — see _folders_card
        return card

    # ------------------------------------------------------- lean refresh
    def _lean_refresh_card(self) -> Card:
        """Staleness list of every tracked channel plus one-click batch
        re-fetch of the stale ones — incremental, only the months since each
        was last fetched (see tools.lean_refresh). The batch buttons cover
        the common cases; per-row tick boxes + "Refresh selected" handle an
        arbitrary hand-picked set. The list defaults to oldest-fetch-first
        and re-sorts on any column-header click (_on_lean_header_clicked);
        it shows up to _LEAN_VISIBLE_ROWS rows before it scrolls. The
        folder-scoped "Refresh comments" control also lives here (moved from
        the Folders card)."""
        card = SectionCard(self.tr_("lean_refresh_title"))
        self.lean_card_ref = card

        self._lean_sort_col = 1        # Updated
        self._lean_sort_desc = False   # oldest fetch first

        self.lean_help_lbl = QLabel(self.tr_("lean_refresh_help"))
        self.lean_help_lbl.setObjectName("hint")
        self.lean_help_lbl.setWordWrap(True)
        card.body.addWidget(self.lean_help_lbl)

        # "Refresh mentions": same lightweight partial re-fetch shape as
        # "Refresh comments" below, but patches `links` instead — only on
        # rows that already have text, since a post with no caption can't
        # carry a link either (see tools.mentions_refresh). For a folder
        # whose channels were fetched before link-extraction existed, or
        # whose posts' links have otherwise gone stale.
        mentions_row = QHBoxLayout()
        self.mentions_refresh_lbl = QLabel(self.tr_("folder_mentions_refresh_label"))
        mentions_row.addWidget(self.mentions_refresh_lbl)
        self.mentions_folder_combo = QComboBox()
        mentions_row.addWidget(self.mentions_folder_combo, 1)
        self.refresh_mentions_btn = QPushButton(self.tr_("folder_mentions_refresh_btn"))
        self.refresh_mentions_btn.setToolTip(self.tr_("folder_mentions_refresh_hint"))
        self.refresh_mentions_btn.clicked.connect(self._on_refresh_mentions_clicked)
        mentions_row.addWidget(self.refresh_mentions_btn)
        card.body.addLayout(mentions_row)

        # "Refresh comments" (moved here from the Folders card): a lightweight
        # partial re-fetch that only patches the `comments` field on each
        # channel's already-stored checkpoint rows (see tools.comments_refresh)
        # instead of a full re-scan — for a folder whose channels were fetched
        # before that field existed, or whose comment counts have gone stale.
        comments_row = QHBoxLayout()
        self.comments_refresh_lbl = QLabel(self.tr_("folder_comments_refresh_label"))
        comments_row.addWidget(self.comments_refresh_lbl)
        self.comments_folder_combo = QComboBox()
        comments_row.addWidget(self.comments_folder_combo, 1)
        self.refresh_comments_btn = QPushButton(self.tr_("folder_comments_refresh_btn"))
        self.refresh_comments_btn.setToolTip(self.tr_("folder_comments_refresh_hint"))
        self.refresh_comments_btn.clicked.connect(self._on_refresh_comments_clicked)
        comments_row.addWidget(self.refresh_comments_btn)
        card.body.addLayout(comments_row)

        btn_row = QHBoxLayout()
        self.lean_oldest_btn = QPushButton(self.tr_("lean_refresh_oldest_btn"))
        self.lean_oldest_btn.setToolTip(self.tr_("lean_refresh_oldest_hint"))
        self.lean_oldest_btn.clicked.connect(lambda: self._on_lean_refresh_clicked("oldest10"))
        btn_row.addWidget(self.lean_oldest_btn)
        self.lean_1mo_btn = QPushButton(self.tr_("lean_refresh_1mo_btn"))
        self.lean_1mo_btn.setToolTip(self.tr_("lean_refresh_1mo_hint"))
        self.lean_1mo_btn.clicked.connect(lambda: self._on_lean_refresh_clicked("1mo"))
        btn_row.addWidget(self.lean_1mo_btn)
        self.lean_3mo_btn = QPushButton(self.tr_("lean_refresh_3mo_btn"))
        self.lean_3mo_btn.setToolTip(self.tr_("lean_refresh_3mo_hint"))
        self.lean_3mo_btn.clicked.connect(lambda: self._on_lean_refresh_clicked("3mo"))
        btn_row.addWidget(self.lean_3mo_btn)
        self.lean_selected_btn = QPushButton(self.tr_("lean_refresh_selected_btn"))
        self.lean_selected_btn.setToolTip(self.tr_("lean_refresh_selected_hint"))
        self.lean_selected_btn.clicked.connect(self._on_lean_refresh_selected)
        btn_row.addWidget(self.lean_selected_btn)
        self.lean_selected_2y_btn = QPushButton(self.tr_("lean_refresh_selected_2y_btn"))
        self.lean_selected_2y_btn.setToolTip(self.tr_("lean_refresh_selected_2y_hint"))
        self.lean_selected_2y_btn.clicked.connect(self._on_lean_refresh_selected_2y)
        btn_row.addWidget(self.lean_selected_2y_btn)
        self.lean_selected_all_btn = QPushButton(self.tr_("lean_refresh_selected_all_btn"))
        self.lean_selected_all_btn.setToolTip(self.tr_("lean_refresh_selected_all_hint"))
        self.lean_selected_all_btn.clicked.connect(self._on_lean_refresh_selected_all)
        btn_row.addWidget(self.lean_selected_all_btn)
        self.refetch_mentions_btn = QPushButton(self.tr_("lean_refetch_mentions_btn"))
        self.refetch_mentions_btn.setToolTip(self.tr_("lean_refetch_mentions_hint"))
        self.refetch_mentions_btn.clicked.connect(self._on_refetch_mentions_selected)
        btn_row.addWidget(self.refetch_mentions_btn)
        btn_row.addStretch()
        card.body.addLayout(btn_row)

        self.lean_empty_lbl = QLabel(self.tr_("lean_refresh_empty"))
        self.lean_empty_lbl.setObjectName("hint")
        card.body.addWidget(self.lean_empty_lbl)

        # col 0 = a per-row tick for "Refresh selected"; the checkboxes live
        # in the cells (see refresh_lean_list), so edit triggers stay off but
        # the check state is still user-toggleable. Clicking any other column
        # header re-sorts the list (_on_lean_header_clicked).
        self.lean_table = QTableWidget(0, 5)
        self._set_lean_headers()
        self.lean_table.verticalHeader().setVisible(False)
        self.lean_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lean_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lean_header = self.lean_table.horizontalHeader()
        lean_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lean_header.setSectionsClickable(True)
        lean_header.sectionClicked.connect(self._on_lean_header_clicked)
        self.lean_table.setColumnWidth(0, 34)
        self.lean_table.setColumnWidth(1, 150)
        self.lean_table.setColumnWidth(3, 140)
        self.lean_table.setColumnWidth(4, 110)
        card.body.addWidget(self.lean_table)

        self.refresh_lean_list()
        return card

    # Rows to keep visible before the list starts scrolling.
    _LEAN_VISIBLE_ROWS = 20
    # Table columns that re-sort the list when their header is clicked
    # (0 is the tick box). 4 (followers) defaults to descending, the rest asc.
    _LEAN_SORT_COLS = (1, 2, 3, 4)

    def _fit_lean_table_height(self, row_count: int) -> None:
        """Lock the table to show up to _LEAN_VISIBLE_ROWS rows, scrolling
        past that — the surrounding card would otherwise collapse it to a
        few rows."""
        shown = max(3, min(row_count, self._LEAN_VISIBLE_ROWS))
        row_h = self.lean_table.verticalHeader().defaultSectionSize()
        header_h = self.lean_table.horizontalHeader().height() or row_h
        self.lean_table.setFixedHeight(
            header_h + row_h * shown + 2 * self.lean_table.frameWidth())

    def _set_lean_headers(self) -> None:
        self.lean_table.setHorizontalHeaderLabels([
            "", self.tr_("lean_refresh_col_updated"),
            self.tr_("lean_refresh_col_channel"), self.tr_("folder_export_col_folder"),
            self.tr_("col_followers")])

    def _on_lean_header_clicked(self, col: int) -> None:
        if col not in self._LEAN_SORT_COLS:
            return
        if col == self._lean_sort_col:
            self._lean_sort_desc = not self._lean_sort_desc
        else:
            self._lean_sort_col = col
            self._lean_sort_desc = col == 4   # followers reads best high-first
        self.refresh_lean_list()

    def _lean_sort_value(self, ch: dict, folder: str):
        col = self._lean_sort_col
        if col == 2:
            return _channel_display_name(ch).casefold()
        if col == 3:
            return folder.casefold()
        if col == 4:
            return int(ch.get("members", 0) or 0)
        return ch.get("fetched_at") or ""   # col 1 (Updated); missing sorts first

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _parse_fetched_at(value: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    def _lean_rows_sorted(self) -> list[dict]:
        """Every checkpoint summary, oldest fetch first (an unparseable /
        missing fetched_at sorts to the very top — most in need of a look).
        Fixed age order for the batch buttons; the table's own display order
        follows the clicked column header (see refresh_lean_list)."""
        return sorted(self.channel_store.list(), key=lambda d: d.get("fetched_at") or "")

    def refresh_lean_list(self) -> None:
        folder_names = {f["id"]: f["name"] for f in self.folder_store.list_folders()}
        folder_of = {ch["key"]: folder_names.get(
            self.folder_store.folder_for_channel(ch["key"]), "")
            for ch in self.channel_store.list()}
        rows = sorted(self.channel_store.list(),
                      key=lambda ch: self._lean_sort_value(ch, folder_of[ch["key"]]),
                      reverse=self._lean_sort_desc)
        self.lean_empty_lbl.setVisible(not rows)
        self.lean_table.setVisible(bool(rows))
        self.lean_table.setRowCount(len(rows))
        now = self._utcnow()
        for i, ch in enumerate(rows):
            dt = self._parse_fetched_at(ch.get("fetched_at", ""))
            if dt is not None:
                updated = f"{dt:%Y-%m-%d} ({max(0, (now - dt).days)}d)"
            else:
                updated = ch.get("fetched_at") or "—"
            tick = QTableWidgetItem()
            tick.setFlags((tick.flags() & ~Qt.ItemFlag.ItemIsEditable)
                          | Qt.ItemFlag.ItemIsUserCheckable)
            tick.setCheckState(Qt.CheckState.Unchecked)
            tick.setData(Qt.ItemDataRole.UserRole, ch["key"])
            self.lean_table.setItem(i, 0, tick)
            self.lean_table.setItem(i, 1, QTableWidgetItem(updated))
            self.lean_table.setItem(i, 2, QTableWidgetItem(_channel_display_name(ch)))
            self.lean_table.setItem(i, 3, QTableWidgetItem(folder_of[ch["key"]]))
            self.lean_table.setItem(i, 4, QTableWidgetItem(fmt_int(ch.get("members", 0))))
        hdr = self.lean_table.horizontalHeader()
        hdr.setSortIndicatorShown(self._lean_sort_col in self._LEAN_SORT_COLS)
        hdr.setSortIndicator(
            self._lean_sort_col,
            Qt.SortOrder.DescendingOrder if self._lean_sort_desc
            else Qt.SortOrder.AscendingOrder)
        self._fit_lean_table_height(len(rows))

    def _lean_checked_keys(self) -> list[str]:
        out: list[str] = []
        for i in range(self.lean_table.rowCount()):
            item = self.lean_table.item(i, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def _lean_candidates(self, mode: str) -> list[dict]:
        rows = self._lean_rows_sorted()
        if mode == "oldest10":
            return rows[:10]
        days = 30 if mode == "1mo" else 91
        cutoff = self._utcnow() - timedelta(days=days)
        out = []
        for ch in rows:
            dt = self._parse_fetched_at(ch.get("fetched_at", ""))
            if dt is None or dt < cutoff:
                out.append(ch)
        return out

    def _on_lean_refresh_clicked(self, mode: str) -> None:
        cands = self._lean_candidates(mode)
        if not cands:
            QMessageBox.information(self, self.tr_("app_title"),
                                   self.tr_("lean_refresh_none"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("lean_refresh_confirm", count=len(cands)))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.lean_refresh([ch["key"] for ch in cands])

    def _on_lean_refresh_selected(self) -> None:
        keys = self._lean_checked_keys()
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                   self.tr_("lean_refresh_none_selected"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("lean_refresh_confirm", count=len(keys)))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.lean_refresh(keys)

    def _on_lean_refresh_selected_2y(self) -> None:
        keys = self._lean_checked_keys()
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                   self.tr_("lean_refresh_none_selected"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("lean_refresh_full_confirm", count=len(keys)))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.lean_refresh(keys, full_period="2y")

    def _on_lean_refresh_selected_all(self) -> None:
        keys = self._lean_checked_keys()
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                   self.tr_("lean_refresh_none_selected"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("lean_refresh_all_confirm", count=len(keys)))
        if reply != QMessageBox.StandardButton.Yes:
            return
        # "all" isn't a channel_stat.PERIOD_DAYS key, so period_cutoff()
        # falls through to its own no-cutoff default -- the same "any
        # unrecognized/empty key means all-time" rule the whole period
        # system already runs on, not a special case bolted on here.
        self.lean_refresh(keys, full_period="all")

    def lean_refresh(self, keys: list[str], full_period: str | None = None) -> bool:
        """Start a lean (incremental) refresh of `keys` — the months since
        each was last fetched, merged in (see tools.lean_refresh). Shared by
        the Config card's batch buttons and the dashboard's Refresh button.
        With `full_period` (a channel_stat.PERIOD_DAYS key such as "2y", or
        "all" — deliberately not a real key, so period_cutoff() falls
        through to no cutoff at all) every key is instead fully re-scanned
        over that window — slower, but the only way to rebuild a channel's
        older history against current per-post fields. Returns True if a
        worker was started."""
        keys = [k for k in keys if k]
        if not keys:
            return False
        self._store_fields()
        if not self._has_conn():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return False
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return False

        self.log_view.clear()
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        self.worker = ToolWorker(run_lean_refresh,
                                 {"keys": keys, "full_period": full_period},
                                 conn, parent=self)
        self.worker.sig_log.connect(self._append_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_ask.connect(self._on_ask)
        self.worker.sig_done.connect(self._on_lean_refresh_done)
        self._set_busy(True)
        self.progress.setRange(0, 0)
        self.worker.start()
        return True

    def _on_lean_refresh_done(self, ok: bool, msg: str) -> None:
        self._set_busy(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if ok else 0)
        self.worker = None
        self.refresh_lean_list()
        if not ok:
            self._append_log(self.tr_("done_fail", msg=msg))
        # Each channel is saved as it finishes, so even a cancelled/failed
        # run has changed data on disk — always resync.
        self.checkpoints_changed.emit()

    def _set_busy(self, busy: bool) -> None:
        for btn in self._busy_btns:
            btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        if busy:
            # Bring the Config screen up so its progress bar / log are visible
            # even when the job was kicked off from another view's card.
            self.worker_started.emit()

    def refresh_tags_list(self) -> None:
        """One tag per line, biggest tag (most assigned channels) first,
        each followed by up to 3 of its biggest channels by followers — a
        quick "what's actually in this tag" glance without opening the
        sidebar's right-click menu on every channel."""
        tags = self.tag_store.list_tags()
        if not tags:
            self.tags_list_lbl.setText(self.tr_("tag_list_empty"))
            return
        summaries = {s["key"]: s for s in self.channel_store.list()}
        channels_by_tag: dict[str, list[dict]] = {}
        for key, name in self.tag_store.assignments.items():
            channels_by_tag.setdefault(name, []).append(summaries.get(key, {"key": key}))

        rows = []
        for t in tags:
            channels = sorted(channels_by_tag.get(t["name"], []),
                              key=lambda c: c.get("members", 0) or 0, reverse=True)
            rows.append((len(channels), t["name"], channels[:3]))
        rows.sort(key=lambda r: r[0], reverse=True)

        lines = []
        for count, name, top in rows:
            examples = ", ".join(html.escape(_channel_display_name(c)) for c in top)
            suffix = f" ({examples})" if examples else ""
            lines.append(f"{count} — {html.escape(name)}{suffix}")
        self.tags_list_lbl.setText("<br>".join(lines))

    def _on_load_tags_md(self) -> None:
        start_dir = str(Path(self.tag_store.source_path).parent) if self.tag_store.source_path \
            else str(Path.home() / "Desktop")
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr_("tag_load_md_btn"), start_dir, "Markdown (*.md)")
        if not path:
            return
        try:
            n = self.tag_store.load_from_md(path)
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        if n == 0:
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("tag_load_md_empty"))
            return
        self.refresh_tags_list()
        self.tags_changed.emit()
        QMessageBox.information(self, self.tr_("app_title"), self.tr_("tag_load_md_done", n=n))

    def refresh_folders_list(self) -> None:
        current_mentions_folder_id = self.mentions_folder_combo.currentData()
        self.mentions_folder_combo.blockSignals(True)
        self.mentions_folder_combo.clear()
        for folder in self.folder_store.list_folders():
            self.mentions_folder_combo.addItem(folder["name"], folder["id"])
        if self.mentions_folder_combo.count():
            idx = self.mentions_folder_combo.findData(current_mentions_folder_id)
            self.mentions_folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.mentions_folder_combo.blockSignals(False)
        has_mentions_folders = self.mentions_folder_combo.count() > 0
        self.mentions_refresh_lbl.setVisible(has_mentions_folders)
        self.mentions_folder_combo.setVisible(has_mentions_folders)
        self.refresh_mentions_btn.setVisible(has_mentions_folders)

        current_folder_id = self.comments_folder_combo.currentData()
        self.comments_folder_combo.blockSignals(True)
        self.comments_folder_combo.clear()
        for folder in self.folder_store.list_folders():
            self.comments_folder_combo.addItem(folder["name"], folder["id"])
        if self.comments_folder_combo.count():
            idx = self.comments_folder_combo.findData(current_folder_id)
            self.comments_folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.comments_folder_combo.blockSignals(False)
        has_folders = self.comments_folder_combo.count() > 0
        self.comments_refresh_lbl.setVisible(has_folders)
        self.comments_folder_combo.setVisible(has_folders)
        self.refresh_comments_btn.setVisible(has_folders)

        current_assign_id = self.assign_all_combo.currentData()
        self.assign_all_combo.blockSignals(True)
        self.assign_all_combo.clear()
        for folder in self.folder_store.list_folders():
            self.assign_all_combo.addItem(folder["name"], folder["id"])
        if self.assign_all_combo.count():
            idx = self.assign_all_combo.findData(current_assign_id)
            self.assign_all_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.assign_all_combo.blockSignals(False)
        self.assign_all_lbl.setVisible(has_folders)
        self.assign_all_combo.setVisible(has_folders)
        self.assign_all_btn.setVisible(has_folders)

        current_export_folder_id = self.mentions_export_folder_combo.currentData()
        self.mentions_export_folder_combo.blockSignals(True)
        self.mentions_export_folder_combo.clear()
        for folder in self.folder_store.list_folders():
            self.mentions_export_folder_combo.addItem(folder["name"], folder["id"])
        if self.mentions_export_folder_combo.count():
            idx = self.mentions_export_folder_combo.findData(current_export_folder_id)
            self.mentions_export_folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.mentions_export_folder_combo.blockSignals(False)
        has_export_folders = self.mentions_export_folder_combo.count() > 0
        self.mentions_export_folder_lbl.setVisible(has_export_folders)
        self.mentions_export_folder_combo.setVisible(has_export_folders)
        self.export_link_report_btn.setVisible(has_export_folders)
        self.export_mentions_report_btn.setVisible(has_export_folders)
        self.mentions_export_empty_lbl.setVisible(not has_export_folders)

        self.refresh_export_periods()

        folders = self.folder_store.list_folders()
        if not folders:
            self.folders_list_lbl.setText(self.tr_("folder_list_empty"))
            return
        counts: dict[str, int] = {}
        for fid in self.folder_store.assignments.values():
            counts[fid] = counts.get(fid, 0) + 1
        chips = [
            f'<span style="color:{html.escape(f["color"])};">&#9679;</span> '
            f'{html.escape(f["name"])} ({counts.get(f["id"], 0)})'
            for f in folders
        ]
        self.folders_list_lbl.setText("&nbsp;&nbsp;&nbsp;".join(chips))

    def _collect_export_periods(self) -> list[tuple[str, str, tuple]]:
        """(label, mode, period_key) for every Half-Year bucket across all
        tracked channels regardless of folder (newest first), then every
        Season bucket the same way — matches FolderStatView's own period
        picker (see its _collect_all_period_keys), just flattened into one
        combo here instead of mode buttons on their own page."""
        def keys_for(mode: str) -> list[tuple[str, str, tuple]]:
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
            return [(label, mode, key) for key, label in
                    sorted(keys.items(), key=lambda kv: kv[0], reverse=True)]

        return keys_for("halfyear") + keys_for("season")

    def refresh_export_periods(self) -> None:
        current = self.folders_export_period_combo.currentData()
        self.folders_export_period_combo.blockSignals(True)
        self.folders_export_period_combo.clear()
        for label, mode, key in self._collect_export_periods():
            self.folders_export_period_combo.addItem(label, (mode, key))
        self.folders_export_period_combo.addItem(self.tr_("period_year_all"), ("all", None))
        # Not combo.findData(current) — PySide6 can't reliably match a
        # tuple-valued itemData (our (mode, period_key) pairs) through
        # QVariant equality, so it always misses and silently resets the
        # selection back to index 0. A plain Python == comparison over
        # itemData works correctly for tuples.
        idx = -1
        if current is not None:
            for i in range(self.folders_export_period_combo.count()):
                if self.folders_export_period_combo.itemData(i) == current:
                    idx = i
                    break
        self.folders_export_period_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.folders_export_period_combo.blockSignals(False)

    def _open_folder_manager(self) -> None:
        dlg = FolderManagerDialog(self.folder_store, self.i18n, self)
        dlg.exec()
        self.refresh_folders_list()
        self.folders_changed.emit()

    # ------------------------------------------------------- folders export
    def _build_folders_md(self) -> str:
        """One row per tracked channel, grouped/sorted exactly like the
        sidebar's "Sort Fols" toggle (see FolderStore.sorted_by_folder) —
        folder list order, unassigned channels last, followers descending
        within each group. Rating/Views/Viral share (see
        folders_export_extra_chk) reuse app.rating.score_entries so they
        come out numerically identical to what Folder Stats itself would
        show for the same folder/period — see _collect_export_metrics."""
        summaries = self.folder_store.sorted_by_folder(self.channel_store.list())
        folder_name = {f["id"]: f["name"] for f in self.folder_store.list_folders()}
        extra = self.folders_export_extra_chk.isChecked()

        headers = [self.tr_("folder_export_col_folder"), self.tr_("folder_export_col_followers"),
                  self.tr_("folder_export_col_id"), self.tr_("folder_export_col_tag")]
        if extra:
            headers += [self.tr_("col_rating"), self.tr_("col_views"),
                       self.tr_("col_viral_share"), self.tr_("col_post_quality")]
        lines = ["| " + " | ".join(headers) + " |",
                 "| " + " | ".join(["---"] * len(headers)) + " |"]

        metrics_by_key: dict[str, tuple] = {}
        if extra:
            mode, target_key = self.folders_export_period_combo.currentData() or ("all", None)
            metrics_by_key = self._collect_export_metrics(summaries, mode, target_key)

        for ch in summaries:
            fid = self.folder_store.folder_for_channel(ch["key"])
            folder = folder_name.get(fid, self.tr_("folder_none"))
            username = ch.get("username") or ""
            if username:
                ident = f"@{username}"
            else:
                # No public username to link to — a title snippet reads
                # better in the exported table than the bare checkpoint
                # key, with the id still there in parens for a lookup.
                # "|" would otherwise be read as a column separator and
                # break the row, so it's stripped rather than escaped.
                title = (ch.get("title") or ch["key"]).replace("|", "")
                ident = f"{title[:18]}({ch['key']})"
            tag = self.tag_store.tag_for_channel(ch["key"]) or ""
            row = [folder, fmt_int(ch.get("members", 0)), ident, tag]
            if extra:
                metrics = metrics_by_key.get(ch["key"])
                if metrics is None:
                    row += ["—", "—", "—", "—"]
                else:
                    score, views, viral_share, quality = metrics
                    row += [f"{score:.3f}", fmt_int(views), f"{viral_share:.1f}%",
                            str(round(quality))]
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines) + "\n"

    def _collect_export_metrics(self, summaries: list[dict], mode: str,
                                target_key: tuple | None) -> dict[str, tuple]:
        """channel key -> (score, views, viral_share_pct, quality), scored
        exactly like FolderStatView's Periodic Stats: entries are grouped by folder
        (channels with no folder form their own group) and normalized
        against only their own group's peers for the same period, via
        app.rating.score_entries — that's what makes a channel's Rating
        here match what Folder Stats itself would show, unlike a
        channel-global metric which couldn't reproduce that per-folder
        normalization at all."""
        groups: dict[str | None, list[str]] = {}
        for ch in summaries:
            fid = self.folder_store.folder_for_channel(ch["key"])
            groups.setdefault(fid, []).append(ch["key"])

        out: dict[str, tuple] = {}
        for keys in groups.values():
            entries = []
            for key in keys:
                data = self.channel_store.load(key) or {}
                totals = self._channel_bucket_totals(data, mode, target_key)
                if totals is None:
                    continue
                entries.append({"key": key, **totals})
            if not entries:
                continue
            score_entries(entries)
            for e in entries:
                # quality_display (readable 0-1000 gauge), not the Rating's
                # internal quality term — see _channel_bucket_totals.
                out[e["key"]] = (e["score"], e["views"], e["viral_share"],
                                 e["quality_display"])
        return out

    @staticmethod
    def _channel_bucket_totals(data: dict, mode: str,
                               target_key: tuple | None) -> dict | None:
        """{"views","shares","reactions","posts","viral_share","repost_share",
        "quality","quality_display"} for one channel's full checkpoint, scoped
        to `mode` — "all" sums every scanned post ever
        (`distributions.monthly`); "halfyear"/"season" use just the one bucket
        matching `target_key`. None if the channel has no data in scope at all
        (empty monthly history for "all", or no bucket matching `target_key`),
        so the caller can tell "genuinely no data" apart from "scored zero".

        Two quality figures ride along, both app.scoring's 0-1000 gauge over
        the in-scope slice of the top-N `rows` pool (same caveat as Folder
        Stats — `rows` is a sample). `quality_display` is the standard gauge
        (viral_excess on, mean) — the readable "Post Quality" column, the
        same number the dashboard shows. `quality` is the Rating's own take:
        median (the pool skews to a channel's best posts) and
        `viral_excess=False` (the Rating already scores reach via its views +
        virality terms). Kept apart so tuning the Rating never makes the
        displayed column unrecognisable. `posts` (the period's post count)
        rides along so app.rating can score views/engagement per post.

        Reposts (posts forwarded in from another channel — see
        app.tools.channel_stat._is_repost) are excluded, exactly as in
        FolderStatView._collect_periods: the monthly `*_own` totals drop
        them, `_quality` skips `rows` entries flagged `repost`, and the
        returned `repost_share` (percent of the period's posts that were
        reposts) feeds app.rating's smooth repost-heavy score cut. Older
        checkpoints without those fields fall back to counting every post,
        report repost_share 0, and are unchanged until refetched."""
        monthly = data.get("distributions", {}).get("monthly") or []
        stats_ = data.get("stats", {})
        # Recent average where available (a grown channel shouldn't score
        # every recent post as a breakout — see channel_stat._recent_avg_views
        # / FolderStatView._channel_avg_views), lifetime otherwise.
        avg_views = float(stats_.get("avg_views_recent") or stats_.get("avg_views", 0) or 0)

        def _quality(rows: list[dict]) -> tuple[float, float]:
            """(rating_quality, display_quality) — see docstring."""
            rows = [r for r in rows if not r.get("repost")]
            if not rows:
                return 0.0, 0.0
            rating = median(post_gauge_value(post_score_raw(r, avg_views, viral_excess=False))
                            for r in rows)
            display = sum(post_gauge_value(post_score_raw(r, avg_views))
                          for r in rows) / len(rows)
            return rating, display

        def _own(m: dict, field: str) -> int:
            return int(m.get(f"{field}_own", m.get(field, 0)) or 0)

        def _repost_share(own: int, total: int) -> float:
            return (total - own) / total * 100 if total else 0

        def _viral_share(viral: int, count: int) -> float:
            return min(viral / count * 100 if count else 0, VIRAL_SHARE_DISPLAY_CAP)

        if mode == "all":
            if not monthly:
                return None
            count = sum(_own(m, "count") for m in monthly)
            total = sum(int(m.get("count", 0) or 0) for m in monthly)
            viral_count = sum(_own(m, "viral_count") for m in monthly)
            quality, quality_display = _quality(data.get("rows", []) or [])
            return {
                "views": sum(_own(m, "views") for m in monthly),
                "shares": sum(_own(m, "shares") for m in monthly),
                "reactions": sum(_own(m, "reactions") for m in monthly),
                "posts": count,   # app.rating divides views/engagement by this
                "viral_share": _viral_share(viral_count, count),
                "repost_share": _repost_share(count, total),
                "quality": quality,
                "quality_display": quality_display,
            }

        buckets: dict[tuple, dict] = {}
        for m in monthly:
            count = int(m.get("count", 0) or 0)
            if not count:
                continue
            try:
                year, month = (int(x) for x in m.get("label", "").split("-"))
            except ValueError:
                continue
            key, _label = period_key_label(year, month, mode)
            b = buckets.setdefault(key, {"count": 0, "count_total": 0, "views": 0,
                                         "shares": 0, "reactions": 0, "viral_count": 0})
            b["count"] += _own(m, "count")
            b["count_total"] += count
            b["views"] += _own(m, "views")
            b["shares"] += _own(m, "shares")
            b["reactions"] += _own(m, "reactions")
            b["viral_count"] += _own(m, "viral_count")
        if target_key not in buckets:
            return None
        b = buckets[target_key]

        period_rows = []
        for r in data.get("rows", []) or []:
            try:
                dt = datetime.fromisoformat((r.get("date") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if period_key_label(dt.year, dt.month, mode)[0] == target_key:
                period_rows.append(r)
        quality, quality_display = _quality(period_rows)
        return {
            "views": b["views"], "shares": b["shares"], "reactions": b["reactions"],
            "posts": b["count"],   # app.rating divides views/engagement by this
            "viral_share": _viral_share(b["viral_count"], b["count"]),
            "repost_share": _repost_share(b["count"], b["count_total"]),
            "quality": quality,
            "quality_display": quality_display,
        }

    def _on_export_folders_md(self) -> None:
        if not self.channel_store.list():
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("report_empty"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("folder_export_md_btn"), "folders.md", "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_folders_md())
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("md_saved", path=path))

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_config"))
        self.sub_lbl.setText(self.tr_("app_title"))
        self.profile_lbl.setText(self.tr_("profile"))
        self.new_profile_btn.setText(self.tr_("new_profile"))
        self.del_profile_btn.setText(self.tr_("delete_profile"))
        for key, edit in self.edits.items():
            lbl = self.conn_form.labelForField(edit)
            if lbl:
                lbl.setText(self.tr_(f"field_{key}"))
        self.save_btn.setText(self.tr_("save"))
        self.qr_btn.setText(self.tr_("qr_login_button"))
        self.check_login_btn.setText(self.tr_("check_login_button"))
        self.loc_lbl.setText(self.tr_("config_location", path=str(self.cfg.path)))
        self.open_folder_btn.setText(self.tr_("open_config_folder"))
        self.theme_lbl.setText(self.tr_("menu_theme"))
        self.theme_combo.blockSignals(True)
        for i, p in enumerate(_THEME_PREFS):
            self.theme_combo.setItemText(i, self.tr_(f"theme_{p}"))
        self.theme_combo.blockSignals(False)
        self.sync_theme_combo()

        self.fetch_card_ref.title_lbl.setText(self.tr_("fetch_title"))
        self.fetch_help_lbl.setText(self.tr_("fetch_help"))
        self.channel_lbl.setText(self.tr_("fetch_channel"))
        self.channel_edit.setPlaceholderText(self.tr_("fetch_channel_placeholder"))
        lbl = self.fetch_form.labelForField(self.period_combo)
        if lbl:
            lbl.setText(self.tr_("fetch_period"))
        for i, k in enumerate(PERIOD_KEYS):
            self.period_combo.setItemText(i, self.tr_(f"period_{k}"))
        self.public_check.setText(self.tr_("fetch_public"))
        self.fetch_btn.setText(self.tr_("fetch_button"))
        self.stop_btn.setText(self.tr_("stop"))

        self.instructions_box.setTitle(self.tr_("instructions_title"))
        self.instructions_info.setText(self.tr_("instructions_text"))

        self.folders_card_ref.title_lbl.setText(self.tr_("folder_section_title"))
        self.folders_help_lbl.setText(self.tr_("folder_manage_help"))
        self.folders_manage_btn.setText(self.tr_("folder_manage"))
        self.folders_export_md_btn.setText(self.tr_("folder_export_md_btn"))
        self.folders_export_md_btn.setToolTip(self.tr_("folder_export_md_hint"))
        self.folders_export_extra_chk.setText(self.tr_("folder_export_extra_cols"))
        self.folders_export_extra_chk.setToolTip(self.tr_("folder_export_extra_cols_hint"))
        self.folders_export_period_combo.setToolTip(self.tr_("folder_export_period_hint"))
        self.refresh_export_periods()
        self.comments_refresh_lbl.setText(self.tr_("folder_comments_refresh_label"))
        self.refresh_comments_btn.setText(self.tr_("folder_comments_refresh_btn"))
        self.refresh_comments_btn.setToolTip(self.tr_("folder_comments_refresh_hint"))
        self.assign_all_lbl.setText(self.tr_("folder_assign_all_label"))
        self.assign_all_btn.setText(self.tr_("folder_assign_all_btn"))
        self.assign_all_btn.setToolTip(self.tr_("folder_assign_all_hint"))
        self.refresh_folders_list()

        self.tags_card_ref.title_lbl.setText(self.tr_("tag_section_title"))
        self.tags_help_lbl.setText(self.tr_("tag_manage_help"))
        self.tags_load_btn.setText(self.tr_("tag_load_md_btn"))
        self.tags_load_btn.setToolTip(self.tr_("tag_load_md_hint"))
        self.refresh_tags_list()

        self.lean_card_ref.title_lbl.setText(self.tr_("lean_refresh_title"))
        self.lean_help_lbl.setText(self.tr_("lean_refresh_help"))
        self.lean_oldest_btn.setText(self.tr_("lean_refresh_oldest_btn"))
        self.lean_oldest_btn.setToolTip(self.tr_("lean_refresh_oldest_hint"))
        self.lean_1mo_btn.setText(self.tr_("lean_refresh_1mo_btn"))
        self.lean_1mo_btn.setToolTip(self.tr_("lean_refresh_1mo_hint"))
        self.lean_3mo_btn.setText(self.tr_("lean_refresh_3mo_btn"))
        self.lean_3mo_btn.setToolTip(self.tr_("lean_refresh_3mo_hint"))
        self.lean_selected_btn.setText(self.tr_("lean_refresh_selected_btn"))
        self.lean_selected_btn.setToolTip(self.tr_("lean_refresh_selected_hint"))
        self.lean_selected_2y_btn.setText(self.tr_("lean_refresh_selected_2y_btn"))
        self.lean_selected_2y_btn.setToolTip(self.tr_("lean_refresh_selected_2y_hint"))
        self.lean_selected_all_btn.setText(self.tr_("lean_refresh_selected_all_btn"))
        self.lean_selected_all_btn.setToolTip(self.tr_("lean_refresh_selected_all_hint"))
        self.refetch_mentions_btn.setText(self.tr_("lean_refetch_mentions_btn"))
        self.refetch_mentions_btn.setToolTip(self.tr_("lean_refetch_mentions_hint"))
        self.lean_empty_lbl.setText(self.tr_("lean_refresh_empty"))
        self._set_lean_headers()
        self.refresh_lean_list()

        self.mentions_export_card_ref.title_lbl.setText(self.tr_("mentions_export_title"))
        self.mentions_export_help_lbl.setText(self.tr_("mentions_export_help"))
        self.mentions_export_folder_lbl.setText(self.tr_("mentions_export_folder_label"))
        self.export_link_report_btn.setText(self.tr_("mentions_export_link_btn"))
        self.export_link_report_btn.setToolTip(self.tr_("mentions_export_link_hint"))
        self.export_mentions_report_btn.setText(self.tr_("mentions_export_mentions_btn"))
        self.export_mentions_report_btn.setToolTip(self.tr_("mentions_export_mentions_hint"))
        self.mentions_export_empty_lbl.setText(self.tr_("folder_list_empty"))
        self.refresh_folders_list()

    # ------------------------------------------------------ field helpers
    def _load_fields(self) -> None:
        for key, edit in self.edits.items():
            edit.setText(self.cfg.get(key))

    def _store_fields(self) -> None:
        for key, edit in self.edits.items():
            self.cfg.profile[key] = edit.text().strip()

    def _save(self) -> None:
        self._store_fields()
        self.cfg.save()
        self.status.setText(self.tr_("saved"))

    def _has_conn(self) -> bool:
        return bool(self.cfg.get("API_ID").strip() and self.cfg.get("API_HASH").strip()
                    and self.cfg.get("PHONE_NUMBER").strip())

    # ------------------------------------------------------------- login
    def _qr_login(self) -> None:
        self._store_fields()
        if not (self.cfg.get("API_ID") and self.cfg.get("API_HASH")):
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return
        QrLoginDialog(self.cfg, self.i18n, self).run_and_report()

    def _check_login(self) -> None:
        self._store_fields()
        if not (self.cfg.get("API_ID") and self.cfg.get("API_HASH")):
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return
        self.check_login_btn.setEnabled(False)
        self.status.setText(self.tr_("check_login_checking"))
        self._login_worker = CheckLoginWorker(
            self.cfg.get("API_ID"), self.cfg.get("API_HASH"),
            self.cfg.session_path(), parent=self)
        self._login_worker.sig_done.connect(self._on_check_login_done)
        self._login_worker.start()

    def _on_check_login_done(self, ok: bool, name: str, phone: str) -> None:
        self.check_login_btn.setEnabled(True)
        if ok:
            self.status.setText(self.tr_("check_login_ok", name=name, phone=phone))
        elif name:
            self.status.setText(self.tr_("done_fail", msg=name))
        else:
            self.status.setText(self.tr_("check_login_not_authorized"))

    # ---------------------------------------------------------- profiles
    def _switch_profile(self, name: str) -> None:
        if not name or name == self.cfg.current_profile:
            return
        self._store_fields()
        self.cfg.current_profile = name
        self.cfg.save()
        self._load_fields()
        self.channel_edit.setText(self.cfg.get("CHANNEL_ID"))

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, self.tr_("new_profile"),
                                        self.tr_("profile_name"))
        if not ok:
            return
        self._store_fields()
        if self.cfg.add_profile(name):
            self.cfg.save()
            self.profile_combo.blockSignals(True)
            self.profile_combo.addItem(name.strip())
            self.profile_combo.setCurrentText(name.strip())
            self.profile_combo.blockSignals(False)
            self._load_fields()

    def _delete_profile(self) -> None:
        name = self.profile_combo.currentText()
        if QMessageBox.question(self, self.tr_("delete_profile"),
                                self.tr_("delete_profile_confirm", name=name)) \
                != QMessageBox.StandardButton.Yes:
            return
        if self.cfg.delete_profile(name):
            self.cfg.save()
            self.profile_combo.blockSignals(True)
            self.profile_combo.removeItem(self.profile_combo.currentIndex())
            self.profile_combo.setCurrentText(self.cfg.current_profile)
            self.profile_combo.blockSignals(False)
            self._load_fields()

    # -------------------------------------------------------------- env io
    def import_env(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("import_env"), "",
                                              "env (*.env *.*)")
        if not path:
            return
        n = self.cfg.import_env(path)
        self._load_fields()
        self.cfg.save()
        self.status.setText(self.tr_("env_imported", n=n))

    def export_env(self) -> None:
        self._store_fields()
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("export_env"), ".env",
                                              "env (*.env *.*)")
        if not path:
            return
        self.cfg.export_env(path)
        self.status.setText(path)

    # --------------------------------------------------------------- fetch
    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _on_fetch_clicked(self) -> None:
        channel = self.channel_edit.text().strip()
        if not channel:
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("fetch_channel"))
            return
        params = {
            "channel": channel,
            "period": PERIOD_KEYS[self.period_combo.currentIndex()],
            "fetch_public": self.public_check.isChecked(),
        }
        self.fetch(params)

    def fetch(self, params: dict) -> None:
        """Start a channel scan. Reused by the dashboard's Re-fetch button."""
        self._store_fields()
        if not self._has_conn():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return
        # Reflect params into the form so the UI matches what's running.
        self.channel_edit.setText(params["channel"])
        if params.get("period") in PERIOD_KEYS:
            self.period_combo.setCurrentIndex(PERIOD_KEYS.index(params["period"]))
        self.public_check.setChecked(bool(params.get("fetch_public")))

        self.cfg.profile["CHANNEL_ID"] = params["channel"]
        self.cfg.save()

        self.log_view.clear()
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        self.worker = ToolWorker(run_channel_stat, params, conn, parent=self)
        self.worker.sig_log.connect(self._append_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_ask.connect(self._on_ask)
        self.worker.sig_done.connect(self._on_fetch_done)

        self._set_busy(True)
        self.progress.setRange(0, 0)
        self.worker.start()

    def _on_stop(self) -> None:
        if self.worker:
            self._append_log(self.tr_("cancelled"))
            self.worker.request_cancel()
            self.stop_btn.setEnabled(False)

    def _append_log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        else:
            self.progress.setRange(0, 0)

    def _on_ask(self, kind: str, _prompt: str) -> None:
        if kind == "password":
            prompt = self.tr_("login_password_prompt")
            echo = QLineEdit.EchoMode.Password
        else:
            prompt = self.tr_("login_code_prompt")
            echo = QLineEdit.EchoMode.Normal
        text, ok = QInputDialog.getText(self, self.tr_("login_title"), prompt, echo)
        if not self.worker:
            return
        if ok and text.strip():
            self.worker.provide_answer(text.strip())
        else:
            self.worker.request_cancel()

    def _on_fetch_done(self, ok: bool, msg: str) -> None:
        self._set_busy(False)
        payload = None
        if ok:
            try:
                payload = json.loads(msg)
            except (ValueError, TypeError):
                ok = False
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if ok else 0)
        self.worker = None

        if ok and payload is not None:
            self._append_log(self.tr_("fetch_done",
                                      title=payload.get("title", ""),
                                      n=len(payload.get("rows", [])),
                                      scanned=payload.get("scanned", 0)))
            self.channel_edit.clear()
            self.channel_fetched.emit(payload)
        else:
            self._append_log(self.tr_("done_fail", msg=msg))

    # --------------------------------------------------- refresh comments
    def _on_assign_all_clicked(self) -> None:
        folder_id = self.assign_all_combo.currentData()
        if not folder_id:
            return
        keys = [ch["key"] for ch in self.channel_store.list()]
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("folder_assign_all_none"))
            return
        folder_name = self.assign_all_combo.currentText()
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("folder_assign_all_confirm", count=len(keys), folder=folder_name))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.folder_store.assign_all(folder_id, keys)
        self.refresh_folders_list()
        self.folders_changed.emit()

    def _on_refresh_comments_clicked(self) -> None:
        folder_id = self.comments_folder_combo.currentData()
        if not folder_id:
            return
        keys = [k for k, fid in self.folder_store.assignments.items() if fid == folder_id]
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("folder_stat_empty_channels"))
            return
        self._store_fields()
        if not self._has_conn():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return

        self.log_view.clear()
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        self.worker = ToolWorker(run_comments_refresh, {"keys": keys}, conn, parent=self)
        self.worker.sig_log.connect(self._append_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_ask.connect(self._on_ask)
        self.worker.sig_done.connect(self._on_refresh_comments_done)

        self._set_busy(True)
        self.progress.setRange(0, 0)
        self.worker.start()

    def _on_refresh_comments_done(self, ok: bool, msg: str) -> None:
        self._set_busy(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if ok else 0)
        self.worker = None
        self.refresh_lean_list()
        if ok:
            self.checkpoints_changed.emit()
        else:
            self._append_log(self.tr_("done_fail", msg=msg))

    # --------------------------------------------------- refresh mentions
    def _on_refresh_mentions_clicked(self) -> None:
        folder_id = self.mentions_folder_combo.currentData()
        if not folder_id:
            return
        keys = [k for k, fid in self.folder_store.assignments.items() if fid == folder_id]
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("folder_stat_empty_channels"))
            return
        self.mentions_refresh(keys)

    def _on_refetch_mentions_selected(self) -> None:
        """The Lean refresh table's checkbox-based counterpart to the
        folder-scoped "Refresh mentions for:" row above — same underlying
        tools.mentions_refresh job, just targeting whichever rows are
        ticked in the staleness list instead of a whole folder."""
        keys = self._lean_checked_keys()
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                   self.tr_("lean_refresh_none_selected"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("mentions_refresh_confirm", count=len(keys)))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.mentions_refresh(keys)

    def mentions_refresh(self, keys: list[str]) -> bool:
        """Start a links-only refetch of `keys` (see tools.mentions_refresh)
        — shared by the folder-scoped "Refresh mentions for:" row and the
        Lean refresh table's checkbox-based "Refetch Mentions" button.
        Returns True if a worker was started."""
        keys = [k for k in keys if k]
        if not keys:
            return False
        self._store_fields()
        if not self._has_conn():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return False
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return False

        self.log_view.clear()
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        self.worker = ToolWorker(run_mentions_refresh, {"keys": keys}, conn, parent=self)
        self.worker.sig_log.connect(self._append_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_ask.connect(self._on_ask)
        self.worker.sig_done.connect(self._on_refresh_mentions_done)

        self._set_busy(True)
        self.progress.setRange(0, 0)
        self.worker.start()
        return True

    def _on_refresh_mentions_done(self, ok: bool, msg: str) -> None:
        self._set_busy(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if ok else 0)
        self.worker = None
        self.refresh_lean_list()
        if ok:
            self.checkpoints_changed.emit()
        else:
            self._append_log(self.tr_("done_fail", msg=msg))

    # ------------------------------------------------------ mentions export
    def _on_export_link_report_clicked(self) -> None:
        self._start_mentions_export(run_link_report_export, "link_report_all.md",
                                    "mentions_export_link_btn", self._on_mentions_export_done)

    def _on_export_mentions_report_clicked(self) -> None:
        self._start_mentions_export(
            run_mentions_report_export, "mentions_report_all.md",
            "mentions_export_mentions_btn", self._on_mentions_export_done)

    def _start_mentions_export(self, tool_func, default_name: str, title_key: str,
                               done_slot) -> None:
        """Shared by both Mentions-export buttons — resolve the selected
        folder to channel keys, ask where to save (same native save dialog
        every other export in this app uses, see _on_export_folders_md),
        then run `tool_func` (tools.mentions_export's two entry points) in
        the background the same way every other batch job here does."""
        folder_id = self.mentions_export_folder_combo.currentData()
        if not folder_id:
            return
        keys = [k for k, fid in self.folder_store.assignments.items() if fid == folder_id]
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                   self.tr_("folder_stat_empty_channels"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_(title_key), default_name, "Markdown (*.md)")
        if not path:
            return
        self._store_fields()
        if not self._has_conn():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return

        self.log_view.clear()
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        self._export_out_path = path
        self.worker = ToolWorker(tool_func, {"keys": keys, "out_path": path}, conn, parent=self)
        self.worker.sig_log.connect(self._append_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_ask.connect(self._on_ask)
        self.worker.sig_done.connect(done_slot)
        self._set_busy(True)
        self.progress.setRange(0, 0)
        self.worker.start()

    def _on_mentions_export_done(self, ok: bool, msg: str) -> None:
        self._set_busy(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if ok else 0)
        self.worker = None
        path, self._export_out_path = self._export_out_path, None
        if not ok:
            self._append_log(self.tr_("done_fail", msg=msg))
        elif msg == "ok":
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("md_saved", path=path))
        else:
            # A real run that still had nothing to write (e.g. every
            # checkpoint in the folder was missing/unreadable) — `msg`
            # already explains why (see tools.mentions_export._run_export).
            QMessageBox.information(self, self.tr_("app_title"), msg)
