"""Build a standalone desktop binary for the current OS with PyInstaller.

PyInstaller does not cross-compile — run this on each target OS separately
to produce that OS's binary:

    macOS / Linux:  ./build.sh     (or: python3 build.py)
    Windows:        build.bat      (or: python build.py)

Output (name: hypestats_<version, dots as underscores>):
  - macOS:    dist/hypestats_26_7_30.app
              (a proper .app bundle — PyInstaller deprecates --onefile +
              --windowed together on macOS, since a .app can't be a single
              file anyway, so this uses --onedir there instead)
  - Windows:  dist/hypestats_26_7_30.exe — single file (--onefile)
  - Linux:    dist/hypestats_26_7_30 — single file (--onefile)
"""
from __future__ import annotations

import os
import pathlib
import sys

import PyInstaller.__main__

from app.version import __version__

ROOT = pathlib.Path(__file__).resolve().parent
ASSETS = ROOT / "assets"

_ICON_BY_PLATFORM = {"darwin": ASSETS / "icon.icns", "win32": ASSETS / "icon.ico"}


def main() -> None:
    dist_name = f"hypestats_{__version__.replace('.', '_')}"

    args = [
        str(ROOT / "main.py"),
        "--name", dist_name,
        "--windowed",
        "--noconfirm",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{ASSETS}{os.pathsep}assets",
        # Telethon and qrcode both do dynamic/lazy imports PyInstaller's
        # static analysis can miss (tl schema modules, PIL plugins) —
        # collect them wholesale rather than chasing ModuleNotFoundError
        # reports from a packaged build one at a time.
        "--collect-all", "telethon",
        "--collect-all", "qrcode",
    ]
    # --onefile + --windowed on macOS is deprecated (a .app can't be a
    # single file) and PyInstaller warns it'll become a hard error — the
    # .app bundle from --onedir already *is* the single thing users drag to
    # Applications, so there's nothing onefile would add there anyway.
    if sys.platform != "darwin":
        args.append("--onefile")

    icon = _ICON_BY_PLATFORM.get(sys.platform)
    if icon and icon.exists():
        args += ["--icon", str(icon)]

    print(f"Building {dist_name} for {sys.platform} with PyInstaller…")
    PyInstaller.__main__.run(args)
    suffix = ".app" if sys.platform == "darwin" else (".exe" if sys.platform == "win32" else "")
    print(f"Done -> dist/{dist_name}{suffix}")


if __name__ == "__main__":
    main()
