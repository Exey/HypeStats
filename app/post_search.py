"""Free-text search over the posts stored in the channel checkpoints — the
logic behind the Folder Search view (app.ui.folder_search_view); nothing in
here touches Qt or the disk.

Scope: only each checkpoint's stored `rows` pool (the top posts by views /
reactions / forwards, the newest ~50, and the best post of every month — see
app.tools.channel_stat.select_pool), never the channel's full history, which
checkpoints don't keep. A post outside that pool can't be found.

Matching: the query is split on whitespace; a post matches when its text
contains *every* word, case-insensitive, as a plain substring ("фото" also
matches "фотограф"). The text searched is the post's whole `full_text`
(falling back to the 140-character `text` preview for older checkpoints).
Reposts forwarded in from other channels are included — they're in the
channel's feed — and simply score 0 Quality (see app.scoring).
"""
from __future__ import annotations

import html
import re
from typing import Iterable

from .scoring import post_score_raw

# sort key -> (row/entry -> sortable value); all best-first (descending).
SORT_KEYS = ("newest", "quality", "views", "reposts")
DEFAULT_SORT = "newest"
LIMITS = (25, 50, 100, 200, 500)
MIN_QUERY_CHARS = 2   # a shorter query doesn't search at all
DEFAULT_LIMIT = 25

# Inline style of a highlighted match inside a QTextBrowser's rich text.
HIGHLIGHT_STYLE = "background-color:#F2C230; color:#12203A;"


def compile_query(text: str) -> tuple[list[re.Pattern], re.Pattern] | None:
    """(one pattern per word, one combined pattern for highlighting), or None
    for an empty query or one shorter than MIN_QUERY_CHARS (after trimming).
    Longer words come first in the combined pattern so
    an overlap highlights the longest match."""
    text = (text or "").strip()
    if len(text) < MIN_QUERY_CHARS:
        return None
    words = text.split()
    pats = [re.compile(re.escape(w), re.IGNORECASE) for w in words]
    combined = re.compile(
        "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)),
        re.IGNORECASE)
    return pats, combined


def post_text(row: dict) -> str:
    return row.get("full_text") or row.get("text") or ""


def matches(text: str, pats: list[re.Pattern]) -> bool:
    return bool(text) and all(p.search(text) for p in pats)


def highlight_html(text: str, combined: re.Pattern | None,
                   style: str = HIGHLIGHT_STYLE) -> str:
    """`text` as HTML-escaped rich text with every match of `combined`
    wrapped in a styled span. Newlines become <br>."""
    out: list[str] = []
    pos = 0
    if combined is not None:
        for m in combined.finditer(text):
            if m.start() == m.end():
                continue
            out.append(html.escape(text[pos:m.start()]))
            out.append(f'<span style="{style}">{html.escape(m.group())}</span>')
            pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out).replace("\n", "<br>")


def _sort_value(entry: dict, sort: str):
    row = entry["row"]
    if sort == "newest":
        return row.get("ts", 0) or 0
    if sort == "views":
        return int(row.get("views", 0) or 0)
    if sort == "reposts":
        return int(row.get("forwards", 0) or 0)
    return entry["raw_score"]   # "quality"


def search_posts(channels: Iterable[dict], query: str, sort: str = DEFAULT_SORT,
                 limit: int = DEFAULT_LIMIT) -> tuple[int, list[dict], re.Pattern | None]:
    """(total matches, the best `limit` of them in `sort` order, highlight
    pattern). `channels` items need `rows` and `avg_views` (the reference
    for the Quality gauge's viral-excess term, same as every other view);
    each result is {"channel", "row", "raw_score"}. An empty or too-short
    query (MIN_QUERY_CHARS) matches nothing."""
    compiled = compile_query(query)
    if compiled is None:
        return 0, [], None
    pats, combined = compiled
    found: list[dict] = []
    for ch in channels:
        avg_views = float(ch.get("avg_views", 0) or 0)
        for row in ch.get("rows") or []:
            if matches(post_text(row), pats):
                found.append({"channel": ch, "row": row,
                              "raw_score": post_score_raw(row, avg_views)})
    found.sort(key=lambda e: _sort_value(e, sort), reverse=True)
    return len(found), found[:max(1, int(limit))], combined
