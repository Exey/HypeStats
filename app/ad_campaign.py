"""Ad Campaign planner — turns "I want N followers for B money by date D"
into a timeline of paid ad placements across the channels this app tracks.
Shared by app.ui.ad_campaign_view (inputs, recommendations, Gantt); nothing
in here touches Qt or the disk, so it can be exercised on plain dicts.

Three layers live here:

1. Price — the same formula as the reference price list (~/xyd/index.html,
   "Реклама — 22 ₽ за подписчика"): a placement costs the channel's
   forecast follower gain over that placement's period × the price per
   follower, +10% for a channel in a folder named "Models", floored to a
   round step (10 at the default price of 22). The forecast is exactly
   app.scoring_pr.ad_forecast's — the same numbers the Mutual PR view shows
   as 24h / 48h / 72h / Week / Month — so a placement's *width on the
   timeline is its period* (1 / 2 / 3 / 7 / 30 days). The price is a flat
   rate per forecast follower, which means every channel is "fairly" priced
   by construction — choosing between them is a question of who beats their
   own forecast, which is what layer 2 is for.

2. Prime era — is this channel *hot right now*? The price list above is
   built from lifetime averages, so a channel whose audience is currently
   spiking is still sold at its old, cooler price: that gap is the
   opportunity. ``prime_profile`` reads the gap from the channel's monthly
   series (``distributions.monthly``, all own posts, see app.activity):

       reach_gain       views per own post over the last PRIME_RECENT_MONTHS
                        full months ÷ the median month of the last
                        PRIME_WINDOW_MONTHS — is each post landing on more
                        eyes than usual?
       engagement_gain  (shares + 0.05 × reactions) ÷ views, recent ÷ median
                        month — are those eyes reacting/forwarding more?
                        Same weighting app.rating uses (shares in full,
                        reactions at REACTIONS_ENGAGEMENT_WEIGHT).
       momentum M       ln(reach_gain) × (1-w) + ln(engagement_gain) × w,
                        w = PRIME_ENGAGEMENT_WEIGHT, minus
                        PRIME_NEUTRAL_MOMENTUM (what a typical tracked
                        channel scores). Log-scale, so 2× up and 2× down are
                        equally far from typical.
       era              the current run of consecutive full months with
                        views per post ≥ PRIME_ERA_MARGIN × the median — a
                        channel is only "prime" when it's been hot for at
                        least PRIME_ERA_MIN_MONTHS, so a single viral month
                        doesn't count as an era.

   score = 50 + PRIME_SCORE_SLOPE × M (0-100, 50 = business as usual);
   state is prime / warm / steady / cooling from that score. The forecast
   is then scaled by ``multiplier`` = exp(PRIME_MULT_SLOPE × M), clamped to
   [PRIME_MULT_MIN, PRIME_MULT_MAX] — deliberately gentle, because the
   forecast itself already rests on unverified conversion assumptions (see
   app.scoring_pr's docstring) and this is one more heuristic on top. A
   channel app.activity already flags as slowing/stalling has a decline cut
   built into its forecast, so its multiplier is capped at 1.0 rather than
   penalising it twice; an *abandoned* one is never a candidate at all.

   Timing: the same monthly series gives a per-calendar-month seasonal
   factor (a channel's typical month vs its own year, needing
   SEASON_MIN_YEARS of history for that month, shrunk toward 1 and clamped),
   and app.scoring_pr.best_days gives a per-weekday factor (days the
   channel itself posts least, so the ad isn't buried). ``window_factor``
   averages both over the days a placement covers; ``best_start`` picks the
   start day that maximises it.

3. Planner — ``plan_campaign`` greedily fills the budget with the
   best-value placements until the target (plus TARGET_SAFETY headroom,
   since every figure here is an estimate) is covered or the money runs
   out. Value = expected followers ÷ price × niche fit (a shared tag with
   the user's own channel, see app.scoring_pr.niche_affinity). No single
   placement may take more than MAX_SLOT_SHARE of the budget (diversifies
   audiences; also what makes a small channel get a long slot and a big one
   a short slot), no more than MAX_STARTS_PER_DAY placements start the same
   day, a placement expected to bring fewer than MIN_SLOT_EXPECTED
   followers isn't worth booking (that money stays as reserve), and one that
   would overshoot the followers still needed by more than MAX_OVERSHOOT× is
   passed over for a snugger fit (the cheapest such slot is the last resort). ``alternatives`` / ``replace_block`` power the "click a block
   to swap the channel" flow — some channels simply don't sell ads.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median

from .activity import channel_activity_trend
from .rating import REACTIONS_ENGAGEMENT_WEIGHT
from .scoring import post_gauge_value, post_score_raw
from .scoring_pr import (
    ad_forecast, best_days, channel_interest, niche_affinity, size_parity,
)

# ---------------------------------------------------------------- price
DEFAULT_TARGET_FOLLOWERS = 1000
DEFAULT_BUDGET = 15000
DEFAULT_PRICE_PER_FOLLOWER = 22.0   # ~/xyd/index.html AD_RUB_PER_SUB
MODELS_MARKUP = 1.1                 # ~/xyd/index.html AD_MODELS_MARKUP
MODELS_FOLDER = "models"            # folder name (case-insensitive) that earns it

# Placement period -> days it occupies on the timeline. Keys are
# app.scoring_pr.AD_VIEW_CURVE's horizons.
HORIZONS: tuple[tuple[str, int], ...] = (
    ("24h", 1), ("48h", 2), ("72h", 3), ("week", 7), ("month", 30),
)
PERIOD_TWO_WEEKS = 14
PERIOD_ONE_MONTH = 30

# --------------------------------------------------------------- planning
TARGET_SAFETY = 1.10       # plan for 10% over the target — figures are estimates
MAX_SLOT_SHARE = 0.20      # one placement never takes more than this of the budget
MAX_STARTS_PER_DAY = 2
MIN_SLOT_EXPECTED = 10     # a placement expected to bring fewer followers isn't worth booking
MAX_OVERSHOOT = 2.0        # skip a slot expected to bring more than this × the followers still needed
MAX_FIT_BONUS = 0.15       # niche fit can lift a channel's ranking by at most this
SIMILAR_SIZE_PARITY = 0.8  # size_parity at/above which a free swap is suggested
MAX_SWAP_SUGGESTIONS = 3

# ------------------------------------------------------------- prime era
PRIME_WINDOW_MONTHS = 18
PRIME_RECENT_MONTHS = 3
PRIME_MIN_POSTS = 2           # own posts a month needs to count as "active"
PRIME_MIN_ACTIVE_MONTHS = 6   # of the window, else no read at all
PRIME_ERA_MARGIN = 1.10
PRIME_ERA_MIN_MONTHS = 2
PRIME_ENGAGEMENT_WEIGHT = 0.3
# Median momentum across ~200 real checkpoints is about -0.1, not 0: the
# latest months' views are still settling and per-post reach drifts down as
# an audience ages, so a channel at "1.0x its usual" is actually doing
# better than most. Momentum is measured from this point, so 50 / x1.0 is a
# typical channel rather than a slightly-cooling one.
PRIME_NEUTRAL_MOMENTUM = -0.10
PRIME_SCORE_SLOPE = 70.0
PRIME_MULT_SLOPE = 0.35
PRIME_MULT_MIN = 0.80
PRIME_MULT_MAX = 1.25
PRIME_GAIN_CLAMP = (0.2, 5.0)  # keeps ln() sane on a near-empty month
STATE_PRIME_MIN = 65.0
STATE_WARM_MIN = 55.0
STATE_STEADY_MIN = 40.0
STATES = ("prime", "warm", "steady", "cooling", "unknown")

SEASON_MIN_YEARS = 2
SEASON_MIN_YEAR_MONTHS = 6    # active months a year needs to serve as its own baseline
SEASON_SHRINK = 2             # pseudo-observations at 1.0 in the shrink
SEASON_CLAMP = (0.85, 1.15)
WEEKDAY_CLAMP = (0.95, 1.10)


# =====================================================================
# Price
# =====================================================================
def price_step(price_per_follower: float) -> float:
    """Rounding step for a placement price: the power of ten at the price
    per follower's own scale — 10 for the reference 22 (as in the price
    list), 1 for 2.5, 0.1 for 0.25 — so a small-denomination currency isn't
    floored to zero."""
    if price_per_follower <= 0:
        return 1.0
    return 10.0 ** math.floor(math.log10(price_per_follower))


def floor_to(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(math.floor(value / step + 1e-9) * step, 6)


def placement_price(forecast_followers: float, price_per_follower: float,
                    is_model: bool = False) -> float:
    """~/xyd/index.html annotateAds: forecast × price per follower, +10% for
    a Models-folder channel, floored to price_step. Always from the *base*
    forecast — prime-era/timing gains are the buyer's edge, not priced in."""
    markup = MODELS_MARKUP if is_model else 1.0
    return floor_to(forecast_followers * price_per_follower * markup,
                    price_step(price_per_follower))


