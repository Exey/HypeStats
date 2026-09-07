"""Fetch a channel: rank its posts by engagement *and* compute activity stats.

This is the extracted-and-extended `channel_top` tool. In a single pass over
the chosen period it does two jobs at once:

* engagement ranking (from tg-super-admin's channel_top) — per-post views,
  reactions and forwards ("private reposts"), album-merged, keeping the union
  of the top-N by each metric so the on-screen table can re-sort and still
  show the true leaders. Public reposts are fetched only for that pool. Each
  post is also flagged as a `repost` when it was forwarded in from another
  channel (see _is_repost) — content-quality scoring and the Rating drop
  those so a channel can't pad its score with someone else's viral post.

* activity analytics (ported from telegram-channel-analyzer) — member count,
  creation date, posts/day, posts-with-media share, average/max views, and
  hour-of-day / day-of-week / month distributions for the charts.

The whole thing is returned as one JSON payload, which the app stores as a
per-channel checkpoint (see app.store).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from telethon.tl.types import MessageEntityMention, MessageEntityTextUrl, MessageEntityUrl

from .common import resolve_entity

HEARTBEAT_EVERY = 500

# A post counts as "viral" when its views beat VIRAL_MULTIPLE × the average
# views per post over the VIRAL_BASELINE_MONTHS calendar months *before* its
# own — a trailing baseline, not the channel's lifetime average. Testing
# against the lifetime figure flags nearly every post of a channel that has
# merely grown as "viral" (one export read 90%); testing against the post's
# own month erases the growth entirely. A trailing window sits in between:
# a genuine growth spurt shows up as elevated virality for a quarter or so
# and then settles as the new level becomes the norm. When there isn't
# enough prior history (fewer than VIRAL_BASELINE_MIN_POSTS in the window)
# it falls back to the lifetime average passed in. And however fast a
# channel grows, at most VIRAL_MONTHLY_CAP_FRAC of any one month's posts can
# be "viral" — past that it's a level shift, not a month of outliers.
VIRAL_MULTIPLE = 2.0
VIRAL_BASELINE_MONTHS = 3
VIRAL_BASELINE_MIN_POSTS = 6
VIRAL_MONTHLY_CAP_FRAC = 0.5

# Period-of-analysis choices offered in the GUI, in calendar days. "all"
# (and any unrecognized/empty key) is intentionally absent — period_cutoff()
# treats a missing key as "no limit".
PERIOD_DAYS = {"3m": 90, "6m": 182, "1y": 365, "2y": 730, "3y": 1095}

# ERR% (engagement rate by reach) only looks at posts old enough that their
# view count has settled — freshly-posted messages are still accumulating
# views and would understate the rate.
ERR_MIN_AGE_DAYS = 14

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]


def period_cutoff(period: str) -> datetime | None:
    """UTC cutoff for a period key, or None for "all"/unrecognized/empty."""
    days = PERIOD_DAYS.get(period)
    return datetime.now(timezone.utc) - timedelta(days=days) if days else None


def _reaction_total(msg) -> int:
    reactions = getattr(msg, "reactions", None)
    if not reactions or not getattr(reactions, "results", None):
        return 0
    return sum(int(getattr(rc, "count", 0) or 0) for rc in reactions.results)


def _comment_total(msg) -> int:
    """Comment count from a channel post's linked discussion thread, if
    any (Telethon's Message.replies.replies) — 0 for posts with no
    comments or no linked discussion group."""
    replies = getattr(msg, "replies", None)
    return int(getattr(replies, "replies", 0) or 0)


def _media_type(msg) -> str:
    """"photo" | "video" | "video_note" (round/circle video) | "audio"
    (voice message or audio file) | "file" (any other document) | "" — used
    by the High-Quality Posts view (and the dashboard's recent-posts row)
    to pick a placeholder icon before a thumbnail is fetched (see
    app.tools.media_fetch). Checked in this order since a round video is
    technically also a "video", and a voice message/audio file/photo/video
    are all technically also a "document", in Telethon's eyes — the more
    specific convenience property always has to be checked first."""
    if getattr(msg, "video_note", None) is not None:
        return "video_note"
    if getattr(msg, "video", None) is not None:
        return "video"
    if getattr(msg, "photo", None) is not None:
        return "photo"
    if getattr(msg, "voice", None) is not None or getattr(msg, "audio", None) is not None:
        return "audio"
    if getattr(msg, "document", None) is not None:
        return "file"
    return ""


def _has_buttons(msg) -> bool:
    """Whether this message carries an inline keyboard — ads/CTAs commonly
    attach one, and app.scoring excludes posts like that from content-
    quality scoring, since clicks it's fishing for aren't the same signal
    as organic engagement."""
    return getattr(msg, "reply_markup", None) is not None


def _is_repost(msg, own_channel_id: int) -> bool:
    """Whether this post was forwarded into the channel from *another*
    source — the channel re-publishing someone else's post (Telethon's
    `Message.fwd_from`). A channel re-forwarding its *own* earlier post is
    not a repost; a forward whose origin Telegram strips (`fwd_from` set but
    `from_id` gone) still is — the content isn't original to this channel
    either way.

    app.scoring scores a repost 0 (its views/reactions belong to the
    original author, not this channel), and the Folder Stats view drops
    reposts from the per-period view/share/reaction/viral totals that feed
    the composite Rating — so a channel can't inflate its quality or rating
    by reposting viral content from a bigger channel. Channel-level stat
    cards (avg views, ERR%, viral share, Mutual PR) still count reposts.
    """
    fwd = getattr(msg, "fwd_from", None)
    if fwd is None:
        return False
    origin = getattr(getattr(fwd, "from_id", None), "channel_id", None)
    if origin is not None and own_channel_id and int(origin) == int(own_channel_id):
        return False
    return True


def _repost_source(msg) -> tuple[int | None, str]:
    """(origin channel_id, author byline) off a forward's `fwd_from` — data
    Telethon already fetched with the message, no extra API call. Both are
    best-effort: `from_id` is usually a bare PeerChannel with no name
    attached (resolved against this app's own tracked channels at display
    time — see app.ui.compare.mentions_view), and `post_author` is empty
    unless the origin channel signed the post with a custom byline.
    (None, "") if the post isn't a forward, or Telegram stripped its origin
    (fwd_from set but from_id gone) — still a repost per _is_repost, just an
    unnamed one."""
    fwd = getattr(msg, "fwd_from", None)
    if fwd is None:
        return None, ""
    channel_id = getattr(getattr(fwd, "from_id", None), "channel_id", None)
    author = (getattr(fwd, "post_author", None) or "").strip()
    return (int(channel_id) if channel_id is not None else None), author


_LINK_ENTITY_TYPES = (MessageEntityUrl, MessageEntityTextUrl, MessageEntityMention)


def _extract_links(msg) -> list[dict]:
    """Every link this message actually carries, as {"text", "url"} pairs,
    distinct by url and in the order they appear: a plain URL typed into
    the text (MessageEntityUrl — its "inner text" *is* the URL, so `text`
    just repeats it), a "text link" (MessageEntityTextUrl — `text` is the
    visible display text, e.g. a person's name, and `url` is its real
    target, otherwise invisible in `msg.message` itself since only the
    display text sits there — the "hidden in text" gap that motivated
    capturing links as their own field), and a bare "@username" mention
    (MessageEntityMention — no URL entity at all, just plain text Telegram
    still auto-detects and treats as a link to that channel/user; missing
    this one meant a post crediting a collaborator as plain "@kuprianow"
    text, with no actual hyperlink, never showed up as a link at all).
    Keeping `text` alongside `url`, rather than just the bare url list this
    used to return, is what lets the Mentions view tell *which* word a link
    belongs to — e.g. a post naming "Алиса" as this week's model, with a
    text-link's display text of "Алиса" pointing at her own channel — and
    use that to strengthen an otherwise-ambiguous name extraction (see
    app.ui.compare.mentions_view._populate_texts_table).

    Uses Message.get_entities_text() rather than slicing `msg.message` by
    hand with the entity's own offset/length — those offsets are UTF-16
    code units (Telegram's wire format), which can disagree with Python
    string indices as soon as an emoji or other astral character appears
    earlier in the text; get_entities_text() (and the get_inner_text()
    helper it calls) already does the surrogate-pair-aware conversion this
    would otherwise get wrong."""
    seen: dict[str, dict] = {}
    for ent, inner_text in msg.get_entities_text(_LINK_ENTITY_TYPES):
        if isinstance(ent, MessageEntityTextUrl):
            url, text = ent.url, inner_text
        elif isinstance(ent, MessageEntityMention):
            username = inner_text.lstrip("@").strip()
            if not username:
                continue
            url, text = f"https://t.me/{username}", inner_text
        else:
            url, text = inner_text, inner_text
        if url:
            seen.setdefault(url, {"text": text, "url": url})
    return list(seen.values())


def _preview(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _top_ids(rows: list[dict], key: str, n: int) -> set[int]:
    ranked = sorted(rows, key=lambda r: r[key], reverse=True)
    return {r["id"] for r in ranked[:n]}


def _one_per_month_ids(rows: list[dict]) -> set[int]:
    """Best (highest-viewed) post's id from *every* calendar month that has
    any post at all. A flat "top-N most recent posts" cutoff (see
    `_top_ids(rows, "ts", top_n)` below) can be entirely consumed by just
    one or two unusually active recent months, silently dropping quieter
    months right next to them from the pool — this guarantees no month is
    ever completely unrepresented, regardless of how many posts its
    neighbors have."""
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("date") or "")[:7]
        if len(label) == 7:
            by_month.setdefault(label, []).append(r)
    ids: set[int] = set()
    for month_rows in by_month.values():
        ids.add(max(month_rows, key=lambda r: r["views"])["id"])
    return ids


def _trimmed_mean_drop_top(values: list[int], drop_frac: float) -> float:
    """Mean after dropping the top `drop_frac` fraction of values (sorted
    ascending) — a true trimmed mean over the *whole* distribution, not
    just excluding the single largest value. Used for avg_reposts_trimmed:
    reposts are far more top-heavy than views/reactions (one giveaway or
    forward chain can be a huge share of a channel's total repost count
    across its whole history), so the plain average swings on outliers
    much more than it should for "typical performance"."""
    if not values:
        return 0.0
    vals = sorted(values)
    keep_n = max(1, round(len(vals) * (1 - drop_frac)))
    kept = vals[:keep_n]
    return sum(kept) / len(kept)


async def _public_forwards(client, input_channel, msg_id: int) -> dict:
    """{'count': int, 'items': [{title, link, views}]} for one post, or
    {'count': -1, 'items': []} if stats aren't available for this channel."""
    from telethon.tl.functions.stats import GetMessagePublicForwardsRequest
    from telethon.tl.types import PublicForwardMessage

    try:
        res = await client(GetMessagePublicForwardsRequest(
            channel=input_channel, msg_id=msg_id, offset="", limit=100))
    except Exception:
        return {"count": -1, "items": []}

    chats_by_id = {c.id: c for c in getattr(res, "chats", [])}
    items: list[dict] = []
    for fwd in getattr(res, "forwards", []):
        if not isinstance(fwd, PublicForwardMessage):
            continue
        m = fwd.message
        cid = getattr(getattr(m, "peer_id", None), "channel_id", None)
        chat = chats_by_id.get(cid)
        title = str(getattr(chat, "title", None) or cid or "?")
        username = getattr(chat, "username", None)
        if username:
            link = f"https://t.me/{username}/{m.id}"
        elif cid:
            link = f"https://t.me/c/{cid}/{m.id}"
        else:
            link = ""
        items.append({"title": title, "link": link,
                      "views": int(getattr(m, "views", 0) or 0)})
    count = int(getattr(res, "count", len(items)) or len(items))
    return {"count": count, "items": items}


async def _count_since(client, entity, cutoff: datetime | None) -> int:
    """Server-computed post count for the period (cheap, no per-message fetch)."""
    if cutoff is None:
        return (await client.get_messages(entity, limit=0)).total or 0
    boundary = await client.get_messages(entity, limit=1, offset_date=cutoff)
    min_id = boundary[0].id if boundary else 0
    return (await client.get_messages(entity, limit=0, min_id=min_id)).total or 0


async def _channel_info(client, entity) -> dict:
    """Member count, creation date and description via GetFullChannel.

    Best-effort: a chat that isn't a broadcast channel (or a stats hiccup)
    just yields blanks rather than failing the whole fetch.
    """
    info = {
        "id": int(getattr(entity, "id", 0) or 0),
        "title": str(getattr(entity, "title", "") or ""),
        "username": getattr(entity, "username", None) or "",
        "members": 0,
        "about": "",
        "created": "",
    }
    created = getattr(entity, "date", None)
    if created:
        info["created"] = created.isoformat()
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        full = await client(GetFullChannelRequest(channel=entity))
        fc = full.full_chat
        info["members"] = int(getattr(fc, "participants_count", 0) or 0)
        info["about"] = str(getattr(fc, "about", "") or "")
    except Exception:
        pass
    return info


def _month_index(label: str) -> int:
    """'YYYY-MM' -> a monotonic month number, for trailing-window math."""
    year, month = (int(x) for x in label.split("-"))
    return year * 12 + (month - 1)


def _viral_baseline(month_posts: list[dict], fallback_avg: float) -> dict[str, float]:
    """label -> the views-per-post average to test that month's posts against
    for "viral" (see VIRAL_MULTIPLE): the mean over the VIRAL_BASELINE_MONTHS
    calendar months immediately before it, or `fallback_avg` (the channel's
    lifetime average) when that window holds fewer than
    VIRAL_BASELINE_MIN_POSTS."""
    by_month: dict[str, list[float]] = {}
    for p in month_posts:
        s = by_month.setdefault(p["label"], [0.0, 0])
        s[0] += p["views"]
        s[1] += 1
    idx = {lbl: _month_index(lbl) for lbl in by_month}
    out: dict[str, float] = {}
    for label in by_month:
        window = range(idx[label] - VIRAL_BASELINE_MONTHS, idx[label])
        total = sum(by_month[l][0] for l, i in idx.items() if i in window)
        count = sum(by_month[l][1] for l, i in idx.items() if i in window)
        out[label] = total / count if count >= VIRAL_BASELINE_MIN_POSTS and total else fallback_avg
    return out


def _recent_avg_views(month_posts: list[dict], fallback_avg: float) -> float:
    """Mean views per post over the most recent VIRAL_BASELINE_MONTHS calendar
    months that have posts — the "what's normal for this channel *now*"
    figure. Used as the reference for the per-post quality gauge's
    viral-excess term (app.scoring) so a channel that has simply grown
    doesn't read every recent post as a breakout. Falls back to the lifetime
    average when there isn't enough recent history."""
    by_month: dict[str, list[float]] = {}
    for p in month_posts:
        s = by_month.setdefault(p["label"], [0.0, 0])
        s[0] += p["views"]
        s[1] += 1
    if not by_month:
        return fallback_avg
    newest = max(_month_index(lbl) for lbl in by_month)
    window = range(newest - VIRAL_BASELINE_MONTHS + 1, newest + 1)
    total = sum(by_month[l][0] for l in by_month if _month_index(l) in window)
    count = sum(by_month[l][1] for l in by_month if _month_index(l) in window)
    return total / count if count >= VIRAL_BASELINE_MIN_POSTS and total else fallback_avg


def _monthly_aggregates(month_posts: list[dict], avg_views: float) -> dict[str, dict]:
    """Per-month sums over *every* scanned post (not just the top-N pool), so
    folder/period views reflect real totals instead of a sampled subset.

    `month_posts` must be the per-post snapshots captured by `_account` at
    merge time — not the final `rows` list, whose views/reactions/forwards
    get bumped up afterwards by later album members via max(). Reading from
    the mutated `rows` would make these sums inconsistent with `stats`
    (avg_views etc.), which are accumulated from the same snapshots.
    """
    baseline = _viral_baseline(month_posts, avg_views)
    agg: dict[str, dict] = {}
    for p in month_posts:
        a = agg.setdefault(p["label"], {
            "views": 0, "shares": 0, "reactions": 0, "viral_count": 0, "count": 0,
            # ..._own = same sums over the channel's *own* posts only
            # (reposts forwarded in from other channels excluded) — the
            # Folder Stats Rating reads these so reposted viral content
            # can't pad a channel's score. See _is_repost.
            "views_own": 0, "shares_own": 0, "reactions_own": 0,
            "viral_count_own": 0, "count_own": 0,
        })
        a["views"] += p["views"]
        a["shares"] += p["shares"]
        a["reactions"] += p["reactions"]
        a["count"] += 1
        base = baseline.get(p["label"], avg_views)
        is_viral = bool(base and p["views"] > VIRAL_MULTIPLE * base)
        if is_viral:
            a["viral_count"] += 1
        if not p.get("repost"):
            a["views_own"] += p["views"]
            a["shares_own"] += p["shares"]
            a["reactions_own"] += p["reactions"]
            a["count_own"] += 1
            if is_viral:
                a["viral_count_own"] += 1
    # However fast the channel grew, at most VIRAL_MONTHLY_CAP_FRAC of a
    # month's posts can be "viral" — beyond that it's a level shift.
    for a in agg.values():
        a["viral_count"] = min(a["viral_count"], int(a["count"] * VIRAL_MONTHLY_CAP_FRAC))
        a["viral_count_own"] = min(a["viral_count_own"],
                                   int(a["count_own"] * VIRAL_MONTHLY_CAP_FRAC))
    return agg


def _monthly_top_posts(rows: list[dict]) -> dict[str, dict]:
    """For each month, the single highest-viewed merged post — computed over
    *every* scanned post (final, post-album-merge state), not just the
    top-N pool, so a period's "most viewed post" is always the real one
    instead of only showing up when it happens to also be a global top-N
    pick. Reposts (content forwarded in from another channel — see
    _is_repost) are skipped so a period's showcased post is the channel's
    own, matching the repost-excluded Rating totals."""
    top: dict[str, dict] = {}
    for r in rows:
        if r.get("repost"):
            continue
        label = (r.get("date") or "")[:7]
        if len(label) != 7:
            continue
        cur = top.get(label)
        if cur is None or r["views"] > cur["views"]:
            top[label] = r
    return top


def _monthly_series(month_counts: dict[str, int], month_agg: dict[str, dict],
                    month_top: dict[str, dict]) -> list[dict]:
    """Fill gaps between the first and last month so the activity bars have no
    holes; each item is {'label': 'YYYY-MM', 'count', 'views', 'shares',
    'reactions', 'viral_count', 'top'}."""
    if not month_counts:
        return []
    keys = sorted(month_counts)
    (y0, m0), (y1, m1) = (map(int, keys[0].split("-")),
                          map(int, keys[-1].split("-")))
    series: list[dict] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        label = f"{y:04d}-{m:02d}"
        a = month_agg.get(label, {})
        series.append({
            "label": label,
            "count": int(month_counts.get(label, 0)),
            "views": int(a.get("views", 0)),
            "shares": int(a.get("shares", 0)),
            "reactions": int(a.get("reactions", 0)),
            "viral_count": int(a.get("viral_count", 0)),
            # Own-posts-only totals (reposts excluded) — Folder Stats
            # Rating reads these; see _monthly_aggregates / _is_repost.
            "count_own": int(a.get("count_own", 0)),
            "views_own": int(a.get("views_own", 0)),
            "shares_own": int(a.get("shares_own", 0)),
            "reactions_own": int(a.get("reactions_own", 0)),
            "viral_count_own": int(a.get("viral_count_own", 0)),
            "top": month_top.get(label),
        })
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return series


async def run_channel_stat(client, p: dict, ctx) -> str:
    """p: channel, top_n, period (PERIOD_DAYS key, '' = all), fetch_public,
    fallback_id (optional — a previously known numeric channel id, see
    resolve_entity, passed through by lean_refresh._full_refresh when
    re-fetching an already-tracked channel; absent for a brand-new fetch,
    which has no prior checkpoint to draw one from)."""
    top_n = int(p.get("top_n") or 20)
    period = p.get("period") or ""
    cutoff = period_cutoff(period)
    fetch_public = bool(p.get("fetch_public"))

    entity = await resolve_entity(client, p["channel"], p.get("fallback_id"))
    info = await _channel_info(client, entity)
    title = info["title"] or str(p["channel"])
    try:
        total = await _count_since(client, entity, cutoff)
    except Exception:
        total = 0
    ctx.log(f"Scanning '{title}' ({total or 'unknown'} post(s))…")

    rows: list[dict] = []
    scanned = 0
    current: dict | None = None   # album row currently being accumulated
    current_gid = None
    current_anchor_msg = None     # first message of `current`'s group — its
                                  # date/media type represent the merged post
    err_cutoff = datetime.now(timezone.utc) - timedelta(days=ERR_MIN_AGE_DAYS)
    last_full_year = datetime.now(timezone.utc).year - 1

    # Activity accumulators (per merged post).
    posts = 0
    sum_views = 0
    views_n = 0
    max_views = 0
    sum_reactions = 0
    sum_forwards = 0
    max_forwards = 0
    sum_views_settled = 0
    views_settled_n = 0
    sum_views_last_year = 0
    sum_forwards_last_year = 0
    forwards_seen: list[int] = []  # per-post reposts, for the trimmed-mean pass below
    with_media = with_photo = with_document = 0
    hour_dist = [0] * 24
    weekday_dist = [0] * 7
    month_counts: dict[str, int] = {}
    month_posts: list[dict] = []  # per-post snapshot at merge time, for _monthly_aggregates
    first_ts: int | None = None
    last_ts: int | None = None

    def _account(row: dict, msg) -> None:
        """Fold one *finalized* merged post into the activity stats.

        Called once a post's album group is fully closed, so `row`'s
        views/reactions/forwards are already the max() across every group
        member. Calling this at group-*start* instead (before later album
        members are merged in) undercounts reactions in particular: Telegram
        often reports the real reaction count on only one message of a
        media group, not the one iteration happens to visit first.
        """
        nonlocal posts, sum_views, views_n, max_views, sum_reactions
        nonlocal sum_forwards, max_forwards, sum_views_settled, views_settled_n
        nonlocal sum_views_last_year, sum_forwards_last_year
        nonlocal with_media, with_photo, with_document, first_ts, last_ts
        posts += 1
        v = row["views"]
        if v > 0:
            sum_views += v
            views_n += 1
            if msg.date and msg.date < err_cutoff:
                sum_views_settled += v
                views_settled_n += 1
        max_views = max(max_views, v)
        sum_reactions += row["reactions"]
        sum_forwards += row["forwards"]
        forwards_seen.append(row["forwards"])
        max_forwards = max(max_forwards, row["forwards"])
        if msg.date and msg.date.year == last_full_year:
            sum_views_last_year += v
            sum_forwards_last_year += row["forwards"]
        if getattr(msg, "media", None) is not None:
            with_media += 1
        if getattr(msg, "photo", None) is not None:
            with_photo += 1
        if getattr(msg, "document", None) is not None:
            with_document += 1
        d = msg.date
        if d:
            hour_dist[d.hour] += 1
            weekday_dist[d.weekday()] += 1
            label = f"{d.year:04d}-{d.month:02d}"
            month_counts[label] = month_counts.get(label, 0) + 1
            month_posts.append({"label": label, "views": v,
                                "shares": row["forwards"], "reactions": row["reactions"],
                                "repost": row.get("repost", False)})
            ts = int(d.timestamp())
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)

    async for msg in client.iter_messages(entity):
        if ctx.cancelled():
            break
        if cutoff and msg.date and msg.date < cutoff:
            break  # newest -> oldest, so we're past the window
        scanned += 1
        if getattr(msg, "action", None) is not None:
            continue  # skip service messages (joins, pins, …)

        gid = getattr(msg, "grouped_id", None)
        full_text = " ".join((getattr(msg, "message", "") or "").split())
        text = _preview(full_text)
        views = int(getattr(msg, "views", 0) or 0)
        reactions = _reaction_total(msg)
        forwards = int(getattr(msg, "forwards", 0) or 0)
        comments = _comment_total(msg)
        media_type = _media_type(msg)
        has_buttons = _has_buttons(msg)
        is_repost = _is_repost(msg, info["id"])
        repost_from_id, repost_from_author = _repost_source(msg)
        links = _extract_links(msg)

        if gid is not None and gid == current_gid:
            # Same album — merge into the row being built (see channel_top).
            current["ids"].append(msg.id)
            current["id"] = msg.id
            current["views"] = max(current["views"], views)
            current["reactions"] = max(current["reactions"], reactions)
            current["forwards"] = max(current["forwards"], forwards)
            current["comments"] = max(current["comments"], comments)
            # OR, not anchor-only like media_type below — an inline
            # keyboard on *any* member of the album is enough to treat the
            # whole merged post as button-driven.
            current["has_buttons"] = current["has_buttons"] or has_buttons
            current["repost"] = current["repost"] or is_repost
            # Fill-in-only, like text/full_text below — a forwarded album
            # shares one origin, so the anchor's own fwd_from (set at
            # creation, just below) is normally already there.
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
            if current is not None:
                _account(current, current_anchor_msg)  # previous group is now closed
            current = {
                "id": msg.id,
                "ids": [msg.id],
                "ts": int(msg.date.timestamp()) if msg.date else 0,
                "date": msg.date.isoformat() if msg.date else "",
                "text": text,
                "full_text": full_text,
                # See _extract_links — paired with text/full_text above
                # (fill-in-only in the merge branch), since a link only
                # makes sense attached to the caption it actually came from.
                "links": links,
                "views": views,
                "reactions": reactions,
                "forwards": forwards,
                "comments": comments,
                # From the anchor message only, like date/ts — an album's
                # other members aren't re-checked, matching the "cover"
                # item a viewer would actually see first.
                "media_type": media_type,
                # Unlike media_type above, this DOES tally every album
                # member (see the merge branch) — {"photo": 7} for a
                # 7-photo album, {"photo": 2, "video": 2} for a mixed one —
                # so the High-Quality Posts grid can show "×7🏞️" etc. on
                # the card instead of just the cover item's single icon.
                "media_counts": {media_type: 1} if media_type else {},
                # See _has_buttons — OR'd across the whole album in the
                # merge branch above, not anchor-only.
                "has_buttons": has_buttons,
                # See _is_repost — likewise OR'd across the album.
                "repost": is_repost,
                # See _repost_source — filled in (not OR'd) across the
                # album in the merge branch above.
                "repost_from_id": repost_from_id,
                "repost_from_author": repost_from_author,
                "public": None,
            }
            current_gid = gid
            current_anchor_msg = msg
            rows.append(current)

        if scanned % HEARTBEAT_EVERY == 0:
            ctx.log(f"  scanned {scanned}/{total or '?'}…")
        ctx.progress(scanned, total)

    if current is not None:
        _account(current, current_anchor_msg)  # last group in the stream

    ctx.log(f"Read stats for {len(rows)} post(s) ({scanned} message(s) scanned).")

    # -------- activity summary --------
    if first_ts is not None and last_ts is not None:
        total_days = max(1, (last_ts - first_ts) // 86400 + 1)
        first_iso = datetime.fromtimestamp(first_ts, timezone.utc).isoformat()
        last_iso = datetime.fromtimestamp(last_ts, timezone.utc).isoformat()
    else:
        total_days, first_iso, last_iso = 0, "", ""
    avg_views_raw = sum_views / views_n if views_n else 0
    month_agg = _monthly_aggregates(month_posts, avg_views_raw)
    # Whole-channel viral share = the sum of the per-month viral counts (each
    # judged against that month's own average, not the lifetime one — see
    # _viral_baseline), so it matches what the Folder Stats / export show for
    # a period rather than reading high just because the channel has grown.
    viral_count = sum(a["viral_count"] for a in month_agg.values())
    stats = {
        "total_posts": posts,
        "avg_views": round(sum_views / views_n, 1) if views_n else 0,
        "avg_views_recent": round(_recent_avg_views(month_posts, avg_views_raw), 1),
        "viral_post_share": round(viral_count / posts * 100, 1) if posts else 0,
        "max_views": max_views,
        "avg_reactions": round(sum_reactions / posts, 1) if posts else 0,
        "avg_reposts": round(sum_forwards / posts, 1) if posts else 0,
        # Top 10% (by reposts) dropped before averaging — see
        # _trimmed_mean_drop_top. Used where a plain average would swing
        # too much on one-off repost spikes (e.g. Content Quality Index).
        "avg_reposts_trimmed": round(_trimmed_mean_drop_top(forwards_seen, 0.10), 2),
        "max_reposts": max_forwards,
        "avg_views_settled": round(sum_views_settled / views_settled_n, 1)
                             if views_settled_n else 0,
        "last_full_year": last_full_year,
        "last_year_views": sum_views_last_year,
        "last_year_reposts": sum_forwards_last_year,
        "avg_posts_per_day": round(posts / total_days, 2) if total_days else 0,
        "posts_with_media": with_media,
        "posts_with_photo": with_photo,
        "posts_with_document": with_document,
        "media_pct": round(with_media / posts * 100, 1) if posts else 0,
        "total_days": total_days,
        "first_post_date": first_iso,
        "last_post_date": last_iso,
    }
    month_top = _monthly_top_posts(rows)
    distributions = {
        "hour": hour_dist,
        "weekday": weekday_dist,          # Mon..Sun
        "monthly": _monthly_series(month_counts, month_agg, month_top),
    }

    # Every scanned post's links, not just the engagement pool below --
    # _extract_links already ran over every row during the iter_messages
    # loop above (it's free, no extra Telegram calls), so this just keeps
    # what the pool filter would otherwise throw away. Still no full_text
    # (only date + id + each link's own {"text","url"}) since it's one
    # entry per *scanned* post rather than per pool post, but the anchor
    # text itself is kept -- app.mentions.classify_channel_links needs it
    # to tell a person's name from a plain "Boosty"/"смотреть здесь" button
    # caption, and it's a couple of words, not a full caption, so this
    # stays cheap even across a channel's entire history. `id` is what lets
    # the Mentions view's Link report popup jump back to the specific post
    # a name/link came from, the same way `_post_id_chips` already does for
    # the pool's own Names Found table. Backs Mentions' "All unique
    # links"/"Balance tg / web links" stats (see
    # mentions_view._link_balance_stats_full) and its full-history
    # fair/fake/unresolved classification (see
    # mentions_view._classify_full_history).
    all_links = [{"date": r["date"], "id": r["id"], "links": r["links"]}
                for r in rows if r["links"]]

    # -------- engagement pool (union of top-N by each metric) --------
    # Also unions in the top-N most *recent* posts ("ts") and one post per
    # calendar month, not just the top-N by engagement — a fresh post has
    # barely had time to accumulate views/reactions/forwards, so on an
    # active channel with a long history it will almost never rank in the
    # engagement-based top-N, leaving the most recent weeks/months
    # completely unrepresented in the pool. "ts" alone isn't quite enough
    # either: a couple of unusually active recent months can consume the
    # entire top-N-by-recency budget and still leave a quieter month right
    # next to them with zero posts in the pool, which is where
    # _one_per_month_ids comes in — see its docstring. The High-Quality
    # Posts view, and the dashboard's "recent posts" row/Quality trend
    # line, all read from this same pool — without both of these, they'd
    # silently drop whichever recent months don't happen to stand out.
    pool_ids: set[int] = set()
    for key in ("views", "reactions", "forwards", "ts"):
        pool_ids |= _top_ids(rows, key, top_n)
    pool_ids |= _one_per_month_ids(rows)
    pool = [r for r in rows if r["id"] in pool_ids]
    pool.sort(key=lambda r: r["views"], reverse=True)

    if fetch_public and pool and not ctx.cancelled():
        input_channel = await client.get_input_entity(entity)
        ctx.log(f"Fetching public reposts for {len(pool)} post(s)…")
        unavailable = False
        for i, r in enumerate(pool, 1):
            if ctx.cancelled():
                break
            r["public"] = await _public_forwards(client, input_channel, r["id"])
            if r["public"]["count"] < 0:
                unavailable = True
            ctx.progress(i, len(pool))
        if unavailable:
            ctx.log("  Public-repost stats are unavailable for this channel "
                    "(needs a channel where you can view statistics).")

    username = info["username"]
    if username:
        link = f"https://t.me/{username}"
    elif info["id"]:
        link = f"https://t.me/c/{info['id']}"
    else:
        link = ""

    return json.dumps({
        "schema": 1,
        "cancelled": ctx.cancelled(),
        "channel": str(p["channel"]),
        "title": title,
        "username": username,
        "link": link,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "period": period,
        "top_n": top_n,
        "fetch_public": fetch_public,
        "info": info,
        "stats": stats,
        "distributions": distributions,
        "scanned": len(rows),
        "rows": pool,
        "all_links": all_links,
    })
