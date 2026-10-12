"""Census Bureau economic indicators (EITS) time-series API. Free key required.

US-government public-domain monthly indicators: retail sales, durable goods
orders, housing starts and permits, new home sales, the trade balance.
`source_code` is "<dataset>:<category_code>:<data_type_code>", always the
seasonally adjusted, national figure. The catalog's, each tied out identical
to FRED on 2026-10-10:

  marts:44X72:SM            retail and food services sales (RSAFS)
  m3:MDM:NO                 durable goods new orders (DGORDER)
  m3:NXA:NO                 nondefense capital goods ex aircraft (NEWORDER)
  resconst:ASTARTS:TOTAL    housing starts, thousands SAAR (HOUST)
  resconst:APERMITS:TOTAL   building permits (PERMIT)
  ressales:ASOLD:TOTAL      new single-family homes sold (HSN1F)
  ftd:BOPGS:BAL             goods and services trade balance (BOPGSTB)

Facts confirmed against the live API:
  * a key is mandatory: a keyless call is redirected to missing_key.html.
  * the path is /data/timeseries/eits/<dataset>; the old /data/eits/ is gone.
  * every dataset but marts REFUSES a request without `time_slot_id` in `get`
    and without `for=us:*`; without the geography resconst and ressales also
    return each census region under the same codes as the national total.
  * a predicate (category_code=…) is echoed back as a second column of the
    same name, so duplicate columns are dropped before parsing.
  * advm3 is the ADVANCE durable goods report; FRED follows the full M3
    report (dataset m3), which revises it a few weeks later.
  * the codes are the agency's, not the guessable ones: starts are category
    ASTARTS with data type TOTAL, not TOTAL/STARTS.
  * the response is a JSON array of arrays whose first row is the header, in
    the long format: cell_value, time, category_code, data_type_code, …

Licence: public domain — a US government work.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import requests

BASE = "https://api.census.gov/data/timeseries/eits/"
EMPTY = ["date", "value"]


def _parse(payload: list[list], category: str, data_type: str) -> pd.DataFrame:
    """Pure: the header-plus-rows JSON → DataFrame[date, value]."""
    if not payload or len(payload) < 2:
        return pd.DataFrame(columns=EMPTY)
    df = pd.DataFrame(payload[1:], columns=payload[0])
    df = df.loc[:, ~df.columns.duplicated()]
    keep = ((df["category_code"] == category) & (df["data_type_code"] == data_type))
    if "seasonally_adj" in df:
        keep &= df["seasonally_adj"].astype(str).str.lower() == "yes"
    df = df[keep]
    out = pd.DataFrame({
        "date": pd.to_datetime(df["time"], format="%Y-%m", errors="coerce"),
        "value": pd.to_numeric(df["cell_value"].astype(str).str.replace(",", ""),
                               errors="coerce"),
    }).dropna()
    out["date"] = out["date"].dt.date
    return (out.drop_duplicates(subset=["date"], keep="last")
               .sort_values("date").reset_index(drop=True))[EMPTY]


def fetch(source_code: str, api_key: str, start: str | None = None) -> pd.DataFrame:
    dataset, category, data_type = source_code.split(":")
    since = pd.Timestamp(start).strftime("%Y-%m") if start else "1950-01"
    r = requests.get(BASE + dataset, timeout=120, params={
        "get": "cell_value,data_type_code,category_code,seasonally_adj,time_slot_id",
        "time": f"from {since} to {date.today():%Y-%m}",
        "category_code": category, "data_type_code": data_type,
        "for": "us:*", "key": api_key})
    if "missing_key" in r.url or "invalid_key" in r.url:
        raise RuntimeError(f"Census rejected the key ({r.url.rsplit('/', 1)[-1]})")
    if r.status_code == 204:
        return pd.DataFrame(columns=EMPTY)
    if r.status_code != 200:
        # Never raise_for_status here: its message carries the URL, and the
        # URL carries the key. The body says what Census objected to.
        raise RuntimeError(f"Census HTTP {r.status_code}: {r.text[:120]}")
    return _parse(r.json(), category, data_type)