# =====================================================================
# Prime era
# =====================================================================
def _own(month: dict, field: str) -> int:
    return int(month.get(f"{field}_own", month.get(field, 0)) or 0)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _engagement(month_or_sums: dict) -> float:
    views = month_or_sums["views"]
    if views <= 0:
        return 0.0
    return (month_or_sums["shares"]
            + REACTIONS_ENGAGEMENT_WEIGHT * month_or_sums["reactions"]) / views


def _month_rows(monthly: list[dict] | None) -> list[dict]:
    """Full months only (the last one is still filling, as in
    app.activity), own posts only."""
    months = [m for m in (monthly or []) if m][:-1]
    return [{
        "label": m.get("label", ""),
        "n": _own(m, "count"),
        "views": _own(m, "views"),
        "reactions": _own(m, "reactions"),
        "shares": _own(m, "shares"),
    } for m in months]


def seasonal_factors(rows: list[dict]) -> dict[int, float]:
    """calendar month (1-12) -> factor. Each active month's views per post
    ÷ the median of its own year, averaged per calendar month, shrunk toward
    1.0 (SEASON_SHRINK) and clamped. A month with fewer than
    SEASON_MIN_YEARS of history stays at 1.0."""
    by_year: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["n"] >= PRIME_MIN_POSTS and len(r["label"]) >= 7:
            by_year[r["label"][:4]].append(r)
    ratios: dict[int, list[float]] = defaultdict(list)
    for months in by_year.values():
        if len(months) < SEASON_MIN_YEAR_MONTHS:
            continue
        base = median(m["views"] / m["n"] for m in months)
        if base <= 0:
            continue
        for m in months:
            try:
                cal = int(m["label"][5:7])
            except ValueError:
                continue
            ratios[cal].append(m["views"] / m["n"] / base)
    out: dict[int, float] = {}
    for cal in range(1, 13):
        seen = ratios.get(cal, [])
        if len(seen) < SEASON_MIN_YEARS:
            out[cal] = 1.0
            continue
        mean = sum(seen) / len(seen)
        shrunk = (mean * len(seen) + SEASON_SHRINK) / (len(seen) + SEASON_SHRINK)
        out[cal] = _clamp(shrunk, *SEASON_CLAMP)
    return out


