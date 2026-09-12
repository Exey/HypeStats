"""Composite per-channel-per-period Rating — shared by the Folder Stats view
(app.ui.folder_stat_view, which computes it per period bucket within one
folder) and the Config screen's Folders MD export (app.ui.config_view, which
reuses it so a channel's exported Rating matches what Folder Stats would show
for the same folder/period). Kept here, not in either view, so neither has to
import UI code from the other to reuse it.

score_entries() takes one period bucket's entries — one dict per channel,
with `views`, `shares`, `reactions`, `posts` (all period *totals* /
counts), `quality` (see app.scoring) and `viral_share` already filled in
(and, optionally, `activity_trend` — see the decline penalty below) — and
adds a `score` key to each, in [0, 1]:

    views/post  = views ÷ posts (mean views *per post*), min-max normalized
                 against the other entries in the same bucket, weighted
                 VIEWS_PER_POST_WEIGHT. Per-post, not the period total, so a
                 channel can't climb the rating just by posting 3× as often.
    total reach = log10 of the absolute period views — but *capped* at
                 (views ÷ posts) × the folder's median post count, so a
                 channel gets credit for the audience it actually delivers
                 without a flood of low-value posts past the folder's normal
                 cadence buying any more. Min-max normalized, weighted
                 TOTAL_REACH_WEIGHT. This is the counterweight to views/post:
                 a small channel with a lucky per-post average still loses
                 here to a genuine heavyweight, and log-scale keeps one
                 giant from compressing everyone else to zero.
    engagement = (shares + REACTIONS_ENGAGEMENT_WEIGHT × reactions) ÷ posts,
                 min-max normalized the same way, weighted ENGAGEMENT_WEIGHT
                 — also per-post. Reactions count for only 5% of their raw
                 value (vs. shares in full) because reaction counts are the
                 easier of the two to artificially pump; a repost is a more
                 costly, harder to fake action.
    quality    = per-post gauge score (app.scoring, `viral_excess=False` —
                 reach is already the views + virality terms), averaged
                 per channel per period as a *median* (the stored top-N row
                 pool is skewed toward the channel's best posts, so a mean
                 runs high; the median is steadier), min-max normalized,
                 weighted QUALITY_WEIGHT.
    forward_rate = shares ÷ views (forwards per view — how often a viewer
                 thought the post worth re-sharing, size-independent),
                 min-max normalized, weighted FORWARD_RATE_WEIGHT. Separates
                 "big loyal audience that taps a reaction" from "content
                 people actually spread".
    virality   = an absolute (not normalized) curve over viral_share — see
                 virality_component() — weighted by virality_weight(viral_
                 share), which itself ramps from VIRALITY_WEIGHT_MIN to
                 VIRALITY_WEIGHT_MAX as viral_share climbs to 15%, so a
                 highly viral channel isn't just scored well on virality,
                 virality also matters more to its rating

`views ÷ posts` and `engagement ÷ posts` divide by max(posts, RATE_MIN_POSTS)
so a channel with only a handful of posts in the period can't post one lucky
hit and top the bucket on an "average" of n=2.

The *_WEIGHT constants are relative, not a budget that has to sum to 1 — the
composite is their weighted average, always divided by their actual total
(VIEWS_PER_POST_WEIGHT + TOTAL_REACH_WEIGHT + ENGAGEMENT_WEIGHT +
QUALITY_WEIGHT + FORWARD_RATE_WEIGHT + virality_weight) for that entry. That
division is the one and only
normalization step, not a conditional safety net for an edge case — so each
constant's *relative* size is a direct, honest read of how much it swings the
score at every viral_share level, with no hidden threshold past which some
other weight silently stops being honored. (An earlier version instead gave
views only whatever budget happened to be left over after fixing the other
three to sum near 1 — which went negative, and floored at 0, for most
realistic viral_share values once those three were tuned up; a floor-at-0
"safety net" that fires on almost every entry isn't one. Giving views its own
explicit weight instead removes that failure mode entirely, not just patches
its symptom.)

On top of the capped "total reach" term, `reach_bonus()` adds a small flat
lift for a channel whose period `views` run *well* above the folder's median
channel: nothing up to REACH_BONUS_START× the median, ramping to
REACH_BONUS_MAX by REACH_BONUS_FULL×. Additive, applied last (after the
penalties below), capped at 1.0 — it only nudges the very top of the folder
and never reshuffles the channels under the threshold.

Finally, `_confidence()` scales the whole score down linearly once the
period post count drops below CONFIDENCE_MIN_POSTS — a rating built off 3
posts, one of which happened to land, is noise, not a ranking a near-dormant
channel should ride. (The "small channel with lucky per-post numbers" problem
is handled structurally by the capped total-reach term above, not here.)

A channel with ~0 reposts in the period is a red flag views/reactions/
quality alone don't catch — it suggests whatever views or apparent virality
the channel shows weren't corroborated by anyone actually sharing the
content. So its viral_share is first cut by ZERO_REPOSTS_PENALTY *before*
the virality curve above (denying it credit for an apparently-viral post),
and the same penalty is then applied again as a flat cut to the whole
composite score.

A channel whose feed *is* largely other people's posts gets a second,
separate demerit. Reposts (posts forwarded in from another channel) are
already dropped from all four scored terms — see app.scoring and
app.tools.channel_stat._is_repost — so a repost-heavy channel isn't
credited for that borrowed reach. On top of that, `repost_share` (the
percent of the period's posts that were reposts) applies a smooth flat cut
to the composite score via repost_share_penalty(): nothing up to
REPOST_SHARE_PENALTY_START (20%), ramping linearly to REPOST_SHARE_PENALTY_MAX
by REPOST_SHARE_PENALTY_FULL (30%) and holding there — a channel coasting on
reposts is doing less original work than its post volume suggests, and its
rating should say so. Checkpoints fetched before per-post reposts were
tracked report repost_share 0 and are unaffected until refetched.

Finally, a channel that's winding down is cut by `activity_trend_penalty()`
— a flat fraction (20% `slowing`, 33% `stalling`, 50% `abandoned`) off the
composite, from the year-over-year verdict in app.activity.channel_activity_trend
that the caller passes in as each entry's `activity_trend`. Applied after
the reach bonus so a former heavyweight feels it too; an entry with no
`activity_trend` (caller didn't supply one, or too little history to judge)
is untouched.
"""
from __future__ import annotations

