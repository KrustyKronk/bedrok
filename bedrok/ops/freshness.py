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
     feed; a seed test keeps every rule inside its series' allowance.

Session series (never stored) have no stored age; `assess` reports them as
such rather than as stale, with their next release when a rule names it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

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


def _window(series_id: str, last: date | None, now: datetime) -> tuple[dict, str] | None:
    """(window of the next period, its frequency) from the series' own rule, or
    for a derived series with none, the latest of its inputs': a spread is out
    when both legs are."""
    rule = releases.rule_for(series_id)
    if rule:
        return releases.resolve(rule, last, now), rule["frequency"]
    spec = next((d for d in derived.DERIVED if d["series_id"] == series_id), None)
    found = [(releases.resolve(r, last, now), r["frequency"])
             for r in map(releases.rule_for, (spec or {}).get("inputs", [])) if r]
    # Inputs at the series' own pace only: a monthly rate's rule says nothing
    # about a quarterly sum built from it.
    found = [f for f in found if f[1] == found[0][1]] if found else []
    return max(found, key=lambda f: f[0]["due_at"]) if found else None


def assess(con, series_id: str, today: date | None = None, *,
           now: datetime | None = None) -> dict | None:
    """Freshness of one catalog series, or None when the id is not in the catalog.

    {series_id, cache, first_date, last_date, future_to, n_obs, age_days,
     allowance_days, stale, status, next_release} where status is 'fresh',
    'due', 'late', 'stale', 'empty' (a stored series with nothing stored yet)
    or 'session' (never stored by design), and next_release is None for a
    series without a release rule, else what core/releases.describe returns.

    `last_date` is the latest observation dated today or earlier. A series that
    also carries observations dated later (IMF WEO forecasts to 2031, ECB SPF
    horizons) reports how far they run as `future_to`, and its age is measured
    from `last_date`: a forecast must never make a series look fresh.

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
           "future_to": top if top is not None and hi is not None and top > hi else None,
           "n_obs": n, "age_days": None, "allowance_days": allow, "stale": False,
           "next_release": None}
    if cache == "session":
        found = _window(series_id, None, now)
        if found:
            out["next_release"] = releases.describe(found[0], found[1], "fresh", None)
        return {**out, "status": "session"}
    if hi is None:
        return {**out, "stale": True, "status": "empty"}
    age = (today - hi).days
    stale = age > allow
    status = "stale" if stale else "fresh"
    found = _window(series_id, hi, now)
    if found:
        w, wfreq = found
        state = releases.status(w, now)
        status = "stale" if stale else state
        out["next_release"] = releases.describe(w, wfreq, state, hi)
    return {**out, "age_days": age, "stale": stale, "status": status}
