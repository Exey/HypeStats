"""Mutual PR (ad-swap) scoring — shared by app.ui.mutual_pr_view, which lists
every tracked channel with an estimated ad-post follower-gain forecast and a
"best days to post" pick, to help decide which channels are worth trading ad
posts with.

Two layers live here: the per-channel ad-post forecast (ad_forecast and
everything it calls), and — at the very bottom, under the "Mutual PR partner
matching" header — the per-*pair* compatibility score behind that view's
"MPR Pairs" / "Пары ВП" table (rank_mutual_pr_pairs), shown on screen and
appended to its Markdown export.

The app has no real ad-campaign outcome data anywhere: ChannelStore keeps one
point-in-time snapshot per channel (no follower-history, no growth-over-time),
and every post record stores a single final view count with no view-age
curve (see app.tools.channel_stat). So unlike app.scoring's per-post formula
(tuned against this app's own real median), the forecast below is built on
constants that are genuinely just documented heuristics, not measurements —
they exist so a forecast can be produced at all, not because they were
calibrated against real ad-swap results:

    AD_VIEW_CURVE: assumed fraction of a post's eventual (settled) reach
        that has landed by each horizon. Shaped to match the accumulation
        curve implied by one real channel's own observed ad performance
        (~60% within 24h, ~75% by 48h, most of the rest trickling in over
        the following two weeks) — generalized into a fixed curve since no
        per-channel view-age data exists to calibrate it per channel.
    FOLLOW_CONVERSION_BASE: assumed fraction of a post's reach that converts
        into a new follower for an *ad* post specifically (as opposed to the
        channel's own organic content), scaled by the channel's own content
        quality (see follow_conversion_rate) since a channel that engages
        its own audience well presumably also converts outside traffic
        better — but only mildly (INTEREST_WEIGHT is deliberately small):
        calibrated against two channels with real known ad-swap outcomes,
        reach (avg_views_settled) alone already predicted their ~2-3x gap
        almost exactly (2.63x raw reach ratio vs. a stated real ~2-3x
        follower-gain ratio), despite one having much lower measured
        content quality than the other. Quality still nudges the estimate,
        it just isn't allowed to fight a clear reach difference the way an
        earlier, much larger weight (plus an added virality multiplier)
        did — that combination crushed a 2.63x-reach channel down to near
        parity with a smaller one, which is what prompted this recalibration.

These are ordinary module constants specifically so they're easy to retune
once real ad-swap outcomes are available to calibrate against — logging
what an actual ad-swap post does (follower count before, then at 24h/48h/
week/month) is the real fix, not anything below; there's currently no
pipeline for that, so everything here stays a heuristic in the meantime.

On top of the calibrated base, ad_forecast applies size_forecast_multiplier
— a deliberate flat scale-up (×3 for channels up to ~6k followers, ×1.5 for
larger, see the FORECAST_SMALL_/FORECAST_LARGE_ constants) reflecting that
smaller channels convert borrowed audience far better. This intentionally
departs from the two-channel calibration above; retune it there.

Reach (avg_views_settled) is used directly and unmodified as the forecast's
total-reach basis — not blended or capped against a shorter, noisier recent
window. An earlier version tried grounding the "month" figure in a 30-60-
day-old post sample specifically, but that window is small enough (a
handful of posts, fewer for an infrequent poster) that a single big post
landing in it could double the estimate; avg_views_settled, averaged over
a channel's entire settled history, doesn't have that problem and needs no
compensating cap to fix it.

Per-horizon adjustments (currently just a rare-posting channel's post
lingering un-buried and so gathering reach more gradually) are applied as
REDISTRIBUTION of one fixed total across AD_VIEW_CURVE's fractions, not as
independent per-horizon multipliers — see ad_forecast. An earlier version
also tilted 24h's fraction toward 48h's by content quality, independently
scaled 24h's own conversion rate harder than the other horizons', which
could (and on real checkpoints, did) let the 24h figure exceed the 48h one
— a cumulative-forecast ordering that makes no sense regardless of what
the constants get tuned to. Redistributing a fixed total is what makes
that impossible by construction rather than by clamping after the fact:
month's fraction is always exactly 1.0 (that's its definition — "all of
it has landed"), and every earlier fraction is built by shrinking toward
0, never past its own next-larger neighbor.

ad_forecast_range() reports a low/high band alongside the point estimate
— a crude, honestly-labeled uncertainty range (not a fitted statistical
interval, since there's no outcome data yet to fit one against) reflecting
the two dominant unverified constants above.

Everything above forecasts off `avg_views_settled`, a lifetime average, so
a channel that used to be busy and has since gone quiet would otherwise
still forecast — and rank, sorted best-first by 24h forecast — off its old
reach. ad_forecast's own `activity_trend` argument cuts the whole forecast
by the same year-over-year decline penalty a channel's Rating already
takes (app.rating.activity_trend_penalty) — the caller
(app.ui.mutual_pr_view) supplies app.activity.channel_activity_trend's own
verdict for it.
"""
from __future__ import annotations

import math
import re
from datetime import datetime

from .periods import year_window_cutoff
from .rating import activity_trend_penalty
from .scoring import post_gauge_value, post_score_raw

