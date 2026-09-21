"""HypeStats — entry point.

Configures logging (console + rotating file in the OS-appropriate location),
applies the theme stylesheet, and shows the main window.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from app.errors import friendly_os_error
from app.version import __version__

# Folder name for the log/config/data dirs -- still the project's original
# name on purpose: renaming it would orphan every existing install's
# checkpoints, session and settings (see app.config.APP_NAME).
APP_NAME = "TgChannelStat"


def _log_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home) / APP_NAME / "logs"
    xdg = os.environ.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else home / ".local" / "state") / APP_NAME.lower()


def _configure_logging() -> None:
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "tgchannelstat.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file.setFormatter(fmt)
    root.addHandler(file)

    logging.getLogger("telethon").setLevel(logging.WARNING)


def _install_excepthook() -> None:
    def handle(exc_type: type[BaseException], exc: BaseException, tb) -> None:
        logging.getLogger(__name__).critical(
            "Unhandled exception", exc_info=(exc_type, exc, tb))
        message = friendly_os_error(exc) if isinstance(exc, OSError) else str(exc)

        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(None, APP_NAME, message or exc_type.__name__)

    sys.excepthook = handle


def main() -> int:
    _configure_logging()
    _install_excepthook()
    logging.getLogger(__name__).info(
        "%s %s starting (platform=%s, python=%s)",
        APP_NAME, __version__, sys.platform, sys.version.split()[0])

    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("HypeStats")
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