def _state_for(score: float, era_months: int) -> str:
    if score >= STATE_PRIME_MIN:
        return "prime" if era_months >= PRIME_ERA_MIN_MONTHS else "warm"
    if score >= STATE_WARM_MIN:
        return "warm"
    if score >= STATE_STEADY_MIN:
        return "steady"
    return "cooling"


def prime_profile(monthly: list[dict] | None, trend: dict | None = None) -> dict:
    """See the module docstring, layer 2. Always returns a dict with every
    key present; `state` is "unknown" (score 50, multiplier 1.0) when the
    monthly series is too thin to read."""
    rows = _month_rows(monthly)
    season = seasonal_factors(rows)
    neutral = {"state": "unknown", "score": 50.0, "multiplier": 1.0,
               "reach_gain": None, "engagement_gain": None,
               "era_since": None, "era_months": 0, "season": season}
    window = rows[-PRIME_WINDOW_MONTHS:]
    active = [r for r in window if r["n"] >= PRIME_MIN_POSTS]
    if len(active) < PRIME_MIN_ACTIVE_MONTHS:
        return neutral

    base_reach = median(r["views"] / r["n"] for r in active)
    base_eng = median(_engagement(r) for r in active)
    recent = window[-PRIME_RECENT_MONTHS:]
    recent_posts = sum(r["n"] for r in recent)
    if base_reach <= 0 or recent_posts == 0:
        return {**neutral, "state": "cooling", "score": 0.0,
                "multiplier": PRIME_MULT_MIN, "reach_gain": 0.0}

    sums = {k: sum(r[k] for r in recent) for k in ("views", "reactions", "shares")}
    lo, hi = PRIME_GAIN_CLAMP
    reach_gain = _clamp(sums["views"] / recent_posts / base_reach, lo, hi)
    eng_gain = (_clamp(_engagement(sums) / base_eng, lo, hi)
                if base_eng > 0 else 1.0)
    momentum = (math.log(reach_gain) * (1 - PRIME_ENGAGEMENT_WEIGHT)
                + math.log(eng_gain) * PRIME_ENGAGEMENT_WEIGHT
                - PRIME_NEUTRAL_MOMENTUM)

    era_months = 0
    era_since = None
    era_reach = 0.0
    for r in reversed(window):
        if r["n"] >= PRIME_MIN_POSTS and r["views"] / r["n"] >= PRIME_ERA_MARGIN * base_reach:
            era_months += 1
            era_since = r["label"]
            era_reach += r["views"] / r["n"]
        else:
            break

    score = _clamp(50.0 + PRIME_SCORE_SLOPE * momentum, 0.0, 100.0)
    multiplier = _clamp(math.exp(PRIME_MULT_SLOPE * momentum),
                        PRIME_MULT_MIN, PRIME_MULT_MAX)
    if trend and trend.get("verdict") in ("slowing", "stalling", "abandoned"):
        multiplier = min(multiplier, 1.0)   # its forecast already carries a decline cut
    return {
        "state": _state_for(score, era_months),
        "score": score,
        "multiplier": multiplier,
        "reach_gain": reach_gain,
        "engagement_gain": eng_gain,
        "era_since": era_since if era_months >= PRIME_ERA_MIN_MONTHS else None,
        "era_months": era_months if era_months >= PRIME_ERA_MIN_MONTHS else 0,
        "season": season,
    }


