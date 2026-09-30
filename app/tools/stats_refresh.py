"""Re-read the live engagement numbers (views, reposts/forwards, reactions,
comments) of specific, already-stored posts — driven by the High-Quality
Posts view's Refetch button.

A checkpoint's `rows` pool holds each post's counters as of whenever the
channel was last fetched; a post's views keep climbing for days, so the
ranking in High-Quality Posts can be working from stale numbers. This asks
Telegram for just those posts by message id (`get_messages(ids=…)`, 100 per
request — far cheaper than re-walking a channel's history) and overwrites
the four counters on the stored rows. An album's counters are the max across
its messages, exactly as app.tools.lean_refresh._scan_since merges them.

Only the rows change. The monthly series, channel stats and `fetched_at` are
left alone (except `max_views` / `max_reposts`, which only ever go up): the
monthly totals cover every post, not just this pool, so they can't be patched
from a handful of rows — a lean refresh is what rebuilds them — and bumping
`fetched_at` here would make the next lean refresh skip months it still owes.
Posts Telegram no longer returns (deleted) keep their stored numbers.

Each row a refetch reads gets `stats_at` (UTC ISO, also stamped by full and
lean scans). The view skips posts whose `stats_at` is under FRESH_HOURS old —
see is_fresh — and spends the freed quota further down the ranking instead.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..store import ChannelStore
from .channel_stat import _comment_total, _reaction_total, utc_stamp
from .common import check_flood, resolve_entity, retry

_BATCH = 100   # client.get_messages(ids=...) batch limit
FRESH_HOURS = 24   # a post read more recently than this isn't read again


def is_fresh(row: dict, now: datetime | None = None) -> bool:
    """True if `row`'s counters were read from Telegram within FRESH_HOURS.
    A row with no (or unparseable) `stats_at` is stale."""
    try:
        at = datetime.fromisoformat(str(row.get("stats_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - at < timedelta(hours=FRESH_HOURS)


def _counters(msgs: list) -> dict[str, int]:
    return {
        "views": max(int(getattr(m, "views", 0) or 0) for m in msgs),
        "reactions": max(_reaction_total(m) for m in msgs),
        "forwards": max(int(getattr(m, "forwards", 0) or 0) for m in msgs),
        "comments": max(_comment_total(m) for m in msgs),
    }


async def _refresh_channel(client, data: dict, posts: list[dict], ctx,
                           on_batch) -> tuple[int, int, int]:
    """Patch `data["rows"]` in place for `posts` ([{"id", "ids"}]). Returns
    (updated, unchanged, missing) post counts; `on_batch(n)` is told how many
    posts' worth of ids each request covered, for the progress bar."""
    ref = data.get("channel") or data.get("username") or data.get("key")
    fallback_id = (data.get("info") or {}).get("id") or None
    entity = await resolve_entity(client, ref, fallback_id)

    wanted = sorted({mid for p in posts for mid in (p.get("ids") or [p["id"]])})
    live: dict[int, object] = {}
    for i in range(0, len(wanted), _BATCH):
        if ctx.cancelled():
            break
        batch = wanted[i:i + _BATCH]
        got = await retry(ctx, client.get_messages, entity, ids=batch)
        for mid, msg in zip(batch, got or []):
            if msg is not None and not getattr(msg, "action", None):
                live[mid] = msg
        on_batch(len(batch) / max(1, len(wanted)) * len(posts))

    by_id = {r["id"]: r for r in data.get("rows") or []}
    stats = data.setdefault("stats", {})
    updated = unchanged = missing = 0
    for p in posts:
        row = by_id.get(p["id"])
        msgs = [live[mid] for mid in (p.get("ids") or [p["id"]]) if mid in live]
        if row is None or not msgs:
            missing += 1
            continue
        fresh = _counters(msgs)
        row["stats_at"] = utc_stamp()
        if all(int(row.get(k, 0) or 0) == v for k, v in fresh.items()):
            unchanged += 1
            continue
        row.update(fresh)
        updated += 1
        stats["max_views"] = max(int(stats.get("max_views", 0) or 0), fresh["views"])
        stats["max_reposts"] = max(int(stats.get("max_reposts", 0) or 0), fresh["forwards"])
    return updated, unchanged, missing


async def run_stats_refresh(client, p: dict, ctx) -> str:
    """p: {"channels": [{"key": checkpoint key, "posts": [{"id", "ids"}, …]}]}."""
    channels = p.get("channels") or []
    total = sum(len(c.get("posts") or []) for c in channels)
    ctx.log(f"Refetching stats of {total} post(s) in {len(channels)} channel(s)…"
            + (f" ({p['skipped_fresh']} read within {FRESH_HOURS} h skipped)"
               if p.get("skipped_fresh") else ""))

    store = ChannelStore()
    done = 0.0
    tot_updated = 0
    for n, entry in enumerate(channels, 1):
        if ctx.cancelled():
            break
        key, posts = entry.get("key"), entry.get("posts") or []
        data = store.load(key) if key and key not in ctx.done else None   # done: previous account
        if not data or not posts:
            done += len(posts)
            ctx.progress(int(done), total)
            continue
        title = data.get("title") or key
        ctx.log(f"[{n}/{len(channels)}] {title}: {len(posts)} post(s)…")
        base = done

        def _on_batch(k: float, base: float = base) -> None:
            nonlocal done
            done += k
            ctx.progress(int(done), total)

        try:
            updated, unchanged, missing = await _refresh_channel(
                client, data, posts, ctx, _on_batch)
        except Exception as exc:  # noqa: BLE001 - surfaced to the GUI log
            check_flood(ctx, exc)
            ctx.log(f"  {title}: {exc}")
            done = base + len(posts)
            ctx.progress(int(done), total)
            continue
        if ctx.cancelled():
            break
        data.setdefault("key", key)
        store.save(data)   # each channel is saved as it finishes
        ctx.item_done(key)
        tot_updated += updated
        ctx.log(f"  {title}: {updated} updated, {unchanged} unchanged"
                + (f", {missing} no longer on Telegram" if missing else "") + ".")
        done = base + len(posts)
        ctx.progress(int(done), total)

    ctx.log(f"Refetch done: {tot_updated} post(s) updated.")
    return "ok"
