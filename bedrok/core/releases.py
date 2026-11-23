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
# A row names one series or, like a rule, a family by its id prefix: one BIS
# release dates every country it carries.
CALENDAR_COLUMNS = ["series_id", "prefix", "feed", "release_name"]
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
    # Two calendars that give the day and not the time (2026-11-23). Eurostat's
    # iCal file has every release as an all-day event with no reference
    # period; the euro indicators come out at 11:00 Luxembourg by long
    # practice, said with "usually". The BIS's RSS feed names each release's
    # dataset and period and stamps every item 10:00 GMT, while its calendar
    # says only that data are released "no later than the specified date".
    "eurostat_ics": {"publisher": "Eurostat", "tz": "Europe/Luxembourg", "fetched": True,
                     "time_stated": False, "time": time(11, 0),
                     "label": "Eurostat's release calendar"},
    "bis_rss": {"publisher": "BIS", "tz": "Europe/Zurich", "fetched": True,
                "time_stated": False,
                "time_note": "the BIS gives the day, releasing no later than it",
                "label": "the BIS's release calendar"},
    "bns_calendar": {"publisher": "Bureau of National Statistics of Kazakhstan",
                     "tz": "Asia/Almaty", "fetched": True,
                     "label": "the Bureau of National Statistics' release calendar"},
    # Central banks' policy decisions (session I). A meeting is not a period of
    # a series: it is an event on the bank's own schedule, and the series that
    # shows its outcome is due once the decision has been taken. Every page
    # gives the day; only the Bank of Canada also gives the time, so the
    # others' `time` is the bank's usual practice, said with "usually".
    # `reflected_days` is how many days after the decision day the store first
    # shows it, read off each bank's own series (2024-26 changes in the store):
    # the New York Fed and the Bank of Canada date a new target from the next
    # day, the Bank of England's Bank Rate changes on the day, and the ECB's
    # rates take effect the following Wednesday. None: no Bedrok series shows
    # the decision on its day (the BIS month-end rows), so it is a date only.
    "fomc_calendar": {"publisher": "Federal Reserve Board", "tz": "America/New_York",
                      "fetched": True, "meetings": True, "time": time(14, 0),
                      "time_stated": False, "reflected_days": 1, "decision": "FOMC decision",
                      "effect": "the New York Fed dates a new target from the day after the decision",
                      "label": "the Fed's FOMC meeting calendar"},
    "ecb_meetings": {"publisher": "ECB", "tz": "Europe/Berlin", "fetched": True,
                     "meetings": True, "time": time(14, 15), "time_stated": False,
                     "reflected_days": 6,
                     "decision": "ECB monetary policy decision",
                     "effect": "new rates take effect the following Wednesday",
                     "label": "the ECB's Governing Council meeting schedule"},
    "boe_mpc": {"publisher": "Bank of England", "tz": "Europe/London", "fetched": True,
                "meetings": True, "time": time(12, 0), "time_stated": False,
                "reflected_days": 0,
                "decision": "Bank of England MPC decision",
                "effect": "Bank Rate changes on the day of the decision",
                "label": "the Bank of England's MPC dates"},
    "boj_mpm": {"publisher": "Bank of Japan", "tz": "Asia/Tokyo", "fetched": True,
                "meetings": True, "time": time(12, 0), "time_stated": False,
                "time_note": "the Bank of Japan sets no time; around midday Tokyo is usual",
                "reflected_days": None,
                "decision": "Bank of Japan policy decision", "related": "jp_policy_rate",
                "label": "the Bank of Japan's monetary policy meeting schedule"},
    "boc_rate": {"publisher": "Bank of Canada", "tz": "America/Toronto", "fetched": True,
                 "meetings": True, "time": time(9, 45), "time_stated": True,
                 "reflected_days": 1,
                 "decision": "Bank of Canada rate announcement",
                 "effect": "a new target is dated from the day after the announcement",
                 "label": "the Bank of Canada's upcoming events"},
    "rba_board": {"publisher": "Reserve Bank of Australia", "tz": "Australia/Sydney",
                  "fetched": True, "meetings": True, "time": time(14, 30),
                  "time_stated": False, "reflected_days": None,
                  "decision": "RBA monetary policy decision", "related": "au_policy_rate",
                  "label": "the Reserve Bank of Australia's board meeting schedule"},
    # The National Bank of Kazakhstan's schedule is one page per year under a
    # stable section (ingest/calendars.py finds the year's page by its label).
    # It gives the day and no time. Its base-rate API has a row dated on each
    # decision day, holds included (every 2024-26 decision checked), so a
    # decision shows on the day and a hold is a row like a change: the
    # "fetched since" test for change-only series is not needed.
    "nbk_base_rate": {"publisher": "National Bank of Kazakhstan", "tz": "Asia/Almaty",
                      "fetched": True, "meetings": True, "time": time(12, 0),
                      "time_stated": False,
                      "time_note": "the National Bank gives the day, not the time",
                      "reflected_days": 0, "holds_recorded": True,
                      "decision": "National Bank of Kazakhstan base rate decision",
                      "effect": "the base rate has a row dated on each decision day, holds included",
                      "label": "the National Bank of Kazakhstan's base rate decision schedule"},
}
MEETING_FEEDS = tuple(f for f, m in FEEDS.items() if m.get("meetings"))
# An exact date has no window to wait through: the period is late a day after
# it, which is long enough for a publisher's API to catch up with its website.
CALENDAR_GRACE = timedelta(days=1)
# How far past the rule's window a calendar entry may still be believed.
CALENDAR_REACH = timedelta(days=60)


