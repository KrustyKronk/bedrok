"""Is a series fresh? One rule, used by `validate` and the MCP server.

Two tests, the second only where a series has a release rule
(core/releases.py, bedrok/seeds/release_rules.csv):

  1. Age. A series is stale when its newest observation is older than its
     allowance: the catalog's `staleness_days` when set, else the frequency
     default below, and for a member of a bulk family (Eurostat per-geo sets,
     World Bank, CFTC) the family's allowance. Ages run from the period-START
     label — a "May" monthly point is dated 05-01 and publishes in late June —
     so the allowances include the label lag as well as the publication lag.
  2. The next release. Age alone cannot see one: on 2026-10-15 the August CPI
     was 75 days old against 90 and passed as current the morning September
     came out. Where a rule says when the next period is usually out, the
     status is `fresh` before that, `due` inside the window and `late` past
     it. Staleness still overrides both, so a wrong rule cannot hide a broken
     feed; a seed test keeps every rule inside its series' allowance. Where
     the publisher has stated the date (the store's release_calendar, filled
     by ingest/calendars.py), that date replaces the rule's window. Reading it
     is offline: `assess` never fetches.

Session series (never stored) have no stored age; `assess` reports them as
such rather than as stale, with their next release when a rule names it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import duckdb

from bedrok.core import releases
from bedrok.ingest import derived, pipeline

# Max acceptable age in days, by frequency. monthly ≈ 31 + ~50 days to publish;
# quarterly ≈ 92 + ~120. 'event' = change-date series (ECB policy rates) that
# legitimately sit unchanged for long stretches.
STALENESS_ALLOWANCE = {"daily": 10, "weekly": 21, "monthly": 90,
                       "quarterly": 220, "annual": 450, "event": 450}
DEFAULT_ALLOWANCE = 45


def allowance(frequency: str | None, staleness_days, series_id: str = "") -> int:
    """Days a series may go without a new observation before it counts as stale."""
    if staleness_days is not None and str(staleness_days).strip() not in ("", "nan", "None"):
        return int(float(staleness_days))
    for _, prefix, allow in pipeline.bulk_summary_specs():
        if series_id.startswith(prefix):
            return int(allow)
    return STALENESS_ALLOWANCE.get((frequency or "monthly").lower(), DEFAULT_ALLOWANCE)


def _calendar(con, series_id: str, rule: dict, w: dict) -> dict | None:
    """The publisher's own date for the period `w` expects, or None. An entry
    naming the period wins; otherwise the first date after the period is over
    (BEA publishes August's PCE on 30 September, so "after September ends"
    must start on 1 October). Bounded by the rule: an entry far past its
    window is not believed."""
    key = releases.calendar_key(series_id)
    if not key:
        return None
    feed, name = key
    tz = ZoneInfo(releases.FEEDS[feed]["tz"])
    end = releases.period_end(w["label"], rule["frequency"])
    after = datetime.combine(end + timedelta(days=1), time(0), tzinfo=tz)
    reach = w["late_after"] + releases.CALENDAR_REACH
    utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
    try:
        row = con.execute(
            "SELECT due_at FROM release_calendar WHERE feed = ? AND release_name = ? "
            "AND due_at <= ? AND (period = ? OR (period IS NULL AND due_at >= ?)) "
            "ORDER BY period IS NULL, due_at LIMIT 1",
            [feed, name, utc(reach), w["label"], utc(after)]).fetchone()
    except duckdb.CatalogException:          # a store from before the table
        return None
    if row is None:
        return None
    return releases.calendar_window(feed, w["label"], row[0].replace(tzinfo=timezone.utc))


def _window(con, series_id: str, last: date | None, now: datetime) -> tuple[dict, str] | None:
    """(window of the next period, its frequency) from the publisher's own
    date or the series' rule, or for a derived series with neither, the latest
    of its inputs': a spread is out when both legs are."""
    rule = releases.rule_for(series_id)
    if rule:
        w = releases.resolve(rule, last, now)
        if last is not None:
            w = _calendar(con, series_id, rule, w) or w
        return w, rule["frequency"]
    spec = next((d for d in derived.DERIVED if d["series_id"] == series_id), None)
    found = [f for f in (_window(con, i, last, now) for i in (spec or {}).get("inputs", [])) if f]
    # Inputs at the series' own pace only: a monthly rate's rule says nothing
    # about a quarterly sum built from it.
    found = [f for f in found if f[1] == found[0][1]] if found else []
    return max(found, key=lambda f: f[0]["due_at"]) if found else None


def assess(con, series_id: str, today: date | None = None, *,
           now: datetime | None = None) -> dict | None:
    """Freshness of one catalog series, or None when the id is not in the catalog.

    {series_id, cache, first_date, last_date, latest_period, future_to, n_obs,
     age_days, allowance_days, stale, status, next_release} where status is 'fresh',
    'due', 'late', 'stale', 'empty' (a stored series with nothing stored yet)
    or 'session' (never stored by design), and next_release is None for a
    series without a release rule, else what core/releases.describe returns.

    `last_date` is the latest observation dated today or earlier. A series that
    also carries observations dated later (IMF WEO forecasts to 2031, ECB SPF
    horizons) reports how far they run as `future_to`, and its age is measured
    from `last_date`: a forecast must never make a series look fresh.

    `latest_period` names the period `last_date` stands for ("2025", "Q2
    2026", "August 2026"). Age runs from a period's first day, which is the
    rule, but "651 days" for 2025's annual value in October 2026 read as
    neglect in the D2 acceptance test; the period's name says what is held.

    `today` alone (tests, a past date) puts `now` at its noon UTC; `now` alone
    sets `today` from it."""
    row = con.execute(
        "SELECT frequency, staleness_days, coalesce(cache, 'store') FROM macro_catalog "
        "WHERE series_id = ?", [series_id]).fetchone()
    if row is None:
        return None
    freq, sd, cache = row
    allow = allowance(freq, sd, series_id)
    if now is None:
        now = (datetime.combine(today, time(12), tzinfo=timezone.utc) if today
               else datetime.now(timezone.utc))
    today = today or now.date()
    lo, hi, top, n = con.execute(
        "SELECT min(date), max(date) FILTER (WHERE date <= ?), max(date), count(*) "
        "FROM macro_series WHERE series_id = ?", [today, series_id]).fetchone()
    out = {"series_id": series_id, "cache": cache, "first_date": lo, "last_date": hi,
           "latest_period": releases.period_name(hi, (freq or "").lower()) if hi else None,
           "future_to": top if top is not None and hi is not None and top > hi else None,
           "n_obs": n, "age_days": None, "allowance_days": allow, "stale": False,
           "next_release": None}
    if cache == "session":
        found = _window(con, series_id, None, now)
        if found:
            out["next_release"] = releases.describe(found[0], found[1], "fresh", None)
        return {**out, "status": "session"}
    if hi is None:
        return {**out, "stale": True, "status": "empty"}
    age = (today - hi).days
    stale = age > allow
    status = "stale" if stale else "fresh"
    found = _window(con, series_id, hi, now)
    if found:
        w, wfreq = found
        state = releases.status(w, now)
        status = "stale" if stale else state
        out["next_release"] = releases.describe(w, wfreq, state, hi)
    return {**out, "age_days": age, "stale": stale, "status": status}