# =====================================================================
# Candidates
# =====================================================================
def channel_label(ch: dict) -> str:
    username = ch.get("username") or ""
    if username:
        return f"@{username}"
    return ch.get("title") or ch.get("channel") or ch.get("key", "?")


def channel_ref(ch: dict) -> str:
    """Identifier to resolve/link a channel — same fallback order as
    app.ui.content_quality_view._channel_ref."""
    return ch.get("channel") or ch.get("username") or ch.get("key", "")


def channel_avg_views(ch: dict) -> float:
    """Same reference app.ui.content_quality_view uses for the post-quality
    gauge's viral-excess term: recent average, else lifetime."""
    stats = ch.get("stats", {}) or {}
    return float(stats.get("avg_views_recent") or stats.get("avg_views", 0) or 0)


def candidate_from_checkpoint(data: dict) -> dict | None:
    """One channel's everything-the-planner-needs, or None when it can't be
    advertised in (no followers/forecast, or posting has all but stopped).

    The forecast recipe mirrors app.ui.mutual_pr_view._reload_entries so a
    price here is the one the Mutual PR table would give. Holds no post rows
    — cheap to keep cached for hundreds of channels."""
    key = data.get("key", "")
    stats = data.get("stats", {}) or {}
    info = data.get("info", {}) or {}
    followers = int(info.get("members", 0) or 0)
    if followers <= 0:
        return None
    rows = data.get("rows", []) or []
    dist = data.get("distributions", {}) or {}
    trend = channel_activity_trend(dist.get("monthly"))
    if trend and trend.get("verdict") == "abandoned":
        return None
    avg_views = float(stats.get("avg_views", 0) or 0)
    interest = channel_interest(rows, avg_views)
    forecast = ad_forecast(
        float(stats.get("avg_views_settled", 0) or 0), interest,
        float(stats.get("avg_posts_per_day", 0) or 0),
        int(stats.get("total_posts", 0) or 0), followers,
        float(stats.get("viral_post_share", 0) or 0), rows, activity_trend=trend)
    if forecast.get("24h", 0) <= 0:
        return None
    weekday_counts = dist.get("weekday") or [0] * 7
    rates = {int(d): _clamp(rate, *WEEKDAY_CLAMP)
             for d, rate in best_days(weekday_counts, interest, top_n=7)}
    return {
        "key": key,
        "label": channel_label(data),
        "followers": followers,
        "forecast": forecast,
        "weekday_rates": rates,
        "best_days": best_days(weekday_counts, interest, top_n=2),
        "prime": prime_profile(dist.get("monthly"), trend),
        "trend": trend,
        "is_model": False,   # folder-derived — the caller sets these
        "tag": None,
        "folder_id": None,
    }


