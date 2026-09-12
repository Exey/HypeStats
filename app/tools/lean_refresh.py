"""Lean (incremental) refresh — bring stored checkpoints up to date without a
full multi-year re-scan.

Driven by the Config screen's "Lean refresh" card. Unlike
channel_stat.run_channel_stat (which re-walks the whole stored period), for
each channel this:

* scans messages only from the first day of the month `fetched_at` falls in
  up to now — so a monthly refresh cadence re-scans ~1 month, and a
  long-neglected channel scans just the gap, still far less than the stored
  2-3 year period;
* rebuilds `distributions.monthly` for exactly those fully-covered months
  and splices them onto the untouched older months;
* re-selects the top-N `rows` pool from the old pool ∪ the fresh posts;
* recomputes the count / views / reactions / reposts / viral-share stats
  that `distributions.monthly` fully determines, and leaves the rest
  (avg_views_settled, media share, hour/weekday posting habits) as they
  were — those barely move month to month and a one-month scan has no
  better data for them;
* refreshes member count and title, and bumps `fetched_at`.

Each channel's checkpoint is saved back to disk as soon as it finishes, so a
long run that's cancelled partway keeps the channels it already did.

A checkpoint with no `distributions.monthly` (fetched before that field
existed) can't be merged incrementally, so it falls back to one full
channel_stat scan — after which future lean refreshes of it are cheap. The
dashboard's Refresh button routes here too, so it's lean by default.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..store import ChannelStore
from .channel_stat import (
    VIRAL_BASELINE_MIN_POSTS, VIRAL_BASELINE_MONTHS, VIRAL_MONTHLY_CAP_FRAC,
    VIRAL_MULTIPLE, _channel_info, _comment_total, _extract_links, _has_buttons,
    _is_repost, _media_type, _month_index, _one_per_month_ids, _preview,
    _public_forwards, _reaction_total, _repost_source, _top_ids, run_channel_stat,
)
from .common import resolve_entity

_FALLBACK_GAP_DAYS = 35   # used when a checkpoint has no parseable fetched_at
_HEARTBEAT_EVERY = 500


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _month_label(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _fill_month_gaps(by_label: dict[str, dict]) -> list[dict]:
    """`{label: entry}` -> a gap-free list from the earliest to latest month,
    same shape channel_stat._monthly_series produces."""
    if not by_label:
        return []
    labels = sorted(by_label)
    y, m = (int(x) for x in labels[0].split("-"))
    y1, m1 = (int(x) for x in labels[-1].split("-"))
    out: list[dict] = []
    while (y, m) <= (y1, m1):
        label = f"{y:04d}-{m:02d}"
        out.append(by_label.get(label, {
            "label": label, "count": 0, "views": 0, "shares": 0,
            "reactions": 0, "viral_count": 0,
            "count_own": 0, "views_own": 0, "shares_own": 0,
            "reactions_own": 0, "viral_count_own": 0, "top": None,
        }))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


async def _scan_since(client, entity, cutoff: datetime, ctx) -> list[dict]:
    """Album-merged posts newer than `cutoff`, each the same shape as a
    channel_stat `rows` entry."""
    rows: list[dict] = []
    current: dict | None = None
    current_gid = None
    scanned = 0
    own_id = int(getattr(entity, "id", 0) or 0)
    async for msg in client.iter_messages(entity):
        if ctx.cancelled():
            break
        if msg.date and msg.date < cutoff:
            break  # newest -> oldest, past the window
        scanned += 1
        if getattr(msg, "action", None) is not None:
            continue  # service message
        gid = getattr(msg, "grouped_id", None)
        full_text = " ".join((getattr(msg, "message", "") or "").split())
        text = _preview(full_text)
        views = int(getattr(msg, "views", 0) or 0)
        reactions = _reaction_total(msg)
        forwards = int(getattr(msg, "forwards", 0) or 0)
        comments = _comment_total(msg)
        media_type = _media_type(msg)
        has_buttons = _has_buttons(msg)
        is_repost = _is_repost(msg, own_id)
        repost_from_id, repost_from_author = _repost_source(msg)
        links = _extract_links(msg)

        if gid is not None and gid == current_gid and current is not None:
            current["ids"].append(msg.id)
            current["id"] = msg.id
            current["views"] = max(current["views"], views)
            current["reactions"] = max(current["reactions"], reactions)
            current["forwards"] = max(current["forwards"], forwards)
            current["comments"] = max(current["comments"], comments)
            current["has_buttons"] = current["has_buttons"] or has_buttons
            current["repost"] = current["repost"] or is_repost
            if current["repost_from_id"] is None and repost_from_id is not None:
                current["repost_from_id"] = repost_from_id
            if not current["repost_from_author"] and repost_from_author:
                current["repost_from_author"] = repost_from_author
            if media_type:
                current["media_counts"][media_type] = (
                    current["media_counts"].get(media_type, 0) + 1)
            if not current["text"] and text:
                current["text"] = text
                current["full_text"] = full_text
                current["links"] = links
        else:
            current = {
                "id": msg.id, "ids": [msg.id],
                "ts": int(msg.date.timestamp()) if msg.date else 0,
                "date": msg.date.isoformat() if msg.date else "",
                "text": text, "full_text": full_text, "links": links,
                "views": views, "reactions": reactions, "forwards": forwards,
                "comments": comments, "media_type": media_type,
                "media_counts": {media_type: 1} if media_type else {},
                "has_buttons": has_buttons, "repost": is_repost,
                "repost_from_id": repost_from_id,
                "repost_from_author": repost_from_author,
                "public": None,
            }
            current_gid = gid
            rows.append(current)

        if scanned % _HEARTBEAT_EVERY == 0:
            ctx.log(f"    scanned {scanned}…")
    return rows


def _merge(data: dict, fresh: list[dict], cutoff: datetime) -> set[int]:
    """Splice `fresh` (posts from `cutoff` onward) into the stored checkpoint
    `data`, in place. `cutoff` is a month boundary, so every month it touches
    was scanned in full and can be rebuilt outright. Returns the set of post
    ids that came from `fresh` (for the caller's public-repost top-up)."""
    stats = data.setdefault("stats", {})
    dist = data.setdefault("distributions", {})
    old_monthly = {m.get("label"): m for m in (dist.get("monthly") or []) if m.get("label")}
    cutoff_label = _month_label(cutoff)

    # --- rows pool: old pool ∪ fresh, re-ranked ---
    top_n = int(data.get("top_n") or 20)
    by_id = {r["id"]: r for r in (data.get("rows") or [])}
    fresh_ids = {fr["id"] for fr in fresh}
    for fr in fresh:
        prev = by_id.get(fr["id"])
        if prev and prev.get("public") and fr.get("public") is None:
            fr["public"] = prev["public"]
        by_id[fr["id"]] = fr
    merged_rows = list(by_id.values())
    pool_ids: set[int] = set()
    for key in ("views", "reactions", "forwards", "ts"):
        pool_ids |= _top_ids(merged_rows, key, top_n)
    pool_ids |= _one_per_month_ids(merged_rows)
    pool = [r for r in merged_rows if r["id"] in pool_ids]
    pool.sort(key=lambda r: r["views"], reverse=True)
    data["rows"] = pool
    data["scanned"] = len(pool)

    # --- monthly: rebuild the rescanned months, keep the older ones ---
    kept = {lbl: m for lbl, m in old_monthly.items() if lbl < cutoff_label}
    fresh_by_month: dict[str, list[dict]] = {}
    for fr in fresh:
        label = (fr.get("date") or "")[:7]
        if len(label) == 7:
            fresh_by_month.setdefault(label, []).append(fr)

    total_views = sum(int(m.get("views", 0) or 0) for m in kept.values())
    total_count = sum(int(m.get("count", 0) or 0) for m in kept.values())
    for posts in fresh_by_month.values():
        total_views += sum(p["views"] for p in posts)
        total_count += len(posts)
    avg_views = total_views / total_count if total_count else 0

    # Per-month (views_total, count) across kept + fresh months, for the
    # trailing "viral" baseline — see channel_stat._viral_baseline.
    month_totals: dict[str, tuple[float, int]] = {
        lbl: (float(m.get("views", 0) or 0), int(m.get("count", 0) or 0))
        for lbl, m in kept.items()
    }
    for lbl, posts in fresh_by_month.items():
        month_totals[lbl] = (sum(p["views"] for p in posts), len(posts))

    def _viral_base(label: str) -> float:
        i0 = _month_index(label)
        window = {i0 - k for k in range(1, VIRAL_BASELINE_MONTHS + 1)}
        tv = tc = 0.0
        for lbl, (v, c) in month_totals.items():
            if _month_index(lbl) in window:
                tv += v
                tc += c
        return tv / tc if tc >= VIRAL_BASELINE_MIN_POSTS and tv else avg_views

    rebuilt = dict(kept)
    for label, posts in fresh_by_month.items():
        # ..._own = same sums over the channel's own posts only (reposts
        # forwarded in from other channels excluded) — the Folder Stats
        # Rating reads these. Matches channel_stat._monthly_aggregates.
        own = [p for p in posts if not p.get("repost")]
        base = _viral_base(label)

        def _is_viral(p: dict, base: float = base) -> bool:
            return bool(base and p["views"] > VIRAL_MULTIPLE * base)

        vc = min(sum(1 for p in posts if _is_viral(p)),
                 int(len(posts) * VIRAL_MONTHLY_CAP_FRAC))
        vc_own = min(sum(1 for p in own if _is_viral(p)),
                     int(len(own) * VIRAL_MONTHLY_CAP_FRAC))
        rebuilt[label] = {
            "label": label,
            "count": len(posts),
            "views": sum(p["views"] for p in posts),
            "shares": sum(p["forwards"] for p in posts),
            "reactions": sum(p["reactions"] for p in posts),
            "viral_count": vc,
            "count_own": len(own),
            "views_own": sum(p["views"] for p in own),
            "shares_own": sum(p["forwards"] for p in own),
            "reactions_own": sum(p["reactions"] for p in own),
            "viral_count_own": vc_own,
            "top": max(own, key=lambda p: p["views"], default=None),
        }
    dist["monthly"] = _fill_month_gaps(rebuilt)

    # --- stats that distributions.monthly fully determines ---
    monthly = dist["monthly"]
    tot_count = sum(int(m.get("count", 0) or 0) for m in monthly)
    tot_views = sum(int(m.get("views", 0) or 0) for m in monthly)
    tot_shares = sum(int(m.get("shares", 0) or 0) for m in monthly)
    tot_react = sum(int(m.get("reactions", 0) or 0) for m in monthly)
    tot_viral = sum(int(m.get("viral_count", 0) or 0) for m in monthly)
    if tot_count:
        stats["total_posts"] = tot_count
        stats["avg_views"] = round(tot_views / tot_count, 1)
        stats["avg_reactions"] = round(tot_react / tot_count, 1)
        stats["avg_reposts"] = round(tot_shares / tot_count, 1)
        stats["viral_post_share"] = round(tot_viral / tot_count * 100, 1)
        # Recent average = last VIRAL_BASELINE_MONTHS months with posts (see
        # channel_stat._recent_avg_views) — the quality gauge's viral-excess
        # reference, so a grown channel doesn't score every recent post high.
        active = [m for m in monthly if int(m.get("count", 0) or 0)]
        recent = active[-VIRAL_BASELINE_MONTHS:]
        rc = sum(int(m.get("count", 0) or 0) for m in recent)
        rv = sum(int(m.get("views", 0) or 0) for m in recent)
        stats["avg_views_recent"] = (round(rv / rc, 1)
                                     if rc >= VIRAL_BASELINE_MIN_POSTS and rv
                                     else stats["avg_views"])
    stats["max_views"] = max(int(stats.get("max_views", 0) or 0),
                             max((p["views"] for p in fresh), default=0))
    stats["max_reposts"] = max(int(stats.get("max_reposts", 0) or 0),
                               max((p["forwards"] for p in fresh), default=0))

    last_dt = max((_parse_iso(p["date"]) for p in fresh if p.get("date")),
                  default=None)
    if last_dt is not None:
        stats["last_post_date"] = last_dt.isoformat()
    first_dt = _parse_iso(stats.get("first_post_date"))
    last_known = _parse_iso(stats.get("last_post_date"))
    if first_dt and last_known:
        total_days = max(1, (last_known - first_dt).days + 1)
        stats["total_days"] = total_days
        stats["avg_posts_per_day"] = round(tot_count / total_days, 2)

    lfy = int(stats.get("last_full_year") or (datetime.now(timezone.utc).year - 1))
    stats["last_full_year"] = lfy
    stats["last_year_views"] = sum(int(m.get("views", 0) or 0) for m in monthly
                                   if m.get("label", "").startswith(str(lfy)))
    stats["last_year_reposts"] = sum(int(m.get("shares", 0) or 0) for m in monthly
                                     if m.get("label", "").startswith(str(lfy)))
    return fresh_ids


async def _full_refresh(client, data: dict, ctx, period: str | None = None) -> str:
    """One full channel_stat scan that replaces the checkpoint outright.

    Used two ways: as the automatic fallback for a checkpoint too old to
    merge incrementally (no `distributions.monthly`, `period` left None so
    the stored period is kept), and for an explicit "re-fetch N years"
    request (`period` given, e.g. "2y") — a lean merge only rebuilds the
    recent months, so it can't retro-fit newly added per-post fields
    (reposts, buttons, comments…) onto a channel's older history; a full
    scan can."""
    params = {
        "channel": data.get("channel") or data.get("username") or data.get("key"),
        "period": (data.get("period") or "") if period is None else period,
        "top_n": int(data.get("top_n") or 20),
        "fetch_public": bool(data.get("fetch_public")),
        # This channel's own numeric id from the checkpoint being replaced
        # -- see resolve_entity's fallback_id, for when its username has
        # since changed or been dropped (UsernameNotOccupiedError) but the
        # channel itself is still there under the same internal id.
        "fallback_id": (data.get("info") or {}).get("id") or None,
    }
    payload = json.loads(await run_channel_stat(client, params, ctx))
    if payload.get("cancelled"):
        return "cancelled"
    payload["key"] = data.get("key")
    # run_channel_stat already refreshes `username`/`link` from this fetch's
    # own resolved entity, but leaves `channel` (the resolve hint for next
    # time) as whatever was passed in -- `params["channel"]`, above, which
    # is this checkpoint's own possibly-stale value. Move it to the live
    # handle too, so a renamed channel stops needing resolve_entity's
    # fallback_id path on every future refresh (see the same fix in
    # _refresh_one).
    if payload.get("username"):
        payload["channel"] = f"@{payload['username']}"
    data.clear()
    data.update(payload)
    return f"full re-fetch ({period})" if period else "full re-fetch (no monthly history to merge)"


async def _refresh_one(client, data: dict, ctx) -> str:
    """Mutate `data` in place to the current state. Returns a short status
    string; raises on a hard failure the caller logs and skips."""
    if not (data.get("distributions") or {}).get("monthly"):
        return await _full_refresh(client, data, ctx)

    ref = data.get("channel") or data.get("username") or data.get("key")
    fallback_id = (data.get("info") or {}).get("id") or None
    entity = await resolve_entity(client, ref, fallback_id)

    fetched = _parse_iso(data.get("fetched_at"))
    if fetched is None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FALLBACK_GAP_DAYS)
    else:
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        cutoff = fetched.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    fresh = await _scan_since(client, entity, cutoff, ctx)
    if ctx.cancelled():
        return "cancelled"

    info = await _channel_info(client, entity)
    fresh_ids = _merge(data, fresh, cutoff)

    if info.get("members"):
        data.setdefault("info", {})["members"] = info["members"]
    if info.get("about"):
        data.setdefault("info", {})["about"] = info["about"]
    if info.get("title"):
        data["title"] = info["title"]
    # A channel's public @username can change (or be dropped) without its
    # internal numeric id changing -- resolve_entity's fallback_id is what
    # lets the resolve above survive that, but nothing used to carry the new
    # username back into the checkpoint: `username`/`link` stayed pinned to
    # whatever the channel was called the very first time it was fetched,
    # even after it renamed itself, so every "Open" link and Mentions'
    # own-channel check (app.mentions.tg_identity_key(data["link"])) kept
    # pointing at the old, dead handle. Synced here from this fetch's own
    # live `info`, same as _full_refresh's fresh payload already gets for
    # free.
    new_username = info.get("username") or ""
    if new_username != (data.get("username") or ""):
        data["username"] = new_username
        data.setdefault("info", {})["username"] = new_username
        data["link"] = (f"https://t.me/{new_username}" if new_username
                        else (f"https://t.me/c/{info['id']}" if info.get("id")
                              else data.get("link", "")))
    # `channel` (the ref tried first on the *next* resolve) is checked
    # separately from the block above, not folded into its `if` -- a
    # checkpoint whose `username`/`link` already got fixed once (e.g. by a
    # _full_refresh, which has always kept them current) but predates this
    # fix would otherwise never trip the "username changed" branch again
    # and stay stuck resolving through fallback_id forever. Only ever moves
    # to a new handle, never blanked -- if the channel dropped its username
    # entirely, the stale ref is still a marginally better first guess than
    # nothing, and fallback_id covers either case regardless.
    if new_username and data.get("channel") != f"@{new_username}":
        data["channel"] = f"@{new_username}"

    if data.get("fetch_public") and fresh_ids and not ctx.cancelled():
        try:
            input_channel = await client.get_input_entity(entity)
            for row in data.get("rows", []):
                if ctx.cancelled():
                    break
                if row["id"] in fresh_ids and row.get("public") is None:
                    row["public"] = await _public_forwards(client, input_channel, row["id"])
        except Exception:  # noqa: BLE001 - public stats are best-effort
            pass

    data["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{len(fresh)} new post(s)"


async def run_lean_refresh(client, p: dict, ctx) -> str:
    """p: {"keys": [checkpoint key, ...], "full_period": "2y" | None}.

    With `full_period` set, every key is fully re-scanned over that period
    (see _full_refresh) instead of the usual incremental merge — for
    rebuilding older history against current per-post fields."""
    keys = p.get("keys") or []
    full_period = p.get("full_period") or None
    store = ChannelStore()
    total = len(keys)
    ctx.log(f"{'Full re-fetch' if full_period else 'Lean refresh'}: {total} channel(s)…")

    updated = 0
    for i, key in enumerate(keys, 1):
        if ctx.cancelled():
            break
        data = store.load(key)
        if not data:
            ctx.progress(i, total)
            continue
        title = data.get("title") or key
        ctx.log(f"[{i}/{total}] {title}…")
        try:
            result = (await _full_refresh(client, data, ctx, full_period)
                      if full_period else await _refresh_one(client, data, ctx))
        except Exception as exc:  # noqa: BLE001 - surfaced to the GUI log
            ctx.log(f"  {title}: {exc}")
            ctx.progress(i, total)
            continue
        if result == "cancelled":
            break
        data.setdefault("key", key)
        store.save(data)
        updated += 1
        ctx.log(f"  {title}: {result}.")
        ctx.progress(i, total)

    ctx.log(f"{'Full re-fetch' if full_period else 'Lean refresh'}: "
            f"updated {updated}/{total} channel(s).")
    return "ok"
