"""Channel activity-trend signal — is a channel still going, or coasting on a
strong past that the all-time Rating / Views / Post Quality still reflect?

Every checkpoint stores a gap-filled monthly series (``distributions.monthly``,
see app.tools.channel_stat._monthly_series). ``channel_activity_trend`` reads
it and compares the most recent 12 complete months against the 12 before them
(the final, still-filling month is always dropped first):

    reach    — total own-post views, recent year vs prior year (the prior
               year is annualized when it's partial, so a channel with only
               ~18 months of history is still judged fairly). The headline
               number: a channel that posts half as often *or* gets half the
               views per post lands here.
    views    — views per own-post, same two years — isolates "each post
               reaches fewer people now" from "just posts less often".
    cadence  — own-posts per month, trailing 6 complete months vs the prior
               12 — a sharper, more recent read on posting having slowed
               (what "35/mo → 10/mo" looks like).
    forwards — forwards per own-post, same trailing-6-vs-prior-12 window —
               content that used to travel and now doesn't. Reported only,
               never gates the verdict: one viral month swings it too hard.

verdict:
    abandoned — posting has all but stopped (nothing in the trailing 6
                months, or cadence down to <15% of before).
    stalling  — yearly reach is under 40% of the prior year.
    slowing   — yearly reach is under 70%.
    active    — everything else.
    None      — under ~16 months of history, or a prior year too thin
                (<12 own posts) to compare against.

Own-post totals (``*_own``) exclude reposts, matching app.rating; older
checkpoints without those fields fall back to the all-post counts.
"""
from __future__ import annotations

_YEAR = 12
_TRAIL = 6
_MIN_PRIOR_MONTHS = 4
_MIN_PRIOR_POSTS = 12

VERDICTS = ("active", "slowing", "stalling", "abandoned")


def _own(month: dict, field: str) -> int:
    return int(month.get(f"{field}_own", month.get(field, 0)) or 0)


def _total(months: list[dict], field: str) -> int:
    return sum(_own(m, field) for m in months)


def _per_post(months: list[dict], field: str) -> float:
    posts = _total(months, "count")
    return _total(months, field) / posts if posts else 0.0


def _ratio(now: float, before: float) -> float | None:
    return round(now / before, 2) if before else None


def channel_activity_trend(monthly: list[dict] | None) -> dict | None:
    """See the module docstring. `monthly` is a checkpoint's
    ``distributions.monthly`` list; returns the trend dict or None when
    there isn't enough history to judge."""
    months = [m for m in (monthly or []) if m]
    if len(months) < 2:
        return None
    months = months[:-1]  # the last month is still accumulating views/posts
    if len(months) < _YEAR + _MIN_PRIOR_MONTHS:
        return None

    recent = months[-_YEAR:]
    prior = months[-2 * _YEAR:-_YEAR] or months[:-_YEAR]
    if len(prior) < _MIN_PRIOR_MONTHS or _total(prior, "count") < _MIN_PRIOR_POSTS:
        return None

    # Annualize a partial prior year so a younger channel isn't judged
    # against a shorter window than its recent one.
    prior_views_annualized = _total(prior, "views") / len(prior) * len(recent)
    reach_ratio = _ratio(_total(recent, "views"), prior_views_annualized)
    views_ratio = _ratio(_per_post(recent, "views"), _per_post(prior, "views"))

    trail = months[-_TRAIL:]
    prior_cad = months[-(_TRAIL + _YEAR):-_TRAIL] or months[:-_TRAIL]
    trail_posts = _total(trail, "count")
    cadence_ratio = _ratio(trail_posts / len(trail),
                           _total(prior_cad, "count") / len(prior_cad)) if prior_cad else None
    forward_ratio = _ratio(_per_post(trail, "shares"), _per_post(prior_cad, "shares"))

    reach = reach_ratio if reach_ratio is not None else 1.0
    if trail_posts == 0 or (cadence_ratio is not None and cadence_ratio < 0.15):
        verdict = "abandoned"
    elif reach < 0.4:
        verdict = "stalling"
    elif reach < 0.7:
        verdict = "slowing"
    else:
        verdict = "active"

    return {
        "verdict": verdict,
        "reach_ratio": reach_ratio,
        "views_ratio": views_ratio,
        "cadence_ratio": cadence_ratio,
        "forward_ratio": forward_ratio,
        "recent_posts": _total(recent, "count"),
        "recent_months": len(recent),
        "prior_months": len(prior),
    }