# Link-detection regexes adapted from tg-super-admin's app/tools/
# links_compare.py (LINK_RE / MENTION_RE / GENERIC_URL_RE) — that tool
# scans live Telethon messages for cross-channel links, this one scans
# this app's own already-stored post text, so the patterns are copied in
# rather than imported across the two separate projects.
# t.me/ links, with or without an "https://" scheme — a bare "t.me/name"
# mention counts exactly the same as a fully-qualified one.
_TME_LINK_RE = re.compile(r"(?<![\w.])(?:https?://)?t\.me/[A-Za-z0-9_+/-]{1,80}",
                          re.IGNORECASE)
# Bare @mentions (Telegram usernames are 5-32 chars, start with a letter);
# `(?<![\w@])` avoids matching the local part of an email address.
_MENTION_RE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}\b")
# Any http(s) URL that *isn't* a t.me link — external/ad-style links.
_EXTERNAL_URL_RE = re.compile(
    r"(?<![\w])https?://(?!(?:www\.)?t\.me/)[^\s<>\)\]]+", re.IGNORECASE)

# "Last ~3 months" — narrowed from 6 on request, since more recent posts
# are a more relevant read of current audience interest than a
# half-year-old average.
INTEREST_WINDOW_DAYS = 90

# Fraction of a post's eventual (settled) reach assumed to have landed by
# each horizon — see module docstring. 24h trimmed to 82% of its original
# 0.60 (-> 0.49) on request, pulling the whole curve's most-visible number
# down a bit.
AD_VIEW_CURVE: dict[str, float] = {
    "24h": 0.49, "48h": 0.75, "72h": 0.78, "week": 0.80, "month": 1.00,
}

# Calibrated together against two channels with real known ad-swap
# outcomes (see module docstring): BASE alone (at INTEREST_WEIGHT's
# midpoint multiplier) reproduces the higher-quality channel's known
# ~200-250-in-24h figure almost exactly, and the resulting *small*
# INTEREST_WEIGHT swing (0.5x-0.8x, not the 0.5x-1.7x an earlier version
# used) still lets the lower-quality-but-much-bigger-reach channel land at
# ~2.25x that figure — inside the real ~2-3x gap the user reported between
# them, which a heavier quality weight (plus a now-removed extra virality
# multiplier) had been crushing down toward parity.
FOLLOW_CONVERSION_BASE = 0.036
INTEREST_WEIGHT = 0.3   # see follow_conversion_rate

# INTEREST_WEIGHT is boosted within this follower range (see
# size_band_factor) — quality reads as a more reliable predictor for a
# channel this size than for a much bigger one, per a real known channel
# in this range whose actual ad-swap follower gain the base weight alone
# couldn't reach even at max interest_gauge (a 6300-avg-view channel with
# middling quality has a real ~100-in-24h/~250-300-in-month outcome; the
# unweighted formula topped out under 100 no matter how high interest_gauge
# went, since INTEREST_WEIGHT only swings the rate 0.5x-0.8x). Matching
# that channel's number *exactly* would need roughly a 7.6x weight
# multiplier just for it — implausibly large from one data point, and it
# would swing every other channel in this same band (verified against two,
# neither with a real target of its own) by a similar amount. SIZE_BAND_
# BOOST below is a deliberately more conservative 3x instead: real
# movement in the right direction without betting this much on a single
# approximate figure.
SIZE_BAND_LOW = 5_000
SIZE_BAND_HIGH = 30_000
SIZE_BAND_MARGIN = 5_000   # linear taper width outside [LOW, HIGH]
SIZE_BAND_BOOST = 3.0

# avg_views_settled capped at followers × this ratio (see reach_basis) — a
# real, otherwise-large-reach channel in this app's own tracked data has
# avg_views_settled at 1.9x its own follower count, the only one of five
# checked channels to exceed 1.0x by any real margin. Views beyond a
# channel's own subscriber base reflect external/viral discovery (public
# forwarding, Telegram's own recommendation surfacing older posts over
# months) that a freshly-placed ad post wouldn't automatically inherit —
# so left uncapped, that channel's forecast came out several times its own
# real known ~100-200-in-24h range even with every other lever at its
# floor. 1.0 leaves any channel whose average is still within its own
# subscriber count (the other four checked) completely untouched.
VIEWS_PER_FOLLOWER_CAP = 1.0

# Extra credit within the SIZE_BAND (see size_band_factor) for a channel
# that produces genuine breakout hits often — stats.viral_post_share, not
# channel_interest's average-engagement-rate gauge — gated off whenever
# VIEWS_PER_FOLLOWER_CAP already reduced that channel's reach basis, so a
# channel whose virality already shows up as a capped, oversized
# avg_views_settled doesn't get credited for the same thing twice.
# WEIGHT recalibrated down from an original 1.5 on request — a real
# ~11K-follower channel at a fairly ordinary 4.0% viral share (this app's
# real p90 is ~4.8%, so not actually an outlier) was hitting near the full
# multiplier on top of SIZE_BAND's own 3x quality boost, more than
# doubling its forecast versus the same figure with viral_boost left out
# entirely — a compounding nobody asked for. Re-solved instead from the
# one real target this module has for a *partial* multiplier (a known
# channel's own figure needed to come down ~10-20% from what WEIGHT=1.5
# gave it), which incidentally also brings the ~11K-follower channel back
# down close to what its reach and quality alone would already predict.
VIRAL_BOOST_WEIGHT = 1.07
VIRAL_BOOST_SATURATE_PCT = 3.0

