"""Which Telegram account fetches each channel: the profile's first
(default) one, or its second (Config -> Telegram -> PHONE_NUMBER_2) — for
channels only the second account is a member of. Persisted separately from
checkpoints, same shape as app.folders.FolderStore's channel assignments:
it is presentation/routing metadata, not fetched channel data.

Only the *exception* is stored (channel key -> 2), so every channel not in
here is on the first account and an unset second phone changes nothing.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import config_dir

FIRST_ACCOUNT = 1
SECOND_ACCOUNT = 2


def accounts_path() -> Path:
    return config_dir() / "accounts.json"


class AccountStore:
    def __init__(self) -> None:
        self.path = accounts_path()
        self.assignments: dict[str, int] = {}   # channel key -> account (only 2s)
        self.load()

    # --------------------------------------------------------------- io
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data.get("assignments") or {}
            self.assignments = {k: SECOND_ACCOUNT for k, v in raw.items()
                                if int(v) == SECOND_ACCOUNT}
        except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
            self.assignments = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"assignments": self.assignments}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ------------------------------------------------------- assignment
    def account_for_channel(self, key: str) -> int:
        return self.assignments.get(key, FIRST_ACCOUNT)

    def set_channel_account(self, key: str, account: int) -> None:
        if account == SECOND_ACCOUNT:
            self.assignments[key] = SECOND_ACCOUNT
        else:
            self.assignments.pop(key, None)
        self.save()

    def set_channels_account(self, keys: list[str], account: int) -> None:
        """set_channel_account for many channels, saved once."""
        for key in keys:
            if account == SECOND_ACCOUNT:
                self.assignments[key] = SECOND_ACCOUNT
            else:
                self.assignments.pop(key, None)
        self.save()

    def prune(self, live_keys: set[str]) -> None:
        """Drop assignments of channels that no longer exist (removed from
        the sidebar), so accounts.json doesn't accumulate dead keys."""
        stale = [k for k in self.assignments if k not in live_keys]
        if stale:
            for k in stale:
                del self.assignments[k]
            self.save()

    def group_by_account(self, keys: list[str]) -> list[tuple[int, list[str]]]:
        """[(account, keys), …] — first account's channels first, each group
        in `keys`' own order, empty groups omitted."""
        by: dict[int, list[str]] = {FIRST_ACCOUNT: [], SECOND_ACCOUNT: []}
        for k in keys:
            by[self.account_for_channel(k)].append(k)
        return [(acc, ks) for acc, ks in by.items() if ks]
