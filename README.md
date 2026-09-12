# TG Channel Stats

A cross-platform desktop app for analyzing Telegram channels. Point it at any
public channel (or a private one you're a member of), pick a period, and it
scans every post to rank the top performers **and** compute activity
analytics. Track many channels side by side, organize them into folders and
tags, and use the folder-level views to study cross-promotion, content
quality, and ad-swap ("mutual PR") potential across a whole group of
channels — all on dashboards you can re-open, re-fetch, and export.

Built with [PySide6](https://doc.qt.io/qtforpython/) (Qt for Python) and
[Telethon](https://docs.telethon.dev/). English and Russian UI.

## Run from source

The quickest way — the script creates a `.venv` on first run, installs
dependencies, and launches the app:

```bash
./run_dev.sh          # macOS / Linux
run_dev.bat           # Windows
```

Re-run the same script any time to start the app; it only reinstalls if
`requirements.txt` changed. See [Installation](#installation) for the manual
equivalent.

![tgchanstat.png](https://i.postimg.cc/MWnhLs56/tgchanstat.png)

## What it does

For a chosen channel and time window, a single pass over the channel's history
produces one JSON checkpoint holding:

- **Engagement ranking** — per-post views, reactions, forwards ("private
  reposts"), comments, media type, links (both a plain URL typed in the
  caption and a "text link"'s real target, which Telegram never puts in the
  plain message text — see **Refresh mentions** below), and whether the
  post was itself forwarded in from another channel (plus that forward's
  origin, when Telegram exposes it — the Mentions view uses this), with
  albums merged into one row. It keeps the union of the top-N by each metric
  *plus* the most recent top-N
  *plus* the best post of every calendar month, so the on-screen table can
  re-sort by any column and still show the true leaders, and the
  quality/recent views never silently drop a month. Optionally fetches
  **public reposts** for that pool (which channels re-shared each post),
  where stats access allows.
- **Activity analytics** — member count, creation date, posts/day,
  average/max views, average/max reposts, average reactions, share of posts
  with media, "settled" average views (posts older than 14 days, whose view
  count has stopped climbing), a trimmed repost average, viral-post share,
  and distributions by **hour of day**, **day of week**, and **month** (with
  per-month view/share/reaction totals over *every* scanned post, not just
  the top-N sample).

Each analyzed channel is saved as its own JSON checkpoint and listed in the
sidebar, so closing the app (or a crash mid-scan) never loses a fetch and you
can re-open any channel instantly without re-scanning Telegram.

## Features

### Per-channel dashboard

Stat cards (members, posts, posts/day, avg views, **ERR%**, **ERV%**,
**Virality index**, avg reposts/reactions, **Ethics** — the channel's cached
mention-fairness score, or a **Calculate** button if it hasn't been scored
yet, see [Mentions fairness (Ethics)](#mentions-fairness-ethics)), a wide activity
trend chart with toggleable Views / Reactions / Shares / Posts / **Quality**
lines (month or season buckets — past a 2-year displayed range, "Aug '25" /
"Fall 2025" style labels would start crowding the axis, so they switch to a
compact "25/8" (month) or "25/F" / "25/W" / "25/P" / "25/S" (season —
Fall/Winter/sPring/Summer) form instead), by-hour and by-weekday bar
charts, a "Last 50 Posts" card row with per-post content-quality gauges,
and a sortable
top-posts table. Click a post to open it in Telegram; click a reposts cell
to see who re-shared it. Export the report as Markdown or copy a plain-text
link list.

### Multi-channel comparison

Three sidebar buttons, sharing one channel multi-select (switching between
them carries the current selection over rather than resetting it):

- **Metrics** — pick 2–8 channels from the sidebar and see their
  stat cards laid out one column per channel for an easy diff, exportable as
  a Markdown table.
- **Charts** — pick up to 8 channels and overlay them on five
  stacked trend charts (Quality, Views, Shares, Reactions, Posts), sharing
  one month/season toggle.
- **Mentions** — pick up to 4 channels to compare post texts and the person
  names mentioned in them (Russian NER via
  [mawo-slovnet](https://github.com/mawo-ru/mawo-slovnet), backstopped by a
  plain dictionary scan for names already in `mentions.md` that the model's
  most common miss — a bare first name in a short, casual sentence —
  otherwise wouldn't catch; see the Dependencies list below for the
  alternatives that were tried and why they were reverted), one shared
  **Mentions Period:** filter across all columns plus a **Reload** button
  (same row, right-aligned) that re-reads `mentions.md` from disk on demand
  — useful after hand-editing the file while the view's already open;
  prompts first if there are unsaved edits, since a reload discards them.
  `mentions.md` is also reloaded automatically whenever channels are
  (re)opened here (skipped while there are unsaved edits), so found/not-found
  status can't go stale across a session. With 2+ channels loaded, a
  **Similar mentions** summary sits in that same row (right of the period
  picker) — exact-text name overlap between every pair of loaded columns
  (1-based position, e.g. "1↔2: 4, 2↔3: 2, 1↔4: 1"), most-overlapping pair
  first, a quick "these two cover the same people" signal before reading
  four columns of text (hover it for which position is which channel). Each
  column's title names the channel and the full post date range already
  stored for it (e.g. "posts 2019-08 — 2026-09"), and below that a sortable
  table (click a header to sort by **ID** — the post id, a bit wider than a
  bare row number and actually useful — **Date** or **Type** — the post's
  media makeup, e.g. "Photos x9, Video x1" or "Circle", plus a **Link…**
  button on its own line for any post with an extracted name (a small menu
  to pick which, if it has more than one) that opens the same
  confirm/correct-then-attach flow as the staging table below, without
  having to scroll down to it — or **Text**) lists its posts (reposts
  included — a repost's own text is shown first-line-prefixed "Forwarded
  from `<source>`", green if that source is already in `mentions.md`,
  resolved against this app's own tracked channels with no extra Telegram
  API call; needs a re-fetch on channels scanned before this existed), with
  every extracted name highlighted inline — and, when that exact name is
  also the post's own link anchor text (a "text link," e.g. a model's name
  hyperlinked to her own channel — see **Refresh mentions** above),
  clickable straight to that link's target: green if the link is already
  sitting in some `mentions.md` row's unclear links (a strong hint of
  which person this is even when the bare name alone is ambiguous, e.g. a
  first name with no surname), the usual highlight color otherwise, still
  clickable either way. Double-click a row to open that post in Telegram.
  Every post's text is also checked against `name_exceptions.txt` (see
  Dependencies below) so a known non-name (e.g. "Мастер-класс") never
  shows up as extracted at all, even before anyone's clicked **Ignore** on
  it. Just above the texts table, a "**Posts N/Total, Telegram links T, Web
  links W, Most repeat (C): `t.me/…`**" line summarizes the column's own
  links — "Total" is the channel's true post count for the period (from its
  full monthly distributions, not just the texts table's row count, which
  is capped at whatever's in the stored top-N sample — a channel with
  thousands of posts might only have a few dozen sampled with full text);
  "Telegram links" vs "Web links" splits by host (`t.me`/`telegram.me`/
  `telegram.org` vs everything else); "Most repeat" is the single most-
  recurring link across the column's posts, shown without its scheme and
  clickable straight to it. Below the texts table, a "**Posts N/Total, Names
  Found: T, in mentions.md M**" staging table (same N/Total fix, newest
  mention first, Name column twice the default width for a full Cyrillic
  ФИО) lists the names
  found — whether each is already in `mentions.md` (green check) or not (a
  **Link…** link, styled like a small button and bolder/green when that name
  is also this post's own Telegram-link anchor text (see **Refresh
  mentions** above), a higher-confidence "this really is a person/channel"
  signal than plain text extraction; creating a new mentions.md row for one
  of these suggests the link's own `@username` as the id instead of the
  extracted name text, e.g. a "Марго" hyperlinked to `t.me/gotomargosha`
  suggests `@gotomargosha` — or an **Ignore** link right next to it for a
  plain extraction mistake — adds to `name_exceptions.txt` and re-extracts
  every loaded column immediately; both live in one word-wrapped cell, so
  they wrap onto a second line rather than getting squeezed together when
  the column's too narrow for both) — and the post ids it came from, each
  its own clickable link in that same word-wrapped cell (wrapping onto more
  lines rather than getting clipped off the edge when a name has been
  mentioned in many posts) that shows the post's cached thumbnail on hover
  if one's been fetched. "Already in
  mentions.md" is matched three ways, most
  confident first: exactly; as a whole-word run — e.g. mentions.md's "Лина
  Жу" auto-matches an extraction of "Мастер-класс Лина Жу", NER's
  occasional habit of grabbing extra text around a real name
  (word-boundary-matched, so a short name like "Иван" can't spuriously
  match *inside* an unrelated word like the surname "Иванов"); or as a
  plausible Russian case variant via `pymorphy3` (see Dependencies below) —
  mentions.md's "Алиса" also matches an extraction of "Алисой" or "Алисы".
  Word boundaries are Unicode letters only, so a decorative emoji glued
  straight onto a word with no space ("Курилко🔥", a common casual-writing
  style) doesn't stop "Елизавета Курилко" from matching.
  Below the Names Found table, a small "**Summary**" stats table sums up how
  trustworthy those names are: **Fair mentions** (a name whose own link
  anchor text is a *Telegram* link already resolved to a `mentions.md` row
  under a *different* id — e.g. "Марго" hyperlinked to a link `mentions.md`
  already files under `@gotomargosha` — the link is what proves the
  identity, not the bare text), **Fake mentions** (a name whose own link
  anchor points to a non-Telegram/web resource instead — not actually a
  Telegram identity), **Force link** (the single most-repeated
  name-anchored link, clickable), **Mentions fairness** (fair ÷ (fair +
  fake), as a percentage), **All unique links** (every distinct url in the
  column's scope, not just name-anchored ones), and **Balance tg / web
  links** (what fraction of those unique links are Telegram vs. everything
  else, e.g. "52%/48%"). Two more rows open a separate window instead of a
  bare number: **Link report** lists every name-anchored link in scope as
  "`<count>: <url>`", one per line, for copy-pasting elsewhere; **Unresolved
  fair links** tables every Telegram link that's a name's own anchor text
  but isn't in `mentions.md` yet — exactly what would turn into a "fair"
  mention once linked — each row with its own **Link…** button running the
  same confirm/attach flow as the main tables, so they can be resolved
  without hunting the name back down
  in the texts table above.
  Below the columns, the
  `mentions.md` table itself (`id | names | unclear links`, at least twice
  the height of the per-column tables above and growing to fit every row —
  no internal scrollbar of its own to fight with the page's) is directly
  editable and word-wraps long cells (growing the row instead of clipping)
  — **id** is 240px, **names** is kept at 45% of the table's width,
  **unclear links** stretches to fill the rest — a **Show in folder** button next to its
  title reveals the file in Finder/Explorer, and a **Save** button (enabled
  only while there are unsaved edits) plus autosave on leaving the view
  keep it persisted.

### Folders & Tags view (`📁`)

The **Folders** and **Tags** cards sit at the top:

- **Folders** — user-defined colored groups, managed in-app. Assign channels
  from the sidebar's right-click menu, the dashboard's folder button, or the
  "assign every channel to a folder" bulk action here. The sidebar can group
  and sort by folder. Also here: a **Markdown export** (one row per channel,
  optionally with per-period Rating / Views / Viral share, plus an **Ethics**
  column; the Rating carries a [year-over-year activity-decline
  penalty](#activity-trend-abandonment)), with a **Calculate Ethics**
  checkbox (folder selector + an **Only
  with Tags** filter) that scores any not-yet-cached channel in scope first —
  see [Mentions fairness (Ethics)](#mentions-fairness-ethics).
- **Tags** — a lightweight one-tag-per-channel taxonomy loaded from a
  Markdown table (`| tag | long tag | description |`); edit the source `.md`
  and reload to change the tag set. Only the per-channel assignment is
  app-owned. A shared tag is the niche signal behind MPR Pairs (folders
  count for much less there).
- **Mentions** — pick a folder (or all channels) and export every one of
  their **Link report** / **Mentions report** tables (the same ones the
  Mentions view's own popups show) into one `link_report_all.md` /
  `mentions_report_all.md`, each channel under its own `## <title>` heading.

Below that, for one folder and one period (monthly / seasonal / half-year /
rolling-year window): **periodic stats** — per-channel views / shares /
reactions / viral-share, the period's most-viewed post, **Post Quality**,
and a composite **Rating** (see [rating.py](#composite-channel-rating)),
exportable as Markdown. (The cross-channel table that used to live here
moved to the Mutual PR view's own Channel links card, and moved again from
reposts to a richer link-based signal — see
[Cross-channel mentions](#cross-channel-mentions).)

### High-Quality Posts view (`🎯`)

For one folder and period, a grid of individual **posts** (not channels)
ranked by content quality — proportional engagement relative to that post's
own reach, not raw view count (see
[scoring.py](#per-post-content-quality)). Filters for minimum
followers, hiding text-only posts, and per-channel caps. On a Top 50-200
slate a follower-scaled per-channel cap balances the list, and any post
with *both* fewer than 4,000 views *and* fewer than 11 shares is dropped
(either alone is still enough to keep it). At Top 250 specifically, the
rule flips stricter: a post needs **both** 3,000+ views **and** 30+ shares
to survive at all — that much bigger a slate pulls in a much longer tail,
and a real share count is what actually proves a post wasn't just a
view-count fluke, so a high view count alone no longer excuses a weak
share count there (`_quality_floor` in `app/ui/content_quality_view.py`).
The per-channel-limit
dropdown's **"Rein in dominant channel"** option (between *No limit* and
*7 per channel*) instead applies an anomaly cap — no fixed limit, but any
single channel holding more than ~11 slots and 3× the typical channel's
share is trimmed to 11, so one prolific channel can't crowd out the variety.
Optional on-demand
**thumbnail fetch** (downloads only the smallest preview image for the posts
on screen into a local cache — nothing else in the app downloads media).
Export as a Markdown table or a copyable Tg-links list with a "top authors"
summary.

### Mutual PR (ad-swap) view (`🤝`)

Above the main table sits a **Channel links** card: which tracked channels
already link to which *other tracked channels*, and how often — see
[Cross-channel mentions](#cross-channel-mentions). A
**Calculate**/**Recalculate** button (re)scans the whole tracked base;
cached, so opening the view just paints the last result. Sorted by
**Mentions** by default (click a header to re-sort) — which channel is
mentioned more sits right at the top, since who already promotes a channel
is exactly the signal a swap decision should see first. Double-click a
**Channel** or **Top source** cell to open that channel in Telegram.

Every tracked channel in one sortable table: followers, an estimated
**ad-post follower-gain forecast** at five horizons (24h / 48h / 72h / week /
month), a "repeated after a month" estimate for a reminder post, and the
least-crowded weekdays to slot an ad in (each with a "how much better than an
average day" rate). Meant to help decide which channels are worth trading ad
posts with. All figures beyond Followers are heuristic estimates — see
[scoring_pr.py](#mutual-pr-ad-swap-forecast) — and the view
says so in the UI.

Below the table sits one more card, **MPR Pairs** — channel pairs ranked for
an ad swap by size / engagement / timing / niche compatibility, excluding
any pair the Channel links card above already shows as connected and
capped at 16 of any one channel's own best pairs (see
[scoring_pr.py](#mutual-pr-partner-matching)), with a
**Best posting days** column showing each side's best days plus `★` for the
days that suit both at once. The **Markdown export** keeps
the main forecast table intact and appends just the MPR Pairs table
(`## Пары ВП`) — neither Channel links nor its own export button feed it.

### Login, profiles, connection settings

- **Two login flows** — QR code (scan from Telegram → Settings → Devices) or
  the classic phone number + SMS code, with 2FA-password support. The session
  is saved, so later fetches need no re-login.
- **Named profiles** — keep several accounts/API keys side by side.
- **`.env` import/export** of connection settings (also understands
  `TG_API_ID` / `TG_API_HASH` / `TG_PHONE` naming).
- **Refresh mentions** — a folder-wide action (Config screen's Lean refresh
  card, same row shape as **Refresh comments** below, sitting just above
  it) that re-reads just the links for every already-textual stored post
  (added after some checkpoints were fetched, or for a channel whose links
  have otherwise gone stale) without a full re-scan — posts with no text at
  all are skipped, since they can't carry a link either. A link is a plain
  URL typed into the caption, a "text link" (hyperlinked display text whose
  actual target is otherwise invisible — Telegram never puts it in the
  plain message text), or a bare `@username` mention with no hyperlink at
  all (Telegram still auto-detects these as their own entity type — a post
  crediting a collaborator as plain "@some_channel" text, with no actual
  link, is captured as one too) — all three come from the fetch itself now,
  not from eyeballing `full_text`.
- **Refresh comments** — a folder-wide action (on the Config screen's Lean
  refresh card) that re-reads just the comment count for stored posts (added
  after some checkpoints were fetched) without a full re-scan.
- **Lean refresh** — the dashboard's **Refresh** button, and a Config-screen
  card with a staleness list of every tracked channel (age, name, folder and
  followers; click a column header to re-sort, defaults to oldest fetch
  first) plus one-click batch buttons: **Oldest 10**, **1 mo+**, **3 mo+**,
  and **Refresh selected** for the rows you tick in the list. It's
  *incremental* — only the months since a channel
  was last fetched are re-scanned and merged into the existing checkpoint,
  so a monthly cadence re-reads ~1 month instead of the whole stored 2–3
  year period. (A checkpoint too old to merge falls back to one full scan.)
  **Re-fetch selected · 2 y** does a full (non-incremental) 2-year re-scan
  of the ticked channels — the way to rebuild older history against per-post
  fields added since the last fetch (reposts, ad buttons, comments).
  **Refetch mentions** is the same ticked-row targeting, but runs the
  **Refresh mentions** job above instead — links only, skipping posts with
  no text.

### UI

- **Bilingual** — English and Russian, switchable at runtime with no widget
  rebuild (a running fetch and unsaved fields survive the switch).
- **Light / Dark theme** — follows the OS appearance by default (and updates
  live if you flip it), or pin System / Light / Dark from the **Theme** menu
  or the picker on the Config screen.
- Telegram work runs on a background thread, so the GUI never freezes; scans
  are cancellable. Rate limits (FloodWait) and transient network errors are
  retried automatically with backoff.
- Charts are drawn natively with QPainter — no matplotlib or QtCharts.

## Stats & scoring algorithms

Three modules hold the formulas, kept separate from the views so multiple
views can share one implementation. Each module's own docstring carries the
full rationale and the tuning history; this is the summary.

### Channel-level metrics

Computed once per fetch by `app/tools/channel_stat.py` and stored in the
checkpoint's `stats`:

| Metric | Formula | Notes |
| --- | --- | --- |
| **ERR%** (engagement rate by reach) | `avg_views_settled / members × 100` | `avg_views_settled` averages only posts older than 14 days, whose view count has settled. |
| **ERV%** (engagement rate by views) | `(avg_reactions + avg_reposts) / avg_views × 100` | Shown on the dashboard and Compare views. |
| **Virality index** | `max_views / avg_views` | Spread between the best post and the average — how often the channel lands an outlier hit. |
| **Viral post share** | `% of posts with views > 2 ×` the **trailing-3-month** avg views/post, capped per-month at 50% and per-period at 40% | Trailing baseline, not the channel's lifetime `avg_views` (`_viral_baseline`, `VIRAL_BASELINE_MONTHS`). Against the lifetime figure a channel that has merely *grown* reads ~90% viral; against its own month the growth vanishes. A trailing window sits between: a real growth spurt shows elevated virality for a quarter or so, then settles. Thin windows (< 6 prior posts) fall back to the lifetime average; at most `VIRAL_MONTHLY_CAP_FRAC` (50%) of a month can be "viral"; and the figure shown in Folder Stats / the export is clamped to `VIRAL_SHARE_DISPLAY_CAP` (40%) — that also caps a stale checkpoint (fetched before this fix) without waiting for a re-fetch. Feeds the Rating (which saturates virality at 15% anyway) and Mutual PR. |
| **Avg reposts (trimmed)** | mean after dropping the top 10% of posts by repost count | Reposts are far more top-heavy than views/reactions, so the plain average swings on one-off spikes. |

### Per-post content quality

`app/scoring.py` ranks individual posts by *proportional* engagement — how much a post
punched above its own reach — not by raw views. Shared by the High-Quality
Posts grid, the dashboard's Quality trend line and post cards, and the
Folders & Tags "Post Quality" column; it also feeds the Rating's quality term
and Mutual PR's "Interest".

Per post, given the post's `views`, `reactions`, `forwards`, `comments`, and
its channel's `avg_views`:

1. A post with an **inline keyboard** (`has_buttons`), or one **forwarded in
   from another channel** (`repost`), scores **0** outright — for the first,
   an ad/CTA button is what's driving engagement, not the content; for the
   second, the views and reactions were earned by the original author, not
   by the channel reposting it.
2. `comments` are capped at 100 (past that a post clearly has real
   discussion either way).
3. `reaction_wt` is tapered in two brackets: the first 1 000 reactions count
   at 0.045 each, 1 000–10 000 at only 0.005 each, beyond 10 000 nothing more
   is added (reaction counts can run anomalously high relative to a post's
   own views).
4. `viral_excess = max(0, views − avg)`, **capped at `VIRAL_EXCESS_CAP_MULT`
   (1×) `avg`** — a bonus for beating the channel's usual reach, weighted
   0.2. Two guards keep it from becoming "score = view multiple":
   - **`avg` is `stats.avg_views_recent`** (trailing ~3 months) where the
     checkpoint has it, not the lifetime average — otherwise a channel that
     merely *grew* has every recent post beating its frozen lifetime figure,
     and the whole grid reads a flat ~700.
   - **the cap**: past ~2× `avg` the post has gone viral (a channel-level
     signal), not shown better craft — the term peaks (~10% of ERV) around
     2× and then fades.

   Used by the High-Quality Posts grid and the dashboard; the composite
   Rating passes `viral_excess=False` (it already has views + virality terms).
5. `ERV% = (forwards×1.0 + comments×0.25 + reaction_wt + viral_excess×0.2) / views × 100`
6. `raw = ERV% × 100`
7. `gauge = 1000 × raw / (raw + 580)` — a saturating curve onto the 0–1000
   gauge (K = 580 ≈ this app's real per-post median raw score, so a typical
   post lands mid-gauge; a hard clamp would flatten most real posts at the
   ceiling).

Numerator terms are ordered by how much each really signals quality:
forwards (a deliberate, costly share) first, then comments, then reactions
(cheapest, easiest to pump).

### Composite channel Rating

`app/rating.py` produces a per-channel-per-period score in `[0, 1]`, shown in the Folders & Tags view's
"Rating" column and reused verbatim by that view's Folders MD export. Channels
are grouped by folder and each channel is normalized against **only its own
folder's peers** for the same period.

**Reposts are excluded from every term.** A post the channel forwarded in
from another channel (`repost`, see `_is_repost` in
[channel_stat.py](#stats--scoring-algorithms)) is dropped from the period's
`views` / `shares` / `reactions` / `viral_share` / `posts` totals and from
the quality median — so a channel can't lift its Rating by reposting a
bigger channel's viral hit. (Channel-level stat cards, ERR%/ERV% and Mutual
PR still count reposts.)

**Repost-heavy penalty.** On top of that exclusion, a channel whose feed
*is* largely reposts takes a smooth flat cut to the composite score: nothing
while reposts are ≤ 20% of the period's posts, ramping linearly to a 30% cut
by the time they hit 30%, and held there above that (`repost_share_penalty`,
constants `REPOST_SHARE_PENALTY_START` / `_FULL` / `_MAX`). Coasting on other
people's posts is less original work than the post count suggests.

**Activity-decline penalty.** When the caller supplies an entry's
`activity_trend` (the [year-over-year verdict](#activity-trend-abandonment)),
`activity_trend_penalty` cuts the composite by a flat fraction —
`ACTIVITY_TREND_PENALTY`: 20% `slowing`, 33% `stalling`, 50% `abandoned`,
nothing for `active` or too-little-history — applied after the reach bonus.
The Folders card's Markdown export passes it; Folder Stats (period-scoped
buckets, where "abandoned now" doesn't belong on a 2024 quarter) does not.

For each channel entry in a period bucket (`views`, `shares`, `reactions`,
`posts`, `quality`, `viral_share`):

- **views/post** — `views ÷ posts` (mean views **per post** — reach
  *efficiency*), min-max normalized within the bucket, weight `0.42`.
  Per-post, not the period total, so a channel can't climb the Rating just by
  posting 3× as often.
- **total reach** — `log10` of the absolute period `views`, **capped** at
  `(views ÷ posts) × the folder's median post count`, min-max normalized,
  weight `0.42`. The counterweight to views/post: a small channel with a
  lucky per-post average still loses here to a genuine heavyweight — but the
  cap means a flood of low-value posts past the folder's normal cadence buys
  no extra rating, and `log10` keeps one giant from flattening everyone else.
- **engagement** — `(shares + 0.05 × reactions) ÷ posts`, also per-post,
  min-max normalized, weight `0.65`. Reactions count for only 5% of raw value
  because reaction counts are the easier of the two to artificially pump; a
  repost is a costlier, harder-to-fake action.
- **quality** — the per-post gauge above but with the *viral-excess* term
  dropped (reach is already the views and virality terms — no triple count),
  taken as the **median** over the channel's stored top-N pool for the period
  (median, not mean: the pool skews toward a channel's best posts), min-max
  normalized, weight `0.45`. (The Rating consumes this; the readable
  **Post Quality** column shown in the view/export is a *separate* figure —
  the standard 0–1000 gauge, viral-excess included, mean — so tuning the
  Rating never turns the displayed number into single digits.)
- **forward rate** — `shares ÷ views` (forwards per view — how often a viewer
  thought the post worth re-sharing, size-independent), min-max normalized,
  weight `0.40`. Separates "big loyal audience that taps a reaction" from
  "content people actually spread".
- **virality** — an *absolute* (not normalized) curve over `viral_share`:
  0% → 0, 1% → 0.05, ramping to 1.0 at 15%+. Its weight itself ramps from
  `0.10` to `0.51` as `viral_share` climbs to 15%, so virality matters more
  to a channel that actually is viral.

`views ÷ posts` and `engagement ÷ posts` divide by `max(posts, 8)`
(`RATE_MIN_POSTS`), so a channel with a handful of posts in the period can't
top the bucket on the "average" of one lucky hit.

`score = (weighted sum of the terms) / (sum of the actual weights)` — that
division is the one and only normalization step.

**Reach bonus.** On top of the capped total-reach term, a small flat lift
(`reach_bonus`) is *added* for a channel whose period `views` run *well*
above the folder's median: nothing up to `REACH_BONUS_START` (4×) the median,
ramping to `REACH_BONUS_MAX` (0.15) by `REACH_BONUS_FULL` (12×). Additive,
applied **last** (after the penalties below), capped at 1.0 — it only nudges
the very top of the folder and never reshuffles the rest.

**Confidence dampener:** the final score is scaled down linearly once the
period post count drops below `CONFIDENCE_MIN_POSTS` (12; 4 posts → ×0.33) —
a rating built off 3 posts, one of which happened to land, is noise. (The
"small channel with a lucky per-post average" case is handled by the capped
total-reach term above, not here.)

**Zero-reposts red flag:** a channel with ~0 reposts in the period has its
`viral_share` cut by 40% *before* the virality curve, and the whole
composite score cut by another 40% flat — apparent virality nobody actually
shared is a warning sign the other metrics don't catch.

### Mutual PR ad-swap forecast

Unlike `app/scoring.py` (tuned against this app's own real median),
`app/scoring_pr.py` is built on **documented heuristics, not measurements** — there is no real
ad-campaign outcome data anywhere in the app. The constants exist so a
forecast can be produced at all, and are ordinary module constants
specifically so they can be retuned once real ad-swap outcomes are logged.

- **`AD_VIEW_CURVE`** — assumed fraction of a post's eventual reach landed by
  each horizon: 24h `0.49`, 48h `0.75`, 72h `0.78`, week `0.80`, month
  `1.00`.
- **Reach basis** — `avg_views_settled`, capped at `followers × 1.0`. Views
  beyond a channel's own subscriber base reflect viral/external discovery a
  freshly-placed ad post wouldn't inherit.
- **Follow-conversion rate** —
  `0.036 × (0.5 + weight × interest_gauge/1000)`, where `weight = 0.3 ×
  size_band_factor(followers)`. Content quality ("Interest", the average
  post-quality gauge over the last ~90 days) swings the rate only ~0.5–0.8×;
  that swing is tripled for channels in the 5 000–30 000 follower band,
  where quality reads as a more reliable predictor.
- **Viral boost** — a ≥1 multiplier from `viral_post_share`, applied only
  within the 5K–30K band and only when the reach cap above didn't already
  fire (so virality isn't credited twice).
- **`size_forecast_multiplier`** — a deliberate flat scale-up on top of the
  calibrated base: **×3.0 for channels up to ~6 000 followers, ×1.5 for
  larger** (linear between 6 000 and 7 000), since smaller channels convert
  a borrowed audience into followers far better.
- **`total = reach × rate × boost × size_forecast_multiplier`**, then
  **redistributed** across the
  horizon fractions by posting rarity: a channel posting below the app's
  median (~0.525 posts/day) has its post linger un-buried, so its reach
  arrives more gradually — early-horizon fractions shrink toward later ones
  (month's fraction is always exactly 1.0, so no horizon can ever exceed the
  total). The rarity effect is squared to separate moderate from extreme
  rare-posters and scaled down for channels with fewer than 20 stored posts.
- **`link_behavior_factor`** — ×(up to 1.05) for a channel that consistently
  single-mentions other channels (genuine cross-promotion), ×(down to 0.95)
  for one that consistently posts external-link spam.
- **Activity-decline cut** — `avg_views_settled` is a lifetime average, so a
  channel that's since gone quiet would otherwise still forecast off its
  old reach and rank at the top of this table's default 24h sort. `ad_forecast`'s
  own `activity_trend` argument (app.ui.mutual_pr_view supplies
  [`channel_activity_trend`](#activity-trend-abandonment)'s verdict) cuts the
  *whole* forecast by the same year-over-year penalty a channel's Rating
  already takes — [`activity_trend_penalty`](#composite-channel-rating): 20%
  `slowing`, 33% `stalling`, 50% `abandoned`. A discounted channel's title
  gets a `⚠` marker and a tooltip explaining why.
- **`ad_forecast_range`** — a crude ±band (low `×0.40`, high `×1.80`)
  combining the two dominant unverified constants; honestly labeled, not a
  fitted interval.
- **`repeated_post_forecast`** — a reminder post a month later, decayed by
  how many of the channel's own posts ran in between:
  `retention = 1 / (1 + posts_between / 16)`.
- **`best_days`** — least-crowded weekdays scored
  `(1 − normalized post rate) × interest_gauge`; the displayed "better than
  an average day" rate is damped to 25% of its raw deviation (the ranking
  still uses the undamped score).

### Mutual PR partner matching

`rank_mutual_pr_pairs` (bottom of `app/scoring_pr.py`) scores every *pair*
of channels for how good an ad swap between them would be — the basis for
the **MPR Pairs** card and the `## Пары ВП` section appended to the Mutual
PR Markdown export. The scoring itself uses only per-channel metrics; two
channels that already promote each other are filtered out **upstream**, by
the caller — see `exclude_keys` below, built by
`app.ui.mutual_pr_view._linked_pair_keys` from the [Channel links
card](#cross-channel-mentions)'s own cache, so a real posted link is what
decides "already connected", not anything scored here. The four scoring
components are a plain weighted sum in `[0, 1]`:

| Component | Weight | Formula |
| --- | --- | --- |
| **`size_parity`** | 0.30 | `1 − abs(log10 subs_A − log10 subs_B) / log10(100)`, clamped to `[0, 1]` — 1.0 for equal size, 0 once one channel is 100× the other. Log-scaled, so a 2× gap scores the same at any absolute size — **except** two channels within `MUTUAL_PR_SIZE_WINDOW` (1,000) followers of each other always score a flat 1.0 regardless of ratio, since that log scale is otherwise unkind to small channels (300 vs 1,300 followers is a 4.3× ratio, same as 40k vs 172k, even though they're obviously the same tier). |
| **`quality_parity`** | 0.30 | `1 − abs(f24_A − f24_B) / (f24_A + f24_B)` — how close the two 24h ad-post forecasts are (a proxy for "both convert ad views similarly"). |
| **`day_overlap`** | 0.20 | shared entries in the two channels' top-2 `best_days` over `min(len_A, len_B)` — 1.0 when both top-2 sets match. |
| **`niche_affinity`** | 0.20 | `1.0` if the two channels carry the **same tag** (a real niche match), `0.30` (`MUTUAL_PR_FOLDER_NICHE`) if they only share a **folder**, else `0`. Tag-first on purpose: a folder is just sidebar organization, so a different-folder same-tag pair beats a same-folder unrelated-tag one. |

The MPR Pairs table (UI card and Markdown export) lists **every pair scoring
`MUTUAL_PR_MIN_SCORE` (0.51) or higher, best first, capped at
`MUTUAL_PR_MAX_PAIRS` (500)** — the low floor just keeps the ranked tail
available; the cap is what bounds the table. Two more cuts, both by
default: any pair `exclude_keys` names (dropped before scoring even runs)
never appears at all, and once ranked, `max_per_channel`
(`MUTUAL_PR_MAX_PAIRS_PER_CHANNEL`, 16) keeps only each channel's own
best-scoring pairs, processed best-first (`_cap_pairs_per_channel`) so a
generalist channel that scores well against nearly everyone can't crowd out
pairs that don't involve it — either can be disabled by passing `None`.
Its **Best posting days** column shows each channel's own best days (`A: … ·
B: …`) and prefixes `★` for the days that are a good ad slot in *both*
channels at once — `mutual_best_days`, which takes any weekday above each
channel's own average (not just a strict overlap of their top-2, which
would miss a day ranked #3 for one side but still clearly above its
average). All weights and constants are ordinary module-level values, meant
to be retuned.

### Cross-channel mentions

`app/cross_mentions.py` backs the Mutual PR view's **Channel links** card:
which *tracked* channels already link to which others, straight from real
posted links. It matches against `ChannelStore`'s own roster of tracked
channels — a different target than [Mentions fairness](#mentions-fairness-ethics),
which matches against `mentions.md`'s roster of known *people* — so it
answers Mutual PR's own question instead: who already promotes whom?

That question used to be answered by a much thinner signal — the view's
original cross-channel table counted only Telegram's own reported "public
forward" stat, present solely for a channel fetched with "Include public
reposts" turned on, which most channels never have. This instead scans
every channel's own `all_links` (collected on every fetch/refresh
regardless of that setting) for *any* t.me link a post's caption carries —
a far larger, far more current sample already sitting on disk.

- **Matching**: a link's target identity comes from
  `app.mentions.tg_identity_key` — `@username` (case-folded, so
  `t.me/Geekography` and `t.me/geekography` match) for a public link, or the
  bare internal id straight out of a private `t.me/c/<id>` link, which is
  also exactly `ChannelStore.list()`'s own `channel_id` — so a private link
  matches a tracked channel exactly as reliably as a public one, no extra
  resolution needed. `mentions.md` extends that one step further: a tracked
  channel that's *also* filed as a `mentions.md` row (same id) has that
  row's own "unclear links" registered as extra aliases for it too, so a
  link still using an old handle, or an otherwise-unresolved private link a
  row already accounts for, keeps matching after the live one's moved on.
  Self-links (a channel linking to its own posts) are dropped — not a
  cross-channel signal.
- **Ranking**: `rank_targets` sums every matching edge onto its *target*
  channel — **Mentions** (total link occurrences) and **Mentioned by** (how
  many distinct channels do the linking), most-mentioned target first —
  since "which channel is mentioned more" is a property of the target, not
  any one source→target edge. Each row's **Top source** is whichever
  channel mentions it most, with the rest available on hover.
- **Speed & caching**: pure dict/string matching over already-fetched data —
  no NER, no morphology, no Telegram calls — so a full base-wide rescan
  (`compute_cross_channel_mentions`) runs in under a second even across
  ~200 channels, fast enough to trigger synchronously from the Calculate
  button rather than through a background worker. The result is cached to
  one shared file (`cross_mentions.json`, not per-checkpoint — this is a
  property of the whole tracked set, not any single channel) with a
  `calculated_at` stamp, so opening the Mutual PR view paints instantly
  from whatever was last calculated instead of rescanning the base.

### Mentions fairness (Ethics)

`app/mentions.py`'s `classify_channel_links` walks a channel's *entire*
stored link history (`all_links`, not just the sampled top-N pool) and buckets
every distinct link (case-insensitively grouped, see `canonical_link_key`) by
what it tells you about the person it credits:

| Status | Meaning |
| --- | --- |
| **fair** | a Telegram link whose id is already a *different* row in `mentions.md` — the link itself proves who's being credited. |
| **fake** | a non-Telegram (web) link anchored to a name — counted once per distinct *name* across posts, not once per link, since one repeat web link can credit many different people over time. |
| **unresolved** | a Telegram link that isn't in `mentions.md` yet — would become "fair" the moment it's linked (see the **Unresolved fair links** popup). |
| **promo** | the channel's own repeated self-plug link ("subscribe to us"), excluded from scoring — unless it targets a specific post, which still counts. |

From those counts:

- **`fairness_pct = round(fair / (fair + fake) × 100)`**, `None` if there's
  neither a fair nor a fake link to score at all.
- **No-link mentions** — names `extract_all_names_per_post` found (the same
  3-tier NER / `mentions.md` dictionary / caption-pattern pipeline the Names
  Found table uses) that never once appeared as a link's own anchor text
  anywhere in the channel — a bare narrative credit with nothing to verify it.
  `apply_no_link_penalty(fairness_pct, no_link_count, fair_count)` **halves**
  `fairness_pct` once `no_link_count` exceeds `fair_count` (e.g. 100% → 50%,
  80% → 40%) — a channel that mostly name-drops people without ever linking
  them reads as much less fair, even if every link it *does* use is clean.

`compute_channel_mentions_cache(data, mentions_store, name_exceptions)` runs
this headlessly, all-time (unscoped by period), over one checkpoint, returning
`fairness_pct` alongside `fair` / `fake` / `no_link` counts and the channel's
**All unique links** / **Balance tg / web** figures — or `None` if the
checkpoint has no `all_links` at all (pre-dates that field; needs a re-fetch).
`cache_channel_mentions(data, cache)` stamps it with a `calculated_at`
timestamp and writes it to the checkpoint's own `mentions_cache`, so it
persists across restarts without the Mentions view open at all. Three places
read/write it:

- **Mentions view** — each column's Summary cards call this on every rebuild
  (all-time scope only) and cache the result; opening or switching to a
  column first paints instantly from whatever's already cached
  (`_show_cached_stats`), then gets overwritten moments later once the real,
  period-scoped classification finishes — so a heavy channel doesn't show a
  blank Summary while it recomputes.
- **Dashboard's Ethics card** — shows the cached score (`—` for a channel
  that was scored but has no fair/fake links), with a **Calculate** button
  when it's never been scored and a **Recalculate** button once it has (the
  only way to refresh one channel, since the batch job skips anything
  already done).
- **Folders & Tags → Folders card export** — an optional **Calculate
  Ethics** checkbox (with a folder selector and an **Only with Tags**
  filter) that runs `tools.mentions_export.run_fairness_calculate` before
  writing the Markdown, so its own **Ethics** column has something to show.
  Lean by default: a channel that's already been calculated — keyed on the
  cache's `calculated_at` stamp, so a legitimate `—` result still counts as
  done — is skipped, not recomputed, so re-running this before every export
  is cheap once a folder's been through it once (`force=True` recalculates
  everyone regardless, not currently wired to any button). The log names
  each channel as it starts and reports the result with its elapsed time.
  In the exported table, the **Ethics** and **Trend** columns are always
  whole-history; only Rating / Views / Viral share / Post Quality follow the
  period picker, and a footnote says so whenever a non-"all" period is set.

### Activity trend (abandonment)

`app/activity.py`'s `channel_activity_trend` answers the one thing an
all-time Rating / Views / Post Quality can't — **is this channel still
going, or coasting on a strong past?** A small channel that was busy last
year and has since gone quiet keeps a flattering lifetime score long after
it stops being worth an ad swap.

It reads the checkpoint's gap-filled monthly series
(`distributions.monthly`), drops the still-filling current month, and
compares the **last 12 complete months** against the **12 before them**
(own-posts only, reposts excluded; a partial prior year is annualized so a
~18-month-old channel is still judged fairly):

| Signal | What it compares |
| --- | --- |
| **reach** | total views, recent year vs prior year — the headline: fewer posts *or* fewer views per post both land here. |
| **views** | views per post, same two years — isolates "each post reaches fewer people" from "just posts less often". |
| **cadence** | posts per month, trailing 6 complete months vs the prior 12 — the sharper, more recent read on posting having slowed (e.g. 35/mo → 10/mo). |
| **forwards** | forwards per post, same trailing-6-vs-prior-12 window — reported only, never gates the verdict (one viral month swings it too hard). |

**Verdict** (driven by yearly reach, except abandonment which is about
posting stopping):

- **abandoned** — nothing posted in the trailing 6 months, or cadence down to <15% of before.
- **stalling** — yearly reach under 40% of the prior year.
- **slowing** — yearly reach under 70%.
- **active** — everything else.
- *blank* — under ~16 months of history, or a prior year too thin (<12 posts) to compare.

**Feeds the composite Rating.** `app/rating.py`'s `activity_trend_penalty`
docks a channel's score by a flat fraction per verdict — **20%** `slowing`,
**33%** `stalling`, **50%** `abandoned` — applied after the reach bonus so a
former heavyweight feels it too (`active`, or too little history to judge,
costs nothing). The Folders card's Markdown export passes each channel's
trend into `score_entries` (`_collect_export_metrics`), computed over the
whole monthly history regardless of the export's period picker, so a
winding-down channel ranks below a live one; a footnote in the export
spells the penalty out. Pure local computation from the stored checkpoint;
`channel_activity_trend` is view-agnostic and reusable anywhere a channel's
monthly series is on hand.

## Requirements

- Python 3.12+ (developed on 3.12)
- Telegram API credentials — an **API_ID** and **API_HASH** from
  [my.telegram.org](https://my.telegram.org)

## Installation

Prefer [`run_dev.sh` / `run_dev.bat`](#run-from-source) above — this is the
manual equivalent if you'd rather manage the environment yourself:

```bash
# from the project root
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 main.py                  # Windows: python main.py
```

Dependencies (`requirements.txt`):

- `PySide6>=6.6` — Qt GUI
- `telethon>=1.34` — Telegram client
- `qrcode[pil]>=7.4` — renders the QR-code login image
- `mawo-slovnet>=1.0.7` — Russian person/location/org NER for the Mentions
  view (app/mentions.py); downloads its model on first use, then works
  offline. If it's missing or the download fails, Mentions still shows post
  texts, just without extracted names (see `extraction_available` in
  app/mentions.py) — nothing else in the app depends on it. Its main gap is
  recall on short, casual mentions (a bare first name like "Марина" in a
  sentence with no surname) — `find_known_names_in_text` (see below and
  `pymorphy3`) backstops that for names already in `mentions.md`, without
  pulling in a heavier model. A multilingual BERT model
  ([Babelscape/wikineural-multilingual-ner](https://huggingface.co/Babelscape/wikineural-multilingual-ner)
  via `transformers[torch]`) genuinely fixed that recall gap outright when
  tried — installed and tested side by side, not assumed — but a packaged
  build went from ~90MB to ~726MB over it, so it was reverted; a rule-based
  alternative (genuine `natasha`'s `NamesExtractor`) was also tried and
  reverted after it mistagged ordinary words ("и", "без", "Просто") as
  names on the same test sentences. DeepPavlov never got that far — its
  latest release pins `numpy<1.24`, which has no Python 3.12 wheels
  (confirmed by a failed install, not just its docs).
- `pymorphy3>=2.0` — Russian morphological analysis, used two ways in
  app/mentions.py: `MentionsStore.find_row`'s declension-matching tier
  (mentions.md's "Алиса" matches an extraction of "Алисой"/"Алисы", or
  "Лина Рязанская" matches "Лину Рязанскую" — an adjectival surname,
  correctly lemmatized, which a hand-rolled suffix list — kept as the
  fallback when pymorphy3 isn't installed — can't do), and
  `find_known_names_in_text`'s plain dictionary-scan fallback mentioned
  above. Picked over pymorphy2 (and genuine `natasha`, which uses pymorphy2
  internally) because pymorphy2 imports `pkg_resources`, removed from
  `setuptools>=81`; pymorphy3 doesn't need that pin.

## Building standalone binaries

`build.sh` / `build.bat` package the app into a standalone binary (no Python
install required to run it) via [PyInstaller](https://pyinstaller.org/):

```bash
./build.sh          # macOS / Linux
build.bat           # Windows
```

## Getting API credentials

1. Open [my.telegram.org](https://my.telegram.org) and log in with your phone
   number (Telegram sends the code to your Telegram app).
2. Click **API development tools**.
3. Fill in *App title* and *Short name* (any text), platform *Desktop*, then
   create the application.
4. Copy **App api_id** → `API_ID` and **App api_hash** → `API_HASH`.
5. `PHONE_NUMBER` must be in international format, e.g. `+79001234567`.

## Usage

Launch with [`run_dev.sh` / `run_dev.bat`](#run-from-source) (or `python3
main.py` in an activated venv), then:

1. On the **Config** screen, enter your `API_ID`, `API_HASH` and
   `PHONE_NUMBER` (or import a `.env`), then authorize — QR code is the quick
   path.
2. In **Fetch a channel**, enter a channel by `@username`, `t.me` link, or
   `-100…` ID, choose how many top posts to keep per metric and the period of
   analysis, and (optionally) enable public-repost lookup.
3. Click **Fetch & analyze**. The channel appears in the sidebar with its
   dashboard; **Refresh** for a lean (incremental) update, **Export** to
   save a Markdown report, **Remove** to drop it.
4. Open **Folders & Tags** from the sidebar to group channels, then use the
   **High-Quality Posts** or **Mutual PR** views for folder-wide analysis.

**Which channels can I analyze?** Any public channel by `@username`, or a
private one you're a member of by its `-100…` ID or `t.me` link.

## Where data is stored

Config, sessions, checkpoints, caches and logs live in the OS-standard
per-user locations under an app folder (`TgChannelStat`). Open **File → Open
config folder** to jump straight there.

| Data | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Config, sessions, checkpoints, folders, tags, mentions, media cache | `~/Library/Application Support/TgChannelStat` | `%APPDATA%\TgChannelStat` | `$XDG_CONFIG_HOME` or `~/.config/TgChannelStat` |
| Logs | `~/Library/Logs/TgChannelStat` | `%LOCALAPPDATA%\TgChannelStat\logs` | `$XDG_STATE_HOME` or `~/.local/state/tgchannelstat` |

- **`config.json`** — language, theme, profiles, and connection fields.
- **Sessions** (`sessions/`) — Telethon session files (one per profile).
- **Checkpoints** (`checkpoints/<channel>.json`) — the per-channel fetch
  results shown in the sidebar. `@Name`, `Name`, and the `-100…` ID all map
  to the same checkpoint.
- **`folders.json`** — folder definitions and channel→folder assignments.
- **`tags.json`** — the loaded tag list, its source `.md` path, and
  channel→tag assignments.
- **`mentions.md`** — the Mentions view's `id | names | unclear links`
  table; unlike `tags.json`'s source file, the app both reads *and writes*
  this one directly (see app/mentions.py).
- **`cross_mentions.json`** — the Mutual PR view's Channel links card cache
  (which tracked channels link to which others — see
  [Cross-channel mentions](#cross-channel-mentions)), one shared file for
  the whole tracked base, written whenever its Calculate/Recalculate button
  runs.
- **`name_exceptions.txt`** — plain text, one entry per line, of text the
  Mentions view's NER extraction should never treat as a person name (built
  in: "Мастер-класс"). Grows via the Names Found table's **Ignore** button;
  can also be hand-edited (reloaded next time a channel's opened there).
- **`media/`** — cached post thumbnails downloaded on demand by the
  High-Quality Posts / dashboard "Fetch media" button.
- **Logs** rotate at ~2 MB, keeping 5 backups.

## Project layout

```
main.py                     # entry point: logging, theme, main window
run_dev.sh / run_dev.bat    # create venv, install deps, launch (macOS·Linux / Windows)
build.py                    # PyInstaller packaging, invoked by build.sh/build.bat
build.sh / build.bat        # create venv, install build deps, package a binary
requirements.txt
requirements-build.txt      # PyInstaller, only needed to package binaries
app/
├── version.py              # app version string (CalVer: YY.M.D)
├── config.py               # JSON config: profiles + .env import/export
├── store.py                # per-channel JSON checkpoint store
├── folders.py              # folder definitions + channel assignments
├── tags.py                 # tag taxonomy loaded from a Markdown table
├── mentions.py              # mentions.md store (app-owned, live-edited) + NER extraction
├── cross_mentions.py       # cross-channel link matching (Mutual PR's Channel links card)
├── activity.py             # year-over-year channel activity trend
├── periods.py              # month/season/rolling-year period-key helpers
├── media_cache.py          # on-disk thumbnail cache paths
├── scoring.py              # per-post content-quality formula (shared)
├── rating.py               # composite per-channel-per-period Rating (shared)
├── scoring_pr.py           # Mutual PR ad-swap forecast heuristics
├── worker.py               # QThread workers: login flows + tool runs
├── i18n.py                 # English / Russian strings
├── text_utils.py           # small string helpers
├── errors.py               # friendly OS-error messages
├── tools/
│   ├── channel_stat.py     # the scan: engagement ranking + activity stats
│   ├── comments_refresh.py # re-read just the comment count for stored posts
│   ├── mentions_refresh.py # re-read just the links for already-textual stored posts
│   ├── lean_refresh.py     # incremental re-scan of the months since last fetch
│   ├── media_fetch.py      # on-demand post-thumbnail download
│   └── common.py           # entity resolution, FloodWait retries
└── ui/
    ├── main_window.py         # sidebar + stacked content views
    ├── config_view.py         # credentials, profiles, login, fetch form, folder MD export
    ├── dashboard_view.py      # stat cards, charts, post cards, top-posts table, export
    ├── compare/
    │   ├── compare_view.py        # side-by-side stat cards for 2-8 channels
    │   ├── compare_charts_view.py # overlaid trend charts for up to 8 channels
    │   └── mentions_view.py       # post-text / mentioned-names comparison for up to 4 + mentions.md editor
    ├── folder_stat_view.py    # Folders & Tags: hosts the folder/tag cards + periodic stats + Rating
    ├── content_quality_view.py # High-Quality Posts grid
    ├── mutual_pr_view.py      # ad-swap follower-gain forecast table
    ├── folder_dialog.py       # folder manager dialog
    ├── side_panel.py          # Config + fetched-channels list, compare modes
    ├── charts.py              # native QPainter chart widgets
    ├── qr_login_dialog.py     # QR-code login dialog
    ├── widgets.py             # shared card widgets (StatCard, PostCard, gauges…)
    └── theme.py               # QSS stylesheet + palette
assets/svgs/                # UI icons
```

## Notes & limitations

- **Folder-level views read stored checkpoints, not fresh Telegram data.**
  Per-period view/share/reaction totals come from every scanned post and are
  accurate; the reposts-between-channels table and per-post quality rely on
  the stored top-N sample, so they're only as complete as the top-N and
  "include public reposts" choices made when each channel was fetched.
- **New per-post fields** (`comments`, `media_type`, `has_buttons`, `repost`)
  are only present on checkpoints fetched after they were added — older
  checkpoints show 0 comments, a text-only placeholder icon, and no
  ad-button or repost exclusion until refetched (or, for comments, until
  "Refresh comments" is run).
- **Mutual PR forecasts are heuristics, not measurements** — see
  [scoring_pr.py](#mutual-pr-ad-swap-forecast). Treat the
  numbers as rough order-of-magnitude guidance.
- **Private channels typed as bare numeric IDs**: Telethon can only resolve a
  peer it has an `access_hash` for. If a `-100…` ID isn't found, the app falls
  back to scanning your dialogs — so being a member of the channel is what
  makes it resolvable.
- **Public reposts** require a channel you have statistics access to; where
  unavailable, that column is simply left blank.
- This tool only reads data your own account can already see; it does nothing
  a normal Telegram client couldn't.