# Posts-in-between at which a repeat ad post's retention has decayed to
# half — see repeated_post_forecast. 16 is chosen so a channel posting at
# this app's real median avg_posts_per_day across its own tracked
# checkpoints (~0.525/day, i.e. ~15.75 posts across the 30-day gap) lands
# almost exactly at the ~50%-of-first-post figure asked for.
REPEAT_DECAY_POSTS_K = 16

# This app's real median avg_posts_per_day across its own tracked
# checkpoints — see rarity().
REFERENCE_POSTS_PER_DAY = 0.525

# rarity() alone (linear in avg_posts_per_day) barely told apart a channel
# posting every ~4-5 days from one posting only every ~7-8 days — 0.581 vs
# 0.752, not a wide gap — even though the real "how buried does this post
# get" dynamic differs more sharply out at that extreme. Squaring widens
# it (0.338 vs 0.566) without moving the moderate case much, so
# RARE_POSTING_SHRINK_MAX below could be rescaled to keep moderate
# rare-posters where they already looked right while genuinely-rare ones
# (e.g. one real ~25K-follower, 0.13-posts/day channel forecasting a
# visibly-too-fast 24h figure relative to its own actual slow, gradual
# view accumulation) shrink further. See _rarity_curve.
RARITY_CURVE_EXPONENT = 2

# Max fraction shrunk out of each early horizon (and implicitly pushed
# later, since month's fraction is fixed at 1.0 — see _rarity_curve) for a
# channel posting far below REFERENCE_POSTS_PER_DAY: its post lingers
# un-buried by a next one, so its audience discovers it more gradually
# instead of mostly in the first day or two. Decreasing across horizons —
# 24h shrinks the most, week the least — is what keeps the curve
# monotonically increasing for every rarity value (verified numerically
# at 0.001 resolution across the full [0,1] range, not just spot-checked).
# Rescaled up from a flat halving (0.40/0.28/0.23/0.15) to compensate for
# RARITY_CURVE_EXPONENT making every rarity value smaller.
RARE_POSTING_SHRINK_MAX: dict[str, float] = {
    "24h": 0.69, "48h": 0.48, "72h": 0.40, "week": 0.26,
}

# Below this many total stored posts, rarity()'s redistribution effect is
# scaled down proportionally — a channel with only a handful of posts
# hasn't given avg_posts_per_day enough evidence to justify reshaping its
# whole curve on it. See _data_quality_factor.
DATA_QUALITY_MIN_POSTS = 20

# Crude low/high multipliers on the point estimate — see ad_forecast_range
# and the module docstring. Derived by combining this module's two named
# unverified ranges: FOLLOW_CONVERSION_BASE's own plausible +-50% (still
# just a single calibration point, not a fitted distribution) with a
# further +-20% for AD_VIEW_CURVE's shape (derived from a single channel's
# own observed performance).
FORECAST_LOW_MULT = 0.5 * 0.8
FORECAST_HIGH_MULT = 1.5 * 1.2

# Flat scale-up of the whole forecast by channel size, applied on top of
# everything else in ad_forecast. Smaller channels convert borrowed
# audience into followers at a much higher rate than big ones — an ad post
# in a niche 3k-follower channel reaches people for whom it's a genuine
# discovery, while the same post in a 100k channel mostly reaches people
# who already know it exists. ×FORECAST_SMALL_MULT at/below
# FORECAST_SMALL_MAX followers, ×FORECAST_LARGE_MULT at/above
# FORECAST_LARGE_MIN, linear between (a soft landing, not a cliff at 6k).
# This is a deliberate override on top of the calibrated base above —
# retune freely.
FORECAST_SMALL_MAX = 6_000
FORECAST_LARGE_MIN = 7_000
FORECAST_SMALL_MULT = 3.0
FORECAST_LARGE_MULT = 1.5

# A post name-dropping 1-MENTION_LINK_MAX_COUNT other channels (a t.me/ or
# @mention link) reads as genuine single-mention cross-promotion — the
# behavior this whole view exists to find — and earns up to
# MENTION_LINK_BONUS_MAX extra on the forecast. More than that in one post
# reads as a directory/link-dump post instead, not real cross-promotion,
# and earns nothing. A post with EXTERNAL_LINK_MIN_COUNT+ non-t.me https://
# links reads as ad/spam behavior instead, cut by up to
# EXTERNAL_LINK_PENALTY_MAX. Both are shares across every stored post (see
# link_behavior_factor), so a channel that does this rarely barely moves
# either number.
MENTION_LINK_MAX_COUNT = 3
MENTION_LINK_BONUS_MAX = 0.05
EXTERNAL_LINK_MIN_COUNT = 2
EXTERNAL_LINK_PENALTY_MAX = 0.05

# How much of a "best day"'s raw deviation from an average day survives
# into its displayed rate — see best_days. 1.0 (no damping) produced
# swings like x2.1-x2.7 on real checkpoints, read as overconfident for
# what's ultimately a thin single-week-shape heuristic; 0.25 keeps the
# ranking (still sorted by the undamped score) but reports a gentler
# number.
BEST_DAY_RATE_DAMPING = 0.25


