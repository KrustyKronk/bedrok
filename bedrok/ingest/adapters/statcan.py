"""Statistics Canada Web Data Service adapter (no API key).

`source_code` is a VECTOR id, e.g. "41690973" (CPI, all-items). Vectors are
the stable handle: a table's coordinates are renumbered when the table is
redesigned, and a vector id is not.

  POST https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods
       [{"vectorId": 41690973, "latestN": 5000}]

answers the vector's whole history (5000 periods is more than a century of
months). Values arrive already scaled to the table's unit; `scalarFactorCode`
says which (0 units, 3 thousands, 6 millions) and the catalog's `units` must
match it.

Licence: Statistics Canada Open Licence, attribution.
"""
from __future__ import annotations

import pandas as pd
import requests

BASE = "https://www150.statcan.gc.ca/t1/wds/rest"
LATEST_N = 5000


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    r = requests.post(f"{BASE}/getDataFromVectorsAndLatestNPeriods", timeout=60,
                      json=[{"vectorId": int(source_code), "latestN": LATEST_N}])
    r.raise_for_status()
    df = _parse(r.json())
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df


def _parse(js: list) -> pd.DataFrame:
    """WDS response (a list of one {status, object}) → [date, value].

    A vector that does not exist answers 200 with a FAILED status; that raises
    rather than storing an empty series."""
    item = js[0]
    if item.get("status") != "SUCCESS":
        raise ValueError(f"StatCan: {item.get('status')} {item.get('object')}")
    points = item["object"].get("vectorDataPoint", [])
    df = pd.DataFrame([(p.get("refPer"), p.get("value")) for p in points],
                      columns=["date", "value"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = (df.dropna(subset=["date", "value"])
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date").reset_index(drop=True))
    df["date"] = df["date"].dt.date
    return df
