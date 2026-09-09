"""Batch export of a folder's Link report / Mentions report — the same two
tables app.ui.compare.mentions_view's own popups show (see its
_open_link_report/_open_mentions_report) — for *every* channel in the
folder at once, concatenated into one Markdown file with a "## {channel
title}" heading per channel. Triggered by the Config screen's "Mentions"
card ("All Link Reports"/"All Mentions Report" buttons).

Pure local computation over each channel's already-stored checkpoint and
the shared mentions.md/name_exceptions.txt — no live Telegram calls at all
— but still runs through the same ToolWorker/Ctx machinery as every other
tool here (background thread, Cancel support, a progress log) rather than
freezing the GUI thread: the Mentions report runs full extraction (NER
included) over every post in every channel's pool, real work for a large
folder.

Deliberately unscoped to any period (unlike the interactive popups, which
follow the Mentions view's own period picker) — there's no such picker
here, and "the channel's whole stored history" is the one sensible default
for an export meant to be read outside the app."""
from __future__ import annotations

from ..mentions import (
    MentionsStore, NameExceptions, classify_channel_links, extract_all_names_per_post,
    name_link_matches, normalize_links, tg_identity_key,
)
from ..store import ChannelStore

_STATUS_LABELS = {"fair": "Fair", "unresolved": "Unresolved", "fake": "Fake", "promo": "Promo"}


def _channel_heading(data: dict, key: str) -> str:
    title = data.get("title") or data.get("channel") or key
    return f"## {title}\n"


def _link_report_section(data: dict, key: str, store: MentionsStore,
                         name_exceptions: NameExceptions) -> str:
    """One channel's Link report table, as Markdown — see
    app.ui.compare.mentions_view._open_link_report for the interactive
    twin this mirrors (same classify_channel_links call, same "fake"
    per-name row expansion, same most-repeated-first order)."""
    entries = data.get("all_links")
    lines = [_channel_heading(data, key)]
    if entries is None:
        lines.append("_No link data — this channel predates `all_links`; "
                     "re-fetch it to include it here._\n")
        return "\n".join(lines)

    own_channel_key = tg_identity_key(data.get("link") or "")
    classes = classify_channel_links(entries, store, name_exceptions, own_channel_key)

    rows: list[tuple[str, str, int, str]] = []
    for c in classes.values():
        url = c["url"]
        if c["status"] == "fake":
            for name in c.get("names") or [c["text"]]:
                rows.append((c["status"], name, c["count"], url))
        else:
            rows.append((c["status"], c["text"], c["count"], url))
    rows.sort(key=lambda r: r[2], reverse=True)

    if not rows:
        lines.append("_No classified links in scope._\n")
        return "\n".join(lines)
    lines.append("| Status | Name | Count | Link |")
    lines.append("| --- | --- | --- | --- |")
    for status, name, count, url in rows:
        status_label = _STATUS_LABELS.get(status, status)
        name = name.replace("|", "\\|")
        lines.append(f"| {status_label} | {name} | {count} | {url} |")
    lines.append("")
    return "\n".join(lines)


def _mentions_report_section(data: dict, key: str, store: MentionsStore,
                             name_exceptions: NameExceptions) -> str:
    """One channel's Mentions report table, as Markdown — see
    app.ui.compare.mentions_view._open_mentions_report for the interactive
    twin: every name extract_all_names_per_post found across the
    channel's stored pool, with whichever link (if any) it was credited
    to, most-mentioned first."""
    posts = data.get("rows") or []
    lines = [_channel_heading(data, key)]
    if not posts:
        lines.append("_No posts stored for this channel._\n")
        return "\n".join(lines)

    all_names = extract_all_names_per_post(posts, store, name_exceptions)
    name_hits: dict[str, list[int]] = {}
    name_link: dict[str, str | None] = {}
    for post, names in zip(posts, all_names):
        post_id = int(post.get("id", 0))
        for name in names:
            name_hits.setdefault(name, []).append(post_id)
            name_link.setdefault(name, None)
        links = normalize_links(post.get("links"))
        for name, link in name_link_matches(names, links):
            if name_link.get(name) is None:
                name_link[name] = link["url"]

    if not name_hits:
        lines.append("_No names found in this channel's stored posts._\n")
        return "\n".join(lines)
    rows = sorted(name_hits.items(), key=lambda kv: len(kv[1]), reverse=True)
    lines.append("| Name | Count | Link |")
    lines.append("| --- | --- | --- |")
    for name, post_ids in rows:
        url = name_link.get(name) or "No link"
        safe_name = name.replace("|", "\\|")
        lines.append(f"| {safe_name} | {len(post_ids)} | {url} |")
    lines.append("")
    return "\n".join(lines)


async def _run_export(p: dict, ctx, section_fn, kind: str) -> str:
    keys = p.get("keys") or []
    out_path = p.get("out_path") or ""
    if not out_path:
        return "No destination file given."
    store = ChannelStore()
    mentions_store = MentionsStore()
    name_exceptions = NameExceptions()
    total = len(keys)
    ctx.log(f"Exporting {kind} for {total} channel(s)…")

    sections: list[str] = []
    done = 0
    for i, key in enumerate(keys, 1):
        if ctx.cancelled():
            break
        data = store.load(key)
        if not data:
            ctx.log(f"  {key}: no checkpoint on disk, skipped.")
            ctx.progress(i, total)
            continue
        title = data.get("title") or key
        sections.append(section_fn(data, key, mentions_store, name_exceptions))
        done += 1
        ctx.log(f"  {title}: done.")
        ctx.progress(i, total)

    if ctx.cancelled():
        return "cancelled"
    if not sections:
        return f"Nothing to export — no readable checkpoints among the {total} selected."

    text = "\n".join(sections)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        raise ValueError(f"Could not write {out_path}: {exc}") from exc
    ctx.log(f"Wrote {done}/{total} channel(s) to {out_path}.")
    return "ok"


async def run_link_report_export(client, p: dict, ctx) -> str:
    """p: {"keys": [ChannelStore key, ...], "out_path": str}. `client`
    unused — kept for ToolWorker's uniform (client, params, ctx) call
    shape, same as every other tool here, even though this one makes no
    Telegram calls at all."""
    return await _run_export(p, ctx, _link_report_section, "Link report")


async def run_mentions_report_export(client, p: dict, ctx) -> str:
    """p: {"keys": [ChannelStore key, ...], "out_path": str}. See
    run_link_report_export re: the unused `client` param."""
    return await _run_export(p, ctx, _mentions_report_section, "Mentions report")
