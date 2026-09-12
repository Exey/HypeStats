"""Cross-channel mentions — which *tracked* channels link to which other
tracked channels, straight from real posted links, and how often. Feeds the
Mutual PR view's "Channel links" card.

Distinct from app.mentions (which matches a link against mentions.md's
roster of known *people*): this matches against ChannelStore's own roster —
"channels we already track in this app" — so it answers Mutual PR's own
question: who already promotes whom? That used to be the Channel links
card's old signal too (app.ui.mutual_pr_view's original
_collect_repost_links), but that one only ever saw a Telegram-reported
"public forward", and only for a channel fetched with "include public
reposts" turned on — most channels never carry that data at all. This
instead scans every channel's own `all_links` (collected on every
fetch/refresh regardless of that setting) for *any* t.me link a post's
caption carries — both a far larger and a far more current sample, already
sitting on disk.

A link's target identity comes from app.mentions.tg_identity_key: "@username"
(case-folded here for matching — t.me/Geekography and t.me/geekography are
the same channel) for a public link, or the bare internal id straight out of
a private t.me/c/<id> link — which is also exactly ChannelStore.list()'s own
"channel_id", so a private link matches a tracked channel exactly as
reliably as a public one, no extra resolution needed. mentions.md extends
that a step further (build_channel_index's `mentions_store` argument): a
tracked channel that's *also* filed as a mentions.md row (same id) has that
row's own "unclear links" registered as extra aliases for it too, so a link
still using an old handle or an otherwise-unresolved private link a row
already accounts for keeps matching after the live one's moved on. Self-
links (a channel linking to its own posts) are dropped — not a cross-
channel signal.

Pure dict/string matching over already-fetched data, no NER, no morphology,
no Telegram calls — fast enough (well under a second across this app's own
~200-channel test set) to run synchronously from the UI thread on a button
click (see app.ui.mutual_pr_view._on_calculate_links_clicked) rather than
through ToolWorker like every multi-channel job elsewhere in this app.
"""
from __future__ import annotations

import json
import time

from .config import config_dir
from .mentions import MentionsStore, normalize_row_identity, tg_identity_key
from .store import ChannelStore


def _link_identity(url: str) -> str | None:
    """tg_identity_key(url), case-folded for the "@username" form so the
    index lookup is case-insensitive the same way canonical_link_key is
    elsewhere in this app."""
    ident = tg_identity_key(url)
    if ident is None:
        return None
    return ident.casefold() if ident.startswith("@") else ident


def build_channel_index(summaries: list[dict],
                        mentions_store: MentionsStore | None = None) -> dict[str, str]:
    """{identity: channel key} from ChannelStore.list()'s own summaries —
    every tracked channel registered under its live @username (case-folded)
    and its internal numeric id, whichever it has. With `mentions_store`
    given, also walks its rows: one whose own id matches a tracked
    channel's identity gets that row's "unclear links" registered as
    additional aliases for the same key — see module docstring."""
    index: dict[str, str] = {}
    for ch in summaries:
        key = ch.get("key")
        if not key:
            continue
        username = ch.get("username") or ""
        if username:
            index[f"@{username}".casefold()] = key
        channel_id = ch.get("channel_id")
        if channel_id:
            index[str(channel_id)] = key

    if mentions_store is not None:
        for row in mentions_store.rows:
            row_ident = normalize_row_identity(row.get("id") or "")
            target_key = index.get(row_ident.casefold() if row_ident.startswith("@")
                                   else row_ident)
            if not target_key:
                continue
            for link in row.get("links") or []:
                ident = _link_identity(link)
                if ident:
                    index.setdefault(ident, target_key)
    return index


def tally_cross_mentions(all_links_by_key: dict[str, list[dict]],
                         index: dict[str, str]) -> list[dict]:
    """Every source->target edge found across `all_links_by_key` (channel
    key -> its own checkpoint's `all_links`), aggregated:
    {"source", "target", "count", "post_ids", "example"} — most-mentioned
    edge first (ties broken by source/target key for a stable order).
    Self-edges (a channel's own link to itself, e.g. a repeated "subscribe"
    plug) are dropped — see module docstring."""
    edges: dict[tuple[str, str], dict] = {}
    for source_key, entries in all_links_by_key.items():
        for entry in entries:
            post_id = entry.get("id")
            for link in entry.get("links") or []:
                url = link.get("url")
                if not url:
                    continue
                ident = _link_identity(url)
                if ident is None:
                    continue
                target_key = index.get(ident)
                if not target_key or target_key == source_key:
                    continue
                e = edges.setdefault((source_key, target_key), {
                    "source": source_key, "target": target_key,
                    "count": 0, "post_ids": [], "example": url,
                })
                e["count"] += 1
                if post_id not in e["post_ids"]:
                    e["post_ids"].append(post_id)
    return sorted(edges.values(), key=lambda e: (-e["count"], e["source"], e["target"]))


def rank_targets(edges: list[dict]) -> list[dict]:
    """One row per *target* channel, most-mentioned first: {"key", "count",
    "sources": [{"key", "count", "example"}, ...]} (sources most-mentioning
    first). What backs the Channel links card's default view — "which
    channel is mentioned more" is a property of the target, summed across
    every source, not any one edge."""
    by_target: dict[str, dict] = {}
    for e in edges:
        t = by_target.setdefault(e["target"], {"key": e["target"], "count": 0, "sources": []})
        t["count"] += e["count"]
        t["sources"].append({"key": e["source"], "count": e["count"], "example": e["example"]})
    for t in by_target.values():
        t["sources"].sort(key=lambda s: -s["count"])
    return sorted(by_target.values(), key=lambda t: -t["count"])


def compute_cross_channel_mentions(store: ChannelStore,
                                   mentions_store: MentionsStore | None = None
                                   ) -> list[dict]:
    """Runs the whole-base scan right now (see module docstring) and
    returns the edge list tally_cross_mentions would — the full pipeline
    (index + load + tally) in one call for the UI's Calculate button."""
    summaries = store.list()
    index = build_channel_index(summaries, mentions_store)
    all_links_by_key: dict[str, list[dict]] = {}
    for ch in summaries:
        data = store.load(ch["key"])
        if data and data.get("all_links") is not None:
            all_links_by_key[ch["key"]] = data["all_links"]
    return tally_cross_mentions(all_links_by_key, index)


# ------------------------------------------------------------------- cache
def cross_mentions_path():
    return config_dir() / "cross_mentions.json"


def load_cross_mentions_cache() -> dict | None:
    """{"edges": [...], "calculated_at": iso-str} last saved by
    cache_cross_mentions — None if it's never been calculated yet, or the
    file's unreadable."""
    try:
        return json.loads(cross_mentions_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def cache_cross_mentions(edges: list[dict]) -> dict:
    """Persists `edges` (see tally_cross_mentions/compute_cross_channel_mentions)
    with a calculated_at stamp, in one shared file — not per-checkpoint,
    since this is a property of the whole tracked set, not any single
    channel. Lets the Channel links card open instantly from whatever was
    last calculated instead of re-scanning the base every time the Mutual
    PR view is shown."""
    data = {"edges": edges,
            "calculated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    cross_mentions_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
