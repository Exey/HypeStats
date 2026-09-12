"""Checkpoint store for fetched channels.

Follows tg-scraper's checkpoint idea — each analyzed channel is persisted as
its own JSON file so a crash (or just closing the app) never loses a fetch —
but keyed per channel rather than a single rolling file, so the sidebar can
list them and re-open any one instantly without re-scanning Telegram.

Layout:  <config_dir>/checkpoints/<key>.json
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from .config import config_dir

SCHEMA = 1


def checkpoints_dir() -> Path:
    d = config_dir() / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def channel_key(channel: str) -> str:
    """Stable filename-safe key for a channel identifier.

    @Name and name resolve to the same key; a -100… ID keeps its digits so a
    private channel typed either way lands on one checkpoint.
    """
    v = str(channel).strip().lstrip("@").lower()
    m = re.search(r"t\.me/(?:c/)?([^/?#\s]+)", v)
    if m:
        v = m.group(1)
    if v.startswith("-100"):
        v = v[4:]
    v = v.lstrip("-")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in v)
    return safe or "channel"


class ChannelStore:
    """Thin filesystem-backed collection of channel checkpoints."""

    def __init__(self) -> None:
        self.dir = checkpoints_dir()

    # --------------------------------------------------------------- write
    def save(self, data: dict) -> str:
        """Persist one channel checkpoint. Returns its key.

        `data` is the payload produced by tools.channel_stat.run_channel_stat
        (already includes schema/key/fetched_at when it comes from a fetch),
        but we defensively fill those in here too.
        """
        key = data.get("key") or channel_key(data.get("channel", "channel"))
        data["key"] = key
        data.setdefault("schema", SCHEMA)
        data.setdefault("fetched_at", time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                     time.gmtime()))
        path = self.dir / f"{key}.json"
        # Atomic write: never leave a half-written checkpoint behind.
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return key

    # ---------------------------------------------------------------- read
    def load(self, key: str) -> dict | None:
        path = self.dir / f"{key}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def list(self) -> list[dict]:
        """Lightweight summaries for the sidebar, newest fetch first."""
        out: list[dict] = []
        for path in self.dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            out.append({
                "key": data.get("key", path.stem),
                "title": data.get("title") or data.get("channel") or path.stem,
                "channel": data.get("channel", ""),
                "username": data.get("username", ""),
                "fetched_at": data.get("fetched_at", ""),
                "members": data.get("info", {}).get("members", 0) or 0,
                # The channel's own numeric Telegram id -- already parsed
                # into `data` above, so this costs nothing extra. Lets a
                # forwarded post's bare fwd_from channel_id (see
                # app.tools.channel_stat._repost_source) be matched back to
                # one of this app's own tracked channels, e.g. in
                # app.ui.compare.mentions_view.
                "channel_id": data.get("info", {}).get("id"),
                # Cached Mentions-view "Fairness"/Ethics score, out of the
                # fuller cache (see app.mentions.compute_channel_mentions_cache/
                # cache_channel_mentions) -- None until it's actually been
                # calculated once, either from the Mentions view itself or
                # the Folders card's "Calculate Ethics" export option
                # (app.tools.mentions_export.run_fairness_calculate).
                # Already parsed into `data` above, so surfacing it here
                # costs nothing extra.
                "fairness_pct": (data.get("mentions_cache") or {}).get("fairness_pct"),
                # Whether the Ethics/Mentions cache has actually been
                # computed for this channel (see
                # app.mentions.mentions_cache_calculated) -- distinct from
                # `fairness_pct`, which stays None for a channel that was
                # calculated but has no fair/fake links. Lets the Folders
                # MD export show "calculated, no score" as "—" and "never
                # calculated" as blank.
                "mentions_calculated": bool(
                    (data.get("mentions_cache") or {}).get("calculated_at")),
            })
        out.sort(key=lambda d: d.get("fetched_at", ""), reverse=True)
        return out

    def delete(self, key: str) -> bool:
        path = self.dir / f"{key}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def find_by_channel_id(self, channel_id: int | None) -> str | None:
        """The key of whichever tracked checkpoint already carries this
        channel's own internal Telegram id (`info.id` — stable across a
        @username rename or drop, unlike the id-derived key a *new* fetch
        would otherwise get via channel_key(), which is username-shaped).
        None if `channel_id` is falsy or nothing matches.

        Lets a fresh "fetch by @username" typed after a channel renamed
        (rather than using Refresh, which already survives a rename via
        resolve_entity's fallback_id) recognize it's the same channel it
        already tracks under the old handle's key, and update that
        checkpoint in place instead of the fetch silently creating a
        second, disconnected entry for what's really one channel — see
        app.ui.main_window._on_channel_fetched."""
        if not channel_id:
            return None
        for summary in self.list():
            if summary.get("channel_id") == channel_id:
                return summary["key"]
        return None
