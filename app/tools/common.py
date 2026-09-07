"""Helpers shared by all tools."""
from __future__ import annotations

import asyncio
import os
import re

# https://t.me/username, t.me/c/12345(/msg), telegram.me/…, with or without
# a trailing /<message id> — Telethon's own parser only handles the plain
# @username form, so links copied straight from the Telegram app (which
# often include a message id or the private-channel /c/ prefix) fail there.
_TME_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.(?:me|dog))/(c/)?([^/?#\s]+)",
    re.IGNORECASE)


def normalize_channel_ref(value: str) -> str:
    """Strip a t.me/telegram.me link down to a bare @username or numeric ID
    so it resolves exactly like typing the username/ID directly would.
    Anything that isn't a t.me-style link passes through unchanged."""
    v = str(value).strip()
    m = _TME_RE.match(v)
    if not m:
        return v
    is_private, ident = m.group(1), m.group(2)
    if is_private:
        return ident if ident.startswith("-") else f"-100{ident}"
    return f"@{ident}"


async def retry(ctx, coro, *args, **kwargs):
    """Call coro(*args, **kwargs) with FloodWait / transient-error retries."""
    from telethon import errors

    for attempt in range(10):
        if ctx.cancelled():
            return None
        try:
            return await coro(*args, **kwargs)
        except errors.FloodWaitError as e:
            ctx.log(f"  FloodWait: sleeping {e.seconds}s…")
            await _sleep_cancellable(ctx, e.seconds)
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            wait = 5 * (attempt + 1)
            ctx.log(f"  Transient error: {e}. Retrying in {wait}s "
                    f"({attempt + 1}/10)…")
            await _sleep_cancellable(ctx, wait)
    ctx.log("  Giving up after 10 attempts.")
    return None


async def _sleep_cancellable(ctx, seconds: float) -> None:
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        if ctx.cancelled():
            return
        await asyncio.sleep(min(0.5, seconds))


async def _resolve_numeric(client, num: int):
    """The robust numeric-id resolution path -- shared by resolve_entity's
    own numeric-input branch and its username-failure fallback:
    get_entity(num), then via PeerChannel (handles the bare-id-vs--100<id>
    mismatch), then a fresh iter_dialogs scan (covers an id typed/stored
    with the wrong sign/prefix, or one Telethon hasn't cached an
    access_hash for yet, by forcing a resync from the account's own chat
    list). None if none of those find it -- never raises."""
    from telethon.tl.types import PeerChannel

    try:
        return await client.get_entity(num)
    except Exception:
        pass
    channel_id = int(str(num)[4:]) if str(num).startswith("-100") else num
    try:
        return await client.get_entity(PeerChannel(channel_id))
    except Exception:
        pass
    async for dialog in client.iter_dialogs():
        if dialog.id == num or getattr(dialog.entity, "id", None) == channel_id:
            return dialog.entity
    return None


async def resolve_entity(client, value, fallback_id: int | None = None):
    """Accepts @username, t.me link, or numeric ID (incl. -100… form).

    `fallback_id` — a channel's own internal numeric Telegram id, from a
    previously stored checkpoint (see channel_stat._channel_info's own
    "id") — is tried (via _resolve_numeric) if `value` fails to resolve as
    a username/link, e.g. Telethon's UsernameNotOccupiedError when a
    channel's public @username has since been changed or dropped. That
    internal id never changes even when the username does, so this is what
    lets an already-tracked channel's refresh survive a rename instead of
    failing outright — every caller that already has the old checkpoint in
    hand (comments_refresh, mentions_refresh, lean_refresh) passes its own
    stored id through for exactly this. A brand-new fetch by a typed
    username has no prior checkpoint to draw one from, so it has nothing
    to fall back to either — that failure is still a real one (the channel
    genuinely can't be found under that name).

    Raises ValueError with an actionable message if the chat still can't be
    found — either because the ID is wrong, or because Telethon has never
    seen that peer before (it can't resolve a bare numeric ID unless it's
    cached the access_hash from a prior dialog/message) and this account
    isn't a member.
    """
    v = normalize_channel_ref(value)
    if not v:
        raise ValueError("Empty channel/chat identifier")
    try:
        num = int(v)
    except ValueError:
        try:
            return await client.get_entity(v)
        except ValueError as e:
            if fallback_id:
                entity = await _resolve_numeric(client, int(fallback_id))
                if entity is not None:
                    return entity
            hint = (f" Its username may have changed — its last known id "
                    f"({fallback_id}) didn't resolve either." if fallback_id else "")
            raise ValueError(
                f"Could not find chat '{v}'. Check the @username/link is "
                f"correct and that this account can see that chat.{hint}"
            ) from e

    entity = await _resolve_numeric(client, num)
    if entity is not None:
        return entity
    raise ValueError(
        f"Chat ID '{value}' not found or not accessible with this account. "
        f"Double-check the ID (e.g. via @userinfobot or web.telegram.org) and "
        f"make sure this account is a member of that chat."
    )


def read_progress(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def save_progress(path: str, value: int) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value))


def reset_progress(path: str) -> bool:
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