# =====================================================================
# Own channel
# =====================================================================
def _norm_ref(text: str) -> str:
    v = re.sub(r"^(https?://)?(www\.)?t\.me/", "", (text or "").strip(),
               flags=re.IGNORECASE)
    return v.split("/")[0].split("?")[0].lstrip("@").lower()


def find_own_channel(query: str, summaries: list[dict]) -> dict | None:
    """The tracked channel (a ChannelStore.list() summary) a typed
    ID/@username/t.me link/title refers to, or None. Matches, in order:
    username, numeric channel id (with or without the -100 prefix), the ref
    the channel was fetched with, its key, then its exact title."""
    q = _norm_ref(query)
    if not q:
        return None
    digits = q[4:] if q.startswith("-100") else q.lstrip("-")
    for s in summaries:
        if (s.get("username") or "").lower() == q:
            return s
    if digits.isdigit():
        for s in summaries:
            if str(s.get("channel_id") or "") == digits:
                return s
    for s in summaries:
        if _norm_ref(s.get("channel") or "") == q or (s.get("key") or "").lower() == q:
            return s
    title = (query or "").strip().lower()
    for s in summaries:
        if (s.get("title") or "").strip().lower() == title:
            return s
    return None


def best_posts(data: dict, limit: int = 3) -> list[dict]:
    """The `limit` highest-Quality posts of a channel checkpoint — the ones
    worth reposting into partner channels or lifting media from. Quality is
    the app-wide post gauge (app.scoring); posts scoring 0 (ad-button posts,
    reposts of other channels' content) can't be recommended. Each result:
    {"row", "raw_score", "gauge"}."""
    avg_views = channel_avg_views(data)
    scored = []
    for row in data.get("rows", []) or []:
        raw = post_score_raw(row, avg_views)
        if raw > 0:
            scored.append({"row": row, "raw_score": raw,
                           "gauge": post_gauge_value(raw)})
    scored.sort(key=lambda s: s["gauge"], reverse=True)
    return scored[:limit]


