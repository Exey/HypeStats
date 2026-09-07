"""Refresh just the `links` field on already-stored checkpoints — used by
Config's "Refresh mentions" folder action.

Same targeted-refetch shape as comments_refresh.run_comments_refresh: reuses
each channel's already-stored checkpoint `rows` (its top-N pool — see
channel_stat.py's module docstring) and re-reads each post's message
entities via a batched `get_messages(ids=...)`, rather than a full re-scan.
Only rows that already have text (`full_text` or `text`) are worth
re-checking — a post with no caption at all can't carry a link either, and
this is exactly the field `links` (see channel_stat._extract_links) was
missing from before that helper existed. Saves each channel's checkpoint
back to disk as soon as that channel's batch finishes, same reason as
comments_refresh: a folder with many channels doesn't lose all its progress
if cancelled or interrupted partway through.
"""
from __future__ import annotations

from .channel_stat import _extract_links
from .common import resolve_entity, retry
from ..store import ChannelStore

_BATCH = 100  # client.get_messages(ids=...) batch limit


async def run_mentions_refresh(client, p: dict, ctx) -> str:
    """p: {"keys": [channel_store key, ...]}."""
    keys = p.get("keys") or []
    store = ChannelStore()
    total = len(keys)
    ctx.log(f"Refreshing mentions for {total} channel(s)…")

    updated_channels = 0
    for done, key in enumerate(keys, 1):
        if ctx.cancelled():
            break
        data = store.load(key)
        rows = (data or {}).get("rows") or []
        text_rows = [r for r in rows if (r.get("full_text") or r.get("text"))]
        if not data or not text_rows:
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
        for row in text_rows:
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
        for row in text_rows:
            candidate_ids = [int(i) for i in (row.get("ids") or [row.get("id", 0)])]
            found = [by_id[mid] for mid in candidate_ids if mid in by_id]
            if not found:
                continue  # not resolved this round (deleted/FloodWait) — leave as-is
            links: dict[str, dict] = {}
            for msg in found:
                for link in _extract_links(msg):
                    links.setdefault(link["url"], link)
            new_links = list(links.values())
            if new_links != (row.get("links") or []):
                row["links"] = new_links
                changed = True

        if changed:
            store.save(data)
            updated_channels += 1
        ctx.log(f"  {data.get('title') or key}: done.")
        ctx.progress(done, total)

    ctx.log(f"Updated links on {updated_channels}/{total} channel(s).")
    return "ok"