import math
from statistics import median

VIEWS_PER_POST_WEIGHT = 0.42    # mean views per post — reach *efficiency*
TOTAL_REACH_WEIGHT = 0.42       # log10 of capped absolute period views — scale
ENGAGEMENT_WEIGHT = 0.65        # shares/reposts, second only to views
QUALITY_WEIGHT = 0.45           # Quality, described in scoring.py
FORWARD_RATE_WEIGHT = 0.40      # forwards ÷ views — "worth re-sharing" signal
VIRALITY_WEIGHT_MIN = 0.10
VIRALITY_WEIGHT_MAX = 0.51
REACTIONS_ENGAGEMENT_WEIGHT = 0.05  # reactions are easy to pump; shares count in full
RATE_MIN_POSTS = 8   # floor on the per-post divisor — see module docstring

# Hard ceiling on a period's viral_share as shown / fed downstream. A channel
# reading 90% "viral" means the baseline is stale — usually a checkpoint
# fetched before channel_stat's trailing-baseline fix, still storing per-month
# viral counts judged against the frozen lifetime average, so a channel that
# merely grew looks all-viral. The composite score already saturates virality
# at 15% viral_share, so this only reins in the displayed figure (Folder Stats
# / the Folders export) and Mutual PR's viral boost — no need to wait for a
# refetch to stop seeing 90%.
VIRAL_SHARE_DISPLAY_CAP = 40.0

# Below this many posts in the period the rating is built on too small a
# sample; the final score is scaled down linearly toward 0. See _confidence().
CONFIDENCE_MIN_POSTS = 12

# Flat additive lift for a channel whose period views tower over the folder's
# median channel — see module docstring and reach_bonus(). Applied last, so it
# only raises the exceptional and never disturbs channels under _START×.
REACH_BONUS_START = 4.0
REACH_BONUS_FULL = 12.0
REACH_BONUS_MAX = 0.15

ZERO_REPOSTS_PENALTY = 0.4  # rating cut for a channel with ~0 reposts — see module docstring

