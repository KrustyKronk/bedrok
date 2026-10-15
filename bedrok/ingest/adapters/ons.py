"""UK Office for National Statistics adapter (no API key).

`source_code` is the series' FULL site path, e.g.
  "economy/inflationandpriceindices/timeseries/d7g7/mm23"
and the adapter appends "/data" to reach the JSON.

The path matters: ONS keys a series by CDID *and* topic, and the same CDID
under the wrong topic prefix 404s — so the catalog stores the whole path
rather than just the CDID.

NOTE: the old api.ons.gov.uk service was decommissioned on 25/11/2024. Do not
resurrect it; www.ons.gov.uk/<path>/data is the live route.

The same JSON names the series' next release (`description.nextRelease`, "21
October 2026"). `fetch` remembers it and `next_release` hands it to the
pipeline, which records it in the release calendar (ingest/calendars.py): an
exact date at no extra request.

Licence: Open Government Licence v3.0 (see bedrok/licensing.py).
"""
from __future__ import annotations

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

BASE = "https://www.ons.gov.uk"

# Highest-resolution series first: a monthly series also publishes quarterly and
# annual roll-ups of itself, and storing the roll-up would silently coarsen it.
_GRAINS = ("months", "quarters", "years")

_FREQUENCY = {"months": "monthly", "quarters": "quarterly", "years": "annual"}

# source_code → (nextRelease text, latest period label, frequency) from the
# last fetch of that series in this process.
_next: dict[str, tuple] = {}

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    url = f"{BASE}/{source_code.strip('/')}/data"
    r = requests.get(url, headers=BROWSER_UA, timeout=60)
    r.raise_for_status()
    js = r.json()
    df = _parse(js)
    grain = next((g for g in _GRAINS if js.get(g)), None)
    if not df.empty and grain:
        _next[source_code] = ((js.get("description") or {}).get("nextRelease"),
                              df["date"].max(), _FREQUENCY[grain])
    if start and not df.empty:
        cutoff = pd.Timestamp(start).date()
        df = df[df["date"] >= cutoff].reset_index(drop=True)
    return df


def next_release(source_code: str) -> tuple | None:
    """(nextRelease text, latest period label, frequency) the last fetch of
    this series read, or None."""
    return _next.get(source_code)


def _observation_date(o: dict):
    """Period label → the period-START date, matching every other stored series.

    Uses the explicit year/month/quarter fields rather than parsing the "2026
    MAY" display string, which is locale-shaped and easy to get subtly wrong."""
    year = o.get("year", "").strip()
    if not year.isdigit():
        return None
    month = (o.get("month") or "").strip().lower()
    if month:
        m = _MONTHS.get(month)
        return pd.Timestamp(int(year), m, 1).date() if m else None
    quarter = (o.get("quarter") or "").strip().upper()
    if quarter.startswith("Q") and quarter[1:].isdigit():
        return pd.Timestamp(int(year), (int(quarter[1:]) - 1) * 3 + 1, 1).date()
    return pd.Timestamp(int(year), 1, 1).date()


def _parse(js: dict) -> pd.DataFrame:
    """ONS timeseries JSON → tidy DataFrame[date, value] (values arrive as strings)."""
    empty = pd.DataFrame(columns=["date", "value"])
    for grain in _GRAINS:
        obs = js.get(grain) or []
        if not obs:
            continue
        rows = [(_observation_date(o), o.get("value")) for o in obs]
        df = pd.DataFrame(rows, columns=["date", "value"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = (df.dropna(subset=["date", "value"])
                .drop_duplicates(subset="date", keep="last")
                .sort_values("date").reset_index(drop=True))
        if not df.empty:
            return df
    return empty
