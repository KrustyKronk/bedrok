"""Is a series fresh? One rule, used by `validate`, `doctor` and the MCP server.

A series is stale when its newest observation is older than its allowance:
the catalog's `staleness_days` when set, else the frequency default below, and
for a member of a bulk family (Eurostat per-geo sets, World Bank, CFTC) the
family's allowance. Ages run from the period-START label — a "May" monthly
point is dated 05-01 and publishes in late June — so the allowances include
the label lag as well as the publication lag.

Session series (never stored) have no stored age; `assess` reports them as
such rather than as stale.
"""
from __future__ import annotations

from datetime import date

from bedrok.ingest import pipeline

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


def assess(con, series_id: str, today: date | None = None) -> dict | None:
    """Freshness of one catalog series, or None when the id is not in the catalog.

    {series_id, cache, first_date, last_date, future_to, n_obs, age_days,
     allowance_days, stale, status} where status is 'fresh', 'stale', 'empty'
    (a stored series with nothing stored yet) or 'session' (never stored by
    design).

    `last_date` is the latest observation dated today or earlier. A series that
    also carries observations dated later (IMF WEO forecasts to 2031, ECB SPF
    horizons) reports how far they run as `future_to`, and its age is measured
    from `last_date`: a forecast must never make a series look fresh."""
    row = con.execute(
        "SELECT frequency, staleness_days, coalesce(cache, 'store') FROM macro_catalog "
        "WHERE series_id = ?", [series_id]).fetchone()
    if row is None:
        return None
    freq, sd, cache = row
    allow = allowance(freq, sd, series_id)
    today = today or date.today()
    lo, hi, top, n = con.execute(
        "SELECT min(date), max(date) FILTER (WHERE date <= ?), max(date), count(*) "
        "FROM macro_series WHERE series_id = ?", [today, series_id]).fetchone()
    out = {"series_id": series_id, "cache": cache, "first_date": lo, "last_date": hi,
           "future_to": top if top is not None and hi is not None and top > hi else None,
           "n_obs": n, "age_days": None, "allowance_days": allow, "stale": False}
    if cache == "session":
        return {**out, "status": "session"}
    if hi is None:
        return {**out, "stale": True, "status": "empty"}
    age = (today - hi).days
    stale = age > allow
    return {**out, "age_days": age, "stale": stale, "status": "stale" if stale else "fresh"}