# Demerit for a channel that leans on reposts (posts forwarded in from other
# channels). No cut up to _START% of the period's posts being reposts, then a
# linear ramp to a _MAX fraction cut by _FULL%, held flat above that. See the
# module docstring and repost_share_penalty().
REPOST_SHARE_PENALTY_START = 20.0
REPOST_SHARE_PENALTY_FULL = 30.0
REPOST_SHARE_PENALTY_MAX = 0.30

# Demerit for a channel that's winding down — the year-over-year verdict from
# app.activity.channel_activity_trend, passed in on each entry as
# `activity_trend`. An all-time Rating otherwise keeps a channel near the top
# of its folder on the strength of the year it was busy; this cuts the
# composite score by a flat fraction per verdict. `active`, or too little
# history to judge (`activity_trend` absent / None), costs nothing.
ACTIVITY_TREND_PENALTY = {"slowing": 0.20, "stalling": 0.33, "abandoned": 0.50}


def activity_trend_penalty(trend: dict | None) -> float:
    """Fraction (0-0.50) to cut from the composite score for a declining
    channel — see ACTIVITY_TREND_PENALTY and app.activity.channel_activity_trend.
    `trend` is that function's return dict (or None); only its `verdict`
    matters here."""
    if not trend:
        return 0.0
    return ACTIVITY_TREND_PENALTY.get(trend.get("verdict"), 0.0)


def repost_share_penalty(repost_share: float) -> float:
    """Fraction (0-REPOST_SHARE_PENALTY_MAX) to cut from the composite score
    for a repost-heavy channel: 0 up to REPOST_SHARE_PENALTY_START, ramping
    linearly to REPOST_SHARE_PENALTY_MAX at REPOST_SHARE_PENALTY_FULL, flat
    above. `repost_share` is a percentage (0-100)."""
    lo, hi = REPOST_SHARE_PENALTY_START, REPOST_SHARE_PENALTY_FULL
    if repost_share <= lo:
        return 0.0
    if repost_share >= hi:
        return REPOST_SHARE_PENALTY_MAX
    return REPOST_SHARE_PENALTY_MAX * (repost_share - lo) / (hi - lo)


def virality_component(viral_share: float) -> float:
    """0-1 virality score from an *absolute* viral-share percentage (not
    normalized against other channels): 0% -> 0, 1% -> 0.05, ramping up to
    1.0 at 15%+ viral share."""
    if viral_share <= 0:
        return 0.0
    if viral_share >= 15:
        return 1.0
    if viral_share <= 1:
        return 0.05 * viral_share
    return 0.05 + (viral_share - 1) * (1.0 - 0.05) / (15 - 1)


def virality_weight(viral_share: float) -> float:
    """How much of the rating virality can swing, itself scaled by how
    viral the channel is: VIRALITY_WEIGHT_MIN at 0% viral share, ramping
    linearly to VIRALITY_WEIGHT_MAX at 15%+ viral share."""
    lo, hi = VIRALITY_WEIGHT_MIN, VIRALITY_WEIGHT_MAX
    if viral_share <= 0:
        return lo
    if viral_share >= 15:
        return hi
    return lo + (viral_share / 15) * (hi - lo)


def reach_bonus(views_ratio: float) -> float:
    """Flat lift (0-REACH_BONUS_MAX) for a channel whose period views are
    `views_ratio`× the folder's median channel: 0 up to REACH_BONUS_START,
    ramping linearly to REACH_BONUS_MAX at REACH_BONUS_FULL, flat above."""
    lo, hi = REACH_BONUS_START, REACH_BONUS_FULL
    if views_ratio <= lo:
        return 0.0
    if views_ratio >= hi:
        return REACH_BONUS_MAX
    return REACH_BONUS_MAX * (views_ratio - lo) / (hi - lo)


def _per_post(total: float, posts: float) -> float:
    """A period total divided by the post count, floored at RATE_MIN_POSTS so
    a 2-post channel's "average" isn't 20× everyone else's."""
    return total / max(posts or 0, RATE_MIN_POSTS)


def _confidence(posts: float) -> float:
    """0-1 multiplier on the final score: 1.0 at CONFIDENCE_MIN_POSTS+ posts
    in the period, ramping linearly to 0 as the count falls to nothing — too
    thin a sample to trust below that."""
    return min(1.0, max(posts or 0, 0) / CONFIDENCE_MIN_POSTS)