# =====================================================================
# Timing
# =====================================================================
def day_factor(cand: dict, day: date) -> float:
    """Weekday × seasonal factor for one calendar day."""
    weekday = cand["weekday_rates"].get(day.weekday(), 1.0)
    season = cand["prime"]["season"].get(day.month, 1.0)
    return weekday * season


def window_factor(cand: dict, start: date, days: int) -> float:
    """Mean day_factor over the `days` a placement covers."""
    days = max(1, days)
    return sum(day_factor(cand, start + timedelta(n)) for n in range(days)) / days


def expected_followers(cand: dict, horizon: str, start: date, days: int) -> float:
    return (cand["forecast"][horizon] * cand["prime"]["multiplier"]
            * window_factor(cand, start, days))


def best_start(cand: dict, days: int, window_start: date, window_days: int,
               starts: Counter | None = None) -> date | None:
    """Start day for a `days`-long placement inside the window with the
    highest window_factor (earliest on a tie), skipping days that already
    hold MAX_STARTS_PER_DAY placements unless every day does. Linear in the
    window (prefix sums of day_factor), so an "until date" a year out stays
    instant."""
    last_offset = window_days - days
    if last_offset < 0:
        return None
    prefix = [0.0]
    for n in range(window_days):
        prefix.append(prefix[-1] + day_factor(cand, window_start + timedelta(n)))
    offsets = sorted(range(last_offset + 1),
                     key=lambda o: (-(prefix[o + days] - prefix[o]), o))
    if starts is not None:
        for o in offsets:
            if starts[o] < MAX_STARTS_PER_DAY:
                return window_start + timedelta(o)
    return window_start + timedelta(offsets[0])


# =====================================================================
# Planner
# =====================================================================
def niche_fit(cand: dict, own: dict | None) -> float:
    """1.0 … 1+MAX_FIT_BONUS: shared tag/folder with the user's own channel
    (app.scoring_pr.niche_affinity), 1.0 when there is no own channel."""
    if not own:
        return 1.0
    same_tag = bool(cand.get("tag")) and cand.get("tag") == own.get("tag")
    same_folder = bool(cand.get("folder_id")) and cand.get("folder_id") == own.get("folder_id")
    return 1.0 + MAX_FIT_BONUS * niche_affinity(same_tag, same_folder)


def make_block(cand: dict, horizon: str, start: date, price_per_follower: float,
               own: dict | None = None) -> dict:
    days = dict(HORIZONS)[horizon]
    price = placement_price(cand["forecast"][horizon], price_per_follower,
                            bool(cand.get("is_model")))
    expected = expected_followers(cand, horizon, start, days)
    return {
        "key": cand["key"],
        "label": cand["label"],
        "followers": cand["followers"],
        "is_model": bool(cand.get("is_model")),
        "horizon": horizon,
        "days": days,
        "start": start,
        "price": price,
        "base_forecast": cand["forecast"][horizon],
        "expected": expected,
        "prime": cand["prime"],
        "rank": (expected / price * niche_fit(cand, own)) if price > 0 else 0.0,
    }


def _fits(price: float, remaining: float, cap: float) -> bool:
    return 0 < price <= min(remaining, cap)


def _slot_for(cand: dict, remaining: float, cap: float, price_per_follower: float,
              window_start: date, window_days: int, starts: Counter,
              own: dict | None) -> dict | None:
    """The placement to book on `cand`: the longest period that fits the
    window, the per-slot cap and what's left of the budget — small channels
    end up with long slots, big ones with short ones — at that period's best
    start day."""
    pick = None
    for horizon, days in HORIZONS:
        if days > window_days:
            continue
        price = placement_price(cand["forecast"][horizon], price_per_follower,
                                bool(cand.get("is_model")))
        if _fits(price, remaining, cap):
            pick = (horizon, days)
    if pick is None:
        return None
    horizon, days = pick
    start = best_start(cand, days, window_start, window_days, starts)
    if start is None:
        return None
    return make_block(cand, horizon, start, price_per_follower, own)


