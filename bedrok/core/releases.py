"""When a series' next period is due: one rule per series or family, in
bedrok/seeds/release_rules.csv.

Freshness by age alone cannot see a release. A monthly series counts as fresh
for its `staleness_days` from the START of its last period, so on 15 October
2026, the morning the September CPI came out, the August point (dated 08-01)
was 75 days old against an allowance of 90 and passed as current. A rule says
when the publisher usually releases the next period, so the store can tell
"not out yet" from "out, and not fetched".

A rule is the publisher's usual timing, not its calendar: the next period is
expected between `lag_min` and `lag_max` days after that period ENDS (a flash
can be negative: before the month is over), at
`time` in `tz`, rolled forward to `weekday` when one is set (payrolls: the
first Friday) and past a weekend otherwise. Holidays are not known; the grace
after `lag_max` absorbs them.

    fresh  the next period is not expected yet
    due    inside the window: it may be out, so it is worth a fetch
    late   past the window and the grace, and still not in the store

A series without a rule keeps the age rule exactly. Pure data and date
arithmetic, in core: nothing here reads the clock or imports a layer above.
"""
from __future__ import annotations

import csv
import functools
import importlib.resources
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RULES_CSV = Path(str(importlib.resources.files("bedrok") / "seeds" / "release_rules.csv"))

COLUMNS = ["series_id", "prefix", "frequency", "lag_min", "lag_max", "weekday", "time",
           "tz", "note"]
FREQUENCIES = ("daily", "weekly", "monthly", "quarterly", "annual")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
GRACE = timedelta(days=2)

# The longest an expected period can lie beyond the last stored label, to its
# own end: a monthly label is the 1st, so the next month ends up to 61 days
# later. Used to check a rule against its series' staleness allowance.
SPAN_DAYS = {"daily": 3, "weekly": 7, "monthly": 61, "quarterly": 183, "annual": 730}

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def parse_row(raw: dict) -> dict:
    """A seed row with its numbers and names checked; raises ValueError."""
    r = {k: (raw.get(k) or "").strip() for k in COLUMNS}
    key = r["series_id"] or r["prefix"]
    if bool(r["series_id"]) == bool(r["prefix"]):
        raise ValueError(f"{key or '?'}: set exactly one of series_id and prefix")
    if r["frequency"] not in FREQUENCIES:
        raise ValueError(f"{key}: frequency {r['frequency']!r}")
    if r["weekday"] and r["weekday"] not in WEEKDAYS:
        raise ValueError(f"{key}: weekday {r['weekday']!r}")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", r["time"]):
        raise ValueError(f"{key}: time {r['time']!r} is not HH:MM")
    lo, hi = int(r["lag_min"]), int(r["lag_max"])
    # A flash can come before its month is over (Eurostat's HICP on the last
    # working day, Destatis's CPI around the 28th): a small negative lag.
    if not -10 <= lo <= hi:
        raise ValueError(f"{key}: lags {lo}..{hi}")
    ZoneInfo(r["tz"])                                   # raises on an unknown zone
    hh, mm = map(int, r["time"].split(":"))
    return {**r, "lag_min": lo, "lag_max": hi, "at": time(hh, mm)}


@functools.lru_cache(maxsize=1)
def load() -> tuple[dict, ...]:
    with open(RULES_CSV, newline="", encoding="utf-8") as f:
        return tuple(parse_row(r) for r in csv.DictReader(f))


def rule_for(series_id: str) -> dict | None:
    """The series' own rule, else its family's (the longest prefix), else None."""
    rules = load()
    own = next((r for r in rules if r["series_id"] == series_id), None)
    if own:
        return own
    fam = [r for r in rules if r["prefix"] and series_id.startswith(r["prefix"])]
    return max(fam, key=lambda r: len(r["prefix"])) if fam else None


# ---- periods -------------------------------------------------------------------