def _parse_date(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def channel_interest(rows: list[dict], avg_views: float,
                     days: int = INTEREST_WINDOW_DAYS) -> float:
    """Average post-quality gauge (app.scoring) over posts from the last
    `days`. Only as complete as the checkpoint's stored post pool for that
    window — see app.tools.channel_stat's top-N pool, the same limitation
    app.ui.folder_stat_view's windowed quality column already accepts.
    Falls back to every stored post if none fall in the window (e.g. an
    older checkpoint whose pool skews further back)."""
    cutoff = year_window_cutoff(days)
    windowed = [r for r in rows if (dt := _parse_date(r.get("date", ""))) and dt >= cutoff]
    pool = windowed or rows
    if not pool:
        return 0.0
    scores = [post_gauge_value(post_score_raw(r, avg_views)) for r in pool]
    return sum(scores) / len(scores)


def size_band_factor(followers: int, low: int = SIZE_BAND_LOW, high: int = SIZE_BAND_HIGH,
                     margin: int = SIZE_BAND_MARGIN, boost: float = SIZE_BAND_BOOST) -> float:
    """1.0 outside [low-margin, high+margin], `boost` flat across
    [low, high], linearly tapering between — see SIZE_BAND_BOOST."""
    if followers <= low - margin or followers >= high + margin:
        return 1.0
    if low <= followers <= high:
        return boost
    if followers < low:
        t = (followers - (low - margin)) / margin
    else:
        t = ((high + margin) - followers) / margin
    return 1.0 + (boost - 1.0) * t


def follow_conversion_rate(interest_gauge: float, followers: int = 0,
                           base: float = FOLLOW_CONVERSION_BASE) -> float:
    """Assumed reach-to-new-follower conversion rate for an ad post, scaled
    0.5x-0.8x the base rate by the channel's own content quality (0-1000
    gauge, weighted by INTEREST_WEIGHT — deliberately a narrow swing, not a
    dominant one, see module docstring for why) — with that weight further
    boosted by size_band_factor(followers) for a channel in the 5K-30K
    follower range, where quality reads as a more reliable predictor."""
    weight = INTEREST_WEIGHT * size_band_factor(followers)
    return base * (0.5 + weight * interest_gauge / 1000)


def reach_basis(avg_views_settled: float, followers: int,
                cap_mult: float = VIEWS_PER_FOLLOWER_CAP) -> tuple[float, bool]:
    """(reach, was_capped) — avg_views_settled, capped at followers ×
    cap_mult — see VIEWS_PER_FOLLOWER_CAP."""
    if followers <= 0:
        return avg_views_settled, False
    cap = followers * cap_mult
    if avg_views_settled > cap:
        return cap, True
    return avg_views_settled, False


def viral_boost(viral_post_share: float, followers: int, was_capped: bool) -> float:
    """>=1 multiplier from stats.viral_post_share — only within SIZE_BAND,
    and only when reach_basis() didn't already cap this channel's reach
    (see VIRAL_BOOST_WEIGHT for why that double-counting is skipped)."""
    if was_capped or size_band_factor(followers) <= 1.0:
        return 1.0
    share = max(0.0, viral_post_share)
    return 1 + VIRAL_BOOST_WEIGHT * min(1.0, share / VIRAL_BOOST_SATURATE_PCT)


def rarity(avg_posts_per_day: float, reference: float = REFERENCE_POSTS_PER_DAY) -> float:
    """0 (posting at/above `reference`) to just under 1 (posting far below
    it) — how much a channel's posting frequency lags this app's real
    median. AD_VIEW_CURVE's shape assumes a post gets "buried" by the
    channel's own next post within a day or so, the normal case that
    pushes most of its eventual reach into the earliest horizons. A
    rarely-posting channel's post instead stays at the top with nothing
    superseding it for days or weeks, so its audience keeps discovering it
    gradually over a much longer real timeline. See _rarity_curve for how
    this reshapes (not inflates) the forecast."""
    if reference <= 0:
        return 0.0
    return 1 - min(1.0, avg_posts_per_day / reference)


def _data_quality_factor(total_posts: int, minimum: int = DATA_QUALITY_MIN_POSTS) -> float:
    """0-1 factor scaling down rarity()'s redistribution for a channel with
    too few stored posts to trust avg_posts_per_day's estimate — 1.0 at or
    above `minimum` total posts, down to 0 for a channel with none."""
    if minimum <= 0:
        return 1.0
    return min(1.0, max(0, total_posts) / minimum)


def _rarity_curve(avg_posts_per_day: float, total_posts: int) -> dict[str, float]:
    """AD_VIEW_CURVE's fractions, redistributed (not inflated — month
    always stays exactly 1.0) by posting rarity, raised to
    RARITY_CURVE_EXPONENT to separate moderate from extreme rare-posters
    (see its docstring). Monotonically increasing for every input by
    construction — see RARE_POSTING_SHRINK_MAX's docstring for the proof
    sketch."""
    r = rarity(avg_posts_per_day) ** RARITY_CURVE_EXPONENT * _data_quality_factor(total_posts)
    curve = dict(AD_VIEW_CURVE)
    for horizon, shrink_max in RARE_POSTING_SHRINK_MAX.items():
        curve[horizon] *= 1 - shrink_max * r
    return curve


def link_behavior_factor(rows: list[dict]) -> float:
    """Multiplier applied across the whole forecast based on how the
    channel actually links out in its posts — see the MENTION_LINK_*/
    EXTERNAL_LINK_* constants above. 1.0 (no posts, or no links at all) up
    to 1 + MENTION_LINK_BONUS_MAX for a channel that consistently
    name-drops other channels, down to as low as 1 - EXTERNAL_LINK_
    PENALTY_MAX for one that consistently posts external-link spam;
    floored at 0 either way."""
    posts = [r.get("full_text") or r.get("text") or "" for r in rows]
    posts = [text for text in posts if text]
    if not posts:
        return 1.0
    mention_qualifying = 0
    external_heavy = 0
    for text in posts:
        mention_count = len(_TME_LINK_RE.findall(text)) + len(_MENTION_RE.findall(text))
        if 1 <= mention_count <= MENTION_LINK_MAX_COUNT:
            mention_qualifying += 1
        if len(_EXTERNAL_URL_RE.findall(text)) >= EXTERNAL_LINK_MIN_COUNT:
            external_heavy += 1
    bonus = MENTION_LINK_BONUS_MAX * (mention_qualifying / len(posts))
    penalty = EXTERNAL_LINK_PENALTY_MAX * (external_heavy / len(posts))
    return max(0.0, 1 + bonus - penalty)


def size_forecast_multiplier(followers: int) -> float:
    """Flat forecast scale-up by channel size — see the FORECAST_SMALL_/
    FORECAST_LARGE_ constants. FORECAST_SMALL_MULT at/below
    FORECAST_SMALL_MAX followers, FORECAST_LARGE_MULT at/above
    FORECAST_LARGE_MIN, linearly interpolated in between."""
    if followers <= FORECAST_SMALL_MAX:
        return FORECAST_SMALL_MULT
    if followers >= FORECAST_LARGE_MIN:
        return FORECAST_LARGE_MULT
    t = (followers - FORECAST_SMALL_MAX) / (FORECAST_LARGE_MIN - FORECAST_SMALL_MAX)
    return FORECAST_SMALL_MULT + t * (FORECAST_LARGE_MULT - FORECAST_SMALL_MULT)


def ad_forecast(avg_views_settled: float, interest_gauge: float,
                avg_posts_per_day: float, total_posts: int = 0,
                followers: int = 0, viral_post_share: float = 0.0,
                rows: list[dict] | None = None,
                activity_trend: dict | None = None) -> dict[str, float]:
    """Estimated *new followers* gained by each horizon after an ad post.

    One fixed total — reach_basis(avg_views_settled, followers) ×
    follow_conversion_rate(..., followers) × viral_boost(...) ×
    size_forecast_multiplier(followers) — is
    multiplied out across `_rarity_curve`'s per-horizon fractions.
    Posting-rarity reshapes *when* that fixed total arrives (see
    _rarity_curve and the module docstring on why this is a
    redistribution, not an independent per-horizon multiplier); month's
    fraction is always exactly 1.0, so no horizon can ever forecast more
    than the total itself. link_behavior_factor(rows), when the caller has
    post text to check, is the one true multiplier on top — it reflects
    the *content* of an ad post, not a reshaping of reach over time, so it
    scales the whole curve evenly.

    `avg_views_settled` is a lifetime average, so a channel that used to be
    busy and has since gone quiet still forecasts off its old reach —
    exactly the "abandoned channel at the top of Mutual PR" problem.
    `activity_trend` (app.activity.channel_activity_trend's own return
    dict, or None) applies the same year-over-year decline cut everywhere
    else in this app: app.rating.activity_trend_penalty, so a `slowing` /
    `stalling` / `abandoned` channel's forecast — and everything downstream
    of it (the main table's default 24h sort, repeated_post_forecast,
    quality_parity/day_overlap on the MPR Pairs side) — is discounted the
    same way its Rating already is. Applied last, evenly across every
    horizon, same as link_behavior_factor."""
    reach, was_capped = reach_basis(avg_views_settled, followers)
    rate = follow_conversion_rate(interest_gauge, followers)
    boost = viral_boost(viral_post_share, followers, was_capped)
    total = reach * rate * boost * size_forecast_multiplier(followers)
    curve = _rarity_curve(avg_posts_per_day, total_posts)
    forecast = {horizon: total * fraction for horizon, fraction in curve.items()}
    if rows is not None:
        factor = link_behavior_factor(rows)
        forecast = {horizon: value * factor for horizon, value in forecast.items()}
    if activity_trend is not None:
        factor = 1.0 - activity_trend_penalty(activity_trend)
        forecast = {horizon: value * factor for horizon, value in forecast.items()}
    return forecast


def ad_forecast_range(forecast: dict[str, float]) -> dict[str, tuple[float, float]]:
    """(low, high) for each horizon in `forecast` (see ad_forecast) — a
    crude, honestly-labeled uncertainty band via FORECAST_LOW_MULT/
    FORECAST_HIGH_MULT, not a statistically fitted interval (there's no
    outcome data yet to fit one against — see module docstring)."""
    return {horizon: (value * FORECAST_LOW_MULT, value * FORECAST_HIGH_MULT)
           for horizon, value in forecast.items()}


def repeated_post_forecast(forecast_24h: float, avg_posts_per_day: float,
                           days: int = 30) -> float:
    """Estimated new followers from a second/reminder ad post placed `days`
    (default 30 — "repeated after a month") after the first one, decayed by
    how many of the channel's *own* posts appeared in between and pushed
    the original down the feed: retention = 1 / (1 + posts_between / K),
    where posts_between = avg_posts_per_day × days and K =
    REPEAT_DECAY_POSTS_K. More intervening posts means more audience
    churn/forgetting, so a noisier channel's repeat performs worse than a
    quieter one's, both relative to that same channel's own 24h forecast."""
    posts_between = avg_posts_per_day * days
    retention = 1 / (1 + posts_between / REPEAT_DECAY_POSTS_K)
    return forecast_24h * retention


def best_days(weekday_counts: list[int], interest_gauge: float,
              top_n: int = 2) -> list[tuple[int, float]]:
    """(weekday index 0=Mon..6=Sun, rate) for the `top_n` days least crowded
    by the channel's own posting habits, i.e. best days to slot in an ad
    post without competing with the channel's own content —
    Best_Day_Score = (1 - normalized post rate) × quality, per weekday. If
    every weekday has the same count (including an all-zero, e.g.
    brand-new, channel), normalized post rate is 0 for all of them rather
    than dividing by zero, so the ranking falls back to interest alone (a
    tie, broken by weekday order). `rate` is that day's score relative to
    the week's average score, damped by BEST_DAY_RATE_DAMPING (e.g. a raw
    2.0x — double an average day — reports as 1.25x: 1 + (2.0-1)×0.25) —
    1.0 for every day when the average score is 0 (nothing to
    differentiate on). Ranking uses the undamped score; only the displayed
    rate is softened."""
    lo, hi = min(weekday_counts), max(weekday_counts)
    spread = hi - lo
    scores = []
    for day, count in enumerate(weekday_counts):
        normalized_rate = (count - lo) / spread if spread else 0.0
        scores.append((day, (1 - normalized_rate) * interest_gauge))
    avg_score = sum(s for _day, s in scores) / len(scores)
    rates = [(day, (score / avg_score) if avg_score else 1.0) for day, score in scores]
    rates.sort(key=lambda dr: dr[1], reverse=True)
    return [(day, 1 + (rate - 1) * BEST_DAY_RATE_DAMPING) for day, rate in rates[:top_n]]


# ======================================================================
# Mutual PR partner matching
# ======================================================================
# Everything above forecasts what *one* channel does with an ad post. This
# section instead scores a *pair* of channels for how good an ad swap
# between them would be — the "MPR Pairs" / "Пары ВП" table (the on-screen
# card and the section appended to app.ui.mutual_pr_view's Markdown export).
#
# It uses only metrics this app already has (followers, the 24h forecast
# above, best_days above, and the two channels' tag / folder). It does NOT
# know whether the two channels have already cross-promoted each other —
# there's no mention graph here. When that data exists, filter those pairs
# out before calling rank_mutual_pr_pairs (or multiply a pair's score by 0),
# the same way an already-swapped pair adds nothing.
#
# The four weights are a plain weighted sum and sum to 1.0, so a pair's
# score is directly in [0, 1]:
#
#     score = W_SIZE     × size_parity       (fair-exchange: similar reach)
#           + W_QUALITY   × quality_parity    (both convert views similarly)
#           + W_DAY       × day_overlap       (their best posting days line up)
#           + W_NICHE     × niche_affinity    (same audience — see below)
#
# niche_affinity is tag-first: a shared *tag* is a real niche match (1.0);
# merely sharing a *folder* counts for much less (MUTUAL_PR_FOLDER_NICHE),
# because a folder is just sidebar organization, not a taxonomy. This is
# what lets a strong cross-folder pair (different folders, same tag) beat a
# weak same-folder one (one folder, unrelated tags) — and channels with no
# tag at all fall back to the folder-only signal.
#
# Tune the weights here; they're ordinary constants on purpose.

MUTUAL_PR_W_SIZE = 0.30
MUTUAL_PR_W_QUALITY = 0.30
MUTUAL_PR_W_DAY_OVERLAP = 0.20
MUTUAL_PR_W_NICHE = 0.20

# niche_affinity value when the two channels only share a folder (no common
# tag) — deliberately small so folder membership barely moves the score and
# cross-folder same-tag pairs surface.
MUTUAL_PR_FOLDER_NICHE = 0.30

# Follower ratio (bigger / smaller) at which the two channels count as
# "totally mismatched in size" — size_parity reaches 0 here and is clamped
# from going negative. 100× ≈ a 5k channel paired with a 500k one.
MUTUAL_PR_SIZE_MAX_RATIO = 100.0

# Two channels within this many followers of each other (absolute, not
# ratio) always score a full size_parity, regardless of what the log-ratio
# below would say — without it, a modest absolute gap between two small
# channels reads as a severe mismatch purely because the *ratio* is large
# (300 vs 1300 followers is a 4.3× ratio, the same as 40k vs 172k), even
# though they're obviously the same tier. Widens how many close-in-size
# pairs clear MUTUAL_PR_MIN_SCORE, on top of whatever the Channel links
# card's own exclusion already surfaces (see app.ui.mutual_pr_view).
MUTUAL_PR_SIZE_WINDOW = 2000

# The MPR Pairs table (UI card and Markdown export) lists every pair scoring
# at or above this, best first, capped at MUTUAL_PR_MAX_PAIRS. The score
# floor is intentionally low — the table is sorted best-first, so a weak
# tail is just ignorable, and the cap is what actually bounds the size.
MUTUAL_PR_MIN_SCORE = 0.51
MUTUAL_PR_MAX_PAIRS = 500

# Once ranked, at most this many of any one channel's own best pairs survive
# (see rank_mutual_pr_pairs's max_per_channel) — otherwise a channel that
# scores well against nearly everyone (a big generalist, say) can crowd the
# whole table with itself, burying pairs that don't involve it at all.
MUTUAL_PR_MAX_PAIRS_PER_CHANNEL = 10

# How many "works well for both channels" weekdays mutual_best_days returns
# (the ★ days in the MPR Pairs "best days" column).
MUTUAL_BEST_DAYS_TOP_N = 2


def size_parity(followers_a: int, followers_b: int,
                max_ratio: float = MUTUAL_PR_SIZE_MAX_RATIO,
                window: int = MUTUAL_PR_SIZE_WINDOW) -> float:
    """1.0 when both channels are the same size, ramping down to 0.0 (and
    clamped there) once one is `max_ratio`× the other. Log-scaled, so a
    10k/20k pair and a 100k/200k pair score the same — a 2× size gap is a
    2× gap regardless of the absolute numbers.

    Overridden to a flat 1.0 first, though, whenever the two are within
    `window` followers of each other in absolute terms (MUTUAL_PR_SIZE_WINDOW)
    — the log-ratio above is otherwise unkind to small channels, where even
    a trivial headcount gap is a large *ratio* (300 vs 1300 followers scores
    the same as 40k vs 172k without this)."""
    a = max(int(followers_a or 0), 1)
    b = max(int(followers_b or 0), 1)
    if abs(a - b) <= window:
        return 1.0
    span = math.log10(max_ratio) or 1.0
    return max(0.0, 1.0 - abs(math.log10(a) - math.log10(b)) / span)


def quality_parity(forecast24_a: float, forecast24_b: float) -> float:
    """1.0 when both channels' 24h ad-post forecasts (see ad_forecast) are
    equal, falling toward 0 as they diverge: `1 - |a - b| / (a + b)`. The
    24h forecast stands in for "how well this channel turns ad views into
    followers". 0.0 when neither channel has any forecast at all."""
    total = float(forecast24_a) + float(forecast24_b)
    if total <= 0:
        return 0.0
    return 1.0 - abs(float(forecast24_a) - float(forecast24_b)) / total


def day_overlap(best_days_a, best_days_b) -> float:
    """Fraction of the *smaller* channel's best-posting weekdays that are
    also a best day for the other channel — `common / min(len_a, len_b)`.
    `best_days_*` are best_days() outputs (lists of (weekday_index, rate));
    only the weekday index matters here. 0.0 when either list is empty."""
    days_a = {int(d) for d, _rate in best_days_a}
    days_b = {int(d) for d, _rate in best_days_b}
    if not days_a or not days_b:
        return 0.0
    return len(days_a & days_b) / min(len(days_a), len(days_b))


def mutual_best_days(ranked_a, ranked_b, top_n: int = MUTUAL_BEST_DAYS_TOP_N):
    """Weekdays that are a good ad slot in *both* channels at once — for a
    coordinated cross-promo where both sides post on the same day.

    `ranked_a` / `ranked_b` are full best_days() outputs — every weekday
    with its rate, i.e. `best_days(counts, interest, top_n=7)`. A day
    qualifies only if it's at or above an average day (rate >= 1.0) for
    *both* channels; qualifiers are ranked by the weaker of the two rates
    (a day has to be solidly good on both sides, not great on one and
    marginal on the other) and the best `top_n` come back as
    `[(weekday_index, min_rate), …]`, best first. Empty when nothing clears
    the bar on both sides.

    Deliberately not a strict intersection of each channel's own top-2
    best_days: that misses a day sitting at #3 for one channel but still
    clearly above its average, which is exactly the kind of day a
    coordinated post wants."""
    rate_b = {int(d): r for d, r in ranked_b}
    both = [(int(d), min(ra, rate_b.get(int(d), 0.0)))
            for d, ra in ranked_a
            if ra >= 1.0 and rate_b.get(int(d), 0.0) >= 1.0]
    both.sort(key=lambda dr: dr[1], reverse=True)
    return both[:top_n]


def niche_affinity(same_tag: bool, same_folder: bool,
                   folder_niche: float = MUTUAL_PR_FOLDER_NICHE) -> float:
    """How likely the two channels share an audience: `1.0` when they carry
    the same tag (a real niche match), `folder_niche` (small) when they only
    share a folder, else `0.0`. Tags are the taxonomy; a folder is just
    sidebar organization, so it counts for much less — a different-folder
    same-tag pair beats a same-folder unrelated-tag one, and untagged
    channels fall back to the folder-only signal."""
    if same_tag:
        return 1.0
    if same_folder:
        return folder_niche
    return 0.0


def mutual_pr_pair_score(followers_a: int, followers_b: int,
                         forecast24_a: float, forecast24_b: float,
                         best_days_a, best_days_b,
                         same_folder: bool, same_tag: bool = False) -> dict:
    """Compatibility of two channels for an ad swap — a dict with the four
    component sub-scores (each in [0, 1]) plus their weighted `score`, also
    in [0, 1] (the MUTUAL_PR_W_* weights sum to 1.0). See the section
    header above for the formula and the deliberate limitation (no mention
    graph — an already-swapped pair has to be excluded upstream)."""
    sp = size_parity(followers_a, followers_b)
    qp = quality_parity(forecast24_a, forecast24_b)
    do = day_overlap(best_days_a, best_days_b)
    na = niche_affinity(same_tag, same_folder)
    score = (MUTUAL_PR_W_SIZE * sp + MUTUAL_PR_W_QUALITY * qp
             + MUTUAL_PR_W_DAY_OVERLAP * do + MUTUAL_PR_W_NICHE * na)
    return {"size_parity": sp, "quality_parity": qp, "day_overlap": do,
            "niche": na, "score": score}


def _cap_pairs_per_channel(ranked: list[dict], max_per_channel: int) -> list[dict]:
    """`ranked` (already best-first) with every channel's own appearances
    capped at `max_per_channel` — processed in score order, so whatever
    survives for a given channel really is its own top N, not an arbitrary
    subset. A pair is dropped the moment *either* side has already hit its
    cap, so one especially compatible channel can't crowd out pairs that
    don't involve it, and a pair's ranking still reflects both sides having
    room left."""
    counts: dict[str, int] = {}
    kept: list[dict] = []
    for p in ranked:
        key_a, key_b = p["a"].get("key"), p["b"].get("key")
        if counts.get(key_a, 0) >= max_per_channel or counts.get(key_b, 0) >= max_per_channel:
            continue
        counts[key_a] = counts.get(key_a, 0) + 1
        counts[key_b] = counts.get(key_b, 0) + 1
        kept.append(p)
    return kept


def rank_mutual_pr_pairs(channels: list[dict],
                         min_score: float = MUTUAL_PR_MIN_SCORE,
                         max_pairs: int = MUTUAL_PR_MAX_PAIRS,
                         exclude_keys: set[frozenset] | None = None,
                         max_per_channel: int | None = MUTUAL_PR_MAX_PAIRS_PER_CHANNEL
                         ) -> list[dict]:
    """Every unordered pair of `channels` that scores `min_score` or higher
    (mutual_pr_pair_score), returned best-first. `max_pairs` is only a hard
    ceiling for a pathologically large folder, not a target count.

    Each `channels` item must carry: `key` (str — identifies it for
    `exclude_keys`/`max_per_channel`; anything hashable works, even if two
    items happen to share one, since it's only ever compared, never used to
    look anything up here), `followers` (int), `forecast` (the ad_forecast
    dict, for its `"24h"` key), `best_days` (a top-N best_days() output —
    drives the day-overlap score component and each side's headline days),
    `best_days_full` (a best_days(..., top_n=7) output — drives
    `mutual_days`; falls back to `best_days` if absent), `folder_id` (str or
    None), and `tag` (str or None — a shared tag is the main niche signal,
    see niche_affinity). Anything else on the item (label, link, …) is left
    untouched and comes back on the result's `a` / `b`.

    `exclude_keys` — an optional set of `frozenset({key_a, key_b})` pairs to
    drop before scoring even runs, e.g. two channels a real posted link
    already connects (see app.ui.mutual_pr_view, which builds this from
    app.cross_mentions) don't need brokering — this table is for pairs that
    *aren't* already working together.

    `max_per_channel` — once ranked, keep at most this many of any one
    channel's own best pairs (see _cap_pairs_per_channel); None disables the
    cap entirely (every scoring pair up to `max_pairs` survives).

    Each result item: `{"a", "b", "score", "size_parity", "quality_parity",
    "day_overlap", "niche", "days_a", "days_b", "mutual_days"}` —
    `days_a` / `days_b` are each channel's own best weekday indices, and
    `mutual_days` are the weekday indices that work well for *both* (see
    mutual_best_days)."""
    ranked: list[dict] = []
    for i in range(len(channels)):
        for j in range(i + 1, len(channels)):
            a, b = channels[i], channels[j]
            if exclude_keys and frozenset((a.get("key"), b.get("key"))) in exclude_keys:
                continue
            fid_a, fid_b = a.get("folder_id"), b.get("folder_id")
            tag_a, tag_b = a.get("tag"), b.get("tag")
            comp = mutual_pr_pair_score(
                a.get("followers", 0), b.get("followers", 0),
                (a.get("forecast") or {}).get("24h", 0.0),
                (b.get("forecast") or {}).get("24h", 0.0),
                a.get("best_days") or [], b.get("best_days") or [],
                bool(fid_a) and fid_a == fid_b,
                bool(tag_a) and tag_a == tag_b,
            )
            if comp["score"] < min_score:
                continue
            days_a = [int(d) for d, _ in (a.get("best_days") or [])]
            days_b = [int(d) for d, _ in (b.get("best_days") or [])]
            mutual = [d for d, _ in mutual_best_days(
                a.get("best_days_full") or a.get("best_days") or [],
                b.get("best_days_full") or b.get("best_days") or [])]
            ranked.append({"a": a, "b": b, "days_a": days_a, "days_b": days_b,
                           "mutual_days": mutual, **comp})
    ranked.sort(key=lambda p: p["score"], reverse=True)
    if max_per_channel:
        ranked = _cap_pairs_per_channel(ranked, max_per_channel)
    return ranked[:max_pairs]
