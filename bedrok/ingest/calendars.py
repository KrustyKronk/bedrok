"""The publishers' own release dates, into the store's release_calendar table.

A release rule (core/releases.py) knows a publisher's habit; a calendar knows
its plan. Where a publisher states the date, it replaces the rule's window and
the answer says whose date it is. The feeds (researched 2026-10-15 and
2026-10-16; docs/engineering/notes.md, "Where release dates come from"):

  bea_json          BEA's release schedule, one JSON file of every release's
                    dates into the next year.
  statcan_json      Statistics Canada's key-indicator schedule: title, the
                    reference period, the day (releases are at 08:30).
  ine_ics           INE's iCal file, about ten weeks ahead.
  ecb_statscal      the ECB's statistical calendar page, about ten months ahead.
  fedboard_pages    the Fed Board's monthly calendar pages, this month and the
                    next (the one after when it is up).
  ons_next_release  the ONS series JSON the adapter already downloads carries
                    `description.nextRelease`; recorded after each fetch
                    (pipeline.sync_macro), no request of its own.

The fetched feeds are read at most once a day.

The BLS publishes an iCal file, but its website refuses automated retrieval
with an explicit policy page, so BLS series stay on their rules.

A failed fetch keeps the dates already stored: a stale calendar is still
nearer the truth than none, and the rule bounds it either way.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from bedrok.core import releases, store
from bedrok.ingest.adapters._util import BROWSER_UA

BEA_URL = "https://apps.bea.gov/API/signup/release_dates.json"
MAX_AGE = timedelta(days=1)


def _utc_naive(d: datetime) -> datetime:
    return d.astimezone(timezone.utc).replace(tzinfo=None)


def parse_bea(js: dict) -> pd.DataFrame:
    """BEA's {release name: {"release_dates": [ISO times]}} → rows. Other keys
    (`file_last_updated`) are not releases."""
    rows = []
    for name, v in js.items():
        if not isinstance(v, dict):
            continue
        for s in v.get("release_dates") or []:
            try:
                rows.append((name, _utc_naive(datetime.fromisoformat(s))))
            except (TypeError, ValueError):
                continue
    df = pd.DataFrame(rows, columns=["release_name", "due_at"])
    return df.drop_duplicates().reset_index(drop=True)


def fetch_bea() -> pd.DataFrame:
    r = requests.get(BEA_URL, headers=BROWSER_UA, timeout=30)
    r.raise_for_status()
    return parse_bea(r.json())


def _at(day: date, clock: time, feed: str) -> datetime:
    return _utc_naive(datetime.combine(day, clock, tzinfo=ZoneInfo(releases.FEEDS[feed]["tz"])))


def _rows(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["release_name", "due_at", "period"])
    df["period"] = pd.to_datetime(df["period"]).dt.date
    # One date per release per moment: two entries of one release on one day
    # (the ECB lists two BSI items together) are one release.
    return df.drop_duplicates(["release_name", "due_at"]).reset_index(drop=True)


STATCAN_URL = ("https://www150.statcan.gc.ca/n1/dai-quo/ssi/homepage/"
               "schedule-key_indicators-eng.json")


def parse_statcan(items: list) -> pd.DataFrame:
    """[{date "2026-11-16 00:00:01", title, description "October 2026"}] →
    rows at 08:30 Toronto. The time in `date` is a sort key, not the release
    time; every key indicator is released at 08:30 Eastern."""
    rows = []
    for it in items or []:
        try:
            day = date.fromisoformat(str(it["date"])[:10])
        except (KeyError, TypeError, ValueError):
            continue
        title = " ".join(str(it.get("title") or "").split())
        if title:
            rows.append((title, _at(day, time(8, 30), "statcan_json"),
                         releases.parse_period(it.get("description"))))
    return _rows(rows)


def fetch_statcan() -> pd.DataFrame:
    r = requests.get(STATCAN_URL, headers=BROWSER_UA, timeout=60)
    r.raise_for_status()
    return parse_statcan(r.json())


INE_URL = "https://www.ine.es/dynt3/Calendario/en/calendario.ics"
# The English finals only: INE's flashes ("Consumer Price Index. Avance.
# Octubre 2026") carry the headline rate alone, and are titled in Spanish.
_INE = re.compile(r"(Consumer Price Index|Economically Active Population Survey)\. "
                  r"((?:%s) \d{4}|Quarter [1-4]/\d{4})" % "|".join(releases._MONTHS))


def parse_ine(text: str) -> pd.DataFrame:
    """INE's iCal → rows for the releases Bedrok dates. Line endings are made
    one kind first (the standard's is CRLF; INE serves LF today), then folded
    lines unfolded (RFC 5545: a continuation starts with a space or tab)."""
    unfolded = re.sub(r"\n[ \t]", "", (text or "").replace("\r\n", "\n"))
    rows = []
    for event in unfolded.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"^SUMMARY[^:\n]*:(.*)$", event, re.M)
        start = re.search(r"^DTSTART;TZID=([^:\n]+):(\d{8}T\d{6})$", event, re.M)
        if not summary or not start:
            continue
        m = _INE.fullmatch(summary.group(1).replace("\\,", ",").strip())
        if not m:
            continue
        local = datetime.strptime(start.group(2), "%Y%m%dT%H%M%S")
        due = _utc_naive(local.replace(tzinfo=ZoneInfo(start.group(1))))
        rows.append((m.group(1), due, releases.parse_period(m.group(2))))
    return _rows(rows)


def fetch_ine() -> pd.DataFrame:
    r = requests.get(INE_URL, headers=BROWSER_UA, timeout=60)
    r.raise_for_status()
    return parse_ine(r.content.decode("utf-8", errors="replace"))


ECB_URL = "https://www.ecb.europa.eu/press/calendars/statscal/html/index.en.html"
# The release each dataset's series come out in, by the page's own name; a
# dataset appears under other names too (BSI's MFI balance sheet, the same
# day), and only this one is kept so a stray entry cannot move a date.
ECB_RELEASES = {"BSI": "Monetary developments in the euro area",
                "MIR": "Euro area bank interest rate statistics",
                "BLS": "Euro area Bank Lending Survey"}
# The bank lending survey is dated in Bedrok by its own quarter (the July round
# is 2026-07-01), not the ECB's reference period: its date is matched by the
# rule's window instead.
_ECB_NO_PERIOD = {"BLS"}


def parse_ecb(text: str) -> pd.DataFrame:
    """The ECB's statistical calendar page: `<dt>25/09/2026 10:00</dt><dd>name
    (Dataset: BSI)<br>Reference period: Aug-2026<br>...</dd>`, Frankfurt time.
    `release_name` is the dataset code, which the series' own codes begin with."""
    rows = []
    for when, body in re.findall(r"<dt>\s*([^<]+?)\s*</dt>\s*<dd>(.*?)</dd>", text or "", re.S):
        words = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())
        m = re.match(r"(.*?) \(Dataset: ([A-Z_]+)\)", words)
        if not m or ECB_RELEASES.get(m.group(2)) != m.group(1):
            continue
        try:
            local = datetime.strptime(when.strip(), "%d/%m/%Y %H:%M")
        except ValueError:
            continue
        ref = re.search(r"Reference period: (\S+)", words)
        period = None if m.group(2) in _ECB_NO_PERIOD or not ref else releases.parse_period(ref.group(1))
        rows.append((m.group(2), _at(local.date(), local.time(), "ecb_statscal"), period))
    return _rows(rows)


def fetch_ecb() -> pd.DataFrame:
    r = requests.get(ECB_URL, headers=BROWSER_UA, timeout=60)
    r.raise_for_status()
    return parse_ecb(r.text)


FED_URL = "https://www.federalreserve.gov/newsevents/{year}-{month}.htm"
_FED_ROW = re.compile(r'<div class="col-xs-2">\s*<p>([^<]*)</p>\s*</div>\s*'
                      r'<div class="col-xs-7">\s*<p>([^<]*)</p>.*?'
                      r'<div class="col-xs-3">\s*<p>([^<]*)</p>', re.S)


def _fed_code(name: str) -> str:
    """"H.4.1 - Factors Affecting Reserve Balances" → "H.4.1"; "Senior Loan
    Officer Opinion Survey on Bank Lending Practices (SLOOS)" → "SLOOS"."""
    if " - " in name:
        return name.split(" - ", 1)[0].strip()
    m = re.search(r"\(([A-Z][A-Za-z0-9.]+)\)$", name)
    return m.group(1) if m else name


def parse_fedboard(text: str, year: int, month: int) -> pd.DataFrame:
    """One monthly calendar page: rows of (time "4:30 p.m.", release, days
    "5, 12, 19, 27"). A row with no time (a holiday, G.20) is not a release
    at a moment, and is skipped. No reference period: matched by window."""
    rows = []
    for clock, name, days in _FED_ROW.findall(text or ""):
        name = " ".join(html.unescape(name).split())
        m = re.fullmatch(r"(\d{1,2}):(\d{2}) ([ap])\.m\.", clock.strip())
        if not m or not name:
            continue
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "p" else 0)
        at = time(hour, int(m.group(2)))
        for d in re.findall(r"\d{1,2}", days):
            try:
                day = date(year, month, int(d))
            except ValueError:
                continue
            rows.append((_fed_code(name), _at(day, at, "fedboard_pages"), None))
    return _rows(rows)


def _month_name(d: date) -> str:
    return releases._MONTHS[d.month - 1].lower()


def fetch_fedboard(today: date | None = None) -> pd.DataFrame:
    """This month's page and the next must answer; the one after is used when
    the Board has put it up (it is not there until late in the month)."""
    first = (today or date.today()).replace(day=1)
    months = [first, releases.next_label(first, "monthly"),
              releases.next_label(releases.next_label(first, "monthly"), "monthly")]
    frames = []
    for i, m in enumerate(months):
        r = requests.get(FED_URL.format(year=m.year, month=_month_name(m)),
                         headers=BROWSER_UA, timeout=60)
        if r.status_code == 404 and i == 2:
            break
        r.raise_for_status()
        frames.append(parse_fedboard(r.text, m.year, m.month))
    return pd.concat(frames, ignore_index=True) if frames else _rows([])


FETCHERS = {"bea_json": fetch_bea, "statcan_json": fetch_statcan, "ine_ics": fetch_ine,
            "ecb_statscal": fetch_ecb, "fedboard_pages": fetch_fedboard}


def parse_ons_next(text: str | None, latest: date | None, frequency: str) -> dict | None:
    """The ONS's "21 October 2026" for the period after `latest`, at 07:00
    London, the ONS's release time. Anything else ("To be announced", blank)
    is no date."""
    if not text or latest is None:
        return None
    try:
        day = datetime.strptime(text.strip(), "%d %B %Y").date()
    except ValueError:
        return None
    due = datetime.combine(day, time(7), tzinfo=ZoneInfo(releases.FEEDS["ons_next_release"]["tz"]))
    return {"period": releases.next_label(latest, frequency), "due_at": _utc_naive(due)}


def _frame(feed: str, df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, "feed", feed)
    out["tz"] = releases.FEEDS[feed]["tz"]
    if "period" not in out:
        out["period"] = pd.NaT
    out["fetched_at"] = _utc_naive(now)
    return out[["feed", "release_name", "due_at", "tz", "period", "fetched_at"]]


def parse_nbk_next(day: date | None, at: time) -> dict | None:
    """The National Bank's "Next:" date at the rule's local time, with no
    period: the page does not say which month is next, and on the reserves
    page the date is often the revision of the month just published. A
    period-less date is believed only inside the next period's window
    (ops/freshness.py), so a revision date before it is ignored."""
    if day is None:
        return None
    due = datetime.combine(day, at, tzinfo=ZoneInfo(releases.FEEDS["nbk_next_release"]["tz"]))
    return {"period": None, "due_at": _utc_naive(due)}


def record_publisher(con, series_id: str, entry: dict | None,
                     now: datetime | None = None, feed: str = "ons_next_release") -> int:
    """Store the next release a series' publisher named (the ONS in the
    series' own response, the National Bank of Kazakhstan on its page),
    replacing what that series had: one date per series, the one just read."""
    if not entry:
        return 0
    df = pd.DataFrame([{"release_name": series_id, "due_at": entry["due_at"],
                        "period": entry["period"]}])
    return store.replace_release_calendar(con, feed,
                                          _frame(feed, df, now or datetime.now(timezone.utc)),
                                          [series_id])


def needs_fetch(con, feed: str, now: datetime | None = None) -> bool:
    """Whether a fetched feed is older than a day (or absent). A day, not a
    week: a rescheduled release (BEA after the 2025 shutdown) must show before
    the old date passes, and the file is small."""
    last = con.execute("SELECT max(fetched_at) FROM release_calendar WHERE feed = ?",
                       [feed]).fetchone()[0]
    now = now or datetime.now(timezone.utc)
    return last is None or _utc_naive(now) - last > MAX_AGE


def sync_feed(con, feed: str, now: datetime | None = None) -> tuple:
    """Fetch one feed and replace its dates: (feed, status, detail)."""
    try:
        df = FETCHERS[feed]()
    except Exception as e:  # noqa: BLE001 — a calendar failure must not fail an answer
        return (feed, "FAIL", f"{type(e).__name__}: {str(e)[:160]}; stored dates kept")
    if df.empty:
        return (feed, "FAIL", "no dates in the answer; stored dates kept")
    n = store.replace_release_calendar(con, feed, _frame(feed, df, now or datetime.now(timezone.utc)))
    return (feed, "ok", f"{n} release dates")


def sync_calendars(con, incremental: bool = True) -> list[tuple]:
    """The refresh stage: every fetched feed, whatever its age."""
    return [sync_feed(con, feed) for feed in FETCHERS]
