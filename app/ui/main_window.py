"""Main window: sidebar (Config + fetched channels) beside a stacked content
area (the Config screen and a reusable channel dashboard)."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ..config import Config, config_dir
from ..folders import FolderStore
from ..i18n import I18n
from ..store import ChannelStore
from ..tags import TagStore
from .compare.compare_charts_view import CompareChartsView
from .compare.compare_view import CompareView
from .compare.mentions_view import MentionsView
from .config_view import ConfigView
from .content_quality_view import ContentQualityView
from .dashboard_view import DashboardView
from .folder_stat_view import FolderStatView
from .mutual_pr_view import MutualPrView
from .side_panel import SidePanel
from .theme import apply_theme


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config()
        self.i18n = I18n(self.cfg.language)
        self.store = ChannelStore()
        self.folder_store = FolderStore()
        self.tag_store = TagStore()
        self.resize(1240, 860)
        self.setMinimumSize(1040, 720)
        self._current_key: str | None = None   # None => Config screen
        self._refetch_return_key: str | None = None  # dashboard to re-open after a lean refresh
        self._sidebar_folded = False
        self._apply_appearance()
        self._build_ui()
        try:
            # Live-follow the OS appearance while pref == "system".
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                self._on_system_theme_changed)
        except Exception:
            pass

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        self.setWindowTitle(self.i18n.tr("app_title"))

        central = QWidget()
        central.setObjectName("root")
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.side = SidePanel(self.i18n, self.folder_store, self.tag_store)
        self.side.config_selected.connect(self._show_config)
        self.side.folder_stat_selected.connect(self._show_folder_stat)
        self.side.content_quality_selected.connect(self._show_content_quality)
        self.side.mutual_pr_selected.connect(self._show_mutual_pr)
        self.side.channel_selected.connect(self._show_channel)
        self.side.compare_requested.connect(self._show_compare)
        self.side.compare_mode_off.connect(self._on_compare_mode_off)
        self.side.compare_charts_selected.connect(self._show_compare_charts)
        self.side.compare_charts_mode_off.connect(self._on_compare_charts_mode_off)
        self.side.compare_mentions_selected.connect(self._show_mentions)
        self.side.compare_mentions_mode_off.connect(self._on_mentions_mode_off)
        self.side.fold_requested.connect(self._fold_sidebar)
        self.side.language_toggle_requested.connect(self._toggle_language)
        self.side.folders_changed.connect(self._on_folders_changed)
        self.side.tags_changed.connect(self._on_folders_changed)
        lay.addWidget(self.side)

        content_col = QVBoxLayout()
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setSpacing(0)

        top_strip = QHBoxLayout()
        top_strip.setContentsMargins(16, 4, 12, 0)
        self.unfold_btn = QPushButton("▶")
        self.unfold_btn.setObjectName("ghost")
        self.unfold_btn.setMinimumWidth(28)
        self.unfold_btn.setStyleSheet("padding: 4px 12px;")
        self.unfold_btn.setToolTip(self.i18n.tr("nav_unfold_hint"))
        self.unfold_btn.clicked.connect(self._unfold_sidebar)
        self.unfold_btn.setVisible(False)
        top_strip.addWidget(self.unfold_btn)
        top_strip.addStretch()
        content_col.addLayout(top_strip)

        self.stack = QStackedWidget()
        self.config_view = ConfigView(self.cfg, self.i18n, self.folder_store, self.tag_store,
                                      self.store)
        self.config_view.channel_fetched.connect(self._on_channel_fetched)
        self.config_view.folders_changed.connect(self._on_folders_changed)
        self.config_view.tags_changed.connect(self._on_folders_changed)
        self.config_view.checkpoints_changed.connect(self._on_checkpoints_changed)
        self.config_view.worker_started.connect(self._show_config)
        self.config_view.theme_change_requested.connect(self._switch_theme)
        self.config_view.zoom_change_requested.connect(self._switch_zoom)
        self.config_view.accent_change_requested.connect(self._switch_accent)
        self.dashboard = DashboardView(self.i18n, self.folder_store, self.tag_store, self.cfg)
        self.dashboard.refetch_requested.connect(self._on_refetch)
        self.dashboard.remove_requested.connect(self._on_remove)
        self.dashboard.folders_changed.connect(self._on_folders_changed)
        self.dashboard.tags_changed.connect(self._on_folders_changed)
        self.compare = CompareView(self.i18n)
        self.folder_stat = FolderStatView(self.i18n, self.folder_store, self.store)
        # The Folders / Tags management cards are built by ConfigView (all the
        # folder/tag logic lives there) but shown at the top of this view.
        self.folder_stat.mount_taxonomy_cards(self.config_view.folders_card,
                                              self.config_view.tags_card,
                                              self.config_view.mentions_export_card)
        self.compare_charts = CompareChartsView(self.i18n)
        self.content_quality = ContentQualityView(self.i18n, self.folder_store, self.store, self.cfg)
        self.mutual_pr = MutualPrView(self.i18n, self.folder_store, self.store,
                                      self.tag_store)
        self.mentions_view = MentionsView(self.i18n)
        self.stack.addWidget(self.config_view)       # index 0
        self.stack.addWidget(self.dashboard)         # index 1
        self.stack.addWidget(self.compare)           # index 2
        self.stack.addWidget(self.folder_stat)       # index 3
        self.stack.addWidget(self.compare_charts)    # index 4
        self.stack.addWidget(self.content_quality)   # index 5
        self.stack.addWidget(self.mutual_pr)         # index 6
        self.stack.addWidget(self.mentions_view)     # index 7
        content_col.addWidget(self.stack, 1)

        content_wrap = QWidget()
        content_wrap.setLayout(content_col)
        lay.addWidget(content_wrap, 1)

        self.setCentralWidget(central)
        self._build_menu()
        self._refresh_sidebar()

        self.side.setVisible(not self._sidebar_folded)
        self.unfold_btn.setVisible(self._sidebar_folded)

        # Restore selection after a rebuild (language switch).
        if self._current_key and self.side.has_channel(self._current_key):
            self._show_channel(self._current_key)
        else:
            self._current_key = None
            self._show_config()

    def _build_menu(self) -> None:
        tr = self.i18n.tr
        self.menuBar().clear()

        file_menu = self.menuBar().addMenu(tr("menu_file"))
        imp = QAction(tr("import_env"), self)
        imp.triggered.connect(self.config_view.import_env)
        file_menu.addAction(imp)
        exp = QAction(tr("export_env"), self)
        exp.triggered.connect(self.config_view.export_env)
        file_menu.addAction(exp)
        file_menu.addSeparator()
        open_cfg = QAction(tr("open_config_folder"), self)
        open_cfg.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir()))))
        file_menu.addAction(open_cfg)
        file_menu.addSeparator()
        quit_act = QAction(tr("quit"), self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        lang_menu = self.menuBar().addMenu(tr("menu_language"))
        for code, label_key in (("en", "lang_en"), ("ru", "lang_ru")):
            act = QAction(tr(label_key), self)
            act.setCheckable(True)
            act.setChecked(self.i18n.lang == code)
            act.triggered.connect(lambda _=False, c=code: self._switch_language(c))
            lang_menu.addAction(act)

        theme_menu = self.menuBar().addMenu(tr("menu_theme"))
        for pref, label_key in (("system", "theme_system"), ("light", "theme_light"),
                                ("dark", "theme_dark"), ("black", "theme_black")):
            act = QAction(tr(label_key), self)
            act.setCheckable(True)
            act.setChecked(self.cfg.theme == pref)
            act.triggered.connect(lambda _=False, p=pref: self._switch_theme(p))
            theme_menu.addAction(act)

    # ------------------------------------------------------------- sidebar
    def _refresh_sidebar(self) -> None:
        self.side.set_channels(self.store.list())
        self.config_view.refresh_lean_list()
        self.config_view.refresh_folders_list()   # folder/tag cards live on…
        self.config_view.refresh_tags_list()      # …the Folders & Tags view now
        self.folder_stat.refresh()
        self.content_quality.refresh()
        self.mutual_pr.refresh()

    def _on_folders_changed(self) -> None:
        # Folder OR tag list/assignments changed, from the Config screen,
        # the dashboard's folder/tag button, or the sidebar's context menu
        # — keep every view in sync regardless of which one triggered it
        # (shared handler since a folder and a tag change both need the
        # exact same views refreshed).
        self.side.refresh_badges()
        self.config_view.refresh_folders_list()
        self.config_view.refresh_tags_list()
        self.config_view.refresh_lean_list()   # keep the Lean refresh Folder column current
        self.folder_stat.refresh()
        self.content_quality.refresh()
        self.mutual_pr.refresh()
        if self._current_key:
            self.dashboard.refresh_folder_button()
            self.dashboard.refresh_tag_button()

    def _fold_sidebar(self) -> None:
        self._sidebar_folded = True
        self.side.setVisible(False)
        self.unfold_btn.setVisible(True)

    def _unfold_sidebar(self) -> None:
        self._sidebar_folded = False
        self.side.setVisible(True)
        self.unfold_btn.setVisible(False)

    # ------------------------------------------------------------- actions
    def _show_config(self) -> None:
        self._current_key = None
        self.side.select_config()
        self.stack.setCurrentWidget(self.config_view)

    def _show_folder_stat(self) -> None:
        self._current_key = None
        self.side.select_folder_stat()
        self.folder_stat.refresh()
        self.stack.setCurrentWidget(self.folder_stat)

    def _show_content_quality(self) -> None:
        self._current_key = None
        self.side.select_content_quality()
        self.content_quality.refresh()
        self.stack.setCurrentWidget(self.content_quality)

    def _show_mutual_pr(self) -> None:
        self._current_key = None
        self.side.select_mutual_pr()
        self.mutual_pr.refresh()
        self.stack.setCurrentWidget(self.mutual_pr)

    def _show_channel(self, key: str) -> None:
        data = self.store.load(key)
        if not data:
            QMessageBox.warning(self, self.i18n.tr("app_title"),
                                self.i18n.tr("nav_no_channels"))
            self._refresh_sidebar()
            self._show_config()
            return
        self._current_key = key
        self.side.select_channel(key)
        self.dashboard.load(data)
        self.stack.setCurrentWidget(self.dashboard)

    def _show_compare(self, keys: list[str]) -> None:
        # Compare is an overlay on top of whatever channel/config was open —
        # _current_key is left untouched so turning compare mode back off
        # (see _on_compare_mode_off) returns to it.
        datas = [self.store.load(k) for k in keys]
        if not all(datas):
            return
        self.compare.load(datas)
        self.stack.setCurrentWidget(self.compare)

    def _on_compare_mode_off(self) -> None:
        if self._current_key and self.side.has_channel(self._current_key):
            self._show_channel(self._current_key)
        else:
            self._show_config()

    def _show_compare_charts(self, keys: list[str]) -> None:
        # Live-updates as the sidebar selection changes (0-8 keys, unlike
        # Compare's 2-8) — see SidePanel.compare_charts_selected. Also an
        # overlay: _current_key is left alone so turning the mode back off
        # (see _on_compare_charts_mode_off) returns to whatever was open.
        datas = [d for d in (self.store.load(k) for k in keys) if d]
        self.compare_charts.load(datas)
        self.stack.setCurrentWidget(self.compare_charts)

    def _on_compare_charts_mode_off(self) -> None:
        if self._current_key and self.side.has_channel(self._current_key):
            self._show_channel(self._current_key)
        else:
            self._show_config()

    def _show_mentions(self, keys: list[str]) -> None:
        # Live-updates as the sidebar selection changes (0-4 keys) — see
        # SidePanel.compare_mentions_selected. Also an overlay: _current_key
        # is left alone so turning the mode back off (see
        # _on_mentions_mode_off) returns to whatever was open.
        datas = [d for d in (self.store.load(k) for k in keys) if d]
        self.mentions_view.load(datas)
        self.stack.setCurrentWidget(self.mentions_view)

    def _on_mentions_mode_off(self) -> None:
        if self._current_key and self.side.has_channel(self._current_key):
            self._show_channel(self._current_key)
        else:
            self._show_config()

    def _on_channel_fetched(self, payload: dict) -> None:
        # A fresh fetch (no `key` of its own yet) would otherwise land under
        # a key derived from whatever @username was typed (see
        # store.channel_key) — if that's a channel already tracked under a
        # different key (typically: it renamed, and the new handle was
        # typed here instead of using Refresh), reuse its existing key so
        # this updates that same checkpoint instead of creating a
        # disconnected duplicate for what's really one channel. See
        # store.ChannelStore.find_by_channel_id.
        channel_id = (payload.get("info") or {}).get("id")
        existing_key = self.store.find_by_channel_id(channel_id)
        if existing_key:
            payload["key"] = existing_key
        key = self.store.save(payload)
        self._refresh_sidebar()
        self._show_channel(key)

    def _on_refetch(self, key: str) -> None:
        """Dashboard Refresh button — a lean (incremental) refresh of just
        this channel, then re-open its dashboard when it finishes."""
        self._show_config()
        if self.config_view.lean_refresh([key]):
            self._refetch_return_key = key

    def _on_checkpoints_changed(self) -> None:
        self._refresh_sidebar()
        key, self._refetch_return_key = self._refetch_return_key, None
        if key and self.side.has_channel(key):
            self._show_channel(key)

    def _on_remove(self, key: str) -> None:
        self.store.delete(key)
        self._refresh_sidebar()
        nxt = self.side.first_channel_key()
        if nxt:
            self._show_channel(nxt)
        else:
            self._show_config()

    # ------------------------------------------------------------ language
    def _toggle_language(self) -> None:
        self._switch_language("ru" if self.i18n.lang == "en" else "en")

    def _switch_language(self, code: str) -> None:
        if code == self.i18n.lang:
            self._build_menu()
            return
        self.i18n.lang = code
        self.cfg.language = code
        self.cfg.save()
        # Retranslate every screen in place — unlike _switch_theme, this
        # never rebuilds widgets, so a running fetch, unsaved config fields,
        # sidebar fold/compare state and the currently-open screen all
        # survive a language switch untouched.
        self.setWindowTitle(self.i18n.tr("app_title"))
        self._build_menu()
        self.side.retranslate()
        self.dashboard.retranslate()
        self.config_view.retranslate()
        self.compare.retranslate()
        self.folder_stat.retranslate()
        self.compare_charts.retranslate()
        self.content_quality.retranslate()
        self.mutual_pr.retranslate()
        self.mentions_view.retranslate()
        self.unfold_btn.setToolTip(self.i18n.tr("nav_unfold_hint"))

    # --------------------------------------------------------------- theme
    def _apply_appearance(self) -> None:
        """Push the saved theme + accent colour + zoom onto the app."""
        apply_theme(QApplication.instance(), self.cfg.theme, self.cfg.accent, self.cfg.zoom)

    def _change_appearance(self, **changes: str) -> None:
        """Common path for the theme / accent / zoom pickers: refuse while a
        job is running (the rebuild below would tear its widgets down),
        otherwise save, restyle and rebuild every screen in place."""
        if all(getattr(self.cfg, k) == v for k, v in changes.items()):
            self._build_menu()
            return
        if self.config_view.is_running():
            QMessageBox.warning(self, self.i18n.tr("app_title"),
                                self.i18n.tr("worker_running"))
            self._build_menu()
            # revert the pickers
            self.config_view.sync_theme_combo()
            self.config_view.sync_zoom_combo()
            self.config_view.sync_accent_swatches()
            return
        for k, v in changes.items():
            setattr(self.cfg, k, v)
        self.cfg.save()
        self._apply_appearance()
        self._build_ui()  # rebuild everything with the new palette / sizes

    def _switch_theme(self, pref: str) -> None:
        self._change_appearance(theme=pref)

    def _switch_zoom(self, zoom: str) -> None:
        self._change_appearance(zoom=zoom)

    def _switch_accent(self, accent: str) -> None:
        self._change_appearance(accent=accent)

    def _on_system_theme_changed(self, *_args) -> None:
        """OS appearance flipped while pref == 'system' — follow it live."""
        if self.cfg.theme != "system" or self.config_view.is_running():
            return
        self._apply_appearance()
        self._build_ui()

    # --------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self.config_view.is_running():
            if QMessageBox.question(self, self.i18n.tr("app_title"),
                                    self.i18n.tr("worker_running")) \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self.config_view.worker:
                self.config_view.worker.request_cancel()
        event.accept()
