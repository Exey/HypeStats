"""App configuration: JSON file with named profiles + .env import/export.

Adapted from tg-super-admin. This app only needs the Telegram connection
fields plus a remembered last channel; per-channel results live in the
checkpoint store (see store.py), not here.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "TgChannelStat"

# Every field a profile stores (also drives .env import/export).
FIELDS = [
    "API_ID",
    "API_HASH",
    "PHONE_NUMBER",
    "CHANNEL_ID",   # last channel typed into the fetch box (convenience only)
]

# Only these are shown on the Config view's connection form.
CONN_FIELDS = ["API_ID", "API_HASH", "PHONE_NUMBER"]

EMPTY_PROFILE = {f: "" for f in FIELDS}


def config_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else home) / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else home / ".config") / APP_NAME


class Config:
    def __init__(self) -> None:
        self.path = config_dir() / "config.json"
        self.language: str = "en"
        self.theme: str = "system"   # "system" | "light" | "dark" | "black"
        self.accent: str = ""        # "" = the theme's own accent, else "#RRGGBB"
        self.zoom: str = "standard"  # "small" | "standard" | "large"
        self.current_profile: str = "default"
        self.profiles: dict[str, dict] = {"default": dict(EMPTY_PROFILE)}
        self.load()

    # ------------------------------------------------------------- storage
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.language = data.get("language", "en")
            self.theme = data.get("theme", "system")
            self.accent = data.get("accent", "") or ""
            self.zoom = data.get("zoom", "standard") or "standard"
            self.current_profile = data.get("current_profile", "default")
            profiles = data.get("profiles") or {}
            self.profiles = {
                name: {**EMPTY_PROFILE, **(vals or {})}
                for name, vals in profiles.items()
            } or {"default": dict(EMPTY_PROFILE)}
            if self.current_profile not in self.profiles:
                self.current_profile = next(iter(self.profiles))
        except (OSError, json.JSONDecodeError, ValueError):
            pass  # first run / corrupted file -> keep defaults

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "language": self.language,
            "theme": self.theme,
            "accent": self.accent,
            "zoom": self.zoom,
            "current_profile": self.current_profile,
            "profiles": self.profiles,
        }
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ------------------------------------------------------------ profiles
    @property
    def profile(self) -> dict:
        return self.profiles.setdefault(self.current_profile, dict(EMPTY_PROFILE))

    def get(self, key: str) -> str:
        return str(self.profile.get(key, "") or "")

    def add_profile(self, name: str) -> bool:
        name = name.strip()
        if not name or name in self.profiles:
            return False
        self.profiles[name] = dict(EMPTY_PROFILE)
        self.current_profile = name
        return True

    def delete_profile(self, name: str) -> bool:
        if len(self.profiles) <= 1 or name not in self.profiles:
            return False
        del self.profiles[name]
        if self.current_profile == name:
            self.current_profile = next(iter(self.profiles))
        return True

    # ------------------------------------------------------------ paths
    def session_path(self) -> str:
        d = config_dir() / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in self.current_profile)
        return str(d / f"{safe}")

    # ------------------------------------------------------------- .env io
    def import_env(self, path: str) -> int:
        """Read KEY=VALUE lines into the current profile. Returns #imported.

        Also accepts tg-scraper's TG_API_ID / TG_API_HASH / TG_PHONE naming.
        """
        aliases = {"TG_API_ID": "API_ID", "TG_API_HASH": "API_HASH",
                   "TG_PHONE": "PHONE_NUMBER", "TG_PHONE_NUMBER": "PHONE_NUMBER"}
        count = 0
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = aliases.get(key.strip(), key.strip())
            if key in FIELDS:
                self.profile[key] = value.strip().strip('"').strip("'")
                count += 1
        return count

    def export_env(self, path: str) -> None:
        lines = [f"{k}={self.get(k)}" for k in FIELDS]
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
