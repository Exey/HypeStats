"""Refresh just the `comments` count on already-stored checkpoints — used by
Config's "Refresh comments" folder action.

A full re-fetch (`channel_stat.run_channel_stat`) re-walks a channel's whole
history, member count, activity distributions, everything — overkill (and
slow) when all that's actually stale is one field added after a channel was
last fetched (see channel_stat.py's `_comment_total`). This instead reuses
each channel's already-stored checkpoint `rows` (its top-N pool — see
channel_stat.py's module docstring) and just re-reads the comment count for
those exact post ids via a batched `get_messages(ids=...)`, same technique
as media_fetch.run_thumbnail_cache. Unlike a tool that returns one big JSON
payload for the UI to save, this saves each channel's checkpoint back to
disk itself as soon as that channel's batch finishes, so a folder with many
channels doesn't lose all its progress if cancelled or interrupted partway
through.
"""
from __future__ import annotations

from .common import resolve_entity, retry
from ..store import ChannelStore

_BATCH = 100  # client.get_messages(ids=...) batch limit


def _comment_total(msg) -> int:
    replies = getattr(msg, "replies", None)
    return int(getattr(replies, "replies", 0) or 0)


async def run_comments_refresh(client, p: dict, ctx) -> str:
    """p: {"keys": [channel_store key, ...]}."""
    keys = p.get("keys") or []
    store = ChannelStore()
    total = len(keys)
    ctx.log(f"Refreshing comments for {total} channel(s)…")

    updated_channels = 0
    for done, key in enumerate(keys, 1):
        if ctx.cancelled():
            break
        data = store.load(key)
        rows = (data or {}).get("rows") or []
        if not data or not rows:
            ctx.progress(done, total)
            continue

        channel_ref = data.get("channel") or data.get("username") or key
        fallback_id = (data.get("info") or {}).get("id") or None
        try:
            entity = await resolve_entity(client, channel_ref, fallback_id)
        except Exception as exc:
            ctx.log(f"  {key}: {exc}")
            ctx.progress(done, total)
            continue

        all_ids: list[int] = []
        for row in rows:
            all_ids.extend(int(i) for i in (row.get("ids") or [row.get("id", 0)]))
        by_id: dict[int, object] = {}
        for i in range(0, len(all_ids), _BATCH):
            if ctx.cancelled():
                break
            batch = all_ids[i:i + _BATCH]
            msgs = await retry(ctx, client.get_messages, entity, ids=batch)
            for msg in (msgs or []):
                if msg is not None:
                    by_id[msg.id] = msg

        changed = False
        for row in rows:
            candidate_ids = [int(i) for i in (row.get("ids") or [row.get("id", 0)])]
            found = [by_id[mid] for mid in candidate_ids if mid in by_id]
            if not found:
                continue  # not resolved this round (deleted/FloodWait) — leave as-is
            best = max(_comment_total(m) for m in found)
            if best != int(row.get("comments", 0) or 0):
                row["comments"] = best
                changed = True

        if changed:
            store.save(data)
            updated_channels += 1
        ctx.log(f"  {data.get('title') or key}: done.")
        ctx.progress(done, total)

    ctx.log(f"Updated comments on {updated_channels}/{total} channel(s).")
    return "ok"