def _norm(value: float, lo: float, hi: float) -> float:
    return (value - lo) / (hi - lo) if hi != lo else 0.0


def _capped_reach(views: float, per_post: float, median_posts: float) -> float:
    """log10 of the period views, but capped at what the channel would have
    delivered posting at the folder's median cadence (per_post × median_posts)
    — so absolute scale counts, but a flood of low-value posts past the
    folder norm buys no more rating. See the module docstring."""
    ceiling = per_post * max(median_posts, 1)
    return math.log10(max(min(views, ceiling), 1.0))


def score_entries(entries: list[dict]) -> None:
    """Fill in a `score` key (0-1) on each entry in place — see module
    docstring for the formula."""
    if not entries:
        return
    median_views = median([e["views"] for e in entries])
    median_posts = median([e.get("posts", 0) for e in entries])
    views_vals = [_per_post(e["views"], e.get("posts", 0)) for e in entries]
    reach_vals = [_capped_reach(e["views"], _per_post(e["views"], e.get("posts", 0)),
                                median_posts) for e in entries]
    eng_vals = [_per_post(e["shares"] + REACTIONS_ENGAGEMENT_WEIGHT * e["reactions"],
                          e.get("posts", 0)) for e in entries]
    quality_vals = [e["quality"] for e in entries]
    fwd_vals = [e["shares"] / max(e["views"], 1) for e in entries]
    vw_min, vw_max = min(views_vals), max(views_vals)
    rc_min, rc_max = min(reach_vals), max(reach_vals)
    eg_min, eg_max = min(eng_vals), max(eng_vals)
    qa_min, qa_max = min(quality_vals), max(quality_vals)
    fw_min, fw_max = min(fwd_vals), max(fwd_vals)
    for e in entries:
        per_post_views = _per_post(e["views"], e.get("posts", 0))
        views_norm = _norm(per_post_views, vw_min, vw_max)
        reach_norm = _norm(_capped_reach(e["views"], per_post_views, median_posts),
                           rc_min, rc_max)
        eng_norm = _norm(_per_post(e["shares"] + REACTIONS_ENGAGEMENT_WEIGHT * e["reactions"],
                                   e.get("posts", 0)), eg_min, eg_max)
        quality_norm = _norm(e["quality"], qa_min, qa_max)
        fwd_norm = _norm(e["shares"] / max(e["views"], 1), fw_min, fw_max)
        zero_reposts = e["shares"] == 0
        viral_share_for_score = e["viral_share"]
        if zero_reposts:
            viral_share_for_score *= 1.0 - ZERO_REPOSTS_PENALTY
        v_score = virality_component(viral_share_for_score)
        v_weight = virality_weight(viral_share_for_score)
        total_weight = (VIEWS_PER_POST_WEIGHT + TOTAL_REACH_WEIGHT + ENGAGEMENT_WEIGHT
                        + QUALITY_WEIGHT + FORWARD_RATE_WEIGHT + v_weight)
        e["score"] = (VIEWS_PER_POST_WEIGHT * views_norm
                     + TOTAL_REACH_WEIGHT * reach_norm
                     + ENGAGEMENT_WEIGHT * eng_norm
                     + QUALITY_WEIGHT * quality_norm
                     + FORWARD_RATE_WEIGHT * fwd_norm
                     + v_weight * v_score) / total_weight
        if zero_reposts:
            e["score"] *= 1.0 - ZERO_REPOSTS_PENALTY
        e["score"] *= 1.0 - repost_share_penalty(e.get("repost_share", 0) or 0)
        # Additive, capped at 1.0 — lifts only the folder's titans and leaves
        # everyone under REACH_BONUS_START× the median untouched.
        ratio = e["views"] / median_views if median_views else 0.0
        e["score"] = min(1.0, e["score"] + reach_bonus(ratio))
        # A channel winding down shouldn't ride an all-time Rating near the
        # top — applied after the reach bonus so even a former titan feels
        # it (see activity_trend_penalty).
        e["score"] *= 1.0 - activity_trend_penalty(e.get("activity_trend"))
        # Last: too few posts in the period → the sample is noise.
        e["score"] *= _confidence(e.get("posts", 0))