def plan_campaign(candidates: list[dict], *, target: int, budget: float,
                  price_per_follower: float, window_start: date, window_days: int,
                  own: dict | None = None, excluded: set[str] | frozenset = frozenset()
                  ) -> list[dict]:
    """Blocks (see make_block), sorted by start date. Greedy by value —
    see the module docstring, layer 3."""
    if target <= 0 or budget <= 0 or price_per_follower <= 0 or window_days <= 0:
        return []
    own_key = (own or {}).get("key")
    pool = [c for c in candidates if c["key"] != own_key and c["key"] not in excluded]
    cap = budget * MAX_SLOT_SHARE
    min_expected = min(MIN_SLOT_EXPECTED, target * 0.1)

    starts: Counter = Counter()
    ranked = []
    for cand in pool:
        block = _slot_for(cand, budget, cap, price_per_follower, window_start,
                          window_days, starts, own)
        if block and block["expected"] >= min_expected:
            ranked.append((block["rank"], cand))
    ranked.sort(key=lambda rc: rc[0], reverse=True)

    need = target * TARGET_SAFETY
    remaining = float(budget)
    blocks: list[dict] = []
    oversized: list[dict] = []   # would overshoot what's still needed — see below

    def book(block: dict) -> None:
        nonlocal need, remaining
        starts[(block["start"] - window_start).days] += 1
        blocks.append(block)
        remaining -= block["price"]
        need -= block["expected"]

    for _rank, cand in ranked:
        if need <= 0 or remaining <= 0:
            break
        block = _slot_for(cand, remaining, cap, price_per_follower, window_start,
                          window_days, starts, own)
        if block is None or block["expected"] < min_expected:
            continue
        if block["expected"] > need * MAX_OVERSHOOT:
            oversized.append(cand)
            continue
        book(block)

    # Only slots that overshoot are left: rather than nothing, finish with
    # the cheapest of them (a target of 1 shouldn't buy the priciest slot,
    # but it shouldn't buy none either).
    if need > 0 and remaining > 0 and oversized:
        finishers = []
        for cand in oversized:
            block = _slot_for(cand, remaining, cap, price_per_follower, window_start,
                              window_days, starts, own)
            if block is not None:
                finishers.append(block)
        if finishers:
            book(min(finishers, key=lambda b: b["price"]))
    blocks.sort(key=lambda b: (b["start"], -b["price"], b["label"]))
    return blocks


def alternatives(candidates: list[dict], blocks: list[dict], index: int, *,
                 budget: float, price_per_follower: float,
                 own: dict | None = None, excluded: set[str] | frozenset = frozenset(),
                 limit: int = 12) -> list[dict]:
    """Best replacement blocks for `blocks[index]`: same start day and
    period, a channel that isn't already in the plan / excluded / the user's
    own, and whose price still fits the budget once the old block's is freed.
    Best value first."""
    old = blocks[index]
    taken = {b["key"] for b in blocks}
    own_key = (own or {}).get("key")
    free = budget - sum(b["price"] for b in blocks) + old["price"]
    out = []
    for cand in candidates:
        if cand["key"] in taken or cand["key"] in excluded or cand["key"] == own_key:
            continue
        block = make_block(cand, old["horizon"], old["start"], price_per_follower, own)
        if 0 < block["price"] <= free:
            out.append(block)
    out.sort(key=lambda b: b["rank"], reverse=True)
    return out[:limit]


# =====================================================================
# Summary + recommendations
# =====================================================================
def plan_totals(blocks: list[dict], target: int, budget: float,
                price_per_follower: float) -> dict:
    spend = sum(b["price"] for b in blocks)
    expected = sum(b["expected"] for b in blocks)
    return {
        "spend": spend,
        "expected": expected,
        "slots": len(blocks),
        "target_pct": (expected / target * 100) if target > 0 else 0.0,
        "reserve": max(0.0, budget - spend),
        "cost_per_follower": (spend / expected) if expected > 0 else None,
        "prime_slots": sum(1 for b in blocks if b["prime"]["state"] == "prime"),
        "affordable": (budget / price_per_follower) if price_per_follower > 0 else 0.0,
    }


