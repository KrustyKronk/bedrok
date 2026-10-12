"""Bank of England Interactive Database (IADB) adapter (no API key).

`source_code` is an IADB series code, e.g. "IUDBEDR" (Official Bank Rate).
Returns a tidy DataFrame[date, value].

Three quirks of this endpoint, all learned the hard way:
  * it 403s without a browser-shaped User-Agent (an empty/HTML body, not an
    HTTP error you can catch by status alone);
  * it 302-redirects to /boeapps/database/_iadb-FromShowColumns.asp, so
    redirects must be followed (requests does by default — don't disable);
  * a single request spanning too many years can return an HTML error page
    with a 200 status, so history is fetched in chunks and any HTML-looking
    body is rejected loudly rather than parsed into nonsense.

Licence: Open Government Licence v3.0. SONIA additionally requires a verbatim
attribution statement — see bedrok/licensing.py.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

BASE = "https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp"

# IADB history starts in the 1960s for the oldest series; chunking keeps each
# request small enough that the service answers with CSV rather than an error page.
EARLIEST_YEAR = 1963
CHUNK_YEARS = 15


class BoEResponseError(RuntimeError):
    """The endpoint answered 200 with something that is not CSV."""


def _request(code: str, date_from: str, date_to: str) -> str:
    params = {"csv.x": "yes", "Datefrom": date_from, "Dateto": date_to,
              "SeriesCodes": code, "CSVF": "TN", "UsingCodes": "Y",
              "VPD": "Y", "VFD": "N"}
    r = requests.get(BASE, params=params, headers=BROWSER_UA, timeout=90)
    r.raise_for_status()
    text = r.text
    if text.lstrip()[:1] == "<":
        raise BoEResponseError(
            f"BoE returned an HTML page instead of CSV for {code} "
            f"({date_from}–{date_to}) — try a narrower window")
    return text


def _parse_csv(text: str) -> pd.DataFrame:
    """IADB CSV → DataFrame[date, value]. Dates look like '02 Jan 2024'."""
    raw = pd.read_csv(io.StringIO(text))
    if raw.shape[1] < 2 or "DATE" not in raw.columns:
        return pd.DataFrame(columns=["date", "value"])
    value_col = [c for c in raw.columns if c != "DATE"][0]
    df = raw[["DATE", value_col]].rename(columns={"DATE": "date", value_col: "value"})
    df["date"] = pd.to_datetime(df["date"], format="%d %b %Y", errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)
    df["date"] = df["date"].dt.date
    return df


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    start_year = pd.Timestamp(start).year if start else EARLIEST_YEAR
    end_year = pd.Timestamp.now().year
    frames = []
    year = start_year
    while year <= end_year:
        chunk_end = min(year + CHUNK_YEARS - 1, end_year)
        text = _request(source_code, f"01/Jan/{year}", f"31/Dec/{chunk_end}")
        frames.append(_parse_csv(text))
        year = chunk_end + 1
    if not frames:
        return pd.DataFrame(columns=["date", "value"])
    # Chunk boundaries can overlap on an edge date — dedupe rather than double-count.
    df = (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date").reset_index(drop=True))
    if start:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df