def _month_add(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _business_day_after(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def next_label(label: date, frequency: str) -> date:
    """The label of the period after `label`."""
    if frequency == "monthly":
        return _month_add(label, 1)
    if frequency == "quarterly":
        return _month_add(label, 3)
    if frequency == "annual":
        return date(label.year + 1, label.month, label.day)
    if frequency == "weekly":
        return label + timedelta(days=7)
    return _business_day_after(label)


def period_end(label: date, frequency: str) -> date:
    """The last day a period covers. Monthly, quarterly and annual labels are
    the period's first day; weekly and daily labels are the day itself."""
    if frequency == "monthly":
        return _month_add(label, 1) - timedelta(days=1)
    if frequency == "quarterly":
        return _month_add(label, 3) - timedelta(days=1)
    if frequency == "annual":
        return date(label.year, 12, 31)
    return label


def period_name(label: date, frequency: str) -> str:
    """How a reader names the period: September 2026, Q3 2026, the week to 14 Oct."""
    if frequency == "monthly":
        return f"{_MONTHS[label.month - 1]} {label.year}"
    if frequency == "quarterly":
        return f"Q{(label.month - 1) // 3 + 1} {label.year}"
    if frequency == "annual":
        return str(label.year)
    day = f"{label.day} {_MONTHS[label.month - 1][:3]} {label.year}"
    return f"the week to {day}" if frequency == "weekly" else day


# ---- the window ----------------------------------------------------------------

def _roll(d: date, weekday: str) -> date:
    """Forward to the rule's weekday, or past a weekend when it names none."""
    if weekday:
        return d + timedelta(days=(WEEKDAYS.index(weekday) - d.weekday()) % 7)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def window(rule: dict, label: date) -> dict:
    """When the period labelled `label` is expected: {label, due_at,
    expected_by, late_after}, aware datetimes in the publisher's zone."""
    tz, end = ZoneInfo(rule["tz"]), period_end(label, rule["frequency"])
    def at(lag: int) -> datetime:
        return datetime.combine(_roll(end + timedelta(days=lag), rule["weekday"]),
                                rule["at"], tzinfo=tz)
    due, by = at(rule["lag_min"]), at(rule["lag_max"])
    return {"label": label, "due_at": due, "expected_by": by, "late_after": by + GRACE}


def _label_weekday(rule: dict) -> int:
    """The weekday a weekly series is dated on, for a rule with a fixed lag:
    claims released on Thursday five days after the week ends are dated Saturday."""
    release = WEEKDAYS.index(rule["weekday"]) if rule["weekday"] else 0
    return (release - rule["lag_min"]) % 7


def resolve(rule: dict, last_label: date | None, now: datetime) -> dict:
    """The window of the next period. With a stored series, the period after
    its last label; for a series never stored (session-only), the first
    period whose release is still ahead of `now`."""
    freq = rule["frequency"]
    if last_label is not None:
        return window(rule, next_label(last_label, freq))
    today = now.astimezone(ZoneInfo(rule["tz"])).date()
    start = today - timedelta(days=SPAN_DAYS[freq] + rule["lag_max"] + 7)
    if freq == "monthly":
        label = start.replace(day=1)
    elif freq == "quarterly":
        label = date(start.year, (start.month - 1) // 3 * 3 + 1, 1)
    elif freq == "annual":
        label = date(start.year, 1, 1)
    elif freq == "weekly":
        label = start - timedelta(days=(start.weekday() - _label_weekday(rule)) % 7)
    else:
        label = _roll(start, "")
    for _ in range(800):
        w = window(rule, label)
        if w["due_at"] > now:
            return w
        label = next_label(label, freq)
    raise ValueError(f"{rule['series_id'] or rule['prefix']}: no release ahead of {now}")


def status(w: dict, now: datetime) -> str:
    if now < w["due_at"]:
        return "fresh"
    return "due" if now <= w["late_after"] else "late"


# ---- what an answer says -------------------------------------------------------------

def _day(d: datetime, year: bool = True) -> str:
    return f"{d.day} {_MONTHS[d.month - 1][:3]}" + (f" {d.year}" if year else "")


def _clock(d: datetime) -> str:
    return f"{d:%H:%M} {d.tzname()}"


def when(w: dict) -> str:
    """'15 Oct 2026, 08:30 EDT', or a range when the rule allows one."""
    a, b = w["due_at"], w["expected_by"]
    if a.date() == b.date():
        return f"{_day(b)}, {_clock(b)}"
    return f"between {_day(a, a.year != b.year)} and {_day(b)}, {_clock(b)}"


def describe(w: dict, frequency: str, state: str, latest: date | None) -> dict:
    """The JSON-safe `next_release` an answer carries, with the sentence to say."""
    period = period_name(w["label"], frequency)
    held = f"{period_name(latest, frequency)} is the latest held" if latest else ""
    if state == "due":
        message = f"{period} is usually out {when(w)}, and is not in the store yet; {held}."
    elif state == "late":
        message = (f"{period} was expected by {_day(w['expected_by'])}, "
                   f"{_clock(w['expected_by'])} and has not reached Bedrok; {held}.")
    else:
        message = f"Next: {period}, usually out {when(w)}."
    return {"period": period, "due_at": w["due_at"].isoformat(),
            "expected_by": w["expected_by"].isoformat(),
            "late_after": w["late_after"].isoformat(),
            "source": "rule", "approximate": True, "message": message}