def _fmt_gain(gain: float | None) -> str:
    return f"{(gain - 1) * 100:+.0f}%" if gain is not None else "—"


def swap_suggestions(candidates: list[dict], own: dict | None,
                     own_followers: int) -> list[dict]:
    """Similar-size channels (size_parity ≥ SIMILAR_SIZE_PARITY) worth a
    free ad swap instead of a paid slot — most niche-fitting, then most
    prime, first."""
    if not own or own_followers <= 0:
        return []
    out = []
    for cand in candidates:
        if cand["key"] == own.get("key"):
            continue
        parity = size_parity(own_followers, cand["followers"])
        if parity >= SIMILAR_SIZE_PARITY:
            out.append((niche_fit(cand, own), cand["prime"]["score"], cand))
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [c for _f, _s, c in out[:MAX_SWAP_SUGGESTIONS]]


def recommendations(blocks: list[dict], candidates: list[dict], *, target: int,
                    budget: float, price_per_follower: float, window_days: int,
                    own: dict | None, own_followers: int, excluded_count: int) -> list[dict]:
    """Structured advice — [{"kind", "level": good|warn|info, ...params}].
    Text lives in the UI layer (i18n); numbers stay raw here, apart from the
    language-neutral "+38%" gain strings."""
    totals = plan_totals(blocks, target, budget, price_per_follower)
    out: list[dict] = []

    full_price = target * price_per_follower
    out.append({"kind": "cost_check", "level": "info", "target": target,
                "price": price_per_follower, "cost": full_price, "budget": budget,
                "affordable": totals["affordable"]})
    if window_days > 0 and target > 0:
        out.append({"kind": "pacing", "level": "info",
                    "per_day": target / window_days, "days": window_days})

    if not blocks:
        out.append({"kind": "no_slots", "level": "warn"})
    else:
        gap = target - totals["expected"]
        if gap <= 0:
            out.append({"kind": "target_met", "level": "good",
                        "expected": totals["expected"], "spend": totals["spend"],
                        "reserve": totals["reserve"], "safety_pct": (TARGET_SAFETY - 1) * 100})
        else:
            cpf = totals["cost_per_follower"] or price_per_follower
            out.append({"kind": "target_short", "level": "warn",
                        "expected": totals["expected"], "pct": totals["target_pct"],
                        "extra_budget": gap * cpf,
                        "need_budget": totals["spend"] + gap * cpf, "cpf": cpf})

        prime = sorted((b for b in blocks if b["prime"]["state"] == "prime"),
                       key=lambda b: b["prime"]["score"], reverse=True)
        if prime:
            top = [{"label": b["label"], "gain": _fmt_gain(b["prime"]["reach_gain"]),
                    "since": b["prime"]["era_since"]} for b in prime[:3]]
            out.append({"kind": "prime_top", "level": "good", "count": len(prime),
                        "total": len(blocks), "top": top})
        else:
            out.append({"kind": "no_prime", "level": "info", "total": len(blocks)})

        first = min(blocks, key=lambda b: b["start"])
        last = max(blocks, key=lambda b: b["start"] + timedelta(b["days"]))
        out.append({"kind": "timing", "level": "info", "first": first["start"],
                    "first_label": first["label"],
                    "last_end": last["start"] + timedelta(last["days"] - 1)})

    if excluded_count:
        out.append({"kind": "excluded", "level": "info", "count": excluded_count})

    if own:
        swaps = swap_suggestions(candidates, own, own_followers)
        if swaps:
            out.append({"kind": "swap", "level": "info",
                        "names": ", ".join(c["label"] for c in swaps)})
    return out