@functools.lru_cache(maxsize=1)
def _calendar_rows() -> tuple[dict, dict]:
    with open(CALENDAR_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    own, fam = {}, {}
    for r in rows:
        sid, prefix = (r.get("series_id") or "").strip(), (r.get("prefix") or "").strip()
        name = (r.get("release_name") or "").strip()
        if bool(sid) == bool(prefix):
            raise ValueError(f"{sid or prefix or '?'}: set exactly one of series_id and prefix")
        if r["feed"] not in FEEDS:
            raise ValueError(f"{sid or prefix}: unknown feed {r['feed']!r}")
        if prefix and not name:
            raise ValueError(f"{prefix}: a family names its release")
        if sid:
            own[sid] = (r["feed"], name or sid)
        else:
            fam[prefix] = (r["feed"], name)
    return own, fam


def load_calendar() -> dict[str, tuple[str, str]]:
    """series_id → (feed, release name), for rows naming one series. A blank
    release name is the series' own id: an ONS series carries its own
    next-release date."""
    return _calendar_rows()[0]


def load_calendar_families() -> dict[str, tuple[str, str]]:
    """id prefix → (feed, release name), for rows naming a family."""
    return _calendar_rows()[1]


def calendar_key(series_id: str) -> tuple[str, str] | None:
    """The series' own calendar entry, else its family's (the longest prefix)."""
    own = load_calendar().get(series_id)
    if own:
        return own
    fam = [p for p in load_calendar_families() if series_id.startswith(p)]
    return load_calendar_families()[max(fam, key=len)] if fam else None


def family_of(series_id: str) -> str | None:
    """The id prefix of the family a series is judged with: its rule's or its
    calendar's, the longest. None for a series with its own rule and entry."""
    if any(r["series_id"] == series_id for r in load()):
        return None
    fams = [r["prefix"] for r in load() if r["prefix"] and series_id.startswith(r["prefix"])]
    fams += [p for p in load_calendar_families() if series_id.startswith(p)]
    return max(fams, key=len) if fams else None


def meeting_feed(series_id: str) -> str | None:
    """The central bank's meeting feed whose decisions this series shows, if any."""
    key = calendar_key(series_id)
    return key[0] if key and key[0] in MEETING_FEEDS else None


def meeting_series(feed: str) -> list[str]:
    """The series that show a bank's decisions, in seed order."""
    return [sid for sid, (f, _) in load_calendar().items() if f == feed]


# ---- editions ------------------------------------------------------------------
# A publication that revises every period at once, on a cycle of its own. The
# IMF's World Economic Outlook comes out each April and October, and its
# latest point dated today or earlier is always the current year's projection:
# on 1 January the next year's becomes "latest" without any fetch, so a period
# rule can never see a new edition. Bedrok cannot yet tell one edition from
# the next in the store (STATE.md item 7), so an edition is said, never
# judged: it does not make a series due or late, and age still decides.
# The IMF's own date for an edition is not published where a program can read
# it; its recent editions came out between the 8th and the 24th (October 2023
# on the 10th, April 2024 on the 16th, October 2024 on the 22nd, April 2025 on
# the 22nd, October 2025 on the 14th, April 2026 on the 14th).
EDITIONS = {
    "weo_": {"publisher": "IMF", "name": "World Economic Outlook", "months": (4, 10),
             "days": (8, 24), "time": time(9, 0), "tz": "America/New_York",
             "note": "IMF World Economic Outlook: each April and October, around the "
                     "IMF and World Bank meetings. https://www.imf.org/en/Publications/WEO"},
}


def edition_for(series_id: str) -> str | None:
    """The id prefix of the edition a series comes in, if any."""
    hit = [p for p in EDITIONS if series_id.startswith(p)]
    return max(hit, key=len) if hit else None


def edition_window(prefix: str, now: datetime) -> dict:
    """The next edition whose usual window has not closed: {label (the
    edition's month), due_at, expected_by, late_after}, all at the window's
    edges, `source` "edition"."""
    meta = EDITIONS[prefix]
    tz = ZoneInfo(meta["tz"])
    lo, hi = meta["days"]
    local = now.astimezone(tz)
    for year in (local.year, local.year + 1):
        for month in meta["months"]:
            end = datetime.combine(date(year, month, hi), meta["time"], tzinfo=tz)
            if end + GRACE > now:
                start = datetime.combine(date(year, month, lo), meta["time"], tzinfo=tz)
                return {"label": date(year, month, 1), "due_at": start, "expected_by": end,
                        "late_after": end + GRACE, "source": "edition", "edition": prefix}
    raise ValueError(f"{prefix}: no edition ahead of {now}")      # pragma: no cover


def describe_edition(w: dict) -> dict:
    """The `next_release` of a series that comes in editions: the next one, for
    information. It never says due or late (see EDITIONS)."""
    meta = EDITIONS[w["edition"]]
    a, b = w["due_at"], w["expected_by"]
    period = f"the {_MONTHS[a.month - 1]} {a.year} {meta['name']}"
    message = (f"Next: {period}, usually out between {a.day} and {b.day} "
               f"{_MONTHS[a.month - 1][:3]} {a.year}. It revises every year at once, "
               f"current and forecast; Bedrok cannot yet tell one edition from the next, "
               f"so this series is judged by age.")
    return {"period": period, "due_at": a.isoformat(), "expected_by": b.isoformat(),
            "late_after": w["late_after"].isoformat(), "source": "edition",
            "approximate": True, "informational": True, "publisher": meta["publisher"],
            "message": message}


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
    meta = FEEDS[w["feed"]] if exact else {}
    timed = meta.get("time_stated", True)
    if exact:
        d = w["due_at"]
        at = (f"{when(w)} ({meta['label']})" if timed
              else f"{_day(d)} ({meta['label']}; {meta['time_note']})" if meta.get("time_note")
              else f"{_day(d)}, usually {_clock(d)} ({meta['label']})")
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
    if state == "behind":
        # Released for the family, not for this member: the publisher's gap,
        # not Bedrok's (ops/freshness.py).
        message = (f"{period} is out for other series in this family but not for this one: "
                   f"the publisher has not released it yet; {held}.")
    return {"period": period, "due_at": w["due_at"].isoformat(),
            "expected_by": w["expected_by"].isoformat(),
            "late_after": w["late_after"].isoformat(),
            "source": "calendar" if exact else "rule",
            "approximate": not (exact and timed),
            **({"publisher": meta["publisher"], "calendar": meta["label"]} if exact else {}),
            **({"date_exact": True} if exact and not timed else {}),
            **({"behind": True} if state == "behind" else {}),
            "message": message}


# ---- central banks' decisions ------------------------------------------------
# How far back a decision still makes its series due. Past this, staleness
# speaks: a decision a month old that never reached the store is a broken feed.
MEETING_LOOKBACK = timedelta(days=30)


def meeting_window(feed: str, decision_at: datetime) -> dict:
    """The window of one decision. `decision_at` is aware or UTC-naive. The
    outcome is expected in the store the day after it is first dated (data for
    a day are published the next), and late two days after that."""
    meta = FEEDS[feed]
    if decision_at.tzinfo is None:
        decision_at = decision_at.replace(tzinfo=ZoneInfo("UTC"))
    due = decision_at.astimezone(ZoneInfo(meta["tz"]))
    lag = timedelta(days=(meta.get("reflected_days") or 0) + 1)
    return {"label": due.date(), "due_at": due, "expected_by": due + lag,
            "late_after": due + lag + GRACE, "source": "meetings", "feed": feed}


def reflected_on(feed: str, decision_day: date) -> date | None:
    """The first day the store can show a decision, or None for a dates-only feed."""
    days = FEEDS[feed].get("reflected_days")
    return None if days is None else decision_day + timedelta(days=days)


def describe_meeting(w: dict, state: str, latest: date | None = None,
                     frequency: str = "daily") -> dict:
    """The `next_release` of a policy-rate series, or a dates-only decision:
    the same keys as `describe`, so an answer reads one shape. The day is the
    bank's own; the time is its usual practice unless the bank states it."""
    meta = FEEDS[w["feed"]]
    d = w["due_at"]
    day = _day(d)
    if meta.get("time_stated"):
        at = f"{day}, {_clock(d)} ({meta['label']})"
    elif meta.get("time_note"):
        at = f"{day} ({meta['label']}); {meta['time_note']}"
    else:
        at = f"{day}, usually {_clock(d)} ({meta['label']})"
    period = f"{meta['decision']} of {day}"
    if latest is None:
        held = ""
    elif frequency == "event":
        held = f"; the latest rate held dates from {_day(latest)}"
    else:
        held = f"; data to {_day(latest)} is the latest held"
    effect = f" ({meta['effect']})" if meta.get("effect") else ""
    if state == "due":
        message = f"The {period} is not in the store yet{effect}{held}."
    elif state == "late":
        message = f"The {period} has not reached Bedrok{effect}{held}."
    else:
        message = f"Next: {meta['decision']}, {at}."
    return {"period": period, "due_at": d.isoformat(),
            "expected_by": w["expected_by"].isoformat(),
            "late_after": w["late_after"].isoformat(), "source": "meetings",
            "approximate": not meta.get("time_stated", False), "date_exact": True,
            "publisher": meta["publisher"], "calendar": meta["label"], "message": message}
