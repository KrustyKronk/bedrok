"""The publishers' own release dates, into the store's release_calendar table.

A release rule (core/releases.py) knows a publisher's habit; a calendar knows
its plan. Where a publisher states the date, it replaces the rule's window and
the answer says whose date it is. Two feeds so far (researched 2026-10-15;
docs/engineering/notes.md, "Where release dates come from"):

  bea_json          BEA's release schedule, one JSON file of every release's
                    dates into the next year. Fetched here, at most once a day.
  ons_next_release  the ONS series JSON the adapter already downloads carries
                    `description.nextRelease`; recorded after each fetch
                    (pipeline.sync_macro), no request of its own.

The BLS publishes an iCal file, but its website refuses automated retrieval
with an explicit policy page, so BLS series stay on their rules.

A failed fetch keeps the dates already stored: a stale calendar is still
nearer the truth than none, and the rule bounds it either way.
"""
from __future__ import annotations

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


FETCHERS = {"bea_json": fetch_bea}


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


def record_publisher(con, series_id: str, entry: dict | None,
                     now: datetime | None = None) -> int:
    """Store the next release a series' own response named (the ONS), replacing
    what that series had: one date per series, the one just read."""
    if not entry:
        return 0
    df = pd.DataFrame([{"release_name": series_id, "due_at": entry["due_at"],
                        "period": entry["period"]}])
    return store.replace_release_calendar(con, "ons_next_release",
                                          _frame("ons_next_release", df, now or datetime.now(timezone.utc)),
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
