"""Left sidebar: a Config entry, then one entry per fetched channel.

The channel entries are rebuilt from the checkpoint store whenever a fetch
completes or a channel is removed. Config and every channel share one
QButtonGroup so exactly one is highlighted at a time (like the
analytics_dashboard nav).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..folders import FolderStore
from ..tags import TagStore
from ..text_utils import consonant_abbreviation
from ..version import __version__
from .compare.compare_view import MAX_COMPARE
from .compare.mentions_view import MAX_MENTIONS_COMPARE
from .dashboard_view import short_num
from .folder_dialog import FolderManagerDialog
from .theme import COLORS, fs, sp, zoom_extra
from .widgets import NavButton, folder_icon, hline

_FOLDER_BADGE_LEN = 2
_TAG_BADGE_LEN = 3


class SidePanel(QFrame):
    config_selected = Signal()
    folder_stat_selected = Signal()
    content_quality_selected = Signal()
    mutual_pr_selected = Signal()
    channel_selected = Signal(str)   # checkpoint key
    compare_requested = Signal(list)  # 2-8 checkpoint keys
    compare_mode_off = Signal()
    compare_charts_selected = Signal(list)  # 0-8 checkpoint keys, live-updates
    compare_charts_mode_off = Signal()
    compare_mentions_selected = Signal(list)  # 0-4 checkpoint keys, live-updates
    compare_mentions_mode_off = Signal()
    fold_requested = Signal()
    language_toggle_requested = Signal()
    folders_changed = Signal()
    tags_changed = Signal()

    def __init__(self, i18n, folder_store: FolderStore, tag_store: TagStore,
                parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setObjectName("sidebar")
        self.setFixedWidth(256 + zoom_extra(12))   # wider at Large zoom, so names/nav labels don't clip
        self.compare_mode = False
        self.compare_charts_mode = False
        self.compare_mentions_mode = False
        # Shared by all three multi-select modes on purpose: switching
        # between Compare / Compare Charts / Mentions is meant to carry the
        # current channel selection over, not reset it — see
        # _toggle_compare_mode / _toggle_compare_charts_mode /
        # _toggle_compare_mentions_mode. Mentions' lower MAX_MENTIONS_COMPARE
        # cap just trims to the most-recently-selected on the way in.
        self._selected_keys: list[str] = []
        self._switching_modes = False
        self.folder_store = folder_store
        self.tag_store = tag_store
        self.sort_by_folder = False
        self._last_channels: list[dict] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 10, 16, 18)
        root.setSpacing(0)

        # One slim header row instead of the old brand title: the version tag
        # on the left, the fold button on the right.
        header_row = QHBoxLayout()
        version_lbl = QLabel(f"v{__version__}")
        version_lbl.setStyleSheet(f"color: {COLORS['faint']}; font-size: {fs(10)}px;")
        header_row.addWidget(version_lbl, 1)
        self.fold_btn = QPushButton("◀")
        self.fold_btn.setObjectName("ghost")
        self.fold_btn.setMinimumWidth(28)
        self.fold_btn.setToolTip(i18n.tr("nav_fold_hint"))
        self.fold_btn.clicked.connect(lambda: self.fold_requested.emit())
        header_row.addWidget(self.fold_btn)
        root.addLayout(header_row)
        root.addSpacing(8)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        # The border always lives on a plain QWidget wrapper, never on a
        # NavButton directly — a NavButton has its own internal icon/label/
        # meta child layout, and a "border" stylesheet property applied
        # straight to it renders stray extra line segments past its right
        # edge (a Qt stylesheet quirk with that inner layout), whereas the
        # exact same border on a plain container renders as one clean pill.
        def _bordered(*widgets: QWidget) -> QWidget:
            holder = QWidget()
            holder.setStyleSheet(
                f"border: 1px solid {COLORS['line']}; border-radius: 12px;")
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            for w in widgets:
                lay.addWidget(w, 1 if w is widgets[0] else 0)
            return holder

        self.config_btn = NavButton(None, i18n.tr("nav_config"))
        self.config_btn.setMinimumHeight(sp(36))
        self.config_btn.setStyleSheet(f"padding: {sp(4)}px 0px; border: none;")
        self.config_btn.clicked.connect(lambda: self.config_selected.emit())
        self.group.addButton(self.config_btn)
        self.lang_btn = QPushButton(i18n.lang.upper())
        self.lang_btn.setObjectName("ghost")
        self.lang_btn.setMinimumWidth(36)
        self.lang_btn.setStyleSheet(f"padding: {sp(4)}px 12px; border: none;")
        self.lang_btn.setToolTip(i18n.tr("nav_lang_hint"))
        self.lang_btn.clicked.connect(lambda: self.language_toggle_requested.emit())
        root.addWidget(_bordered(self.config_btn, self.lang_btn))
        root.addSpacing(4)

        # icon_name=None on these three: the label carries its own emoji, so
        # a second SVG icon column would be redundant — left-aligned via
        # #navBtn's QSS the same as every other nav entry.
        self.folder_stat_btn = NavButton(None, i18n.tr("nav_folder_stat"))
        self.folder_stat_btn.setMinimumHeight(sp(36))
        self.folder_stat_btn.setStyleSheet(f"padding: {sp(4)}px 0px; border: none;")
        self.folder_stat_btn.clicked.connect(lambda: self.folder_stat_selected.emit())
        self.group.addButton(self.folder_stat_btn)
        root.addWidget(_bordered(self.folder_stat_btn))
        root.addSpacing(4)

        self.content_quality_btn = NavButton(None, i18n.tr("nav_content_quality"))
        self.content_quality_btn.setMinimumHeight(sp(36))
        self.content_quality_btn.setStyleSheet(f"padding: {sp(4)}px 0px; border: none;")
        self.content_quality_btn.clicked.connect(lambda: self.content_quality_selected.emit())
        self.group.addButton(self.content_quality_btn)
        root.addWidget(_bordered(self.content_quality_btn))
        root.addSpacing(4)

        self.mutual_pr_btn = NavButton(None, i18n.tr("nav_mutual_pr"))
        self.mutual_pr_btn.setMinimumHeight(sp(36))
        self.mutual_pr_btn.setStyleSheet(f"padding: {sp(4)}px 0px; border: none;")
        self.mutual_pr_btn.clicked.connect(lambda: self.mutual_pr_selected.emit())
        self.group.addButton(self.mutual_pr_btn)
        root.addWidget(_bordered(self.mutual_pr_btn))
        root.addSpacing(4)

        # Compare Charts + Compare Metrics + Mentions share one row — none is
        # part of `self.group`: like Compare below, they repurpose channel
        # clicks into a multi-select instead of single-page navigation, so
        # none can be an exclusive-group "current page" entry the way Config
        # and Folder Stats are. Short, plain labels (no emoji/icon prefix)
        # are what makes fitting all three side by side possible without
        # clipping.
        compare_row = QHBoxLayout()
        compare_row.setSpacing(6)
        self.compare_charts_btn = QPushButton(i18n.tr("nav_compare_charts"))
        self.compare_charts_btn.setObjectName("ghost")
        self.compare_charts_btn.setCheckable(True)
        self.compare_charts_btn.setStyleSheet(f"padding: {sp(4)}px 8px; font-size: {fs(13)}px;")
        self.compare_charts_btn.setToolTip(i18n.tr("nav_compare_charts_hint"))
        self.compare_charts_btn.toggled.connect(self._toggle_compare_charts_mode)
        compare_row.addWidget(self.compare_charts_btn, 1)

        self.compare_btn = QPushButton(i18n.tr("nav_compare"))
        self.compare_btn.setObjectName("ghost")
        self.compare_btn.setCheckable(True)
        self.compare_btn.setStyleSheet(f"padding: {sp(4)}px 8px; font-size: {fs(13)}px;")
        self.compare_btn.setToolTip(i18n.tr("nav_compare_hint"))
        self.compare_btn.toggled.connect(self._toggle_compare_mode)
        compare_row.addWidget(self.compare_btn, 1)

        self.mentions_btn = QPushButton(i18n.tr("nav_mentions"))
        self.mentions_btn.setObjectName("ghost")
        self.mentions_btn.setCheckable(True)
        self.mentions_btn.setStyleSheet(f"padding: {sp(4)}px 8px; font-size: {fs(13)}px;")
        self.mentions_btn.setToolTip(i18n.tr("nav_mentions_hint"))
        self.mentions_btn.toggled.connect(self._toggle_compare_mentions_mode)
        compare_row.addWidget(self.mentions_btn, 1)
        root.addLayout(compare_row)
        root.addSpacing(6)

        # Toggle: grouped by folder (folder list order, unassigned last),
        # sorted by followers within each group — instead of the default
        # flat "everyone sorted by followers" list. See
        # FolderStore.sorted_by_folder for the actual ordering.
        self.sort_folders_btn = QPushButton(i18n.tr("nav_sort_folders"))
        self.sort_folders_btn.setObjectName("ghost")
        self.sort_folders_btn.setCheckable(True)
        self.sort_folders_btn.setStyleSheet(f"padding: {sp(4)}px 12px;")
        self.sort_folders_btn.setToolTip(i18n.tr("nav_sort_folders_hint"))
        self.sort_folders_btn.toggled.connect(self._on_sort_folders_toggled)
        root.addWidget(self.sort_folders_btn)
        root.addSpacing(6)
        root.addWidget(hline())

        # Scrollable channel list — no spacing after the divider above: it
        # should sit flush against the list rather than leaving a gap.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.list_lay = QVBoxLayout(holder)
        self.list_lay.setContentsMargins(0, sp(6), 0, sp(6))
        self.list_lay.setSpacing(sp(4))
        self.empty_lbl = QLabel(i18n.tr("nav_no_channels"))
        self.empty_lbl.setObjectName("navEmpty")
        self.empty_lbl.setWordWrap(True)
        self.list_lay.addWidget(self.empty_lbl)
        self.list_lay.addStretch()
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

        self._channel_btns: dict[str, NavButton] = {}


    # ------------------------------------------------------------ rebuild
    def set_channels(self, channels: list[dict]) -> None:
        """channels: [{key, title, members, ...}] — sorted here by members
        desc, or grouped by folder (see FolderStore.sorted_by_folder) when
        the "Sort Fols" toggle is on. Cached in `_last_channels` so the
        toggle can re-sort and rebuild without needing fresh data from the
        caller — see _on_sort_folders_toggled."""
        self._last_channels = list(channels)
        for btn in self._channel_btns.values():
            self.group.removeButton(btn)
            btn.deleteLater()
        self._channel_btns.clear()

        # Everything before the trailing stretch gets cleared except empty_lbl.
        self.empty_lbl.setVisible(not channels)

        if self.sort_by_folder:
            channels = self.folder_store.sorted_by_folder(channels)
        else:
            channels = sorted(channels, key=lambda c: c.get("members", 0) or 0, reverse=True)

        # Drop folder/tag assignments for channels that no longer exist
        # (removed from the sidebar) so folders.json/tags.json don't
        # accumulate dead keys.
        live_keys = {ch["key"] for ch in channels}
        stale = [k for k in self.folder_store.assignments if k not in live_keys]
        for k in stale:
            self.folder_store.set_channel_folder(k, None)
        stale_tags = [k for k in self.tag_store.assignments if k not in live_keys]
        for k in stale_tags:
            self.tag_store.set_channel_tag(k, None)

        self._selected_keys.clear()
        for ch in channels:
            username = ch.get("username") or ""
            label = f"@{username}" if username else (ch.get("title") or ch.get("key", "?"))
            btn = NavButton("reports", label)
            btn.setToolTip(ch.get("channel") or ch.get("title", ""))
            members = ch.get("members", 0) or 0
            btn.set_meta(short_num(members, 1) if members else "")
            key = ch["key"]
            btn.clicked.connect(lambda _=False, k=key: self._on_channel_clicked(k))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, k=key, b=btn: self._show_channel_menu(k, b, pos))
            self.group.addButton(btn)
            # insert above the stretch (last item)
            self.list_lay.insertWidget(self.list_lay.count() - 1, btn)
            self._channel_btns[key] = btn
        self.group.setExclusive(not self._any_compare_mode())
        self.refresh_badges()

    def _on_sort_folders_toggled(self, on: bool) -> None:
        self.sort_by_folder = on
        self._sync_sort_folders_label()
        self.set_channels(self._last_channels)

    def _sync_sort_folders_label(self) -> None:
        # Label describes what the button does FROM here: "Sort by Members"
        # (flat, off) vs "Sort by Folders" (grouped, on) — see
        # FolderStore.sorted_by_folder for the actual ordering.
        key = "nav_sort_folders_active" if self.sort_by_folder else "nav_sort_folders"
        self.sort_folders_btn.setText(self.i18n.tr(key))

    # --------------------------------------------------------- folders/tags
    def refresh_badges(self) -> None:
        """Left-of-label badge per channel: a tag's 3-consonant abbreviation
        if one is assigned (falling back to the channel's folder color, or
        muted if it's not in a folder either), else a folder's 2-consonant
        abbreviation if it's at least in one, else the plain default icon.
        Tags don't carry their own color — see app.tags module docstring."""
        for key, btn in self._channel_btns.items():
            folder_id = self.folder_store.folder_for_channel(key)
            folder = self.folder_store.get_folder(folder_id) if folder_id else None
            tag_name = self.tag_store.tag_for_channel(key)
            if tag_name and self.tag_store.has_tag(tag_name):
                color = folder["color"] if folder else COLORS["muted"]
                tooltip = f"{tag_name} · {folder['name']}" if folder else tag_name
                btn.set_badge(consonant_abbreviation(tag_name, _TAG_BADGE_LEN), color, tooltip)
            elif folder:
                btn.set_badge(consonant_abbreviation(folder["name"], _FOLDER_BADGE_LEN),
                             folder["color"], folder["name"])
            else:
                btn.clear_badge()

    def _show_channel_menu(self, key: str, btn: NavButton, pos) -> None:
        menu = QMenu(self)
        current_folder = self.folder_store.folder_for_channel(key)

        none_act = menu.addAction(self.i18n.tr("folder_none"))
        none_act.setCheckable(True)
        none_act.setChecked(current_folder is None)
        none_act.triggered.connect(lambda: self._assign_folder(key, None))

        folders = self.folder_store.list_folders()
        if folders:
            menu.addSeparator()
            for folder in folders:
                act = menu.addAction(folder_icon(folder["color"]), folder["name"])
                act.setCheckable(True)
                act.setChecked(folder["id"] == current_folder)
                act.triggered.connect(
                    lambda _=False, fid=folder["id"]: self._assign_folder(key, fid))

        menu.addSeparator()
        manage_act = menu.addAction(self.i18n.tr("folder_manage"))
        manage_act.triggered.connect(self._open_folder_manager)

        # Tags are a second, independent single-choice assignment — own
        # section, same None/list shape as folders above minus "Manage"
        # (tags are only ever defined by loading a Markdown file, see
        # ConfigView's Tags card).
        tags = self.tag_store.list_tags()
        if tags:
            menu.addSeparator()
            current_tag = self.tag_store.tag_for_channel(key)
            tag_none_act = menu.addAction(self.i18n.tr("tag_none"))
            tag_none_act.setCheckable(True)
            tag_none_act.setChecked(current_tag is None)
            tag_none_act.triggered.connect(lambda: self._assign_tag(key, None))
            for tag in tags:
                act = menu.addAction(tag["name"])
                act.setCheckable(True)
                act.setChecked(tag["name"] == current_tag)
                act.triggered.connect(
                    lambda _=False, name=tag["name"]: self._assign_tag(key, name))

        menu.exec(btn.mapToGlobal(pos))

    def _assign_folder(self, key: str, folder_id: str | None) -> None:
        self.folder_store.set_channel_folder(key, folder_id)
        self.refresh_badges()
        self.folders_changed.emit()

    def _open_folder_manager(self) -> None:
        dlg = FolderManagerDialog(self.folder_store, self.i18n, self)
        dlg.exec()
        self.refresh_badges()
        self.folders_changed.emit()

    def _assign_tag(self, key: str, name: str | None) -> None:
        self.tag_store.set_channel_tag(key, name)
        self.refresh_badges()
        self.tags_changed.emit()

    # ------------------------------------------------------------- compare
    def _sync_checked_buttons(self, keys: list[str]) -> None:
        key_set = set(keys)
        for key, btn in self._channel_btns.items():
            btn.setChecked(key in key_set)

    def _any_compare_mode(self) -> bool:
        return self.compare_mode or self.compare_charts_mode or self.compare_mentions_mode

    def _exit_other_compare_modes(self, keep: str) -> None:
        """Turn off whichever of the other two multi-select modes are on,
        without clearing `_selected_keys` or emitting "mode off" — see the
        callers below, and the module-level note on why the selection is
        shared across all three."""
        self._switching_modes = True
        if keep != "compare" and self.compare_btn.isChecked():
            self.compare_btn.setChecked(False)
        if keep != "charts" and self.compare_charts_btn.isChecked():
            self.compare_charts_btn.setChecked(False)
        if keep != "mentions" and self.mentions_btn.isChecked():
            self.mentions_btn.setChecked(False)
        self._switching_modes = False

    def _toggle_compare_mode(self, on: bool) -> None:
        self.compare_mode = on
        if on and (self.compare_charts_mode or self.compare_mentions_mode):
            # Mutually exclusive multi-select modes, but switching between
            # them is meant to carry `_selected_keys` over — the guard stops
            # the nested toggle(False) calls this triggers from clearing the
            # selection or emitting "mode off" (which would navigate away,
            # e.g. back to Config) on their way out, since we're headed
            # straight into Compare instead.
            self._exit_other_compare_modes(keep="compare")
        self.group.setExclusive(not self._any_compare_mode())
        if on:
            self.config_btn.setChecked(False)
            self._sync_checked_buttons(self._selected_keys)
            if len(self._selected_keys) >= 2:
                self.compare_requested.emit(list(self._selected_keys))
        elif not self._switching_modes:
            self._selected_keys.clear()
            self._sync_checked_buttons(self._selected_keys)
            self.compare_mode_off.emit()

    def _toggle_compare_charts_mode(self, on: bool) -> None:
        self.compare_charts_mode = on
        if on and (self.compare_mode or self.compare_mentions_mode):
            self._exit_other_compare_modes(keep="charts")  # see _toggle_compare_mode
        self.group.setExclusive(not self._any_compare_mode())
        if on:
            self.config_btn.setChecked(False)
            self.folder_stat_btn.setChecked(False)
            self.content_quality_btn.setChecked(False)
            self._sync_checked_buttons(self._selected_keys)
            self.compare_charts_selected.emit(list(self._selected_keys))
        elif not self._switching_modes:
            self._selected_keys.clear()
            self._sync_checked_buttons(self._selected_keys)
            self.compare_charts_mode_off.emit()

    def _toggle_compare_mentions_mode(self, on: bool) -> None:
        self.compare_mentions_mode = on
        if on and (self.compare_mode or self.compare_charts_mode):
            self._exit_other_compare_modes(keep="mentions")  # see _toggle_compare_mode
        self.group.setExclusive(not self._any_compare_mode())
        if on:
            self.config_btn.setChecked(False)
            self.folder_stat_btn.setChecked(False)
            self.content_quality_btn.setChecked(False)
            # Lower cap than Compare/Compare Charts — trim to the
            # most-recently-selected on the way in rather than silently
            # ignoring the carried-over tail.
            if len(self._selected_keys) > MAX_MENTIONS_COMPARE:
                stale = self._selected_keys[:-MAX_MENTIONS_COMPARE]
                self._selected_keys = self._selected_keys[-MAX_MENTIONS_COMPARE:]
                for k in stale:
                    self._channel_btns[k].setChecked(False)
            self._sync_checked_buttons(self._selected_keys)
            self.compare_mentions_selected.emit(list(self._selected_keys))
        elif not self._switching_modes:
            self._selected_keys.clear()
            self._sync_checked_buttons(self._selected_keys)
            self.compare_mentions_mode_off.emit()

    def _on_channel_clicked(self, key: str) -> None:
        if self.compare_charts_mode:
            btn = self._channel_btns[key]
            if btn.isChecked():
                if len(self._selected_keys) >= MAX_COMPARE:
                    stale = self._selected_keys.pop(0)
                    self._channel_btns[stale].setChecked(False)
                self._selected_keys.append(key)
            elif key in self._selected_keys:
                self._selected_keys.remove(key)
            self.compare_charts_selected.emit(list(self._selected_keys))
            return
        if self.compare_mentions_mode:
            btn = self._channel_btns[key]
            if btn.isChecked():
                if len(self._selected_keys) >= MAX_MENTIONS_COMPARE:
                    stale = self._selected_keys.pop(0)
                    self._channel_btns[stale].setChecked(False)
                self._selected_keys.append(key)
            elif key in self._selected_keys:
                self._selected_keys.remove(key)
            self.compare_mentions_selected.emit(list(self._selected_keys))
            return
        if not self.compare_mode:
            self.channel_selected.emit(key)
            return
        btn = self._channel_btns[key]
        if btn.isChecked():
            if len(self._selected_keys) >= MAX_COMPARE:
                stale = self._selected_keys.pop(0)
                self._channel_btns[stale].setChecked(False)
            self._selected_keys.append(key)
        elif key in self._selected_keys:
            self._selected_keys.remove(key)
        if len(self._selected_keys) >= 2:
            self.compare_requested.emit(list(self._selected_keys))

    # ------------------------------------------------------------- select
    def select_config(self) -> None:
        self.config_btn.setChecked(True)

    def select_folder_stat(self) -> None:
        self.folder_stat_btn.setChecked(True)

    def select_content_quality(self) -> None:
        self.content_quality_btn.setChecked(True)

    def select_mutual_pr(self) -> None:
        self.mutual_pr_btn.setChecked(True)

    def select_channel(self, key: str) -> None:
        btn = self._channel_btns.get(key)
        if btn:
            btn.setChecked(True)

    def has_channel(self, key: str) -> bool:
        return key in self._channel_btns

    def first_channel_key(self) -> str | None:
        return next(iter(self._channel_btns), None)

    def retranslate(self) -> None:
        self.config_btn.set_text(self.i18n.tr("nav_config"))
        self.folder_stat_btn.set_text(self.i18n.tr("nav_folder_stat"))
        self.content_quality_btn.set_text(self.i18n.tr("nav_content_quality"))
        self.mutual_pr_btn.set_text(self.i18n.tr("nav_mutual_pr"))
        self.empty_lbl.setText(self.i18n.tr("nav_no_channels"))
        self.compare_btn.setText(self.i18n.tr("nav_compare"))
        self.compare_btn.setToolTip(self.i18n.tr("nav_compare_hint"))
        self._sync_sort_folders_label()
        self.sort_folders_btn.setToolTip(self.i18n.tr("nav_sort_folders_hint"))
        self.compare_charts_btn.setText(self.i18n.tr("nav_compare_charts"))
        self.compare_charts_btn.setToolTip(self.i18n.tr("nav_compare_charts_hint"))
        self.mentions_btn.setText(self.i18n.tr("nav_mentions"))
        self.mentions_btn.setToolTip(self.i18n.tr("nav_mentions_hint"))
        self.fold_btn.setToolTip(self.i18n.tr("nav_fold_hint"))
        self.lang_btn.setText(self.i18n.lang.upper())
        self.lang_btn.setToolTip(self.i18n.tr("nav_lang_hint"))
