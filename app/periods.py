"""Month/season period-key helpers shared by the dashboard trend chart and
the Folder Stats view, so both group `distributions.monthly` entries into
the same season buckets. Also the "Year mode" rolling-window options shared
by Folder Stats and High-Quality Posts (see YEAR_WINDOW_OPTIONS)."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

SEASON_BY_MONTH = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer",
    9: "Fall", 10: "Fall", 11: "Fall",
}
SEASON_ORDER = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}
# Spring gets "P" (not "S") since Summer already claims that letter — the
# 4 stay distinct: F(all)/W(inter)/P(spring)/S(ummer).
SEASON_LETTER = {"Winter": "W", "Spring": "P", "Summer": "S", "Fall": "F"}


def period_key_label(year: int, month: int, mode: str) -> tuple[tuple, str]:
    """(sort_key, display_label) for a calendar month, grouped into a season
    when mode == "season" ("Fall 2025", "Winter 2025/26") or mode ==
    "season_short" (same grouping, "25/F", "25/W" — compact form for a
    chart/table whose displayed range is long enough that full names take
    up too much room, e.g. dashboard_view's trend chart past 2 years), a
    calendar half (Jan-Jun / Jul-Dec) when mode == "halfyear", a whole
    calendar year ("2026", Jan-Dec) when mode == "year", else the month
    itself. The sort key is identical for "season"/"season_short" —
    only the label differs — so bucketing by it isn't affected by which
    label form a caller asked for."""
    if mode in ("season", "season_short"):
        season = SEASON_BY_MONTH[month]
        year = year + 1 if month == 12 else year
        if mode == "season_short":
            lead_year = year - 1 if season == "Winter" else year
            label = f"{str(lead_year)[2:]}/{SEASON_LETTER[season]}"
        elif season == "Winter":
            label = f"Winter {year - 1}/{str(year)[2:]}"
        else:
            label = f"{season} {year}"
        return (year, SEASON_ORDER[season]), label
    if mode == "halfyear":
        half = 1 if month <= 6 else 2
        return (year, half), f"{year} H{half}"
    if mode == "year":
        return (year,), str(year)
    return (year, month), f"{year:04d}-{month:02d}"


# A calendar year picked in the Folders export is read with this lag: posts
# newer than this many months haven't gathered their views yet, so counting
# them would drag a year's numbers down. See year_lag_last_month.
YEAR_LAG_MONTHS = 2


def year_lag_last_month(today: date | None = None,
                        lag_months: int = YEAR_LAG_MONTHS) -> tuple[int, int]:
    """(year, month) of the last *complete* calendar month that ends on or
    before `today` minus `lag_months` — on Oct 14 with a 2-month lag the
    cutoff is Aug 14, so July is the last month included (August is still
    only partly past it). Granularity is a month because the checkpoints'
    per-month series (`distributions.monthly`) is what year totals come from."""
    today = today or date.today()
    y, m = today.year, today.month - lag_months
    while m < 1:
        y, m = y - 1, m + 12
    cutoff = date(y, m, min(today.day, calendar.monthrange(y, m)[1]))
    if cutoff.day == calendar.monthrange(y, m)[1]:
        return y, m
    return (y, m - 1) if m > 1 else (y - 1, 12)


# "Year mode" sub-options: (key, i18n label key, rolling window in days —
# None for "All Fetched Time", i.e. no cutoff at all). A flat rolling
# window from today, not a calendar year — "Last Year" means the last 365
# days, not e.g. all of 2026.
YEAR_WINDOW_OPTIONS = [
    ("half", "period_year_half", 182),
    ("1y", "period_year_1y", 365),
    ("1.5y", "period_year_1_5y", 547),
    ("2y", "period_year_2y", 730),
    ("all", "period_year_all", None),
]


def year_window_cutoff(window_days: int | None) -> datetime | None:
    """UTC cutoff datetime for a Year-mode window's day count, or None for
    no cutoff (the "All Fetched Time" option)."""
    if window_days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=window_days)
