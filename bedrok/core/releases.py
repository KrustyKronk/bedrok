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

Where a publisher states the date itself, a calendar entry replaces the
rule's window (bedrok/seeds/release_calendar.csv names the feed and the
release for each series; ingest/calendars.py fetches the dates into the
store). The rule still bounds it: an entry far past the rule's window is
ignored, so a renamed release cannot pin a series to a date a year out.
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
CALENDAR_CSV = Path(str(importlib.resources.files("bedrok") / "seeds" / "release_calendar.csv"))

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
_ORDINALS = ("first", "second", "third", "fourth")


def parse_period(text: str | None) -> date | None:
    """A calendar's reference period as the label Bedrok dates it by (the
    period's first day): "October 2026", "Aug-2026", "third quarter 2026",
    "Quarter 3/2026". Anything else is None: the date is still kept, with no
    period, and matched by its window instead."""
    t = " ".join(str(text or "").split()).lower()
    months = [m.lower() for m in _MONTHS]
    m = re.fullmatch(r"([a-z]+)[ -](\d{4})", t)
    if m:
        word, year = m.group(1), int(m.group(2))
        hit = [i for i, name in enumerate(months) if name == word or name[:3] == word]
        if hit:
            return date(year, hit[0] + 1, 1)
    m = re.fullmatch(r"(first|second|third|fourth) quarter (\d{4})", t) \
        or re.fullmatch(r"quarter ([1-4])/(\d{4})", t)
    if m:
        q = _ORDINALS.index(m.group(1)) + 1 if m.group(1) in _ORDINALS else int(m.group(1))
        return date(int(m.group(2)), 3 * q - 2, 1)
    return None


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
    # working day, Destatis's CPI around the 28th): a small negative lag. A
    # quarterly survey dated to its own quarter is published inside it (the
    # Fed's SLOOS, the ECB's bank lending survey): up to a quarter before.
    if not -90 <= lo <= hi:
        raise ValueError(f"{key}: lags {lo}..{hi}")
    ZoneInfo(r["tz"])                                   # raises on an unknown zone
    hh, mm = map(int, r["time"].split(":"))
    return {**r, "lag_min": lo, "lag_max": hi, "at": time(hh, mm)}


@functools.lru_cache(maxsize=1)
def load() -> tuple[dict, ...]:
    with open(RULES_CSV, newline="", encoding="utf-8") as f:
        return tuple(parse_row(r) for r in csv.DictReader(f))


# The publishers' own dates. `fetched` feeds are downloaded on their own
# (ingest/calendars.py); a `publisher` feed rides in the series' own response.
CALENDAR_COLUMNS = ["series_id", "feed", "release_name"]
FEEDS = {
    "bea_json": {"publisher": "BEA", "tz": "America/New_York", "fetched": True,
                 "label": "BEA's release schedule"},
    "ons_next_release": {"publisher": "ONS", "tz": "Europe/London", "fetched": False,
                         "label": "the ONS's next-release date"},
    # The National Bank of Kazakhstan prints "Next: dd.mm.yyyy" on each
    # statistics page; it is read after the page's series is fetched, one
    # extra request per page. Asia/Almaty is UTC+5 since 2024-03-01 (tzdata
    # 2024a); an older zone database says +6.
    "nbk_next_release": {"publisher": "National Bank of Kazakhstan", "tz": "Asia/Almaty",
                         "fetched": False, "extra_request": True,
                         "label": "the National Bank of Kazakhstan's next-release date"},
    "statcan_json": {"publisher": "Statistics Canada", "tz": "America/Toronto",
                     "fetched": True, "label": "Statistics Canada's release schedule"},
    "ine_ics": {"publisher": "INE", "tz": "Europe/Madrid", "fetched": True,
                "label": "INE's release calendar"},
    "ecb_statscal": {"publisher": "ECB", "tz": "Europe/Berlin", "fetched": True,
                     "label": "the ECB's statistical calendar"},
    "fedboard_pages": {"publisher": "Federal Reserve Board", "tz": "America/New_York",
                       "fetched": True, "label": "the Fed Board's release calendar"},
}
# An exact date has no window to wait through: the period is late a day after
# it, which is long enough for a publisher's API to catch up with its website.
CALENDAR_GRACE = timedelta(days=1)
# How far past the rule's window a calendar entry may still be believed.
CALENDAR_REACH = timedelta(days=60)


@functools.lru_cache(maxsize=1)
def load_calendar() -> dict[str, tuple[str, str]]:
    """series_id → (feed, release name). A blank release name is the series'
    own id: an ONS series carries its own next-release date."""
    with open(CALENDAR_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        if r["feed"] not in FEEDS:
            raise ValueError(f"{r['series_id']}: unknown feed {r['feed']!r}")
        out[r["series_id"]] = (r["feed"], r["release_name"].strip() or r["series_id"])
    return out


def calendar_key(series_id: str) -> tuple[str, str] | None:
    return load_calendar().get(series_id)


def calendar_window(feed: str, label: date, due_at: datetime) -> dict:
    """The window of a period whose release date the publisher has stated."""
    due = due_at.astimezone(ZoneInfo(FEEDS[feed]["tz"]))
    return {"label": label, "due_at": due, "expected_by": due,
            "late_after": due + CALENDAR_GRACE, "source": "calendar", "feed": feed}


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
    return {"label": label, "due_at": due, "expected_by": by, "late_after": by + GRACE,
            "source": "rule"}


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
    """The JSON-safe `next_release` an answer carries, with the sentence to say.
    A rule's timing is the publisher's habit and says "usually"; a calendar
    date is the publisher's own and says whose."""
    period = period_name(w["label"], frequency)
    held = f"{period_name(latest, frequency)} is the latest held" if latest else ""
    exact = w.get("source") == "calendar"
    if exact:
        at = f"{when(w)} ({FEEDS[w['feed']]['label']})"
        message = {"due": f"{period} is scheduled for {at} and is not in the store yet; {held}.",
                   "late": f"{period} was scheduled for {at} and has not reached Bedrok; {held}."
                   }.get(state, f"Next: {period}, scheduled for {at}.")
    elif state == "due":
        message = f"{period} is usually out {when(w)}, and is not in the store yet; {held}."
    elif state == "late":
        message = (f"{period} was expected by {_day(w['expected_by'])}, "
                   f"{_clock(w['expected_by'])} and has not reached Bedrok; {held}.")
    else:
        message = f"Next: {period}, usually out {when(w)}."
    return {"period": period, "due_at": w["due_at"].isoformat(),
            "expected_by": w["expected_by"].isoformat(),
            "late_after": w["late_after"].isoformat(),
            "source": "calendar" if exact else "rule", "approximate": not exact,
            **({"publisher": FEEDS[w["feed"]]["publisher"],
                "calendar": FEEDS[w["feed"]]["label"]} if exact else {}),
            "message": message}
