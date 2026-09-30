"""Fetch and cache the first-photo/video thumbnail for a set of posts —
used by the High-Quality Posts view so its cards can show a preview image
without every channel needing to carry full media in its checkpoint
(nothing else in this app downloads media; checkpoints only ever store
text/counts).

Deliberately separate from `channel_stat.run_channel_stat`: it's triggered
on demand by a button in the view itself (only for the posts currently on
screen), not as part of every regular channel fetch — downloading images
for every post of every channel on every fetch would be slow and bloat
storage for a feature most people won't use every time.
"""
from __future__ import annotations

from .common import check_flood, resolve_entity, retry
from ..media_cache import thumbnail_path

_BATCH = 100  # client.get_messages(ids=...) batch limit


async def run_thumbnail_cache(client, p: dict, ctx) -> str:
    """p: {"posts": [{"channel": str, "id": int, "ids": [int, ...]}, ...]}.
    `channel` is whatever identifier the post's checkpoint already carries
    (@username, -100… id, or a t.me link — same as build_post_link and
    resolve_entity handle elsewhere). `id` is the post's *canonical* id
    (what the checkpoint and the card both key off), `ids` is every
    message id merged into that post (the same post can be a whole album —
    see channel_stat.py) — a post's `id` alone isn't necessarily the album
    member that actually carries a photo/video, so every candidate is
    tried until one downloads successfully, but the result is always saved
    under the *canonical* id so the view can find it again.

    Downloads the *smallest* thumbnail (not the full-res image/video —
    this is for a small card preview) for the first candidate that has one
    (photo, video, or round/circle video note), skipping posts that
    already have a cached thumbnail. Text-only posts, or posts whose media
    Telegram doesn't expose a thumbnail for, are silently skipped."""
    posts = p.get("posts") or []
    total = len(posts)
    ctx.log(f"Fetching thumbnails for {total} post(s)…")

    by_channel: dict[str, list[dict]] = {}
    for post in posts:
        by_channel.setdefault(str(post["channel"]), []).append(post)

    done = 0
    cached = 0
    for channel, channel_posts in by_channel.items():
        if ctx.cancelled():
            break

        # Drop already-cached posts *before* asking Telegram for anything —
        # no point spending a get_messages call (and rate-limit budget) on
        # media nothing is going to be downloaded for.
        to_fetch = [post for post in channel_posts
                   if not thumbnail_path(channel, int(post.get("id", 0))).exists()]
        skipped = len(channel_posts) - len(to_fetch)
        done += skipped
        if skipped:
            ctx.progress(done, total)
        if not to_fetch:
            continue

        try:
            entity = await resolve_entity(client, channel)
        except Exception as exc:
            check_flood(ctx, exc)
            ctx.log(f"  {channel}: {exc}")
            done += len(to_fetch)
            ctx.progress(done, total)
            continue

        # Fetch every candidate message across every post for this channel
        # in one batched pass — cheaper than one request per post.
        all_ids: list[int] = []
        for post in to_fetch:
            all_ids.extend(int(i) for i in (post.get("ids") or [post.get("id", 0)]))
        by_id: dict[int, object] = {}
        for i in range(0, len(all_ids), _BATCH):
            if ctx.cancelled():
                break
            batch = all_ids[i:i + _BATCH]
            msgs = await retry(ctx, client.get_messages, entity, ids=batch)
            for msg in (msgs or []):
                if msg is not None:
                    by_id[msg.id] = msg

        for post in to_fetch:
            done += 1
            ctx.progress(done, total)
            if ctx.cancelled():
                break
            canonical_id = int(post.get("id", 0))
            dest = thumbnail_path(channel, canonical_id)
            candidate_ids = [int(i) for i in (post.get("ids") or [canonical_id])]
            for mid in candidate_ids:
                msg = by_id.get(mid)
                if msg is None:
                    continue
                has_thumb_source = bool(
                    getattr(msg, "photo", None) or getattr(msg, "video", None)
                    or getattr(msg, "video_note", None))
                if not has_thumb_source:
                    continue
                try:
                    result = await retry(ctx, client.download_media, msg,
                                         file=str(dest), thumb=0)
                except Exception as exc:
                    check_flood(ctx, exc)
                    ctx.log(f"  {channel}#{mid}: {exc}")
                    continue
                if result:
                    cached += 1
                    break  # this post is done — don't try its other ids

    ctx.log(f"Cached {cached} new thumbnail(s) ({total - cached} skipped or "
            f"already cached).")
    return "ok"
